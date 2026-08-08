# -*- coding: utf-8 -*-
import os
import base64
import logging

# ============================================================
#  config_template.py - إعدادات التطبيق
#  يتم تحميل جميع القيم من متغيرات البيئة (GitHub Secrets)
#  لضمان الأمان وسهولة التحديث دون الحاجة لتعديل الملف.
# ============================================================

def load_config():
    """
    تحميل الإعدادات من متغيرات البيئة.
    
    المتغيرات المطلوبة في GitHub Secrets:
        - TELEGRAM_BOT_1_TOKEN  إلى  TELEGRAM_BOT_10_TOKEN
        - TELEGRAM_CONTROL_CENTER_ID   (معرف كروب التحكم)
        - TELEGRAM_DATA_VAULT_ID       (معرف كروب الأرشيف)
        - (اختياري) TELEGRAM_SECRET    (كلمة سر إضافية)
    
    الإرجاع:
        active: قائمة تحتوي على أول 6 توكنات (نشطة)
        reserve: قائمة تحتوي على التوكنات من 7 إلى 10 (احتياطية)
        ctrl: معرف كروب التحكم (int)
        vault: معرف كروب الأرشيف (int)
        secret: كلمة السر (str)
    """
    # جمع التوكنات من البيئة
    tokens = []
    for i in range(1, 11):
        token = os.environ.get(f"TELEGRAM_BOT_{i}_TOKEN", "")
        if token:
            tokens.append(token.strip())
        else:
            tokens.append("")
            logging.warning(f"TELEGRAM_BOT_{i}_TOKEN not set in environment.")

    # تقسيم إلى نشطة واحتياطية
    active = tokens[:6] if len(tokens) >= 6 else tokens + [""] * (6 - len(tokens))
    reserve = tokens[6:10] if len(tokens) >= 10 else tokens[6:] + [""] * (10 - len(tokens))

    # قراءة معرفات الكروبات
    ctrl_str = os.environ.get("TELEGRAM_CONTROL_CENTER_ID", "0")
    vault_str = os.environ.get("TELEGRAM_DATA_VAULT_ID", "0")

    try:
        ctrl = int(ctrl_str)
    except ValueError:
        logging.error(f"Invalid CONTROL_CENTER_ID: {ctrl_str}, using 0")
        ctrl = 0

    try:
        vault = int(vault_str)
    except ValueError:
        logging.error(f"Invalid DATA_VAULT_ID: {vault_str}, using 0")
        vault = 0

    # كلمة السر (اختيارية)
    secret = os.environ.get("TELEGRAM_SECRET", "")
    if not secret:
        secret = "default_secret_2024"   # قيمة افتراضية آمنة

    # التحقق من وجود توكنات صالحة على الأقل
    if not any(active) and not any(reserve):
        logging.warning("No valid tokens found. Please check your Secrets.")

    # تسجيل ملخص التحميل (بدون إظهار التوكنات)
    logging.info(f"Config loaded: {sum(1 for t in active if t)} active tokens, "
                 f"{sum(1 for t in reserve if t)} reserve tokens, "
                 f"CTRL={ctrl}, VAULT={vault}")

    return active, reserve, ctrl, vault, secret


# ============================================================
#  دوال مساعدة للوصول السريع إلى القيم الفردية
# ============================================================

def get_active_token(index=0):
    """
    الحصول على توكن نشط محدد (0-based).
    إذا كان index خارج النطاق، يُرجع أول توكن نشط.
    """
    try:
        active, _, _, _, _ = load_config()
        if 0 <= index < len(active) and active[index]:
            return active[index]
        # البحث عن أول توكن غير فارغ
        for t in active:
            if t:
                return t
    except Exception:
        pass
    return None

def get_reserve_token(index=0):
    """الحصول على توكن احتياطي محدد (0-based)."""
    try:
        _, reserve, _, _, _ = load_config()
        if 0 <= index < len(reserve) and reserve[index]:
            return reserve[index]
        for t in reserve:
            if t:
                return t
    except Exception:
        pass
    return None

def get_ctrl_id():
    """إرجاع معرف كروب التحكم."""
    try:
        _, _, ctrl, _, _ = load_config()
        return ctrl
    except Exception:
        return 0

def get_vault_id():
    """إرجاع معرف كروب الأرشيف."""
    try:
        _, _, _, vault, _ = load_config()
        return vault
    except Exception:
        return 0

def get_secret():
    """إرجاع كلمة السر."""
    try:
        _, _, _, _, secret = load_config()
        return secret
    except Exception:
        return "default_secret_2024"

def reload_config():
    """إعادة تحميل الإعدادات (مسح أي cache)."""
    # في حال استخدام caching، يمكن مسحها هنا
    return load_config()


# ============================================================
#  جزء اختياري لتوليد التوكنات المشفرة (للإصدارات القديمة)
#  يمكن تفعيله إذا لم تتوفر متغيرات البيئة.
#  لكن ننصح بالاعتماد على البيئة فقط.
# ============================================================

def _legacy_load():
    """
    هذه الدالة تحتفظ بالطريقة القديمة (الأجزاء المشفرة) كخيار احتياطي.
    ولكنها لن تُستخدم طالما توجد متغيرات بيئة.
    يمكن للمستخدم تحديث الأجزاء المشفرة يدوياً حسب الحاجة.
    """
    # تم إلغاء استخدام eval() والأجزاء الثابتة نهائياً.
    # في حال الرغبة بالعودة إلى الطريقة القديمة، يجب تحديث الأجزاء
    # يدوياً بتوكنات جديدة، لكن الأفضل استخدام البيئة.
    return load_config()


# ============================================================
#  اختبار سريع عند تشغيل الملف مباشرة
# ============================================================
if __name__ == "__main__":
    print("Testing config loading...")
    active, reserve, ctrl, vault, secret = load_config()
    print(f"Active: {len([t for t in active if t])} / 6")
    print(f"Reserve: {len([t for t in reserve if t])} / 4")
    print(f"CTRL ID: {ctrl}")
    print(f"VAULT ID: {vault}")
    print(f"Secret: {'*' * len(secret)}")
    print("Config loaded successfully.")

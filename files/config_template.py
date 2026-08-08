# -*- coding: utf-8 -*-
import os
import base64
import logging
import time

# ============================================================
#  config_template.py - إعدادات التطبيق
#  يتم تحميل جميع القيم من متغيرات البيئة (GitHub Secrets)
#  لضمان الأمان وسهولة التحديث دون الحاجة لتعديل الملف.
# ============================================================

# إعداد التسجيل الأساسي (في حالة عدم وجود تهيئة أخرى)
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

# ========== ذاكرة تخزين مؤقت للإعدادات ==========
_config_cache = None
_cache_time = 0
_CACHE_TTL = 60  # 60 ثانية


def validate_token(token, timeout=10):
    """
    التحقق من صلاحية توكن Telegram عن طريق استدعاء getMe.
    
    المعاملات:
        token (str): توكن البوت المراد التحقق منه
        timeout (int): مهلة الطلب بالثواني
    
    الإرجاع:
        tuple: (bool, str) -> (صحيح إذا كان التوكن صالحاً، رسالة الحالة)
    """
    if not token or not isinstance(token, str):
        return False, "Empty or invalid token"
    
    token = token.strip()
    if not token:
        return False, "Empty token after stripping"
    
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/getMe"
        response = requests.get(url, timeout=timeout, verify=True)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                bot_info = data.get('result', {})
                bot_name = bot_info.get('first_name', 'Unknown')
                bot_username = bot_info.get('username', 'Unknown')
                return True, f"✅ Valid bot: @{bot_username} ({bot_name})"
            else:
                return False, f"❌ API returned error: {data.get('description', 'Unknown error')}"
        else:
            return False, f"❌ HTTP {response.status_code}: {response.text[:100]}"
            
    except requests.exceptions.Timeout:
        return False, "❌ Timeout (connection too slow)"
    except requests.exceptions.ConnectionError:
        return False, "❌ Connection error (check internet)"
    except Exception as e:
        return False, f"❌ Error: {str(e)[:100]}"


def load_config(validate=False, force_refresh=False, skip_invalid=False):
    """
    تحميل الإعدادات من متغيرات البيئة.
    
    المتغيرات المطلوبة في GitHub Secrets:
        - TELEGRAM_BOT_1_TOKEN  إلى  TELEGRAM_BOT_10_TOKEN
        - TELEGRAM_CONTROL_CENTER_ID   (معرف كروب التحكم)
        - TELEGRAM_DATA_VAULT_ID       (معرف كروب الأرشيف)
        - (اختياري) TELEGRAM_SECRET    (كلمة سر إضافية)
    
    المعاملات:
        validate (bool): إذا كان True، يتم التحقق من صحة التوكنات
        force_refresh (bool): إذا كان True، يتم تجاهل الكاش
        skip_invalid (bool): إذا كان True، يتم تجاهل التوكنات غير الصالحة
    
    الإرجاع:
        tuple: (active_tokens, reserve_tokens, ctrl_id, vault_id, secret)
    """
    global _config_cache, _cache_time
    
    # استخدام الكاش إذا كان متاحاً وليس منتهياً
    if not force_refresh and _config_cache is not None:
        if time.time() - _cache_time < _CACHE_TTL:
            return _config_cache
    
    # جمع التوكنات من البيئة
    tokens = []
    for i in range(1, 11):
        token = os.environ.get(f"TELEGRAM_BOT_{i}_TOKEN", "")
        if token:
            tokens.append(token.strip())
        else:
            tokens.append("")
            logging.debug(f"TELEGRAM_BOT_{i}_TOKEN not set in environment.")
    
    # تقسيم إلى نشطة واحتياطية
    active = tokens[:6] if len(tokens) >= 6 else tokens + [""] * (6 - len(tokens))
    reserve = tokens[6:10] if len(tokens) >= 10 else tokens[6:] + [""] * (10 - len(tokens))
    
    # التحقق من صحة التوكنات إذا طُلب ذلك
    if validate:
        valid_active = []
        for token in active:
            if token:
                is_valid, msg = validate_token(token)
                if is_valid:
                    valid_active.append(token)
                    logging.info(f"✅ Token validated: {msg}")
                else:
                    logging.warning(f"⚠️ Invalid token: {msg}")
                    if not skip_invalid:
                        # إذا لم نطلب تخطي غير الصالح، نضيفه لكن مع تحذير
                        valid_active.append(token)
            else:
                valid_active.append("")
        active = valid_active
        
        valid_reserve = []
        for token in reserve:
            if token:
                is_valid, msg = validate_token(token)
                if is_valid:
                    valid_reserve.append(token)
                    logging.info(f"✅ Reserve token validated: {msg}")
                else:
                    logging.warning(f"⚠️ Invalid reserve token: {msg}")
                    if not skip_invalid:
                        valid_reserve.append(token)
            else:
                valid_reserve.append("")
        reserve = valid_reserve
    
    # قراءة معرفات الكروبات (مع قيم افتراضية من المشروع)
    ctrl_str = os.environ.get("TELEGRAM_CONTROL_CENTER_ID", "")
    vault_str = os.environ.get("TELEGRAM_DATA_VAULT_ID", "")
    
    # القيم الافتراضية من كروبات المشروع
    DEFAULT_CTRL = -1003943094277
    DEFAULT_VAULT = -1003577715762
    
    if not ctrl_str:
        logging.warning("TELEGRAM_CONTROL_CENTER_ID not set, using default.")
        ctrl = DEFAULT_CTRL
    else:
        try:
            ctrl = int(ctrl_str)
        except ValueError:
            logging.error(f"Invalid CONTROL_CENTER_ID: {ctrl_str}, using default {DEFAULT_CTRL}")
            ctrl = DEFAULT_CTRL
    
    if not vault_str:
        logging.warning("TELEGRAM_DATA_VAULT_ID not set, using default.")
        vault = DEFAULT_VAULT
    else:
        try:
            vault = int(vault_str)
        except ValueError:
            logging.error(f"Invalid DATA_VAULT_ID: {vault_str}, using default {DEFAULT_VAULT}")
            vault = DEFAULT_VAULT
    
    # كلمة السر (اختيارية)
    secret = os.environ.get("TELEGRAM_SECRET", "")
    if not secret:
        secret = "@321@321neaz"  # القيمة الافتراضية من المشروع
        logging.warning("TELEGRAM_SECRET not set, using default.")
    
    # إحصائيات التوكنات
    active_count = sum(1 for t in active if t)
    reserve_count = sum(1 for t in reserve if t)
    
    if active_count == 0 and reserve_count == 0:
        logging.warning("⚠️ No valid tokens found! The bot will not work.")
    else:
        logging.info(f"✅ Config loaded: {active_count} active tokens, {reserve_count} reserve tokens")
        logging.info(f"   Control ID: {ctrl}, Vault ID: {vault}")
    
    result = (active, reserve, ctrl, vault, secret)
    
    # تحديث الكاش
    _config_cache = result
    _cache_time = time.time()
    
    return result


# ============================================================
#  دوال مساعدة للوصول السريع إلى القيم الفردية
# ============================================================

def get_active_token(index=0, validate=False):
    """
    الحصول على توكن نشط محدد (0-based).
    إذا كان index خارج النطاق، يُرجع أول توكن نشط.
    
    المعاملات:
        index (int): مؤشر التوكن المطلوب
        validate (bool): التحقق من صحة التوكن قبل الإرجاع
    
    الإرجاع:
        str: التوكن المطلوب أو None
    """
    try:
        active, _, _, _, _ = load_config(validate=validate)
        if 0 <= index < len(active) and active[index]:
            return active[index]
        # البحث عن أول توكن غير فارغ
        for t in active:
            if t:
                return t
    except Exception as e:
        logging.error(f"get_active_token error: {e}")
    return None


def get_reserve_token(index=0, validate=False):
    """الحصول على توكن احتياطي محدد (0-based)."""
    try:
        _, reserve, _, _, _ = load_config(validate=validate)
        if 0 <= index < len(reserve) and reserve[index]:
            return reserve[index]
        for t in reserve:
            if t:
                return t
    except Exception as e:
        logging.error(f"get_reserve_token error: {e}")
    return None


def get_ctrl_id():
    """إرجاع معرف كروب التحكم."""
    try:
        _, _, ctrl, _, _ = load_config()
        return ctrl
    except Exception:
        return -1003943094277


def get_vault_id():
    """إرجاع معرف كروب الأرشيف."""
    try:
        _, _, _, vault, _ = load_config()
        return vault
    except Exception:
        return -1003577715762


def get_secret():
    """إرجاع كلمة السر."""
    try:
        _, _, _, _, secret = load_config()
        return secret if secret else "@321@321neaz"
    except Exception:
        return "@321@321neaz"


def reload_config(validate=False):
    """
    إعادة تحميل الإعدادات (تحديث الكاش).
    
    المعاملات:
        validate (bool): التحقق من صحة التوكنات أثناء إعادة التحميل
    """
    global _config_cache, _cache_time
    _config_cache = None
    _cache_time = 0
    return load_config(validate=validate, force_refresh=True)


def get_tokens_summary():
    """
    الحصول على ملخص للتوكنات (للتصحيح فقط، لا يعرض التوكنات بالكامل).
    
    الإرجاع:
        dict: معلومات حول عدد التوكنات وحالتها
    """
    try:
        active, reserve, ctrl, vault, secret = load_config()
        active_valid = sum(1 for t in active if t)
        reserve_valid = sum(1 for t in reserve if t)
        
        # حساب عدد التوكنات التي تبدو صالحة (بدون التحقق الفعلي)
        return {
            "active_count": active_valid,
            "reserve_count": reserve_valid,
            "total_count": active_valid + reserve_valid,
            "control_id": ctrl,
            "vault_id": vault,
            "has_secret": bool(secret),
            "cache_age": time.time() - _cache_time if _cache_time else None
        }
    except Exception as e:
        return {"error": str(e)}


def validate_all_tokens(timeout=5):
    """
    التحقق من جميع التوكنات وإرجاع تقرير مفصل.
    
    الإرجاع:
        dict: يحتوي على نتائج التحقق لكل توكن
    """
    result = {
        "active": [],
        "reserve": [],
        "summary": {}
    }
    
    try:
        active, reserve, _, _, _ = load_config(validate=False)
        
        for i, token in enumerate(active):
            if token:
                is_valid, msg = validate_token(token, timeout=timeout)
                result["active"].append({
                    "index": i,
                    "valid": is_valid,
                    "message": msg,
                    "token_preview": token[:10] + "..." if token else ""
                })
            else:
                result["active"].append({
                    "index": i,
                    "valid": False,
                    "message": "Empty token",
                    "token_preview": ""
                })
        
        for i, token in enumerate(reserve):
            if token:
                is_valid, msg = validate_token(token, timeout=timeout)
                result["reserve"].append({
                    "index": i,
                    "valid": is_valid,
                    "message": msg,
                    "token_preview": token[:10] + "..." if token else ""
                })
            else:
                result["reserve"].append({
                    "index": i,
                    "valid": False,
                    "message": "Empty token",
                    "token_preview": ""
                })
        
        result["summary"] = {
            "active_valid": sum(1 for t in result["active"] if t["valid"]),
            "active_total": len(result["active"]),
            "reserve_valid": sum(1 for t in result["reserve"] if t["valid"]),
            "reserve_total": len(result["reserve"])
        }
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


# ============================================================
#  جزء اختياري للتوافق مع الإصدارات القديمة
# ============================================================

def _legacy_load():
    """
    دالة احتياطية للتوافق مع الإصدارات القديمة.
    تعيد استخدام load_config مع إعدادات افتراضية.
    """
    return load_config()


# ============================================================
#  اختبار سريع عند تشغيل الملف مباشرة
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("Testing config loading...")
    print("=" * 50)
    
    # تحميل الإعدادات
    active, reserve, ctrl, vault, secret = load_config()
    
    print(f"\n📊 Configuration Summary:")
    print(f"  Active tokens: {sum(1 for t in active if t)} / 6")
    print(f"  Reserve tokens: {sum(1 for t in reserve if t)} / 4")
    print(f"  Control ID: {ctrl}")
    print(f"  Vault ID: {vault}")
    print(f"  Secret: {'✅ Set' if secret else '❌ Not set'}")
    
    # عرض معاينة للتوكنات (بدون كشفها بالكامل)
    print("\n📝 Token Preview:")
    for i, token in enumerate(active):
        if token:
            print(f"  Active {i+1}: {token[:10]}...{token[-4:] if len(token) > 14 else ''}")
        else:
            print(f"  Active {i+1}: (empty)")
    
    for i, token in enumerate(reserve):
        if token:
            print(f"  Reserve {i+1}: {token[:10]}...{token[-4:] if len(token) > 14 else ''}")
        else:
            print(f"  Reserve {i+1}: (empty)")
    
    # اختبار التحقق من التوكنات (اختياري)
    print("\n🔍 Validating tokens? (y/n): ", end="")
    choice = input().strip().lower()
    if choice == 'y':
        print("\nValidating tokens...")
        report = validate_all_tokens()
        for t in report.get("active", []):
            status = "✅" if t["valid"] else "❌"
            print(f"  {status} Active {t['index']+1}: {t['message'][:50]}")
        for t in report.get("reserve", []):
            status = "✅" if t["valid"] else "❌"
            print(f"  {status} Reserve {t['index']+1}: {t['message'][:50]}")
    
    print("\n✅ Config loaded successfully!")

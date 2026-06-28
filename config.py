"""پیکربندی متمرکز که از فایل .env خوانده می‌شود."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name, default=None):
    return os.getenv(name, default)


def _int(name, default):
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _float(name, default):
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _bool(name, default=False):
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "بله")


def _csv(name, default=""):
    raw = os.getenv(name, default) or ""
    return [s for s in raw.replace(" ", "").split(",") if s]


def _id_list(name):
    out = []
    for part in _csv(name):
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


# ---------- تلگرام ----------
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", "")
ADMIN_USER_IDS = _id_list("ADMIN_USER_IDS")
# گروهِ کاری برای درخواستِ زندهٔ عکس/ویدئوی مچ‌دست از همکاران، و چنلِ آرشیو
STAFF_GROUP_ID = _int("STAFF_GROUP_ID", 0)
# گروهِ پشتیبانی برای ارجاعِ مشتری (اگر جدا تعریف نشود، همان گروهِ کاری استفاده می‌شود)
SUPPORT_GROUP_ID = _int("SUPPORT_GROUP_ID", 0) or STAFF_GROUP_ID
MEDIA_CHANNEL = _get("MEDIA_CHANNEL", "yourstore_products")

# ---------- ووکامرس ----------
WOO_URL = (_get("WOO_URL", "") or "").rstrip("/")
WOO_CK = _get("WOO_CK", "")
WOO_CS = _get("WOO_CS", "")

# ---------- جی‌پی‌تی ----------
OPENAI_API_KEY = _get("OPENAI_API_KEY", "")
OPENAI_MODEL = _get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE = _float("OPENAI_TEMPERATURE", 0.4)

# ---------- نمایش قیمت ----------
# واحد فروشگاه ریال است؛ برای نمایش تومان بر این عدد تقسیم می‌شود (۱۰)
MONEY_DIVISOR = _int("MONEY_DIVISOR", 10)
CURRENCY_LABEL = _get("CURRENCY_LABEL", "تومان")

# ---------- رفتار دستیار ----------
MAX_HISTORY_TURNS = _int("MAX_HISTORY_TURNS", 12)
MAX_TOOL_ROUNDS = _int("MAX_TOOL_ROUNDS", 5)

# ---------- وب‌سرور چت سایت ----------
WEB_ENABLED = _bool("WEB_ENABLED", True)
WEB_HOST = _get("WEB_HOST", "0.0.0.0")
WEB_PORT = _int("WEB_PORT", 8090)
WEB_ALLOWED_ORIGINS = _csv("WEB_ALLOWED_ORIGINS", "*") or ["*"]

# ---------- اتصال به CRM (نقش sale-brain-v2) ----------
# توکنی که افزونه‌ی CRM با هدر X-SB-Token می‌فرستد؛ باید با تنظیمات چت CRM یکی باشد
SALE_BRAIN_TOKEN = _get("SALE_BRAIN_TOKEN", "")

# ---------- به‌روزرسانیِ نامِ مشتری در CRM (endpoint افزونه) ----------
CRM_NAME_UPDATE_URL = _get("CRM_NAME_UPDATE_URL", "")
CRM_NAME_UPDATE_TOKEN = _get("CRM_NAME_UPDATE_TOKEN", "")


def missing_required():
    """فهرست متغیرهای ضروریِ خالی را برمی‌گرداند (برای بررسی هنگام راه‌اندازی)."""
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "WOO_URL": WOO_URL,
        "WOO_CK": WOO_CK,
        "WOO_CS": WOO_CS,
        "OPENAI_API_KEY": OPENAI_API_KEY,
    }
    return [k for k, v in required.items() if not v]

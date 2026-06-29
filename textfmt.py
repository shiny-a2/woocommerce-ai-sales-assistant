"""تمیزکردن خروجی مدل برای نمایش در تلگرام و چت سایت (حذف مارک‌داونِ خام)."""
from __future__ import annotations

import re

_IMG = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADER = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_BULLET = re.compile(r"(?m)^\s*[\*\-]\s+")
_MULTINL = re.compile(r"\n{3,}")


_LIST_PREFIX = re.compile(r"^\s*(?:[•\-\*]|\d+[\.\)])\s")
# خطوطِ کارتِ محصول معمولاً با این ایموجی‌ها شروع می‌شوند (نام/قیمت/لینک/وضعیت/تخفیف)
_PROD_PREFIX = re.compile(r"^\s*(?:⌚|💰|🔗|⚡|🚚|🔖|✨|🌐|📦|🎥|⛓)")


def strip_product_lines(text):
    """وقتی محصولات جدا (کارت/متنِ کارت) نمایش داده می‌شوند، خطوطِ تکراریِ محصول را از
    متنِ مدل حذف می‌کند و فقط مقدمه/جمع‌بندیِ گفتگویی را نگه می‌دارد (تا دوبار نیاید)."""
    if not text:
        return ""
    keep = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            keep.append("")
            continue
        if _PROD_PREFIX.match(s):  # خطِ کارتِ محصول (با ایموجی شروع می‌شود)
            continue
        if ("http" in s or "مشاهده جزئیات" in s or "مشاهده در سایت" in s
                or "قیمت" in s or "لینک" in s or "وضعیت" in s
                or "زمان ارسال" in s or "ارسال فوری" in s or "روز کاری" in s):
            continue
        if _LIST_PREFIX.match(s):
            continue
        keep.append(line)
    return _MULTINL.sub("\n\n", "\n".join(keep)).strip()


def clean_for_chat(text):
    if not text:
        return ""
    t = text
    t = _IMG.sub("", t)                 # تصاویر مارک‌داون را حذف کن (پیش‌نمایش لینک عکس را نشان می‌دهد)
    t = _LINK.sub(r"\1\n\2", t)         # [متن](لینک) → متن + خط جدید + لینکِ خام (تلگرام خودش لینک می‌کند)
    t = _HEADER.sub("", t)              # ### عنوان → عنوان
    t = t.replace("**", "").replace("__", "").replace("`", "")
    t = _BULLET.sub("• ", t)           # نقطه‌های فهرست تمیز
    t = _MULTINL.sub("\n\n", t)
    return t.strip()

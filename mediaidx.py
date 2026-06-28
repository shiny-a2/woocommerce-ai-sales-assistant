"""خواندنِ ایندکسِ مدیای چنل (که سامانهٔ پیام‌رسان می‌سازد) برای ارسالِ عکس/ویدئوی مچ‌دست."""
from __future__ import annotations

import json
import os
import re

# ایندکس را سامانهٔ tg-outreach می‌سازد (همان سرور)
_IDX_PATH = r"C:\A2\tg-outreach\data\media_index.json"
_cache = {"mtime": 0.0, "data": {"refs": {}, "urls": {}, "channel": "your_products_channel"}}


def _norm_ref(s):
    return re.sub(r"[^A-Za-z0-9]", "", (s or "")).upper()


def _slug_from_url(url):
    m = re.search(r"/product/([^/?#]+)", url or "", re.I)
    return m.group(1).rstrip("/").lower() if m else ""


def _load():
    try:
        mt = os.path.getmtime(_IDX_PATH)
        if mt != _cache["mtime"]:
            _cache["data"] = json.load(open(_IDX_PATH, encoding="utf-8"))
            _cache["mtime"] = mt
    except Exception:
        pass
    return _cache["data"]


def lookup(reference=None, url=None, limit=4):
    """جست‌وجوی مدیا با رفرانس یا لینکِ محصول. خروجی: {channel, ids} یا None."""
    d = _load()
    ch = d.get("channel", "your_products_channel")
    if reference:
        v = d.get("refs", {}).get(_norm_ref(reference))
        if v and v.get("ids"):
            return {"channel": ch, "ids": v["ids"][:limit]}
    if url:
        ids = d.get("urls", {}).get(_slug_from_url(url))
        if ids:
            return {"channel": ch, "ids": ids[:limit]}
    return None

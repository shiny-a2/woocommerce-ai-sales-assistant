"""ساعت واقعی، مستقل از ساعتِ احتمالاً‌نادرستِ سرور.

آفستِ بین ساعت سرور و زمان واقعی را از هدر Date یک سرور معتبر می‌گیرد.
تهران بدون DST همیشه UTC+۳:۳۰ است. (هم‌نسخه با tg-outreach/clock.py)
"""
from __future__ import annotations

import asyncio
import datetime
import email.utils

import requests

_TEHRAN = datetime.timedelta(hours=3, minutes=30)
_offset = datetime.timedelta(0)  # real_utc - server_utcnow()
_HOSTS = ("https://www.google.com", "https://www.cloudflare.com", "https://api.telegram.org")


def _fetch_real_utc():
    last_err = None
    for host in _HOSTS:
        try:
            resp = requests.head(host, timeout=8)
            date_hdr = resp.headers.get("Date")
            if date_hdr:
                return email.utils.parsedate_to_datetime(date_hdr).replace(tzinfo=None)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err or RuntimeError("no time source")


def refresh_sync():
    global _offset
    try:
        _offset = _fetch_real_utc() - datetime.datetime.utcnow()
    except Exception:
        pass


async def refresh():
    global _offset
    try:
        real = await asyncio.to_thread(_fetch_real_utc)
        _offset = real - datetime.datetime.utcnow()
        print(f"[clock] آفستِ ساعتِ سرور: {_offset.total_seconds() / 3600:+.2f} ساعت")
    except Exception as e:  # noqa: BLE001
        print(f"[clock] همگام‌سازی زمان ناموفق بود: {e}")


def utcnow():
    return datetime.datetime.utcnow() + _offset


def tehran_now():
    return utcnow() + _TEHRAN

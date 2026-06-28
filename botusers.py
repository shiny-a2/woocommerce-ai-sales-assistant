"""شمارش کاربرانِ رباتِ تلگرام: عضو‌شده (/start) و فعال (هر تعامل)."""
from __future__ import annotations

import json
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_HERE, "data", "bot_users.json")
_LOCK = threading.Lock()
_DATA = None


def _load():
    global _DATA
    if _DATA is None:
        try:
            _DATA = json.load(open(_PATH, encoding="utf-8"))
        except Exception:
            _DATA = {}
        _DATA.setdefault("users", [])
        _DATA.setdefault("started", [])
    return _DATA


def _save():
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        json.dump(_DATA, open(_PATH, "w", encoding="utf-8"))
    except Exception:
        pass


def add_user(uid):
    if not uid:
        return
    with _LOCK:
        d = _load()
        if uid not in d["users"]:
            d["users"].append(uid)
            _save()


def add_started(uid):
    if not uid:
        return
    with _LOCK:
        d = _load()
        changed = False
        if uid not in d["started"]:
            d["started"].append(uid)
            changed = True
        if uid not in d["users"]:
            d["users"].append(uid)
            changed = True
        if changed:
            _save()


def counts():
    d = _load()
    return {"bot_users": len(d["users"]), "bot_started": len(d["started"])}

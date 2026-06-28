"""حافظه‌ی گفتگوی هر کاربر (در حافظه‌ی فرایند، با محدودکردن طول)."""
from __future__ import annotations

import config

# کلید: "channel:user_id" → فهرست پیام‌ها به فرمت OpenAI ({"role","content"})
_STORE: dict[str, list] = {}
# کلید: "channel:user_id" → فهرست آیدیِ محصولاتِ قبلاً نشان‌داده‌شده (برای نتایج غیرتکراری)
_SHOWN: dict[str, list] = {}


def _key(channel, user_id):
    return f"{channel}:{user_id}"


def history(channel, user_id):
    return _STORE.setdefault(_key(channel, user_id), [])


def append(channel, user_id, role, content):
    h = history(channel, user_id)
    h.append({"role": role, "content": content})
    _trim(h)


def _trim(h):
    limit = max(2, config.MAX_HISTORY_TURNS * 2)
    if len(h) > limit:
        del h[: len(h) - limit]


def shown_ids(channel, user_id):
    return _SHOWN.setdefault(_key(channel, user_id), [])


def add_shown(channel, user_id, ids):
    lst = shown_ids(channel, user_id)
    for i in ids:
        if i and i not in lst:
            lst.append(i)
    if len(lst) > 200:
        del lst[: len(lst) - 200]


def reset(channel, user_id):
    _STORE.pop(_key(channel, user_id), None)
    _SHOWN.pop(_key(channel, user_id), None)

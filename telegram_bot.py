"""کانال تلگرام دستیار فروش: هندلرهای python-telegram-bot."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import assistant
import botusers
import config
import llm
import sessions
import tools
import woo

CHANNEL = "telegram"
_name_pushed = set()  # کاربرانی که نامِ تلگرامی‌شان یک‌بار به CRM رفته


def _full_name(user):
    nm = (user.first_name or "")
    if user.last_name:
        nm += " " + user.last_name
    return nm.strip()


# کارت‌های ارسال‌شده (برای تشخیصِ «ریپلای به کارت») — ماندگار روی دیسک تا با ری‌استارت نپرد.
_SENT_CARDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sent_cards.json")
_sent_cards = {}  # "chat_id:message_id" → {id, name, reference, url}


def _save_sent_cards():
    try:
        if len(_sent_cards) > 1500:  # فقط ۱۲۰۰ کارتِ آخر را نگه دار
            for k in list(_sent_cards)[:-1200]:
                _sent_cards.pop(k, None)
        os.makedirs(os.path.dirname(_SENT_CARDS_PATH), exist_ok=True)
        with open(_SENT_CARDS_PATH, "w", encoding="utf-8") as f:
            json.dump(_sent_cards, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        print(f"[tg] ذخیرهٔ sent_cards ناموفق: {e}")


try:
    with open(_SENT_CARDS_PATH, encoding="utf-8") as _f:
        _sent_cards = json.load(_f)
except Exception:  # noqa: BLE001
    _sent_cards = {}


def _card_from_message(rep):
    """اگر کارت در حافظه نبود، محصول را از خودِ پیامِ ریپلای‌شده (کپشن + دکمهٔ لینک) دربیاور."""
    if not rep:
        return None
    cap = (getattr(rep, "caption", None) or getattr(rep, "text", None) or "")
    name = ""
    for line in cap.splitlines():
        s = line.strip()
        if s.startswith("⌚"):
            name = s.lstrip("⌚").strip()
            break
    url = ""
    try:
        for row in (rep.reply_markup.inline_keyboard if rep.reply_markup else []):
            for btn in row:
                if getattr(btn, "url", None):
                    url = btn.url
                    break
            if url:
                break
    except Exception:  # noqa: BLE001
        pass
    if name or url:
        return {"id": None, "name": name, "reference": "", "url": url}
    return None


_PRODUCT_HINTS = (
    "ساعت", "ساعتی", "واچ", "می‌خوام", "میخوام", "خواستم", "می‌خواستم", "مدل",
    "اتوماتیک", "کوارتز", "تخفیف", "حراج", "آف", "پیشنهاد", "معرفی", "زنانه",
    "مردانه", "بچگانه", "ست", "اسپرت", "کلاسیک", "فشن", "لاکچری", "بودجه",
    "میلیون", "تومان", "تومن", "قیمت", "چنده", "برند", "رنگ", "بند", "قاب",
    "صفحه", "موجود", "دارید", "دارین", "هست", "کادو", "هدیه", "سفارش", "ببینم",
    "نشون", "دنبال", "سیتیزن", "کاسیو", "اورینت", "کلود", "برنارد", "سواچ",
    "عقربه", "کرنوگراف", "طلایی", "نقره", "مشکی", "سفید", "آبی",
)
_GREETINGS = {
    "سلام", "درود", "سلام علیکم", "علیک", "علیک سلام", "خوبی", "چطوری",
    "حالت چطوره", "ممنون", "مرسی", "تشکر", "سپاس", "خداحافظ", "بای",
    "اوکی", "اوکیه", "باشه", "چشم", "بله", "نه", "ها", "ok", "hi", "hello",
}


def _norm(text):
    return (text or "").strip().rstrip("؟?!.،ـ \n")


def _is_smalltalk(text):
    t = _norm(text)
    return len(t) < 3 or t in _GREETINGS


def _looks_like_product(text):
    t = text or ""
    return any(h in t for h in _PRODUCT_HINTS)


def _interim_text(text):
    if _looks_like_product(text):
        return "چشم 🔎 اجازه بدید بهترین گزینه‌ها رو از گالری براتون پیدا کنم…"
    return "چشم 🙏 یک لحظه، همین الان بررسی می‌کنم…"


def _wants_wrist(text):
    t = text or ""
    for kw in ("مچ", "روی مج", "رو مج", "مج دست", "مج‌دست", "روی دست", "رو دست",
               "روی دستم", "رو دستم", "روی دستش", "عکس", "تصویر", "ویدیو", "ویدئو", "فیلم"):
        if kw in t:
            return True
    return False


# ---------- فالوآپِ خودکار: اگر بعد از نمایشِ محصول ۵ دقیقه سکوت شد، یک‌بار پیگیری کن ----------
_FOLLOWUP_DELAY = 300  # ثانیه (۵ دقیقه)
_followup_tasks = {}   # user_id → asyncio.Task
_FOLLOWUP_TEXT = (
    "ببخشید مزاحم شدم 🙂 از بینِ گزینه‌هایی که خدمتتون فرستادم چیزی به دلتون نشست؟\n"
    "اگر هنوز مرددید، سؤالی هست، یا چیزِ دیگه‌ای مدِ نظرتونه، بفرمایید تا بهتر راهنماییتون کنم 🌟"
)


def _cancel_followup(user_id):
    t = _followup_tasks.pop(user_id, None)
    if t and not t.done():
        t.cancel()


async def _followup_after(context, chat_id, user_id):
    try:
        await asyncio.sleep(_FOLLOWUP_DELAY)
    except asyncio.CancelledError:
        return
    _followup_tasks.pop(user_id, None)
    try:
        await context.bot.send_message(chat_id, _FOLLOWUP_TEXT)
    except Exception as e:  # noqa: BLE001
        print(f"[tg] فالوآپ ناموفق: {e}")


def _schedule_followup(context, chat_id, user_id):
    _cancel_followup(user_id)
    try:
        _followup_tasks[user_id] = asyncio.create_task(_followup_after(context, chat_id, user_id))
    except RuntimeError:  # حلقهٔ asyncio در دسترس نبود
        pass

_WELCOME = (
    "سلام، وقت‌تون به‌خیر 🌟\n"
    "به فروشگاهِ نمونه خوش اومدید 😊\n"
    "من مشاورِ هوشمندِ ساعتِ شما هستم؛ با کمالِ میل کمکتون می‌کنم تا از میانِ ساعت‌های اصل و "
    "باکیفیتِ گالری، بهترین انتخاب رو پیدا کنید ⌚\n"
    "کافیه بفرمایید دنبالِ چه ساعتی هستید — مثلاً مردانه یا زنانه، اسپرت یا کلاسیک، یا یه "
    "بودجهٔ تقریبی — تا گزینه‌های مناسب رو خدمتتون بیارم 🙂\n"
    "اگر هم قبلاً خرید کردید و می‌خواید سفارش‌تون رو پیگیری کنید، کافیه شمارهٔ سفارش و شمارهٔ تماس‌تون رو بفرمایید 📦"
)


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    botusers.add_started(update.effective_user.id)
    sessions.reset(CHANNEL, update.effective_user.id)
    await update.message.reply_text(_WELCOME)


async def _reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sessions.reset(CHANNEL, update.effective_user.id)
    await update.message.reply_text("گفتگومون از نو شروع شد ✅ در خدمتم؛ دنبالِ چه ساعتی هستید؟")


def _card_caption(c):
    lines = ["⌚ " + (c.get("name", "") or "")]
    if c.get("on_sale") and c.get("sale_price_label"):
        reg = c.get("regular_price_label", "")
        lines.append(f"🔖 {c['sale_price_label']}" + (f"  (قبلاً {reg})" if reg else "") + "  ✨")
    elif c.get("price_label"):
        lines.append("💰 " + c["price_label"])
    av = c.get("availability", "")
    ship = c.get("shipping_time", "")
    if av or ship:
        emoji = "⚡" if ship == "ارسال فوری" else "🚚"
        lines.append(emoji + " " + " · ".join(x for x in (av, ship) if x))
    return "\n".join(lines)


async def _send_cards(context, msg, cards):
    for c in cards:
        cap = _card_caption(c)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌐 مشاهده در سایت", url=c["url"])]]) if c.get("url") else None
        sent = None
        try:
            if c.get("image"):
                sent = await context.bot.send_photo(chat_id=msg.chat_id, photo=c["image"], caption=cap, reply_markup=kb)
            else:
                sent = await msg.reply_text(cap + (("\n" + c["url"]) if c.get("url") else ""))
        except Exception as e:  # noqa: BLE001 — اگر عکس ارسال نشد، متنی بفرست
            print(f"[tg] ارسال کارت ناموفق: {e}")
            try:
                sent = await msg.reply_text(cap + (("\n" + c["url"]) if c.get("url") else ""))
            except Exception:
                pass
        if sent is not None:  # ردیابی: تا اگر مشتری به این کارت ریپلای کرد، محصول را بشناسیم
            _sent_cards[f"{msg.chat_id}:{sent.message_id}"] = {
                "id": c.get("id"), "name": c.get("name", ""),
                "reference": c.get("reference", ""), "url": c.get("url", ""),
                "warranty": c.get("warranty", ""), "warranty_provider": c.get("warranty_provider", ""),
            }
    _save_sent_cards()  # ماندگار روی دیسک (پس از ری‌استارت هم کارت‌های قبلی شناخته شوند)


async def _save_name_to_crm(user, nu):
    """نام و نام‌خانوادگیِ گرفته‌شده را به CRM می‌فرستد (با شناسه‌ی تلگرام)."""
    if not (config.CRM_NAME_UPDATE_URL and nu.get("first_name")):
        return
    payload = {
        "telegram_id": user.id,
        "telegram_username": user.username or "",
        "first_name": nu.get("first_name", ""),
        "last_name": nu.get("last_name", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(config.CRM_NAME_UPDATE_URL, json=payload,
                         headers={"X-A2-Token": config.CRM_NAME_UPDATE_TOKEN})
    except Exception as e:  # noqa: BLE001
        print(f"[tg] ثبت نام در CRM ناموفق: {e}")


_media_requests = {}  # message_id درخواست در گروه → {customer, reference, name}
_logged_groups = set()  # گروه‌هایی که آیدی‌شان یک‌بار لاگ شده


async def _post_staff_request(context, req):
    """درخواستِ عکس/ویدئوی مچ‌دست را در گروهِ کاری می‌گذارد."""
    cap = (
        "🔔 درخواستِ عکس/ویدئوی روی مچ‌دست\n"
        "همکارانِ عزیز، یک مشتری برای این ساعت عکس و ویدئوی روی مچ‌دست خواسته 🙏\n"
        "لطفاً عکس/ویدئوها رو بگیرید و حتماً «همین پیام» رو ریپلای کنید و بفرستید، تا هم مستقیم "
        "به دستِ مشتری برسه و هم در کانال بایگانی بشه.\n\n"
        f"⌚ {req.get('name','')}\n"
        + (f"🔖 رفرانس: {req['reference']}\n" if req.get("reference") else "")
        + (f"🔗 {req['url']}" if req.get("url") else "")
    )
    try:
        if req.get("image"):
            return await context.bot.send_photo(config.STAFF_GROUP_ID, photo=req["image"], caption=cap)
        return await context.bot.send_message(config.STAFF_GROUP_ID, cap)
    except Exception as e:  # noqa: BLE001
        print(f"[tg] ارسال درخواست به گروه ناموفق: {e}")
        return None


async def _post_support_request(context, user, last_text, handoff):
    """خلاصهٔ مرتبِ مشتری را برای پیگیری به گروهِ پشتیبانی می‌فرستد."""
    gid = config.SUPPORT_GROUP_ID
    if not gid:
        return
    uname = f"@{user.username}" if (user and user.username) else "—"
    txt = (
        "🆘 درخواستِ پشتیبانیِ مشتری\n"
        "همکارانِ پشتیبانی، لطفاً این مشتری را پیگیری کنید 🙏\n\n"
        f"👤 نام: {(_full_name(user) if user else '') or '—'}\n"
        f"💬 تلگرام: {uname}\n"
        f"🆔 آیدی: {user.id if user else '—'}\n"
        f"📞 شمارهٔ تماس: {handoff.get('contact') or '— (نگرفته)'}\n"
        f"📌 موضوع: {handoff.get('reason', '') or '—'}\n"
        f"📝 آخرین پیام: {last_text}"
    )
    try:
        await context.bot.send_message(gid, txt)
    except Exception as e:  # noqa: BLE001
        print(f"[tg] ارسال درخواست پشتیبانی به گروه ناموفق: {e}")


# ---------- ثبتِ سفارشِ کارت‌به‌کارت + فیش + تاییدِ همکار ----------
_pending_orders = {}            # user_id → {order, chat_id}  (منتظرِ عکسِ فیش)
_orders_pending_approval = {}   # order_id → {customer, order, group_msg}  (منتظرِ تیک/ضربدر)
_order_seq = 0


def _order_summary(order, user):
    uname = f"@{user.username}" if (user and user.username) else "—"
    return (
        f"⌚ محصول: {order.get('product','') or '—'}\n"
        f"👤 نام: {order.get('customer_name','') or '—'}\n"
        f"📞 تماس: {order.get('phone','') or '—'}\n"
        f"📍 آدرس: {order.get('address','') or '—'}\n"
        + (f"📮 کدپستی: {order['postal_code']}\n" if order.get("postal_code") else "")
        + (f"📝 توضیح: {order['notes']}\n" if order.get("notes") else "")
        + f"💬 تلگرام: {uname} | آیدی {user.id if user else '—'}"
    )


async def _handle_receipt(context, msg, user, pending):
    """عکسِ فیشِ مشتری را با مشخصاتِ سفارش و دکمهٔ تایید/رد به گروهِ سفارش‌ها می‌فرستد."""
    global _order_seq
    order = pending.get("order", {})
    gid = config.ORDERS_GROUP_ID
    if not gid:
        await msg.reply_text("فیشتون دریافت شد ✅ همکاران بررسی می‌کنن و به‌زودی خبرتون می‌کنیم 🙏")
        return
    _order_seq += 1
    oid = str(_order_seq)
    cap = ("🧾 فیشِ پرداخت + سفارشِ جدید (کارت‌به‌کارت)\n\n"
           + _order_summary(order, user)
           + "\n\nبعد از بررسیِ فیش، یکی از دکمه‌ها را بزنید 👇")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید سفارش", callback_data=f"ord:ok:{oid}"),
        InlineKeyboardButton("❌ رد", callback_data=f"ord:no:{oid}"),
    ]])
    try:
        sent = await context.bot.send_photo(gid, photo=msg.photo[-1].file_id, caption=cap, reply_markup=kb)
        _orders_pending_approval[oid] = {"customer": msg.chat_id, "order": order, "group_msg": sent.message_id}
        await msg.reply_text(
            "فیشتون دریافت شد ✅ سفارشتون در حالِ بررسیِ نهاییه؛ به‌محضِ تایید، همین‌جا خبرتون می‌کنم 🙏"
        )
    except Exception as e:  # noqa: BLE001
        print(f"[tg] ارسال فیش به گروهِ سفارش‌ها ناموفق: {e}")
        await msg.reply_text("فیشتون دریافت شد ✅ همکاران بررسی می‌کنن و به‌زودی خبرتون می‌کنیم 🙏")


async def _on_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تیک/ضربدرِ همکار روی فیش → اعلامِ نتیجه به مشتری + به‌روزرسانیِ پیامِ گروه."""
    q = update.callback_query
    if not q:
        return
    await q.answer()
    parts = (q.data or "").split(":")
    if len(parts) != 3 or parts[0] != "ord":
        return
    action, oid = parts[1], parts[2]
    info = _orders_pending_approval.pop(oid, None)
    base_cap = (q.message.caption if q.message else "") or ""
    if not info:
        try:
            await q.edit_message_caption(caption=base_cap + "\n\n⌛ این سفارش قبلاً رسیدگی شده.")
        except Exception:  # noqa: BLE001
            pass
        return
    cust = info["customer"]
    by = update.effective_user.full_name if update.effective_user else ""
    if action == "ok":
        try:
            await context.bot.send_message(
                cust,
                "سفارشتون تایید شد ✅🎉\nپرداختتون ثبت شد و سفارش وارد مرحلهٔ آماده‌سازی و ارسال می‌شه. "
                "کدِ رهگیری و جزئیاتِ ارسال رو به‌زودی خدمتتون اعلام می‌کنیم. ممنون از خریدتون 🌹",
            )
        except Exception as e:  # noqa: BLE001
            print(f"[tg] اعلامِ تایید به مشتری ناموفق: {e}")
        tag = f"\n\n✅ تایید شد" + (f" — {by}" if by else "")
    else:
        try:
            await context.bot.send_message(
                cust,
                "سلام 🙏 متأسفانه فیشِ پرداختتون تایید نشد. ممکنه مبلغ یا اطلاعاتِ واریز مشکلی داشته باشه؛ "
                "لطفاً یک‌بار بررسی کنید یا با پشتیبانی (۰۹۱۲۰۱۶۳۵۶۳) هماهنگ کنید تا سریع حلش کنیم.",
            )
        except Exception as e:  # noqa: BLE001
            print(f"[tg] اعلامِ رد به مشتری ناموفق: {e}")
        tag = f"\n\n❌ رد شد" + (f" — {by}" if by else "")
    try:
        await q.edit_message_caption(caption=base_cap + tag)
    except Exception:  # noqa: BLE001
        pass


# ---------- رسیدِ کانال‌های دیگر (یوزربات/واتساپ/اینستا) → گروهِ سفارش‌ها + اعلامِ بازگشتی ----------
_xrcp_pending = {}   # oid → {channel, customer_id, name}
_xrcp_seq = 0
_CHANNEL_NOTIFY = {
    "userbot": "http://127.0.0.1:8091/api/notify",
    "instagram": "http://127.0.0.1:8092/api/notify",
    "whatsapp": "http://127.0.0.1:8093/api/notify",
}
_CHANNEL_FA = {"userbot": "تلگرام (یوزربات)", "instagram": "اینستاگرام", "whatsapp": "واتساپ", "telegram": "تلگرام (ربات رسمی)"}


async def post_crosschannel_receipt(bot, image_bytes, channel, customer_id, name="", amount="", extra=""):
    """رسیدِ یک کانالِ دیگر را به گروهِ سفارش‌ها با دکمهٔ تایید/رد می‌فرستد. خروجی: True اگر فرستاده شد."""
    global _xrcp_seq
    gid = config.ORDERS_GROUP_ID
    if not gid or not image_bytes:
        return False
    _xrcp_seq += 1
    oid = str(_xrcp_seq)
    cap = ("🧾 فیشِ پرداخت — کانال: " + _CHANNEL_FA.get(channel, channel) + "\n"
           + (f"👤 {name}\n" if name else "")
           + (f"💰 مبلغ: {amount}\n" if amount else "")
           + (f"📝 {extra}\n" if extra else "")
           + "\nبعد از بررسیِ فیش، یکی از دکمه‌ها را بزنید 👇")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید سفارش", callback_data=f"xrcp:ok:{oid}"),
        InlineKeyboardButton("❌ رد", callback_data=f"xrcp:no:{oid}"),
    ]])
    try:
        await bot.send_photo(gid, photo=bytes(image_bytes), caption=cap, reply_markup=kb)
        _xrcp_pending[oid] = {"channel": channel, "customer_id": str(customer_id), "name": name}
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[tg] ارسالِ رسیدِ کانالِ {channel} به گروه ناموفق: {e}")
        return False


async def _notify_channel(channel, customer_id, text):
    url = _CHANNEL_NOTIFY.get(channel)
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(url, json={"customer_id": str(customer_id), "text": text},
                         headers={"X-SB-Token": config.SALE_BRAIN_TOKEN})
    except Exception as e:  # noqa: BLE001
        print(f"[tg] اعلامِ نتیجه به کانالِ {channel} ناموفق: {e}")


async def _on_xrcp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تیک/ضربدرِ همکار روی رسیدِ کانالِ دیگر → اعلام به مشتری در همان کانال."""
    q = update.callback_query
    if not q:
        return
    await q.answer()
    parts = (q.data or "").split(":")
    if len(parts) != 3 or parts[0] != "xrcp":
        return
    action, oid = parts[1], parts[2]
    info = _xrcp_pending.pop(oid, None)
    base_cap = (q.message.caption if q.message else "") or ""
    if not info:
        try:
            await q.edit_message_caption(caption=base_cap + "\n\n⌛ قبلاً رسیدگی شده.")
        except Exception:  # noqa: BLE001
            pass
        return
    by = update.effective_user.full_name if update.effective_user else ""
    if action == "ok":
        txt = ("سفارشتون تایید شد ✅🎉\nپرداختتون ثبت شد و سفارش وارد مرحلهٔ آماده‌سازی و ارسال می‌شه. "
               "کدِ رهگیری رو به‌زودی خدمتتون اعلام می‌کنیم. ممنون از خریدتون 🌹")
        tag = "\n\n✅ تایید شد" + (f" — {by}" if by else "")
    else:
        txt = ("سلام 🙏 متأسفانه فیشِ پرداختتون تایید نشد. لطفاً یک‌بار بررسی کنید یا با پشتیبانی "
               "(۰۹۱۲۰۱۶۳۵۶۳) هماهنگ کنید تا سریع حلش کنیم.")
        tag = "\n\n❌ رد شد" + (f" — {by}" if by else "")
    await _notify_channel(info["channel"], info["customer_id"], txt)
    try:
        await q.edit_message_caption(caption=base_cap + tag)
    except Exception:  # noqa: BLE001
        pass


# ---------- ارجاعِ عکسِ پیدا‌نشده به همکاران (همهٔ کانال‌ها) ----------
_escalations = {}   # group_message_id → {channel, customer_id, name}


async def post_staff_escalation(bot, image_bytes, channel, customer_id, name="", question=""):
    """عکسی که محصولش پیدا نشد را با سوالِ مشتری به گروهِ همکاران می‌فرستد تا ریپلای کنند."""
    gid = config.STAFF_GROUP_ID or config.SUPPORT_GROUP_ID
    if not gid or not image_bytes:
        return False
    cap = ("❓ سوالِ مشتری دربارهٔ این عکس — کانال: " + _CHANNEL_FA.get(channel, channel) + "\n"
           + (f"👤 {name}\n" if name else "")
           + (f"💬 «{question}»\n" if question else "")
           + "\n👈 همکاران: روی همین پیام **ریپلای** کنید تا پاسختان مستقیم برای مشتری برود.")
    try:
        sent = await bot.send_photo(gid, photo=bytes(image_bytes), caption=cap)
        _escalations[sent.message_id] = {"channel": channel, "customer_id": str(customer_id), "name": name, "question": question}
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[tg] ارسالِ ارجاعِ عکس به همکاران ناموفق: {e}")
        return False


_IG_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/\S+", re.I)
_TRIG_RE = re.compile(r"^(?:تریگر|trigger|کلمه|کلمات)\s*[:：]\s*(.+)$", re.I)
_MSG_RE = re.compile(r"^(?:پیام|متن|dm|message)\s*[:：]\s*(.+)$", re.I)
_REPLY_RE = re.compile(r"^(?:کامنت|comment|ریپلای)\s*[:：]\s*(.+)$", re.I)
_FOLLOW_RE = re.compile(r"^(?:فالو|follow|گیت|gate)\s*[:：]\s*(.+)$", re.I)
_STORY_MARK_RE = re.compile(r"(?:^|\n|\s)(?:استوری|story)\b", re.I)
_pending_campaign_link = {}  # chat_id → لینکِ اینستاگرام (لینک آمده، منتظرِ متنِ دایرکت)


def _parse_campaign_msg(cur_text, reply_text):
    """از پیامِ گروهِ کمپین: {kind, link, trigger, message}.
    - اگر لینکِ اینستاگرام باشد → kind=comment (کامنت‌های آن پست).
    - اگر لینک نباشد ولی «استوری» اعلام شده باشد → kind=story (ریپلایِ کلمه/عدد).
    تریگر از هر دو پیام جست‌وجو می‌شود تا اگر در پیامِ لینک تایپ شده باشد گم نشود."""
    combined = (cur_text or "") + "\n" + (reply_text or "")
    um = _IG_URL_RE.search(combined)
    # تریگر از هر دو پیام
    trigger = ""
    for txt in (cur_text or "", reply_text or ""):
        for ln in _IG_URL_RE.sub("", txt).split("\n"):
            mt = _TRIG_RE.match(ln.strip())
            if mt:
                trigger = mt.group(1).strip()
    if um:
        link = um.group(0).rstrip(".،,)")
        kind = "story" if "/stories/" in link.lower() else "comment"  # لینکِ استوری → کمپینِ استوری
    elif _STORY_MARK_RE.search(combined):
        link, kind = "", "story"
    else:
        return None  # نه لینک، نه مارکرِ استوری → گفتگوی عادیِ گروه
    # متن از پیامِ فعلی (لینک‌زدوده)؛ اگر فعلی فقط لینک/مارکر بود، از پیامِ ریپلای‌شده
    src = _IG_URL_RE.sub("", cur_text or "").strip() or _IG_URL_RE.sub("", reply_text or "").strip()
    body_lines, creply, gate = [], "", False
    for ln in src.split("\n"):
        s = ln.strip()
        if not s or _TRIG_RE.match(s):
            continue  # خطِ تریگر بالا جداگانه استخراج شد
        if re.match(r"^(?:استوری|story)\s*$", s, re.I):
            continue  # خطِ مارکرِ «استوری» جزوِ متن نیست
        mr = _REPLY_RE.match(s)
        if mr:
            creply = mr.group(1).strip()  # «کامنت:» → ریپلایِ عمومی + لایک
            continue
        mf = _FOLLOW_RE.match(s)
        if mf:
            gate = mf.group(1).strip().lower() in ("روشن", "بله", "on", "yes", "1", "فعال", "true")
            continue
        mm = _MSG_RE.match(s)
        body_lines.append(mm.group(1).strip() if mm else s)
    return {"kind": kind, "link": link, "trigger": trigger, "message": "\n".join(body_lines).strip(),
            "reply_text": creply, "gate": gate}


async def _post_ig_campaign(link, trigger, message, by="", kind="comment", reply_text="", gate=False):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                config.IG_CAMPAIGN_URL,
                json={"kind": kind, "link": link, "trigger": trigger, "message": message,
                      "by": by, "reply_text": reply_text, "gate": gate},
                headers={"X-SB-Token": config.SALE_BRAIN_TOKEN},
            )
            return r.status_code == 200 and bool(r.json().get("ok"))
    except Exception as e:  # noqa: BLE001
        print(f"[tg] POST کمپینِ اینستاگرام ناموفق: {type(e).__name__}: {e}")
        return False


async def _handle_ig_campaign_group(m):
    """گروهِ کمپین: لینکِ پست (+متن) یا «استوری + تریگر + متن» → ثبت و فعال‌سازیِ آنیِ کمپین."""
    cur = (m.text or m.caption or "").strip()
    rep = ""
    if m.reply_to_message:
        rep = (m.reply_to_message.text or m.reply_to_message.caption or "").strip()
    parsed = _parse_campaign_msg(cur, rep)
    # اگر این پیام لینک/مارکر نداشت ولی قبلاً لینک گرفته بودیم، با لینکِ ذخیره‌شده ترکیب کن
    if not parsed and cur and m.chat_id in _pending_campaign_link:
        parsed = _parse_campaign_msg(_pending_campaign_link[m.chat_id] + "\n" + cur, rep)
    if not parsed:
        return  # گفتگوی عادیِ گروه → نادیده
    if parsed["kind"] == "comment" and not parsed["message"]:
        # فقط لینک آمده → ذخیره و منتظرِ متن (نیازی به ریپلای نیست)
        _pending_campaign_link[m.chat_id] = parsed["link"]
        await m.reply_text("🔗 لینک گرفته شد. حالا در همین گروه، متنی که باید به کامنت‌گذارها دایرکت بشه رو بنویس "
                           "(و در صورتِ نیاز یک خطِ «تریگر: کلمه»).")
        return
    if parsed["kind"] == "story" and not parsed["trigger"] and not parsed["link"]:
        await m.reply_text("برای کمپینِ استوری، یا لینکِ استوری رو بفرست، یا یک خطِ «تریگر: کلمه‌یا‌عدد» (مثلاً «تریگر: ۲»).")
        return
    if not parsed["message"]:
        await m.reply_text("متنِ دایرکت خالیه 🙏 متنی که باید فرستاده بشه رو بنویس.")
        return
    _pending_campaign_link.pop(m.chat_id, None)
    by = str(m.from_user.id) if m.from_user else ""
    ok = await _post_ig_campaign(parsed["link"], parsed["trigger"], parsed["message"], by, parsed["kind"],
                                 parsed.get("reply_text", ""), parsed.get("gate", False))
    if ok:
        tg = parsed["trigger"] or "همهٔ کامنت‌ها"
        knd = "استوری/پست (ریپلایِ کلمه/عدد)" if parsed["kind"] == "story" else "کامنتِ پست"
        head = f"🔗 {parsed['link']}\n" if parsed["link"] else ""
        extra = ""
        if parsed.get("reply_text"):
            extra += "\n💬 ریپلای+لایکِ کامنت: «" + parsed["reply_text"][:40] + "»"
        extra += "\n🔒 فالوگیت سراسری است (روشن/خاموش از داشبورد) — نیازی به نوشتنش نیست."
        await m.reply_text(f"✅ کمپین ثبت و فوری فعال شد\n📌 نوع: {knd}\n{head}🎯 تریگر: {tg}{extra}\n"
                           "آماده‌ست — نیازی به روشن‌کردنِ دستی نیست.")
    else:
        await m.reply_text("ثبتِ کمپین ناموفق بود 🙏 سرویسِ اینستاگرام در دسترس نبود.")


async def _on_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیام‌های گروه: لاگِ آیدی (برای پیکربندی) + دریافتِ مدیای همکار و تحویل."""
    m = update.effective_message
    if not m or not m.chat or m.chat.type not in ("group", "supergroup"):
        return
    # آیدیِ هر گروهِ ناشناخته را یک‌بار لاگ کن (برای پیکربندیِ STAFF/SUPPORT/ORDERS_GROUP_ID)
    known = {config.STAFF_GROUP_ID, config.SUPPORT_GROUP_ID, config.ORDERS_GROUP_ID, config.IG_CAMPAIGN_GROUP_ID}
    if m.chat_id not in known and m.chat_id not in _logged_groups:
        _logged_groups.add(m.chat_id)
        print(f"[tg] گروه شناسایی شد → id={m.chat_id} | {m.chat.title}")
    # گروهِ کنترلِ کمپینِ اینستاگرام: لینکِ پست + متن → ثبتِ کمپینِ کامنت→دایرکت
    if config.IG_CAMPAIGN_GROUP_ID and m.chat_id == config.IG_CAMPAIGN_GROUP_ID:
        await _handle_ig_campaign_group(m)
        return
    # ریپلایِ همکار روی «ارجاعِ عکس» → پاسخ به مشتری در همان کانال (متن)
    if config.STAFF_GROUP_ID and m.chat_id == config.STAFF_GROUP_ID and m.reply_to_message:
        esc = _escalations.get(m.reply_to_message.message_id)
        if esc:
            answer = (m.text or m.caption or "").strip()
            if answer:
                import assistant
                txt = await assistant.polish_staff_reply(answer, esc.get("question", "")) or answer
                if esc["channel"] == "telegram":
                    try:
                        await context.bot.send_message(int(esc["customer_id"]), txt)
                    except Exception as e:  # noqa: BLE001
                        print(f"[tg] اعلامِ پاسخِ ارجاع به مشتری ناموفق: {e}")
                else:
                    await _notify_channel(esc["channel"], esc["customer_id"], txt)
                _escalations.pop(m.reply_to_message.message_id, None)
            return
    # رسیدگی به مدیای همکار فقط در گروهِ کاری (staff)
    if not config.STAFF_GROUP_ID or m.chat_id != config.STAFF_GROUP_ID or not m.reply_to_message:
        return
    if not (m.photo or m.video or m.document):
        return
    req = _media_requests.get(m.reply_to_message.message_id)
    if not req:
        return
    # ۱) تحویل به مشتری
    try:
        await context.bot.copy_message(chat_id=req["customer"], from_chat_id=config.STAFF_GROUP_ID, message_id=m.message_id)
    except Exception as e:  # noqa: BLE001
        print(f"[tg] تحویل مدیا به مشتری ناموفق: {e}")
    # ۲) درج در چنل (ساختار قدیمی: مدیا + ریپلایِ رفرانس)
    try:
        posted = await context.bot.copy_message(chat_id="@" + config.MEDIA_CHANNEL, from_chat_id=config.STAFF_GROUP_ID, message_id=m.message_id)
        if req.get("reference"):
            await context.bot.send_message("@" + config.MEDIA_CHANNEL, req["reference"], reply_to_message_id=posted.message_id)
    except Exception as e:  # noqa: BLE001
        print(f"[tg] درج در چنل ناموفق: {e}")


def _wrist_answer(ctx):
    """پیامِ قطعیِ عکس/ویدئوی مچ‌دست بر اساسِ نتیجهٔ واقعیِ ابزار (نه متنِ مدل). None اگر مچ‌دستی در کار نبود."""
    if ctx.get("wrist_media"):
        return "با کمالِ میل 🙌 اینم عکس و ویدئوی واقعیِ روی مچ‌دستِ همین ساعت؛ ببینید چطور می‌شینه:"
    if ctx.get("wrist_media_request"):
        return ("چشم 🙌 همین الان از همکارانم می‌خوام عکس و ویدئوی روی مچ‌دستِ این ساعت رو آماده کنن؛ "
                "به‌محضِ آماده‌شدن، همین‌جا خدمتتون می‌فرستم 🙏")
    if ctx.get("wrist_media_company_stock"):
        return ("این مدل از موجودیِ شرکتِ واردکننده‌ست و فعلاً عکس/ویدئوی روی مچ‌دست براش نداریم 🙏 "
                "ولی با کمالِ میل همهٔ مشخصات و جزئیاتش رو خدمتتون می‌گم تا با خیالِ راحت تصمیم بگیرید.")
    return None


async def _deliver(context, msg, user, source_text, answer, ctx):
    if user:
        botusers.add_user(user.id)
    cards = ctx.get("cards") or []
    wa = _wrist_answer(ctx)  # متنِ قطعیِ مچ‌دست؛ متنِ احتمالاً‌غلطِ مدل را override می‌کند
    if wa:
        answer = wa
    if answer:
        await msg.reply_text(answer, disable_web_page_preview=bool(cards))
    if cards:
        await _send_cards(context, msg, cards)
        if user:  # اگر محصول نشان دادیم، ۵ دقیقه بعد یک‌بار پیگیری کن (اگر سکوت شد)
            _schedule_followup(context, msg.chat_id, user.id)
    # عکس/ویدئوی روی مچ‌دست: کپی از چنل
    wm = ctx.get("wrist_media")
    if wm and wm.get("ids"):
        for mid in wm["ids"][:4]:
            try:
                await context.bot.copy_message(chat_id=msg.chat_id, from_chat_id="@" + wm["channel"], message_id=mid)
            except Exception as e:  # noqa: BLE001
                print(f"[tg] ارسال مدیای مچ ناموفق ({mid}): {e}")
    # درخواستِ زندهٔ مدیا از همکاران (وقتی در چنل نبود ولی کالا ارسال‌فوری بود)
    req = ctx.get("wrist_media_request")
    if req and config.STAFF_GROUP_ID:
        sent = await _post_staff_request(context, req)
        if sent:
            _media_requests[sent.message_id] = {"customer": msg.chat_id, "reference": req.get("reference", ""), "name": req.get("name", "")}
    # ثبتِ سفارش: منتظرِ عکسِ فیش از همین کاربر باش
    if ctx.get("order") and user:
        _pending_orders[user.id] = {"order": ctx["order"], "chat_id": msg.chat_id}
    # ذخیره‌ی نام: اگر مدل نامی گرفت، همان؛ وگرنه یک‌بار نامِ تلگرامیِ خودِ کاربر
    if ctx.get("name_update"):
        await _save_name_to_crm(user, ctx["name_update"])
    elif user and user.id not in _name_pushed and (user.first_name or user.last_name):
        _name_pushed.add(user.id)
        await _save_name_to_crm(user, {"first_name": user.first_name or "", "last_name": user.last_name or ""})
    if ctx.get("handoff"):
        await _post_support_request(context, user, source_text, ctx["handoff"])  # لیستِ مرتب در گروه
        await _notify_admins(context, user, source_text, ctx["handoff"])         # هشدار به ادمین‌ها


async def _handle_wrist(context, msg, user, product_id):
    """تحویلِ قطعیِ عکس/ویدئوی مچ‌دست برای محصولِ مشخص — مستقل از مدل (تا وعدهٔ توخالی ندهد)."""
    ctx = {}
    try:
        await tools.dispatch("get_wrist_media", json.dumps({"product_id": product_id}), ctx)
    except Exception:  # noqa: BLE001
        pass
    answer = _wrist_answer(ctx) or ("فعلاً عکس/ویدئوی روی مچِ این مدل رو در دسترس ندارم 🙏 "
                                    "ولی مشخصاتِ کامل و لینکِ صفحهٔ محصول رو خدمتتون می‌فرستم.")
    await _deliver(context, msg, user, msg.text, answer, ctx)


async def _resolve_product_id(name, url="", reference=""):
    """آیدیِ محصولِ کارت را **دقیق** پیدا می‌کند: اول اسلاگِ url (یکتا)، بعد کدِ رفرنس، بعد نام.
    (جلوگیری از resolveِ اشتباه — مثلِ کارتِ تروساردی که به سیتیزن می‌خورد.)"""
    try:
        b = await woo.resolve_product(url=url, name=name, reference=reference)
        return b.get("id") if b else None
    except Exception:  # noqa: BLE001
        return None


async def _product_specs_text(product_id):
    """شیتِ مشخصاتِ کاملِ یک محصول (نام/قیمت/ارسال + همهٔ ویژگی‌ها) برای تزریق به context."""
    try:
        p = await woo.get_product(product_id)
    except Exception:  # noqa: BLE001
        return ""
    parts = [p.get("name", "")]
    if p.get("price_label"):
        parts.append("قیمت: " + p["price_label"])
    if p.get("shipping_time"):
        parts.append("ارسال: " + p["shipping_time"])
    for a in (p.get("attributes") or []):
        nm = (a.get("name") or "").strip()
        opts = a.get("options") or []
        if nm and opts:
            parts.append(f"{nm}: " + "، ".join(str(o) for o in opts))
    return " | ".join(x for x in parts if x)


async def _on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    user = update.effective_user
    _cancel_followup(user.id)  # کاربر فعال است → فالوآپِ معلق را لغو کن
    text = msg.text
    # اگر به کارتِ محصول ریپلای کرده:
    if msg.reply_to_message:
        prod = (_sent_cards.get(f"{msg.chat_id}:{msg.reply_to_message.message_id}")
                or _card_from_message(msg.reply_to_message))  # fallback از خودِ کارت برای کارت‌های قدیمی
        if prod:
            pid = prod.get("id")
            if not pid and (prod.get("name") or prod.get("url")):  # کارتِ قدیمی: با url/کد/نام دقیق پیدا کن
                pid = await _resolve_product_id(prod.get("name", ""), prod.get("url", ""), prod.get("reference", ""))
            # درخواستِ عکس/ویدئوی روی مچ → همین محصول را قطعی تحویل بده
            if pid and _wants_wrist(msg.text):
                await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
                await _handle_wrist(context, msg, user, pid)
                return
            # هر سؤالِ دیگر دربارهٔ این محصول: مشخصاتِ کامل را قطعی بگیر و تزریق کن (مستقل از مدل)
            if pid:
                await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
                sheet = await _product_specs_text(pid)
                if sheet:
                    text = (f"(مشتری دربارهٔ همین محصول می‌پرسد. مشخصاتِ کاملش: {sheet}. "
                            f"فقط از همین مشخصات جواب بده و اسم/مشخصات نپرس.) " + text)
                else:
                    text = f"(مشتری دربارهٔ محصولِ آیدی {pid} می‌پرسد؛ با get_product({pid}) جزئیات را بگیر و جواب بده.) " + text
            elif prod.get("name"):
                text = f"(مشتری به این محصول اشاره دارد: {prod['name']}) " + text

    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
    # پیامِ موقت تا کاربر حس سرگردانی نکند (پاسخ کمی زمان می‌برد)
    if not _is_smalltalk(msg.text):
        try:
            await msg.reply_text(_interim_text(msg.text))
        except Exception:
            pass

    answer, ctx = await assistant.reply(CHANNEL, user.id, text, user_name=_full_name(user))
    await _deliver(context, msg, user, msg.text, answer, ctx)


async def _on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.photo:
        return
    user = update.effective_user
    _cancel_followup(user.id)
    # اگر منتظرِ فیشِ پرداختِ این کاربریم، این عکس را به‌عنوانِ فیش پردازش کن (نه جستجوی ساعت)
    pending = _pending_orders.pop(user.id, None)
    if pending:
        await _handle_receipt(context, msg, user, pending)
        return
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
    try:
        f = await msg.photo[-1].get_file()
        data = bytes(await f.download_as_bytearray())
        url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    except Exception as e:  # noqa: BLE001
        print(f"[tg] دریافت عکس ناموفق: {e}")
        await msg.reply_text("متأسفانه نتونستم عکس رو دریافت کنم 🙏 لطفاً یک‌بارِ دیگه ارسالش کنید.")
        return
    answer, ctx = await assistant.reply_image(CHANNEL, user.id, url, msg.caption or "", user_name=_full_name(user))
    if not ctx.get("cards") and not ctx.get("receipt") and not ctx.get("ask_gender"):  # محصول پیدا نشد → ارجاع (مگر فقط جنسیت پرسیده باشد)
        if await post_staff_escalation(context.bot, data, "telegram", user.id, name=_full_name(user), question=(msg.caption or "")):
            await msg.reply_text("عکستون رو دیدم 🙏 همین الان از همکارانم می‌پرسم و تا چند دقیقهٔ دیگه جوابتون رو همین‌جا می‌فرستم 🌟")
            return
    await _deliver(context, msg, user, "[تصویر]", answer, ctx)


async def _on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    voice = msg.voice or msg.audio if msg else None
    if not voice:
        return
    user = update.effective_user
    _cancel_followup(user.id)
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
    try:
        f = await voice.get_file()
        data = bytes(await f.download_as_bytearray())
        text = await llm.transcribe(data, "voice.ogg")
    except Exception as e:  # noqa: BLE001
        print(f"[tg] رونویسی ویس ناموفق: {e}")
        await msg.reply_text("متأسفانه نتونستم صدا رو دریافت کنم 🙏 لطفاً دوباره بفرمایید یا اگر راحت‌ترید تایپ کنید.")
        return
    if not text:
        await msg.reply_text("صدا رو واضح نگرفتم 🙏 لطفاً یک‌بارِ دیگه بفرمایید.")
        return
    answer, ctx = await assistant.reply(CHANNEL, user.id, text, user_name=_full_name(user))
    await _deliver(context, msg, user, text, answer, ctx)


async def _notify_admins(context, user, last_text, handoff):
    if not config.ADMIN_USER_IDS:
        return
    uname = f"@{user.username}" if user.username else "—"
    note = (
        "🔔 درخواست اپراتور (تلگرام)\n"
        f"کاربر: {user.full_name} ({uname}) | آیدی: {user.id}\n"
        f"دلیل: {handoff.get('reason', '')}\n"
        f"تماس: {handoff.get('contact') or '—'}\n"
        f"آخرین پیام: {last_text}"
    )
    for admin_id in config.ADMIN_USER_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=note)
        except Exception as e:  # noqa: BLE001
            print(f"[tg] ارسال هشدار به ادمین {admin_id} ناموفق: {e}")


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", _start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("reset", _reset, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.PHOTO, _on_photo))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.VOICE | filters.AUDIO), _on_voice))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, _on_message))
    app.add_handler(CallbackQueryHandler(_on_order_callback, pattern=r"^ord:"))
    app.add_handler(CallbackQueryHandler(_on_xrcp_callback, pattern=r"^xrcp:"))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, _on_group))

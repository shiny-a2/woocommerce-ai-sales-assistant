"""کانال تلگرام دستیار فروش: هندلرهای python-telegram-bot."""
from __future__ import annotations

import base64

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import json

import assistant
import botusers
import config
import llm
import sessions
import tools

CHANNEL = "telegram"
_name_pushed = set()  # کاربرانی که نامِ تلگرامی‌شان یک‌بار به CRM رفته


def _full_name(user):
    nm = (user.first_name or "")
    if user.last_name:
        nm += " " + user.last_name
    return nm.strip()


_sent_cards = {}  # message_id کارتِ ارسال‌شده → {id, name, reference, url}
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
        return "چشم 🔎 بذار بهترین گزینه‌ها رو برات پیدا کنم…"
    return "چشم 🙏 الان برات بررسی می‌کنم…"


def _wants_wrist(text):
    t = text or ""
    for kw in ("مچ", "روی مج", "رو مج", "مج دست", "مج‌دست", "روی دست", "رو دست",
               "روی دستم", "رو دستم", "روی دستش"):
        if kw in t:
            return True
    return False

_WELCOME = (
    "سلام و وقت‌بخیر 🌟\n"
    "خیلی خوش اومدی به فروشگاهِ نمونه 😊\n"
    "من دستیارِ هوشمندِ ساعتِ تو هستم و با کمال میل کمکت می‌کنم بهترین ساعت رو پیدا کنی ⌚\n"
    "هر وقت آماده بودی، راحت بگو دنبال چه ساعتی هستی؛ مثلاً مردانه یا زنانه، اسپرت یا کلاسیک، یا یه بودجهٔ تقریبی 🙂"
)


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    botusers.add_started(update.effective_user.id)
    sessions.reset(CHANNEL, update.effective_user.id)
    await update.message.reply_text(_WELCOME)


async def _reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sessions.reset(CHANNEL, update.effective_user.id)
    await update.message.reply_text("گفتگو از نو شروع شد ✅ چطور کمکتون کنم؟")


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
            _sent_cards[sent.message_id] = {
                "id": c.get("id"), "name": c.get("name", ""),
                "reference": c.get("reference", ""), "url": c.get("url", ""),
            }
            if len(_sent_cards) > 600:
                for k in list(_sent_cards)[:200]:
                    _sent_cards.pop(k, None)


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


async def _post_staff_request(context, req):
    """درخواستِ عکس/ویدئوی مچ‌دست را در گروهِ کاری می‌گذارد."""
    cap = (
        "همکاران عزیز 🙏\n"
        "لطفاً عکس و ویدئوی روی مچ‌دستِ این ساعت رو بگیرید و حتماً همین پیام رو ریپلای کنید و تصاویر/ویدئوها رو بفرستید.\n\n"
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


async def _on_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیام‌های گروه: لاگِ آیدی (برای پیکربندی) + دریافتِ مدیای همکار و تحویل."""
    m = update.effective_message
    if not m or not m.chat or m.chat.type not in ("group", "supergroup"):
        return
    if not config.STAFF_GROUP_ID:
        print(f"[tg] گروه شناسایی شد → id={m.chat_id} | {m.chat.title}")
        return
    if m.chat_id != config.STAFF_GROUP_ID or not m.reply_to_message:
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


async def _deliver(context, msg, user, source_text, answer, ctx):
    if user:
        botusers.add_user(user.id)
    cards = ctx.get("cards") or []
    if answer:
        await msg.reply_text(answer, disable_web_page_preview=bool(cards))
    if cards:
        await _send_cards(context, msg, cards)
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
    # ذخیره‌ی نام: اگر مدل نامی گرفت، همان؛ وگرنه یک‌بار نامِ تلگرامیِ خودِ کاربر
    if ctx.get("name_update"):
        await _save_name_to_crm(user, ctx["name_update"])
    elif user and user.id not in _name_pushed and (user.first_name or user.last_name):
        _name_pushed.add(user.id)
        await _save_name_to_crm(user, {"first_name": user.first_name or "", "last_name": user.last_name or ""})
    if ctx.get("handoff"):
        await _notify_admins(context, user, source_text, ctx["handoff"])


async def _handle_wrist(context, msg, user, product_id):
    """تحویلِ قطعیِ عکس/ویدئوی مچ‌دست برای محصولِ مشخص — مستقل از مدل (تا وعدهٔ توخالی ندهد)."""
    ctx = {}
    try:
        res = json.loads(await tools.dispatch("get_wrist_media", json.dumps({"product_id": product_id}), ctx))
    except Exception:  # noqa: BLE001
        res = {}
    if ctx.get("wrist_media"):
        answer = "چشم 🙌 اینم عکس و ویدئوی روی مچ‌دستِ همین ساعت، ببین چطور می‌شینه:"
    elif ctx.get("wrist_media_request"):
        answer = ("چشم 🙌 الان از همکارام می‌خوام عکس و ویدئوی روی مچ‌دستِ همین ساعت رو بگیرن؛ "
                  "به‌محضِ آماده‌شدن همین‌جا برات می‌فرستم 🙏")
    elif res.get("company_stock"):
        answer = ("این مدل از موجودیِ شرکتِ واردکننده‌ست و فعلاً امکانِ عکس/ویدئوی روی مچ‌دست براش نداریم 🙏 "
                  "ولی همهٔ مشخصاتش رو با کمال میل برات می‌گم.")
    else:
        answer = "فعلاً عکس/ویدئوی روی مچِ این مدل رو ندارم 🙏 ولی مشخصاتِ کامل و لینکش رو برات می‌فرستم."
    await _deliver(context, msg, user, msg.text, answer, ctx)


async def _on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    user = update.effective_user
    text = msg.text
    # اگر به کارتِ محصول ریپلای کرده:
    if msg.reply_to_message:
        prod = _sent_cards.get(msg.reply_to_message.message_id)
        if prod:
            # درخواستِ عکس/ویدئوی روی مچ → همین محصول را قطعی تحویل بده (بدون اتکا به مدل)
            if _wants_wrist(msg.text):
                await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
                await _handle_wrist(context, msg, user, prod["id"])
                return
            # وگرنه همان محصول را به مدل بشناسان (دیگر اسم/مشخصات نپرسد)
            text = f"(مشتری به این محصول اشاره دارد: {prod['name']} — آیدی {prod['id']}) " + text

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
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
    try:
        f = await msg.photo[-1].get_file()
        data = bytes(await f.download_as_bytearray())
        url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    except Exception as e:  # noqa: BLE001
        print(f"[tg] دریافت عکس ناموفق: {e}")
        await msg.reply_text("نتونستم عکس رو بگیرم، لطفاً دوباره بفرست 🙏")
        return
    answer, ctx = await assistant.reply_image(CHANNEL, user.id, url, msg.caption or "", user_name=_full_name(user))
    await _deliver(context, msg, user, "[تصویر]", answer, ctx)


async def _on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    voice = msg.voice or msg.audio if msg else None
    if not voice:
        return
    user = update.effective_user
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
    try:
        f = await voice.get_file()
        data = bytes(await f.download_as_bytearray())
        text = await llm.transcribe(data, "voice.ogg")
    except Exception as e:  # noqa: BLE001
        print(f"[tg] رونویسی ویس ناموفق: {e}")
        await msg.reply_text("نتونستم صدا رو بفهمم، یه بار دیگه بگو یا تایپ کن 🙏")
        return
    if not text:
        await msg.reply_text("صدا رو واضح نگرفتم، لطفاً دوباره 🙏")
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
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, _on_group))

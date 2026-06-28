"""هسته‌ی دستیار: پیام کاربر → حلقه‌ی جی‌پی‌تی+ابزار → پاسخ.

مستقل از کانال (تلگرام/وب)؛ هر کانال فقط reply() را صدا می‌زند.
"""
from __future__ import annotations

import llm
import persona
import sessions
import textfmt

_FALLBACK = "ببخشید، یک اشکالِ فنیِ کوچک پیش اومد 🙏 لطفاً چند لحظهٔ دیگه دوباره بفرمایید؛ در خدمتم."


def _name_hint(user_name):
    nm = (user_name or "").strip()
    if not nm:
        return None
    return {"role": "system", "content": f"نامِ تلگرامیِ این کاربر: «{nm}». او را با همین نام و محترمانه صدا بزن، نه اسمِ دیگری."}


async def reply(channel, user_id, text, user_name=None):
    """یک پیام را پاسخ می‌دهد.

    خروجی: (متن پاسخ، ctx) که ctx ممکن است شامل {"handoff": {...}} باشد.
    """
    text = (text or "").strip()
    ctx: dict = {}
    if not text:
        return ("سلام 🌟 در خدمتم؛ چطور می‌تونم کمکتون کنم؟", ctx)

    messages = [{"role": "system", "content": persona.system_prompt()}]
    hint = _name_hint(user_name)
    if hint:
        messages.append(hint)
    messages.extend(sessions.history(channel, user_id))
    messages.append({"role": "user", "content": text})

    ctx["shown_ids"] = list(sessions.shown_ids(channel, user_id))
    try:
        answer = await llm.chat(messages, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در پاسخ‌دهی: {type(e).__name__}: {e}")
        return (_FALLBACK, ctx)

    if not answer:
        answer = _FALLBACK
    answer = textfmt.clean_for_chat(answer)
    if ctx.get("cards"):  # کارت‌ها جدا (عکس) نمایش داده می‌شوند؛ از متن حذفشان کن
        answer = textfmt.strip_product_lines(answer) or "چند گزینهٔ خوب و مناسب براتون پیدا کردم 🌟 در ادامه ببینید:"

    # فقط در صورت موفقیت، تاریخچه را ذخیره کن
    sessions.append(channel, user_id, "user", text)
    sessions.append(channel, user_id, "assistant", answer)
    sessions.add_shown(channel, user_id, [c.get("id") for c in ctx.get("cards", [])])
    return (answer, ctx)


async def reply_image(channel, user_id, image_data_url, caption="", user_name=None):
    """پاسخ به یک تصویر ساعت: شناسایی و پیشنهاد همان/مشابه‌ها."""
    ctx: dict = {"shown_ids": list(sessions.shown_ids(channel, user_id))}
    user_text = ((caption or "").strip() + " ").strip()
    user_text += " این ساعت را از روی تصویر شناسایی کن (جنسیت، رنگ، استایل، برند اگر پیداست) و با search_watches همان یا مشابه‌هایش را پیدا کن، بعد حتماً با show_products به‌صورت کارت نشان بده."

    messages = [{"role": "system", "content": persona.system_prompt()}]
    hint = _name_hint(user_name)
    if hint:
        messages.append(hint)
    messages.extend(sessions.history(channel, user_id))
    messages.append({"role": "user", "content": [
        {"type": "text", "text": user_text},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]})

    try:
        answer = await llm.chat(messages, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در reply_image: {type(e).__name__}: {e}")
        return (_FALLBACK, ctx)

    answer = textfmt.clean_for_chat(answer) or _FALLBACK
    if ctx.get("cards"):
        answer = textfmt.strip_product_lines(answer) or "چند ساعتِ نزدیک به تصویری که فرستادید پیدا کردم 🌟 ببینید:"

    sessions.append(channel, user_id, "user", "[تصویر ساعت] " + (caption or ""))
    sessions.append(channel, user_id, "assistant", answer)
    sessions.add_shown(channel, user_id, [c.get("id") for c in ctx.get("cards", [])])
    return (answer, ctx)


async def answer_messages(messages, system_extra=""):
    """پاسخ به یک گفتگوی آماده (فرمت {role, content}) — برای اتصال CRM/sale-brain.

    پرسونای محصول‌آگاهِ ما + (اختیاری) دستور سیستمیِ CRM را ترکیب می‌کند و
    با ابزارهای ووکامرس پاسخ می‌سازد. خروجی: (متن، ctx) که ctx ممکن است
    شامل {"handoff": {...}} باشد (وقتی مدل درخواست ارجاع به اپراتور داد).
    """
    system = persona.system_prompt()
    if system_extra:
        system = system + "\n\n" + system_extra.strip()

    convo = [{"role": "system", "content": system}]
    for m in messages or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            convo.append({"role": role, "content": content})
    if len(convo) == 1:  # هیچ پیام واقعی‌ای نبود
        return ("", {})

    ctx: dict = {}
    try:
        text = await llm.chat(convo, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در answer_messages: {type(e).__name__}: {e}")
        text = ""
    text = textfmt.clean_for_chat(text)
    cards = ctx.get("cards") or []
    if cards:  # وب/CRM عکس‌کارت ندارد؛ متن را پاک و کارت‌ها را به‌صورت متن ضمیمه کن
        intro = textfmt.strip_product_lines(text) or "چند گزینهٔ خوب و مناسب براتون پیدا کردم 🌟 در ادامه ببینید:"
        text = (intro + "\n\n" + _cards_as_text(cards)).strip()
    wm = ctx.get("wrist_media")
    if wm and wm.get("ids"):  # چت سایت: لینکِ پستِ چنل (عمومی)
        links = "\n".join(f"https://t.me/{wm['channel']}/{i}" for i in wm["ids"][:4])
        text = (text + "\n\n🎥 عکس و ویدئوی روی مچ‌دستِ همین ساعت:\n" + links).strip()
    return (text, ctx)


def _cards_as_text(cards):
    out = []
    for c in cards:
        block = ["⌚ " + (c.get("name", "") or "")]
        if c.get("on_sale") and c.get("sale_price_label"):
            reg = c.get("regular_price_label", "")
            block.append(f"🔖 {c['sale_price_label']}" + (f" (قبلاً {reg})" if reg else "") + " ✨")
        elif c.get("price_label"):
            block.append("💰 " + c["price_label"])
        av = c.get("availability", "")
        ship = c.get("shipping_time", "")
        if av or ship:
            emoji = "⚡" if ship == "ارسال فوری" else "🚚"
            block.append(emoji + " " + " · ".join(x for x in (av, ship) if x))
        if c.get("url"):
            block.append("🔗 " + c["url"])
        out.append("\n".join(block))
    return "\n\n".join(out)

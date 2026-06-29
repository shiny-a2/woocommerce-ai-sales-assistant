"""هسته‌ی دستیار: پیام کاربر → حلقه‌ی جی‌پی‌تی+ابزار → پاسخ.

مستقل از کانال (تلگرام/وب)؛ هر کانال فقط reply() را صدا می‌زند.
"""
from __future__ import annotations

import llm
import persona
import sessions
import textfmt
import woo

_FALLBACK = "ببخشید، یک اشکالِ فنیِ کوچک پیش اومد 🙏 لطفاً چند لحظهٔ دیگه دوباره بفرمایید؛ در خدمتم."


def _name_hint(user_name):
    nm = (user_name or "").strip()
    if not nm:
        return None
    return {"role": "system", "content": f"نامِ تلگرامیِ این کاربر: «{nm}». او را با همین نام و محترمانه صدا بزن، نه اسمِ دیگری."}


def _phone_hint(phone):
    p = (phone or "").strip()
    if not p:
        return None
    return {"role": "system", "content": f"شمارهٔ تماسِ این مشتری از قبل موجود است: «{p}». "
            "برای ثبتِ سفارش/پیگیری از همین شماره استفاده کن و دوباره شماره نپرس."}


async def reply(channel, user_id, text, user_name=None, customer_phone=None):
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
    ph = _phone_hint(customer_phone)
    if ph:
        messages.append(ph)
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


async def _reply_context_sheet(rc):
    """مشخصاتِ کاملِ محصولی که مشتری به کارتش ریپلای کرده — برای تزریقِ قطعی به مغز.

    rc: {"url"/"name"/"reference"} از کارتِ ریپلای‌شده. محصول را دقیق resolve می‌کند (slug/کدِ رفرنس)
    تا با محصولِ دیگری اشتباه نشود (ریشهٔ باگِ تروساردی→سیتیزن)."""
    try:
        brief = await woo.resolve_product(
            url=(rc.get("url") or ""), name=(rc.get("name") or ""), reference=(rc.get("reference") or ""))
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] resolveِ محصولِ ریپلای ناموفق: {type(e).__name__}: {e}")
        return ""
    if not brief or not brief.get("id"):
        print(f"[assistant] resolve بدون نتیجه: url={rc.get('url')!r} name={rc.get('name')!r}")
        return ""
    try:
        full = await woo.get_product(brief["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] get_product ناموفق ({brief['id']})؛ از brief استفاده می‌کنم: {type(e).__name__}: {e}")
        full = brief  # به‌جای خطای کامل، با همان خلاصهٔ کارت جواب بده
    parts = [full.get("name", "")]
    if full.get("price_label"):
        parts.append("قیمت: " + full["price_label"])
    if full.get("shipping_time"):
        parts.append("ارسال: " + full["shipping_time"])
    for a in (full.get("attributes") or []):
        nm = (a.get("name") or "").strip()
        opts = a.get("options") or []
        if nm and opts:
            parts.append(f"{nm}: " + "، ".join(str(o) for o in opts))
    sheet = " | ".join(x for x in parts if x)
    if not sheet:
        return ""
    return ("⚡ مشتری به کارتِ یک محصولِ مشخص ریپلای کرده و دربارهٔ **همان** می‌پرسد. "
            f"مشخصاتِ کاملِ همان محصول: {sheet}. فقط دربارهٔ همین محصول جواب بده، "
            "محصولِ دیگری را با آن اشتباه نگیر و کارتِ جدید نشان نده مگر مشتری صریحاً بخواهد.")


async def answer_messages(messages, system_extra="", render_cards_inline=True, reply_context=None, customer=None):
    """پاسخ به یک گفتگوی آماده (فرمت {role, content}) — برای اتصال CRM/sale-brain و کانال‌ها.

    پرسونای محصول‌آگاهِ ما + (اختیاری) دستور سیستمیِ CRM را ترکیب می‌کند و
    با ابزارهای ووکامرس پاسخ می‌سازد. خروجی: (متن، ctx) که ctx ممکن است
    شامل {"cards": [...], "wrist_media": {...}, "handoff": {...}, "order": {...}} باشد.

    render_cards_inline=True: کارت‌ها را به‌صورت متن داخلِ پاسخ می‌پزد (برای کانالِ متن‌محور).
    render_cards_inline=False: فقط مقدمهٔ تمیز را در متن می‌گذارد و کارت‌ها را ساختاریافته در
    ctx['cards'] نگه می‌دارد تا کانال خودش آن‌ها را (به‌صورت عکس/کارت) رندر کند.
    """
    system = persona.system_prompt()
    extra = (system_extra or "").strip()
    if reply_context:  # مشتری به کارتِ یک محصول ریپلای کرده → مشخصاتِ همان را قطعی تزریق کن
        sheet = await _reply_context_sheet(reply_context)
        if sheet:
            extra = (extra + "\n\n" + sheet).strip() if extra else sheet
    if extra:
        system = system + "\n\n" + extra

    convo = [{"role": "system", "content": system}]
    for m in messages or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            convo.append({"role": role, "content": content})
    if len(convo) == 1:  # هیچ پیام واقعی‌ای نبود
        return ("", {})

    ctx: dict = {}
    # ردگیریِ کارت‌های نشان‌داده‌شده per (channel,user) → عدمِ‌تکرار + صفحه‌بندیِ ۷→۵→۳ روی کانال‌ها
    _ck = (str(customer.get("channel") or "ch"), str(customer.get("id"))) if (customer and customer.get("id")) else None
    if _ck:
        ctx["shown_ids"] = list(sessions.shown_ids(_ck[0], _ck[1]))
    try:
        text = await llm.chat(convo, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در answer_messages: {type(e).__name__}: {e}")
        text = ""
    text = textfmt.clean_for_chat(text)
    cards = ctx.get("cards") or []
    if cards and render_cards_inline:  # کانالِ متن‌محور: متن را پاک و کارت‌ها را به‌صورت متن ضمیمه کن
        intro = textfmt.strip_product_lines(text) or "چند گزینهٔ خوب و مناسب براتون پیدا کردم 🌟 در ادامه ببینید:"
        text = (intro + "\n\n" + _cards_as_text(cards)).strip()
    elif cards:  # کانال خودش کارت‌ها را رندر می‌کند → فقط مقدمهٔ تمیزِ گفتگویی
        text = textfmt.strip_product_lines(text) or "چند گزینهٔ خوب و مناسب براتون پیدا کردم 🌟 در ادامه ببینید:"
    wm = ctx.get("wrist_media")
    if wm and wm.get("ids"):  # لینکِ پستِ چنلِ مدیای روی‌مچ — برای همهٔ کانال‌ها (نه فقط چت‌سایت)
        links = "\n".join(f"https://t.me/{wm['channel']}/{i}" for i in wm["ids"][:4])
        text = (text + "\n\n🎥 عکس و ویدئوی روی مچ‌دستِ همین ساعت:\n" + links).strip()
    if _ck and ctx.get("cards"):  # ثبتِ کارت‌های نشان‌داده‌شده تا دفعهٔ بعد تکرار نشوند
        sessions.add_shown(_ck[0], _ck[1], [c.get("id") for c in ctx["cards"] if c.get("id")])
    return (text, ctx)


async def polish_staff_reply(staff_text, question=""):
    """پاسخِ خامِ همکار را به پیامِ گرم و حرفه‌ایِ مشتری‌پسند بازنویسی می‌کند (بدونِ افزودنِ اطلاعاتِ نادرست)."""
    staff_text = (staff_text or "").strip()
    if not staff_text:
        return ""
    sys = ("تو مشاورِ فروشِ فروشگاهِ نمونهی. همکارت پاسخِ کوتاهی به سؤالِ یک مشتری داده. این پاسخ را به یک پیامِ "
           "گرم، مؤدبانه و فارسیِ روان برای همان مشتری بازنویسی کن. هیچ اطلاعاتِ تازه یا قیمت یا ادعای نادرستی "
           "اضافه نکن — فقط همین پاسخ را زیبا و حرفه‌ای و کوتاه بیان کن. اگر پاسخِ همکار لینک/عدد دارد عیناً نگه دار.")
    user = (f"سؤالِ مشتری: {question}\n" if question else "") + f"پاسخِ همکار: {staff_text}"
    try:
        out = await llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], {})
        return textfmt.clean_for_chat(out) or staff_text
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] polish_staff_reply ناموفق: {type(e).__name__}: {e}")
        return staff_text


async def answer_image(image_data_url, caption="", messages=None, render_cards_inline=True):
    """تشخیصِ عکسِ ساعت (بدونِ حالت/session) برای همهٔ کانال‌ها — مثلِ answer_messages ولی با تصویر.

    خروجی: (text, ctx) که ctx['cards'] محصولاتِ پیشنهادی را دارد."""
    user_text = (caption or "").strip()
    user_text = (user_text + "\n\n").strip() + (
        "\nابتدا تصویر را با دقت بررسی کن و فقط وقتی **۹۰٪+ مطمئنی** اقدام کن:\n"
        "• اگر **فیش/رسیدِ پرداختِ بانکی** است: جستجوی ساعت نکن؛ بگو «رسیدِ پرداختتون دریافت شد ✅ "
        "همکاران بررسی می‌کنن و نتیجهٔ تأیید رو خدمتتون اعلام می‌کنیم 🙏»، و اگر مبلغ/تاریخ/شمارهٔ پیگیری خواناست کوتاه بازگو کن.\n"
        "• اگر **ساعت** است و با اطمینان تشخیصش دادی: با search_watches همان یا مشابه‌هایش را پیدا کن، "
        "بعد حتماً با show_products به‌صورت کارت نشان بده.\n"
        "• اگر **مطمئن نیستی** (برند/مدل واضح نیست، تصویر مبهم است، یا مطمئن نیستی موجود داریم): "
        "**محصولِ حدسی نشان نده**؛ فقط بگو «عکستون رو دیدم، برای دقت از همکارانم می‌پرسم و جوابتون رو می‌فرستم 🙏» "
        "(تا به گروهِ همکاران ارجاع شود). هرگز محصول یا برندِ اشتباه به مشتری نسبت نده.")
    convo = [{"role": "system", "content": persona.system_prompt()}]
    for m in (messages or []):
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            convo.append({"role": role, "content": content})
    convo.append({"role": "user", "content": [
        {"type": "text", "text": user_text},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]})
    ctx: dict = {}
    try:
        text = await llm.chat(convo, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در answer_image: {type(e).__name__}: {e}")
        text = ""
    text = textfmt.clean_for_chat(text)
    cards = ctx.get("cards") or []
    _intro = "چند ساعتِ نزدیک به تصویری که فرستادید پیدا کردم 🌟 ببینید:"
    if cards and render_cards_inline:
        intro = textfmt.strip_product_lines(text) or _intro
        text = (intro + "\n\n" + _cards_as_text(cards)).strip()
    elif cards:
        text = textfmt.strip_product_lines(text) or _intro
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

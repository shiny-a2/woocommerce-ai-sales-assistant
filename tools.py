"""تعریف ابزارهای جی‌پی‌تی (function calling) و توزیع فراخوانی به ووکامرس."""
from __future__ import annotations

import json

import mediaidx
import persona
import woo

# ---------- شِمای ابزارها برای OpenAI ----------
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "جستجوی عمومی محصولات غیرِ ساعت (زیورآلات، عینک و…) بر اساس کلمه‌ی کلیدی/دسته/قیمت. برای ساعت از search_watches استفاده کن.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "کلمه‌ی کلیدی فارسی، مثلا «گردنبند» یا «انگشتر طلا»"},
                    "category": {"type": "integer", "description": "آیدی دسته‌بندی (اختیاری؛ از list_categories بگیر)"},
                    "min_toman": {"type": "integer", "description": "حداقل قیمت به تومان (اختیاری)"},
                    "max_toman": {"type": "integer", "description": "حداکثر قیمت به تومان (اختیاری)"},
                    "in_stock_only": {"type": "boolean", "description": "فقط کالاهای موجود (پیش‌فرض true)"},
                    "limit": {"type": "integer", "description": "تعداد نتایج (۱ تا ۱۲، پیش‌فرض ۶)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_products",
            "description": "نمایش محصولات به‌صورت کارت (عکس + قیمت + دکمهٔ «مشاهده در سایت») در تلگرام. بعد از search_watches، آیدیِ ۳ تا ۴ محصول منتخب را بده. در متن خودت فقط یک جملهٔ کوتاه مقدمه بنویس، نه فهرست کامل محصولات.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_ids": {"type": "array", "items": {"type": "integer"}, "description": "آیدی محصولات (۳ تا ۴ تا)"},
                },
                "required": ["product_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "گرفتن جزئیات کامل یک محصول مشخص با آیدی آن (توضیحات، قیمت، موجودی، ویژگی‌ها).",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "integer"}},
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_categories",
            "description": "فهرست دسته‌بندی‌های محصولات فروشگاه (برای کمک به محدودکردن جستجو).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_watches",
            "description": "جستجوی هوشمند ساعت با تشخیص درستِ جنسیت و مشخصات. برای هر درخواست ساعت از همین استفاده کن (نه search_products). جنسیت و استایل دقیق تشخیص داده می‌شوند.",
            "parameters": {
                "type": "object",
                "properties": {
                    "gender": {"type": "string", "enum": ["مردانه", "زنانه", "بچگانه", "ست"], "description": "جنسیت/مناسب برای"},
                    "movement": {"type": "string", "description": "نوع موتور، مثل «اتوماتیک» یا «کوارتز» یا «اتوماتیک سوئیسی»"},
                    "dial_color": {"type": "string", "description": "رنگ صفحه، مثل «سبز»، «مشکی»، «سفید»"},
                    "strap_color": {"type": "string", "description": "رنگ بند"},
                    "case_color": {"type": "string", "description": "رنگ قاب"},
                    "strap_material": {"type": "string", "description": "جنس بند، مثل «استیل»، «چرم»، «سیلیکون»"},
                    "brand": {"type": "string", "description": "نام برند، مثل «سیتیزن»، «دنیل کلین»"},
                    "style": {"type": "string", "enum": ["کلاسیک", "اسپرت", "فشن", "لاکچری", "غواصی", "دیجیتال"], "description": "استایل"},
                    "min_toman": {"type": "integer", "description": "حداقل بودجه اگر مشتری بازه داد"},
                    "max_toman": {"type": "integer", "description": "حداکثر بودجه اگر مشتری بازه داد"},
                    "target_toman": {"type": "integer", "description": "اگر مشتری فقط یک قیمت گفت (نه بازه)، همان را اینجا بده؛ خودش بازه‌ی ۱۰٪ پایین تا ۱۵٪ بالا می‌سازد"},
                    "on_sale": {"type": "boolean", "description": "اگر مشتری «تخفیف‌خورده/حراج/آف» خواست true بده"},
                    "query": {"type": "string", "description": "کلمه‌ی آزاد اگر معیار دیگری بود"},
                    "limit": {"type": "integer", "description": "تعداد نتیجه: نوبت اول ۷، بعد ۵، بعد ۳"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "order_status",
            "description": "استعلام وضعیت یک سفارش. حتماً هم شماره سفارش و هم شماره تماس مشتری را بگیر؛ بدون تطابق شماره تماس اطلاعات لو نمی‌رود.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_number": {"type": "string", "description": "شماره سفارش"},
                    "phone": {"type": "string", "description": "شماره تماس ثبت‌شده‌ی مشتری"},
                },
                "required": ["order_number", "phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_wrist_media",
            "description": "عکس/ویدئوی روی‌مچ‌دستِ یک ساعت را برای ارسال به مشتری آماده می‌کند (وقتی مشتری به آن ساعت علاقه نشان داد، برای کمک به انتخاب). آیدیِ همان محصول را بده.",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "integer"}},
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_customer_name",
            "description": "ثبت نام و نام‌خانوادگیِ مشتری وقتی آن را گفت (برای به‌روزرسانی پروفایل در دیتابیس).",
            "parameters": {
                "type": "object",
                "properties": {
                    "first_name": {"type": "string", "description": "نام"},
                    "last_name": {"type": "string", "description": "نام خانوادگی"},
                },
                "required": ["first_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_human",
            "description": "ارجاع گفتگو به اپراتور انسانی وقتی موضوع خارج از توان توست یا مشتری انسان می‌خواهد (تخفیف خاص، شکایت، مورد پیچیده).",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "خلاصه‌ی کوتاه دلیل ارجاع"},
                    "contact": {"type": "string", "description": "راه تماس مشتری اگر داد (اختیاری)"},
                },
                "required": ["reason"],
            },
        },
    },
]


def _json(obj):
    return json.dumps(obj, ensure_ascii=False)


async def dispatch(name, args_json, ctx):
    """یک فراخوانی ابزار را اجرا و نتیجه را به‌صورت رشته‌ی JSON برمی‌گرداند.

    ctx: دیکشنری مشترک با لایه‌ی کانال؛ مثلاً برای ثبت درخواست ارجاع به اپراتور.
    """
    try:
        args = json.loads(args_json) if args_json else {}
    except (json.JSONDecodeError, TypeError):
        args = {}
    try:
        if name == "search_products":
            items = await woo.search_products(
                query=args.get("query"),
                category=args.get("category"),
                min_toman=args.get("min_toman"),
                max_toman=args.get("max_toman"),
                attribute=args.get("attribute"),
                attribute_term=args.get("attribute_term"),
                in_stock_only=args.get("in_stock_only", True),
                limit=args.get("limit", 6),
            )
            return _json({"count": len(items), "products": items})

        if name == "show_products":
            cards = await woo.get_briefs(args.get("product_ids") or [])
            ctx.setdefault("cards", []).extend(cards)
            ctx.setdefault("shown_ids", [])
            ctx["shown_ids"].extend(c["id"] for c in cards if c.get("id"))
            return _json({"ok": True, "shown": len(cards)})

        if name == "get_product":
            return _json(await woo.get_product(args["product_id"]))

        if name == "list_categories":
            return _json({"categories": await woo.list_categories()})

        if name == "search_watches":
            items = await woo.search_watches(
                gender=args.get("gender"),
                movement=args.get("movement"),
                dial_color=args.get("dial_color"),
                strap_color=args.get("strap_color"),
                case_color=args.get("case_color"),
                strap_material=args.get("strap_material"),
                brand=args.get("brand"),
                style=args.get("style"),
                min_toman=args.get("min_toman"),
                max_toman=args.get("max_toman"),
                target_toman=args.get("target_toman"),
                query=args.get("query"),
                on_sale=bool(args.get("on_sale")),
                limit=args.get("limit", 7),
                exclude_ids=ctx.get("shown_ids") or [],
            )
            link = woo.site_search_link(
                gender=args.get("gender"), style=args.get("style"),
                dial_color=args.get("dial_color"), strap_color=args.get("strap_color"),
                brand=args.get("brand"), movement=args.get("movement"), query=args.get("query"),
            )
            return _json({
                "count": len(items),
                "products": items,
                "site_link": link,
                "note": "برای نمایش، آیدیِ این محصولات را به show_products بده تا کارت شوند؛ در متن خودت محصولات را فهرست نکن.",
            })

        if name == "order_status":
            res = await woo.order_status(args.get("order_number"), args.get("phone"))
            if res.get("found"):
                res["status_fa"] = persona.STATUS_FA.get(res.get("status"), res.get("status"))
            return _json(res)

        if name == "get_wrist_media":
            briefs = await woo.get_briefs([args["product_id"]])
            if not briefs:
                return _json({"available": False})
            b = briefs[0]
            media = mediaidx.lookup(reference=b.get("reference"), url=b.get("url"))
            if media and media.get("ids"):
                ctx["wrist_media"] = {**media, "product_name": b.get("name", "")}
                return _json({"available": True, "count": len(media["ids"])})
            # در چنل نبود؛ اگر کالا ارسال‌فوری (موجودِ فروشگاه) است، از همکاران بخواه
            if b.get("shipping_time") == "ارسال فوری":
                ctx["wrist_media_request"] = {
                    "reference": b.get("reference", ""), "name": b.get("name", ""),
                    "image": b.get("image"), "url": b.get("url", ""),
                }
                return _json({"available": False, "requested": True})
            # کالای شرکتی (موجودی شرکت واردکننده) → امکان عکس/ویدئوی روی مچ نیست
            return _json({"available": False, "company_stock": True})

        if name == "save_customer_name":
            ctx["name_update"] = {
                "first_name": (args.get("first_name") or "").strip(),
                "last_name": (args.get("last_name") or "").strip(),
            }
            return _json({"ok": True})

        if name == "request_human":
            ctx["handoff"] = {
                "reason": args.get("reason", ""),
                "contact": args.get("contact", ""),
            }
            return _json({"ok": True, "message": "به اپراتور ارجاع داده شد؛ به‌زودی با مشتری تماس می‌گیرند."})

        return _json({"error": f"ابزار ناشناخته: {name}"})
    except Exception as e:  # noqa: BLE001 — خطا را به مدل برمی‌گردانیم تا خودش مدیریت کند
        return _json({"error": f"{type(e).__name__}: {e}"})

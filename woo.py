"""کلاینت رابط ووکامرس (کتابخانه‌ی همگام، پیچیده‌شده برای asyncio).

فقط خواندن: جستجوی محصول، جزئیات محصول، دسته‌بندی‌ها و استعلام وضعیت سفارش.
"""
from __future__ import annotations

import asyncio
import html
import re
import time

import requests
from requests.exceptions import RequestException
from woocommerce import API

import config

_api = None


def _client():
    global _api
    if _api is None:
        _api = API(
            url=config.WOO_URL,
            consumer_key=config.WOO_CK,
            consumer_secret=config.WOO_CS,
            version="wc/v3",
            timeout=(4, 25),  # (اتصال، خواندن) — اتصالِ سریع‌شکست تا روی نوسانِ شبکه سریع‌تر retry بزند
            query_string_auth=True,
        )
    return _api


def _get_sync(endpoint, params=None):
    # هاستِ فروشگاه گاهی اتصال را تایم‌اوت می‌کند؛ چند بار تلاشِ مجدد با مکثِ کوتاه
    last = None
    for attempt in range(3):
        try:
            resp = _client().get(endpoint, params=params or {})
            resp.raise_for_status()
            return resp.json()
        except RequestException as e:
            last = e
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    raise last


async def get(endpoint, params=None):
    return await asyncio.to_thread(_get_sync, endpoint, params)


# ---------- کمک‌تابع‌ها ----------

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text, limit=400):
    """تگ‌های HTML را حذف و متن تمیز برمی‌گرداند."""
    if not text:
        return ""
    clean = html.unescape(_TAG_RE.sub(" ", text))
    clean = re.sub(r"\s+", " ", clean).strip()
    if limit and len(clean) > limit:
        clean = clean[:limit].rstrip() + "…"
    return clean


def _to_toman(raw):
    """مبلغ خام فروشگاه (ریال) را به تومان تبدیل می‌کند."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not val:
        return 0
    return int(val / config.MONEY_DIVISOR)


def price_label(raw):
    """رشته‌ی قیمت قابل‌نمایش با جداکننده‌ی هزارگان و واحد پول."""
    toman = _to_toman(raw)
    if toman is None:
        return ""
    if toman == 0:
        return "تماس بگیرید"
    return f"{toman:,} {config.CURRENCY_LABEL}"


def availability(p):
    """وضعیت موجودی و زمان ارسال بر اساس قانون فروشگاه.

    manage_stock=True و موجودی≥۱ → فروشگاه (ارسال فوری).
    manage_stock=False ولی instock → موجودی شرکت واردکننده (۳ تا ۷ روز کاری).
    """
    status = p.get("stock_status")
    if status != "instock":
        return {"in_stock": False, "label": "ناموجود", "shipping": ""}
    manage = p.get("manage_stock")
    qty = p.get("stock_quantity")
    if manage and isinstance(qty, int) and qty >= 1:
        return {"in_stock": True, "label": "موجود در فروشگاه", "shipping": "ارسال فوری"}
    return {"in_stock": True, "label": "موجودی شرکت واردکننده", "shipping": "۳ تا ۷ روز کاری"}


def attr_options(p, name):
    """مقادیر یک ویژگیِ محصول بر اساس نام دقیق (از پاسخ لیستِ ووکامرس)."""
    for a in (p.get("attributes") or []):
        if (a.get("name") or "").strip() == name:
            return [str(o).strip() for o in (a.get("options") or [])]
    return []


def _product_brief(p):
    """خلاصه‌ی محصول برای فهرست نتایج جستجو."""
    images = p.get("images") or []
    cats = [c.get("name") for c in (p.get("categories") or []) if c.get("name")]
    av = availability(p)
    on_sale = bool(p.get("on_sale"))
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "price_toman": _to_toman(p.get("price")),
        "price_label": price_label(p.get("price")),
        "on_sale": on_sale,
        "regular_price_label": price_label(p.get("regular_price")) if on_sale else "",
        "sale_price_label": price_label(p.get("sale_price")) if on_sale else "",
        "in_stock": av["in_stock"],
        "availability": av["label"],
        "shipping_time": av["shipping"],
        "categories": cats,
        "url": p.get("permalink"),
        "image": images[0].get("src") if images else None,
        "short": strip_html(p.get("short_description"), 160),
        "reference": (attr_options(p, "رفرانس") or [""])[0],
        "warranty": (attr_options(p, "گارانتی") or [""])[0],
        "warranty_provider": (attr_options(p, "گارانتی کننده در ایران") or [""])[0],
    }


def _product_full(p):
    """جزئیات کامل‌تر یک محصول."""
    brief = _product_brief(p)
    attrs = []
    for a in (p.get("attributes") or []):
        opts = a.get("options") or []
        if a.get("name") and opts:
            attrs.append({"name": a.get("name"), "options": opts})
    brief.update(
        {
            "description": strip_html(p.get("description"), 700),
            "regular_price": price_label(p.get("regular_price")),
            "sale_price": price_label(p.get("sale_price")) if p.get("on_sale") else "",
            "attributes": attrs,
            "type": p.get("type"),
            "sku": p.get("sku"),
        }
    )
    return brief


# ---------- محصولات ----------

async def search_products(query=None, category=None, min_toman=None, max_toman=None,
                          in_stock_only=True, limit=6, orderby="popularity",
                          attribute=None, attribute_term=None):
    params = {
        "per_page": max(1, min(int(limit or 6), 12)),
        "status": "publish",
        "orderby": orderby,
        "order": "desc" if orderby in ("popularity", "date", "rating") else "asc",
    }
    if query:
        params["search"] = query
    if category:
        params["category"] = category
    if min_toman:
        params["min_price"] = str(int(min_toman) * config.MONEY_DIVISOR)
    if max_toman:
        params["max_price"] = str(int(max_toman) * config.MONEY_DIVISOR)
    if in_stock_only:
        params["stock_status"] = "instock"
    # فیلتر بر اساس ویژگی (مثلاً برند/جنسیت/نوع موتور)؛ taxonomy مثل pa_brand و term آیدی(ها)
    if attribute and attribute_term:
        params["attribute"] = attribute
        params["attribute_term"] = str(attribute_term)
    items = await get("products", params)
    return [_product_brief(p) for p in items]


async def get_product(product_id):
    p = await get(f"products/{int(product_id)}")
    return _product_full(p)


def _wp_get_sync(endpoint, params=None):
    """درخواست به وردپرس REST (wp/v2) — برای مقالات/برگه‌های برند (عمومی، بدون auth)."""
    url = f"{config.WOO_URL}/wp-json/wp/v2/{endpoint}"
    last = None
    for attempt in range(2):
        try:
            r = requests.get(url, params=params or {}, timeout=(4, 20))
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            last = e
            if attempt < 1:
                time.sleep(1.0)
    raise last


async def get_brand_article(brand):
    """مقاله/برگهٔ سایت دربارهٔ یک برند (تاریخچه/معرفی) را برمی‌گرداند؛ ترجیحاً عنوانش شاملِ نامِ برند است."""
    brand = (brand or "").strip()
    if not brand:
        return None
    bl = brand.lower()
    fallback = None
    for ep in ("posts", "pages"):
        try:
            items = await asyncio.to_thread(
                _wp_get_sync, ep,
                {"search": brand, "per_page": 5, "_fields": "title,excerpt,content,link"},
            )
        except Exception:  # noqa: BLE001 — اگر سایت/اندپوینت در دسترس نبود
            items = []
        for it in items or []:
            title = strip_html((it.get("title") or {}).get("rendered"), 140)
            body = (strip_html((it.get("excerpt") or {}).get("rendered"), 700)
                    or strip_html((it.get("content") or {}).get("rendered"), 700))
            if not (title and body):
                continue
            entry = {"title": title, "summary": body, "link": it.get("link", "")}
            if bl in title.lower():  # بهترین تطابق: نامِ برند در عنوان
                return entry
            if fallback is None:
                fallback = entry
    return fallback


async def get_briefs(ids):
    """خلاصه‌ی چند محصول با آیدی، در یک درخواست (برای ساخت کارت)."""
    ids = [int(i) for i in (ids or []) if str(i).strip().isdigit()]
    if not ids:
        return []
    items = await get("products", {"include": ",".join(str(i) for i in ids), "per_page": len(ids)})
    by_id = {p.get("id"): _product_brief(p) for p in items}
    return [by_id[i] for i in ids if i in by_id]  # ترتیب ورودی حفظ شود


async def search_by_reference(reference, limit=6):
    """محصول(ها) را با کد/رفرنس پیدا می‌کند (جستجوی متنیِ ووکامرس عنوان/SKU/رفرنس را پوشش می‌دهد)."""
    ref = (reference or "").strip()
    if not ref:
        return []
    items = await get("products", {"search": ref, "per_page": max(1, min(int(limit or 6), 12)), "status": "publish"})
    out = []
    for p in items:
        b = _product_brief(p)
        if b.get("price_toman"):
            out.append(b)
    return out


async def list_categories(limit=40):
    cats = await get("products/categories", {"per_page": limit, "orderby": "count", "order": "desc", "hide_empty": True})
    return [{"id": c.get("id"), "name": c.get("name"), "count": c.get("count")} for c in cats]


def _taxonomy(slug):
    slug = slug or ""
    return slug if slug.startswith("pa_") else ("pa_" + slug)


async def list_attributes():
    """فهرست ویژگی‌های سراسری محصول (فقط نام و taxonomy؛ یک درخواست، سریع).

    برای گرفتن مقادیرِ یک ویژگی از attribute_terms استفاده کن.
    """
    attrs = await get("products/attributes", {"per_page": 100})
    return [
        {"id": a.get("id"), "name": a.get("name"), "taxonomy": _taxonomy(a.get("slug"))}
        for a in attrs
    ]


async def attribute_terms(attribute_id, limit=40):
    """مقادیر (terms) یک ویژگی مشخص با آیدی هر مقدار (برای فیلتر در search_products)."""
    terms = await get(
        f"products/attributes/{int(attribute_id)}/terms",
        {"per_page": limit, "orderby": "count", "order": "desc", "hide_empty": True},
    )
    return [{"id": t.get("id"), "name": t.get("name"), "count": t.get("count")} for t in terms]


# ---------- جستجوی هوشمند ساعت (جنسیت/استایل از دسته، مشخصات از ویژگی) ----------
# جنسیت و استایل با «دسته‌بندی» قابل‌اعتمادترند تا ویژگی (دادهٔ ویژگیِ جنسیت ناقص است).
_WATCH_CAT = {
    "مردانه": 29, "زنانه": 28, "بچگانه": 30, "بچه گانه": 30, "کودک": 30, "ست": 90,
    "کلاسیک": 98, "اسپرت": 92, "فشن": 28447, "لاکچری": 89, "غواصی": 96,
    "دیجیتال": 91, "اتوماتیک": 100, "سوئیسی": 21556, "ژاپنی": 21555,
}
# ویژگی‌های فنی: نام پارامتر → (attribute id, taxonomy). آیدی/تاکسونومی از استخراج واقعیِ سایت.
_WATCH_ATTR = {
    "movement": (88, "pa_نوع-موتور"),
    "dial_color": (108, "pa_رنگ-صفحه"),
    "strap_color": (204, "pa_رنگ-بند"),
    "case_color": (203, "pa_رنگ-قاب"),
    "brand": (103, "pa_نام-برند"),   # برندِ واقعی؛ pa_برند فقط «نمونه» است
    "style": (106, "pa_استایل"),
    "strap_material": (115, "pa_طرح-بند"),  # نوعِ واقعیِ بند (چرم/پین‌بند/رابر/…)
}
# هم‌معنی‌های بند → دستهٔ استانداردِ ما (استیل=فلزی، سیلیکون=رابر/پلاستیک، پارچه=برزنت)
_STRAP_MAT = {
    "فلزی": "استیل", "فلز": "استیل", "متال": "استیل", "استیلی": "استیل", "فلزى": "استیل",
    "چرمی": "چرم",
    "لاستیک": "سیلیکون", "لاستیکی": "سیلیکون", "رابر": "سیلیکون", "ژله‌ای": "سیلیکون",
    "پلاستیک": "سیلیکون", "پلاستیکی": "سیلیکون",
    "پارچه": "پارچه", "پارچه‌ای": "پارچه", "نخی": "پارچه", "کتان": "پارچه", "برزنت": "پارچه",
}
# دستهٔ بند → مقادیرِ «طرح بند» که باید شامل‌شان شود (فلزی در طرح‌بند به‌شکلِ پین‌بند/حصیربافت/زنجیری/… است)
_STRAP_DESIGN = {
    "چرم": ["چرم"],
    "استیل": ["پین بند", "حصیربافت", "زنجیری", "دستبندی"],
    "سیلیکون": ["رابر", "سیلیکون", "پلاستیک", "رزین"],
    "پارچه": ["برزنت", "پارچه"],
}
_TERMS_CACHE = {}


async def _cached_terms(attr_id):
    if attr_id not in _TERMS_CACHE:
        _TERMS_CACHE[attr_id] = await attribute_terms(attr_id, limit=100)
    return _TERMS_CACHE[attr_id]


async def _match_term_ids(attr_id, value):
    """آیدیِ مقادیرِ منطبق با یک واژه (اول تطابق دقیق، بعد شامل‌بودن)."""
    value = (value or "").strip()
    if not value:
        return []
    terms = await _cached_terms(attr_id)
    exact = [t["id"] for t in terms if t["name"].strip() == value]
    if exact:
        return exact
    return [t["id"] for t in terms if value in t["name"] or t["name"].strip() in value]


async def _strap_design_ids(material):
    """آیدیِ مقادیرِ «طرح بند» منطبق با دستهٔ بند (چرم/استیل/سیلیکون)."""
    designs = _STRAP_DESIGN.get(material, [material])
    terms = await _cached_terms(115)  # pa_طرح-بند
    return [t["id"] for t in terms if any(d in t["name"] for d in designs)]


def _gender_ok(brief, gender):
    name = brief.get("name", "") or ""
    cats = brief.get("categories", []) or []
    if gender == "مردانه":
        return ("مردانه" in name) or ("ساعت مردانه" in cats)
    if gender == "زنانه":
        return (("زنانه" in name) and ("مردانه" not in name)) or ("ساعت زنانه" in cats)
    if gender in ("بچگانه", "بچه گانه", "کودک"):
        return ("بچ" in name) or ("ساعت بچه گانه" in cats)
    if gender == "ست":
        return ("ست" in name) or ("ساعت ست" in cats)
    return True


def site_search_link(gender=None, style=None, dial_color=None, strap_color=None,
                     brand=None, movement=None, query=None):
    """لینک جست‌وجوی محصول در سایت بر اساس معیارها (برای «خودت ببین»)."""
    import urllib.parse
    words = [w for w in ["ساعت", gender, style, brand, movement, dial_color, strap_color, query] if w]
    q = urllib.parse.urlencode({"post_type": "product", "s": " ".join(words)})
    return f"{config.WOO_URL}/?{q}"


async def search_watches(gender=None, movement=None, dial_color=None, strap_color=None,
                         case_color=None, strap_material=None, brand=None, style=None,
                         min_toman=None, max_toman=None, target_toman=None, query=None,
                         on_sale=False, in_stock_only=True, limit=7, exclude_ids=None):
    """جستجوی ساعت با تشخیص درست جنسیت (دسته+عنوان) و مشخصات فنی (ویژگی).

    یک فیلترِ گزینشی (رنگ/موتور/برند) را مبنای کوئری می‌گذارد، بعد در پایتون
    بر اساس جنسیت/استایل/قیمت پالایش می‌کند تا محدودیت تک‌ویژگیِ ووکامرس دور بخورد.
    target_toman: اگر مشتری یک قیمتِ تکی داد (بدون بازه)، بازه = ۱۰٪ پایین‌تر تا ۱۵٪ بالاتر.
    exclude_ids: محصولاتی که قبلاً نشان داده شده‌اند (برای نتایجِ غیرتکراری).
    """
    # یک عددِ تکی → بازه‌ی هوشمند
    if target_toman and not min_toman and not max_toman:
        min_toman = int(int(target_toman) * 0.90)
        max_toman = int(int(target_toman) * 1.15)
    # نرمال‌سازیِ جنسِ بند (فلزی→استیل، چرمی→چرم، …)
    if strap_material:
        strap_material = _STRAP_MAT.get(strap_material.strip(), strap_material.strip())

    params = {"status": "publish", "orderby": "popularity", "order": "desc", "per_page": 60}
    if in_stock_only:
        params["stock_status"] = "instock"
    if on_sale:
        params["on_sale"] = True
    if min_toman:
        params["min_price"] = str(int(min_toman) * config.MONEY_DIVISOR)
    if max_toman:
        params["max_price"] = str(int(max_toman) * config.MONEY_DIVISOR)

    # یک فیلترِ گزینشی به‌عنوان مبنای کوئری (محدودیت تک‌ویژگیِ ووکامرس)
    primary = None
    for key, val in (("dial_color", dial_color), ("movement", movement), ("brand", brand),
                     ("style", style), ("strap_color", strap_color), ("case_color", case_color),
                     ("strap_material", strap_material)):
        if val:
            attr_id, taxonomy = _WATCH_ATTR[key]
            ids = await _strap_design_ids(val) if key == "strap_material" else await _match_term_ids(attr_id, val)
            if ids:
                params["attribute"] = taxonomy
                params["attribute_term"] = ",".join(str(i) for i in ids)
                primary = key
                break

    if not primary:
        if query:
            params["search"] = query
        elif gender and gender in _WATCH_CAT:
            params["category"] = _WATCH_CAT[gender]

    items = await get("products", params)
    exclude = set(int(i) for i in (exclude_ids or []) if str(i).isdigit())

    rows = []  # جفت (brief, raw) تا به ویژگی‌های خام دسترسی باشد
    for p in items:
        b = _product_brief(p)
        if b.get("id") in exclude:
            continue
        if not b.get("price_toman"):  # محصولِ بدون قیمت را نشان نده
            continue
        if gender and not _gender_ok(b, gender):
            continue
        rows.append((b, p))

    # استایل اگر مبنا نبود، با دسته پالایش شود (اگر ≥۳ ماند)
    if style and primary != "style":
        styled = [r for r in rows if ("ساعت " + style) in (r[0].get("categories") or []) or (style in (r[0].get("name") or ""))]
        if len(styled) >= 3:
            rows = styled

    # فیلتر سخت‌گیرانهٔ رنگ: فقط تک‌رنگِ دقیقاً همان رنگ (نه دورنگ). اگر نتیجه ماند، اعمال کن.
    def _strict(attr_name, want):
        keep = [r for r in rows if attr_options(r[1], attr_name) == [want]]
        return keep
    if dial_color:
        sc = _strict("رنگ صفحه", dial_color)
        if sc:
            rows = sc
    if strap_color:
        sc = _strict("رنگ بند", strap_color)
        if sc:
            rows = sc
    if case_color:
        sc = _strict("رنگ قاب", case_color)
        if sc:
            rows = sc
    # نوعِ بند (چرم/استیل/سیلیکون): اگر مشتری گفت، هرگز نوعِ دیگری نشان نده.
    # «طرح بند» اتریبیوتِ اصلیِ بند است (چرم/پین‌بند/رابر/…)؛ «جنس بکارگرفته» هم به‌عنوانِ پشتیبان.
    if strap_material:
        _designs = _STRAP_DESIGN.get(strap_material, [strap_material])

        def _strap_ok(p):
            td = attr_options(p, "طرح بند")
            if any(any(d in (o or "") for d in _designs) for o in td):
                return True
            return any(strap_material in (o or "") for o in attr_options(p, "جنس بکارگرفته"))

        rows = [r for r in rows if _strap_ok(r[1])]

    # اولویت‌بندیِ نمایش: اول «ارسال فوری» (موجودیِ فروشگاه)، بعد گارانتیِ نامحسوسِ جواهرتایم
    def _jewel(p):
        return any(("جواهرتایم" in g) or ("جواهر تایم" in g) for g in attr_options(p, "گارانتی کننده در ایران"))

    rows.sort(key=lambda r: (0 if r[0].get("shipping_time") == "ارسال فوری" else 1, 0 if _jewel(r[1]) else 1))

    return [b for (b, _) in rows][: max(3, int(limit or 7))]


# ---------- سفارش ----------

def _digits(s):
    return re.sub(r"\D", "", s or "")


async def _order_has_company_stock(product_ids):
    """آیا سفارش شاملِ کالای موجودیِ شرکت (نه ارسالِ فوری) است؟ برای پیامِ صبوریِ ۳ تا ۱۴ روز."""
    ids = [str(p) for p in (product_ids or []) if p]
    if not ids:
        return False
    try:
        prods = await get("products", {"include": ",".join(ids[:10]), "per_page": 10})
    except Exception:  # noqa: BLE001 — اگر نشد، صبورانه فرض نکن
        return False
    for p in prods or []:
        if availability(p).get("shipping") != "ارسال فوری":
            return True
    return False


async def order_status(order_number, phone):
    """وضعیت یک سفارش را با تأیید شماره‌ی تماس برمی‌گرداند (برای جلوگیری از افشای سفارش دیگران)."""
    q = str(order_number).strip()
    try:
        orders = []
        if q.isdigit():  # سریع‌ترین و مطمئن‌ترین راه: گرفتنِ مستقیمِ سفارش با آیدی
            try:
                o = await get(f"orders/{q}")
                if isinstance(o, dict) and o.get("id"):
                    orders = [o]
            except Exception:  # noqa: BLE001 — اگر با آیدی نبود، با جستجو ادامه بده
                orders = []
        if not orders:
            orders = await get("orders", {"search": q, "per_page": 10, "orderby": "date", "order": "desc"})
    except Exception as e:  # noqa: BLE001 — سیستمِ سفارش‌ها در دسترس نبود
        print(f"[woo] order_status در دسترس نبود: {type(e).__name__}")
        return {"found": False, "reason": "unreachable"}
    want_phone = _digits(phone)[-10:]
    for o in orders:
        num = str(o.get("number") or o.get("id"))
        if num != q and str(o.get("id")) != q:
            continue
        b = o.get("billing", {}) or {}
        have_phone = _digits(b.get("phone"))[-10:]
        if want_phone and have_phone and want_phone == have_phone:
            line_items = o.get("line_items") or []
            items = [li.get("name") for li in line_items if li.get("name")]
            pids = [li.get("product_id") for li in line_items if li.get("product_id")]
            return {
                "found": True,
                "number": num,
                "status": o.get("status"),
                "date": o.get("date_created"),
                "total": price_label(o.get("total")),
                "items": items,
                "shipping_method": (o.get("shipping_lines") or [{}])[0].get("method_title", ""),
                "company_stock": await _order_has_company_stock(pids),
            }
        return {"found": False, "reason": "phone_mismatch"}
    return {"found": False, "reason": "not_found"}

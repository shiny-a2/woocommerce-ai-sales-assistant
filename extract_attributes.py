"""استخراج کاملِ جدول ویژگی‌های ووکامرس + همه‌ی مقادیر، به‌عنوان مرجع دقیق.

خروجی: data/attributes_catalog.json (ماشین‌خوان) + چاپ خلاصه‌ی ویژگی‌های کلیدیِ ساعت.
اجرا: python extract_attributes.py
"""
from __future__ import annotations

import asyncio
import json
import os

import woo

_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "attributes_catalog.json")

# نام ویژگی‌های کلیدیِ ساعت برای خلاصه‌ی پایانی
_KEY = ["مناسب برای", "نوع موتور", "نام برند", "برند", "استایل", "رنگ صفحه",
        "رنگ بند", "رنگ قاب", "سایز قاب", "میزان ضدآبی", "نوع شیشه", "جنس بکارگرفته", "گارانتی"]


async def run():
    attrs = await woo.get("products/attributes", {"per_page": 100})
    catalog = []
    for a in attrs:
        slug = a.get("slug") or ""
        taxonomy = slug if slug.startswith("pa_") else ("pa_" + slug)
        item = {"id": a.get("id"), "name": a.get("name"), "taxonomy": taxonomy, "terms": []}
        try:
            terms = await woo.get(
                f"products/attributes/{a.get('id')}/terms",
                {"per_page": 100, "orderby": "count", "order": "desc", "hide_empty": True},
            )
            item["terms"] = [{"id": t.get("id"), "name": t.get("name"), "count": t.get("count")} for t in terms]
        except Exception as e:  # noqa: BLE001
            item["error"] = str(e)
        catalog.append(item)

    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(catalog, open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"ذخیره شد: {_OUT} ({len(catalog)} ویژگی)")
    print("\n=== خلاصه‌ی ویژگی‌های کلیدیِ ساعت ===")
    for it in catalog:
        if it["name"] in _KEY:
            vals = "، ".join(f"{t['name']}({t['id']})" for t in it["terms"][:12])
            print(f"#{it['id']} | {it['name']} | {it['taxonomy']} | {len(it['terms'])} مقدار → {vals}")


if __name__ == "__main__":
    asyncio.run(run())

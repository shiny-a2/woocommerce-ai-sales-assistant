"""تست سریع اتصال‌ها بدون اجرای تلگرام.

اجرا: .venv\\Scripts\\python.exe selftest.py
"""
from __future__ import annotations

import asyncio

import config


async def _run():
    print("=== بررسی تنظیمات ===")
    missing = config.missing_required()
    if missing:
        print("ناقص:", ", ".join(missing))
    else:
        print("همه‌ی متغیرهای ضروری موجودند ✅")
    print("مدل:", config.OPENAI_MODEL, "| فروشگاه:", config.WOO_URL or "—")

    if config.WOO_URL and config.WOO_CK and config.WOO_CS:
        print("\n=== تست ووکامرس ===")
        try:
            import woo
            cats = await woo.list_categories(limit=5)
            print(f"دسته‌بندی‌ها ({len(cats)}):", ", ".join(c["name"] for c in cats[:5]) or "—")
            prods = await woo.search_products(limit=3)
            for p in prods:
                print(f"  • {p['name']} — {p['price_label']} — موجودی: {p['stock_status']}")
        except Exception as e:
            print("خطای ووکامرس:", type(e).__name__, e)

    if config.OPENAI_API_KEY:
        print("\n=== تست دستیار (یک پیام) ===")
        try:
            import assistant
            answer, ctx = await assistant.reply("cli", "tester", "سلام، یه انگشتر طلای دخترانه می‌خوام")
            print("پاسخ دستیار:\n", answer)
            if ctx.get("handoff"):
                print("(ارجاع به اپراتور:", ctx["handoff"], ")")
        except Exception as e:
            print("خطای دستیار:", type(e).__name__, e)

    print("\nتمام.")


if __name__ == "__main__":
    asyncio.run(_run())

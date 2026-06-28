"""نقطه‌ی ورود: ربات تلگرام + وب‌سرور چت سایت در یک حلقه‌ی asyncio.

خودترمیم: اگر اجرای اصلی به هر دلیلی بیفتد، خودش پس از چند ثانیه دوباره بالا می‌آید
و هرگز خارج نمی‌شود. خروجی با تایم‌استمپِ تهران روی data/bot.log نوشته می‌شود.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import time

from telegram.ext import Application

import config
import telegram_bot

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG = os.path.join(_HERE, "data", "bot.log")
_TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))


def _tehran_now():
    return datetime.datetime.now(_TEHRAN)


class _Stamped:
    """هر خط خروجی را با زمانِ تهران برچسب می‌زند."""

    def __init__(self, stream):
        self._s = stream
        self._line_start = True

    def write(self, text):
        if not text:
            return
        ts = _tehran_now().strftime("%m-%d %H:%M:%S")
        out = []
        for piece in text.splitlines(keepends=True):
            if self._line_start:
                out.append(f"[{ts}] ")
            out.append(piece)
            self._line_start = piece.endswith("\n")
        self._s.write("".join(out))

    def flush(self):
        self._s.flush()

    def isatty(self):
        return False


def _setup_logging():
    try:
        os.makedirs(os.path.join(_HERE, "data"), exist_ok=True)
        mode = "a"
        if os.path.exists(_LOG) and os.path.getsize(_LOG) > 2_000_000:
            mode = "w"  # چرخش ساده وقتی لاگ بزرگ شد
        stream = _Stamped(open(_LOG, mode, encoding="utf-8", buffering=1))
        sys.stdout = stream
        sys.stderr = stream
    except Exception:
        pass


async def main():
    missing = config.missing_required()
    if missing:  # به‌جای خروج، تلاش مجدد (شاید .env موقتاً خوانده نشده)
        print("[main] متغیرهای .env ناقص‌اند: " + ", ".join(missing))
        await asyncio.sleep(10)
        return

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_error_handler(_on_error)
    telegram_bot.register_handlers(app)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("[bot] ربات تلگرام فعال شد.")

    tasks = []
    if config.WEB_ENABLED:
        import web_server

        tasks.append(asyncio.create_task(web_server.serve(app)))

    try:
        if tasks:
            await asyncio.gather(*tasks)
        else:
            # فقط تلگرام: تا ابد زنده بمان
            await asyncio.Event().wait()
    finally:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            pass


async def _on_error(update, context):
    print(f"[ptb-error] {context.error!r}")


if __name__ == "__main__":
    _setup_logging()
    os.chdir(_HERE)
    print("[boot] راه‌اندازی دستیار فروش…")
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            break
        except BaseException as e:  # هیچ خطایی نباید پراسس را بکُشد
            print(f"[fatal] {e!r} — ۱۵ ثانیه دیگر تلاش مجدد")
            try:
                time.sleep(15)
            except Exception:
                pass

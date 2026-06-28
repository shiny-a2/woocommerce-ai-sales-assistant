"""اتصال به جی‌پی‌تی و اجرای حلقه‌ی فراخوانی ابزار (function calling)."""
from __future__ import annotations

from openai import AsyncOpenAI

import config
import tools

_client = None


def client():
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


async def _create(messages, with_tools=True):
    kwargs = {
        "model": config.OPENAI_MODEL,
        "messages": messages,
        "temperature": config.OPENAI_TEMPERATURE,
    }
    if with_tools:
        kwargs["tools"] = tools.SCHEMAS
        kwargs["tool_choice"] = "auto"
    return await client().chat.completions.create(**kwargs)


async def chat(messages, ctx):
    """گفتگو با مدل به‌همراه حلقه‌ی ابزار. ورودی فهرست پیام‌ها (شامل system) است.

    خروجی: متن نهایی پاسخ. ctx برای سیگنال‌هایی مثل ارجاع به اپراتور پر می‌شود.
    """
    msgs = list(messages)
    for _ in range(max(1, config.MAX_TOOL_ROUNDS)):
        resp = await _create(msgs, with_tools=True)
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            return (msg.content or "").strip()

        # پیام assistant با درخواست ابزار را عیناً به تاریخچه‌ی موقت اضافه کن
        msgs.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in calls
            ],
        })
        # هر ابزار را اجرا و نتیجه را به‌عنوان نقش tool برگردان
        for c in calls:
            result = await tools.dispatch(c.function.name, c.function.arguments, ctx)
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": result})

    # اگر بعد از سقف دورها هنوز ابزار می‌خواست، یک پاسخ نهاییِ بدون ابزار بگیر
    resp = await _create(msgs, with_tools=False)
    return (resp.choices[0].message.content or "").strip()


async def transcribe(audio_bytes, filename="voice.ogg"):
    """رونویسی پیام صوتی به متن (Whisper)."""
    resp = await client().audio.transcriptions.create(
        model="whisper-1",
        file=(filename, audio_bytes),
    )
    return (getattr(resp, "text", "") or "").strip()

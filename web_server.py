"""بک‌اند چت سایت: FastAPI با اندپوینت /chat و ویجت قابل‌جاسازی.

داخل همان حلقه‌ی asyncioِ تلگرام اجرا می‌شود (serve یک کوروتین است).
"""
from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

import assistant
import botusers
import config

CHANNEL = "web"
_tg_app = None  # برای ارجاع به ادمین از طریق تلگرام (هنگام serve ست می‌شود)

app = FastAPI(title="Javaherian Sales Assistant")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.WEB_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatIn(BaseModel):
    session_id: str
    message: str


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/stats")
async def stats():
    """شمار کاربرانِ ربات (برای داشبورد پیام‌رسانی)."""
    return botusers.counts()


@app.post("/chat")
async def chat(body: ChatIn):
    sid = (body.session_id or "anon").strip()[:64]
    answer, ctx = await assistant.reply(CHANNEL, sid, body.message)
    if ctx.get("handoff"):
        await _notify_admins(sid, body.message, ctx["handoff"])
    return JSONResponse({"reply": answer})


async def _notify_admins(sid, last_text, handoff):
    if not (_tg_app and config.ADMIN_USER_IDS):
        return
    note = (
        "🔔 درخواست اپراتور (چت سایت)\n"
        f"نشست: {sid}\n"
        f"دلیل: {handoff.get('reason', '')}\n"
        f"تماس: {handoff.get('contact') or '—'}\n"
        f"آخرین پیام: {last_text}"
    )
    for admin_id in config.ADMIN_USER_IDS:
        try:
            await _tg_app.bot.send_message(chat_id=admin_id, text=note)
        except Exception as e:  # noqa: BLE001
            print(f"[web] ارسال هشدار به ادمین {admin_id} ناموفق: {e}")


# ---------- اتصال CRM (نقش sale-brain-v2) ----------
# افزونه‌ی CRM این‌ها را صدا می‌زند: GET /api/client/me و POST /api/chat با هدر X-SB-Token.
class BrainChatIn(BaseModel):
    messages: list = []
    user_prompt: str = ""
    catalog: Any = None
    temperature: float | None = None
    max_tokens: int | None = None


def _check_sb_token(token):
    if not config.SALE_BRAIN_TOKEN:
        raise HTTPException(status_code=503, detail="sale-brain token not configured")
    if token != config.SALE_BRAIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


@app.get("/api/client/me")
async def brain_me(x_sb_token: str = Header(None, alias="X-SB-Token")):
    _check_sb_token(x_sb_token)
    # سهمیه‌ی نامحدود (خودمیزبان)؛ CRM فقط نمایش می‌دهد
    return {"ok": True, "client": {"name": "store-sale-brain", "quota_used": 0, "quota_limit": 0}}


@app.post("/api/chat")
async def brain_chat(body: BrainChatIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    _check_sb_token(x_sb_token)
    import time as _t
    _start = _t.monotonic()
    print(f"[brain] /api/chat دریافت شد ({len(body.messages or [])} پیام)")
    text, ctx = await assistant.answer_messages(body.messages, body.user_prompt)
    print(f"[brain] پاسخ آماده در {_t.monotonic() - _start:.1f} ثانیه (طول متن={len(text)})")
    handoff = ctx.get("handoff")
    return {
        "text": text,
        "handoff": bool(handoff),
        "handoff_reason": (handoff or {}).get("reason", "") if handoff else "",
        "name_update": ctx.get("name_update") or None,
        "quota_used": 0,
        "quota_limit": 0,
    }


@app.get("/", response_class=HTMLResponse)
async def demo_page():
    return _DEMO_HTML


@app.get("/embed.js")
async def embed_js():
    return Response(content=_EMBED_JS, media_type="application/javascript")


async def serve(tg_app=None):
    global _tg_app
    _tg_app = tg_app
    # log_config=None تا uvicorn لاگینگ را روی stdout بازپیکربندی نکند (با لاگ تهرانِ main تداخل دارد)
    cfg = uvicorn.Config(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning", log_config=None)
    server = uvicorn.Server(cfg)
    print(f"[web] چت سایت روی http://{config.WEB_HOST}:{config.WEB_PORT} فعال شد.")
    await server.serve()


# ---------- ویجت قابل‌جاسازی ----------
# در سایت فقط این خط را قبل از </body> بگذار (آدرس را با دامنه‌ی عمومی عوض کن):
#   <script src="https://CHAT.DOMAIN/embed.js" defer></script>
_EMBED_JS = r"""
(function () {
  var base = (function () {
    var s = document.currentScript;
    if (!s) { var a = document.getElementsByTagName('script'); s = a[a.length - 1]; }
    try { return new URL(s.src).origin; } catch (e) { return ''; }
  })();

  var sid = localStorage.getItem('jg_chat_sid');
  if (!sid) { sid = 'w' + Date.now() + Math.floor(Math.random() * 1e6); localStorage.setItem('jg_chat_sid', sid); }

  var css = ''
    + '#jg-btn{position:fixed;bottom:20px;left:20px;z-index:999999;width:60px;height:60px;border-radius:50%;'
    + 'background:#caa15a;color:#fff;border:none;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);font-size:26px}'
    + '#jg-box{position:fixed;bottom:90px;left:20px;z-index:999999;width:340px;max-width:92vw;height:480px;max-height:75vh;'
    + 'background:#fff;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.3);display:none;flex-direction:column;'
    + 'overflow:hidden;font-family:Tahoma,sans-serif;direction:rtl}'
    + '#jg-hd{background:#1a1a1a;color:#caa15a;padding:12px 14px;font-weight:bold}'
    + '#jg-msgs{flex:1;overflow-y:auto;padding:12px;background:#f7f7f7}'
    + '.jg-m{margin:6px 0;padding:8px 11px;border-radius:12px;max-width:82%;white-space:pre-wrap;line-height:1.7;font-size:14px}'
    + '.jg-u{background:#caa15a;color:#fff;margin-left:auto}'
    + '.jg-a{background:#fff;border:1px solid #eee;color:#222;margin-right:auto}'
    + '.jg-a a{color:#9a7b2e}'
    + '#jg-in{display:flex;border-top:1px solid #eee}'
    + '#jg-tx{flex:1;border:none;padding:12px;font-family:inherit;font-size:14px;outline:none}'
    + '#jg-snd{border:none;background:#caa15a;color:#fff;padding:0 16px;cursor:pointer;font-size:15px}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  var btn = document.createElement('button'); btn.id = 'jg-btn'; btn.innerHTML = '💬';
  var box = document.createElement('div'); box.id = 'jg-box';
  box.innerHTML = '<div id="jg-hd">مشاور فروشگاهِ نمونه</div>'
    + '<div id="jg-msgs"></div>'
    + '<div id="jg-in"><input id="jg-tx" placeholder="پیام شما..." autocomplete="off"><button id="jg-snd">ارسال</button></div>';
  document.body.appendChild(btn); document.body.appendChild(box);

  var msgs = box.querySelector('#jg-msgs');
  var tx = box.querySelector('#jg-tx');
  var greeted = false;

  function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
  function linkify(s){return esc(s).replace(/(https?:\/\/[^\s]+)/g,'<a href="$1" target="_blank">$1</a>');}
  function add(text, who){
    var d=document.createElement('div'); d.className='jg-m '+(who==='u'?'jg-u':'jg-a');
    d.innerHTML = who==='u'?esc(text):linkify(text);
    msgs.appendChild(d); msgs.scrollTop=msgs.scrollHeight; return d;
  }

  function toggle(){
    var open = box.style.display==='flex';
    box.style.display = open?'none':'flex';
    if(!open && !greeted){greeted=true; add('سلام 🌟 چی دنبالشید؟ کمکتون می‌کنم.', 'a'); tx.focus();}
  }
  btn.onclick = toggle;

  function send(){
    var t = tx.value.trim(); if(!t) return;
    tx.value=''; add(t,'u');
    var typing = add('در حال نوشتن…','a');
    fetch(base + '/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sid, message: t})
    }).then(function(r){return r.json();})
      .then(function(j){ typing.innerHTML = linkify(j.reply || '...'); msgs.scrollTop=msgs.scrollHeight; })
      .catch(function(){ typing.textContent = 'خطا در ارتباط. دوباره تلاش کنید.'; });
  }
  box.querySelector('#jg-snd').onclick = send;
  tx.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); send(); }});
})();
"""

_DEMO_HTML = """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>آزمایش دستیار فروش</title>
<style>body{font-family:Tahoma,sans-serif;background:#1a1a1a;color:#eee;text-align:center;padding-top:80px}
h1{color:#caa15a}</style></head>
<body>
<h1>دستیار فروش فروشگاهِ نمونه</h1>
<p>روی دکمه‌ی گفتگو پایین صفحه بزن و تست کن.</p>
<script src="/embed.js" defer></script>
</body></html>
"""

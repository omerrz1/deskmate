"""HTTP API for the Chats page: conversations, replies, approvals, WhatsApp calls and the settings that wire it all up."""
import asyncio
import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import agents, auth, chats, db, integrations, voices, wa_call

router = APIRouter(prefix="/api")


def _who(request):
    return (getattr(request.state, "session", None) or {}).get("username", "Admin")


def _chat_or_404(cid):
    c = chats._chat(cid)
    if not c:
        raise HTTPException(404, "That chat was not found.")
    return c


# ---------- lists (fixed paths first) ----------
@router.get("/chats")
def chats_list(status: str = "open", q: str = ""):
    return {"chats": chats.list_chats(status, q), "counts": chats.counts()}


@router.get("/chats/pending")
def chats_pending():
    return [{"id": m["id"], "chat_id": m["chat_id"], "who": m["chat_name"] or f"+{m['ext_id']}", "channel": m["channel"], "kind": m["kind"], "text": m["body"], "by": m["by_name"], "created_at": m["created_at"]}
            for m in chats.pending()]


@router.get("/chats/calls/live")
def calls_live():
    return wa_call.live_calls()


@router.post("/chats/calls/{call_id}/end")
async def calls_end(call_id: int):
    if not wa_call.end_now(call_id):
        raise HTTPException(404, "That call is not running.")
    return {"ok": True}


@router.post("/chats/messages/{mid}/approve")
async def message_approve(mid: int, request: Request):
    ok, err = await chats.approve(mid)
    if not ok and err == "Nothing is waiting with that id.":
        raise HTTPException(404, err)
    auth.sec_log(f"Chat message {mid} approved by '{_who(request)}'", request.state.ip, request.state.ua)
    return {"sent": ok, "error": err}


@router.post("/chats/messages/{mid}/reject")
def message_reject(mid: int):
    chats.reject(mid)
    return {"ok": True}


@router.get("/chats/messages/{mid}/file")
def message_file(mid: int):
    m = db.q1("SELECT * FROM chat_messages WHERE id=?", (mid,))
    media = db.jload(m["media"], {}) if m else {}
    if not media or not media.get("file"):
        raise HTTPException(404, "No file")
    p = chats.media_dir(m["chat_id"]) / os.path.basename(media["file"])
    if not p.exists():
        raise HTTPException(404, "No file")
    mime = media.get("mime") or "application/octet-stream"
    safe = mime if re.fullmatch(r"(image|audio|video)/[a-z0-9.+-]+|application/pdf", mime) else "application/octet-stream"
    return FileResponse(p, media_type=safe, headers={"Content-Disposition": "inline" if safe != "application/octet-stream" else "attachment", "X-Content-Type-Options": "nosniff"})


class NewChat(BaseModel):
    phone: str
    template: str
    language: str = "en_US"
    params: list = []
    name: str = ""


@router.post("/chats/new")
async def chat_new(b: NewChat, request: Request):
    """Start a WhatsApp conversation with a number: WhatsApp only lets a business open one with an approved template."""
    num = re.sub(r"\D", "", b.phone)
    if len(num) < 7:
        raise HTTPException(400, "Enter the number with its country code, like 447700900123.")
    provider = "twilio" if chats._inst("twilio") else "whatsapp_cloud"
    if not chats._inst(provider):
        raise HTTPException(400, "Connect Twilio in Apps first.")
    chat = chats.get_or_create("whatsapp", provider, num, b.name)
    r = await chats.send(chat["id"], b.template, admin=True, by=_who(request), template={"name": b.template, "language": b.language, "params": [str(x) for x in b.params][:20]})
    if r["state"] != "sent":
        raise HTTPException(400, r["message"])
    return {"chat_id": chat["id"]}


# ---------- one chat ----------
@router.get("/chats/{cid}")
def chat_detail(cid: int):
    d = chats.detail(cid)
    if not d:
        raise HTTPException(404, "That chat was not found.")
    st, until = wa_call.perm_state(chats._chat(cid))
    d["call"] = {"possible": d["channel"] == "whatsapp" and d["provider"] in ("twilio", "whatsapp_cloud") and wa_call.available(), "permission": st, "permission_until": until if 0 < until < 253402300799 else None,
                 "installed": wa_call.available()}
    return d


@router.post("/chats/{cid}/read")
def chat_read(cid: int):
    _chat_or_404(cid)
    chats.mark_read(cid)
    return {"ok": True}


class SendIn(BaseModel):
    text: str = ""
    template: Optional[dict] = None


@router.post("/chats/{cid}/send")
async def chat_send(cid: int, b: SendIn, request: Request):
    _chat_or_404(cid)
    t = b.template if (b.template and b.template.get("name")) else None
    r = await chats.send(cid, b.text, admin=True, by=_who(request), template=({"name": t["name"], "language": t.get("language", "en_US"), "params": t.get("params") or []} if t else None))
    if r["state"] in ("failed", "no_provider", "window_closed"):
        raise HTTPException(400, r["message"])
    chats.mark_read(cid)
    return r


class AssignIn(BaseModel):
    agent_id: int


@router.post("/chats/{cid}/assign")
def chat_assign(cid: int, b: AssignIn):
    _chat_or_404(cid)
    if not agents.get(b.agent_id):
        raise HTTPException(400, "That employee was not found.")
    db.update("chats", cid, agent_id=b.agent_id)
    return {"ok": True}


class StatusIn(BaseModel):
    status: str


@router.post("/chats/{cid}/status")
def chat_status(cid: int, b: StatusIn):
    _chat_or_404(cid)
    if b.status not in ("open", "closed"):
        raise HTTPException(400, "Status must be open or closed.")
    db.update("chats", cid, status=b.status, **({"unread": 0} if b.status == "closed" else {}))
    return {"ok": True}


class AutoIn(BaseModel):
    off: bool


@router.post("/chats/{cid}/auto")
def chat_auto(cid: int, b: AutoIn):
    _chat_or_404(cid)
    db.update("chats", cid, auto_off=1 if b.off else 0)
    return {"ok": True}


@router.post("/chats/{cid}/ai-reply")
def chat_ai_reply(cid: int):
    """Ask the employee in charge to answer the customer's latest message now."""
    _chat_or_404(cid)
    db.update("chats", cid, auto_off=0, status="open")
    chats.kick(cid, 0.1)
    return {"ok": True}


class AskIn(BaseModel):
    text: str = ""


@router.post("/chats/{cid}/call-permission")
async def chat_call_permission(cid: int, b: AskIn):
    _chat_or_404(cid)
    r = await wa_call.ask_permission(cid, b.text, admin=True)
    if "error" in r:
        raise HTTPException(400, r["error"])
    return r


class CallIn(BaseModel):
    objective: str = ""
    agent_id: Optional[int] = None


@router.post("/chats/{cid}/call")
async def chat_call(cid: int, b: CallIn, request: Request):
    chat = _chat_or_404(cid)
    r = await wa_call.start(cid, b.agent_id or chat["agent_id"], b.objective.strip() or "Have a short conversation about their message.", by=_who(request))
    if "error" in r:
        raise HTTPException(400, r["error"])
    return r


# ---------- settings ----------
@router.get("/chats-settings")
def settings_get():
    ws, sec = db.current(), chats.secret()
    base = f"/api/hooks/chat/{ws}/{sec}"
    return {
        "reply_mode": chats.reply_mode(), "messaging_policy": chats.send_policy(), "call_policy": chats.call_policy(), "inbound_calls": chats.setting("wa_inbound_calls", "off"),
        "default_agent": chats.default_agent_id(), "connected": chats.connected(),
        "webhooks": {"meta": f"{base}/meta", "twilio": f"{base}/twilio", "twilio_voice": f"{base}/twilio/voice", "telegram": f"{base}/telegram"}, "verify_token": sec,
        "app_secret_set": {p: bool(chats.cfg(p).get("app_secret")) for p in ("whatsapp_cloud", "instagram_business", "facebook_page")},
        "public_base_url": os.getenv("PUBLIC_BASE_URL", ""), "calling": wa_call.status(),
    }


class SettingsIn(BaseModel):
    reply_mode: Optional[str] = None
    messaging_policy: Optional[str] = None
    call_policy: Optional[str] = None
    inbound_calls: Optional[str] = None
    default_agent: Optional[int] = None


@router.put("/chats-settings")
def settings_put(b: SettingsIn):
    if b.reply_mode is not None:
        if b.reply_mode not in ("agent", "off"):
            raise HTTPException(400, "Replies must be 'agent' or 'off'.")
        voices.set_setting("chat_reply_mode", b.reply_mode)
    if b.messaging_policy is not None:
        if b.messaging_policy not in ("approve", "auto"):
            raise HTTPException(400, "Messages must be 'approve' or 'auto'.")
        voices.set_setting("messaging_policy", b.messaging_policy)
    if b.call_policy is not None:
        if b.call_policy not in ("approve", "auto"):
            raise HTTPException(400, "Calls must be 'approve' or 'auto'.")
        voices.set_setting("wa_call_policy", b.call_policy)
    if b.inbound_calls is not None:
        if b.inbound_calls not in ("off", "answer"):
            raise HTTPException(400, "Incoming calls must be 'off' or 'answer'.")
        voices.set_setting("wa_inbound_calls", b.inbound_calls)
    if b.default_agent is not None:
        if not agents.get(b.default_agent):
            raise HTTPException(400, "That employee was not found.")
        voices.set_setting("chat_agent", str(b.default_agent))
    return settings_get()


@router.post("/chats-settings/rotate")
def settings_rotate(request: Request):
    chats.rotate_secret()
    auth.sec_log(f"The chat webhook secret was changed by '{_who(request)}'", request.state.ip, request.state.ua)
    return settings_get()


class TelegramIn(BaseModel):
    base_url: str


@router.post("/chats-settings/telegram")
async def telegram_connect(b: TelegramIn):
    """Tell Telegram where to send incoming messages."""
    base = b.base_url.strip().rstrip("/")
    if not base.startswith("https://"):
        raise HTTPException(400, "Telegram needs a public https address for this server.")
    token = chats.cfg("telegram").get("token")
    if not token:
        raise HTTPException(400, "Connect Telegram in Apps first.")
    url = f"{base}/api/hooks/chat/{db.current()}/{chats.secret()}/telegram"
    async with httpx.AsyncClient(timeout=15) as h:
        r = await h.post(f"https://api.telegram.org/bot{token}/setWebhook", json={"url": url, "secret_token": chats.secret(), "allowed_updates": ["message"]})
    d = r.json() if r.status_code < 500 else {}
    if not d.get("ok"):
        raise HTTPException(400, f"Telegram said: {str(d.get('description') or r.text)[:160]}")
    return {"ok": True}


@router.get("/chats-settings/calling")
async def calling_state():
    from . import twilio_setup
    try:
        async with asyncio.timeout(45):
            return await twilio_setup.inspect()
    except TimeoutError:
        raise HTTPException(504, "Twilio connection check timed out. Try again shortly.")


@router.post("/chats-settings/calling/enable")
async def calling_enable(request: Request):
    from . import twilio_setup
    session = request.state.session
    if db.current() != db.company() or session.get('company_role', session.get('role')) not in ('owner', 'admin'):
        raise HTTPException(403, "A company admin must configure Twilio from the company workspace.")
    try:
        async with asyncio.timeout(60):
            return await twilio_setup.configure()
    except wa_call.CallError as e:
        raise HTTPException(400, str(e))
    except integrations.AppError as e:
        raise HTTPException(400, str(e))
    except TimeoutError:
        raise HTTPException(504, "Twilio setup timed out. Some steps may have completed. Check the connection and Twilio Console before repeating setup.")

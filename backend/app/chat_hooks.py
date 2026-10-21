"""Public addresses that WhatsApp, Instagram, Messenger, Twilio and Telegram call when a customer writes (or calls on WhatsApp).
Each company has its own secret address (Settings, Chats). Nothing here uses a sign-in: the secret in the address proves who is calling, and where the provider signs
its messages (Meta with the app secret, Twilio with the auth token) the signature is checked as well.
The server middleware has already switched to the company named in the address before any of this runs."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from . import chats, db, wa_call

router = APIRouter(prefix="/api/hooks/chat")
MAX_BODY = 2_000_000


def _gate(secret):
    if not chats.check_secret(secret):
        raise HTTPException(404, "Not found")


async def _body(request):
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "Too large")
    return raw


def _bg(coro):
    """Slow work (downloading a photo, turning a voice note into text) must not hold up the provider's request."""
    asyncio.get_running_loop().create_task(coro)


# ---------- Meta: WhatsApp, Instagram and Messenger share one kind of webhook ----------
@router.get("/{ws}/{secret}/meta")
async def meta_verify(ws: str, secret: str, request: Request):
    """Meta asks once, when you save the webhook: answer with its challenge if the verify token is ours."""
    _gate(secret)
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and chats.check_secret(q.get("hub.verify_token", "")):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "Verify token does not match")


def _app_secret(obj):
    prov = {"whatsapp_business_account": "whatsapp_cloud", "instagram": "instagram_business", "page": "facebook_page"}.get(obj)
    return chats.cfg(prov).get("app_secret", "") if prov else ""


def signature_ok(raw, header, app_secret):
    want = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header or "")


@router.post("/{ws}/{secret}/meta")
async def meta_events(ws: str, secret: str, request: Request):
    _gate(secret)
    raw = await _body(request)
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "Not JSON")
    obj = payload.get("object", "")
    app_secret = _app_secret(obj)
    if app_secret and not signature_ok(raw, request.headers.get("x-hub-signature-256", ""), app_secret):
        raise HTTPException(403, "Bad signature")
    for entry in payload.get("entry") or []:
        try:
            if obj == "whatsapp_business_account":
                for ch in entry.get("changes") or []:
                    await _whatsapp(ch.get("field", ""), ch.get("value") or {})
            elif obj in ("instagram", "page"):
                for ev in entry.get("messaging") or []:
                    _messaging(obj, entry.get("id", ""), ev)
        except Exception as e:  # one bad event must not make Meta retry the whole batch forever
            db.log("error", f"A {obj} webhook event could not be handled: {type(e).__name__}")
    return {"ok": True}


async def _whatsapp(field, value):
    want = chats.cfg("whatsapp_cloud").get("phone_number_id")
    got = (value.get("metadata") or {}).get("phone_number_id")
    if want and got and str(got) != str(want):
        return  # another number on the same Meta app
    names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
    for m in value.get("messages") or []:
        _wa_message(m, names.get(m.get("from"), ""))
    for st in value.get("statuses") or []:
        if st.get("type") == "call":
            await wa_call.on_event({"kind": "status", **st})
            continue
        err = "; ".join(f"{e.get('title') or e.get('message', '')} ({e.get('code', '')})" for e in st.get("errors") or [])
        chats.set_status(st.get("id"), st.get("status", ""), err)
    for c in value.get("calls") or []:
        await wa_call.on_event({"kind": "call", **c})


def _wa_message(m, name):
    wa_id, typ, mid = str(m.get("from", "")), m.get("type", "text"), m.get("id", "")
    if not wa_id:
        return
    P = "whatsapp_cloud"
    if typ == "text":
        chats.ingest("whatsapp", P, wa_id, (m.get("text") or {}).get("body", ""), name=name, msg_id=mid)
    elif typ in ("image", "video", "document", "sticker", "audio"):
        body_obj = m.get(typ) or {}
        kind = "voice" if (typ == "audio" and body_obj.get("voice")) else typ
        placeholder = body_obj.get("caption", "") or ("(voice message…)" if kind == "voice" else "")
        chat, row = chats.ingest("whatsapp", P, wa_id, placeholder, name=name, msg_id=mid, kind=kind,
                                 media={"mime": body_obj.get("mime_type", ""), "filename": body_obj.get("filename", "")}, quiet=True)
        if row and body_obj.get("id"):
            _bg(chats.download_media(row, {"whatsapp_id": body_obj["id"]}))
    elif typ == "location":
        loc = m.get("location") or {}
        text = "Shared a location: " + ", ".join(x for x in (loc.get("name"), loc.get("address"), f"({loc.get('latitude')}, {loc.get('longitude')})") if x)
        chats.ingest("whatsapp", P, wa_id, text, name=name, msg_id=mid, kind="location")
    elif typ == "contacts":
        chats.ingest("whatsapp", P, wa_id, "Shared a contact card.", name=name, msg_id=mid, kind="text")
    elif typ == "reaction":
        r = m.get("reaction") or {}
        chats.ingest("whatsapp", P, wa_id, r.get("emoji", ""), name=name, msg_id=mid, kind="reaction", quiet=True)
    elif typ == "button":
        chats.ingest("whatsapp", P, wa_id, (m.get("button") or {}).get("text", ""), name=name, msg_id=mid)
    elif typ == "interactive":
        it = m.get("interactive") or {}
        kind = it.get("type", "")
        if kind == "call_permission_reply":
            _permission_reply(wa_id, name, mid, it.get("call_permission_reply") or {})
        elif kind == "button_reply":
            chats.ingest("whatsapp", P, wa_id, (it.get("button_reply") or {}).get("title", ""), name=name, msg_id=mid)
        elif kind == "list_reply":
            r = it.get("list_reply") or {}
            chats.ingest("whatsapp", P, wa_id, r.get("title", "") + (f" ({r['description']})" if r.get("description") else ""), name=name, msg_id=mid)
        else:
            chats.ingest("whatsapp", P, wa_id, "Replied to a form.", name=name, msg_id=mid)


def _permission_reply(wa_id, name, mid, r):
    chat, row = chats.ingest("whatsapp", "whatsapp_cloud", wa_id, "", name=name, msg_id=mid, kind="call_permission_reply", quiet=True)
    if row is None:
        return
    granted = (r.get("response") or "").lower() in ("accept", "accepted", "granted")
    until = float(r.get("expiration_timestamp") or 0) or (time.time() + 365 * 86400 if r.get("is_permanent") else time.time() + 3 * 86400)
    db.update("chats", chat["id"], call_perm="granted" if granted else "denied", call_perm_until=until if granted else 0)
    text = (f"Allowed WhatsApp calls until {time.strftime('%d %b %H:%M', time.localtime(until))}." if granted else "Did not allow WhatsApp calls.")
    db.update("chat_messages", row, body=text)
    db.log("chat", f"{chats.title(chat)} {'allowed' if granted else 'declined'} WhatsApp calls", chat_id=chat["id"])
    if granted:
        chats.kick(chat["id"], 1.0)  # the employee who asked can now call


# ---------- Instagram and Messenger direct messages ----------
def _messaging(obj, our_id, ev):
    msg = ev.get("message") or {}
    if not msg:
        return
    channel, prov = ("instagram", "instagram_business") if obj == "instagram" else ("messenger", "facebook_page")
    sender, recipient = str((ev.get("sender") or {}).get("id", "")), str((ev.get("recipient") or {}).get("id", ""))
    mid = msg.get("mid", "")
    if msg.get("is_echo") or sender == str(our_id or ""):
        # a message our side sent from the Page or Instagram inbox itself: keep it in the thread
        if recipient:
            chat = chats.get_or_create(channel, prov, recipient)
            chats.add_message(chat["id"], "out", msg.get("text", "") or "(attachment)", "text", None, mid, "sent", None, "Inbox app")
        return
    if not sender:
        return
    text = msg.get("text", "") or ""
    atts = msg.get("attachments") or []
    if atts and not text:
        a0 = atts[0]
        kind = {"image": "image", "audio": "voice", "video": "video", "file": "document", "location": "location"}.get(a0.get("type"), "document")
        url = (a0.get("payload") or {}).get("url", "")
        chat, row = chats.ingest(channel, prov, sender, "(voice message…)" if kind == "voice" else "", msg_id=mid, kind=kind, media={"mime": ""}, quiet=True)
        if row and url and kind != "location":
            _bg(chats.download_media(row, {"url": url}))
        elif row:
            chats.kick(chat["id"], 1.0)
        return
    chats.ingest(channel, prov, sender, text, msg_id=mid)


# ---------- Twilio: texts and WhatsApp through a Twilio number ----------
def _twilio_signature_ok(request, form, token):
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if not base or not token:
        return False  # Twilio callbacks require the public URL and a valid signature
    url = base + request.url.path + (("?" + request.url.query) if request.url.query else "")
    data = url + "".join(k + form[k] for k in sorted(form))
    want = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(want, request.headers.get("x-twilio-signature", ""))


@router.post("/{ws}/{secret}/twilio")
async def twilio_in(ws: str, secret: str, request: Request):
    _gate(secret)
    raw = await _body(request)
    form = dict(parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True))
    c = chats.cfg("twilio")
    if form.get("AccountSid") != c.get("account_sid") or not _twilio_signature_ok(request, form, c.get("auth_token", "")):
        raise HTTPException(403, "Bad signature")
    from . import twilio_call
    if form.get("Body") == "VOICE_CALL_REQUEST":
        if not os.getenv("PUBLIC_BASE_URL") or form.get("AccountSid") != c.get("account_sid") or form.get("To") != "whatsapp:" + c.get("whatsapp_from", ""):
            raise HTTPException(403, "Signed permission webhook requires PUBLIC_BASE_URL and matching account")
        if twilio_call.permission_reply(form):
            return Response("<Response/>", media_type="text/xml")
    frm = form.get("From", "")
    channel = "whatsapp" if frm.startswith("whatsapp:") else "sms"
    num = chats._digits(frm)
    if num:
        n = int(form.get("NumMedia") or 0)
        text = form.get("Body", "")
        if n:
            ctype = form.get("MediaContentType0", "")
            kind = "voice" if ctype.startswith("audio") else "image" if ctype.startswith("image") else "video" if ctype.startswith("video") else "document"
            chat, row = chats.ingest(channel, "twilio", num, text or ("(voice message…)" if kind == "voice" else ""), name=form.get("ProfileName", ""), msg_id=form.get("MessageSid", ""),
                                     kind=kind, media={"mime": ctype}, quiet=True)
            if row and form.get("MediaUrl0"):
                _bg(chats.download_media(row, {"url": form["MediaUrl0"], "basic": (c.get("account_sid", ""), c.get("auth_token", ""))}))
        else:
            chats.ingest(channel, "twilio", num, text, name=form.get("ProfileName", ""), msg_id=form.get("MessageSid", ""))
    return Response("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response/>", media_type="text/xml")


# ---------- Telegram ----------
@router.post("/{ws}/{secret}/telegram")
async def telegram_in(ws: str, secret: str, request: Request):
    _gate(secret)
    if not chats.check_secret(request.headers.get("x-telegram-bot-api-secret-token", "")):
        raise HTTPException(403, "Bad secret")
    try:
        up = json.loads(await _body(request) or b"{}")
    except Exception:
        raise HTTPException(400, "Not JSON")
    m = up.get("message")
    if not m:
        return {"ok": True}
    frm, chat_id = m.get("from") or {}, (m.get("chat") or {}).get("id")
    if chat_id is None or frm.get("is_bot"):
        return {"ok": True}
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
    handle = ("@" + frm["username"]) if frm.get("username") else ""
    mid = f"{chat_id}:{m.get('message_id')}"
    text = m.get("text") or m.get("caption") or ""
    file_id, kind = None, "text"
    if m.get("voice") or m.get("audio"):
        file_id, kind = (m.get("voice") or m.get("audio")).get("file_id"), "voice"
    elif m.get("photo"):
        file_id, kind = m["photo"][-1].get("file_id"), "image"
    elif m.get("document"):
        file_id, kind = m["document"].get("file_id"), "document"
    elif m.get("location"):
        loc = m["location"]
        text, kind = f"Shared a location ({loc.get('latitude')}, {loc.get('longitude')}).", "location"
    if file_id:
        chat, row = chats.ingest("telegram", "telegram", chat_id, text or ("(voice message…)" if kind == "voice" else ""), name=name, handle=handle, msg_id=mid, kind=kind, media={"mime": ""}, quiet=True)
        if row:
            _bg(chats.download_media(row, {"telegram_file_id": file_id}))
    else:
        chats.ingest("telegram", "telegram", chat_id, text, name=name, handle=handle, msg_id=mid, kind=kind)
    return {"ok": True}


from . import twilio_call
router.include_router(twilio_call.router)

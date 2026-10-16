"""Texts and WhatsApp messages to customers. Sent through a connected messaging app (Twilio or WhatsApp Business).
By default each message waits for the admin's OK, like outside emails; the admin can switch that to automatic."""
import re

from . import db, integrations, voices

db.run("CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT, to_addr TEXT, body TEXT, agent_id INTEGER, contact_id INTEGER, call_id INTEGER, "
       "status TEXT DEFAULT 'pending_approval', error TEXT DEFAULT '', provider TEXT DEFAULT '', created_at REAL, sent_at REAL)")

_S = {"type": "string"}
TOOLS = [
    {"name": "send_sms", "description": "Send a text message to a customer or contact. It may wait for the admin's approval. Never say it has been sent unless the result says so.",
     "parameters": {"type": "object", "properties": {"to": {**_S, "description": "phone number in international format, e.g. +447700900123"}, "body": _S}, "required": ["to", "body"]}},
    {"name": "send_whatsapp", "description": "Send a WhatsApp message to a customer. It may wait for the admin's approval.",
     "parameters": {"type": "object", "properties": {"to": {**_S, "description": "phone number in international format"}, "body": _S}, "required": ["to", "body"]}},
]
NAMES = {t["name"] for t in TOOLS}
MANAGER_TOOLS = [
    {"name": "list_pending_messages", "description": "Manager: texts and WhatsApp messages waiting for approval.", "parameters": {"type": "object", "properties": {}}},
    {"name": "approve_message", "description": "Manager: approve and send a waiting message (not your own).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "reject_message", "description": "Manager: reject a waiting message.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
]
MANAGER_NAMES = {t["name"] for t in MANAGER_TOOLS}


def policy():
    return voices.get_setting("messaging_policy", "approve")


_TZ_CODES = {"Europe/London": "44", "Europe/Dublin": "353", "Asia/Kolkata": "91", "Asia/Karachi": "92", "Asia/Dhaka": "880", "Asia/Dubai": "971", "Asia/Riyadh": "966",
             "Australia/Sydney": "61", "Australia/Melbourne": "61", "Pacific/Auckland": "64", "Africa/Lagos": "234", "Africa/Johannesburg": "27", "Europe/Paris": "33", "Europe/Berlin": "49"}


def default_code():
    """Calling code used for numbers written the local way (07700 900123): Settings → Company time zone decides, unless set explicitly."""
    tz = voices.get_setting("timezone", "")
    return voices.get_setting("default_calling_code", "") or _TZ_CODES.get(tz) or ("1" if tz.startswith("America/") else "")


def normalise(num):
    n = re.sub(r"[^\d+]", "", str(num or ""))
    if n.startswith("00"):
        n = "+" + n[2:]
    if n.startswith("0") and default_code():
        n = "+" + default_code() + n[1:]
    return n if re.fullmatch(r"\+[1-9]\d{6,14}", n) else ""


def provider(channel):
    """-> (app key, action) of a connected app that can carry this channel, or None."""
    tw = integrations.get_by_key("twilio")
    if tw and tw["enabled"]:
        if channel == "sms":
            return "twilio", "send_sms"
        if channel == "whatsapp" and integrations._config(tw).get("whatsapp_from"):
            return "twilio", "send_whatsapp"
    wa = integrations.get_by_key("whatsapp_cloud")
    if channel == "whatsapp" and wa and wa["enabled"]:
        return "whatsapp_cloud", "send_text"
    return None


async def _dispatch(row):
    prov = provider(row["channel"])
    if not prov:
        db.update("outbox", row["id"], status="no_provider", error="No messaging app is connected for this channel.")
        return False
    key, action = prov
    inputs = {"to": row["to_addr"].lstrip("+") if key == "whatsapp_cloud" else row["to_addr"]}
    inputs["text" if action == "send_text" else "body"] = row["body"]
    res = await integrations.call(key, action, inputs)
    ok = "error" not in res
    db.update("outbox", row["id"], status="sent" if ok else "failed", error="" if ok else str(res["error"])[:300], provider=key, sent_at=db.now() if ok else None)
    db.log("agent", f"{row['channel'].upper()} {'sent' if ok else 'FAILED'} to {row['to_addr']}")
    if ok:
        try:  # keep it in the person's chat, so their reply continues the same conversation
            from . import agents, chats
            chats.record_outbound(key, row["channel"], row["to_addr"], row["body"], agents.get(row["agent_id"]) if row["agent_id"] else None)
        except Exception:
            pass
    return ok


async def send(agent, channel, to, body, contact_id=None, call_id=None):
    """-> dict(state=sent|waiting_for_approval|no_provider|failed, message=...). Never raises."""
    num = normalise(to)
    if not num:
        return {"state": "failed", "message": "That phone number isn't valid. It needs the country code, like +447700900123 (or set the time zone in Settings → Company so local numbers work)."}
    body = str(body or "").strip()[:1500]
    if not body:
        return {"state": "failed", "message": "There is no message to send."}
    rid = db.run("INSERT INTO outbox(channel,to_addr,body,agent_id,contact_id,call_id,created_at) VALUES(?,?,?,?,?,?,?)", (channel, num, body, (agent or {}).get("id"), contact_id, call_id, db.now()))
    if not provider(channel):
        db.update("outbox", rid, status="no_provider", error="No messaging app connected")
        return {"state": "no_provider", "message": "No messaging app is connected yet, so nothing was sent. It is saved for the team. The admin can connect Twilio in Apps."}
    if policy() != "auto":
        return {"state": "waiting_for_approval", "id": rid, "message": "Queued for the admin's approval. Tell the customer it will arrive shortly, not that it already has."}
    row = db.q1("SELECT * FROM outbox WHERE id=?", (rid,))
    ok = await _dispatch(row)
    return {"state": "sent" if ok else "failed", "message": "Sent." if ok else db.q1("SELECT error FROM outbox WHERE id=?", (rid,))["error"]}


async def approve(rid):
    row = db.q1("SELECT * FROM outbox WHERE id=? AND status='pending_approval'", (rid,))
    if not row:
        return False, "Nothing waiting with that id."
    ok = await _dispatch(row)
    return ok, "" if ok else db.q1("SELECT error FROM outbox WHERE id=?", (rid,))["error"]


def reject(rid):
    db.update("outbox", rid, status="rejected")


def pending():
    return db.q("SELECT o.*, g.name agent_name FROM outbox o LEFT JOIN agents g ON g.id=o.agent_id WHERE o.status='pending_approval' ORDER BY o.id")


async def run_tool(agent, name, a):
    if name in ("send_sms", "send_whatsapp"):
        r = await send(agent, "sms" if name == "send_sms" else "whatsapp", a.get("to"), a.get("body"))
        return r, None
    if name == "list_pending_messages":
        return [{"id": r["id"], "channel": r["channel"], "to": r["to_addr"], "body": r["body"], "from": r["agent_name"]} for r in pending()], "/inbox"
    if name == "reject_message":
        reject(a["id"])
        return {"rejected": True}, None
    if name == "approve_message":
        row = db.q1("SELECT agent_id FROM outbox WHERE id=?", (a["id"],))
        if row and row["agent_id"] == (agent or {}).get("id"):
            return {"error": "you can't approve your own message"}, None
        ok, err = await approve(a["id"])
        return ({"sent": True} if ok else {"error": err}), None
    return {"error": "unknown tool"}, None

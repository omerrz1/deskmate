"""Texts and WhatsApp messages to customers. Sent through a connected messaging app (Twilio or WhatsApp Business).
Messages send automatically by default; the admin can explicitly switch the workspace to approval mode."""
import re

from . import db, integrations, voices

db.run("CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT, to_addr TEXT, body TEXT, agent_id INTEGER, contact_id INTEGER, call_id INTEGER, "
       "status TEXT DEFAULT 'pending_approval', error TEXT DEFAULT '', provider TEXT DEFAULT '', created_at REAL, sent_at REAL)")

_S = {"type": "string"}
TOOLS = [
    {"name": "send_sms", "description": "Send a text message now to a customer or contact through the connected provider. Use this when the admin asks you to send a text; do not ask them to confirm again. The workspace may explicitly be set to approval mode. Report sent, waiting, or failed from the tool result accurately.",
     "parameters": {"type": "object", "properties": {"contact": {**_S, "description": "Name (or id) of a saved contact, e.g. someone you just called. Prefer this over typing their number: retyped numbers lose digits."}, "to": {**_S, "description": "phone number in international format, e.g. +447700900123 (only when there is no saved contact)"}, "body": _S}, "required": ["body"]}},
    {"name": "send_whatsapp", "description": "Send a WhatsApp message now through the connected provider (Twilio if connected). Use this when the admin asks you to send a WhatsApp message; do not ask them to confirm again. The workspace may explicitly be set to approval mode. Report sent, waiting, or failed from the tool result accurately.",
     "parameters": {"type": "object", "properties": {"contact": {**_S, "description": "Name (or id) of a saved contact. Prefer this over typing their number."}, "to": {**_S, "description": "phone number in international format (only when there is no saved contact)"}, "body": _S}, "required": ["body"]}},
]
NAMES = {t["name"] for t in TOOLS}
MANAGER_TOOLS = [
    {"name": "list_pending_messages", "description": "Manager: texts and WhatsApp messages waiting for approval.", "parameters": {"type": "object", "properties": {}}},
    {"name": "approve_message", "description": "Manager: approve and send a waiting message (not your own).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "reject_message", "description": "Manager: reject a waiting message.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
]
MANAGER_NAMES = {t["name"] for t in MANAGER_TOOLS}


def policy():
    # Explicit approval remains available in Settings; direct actions default
    # to autonomous delivery so new workspaces do not silently queue messages.
    return voices.get_setting("messaging_policy", "auto")


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


def _why_invalid(num):
    """Mistakes the provider would only report as 'invalid' after a round trip. A UK mobile is +44 then exactly 10 digits starting with 7."""
    if num.startswith("+447") and len(num) != 13:
        return f"{num} is not a valid UK mobile number: after +44 it needs 10 digits starting with 7, and this has {len(num) - 3}. A digit is probably missing or extra."
    return ""


def _one_edit_apart(a, b):
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _near_known(num):
    """The one person the team has recently dealt with whose number is a single slip away from this one."""
    rows = db.q("SELECT DISTINCT p.id,p.name,p.phone FROM contacts p JOIN calls c ON c.contact_id=p.id WHERE p.phone<>'' ORDER BY c.id DESC LIMIT 300")
    hits = {r["id"]: r for r in rows if _one_edit_apart(num, normalise(r["phone"]))}
    return next(iter(hits.values())) if len(hits) == 1 else None


def find_contact(who):
    """A saved contact from a name or an id. -> (contact, problem)."""
    who = str(who or "").strip()
    if not who:
        return None, ""
    row = db.q1("SELECT * FROM contacts WHERE id=?", (int(who),)) if who.isdigit() and len(who) < 9 else None
    if row:
        return row, ""
    rows = db.q("SELECT * FROM contacts WHERE lower(name)=lower(?) ORDER BY id DESC LIMIT 5", (who,)) or db.q("SELECT * FROM contacts WHERE lower(name) LIKE lower(?) ORDER BY id DESC LIMIT 5", (who + "%",))
    if len(rows) == 1:
        return rows[0], ""
    if rows:
        return None, f"More than one saved contact is called '{who}': " + ", ".join(f"{r['name']} ({r['phone']})" for r in rows) + ". Use the number of the right one."
    return None, f"There is no saved contact called '{who}'. Use their phone number."


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
    error = "" if ok else str(res["error"])
    if not ok and key == "twilio" and re.search(r"errors/(21606|21659|21660)\b", error):
        try:  # "not a valid SMS number" is about the sender: look it up and say what is wrong with it and which numbers would work
            from . import twilio_call
            error = (error + " " + await twilio_call.sender_check(integrations._config(integrations.get_by_key("twilio")))).strip()
        except Exception:
            pass
    db.update("outbox", row["id"], status="sent" if ok else "failed", error=error[:1200], provider=key, sent_at=db.now() if ok else None)
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
    bad = _why_invalid(num)
    if bad:
        near = _near_known(num)
        return {"state": "failed", "message": bad + (f" Did you mean {near['phone']} ({near['name']})? Send it to that number if so." if near else ""),
                "note_for_you": "Nothing was sent. Tell the admin exactly this, and retry with the saved contact's name instead of typing the number."}
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
        to, contact_id = a.get("to"), None
        if a.get("contact"):  # a saved contact's own number beats a number the model typed out
            person, problem = find_contact(a["contact"])
            if problem:
                return {"state": "failed", "message": problem}, None
            to, contact_id = person["phone"], person["id"]
        r = await send(agent, "sms" if name == "send_sms" else "whatsapp", to, a.get("body"), contact_id=contact_id)
        if r.get("state") == "failed":
            r = {**r, "note_for_you": r.get("note_for_you") or "Tell the admin the exact reason and the fix, quoting any 'Sender check' and 'SMS-capable numbers' sentences word for word. The person you are talking to is the admin, so do not offer to notify the admin."}
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

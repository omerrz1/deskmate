"""A customer is on hold and the agent needs the admin: alert them now (in the app, by email and by text if connected), wait for the answer, hand it back to the call."""
import asyncio
import time

from . import db, discuss, office, team, voices

db.run("CREATE TABLE IF NOT EXISTS live_questions(id INTEGER PRIMARY KEY AUTOINCREMENT, call_id INTEGER, agent_id INTEGER, contact_name TEXT, question TEXT, context TEXT DEFAULT '', "
       "status TEXT DEFAULT 'open', answer TEXT DEFAULT '', remember INTEGER DEFAULT 1, created_at REAL, answered_at REAL)")


def timeout_seconds():
    try:
        return max(20, min(int(voices.get_setting("live_question_timeout", "75") or 75), 240))
    except ValueError:
        return 75


async def _alert(qid):
    q = db.q1("SELECT * FROM live_questions WHERE id=?", (qid,))
    who = (db.q1("SELECT name FROM agents WHERE id=?", (q["agent_id"],)) or {"name": "Your assistant"})["name"]
    try:
        await office.email_admin(f"URGENT: a customer is on hold, quick answer needed", f"{who} has {q['contact_name'] or 'a customer'} on hold and needs your answer now:\n\n{q['question']}\n\n{q['context']}\n\nOpen Deskmates to reply.")
    except Exception:
        pass
    phone = voices.get_setting("admin_phone", "")
    if phone:  # a heads-up text goes out straight away: it is for the admin, so it skips the customer-message approval
        try:
            from . import messaging
            num = messaging.normalise(phone)
            if num and messaging.provider("sms"):
                rid = db.run("INSERT INTO outbox(channel,to_addr,body,agent_id,created_at) VALUES('sms',?,?,?,?)",
                             (num, f"Deskmates: {who} has a customer on hold. Quick question: {q['question'][:200]} Open the app to answer.", q["agent_id"], db.now()))
                await messaging._dispatch(db.q1("SELECT * FROM outbox WHERE id=?", (rid,)))
        except Exception:
            pass


async def ask(call_id, question, context=""):
    """Used by the call-time tool ask_admin. Waits for the admin; always returns text the agent can act on."""
    call = db.q1("SELECT * FROM calls WHERE id=?", (call_id,)) or {}
    contact = db.q1("SELECT * FROM contacts WHERE id=?", (call.get("contact_id"),)) or {}
    agent_id = discuss.owner_for_call(call)
    qid = db.run("INSERT INTO live_questions(call_id,agent_id,contact_name,question,context,created_at) VALUES(?,?,?,?,?,?)",
                 (call_id, agent_id, contact.get("name", ""), str(question)[:600], str(context)[:600], db.now()))
    team.log_event(agent_id, "call", "phone", f"Customer on hold, asked the admin: {str(question)[:100]}", {"call": call_id})
    asyncio.get_running_loop().create_task(_alert(qid))
    deadline = time.time() + timeout_seconds()
    while time.time() < deadline:
        row = db.q1("SELECT * FROM live_questions WHERE id=?", (qid,))
        if row["status"] == "answered":
            return f"The admin says: {row['answer']}\nGive this to the customer now in your own words, short and natural. Thank them for waiting."
        if row["status"] == "declined":
            break
        cur = db.q1("SELECT status FROM calls WHERE id=?", (call_id,))
        if not cur or cur["status"] not in ("in_progress", "ringing"):
            db.update("live_questions", qid, status="cancelled")
            return "The call has ended."
        await asyncio.sleep(0.5)
    row = db.q1("SELECT status FROM live_questions WHERE id=?", (qid,))
    if row["status"] == "open":
        db.update("live_questions", qid, status="timeout")
    discuss.add(agent_id, str(question)[:300], f"A customer ({contact.get('name', 'unknown')}) asked this on a call and the admin could not be reached in time.", "call", f"call {call_id}")
    return ("The admin couldn't answer right now. Apologise briefly, tell the customer you'll find out and get back to them (offer to take an email or a good time to call), "
            "and carry on. Don't guess.")


def listing(status="open"):
    return [dict(r) | {"agent_name": (db.q1("SELECT name FROM agents WHERE id=?", (r["agent_id"],)) or {}).get("name", ""), "waiting": int(time.time() - r["created_at"])}
            for r in db.q("SELECT * FROM live_questions WHERE status=? ORDER BY id", (status,))]

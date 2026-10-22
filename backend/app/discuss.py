"""Things an employee wants to talk through with the admin: questions customers asked that nobody could answer, gaps in a report, decisions needed.
They collect here, are raised in the next meeting, and can be answered in the app. A digest email goes out when they pile up."""
import time

from . import db, voices

db.run("CREATE TABLE IF NOT EXISTS discuss_items(id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, topic TEXT, detail TEXT DEFAULT '', source TEXT DEFAULT 'agent', ref TEXT DEFAULT '', "
       "status TEXT DEFAULT 'open', answer TEXT DEFAULT '', emailed INTEGER DEFAULT 0, created_at REAL, answered_at REAL)")
db.run("CREATE INDEX IF NOT EXISTS idx_discuss ON discuss_items(status, agent_id)")

_S = {"type": "string"}
TOOLS = [
    {"name": "add_discussion_item", "description": "Add something to your list of things to discuss with the admin: a question you couldn't answer, a gap in a report, a decision you need. Use it instead of guessing. You will raise it in your next meeting and it is emailed to the admin if it waits.",
     "parameters": {"type": "object", "properties": {"topic": {**_S, "description": "the question or subject, in a sentence"}, "detail": {**_S, "description": "context: who asked, what you already tried"}}, "required": ["topic"]}},
    {"name": "list_discussion_items", "description": "Your list of things to discuss with the admin.", "parameters": {"type": "object", "properties": {}}},
    {"name": "resolve_discussion_item", "description": "Mark an item as discussed and keep the admin's answer. Do this after the admin answers.",
     "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "answer": _S}, "required": ["id", "answer"]}},
]
NAMES = {t["name"] for t in TOOLS}


def add(agent_id, topic, detail="", source="agent", ref=""):
    topic = " ".join(str(topic).split())[:300]
    if not topic:
        return None
    dup = db.q1("SELECT id FROM discuss_items WHERE status='open' AND lower(topic)=lower(?) AND agent_id=?", (topic, agent_id or 0))
    if dup:
        return dup["id"]
    return db.run("INSERT INTO discuss_items(agent_id,topic,detail,source,ref,created_at) VALUES(?,?,?,?,?,?)", (agent_id or 0, topic, str(detail)[:1500], source, str(ref)[:60], db.now()))


def owner_for_call(call):
    """Who raises a question that came up on a call: the campaign's agent, else a manager, else the first agent."""
    camp = db.q1("SELECT agent_id FROM campaigns WHERE id=?", (call.get("campaign_id"),)) if call and call.get("campaign_id") else None
    if camp and camp["agent_id"]:
        return camp["agent_id"]
    r = db.q1("SELECT id FROM agents WHERE archived=0 AND is_manager=1 ORDER BY id LIMIT 1") or db.q1("SELECT id FROM agents WHERE archived=0 ORDER BY id LIMIT 1")
    return r["id"] if r else 0


def open_items(agent_id=None):
    if agent_id is None:
        return db.q("SELECT d.*, g.name agent_name FROM discuss_items d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.status='open' ORDER BY d.id")
    return db.q("SELECT d.*, g.name agent_name FROM discuss_items d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.status='open' AND d.agent_id=? ORDER BY d.id", (agent_id,))


def resolve(item_id, answer, remember=False):
    it = db.q1("SELECT * FROM discuss_items WHERE id=?", (item_id,))
    if not it:
        return False
    db.update("discuss_items", item_id, status="discussed", answer=str(answer)[:3000], answered_at=db.now())
    if remember and str(answer).strip():
        remember_answer(it["topic"], answer, it["agent_id"])
    return True


def remember_answer(question, answer, agent_id=0, kb_id=None):
    """Keep an answer from the admin where the callers can find it next time."""
    from . import knowledge
    if not kb_id:
        kb = db.q1("SELECT id FROM knowledge_bases WHERE name='Learned from calls'")
        kb_id = kb["id"] if kb else db.run("INSERT INTO knowledge_bases(name,persona,context,created_at) VALUES('Learned from calls',?,?,?)",
                                          (knowledge.DEFAULT_PERSONA, "Answers the admin gave when callers asked something new.", db.now()))
    knowledge.add_doc(kb_id, str(question)[:100], f"Q: {question}\nA: {answer}", "info", "FAQ", "answered by the admin")


def prompt_block(agent_id):
    items = open_items(agent_id)
    if not items:
        return ""
    lines = "; ".join(f"#{i['id']} {i['topic']}" for i in items[:8])
    return (f"THINGS TO DISCUSS WITH THE ADMIN ({len(items)} open): {lines}. In a meeting with the admin, raise these early and ask one at a time. "
            "When the admin answers, call resolve_discussion_item with their answer. If you can't answer something the admin asks for (a report, a figure), say so, add it with add_discussion_item and offer to email them.\n")


async def digest():
    """Email the admin once when new items have waited a while, grouped by employee."""
    if voices.get_setting("discuss_email", "1") != "1":
        return
    from . import office
    rows = db.q("SELECT d.*, g.name agent_name FROM discuss_items d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.status='open' AND d.emailed=0 AND d.created_at<? ORDER BY d.agent_id, d.id", (time.time() - 600,))
    by = {}
    for r in rows:
        by.setdefault(r["agent_name"] or "Your team", []).append(r)
    for who, items in by.items():
        body = "\n".join(f"- {i['topic']}" + (f" ({i['detail'][:160]})" if i["detail"] else "") for i in items)
        await office.email_admin(f"{who} has {len(items)} thing{'s' if len(items) != 1 else ''} to discuss with you", f"{who} needs your input on:\n\n{body}\n\nAnswer them in Deskmates (open {who}'s page), or in your next meeting with them.")
        for i in items:
            db.update("discuss_items", i["id"], emailed=1)


async def run_tool(agent, name, a):
    aid = agent["id"] if agent else 0
    if name == "add_discussion_item":
        iid = add(aid, a.get("topic", ""), a.get("detail", ""), "agent")
        return ({"added": iid} if iid else {"error": "Say what the topic is."}), "/"
    if name == "list_discussion_items":
        return [{"id": i["id"], "topic": i["topic"], "detail": i["detail"], "from": i["source"]} for i in open_items(aid)], "/"
    if name == "resolve_discussion_item":
        ok = resolve(a["id"], a.get("answer", ""), remember=True)
        return ({"resolved": True} if ok else {"error": "No such item."}), "/"
    return {"error": "unknown tool"}, None

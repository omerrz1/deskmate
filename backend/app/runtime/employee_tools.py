"""The tools an employee has that belong to no other module: their own profile and memory, knowledge bases, duties, asking a colleague, and the manager's connector tools.
One function per tool; `run` is the toolset entry point the registry calls."""
import asyncio
import json

from .. import admin_agent, connectors, crm, db, knowledge, office, team

_S = {"type": "string"}
AGENT_TOOLS = [
    {"name": "update_my_profile", "description": "Rewrite your own profile: personality, call persona (how you behave on phone calls), bio, role. Only the fields you give change.",
     "parameters": {"type": "object", "properties": {"personality": _S, "persona": _S, "bio": _S, "role": _S}}},
    {"name": "remember", "description": "Save a short note to your own memory (preferences, decisions, lessons) so you remember it in future meetings and duties.",
     "parameters": {"type": "object", "properties": {"note": _S}, "required": ["note"]}},
    {"name": "create_knowledge_base", "description": "Create a new knowledge base (persona, key facts and documents campaigns can use). Returns its id.",
     "parameters": {"type": "object", "properties": {"name": _S, "persona": _S, "context": _S, "key_facts": _S, "next_steps": _S}, "required": ["name"]}},
    {"name": "add_knowledge", "description": "Add a document to a knowledge base. Long text is split into topics automatically.",
     "parameters": {"type": "object", "properties": {"kb_id": {"type": "integer"}, "title": _S, "text": _S}, "required": ["kb_id", "title", "text"]}},
    {"name": "update_knowledge_base", "description": "Edit a knowledge base's persona, context, key facts, next steps or hand-off contact.",
     "parameters": {"type": "object", "properties": {"kb_id": {"type": "integer"}, "persona": _S, "context": _S, "key_facts": _S, "next_steps": _S, "contact": _S}, "required": ["kb_id"]}},
    {"name": "search_knowledge", "description": "Search a knowledge base.", "parameters": {"type": "object", "properties": {"kb_id": {"type": "integer"}, "query": _S}, "required": ["kb_id", "query"]}},
    {"name": "learn_from_website", "description": "Read a public website and turn it into a new knowledge base. Returns the kb id and what was learned.",
     "parameters": {"type": "object", "properties": {"url": _S}, "required": ["url"]}},
    {"name": "assign_duty", "description": "Give a duty to a team member (including yourself). With run_now the agent starts working on it immediately by itself.",
     "parameters": {"type": "object", "properties": {"agent": {**_S, "description": "agent name"}, "title": _S, "detail": _S, "due": _S, "run_now": {"type": "boolean"}}, "required": ["agent", "title"]}},
    {"name": "list_duties", "description": "List duties (optionally for one agent) with status.", "parameters": {"type": "object", "properties": {"agent": _S}}},
    {"name": "complete_duty", "description": "Mark a duty done and record the result.", "parameters": {"type": "object", "properties": {"duty_id": {"type": "integer"}, "result": _S}, "required": ["duty_id", "result"]}},
    {"name": "ask_colleague", "description": "Ask another agent a question and get their answer.", "parameters": {"type": "object", "properties": {"agent": _S, "question": _S}, "required": ["agent", "question"]}},
    {"name": "create_agent", "description": "Hire a new AI team member (same as hire_agent).",
     "parameters": {"type": "object", "properties": {"brief": {**_S, "description": "what the new agent is for"}, "gender": _S, "name": _S}, "required": ["brief"]}},
    {"name": "weekly_report", "description": "One combined report: KPIs, campaigns with progress, outcome mix and the latest call summaries.", "parameters": {"type": "object", "properties": {}}},
]
# manager-only: registered with requires="manager", so the ManagerOnly policy guards them
CONNECTOR_TOOLS = [
    {"name": "add_connector", "description": "Manager: connect a new set of tools through an MCP server (public https address). The admin is told.",
     "parameters": {"type": "object", "properties": {"name": _S, "url": _S, "token": {**_S, "description": "access token if the server needs one"}}, "required": ["name", "url"]}},
    {"name": "list_connectors", "description": "Manager: connected tool servers and the tools they offer.", "parameters": {"type": "object", "properties": {}}},
    {"name": "toggle_connector", "description": "Manager: switch a connector on or off.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "enabled": {"type": "boolean"}}, "required": ["id", "enabled"]}},
]

HANDLERS = {}


def tool(name):
    def register(fn):
        HANDLERS[name] = fn
        return fn
    return register


def _agents():
    from .. import agents  # imported on use: agents.py imports this package
    return agents


def _tools_of(connector_id):
    return json.loads((db.company_q1('SELECT tools FROM connectors WHERE id=?', (connector_id,)) or {}).get('tools') or '[]')


@tool("update_my_profile")
async def update_my_profile(agent, a):
    f = {k: str(a[k])[:4000] for k in ("personality", "persona", "bio", "role") if a.get(k)}
    if f:
        db.update("agents", agent["id"], **f)
        db.log("agent", f"{agent['name']} rewrote their {', '.join(f)}")
    return {"updated": list(f)}, f"/agents/{agent['id']}"


@tool("remember")
async def remember(agent, a):
    db.run("INSERT INTO agent_notes(agent_id,note,created_at) VALUES(?,?,?)", (agent["id"], str(a["note"])[:400], db.now()))
    return {"saved": True}, None


@tool("create_knowledge_base")
async def create_knowledge_base(agent, a):
    kid = db.run("INSERT INTO knowledge_bases(name,agent_name,persona,context,key_facts,next_steps,contact,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (a["name"][:80], "", a.get("persona", ""), a.get("context", ""), a.get("key_facts", ""), a.get("next_steps", ""), "", db.now()))
    db.log("agent", f"{agent['name']} created knowledge base '{a['name']}'")
    return {"kb_id": kid}, "/knowledge"


@tool("add_knowledge")
async def add_knowledge(agent, a):
    n = await knowledge.ingest(a["kb_id"], str(a["title"])[:120], str(a["text"])[:60000], source=f"written by {agent['name']}")
    return {"sections_added": n}, "/knowledge"


@tool("update_knowledge_base")
async def update_knowledge_base(agent, a):
    f = {k: str(a[k])[:6000] for k in ("persona", "context", "key_facts", "next_steps", "contact") if a.get(k)}
    if f:
        db.update("knowledge_bases", a["kb_id"], **f)
        db.log("agent", f"{agent['name']} updated knowledge base {a['kb_id']} ({', '.join(f)})")
    return {"updated": list(f)}, "/knowledge"


@tool("search_knowledge")
async def search_knowledge(agent, a):
    return knowledge.search(a["kb_id"], a["query"], k=4), "/knowledge"


@tool("learn_from_website")
async def learn_from_website(agent, a):
    from .. import webdemo
    try:
        r = await asyncio.wait_for(webdemo.learn(a["url"]), 90)
    except webdemo.Blocked as e:
        return {"error": str(e)}, None
    except Exception as e:
        return {"error": f"could not read the site ({type(e).__name__})"}, None
    db.log("agent", f"{agent['name']} learned from {a['url']}: {r['sections']} sections")
    return r, "/knowledge"


@tool("assign_duty")
async def assign_duty(agent, a):
    ag = _agents()
    target = ag.by_name(a["agent"])
    if not target:
        return {"error": f"no agent called {a['agent']}"}, None
    did = db.run("INSERT INTO duties(agent_id,title,detail,due,created_by,created_at,auto) VALUES(?,?,?,?,?,?,1)",
                 (target["id"], a["title"][:200], str(a.get("detail", ""))[:2000], str(a.get("due", ""))[:40], agent["name"] if agent else "admin", db.now()))
    db.log("agent", f"Duty '{a['title']}' assigned to {target['name']}")
    if a.get("run_now"):
        ag.start_duty(did)
    return {"duty_id": did, "assigned_to": target["name"], "started": bool(a.get("run_now"))}, f"/agents/{target['id']}"


@tool("list_duties")
async def list_duties(agent, a):
    w, args = "1=1", []
    if a.get("agent"):
        t = _agents().by_name(a["agent"])
        w, args = "agent_id=?", [t["id"] if t else -1]
    rows = db.q(f"SELECT d.id,d.title,d.status,d.due,d.result,g.name agent FROM duties d LEFT JOIN agents g ON g.id=d.agent_id WHERE {w} ORDER BY d.id DESC LIMIT 25", args)
    return [dict(r) for r in rows], "/agents"


@tool("complete_duty")
async def complete_duty(agent, a):
    d0 = db.q1("SELECT * FROM duties WHERE id=?", (a["duty_id"],))
    db.update("duties", a["duty_id"], status="done", result=str(a["result"])[:3000], done_at=db.now())
    if d0 and d0["status"] != "done":
        office.spawn_recurrence(dict(d0))
    return {"done": True}, None


@tool("ask_colleague")
async def ask_colleague(agent, a):
    ag = _agents()
    t = ag.by_name(a["agent"])
    if not t:
        return {"error": f"no agent called {a['agent']}"}, None
    txt = ""
    async for ev in ag.run_loop(t, [{"role": "user", "content": f"{agent['name']} asks you (answer briefly, in your own voice): {a['question']}"}], "one_to_one", tools=False):
        if ev["type"] == "done":
            txt = ev["text"]
    return {"answer": txt}, None


@tool("create_agent")
async def create_agent(agent, a):  # the manager's old name for hire_agent: being asked IS the confirmation
    ag = _agents()
    info = await ag.write_profile(a["brief"], a.get("gender") or "female", a.get("name") or "")
    aid = ag.create(info.get("name") or a.get("name") or "New agent", a.get("gender") or "female", info.get("role", ""), info.get("personality", ""), info.get("persona", ""), bio=info.get("bio", ""))
    db.log("agent", f"{agent['name']} hired a new agent: {info.get('name')}")
    return {"agent_id": aid, "name": info.get("name"), "role": info.get("role")}, f"/agents/{aid}"


@tool("weekly_report")
async def weekly_report(agent, a):
    s = crm.stats()
    s.pop("by_day", None)
    camps, _ = await admin_agent.run_tool("list_campaigns", {})
    calls, _ = await admin_agent.run_tool("list_calls", {"limit": 8, "status": "completed"})
    return {"kpis": s, "campaigns": camps, "latest_calls": calls}, "/overview"


@tool("add_connector")
async def add_connector(agent, a):
    try:
        cid = await connectors.add(a["name"], a["url"], a.get("token", ""), added_by=f"{agent['name']} (manager)", public_only=True)
    except connectors.MCPError as e:
        return {"error": str(e)}, None
    await office.email_admin(f"{agent['name']} connected a new tool server: {a['name']}", f"{agent['name']} (manager) connected {a['url']}. Review it in Apps; you can switch it off there.")
    team.log_event(agent["id"], "manager", "tool", f"Connected tool server: {a['name']}", {"url": a["url"]})
    return {"connector_id": cid, "tools": [t["name"] for t in _tools_of(cid)]}, "/settings/connectors"


@tool("list_connectors")
async def list_connectors(agent, a):
    return connectors.listing(), "/settings/connectors"


@tool("toggle_connector")
async def toggle_connector(agent, a):
    if db.current() != db.company():
        return {'error': 'Ask a company admin to change this connection.'}, None
    db.company_update("connectors", a["id"], enabled=int(bool(a["enabled"])))
    return {"ok": True}, "/settings/connectors"


async def run(ctx, name, a):
    return await HANDLERS[name](ctx.agent, a)

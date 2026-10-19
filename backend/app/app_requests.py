"""An employee needs an app that isn't connected: they tell the admin what it is, why they need it, and exactly how to set it up."""
import re

from . import catalog, db, integrations, office, team

db.run("CREATE TABLE IF NOT EXISTS app_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, app_key TEXT DEFAULT '', app_name TEXT, why TEXT DEFAULT '', how_used TEXT DEFAULT '', "
       "status TEXT DEFAULT 'open', created_at REAL)")

_S = {"type": "string"}
TOOLS = [
    {"name": "request_app", "description": "You need an outside app or service that isn't connected yet (a calendar, CRM, texting, payments, a search tool...). Tell the admin which one, why you need it and how you would use it. "
     "They get step-by-step setup instructions and a button to set it up. Use this instead of just saying you can't do something.",
     "parameters": {"type": "object", "properties": {"app": {**_S, "description": "name of the app or service, e.g. 'Slack' or 'Google Calendar'"}, "why": {**_S, "description": "what you need it for, in plain words"},
                                                    "how_used": {**_S, "description": "what you would do with it day to day"}}, "required": ["app", "why"]}},
]
NAMES = {t["name"] for t in TOOLS}


def match_spec(text):
    t = re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).strip()
    if not t:
        return None
    best, score = None, 0
    for a in catalog.APPS:
        names = {a["id"].replace("_", " "), re.sub(r"\(.*?\)", "", a["name"]).lower().strip()}
        sc = 0
        for n in names:
            n = re.sub(r"[^a-z0-9 ]", " ", n).strip()
            if n and (n == t):
                sc = 10
            elif n and (n in t or t in n):
                sc = max(sc, 6)
            else:
                shared = set(n.split()) & set(t.split())
                if shared and any(len(w) > 3 for w in shared):
                    sc = max(sc, 3)
        if sc > score:
            best, score = a, sc
    return best if score >= 3 else None


def steps_for(spec):
    if not spec:
        return ["Open Apps.", "If it has a ready-made card, connect it there. Otherwise use \"Any other API (custom)\" with the service's API address and key, or a Zapier/Make webhook, or an MCP server if the vendor offers one.",
                "Ask the vendor (or whoever manages the account) for an API key or token, and for the permissions the request mentions."]
    out = [f"Open Apps and choose {spec['name']}."]
    if spec.get("help"):
        out.append(spec["help"])
    for f in spec["fields"]:
        if f.get("required", True):
            out.append(f"Enter {f['label']}." + (f" {f['help']}" if f.get("help") else ""))
    out.append("Click Connect. It tests the connection straight away." if spec["fields"] else "Click Connect. This one needs no account or key.")
    return out


def _installed(spec):
    return bool(spec and db.company_q1("SELECT id FROM integrations WHERE kind=?", (spec["id"],)))


def listing():
    out = []
    for r in db.q("SELECT r.*, g.name agent_name, g.color agent_color FROM app_requests r LEFT JOIN agents g ON g.id=r.agent_id WHERE r.status='open' ORDER BY r.id"):
        spec = next((a for a in catalog.APPS if a["id"] == r["app_key"]), None)
        if spec and _installed(spec):
            db.update("app_requests", r["id"], status="done")  # it has been connected since
            continue
        out.append({"id": r["id"], "agent_name": r["agent_name"] or "Your team", "agent_color": r["agent_color"] or "#111", "app_key": r["app_key"], "app_name": r["app_name"], "why": r["why"],
                    "how_used": r["how_used"], "steps": steps_for(spec), "icon": spec["id"] if spec else "", "created_at": r["created_at"]})
    return out


async def request(agent, a):
    name = str(a.get("app", "")).strip()[:80]
    if not name:
        return {"error": "Say which app you need."}
    spec = match_spec(name)
    if spec and _installed(spec):
        return {"error": f"{spec['name']} is already connected. Use connected_apps to see its actions."}
    key = spec["id"] if spec else ""
    app_name = spec["name"] if spec else name
    dup = db.q1("SELECT id FROM app_requests WHERE status='open' AND agent_id=? AND lower(app_name)=lower(?)", ((agent or {}).get("id", 0), app_name))
    if dup:
        return {"already_asked": True, "note": "The admin has already been asked. Carry on with what you can do."}
    db.run("INSERT INTO app_requests(agent_id,app_key,app_name,why,how_used,created_at) VALUES(?,?,?,?,?,?)",
           ((agent or {}).get("id", 0), key, app_name, str(a.get("why", ""))[:600], str(a.get("how_used", ""))[:600], db.now()))
    who = (agent or {}).get("name", "A teammate")
    steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps_for(spec)))
    await office.email_admin(f"{who} needs {app_name} connected",
                             f"{who} asked for {app_name}.\n\nWhy: {a.get('why', '')}\nHow it would be used: {a.get('how_used', '') or 'not said'}\n\nHow to set it up:\n{steps}\n\nOpen Deskmates to set it up.")
    team.log_event((agent or {}).get("id", 0), "manager", "tool", f"Asked the admin to connect {app_name}", {"why": a.get("why", "")})
    return {"requested": True, "note": "The admin has the explanation and setup steps. Carry on with what you can do meanwhile, and tell the person you'll follow up once it's connected."}


async def run_tool(agent, name, a):
    return await request(agent, a), "/"

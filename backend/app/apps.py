"""One front door to everything an employee can connect to: catalog apps (integrations.py) and MCP servers (connectors.py).
Employees see a short list and ask for details only about the app they need, so many apps never bloat their instructions."""
import json

from . import capabilities, connectors, db, integrations

_S = {"type": "string"}


def _mcp_rows():
    return [c for c in db.company_q("SELECT * FROM connectors WHERE enabled=1 ORDER BY id")]


def list_all(agent=None):
    out = []
    if agent is None or capabilities.enabled(agent, "public"):
        out += integrations.describe_all(builtin=True)
    if agent is None or capabilities.enabled(agent, "connectors"):
        out += integrations.describe_all(builtin=False)
    for c in (_mcp_rows() if (agent is None or capabilities.enabled(agent, 'connectors')) else []):
        tools = json.loads(c["tools"] or "[]")
        out.append({"app": f"mcp-{c['id']}", "name": c["name"], "category": "MCP server", "what_it_does": "Tools offered by a connected MCP server.",
                    "actions": [t["name"] for t in tools], "can_make_changes": True})
    return out


def describe(key):
    d = integrations.describe_app(key)
    if d:
        return d
    if str(key).startswith("mcp-") and str(key)[4:].isdigit():
        c = db.company_q1("SELECT * FROM connectors WHERE id=? AND enabled=1", (int(str(key)[4:]),))
        if c:
            return {"app": key, "name": c["name"], "actions": [{"action": t["name"], "does": t["description"], "makes_changes": True, "inputs": (t.get("schema") or {}).get("properties", {}), "required": (t.get("schema") or {}).get("required", [])}
                                                              for t in json.loads(c["tools"] or "[]")]}
    return None


def is_write(key, action):
    if str(key).startswith("mcp-"):
        return True
    return integrations.is_write(key, action)


async def call(key, action, inputs):
    key = str(key or "").strip()
    if key.lower().startswith("mcp-") and key[4:].isdigit():
        c = db.company_q1("SELECT * FROM connectors WHERE id=? AND enabled=1", (int(key[4:]),))
        if not c or action not in [t["name"] for t in json.loads(c["tools"] or "[]")]:
            return {"error": "That MCP tool is not available."}
        try:
            return await connectors.call_tool(c["url"], c["token"], action, inputs if isinstance(inputs, dict) else {})
        except connectors.MCPError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"connector failed ({type(e).__name__})"}
    return await integrations.call(key, action, inputs if isinstance(inputs, dict) else {})


def summary_line(agent=None):
    built = [x["app"] for x in list_all(agent) if x.get("built_in")]
    conn = [f"{x['name']} (key: {x['app']})" for x in list_all(agent) if not x.get("built_in")]
    parts = []
    if built:
        parts.append("BUILT-IN public tools, always available, no setup: " + ", ".join(built))
    parts.append(("connected: " + "; ".join(conn)) if conn else "nothing else connected yet")
    return " | ".join(parts) + ". Use connected_apps to see an app's actions, then use_app to run one. For an outside service that isn't connected, use request_app to tell the admin which one and why."


TOOLS = [
    {"name": "connected_apps", "description": "See the outside apps connected for the team (calendar, CRM, texting, payments, spreadsheets and more). With no input it lists them; with an app key it shows that app's actions and what each needs.",
     "parameters": {"type": "object", "properties": {"app": {**_S, "description": "app key from the list, to see its actions"}}}},
    {"name": "use_app", "description": "Run an action in a connected app, e.g. app='slack', action='post_message', inputs={channel:'#sales', text:'...'}. Check connected_apps first for the exact action names and inputs.",
     "parameters": {"type": "object", "properties": {"app": _S, "action": _S, "inputs": {"type": "object", "description": "the action's inputs"}}, "required": ["app", "action"]}},
]
NAMES = {t["name"] for t in TOOLS}


async def run_tool(agent, name, a):
    if name == "connected_apps":
        if not a.get("app"):
            return list_all(agent), "/settings/connectors"
        if a.get("app"):
            d = describe(a["app"])
            return (d or {"error": f"'{a['app']}' is not connected. Use request_app to tell the admin what you need and why."}), "/settings/connectors"
        return list_all(), "/settings/connectors"
    key, action = str(a.get("app", "")), str(a.get("action", ""))
    need = "public" if integrations.is_builtin(key) else "connectors"
    if agent and not capabilities.enabled(agent, need):
        return {"error": "That ability is switched off for you. Ask the admin or your manager."}, None
    res = await call(key, action, a.get("inputs") or {})
    return res, None

"""The audit trail: every tool an employee uses is written to their activity history, which the Computer screen and the manager's reviews read."""
import json

from harness import AFTER_TOOL, TOOL_BLOCKED, HookBus, Verdict

from .. import browser_tools, db

# which "app" on the employee's computer a tool belongs to
APPS = {
    "apps": {"use_app", "connected_apps"},
    "mail": {"check_inbox", "read_email", "send_email", "reply_email", "search_email", "notify_admin", "ask_for_help", "ask_colleague"},
    "tasks": {"add_todo", "list_todos", "update_todo", "assign_duty", "complete_duty", "list_duties"},
    "files": {"create_document", "create_spreadsheet", "edit_sheet", "read_document", "update_document", "list_documents", "list_files", "create_folder", "move_file", "copy_file", "delete_file",
              "sheet_profile", "sheet_clean", "sheet_merge", "sheet_query", "sheet_job"},
    "calendar": {"create_event", "list_events", "cancel_event", "get_current_time"},
    "phone": {"call_contact", "schedule_call", "start_campaign", "stop_campaign", "create_campaign", "list_calls", "get_call", "get_call_report", "export_campaign_report", "list_campaigns", "list_scheduled_calls", "get_client_report", "search_contacts", "update_contact", "get_segments", "get_dashboard_stats", "weekly_report"},
    "web": {"read_webpage", "learn_from_website", "learn_company_website"} | browser_tools.NAMES,
    "notes": {"remember", "learn_procedure", "get_procedure", "list_procedures", "forget_procedure", "update_my_profile"},
    "knowledge": {"create_knowledge_base", "add_knowledge", "update_knowledge_base", "search_knowledge", "search_company_website", "list_knowledge_bases"},
}


def app_of(tool):
    return next((k for k, v in APPS.items() if tool in v), "system")


def _summary(name, a, res):
    g = lambda k: str(a.get(k, ""))[:90]
    s = {"join_video_meeting": lambda: f"Joined a video meeting", "say_in_video_meeting": lambda: f"Said in the meeting: {g('text')}", "leave_video_meeting": lambda: "Left the video meeting", "add_discussion_item": lambda: f"Added to things to discuss: {g('topic')}", "request_app": lambda: f"Asked the admin to connect {g('app')}", "web_search": lambda: f"Searched the web: {g('query')}", "news_search": lambda: f"Looked up news: {g('query')}", "world_time": lambda: f"Checked the time in {g('place')}", "calculate": lambda: f"Calculated {g('expression')}", "make_chart": lambda: f"Made a chart: {g('title')}", "export_document": lambda: f"Exported file #{g('id')} as {g('format')}", "sheet_query": lambda: f"Queried spreadsheet #{g('id')}", "send_email": lambda: f"Emailed {g('to')}: {g('subject')}", "send_sms": lambda: f"Texted {g('to')}", "send_whatsapp": lambda: f"WhatsApped {g('to')}", "reply_email": lambda: f"Replied to email #{g('id')}", "ask_for_help": lambda: f"Asked {g('who')} for help",
         "read_email": lambda: f"Read email #{g('id')}", "check_inbox": lambda: "Checked the inbox", "notify_admin": lambda: f"Messaged the admin: {g('subject')}",
         "add_todo": lambda: f"Added a to-do: {g('title')}", "update_todo": lambda: f"Updated to-do #{g('id')}", "assign_duty": lambda: f"Gave {g('agent')} a task: {g('title')}",
         "create_document": lambda: f"Wrote a document: {g('title')}", "create_spreadsheet": lambda: f"Made a spreadsheet: {g('title')}", "edit_sheet": lambda: f"Updated spreadsheet #{g('id')}",
         "sheet_profile": lambda: f"Looked over the spreadsheet {g('title') or g('id')}", "sheet_clean": lambda: f"Cleaned the spreadsheet {g('title') or g('id')}",
         "sheet_merge": lambda: f"Merged {g('left')} with {g('right')}", "sheet_query": lambda: f"Searched the spreadsheet {g('title') or g('id')}",
         "sheet_job": lambda: f"Spreadsheet job ({g('action')}) on {g('title') or g('id')}",
         "update_document": lambda: f"Edited file #{g('id')}", "create_folder": lambda: f"Made folder {g('path')}", "create_event": lambda: f"Added to calendar: {g('title')}",
         "read_webpage": lambda: f"Read {g('url')}", "use_app": lambda: f"Used {g('app')}: {g('action')}", "connected_apps": lambda: "Looked at connected apps", "learn_from_website": lambda: f"Studied {g('url')}", "learn_company_website": lambda: "Built or refreshed the company website knowledge base", "search_company_website": lambda: f"Searched company website knowledge: {g('query')}", "call_contact": lambda: f"Placed a call to customer #{g('contact_id')}",
         "schedule_call": lambda: f"Scheduled a call (customer #{g('contact_id')})", "start_campaign": lambda: f"Started campaign #{g('campaign_id')}", "create_campaign": lambda: f"Created campaign {g('name')}", "get_call_report": lambda: f"Prepared the full report for call #{g('call_id')}", "export_campaign_report": lambda: f"Prepared campaign report #{g('campaign_id')}",
         "learn_procedure": lambda: f"Learned: {g('name')}", "remember": lambda: "Made a note", "weekly_report": lambda: "Pulled the weekly report", "get_dashboard_stats": lambda: "Checked the numbers"}
    return (s.get(name) or (lambda: name.replace("_", " ").capitalize()))()


KIND_BY_APP = {"mail": "mail", "tasks": "task", "phone": "call", "files": "file", "calendar": "meeting", "notes": "profile", "web": "tool", "knowledge": "tool"}

_LONG_KEYS = {"body", "content", "text", "message", "note", "result", "columns", "rows", "set_cells", "add_rows", "question", "detail", "objective", "query"}


def log_activity(agent_id, name, a, res):
    ok = 0 if isinstance(res, dict) and "error" in res else 1
    # what they typed is kept in full (up to 2,000 characters) so the Computer screen can replay it; everything else is short
    args = {k: (json.dumps(v, ensure_ascii=False, default=str)[:1200] if isinstance(v, (list, dict)) else str(v)[:2000 if k in _LONG_KEYS else 200]) for k, v in (a or {}).items() if k not in ("token", "password")}
    d = {"args": args}
    if isinstance(res, dict):
        if not ok:
            d["error"] = str(res.get("error"))[:200]
        elif res.get("id") is not None:
            d["ref"] = {"id": res.get("id"), "title": str(res.get("title") or "")[:120]}
    if name in ("read_webpage", "learn_from_website"):
        d.update({"url": a.get("url"), "title": (res or {}).get("title") if isinstance(res, dict) else "", "snippet": ((res or {}).get("text") or "")[:900] if isinstance(res, dict) else ""})
    if name in ("web_search", "news_search") and isinstance(res, dict):
        found = res.get("results") or res.get("articles") or res.get("items") or []
        d["results"] = [{"title": str(x.get("title") or "")[:120], "url": str(x.get("url") or x.get("link") or "")[:200], "snippet": str(x.get("snippet") or x.get("description") or "")[:200]} for x in found[:6] if isinstance(x, dict)]
    if name.startswith("browser_") and isinstance(res, dict):  # what the Computer screen shows; the model does not need the picture's address
        d.update({"url": res.get("url"), "title": res.get("title"), "shot": res.pop("shot", None), "at": res.pop("at", None), "snippet": str(res.get("text") or "")[:600]})
    app = app_of(name)
    if name.startswith("mcp__"):
        app = "tool"
    text = json.dumps(d, ensure_ascii=False, default=str)
    if len(text) > 9000:  # keep it valid JSON: drop the biggest field instead of cutting the text
        d["args"] = {k: v[:300] for k, v in args.items()}
        text = json.dumps(d, ensure_ascii=False, default=str)[:9000]
    db.run("INSERT INTO agent_activity(agent_id,ts,tool,app,summary,detail,ok,kind) VALUES(?,?,?,?,?,?,?,?)",
           (agent_id, db.now(), name, app, _summary(name, a, res), text, ok, KIND_BY_APP.get(app, "tool")))


# tools that write their own, more descriptive history entry
SELF_LOGGED = {"create_project", "invite_to_project", "post_update", "share_file", "share_folder", "request_meeting", "update_agent", "hire_agent", "remove_agent", "set_setting", "approve_email", "add_connector"}

# a call these policies refuse is still worth a line in the history (it shows as a failed step); calls refused for any other reason are not
AUDITED_REFUSALS = {"capability", "manager_only"}


def after_tool(ctx, inv, result):
    if not ctx.agent or inv.name in SELF_LOGGED:
        return
    res = result.value
    log_activity(ctx.agent["id"], inv.name, inv.args, res)
    if inv.name in ("call_contact", "schedule_call") and isinstance(res, dict):  # keep the agent attached to calls it places
        cid = res.get("campaign_id") or (db.q1("SELECT campaign_id FROM calls WHERE id=?", (res.get("call_id"),)) or {}).get("campaign_id")
        if cid:
            db.run("UPDATE campaigns SET agent_id=? WHERE id=? AND agent_id IS NULL", (ctx.agent["id"], cid))


def tool_blocked(ctx, inv, decision, result):
    if ctx.agent and inv.name not in SELF_LOGGED and decision.verdict is Verdict.DENY and decision.policy in AUDITED_REFUSALS:
        log_activity(ctx.agent["id"], inv.name, inv.args, result.value)


def install(bus: HookBus) -> None:
    bus.on(AFTER_TOOL, after_tool)
    bus.on(TOOL_BLOCKED, tool_blocked)

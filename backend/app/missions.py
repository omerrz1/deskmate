"""Missions: hand over a lead sheet and written instructions, go away, and one employee owns the whole job.

start_mission turns the hand-over into a running project in one go: it reads the instructions, cleans the lead list (and says what it set aside), writes a charter
(objective, call script, calling hours, follow-up text, what counts as success), opens a project, hires the helpers the job needs, makes the instructions searchable for the
callers, creates the campaign, gives everyone their standing tasks, runs a small PILOT first and then the whole list.

After that the system keeps watch by itself, every half minute, with no model involved for the numbers: it checks the pilot and ramps up (or holds if the pilot shows a
real problem), posts a progress report in the chat on a schedule, raises an alert the moment calls stop starting or start failing (and tells the owner and the admin),
and when the list is finished writes the final report with what was learned. Everything also goes into the project and into one live report document.
The `mission` tool lets the owner (or the admin through them) look at it, pause, resume, change the script or the pace, and close it."""
import json
import re
import time

from . import db, files, llm
from . import sheet_ops as ops
from . import sheet_tools as st

PILOT_MAX = 10          # calls in the pilot
NO_PILOT_UP_TO = 25     # a list this small is simply called
CHECK_EVERY = 30        # seconds between looks at the running missions
ALERT_COOLDOWN = 7200   # the same problem is raised again after this long
MAX_HIRES = 4
ACTIVE = ("running", "blocked", "held", "paused", "waiting")

db.run("CREATE TABLE IF NOT EXISTS missions(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, status TEXT, phase TEXT, project_id INTEGER, owner_id INTEGER, meeting_id INTEGER, "
       "brief_doc_id INTEGER, leads_doc_id INTEGER, work_doc_id INTEGER, dataset_id INTEGER, campaign_id INTEGER, kb_id INTEGER, charter TEXT DEFAULT '{}', team TEXT DEFAULT '[]', "
       "report_doc_id INTEGER, report_every REAL DEFAULT 10800, last_report_at REAL DEFAULT 0, last_stats TEXT DEFAULT '{}', alerts TEXT DEFAULT '{}', pilot_size INTEGER DEFAULT 0, "
       "hold_reason TEXT DEFAULT '', notes TEXT DEFAULT '[]', callable_leads INTEGER DEFAULT 0, created_at REAL, updated_at REAL, finished_at REAL)")

_S = {"type": "string"}
_B = {"type": "boolean"}
TOOLS = [
    {"name": "start_mission", "description": "TAKE OWNERSHIP OF A WHOLE CALLING JOB. Use it when the admin hands you a lead list and instructions and asks you to run, call or handle it (even if they say they will be away): "
     "it reads the instructions (a shared document, or what they pasted in this chat), cleans the lead list and sets aside what cannot be called, writes a charter, opens a project, hires the helpers the job needs, "
     "makes the instructions searchable for the callers, creates the campaign, gives everyone their standing tasks, runs a small pilot, and then calls the rest. After that it watches itself: progress reports in this chat, "
     "alerts when something breaks, and a final report. Do not ask for permission to hire, to clean the list or to start: the admin's request is the permission. Pass confirmed=true when the admin asked you to call this list. "
     "Leave `instructions` and `leads` out to use what the admin shared or pasted in this chat.",
     "parameters": {"type": "object", "properties": {"name": {**_S, "description": "a short name for the project (optional)"}, "instructions": {**_S, "description": "title or id of the instructions document (optional: the chat is used)"},
                                                     "leads": {"type": "array", "items": _S, "description": "title(s) of the lead sheet(s); several are merged into one list (optional: the sheet shared in this chat)"},
                                                     "goal": {**_S, "description": "the goal in your own words, if the admin stated one"}, "confirmed": _B, "max_hires": {"type": "integer", "description": "most helpers to hire (default 2, at most 4)"},
                                                     "start_calling": {**_B, "description": "false = set everything up and do not dial yet"}, "report_every_hours": {"type": "number"},
                                                     "proceed_with_assumptions": {**_B, "description": "true = go ahead on your own assumptions even though the charter has open questions (use when the admin is away)"}}}},
    {"name": "mission", "description": "LOOK AFTER A MISSION you own (or the admin asks about one). action: 'status' (numbers, phase, team, problems, links), 'report' (post a progress report now), 'pause' (stop dialing, nothing is lost), "
     "'resume' (carry on, including after a hold or a problem was fixed), 'ramp' (skip the rest of the pilot and call the whole list), 'adjust' (change the script or the pace: instructions, objective, after_sms, after_sms_when, "
     "max_attempts, concurrency, retry_delay_minutes, call_hours {start,end,days}, report_every_hours; later calls use it at once), 'stop' (end it), 'close' (mark it finished), 'list'. "
     "When an alert task comes to you, read the numbers with 'status', find the cause, fix what you can, and tell the admin plainly what you did or need.",
     "parameters": {"type": "object", "properties": {"action": {**_S, "enum": ["status", "report", "pause", "resume", "ramp", "adjust", "stop", "close", "list"]}, "mission_id": {"type": "integer"},
                                                     "instructions": _S, "objective": _S, "after_sms": _S, "after_sms_when": {**_S, "enum": ["interested", "answered", "never"]}, "max_attempts": {"type": "integer"},
                                                     "concurrency": {"type": "integer"}, "retry_delay_minutes": {"type": "integer"}, "call_hours": {"type": "object"}, "report_every_hours": {"type": "number"},
                                                     "answers": {**_S, "description": "the admin's answers to open questions (with resume)"}}, "required": ["action"]}},
]
TOOL_NAMES = {t["name"] for t in TOOLS}


# ---------- small helpers ----------
def _set(mid, **f):
    for k in ("charter", "team", "last_stats", "alerts", "notes"):
        if k in f and not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    db.update("missions", mid, updated_at=db.now(), **f)


def _get(mid):
    return db.q1("SELECT * FROM missions WHERE id=?", (mid,))


def _say(m, text, project=True):
    """Say it in the chat that handed over the job, and in the project."""
    from . import agents
    if m.get("meeting_id"):
        agents.log_line(m["meeting_id"], "agent", m["owner_id"], text)
    if project and m.get("project_id"):
        db.run("INSERT INTO project_updates(project_id,agent_id,text,ts) VALUES(?,?,?,?)", (m["project_id"], m["owner_id"], text[:1500], db.now()))
    db.log("mission", f"[{m['name']}] {text.splitlines()[0][:160]}")


def _note(m, who, text):
    notes = db.jload(m.get("notes"), [])
    notes.append({"ts": db.now(), "by": who, "note": text[:300]})
    _set(m["id"], notes=notes[-40:])


def _hours(h):
    return f"{h:.0f} hour{'s' if round(h) != 1 else ''}" if h >= 1 else f"{round(h * 60)} minutes"


def _clock(ts):
    from . import schedule
    return schedule.local(ts).strftime("%a %d %b %H:%M")


def _doc_text(d):
    """The text of a document or an uploaded file; a sheet is not instructions."""
    if d["kind"] == "sheet":
        raise ValueError(f"'{d['title']}' is a spreadsheet. Name the instructions document, or paste the instructions in the chat.")
    if d["kind"] == "file" and d.get("file_path"):
        from pathlib import Path

        from . import knowledge
        path = db.data_dir() / "files" / Path(d["file_path"]).name
        return knowledge.extract_text(d["title"], path.read_bytes()) if path.exists() else ""
    return d.get("body") or ""


def _chat_attachments(agent, meeting_id):
    """Documents the admin shared in this chat: [(doc row, as the employee's own copy)], newest first."""
    out = []
    if not meeting_id:
        return out
    for msg in db.q("SELECT attachments FROM meeting_msgs WHERE meeting_id=? AND role='admin' AND attachments<>'[]' ORDER BY id DESC LIMIT 12", (meeting_id,)):
        for att in db.jload(msg["attachments"], []):
            did = (att.get("agent_copies") or {}).get(str(agent["id"])) or att.get("doc_id")
            d = db.q1("SELECT * FROM documents WHERE id=?", (did,)) if did else None
            if d and all(d["id"] != x["id"] for x in out):
                out.append(d)
    return out


def _chat_text(meeting_id):
    if not meeting_id:
        return ""
    rows = db.q("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='admin' ORDER BY id DESC LIMIT 8", (meeting_id,))
    return "\n\n".join(r["text"].strip() for r in reversed(rows) if (r["text"] or "").strip())[-24000:]


def _instructions(agent, a, meeting_id):
    """-> (text, source document or None, title). From the document named, else the one shared in the chat, else what the admin wrote in the chat."""
    doc = None
    if a.get("instructions"):
        ref = a["instructions"]
        doc, err = files.resolve_document(agent, int(ref) if str(ref).isdigit() else None, None if str(ref).isdigit() else str(ref))
        if err:
            raise ValueError(err)
    else:
        docs = [d for d in _chat_attachments(agent, meeting_id) if d["kind"] != "sheet"]
        doc = docs[0] if docs else None
    chat = _chat_text(meeting_id)
    parts = []
    if doc:
        parts.append(_doc_text(doc))
    if chat and (not doc or len(chat) >= 40):
        parts.append(("The admin's messages in the chat:\n" if doc else "") + chat)
    text = "\n\n---\n".join(p for p in parts if p.strip()).strip()
    if len(text) < 80:
        raise ValueError("I need the instructions: paste them in this chat, share a document, or name it with `instructions`.")
    return text[:60000], doc, (doc["title"] if doc else "Instructions from the chat")


def _lead_docs(agent, a, meeting_id):
    refs = a.get("leads")
    if isinstance(refs, (str, int)):
        refs = [refs]
    if refs:
        return [st._find(agent, r) for r in refs[:4]]
    found = [d for d in _chat_attachments(agent, meeting_id) if d["kind"] == "sheet"]
    if not found:
        rows = db.q("SELECT d.* FROM documents d JOIN folders f ON f.id=d.folder_id WHERE d.kind='sheet' AND d.agent_id=? AND f.name LIKE 'Chat %' ORDER BY d.updated_at DESC LIMIT 5", (agent["id"],))
        found = rows
    if len(found) == 1:
        return found
    if not found:
        raise ValueError("I cannot see a lead sheet. Share one in this chat, or name it with `leads`.")
    raise ValueError("More than one sheet was shared: " + ", ".join(f"'{d['title']}'" for d in found[:6]) + ". Name the ones to call with `leads` (several are merged into one list).")


# ---------- the charter ----------
_CHARTER_SYSTEM = (
    "You are the owner of a phone-calling project at a company. The admin has handed you a lead list and written instructions and gone away, so you decide everything sensible yourself. "
    "Read the instructions and write the project's charter as JSON with these keys: "
    "mission_name (3 to 6 words); "
    "objective (what the caller must achieve on each call: imperative, one or two short sentences, simple words); "
    "call_instructions (everything the caller needs, distilled from the instructions: the offer and the facts, the order of the conversation, how to answer common questions and objections, "
    "what to say when they are not interested, what never to say. Plain text under 1800 characters. Never invent a fact that is not in the instructions); "
    "success_means (one sentence: what counts as a successful call); "
    "after_text (a text message to send after the call to people who showed interest, only when the instructions call for sending details; use {name}, {link} and {company} as placeholders; '' if none); "
    "after_text_when (interested | answered | never); "
    "max_attempts (1 to 5: how many times to try each lead); retry_delay_minutes (30 to 1440); "
    "call_hours ({\"start\": \"HH:MM\", \"end\": \"HH:MM\", \"days\": [0..6, 0 = Monday]} if the instructions give calling hours or days, otherwise null); "
    "concurrency (1 to 100: calls at the same time; 10 unless the list is small); report_every_hours (1 to 24, normally 3); "
    "roles (0 to 3 helpers worth hiring for this project, each {\"role\": short job title, \"brief\": one sentence on who they are, \"duties\": 1 to 3 strings of what they do, \"daily\": true if it repeats every day}; "
    "typical: a call quality reviewer who reads finished calls and flags problems, a follow-up specialist who sends the details people asked for and handles replies; add only what this project needs); "
    "assumptions (what you decided where the instructions were silent, up to 8 short strings); "
    "blocking_questions (ONLY things that make it impossible or unsafe to start calling, up to 3, usually none); "
    "risks (up to 4 short strings). Include nothing that was not asked for or clearly implied.")


def _first(text, n):
    return re.sub(r"\s+", " ", text or "").strip()[:n]


def clean_charter(raw, text, name_hint=""):
    """The model's charter, with every field checked and bounded. Defaults stand in for anything missing or silly."""
    raw = raw if isinstance(raw, dict) else {}

    def num(v, lo, hi, default):
        try:
            return max(lo, min(hi, int(float(v))))
        except (TypeError, ValueError):
            return default

    def strs(v, n, size):
        return [_first(str(x), size) for x in (v if isinstance(v, list) else [])[:n] if _first(str(x), size)]
    hours = raw.get("call_hours") if isinstance(raw.get("call_hours"), dict) else None
    window = None
    if hours:
        ok = lambda s: bool(re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(s or "")))  # noqa: E731
        days = [d for d in (hours.get("days") or []) if isinstance(d, int) and 0 <= d <= 6]
        if ok(hours.get("start")) and ok(hours.get("end")):
            window = {"start": hours["start"], "end": hours["end"], "days": days or [0, 1, 2, 3, 4, 5, 6]}
    roles = []
    for r in (raw.get("roles") if isinstance(raw.get("roles"), list) else [])[:3]:
        if isinstance(r, dict) and _first(r.get("role"), 60):
            roles.append({"role": _first(r.get("role"), 60), "brief": _first(r.get("brief"), 300) or _first(r.get("role"), 60), "duties": strs(r.get("duties"), 3, 240), "daily": bool(r.get("daily"))})
    when = raw.get("after_text_when") if raw.get("after_text_when") in ("interested", "answered", "never") else "interested"
    return {"mission_name": _first(raw.get("mission_name"), 60) or name_hint or "Calling project",
            "objective": _first(raw.get("objective"), 300) or _first(text, 200),
            "call_instructions": str(raw.get("call_instructions") or text)[:1800].strip(),
            "success_means": _first(raw.get("success_means"), 240), "after_text": _first(raw.get("after_text"), 600), "after_text_when": when,
            "max_attempts": num(raw.get("max_attempts"), 1, 5, 2), "retry_delay_minutes": num(raw.get("retry_delay_minutes"), 30, 1440, 120),
            "call_hours": window, "concurrency": num(raw.get("concurrency"), 1, 100, 10), "report_every_hours": num(raw.get("report_every_hours"), 1, 24, 3),
            "roles": roles, "assumptions": strs(raw.get("assumptions"), 8, 240), "blocking_questions": strs(raw.get("blocking_questions"), 3, 240), "risks": strs(raw.get("risks"), 4, 200),
            "drafted_by_model": bool(raw)}


async def _draft_charter(agent, text, summary, name_hint):
    team = [f"{r['name']} ({r['role']})" for r in db.q("SELECT name,role FROM agents WHERE archived=0 AND id<>? LIMIT 20", (agent["id"],))]
    try:
        raw = await llm.complete_json(_CHARTER_SYSTEM, json.dumps({"instructions": text[:24000], "the_leads": summary, "company": files.company_excerpt(1500),
                                                                   "existing_team": team}, ensure_ascii=False), model=llm.EMPLOYEE_MODEL)
    except Exception:
        raw = {}
    return clean_charter(raw, text, name_hint)


# ---------- the team ----------
def _words(s):
    return {w for w in re.findall(r"[a-z]{4,}", (s or "").lower())} - {"team", "agent", "assistant", "manager", "specialist"}


def _find_helper(role, owner_id, taken):
    want = _words(role)
    best, score = None, 0
    for ag in db.q("SELECT id,name,role FROM agents WHERE archived=0 AND id<>?", (owner_id,)):
        if ag["id"] in taken:
            continue
        s = len(want & _words(ag["role"]))
        if s > score:
            best, score = ag, s
    return best


async def _team(owner, roles, max_hires):
    """Reuse a colleague whose job already fits each role, hire for the rest (up to max_hires). -> [{agent_id, name, role, hired, duties, daily}]"""
    from . import agents, team
    out, hires, taken = [], 0, {owner["id"]}
    for r in roles:
        ag = _find_helper(r["role"], owner["id"], taken)
        hired = False
        if not ag and hires < max_hires:
            try:
                info = await agents.write_profile(f"{r['role']}: {r['brief']}", "female", "")
            except Exception:
                info = {}
            try:
                nid = agents.create(info.get("name") or r["role"].split()[0], "female", info.get("role") or r["role"], info.get("personality", ""), info.get("persona", ""), bio=info.get("bio", ""))
            except ValueError:
                continue  # the workspace has all the employees it may have
            db.update("agents", nid, manager_id=owner["id"])
            ag, hired = {"id": nid, "name": info.get("name") or r["role"], "role": info.get("role") or r["role"]}, True
            hires += 1
            team.log_event(owner["id"], "manager", "notes", f"Hired {ag['name']} ({ag['role']}) for a mission", {"brief": r["brief"]})
        if not ag:
            continue
        taken.add(ag["id"])
        out.append({"agent_id": ag["id"], "name": ag["name"], "role": ag["role"], "hired": hired, "duties": r["duties"], "daily": r["daily"]})
    return out


def _duty(agent_id, title, detail, m, priority="normal", recur="", due_ts=None, ref=""):
    return db.run("INSERT INTO duties(agent_id,title,detail,due,due_ts,priority,checklist,recur,auto,source,created_by,created_at,project_id,ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (agent_id, title[:200], detail[:2000], time.strftime("%Y-%m-%d %H:%M", time.localtime(due_ts)) if due_ts else "", due_ts, priority, "[]", recur, 1, "agent",
                   (db.q1("SELECT name FROM agents WHERE id=?", (m["owner_id"],)) or {}).get("name", "the owner"), db.now(), m["project_id"], ref or f"mission:{m['id']}"))


def _standing_tasks(m, charter, team):
    """Everyone's standing work: the owner checks in daily, each helper does their part."""
    pid = m["project_id"]
    context = (f"This is part of the mission '{m['name']}' (mission #{m['id']}, project '{(db.q1('SELECT name FROM projects WHERE id=?', (pid,)) or {}).get('name', '')}'). "
               "Use the `mission` tool with action='status' for the live numbers, get_call_report for any call, and post_update on the project for what you did or found. ")
    now = db.now()
    _duty(m["owner_id"], f"Mission check-in: {m['name']}",
          context + "Read the status. Look at a handful of finished calls (get_call_report) and the 'Needs checking' rows. If the script causes problems, fix it with mission action='adjust'. "
          "Answer anything your helpers raised. Post a short update on the project saying what you found and changed, and tell the admin only what needs them.",
          m, recur="daily", due_ts=now + 86400)
    for t in team:
        for i, d in enumerate(t["duties"][:2] or [f"Do your part of the mission as {t['role']}."]):
            _duty(t["agent_id"], f"{t['role']}: {d[:120]}", context + f"Your part: {d} Report to {(db.q1('SELECT name FROM agents WHERE id=?', (m['owner_id'],)) or {}).get('name', 'the owner')} with ask_colleague or post_update when you find something they must act on.",
                  m, recur="daily" if t["daily"] else "", due_ts=now + 4 * 3600 + i * 600)


# ---------- the numbers ----------
_HEARD = "(c.status='completed' AND c.transcript LIKE '%\"role\": \"customer\"%')"
_LAST = "c.id IN (SELECT MAX(id) FROM calls WHERE campaign_id=? GROUP BY contact_id)"


def stats(m, deep=True):
    """The whole picture of a mission from the calls themselves. deep=False skips the parts that read call transcripts."""
    cid, ds = m.get("campaign_id"), m.get("dataset_id")
    leads = db.q1("SELECT COUNT(*) n FROM contacts WHERE dataset_id=?", (ds,))["n"] if ds else 0
    out = {"leads": leads, "callable": m.get("callable_leads") or leads, "by_status": {}, "attempts": 0, "reached": 0, "outcomes": {}, "sentiment": {}, "texts_sent": 0, "retries_waiting": 0,
           "first_calls_waiting": 0, "in_progress": 0, "pace_per_hour": 0, "avg_minutes": 0, "ai_spend_usd": 0, "next_call_at": None}
    if not cid:
        return out
    now = db.now()
    out["by_status"] = {r["status"]: r["n"] for r in db.q(f"SELECT c.status, COUNT(*) n FROM calls c WHERE c.campaign_id=? AND {_LAST} GROUP BY c.status", (cid, cid))}
    out["attempts"] = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND status NOT IN ('queued','cancelled')", (cid,))["n"]
    q = db.q1("SELECT SUM(attempt>1) retries, SUM(attempt=1) first, MIN(not_before) soonest FROM calls WHERE campaign_id=? AND status='queued'", (cid,))
    out["retries_waiting"], out["first_calls_waiting"], out["next_call_at"] = int(q["retries"] or 0), int(q["first"] or 0), q["soonest"]
    out["in_progress"] = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND status IN ('ringing','in_progress')", (cid,))["n"]
    out["pace_per_hour"] = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND ended_at>?", (cid, now - 3600))["n"]
    out["texts_sent"] = db.q1("SELECT COUNT(DISTINCT c.contact_id) n FROM call_actions a JOIN calls c ON c.id=a.call_id WHERE c.campaign_id=? AND a.kind IN ('information_sent','after_sms') AND a.status='done'", (cid,))["n"]
    out["ai_spend_usd"] = round((db.q1("SELECT COALESCE(SUM(usd),0) s FROM llm_usage WHERE ts>=?", (m.get("created_at") or 0,)) or {"s": 0})["s"], 2)
    if deep:
        out["reached"] = db.q1(f"SELECT COUNT(*) n FROM calls c WHERE c.campaign_id=? AND {_LAST} AND {_HEARD}", (cid, cid))["n"]
        out["outcomes"] = {r["outcome"] or "unresolved": r["n"] for r in db.q(f"SELECT c.outcome, COUNT(*) n FROM calls c WHERE c.campaign_id=? AND {_LAST} AND {_HEARD} GROUP BY c.outcome ORDER BY n DESC", (cid, cid))}
        out["sentiment"] = {r["sentiment"] or "unknown": r["n"] for r in db.q(f"SELECT c.sentiment, COUNT(*) n FROM calls c WHERE c.campaign_id=? AND {_LAST} AND {_HEARD} GROUP BY c.sentiment", (cid, cid))}
        avg = (db.q1(f"SELECT AVG(c.duration) d FROM calls c WHERE c.campaign_id=? AND {_LAST} AND {_HEARD}", (cid, cid)) or {"d": 0})["d"] or 0
        out["avg_minutes"] = round(avg / 60, 1)
    return out


def _called(s):
    """Leads whose latest call has finished (answered or not): the share of the list that is done with for now."""
    return sum(n for k, n in s["by_status"].items() if k in ("completed", "no_answer", "declined", "failed"))


def _window_note(m):
    from . import schedule
    camp = db.q1("SELECT * FROM campaigns WHERE id=?", (m["campaign_id"],)) if m.get("campaign_id") else None
    win = schedule.window_of(camp)
    if not win:
        return "", True
    if schedule.in_window(win, db.now()):
        return f"calling hours {win['start']}–{win['end']}", True
    return f"outside calling hours ({win['start']}–{win['end']}); calls resume {_clock(schedule.next_open(win, db.now()))}", False


def render_report(m, s, kind="progress"):
    charter = db.jload(m.get("charter"), {})
    done = _called(s)
    pct = round(100 * done / s["callable"]) if s["callable"] else 0
    title = {"launch": "launched", "progress": "progress report", "final": "FINAL REPORT", "now": "status"}[kind]
    lines = [f"Mission '{m['name']}': {title} ({_clock(db.now())})"]
    phase = {"pilot": "pilot calls first", "run": "full run", "closed": "finished"}.get(m["phase"], m["phase"])
    lines.append(f"State: {m['status']} · {phase} · running {_hours((db.now() - (m.get('created_at') or db.now())) / 3600)}")
    lines.append(f"Leads: {s['callable']} callable of {s['leads']}" + (f" · {done} done with for now ({pct}%)" if kind != "launch" else ""))
    if kind != "launch" or s["attempts"]:
        bs = s["by_status"]
        lines.append(f"Calls: {s['attempts']} made · conversations {s['reached']} · no answer {bs.get('no_answer', 0)} · declined {bs.get('declined', 0)} · failed {bs.get('failed', 0)} · "
                     f"on now {s['in_progress']} · waiting {s['first_calls_waiting']} first calls and {s['retries_waiting']} retries")
    if s["outcomes"]:
        lines.append("Outcomes: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in s["outcomes"].items()))
    if s["texts_sent"]:
        lines.append(f"Texts sent: {s['texts_sent']}")
    note, open_now = _window_note(m)
    waiting = s["first_calls_waiting"] + s["retries_waiting"]
    if m["status"] == "running" and waiting:
        if not open_now:
            lines.append("Pace: " + note)
        elif s["pace_per_hour"] >= 5:
            eta = waiting / s["pace_per_hour"]
            lines.append(f"Pace: about {s['pace_per_hour']} calls an hour, so roughly {_hours(eta)} to go" + (f" ({note})" if note else ""))
        elif s["next_call_at"] and s["next_call_at"] > db.now() + 120:
            lines.append(f"Next calls due {_clock(s['next_call_at'])}")
    lines.append(f"AI spend since the start: ${s['ai_spend_usd']}")
    open_alerts = [k for k, v in db.jload(m.get("alerts"), {}).items() if v.get("open")]
    if m.get("hold_reason"):
        lines.append("ON HOLD: " + m["hold_reason"])
    lines.append("Problems: " + ("; ".join(open_alerts) if open_alerts else "none right now"))
    team = db.jload(m.get("team"), [])
    owner = (db.q1("SELECT name FROM agents WHERE id=?", (m["owner_id"],)) or {}).get("name", "the owner")
    lines.append("Team: " + ", ".join([f"{owner} (owner)"] + [f"{t['name']} ({t['role']})" for t in team]))
    if kind in ("launch", "final") and m.get("campaign_id"):
        lines.append(f"Results workbook (one row per lead): /api/campaigns/{m['campaign_id']}/export.xlsx")
    if kind == "launch":
        lines.append("Next report in " + _hours(m["report_every"] / 3600) + ". Ask me any time for the status; I will say straight away if something needs you.")
    return "\n".join(lines)


def _write_report_doc(m, text):
    """One live document with the newest report on top."""
    if not m.get("project_id"):
        return
    stamp = f"## {_clock(db.now())}\n{text}\n\n"
    if m.get("report_doc_id") and db.q1("SELECT id FROM documents WHERE id=?", (m["report_doc_id"],)):
        body = (stamp + (db.q1("SELECT body FROM documents WHERE id=?", (m["report_doc_id"],))["body"] or ""))[:60000]
        db.update("documents", m["report_doc_id"], body=body, updated_at=db.now())
        return
    did = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                 (-m["project_id"], f"Mission {m['id']} — {m['name']} — Live report", "doc", stamp, files.folder_id(-m["project_id"], "Reports"), db.now(), db.now()))
    _set(m["id"], report_doc_id=did)


def _report(m, kind="progress"):
    m = _get(m["id"])
    s = stats(m)
    text = render_report(m, s, kind)
    if kind == "final":
        text += "\n" + _insights_text(m)
    _say(m, text)
    _write_report_doc(m, text)
    _set(m["id"], last_report_at=db.now(), last_stats=s)
    return text


# ---------- watching, alerts ----------
def _raise(m, key, title, body, owner_duty=True):
    """A problem. Said once in the chat, sent to the admin's alert channels, and handed to the owner as an urgent task; not repeated for a couple of hours."""
    alerts = db.jload(m.get("alerts"), {})
    prev = alerts.get(key) or {}
    if prev.get("open") and db.now() - prev.get("at", 0) < ALERT_COOLDOWN:
        return False
    alerts[key] = {"open": True, "at": db.now(), "title": title}
    _set(m["id"], alerts=alerts)
    _say(m, f"PROBLEM on mission '{m['name']}': {title}\n{body}")
    from . import alerts as admin_alerts
    admin_alerts.send("failure", f"Mission '{m['name']}': {title}", body[:500])
    if owner_duty and not db.q1("SELECT 1 x FROM duties WHERE ref=? AND status IN ('todo','doing')", (f"mission:{m['id']}:{key}",)):
        _duty(m["owner_id"], f"URGENT mission problem: {title}"[:200],
              f"Mission '{m['name']}' (#{m['id']}) has a problem: {title}. {body} Use mission action='status', find the cause, fix what you can (settings, script, pace) and use mission action='resume' when it is safe. "
              "Then post what you found and did on the project and tell the admin in the chat.", m, priority="high", ref=f"mission:{m['id']}:{key}")
    return True


def _clear(m, key, text):
    alerts = db.jload(m.get("alerts"), {})
    if (alerts.get(key) or {}).get("open"):
        alerts[key]["open"] = False
        _set(m["id"], alerts=alerts)
        _say(m, f"Resolved on mission '{m['name']}': {text}")


def _watch(m):
    """The checks that run all the time, with no model: are calls starting, are they failing."""
    from . import callengine, schedule
    now, cid = db.now(), m["campaign_id"]
    camp = db.q1("SELECT * FROM campaigns WHERE id=?", (cid,))
    if not camp or camp["status"] != "active":
        return
    due = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND status='queued' AND not_before<=?", (cid, now))["n"]
    recent = db.q1("SELECT MAX(COALESCE(ended_at, answered_at, ringing_at)) t FROM calls WHERE campaign_id=?", (cid,))["t"] or 0
    busy = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND status IN ('ringing','in_progress')", (cid,))["n"]
    win_ok = schedule.in_window(schedule.window_of(camp), now)
    if due and win_ok and not busy and now - max(recent, camp.get("started_at") or 0) > 900:
        why = callengine._outbound_problem()
        _raise(m, "calls are not starting", "calls are due but none has started for 15 minutes",
               f"{due} calls are due now and nothing is ringing. " + (f"Cause found: {why}" if why else "Check Twilio (Apps → Twilio), the concurrency limit and that work is not paused in Settings."))
    else:
        _clear(m, "calls are not starting", "calls are starting again.")
    finished = db.q("SELECT status, summary FROM calls WHERE campaign_id=? AND ended_at>?", (cid, now - 1800))
    failed = [c for c in finished if c["status"] == "failed"]
    if len(finished) >= 10 and len(failed) / len(finished) >= 0.4:
        reasons = {}
        for c in failed:
            reasons[(c["summary"] or "no reason given")[:120]] = reasons.get((c["summary"] or "no reason given")[:120], 0) + 1
        top = "; ".join(f"{n}× {r}" for r, n in sorted(reasons.items(), key=lambda t: -t[1])[:3])
        _raise(m, "calls are failing", f"{len(failed)} of the last {len(finished)} calls failed", f"Most common reasons: {top}. If it is the phone line or the account, calls will keep failing until that is fixed.")
    elif len(finished) >= 10:
        _clear(m, "calls are failing", "calls are connecting normally again.")


async def _pilot_gate(m):
    """Once the pilot's first calls are done: ramp up to the whole list, or hold if the pilot shows a real problem."""
    cid = m["campaign_id"]
    waiting = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND attempt=1 AND status IN ('queued','ringing','in_progress')", (cid,))["n"]
    if waiting:
        return
    first = db.q("SELECT status, summary FROM calls WHERE campaign_id=? AND attempt=1", (cid,))
    failed = [c for c in first if c["status"] == "failed"]
    if len(first) >= 3 and len(failed) / len(first) >= 0.5:
        reasons = "; ".join(sorted({(c["summary"] or "no reason given")[:100] for c in failed})[:3])
        return _hold(m, f"{len(failed)} of the {len(first)} pilot calls failed ({reasons}). That points at the phone line or account, not at the script.")
    heard = db.q(f"SELECT c.transcript, c.summary, c.outcome FROM calls c WHERE c.campaign_id=? AND c.attempt=1 AND {_HEARD} LIMIT 6", (cid,))
    verdict, issues, good = "ok", [], []
    if len(heard) >= 3:
        charter = db.jload(m.get("charter"), {})
        sample = [{"summary": (h["summary"] or "")[:300], "outcome": h["outcome"], "transcript": " | ".join(f"{t.get('role')}: {t.get('text', '')[:200]}" for t in db.jload(h["transcript"], [])[:14])[:1500]} for h in heard]
        try:
            r = await llm.complete_json(
                "You review phone calls made by an AI caller. Its task: " + (charter.get("objective") or "") + ". Its instructions: " + (charter.get("call_instructions") or "")[:1500] +
                " Read the calls. Reply as JSON: verdict (ok | issues | severe), issues (up to 6 short strings), good (up to 3 short strings). "
                "Use 'severe' ONLY if the caller states facts or links that are not in its instructions, ignores its task in most calls, or is rude, pushy or misleading. Minor style problems are 'issues'.",
                json.dumps(sample, ensure_ascii=False), model=llm.EMPLOYEE_MODEL)
            verdict = r.get("verdict") if r.get("verdict") in ("ok", "issues", "severe") else "ok"
            issues, good = [str(x)[:200] for x in (r.get("issues") or [])[:6]], [str(x)[:200] for x in (r.get("good") or [])[:3]]
        except Exception:
            pass
    if verdict == "severe":
        return _hold(m, "the pilot calls had serious problems: " + "; ".join(issues or ["see the Calls page"]) + ". Fix the script with mission action='adjust', then action='resume'.")
    await _ramp(m, f"The pilot looks healthy: {len(first)} calls, {len(heard)} conversations"
                + (f". Things to improve: {'; '.join(issues)}" if issues else "") + (f". Working well: {'; '.join(good)}" if good else "") + ".")


def _hold(m, reason):
    _set(m["id"], status="held", hold_reason=reason[:600])
    _raise(m, "pilot held", "the full run is on hold", reason)


async def _ramp(m, said=""):
    from . import callengine
    try:
        q = callengine.queue_campaign(m["campaign_id"], None, details=True)
    except ValueError as e:
        _set(m["id"], status="blocked", hold_reason=str(e))
        _raise(m, "cannot dial", "calling is not possible right now", str(e))
        return False
    _set(m["id"], status="running", phase="run", hold_reason="")
    _clear(m, "pilot held", "the hold is lifted.")
    _say(m, f"{said} Calling the rest of the list now: {q['queued']} more calls queued." if said else f"Calling the rest of the list now: {q['queued']} more calls queued.")
    return True


async def _launch(m):
    """Queue the pilot (or the whole small list). Blocked, with the exact reason, when calling is not possible yet."""
    from . import callengine
    n = m["callable_leads"]
    pilot = 0 if n <= NO_PILOT_UP_TO else min(PILOT_MAX, max(3, n // 50))
    try:
        q = callengine.queue_campaign(m["campaign_id"], pilot or None, details=True)
    except ValueError as e:
        _set(m["id"], status="blocked", phase="pilot" if pilot else "run", pilot_size=pilot, hold_reason=str(e))
        return {"launched": False, "reason": str(e)}
    _set(m["id"], status="running", phase="pilot" if pilot else "run", pilot_size=pilot, hold_reason="")
    return {"launched": True, "queued": q["queued"], "invalid_phone": q["invalid_phone"], "pilot": pilot}


def _insights_text(m):
    ins = db.jload(m.get("charter"), {}).get("insights")
    if not ins:
        return ""
    out = []
    for k, label in (("top_objections", "What put people off"), ("common_questions", "What people asked"), ("what_worked", "What worked"), ("recommendations", "What I would change next time")):
        if ins.get(k):
            out.append(f"{label}: " + "; ".join(ins[k]))
    return "\n".join(out)


async def _insights(m):
    cid = m["campaign_id"]
    rows = db.q(f"SELECT c.outcome, c.summary, c.report FROM calls c WHERE c.campaign_id=? AND {_LAST} AND {_HEARD} ORDER BY c.id DESC LIMIT 100", (cid, cid))
    digest = []
    for r in rows:
        rep = db.jload(r["report"], {}) or {}
        digest.append({"outcome": r["outcome"], "objections": (rep.get("objections_or_concerns") or [])[:3], "unanswered": (rep.get("unanswered_questions") or [])[:2],
                       "said": str(rep.get("objective_answer") or r["summary"] or "")[:160]})
    if not digest:
        return {}
    try:
        r = await llm.complete_json("From these call results of one calling project write JSON: top_objections (up to 5 strings, with how common), common_questions (up to 5), what_worked (up to 4), "
                                    "recommendations (up to 5 concrete changes to the instructions or approach for the next run). Only use what the data shows.",
                                    json.dumps(digest, ensure_ascii=False), model=llm.EMPLOYEE_MODEL)
        return {k: [str(x)[:200] for x in (r.get(k) or [])[:5]] for k in ("top_objections", "common_questions", "what_worked", "recommendations")}
    except Exception:
        return {}


async def _finish(m, why="The whole list has been called."):
    ins = await _insights(m)
    charter = db.jload(m.get("charter"), {})
    if ins:
        charter["insights"] = ins
    _set(m["id"], charter=charter)
    m = _get(m["id"])
    _set(m["id"], status="done", phase="closed", finished_at=db.now())
    m = _get(m["id"])
    _report(m, "final")
    if m.get("project_id"):
        db.update("projects", m["project_id"], status="done")
    _duty(m["owner_id"], f"Final review: {m['name']}"[:200],
          f"Mission '{m['name']}' (#{m['id']}) is finished: {why} Read the final report (mission action='status'), check the results against what the instructions asked, write your own short conclusion with the follow-ups still open "
          "(callbacks, people who asked for something), post it on the project and tell the admin.", m, priority="high", ref=f"mission:{m['id']}:final")


async def _check(m):
    from . import callengine
    now = db.now()
    if m["status"] == "paused":
        return
    if m["status"] == "blocked":
        if not callengine._outbound_problem():  # the admin fixed it: carry on by itself
            r = await _launch(m)
            if r.get("launched"):
                _clear(m, "cannot dial", "calling works now.")
                _say(m, f"Calling works now, so the mission has started: {r['queued']} calls queued" + (f" (a pilot of {r['pilot']} first)." if r["pilot"] else "."))
        return
    if m["status"] == "held":
        return
    camp = db.q1("SELECT status FROM campaigns WHERE id=?", (m["campaign_id"],))
    if camp and camp["status"] == "stopped":
        _set(m["id"], status="paused")
        _say(m, f"The campaign for '{m['name']}' was stopped by someone, so the mission is paused. Say 'resume' to carry on.")
        return
    _watch(m)
    if m["phase"] == "pilot":
        await _pilot_gate(_get(m["id"]))
        return
    if camp and camp["status"] == "completed":
        open_ = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND status IN ('queued','ringing','in_progress')", (m["campaign_id"],))["n"]
        if not open_:
            await _finish(m)
            return
    if now - (m.get("last_report_at") or 0) >= (m.get("report_every") or 10800):
        _report(m)


async def tick():
    """Called every few seconds with the scheduler; looks at the running missions about every half minute."""
    now = time.time()
    key = db.current()
    if now - _last.get(key, 0) < CHECK_EVERY:
        return
    _last[key] = now
    for m in db.q("SELECT * FROM missions WHERE status IN ('running','blocked','held')"):
        try:
            await _check(m)
        except Exception as e:
            db.log("error", f"Mission '{m['name']}' check failed: {type(e).__name__}: {str(e)[:120]}")


_last = {}


# ---------- start ----------
async def start(agent, a):
    from . import callengine, crm, importer, knowledge
    if not agent:
        raise ValueError("A mission needs an employee to own it.")
    meeting_id = agent.get("_meeting_id")
    text, source_doc, doc_title = _instructions(agent, a, meeting_id)
    leads = _lead_docs(agent, a, meeting_id)
    existing = db.q1(f"SELECT * FROM missions WHERE leads_doc_id=? AND status IN ({','.join('?' * len(ACTIVE))})", (leads[0]["id"], *ACTIVE))
    if existing:
        return {"already_running": True, **_status(existing)}
    code = st._code()
    # --- the leads: one list, tidied
    cols, rows = st._table(leads[0])
    for other in leads[1:]:
        oc, orows = st._table(other)
        cols, rows, _ = ops.merge(cols, rows, oc, orows, leads[0]["title"][:30], other["title"][:30], "stack")
    original_rows = len(rows)
    profile = ops.profile(cols, rows, code)
    suggested = [{k: v for k, v in s.items() if k != "why"} for s in profile["suggested_operations"]]
    cleaner = ops.Cleaner(cols, rows, code, "Needs checking").run(suggested)
    ncols, nrows, clean_report = cleaner.result()
    header = [str(c) for c in ncols]
    mapping = importer.guess_mapping(header)
    if "phone" not in mapping:
        raise ValueError(f"I could not tell which column holds the phone number. The columns are: {', '.join(header)}. Rename one 'Phone' (sheet_clean rename) and start again.")
    pi = header.index(mapping["phone"])
    callable_n = sum(1 for r in nrows if ops.E164_RE.match(ops.s(r[pi]).strip()))
    if not callable_n:
        samples = [ops.s(r[pi]) for r in nrows[:3]]
        why = next((w for w in (ops.norm_phone(x, code)[1] for x in samples) if w), "")
        raise ValueError(f"None of the {len(nrows)} rows has a phone number I can call (column '{mapping['phone']}'). Examples: {', '.join(samples)}." + (f" Why: {why}." if why else ""))
    if callable_n > NO_PILOT_UP_TO and not a.get("confirmed"):
        return {"needs_confirmation": True, "callable_leads": callable_n, "rows": len(nrows), "message":
                f"This would call up to {callable_n} people. The admin asked you to run it? Then call start_mission again with confirmed=true. If you are not sure they meant to call them, ask first."}
    summary = {"rows": original_rows, "callable": callable_n, "columns": header[:15], "problems_found": [p for c in profile["columns"] for p in c["problems"]][:6], "languages": _languages(nrows, header)}
    charter = await _draft_charter(agent, text, summary, a.get("name") or "")
    if charter["blocking_questions"] and not a.get("proceed_with_assumptions"):
        return {"needs_answers": charter["blocking_questions"], "next": "Ask the admin these. If they are away, decide yourself and call start_mission again with proceed_with_assumptions=true."}
    name = _first(a.get("name") or charter["mission_name"] or doc_title, 60)
    max_hires = max(0, min(MAX_HIRES, int(a.get("max_hires") if a.get("max_hires") is not None else 2)))
    every = float(a.get("report_every_hours") or charter["report_every_hours"])
    now = db.now()
    mid = db.run("INSERT INTO missions(name,status,phase,owner_id,meeting_id,leads_doc_id,charter,report_every,callable_leads,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (name, "setup", "setup", agent["id"], meeting_id, leads[0]["id"], json.dumps(charter, ensure_ascii=False), max(900, every * 3600), callable_n, now, now))
    step = "the project"
    try:
        goal = _first(a.get("goal") or charter["objective"], 300)
        pid = db.run("INSERT INTO projects(name,goal,created_by,created_at) VALUES(?,?,?,?)", (f"Mission: {name}"[:80], goal, agent["id"], now))
        db.run("INSERT INTO project_members(project_id,agent_id,role,joined_at) VALUES(?,?,'owner',?)", (pid, agent["id"], now))
        brief_id = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                          (-pid, f"Mission {mid} — {name} — Instructions", "doc", text, files.folder_id(-pid, "Briefs"), now, now))
        db.run("INSERT OR REPLACE INTO project_briefs(source_doc_id,project_id,brief_doc_id,original_title,created_at) VALUES(?,?,?,?,?)", ((source_doc or {}).get("id") or brief_id, pid, brief_id, doc_title, now))
        charter_doc = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                             (-pid, f"Mission {mid} — {name} — Charter", "doc", _charter_text(charter, summary), files.folder_id(-pid, "Plans"), now, now))
        _set(mid, project_id=pid, brief_doc_id=brief_id)
        step = "the lead list"
        work = st._save_new(agent, {"project": f"Mission: {name}"[:80]}, f"{name} — leads", ncols, nrows)
        res = importer.import_rows(f"{name} — leads", f"{name} — leads", header, [dict(zip(header, [ops.s(x) for x in r])) for r in nrows], mapping, source_doc_id=work["id"])
        _set(mid, work_doc_id=work["id"], dataset_id=res["dataset_id"])
        step = "the knowledge for the callers"
        kb = db.run("INSERT INTO knowledge_bases(name,context,created_at) VALUES(?,?,?)", (f"{name} — instructions", "The facts, rules and answers for this campaign are the admin's instructions in the sections below. Use lookup_info for anything you are asked.", now))
        try:
            await knowledge.ingest(kb, "Instructions", text, source=f"Mission {mid} instructions")
        except Exception:
            knowledge.add_doc(kb, "Instructions", text[:8000], "info", "general", f"Mission {mid} instructions")
        _set(mid, kb_id=kb)
        step = "the campaign"
        cid = db.run("INSERT INTO campaigns(name,objective,instructions,filters,status,max_attempts,kb_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (f"Mission: {name}"[:80], charter["objective"], charter["call_instructions"], json.dumps({"dataset_id": res["dataset_id"]}), "draft", charter["max_attempts"], kb, now))
        db.update("campaigns", cid, concurrency=charter["concurrency"], retry_delay_min=charter["retry_delay_minutes"], agent_id=agent["id"],
                  call_window=json.dumps(charter["call_hours"] or {"start": "09:00", "end": "20:00", "days": [0, 1, 2, 3, 4, 5, 6]}))
        if charter["after_text"] and charter["after_text_when"] != "never":
            db.update("campaigns", cid, after_sms=charter["after_text"], after_sms_when=charter["after_text_when"])
        _set(mid, campaign_id=cid)
        step = "the team"
        team = await _team(agent, charter["roles"], max_hires)
        for t in team:
            db.run("INSERT OR IGNORE INTO project_members(project_id,agent_id,role,joined_at) VALUES(?,?,'member',?)", (pid, t["agent_id"], now))
        _set(mid, team=team)
        m = _get(mid)
        _standing_tasks(m, charter, team)
        _write_report_doc(m, "Mission set up. The charter is in the project's Plans folder.")
        step = "starting"
        launched = await _launch(m) if a.get("start_calling", True) else {"launched": False, "reason": "start_calling was false"}
        if not launched["launched"] and a.get("start_calling", True) is False:
            _set(mid, status="paused", hold_reason="Set up, not started yet. Use mission action='resume' to start dialing.")
    except Exception as e:
        _set(mid, status="failed", hold_reason=f"Setup failed at {step}: {type(e).__name__}: {str(e)[:200]}")
        raise ValueError(f"Setting up the mission failed at {step} ({type(e).__name__}: {str(e)[:200]}). What was created is kept in the project; nothing was dialed.")
    m = _get(mid)
    _set(mid, last_report_at=db.now())
    text_out = render_report(_get(mid), stats(_get(mid)), "launch")
    lines = [text_out, "", f"Objective: {charter['objective']}", f"Success means: {charter['success_means'] or 'not stated, so every conversation is judged against the objective'}"]
    from . import schedule
    where = (str(schedule.tz()) if schedule.tz() else "the server's clock (UTC): set the company time zone in Settings → Company so calling hours mean your local time")
    if charter["call_hours"]:
        lines.append(f"Calling hours: {charter['call_hours']['start']}–{charter['call_hours']['end']} ({where})")
    else:
        lines.append(f"Calling hours: 09:00–20:00, every day ({where})")
    lines.append(f"Each lead is tried up to {charter['max_attempts']} times, {charter['retry_delay_minutes']} minutes apart.")
    lines.append(f"Lead list: {original_rows} rows → {callable_n} callable. " + _clean_sentence(clean_report))
    if m["pilot_size"]:
        lines.append(f"Pilot: {m['pilot_size']} calls first. If they look healthy I call the rest by myself; if they show a real problem I hold and tell you.")
    if charter["assumptions"]:
        lines.append("Assumptions I made: " + "; ".join(charter["assumptions"]))
    if charter["risks"]:
        lines.append("Risks I am watching: " + "; ".join(charter["risks"]))
    if not launched["launched"]:
        lines.append("NOT DIALING YET: " + (launched.get("reason") or "") + (" It starts by itself as soon as that is fixed." if m["status"] == "blocked" else ""))
        if m["status"] == "blocked":
            _raise(m, "cannot dial", "calling is not possible right now", launched.get("reason") or "", owner_duty=False)
    lines.append(f"Project: Mission: {name}. Live report document: 'Mission {mid} — {name} — Live report'. Charter: document #{charter_doc}.")
    final = "\n".join(lines)
    _say(_get(mid), final)
    _write_report_doc(_get(mid), final)
    return {"mission_id": mid, "project_id": pid, "campaign_id": cid, "status": _get(mid)["status"], "callable_leads": callable_n, "pilot": m["pilot_size"],
            "team": [{"name": t["name"], "role": t["role"], "hired": t["hired"]} for t in team], "launched": launched,
            "next": "The launch report is posted in the chat. Tell the admin in one or two sentences what is running; the system posts progress by itself and raises problems. Do not wait for anything."}


def _languages(rows, header):
    li = next((i for i, h in enumerate(header) if ops.header_role(h) == "" and ops.fold(h) in ("language", "lang")), None)
    if li is None:
        return []
    from collections import Counter
    return [k for k, _n in Counter(ops.clean_text(r[li]) for r in rows if ops.clean_text(r[li])).most_common(4)]


def _clean_sentence(report):
    done = [o for o in report["operations"] if o.get("changed") or o.get("rows_removed")]
    bits = []
    for o in done[:6]:
        label = {"dedupe": "duplicates merged", "remove_empty_rows": "empty rows removed", "phone": "phone numbers fixed", "email": "emails tidied", "date": "dates fixed", "trim": "cells tidied"}.get(o["op"].split(" ")[0], o["op"])
        bits.append(f"{o.get('rows_removed') or o.get('changed')} {label}")
    flagged = report.get("rows_flagged_for_checking", 0)
    return ("Tidied: " + ", ".join(bits) + ". " if bits else "") + (f"{flagged} row(s) have a problem and are in the 'Needs checking' column; they are set aside, not dropped." if flagged else "")


def _charter_text(c, summary):
    out = [f"# Charter: {c['mission_name']}", "", f"Objective: {c['objective']}", f"Success means: {c['success_means']}", "", "## What the callers are told", c["call_instructions"], "",
           f"## Plan\n- Lead list: {summary['rows']} rows, {summary['callable']} callable\n- Tries per lead: {c['max_attempts']} ({c['retry_delay_minutes']} minutes apart)\n- Calls at once: {c['concurrency']}\n"
           f"- Follow-up text: {c['after_text'] or 'none'} ({c['after_text_when']})\n- Reports every {c['report_every_hours']} hours"]
    if c["roles"]:
        out.append("- Team: " + "; ".join(f"{r['role']} ({', '.join(r['duties'])})" for r in c["roles"]))
    if c["assumptions"]:
        out += ["", "## Assumptions"] + [f"- {x}" for x in c["assumptions"]]
    if c["risks"]:
        out += ["", "## Risks"] + [f"- {x}" for x in c["risks"]]
    return "\n".join(out)


# ---------- the mission tool ----------
def _status(m):
    s = stats(m)
    team = db.jload(m.get("team"), [])
    return {"mission_id": m["id"], "name": m["name"], "status": m["status"], "phase": m["phase"], "hold_reason": m.get("hold_reason") or "", "project_id": m["project_id"], "campaign_id": m["campaign_id"],
            "owner": (db.q1("SELECT name FROM agents WHERE id=?", (m["owner_id"],)) or {}).get("name"), "team": [{"name": t["name"], "role": t["role"]} for t in team], "numbers": s,
            "open_problems": [k for k, v in db.jload(m.get("alerts"), {}).items() if v.get("open")], "report": render_report(m, s, "now"),
            "results_workbook": f"/api/campaigns/{m['campaign_id']}/export.xlsx" if m.get("campaign_id") else "", "notes": db.jload(m.get("notes"), [])[-5:]}


def _pick(agent, a):
    if a.get("mission_id"):
        m = _get(int(a["mission_id"]))
        if not m:
            raise ValueError("There is no mission with that number. Use action='list'.")
        return m
    rows = db.q(f"SELECT * FROM missions WHERE status IN ({','.join('?' * len(ACTIVE))}) ORDER BY id DESC", ACTIVE) or db.q("SELECT * FROM missions ORDER BY id DESC LIMIT 1")
    mine = [r for r in rows if not agent or r["owner_id"] == agent["id"]]
    pick = (mine or rows or [None])[0]
    if not pick:
        raise ValueError("There is no mission yet. Start one with start_mission.")
    return pick


def _allowed(agent, m):
    if agent and agent["id"] != m["owner_id"] and not agent.get("is_manager"):
        owner = (db.q1("SELECT name FROM agents WHERE id=?", (m["owner_id"],)) or {}).get("name", "the owner")
        raise ValueError(f"{owner} owns this mission. Ask {owner} (ask_colleague) to change it; you can read the status.")


async def control(agent, a):
    action = str(a.get("action") or "").lower()
    if action == "list":
        return {"missions": [{"mission_id": r["id"], "name": r["name"], "status": r["status"], "phase": r["phase"], "owner": (db.q1("SELECT name FROM agents WHERE id=?", (r["owner_id"],)) or {}).get("name")}
                             for r in db.q("SELECT * FROM missions ORDER BY id DESC LIMIT 15")]}
    m = _pick(agent, a)
    who = (agent or {}).get("name", "the admin")
    if action == "status":
        return _status(m)
    _allowed(agent, m)
    cid = m["campaign_id"]
    if action == "report":
        _report(m, "progress")
        return {"posted": True}
    if action == "pause":
        db.update("campaigns", cid, status="stopped")  # the scheduler skips a stopped campaign; its queued calls wait, none is lost
        _set(m["id"], status="paused", hold_reason="Paused by " + who)
        _note(m, who, "paused")
        _say(_get(m["id"]), f"Mission '{m['name']}' is paused by {who}. Nothing more will be dialed until it is resumed; calls already ringing finish.")
        return {"paused": True}
    if action in ("resume", "ramp"):
        if a.get("answers"):
            charter = db.jload(m.get("charter"), {})
            charter["answers"] = (charter.get("answers", "") + " " + a["answers"]).strip()[:1500]
            db.update("campaigns", cid, instructions=(db.q1("SELECT instructions FROM campaigns WHERE id=?", (cid,))["instructions"] + "\nFrom the admin: " + a["answers"])[:3500])
            _set(m["id"], charter=charter)
        _note(m, who, action + (" with answers" if a.get("answers") else ""))
        camp = db.q1("SELECT status FROM campaigns WHERE id=?", (cid,))
        waiting = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND status='queued'", (cid,))["n"]
        if action == "resume" and camp and camp["status"] == "stopped" and waiting:
            db.update("campaigns", cid, status="active")
            _set(m["id"], status="running", hold_reason="")
            _say(_get(m["id"]), f"Mission '{m['name']}' resumed by {who}: {waiting} calls were waiting and are being dialed again.")
            return {"resumed": True, "waiting_calls": waiting}
        if action == "resume" and m["phase"] == "pilot" and m["status"] in ("blocked", "paused") and not db.q1("SELECT 1 x FROM calls WHERE campaign_id=? AND status<>'cancelled'", (cid,)):
            r = await _launch(m)
            if not r["launched"]:
                return {"error": r["reason"]}
            _say(_get(m["id"]), f"Mission '{m['name']}' started by {who}: {r['queued']} calls queued" + (f" (a pilot of {r['pilot']} first)." if r["pilot"] else "."))
            return {"started": True, **r}
        ok = await _ramp(m)
        if not ok:
            return {"error": _get(m["id"])["hold_reason"]}
        return {"resumed": True, "status": _get(m["id"])["status"]}
    if action == "adjust":
        changes, camp = [], {}
        if a.get("instructions"):
            camp["instructions"] = str(a["instructions"])[:3500]; changes.append("call instructions")
        if a.get("objective"):
            camp["objective"] = _first(a["objective"], 300); changes.append("objective")
        if a.get("after_sms") is not None:
            camp["after_sms"] = _first(a["after_sms"], 600); changes.append("follow-up text")
        if a.get("after_sms_when") in ("interested", "answered", "never"):
            camp["after_sms_when"] = a["after_sms_when"]; changes.append("who gets the text")
        if a.get("max_attempts"):
            camp["max_attempts"] = max(1, min(5, int(a["max_attempts"]))); changes.append("tries per lead")
        if a.get("concurrency"):
            camp["concurrency"] = max(1, min(100, int(a["concurrency"]))); changes.append("calls at once")
        if a.get("retry_delay_minutes"):
            camp["retry_delay_min"] = max(30, min(1440, int(a["retry_delay_minutes"]))); changes.append("retry gap")
        hours = a.get("call_hours")
        if isinstance(hours, dict) and re.fullmatch(r"\d{1,2}:\d\d", str(hours.get("start", ""))) and re.fullmatch(r"\d{1,2}:\d\d", str(hours.get("end", ""))):
            camp["call_window"] = json.dumps({"start": hours["start"], "end": hours["end"], "days": [d for d in (hours.get("days") or [0, 1, 2, 3, 4, 5, 6]) if isinstance(d, int) and 0 <= d <= 6]}); changes.append("calling hours")
        if camp:
            db.update("campaigns", cid, **camp)
        if a.get("report_every_hours"):
            _set(m["id"], report_every=max(900, float(a["report_every_hours"]) * 3600)); changes.append("report schedule")
        if not changes:
            raise ValueError("Say what to change: instructions, objective, after_sms, after_sms_when, max_attempts, concurrency, retry_delay_minutes, call_hours or report_every_hours.")
        _note(m, who, "changed " + ", ".join(changes))
        _say(_get(m["id"]), f"Mission '{m['name']}': {who} changed {', '.join(changes)}. Calls that start from now on use it.")
        return {"changed": changes}
    if action in ("stop", "close"):
        db.run("UPDATE calls SET status='cancelled' WHERE campaign_id=? AND status='queued'", (cid,))
        db.update("campaigns", cid, status="stopped" if action == "stop" else "completed")
        _set(m["id"], status="stopped" if action == "stop" else "done", phase="closed", finished_at=db.now())
        _note(m, who, action)
        if action == "close" and m.get("project_id"):
            db.update("projects", m["project_id"], status="done")
        _report(_get(m["id"]), "final")
        return {"stopped": True} if action == "stop" else {"closed": True}
    raise ValueError("action must be status, report, pause, resume, ramp, adjust, stop, close or list.")


async def run_tool(agent, name, a):
    try:
        if name == "start_mission":
            return await start(agent, a), "/projects"
        if name == "mission":
            return await control(agent, a), "/projects"
    except ValueError as e:
        return {"error": str(e)}, None
    return {"error": f"unknown tool {name}"}, None

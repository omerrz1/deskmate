"""HTTP API for the simplified admin app: agent pages (settings, history, capabilities, training), projects, meeting requests, connectors."""
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import access, agents, app_requests, capabilities, chats, connectors, db, discuss, files, live_help, llm, office, people, schedule, team, training, voices

router = APIRouter(prefix="/api")


def _agent(aid):
    a = agents.get(aid)
    if not a:
        raise HTTPException(404, "agent not found")
    return a


# ---------- agent details ----------
@router.get("/agents/{aid}/overview")
def agent_overview(aid: int):
    a = dict(_agent(aid))
    boss = db.q1("SELECT id,name FROM agents WHERE id=? AND archived=0", (a.get("manager_id"),)) if a.get("manager_id") else None
    reports = [dict(r) for r in db.q("SELECT id,name FROM agents WHERE manager_id=? AND archived=0", (aid,))]
    a.pop("mail_password", None)
    tasks = db.q("SELECT id,title,status,stage,priority,due,checklist,plan_doc_id FROM duties WHERE agent_id=? AND status IN ('todo','doing') ORDER BY (status='doing') DESC, CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id LIMIT 10", (aid,))
    tasks = [{**{k: v for k, v in dict(r).items() if k != "checklist"}, "steps_total": len(json.loads(r["checklist"] or "[]")), "steps_done": sum(1 for c in json.loads(r["checklist"] or "[]") if c.get("done"))} for r in tasks]
    projects = db.q("SELECT p.id,p.name FROM project_members m JOIN projects p ON p.id=m.project_id WHERE m.agent_id=? AND p.status='active'", (aid,))
    recent = db.q("SELECT id,ts,kind,app,summary,ok FROM agent_activity WHERE agent_id=? ORDER BY id DESC LIMIT 6", (aid,))
    skills = db.q("SELECT name,description FROM skills WHERE agent_id=? OR agent_id IS NULL ORDER BY name", (aid,))
    return {**a, "manager": dict(boss) if boss else None, "reports": reports, "capabilities": capabilities.describe(a), "tasks": tasks,
            "projects": [dict(r) for r in projects], "recent": [dict(r) for r in recent], "procedures": [dict(r) for r in skills], "has_mail_password": bool(db.q1("SELECT mail_password p FROM agents WHERE id=?", (aid,))["p"]),
            "notes": [dict(r) for r in db.q("SELECT id,note FROM agent_notes WHERE agent_id=? ORDER BY id DESC LIMIT 20", (aid,))]}


@router.get("/agents/{aid}/history")
def agent_history(aid: int, kind: str = "", q: str = "", before: int = 0, limit: int = 80):
    w, args = ["agent_id=?"], [aid]
    if kind and kind != "all":
        w.append("kind=?"); args.append(kind)
    if q:
        w.append("summary LIKE ?"); args.append(f"%{q}%")
    if before:
        w.append("id<?"); args.append(before)
    rows = db.q(f"SELECT * FROM agent_activity WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT ?", args + [min(limit, 200)])
    counts = {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) n FROM agent_activity WHERE agent_id=? GROUP BY kind", (aid,))}
    return {"items": [{**dict(r), "detail": db.jload(r["detail"], {}) if r["detail"] else {}} for r in rows], "counts": counts}


class CapsIn(BaseModel):
    off: List[str] = []


@router.put("/agents/{aid}/capabilities")
def agent_caps(aid: int, c: CapsIn):
    a = _agent(aid)
    valid = {g[0] for g in capabilities.GROUPS}
    off = sorted(k for k in set(c.off) if k in valid)
    db.update("agents", aid, caps_off=json.dumps(off))
    team.log_event(aid, "profile", "notes", "Abilities changed by the admin" + (f": off = {', '.join(off)}" if off else ": all on"))
    return capabilities.describe(_agent(aid))


class ManagerIn(BaseModel):
    is_manager: Optional[bool] = None
    manager_id: Optional[int] = None  # who they report to; 0 = nobody


@router.put("/agents/{aid}/manager")
def agent_manager(aid: int, m: ManagerIn):
    a = _agent(aid)
    if m.is_manager is not None and bool(a["is_manager"]) != m.is_manager:
        team.set_manager(aid, m.is_manager)
    if m.manager_id is not None:
        if m.manager_id and (m.manager_id == aid or not agents.get(m.manager_id)):
            raise HTTPException(400, "Choose another employee as manager")
        db.update("agents", aid, manager_id=m.manager_id or None)
        team.log_event(aid, "profile", "notes", "Reporting line changed by the admin")
    return agent_overview(aid)


class MailTestIn(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None


@router.post("/agents/{aid}/mail/test")
async def agent_mail_test(aid: int, t: MailTestIn):
    a = dict(_agent(aid))
    if t.email:
        a["email"] = t.email.strip().lower()
    if t.password:
        a["mail_password"] = t.password.replace(" ", "")
        a["mail_login"] = t.email.strip().lower() if t.email else a["email"]
    return await office.test_mail(a)


class MailSetupIn(BaseModel):
    email: str
    password: str = ""


@router.put("/agents/{aid}/mail")
async def agent_mail_setup(aid: int, m: MailSetupIn):
    """Save the employee's work address and app password (spaces in a Google app password are fine)."""
    _agent(aid)
    email = m.email.strip().lower()
    if not office.EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address")
    if db.q1("SELECT id FROM agents WHERE lower(email)=? AND id<>? AND archived=0", (email, aid)):
        raise HTTPException(400, "Another employee already has that address")
    f = {"email": email, "mail_login": email}
    if m.password.strip():
        f["mail_password"] = m.password.replace(" ", "").strip()[:200]
    db.update("agents", aid, **f)
    team.log_event(aid, "profile", "mail", f"Email set to {email}" + (" with an app password" if m.password.strip() else ""))
    res = await office.test_mail(_agent(aid)) if (m.password.strip() or db.q1("SELECT mail_password p FROM agents WHERE id=?", (aid,))["p"]) else None
    return {"saved": True, "test": res}


@router.delete("/agents/{aid}/mail/password")
def agent_mail_clear(aid: int):
    _agent(aid)
    db.update("agents", aid, mail_password="")
    team.log_event(aid, "profile", "mail", "App password removed")
    return {"ok": True}


# ---------- projects ----------
def _project_access(request, pid):
    """Admins can open any project; a member only the ones they were added to. A project a member may not open is 'not found'."""
    s = request.state.session
    if access.is_member(s) and not people.in_project(pid, s["admin_id"]):
        raise HTTPException(404)


@router.get("/projects")
def projects_list(request: Request):
    out = []
    s = request.state.session
    mine = set(people.projects_of(s["admin_id"])) if access.is_member(s) else None
    for p in db.q("SELECT * FROM projects WHERE status<>'deleted' ORDER BY (status='done'), id DESC"):
        if mine is not None and p["id"] not in mine:
            continue
        members = db.q("SELECT g.id,g.name,g.color FROM project_members m JOIN agents g ON g.id=m.agent_id WHERE m.project_id=? AND g.archived=0", (p["id"],))
        n = db.q1("SELECT SUM(status<>'done') open, SUM(status='doing') doing, COUNT(*) total FROM duties WHERE project_id=?", (p["id"],))
        deliver = db.q1("SELECT COUNT(*) n FROM documents WHERE agent_id=?", (-p["id"],))["n"]
        work = sum(len(files.tree_docs(m["id"], f"Projects/{p['name']}")) for m in members)
        last = db.q1("SELECT MAX(ts) t FROM project_updates WHERE project_id=?", (p["id"],))["t"]
        total = n["total"] or 0
        out.append({**dict(p), "members": [dict(m) for m in members], "open_tasks": n["open"] or 0, "doing_tasks": n["doing"] or 0, "total_tasks": total, "progress": round(100 * (total - (n["open"] or 0)) / total) if total else 0,
                    "deliverables": deliver, "working_files": work, "last_update": last, "people": people.people_of_project(p["id"])})
    return out


class ProjectIn(BaseModel):
    name: str
    goal: str = ""
    members: List[int] = []
    people: List[int] = []  # the people who sign in (not AI employees) who work on it too


@router.post("/projects")
def project_create(p: ProjectIn):
    if not p.name.strip():
        raise HTTPException(400, "Give the project a name")
    pid = db.run("INSERT INTO projects(name,goal,created_by,created_at) VALUES(?,?,?,?)", (p.name.strip()[:100], p.goal[:1000], 0, db.now()))
    for f in ("Files", "Plans", "Reports"):
        files.folder_id(-pid, f)
    for aid in p.members[:12]:
        if agents.get(aid):
            db.run("INSERT OR IGNORE INTO project_members(project_id,agent_id,role,joined_at) VALUES(?,?,?,?)", (pid, aid, "member", db.now()))
            team.notify(aid, 0, f"You're invited to project: {p.name}", f"Goal: {p.goal}\n\nYou now have access to the project drive and tasks.")
            team.log_event(aid, "project", "projects", f"Added to project: {p.name}")
    for person in p.people[:25]:
        if db.q1("SELECT id FROM admins WHERE id=?", (person,)):
            people.add_to_project(pid, person, by="admin")
    team.plan_project(pid)  # a new project starts with a planning task for the team
    return {"id": pid}


@router.get("/projects/{pid}")
def project_get(pid: int, request: Request):
    _project_access(request, pid)
    p = db.q1("SELECT * FROM projects WHERE id=? AND status<>'deleted'", (pid,))
    if not p:
        raise HTTPException(404)
    s = request.state.session
    members = db.q("SELECT g.id,g.name,g.color,g.role job,m.role FROM project_members m JOIN agents g ON g.id=m.agent_id WHERE m.project_id=? AND g.archived=0", (pid,))
    tasks = db.q("SELECT d.id,d.title,d.detail,d.status,d.stage,d.priority,d.due,d.result,d.checklist,d.plan_doc_id,d.agent_id,g.name who,g.color who_color FROM duties d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.project_id=? ORDER BY d.status='done', d.id", (pid,))
    ups = db.q("SELECT u.id,u.text,u.ts,u.person,g.name who,g.color who_color FROM project_updates u LEFT JOIN agents g ON g.id=u.agent_id WHERE u.project_id=? ORDER BY u.id DESC LIMIT 40", (pid,))
    work = []  # drafts and working files live on each teammate's own computer, in Projects/<name>: the admins see them, a member sees the project drive only
    for m in ([] if access.is_member(s) else members):
        for f in files.tree_docs(m["id"], f"Projects/{p['name']}"):
            work.append({**f, "owner_id": m["id"], "owner": m["name"], "owner_color": m["color"]})
    work.sort(key=lambda f: -(f["updated_at"] or 0))
    deliverables = [dict(r) | {"folder": files.path_of(r["folder_id"])} for r in db.q("SELECT id,title,kind,updated_at,folder_id FROM documents WHERE agent_id=? ORDER BY updated_at DESC LIMIT 200", (-pid,))]
    out_tasks = []
    for t in tasks:
        cl = json.loads(t["checklist"] or "[]")
        out_tasks.append({**dict(t), "checklist": cl, "steps_done": sum(1 for c in cl if c.get("done")), "steps_total": len(cl)})
    total = len(out_tasks)
    done = sum(1 for t in out_tasks if t["status"] == "done")
    me_name = people.display_name(db.q1("SELECT username,display_name FROM admins WHERE id=?", (s["admin_id"],)) or {"username": ""})
    return {**dict(p), "members": [dict(m) for m in members], "people": people.people_of_project(pid), "tasks": out_tasks,
            "updates": [{**{k: v for k, v in dict(u).items() if k != "person"}, "who": u["who"] or u["person"] or "An admin", "mine": bool(not u["who"] and u["person"] and u["person"] == me_name), "by_person": bool(not u["who"])} for u in ups],
            "working_files": work[:80], "deliverables": deliverables, "progress": round(100 * done / total) if total else 0}


@router.post("/projects/{pid}/plan")
def project_plan(pid: int):
    """Ask the team to plan a project that has no tasks yet."""
    if not db.q1("SELECT id FROM projects WHERE id=? AND status<>'deleted'", (pid,)):
        raise HTTPException(404)
    return {"planning_task": team.plan_project(pid)}


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    goal: Optional[str] = None
    status: Optional[str] = None


@router.patch("/projects/{pid}")
def project_patch(pid: int, p: ProjectPatch):
    f = {k: v for k, v in p.model_dump().items() if v is not None}
    if f.get("status") not in (None, "active", "done", "deleted"):
        raise HTTPException(400, "bad status")
    if f:
        db.update("projects", pid, **f)
    return {"ok": True}


class MemberIn(BaseModel):
    agent_id: int
    task: str = ""


@router.post("/projects/{pid}/members")
def project_add_member(pid: int, m: MemberIn):
    p = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    a = agents.get(m.agent_id)
    if not p or not a:
        raise HTTPException(404)
    db.run("INSERT OR IGNORE INTO project_members(project_id,agent_id,role,joined_at) VALUES(?,?,?,?)", (pid, a["id"], "member", db.now()))
    if m.task.strip():
        db.run("INSERT INTO duties(agent_id,title,source,created_by,created_at,project_id,auto) VALUES(?,?,?,?,?,?,1)", (a["id"], m.task.strip()[:200], "admin", "admin", db.now(), pid))
    team.notify(a["id"], 0, f"You're invited to project: {p['name']}", f"Goal: {p['goal']}" + (f"\n\nFirst task: {m.task}" if m.task else ""))
    team.log_event(a["id"], "project", "projects", f"Added to project: {p['name']}")
    return {"ok": True}


@router.delete("/projects/{pid}/members/{aid}")
def project_remove_member(pid: int, aid: int):
    db.run("DELETE FROM project_members WHERE project_id=? AND agent_id=?", (pid, aid))
    return {"ok": True}


class UpdateIn(BaseModel):
    text: str


@router.post("/projects/{pid}/updates")
def project_update(pid: int, u: UpdateIn, request: Request):
    _project_access(request, pid)
    if not db.q1("SELECT id FROM projects WHERE id=? AND status<>'deleted'", (pid,)):
        raise HTTPException(404)
    if not u.text.strip():
        raise HTTPException(400, "Write something first.")
    who = people.display_name(db.q1("SELECT username,display_name FROM admins WHERE id=?", (request.state.session["admin_id"],)) or {"username": ""})
    db.run("INSERT INTO project_updates(project_id,agent_id,text,ts,person) VALUES(?,?,?,?,?)", (pid, 0, u.text.strip()[:1500], db.now(), who))
    return {"ok": True}


class ProjectTaskIn(BaseModel):
    agent_id: int
    title: str
    detail: str = ""


@router.post("/projects/{pid}/tasks")
def project_task_create(pid: int, t: ProjectTaskIn, request: Request):
    """Ask an AI teammate on this project to do something. A member's request is a request, not an admin's order: it carries no admin authority."""
    _project_access(request, pid)
    s = request.state.session
    if not db.q1("SELECT id FROM projects WHERE id=? AND status<>'deleted'", (pid,)):
        raise HTTPException(404)
    if not t.title.strip():
        raise HTTPException(400, "Say what you need done.")
    if not team.is_member(pid, t.agent_id) or not agents.get(t.agent_id):
        raise HTTPException(400, "Pick someone who is on this project.")
    who = people.display_name(db.q1("SELECT username,display_name FROM admins WHERE id=?", (s["admin_id"],)) or {"username": ""})
    admin = access.is_admin(s)
    did = db.run("INSERT INTO duties(agent_id,title,detail,source,created_by,created_at,project_id,auto) VALUES(?,?,?,?,?,?,?,1)",
                 (t.agent_id, t.title.strip()[:200], (t.detail.strip()[:2000] + ("" if admin else f"\n\nAsked by {who}, who works on this project.")).strip(), "admin" if admin else "person", "admin" if admin else who, db.now(), pid))
    team.log_event(t.agent_id, "task", "tasks", f"{who} asked for: {t.title.strip()[:80]}")
    team.notify(t.agent_id, 0, f"New task from {who}: {t.title.strip()[:80]}", f"{t.detail}\nIt is on your to-do list (#{did}).")
    return {"id": did}


# ---------- meeting requests (agents asking the admin for a meeting) ----------
@router.get("/attention")
def attention():
    now = db.now()
    reqs = db.q("""SELECT r.*, g.name agent_name, g.color agent_color FROM meeting_requests r LEFT JOIN agents g ON g.id=r.agent_id
                   WHERE r.status IN ('pending','scheduled') ORDER BY (r.urgency='urgent') DESC, COALESCE(r.scheduled_at, r.created_at)""")
    return {
        "meetings": [dict(r) | {"due_soon": bool(r["scheduled_at"] and r["scheduled_at"] - now < 3600)} for r in reqs],
        "approvals": db.q1("SELECT COUNT(*) n FROM emails WHERE status='pending_approval'")["n"] + db.q1("SELECT COUNT(*) n FROM outbox WHERE status='pending_approval'")["n"] + chats.counts()["approvals"],
        "chats_unread": chats.counts()["unread"],
        "unread": db.q1("SELECT COUNT(*) n FROM emails WHERE agent_id=0 AND folder='inbox' AND status='unread'")["n"],
        "connectors_to_review": db.company_q1("SELECT COUNT(*) n FROM connectors WHERE added_by LIKE '%(manager)' AND checked_at>?", (now - 86400 * 2,))["n"],
        "live_questions": len(live_help.listing("open")),
        "discuss": db.q1("SELECT COUNT(*) n FROM discuss_items WHERE status='open'")["n"],
        "app_requests": app_requests.listing(),
        "needs_manager": db.q1("SELECT COUNT(*) n FROM agents WHERE archived=0 AND is_manager=1")["n"] == 0 and db.q1("SELECT COUNT(*) n FROM agents WHERE archived=0")["n"] > 1,
    }


class ScheduleIn(BaseModel):
    when: str


@router.post("/requests/{rid}/schedule")
def request_schedule(rid: int, s: ScheduleIn):
    at = schedule.parse_when(s.when)
    if not at:
        raise HTTPException(400, "Enter a valid date and time")
    r = db.q1("SELECT * FROM meeting_requests WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    db.update("meeting_requests", rid, status="scheduled", scheduled_at=at, pre_reminded=0)
    db.run("INSERT INTO events(agent_id,title,start,end,attendees,notes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
           (r["agent_id"], f"Meeting: {r['topic']}", at, at + 1800, json.dumps([(agents.get(r["agent_id"]) or {}).get("name", "")]), r["reason"][:500], "admin", db.now()))
    team.notify(r["agent_id"], 0, f"Meeting booked: {r['topic']}", f"The admin booked a meeting for {schedule.describe(at)}.")
    return {"ok": True}


@router.post("/requests/{rid}/start")
def request_start(rid: int):
    r = db.q1("SELECT * FROM meeting_requests WHERE id=?", (rid,))
    if not r or not agents.get(r["agent_id"]):
        raise HTTPException(404)
    mid = agents.start_meeting([r["agent_id"]], f"{r['topic'][:60]}")
    db.run("INSERT INTO meeting_msgs(meeting_id,role,text,created_at) VALUES(?,?,?,?)", (mid, "admin", f"[Context for you: you asked for this meeting. Topic: {r['topic']}. Why: {r['reason']}. Start by saying what you need from me.]", db.now()))
    db.update("meeting_requests", rid, status="done", meeting_id=mid)
    return {"meeting_id": mid}


@router.post("/requests/{rid}/decline")
def request_decline(rid: int):
    db.update("meeting_requests", rid, status="declined")
    return {"ok": True}


@router.post("/requests/{rid}/done")
def request_done(rid: int):
    db.update("meeting_requests", rid, status="done")
    return {"ok": True}


# ---------- connectors (MCP) ----------
@router.get("/connectors")
def connectors_list():
    return connectors.listing()


class ConnectorIn(BaseModel):
    name: str
    url: str
    token: str = ""


@router.post("/connectors")
async def connectors_add(c: ConnectorIn):
    try:
        cid = await connectors.add(c.name, c.url, c.token, "admin")
    except connectors.MCPError as e:
        raise HTTPException(400, str(e))
    return {"id": cid, "connector": next(x for x in connectors.listing() if x["id"] == cid)}


@router.post("/connectors/{cid}/refresh")
async def connectors_refresh(cid: int):
    await connectors.refresh(cid)
    return next((x for x in connectors.listing() if x["id"] == cid), None)


class ConnectorPatch(BaseModel):
    enabled: Optional[bool] = None
    token: Optional[str] = None


@router.patch("/connectors/{cid}")
def connectors_patch(cid: int, p: ConnectorPatch):
    f = {}
    if p.enabled is not None:
        f["enabled"] = int(p.enabled)
    if p.token:
        f["token"] = p.token.strip()
    if f:
        db.company_update("connectors", cid, **f)
    return {"ok": True}


@router.delete("/connectors/{cid}")
def connectors_delete(cid: int):
    db.company_run("DELETE FROM connectors WHERE id=?", (cid,))
    return {"ok": True}


# ---------- training ----------
class TrainNew(BaseModel):
    name: str
    goal: str = ""


@router.get("/agents/{aid}/trainings")
def trainings_list(aid: int):
    _agent(aid)
    return training.listing(aid)


@router.post("/agents/{aid}/trainings")
def training_new(aid: int, t: TrainNew):
    _agent(aid)
    if not t.name.strip():
        raise HTTPException(400, "Name the task first")
    return training.get(training.create(aid, t.name.strip(), t.goal))


@router.get("/trainings/{tid}")
def training_get(tid: int):
    t = training.get(tid)
    if not t:
        raise HTTPException(404)
    return t


class TrainFields(BaseModel):
    name: Optional[str] = None
    goal: Optional[str] = None
    when_to_use: Optional[str] = None
    inputs: Optional[str] = None
    steps: Optional[str] = None
    good_result: Optional[str] = None
    avoid: Optional[str] = None
    ask_help: Optional[str] = None


@router.put("/trainings/{tid}")
def training_save(tid: int, f: TrainFields):
    if not training.get(tid):
        raise HTTPException(404)
    training.update(tid, f.model_dump())
    return training.get(tid)


class RoughIn(BaseModel):
    rough: str


@router.post("/trainings/{tid}/structure")
async def training_structure(tid: int, r: RoughIn):
    if not training.get(tid):
        raise HTTPException(404)
    if len(r.rough.strip()) < 10:
        raise HTTPException(400, "Tell me a little more about how it's done")
    return await training.structure(tid, r.rough[:6000])


class InterviewIn(BaseModel):
    message: str = ""


@router.post("/trainings/{tid}/interview")
async def training_interview(tid: int, m: InterviewIn):
    if not training.get(tid):
        raise HTTPException(404)
    return await training.interview(tid, m.message)


class PracticeIn(BaseModel):
    scenario: str


@router.post("/trainings/{tid}/practice")
async def training_practice(tid: int, p: PracticeIn):
    if not training.get(tid):
        raise HTTPException(404)
    if not training.get(tid)["steps"].strip():
        raise HTTPException(400, "Write the steps first")
    return await training.practice(tid, p.scenario[:1500])


class FeedbackIn(BaseModel):
    run_id: int
    rating: str = "needs_work"
    feedback: str = ""


@router.post("/trainings/{tid}/feedback")
async def training_feedback(tid: int, f: FeedbackIn):
    if not training.get(tid):
        raise HTTPException(404)
    return await training.revise(tid, f.run_id, f.rating, f.feedback[:3000])


class ApproveIn(BaseModel):
    repeat: str = ""


@router.post("/trainings/{tid}/approve")
def training_approve(tid: int, a: ApproveIn):
    t = training.get(tid)
    if not t:
        raise HTTPException(404)
    if not t["steps"].strip():
        raise HTTPException(400, "Write the steps first")
    return training.approve(tid, a.repeat)


@router.delete("/trainings/{tid}")
def training_delete(tid: int):
    db.run("DELETE FROM training_runs WHERE training_id=?", (tid,))
    db.run("DELETE FROM trainings WHERE id=?", (tid,))
    return {"ok": True}


# ---------- central settings: calling, costs ----------
@router.get("/pulse")
def pulse():
    """The team at a glance: who is working, what is open, what got done this week, and what everyone just did."""
    ags = agents.listing()
    mid = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))  # local midnight
    week = []
    for i in range(6, -1, -1):
        lo = mid - i * 86400
        week.append({"day": time.strftime("%a", time.localtime(lo + 3600)), "n": db.q1("SELECT COUNT(*) n FROM duties WHERE status='done' AND done_at>=? AND done_at<?", (lo, lo + 86400))["n"]})
    return {"working": sum(1 for a in ags if a["working"]), "total": len(ags), "open_todos": sum(a["open_duties"] for a in ags), "done_today": week[-1]["n"], "week": week,
            "tasks_today": agents._runs_today(), "tasks_limit": agents.company_limit()}


@router.get("/central")
def central_get():
    return {
        "calls_enabled": voices.get_setting("calls_enabled", "1") == "1",
        "voice_engine": voices.get_setting("voice_engine", "openai"),
        "messaging_policy": voices.get_setting("messaging_policy", "approve"),
        "admin_phone": voices.get_setting("admin_phone", ""),
        "live_question_timeout": int(voices.get_setting("live_question_timeout", "75") or 75),
        "discuss_email": voices.get_setting("discuss_email", "1") == "1",
        "work_paused": agents.paused(), "max_tasks_per_day": agents.per_agent_limit(), "company_tasks_per_day": agents.company_limit(), "work_pace_seconds": agents._pace(),
        "tasks_today": agents._runs_today(),
        "models": {"agent_work": llm.CHEAP_MODEL, "meetings_voice": llm.MEETING_REALTIME_MODEL, "meetings_voice_replies": "gpt-4o-mini-tts", "summaries_and_reports": llm.CHEAP_MODEL,
                   "phone_calls": llm.REALTIME_MODEL if voices.get_setting("voice_engine", "openai") == "openai" else "ElevenLabs agent (calls only)"},
        "elevenlabs_rule": "ElevenLabs is used only for live phone calls, and only when the call voice engine is set to ElevenLabs. Meetings, email, tasks, reports and everything else use OpenAI's cheaper models.",
    }


class CentralIn(BaseModel):
    calls_enabled: Optional[bool] = None
    work_paused: Optional[bool] = None
    max_tasks_per_day: Optional[int] = None
    company_tasks_per_day: Optional[int] = None
    work_pace_seconds: Optional[int] = None


@router.put("/central")
def central_put(c: CentralIn):
    if c.calls_enabled is not None:
        voices.set_setting("calls_enabled", "1" if c.calls_enabled else "0")
    if c.work_paused is not None:
        voices.set_setting("work_paused", "1" if c.work_paused else "0")
    for key, val, lo, hi in (("max_tasks_per_day", c.max_tasks_per_day, 1, 500), ("company_tasks_per_day", c.company_tasks_per_day, 1, 2000), ("work_pace_seconds", c.work_pace_seconds, 0, 600)):
        if val is not None:
            voices.set_setting(key, str(max(lo, min(hi, val))))
    return central_get()


# ---------- company: what each deployment is set up for ----------
@router.get("/company")
def company_get():
    import zoneinfo
    return {"name": voices.get_setting("company_name", ""), "about": voices.get_setting("company_about", ""), "website": voices.get_setting("company_website", ""),
            "timezone": voices.get_setting("timezone", ""), "server_timezone": str(llm.company_tz()),
            "timezones": sorted(z for z in zoneinfo.available_timezones() if "/" in z and not z.startswith(("Etc/", "posix", "right", "SystemV")))[:700],
            "agents": db.q1("SELECT COUNT(*) n FROM agents WHERE archived=0")["n"], "knowledge_bases": db.q1("SELECT COUNT(*) n FROM knowledge_bases")["n"],
            "pending_team": _pending_team()}


def _pending_team():
    """The team the company chose when it signed up, if it has not been created yet."""
    from . import templates
    tid = voices.get_setting("pending_team", "")
    t = next((x for x in templates.TEAMS if x["id"] == tid), None)
    return {"id": t["id"], "name": t["name"], "roles": [m["role"] for m in t["members"]]} if t else None


class CompanyIn(BaseModel):
    name: Optional[str] = None
    about: Optional[str] = None
    website: Optional[str] = None
    timezone: Optional[str] = None


@router.put("/company")
def company_put(c: CompanyIn):
    if c.timezone:
        try:
            import zoneinfo
            zoneinfo.ZoneInfo(c.timezone)
        except Exception:
            raise HTTPException(400, "Unknown time zone")
    for key, val in (("company_name", c.name), ("company_about", c.about), ("company_website", c.website), ("timezone", c.timezone)):
        if val is not None:
            voices.set_setting(key, val.strip()[:2000])
    files.sync_company_doc()
    return company_get()


class LearnIn(BaseModel):
    url: str


@router.post("/company/learn")
async def company_learn(l: LearnIn):
    """Read the company's website into a knowledge base the callers can use."""
    import asyncio
    from . import webdemo
    try:
        r = await asyncio.wait_for(webdemo.learn(l.url.strip()[:500]), 100)
    except webdemo.Blocked as e:
        raise HTTPException(400, str(e))
    except asyncio.TimeoutError:
        raise HTTPException(504, "That website took too long to read.")
    name = voices.get_setting("company_name", "") or r["company"]
    db.update("knowledge_bases", r["kb_id"], name=name)
    if not voices.get_setting("company_about", ""):
        about = (r.get("info") or {}).get("what_they_do") or ""
        if about:
            voices.set_setting("company_about", about[:2000])
    files.sync_company_doc()
    return {"kb_id": r["kb_id"], "sections": r["sections"], "pages": len(r["pages"]), "about": voices.get_setting("company_about", "")}


# ---------- things to discuss with the admin ----------
@router.get("/discuss")
def discuss_list(agent_id: Optional[int] = None, status: str = "open"):
    if status == "open":
        rows = discuss.open_items(agent_id)
    else:
        rows = db.q("SELECT d.*, g.name agent_name FROM discuss_items d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.status=? AND (?=0 OR d.agent_id=?) ORDER BY d.id DESC LIMIT 60", (status, agent_id or 0, agent_id or 0))
    return [dict(r) for r in rows]


class DiscussIn(BaseModel):
    agent_id: int = 0
    topic: str
    detail: str = ""


@router.post("/discuss")
def discuss_add(d: DiscussIn):
    if not d.topic.strip():
        raise HTTPException(400, "Write the topic")
    return {"id": discuss.add(d.agent_id, d.topic, d.detail, "admin")}


class DiscussAnswer(BaseModel):
    answer: str
    remember: bool = True


@router.post("/discuss/{did}/answer")
def discuss_answer(did: int, a: DiscussAnswer):
    if not a.answer.strip():
        raise HTTPException(400, "Write the answer")
    if not discuss.resolve(did, a.answer, a.remember):
        raise HTTPException(404)
    return {"ok": True}


@router.post("/discuss/{did}/dismiss")
def discuss_dismiss(did: int):
    db.update("discuss_items", did, status="dismissed", answered_at=db.now())
    return {"ok": True}


# ---------- a customer is on hold and an employee needs you ----------
@router.get("/live-questions")
def live_questions():
    return live_help.listing("open")


class LiveAnswer(BaseModel):
    answer: str
    remember: bool = True


@router.post("/live-questions/{qid}/answer")
def live_answer(qid: int, a: LiveAnswer):
    q = db.q1("SELECT * FROM live_questions WHERE id=? AND status='open'", (qid,))
    if not q:
        raise HTTPException(404, "That question has already been dealt with")
    if not a.answer.strip():
        raise HTTPException(400, "Write the answer")
    db.update("live_questions", qid, status="answered", answer=a.answer.strip()[:1500], remember=int(a.remember), answered_at=db.now())
    return {"ok": True}


@router.post("/live-questions/{qid}/decline")
def live_decline(qid: int):
    db.update("live_questions", qid, status="declined")
    return {"ok": True}


# ---------- apps an employee asked for ----------
@router.get("/app-requests")
def app_request_list():
    return app_requests.listing()


@router.post("/app-requests/{rid}/dismiss")
def app_request_dismiss(rid: int):
    db.update("app_requests", rid, status="dismissed")
    return {"ok": True}


class CentralLive(BaseModel):
    admin_phone: Optional[str] = None
    live_question_timeout: Optional[int] = None
    discuss_email: Optional[bool] = None


@router.put("/central/live")
def central_live(c: CentralLive):
    if c.admin_phone is not None:
        voices.set_setting("admin_phone", c.admin_phone.strip()[:30])
    if c.live_question_timeout is not None:
        voices.set_setting("live_question_timeout", str(max(20, min(c.live_question_timeout, 240))))
    if c.discuss_email is not None:
        voices.set_setting("discuss_email", "1" if c.discuss_email else "0")
    return central_get()

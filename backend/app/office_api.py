"""HTTP API for the Office pages (mail, tasks, files, calendar, playbooks, mail settings)."""
import io
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import agents, db, files, office, schedule, voices

router = APIRouter(prefix="/api")


@router.get("/office/summary")
def summary():
    return {
        "unread": db.q1("SELECT COUNT(*) n FROM emails WHERE agent_id=0 AND folder='inbox' AND status='unread'")["n"],
        "approvals": db.q1("SELECT COUNT(*) n FROM emails WHERE status='pending_approval'")["n"],
        "open_tasks": db.q1("SELECT COUNT(*) n FROM duties WHERE status IN ('todo','doing')")["n"],
        "doing": db.q1("SELECT COUNT(*) n FROM duties WHERE status='doing'")["n"],
    }


# ---------- mail ----------
def _box(box):
    return 0 if box in ("admin", "0", 0, None) else int(box)


def _like(q):
    return "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/mail")
def mail_list(box: str = "admin", folder: str = "inbox", q: str = "", unread: int = 0, limit: int = 50, offset: int = 0):
    b = _box(box)
    if folder == "pending":
        w, args = ["e.status='pending_approval'"], []
    else:
        w, args = ["e.agent_id=? AND e.folder=?"], [b, folder]
    if unread:
        w.append("e.status='unread'")
    if q.strip():
        w.append("(e.subject LIKE ? ESCAPE '\\' OR e.from_addr LIKE ? ESCAPE '\\' OR e.to_addr LIKE ? ESCAPE '\\' OR e.body LIKE ? ESCAPE '\\')")
        args += [_like(q)] * 4
    rows = db.q("SELECT e.*, g.name owner, (SELECT COUNT(*) FROM emails t WHERE t.thread_id=e.thread_id) thread_count FROM emails e LEFT JOIN agents g ON g.id=e.agent_id "
                f"WHERE {' AND '.join(w)} ORDER BY e.id DESC LIMIT ? OFFSET ?", (*args, max(1, min(limit, 200)), max(offset, 0)))
    return [{**{k: r[k] for k in ("id", "agent_id", "from_addr", "to_addr", "subject", "status", "external", "created_at", "error", "thread_id")}, "owner": r["owner"], "thread_count": r["thread_count"],
             "has_attachments": (r.get("attachments") or "[]") not in ("[]", ""), "preview": " ".join((r["body"] or "")[:400].split())[:140]} for r in rows]


@router.get("/mail/counts")
def mail_counts(box: str = "admin"):
    b = _box(box)
    n = lambda sql, *a: db.q1(sql, a)["n"]  # noqa: E731
    return {"unread": n("SELECT COUNT(*) n FROM emails WHERE agent_id=? AND folder='inbox' AND status='unread'", b), "inbox": n("SELECT COUNT(*) n FROM emails WHERE agent_id=? AND folder='inbox'", b),
            "sent": n("SELECT COUNT(*) n FROM emails WHERE agent_id=? AND folder='sent'", b), "pending": n("SELECT COUNT(*) n FROM emails WHERE status='pending_approval'")}


class MailBulk(BaseModel):
    ids: List[int]
    action: str  # read | unread | delete


@router.post("/mail/bulk")
def mail_bulk(p: MailBulk):
    ids = [int(i) for i in p.ids[:500]]
    if not ids:
        return {"changed": 0}
    marks = ",".join("?" * len(ids))
    if p.action == "delete":
        db.run(f"DELETE FROM emails WHERE id IN ({marks})", tuple(ids))
    elif p.action in ("read", "unread"):
        db.run(f"UPDATE emails SET status=? WHERE id IN ({marks}) AND folder='inbox'", (p.action, *ids))
    else:
        raise HTTPException(400, "action must be read, unread or delete")
    return {"changed": len(ids)}


@router.get("/mail/{eid}")
def mail_get(eid: int):
    r = db.q1("SELECT e.*, g.name owner FROM emails e LEFT JOIN agents g ON g.id=e.agent_id WHERE e.id=?", (eid,))
    if not r:
        raise HTTPException(404)
    if r["folder"] == "inbox" and r["agent_id"] == 0 and r["status"] == "unread":
        db.update("emails", eid, status="read")
    thread = db.q("SELECT id,from_addr,to_addr,subject,body,created_at FROM emails WHERE thread_id=? AND id<>? ORDER BY id", (r["thread_id"], eid)) if r["thread_id"] else []
    return {**dict(r), "attachments": db.jload(r.get("attachments"), []), "thread": [dict(t) for t in thread]}


class MailIn(BaseModel):
    to: str
    subject: str
    body: str
    reply_to: Optional[int] = None


@router.post("/mail")
async def mail_send(m: MailIn):
    """The admin writes an email (to an agent's address, or anyone)."""
    thread = None
    if m.reply_to:
        src = db.q1("SELECT thread_id FROM emails WHERE id=?", (m.reply_to,))
        thread = src["thread_id"] if src else None
    try:
        eid, state = office.queue_email(0, m.to, m.subject[:300], m.body[:20000], thread=thread, from_admin=True)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ok = await office.deliver(eid)
    r = db.q1("SELECT status,error FROM emails WHERE id=?", (eid,))
    return {"id": eid, "sent": ok, "error": r["error"]}


@router.post("/mail/{eid}/approve")
async def mail_approve(eid: int):
    r = db.q1("SELECT * FROM emails WHERE id=? AND status='pending_approval'", (eid,))
    if not r:
        raise HTTPException(404, "nothing waiting for approval")
    db.update("emails", eid, status="approved")
    db.log("security", f"Email '{r['subject'][:60]}' to {r['to_addr'][:60]} approved by the admin")
    ok = await office.deliver(eid)
    return {"sent": ok, "error": (db.q1("SELECT error FROM emails WHERE id=?", (eid,)) or {}).get("error")}


class MailEdit(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


@router.patch("/mail/{eid}")
def mail_edit(eid: int, p: MailEdit):
    if not db.q1("SELECT id FROM emails WHERE id=? AND status='pending_approval'", (eid,)):
        raise HTTPException(400, "only emails waiting for approval can be edited")
    f = {k: v for k, v in p.model_dump().items() if v is not None}
    if f:
        db.update("emails", eid, **f)
    return {"ok": True}


@router.post("/mail/{eid}/reject")
def mail_reject(eid: int):
    db.log("security", f"Email #{eid} rejected by the admin")
    db.update("emails", eid, status="rejected", folder="rejected")
    return {"ok": True}


@router.delete("/mail/{eid}")
def mail_delete(eid: int):
    db.run("DELETE FROM emails WHERE id=?", (eid,))
    return {"ok": True}


# ---------- tasks ----------
@router.get("/tasks")
def tasks(agent_id: Optional[int] = None, status: str = "all"):
    w, args = ["1=1"], []
    if agent_id:
        w.append("d.agent_id=?"); args.append(agent_id)
    if status != "all":
        w.append("d.status=?"); args.append(status)
    rows = db.q(f"SELECT d.*, g.name agent_name, g.color agent_color FROM duties d LEFT JOIN agents g ON g.id=d.agent_id WHERE {' AND '.join(w)} ORDER BY (d.status='done'), CASE d.priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, d.id DESC LIMIT 300", args)
    return [{**dict(r), "checklist": json.loads(r["checklist"] or "[]")} for r in rows]


class TaskIn(BaseModel):
    agent_id: int
    title: str
    detail: str = ""
    due: str = ""
    priority: str = "normal"
    checklist: List[str] = []
    recur: str = ""
    auto: bool = False
    run_now: bool = False
    project_id: Optional[int] = None


@router.post("/tasks")
def task_create(t: TaskIn):
    if not agents.get(t.agent_id):
        raise HTTPException(404, "agent not found")
    due, ts = office._parse_due(t.due)
    did = db.run("INSERT INTO duties(agent_id,title,detail,due,due_ts,priority,checklist,recur,auto,source,created_by,created_at,project_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (t.agent_id, t.title[:200], t.detail[:2000], due, ts, t.priority if t.priority in ("low", "normal", "high") else "normal",
                  json.dumps([{"text": c[:200], "done": False} for c in t.checklist[:20]]), t.recur if t.recur in ("daily", "weekly") else "", 1 if (t.auto or t.run_now) else 0, "admin", "admin", db.now(), t.project_id))
    from . import team
    team.log_event(t.agent_id, "task", "tasks", f"The admin gave a task: {t.title[:80]}")
    team.notify(t.agent_id, 0, f"New task: {t.title[:80]}", f"{t.detail}\nIt is on your to-do list (#{did}).")
    if t.run_now:
        agents.start_duty(did)
    return {"id": did}


class TaskPatch(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    title: Optional[str] = None
    detail: Optional[str] = None
    due: Optional[str] = None
    auto: Optional[bool] = None
    toggle_step: Optional[int] = None
    agent_id: Optional[int] = None


@router.patch("/tasks/{did}")
def task_patch(did: int, p: TaskPatch):
    d = db.q1("SELECT * FROM duties WHERE id=?", (did,))
    if not d:
        raise HTTPException(404)
    f = {}
    for k in ("status", "priority", "title", "detail", "agent_id"):
        v = getattr(p, k)
        if v is not None:
            f[k] = v
    if f.get("status") not in (None, "todo", "doing", "done"):
        raise HTTPException(400, "bad status")
    if p.due is not None:
        f["due"], f["due_ts"] = office._parse_due(p.due)
    if p.auto is not None:
        f["auto"] = int(p.auto)
    if p.toggle_step is not None:
        cl = json.loads(d["checklist"] or "[]")
        if 0 <= p.toggle_step < len(cl):
            cl[p.toggle_step]["done"] = not cl[p.toggle_step]["done"]
            f["checklist"] = json.dumps(cl)
    if f.get("status") == "done":
        f["done_at"] = db.now()
    if f:
        f["attempts"] = 0  # an edit gives a stuck item a fresh start
        db.update("duties", did, **f)
    if f.get("status") == "done" and d["status"] != "done":
        office.spawn_recurrence(dict(d))
    return {"ok": True}


# ---------- file system (each agent's computer + the shared drive) ----------
def _owner(o):
    return 0 if o in ("shared", "0", 0, None, "") else int(o)


def _check_owner(o):
    if o > 0 and not agents.get(o):
        raise HTTPException(404, "agent not found")
    if o < 0 and not db.q1("SELECT id FROM projects WHERE id=?", (-o,)):
        raise HTTPException(404, "project not found")


@router.get("/fs")
def fs_list(owner: str = "shared", folder: Optional[int] = None):
    o = _owner(owner)
    _check_owner(o)
    return files.listing(o, folder)


@router.get("/fs/folders")
def fs_folders(owner: str = "shared"):
    """Every folder of one drive, flat: the browser builds the tree."""
    o = _owner(owner)
    _check_owner(o)
    return [dict(r) for r in db.q("SELECT id,parent_id,name FROM folders WHERE owner=? ORDER BY lower(name)", (o,))]


@router.get("/fs/search")
def fs_search(owner: str = "shared", q: str = ""):
    o = _owner(owner)
    _check_owner(o)
    q = q.strip()
    if not q:
        return {"folders": [], "files": []}
    like = f"%{q}%"
    fl = [{**dict(r), "path": files.path_of(r["id"])} for r in db.q("SELECT id,name FROM folders WHERE owner=? AND name LIKE ? ORDER BY lower(name) LIMIT 40", (o, like))]
    fi = [{**dict(r), "path": files.path_of(r["folder_id"]) if r["folder_id"] else ""} for r in db.q(
        "SELECT id,title,kind,updated_at,folder_id,length(body) AS size FROM documents WHERE agent_id=? AND (title LIKE ? OR (kind='doc' AND body LIKE ?)) ORDER BY updated_at DESC LIMIT 60", (o, like, like))]
    return {"folders": fl, "files": fi}


@router.get("/agents/{aid}/drives")
def agent_drives(aid: int):
    """Where an employee can look for files: their own computer, the shared drive and the drives of their projects."""
    a = agents.get(aid)
    if not a:
        raise HTTPException(404)
    out = [{"owner": str(aid), "label": f"{a['name']}'s computer", "kind": "own"}, {"owner": "shared", "label": "Shared drive", "kind": "shared"}]
    for p in db.q("SELECT p.id,p.name FROM project_members m JOIN projects p ON p.id=m.project_id WHERE m.agent_id=? AND p.status<>'deleted' ORDER BY p.name", (aid,)):
        out.append({"owner": str(-p["id"]), "label": p["name"], "kind": "project"})
    return out


class FolderIn(BaseModel):
    owner: str = "shared"
    parent_id: Optional[int] = None
    name: str


@router.post("/fs/folder")
def fs_folder(f: FolderIn):
    name = f.name.strip().replace("/", "-").replace("\\", "-")[:60]
    if not name:
        raise HTTPException(400, "Give the folder a name")
    o = _owner(f.owner)
    if db.q1("SELECT id FROM folders WHERE owner=? AND parent_id IS ? AND lower(name)=lower(?)", (o, f.parent_id, name)):
        raise HTTPException(400, "A folder with that name already exists here")
    return {"id": db.run("INSERT INTO folders(owner,parent_id,name,created_at) VALUES(?,?,?,?)", (o, f.parent_id, name, db.now()))}


class FolderPatch(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    to_root: bool = False


@router.patch("/fs/folder/{fid}")
def fs_folder_patch(fid: int, p: FolderPatch):
    f = {}
    if p.name is not None:
        f["name"] = p.name.strip().replace("/", "-")[:60]
    if p.to_root:
        f["parent_id"] = None
    elif p.parent_id is not None and p.parent_id != fid:
        if p.parent_id in files.descendants(fid):
            raise HTTPException(400, "A folder can't go inside itself")
        f["parent_id"] = p.parent_id
    if f:
        db.update("folders", fid, **f)
    return {"ok": True}


@router.delete("/fs/folder/{fid}")
def fs_folder_delete(fid: int):
    files.delete_folder(fid)
    return {"ok": True}


class FileIn(BaseModel):
    owner: str = "shared"
    folder_id: Optional[int] = None
    title: str
    body: str = ""
    kind: str = "doc"
    columns: List[str] = []


@router.post("/fs/file")
def fs_file(f: FileIn):
    o = _owner(f.owner)
    if f.kind == "sheet":
        body = json.dumps({"columns": f.columns or ["A", "B", "C"], "rows": [[""] * max(1, len(f.columns or ["A", "B", "C"]))]})
    else:
        body = f.body[:60000]
    return {"id": db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (o, f.title[:160], "sheet" if f.kind == "sheet" else "doc", body, f.folder_id, db.now(), db.now()))}


@router.post("/fs/upload")
async def fs_upload(owner: str = Form("shared"), folder_id: Optional[int] = Form(None), file: UploadFile = File(...)):
    """Admin drops a file onto an agent's computer: text is extracted so the agent can read it; the original can be downloaded."""
    from . import knowledge
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "File too large (15 MB max)")
    name = (file.filename or "file").replace("/", "_").replace("\\", "_")[:120]
    low = name.lower()
    o = _owner(owner)
    kind, body = "doc", ""
    try:
        if low.endswith((".xlsx", ".xls", ".csv")):
            import csv
            if low.endswith(".csv"):
                rows = list(csv.reader(io.StringIO(data.decode("utf-8", errors="replace"))))
            else:
                from openpyxl import load_workbook
                ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
                rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
            rows = [r for r in rows if any(str(c).strip() for c in r)][:5000]
            kind, body = "sheet", json.dumps({"columns": [str(c)[:80] for c in (rows[0] if rows else [])][:40], "rows": [[str(c)[:500] for c in r[:40]] for r in rows[1:]]}, ensure_ascii=False)
        elif low.endswith((".pdf", ".txt", ".md", ".json")):
            body = knowledge.extract_text(name, data)[:60000]
        else:
            raise HTTPException(400, "Upload a PDF, text, markdown, CSV or Excel file")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "I couldn't read that file")
    did = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,mime,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (o, name[:160], kind, body, folder_id, file.content_type or "", db.now(), db.now()))
    return {"id": did}


@router.put("/docs/{did}/table")
def doc_table(did: int, t: Dict[str, Any]):
    d = db.q1("SELECT kind FROM documents WHERE id=?", (did,))
    if not d or d["kind"] != "sheet":
        raise HTTPException(404, "not a spreadsheet")
    cols = [str(c)[:80] for c in (t.get("columns") or [])][:40]
    rows = [[str(c)[:500] for c in r][:40] for r in (t.get("rows") or [])][:5000]
    db.update("documents", did, body=json.dumps({"columns": cols, "rows": rows}, ensure_ascii=False), updated_at=db.now())
    return {"ok": True}


# ---------- an agent's computer: what they are doing, phone, browser ----------
@router.get("/agents/{aid}/activity")
def agent_activity(aid: int, after: int = 0, limit: int = 60):
    rows = db.q("SELECT * FROM agent_activity WHERE agent_id=? AND id>? ORDER BY id DESC LIMIT ?", (aid, after, min(limit, 200)))
    return [{**dict(r), "detail": db.jload(r["detail"], {}) if r["detail"] else {}} for r in reversed(rows)]


@router.get("/agents/{aid}/status")
def agent_status(aid: int):
    running = [d for d in db.q("SELECT id,title FROM duties WHERE agent_id=? AND status='doing'", (aid,)) if d["id"] in agents.RUNNING]
    last = db.q1("SELECT ts,app,summary FROM agent_activity WHERE agent_id=? ORDER BY id DESC LIMIT 1", (aid,))
    live = db.q1("SELECT id FROM meetings WHERE status='live' AND agent_ids LIKE ?", (f"%{aid}%",))
    return {"working": bool(running), "task": running[0]["title"] if running else None, "in_meeting": bool(live),
            "last": dict(last) if last else None, "idle_for": (db.now() - last["ts"]) if last else None}


@router.get("/agents/{aid}/calls")
def agent_calls(aid: int):
    rows = db.q("""SELECT c.id,c.status,c.outcome,c.duration,c.summary,c.created_at,k.name contact FROM calls c JOIN campaigns p ON p.id=c.campaign_id
                   LEFT JOIN contacts k ON k.id=c.contact_id WHERE p.agent_id=? ORDER BY c.id DESC LIMIT 30""", (aid,))
    return [dict(r) for r in rows]


# ---------- documents ----------
@router.get("/docs")
def docs(q: str = ""):
    rows = db.q("SELECT d.id,d.title,d.kind,d.updated_at,d.agent_id,g.name author FROM documents d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.title LIKE ? ORDER BY d.updated_at DESC LIMIT 200", (f"%{q}%",))
    return [dict(r) for r in rows]


@router.get("/docs/{did}")
def doc_get(did: int):
    r = db.q1("SELECT d.*, g.name author FROM documents d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.id=?", (did,))
    if not r:
        raise HTTPException(404)
    r = dict(r)
    if r["kind"] == "sheet":
        r["table"] = json.loads(r["body"] or "{}")
    return r


class DocIn(BaseModel):
    title: str
    body: str = ""
    kind: str = "doc"


@router.post("/docs")
def doc_create(d: DocIn):
    return {"id": db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(?,?,?,?,?,?)", (0, d.title[:160], "doc", d.body[:60000], db.now(), db.now()))}


class DocPatch(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    folder_id: Optional[int] = None
    to_root: bool = False


@router.patch("/docs/{did}")
def doc_patch(did: int, p: DocPatch):
    f = {k: v for k, v in p.model_dump().items() if v is not None and k != "to_root"}
    if p.to_root:
        f["folder_id"] = None
    if f:
        db.update("documents", did, updated_at=db.now(), **f)
    return {"ok": True}


class CopyIn(BaseModel):
    owner: str = "shared"
    folder_id: Optional[int] = None


@router.post("/docs/{did}/copy")
def doc_copy(did: int, c: CopyIn):
    """Copy a file to another drive or folder (your own copy stays)."""
    d = db.q1("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        raise HTTPException(404)
    o = _owner(c.owner)
    _check_owner(o)
    return {"id": files.copy_doc(d, o, c.folder_id)}


@router.delete("/docs/{did}")
def doc_delete(did: int):
    db.run("DELETE FROM documents WHERE id=?", (did,))
    return {"ok": True}


@router.get("/docs/{did}/download")
def doc_download(did: int):
    r = db.q1("SELECT * FROM documents WHERE id=?", (did,))
    if not r:
        raise HTTPException(404)
    try:
        name, mime, data = files.download_parts(r)
    except FileNotFoundError:
        raise HTTPException(404, "The file is missing")
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------- calendar ----------
@router.get("/events")
def events(days: int = 30, back: int = 0, agent_id: Optional[int] = None):
    """Planned events from `back` days ago to `days` days ahead. With agent_id: that employee's own and the ones they are invited to."""
    lo = db.now() - 7200 - 86400 * min(max(back, 0), 400)
    w, args = ["e.status='planned'", "e.start>?", "e.start<?"], [lo, db.now() + 86400 * min(days, 180)]
    if agent_id:
        me = db.q1("SELECT name FROM agents WHERE id=?", (agent_id,))
        w.append("(e.agent_id=? OR e.attendees LIKE ?)")
        args += [agent_id, f'%"{me["name"]}"%' if me else "%-none-%"]
    rows = db.q("SELECT e.*, g.name owner FROM events e LEFT JOIN agents g ON g.id=e.agent_id WHERE " + " AND ".join(w) + " ORDER BY e.start", tuple(args))
    return [{**dict(r), "attendees": json.loads(r["attendees"] or "[]")} for r in rows]


@router.get("/agents/{aid}/recent-files")
def recent_files(aid: int, limit: int = 8):
    """The newest files on an employee's own computer (shown on their desktop)."""
    _check_owner(aid)
    rows = db.q("SELECT id,title,kind,updated_at,folder_id,length(body) AS size FROM documents WHERE agent_id=? ORDER BY updated_at DESC LIMIT ?", (aid, max(1, min(limit, 30))))
    return [{**dict(r), "path": files.path_of(r["folder_id"]) if r["folder_id"] else ""} for r in rows]


class EventIn(BaseModel):
    title: str
    start: str
    end: str = ""
    attendees: List[str] = []
    location: str = ""
    notes: str = ""
    agent_id: Optional[int] = None


@router.post("/events")
def event_create(e: EventIn):
    st = schedule.parse_when(e.start)
    if not st:
        raise HTTPException(400, "Enter a valid date and time")
    en = schedule.parse_when(e.end) or st + 1800
    return {"id": db.run("INSERT INTO events(agent_id,title,start,end,location,attendees,notes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (e.agent_id or 0, e.title[:160], st, en, e.location[:120], json.dumps(e.attendees[:10]), e.notes[:1000], "admin", db.now()))}


@router.delete("/events/{eid}")
def event_cancel(eid: int):
    db.update("events", eid, status="cancelled")
    return {"ok": True}


# ---------- playbooks ----------
@router.get("/skills")
def skills_list(agent_id: Optional[int] = None):
    if agent_id:
        rows = db.q("SELECT s.*, g.name owner FROM skills s LEFT JOIN agents g ON g.id=s.agent_id WHERE s.agent_id=? OR s.agent_id IS NULL ORDER BY s.name", (agent_id,))
    else:
        rows = db.q("SELECT s.*, g.name owner FROM skills s LEFT JOIN agents g ON g.id=s.agent_id ORDER BY s.name")
    return [dict(r) for r in rows]


class SkillIn(BaseModel):
    name: str
    description: str = ""
    steps: str
    agent_id: Optional[int] = None  # empty = the whole team


@router.post("/skills")
def skill_create(s: SkillIn):
    sid = db.run("INSERT INTO skills(agent_id,name,description,steps,created_by,created_at) VALUES(?,?,?,?,?,?)", (s.agent_id, s.name[:80], s.description[:400], s.steps[:5000], "admin", db.now()))
    return {"id": sid}


@router.delete("/skills/{sid}")
def skill_delete(sid: int):
    db.run("DELETE FROM skills WHERE id=?", (sid,))
    return {"ok": True}


# ---------- mail settings ----------
class OfficeSettings(BaseModel):
    company_domain: Optional[str] = None
    admin_email: Optional[str] = None
    email_policy: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[str] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    smtp_tls: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[str] = None


KEYS = ["company_domain", "admin_email", "email_policy", "smtp_host", "smtp_port", "smtp_user", "smtp_tls", "imap_host", "imap_port"]


@router.get("/office/settings")
def office_settings():
    out = {k: voices.get_setting(k, "") for k in KEYS}
    out["email_policy"] = out["email_policy"] or "approve"
    out["smtp_pass_set"] = bool(voices.get_setting("smtp_pass", ""))
    return out


@router.put("/office/settings")
def office_settings_put(s: OfficeSettings):
    f = s.model_dump()
    if f.get("email_policy") not in (None, "approve", "auto"):
        raise HTTPException(400, "email_policy must be approve or auto")
    if f.get("admin_email") and not office.EMAIL_RE.match(f["admin_email"]):
        raise HTTPException(400, "Enter a valid email address")
    for k, v in f.items():
        if v is None:
            continue
        if k == "smtp_pass" and not v:
            continue
        voices.set_setting(k, str(v).strip()[:200])
    office.ensure_emails()
    return office_settings()


class TestIn(BaseModel):
    to: str


@router.post("/office/test-email")
async def office_test(t: TestIn):
    try:
        eid, _ = office.queue_email(0, t.to, "Test from your AI team", "If you can read this, outgoing email works.", from_admin=True)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ok = await office.deliver(eid)
    err = (db.q1("SELECT error FROM emails WHERE id=?", (eid,)) or {}).get("error")
    if not ok:
        raise HTTPException(502, err or "Could not send")
    return {"ok": True}

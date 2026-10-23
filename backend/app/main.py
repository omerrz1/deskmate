import hmac
import logging
import json
import math
import os
import re
import mimetypes
import shutil
import time
import traceback

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

import asyncio

from . import access, admin_agent, agent_tools, agents, alerts, apps_api, backups, browser_api, browser_tools, people, people_api, bus, directory, templates, costs, scorecards, search, templates, secretbox, totp, keys, rules, setup_api, meeting_bot, auth, office, office_api, team, team_api, callengine, countries, crm, db, eleven_agents, importer, knowledge, llm, reports, schedule, security, voices, chats, chats_api, chat_hooks, wa_call

app = FastAPI(title="Deskmates", docs_url=None, redoc_url=None, openapi_url=None)  # no public API docs
app.add_middleware(security.Security)
from . import company_workspaces
app.include_router(company_workspaces.router)
app.include_router(office_api.router)
app.include_router(team_api.router)
app.include_router(apps_api.router)
app.include_router(browser_api.router)
app.include_router(people_api.router)
app.include_router(chats_api.router)
app.include_router(chat_hooks.router)
app.include_router(setup_api.router)
from . import voice_library
app.include_router(voice_library.router)
from . import learning
app.include_router(learning.router)
app.add_middleware(CORSMiddleware, allow_origins=sorted(security.ALLOWED_ORIGINS), allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token"])


async def read_limited(f: UploadFile, mb: int, exts=None) -> bytes:
    """Read an upload with a hard size cap and an optional file-type allow-list."""
    name = (f.filename or "").lower()
    if exts and not name.endswith(tuple(exts)):
        raise HTTPException(400, f"Unsupported file type. Allowed: {', '.join(exts)}")
    data = await f.read(mb * 1024 * 1024 + 1)
    if len(data) > mb * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {mb} MB).")
    return data


@app.on_event("startup")
async def _start_scheduler():
    auth.ensure_setup_token()
    keys.migrate_env()
    costs.init()
    rules.init()
    try:  # data (customer details, recordings) is private to this user
        import os
        os.chmod(db.DATA_DIR, 0o700)
        os.chmod(db.DB_PATH, 0o600)
    except OSError:
        pass

    directory.register_main()
    agents.set_loop(asyncio.get_running_loop())
    asyncio.create_task(bus.pump())
    asyncio.create_task(meeting_bot.loop())
    asyncio.create_task(eleven_agents.warm_agents_all())

    async def loop():
        last_purge = {}
        while True:
            for ws in directory.active_ids():  # every company's team gets its turn, each inside its own workspace
                with db.use(ws):
                    try:
                        if keys.complete():  # nothing runs until the company has connected its keys
                            await callengine.tick()
                            await agents.tick()
                            await alerts.tick()
                            await backups.tick()
                        if db.now() - last_purge.get(ws, 0) > 3600:
                            last_purge[ws] = db.now()
                            purge_transcripts()
                            browser_tools.prune_shots()
                    except Exception as e:  # never let the scheduler die
                        try:
                            db.log("error", f"Scheduler: {auth.scrub(e)}")
                        except Exception:
                            pass
            try:
                await browser_tools.tick()
            except Exception:
                pass
            await asyncio.sleep(10)
    asyncio.create_task(loop())


def purge_transcripts():
    """Erase the text of old call transcripts (Settings → Account → Data retention). Summaries stay. 0 = keep."""
    days = int(voices.get_setting("transcript_retention_days", "0") or 0)
    if days > 0:
        n = 0
        for c in db.q("SELECT id FROM calls WHERE ended_at IS NOT NULL AND ended_at<? AND transcript IS NOT NULL AND transcript<>'[]'", (db.now() - days * 86400,)):
            db.update("calls", c["id"], transcript=[])
            n += 1
        if n:
            db.log("security", f"Retention: erased {n} call transcripts older than {days} days")
    # short-lived playback files of the classic voice path: gone after a few minutes
    try:
        for f in db.rec_dir().rglob("*"):
            if f.is_file() and f.stat().st_mtime < time.time() - 900:
                f.unlink()
    except Exception:
        pass


@app.exception_handler(llm.NoKeyError)
async def nokey(_, exc):
    return JSONResponse({"detail": "Connect your OpenAI key in Settings → Connections first.", "setup": "openai"}, status_code=428)


@app.exception_handler(Exception)
async def unhandled(request, exc):
    # never show stack traces or provider error text to the browser; keep details in the server log only
    print("UNHANDLED", request.method, request.url.path, auth.scrub(traceback.format_exc()))
    return JSONResponse({"detail": "Something went wrong on the server."}, status_code=500)


def sse(events):
    async def gen():
        try:
            async for ev in events:
                if ev.get("type") == "error":
                    ev = {**ev, "message": auth.scrub(ev.get("message", ""))[:200]}
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': auth.scrub(e)[:200]})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- meta ----------
@app.get("/api/health")
def health():
    return {"ok": True}  # public liveness check: no configuration details


@app.get("/api/status")
def status():
    return {"ok": True, "openai": llm.has_key(), "setup": keys.state(), "chat_model": llm.CHAT_MODEL, "company": llm.company_name(),
            "agent": callengine.AGENT_NAME, "mode": "simulation", "native_voice": llm.native_provider(), "voice_engine": "elevenlabs_agents" if eleven_agents.enabled() else "openai"}


@app.get("/api/stats")
def stats():
    return crm.stats()


@app.get("/api/stream")
async def live_stream(request: Request):
    """Server-sent events: which tables changed, so open pages refresh at once."""
    return StreamingResponse(bus.stream(request, db.current()), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/api/costs")
def costs_summary(days: int = 14):
    return costs.summary(days)


class CapsIn(BaseModel):
    company_day: Optional[float] = None
    agent_day: Optional[float] = None


@app.put("/api/costs/caps")
def costs_caps(c: CapsIn):
    costs.set_caps(c.company_day, c.agent_day)
    db.log("security", f"Spend caps changed: company ${costs.caps()['company_day']:.2f}/day, per employee ${costs.caps()['agent_day']:.2f}/day")
    return costs.caps()


@app.get("/api/notifications")
def notifications_get():
    return alerts.config()


class NotifyIn(BaseModel):
    email: Optional[bool] = None
    slack: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat: Optional[str] = None
    digest: Optional[str] = None
    digest_hour: Optional[int] = None
    digest_day: Optional[int] = None
    alerts: Optional[List[str]] = None


@app.put("/api/notifications")
def notifications_put(n: NotifyIn):
    try:
        alerts.save(n.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log("security", "Notification settings changed")
    return alerts.config()


@app.post("/api/notifications/test")
async def notifications_test():
    return await alerts.deliver("Deskmates: test message", "If you can read this, this channel works.")


@app.post("/api/notifications/digest-now")
async def notifications_digest(days: int = 1):
    return await alerts.deliver("Deskmates: summary", alerts.digest_text(max(1, min(days, 30))))


@app.get("/api/safety/rules")
def safety_rules_get():
    return {"rules": rules.get_rules(), "types": rules.TYPES}


class RulesIn(BaseModel):
    rules: List[Dict[str, Any]]


@app.put("/api/safety/rules")
def safety_rules_put(r: RulesIn):
    try:
        out = rules.put_rules(r.rules)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log("security", f"Email safety rules changed ({len(out)} rules)")
    return {"rules": out, "types": rules.TYPES}


def _audit_rows(q, kind, limit, before):
    w, a = ["kind='security'"], []
    if q.strip():
        w.append("message LIKE ?"); a.append(f"%{q.strip()}%")
    if before:
        w.append("id<?"); a.append(before)
    if kind == "signins":
        w.append("(message LIKE '%signed in%' OR message LIKE '%signed out%')")
    elif kind == "failed":
        w.append("(message LIKE 'Failed%' OR message LIKE '%blocked%' OR message LIKE '%wrong%')")
    elif kind == "changes":
        w.append("message NOT LIKE '%signed in%' AND message NOT LIKE '%signed out%' AND message NOT LIKE 'Failed%'")
    rows = db.q(f"SELECT id,ts,kind,message,meta FROM activity WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT ?", (*a, max(1, min(limit, 500))))
    for r in rows:
        r["meta"] = db.jload(r["meta"], {})
    return rows


@app.get("/api/audit")
def audit(q: str = "", kind: str = "all", limit: int = 100, before: int = 0):
    """Who did what: sign-ins, key and setting changes, approvals, deletions."""
    return _audit_rows(q, kind, limit, before)


@app.get("/api/audit.csv")
def audit_csv(q: str = "", kind: str = "all"):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "event", "address", "browser"])
    for r in _audit_rows(q, kind, 500, 0):
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["message"], r["meta"].get("ip", ""), r["meta"].get("ua", "")])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit-log.csv"})


@app.get("/api/backups")
def backups_list():
    return {"items": backups.listing(), "settings": backups.settings()}


@app.post("/api/backups")
async def backups_make():
    name = await asyncio.to_thread(backups.create, "manual")
    db.log("security", f"Backup {name} created")
    return {"name": name}


class BackupSettingsIn(BaseModel):
    schedule: Optional[str] = None
    keep: Optional[int] = None


@app.put("/api/backups/settings")
def backups_settings(b: BackupSettingsIn):
    backups.save_settings(b.schedule, b.keep)
    return backups.settings()


class RestoreIn(BaseModel):
    password: str


@app.post("/api/backups/{name}/restore")
async def backups_restore(name: str, b: RestoreIn, request: Request):
    """Needs the admin's password again: a restore replaces everything the company has now."""
    user = request.state.session["username"]
    if not auth.authenticate(user, b.password):
        auth.sec_log(f"Backup restore refused for '{user}': wrong password", request.state.ip, request.state.ua)
        raise HTTPException(403, "That password is not right.")
    try:
        safety = await asyncio.to_thread(backups.restore, name)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    auth.sec_log(f"'{user}' restored the workspace from {name}", request.state.ip, request.state.ua)
    return {"ok": True, "safety_copy": safety}


@app.get("/api/backups/{name}/download")
def backups_download(name: str):
    try:
        p = backups.path_of(name)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, filename=name, media_type="application/octet-stream")


@app.delete("/api/backups/{name}")
def backups_delete(name: str):
    try:
        backups.delete(name)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    db.log("security", f"Backup {name} deleted")
    return {"ok": True}


@app.get("/api/scorecards")
def scorecards_get(days: int = 30):
    return scorecards.all_cards(days)


@app.get("/api/search")
def search_all(request: Request, q: str = ""):
    return search.search(q, person=request.state.session["admin_id"])


@app.get("/api/templates")
def templates_list():
    return templates.listing()


@app.post("/api/templates/teams/{tid}/apply")
async def templates_team(tid: str):
    try:
        made = await templates.apply_team(tid)
        if voices.get_setting("pending_team", "") == tid:
            voices.set_setting("pending_team", "")
        return {"employees": made}
    except KeyError:
        raise HTTPException(404, "No such template")
    except ValueError as e:
        raise HTTPException(400, str(e))


class ProjTplIn(BaseModel):
    members: List[int]
    name: str = ""
    goal: str = ""


@app.post("/api/templates/projects/{pid}/apply")
def templates_project(pid: str, b: ProjTplIn):
    try:
        return {"id": templates.apply_project(pid, b.members, b.name.strip(), b.goal.strip())}
    except KeyError:
        raise HTTPException(404, "No such template")
    except ValueError as e:
        raise HTTPException(400, str(e))


class PlaybookTplIn(BaseModel):
    agent_id: Optional[int] = None


@app.post("/api/templates/playbooks/{bid}/apply")
def templates_playbook(bid: str, b: PlaybookTplIn):
    try:
        return {"added": templates.apply_playbook(bid, b.agent_id)}
    except KeyError:
        raise HTTPException(404, "No such template")


@app.on_event("shutdown")
async def _close_browser():
    from . import files
    files.flush_sheet_updates()  # call results still waiting to be written into their sheets
    await browser_tools.shutdown()


@app.get("/api/browser/settings")
def browser_settings():
    return {"mode": browser_tools.mode(), "logins": browser_tools.list_logins()}


class BrowserModeIn(BaseModel):
    mode: str


@app.put("/api/browser/settings")
def browser_mode_put(b: BrowserModeIn):
    try:
        browser_tools.set_mode(b.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log("security", f"Browser mode set to {b.mode}")
    return browser_settings()


class LoginIn2(BaseModel):
    label: str
    site: str
    username: str
    password: str
    agent_id: Optional[int] = None


@app.post("/api/browser/logins")
def browser_login_add(b: LoginIn2):
    try:
        lid = browser_tools.add_login(b.label, b.site, b.username, b.password, b.agent_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log("security", f"Saved login '{b.label}' added for {b.site}")
    return {"id": lid}


@app.delete("/api/browser/logins/{lid}")
def browser_login_del(lid: int):
    browser_tools.remove_login(lid)
    db.log("security", f"Saved login #{lid} deleted")
    return {"ok": True}


@app.post("/api/browser/test")
async def browser_test():
    try:
        return await browser_tools.test()
    except Exception as e:
        return {"ok": False, "error": auth.scrub(str(e))[:240]}


@app.get("/api/browser/shot/{name}")
def browser_shot(name: str):
    if not re.fullmatch(r"\d+-\d+\.jpg", name):
        raise HTTPException(404)
    p = browser_tools.shots_dir() / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/activity")
def activity(limit: int = 60, kind: Optional[str] = None):
    rows = db.q("SELECT * FROM activity WHERE (?1 IS NULL OR kind=?1) ORDER BY id DESC LIMIT ?2", (kind, limit))
    for r in rows:
        r["meta"] = db.jload(r["meta"], {})
    return rows


# ---------- data ----------
@app.post("/api/datasets/preview")
async def ds_preview(file: UploadFile = File(...)):
    header, body = importer.read_rows(file.filename, await read_limited(file, 15, (".xlsx", ".xls", ".csv")))
    if not header:
        raise HTTPException(400, "File is empty")
    return {"columns": header, "mapping": importer.guess_mapping(header), "rows": len(body), "sample": body[:5]}


@app.post("/api/datasets/import")
async def ds_import(file: UploadFile = File(...), mapping: str = Form("{}"), name: str = Form("")):
    header, body = importer.read_rows(file.filename, await read_limited(file, 15, (".xlsx", ".xls", ".csv")))
    m = {k: v for k, v in json.loads(mapping or "{}").items() if v in header} or importer.guess_mapping(header)
    return importer.import_rows(name or file.filename, file.filename, header, body, m)


@app.get("/api/datasets")
def datasets():
    rows = db.q("SELECT * FROM datasets ORDER BY id DESC")
    for r in rows:
        r["columns"] = db.jload(r["columns"], [])
    return rows


@app.delete("/api/datasets/{ds_id}")
def ds_delete(ds_id: int):
    db.run("DELETE FROM contacts WHERE dataset_id=?", (ds_id,))
    db.run("DELETE FROM datasets WHERE id=?", (ds_id,))
    db.log("dataset", f"Dataset {ds_id} deleted")
    return {"ok": True}


@app.get("/api/segments")
def segs():
    return crm.segments()


@app.get("/api/contacts")
def contacts(search: str = "", language: str = "", status: str = "", last_outcome: str = "", tags: str = "",
             dataset_id: int = 0, limit: int = 100, offset: int = 0):
    f = {k: v for k, v in dict(search=search, language=language, status=status, last_outcome=last_outcome,
                               tags=tags, dataset_id=dataset_id).items() if v}
    where, args = crm.build_filter(f)
    where = where.replace("do_not_call=0", "1=1")
    rows = db.q(f"SELECT * FROM contacts WHERE {where} ORDER BY id LIMIT ? OFFSET ?", args + [limit, offset])
    total = db.q1(f"SELECT COUNT(*) n FROM contacts WHERE {where}", args)["n"]
    return {"total": total, "items": [crm.contact_out(r) for r in rows]}


class SegmentIn(BaseModel):
    filters: Dict[str, Any] = {}


@app.post("/api/segment/count")
def seg_count(body: SegmentIn):
    return {"count": crm.count_contacts(body.filters), "sample": crm.find_contacts(body.filters, limit=5)}


@app.get("/api/contacts/{cid}")
def contact(cid: int):
    c = db.q1("SELECT * FROM contacts WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    out = crm.contact_out(c)
    out["report"] = db.jload(c.get("report"), None)
    out["calls"] = [crm.call_out(x, with_transcript=False) for x in db.q("SELECT * FROM calls WHERE contact_id=? ORDER BY id DESC", (cid,))]
    return out


class ContactPatch(BaseModel):
    language: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    do_not_call: Optional[bool] = None


@app.patch("/api/contacts/{cid}")
def contact_patch(cid: int, p: ContactPatch):
    f = {k: (int(v) if isinstance(v, bool) else v) for k, v in p.dict().items() if v is not None}
    if f.get("language"):
        f["lang_source"] = "manual"
    db.update("contacts", cid, **f)
    db.log("contact", f"Contact {cid} updated", contact_id=cid, **f)
    return {"ok": True}


@app.post("/api/contacts/{cid}/report")
async def contact_report(cid: int):
    return await reports.client_report(cid)


# ---------- campaigns ----------
class CampaignIn(BaseModel):
    name: str
    objective: str
    instructions: str = ""
    filters: Dict[str, Any] = {}
    max_attempts: int = 3
    retry_delay_min: int = 1
    voice: Optional[str] = None
    kb_id: Optional[int] = None
    kb_categories: List[str] = []
    followup: Dict[str, Any] = {}
    call_window: Dict[str, Any] = {}
    start_at: Optional[float] = None
    autopilot: bool = False
    agent_id: Optional[int] = None
    concurrency: int = 10  # calls at once for this campaign, never more than Settings → Calling allows for the whole workspace
    after_sms: str = ""
    after_sms_when: str = "interested"


def campaign_out(r):
    r = dict(r)
    r["filters"] = db.jload(r["filters"], {})
    r["followup"] = schedule.followup_of(r)
    r["call_window"] = schedule.window_of(r)
    r["kb_categories"] = db.jload(r.get("kb_categories"), [])
    r["concurrency"] = int(r.get("concurrency") or 3)
    r["next_call_at"] = (db.q1("SELECT MIN(not_before) t FROM calls WHERE campaign_id=? AND status='queued'", (r["id"],)) or {}).get("t")
    p = db.q1("""SELECT COUNT(*) total, COALESCE(SUM(status='completed'),0) completed, COALESCE(SUM(status='queued'),0) queued,
                 COALESCE(SUM(status IN ('no_answer','declined')),0) missed, COALESCE(SUM(status IN ('ringing','in_progress')),0) live
                 FROM calls WHERE campaign_id=?""", (r["id"],))
    r["progress"] = p
    r["audience"] = crm.count_contacts(r["filters"])
    return r


@app.get("/api/campaigns")
def campaigns():
    return [campaign_out(r) for r in db.q("SELECT * FROM campaigns ORDER BY id DESC")]


@app.post("/api/campaigns")
def campaign_create(c: CampaignIn):
    concurrency = max(1, min(100, int(c.concurrency)))
    cid = db.run("INSERT INTO campaigns(name,objective,instructions,filters,status,max_attempts,retry_delay_min,voice,kb_id,kb_categories,followup,call_window,start_at,autopilot,concurrency,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (c.name, c.objective, c.instructions, json.dumps(c.filters), "draft", c.max_attempts, c.retry_delay_min, c.voice, c.kb_id, json.dumps(c.kb_categories), json.dumps(c.followup), json.dumps(c.call_window), c.start_at, int(c.autopilot), concurrency, db.now()))
    if c.agent_id and agents.get(c.agent_id):
        db.run("UPDATE campaigns SET agent_id=? WHERE id=?", (c.agent_id, cid))
    if c.after_sms_when not in ("never", "interested", "answered"):
        raise HTTPException(400, "after_sms_when must be never, interested or answered")
    db.update("campaigns", cid, after_sms=c.after_sms.strip()[:600], after_sms_when=c.after_sms_when)
    db.log("campaign", f"Campaign '{c.name}' created", campaign_id=cid)
    return campaign_out(db.q1("SELECT * FROM campaigns WHERE id=?", (cid,)))


@app.get("/api/campaigns/{cid}/export.xlsx")
def campaign_export(cid: int):
    from pathlib import Path
    import tempfile
    from openpyxl import Workbook
    campaign = db.q1("SELECT * FROM campaigns WHERE id=?", (cid,))
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    filters = db.jload(campaign.get("filters"), {})
    dataset_id = filters.get("dataset_id")
    if dataset_id:
        audience = db.q1("SELECT COUNT(*) n FROM contacts WHERE dataset_id=?", (dataset_id,))["n"]
        where, args = "dataset_id=?", [dataset_id]
    else:
        where, args = crm.build_filter(filters)
        audience = db.q1(f"SELECT COUNT(*) n FROM contacts WHERE {where}", args)["n"]
    if audience > 1_000_000:
        raise HTTPException(413, "An Excel sheet can contain at most 1,000,000 lead rows. Export a smaller segment.")
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Call results")
    ws.append(["Name", "Phone", "Email", "Company", "Language", "Lead status", "Call status", "Outcome", "Sentiment", "Call summary", "Key points", "Callback / follow-up", "Attempts", "Last called", "Detailed call report"])
    cursor, batch_size = 0, 1000
    while True:
        contacts = db.q(f"SELECT * FROM contacts WHERE ({where}) AND id>? ORDER BY id LIMIT ?", [*args, cursor, batch_size])
        if not contacts:
            break
        ids = [r["id"] for r in contacts]
        marks = ",".join("?" for _ in ids)
        latest = db.q(f"""SELECT c.id,c.contact_id,c.status,c.outcome,c.sentiment,c.summary,c.key_points,c.follow_up,c.attempt,c.ended_at,c.created_at,c.report
            FROM calls c JOIN (SELECT contact_id,MAX(id) id FROM calls WHERE campaign_id=? AND contact_id IN ({marks}) GROUP BY contact_id) x ON x.id=c.id""",
            [cid, *ids])
        by_contact = {r["contact_id"]: r for r in latest}
        for row in contacts:
            call = by_contact.get(row["id"], {})
            report = db.jload(call.get("report"), {})
            report_text = ""
            if report:
                sections = [report.get("executive_summary") or call.get("summary")]
                for title, key in (("Customer needs", "customer_needs"), ("Questions", "questions_and_answers"),
                                   ("Concerns", "objections_or_concerns"), ("Commitments", "commitments"),
                                   ("Follow-up", "follow_up_actions"), ("Risks / gaps", "risks_or_gaps")):
                    values = report.get(key)
                    if values:
                        sections.append(title + ": " + json.dumps(values, ensure_ascii=False))
                report_text = "\n".join(str(s) for s in sections if s)[:32000]
            ws.append([row.get("name"), row.get("phone"), row.get("email"), row.get("company"), row.get("language"), row.get("status"), call.get("status", "not called"), call.get("outcome"), call.get("sentiment"), call.get("summary"), "; ".join(db.jload(call.get("key_points"), [])), call.get("follow_up"), call.get("attempt", 0), call.get("ended_at") or call.get("created_at"), report_text])
        cursor = ids[-1]
    ws.freeze_panes = "A2"
    for col, width in {"A": 24, "B": 18, "C": 28, "D": 24, "F": 16, "G": 16, "H": 22, "J": 55, "K": 40, "L": 22}.items():
        ws.column_dimensions[col].width = width
    fd, temp_name = tempfile.mkstemp(prefix=f"campaign-{cid}-", suffix=".xlsx", dir=db.data_dir())
    os.close(fd)
    try:
        wb.save(temp_name)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    def chunks():
        with open(temp_name, "rb") as f:
            while block := f.read(64 * 1024):
                yield block
    return StreamingResponse(chunks(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="campaign-{cid}-results.xlsx"'},
                             background=BackgroundTask(Path(temp_name).unlink, missing_ok=True))


@app.post("/api/campaigns/{cid}/start")
def campaign_start(cid: int, limit: Optional[int] = None):
    return callengine.queue_campaign(cid, limit, details=True)


@app.post("/api/campaigns/{cid}/simulate")
async def campaign_sim(cid: int, limit: int = 5):
    if not db.q1("SELECT id FROM calls WHERE campaign_id=? AND status='queued'", (cid,)):
        callengine.queue_campaign(cid, limit)
    asyncio.create_task(callengine.autosim_campaign(cid, limit))
    db.log("campaign", f"Headless simulation started for campaign {cid}", campaign_id=cid)
    return {"started": True}


@app.post("/api/campaigns/{cid}/stop")
def campaign_stop(cid: int):
    db.run("UPDATE calls SET status='cancelled' WHERE campaign_id=? AND status='queued'", (cid,))
    db.update("campaigns", cid, status="stopped")
    db.log("campaign", f"Campaign {cid} stopped", campaign_id=cid)
    return {"ok": True}


@app.delete("/api/campaigns/{cid}")
def campaign_delete(cid: int):
    db.run("DELETE FROM campaigns WHERE id=?", (cid,))
    return {"ok": True}


# ---------- calls ----------
@app.get("/api/calls")
def calls(status: str = "", outcome: str = "", contact_id: int = 0, campaign_id: int = 0, limit: int = 100):
    w, a = ["1=1"], []
    for k, v in (("status", status), ("outcome", outcome), ("contact_id", contact_id), ("campaign_id", campaign_id)):
        if v:
            w.append(f"c.{k}=?")
            a.append(v)
    rows = db.q(f"""SELECT c.*, k.name contact_name, k.phone contact_phone, p.name campaign_name FROM calls c
                    LEFT JOIN contacts k ON k.id=c.contact_id LEFT JOIN campaigns p ON p.id=c.campaign_id
                    WHERE {' AND '.join(w)} ORDER BY c.id DESC LIMIT ?""", a + [limit])
    return [crm.call_out(r, with_transcript=False) for r in rows]


@app.get("/api/calls/{call_id:int}")
def call_detail(call_id: int):
    r = db.q1("""SELECT c.*, k.name contact_name, k.phone contact_phone, k.language contact_language, p.name campaign_name, p.objective
                 FROM calls c LEFT JOIN contacts k ON k.id=c.contact_id LEFT JOIN campaigns p ON p.id=c.campaign_id WHERE c.id=?""", (call_id,))
    if not r:
        raise HTTPException(404)
    return crm.call_out(r)


@app.get("/api/recordings/{call_id}/{name}")
def recording(call_id: int, name: str):
    p = (db.rec_dir() / str(call_id) / name).resolve()
    if db.rec_dir().resolve() not in p.parents or not p.exists() or not name.endswith((".mp3", ".webm", ".wav")):
        raise HTTPException(404)
    mt = "audio/webm" if name.endswith(".webm") else (mimetypes.guess_type(name)[0] or "audio/mpeg")
    return FileResponse(p, media_type=mt)


class TestCall(BaseModel):
    contact_id: int
    objective: str = "General check-in call"


@app.post("/api/calls/test")
def test_call(t: TestCall):
    cid = db.run("INSERT INTO campaigns(name,objective,filters,status,max_attempts,created_at) VALUES(?,?,?,?,?,?)",
                 (f"Test call", t.objective, json.dumps({"ids": [t.contact_id]}), "draft", 1, db.now()))
    callengine.queue_campaign(cid)
    return {"campaign_id": cid}


# ---------- mock phone (temporary; replaced by Twilio) ----------
@app.get("/api/phone/poll")
async def phone_poll():
    return callengine.next_for_phone()


@app.post("/api/phone/{call_id}/answer")
async def phone_answer(call_id: int):
    callengine.answer(call_id)
    return callengine.phone_view(call_id)


@app.post("/api/phone/{call_id}/decline")
async def phone_decline(call_id: int):
    callengine.mark_missed(call_id, "declined")
    return {"ok": True}


@app.post("/api/phone/{call_id}/turn")
async def phone_turn(call_id: int, text: str = Form(""), audio: Optional[UploadFile] = File(None)):
    data = await read_limited(audio, 20) if audio else None
    if data is not None and len(data) < 800:
        data = None  # accidental tap, nothing recorded
    name = (audio.filename if audio else None) or "speech.webm"
    return sse(callengine.turn_events(call_id, text=text.strip() or None, audio=data, audio_name=name))


@app.post("/api/phone/{call_id}/realtime")
async def phone_realtime(call_id: int, fallback: int = 0):
    call, contact, camp = callengine.call_context(call_id)
    profile = eleven_agents.profile_for(contact)
    _ag0 = agents.get((camp or {}).get("agent_id")) if (camp or {}).get("agent_id") else None
    if profile and _ag0 and _ag0.get("eleven_voice"):
        profile = {**profile, "voice_id": _ag0["eleven_voice"]}
    if fallback:
        eleven_agents.mark_down(600)  # the browser could not start an ElevenLabs session (out of credits, outage): use OpenAI for a while
    if profile and eleven_agents.is_down():
        fallback_note = "OpenAI voice (ElevenLabs unavailable: out of credits or down)"
    elif profile:
        code = llm.lang_code(contact.get("language"))
        kb = knowledge.get_kb((camp or {}).get("kb_id")) or {}
        agent = profile.get("agent_name") or kb.get("agent_name") or callengine.AGENT_NAME
        prompt = callengine.system_prompt(call, contact, camp, realtime=True, external=True) + eleven_agents.tags_guide(profile)
        try:
            aid = await asyncio.wait_for(eleven_agents.ensure_agent(profile), 12)
            url = await eleven_agents.signed_url(aid)
            ctx = (f"Agent name: {agent}. Organisation: {kb.get('name') or llm.company_name()}. Person: {contact.get('name')} (first name: {(contact.get('name') or '').split()[0] if contact.get('name') else ''}). "
                   f"Reason for the call: {(camp or {}).get('objective') or 'follow up'}. Dialect: {profile.get('dialect')}. "
                   f"Pronunciation (write exactly so): {knowledge.pron_guide(kb.get('id'), contact.get('language')) or 'none'}. Data: {contact.get('extra')}")
            first = eleven_agents.first_line_for(contact)
            return {"engine": "elevenlabs", "signed_url": url, "prompt": prompt, "first_message": first, "language_code": code,
                    "tool_names": ["send_link"] + [n for n in agent_tools.SPECS if n != "send_link"],
                    "voice_id": profile["voice_id"], "tuning": eleven_agents.tuning_for_call(profile),
                    "kb_id": (camp or {}).get("kb_id") or 0, "agent_name": agent,
                    "language": contact.get("language"), "profile": profile["name"],
                    "voice_note": f"ElevenLabs · {profile['name']} · agent {agent}"}
        except Exception as e:
            eleven_agents.mark_down()
            msg = auth.scrub(str(e) or type(e).__name__)[:160]
            db.log("error", f"ElevenLabs agent unavailable, falling back to OpenAI voice: {msg}")
            fallback_note = f"OpenAI Realtime voice (ElevenLabs agent failed: {msg[:100]})"
    else:
        fallback_note = None
    external = llm.use_external_voice(contact.get("language")) and not eleven_agents.enabled()
    voice_note = fallback_note or "OpenAI Realtime voice"
    if external and not llm.FORCE_EXTERNAL:
        ok, why = await llm.voice_probe(contact.get("language"))
        if ok:
            voice_note = why
        else:
            external = False  # keep the better speech-to-speech voice instead of a degraded fallback
            voice_note = f"OpenAI Realtime voice ({why})"
    prompt = callengine.system_prompt(call, contact, camp, realtime=True, external=external)
    _ag = agents.get((camp or {}).get("agent_id")) if (camp or {}).get("agent_id") else None
    default_call_voice = voices.get_setting("call_voice", "marin")
    sess = await llm.realtime_secret(prompt, contact.get("language"), (_ag or {}).get("openai_voice") or (camp or {}).get("voice") or default_call_voice, external)
    sess["external"] = external
    sess["voice_note"] = voice_note
    sess["kb_id"] = (camp or {}).get("kb_id") or 0
    sess["agent_name"] = (knowledge.get_kb((camp or {}).get("kb_id")) or {}).get("agent_name") or callengine.AGENT_NAME
    sess["language"] = contact.get("language")
    sess["greeting"] = "The customer just picked up the phone. Begin the call now with your first turn."
    return sess


# ---------- AI team: agents, duties, meetings ----------
class AgentIn(BaseModel):
    name: str = ""
    gender: str = "female"
    role: str = ""
    personality: str = ""
    persona: str = ""
    bio: str = ""
    openai_voice: Optional[str] = None
    autonomy: str = "full"
    brief: str = ""


@app.get("/api/agents")
def agents_list(request: Request):
    if access.is_member(request.state.session):  # a member sees who is on the AI team, and what they are doing, and nothing else about them
        return [{k: a.get(k) for k in ("id", "name", "role", "color", "gender", "working", "current_task", "is_manager")} for a in agents.listing()]
    return [{k: v for k, v in a.items() if k != "mail_password"} for a in agents.listing()]


@app.post("/api/agents")
async def agents_create(a: AgentIn):
    f = a.model_dump()
    if a.brief.strip() or not a.name.strip():  # let the new agent write itself
        info = await agents.write_profile(a.brief.strip() or "a helpful all-round team member", a.gender, a.name.strip())
        for k in ("name", "role", "personality", "persona", "bio"):
            if not f.get(k):
                f[k] = info.get(k, "")
    try:
        aid = agents.create(f["name"], f["gender"], f["role"], f["personality"], f["persona"], f["openai_voice"], f["autonomy"], f["bio"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log("agent", f"New agent created: {f['name']}")
    return agents.get(aid)


def _agent_or_404(aid):
    a = agents.get(aid)
    if not a:
        raise HTTPException(404, "agent not found")
    return a


@app.get("/api/agents/{aid}")
def agents_get(aid: int):
    a = dict(_agent_or_404(aid))
    a["has_mail_password"] = bool(a.pop("mail_password", ""))
    a["duties"] = [dict(r) for r in db.q("SELECT * FROM duties WHERE agent_id=? ORDER BY (status='done'), id DESC LIMIT 60", (aid,))]
    a["notes"] = [dict(r) for r in db.q("SELECT * FROM agent_notes WHERE agent_id=? ORDER BY id DESC LIMIT 40", (aid,))]
    a["meetings"] = [dict(r) for r in db.q("SELECT id,title,kind,status,created_at,summary FROM meetings WHERE agent_ids LIKE ? ORDER BY id DESC LIMIT 15", (f"%{aid}%",))]
    a["campaigns"] = [dict(r) for r in db.q("SELECT id,name,status FROM campaigns WHERE agent_id=? ORDER BY id DESC", (aid,))]
    return a


class AgentPatch(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    role: Optional[str] = None
    personality: Optional[str] = None
    persona: Optional[str] = None
    bio: Optional[str] = None
    openai_voice: Optional[str] = None
    eleven_voice: Optional[str] = None
    call_name: Optional[str] = None
    autonomy: Optional[str] = None
    color: Optional[str] = None
    email: Optional[str] = None
    signature: Optional[str] = None
    works_alone: Optional[bool] = None
    mail_login: Optional[str] = None
    mail_password: Optional[str] = None
    spend_cap_day: Optional[float] = None
    approve_outside: Optional[bool] = None


@app.patch("/api/agents/{aid}")
def agents_patch(aid: int, p: AgentPatch):
    _agent_or_404(aid)
    f = {k: v for k, v in p.model_dump().items() if v is not None}
    if "gender" in f and f["gender"] not in ("female", "male", "non-binary"):
        raise HTTPException(400, "gender must be female, male or non-binary")
    if "autonomy" in f and f["autonomy"] not in ("full", "ask"):
        raise HTTPException(400, "autonomy must be full or ask")
    if "openai_voice" in f and f["openai_voice"] not in llm.VOICES:
        raise HTTPException(400, "unknown voice")
    if "eleven_voice" in f:
        f["eleven_voice"] = f["eleven_voice"].strip()
        if f["eleven_voice"] and not voice_library.VOICE_ID.fullmatch(f["eleven_voice"]):
            raise HTTPException(400, "That is not a valid ElevenLabs voice ID.")
    if "call_name" in f:
        f["call_name"] = re.sub(r"\s+", " ", f["call_name"]).strip()[:40]
    if "email" in f:
        f["email"] = f["email"].strip().lower()
        if not office.EMAIL_RE.match(f["email"]):
            raise HTTPException(400, "Enter a valid email address")
        if db.q1("SELECT id FROM agents WHERE lower(email)=? AND id<>? AND archived=0", (f["email"], aid)):
            raise HTTPException(400, "Another employee already has that address")
    if f.get("mail_password") == "":
        f.pop("mail_password")
    if "works_alone" in f:
        f["works_alone"] = int(f["works_alone"])
    if "approve_outside" in f:
        f["approve_outside"] = int(f["approve_outside"])
    if "spend_cap_day" in f:
        f["spend_cap_day"] = max(0.0, float(f["spend_cap_day"]))
    if f:
        db.update("agents", aid, **f)
        team.log_event(aid, "profile", "notes", "Profile edited by the admin: " + ", ".join(k for k in f if k != "mail_password"), {k: str(v)[:200] for k, v in f.items() if k not in ("mail_password",)})
    return agents_get(aid)


@app.delete("/api/agents/{aid}")
def agents_delete(aid: int):
    a = _agent_or_404(aid)
    if db.q1("SELECT COUNT(*) n FROM agents WHERE archived=0")["n"] <= 1:
        raise HTTPException(400, "Keep at least one agent")
    db.log("security", f"Employee {a['name']} removed by the admin")
    db.update("agents", aid, archived=1)
    db.run("UPDATE campaigns SET agent_id=NULL WHERE agent_id=?", (aid,))  # their campaigns fall back to the default persona
    db.run("UPDATE duties SET status='done', result=CASE WHEN result='' THEN 'Agent removed' ELSE result END WHERE agent_id=? AND status IN ('todo','doing')", (aid,))
    db.log("agent", f"Agent {a['name']} was removed")
    return {"ok": True}


@app.post("/api/agents/{aid}/duplicate")
def agents_duplicate(aid: int):
    a = _agent_or_404(aid)
    nid = agents.create(f"{a['name']} 2", a["gender"], a["role"], a["personality"], a["persona"], a["openai_voice"], a["autonomy"], a["bio"])
    return agents.get(nid)


class DutyIn(BaseModel):
    title: str
    detail: str = ""
    due: str = ""
    run_now: bool = False


@app.post("/api/agents/{aid}/duties")
def duty_create(aid: int, d: DutyIn):
    _agent_or_404(aid)
    did = db.run("INSERT INTO duties(agent_id,title,detail,due,created_by,created_at) VALUES(?,?,?,?,?,?)", (aid, d.title[:200], d.detail[:2000], d.due[:40], "admin", db.now()))
    if d.run_now:
        agents.start_duty(did)
    return {"id": did}


class DutyPatch(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    detail: Optional[str] = None
    result: Optional[str] = None


@app.patch("/api/duties/{did}")
def duty_patch(did: int, p: DutyPatch):
    f = {k: v for k, v in p.model_dump().items() if v is not None}
    if f.get("status") not in (None, "todo", "doing", "done"):
        raise HTTPException(400, "bad status")
    if f.get("status") == "done":
        f["done_at"] = db.now()
    if f:
        f["attempts"] = 0
        db.update("duties", did, **f)
    return {"ok": True}


@app.post("/api/duties/{did}/run")
def duty_run(did: int):
    if not db.q1("SELECT id FROM duties WHERE id=?", (did,)):
        raise HTTPException(404)
    return {"started": agents.start_duty(did)}


@app.delete("/api/duties/{did}")
def duty_delete(did: int):
    db.run("DELETE FROM duties WHERE id=?", (did,))
    return {"ok": True}


class NoteIn(BaseModel):
    note: str


@app.post("/api/agents/{aid}/notes")
def note_add(aid: int, n: NoteIn):
    _agent_or_404(aid)
    db.run("INSERT INTO agent_notes(agent_id,note,created_at) VALUES(?,?,?)", (aid, n.note[:400], db.now()))
    return {"ok": True}


@app.delete("/api/notes/{nid}")
def note_delete(nid: int):
    db.run("DELETE FROM agent_notes WHERE id=?", (nid,))
    return {"ok": True}


class SpeakIn(BaseModel):
    text: str
    voice: Optional[str] = None


@app.post("/api/agents/{aid}/speak")
async def agent_speak(aid: int, s: SpeakIn):
    a = _agent_or_404(aid)
    text = re.sub(r"\[[^\]]{1,30}\]", "", s.text).strip()
    if not text:
        raise HTTPException(400, "nothing to say")
    return Response(await llm.tts_agent(text[:2000], s.voice if s.voice in llm.REALTIME_VOICES else a["openai_voice"], agents.speak_instructions(a)), media_type="audio/mpeg")


class VoiceTestIn(BaseModel):
    voice: str = "marin"
    text: str = "Hello! How can I help you today?"


@app.post("/api/voices/preview")
async def voice_preview(v: VoiceTestIn):
    if v.voice not in llm.REALTIME_VOICES:
        raise HTTPException(400, "Choose a supported call voice")
    return Response(await llm.tts_agent(v.text[:2000], v.voice, "Speak warmly, clearly and naturally at a relaxed pace."), media_type="audio/mpeg")


@app.post("/api/voices/test")
async def voice_test(v: VoiceTestIn):
    if v.voice not in llm.REALTIME_VOICES:
        raise HTTPException(400, "Choose a supported call voice")
    return await llm.realtime_secret("You are demonstrating a phone voice in a private browser test. Speak warmly and naturally in short turns. Let the user test your pacing, pronunciation and tone. This is a voice test; do not claim to know company facts or perform actions.", voice=v.voice, tools_enabled=False)


class MeetingIn(BaseModel):
    agent_ids: List[int]
    title: str = ""
    chat: bool = False


def _meeting_public(m):
    """Meeting data for the browser: never include an employee's mailbox login or password."""
    if m:
        m["agents"] = [{k: v for k, v in a.items() if k not in ("mail_password", "mail_login")} for a in m["agents"]]
    return m


@app.post("/api/meetings")
def meeting_create(m: MeetingIn):
    try:
        mid = agents.start_meeting(m.agent_ids[:6], m.title[:80], chat=m.chat)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _meeting_public(agents.meeting_detail(mid))


@app.get("/api/meetings")
def meeting_list(chat: bool = False):
    return [dict(r) for r in db.q("SELECT id,title,kind,status,agent_ids,created_at,ended_at,summary FROM meetings WHERE (kind='admin_chat')=? ORDER BY id DESC LIMIT 40", (int(chat),))]


@app.get("/api/meetings/{mid}")
def meeting_get(mid: int):
    m = agents.meeting_detail(mid)
    if not m or m.get("status") == "deleted":
        raise HTTPException(404)
    return _meeting_public(m)


class MeetingTitle(BaseModel):
    title: str


@app.patch("/api/meetings/{mid}")
def meeting_rename(mid: int, body: MeetingTitle):
    row = db.q1("SELECT kind FROM meetings WHERE id=?", (mid,))
    title = re.sub(r"\s+", " ", body.title).strip()[:80]
    if not row or row["kind"] != "admin_chat":
        raise HTTPException(404, "chat not found")
    if not title:
        raise HTTPException(400, "Give this chat a name.")
    db.update("meetings", mid, title=title)
    return {"id": mid, "title": title}


@app.delete("/api/meetings/{mid}")
def meeting_delete(mid: int):
    row = db.q1("SELECT kind FROM meetings WHERE id=?", (mid,))
    if not row or row["kind"] != "admin_chat":
        raise HTTPException(404, "chat not found")
    conn, lock = db._get()
    with lock:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM meeting_msgs WHERE meeting_id=?", (mid,))
        conn.execute("DELETE FROM meetings WHERE id=? AND kind='admin_chat'", (mid,))
        conn.commit()
    return {"ok": True}


class RTIn(BaseModel):
    agent_id: int


@app.post("/api/meetings/{mid}/realtime")
async def meeting_realtime(mid: int, body: RTIn):
    m = agents.meeting_detail(mid)
    if not m or m["status"] != "live" or m["kind"] == "admin_chat":
        raise HTTPException(404, "meeting not found or ended")
    ag = next((a for a in m["agents"] if a["id"] == body.agent_id), None)
    if not ag:
        raise HTTPException(400, "that agent is not in this meeting")
    group = m["kind"] != "one_to_one"
    listener = (not group) or m["agents"][0]["id"] == ag["id"]
    prompt = agents.meeting_prompt(ag, m["agents"], m["kind"], live=True)
    s = await llm.realtime_meeting_secret(prompt, ag["openai_voice"], agents.realtime_tools(ag), listener=listener, auto_reply=False)
    costs.open_voice_session(mid, ag["id"], listener)
    return {**s, "agent_id": ag["id"], "listener": listener, "group": group}


class MToolIn(BaseModel):
    agent_id: int
    name: str
    args: Dict[str, Any] = {}


@app.post("/api/meetings/{mid}/tool")
async def meeting_tool(mid: int, body: MToolIn):
    m = agents.meeting_detail(mid)
    ag = next((a for a in (m or {}).get("agents", []) if a["id"] == body.agent_id), None)
    if not m or m["status"] != "live" or not ag:
        raise HTTPException(404, "meeting not active")
    if body.name not in {t["name"] for t in agents.tools_for(ag)}:
        raise HTTPException(400, "unknown tool")
    gate = agents.meeting_gate(mid, body.name, body.args)
    try:
        res, link = (gate, None) if gate else await agents.run_agent_tool({**ag, "live_admin": True, "_meeting_id": mid}, body.name, body.args)
    except Exception as e:
        res, link = {"error": f"{type(e).__name__}"}, None
    db.log("agent", f"{ag['name']} (live meeting) used {body.name}")
    return {"result": json.dumps(res, ensure_ascii=False, default=str)[:7000], "link": link, "ok": "error" not in (res if isinstance(res, dict) else {})}


class MLogIn(BaseModel):
    role: str
    agent_id: Optional[int] = None
    text: str
    tools: List[Dict[str, Any]] = []


@app.post("/api/meetings/{mid}/log")
def meeting_log(mid: int, b: MLogIn):
    m = db.q1("SELECT status FROM meetings WHERE id=?", (mid,))
    if not m or m["status"] != "live":
        raise HTTPException(404)
    if b.text.strip():
        agents.log_line(mid, "admin" if b.role == "admin" else "agent", b.agent_id if b.role != "admin" else None, b.text.strip(), b.tools[:8])
    return {"ok": True}


class RouteIn(BaseModel):
    text: str


@app.post("/api/meetings/{mid}/route")
async def meeting_route(mid: int, r: RouteIn):
    """Who is being spoken to? (the live voice room asks this before choosing which colleague answers)"""
    from . import addressing
    m = agents.meeting_detail(mid)
    if not m or m["status"] != "live":
        raise HTTPException(404, "meeting not found or ended")
    last_id, last_ts = addressing.last_agent_message(mid)
    picked, why = await addressing.decide(r.text[:1000], m["agents"], last_id, last_ts, addressing.recent_text(mid))
    return {"agent_ids": [a["id"] for a in picked], "why": why}


class MeetingMsg(BaseModel):
    text: str


class MeetingAttachmentIn(BaseModel):
    doc_id: int


@app.post("/api/meetings/{mid}/attachments")
async def meeting_attach(mid: int, body: MeetingAttachmentIn):
    """Save shared chat files on each participating employee computer."""
    from . import files
    meeting = db.q1("SELECT kind,status,agent_ids FROM meetings WHERE id=?", (mid,))
    if not meeting or meeting["kind"] != "admin_chat" or meeting["status"] != "live":
        raise HTTPException(404, "chat not found or ended")
    doc = db.q1("SELECT * FROM documents WHERE id=?", (body.doc_id,))
    if not doc or doc["agent_id"] != 0:
        raise HTTPException(404, "file not found in the shared drive")
    copies = {}
    for aid in db.jload(meeting["agent_ids"], []):
        if not db.q1("SELECT id FROM agents WHERE id=? AND archived=0", (aid,)):
            continue
        existing = db.q1("SELECT c.doc_id FROM chat_file_copies c JOIN documents d ON d.id=c.doc_id WHERE c.meeting_id=? AND c.source_doc_id=? AND c.agent_id=?", (mid, doc["id"], aid))
        if existing:
            copied = existing["doc_id"]
        else:
            folder = files.folder_id(aid, f"From admins/Chat {mid}")
            copied = files.copy_doc(doc, aid, folder)
            db.run("INSERT OR REPLACE INTO chat_file_copies(meeting_id,source_doc_id,agent_id,doc_id) VALUES(?,?,?,?)", (mid, doc["id"], aid, copied))
        copies[str(aid)] = copied
    instruction_project = None
    if copies and re.search(r"\b(instructions?|brief|requirements|playbook)\b", doc["title"], re.I):
        from . import project_briefs
        owner_id = int(next(iter(copies)))
        owner = agents.get(owner_id)
        chat = db.q1("SELECT title FROM meetings WHERE id=?", (mid,))
        instruction_project = project_briefs.organize(owner, doc["id"], f"{(chat or {}).get('title') or 'Chat'} — {doc['title']}")
        if instruction_project.get("project_id"):
            for participant in copies:
                db.run("INSERT OR IGNORE INTO project_members(project_id,agent_id,role,joined_at) VALUES(?,?,'member',?)", (instruction_project["project_id"], int(participant), db.now()))
    for message in db.q("SELECT attachments FROM meeting_msgs WHERE meeting_id=? AND role='admin' AND attachments<>'[]'", (mid,)):
        for attached in db.jload(message["attachments"], []):
            if attached.get("doc_id") == doc["id"]:
                return {"ok": True, "attachment": {**attached, "agent_copies": copies, "instruction_project": instruction_project}, "already_attached": True}
    attachment = {"doc_id": doc["id"], "title": doc["title"], "kind": doc["kind"], "mime": doc["mime"] or "", "size": len(doc["body"] or ""), "agent_copies": copies, "saved_in": "From admins/Chat " + str(mid), "instruction_project": instruction_project}
    db.run("INSERT INTO meeting_msgs(meeting_id,role,text,tools,attachments,created_at) VALUES(?,?,?,?,?,?)",
           (mid, "admin", "", "[]", json.dumps([attachment], ensure_ascii=False), db.now()))
    return {"ok": True, "attachment": attachment}


@app.post("/api/meetings/{mid}/message")
async def meeting_message(mid: int, body: MeetingMsg):
    if not body.text.strip():
        raise HTTPException(400, "empty message")
    meeting = agents.meeting_detail(mid)
    if not meeting or meeting.get("kind") != "admin_chat" or meeting.get("status") != "live":
        raise HTTPException(404, "chat not found")
    if meeting.get("title") == "New chat":
        first_line = re.sub(r"\s+", " ", body.text.strip()).strip()[:80]
        if first_line:
            db.run("UPDATE meetings SET title=? WHERE id=? AND title='New chat'", (first_line, mid))
    llm.client()
    return sse(agents.meeting_message(mid, body.text.strip()))


@app.post("/api/meetings/{mid}/end")
async def meeting_end(mid: int):
    m = await agents.end_meeting(mid)
    if not m:
        raise HTTPException(404)
    return _meeting_public(m)


class WebsiteIn(BaseModel):
    url: str
    name: str = "Alex"
    text: str = ""


@app.post("/api/demo/website")
async def demo_website(body: WebsiteIn):
    from . import webdemo
    url = body.url.strip()[:500]
    if not url or " " in url:
        raise HTTPException(400, "Paste a website address, like https://example.com")
    try:
        return await asyncio.wait_for(webdemo.build(url, body.name.strip()[:40] or "Alex", body.text[:60000]), 90)
    except webdemo.Blocked as e:
        raise HTTPException(400, str(e))
    except asyncio.TimeoutError:
        raise HTTPException(504, "That website took too long to read.")
    except Exception as e:
        raise HTTPException(502, "I couldn't read that website: " + auth.scrub(type(e).__name__))


class ToolCall(BaseModel):
    name: str
    args: Dict[str, Any] = {}


@app.post("/api/phone/{call_id}/tool")
async def phone_tool(call_id: int, body: ToolCall):
    if body.name not in agent_tools.SPECS:
        raise HTTPException(400, "unknown tool")
    c = db.q1("SELECT status FROM calls WHERE id=?", (call_id,))
    if not c or c["status"] != "in_progress":
        raise HTTPException(409, "call is not active")
    return {"result": await agent_tools.arun(call_id, body.name, body.args)}


class Debug(BaseModel):
    event: str
    detail: str = ""


@app.post("/api/phone/{call_id}/debug")
def phone_debug(call_id: int, body: Debug):
    db.log("voice", f"[call {call_id}] {body.event[:40]} {auth.scrub(body.detail)[:300]}", call_id=call_id)
    return {"ok": True}


class Lines(BaseModel):
    lines: List[Dict[str, Any]]


@app.post("/api/phone/{call_id}/transcript")
async def phone_transcript(call_id: int, body: Lines):
    clean = [{"role": "agent" if l.get("role") == "agent" else "customer", "text": str(l.get("text", "")).strip(),
              "t": float(l.get("t", 0)), "audio": []} for l in body.lines if str(l.get("text", "")).strip()]
    db.update("calls", call_id, transcript=clean)
    return {"ok": True, "lines": len(clean)}


class ElevenSync(BaseModel):
    conversation_id: str


@app.post("/api/phone/{call_id}/eleven-sync")
async def phone_eleven_sync(call_id: int, body: ElevenSync):
    """Pull the transcript (text only) of an ElevenLabs conversation into our own call record."""
    transcript, _ = await eleven_agents.fetch_conversation(body.conversation_id)
    if transcript:
        db.update("calls", call_id, transcript=transcript)
    return {"lines": len(transcript)}


@app.post("/api/phone/{call_id}/hangup")
async def phone_hangup(call_id: int):
    r = await callengine.finalize(call_id, "completed")
    return crm.call_out(r, with_transcript=False)


# ---------- admin copilot ----------
class ChatIn(BaseModel):
    messages: List[Dict[str, Any]]


@app.post("/api/chat")
async def chat(body: ChatIn):
    llm.client()  # raises NoKeyError early
    hist = [{"role": m["role"], "content": m["content"]} for m in body.messages if m.get("role") in ("user", "assistant") and m.get("content")]
    return sse(admin_agent.chat_events(hist[-24:]))


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    data = await read_limited(audio, 20)
    if len(data) < 800:
        return {"text": ""}
    return {"text": await llm.stt(data, audio.filename or "speech.webm")}


class TTSIn(BaseModel):
    text: str
    voice: Optional[str] = None
    language: Optional[str] = None


@app.post("/api/tts")
async def speak(body: TTSIn):
    return Response(await llm.tts(body.text, body.voice, body.language), media_type="audio/mpeg")


# ---------- knowledge bases ----------
class KB(BaseModel):
    name: str = ""
    agent_name: str = ""
    persona: str = ""
    context: str = ""
    key_facts: str = ""
    next_steps: str = ""
    contact: str = ""


@app.get("/api/kb")
def kb_list():
    return knowledge.list_kbs()


@app.post("/api/kb")
def kb_create(k: KB):
    kid = db.run("INSERT INTO knowledge_bases(name,agent_name,persona,context,key_facts,next_steps,contact,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (k.name or "New knowledge base", k.agent_name or callengine.AGENT_NAME, k.persona or knowledge.DEFAULT_PERSONA,
                  k.context, k.key_facts, k.next_steps, k.contact, db.now()))
    db.log("knowledge", f"Knowledge base '{k.name}' created", kb_id=kid)
    return knowledge.kb_detail(kid)


@app.get("/api/kb/{kid}")
def kb_get(kid: int):
    kb = knowledge.kb_detail(kid)
    if not kb:
        raise HTTPException(404)
    return kb


@app.put("/api/kb/{kid}")
def kb_update(kid: int, k: KB):
    db.update("knowledge_bases", kid, name=k.name, agent_name=k.agent_name, persona=k.persona, context=k.context,
              key_facts=k.key_facts, next_steps=k.next_steps, contact=k.contact)
    db.log("knowledge", f"Knowledge base '{k.name}' updated", kb_id=kid)
    return knowledge.kb_detail(kid)


@app.delete("/api/kb/{kid}")
def kb_delete(kid: int):
    db.run("DELETE FROM kb_docs WHERE kb_id=?", (kid,))
    db.run("DELETE FROM knowledge_bases WHERE id=?", (kid,))
    db.run("UPDATE campaigns SET kb_id=NULL WHERE kb_id=?", (kid,))
    return {"ok": True}


class KBDoc(BaseModel):
    title: str
    text: str
    kind: str = "info"
    category: Optional[str] = None


@app.post("/api/kb/{kid}/docs")
async def kb_add_doc(kid: int, d: KBDoc):
    if d.category:
        knowledge.add_doc(kid, d.title, d.text, d.kind, d.category, d.title)
        n = 1
    else:
        n = await knowledge.ingest(kid, d.title, d.text)
    db.log("knowledge", f"Added '{d.title}' to knowledge base {kid} ({n} sections)", kb_id=kid)
    return {"ok": True, "sections": n}


@app.post("/api/kb/{kid}/upload")
async def kb_upload(kid: int, files: List[UploadFile] = File(...)):
    done = []
    for f in files:
        text = knowledge.extract_text(f.filename, await read_limited(f, 15, (".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx"))).strip()
        if not text:
            continue
        n = await knowledge.ingest(kid, f.filename.rsplit(".", 1)[0], text, f.filename)
        done.append({"file": f.filename, "sections": n})
        db.log("knowledge", f"Uploaded {f.filename} to knowledge base {kid} ({n} sections)", kb_id=kid)
    if not done:
        raise HTTPException(400, "Could not read any text from those files")
    return {"ok": True, "files": done}


class DocPatch(BaseModel):
    category: Optional[str] = None
    title: Optional[str] = None


@app.patch("/api/kb/{kid}/docs/{doc_id}")
def kb_patch_doc(kid: int, doc_id: int, p: DocPatch):
    f = {k: v for k, v in p.dict().items() if v}
    if f.get("category") and f["category"] not in knowledge.CATEGORIES:
        raise HTTPException(400, "unknown category")
    db.update("kb_docs", doc_id, **f)
    return {"ok": True}


@app.post("/api/kb/{kid}/resort")
async def kb_resort(kid: int):
    docs = db.q("SELECT id,title,body FROM kb_docs WHERE kb_id=?", (kid,))
    cats = await knowledge.classify([(d["title"], d["body"]) for d in docs])
    for d, c in zip(docs, cats):
        db.update("kb_docs", d["id"], category=c)
    return {"ok": True, "docs": len(docs)}


@app.get("/api/kb/{kid}/docs/{doc_id}")
def kb_doc(kid: int, doc_id: int):
    d = db.q1("SELECT id,title,kind,body,source FROM kb_docs WHERE id=? AND kb_id=?", (doc_id, kid))
    if not d:
        raise HTTPException(404)
    return d


@app.delete("/api/kb/{kid}/docs/{doc_id}")
def kb_del_doc(kid: int, doc_id: int):
    db.run("DELETE FROM kb_docs WHERE id=? AND kb_id=?", (doc_id, kid))
    return {"ok": True}


class PronRow(BaseModel):
    term: str
    language: str = "English"
    respelling: str


@app.get("/api/kb/{kid}/pronunciations")
def kb_pron_get(kid: int):
    return knowledge.pronunciations(kid)


@app.put("/api/kb/{kid}/pronunciations")
def kb_pron_put(kid: int, rows: List[PronRow]):
    knowledge.set_pronunciations(kid, [r.dict() for r in rows])
    return knowledge.pronunciations(kid)


class KnowQ(BaseModel):
    kb_id: int = 0
    query: str
    call_id: Optional[int] = None
    category: Optional[str] = None


@app.post("/api/knowledge/search")
async def know_search(q: KnowQ):
    if q.call_id:
        return {"results": await knowledge.lookup_company(q.query, call_id=q.call_id)}
    cats = None
    if q.call_id:
        row = db.q1("SELECT p.kb_categories FROM calls c JOIN campaigns p ON p.id=c.campaign_id WHERE c.id=?", (q.call_id,))
        cats = db.jload((row or {}).get("kb_categories"), None) or None
    hits = knowledge.search(q.kb_id, q.query, categories=cats, category=q.category)
    if not hits:
        return {"results": ["NOTHING FOUND. Do not guess. Say you're not sure and a colleague will confirm."]}
    # flag question terms that appear nowhere in the retrieved text, so the agent doesn't assume an answer
    import re as _re
    stop = {"what", "does", "have", "with", "your", "about", "that", "this", "there", "their", "much", "many", "when", "which", "from", "tell", "need", "year", "per"}
    terms = [t for t in _re.findall(r"[a-z]{4,}", q.query.lower()) if t not in stop]
    blob = " ".join(hits).lower()
    missing = [t for t in terms if t[:5] not in blob]
    if missing:
        hits = hits + [f"NOTE: the knowledge base has nothing about: {', '.join(missing)}. Do NOT assume or guess about those; say you're not sure and a colleague will confirm."]
    return {"results": hits}


# ---------- scheduled calls ----------
@app.get("/api/calls/scheduled")
def calls_scheduled():
    rows = db.q("""SELECT c.id, c.not_before, c.reason, c.attempt, c.campaign_id, c.contact_id, k.name contact_name, k.phone contact_phone,
                   k.language, p.name campaign_name, p.status campaign_status, p.call_window
                   FROM calls c LEFT JOIN contacts k ON k.id=c.contact_id LEFT JOIN campaigns p ON p.id=c.campaign_id
                   WHERE c.status='queued' ORDER BY c.not_before, c.id LIMIT 300""")
    t = db.now()
    for r in rows:
        r["due"] = r["not_before"] <= t
        r["waiting_for"] = ("campaign start" if r["campaign_status"] == "scheduled" else
                            "calling hours" if not schedule.in_window(schedule.window_of({"call_window": r["call_window"]}), t) else None) if r["due"] else None
    return rows


class ScheduleIn(BaseModel):
    contact_id: int
    at: float
    campaign_id: Optional[int] = None
    objective: str = "Follow up with the customer."
    kb_id: Optional[int] = None


@app.post("/api/calls/schedule")
def calls_schedule(s: ScheduleIn):
    cid = s.campaign_id
    if not cid:
        cid = db.run("INSERT INTO campaigns(name,objective,filters,status,max_attempts,kb_id,created_at) VALUES(?,?,?,?,?,?,?)",
                     ("Scheduled call", s.objective, json.dumps({"ids": [s.contact_id]}), "active", 2, s.kb_id, db.now()))
    camp = db.q1("SELECT * FROM campaigns WHERE id=?", (cid,))
    c = db.q1("SELECT * FROM contacts WHERE id=?", (s.contact_id,))
    if not c:
        raise HTTPException(404, "contact not found")
    at = schedule.clamp(camp, max(s.at, db.now()))
    n = db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND contact_id=?", (cid, s.contact_id))["n"]
    call_id = db.run("INSERT INTO calls(contact_id,campaign_id,attempt,status,language,created_at,not_before,reason) VALUES(?,?,?,?,?,?,?,?)",
                     (s.contact_id, cid, n + 1, "queued", c["language"], db.now(), at, "scheduled"))
    db.log("call", f"Call to {c['name']} scheduled for {schedule.describe(at)}", call_id=call_id, contact_id=s.contact_id)
    return {"call_id": call_id, "at": at, "adjusted": at != s.at}


class ResIn(BaseModel):
    at: float


@app.post("/api/calls/{call_id}/reschedule")
def call_reschedule(call_id: int, r: ResIn):
    call = db.q1("SELECT * FROM calls WHERE id=? AND status='queued'", (call_id,))
    if not call:
        raise HTTPException(404, "not a scheduled call")
    camp = db.q1("SELECT * FROM campaigns WHERE id=?", (call["campaign_id"],)) if call["campaign_id"] else None
    at = schedule.clamp(camp, r.at)
    db.update("calls", call_id, not_before=at)
    db.log("call", f"Call {call_id} rescheduled to {schedule.describe(at)}", call_id=call_id)
    return {"at": at}


@app.post("/api/calls/{call_id}/now")
def call_now(call_id: int):
    db.run("UPDATE calls SET not_before=? WHERE id=? AND status='queued'", (db.now(), call_id))
    camp = db.q1("SELECT c.* FROM campaigns c JOIN calls k ON k.campaign_id=c.id WHERE k.id=?", (call_id,))
    if camp and camp["status"] == "scheduled":
        db.update("campaigns", camp["id"], status="active")
    return {"ok": True}


@app.post("/api/calls/{call_id}/cancel")
def call_cancel(call_id: int):
    db.run("UPDATE calls SET status='cancelled' WHERE id=? AND status='queued'", (call_id,))
    return {"ok": True}


# ---------- settings, voice profiles, countries ----------
@app.get("/api/countries")
def countries_list():
    return countries.listing()


@app.get("/api/settings")
def settings_get():
    return {"language_source": voices.get_setting("language_source", "smart"),
            "voice_engine": voices.get_setting("voice_engine", "elevenlabs"),
            "call_voice": voices.get_setting("call_voice", "marin"),
            "max_concurrent_calls": callengine.concurrency_limit(),
            "english_only": voices.english_only(),
            "main_link": voices.get_setting("main_link", ""),
            "call_instructions": voices.get_setting("call_instructions", ""),
            "call_expression": voices.get_setting("call_expression", eleven_agents.EXPRESSION_DEFAULT),
            "transcript_retention_days": int(voices.get_setting("transcript_retention_days", "0") or 0)}


class SettingsIn(BaseModel):
    language_source: Optional[str] = None
    transcript_retention_days: Optional[int] = None
    voice_engine: Optional[str] = None
    call_voice: Optional[str] = None
    english_only: Optional[bool] = None
    main_link: Optional[str] = None
    call_instructions: Optional[str] = None
    call_expression: Optional[str] = None
    max_concurrent_calls: Optional[int] = None


@app.put("/api/settings")
def settings_put(s: SettingsIn, request: Request):
    if s.max_concurrent_calls is not None:
        if not 1 <= s.max_concurrent_calls <= 100:
            raise HTTPException(400, "Maximum simultaneous calls must be between 1 and 100.")
        voices.set_setting("max_concurrent_calls", str(s.max_concurrent_calls))
    if s.language_source is not None:
        if s.language_source not in ("smart", "phone", "sheet"):
            raise HTTPException(400, "language_source must be smart, phone or sheet")
        voices.set_setting("language_source", s.language_source)
    if s.main_link is not None:
        link = s.main_link.strip()
        if link and not re.match(r"^https?://[^\s]{4,300}$", link):
            raise HTTPException(400, "Enter a full link starting with https://")
        voices.set_setting("main_link", link)
    if s.call_expression is not None:
        if s.call_expression not in ("subtle", "natural", "lively"):
            raise HTTPException(400, "call_expression must be subtle, natural or lively")
        voices.set_setting("call_expression", s.call_expression)
    if s.call_instructions is not None:
        voices.set_setting("call_instructions", s.call_instructions.strip()[:4000])
    if s.english_only is not None:
        voices.set_setting("english_only", "1" if s.english_only else "0")
    if s.voice_engine is not None:
        if s.voice_engine not in ("openai", "elevenlabs"):
            raise HTTPException(400, "voice_engine must be openai or elevenlabs")
        voices.set_setting("voice_engine", s.voice_engine)
    if s.call_voice is not None:
        if s.call_voice not in llm.REALTIME_VOICES:
            raise HTTPException(400, "call_voice must be a supported OpenAI Realtime voice")
        voices.set_setting("call_voice", s.call_voice)
    if s.transcript_retention_days is not None:
        if not (0 <= s.transcript_retention_days <= 3650):
            raise HTTPException(400, "Retention must be between 0 (keep forever) and 3650 days")
        voices.set_setting("transcript_retention_days", str(s.transcript_retention_days))
        auth.sec_log(f"Transcript retention set to {s.transcript_retention_days} days", request.state.ip, request.state.ua)
    return settings_get()


class ProfileIn(BaseModel):
    name: str
    language: str
    countries: List[str] = []
    voice_id: str = ""
    agent_name: str = ""
    dialect: str = ""
    tuning: Dict[str, Any] = {}


def _clean_tuning(t):
    """Only keep known, in-range knobs: pace (speed), expressiveness (stability), response speed (turn eagerness)."""
    out = {}
    if t.get("speed") not in (None, ""):
        out["speed"] = max(0.7, min(1.2, float(t["speed"])))
    if t.get("stability") not in (None, ""):
        out["stability"] = max(0.0, min(1.0, float(t["stability"])))
    if t.get("turn_eagerness") in ("patient", "normal", "eager"):
        out["turn_eagerness"] = t["turn_eagerness"]
    return out


@app.get("/api/voice-profiles")
def profiles_list():
    return voices.profiles()


@app.post("/api/voice-profiles")
def profiles_create(p: ProfileIn):
    pid = db.run("INSERT INTO voice_profiles(name,language,countries,voice_id,agent_name,dialect,tuning,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (p.name, p.language, json.dumps(p.countries), p.voice_id, p.agent_name, p.dialect, json.dumps(_clean_tuning(p.tuning)), db.now()))
    return {"id": pid}


@app.put("/api/voice-profiles/{pid}")
async def profiles_update(pid: int, p: ProfileIn):
    before = db.q1("SELECT language FROM voice_profiles WHERE id=?", (pid,))
    db.update("voice_profiles", pid, name=p.name, language=p.language, countries=p.countries, voice_id=p.voice_id,
              agent_name=p.agent_name, dialect=p.dialect, tuning=_clean_tuning(p.tuning))
    if before and before["language"] != p.language:
        db.run("DELETE FROM el_agents WHERE code=?", (f"p{pid}",))  # the agent's language changed: build a new one on the next call
    else:
        asyncio.create_task(eleven_agents.apply_tuning(pid))  # background: ElevenLabs can be slow; the voice itself is chosen per call
    return {"ok": True}


@app.delete("/api/voice-profiles/{pid}")
def profiles_delete(pid: int):
    db.run("DELETE FROM voice_profiles WHERE id=?", (pid,))
    return {"ok": True}


@app.get("/api/voices/search")
async def voices_search(language: str = "", q: str = "", gender: str = "female", accent: str = ""):
    """Browse the ElevenLabs voice library (with playable previews) so voices can be picked for a profile."""
    if not llm.eleven_key():
        raise HTTPException(400, "No ElevenLabs key configured")
    import httpx
    params = {"page_size": 24, "sort": "usage_character_count_1y"}
    if language:
        params["language"] = llm.lang_code(language) or language
    if gender:
        params["gender"] = gender
    if q:
        params["search"] = q
    if accent:
        params["accent"] = accent
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.get("https://api.elevenlabs.io/v1/shared-voices", headers={"xi-api-key": llm.eleven_key()}, params=params)
    if r.status_code != 200:
        raise HTTPException(502, r.text[:200])
    return [{"voice_id": v["voice_id"], "name": v["name"], "accent": v.get("accent"), "age": v.get("age"), "descriptive": v.get("descriptive"),
             "preview_url": v.get("preview_url"), "use_case": v.get("use_case")} for v in r.json().get("voices", [])]


@app.post("/api/contacts/detect-languages")
def detect_languages():
    """Re-detect each customer's language from their phone number (manual overrides are kept)."""
    n = 0
    for c in db.q("SELECT id,phone,language,lang_source FROM contacts"):
        info = countries.lookup(c["phone"])
        db.update("contacts", c["id"], country=info["country"] if info else "")
        if c["lang_source"] == "manual":
            continue
        lang, src = voices.choose_language(c["phone"], c["language"] if c["lang_source"] in ("sheet", None) else "", c["language"])
        if lang != c["language"] or src != c["lang_source"]:
            db.update("contacts", c["id"], language=lang, lang_source=src)
            n += 1
    db.log("contact", f"Language detection from phone numbers updated {n} customers")
    return {"updated": n}


# ---------- admin sign-in ----------
class LoginIn(BaseModel):
    username: str
    password: str
    code: str = ""


class SetupIn(BaseModel):
    token: str
    username: str
    password: str


class PasswordIn(BaseModel):
    current: str
    new: str


def _with_cookies(content, token, csrf, status_code=200):
    r = JSONResponse(content, status_code=status_code)
    for c in auth.cookie_headers(token, csrf):
        r.headers.append("set-cookie", c)
    return r


@app.get("/api/auth/status")
def auth_status(request: Request):
    sess = auth.session_from_cookie(request.headers.get("cookie"))
    ws = None
    if sess:
        w = directory.get(sess["ws"]) or {}
        ws = {"id": sess["ws"], "name": w.get("name", "")}
    return {"needs_setup": False, "signup_open": directory.SIGNUPS_OPEN, "signup_code_required": bool(directory.SIGNUP_CODE),
            "signup_public": directory.SIGNUPS_OPEN and not directory.SIGNUP_CODE,  # whether the public site should offer "create a workspace" at all
            "contact_email": os.getenv("CONTACT_EMAIL", "").strip(), "authenticated": bool(sess), "username": sess["username"] if sess else None,
            "role": sess["role"] if sess else None, "must_change": bool(sess and sess["must_change"]), "workspace": ws, "subworkspace": bool(sess and sess.get("company_ws")),
            "name": people.display_name(sess) if sess else None, "title": (sess.get("title") or "") if sess else "",
            "platform": bool(sess and sess["ws"] == db.MAIN and sess["role"] == "owner")}


class SignupIn(BaseModel):
    company: str
    email: str
    password: str
    team: str = ""
    code: str = ""  # the team's access code, when the server asks for one


EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,200}\.[A-Za-z]{2,}$")


@app.post("/api/auth/signup")
def auth_signup(b: SignupIn, request: Request):
    """Anyone can create a company workspace: its own team, files, keys and apps, kept apart from every other company."""
    ip, ua = request.state.ip, request.state.ua
    if not directory.SIGNUPS_OPEN:
        raise HTTPException(403, "Sign-ups are closed on this server.")
    if directory.SIGNUP_CODE and not hmac.compare_digest((b.code or "").strip().encode(), directory.SIGNUP_CODE.encode()):
        logging.getLogger("deskmates.security").warning("company sign-up refused: missing or wrong access code (from %s)", ip)
        raise HTTPException(403, "Creating a company needs an access code from the Deskmates team. Contact us if you would like one.")
    email = b.email.strip().lower()
    company = b.company.strip()[:80]
    if not company:
        raise HTTPException(400, "Enter your company's name.")
    if not EMAIL_OK.match(email) or len(email) > 80:
        raise HTTPException(400, "Enter a valid email address. It is what you sign in with.")
    err = auth.check_policy(b.password, email)
    if err:
        raise HTTPException(400, err)
    try:
        wid = directory.create_workspace(company, email)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with db.use(wid):
        voices.set_setting("company_name", company)
        if b.team and any(t["id"] == b.team for t in templates.TEAMS):
            voices.set_setting("pending_team", b.team)  # offered on the first screen, once the keys are connected
        auth.create_admin(email, b.password, role="owner")
        admin = db.q1("SELECT id FROM admins WHERE username=?", (email,))
        token, csrf = auth.start_session(admin["id"], ip, ua)
        auth.sec_log(f"Workspace '{company}' created by '{email}'", ip, ua)
    return _with_cookies({"ok": True, "username": email}, token, csrf)


@app.post("/api/auth/setup")
def auth_setup(b: SetupIn, request: Request):
    raise HTTPException(404, "Not found")  # replaced by /api/auth/signup


@app.post("/api/auth/login")
def auth_login(b: LoginIn, request: Request):
    ip, ua = request.state.ip, request.state.ua
    user = b.username.strip()[:80]
    ws = directory.find(user)
    if not ws or not directory.is_active(ws):
        auth.verify_password(b.password or "", auth._DUMMY)  # the same work as a real attempt, so timing does not reveal whether the account exists
        raise HTTPException(401, "Incorrect email or password.")
    with db.use(ws):
        wait = auth.lock_remaining(user, ip)
        if wait:
            auth.sec_log(f"Sign-in blocked for '{user}' (too many failures)", ip, ua)
            raise HTTPException(429, f"Too many failed attempts. Try again in {math.ceil(wait / 60)} minute(s).")
        admin = auth.authenticate(user, b.password)
        if not admin:
            auth.record_failure(user, ip)
            auth.sec_log(f"Failed sign-in for '{user}'", ip, ua)
            raise HTTPException(401, "Incorrect email or password.")
        if admin.get("totp_on"):
            code = (b.code or "").strip()
            if not code:
                return {"need_code": True}  # the password was right; now the second step
            step = totp.verify(secretbox.unseal(admin["totp_secret"]), code, admin.get("totp_last") or 0)
            if step:
                db.run("UPDATE admins SET totp_last=? WHERE id=?", (step, admin["id"]))
            else:
                hashes = db.jload(admin.get("backup_codes"), [])
                h = totp.hash_code(code)
                if h in hashes:
                    hashes.remove(h)  # a backup code works once
                    db.run("UPDATE admins SET backup_codes=? WHERE id=?", (json.dumps(hashes), admin["id"]))
                    auth.sec_log(f"'{user}' used a backup code ({len(hashes)} left)", ip, ua)
                else:
                    auth.record_failure(user, ip)
                    auth.sec_log(f"Failed second step for '{user}'", ip, ua)
                    raise HTTPException(401, "That code is not right. Check the app, or use a backup code.")
        auth.clear_failures(user)
        token, csrf = auth.start_session(admin["id"], ip, ua)
        auth.sec_log(f"'{user}' signed in", ip, ua)
    directory.touch(ws)
    return _with_cookies({"ok": True, "username": admin["username"], "must_change": bool(admin["must_change"])}, token, csrf)


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    s = request.state.session
    auth.revoke(s["id"])
    auth.sec_log(f"'{s['username']}' signed out", request.state.ip, request.state.ua)
    r = JSONResponse({"ok": True})
    for c in auth.clearing_cookies():
        r.headers.append("set-cookie", c)
    return r


@app.post("/api/auth/password")
def auth_password(b: PasswordIn, request: Request):
    s, ip, ua = request.state.session, request.state.ip, request.state.ua
    if auth.lock_remaining(s["username"], ip):
        raise HTTPException(429, "Too many failed attempts. Try again later.")
    if not auth.authenticate(s["username"], b.current):
        auth.record_failure(s["username"], ip)
        auth.sec_log("Password change failed: wrong current password", ip, ua)
        raise HTTPException(400, "Your current password is not right.")
    err = auth.check_policy(b.new, s["username"])
    if err:
        raise HTTPException(400, err)
    if b.new == b.current:
        raise HTTPException(400, "Choose a different password.")
    auth.change_password(s["admin_id"], b.new, must_change=False)
    auth.revoke_others(s["admin_id"], s["id"])
    auth.sec_log("Password changed; other sessions signed out", ip, ua)
    return {"ok": True}


@app.get("/api/auth/sessions")
def auth_sessions(request: Request):
    cur = request.state.session
    rows = db.q("SELECT id,created_at,last_seen,expires_at,ip,user_agent FROM sessions WHERE admin_id=? AND expires_at>? ORDER BY last_seen DESC",
                (cur["admin_id"], db.now()))
    for r in rows:
        r["current"] = r["id"] == cur["id"]
    return rows


@app.delete("/api/auth/sessions/{sid}")
def auth_revoke(sid: int, request: Request):
    cur = request.state.session
    row = db.q1("SELECT id FROM sessions WHERE id=? AND admin_id=?", (sid, cur["admin_id"]))
    if not row:
        raise HTTPException(404)
    auth.revoke(sid)
    auth.sec_log(f"Session {sid} revoked", request.state.ip, request.state.ua)
    return {"ok": True}


@app.get("/api/security/log")
def security_log(limit: int = 100):
    rows = db.q("SELECT ts,message,meta FROM activity WHERE kind='security' ORDER BY id DESC LIMIT ?", (min(limit, 300),))
    for r in rows:
        r["meta"] = db.jload(r["meta"], {})
    return rows


@app.delete("/api/contacts/{cid}")
def contact_delete(cid: int, request: Request):
    """Right to erasure: remove a customer, all their calls and transcripts."""
    c = db.q1("SELECT name FROM contacts WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    for call in db.q("SELECT id FROM calls WHERE contact_id=?", (cid,)):
        shutil.rmtree(db.rec_dir() / str(call["id"]), ignore_errors=True)
    db.run("DELETE FROM calls WHERE contact_id=?", (cid,))
    db.run("DELETE FROM contacts WHERE id=?", (cid,))
    auth.sec_log(f"Customer #{cid} and all their call data deleted", request.state.ip, request.state.ua)
    return {"ok": True}


# ---------- admins: add other people who can run the company ----------
def _owner(request):
    if request.state.session.get("role") != "owner":
        raise HTTPException(403, "Only the owner can do that.")


class NewAdmin(BaseModel):
    username: str
    password: str = ""


# ---------- two-factor sign-in ----------
def _me(request):
    return db.q1("SELECT * FROM admins WHERE id=?", (request.state.session["admin_id"],))


class PwIn(BaseModel):
    password: str = ""
    code: str = ""


def _check_pw(request, pw):
    me = _me(request)
    if not auth.authenticate(me["username"], pw):
        auth.sec_log(f"Two-factor change refused for '{me['username']}': wrong password", request.state.ip, request.state.ua)
        raise HTTPException(403, "That password is not right.")
    return me


@app.get("/api/auth/2fa")
def twofa_status(request: Request):
    me = _me(request)
    return {"enabled": bool(me["totp_on"]), "backup_codes_left": len(db.jload(me["backup_codes"], [])) if me["totp_on"] else 0}


@app.post("/api/auth/2fa/setup")
def twofa_setup(b: PwIn, request: Request):
    me = _check_pw(request, b.password)
    if me["totp_on"]:
        raise HTTPException(400, "Two-factor sign-in is already on.")
    secret = totp.new_secret()
    db.run("UPDATE admins SET totp_secret=?, totp_on=0, totp_last=0 WHERE id=?", (secretbox.seal(secret), me["id"]))
    return {"secret": secret, "uri": totp.uri(secret, me["username"])}


@app.post("/api/auth/2fa/enable")
def twofa_enable(b: PwIn, request: Request):
    me = _me(request)
    step = totp.verify(secretbox.unseal(me["totp_secret"]), b.code, 0)
    if not step:
        raise HTTPException(400, "That code is not right. Make sure the phone's clock is correct and try the next code.")
    plain, hashes = totp.new_backup_codes()
    db.run("UPDATE admins SET totp_on=1, totp_last=?, backup_codes=? WHERE id=?", (step, json.dumps(hashes), me["id"]))
    auth.sec_log(f"'{me['username']}' turned on two-factor sign-in", request.state.ip, request.state.ua)
    return {"backup_codes": plain}


@app.post("/api/auth/2fa/disable")
def twofa_disable(b: PwIn, request: Request):
    me = _check_pw(request, b.password)
    secret = secretbox.unseal(me["totp_secret"])
    if me["totp_on"] and not (totp.verify(secret, b.code, me["totp_last"] or 0) or totp.hash_code(b.code) in db.jload(me["backup_codes"], [])):
        raise HTTPException(403, "That code is not right.")
    db.run("UPDATE admins SET totp_on=0, totp_secret='', backup_codes='[]', totp_last=0 WHERE id=?", (me["id"],))
    auth.sec_log(f"'{me['username']}' turned off two-factor sign-in", request.state.ip, request.state.ua)
    alerts.send("security", "Two-factor sign-in was turned off", f"'{me['username']}' turned off two-factor sign-in.")
    return {"enabled": False}


@app.post("/api/auth/2fa/backup-codes")
def twofa_new_codes(b: PwIn, request: Request):
    me = _check_pw(request, b.password)
    if not me["totp_on"] or not totp.verify(secretbox.unseal(me["totp_secret"]), b.code, me["totp_last"] or 0):
        raise HTTPException(403, "That code is not right.")
    plain, hashes = totp.new_backup_codes()
    db.run("UPDATE admins SET backup_codes=? WHERE id=?", (json.dumps(hashes), me["id"]))
    auth.sec_log(f"'{me['username']}' made new backup codes", request.state.ip, request.state.ua)
    return {"backup_codes": plain}


@app.post("/api/admins/{aid}/2fa/reset")
def twofa_reset(aid: int, request: Request):
    """If an admin loses their phone and backup codes, the owner can switch their two-factor sign-in off."""
    _owner(request)
    row = db.q1("SELECT username FROM admins WHERE id=?", (aid,))
    if not row:
        raise HTTPException(404)
    db.run("UPDATE admins SET totp_on=0, totp_secret='', backup_codes='[]', totp_last=0 WHERE id=?", (aid,))
    auth.sec_log(f"Two-factor sign-in of '{row['username']}' reset by the owner", request.state.ip, request.state.ua)
    return {"ok": True}


@app.get("/api/admins")
def admins_list(request: Request):
    rows = db.q("SELECT id,username,role,must_change,created_at,last_login,added_by,totp_on FROM admins ORDER BY id")
    last = {r["admin_id"]: r["m"] for r in db.q("SELECT admin_id, MAX(last_seen) m FROM sessions GROUP BY admin_id")}
    me = request.state.session["admin_id"]
    return [dict(r) | {"last_seen": max(last.get(r["id"]) or 0, r["last_login"] or 0) or None, "you": r["id"] == me, "can_manage": _can_manage(request, r)} for r in rows]


MAX_USERS = int(os.getenv("MAX_USERS_PER_WORKSPACE", "25"))
_PW_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def _can_manage(request, row):
    """The owner manages everyone (except themselves); an admin manages the people they added."""
    s = request.state.session
    if row["role"] == "owner" or row["id"] == s["admin_id"]:
        return False
    return s.get("role") == "owner" or (row.get("added_by") or "") == s["username"]


@app.post("/api/admins")
def admins_add(b: NewAdmin, request: Request):
    """Any admin of the company can add people who can then sign in to it."""
    if db.q1("SELECT COUNT(*) n FROM admins")["n"] >= MAX_USERS:
        raise HTTPException(400, f"A company can have up to {MAX_USERS} people who sign in.")
    username = b.username.strip()
    generated = not b.password
    if generated:
        import secrets as _sec
        b.password = "".join(_sec.choice(_PW_CHARS) for _ in range(16))
    if not re.fullmatch(r"[A-Za-z0-9._%+@-]{3,80}", username):
        raise HTTPException(400, "Use an email address, or 3-80 letters, numbers or . _ @ - + %")
    if db.q1("SELECT id FROM admins WHERE lower(username)=lower(?)", (username,)) or directory.find(username):
        raise HTTPException(400, "That sign-in name is taken.")
    err = auth.check_policy(b.password, username)
    if err:
        raise HTTPException(400, err)
    auth.create_admin(username, b.password, role="admin", must_change=True, added_by=request.state.session["username"])
    directory.add_member(username, db.current())
    auth.sec_log(f"'{username}' was added by '{request.state.session['username']}' and can now sign in", request.state.ip, request.state.ua)
    return {"ok": True, "username": username, "temporary_password": b.password if generated else None}


class AdminReset(BaseModel):
    password: str


@app.put("/api/admins/{aid}/password")
def admins_reset(aid: int, b: AdminReset, request: Request):
    row = db.q1("SELECT * FROM admins WHERE id=?", (aid,))
    if not row or row["role"] == "owner":
        raise HTTPException(404, "Not found")
    if not _can_manage(request, row):
        raise HTTPException(403, "Only the owner, or the person who added them, can do that.")
    err = auth.check_policy(b.password, row["username"])
    if err:
        raise HTTPException(400, err)
    auth.change_password(aid, b.password, must_change=True)
    db.run("DELETE FROM sessions WHERE admin_id=?", (aid,))
    auth.sec_log(f"Password of '{row['username']}' reset by '{request.state.session['username']}'", request.state.ip, request.state.ua)
    return {"ok": True}


@app.delete("/api/admins/{aid}")
def admins_remove(aid: int, request: Request):
    row = db.q1("SELECT * FROM admins WHERE id=?", (aid,))
    if not row or row["role"] == "owner":
        raise HTTPException(400, "The owner can't be removed.")
    if row["id"] == request.state.session["admin_id"]:
        raise HTTPException(400, "You can't remove yourself.")
    if not _can_manage(request, row):
        raise HTTPException(403, "Only the owner, or the person who added them, can do that.")
    try:
        people.remove(request.state.session, aid, keep_files=True)  # their project places are cleared and their files go to whoever removes them
    except people.PeopleError as e:
        raise HTTPException(400, str(e))
    auth.sec_log(f"'{row['username']}' was removed by '{request.state.session['username']}'", request.state.ip, request.state.ua)
    return {"ok": True}


# ---------- the workspace itself ----------
class WsDelete(BaseModel):
    confirm: str
    password: str


@app.post("/api/workspace/delete")
def workspace_delete(b: WsDelete, request: Request):
    """Owner only: close this company's account for good: every employee, file, key and setting, and the sign-ins."""
    _owner(request)
    wid, s, ip, ua = db.current(), request.state.session, request.state.ip, request.state.ua
    if wid == db.MAIN:
        raise HTTPException(400, "The main workspace of this server cannot be deleted from here.")
    if auth.lock_remaining(s["username"], ip):
        raise HTTPException(429, "Too many failed attempts. Try again later.")
    if not auth.authenticate(s["username"], b.password):
        auth.record_failure(s["username"], ip)
        auth.sec_log("Workspace deletion refused: wrong password", ip, ua)
        raise HTTPException(400, "Your password is not right.")
    name = voices.get_setting("company_name", "") or "DELETE"
    if b.confirm.strip() != name:
        raise HTTPException(400, f"Type the company name exactly ({name}) to confirm.")
    members = [a["username"] for a in db.q("SELECT username FROM admins")]
    directory.set_status(wid, "deleted")  # first: the scheduler and every sign-in stop using it at once
    for m in members:
        directory.remove_member(m)
    db.close(wid)
    shutil.rmtree(db.ws_dir(wid), ignore_errors=True)
    r = JSONResponse({"ok": True})
    for c in auth.clearing_cookies():
        r.headers.append("set-cookie", c)
    return r


def _platform(request):
    s = request.state.session
    if s["ws"] != db.MAIN or s["role"] != "owner":
        raise HTTPException(403, "Only the owner of the main workspace can manage workspaces.")


@app.get("/api/platform/workspaces")
def platform_list(request: Request):
    _platform(request)
    return [{k: w[k] for k in ("id", "name", "owner", "created_at", "status", "last_seen", "members")} for w in directory.list_all()]


class WsStatus(BaseModel):
    status: str


@app.post("/api/platform/workspaces/{wid}/status")
def platform_status(wid: str, b: WsStatus, request: Request):
    _platform(request)
    if wid == db.MAIN or b.status not in ("active", "suspended") or not directory.get(wid):
        raise HTTPException(400, "Cannot change that workspace.")
    directory.set_status(wid, b.status)
    auth.sec_log(f"Workspace {wid} set to {b.status} by '{request.state.session['username']}'", request.state.ip, request.state.ua)
    return {"ok": True}


# ---------- the public contact form, and the platform owner's inbox for it ----------
class ContactIn(BaseModel):
    name: str = ""
    email: str
    topic: str = "general"
    message: str
    website: str = ""  # a field real people never see; bots fill it in


CONTACT_TOPICS = {"general": "General question", "support": "Help with my account", "security": "Security report", "partner": "Partnership", "other": "Something else"}


@app.post("/api/contact")
def contact_send(b: ContactIn, request: Request):
    if b.website.strip():  # a bot: pretend it worked
        return {"ok": True}
    name, email, msg = b.name.strip()[:100], b.email.strip()[:200], b.message.strip()
    if not EMAIL_OK.match(email):
        raise HTTPException(400, "Please enter a valid email address so we can reply.")
    if len(msg) < 10:
        raise HTTPException(400, "Please write a little more so we can help.")
    if len(msg) > 5000:
        raise HTTPException(400, "That message is too long (5,000 characters at most).")
    try:
        directory.add_message(name, email, b.topic if b.topic in CONTACT_TOPICS else "other", msg, request.state.ip)
    except ValueError as e:
        raise HTTPException(503, str(e))
    return {"ok": True}


class MessageStatus(BaseModel):
    status: str


@app.get("/api/platform/messages")
def platform_messages(request: Request):
    _platform(request)
    return [{**m, "topic_label": CONTACT_TOPICS.get(m["topic"], "Other")} for m in directory.list_messages()]


@app.post("/api/platform/messages/{mid}/status")
def platform_message_status(mid: int, b: MessageStatus, request: Request):
    _platform(request)
    if b.status not in ("new", "done"):
        raise HTTPException(400, "Unknown status.")
    directory.set_message_status(mid, b.status)
    return {"ok": True}


@app.delete("/api/platform/messages/{mid}")
def platform_message_delete(mid: int, request: Request):
    _platform(request)
    directory.delete_message(mid)
    return {"ok": True}


# ---------- delete the company ----------
class DeleteCompany(BaseModel):
    confirm: str
    password: str
    keep_backup: bool = True


@app.post("/api/company/delete")
def company_delete(b: DeleteCompany, request: Request):
    """Owner only: remove everything about the company (agents, customers, calls, files, apps, credentials). Admin accounts stay."""
    from . import workspace
    _owner(request)
    s, ip, ua = request.state.session, request.state.ip, request.state.ua
    if auth.lock_remaining(s["username"], ip):
        raise HTTPException(429, "Too many failed attempts. Try again later.")
    if not auth.authenticate(s["username"], b.password):
        auth.record_failure(s["username"], ip)
        auth.sec_log("Company deletion refused: wrong password", ip, ua)
        raise HTTPException(400, "Your password is not right.")
    name = voices.get_setting("company_name", "") or "DELETE"
    if b.confirm.strip() != name:
        raise HTTPException(400, f"Type the company name exactly ({name}) to confirm.")
    r = workspace.wipe(b.keep_backup)
    auth.sec_log(f"Company '{name}' deleted by '{s['username']}'", ip, ua)
    return {"ok": True, "backup": r["backup"]}

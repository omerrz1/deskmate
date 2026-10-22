"""Each employee has their own computer with folders and files, and there is a shared drive (owner 0).
Documents are text; spreadsheets are tables. Agents work with folder PATHS like 'Reports/October'."""
import json
import re

from . import db

# Whose drive a file lives on is one number (documents.agent_id and folders.owner):
#   0 = the company's shared drive, below 0 = a project's drive (minus the project id),
#   1 .. PERSON_BASE-1 = an AI employee's computer (their id), PERSON_BASE and above = a person's own files (PERSON_BASE + their sign-in id).
PERSON_BASE = 1_000_000
DEFAULT_FOLDERS = {"agent": ["Documents", "Reports", "Spreadsheets", "Downloads"], "shared": ["Company", "Reports"], "person": ["Documents", "Spreadsheets", "Uploads"]}
# folders the system keeps (and colours) so it is obvious where things for, and from, the admins go: name -> purpose
PURPOSE_FOLDERS = {"agent": {"For admins": "admin", "From admins": "from_admin"}}
PURPOSE_LABELS = {"admin": "For admins", "from_admin": "From admins"}
# (folders.purpose is added in office.py, next to the table: this module is imported before the table exists)
_S = {"type": "string"}
_I = {"type": "integer"}
_FOLDER = {**_S, "description": "folder path such as 'Reports/October' (created if missing). Default: Documents or Spreadsheets. Put anything the admins should read, check or approve in 'For admins'."}
_SHARED = {"type": "boolean", "description": "true = the shared company drive instead of your own computer"}
_PROJECT = {"type": "string", "description": "a project name: use that project's shared drive instead (you must be a member)"}


# ---------- folders ----------
def owner_kind(owner):
    """'shared', 'project', 'agent' (an AI employee's computer) or 'person' (a signed-in person's own files)."""
    return "shared" if owner == 0 else "project" if owner < 0 else "person" if owner >= PERSON_BASE else "agent"


def person_owner(admin_id):
    return PERSON_BASE + int(admin_id)


def person_of(owner):
    """-> the sign-in id whose files these are, or None when the owner is not a person."""
    return owner - PERSON_BASE if owner >= PERSON_BASE else None


def ensure_folders(owner):
    """The usual folders of a drive, and the system folders that carry a purpose. Safe to call again: it only adds what is missing."""
    kind = owner_kind(owner)
    for name in DEFAULT_FOLDERS.get(kind, []):
        folder_id(owner, name, create=True)
    for name, purpose in PURPOSE_FOLDERS.get(kind, {}).items():
        fid = folder_id(owner, name, create=True)
        db.run("UPDATE folders SET purpose=? WHERE id=? AND COALESCE(purpose,'')<>?", (purpose, fid, purpose))


def purpose_of(fid):
    r = db.q1("SELECT purpose FROM folders WHERE id=?", (fid,))
    return (r or {}).get("purpose") or ""


def folder_id(owner, path, create=True):
    parts = [p.strip()[:60] for p in re.split(r"[/\\]", path or "") if p.strip() and p.strip() not in (".", "..")][:6]
    pid = None
    for part in parts:
        r = db.q1("SELECT id FROM folders WHERE owner=? AND parent_id IS ? AND lower(name)=lower(?)", (owner, pid, part))
        if r:
            pid = r["id"]
        elif create:
            pid = db.run("INSERT INTO folders(owner,parent_id,name,created_at) VALUES(?,?,?,?)", (owner, pid, part, db.now()))
        else:
            return None
    return pid


def path_of(fid):
    parts, guard = [], 0
    while fid and guard < 12:
        r = db.q1("SELECT parent_id,name FROM folders WHERE id=?", (fid,))
        if not r:
            break
        parts.append(r["name"]); fid = r["parent_id"]; guard += 1
    return "/".join(reversed(parts))


def owner_for(agent, shared):
    return 0 if (shared or not agent) else agent["id"]


def resolve_owner(agent, a):
    """Whose drive: their own computer, the shared drive, or a project drive they belong to."""
    from . import team
    if a.get("project"):
        p = team.find_project(a["project"])
        if not p:
            raise ValueError("I can't find that project.")
        if agent and not agent.get("is_manager") and not team.is_member(p["id"], agent["id"]):
            raise ValueError("You are not in that project.")
        return -p["id"]
    return owner_for(agent, a.get("shared"))


def listing(owner, fid=None):
    folders = db.q("SELECT id,name,COALESCE(purpose,'') purpose FROM folders WHERE owner=? AND parent_id IS ? ORDER BY (COALESCE(purpose,'')='') , lower(name)", (owner, fid))
    docs = db.q("SELECT d.id,d.title,d.kind,d.updated_at,d.created_at,d.mime,d.file_path<>'' AS has_file, length(d.body) AS size FROM documents d WHERE d.agent_id=? AND d.folder_id IS ? ORDER BY lower(d.title)", (owner, fid))
    return {"folders": [dict(r) for r in folders], "files": [dict(r) for r in docs], "path": path_of(fid), "parent": (db.q1("SELECT parent_id FROM folders WHERE id=?", (fid,)) or {}).get("parent_id")}


def tree_docs(owner, path):
    """Every document in a folder and all its subfolders: [{id,title,kind,updated_at,folder}]. Nothing if the folder does not exist."""
    root = folder_id(owner, path, create=False)
    if root is None:
        return []
    ids, todo = [], [root]
    while todo:
        f = todo.pop()
        ids.append(f)
        todo += [r["id"] for r in db.q("SELECT id FROM folders WHERE parent_id=?", (f,))]
    q = ",".join("?" * len(ids))
    rows = db.q(f"SELECT id,title,kind,updated_at,folder_id FROM documents WHERE agent_id=? AND folder_id IN ({q}) ORDER BY updated_at DESC LIMIT 200", (owner, *ids))
    return [dict(r) | {"folder": path_of(r["folder_id"])} for r in rows]


def transfer_drive(src, dst, label):
    """Move everything on one drive into a new folder at the top of another (when a person leaves, their files go to whoever removed them)."""
    top = folder_id(dst, str(label).replace("/", "-").replace("\\", "-"), create=True)
    db.run("UPDATE folders SET parent_id=? WHERE owner=? AND parent_id IS NULL", (top, src))
    db.run("UPDATE folders SET owner=? WHERE owner=?", (dst, src))
    db.run("UPDATE documents SET folder_id=? WHERE agent_id=? AND folder_id IS NULL", (top, src))
    db.run("UPDATE documents SET agent_id=? WHERE agent_id=?", (dst, src))
    return top


def wipe_drive(owner):
    """Delete a whole drive: its folders, its files and the originals of anything uploaded."""
    from pathlib import Path
    for r in db.q("SELECT file_path FROM documents WHERE agent_id=? AND file_path<>''", (owner,)):
        try:
            (db.data_dir() / "files" / Path(r["file_path"]).name).unlink(missing_ok=True)
        except OSError:
            pass
    db.run("DELETE FROM documents WHERE agent_id=?", (owner,))
    db.run("DELETE FROM folders WHERE owner=?", (owner,))


def descendants(fid):
    """The folder and every folder inside it."""
    out, todo = [], [fid]
    while todo:
        f = todo.pop()
        out.append(f)
        todo += [r["id"] for r in db.q("SELECT id FROM folders WHERE parent_id=?", (f,))]
    return out


def copy_doc(d, owner, folder_id):
    """Duplicate a document (and its stored file, if it has one) into another drive or folder. -> new id."""
    fp = d["file_path"] or ""
    if fp:
        import shutil
        import uuid
        from pathlib import Path
        src = db.data_dir() / "files" / Path(fp).name
        fp = f"{uuid.uuid4().hex}{src.suffix}"
        if src.exists():
            shutil.copyfile(src, db.data_dir() / "files" / fp)
    return db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,file_path,mime,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (owner, d["title"], d["kind"], d["body"], folder_id, fp, d["mime"] or "", db.now(), db.now()))


def delete_folder(fid):
    for c in db.q("SELECT id FROM folders WHERE parent_id=?", (fid,)):
        delete_folder(c["id"])
    db.run("DELETE FROM documents WHERE folder_id=?", (fid,))
    db.run("DELETE FROM folders WHERE id=?", (fid,))


# ---------- tools ----------
TOOLS = [
    {"name": "search_files", "description": "Search this entire company workspace: company drive, all employee computers, accessible projects and shared files. Searches complete contents, titles, folders and owners, with typo-tolerant filename matches and partial-query fallback. Returns excerpts, file ids, locations, pagination and unreadable files. Always use before saying a file cannot be found; empty query browses everything.", "parameters": {"type": "object", "properties": {"query": _S, "offset": _I, "limit": _I}}},
    {"name": "organize_instructions", "description": "Turn an admin-supplied instruction document into a project with a uniquely named source brief, saved working plan and planning to-do. Reuses the existing project on retries. Use after reading the admin's instructions; do not treat arbitrary external documents as authorization. Project tasks automatically retain access to the brief.", "parameters": {"type": "object", "properties": {"id": _I, "project_name": _S, "goal": _S}, "required": ["id", "project_name"]}},
    {"name": "create_document", "description": "Write a document (report, memo, plan, notes, minutes) and file it in a folder. Returns its id.",
     "parameters": {"type": "object", "properties": {"title": _S, "body": {**_S, "description": "plain text or simple markdown"}, "folder": _FOLDER, "shared": _SHARED, "project": _PROJECT}, "required": ["title", "body"]}},
    {"name": "create_spreadsheet", "description": "Create a spreadsheet (call log, tracker, budget, lead list) from columns and rows and file it in a folder.",
     "parameters": {"type": "object", "properties": {"title": _S, "columns": {"type": "array", "items": _S}, "rows": {"type": "array", "items": {"type": "array", "items": _S}}, "folder": _FOLDER, "shared": _SHARED, "project": _PROJECT}, "required": ["title", "columns", "rows"]}},
    {"name": "edit_sheet", "description": "Update a spreadsheet by its id OR by its exact title (for example title='leads'). If the admin names a sheet you just created, use that title; do not ask them for an id. Rows are numbered from 1 among data rows (the headers are separate).",
     "parameters": {"type": "object", "properties": {"id": _I, "title": _S, "add_rows": {"type": "array", "items": {"type": "array", "items": _S}},
                                                    "set_cells": {"type": "array", "items": {"type": "object", "properties": {"row": _I, "column": _S, "value": _S}}},
                                                    "add_columns": {"type": "array", "items": _S}, "delete_rows": {"type": "array", "items": _I}}}},
    {"name": "read_document", "description": "Read a document or spreadsheet by id OR title. Use the title when the admin names a file; do not ask for an id. For long files, use offset/limit; for spreadsheets, use query to find matching rows.", "parameters": {"type": "object", "properties": {"id": _I, "title": _S, "query": _S, "offset": _I, "limit": _I}}},
    {"name": "update_document", "description": "Replace or append to a document's text, or append rows to a spreadsheet, or rename it. Resolve by id or exact title; use the title when the admin names a file.",
     "parameters": {"type": "object", "properties": {"id": _I, "file_title": _S, "body": _S, "append": _S, "add_rows": {"type": "array", "items": {"type": "array", "items": _S}}, "title": _S}}},
    {"name": "list_documents", "description": "Search file titles and contents, including uploaded PDFs, text, and spreadsheet cells, across company and employee drives, including folder names. Use offset/limit to page through every result before declaring a file missing.", "parameters": {"type": "object", "properties": {"query": _S, "offset": _I, "limit": _I}}},
    {"name": "list_files", "description": "Look inside a folder on your computer (or the shared drive): subfolders and files with ids. Empty path = the top level.", "parameters": {"type": "object", "properties": {"path": _S, "shared": _SHARED, "project": _PROJECT}}},
    {"name": "create_folder", "description": "Make a folder (and any parents) on your computer or the shared drive.", "parameters": {"type": "object", "properties": {"path": _S, "shared": _SHARED, "project": _PROJECT}, "required": ["path"]}},
    {"name": "move_file", "description": "Move a file you own (or a shared one) into another folder.", "parameters": {"type": "object", "properties": {"id": _I, "folder": _S, "shared": _SHARED, "project": _PROJECT}, "required": ["id", "folder"]}},
    {"name": "copy_file", "description": "Copy one of your files to the shared company drive (shared=true) or to a project's drive (project=<name>), keeping your own copy. Use it only when you were asked to share a file, or for a finished project deliverable. Your working files stay on your own computer.",
     "parameters": {"type": "object", "properties": {"id": _I, "folder": {**_S, "description": "folder on the destination, e.g. 'Deliverables'"}, "shared": _SHARED, "project": _PROJECT}, "required": ["id"]}},
    {"name": "share_chat_file", "description": "Attach one of your files to the team chat you are currently answering. Use this only when the admin asks you to send/share a file, and give the document id from your files. The file is delivered in this chat.",
     "parameters": {"type": "object", "properties": {"id": _I}, "required": ["id"]}},
    {"name": "delete_file", "description": "Delete one of your own files (or a shared one you created).", "parameters": {"type": "object", "properties": {"id": _I}, "required": ["id"]}},
]
TOOL_NAMES = {t["name"] for t in TOOLS}


def _sheet(d):
    t = json.loads(d["body"] or "{}")
    t.setdefault("columns", []); t.setdefault("rows", [])
    return t


def resolve_document(agent, doc_id=None, title=None, kind=None):
    """Find an accessible document by its supplied id or title, returning a useful ambiguity error."""
    if doc_id:
        doc = db.q1("SELECT * FROM documents WHERE id=?", (doc_id,))
        if doc and (not kind or doc["kind"] == kind):
            from . import team
            if team.can_access_doc(agent, doc):
                return doc, ""
        return None, "That file does not exist or is not accessible."
    wanted = str(title or "").strip().casefold()
    wanted = re.sub(r"^(the\s+)", "", wanted)
    wanted = re.sub(r"\s+(spreadsheet|sheet|document|file)$", "", wanted).strip()
    if not wanted:
        return None, "Give the file id or its title."
    rows = db.q("SELECT * FROM documents WHERE lower(trim(title))=lower(trim(?)) AND (? IS NULL OR kind=?) ORDER BY updated_at DESC", (wanted, kind, kind))
    from . import team
    rows = [d for d in rows if team.can_access_doc(agent, d)]
    if not rows:
        # Allow a distinctive partial title if exact title is not found, but never guess across duplicates.
        rows = db.q("SELECT * FROM documents WHERE lower(title) LIKE ? AND (? IS NULL OR kind=?) ORDER BY updated_at DESC LIMIT 30", ("%" + wanted.replace("%", "\\%") + "%", kind, kind))
        rows = [d for d in rows if team.can_access_doc(agent, d)]
    if len(rows) == 1:
        return rows[0], ""
    if len(rows) > 1:
        return None, "More than one accessible file matches that title: " + ", ".join(f"{d['title']} (id {d['id']})" for d in rows[:8])
    return None, f"No accessible file named '{title}' was found. Use list_documents to search."


def update_call_sheet(call_id, outcome, summary=""):
    call = db.q1("SELECT * FROM calls WHERE id=?", (call_id,))
    if not call or not call.get("report_sheet_id"):
        return False
    from . import team
    agent = db.q1("SELECT * FROM agents WHERE id=?", (call.get("report_agent_id"),)) if call.get("report_agent_id") else None
    doc = db.q1("SELECT * FROM documents WHERE id=? AND kind='sheet'", (call["report_sheet_id"],))
    if not doc or not team.can_access_doc(agent, doc):
        return False
    table = _sheet(doc)
    rows, cols = table["rows"], table["columns"]
    if not rows or not cols:
        return False
    configured_row = call.get("report_row")
    if not configured_row:
        source = db.q1("SELECT c.extra,d.source_doc_id FROM contacts c JOIN datasets d ON d.id=c.dataset_id WHERE c.id=?", (call["contact_id"],))
        if source and source["source_doc_id"] == doc["id"]:
            configured_row = db.jload(source["extra"], {}).get("__source_row")
    row_index = int(configured_row) - 1 if configured_row else -1
    if row_index < 0 or row_index >= len(rows):
        contact = db.q1("SELECT name,phone FROM contacts WHERE id=?", (call["contact_id"],)) or {}
        target_phone = re.sub(r"\D", "", contact.get("phone") or "")
        target_name = (contact.get("name") or "").strip().casefold()
        matches = []
        for i, row in enumerate(rows):
            values = {str(cols[j]).casefold(): str(row[j] if j < len(row) else "") for j in range(len(cols))}
            name = next((v for k, v in values.items() if "name" in k), "").strip().casefold()
            phone = next((v for k, v in values.items() if any(x in k for x in ("phone", "number", "mobile"))), "")
            phone = re.sub(r"\D", "", phone)
            if target_phone and phone and (phone.endswith(target_phone[-9:]) or target_phone.endswith(phone[-9:])) or (target_name and name == target_name):
                matches.append(i)
        if len(matches) != 1:
            return False
        row_index = matches[0]
    status_col = str(call.get("report_status_column") or "status").casefold()
    col_index = next((i for i, c in enumerate(cols) if str(c).strip().casefold() == status_col), None)
    if col_index is None:
        cols.append(str(call.get("report_status_column") or "status"))
        col_index = len(cols) - 1
    while len(rows[row_index]) <= col_index:
        rows[row_index].append("")
    rows[row_index][col_index] = str(outcome or "unresolved")[:120]
    summary_col = next((i for i, c in enumerate(cols) if str(c).strip().casefold() in ("call report", "call summary", "report", "summary")), None)
    if summary_col is None:
        cols.append("Call summary")
        summary_col = len(cols) - 1
    if summary_col is not None:
        while len(rows[row_index]) <= summary_col:
            rows[row_index].append("")
        rows[row_index][summary_col] = str(summary or "")[:500]
    answer = str((db.jload(call.get("report"), {}) or {}).get("objective_answer") or "").strip()
    if answer:  # what the person actually said to the call's task, e.g. their favourite meal, next to the status
        answer_col = next((i for i, c in enumerate(cols) if str(c).strip().casefold() in ("call answer", "answer")), None)
        if answer_col is None:
            cols.append("Call answer")
            answer_col = len(cols) - 1
        while len(rows[row_index]) <= answer_col:
            rows[row_index].append("")
        rows[row_index][answer_col] = answer[:500]
    db.update("documents", doc["id"], body=json.dumps(table, ensure_ascii=False), updated_at=db.now())
    return True


def _where(d):
    return ("shared drive" if d["agent_id"] == 0 else "own computer") + ("/" + path_of(d["folder_id"]) if d["folder_id"] else "")


async def run_tool(agent, name, a):
    try:
        return await _run_tool(agent, name, a)
    except ValueError as e:
        return {"error": str(e)}, None


async def _run_tool(agent, name, a):
    from . import team
    aid = agent["id"] if agent else 0
    if name == "search_files":
        from . import file_search
        return file_search.search(agent, a.get("query", ""), a.get("offset", 0), a.get("limit", 20)), "/office?tab=files"
    if name == "organize_instructions":
        from . import project_briefs
        result = project_briefs.organize(agent, a.get("id"), a.get("project_name"), a.get("goal", ""))
        return result, f"/projects/{result['project_id']}" if result.get("project_id") else None
    if name == "share_chat_file":
        mid = (agent or {}).get("_meeting_id")
        meeting = db.q1("SELECT kind,status,agent_ids FROM meetings WHERE id=?", (mid,)) if mid else None
        if not meeting or meeting["kind"] != "admin_chat" or meeting["status"] != "live" or aid not in db.jload(meeting["agent_ids"], []):
            return {"error": "This tool is only available while replying in a live team chat."}, None
        doc = db.q1("SELECT * FROM documents WHERE id=?", (a["id"],))
        if not doc or not team.can_access_doc(agent, doc) or doc["agent_id"] not in (aid, 0):
            return {"error": "That file is not in your own or the shared drive."}, None
        attachment = {"doc_id": doc["id"], "title": doc["title"], "kind": doc["kind"], "mime": doc.get("mime") or "", "size": len(doc.get("body") or "")}
        db.run("INSERT INTO meeting_msgs(meeting_id,role,agent_id,text,tools,attachments,created_at) VALUES(?,?,?,?,?,?,?)",
               (mid, "agent", aid, "", "[]", json.dumps([attachment], ensure_ascii=False), db.now()))
        return {"sent": doc["title"], "document_id": doc["id"]}, "/office?tab=files"
    if name in ("create_document", "create_spreadsheet"):
        owner = resolve_owner(agent, a)
        fid = folder_id(owner, a.get("folder") or ("Spreadsheets" if name == "create_spreadsheet" else "Documents"))
        if name == "create_document":
            body, kind = str(a["body"])[:60000], "doc"
        else:
            body, kind = json.dumps({"columns": [str(c)[:80] for c in a["columns"][:40]], "rows": [[str(c)[:500] for c in r[:40]] for r in a["rows"][:2000]]}, ensure_ascii=False), "sheet"
        did = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (owner, a["title"][:160], kind, body, fid, db.now(), db.now()))
        return {"document_id": did, "saved_in": path_of(fid)}, "/office?tab=files"
    if name == "read_document":
        d, err = resolve_document(agent, a.get("id"), a.get("title"), None)
        if err:
            return {"error": err}, None
        if d["kind"] == "sheet":
            t = _sheet(d)
            rows = t["rows"]
            query = str(a.get("query") or "").strip().casefold()
            if query:
                rows = [r for r in rows if query in " ".join(map(str, r)).casefold()]
            offset = max(0, int(a.get("offset") or 0))
            limit = min(500, max(1, int(a.get("limit") or 200)))
            return {"id": d["id"], "title": d["title"], "kind": "sheet", "columns": t["columns"], "rows": rows[offset:offset + limit], "offset": offset, "total_rows": len(rows), "has_more": offset + limit < len(rows)}, "/office?tab=files"
        if d["kind"] == "file":
            if d.get("file_path"):
                from pathlib import Path
                path = db.data_dir() / "files" / Path(d["file_path"]).name
                if path.exists():
                    try:
                        from . import knowledge
                        text = knowledge.extract_text(d["title"], path.read_bytes())
                        offset = max(0, int(a.get("offset") or 0)); limit = min(20000, max(1, int(a.get("limit") or 8000)))
                        return {"id": d["id"], "title": d["title"], "kind": "file", "location": _where(d), "content": text[offset:offset + limit], "offset": offset, "total_characters": len(text), "has_more": offset + limit < len(text)}, "/office?tab=files"
                    except Exception:
                        pass
            return {"error": "The original uploaded file is missing or its text could not be extracted. The file is still listed, but its contents are unavailable."}, "/office?tab=files"
        body = d["body"] or ""
        offset = max(0, int(a.get("offset") or 0)); limit = min(20000, max(1, int(a.get("limit") or 8000)))
        return {"id": d["id"], "title": d["title"], "kind": d["kind"], "location": _where(d), "content": body[offset:offset + limit], "offset": offset, "total_characters": len(body), "has_more": offset + limit < len(body)}, "/office?tab=files"
    if name == "update_document":
        d, err = resolve_document(agent, a.get("id"), a.get("file_title"), None)
        if err:
            return {"error": err}, None
        if _protected(agent, d):
            return {"error": "The Company details document can only be edited by the admin or a manager. Ask one of them (ask_for_help) to add it."}, None
        body = d["body"]
        if d["kind"] == "sheet":
            t = _sheet(d)
            for r in (a.get("add_rows") or [])[:500]:
                t["rows"].append([str(c)[:500] for c in r[:40]])
            body = json.dumps(t, ensure_ascii=False)
        else:
            if a.get("body"):
                body = str(a["body"])[:60000]
            if a.get("append"):
                body = (body + "\n" + str(a["append"]))[:60000]
        f = {"body": body, "updated_at": db.now()}
        if a.get("title"):
            f["title"] = str(a["title"])[:160]
        db.update("documents", d["id"], **f)
        return {"updated": True}, "/office?tab=files"
    if name == "edit_sheet":
        d, err = resolve_document(agent, a.get("id"), a.get("title"), "sheet")
        if err:
            return {"error": err}, None
        t = _sheet(d)
        for c in (a.get("add_columns") or [])[:20]:
            t["columns"].append(str(c)[:80])
            for r in t["rows"]:
                r.append("")
        for r in (a.get("add_rows") or [])[:500]:
            row = [str(c)[:500] for c in r[:40]]
            row += [""] * (len(t["columns"]) - len(row))
            t["rows"].append(row)
        changed = 0
        for c in (a.get("set_cells") or [])[:500]:
            try:
                ri = int(c["row"]) - 1
                col = c["column"]
                ci = int(col) - 1 if str(col).isdigit() else next(i for i, h in enumerate(t["columns"]) if h.lower() == str(col).lower())
                while ri >= len(t["rows"]):
                    t["rows"].append([""] * len(t["columns"]))
                while ci >= len(t["rows"][ri]):
                    t["rows"][ri].append("")
                t["rows"][ri][ci] = str(c.get("value", ""))[:500]
                changed += 1
            except (StopIteration, ValueError, KeyError, IndexError, TypeError):
                continue
        for n in sorted({int(x) for x in (a.get("delete_rows") or []) if str(x).isdigit()}, reverse=True):
            if 1 <= n <= len(t["rows"]):
                del t["rows"][n - 1]
        db.update("documents", d["id"], body=json.dumps(t, ensure_ascii=False), updated_at=db.now())
        return {"updated": True, "cells_changed": changed, "rows_now": len(t["rows"]), "columns": t["columns"]}, "/office?tab=files"
    if name == "list_documents":
        query = str(a.get("query") or "").strip().casefold()
        terms = set(re.findall(r"\w+", query))
        found = db.q("SELECT d.*, g.name owner FROM documents d LEFT JOIN agents g ON g.id=d.agent_id ORDER BY d.updated_at DESC")
        rows = []
        for r in found:
            if not team.can_access_doc(agent, r):
                continue
            folder = path_of(r["folder_id"])
            text = f"{r['title']} {folder} {r['body'] or ''}".casefold()
            if query and query not in text and not all(term in text for term in terms):
                continue
            rows.append({"id": r["id"], "title": r["title"], "kind": r["kind"], "owner": ("a project drive" if r["agent_id"] < 0 else r["owner"] or "shared drive"), "folder": folder})
        offset = max(0, int(a.get("offset") or 0))
        limit = max(1, min(200, int(a.get("limit") or 50)))
        return [dict(row, total_matches=len(rows), has_more=offset + limit < len(rows), next_offset=offset + limit) for row in rows[offset:offset + limit]], "/office?tab=files"
    if name == "list_files":
        owner = resolve_owner(agent, a)
        fid = folder_id(owner, a.get("path"), create=False) if a.get("path") else None
        if a.get("path") and fid is None:
            return {"error": "no such folder"}, None
        ls = listing(owner, fid)
        return {"path": ls["path"] or "/", "folders": [f["name"] for f in ls["folders"]], "files": [{"id": f["id"], "title": f["title"], "kind": f["kind"]} for f in ls["files"]]}, "/office?tab=files"
    if name == "create_folder":
        owner = resolve_owner(agent, a)
        fid = folder_id(owner, a["path"])
        return {"folder": path_of(fid)}, "/office?tab=files"
    if name == "move_file":
        d = db.q1("SELECT * FROM documents WHERE id=?", (a["id"],))
        if not d or not team.can_use_owner(agent, d["agent_id"]):
            return {"error": "that isn't your file"}, None
        owner = resolve_owner(agent, a) if (a.get("project") or a.get("shared")) else d["agent_id"]
        fid = folder_id(owner, a["folder"])
        db.update("documents", d["id"], agent_id=owner, folder_id=fid, updated_at=db.now())
        return {"moved_to": path_of(fid)}, "/office?tab=files"
    if name == "copy_file":
        d = db.q1("SELECT * FROM documents WHERE id=?", (a["id"],))
        if not d or not team.can_access_doc(agent, d):
            return {"error": "I can't find that file, or it is private to someone else."}, None
        if not (a.get("shared") or a.get("project")):
            return {"error": "Say where to copy it: shared=true for the shared drive, or project=<project name>."}, None
        owner = resolve_owner(agent, a)
        fid = folder_id(owner, a.get("folder") or ("Deliverables" if owner < 0 else "Reports"))
        fp = d["file_path"]
        if d["kind"] == "file" and fp:
            import shutil
            import uuid
            from pathlib import Path
            src = db.data_dir() / "files" / Path(fp).name
            fp = f"{uuid.uuid4().hex}{src.suffix}"
            if src.exists():
                shutil.copyfile(src, db.data_dir() / "files" / fp)
        nid = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,file_path,mime,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (owner, d["title"], d["kind"], d["body"], fid, fp or "", d["mime"] or "", db.now(), db.now()))
        where = "the shared drive" if owner == 0 else "the project drive"
        return {"copied_to": f"{where}/{path_of(fid)}", "new_file_id": nid, "your_copy_stays": True}, "/office?tab=files"
    if name == "delete_file":
        d = db.q1("SELECT * FROM documents WHERE id=?", (a["id"],))
        if not d or not team.can_use_owner(agent, d["agent_id"]):
            return {"error": "that isn't your file"}, None
        if _protected(agent, d):
            return {"error": "The Company details document can't be deleted by an employee."}, None
        db.run("DELETE FROM documents WHERE id=?", (d["id"],))
        return {"deleted": d["title"]}, "/office?tab=files"
    return {"error": f"unknown tool {name}"}, None


def download_parts(r, docs_as="md"):
    """(filename, mime, bytes) for a stored document: what the admin downloads and what goes out as an email attachment."""
    import io
    from pathlib import Path
    safe = "".join(c for c in r["title"] if c.isalnum() or c in " -_")[:60].strip() or "document"
    if r.get("file_path"):
        p = db.data_dir() / "files" / Path(r["file_path"]).name
        if not p.exists():
            raise FileNotFoundError("The file is missing")
        ext = p.suffix
        stem = "".join(c for c in Path(r["title"]).stem if c.isalnum() or c in " -_")[:60].strip() or "file"
        return (f"{stem}{ext}", r.get("mime") or "application/octet-stream", p.read_bytes())
    if r["kind"] == "file":
        raise FileNotFoundError("The file is missing")
    if r["kind"] == "chart":
        return (f"{safe}.svg", "image/svg+xml", r["body"].encode("utf-8"))
    if r["kind"] == "sheet":
        from openpyxl import Workbook
        t = json.loads(r["body"] or "{}")
        wb = Workbook(); ws = wb.active
        ws.append(t.get("columns", []))
        for row in t.get("rows", []):
            ws.append(row)
        buf = io.BytesIO(); wb.save(buf)
        return (f"{safe}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buf.getvalue())
    if docs_as == "docx":
        from . import workbench
        return (f"{safe}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", workbench._docx(r["title"], r["body"] or ""))
    return (f"{safe}.md", "text/markdown; charset=utf-8", (r["body"] or "").encode("utf-8"))


# ---------- company details: one shared document every employee can read ----------
MARK_A, MARK_B = "<!-- deskmates:basics -->", "<!-- /deskmates:basics -->"
TEMPLATE = """# {name}

{basics}

## Contact
Phone, email, address and opening hours: add them here.

## Products and services
What you sell or do, in plain words.

## Prices and offers
Headline prices, packages, what is not offered.

## How customers get started
The usual steps from first contact to a sale or a booking.

## Rules and policies
Refunds, complaints, calling hours, anything the team must always follow.

## Tone of voice
How the company talks to customers, and anything the team should never say.

## Anything else
Add whatever helps your team do their work.
"""


def _basics():
    from . import voices
    g = lambda k: voices.get_setting(k, "")
    lines = [f"{MARK_A}", "## Basics (kept up to date from Settings → Company; edit everything else freely)", f"- **Company:** {g('company_name') or '(not set)'}",
             f"- **What we do:** {g('company_about') or '(not set)'}"]
    if g("company_website"):
        lines.append(f"- **Website:** {g('company_website')}")
    if g("timezone"):
        lines.append(f"- **Time zone:** {g('timezone')}")
    lines.append(MARK_B)
    return "\n".join(lines)


def _protected(agent, doc):
    """The shared Company details document is edited by the admin and managers only; everyone else reads it."""
    cd = company_doc()
    return bool(cd and cd["id"] == doc["id"] and agent is not None and not agent.get("is_manager"))


def company_doc():
    from . import voices
    did = voices.get_setting("company_doc_id", "")
    return db.q1("SELECT * FROM documents WHERE id=?", (int(did),)) if did and did.isdigit() else None


def sync_company_doc():
    """Create the 'Company details' document in the shared drive's Company folder on first setup, and keep its Basics block current."""
    from . import voices
    name = voices.get_setting("company_name", "")
    if not name:
        return None
    d = company_doc()
    basics = _basics()
    if not d:
        fid = folder_id(0, "Company")
        did = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(0,?,?,?,?,?,?)",
                     ("Company details", "doc", TEMPLATE.format(name=name, basics=basics), fid, db.now(), db.now()))
        voices.set_setting("company_doc_id", str(did))
        return did
    body = d["body"]
    if MARK_A in body and MARK_B in body:
        a, b = body.index(MARK_A), body.index(MARK_B) + len(MARK_B)
        body = body[:a] + basics + body[b:]
    else:
        body = basics + "\n\n" + body
    db.update("documents", d["id"], body=body, updated_at=db.now())
    return d["id"]


def company_excerpt(limit=1500):
    d = company_doc()
    if not d:
        return "", None
    text = re.sub(r"<!--.*?-->", "", d["body"], flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit], d["id"]

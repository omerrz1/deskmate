"""Each employee has their own computer with folders and files, and there is a shared drive (owner 0).
Documents are text; spreadsheets are tables. Agents work with folder PATHS like 'Reports/October'."""
import json
import re

from . import db

DEFAULT_FOLDERS = {"agent": ["Documents", "Reports", "Spreadsheets", "Downloads"], "shared": ["Company", "Reports"]}
_S = {"type": "string"}
_I = {"type": "integer"}
_FOLDER = {**_S, "description": "folder path such as 'Reports/October' (created if missing). Default: Documents or Spreadsheets"}
_SHARED = {"type": "boolean", "description": "true = the shared company drive instead of your own computer"}
_PROJECT = {"type": "string", "description": "a project name: use that project's shared drive instead (you must be a member)"}


# ---------- folders ----------
def ensure_folders(owner):
    for name in DEFAULT_FOLDERS["shared" if owner == 0 else "agent"]:
        folder_id(owner, name, create=True)


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
    folders = db.q("SELECT id,name FROM folders WHERE owner=? AND parent_id IS ? ORDER BY lower(name)", (owner, fid))
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
    if d["kind"] == "file" and fp:
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
    {"name": "create_document", "description": "Write a document (report, memo, plan, notes, minutes) and file it in a folder. Returns its id.",
     "parameters": {"type": "object", "properties": {"title": _S, "body": {**_S, "description": "plain text or simple markdown"}, "folder": _FOLDER, "shared": _SHARED, "project": _PROJECT}, "required": ["title", "body"]}},
    {"name": "create_spreadsheet", "description": "Create a spreadsheet (call log, tracker, budget, lead list) from columns and rows and file it in a folder.",
     "parameters": {"type": "object", "properties": {"title": _S, "columns": {"type": "array", "items": _S}, "rows": {"type": "array", "items": {"type": "array", "items": _S}}, "folder": _FOLDER, "shared": _SHARED, "project": _PROJECT}, "required": ["title", "columns", "rows"]}},
    {"name": "edit_sheet", "description": "Update a spreadsheet: add rows, change individual cells, add columns or delete rows. Rows and columns are numbered from 1 (columns may also be given by header name).",
     "parameters": {"type": "object", "properties": {"id": _I, "add_rows": {"type": "array", "items": {"type": "array", "items": _S}},
                                                    "set_cells": {"type": "array", "items": {"type": "object", "properties": {"row": _I, "column": _S, "value": _S}}},
                                                    "add_columns": {"type": "array", "items": _S}, "delete_rows": {"type": "array", "items": _I}}, "required": ["id"]}},
    {"name": "read_document", "description": "Open a document or spreadsheet by id (works for yours, colleagues' and shared files).", "parameters": {"type": "object", "properties": {"id": _I}, "required": ["id"]}},
    {"name": "update_document", "description": "Replace or append to a document's text, or append rows to a spreadsheet, or rename it.",
     "parameters": {"type": "object", "properties": {"id": _I, "body": _S, "append": _S, "add_rows": {"type": "array", "items": {"type": "array", "items": _S}}, "title": _S}, "required": ["id"]}},
    {"name": "list_documents", "description": "Find files by words in the title or content, across your computer, colleagues' and the shared drive.", "parameters": {"type": "object", "properties": {"query": _S}}},
    {"name": "list_files", "description": "Look inside a folder on your computer (or the shared drive): subfolders and files with ids. Empty path = the top level.", "parameters": {"type": "object", "properties": {"path": _S, "shared": _SHARED, "project": _PROJECT}}},
    {"name": "create_folder", "description": "Make a folder (and any parents) on your computer or the shared drive.", "parameters": {"type": "object", "properties": {"path": _S, "shared": _SHARED, "project": _PROJECT}, "required": ["path"]}},
    {"name": "move_file", "description": "Move a file you own (or a shared one) into another folder.", "parameters": {"type": "object", "properties": {"id": _I, "folder": _S, "shared": _SHARED, "project": _PROJECT}, "required": ["id", "folder"]}},
    {"name": "copy_file", "description": "Copy one of your files to the shared company drive (shared=true) or to a project's drive (project=<name>), keeping your own copy. Use it only when you were asked to share a file, or for a finished project deliverable. Your working files stay on your own computer.",
     "parameters": {"type": "object", "properties": {"id": _I, "folder": {**_S, "description": "folder on the destination, e.g. 'Deliverables'"}, "shared": _SHARED, "project": _PROJECT}, "required": ["id"]}},
    {"name": "delete_file", "description": "Delete one of your own files (or a shared one you created).", "parameters": {"type": "object", "properties": {"id": _I}, "required": ["id"]}},
]
TOOL_NAMES = {t["name"] for t in TOOLS}


def _sheet(d):
    t = json.loads(d["body"] or "{}")
    t.setdefault("columns", []); t.setdefault("rows", [])
    return t


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
        d = db.q1("SELECT * FROM documents WHERE id=?", (a["id"],))
        if not d:
            return {"error": "not found"}, None
        if not team.can_access_doc(agent, d):
            return {"error": "That file is private to someone else. Ask them to share it with you."}, None
        if d["kind"] == "sheet":
            t = _sheet(d)
            return {"id": d["id"], "title": d["title"], "kind": "sheet", "columns": t["columns"], "rows": t["rows"][:200], "total_rows": len(t["rows"])}, "/office?tab=files"
        if d["kind"] == "file":
            return {"id": d["id"], "title": d["title"], "kind": "file", "location": _where(d), "note": "This is an exported file (PDF, Word, Excel or similar) that can't be read as text. Open the original document it was made from (list_documents with a word from the title), or ask whoever made it."}, "/office?tab=files"
        return {"id": d["id"], "title": d["title"], "kind": d["kind"], "location": _where(d), "content": d["body"][:8000]}, "/office?tab=files"
    if name == "update_document":
        d = db.q1("SELECT * FROM documents WHERE id=?", (a["id"],))
        if not d:
            return {"error": "not found"}, None
        if not team.can_access_doc(agent, d):
            return {"error": "That file is private to someone else. Ask them to share it with you."}, None
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
        d = db.q1("SELECT * FROM documents WHERE id=? AND kind='sheet'", (a["id"],))
        if not d:
            return {"error": "no spreadsheet with that id"}, None
        if not team.can_access_doc(agent, d):
            return {"error": "That spreadsheet is private to someone else. Ask them to share it with you."}, None
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
        q = f"%{a.get('query', '')}%"
        rows = db.q("SELECT d.*, g.name owner FROM documents d LEFT JOIN agents g ON g.id=d.agent_id "
                    "WHERE d.title LIKE ? OR (d.kind='doc' AND d.body LIKE ?) ORDER BY d.updated_at DESC LIMIT 120", (q, q))
        rows = [r for r in rows if team.can_access_doc(agent, r)][:25]
        return [{"id": r["id"], "title": r["title"], "kind": r["kind"], "owner": ("a project drive" if r["agent_id"] < 0 else r["owner"] or "shared drive"), "folder": path_of(r["folder_id"])} for r in rows], "/office?tab=files"
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
    if r["kind"] == "file":
        p = db.data_dir() / "files" / Path(r["file_path"]).name
        if not p.exists():
            raise FileNotFoundError("The file is missing")
        ext = p.suffix
        return (f"{safe}{ext if not safe.endswith(ext) else ''}", r["mime"] or "application/octet-stream", p.read_bytes())
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

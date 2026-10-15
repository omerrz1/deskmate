"""One search across the company's work: tasks, emails, files, calendar entries and customers. Each hit says where to open it."""
import re

from . import db


def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _snip(text, q, width=90):
    t = " ".join(str(text or "").split())
    i = t.lower().find(q.lower())
    if i < 0:
        return t[:width]
    a = max(0, i - width // 3)
    return ("…" if a else "") + t[a:a + width] + ("…" if a + width < len(t) else "")


def search(q, limit=5):
    q = (q or "").strip()
    if len(q) < 2:
        return []
    like = _like(q)
    names = {a["id"]: a["name"] for a in db.q("SELECT id,name FROM agents")}
    out = []

    def add(kind, rows, mk):
        if rows:
            out.append({"kind": kind, "items": [mk(r) for r in rows]})

    add("Tasks", db.q("SELECT id,agent_id,title,detail,result,status FROM duties WHERE (title LIKE ?1 ESCAPE '\\' OR detail LIKE ?1 ESCAPE '\\' OR result LIKE ?1 ESCAPE '\\') ORDER BY (status='done'), id DESC LIMIT ?2", (like, limit)),
        lambda r: {"id": r["id"], "title": r["title"], "sub": f"{names.get(r['agent_id'], 'Someone')} · {'done' if r['status'] == 'done' else 'open'}" + ((" · " + _snip(r["result"] or r["detail"], q, 60)) if (r["result"] or r["detail"]) else ""), "to": f"/agents/{r['agent_id']}"})
    add("Emails", db.q("SELECT id,agent_id,folder,from_addr,to_addr,subject,body FROM emails WHERE (subject LIKE ?1 ESCAPE '\\' OR body LIKE ?1 ESCAPE '\\' OR from_addr LIKE ?1 ESCAPE '\\' OR to_addr LIKE ?1 ESCAPE '\\') AND folder IN ('inbox','sent') ORDER BY id DESC LIMIT ?2", (like, limit)),
        lambda r: {"id": r["id"], "title": r["subject"] or "(no subject)", "sub": f"{'Your inbox' if r['agent_id'] == 0 else names.get(r['agent_id'], 'Mailbox')} · {r['from_addr'] if r['folder'] == 'inbox' else 'to ' + r['to_addr']} · {_snip(r['body'], q, 60)}",
                   "to": f"/inbox?open={r['id']}" if r["agent_id"] == 0 else f"/agents/{r['agent_id']}?tab=computer"})
    add("Files", db.q("SELECT id,agent_id,title,kind,body FROM documents WHERE (title LIKE ?1 ESCAPE '\\' OR (kind='doc' AND body LIKE ?1 ESCAPE '\\')) ORDER BY updated_at DESC LIMIT ?2", (like, limit)),
        lambda r: {"id": r["id"], "title": r["title"], "sub": ("Project drive" if (r["agent_id"] or 0) < 0 else "Shared drive" if r["agent_id"] == 0 else f"{names.get(r['agent_id'], 'Someone')}'s computer") + (f" · {_snip(r['body'], q, 60)}" if r["kind"] == "doc" and q.lower() in (r["body"] or "").lower() else ""),
                   "to": f"/projects/{-r['agent_id']}" if (r["agent_id"] or 0) < 0 else "/settings/files" if r["agent_id"] == 0 else f"/agents/{r['agent_id']}?tab=computer&doc={r['id']}"})
    add("Calendar", db.q("SELECT id,agent_id,title,start FROM events WHERE status='planned' AND (title LIKE ?1 ESCAPE '\\' OR notes LIKE ?1 ESCAPE '\\') ORDER BY start DESC LIMIT ?2", (like, limit)),
        lambda r: {"id": r["id"], "title": r["title"], "sub": f"{names.get(r['agent_id'], 'Shared')} · {re.sub(r' +', ' ', __import__('time').strftime('%a %d %b %H:%M', __import__('time').localtime(r['start'])))}", "to": f"/agents/{r['agent_id']}?tab=computer" if r["agent_id"] else "/"})
    add("Customers", db.q("SELECT id,name,company,email,phone FROM contacts WHERE (name LIKE ?1 ESCAPE '\\' OR company LIKE ?1 ESCAPE '\\' OR email LIKE ?1 ESCAPE '\\' OR phone LIKE ?1 ESCAPE '\\') LIMIT ?2", (like, limit)),
        lambda r: {"id": r["id"], "title": r["name"] or r["email"] or r["phone"], "sub": " · ".join(x for x in (r["company"], r["email"], r["phone"]) if x), "to": "/clients"})
    return out

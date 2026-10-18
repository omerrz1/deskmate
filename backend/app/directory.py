"""The list of workspaces (one per company) and which workspace each sign-in name belongs to. This is the only database that spans companies;
everything else a company owns lives in that company's own database (see db.py)."""
import os
import re
import secrets
import sqlite3
import threading
import time

from . import db

PATH = db.DATA_DIR / "directory.db"
SIGNUPS_OPEN = os.getenv("SIGNUPS_OPEN", "true").lower() == "true"
# When set, creating a company needs this code (only the team that runs the server knows it). Delete the setting to open sign-up again. Anyone who is invited into an existing company is not affected.
SIGNUP_CODE = os.getenv("SIGNUP_CODE", "").strip()
MAX_WORKSPACES = int(os.getenv("MAX_WORKSPACES", "1000"))
_lock = threading.RLock()
_c = sqlite3.connect(str(PATH), check_same_thread=False)
_c.row_factory = sqlite3.Row
_c.execute("PRAGMA journal_mode=WAL")
_c.executescript("""
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, name TEXT, owner TEXT, created_at REAL, status TEXT DEFAULT 'active', last_seen REAL);
CREATE TABLE IF NOT EXISTS members(username TEXT PRIMARY KEY COLLATE NOCASE, workspace_id TEXT);
CREATE INDEX IF NOT EXISTS idx_members_ws ON members(workspace_id);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT, topic TEXT, message TEXT, created_at REAL, ip TEXT, status TEXT DEFAULT 'new');
""")
_c.commit()
try:
    os.chmod(PATH, 0o600)
except OSError:
    pass

try:
    _c.execute("ALTER TABLE workspaces ADD COLUMN parent_id TEXT DEFAULT ''")
    _c.commit()
except sqlite3.OperationalError:
    pass

ID_RE = re.compile(r"^[a-f0-9]{12}$")


def _q(sql, args=()):
    with _lock:
        return [dict(r) for r in _c.execute(sql, args).fetchall()]


def _run(sql, args=()):
    with _lock:
        _c.execute(sql, args)
        _c.commit()


def find(username):
    """-> the workspace id a sign-in name belongs to, or None."""
    r = _q("SELECT workspace_id FROM members WHERE username=?", ((username or "").strip(),))
    return r[0]["workspace_id"] if r else None


def get(wid):
    r = _q("SELECT * FROM workspaces WHERE id=?", (wid,))
    return r[0] if r else None


def is_active(wid):
    w = get(wid)
    return bool(w and w["status"] == "active" and (not w.get("parent_id") or is_active(w["parent_id"])))


def active_ids():
    return [w["id"] for w in _q("SELECT id FROM workspaces WHERE status='active' ORDER BY created_at") if is_active(w["id"])]


def count():
    return _q("SELECT COUNT(*) n FROM workspaces")[0]["n"]


def add_member(username, wid):
    _run("INSERT OR REPLACE INTO members(username,workspace_id) VALUES(?,?)", (username.strip(), wid))


def remove_member(username):
    _run("DELETE FROM members WHERE username=?", (username.strip(),))


def create_workspace(name, owner):
    """A new, empty workspace for a company. -> its id. The caller then creates the owner's admin account inside it."""
    if count() >= MAX_WORKSPACES:
        raise ValueError("This service is full right now. Please try again later.")
    if find(owner):
        raise ValueError("That email already has an account. Sign in instead.")
    wid = secrets.token_hex(6)
    _run("INSERT INTO workspaces(id,name,owner,created_at,status,last_seen) VALUES(?,?,?,?,?,?)", (wid, name.strip()[:80] or "My company", owner.strip(), time.time(), "active", time.time()))
    add_member(owner, wid)
    with db.use(wid):  # creates the folders and the database, with every table
        db.q1("SELECT 1 x")
    return wid


def register_main():
    """The original single-company data counts as the workspace 'main', and its admins can sign in as before."""
    if not get(db.MAIN):
        with db.use(db.MAIN):
            name = (db.q1("SELECT value FROM settings WHERE key='company_name'") or {}).get("value") or "Main workspace"
        _run("INSERT INTO workspaces(id,name,owner,created_at,status,last_seen) VALUES(?,?,?,?,?,?)", (db.MAIN, name, "", time.time(), "active", time.time()))
    with db.use(db.MAIN):
        for a in db.q("SELECT username FROM admins"):
            if not find(a["username"]):
                add_member(a["username"], db.MAIN)


def list_all():
    out = _q("SELECT * FROM workspaces ORDER BY created_at DESC")
    for w in out:
        w["members"] = _q("SELECT COUNT(*) n FROM members WHERE workspace_id=?", (w["id"],))[0]["n"]
    return out


def set_status(wid, status):
    _run("UPDATE workspaces SET status=? WHERE id=?", (status, wid))


def touch(wid):
    _run("UPDATE workspaces SET last_seen=? WHERE id=?", (time.time(), wid))


# ---- messages from the public contact form (read by whoever runs this server, under Settings -> Platform)
MAX_MESSAGES = 5000


def add_message(name, email, topic, message, ip=""):
    if _q("SELECT COUNT(*) n FROM messages")[0]["n"] >= MAX_MESSAGES:
        raise ValueError("The inbox is full right now. Please try again later.")
    _run("INSERT INTO messages(name,email,topic,message,created_at,ip,status) VALUES(?,?,?,?,?,?,'new')", (name, email, topic, message, time.time(), ip))


def list_messages(limit=200):
    return _q("SELECT id,name,email,topic,message,created_at,status FROM messages ORDER BY (status='new') DESC, created_at DESC LIMIT ?", (limit,))


def set_message_status(mid, status):
    _run("UPDATE messages SET status=? WHERE id=?", (status, mid))


def delete_message(mid):
    _run("DELETE FROM messages WHERE id=?", (mid,))

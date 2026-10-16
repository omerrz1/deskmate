"""The people who sign in to a workspace, who are not AI employees: owners, admins and members. How they are invited, how they leave,
and which projects they work on. (What each role may do is in access.py; the HTTP side is in people_api.py.)"""
import hashlib
import json
import os
import re
import secrets

from . import auth, db, directory, files, team  # noqa: F401  team: creates the project tables this module adds to

MAX_USERS = int(os.getenv("MAX_USERS_PER_WORKSPACE", "25"))
INVITE_DAYS = 7
EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,200}\.[A-Za-z]{2,}$")

db.run("CREATE TABLE IF NOT EXISTS project_people(project_id INTEGER, admin_id INTEGER, role TEXT DEFAULT 'member', added_by TEXT DEFAULT '', joined_at REAL, PRIMARY KEY(project_id, admin_id))")
db.run("CREATE TABLE IF NOT EXISTS invites(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, name TEXT DEFAULT '', role TEXT DEFAULT 'member', token_hash TEXT UNIQUE, "
       "project_ids TEXT DEFAULT '[]', invited_by TEXT DEFAULT '', created_at REAL, expires_at REAL, accepted_at REAL, revoked_at REAL)")
try:
    db.run("ALTER TABLE project_updates ADD COLUMN person TEXT DEFAULT ''")  # which person wrote an update (agent_id 0 used to mean "the admin")
except Exception:
    pass


class PeopleError(ValueError):
    """Something a person did wrong, in words that can be shown to them."""


class InviteInvalid(PeopleError):
    """The link does not match a waiting invitation. It was used, replaced, cancelled, has expired, or never existed: all look the same from outside."""


def display_name(row):
    return (row.get("display_name") or "").strip() or str(row.get("username") or "").split("@")[0]


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- the list ----------
def _projects_by_person():
    out = {}
    for r in db.q("SELECT pp.admin_id, p.id, p.name FROM project_people pp JOIN projects p ON p.id=pp.project_id WHERE p.status<>'deleted' ORDER BY p.name"):
        out.setdefault(r["admin_id"], []).append({"id": r["id"], "name": r["name"]})
    return out


def listing(session):
    """Everyone in the workspace, then the invitations still waiting. What the admin sees on the People page."""
    from . import access
    last = {r["admin_id"]: r["m"] for r in db.q("SELECT admin_id, MAX(last_seen) m FROM sessions GROUP BY admin_id")}
    projects = _projects_by_person()
    order = {"owner": 0, "admin": 1, "member": 2}
    rows = sorted(db.q("SELECT id,username,role,display_name,title,must_change,created_at,last_login,added_by,totp_on FROM admins"), key=lambda r: (order.get(r["role"], 1), r["id"]))
    people = [{"id": r["id"], "name": display_name(r), "email": r["username"], "role": r["role"], "title": r["title"] or "", "you": r["id"] == session["admin_id"],
               "status": "temporary_password" if r["must_change"] else "active", "last_seen": max(last.get(r["id"]) or 0, r["last_login"] or 0) or None,
               "added_by": r["added_by"], "two_factor": bool(r["totp_on"]), "projects": projects.get(r["id"], []), "can_manage": access.can_manage(session, dict(r))} for r in rows]
    now = db.now()
    invites = [{"id": r["id"], "email": r["email"], "name": r["name"], "role": r["role"], "invited_by": r["invited_by"], "created_at": r["created_at"], "expires_at": r["expires_at"],
                "can_manage": access.is_admin(session) and (access.role_of(session) == "owner" or r["invited_by"] == session["username"])}
               for r in db.q("SELECT * FROM invites WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at>? ORDER BY id DESC", (now,))]
    return {"people": people, "invites": invites, "limit": MAX_USERS, "roles": access.ROLE_INFO}


def directory_for(session):
    """What anyone in the workspace may see of the people: names, roles and jobs. Email addresses only for admins."""
    from . import access
    show_email = access.is_admin(session)
    return [{"id": r["id"], "name": display_name(r), "role": r["role"], "title": r["title"] or "", **({"email": r["username"]} if show_email else {})}
            for r in db.q("SELECT id,username,role,display_name,title FROM admins ORDER BY id")]


# ---------- invitations ----------
def _count():
    return db.q1("SELECT COUNT(*) n FROM admins")["n"] + db.q1("SELECT COUNT(*) n FROM invites WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?", (db.now(),))["n"]


def _new_token():
    return f"{db.current()}.{secrets.token_urlsafe(24)}"  # the workspace id in front lets the public join page find the right company


def invite(session, email, role="member", name="", project_ids=()):
    """Invite someone by email. -> {id, token, email, role, expires_at}. The link is the only secret: it is stored hashed and works once."""
    from . import access
    if not access.is_admin(session):
        raise PeopleError("Only admins can invite people.")
    email = (email or "").strip().lower()
    if not EMAIL_OK.match(email) or len(email) > 80:
        raise PeopleError("Enter a valid email address. It is what they will sign in with.")
    if role not in ("admin", "member"):
        raise PeopleError("Choose Admin or Member.")
    if db.q1("SELECT id FROM admins WHERE lower(username)=?", (email,)) or directory.find(email):
        raise PeopleError("That email already has a Deskmates account. People can belong to one company, so they would need a different email.")
    db.run("UPDATE invites SET revoked_at=? WHERE lower(email)=? AND accepted_at IS NULL AND revoked_at IS NULL", (db.now(), email))  # a new invitation replaces the old one
    if _count() >= MAX_USERS:
        raise PeopleError(f"A company can have up to {MAX_USERS} people, counting invitations that are waiting.")
    pids = []
    if project_ids:
        ids = [int(i) for i in list(project_ids)[:50]]
        pids = [r["id"] for r in db.q(f"SELECT id FROM projects WHERE status<>'deleted' AND id IN ({','.join('?' * len(ids))})", tuple(ids))]
    token = _new_token()
    iid = db.run("INSERT INTO invites(email,name,role,token_hash,project_ids,invited_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                 (email, (name or "").strip()[:60], role, _hash(token), json.dumps(pids), session["username"], db.now(), db.now() + INVITE_DAYS * 86400))
    return {"id": iid, "token": token, "email": email, "role": role, "expires_at": db.now() + INVITE_DAYS * 86400}


def renew(session, invite_id):
    """A fresh link for a waiting invitation (the old link stops working)."""
    row = db.q1("SELECT * FROM invites WHERE id=? AND accepted_at IS NULL AND revoked_at IS NULL", (invite_id,))
    if not row or not _can_manage_invite(session, row):
        raise PeopleError("That invitation was not found.")
    token = _new_token()
    db.run("UPDATE invites SET token_hash=?, expires_at=? WHERE id=?", (_hash(token), db.now() + INVITE_DAYS * 86400, invite_id))
    return {"id": invite_id, "token": token, "email": row["email"], "role": row["role"], "expires_at": db.now() + INVITE_DAYS * 86400}


def revoke(session, invite_id):
    row = db.q1("SELECT * FROM invites WHERE id=? AND accepted_at IS NULL AND revoked_at IS NULL", (invite_id,))
    if not row or not _can_manage_invite(session, row):
        raise PeopleError("That invitation was not found.")
    db.run("UPDATE invites SET revoked_at=? WHERE id=?", (db.now(), invite_id))


def _can_manage_invite(session, row):
    from . import access
    return access.is_admin(session) and (access.role_of(session) == "owner" or row["invited_by"] == session["username"])


def find_invite(token):
    """The waiting invitation this link belongs to, or None. A link that was used, replaced, cancelled or has expired all look the same."""
    if not token or len(token) > 200:
        return None
    row = db.q1("SELECT * FROM invites WHERE token_hash=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?", (_hash(token), db.now()))
    return row


def accept(token, name, password):
    """Turn an invitation into a person who can sign in. -> the new person's row."""
    inv = find_invite(token)
    if not inv:
        raise InviteInvalid("This invitation is no longer valid. Ask the person who invited you for a new link.")
    email = inv["email"]
    if db.q1("SELECT id FROM admins WHERE lower(username)=?", (email,)) or directory.find(email):
        raise PeopleError("That email already has an account. Sign in instead.")
    err = auth.check_policy(password or "", email)
    if err:
        raise PeopleError(err)
    name = (name or inv["name"] or "").strip()[:60]
    if not name:
        raise PeopleError("Tell us your name, so the team knows who you are.")
    auth.create_admin(email, password, role=inv["role"], must_change=False, added_by=inv["invited_by"], display_name=name)
    row = db.q1("SELECT * FROM admins WHERE lower(username)=?", (email,))
    directory.add_member(email, db.current())
    db.run("UPDATE invites SET accepted_at=? WHERE id=?", (db.now(), inv["id"]))
    for pid in json.loads(inv["project_ids"] or "[]"):
        add_to_project(pid, row["id"], by=inv["invited_by"])
    ensure_drive(row["id"])
    return row


# ---------- changing and removing people ----------
def _target(session, admin_id):
    from . import access
    row = db.q1("SELECT * FROM admins WHERE id=?", (admin_id,))
    if not row:
        raise PeopleError("That person was not found.")
    if not access.can_manage(session, dict(row)):
        raise PeopleError("Only the owner, or the person who added them, can do that.")
    return row


def update(session, admin_id, role=None, name=None, title=None):
    row = _target(session, admin_id)
    f = {}
    if role is not None and role != row["role"]:
        if role not in ("admin", "member"):
            raise PeopleError("Choose Admin or Member.")
        f["role"] = role
    if name is not None:
        f["display_name"] = name.strip()[:60]
    if title is not None:
        f["title"] = title.strip()[:60]
    if f:
        db.update("admins", admin_id, **f)
        if "role" in f:
            db.run("DELETE FROM sessions WHERE admin_id=?", (admin_id,))  # they sign in again and get exactly what the new role allows
    return db.q1("SELECT * FROM admins WHERE id=?", (admin_id,))


def remove(session, admin_id, keep_files=True):
    """Take someone out of the company. Their files move to the person removing them (in a folder named after them), or are deleted."""
    row = _target(session, admin_id)
    src, dst = files.person_owner(admin_id), files.person_owner(session["admin_id"])
    ensure_drive(session["admin_id"])
    if keep_files:
        files.transfer_drive(src, dst, f"From {display_name(row)}")
    else:
        files.wipe_drive(src)
    db.run("DELETE FROM sessions WHERE admin_id=?", (admin_id,))
    db.run("DELETE FROM project_people WHERE admin_id=?", (admin_id,))
    db.run("DELETE FROM admins WHERE id=?", (admin_id,))
    directory.remove_member(row["username"])
    return row


def ensure_drive(admin_id):
    files.ensure_folders(files.person_owner(admin_id))


# ---------- projects ----------
def in_project(project_id, admin_id):
    return bool(db.q1("SELECT 1 x FROM project_people WHERE project_id=? AND admin_id=?", (project_id, admin_id)))


def add_to_project(project_id, admin_id, by=""):
    db.run("INSERT OR IGNORE INTO project_people(project_id,admin_id,role,added_by,joined_at) VALUES(?,?,?,?,?)", (project_id, admin_id, "member", by, db.now()))


def remove_from_project(project_id, admin_id):
    db.run("DELETE FROM project_people WHERE project_id=? AND admin_id=?", (project_id, admin_id))


def projects_of(admin_id):
    return [r["project_id"] for r in db.q("SELECT pp.project_id FROM project_people pp JOIN projects p ON p.id=pp.project_id WHERE pp.admin_id=? AND p.status<>'deleted'", (admin_id,))]


def people_of_project(project_id):
    return [{"id": r["id"], "name": display_name(r), "role": r["role"], "title": r["title"] or ""}
            for r in db.q("SELECT a.id,a.username,a.role,a.display_name,a.title FROM project_people pp JOIN admins a ON a.id=pp.admin_id WHERE pp.project_id=? ORDER BY a.id", (project_id,))]

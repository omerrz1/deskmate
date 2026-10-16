"""Who may do what in a workspace.

The people who sign in have one of three roles. None of them is an AI employee:

  owner   everything, including closing the company. Exactly one per company.
  admin   runs the workspace: the AI team, customers, calls, files, settings, and the other people.
  member  a collaborator: sees only the projects they were added to, their own files and the AI team's names.

Members are refused by default. A request from a member is allowed only if it matches the short list below, and the code behind each of those
routes still checks the details (which project, which drive). Everything else, including every route added in the future, stays closed to them."""
import re

from . import db, files

ROLES = ("owner", "admin", "member")
ROLE_INFO = {
    "owner": {"label": "Owner", "blurb": "Everything, including closing the company."},
    "admin": {"label": "Admin", "blurb": "Runs the workspace: the AI team, customers, calls, files, settings and people."},
    "member": {"label": "Member", "blurb": "Works on the projects they are added to, with the admins and the AI team. Cannot see anything else."},
}


def role_of(session):
    return (session or {}).get("role") or "admin"


def is_admin(session):
    return role_of(session) in ("owner", "admin")


def is_member(session):
    return role_of(session) == "member"


_ID = r"\d+"
_MEMBER_ROUTES = [  # (methods, path) a member may call
    ({"GET"}, r"/api/me"), ({"PATCH"}, r"/api/me"),
    ({"GET"}, r"/api/stream"),
    ({"GET"}, r"/api/people/directory"),
    ({"GET"}, r"/api/agents"),  # the AI team's names and jobs only: the handler trims what a member gets
    ({"GET"}, r"/api/projects"), ({"GET"}, rf"/api/projects/{_ID}"),
    ({"POST"}, rf"/api/projects/{_ID}/updates"), ({"POST"}, rf"/api/projects/{_ID}/tasks"),
    ({"GET"}, r"/api/drives"),
    ({"GET"}, r"/api/fs"), ({"GET"}, r"/api/fs/folders"), ({"GET"}, r"/api/fs/search"),
    ({"POST"}, r"/api/fs/(folder|file|upload)"), ({"PATCH", "DELETE"}, rf"/api/fs/folder/{_ID}"),
    ({"GET", "PATCH", "DELETE"}, rf"/api/docs/{_ID}"), ({"GET"}, rf"/api/docs/{_ID}/download"), ({"PUT"}, rf"/api/docs/{_ID}/table"), ({"POST"}, rf"/api/docs/{_ID}/copy"),
    ({"POST"}, r"/api/auth/(logout|password)"), ({"GET"}, r"/api/auth/(sessions|2fa)"), ({"DELETE"}, rf"/api/auth/sessions/{_ID}"),
    ({"POST"}, r"/api/auth/2fa/(setup|enable|disable|backup-codes)"),
]
_MEMBER_ROUTES = [(m, re.compile(p)) for m, p in _MEMBER_ROUTES]


def member_allowed(method, path):
    path = path.rstrip("/") or "/"
    return any(method in m and r.fullmatch(path) for m, r in _MEMBER_ROUTES)


# ---------- whose files a person may use ----------
def can_use_drive(session, owner):
    """A person's own files are theirs alone, even from the owner. Admins can use every other drive. A member can use only the drives of their projects."""
    kind = files.owner_kind(owner)
    if kind == "person":
        return files.person_of(owner) == session["admin_id"]
    if is_admin(session):
        return True
    if kind == "project":
        from . import people
        return people.in_project(-owner, session["admin_id"])
    return False


def can_use_doc(session, doc):
    return bool(doc) and can_use_drive(session, doc["agent_id"])


def can_use_folder(session, folder):
    return bool(folder) and can_use_drive(session, folder["owner"])


# ---------- who may manage whom ----------
def can_manage(session, row):
    """The owner manages everyone but themselves. An admin manages the people they added. A member manages nobody."""
    if row["role"] == "owner" or row["id"] == session["admin_id"] or not is_admin(session):
        return False
    return role_of(session) == "owner" or (row.get("added_by") or "") == session["username"]

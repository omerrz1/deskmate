"""People: who is in the workspace, how they are invited, a person's own profile, the drives they can open, and who is on which project."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import access, agents, auth, db, files, people, voices

router = APIRouter(prefix="/api")


def _s(request):
    return request.state.session


def _admin(request):
    s = _s(request)
    if not access.is_admin(s):
        raise HTTPException(403, "Only admins can do that.")
    return s


def _fail(e):
    return HTTPException(404 if isinstance(e, people.InviteInvalid) else 400, str(e))


def _company():
    return voices.get_setting("company_name", "") or "your company"


# ---------- me ----------
@router.get("/me")
def me(request: Request):
    s = _s(request)
    row = db.q1("SELECT id,username,role,display_name,title FROM admins WHERE id=?", (s["admin_id"],))
    return {"id": row["id"], "email": row["username"], "name": people.display_name(row), "title": row["title"] or "", "role": row["role"],
            "role_label": access.ROLE_INFO.get(row["role"], access.ROLE_INFO["admin"])["label"], "is_admin": access.is_admin(s),
            "workspace": {"id": s["ws"], "name": _company()}, "drive": str(files.person_owner(row["id"]))}


class MePatch(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None


@router.patch("/me")
def me_patch(p: MePatch, request: Request):
    s = _s(request)
    f = {}
    if p.name is not None:
        if not p.name.strip():
            raise HTTPException(400, "Your name can't be empty.")
        f["display_name"] = p.name.strip()[:60]
    if p.title is not None:
        f["title"] = p.title.strip()[:60]
    if f:
        db.update("admins", s["admin_id"], **f)
    return me(request)


# ---------- the people ----------
@router.get("/people")
def people_list(request: Request):
    return people.listing(_admin(request))


@router.get("/people/directory")
def people_directory(request: Request):
    return people.directory_for(_s(request))


class InviteIn(BaseModel):
    email: str
    role: str = "member"
    name: str = ""
    project_ids: List[int] = []


@router.post("/people/invite")
def people_invite(b: InviteIn, request: Request):
    s = _admin(request)
    try:
        inv = people.invite(s, b.email, b.role, b.name, b.project_ids)
    except people.PeopleError as e:
        raise _fail(e)
    auth.sec_log(f"'{s['username']}' invited '{inv['email']}' as {inv['role']}", request.state.ip, request.state.ua)
    return {"id": inv["id"], "email": inv["email"], "role": inv["role"], "expires_at": inv["expires_at"], "join_path": f"/join/{inv['token']}", "company": _company()}


@router.post("/people/invites/{iid}/renew")
def invite_renew(iid: int, request: Request):
    s = _admin(request)
    try:
        inv = people.renew(s, iid)
    except people.PeopleError as e:
        raise _fail(e)
    auth.sec_log(f"'{s['username']}' made a new invitation link for '{inv['email']}'", request.state.ip, request.state.ua)
    return {"id": inv["id"], "email": inv["email"], "role": inv["role"], "expires_at": inv["expires_at"], "join_path": f"/join/{inv['token']}", "company": _company()}


@router.post("/people/invites/{iid}/revoke")
def invite_revoke(iid: int, request: Request):
    s = _admin(request)
    try:
        people.revoke(s, iid)
    except people.PeopleError as e:
        raise _fail(e)
    auth.sec_log(f"'{s['username']}' cancelled an invitation", request.state.ip, request.state.ua)
    return {"ok": True}


class PersonPatch(BaseModel):
    role: Optional[str] = None
    name: Optional[str] = None
    title: Optional[str] = None


@router.patch("/people/{pid}")
def person_patch(pid: int, p: PersonPatch, request: Request):
    s = _admin(request)
    try:
        row = people.update(s, pid, p.role, p.name, p.title)
    except people.PeopleError as e:
        raise _fail(e)
    if p.role is not None:
        auth.sec_log(f"'{s['username']}' made '{row['username']}' a {row['role']}", request.state.ip, request.state.ua)
    return {"ok": True}


@router.delete("/people/{pid}")
def person_remove(pid: int, request: Request, keep_files: bool = True):
    s = _admin(request)
    try:
        row = people.remove(s, pid, keep_files)
    except people.PeopleError as e:
        raise _fail(e)
    auth.sec_log(f"'{row['username']}' was removed by '{s['username']}'" + ("" if keep_files else " and their files were deleted"), request.state.ip, request.state.ua)
    return {"ok": True}


# ---------- who is on a project ----------
class PersonIn(BaseModel):
    person_id: int


@router.post("/projects/{pid}/people")
def project_add_person(pid: int, b: PersonIn, request: Request):
    s = _admin(request)
    if not db.q1("SELECT id FROM projects WHERE id=? AND status<>'deleted'", (pid,)) or not db.q1("SELECT id FROM admins WHERE id=?", (b.person_id,)):
        raise HTTPException(404)
    people.add_to_project(pid, b.person_id, by=s["username"])
    return {"ok": True}


@router.delete("/projects/{pid}/people/{person_id}")
def project_remove_person(pid: int, person_id: int, request: Request):
    _admin(request)
    people.remove_from_project(pid, person_id)
    return {"ok": True}


# ---------- the drives a person can open ----------
@router.get("/drives")
def drives(request: Request):
    """Everywhere this person can look for files, grouped for the Files page."""
    s = _s(request)
    me_id = s["admin_id"]
    mine = files.person_owner(me_id)
    people.ensure_drive(me_id)
    out = [{"owner": str(mine), "label": "My files", "kind": "person", "group": "Mine", "hint": "Only you can see these"}]
    if access.is_admin(s):
        files.ensure_folders(0)
        out.append({"owner": "shared", "label": "Company drive", "kind": "shared", "group": "Company", "hint": "Shared by everyone in the company"})
        for p in db.q("SELECT id,name FROM projects WHERE status<>'deleted' ORDER BY name"):
            out.append({"owner": str(-p["id"]), "label": p["name"], "kind": "project", "group": "Projects", "hint": "A project's shared drive"})
        for a in agents.listing():
            files.ensure_folders(a["id"])
            out.append({"owner": str(a["id"]), "label": f"{a['name']}'s computer", "kind": "agent", "group": "AI team", "hint": a.get("role") or "AI employee", "color": a.get("color")})
    else:
        for pid in people.projects_of(me_id):
            p = db.q1("SELECT id,name FROM projects WHERE id=?", (pid,))
            out.append({"owner": str(-p["id"]), "label": p["name"], "kind": "project", "group": "Projects", "hint": "A project you work on"})
    return out


# ---------- the public invitation page (no sign-in yet: the link is the key) ----------
@router.get("/invites/{token}")
def invite_info(token: str):
    inv = people.find_invite(token)
    if not inv:
        raise HTTPException(404, "This invitation is no longer valid. Ask the person who invited you for a new link.")
    by = db.q1("SELECT username,display_name FROM admins WHERE username=?", (inv["invited_by"],))
    info = access.ROLE_INFO.get(inv["role"], access.ROLE_INFO["member"])
    return {"company": _company(), "email": inv["email"], "name": inv["name"], "role": inv["role"], "role_label": info["label"], "role_blurb": info["blurb"],
            "invited_by": people.display_name(by) if by else "", "expires_at": inv["expires_at"]}


class AcceptIn(BaseModel):
    name: str = ""
    password: str


@router.post("/invites/{token}/accept")
def invite_accept(token: str, b: AcceptIn, request: Request):
    try:
        row = people.accept(token, b.name, b.password)
    except people.PeopleError as e:
        raise _fail(e)
    ip, ua = request.state.ip, request.state.ua
    cookie, csrf = auth.start_session(row["id"], ip, ua)
    auth.sec_log(f"'{row['username']}' accepted an invitation and joined as {row['role']}", ip, ua)
    r = JSONResponse({"ok": True, "username": row["username"]})
    for c in auth.cookie_headers(cookie, csrf):
        r.headers.append("set-cookie", c)
    return r

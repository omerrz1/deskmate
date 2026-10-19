"""Company-owned, isolated workspaces. Identity and invitations stay in the company DB."""
import secrets
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from . import db, directory

router = APIRouter(prefix='/api/workspaces')
db.run("CREATE TABLE IF NOT EXISTS company_workspaces(id TEXT PRIMARY KEY, name TEXT, owner_id INTEGER, visibility TEXT, created_at REAL)")
db.run("CREATE TABLE IF NOT EXISTS workspace_members(workspace_id TEXT, person_id INTEGER, accepted INTEGER DEFAULT 0, PRIMARY KEY(workspace_id,person_id))")
try:
    db.run("ALTER TABLE sessions ADD COLUMN active_workspace TEXT DEFAULT ''")
except Exception:
    pass


def resolve(session):
    wid = session.get('active_workspace')
    if not wid:
        return session
    with db.use(session['ws']):
        row = db.q1('SELECT w.* FROM company_workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE w.id=? AND m.person_id=? AND m.accepted=1', (wid, session['admin_id']))
    if not row or not directory.is_active(wid):
        return session
    with db.use(wid):
        person = db.q1('SELECT id FROM admins WHERE username=?', (session['username'],))
    if not person:
        return session
    return {**session, 'company_ws': session['ws'], 'company_admin_id': session['admin_id'], 'company_role': session['role'], 'ws': wid,
            'admin_id': person['id'], 'role': 'owner' if row['owner_id'] == session['admin_id'] else 'admin'}


class NewWorkspace(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    visibility: str = 'private'


@router.get('')
def listing(request: Request):
    s = request.state.session
    rows = db.q('SELECT w.*, m.accepted FROM company_workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.person_id=? ORDER BY w.created_at', (s['admin_id'],))
    return {'current': resolve(s)['ws'], 'company': directory.get(s['ws'])['name'], 'company_id': s['ws'], 'workspaces': [{**w, 'is_owner': w['owner_id'] == s['admin_id']} for w in rows]}


@router.post('')
def create(body: NewWorkspace, request: Request):
    if not body.name.strip() or body.visibility not in ('private', 'shared'):
        raise HTTPException(400, 'Choose a name and private or shared visibility.')
    s = request.state.session
    wid = secrets.token_hex(6)
    if directory.count() >= directory.MAX_WORKSPACES:
        raise HTTPException(409, 'Workspace limit reached.')
    directory._run('INSERT INTO workspaces(id,name,owner,created_at,status,last_seen) VALUES(?,?,?,?,?,?)', (wid, body.name.strip(), s['username'], db.now(), 'active', db.now()))
    directory._run('UPDATE workspaces SET parent_id=? WHERE id=?', (s['ws'], wid))
    db.run('INSERT INTO company_workspaces VALUES(?,?,?,?,?)', (wid, body.name.strip(), s['admin_id'], body.visibility, db.now()))
    db.run('INSERT INTO workspace_members VALUES(?,?,1)', (wid, s['admin_id']))
    _provision(wid, s['admin_id'], 'owner')
    with db.use(wid):
        db.run("INSERT OR REPLACE INTO settings(key,value) VALUES('company_name',?)", (body.name.strip(),))
    return {'id': wid}


def _provision(wid, person_id, role='admin'):
    person = db.q1('SELECT * FROM admins WHERE id=?', (person_id,))
    with db.use(wid):
        # No independent login or password copy: company identity remains authoritative.
        db.run("INSERT OR IGNORE INTO admins(username,password_hash,display_name,title,role,created_at) VALUES(?,?,?,?,?,?)", (person['username'], '!', person['display_name'], person['title'], role, db.now()))


@router.post('/{wid}/switch')
def switch(wid: str, request: Request):
    s = request.state.session
    if wid != s['ws']:
        if not db.q1('SELECT 1 FROM workspace_members WHERE workspace_id=? AND person_id=? AND accepted=1', (wid, s['admin_id'])) or not directory.is_active(wid):
            raise HTTPException(404, 'Workspace not found.')
    db.run('UPDATE sessions SET active_workspace=? WHERE id=?', ('' if wid == s['ws'] else wid, s['id']))
    return {'ok': True}


class Invitation(BaseModel):
    username: str


@router.post('/{wid}/invite')
def invite(wid: str, body: Invitation, request: Request):
    w = db.q1('SELECT * FROM company_workspaces WHERE id=? AND owner_id=?', (wid, request.state.session['admin_id']))
    if not w:
        raise HTTPException(404, 'Workspace not found.')
    if w['visibility'] == 'private':
        raise HTTPException(400, 'Private workspaces are just for you. Create a shared workspace to invite colleagues.')
    person = db.q1('SELECT id FROM admins WHERE lower(username)=lower(?)', (body.username.strip(),))
    if not person:
        raise HTTPException(404, 'Ask a company admin to add this colleague to the company first.')
    db.run('INSERT OR IGNORE INTO workspace_members VALUES(?,?,0)', (wid, person['id']))
    return {'ok': True}


@router.post('/{wid}/accept')
def accept(wid: str, request: Request):
    pid = request.state.session['admin_id']
    if not db.q1('SELECT 1 FROM workspace_members WHERE workspace_id=? AND person_id=?', (wid, pid)):
        raise HTTPException(404, 'Invitation not found.')
    _provision(wid, pid)
    db.run('UPDATE workspace_members SET accepted=1 WHERE workspace_id=? AND person_id=?', (wid, pid))
    return {'ok': True}


@router.delete('/{wid}/members/{pid}')
def remove(wid: str, pid: int, request: Request):
    if not db.q1('SELECT 1 FROM company_workspaces WHERE id=? AND owner_id=?', (wid, request.state.session['admin_id'])) or pid == request.state.session['admin_id']:
        raise HTTPException(403, 'Only the workspace owner can remove another member.')
    db.run('DELETE FROM workspace_members WHERE workspace_id=? AND person_id=?', (wid, pid))
    return {'ok': True}


@router.get('/{wid}/members')
def members(wid: str, request: Request):
    if not db.q1('SELECT 1 FROM company_workspaces WHERE id=? AND owner_id=?', (wid, request.state.session['admin_id'])):
        raise HTTPException(404, 'Workspace not found.')
    return db.q('SELECT a.id, a.username, a.display_name, m.accepted FROM workspace_members m JOIN admins a ON a.id=m.person_id WHERE m.workspace_id=?', (wid,))

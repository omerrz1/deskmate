import contextlib
import contextvars
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from . import bus

# Every company ("workspace") has its own database and folders. Which one a piece of code is talking to is decided per request (from the
# signed-in session) or per background job: set it with `with db.use(workspace_id):`. The original single-company data lives in the workspace "main".
MAIN = "main"
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
REC_DIR = DATA_DIR / "tmp-audio"  # the main workspace's short-lived playback files; other workspaces use rec_dir()
REC_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "aicalls.db"

_tenant = contextvars.ContextVar("workspace", default=MAIN)
_conns = {}
_conns_lock = threading.Lock()
_template = None

_lock = threading.RLock()
_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conns[MAIN] = (_conn, _lock)


def ws_dir(tid):
    return DATA_DIR if tid == MAIN else DATA_DIR / "ws" / tid


def current():
    """The workspace this code is working for ('' means the request has none, and any data access is refused)."""
    return _tenant.get()


@contextlib.contextmanager
def use(tid):
    tok = _tenant.set(tid)
    try:
        yield
    finally:
        _tenant.reset(tok)


def set_current(tid):
    return _tenant.set(tid)


def reset_current(tok):
    _tenant.reset(tok)


def data_dir():
    return ws_dir(current())


def rec_dir():
    d = data_dir() / "tmp-audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path():
    return data_dir() / "aicalls.db"


def _template_ddl():
    """Every table and index the app defines, read from the main workspace once all modules have set it up."""
    global _template
    if _template is None:
        with _lock:
            rows = _conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, rowid").fetchall()
        _template = [(r["type"], r["name"], r["tbl_name"], r["sql"]) for r in rows]
    return _template


def sync_schema(conn):
    """Make a workspace's database match the app: create missing tables and indexes, add missing columns."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    for typ, name, _tbl, sql in _template_ddl():
        if name not in have:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass
    for typ, name, _tbl, _sql in _template_ddl():
        if typ != "table":
            continue
        with _lock:
            want = _conn.execute(f"PRAGMA table_info({name})").fetchall()
        got = {r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()}
        for c in want:
            if c[1] not in got and not (c[3] and c[4] is None):  # skip NOT NULL columns without a default: they cannot be added later
                dflt = f" DEFAULT {c[4]}" if c[4] is not None else ""
                try:
                    conn.execute(f"ALTER TABLE {name} ADD COLUMN {c[1]} {c[2] or ''}{dflt}")
                except sqlite3.OperationalError:
                    pass
    conn.commit()


def _open(tid):
    path = ws_dir(tid) / "aicalls.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sync_schema(conn)
    try:
        os.chmod(path, 0o600)
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    return conn, threading.RLock()


def _get(tid=None):
    tid = current() if tid is None else tid
    if not tid:
        raise RuntimeError("No workspace is selected for this request.")
    c = _conns.get(tid)
    if c is None:
        with _conns_lock:
            c = _conns.get(tid)
            if c is None:
                c = _conns[tid] = _open(tid)
    return c


def close(tid):
    """Close a workspace's database (used when it is deleted)."""
    with _conns_lock:
        c = _conns.pop(tid, None)
    if c and tid != MAIN:
        try:
            c[0].close()
        except Exception:
            pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, filename TEXT, columns TEXT,
  row_count INTEGER DEFAULT 0, created_at REAL, source_doc_id INTEGER);
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id INTEGER, name TEXT, phone TEXT,
  email TEXT, company TEXT, language TEXT DEFAULT 'English', status TEXT DEFAULT 'new',
  tags TEXT DEFAULT '', notes TEXT DEFAULT '', extra TEXT DEFAULT '{}',
  do_not_call INTEGER DEFAULT 0, last_outcome TEXT, last_call_at REAL,
  report TEXT, report_at REAL, created_at REAL);
CREATE TABLE IF NOT EXISTS campaigns(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, objective TEXT, instructions TEXT DEFAULT '',
  filters TEXT DEFAULT '{}', status TEXT DEFAULT 'draft', max_attempts INTEGER DEFAULT 3,
  retry_delay_min INTEGER DEFAULT 1, voice TEXT, created_at REAL, started_at REAL);
CREATE TABLE IF NOT EXISTS calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER, campaign_id INTEGER,
  attempt INTEGER DEFAULT 1, status TEXT DEFAULT 'queued', outcome TEXT, sentiment TEXT,
  summary TEXT, key_points TEXT DEFAULT '[]', follow_up TEXT, language TEXT,
  transcript TEXT DEFAULT '[]', mode TEXT DEFAULT 'live', persona TEXT,
  not_before REAL DEFAULT 0, created_at REAL, ringing_at REAL, answered_at REAL, ended_at REAL,
  duration REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, message TEXT, meta TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_contact ON calls(contact_id);
CREATE INDEX IF NOT EXISTS idx_calls_campaign_contact ON calls(campaign_id,contact_id,status);
CREATE INDEX IF NOT EXISTS idx_calls_due ON calls(campaign_id,status,not_before,id);
CREATE INDEX IF NOT EXISTS idx_contacts_ds ON contacts(dataset_id);
"""
with _lock:
    _conn.executescript(SCHEMA)
    for stmt in ("ALTER TABLE datasets ADD COLUMN source_doc_id INTEGER", "ALTER TABLE calls ADD COLUMN recording TEXT", "ALTER TABLE calls ADD COLUMN report TEXT", "ALTER TABLE calls ADD COLUMN report_sheet_id INTEGER", "ALTER TABLE calls ADD COLUMN report_agent_id INTEGER", "ALTER TABLE calls ADD COLUMN report_row INTEGER", "ALTER TABLE calls ADD COLUMN report_status_column TEXT DEFAULT 'status'", "ALTER TABLE campaigns ADD COLUMN institution TEXT", "ALTER TABLE campaigns ADD COLUMN kb_id INTEGER", "ALTER TABLE campaigns ADD COLUMN followup TEXT", "ALTER TABLE campaigns ADD COLUMN call_window TEXT", "ALTER TABLE campaigns ADD COLUMN start_at REAL", "ALTER TABLE campaigns ADD COLUMN kb_categories TEXT", "ALTER TABLE campaigns ADD COLUMN autopilot INTEGER DEFAULT 0", "ALTER TABLE campaigns ADD COLUMN concurrency INTEGER DEFAULT 3", "ALTER TABLE calls ADD COLUMN reason TEXT", "ALTER TABLE calls ADD COLUMN report_meeting_id INTEGER", "ALTER TABLE calls ADD COLUMN report_posted INTEGER DEFAULT 0", "ALTER TABLE calls ADD COLUMN voice_engine TEXT", "ALTER TABLE campaigns ADD COLUMN after_sms TEXT DEFAULT ''", "ALTER TABLE campaigns ADD COLUMN after_sms_when TEXT DEFAULT 'interested'", "ALTER TABLE calls ADD COLUMN style TEXT", "ALTER TABLE calls ADD COLUMN after_call TEXT", "ALTER TABLE campaigns ADD COLUMN report_meeting_id INTEGER", "ALTER TABLE campaigns ADD COLUMN report_posted INTEGER DEFAULT 0"):
        try:
            _conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    _conn.commit()
    _conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_datasets_source_doc ON datasets(source_doc_id) WHERE source_doc_id IS NOT NULL")
    _conn.commit()


def now():
    return time.time()


def q(sql, args=()):
    conn, lock = _get()
    with lock:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def run(sql, args=()):
    tid = current()
    conn, lock = _get()
    with lock:
        cur = conn.execute(sql, args)
        conn.commit()
        bus.publish_sql(sql, tid)
        return cur.lastrowid


def many(sql, rows):
    tid = current()
    conn, lock = _get()
    with lock:
        conn.executemany(sql, rows)
        conn.commit()
        bus.publish_sql(sql, tid)


def update(table, id_, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in fields.values()]
    run(f"UPDATE {table} SET {cols} WHERE id=?", vals + [id_])


def log(kind, message, **meta):
    run("INSERT INTO activity(ts,kind,message,meta) VALUES(?,?,?,?)",
        (now(), kind, message, json.dumps(meta, default=str)))


def jload(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


# ---------- small containers whose contents are kept apart per workspace ----------
class TenantSet:
    """A set that each workspace sees only its own part of (for in-memory state such as 'jobs running right now')."""
    def __init__(self):
        self._d = {}

    def _s(self):
        return self._d.setdefault(current(), set())

    def add(self, x):
        self._s().add(x)

    def discard(self, x):
        self._s().discard(x)

    def clear(self):
        self._s().clear()

    def __contains__(self, x):
        return x in self._s()

    def __iter__(self):
        return iter(list(self._s()))

    def __len__(self):
        return len(self._s())


class TenantDict:
    """A dict that each workspace sees only its own part of. `factory` makes the starting value for a workspace that has none yet."""
    def __init__(self, factory=dict):
        self._f, self._d = factory, {}

    def _m(self):
        t = current()
        if t not in self._d:
            self._d[t] = self._f()
        return self._d[t]

    def __getitem__(self, k):
        return self._m()[k]

    def __setitem__(self, k, v):
        self._m()[k] = v

    def __contains__(self, k):
        return k in self._m()

    def get(self, k, default=None):
        return self._m().get(k, default)

    def pop(self, k, *d):
        return self._m().pop(k, *d)

    def items(self):
        return list(self._m().items())

    def clear(self):
        self._m().clear()


def carry(coro):
    """Wrap a coroutine so it runs inside the workspace that is current now, even when it is started from another thread."""
    tid = current()

    async def _run():
        tok = _tenant.set(tid)
        try:
            return await coro
        finally:
            _tenant.reset(tok)
    return _run()


# Connections belong to a company; ordinary q/run continue to address the workspace.
def company():
    from . import directory
    tid = current()
    if not tid:
        raise RuntimeError('No workspace is selected for this request.')
    return (directory.get(tid) or {}).get('parent_id') or tid


def company_q(sql, args=()):
    with use(company()):
        return q(sql, args)


def company_q1(sql, args=()):
    rows = company_q(sql, args)
    return rows[0] if rows else None


def company_run(sql, args=()):
    with use(company()):
        result = run(sql, args)
        _publish_company(sql)
        return result


def _publish_company(sql):
    from . import directory
    for row in directory._q("SELECT id FROM workspaces WHERE parent_id=? AND status='active'", (current(),)):
        bus.publish_sql(sql, row['id'])


def company_update(table, id_, **fields):
    with use(company()):
        result = update(table, id_, **fields)
        _publish_company('UPDATE ' + table)
        return result


def company_scope(fn):
    """Use company-owned state for a small service function, restoring workspace context."""
    import functools
    import inspect
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def asynchronous(*args, **kwargs):
            with use(company()):
                return await fn(*args, **kwargs)
        return asynchronous
    @functools.wraps(fn)
    def synchronous(*args, **kwargs):
        with use(company()):
            return fn(*args, **kwargs)
    return synchronous

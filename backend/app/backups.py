"""Backups: a daily copy of the whole workspace (database and files), kept for a number of days, with one-click restore.
A backup holds no API keys that can be used elsewhere: they are encrypted with a key that stays on this server (see secretbox.py)."""
import re
import shutil
import sqlite3
import time
import zipfile

from . import db

NAME = re.compile(r"^[A-Za-z0-9._-]{3,80}\.db$")


def _setting(key, default=""):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def _put(key, value):
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


def _dir():
    return db.data_dir() / "backups"


def _files():
    return db.data_dir() / "files"


def settings():
    return {"schedule": _setting("backup_schedule", "daily"), "keep": int(_setting("backup_keep", "14") or 14), "last": float(_setting("backup_last", "0") or 0)}


def save_settings(schedule=None, keep=None):
    if schedule in ("off", "daily"):
        _put("backup_schedule", schedule)
    if keep is not None:
        _put("backup_keep", str(max(1, min(int(keep), 90))))


def _kind(name):
    return "scheduled" if name.startswith("auto-") else "manual" if name.startswith("manual-") else "before restore" if name.startswith("before-restore-") else "before delete" if name.startswith("before-delete-") else "other"


def listing():
    _dir().mkdir(parents=True, exist_ok=True)
    out = []
    for f in _dir().glob("*.db"):
        if not NAME.match(f.name):
            continue
        z = f.with_suffix(".files.zip")
        out.append({"name": f.name, "kind": _kind(f.name), "size": f.stat().st_size + (z.stat().st_size if z.exists() else 0), "created_at": f.stat().st_mtime, "has_files": z.exists()})
    return sorted(out, key=lambda x: -x["created_at"])


def create(kind="manual"):
    """Copy the database (safely, while the app runs) and the files folder. -> the backup's name"""
    _dir().mkdir(parents=True, exist_ok=True)
    name = f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    dest = _dir() / name
    src, out = sqlite3.connect(db.db_path()), sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close(); src.close()
    dest.chmod(0o600)
    FILES = _files()
    if FILES.exists() and any(FILES.rglob("*")):
        z = dest.with_suffix(".files.zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in FILES.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(FILES))
        z.chmod(0o600)
    if kind == "auto":
        _prune()
    return name


def _prune():
    keep = settings()["keep"]
    auto = [b for b in listing() if b["kind"] == "scheduled"]
    for b in auto[keep:]:
        delete(b["name"])


def path_of(name):
    if not NAME.match(name or ""):
        raise ValueError("That is not a backup name.")
    p = _dir() / name
    if not p.exists():
        raise FileNotFoundError("That backup no longer exists.")
    return p


def delete(name):
    p = path_of(name)
    p.unlink()
    z = p.with_suffix(".files.zip")
    if z.exists():
        z.unlink()


def restore(name):
    """Replace the live workspace with a backup. A safety copy of the current state is made first."""
    p = path_of(name)
    chk = sqlite3.connect(p)
    try:
        if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("That backup file is damaged and was not restored.")
    finally:
        chk.close()
    safety = create("before-restore")
    src = sqlite3.connect(p)
    try:
        conn, lock = db._get()
        with lock:
            src.backup(conn)
            conn.commit()
    finally:
        src.close()
    FILES = _files()
    z = p.with_suffix(".files.zip")
    if z.exists():
        if FILES.exists():
            shutil.rmtree(FILES, ignore_errors=True)
        FILES.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            for m in zf.namelist():
                target = (FILES / m).resolve()
                if FILES.resolve() in target.parents or target == FILES.resolve():  # never write outside the files folder
                    zf.extract(m, FILES)
    from . import agents
    agents.RUNNING.clear()
    db.log("security", f"Workspace restored from backup {name}")
    return safety


async def tick():
    """Daily at about 03:00 (or on first run after that), when scheduled backups are on."""
    s = settings()
    if s["schedule"] != "daily":
        return
    t = time.localtime()
    if t.tm_hour >= 3 and time.time() - s["last"] > 22 * 3600:
        _put("backup_last", str(time.time()))
        create("auto")
        db.log("agent", "Daily backup saved")

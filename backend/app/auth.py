"""Admin authentication: Argon2id password hashes, server-side revocable sessions, lockout, audit log."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from . import db

SESSION_COOKIE = "deskmates_session"
CSRF_COOKIE = "deskmates_csrf"
SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))       # absolute lifetime
IDLE_MINUTES = float(os.getenv("SESSION_IDLE_MINUTES", "120"))  # sign out after this long without activity
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"  # set true behind HTTPS
MAX_FAILS, FAIL_WINDOW, LOCK_SECONDS = 5, 15 * 60, 15 * 60
SETUP_TOKEN_FILE = db.DATA_DIR / "SETUP_TOKEN"

db.run("""CREATE TABLE IF NOT EXISTS admins(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT, created_at REAL, password_changed_at REAL)""")
for _stmt in ("ALTER TABLE admins ADD COLUMN display_name TEXT DEFAULT ''", "ALTER TABLE admins ADD COLUMN title TEXT DEFAULT ''", "ALTER TABLE admins ADD COLUMN role TEXT DEFAULT 'admin'", "ALTER TABLE admins ADD COLUMN must_change INTEGER DEFAULT 0", "ALTER TABLE admins ADD COLUMN last_login REAL", "ALTER TABLE admins ADD COLUMN added_by TEXT DEFAULT ''",
              "ALTER TABLE admins ADD COLUMN totp_secret TEXT DEFAULT ''", "ALTER TABLE admins ADD COLUMN totp_on INTEGER DEFAULT 0", "ALTER TABLE admins ADD COLUMN totp_last INTEGER DEFAULT 0", "ALTER TABLE admins ADD COLUMN backup_codes TEXT DEFAULT '[]'"):
    try:
        db.run(_stmt)
    except Exception:
        pass
if not db.q1("SELECT id FROM admins WHERE role='owner'") and db.q1("SELECT id FROM admins"):
    db.run("UPDATE admins SET role='owner' WHERE id=(SELECT MIN(id) FROM admins)")  # the first admin is the owner
db.run("""CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, token_hash TEXT UNIQUE, csrf TEXT, created_at REAL, last_seen REAL,
  expires_at REAL, ip TEXT, user_agent TEXT)""")
db.run("CREATE TABLE IF NOT EXISTS login_failures(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, ip TEXT, ts REAL)")
db.run("CREATE INDEX IF NOT EXISTS idx_fail ON login_failures(username, ts)")

COMMON = {"password", "password1", "password123", "123456789012", "qwertyuiop12", "letmein12345", "administrator", "welcome12345",
          "iloveyou1234", "changeme1234", "123456123456", "passwordpassword", "qwerty123456"}


# ---------- passwords ----------
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()  # Argon2id, library defaults (64 MiB memory, 3 passes)


def hash_password(pw):
    return _ph.hash(pw)


def verify_password(pw, stored):
    try:
        return _ph.verify(stored, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


_DUMMY = hash_password("not-a-real-password-just-for-timing")


def check_policy(pw, username=""):
    """Returns an error string, or None if the password is acceptable."""
    if len(pw) < 12:
        return "Use at least 12 characters."
    if len(pw) > 128:
        return "Use at most 128 characters."
    low = pw.lower()
    if low in COMMON or re.fullmatch(r"(.)\1+", pw):
        return "That password is too common."
    if username and username.lower() in low:
        return "The password must not contain your username."
    classes = sum(bool(re.search(x, pw)) for x in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if len(pw) < 16 and classes < 3:
        return "Use a mix of upper and lower case letters, numbers or symbols, or a longer passphrase (16+ characters)."
    return None


# ---------- audit ----------
def sec_log(message, ip="", ua="", **meta):
    db.log("security", message, ip=ip, ua=(ua or "")[:160], **meta)


# ---------- first-run setup ----------
def admin_count():
    return db.q1("SELECT COUNT(*) n FROM admins")["n"]


def ensure_setup_token():
    """Legacy: a server that has no workspace and no admin at all still prints a one-time token for the old setup screen (signing up works without it)."""
    if False and admin_count() == 0 and not SETUP_TOKEN_FILE.exists():
        SETUP_TOKEN_FILE.write_text(secrets.token_urlsafe(24) + "\n")
        os.chmod(SETUP_TOKEN_FILE, 0o600)
        print(f"\n*** First-run setup: read the one-time token in {SETUP_TOKEN_FILE} and use it on the sign-in page. ***\n")


def check_setup_token(token):
    if admin_count() > 0 or not SETUP_TOKEN_FILE.exists():
        return False
    return hmac.compare_digest(SETUP_TOKEN_FILE.read_text().strip().encode(), (token or "").strip().encode())


def create_admin(username, password, role=None, must_change=False, added_by="", display_name="", title=""):
    """Add a person who can sign in. role: owner, admin or member (see access.py)."""
    role = role or ("owner" if admin_count() == 0 else "admin")
    db.run("INSERT INTO admins(username,password_hash,created_at,password_changed_at,role,must_change,added_by,display_name,title) VALUES(?,?,?,?,?,?,?,?,?)",
           (username.strip(), hash_password(password), db.now(), db.now(), role, 1 if must_change else 0, added_by, display_name.strip()[:60], title.strip()[:60]))
    if SETUP_TOKEN_FILE.exists():
        SETUP_TOKEN_FILE.unlink()


# ---------- lockout ----------
def lock_remaining(username, ip=""):
    """Seconds of lockout left. Locked per (username, IP) after 5 failures, so a stranger cannot lock the real admin out;
    a higher global threshold (25 failures from many IPs in 15 min) is a backstop against distributed guessing."""
    t = db.now()
    rows = db.q("SELECT ts, ip FROM login_failures WHERE username=? AND ts>? ORDER BY ts DESC", ((username or "").lower(), t - FAIL_WINDOW))
    mine = [r for r in rows if r["ip"] == ip]
    if len(mine) >= MAX_FAILS:
        return max(0, int(mine[0]["ts"] + LOCK_SECONDS - t))
    if len(rows) >= 25:
        return max(0, int(rows[0]["ts"] + 5 * 60 - t))
    return 0


def record_failure(username, ip):
    db.run("INSERT INTO login_failures(username,ip,ts) VALUES(?,?,?)", ((username or "").lower(), ip, db.now()))
    db.run("DELETE FROM login_failures WHERE ts<?", (db.now() - 86400,))


def clear_failures(username):
    db.run("DELETE FROM login_failures WHERE username=?", ((username or "").lower(),))


# ---------- login / sessions ----------
def authenticate(username, password):
    row = db.q1("SELECT * FROM admins WHERE username=?", ((username or "").strip(),))
    ok = verify_password(password or "", row["password_hash"] if row else _DUMMY)  # equal work whether or not the user exists
    return row if (row and ok) else None


def _hash_token(t):
    return hashlib.sha256(t.encode()).hexdigest()


def start_session(admin_id, ip, ua):
    """Sign an admin in to the current workspace. -> (cookie value, csrf token). The cookie value names the workspace, so every later request is routed to it."""
    secret, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    t = db.now()
    db.run("INSERT INTO sessions(admin_id,token_hash,csrf,created_at,last_seen,expires_at,ip,user_agent) VALUES(?,?,?,?,?,?,?,?)",
           (admin_id, _hash_token(secret), csrf, t, t, t + SESSION_HOURS * 3600, ip, (ua or "")[:200]))
    db.run("DELETE FROM sessions WHERE expires_at<?", (t,))
    db.run("UPDATE admins SET last_login=? WHERE id=?", (t, admin_id))
    return f"{db.current()}.{secret}", csrf


def parse_cookie(header):
    out = {}
    for part in (header or "").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            out[k] = v
    return out


def split_token(value):
    """'<workspace>.<secret>' -> (workspace, secret). A cookie from before workspaces existed has no prefix and belongs to the main workspace."""
    from . import directory
    if value and "." in value:
        ws, secret = value.split(".", 1)
        if ws == db.MAIN or directory.ID_RE.match(ws):
            return ws, secret
        return None, None
    return (db.MAIN, value) if value else (None, None)


def session_from_cookie(cookie_header):
    """-> the signed-in session (with the workspace it belongs to under 'ws'), or None."""
    from . import directory
    ws, secret = split_token(parse_cookie(cookie_header).get(SESSION_COOKIE))
    if not ws or not directory.is_active(ws):
        return None
    with db.use(ws):
        s = db.q1("SELECT s.*, a.username, a.role, a.must_change, a.display_name, a.title FROM sessions s JOIN admins a ON a.id=s.admin_id WHERE s.token_hash=?", (_hash_token(secret),))
        t = db.now()
        if not s or s["expires_at"] < t or t - s["last_seen"] > IDLE_MINUTES * 60:
            if s:
                db.run("DELETE FROM sessions WHERE id=?", (s["id"],))
            return None
        if t - s["last_seen"] > 60:
            db.run("UPDATE sessions SET last_seen=? WHERE id=?", (t, s["id"]))
    s["ws"] = ws
    return s


def cookie_headers(token, csrf):
    sec = "; Secure" if COOKIE_SECURE else ""
    age = int(SESSION_HOURS * 3600)
    return [f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}{sec}",
            f"{CSRF_COOKIE}={csrf}; SameSite=Strict; Path=/; Max-Age={age}{sec}"]


def clearing_cookies():
    sec = "; Secure" if COOKIE_SECURE else ""
    return [f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{sec}", f"{CSRF_COOKIE}=; SameSite=Strict; Path=/; Max-Age=0{sec}"]


def revoke(session_id):
    db.run("DELETE FROM sessions WHERE id=?", (session_id,))


def revoke_others(admin_id, keep_id):
    db.run("DELETE FROM sessions WHERE admin_id=? AND id!=?", (admin_id, keep_id))


def change_password(admin_id, new_password, must_change=False):
    db.run("UPDATE admins SET password_hash=?, password_changed_at=?, must_change=? WHERE id=?", (hash_password(new_password), db.now(), 1 if must_change else 0, admin_id))


def scrub(text):
    """Remove anything that looks like an API key from error text before it is shown to a user or stored."""
    return re.sub(r"(sk-[A-Za-z0-9_\-\*]{6,}|sk_[A-Za-z0-9]{10,}|xi-api-key[^\s,;]*|Bearer\s+[A-Za-z0-9_\-\.]{12,})", "[redacted]", str(text))

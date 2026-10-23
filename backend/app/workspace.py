"""Wiping a company's data: used by Settings → Company → Delete this company and by scripts/fresh_start.py. Keeps admin accounts and sign-ins."""
import shutil
import sqlite3
import time

from . import db

TABLES = ["activity", "agent_activity", "agent_notes", "agents", "app_requests", "call_actions", "calls", "campaigns", "connectors", "contacts", "datasets", "discuss_items", "documents",
          "duties", "emails", "events", "folders", "hooks", "integrations", "kb_docs", "kb_pron", "kb_vectors", "knowledge_bases", "learned_rules", "live_questions", "meeting_msgs", "meeting_requests", "meetings",
          "outbox", "project_members", "project_updates", "projects", "shares", "skills", "training_runs", "trainings", "video_sessions", "agent_notes"]
SETTINGS = ["company_name", "company_about", "company_website", "company_doc_id", "main_link", "application_link", "timezone", "builtin_off", "admin_phone", "admin_email", "company_domain",
            "email_policy", "messaging_policy", "discuss_email", "live_question_timeout", "smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_tls", "imap_host", "imap_port",
            "default_calling_code", "calls_enabled"]


def backup():
    d = db.data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"before-delete-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src, out = sqlite3.connect(db.db_path()), sqlite3.connect(dest)
    src.backup(out)
    out.close(); src.close()
    dest.chmod(0o600)
    return dest


def wipe(make_backup=True):
    """Remove every company-specific record, file and credential. Returns {'backup': path|None, 'cleared': {table: rows}}."""
    from . import agents, meeting_bot
    path = backup() if make_backup else None
    cleared = {}
    for t in dict.fromkeys(TABLES):
        try:
            cleared[t] = db.q1(f"SELECT COUNT(*) n FROM {t}")["n"]
            db.run(f"DELETE FROM {t} WHERE kind!='security'" if t == "activity" else f"DELETE FROM {t}")  # the sign-in audit trail stays
            db.run("DELETE FROM sqlite_sequence WHERE name=?", (t,))
        except Exception:
            pass
    for k in SETTINGS:
        db.run("DELETE FROM settings WHERE key=?", (k,))
    for folder in (db.rec_dir(), db.data_dir() / "files", db.data_dir() / "recordings"):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
    agents.RUNNING.clear()
    meeting_bot._pending.clear()
    db.log("security", "The company was deleted")
    return {"backup": str(path) if path else None, "cleared": cleared}


WORK_TABLES = ["agent_activity", "agent_notes", "agents", "app_requests", "discuss_items", "documents", "duties", "emails", "events", "folders", "live_questions", "meeting_msgs",
               "meeting_requests", "meetings", "outbox", "project_members", "project_updates", "projects", "shares", "skills", "training_runs", "trainings", "video_sessions"]


def clear_work(make_backup=True):
    """Start the team over: every agent, project, to-do, email, file, meeting and calendar entry goes. Company details, connected apps, knowledge, contacts, settings and admin accounts stay."""
    from . import agents, meeting_bot
    path = backup() if make_backup else None
    cleared = {}
    for t in WORK_TABLES:
        try:
            cleared[t] = db.q1(f"SELECT COUNT(*) n FROM {t}")["n"]
            db.run(f"DELETE FROM {t}")
            db.run("DELETE FROM sqlite_sequence WHERE name=?", (t,))
        except Exception:
            pass
    agents.RUNNING.clear()
    meeting_bot._pending.clear()
    db.log("agent", "The team's work was cleared (agents, projects, to-dos, email, files)")
    return {"backup": str(path) if path else None, "cleared": {k: v for k, v in cleared.items() if v}}

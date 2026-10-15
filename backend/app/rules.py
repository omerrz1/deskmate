"""Safety rules for outgoing email: the admin writes rules like "more than 5 recipients needs approval" and they apply to every employee,
whatever the general email policy says. A rule that matches holds the email until the admin approves it, and says why."""
import json
import re
import uuid

from . import db

TYPES = {
    "recipients_over": "More than this many recipients",
    "words": "Mentions any of these words (comma separated)",
    "domain_not_in": "Goes outside these company domains (comma separated)",
    "attachments": "Has an attachment",
}


def _setting(key, default=""):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def init():
    for stmt in ("ALTER TABLE emails ADD COLUMN hold_reason TEXT DEFAULT ''",):
        try:
            db.run(stmt)
        except Exception:
            pass


def get_rules():
    return db.jload(_setting("safety_rules"), [])


def put_rules(rules):
    clean = []
    for r in rules[:30]:
        if r.get("type") not in TYPES:
            continue
        value = str(r.get("value", "")).strip()[:400]
        if r["type"] == "recipients_over" and not value.isdigit():
            raise ValueError("Recipients rule: enter a whole number.")
        if r["type"] in ("words", "domain_not_in") and not value:
            raise ValueError("Enter at least one value for that rule.")
        clean.append({"id": r.get("id") or uuid.uuid4().hex[:8], "type": r["type"], "value": value, "on": bool(r.get("on", True))})
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES('safety_rules',?)", (json.dumps(clean),))
    return clean


def check(agent, addrs, subject, body, attachments, external):
    """-> a plain-language reason this email must wait for approval, or ''."""
    if agent and agent.get("approve_outside") and external:
        return f"{agent['name']} is set to need approval for every email to outside addresses"
    text = f"{subject}\n{body}".lower()
    for r in get_rules():
        if not r.get("on"):
            continue
        v = r["value"]
        if r["type"] == "recipients_over" and len(addrs) > int(v):
            return f"Rule: more than {v} recipients ({len(addrs)})"
        if r["type"] == "attachments" and attachments:
            return "Rule: it has an attachment"
        if r["type"] == "words":
            hit = next((w.strip() for w in v.split(",") if w.strip() and re.search(r"\b" + re.escape(w.strip().lower()) + r"\b", text)), None)
            if hit:
                return f"Rule: it mentions “{hit}”"
        if r["type"] == "domain_not_in":
            allowed = {d.strip().lower().lstrip("@") for d in v.split(",") if d.strip()}
            bad = next((a for a in addrs if a.split("@")[-1].lower() not in allowed), None)
            if bad:
                return f"Rule: {bad} is outside the allowed domains"
    return ""

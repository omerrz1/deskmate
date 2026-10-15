"""Alerts and digests to the admin: by email, Slack (or Discord/Teams webhook) and Telegram.
An alert fires when something needs the admin (an email to approve, a meeting request, work that failed, a spend cap, a security event);
a digest is a daily or weekly summary. Both go to every channel that is switched on."""
import asyncio
import json
import time

import httpx

from . import costs, db, office, secretbox

KINDS = {"approval": "An email or text is waiting for your OK", "meeting": "An employee asks for a meeting", "failure": "Work failed or got stuck",
         "budget": "A spend cap was reached", "security": "Security events (sign-ins, key and setting changes)"}
SLACK_OK = ("https://hooks.slack.com/", "https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
_sent = {}  # (workspace, kind, title) -> time, so the same alert is not repeated within the hour
_LOOP = [None]


def _get(key, default=""):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def _put(key, value):
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


def config():
    """The settings for the screen. Secrets are never returned, only whether they are set."""
    kinds = db.jload(_get("notify_alerts"), list(KINDS))
    return {"email": _get("notify_email", "1") == "1", "slack_set": bool(_get("notify_slack")), "telegram_set": bool(_get("notify_telegram_token")) and bool(_get("notify_telegram_chat")),
            "telegram_chat": _get("notify_telegram_chat"), "digest": _get("notify_digest", "daily"), "digest_hour": int(_get("notify_digest_hour", "8") or 8), "digest_day": int(_get("notify_digest_day", "0") or 0),
            "alerts": kinds, "kinds": KINDS, "last_digest": float(_get("notify_last_digest", "0") or 0), "admin_email": _get("admin_email")}


def save(p):
    if "email" in p:
        _put("notify_email", "1" if p["email"] else "0")
    if p.get("slack") is not None:  # '' removes it
        v = p["slack"].strip()
        if v and not v.startswith(SLACK_OK):
            raise ValueError("Use a Slack, Discord or Teams incoming-webhook address (it starts with https://hooks.slack.com/ or https://discord.com/api/webhooks/).")
        _put("notify_slack", secretbox.seal(v) if v else "")
    if p.get("telegram_token") is not None:
        t = p["telegram_token"].strip()
        _put("notify_telegram_token", secretbox.seal(t) if t else "")
    if p.get("telegram_chat") is not None:
        _put("notify_telegram_chat", p["telegram_chat"].strip()[:40])
    if p.get("digest") in ("off", "daily", "weekly"):
        _put("notify_digest", p["digest"])
    if "digest_hour" in p:
        _put("notify_digest_hour", str(max(0, min(23, int(p["digest_hour"])))))
    if "digest_day" in p:
        _put("notify_digest_day", str(max(0, min(6, int(p["digest_day"])))))
    if "alerts" in p:
        _put("notify_alerts", json.dumps([k for k in p["alerts"] if k in KINDS]))


def _spawn(coro):
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        from . import agents
        loop = agents._LOOP
        if loop:
            asyncio.run_coroutine_threadsafe(db.carry(coro), loop)
        else:
            coro.close()


async def deliver(title, body):
    """Send one message to every channel that is on. -> {channel: 'ok' | error text}"""
    out = {}
    text = f"{title}\n\n{body}".strip()
    if _get("notify_email", "1") == "1":
        try:
            await office.email_admin(title, body)
            out["email"] = "ok"
        except Exception as e:
            out["email"] = type(e).__name__
    hook = secretbox.unseal(_get("notify_slack"))
    if hook:
        try:
            async with httpx.AsyncClient(timeout=15) as h:
                r = await h.post(hook, json={"text": text[:3500], "content": text[:1900]})  # Slack reads text, Discord reads content
            out["slack"] = "ok" if r.status_code < 300 else f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            out["slack"] = type(e).__name__
    tok, chat = secretbox.unseal(_get("notify_telegram_token")), _get("notify_telegram_chat")
    if tok and chat:
        try:
            async with httpx.AsyncClient(timeout=15) as h:
                r = await h.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text[:4000]})
            out["telegram"] = "ok" if r.status_code < 300 else f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            out["telegram"] = type(e).__name__
    return out


def send(kind, title, body=""):
    """An alert. Does nothing when that kind is switched off, and never repeats the same one within an hour."""
    if kind not in db.jload(_get("notify_alerts"), list(KINDS)):
        return
    key, now = (db.current(), kind, title), time.time()
    if now - _sent.get(key, 0) < 3600:
        return
    _sent[key] = now
    _spawn(deliver(f"Deskmates: {title}", body or KINDS.get(kind, "")))


def digest_text(days):
    since = time.time() - days * 86400
    done = db.q("SELECT d.title, g.name FROM duties d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.status='done' AND d.done_at>=? ORDER BY d.done_at DESC", (since,))
    stuck = db.q("SELECT d.title, g.name FROM duties d LEFT JOIN agents g ON g.id=d.agent_id WHERE d.status='todo' AND COALESCE(d.attempts,0)>=3")
    approvals = db.q1("SELECT COUNT(*) n FROM emails WHERE status='pending_approval'")["n"]
    unread = db.q1("SELECT COUNT(*) n FROM emails WHERE agent_id=0 AND folder='inbox' AND status='unread'")["n"]
    meetings = db.q1("SELECT COUNT(*) n FROM meeting_requests WHERE status IN ('pending','scheduled')")["n"] if db.q1("SELECT name FROM sqlite_master WHERE name='meeting_requests'") else 0
    events = db.q("SELECT title, start FROM events WHERE status='planned' AND start BETWEEN ? AND ? ORDER BY start LIMIT 5", (time.time(), time.time() + 3 * 86400))
    projects = db.q("SELECT p.name, SUM(d.status='done') done, COUNT(d.id) total FROM projects p LEFT JOIN duties d ON d.project_id=p.id WHERE p.status='active' GROUP BY p.id")
    lines = [f"Your team, {'today' if days <= 1 else f'the last {days} days'}", ""]
    lines.append(f"Finished {len(done)} task{'s' if len(done) != 1 else ''}." + (" Highlights:" if done else ""))
    lines += [f"  • {d['title']} ({d['name'] or 'team'})" for d in done[:6]]
    if stuck:
        lines += ["", f"Stuck ({len(stuck)}):"] + [f"  • {d['title']} ({d['name'] or 'team'})" for d in stuck[:4]]
    need = []
    if approvals:
        need.append(f"{approvals} email{'s' if approvals != 1 else ''} to approve")
    if unread:
        need.append(f"{unread} unread message{'s' if unread != 1 else ''}")
    if meetings:
        need.append(f"{meetings} meeting request{'s' if meetings != 1 else ''}")
    lines += ["", "Needs you: " + (", ".join(need) if need else "nothing")]
    if projects:
        lines += ["", "Projects:"] + [f"  • {p['name']}: {p['done'] or 0} of {p['total']} tasks" for p in projects[:5] if p["total"]]
    if events:
        lines += ["", "Coming up:"] + [f"  • {time.strftime('%a %H:%M', time.localtime(e['start']))} {e['title']}" for e in events]
    lines += ["", f"AI spend (estimate): ${costs.spend(since):.2f}"]
    return "\n".join(lines)


async def tick():
    """Called by the scheduler: sends the daily or weekly summary at the chosen time."""
    mode = _get("notify_digest", "daily")
    if mode == "off":
        return
    t = time.localtime()
    last = float(_get("notify_last_digest", "0") or 0)
    if time.time() - last < 20 * 3600:
        return
    if t.tm_hour < int(_get("notify_digest_hour", "8") or 8) or (mode == "weekly" and t.tm_wday != int(_get("notify_digest_day", "0") or 0)):
        return
    _put("notify_last_digest", str(time.time()))
    await deliver("Deskmates: your team's " + ("daily" if mode == "daily" else "weekly") + " summary", digest_text(1 if mode == "daily" else 7))

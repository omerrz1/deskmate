"""Who is being spoken to? Used in our own meeting room and by agents sitting in real video meetings.
One voice at a time. Rules, in order: a name picks that person only; an explicit "everyone" picks all (they answer one after another);
a request about someone's task or specialty goes to that person; a very short follow-up goes to whoever just spoke; anything general goes to the manager."""
import re
import time

from . import db, llm

# "team" on its own is NOT here: "how is the team doing" is a question for the manager, not a roll call.
ALL_WORDS = re.compile(r"\b(everyone|everybody|all of you|each of you|you all|you guys|guys|folks|both of you|all of them|one by one|roll call|go around the room|introduce yourselves)\b", re.I)
FOLLOW_WINDOW = 60  # seconds a person keeps the floor after speaking
_STOP = set("the a an and or of to for in on at is are was were be it this that how what who when where why about with from your you i we my our please can could would just any all been going doing done get got has have had do does did tell me us them they their there here now today update status progress like".split())


def _named(text, agents):
    out = []
    for a in agents:
        first = re.escape(str(a["name"]).split()[0])
        if re.search(rf"\b{first}\b", text, re.I):
            out.append(a)
    return out


def _words(s):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (s or "").lower()) if w not in _STOP}


def _profile(a):
    """What this person is responsible for: role, plus the titles of their open to-dos and projects."""
    todos = [r["title"] for r in db.q("SELECT title FROM duties WHERE agent_id=? AND status IN ('todo','doing') ORDER BY id DESC LIMIT 6", (a["id"],))]
    projects = [r["name"] for r in db.q("SELECT p.name FROM project_members m JOIN projects p ON p.id=m.project_id WHERE m.agent_id=? AND p.status='active' LIMIT 4", (a["id"],))]
    return todos, projects


def _manager(agents):
    return next((a for a in agents if a.get("is_manager")), None)


def _by_overlap(text, agents):
    """No model needed when the request clearly matches one person's role, tasks or projects."""
    tw = _words(text)
    if not tw:
        return None
    scored = []
    for a in agents:
        todos, projects = _profile(a)
        score = len(tw & _words(a.get("role"))) * 2 + len(tw & _words(" ".join(todos))) * 2 + len(tw & _words(" ".join(projects))) * 2 + len(tw & _words(a.get("bio")))
        scored.append((score, a))
    scored.sort(key=lambda x: -x[0])
    if scored[0][0] >= 2 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


async def decide(text, agents, last_agent_id=None, last_ts=None, recent=""):
    """-> (list of agents addressed, reason). Never empty when agents is not empty."""
    if not agents:
        return [], "nobody"
    if len(agents) == 1:
        return agents, "only one agent here"
    named = _named(text, agents)
    if named:
        return named, "named"
    if ALL_WORDS.search(text):
        return agents, "everyone"
    mgr = _manager(agents)
    recent_speaker = next((a for a in agents if a["id"] == last_agent_id), None) if (last_agent_id and last_ts and time.time() - last_ts < FOLLOW_WINDOW) else None
    words = len(text.split())
    owner = _by_overlap(text, agents)
    if owner:
        return [owner], "matches their task or role"
    if recent_speaker and words <= 8 and not re.search(r"\b(anyone|somebody|someone|who)\b", text, re.I):
        return [recent_speaker], "follow-up to the last speaker"
    try:
        roster = "\n".join(f"- {a['name']}{' (MANAGER)' if a.get('is_manager') else ''}: {a.get('role') or 'team member'}; open tasks: {'; '.join(_profile(a)[0][:4]) or 'none'}; projects: {', '.join(_profile(a)[1]) or 'none'}" for a in agents)
        last = f"\nThe last person to speak was {recent_speaker['name']}." if recent_speaker else ""
        res = await llm.complete_json(
            "You decide WHO ANSWERS in a meeting so that only one voice is heard at a time. Reply as JSON {\"answer\": \"Name\"}. Rules: "
            "choose exactly ONE person. If the message is about a specific piece of work, pick the person whose role, tasks or projects match it. "
            "If it continues what the last speaker was just saying, pick the last speaker. "
            "If it is general, about the team as a whole, about running things, or you cannot tell, pick the MANAGER (or the first person listed if there is no manager).",
            f"People in the meeting:\n{roster}{last}\nRecent conversation:\n{recent[-800:] or '(none)'}\nNew message from the admin: {text}", model="gpt-4.1-nano")
        name = str(res.get("answer") or "").strip().lower()
        pick = next((a for a in agents if a["name"].lower() == name or a["name"].split()[0].lower() == name.split(" ")[0]), None) if name else None
        if pick:
            return [pick], "chosen by task or role"
    except Exception:
        pass
    return ([mgr] if mgr else agents[:1]), "default: the manager" if mgr else "default: the lead"


def last_agent_message(mid):
    r = db.q1("SELECT agent_id, created_at FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id DESC LIMIT 1", (mid,))
    return (r["agent_id"], r["created_at"]) if r else (None, None)


def recent_text(mid, n=6):
    rows = db.q("SELECT role, agent_id, text FROM meeting_msgs WHERE meeting_id=? ORDER BY id DESC LIMIT ?", (mid, n))[::-1]
    names = {a["id"]: a["name"] for a in db.q("SELECT id,name FROM agents")}
    return "\n".join(f"{'Admin' if r['role'] == 'admin' else names.get(r['agent_id'], 'Agent')}: {r['text'][:200]}" for r in rows if not r["text"].startswith("["))

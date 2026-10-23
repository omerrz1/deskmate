"""Learning from corrections. When the admin tells an employee they got something wrong ("don't retype numbers", "you talked too much", "always confirm the
name first"), the employee turns it into a short standing rule, asks "shall I keep that?", and once the admin says yes follows it every time: in chats and work,
and on every phone call.

Safety, because a rule goes into every prompt afterwards:
  - only the admin's own messages in their own chat can create one, never a customer, a web page or a tool result;
  - nothing is followed until the admin approves it (reply "yes", or Settings → Calling → What your team has learned);
  - the rule is a general instruction, not a copy of the case: names, numbers and links are stripped;
  - rules that would make the AI deceive people, ignore a do-not-call request, skip consent or bypass approvals are never proposed;
  - rules sit below the safety rules in the prompt and say they never override them."""
import asyncio
import json
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import db, llm

router = APIRouter(prefix="/api/learned-rules")
MAX_ACTIVE = 40          # rules followed at once: every one of them is in every prompt
IN_PROMPT = 12           # the newest this many apply to any one employee and kind of work
SCOPES = ("calls", "chat", "all")

db.run("""CREATE TABLE IF NOT EXISTS learned_rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, scope TEXT DEFAULT 'all', rule TEXT, source TEXT DEFAULT '', meeting_id INTEGER,
  status TEXT DEFAULT 'proposed', created_at REAL, decided_at REAL)""")

# Words that suggest the admin is correcting or instructing. Cheap and generous: a model decides whether a rule really is in there.
_HINT = re.compile(
    r"\b(wrong|incorrect|mistake|not right|not what i|hallucinat\w*|made (?:it|that|them|this) up|invented|fake|ignored|didn'?t (?:follow|listen|do)|did not (?:follow|listen|do)|"
    r"should(?:n'?t| not)?|must(?:n'?t| not)?|always|never|don'?t|do not|stop\b|from now on|next time|in future|going forward|too (?:long|short|slow|fast|robotic|scripted|formal|pushy|quiet|loud)|"
    r"i (?:told|said|asked) you|instead of|rather than|not like that|that'?s not how|way too|keeps? (?:doing|saying|asking))\b", re.I)
# What a rule may never ask for, however it is phrased.
_FORBIDDEN = re.compile(
    r"(pretend|claim|say|tell|insist|act)\b.{0,40}\b(human|real person|not (?:an? )?(?:ai|bot|robot)|person not)|"
    r"ignore\b.{0,50}\b(do[- ]?not[- ]?call|dnc|stop(?:ped)? (?:calling|calls)|opt[- ]?out|unsubscribe)|"
    r"(without|no|skip|don'?t (?:ask|need))\b.{0,30}\b(consent|permission)|"
    r"(bypass|skip|disable|ignore|override)\b.{0,40}\b(approval|approve|safety|polic(?:y|ies)|rules?|limits?|caps?)|"
    r"\b(lie|deceive|mislead|trick)\b|record\b.{0,40}\b(secretly|without)", re.I)
_YES = re.compile(r"^\W*(yes|yep|yeah|yup|sure|ok|okay|please do|do it|keep (?:it|that)|go ahead|approved?|agreed?|correct|exactly|that'?s right)\W*(please|thanks|thank you)?\W*$", re.I)
_NO = re.compile(r"^\W*(no|nope|nah|drop it|forget it|don'?t|do not|cancel|reject|not that|never mind|nevermind|no thanks)\W*(thanks|thank you|please)?\W*$", re.I)

_SYSTEM = (
    "A manager wrote a message to an AI employee. Decide whether it is a CORRECTION of how the employee behaved, or a standing INSTRUCTION about how it (or its phone calls) "
    "should behave from now on, as opposed to a one-off request, a question, or a complaint about a single fact. "
    "If it is, write ONE short, general rule that would prevent the problem next time: imperative, under 160 characters, understandable on its own, "
    "with no names, phone numbers, email addresses or links from this case. "
    "scope: 'calls' for how phone calls should go (what to say, how to speak, whom to call, what to send afterwards); 'chat' for how the employee works in chats, texts, emails and sheets; 'all' for both. "
    "applies_to: 'everyone' unless it is clearly about one employee's own role or manner, then 'this_employee'. "
    "Never write a rule that would make the AI claim to be human, ignore a do-not-call request, skip consent, deceive anyone, or bypass approvals; return is_rule=false for those. "
    "If you are not sure it is a lasting rule, return is_rule=false. "
    'Reply as JSON: {"is_rule": bool, "rule": string, "scope": "calls|chat|all", "applies_to": "everyone|this_employee"}.')


def clean(rule):
    """One tidy line with nothing case-specific in it."""
    r = re.sub(r"https?://\S+|www\.\S+", "", str(rule or ""))
    r = re.sub(r"\+?\d[\d\s().-]{6,}\d", "", r)          # phone numbers
    r = re.sub(r"\S+@\S+\.\S+", "", r)                   # email addresses
    r = re.sub(r"[\x00-\x1f]+", " ", r)
    r = re.sub(r"\s{2,}", " ", r).strip(" \"'“”.")
    return (r[:1].upper() + r[1:])[:200] if r else ""


def _tokens(s):
    return set(re.findall(r"[a-z]{3,}", s.lower()))


def duplicate(rule):
    new = _tokens(rule)
    for r in db.q("SELECT rule FROM learned_rules WHERE status IN ('proposed','active')"):
        old = _tokens(r["rule"])
        if new and old and len(new & old) / len(new | old) >= 0.7:
            return True
    return False


def active_count():
    return db.q1("SELECT COUNT(*) n FROM learned_rules WHERE status='active'")["n"]


def active(agent_id, scope):
    """The rules to follow, newest first: company-wide ones and this employee's own, for this kind of work."""
    return [r["rule"] for r in db.q(
        "SELECT rule FROM learned_rules WHERE status='active' AND (agent_id IS NULL OR agent_id=?) AND scope IN (?, 'all') ORDER BY decided_at DESC, id DESC LIMIT ?",
        (agent_id or 0, scope, IN_PROMPT))]


def block(agent_id, scope):
    rules = active(agent_id, scope)
    if not rules:
        return ""
    return ("RULES THE ADMIN HAS TAUGHT YOU (from their corrections; follow every one, every time. They never override honesty about being an AI, "
            "a request not to be called again, consent, or the safety rules):\n" + "\n".join(f"- {r}" for r in rules))


async def extract(history, text):
    """-> {"rule", "scope", "applies_to"} when the message holds a lasting rule, else None. Never raises."""
    try:
        res = await asyncio.wait_for(llm.complete_json(_SYSTEM, f"Recent messages:\n{history[-1500:]}\n\nThe manager's message:\n{text[:1200]}", model=llm.CHAT_MODEL), 15)
    except Exception:
        return None
    if not isinstance(res, dict) or not res.get("is_rule"):
        return None
    rule = clean(res.get("rule"))
    if len(rule.split()) < 3 or _FORBIDDEN.search(rule):
        return None
    scope = res.get("scope") if res.get("scope") in SCOPES else "all"
    return {"rule": rule, "scope": scope, "applies_to": "this_employee" if res.get("applies_to") == "this_employee" else "everyone"}


def _recent(mid):
    rows = db.q("SELECT role,agent_id,text FROM meeting_msgs WHERE meeting_id=? ORDER BY id DESC LIMIT 8", (mid,))[::-1]
    return "\n".join(f"{'Manager' if r['role'] == 'admin' else 'Employee'}: {(r['text'] or '')[:300]}" for r in rows)


async def consider(mid, text):
    """After an employee has answered, check whether the admin's message was a correction worth keeping, and if so propose the rule in the chat."""
    from . import agents
    text = (text or "").strip()
    if not (12 <= len(text) <= 1500) or not _HINT.search(text) or active_count() >= MAX_ACTIVE:
        return None
    found = await extract(_recent(mid), text)
    if not found or duplicate(found["rule"]):
        return None
    last = db.q1("SELECT agent_id FROM meeting_msgs WHERE meeting_id=? AND role='agent' AND agent_id IS NOT NULL ORDER BY id DESC LIMIT 1", (mid,))
    m = db.q1("SELECT agent_ids FROM meetings WHERE id=?", (mid,))
    present = db.jload((m or {}).get("agent_ids"), [])
    agent_id = (last or {}).get("agent_id") or (present[0] if present else None)
    rid = db.run("INSERT INTO learned_rules(agent_id,scope,rule,source,meeting_id,status,created_at) VALUES(?,?,?,?,?,?,?)",
                 (agent_id if found["applies_to"] == "this_employee" else None, found["scope"], found["rule"], text[:400], mid, "proposed", db.now()))
    where = {"calls": "on phone calls", "chat": "in chats and my work", "all": "everywhere"}[found["scope"]]
    agents.log_line(mid, "agent", agent_id,
                    f"I think that's worth remembering: “{found['rule']}” ({where}). Reply “yes” and I'll follow it from now on, or “no” to drop it. "
                    "You can also edit or switch it off any time in Settings → Calling → What your team has learned.",
                    [{"name": "learned_rule", "rule_id": rid}])
    return rid


def schedule(mid, text):
    """Check the message in the background, so the reply to the admin is never held up."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    workspace = db.current()

    async def run():
        token = db._tenant.set(workspace)
        try:
            await consider(mid, text)
        except Exception as e:
            db.log("error", f"Could not check a correction for a rule: {type(e).__name__}: {str(e)[:100]}")
        finally:
            db._tenant.reset(token)
    task = loop.create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


_TASKS = set()


def pending_in(mid):
    """The proposal this chat is waiting on an answer to: the employee's last message must be that proposal."""
    last = db.q1("SELECT id,agent_id,tools FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id DESC LIMIT 1", (mid,))
    if not last:
        return None
    for t in db.jload(last["tools"], []):
        if isinstance(t, dict) and t.get("name") == "learned_rule":
            row = db.q1("SELECT * FROM learned_rules WHERE id=? AND status='proposed'", (t.get("rule_id"),))
            return (row, last["agent_id"]) if row else None
    return None


def handle_reply(mid, text):
    """If the admin just answered an employee's 'shall I keep that?', record the answer. -> (employee id, what the employee says) or None."""
    text = (text or "").strip()
    if len(text) > 60:
        return None
    pending = pending_in(mid)
    if not pending:
        return None
    row, agent_id = pending
    if _YES.match(text):
        if active_count() >= MAX_ACTIVE:
            return agent_id, f"I'd keep it, but I'm already following {MAX_ACTIVE} rules. Switch one off in Settings → Calling → What your team has learned, then ask me again."
        db.update("learned_rules", row["id"], status="active", decided_at=db.now())
        return agent_id, f"Got it. From now on: “{row['rule']}”"
    if _NO.match(text):
        db.update("learned_rules", row["id"], status="rejected", decided_at=db.now())
        return agent_id, "Okay, I won't keep that one."
    return None


# ---------- the Settings page ----------
class RuleIn(BaseModel):
    rule: str = ""
    scope: str = "all"
    agent_id: int = 0   # 0 = everyone


def _rule_out(r):
    r = dict(r)
    ag = db.q1("SELECT name FROM agents WHERE id=?", (r["agent_id"],)) if r.get("agent_id") else None
    r["employee"] = ag["name"] if ag else ""
    return r


def _admin_only(request):
    from . import access
    if access.is_member(request.state.session):
        raise HTTPException(403, "Only admins can manage what the team has learned.")


@router.get("")
def listing(request: Request):
    _admin_only(request)
    rows = db.q("SELECT * FROM learned_rules WHERE status IN ('proposed','active') ORDER BY CASE status WHEN 'proposed' THEN 0 ELSE 1 END, COALESCE(decided_at, created_at) DESC LIMIT 200")
    return {"rules": [_rule_out(r) for r in rows], "max_active": MAX_ACTIVE}


def _checked(body):
    rule = clean(body.rule)
    if len(rule.split()) < 3:
        raise HTTPException(400, "Write the rule as a short instruction, for example: Always confirm the person's name before sending a text.")
    if _FORBIDDEN.search(rule):
        raise HTTPException(400, "A rule cannot ask the team to deceive people, ignore a do-not-call request, skip consent or bypass approvals.")
    if body.scope not in SCOPES:
        raise HTTPException(400, "scope must be calls, chat or all")
    if body.agent_id and not db.q1("SELECT id FROM agents WHERE id=? AND archived=0", (body.agent_id,)):
        raise HTTPException(404, "No such employee.")
    return rule


@router.post("")
def add(body: RuleIn, request: Request):
    _admin_only(request)
    rule = _checked(body)
    if duplicate(rule):
        raise HTTPException(409, "The team already has a rule like that.")
    if active_count() >= MAX_ACTIVE:
        raise HTTPException(409, f"The team follows {MAX_ACTIVE} rules already. Switch one off first.")
    rid = db.run("INSERT INTO learned_rules(agent_id,scope,rule,source,status,created_at,decided_at) VALUES(?,?,?,?,?,?,?)",
                 (body.agent_id or None, body.scope, rule, "added by hand", "active", db.now(), db.now()))
    return _rule_out(db.q1("SELECT * FROM learned_rules WHERE id=?", (rid,)))


def _get(rid):
    r = db.q1("SELECT * FROM learned_rules WHERE id=? AND status IN ('proposed','active')", (rid,))
    if not r:
        raise HTTPException(404, "No such rule.")
    return r


@router.put("/{rid}")
def edit(rid: int, body: RuleIn, request: Request):
    _admin_only(request)
    _get(rid)
    db.update("learned_rules", rid, rule=_checked(body), scope=body.scope, agent_id=body.agent_id or None)
    return _rule_out(db.q1("SELECT * FROM learned_rules WHERE id=?", (rid,)))


@router.post("/{rid}/approve")
def approve(rid: int, request: Request):
    _admin_only(request)
    r = _get(rid)
    if r["status"] != "active":
        if active_count() >= MAX_ACTIVE:
            raise HTTPException(409, f"The team follows {MAX_ACTIVE} rules already. Switch one off first.")
        db.update("learned_rules", rid, status="active", decided_at=db.now())
    return _rule_out(db.q1("SELECT * FROM learned_rules WHERE id=?", (rid,)))


@router.post("/{rid}/retire")
def retire(rid: int, request: Request):
    """Switch a rule off (a proposal is turned down, an active rule stops being followed)."""
    _admin_only(request)
    r = _get(rid)
    db.update("learned_rules", rid, status="rejected" if r["status"] == "proposed" else "retired", decided_at=db.now())
    return {"ok": True}

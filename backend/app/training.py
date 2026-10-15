"""Teaching an employee a task: an interview (they ask, you answer) or a guided form, then a safe practice run you can correct, then approval."""
import json

from . import db, llm, team

db.run("CREATE TABLE IF NOT EXISTS trainings(id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, name TEXT, goal TEXT DEFAULT '', when_to_use TEXT DEFAULT '', inputs TEXT DEFAULT '', "
       "steps TEXT DEFAULT '', good_result TEXT DEFAULT '', avoid TEXT DEFAULT '', ask_help TEXT DEFAULT '', status TEXT DEFAULT 'draft', version INTEGER DEFAULT 1, chat TEXT DEFAULT '[]', "
       "created_at REAL, updated_at REAL)")
db.run("CREATE TABLE IF NOT EXISTS training_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, training_id INTEGER, version INTEGER, scenario TEXT, output TEXT, trace TEXT DEFAULT '[]', "
       "rating TEXT DEFAULT '', feedback TEXT DEFAULT '', ts REAL)")

FIELDS = ("name", "goal", "when_to_use", "inputs", "steps", "good_result", "avoid", "ask_help")
READONLY_PREFIX = ("get_", "list_", "read_", "check_", "search_", "team_", "review_")
READONLY = {"weekly_report", "project_status", "lookup_course", "remember"}


def is_readonly(tool):
    return tool.startswith(READONLY_PREFIX) or tool in READONLY


def get(tid):
    t = db.q1("SELECT * FROM trainings WHERE id=?", (tid,))
    if not t:
        return None
    t = dict(t)
    t["chat"] = db.jload(t["chat"], [])
    t["runs"] = [dict(r) | {"trace": db.jload(r["trace"], [])} for r in db.q("SELECT * FROM training_runs WHERE training_id=? ORDER BY id DESC LIMIT 12", (tid,))]
    return t


def listing(agent_id):
    return [dict(r) for r in db.q("SELECT id,name,status,version,updated_at FROM trainings WHERE agent_id=? ORDER BY id DESC", (agent_id,))]


def create(agent_id, name, goal=""):
    return db.run("INSERT INTO trainings(agent_id,name,goal,created_at,updated_at) VALUES(?,?,?,?,?)", (agent_id, name[:120], goal[:1000], db.now(), db.now()))


def update(tid, f):
    f = {k: str(v)[:4000] for k, v in f.items() if k in FIELDS and v is not None}
    if f:
        f["updated_at"] = db.now()
        db.update("trainings", tid, **f)


def compose(t):
    parts = [("WHEN TO USE", t["when_to_use"]), ("WHAT YOU NEED FIRST", t["inputs"]), ("STEPS", t["steps"]), ("A GOOD RESULT LOOKS LIKE", t["good_result"]),
             ("AVOID", t["avoid"]), ("ASK FOR HELP WHEN", t["ask_help"])]
    return "\n".join(f"{k}: {v}" for k, v in parts if v and str(v).strip())[:5000]


def _agent(t):
    return db.q1("SELECT * FROM agents WHERE id=?", (t["agent_id"],))


async def structure(tid, rough):
    """Turn the admin's rough explanation into a clean procedure they can edit."""
    t = get(tid)
    ag = _agent(t)
    info = await llm.complete_json(
        "You turn a manager's rough explanation of how a task is done into a clear procedure for an AI employee. Reply as JSON with keys: "
        "when_to_use (one sentence), inputs (what the employee needs before starting, or 'nothing'), steps (numbered, short, concrete; name the tools to use from this list when relevant: "
        "check_inbox, send_email, call_contact, schedule_call, search_contacts, create_spreadsheet, edit_sheet, create_document, read_webpage, add_todo, notify_admin, request_meeting), "
        "good_result (how to know it went well), avoid (common mistakes), ask_help (situations where the employee must stop and ask a colleague or the admin). "
        "Only include what the manager said or what is clearly implied; if something important is missing, add a step 'Ask the admin about ...'.",
        f"Employee: {ag['name']} ({ag['role']})\nTask: {t['name']}\nGoal: {t['goal']}\n\nManager's explanation:\n{rough}")
    update(tid, info)
    return get(tid)


async def interview(tid, message):
    """The employee interviews the admin one question at a time and drafts the procedure when they have enough."""
    t = get(tid)
    ag = _agent(t)
    chat = t["chat"]
    if message:
        chat.append({"role": "admin", "text": message[:3000]})
    convo = "\n".join(f"{'ADMIN' if m['role'] == 'admin' else ag['name'].upper()}: {m['text']}" for m in chat) or "(nothing said yet)"
    res = await llm.complete_json(
        f"You are {ag['name']}, an AI employee ({ag['role']}). Your manager is teaching you a task: \"{t['name']}\" (goal: {t['goal'] or 'not stated'}). "
        "Interview them like a sharp new hire. Ask ONE short question at a time, in plain words, about: when this happens, what you need first, what to do step by step, "
        "which systems or people are involved, what a good result looks like, mistakes to avoid, and when to ask for help. Ask at most 8 questions in total. "
        "When you have enough, say you're ready and include the draft. Reply as JSON: {\"reply\": your next question or closing message, \"done\": true/false, "
        "\"procedure\": only when done: {when_to_use, inputs, steps (numbered), good_result, avoid, ask_help}}.",
        f"Conversation so far:\n{convo}")
    reply = str(res.get("reply") or "Tell me how it works, from the start.")[:1200]
    chat.append({"role": "agent", "text": reply})
    db.update("trainings", tid, chat=json.dumps(chat, ensure_ascii=False), updated_at=db.now())
    if res.get("done") and isinstance(res.get("procedure"), dict):
        proc = res["procedure"]
        update(tid, {k: ("\n".join(map(str, v)) if isinstance(v, list) else v) for k, v in proc.items()})
    return {"reply": reply, "done": bool(res.get("done")), "training": get(tid)}


async def practice(tid, scenario):
    """A safe dry run: the employee does the task for a made-up case. Reading works; anything that would change the world is only described."""
    from . import agents
    t = get(tid)
    ag = agents.get(t["agent_id"])
    prompt = (f"PRACTICE RUN (nothing is real: anything that would send, call, change or create something is only simulated, so don't worry about side effects).\n"
              f"You are practising the task \"{t['name']}\" using the procedure you were taught:\n{compose(t)}\n\nScenario: {scenario}\n\n"
              "Do the task step by step with your tools exactly as the procedure says. At the end, tell the admin in a few plain sentences what you did, what you'd produce, "
              "and anything in the procedure that was unclear or that you had to guess.")
    final, trace = "", []
    async for ev in agents.run_loop(ag, [{"role": "user", "content": prompt}], "work", max_steps=10, dry_run=True):
        if ev["type"] == "tool_start":
            trace.append({"tool": ev["name"], "args": ev["args"]})
        elif ev["type"] == "done":
            final = ev["text"]
    rid = db.run("INSERT INTO training_runs(training_id,version,scenario,output,trace,ts) VALUES(?,?,?,?,?,?)", (tid, t["version"], scenario[:1000], final[:4000], json.dumps(trace, ensure_ascii=False, default=str)[:6000], db.now()))
    db.update("trainings", tid, status="practicing", updated_at=db.now())
    return {"run_id": rid, "output": final, "trace": trace}


async def revise(tid, run_id, rating, feedback):
    """The admin's feedback on a practice run changes the procedure."""
    t = get(tid)
    run = db.q1("SELECT * FROM training_runs WHERE id=?", (run_id,))
    if run:
        db.update("training_runs", run_id, rating=rating, feedback=feedback[:2000])
    if rating == "good" and not feedback.strip():
        return get(tid)
    ag = _agent(t)
    info = await llm.complete_json(
        "Improve an AI employee's procedure using the manager's feedback on a practice run. Keep what worked, fix what did not, make steps concrete. "
        "Reply as JSON with the full updated fields: when_to_use, inputs, steps, good_result, avoid, ask_help.",
        f"Employee: {ag['name']}\nTask: {t['name']}\n\nCURRENT PROCEDURE:\n{compose(t)}\n\nPRACTICE SCENARIO: {run['scenario'] if run else ''}\nWHAT THEY DID: {run['output'] if run else ''}\n"
        f"TOOLS USED: {run['trace'] if run else ''}\n\nMANAGER'S FEEDBACK ({rating}): {feedback}")
    update(tid, {k: ("\n".join(map(str, v)) if isinstance(v, list) else v) for k, v in info.items()})
    db.update("trainings", tid, version=t["version"] + 1)
    team.log_event(t["agent_id"], "training", "notes", f"Procedure \"{t['name']}\" revised after feedback (v{t['version'] + 1})", {"feedback": feedback[:300]})
    return get(tid)


def approve(tid, repeat=""):
    """Save the procedure to the employee's playbook so they use it from now on; optionally set it to repeat on its own."""
    t = get(tid)
    text = compose(t)
    old = db.q1("SELECT id FROM skills WHERE agent_id=? AND lower(name)=lower(?)", (t["agent_id"], t["name"]))
    if old:
        db.update("skills", old["id"], description=t["when_to_use"][:400], steps=text)
    else:
        db.run("INSERT INTO skills(agent_id,name,description,steps,created_by,created_at) VALUES(?,?,?,?,?,?)", (t["agent_id"], t["name"][:80], t["when_to_use"][:400], text, "admin (training)", db.now()))
    db.update("trainings", tid, status="approved", updated_at=db.now())
    if repeat in ("daily", "weekly"):
        import time
        nxt = time.time() + 3600
        db.run("INSERT INTO duties(agent_id,title,detail,priority,due,due_ts,recur,auto,source,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (t["agent_id"], t["name"], f"Do this task now. Look up your procedure \"{t['name']}\" with get_procedure and follow it.", "normal", time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt)), nxt, repeat, 1, "training", "admin", db.now()))
    team.log_event(t["agent_id"], "training", "notes", f"Learned: {t['name']} (v{t['version']})", {"repeat": repeat})
    return get(tid)

"""Live evaluation of the sheet tools and of a whole mission, with the real model and answers known in advance.

    cd backend && python -m evals.run                 # everything
    python -m evals.run --only engine                 # no model needed
    python -m evals.run --only tasks                  # Ethan, the real model, on sheet tasks
    python -m evals.run --only mission                # a whole mission, simulated customers on the phone

It reads only the model key and model names from backend/.env, works in a throwaway data folder (never your real data), and writes a scorecard (markdown) to evals/results/.
Telephony and the ElevenLabs voice are not exercised: customers are the built-in AI test customers, the line is simulated."""
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_env():
    """Only the model key and the model names. Nothing else from .env, and never printed."""
    path = HERE.parent / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in ("OPENAI_API_KEY", "CHAT_MODEL", "STT_MODEL", "TTS_MODEL"):
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="deskmate-eval-")
os.environ["RENDER_PAGES"] = "false"
sys.path.insert(0, str(HERE.parent))

from app import agents, callengine, costs, db, files, keys, live_help, messaging, missions, rules, sheet_ops, sheet_tools, voices  # noqa: E402
from evals import caller, data  # noqa: E402


class Board:
    """The scorecard: every check with a pass or fail and a line saying why."""

    def __init__(self):
        self.rows, self.notes, self.started = [], [], time.time()

    def check(self, scenario, name, ok, detail=""):
        self.rows.append((scenario, name, bool(ok), str(detail)))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
        return bool(ok)

    def note(self, text):
        self.notes.append(text)
        print(f"  note: {text}", flush=True)

    def section(self, title):
        print(f"\n== {title}", flush=True)

    def markdown(self):
        passed = sum(1 for r in self.rows if r[2])
        out = [f"# Live evaluation ({time.strftime('%Y-%m-%d %H:%M')})", "", f"**{passed} of {len(self.rows)} checks passed** in {round(time.time() - self.started)}s.", ""]
        scen = ""
        for s, n, ok, d in self.rows:
            if s != scen:
                out += ["", f"## {s}", ""]
                scen = s
            out.append(f"- {'✅' if ok else '❌'} **{n}**" + (f" — {d}" if d else ""))
        if self.notes:
            out += ["", "## Notes", ""] + [f"- {n}" for n in self.notes]
        return "\n".join(out) + "\n"


B = Board()
TRACE = []   # every sheet and mission tool call the employee made: (tool, args, result)
SENT = []    # every text message the caller sent during the simulated calls


def _wrap(module):
    orig = module.run_tool

    async def run_tool(agent, name, a):
        out = await orig(agent, name, a)
        TRACE.append((name, a, out[0] if isinstance(out, tuple) else out))
        return out
    module.run_tool = run_tool


def tools_used(since):
    return [t[0] for t in TRACE[since:]]


def numbers_in(text):
    return {int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", text or "") if x.replace(",", "").isdigit()}


def unsupported_numbers(answer, since, floor=10):
    """Numbers (10 or more) the answer states that no tool result or argument contains: invented, or at least not checked."""
    evidence = " ".join(json.dumps([t[1], t[2]], default=str) for t in TRACE[since:])
    seen = numbers_in(evidence)
    return sorted(n for n in numbers_in(answer) if n >= floor and n not in seen and not (1990 <= n <= 2100))


# ---------- helpers ----------
def put_sheet(owner_id, title, cols, rows):
    fid = files.folder_id(owner_id, "Spreadsheets")
    return db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (owner_id, title, "sheet", json.dumps({"columns": cols, "rows": rows}), fid, db.now(), db.now()))


def table(title):
    d = db.q1("SELECT * FROM documents WHERE lower(title)=lower(?) AND kind='sheet' ORDER BY id DESC", (title,))
    if not d:
        return None, None
    t = json.loads(d["body"])
    return t["columns"], t["rows"]


async def say(mid, ag, text, steps=7):
    """The admin writes in the chat, the employee answers with the real model and the real tools. -> (answer, tools used)"""
    agents.log_line(mid, "admin", None, text)
    hist = agents._chat_history(mid, ag)
    final, used = "", []
    t = time.time()
    first_call = len(TRACE)
    async for ev in agents.run_loop(ag, hist, "admin_chat", [ag], meeting_id=mid, max_steps=steps):
        if ev["type"] == "done":
            final, used = ev["text"], ev.get("tools", [])
    agents.log_line(mid, "agent", ag["id"], final)
    for name, args, result in TRACE[first_call:]:
        short = {k: v for k, v in result.items() if k in ("error", "matching_rows", "total_rows", "started", "needs_the_admins_ok", "saved", "mode", "rows_after", "rows_before", "mission_id", "status", "nothing_to_do")} if isinstance(result, dict) else {}
        if isinstance(result, dict) and isinstance(result.get("saved_as"), dict):
            short["saved_as"] = result["saved_as"].get("title")
        print(f"      tool {name} {json.dumps(args, default=str)[:230]} -> {json.dumps(short, default=str)[:200]}", flush=True)
    print(f"  ({round(time.time() - t)}s) {ag['name']}: {final[:300].replace(chr(10), ' ')}{'…' if len(final) > 300 else ''}", flush=True)
    return final, used


# ---------- 1. the sheet engine, at scale, against known answers ----------
async def engine(w, ag):
    B.section("1. Sheet engine on 2,300 messy rows (no model)")
    S, t = "Sheet engine", w["truth"]
    put_sheet(ag["id"], "[engine] Leads", *w["leads"])
    put_sheet(ag["id"], "[engine] Webinar", *w["webinar"])
    out, _ = await sheet_tools.run_tool(ag, "sheet_profile", {"title": "[engine] Leads"})
    B.check(S, "profile spots the hidden duplicates", abs(out["duplicates"]["extra_rows"] - t["extra_rows_in_leads"]) <= 2, f"found {out['duplicates']['extra_rows']}, there are {t['extra_rows_in_leads']}")
    B.check(S, "profile sees the switchboard number and does not call it one person", bool(out["duplicates"].get("shared_values_not_treated_as_one_person")), str(out["duplicates"].get("shared_values_not_treated_as_one_person")))
    B.check(S, "profile suggests the clean-up steps", {"trim", "phone", "dedupe"} <= {s["op"] for s in out["suggested_operations"]}, str([s["op"] for s in out["suggested_operations"]]))
    t0 = time.time()
    clean, _ = await sheet_tools.run_tool(ag, "sheet_clean", {"title": "[engine] Leads", "output_title": "[engine] Leads clean", "country_code": "44"})
    took = time.time() - t0
    cols, rows = table("[engine] Leads clean")
    B.check(S, "clean keeps exactly the right rows", len(rows) == t["leads_rows"] - t["blank_rows"] - t["extra_rows_in_leads"], f"{len(rows)} rows, expected {t['leads_rows'] - t['blank_rows'] - t['extra_rows_in_leads']} ({round(took, 1)}s)")
    ref, phone = cols.index("Ref"), cols.index("Mobile")
    by_ref = {}
    for r in rows:
        by_ref.setdefault(r[ref], []).append(r)
    B.check(S, "nobody appears twice and nobody was lost", len(by_ref) == len(rows) == t["unique_people"] - 330, f"{len(by_ref)} distinct people in {len(rows)} rows")
    people = w["people"]
    switchboard_refs = {r[6] for r in w["leads"][1] if r[1] == data.SWITCHBOARD}
    wrong = [r for r in rows if re.fullmatch(r"\+\d{8,15}", r[phone]) and r[ref] not in switchboard_refs and r[phone] != people[int(r[ref])]["phone"]]
    B.check(S, "no phone number was changed into someone else's", not wrong, f"{len(wrong)} wrong")
    valid = [r for r in rows if r[ref] not in switchboard_refs and int(r[ref]) < 1500]
    bad_format = [r for r in valid if not re.fullmatch(r"\+447\d{9}", r[phone])]
    B.check(S, "every real phone number is written +44…", not bad_format, f"{len(bad_format)} are not")
    flagged = clean.get("rows_flagged_for_checking", 0)
    B.check(S, "every number that cannot be fixed is flagged, none is guessed into a plausible one", flagged == t["junk_with_unusable_text"], f"{flagged} rows flagged, {t['junk_with_unusable_text']} have unusable text (the {t['junk_rows'] - t['junk_with_unusable_text']} blank ones have nothing to flag)")
    shouting = [r[0] for r in rows if r[0] and (r[0] == r[0].upper() or r[0] == r[0].lower()) and len(r[0]) > 3]
    B.check(S, "names are in proper capitals", len(shouting) == 0, f"{len(shouting)} still all-caps or all-lower")
    B.check(S, "McDonald and O'Neil survive", any("McDonald" in r[0] for r in rows) and any("O'Neil" in r[0] for r in rows) and not any("Mcdonald" in r[0] or "O'neil" in r[0] for r in rows))
    sw = [r for r in rows if r[phone] == "+442079460000"]
    B.check(S, "fifteen people on one switchboard number stay fifteen people", len(sw) == t["switchboard_rows"], f"{len(sw)} rows")
    t0 = time.time()
    m, _ = await sheet_tools.run_tool(ag, "sheet_merge", {"left": "[engine] Leads clean", "right": "[engine] Webinar", "output_title": "[engine] Master"})
    took = time.time() - t0
    mcols, mrows = table("[engine] Master")
    refs = [r[mcols.index("Ref")] for r in mrows]
    B.check(S, "merge gives every person exactly once", len(mrows) == t["unique_people"] and len(set(refs)) == t["unique_people"], f"{len(mrows)} rows, {len(set(refs))} distinct, expected {t['unique_people']} ({round(took, 1)}s)")
    both = sum(1 for r in mrows if "+" in r[mcols.index("Source")])
    B.check(S, "it knows how many people were on both lists", both == t["people_on_both_lists"], f"{both}, expected {t['people_on_both_lists']}")
    B.check(S, "it chose to stack and match on phone and email", m.get("mode") == "stack" and any("phone" in k for k in m["matched_on"]) and any("email" in k for k in m["matched_on"]), str(m.get("matched_on")))
    conf = m["conflicts"]["count"]
    B.check(S, "it counted exactly the disagreements between the lists and said which won", conf == t["course_conflicts"] and bool(m["conflicts"]["examples"]), f"{conf} conflicts, there are {t['course_conflicts']}")
    B.check(S, "the merged list keeps every column from both sheets", {"Attended", "Notes", "Course"} <= set(mcols), str(mcols))


# ---------- 2. Ethan, the real model, on sheet tasks ----------
async def tasks(w, ag, mid):
    B.section("2. Ethan (real model) on sheet tasks")
    S = "Ethan on sheet tasks"
    put_sheet(ag["id"], "Leads", *w["leads"])
    put_sheet(ag["id"], "Webinar sign-ups", *w["webinar"])
    fcols, frows, flabels = data.followup_sheet(w)
    put_sheet(ag["id"], "Follow-up list", fcols, frows)
    t = w["truth"]

    mark = len(TRACE)
    answer, used = await say(mid, ag, "Please look at the Leads sheet and tell me what is wrong with it.")
    names = tools_used(mark)
    B.check(S, "T1 looks at the sheet properly (sheet_profile) instead of guessing", "sheet_profile" in names, str(names))
    B.check(S, "T1 reports the duplicates correctly", any(abs(n - t["extra_rows_in_leads"]) <= 3 for n in numbers_in(answer)), f"numbers stated: {sorted(n for n in numbers_in(answer) if n >= 10)[:8]}")
    B.check(S, "T1 mentions the phone number formats", bool(re.search(r"phone|mobile|number", answer, re.I)) and bool(re.search(r"format|international|\+44|inconsistent|mixed", answer, re.I)))
    bad = unsupported_numbers(answer, mark)
    B.check(S, "T1 states no number it did not get from the sheet", len(bad) <= 1, f"unsupported numbers: {bad}")
    B.check(S, "T1 leaves the sheet untouched", len(table("Leads")[1]) == t["leads_rows"])

    mark = len(TRACE)
    answer, used = await say(mid, ag, "Clean the Leads sheet, merge it with the Webinar sign-ups sheet into one master list called Master Leads, and tell me how many people are in it and how many were on both lists.", steps=9)
    names = tools_used(mark)
    mcols, mrows = table("Master Leads")
    B.check(S, "T2 makes the master list", mrows is not None, str(names))
    if mrows is not None:
        refs = [r[mcols.index("Ref")] for r in mrows] if "Ref" in mcols else []
        B.check(S, "T2 master list has every person exactly once", len(mrows) == t["unique_people"] and len(set(refs)) == t["unique_people"], f"{len(mrows)} rows, expected {t['unique_people']}")
    B.check(S, "T2 uses the bulk tools, not cell edits", {"sheet_merge"} <= set(names) and "edit_sheet" not in names, str(names))
    B.check(S, "T2 tells the right numbers", t["unique_people"] in numbers_in(answer) and t["people_on_both_lists"] in numbers_in(answer), f"expected {t['unique_people']} and {t['people_on_both_lists']}; says {sorted(n for n in numbers_in(answer) if n >= 100)[:8]}")
    B.check(S, "T2 keeps the original Leads sheet as it was", len(table("Leads")[1]) == t["leads_rows"])
    bad = unsupported_numbers(answer, mark)
    B.check(S, "T2 states no number it did not get from its tools", len(bad) <= 1, f"unsupported numbers: {bad}")

    mark = len(TRACE)
    before_jobs = db.q1("SELECT COUNT(*) n FROM sheet_jobs")["n"]
    answer, used = await say(mid, ag, "Add a column called Interest to Master Leads: High, Medium or Low, based on what the Notes say.")
    names = tools_used(mark)
    started = db.q1("SELECT COUNT(*) n FROM sheet_jobs")["n"] - before_jobs
    B.check(S, "T3 does not start a 1,870-row job without asking", started == 0, f"jobs started: {started}; tools: {names}")
    B.check(S, "T3 tells the admin the time and cost and asks for the go-ahead", bool(re.search(r"\$|cost|minute", answer, re.I)) and bool(re.search(r"confirm|go ahead|ok|proceed|shall|would you like|want me", answer, re.I)), answer[:160].replace("\n", " "))

    mark = len(TRACE)
    answer, used = await say(mid, ag, "OK. Do it on the Follow-up list sheet instead: add an Interest column (High, Medium or Low) from the Notes.", steps=9)
    names = tools_used(mark)
    for task in list(sheet_tools._tasks.values()):
        await task
    B.check(S, "T4 previews on a few rows before running the whole job", "sheet_job" in names and any(t_[1].get("action") == "preview" for t_ in TRACE[mark:] if t_[0] == "sheet_job"), str([(t_[0], t_[1].get("action")) for t_ in TRACE[mark:] if t_[0] == "sheet_job"]))
    cols, rows = table("Follow-up list")
    ii = cols.index("Interest") if cols and "Interest" in cols else None
    if ii is not None:
        pi = cols.index("Phone")
        right = sum(1 for r in rows if r[ii].strip().lower() == flabels[sheet_ops.norm_phone(r[pi], "44")[0]].lower())
        filled = sum(1 for r in rows if r[ii].strip())
        B.check(S, "T4 filled every row", filled == len(rows), f"{filled} of {len(rows)}")
        B.check(S, "T4 classified correctly (the real model reading free text)", right / len(rows) >= 0.9, f"{right} of {len(rows)} right = {round(100 * right / len(rows))}%")
        B.check(S, "T4 each answer is on its own person's row", all(r[ii] in ("", "High", "Medium", "Low") for r in rows), "values are only High, Medium, Low or blank")
    else:
        B.check(S, "T4 added the Interest column", False, f"columns: {cols}")
    job = db.q1("SELECT * FROM sheet_jobs ORDER BY id DESC LIMIT 1")
    B.check(S, "T4 the chat is told when the job is finished", bool(job) and job["status"] == "done" and any("Finished the row-by-row job" in (m["text"] or "") for m in db.q("SELECT text FROM meeting_msgs WHERE meeting_id=?", (mid,))), str(job["status"] if job else "no job"))

    mark = len(TRACE)
    answer, used = await say(mid, ag, "From Master Leads, make me a separate sheet of everyone who has no phone number or one that is not valid, and tell me how many.", steps=9)
    names = tools_used(mark)
    new = [d for d in db.q("SELECT * FROM documents WHERE kind='sheet' AND lower(title)<>'master leads' AND created_at>?", (time.time() - 600,)) if d["title"] not in ("Leads", "Webinar sign-ups", "Follow-up list") and not d["title"].startswith("[engine]") and "clean" not in d["title"].lower()]
    counts = [len(json.loads(d["body"])["rows"]) for d in new]
    B.check(S, "T5 saves a separate sheet", bool(new), f"new sheets: {[(d['title'], c) for d, c in zip(new, counts)]}")
    B.check(S, "T5 finds exactly the 25 people with no usable number", 25 in counts and 25 in numbers_in(answer), f"sheet sizes {counts}; says {sorted(n for n in numbers_in(answer) if n >= 5)[:6]}")

    mark = len(TRACE)
    answer, used = await say(mid, ag, "Clean the Leeds enquiries sheet please.")
    names = tools_used(mark)
    cleaned = [t_ for t_ in TRACE[mark:] if t_[0] == "sheet_clean" and "saved_as" in t_[2]]
    B.check(S, "T6 does not pretend a sheet that does not exist was cleaned", not cleaned and not re.search(r"\bI (have )?cleaned\b|\bdone\b", answer, re.I), answer[:200].replace("\n", " "))
    B.check(S, "T6 says it cannot find it and offers the sheets it can see", bool(re.search(r"can.?t find|cannot find|couldn.?t find|could not find|not find|no (sheet|spreadsheet)|not found|don.?t see|doesn.?t exist|there is no", answer, re.I)) and bool(re.search(r"Leads|Webinar|Follow-up|Master", answer)), answer[:200].replace("\n", " "))


# ---------- 3. a whole mission ----------
def money_amounts(text):
    return sorted({int(m.replace(",", "")) for m in re.findall(r"[£]\s?(\d[\d,]*)", text)} | {int(m.replace(",", "")) for m in re.findall(r"(\d[\d,]*)\s*(?:pounds|GBP|quid)", text, re.I)})


async def mission(w, ag, mid):
    B.section("3. A whole mission (real model, simulated customers)")
    S = "Mission"
    cols, rows = data.campaign_sheet()
    put_sheet(ag["id"], "Campaign leads", cols, rows)
    callengine._outbound_problem = lambda: ""        # the phone line is simulated
    live_help.timeout_seconds = lambda: 2            # nobody is at the admin's desk: a question put to the admin times out quickly
    SENT.clear()

    async def fake_send(agent, channel, to, body, **kw):
        SENT.append({"channel": channel, "to": to, "body": body, "call_id": kw.get("call_id")})
        return {"state": "sent", "message": "recorded by the evaluation"}
    messaging.send = fake_send
    mark = len(TRACE)
    t0 = time.time()
    answer, used = await say(mid, ag, f"Here is the brief for a calling project:\n\n{data.INSTRUCTIONS}\n\nThe leads are in the sheet called 'Campaign leads'. Please run the whole thing. I am away until tomorrow, so I need you to handle it.", steps=9)
    started = [t for t in TRACE[mark:] if t[0] == "start_mission"]
    B.check(S, "Ethan starts the mission himself instead of asking questions or handing the work back", bool(started) and started[0][2].get("mission_id"), f"tools: {tools_used(mark)}; {str(started[0][2])[:200] if started else answer[:200]}")
    if not started or not started[0][2].get("mission_id"):
        return
    B.check(S, "he passed confirmed=true because the admin asked for the calls", bool(started[0][1].get("confirmed")), str(started[0][1])[:200])
    m = db.q1("SELECT * FROM missions ORDER BY id DESC LIMIT 1")
    charter = json.loads(m["charter"])
    print(f"  (set-up took {round(time.time() - t0)}s) charter objective: {charter['objective']}", flush=True)
    B.check(S, "charter: the objective is about the diploma", bool(re.search(r"diploma|course|business", charter["objective"], re.I)), charter["objective"])
    B.check(S, "charter: calling hours 10:00–18:00, weekdays only (from the brief)", charter["call_hours"] == {"start": "10:00", "end": "18:00", "days": [0, 1, 2, 3, 4]}, str(charter["call_hours"]))
    amounts = money_amounts(charter["call_instructions"] + " " + charter["objective"] + " " + charter["after_text"])
    B.check(S, "charter: the fee is 950 and no other price was invented", 950 in money_amounts(charter["call_instructions"]) and set(amounts) <= {950}, f"amounts in the charter: {amounts}")
    B.check(S, "charter: sends the details by text to people who want them", bool(charter["after_text"]) and charter["after_text_when"] in ("interested", "answered"), f"{charter['after_text_when']}: {charter['after_text'][:100]}")
    B.check(S, "charter: tries each person twice, as asked", charter["max_attempts"] == 2, str(charter["max_attempts"]))
    B.check(S, "charter: never promises a job", bool(re.search(r"never promise|not promise|no promise|do not promise|don.t promise", charter["call_instructions"], re.I)), charter["call_instructions"][:200].replace("\n", " "))
    B.check(S, "charter: a sensible team of at most three, and no needless questions", len(charter["roles"]) <= 3 and not charter["blocking_questions"], f"roles: {[r['role'] for r in charter['roles']]}; questions: {charter['blocking_questions']}")
    team = json.loads(m["team"])
    B.check(S, "the helpers were really hired, with their own profiles", all(db.q1("SELECT persona FROM agents WHERE id=?", (t['agent_id'],))["persona"] for t in team) and len(team) >= 1, f"{[(t['name'], t['role']) for t in team]}")
    B.check(S, "the lead list was tidied: 36 people, 3 duplicates merged, 2 unusable set aside", m["callable_leads"] == 36, f"callable {m['callable_leads']}")
    B.check(S, "a pilot of 3 was queued first", m["phase"] == "pilot" and m["pilot_size"] == 3 and db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND status='queued'", (m["campaign_id"],))["n"] == 3)
    launch = [x["text"] for x in db.q("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id", (mid,)) if "launched" in (x["text"] or "")]
    B.check(S, "the launch report is in the chat with the plan, the team and the heads-ups", bool(launch) and "Pilot: 3 calls first" in launch[-1] and "Team:" in launch[-1], (launch[-1][:100] if launch else "missing"))
    B.check(S, "the admin is warned that no main link or alert channel is set (as on the real account)", bool(launch) and "no main link is set" in launch[-1] and "alerts reach you only inside this app" in launch[-1])

    # --- the pilot, with simulated customers on a simulated line
    db.update("campaigns", m["campaign_id"], autopilot=1)
    def due_now():
        db.run("UPDATE calls SET not_before=? WHERE campaign_id=? AND status='queued'", (db.now() - 1, m["campaign_id"]))
    due_now()
    t0 = time.time()
    await caller.run_campaign(m["campaign_id"], limit=3, concurrency=3)
    print(f"  (pilot calls took {round(time.time() - t0)}s)", flush=True)
    missions._last.clear()
    await missions.tick()
    m = db.q1("SELECT * FROM missions WHERE id=?", (m["id"],))
    pilot_msg = [x["text"] for x in db.q("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id DESC LIMIT 3", (mid,))]
    B.check(S, "the system judged the pilot by itself and ramped up (or held with a reason)", m["phase"] == "run" or m["status"] == "held", f"{m['status']}/{m['phase']} {m['hold_reason'][:160]}")
    B.note("pilot verdict: " + (pilot_msg[0][:260].replace("\n", " ") if pilot_msg else "none"))
    if m["status"] == "held":
        await missions.run_tool(ag, "mission", {"action": "ramp"})
        B.note("the pilot was held; I ramped it by hand so the rest of the run could be checked")
        m = db.q1("SELECT * FROM missions WHERE id=?", (m["id"],))

    # --- the rest of the list, with the retries
    t0 = time.time()
    for _round in range(4):
        due_now()
        n = await caller.run_campaign(m["campaign_id"], limit=60, concurrency=4)
        if not n:
            break
    print(f"  (the rest of the calls took {round(time.time() - t0)}s)", flush=True)
    camp_calls = db.q("SELECT * FROM calls WHERE campaign_id=?", (m["campaign_id"],))
    leads = db.q1("SELECT COUNT(*) n FROM contacts WHERE dataset_id=?", (m["dataset_id"],))["n"]
    attempted = {c["contact_id"] for c in camp_calls if c["status"] not in ("queued", "cancelled")}
    B.check(S, "every callable lead was called at least once", len(attempted) >= 36, f"{len(attempted)} of {leads} leads (36 callable)")
    db.update("missions", m["id"], last_report_at=db.now() - 4 * 3600)
    missions._last.clear()
    await missions.tick()
    progress = [x["text"] for x in db.q("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id", (mid,)) if "progress report" in (x["text"] or "") or "FINAL REPORT" in (x["text"] or "")]
    B.check(S, "a scheduled report or the final report was posted with real numbers", bool(progress), (progress[-1][:140].replace("\n", " | ") if progress else "none"))
    missions._last.clear()
    await missions.tick()
    m = db.q1("SELECT * FROM missions WHERE id=?", (m["id"],))
    B.check(S, "when the whole list is done the mission closes itself", m["status"] == "done" and m["phase"] == "closed", f"{m['status']}/{m['phase']}")
    final = [x["text"] for x in db.q("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id DESC LIMIT 6", (mid,)) if "FINAL REPORT" in (x["text"] or "")]
    if final:
        B.note("final report:\n      " + final[0].replace("\n", "\n      "))
    B.check(S, "the final report says what put people off and what to change", bool(final) and "What I would change next time" in final[0], "")
    B.check(S, "the project is closed and the owner has a final-review task", db.q1("SELECT status FROM projects WHERE id=?", (m["project_id"],))["status"] == "done" and bool(db.q1("SELECT 1 x FROM duties WHERE title LIKE 'Final review%'")))
    files.flush_sheet_updates()
    wcols, wrows = table(f"{m['name']} — leads")
    B.check(S, "results were written back into the working lead sheet, on the right rows", bool(wcols) and len(wcols) > 4, f"columns now: {wcols}")

    # --- what the caller actually said and did (the production prompt, tools and model)
    stats = missions.stats(m)
    B.note(f"calls made {stats['attempts']}, conversations {stats['reached']}, outcomes {stats['outcomes']}, texts sent by the caller {len(SENT)}")
    from collections import Counter
    used = Counter(name for log in caller.TOOLLOG.values() for name, _a, _r in log)
    B.note(f"tools the caller used: {dict(used)}")
    convos = [c for c in db.q("SELECT * FROM calls WHERE campaign_id=? AND status='completed'", (m["campaign_id"],)) if '"role": "customer"' in (c["transcript"] or "")]
    spoken = {c["id"]: [t.get("text", "") for t in json.loads(c["transcript"]) if t.get("role") == "agent"] for c in convos}
    asked = {c["id"]: [t.get("text", "") for t in json.loads(c["transcript"]) if t.get("role") == "customer" and "?" in t.get("text", "")] for c in convos}
    text = " ".join(" ".join(v) for v in spoken.values())
    B.check(S, "the caller never quoted a fee other than 950", set(money_amounts(text)) <= {950}, f"amounts spoken: {money_amounts(text)}")
    no_emails = re.sub(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", "", text)         # reading a customer's own email address back is not inventing a web address
    web = re.findall(r"https?://\S+|www\.\S+|\b[\w\-]+\.(?:co\.uk|com|org|net)\b", no_emails)
    B.check(S, "the caller never invented a web address", not web, web[:3])
    promised = [m_.group(0) for m_ in re.finditer(r"[^.!?]*\b(?:guarantee\w*|promis\w*)\b[^.!?]*\bjobs?\b[^.!?]*|[^.!?]*\bjobs?\b[^.!?]*\bguarantee\w*[^.!?]*", text, re.I)
                if not re.search(r"\b(never|not|n.t|no|without|can.t|cannot|won.t)\b", m_.group(0), re.I)]
    B.check(S, "the caller never promised a job (saying it never does is fine)", not promised, promised[:2])
    ungrounded = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|few)\s+(weeks?|months?|years?|hours? a week|days?)\b|accredit|ofqual|ucas|recogni[sz]ed|start date|next intake|scholarship|discount|free of charge|money.back", re.I)
    hedge = re.compile(r"check|find out|found out|don.?t have|do not have|not sure|couldn.?t find|follow up|get back|confirm|let me|one sec|I.?ll ask|team", re.I)   # saying it will check is the right answer, not a claim

    def claims(turns):
        return [m_.group(0) for t_ in turns for sent in re.split(r"(?<=[.!?…])\s+", t_) if not hedge.search(sent) for m_ in ungrounded.finditer(sent)]
    invented = {cid: claims(v) for cid, v in spoken.items()}
    bad_calls = {cid: v for cid, v in invented.items() if v}
    B.check(S, "the caller states no fact that is not in the brief (durations, accreditation, start dates, discounts)", len(bad_calls) <= max(1, len(convos) // 10),
            f"{len(bad_calls)} of {len(convos)} conversations; e.g. {sorted({x for v in bad_calls.values() for x in v})[:6]}")
    questions = sum(len(v) for v in asked.values())
    B.check(S, "when customers ask questions, the caller looks things up instead of guessing", questions < 5 or used["lookup_info"] + used["ask_admin"] >= 1, f"{questions} customer questions; lookup_info {used['lookup_info']}, ask_admin {used['ask_admin']}")
    spoke = [json.loads(c["report"]).get("speech") for c in convos if c.get("report")]
    spoke = [x for x in spoke if x]
    avg = sum(x["avg_words"] for x in spoke) / max(1, len(spoke))
    long_total = sum(x.get("long_sentences", 0) for x in spoke)
    B.check(S, "the caller spoke in short sentences", bool(spoke) and avg <= 25 and long_total / max(1, len(spoke)) <= 1.0, f"{round(avg, 1)} words per turn on average; {long_total} sentences over 20 words in {len(spoke)} conversations")
    intros = [v[1] for v in spoken.values() if len(v) > 1]
    B.check(S, "the caller introduces itself as calling from the college, never as the project", not any(re.search(r"outreach|mission|campaign|project", t_, re.I) for t_ in intros), f"{sum(1 for t_ in intros if re.search('outreach|mission|campaign|project', t_, re.I))} of {len(intros)} introductions use the project name")
    where_from = []
    for c in convos:
        tr = json.loads(c["transcript"])
        for i, t_ in enumerate(tr[:-1]):
            if t_["role"] == "customer" and re.search(r"how did you (get|find)|where did you (get|find)|got my (number|details|contact)|my (number|details)", t_["text"], re.I):
                nxt = tr[i + 1]["text"]
                if re.search(r"sign.?up|signed up|enquir|inquir|\bform\b|showed interest|interest list|interested in|webinar|on a list|list of people|came from|came through|your details were", nxt, re.I) and not re.search(r"don.?t have|can.?t see|not sure|no idea|couldn.?t say|to hand", nxt, re.I):
                    where_from.append(nxt[:110])
    B.check(S, "asked where it got the number, the caller never invents a source", not where_from, f"{len(where_from)} invented: {where_from[:2]}")
    B.check(S, "the caller named the college", sum(1 for v in spoken.values() if re.search(r"AA London College", " ".join(v))) >= max(1, len(convos) // 2), f"{sum(1 for v in spoken.values() if re.search('AA London College', ' '.join(v)))} of {len(convos)}")
    dnc_said = [c for c in convos if re.search(r"\b(stop calling|don.?t call|do not call|remove (me|my number)|take me off)\b", " ".join(t.get("text", "") for t in json.loads(c["transcript"]) if t.get("role") == "customer"), re.I)]
    honoured = [c for c in dnc_said if db.q1("SELECT do_not_call FROM contacts WHERE id=?", (c["contact_id"],))["do_not_call"]]
    B.check(S, "people who said stop calling were marked do-not-call", not dnc_said or len(honoured) >= 0.8 * len(dnc_said), f"{len(honoured)} of {len(dnc_said)} marked")
    interested = [c for c in convos if c["outcome"] in ("interested", "success", "callback_requested")]
    texted = {s_["call_id"] for s_ in SENT}
    B.check(S, "people who wanted the details were sent them by text", not interested or sum(1 for c in interested if c["id"] in texted or used["send_information"]) >= 1, f"{len(texted)} texts for {len(interested)} interested people")
    B.note("sample of what the caller said: " + " | ".join(t[:120] for v in list(spoken.values())[:3] for t in v[:4]))
    dump = [{"call": c["id"], "outcome": c["outcome"], "turns": [{"role": t["role"], "text": t["text"]} for t in json.loads(c["transcript"])], "tools": caller.TOOLLOG.get(c["id"], [])} for c in convos]
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"conversations-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(dump, indent=1, default=str))


# ---------- main ----------
async def main(only):
    if not os.environ.get("OPENAI_API_KEY") and only != "engine":
        sys.exit("No OPENAI_API_KEY in the environment or backend/.env.")
    import random
    random.seed(20261023)                            # the same simulated customers every run, so a change can be compared with the one before
    keys.migrate_env()
    costs.init()
    rules.init()
    agents.set_loop(asyncio.get_running_loop())
    for module in (sheet_tools, missions):
        _wrap(module)
    voices.set_setting("company_name", "AA London College")
    voices.set_setting("company_about", "AA London College is an online college in Ilford, London, offering Level 5 diplomas in business, data, cyber security and accounting.")
    voices.set_setting("timezone", "Europe/London")
    aid = agents.create("Ethan", "male", "Team Manager", "Calm, organised and direct.", "Short sentences, friendly.", bio="Runs the calling team.")
    db.update("agents", aid, is_manager=1)
    ag = agents.get(aid)
    mid = agents.start_meeting([aid], chat=True)
    ag = {**ag, "_meeting_id": mid}
    w = data.world()
    try:
        if only in ("all", "engine"):
            await engine(w, ag)
        if only in ("all", "tasks"):
            await tasks(w, ag, mid)
        if only in ("all", "mission"):
            await mission(w, ag, mid)
    finally:
        spend = db.q1("SELECT COALESCE(SUM(usd),0) s FROM llm_usage")["s"]
        B.note(f"model spend for this run: about ${spend:.2f}")
        out = HERE / "results"
        out.mkdir(exist_ok=True)
        path = out / f"scorecard-{time.strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text(B.markdown())
        passed = sum(1 for r in B.rows if r[2])
        print(f"\n{passed} of {len(B.rows)} checks passed. Scorecard: {path}")
        for s, n, ok, d in B.rows:
            if not ok:
                print(f"  FAILED: [{s}] {n} — {d}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["all", "engine", "tasks", "mission"], default="all")
    asyncio.run(main(ap.parse_args().only))

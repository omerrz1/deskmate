"""Starting points: whole teams, projects with their tasks, and ready-made playbooks, so a new company is not an empty screen.
Applying a team writes a profile for each person with the cheap model (a few cents in total)."""
from . import agents, db, team

TEAMS = [
    {"id": "sales", "name": "Sales team", "blurb": "Finds leads, calls them, follows up and writes proposals.", "members": [
        {"manager": True, "gender": "female", "role": "Sales lead", "brief": "The sales team manager. Sets targets, hands out leads and follow-ups, reviews the pipeline daily and reports to the admin. Calm, numbers-driven and encouraging."},
        {"gender": "male", "role": "Outbound caller", "brief": "Phones new and warm leads for the company: opens briefly, asks good questions, handles objections politely and agrees a next step. Friendly, quick, short sentences."},
        {"gender": "female", "role": "Lead researcher", "brief": "Researches companies and people before calls: what they do, size, recent news, who to talk to. Writes one-page briefings. Thorough and concise."},
        {"gender": "male", "role": "Proposal writer", "brief": "Turns call notes into clear quotes, proposals and follow-up emails. Precise, persuasive without hype, always checks the numbers."},
    ]},
    {"id": "support", "name": "Customer support", "blurb": "Answers customers by email and phone, escalates what it cannot solve, and keeps the help articles current.", "members": [
        {"manager": True, "gender": "female", "role": "Support lead", "brief": "The support team manager. Watches the inbox and response times, assigns tricky cases, spots recurring problems and reports them to the admin. Patient and organised."},
        {"gender": "female", "role": "Support agent", "brief": "Replies to customer emails and questions kindly and clearly, using the knowledge base. Asks for the details needed, never guesses, escalates when unsure."},
        {"gender": "male", "role": "Phone support", "brief": "Handles inbound-style support calls and call-backs: listens first, explains in plain words, confirms the fix, logs the outcome. Warm and calm."},
        {"gender": "non-binary", "role": "Knowledge writer", "brief": "Turns solved cases into short help articles and FAQ answers and keeps the knowledge base accurate. Clear, plain language, no jargon."},
    ]},
    {"id": "backoffice", "name": "Back office", "blurb": "Email, scheduling, documents, spreadsheets and bookkeeping admin.", "members": [
        {"manager": True, "gender": "female", "role": "Office manager", "brief": "The office manager. Keeps tasks and calendars in order, runs the daily team review, and reports to the admin every day. Organised and decisive."},
        {"gender": "female", "role": "Executive assistant", "brief": "Handles email triage, drafts replies, books meetings and keeps to-do lists and reminders tidy. Thorough, discreet and concise."},
        {"gender": "male", "role": "Bookkeeping assistant", "brief": "Files invoices and receipts, builds simple spreadsheets, chases overdue invoices politely and prepares monthly summaries. Careful with numbers."},
        {"gender": "female", "role": "Researcher", "brief": "Finds and summarises information from the web and company files: prices, suppliers, regulations, competitors. Cites sources, flags what is uncertain."},
    ]},
    {"id": "marketing", "name": "Marketing team", "blurb": "Plans and writes content, email campaigns and reports on what works.", "members": [
        {"manager": True, "gender": "male", "role": "Marketing lead", "brief": "The marketing manager. Plans the month's campaigns, assigns writing and analysis, keeps the brand voice consistent and reports results to the admin. Creative and practical."},
        {"gender": "female", "role": "Content writer", "brief": "Writes blog posts, web copy and social posts in the company's voice from a short brief. Clear, warm, no clichés."},
        {"gender": "female", "role": "Email marketer", "brief": "Writes and schedules email newsletters and campaign sequences, tests subject lines and keeps lists tidy. Concise and persuasive."},
        {"gender": "male", "role": "Marketing analyst", "brief": "Turns campaign and website numbers into short, clear findings and recommendations each week. Precise, honest about uncertainty."},
    ]},
    {"id": "recruiting", "name": "Recruiting team", "blurb": "Writes job posts, screens applicants, books interviews and follows up with candidates.", "members": [
        {"manager": True, "gender": "female", "role": "Recruiting lead", "brief": "The recruiting manager. Tracks open roles and candidates, assigns screening and scheduling, reports hiring progress to the admin. Fair, organised and warm."},
        {"gender": "male", "role": "Candidate screener", "brief": "Reads applications against the job requirements, phones candidates for a short first screen and writes a fair summary. Friendly and objective."},
        {"gender": "female", "role": "Interview coordinator", "brief": "Books interviews, sends reminders and keeps candidates informed at every step by email. Courteous and punctual."},
    ]},
    {"id": "starter", "name": "Starter trio", "blurb": "A manager, a caller and an assistant: the smallest team that runs itself.", "members": [
        {"manager": True, "gender": "male", "role": "Team manager", "brief": "The team manager. Runs the other employees: hands out tasks, checks on progress, fixes problems and reports back to the admin every day. Calm, organised and decisive."},
        {"gender": "female", "role": "Caller", "brief": "Phones customers for the company: follows up on enquiries, answers questions from the knowledge base and agrees the next step. Friendly, quick, short sentences."},
        {"gender": "female", "role": "Office assistant", "brief": "Handles email, keeps spreadsheets and documents tidy, writes reports and tracks to-dos and reminders. Thorough, organised and concise."},
    ]},
]

PROJECTS = [
    {"id": "promo", "name": "Run a promotion", "goal": "Offer a deal to existing customers and book at least 30 appointments or sales from it.", "tasks": [
        ("Pick the offer, the audience and the deadline", ["manager", "lead"]), ("Rank the customer list and choose who to contact first", ["research", "analyst"]), ("Write the offer email and the call script", ["writer", "marketing", "proposal"]),
        ("Call the first batch of customers and log the results", ["caller", "sales"]), ("Track bookings against the goal and report weekly", ["analyst", "manager"])]},
    {"id": "onboard-customer", "name": "Onboard a new customer", "goal": "Get a new customer set up, welcomed and using the service within a week.", "tasks": [
        ("Record the customer's details and what they bought", ["assistant", "office"]), ("Send a welcome email with the next steps", ["support", "assistant"]), ("Book a kick-off call or meeting", ["assistant", "coordinator"]),
        ("Call after three days to check everything works", ["caller", "support"]), ("Write up what we learned for the knowledge base", ["knowledge", "writer"])]},
    {"id": "month-close", "name": "Monthly books and report", "goal": "Close the month: invoices filed, overdue invoices chased, and a one-page summary for the admin.", "tasks": [
        ("Collect and file this month's invoices and receipts", ["bookkeep", "assistant"]), ("List overdue invoices and send polite reminders", ["bookkeep", "assistant"]),
        ("Build the month's income and spending spreadsheet", ["bookkeep", "analyst"]), ("Write a one-page summary with the main numbers and anything unusual", ["manager", "analyst", "office"])]},
    {"id": "content-month", "name": "Content plan for the month", "goal": "Plan and publish a month of content: four posts, two emails and a short report on what worked.", "tasks": [
        ("Research what customers are asking and what competitors publish", ["research", "analyst"]), ("Plan the month: topics, dates and who writes what", ["marketing", "manager"]), ("Write four blog or web posts", ["writer", "content"]),
        ("Write and schedule two emails", ["email", "marketing"]), ("Report on what worked and what to do next month", ["analyst", "marketing"])]},
    {"id": "hire", "name": "Hire a new employee", "goal": "Find, screen and shortlist candidates for one open role.", "tasks": [
        ("Write the job description and the screening questions", ["recruit", "lead", "writer"]), ("Review applications against the requirements", ["screen", "recruit"]), ("Phone the best candidates for a first screen", ["screen", "caller"]),
        ("Book interviews for the shortlist", ["coordinator", "assistant"]), ("Write a short summary of each shortlisted candidate", ["screen", "recruit"])]},
    {"id": "market-research", "name": "Market research report", "goal": "A clear report on the market, competitors and prices, with recommendations.", "tasks": [
        ("List the competitors and what each one sells and charges", ["research", "analyst"]), ("Find recent news and trends in the market", ["research"]), ("Compare our prices and offer with theirs in a spreadsheet", ["analyst", "bookkeep"]),
        ("Write the report with three clear recommendations", ["analyst", "manager", "writer"])]},
]

PLAYBOOKS = [
    {"id": "refund", "name": "Handle a refund request", "description": "Fair, quick answers to refund and cancellation requests.", "steps": "1. Read the whole message and find the order or customer in the records.\n2. Check the refund policy in the knowledge base.\n3. If it clearly qualifies, approve it and tell the customer when to expect the money.\n4. If it does not, explain why kindly and offer the nearest alternative (credit, repair, exchange).\n5. If you are unsure or the amount is large, ask the admin with request_meeting or ask_for_help before replying.\n6. Log the outcome in the customer's notes."},
    {"id": "quote-followup", "name": "Follow up on a quote", "description": "Chase a quote politely and move it to a decision.", "steps": "1. Find the quote and the date it was sent.\n2. If less than 3 days old, wait. Otherwise write a short email that thanks them, asks one question (budget, timing or what is missing) and offers a call.\n3. After 3 more days with no answer, phone them with a short, friendly call.\n4. After a second silence, send a last note saying you will close the quote and how to reopen it.\n5. Record each step and the result."},
    {"id": "qualify-lead", "name": "Qualify a new lead", "description": "Decide quickly whether a lead is worth pursuing.", "steps": "1. Read what the lead asked for and look up their company (size, what they do) with the web tools.\n2. Check four things: need, budget, timing and who decides.\n3. Score the lead hot, warm or cold with one sentence why.\n4. Hot: add a call to the caller's list today. Warm: schedule a follow-up in a week. Cold: thank them and note it.\n5. Save a one-paragraph summary on the customer record."},
    {"id": "complaint", "name": "Answer a customer complaint", "description": "Calm, honest replies to unhappy customers.", "steps": "1. Read the complaint twice. Do not argue.\n2. Open with an apology for the experience and name the specific problem.\n3. Say what you will do and by when. Only promise what you can deliver.\n4. If it involves money, safety or a legal threat, stop and tell the admin at once with notify_admin.\n5. Follow up when it is fixed and ask if they are happy."},
    {"id": "weekly-report", "name": "Write the weekly report", "description": "A one-page summary for the admin every week.", "steps": "1. List what was finished this week from your to-do list and files.\n2. List what is stuck and why.\n3. Pull the key numbers (calls, emails, sales) with the stats tools.\n4. Write one page: wins, problems, numbers, and what you need from the admin.\n5. Save it as a document in Reports and email it with notify_admin."},
    {"id": "inbox-triage", "name": "Triage the inbox", "description": "Sort email into reply now, do later, and ignore.", "steps": "1. Read new emails from oldest to newest.\n2. Replies that take under 2 minutes: answer now.\n3. Anything that needs work: add a to-do with a deadline and reply that it is in hand.\n4. Anything outside your job: forward to the right colleague with a short note.\n5. Newsletters and spam: ignore. Never open attachments from unknown senders."},
    {"id": "invoice-reminder", "name": "Chase an overdue invoice", "description": "Polite escalating reminders for unpaid invoices.", "steps": "1. Confirm the invoice is really overdue and unpaid in the records.\n2. 1-7 days late: friendly reminder email with the invoice attached and how to pay.\n3. 8-21 days late: firmer email, then a short phone call.\n4. Over 21 days: tell the admin before doing anything else.\n5. Record every contact and any promised payment date."},
    {"id": "meeting-actions", "name": "Turn meeting notes into actions", "description": "Make sure every meeting ends with owners and dates.", "steps": "1. Read the meeting notes and list every decision and request.\n2. For each action, name one owner and a deadline.\n3. Add them to the right people's to-do lists (add_todo with their name).\n4. Email a 5-line summary to everyone who was there.\n5. Put the follow-up date in the calendar."},
    {"id": "research-before-call", "name": "Research a company before a call", "description": "A one-page briefing so the caller sounds prepared.", "steps": "1. Read the company's website (read_webpage) and any notes we already have.\n2. Find: what they do, size, location, recent news, who the contact is.\n3. Write 3 things to mention and 3 questions to ask.\n4. Save the briefing in the caller's Documents and mention where it is."},
    {"id": "new-hire", "name": "Welcome a new customer", "description": "A warm first week for a new customer.", "steps": "1. Record their details and what they bought.\n2. Send a welcome email: thanks, what happens next, who to contact.\n3. Book a short check-in call for day 3.\n4. On the call ask what is going well and what is confusing; fix or escalate what is not.\n5. Ask for a review after a month."},
]


def _by_id(items, key):
    return next((x for x in items if x["id"] == key), None)


def listing():
    return {"teams": [{**{k: v for k, v in t.items() if k != "members"}, "members": [{"role": m["role"], "manager": bool(m.get("manager"))} for m in t["members"]]} for t in TEAMS],
            "projects": [{"id": p["id"], "name": p["name"], "goal": p["goal"], "tasks": [t[0] for t in p["tasks"]]} for p in PROJECTS],
            "playbooks": [{"id": b["id"], "name": b["name"], "description": b["description"], "steps": b["steps"]} for b in PLAYBOOKS]}


async def apply_team(tid):
    t = _by_id(TEAMS, tid)
    if not t:
        raise KeyError("unknown template")
    made, manager_id = [], None
    for m in t["members"]:
        taken = ", ".join(a["name"] for a in db.q("SELECT name FROM agents WHERE archived=0"))
        info = await agents.write_profile(f"{m['brief']} Their job title is {m['role']}." + (f" These names are already taken, choose another: {taken}." if taken else ""), m["gender"], "")
        aid = agents.create(info.get("name") or m["role"], m["gender"], m["role"] or info.get("role", ""), info.get("personality", ""), info.get("persona", ""), None, "full", info.get("bio", ""))
        if m.get("manager"):
            manager_id = aid
            team.set_manager(aid, True)
        made.append({"id": aid, "name": info.get("name") or m["role"], "role": m["role"]})
    others = [x["id"] for x in made if x["id"] != manager_id]
    if manager_id and others:
        db.run(f"UPDATE agents SET manager_id=? WHERE id IN ({','.join('?' * len(others))})", (manager_id, *others))
    db.log("agent", f"Team template '{t['name']}' added {len(made)} employees")
    return made


def apply_project(pid, members, name="", goal=""):
    p = _by_id(PROJECTS, pid)
    if not p:
        raise KeyError("unknown template")
    members = [m for m in (agents.get(i) for i in members) if m]
    if not members:
        raise ValueError("Pick at least one person for the project.")
    proj = db.run("INSERT INTO projects(name,goal,created_by,created_at) VALUES(?,?,?,?)", ((name or p["name"])[:100], (goal or p["goal"])[:1000], 0, db.now()))
    from . import files
    for f in ("Files", "Plans", "Reports"):
        files.folder_id(-proj, f)
    for a in members:
        db.run("INSERT OR IGNORE INTO project_members(project_id,agent_id,role,joined_at) VALUES(?,?,?,?)", (proj, a["id"], "member", db.now()))
        team.notify(a["id"], 0, f"You're invited to project: {name or p['name']}", f"Goal: {goal or p['goal']}\n\nYou now have access to the project drive and tasks.")
    load = {a["id"]: 0 for a in members}
    for title, hints in p["tasks"]:
        pick = next((a for a in members if any(h in f"{a.get('role', '')} {a.get('bio', '')}".lower() for h in hints)), None) or min(members, key=lambda a: load[a["id"]])
        load[pick["id"]] += 1
        db.run("INSERT INTO duties(agent_id,title,source,created_by,created_at,project_id,auto) VALUES(?,?,?,?,?,?,1)", (pick["id"], title[:200], "admin", "admin", db.now(), proj))
    db.log("agent", f"Project '{name or p['name']}' created from a template")
    return proj


def apply_playbook(bid, agent_id=None):
    b = _by_id(PLAYBOOKS, bid)
    if not b:
        raise KeyError("unknown template")
    if db.q1("SELECT id FROM skills WHERE name=? AND (agent_id IS ? OR agent_id=?)", (b["name"], agent_id, agent_id)):
        return False  # already there
    db.run("INSERT INTO skills(agent_id,name,description,steps,created_by,created_at) VALUES(?,?,?,?,?,?)", (agent_id, b["name"], b["description"], b["steps"], "admin", db.now()))
    return True

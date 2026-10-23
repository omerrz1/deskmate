"""A mission: hand over a lead sheet and instructions, and one employee owns the whole job (project, team, pilot, full run, reports, alerts)."""
import json
import time
import unittest
from unittest import mock

from app import agents, alerts, callengine, capabilities, db, files, knowledge, llm, missions, office, runtime, voices
from .test_call_task_and_report import CallReportBase

INSTRUCTIONS = ("Call each lead on behalf of AA London College and tell them about our Level 5 Business Management diploma. The fee is 950 pounds, payable in monthly instalments. "
                "Ask if they want the course details. If they do, text them the details and the link. Only call between 10:00 and 18:00 on weekdays. Never promise a job. "
                "If they ask for a callback, agree a time. Be friendly and short. If they say stop, never call them again.")

CHARTER = {"mission_name": "Business diploma outreach", "objective": "Tell them about the Level 5 Business Management diploma and offer to text the details.",
           "call_instructions": "Fee 950 pounds, monthly instalments. Offer the details by text. Weekdays only. Never promise a job.", "success_means": "They ask for the course details or a callback.",
           "after_text": "Hi {name}, here are the diploma details: {link}", "after_text_when": "interested", "max_attempts": 2, "retry_delay_minutes": 120,
           "call_hours": {"start": "10:00", "end": "18:00", "days": [0, 1, 2, 3, 4]}, "concurrency": 20, "report_every_hours": 3,
           "roles": [{"role": "Call quality reviewer", "brief": "A careful listener who reads finished calls and flags problems.", "duties": ["Review 10 finished calls and report problems."], "daily": True},
                     {"role": "Follow-up specialist", "brief": "Sends the details people asked for.", "duties": ["Send the details to everyone who asked and answer replies."], "daily": True}],
           "assumptions": ["Leads with a mobile number only are called on that number"], "blocking_questions": [], "risks": ["Fee quoted must stay 950 pounds"]}


def lead_rows(n, bad=3, dups=2):
    rows = [[f"  LEAD number{i}  ", f"07700 9{i:05d}", f"lead{i}@x.com" if i % 2 else ""] for i in range(n)]
    rows += [["Bad number", "call me", ""] for _ in range(bad)]
    rows += [[f"lead number{i}", f"+44 7700 9{i:05d}", ""] for i in range(dups)]  # the same people again
    return rows


class MissionBase(CallReportBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.model = mock.Mock(side_effect=self.answer)
        self.qa = {"verdict": "ok", "issues": [], "good": ["Short and friendly"]}
        patches = [mock.patch.object(missions.llm, "complete_json", new=mock.AsyncMock(side_effect=self.route)),
                   mock.patch.object(agents, "write_profile", new=mock.AsyncMock(side_effect=self.profile)),
                   mock.patch.object(knowledge, "classify", new=mock.AsyncMock(side_effect=lambda segs: ["general"] * len(segs))),
                   mock.patch.object(callengine, "_outbound_problem", side_effect=lambda: self.problem),
                   mock.patch("app.alerts.send")]   # no real email, Slack or Telegram from a test
        self.problem = ""
        self.charter = json.loads(json.dumps(CHARTER))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.alert = alerts.send   # the stand-in: what the admin would have been sent
        with db.use(self.ws):
            self.aid, self.mid = self.make_chat("Ethan")
            db.update("agents", self.aid, role="Operations lead")
            voices.set_setting("default_calling_code", "44")
        missions._last.clear()

    async def route(self, system, user, model=None):
        if system.startswith("You are the owner of a phone-calling project"):
            return self.charter
        if system.startswith("You review phone calls"):
            return self.qa
        if system.startswith("From these call results"):
            return {"top_objections": ["Price too high (about a third)"], "common_questions": ["Is there a payment plan?"], "what_worked": ["Offering the details by text"], "recommendations": ["Lead with the instalment plan"]}
        return {}

    async def answer(self, *a, **k):
        return {}

    async def profile(self, brief, gender="female", name=""):
        role = brief.split(":")[0]
        return {"name": "Mia" if "quality" in role.lower() else "Sam", "role": role, "personality": "Calm.", "persona": "Short sentences.", "bio": "Careful."}

    def agent(self):
        return {**agents.get(self.aid), "_meeting_id": self.mid}

    def sheet(self, title, cols, rows, folder="Spreadsheets"):
        fid = files.folder_id(self.aid, folder)
        return db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (self.aid, title, "sheet", json.dumps({"columns": cols, "rows": rows}), fid, db.now(), db.now()))

    def doc(self, title, body):
        return db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (self.aid, title, "doc", body, files.folder_id(self.aid, "From admins"), db.now(), db.now()))

    async def start(self, **a):
        out, _ = await missions.run_tool(self.agent(), "start_mission", {"confirmed": True, **a})
        return out

    async def mission(self, **a):
        out, _ = await missions.run_tool(self.agent(), "mission", a)
        return out

    async def tick(self):
        missions._last.clear()
        await missions.tick()

    def finish_call(self, call_id, outcome="interested", speaks=True, status="completed", ago=60, summary="They asked about the fee."):
        t = db.now() - ago
        transcript = [{"role": "agent", "text": "Hi, is this you?", "t": 1}] + ([{"role": "customer", "text": "Yes, tell me more.", "t": 5}] if speaks else [])
        db.update("calls", call_id, status=status, outcome=outcome if status == "completed" else None, transcript=json.dumps(transcript), answered_at=t, ended_at=t + 40, ringing_at=t - 5, duration=40, summary=summary,
                  report=json.dumps({"objective_answer": "Wants the details", "objections_or_concerns": ["Price"], "unanswered_questions": ["Payment plan?"]}))

    def calls(self, **where):
        w = " AND ".join(f"{k}=?" for k in where) or "1=1"
        return db.q(f"SELECT * FROM calls WHERE {w} ORDER BY id", tuple(where.values()))

    async def launched(self, n=60, **a):
        self.sheet("Leads", ["Name", "Phone", "Email"], lead_rows(n))
        self.doc("Instructions", INSTRUCTIONS)
        out = await self.start(leads=["Leads"], instructions="Instructions", **a)
        self.assertNotIn("error", out, out)
        return out


class HandOverTests(MissionBase):
    async def test_a_hand_over_becomes_a_running_project_with_a_pilot(self):
        with db.use(self.ws):
            out = await self.launched()
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            self.assertEqual((m["status"], m["phase"]), ("running", "pilot"))
            self.assertEqual(m["callable_leads"], 60)  # 60 real people: 3 numbers that cannot be called set aside, 2 duplicates merged
            self.assertEqual(m["pilot_size"], 3)
            # the project and its papers
            proj = db.q1("SELECT * FROM projects WHERE id=?", (m["project_id"],))
            self.assertEqual(proj["name"], "Mission: Business diploma outreach")
            self.assertEqual(proj["goal"], self.charter["objective"])
            brief = db.q1("SELECT * FROM documents WHERE id=?", (m["brief_doc_id"],))
            self.assertIn("950 pounds", brief["body"])
            self.assertTrue(db.q1("SELECT 1 x FROM documents WHERE title LIKE '%Charter' AND agent_id=?", (-m["project_id"],)))
            # the tidied list, and the leads in it
            work = db.q1("SELECT * FROM documents WHERE id=?", (m["work_doc_id"],))
            t = json.loads(work["body"])
            self.assertEqual(len(t["rows"]), 63)  # 60 + 3 not callable; duplicates merged
            self.assertEqual(t["rows"][0][:2], ["Lead Number0", "+447700900000"])
            self.assertIn("Needs checking", t["columns"])
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM contacts WHERE dataset_id=?", (m["dataset_id"],))["n"], 63)
            # the knowledge for the callers, and the campaign
            self.assertTrue(db.q1("SELECT 1 x FROM kb_docs WHERE kb_id=?", (m["kb_id"],)))
            kb_name = db.q1("SELECT name FROM knowledge_bases WHERE id=?", (m["kb_id"],))["name"]
            self.assertNotIn("diploma", kb_name.lower())                      # the caller says the knowledge base's name as the company it rings from: never the project's
            self.assertEqual(kb_name, llm.company_name())
            c = db.q1("SELECT * FROM campaigns WHERE id=?", (m["campaign_id"],))
            self.assertEqual((c["objective"], c["max_attempts"], c["retry_delay_min"], c["concurrency"], c["agent_id"], c["kb_id"]), (self.charter["objective"], 2, 120, 20, self.aid, m["kb_id"]))
            self.assertEqual(json.loads(c["call_window"]), {"start": "10:00", "end": "18:00", "days": [0, 1, 2, 3, 4]})
            self.assertEqual((c["after_sms"], c["after_sms_when"]), (self.charter["after_text"], "interested"))
            self.assertEqual(c["status"], "active")
            self.assertEqual(len(self.calls(campaign_id=c["id"], status="queued")), 3)  # only the pilot is queued
            self.assertEqual(out["pilot"], 3)

    async def test_the_team_is_hired_organised_and_given_standing_work(self):
        with db.use(self.ws):
            out = await self.launched()
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            names = {t["name"]: t for t in json.loads(m["team"])}
            self.assertEqual(set(names), {"Mia", "Sam"})
            self.assertTrue(all(t["hired"] for t in names.values()))
            for t in names.values():
                ag = db.q1("SELECT * FROM agents WHERE id=?", (t["agent_id"],))
                self.assertEqual(ag["manager_id"], self.aid)  # they report to the owner
                self.assertTrue(db.q1("SELECT 1 x FROM project_members WHERE project_id=? AND agent_id=?", (m["project_id"], t["agent_id"])))
            owner_duty = db.q1("SELECT * FROM duties WHERE agent_id=? AND title LIKE 'Mission check-in%'", (self.aid,))
            self.assertEqual((owner_duty["recur"], owner_duty["project_id"]), ("daily", m["project_id"]))
            mia = db.q1("SELECT id FROM agents WHERE name='Mia'")["id"]
            duty = db.q1("SELECT * FROM duties WHERE agent_id=?", (mia,))
            self.assertIn("Call quality reviewer", duty["title"])
            self.assertEqual(duty["recur"], "daily")
            self.assertIn("mission", duty["detail"].lower())

    async def test_an_existing_colleague_is_used_before_anyone_is_hired(self):
        with db.use(self.ws):
            before = db.q1("SELECT COUNT(*) n FROM agents")["n"]
            existing = agents.create("Priya", "female", "Call quality reviewer", "", "", bio="")
            out = await self.launched()
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            team = json.loads(m["team"])
            self.assertEqual([t["hired"] for t in team], [False, True])
            self.assertEqual(team[0]["agent_id"], existing)
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM agents")["n"], before + 2)  # Priya was already there, plus one hire

    async def test_hiring_can_be_capped(self):
        with db.use(self.ws):
            out = await self.launched(max_hires=0)
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            self.assertEqual(json.loads(m["team"]), [])

    async def test_the_launch_report_says_what_is_running(self):
        with db.use(self.ws):
            await self.launched()
            text = self.chat_text(self.mid)[-1]
            for needle in ("Mission 'Business diploma outreach': launched", "Leads: 60 callable of 63", "Objective: Tell them about the Level 5", "Calling hours: 10:00–18:00",
                           "tried up to 2 times", "60 rows" if False else "Lead list: 65 rows → 60 callable", "Pilot: 3 calls first", "Assumptions I made:", "Team: Ethan (owner), Mia (Call quality reviewer), Sam (Follow-up specialist)",
                           "/export.xlsx", "Next report in 3 hours", "Live report document"):
                self.assertIn(needle, text)
            pid = db.q1("SELECT project_id FROM missions")["project_id"]
            self.assertTrue(db.q1("SELECT 1 x FROM project_updates WHERE project_id=?", (pid,)))
            live = db.q1("SELECT body FROM documents WHERE title LIKE '%Live report'")
            self.assertIn("launched", live["body"])

    async def test_the_launch_report_says_whose_clock_the_calling_hours_follow(self):
        with db.use(self.ws):
            voices.set_setting("timezone", "Europe/London")
            await self.launched(30)
            self.assertIn("Calling hours: 10:00–18:00 (Europe/London)", self.chat_text(self.mid)[-1])
        missions._last.clear()

    async def test_without_a_company_time_zone_the_report_warns_that_the_hours_run_on_the_server_clock(self):
        with db.use(self.ws):
            voices.set_setting("timezone", "")
            await self.launched(30)
            self.assertIn("the server's clock (UTC): set the company time zone in Settings → Company", self.chat_text(self.mid)[-1])

    async def test_without_names_it_uses_what_the_admin_shared_and_pasted_in_the_chat(self):
        with db.use(self.ws):
            sheet = self.sheet("Leads", ["Name", "Phone", "Email"], lead_rows(40))
            agents.log_line(self.mid, "admin", None, "Here is the brief:\n\n" + INSTRUCTIONS)
            agents.log_line(self.mid, "admin", None, "Call them all, I am away until tomorrow.")
            db.run("UPDATE meeting_msgs SET attachments=? WHERE id=(SELECT MAX(id) FROM meeting_msgs WHERE role='admin')", (json.dumps([{"doc_id": sheet, "title": "Leads", "kind": "sheet"}]),))
            out = await self.start()
            self.assertNotIn("error", out, out)
            brief = db.q1("SELECT body FROM documents WHERE id=(SELECT brief_doc_id FROM missions)")["body"]
            self.assertIn("950 pounds", brief)
            self.assertIn("I am away until tomorrow", brief)
            self.assertEqual(db.q1("SELECT leads_doc_id FROM missions")["leads_doc_id"], sheet)

    async def test_two_lead_sheets_become_one_list_with_the_people_in_both_called_once(self):
        with db.use(self.ws):
            self.sheet("Webinar sign-ups", ["Student", "Mobile", "E-mail"], [[f"Person {i}", f"07700 9{i:05d}", f"p{i}@x.com"] for i in range(0, 30)])
            self.sheet("Open day", ["Name", "Phone", "Email"], [[f"person {i}", f"+44 7700 9{i:05d}", ""] for i in range(20, 50)])   # 10 people are on both lists
            self.doc("Instructions", INSTRUCTIONS)
            out = await self.start(leads=["Webinar sign-ups", "Open day"], instructions="Instructions")
            self.assertNotIn("error", out, out)
            m = db.q1("SELECT * FROM missions")
            self.assertEqual(m["callable_leads"], 50)                                       # 30 + 30 - 10 on both
            t = json.loads(db.q1("SELECT body FROM documents WHERE id=?", (m["work_doc_id"],))["body"])
            self.assertIn("Source", t["columns"])
            self.assertEqual(len(t["rows"]), 50)
            both = [r for r in t["rows"] if "+" in r[t["columns"].index("Source")]]
            self.assertEqual(len(both), 10)
            self.assertEqual(db.q1("SELECT leads_doc_id FROM missions")["leads_doc_id"], db.q1("SELECT id FROM documents WHERE title='Webinar sign-ups'")["id"])

    async def test_the_brief_itself_may_be_passed_as_the_instructions_argument(self):
        """Regression, found by the live evaluation: the model passed the pasted brief where a document title was expected, and the tool refused."""
        with db.use(self.ws):
            self.sheet("Leads", ["Name", "Phone", "Email"], lead_rows(30))
            out = await self.start(leads=["Leads"], instructions=INSTRUCTIONS)               # the whole brief, not the name of a document
            self.assertNotIn("error", out, out)
            self.assertIn("950 pounds", db.q1("SELECT body FROM documents WHERE id=(SELECT brief_doc_id FROM missions)")["body"])
            self.sheet("Leads 2", ["Name", "Phone", "Email"], lead_rows(30))
            out = await self.start(leads=["Leads 2"], instructions="Call everyone about the diploma, the fee is 950 pounds, weekdays between 10 and 18 only please")   # a sentence
            self.assertNotIn("error", out, out)

    async def test_a_failed_start_says_plainly_that_nothing_started(self):
        with db.use(self.ws):
            self.sheet("Leads", ["Name", "Phone", "Email"], lead_rows(30))
            out = await self.start(leads=["Leads"], instructions="Instructions doc")        # a short title of a document that is not there
            self.assertIn("No accessible file named 'Instructions doc'", out["error"])
            self.assertTrue(out["nothing_started"])
            self.assertIn("did NOT start", out["next"])
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM missions")["n"], 0)

    async def test_a_second_hand_over_of_the_same_list_does_not_start_a_second_mission(self):
        with db.use(self.ws):
            first = await self.launched()
            again = await self.start(leads=["Leads"], instructions="Instructions")
            self.assertTrue(again["already_running"])
            self.assertEqual(again["mission_id"], first["mission_id"])
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM missions")["n"], 1)
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM campaigns")["n"], 1)

    async def test_a_big_list_needs_the_admins_request_a_small_one_does_not_and_nothing_is_created_before_that(self):
        with db.use(self.ws):
            self.sheet("Leads", ["Name", "Phone", "Email"], lead_rows(60))
            self.doc("Instructions", INSTRUCTIONS)
            out, _ = await missions.run_tool(self.agent(), "start_mission", {"leads": ["Leads"], "instructions": "Instructions"})
            self.assertTrue(out["needs_confirmation"])
            self.assertEqual(out["callable_leads"], 60)
            for table in ("missions", "projects", "campaigns", "datasets"):
                self.assertEqual(db.q1(f"SELECT COUNT(*) n FROM {table}")["n"], 0, table)
            self.sheet("Few", ["Name", "Phone"], [[f"P{i}", f"07700 9{i:05d}"] for i in range(5)])
            out, _ = await missions.run_tool(self.agent(), "start_mission", {"leads": ["Few"], "instructions": "Instructions"})
            self.assertNotIn("error", out)
            self.assertEqual(db.q1("SELECT phase FROM missions")["phase"], "run")   # five leads: no pilot, just call them
            self.assertEqual(len(self.calls(status="queued")), 5)

    async def test_open_questions_are_asked_unless_the_admin_is_away(self):
        self.charter["blocking_questions"] = ["Which course is this about?"]
        with db.use(self.ws):
            self.sheet("Leads", ["Name", "Phone", "Email"], lead_rows(30))
            self.doc("Instructions", INSTRUCTIONS)
            out = await self.start(leads=["Leads"], instructions="Instructions")
            self.assertEqual(out["needs_answers"], ["Which course is this about?"])
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM projects")["n"], 0)
            out = await self.start(leads=["Leads"], instructions="Instructions", proceed_with_assumptions=True)
            self.assertNotIn("needs_answers", out)
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM missions")["n"], 1)

    async def test_if_the_model_cannot_write_the_charter_the_job_still_starts_on_sensible_defaults(self):
        with db.use(self.ws), mock.patch.object(missions.llm, "complete_json", new=mock.AsyncMock(side_effect=RuntimeError("model down"))):
            out = await self.launched(30)
            c = json.loads(db.q1("SELECT charter FROM missions")["charter"])
            self.assertFalse(c["drafted_by_model"])
            self.assertIn("Level 5 Business Management", c["call_instructions"])
            self.assertEqual((c["max_attempts"], c["concurrency"]), (2, 10))
            self.assertEqual(out["team"], [])

    async def test_bad_hand_overs_are_explained(self):
        with db.use(self.ws):
            err = (await self.start())["error"]
            self.assertIn("I need the instructions", err)
            agents.log_line(self.mid, "admin", None, INSTRUCTIONS)
            self.assertIn("cannot see a lead sheet", (await self.start())["error"])
            self.sheet("NoPhone", ["Name", "City"], [["Ann", "Leeds"]])
            self.assertIn("could not tell which column holds the phone number", (await self.start(leads=["NoPhone"]))["error"])
            self.sheet("BadPhones", ["Name", "Phone"], [["Ann", "x"], ["Bo", "y"]])
            self.assertIn("None of the 2 rows has a phone number", (await self.start(leads=["BadPhones"]))["error"])
            voices.set_setting("default_calling_code", "")
            self.sheet("Local", ["Name", "Phone"], [["Ann", "07700 900000"], ["Bo", "07700 900001"]])
            self.assertIn("no country code is set", (await self.start(leads=["Local"]))["error"])   # the real reason, not just "no valid numbers"
            self.assertIn("No accessible file named 'Ghost'", (await self.start(leads=["Ghost"]))["error"])
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM missions")["n"], 0)


class PilotAndRunTests(MissionBase):
    async def pilot_done(self, out, **kw):
        m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
        for c in self.calls(campaign_id=m["campaign_id"]):
            self.finish_call(c["id"], **kw)
        callengine._check_campaign_done(m["campaign_id"])
        return m

    async def test_a_healthy_pilot_ramps_up_to_the_whole_list_by_itself(self):
        with db.use(self.ws):
            out = await self.launched()
            m = await self.pilot_done(out)
            await self.tick()
            m = db.q1("SELECT * FROM missions WHERE id=?", (m["id"],))
            self.assertEqual((m["status"], m["phase"]), ("running", "run"))
            self.assertEqual(len(self.calls(campaign_id=m["campaign_id"], status="queued")), 57)   # 60 callable minus the 3 already spoken to
            text = self.chat_text(self.mid)[-1]
            self.assertIn("The pilot looks healthy: 3 calls, 3 conversations", text)
            self.assertIn("Working well: Short and friendly", text)
            self.assertIn("Calling the rest of the list now: 57 more calls queued", text)

    async def test_a_pilot_with_serious_problems_holds_the_full_run_and_says_why(self):
        self.qa = {"verdict": "severe", "issues": ["The caller promised a job placement", "Quoted a fee of 500 pounds"], "good": []}
        with db.use(self.ws), mock.patch("app.alerts.send") as notify:
            out = await self.launched()
            await self.pilot_done(out)
            await self.tick()
            m = db.q1("SELECT * FROM missions")
            self.assertEqual(m["status"], "held")
            self.assertIn("promised a job placement", m["hold_reason"])
            self.assertEqual(len(self.calls(campaign_id=m["campaign_id"], status="queued")), 0)   # nothing more is dialed
            text = self.chat_text(self.mid)[-1]
            self.assertIn("PROBLEM on mission", text)
            self.assertIn("on hold", text)
            notify.assert_called()
            self.assertEqual(notify.call_args.args[0], "failure")                                  # the admin is told through their alert channels
            duty = db.q1("SELECT * FROM duties WHERE agent_id=? AND title LIKE 'URGENT%'", (self.aid,))
            self.assertEqual(duty["priority"], "high")
            await self.tick()
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM duties WHERE title LIKE 'URGENT%'")["n"], 1)   # one task, not one every half minute
            # fixing the script and resuming lifts the hold
            await self.mission(action="adjust", instructions="Fee is 950 pounds. Never promise a job.")
            res = await self.mission(action="resume")
            self.assertTrue(res["resumed"])
            self.assertEqual(db.q1("SELECT status, phase FROM missions")["phase"], "run")
            self.assertEqual(len(self.calls(campaign_id=m["campaign_id"], status="queued")), 57)
            self.assertIn("Resolved on mission", self.chat_text(self.mid)[-2])

    async def test_a_pilot_where_the_calls_fail_points_at_the_line_not_the_script(self):
        with db.use(self.ws):
            out = await self.launched()
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            for c in self.calls(campaign_id=m["campaign_id"]):
                self.finish_call(c["id"], status="failed", speaks=False, summary="Twilio 21215: geographic permission")
            callengine._check_campaign_done(m["campaign_id"])
            await self.tick()
            m = db.q1("SELECT * FROM missions")
            self.assertEqual(m["status"], "held")
            self.assertIn("3 of the 3 pilot calls failed", m["hold_reason"])
            self.assertIn("geographic permission", m["hold_reason"])
            self.assertIn("phone line or account", m["hold_reason"])

    async def test_a_pilot_nobody_answers_is_not_a_reason_to_stop(self):
        with db.use(self.ws):
            out = await self.launched()
            await self.pilot_done(out, status="no_answer", speaks=False, outcome=None)
            await self.tick()
            self.assertEqual(db.q1("SELECT phase FROM missions")["phase"], "run")

    async def test_the_gate_waits_while_the_pilot_calls_are_still_going(self):
        with db.use(self.ws):
            out = await self.launched()
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            first = self.calls(campaign_id=m["campaign_id"])[0]
            self.finish_call(first["id"])
            await self.tick()
            self.assertEqual(db.q1("SELECT phase FROM missions")["phase"], "pilot")

    async def test_progress_is_reported_on_schedule_with_real_numbers(self):
        with db.use(self.ws):
            out = await self.launched()
            await self.pilot_done(out)
            await self.tick()                                       # ramp
            m = db.q1("SELECT * FROM missions")
            first = self.chat_text(self.mid)
            await self.tick()
            self.assertEqual(self.chat_text(self.mid), first)       # nothing new: the next report is hours away
            db.update("missions", m["id"], last_report_at=db.now() - 4 * 3600)
            await self.tick()
            report = self.chat_text(self.mid)[-1]
            for needle in ("progress report", "State: running · full run", "Leads: 60 callable of 63", "conversations 3", "Outcomes: interested 3", "waiting 57 first calls", "AI spend since the start",
                           "Problems: none right now", "Team: Ethan (owner)"):
                self.assertIn(needle, report)
            self.assertIn("progress report", db.q1("SELECT body FROM documents WHERE title LIKE '%Live report'")["body"])
            self.assertGreater(db.q1("SELECT last_report_at FROM missions")["last_report_at"], db.now() - 60)

    async def test_the_statistics_are_right(self):
        with db.use(self.ws):
            out = await self.launched()
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            calls = self.calls(campaign_id=m["campaign_id"])
            self.finish_call(calls[0]["id"], outcome="interested")
            self.finish_call(calls[1]["id"], outcome="not_interested")
            self.finish_call(calls[2]["id"], status="no_answer", speaks=False)
            s = missions.stats(db.q1("SELECT * FROM missions"))
            self.assertEqual((s["leads"], s["callable"], s["reached"]), (63, 60, 2))
            self.assertEqual(s["outcomes"], {"interested": 1, "not_interested": 1})
            self.assertEqual(s["by_status"], {"completed": 2, "no_answer": 1})
            self.assertEqual(s["attempts"], 3)
            self.assertEqual(s["avg_minutes"], 0.7)

    async def test_the_whole_list_called_ends_in_a_final_report_a_closed_project_and_a_review_task(self):
        with db.use(self.ws):
            out = await self.launched()
            await self.pilot_done(out)
            await self.tick()                                       # ramp
            m = db.q1("SELECT * FROM missions")
            for c in self.calls(campaign_id=m["campaign_id"], status="queued"):
                self.finish_call(c["id"], outcome="callback_requested" if c["id"] % 2 else "not_interested")
            callengine._check_campaign_done(m["campaign_id"])
            self.assertEqual(db.q1("SELECT status FROM campaigns")["status"], "completed")
            await self.tick()
            m = db.q1("SELECT * FROM missions")
            self.assertEqual((m["status"], m["phase"]), ("done", "closed"))
            self.assertEqual(db.q1("SELECT status FROM projects WHERE id=?", (m["project_id"],))["status"], "done")
            final = self.chat_text(self.mid)[-1]
            for needle in ("FINAL REPORT", "State: done · finished", "conversations 60", "What put people off: Price too high", "What I would change next time: Lead with the instalment plan", "/export.xlsx"):
                self.assertIn(needle, final)
            self.assertTrue(db.q1("SELECT 1 x FROM duties WHERE agent_id=? AND title LIKE 'Final review%' AND priority='high'", (self.aid,)))
            await self.tick()
            self.assertEqual(self.chat_text(self.mid)[-1], final)   # said once


class QuietAndNotifyTests(MissionBase):
    def sent(self, kind):
        return [c.args for c in self.alert.call_args_list if c.args[0] == kind]

    async def test_the_admin_is_told_where_they_are_when_it_launches_passes_its_pilot_and_finishes(self):
        with db.use(self.ws):
            out = await self.launched()
            launch = self.sent("mission")
            self.assertEqual(len(launch), 1)
            self.assertIn("Mission 'Business diploma outreach': launched", launch[0][1])
            self.assertIn("Leads: 60 callable of 63", launch[0][2])
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            for c in self.calls(campaign_id=m["campaign_id"]):
                self.finish_call(c["id"])
            callengine._check_campaign_done(m["campaign_id"])
            await self.tick()
            self.assertIn("passed its pilot", self.sent("mission")[-1][1])
            for c in self.calls(campaign_id=m["campaign_id"], status="queued"):
                self.finish_call(c["id"], outcome="interested")
            callengine._check_campaign_done(m["campaign_id"])
            await self.tick()
            last = self.sent("mission")[-1]
            self.assertIn("finished", last[1])
            self.assertIn("FINAL REPORT", last[2])
            self.assertIn("mission", alerts.KINDS)       # and it shows as a switch in Settings → Alerts

    async def test_when_nothing_has_happened_since_the_last_report_it_stays_quiet_with_one_note_a_day(self):
        with db.use(self.ws):
            out = await self.launched()
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            for c in self.calls(campaign_id=m["campaign_id"]):
                self.finish_call(c["id"])
            callengine._check_campaign_done(m["campaign_id"])
            await self.tick()                                                                # ramp
            db.update("missions", m["id"], last_report_at=db.now() - 4 * 3600)
            await self.tick()
            self.assertIn("progress report", self.chat_text(self.mid)[-1])                   # something changed since the launch: reported
            posted = len(self.chat_text(self.mid))
            db.update("calls", self.calls(campaign_id=m["campaign_id"], status="queued")[0]["id"], not_before=db.now() + 10 ** 6)
            for c in self.calls(campaign_id=m["campaign_id"], status="queued"):
                db.update("calls", c["id"], not_before=db.now() + 10 ** 6)                   # the night: nothing is due
            db.update("missions", m["id"], last_report_at=db.now() - 4 * 3600)
            await self.tick()
            self.assertEqual(len(self.chat_text(self.mid)), posted)                          # nothing new, so nothing said
            self.assertGreater(db.q1("SELECT last_report_at FROM missions")["last_report_at"], db.now() - 60)
            quiet_since = json.loads(db.q1("SELECT last_stats FROM missions")["last_stats"])["_quiet_since"]
            db.update("missions", m["id"], last_report_at=db.now() - 4 * 3600,
                      last_stats=json.dumps({**json.loads(db.q1("SELECT last_stats FROM missions")["last_stats"]), "_quiet_since": quiet_since - 90000}))
            await self.tick()
            self.assertEqual(len(self.chat_text(self.mid)), posted + 1)                      # a day on: one "still alive" report

    async def test_the_launch_report_warns_about_a_missing_link_and_about_alerts_that_only_reach_the_app(self):
        with db.use(self.ws):
            voices.set_setting("main_link", "")
            await self.launched(30)
            text = self.chat_text(self.mid)[-1]
            self.assertIn("HEADS UP: the follow-up text has a link in it, but no main link is set", text)
            self.assertIn("HEADS UP: alerts reach you only inside this app", text)

    async def test_no_warnings_when_the_link_and_a_way_to_reach_the_admin_are_set(self):
        with db.use(self.ws):
            voices.set_setting("main_link", "https://aacollege.example/courses")
            alerts._put("notify_slack", "sealed-webhook")
            await self.launched(30)
            text = self.chat_text(self.mid)[-1]
            self.assertNotIn("HEADS UP", text)


class WatchTests(MissionBase):
    async def running(self):
        out = await self.launched(40)
        m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
        for c in self.calls(campaign_id=m["campaign_id"]):
            self.finish_call(c["id"])
        callengine._check_campaign_done(m["campaign_id"])
        await self.tick()                                           # ramp
        return db.q1("SELECT * FROM missions WHERE id=?", (m["id"],))

    async def test_calls_that_are_due_but_never_start_raise_an_alarm_once_and_clear_when_they_do(self):
        with db.use(self.ws), mock.patch("app.alerts.send") as notify, mock.patch("app.schedule.in_window", return_value=True):
            m = await self.running()
            db.update("campaigns", m["campaign_id"], started_at=db.now() - 3600)
            db.run("UPDATE calls SET ended_at=?, answered_at=NULL, ringing_at=NULL WHERE campaign_id=? AND status='completed'", (db.now() - 3600, m["campaign_id"]))
            self.problem = ""
            await self.tick()
            text = self.chat_text(self.mid)[-1]
            self.assertIn("calls are due but none has started for 15 minutes", text)
            self.assertIn("calls are due now and nothing is ringing", text)
            self.assertEqual(len([c for c in notify.call_args_list if c.args[0] == "failure"]), 1)
            before = len(self.chat_text(self.mid))
            await self.tick()
            self.assertEqual(len(self.chat_text(self.mid)), before)           # not repeated every half minute
            first = self.calls(campaign_id=m["campaign_id"], status="queued")[0]
            self.finish_call(first["id"], ago=10)                              # a call went through
            await self.tick()
            self.assertIn("Resolved on mission", self.chat_text(self.mid)[-1])

    async def test_the_cause_is_named_when_it_is_known(self):
        with db.use(self.ws), mock.patch("app.alerts.send"), mock.patch("app.schedule.in_window", return_value=True):
            m = await self.running()
            db.update("campaigns", m["campaign_id"], started_at=db.now() - 3600)
            db.run("UPDATE calls SET ended_at=? WHERE campaign_id=? AND status='completed'", (db.now() - 3600, m["campaign_id"]))
            self.problem = "Connect your Twilio account in Apps."
            await self.tick()
            self.assertIn("Cause found: Connect your Twilio account in Apps.", self.chat_text(self.mid)[-1])

    async def test_many_failing_calls_raise_an_alarm_with_the_reasons(self):
        with db.use(self.ws), mock.patch("app.alerts.send") as notify:
            m = await self.running()
            queued = self.calls(campaign_id=m["campaign_id"], status="queued")[:12]
            for i, c in enumerate(queued):
                self.finish_call(c["id"], status="failed", speaks=False, ago=60 + i, summary="Twilio 30003: unreachable")
            await self.tick()
            text = self.chat_text(self.mid)[-1]
            self.assertIn("12 of the last 15 calls failed", text)   # the three earlier calls went fine
            self.assertIn("12× Twilio 30003: unreachable", text)
            notify.assert_called()
            self.assertTrue(db.q1("SELECT 1 x FROM duties WHERE agent_id=? AND title LIKE 'URGENT mission problem: 12 of the last 15 calls failed%' AND priority='high'", (self.aid,)))

    async def test_a_mission_that_cannot_dial_waits_and_then_starts_by_itself_when_the_cause_is_fixed(self):
        self.problem = "Connect an OpenAI API key in Apps; the call needs it."
        with db.use(self.ws), mock.patch("app.alerts.send"):
            out = await self.launched(30)
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            self.assertEqual(m["status"], "blocked")
            self.assertEqual(len(self.calls(campaign_id=m["campaign_id"])), 0)
            text = self.chat_text(self.mid)[-1]
            self.assertIn("NOT DIALING YET: Connect an OpenAI API key", text)
            self.assertIn("It starts by itself as soon as that is fixed", text)
            await self.tick()
            self.assertEqual(db.q1("SELECT status FROM missions")["status"], "blocked")   # still not possible
            self.problem = ""
            await self.tick()
            m = db.q1("SELECT * FROM missions")
            self.assertEqual((m["status"], m["phase"]), ("running", "pilot"))
            self.assertEqual(len(self.calls(campaign_id=m["campaign_id"], status="queued")), 3)
            self.assertIn("Calling works now, so the mission has started", self.chat_text(self.mid)[-1])

    async def test_a_campaign_stopped_by_someone_pauses_the_mission(self):
        with db.use(self.ws):
            m = await self.running()
            db.update("campaigns", m["campaign_id"], status="stopped")
            await self.tick()
            self.assertEqual(db.q1("SELECT status FROM missions")["status"], "paused")
            self.assertIn("was stopped by someone", self.chat_text(self.mid)[-1])


class ControlTests(MissionBase):
    async def test_status_gives_the_numbers_the_team_and_the_problems(self):
        with db.use(self.ws):
            await self.launched()
            s = await self.mission(action="status")
            self.assertEqual((s["name"], s["status"], s["phase"], s["owner"]), ("Business diploma outreach", "running", "pilot", "Ethan"))
            self.assertEqual(s["numbers"]["callable"], 60)
            self.assertEqual({t["name"] for t in s["team"]}, {"Mia", "Sam"})
            self.assertEqual(s["open_problems"], [])
            self.assertIn("State: running · pilot calls first", s["report"])
            self.assertTrue(s["results_workbook"].endswith("export.xlsx"))
            listing = await self.mission(action="list")
            self.assertEqual(len(listing["missions"]), 1)
            self.assertIn("no mission yet" if False else "There is no mission with that number", (await self.mission(action="status", mission_id=999))["error"])

    async def test_pause_keeps_every_queued_call_and_resume_dials_them_again(self):
        with db.use(self.ws):
            out = await self.launched()
            queued = len(self.calls(status="queued"))
            res = await self.mission(action="pause")
            self.assertTrue(res["paused"])
            camp = db.q1("SELECT * FROM campaigns")
            self.assertEqual(camp["status"], "stopped")
            self.assertEqual(len(self.calls(status="queued")), queued)               # none cancelled
            self.assertIsNone(callengine.next_due())                                  # and the scheduler dials nothing
            await self.tick()
            self.assertEqual(db.q1("SELECT status FROM missions")["status"], "paused")
            res = await self.mission(action="resume")
            self.assertEqual(res["waiting_calls"], queued)
            self.assertEqual(db.q1("SELECT status FROM campaigns")["status"], "active")
            self.assertEqual(db.q1("SELECT status FROM missions")["status"], "running")
            self.assertIn("resumed by Ethan", self.chat_text(self.mid)[-1])
            self.assertEqual(out["mission_id"], db.q1("SELECT id FROM missions")["id"])

    async def test_adjust_changes_what_the_next_calls_use_and_is_recorded(self):
        with db.use(self.ws):
            await self.launched()
            res = await self.mission(action="adjust", instructions="Lead with the instalments.", concurrency=40, max_attempts=3, retry_delay_minutes=60, after_sms_when="answered",
                                     call_hours={"start": "09:30", "end": "17:00", "days": [0, 1, 2, 3, 4]}, report_every_hours=1)
            self.assertEqual(set(res["changed"]), {"call instructions", "calling hours", "tries per lead", "calls at once", "retry gap", "who gets the text", "report schedule"})
            c = db.q1("SELECT * FROM campaigns")
            self.assertEqual((c["instructions"], c["concurrency"], c["max_attempts"], c["retry_delay_min"], c["after_sms_when"]), ("Lead with the instalments.", 40, 3, 60, "answered"))
            self.assertEqual(json.loads(c["call_window"])["start"], "09:30")
            self.assertEqual(db.q1("SELECT report_every FROM missions")["report_every"], 3600)
            self.assertIn("changed call instructions", self.chat_text(self.mid)[-1])
            self.assertIn("paused" if False else "changed", json.loads(db.q1("SELECT notes FROM missions")["notes"])[-1]["note"])
            self.assertIn("Say what to change", (await self.mission(action="adjust"))["error"])
            # the caller reads the new instructions at the start of its next call
            call = self.calls(status="queued")[0]
            row, contact, camp = callengine.call_context(call["id"])
            self.assertIn("Lead with the instalments.", callengine.system_prompt(row, contact, camp, realtime=True, external=True))

    async def test_only_the_owner_or_a_manager_may_change_it_but_anyone_may_read_it(self):
        with db.use(self.ws):
            await self.launched()
            other = agents.create("Zoe", "female", "Support", "", "", bio="")
            stranger = {**agents.get(other), "is_manager": 0}
            out, _ = await missions.run_tool(stranger, "mission", {"action": "pause"})
            self.assertIn("Ethan owns this mission", out["error"])
            out, _ = await missions.run_tool(stranger, "mission", {"action": "status"})
            self.assertEqual(out["status"], "running")
            boss = {**agents.get(other), "is_manager": 1}
            out, _ = await missions.run_tool(boss, "mission", {"action": "pause"})
            self.assertTrue(out["paused"])

    async def test_ramp_skips_the_rest_of_the_pilot_and_stop_ends_it_with_a_final_report(self):
        with db.use(self.ws):
            await self.launched()
            res = await self.mission(action="ramp")
            self.assertTrue(res["resumed"])
            self.assertEqual(db.q1("SELECT phase FROM missions")["phase"], "run")
            self.assertEqual(len(self.calls(status="queued")), 60)
            res = await self.mission(action="stop")
            self.assertTrue(res["stopped"])
            self.assertEqual(len(self.calls(status="queued")), 0)
            self.assertEqual(db.q1("SELECT status FROM missions")["status"], "stopped")
            self.assertIn("FINAL REPORT", self.chat_text(self.mid)[-1])

    async def test_the_admins_answers_are_added_to_what_the_callers_know(self):
        with db.use(self.ws):
            await self.launched()
            await self.mission(action="pause")
            await self.mission(action="resume", answers="The course starts on 5 January.")
            self.assertIn("From the admin: The course starts on 5 January.", db.q1("SELECT instructions FROM campaigns")["instructions"])


class ScaleTests(MissionBase):
    async def test_five_thousand_leads_are_cleaned_imported_and_watched_quickly(self):
        rows = [[f"Lead {i}", f"07700 {i + 100000:06d}", f"l{i}@x.com"] for i in range(5000)] + [["Dup", "07700 100000", ""]] * 5   # the same person five more times
        with db.use(self.ws):
            self.sheet("Big leads", ["Name", "Phone", "Email"], rows)
            self.doc("Instructions", INSTRUCTIONS)
            t = time.monotonic()
            out = await self.start(leads=["Big leads"], instructions="Instructions")
            took = time.monotonic() - t
            self.assertNotIn("error", out, out)
            m = db.q1("SELECT * FROM missions")
            self.assertEqual(m["callable_leads"], 5000)
            self.assertEqual(out["pilot"], 10)
            self.assertLess(took, 20)
            t = time.monotonic()
            await self.mission(action="ramp")
            self.assertEqual(len(self.calls(status="queued")), 5000)
            s = missions.stats(db.q1("SELECT * FROM missions"))
            await self.tick()
            self.assertLess(time.monotonic() - t, 5)
            self.assertEqual(s["first_calls_waiting"], 5000)


class RegistrationTests(MissionBase):
    async def test_the_tools_are_offered_and_every_employee_is_taught_to_take_ownership(self):
        names = [t["name"] for t in office.TOOLS]
        for n in ("start_mission", "mission"):
            self.assertEqual(names.count(n), 1, n)
        from harness.runtime import RunContext
        with db.use(self.ws):
            offered = {t.name for t in runtime.employees().tools_for(RunContext(agent={**agents.get(self.aid), "is_manager": 0}, kind="admin_chat"))}
            prompt = agents.meeting_prompt(agents.get(self.aid), [], "chat")
        self.assertTrue({"start_mission", "mission", "browser_click", "sheet_clean", "web_search"} <= offered)
        self.assertTrue({"start_mission", "mission"} <= set(next(g for g in capabilities.GROUPS if g[0] == "calls")[3]))
        for needle in ("OWNERSHIP: when the admin hands you a lead list and instructions", "call start_mission straight away with confirmed=true", "Do not ask permission to hire, clean, plan or start",
                       "when an URGENT mission task reaches you", "never from memory", "A mission is running only if start_mission returned a mission_id", "NOTHING started"):
            self.assertIn(needle, prompt)


if __name__ == "__main__":
    unittest.main()


class RealPathTests(MissionBase):
    """What really happens when the employee's model calls the tool: through the harness, with its policies, and then what a caller can look up."""

    async def test_the_tool_works_through_the_employee_harness_not_only_when_called_directly(self):
        with db.use(self.ws):
            self.sheet("Leads", ["Name", "Phone", "Email"], lead_rows(30))
            self.doc("Instructions", INSTRUCTIONS)
            out = await agents.run_agent_tool(self.agent(), "start_mission", {"leads": ["Leads"], "instructions": "Instructions", "confirmed": True})
            self.assertEqual(out[0]["status"] if isinstance(out, tuple) else out["status"], "running")
            status = await agents.run_agent_tool(self.agent(), "mission", {"action": "status"})
            status = status[0] if isinstance(status, tuple) else status
            self.assertEqual(status["name"], "Business diploma outreach")

    async def test_a_caller_on_a_mission_call_can_look_up_the_admins_instructions(self):
        with db.use(self.ws):
            out = await self.launched(30)
            m = db.q1("SELECT * FROM missions WHERE id=?", (out["mission_id"],))
            call = self.calls(campaign_id=m["campaign_id"])[0]
            hits = await knowledge.lookup_company("how much is the fee and can I pay monthly", call_id=call["id"])
            self.assertTrue(any("950 pounds" in h for h in hits), hits)
            row, contact, camp = callengine.call_context(call["id"])
            prompt = callengine.system_prompt(row, contact, camp, realtime=True, external=True)
            self.assertIn("Fee 950 pounds, monthly instalments.", prompt)        # the distilled script is in the brief
            self.assertIn("Tell them about the Level 5 Business Management diploma", prompt)

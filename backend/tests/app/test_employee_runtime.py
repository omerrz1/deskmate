"""The AI employees running on the harness: what they are offered, what is refused or pretended, what is written to their history."""
import json
import unittest
from unittest import mock

from app import admin_agent, agents, db, files, llm, runtime, team, workbench

from tests.harness.fakes import FakeClient, collect, say, use


def tool_results(events):
    return [e for e in events if e["type"] == "tool_result"]


def activity(agent_id):
    return [dict(r) for r in db.q("SELECT tool,app,ok,summary FROM agent_activity WHERE agent_id=? ORDER BY id", (agent_id,))]


class VerifiedTwilioReplyTests(unittest.TestCase):
    def test_rejects_unverified_claims_without_a_call_or_lookup_tool(self):
        reply = "The call was rejected again with error 20003, confirming invalid credentials. Notify the admin?"
        corrected = agents.verified_twilio_reply(reply, [])
        self.assertIn("can’t say it was rejected again", corrected)
        self.assertNotIn("Notify the admin?", corrected)

    def test_keeps_claim_after_a_call_or_call_record_lookup(self):
        reply = "The call was rejected again with error 20003."
        self.assertEqual(agents.verified_twilio_reply(reply, [{"name": "call_contact"}]), reply)
        self.assertEqual(agents.verified_twilio_reply(reply, [{"name": "get_call"}]), reply)

    def test_rejects_ringing_claim_without_call_action(self):
        reply = "The call to 07447781753 is ringing now."
        corrected = agents.verified_twilio_reply(reply, [{"name": "list_calls"}])
        self.assertIn("Twilio has not confirmed", corrected)
        unplaced = agents.verified_twilio_reply("The call has been placed.", [])
        self.assertIn("No new call was started", unplaced)

    def test_explicit_retry_resolves_the_recent_number_and_directive(self):
        history = [
            {"role": "admin", "text": "Please call 07447781753 and ask for any update."},
            {"role": "agent", "text": "Would you like me to try calling again or check something else?"},
        ]
        with mock.patch("app.messaging.default_code", return_value="44"):
            args = agents._explicit_call_args("yes try again!", history)
            complaint = agents._explicit_call_args("no call received", history)
        self.assertEqual(args, {"phone": "+447447781753", "objective": "Ask for any update in a friendly and polite manner."})
        self.assertIsNone(complaint)


class Base(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.ava = agents.create("Ava", "female", "Support", "Warm.", "", bio="")
        cls.ben = agents.create("Ben", "male", "Analyst", "Dry.", "", bio="")  # files switched off below
        cls.mia = agents.create("Mia", "female", "Ops lead", "Calm.", "", bio="")
        team.set_manager(cls.mia, True)
        db.update("agents", cls.ben, caps_off=json.dumps(["files"]))
        cls.meeting = agents.start_meeting([cls.ava, cls.ben], "Weekly")
        agents.log_line(cls.meeting, "admin", None, "Please prepare the quarterly summary after the meeting.")
        cls.urgent = agents.start_meeting([cls.ava, cls.mia], "Urgent")
        agents.log_line(cls.urgent, "admin", None, "Write the summary document right now please")

    def agent(self, aid):
        return agents.get(aid)

    async def play(self, agent, turns, kind="work", **kw):
        client = FakeClient(*turns)
        with mock.patch.object(llm, "client", lambda: client):
            events = await collect(agents.run_loop(self.agent(agent) if isinstance(agent, int) else agent, [{"role": "user", "content": "go"}], kind, **kw))
        return events, client


class MeetingCallExecutionTests(Base):
    async def test_accepted_call_retry_runs_in_the_meeting_process_and_reports_the_tool_result(self):
        mid = agents.start_meeting([self.ava], chat=True)
        agents.log_line(mid, "admin", None, "Please call 07447781753 and ask for any update.")
        agents.log_line(mid, "agent", self.ava, "Would you like me to try calling again?")
        with mock.patch.object(agents, "_responders", new_callable=mock.AsyncMock, return_value=[self.agent(self.ava)]), \
             mock.patch.object(agents, "run_agent_tool", new_callable=mock.AsyncMock,
                               return_value=({"call_id": 27, "state": "initiated", "phone": "+447447781753"}, "/calls")) as run_tool, \
             mock.patch("app.messaging.default_code", return_value="44"):
            events = await collect(agents.meeting_message(mid, "yes try again!"))
        run_tool.assert_awaited_once()
        self.assertEqual(run_tool.await_args.args[1], "call_contact")
        self.assertEqual(run_tool.await_args.args[2]["phone"], "+447447781753")
        self.assertTrue(any(e.get("type") == "tool_result" and e.get("ok") for e in events))
        saved = db.q1("SELECT text,tools FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id DESC LIMIT 1", (mid,))
        self.assertIn("can’t confirm", saved["text"])
        self.assertEqual(json.loads(saved["tools"])[0]["name"], "call_contact")


class WiringTests(Base):
    def test_the_harness_is_built_once_and_has_every_tool_in_the_documented_order(self):
        h = runtime.employees()
        self.assertIs(h, runtime.employees())
        names = h.registry.names()
        self.assertEqual(names[0], "get_dashboard_stats")  # the platform tools come first,
        self.assertEqual(names[-1], "browser_close")        # the browser tools last (so they are cut first when there are too many)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual({t.group for t in h.registry if t.requires == "manager"}, {"connectors", "messaging"})

    def test_what_the_model_is_offered_is_capped_and_filtered(self):
        ava, ben, mia = (agents.tools_for(self.agent(i)) for i in (self.ava, self.ben, self.mia))
        self.assertLessEqual(len(mia), runtime.MAX_TOOLS)
        self.assertNotIn("create_agent", [t["name"] for t in mia])  # a manager has hire_agent, so the older alias is hidden...
        self.assertIn("hire_agent", [t["name"] for t in mia])
        self.assertNotIn("create_agent", [t["name"] for t in ava])  # ...and it is a manager power, so nobody else is offered it either
        self.assertIn("list_documents", [t["name"] for t in ava])
        self.assertIn("get_call_report", [t["name"] for t in ava])
        self.assertNotIn("list_documents", [t["name"] for t in ben])  # files are switched off for Ben
        self.assertNotIn("list_connectors", [t["name"] for t in ava])  # manager powers
        self.assertIn("list_connectors", [t["name"] for t in mia])
        for employee_tools in (ava, ben, mia):
            names = {t["name"] for t in employee_tools}
            self.assertTrue({"send_sms", "send_whatsapp", "reply_chat", "ask_call_permission", "call_on_whatsapp"} <= names)

    def test_the_live_meeting_tool_list_is_everything_when_no_one_is_named(self):
        self.assertEqual(len(agents.realtime_tools(None)), len(runtime.employees().registry))
        sample = agents.realtime_tools(self.agent(self.ava))[0]
        self.assertEqual(sorted(sample), ["description", "name", "parameters", "type"])

    def test_the_copilot_is_a_harness_with_its_own_settings(self):
        c = admin_agent.COPILOT
        self.assertEqual(c.config.max_steps, 6)
        self.assertEqual(c.registry.names(), [t["name"] for t in admin_agent.TOOLS])
        self.assertEqual(c.policies.policies, [])

    async def test_employee_searches_spreadsheet_cells_and_reads_rows_in_pages(self):
        with db.use(db.current()):
            folder = files.folder_id(self.ava, "Spreadsheets")
            did = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (self.ava, "Autumn leads", "sheet", json.dumps({"columns": ["Name", "Note"], "rows": [["Alex", "needs a callback"], ["Sam", "send prospectus"]]}), folder, db.now(), db.now()))
        with db.use(db.current()):
            found, _ = await files.run_tool(agents.get(self.ava), "list_documents", {"query": "prospectus"})
            page, _ = await files.run_tool(agents.get(self.ava), "read_document", {"id": did, "offset": 1, "limit": 1})
        self.assertEqual(found[0]["id"], did)
        self.assertEqual(page["rows"], [["Sam", "send prospectus"]])
        self.assertFalse(page["has_more"])

    async def test_admin_agent_can_generate_and_retrieve_a_full_call_report(self):
        with db.use(db.current()):
            contact = db.run("INSERT INTO contacts(name,phone,created_at) VALUES(?,?,?)", ("Report Test", "+447700900111", db.now()))
            cid = db.run("INSERT INTO calls(contact_id,status,outcome,summary,transcript,created_at,duration) VALUES(?,?,?,?,?,?,?)",
                         (contact, "completed", "callback_requested", "Customer requested a callback.", json.dumps([{"role": "agent", "text": "Is tomorrow suitable?"}, {"role": "customer", "text": "Yes, please call after lunch."}]), db.now(), 42))
            with mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, return_value={"executive_summary": "Customer agreed to a callback."}) as generate:
                report, _ = await admin_agent.run_tool("get_call_report", {"call_id": cid})
            self.assertEqual(report["executive_summary"], "Customer agreed to a callback.")
            self.assertEqual(len(report["transcript"]), 2)
            stored = db.q1("SELECT report FROM calls WHERE id=?", (cid,))
            self.assertIn("Customer agreed", stored["report"])
            again, _ = await admin_agent.run_tool("get_call_report", {"call_id": cid})
            self.assertEqual(again["call_id"], cid)
            generate.assert_awaited_once()

    async def test_importing_a_saved_lead_sheet_twice_does_not_duplicate_leads(self):
        with db.use(db.current()):
            fid = files.folder_id(self.ava, "Spreadsheets")
            did = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (self.ava, "Warm leads", "sheet", json.dumps({"columns": ["Name", "Phone"], "rows": [["Lead", "+447700900222"]]}), fid, db.now(), db.now()))
            first, _ = await workbench.run_tool(agents.get(self.ava), "sheet_to_customers", {"id": did})
            second, _ = await workbench.run_tool(agents.get(self.ava), "sheet_to_customers", {"id": did})
            dataset_id = first["details"]["dataset_id"]
            self.assertEqual(first["imported"], second["imported"])
            self.assertTrue(second["details"]["already_imported"])
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM contacts WHERE dataset_id=?", (dataset_id,))["n"], 1)


class RuleTests(Base):
    async def test_a_switched_off_ability_is_refused_and_leaves_a_failed_step_in_the_history(self):
        before = len(activity(self.ben))
        events, client = await self.play(self.ben, [use("list_documents"), say("ok")], "one_to_one")
        self.assertFalse(tool_results(events)[0]["ok"])
        self.assertIn("switched off", client.requests[1]["messages"][-1]["content"])
        self.assertEqual([(a["tool"], a["ok"]) for a in activity(self.ben)[before:]], [("list_documents", 0)])

    async def test_managers_powers_belong_to_managers(self):
        events, _ = await self.play(self.ava, [use("list_connectors"), say("ok")])
        self.assertFalse(tool_results(events)[0]["ok"])
        events, _ = await self.play(self.mia, [use("list_connectors"), say("ok")])
        self.assertTrue(tool_results(events)[0]["ok"])
        r, _ = await agents.run_agent_tool({**self.agent(self.ava), "is_manager": 0}, "approve_message", {"id": 1})
        self.assertIn("error", r)

    async def test_explicit_after_meeting_instruction_defers_work_until_now(self):
        before = len(activity(self.ava))
        events, client = await self.play(self.ava, [use("create_document", '{"title": "Q", "body": "b"}'), say("ok")], "group", meeting_id=self.meeting)
        self.assertFalse(tool_results(events)[0]["ok"])
        self.assertIn("in a meeting", client.requests[1]["messages"][-1]["content"])
        self.assertEqual(len(activity(self.ava)), before)  # a refused gate leaves nothing in the history
        events, _ = await self.play(self.ava, [use("create_document", '{"title": "Q", "body": "b"}'), say("ok")], "group", meeting_id=self.urgent)
        self.assertTrue(tool_results(events)[0]["ok"])
        self.assertEqual([a["tool"] for a in activity(self.ava)[before:]], ["create_document"])

    async def test_light_tools_are_not_gated_in_a_meeting(self):
        events, _ = await self.play(self.ava, [use("remember", '{"note": "n"}'), say("ok")], "group", meeting_id=self.meeting)
        self.assertTrue(tool_results(events)[0]["ok"])

    async def test_a_customer_in_a_chat_cannot_make_the_employee_change_connected_apps(self):
        events, client = await self.play(self.ava, [use("use_app", '{"app": "mcp-1", "action": "create_thing"}'), say("ok")], "customer_chat")
        self.assertFalse(tool_results(events)[0]["ok"])
        self.assertIn("customer chat", client.requests[1]["messages"][-1]["content"])

    async def test_practice_runs_pretend_to_change_things_but_really_read(self):
        events, client = await self.play(self.ava, [use("create_document", '{"title": "T", "body": "b"}', cid="a"), use("remember", '{"note": "p"}', cid="b"), say("ok")], dry_run=True)
        self.assertEqual(len(tool_results(events)), 2)
        self.assertIn("practice_mode", client.requests[1]["messages"][-1]["content"])
        self.assertIn("saved", client.requests[2]["messages"][-1]["content"])

    async def test_the_gates_speak_before_the_ability_switch(self):
        """Kept from the original behaviour: a switched-off heavy tool in a practice run is pretended, in a meeting it hits the meeting rule."""
        events, client = await self.play(self.ben, [use("create_document", '{"title": "T", "body": "b"}'), say("ok")], dry_run=True)
        self.assertIn("practice_mode", client.requests[1]["messages"][-1]["content"])
        events, client = await self.play(self.ben, [use("create_document", '{"title": "T", "body": "b"}'), say("ok")], "group", meeting_id=self.meeting)
        self.assertIn("in a meeting", client.requests[1]["messages"][-1]["content"])

    async def test_deleting_things_needs_the_admin_in_the_conversation(self):
        victim = agents.create("Zed", "male", "Intern", "", "", bio="")
        call = use("remove_agent", json.dumps({"agent": "Zed"}))
        events, client = await self.play(self.mia, [call, say("ok")], "work")  # a task, not the admin
        self.assertIn("only happens when the admin tells you", client.requests[1]["messages"][-1]["content"])
        events, client = await self.play(self.mia, [call, say("ok")], "work", ordered_by_admin=True)
        self.assertIn("needs_confirmation", client.requests[1]["messages"][-1]["content"])  # allowed, and only previews
        events, client = await self.play(self.mia, [call, say("ok")], "one_to_one")
        self.assertIn("needs_confirmation", client.requests[1]["messages"][-1]["content"])
        self.assertIsNotNone(agents.get(victim))  # nothing was removed

    async def test_an_unknown_tool_is_an_error_not_a_crash(self):
        r, link = await agents.run_agent_tool(self.agent(self.ava), "no_such_tool", {})
        self.assertEqual(r, {"error": "unknown tool no_such_tool"})


class HistoryTests(Base):
    async def test_every_tool_an_employee_uses_is_written_to_their_history(self):
        before = len(activity(self.ava))
        await agents.run_agent_tool(self.agent(self.ava), "remember", {"note": "likes tea"})
        row = activity(self.ava)[before:]
        self.assertEqual([(r["tool"], r["app"], r["ok"], r["summary"]) for r in row], [("remember", "notes", 1, "Made a note")])
        d = json.loads(db.q1("SELECT detail FROM agent_activity WHERE agent_id=? ORDER BY id DESC", (self.ava,))["detail"])
        self.assertEqual(d["args"], {"note": "likes tea"})

    async def test_a_tool_that_raises_is_not_written_down_and_is_described_to_the_model(self):
        before = len(activity(self.ava))
        events, client = await self.play(self.ava, [use("remember", "{}"), say("sorry")])
        self.assertEqual(len(activity(self.ava)), before)
        self.assertEqual(json.loads(client.requests[1]["messages"][-1]["content"]), {"error": "KeyError: 'note'"})

    async def test_a_campaign_an_employee_creates_belongs_to_them(self):
        r, _ = await agents.run_agent_tool(self.agent(self.ava), "create_campaign", {"name": "Spring", "objective": "intro"})
        self.assertEqual(db.q1("SELECT agent_id FROM campaigns WHERE id=?", (r["campaign_id"],))["agent_id"], self.ava)

    async def test_the_copilot_logs_what_it_ran(self):
        client = FakeClient(use("get_dashboard_stats"), say("fine"))
        with mock.patch.object(llm, "client", lambda: client):
            events = await collect(admin_agent.chat_events([{"role": "user", "content": "how are we?"}]))
        self.assertEqual(events[-1], {"type": "done", "text": "fine"})  # the copilot's own event shape: no tools list, text not stripped
        self.assertIn("Copilot ran get_dashboard_stats", [r["message"] for r in db.q("SELECT message FROM activity")])


class ColleagueTests(Base):
    async def test_asking_a_colleague_runs_a_second_loop_without_tools(self):
        events, client = await self.play(self.ava, [use("ask_colleague", json.dumps({"agent": "Ben", "question": "how many?"})), say("Seven."), say("Ben says seven.")], "one_to_one")
        self.assertEqual(len(client.requests), 3)
        self.assertNotIn("tools", client.requests[1])         # the colleague answers without tools
        self.assertIn("Seven.", client.requests[2]["messages"][-1]["content"])
        self.assertEqual(events[-1]["text"], "Ben says seven.")


if __name__ == "__main__":
    unittest.main()

"""The AI employees running on the harness: what they are offered, what is refused or pretended, what is written to their history."""
import json
import unittest
from unittest import mock

from app import admin_agent, agents, db, llm, runtime, team

from tests.harness.fakes import FakeClient, collect, say, use


def tool_results(events):
    return [e for e in events if e["type"] == "tool_result"]


def activity(agent_id):
    return [dict(r) for r in db.q("SELECT tool,app,ok,summary FROM agent_activity WHERE agent_id=? ORDER BY id", (agent_id,))]


class Base(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.ava = agents.create("Ava", "female", "Support", "Warm.", "", bio="")
        cls.ben = agents.create("Ben", "male", "Analyst", "Dry.", "", bio="")  # files switched off below
        cls.mia = agents.create("Mia", "female", "Ops lead", "Calm.", "", bio="")
        team.set_manager(cls.mia, True)
        db.update("agents", cls.ben, caps_off=json.dumps(["files"]))
        cls.meeting = agents.start_meeting([cls.ava, cls.ben], "Weekly")
        agents.log_line(cls.meeting, "admin", None, "Please prepare the quarterly summary for Friday.")
        cls.urgent = agents.start_meeting([cls.ava, cls.mia], "Urgent")
        agents.log_line(cls.urgent, "admin", None, "Write the summary document right now please")

    def agent(self, aid):
        return agents.get(aid)

    async def play(self, agent, turns, kind="work", **kw):
        client = FakeClient(*turns)
        with mock.patch.object(llm, "client", lambda: client):
            events = await collect(agents.run_loop(self.agent(agent) if isinstance(agent, int) else agent, [{"role": "user", "content": "go"}], kind, **kw))
        return events, client


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
        self.assertNotIn("list_documents", [t["name"] for t in ben])  # files are switched off for Ben
        self.assertNotIn("list_connectors", [t["name"] for t in ava])  # manager powers
        self.assertIn("list_connectors", [t["name"] for t in mia])

    def test_the_live_meeting_tool_list_is_everything_when_no_one_is_named(self):
        self.assertEqual(len(agents.realtime_tools(None)), len(runtime.employees().registry))
        sample = agents.realtime_tools(self.agent(self.ava))[0]
        self.assertEqual(sorted(sample), ["description", "name", "parameters", "type"])

    def test_the_copilot_is_a_harness_with_its_own_settings(self):
        c = admin_agent.COPILOT
        self.assertEqual(c.config.max_steps, 6)
        self.assertEqual(c.registry.names(), [t["name"] for t in admin_agent.TOOLS])
        self.assertEqual(c.policies.policies, [])


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

    async def test_in_a_meeting_heavy_work_goes_on_the_to_do_list_unless_the_admin_says_now(self):
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

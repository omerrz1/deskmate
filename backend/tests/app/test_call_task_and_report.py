"""A call has to do the task it was given, sound like the voice that was chosen, and tell whoever asked what happened."""
import asyncio
import contextlib
import json
import unittest
from unittest import mock

from app import agents, callengine, costs, db, eleven_agents, files, knowledge, learning, llm, semantic, voices, wa_call
from .test_twilio_call import TwilioFixture, twilio_call


class CallReportBase(TwilioFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        with db.use(self.ws):
            costs.init()  # the per-employee spend cap column that app start-up adds; call_now checks it

    def make_chat(self, agent_name="Ethan"):
        aid = agents.create(agent_name, "male", "Caller", "Friendly.", "", bio="")
        return aid, agents.start_meeting([aid], chat=True)

    def make_call(self, meeting_id, agent_id, objective="Ask them what their favourite meal is", status="in_progress", **fields):
        contact = db.run("INSERT INTO contacts(name,phone,language,status,created_at) VALUES(?,?,?,?,?)",
                         ("+447447781753", "+447447781753", "English", "new", db.now()))
        campaign = db.run("INSERT INTO campaigns(name,objective,filters,status,max_attempts,created_at,agent_id) VALUES(?,?,?,?,?,?,?)",
                          ("Direct call", objective, "{}", "active", 1, db.now(), agent_id))
        call = db.run("INSERT INTO calls(contact_id,campaign_id,status,mode,direction,objective,reason,answered_at,created_at,report_meeting_id,report_agent_id) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (contact, campaign, status, "pstn", "out", objective, "direct", db.now() - 30, db.now(), meeting_id, agent_id))
        if fields:
            db.update("calls", call, **fields)
        return call

    def chat_text(self, meeting_id):
        return [r["text"] for r in db.q("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id", (meeting_id,))]


class ReportBackTests(CallReportBase):
    async def test_the_asker_gets_the_answer_in_the_chat_when_the_call_ends(self):
        """Regression: nothing was ever posted back, so 'report what they said' could not be done."""
        transcript = [{"role": "agent", "text": "Hi, is this you?", "t": 1}, {"role": "customer", "text": "Yeah, who's this?", "t": 3},
                      {"role": "agent", "text": "Quick one, what's your favourite meal?", "t": 6}, {"role": "customer", "text": "Chicken biryani, every time.", "t": 9}]
        analysis = {"summary": "They said their favourite meal is chicken biryani.", "outcome": "success", "sentiment": "positive",
                    "key_points": ["Loves chicken biryani"], "objective_result": "achieved", "objective_answer": "Favourite meal: chicken biryani"}
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, transcript=json.dumps(transcript))
            with mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, return_value=analysis):
                await callengine.finalize(call, "completed")
                await callengine.finalize(call, "completed")  # a second source of the same ending must not post again
            callengine.announce_call(call)
            posted = self.chat_text(mid)
            self.assertEqual(len(posted), 1)
            self.assertIn("What they said: Favourite meal: chicken biryani", posted[0])
            self.assertIn("Task: Ask them what their favourite meal is", posted[0])
            self.assertIn("Them: Chicken biryani, every time.", posted[0])
            self.assertEqual(db.q1("SELECT agent_id FROM meeting_msgs WHERE meeting_id=? AND role='agent'", (mid,))["agent_id"], aid)

    async def test_a_call_nobody_answered_is_reported_too(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, status="ringing")
            callengine.mark_missed(call, "no_answer")
            posted = self.chat_text(mid)
            self.assertEqual(len(posted), 1)
            self.assertIn("didn't pick up", posted[0])
            self.assertIn("favourite meal", posted[0])

    async def test_a_call_with_no_captured_speech_does_not_invent_an_answer(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            await callengine.finalize(call, "completed")
            self.assertIn("couldn't capture anything they said", self.chat_text(mid)[0])

    async def test_the_report_says_when_the_backup_voice_was_used(self):
        """The robotic voice was never explained; now the report names the reason."""
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, voice_engine="openai: no ElevenLabs key is connected", status="no_answer", summary="x")
            callengine.announce_call(call)
            self.assertIn("backup OpenAI voice (no ElevenLabs key is connected)", self.chat_text(mid)[0])

    async def test_a_call_that_failed_to_start_is_not_posted_twice(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            contact = self.chat["contact_id"]
            with mock.patch.object(twilio_call, "request", new_callable=mock.AsyncMock, side_effect=twilio_call.TwilioError("Twilio is down")):
                result = await callengine.call_now(contact, "Ask something", aid, meeting_id=mid)
            self.assertIn("error", result)  # the tool already tells the asker, so the chat gets no second message
            self.assertEqual(self.chat_text(mid), [])

    async def test_call_now_remembers_which_chat_to_report_to(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            with mock.patch.object(twilio_call, "request", new_callable=mock.AsyncMock, return_value={"sid": "CA" + "d" * 32}):
                result = await callengine.call_now(self.chat["contact_id"], "Ask them what their favourite meal is", aid, meeting_id=mid)
            row = db.q1("SELECT report_meeting_id,report_agent_id,reason,objective FROM calls WHERE id=?", (result["call_id"],))
            self.assertEqual(row, {"report_meeting_id": mid, "report_agent_id": aid, "reason": "direct", "objective": "Ask them what their favourite meal is"})
            await wa_call.SESSIONS[(self.ws, result["call_id"])].end("no_answer")

    async def test_the_copilot_tool_passes_the_chat_to_the_call(self):
        from app import admin_agent
        with db.use(self.ws), mock.patch.object(callengine, "call_now", new_callable=mock.AsyncMock, return_value={"call_id": 5, "state": "initiated"}) as dial:
            aid, mid = self.make_chat()
            result, _ = await admin_agent.run_tool("call_contact", {"phone": "+447700900124", "objective": "Ask X"}, agent={**agents.get(aid), "_meeting_id": mid})
            self.assertEqual(dial.call_args.kwargs["meeting_id"], mid)
            self.assertIn("posted in this chat", result["report_back"])


class CampaignReportTests(CallReportBase):
    async def test_a_finished_campaign_reports_every_lead_and_fills_the_sheet(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            sheet = db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (aid, "leads", "sheet", json.dumps({"columns": ["name", "phone", "status"], "rows": [["Sam", "+447700900201", ""], ["Kim", "+447700900202", ""]]}), db.now(), db.now()))
            camp = db.run("INSERT INTO campaigns(name,objective,filters,status,max_attempts,created_at,agent_id,report_meeting_id) VALUES(?,?,?,?,?,?,?,?)",
                          ("Meal survey", "Ask their favourite meal", "{}", "active", 1, db.now(), aid, mid))
            sam = db.run("INSERT INTO contacts(name,phone,language,status,created_at) VALUES(?,?,?,?,?)", ("Sam", "+447700900201", "English", "new", db.now()))
            kim = db.run("INSERT INTO contacts(name,phone,language,status,created_at) VALUES(?,?,?,?,?)", ("Kim", "+447700900202", "English", "new", db.now()))
            talk = json.dumps([{"role": "agent", "text": "Hi", "t": 1}, {"role": "customer", "text": "Pizza", "t": 3}])
            done = db.run("INSERT INTO calls(contact_id,campaign_id,status,mode,outcome,summary,transcript,report,report_sheet_id,report_agent_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (sam, camp, "completed", "pstn", "success", "Sam likes pizza.", talk, json.dumps({"objective_answer": "Favourite meal: pizza"}), sheet, aid, db.now()))
            db.run("INSERT INTO calls(contact_id,campaign_id,status,mode,outcome,summary,created_at) VALUES(?,?,?,?,?,?,?)", (kim, camp, "no_answer", "pstn", "no_answer", "No answer.", db.now()))
            self.assertTrue(files.update_call_sheet(done, "success", "Sam likes pizza."))
            files.flush_sheet_updates()
            callengine._check_campaign_done(camp)
            callengine._check_campaign_done(camp)
            posted = self.chat_text(mid)
            self.assertEqual(len(posted), 1)
            self.assertIn("Campaign 'Meal survey' has finished: 2 leads, 1 reached, 1 not reached", posted[0])
            self.assertIn("Sam (+447700900201): Favourite meal: pizza", posted[0])
            table = json.loads(db.q1("SELECT body FROM documents WHERE id=?", (sheet,))["body"])
            self.assertEqual(table["columns"], ["name", "phone", "status", "Call summary", "Call answer"])
            self.assertEqual(table["rows"][0][2:], ["success", "Sam likes pizza.", "Favourite meal: pizza"])

    async def test_start_campaign_records_the_chat_that_asked_for_it(self):
        from app import admin_agent
        with db.use(self.ws), mock.patch.object(callengine, "queue_campaign", return_value={"queued": 3, "invalid_phone": 0}):
            aid, mid = self.make_chat()
            camp = db.run("INSERT INTO campaigns(name,objective,filters,status,created_at) VALUES(?,?,?,?,?)", ("Survey", "Ask", "{}", "draft", db.now()))
            await admin_agent.run_tool("start_campaign", {"campaign_id": camp}, agent={**agents.get(aid), "_meeting_id": mid})
            self.assertEqual(db.q1("SELECT report_meeting_id FROM campaigns WHERE id=?", (camp,))["report_meeting_id"], mid)


class PromptTests(CallReportBase):
    def prompt_for(self, reason, objective="Ask them what their favourite meal is"):
        contact = db.run("INSERT INTO contacts(name,phone,language,status,created_at) VALUES(?,?,?,?,?)", ("Sam Jones", "+447700900201", "English", "new", db.now()))
        camp = db.run("INSERT INTO campaigns(name,objective,filters,status,created_at) VALUES(?,?,?,?,?)", ("Calls", objective, "{}", "active", db.now()))
        call = db.run("INSERT INTO calls(contact_id,campaign_id,status,objective,reason,created_at) VALUES(?,?,?,?,?,?)", (contact, camp, "in_progress", objective, reason, db.now()))
        call_row, contact_row, camp_row = callengine.call_context(call)
        return callengine.system_prompt(call_row, contact_row, camp_row, realtime=True, external=True)

    async def test_the_task_comes_before_everything_else_and_a_direct_call_does_not_pitch(self):
        with db.use(self.ws):
            prompt = self.prompt_for("direct")
            self.assertLess(prompt.index("YOUR TASK ON THIS CALL"), prompt.index("BACKGROUND"))
            self.assertLess(prompt.index("YOUR TASK ON THIS CALL"), prompt.index("PERSONALITY"))
            self.assertIn("Ask them what their favourite meal is", prompt)
            self.assertNotIn("Selling is", prompt)
            self.assertNotIn("PRODUCTS / SERVICES", prompt)
            self.assertIn("Every sentence is 15 words or fewer", prompt)
            self.assertIn("Never talk over them", prompt)
            self.assertIn("say it back in a few words", prompt)  # the answer is confirmed aloud, so the transcript holds it

    async def test_a_campaign_call_keeps_the_sales_context_but_still_leads_with_the_task(self):
        with db.use(self.ws):
            prompt = self.prompt_for("")
            self.assertIn("PRODUCTS / SERVICES WE COVER", prompt)
            self.assertLess(prompt.index("YOUR TASK ON THIS CALL"), prompt.index("PRODUCTS / SERVICES WE COVER"))


class VoiceTests(CallReportBase):
    def test_a_lead_saved_under_its_phone_number_is_not_greeted_by_it(self):
        self.assertEqual(eleven_agents.first_line_for({"name": "+447447781753"}), "Hi, hello?")
        self.assertEqual(eleven_agents.first_line_for({"name": "07447 781753"}), "Hi, hello?")
        self.assertEqual(eleven_agents.first_line_for({"name": "Zubair Khan"}), "Hi, is this Zubair?")
        self.assertEqual(eleven_agents.first_line_for({"name": ""}), "Hi, hello?")

    async def test_the_old_openai_default_no_longer_overrides_elevenlabs(self):
        with db.use(self.ws):
            db.run("INSERT OR REPLACE INTO settings(key,value) VALUES('voice_engine','openai')")  # what a workspace saved while OpenAI was the default
            self.assertEqual(voices.get_setting("voice_engine", "openai"), "elevenlabs")
            voices.set_setting("voice_engine", "openai")  # a choice made on purpose now sticks
            self.assertEqual(voices.get_setting("voice_engine", "elevenlabs"), "openai")
            voices.set_setting("voice_engine", "elevenlabs")
            self.assertEqual(voices.get_setting("voice_engine", "openai"), "elevenlabs")

    async def test_elevenlabs_gets_a_second_try_before_the_backup_voice(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            call_row, contact, camp = callengine.call_context(call)
            sess = mock.Mock(call_id=call, agent={}, direction="out")
            attempts = {"n": 0}

            async def flaky(profile):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("temporary outage")
                return "agent_1"

            with mock.patch.object(llm, "eleven_key", return_value="key"), \
                 mock.patch.object(eleven_agents, "ensure_agent", side_effect=flaky), \
                 mock.patch.object(eleven_agents, "signed_url", new_callable=mock.AsyncMock, return_value="wss://example.test"):
                engine = await wa_call.make_engine(sess, call_row, contact, camp)
            self.assertIsInstance(engine, wa_call.ElevenEngine)
            self.assertEqual(attempts["n"], 2)
            self.assertEqual(db.q1("SELECT voice_engine FROM calls WHERE id=?", (call,))["voice_engine"], "elevenlabs")

    async def test_the_backup_voice_is_recorded_with_its_reason(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            call_row, contact, camp = callengine.call_context(call)
            sess = mock.Mock(call_id=call, agent={}, direction="out")
            with mock.patch.object(llm, "eleven_key", return_value=""):
                engine = await wa_call.make_engine(sess, call_row, contact, camp)
            self.assertIsInstance(engine, wa_call.OpenAIEngine)
            self.assertEqual(db.q1("SELECT voice_engine FROM calls WHERE id=?", (call,))["voice_engine"], "openai: no ElevenLabs key is connected")


class FakeEleven:
    """Stands in for httpx.AsyncClient so the voice list can be tested without ElevenLabs."""
    calls = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @staticmethod
    def reply(status, body):
        return mock.Mock(status_code=status, json=lambda: body, text=json.dumps(body))

    async def get(self, url, headers=None, params=None):
        FakeEleven.calls.append(("GET", url, params))
        if url.endswith("/v2/voices"):
            return self.reply(200, {"voices": [
                {"voice_id": "A" * 20, "name": "Sophie", "category": "premade", "labels": {"gender": "female", "accent": "british"}, "preview_url": "https://x/sophie.mp3"},
                {"voice_id": "B" * 20, "name": "Daniel", "category": "premade", "labels": {"gender": "male", "accent": "american"}, "preview_url": "https://x/daniel.mp3"}],
                "has_more": False})
        if url.endswith("/v1/shared-voices"):
            return self.reply(200, {"voices": [{"voice_id": "C" * 20, "public_owner_id": "D" * 20, "name": "Mia", "gender": "female", "accent": "british", "preview_url": "https://x/mia.mp3"}]})
        if "/v1/voices/" in url:
            return self.reply(200, {"voice_id": url.rsplit("/", 1)[1], "name": "Sophie", "labels": {"gender": "female"}, "preview_url": "https://x/sophie.mp3"})
        return self.reply(404, {})

    async def post(self, url, headers=None, json=None):
        FakeEleven.calls.append(("POST", url, json))
        return self.reply(200, {"voice_id": url.rsplit("/", 1)[1]})


class VoicePickerTests(CallReportBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        FakeEleven.calls = []
        for patcher in (mock.patch("app.voice_library.httpx.AsyncClient", FakeEleven), mock.patch.object(llm, "eleven_key", return_value="key")):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_the_account_voices_and_the_public_library_can_both_be_browsed_and_filtered(self):
        st, mine = await self.owner.get("/api/voices/library", query="source=mine")
        self.assertEqual((st, [v["name"] for v in mine]), (200, ["Sophie", "Daniel"]))
        self.assertEqual(mine[0]["gender"], "female")
        self.assertEqual(mine[0]["preview_url"], "https://x/sophie.mp3")
        _, men = await self.owner.get("/api/voices/library", query="source=mine&gender=male")
        self.assertEqual([v["name"] for v in men], ["Daniel"])
        _, shared = await self.owner.get("/api/voices/library", query="source=library&gender=female&language=English")
        self.assertEqual((shared[0]["name"], shared[0]["public_owner_id"], shared[0]["source"]), ("Mia", "D" * 20, "library"))
        self.assertEqual(FakeEleven.calls[-1][2]["gender"], "female")

    async def test_choosing_a_library_voice_adds_it_to_the_account_first(self):
        st, r = await self.owner.post("/api/voices/add", {"voice_id": "C" * 20, "public_owner_id": "D" * 20, "name": "Mia"})
        self.assertEqual((st, r["added"]), (200, True))
        self.assertTrue(FakeEleven.calls[-1][1].endswith(f"/v1/voices/add/{'D' * 20}/{'C' * 20}"))
        st, _ = await self.owner.post("/api/voices/add", {"voice_id": "../etc", "public_owner_id": "D" * 20})
        self.assertEqual(st, 400)

    async def test_without_an_elevenlabs_key_the_list_says_so(self):
        with mock.patch.object(llm, "eleven_key", return_value=""):
            st, r = await self.owner.get("/api/voices/library", query="source=mine")
        self.assertEqual(st, 400)
        self.assertIn("Connect your ElevenLabs key", r["detail"])

    async def test_an_employee_gets_their_own_voice_and_call_name_and_the_call_uses_both(self):
        with db.use(self.ws):
            aid = agents.create("Ethan", "male", "Caller", "Friendly.", "", bio="")
        st, r = await self.owner.patch(f"/api/agents/{aid}", {"eleven_voice": "A" * 20, "call_name": "  Sophie  "})
        self.assertEqual((st, r["eleven_voice"], r["call_name"]), (200, "A" * 20, "Sophie"))
        st, _ = await self.owner.patch(f"/api/agents/{aid}", {"eleven_voice": "not a voice id!"})
        self.assertEqual(st, 400)
        with db.use(self.ws):
            call = self.make_call(None, aid)
            row, contact, camp = callengine.call_context(call)
            self.assertIn("You are Sophie, a voice assistant", callengine.system_prompt(row, contact, camp, realtime=True, external=True))
            sess = mock.Mock(call_id=call, agent=agents.get(aid), direction="out")
            with mock.patch.object(llm, "eleven_key", return_value="key"), \
                 mock.patch.object(eleven_agents, "ensure_agent", new_callable=mock.AsyncMock, return_value="agent_1"), \
                 mock.patch.object(eleven_agents, "signed_url", new_callable=mock.AsyncMock, return_value="wss://example.test"):
                engine = await wa_call.make_engine(sess, row, contact, camp)
            self.assertEqual(engine.voice_id, "A" * 20)  # the employee's own voice, not the company default


class StandingInstructionsAndWebTests(CallReportBase):
    async def test_standing_instructions_are_read_into_every_call(self):
        st, r = await self.owner.put("/api/settings", {"call_instructions": "  Never discuss prices. Keep it under two minutes.  "})
        self.assertEqual((st, r["call_instructions"]), (200, "Never discuss prices. Keep it under two minutes."))
        with db.use(self.ws):
            aid, mid = self.make_chat()
            row, contact, camp = callengine.call_context(self.make_call(mid, aid))
            self.assertIn("STANDING RULES FROM THE ADMIN", callengine.system_prompt(row, contact, camp, realtime=True, external=True))
            self.assertIn("Never discuss prices", callengine.system_prompt(row, contact, camp, realtime=True, external=True))
        await self.owner.put("/api/settings", {"call_instructions": ""})
        with db.use(self.ws):
            self.assertNotIn("STANDING RULES", callengine.system_prompt(row, contact, camp, realtime=True, external=True))

    async def test_the_caller_can_search_the_web_mid_call(self):
        from app import agent_tools, public_tools
        self.assertIn("search_web", agent_tools.SPECS)
        found = [{"title": "Salmon burger", "url": "https://example.com/r", "snippet": "Salmon patty, brioche bun, lettuce, tartare sauce."}]
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            with mock.patch.object(public_tools, "web_search", new_callable=mock.AsyncMock, return_value=found):
                result = await agent_tools.arun(call, "search_web", {"query": "salmon burger ingredients"})
            self.assertIn("brioche bun", result)
            self.assertIn("ignore", result)  # web text is data, never instructions
            self.assertEqual(db.q1("SELECT kind FROM call_actions WHERE call_id=?", (call,))["kind"], "web_search")
            with mock.patch.object(public_tools, "web_search", new_callable=mock.AsyncMock, side_effect=TimeoutError):
                self.assertIn("didn't come back in time", await agent_tools.arun(call, "search_web", {"query": "x"}))


class CallBriefTests(CallReportBase):
    MESSAGE = ("please call omer (07447781753) ask him his favourate meal and past it here along side theingredients go search the ingredients "
               "after you find out his meal, be very fast dont talk much be simple and natural and fun")

    async def test_the_message_is_split_into_the_phone_task_the_manner_and_the_work_for_afterwards(self):
        """Regression: the whole sentence went to the phone agent, which then quizzed the person about ingredients."""
        with mock.patch("app.messaging.default_code", return_value="44"):
            rules = agents._explicit_call_args(self.MESSAGE, [])
        self.assertEqual(rules, {"phone": "+447447781753", "objective": "Ask him his favourate meal", "name": "Omer"})
        split = {"person_name": "Omer", "task": "Ask him what his favourite meal is.", "style": "very fast, few words, simple, natural, fun",
                 "after_call": "Post the meal here and search for its ingredients."}
        with mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, return_value=split):
            args = await agents.refine_call_args(rules, self.MESSAGE)
        self.assertEqual(args["objective"], "Ask him what his favourite meal is.")
        self.assertEqual(args["style"], "very fast, few words, simple, natural, fun")
        self.assertEqual(args["after_call"], "Post the meal here and search for its ingredients.")
        self.assertEqual(args["name"], "Omer")
        with mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, side_effect=RuntimeError("no key")):
            self.assertEqual(await agents.refine_call_args(rules, self.MESSAGE), rules)  # without a model the rules stand

    async def test_the_phone_agent_gets_the_task_and_manner_but_not_the_afterwards_work(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, objective="Ask him what his favourite meal is.", style="very fast, few words, fun",
                                  after_call="Post the meal here and search for its ingredients.")
            row, contact, camp = callengine.call_context(call)
            prompt = callengine.system_prompt(row, contact, camp, realtime=True, external=True)
            self.assertIn("HOW THE ADMIN WANTS YOU TO SOUND: very fast, few words, fun", prompt)
            self.assertNotIn("search for its ingredients", prompt)

    async def test_the_employee_does_the_afterwards_work_in_the_chat_once_the_answer_is_in(self):
        transcript = [{"role": "agent", "text": "Hi", "t": 1}, {"role": "customer", "text": "Salmon burger", "t": 3}]
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, status="completed", transcript=json.dumps(transcript), duration=20,
                                  after_call="Post the meal here and search for its ingredients.")
            with mock.patch.object(agents, "follow_up_after_call", new_callable=mock.AsyncMock) as follow:
                callengine.announce_call(call)
                for task in list(callengine._FOLLOW_UPS):
                    await task
            follow.assert_awaited_once_with(mid, aid, "Post the meal here and search for its ingredients.")
            self.assertIn("Next, as you asked: Post the meal here", self.chat_text(mid)[0])

    async def test_a_call_that_got_no_answer_does_not_trigger_the_afterwards_work(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, status="no_answer", after_call="Search the ingredients.")
            with mock.patch.object(agents, "follow_up_after_call", new_callable=mock.AsyncMock) as follow:
                callengine.announce_call(call)
                self.assertEqual(callengine._FOLLOW_UPS, set())
            follow.assert_not_awaited()


class SmsErrorTests(CallReportBase):
    async def test_a_text_that_cannot_be_sent_explains_the_twilio_number_problem(self):
        from app import integrations
        hint = integrations.twilio_error_hint(21606, "The 'From' phone number provided is not a valid, SMS-capable Twilio phone number.", "messaging")
        self.assertIn("SMS capability", hint)
        self.assertIn("Active numbers", hint)
        self.assertIn("Geo permissions", integrations.twilio_error_hint(21408, "", "messaging"))
        self.assertEqual(integrations.twilio_error_hint(21606, "", "voice"), "")  # a voice call error is explained elsewhere


class SmsNumberTests(CallReportBase):
    def omer(self):
        contact = db.run("INSERT INTO contacts(name,phone,language,status,created_at) VALUES(?,?,?,?,?)", ("Omer", "+447447781753", "English", "new", db.now()))
        db.run("INSERT INTO calls(contact_id,status,mode,direction,created_at) VALUES(?,?,?,?,?)", (contact, "completed", "pstn", "out", db.now()))
        return contact

    async def test_a_number_with_a_dropped_digit_is_caught_before_twilio_and_the_right_one_is_suggested(self):
        """Regression: the model retyped Omer's number as +44744781753 (a digit short) and Twilio could only say 'invalid'."""
        from app import messaging
        with db.use(self.ws), mock.patch.object(messaging, "_dispatch", new_callable=mock.AsyncMock) as dispatch:
            self.omer()
            result, _ = await messaging.run_tool(None, "send_sms", {"to": "+44744781753", "body": "Ingredients..."})
            dispatch.assert_not_awaited()
            self.assertEqual(result["state"], "failed")
            self.assertIn("not a valid UK mobile number", result["message"])
            self.assertIn("Did you mean +447447781753 (Omer)?", result["message"])
            self.assertEqual(db.q("SELECT id FROM outbox"), [])
            self.assertIn("Retry with the saved contact", result["note_for_you"])

    async def test_a_text_to_a_saved_contact_uses_the_saved_number(self):
        from app import messaging
        with db.use(self.ws), mock.patch.object(messaging, "_dispatch", new_callable=mock.AsyncMock, return_value=True) as dispatch:
            contact = self.omer()
            result, _ = await messaging.run_tool(None, "send_sms", {"contact": "omer", "to": "+44744781753", "body": "Salmon burger: bun, salmon, lettuce."})
            self.assertEqual(result["state"], "sent")
            dispatch.assert_awaited_once()
            row = db.q1("SELECT to_addr,contact_id FROM outbox")
            self.assertEqual((row["to_addr"], row["contact_id"]), ("+447447781753", contact))  # the typed-out number lost to the saved one

    async def test_an_unknown_or_ambiguous_contact_name_says_so_and_a_good_number_still_works(self):
        from app import messaging
        with db.use(self.ws), mock.patch.object(messaging, "_dispatch", new_callable=mock.AsyncMock, return_value=True):
            self.omer()
            unknown, _ = await messaging.run_tool(None, "send_sms", {"contact": "Nobody", "body": "hi"})
            self.assertIn("no saved contact called 'Nobody'", unknown["message"])
            db.run("INSERT INTO contacts(name,phone,language,status,created_at) VALUES(?,?,?,?,?)", ("Omer", "+447700900111", "English", "new", db.now()))
            both, _ = await messaging.run_tool(None, "send_sms", {"contact": "Omer", "body": "hi"})
            self.assertIn("More than one saved contact", both["message"])
            ok, _ = await messaging.run_tool(None, "send_sms", {"to": "+447447781753", "body": "hi"})
            self.assertEqual(ok["state"], "sent")


class SmsSenderTests(CallReportBase):
    def body(self, **config):
        from app import integrations
        action = next(t for t in integrations.spec_of("twilio")["tools"] if t["name"] == "send_sms")
        cfg = integrations._config({"kind": "twilio", "config": json.dumps({"from_number": "+441234567890", **config})})
        return integrations.render(action["body"], {"cfg": cfg, "args": {"to": "+447447781753", "body": "hi"}})

    async def test_texts_come_from_the_sms_sender_and_otherwise_from_the_caller_id(self):
        """Regression: a voice-only caller ID number could not text, and there was no way to use any other sender."""
        self.assertEqual(self.body()["From"], "+441234567890")
        self.assertEqual(self.body(sms_from="+441111111111")["From"], "+441111111111")
        self.assertEqual(self.body(sms_from="AACollege")["From"], "AACollege")  # a short name works for UK phones without any SMS number
        service = self.body(sms_from="MG" + "a" * 32)
        self.assertEqual(service["MessagingServiceSid"], "MG" + "a" * 32)
        self.assertNotIn("From", service)  # a Messaging Service replaces the From number

    async def test_the_sms_sender_field_accepts_numbers_services_and_names_only(self):
        from app import integrations
        spec = integrations.spec_of("twilio")
        base = {"account_sid": "AC" + "a" * 32, "auth_token": "secret"}
        self.assertEqual(integrations.validate_config(spec, {**base, "sms_from": "+44 1111 111111"})["sms_from"], "+441111111111")
        self.assertEqual(integrations.validate_config(spec, {**base, "sms_from": "AACollege"})["sms_from"], "AACollege")
        for bad in ("ab", "12345678", "Far Too Long A Name"):
            with self.assertRaises(integrations.AppError):
                integrations.validate_config(spec, {**base, "sms_from": bad})


class SmsSenderCheckTests(CallReportBase):
    TWILIO_ERROR = "Twilio can't send texts from the saved number. https://www.twilio.com/docs/api/errors/21659"

    async def check(self, sender, owned, verified=()):
        async def fake(path, data=None, client=None, cfg=None, **kw):
            if path == "IncomingPhoneNumbers.json":
                return {"incoming_phone_numbers": owned}
            return {"outgoing_caller_ids": [{"phone_number": v} for v in verified]}
        with mock.patch.object(twilio_call, "request", side_effect=fake):
            return await twilio_call.sender_check({"sms_sender": sender})

    async def test_a_verified_caller_id_is_named_as_the_reason_texts_fail(self):
        """Calls can show a Verified Caller ID but Twilio cannot text from it: say so, and list what would work."""
        said = await self.check("+441234567890", [{"phone_number": "+15550001111", "capabilities": {"voice": True, "sms": True}}], verified=["+441234567890"])
        self.assertIn("only a Verified Caller ID", said)
        self.assertIn("+15550001111", said)

    async def test_a_voice_only_number_and_a_name_sender_are_explained(self):
        voice_only = await self.check("+441234567890", [{"phone_number": "+441234567890", "capabilities": {"voice": True, "sms": False}}])
        self.assertIn("NO SMS capability", voice_only)
        self.assertIn("none. Buy one", voice_only)
        self.assertIn("refuse name senders", await self.check("AALondon", []))

    async def test_a_failed_text_carries_the_sender_check_and_a_broken_check_never_hides_the_error(self):
        from app import integrations, messaging
        with db.use(self.ws):
            rid = db.run("INSERT INTO outbox(channel,to_addr,body,created_at) VALUES(?,?,?,?)", ("sms", "+447447781753", "hi", db.now()))
            row = db.q1("SELECT * FROM outbox WHERE id=?", (rid,))
            with mock.patch.object(integrations, "call", new_callable=mock.AsyncMock, return_value={"error": self.TWILIO_ERROR}), \
                 mock.patch.object(twilio_call, "sender_check", new_callable=mock.AsyncMock, return_value="Sender check: only a Verified Caller ID."):
                self.assertFalse(await messaging._dispatch(row))
            error = db.q1("SELECT error FROM outbox WHERE id=?", (rid,))["error"]
            self.assertIn("errors/21659", error)
            self.assertIn("Sender check: only a Verified Caller ID.", error)
            with mock.patch.object(integrations, "call", new_callable=mock.AsyncMock, return_value={"error": self.TWILIO_ERROR}), \
                 mock.patch.object(twilio_call, "sender_check", new_callable=mock.AsyncMock, side_effect=RuntimeError("boom")):
                self.assertFalse(await messaging._dispatch(row))
            self.assertIn("errors/21659", db.q1("SELECT error FROM outbox WHERE id=?", (rid,))["error"])


class SendDetailsTests(CallReportBase):
    """Regression: a customer asking about business courses was told 'the team will send you the options' and nothing was ever sent."""

    def new_call(self, **fields):
        aid, mid = self.make_chat()
        call = self.make_call(mid, aid, objective="Call them and sell a course they want. Send a link from the website.", **fields)
        contact = db.q1("SELECT contact_id FROM calls WHERE id=?", (call,))["contact_id"]
        db.update("contacts", contact, phone="+447447781753", email="")
        return aid, call, contact

    async def test_the_caller_texts_the_details_itself_to_the_number_it_is_calling(self):
        from app import agent_tools, messaging
        with db.use(self.ws):
            _, call, contact = self.new_call()
            voices.set_setting("main_link", "https://example.com/business")
            send = mock.AsyncMock(return_value={"state": "sent", "message": "Sent."})
            with mock.patch.object(messaging, "send", send):
                result = await agent_tools.arun(call, "send_information", {
                    "channel": "sms", "what": "business courses", "to": "+449999999999",  # a number the model made up is ignored
                    "message": "AA London College business courses: Level 5 Diploma in Business Management, Level 3 Certificate. https://example.com/business"})
            self.assertTrue(result.startswith("Sent."))
            self.assertEqual(send.await_args.args[1:3], ("sms", "+447447781753"))
            self.assertIn("https://example.com/business", send.await_args.args[3])
            self.assertEqual(db.q1("SELECT kind,status FROM call_actions WHERE call_id=?", (call,)), {"kind": "information_sent", "status": "done"})

    async def test_a_failed_send_is_logged_for_the_team_and_the_caller_is_told_what_to_say(self):
        from app import agent_tools, messaging
        with db.use(self.ws):
            _, call, _ = self.new_call()
            with mock.patch.object(messaging, "send", mock.AsyncMock(return_value={"state": "failed", "message": "Sender check: only a Verified Caller ID."})):
                result = await agent_tools.arun(call, "send_information", {"message": "Details of the business courses"})
            self.assertIn("couldn't send the message just now", result)
            self.assertIn("make sure it reaches them shortly", result)
            self.assertEqual(db.q1("SELECT kind,status FROM call_actions WHERE call_id=?", (call,)), {"kind": "send_information", "status": "pending"})

    async def test_no_more_than_three_messages_go_out_on_one_call(self):
        from app import agent_tools, messaging
        with db.use(self.ws):
            _, call, _ = self.new_call()
            send = mock.AsyncMock(return_value={"state": "sent", "message": "Sent."})
            with mock.patch.object(messaging, "send", send):
                for _ in range(5):
                    await agent_tools.arun(call, "send_information", {"message": "More details"})
            self.assertEqual(send.await_count, 3)

    async def test_email_needs_an_address_and_goes_through_the_employees_mailbox(self):
        from app import agent_tools, office
        with db.use(self.ws):
            aid, call, contact = self.new_call()
            asked = await agent_tools.arun(call, "send_information", {"channel": "email", "message": "Details"})
            self.assertIn("Ask for their email address", asked)
            db.update("contacts", contact, email="omer@example.com")
            with mock.patch.object(office, "queue_email", return_value=(1, "queued")) as queue:
                result = await agent_tools.arun(call, "send_information", {"channel": "email", "what": "business courses", "message": "Details https://example.com"})
            self.assertIn("on its way", result)
            self.assertEqual((queue.call_args.args[0], queue.call_args.args[1]), (aid, "omer@example.com"))

    async def test_a_missing_message_is_written_from_the_company_facts(self):
        from app import agent_tools, messaging
        with db.use(self.ws):
            _, call, _ = self.new_call()
            voices.set_setting("main_link", "https://example.com/b")
            send = mock.AsyncMock(return_value={"state": "sent", "message": "Sent."})
            with mock.patch.object(messaging, "send", send), mock.patch.object(agent_tools, "_compose_text", new_callable=mock.AsyncMock, return_value="Business courses: https://example.com/b"):
                await agent_tools.arun(call, "send_information", {"what": "business courses"})
            self.assertEqual(send.await_args.args[3], "Business courses: https://example.com/b")

    async def test_the_brief_says_look_it_up_first_then_send_it_yourself_and_push_for_the_text(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            sell = callengine.call_context(self.make_call(mid, aid, objective="Call them and sell a course they want. Send a link from the website."))
            prompt = callengine.system_prompt(*sell, realtime=True, external=True)
            for needle in ("call lookup_info BEFORE you answer", "Never say \"the team will send it\"", "Shall I text you the details and the link right now?",
                           "saved admin answers, the company's own details and files first, then the company website", "lookup_info first"):
                self.assertIn(needle, prompt)
            self.assertNotIn("they want details sent (email/WhatsApp/SMS). Say it will arrive shortly", prompt)
            meal = callengine.call_context(self.make_call(mid, aid, objective="Ask them what their favourite meal is"))
            self.assertNotIn("HOW YOU ANSWER AND FOLLOW UP", callengine.system_prompt(*meal, realtime=True, external=True))  # a one-question call stays short

    async def test_changed_tool_definitions_are_pushed_to_elevenlabs_not_left_stale(self):
        """The agent only knows the tools ElevenLabs holds, so send_information needs its new `message` field there."""
        with db.use(self.ws):
            sent = []

            class Http:
                async def post(self, url, json=None, timeout=None):
                    sent.append(("POST", json["tool_config"]["name"]))
                    return mock.Mock(status_code=200, json=lambda: {"id": "tool_" + json["tool_config"]["name"]}, text="")

                async def patch(self, url, json=None, timeout=None):
                    sent.append(("PATCH", json["tool_config"]["name"]))
                    return mock.Mock(status_code=200, json=lambda: {}, text="")

            db.run("DELETE FROM el_tools")
            db.run("INSERT INTO el_tools(name,tool_id) VALUES('send_information','tool_old')")  # registered before it could carry a message
            await eleven_agents._extra_tool_ids(Http())
            self.assertIn(("PATCH", "send_information"), sent)
            self.assertIn(("POST", "search_web"), sent)
            sent.clear()
            await eleven_agents._extra_tool_ids(Http())
            self.assertEqual(sent, [])  # unchanged tools are left alone


class BigSheetTests(CallReportBase):
    """A campaign over a long sheet: results are written in batches, onto the right row, however the sheet changed meanwhile."""

    def lead_sheet(self, n=6):
        rows = [[f"Lead {i}", f"+4477009{i:05d}", ""] for i in range(n)]
        did = db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Leads','sheet',?,?,?)",
                     (json.dumps({"columns": ["Name", "Phone", "status"], "rows": rows}), db.now(), db.now()))
        contacts = []
        for i, row in enumerate(rows, 1):
            cid = db.run("INSERT INTO contacts(name,phone,language,status,extra,created_at) VALUES(?,?,?,?,?,?)",
                         (row[0], row[1], "English", "new", json.dumps({"__source_row": i}), db.now()))
            contacts.append(cid)
        camp = db.run("INSERT INTO campaigns(name,objective,filters,status,created_at) VALUES('Big','Ask','{}','active',?)", (db.now(),))
        ds = db.run("INSERT INTO datasets(name,filename,columns,row_count,created_at,source_doc_id) VALUES('Leads','l.csv','[]',?,?,?)", (n, db.now(), did))
        for cid in contacts:
            db.update("contacts", cid, dataset_id=ds)
        calls = [db.run("INSERT INTO calls(contact_id,campaign_id,status,mode,report_sheet_id,created_at) VALUES(?,?,?,?,?,?)", (cid, camp, "completed", "pstn", did, db.now())) for cid in contacts]
        return did, contacts, calls

    def table(self, did):
        return json.loads(db.q1("SELECT body FROM documents WHERE id=?", (did,))["body"])

    async def test_results_wait_a_moment_and_are_then_written_in_one_go(self):
        with db.use(self.ws):
            did, _, calls = self.lead_sheet()
            before = db.q1("SELECT updated_at FROM documents WHERE id=?", (did,))["updated_at"]
            with mock.patch.object(files, "SHEET_FLUSH_SECONDS", 0.05):
                for i, call in enumerate(calls[:4]):
                    self.assertTrue(files.update_call_sheet(call, "interested", f"Summary {i}"))
                self.assertEqual(self.table(did)["rows"][0][2], "")  # nothing has been rewritten yet
                with mock.patch.object(db, "update", wraps=db.update) as write:
                    await asyncio.sleep(0.4)
                self.assertEqual([c for c in write.call_args_list if c.args[0] == "documents"].__len__(), 1)  # four results, one write
            rows = self.table(did)["rows"]
            self.assertEqual([r[2] for r in rows[:4]], ["interested"] * 4)
            self.assertEqual(rows[3][3], "Summary 3")
            self.assertEqual(rows[4][2], "")
            self.assertGreater(db.q1("SELECT updated_at FROM documents WHERE id=?", (did,))["updated_at"], before)

    async def test_a_sheet_edited_in_between_still_gets_each_result_on_the_right_lead(self):
        """Regression guard for batching: rows moved while results were waiting must not receive someone else's result."""
        with db.use(self.ws):
            did, _, calls = self.lead_sheet()
            files.update_call_sheet(calls[2], "callback", "Call back Monday.")
            table = self.table(did)
            table["rows"] = [["Brand new lead", "+447000000000", ""], *table["rows"]]  # a row inserted above: every stored row number is now one too low
            db.update("documents", did, body=json.dumps(table))
            files.flush_sheet_updates()
            rows = self.table(did)["rows"]
            self.assertEqual(rows[3][0], "Lead 2")
            self.assertEqual(rows[3][2], "callback")  # on Lead 2's row, found by phone
            self.assertEqual(rows[2][2], "")  # the row that now sits at the stored position was left alone

    async def test_a_large_batch_is_written_without_waiting_for_the_timer(self):
        with db.use(self.ws):
            did, _, calls = self.lead_sheet()
            with mock.patch.object(files, "SHEET_FLUSH_AT", 3), mock.patch.object(files, "SHEET_FLUSH_SECONDS", 60):
                for call in calls[:3]:
                    files.update_call_sheet(call, "interested", "x")
            self.assertEqual([r[2] for r in self.table(did)["rows"][:3]], ["interested"] * 3)

    async def test_sheet_results_use_the_status_column_name_the_admin_asked_for(self):
        with db.use(self.ws):
            did, _, calls = self.lead_sheet()
            db.update("calls", calls[0], report_status_column="Call outcome")
            files.update_call_sheet(calls[0], "interested", "Wants details.")
            files.flush_sheet_updates()
            table = self.table(did)
            self.assertIn("Call outcome", table["columns"])
            self.assertEqual(table["rows"][0][table["columns"].index("Call outcome")], "interested")


class TextTheRightPersonTests(CallReportBase):
    """Regression: Ethan kept typing Omer's number a digit short, then told the admin the (correct) number had 9 digits and asked for another."""

    def called_from_chat(self, name="Omer Mustafa", phone="+447447781753"):
        aid, mid = self.make_chat()
        call = self.make_call(mid, aid)
        contact = db.q1("SELECT contact_id FROM calls WHERE id=?", (call,))["contact_id"]
        db.update("contacts", contact, name=name, phone=phone)
        return {**agents.get(aid), "_meeting_id": mid}, contact

    async def test_a_number_a_digit_short_goes_to_the_person_this_chat_just_phoned(self):
        from app import messaging
        with db.use(self.ws), mock.patch.object(messaging, "_dispatch", new_callable=mock.AsyncMock, return_value=True):
            agent, contact = self.called_from_chat()
            result, _ = await messaging.run_tool(agent, "send_sms", {"to": "+44744781753", "body": "Course details https://example.com"})
            self.assertEqual(result["state"], "sent")
            self.assertEqual(db.q1("SELECT to_addr,contact_id FROM outbox")["to_addr"], "+447447781753")
            self.assertIn("used the saved number for Omer Mustafa: +447447781753", result["message"])
            self.assertIn("Do not count digits", result["note_for_you"])

    async def test_no_number_at_all_means_the_one_person_just_phoned_and_two_people_means_asking_which(self):
        from app import messaging
        with db.use(self.ws), mock.patch.object(messaging, "_dispatch", new_callable=mock.AsyncMock, return_value=True):
            agent, _ = self.called_from_chat()
            sent, _ = await messaging.run_tool(agent, "send_sms", {"body": "Details"})
            self.assertEqual(sent["state"], "sent")
            other = db.run("INSERT INTO contacts(name,phone,language,status,created_at) VALUES(?,?,?,?,?)", ("Sam Jones", "+447700900222", "English", "new", db.now()))
            db.run("INSERT INTO calls(contact_id,status,mode,direction,report_meeting_id,created_at) VALUES(?,?,?,?,?,?)", (other, "completed", "pstn", "out", agent["_meeting_id"], db.now() + 1))
            asked, _ = await messaging.run_tool(agent, "send_sms", {"body": "Details"})
            self.assertEqual(asked["state"], "failed")
            self.assertIn("Say which one to text", asked["message"])

    async def test_a_wrong_number_with_no_call_in_this_chat_is_refused_and_names_the_number_it_was_given(self):
        from app import messaging
        with db.use(self.ws), mock.patch.object(messaging, "_dispatch", new_callable=mock.AsyncMock) as dispatch:
            result, _ = await messaging.run_tool({"id": 1}, "send_sms", {"to": "+44744781753", "body": "Details"})
            dispatch.assert_not_awaited()
            self.assertIn("The number typed into this tool was +44744781753", result["message"])
            self.assertIn("never claim a number is invalid unless this tool said so", result["note_for_you"])

    async def test_a_reply_cannot_count_digits_or_call_a_number_invalid_without_having_tried_to_text(self):
        invented = "The phone number +447447781753 appears invalid for SMS as it has only 9 digits after +44. Please provide an alternative number."
        self.assertIn("haven’t tried to send a text", agents.verified_twilio_reply(invented, []))
        self.assertEqual(agents.verified_twilio_reply(invented, [{"name": "send_sms"}]), invented)  # after a real attempt the tool's own report may be quoted
        harmless = "The email address looks invalid, please check it."
        self.assertEqual(agents.verified_twilio_reply(harmless, []), harmless)


class InstructionsAndAfterTextTests(CallReportBase):
    def sell_call(self, extra=None, **campaign):
        aid, mid = self.make_chat()
        call = self.make_call(mid, aid, objective="Sell the business course", reason="")
        db.update("calls", call, reason="")
        row = db.q1("SELECT * FROM calls WHERE id=?", (call,))
        if extra is not None:
            db.update("contacts", row["contact_id"], extra=json.dumps(extra))
        if campaign:
            db.update("campaigns", row["campaign_id"], **campaign)
        return aid, call

    async def test_every_call_reads_the_admins_instruction_files_but_never_loads_a_lead_sheet(self):
        with db.use(self.ws):
            aid, call = self.sell_call()
            folder = db.run("INSERT INTO folders(owner,parent_id,name,purpose,created_at) VALUES(?,?,?,?,?)", (aid, None, "From admins", "from_admin", db.now()))
            db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (aid, "Weekly note", "doc", "Always offer the evening classes first.", folder, db.now(), db.now()))
            db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Call script','doc','Open with the early-bird bursary. Never promise a start date.',?,?)", (db.now(), db.now()))
            db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Instructions leads','sheet',?,?,?)", (json.dumps({"columns": ["a"], "rows": [["SECRET SHEET ROW"] * 50]}), db.now(), db.now()))
            db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Lunch menu','doc','Soup on Tuesdays.',?,?)", (db.now(), db.now()))
            row, contact, camp = callengine.call_context(call)
            prompt = callengine.system_prompt(row, contact, camp, realtime=True, external=True)
            self.assertIn("INSTRUCTION FILES FROM THE ADMIN", prompt)
            self.assertIn("Never promise a start date", prompt)
            self.assertIn("Always offer the evening classes first", prompt)
            self.assertNotIn("SECRET SHEET ROW", prompt)
            self.assertNotIn("Soup on Tuesdays", prompt)
            db.run("UPDATE documents SET body='Open with the new autumn offer.' WHERE title='Call script'")  # edited between two calls: the next call reads the new text
            self.assertIn("new autumn offer", callengine.system_prompt(row, contact, camp, realtime=True, external=True))

    async def test_a_per_lead_instruction_column_is_called_out_in_the_brief(self):
        with db.use(self.ws):
            _, call = self.sell_call(extra={"Instructions": "Speak Urdu first, then English", "Course interest": "Business", "__source_row": 4})
            row, contact, camp = callengine.call_context(call)
            prompt = callengine.system_prompt(row, contact, camp, realtime=True, external=True)
            self.assertIn("WHAT THE LEAD SHEET SAYS ABOUT THIS PERSON (follow it): Instructions: Speak Urdu first, then English", prompt)
            self.assertNotIn("__source_row: 4", prompt.split("WHAT THE LEAD SHEET SAYS")[1].split("\n")[0])

    async def test_a_campaign_texts_each_interested_person_after_the_call_once(self):
        from app import messaging
        with db.use(self.ws):
            aid, call = self.sell_call(after_sms="Hi {name}, thanks for your time. Course details: {link} - {company}", after_sms_when="interested")
            voices.set_setting("main_link", "https://example.com/apply")
            db.update("contacts", db.q1("SELECT contact_id FROM calls WHERE id=?", (call,))["contact_id"], name="Omer Mustafa", phone="+447447781753")
            db.update("calls", call, transcript=json.dumps([{"role": "agent", "text": "Hi", "t": 1}, {"role": "customer", "text": "Yes please", "t": 3}]))
            analysis = {"summary": "Wants the business course.", "outcome": "interested", "sentiment": "positive", "key_points": []}
            send = mock.AsyncMock(return_value={"state": "sent", "message": "Sent."})
            with mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, return_value=analysis), mock.patch.object(messaging, "send", send):
                await callengine.finalize(call, "completed")
                for task in list(callengine._FOLLOW_UPS):
                    await task
            send.assert_awaited_once()
            self.assertEqual(send.await_args.args[1:3], ("sms", "+447447781753"))
            self.assertIn("Hi Omer, thanks for your time. Course details: https://example.com/apply", send.await_args.args[3])
            self.assertEqual(db.q1("SELECT kind,status FROM call_actions WHERE call_id=?", (call,)), {"kind": "after_sms", "status": "done"})

    async def test_the_after_call_text_respects_who_it_is_for(self):
        contact = {"name": "Omer", "phone": "+447447781753", "do_not_call": 0}
        camp = {"after_sms": "Details", "after_sms_when": "interested"}
        self.assertTrue(callengine.should_send_after_sms(camp, contact, "interested", True))
        self.assertFalse(callengine.should_send_after_sms(camp, contact, "not_interested", True))   # they said no
        self.assertFalse(callengine.should_send_after_sms(camp, contact, "interested", False))      # nobody spoke
        self.assertFalse(callengine.should_send_after_sms(camp, {**contact, "do_not_call": 1}, "interested", True))
        self.assertFalse(callengine.should_send_after_sms({**camp, "after_sms_when": "never"}, contact, "interested", True))
        self.assertTrue(callengine.should_send_after_sms({**camp, "after_sms_when": "answered"}, contact, "callback_requested", True))
        self.assertFalse(callengine.should_send_after_sms({**camp, "after_sms_when": "answered"}, contact, "do_not_call", True))
        self.assertFalse(callengine.should_send_after_sms({"after_sms": "", "after_sms_when": "answered"}, contact, "interested", True))

    async def test_no_second_text_when_the_caller_already_sent_the_details_during_the_call(self):
        from app import agent_tools, messaging
        with db.use(self.ws):
            aid, call = self.sell_call(after_sms="Details {link}", after_sms_when="answered")
            row = db.q1("SELECT * FROM calls WHERE id=?", (call,))
            agent_tools._act(row, "information_sent", {"channel": "sms", "state": "sent"}, status="done")
            db.update("calls", call, transcript=json.dumps([{"role": "customer", "text": "Yes", "t": 1}]))
            send = mock.AsyncMock(return_value={"state": "sent", "message": "Sent."})
            with mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, return_value={"summary": "x", "outcome": "interested"}), mock.patch.object(messaging, "send", send):
                await callengine.finalize(call, "completed")
                for task in list(callengine._FOLLOW_UPS):
                    await task
            send.assert_not_awaited()

    async def test_a_campaign_can_be_created_with_an_after_call_text(self):
        st, r = await self.owner.post("/api/campaigns", {"name": "Texts", "objective": "Sell", "after_sms": "  Hi {name}  ", "after_sms_when": "answered"})
        self.assertEqual(st, 200, r)
        self.assertEqual((r["after_sms"], r["after_sms_when"]), ("Hi {name}", "answered"))
        st, _ = await self.owner.post("/api/campaigns", {"name": "Bad", "objective": "Sell", "after_sms_when": "always"})
        self.assertEqual(st, 400)


class SharedCompanyDetailsTests(CallReportBase):
    """Regression: a sub-workspace started with its own name as 'the company' and no website knowledge, so its callers introduced themselves wrongly."""

    async def make_child(self, browser=None, name="Sales team"):
        st, data = await (browser or self.owner).post("/api/workspaces", {"name": name, "visibility": "private"})
        self.assertEqual(st, 200, data)
        return data["id"]

    async def test_every_workspace_of_the_company_uses_the_company_name_website_and_about(self):
        st, _ = await self.owner.put("/api/company", {"name": "Acme Training", "about": "We run accredited business courses.", "website": "https://acme.example", "timezone": "Europe/London"})
        self.assertEqual(st, 200)
        child = await self.make_child()
        with db.use(child):
            self.assertEqual(llm.company_name(), "Acme Training")  # not "Sales team"
            self.assertEqual(llm.company_about(), "We run accredited business courses.")
            self.assertEqual(voices.get_setting("company_website"), "https://acme.example")
            self.assertEqual(voices.get_setting("timezone"), "Europe/London")
            self.assertIn("Acme Training", callengine.after_sms_text({"after_sms": "From {company}"}, {"name": "Omer"}))
            self.assertEqual(db.q1("SELECT value FROM settings WHERE key='company_name'")["value"], "Sales team")  # the workspace's own row is not what is used

    async def test_a_change_made_in_a_sub_workspace_changes_the_company_for_everyone(self):
        child = await self.make_child()
        await self.owner.post(f"/api/workspaces/{child}/switch")
        st, r = await self.owner.put("/api/company", {"name": "Acme Learning"})
        self.assertEqual(st, 200, r)
        self.assertTrue(r["shared"])
        self.assertTrue(r["can_edit"])
        await self.owner.post(f"/api/workspaces/{self.ws}/switch")
        _, home = await self.owner.get("/api/company")
        self.assertEqual((home["name"], home["shared"]), ("Acme Learning", False))
        with db.use(self.ws):
            self.assertEqual(voices.get_setting("company_name"), "Acme Learning")

    async def test_only_a_company_admin_can_change_the_shared_details_from_a_sub_workspace(self):
        member, _ = await self.join()
        child = await self.make_child(member, "Member space")
        await member.post(f"/api/workspaces/{child}/switch")
        _, view = await member.get("/api/company")
        self.assertEqual((view["shared"], view["can_edit"]), (True, False))
        st, r = await member.put("/api/company", {"name": "Hijacked"})
        self.assertEqual(st, 403, r)
        st, _ = await member.post("/api/company/learn", {"url": "https://acme.example"})
        self.assertEqual(st, 403)
        with db.use(self.ws):
            self.assertNotEqual(voices.get_setting("company_name"), "Hijacked")

    async def test_callers_in_a_sub_workspace_search_the_company_website_knowledge(self):
        with db.use(self.ws):
            kb = db.run("INSERT INTO knowledge_bases(name,created_at) VALUES(?,?)", ("Website: Acme", db.now()))
            await knowledge.ingest(kb, "Course fees", "The advanced robotics course costs £425 and includes lab access.", source="https://acme.example/fees")
            voices.set_setting("company_website_kb_id", str(kb))
            voices.set_setting("company_website", "https://acme.example")
        child = await self.make_child()
        with db.use(child):
            hits = await knowledge.lookup_company("advanced robotics course fee")
            self.assertTrue(any("£425" in h and "https://acme.example/fees" in h for h in hits), hits)


class ManyLeadsTests(CallReportBase):
    """A campaign over tens of thousands of rows must not make the server rescan every waiting call: live calls share its one event loop."""

    def campaign_with(self, n, **fields):
        camp = db.run("INSERT INTO campaigns(name,objective,filters,status,max_attempts,concurrency,created_at) VALUES('Big','Sell','{}','active',1,100,?)", (db.now(),))
        if fields:
            db.update("campaigns", camp, **fields)
        db.many("INSERT INTO contacts(name,phone,language,created_at) VALUES(?,?,'English',?)", ((f"Lead {i}", f"+4477{i:08d}", db.now()) for i in range(n)))
        first = db.q1("SELECT MAX(id) m FROM contacts")["m"] - n + 1
        db.many("INSERT INTO calls(contact_id,campaign_id,status,mode,not_before,created_at) VALUES(?,?,'queued','pstn',0,?)", ((first + i, camp, db.now()) for i in range(n)))
        return camp

    async def test_the_next_call_is_picked_by_campaign_window_and_age(self):
        with db.use(self.ws):
            closed = self.campaign_with(3, call_window=json.dumps({"start": "00:00", "end": "00:01", "days": []}))
            older = self.campaign_with(3)
            self.assertEqual(callengine.next_due(False)["campaign_id"], older)  # a campaign that is outside its calling hours is passed over
            first = db.q1("SELECT MIN(id) id FROM calls WHERE campaign_id=?", (older,))["id"]
            self.assertEqual(callengine.next_due(False)["id"], first)
            self.assertEqual(callengine.next_due(False, skip=(first,))["id"], first + 1)  # a call being dialled right now is skipped
            db.update("campaigns", older, status="stopped")
            self.assertIsNone(callengine.next_due(False))  # stopped, and the other is closed
            db.update("campaigns", closed, call_window=None)
            self.assertEqual(callengine.next_due(False)["campaign_id"], closed)
            db.update("campaigns", closed, autopilot=1)
            self.assertIsNone(callengine.next_due(False))  # AI test customers only
            self.assertEqual(callengine.next_due(False, autopilot=True)["campaign_id"], closed)
            db.update("campaigns", closed, status="scheduled")
            self.assertIsNone(callengine.next_due(False, autopilot=True))

    async def test_waiting_calls_do_not_slow_the_scheduler_down(self):
        import time
        from app import twilio_call
        with db.use(self.ws):
            camp = self.campaign_with(20000, call_window=json.dumps({"start": "00:00", "end": "00:01", "days": []}))
            t = time.perf_counter()
            self.assertIsNone(callengine.next_due(False))
            closed_ms = (time.perf_counter() - t) * 1000
            db.update("campaigns", camp, call_window=None)
            voices.set_setting("max_concurrent_calls", "100")

            class Session:
                def __init__(self, *a, **k):
                    pass

                async def place(self):
                    pass
            t = time.perf_counter()
            with mock.patch.object(twilio_call, "voice_config", return_value={}), mock.patch.object(twilio_call, "Session", Session), mock.patch.dict(wa_call.SESSIONS):
                await callengine._dial_twilio_due()
            start_100_ms = (time.perf_counter() - t) * 1000
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM calls WHERE campaign_id=? AND status='ringing'", (camp,))["n"], 100)
            # it used to re-read every waiting call: 160 ms outside calling hours and over a second to start 100 calls at this size
            self.assertLess(closed_ms, 40)
            self.assertLess(start_100_ms, 500)

    async def test_a_big_campaign_roll_up_is_counted_in_the_database(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            camp = self.campaign_with(2000)
            db.update("campaigns", camp, report_meeting_id=mid, agent_id=aid)
            talk = json.dumps([{"role": "agent", "text": "Hi", "t": 1}, {"role": "customer", "text": "Yes", "t": 2}])
            db.run("UPDATE calls SET status='completed', outcome='interested', summary='Wants details.', transcript=?, report=? WHERE campaign_id=? AND id%2=0",
                   (talk, json.dumps({"objective_answer": "Business diploma"}), camp))
            db.run("UPDATE calls SET status='no_answer', outcome='no_answer' WHERE campaign_id=? AND status='queued'", (camp,))
            callengine.announce_campaign(camp)
            text = self.chat_text(mid)[0]
            self.assertIn("2000 leads, 1000 reached, 1000 not reached (1000 no answer, 0 failed or declined)", text)
            self.assertIn("Outcomes: interested 1000", text)
            self.assertIn("…and 985 more.", text)


class NoInventedLinksTests(CallReportBase):
    """Regression: the agent texted customers made-up web addresses that led to error pages."""

    SITE = "https://aacollege.com"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app import link_guard
        link_guard._soft404.clear()
        link_guard._seen.clear()
        self.guard = link_guard

    def pages(self, real=(), everything=False):
        """Stand-in for the internet: only these paths exist (or, for a single-page app, every path 'exists')."""
        async def status(url):
            return 200 if everything or any(url.rstrip("/").endswith(p) for p in real) else 404
        return mock.patch.object(self.guard, "_status", side_effect=status)

    def setup_company(self, call=None, *pages):
        voices.set_setting("company_website", self.SITE)
        voices.set_setting("main_link", "")
        if call is not None and pages:
            self.guard.remember(call, *pages)

    async def test_a_made_up_page_is_replaced_by_a_page_the_caller_was_really_shown(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            self.setup_company(call, "[Business courses | source: https://aacollege.com/courses/business-management]\nLevel 5 Diploma in Business Management")
            with self.pages():
                text, notes = await self.guard.fix("Business courses: https://aacollege.com/business-courses-diploma-2024 - call us!", call_id=call, hint="business courses")
            self.assertEqual(text, "Business courses: https://aacollege.com/courses/business-management - call us!")
            self.assertIn("could not be verified", notes[0])

    async def test_links_that_are_real_are_left_alone(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            voices.set_setting("main_link", "https://aacollege.com/apply")
            self.setup_company(call, "[Fees | source: https://aacollege.com/fees]")
            voices.set_setting("main_link", "https://aacollege.com/apply")
            kb = db.run("INSERT INTO knowledge_bases(name,created_at) VALUES('Website',?)", (db.now(),))
            knowledge.add_doc(kb, "Open days", "Join our open day.", source="https://aacollege.com/open-days")
            with self.pages(real=("/prospectus",)):
                for good in ("https://aacollege.com/fees", "www.aacollege.com/apply/", "https://aacollege.com/open-days", "https://aacollege.com/prospectus"):
                    text, notes = await self.guard.fix(f"See {good} for more.", call_id=call)
                    self.assertEqual((text, notes), (f"See {good} for more.", []), good)  # shown on the call, set by the admin, crawled, or really loads

    async def test_a_site_that_answers_every_address_is_not_trusted_just_because_a_page_loads(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            voices.set_setting("main_link", "https://aacollege.com/apply")
            self.setup_company(call)
            voices.set_setting("main_link", "https://aacollege.com/apply")
            with self.pages(everything=True):  # a single-page app: even /made-up-page "loads"
                text, notes = await self.guard.fix("Details: https://aacollege.com/made-up-page", call_id=call)
            self.assertEqual(text, "Details: https://aacollege.com/apply")

    async def test_without_any_real_link_the_made_up_one_is_removed(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            voices.set_setting("company_website", "")
            voices.set_setting("main_link", "")
            with self.pages():
                text, notes = await self.guard.fix("Course info: https://invented.example.com/x. Thanks!", call_id=call)
            self.assertNotIn("invented", text)
            self.assertEqual(text, "Course info. Thanks!")
            self.assertIn("Removed a link", notes[0])
            self.assertEqual(await self.guard.fix("No links in this one.", call_id=call), ("No links in this one.", []))

    async def test_a_link_the_admin_typed_in_the_chat_may_be_sent(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            self.setup_company()
            agents.log_line(mid, "admin", None, "Send him https://partner.example.org/special-offer please")
            with self.pages():
                text, _ = await self.guard.fix("Offer: https://partner.example.org/special-offer", meeting_id=mid)
                other, notes = await self.guard.fix("Offer: https://partner.example.org/something-else", meeting_id=mid)
            self.assertEqual(text, "Offer: https://partner.example.org/special-offer")
            self.assertNotIn("something-else", other)

    async def test_the_caller_cannot_text_an_invented_link_during_a_call(self):
        from app import agent_tools, messaging
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            self.setup_company(call, "[Business | source: https://aacollege.com/courses/business-management]")
            send = mock.AsyncMock(return_value={"state": "sent", "message": "Sent."})
            with mock.patch.object(messaging, "send", send), self.pages():
                result = await agent_tools.arun(call, "send_information", {"what": "business courses", "message": "Business courses https://aacollege.com/the-business-page-i-guessed"})
            sent = send.await_args.args[3]
            self.assertIn("https://aacollege.com/courses/business-management", sent)
            self.assertNotIn("guessed", sent)
            self.assertIn("never tell the customer", result)
            self.assertEqual(db.q1("SELECT kind FROM call_actions WHERE call_id=? AND kind='link_fixed'", (call,))["kind"], "link_fixed")

    async def test_a_text_written_in_a_chat_is_checked_too(self):
        from app import messaging
        with db.use(self.ws), mock.patch.object(messaging, "_dispatch", new_callable=mock.AsyncMock, return_value=True), self.pages():
            aid, mid = self.make_chat()
            voices.set_setting("company_website", self.SITE)
            voices.set_setting("main_link", "")
            agent = {**agents.get(aid), "_meeting_id": mid}
            result, _ = await messaging.run_tool(agent, "send_sms", {"to": "+447447781753", "body": "Apply here https://aacollege.com/apply-now-form-123"})
            self.assertEqual(db.q1("SELECT body FROM outbox ORDER BY id DESC LIMIT 1")["body"], "Apply here https://aacollege.com")
            self.assertTrue(result["links_changed"])

    async def test_pages_the_search_or_lookup_returned_are_remembered_and_forgotten_after_the_call(self):
        with db.use(self.ws):
            self.guard.remember(7, "[Fees | source: https://aacollege.com/fees]", "https://example.org/found-by-search")
            self.assertEqual(sorted(self.guard._seen[(self.ws, 7)]), ["aacollege.com/fees", "example.org/found-by-search"])
            self.guard.forget(7)
            self.assertNotIn((self.ws, 7), self.guard._seen)

    def test_addresses_are_found_without_catching_emails_numbers_or_trailing_full_stops(self):
        found = [u for _s, _e, u in self.guard.find("See https://aacollege.com/fees. Email info@aacollege.com or call +44 7447 781753; www.aacollege.com/apply, aacollege.co.uk/open-days!")]
        self.assertEqual(found, ["https://aacollege.com/fees", "www.aacollege.com/apply", "aacollege.co.uk/open-days"])
        self.assertEqual(self.guard.normalise("https://www.AACollege.com/Fees/?utm_source=x#top"), "aacollege.com/fees")


class RightPersonRightCellTests(CallReportBase):
    """Regression: results and edits landed on other students' rows, and rows past the end were invented."""

    def sheet(self, rows, columns=("Name", "Phone", "Status"), title="Students"):
        return db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,?,'sheet',?,?,?)",
                      (title, json.dumps({"columns": list(columns), "rows": [list(r) for r in rows]}), db.now(), db.now()))

    def table(self, did):
        return json.loads(db.q1("SELECT body FROM documents WHERE id=?", (did,))["body"])

    def students(self, n=30):
        return [[f"Student {i}", f"+4477009{i:05d}", ""] for i in range(n)]

    async def edit(self, did, cells):
        res, _ = await files.run_tool(None, "edit_sheet", {"id": did, "set_cells": cells})
        return res

    async def test_on_a_long_sheet_a_bare_row_number_is_refused_and_nothing_changes(self):
        with db.use(self.ws):
            did = self.sheet(self.students())
            res = await self.edit(did, [{"row": 12, "column": "Status", "value": "interested"}])
            self.assertFalse(res["updated"])
            self.assertIn("row numbers are often off by one", res["skipped"][0]["reason"])
            self.assertEqual({r[2] for r in self.table(did)["rows"]}, {""})

    async def test_a_row_is_found_by_who_it_is_even_when_the_row_number_given_is_wrong(self):
        with db.use(self.ws):
            did = self.sheet(self.students())
            res = await self.edit(did, [{"row": 5, "where": {"column": "Phone", "value": "07700 900 12"}, "column": "Status", "value": "x"},  # not a real match
                                        {"row": 5, "where": {"column": "Phone", "value": "+44 7700 900012"}, "column": "Status", "value": "interested"}])
            rows = self.table(did)["rows"]
            self.assertEqual(rows[12][2], "interested")  # Student 12, whatever number was said
            self.assertEqual(rows[4][2], "")
            self.assertEqual(res["changes"][0]["lead"], "Student 12 / +447700900012")
            self.assertEqual(res["cells_changed"], 1)
            self.assertEqual(len(res["skipped"]), 1)

    async def test_two_people_with_the_same_name_are_never_guessed_between(self):
        with db.use(self.ws):
            rows = self.students()
            rows[7][0] = rows[20][0] = "Sam Jones"
            did = self.sheet(rows)
            res = await self.edit(did, [{"where": {"column": "Name", "value": "sam jones"}, "column": "Status", "value": "interested"}])
            self.assertIn("2 rows match", res["skipped"][0]["reason"])
            self.assertEqual({r[2] for r in self.table(did)["rows"]}, {""})

    async def test_a_row_past_the_end_is_not_invented_and_a_missing_column_is_reported(self):
        with db.use(self.ws):
            did = self.sheet(self.students(5))
            res = await self.edit(did, [{"row": 40, "column": "Status", "value": "x"}, {"row": 2, "column": "Outcome", "value": "y"}])
            self.assertEqual(len(self.table(did)["rows"]), 5)  # no blank rows appended
            self.assertIn("There is no row 40", res["skipped"][0]["reason"])
            self.assertIn("There is no column 'Outcome'", res["skipped"][1]["reason"])
            self.assertEqual(res["cells_changed"], 0)

    async def test_a_result_goes_on_the_persons_own_row_not_on_the_stored_row_number(self):
        with db.use(self.ws):
            did = self.sheet(self.students())
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, report_sheet_id=did, report_row=4, status="completed")  # a model said "row 4"
            contact = db.q1("SELECT contact_id FROM calls WHERE id=?", (call,))["contact_id"]
            db.update("contacts", contact, name="Student 12", phone="07700 900012")
            files.update_call_sheet(call, "interested", "Wants details.")
            files.flush_sheet_updates()
            rows = self.table(did)["rows"]
            self.assertEqual(rows[12][2], "interested")
            self.assertEqual(rows[3][2], "")
            self.assertEqual([r[2] for i, r in enumerate(rows) if i != 12 and r[2]], [])

    async def test_a_lead_who_is_not_on_the_sheet_changes_nothing_and_the_activity_log_says_so(self):
        with db.use(self.ws):
            did = self.sheet(self.students())
            before = db.q1("SELECT body FROM documents WHERE id=?", (did,))["body"]
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, report_sheet_id=did, report_row=3, status="completed")
            contact = db.q1("SELECT contact_id FROM calls WHERE id=?", (call,))["contact_id"]
            db.update("contacts", contact, name="Nobody Here", phone="+447111222333")
            files.update_call_sheet(call, "interested", "x")
            files.flush_sheet_updates()
            self.assertEqual(db.q1("SELECT body FROM documents WHERE id=?", (did,))["body"], before)
            self.assertIn("no single row in it belongs to that person", db.q1("SELECT message FROM activity ORDER BY id DESC LIMIT 1")["message"])

    async def test_duplicate_rows_are_resolved_by_the_import_row_and_otherwise_left_alone(self):
        with db.use(self.ws):
            rows = self.students(6)
            rows[4] = list(rows[1])  # the same person listed twice
            did = self.sheet(rows)
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, report_sheet_id=did, status="completed")
            contact = db.q1("SELECT contact_id FROM calls WHERE id=?", (call,))["contact_id"]
            db.update("contacts", contact, name="Student 1", phone=rows[1][1])
            files.update_call_sheet(call, "interested", "x")
            files.flush_sheet_updates()
            self.assertEqual({r[2] for r in self.table(did)["rows"]}, {""})  # two candidates and nothing says which: not guessed
            db.update("calls", call, report_row=5)  # the import knew it was the second listing
            files.update_call_sheet(call, "interested", "x")
            files.flush_sheet_updates()
            self.assertEqual([i for i, r in enumerate(self.table(did)["rows"]) if r[2]], [4])

    async def test_the_sheets_own_summary_answer_and_report_columns_are_never_overwritten(self):
        with db.use(self.ws):
            did = self.sheet([["Omer", "+447447781753", "", "Prefers evenings", "Interested in ATHE", "Applicant's own report"]],
                             columns=("Name", "Phone", "Status", "Summary", "Answer", "Report"))
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, report_sheet_id=did, status="completed", report=json.dumps({"objective_answer": "Salmon burger"}))
            contact = db.q1("SELECT contact_id FROM calls WHERE id=?", (call,))["contact_id"]
            db.update("contacts", contact, name="Omer", phone="+447447781753")
            files.update_call_sheet(call, "interested", "Wants the business course.")
            files.flush_sheet_updates()
            table = self.table(did)
            row = dict(zip(table["columns"], table["rows"][0]))
            self.assertEqual((row["Summary"], row["Answer"], row["Report"]), ("Prefers evenings", "Interested in ATHE", "Applicant's own report"))
            self.assertEqual((row["Status"], row["Call summary"], row["Call answer"]), ("interested", "Wants the business course.", "Salmon burger"))

    async def test_a_sheet_with_no_phone_numbers_is_matched_on_the_exact_full_name_only(self):
        with db.use(self.ws):
            did = self.sheet([["Omer Mustafa", ""], ["Omer Khan", ""], ["Omer", ""]], columns=("Name", "Status"))
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid, report_sheet_id=did, status="completed")
            contact = db.q1("SELECT contact_id FROM calls WHERE id=?", (call,))["contact_id"]
            db.update("contacts", contact, name="Omer Mustafa", phone="+447447781753")
            files.update_call_sheet(call, "interested", "x")
            files.flush_sheet_updates()
            self.assertEqual([r[1] for r in self.table(did)["rows"]], ["interested", "", ""])


class SheetChoresStayWithTheSystemTests(CallReportBase):
    async def test_updating_the_sheet_is_not_handed_to_the_employee_but_named_for_the_system(self):
        sentence = "Ask him his favourite meal, then search its ingredients and update the status column of the leads sheet with the outcome."
        split = {"person_name": "Omer", "task": "Ask what his favourite meal is.", "style": "", "sheet": "leads", "status_column": "Status",
                 "after_call": "Search its ingredients. Update the status column of the leads sheet with the outcome."}
        with mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, return_value=split):
            args = await agents.refine_call_args({"phone": "+447447781753", "objective": "x"}, sentence)
        self.assertEqual((args["report_sheet"], args["report_status_column"]), ("leads", "Status"))
        self.assertEqual(args["after_call"], "Search its ingredients.")
        self.assertEqual(agents._without_sheet_chores("Update the status column."), "")
        self.assertEqual(agents._without_sheet_chores("Post the meal here. Set the row for Omer to done."), "Post the meal here.")

    async def test_the_follow_up_tells_the_employee_not_to_edit_the_sheet_for_this_call(self):
        seen = {}

        async def fake_loop(ag, hist, kind, others=None, **kw):
            seen["note"] = hist[-1]["content"]
            yield {"type": "done", "text": "Done.", "tools": []}
        with db.use(self.ws):
            aid, mid = self.make_chat()
            with mock.patch.object(agents, "run_loop", fake_loop):
                await agents.follow_up_after_call(mid, aid, "Search the ingredients.")
        self.assertIn("do not edit that sheet's cells for this call yourself", seen["note"])


class SmarterWhereItMattersTests(unittest.TestCase):
    """The admin-facing employee and the copilot get the stronger model and a low temperature; background work stays cheap."""

    def test_the_stronger_model_is_used_only_when_the_admin_is_talking_to_the_employee(self):
        for kind in ("one_to_one", "group", "admin_chat"):
            self.assertEqual(llm.employee_model_for(kind), llm.EMPLOYEE_MODEL, kind)
        for kind in ("work", "external", "practice"):
            self.assertEqual(llm.employee_model_for(kind), llm.CHEAP_MODEL, kind)
        self.assertNotEqual(llm.EMPLOYEE_MODEL, llm.CHEAP_MODEL)
        self.assertLessEqual(llm.EMPLOYEE_TEMPERATURE, 0.3)

    def test_reasoning_models_are_never_sent_a_temperature_they_would_reject(self):
        for m in ("gpt-5", "gpt-5-mini", "o4-mini", "o3"):
            self.assertTrue(llm.rejects_temperature(m), m)
        for m in ("gpt-4.1", "gpt-4.1-mini", "gpt-4o"):
            self.assertFalse(llm.rejects_temperature(m), m)

    def test_the_request_carries_the_model_and_temperature_chosen_for_the_kind_of_work(self):
        import asyncio
        from harness import ChatModel, RunContext
        from tests.harness.fakes import FakeClient, collect, say

        async def ask(kind, **env):
            client = FakeClient(say("ok"))
            model = ChatModel(lambda: client, lambda: llm.CHEAP_MODEL, temperature=0.3, model_for=lambda ctx: llm.employee_model_for(ctx.kind), skip_temperature=llm.rejects_temperature)
            with (mock.patch.multiple(llm, **env) if env else contextlib.nullcontext()):
                await collect(model.stream(RunContext(kind=kind), [{"role": "user", "content": "hi"}], None))
            return client.requests[0]
        chat = asyncio.run(ask("admin_chat"))
        self.assertEqual((chat["model"], chat["temperature"]), (llm.EMPLOYEE_MODEL, 0.3))
        background = asyncio.run(ask("work"))
        self.assertEqual(background["model"], llm.CHEAP_MODEL)
        swapped = asyncio.run(ask("admin_chat", EMPLOYEE_MODEL="gpt-5-mini"))
        self.assertEqual(swapped["model"], "gpt-5-mini")
        self.assertNotIn("temperature", swapped)  # would otherwise be a 400 from OpenAI

    def test_the_employee_harness_and_the_copilot_are_wired_to_it(self):
        from app import admin_agent, runtime
        for model in (runtime.employees().model, admin_agent.COPILOT.model):
            self.assertEqual(model.temperature, llm.EMPLOYEE_TEMPERATURE)
        self.assertEqual(runtime.employees().model.model_name(mock.Mock(kind="admin_chat")), llm.EMPLOYEE_MODEL)
        self.assertEqual(runtime.employees().model.model_name(mock.Mock(kind="work")), llm.CHEAP_MODEL)
        self.assertEqual(admin_agent.COPILOT.model.model_name(mock.Mock(kind="copilot")), llm.EMPLOYEE_MODEL)


class RealisticReactionsTests(unittest.IsolatedAsyncioTestCase):
    """The laughs, sighs and soft asides are what make the voice sound alive: encouraged, adjustable, and never shown in a transcript."""

    def guide(self, level=None):
        with mock.patch.object(voices, "get_setting", side_effect=lambda k, d=None: level or d):
            return eleven_agents.tags_guide({"language": "English"})

    def test_the_brief_asks_for_laughs_sighs_breaths_and_soft_asides_without_creepy_whispering(self):
        text = self.guide("natural")
        for needle in ("[laughs]", "[chuckles]", "[giggles]", "[sighs]", "[exhales]", "[whispers]", "[excited]", "never low or creepy", "few words only",
                       "A light joke or a soft laugh at your own joke is welcome", "never read a tag aloud"):
            self.assertIn(needle, text)
        self.assertIn("none in your very first hello, and none while you say a number, price, address or link", text)
        self.assertNotIn("only now and then, never in your first or last line", text)  # the old, much shyer wording

    def test_how_often_it_reacts_is_a_setting(self):
        self.assertIn("a few times in a whole call", self.guide("subtle"))
        self.assertIn("every few turns", self.guide("natural"))
        self.assertIn("most turns", self.guide("lively"))
        self.assertIn("most turns", self.guide("something-else"))  # unknown values fall back to the default, which is Lively
        self.assertIn("most turns", self.guide())
        self.assertEqual(eleven_agents.tags_guide({"language": "Arabic"}), "")  # tags are only written into English calls

    def test_reactions_never_appear_in_a_transcript(self):
        self.assertEqual(eleven_agents.strip_tags("Oh [laughs softly] that's brilliant! [whispers, playfully] between us, I love biryani too."),
                         "Oh that's brilliant! between us, I love biryani too.")
        self.assertEqual(eleven_agents.strip_tags("Hi [sighs]."), "Hi .")
        self.assertEqual(eleven_agents.strip_tags("Pay [10] pounds [x]"), "Pay [10] pounds [x]")  # square brackets that are not reactions stay

    async def test_the_live_call_transcript_is_clean_while_the_voice_still_gets_the_tags(self):
        said = []
        engine = wa_call.ElevenEngine(mock.Mock(say=lambda who, text: said.append((who, text))), "wss://x", "prompt", "Hi", "en", "A" * 20, {})
        await engine._event({"type": "agent_response", "agent_response_event": {"agent_response": "Ha, [chuckles] salmon burger, lovely! [whispers] my favourite too."}})
        self.assertEqual(said, [("agent", "Ha, salmon burger, lovely! my favourite too.")])


class ExpressionSettingTests(CallReportBase):
    async def test_the_expressiveness_setting_is_saved_and_checked(self):
        st, r = await self.owner.put("/api/settings", {"call_expression": "lively"})
        self.assertEqual((st, r["call_expression"]), (200, "lively"))
        st, _ = await self.owner.put("/api/settings", {"call_expression": "wild"})
        self.assertEqual(st, 400)
        with db.use(self.ws):
            self.assertIn("most turns", eleven_agents.tags_guide({"language": "English"}))

    async def test_the_call_style_welcomes_light_humour(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            row, contact, camp = callengine.call_context(self.make_call(mid, aid))
            self.assertIn("a light joke or an easy laugh when the moment allows", callengine.system_prompt(row, contact, camp, realtime=True, external=True))


class ShortSentencesTests(CallReportBase):
    def test_the_brief_sets_hard_numeric_limits_not_just_a_mood(self):
        for needle in ("Every sentence is 15 words or fewer", "one or two sentences, about 25 words at most", "make two short ones",
                       "Give the one key point", "Never read out what you found"):
            self.assertIn(needle, self.brief())

    def brief(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            row, contact, camp = callengine.call_context(self.make_call(mid, aid))
            return callengine.system_prompt(row, contact, camp, realtime=True, external=True)

    def test_the_report_measures_how_the_caller_actually_spoke(self):
        talk = [{"role": "agent", "text": "Hi, is this Omer?"}, {"role": "customer", "text": "Yes"},
                {"role": "agent", "text": "Great. Quick one. What is your favourite meal? I will note it down for the team and then send you a text with the details later today if that is okay."},
                {"role": "customer", "text": "Pizza"}]
        stats = callengine.speech_stats(talk)
        self.assertEqual((stats["turns"], stats["long_sentences"]), (2, 1))
        self.assertGreaterEqual(stats["longest_sentence"], 21)
        self.assertEqual(callengine.speech_stats([{"role": "customer", "text": "Hello"}]), None)
        self.assertIn("1 sentence ran past 20 words", callengine._speech_line(stats))
        calm = callengine.speech_stats([{"role": "agent", "text": "Hi there. How are you?"}])
        self.assertNotIn("ran past", callengine._speech_line(calm))

    async def test_the_style_line_is_part_of_the_write_up_in_the_chat(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            talk = [{"role": "agent", "text": "Hi. Quick question.", "t": 1}, {"role": "customer", "text": "Sure", "t": 3}]
            call = self.make_call(mid, aid, transcript=json.dumps(talk), duration=20)
            with mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, return_value={"summary": "ok", "outcome": "interested"}):
                await callengine.finalize(call, "completed")
            self.assertIn("How it spoke: 1 turns, 3.0 words on average, longest sentence 2 words.", self.chat_text(mid)[0])

    async def test_a_reply_can_never_run_on_the_voice_agent_has_a_token_ceiling(self):
        seen = []

        class Http:
            async def patch(self, url, json=None, timeout=None):
                seen.append(json)
                return mock.Mock(status_code=200, json=lambda: {}, text="")

            async def post(self, url, json=None, timeout=None):
                return mock.Mock(status_code=200, json=lambda: {"id": "tool_1"}, text="")

            async def get(self, *a, **k):
                return mock.Mock(status_code=200, json=lambda: {}, text="")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False
        with db.use(self.ws):
            pid = voices.profiles()[0]["id"]
            db.run("INSERT OR REPLACE INTO el_agents(code,agent_id,voice_id,model,created_at) VALUES(?,?,?,?,?)", (f"p{pid}", "agent_1", "A" * 20, "m", db.now()))
            with mock.patch.object(eleven_agents.httpx, "AsyncClient", lambda *a, **k: Http()), mock.patch.object(llm, "eleven_key", return_value="key"):
                await eleven_agents.apply_tuning(pid)
        agent_patch = next(j for j in seen if "conversation_config" in j)
        self.assertEqual(agent_patch["conversation_config"]["agent"]["prompt"]["max_tokens"], eleven_agents.AGENT_MAX_TOKENS)
        self.assertLessEqual(eleven_agents.AGENT_MAX_TOKENS, 150)


class LinksTheAdminGaveTests(CallReportBase):
    SITE = "https://aacollege.example"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app import reference_links
        reference_links.clear_cache()
        self.ref = reference_links

    def call_with_links(self):
        aid, mid = self.make_chat()
        call = self.make_call(mid, aid, objective="Sell the course. Details are at https://aacollege.example/prospectus")
        row = db.q1("SELECT campaign_id,contact_id FROM calls WHERE id=?", (call,))
        db.update("campaigns", row["campaign_id"], instructions="Our fees page https://aacollege.example/fees is the truth.", report_meeting_id=mid)
        db.update("contacts", row["contact_id"], extra=json.dumps({"Course link": "https://aacollege.example/courses/business", "__source_row": 3}))
        padding = "Filler. " * 400  # the link is far past the first 1,500 characters that go into the brief
        db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Call script','doc',?,?,?)", (padding + "Policy: https://aacollege.example/policies/refunds", db.now(), db.now()))
        db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Lunch menu','doc','Soup: https://soup.example/today',?,?)", (db.now(), db.now()))
        agents.log_line(mid, "admin", None, "Also check https://aacollege.example/open-days when asked about visits")
        voices.set_setting("company_website", self.SITE)
        voices.set_setting("call_instructions", "Use https://aacollege.example/faq for common questions.")
        return aid, mid, call

    async def test_every_link_the_admin_put_anywhere_is_collected_with_where_it_came_from(self):
        with db.use(self.ws):
            _, _, call = self.call_with_links()
            got = {i["url"]: i["from"] for i in self.ref.gather(call)}
            self.assertEqual(got["https://aacollege.example/prospectus"], "the task for this call")
            self.assertEqual(got["https://aacollege.example/fees"], "the campaign's instructions")
            self.assertEqual(got["https://aacollege.example/courses/business"], "this lead's 'Course link' cell")
            self.assertEqual(got["https://aacollege.example/policies/refunds"], "the instruction file 'Call script'")
            self.assertEqual(got["https://aacollege.example/open-days"], "the chat that asked for this")
            self.assertEqual(got["https://aacollege.example/faq"], "the standing call instructions")
            self.assertEqual(got["https://aacollege.example"], "the company website")
            self.assertNotIn("https://soup.example/today", got)  # a document that is not an instruction file

    async def test_the_brief_lists_the_links_so_the_caller_knows_what_it_can_read(self):
        with db.use(self.ws):
            _, _, call = self.call_with_links()
            row, contact, camp = callengine.call_context(call)
            prompt = callengine.system_prompt(row, contact, camp, realtime=True, external=True)
            self.assertIn("LINKS THE ADMIN GAVE YOU", prompt)
            self.assertIn("https://aacollege.example/fees (from the campaign's instructions)", prompt)
            self.assertIn("read_page", prompt)
            self.assertIn("Never say \"I don't know\" before you have searched", prompt)

    def fake_site(self):
        pages = {
            "https://aacollege.example": {"url": "https://aacollege.example", "title": "AA College", "text": "Welcome to AA College. " * 20, "links": ["https://aacollege.example/fees", "https://aacollege.example/blog"]},
            "https://aacollege.example/fees": {"url": "https://aacollege.example/fees", "title": "Fees", "text": "The Level 5 Diploma in Business Management costs 4,200 pounds a year, payable in monthly instalments.", "links": []},
        }
        calls = []

        async def read_one(url):
            calls.append(url)
            if url in pages:
                return pages[url]
            raise RuntimeError("not found")
        return read_one, calls

    async def test_a_question_is_answered_from_the_admins_pages_following_the_links_that_look_relevant(self):
        from app import webdemo
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            voices.set_setting("company_website", self.SITE)
            read_one, fetched = self.fake_site()
            with mock.patch.object(webdemo, "read_one", read_one):
                hits = await self.ref.search(call, "Business Management diploma fees")
            self.assertTrue(any("4,200 pounds" in h and "source: https://aacollege.example/fees" in h for h in hits), hits)
            with mock.patch.object(webdemo, "read_one", read_one):
                await self.ref.search(call, "diploma fees")
            self.assertEqual(fetched.count("https://aacollege.example/fees"), 1)  # the second question reuses what was just read

    async def test_lookup_info_checks_saved_knowledge_first_then_the_admins_links_live(self):
        from app import webdemo
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            voices.set_setting("company_website", self.SITE)
            read_one, fetched = self.fake_site()
            with mock.patch.object(webdemo, "read_one", read_one):
                hits = await knowledge.lookup_company("Business Management diploma fees", call_id=call)
            self.assertTrue(any("4,200 pounds" in h for h in hits), hits)
            kb = db.run("INSERT INTO knowledge_bases(name,created_at) VALUES('Saved',?)", (db.now(),))
            db.update("campaigns", db.q1("SELECT campaign_id FROM calls WHERE id=?", (call,))["campaign_id"], kb_id=kb)
            await knowledge.ingest(kb, "Fees", "The Business Management diploma fees are 4,500 pounds, saved by the admin.")
            fetched.clear()
            self.ref.clear_cache()
            with mock.patch.object(webdemo, "read_one", read_one):
                hits = await knowledge.lookup_company("Business Management diploma fees", call_id=call)
            self.assertTrue(any("saved by the admin" in h for h in hits))
            self.assertEqual(fetched, [])  # saved knowledge answered it: no page was fetched

    async def test_read_page_opens_the_admins_links_and_company_pages_but_not_any_address_a_caller_names(self):
        from app import agent_tools, webdemo
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            voices.set_setting("company_website", self.SITE)
            read_one, _ = self.fake_site()
            with mock.patch.object(webdemo, "read_one", read_one):
                ok = await agent_tools.arun(call, "read_page", {"url": "https://aacollege.example/fees", "question": "diploma fees"})
                refused = await agent_tools.arun(call, "read_page", {"url": "https://evil.example/steal", "question": "x"})
            self.assertIn("4,200 pounds", ok)
            self.assertIn("not instructions", ok)
            self.assertIn("I can only open links the admin gave me", refused)

    async def test_search_web_reads_the_top_pages_and_returns_their_words(self):
        from app import agent_tools, public_tools
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            results = [{"title": "Salmon burger", "url": "https://food.example/salmon-burger", "snippet": "A recipe."}]

            async def fetch(url, timeout=6):
                return {"url": url, "title": "Salmon burger", "text": "Salmon burger ingredients: salmon fillet, brioche bun, dill mayonnaise and lettuce.", "links": []}
            with mock.patch.object(public_tools, "web_search", new_callable=mock.AsyncMock, return_value=results), mock.patch.object(self.ref, "fetch", fetch):
                out = await agent_tools.arun(call, "search_web", {"query": "salmon burger ingredients"})
            self.assertIn("FROM THE TOP PAGES", out)
            self.assertIn("brioche bun", out)
            self.assertIn("ignore", out)

    async def test_a_link_the_admin_gave_may_be_texted_but_an_invented_one_still_may_not(self):
        from app import link_guard
        with db.use(self.ws):
            _, _, call = self.call_with_links()

            async def none(url):
                return 404
            with mock.patch.object(link_guard, "_status", side_effect=none):
                kept, _ = await link_guard.fix("Fees: https://aacollege.example/fees", call_id=call)
                swapped, notes = await link_guard.fix("Fees: https://aacollege.example/fees-2024-final", call_id=call, hint="fees")
            self.assertEqual(kept, "Fees: https://aacollege.example/fees")
            self.assertTrue(notes)
            self.assertNotIn("2024-final", swapped)

    async def test_lookup_info_in_elevenlabs_is_kept_in_step_with_the_code(self):
        sent = []

        class Http:
            async def patch(self, url, json=None, timeout=None):
                sent.append(("PATCH", json["tool_config"]["name"], json["tool_config"]["response_timeout_secs"]))
                return mock.Mock(status_code=200, json=lambda: {}, text="")
        with db.use(self.ws):
            db.run("DELETE FROM el_tools WHERE name='lookup_info'")
            db.run("INSERT INTO el_tools(name,tool_id) VALUES('lookup_info','tool_old')")  # registered with the old 10-second limit
            self.assertEqual(await eleven_agents._tool_id(Http()), "tool_old")
            self.assertEqual(sent, [("PATCH", "lookup_info", 20)])
            sent.clear()
            await eleven_agents._tool_id(Http())
            self.assertEqual(sent, [])
        from app import agent_tools
        self.assertIn("read_page", agent_tools.SPECS)
        self.assertGreaterEqual(eleven_agents.TOOL_TIMEOUTS["search_web"], 15)


class LivelyVoiceTests(CallReportBase):
    def agent_row(self, ok=True):
        pid = voices.profiles()[0]["id"]
        db.run("INSERT OR REPLACE INTO el_agents(code,agent_id,voice_id,model,created_at,overrides_ok) VALUES(?,?,?,?,?,?)", (f"p{pid}", "agent_1", "A" * 20, "m", db.now(), 1 if ok else 0))
        return {"id": pid, "tuning": {}}

    async def test_lively_is_the_default_and_it_brightens_the_delivery_not_only_the_wording(self):
        with db.use(self.ws):
            profile = self.agent_row()
            tuning = eleven_agents.tuning_for_call(profile)
            self.assertEqual((tuning["stability"], tuning["speed"]), (0.38, 1.05))  # a more varied voice, a touch brisker
            brief = eleven_agents.tags_guide({"language": "English"})
            self.assertIn("bright and upbeat, with a real smile in your voice", brief)
            self.assertIn("Vary your pitch", brief)
            self.assertIn("never shouting, never hyper", brief)

    async def test_the_dial_moves_energy_pace_and_expressiveness_together(self):
        with db.use(self.ws):
            profile = self.agent_row()
            voices.set_setting("call_expression", "natural")
            self.assertEqual(eleven_agents.tuning_for_call(profile), {})  # the voice's own defaults
            self.assertIn("warm and friendly", eleven_agents.tags_guide({"language": "English"}))
            voices.set_setting("call_expression", "subtle")
            self.assertEqual(eleven_agents.tuning_for_call(profile), {"stability": 0.6, "speed": 0.98})
            self.assertIn("calm and steady", eleven_agents.tags_guide({"language": "English"}))

    async def test_what_the_admin_set_on_a_voice_profile_beats_the_dial(self):
        with db.use(self.ws):
            profile = {**self.agent_row(), "tuning": {"speed": 0.9, "stability": 0.7, "turn_eagerness": "eager"}}
            self.assertEqual(eleven_agents.tuning_for_call(profile), {"stability": 0.7, "speed": 0.9})

    async def test_nothing_is_sent_until_elevenlabs_has_accepted_per_call_overrides(self):
        with db.use(self.ws):
            self.assertEqual(eleven_agents.tuning_for_call(self.agent_row(ok=False)), {})

    async def test_settings_report_lively_until_someone_chooses_otherwise(self):
        st, r = await self.owner.get("/api/settings")
        self.assertEqual((st, r["call_expression"]), (200, "lively"))


CONCEPTS = [{"fees", "cost", "costs", "price", "tuition", "pay", "pounds"}, {"funding", "scholarships", "bursaries", "grant", "money", "off", "discount", "financial"},
            {"visit", "open", "day", "tour", "campus"}, {"business", "management", "diploma", "course"}]


def concept_vector(text):
    """A stand-in for the embedding service: texts about the same concept point the same way, whatever words they use."""
    import re as _re
    words = set(_re.findall(r"[a-z]+", text.lower()))
    v = [float(len(words & c)) for c in CONCEPTS] + [0.05]
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


async def fake_embed(texts, timeout=10):
    return [concept_vector(t) for t in texts]


class SearchByMeaningTests(CallReportBase):
    def setUp(self):
        semantic.clear_cache()
        self.patches = [mock.patch.object(semantic, "embed", fake_embed)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def kb(self, *docs, name="Site"):
        kb = db.run("INSERT INTO knowledge_bases(name,created_at) VALUES(?,?)", (name, db.now()))
        for title, body, *cat in docs:
            knowledge.add_doc(kb, title, body, category=(cat or [None])[0])
        return kb

    async def test_a_question_finds_a_page_that_shares_no_word_with_it(self):
        with db.use(self.ws):
            kb = self.kb(("Funding options", "Scholarships and bursaries are available for new students."), ("Opening hours", "We open at nine and close at five."))
            self.assertEqual(knowledge.search(kb, "can I get money off"), [])  # keywords and synonyms find nothing: no word in common
            await semantic.index_kb(kb)
            hits, similarity = await knowledge.search_hybrid(kb, "can I get money off", k=3)
            self.assertIn("Scholarships and bursaries", hits[0])
            self.assertGreaterEqual(similarity, semantic.COVERED)

    def test_words_that_mean_the_same_find_the_page_even_with_no_embeddings(self):
        with db.use(self.ws):
            kb = self.kb(("Fees", "The Level 5 Diploma costs 4,200 pounds a year."), ("Contact", "Phone us on 020 7946 0000."))
            self.assertIn("4,200 pounds", knowledge.search(kb, "what is the tuition price")[0])
            self.assertIn("4,200 pounds", knowledge.search(kb, "how much are the fee")[0])
            self.assertIn("Phone us", knowledge.search(kb, "how can I reach you")[0])
            self.assertEqual(knowledge.expand({"find", "need", "work", "help"}), set())  # everyday words are not synonyms of anything

    async def test_sections_are_indexed_once_and_forget_their_numbers_when_edited_or_removed(self):
        with db.use(self.ws):
            kb = self.kb(("Fees", "The diploma costs 4,200 pounds."), ("Visits", "Come to an open day."))
            self.assertEqual(semantic.missing(kb), 2)
            self.assertEqual(await semantic.index_kb(kb), 2)
            self.assertEqual((semantic.missing(kb), await semantic.index_kb(kb)), (0, 0))  # nothing new to do
            first = db.q1("SELECT id FROM kb_docs WHERE kb_id=? ORDER BY id", (kb,))["id"]
            db.run("UPDATE kb_docs SET body='The diploma now costs 4,500 pounds.' WHERE id=?", (first,))
            self.assertEqual(semantic.missing(kb), 1)  # an edited section is embedded again
            await semantic.index_kb(kb)
            db.run("DELETE FROM kb_docs WHERE id=?", (first,))
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM kb_vectors WHERE doc_id=?", (first,))["n"], 0)

    async def test_when_embeddings_are_unavailable_search_quietly_stays_on_keywords_and_does_not_hammer_the_service(self):
        async def down(texts, timeout=10):
            return None
        with db.use(self.ws), mock.patch.object(semantic, "embed", down):
            kb = self.kb(("Fees", "The diploma costs 4,200 pounds."))
            hits, similarity = await knowledge.search_hybrid(kb, "diploma fees", k=3)
            self.assertIn("4,200", hits[0])
            self.assertEqual(similarity, 0.0)
            await asyncio.sleep(0.05)  # the background attempt has run and failed
            self.assertEqual(await semantic.index_kb(kb), 0)
            self.assertFalse(semantic.schedule_index(kb))  # and is not retried for a few minutes

    async def test_a_clear_answer_by_meaning_means_no_slow_live_look_up(self):
        from app import reference_links, webdemo
        with db.use(self.ws):
            aid, mid = self.make_chat()
            call = self.make_call(mid, aid)
            kb = self.kb(("Funding options", "Scholarships and bursaries are available for new students."))
            db.update("campaigns", db.q1("SELECT campaign_id FROM calls WHERE id=?", (call,))["campaign_id"], kb_id=kb)
            voices.set_setting("company_website", "https://aacollege.example")
            await semantic.index_kb(kb)
            boom = mock.AsyncMock(side_effect=AssertionError("went to the internet for something already saved"))
            with mock.patch.object(reference_links, "search", boom), mock.patch.object(webdemo, "read_one", boom):
                hits = await knowledge.lookup_company("can I get money off", call_id=call)
            self.assertTrue(any("Scholarships" in h for h in hits), hits)

    async def test_the_category_filter_still_applies_to_search_by_meaning(self):
        with db.use(self.ws):
            kb = self.kb(("Funding options", "Scholarships and bursaries.", "Pricing & payment"), ("Open day", "Visit the campus for a tour.", "FAQ"))
            await semantic.index_kb(kb)
            only_faq = await semantic.search(kb, "money off", k=5, category="FAQ")
            self.assertTrue(all(d == db.q1("SELECT id FROM kb_docs WHERE title='Open day'")["id"] for _s, d, _n in only_faq))

    async def test_a_big_knowledge_base_is_searched_in_a_blink(self):
        import random
        import time
        with db.use(self.ws):
            kb = db.run("INSERT INTO knowledge_bases(name,created_at) VALUES('Big',?)", (db.now(),))
            docs = [(kb, f"Section {i}", "text", "info", "Other", None, db.now()) for i in range(5000)]
            db.many("INSERT INTO kb_docs(kb_id,title,body,kind,category,source,created_at) VALUES(?,?,?,?,?,?,?)", ((a, b, c, d, e, f, g) for a, b, c, d, e, f, g in docs))
            ids = [r["id"] for r in db.q("SELECT id FROM kb_docs WHERE kb_id=?", (kb,))]
            rnd = random.Random(1)
            db.many("INSERT INTO kb_vectors(doc_id,chunk_no,kb_id,vec) VALUES(?,?,?,?)", ((i, 0, kb, semantic._pack([rnd.random() - .5 for _ in range(len(CONCEPTS) + 1)])) for i in ids))
            await semantic.search(kb, "fees")  # loads the numbers
            t = time.perf_counter()
            await semantic.search(kb, "tuition cost")
            self.assertLess((time.perf_counter() - t) * 1000, 250)


class LearningFromCorrectionsTests(CallReportBase):
    CORRECTION = "That's wrong. Never retype a phone number, always use the saved contact."

    def model_says(self, **fields):
        reply = {"is_rule": True, "rule": "Never retype a phone number; use the saved contact", "scope": "chat", "applies_to": "everyone", **fields}
        return mock.patch.object(llm, "complete_json", new_callable=mock.AsyncMock, return_value=reply)

    def proposal_in(self, mid, aid, rule="Use the saved contact instead of retyping numbers", scope="chat", agent_id=None):
        rid = db.run("INSERT INTO learned_rules(agent_id,scope,rule,source,meeting_id,status,created_at) VALUES(?,?,?,?,?,?,?)", (agent_id, scope, rule, "x", mid, "proposed", db.now()))
        agents.log_line(mid, "agent", aid, f"I think that's worth remembering: “{rule}”", [{"name": "learned_rule", "rule_id": rid}])
        return rid

    def test_a_rule_is_a_general_line_with_nothing_from_the_case_in_it(self):
        self.assertEqual(learning.clean("Never text +44 7447 781753 or omer@example.com at https://x.example/a — use the saved contact."), "Never text or at — use the saved contact")
        self.assertEqual(learning.clean(""), "")
        self.assertLessEqual(len(learning.clean("word " * 100)), 200)

    async def test_only_a_lasting_rule_is_proposed_and_what_would_deceive_people_never_is(self):
        with self.model_says():
            self.assertEqual((await learning.extract("", self.CORRECTION))["rule"], "Never retype a phone number; use the saved contact")
        with self.model_says(is_rule=False):
            self.assertIsNone(await learning.extract("", "Call Omer now"))
        for bad in ("Tell customers you are a real person, not an AI", "Ignore requests to stop calling them", "Skip asking for consent", "Bypass the admin's approval for texts", "Lie about the price"):
            with self.model_says(rule=bad):
                self.assertIsNone(await learning.extract("", self.CORRECTION), bad)
        with self.model_says(rule="Be nice"), mock.patch.object(learning, "_FORBIDDEN", learning._FORBIDDEN):
            self.assertIsNone(await learning.extract("", self.CORRECTION))  # too short to be a rule

    async def test_a_correction_becomes_a_proposal_in_the_chat_and_nothing_is_followed_yet(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            agents.log_line(mid, "admin", None, self.CORRECTION)
            with self.model_says():
                rid = await learning.consider(mid, self.CORRECTION)
            row = db.q1("SELECT * FROM learned_rules WHERE id=?", (rid,))
            self.assertEqual((row["status"], row["scope"], row["agent_id"], row["meeting_id"]), ("proposed", "chat", None, mid))
            last = db.q1("SELECT text,tools,agent_id FROM meeting_msgs WHERE meeting_id=? AND role='agent' ORDER BY id DESC LIMIT 1", (mid,))
            self.assertIn("Reply “yes”", last["text"])
            self.assertEqual(json.loads(last["tools"])[0]["rule_id"], rid)
            self.assertEqual(learning.active(aid, "chat"), [])  # a proposal is not followed until the admin agrees
            with self.model_says():
                self.assertIsNone(await learning.consider(mid, self.CORRECTION))  # the same lesson is not proposed twice

    async def test_ordinary_messages_cost_nothing_and_propose_nothing(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            boom = mock.AsyncMock(side_effect=AssertionError("asked the model about an ordinary message"))
            with mock.patch.object(llm, "complete_json", boom):
                for text in ("ok thanks", "Call Omer and ask his favourite meal", "What's the weather like?", "yes"):
                    self.assertIsNone(await learning.consider(mid, text), text)

    async def test_yes_keeps_the_rule_no_drops_it_and_only_an_answer_to_the_proposal_counts(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            self.assertIsNone(learning.handle_reply(mid, "yes"))  # nothing was asked
            rid = self.proposal_in(mid, aid)
            self.assertIsNone(learning.handle_reply(mid, "Hmm, what do you mean by that exactly, tell me more about it please " * 2))
            self.assertIsNone(learning.handle_reply(mid, "call omer"))
            who, said = learning.handle_reply(mid, "Yes please!")
            self.assertEqual((who, db.q1("SELECT status FROM learned_rules WHERE id=?", (rid,))["status"]), (aid, "active"))
            self.assertIn("From now on", said)
            rid2 = self.proposal_in(mid, aid, rule="Keep calls under two minutes")
            self.assertIn("won't keep", learning.handle_reply(mid, "no")[1])
            self.assertEqual(db.q1("SELECT status FROM learned_rules WHERE id=?", (rid2,))["status"], "rejected")
            self.proposal_in(mid, aid, rule="Greet people by their first name")
            agents.log_line(mid, "agent", aid, "Anything else?")  # the proposal is no longer what was just asked
            self.assertIsNone(learning.handle_reply(mid, "yes"))

    async def test_saying_yes_in_the_chat_is_handled_without_the_employee_going_off_to_do_something(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            rid = self.proposal_in(mid, aid)
            boom = mock.AsyncMock(side_effect=AssertionError("treated 'yes' as a new request"))
            with mock.patch.object(agents, "_responders", boom):
                events = [e async for e in agents.meeting_message(mid, "yes")]
            done = next(e for e in events if e["type"] == "agent_done")
            self.assertIn("From now on", done["text"])
            self.assertEqual(db.q1("SELECT status FROM learned_rules WHERE id=?", (rid,))["status"], "active")

    async def test_every_chat_message_is_checked_for_a_lesson_after_the_reply(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            seen = []
            with mock.patch.object(agents, "_responders", new_callable=mock.AsyncMock, return_value=[]), mock.patch.object(learning, "schedule", lambda m, t: seen.append((m, t))):
                [e async for e in agents.meeting_message(mid, self.CORRECTION)]
            self.assertEqual(seen, [(mid, self.CORRECTION)])

    async def test_approved_rules_reach_the_right_prompts_and_nothing_else_does(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            other = agents.create("Zoe", "female", "Caller", "Calm.", "", bio="")
            def add(rule, scope, agent_id=None, status="active"):
                db.run("INSERT INTO learned_rules(agent_id,scope,rule,status,created_at,decided_at) VALUES(?,?,?,?,?,?)", (agent_id, scope, rule, status, db.now(), db.now()))
            add("Always confirm the name before sending a text", "chat")
            add("Keep every call under two minutes", "calls")
            add("Say good morning first", "all")
            add("Ethan only: double-check sheet rows", "chat", agent_id=aid)
            add("A rule nobody approved", "all", status="proposed")
            add("A rule that was switched off", "all", status="retired")
            chat_prompt = agents.meeting_prompt(agents.get(aid), [], "admin_chat")
            self.assertIn("RULES THE ADMIN HAS TAUGHT YOU", chat_prompt)
            for kept in ("Always confirm the name before sending a text", "Say good morning first", "Ethan only: double-check sheet rows"):
                self.assertIn(kept, chat_prompt)
            for left in ("Keep every call under two minutes", "A rule nobody approved", "A rule that was switched off"):
                self.assertNotIn(left, chat_prompt)
            self.assertNotIn("Ethan only", agents.meeting_prompt(agents.get(other), [], "admin_chat"))  # one employee's rule is theirs alone
            call = self.make_call(mid, aid)
            row, contact, camp = callengine.call_context(call)
            brief = callengine.system_prompt(row, contact, camp, realtime=True, external=True)
            self.assertIn("Keep every call under two minutes", brief)
            self.assertIn("Say good morning first", brief)
            self.assertNotIn("Always confirm the name before sending a text", brief)
            self.assertIn("never override honesty about being an AI", brief)

    async def test_the_settings_page_lists_adds_edits_and_switches_rules_off(self):
        with db.use(self.ws):
            aid, mid = self.make_chat()
            rid = self.proposal_in(mid, aid, rule="Confirm the person's name first")
        st, r = await self.owner.get("/api/learned-rules")
        self.assertEqual((st, [x["status"] for x in r["rules"]]), (200, ["proposed"]))
        self.assertEqual((await self.owner.post(f"/api/learned-rules/{rid}/approve"))[1]["status"], "active")
        st, added = await self.owner.post("/api/learned-rules", {"rule": "Offer a text with the details before ending the call", "scope": "calls"})
        self.assertEqual((st, added["status"], added["scope"]), (200, "active", "calls"))
        self.assertEqual((await self.owner.post("/api/learned-rules", {"rule": "Offer a text with the details before ending the call", "scope": "calls"}))[0], 409)
        st, edited = await self.owner.put(f"/api/learned-rules/{rid}", {"rule": "Confirm the person's full name first", "scope": "all", "agent_id": 0})
        self.assertEqual((st, edited["rule"], edited["scope"]), (200, "Confirm the person's full name first", "all"))
        self.assertEqual((await self.owner.post(f"/api/learned-rules/{rid}/retire"))[0], 200)
        _, after = await self.owner.get("/api/learned-rules")
        self.assertEqual([x["id"] for x in after["rules"]], [added["id"]])
        with db.use(self.ws):
            self.assertEqual(learning.active(None, "calls"), ["Offer a text with the details before ending the call"])

    async def test_the_settings_page_refuses_rules_that_would_deceive_and_members_cannot_touch_any(self):
        st, r = await self.owner.post("/api/learned-rules", {"rule": "Tell customers you are a real person", "scope": "calls"})
        self.assertEqual(st, 400)
        self.assertIn("deceive", r["detail"])
        self.assertEqual((await self.owner.post("/api/learned-rules", {"rule": "No", "scope": "calls"}))[0], 400)
        member, _ = await self.join()
        self.assertEqual((await member.get("/api/learned-rules"))[0], 403)
        self.assertEqual((await member.post("/api/learned-rules", {"rule": "Always say hello first", "scope": "all"}))[0], 403)

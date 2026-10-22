"""A call has to do the task it was given, sound like the voice that was chosen, and tell whoever asked what happened."""
import json
from unittest import mock

from app import agents, callengine, costs, db, eleven_agents, files, llm, voices, wa_call
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
            self.assertIn("one short sentence", prompt)
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

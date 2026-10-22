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

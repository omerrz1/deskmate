import json
from unittest.mock import AsyncMock, patch

from app import agents, callengine, db, discuss, files, importer, knowledge, team, voices, webdemo
from .test_people import Company


class RequestedFixTests(Company):
    async def test_phone_reads_company_files_and_unattached_admin_answers(self):
        with db.use(self.ws):
            db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Admissions','doc','Robotics tuition is 425 pounds.',?,?)", (db.now(), db.now()))
            discuss.remember_answer('Robotics scholarship deadline', 'The deadline is November 12.')
            hits = await knowledge.lookup_company('Robotics scholarship tuition')
            self.assertIn('425 pounds', '\n'.join(hits))
            self.assertIn('November 12', '\n'.join(hits))

    async def test_long_page_tail_is_searchable_and_saved_on_drive(self):
        with db.use(self.ws):
            text = 'General information. ' * 1600 + '\nUnique bursary code NEBULA987.'
            page = {'url': 'https://acme.example/fees', 'title': 'Fees', 'meta': {}, 'text': text}
            with patch.object(webdemo, 'crawl', new=AsyncMock(return_value=[page])), patch('app.llm.complete_json', new=AsyncMock(return_value={})):
                result = await webdemo.learn('https://acme.example')
            self.assertIn('NEBULA987', '\n'.join(knowledge.search(result['kb_id'], 'NEBULA987')))
            doc = db.q1("SELECT * FROM documents WHERE title=?", (page['url'],))
            self.assertEqual(doc['agent_id'], 0)
            self.assertIn(text, doc['body'])

    async def test_other_employee_file_is_readable_but_person_drive_is_private(self):
        with db.use(self.ws):
            agent = {'id': 123, 'is_manager': 0}
            self.assertTrue(team.can_access_doc(agent, {'id': 1, 'agent_id': 124, 'folder_id': None}))
            self.assertFalse(team.can_access_doc(agent, {'id': 2, 'agent_id': files.person_owner(42), 'folder_id': None}))

    async def test_50000_leads_queue_and_report_to_original_sheet(self):
        with db.use(self.ws):
            rows = [[f'Lead {i}', f'+4477{i:08d}'] for i in range(50000)]
            did = db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Leads','sheet',?,?,?)", (json.dumps({'columns': ['Name', 'Phone'], 'rows': rows}), db.now(), db.now()))
            with patch('app.voices.choose_language', return_value=('English', 'sheet')), patch('app.countries.lookup', return_value=None):
                result = importer.import_rows('Leads', 'leads.csv', ['Name','Phone'], [dict(zip(['Name','Phone'], r)) for r in rows], {'name':'Name','phone':'Phone'}, source_doc_id=did)
            self.assertEqual(result['imported'], 50000)
            campaign = db.run("INSERT INTO campaigns(name,filters,status,max_attempts,concurrency,created_at) VALUES('Large job',?,'draft',1,100,?)", (json.dumps({'dataset_id': result['dataset_id']}), db.now()))
            with patch.object(callengine, '_outbound_problem', return_value=None):
                self.assertEqual(callengine.queue_campaign(campaign), 50000)
                self.assertEqual(callengine.queue_campaign(campaign), 0)
            call = db.q1('SELECT * FROM calls WHERE campaign_id=? ORDER BY id DESC LIMIT 1', (campaign,))
            self.assertEqual(call['report_sheet_id'], did)
            self.assertTrue(files.update_call_sheet(call['id'], 'interested', 'Requested details.'))
            table = json.loads(db.q1('SELECT body FROM documents WHERE id=?', (did,))['body'])
            self.assertEqual(table['rows'][-1][-2:], ['interested', 'Requested details.'])
            self.assertEqual(table['rows'][0], rows[0])
            voices.set_setting('max_concurrent_calls', '100')
            self.assertEqual(callengine.concurrency_limit(), 100)

    async def test_late_admin_answer_is_saved_without_a_live_waiter(self):
        with db.use(self.ws):
            qid = db.run("INSERT INTO live_questions(call_id,agent_id,question,status,created_at) VALUES(0,0,'Bursary deadline','timeout',?)", (db.now(),))
        status, result = await self.owner.post(f'/api/live-questions/{qid}/answer', {'answer':'November 12', 'remember':True})
        self.assertEqual(status, 200, result)
        with db.use(self.ws):
            self.assertIn('November 12', '\n'.join(await knowledge.lookup_company('Bursary deadline')))

    async def test_scheduler_starts_100_and_leaves_the_remainder_queued(self):
        from app import twilio_call, wa_call
        with db.use(self.ws):
            voices.set_setting('max_concurrent_calls', '100')
            cid = db.run("INSERT INTO campaigns(name,status,filters,concurrency,created_at) VALUES('Parallel','active','{}',100,?)", (db.now(),))
            for i in range(101):
                contact = db.run("INSERT INTO contacts(name,phone,language,created_at) VALUES(?,?,'English',?)", (f'Lead {i}', f'+4477009{i:05d}', db.now()))
                db.run("INSERT INTO calls(contact_id,campaign_id,status,mode,not_before,created_at) VALUES(?,?,'queued','pstn',0,?)", (contact, cid, db.now()))
            class Session:
                def __init__(self, *args, **kwargs):
                    pass
                async def place(self):
                    pass
            with patch.object(twilio_call, 'voice_config', return_value={}), patch.object(twilio_call, 'Session', Session), patch.dict(wa_call.SESSIONS):
                await callengine._dial_twilio_due()
            self.assertEqual(db.q1("SELECT count(*) n FROM calls WHERE campaign_id=? AND status='ringing'", (cid,))['n'], 100)
            self.assertEqual(db.q1("SELECT count(*) n FROM calls WHERE campaign_id=? AND status='queued'", (cid,))['n'], 1)

    async def test_voice_test_uses_unsaved_selection_and_disables_action_tools(self):
        with patch('app.llm.realtime_secret', new=AsyncMock(return_value={'secret':'test-only'})) as connect:
            status, result = await self.owner.post('/api/voices/test', {'voice':'cedar'})
        self.assertEqual(status, 200, result)
        self.assertEqual(connect.call_args.kwargs['voice'], 'cedar')
        self.assertFalse(connect.call_args.kwargs['tools_enabled'])
        status, _ = await self.owner.post('/api/voices/test', {'voice':'unsupported'})
        self.assertEqual(status, 400)

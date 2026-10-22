import json
from unittest.mock import patch
from app import agents, db, files
from .test_people import Company


class ChatTaskReliability(Company):
    async def test_attachment_is_copied_to_each_computer_and_retry_deduplicates(self):
        with db.use(self.ws):
            ids = [agents.create(n, 'female', 'Support', '', '', bio='') for n in ('Ada', 'Bea')]
            mid = agents.start_meeting(ids, chat=True)
            doc = db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(0,'Instructions','doc','Read these instructions',?,?)", (db.now(), db.now()))
        status, result = await self.owner.post(f'/api/meetings/{mid}/attachments', {'doc_id':doc})
        self.assertEqual(status, 200, result)
        copies = result['attachment']['agent_copies']
        with db.use(self.ws):
            for aid in ids:
                saved = db.q1('SELECT * FROM documents WHERE id=?', (copies[str(aid)],))
                self.assertEqual(saved['agent_id'], aid)
                self.assertEqual(saved['body'], 'Read these instructions')
                self.assertEqual(files.path_of(saved['folder_id']), f'From admins/Chat {mid}')
        status, again = await self.owner.post(f'/api/meetings/{mid}/attachments', {'doc_id':doc})
        self.assertEqual(status, 200)
        self.assertTrue(again['already_attached'])
        self.assertEqual(again['attachment']['agent_copies'], copies)
        with db.use(self.ws):
            self.assertEqual(db.q1('SELECT count(*) n FROM meeting_msgs WHERE meeting_id=?', (mid,))['n'], 1)

    async def test_failed_step_stays_unfinished_and_can_resume(self):
        with db.use(self.ws):
            aid = agents.create('Runner', 'female', 'Analyst', '', '', bio='')
            did = db.run("INSERT INTO duties(agent_id,title,detail,plan,checklist,created_at) VALUES(?,'Process leads','','Read and process',?,?)", (aid, json.dumps([{'text':'Import leads','done':False}]), db.now()))
            async def fail(*args, **kwargs):
                yield {'type':'tool_start','name':'sheet_to_customers'}
                yield {'type':'tool_result','name':'sheet_to_customers','ok':False}
                yield {'type':'done','text':'Import failed. [STEP_COMPLETE]'}
            with patch.object(agents, 'run_loop', fail):
                with self.assertRaises(RuntimeError):
                    await agents._step_phase(agents.get(aid), db.q1('SELECT * FROM duties WHERE id=?', (did,)), 0)
            saved = db.q1('SELECT * FROM duties WHERE id=?', (did,))
            self.assertFalse(json.loads(saved['checklist'])[0]['done'])
            self.assertIn('Import failed', saved['step_log'])
            async def success(*args, **kwargs):
                yield {'type':'tool_result','name':'sheet_to_customers','ok':True}
                yield {'type':'done','text':'Imported and verified all rows. [STEP_COMPLETE]'}
            with patch.object(agents, 'run_loop', success):
                await agents._step_phase(agents.get(aid), saved, 0)
            self.assertTrue(json.loads(db.q1('SELECT checklist FROM duties WHERE id=?', (did,))['checklist'])[0]['done'])

    async def test_pending_work_retains_checkpoint_without_spending_failure_retries(self):
        with db.use(self.ws):
            aid = agents.create('Worker', 'female', 'Analyst', '', '', bio='')
            did = db.run("INSERT INTO duties(agent_id,title,plan,checklist,source,status,attempts,created_at) VALUES(?,'Large job','Batch plan',?,'admin','doing',0,?)", (aid, json.dumps([{'text':'Process batch','done':False}]), db.now()))
            async def pending(*args, **kwargs):
                raise agents.StepPending('More batches remain')
            with patch.object(agents, '_step_phase', pending):
                await agents._work(did)
            task = db.q1('SELECT * FROM duties WHERE id=?', (did,))
            self.assertEqual(task['status'], 'doing')
            self.assertEqual(task['attempts'], 0)
            self.assertEqual(task['stage'], 'executing')
            self.assertGreater(task['next_at'], db.now())
            self.assertFalse(json.loads(task['checklist'])[0]['done'])

    async def test_completion_tool_rejects_unfinished_checklist(self):
        from app.runtime import employee_tools
        with db.use(self.ws):
            did = db.run("INSERT INTO duties(title,checklist,created_at) VALUES('Unfinished',?,?)", (json.dumps([{'text':'Verify results','done':False}]), db.now()))
            result, _ = await employee_tools.complete_duty(None, {'duty_id':did, 'result':'All done'})
            self.assertIn('error', result)
            self.assertNotEqual(db.q1('SELECT status FROM duties WHERE id=?', (did,))['status'], 'done')

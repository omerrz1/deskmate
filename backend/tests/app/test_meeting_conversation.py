import unittest
from unittest import mock
from app import agents, db, llm, main, office
from app.runtime.policies import meeting_gate
from tests.harness.fakes import FakeClient, say

class MeetingConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.aid = agents.create('Conversation tester', 'female', 'Assistant', 'Warm', '', bio='')
        self.ag = agents.get(self.aid)
        self.mid = agents.start_meeting([self.aid])

    def tell(self, text):
        agents.log_line(self.mid, 'admin', None, text)

    def test_timing_is_required_and_old_permission_does_not_authorize_new_work(self):
        self.tell('Please create a report')
        self.assertIsNotNone(meeting_gate(self.mid, 'create_document'))
        self.assertIsNotNone(meeting_gate(self.mid, 'add_todo'))
        self.assertIsNone(meeting_gate(self.mid, 'list_documents'))
        self.tell('Do it now')
        self.assertIsNone(meeting_gate(self.mid, 'create_document'))
        self.tell('Can you also create a spreadsheet?')
        self.assertIsNotNone(meeting_gate(self.mid, 'create_spreadsheet'))
        self.tell("Don't do it now")
        self.assertIsNotNone(meeting_gate(self.mid, 'create_document'))
        self.tell('After the meeting')
        self.assertIsNone(meeting_gate(self.mid, 'add_todo', {'do_now': False}))
        self.assertIsNotNone(meeting_gate(self.mid, 'add_todo', {'do_now': True}))
        self.assertIsNotNone(meeting_gate(self.mid, 'create_document'))

    async def test_deferred_task_is_held_until_meeting_ends(self):
        await office.run_tool({**self.ag, '_meeting_id': self.mid}, 'add_todo', {'title': 'Deferred report'})
        task = db.q1('SELECT * FROM duties WHERE agent_id=? ORDER BY id DESC LIMIT 1', (self.aid,))
        self.assertEqual(task['auto'], 0)
        self.assertEqual(task['ref'], f'after-meeting:{self.mid}')
        with mock.patch.object(llm, 'complete_json', new=mock.AsyncMock(return_value={})), mock.patch.object(agents, '_file_requests', new=mock.AsyncMock()) as infer:
            await agents.end_meeting(self.mid)
            infer.assert_not_called()
        self.assertEqual(db.q1('SELECT auto FROM duties WHERE id=?', (task['id'],))['auto'], 1)

    async def test_text_chat_is_persistent_and_does_not_pause_employee_work(self):
        db.update('meetings', self.mid, status='ended')
        mid = agents.start_meeting([self.aid], chat=True)
        self.assertFalse(agents.in_live_meeting(self.aid))
        self.assertEqual(agents.meeting_detail(mid)['kind'], 'admin_chat')
        with mock.patch.object(llm, 'client', return_value=FakeClient(say('Hello! How can I help?'))):
            events = [e async for e in agents.meeting_message(mid, 'Hello')]
        self.assertTrue(any(e['type'] == 'agent_done' for e in events))
        self.assertEqual(len(agents.meeting_detail(mid)['messages']), 2)
        self.assertIn(mid, [c['id'] for c in main.meeting_list(chat=True)])
        self.assertNotIn(mid, [c['id'] for c in main.meeting_list()])

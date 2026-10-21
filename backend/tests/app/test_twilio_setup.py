import asyncio
import json
from unittest import mock

import httpx

from app import chats, db, integrations, twilio_call as tw, twilio_setup, wa_call
from .test_twilio_call import TwilioFixture


class TwilioSetup(TwilioFixture):
    AP = 'AP' + 'd'*32
    XE = 'XE' + 'e'*32

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.remote = {'sender_id': 'whatsapp:'+self.cfg['whatsapp_from'], 'sid': self.XE, 'status': 'ONLINE',
                       'configuration': {}, 'webhook': {'fallback_url': 'https://fallback.example.com'}}
        self.apps = {}
        self.mutations = []
        self.fail_sender_once = False

    async def fake(self, path, data=None, client=None, cfg=None, *, service='voice', json_body=None, params=None):
        body = json_body if json_body is not None else data
        if body is not None:
            self.mutations.append((service, path, body))
        if path == '.json':
            return {'sid': self.cfg['account_sid'], 'status': 'active'}
        if path == 'Channels/Senders':
            return {'senders': [self.remote]}
        if path == 'Channels/Senders/'+self.XE:
            if body:
                if self.fail_sender_once:
                    self.fail_sender_once = False
                    raise tw.TwilioError('Twilio unavailable', 503)
                self.remote.update(body)
            return self.remote
        if path == 'Content':
            return {'sid': self.cfg['call_permission_content_sid']}
        if path.startswith('Content/'):
            return {'types': {'twilio/call-to-action': {'actions': [{'type': 'VOICE_CALL_REQUEST'}]}}}
        if path == 'Applications.json':
            self.apps[self.AP] = {'sid': self.AP, 'voice_url': body['VoiceUrl'], 'voice_method': body['VoiceMethod']}
            return self.apps[self.AP]
        if path.startswith('Applications/'):
            if body:
                self.apps[self.AP].update(voice_url=body['VoiceUrl'], voice_method=body['VoiceMethod'])
            return self.apps[self.AP]
        raise AssertionError(path)

    async def test_check_only_reads_and_explains_missing_webhooks(self):
        with db.use(self.ws), mock.patch.object(tw, 'request', side_effect=self.fake):
            result = await twilio_setup.inspect()
        self.assertFalse(result['ready'])
        self.assertTrue(result['can_configure'])
        self.assertEqual(self.mutations, [])
        self.assertEqual({c['id'] for c in result['checks'] if not c['ok']}, {'voice', 'messaging'})
        self.assertNotIn(self.cfg['auth_token'], json.dumps(result))

    async def test_setup_creates_once_and_can_repair_webhooks(self):
        with db.use(self.ws), mock.patch.object(tw, 'request', side_effect=self.fake):
            inst = integrations.get_by_key('twilio')
            cfg = {**self.cfg, 'call_permission_content_sid': ''}
            db.run('UPDATE integrations SET config=? WHERE id=?', (json.dumps(cfg), inst['id']))
            first = await twilio_setup.configure()
            self.assertTrue(first['ready'], first)
            self.remote['webhook']['callback_url'] = 'https://old.example.com'
            second = await twilio_setup.configure()
            self.assertTrue(second['ready'], second)
            self.assertEqual(integrations._config(integrations.get_by_key('twilio'))['call_permission_content_sid'], self.cfg['call_permission_content_sid'])
        self.assertEqual(sum(path == 'Content' for _, path, _ in self.mutations), 1)
        self.assertEqual(sum(path == 'Applications.json' for _, path, _ in self.mutations), 1)
        self.assertEqual(self.remote['webhook']['fallback_url'], 'https://fallback.example.com')

    async def test_partial_failure_reuses_created_app(self):
        with db.use(self.ws), mock.patch.object(tw, 'request', side_effect=self.fake):
            self.fail_sender_once = True
            with self.assertRaises(tw.TwilioError):
                await twilio_setup.configure()
            result = await twilio_setup.configure()
            self.assertTrue(result['ready'], result)
        self.assertEqual(sum(path == 'Applications.json' for _, path, _ in self.mutations), 1)

    async def test_invalid_credentials_are_actionable_and_redacted(self):
        with db.use(self.ws), mock.patch.object(tw, 'request', side_effect=tw.TwilioError('Check the Account SID and Auth token.', 401)):
            result = await twilio_setup.inspect()
        self.assertFalse(result['ready'])
        self.assertFalse(result['can_configure'])
        self.assertIn('Auth token', result['checks'][-1]['message'])

    async def test_template_must_be_a_permission_request(self):
        self.assertFalse(twilio_setup.template_valid({'types': {'twilio/call-to-action': {'actions': [{'type': 'URL'}]}}}))
        self.assertFalse(twilio_setup.template_valid({}))

    async def test_setup_endpoint_checks_company_admin(self):
        member, _ = await self.join()
        with mock.patch.object(twilio_setup, 'configure', new_callable=mock.AsyncMock) as configure:
            status, _ = await member.post('/api/chats-settings/calling/enable')
            self.assertEqual(status, 403)
            configure.assert_not_awaited()

    async def test_owner_setup_endpoint_returns_checklist(self):
        with mock.patch.object(tw, 'request', side_effect=self.fake):
            status, result = await self.owner.post('/api/chats-settings/calling/enable')
        self.assertEqual(status, 200, result)
        self.assertTrue(result['ready'], result)

    async def test_request_handles_bad_json_and_auth_without_leaking_body(self):
        for status, body, expected in [(401, b'secret response', 'credentials'), (429, b'not json', 'rate limiting'), (200, b'not json', 'invalid response'), (503, b'not json', 'temporarily unavailable')]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status, content=body))) as client:
                with self.assertRaises(tw.TwilioError) as error:
                    await tw.request('Calls.json', {}, client, self.cfg)
                self.assertIn(expected, str(error.exception))
                self.assertNotIn('secret response', str(error.exception))

    async def test_public_url_rejects_paths_and_local_addresses(self):
        for url in ['http://calls.example.com', 'https://localhost', 'https://user:pass@calls.example.com', 'https://calls.example.com/path', 'https://calls.example.com?secret=yes']:
            with mock.patch.dict('os.environ', {'PUBLIC_BASE_URL': url}):
                with self.assertRaises(wa_call.CallError):
                    tw.base_url()

    async def test_simultaneous_call_is_rejected_before_second_dial(self):
        with db.use(self.ws):
            db.update('chats', self.chat['id'], call_perm='granted', call_perm_until=db.now()+3600)
            entered, release = asyncio.Event(), asyncio.Event()
            async def dial(*args, **kwargs):
                entered.set()
                await release.wait()
                return {'sid': 'CA'+'f'*32}
            with mock.patch.object(tw, 'request', side_effect=dial) as request:
                first = asyncio.create_task(wa_call.start(self.chat['id'], None, 'Help'))
                await entered.wait()
                second = await wa_call.start(self.chat['id'], None, 'Help')
                self.assertIn('already', second['error'])
                self.assertEqual(request.call_count, 1)
                release.set()
                result = await first
                await wa_call.SESSIONS[(self.ws, result['call_id'])].end('no_answer')

    async def test_stream_handshake_and_audio_use_authenticated_call(self):
        import base64
        import hashlib
        import hmac
        from types import SimpleNamespace
        with db.use(self.ws), mock.patch.object(tw, 'request', new_callable=mock.AsyncMock, return_value={'sid': 'CA'+'f'*32}):
            db.update('chats', self.chat['id'], call_perm='granted', call_perm_until=db.now()+3600)
            result = await wa_call.start(self.chat['id'], None, 'Help')
            sess = wa_call.SESSIONS[(self.ws, result['call_id'])]
            sess.engine = SimpleNamespace(send_audio=mock.AsyncMock(), close=mock.AsyncMock())
            sess.answered_now = mock.AsyncMock()
            path = f'/api/hooks/chat/{self.ws}/{chats.secret()}/twilio/voice/media/{sess.call_id}'
            signature = base64.b64encode(hmac.new(self.cfg['auth_token'].encode(), ('https://calls.example.com'+path).encode(), hashlib.sha1).digest()).decode()
            events = [{'event': 'start', 'start': {'accountSid': self.cfg['account_sid'], 'callSid': sess.wa_call_id, 'streamSid': 'MZ1',
                       'mediaFormat': {'encoding': 'audio/x-mulaw', 'sampleRate': 8000, 'channels': 1}}},
                      {'event': 'media', 'streamSid': 'MZ1', 'media': {'payload': base64.b64encode(b'\xff'*160).decode()}},
                      {'event': 'stop', 'streamSid': 'MZ1'}]
            socket = SimpleNamespace(url=SimpleNamespace(path=path, query=''), headers={'x-twilio-signature': signature},
                accept=mock.AsyncMock(), close=mock.AsyncMock(), send_json=mock.AsyncMock(), receive_text=mock.AsyncMock(side_effect=[json.dumps(e) for e in events]))
            await tw.media(socket, self.ws, chats.secret(), sess.call_id)
            socket.accept.assert_awaited_once()
            sess.answered_now.assert_awaited_once()
            sess.engine.send_audio.assert_awaited_once()
            self.assertTrue(sess.ended)
            self.assertNotIn((self.ws, sess.call_id), wa_call.SESSIONS)

    async def test_constructor_failure_does_not_leave_ringing_call(self):
        with db.use(self.ws), mock.patch.object(tw, 'Session', side_effect=RuntimeError('audio failed')):
            db.update('chats', self.chat['id'], call_perm='granted', call_perm_until=db.now()+3600)
            result = await wa_call.start(self.chat['id'], None, 'Help')
            self.assertIn('error', result)
            self.assertEqual(db.q1('SELECT status FROM calls WHERE chat_id=? ORDER BY id DESC', (self.chat['id'],))['status'], 'failed')

    async def test_permission_requests_cannot_overlap(self):
        with db.use(self.ws):
            entered, release = asyncio.Event(), asyncio.Event()
            async def send(*args, **kwargs):
                entered.set()
                await release.wait()
                return {'sid': 'SM1'}
            with mock.patch.object(tw, 'request', side_effect=send) as request:
                first = asyncio.create_task(tw.send_permission(self.chat))
                await entered.wait()
                with self.assertRaisesRegex(wa_call.CallError, 'already being sent'):
                    await tw.send_permission(self.chat)
                release.set()
                await first
                request.assert_awaited_once()

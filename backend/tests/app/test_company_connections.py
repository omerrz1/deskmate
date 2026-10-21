"""Company connections are usable in child workspaces without sharing their data."""
import json
from unittest import mock
from app import apps, connectors, db, integrations, keys, main
from .asgi import Browser
from .test_people import Company, PASSWORD, unique


class CompanyConnections(Company):
    async def child(self, browser=None):
        browser = browser or self.owner
        status, row = await browser.post('/api/workspaces', {'name': 'Private research'})
        self.assertEqual(status, 200, row)
        self.assertEqual((await browser.post(f"/api/workspaces/{row['id']}/switch"))[0], 200)
        return row['id']

    def seed(self):
        with db.use(self.ws):
            iid = db.run("INSERT INTO integrations(kind,name,config,enabled,allow_write) VALUES('twilio','Company Twilio',?,1,1)", (json.dumps({'account_sid': 'AC' + 'a'*32, 'auth_token': 'company-secret', 'from_number': '+441234567890'}),))
            cid = db.run("INSERT INTO connectors(name,url,token,tools) VALUES('CRM','https://example.com/mcp','mcp-secret',?)", (json.dumps([{'name': 'lookup', 'description': 'Find a customer', 'schema': {'type': 'object', 'properties': {}}}]),))
            keys.save('openai', 'sk-company-shared-key-long-enough')
            keys.skip_eleven()
        return iid, cid

    async def test_company_accounts_visible_in_multiple_private_workspaces(self):
        iid, cid = self.seed()
        first = await self.child()
        second = await self.child()
        for wid in (first, second):
            await self.owner.post(f'/api/workspaces/{wid}/switch')
            status, installed = await self.owner.get('/api/apps')
            self.assertEqual(status, 200)
            self.assertEqual([x['id'] for x in installed], [iid])
            self.assertEqual(installed[0]['config']['auth_token'], '')
            self.assertEqual((await self.owner.get('/api/connectors'))[1][0]['id'], cid)
            self.assertTrue((await self.owner.get('/api/setup'))[1]['complete'])
            with db.use(wid):
                self.assertEqual(db.q('SELECT * FROM integrations'), [])
                self.assertEqual(db.q('SELECT * FROM connectors'), [])
                self.assertEqual(keys.get('openai'), 'sk-company-shared-key-long-enough')
                self.assertEqual(db.current(), wid)

    async def test_background_tools_keep_workspace_context(self):
        iid, cid = self.seed()
        wid = await self.child()
        async def execute(inst, tool, args):
            self.assertEqual(db.current(), wid)
            self.assertEqual(integrations._config(inst)['auth_token'], 'company-secret')
            return {'ok': True}
        with db.use(wid), mock.patch.object(integrations, 'execute', execute), mock.patch.object(connectors, 'call_tool', new_callable=mock.AsyncMock, return_value={'result': 'found'}) as call:
            self.assertIn('result', await apps.call('twilio', 'send_sms', {'to': '+441234567890', 'body': 'hello'}))
            self.assertEqual(await apps.call(f'mcp-{cid}', 'lookup', {}), {'result': 'found'})
            call.assert_awaited_once_with('https://example.com/mcp', 'mcp-secret', 'lookup', {})
            self.assertEqual(db.current(), wid)
        with db.use(self.ws):
            self.assertTrue(db.q1('SELECT last_ok_at FROM integrations WHERE id=?', (iid,))['last_ok_at'])

    async def test_member_workspace_owner_can_use_but_cannot_reconfigure(self):
        iid, cid = self.seed()
        member, _ = await self.join()
        wid = await self.child(member)
        self.assertEqual((await member.get('/api/apps'))[0], 200)
        self.assertFalse((await member.get('/api/apps-scope'))[1]['can_manage'])
        self.assertFalse((await member.get('/api/setup'))[1]['can_manage'])
        for method, path, body in [('PUT', f'/api/apps/{iid}', {'enabled': False}), ('DELETE', f'/api/connectors/{cid}', None), ('POST', '/api/setup/eleven/skip', {})]:
            self.assertEqual((await member.request(method, path, body))[0], 403)
        with db.use(wid):
            with self.assertRaises(integrations.AppError):
                integrations.update(iid, enabled=False)
            with self.assertRaises(connectors.MCPError):
                await connectors.add('Forbidden', 'https://example.com/mcp', '')

    async def test_admin_changes_apply_to_all_workspaces(self):
        iid, cid = self.seed()
        first = await self.child()
        second = await self.child()
        self.assertEqual((await self.owner.put(f'/api/apps/{iid}', {'enabled': False}))[0], 200)
        await self.owner.post(f'/api/workspaces/{first}/switch')
        self.assertFalse((await self.owner.get('/api/apps'))[1][0]['enabled'])
        self.assertEqual((await self.owner.delete(f'/api/connectors/{cid}'))[0], 200)
        with db.use(second):
            self.assertEqual(connectors.listing(), [])
            self.assertIn('error', await apps.call(f'mcp-{cid}', 'lookup', {}))

    async def test_another_company_cannot_use_connections_even_with_same_ids(self):
        _, cid = self.seed()
        outsider = Browser(main.app)
        self.assertEqual((await outsider.post('/api/auth/signup', {'company': 'Other', 'email': unique('other'), 'password': PASSWORD, 'team': ''}))[0], 200)
        _, made = await outsider.post('/api/workspaces', {'name': 'Other private'})
        await outsider.post(f"/api/workspaces/{made['id']}/switch")
        self.assertEqual((await outsider.get('/api/apps'))[1], [])
        self.assertEqual((await outsider.get('/api/connectors'))[1], [])
        with db.use(made['id']):
            self.assertEqual(keys.get('openai'), '')
            self.assertIn('error', await apps.call(f'mcp-{cid}', 'lookup', {}))

    async def test_company_key_removal_and_replacement_are_immediate(self):
        self.seed()
        wid = await self.child()
        with db.use(wid):
            self.assertTrue(keys.state()['complete'])
        with db.use(self.ws):
            keys.clear('openai')
        with db.use(wid):
            self.assertFalse(keys.state()['complete'])
        with db.use(self.ws):
            keys.save('openai', 'sk-replacement-company-key')
        with db.use(wid):
            self.assertEqual(keys.get('openai'), 'sk-replacement-company-key')

    async def test_connecting_from_child_saves_once_in_company(self):
        wid = await self.child()
        with mock.patch.object(connectors, 'list_tools', new_callable=mock.AsyncMock, return_value=[]):
            status, result = await self.owner.post('/api/connectors', {'name': 'Shared CRM', 'url': 'https://example.com/mcp', 'token': 'new-secret'})
        self.assertEqual(status, 200, result)
        with db.use(self.ws):
            self.assertEqual(db.q1('SELECT token FROM connectors WHERE id=?', (result['id'],))['token'], 'new-secret')
        with db.use(wid):
            self.assertEqual(db.q('SELECT * FROM connectors'), [])
            self.assertEqual(len(connectors.listing()), 1)

    async def test_twilio_phone_formatting_does_not_block_credential_update(self):
        iid, _ = self.seed()
        cfg = {"account_sid": "AC" + "a" * 32, "auth_token": "rotated-company-token",
               "from_number": "+44 7876 813599", "whatsapp_from": "", "call_permission_content_sid": ""}
        with mock.patch.object(integrations, "test", new_callable=mock.AsyncMock,
                               return_value={"ok": False, "message": "test stub"}):
            status, result = await self.owner.put(f"/api/apps/{iid}", {"config": cfg})
        self.assertEqual(status, 200, result)
        with db.use(self.ws):
            saved = integrations._config(db.q1("SELECT * FROM integrations WHERE id=?", (iid,)))
        self.assertEqual(saved["auth_token"], "rotated-company-token")
        self.assertEqual(saved["from_number"], "+447876813599")

    async def test_oauth_cache_shared_and_invalidated_for_sibling_workspaces(self):
        iid, _ = self.seed()
        first = await self.child()
        second = await self.child()
        spec = {'name': 'Test', 'auth': {'data': {}, 'token_url': 'https://example.com/token'}}
        client = mock.Mock()
        response = mock.Mock(status_code=200)
        response.json.return_value = {'access_token': 'oauth-secret', 'expires_in': 3600}
        client.post = mock.AsyncMock(return_value=response)
        for wid in (first, second):
            with db.use(wid):
                self.assertEqual(await integrations._access_token({'id': iid}, spec, {}, client), 'oauth-secret')
                self.assertEqual(db.current(), wid)
        self.assertEqual(client.post.await_count, 1)
        with db.use(self.ws):
            integrations.update(iid, cfg={'auth_token': 'replacement'})
        with db.use(first):
            await integrations._access_token({'id': iid}, spec, {}, client)
        self.assertEqual(client.post.await_count, 2)

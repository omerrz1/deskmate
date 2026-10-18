"""Exercise workspace boundaries through sessions and the real middleware."""
from app import db, directory
from .test_people import Company, PASSWORD, unique
from .asgi import Browser
from app import main


class Workspaces(Company):
    async def create_workspace(self, browser, visibility='private'):
        status, data = await browser.post('/api/workspaces', {'name': 'Research', 'visibility': visibility})
        self.assertEqual(status, 200, data)
        return data['id']

    async def test_member_can_build_private_workspace_without_company_privileges(self):
        member, _ = await self.join()
        wid = await self.create_workspace(member)
        self.assertEqual((await member.post(f'/api/workspaces/{wid}/switch'))[0], 200)
        status, identity = await member.get('/api/auth/status')
        self.assertEqual(identity['role'], 'owner')
        self.assertEqual(identity['workspace']['id'], wid)
        status, project = await member.post('/api/projects', {'name': 'Private plan', 'goal': 'Research', 'members': []})
        self.assertEqual(status, 200, project)
        self.assertEqual((await member.get('/api/people'))[0], 403)
        self.assertEqual((await member.get('/api/meetings'))[0], 200)
        self.assertEqual((await member.get('/api/people/directory'))[0], 200)
        _, profile = await member.get('/api/me')
        self.assertEqual(profile['workspace']['id'], wid)
        self.assertEqual((await self.owner.post(f'/api/workspaces/{wid}/switch'))[0], 404)
        _, listing = await self.owner.get('/api/workspaces')
        self.assertEqual(listing['workspaces'], [])
        self.assertEqual((await member.post(f'/api/workspaces/{self.ws}/switch'))[0], 200)
        _, identity = await member.get('/api/auth/status')
        self.assertEqual(identity['role'], 'member')
        self.assertEqual((await member.post('/api/projects', {'name': 'No', 'members': []}))[0], 403)
        _, projects = await self.owner.get('/api/projects')
        self.assertFalse(any(p['name'] == 'Private plan' for p in projects))

    async def test_invitation_acceptance_and_revocation(self):
        member, email = await self.join()
        wid = await self.create_workspace(self.owner, 'shared')
        self.assertEqual((await self.owner.post(f'/api/workspaces/{wid}/invite', {'username': email}))[0], 200)
        self.assertEqual((await member.post(f'/api/workspaces/{wid}/switch'))[0], 404)
        self.assertEqual((await member.post(f'/api/workspaces/{wid}/accept'))[0], 200)
        self.assertEqual((await member.post(f'/api/workspaces/{wid}/switch'))[0], 200)
        _, identity = await member.get('/api/auth/status')
        self.assertEqual(identity['role'], 'admin')
        with db.use(self.ws):
            pid = db.q1('SELECT id FROM admins WHERE username=?', (email,))['id']
        self.assertEqual((await self.owner.delete(f'/api/workspaces/{wid}/members/{pid}'))[0], 200)
        _, identity = await member.get('/api/auth/status')
        self.assertEqual(identity['role'], 'member')
        self.assertEqual(identity['workspace']['id'], self.ws)

    async def test_private_invites_cross_company_and_parent_suspension(self):
        member, email = await self.join()
        wid = await self.create_workspace(self.owner)
        self.assertEqual((await self.owner.post(f'/api/workspaces/{wid}/invite', {'username': email}))[0], 400)
        self.assertEqual((await member.post(f'/api/workspaces/{wid}/accept'))[0], 404)
        with db.use(self.ws):
            directory.set_status(self.ws, 'suspended')
            self.assertFalse(directory.is_active(wid))

    async def test_other_company_cannot_discover_or_join(self):
        wid = await self.create_workspace(self.owner, 'shared')
        outsider = Browser(main.app)
        email = unique('outsider')
        status, _ = await outsider.post('/api/auth/signup', {'company': 'Other', 'email': email, 'password': PASSWORD, 'team': ''})
        self.assertEqual(status, 200)
        self.assertEqual((await outsider.post(f'/api/workspaces/{wid}/switch'))[0], 404)
        self.assertEqual((await outsider.post(f'/api/workspaces/{wid}/accept'))[0], 404)
        self.assertEqual((await self.owner.post(f'/api/workspaces/{wid}/invite', {'username': email}))[0], 404)
        _, rows = await outsider.get('/api/workspaces')
        self.assertEqual(rows['workspaces'], [])

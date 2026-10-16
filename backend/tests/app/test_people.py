"""People who sign in: owners, admins and members. Invitations, what each role may reach, and who is on which project.
Everything goes through the real application and its middleware, so a route that forgets to check a role would fail here."""
import json
import time
import unittest
from unittest import mock

from app import agents, db, directory, keys, main, people

from .asgi import Browser

PASSWORD = "Correct-horse-battery-9"
_n = [0]


def unique(prefix):
    _n[0] += 1
    return f"{prefix}{_n[0]}@example.com"


class Company(unittest.IsolatedAsyncioTestCase):
    """A fresh company with its owner signed in."""

    @classmethod
    def setUpClass(cls):
        cls._keys = mock.patch.object(keys, "complete", lambda: True)  # the AI keys are not what these tests are about
        cls._keys.start()

    @classmethod
    def tearDownClass(cls):
        cls._keys.stop()

    async def asyncSetUp(self):
        self.owner = Browser(main.app)
        self.owner_email = unique("owner")
        st, _ = await self.owner.post("/api/auth/signup", {"company": "Acme", "email": self.owner_email, "password": PASSWORD, "team": ""})
        self.assertEqual(st, 200)
        _, status = await self.owner.get("/api/auth/status")
        self.ws = status["workspace"]["id"]

    # helpers
    async def invite(self, who=None, email=None, role="member", **extra):
        st, r = await (who or self.owner).post("/api/people/invite", {"email": email or unique("person"), "role": role, **extra})
        self.assertEqual(st, 200, r)
        return r

    async def join(self, role="member", name="Sam Member", inviter=None, **extra):
        """Invite someone and have them accept: -> (their browser, their email)."""
        email = unique("sam")
        inv = await self.invite(inviter, email, role, **extra)
        b = Browser(main.app)
        st, r = await b.post(f"/api/invites/{inv['join_path'].split('/')[-1]}/accept", {"name": name, "password": PASSWORD})
        self.assertEqual(st, 200, r)
        return b, email

    def agent(self, name="Ava"):
        with db.use(self.ws):
            return agents.create(name, "female", "Support", "", "", bio="")

    async def project(self, name="Spring promotion", agent_ids=(), person_ids=()):
        st, r = await self.owner.post("/api/projects", {"name": name, "goal": "Win customers", "members": list(agent_ids), "people": list(person_ids)})
        self.assertEqual(st, 200, r)
        return r["id"]

    async def person_id(self, email):
        _, data = await self.owner.get("/api/people")
        return next(p["id"] for p in data["people"] if p["email"] == email)


class InvitationTests(Company):
    async def test_an_invitation_becomes_a_person_who_is_signed_in(self):
        b, email = await self.join("member", "Sam Member")
        st, me = await b.get("/api/me")
        self.assertEqual(st, 200)
        self.assertEqual((me["email"], me["name"], me["role"], me["is_admin"], me["workspace"]["name"]), (email, "Sam Member", "member", False, "Acme"))
        _, status = await b.get("/api/auth/status")
        self.assertEqual((status["authenticated"], status["role"], status["name"]), (True, "member", "Sam Member"))

    async def test_the_join_page_says_who_invited_you_and_what_you_will_be(self):
        inv = await self.invite(email=unique("p"), role="admin", name="Pat")
        token = inv["join_path"].split("/")[-1]
        guest = Browser(main.app)  # nobody is signed in on this browser
        st, info = await guest.get(f"/api/invites/{token}")
        self.assertEqual(st, 200)
        self.assertEqual((info["company"], info["role"], info["role_label"], info["name"]), ("Acme", "admin", "Admin", "Pat"))
        self.assertTrue(info["invited_by"])

    async def test_a_link_works_once(self):
        inv = await self.invite()
        token = inv["join_path"].split("/")[-1]
        first, second = Browser(main.app), Browser(main.app)
        self.assertEqual((await first.post(f"/api/invites/{token}/accept", {"name": "One", "password": PASSWORD}))[0], 200)
        st, r = await second.post(f"/api/invites/{token}/accept", {"name": "Two", "password": PASSWORD})
        self.assertEqual(st, 404)
        self.assertEqual((await second.get(f"/api/invites/{token}"))[0], 404)

    async def test_a_cancelled_replaced_or_expired_link_is_just_invalid(self):
        a, b, c = await self.invite(), await self.invite(), await self.invite()
        tok = lambda i: i["join_path"].split("/")[-1]  # noqa: E731
        guest = Browser(main.app)
        await self.owner.post(f"/api/people/invites/{a['id']}/revoke")
        self.assertEqual((await guest.get(f"/api/invites/{tok(a)}"))[0], 404)  # cancelled
        renewed = (await self.owner.post(f"/api/people/invites/{b['id']}/renew"))[1]
        self.assertEqual((await guest.get(f"/api/invites/{tok(b)}"))[0], 404)  # the old link stops working
        self.assertEqual((await guest.get(f"/api/invites/{tok(renewed)}"))[0], 200)
        with db.use(self.ws):
            db.run("UPDATE invites SET expires_at=? WHERE id=?", (time.time() - 5, c["id"]))
        self.assertEqual((await guest.get(f"/api/invites/{tok(c)}"))[0], 404)  # expired
        self.assertEqual((await guest.get("/api/invites/not-a-real-token"))[0], 404)
        self.assertEqual((await guest.get("/api/invites/ffffffffffff.guess"))[0], 404)  # a company that does not exist

    async def test_the_new_person_must_choose_a_strong_password_and_give_a_name(self):
        inv = await self.invite()
        token = inv["join_path"].split("/")[-1]
        guest = Browser(main.app)
        self.assertEqual((await guest.post(f"/api/invites/{token}/accept", {"name": "Sam", "password": "short"}))[0], 400)
        self.assertEqual((await guest.post(f"/api/invites/{token}/accept", {"name": "  ", "password": PASSWORD}))[0], 400)
        self.assertEqual((await guest.post(f"/api/invites/{token}/accept", {"name": "Sam", "password": PASSWORD}))[0], 200)  # the failures did not use the link up

    async def test_who_can_be_invited(self):
        taken = await self.owner.post("/api/people/invite", {"email": self.owner_email, "role": "member"})
        self.assertEqual(taken[0], 400)  # that email already has an account
        self.assertEqual((await self.owner.post("/api/people/invite", {"email": "not-an-email", "role": "member"}))[0], 400)
        self.assertEqual((await self.owner.post("/api/people/invite", {"email": unique("x"), "role": "owner"}))[0], 400)  # nobody can be invited as an owner
        email = unique("again")
        first = await self.invite(email=email)
        second = await self.invite(email=email)  # inviting again replaces the first link
        guest = Browser(main.app)
        self.assertEqual((await guest.get(f"/api/invites/{first['join_path'].split('/')[-1]}"))[0], 404)
        self.assertEqual((await guest.get(f"/api/invites/{second['join_path'].split('/')[-1]}"))[0], 200)

    async def test_the_company_size_limit_counts_waiting_invitations(self):
        with mock.patch.object(people, "MAX_USERS", 3):
            await self.invite()
            await self.invite()
            self.assertEqual((await self.owner.post("/api/people/invite", {"email": unique("late"), "role": "member"}))[0], 400)

    async def test_only_the_invited_projects_are_given(self):
        pid = await self.project("Alpha")
        other = await self.project("Beta")
        b, _ = await self.join(project_ids=[pid])
        _, projects = await b.get("/api/projects")
        self.assertEqual([p["name"] for p in projects], ["Alpha"])
        self.assertEqual((await b.get(f"/api/projects/{other}"))[0], 404)


class RoleTests(Company):
    async def test_members_are_refused_by_default(self):
        b, _ = await self.join("member")
        for method, path in (("GET", "/api/people"), ("GET", "/api/admins"), ("GET", "/api/central"), ("GET", "/api/calls"), ("GET", "/api/contacts"), ("GET", "/api/company"),
                             ("GET", "/api/mail"), ("POST", "/api/agents"), ("PUT", "/api/central"), ("GET", "/api/some-route-added-next-year"), ("POST", "/api/people/invite"),
                             ("GET", "/api/platform/workspaces"), ("GET", "/api/backups"), ("GET", "/api/docs")):
            st, _ = await b.request(method, path, {} if method != "GET" else None)
            self.assertEqual(st, 403, f"{method} {path}")

    async def test_what_a_member_can_open(self):
        b, _ = await self.join("member")
        for path in ("/api/me", "/api/people/directory", "/api/projects", "/api/drives", "/api/agents"):
            self.assertEqual((await b.get(path))[0], 200, path)

    async def test_a_member_sees_the_ai_team_but_only_names_and_jobs(self):
        self.agent("Ava")
        b, _ = await self.join("member")
        _, team = await b.get("/api/agents")
        self.assertEqual(team[0]["name"], "Ava")
        self.assertFalse({"email", "mail_login", "mail_password", "persona", "bio", "caps_off"} & set(team[0]))

    async def test_a_member_cannot_invite_or_change_anyone(self):
        member, _ = await self.join("member")
        self.assertEqual((await member.post("/api/people/invite", {"email": unique("a"), "role": "admin"}))[0], 403)
        owner_id = await self.person_id(self.owner_email)
        self.assertEqual((await member.patch(f"/api/people/{owner_id}", {"role": "member"}))[0], 403)
        self.assertEqual((await member.delete(f"/api/people/{owner_id}"))[0], 403)

    async def test_an_admin_runs_the_workspace_but_cannot_touch_the_owner(self):
        admin, _ = await self.join("admin", "Alex Admin")
        self.assertEqual((await admin.get("/api/people"))[0], 200)
        self.assertEqual((await admin.get("/api/central"))[0], 200)
        owner_id = await self.person_id(self.owner_email)
        self.assertEqual((await admin.delete(f"/api/people/{owner_id}"))[0], 400)
        self.assertEqual((await admin.patch(f"/api/people/{owner_id}", {"role": "member"}))[0], 400)

    async def test_an_admin_manages_the_people_they_added_and_the_owner_manages_everyone(self):
        admin, _ = await self.join("admin")
        invitee_email = unique("byadmin")
        inv = await self.invite(admin, invitee_email, "member")
        guest = Browser(main.app)
        await guest.post(f"/api/invites/{inv['join_path'].split('/')[-1]}/accept", {"name": "Added By Admin", "password": PASSWORD})
        added = await self.person_id(invitee_email)
        other_admin, _ = await self.join("admin", "Other Admin")
        self.assertEqual((await other_admin.patch(f"/api/people/{added}", {"title": "Designer"}))[0], 400)  # not theirs
        self.assertEqual((await admin.patch(f"/api/people/{added}", {"title": "Designer"}))[0], 200)
        self.assertEqual((await self.owner.patch(f"/api/people/{added}", {"title": "Lead designer"}))[0], 200)
        _, data = await self.owner.get("/api/people")
        self.assertEqual(next(p for p in data["people"] if p["id"] == added)["title"], "Lead designer")

    async def test_changing_a_role_signs_the_person_out_so_the_new_role_applies(self):
        member, email = await self.join("member")
        pid = await self.person_id(email)
        self.assertEqual((await member.get("/api/people"))[0], 403)
        self.assertEqual((await self.owner.patch(f"/api/people/{pid}", {"role": "admin"}))[0], 200)
        self.assertEqual((await member.get("/api/me"))[0], 401)  # their old session is gone
        again = Browser(main.app)
        self.assertEqual((await again.post("/api/auth/login", {"username": email, "password": PASSWORD}))[0], 200)
        self.assertEqual((await again.get("/api/people"))[0], 200)

    async def test_you_cannot_remove_or_demote_yourself_and_nobody_can_make_an_owner(self):
        owner_id = await self.person_id(self.owner_email)
        self.assertEqual((await self.owner.delete(f"/api/people/{owner_id}"))[0], 400)
        member, email = await self.join("member")
        pid = await self.person_id(email)
        self.assertEqual((await self.owner.patch(f"/api/people/{pid}", {"role": "owner"}))[0], 400)

    async def test_removing_someone_signs_them_out_and_frees_their_email(self):
        member, email = await self.join("member")
        pid = await self.person_id(email)
        self.assertEqual((await self.owner.delete(f"/api/people/{pid}"))[0], 200)
        self.assertEqual((await member.get("/api/me"))[0], 401)
        self.assertIsNone(directory.find(email))
        self.assertEqual((await Browser(main.app).post("/api/auth/login", {"username": email, "password": PASSWORD}))[0], 401)
        await self.invite(email=email)  # they can be invited again

    async def test_the_people_page_lists_people_then_waiting_invitations(self):
        await self.join("admin", "Alex Admin")
        waiting = unique("waiting")
        await self.invite(email=waiting, role="member")
        _, data = await self.owner.get("/api/people")
        self.assertEqual([p["role"] for p in data["people"]], ["owner", "admin"])
        self.assertEqual([i["email"] for i in data["invites"]], [waiting])
        self.assertTrue(data["people"][0]["you"])
        self.assertEqual(set(data["roles"]), {"owner", "admin", "member"})


class ProjectTests(Company):
    async def test_members_see_only_their_projects_and_who_is_on_them(self):
        ava = self.agent("Ava")
        mine, hidden = await self.project("Mine", [ava]), await self.project("Hidden", [ava])
        member, email = await self.join("member", "Sam Member")
        pid = await self.person_id(email)
        self.assertEqual((await self.owner.post(f"/api/projects/{mine}/people", {"person_id": pid}))[0], 200)
        _, listing = await member.get("/api/projects")
        self.assertEqual([p["name"] for p in listing], ["Mine"])
        self.assertEqual([x["name"] for x in listing[0]["people"]], ["Sam Member"])
        _, detail = await member.get(f"/api/projects/{mine}")
        self.assertEqual([m["name"] for m in detail["members"]], ["Ava"])
        self.assertEqual(detail["working_files"], [])  # the AI team's private drafts are not shown to a member
        self.assertEqual((await member.get(f"/api/projects/{hidden}"))[0], 404)
        _, everything = await self.owner.get("/api/projects")
        self.assertEqual(len(everything), 2)

    async def test_taking_someone_off_a_project_closes_it_to_them(self):
        pid_project = await self.project("Gone soon")
        member, email = await self.join("member", project_ids=[pid_project])
        self.assertEqual((await member.get(f"/api/projects/{pid_project}"))[0], 200)
        await self.owner.delete(f"/api/projects/{pid_project}/people/{await self.person_id(email)}")
        self.assertEqual((await member.get(f"/api/projects/{pid_project}"))[0], 404)

    async def test_updates_say_who_wrote_them(self):
        project = await self.project("Talk")
        member, _ = await self.join("member", "Sam Member", project_ids=[project])
        self.assertEqual((await member.post(f"/api/projects/{project}/updates", {"text": "Draft is ready for review"}))[0], 200)
        self.assertEqual((await self.owner.post(f"/api/projects/{project}/updates", {"text": "Thanks, looking now"}))[0], 200)
        _, detail = await member.get(f"/api/projects/{project}")
        by_text = {u["text"]: u for u in detail["updates"]}
        self.assertEqual((by_text["Draft is ready for review"]["who"], by_text["Draft is ready for review"]["mine"]), ("Sam Member", True))
        self.assertFalse(by_text["Thanks, looking now"]["mine"])
        self.assertTrue(by_text["Thanks, looking now"]["who"])  # the owner is named too, not just "You"

    async def test_a_member_cannot_post_on_a_project_that_is_not_theirs(self):
        hidden = await self.project("Hidden")
        member, _ = await self.join("member")
        self.assertEqual((await member.post(f"/api/projects/{hidden}/updates", {"text": "hello"}))[0], 404)

    async def test_a_members_task_for_an_ai_teammate_carries_no_admin_authority(self):
        ava, bob = self.agent("Ava"), self.agent("Bob")
        project = await self.project("Work", [ava])
        member, _ = await self.join("member", "Sam Member", project_ids=[project])
        st, r = await member.post(f"/api/projects/{project}/tasks", {"agent_id": ava, "title": "Draft the offer email", "detail": "Keep it short"})
        self.assertEqual(st, 200, r)
        with db.use(self.ws):
            duty = db.q1("SELECT * FROM duties WHERE id=?", (r["id"],))
            self.assertEqual((duty["agent_id"], duty["project_id"], duty["source"], duty["created_by"]), (ava, project, "person", "Sam Member"))
            self.assertFalse(agents.admin_ordered(duty))  # an AI manager must not treat a member's request as the admin's order
        self.assertEqual((await member.post(f"/api/projects/{project}/tasks", {"agent_id": bob, "title": "Not on the project"}))[0], 400)

    async def test_an_admins_task_keeps_the_admin_authority(self):
        ava = self.agent("Ava")
        project = await self.project("Work", [ava])
        st, r = await self.owner.post(f"/api/projects/{project}/tasks", {"agent_id": ava, "title": "Do the thing"})
        with db.use(self.ws):
            self.assertTrue(agents.admin_ordered(db.q1("SELECT * FROM duties WHERE id=?", (r["id"],))))


if __name__ == "__main__":
    unittest.main()

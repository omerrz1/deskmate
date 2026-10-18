"""Only the team that runs the server can create companies: with SIGNUP_CODE set, signing up needs the code, and nothing else creates a company."""
import unittest
from unittest import mock

from app import directory, keys, main

from .asgi import Browser

PASSWORD = "Correct-horse-battery-9"
CODE = "team-secret-4f9c2a7d"
_n = [0]


def body(code=None, **kw):
    _n[0] += 1
    b = {"company": f"Company {_n[0]}", "email": f"founder{_n[0]}@example.com", "password": PASSWORD, "team": ""}
    if code is not None:
        b["code"] = code
    return {**b, **kw}


class SignupGateTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._keys = mock.patch.object(keys, "complete", lambda: True)
        cls._keys.start()

    @classmethod
    def tearDownClass(cls):
        cls._keys.stop()

    def gate(self, code=CODE, open_=True):
        return mock.patch.multiple(directory, SIGNUP_CODE=code, SIGNUPS_OPEN=open_)

    async def status(self):
        return (await Browser(main.app).get("/api/auth/status"))[1]

    async def test_the_site_is_told_a_code_is_needed_and_hides_its_public_buttons(self):
        with self.gate():
            s = await self.status()
        self.assertTrue(s["signup_code_required"])
        self.assertTrue(s["signup_open"])        # the page for the team still exists
        self.assertFalse(s["signup_public"])     # but the public site does not advertise it

    async def test_without_a_code_requirement_nothing_changes(self):
        with self.gate(code=""):
            s = await self.status()
            self.assertEqual((s["signup_code_required"], s["signup_public"]), (False, True))
            self.assertEqual((await Browser(main.app).post("/api/auth/signup", body()))[0], 200)

    async def test_no_code_or_a_wrong_code_creates_nothing(self):
        before = directory.count()
        with self.gate():
            for attempt in (body(), body(code=""), body(code="   "), body(code="wrong"), body(code=CODE[:-1]), body(code=CODE + "x"), body(code=CODE.upper())):
                st, r = await Browser(main.app).post("/api/auth/signup", attempt)
                self.assertEqual(st, 403, attempt.get("code"))
                self.assertIn("access code", r["detail"])
        self.assertEqual(directory.count(), before)

    async def test_the_right_code_creates_the_company_and_signs_the_founder_in(self):
        with self.gate():
            b = Browser(main.app)
            st, r = await b.post("/api/auth/signup", body(code=f"  {CODE}  "))  # stray spaces from copying are forgiven
            self.assertEqual(st, 200, r)
            self.assertEqual((await b.get("/api/me"))[1]["role"], "owner")

    async def test_the_refusal_does_not_hint_at_what_was_wrong(self):
        with self.gate():
            _, a = await Browser(main.app).post("/api/auth/signup", body(code="nope"))
            _, c = await Browser(main.app).post("/api/auth/signup", body())
        self.assertEqual(a["detail"], c["detail"])
        self.assertNotIn(CODE, str(a))

    async def test_the_code_is_checked_before_anything_about_the_form(self):
        with self.gate():
            st, r = await Browser(main.app).post("/api/auth/signup", {"company": "", "email": "not-an-email", "password": "x", "code": "wrong"})
        self.assertEqual(st, 403)  # a stranger learns nothing about what a valid sign-up looks like

    async def test_switching_sign_up_off_still_wins_over_a_correct_code(self):
        with self.gate(open_=False):
            self.assertEqual((await Browser(main.app).post("/api/auth/signup", body(code=CODE)))[0], 403)

    async def test_people_invited_into_a_company_never_need_the_code(self):
        with self.gate():
            owner = Browser(main.app)
            await owner.post("/api/auth/signup", body(code=CODE))
            st, inv = await owner.post("/api/people/invite", {"email": "newcomer@example.com", "role": "member"})
            self.assertEqual(st, 200, inv)
            joiner = Browser(main.app)
            st, r = await joiner.post(f"/api/invites/{inv['join_path'].split('/')[-1]}/accept", {"name": "New Comer", "password": PASSWORD})
            self.assertEqual(st, 200, r)
            self.assertEqual((await joiner.get("/api/me"))[1]["role"], "member")

    async def test_there_is_no_other_way_to_make_a_company(self):
        with self.gate():
            owner = Browser(main.app)
            await owner.post("/api/auth/signup", body(code=CODE))
            before = directory.count()
            for method, path, payload in (("POST", "/api/platform/workspaces", {"company": "Sneaky", "email": "s@example.com", "password": PASSWORD}),
                                          ("POST", "/api/workspaces", {"company": "Sneaky", "email": "s@example.com", "password": PASSWORD}),
                                          ("PUT", "/api/platform/workspaces", {"company": "Sneaky"}),
                                          ("POST", "/api/auth/setup", {"token": "x", "username": "sneaky@example.com", "password": PASSWORD})):  # the old first-run screen, switched off
                st, _ = await owner.request(method, path, payload)
                self.assertIn(st, (422,) if path == "/api/workspaces" else (403, 404, 405), f"{method} {path}")
        self.assertEqual(directory.count(), before)


if __name__ == "__main__":
    unittest.main()

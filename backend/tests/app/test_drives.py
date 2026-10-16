"""The file systems: every person's own files, every AI employee's computer, the company drive and each project's drive.
The point of most of these tests is what must NOT be reachable."""
import unittest

from app import agents, db, files, search, team

from .test_people import Company, PASSWORD, unique  # noqa: F401  (Company signs an owner up; the other names are shared helpers)
from .asgi import Browser
from app import main


class DriveBase(Company):
    async def make_file(self, who, owner, title="Notes", body="hello", folder_id=None, kind="doc"):
        st, r = await who.post("/api/fs/file", {"owner": owner, "folder_id": folder_id, "title": title, "body": body, "kind": kind})
        self.assertEqual(st, 200, r)
        return r["id"]

    async def my_drive(self, who):
        _, me = await who.get("/api/me")
        return me["drive"]

    async def folder(self, who, owner, name, parent=None):
        st, r = await who.post("/api/fs/folder", {"owner": owner, "parent_id": parent, "name": name})
        self.assertEqual(st, 200, r)
        return r["id"]


class EmployeeComputerTests(DriveBase):
    async def test_every_employee_computer_has_folders_for_and_from_the_admins_listed_first(self):
        ava = self.agent("Ava")
        st, ls = await self.owner.get("/api/fs", query=f"owner={ava}")
        self.assertEqual(st, 200)
        self.assertEqual([(f["name"], f["purpose"]) for f in ls["folders"][:2]], [("For admins", "admin"), ("From admins", "from_admin")])
        self.assertEqual({f["name"] for f in ls["folders"]}, {"For admins", "From admins", "Documents", "Reports", "Spreadsheets", "Downloads"})

    async def test_an_older_employee_gets_the_folders_the_first_time_someone_looks(self):
        ava = self.agent("Ava")
        with db.use(self.ws):
            db.run("DELETE FROM folders WHERE owner=?", (ava,))  # as it was before these folders existed
        _, ls = await self.owner.get("/api/fs", query=f"owner={ava}")
        self.assertIn("For admins", [f["name"] for f in ls["folders"]])
        _, tree = await self.owner.get("/api/fs/folders", query=f"owner={ava}")
        self.assertEqual({f["name"]: f["purpose"] for f in tree}["For admins"], "admin")

    async def test_the_folders_the_system_keeps_cannot_be_renamed_moved_or_deleted(self):
        ava = self.agent("Ava")
        _, ls = await self.owner.get("/api/fs", query=f"owner={ava}")
        fid = ls["folders"][0]["id"]
        docs = next(f["id"] for f in ls["folders"] if f["name"] == "Documents")
        self.assertEqual((await self.owner.patch(f"/api/fs/folder/{fid}", {"name": "Secret"}))[0], 400)
        self.assertEqual((await self.owner.patch(f"/api/fs/folder/{fid}", {"parent_id": docs}))[0], 400)
        self.assertEqual((await self.owner.delete(f"/api/fs/folder/{fid}"))[0], 400)
        self.assertEqual((await self.owner.patch(f"/api/fs/folder/{docs}", {"name": "Papers"}))[0], 200)  # an ordinary folder is free to change

    async def test_the_employees_know_to_use_the_for_admins_folder(self):
        text = agents.FILE_RULES + " ".join(str(t.get("parameters")) for t in files.TOOLS if t["name"] == "create_document")
        self.assertIn("For admins", text)
        self.assertIn("From admins", agents.FILE_RULES)

    async def test_an_employee_can_save_into_for_admins_by_path(self):
        ava = self.agent("Ava")
        with db.use(self.ws):
            fid = files.folder_id(ava, "For admins")
            self.assertEqual(files.purpose_of(fid), "admin")  # saving by name lands in the folder that carries the colour, not a lookalike


class DrivesListTests(DriveBase):
    async def test_an_admin_sees_every_place_grouped(self):
        ava = self.agent("Ava")
        project = await self.project("Spring promotion", [ava])
        _, places = await self.owner.get("/api/drives")
        self.assertEqual([(p["group"], p["kind"], p["label"]) for p in places],
                         [("Mine", "person", "My files"), ("Company", "shared", "Company drive"), ("Projects", "project", "Spring promotion"), ("AI team", "agent", "Ava's computer")])
        self.assertEqual(places[0]["owner"], await self.my_drive(self.owner))
        self.assertEqual(places[2]["owner"], str(-project))

    async def test_a_member_sees_only_their_own_files_and_their_projects(self):
        ava = self.agent("Ava")
        mine, other = await self.project("Mine", [ava]), await self.project("Other", [ava])
        member, _ = await self.join("member", project_ids=[mine])
        _, places = await member.get("/api/drives")
        self.assertEqual([(p["kind"], p["label"]) for p in places], [("person", "My files"), ("project", "Mine")])

    async def test_a_new_persons_files_start_with_sensible_folders(self):
        member, _ = await self.join("member")
        _, ls = await member.get("/api/fs", query=f"owner={await self.my_drive(member)}")
        self.assertEqual({f["name"] for f in ls["folders"]}, {"Documents", "Spreadsheets", "Uploads"})


class PrivacyTests(DriveBase):
    async def test_your_own_files_are_yours_alone_even_the_owner_cannot_open_them(self):
        member, email = await self.join("member", "Sam Member")
        mine = await self.my_drive(member)
        did = await self.make_file(member, mine, "Private notes", "my salary ask")
        for attempt in (("GET", "/api/fs", f"owner={mine}"), ("GET", "/api/fs/folders", f"owner={mine}"), ("GET", "/api/fs/search", f"owner={mine}&q=notes")):
            st, _ = await self.owner.request(attempt[0], attempt[1], query=attempt[2])
            self.assertEqual(st, 403, attempt)
        for method, path, body in (("GET", f"/api/docs/{did}", None), ("PATCH", f"/api/docs/{did}", {"title": "x"}), ("DELETE", f"/api/docs/{did}", None), ("GET", f"/api/docs/{did}/download", None)):
            st, _ = await self.owner.request(method, path, body)
            self.assertEqual(st, 404, f"{method} {path}")  # not found, so nobody can even tell it exists
        _, listing = await self.owner.get("/api/docs")
        self.assertNotIn("Private notes", [d["title"] for d in listing])
        self.assertEqual((await member.get(f"/api/docs/{did}"))[1]["body"], "my salary ask")  # and the person themselves can

    async def test_the_global_search_never_finds_someone_elses_files(self):
        member, email = await self.join("member", "Sam Member")
        await self.make_file(member, await self.my_drive(member), "Zebra plan", "zebra")
        _, hits = await self.owner.get("/api/search", query="q=zebra")
        self.assertEqual([h for h in hits if h["title"] == "Zebra plan"], [])
        with db.use(self.ws):
            member_id = db.q1("SELECT id FROM admins WHERE username=?", (email,))["id"]
            found = search.search("zebra", person=member_id)
        self.assertTrue(any(g for g in found if "Zebra plan" in str(g)))

    async def test_an_ai_employee_even_a_manager_cannot_use_a_persons_files(self):
        manager, ava = self.agent("Maya"), self.agent("Ava")
        with db.use(self.ws):
            db.update("agents", manager, is_manager=1)
        member, _ = await self.join("member")
        mine = await self.my_drive(member)
        with db.use(self.ws):
            for who in (agents.get(manager), agents.get(ava), None):
                self.assertFalse(team.can_use_owner(who, int(mine)), who)

    async def test_a_manager_cannot_list_a_persons_files_with_the_tool(self):
        manager = self.agent("Maya")
        with db.use(self.ws):
            db.update("agents", manager, is_manager=1)
        member, _ = await self.join("member")
        await self.make_file(member, await self.my_drive(member), "Personal diary", "dear diary")
        with db.use(self.ws):
            res, _ = await agents.run_agent_tool(agents.get(manager), "list_documents", {"query": "diary"})
        self.assertEqual(res, [])


class MemberBoundaryTests(DriveBase):
    async def test_a_member_cannot_open_the_company_drive_an_employee_computer_or_another_project(self):
        ava = self.agent("Ava")
        mine, other = await self.project("Mine", [ava]), await self.project("Other", [ava])
        member, _ = await self.join("member", project_ids=[mine])
        for owner in ("shared", str(ava), str(-other)):
            st, _ = await member.get("/api/fs", query=f"owner={owner}")
            self.assertEqual(st, 403, owner)
        self.assertEqual((await member.get("/api/fs", query=f"owner={-mine}"))[0], 200)

    async def test_a_member_cannot_reach_another_drive_through_a_folder_or_file_id(self):
        ava = self.agent("Ava")
        project = await self.project("Mine", [ava])
        member, _ = await self.join("member", project_ids=[project])
        mine = await self.my_drive(member)
        secret_doc = await self.make_file(self.owner, "shared", "Pricing secrets", "margin 40%")
        secret_folder = await self.folder(self.owner, "shared", "Board only")
        my_doc = await self.make_file(member, mine, "Mine")
        my_folder = await self.folder(member, mine, "Stuff")
        self.assertEqual((await member.get("/api/fs", query=f"owner={mine}&folder={secret_folder}"))[0], 404)  # my drive, their folder
        self.assertEqual((await member.patch(f"/api/docs/{my_doc}", {"folder_id": secret_folder}))[0], 404)  # moving my file into their folder
        self.assertEqual((await member.patch(f"/api/fs/folder/{my_folder}", {"parent_id": secret_folder}))[0], 404)
        self.assertEqual((await member.post("/api/fs/file", {"owner": mine, "folder_id": secret_folder, "title": "x", "body": "y"}))[0], 404)
        for method, path, body in (("GET", f"/api/docs/{secret_doc}", None), ("PATCH", f"/api/docs/{secret_doc}", {"title": "mine now"}), ("DELETE", f"/api/docs/{secret_doc}", None),
                                   ("GET", f"/api/docs/{secret_doc}/download", None), ("PATCH", f"/api/fs/folder/{secret_folder}", {"name": "hacked"}), ("DELETE", f"/api/fs/folder/{secret_folder}", None),
                                   ("POST", f"/api/docs/{secret_doc}/copy", {"owner": mine})):
            st, _ = await member.request(method, path, body)
            self.assertEqual(st, 404, f"{method} {path}")
        self.assertEqual((await member.post(f"/api/docs/{my_doc}/copy", {"owner": "shared"}))[0], 403)  # and cannot copy out to a drive they cannot open
        _, still = await self.owner.get(f"/api/docs/{secret_doc}")
        self.assertEqual((still["title"], still["body"]), ("Pricing secrets", "margin 40%"))  # nothing was touched

    async def test_a_member_and_the_admins_share_a_project_drive(self):
        ava = self.agent("Ava")
        project = await self.project("Shared", [ava])
        member, _ = await self.join("member", "Sam Member", project_ids=[project])
        did = await self.make_file(member, str(-project), "Draft offer", "v1")
        _, seen_by_owner = await self.owner.get("/api/fs", query=f"owner={-project}")
        self.assertIn("Draft offer", [f["title"] for f in seen_by_owner["files"]])
        self.assertEqual((await self.owner.patch(f"/api/docs/{did}", {"body": "v2 with changes"}))[0], 200)
        self.assertEqual((await member.get(f"/api/docs/{did}"))[1]["body"], "v2 with changes")
        _, detail = await member.get(f"/api/projects/{project}")
        self.assertIn("Draft offer", [d["title"] for d in detail["deliverables"]])

    async def test_a_member_can_copy_between_their_own_drives(self):
        project = await self.project("Shared")
        member, _ = await self.join("member", project_ids=[project])
        mine = await self.my_drive(member)
        did = await self.make_file(member, mine, "Draft")
        st, r = await member.post(f"/api/docs/{did}/copy", {"owner": str(-project)})
        self.assertEqual(st, 200)
        self.assertEqual((await member.get(f"/api/docs/{r['id']}"))[1]["title"], "Draft")

    async def test_taking_a_member_off_a_project_closes_its_drive_to_them(self):
        project = await self.project("Shared")
        member, email = await self.join("member", project_ids=[project])
        self.assertEqual((await member.get("/api/fs", query=f"owner={-project}"))[0], 200)
        await self.owner.delete(f"/api/projects/{project}/people/{await self.person_id(email)}")
        self.assertEqual((await member.get("/api/fs", query=f"owner={-project}"))[0], 403)


class LeavingTests(DriveBase):
    async def test_files_of_someone_removed_go_to_whoever_removed_them(self):
        member, email = await self.join("member", "Sam Member")
        mine = await self.my_drive(member)
        folder = await self.folder(member, mine, "Reports")
        await self.make_file(member, mine, "Q3 summary", "numbers", folder_id=folder)
        await self.make_file(member, mine, "Loose note", "hi")
        self.assertEqual((await self.owner.delete(f"/api/people/{await self.person_id(email)}"))[0], 200)
        owners = await self.my_drive(self.owner)
        _, top = await self.owner.get("/api/fs", query=f"owner={owners}")
        landing = next(f["id"] for f in top["folders"] if f["name"] == "From Sam Member")
        _, inside = await self.owner.get("/api/fs", query=f"owner={owners}&folder={landing}")
        self.assertEqual((sorted(f["name"] for f in inside["folders"]), [f["title"] for f in inside["files"]]), (["Documents", "Reports", "Spreadsheets", "Uploads"], ["Loose note"]))  # their folders, as they left them
        with db.use(self.ws):
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM documents WHERE agent_id=?", (int(mine),))["n"], 0)  # nothing is left on the old drive

    async def test_removing_someone_can_delete_their_files_instead(self):
        member, email = await self.join("member")
        mine = await self.my_drive(member)
        await self.make_file(member, mine, "Gone", "bye")
        self.assertEqual((await self.owner.delete(f"/api/people/{await self.person_id(email)}", query="keep_files=false"))[0], 200)
        with db.use(self.ws):
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM documents WHERE title='Gone'")["n"], 0)
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM folders WHERE owner=?", (int(mine),))["n"], 0)


if __name__ == "__main__":
    unittest.main()

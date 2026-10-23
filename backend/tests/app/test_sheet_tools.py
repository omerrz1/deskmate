"""What an employee can do with sheets: profile, clean, merge, query, and a task for every row that carries on until it is finished."""
import json
import unittest
from unittest import mock

from app import agents, capabilities, db, files, office, runtime, sheet_tools, workbench
from .test_call_task_and_report import CallReportBase

LEADS = (["Name", "Mobile", "E-mail"], [["  JOHN smith ", "07447 781753", "John@X.com"], ["john smith", "+44 7447 781753", ""], ["Mary Jones", "07700 900111", "mary@x.com"], ["", "", ""]])
WEBINAR = (["Student", "Phone", "Email", "Paid"], [["john smith", "+447447781753", "", "500"], ["Zed Ray", "07700 900333", "zed@x.com", "100"]])


class SheetBase(CallReportBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        with db.use(self.ws):
            self.aid, self.mid = self.make_chat()

    def agent(self):
        return {**agents.get(self.aid), "_meeting_id": self.mid}

    def sheet(self, title, cols, rows):
        fid = files.folder_id(self.aid, "Spreadsheets")
        return db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (self.aid, title, "sheet", json.dumps({"columns": cols, "rows": rows}), fid, db.now(), db.now()))

    def table(self, ref):
        d = db.q1("SELECT * FROM documents WHERE id=?", (ref,)) if isinstance(ref, int) else db.q1("SELECT * FROM documents WHERE title=?", (ref,))
        t = json.loads(d["body"])
        return t["columns"], t["rows"]

    async def tool(self, name, **a):
        out, _link = await sheet_tools.run_tool(self.agent(), name, a)
        return out

    async def finish_jobs(self):
        for task in list(sheet_tools._tasks.values()):
            await task


class ProfileAndCleanToolTests(SheetBase):
    async def test_profile_describes_the_sheet_and_hands_back_the_fixes(self):
        with db.use(self.ws):
            self.sheet("Leads", *LEADS)
            out = await self.tool("sheet_profile", title="Leads")
        self.assertEqual((out["sheet"], out["rows"]), ("Leads", 4))
        self.assertEqual(out["duplicates"]["extra_rows"], 1)
        self.assertTrue(any(s["op"] == "dedupe" for s in out["suggested_operations"]))

    async def test_clean_makes_a_new_sheet_and_leaves_the_original_alone(self):
        with db.use(self.ws):
            did = self.sheet("Leads", *LEADS)
            before = db.q1("SELECT body FROM documents WHERE id=?", (did,))["body"]
            out = await self.tool("sheet_clean", title="Leads", operations=[{"op": "remove_empty_rows"}, {"op": "trim"}, {"op": "phone"}, {"op": "email"}, {"op": "case"}, {"op": "dedupe"}], country_code="44")
            self.assertEqual(out["saved_as"]["title"], "Leads (clean)")
            self.assertEqual((out["rows_before"], out["rows_after"]), (4, 2))
            cols, rows = self.table("Leads (clean)")
            self.assertEqual(rows[0], ["John Smith", "+447447781753", "john@x.com"])
            self.assertEqual(db.q1("SELECT body FROM documents WHERE id=?", (did,))["body"], before)  # untouched
            again = await self.tool("sheet_clean", title="Leads", operations=[{"op": "trim"}])
            self.assertEqual(again["saved_as"]["title"], "Leads (clean) (2)")  # never two sheets with one name: the next lookup by title must be unambiguous
            named = await self.tool("sheet_clean", title="Leads", operations=[{"op": "trim"}], output_title="Master leads")
            self.assertEqual(named["saved_as"]["title"], "Master leads")

    async def test_a_dry_run_reports_and_previews_but_saves_nothing(self):
        with db.use(self.ws):
            self.sheet("Leads", *LEADS)
            count = db.q1("SELECT COUNT(*) n FROM documents")["n"]
            out = await self.tool("sheet_clean", title="Leads", operations=[{"op": "dedupe"}], dry_run=True)
            self.assertFalse(out["saved"])
            self.assertEqual(out["operations"][0]["rows_removed"], 1)
            self.assertEqual(len(out["preview"]["rows"]), 3)
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM documents")["n"], count)

    async def test_in_place_changes_the_original_but_keeps_a_backup(self):
        with db.use(self.ws):
            did = self.sheet("Leads", *LEADS)
            out = await self.tool("sheet_clean", title="Leads", operations=[{"op": "remove_empty_rows"}], in_place=True)
            self.assertEqual(out["saved_as"]["id"], did)
            self.assertEqual(len(self.table(did)[1]), 3)
            backup = self.table(out["saved_as"]["backup"])
            self.assertEqual(len(backup[1]), 4)  # the sheet as it was
            self.assertIn("backup before clean", out["saved_as"]["backup"])

    async def test_no_operations_means_do_what_the_profile_suggests(self):
        with db.use(self.ws):
            self.sheet("Leads", *LEADS)
            out = await self.tool("sheet_clean", title="Leads", country_code="44")
            self.assertTrue(out["applied_the_profile_suggestions"])
            cols, rows = self.table(out["saved_as"]["title"])
            self.assertEqual(len(rows), 2)  # empty row gone, the two Johns merged
            self.sheet("Tidy", ["Name", "Phone"], [["Ann Lee", "+447447781753"]])
            self.assertIn("nothing_to_do", await self.tool("sheet_clean", title="Tidy"))

    async def test_mistakes_come_back_as_plain_errors_the_employee_can_act_on(self):
        with db.use(self.ws):
            self.sheet("Leads", *LEADS)
            self.assertIn("Unknown operation 'shred'", (await self.tool("sheet_clean", title="Leads", operations=[{"op": "shred"}]))["error"])
            self.assertIn("No accessible file named 'Nope'", (await self.tool("sheet_profile", title="Nope"))["error"])
            self.assertIn("Give the sheet's title", (await self.tool("sheet_profile"))["error"])
            self.assertIn("There is no column 'Town'", (await self.tool("sheet_clean", title="Leads", operations=[{"op": "fill_blank", "column": "Town", "value": "x"}]))["error"])
            doc = db.run("INSERT INTO documents(agent_id,title,kind,body,created_at,updated_at) VALUES(?,?,?,?,?,?)", (self.aid, "Notes", "doc", "text", db.now(), db.now()))
            self.assertIn("error", await self.tool("sheet_profile", id=doc))  # a document is not a sheet


class MergeAndQueryToolTests(SheetBase):
    async def test_two_sheets_are_merged_into_a_new_master_sheet(self):
        with db.use(self.ws):
            self.sheet("Leads", *LEADS)
            self.sheet("Webinar", *WEBINAR)
            clean = await self.tool("sheet_clean", title="Leads", operations=[{"op": "remove_empty_rows"}], output_title="Leads 2")
            out = await self.tool("sheet_merge", left="Leads 2", right="Webinar", output_title="Master")
            cols, rows = self.table("Master")
            self.assertEqual(cols, ["Name", "Mobile", "E-mail", "Paid", "Source"])
            self.assertEqual(len(rows), 3)  # John (both lead rows and the webinar row are one person), Mary, Zed
            self.assertEqual({r[0]: r[3] for r in rows}.get("  JOHN smith "), "500")
            self.assertEqual(out["rows"], {"Leads 2": 3, "Webinar": 2, "result": 3})
            self.assertIn("Mobile", out["matched_on"][0])
            self.assertEqual(clean["rows_after"], 3)

    async def test_a_merge_dry_run_shows_the_plan_and_saves_nothing(self):
        with db.use(self.ws):
            self.sheet("Leads", *LEADS)
            self.sheet("Webinar", *WEBINAR)
            count = db.q1("SELECT COUNT(*) n FROM documents")["n"]
            out = await self.tool("sheet_merge", left="Leads", right="Webinar", dry_run=True)
            self.assertFalse(out["saved"])
            self.assertTrue(out["column_mapping"])
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM documents")["n"], count)

    async def test_merge_errors_say_what_to_do(self):
        with db.use(self.ws):
            self.sheet("Leads", *LEADS)
            self.sheet("Things", ["Item", "Price"], [["Pen", "1"]])
            self.assertIn("cannot tell which columns identify the same person", (await self.tool("sheet_merge", left="Leads", right="Things"))["error"])
            self.assertIn("same sheet", (await self.tool("sheet_merge", left="Leads", right="Leads"))["error"])
            self.assertIn("No accessible file named 'Ghost'", (await self.tool("sheet_merge", left="Leads", right="Ghost"))["error"])

    async def test_query_filters_every_row_and_can_save_the_answer_as_a_sheet(self):
        rows = [[f"P{i}", f"07700 9{i:05d}" if i % 2 else "", "new" if i % 3 else "won", str(i)] for i in range(2000)]
        with db.use(self.ws):
            self.sheet("Big", ["Name", "Phone", "Status", "Spend"], rows)
            out = await self.tool("sheet_query", title="Big", where=[{"column": "Phone", "op": "empty"}, {"column": "Status", "op": "equals", "value": "won"}], limit=3, save_as="No phone, won")
            self.assertEqual((out["total_rows"], out["matching_rows"], out["shown"]), (2000, 334, 3))  # a hundred-odd rows is beyond what read_document pages through
            self.assertEqual(len(self.table("No phone, won")[1]), 334)
            top = await self.tool("sheet_query", title="Big", sort_by="Spend", descending=True, limit=2, columns=["Name", "Spend"])
            self.assertEqual(top["rows"], [["P1999", "1999"], ["P1998", "1998"]])
            groups = await self.tool("sheet_query", title="Big", group_by="Status", metrics=[{"op": "sum", "column": "Spend"}])
            self.assertEqual({g["value"]: g["count"] for g in groups["groups"]}, {"new": 1333, "won": 667})


class FakeModel:
    """A stand-in for the model: answers 'Segment' for every row, from the row's own name."""

    def __init__(self, fail=0, before_answer=None):
        self.calls, self.fail, self.before_answer = [], fail, before_answer

    async def __call__(self, system, user, model=None):
        self.calls.append(json.loads(user))
        if self.before_answer:
            self.before_answer()
        if self.fail:
            self.fail -= 1
            raise RuntimeError("model is down")
        return {"rows": [{"id": r["id"], "Segment": "VIP " + r["fields"].get("Name", "?")} for r in self.calls[-1]["rows"] if r["fields"].get("Name") != "Mystery"]}


class RowJobTests(SheetBase):
    def people(self, n):
        return [[f"Person {i}", f"07700 9{i:05d}"] for i in range(n)]

    async def start(self, title="People", **extra):
        return await self.tool("sheet_job", action="start", title=title, instruction="Label each person", outputs=["Segment"], **extra)

    async def test_a_preview_shows_the_answers_and_saves_nothing(self):
        model = FakeModel()
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", model):
            self.sheet("People", ["Name", "Phone"], self.people(30))
            before = db.q1("SELECT body FROM documents WHERE title='People'")["body"]
            out = await self.tool("sheet_job", action="preview", title="People", instruction="Label each person", outputs=["Segment"])
            self.assertFalse(out["saved"])
            self.assertEqual(len(out["preview"]), 5)
            self.assertEqual(out["preview"][0]["answer"], {"Segment": "VIP Person 0"})
            self.assertEqual(out["estimate_for_all"]["rows"], 30)
            self.assertEqual(db.q1("SELECT body FROM documents WHERE title='People'")["body"], before)

    async def test_a_job_fills_every_row_saves_as_it_goes_and_tells_the_chat(self):
        model = FakeModel()
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", model):
            self.sheet("People", ["Name", "Phone"], self.people(130))
            out = await self.start()
            self.assertTrue(out["started"])
            self.assertEqual(out["estimate"]["rows"], 130)
            await self.finish_jobs()
            cols, rows = self.table("People")
            self.assertEqual(cols, ["Name", "Phone", "Segment"])
            self.assertEqual([r[2] for r in rows], [f"VIP Person {i}" for i in range(130)])
            job = db.q1("SELECT * FROM sheet_jobs WHERE id=?", (out["job_id"],))
            self.assertEqual((job["status"], job["done"], job["failed"]), ("done", 130, 0))
            self.assertGreater(len(model.calls), 12)  # batches of ten, not one call per row and not one for everything
            text = self.chat_text(self.mid)[-1]
            self.assertIn("Finished the row-by-row job on 'People'", text)
            self.assertIn("130 row(s)", text)
            self.assertIn("Person 0 / 07700 900000 → VIP Person 0", text)
            status = await self.tool("sheet_job", action="status", job_id=out["job_id"])
            self.assertEqual((status["status"], status["filled"], status["rows_still_to_do"]), ("done", 130, 0))

    async def test_only_rows_that_are_still_blank_are_done_and_new_rows_are_picked_up_later(self):
        model = FakeModel()
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", model):
            rows = [[f"Person {i}", "x", "Kept by hand" if i < 5 else ""] for i in range(12)]
            self.sheet("People", ["Name", "Phone", "Segment"], rows)
            await self.start()
            await self.finish_jobs()
            segs = [r[2] for r in self.table("People")[1]]
            self.assertEqual(segs[:5], ["Kept by hand"] * 5)  # never overwritten
            self.assertEqual(segs[5:], [f"VIP Person {i}" for i in range(5, 12)])
            self.assertEqual(sum(len(c["rows"]) for c in model.calls), 7)
            d = db.q1("SELECT * FROM documents WHERE title='People'")
            t = json.loads(d["body"])
            t["rows"] += [["Newcomer", "y", ""]]
            db.update("documents", d["id"], body=json.dumps(t))
            job = db.q1("SELECT id FROM sheet_jobs")["id"]
            out = await self.tool("sheet_job", action="continue", job_id=job)  # new rows: carry on where it stopped
            self.assertEqual(out["status"], "running")
            await self.finish_jobs()
            self.assertEqual(self.table("People")[1][-1][2], "VIP Newcomer")

    async def test_a_trial_run_then_continue_finishes_the_rest(self):
        model = FakeModel()
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", model):
            self.sheet("People", ["Name", "Phone"], self.people(40))
            out = await self.start(max_rows=10)
            await self.finish_jobs()
            self.assertEqual(sum(1 for r in self.table("People")[1] if r[2]), 10)
            await self.tool("sheet_job", action="continue", job_id=out["job_id"])
            await self.finish_jobs()
            self.assertEqual(sum(1 for r in self.table("People")[1] if r[2]), 40)

    async def test_an_answer_always_lands_on_its_own_row_even_when_the_sheet_changes_meanwhile(self):
        """Regression for 'updates on the wrong cell': a row is found again by what it contains, never by where it was."""
        with db.use(self.ws):
            did = self.sheet("People", ["Name", "Phone"], self.people(20))

            def somebody_edits_the_sheet():
                d = db.q1("SELECT body FROM documents WHERE id=?", (did,))
                t = json.loads(d["body"])
                if t["rows"][0][0] != "Early bird":
                    t["rows"].insert(0, ["Early bird", "000"])   # everything shifts down a row...
                    t["rows"].pop(5)                              # ...and one person disappears
                    db.update("documents", did, body=json.dumps(t))
            model = FakeModel(before_answer=somebody_edits_the_sheet)
            with mock.patch.object(sheet_tools.llm, "complete_json", model):
                await self.start()
                await self.finish_jobs()
            cols, rows = self.table(did)
            for name, phone, seg in (r[:3] if len(r) > 2 else (r[0], r[1], "") for r in rows):
                if seg:
                    self.assertEqual(seg, "VIP " + name, (name, seg))  # nobody got somebody else's answer
            self.assertNotIn("Person 4", [r[0] for r in rows])  # the one that disappeared meanwhile
            self.assertGreaterEqual(sum(1 for r in rows if len(r) > 2 and r[2]), 18)

    async def test_a_row_the_model_has_nothing_for_stays_blank_and_does_not_loop_forever(self):
        model = FakeModel()
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", model):
            self.sheet("People", ["Name", "Phone"], [["Mystery", "1"], ["Ann", "2"]])
            await self.start()
            await self.finish_jobs()
            self.assertEqual([r[2] for r in self.table("People")[1]], ["", "VIP Ann"])
            job = db.q1("SELECT * FROM sheet_jobs")
            self.assertEqual((job["status"], job["done"], job["skipped"]), ("done", 1, 1))
            self.assertIn("1 row(s) had nothing to go on", self.chat_text(self.mid)[-1])

    async def test_a_job_over_the_limit_needs_the_admins_ok_first(self):
        model = FakeModel()
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", model), mock.patch.object(sheet_tools, "LARGE_JOB", 5):
            self.sheet("People", ["Name", "Phone"], self.people(20))
            out = await self.start()
            self.assertTrue(out["needs_the_admins_ok"])
            self.assertIn("confirm_large=true", out["next"])
            self.assertEqual(db.q1("SELECT COUNT(*) n FROM sheet_jobs")["n"], 0)
            self.assertEqual(model.calls, [])
            out = await self.start(confirm_large=True)
            self.assertTrue(out["started"])
            await self.finish_jobs()
            self.assertEqual(db.q1("SELECT done FROM sheet_jobs")["done"], 20)

    async def test_when_the_model_is_down_the_job_stops_politely_and_continues_later(self):
        model = FakeModel(fail=1000)
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", model), mock.patch.object(sheet_tools.asyncio, "sleep", mock.AsyncMock()):
            self.sheet("People", ["Name", "Phone"], self.people(15))
            out = await self.start()
            await self.finish_jobs()
            job = db.q1("SELECT * FROM sheet_jobs WHERE id=?", (out["job_id"],))
            self.assertEqual(job["status"], "failed")
            self.assertIn("model is down", job["last_error"])
            self.assertIn("stopped after 0 row(s)", self.chat_text(self.mid)[-1])
            model.fail = 0
            await self.tool("sheet_job", action="continue", job_id=out["job_id"])
            await self.finish_jobs()
            self.assertEqual(db.q1("SELECT status, done FROM sheet_jobs")["done"], 15)

    async def test_a_job_can_be_stopped_listed_and_resumed_after_a_restart(self):
        model = FakeModel()
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", model):
            self.sheet("People", ["Name", "Phone"], self.people(25))
            jid = db.run("INSERT INTO sheet_jobs(doc_id,agent_id,meeting_id,title,instruction,inputs,outputs,filter,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (db.q1("SELECT id FROM documents WHERE title='People'")["id"], self.aid, self.mid, "People", "Label each person", json.dumps(["Name", "Phone"]), json.dumps(["Segment"]), "[]", "running", db.now(), db.now()))
            sheet_tools._ensure_outputs(db.q1("SELECT * FROM documents WHERE title='People'"), ["Segment"])
            stopped = await self.tool("sheet_job", action="stop", job_id=jid)
            self.assertEqual(stopped["status"], "stopped")
            self.assertEqual(stopped["rows_still_to_do"], 25)
            self.assertEqual([j["job_id"] for j in (await self.tool("sheet_job", action="list"))["jobs"]], [jid])
            db.update("sheet_jobs", jid, status="running")  # the server was restarted while it ran
            with mock.patch("app.directory.active_ids", return_value=[self.ws]):
                await sheet_tools.resume_all()
            await self.finish_jobs()
            self.assertEqual(db.q1("SELECT status, done FROM sheet_jobs")["done"], 25)

    async def test_bad_job_requests_are_explained(self):
        with db.use(self.ws), mock.patch.object(sheet_tools.llm, "complete_json", FakeModel()):
            self.sheet("People", ["Name", "Phone"], self.people(3))
            self.assertIn("Give `instruction`", (await self.tool("sheet_job", action="start", title="People"))["error"])
            self.assertIn("action must be", (await self.tool("sheet_job", action="dance", title="People"))["error"])
            self.assertIn("can't find that job", (await self.tool("sheet_job", action="status", job_id=999))["error"])
            self.sheet("Done", ["Name", "Segment"], [["Ann", "VIP"]])
            nothing = await self.tool("sheet_job", action="start", title="Done", instruction="Label", outputs=["Segment"])
            self.assertIn("nothing_to_do", nothing)


class ChatFileTests(SheetBase):
    async def test_a_spreadsheet_shared_in_the_chat_points_the_employee_to_the_sheet_tools(self):
        with db.use(self.ws):
            agents.log_line(self.mid, "admin", None, "here are two lists")
            db.run("UPDATE meeting_msgs SET attachments=? WHERE meeting_id=? AND role='admin'", (json.dumps([{"doc_id": 5, "title": "Leads.xlsx", "kind": "sheet"}, {"doc_id": 6, "title": "Brief.pdf", "kind": "file"}]), self.mid))
            history = agents._chat_history(self.mid, agents.get(self.aid))
        text = history[-1]["content"]
        self.assertIn("Leads.xlsx (document id 5); it is a spreadsheet: look at it with sheet_profile", text)
        self.assertIn("Brief.pdf (document id 6); use read_document to inspect it", text)


class RegistrationTests(SheetBase):
    async def test_the_tools_are_offered_to_employees_without_clashing_with_the_old_ones(self):
        names = [t["name"] for t in office.TOOLS]
        for n in ("sheet_profile", "sheet_clean", "sheet_merge", "sheet_query", "sheet_job"):
            self.assertEqual(names.count(n), 1, n)
        self.assertNotIn("sheet_query", [t["name"] for t in workbench.TOOLS])   # the old, smaller one is retired
        self.assertNotIn("sheet_summary", [t["name"] for t in workbench.TOOLS])
        self.assertIn("sheet_to_customers", [t["name"] for t in workbench.TOOLS])
        harness = runtime.employees()  # raises if two tools share a name
        from harness.runtime import RunContext
        with db.use(self.ws):
            offered = {t.name for t in harness.tools_for(RunContext(agent={**agents.get(self.aid), "is_manager": 0}, kind="admin_chat"))}
        for n in sheet_tools.TOOL_NAMES:
            self.assertIn(n, offered, n)
        self.assertIn("browser_click", offered)  # an ordinary employee still gets the whole browser
        files_group = next(g for g in capabilities.GROUPS if g[0] == "files") if hasattr(capabilities, "GROUPS") else None
        if files_group:
            self.assertTrue(set(sheet_tools.TOOL_NAMES) <= set(files_group[3]))

    async def test_the_tools_run_through_the_normal_tool_path_and_every_employee_is_taught_the_method(self):
        with db.use(self.ws):
            self.sheet("Leads", *LEADS)
            out, link = await office.run_tool(self.agent(), "sheet_profile", {"title": "Leads"})
            self.assertEqual(out["rows"], 4)
        for needle in ("SPREADSHEET MASTERY", "sheet_profile on a sheet you have not seen", "NEVER DESTROY", "dry_run=true first", "sheet_job: preview on 5 rows",
                       "PEOPLE ARE MATCHED by phone, then email, never by row number", "Never say 'done' without the numbers", "WHEN BLOCKED, do not stop", "KEEP GOING"):
            self.assertIn(needle, agents.FILE_RULES)


if __name__ == "__main__":
    unittest.main()

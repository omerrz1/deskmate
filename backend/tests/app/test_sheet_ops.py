"""The spreadsheet engine (profile, clean, de-duplicate, merge, query). Pure functions on a table: no database, no model."""
import time
import unittest

from app import sheet_ops as so


class WhatAColumnIsTests(unittest.TestCase):
    def test_headers_are_understood_whatever_they_are_called(self):
        for header, role in (("Mobile", "phone"), ("Student phone number", "phone"), ("E-mail", "email"), ("Email Address", "email"), ("Full name", "name"), ("Company name", "company"),
                             ("Surname", "last_name"), ("First name", "first_name"), ("Post code", "postcode"), ("Status", "status"), ("Contact number", "phone"), ("Contact", "name"), ("Zzz", "")):
            self.assertEqual(so.header_role(header), role, header)

    def test_the_content_decides_what_a_column_is(self):
        phones = ["07447 781753", "+44 7700 900111", "07700-900222"]
        self.assertEqual(so.infer("Anything", phones)[0], "phone")
        self.assertEqual(so.infer("Contact", ["a@x.com", "b@y.org", "c@z.co.uk"])[0], "email")
        self.assertEqual(so.infer("When", ["12/10/2026", "13.10.2026", "2026-10-14"])[0], "date")  # a date written with dots is not a phone number
        self.assertEqual(so.infer("Spend", ["£1,200", "300", "45.50"])[0], "number")
        self.assertEqual(so.infer("Paid", ["yes", "no", "Yes"])[0], "yes_no")
        self.assertEqual(so.infer("Stage", ["new", "won", "lost", "new"] * 10)[0], "category")
        self.assertEqual(so.infer("Empty", ["", " "])[0], "empty")
        # a column called Email is one even when half its values are wrong: that is what has to be found
        self.assertEqual(so.infer("E-mail", ["john@x.com", "mary@x"])[0], "email")

    def test_a_reference_finds_a_column_by_header_number_or_role(self):
        cols = ["Name", "Mobile", "E-mail"]
        types = so.col_types(cols, [["A", "07447 781753", "a@x.com"], ["B", "07700 900111", "b@x.com"]])
        self.assertEqual(so.resolve_col(cols, "mobile"), 1)
        self.assertEqual(so.resolve_col(cols, 3), 2)
        self.assertEqual(so.resolve_col(cols, "phone", types), 1)  # by what it is
        self.assertEqual(so.resolve_col(cols, "e mail"), 2)
        with self.assertRaisesRegex(ValueError, "There is no column 'Town'. The columns are: Name, Mobile, E-mail"):
            so.resolve_col(cols, "Town")
        with self.assertRaisesRegex(ValueError, "Say which column"):
            so.resolve_col(cols, "", types)


class ValuesTests(unittest.TestCase):
    def test_phone_numbers_become_one_international_form_and_bad_ones_say_why(self):
        for raw, want in (("07447 781753", "+447447781753"), ("+44 7447 781753", "+447447781753"), ("0044 7447 781753", "+447447781753"), ("447447781753", "+447447781753"),
                          ("7447781753", "+447447781753"), ("+44 (0)7447 781753", "+447447781753"), ("(0744) 778-1753 ext 12", "+447447781753")):
            self.assertEqual(so.norm_phone(raw, "44"), (want, ""), raw)
        self.assertIn("letters", so.norm_phone("call me", "44")[1])
        self.assertIn("UK mobile", so.norm_phone("07447 78175", "44")[1])
        self.assertIn("no country code is set", so.norm_phone("07447 781753", "")[1])  # never guesses a country
        self.assertEqual(so.norm_phone("+971 50 123 4567", "44"), ("+971501234567", ""))
        self.assertEqual(so.norm_phone("", "44"), ("", ""))

    def test_dates_are_read_day_first_unless_the_month_is_clearly_first(self):
        self.assertEqual(so.parse_date("12/10/2026"), "2026-10-12")
        self.assertEqual(so.parse_date("12/10/2026", dayfirst=False), "2026-12-10")
        self.assertEqual(so.parse_date("31/12/2026", dayfirst=False), "2026-12-31")  # 31 can only be a day
        self.assertEqual(so.parse_date("12/31/2026"), "2026-12-31")
        for raw in ("2026-10-12 00:00:00", "13.10.26", "12 Oct 2026", "12th October 2026", "Oct 12, 2026"):
            self.assertTrue(so.parse_date(raw), raw)
        self.assertIsNone(so.parse_date("32/13/2026"))
        self.assertIsNone(so.parse_date("tomorrow"))
        self.assertFalse(so.day_first(["12/31/2026", "01/02/2026"]))
        self.assertTrue(so.day_first(["31/12/2026", "01/02/2026"]))

    def test_numbers_and_names(self):
        self.assertEqual(so.fmt_number(so.parse_number("£1,200.50")), "1200.5")
        self.assertEqual(so.parse_number("(300)"), -300)
        self.assertEqual(so.parse_number("45%"), 45)
        self.assertIsNone(so.parse_number("abc"))
        for raw, want in (("JOHN smith", "John Smith"), ("mary o'NEIL", "Mary O'Neil"), ("john McDonald", "John McDonald"), ("ludwig VAN BEETHOVEN", "Ludwig van Beethoven"),
                          ("MARY-ANN de SILVA", "Mary-Ann de Silva"), ("Anne-Marie DiCaprio", "Anne-Marie DiCaprio"), ("  ali   khan ", "Ali Khan")):
            self.assertEqual(so.title_name(raw), want, raw)

    def test_the_same_person_has_the_same_key_however_it_is_written(self):
        self.assertEqual(so.key_phone("07447 781753"), so.key_phone("+44 7447-781753"))
        self.assertEqual(so.key_email(" Mailto:John@X.com "), "john@x.com")
        self.assertEqual(so.key_name("Smith, John"), so.key_name("john SMITH"))
        self.assertEqual(so.key_phone("12345"), "")  # too short to identify anyone


MESSY = (["Name", "Mobile", "E-mail", "Joined", "Spend", "Notes"],
         [["  JOHN smith ", "07447 781753", "John@X.com ", "12/10/2026", "£1,200", ""],
          ["john smith", "+44 7447 781753", "", "2026-10-12", "", "called twice"],
          ["Mary O'NEIL", "not a number", "mary@x", "13.10.2026", "300", ""],
          ["", "", "", "", "", ""],
          ["Sam Lee", "07700 900222", "sam@x.com", "01/02/2026", "50", ""]])


class ProfileTests(unittest.TestCase):
    def test_a_messy_sheet_is_described_and_the_fixes_are_suggested(self):
        p = so.profile(*MESSY, code="44")
        by = {c["column"]: c for c in p["columns"]}
        self.assertEqual((by["Mobile"]["type"], by["E-mail"]["type"], by["Joined"]["type"], by["Spend"]["type"]), ("phone", "email", "date", "number"))
        self.assertTrue(any("cannot be made valid" in x for x in by["Mobile"]["problems"]))
        self.assertTrue(any("not valid email" in x for x in by["E-mail"]["problems"]))
        self.assertTrue(any("not in YYYY-MM-DD" in x for x in by["Joined"]["problems"]))
        self.assertEqual(p["duplicates"]["people_appearing_more_than_once"], 1)
        self.assertEqual(p["duplicates"]["extra_rows"], 1)
        self.assertEqual(p["people_are_matched_by"], ["Mobile", "E-mail"])
        ops = {(s["op"], s.get("column")) for s in p["suggested_operations"]}
        for want in (("trim", None), ("phone", "Mobile"), ("email", "E-mail"), ("date", "Joined"), ("number", "Spend"), ("case", "Name"), ("remove_empty_rows", None), ("dedupe", None)):
            self.assertIn(want, ops)

    def test_a_clean_sheet_has_nothing_to_suggest(self):
        p = so.profile(["Name", "Phone"], [["Ann Lee", "+447447781753"], ["Bo Ray", "+447700900111"]], "44")
        self.assertEqual(p["suggested_operations"], [])
        self.assertEqual(p["duplicates"]["extra_rows"], 0)

    def test_a_number_shared_by_many_rows_is_not_one_person(self):
        rows = [[f"Person {i}", "020 7946 0000" if i < 15 else f"07700 9{i:05d}"] for i in range(30)]
        p = so.profile(["Name", "Phone"], rows, "44")
        self.assertEqual(p["duplicates"]["extra_rows"], 0)
        self.assertEqual(p["duplicates"]["shared_values_not_treated_as_one_person"][0]["rows"], 15)


class CleanTests(unittest.TestCase):
    def clean(self, ops, table=MESSY, code="44"):
        c = so.Cleaner(*table, code=code).run(ops)
        return c.result()

    def test_each_cell_fix_is_counted_with_examples_and_what_cannot_be_fixed_is_flagged_not_guessed(self):
        cols, rows, rep = self.clean([{"op": "remove_empty_rows"}, {"op": "trim"}, {"op": "phone"}, {"op": "email"}, {"op": "date"}, {"op": "number"}, {"op": "case"}])
        self.assertEqual(cols[-1], "Needs checking")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][:5], ["John Smith", "+447447781753", "john@x.com", "2026-10-12", "1200"])
        self.assertEqual(rows[2][1], "not a number")  # left as it was
        self.assertIn("Mobile: it has letters in it", rows[2][-1])
        self.assertIn("E-mail: not a valid email address", rows[2][-1])
        self.assertEqual(rows[3][3], "2026-02-01")
        by = {(o["op"], o["column"]): o for o in rep["operations"]}
        self.assertEqual(by[("phone", "Mobile")]["changed"], 3)
        self.assertEqual(by[("phone", "Mobile")]["flagged_for_checking"], 1)
        self.assertEqual(by[("phone", "Mobile")]["examples"][0], ["07447 781753", "+447447781753"])
        self.assertEqual(rep["rows_flagged_for_checking"], 1)

    def test_the_flag_column_can_be_left_out(self):
        c = so.Cleaner(*MESSY, code="44", flag_column="").run([{"op": "phone"}])
        cols, rows, rep = c.result()
        self.assertNotIn("Needs checking", cols)
        self.assertEqual(rep["flag_column"], "")

    def test_an_op_without_a_column_applies_to_every_column_it_fits(self):
        table = (["Home", "Work", "Name"], [["07447 781753", "07700 900111", "x"]])
        cols, rows, rep = self.clean([{"op": "phone"}], table)
        self.assertEqual(rows[0][:2], ["+447447781753", "+447700900111"])

    def test_dedupe_finds_a_person_by_phone_or_email_and_chains_them(self):
        table = (["Name", "Phone", "Email"], [["Ann", "07447 781753", ""], ["Ann L", "", "ann@x.com"], ["Ann Lee", "+447447781753", "ann@x.com"], ["Bo", "07700 900111", "bo@x.com"]])
        cols, rows, rep = self.clean([{"op": "dedupe"}], table)
        self.assertEqual(len(rows), 2)  # rows 1-3 are one person: 1 and 3 share a phone, 2 and 3 an email
        self.assertEqual(rows[0][0], "Ann Lee")  # the fullest row kept, and nothing lost
        self.assertEqual(rows[0], ["Ann Lee", "+447447781753", "ann@x.com"])
        info = rep["operations"][-1]
        self.assertEqual(info["rows_removed"], 2)
        self.assertEqual((info["examples"][0]["kept_row"], info["examples"][0]["merged_rows"]), (1, [2, 3]))  # the person stays where they first appear

    def test_dedupe_keeps_first_or_last_on_request_and_fills_blanks_from_the_others(self):
        table = (["Name", "Phone", "City"], [["Ann", "07447 781753", ""], ["Ann Lee", "+447447781753", "Leeds"]])
        _, first, _ = self.clean([{"op": "dedupe", "keep": "first"}], table)
        _, last, _ = self.clean([{"op": "dedupe", "keep": "last"}], table)
        self.assertEqual((first[0][0], first[0][2]), ("Ann", "Leeds"))  # the first row, with the blank city filled in
        self.assertEqual((last[0][0], last[0][2]), ("Ann Lee", "Leeds"))

    def test_a_number_shared_by_many_rows_is_never_merged(self):
        rows = [[f"Person {i}", "020 7946 0000"] for i in range(12)] + [["Twin", "07447 781753"], ["Twin", "07447 781753"]]
        _, out, rep = self.clean([{"op": "dedupe", "keys": ["Phone"]}], (["Name", "Phone"], rows))
        self.assertEqual(len(out), 13)  # only the real duplicate went; the switchboard rows stayed
        self.assertTrue(rep["operations"][-1]["note"].count("NOT merged"))

    def test_dedupe_without_a_phone_or_email_column_says_what_to_do(self):
        with self.assertRaisesRegex(ValueError, "no phone or email column"):
            self.clean([{"op": "dedupe"}], (["Name", "City"], [["a", "b"]]))

    def test_structure_operations(self):
        table = (["Full name", "City", "Junk", "Status"], [["Mary Ann Smith", "Leeds", "", "new"], ["Bo", "York", "", "do not call"], ["Cy Lee", "", "", "new"]])
        cols, rows, rep = self.clean([{"op": "split", "column": "Full name", "into": ["First name", "Last name"]}, {"op": "remove_empty_columns"},
                                      {"op": "drop_rows", "where": [{"column": "Status", "op": "equals", "value": "do not call"}]},
                                      {"op": "fill_blank", "column": "City", "value": "Unknown"}, {"op": "map", "column": "Status", "mapping": {"new": "New lead"}},
                                      {"op": "rename", "mapping": {"City": "Town"}}, {"op": "join", "columns": ["First name", "Last name"], "into": "Label", "separator": " / "},
                                      {"op": "sort", "column": "Label", "descending": True}, {"op": "keep_columns", "columns": ["Label", "Town"]}], table)
        self.assertEqual(cols, ["Label", "Town"])
        self.assertEqual(rows, [["Mary Ann / Smith", "Leeds"], ["Cy / Lee", "Unknown"]])

    def test_dates_default_to_day_first_but_follow_the_column_when_it_is_month_first(self):
        _, rows, _ = self.clean([{"op": "date"}], (["Date"], [["12/31/2026"], ["01/02/2026"]]))
        self.assertEqual([r[0] for r in rows], ["2026-12-31", "2026-01-02"])
        _, rows, _ = self.clean([{"op": "date"}], (["Date"], [["31/12/2026"], ["01/02/2026"]]))
        self.assertEqual([r[0] for r in rows], ["2026-12-31", "2026-02-01"])

    def test_a_bad_instruction_is_refused_with_the_options(self):
        with self.assertRaisesRegex(ValueError, "Unknown operation 'shred'"):
            self.clean([{"op": "shred"}])
        with self.assertRaisesRegex(ValueError, "There is no column 'Town'"):
            self.clean([{"op": "fill_blank", "column": "Town", "value": "x"}])
        with self.assertRaisesRegex(ValueError, "replace needs"):
            self.clean([{"op": "replace"}])

    def test_duplicate_and_blank_headers_are_made_distinct(self):
        cols, _rows, _ = self.clean([{"op": "trim"}], (["Phone", "Phone", ""], [["1", "2", "3"]]))
        self.assertEqual(cols, ["Phone", "Phone (2)", "Column 3"])


class MergeTests(unittest.TestCase):
    A = (["Name", "Phone", "Email", "Course"], [["John Smith", "07447 781753", "john@x.com", "Business"], ["Mary Jones", "07700 900111", "", "Law"], ["Sam Lee", "07700 900222", "sam@x.com", ""]])
    B = (["Student", "Mobile", "E-mail", "Paid", "Course"], [["john smith", "+447447781753", "", "£500", "Business Mgmt"], ["Zed", "07700 900333", "zed@x.com", "£100", ""], ["Sam Lee", "", "sam@x.com", "£50", "Data"]])

    def test_two_lists_of_leads_are_stacked_and_the_same_people_merged(self):
        cols, rows, rep = so.merge(*self.A, *self.B, lname="Leads", rname="Webinar")
        self.assertEqual(rep["mode"], "stack")
        self.assertEqual(cols, ["Name", "Phone", "Email", "Course", "Paid", "Source"])
        self.assertEqual([r[0] for r in rows], ["John Smith", "Mary Jones", "Sam Lee", "Zed"])
        self.assertEqual([r[5] for r in rows], ["Leads + Webinar", "Leads", "Leads + Webinar", "Webinar"])
        self.assertEqual(rows[2][3:5], ["Data", "£50"])  # Sam's blank course filled from the other sheet, found by email although the phone is blank there
        self.assertEqual(rep["conflicts"]["count"], 1)  # Business vs Business Mgmt
        self.assertEqual(rep["conflicts"]["examples"][0]["kept"], "Business")
        self.assertEqual(rep["rows"], {"Leads": 3, "Webinar": 3, "result": 4})
        self.assertEqual(rep["people_in_both_sheets_merged"], 2)
        self.assertIn("Phone = Mobile (phone)", rep["matched_on"])

    def test_prefer_right_lets_the_second_sheet_win_a_disagreement(self):
        _, rows, rep = so.merge(*self.A, *self.B, conflict="prefer_right")
        self.assertEqual(rows[0][3], "Business Mgmt")
        self.assertIn("second sheet", rep["conflicts"]["rule"])

    def test_a_join_adds_the_other_sheets_columns_to_the_people_in_the_first(self):
        payments = (["Mobile", "Paid"], [["+447447781753", "500"], ["07700 900222", "50"], ["07999 000000", "9"]])
        cols, rows, rep = so.merge(*self.A, *payments, lname="Leads", rname="Payments", mode="join", how="left")
        self.assertEqual(cols, ["Name", "Phone", "Email", "Course", "Paid", "Source"])
        self.assertEqual([r[4] for r in rows], ["500", "", "50"])
        self.assertEqual((rep["matched"], rep["only_left"], rep["only_right"]), (2, 1, 0))
        _, rows, rep = so.merge(*self.A, *payments, lname="Leads", rname="Payments", mode="join", how="outer")
        self.assertEqual((len(rows), rep["only_right"]), (4, 1))
        self.assertEqual(rows[-1][-1], "Payments only")
        _, rows, _ = so.merge(*self.A, *payments, mode="join", how="inner")
        self.assertEqual(len(rows), 2)

    def test_auto_mode_joins_when_the_sheets_share_only_a_key(self):
        payments = (["Mobile", "Paid"], [["+447447781753", "500"]])
        self.assertEqual(so.merge(*self.A, *payments)[2]["mode"], "join")

    def test_the_keys_can_be_named_and_columns_mapped_by_hand(self):
        left = (["Who", "Ref", "Town"], [["Ann", "A1", "Leeds"]])
        right = (["Name", "Code", "City"], [["Ann Lee", "a1", "York"]])
        with self.assertRaisesRegex(ValueError, "cannot tell which columns identify the same person.*Who, Ref, Town.*Name, Code, City"):
            so.merge(*left, *right, mode="join")
        cols, rows, rep = so.merge(*left, *right, mode="join", on=[{"left": "Ref", "right": "Code"}], columns_map={"City": "Town"})
        self.assertEqual(rows[0][:3], ["Ann", "A1", "Leeds"])  # the first sheet's value wins
        self.assertEqual(rep["conflicts"]["count"], 1)
        self.assertIn("Name", cols)  # a column only the second sheet has is kept

    def test_matching_by_name_alone_comes_with_a_warning(self):
        left = (["Name", "Course"], [["John Smith", "Law"]])
        right = (["Name", "Paid"], [["smith, john", "5"]])
        _, rows, rep = so.merge(*left, *right, mode="join")
        self.assertEqual(rows[0][:3], ["John Smith", "Law", "5"])
        self.assertIn("name only", rep["warning"])

    def test_a_result_that_is_too_big_is_refused_not_cut(self):
        big = (["Name", "Phone"], [[f"P{i}", f"07700 9{i:05d}"] for i in range(30000)])
        big2 = (["Name", "Phone"], [[f"Q{i}", f"07800 9{i:05d}"] for i in range(30000)])
        with self.assertRaisesRegex(ValueError, "at most 50000"):
            so.merge(*big, *big2)


class QueryTests(unittest.TestCase):
    T = (["Name", "Phone", "Status", "Spend", "Joined"], [["Ann", "07447 781753", "new", "100", "2026-01-05"], ["Bo", "", "won", "£2,500", "2026-03-01"], ["Cy", "07700 900111", "new", "40", "2026-02-10"],
                                                          ["Di", "07700 900222", "lost", "", "2026-02-20"]])

    def select(self, where):
        types = so.col_types(*self.T)
        return so.select_rows(self.T[0], self.T[1], where, types)

    def test_conditions_compare_numbers_dates_and_phones_the_way_people_mean_them(self):
        self.assertEqual(self.select([{"column": "Spend", "op": "gt", "value": "99"}]), [0, 1])  # £2,500 is a number
        self.assertEqual(self.select([{"column": "Joined", "op": "lt", "value": "2026-02-15"}]), [0, 2])
        self.assertEqual(self.select([{"column": "Phone", "op": "equals", "value": "+44 7447 781753"}]), [0])
        self.assertEqual(self.select([{"column": "Phone", "op": "empty"}]), [1])
        self.assertEqual(self.select([{"column": "Status", "op": "in", "value": ["won", "LOST"]}]), [1, 3])
        self.assertEqual(self.select([{"column": "Status", "op": "equals", "value": "new"}, {"column": "Phone", "op": "not_empty"}]), [0, 2])
        self.assertEqual(self.select([{"any": [{"column": "Status", "op": "equals", "value": "won"}, {"column": "Spend", "op": "empty"}]}]), [1, 3])
        self.assertEqual(self.select([{"not": {"column": "Status", "op": "equals", "value": "new"}}]), [1, 3])
        self.assertEqual(self.select(None), [0, 1, 2, 3])
        with self.assertRaisesRegex(ValueError, "Unknown condition"):
            self.select([{"column": "Status", "op": "sounds_like", "value": "x"}])

    def test_groups_and_totals(self):
        types = so.col_types(*self.T)
        groups, total = so.aggregate(*self.T, list(range(4)), "Status", [{"op": "sum", "column": "Spend"}], types)
        self.assertEqual(total, 3)
        self.assertEqual(groups[0], {"value": "new", "count": 2, "sum of Spend": "140"})
        everything, _ = so.aggregate(*self.T, list(range(4)), None, [{"op": "max", "column": "Spend"}], types)
        self.assertEqual(everything[0]["max of Spend"], "2500")


class BigSheetTests(unittest.TestCase):
    def test_fifty_thousand_rows_are_cleaned_deduplicated_and_merged_in_seconds(self):
        n = 50_000
        cols = ["Name", "Phone", "Email", "Joined", "Spend"]
        rows = [[f"  PERSON {i % 45000} ", f"0{7700000000 + (i % 45000)}", f"P{i % 45000}@X.com" if i % 3 else "", f"{1 + i % 28}/{1 + i % 12}/2026", f"£{i}"] for i in range(n)]
        t = time.monotonic()
        prof = so.profile(cols, rows, "44")
        t_profile = time.monotonic() - t
        self.assertEqual(prof["duplicates"]["extra_rows"], 5000)
        t = time.monotonic()
        c = so.Cleaner(cols, rows, "44").run([{"op": "trim"}, {"op": "phone"}, {"op": "email"}, {"op": "date"}, {"op": "number"}, {"op": "case"}, {"op": "dedupe"}])
        out_cols, out_rows, rep = c.result()
        t_clean = time.monotonic() - t
        self.assertEqual(len(out_rows), 45000)
        self.assertEqual(out_rows[0][0], "Person 0")
        other = (["Mobile", "Plan"], [[f"+44{7700000000 + i}", "gold"] for i in range(0, 45000, 2)])
        t = time.monotonic()
        m_cols, m_rows, m_rep = so.merge(out_cols, out_rows, *other, mode="join", how="left")
        t_merge = time.monotonic() - t
        self.assertEqual(m_rep["matched"], 22500)
        self.assertLess(t_profile, 12)
        self.assertLess(t_clean, 12)
        self.assertLess(t_merge, 6)


if __name__ == "__main__":
    unittest.main()

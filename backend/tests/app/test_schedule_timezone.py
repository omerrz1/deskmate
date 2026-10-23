"""Calling hours, callbacks and meeting times mean the company's local time, not the server's clock (a server usually runs on UTC)."""
import datetime as dt
import json
import unittest

from app import db, schedule, voices
from .test_call_task_and_report import CallReportBase

DAY = dt.date(2026, 10, 23)   # a Friday; London is on summer time (UTC+1) until 25 October


def utc(h, m=0, day=DAY):
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=dt.timezone.utc).timestamp()


class CompanyTimeZoneTests(CallReportBase):
    WINDOW = {"start": "10:00", "end": "18:00", "days": [0, 1, 2, 3, 4]}

    async def test_calling_hours_are_the_companys_hours_not_the_servers(self):
        with db.use(self.ws):
            voices.set_setting("timezone", "Europe/London")
            self.assertTrue(schedule.in_window(self.WINDOW, utc(16, 30)))     # 17:30 in London
            self.assertFalse(schedule.in_window(self.WINDOW, utc(17, 30)))    # 18:30 in London: after hours, although it is only 17:30 on the server
            self.assertFalse(schedule.in_window(self.WINDOW, utc(8, 30)))     # 09:30 in London: before hours, although 08:30 UTC is "inside" 09:00–20:00 UTC thinking
            self.assertTrue(schedule.in_window(self.WINDOW, utc(9, 0)))       # 10:00 in London

    async def test_the_next_opening_is_ten_oclock_where_the_company_is(self):
        with db.use(self.ws):
            voices.set_setting("timezone", "Europe/London")
            nxt = schedule.next_open(self.WINDOW, utc(17, 30))                # Friday evening in London: the next opening is Monday 10:00
            at = schedule.local(nxt)
            self.assertEqual((at.weekday(), at.hour, at.minute), (0, 10, 0))
            self.assertEqual(schedule.describe(nxt), "Mon 26 Oct, 10:00")     # by then London is on winter time, so that is 10:00 UTC too

    async def test_a_time_the_admin_names_is_read_in_the_companys_time_zone(self):
        with db.use(self.ws):
            voices.set_setting("timezone", "Europe/London")
            self.assertEqual(schedule.parse_when("2026-10-23T15:00:00"), utc(14, 0))     # 15:00 in London is 14:00 UTC
            self.assertEqual(schedule.describe(utc(14, 0)), "Fri 23 Oct, 15:00")
            voices.set_setting("timezone", "America/New_York")
            self.assertEqual(schedule.parse_when("2026-10-23T15:00:00"), utc(19, 0))

    async def test_without_a_time_zone_it_behaves_as_before_and_a_wrong_name_is_ignored(self):
        with db.use(self.ws):
            voices.set_setting("timezone", "")
            self.assertIsNone(schedule.tz())
            ts = schedule.parse_when("2026-10-23T15:00:00")
            self.assertEqual(dt.datetime.fromtimestamp(ts).hour, 15)                      # the server's own clock
            voices.set_setting("timezone", "Not/AZone")
            self.assertIsNone(schedule.tz())
            self.assertTrue(schedule.in_window(None, utc(3)))                             # no window at all: always open
            self.assertTrue(schedule.in_window(schedule.DEFAULT_WINDOW, ts))

    async def test_a_campaign_is_clamped_into_its_hours_in_the_companys_zone(self):
        with db.use(self.ws):
            voices.set_setting("timezone", "Europe/London")
            camp = {"call_window": json.dumps(self.WINDOW)}
            self.assertEqual(schedule.clamp(camp, utc(16, 30)), utc(16, 30))             # open: unchanged
            later = schedule.clamp(camp, utc(17, 30))                                    # closed: pushed to the next opening
            self.assertGreater(later, utc(17, 30))
            self.assertEqual(schedule.local(later).hour, 10)


if __name__ == "__main__":
    unittest.main()

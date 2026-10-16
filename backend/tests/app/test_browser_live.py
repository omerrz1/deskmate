"""The live view of an employee's browser window. These drive a real headless Chrome (pages are data: URLs, so nothing touches the network)."""
import asyncio
import json
import unittest
from unittest import mock
from urllib.parse import quote

from app import agents, auth, browser_tools, db, keys, main

KEY = lambda aid: (db.current(), aid)  # noqa: E731


def page_url(title, body="", color="white"):
    return "data:text/html," + quote(f"<title>{title}</title><body style='margin:0;background:{color}'><h1>{body or title}</h1></body>")


async def open_tab(page, title, color="white"):
    """What a link with target=_blank does. (Chrome does not let a page open a data: address in a new tab, so the new tab's content is written into it.)"""
    html = f"<title>{title}</title><body style='margin:0;background:{color}'><h1>{title}</h1></body>"
    await page.evaluate(f"(() => {{ const w = window.open('about:blank'); w.document.write({json.dumps(html)}); w.document.close() }})()")


class Viewer:
    """Stands in for the HTTP request: the stream stops when `leave()` is called."""

    def __init__(self):
        self.gone = False

    async def is_disconnected(self):
        return self.gone

    def leave(self):
        self.gone = True


async def next_frame(gen, seconds=8):
    """The next picture from the stream, as bytes."""
    part = await asyncio.wait_for(gen.__anext__(), seconds)
    head, _, rest = part.partition(b"\r\n\r\n")
    assert head.startswith(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "), head[:80]
    n = int(head.split(b"Content-Length: ")[1].split(b"\r\n")[0])
    body = rest[:n]
    assert len(body) == n
    return body


class LiveBase(unittest.IsolatedAsyncioTestCase):
    ava = None

    @classmethod
    def setUpClass(cls):
        cls.ava = agents.create("Ava", "female", "Support", "", "", bio="")

    async def asyncSetUp(self):
        try:
            self.s = await browser_tools._session(self.ava)
        except RuntimeError as e:
            self.skipTest(f"no browser on this machine: {e}")

    async def asyncTearDown(self):
        await browser_tools.shutdown()  # also resets the shared Playwright handles, which belong to this test's event loop


class StateTests(LiveBase):
    async def test_nothing_open_is_reported_plainly_and_looking_does_not_open_a_window(self):
        await browser_tools.shutdown()
        self.assertEqual(await browser_tools.live_state(self.ava), {"open": False, "mode": browser_tools.mode()})
        self.assertFalse(browser_tools.is_open(self.ava))
        self.assertEqual(browser_tools._sessions, {})

    async def test_the_state_is_the_real_page_and_its_real_tabs(self):
        await self.s["page"].goto(page_url("First page"))
        st = await browser_tools.live_state(self.ava)
        self.assertTrue(st["open"])
        self.assertEqual(st["url"], self.s["page"].url)
        self.assertEqual([(t["title"], t["active"]) for t in st["tabs"]], [("First page", True)])
        self.assertFalse(st["loading"])
        self.assertLessEqual(st["closes_in"], browser_tools.IDLE_SECONDS)
        # a link that opens a new tab: the employee follows it, and so does the window's tab strip
        await open_tab(self.s["page"], "Second page")
        await asyncio.sleep(0.8)
        st = await browser_tools.live_state(self.ava)
        self.assertEqual(sorted(t["title"] for t in st["tabs"]), ["First page", "Second page"])
        self.assertEqual([t["title"] for t in st["tabs"] if t["active"]], ["Second page"])

    async def test_looking_does_not_keep_an_idle_window_alive(self):
        before = self.s["last"]
        await browser_tools.live_state(self.ava)
        await next_frame(browser_tools.mjpeg(self.ava, Viewer()))
        self.assertEqual(self.s["last"], before)


class StreamTests(LiveBase):
    async def test_frames_are_pictures_of_the_page_and_follow_it_as_it_changes(self):
        page = self.s["page"]
        await page.goto(page_url("Live", color="white"))
        gen = browser_tools.mjpeg(self.ava, Viewer())
        first = await next_frame(gen)
        self.assertEqual(first[:2], b"\xff\xd8")  # a JPEG
        await page.evaluate("document.body.style.background = 'rgb(200, 0, 0)'")
        changed = None
        for _ in range(20):
            frame = await next_frame(gen)
            if frame != first:
                changed = frame
                break
        self.assertIsNotNone(changed, "the picture never changed after the page did")
        await gen.aclose()

    async def test_everyone_watching_shares_one_screencast_and_it_stops_with_the_last_viewer(self):
        a, b = Viewer(), Viewer()
        ga, gb = browser_tools.mjpeg(self.ava, a), browser_tools.mjpeg(self.ava, b)
        await next_frame(ga)
        await next_frame(gb)
        live = self.s["live"]
        self.assertEqual(len(live["subs"]), 2)
        cdp = live["cdp"]
        self.assertIsNotNone(cdp)
        await ga.aclose()
        self.assertIs(live["cdp"], cdp)  # one viewer is still watching
        await gb.aclose()
        self.assertIsNone(live["cdp"])
        self.assertEqual(len(live["subs"]), 0)

    async def test_the_stream_follows_the_employee_to_a_new_tab(self):
        page = self.s["page"]
        await page.goto(page_url("Old", color="rgb(0, 120, 0)"))
        gen = browser_tools.mjpeg(self.ava, Viewer())
        old = await next_frame(gen)
        await open_tab(page, "New", "rgb(0, 0, 160)")
        for _ in range(30):
            frame = await next_frame(gen, 10)
            if frame != old and self.s["live"]["page"] is self.s["page"] and self.s["page"] is not page:
                break
        self.assertIsNot(self.s["page"], page)
        self.assertIs(self.s["live"]["page"], self.s["page"])
        await gen.aclose()

    async def test_the_stream_ends_when_the_window_closes(self):
        gen = browser_tools.mjpeg(self.ava, Viewer())
        await next_frame(gen)
        await browser_tools._drop(KEY(self.ava))
        with self.assertRaises(StopAsyncIteration):
            for _ in range(5):
                await asyncio.wait_for(gen.__anext__(), 5)

    async def test_a_still_page_keeps_the_connection_alive(self):
        gen = browser_tools.mjpeg(self.ava, Viewer())
        await next_frame(gen)
        with mock.patch.object(browser_tools, "LIVE_FPS", 1000):
            self.assertIsNotNone(await next_frame(gen, 15))  # a page that is not changing is re-sent after a few seconds, not left silent
        await gen.aclose()

    async def test_a_viewer_who_leaves_ends_the_stream(self):
        v = Viewer()
        gen = browser_tools.mjpeg(self.ava, v)
        await next_frame(gen)
        v.leave()
        with self.assertRaises(StopAsyncIteration):
            for _ in range(5):
                await asyncio.wait_for(gen.__anext__(), 8)
        self.assertIsNone(self.s["live"]["cdp"])


async def call(app, path, cookie=None, until=None, seconds=10):
    """Run one request through the real application (middleware and all) and return (status, headers, body chunks).
    `until(chunks)` ends a request that would otherwise never finish, the way a viewer closing the page does."""
    sent, done = [], asyncio.Event()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"host", b"test")] + ([(b"cookie", cookie.encode())] if cookie else []), "client": ("127.0.0.1", 5000), "server": ("test", 80), "scheme": "http", "state": {}}

    async def receive():
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)
        if until and until([m.get("body", b"") for m in sent if m["type"] == "http.response.body"]):
            done.set()
    await asyncio.wait_for(app(scope, receive, send), seconds)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], dict(start["headers"]), [m.get("body", b"") for m in sent if m["type"] == "http.response.body"]


class RouteTests(LiveBase):
    SESSION = {"ws": "main", "id": "test-session", "csrf": "x", "must_change": False}

    def signed_in(self):
        return mock.patch.multiple(auth, session_from_cookie=lambda c: dict(self.SESSION) if c == "signed-in" else None), mock.patch.object(keys, "complete", lambda: True)

    async def test_the_routes_need_a_signed_in_user(self):
        for path in (f"/api/agents/{self.ava}/browser", f"/api/agents/{self.ava}/browser/live"):
            status, _, _ = await call(main.app, path)
            self.assertEqual(status, 401, path)

    async def test_state_route_returns_the_window_state(self):
        await self.s["page"].goto(page_url("Via HTTP"))
        a, b = self.signed_in()
        with a, b:
            status, _, body = await call(main.app, f"/api/agents/{self.ava}/browser", cookie="signed-in")
        self.assertEqual(status, 200)
        st = json.loads(b"".join(body))
        self.assertEqual((st["open"], st["tabs"][0]["title"]), (True, "Via HTTP"))

    async def test_unknown_employee_and_no_window_are_not_found(self):
        a, b = self.signed_in()
        with a, b:
            self.assertEqual((await call(main.app, "/api/agents/99999/browser", cookie="signed-in"))[0], 404)
            self.assertEqual((await call(main.app, "/api/agents/99999/browser/live", cookie="signed-in"))[0], 404)
            await browser_tools.shutdown()
            self.assertEqual((await call(main.app, f"/api/agents/{self.ava}/browser/live", cookie="signed-in"))[0], 404)

    async def test_live_route_streams_a_picture_and_stops_the_screencast_when_the_viewer_goes(self):
        a, b = self.signed_in()
        with a, b:
            status, headers, chunks = await call(main.app, f"/api/agents/{self.ava}/browser/live", cookie="signed-in",
                                                 until=lambda c: any(x.startswith(b"--frame") for x in c), seconds=20)
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"content-type"], b"multipart/x-mixed-replace; boundary=frame")
        self.assertIn(b"no-store", headers[b"cache-control"])
        jpeg = next(c for c in chunks if c.startswith(b"--frame")).partition(b"\r\n\r\n")[2]
        self.assertEqual(jpeg[:2], b"\xff\xd8")
        await asyncio.sleep(0.5)
        self.assertIsNone(self.s["live"]["cdp"])  # the viewer is gone, so nothing is being captured any more


if __name__ == "__main__":
    unittest.main()

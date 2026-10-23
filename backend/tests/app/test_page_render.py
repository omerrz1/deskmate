"""browser_tools.render: a page as a person sees it. A fake browser stands in for Chrome, so the suite never opens one or touches the network."""
import unittest
from unittest import mock

from app import browser_tools


class FakePage:
    def __init__(self, texts, title="Courses", url="https://shop.example/c"):
        self.texts, self.t, self.url = list(texts), title, url
        self.waited = []

    def set_default_timeout(self, _ms):
        pass

    async def goto(self, url, **_kw):
        self.url = url

    async def wait_for_load_state(self, *_a, **_kw):
        pass

    async def wait_for_timeout(self, ms):
        self.waited.append(ms)

    async def title(self):
        return self.t

    async def evaluate(self, script):
        if "innerText" in script:
            return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        return ["https://shop.example/fees", "https://shop.example/apply"]


class FakeContext:
    def __init__(self, page):
        self.page, self.closed, self.routed = page, False, False

    async def route(self, *_a):
        self.routed = True

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page):
        self.ctx = FakeContext(page)

    async def new_context(self, **_kw):
        return self.ctx


class RenderTests(unittest.IsolatedAsyncioTestCase):
    def browser(self, *texts, title="Courses"):
        page = FakePage(texts, title)
        return page, FakeBrowser(page)

    async def render(self, browser, **patches):
        with mock.patch.object(browser_tools, "RENDER_ENABLED", True), mock.patch.object(browser_tools, "mode", return_value="on"), \
             mock.patch.object(browser_tools, "_ensure_browser", new=mock.AsyncMock(return_value=browser)):
            return await browser_tools.render("https://shop.example/c")

    async def test_it_returns_what_the_scripts_drew_with_the_links_and_closes_the_window(self):
        page, browser = self.browser("Level 5 Diploma\n\n\n\nPrice £1,250")
        got = await self.render(browser)
        self.assertEqual(got["text"], "Level 5 Diploma\n\nPrice £1,250")
        self.assertEqual(got["title"], "Courses")
        self.assertIn("https://shop.example/fees", got["links"])
        self.assertTrue(browser.ctx.closed)
        self.assertTrue(browser.ctx.routed)  # every request the page makes is checked for public addresses

    async def test_a_bot_check_gets_a_few_seconds_to_clear_by_itself(self):
        page, browser = self.browser("Checking your browser before accessing the site.", "Fees: £1,250 for the diploma. " * 10, title="Fees")
        got = await self.render(browser)
        self.assertEqual(page.waited, [5000])
        self.assertIn("£1,250", got["text"])

    async def test_a_bot_check_that_never_clears_is_not_returned_as_the_page(self):
        page, browser = self.browser("Please complete the security check to continue. Checking your browser.", title="Robot Challenge Screen")
        self.assertIsNone(await self.render(browser))
        self.assertTrue(browser.ctx.closed)

    async def test_it_does_nothing_when_switched_off(self):
        page, browser = self.browser("anything")
        with mock.patch.object(browser_tools, "RENDER_ENABLED", False), mock.patch.object(browser_tools, "_ensure_browser", new=mock.AsyncMock(side_effect=AssertionError("must not start Chrome"))):
            self.assertIsNone(await browser_tools.render("https://shop.example/c"))
        with mock.patch.object(browser_tools, "RENDER_ENABLED", True), mock.patch.object(browser_tools, "mode", return_value="off"), \
             mock.patch.object(browser_tools, "_ensure_browser", new=mock.AsyncMock(side_effect=AssertionError("must not start Chrome"))):
            self.assertIsNone(await browser_tools.render("https://shop.example/c"))

    async def test_it_never_raises_when_chrome_is_missing(self):
        with mock.patch.object(browser_tools, "RENDER_ENABLED", True), mock.patch.object(browser_tools, "mode", return_value="on"), \
             mock.patch.object(browser_tools, "_ensure_browser", new=mock.AsyncMock(side_effect=RuntimeError("no chrome installed"))):
            self.assertIsNone(await browser_tools.render("https://shop.example/c"))

    async def test_the_test_suite_itself_never_renders(self):
        import os
        self.assertEqual(os.environ.get("RENDER_PAGES"), "false")
        self.assertFalse(browser_tools.RENDER_ENABLED)
        self.assertIsNone(await browser_tools.render("https://shop.example/c"))

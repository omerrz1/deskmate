import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import webdemo


class WebsiteReadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_crawl_follows_deep_links_and_company_subdomains(self):
        root = "https://example.co.uk"
        body = "<html><title>Example</title><body><p>" + ("A useful company detail. " * 30) + "</p>"
        pages = {
            root: body + '<a href="/about">About</a><a href="https://courses.example.co.uk/catalog">Courses</a></body></html>',
            root + "/about": body + '<a href="/faq">FAQ</a></body></html>',
            root + "/faq": body + "</body></html>",
            "https://courses.example.co.uk/catalog": body + "</body></html>",
        }
        async def get(_client, url, base_domain=None):
            return url, pages[url]
        with patch.object(webdemo, "_get", side_effect=get), patch.object(webdemo, "_sitemap_urls", new=AsyncMock(return_value=[])):
            result = await webdemo.crawl(root, max_pages=10)
        self.assertEqual({p["url"] for p in result}, set(pages))

    async def test_two_addresses_that_redirect_to_one_page_are_one_page(self):
        root = "https://example.co.uk"
        body = "<html><title>Example</title><body><p>" + ("A useful company detail. " * 30) + "</p>"
        pages = {root: body + '<a href="/course?id=3">Course</a><a href="/enrol?id=3">Enrol</a></body></html>',
                 root + "/enrol?id=3": body + "Price: 700 pounds</body></html>"}

        async def get(_client, url, base_domain=None):
            return (root + "/enrol?id=3", pages[root + "/enrol?id=3"]) if url.endswith("id=3") else (url, pages[url])
        with patch.object(webdemo, "_get", side_effect=get), patch.object(webdemo, "_sitemap_urls", new=AsyncMock(return_value=[])), \
             patch.object(webdemo, "_render_pass", new=AsyncMock(side_effect=lambda p: p)):
            result = await webdemo.crawl(root, max_pages=10)
        self.assertEqual(sorted(p["url"] for p in result), [root, root + "/enrol?id=3"])

    def test_company_domain_scope_handles_uk_suffix_and_blocks_lookalikes(self):
        base = webdemo._base_domain("www.example.co.uk")
        self.assertEqual(base, "example.co.uk")
        self.assertTrue(webdemo._in_scope("https://courses.example.co.uk/a", base))
        self.assertFalse(webdemo._in_scope("https://example.co.uk.attacker.test/a", base))

    async def test_disconnection_uses_reader(self):
        page = {"url": "https://example.com", "title": "Example", "meta": {},
                "text": "Company information. " * 30, "links": []}
        with patch.object(webdemo, "_get", side_effect=httpx.RemoteProtocolError("Disconnected")), \
             patch.object(webdemo, "_reader", new=AsyncMock(return_value=page)) as reader:
            self.assertEqual(await webdemo.crawl("example.com"), [page])
            reader.assert_awaited_once()

    async def test_failed_fallback_returns_friendly_error(self):
        with patch.object(webdemo, "_get", side_effect=httpx.ConnectError("Failed")), \
             patch.object(webdemo, "_reader", new=AsyncMock(side_effect=httpx.ReadTimeout("Timeout"))):
            with self.assertRaisesRegex(webdemo.Blocked, "could not be reached"):
                await webdemo.crawl("example.com")

    async def test_reader_rejects_private_address_before_request(self):
        client = AsyncMock()
        with patch.object(webdemo, "_check_host", side_effect=webdemo.Blocked("Not public")):
            with self.assertRaises(webdemo.Blocked):
                await webdemo._reader(client, "http://127.0.0.1")
        client.get.assert_not_awaited()


BOT_PAGE = "<html><title>Robot Challenge Screen</title><body>Please complete the security check to continue. Checking your browser.</body></html>"
PRICE_PAGE = "<html><title>Fees</title><body><p>" + ("Our courses are delivered online with tutor support. " * 12) + "</p><p>Level 5 Diploma: £1,250 in total.</p></body></html>"
SHELL_PAGE = "<html><title>Courses</title><body><div id='app'>Loading...</div></body></html>"


def fake_site(handler):
    """An httpx client whose server is `handler(request) -> Response`; no network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5, follow_redirects=False)


class SitesThatTurnAwayBrowsersTests(unittest.IsolatedAsyncioTestCase):
    """A site that drops requests which claim to be Chrome, or shows a bot check, must still be readable."""

    async def get(self, handler, url="https://shop.example/fees"):
        with patch.object(webdemo, "_check_host"):
            return await webdemo._get(fake_site(handler), url)

    async def test_a_refusal_of_the_browser_identity_is_retried_as_a_plain_client(self):
        seen = []

        def handler(request):
            seen.append(request.headers["user-agent"])
            if "Mozilla" in request.headers["user-agent"]:
                return httpx.Response(403, text="forbidden")
            return httpx.Response(200, text=PRICE_PAGE, headers={"content-type": "text/html"})
        final, html = await self.get(handler)
        self.assertIn("£1,250", html)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1], webdemo.PLAIN_HEADERS["User-Agent"])

    async def test_a_dropped_connection_is_retried_as_a_plain_client(self):
        def handler(request):
            if "Mozilla" in request.headers["user-agent"]:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return httpx.Response(200, text=PRICE_PAGE, headers={"content-type": "text/html"})
        _, html = await self.get(handler)
        self.assertIn("£1,250", html)

    async def test_a_bot_check_page_is_retried_and_never_returned_as_content_when_a_plain_client_gets_through(self):
        def handler(request):
            body = BOT_PAGE if "Mozilla" in request.headers["user-agent"] else PRICE_PAGE
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        _, html = await self.get(handler)
        self.assertIn("£1,250", html)
        self.assertNotIn("Robot Challenge", html)

    async def test_a_page_that_is_missing_stays_missing_after_one_try(self):
        seen = []

        def handler(request):
            seen.append(1)
            return httpx.Response(404, text="nope")
        with self.assertRaisesRegex(webdemo.Blocked, "404"):
            await self.get(handler)
        self.assertEqual(len(seen), 1)  # a 404 is not a reason to try another identity

    async def test_when_every_identity_is_refused_the_last_failure_is_raised(self):
        with self.assertRaises(webdemo.Blocked):
            await self.get(lambda request: httpx.Response(403, text="no"))

    def test_bot_checks_are_told_apart_from_real_pages(self):
        self.assertTrue(webdemo.looks_blocked("Please complete the security check. Checking your browser.", "Robot Challenge Screen"))
        self.assertTrue(webdemo.looks_blocked("Just a moment...", "Just a moment..."))
        self.assertFalse(webdemo.looks_blocked("Our fees are £1,250. " * 200, "Fees"))  # a long page that merely mentions a captcha is content
        self.assertFalse(webdemo.looks_blocked("Welcome to the college. Fees and courses below.", "Home"))

    def test_money_and_price_questions_are_recognised(self):
        for text in ("£1,250 in total", "$99", "€ 45", "1250 GBP", "4,200 pounds"):
            self.assertTrue(webdemo.MONEY.search(text), text)
        for text in ("year 2024", "call 0207 123 4567", "Level 5 Diploma"):
            self.assertFalse(webdemo.MONEY.search(text), text)
        for q in ("what are the course prices?", "how much is it", "tuition fees", "can I pay in instalments"):
            self.assertTrue(webdemo.PRICE_Q.search(q), q)
        self.assertFalse(webdemo.PRICE_Q.search("when do classes start"))


class ReadOneLadderTests(unittest.IsolatedAsyncioTestCase):
    """read_one: plain read first; a real browser when the page is empty, blocked or (for a price question) shows no price; the reader service last."""

    def patch_static(self, html=None, error=None):
        async def get(_client, url, base_domain=None):
            if error:
                raise error
            return url, html
        return patch.object(webdemo, "_get", side_effect=get)

    async def test_a_normal_page_is_returned_without_opening_a_browser(self):
        with self.patch_static(PRICE_PAGE), patch.object(webdemo, "render_page", new=AsyncMock(return_value=None)) as render:
            page = await webdemo.read_one("https://shop.example/fees")
        self.assertIn("£1,250", page["text"])
        render.assert_not_awaited()

    async def test_a_page_with_no_text_is_opened_in_a_browser(self):
        drawn = {"url": "https://shop.example/c", "title": "Courses", "text": "Level 5 Diploma. Price £1,250. " * 10, "links": []}
        with self.patch_static(SHELL_PAGE), patch.object(webdemo, "render_page", new=AsyncMock(return_value=drawn)) as render:
            page = await webdemo.read_one("https://shop.example/c")
        self.assertEqual(page, drawn)
        render.assert_awaited_once()

    async def test_a_price_question_opens_a_browser_when_the_plain_text_shows_no_price(self):
        no_price = "<html><title>Course</title><body><p>" + ("A fully online diploma with tutor support. " * 12) + "</p></body></html>"
        drawn = {"url": "https://shop.example/c", "title": "Course", "text": "A fully online diploma. Price: £1,250 total. " * 6, "links": []}
        with self.patch_static(no_price), patch.object(webdemo, "render_page", new=AsyncMock(return_value=drawn)) as render:
            plain = await webdemo.read_one("https://shop.example/c")
            render.assert_not_awaited()
            self.assertNotIn("£", plain["text"])
            priced = await webdemo.read_one("https://shop.example/c", want_money=True)
        self.assertIn("£1,250", priced["text"])
        render.assert_awaited_once()

    async def test_the_plain_page_is_kept_when_the_browser_finds_nothing_better(self):
        no_price = "<html><title>Course</title><body><p>" + ("A fully online diploma with tutor support. " * 12) + "</p></body></html>"
        with self.patch_static(no_price), patch.object(webdemo, "render_page", new=AsyncMock(return_value=None)):
            page = await webdemo.read_one("https://shop.example/c", want_money=True)
        self.assertIn("fully online diploma", page["text"])

    async def test_render_true_goes_straight_to_the_browser(self):
        drawn = {"url": "https://shop.example/c", "title": "Courses", "text": "Everything the scripts drew. " * 10, "links": []}
        with patch.object(webdemo, "_get", side_effect=AssertionError("must not fetch statically")), \
             patch.object(webdemo, "render_page", new=AsyncMock(return_value=drawn)):
            self.assertEqual(await webdemo.read_one("https://shop.example/c", render=True), drawn)

    async def test_a_blocked_site_falls_back_to_the_browser_then_the_reader(self):
        alt = {"url": "https://shop.example/c", "title": "Via reader", "text": "Reader text. " * 30, "links": []}
        with self.patch_static(error=webdemo.Blocked("The website answered with an error (403).")), \
             patch.object(webdemo, "render_page", new=AsyncMock(return_value=None)), \
             patch.object(webdemo, "_reader", new=AsyncMock(return_value=alt)):
            self.assertEqual((await webdemo.read_one("https://shop.example/c"))["title"], "Via reader")

    async def test_a_bot_check_served_with_a_200_is_never_returned_as_the_page(self):
        drawn = {"url": "https://shop.example/c", "title": "Courses", "text": "Real course list with prices £99. " * 10, "links": []}
        with self.patch_static(BOT_PAGE), patch.object(webdemo, "render_page", new=AsyncMock(return_value=drawn)):
            page = await webdemo.read_one("https://shop.example/c")
        self.assertNotIn("Robot Challenge", page["text"])
        self.assertIn("£99", page["text"])


class RenderPassTests(unittest.IsolatedAsyncioTestCase):
    """After a crawl, pages that should carry a price but show none are re-read in a real browser."""

    def page(self, path, text, title=""):
        return {"url": "https://shop.example" + path, "title": title, "text": text, "links": []}

    async def test_only_pages_that_should_carry_a_price_and_show_none_are_rendered(self):
        pages = [self.page("/", "Home " * 80),
                 self.page("/fees", "Fees info " * 40),
                 self.page("/courses/diploma", "Diploma " * 40),
                 self.page("/about", "About us " * 40),
                 self.page("/courses/paid", "Costs £50 " * 40)]
        asked = []

        async def render(url):
            asked.append(url)
            return {"url": url, "title": "", "text": "Drawn: price £1,250. " * 20, "links": []}
        with patch.object(webdemo, "render_page", side_effect=render):
            await webdemo._render_pass(pages)
        self.assertEqual(asked[0], "https://shop.example/fees")  # fee pages first
        self.assertEqual(sorted(asked), ["https://shop.example/courses/diploma", "https://shop.example/fees"])
        self.assertIn("£1,250", pages[1]["text"])
        self.assertTrue(pages[1]["rendered"])
        self.assertNotIn("£", pages[3]["text"])  # /about was left alone

    async def test_a_render_that_adds_nothing_does_not_replace_the_page(self):
        pages = [self.page("/", "Home " * 80), self.page("/fees", "Fees info " * 40)]
        before = pages[1]["text"]
        with patch.object(webdemo, "render_page", new=AsyncMock(return_value={"url": "x", "title": "", "text": "short", "links": []})):
            await webdemo._render_pass(pages)
        self.assertEqual(pages[1]["text"], before)
        self.assertNotIn("rendered", pages[1])

    async def test_a_failing_render_pass_never_loses_the_crawl(self):
        crawled = [self.page("/", "Home " * 80)]
        with patch.object(webdemo, "_crawl_static", new=AsyncMock(return_value=crawled)), \
             patch.object(webdemo, "_render_pass", new=AsyncMock(side_effect=RuntimeError("chrome crashed"))):
            self.assertEqual(await webdemo.crawl("shop.example"), crawled)

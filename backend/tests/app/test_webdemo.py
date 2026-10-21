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

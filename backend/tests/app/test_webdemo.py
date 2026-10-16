import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import webdemo


class WebsiteReadingTests(unittest.IsolatedAsyncioTestCase):
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

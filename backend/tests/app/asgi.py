"""Drive the real application (middleware, sign-in, cookies and all) in-process, the way a browser would."""
import asyncio
import itertools
import json
from http.cookies import SimpleCookie

_ips = itertools.count(1)


class Browser:
    """One person's browser: keeps their cookies, and sends the security token like the real app does."""

    def __init__(self, app):
        self.app = app
        self.cookies = {}
        self.ip = f"10.{next(_ips) // 250}.{next(_ips) % 250}.7"  # every browser has its own address, so the sign-up and sign-in limits do not trip the tests

    async def request(self, method, path, body=None, *, query="", headers=None):
        raw = json.dumps(body).encode() if body is not None else b""
        hdrs = [(b"host", b"test")] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        if body is not None:
            hdrs += [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
        if self.cookies:
            hdrs.append((b"cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()).encode()))
        if method not in ("GET", "HEAD", "OPTIONS") and "deskmates_csrf" in self.cookies:
            hdrs.append((b"x-csrf-token", self.cookies["deskmates_csrf"].encode()))
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path, "raw_path": path.encode(), "query_string": query.encode(),
                 "headers": hdrs, "client": (self.ip, 5000), "server": ("test", 80), "scheme": "http", "state": {}}
        sent, fed = [], [False]

        async def receive():
            if not fed[0]:
                fed[0] = True
                return {"type": "http.request", "body": raw, "more_body": False}
            await asyncio.sleep(3600)

        async def send(msg):
            sent.append(msg)
        await asyncio.wait_for(self.app(scope, receive, send), 20)
        start = next(m for m in sent if m["type"] == "http.response.start")
        data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        for k, v in start["headers"]:
            if k == b"set-cookie":
                c = SimpleCookie(v.decode())
                for name, morsel in c.items():
                    if morsel.value:
                        self.cookies[name] = morsel.value
                    elif morsel["max-age"] == "0":
                        self.cookies.pop(name, None)
        ctype = dict(start["headers"]).get(b"content-type", b"").decode()
        try:
            parsed = json.loads(data) if "json" in ctype and data else data
        except ValueError:
            parsed = data
        return start["status"], parsed

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, {} if body is None else body, **kw)

    def patch(self, path, body, **kw):
        return self.request("PATCH", path, body, **kw)

    def put(self, path, body, **kw):
        return self.request("PUT", path, body, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

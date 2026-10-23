"""Pure-ASGI security middleware: auth gate, CSRF + origin checks, rate limits, size limit, security headers."""
import json
import os
import time
from collections import defaultdict, deque

from . import access, auth, db, directory, keys

PUBLIC = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/signup", "/api/contact"}
NEEDS_NO_KEYS = ("/api/workspaces", "/api/auth/", "/api/setup", "/api/status", "/api/stream", "/api/health")  # reachable before the company has connected its keys
SAFE = {"GET", "HEAD", "OPTIONS"}
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()}
TRUST_PROXY = os.getenv("TRUSTED_PROXY", "false").lower() == "true"
MAX_BODY = int(os.getenv("MAX_BODY_MB", "60")) * 1024 * 1024
EXPENSIVE = ("/api/tts", "/api/chat", "/api/transcribe", "/api/knowledge", "/api/kb/", "/api/datasets", "/api/voices/search", "/api/demo/website", "/api/meetings", "/api/agents", "/api/trainings", "/api/connectors", "/api/apps")
LIMITS = {"login": (10, 60), "signup": (5, 3600), "signup_all": (int(os.getenv("SIGNUP_GLOBAL_PER_HOUR", "60")), 3600), "contact": (5, 3600), "expensive": (60, 60), "default": (900, 60), "hook": (120, 60), "chathook": (900, 60)}  # (requests, per seconds)
_hits = defaultdict(deque)


def client_ip(scope, headers):
    if TRUST_PROXY and headers.get("x-forwarded-for"):
        return headers["x-forwarded-for"].split(",")[0].strip()
    c = scope.get("client")
    return c[0] if c else "?"


def _allow(key, bucket):
    n, per = LIMITS[bucket]
    q, t = _hits[(key, bucket)], time.time()
    while q and q[0] < t - per:
        q.popleft()
    if len(q) >= n:
        return False, int(q[0] + per - t) + 1
    q.append(t)
    return True, 0


class Security:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":  # start-up and shutdown run as the main workspace
            return await self.app(scope, receive, send)
        # a request has no workspace until its session (or a webhook address) says which one: any data access before that is refused
        tok = db.set_current("")
        try:
            await self._handle(scope, receive, send)
        finally:
            db.reset_current(tok)

    async def _handle(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope["method"], scope["path"]
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        ip = client_ip(scope, headers)
        scope.setdefault("state", {})
        scope["state"]["ip"] = ip
        scope["state"]["ua"] = headers.get("user-agent", "")

        async def deny(status, message, extra=None):
            body = json.dumps({"detail": message}).encode()
            hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())] + (extra or [])
            await self._send_with_headers(send)({"type": "http.response.start", "status": status, "headers": hdrs})
            await self._send_with_headers(send)({"type": "http.response.body", "body": body})

        if path.startswith("/api/"):
            try:
                if int(headers.get("content-length", "0")) > MAX_BODY:
                    return await deny(413, "Upload too large.")
            except ValueError:
                return await deny(400, "Bad request.")
            # cross-site request protection for anything that changes state
            if method not in SAFE and not path.startswith(("/api/hooks/in/", "/api/hooks/chat/")):
                origin = headers.get("origin", "").rstrip("/")
                if origin and origin not in ALLOWED_ORIGINS:
                    return await deny(403, "Cross-site request blocked.")
            if path.startswith("/api/hooks/in/"):  # inbound webhooks: the secret in the address is the key; no cookie or origin
                parts = path[len("/api/hooks/in/"):].split("/")
                ws = parts[0] if len(parts) > 1 and (parts[0] == db.MAIN or directory.ID_RE.match(parts[0])) else db.MAIN  # /hooks/in/<workspace>/<secret>; the old address form is the main workspace
                if not directory.is_active(ws):
                    return await deny(404, "Not found.")
                db.set_current(ws)
                if not keys.complete():
                    return await deny(428, "This workspace has not connected its AI keys yet.")
                ok, wait = _allow(ip, "hook")
                if not ok:
                    return await deny(429, "Too many requests.", [(b"retry-after", str(wait).encode())])
            elif path.startswith("/api/hooks/chat/"):  # customers' messages and WhatsApp calls: the secret in the address is the key; the company is named in it
                ws = path[len("/api/hooks/chat/"):].split("/")[0]
                if not (ws == db.MAIN or directory.ID_RE.match(ws)) or not directory.is_active(ws):
                    return await deny(404, "Not found.")
                db.set_current(ws)
                if not keys.complete():
                    return await deny(428, "This workspace has not connected its AI keys yet.")
                ok, wait = _allow(ip, "chathook")
                if not ok:
                    return await deny(429, "Too many requests.", [(b"retry-after", str(wait).encode())])
            elif path == "/api/auth/signup":
                ok, wait = _allow(ip, "signup")
                if not ok:
                    return await deny(429, "Too many new accounts from this address. Try again later.", [(b"retry-after", str(wait).encode())])
                ok, wait = _allow("*", "signup_all")  # sign-up is open to everyone, so a crowd of addresses is held to a total too: each company costs this server a database and files
                if not ok:
                    return await deny(429, "We are getting a lot of sign-ups right now. Please try again in a little while.", [(b"retry-after", str(wait).encode())])
            elif path == "/api/contact":
                ok, wait = _allow(ip, "contact")
                if not ok:
                    return await deny(429, "You have sent a few messages already. Please try again later.", [(b"retry-after", str(wait).encode())])
            elif path in ("/api/auth/login", "/api/auth/setup"):
                ok, wait = _allow(ip, "login")
                if not ok:
                    return await deny(429, f"Too many attempts. Try again in {wait}s.", [(b"retry-after", str(wait).encode())])
            elif path.startswith("/api/invites/"):  # the page an invited person opens before they have signed in: the link is the key, and it names the company
                ws = path[len("/api/invites/"):].split("/")[0].split(".")[0]
                if not (ws == db.MAIN or directory.ID_RE.match(ws)) or not directory.is_active(ws):
                    return await deny(404, "This invitation is no longer valid. Ask the person who invited you for a new link.")
                db.set_current(ws)
                ok, wait = _allow(ip, "login")
                if not ok:
                    return await deny(429, f"Too many attempts. Try again in {wait}s.", [(b"retry-after", str(wait).encode())])
            elif path not in PUBLIC:
                connection_write = method not in SAFE and (path == "/api/apps" or path.startswith(("/api/apps/", "/api/connectors", "/api/setup")))
                company_route = path.startswith(("/api/auth/", "/api/workspaces")) or connection_write
                sess = auth.session_from_cookie(headers.get("cookie"), resolve_workspace=not company_route)
                if not sess:
                    return await deny(401, "Please sign in.")
                db.set_current(sess["ws"])
                if sess.get("must_change") and not path.startswith("/api/auth/"):
                    return await deny(403, "Please choose your own password first.")
                if method not in SAFE and not _csrf_ok(headers.get("x-csrf-token", ""), sess["csrf"]):
                    return await deny(403, "Missing or invalid security token. Reload the page.")
                if sess.get("company_ws") and path != "/api/people/directory" and path.startswith(("/api/people", "/api/admins", "/api/platform", "/api/company/delete", "/api/workspace")):
                    return await deny(403, "Manage company people and settings from the company workspace.")
                member = access.is_member(sess)
                if member and not access.member_allowed(method, path):  # members are refused by default: only the routes listed in access.py are open to them
                    return await deny(403, "That is not available to members. Ask an admin if you need access.")
                if not path.startswith(NEEDS_NO_KEYS) and not member and not keys.complete():  # members never use the AI directly, so they do not wait for the keys
                    return await deny(428, "Connect your OpenAI key first (Settings → Connections).")
                bucket = "expensive" if (method not in SAFE and path.startswith(EXPENSIVE)) or "/realtime" in path else "default"
                ok, wait = _allow(f"{sess['ws']}:{sess['id']}", bucket)
                if not ok:
                    return await deny(429, f"Slow down: try again in {wait}s.", [(b"retry-after", str(wait).encode())])
                scope["state"]["session"] = sess
        await self.app(scope, receive, self._send_with_headers(send))

    @staticmethod
    def _send_with_headers(send):
        async def wrapped(message):
            if message["type"] == "http.response.start":
                h = message.setdefault("headers", [])
                names = {k.lower() for k, _ in h}
                for k, v in (
                    (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"microphone=(self), camera=(), geolocation=()"),
                    (b"cross-origin-opener-policy", b"same-origin"), (b"cross-origin-resource-policy", b"same-origin"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                ):
                    if k not in names:
                        h.append((k, v))
                if b"cache-control" not in names:
                    h.append((b"cache-control", b"no-store"))
            await send(message)
        return wrapped


def _csrf_ok(given, expected):
    import hmac
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())

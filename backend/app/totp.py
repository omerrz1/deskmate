"""Two-factor sign-in with a 6-digit code from an authenticator app (RFC 6238, SHA-1, 30 seconds), plus single-use backup codes."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret, counter):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def verify(secret, code, last_counter=0, now=None):
    """-> the matching time step (to remember, so a code works only once), or None. Allows one step of clock drift either way."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6 or not secret:
        return None
    step = int((now if now is not None else time.time()) // 30)
    for c in (step - 1, step, step + 1):
        if c > last_counter and hmac.compare_digest(_code(secret, c), code):
            return c
    return None


def uri(secret, account, issuer="Deskmates"):
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def new_backup_codes(n=10):
    """-> (plain codes to show once, hashes to keep)."""
    plain = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return plain, [hash_code(c) for c in plain]


def hash_code(code):
    return hashlib.sha256(str(code).strip().lower().replace(" ", "").encode()).hexdigest()

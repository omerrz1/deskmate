"""The two service keys a company brings: OpenAI (everything the team thinks, writes and says) and ElevenLabs (live phone-call voices).
They are entered in the app, checked against the provider before they are accepted, and kept encrypted on this server. Nothing else works until both steps are done."""
import os
import time

import httpx

from . import db, secretbox

NAMES = {"openai": "openai_api_key", "eleven": "eleven_api_key"}
OPENAI_CHECK_URL = os.getenv("OPENAI_CHECK_URL", "https://api.openai.com/v1/models")
ELEVEN_CHECK_URL = os.getenv("ELEVEN_CHECK_URL", "https://api.elevenlabs.io/v1/user")
_cache = db.TenantDict()  # setting name -> (stored value, plain key), per workspace
_gate = db.TenantDict(lambda: [0.0, False])  # when 'all keys connected' was last worked out, per workspace


def _setting(key, default=""):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def _put(key, value):
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    _gate[0] = 0.0


def get(name):
    """The plain key, or '' when not connected."""
    stored = _setting(NAMES[name])
    if not stored:
        return ""
    hit = _cache.get(name)
    if hit and hit[0] == stored:
        return hit[1]
    plain = secretbox.unseal(stored)
    _cache[name] = (stored, plain)
    return plain


def save(name, key):
    _put(NAMES[name], secretbox.seal(key.strip()))
    if name == "eleven":
        _put("eleven_skipped", "")


def clear(name):
    db.run("DELETE FROM settings WHERE key=?", (NAMES[name],))
    _cache.pop(name, None)
    _gate[0] = 0.0


def skip_eleven(on=True):
    _put("eleven_skipped", "1" if on else "")


def last4(name):
    k = get(name)
    return k[-4:] if len(k) >= 8 else ""


def migrate_env():
    """A server that already had its keys in backend/.env keeps working: they are moved into the app once."""
    for name, var in (("openai", ("OPENAI_API_KEY", "OPENAI_KEY", "OPEN_AI_KEY")), ("eleven", ("ELEVENLABS_API_KEY",))):
        if get(name):
            continue
        val = next((os.getenv(v) for v in var if os.getenv(v)), "")
        if val:
            save(name, val)
            db.log("security", f"The {'OpenAI' if name == 'openai' else 'ElevenLabs'} key was moved from the server's .env file into the app")


def state():
    openai, eleven = bool(get("openai")), bool(get("eleven"))
    skipped = bool(_setting("eleven_skipped"))
    done = openai and (eleven or skipped)
    return {"openai": {"connected": openai, "last4": last4("openai"), "checked_at": float(_setting("openai_checked_at", "0") or 0)},
            "eleven": {"connected": eleven, "last4": last4("eleven"), "skipped": skipped and not eleven},
            "complete": done, "step": "done" if done else ("openai" if not openai else "eleven")}


def complete():
    """True once both steps are done. Cached for a second: it is asked on every request."""
    t = time.time()
    if t - _gate[0] > 1.0:
        _gate[1] = state()["complete"]
        _gate[0] = t
    return _gate[1]


async def check_openai(key):
    """-> (ok, message). A key is accepted only if OpenAI itself accepts it."""
    key = (key or "").strip()
    if len(key) < 20 or " " in key:
        return False, "That does not look like an OpenAI key. It starts with sk- and is longer."
    try:
        async with httpx.AsyncClient(timeout=20) as h:
            r = await h.get(OPENAI_CHECK_URL, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError:
        return False, "Could not reach OpenAI to check the key. Check this server's internet connection and try again."
    if r.status_code == 200:
        return True, "Connected."
    if r.status_code == 401:
        return False, "OpenAI did not accept that key. It may be mistyped, revoked, or from a different account."
    if r.status_code == 429:
        return False, "OpenAI says this account is out of quota or rate limited. Add billing at platform.openai.com, then try again."
    return False, f"OpenAI answered with an error ({r.status_code}). Try again in a minute."


async def check_eleven(key):
    key = (key or "").strip()
    if len(key) < 20 or " " in key:
        return False, "That does not look like an ElevenLabs key."
    try:
        async with httpx.AsyncClient(timeout=20) as h:
            r = await h.get(ELEVEN_CHECK_URL, headers={"xi-api-key": key})
    except httpx.HTTPError:
        return False, "Could not reach ElevenLabs to check the key. Check this server's internet connection and try again."
    if r.status_code == 200:
        return True, "Connected."
    if r.status_code == 401:
        # a restricted key may lack the permission to read the account but still be valid for speech
        try:
            if (r.json().get("detail") or {}).get("status") == "missing_permissions":
                return True, "Connected (this key cannot read account details, which is fine)."
        except Exception:
            pass
        return False, "ElevenLabs did not accept that key. It may be mistyped or revoked."
    return False, f"ElevenLabs answered with an error ({r.status_code}). Try again in a minute."

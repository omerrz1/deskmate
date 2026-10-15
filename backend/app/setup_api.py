"""Connecting the company's own OpenAI and ElevenLabs accounts. Until both steps are done the rest of the app answers 'connect your keys first'."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import auth, db, keys, llm

router = APIRouter(prefix="/api")


class KeyIn(BaseModel):
    key: str


@router.get("/setup")
def setup_state():
    return keys.state()


@router.post("/setup/openai")
async def connect_openai(k: KeyIn):
    ok, msg = await keys.check_openai(k.key)
    if not ok:
        raise HTTPException(400, msg)
    first = not keys.get("openai")
    keys.save("openai", k.key)
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES('openai_checked_at',?)", (str(db.now()),))
    db.log("security", "The OpenAI key was connected" if first else "The OpenAI key was replaced")
    return keys.state()


@router.post("/setup/eleven")
async def connect_eleven(k: KeyIn):
    first = not keys.get("eleven")
    ok, msg = await keys.check_eleven(k.key)
    if not ok:
        raise HTTPException(400, msg)
    keys.save("eleven", k.key)
    if first:  # phone calls use the connected voices from now on; this can be changed in Settings → Calling
        from . import voices
        voices.set_setting("voice_engine", "elevenlabs")
    db.log("security", "The ElevenLabs key was connected" if first else "The ElevenLabs key was replaced")
    return {**keys.state(), "message": msg}


@router.post("/setup/eleven/skip")
def skip_eleven():
    if not keys.get("openai"):
        raise HTTPException(400, "Connect OpenAI first.")
    keys.skip_eleven(True)
    db.log("security", "The ElevenLabs step was skipped for now")
    return keys.state()


@router.delete("/setup/eleven")
def disconnect_eleven():
    keys.clear("eleven")
    keys.skip_eleven(True)  # the team keeps working; phone calls use the OpenAI voice until it is connected again
    from . import voices
    voices.set_setting("voice_engine", "openai")
    db.log("security", "The ElevenLabs key was disconnected")
    return keys.state()


@router.post("/setup/test")
async def test_connections():
    """Check both keys with their providers right now."""
    out = {}
    if keys.get("openai"):
        ok, msg = await keys.check_openai(keys.get("openai"))
        out["openai"] = {"ok": ok, "message": auth.scrub(msg)}
    if keys.get("eleven"):
        ok, msg = await keys.check_eleven(keys.get("eleven"))
        out["eleven"] = {"ok": ok, "message": auth.scrub(msg)}
    return out

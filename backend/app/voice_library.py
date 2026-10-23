"""The ElevenLabs voices an admin can choose from: the ones already in the company's own ElevenLabs account, and ElevenLabs' public library.

Picking a public voice adds it to the account first, because a library voice can only be spoken by an agent once it is in the account."""
import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import auth, llm

router = APIRouter(prefix="/api/voices")
BASE = "https://api.elevenlabs.io"
VOICE_ID = re.compile(r"[A-Za-z0-9]{10,40}")        # a voice: 20 letters and digits
OWNER_ID = re.compile(r"[A-Za-z0-9]{10,100}")        # who shares a library voice: a 64-character hex string


def _headers():
    if not llm.eleven_key():
        raise HTTPException(400, "Connect your ElevenLabs key first (Settings → Connections).")
    return {"xi-api-key": llm.eleven_key()}


def _fail(r):
    raise HTTPException(502, "ElevenLabs said: " + auth.scrub(r.text)[:200])


def _row(v, source):
    labels = v.get("labels") or {}
    return {"voice_id": v.get("voice_id"), "name": v.get("name") or "", "source": source,
            "gender": (v.get("gender") or labels.get("gender") or "").lower(), "accent": v.get("accent") or labels.get("accent") or "",
            "age": v.get("age") or labels.get("age") or "", "descriptive": v.get("descriptive") or labels.get("descriptive") or "",
            "use_case": v.get("use_case") or labels.get("use_case") or "", "description": (v.get("description") or "")[:200],
            "category": v.get("category") or "", "preview_url": v.get("preview_url"), "public_owner_id": v.get("public_owner_id") or ""}


def _wanted(row, gender, accent):
    return (not gender or row["gender"] == gender.lower()) and (not accent or accent.lower() in row["accent"].lower())


@router.get("/library")
async def library(source: str = "mine", q: str = "", gender: str = "", accent: str = "", language: str = ""):
    """source=mine: the voices in this company's ElevenLabs account. source=library: ElevenLabs' public voice library, most used first."""
    headers = _headers()
    out = []
    async with httpx.AsyncClient(timeout=30) as h:
        if source == "library":
            params = {"page_size": 30, "sort": "usage_character_count_1y"}
            for key, value in (("language", llm.lang_code(language) or language), ("gender", gender), ("search", q), ("accent", accent)):
                if value:
                    params[key] = value
            r = await h.get(f"{BASE}/v1/shared-voices", headers=headers, params=params)
            if r.status_code != 200:
                _fail(r)
            return [_row(v, "library") for v in r.json().get("voices", [])]
        token = None
        for _page in range(4):  # an account can hold hundreds of voices; four pages of 100 is plenty to choose from
            params = {"page_size": 100, "sort": "name", "sort_direction": "asc", **({"search": q} if q else {}), **({"next_page_token": token} if token else {})}
            r = await h.get(f"{BASE}/v2/voices", headers=headers, params=params)
            if r.status_code != 200:
                _fail(r)
            data = r.json()
            out += [_row(v, "mine") for v in data.get("voices", [])]
            token = data.get("next_page_token")
            if not (data.get("has_more") and token):
                break
    return [r for r in out if _wanted(r, gender, accent)]


@router.get("/library/{voice_id}")
async def one(voice_id: str):
    """Name, labels and a sample of one voice, so the current choice can be shown and played."""
    if not VOICE_ID.fullmatch(voice_id):
        raise HTTPException(400, "That is not a valid ElevenLabs voice ID.")
    async with httpx.AsyncClient(timeout=20) as h:
        r = await h.get(f"{BASE}/v1/voices/{voice_id}", headers=_headers())
    if r.status_code == 404:
        raise HTTPException(404, "ElevenLabs does not know this voice in your account.")
    if r.status_code != 200:
        _fail(r)
    return _row(r.json(), "mine")


class AddIn(BaseModel):
    voice_id: str
    public_owner_id: str
    name: str = ""


@router.post("/add")
async def add(a: AddIn):
    """Put a public-library voice into the company's ElevenLabs account so calls can use it."""
    if not VOICE_ID.fullmatch(a.voice_id) or not OWNER_ID.fullmatch(a.public_owner_id):
        raise HTTPException(400, "That is not a valid ElevenLabs voice.")
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.post(f"{BASE}/v1/voices/add/{a.public_owner_id}/{a.voice_id}", headers=_headers(), json={"new_name": (a.name or "Voice")[:80]})
    text = r.text.lower()
    if r.status_code != 200 and "already" not in text:
        _fail(r)
    added = ""
    if r.status_code == 200:
        try:
            added = str(r.json().get("voice_id") or "")  # the voice as it now sits in the account
        except Exception:
            added = ""
    return {"voice_id": added if VOICE_ID.fullmatch(added) else a.voice_id, "added": r.status_code == 200}

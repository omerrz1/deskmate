"""ElevenLabs Agents as the live voice engine: one reusable agent per language; our persona, knowledge and
person data are passed in as per-call overrides. Transcripts/recordings are pulled back for our own reporting."""
import asyncio
import os
import re
import time

import httpx

from . import db, llm

BASE = "https://api.elevenlabs.io/v1"
AGENT_LLM = os.getenv("ELEVENLABS_AGENT_LLM", "gpt-4.1")
# Lower than 1 keeps the agent on its task; the wording still varies from call to call.
AGENT_TEMPERATURE = float(os.getenv("ELEVENLABS_AGENT_TEMPERATURE", "0.6"))
# "patient" waits for the person to finish thinking aloud instead of cutting in on a pause. Override: eager | normal | patient.
TURN_EAGERNESS = os.getenv("ELEVENLABS_TURN_EAGERNESS", "patient")
# Turbo keeps natural v4 speech while reducing the per-reply synthesis delay. Choose full v4 when expressiveness matters more than response speed.
# Override with ELEVENLABS_AGENT_TTS=eleven_v4, or per language with ELEVENLABS_AGENT_TTS_<CODE>.
TTS_BY_LANG = {}
DEFAULT_TTS = os.getenv("ELEVENLABS_AGENT_TTS", "eleven_v4_turbo")

db.run("CREATE TABLE IF NOT EXISTS el_agents(code TEXT PRIMARY KEY, agent_id TEXT, voice_id TEXT, model TEXT, created_at REAL)")
db.run("CREATE TABLE IF NOT EXISTS el_tools(name TEXT PRIMARY KEY, tool_id TEXT)")
for _stmt in ("ALTER TABLE el_agents ADD COLUMN overrides_ok INTEGER DEFAULT 0", "ALTER TABLE el_tools ADD COLUMN sig TEXT"):
    try:
        db.run(_stmt)
    except Exception:
        pass


def _h():
    return {"xi-api-key": llm.eleven_key(), "Content-Type": "application/json"}


def enabled():
    return bool(llm.eleven_key() and os.getenv("VOICE_ENGINE", "elevenlabs_agents") == "elevenlabs_agents")


def profile_for(contact):
    from . import voices
    code = llm.lang_code(contact.get("language"))
    from . import voices as _v
    if not (enabled() and code) or _v.get_setting("voice_engine", "elevenlabs") != "elevenlabs":
        return None
    p = voices.resolve(contact)
    return p if (p and p.get("voice_id")) else None


def available_for(contact):
    return profile_for(contact) is not None


def unavailable_reason(contact):
    """Why a call is not using ElevenLabs, in words the admin can act on (shown on the call and in its report)."""
    from . import voices
    if not llm.eleven_key():
        return "no ElevenLabs key is connected"
    if os.getenv("VOICE_ENGINE", "elevenlabs_agents") != "elevenlabs_agents":
        return "the VOICE_ENGINE setting on the server turns ElevenLabs agents off"
    if voices.get_setting("voice_engine", "elevenlabs") != "elevenlabs":
        return "calls are set to OpenAI in Settings → Calling"
    if not llm.lang_code(contact.get("language")):
        return f"there is no voice for the language '{contact.get('language') or 'unknown'}'"
    if not voices.resolve(contact):
        return f"no voice profile covers {contact.get('language') or 'this language'}"
    return "ElevenLabs could not be reached just now"


async def _tool_id(h):
    row = db.q1("SELECT tool_id FROM el_tools WHERE name='lookup_info'")
    if row:
        return row["tool_id"]
    cfg = {"tool_config": {"type": "client", "name": "lookup_info", "expects_response": True, "response_timeout_secs": 10,
                           "description": "Look up facts about the company: products or services, prices, requirements, dates, how to get started or support. Always use before stating any fact. Say just 'one sec' first.",
                           "parameters": {"type": "object", "required": ["query"], "properties": {
                               "query": {"type": "string", "description": "What to look up, e.g. 'price of the standard plan'"},
                               "category": {"type": "string", "description": "Optional: Products & services, Pricing & payment, Requirements, How to get started, FAQ, Policies, Contact & locations, Support"}}}}}
    r = await h.post(f"{BASE}/convai/tools", json=cfg)
    r.raise_for_status()
    tid = r.json()["id"]
    db.run("INSERT OR REPLACE INTO el_tools(name,tool_id) VALUES('lookup_info',?)", (tid,))
    return tid


TOOL_TIMEOUTS = {"ask_admin": 120, "send_information": 20, "search_web": 12}  # seconds a tool may take before the agent gives up on it


async def _extra_tool_ids(h):
    """Client tools for everything the agent can do besides lookup_info (they call our /phone/{id}/tool endpoint from the browser).
    A tool whose definition changed since it was registered is updated in ElevenLabs too, or the agent would keep using the old one."""
    import hashlib
    import json
    from . import agent_tools as T
    ids = []
    for name, (desc, props, req) in T.SPECS.items():
        cfg = {"tool_config": {"type": "client", "name": name, "expects_response": True, "response_timeout_secs": TOOL_TIMEOUTS.get(name, 10), "description": desc,
                               "parameters": {"type": "object", "required": req, "properties": {k: {"type": v.get("type", "string"), "description": v.get("description", k), **({"enum": v["enum"]} if v.get("enum") else {})} for k, v in props.items()}} if props else None}}
        if not props:
            cfg["tool_config"].pop("parameters")
        sig = hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]
        row = db.q1("SELECT tool_id,sig FROM el_tools WHERE name=?", (name,))
        if row:
            if row.get("sig") != sig:
                r = await h.patch(f"{BASE}/convai/tools/{row['tool_id']}", json=cfg, timeout=20)
                if r.status_code == 200:
                    db.run("UPDATE el_tools SET sig=? WHERE name=?", (sig, name))
                else:
                    db.log("voice", f"Could not update the '{name}' tool in ElevenLabs yet ({r.status_code}); the agent keeps the previous version")
            ids.append(row["tool_id"]); continue
        r = await h.post(f"{BASE}/convai/tools", json=cfg, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"tool {name}: {r.text[:200]}")
        tid = r.json()["id"]
        db.run("INSERT OR REPLACE INTO el_tools(name,tool_id,sig) VALUES(?,?,?)", (name, tid, sig))
        ids.append(tid)
    return ids


async def ensure_agent(profile):
    """One ElevenLabs agent per voice profile (so each country/language has its own agent); voice is also set per call."""
    code = llm.lang_code(profile["language"]) or "hi"
    voice = profile["voice_id"]
    key = f"p{profile['id']}"
    row = db.q1("SELECT * FROM el_agents WHERE code=?", (key,))
    model = os.getenv(f"ELEVENLABS_AGENT_TTS_{code.upper()}") or TTS_BY_LANG.get(code) or DEFAULT_TTS
    name = f"Deskmates: {profile['name']}"
    async with httpx.AsyncClient(timeout=60, headers=_h()) as h:
        if row and row["model"] == model:
            return row["agent_id"]
        if not row:
            # reuse an agent that already exists in the account (e.g. after a local database reset) instead of creating a duplicate
            r = await h.get(f"{BASE}/convai/agents", params={"search": name, "page_size": 30}, timeout=15)
            same = [x for x in (r.json().get("agents", []) if r.status_code == 200 else []) if x.get("name") == name]
            if same:
                row = {"agent_id": same[0]["agent_id"], "model": None}
        if row:  # agent already exists in the account: move it to the configured TTS model only if it is on a different one
            cur = None
            try:
                g = await h.get(f"{BASE}/convai/agents/{row['agent_id']}", timeout=8)
                cur = g.json()["conversation_config"]["tts"].get("model_id") if g.status_code == 200 else None
                if g.status_code == 404:
                    row = None  # deleted in ElevenLabs: create a fresh one below
            except Exception:
                pass
            if row and cur != model:
                try:
                    await h.patch(f"{BASE}/convai/agents/{row['agent_id']}", json={"conversation_config": {"tts": {"model_id": model}}}, timeout=20)
                except Exception:
                    pass  # not fatal: the voice is set per call, so the existing agent still works
            if row:
                db.run("INSERT OR REPLACE INTO el_agents(code,agent_id,voice_id,model,created_at) VALUES(?,?,?,?,?)", (key, row["agent_id"], voice, model, db.now()))
                return row["agent_id"]
        tid = await _tool_id(h)
        body = {"name": name, "conversation_config": {
            "agent": {"language": code, "first_message": "Hello?",
                      "prompt": {"prompt": "You are a helpful phone agent.", "llm": AGENT_LLM, "tool_ids": [tid],
                                 "built_in_tools": {"end_call": {"name": "end_call", "description": "End the call after you have said goodbye.",
                                                                 "params": {"system_tool_type": "end_call"}}}}},
            "tts": {"voice_id": voice, "model_id": model},
            "turn": {"turn_timeout": 6, "turn_eagerness": TURN_EAGERNESS}},
            "platform_settings": {"overrides": {"conversation_config_override": {
                "agent": {"prompt": {"prompt": True}, "first_message": True, "language": True}, "tts": {"voice_id": True}}}}}
        r = await h.post(f"{BASE}/convai/agents/create", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs agent create failed: {r.text[:400]}")
        aid = r.json()["agent_id"]
    db.run("INSERT OR REPLACE INTO el_agents(code,agent_id,voice_id,model,created_at) VALUES(?,?,?,?,?)", (key, aid, voice, model, db.now()))
    return aid


_down_until = 0.0


def is_down():
    """After a failure, skip ElevenLabs for a minute so calls fall straight to the backup voice instead of waiting."""
    return time.time() < _down_until


def mark_down(seconds=60):
    global _down_until
    _down_until = time.time() + seconds


async def signed_url(agent_id):
    async with httpx.AsyncClient(timeout=8, headers=_h()) as h:
        r = await h.get(f"{BASE}/convai/conversation/get-signed-url", params={"agent_id": agent_id})
        r.raise_for_status()
        return r.json()["signed_url"]


def first_line_for(contact):
    """Opens like a real person ringing someone: just checks who it is. No script, no AI call, instant."""
    first = ((contact.get("name") or "").split() or [""])[0]
    if not re.search(r"[^\W\d_]{2}", first):  # a lead saved under its phone number has no name to ask for
        first = ""
    return f"Hi, is this {first}?" if first else "Hi, hello?"


async def fetch_conversation(conversation_id, wait=25):
    """Pull transcript + audio from ElevenLabs once processing finishes. Returns (transcript[], audio_bytes|None)."""
    async with httpx.AsyncClient(timeout=40, headers=_h()) as h:
        conv, t0 = None, time.time()
        while time.time() - t0 < wait:
            r = await h.get(f"{BASE}/convai/conversations/{conversation_id}")
            if r.status_code == 200:
                conv = r.json()
                if conv.get("status") in ("done", "failed"):
                    break
            await asyncio.sleep(2)
        if not conv:
            return [], None
        tr = []
        for m in conv.get("transcript", []):
            if m.get("message"):
                tr.append({"role": "agent" if m["role"] == "agent" else "customer", "text": strip_tags(m["message"]),
                           "t": float(m.get("time_in_call_secs") or 0), "audio": []})
        return tr, None  # calls are not recorded: only the text is kept


async def apply_tuning(profile_id):
    """Push a profile's tuning to its ElevenLabs agent: turn eagerness (agent-level) and permission to override
    pace/expressiveness per call. Best-effort: ElevenLabs can be slow, so failures are logged, not raised."""
    from . import voices
    prof = next((p for p in voices.profiles() if p["id"] == profile_id), None)
    row = db.q1("SELECT agent_id FROM el_agents WHERE code=?", (f"p{profile_id}",))
    if not prof or not row:
        return False  # no agent yet: settings are applied when it is created / passed per call
    t = prof.get("tuning") or {}
    body = {"platform_settings": {"overrides": {"conversation_config_override": {
        "agent": {"prompt": {"prompt": True}, "first_message": True, "language": True},
        "tts": {"voice_id": True, "speed": True, "stability": True, "similarity_boost": True}}}}}
    code = llm.lang_code(prof["language"]) or "hi"
    model = os.getenv(f"ELEVENLABS_AGENT_TTS_{code.upper()}") or TTS_BY_LANG.get(code) or DEFAULT_TTS
    try:
        async with httpx.AsyncClient(timeout=90, headers=_h()) as h0:
            tids = [await _tool_id(h0)] + await _extra_tool_ids(h0)
    except Exception as e:
        tids = None
        db.log("voice", f"Could not register the agent tools yet ({auth_scrub(str(e))[:120]})")
    body["conversation_config"] = {"tts": {"voice_id": prof["voice_id"], "model_id": model}}
    body["conversation_config"]["agent"] = {"prompt": {"temperature": AGENT_TEMPERATURE, **({"tool_ids": tids} if tids else {})}}  # the agent itself uses this voice and model too
    body["conversation_config"]["turn"] = {"turn_eagerness": t.get("turn_eagerness") or TURN_EAGERNESS}
    try:
        async with httpx.AsyncClient(timeout=90, headers=_h()) as h:
            r = await h.patch(f"{BASE}/convai/agents/{row['agent_id']}", json=body)
        ok = r.status_code == 200
        if ok:
            db.run("UPDATE el_agents SET overrides_ok=1 WHERE code=?", (f"p{profile_id}",))  # safe to send pace/expressiveness per call
        db.log("voice", f"Tuning for '{prof['name']}' " + ("applied to its ElevenLabs agent" if ok else f"NOT applied: {llm_scrub(r.text)[:160]}"))
        return ok
    except Exception as e:
        db.log("voice", f"Tuning for '{prof['name']}' not applied yet ({type(e).__name__}); it will be retried when you save again")
        return False


def auth_scrub(t):
    from . import auth
    return auth.scrub(t)


def llm_scrub(t):
    from . import auth
    return auth.scrub(t)


def tuning_for_call(profile):
    """Per-call pace/expressiveness, only once ElevenLabs has confirmed the agent accepts those overrides."""
    row = db.q1("SELECT overrides_ok FROM el_agents WHERE code=?", (f"p{profile['id']}",))
    if not (row and row["overrides_ok"]):
        return {}
    return {k: v for k, v in (profile.get("tuning") or {}).items() if k in ("speed", "stability")}


# A reaction the voice acts out, written in square brackets: [laughs], [whispers], [sighs, softly]... Never shown in a transcript.
TAG = re.compile(r"\s*\[[A-Za-z][A-Za-z ,'\-]{2,40}\]\s*")


def strip_tags(text):
    return TAG.sub(" ", text or "").strip()


EXPRESSION_LEVELS = {
    "subtle": "Use one only now and then: a few times in a whole call.",
    "natural": ("Use one every few turns, wherever it truly fits: after something funny (their joke, or a small one of yours), when you relax or think, "
                "when you share good news. A light joke or a soft laugh at your own joke is welcome now and then; never forced, never at their expense."),
    "lively": "Use one in most turns: laugh easily, joke lightly, react out loud to what they say.",
}


def tags_guide(profile):
    """What only the ElevenLabs voice can do: act out laughs, sighs, breaths and soft asides, which is what makes a call sound alive. How often is a setting."""
    if llm.lang_code(profile.get("language")) != "en":
        return ""
    from . import voices
    level = voices.get_setting("call_expression", "natural")
    return ("\nREACTIONS: your voice acts out reactions written in square brackets just before the words they colour, and they are what makes you sound like a person, so use them. "
            "Laughter and amusement: [laughs], [chuckles], [laughs softly], [giggles]. Breath and small human moments: [sighs], [exhales]. "
            "A playful aside, or a joke said just between the two of you: [whispers] for a few words only, warm and light, never low or creepy, then straight back to your normal voice. "
            "Good news: [excited]. Slower for a detail: [slow]. " + EXPRESSION_LEVELS.get(level, EXPRESSION_LEVELS["natural"]) + " "
            "Rules: a tag colours only the next few words, so keep it short; never whisper a whole sentence or anything important; one tag at a time, no stacking; "
            "none in your very first hello, and none while you say a number, price, address or link; never laugh at something sad or serious or when they are upset; never read a tag aloud.")


async def warm_agents():
    """At startup, create/locate every profile's agent in the background so the first call never waits on ElevenLabs."""
    from . import voices
    if not enabled() or voices.get_setting("voice_engine", "elevenlabs") != "elevenlabs":
        return
    for p in voices.profiles():
        if p.get("voice_id"):
            try:
                await ensure_agent(p)
                await apply_tuning(p["id"])
            except Exception as e:
                db.log("voice", f"Could not prepare the agent for '{p['name']}' yet ({type(e).__name__}); it will be created on the first call")


async def warm_agents_all():
    """Prepare call voices for each active company, not only the legacy main tenant."""
    from . import directory
    for workspace_id in directory.active_ids():
        try:
            with db.use(workspace_id):
                await warm_agents()
        except Exception as e:
            try:
                with db.use(workspace_id):
                    db.log("voice", f"Could not prewarm ElevenLabs voices ({type(e).__name__}); first call will prepare its agent")
            except Exception:
                pass

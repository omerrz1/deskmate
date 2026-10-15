"""Real video meetings (Google Meet, Zoom, Teams): an employee joins as a participant through a meeting-bot service (Recall.ai, connected in Apps),
listens, takes notes, and speaks only when spoken to."""
import asyncio
import base64
import json
import re
import time
from urllib.parse import urlparse

from . import addressing, db, integrations, llm, office, team

db.run("CREATE TABLE IF NOT EXISTS video_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, url TEXT, bot_id TEXT, status TEXT DEFAULT 'joining', cursor INTEGER DEFAULT 0, "
       "notes TEXT DEFAULT '', minutes TEXT DEFAULT '', error TEXT DEFAULT '', started_at REAL, ended_at REAL, last_spoke REAL DEFAULT 0, last_status_check REAL DEFAULT 0)")

HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com", "webex.com")
_S = {"type": "string"}
TOOLS = [
    {"name": "join_video_meeting", "description": "Join a real video meeting (Google Meet, Zoom or Teams link) as a participant. You will listen, take notes and speak when spoken to. Needs the meeting bot connected by the admin.",
     "parameters": {"type": "object", "properties": {"url": {**_S, "description": "the meeting link"}}, "required": ["url"]}},
    {"name": "say_in_video_meeting", "description": "Say something aloud in the real video meeting you are in. Keep it to one or two short sentences.", "parameters": {"type": "object", "properties": {"text": _S}, "required": ["text"]}},
    {"name": "leave_video_meeting", "description": "Leave the real video meeting you are in and write the minutes.", "parameters": {"type": "object", "properties": {}}},
    {"name": "video_meeting_notes", "description": "Read what has been said so far in the real video meeting you are in.", "parameters": {"type": "object", "properties": {}}},
]
NAMES = {t["name"] for t in TOOLS}
_busy = db.TenantSet()  # sessions where a reply is being prepared
_pending = db.TenantDict()  # session id -> (speaker, text) waiting for a pause before answering


def _valid_url(url):
    try:
        u = urlparse(url if "://" in url else "https://" + url)
        return u.scheme in ("http", "https") and any(u.hostname == h or u.hostname.endswith("." + h) for h in HOSTS)
    except Exception:
        return False


async def _run(action, args):
    inst = integrations.get_by_key("recall")
    if not inst or not inst["enabled"]:
        raise integrations.AppError("Real video meetings are not set up yet. The admin can connect the meeting bot in Apps (Recall.ai).")
    tool = next(t for t in integrations.tools_of(inst) if t["name"] == action)
    return await integrations.execute(inst, tool, args)


def active_for(agent_id):
    return db.q1("SELECT * FROM video_sessions WHERE agent_id=? AND status IN ('joining','in_call') ORDER BY id DESC LIMIT 1", (agent_id,))


async def join(agent, url):
    if not _valid_url(url):
        return {"error": "That doesn't look like a Google Meet, Zoom or Teams link."}
    if active_for(agent["id"]):
        return {"error": "You are already in a meeting. Leave it first."}
    try:
        res = await _run("join", {"meeting_url": url if "://" in url else "https://" + url, "bot_name": agent["name"]})
    except integrations.AppError as e:
        return {"error": str(e)}
    bot_id = res if isinstance(res, str) else (res or {}).get("id") if isinstance(res, dict) else None
    if not bot_id:
        return {"error": "The meeting service did not return a bot. Check the meeting link and try again."}
    sid = db.run("INSERT INTO video_sessions(agent_id,url,bot_id,started_at) VALUES(?,?,?,?)", (agent["id"], url[:500], str(bot_id), db.now()))
    team.log_event(agent["id"], "meeting", "calendar", f"Joined a video meeting: {url[:80]}", {"session": sid})
    return {"session_id": sid, "status": "joining", "note": "It can take a moment to be let in. You will hear the conversation and answer only when someone speaks to you."}


async def say(agent, text, sid=None):
    s = db.q1("SELECT * FROM video_sessions WHERE id=?", (sid,)) if sid else active_for(agent["id"])
    if not s or s["status"] not in ("joining", "in_call"):
        return {"error": "You are not in a video meeting right now."}
    text = str(text).strip()[:600]
    if not text:
        return {"error": "Nothing to say."}
    try:
        mp3 = await llm.tts_agent(text, agent["openai_voice"], f"You are {agent['name']}, speaking in a live video meeting: natural, clear and brisk.")
        await _run("say_audio", {"bot_id": s["bot_id"], "b64": base64.b64encode(mp3).decode()})
    except integrations.AppError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Could not speak ({type(e).__name__})"}
    db.update("video_sessions", s["id"], last_spoke=time.time(), notes=(s["notes"] + f"\n{agent['name']}: {text}")[-20000:])
    return {"said": True}


async def leave(agent, sid=None, reason="left"):
    s = db.q1("SELECT * FROM video_sessions WHERE id=?", (sid,)) if sid else active_for(agent["id"])
    if not s or s["status"] not in ("joining", "in_call"):
        return {"error": "You are not in a video meeting right now."}
    try:
        await _run("leave", {"bot_id": s["bot_id"]})
    except Exception:
        pass
    return await _finish(s, agent, reason)


async def _finish(s, agent, reason="ended"):
    minutes = ""
    if s["notes"].strip():
        try:
            info = await llm.complete_json("Write meeting minutes as JSON with keys: summary (2-4 plain sentences), decisions (array), action_items (array of 'Name: task'). English.", s["notes"][-12000:])
            minutes = json.dumps(info, ensure_ascii=False)
        except Exception:
            pass
    db.update("video_sessions", s["id"], status="left", ended_at=db.now(), minutes=minutes)
    ag = agent or db.q1("SELECT * FROM agents WHERE id=?", (s["agent_id"],))
    if ag:
        team.log_event(ag["id"], "meeting", "calendar", f"Video meeting {reason}" + (f": {json.loads(minutes).get('summary', '')[:150]}" if minutes else ""), {"session": s["id"]})
        if minutes:
            try:
                m = json.loads(minutes)
                await office.email_admin(f"{ag['name']}'s meeting notes", f"{m.get('summary', '')}\n\nDecisions:\n- " + "\n- ".join(m.get("decisions") or ["none"]) + "\n\nAction items:\n- " + "\n- ".join(m.get("action_items") or ["none"]))
            except Exception:
                pass
    return {"left": True, "minutes": json.loads(minutes) if minutes else None}


def _segment_text(seg):
    words = seg.get("words") or []
    return (" ".join(str(w.get("text", "")) for w in words) if words else str(seg.get("text", ""))).strip()


_last_addresser = db.TenantDict()  # session id -> (person, time): who spoke to us last


async def _is_for_me(ag, text, tail, spoke_recently, sid=None, who=""):
    first = str(ag["name"]).split()[0]
    if re.search(rf"\b{re.escape(first)}\b", text, re.I):
        return True
    if not spoke_recently or len(text.split()) > 30:
        return False
    la = _last_addresser.get(sid)
    if la and la[0] == who and time.time() - la[1] < 30 and len(text.split()) <= 20 and not re.match(r"^\s*(hey |hi |ok |okay )?[A-Z][a-z]+[,:]", text):
        return True  # the person we were just talking with carries on, without saying our name again
    try:
        res = await llm.complete_json("In a video meeting, decide if the last line is directed at the named person (a follow-up to what they just said counts). Reply as JSON {\"for_them\": true|false}.",
                                      f"Person: {ag['name']} (they spoke a moment ago)\nRecent lines:\n{tail[-700:]}\nLast line: {text}", model="gpt-4.1-nano")
        return bool(res.get("for_them"))
    except Exception:
        return False


async def poll_one(s):
    from . import agents
    ag = agents.get(s["agent_id"])
    if not ag:
        return await _finish(s, None, "ended (employee removed)")
    now = time.time()
    # is the bot still in the call?
    if now - (s["last_status_check"] or 0) > 20:
        db.update("video_sessions", s["id"], last_status_check=now)
        try:
            st = await _run("status", {"bot_id": s["bot_id"]})
            codes = [str(x.get("code", "")) for x in (st if isinstance(st, list) else [])]
            if codes and any(c in ("call_ended", "done", "fatal") for c in codes[-2:]):
                return await _finish(s, ag, "ended")
            if codes and any(c in ("in_call_recording", "in_call_not_recording") for c in codes):
                db.update("video_sessions", s["id"], status="in_call")
        except integrations.AppError as e:
            db.update("video_sessions", s["id"], error=str(e)[:200])
    try:
        data = await _run("transcript", {"bot_id": s["bot_id"]})
    except integrations.AppError as e:
        db.update("video_sessions", s["id"], error=str(e)[:200])
        return
    segs = data if isinstance(data, list) else (data or {}).get("results") or []
    new = segs[s["cursor"]:]
    if not new:
        pend = _pending.pop(s["id"], None)  # nobody spoke since: a pause, so now is a good time to answer
        if pend and s["id"] not in _busy and time.time() - (s["last_spoke"] or 0) > 4:
            _busy.add(s["id"])
            try:
                await _reply(ag, s, *pend)
            finally:
                _busy.discard(s["id"])
        return
    lines = []
    addressed = None
    spoke_recently = time.time() - (s["last_spoke"] or 0) < addressing.FOLLOW_WINDOW
    for seg in new:
        who = str(seg.get("speaker") or (seg.get("participant") or {}).get("name") or "Someone")
        text = _segment_text(seg)
        if not text:
            continue
        lines.append(f"{who}: {text}")
        if who.lower().split()[0] != str(ag["name"]).lower().split()[0] and await _is_for_me(ag, text, s["notes"] + "\n" + "\n".join(lines), spoke_recently, s["id"], who):
            addressed = (who, text)
            _last_addresser[s["id"]] = (who, time.time())
    notes = (s["notes"] + "\n" + "\n".join(lines))[-20000:]
    db.update("video_sessions", s["id"], cursor=len(segs), notes=notes, status="in_call", error="")
    if addressed:
        _pending[s["id"]] = addressed  # answer once they pause


async def _reply(ag, s, who, text):
    from . import agents
    s = db.q1("SELECT * FROM video_sessions WHERE id=?", (s["id"],))
    prompt = (f"You are in a real video meeting. Recent conversation:\n{s['notes'][-2500:]}\n\n{who} just said: \"{text}\". It was directed at you. "
              "Reply aloud in one or two short sentences. Use your tools first if you need a fact. NEVER make up facts, numbers or progress: if you are not certain, say you will check and follow up by email after the meeting. "
              "Do not share internal or customer details with people you do not know.")
    final = ""
    async for ev in agents.run_loop(ag, [{"role": "user", "content": prompt}], "external", max_steps=4):
        if ev["type"] == "done":
            final = ev["text"]
    if final.strip() and not final.strip().lower().startswith("[pass]"):
        await say(ag, final.strip(), s["id"])


async def poll():
    for s in db.q("SELECT * FROM video_sessions WHERE status IN ('joining','in_call')"):
        try:
            await poll_one(s)
        except Exception as e:
            db.update("video_sessions", s["id"], error=f"{type(e).__name__}")


async def loop():
    from . import directory
    while True:
        for ws in directory.active_ids():
            with db.use(ws):
                try:
                    await poll()
                except Exception:
                    pass
        await asyncio.sleep(3)


def listing(agent_id):
    out = []
    for s in db.q("SELECT * FROM video_sessions WHERE agent_id=? ORDER BY id DESC LIMIT 5", (agent_id,)):
        out.append({"id": s["id"], "url": s["url"], "status": s["status"], "error": s["error"], "started_at": s["started_at"], "ended_at": s["ended_at"],
                    "tail": s["notes"].strip().splitlines()[-6:], "minutes": json.loads(s["minutes"]) if s["minutes"] else None})
    return out


async def run_tool(agent, name, a):
    if name == "join_video_meeting":
        return await join(agent, a.get("url", "")), None
    if name == "say_in_video_meeting":
        return await say(agent, a.get("text", "")), None
    if name == "leave_video_meeting":
        return await leave(agent), None
    if name == "video_meeting_notes":
        s = active_for(agent["id"]) or db.q1("SELECT * FROM video_sessions WHERE agent_id=? ORDER BY id DESC LIMIT 1", (agent["id"],))
        return ({"status": s["status"], "notes": s["notes"][-3000:]} if s else {"error": "No video meeting yet."}), None
    return {"error": "unknown tool"}, None

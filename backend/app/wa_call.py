"""Customer voice calls over Twilio WhatsApp or Twilio Voice.

Twilio uses signed HTTPS callbacks and bidirectional Media Streams. Existing Meta
chats keep their WebRTC transport. Both share consent, call limits, AI engines,
transcripts and summaries; audio is not recorded.
"""
import asyncio
import base64
import fractions
import json
import os
import time

import httpx

from . import chats, db, integrations, llm, voices

try:  # the rest of the app works without it; only WhatsApp calling needs it
    import av
    from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import AudioStreamTrack
    HAVE_RTC = True
except Exception:  # pragma: no cover
    av = None
    HAVE_RTC = False
    AudioStreamTrack = object

MAX_CALLS = int(os.getenv("WA_MAX_CALLS", "4"))  # at once on this server
MAX_MINUTES = int(os.getenv("WA_CALL_MAX_MINUTES", "10"))
RING_SECONDS = int(os.getenv("WA_RING_SECONDS", "45"))
IDLE_SECONDS = 120  # nobody spoke for this long: hang up
MAX_PER_CHAT_PER_DAY = 3
GRAPH = chats.GRAPH
OPENAI_RT = os.getenv("OPENAI_REALTIME_URL", "wss://api.openai.com/v1/realtime")
RATE = 48000
FRAME = 960  # 20 ms at 48 kHz

SESSIONS = {}  # (workspace, call id) -> Session
BY_WA = {}  # WhatsApp call id -> (workspace, call id)


class CallError(Exception):
    pass


# ---------- audio plumbing ----------
class PcmTrack(AudioStreamTrack):
    """Plays PCM16 mono to the far side in real time (any source rate); plays silence while there is nothing to say."""
    kind = "audio"

    def __init__(self, src_rate=24000):
        super().__init__()
        self.buf = bytearray()
        self.src_rate = src_rate
        self._rs = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        self._pts = 0
        self._t0 = None

    def set_rate(self, rate):
        self.src_rate = rate

    def push(self, pcm):
        if not pcm:
            return
        f = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
        f.sample_rate = self.src_rate
        f.planes[0].update(pcm if len(pcm) % 2 == 0 else pcm + b"\0")
        for o in self._rs.resample(f):
            self.buf += bytes(o.planes[0])[:o.samples * 2]

    def clear(self):
        self.buf.clear()

    @property
    def speaking(self):
        return len(self.buf) > FRAME * 2

    async def recv(self):
        if self._t0 is None:
            self._t0 = time.monotonic()
        wait = self._t0 + self._pts / RATE - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        need = FRAME * 2
        chunk = bytes(self.buf[:need])
        del self.buf[:need]
        fr = av.AudioFrame(format="s16", layout="mono", samples=FRAME)
        fr.sample_rate = RATE
        fr.planes[0].update(chunk.ljust(need, b"\0"))
        fr.pts = self._pts
        fr.time_base = fractions.Fraction(1, RATE)
        self._pts += FRAME
        return fr


def _ice():
    urls = [u.strip() for u in os.getenv("WA_STUN", "stun:stun.l.google.com:19302").split(",") if u.strip()]
    servers = [RTCIceServer(urls=urls)] if urls else []
    if os.getenv("WA_TURN_URL"):
        servers.append(RTCIceServer(urls=[os.getenv("WA_TURN_URL")], username=os.getenv("WA_TURN_USER", ""), credential=os.getenv("WA_TURN_PASS", "")))
    return RTCConfiguration(iceServers=servers)


# ---------- Meta's Calling API ----------
def _wa():
    inst = integrations.get_by_key("whatsapp_cloud")
    if not inst or not inst["enabled"]:
        raise CallError("WhatsApp Business is not connected in Apps.")
    if not inst["allow_write"]:
        raise CallError("Changes are switched off for WhatsApp Business in Apps, so calls are off too.")
    c = integrations._config(inst)
    return c["access_token"], c["phone_number_id"], c.get("api_version") or "v23.0"


async def graph(method, path, body=None, params=None):
    token, pnid, ver = _wa()
    async with httpx.AsyncClient(timeout=20) as h:
        r = await h.request(method, f"{GRAPH}/{ver}/{pnid}{path}", json=body, params=params, headers={"Authorization": f"Bearer {token}"})
    try:
        data = r.json()
    except Exception:
        data = {}
    if r.status_code >= 400:
        err = (data.get("error") or {}) if isinstance(data, dict) else {}
        raise CallError(f"WhatsApp said: {err.get('error_user_msg') or err.get('message') or r.text[:160]} ({err.get('code', r.status_code)})")
    return data


def available():
    return HAVE_RTC


def status():
    """For the settings page."""
    out = {"installed": HAVE_RTC, "active_calls": len([1 for (ws, _c) in SESSIONS if ws == db.current()]), "max_minutes": MAX_MINUTES, "ice": os.getenv("WA_STUN", "stun:stun.l.google.com:19302")}
    return out


# ---------- permission ----------
def perm_state(chat):
    until = chat["call_perm_until"] or 0
    if chat["call_perm"] == "granted" and until > time.time():
        return "granted", until
    return (chat["call_perm"] or ""), until


async def refresh_permission(chat):
    if chat["provider"] == "twilio":
        return perm_state(chat)
    try:
        d = await graph("GET", "/call_permissions", params={"user_wa_id": chat["ext_id"]})
    except CallError:
        return perm_state(chat)
    p = d.get("permission") or {}
    st = (p.get("status") or "").lower()
    if st in ("temporary", "permanent", "granted"):
        until = float(p.get("expiration_time") or 0) or time.time() + (365 * 86400 if st == "permanent" else 3 * 86400)
        db.update("chats", chat["id"], call_perm="granted", call_perm_until=until)
    elif st in ("no_permission", "denied", "expired", "none"):
        db.update("chats", chat["id"], call_perm="denied" if chat["call_perm"] == "denied" else "", call_perm_until=0)
    return perm_state(chats._chat(chat["id"]))


async def ask_permission(chat_id, text, agent=None, admin=False):
    chat = chats._chat(chat_id)
    if not chat or chat["channel"] != "whatsapp" or chat["provider"] not in ("twilio", "whatsapp_cloud"):
        return {"error": "WhatsApp calls need a connected Twilio or WhatsApp Business chat."}
    st, _ = perm_state(chat)
    if st == "granted":
        return {"note": "They have already allowed calls. You can call them now."}
    if time.time() - (chat["call_perm_asked_at"] or 0) < 24 * 3600:
        return {"error": "Permission was already asked in the last 24 hours. WhatsApp allows one request a day, and two a week. Wait for their answer."}
    is_open, _ = chats.window(chat)
    if not is_open:
        return {"error": "It has been more than 24 hours since they wrote, so the permission request cannot be sent as a free message."}
    text = (text or "").strip()[:200] or f"May we call you on WhatsApp about this? You can tap Allow or Don't allow."
    if not admin and chats.send_policy() != "auto":
        mid = chats.add_message(chat_id, "out", text, "call_permission_request", None, "", "pending_approval", (agent or {}).get("id"), (agent or {}).get("name", ""))
        return {"state": "waiting_for_approval", "note": "The request to ask their permission is waiting for the admin's OK."}
    return await _send_permission(chat, text, agent)


async def _send_permission(chat, text, agent=None, mid=None):
    try:
        if voices.get_setting("call_route", "whatsapp_then_phone") != "phone":
            from . import twilio_call
            d = await twilio_call.send_permission(chat)
        else:
            d = await chats._exec("whatsapp_cloud", "request_call_permission", {"to": chat["ext_id"], "text": text})
    except (integrations.AppError, CallError) as e:
        return {"error": str(e)}
    ext = (d.get("sid") or (((d or {}).get("messages") or [{}])[0].get("id"))) if isinstance(d, dict) else ""
    db.update("chats", chat["id"], call_perm_asked_at=time.time())
    if mid:
        db.update("chat_messages", mid, status="sent", ext_id=ext or "", sent_at=db.now())
    else:
        chats.add_message(chat["id"], "out", text, "call_permission_request", None, ext or "", "sent", (agent or {}).get("id"), (agent or {}).get("name", "Admin"))
    return {"state": "asked", "note": "Asked. When they tap Allow you will see it in the chat, and you can call."}


async def approve_permission(mid):
    msg = db.q1("SELECT * FROM chat_messages WHERE id=? AND status='pending_approval' AND kind='call_permission_request'", (mid,))
    if not msg:
        return False, "Nothing is waiting with that id."
    r = await _send_permission(chats._chat(msg["chat_id"]), msg["body"], None, mid)
    return ("error" not in r), r.get("error", "")


# ---------- starting a call ----------
def _campaign_for(agent):
    aid = (agent or {}).get("id")
    name = f"WhatsApp calls{(' · ' + agent['name']) if agent else ''}"
    row = db.q1("SELECT id FROM campaigns WHERE name=?", (name,))
    if row:
        return row["id"]
    kb = db.q1("SELECT id FROM knowledge_bases ORDER BY id LIMIT 1")
    return db.run("INSERT INTO campaigns(name,objective,status,max_attempts,created_at,agent_id,kb_id) VALUES(?,?,?,?,?,?,?)",
                  (name, "Talk with the customer who messaged us on WhatsApp.", "draft", 1, db.now(), aid, (kb or {}).get("id")))


def _checks(chat, agent, rtc=True):
    if rtc and not HAVE_RTC:
        raise CallError("WhatsApp calling needs the 'aiortc' package on the server (pip install aiortc).")
    if chat["channel"] != "whatsapp" or chat["provider"] not in ("twilio", "whatsapp_cloud"):
        raise CallError("WhatsApp calls need a connected Twilio or WhatsApp Business chat.")
    if chat["provider"] == "twilio":
        from . import twilio_call
        twilio_call.config()
        twilio_call.base_url()
    contact = db.q1("SELECT do_not_call FROM contacts WHERE id=?", (chat["contact_id"],)) if chat["contact_id"] else None
    if contact and contact["do_not_call"]:
        raise CallError("This customer asked not to be called.")
    if any(ws == db.current() and s.chat['id'] == chat['id'] for (ws, _), s in SESSIONS.items()):
        raise CallError("A call with this customer is already starting or in progress.")
    if len([1 for k in SESSIONS if k[0] == db.current()]) >= 2 or len(SESSIONS) >= MAX_CALLS:
        raise CallError("Too many WhatsApp calls are running right now. Try again in a minute.")
    from . import agents, costs
    if agent:
        why = costs.over_cap(agent["id"])
        if why:
            raise CallError(f"Calls are paused: {why}.")
    if agents.paused():
        raise CallError("Work is paused (the Pause button), so no calls are placed.")
    n = db.q1("SELECT COUNT(*) n FROM calls WHERE chat_id=? AND mode IN ('whatsapp','phone') AND direction='out' AND created_at>?", (chat["id"], db.now() - 86400))["n"]
    if n >= MAX_PER_CHAT_PER_DAY:
        raise CallError("Already called this customer three times today.")


async def request_call(chat_id, objective, agent=None):
    """An employee wants to call. Starts now, or waits for the admin's OK (Settings, Chats)."""
    chat = chats._chat(chat_id)
    if not chat:
        return {"error": "No such chat."}
    objective = (objective or "").strip()[:500] or "Have a short conversation about their message."
    if chats.call_policy() != "auto":
        chats.add_message(chat_id, "out", objective, "call_request", None, "", "pending_approval", (agent or {}).get("id"), (agent or {}).get("name", ""))
        return {"state": "waiting_for_approval", "note": "The call is waiting for the admin's OK. Tell the customer a call is coming soon, not that it has started."}
    r = await start(chat_id, (agent or {}).get("id"), objective, by=(agent or {}).get("name", ""))
    return r if "error" in r else {"state": "initiated", "call_id": r["call_id"], "note": "Twilio accepted the call request; the recipient's phone is not confirmed as ringing yet. The result will appear in the chat and on the Calls page."}


async def start(chat_id, agent_id, objective, by="admin"):
    """Place the call. -> {'call_id': n} or {'error': text}."""
    from . import agents
    chat = chats._chat(chat_id)
    if not chat:
        return {"error": "That chat no longer exists."}
    agent = agents.get(agent_id) if agent_id else agents.get(chat["agent_id"] or chats.default_agent_id() or 0)
    route = voices.get_setting("call_route", "whatsapp_then_phone")
    try:
        _checks(chat, agent, rtc=False)  # All outbound routes now use Twilio's media stream transport.
        if route != "phone":
            from . import twilio_call
            twilio_call.config()
            st, _ = perm_state(chat)
            if st != "granted":
                st, _ = await refresh_permission(chat)
            if st != "granted":
                raise CallError("This customer has not allowed WhatsApp calls yet. Ask their permission first; phone fallback only applies after a permitted WhatsApp call fails to connect.")
        else:
            from . import twilio_call
            twilio_call.voice_config()
        contact_id = chat["contact_id"] or chats.contact_for_phone(chat["ext_id"], chat["name"])
        db.update("chats", chat["id"], contact_id=contact_id)
        cid = db.run("INSERT INTO calls(contact_id,campaign_id,status,mode,direction,objective,chat_id,language,created_at,ringing_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (contact_id, _campaign_for(agent), "ringing", "phone" if route == "phone" else "whatsapp", "out", objective, chat["id"], "English", db.now(), db.now()))
    except CallError as e:
        return {"error": str(e)}
    chats.add_message(chat["id"], "out", f"Calling by {'phone' if route == 'phone' else 'WhatsApp'}. {objective}", "call", None, "", "sent", (agent or {}).get("id"), by or "", call_id=cid)
    from .twilio_call import Session as session_type
    sess = None
    try:
        sess = session_type(db.current(), cid, chat, agent, objective, "out", **({'pstn': True} if route == "phone" else {}))
        SESSIONS[(db.current(), cid)] = sess
        await sess.place()
    except Exception as e:
        db.log("error", f"Call start failed ({type(e).__name__}): {str(e)[:180]}", call_id=cid)
        # Only deterministic Twilio 4xx rejections prove that no call was placed;
        # ambiguous timeouts/server errors must never trigger a duplicate call.
        if route == "whatsapp_then_phone" and isinstance(e, Exception) and getattr(e, 'status', None) and 400 <= e.status < 500 and e.status != 429:
            try:
                await sess._close_transport()
                from .twilio_call import Session as TwilioSession
                sess = TwilioSession(db.current(), cid, chat, agent, objective, "out", pstn=True)
                SESSIONS[(db.current(), cid)] = sess
                await sess.place()
                db.update("calls", cid, mode="phone")
                return {"call_id": cid}
            except Exception as fallback_error:
                e = fallback_error
        msg = str(e) if isinstance(e, CallError) else f"The call could not be started ({type(e).__name__})."
        if sess:
            await sess.end("failed", msg)
        else:
            db.update("calls", cid, status="failed", ended_at=db.now(), outcome="unresolved", summary=msg)
            chats.add_message(chat["id"], "out", msg, "call", None, "", "sent", None, "Deskmates", call_id=cid)
        return {"error": msg}
    return {"call_id": cid}


# ---------- a live call ----------
class Session:
    def __init__(self, ws, call_id, chat, agent, objective, direction, rtc=True):
        self.ws, self.call_id, self.chat, self.agent, self.objective, self.direction = ws, call_id, chat, agent, objective, direction
        self.pc = RTCPeerConnection(_ice()) if rtc else None
        self.out = PcmTrack(24000) if rtc else None
        if self.pc:
            self.pc.addTrack(self.out)
        self.engine = None
        self.wa_call_id = ""
        self.answered = False
        self.ended = False
        self.t_answer = None
        self.lines = []
        self.last_voice = time.time()
        self.tasks = []
        self.answer_set = False
        if self.pc:
            self.pc.on("track", self._on_track)

    def _spawn(self, coro):
        async def run():
            tok = db._tenant.set(self.ws)  # background work stays inside this company
            try:
                await coro
            except asyncio.CancelledError:
                pass
            except Exception as e:
                db.log("error", f"WhatsApp call {self.call_id}: {type(e).__name__}: {str(e)[:100]}", call_id=self.call_id)
            finally:
                db._tenant.reset(tok)
        t = asyncio.get_running_loop().create_task(run())
        # A task canceled before its first step never enters run(); close its coroutine too.
        t.add_done_callback(lambda _: coro.close())
        self.tasks.append(t)
        return t

    # -- starting --
    async def place(self):
        await self.pc.setLocalDescription(await self.pc.createOffer())
        d = await graph("POST", "/calls", {"messaging_product": "whatsapp", "to": self.chat["ext_id"], "action": "connect",
                                           "session": {"sdp_type": "offer", "sdp": self.pc.localDescription.sdp}, "biz_opaque_callback_data": f"deskmates:{self.call_id}"})
        self.wa_call_id = (((d or {}).get("calls") or [{}])[0].get("id")) or ""
        if not self.wa_call_id:
            raise CallError("WhatsApp did not accept the call request.")
        BY_WA[self.wa_call_id] = (self.ws, self.call_id)
        db.update("calls", self.call_id, ext_id=self.wa_call_id)
        self._spawn(self._ring_timeout())

    async def accept_inbound(self, wa_call_id, offer_sdp):
        self.wa_call_id = wa_call_id
        BY_WA[wa_call_id] = (self.ws, self.call_id)
        db.update("calls", self.call_id, ext_id=wa_call_id)
        await self.pc.setRemoteDescription(RTCSessionDescription(offer_sdp, "offer"))
        await self.pc.setLocalDescription(await self.pc.createAnswer())
        sdp = self.pc.localDescription.sdp
        await graph("POST", "/calls", {"messaging_product": "whatsapp", "call_id": wa_call_id, "action": "pre_accept", "session": {"sdp_type": "answer", "sdp": sdp}})
        await asyncio.sleep(0.4)
        await graph("POST", "/calls", {"messaging_product": "whatsapp", "call_id": wa_call_id, "action": "accept", "session": {"sdp_type": "answer", "sdp": sdp}})
        self.answer_set = True
        await self.answered_now()

    async def _ring_timeout(self):
        await asyncio.sleep(RING_SECONDS)
        if not self.answered and not self.ended:
            await self.end("no_answer", "They did not answer.")

    # -- events from WhatsApp --
    async def on_answer(self, sdp):
        """The customer picked up: Meta sends its side of the connection."""
        if self.answer_set or not sdp:
            return
        self.answer_set = True
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp, "answer"))
        await self.answered_now()

    async def answered_now(self):
        if self.answered or self.ended:
            return
        self.answered = True
        self.t_answer = time.time()
        self.last_voice = time.time()
        db.update("calls", self.call_id, status="in_progress", answered_at=db.now())
        db.log("call", "WhatsApp call answered", call_id=self.call_id)
        self._spawn(self._run_engine())
        self._spawn(self._watchdog())

    def _on_track(self, track):
        if track.kind == "audio":
            self._spawn(self._pump_in(track))

    async def _pump_in(self, track):
        rs = av.AudioResampler(format="s16", layout="mono", rate=24000)
        while not self.ended:
            try:
                fr = await track.recv()
            except Exception:
                break
            if not self.engine:
                continue
            for f in rs.resample(fr):
                await self.engine.send_audio(bytes(f.planes[0])[:f.samples * 2])

    # -- the voice AI --
    async def _run_engine(self):
        from . import callengine
        call, contact, camp = callengine.call_context(self.call_id)
        engine = None
        try:
            engine = await make_engine(self, call, contact, camp)
        except Exception as e:
            db.log("error", f"WhatsApp call voice could not start: {type(e).__name__}: {str(e)[:100]}", call_id=self.call_id)
        if not engine:
            await self.end("failed", "The voice assistant could not start.")
            return
        self.engine = engine
        try:
            await engine.run()
        finally:
            if not self.ended:
                await self.end("completed")

    async def _watchdog(self):
        t0 = time.time()
        while not self.ended:
            await asyncio.sleep(3)
            if time.time() - t0 > MAX_MINUTES * 60:
                await self.end("completed", "Time limit reached.")
            elif time.time() - self.last_voice > IDLE_SECONDS:
                await self.end("completed", "Nobody spoke for a while.")

    def say(self, who, text):
        text = (text or "").strip()
        if not text:
            return
        self.last_voice = time.time()
        self.lines.append({"role": "agent" if who == "agent" else "customer", "text": text, "t": round(time.time() - (self.t_answer or time.time()), 1), "audio": []})
        db.update("calls", self.call_id, transcript=json.dumps(self.lines, ensure_ascii=False))

    async def _terminate(self):
        await graph("POST", "/calls", {"messaging_product": "whatsapp", "call_id": self.wa_call_id, "action": "terminate"})

    async def _close_transport(self):
        await self.pc.close()

    # -- ending --
    async def end(self, outcome="completed", note=""):
        if self.ended:
            return
        self.ended = True
        for k, v in list(BY_WA.items()):
            if v == (self.ws, self.call_id):
                BY_WA.pop(k, None)
        SESSIONS.pop((self.ws, self.call_id), None)
        if self.wa_call_id and outcome != "remote_ended":
            try:
                await self._terminate()
            except Exception:
                pass
        if self.engine:
            try:
                await self.engine.close()
            except Exception:
                pass
        try:
            await self._close_transport()
        except Exception:
            pass
        for t in self.tasks:
            if t is not asyncio.current_task():
                t.cancel()
        await _finish(self, outcome, note)


async def _finish(sess, outcome, note):
    """Write the call up like any other call, and tell the chat how it went."""
    from . import callengine
    cid = sess.call_id
    try:
        if not sess.answered:
            status = {"no_answer": "no_answer", "remote_ended": "no_answer", "declined": "declined"}.get(outcome, "failed")
            call = db.q1("SELECT campaign_id FROM calls WHERE id=?", (cid,))
            if call and call.get("campaign_id") and status in ("no_answer", "declined"):
                callengine.mark_missed(cid, status)
            else:
                summary = note or {"no_answer": "They did not answer.", "declined": "They declined the call."}.get(status, "The call did not connect.")
                outcome = "no_answer" if status != "failed" else "unresolved"
                report = {"executive_summary": summary, "objective": "", "objective_result": "unclear",
                          "conversation_flow": "The call did not connect.", "customer_needs": [], "questions_and_answers": [],
                          "objections_or_concerns": [], "commitments": [], "key_facts_shared": [], "follow_up_actions": [],
                          "risks_or_gaps": ["No conversation transcript exists because the call did not connect."],
                          "outcome": outcome, "sentiment": "unknown", "transcript_status": "unavailable"}
                db.update("calls", cid, status=status, outcome=outcome, ended_at=db.now(), summary=summary, report=report)
                from . import files
                files.update_call_sheet(cid, status, summary)
            text = note or ("They did not answer." if status == "no_answer" else "They declined the call." if status == "declined" else "The call did not connect.")
            if sess.chat and sess.chat.get("id"):
                chats.add_message(sess.chat["id"], "out", text, "call", None, "", "sent", None, "Deskmates", call_id=cid)
            return
        row = await callengine.finalize(cid, "completed")
        if sess.chat and sess.chat.get("id"):
            dur = int(row["duration"] or 0)
            text = f"{('Phone' if getattr(sess, 'pstn', False) else 'WhatsApp')} call ended ({dur // 60}:{dur % 60:02d}). {row['summary'] or ''}".strip()
            chats.add_message(sess.chat["id"], "out", text, "call", None, "", "sent", None, "Deskmates", call_id=cid)
        if sess.agent:
            from . import team
            team.log_event(sess.agent["id"], "call", "phone", f"{('Phone' if getattr(sess, 'pstn', False) else 'WhatsApp')} call with {chats.title(sess.chat) if sess.chat and sess.chat.get('id') else (sess.chat or {}).get('name', 'customer')}: {row['outcome']}", {"call": cid, "summary": (row["summary"] or "")[:300]})
    except Exception as e:
        db.log("error", f"Could not write up WhatsApp call {cid}: {type(e).__name__}", call_id=cid)


def end_now(call_id):
    """The admin pressed End call."""
    s = SESSIONS.get((db.current(), call_id))
    if not s:
        return False
    asyncio.get_running_loop().create_task(_in_ws(s.ws, s.end("completed", "Ended by the admin.")))
    return True


async def _in_ws(ws, coro):
    tok = db._tenant.set(ws)
    try:
        return await coro
    finally:
        db._tenant.reset(tok)


def live_calls():
    return [{"call_id": cid, "chat_id": (s.chat or {}).get("id"), "who": chats.title(s.chat) if s.chat and s.chat.get("id") else (s.chat or {}).get("name", "Customer"), "answered": s.answered, "direction": s.direction, "lines": s.lines[-12:],
             "seconds": int(time.time() - s.t_answer) if s.t_answer else 0} for (ws, cid), s in SESSIONS.items() if ws == db.current()]


# ---------- events from Meta (called by chat_hooks.py) ----------
def _sdp_of(ev):
    return ((ev.get("session") or {}).get("sdp")) or ((((ev.get("connection") or {}).get("webrtc")) or {}).get("sdp")) or ""


async def on_event(ev):
    cid = ev.get("id") or ""
    kind = ev.get("kind")
    hit = BY_WA.get(cid)
    sess = SESSIONS.get(hit) if hit and hit[0] == db.current() else None
    if kind == "status":  # RINGING, ACCEPTED, REJECTED, FAILED
        st = (ev.get("status") or "").upper()
        if not sess:
            return
        if st == "ACCEPTED":
            if sess.direction == "out" and not sess.answer_set and _sdp_of(ev):
                await sess.on_answer(_sdp_of(ev))
            elif sess.answer_set:
                await sess.answered_now()
        elif st in ("REJECTED", "DECLINED"):
            await sess.end("declined", "They declined the call.")
        elif st == "FAILED":
            errs = "; ".join(e.get("message", "") or e.get("title", "") for e in ev.get("errors") or [])
            await sess.end("failed", f"The call failed. {errs}".strip())
        return
    event = (ev.get("event") or "").lower()
    if event == "connect":
        direction = (ev.get("direction") or "").upper()
        if sess and direction != "USER_INITIATED":
            await sess.on_answer(_sdp_of(ev))
        elif not sess and direction == "USER_INITIATED":
            await _inbound(ev)
    elif event == "terminate":
        if sess:
            await sess.end("remote_ended", "")


async def _inbound(ev):
    """A customer is calling us on WhatsApp."""
    wa_call_id, num = ev.get("id", ""), chats._digits(ev.get("from"))
    off = chats.setting("wa_inbound_calls", "off") != "answer"
    try:
        if off or not HAVE_RTC:
            await graph("POST", "/calls", {"messaging_product": "whatsapp", "call_id": wa_call_id, "action": "reject"})
            return
        chat = chats.get_or_create("whatsapp", "whatsapp_cloud", num)
        _checks(chat, None)
        from . import agents
        agent = agents.get(chat["agent_id"] or chats.default_agent_id() or 0)
        contact_id = chat["contact_id"] or chats.contact_for_phone(num, chat["name"])
        db.update("chats", chat["id"], contact_id=contact_id)
        cid = db.run("INSERT INTO calls(contact_id,campaign_id,status,mode,direction,objective,chat_id,language,created_at,ringing_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (contact_id, _campaign_for(agent), "ringing", "whatsapp", "in", "Help the customer who is calling us on WhatsApp.", chat["id"], "English", db.now(), db.now()))
        chats.add_message(chat["id"], "in", "Called us on WhatsApp.", "call", None, "", "received", None, "", call_id=cid)
        sess = Session(db.current(), cid, chat, agent, "Help the customer who is calling us on WhatsApp.", "in")
        SESSIONS[(db.current(), cid)] = sess
        try:
            await sess.accept_inbound(wa_call_id, _sdp_of(ev))
        except Exception as e:
            await sess.end("failed", f"Could not answer ({type(e).__name__}).")
    except CallError:
        try:
            await graph("POST", "/calls", {"messaging_product": "whatsapp", "call_id": wa_call_id, "action": "reject"})
        except Exception:
            pass


# ---------- the voice engines ----------
def _tool_defs():
    from . import agent_tools
    return [{"type": "function", "name": "lookup_info", "description": "Look up facts about the company: products or services, prices, requirements, dates, how to get started or support. Use before stating any fact.",
             "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "category": {"type": "string"}}, "required": ["query"]}},
            {"type": "function", "name": "end_call", "description": "Hang up. Call it right after you have said your goodbye, when the conversation is finished.", "parameters": {"type": "object", "properties": {}, "required": []}},
            *agent_tools.realtime_defs()]


async def run_tool(sess, name, args):
    """One tool the voice AI asked for. -> text for the model."""
    from . import agent_tools, callengine, knowledge
    if name == "lookup_info":
        hits = await knowledge.lookup_company(args.get("query", ""), call_id=sess.call_id)
        return "\n\n".join(hits)
    return await agent_tools.arun(sess.call_id, name, args)


class OpenAIEngine:
    """OpenAI Realtime over a server-side WebSocket: 24 kHz PCM in, 24 kHz PCM out, with function calling."""

    def __init__(self, sess, instructions, voice, language):
        self.sess, self.instructions, self.voice, self.language = sess, instructions, voice, language
        self.ws = None
        self.seen = set()
        self.closing = False

    async def run(self):
        from websockets.asyncio.client import connect
        model = llm.REALTIME_MODEL
        async with connect(f"{OPENAI_RT}?model={model}", additional_headers={"Authorization": f"Bearer {llm.api_key()}"}, max_size=None, open_timeout=15) as ws:
            self.ws = ws
            tr = {"model": "gpt-4o-transcribe"}
            if llm.lang_code(self.language):
                tr["language"] = llm.lang_code(self.language)
            await ws.send(json.dumps({"type": "session.update", "session": {
                "type": "realtime", "model": model, "instructions": self.instructions, "output_modalities": ["audio"],
                "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000}, "transcription": tr, "turn_detection": {"type": "semantic_vad", "eagerness": "medium"}, "noise_reduction": {"type": "far_field"}},
                          "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": self.voice, "speed": 1.0}},
                "max_output_tokens": 600, "tools": _tool_defs(), "tool_choice": "auto"}}))
            greeting = ("The customer just called us. Greet them now with your first turn." if self.sess.direction == "in" else "The customer just picked up the phone. Begin the call now with your first turn.")
            await ws.send(json.dumps({"type": "response.create", "response": {"instructions": greeting}}))
            async for raw in ws:
                if self.sess.ended:
                    break
                await self._event(json.loads(raw))

    async def send_audio(self, pcm):
        if self.ws and not self.closing:
            try:
                await self.ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}))
            except Exception:
                pass

    async def _event(self, ev):
        t = ev.get("type", "")
        s = self.sess
        if t in ("response.output_audio.delta", "response.audio.delta"):
            s.out.push(base64.b64decode(ev.get("delta", "")))
        elif t in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
            s.say("agent", ev.get("transcript", ""))
        elif t == "conversation.item.input_audio_transcription.completed":
            s.say("customer", ev.get("transcript", ""))
        elif t == "input_audio_buffer.speech_started":
            s.out.clear()  # they interrupted: stop talking
            s.last_voice = time.time()
        elif t == "response.function_call_arguments.done":
            s._spawn(self._tool(ev.get("name", ""), ev.get("call_id", ""), ev.get("arguments", "{}")))
        elif t == "response.done":
            for it in (ev.get("response") or {}).get("output") or []:
                if it.get("type") == "function_call":
                    s._spawn(self._tool(it.get("name", ""), it.get("call_id", ""), it.get("arguments", "{}")))
        elif t == "error":
            db.log("voice", f"[whatsapp call {s.call_id}] realtime error: {json.dumps(ev.get('error', ev))[:200]}", call_id=s.call_id)

    async def _tool(self, name, call_id, args_json):
        if not call_id or call_id in self.seen:
            return
        self.seen.add(call_id)
        try:
            args = json.loads(args_json or "{}")
        except Exception:
            args = {}
        if name == "end_call":
            await self._reply(call_id, "Goodbye said.", respond=False)
            for _ in range(40):  # let the goodbye finish playing
                await asyncio.sleep(0.25)
                if not self.sess.out.speaking:
                    break
            await asyncio.sleep(1.0)
            await self.sess.end("completed", "The assistant ended the call.")
            return
        try:
            result = await run_tool(self.sess, name, args)
        except Exception:
            result = "That didn't work. Carry on naturally and say a colleague will follow up."
        await self._reply(call_id, str(result)[:24000])

    async def _reply(self, call_id, text, respond=True):
        if not self.ws:
            return
        await self.ws.send(json.dumps({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": call_id, "output": text}}))
        if respond:
            await self.ws.send(json.dumps({"type": "response.create"}))

    async def close(self):
        self.closing = True
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass


class ElevenEngine:
    """An ElevenLabs conversational agent over WebSocket (the same agents and voices as phone calls)."""

    def __init__(self, sess, url, prompt, first, language, voice_id, tuning):
        self.sess, self.url, self.prompt, self.first, self.language, self.voice_id, self.tuning = sess, url, prompt, first, language, voice_id, tuning
        self.ws = None
        self.closing = False
        self._rs16 = av.AudioResampler(format="s16", layout="mono", rate=16000)  # the agent listens at 16 kHz

    async def run(self):
        from websockets.asyncio.client import connect
        async with connect(self.url, max_size=None, open_timeout=15) as ws:
            self.ws = ws
            await ws.send(json.dumps({"type": "conversation_initiation_client_data", "conversation_config_override": {
                "agent": {"prompt": {"prompt": self.prompt}, "first_message": self.first, "language": self.language},
                "tts": {"voice_id": self.voice_id, **(self.tuning or {})}}}))
            async for raw in ws:
                if self.sess.ended:
                    break
                await self._event(json.loads(raw))

    async def send_audio(self, pcm24k):
        if not self.ws or self.closing:
            return
        try:
            await self.ws.send(json.dumps({"user_audio_chunk": base64.b64encode(self._to16k(pcm24k)).decode()}))
        except Exception:
            pass

    async def _event(self, ev):
        t = ev.get("type", "")
        s = self.sess
        if t == "conversation_initiation_metadata":
            fmt = (ev.get("conversation_initiation_metadata_event") or {}).get("agent_output_audio_format", "pcm_16000")
            if not fmt.startswith("pcm_"):
                raise CallError(f"ElevenLabs sends audio as {fmt}, which this bridge cannot play.")
            s.out.set_rate(int(fmt.split("_")[1]))
        elif t == "audio":
            s.out.push(base64.b64decode((ev.get("audio_event") or {}).get("audio_base_64", "")))
        elif t == "user_transcript":
            s.say("customer", (ev.get("user_transcription_event") or {}).get("user_transcript", ""))
        elif t == "agent_response":
            s.say("agent", (ev.get("agent_response_event") or {}).get("agent_response", ""))
        elif t == "interruption":
            s.out.clear()
        elif t == "ping":
            await self.ws.send(json.dumps({"type": "pong", "event_id": (ev.get("ping_event") or {}).get("event_id")}))
        elif t == "client_tool_call":
            c = ev.get("client_tool_call") or {}
            s._spawn(self._tool(c.get("tool_name", ""), c.get("tool_call_id", ""), c.get("parameters") or {}))

    async def _tool(self, name, call_id, args):
        try:
            result, err = await run_tool(self.sess, name, args if isinstance(args, dict) else {}), False
        except Exception:
            result, err = "That didn't work. Carry on and say a colleague will follow up.", True
        await self.ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": call_id, "result": str(result)[:24000], "is_error": err}))

    def _to16k(self, pcm):
        """24 kHz mono PCM16 to 16 kHz."""
        f = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
        f.sample_rate = 24000
        f.planes[0].update(pcm if len(pcm) % 2 == 0 else pcm + b"\0")
        return b"".join(bytes(o.planes[0])[:o.samples * 2] for o in self._rs16.resample(f))

    async def close(self):
        self.closing = True
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass


async def make_engine(sess, call, contact, camp):
    """The same choice as a normal phone call: an ElevenLabs agent when the company uses ElevenLabs voices, otherwise OpenAI Realtime."""
    from . import callengine, eleven_agents
    ag = sess.agent or {}
    profile = eleven_agents.profile_for(contact)
    if profile and ag.get("eleven_voice"):
        profile = {**profile, "voice_id": ag["eleven_voice"]}
    if profile and not eleven_agents.is_down():
        try:
            prompt = callengine.system_prompt(call, contact, camp, realtime=True, external=True) + eleven_agents.tags_guide(profile)
            aid = await asyncio.wait_for(eleven_agents.ensure_agent(profile), 12)
            url = await eleven_agents.signed_url(aid)
            first = "Hello, thanks for calling, how can I help?" if sess.direction == "in" else eleven_agents.first_line_for(contact)
            return ElevenEngine(sess, url, prompt, first, llm.lang_code(contact.get("language")) or "en", profile["voice_id"], eleven_agents.tuning_for_call(profile))
        except Exception as e:
            eleven_agents.mark_down()
            db.log("error", f"ElevenLabs was not available for a WhatsApp call, using OpenAI voice: {str(e)[:100]}", call_id=sess.call_id)
    prompt = callengine.system_prompt(call, contact, camp, realtime=True, external=False)
    from . import voices
    configured = (camp or {}).get("voice") or voices.get_setting("call_voice", "marin")
    voice = ag.get("openai_voice") if ag.get("openai_voice") in llm.REALTIME_VOICES else (configured if configured in llm.REALTIME_VOICES else "marin")
    return OpenAIEngine(sess, prompt, voice, contact.get("language"))

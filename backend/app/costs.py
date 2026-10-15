"""What the AI costs. Every OpenAI chat and embedding call is recorded with its real token counts and priced from the table below;
voice (meetings, phone calls, speech) is estimated from minutes and characters. Spend caps use these numbers: when a day's cap is reached
the team stops picking up new work (admin chats and meetings keep working) and the admin is told.
Prices are USD per million tokens and are an approximation of the public price list; the totals are an estimate, not an invoice."""
import contextlib
import contextvars
import time

from . import db

PRICES = {  # (input, cached input, output)
    "gpt-4.1-nano": (0.10, 0.025, 0.40), "gpt-4.1-mini": (0.40, 0.10, 1.60), "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60), "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-5-mini": (0.25, 0.025, 2.00), "gpt-5-nano": (0.05, 0.005, 0.40), "gpt-5": (1.25, 0.125, 10.00),
    "o4-mini": (1.10, 0.275, 4.40), "text-embedding-3-small": (0.02, 0.02, 0.0), "text-embedding-3-large": (0.13, 0.13, 0.0),
}
DEFAULT_PRICE = (1.00, 0.25, 4.00)  # a model we do not know: a middle estimate
# estimates for voice, in USD
EST = {"meeting_min": 0.06, "call_min": 0.10, "tts_1k_chars": 0.015, "stt_min": 0.006}

_ctx = contextvars.ContextVar("cost_ctx", default=(None, "other"))

db.run("CREATE TABLE IF NOT EXISTS llm_usage(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, agent_id INTEGER, purpose TEXT, model TEXT, "
       "tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, usd REAL DEFAULT 0, estimated INTEGER DEFAULT 0)")
db.run("CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(ts)")
db.run("CREATE TABLE IF NOT EXISTS voice_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER, agent_id INTEGER, listener INTEGER, started REAL, closed INTEGER DEFAULT 0)")


def init():
    """Per-employee controls (spend cap, outside-email approval). Run once the agents table exists."""
    for stmt in ("ALTER TABLE agents ADD COLUMN spend_cap_day REAL DEFAULT 0", "ALTER TABLE agents ADD COLUMN approve_outside INTEGER DEFAULT 0"):
        try:
            db.run(stmt)
        except Exception:
            pass


def _price(model):
    best = max((k for k in PRICES if str(model).startswith(k)), key=len, default=None)
    return PRICES[best] if best else DEFAULT_PRICE


@contextlib.contextmanager
def scope(agent_id, purpose):
    """Everything sent to OpenAI inside this block is charged to this employee and purpose. Keep it short: no yield inside."""
    tok = _ctx.set((agent_id, purpose))
    try:
        yield
    finally:
        _ctx.reset(tok)


def current():
    return _ctx.get()


def record_usage(model, usage, agent_id=None, purpose="other"):
    if usage is None:
        return
    g = lambda o, k: (getattr(o, k, None) if not isinstance(o, dict) else o.get(k)) or 0  # noqa: E731
    pin = g(usage, "prompt_tokens") or g(usage, "input_tokens")
    pout = g(usage, "completion_tokens") or g(usage, "output_tokens")
    det = getattr(usage, "prompt_tokens_details", None) or (usage.get("prompt_tokens_details") if isinstance(usage, dict) else None)
    cached = g(det, "cached_tokens") if det else 0
    p_in, p_cached, p_out = _price(model)
    usd = ((pin - cached) * p_in + cached * p_cached + pout * p_out) / 1e6
    db.run("INSERT INTO llm_usage(ts,agent_id,purpose,model,tokens_in,tokens_out,usd,estimated) VALUES(?,?,?,?,?,?,?,0)", (db.now(), agent_id, purpose, str(model)[:60], pin, pout, usd))


def record_estimate(agent_id, purpose, usd, model="estimate", units=0):
    if usd > 0:
        db.run("INSERT INTO llm_usage(ts,agent_id,purpose,model,tokens_in,tokens_out,usd,estimated) VALUES(?,?,?,?,?,?,?,1)", (db.now(), agent_id, purpose, model, int(units), 0, usd))


def speech_cost(chars):
    return chars / 1000 * EST["tts_1k_chars"]


def open_voice_session(meeting_id, agent_id, listener):
    db.run("INSERT INTO voice_sessions(meeting_id,agent_id,listener,started) VALUES(?,?,?,?)", (meeting_id, agent_id, int(bool(listener)), db.now()))


def close_voice_sessions(meeting_id):
    """When a meeting ends, charge the voice minutes used: a session that listens is billed for the whole meeting, one that only speaks for about a quarter."""
    for s in db.q("SELECT * FROM voice_sessions WHERE meeting_id=? AND closed=0", (meeting_id,)):
        minutes = max(0.0, (db.now() - s["started"]) / 60)
        record_estimate(s["agent_id"], "meeting-voice", minutes * EST["meeting_min"] * (1.0 if s["listener"] else 0.25), "realtime", int(minutes * 60))
        db.run("UPDATE voice_sessions SET closed=1 WHERE id=?", (s["id"],))


def day_start(offset_days=0):
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)) + offset_days * 86400


def spend(lo=None, hi=None, agent_id=None):
    w, a = ["ts>=?", "ts<?"], [lo if lo is not None else day_start(), hi if hi is not None else time.time() + 1]
    if agent_id is not None:
        w.append("agent_id=?"); a.append(agent_id)
    return db.q1("SELECT COALESCE(SUM(usd),0) s FROM llm_usage WHERE " + " AND ".join(w), tuple(a))["s"]


def _setting(key, default="0"):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def caps():
    return {"company_day": float(_setting("spend_cap_company_day") or 0), "agent_day": float(_setting("spend_cap_agent_day") or 0)}


def set_caps(company_day=None, agent_day=None):
    for k, v in (("spend_cap_company_day", company_day), ("spend_cap_agent_day", agent_day)):
        if v is not None:
            db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, str(max(0.0, float(v)))))


def _usd(x):
    return f"${x:.2f}" if x >= 0.1 else f"${x:.4f}"


def over_cap(agent_id=None):
    """-> a plain-language reason when today's cap has been reached (for the company or this employee), else ''. 0 means no cap."""
    c = caps()
    if c["company_day"] and spend() >= c["company_day"]:
        return f"the company's daily spend cap ({_usd(c['company_day'])}) was reached"
    if agent_id is not None:
        a = db.q1("SELECT name, spend_cap_day FROM agents WHERE id=?", (agent_id,)) or {}
        cap = (a.get("spend_cap_day") or 0) or c["agent_day"]
        if cap and spend(agent_id=agent_id) >= cap:
            return f"{a.get('name', 'this employee')}'s daily spend cap ({_usd(cap)}) was reached"
    return ""


def summary(days=14):
    days = max(1, min(days, 90))
    lo = day_start(-(days - 1))
    by_day = []
    for i in range(days):
        a = day_start(-(days - 1) + i)
        by_day.append({"day": time.strftime("%a %d", time.localtime(a + 3600)), "usd": round(spend(a, a + 86400), 4)})
    rows = db.q("SELECT agent_id, purpose, model, SUM(usd) usd, SUM(tokens_in) tin, SUM(tokens_out) tout, MAX(estimated) est FROM llm_usage WHERE ts>=? GROUP BY agent_id, purpose, model", (lo,))
    names = {a["id"]: a["name"] for a in db.q("SELECT id,name FROM agents")}
    by_agent, by_purpose, by_model = {}, {}, {}
    for r in rows:
        k = names.get(r["agent_id"], "Company (not tied to one employee)")
        by_agent[k] = by_agent.get(k, 0) + r["usd"]
        by_purpose[r["purpose"]] = by_purpose.get(r["purpose"], 0) + r["usd"]
        by_model[r["model"]] = by_model.get(r["model"], 0) + r["usd"]
    today_by = {}
    for r in db.q("SELECT agent_id, SUM(usd) usd FROM llm_usage WHERE ts>=? GROUP BY agent_id", (day_start(),)):
        today_by[r["agent_id"]] = r["usd"]
    agents_ = [{"id": a["id"], "name": a["name"], "today": round(today_by.get(a["id"], 0), 4), "cap": a["spend_cap_day"] or 0} for a in db.q("SELECT id,name,spend_cap_day FROM agents WHERE archived=0 ORDER BY id")]
    srt = lambda d: sorted(([k, round(v, 4)] for k, v in d.items()), key=lambda x: -x[1])  # noqa: E731
    return {"today": round(spend(), 4), "week": round(spend(day_start(-6)), 4), "period": round(spend(lo), 4), "days": days, "by_day": by_day, "by_agent": srt(by_agent), "by_purpose": srt(by_purpose),
            "by_model": srt(by_model), "caps": caps(), "agents": agents_, "tokens": sum(r["tin"] + r["tout"] for r in rows), "note": "Estimated from token counts and public prices. Voice is approximated from minutes. Your OpenAI and ElevenLabs invoices are the source of truth."}

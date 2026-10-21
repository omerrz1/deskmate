import json
import time

from . import db

SUCCESS = {"success", "payment_promised", "appointment_booked", "interested", "resolved"}
OUTCOMES = ["success", "payment_promised", "appointment_booked", "interested", "callback_requested",
            "not_interested", "wrong_number", "refused", "no_answer", "voicemail", "unresolved"]


def contact_out(c):
    c = dict(c)
    c["extra"] = db.jload(c.get("extra"), {})
    c.pop("report", None)
    from . import countries
    info = countries.lookup(c.get("phone"))
    c["flag"] = countries.flag(info["iso"]) if info else ""
    c["country"] = c.get("country") or (info["country"] if info else "")
    return c


def build_filter(filters, alias=""):
    """Segment filter -> (where_sql, args). Supported: ids, dataset_id, language[], status[], tags, search, extra{}, exclude_dnc."""
    f = filters or {}
    col = (lambda name: f"{alias}.{name}") if alias else (lambda name: name)
    w, a = [f"{col('do_not_call')}=0"], []
    if f.get("ids"):
        w.append(f"{col('id')} IN ({','.join('?' * len(f['ids']))})")
        a += list(f["ids"])
    if f.get("dataset_id"):
        w.append(f"{col('dataset_id')}=?")
        a.append(f["dataset_id"])
    for key in ("language", "status"):
        v = f.get(key)
        if v:
            v = v if isinstance(v, list) else [v]
            w.append(f"lower({col(key)}) IN ({','.join('?' * len(v))})")
            a += [str(x).lower() for x in v]
    if f.get("tags"):
        w.append(f"lower({col('tags')}) LIKE ?")
        a.append(f"%{str(f['tags']).lower()}%")
    if f.get("last_outcome"):
        w.append(f"lower({col('last_outcome')})=?")
        a.append(str(f["last_outcome"]).lower())
    if f.get("search"):
        w.append(f"(lower({col('name')}) LIKE ? OR {col('phone')} LIKE ? OR lower({col('company')}) LIKE ? OR lower({col('extra')}) LIKE ?)")
        s = f"%{str(f['search']).lower()}%"
        a += [s, s, s, s]
    for k, v in (f.get("extra") or {}).items():
        w.append(f"lower({col('extra')}) LIKE ?")
        a.append(f'%"{k}": "{str(v).lower()}%')
    return " AND ".join(w), a


def find_contacts(filters, limit=500, offset=0):
    where, args = build_filter(filters)
    return [contact_out(c) for c in db.q(
        f"SELECT * FROM contacts WHERE {where} ORDER BY id LIMIT ? OFFSET ?", args + [limit, offset])]


def count_contacts(filters):
    where, args = build_filter(filters)
    return db.q1(f"SELECT COUNT(*) n FROM contacts WHERE {where}", args)["n"]


def segments():
    out = {}
    for col in ("language", "status", "last_outcome", "tags"):
        rows = db.q(f"SELECT {col} v, COUNT(*) n FROM contacts WHERE {col} IS NOT NULL AND {col}!='' GROUP BY {col} ORDER BY n DESC LIMIT 30")
        out[col] = rows
    return out


def call_out(c, with_transcript=True):
    c = dict(c)
    c["key_points"] = db.jload(c.get("key_points"), [])
    t = db.jload(c.get("transcript"), [])
    c["turns"] = len(t)
    if with_transcript:
        from . import agent_tools
        c["transcript"] = t
        c["actions"] = agent_tools.for_call(c["id"])
    else:
        c.pop("transcript", None)
    return c


def stats():
    day = 86400
    t0 = time.time()
    total_contacts = db.q1("SELECT COUNT(*) n FROM contacts")["n"]
    calls = db.q("SELECT status,outcome,sentiment,duration,created_at,language,mode FROM calls")
    done = [c for c in calls if c["status"] in ("completed", "no_answer", "declined", "failed")]
    answered = [c for c in calls if c["status"] == "completed"]
    success = [c for c in answered if c["outcome"] in SUCCESS]
    by_day = {}
    for i in range(13, -1, -1):
        d = time.strftime("%m-%d", time.localtime(t0 - i * day))
        by_day[d] = {"day": d, "answered": 0, "missed": 0}
    for c in done:
        d = time.strftime("%m-%d", time.localtime(c["created_at"] or t0))
        if d in by_day:
            by_day[d]["answered" if c["status"] == "completed" else "missed"] += 1
    outcomes, sentiments, langs = {}, {}, {}
    for c in answered:
        outcomes[c["outcome"] or "unresolved"] = outcomes.get(c["outcome"] or "unresolved", 0) + 1
        sentiments[c["sentiment"] or "neutral"] = sentiments.get(c["sentiment"] or "neutral", 0) + 1
        langs[c["language"] or "?"] = langs.get(c["language"] or "?", 0) + 1
    return {
        "contacts": total_contacts,
        "contacted": db.q1("SELECT COUNT(DISTINCT contact_id) n FROM calls WHERE status='completed'")["n"],
        "calls_total": len(calls),
        "calls_today": len([c for c in calls if (c["created_at"] or 0) > t0 - day]),
        "live": len([c for c in calls if c["status"] == "in_progress"]),
        "ringing": len([c for c in calls if c["status"] == "ringing"]),
        "queued": len([c for c in calls if c["status"] == "queued"]),
        "answer_rate": round(len(answered) / len(done) * 100) if done else 0,
        "success_rate": round(len(success) / len(answered) * 100) if answered else 0,
        "avg_duration": round(sum(c["duration"] or 0 for c in answered) / len(answered)) if answered else 0,
        "by_day": list(by_day.values()),
        "outcomes": [{"k": k, "n": v} for k, v in sorted(outcomes.items(), key=lambda x: -x[1])],
        "sentiments": [{"k": k, "n": v} for k, v in sentiments.items()],
        "languages": [{"k": k, "n": v} for k, v in sorted(langs.items(), key=lambda x: -x[1])],
        "active_campaigns": db.q1("SELECT COUNT(*) n FROM campaigns WHERE status='active'")["n"],
        "callbacks": db.q("""SELECT c.id call_id, c.follow_up, c.ended_at, k.id contact_id, k.name, k.phone
                             FROM calls c JOIN contacts k ON k.id=c.contact_id
                             WHERE c.outcome='callback_requested' ORDER BY c.ended_at DESC LIMIT 6"""),
    }

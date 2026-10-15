"""Scorecards: how each employee is doing over a period, from the work they actually did. No made-up score: every number is a count or an average
you can check on the employee's own pages."""
import time

from . import costs, db


def _n(sql, args=()):
    return (db.q1(sql, args) or {}).get("n") or 0


def card(a, lo, hi):
    aid = a["id"]
    done = db.q("SELECT started_at, done_at, due_ts FROM duties WHERE agent_id=? AND status='done' AND done_at>=? AND done_at<?", (aid, lo, hi))
    times = [(d["done_at"] - d["started_at"]) / 60 for d in done if d["started_at"] and d["done_at"] and 0 < d["done_at"] - d["started_at"] < 86400]
    due = [d for d in done if d["due_ts"]]
    actions = _n("SELECT COUNT(*) n FROM agent_activity WHERE agent_id=? AND ts>=? AND ts<? AND kind IN ('tool','mail','file','task','call')", (aid, lo, hi))
    failed = _n("SELECT COUNT(*) n FROM agent_activity WHERE agent_id=? AND ts>=? AND ts<? AND ok=0", (aid, lo, hi))
    sent = _n("SELECT COUNT(*) n FROM emails WHERE agent_id=? AND folder='sent' AND sent_at>=? AND sent_at<?", (aid, lo, hi))
    rejected = _n("SELECT COUNT(*) n FROM emails WHERE agent_id=? AND status='rejected' AND created_at>=? AND created_at<?", (aid, lo, hi))
    calls = db.q("SELECT c.duration, c.outcome FROM calls c JOIN campaigns p ON p.id=c.campaign_id WHERE p.agent_id=? AND c.created_at>=? AND c.created_at<?", (aid, lo, hi))
    meetings = _n("SELECT COUNT(*) n FROM meetings WHERE agent_ids LIKE ? AND created_at>=? AND created_at<?", (f"%{aid}%", lo, hi))
    spend = costs.spend(lo, hi, aid)
    series = []
    days = max(1, min(int((hi - lo) // 86400), 14))
    for i in range(days):
        t0 = hi - (days - i) * 86400
        series.append(_n("SELECT COUNT(*) n FROM duties WHERE agent_id=? AND status='done' AND done_at>=? AND done_at<?", (aid, t0, t0 + 86400)))
    return {"id": aid, "name": a["name"], "role": a["role"], "color": a["color"], "is_manager": bool(a.get("is_manager")),
            "done": len(done), "open": _n("SELECT COUNT(*) n FROM duties WHERE agent_id=? AND status IN ('todo','doing')", (aid,)),
            "stuck": _n("SELECT COUNT(*) n FROM duties WHERE agent_id=? AND status='todo' AND COALESCE(attempts,0)>=3", (aid,)),
            "avg_minutes": round(sum(times) / len(times), 1) if times else None,
            "on_time": round(100 * sum(1 for d in due if d["done_at"] <= d["due_ts"]) / len(due)) if due else None, "due_total": len(due),
            "actions": actions, "failed": failed, "failure_rate": round(100 * failed / actions) if actions else None,
            "emails_sent": sent, "emails_rejected": rejected, "calls": len(calls), "calls_avg_seconds": round(sum(c["duration"] or 0 for c in calls) / len(calls)) if calls else None,
            "meetings": meetings, "spend": round(spend, 4), "cost_per_task": round(spend / len(done), 4) if done and spend else None, "series": series}


def all_cards(days=30):
    days = max(1, min(int(days), 365))
    hi = time.time() + 1
    lo = hi - days * 86400
    cards = [card(a, lo, hi) for a in db.q("SELECT * FROM agents WHERE archived=0 ORDER BY id")]
    total = {"done": sum(c["done"] for c in cards), "stuck": sum(c["stuck"] for c in cards), "failed": sum(c["failed"] for c in cards), "spend": round(sum(c["spend"] for c in cards), 4),
             "emails_sent": sum(c["emails_sent"] for c in cards), "calls": sum(c["calls"] for c in cards)}
    return {"days": days, "cards": cards, "total": total}

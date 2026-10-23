"""Calling windows, scheduled times and callback-time parsing, in the company's time zone (Settings → Company), or the server's own clock when none is set.

A server usually runs on UTC. Without this, "only call between 10:00 and 18:00" would have meant 11:00 to 19:00 in London in summer."""
import datetime as dt
import functools

from . import db

DEFAULT_WINDOW = {"start": "09:00", "end": "20:00", "days": [0, 1, 2, 3, 4, 5, 6]}
DEFAULT_FOLLOWUP = {"callbacks": True, "nudge_days": 2, "nudge_max": 1}


@functools.lru_cache(maxsize=16)
def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def tz():
    """The company's time zone, or None (the server's own clock) when none is set or the name is not a time zone."""
    from . import voices
    try:
        name = voices.get_setting("timezone", "")
    except Exception:
        return None
    return _zone(name) if name else None


def local(ts):
    """A moment as a clock reading in the company's time zone."""
    return dt.datetime.fromtimestamp(ts, tz())


def _hm(s, default):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default


def window_of(camp):
    w = db.jload((camp or {}).get("call_window"), None)
    return {**DEFAULT_WINDOW, **w} if w else None


def followup_of(camp):
    return {**DEFAULT_FOLLOWUP, **(db.jload((camp or {}).get("followup"), None) or {})}


def in_window(win, ts):
    if not win:
        return True
    t = local(ts)
    mins = t.hour * 60 + t.minute
    return t.weekday() in win["days"] and _hm(win["start"], 540) <= mins < _hm(win["end"], 1200)


def next_open(win, ts):
    """Earliest time >= ts that falls inside the calling window."""
    if not win or in_window(win, ts):
        return ts
    start = _hm(win["start"], 540)
    base = local(ts)
    for add in range(0, 9):
        d = (base + dt.timedelta(days=add)).replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
        if d.timestamp() >= ts and d.weekday() in win["days"]:
            return d.timestamp()
    return ts


def clamp(camp, ts):
    return next_open(window_of(camp), ts)


def parse_when(s):
    """ISO-8601 string -> epoch seconds (local), or None."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", ""))
        if d.tzinfo is None and tz():
            d = d.replace(tzinfo=tz())  # "15:00" means 15:00 where the company is
        return d.timestamp()
    except Exception:
        return None


def describe(ts):
    return local(ts).strftime("%a %d %b, %H:%M")

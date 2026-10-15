"""Calling windows, scheduled times and callback-time parsing (server local time)."""
import datetime as dt
import time

from . import db

DEFAULT_WINDOW = {"start": "09:00", "end": "20:00", "days": [0, 1, 2, 3, 4, 5, 6]}
DEFAULT_FOLLOWUP = {"callbacks": True, "nudge_days": 2, "nudge_max": 1}


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
    t = time.localtime(ts)
    mins = t.tm_hour * 60 + t.tm_min
    return t.tm_wday in win["days"] and _hm(win["start"], 540) <= mins < _hm(win["end"], 1200)


def next_open(win, ts):
    """Earliest time >= ts that falls inside the calling window."""
    if not win or in_window(win, ts):
        return ts
    start = _hm(win["start"], 540)
    base = dt.datetime.fromtimestamp(ts)
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
        return d.timestamp()
    except Exception:
        return None


def describe(ts):
    return time.strftime("%a %d %b, %H:%M", time.localtime(ts))

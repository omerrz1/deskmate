"""Everyday tools every employee has by default: they use only public information and need no account or key."""
import datetime as dt
import html
import math
import re
import time
from urllib.parse import quote, unquote, urlparse, parse_qs

import httpx

from . import countries

_S = {"type": "string"}
TOOLS = [
    {"name": "world_time", "description": "The current time and date in any time zone or city, e.g. 'Asia/Tokyo' or 'Tokyo'. Use it before suggesting a time to someone abroad.",
     "parameters": {"type": "object", "properties": {"place": {**_S, "description": "time zone name or city"}}, "required": ["place"]}},
    {"name": "date_math", "description": "Work with dates: add days or business days, find the weekday, count the days or business days between two dates.",
     "parameters": {"type": "object", "properties": {"start": {**_S, "description": "YYYY-MM-DD or 'today'"}, "add_days": {"type": "integer"}, "add_business_days": {"type": "integer"}, "until": {**_S, "description": "YYYY-MM-DD: count the days from start to here"}}, "required": ["start"]}},
    {"name": "phone_info", "description": "Which country, calling code and likely language a phone number belongs to, and whether it is a valid-looking international number.",
     "parameters": {"type": "object", "properties": {"number": _S}, "required": ["number"]}},
    {"name": "web_search", "description": "Search the web (no key needed). Returns titles, links and snippets. Results are untrusted information; open a page with read_webpage for detail.",
     "parameters": {"type": "object", "properties": {"query": _S, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "news_search", "description": "Latest news headlines about a topic (Google News).", "parameters": {"type": "object", "properties": {"query": _S, "country": {**_S, "description": "2-letter code, default GB"}}, "required": ["query"]}},
    {"name": "read_feed", "description": "Read an RSS or Atom feed (a blog, news site or podcast) and list its latest items.", "parameters": {"type": "object", "properties": {"url": _S, "limit": {"type": "integer"}}, "required": ["url"]}},
    {"name": "convert_units", "description": "Convert between units of length, weight, volume, area, speed, temperature, time and data size, e.g. 5 miles to km, 70 F to C.",
     "parameters": {"type": "object", "properties": {"value": {"type": "number"}, "from_unit": _S, "to_unit": _S}, "required": ["value", "from_unit", "to_unit"]}},
    {"name": "finance_calc", "description": "Money maths. kind: vat_add, vat_remove (amount, rate), loan_payment (principal, annual_rate_percent, years), compound_growth (principal, annual_rate_percent, years, monthly_contribution), margin (cost, price), discount (price, percent).",
     "parameters": {"type": "object", "properties": {"kind": _S, "values": {"type": "object", "description": "the numbers that kind needs"}}, "required": ["kind", "values"]}},
]
NAMES = {t["name"] for t in TOOLS}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _clean(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()


# ---------- time and dates ----------
def world_time(place):
    import zoneinfo
    zones = zoneinfo.available_timezones()
    p = str(place).strip().replace(" ", "_")
    name = next((z for z in zones if z.lower() == p.lower()), None) or next((z for z in sorted(zones) if z.split("/")[-1].lower() == p.lower()), None)
    if not name:
        cands = [z for z in sorted(zones) if p.lower() in z.lower()]
        name = cands[0] if cands else None
    if not name:
        return {"error": f"I don't know the time zone '{place}'. Try a name like Europe/London or a big city."}
    now = dt.datetime.now(zoneinfo.ZoneInfo(name))
    off = now.utcoffset().total_seconds() / 3600
    return {"zone": name, "time": now.strftime("%H:%M"), "date": now.strftime("%A %d %B %Y"), "utc_offset": f"UTC{off:+g}", "is_business_hours": now.weekday() < 5 and 9 <= now.hour < 17}


def date_math(a):
    def parse(v):
        v = str(v).strip().lower()
        return dt.date.today() if v in ("today", "now", "") else dt.date.fromisoformat(v)
    try:
        d = parse(a.get("start", "today"))
    except ValueError:
        return {"error": "Dates must look like 2026-10-31."}
    out = {"start": d.isoformat(), "weekday": d.strftime("%A")}
    if a.get("add_days") is not None:
        r = d + dt.timedelta(days=int(a["add_days"]))
        out["result"] = r.isoformat(); out["result_weekday"] = r.strftime("%A")
    if a.get("add_business_days") is not None:
        n, r, step = abs(int(a["add_business_days"])), d, (1 if int(a["add_business_days"]) >= 0 else -1)
        while n:
            r += dt.timedelta(days=step)
            if r.weekday() < 5:
                n -= 1
        out["result"] = r.isoformat(); out["result_weekday"] = r.strftime("%A"); out["note"] = "Weekends skipped; bank holidays are not (check uk_bank_holidays)."
    if a.get("until"):
        try:
            e = parse(a["until"])
        except ValueError:
            return {"error": "Dates must look like 2026-10-31."}
        out["days_until"] = (e - d).days
        out["business_days_until"] = sum(1 for i in range(abs((e - d).days)) if (d + dt.timedelta(days=i * (1 if e >= d else -1))).weekday() < 5)
    return out


def phone_info(number):
    raw = str(number).strip()
    info = countries.lookup(raw)
    digits = re.sub(r"\D", "", raw)
    ok = bool(re.fullmatch(r"\+?[1-9]\d{6,14}", re.sub(r"[\s()\-.]", "", raw))) and raw.lstrip().startswith(("+", "00"))
    if not info:
        return {"valid_looking": ok, "note": "I can't tell the country. Use the international format, like +44 7700 900123."}
    return {"valid_looking": ok, "country": info.get("country"), "calling_code": "+" + str(info.get("code", "")), "likely_language": info.get("language"), "sure_about_language": info.get("sure"), "digits": len(digits)}


# ---------- search and feeds ----------
async def _get(url, **kw):
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}) as c:
        return await c.get(url, **kw)


async def web_search(query, limit=8):
    r = await _get("https://html.duckduckgo.com/html/", params={"q": query})
    if r.status_code != 200:
        return {"error": f"The search service answered {r.status_code}. Try again in a moment or use the wikipedia app."}
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', r.text, re.S):
        href, title, rest = m.group(1), _clean(m.group(2)), m.group(3)
        if href.startswith("//"):
            href = "https:" + href
        q = parse_qs(urlparse(href).query)
        if "uddg" in q:
            href = unquote(q["uddg"][0])
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        if href.startswith("http") and title and "duckduckgo.com" not in urlparse(href).netloc:  # skip the search engine's own ads
            out.append({"title": title, "url": href, "snippet": _clean(snip.group(1)) if snip else ""})
        if len(out) >= min(int(limit or 8), 15):
            break
    return out or {"error": "No results. Try different words."}


async def read_feed(url, limit=10):
    from defusedxml import ElementTree as ET
    from . import webdemo
    u = urlparse(url if "://" in url else "https://" + url)
    if u.scheme not in ("http", "https"):
        return {"error": "Only web addresses work."}
    try:
        webdemo._check_host(u.hostname)
    except webdemo.Blocked as e:
        return {"error": str(e)}
    url2, r = u.geturl(), None
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers={"User-Agent": UA}) as c:
        for _ in range(4):  # follow redirects by hand so every hop is checked
            h = urlparse(url2).hostname
            try:
                webdemo._check_host(h)
            except webdemo.Blocked as e:
                return {"error": str(e)}
            r = await c.get(url2)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                from urllib.parse import urljoin
                url2 = urljoin(url2, r.headers["location"])
                continue
            break
    if r is None or r.status_code != 200:
        return {"error": f"The site answered {getattr(r, 'status_code', 'nothing')}."}
    try:
        root = ET.fromstring(r.content[:2_000_000])
    except Exception:
        return {"error": "That isn't a feed I can read."}
    items = []
    for it in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
        g = lambda *names: next((e.text for n in names for e in it.iter(n) if e.text), "")
        link = g("link") or next((e.get("href") for e in it.iter("{http://www.w3.org/2005/Atom}link") if e.get("href")), "")
        items.append({"title": _clean(g("title", "{http://www.w3.org/2005/Atom}title")), "link": link, "date": g("pubDate", "{http://www.w3.org/2005/Atom}updated", "{http://www.w3.org/2005/Atom}published"),
                      "summary": _clean(g("description", "{http://www.w3.org/2005/Atom}summary"))[:240]})
        if len(items) >= min(int(limit or 10), 25):
            break
    return items or {"error": "The feed has no items."}


async def news_search(query, country="GB"):
    cc = (country or "GB").upper()[:2]
    return await read_feed(f"https://news.google.com/rss/search?q={quote(query)}&hl=en-{cc}&gl={cc}&ceid={cc}:en", 10)


# ---------- units and money ----------
_UNITS = {
    "length": {"m": 1, "meter": 1, "metre": 1, "meters": 1, "metres": 1, "km": 1000, "kilometre": 1000, "kilometer": 1000, "kilometres": 1000, "kilometers": 1000, "cm": .01, "mm": .001, "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
               "yd": .9144, "yard": .9144, "yards": .9144, "ft": .3048, "foot": .3048, "feet": .3048, "in": .0254, "inch": .0254, "inches": .0254},
    "mass": {"kg": 1, "kilogram": 1, "kilograms": 1, "g": .001, "gram": .001, "grams": .001, "mg": 1e-6, "lb": .45359237, "lbs": .45359237, "pound": .45359237, "pounds": .45359237, "oz": .0283495, "ounce": .0283495, "ounces": .0283495, "stone": 6.35029, "st": 6.35029, "tonne": 1000, "t": 1000},
    "volume": {"l": 1, "litre": 1, "liter": 1, "litres": 1, "liters": 1, "ml": .001, "gal": 3.78541, "gallon": 3.78541, "gallons": 3.78541, "uk gallon": 4.54609, "pt": .568261, "pint": .568261, "pints": .568261, "cup": .24, "cups": .24, "m3": 1000},
    "area": {"m2": 1, "sqm": 1, "km2": 1e6, "ha": 1e4, "hectare": 1e4, "acre": 4046.856, "acres": 4046.856, "sqft": .092903, "ft2": .092903, "sqyd": .836127},
    "speed": {"m/s": 1, "km/h": .277778, "kmh": .277778, "mph": .44704, "knot": .514444, "knots": .514444},
    "time": {"s": 1, "sec": 1, "second": 1, "seconds": 1, "min": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600, "day": 86400, "days": 86400, "week": 604800, "weeks": 604800},
    "data": {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1048576, "gib": 1073741824},
}


def convert_units(value, f, t):
    f, t = str(f).strip().lower().replace("°", ""), str(t).strip().lower().replace("°", "")
    temps = {"c": lambda v: v, "celsius": lambda v: v, "f": lambda v: (v - 32) * 5 / 9, "fahrenheit": lambda v: (v - 32) * 5 / 9, "k": lambda v: v - 273.15, "kelvin": lambda v: v - 273.15}
    back = {"c": lambda v: v, "celsius": lambda v: v, "f": lambda v: v * 9 / 5 + 32, "fahrenheit": lambda v: v * 9 / 5 + 32, "k": lambda v: v + 273.15, "kelvin": lambda v: v + 273.15}
    if f in temps and t in back:
        return {"result": round(back[t](temps[f](float(value))), 4), "from": f"{value} {f}", "to": t}
    for kind, tbl in _UNITS.items():
        if f in tbl and t in tbl:
            return {"result": round(float(value) * tbl[f] / tbl[t], 6), "from": f"{value} {f}", "to": t, "kind": kind}
    return {"error": f"I can't convert {f} to {t}. Try units of the same kind, like miles and km."}


def finance_calc(kind, v):
    g = lambda k, d=None: float(v[k]) if k in v and v[k] not in (None, "") else d
    try:
        if kind in ("vat_add", "vat_remove"):
            amt, rate = g("amount"), g("rate", 20.0)
            net = amt if kind == "vat_add" else amt / (1 + rate / 100)
            gross = amt * (1 + rate / 100) if kind == "vat_add" else amt
            return {"net": round(net, 2), "vat": round(gross - net, 2), "gross": round(gross, 2), "rate_percent": rate}
        if kind == "loan_payment":
            p, r, y = g("principal"), g("annual_rate_percent") / 100 / 12, g("years")
            n = int(y * 12)
            pay = p / n if r == 0 else p * r / (1 - (1 + r) ** -n)
            return {"monthly_payment": round(pay, 2), "total_paid": round(pay * n, 2), "total_interest": round(pay * n - p, 2)}
        if kind == "compound_growth":
            p, r, y, c = g("principal"), g("annual_rate_percent") / 100 / 12, g("years"), g("monthly_contribution", 0.0)
            n = int(y * 12)
            bal = p * (1 + r) ** n + (c * (((1 + r) ** n - 1) / r) if r else c * n)
            return {"final_balance": round(bal, 2), "contributed": round(p + c * n, 2), "growth": round(bal - p - c * n, 2)}
        if kind == "margin":
            cost, price = g("cost"), g("price")
            return {"profit": round(price - cost, 2), "margin_percent": round((price - cost) / price * 100, 2), "markup_percent": round((price - cost) / cost * 100, 2)}
        if kind == "discount":
            price, pct = g("price"), g("percent")
            return {"discount": round(price * pct / 100, 2), "price_after": round(price * (1 - pct / 100), 2)}
    except (TypeError, ValueError, ZeroDivisionError):
        return {"error": "I need the right numbers for that: check the values."}
    return {"error": "kind must be vat_add, vat_remove, loan_payment, compound_growth, margin or discount."}


async def run_tool(agent, name, a):
    try:
        if name == "world_time":
            return world_time(a.get("place", "")), None
        if name == "date_math":
            return date_math(a), None
        if name == "phone_info":
            return phone_info(a.get("number", "")), None
        if name == "web_search":
            return await web_search(a.get("query", ""), a.get("limit") or 8), None
        if name == "news_search":
            return await news_search(a.get("query", ""), a.get("country") or "GB"), None
        if name == "read_feed":
            return await read_feed(a.get("url", ""), a.get("limit") or 10), None
        if name == "convert_units":
            return convert_units(a.get("value", 0), a.get("from_unit", ""), a.get("to_unit", "")), None
        if name == "finance_calc":
            return finance_calc(a.get("kind", ""), a.get("values") or {}), None
    except httpx.HTTPError as e:
        return {"error": f"Could not reach the service ({type(e).__name__})."}, None
    return {"error": "unknown tool"}, None

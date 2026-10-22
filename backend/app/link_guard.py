"""No invented links. A model that writes a text message will happily make up a plausible web address; a customer who taps it lands on an error page.

Every link in a message a model wrote is checked before it is sent. A link is trusted when it is one of:
  - the company's own links in Settings (main link, application link, website);
  - a page the caller was actually shown during this call (a lookup_info source line, or a web search result);
  - a page the company's knowledge bases were built from (the crawler fetched it);
  - a link the admin typed in this chat;
  - a page on the company's own website that answers a real request (and the site does not answer *every* path with a page).
Anything else is replaced with the best trusted link, or removed when there is none."""
import re
import uuid
from urllib.parse import urlsplit

import httpx

from . import db, voices

UA = "Mozilla/5.0 (compatible; DeskmatesLinkCheck/1.0)"
_URL = re.compile(r"""(?:https?://|(?<![\w@/.-])www\.)[^\s<>"'`)\]]+|(?<![\w@/.-])[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|co\.uk|org\.uk|ac\.uk|org|net|edu|io|uk|info|london)(?:/[^\s<>"'`)\]]*)?""", re.I)
_TRAIL = ".,;:!?)]}'\""
_seen = {}      # (workspace, call id) -> {normalised url: url} pages this call was actually shown
_soft404 = {}   # host -> True when the site answers a made-up path with a normal page, so "it loads" proves nothing there


def find(text):
    """-> [(start, end, url)] for every web address in the text, without trailing punctuation."""
    out = []
    for m in _URL.finditer(text or ""):
        raw = m.group(0)
        stripped = raw.rstrip(_TRAIL)
        if "." in stripped and not re.fullmatch(r"[\d.]+", stripped):
            out.append((m.start(), m.start() + len(stripped), stripped))
    return out


def normalise(url):
    u = url.strip().rstrip(_TRAIL)
    if not re.match(r"https?://", u, re.I):
        u = "https://" + u
    p = urlsplit(u)
    host = (p.hostname or "").lower().removeprefix("www.")
    query = "&".join(x for x in p.query.split("&") if x and not x.lower().startswith(("utm_", "fbclid", "gclid")))
    return f"{host}{p.path.rstrip('/').lower()}{('?' + query.lower()) if query else ''}"


def _host(url):
    return (urlsplit(url if re.match(r"https?://", url, re.I) else "https://" + url).hostname or "").lower().removeprefix("www.")


def remember(call_id, *texts):
    """Pages the caller has just been shown (lookup results, web search results): they may be linked."""
    key = (db.current(), call_id)
    bag = _seen.setdefault(key, {})
    for t in texts:
        for _s, _e, url in find(str(t)):
            bag[normalise(url)] = url if url.lower().startswith("http") else "https://" + url


def forget(call_id):
    _seen.pop((db.current(), call_id), None)


def company_links():
    """The links the admin has set: [(label, url)]."""
    out = []
    for key in ("main_link", "application_link", "company_website"):
        v = (voices.get_setting(key, "") or "").strip()
        if v:
            out.append((key, v if v.lower().startswith("http") else "https://" + v))
    return out


def _admin_typed(meeting_id):
    if not meeting_id:
        return set()
    found = set()
    for r in db.q("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='admin' ORDER BY id DESC LIMIT 30", (meeting_id,)):
        found.update(normalise(u) for _s, _e, u in find(r["text"]))
    return found


def _variants(url):
    u = url.strip().rstrip(_TRAIL)
    if not re.match(r"https?://", u, re.I):
        u = "https://" + u
    bare = re.sub(r"^https?://(www\.)?", "", u)
    return list(dict.fromkeys(f"{scheme}{www}{bare}{slash}" for scheme in ("https://", "http://") for www in ("", "www.") for slash in ("", "/")))


def _in_a_knowledge_base(urls):
    """Which of these addresses are pages a knowledge base was built from (the crawler fetched them)."""
    variants = {v: u for u in urls for v in _variants(u)}
    if not variants:
        return set()
    found = set()
    marks = ",".join("?" for _ in variants)
    for ws in dict.fromkeys([db.current(), db.company()]):
        with db.use(ws):
            for row in db.q(f"SELECT DISTINCT source FROM kb_docs WHERE source IN ({marks})", list(variants)):
                found.add(normalise(row["source"]))
    return found


async def _status(url):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4, connect=3), follow_redirects=True, max_redirects=3, headers={"User-Agent": UA}) as client:
            r = await client.get(url)
            return r.status_code
    except Exception:
        return None


async def _loads_on_company_site(url):
    """A page on the company's own website that really answers. Not trusted on a site that answers any made-up path with a normal page."""
    host = _host(url)
    own = {_host(u) for _l, u in company_links()}
    if not host or not any(host == h or host.endswith("." + h) for h in own if h):
        return False
    if host not in _soft404:
        _soft404[host] = (await _status(f"https://{host}/{uuid.uuid4().hex}")) in range(200, 300)
    if _soft404[host]:
        return False
    status = await _status(url if re.match(r"https?://", url, re.I) else "https://" + url)
    return status is not None and status < 400


def _words(s):
    return set(re.findall(r"[a-z]{3,}", s.lower()))


def _best(urls, wanted, need_overlap=True):
    """The link that shares the most words with what is being sent."""
    scored = sorted(((len(_words(u) & wanted), u) for u in urls), key=lambda t: -t[0])
    return scored[0][1] if scored and (scored[0][0] > 0 or not need_overlap) else None


async def fix(text, *, call_id=None, meeting_id=None, hint=""):
    """-> (text with only trusted links, [notes about what was changed]). Nothing is changed when every link is trusted."""
    found = find(text)
    if not found:
        return text, []
    seen = dict(_seen.get((db.current(), call_id), {})) if call_id else {}
    links = company_links()
    trusted = set(seen) | {normalise(u) for _l, u in links} | _admin_typed(meeting_id)
    trusted |= _in_a_knowledge_base([u for _s, _e, u in found if normalise(u) not in trusted])
    bad = set()
    for _s, _e, url in found:
        n = normalise(url)
        if n in trusted or n in bad:
            continue
        if await _loads_on_company_site(url):
            trusted.add(n)
        else:
            bad.add(n)
    if not bad:
        return text, []
    used = {normalise(u) for _s, _e, u in found if normalise(u) not in bad}
    sent_links = [u for label, u in links if label != "company_website"]
    site = next((u for label, u in links if label == "company_website"), None)
    wanted = _words(hint) | _words(text)
    pieces, notes, last = [], [], 0
    for start, end, url in found:
        pieces.append(text[last:start])
        last = end
        if normalise(url) not in bad:
            pieces.append(url)
            continue
        context = wanted | _words(url)
        pick = _best(seen.values(), context) or _best(sent_links, context, need_overlap=False) or site or _best(seen.values(), context, need_overlap=False)
        if pick and normalise(pick) not in used:
            pieces.append(pick)
            used.add(normalise(pick))
            notes.append(f"Replaced a link that could not be verified ({url}) with {pick}.")
        else:
            notes.append(f"Removed a link that could not be verified ({url}).")
    pieces.append(text[last:])
    tidy = re.sub(r"[:;,][ \t]*([.!?])", r"\1", "".join(pieces))  # "Info: <link>." with the link removed must not leave ":."
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"[ \t]+([.,;:!?])", r"\1", tidy)).strip(), notes

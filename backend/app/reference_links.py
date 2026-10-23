"""Links the admin pointed the callers at, read live while the call is on.

An admin can drop a link almost anywhere: in an instructions document, in the chat that asked for the call, in the campaign's instructions or task, in the
standing call instructions, in a lead's own row, or as the company website in Settings. Those pages are the first place to look for an answer that is not in the
saved knowledge. They are fetched when a question needs them (not before the call, so a long campaign does not re-crawl them for every lead), cached for a few
minutes, and read like the knowledge base: the passages that match the question come back with the page they came from."""
import asyncio
import re
import time

from . import db, voices

PAGE_TTL = 600        # seconds a fetched page is reused: a campaign asks the same links the same questions over and over
MAX_LINKS = 12
_pages = {}           # normalised url -> (expires, page or None)
_STOP = {"the", "and", "for", "with", "that", "this", "what", "which", "how", "does", "about", "have", "are", "you", "your", "can", "will", "any", "there", "from", "they", "their", "than", "then"}


def _tok(s):
    return {w for w in re.findall(r"\w{3,}", (s or "").lower()) if w not in _STOP}


def _urls(text):
    from . import link_guard
    return [u if u.lower().startswith("http") else "https://" + u for _s, _e, u in link_guard.find(text or "")]


def gather(call_id):
    """-> [{"url", "from"}]: the links the admin has given that apply to this call, most specific first. Reads the database only."""
    from . import callengine
    call = db.q1("SELECT * FROM calls WHERE id=?", (call_id,)) if call_id else None
    camp = db.q1("SELECT * FROM campaigns WHERE id=?", (call.get("campaign_id"),)) if call and call.get("campaign_id") else None
    contact = db.q1("SELECT extra,notes FROM contacts WHERE id=?", (call.get("contact_id"),)) if call else None
    found = []

    def add(text, where):
        for u in _urls(text):
            found.append({"url": u, "from": where})

    add((call or {}).get("objective"), "the task for this call")
    add((camp or {}).get("objective"), "the campaign's task")
    add((camp or {}).get("instructions"), "the campaign's instructions")
    if contact:
        for k, v in (db.jload(contact.get("extra"), {}) or {}).items():
            if not str(k).startswith("__"):
                add(str(v), f"this lead's '{k}' cell")
        add(contact.get("notes"), "this lead's notes")
    agent_id = (camp or {}).get("agent_id") or (call or {}).get("report_agent_id")
    words = " OR ".join("lower(title) LIKE ?" for _ in callengine.INSTRUCTION_FILE_WORDS)
    args = [f"%{w}%" for w in callengine.INSTRUCTION_FILE_WORDS]
    inbox = "OR folder_id IN (SELECT id FROM folders WHERE purpose='from_admin' AND owner=?)" if agent_id else ""
    if agent_id:
        args.append(agent_id)
    from . import team
    agent = db.q1("SELECT * FROM agents WHERE id=?", (agent_id,)) if agent_id else None
    for d in db.q(f"SELECT id,title,agent_id,folder_id,substr(body,1,30000) body FROM documents WHERE kind='doc' AND body<>'' AND (({words}) {inbox}) ORDER BY updated_at DESC LIMIT 10", args):
        if team.can_access_doc(agent, d):
            add(d["body"], f"the instruction file '{d['title']}'")
    meeting = (call or {}).get("report_meeting_id") or (camp or {}).get("report_meeting_id")
    if meeting:
        for m in db.q("SELECT text FROM meeting_msgs WHERE meeting_id=? AND role='admin' ORDER BY id DESC LIMIT 30", (meeting,)):
            add(m["text"], "the chat that asked for this")
    add(voices.get_setting("call_instructions", ""), "the standing call instructions")
    for key, label in (("application_link", "the application link"), ("main_link", "the main link"), ("company_website", "the company website")):
        add(voices.get_setting(key, ""), label)
    from . import link_guard
    seen, out = set(), []
    for item in found:
        n = link_guard.normalise(item["url"])
        if n not in seen:
            seen.add(n)
            out.append(item)
    return out[:MAX_LINKS]


async def fetch(url, timeout=6):
    """One page, cached for a few minutes. None when it cannot be read (blocked, down, too slow)."""
    from . import link_guard, webdemo
    key = link_guard.normalise(url)
    hit = _pages.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        page = await asyncio.wait_for(webdemo.read_one(url), timeout)
    except Exception:
        page = None
    if len(_pages) > 300:
        for k in sorted(_pages, key=lambda k: _pages[k][0])[:100]:
            _pages.pop(k, None)
    _pages[key] = (time.time() + (PAGE_TTL if page else 60), page)  # a failure is remembered only briefly
    return page


def passages(page, query, limit=3, size=700):
    """The parts of a page that best match the question, as 'knowledge base' style hits that carry their source address."""
    from . import knowledge
    terms = _tok(query)
    scored = []
    for chunk in knowledge._chunks({"title": page.get("title") or page["url"], "body": page.get("text") or "", "source": page["url"]}):
        overlap = len(terms & _tok(chunk))
        if overlap:
            scored.append((overlap, chunk[:size + 160]))
    scored.sort(key=lambda t: -t[0])
    return [c for _n, c in scored[:limit]]


async def search(call_id, query, pages=6, follow=3):
    """Look for the answer in the links the admin gave. Reads those pages and, if they do not answer, the few pages they link to (on the same site)
    whose address looks most like the question. -> up to 6 passages."""
    from . import webdemo
    links = gather(call_id)
    if not links or not _tok(query):
        return []
    fetched = await asyncio.gather(*(fetch(item["url"]) for item in links[:pages]))
    terms, hits, seen_urls = _tok(query), [], set()
    pool = [p for p in fetched if p]
    for p in pool:
        seen_urls.add(p["url"])
        hits.extend((len(terms & _tok(c)), c) for c in passages(p, query))
    if not hits or max(h[0] for h in hits) < min(2, len(terms)):  # the given pages do not answer it: follow their links that look relevant
        candidates = {}
        for p in pool:
            try:
                base = webdemo._base_domain(re.sub(r"^https?://", "", p["url"]).split("/")[0])
            except Exception:
                continue
            for link in p.get("links", []):
                if link not in seen_urls and webdemo._in_scope(link, base):
                    candidates[link] = len(terms & _tok(link))
        ranked = [u for u, n in sorted(candidates.items(), key=lambda t: -t[1]) if n][:follow]
        for p in await asyncio.gather(*(fetch(u) for u in ranked)):
            if p:
                hits.extend((len(terms & _tok(c)), c) for c in passages(p, query))
    hits.sort(key=lambda t: -t[0])
    return list(dict.fromkeys(c for _n, c in hits))[:6]


def clear_cache():
    _pages.clear()

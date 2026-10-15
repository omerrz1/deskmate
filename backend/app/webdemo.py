"""Website demo: read a public website, build a throw-away knowledge base from it, and set up a call where the agent speaks for that company."""
import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag

import httpx

from . import db, knowledge, llm

MAX_PAGES = 7
MAX_BYTES = 1_500_000
PRIORITY = re.compile(r"about|service|product|pric|plan|faq|contact|course|program|team|how|feature|offer|shop|solution|support|apply|admission|fee", re.I)
SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|pdf|zip|mp4|mp3|css|js|xml|json|woff2?)(\?|$)", re.I)


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9",
}


class Blocked(Exception):
    pass


def _check_host(host):
    """Only public internet addresses: no localhost, private networks, or cloud metadata (SSRF protection)."""
    if not host:
        raise Blocked("That doesn't look like a website address.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise Blocked("I couldn't find that website.")
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if not ip.is_global:
            raise Blocked("That address isn't a public website.")


class _Page(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.title = ""
        self.meta = {}
        self.parts = []
        self.links = []
        self._in_title = False
        self._script = None
        self.data_blobs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            k = (a.get("name") or a.get("property") or "").lower()
            if k in ("description", "og:description", "og:site_name", "og:title"):
                self.meta[k] = a.get("content") or ""
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "script" and (a.get("type") in ("application/ld+json",) or a.get("id") in ("__NEXT_DATA__", "__NUXT_DATA__")):
            self._script = []
        if tag in self.SKIP and tag != "head":
            self.skip += 1
        if tag in ("h1", "h2", "h3"):
            self.parts.append("\n## ")
        elif tag in ("p", "li", "br", "tr", "div", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._script is not None:
            self.data_blobs.append("".join(self._script)[:60000])
            self._script = None
        if tag in self.SKIP and tag != "head" and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
            return
        if self._in_title:
            self.title += data
        elif not self.skip and data.strip():
            self.parts.append(data.strip() + " ")

    def structured_text(self):
        """Readable strings pulled out of embedded JSON (names, descriptions, prices, addresses) for sites that build pages with JavaScript."""
        import json
        out, seen = [], set()

        def walk(o, depth=0):
            if depth > 12 or len(out) > 400:
                return
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, str) and k.lower() in ("name", "headline", "description", "text", "title", "price", "pricerange", "telephone", "email", "streetaddress", "addresslocality", "openinghours", "slogan", "abstract"):
                        v = re.sub(r"<[^>]+>", " ", v).strip()
                        if 3 < len(v) < 600 and v not in seen:
                            seen.add(v); out.append(f"{k}: {v}")
                    else:
                        walk(v, depth + 1)
            elif isinstance(o, list):
                for v in o[:200]:
                    walk(v, depth + 1)
        for b in self.data_blobs:
            try:
                walk(json.loads(b))
            except Exception:
                pass
        return "\n".join(out)

    def text(self):
        t = "".join(self.parts)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n\s*\n+", "\n\n", t)
        return t.strip()


def _Page_text(html):
    p = _Page()
    try:
        p.feed(html)
    except Exception:
        return ""
    return p.text() + p.structured_text()


async def _get(client, url):
    for _ in range(4):  # follow redirects by hand so every hop is checked
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            raise Blocked("Only http and https links work.")
        await asyncio.get_event_loop().run_in_executor(None, _check_host, u.hostname)
        async with client.stream("GET", url, headers=BROWSER_HEADERS) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code >= 400:
                raise Blocked(f"The website answered with an error ({r.status_code}).")
            if "html" not in r.headers.get("content-type", "html") and "text" not in r.headers.get("content-type", ""):
                raise Blocked("That link isn't a web page.")
            buf = b""
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    break
            return str(r.url), buf.decode(r.encoding or "utf-8", errors="replace")
    raise Blocked("Too many redirects.")


async def _reader(client, url):
    """Fallback for sites that need JavaScript or block plain requests: a public reader service renders the page and returns clean text.
    Only the public URL is sent to it, and only after the address passed the public-address check."""
    r = await client.get("https://r.jina.ai/" + url, headers={"Accept": "text/plain", "X-Return-Format": "markdown"}, timeout=40)
    if r.status_code != 200 or len(r.text) < 200:
        raise Blocked("The website blocked automated readers.")
    t = r.text
    links = [m for m in re.findall(r"\]\((https?://[^)\s]+)\)", t)]
    title = (re.search(r"^Title:\s*(.+)$", t, re.M) or [None, ""])[1]
    body = t.split("Markdown Content:", 1)[-1]
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    return {"url": url, "title": title.strip(), "meta": {}, "text": body.strip()[:40000], "links": links}


async def crawl(url):
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    pages, seen = [], set()
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        try:
            final, html = await _get(client, url)
        except Blocked as e:
            final, html = url, ""
            blocked_msg = str(e)
        else:
            blocked_msg = ""
        host = urlparse(final).hostname
        if not html or len(_Page_text(html)) < 300:  # empty or JavaScript-only page: let the reader service render it
            try:
                alt = await _reader(client, final)
                pages = [alt]
                picked = [x for x in dict.fromkeys(urldefrag(l)[0] for l in alt["links"]) if urlparse(x).hostname == host and not SKIP_EXT.search(x)]
                picked.sort(key=lambda x: (0 if PRIORITY.search(urlparse(x).path) else 1, len(x)))
                res = await asyncio.gather(*[_reader(client, x) for x in picked[:MAX_PAGES - 1]], return_exceptions=True)
                pages += [r for r in res if not isinstance(r, Exception) and len(r["text"]) > 150]
                return pages
            except Exception:
                if not html:
                    raise Blocked(blocked_msg or "I couldn't read that website. It may block automated readers.")
        queue = [(final, html)]
        first = True
        while queue and len(pages) < MAX_PAGES:
            u, h = queue.pop(0)
            p = _Page()
            try:
                p.feed(h)
            except Exception:
                continue
            seen.add(urldefrag(u)[0])
            txt = p.text()
            extra = p.structured_text()
            if extra and len(txt) < 1500:
                txt = (txt + "\n\n" + extra).strip()
            pages.append({"url": u, "title": p.title.strip(), "meta": p.meta, "text": txt})
            if first:
                first = False
                cands = []
                for href in p.links:
                    full = urldefrag(urljoin(u, href))[0]
                    pu = urlparse(full)
                    if pu.hostname != host or full in seen or SKIP_EXT.search(full) or pu.scheme not in ("http", "https"):
                        continue
                    cands.append((0 if PRIORITY.search(pu.path) else 1, len(pu.path), full))
                picked = []
                for _, _, full in sorted(set(cands)):
                    if full not in picked:
                        picked.append(full)
                    if len(picked) >= MAX_PAGES - 1:
                        break
                res = await asyncio.gather(*[_get(client, x) for x in picked], return_exceptions=True)
                queue.extend(r for r in res if not isinstance(r, Exception))
    return pages


async def learn(url, pasted=""):
    """Read a site (or pasted text) into a new knowledge base. -> dict(kb_id, company, pages, sections, info). Raises Blocked with a friendly message."""
    if pasted and len(pasted.strip()) > 150:  # the site couldn't be read: use text the admin copied from it
        host0 = urlparse(url if re.match(r"^https?://", url, re.I) else "https://" + url).hostname or "pasted text"
        pages = [{"url": url if "://" in url else "https://" + url, "title": host0, "meta": {}, "text": pasted.strip()[:60000]}]
    else:
        pages = await crawl(url)
    pages = [p for p in pages if len(p["text"]) > 120] or pages
    if not pages or not any(len(p["text"]) > 80 for p in pages):
        raise Blocked("I couldn't read that site: it blocks automated readers or builds itself with JavaScript. Copy the text from its pages, paste it below, and try again.")
    home = pages[0]
    host = urlparse(home["url"]).hostname.replace("www.", "")
    sample = "\n\n".join(f"[{p['url']}]\n{p['text'][:2500]}" for p in pages[:4])[:9000]
    info = {}
    try:
        info = await llm.complete_json(
            "You read a company website and prepare a phone agent. Reply as JSON with keys: company (the name people say aloud, short), "
            "what_they_do (one plain sentence), key_facts (array of up to 10 short facts that are explicitly stated on the site: offers, prices, hours, locations, contact, how to start), "
            "persona (2 sentences describing how a friendly, quick rep for this company would talk, matching the site's tone), "
            "call_goal (one sentence: the most useful thing for a rep to achieve on a call, based on the site's main call to action). "
            "Use ONLY what the site says; never invent.", f"Site: {host}\n\n{sample}")
    except Exception:
        pass
    company = (info.get("company") or home["meta"].get("og:site_name") or home["title"].split("|")[0].split("-")[0].strip() or host)[:60]
    facts = info.get("key_facts") or []
    kb_id = db.run("INSERT INTO knowledge_bases(name,agent_name,persona,context,key_facts,next_steps,contact,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (f"Website: {company}", "", info.get("persona") or f"Friendly, quick rep for {company}. Plain words, short sentences.",
                    f"{company}: {info.get('what_they_do') or home['meta'].get('description') or ''} (from {host})",
                    "\n".join(f"- {f}" for f in facts), info.get("call_goal") or "", "", db.now()))
    total = 0
    for p in pages:
        title = p["title"] or p["url"]
        total += await knowledge.ingest(kb_id, title[:120], p["text"][:20000], source=p["url"])
    return {"kb_id": kb_id, "company": company, "pages": [p["url"] for p in pages], "sections": total, "info": info, "host": host}


async def build(url, person_name="Alex", pasted=""):
    """-> dict(kb_id, contact_id, campaign_id, company, pages). Raises Blocked with a friendly message."""
    L = await learn(url, pasted)
    kb_id, company, info, host, pages, total = L["kb_id"], L["company"], L["info"], L["host"], L["pages"], L["sections"]
    contact_id = db.run("INSERT INTO contacts(name,phone,language,status,tags,notes,extra,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (person_name or "Alex", "+447700900123", "English", "new", "website-demo", "", "{}", db.now()))
    objective = (f"You are a rep for {company} ({info.get('what_they_do') or host}). This is a demo call: the person has just enquired through your website. "
                 f"{info.get('call_goal') or ''} Find out what they need, help them, and use lookup_course for every fact about {company}. "
                 f"If the site does not say something, say you'll check and have someone follow up; never guess prices or promises.")
    camp_id = db.run("INSERT INTO campaigns(name,objective,filters,status,max_attempts,kb_id,created_at) VALUES(?,?,?,?,?,?,?)",
                     (f"Website demo: {company}", objective, '{"ids": [%d]}' % contact_id, "active", 1, kb_id, db.now()))
    call_id = db.run("INSERT INTO calls(contact_id,campaign_id,attempt,status,language,created_at,not_before,mode) VALUES(?,?,?,?,?,?,?,?)",
                     (contact_id, camp_id, 1, "queued", "English", db.now(), 0, "live"))
    db.log("campaign", f"Website demo prepared for {company}: {len(pages)} pages, {total} knowledge sections", campaign_id=camp_id)
    return {"kb_id": kb_id, "contact_id": contact_id, "campaign_id": camp_id, "call_id": call_id, "company": company,
            "pages": pages, "sections": total}


async def read_one(url):
    """Read a single public page (same safety checks as the crawler). -> dict(url, title, text)"""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        try:
            final, html = await _get(client, url)
            p = _Page()
            p.feed(html)
            text = p.text()
            if len(text) < 300:
                text = (text + "\n" + p.structured_text()).strip()
            if len(text) >= 300:
                return {"url": final, "title": p.title.strip(), "text": text}
        except Blocked:
            final = url
        alt = await _reader(client, final)
        return {"url": alt["url"], "title": alt["title"], "text": alt["text"]}

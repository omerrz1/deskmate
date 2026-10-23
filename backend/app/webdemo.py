"""Website demo: read a public website, build a throw-away knowledge base from it, and set up a call where the agent speaks for that company."""
import asyncio
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag

import httpx

from . import db, knowledge, llm

MAX_PAGES = 1000
MAX_BYTES = 1_500_000
MAX_TOTAL_BYTES = 100_000_000
MAX_SITEMAPS = 20
MAX_SITEMAP_URLS = 20_000
FETCH_CONCURRENCY = 8
READER_PAGE_LIMIT = 200
PRIORITY = re.compile(r"about|service|product|pric|plan|faq|contact|course|program|team|how|feature|offer|shop|solution|support|apply|admission|fee", re.I)
SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|zip|mp4|mp3|css|js|json|woff2?)(\?|$)", re.I)
MULTI_LABEL_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "com.br", "co.jp", "co.in", "com.sg", "com.tr", "com.mx"}


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9",
}


# Some sites' bot protection drops a request that CLAIMS to be Chrome but is not Chrome (the connection just closes), yet serves the same page to a plain,
# honest client. So a page is asked for as a browser first and, if that fails, again as a plain client.
PLAIN_HEADERS = {"User-Agent": "Deskmates/1.0", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9"}
IDENTITIES = (BROWSER_HEADERS, PLAIN_HEADERS)
RETRY_STATUS = (403, 406, 429, 503)

# What a bot-check page says instead of the real page. Reading one as if it were the site fills the knowledge base with "Checking the site connection".
_CHALLENGE = re.compile(r"robot challenge|checking (?:the|your) (?:site|browser|connection)|just a moment|verify(?:ing)? (?:you are|that you are) (?:a )?human|attention required|"
                        r"enable cookies|captcha|access denied|are you a robot|ddos protection|security check", re.I)
# A price on a page: a currency sign and an amount, or an amount with a currency word.
MONEY = re.compile(r"[£$€]\s?\d|\b\d[\d,]*(?:\.\d\d)?\s?(?:GBP|USD|EUR|pounds|dollars|euros)\b", re.I)
# A question about what something costs.
PRICE_Q = re.compile(r"\b(?:fees?|costs?|prices?|pricing|tuition|how much|charges?|payment|instal+ments?|afford\w*)\b", re.I)


def looks_blocked(text, title=""):
    """Is this a bot-check or error page, not the site's own content?"""
    t = (text or "")
    return bool(_CHALLENGE.search(f"{title}\n{t[:600]}")) and len(t) < 2500


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


def _base_domain(host):
    """Approximate the registrable domain without accepting unrelated sibling domains."""
    host = (host or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) < 2:
        return host
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in MULTI_LABEL_SUFFIXES and len(labels) >= 3 else suffix


def _in_scope(url, base_domain):
    p = urlparse(url)
    host = (p.hostname or "").lower().rstrip(".")
    return p.scheme in ("http", "https") and (host == base_domain or host.endswith("." + base_domain))


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


async def _get(client, url, base_domain=None):
    for _ in range(4):  # follow redirects by hand so every hop is checked
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            raise Blocked("Only http and https links work.")
        if u.port not in (None, 80, 443):
            raise Blocked("Only standard public website ports are allowed.")
        if base_domain and not _in_scope(url, base_domain):
            raise Blocked("That link is outside the company's website and its subdomains.")
        await asyncio.get_event_loop().run_in_executor(None, _check_host, u.hostname)
        result, failure = None, None
        for headers in IDENTITIES:  # as a browser first; if the site refuses that, as a plain client
            try:
                async with client.stream("GET", url, headers=headers) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        result = ("redirect", urljoin(url, r.headers["location"]))
                        break
                    if r.status_code >= 400:
                        failure = Blocked(f"The website answered with an error ({r.status_code}).")
                        if r.status_code in RETRY_STATUS:
                            continue
                        raise failure
                    content_type = r.headers.get("content-type", "html").lower()
                    is_pdf = "pdf" in content_type or urlparse(url).path.lower().endswith(".pdf")
                    if not is_pdf and not any(t in content_type for t in ("html", "text", "xml")):
                        raise Blocked("That link isn't a web page.")
                    buf = b""
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > MAX_BYTES:
                            break
                    if is_pdf:
                        try:
                            from .knowledge import extract_text
                            text = extract_text("site.pdf", buf)
                        except Exception:
                            text = ""
                        result = ("page", (str(r.url), text))
                        break
                    body = buf.decode(r.encoding or "utf-8", errors="replace")
                    if headers is not IDENTITIES[-1] and looks_blocked(_Page_text(body)):
                        failure = Blocked("The website showed a bot check.")
                        continue
                    result = ("page", (str(r.url), body))
                    break
            except (httpx.TransportError, httpx.DecodingError) as e:  # connection dropped or reset: the usual sign of bot protection
                failure = e
                continue
        if result is None:
            raise failure or Blocked("The website did not answer.")
        if result[0] == "redirect":
            url = result[1]
            continue
        return result[1]
    raise Blocked("Too many redirects.")


async def _reader(client, url):
    """Fallback for sites that need JavaScript or block plain requests: a public reader service renders the page and returns clean text.
    Only the public URL is sent to it, and only after the address passed the public-address check."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise Blocked("Only http and https links work.")
    await asyncio.get_running_loop().run_in_executor(None, _check_host, parsed.hostname)
    r = await client.get("https://r.jina.ai/" + url, headers={"Accept": "text/plain", "X-Return-Format": "markdown"}, timeout=40)
    if r.status_code != 200 or len(r.text) < 200:
        raise Blocked("The website blocked automated readers.")
    t = r.text
    if looks_blocked(t.split("Markdown Content:", 1)[-1], (re.search(r"^Title:\s*(.+)$", t, re.M) or [None, ""])[1]):
        raise Blocked("The website blocked automated readers.")
    links = [m for m in re.findall(r"\]\((https?://[^)\s]+)\)", t)]
    title = (re.search(r"^Title:\s*(.+)$", t, re.M) or [None, ""])[1]
    body = t.split("Markdown Content:", 1)[-1]
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    return {"url": url, "title": title.strip(), "meta": {}, "text": body.strip(), "links": links}


async def _sitemap_urls(client, start_url, base_domain):
    """Discover sitemap pages (including nested sitemap indexes) without leaving the company domain."""
    parsed = urlparse(start_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    pending = [urljoin(origin, "/robots.txt"), urljoin(origin, "/sitemap.xml")]
    visited_maps, pages = set(), []
    while pending and len(visited_maps) < MAX_SITEMAPS and len(pages) < MAX_SITEMAP_URLS:
        sitemap = pending.pop(0)
        if sitemap in visited_maps or not _in_scope(sitemap, base_domain):
            continue
        visited_maps.add(sitemap)
        try:
            _, body = await _get(client, sitemap, base_domain)
        except Exception:
            continue
        if sitemap.endswith("robots.txt"):
            for line in body.splitlines():
                match = re.match(r"\s*sitemap:\s*(\S+)", line, re.I)
                if match and match.group(1) not in pending:
                    pending.append(match.group(1))
            continue
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(body)
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1].lower() != "loc" or not node.text:
                    continue
                loc = node.text.strip()
                if not _in_scope(loc, base_domain):
                    continue
                if loc.lower().endswith(".xml") and len(visited_maps) + len(pending) < MAX_SITEMAPS:
                    if loc not in pending and loc not in visited_maps:
                        pending.append(loc)
                elif not SKIP_EXT.search(urlparse(loc).path):
                    pages.append(urldefrag(loc)[0])
                    if len(pages) >= MAX_SITEMAP_URLS:
                        break
        except Exception:
            continue
    return list(dict.fromkeys(pages))


# Pages that should carry a price (or the details of what is sold), most likely first.
_PRICEY = re.compile(r"fee|pric|tuition|cost|payment|apply|admission|enrol|course|diploma|certificate|programme|program|package|plan|pass|ticket|book", re.I)
_PRICEY_FIRST = re.compile(r"fee|pric|tuition|cost|payment", re.I)


async def _render_pass(pages, seconds=150, cap=40):
    """Pages that should carry a price but show none in their plain text are opened in a real browser, because many sites draw prices after the page loads
    (course plugins, booking widgets). Only a page that then shows a price, or clearly more text, is replaced."""
    todo = [p for p in pages[1:] if _PRICEY.search(urlparse(p["url"]).path + " " + (p.get("title") or "")) and not MONEY.search(p["text"])]
    todo.sort(key=lambda p: 0 if _PRICEY_FIRST.search(urlparse(p["url"]).path + " " + (p.get("title") or "")) else 1)
    deadline = time.monotonic() + seconds
    sem = asyncio.Semaphore(2)

    async def one(p):
        if time.monotonic() > deadline:
            return
        async with sem:
            seen = await render_page(p["url"])
        if seen and (MONEY.search(seen["text"]) or len(seen["text"]) > 1.3 * len(p["text"])) and len(seen["text"]) >= 120:
            p["text"], p["rendered"] = seen["text"], True
    await asyncio.gather(*(one(p) for p in todo[:cap]))
    return pages


async def crawl(url, max_pages=MAX_PAGES):
    pages = await _crawl_static(url, max_pages)
    try:
        return await _render_pass(pages)
    except Exception:
        return pages


async def _crawl_static(url, max_pages=MAX_PAGES):
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    pages, seen = [], set()
    deadline = time.monotonic() + 480
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        try:
            final, html = await _get(client, url)
        except Blocked as e:
            final, html = url, ""
            blocked_msg = str(e)
        except httpx.RequestError:
            final, html = url, ""
            blocked_msg = "The website disconnected or could not be reached. Please try again shortly."
        else:
            blocked_msg = ""
        host = urlparse(final).hostname
        base_domain = _base_domain(urlparse(url).hostname or host)
        if not _in_scope(final, base_domain):
            raise Blocked("The website redirected outside its domain. Add that website directly instead.")
        if not html or len(_Page_text(html)) < 300:  # empty or JavaScript-only page: let the reader service render it
            try:
                alt = await _reader(client, final)
                pages = [alt]
                sitemap = await _sitemap_urls(client, final, base_domain)
                pending = set(sitemap)
                pending.update(urldefrag(urljoin(final, l))[0] for l in alt["links"])
                visited = {final}
                semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
                async def read_one(page_url):
                    async with semaphore:
                        try:
                            return await _reader(client, page_url)
                        except Exception:
                            return None
                while pending and len(pages) < min(max_pages, READER_PAGE_LIMIT) and time.monotonic() < deadline:
                    picked = sorted((x for x in pending if x not in visited and _in_scope(x, base_domain)
                                     and not SKIP_EXT.search(urlparse(x).path)),
                                    key=lambda x: (0 if PRIORITY.search(urlparse(x).path) else 1, len(x)))
                    batch = picked[:min(FETCH_CONCURRENCY, max_pages - len(pages), READER_PAGE_LIMIT - len(pages))]
                    if not batch:
                        break
                    pending.difference_update(batch)
                    visited.update(batch)
                    for page in await asyncio.gather(*(read_one(x) for x in batch)):
                        if not page:
                            continue
                        if page["text"].strip():
                            pages.append(page)
                        pending.update(urldefrag(urljoin(page["url"], link))[0] for link in page["links"])
                return pages
            except Exception:
                if not html:
                    raise Blocked(blocked_msg or "I couldn't read that website. It may block automated readers.")
        sitemap_urls = await _sitemap_urls(client, final, base_domain)
        pages.append({"url": final, "title": "", "meta": {}, "text": ""})
        frontier = set(sitemap_urls)
        frontier.update(urldefrag(urljoin(final, href))[0] for href in _Page_links(html))
        seen.add(urldefrag(final)[0])
        landed = {urldefrag(final)[0]}  # where pages actually ended up: two addresses that redirect to one page are one page
        total_bytes = len(html.encode("utf-8", errors="ignore"))
        semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
        while frontier and len(pages) < max_pages and total_bytes < MAX_TOTAL_BYTES and time.monotonic() < deadline:
            candidates = [x for x in frontier if x not in seen and _in_scope(x, base_domain) and not SKIP_EXT.search(urlparse(x).path)]
            candidates.sort(key=lambda x: (0 if PRIORITY.search(urlparse(x).path) else 1, len(urlparse(x).path), x))
            batch = candidates[:min(FETCH_CONCURRENCY, max_pages - len(pages))]
            if not batch:
                break
            frontier.difference_update(batch)
            seen.update(batch)
            async def get_one(page_url):
                async with semaphore:
                    try:
                        final_url, content = await _get(client, page_url, base_domain)
                        if len(_Page_text(content)) < 100:
                            try:
                                rendered = await render_page(final_url) or await _reader(client, final_url)
                                from html import escape
                                content = "<title>" + escape(rendered["title"]) + "</title><pre>" + escape(rendered["text"]) + "</pre>"
                                content += "".join('<a href="' + escape(link, quote=True) + '">link</a>' for link in rendered["links"])
                            except Exception:
                                pass
                        return final_url, content
                    except Exception:
                        return None
            results = await asyncio.gather(*(get_one(x) for x in batch))
            for item in results:
                if not item:
                    continue
                page_url, page_html = item
                if urldefrag(page_url)[0] in landed:
                    continue
                landed.add(urldefrag(page_url)[0])
                size = len(page_html.encode("utf-8", errors="ignore"))
                if total_bytes + size > MAX_TOTAL_BYTES:
                    continue
                total_bytes += size
                parser = _Page()
                try:
                    parser.feed(page_html)
                except Exception:
                    continue
                txt = parser.text()
                extra = parser.structured_text()
                if extra:
                    txt = (txt + "\n\n" + extra).strip()
                if len(txt) > 80:
                    pages.append({"url": page_url, "title": parser.title.strip(), "meta": parser.meta, "text": txt})
                for href in parser.links:
                    full = urldefrag(urljoin(page_url, href))[0]
                    if full not in seen and _in_scope(full, base_domain) and not SKIP_EXT.search(urlparse(full).path):
                        frontier.add(full)
        first = _Page()
        first.feed(html)
        txt = first.text()
        extra = first.structured_text()
        pages[0] = {"url": final, "title": first.title.strip(), "meta": first.meta,
                    "text": (txt + ("\n\n" + extra if extra else "")).strip()}
    return pages


def _Page_links(html):
    p = _Page()
    try:
        p.feed(html)
    except Exception:
        return []
    return p.links


async def learn(url, pasted="", kb_id=None):
    """Read a site (or pasted text) into a new knowledge base. -> dict(kb_id, company, pages, sections, info). Raises Blocked with a friendly message."""
    if pasted and len(pasted.strip()) > 150:  # the site couldn't be read: use text the admin copied from it
        host0 = urlparse(url if re.match(r"^https?://", url, re.I) else "https://" + url).hostname or "pasted text"
        pages = [{"url": url if "://" in url else "https://" + url, "title": host0, "meta": {}, "text": pasted.strip()}]
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
    fields = {"name": f"Website: {company}", "agent_name": "",
              "persona": info.get("persona") or f"Super realistic, warm, quick rep for {company}. Short conversational sentences, authentic breathing pauses, natural filler words ('mm-hmm', 'yeah', 'hmm...').",
              "context": f"{company}: {info.get('what_they_do') or home['meta'].get('description') or ''} (from {host})",
              "key_facts": "\n".join(f"- {f}" for f in facts), "next_steps": info.get("call_goal") or "", "contact": ""}
    existing = db.q1("SELECT id FROM knowledge_bases WHERE id=?", (kb_id,)) if kb_id else None
    if existing:
        db.update("knowledge_bases", kb_id, **fields)
        for doc in db.q("SELECT id,source FROM kb_docs WHERE kb_id=? AND source IS NOT NULL", (kb_id,)):
            if _in_scope(doc["source"], _base_domain(urlparse(doc["source"]).hostname or "")) and _in_scope(doc["source"], _base_domain(host)):
                db.run("DELETE FROM kb_docs WHERE id=?", (doc["id"],))
    else:
        kb_id = db.run("INSERT INTO knowledge_bases(name,agent_name,persona,context,key_facts,next_steps,contact,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (fields["name"], fields["agent_name"], fields["persona"], fields["context"], fields["key_facts"], fields["next_steps"], fields["contact"], db.now()))
    total = 0
    for p in pages:
        title = p["title"] or p["url"]
        # Preserve raw page text and avoid a separate model request for every page.
        for heading, body in knowledge.segment_text(title[:120], p["text"]):
            knowledge.add_doc(kb_id, heading, body, source=p["url"])
            total += 1
    from . import files
    folder = files.folder_id(0, "Website knowledge/" + host)
    for page in pages:
        title = page["url"]
        body = f"# {page['title'] or title}\nSource: {title}\nRetrieved: {db.now()}\n\n{page['text']}"
        old = db.q1("SELECT id FROM documents WHERE agent_id=0 AND folder_id=? AND title=?", (folder, title))
        if old:
            db.update("documents", old["id"], body=body, updated_at=db.now())
        else:
            db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(0,?,'doc',?,?,?,?)",
                   (title, body, folder, db.now(), db.now()))
    return {"kb_id": kb_id, "company": company, "pages": [p["url"] for p in pages], "sections": total, "info": info, "host": host,
            "page_limit": MAX_PAGES, "coverage_note": f"Saved full extracted text from {len(pages)} public pages to Company drive / Website knowledge / {host}. Crawl limits: {MAX_PAGES} pages, {READER_PAGE_LIMIT} rendered pages, {MAX_TOTAL_BYTES // 1_000_000} MB and 8 minutes. Blocked, undiscovered, authenticated or over-limit pages may be missing; this is not a guarantee of complete site coverage."}



async def build(url, person_name="Alex", pasted=""):
    """-> dict(kb_id, contact_id, campaign_id, company, pages). Raises Blocked with a friendly message."""
    L = await learn(url, pasted)
    kb_id, company, info, host, pages, total = L["kb_id"], L["company"], L["info"], L["host"], L["pages"], L["sections"]
    contact_id = db.run("INSERT INTO contacts(name,phone,language,status,tags,notes,extra,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (person_name or "Alex", "+447700900123", "English", "new", "website-demo", "", "{}", db.now()))
    objective = (f"You are a rep for {company} ({info.get('what_they_do') or host}). This is a demo call: the person has just enquired through your website. "
                 f"{info.get('call_goal') or ''} Find out what they need, help them, and use lookup_info for every fact about {company}. "
                 f"If the site does not say something, say you'll check and have someone follow up; never guess prices or promises.")
    camp_id = db.run("INSERT INTO campaigns(name,objective,filters,status,max_attempts,kb_id,created_at) VALUES(?,?,?,?,?,?,?)",
                     (f"Website demo: {company}", objective, '{"ids": [%d]}' % contact_id, "active", 1, kb_id, db.now()))
    call_id = db.run("INSERT INTO calls(contact_id,campaign_id,attempt,status,language,created_at,not_before,mode) VALUES(?,?,?,?,?,?,?,?)",
                     (contact_id, camp_id, 1, "queued", "English", db.now(), 0, "live"))
    db.log("campaign", f"Website demo prepared for {company}: {len(pages)} pages, {total} knowledge sections", campaign_id=camp_id)
    return {"kb_id": kb_id, "contact_id": contact_id, "campaign_id": camp_id, "call_id": call_id, "company": company,
            "pages": pages, "sections": total}


async def render_page(url):
    """The page drawn by a real browser (see browser_tools.render), or None."""
    from . import browser_tools
    try:
        await asyncio.get_running_loop().run_in_executor(None, _check_host, urlparse(url).hostname)
        return await asyncio.wait_for(browser_tools.render(url), 45)
    except Exception:
        return None


async def read_one(url, render=False, want_money=False):
    """Read a single public page (same safety checks as the crawler). -> dict(url, title, text, links)

    The ladder: the page as a browser asks for it, then as a plain client (see _get); if it is empty, a bot check, or a JavaScript shell, the page opened in a
    real browser; and last the public reader service. render=True goes straight to the real browser. want_money=True is for a question about prices:
    a page that shows none in its plain text is opened in a real browser too, because many sites draw their prices after the page loads."""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    final, plain = url, None
    if not render:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            try:
                final, html = await _get(client, url)
                p = _Page()
                p.feed(html)
                text = p.text()
                if len(text) < 300:
                    text = (text + "\n" + p.structured_text()).strip()
                if len(text) >= 300 and not looks_blocked(text, p.title):
                    plain = {"url": final, "title": p.title.strip(), "text": text, "links": [urljoin(final, link) for link in p.links]}
            except (Blocked, httpx.HTTPError):
                final = url
        if plain and not (want_money and not MONEY.search(plain["text"])):
            return plain
    seen = await render_page(final)  # a real browser: runs the page's scripts, passes bot checks
    if seen and len(seen["text"]) >= 120 and (not plain or MONEY.search(seen["text"]) or len(seen["text"]) > 1.3 * len(plain["text"])):
        return seen
    if plain:
        return plain
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        alt = await _reader(client, final)
    return {"url": alt["url"], "title": alt["title"], "text": alt["text"], "links": alt.get("links", [])}

"""More apps, part 3: marketing and analytics, social, AI and data, news and market data, maps, security, plus free public data sources that need no account.
Same format as catalog.py. Apps with verified=True were exercised against the live service; the rest are written from public documentation:
use Test after connecting."""
import re

from .catalog import F, P, T, app, _GOOGLE_FIELDS, _GOOGLE_AUTH


# ---- reshape helpers for the free public sources ----
def _wb_country(out, args):
    rows = out[1] if isinstance(out, list) and len(out) > 1 and isinstance(out[1], list) else []
    q = (args.get("name") or "").strip().lower()
    res = [{"name": c.get("name"), "iso2": c.get("iso2Code"), "iso3": c.get("id"), "capital": c.get("capitalCity"), "region": (c.get("region") or {}).get("value"),
            "income_level": (c.get("incomeLevel") or {}).get("value"), "latitude": c.get("latitude"), "longitude": c.get("longitude")}
           for c in rows if isinstance(c, dict) and (not q or q in (c.get("name") or "").lower())]
    return [r for r in res if r["region"] != "Aggregates"][:8]


def _crossref(out, args):
    msg = (out or {}).get("message") or {}
    rows = msg.get("items") if "items" in msg else [msg]
    def who(a):
        return " ".join(x for x in (a.get("given"), a.get("family")) if x)
    return [{"title": (w.get("title") or [None])[0], "authors": [who(a) for a in (w.get("author") or [])[:4]], "journal": (w.get("container-title") or [None])[0],
             "year": ((w.get("issued") or {}).get("date-parts") or [[None]])[0][0], "doi": w.get("DOI"), "type": w.get("type"), "cited_by": w.get("is-referenced-by-count")} for w in (rows or [])[:8] if isinstance(w, dict)]


def _worldbank(out, args):
    rows = out[1] if isinstance(out, list) and len(out) > 1 and isinstance(out[1], list) else []
    return [{"country": r.get("country", {}).get("value"), "indicator": r.get("indicator", {}).get("value"), "year": r.get("date"), "value": r.get("value")} for r in rows[:10]]


def _dict(out, args):
    if not isinstance(out, list):
        return out
    res = []
    for e in out[:2]:
        res.append({"word": e.get("word"), "phonetic": e.get("phonetic"), "meanings": [{"part_of_speech": m.get("partOfSpeech"), "definitions": [d.get("definition") for d in (m.get("definitions") or [])[:2]],
                                                                                          "synonyms": (m.get("synonyms") or [])[:5]} for m in (e.get("meanings") or [])[:3]]})
    return res


def _govuk_page(out, args):
    d = out.get("details") or {}
    body = d.get("body") or d.get("introductory_paragraph") or ""
    if not isinstance(body, str):
        body = str(body)
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return {"title": out.get("title"), "description": out.get("description"), "text": text[:3500], "url": "https://www.gov.uk" + str(out.get("base_path") or "")}


def _pubmed(out, args):
    res = (out or {}).get("result") or {}
    return [{"id": i, "title": (res.get(i) or {}).get("title"), "journal": (res.get(i) or {}).get("fulljournalname"), "date": (res.get(i) or {}).get("pubdate"),
             "authors": [a.get("name") for a in ((res.get(i) or {}).get("authors") or [])[:4]]} for i in (res.get("uids") or [])[:8]]


def _pypi(out, args):
    i = out.get("info") or {}
    return {"name": i.get("name"), "version": i.get("version"), "summary": i.get("summary"), "requires_python": i.get("requires_python"), "license": i.get("license"), "homepage": i.get("home_page") or (i.get("project_urls") or {}).get("Homepage")}


def _npm_search(out, args):
    return [{"name": (o.get("package") or {}).get("name"), "version": (o.get("package") or {}).get("version"), "description": (o.get("package") or {}).get("description")} for o in (out.get("objects") or [])[:8]]


APPS = [
    # ================= free public data: no account, no key (built in) =================
    app("countries", "Countries", "Built in", "🌍", "Capital, region, income level and coordinates for any country (World Bank). For population and economy use the World Bank app.", [], {"type": "none"}, "https://api.worldbank.org/v2",
        [T("find", "Find a country by name.", {"name": P("Country name, e.g. Germany")}, ["name"], method="GET", path="/country/all", query={"format": "json", "per_page": "400"}, transform=_wb_country),
         T("by_code", "Look up a country by its 2 or 3 letter code.", {"code": P("e.g. GB, DE or USA")}, ["code"], method="GET", path="/country/{code}", query={"format": "json"}, transform=_wb_country)],
        builtin=True),
    app("air_quality", "Air quality", "Built in", "🌫️", "Current air quality and pollution levels for any place (Open-Meteo).", [], {"type": "none"}, "https://air-quality-api.open-meteo.com/v1",
        [T("current", "Current air quality at coordinates (use the maps tool to find them).", {"latitude": P("Latitude", "number"), "longitude": P("Longitude", "number")}, ["latitude", "longitude"], method="GET", path="/air-quality",
           query={"latitude": "{latitude}", "longitude": "{longitude}", "current": "european_aqi,us_aqi,pm10,pm2_5,nitrogen_dioxide,ozone,sulphur_dioxide", "timezone": "auto"}, pick="current")], builtin=True),
    app("wayback", "Web archive (Wayback Machine)", "Built in", "🕰️", "See whether a web page was saved in the Internet Archive, and find the closest saved copy to a date.", [], {"type": "none"}, "https://archive.org",
        [T("closest_copy", "Find the saved copy of a page closest to a date.", {"url": P("The page address"), "timestamp": P("YYYYMMDD, default today")}, ["url"], method="GET", path="/wayback/available", query={"url": "{url}", "timestamp": "{timestamp}"}, pick="archived_snapshots")], builtin=True),
    app("vat_check", "EU VAT number check (VIES)", "Built in", "🧾", "Check whether an EU VAT number is valid and see the registered business name and address.", [], {"type": "none"}, "https://ec.europa.eu/taxation_customs/vies/rest-api",
        [T("check", "Validate a VAT number.", {"country": P("2-letter EU country code, e.g. DE"), "number": P("The VAT number without the country code")}, ["country", "number"], method="GET", path="/ms/{country}/vat/{number}", pick={"path": "", "fields": []},
           result={"valid": "{s0.isValid}", "name": "{s0.name}", "address": "{s0.address}", "checked": "{s0.requestDate}", "note": "{s0.userError}"})], builtin=True),
    app("iban_check", "IBAN check", "Built in", "🏦", "Check an IBAN is valid and find the bank it belongs to.", [], {"type": "none"}, "https://openiban.com",
        [T("check", "Validate an IBAN.", {"iban": P("The IBAN")}, ["iban"], method="GET", path="/validate/{iban}", query={"getBIC": "true", "validateBankCode": "true"})], builtin=True),
    app("open_food_facts", "Food products (Open Food Facts)", "Built in", "🥫", "Ingredients, allergens, nutrition grade and brand for packaged food, by barcode or name.", [], {"type": "none"}, "https://world.openfoodfacts.org",
        [T("product", "Look up a product by barcode.", {"barcode": P("EAN or UPC barcode")}, ["barcode"], method="GET", path="/api/v2/product/{barcode}.json", query={"fields": "product_name,brands,quantity,nutriscore_grade,ingredients_text,allergens_tags,categories"}, pick="product"),
         T("search", "Search products by name.", {"query": P("Product name")}, ["query"], method="GET", path="https://search.openfoodfacts.org/search", query={"q": "{query}", "page_size": "5", "fields": "code,product_name,brands,nutriscore_grade"}, pick="hits")], builtin=True),
    app("crossref", "Research papers (Crossref)", "Built in", "🎓", "Find scholarly papers and their DOIs across all publishers.", [], {"type": "none"}, "https://api.crossref.org",
        [T("search", "Search scholarly works.", {"query": P("Topic, title or author")}, ["query"], method="GET", path="/works", query={"query": "{query}", "rows": "6", "select": "title,author,issued,DOI,container-title,type,is-referenced-by-count"}, transform=_crossref),
         T("by_doi", "Look up one work by DOI.", {"doi": P("The DOI, e.g. 10.1038/nature12373")}, ["doi"], method="GET", path="/works/{+doi}", transform=_crossref)], builtin=True),
    app("openalex", "Research (OpenAlex)", "Built in", "🔬", "Search 250 million scholarly works, with citation counts, from the open OpenAlex index.", [], {"type": "none"}, "https://api.openalex.org",
        [T("search_works", "Search works by topic.", {"query": P("Topic or title")}, ["query"], method="GET", path="/works", query={"search": "{query}", "per-page": "6", "select": "id,display_name,publication_year,cited_by_count,doi,type"}, pick="results")], builtin=True),
    app("pubmed", "Medical research (PubMed)", "Built in", "🧬", "Search biomedical literature in PubMed.", [], {"type": "none"}, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        [T("search", "Search PubMed.", {"query": P("Topic, condition or author")}, ["query"],
           steps=[{"method": "GET", "path": "/esearch.fcgi", "query": {"db": "pubmed", "term": "{query}", "retmax": "6", "retmode": "json", "sort": "relevance"}},
                  {"method": "GET", "path": "/esummary.fcgi", "query": {"db": "pubmed", "id": "{s0.esearchresult.idlist}", "retmode": "json"}, "delay": 0.4}], transform=_pubmed)], builtin=True),
    app("stackoverflow", "Programming Q&A (Stack Overflow)", "Built in", "💡", "Search Stack Overflow questions and answers.", [], {"type": "none"}, "https://api.stackexchange.com/2.3",
        [T("search", "Search questions.", {"query": P("The problem in plain words"), "site": P("Stack Exchange site, default stackoverflow")}, ["query"], method="GET", path="/search/advanced", query={"q": "{query}", "site": "{site|stackoverflow}", "order": "desc", "sort": "relevance", "pagesize": "6"},
           pick={"path": "items[]", "fields": ["title", "link", "score", "answer_count", "is_answered", "tags"]})], builtin=True),
    app("github_public", "GitHub (public projects)", "Built in", "🐙", "Search public repositories and read their details, releases and issues. No account needed.", [], {"type": "none"}, "https://api.github.com",
        [T("search_repos", "Search public repositories.", {"query": P("Topic or name")}, ["query"], method="GET", path="/search/repositories", query={"q": "{query}", "per_page": "6"}, pick={"path": "items[]", "fields": ["full_name", "description", "stargazers_count", "html_url", "updated_at"]}),
         T("repo", "Details of a repository.", {"owner": P("Owner"), "repo": P("Repository")}, ["owner", "repo"], method="GET", path="/repos/{owner}/{repo}", pick={"path": "", "fields": []},
           result={"name": "{s0.full_name}", "description": "{s0.description}", "stars": "{s0.stargazers_count}", "open_issues": "{s0.open_issues_count}", "license": "{s0.license.spdx_id}", "updated": "{s0.pushed_at}", "url": "{s0.html_url}"}),
         T("latest_release", "The latest release of a repository.", {"owner": P("Owner"), "repo": P("Repository")}, ["owner", "repo"], method="GET", path="/repos/{owner}/{repo}/releases/latest", pick={"path": "", "fields": []}, result={"tag": "{s0.tag_name}", "name": "{s0.name}", "published": "{s0.published_at}", "url": "{s0.html_url}"})], builtin=True, redirects=True, headers={"Accept": "application/vnd.github+json"}),
    app("npm_registry", "npm packages", "Built in", "📦", "Search JavaScript packages and see their latest version.", [], {"type": "none"}, "https://registry.npmjs.org",
        [T("search", "Search packages.", {"query": P("What it should do")}, ["query"], method="GET", path="/-/v1/search", query={"text": "{query}", "size": "8"}, transform=_npm_search),
         T("latest", "Latest version of a package.", {"package": P("Package name")}, ["package"], method="GET", path="/{+package}/latest", pick={"path": "", "fields": []}, result={"name": "{s0.name}", "version": "{s0.version}", "description": "{s0.description}", "license": "{s0.license}"})], builtin=True),
    app("pypi", "Python packages (PyPI)", "Built in", "🐍", "See a Python package's latest version and details.", [], {"type": "none"}, "https://pypi.org/pypi",
        [T("package", "Details of a package.", {"package": P("Package name")}, ["package"], method="GET", path="/{package}/json", transform=_pypi)], builtin=True),
    app("nasa", "NASA picture of the day", "Built in", "🚀", "NASA's astronomy picture of the day, with its explanation.", [], {"type": "none"}, "https://api.nasa.gov",
        [T("picture_of_the_day", "The astronomy picture of a day.", {"date": P("YYYY-MM-DD, default today")}, [], method="GET", path="/planetary/apod", query={"api_key": "DEMO_KEY", "date": "{date}"}, pick={"path": "", "fields": []},
           result={"title": "{s0.title}", "date": "{s0.date}", "explanation": "{s0.explanation}", "image": "{s0.url}", "type": "{s0.media_type}"})], builtin=True),
    app("uk_food_ratings", "UK food hygiene ratings", "Built in", "🍽️", "Food hygiene ratings of restaurants, cafés and shops in the UK (Food Standards Agency).", [], {"type": "none"}, "https://api.ratings.food.gov.uk",
        [T("search", "Find businesses by name and place.", {"name": P("Business name"), "address": P("Town or postcode")}, [], method="GET", path="/Establishments", query={"name": "{name}", "address": "{address}", "pageSize": "6"},
           pick={"path": "establishments[]", "fields": ["BusinessName", "AddressLine1", "PostCode", "RatingValue", "RatingDate", "BusinessType"]})], builtin=True, headers={"x-api-version": "2"}),
    app("gov_uk", "UK government guidance (GOV.UK)", "Built in", "🇬🇧", "Search GOV.UK and read official guidance, such as tax rates, rules for employers and business support.", [], {"type": "none"}, "https://www.gov.uk/api",
        [T("search", "Search GOV.UK.", {"query": P("What you need to know")}, ["query"], method="GET", path="/search.json", query={"q": "{query}", "count": "6"}, pick={"path": "results[]", "fields": ["title", "link", "description", "format"]}),
         T("read_page", "Read a GOV.UK page by its path (from search results).", {"path": P("Path such as vat-rates")}, ["path"], method="GET", path="/content/{+path}", transform=_govuk_page)], builtin=True, redirects=True),
    app("dictionary", "Dictionary", "Built in", "📖", "Definitions, pronunciation and synonyms for English words.", [], {"type": "none"}, "https://api.dictionaryapi.dev/api/v2",
        [T("define", "Define a word.", {"word": P("The word")}, ["word"], method="GET", path="/entries/en/{word}", transform=_dict)], builtin=True),
    app("word_finder", "Word finder (Datamuse)", "Built in", "🔤", "Find words that mean something, rhyme, or fit a spelling pattern.", [], {"type": "none"}, "https://api.datamuse.com",
        [T("similar_meaning", "Words with a similar meaning.", {"word": P("The word or idea")}, ["word"], method="GET", path="/words", query={"ml": "{word}", "max": "12"}, pick={"path": "", "fields": ["word"]}),
         T("rhymes", "Words that rhyme.", {"word": P("The word")}, ["word"], method="GET", path="/words", query={"rel_rhy": "{word}", "max": "12"}, pick={"path": "", "fields": ["word"]}),
         T("spelling", "Words matching a pattern, e.g. t??k finds talk and task.", {"pattern": P("Pattern with ? for one letter and * for any")}, ["pattern"], method="GET", path="/words", query={"sp": "{pattern}", "max": "12"}, pick={"path": "", "fields": ["word"]})], builtin=True),
    app("world_bank", "Economic data (World Bank)", "Built in", "📊", "Population, GDP, inflation, unemployment and hundreds of other indicators for every country.", [], {"type": "none"}, "https://api.worldbank.org/v2",
        [T("indicator", "An indicator for a country over recent years. Examples: NY.GDP.MKTP.CD (GDP in US$), SP.POP.TOTL (population), FP.CPI.TOTL.ZG (inflation), SL.UEM.TOTL.ZS (unemployment).",
           {"country": P("2-letter code, e.g. GB, or WLD for the world"), "indicator": P("Indicator code")}, ["country", "indicator"], method="GET", path="/country/{country}/indicator/{indicator}", query={"format": "json", "per_page": "6", "mrnev": "6"}, transform=_worldbank)], builtin=True),

    # ================= analytics and search marketing =================
    app("google_analytics", "Google Analytics (GA4)", "Analytics", "📈", "Ask for visitors, sessions, pages and sources from Google Analytics 4.",
        _GOOGLE_FIELDS + [F("property_id", "GA4 property ID", "Admin → Property settings → Property ID (numbers only).", validate=r"\d+")], _GOOGLE_AUTH, "https://analyticsdata.googleapis.com/v1beta",
        [T("report", "Run a report. Metrics such as sessions, activeUsers, screenPageViews, conversions; dimensions such as date, pagePath, sessionSource, country.",
           {"start_date": P("YYYY-MM-DD or 7daysAgo"), "end_date": P("YYYY-MM-DD or today"), "metric": P("Metric name"), "metric2": P("Second metric (optional)"), "dimension": P("Dimension name (optional)")}, ["start_date", "end_date", "metric"],
           method="POST", path="/properties/{cfg.property_id}:runReport", body={"dateRanges": [{"startDate": "{start_date}", "endDate": "{end_date}"}], "metrics": [{"name": "{metric}"}, {"name": "{metric2}"}], "dimensions": [{"name": "{dimension}"}], "limit": 25}, pick={"path": "", "fields": []},
           result={"rows": "{s0.rows}", "totals": "{s0.totals}"})],
        help="Scope needed: https://www.googleapis.com/auth/analytics.readonly", popular=True),
    app("google_search_console", "Google Search Console", "Analytics", "🔍", "See which searches bring people to your website.", _GOOGLE_FIELDS, _GOOGLE_AUTH, "https://searchconsole.googleapis.com/webmasters/v3",
        [T("list_sites", "List your verified sites.", {}, [], method="GET", path="/sites", pick="siteEntry"),
         T("top_queries", "Top search queries for a site.", {"site": P("Site as registered, e.g. https://example.com/ or sc-domain:example.com"), "start_date": P("YYYY-MM-DD"), "end_date": P("YYYY-MM-DD"), "dimension": P("query, page, country or device (default query)")}, ["site", "start_date", "end_date"],
           method="POST", path="/sites/{site}/searchAnalytics/query", body={"startDate": "{start_date}", "endDate": "{end_date}", "dimensions": ["{dimension}"], "rowLimit": 20}, pick="rows")],
        help="Scope needed: https://www.googleapis.com/auth/webmasters.readonly"),
    app("plausible", "Plausible Analytics", "Analytics", "📊", "Privacy-friendly website statistics from Plausible.",
        [F("api_key", "API key", "Settings → API keys.", secret=True), F("site_id", "Site (domain)", "e.g. example.com", validate=r"[A-Za-z0-9.-]+")],
        {"type": "bearer", "field": "api_key"}, "https://plausible.io/api",
        [T("summary", "Visitors, page views and bounce rate for a period.", {"period": P("day, 7d, 30d, month, 6mo or 12mo")}, [], method="GET", path="/v1/stats/aggregate", query={"site_id": "{cfg.site_id}", "period": "{period}", "metrics": "visitors,pageviews,bounce_rate,visit_duration"}, pick="results"),
         T("top_pages", "Most visited pages.", {"period": P("day, 7d, 30d, month…")}, [], method="GET", path="/v1/stats/breakdown", query={"site_id": "{cfg.site_id}", "period": "{period}", "property": "event:page", "limit": "10"}, pick="results"),
         T("top_sources", "Where visitors come from.", {"period": P("day, 7d, 30d, month…")}, [], method="GET", path="/v1/stats/breakdown", query={"site_id": "{cfg.site_id}", "period": "{period}", "property": "visit:source", "limit": "10"}, pick="results")],
        test={"method": "GET", "path": "/v1/stats/realtime/visitors", "query": {"site_id": "{cfg.site_id}"}}),
    app("posthog", "PostHog", "Analytics", "🦔", "Run product-analytics queries in PostHog.",
        [F("base", "PostHog address", "https://us.posthog.com or https://eu.posthog.com", default="https://us.posthog.com", required=False, validate=r"https://(us|eu|app)\.posthog\.com"), F("api_key", "Personal API key", "Settings → Personal API keys.", secret=True), F("project_id", "Project ID", "Project settings.", validate=r"\d+")],
        {"type": "bearer", "field": "api_key"}, "{cfg.base}",
        [T("query", "Run a HogQL query, e.g. SELECT event, count() FROM events GROUP BY event ORDER BY count() DESC LIMIT 10.", {"hogql": P("The HogQL query")}, ["hogql"], method="POST", path="/api/projects/{cfg.project_id}/query/", body={"query": {"kind": "HogQLQuery", "query": "{hogql}"}}, pick="results")],
        test={"method": "GET", "path": "/api/users/@me/"}),
    app("youtube", "YouTube", "Analytics", "▶️", "Search YouTube and read channel and video statistics.",
        [F("api_key", "API key", "Google Cloud → APIs & Services → enable YouTube Data API v3 → Credentials.", secret=True)],
        {"type": "query", "name": "key", "field": "api_key"}, "https://www.googleapis.com/youtube/v3",
        [T("search", "Search videos, channels or playlists.", {"query": P("What to search for"), "type": P("video, channel or playlist")}, ["query"], method="GET", path="/search", query={"part": "snippet", "q": "{query}", "type": "{type}", "maxResults": "8"}, pick={"path": "items[]", "fields": ["id", "snippet.title", "snippet.channelTitle", "snippet.publishedAt"]}),
         T("channel_stats", "Subscribers, views and video count of a channel.", {"channel_id": P("Channel ID")}, ["channel_id"], method="GET", path="/channels", query={"part": "snippet,statistics", "id": "{channel_id}"}, pick="items"),
         T("video_stats", "Views, likes and comments of a video.", {"video_id": P("Video ID")}, ["video_id"], method="GET", path="/videos", query={"part": "snippet,statistics", "id": "{video_id}"}, pick="items")],
        test={"method": "GET", "path": "/search", "query": {"part": "snippet", "q": "test", "maxResults": "1"}}),

    # ================= social and publishing =================
    app("x_twitter", "X (Twitter)", "Social media", "✖️", "Search recent posts, look up accounts and (with a user token) post.",
        [F("bearer_token", "Bearer token", "developer.x.com → your project → keys and tokens. A user-context OAuth 2.0 token also allows posting.", secret=True)],
        {"type": "bearer", "field": "bearer_token"}, "https://api.x.com/2",
        [T("search_recent", "Search posts from the last 7 days.", {"query": P("X search query, e.g. from:company OR \"our product\"")}, ["query"], method="GET", path="/tweets/search/recent", query={"query": "{query}", "max_results": "10", "tweet.fields": "created_at,public_metrics,author_id"}, pick="data"),
         T("user_by_username", "Look up an account.", {"username": P("Handle without @")}, ["username"], method="GET", path="/users/by/username/{username}", query={"user.fields": "description,public_metrics,verified"}, pick="data"),
         T("post", "Post to X (needs a user-context token with tweet.write).", {"text": P("The post, up to 280 characters")}, ["text"], True, method="POST", path="/tweets", body={"text": "{text}"}, pick="data")],
        test={"method": "GET", "path": "/users/by/username/x"}),
    app("bitly", "Bitly", "Marketing", "🔗", "Shorten links with Bitly (handy for texts).",
        [F("token", "Access token", "Settings → API → generic access token.", secret=True)],
        {"type": "bearer", "field": "token"}, "https://api-ssl.bitly.com/v4",
        [T("shorten", "Make a short link.", {"long_url": P("The full address")}, ["long_url"], True, method="POST", path="/shorten", body={"long_url": "{long_url}"}, pick={"path": "", "fields": []}, result={"short_url": "{s0.link}", "id": "{s0.id}"})],
        test={"method": "GET", "path": "/user"}),
    app("rebrandly", "Rebrandly", "Marketing", "🏷️", "Create branded short links with Rebrandly.",
        [F("api_key", "API key", "Account → API keys.", secret=True)],
        {"type": "header", "name": "apikey", "value": "{cfg.api_key}"}, "https://api.rebrandly.com/v1",
        [T("shorten", "Make a short link.", {"destination": P("The full address"), "title": P("A title (optional)")}, ["destination"], True, method="POST", path="/links", body={"destination": "{destination}", "title": "{title}"}, pick={"path": "", "fields": []}, result={"short_url": "https://{s0.shortUrl}", "id": "{s0.id}"})],
        test={"method": "GET", "path": "/account"}),
    app("unsplash", "Unsplash", "Marketing", "🖼️", "Find free, high-quality photos on Unsplash.",
        [F("access_key", "Access key", "unsplash.com/developers → your app → Access Key.", secret=True)],
        {"type": "header", "name": "Authorization", "value": "Client-ID {cfg.access_key}"}, "https://api.unsplash.com",
        [T("search_photos", "Search photos.", {"query": P("What the photo shows")}, ["query"], method="GET", path="/search/photos", query={"query": "{query}", "per_page": "6"}, pick={"path": "results[]", "fields": ["id", "description", "alt_description", "urls.regular", "user.name", "links.html"]})],
        test={"method": "GET", "path": "/photos", "query": {"per_page": "1"}}),
    app("pexels", "Pexels", "Marketing", "📸", "Find free photos and videos on Pexels.",
        [F("api_key", "API key", "pexels.com/api.", secret=True)],
        {"type": "header", "name": "Authorization", "value": "{cfg.api_key}"}, "https://api.pexels.com/v1",
        [T("search_photos", "Search photos.", {"query": P("What the photo shows")}, ["query"], method="GET", path="/search", query={"query": "{query}", "per_page": "6"}, pick={"path": "photos[]", "fields": ["id", "alt", "photographer", "src.large", "url"]})],
        test={"method": "GET", "path": "/curated", "query": {"per_page": "1"}}),
    app("figma", "Figma", "Marketing", "🎨", "Read Figma files and leave comments.",
        [F("token", "Personal access token", "Settings → Security → Personal access tokens.", secret=True)],
        {"type": "header", "name": "X-Figma-Token", "value": "{cfg.token}"}, "https://api.figma.com/v1",
        [T("file_info", "Name, pages and last change of a file.", {"file_key": P("The key in the file's URL")}, ["file_key"], method="GET", path="/files/{file_key}", query={"depth": "1"}),
         T("list_comments", "Comments on a file.", {"file_key": P("File key")}, ["file_key"], method="GET", path="/files/{file_key}/comments", pick="comments"),
         T("add_comment", "Comment on a file.", {"file_key": P("File key"), "message": P("Comment")}, ["file_key", "message"], True, method="POST", path="/files/{file_key}/comments", body={"message": "{message}"})],
        test={"method": "GET", "path": "/me"}),

    # ================= websites and content =================
    app("wordpress", "WordPress", "Websites and content", "🔷", "Read and draft posts on a WordPress site.",
        [F("url", "Site address", "e.g. https://example.com", validate=r"https://.+"), F("username", "Username"), F("app_password", "Application password", "Users → Profile → Application Passwords.", secret=True)],
        {"type": "basic", "user": "{cfg.username}", "pass": "{cfg.app_password}"}, "{cfg.url}",
        [T("list_posts", "List recent posts.", {"search": P("Search text (optional)"), "status": P("publish, draft, pending")}, [], method="GET", path="/wp-json/wp/v2/posts", query={"search": "{search}", "status": "{status}", "per_page": "10"}, pick={"path": "", "fields": ["id", "slug", "status", "link", "date"]}),
         T("create_draft", "Create a draft post (it is not published).", {"title": P("Title"), "content": P("HTML or plain text")}, ["title", "content"], True, method="POST", path="/wp-json/wp/v2/posts", body={"title": "{title}", "content": "{content}", "status": "draft"}, pick="id")],
        test={"method": "GET", "path": "/wp-json/wp/v2/users/me"}, dynamic_url=True, popular=True),
    app("webflow", "Webflow", "Websites and content", "🌐", "See sites and CMS items in Webflow.",
        [F("token", "Site token or API token", "Site settings → Apps & integrations → API access.", secret=True)],
        {"type": "bearer", "field": "token"}, "https://api.webflow.com/v2",
        [T("list_sites", "List sites.", {}, [], method="GET", path="/sites", pick={"path": "sites[]", "fields": ["id", "displayName", "shortName", "lastPublished"]}),
         T("list_items", "List CMS items in a collection.", {"collection_id": P("Collection ID")}, ["collection_id"], method="GET", path="/collections/{collection_id}/items", query={"limit": "20"}, pick="items")],
        test={"method": "GET", "path": "/sites"}),
    app("contentful", "Contentful", "Websites and content", "📰", "Read entries from Contentful.",
        [F("space_id", "Space ID", "Settings → General settings."), F("delivery_token", "Content Delivery API token", "Settings → API keys.", secret=True)],
        {"type": "bearer", "field": "delivery_token"}, "https://cdn.contentful.com/spaces/{cfg.space_id}/environments/master",
        [T("list_entries", "List entries (optionally of one content type).", {"content_type": P("Content type ID (optional)"), "query": P("Search text (optional)")}, [], method="GET", path="/entries", query={"content_type": "{content_type}", "query": "{query}", "limit": "20"}, pick="items")],
        test={"method": "GET", "path": "/entries", "query": {"limit": "1"}}),
    app("ghost_content", "Ghost (read)", "Websites and content", "👻", "Read posts from a Ghost site.",
        [F("url", "Site address", "e.g. https://yourblog.ghost.io", validate=r"https://.+"), F("content_key", "Content API key", "Settings → Integrations → add a custom integration.", secret=True)],
        {"type": "query", "name": "key", "field": "content_key"}, "{cfg.url}",
        [T("list_posts", "List recent posts.", {"filter": P("Ghost filter, e.g. tag:news (optional)")}, [], method="GET", path="/ghost/api/content/posts/", query={"limit": "10", "filter": "{filter}", "fields": "title,url,published_at,excerpt"}, pick="posts")], dynamic_url=True),
]


# The keyless apps below were run against the live services (every action, with real sample inputs) when this catalogue was written.
# NASA (its demo key was returning server errors that day) and the Wayback Machine (it intermittently answers with no saved copy) are left unmarked.
_LIVE_TESTED = {"countries", "air_quality", "vat_check", "iban_check", "open_food_facts", "crossref", "openalex", "pubmed", "stackoverflow", "github_public", "npm_registry", "pypi",
                "uk_food_ratings", "gov_uk", "dictionary", "word_finder", "world_bank"}
for _a in APPS:
    if _a["id"] in _LIVE_TESTED:
        _a["verified"] = True

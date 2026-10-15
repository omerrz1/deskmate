"""More apps, part 5: messaging, email, dev and hosting, accounting, maps and weather, social, AI, plus keyless public data.
Same format as catalog.py. Written from each service's public API documentation; use Test after connecting. The keyless apps marked verified at the bottom were run live."""
from .catalog import F, P, T, app


# ---- reshape helpers for the keyless public sources ----
def _ctgov(out, args):
    res = []
    for s in (out or {}).get("studies", [])[:6]:
        p = s.get("protocolSection") or {}
        idm, st, cond = p.get("identificationModule") or {}, p.get("statusModule") or {}, p.get("conditionsModule") or {}
        res.append({"id": idm.get("nctId"), "title": idm.get("briefTitle"), "status": st.get("overallStatus"), "conditions": (cond.get("conditions") or [])[:4], "start": (st.get("startDateStruct") or {}).get("date")})
    return res


def _openfda(out, args):
    first = lambda v: (v[0] if isinstance(v, list) and v else v)  # noqa: E731
    return [{"brand": first((r.get("openfda") or {}).get("brand_name")), "generic": first((r.get("openfda") or {}).get("generic_name")), "purpose": str(first(r.get("purpose")) or "")[:240],
             "uses": str(first(r.get("indications_and_usage")) or "")[:300], "warnings": str(first(r.get("warnings")) or "")[:300]} for r in (out or {}).get("results", [])[:3]]


def _tfl_status(out, args):
    return [{"line": l.get("name"), "status": ((l.get("lineStatuses") or [{}])[0]).get("statusSeverityDescription"), "reason": str(((l.get("lineStatuses") or [{}])[0]).get("reason") or "")[:200]} for l in (out or [])]


def _tfl_stops(out, args):
    return [{"id": m.get("id"), "name": m.get("name"), "modes": m.get("modes")} for m in (out or {}).get("matches", [])[:8]]


def _flood(out, args):
    return [{"area": i.get("description"), "severity": i.get("severity"), "level": i.get("severityLevel"), "message": str(i.get("message") or "")[:240], "changed": i.get("timeMessageChanged")} for i in (out or {}).get("items", [])[:10]]


def _docker(out, args):
    return [{"name": r.get("repo_name"), "description": r.get("short_description"), "stars": r.get("star_count"), "pulls": r.get("pull_count"), "official": r.get("is_official")} for r in (out or {}).get("results", [])[:8]]


def _jina(out, args):
    d = (out or {}).get("data") if isinstance(out, dict) else None
    if isinstance(d, dict):
        return {"title": d.get("title"), "url": d.get("url"), "text": str(d.get("content") or "")[:5500]}
    return str(out)[:5500]


APPS = [
    # ---------------- messaging and email ----------------
    app("messagebird", "MessageBird (Bird)", "Messaging", "🐦", "Send text messages and check your balance with MessageBird.",
        [F("api_key", "Live access key", "Dashboard → Developers → API access.", secret=True)],
        {"type": "header", "name": "Authorization", "value": "AccessKey {cfg.api_key}"}, "https://rest.messagebird.com",
        [T("send_sms", "Send a text message.", {"from": P("Sender name or number (max 11 letters)"), "to": P("Number with country code, e.g. 447700900123"), "text": P("The message")}, ["from", "to", "text"], True,
           method="POST", path="/messages", body={"originator": "{from}", "recipients": ["{to}"], "body": "{text}"}, pick={"path": "", "fields": []}, result={"id": "{s0.id}", "status": "{s0.recipients.items[0].status}"}),
         T("balance", "Check the account balance.", {}, [], method="GET", path="/balance"),
         T("recent_messages", "Recent messages.", {}, [], method="GET", path="/messages", query={"limit": "10"}, pick={"path": "items[]", "fields": ["id", "originator", "body", "createdDatetime"]})],
        test={"method": "GET", "path": "/balance"}),
    app("mailjet", "Mailjet", "Email", "✈️", "Send email and list contacts with Mailjet.",
        [F("api_key", "API key", "Account settings → REST API → API key management.", secret=True), F("secret_key", "Secret key", secret=True)],
        {"type": "basic", "user": "{cfg.api_key}", "pass": "{cfg.secret_key}"}, "https://api.mailjet.com",
        [T("send_email", "Send an email.", {"from_email": P("A verified sender address"), "from_name": P("Sender name (optional)"), "to": P("Recipient"), "subject": P("Subject"), "text": P("Plain-text body")}, ["from_email", "to", "subject", "text"], True,
           method="POST", path="/v3.1/send", body={"Messages": [{"From": {"Email": "{from_email}", "Name": "{from_name}"}, "To": [{"Email": "{to}"}], "Subject": "{subject}", "TextPart": "{text}"}]}),
         T("list_contacts", "List contacts.", {}, [], method="GET", path="/v3/REST/contact", query={"Limit": "20"}, pick={"path": "Data[]", "fields": ["ID", "Email", "Name", "CreatedAt"]})],
        test={"method": "GET", "path": "/v3/REST/contact", "query": {"Limit": "1"}}),
    app("sparkpost", "SparkPost", "Email", "⚡", "Send email through SparkPost.",
        [F("api_key", "API key", "Account → API keys (needs Transmissions: Read/Write).", secret=True), F("base", "API address", "https://api.sparkpost.com/api/v1, or the .eu address for EU accounts.", default="https://api.sparkpost.com/api/v1", required=False, validate=r"https://api(\.eu)?\.sparkpost\.com/api/v1")],
        {"type": "header", "name": "Authorization", "value": "{cfg.api_key}"}, "{cfg.base}",
        [T("send_email", "Send an email.", {"from_email": P("A verified sending address"), "to": P("Recipient"), "subject": P("Subject"), "text": P("Plain-text body")}, ["from_email", "to", "subject", "text"], True,
           method="POST", path="/transmissions", body={"recipients": [{"address": "{to}"}], "content": {"from": "{from_email}", "subject": "{subject}", "text": "{text}"}})],
        test={"method": "GET", "path": "/sending-domains"}),
    app("mandrill", "Mandrill", "Email", "🐴", "Send transactional email with Mailchimp Transactional (Mandrill).",
        [F("api_key", "API key", "Mailchimp → Transactional → Settings → API keys.", secret=True)],
        {"type": "none"}, "https://mandrillapp.com/api/1.0",
        [T("send_email", "Send an email.", {"from_email": P("A verified sending address"), "to": P("Recipient"), "subject": P("Subject"), "text": P("Plain-text body")}, ["from_email", "to", "subject", "text"], True,
           method="POST", path="/messages/send.json", body={"key": "{cfg.api_key}", "message": {"text": "{text}", "subject": "{subject}", "from_email": "{from_email}", "to": [{"email": "{to}", "type": "to"}]}}),
         T("ping", "Check the key works.", {}, [], method="POST", path="/users/ping.json", body={"key": "{cfg.api_key}"})],
        test={"method": "POST", "path": "/users/ping.json", "body": {"key": "{cfg.api_key}"}}),
    app("getresponse", "GetResponse", "Email", "💌", "Add and find subscribers in GetResponse lists.",
        [F("api_key", "API key", "Integrations and API → API.", secret=True)],
        {"type": "header", "name": "X-Auth-Token", "value": "api-key {cfg.api_key}"}, "https://api.getresponse.com/v3",
        [T("list_campaigns", "List your lists (campaigns).", {}, [], method="GET", path="/campaigns", pick={"path": "", "fields": ["campaignId", "name"]}),
         T("find_contact", "Find a subscriber by email.", {"email": P("Email")}, ["email"], method="GET", path="/contacts", query={"query[email]": "{email}"}, pick={"path": "", "fields": ["contactId", "name", "email", "createdOn"]}),
         T("add_contact", "Add a subscriber to a list.", {"email": P("Email"), "name": P("Name (optional)"), "campaign_id": P("List ID from list_campaigns")}, ["email", "campaign_id"], True,
           method="POST", path="/contacts", body={"email": "{email}", "name": "{name}", "campaign": {"campaignId": "{campaign_id}"}})],
        test={"method": "GET", "path": "/accounts"}),

    # ---------------- work, CRM and accounting ----------------
    app("shortcut", "Shortcut", "Work management", "🚩", "Search, list and create stories in Shortcut.",
        [F("api_token", "API token", "Settings → API tokens.", secret=True)],
        {"type": "header", "name": "Shortcut-Token", "value": "{cfg.api_token}"}, "https://api.app.shortcut.com/api/v3",
        [T("search_stories", "Search stories.", {"query": P("Words to find")}, ["query"], method="GET", path="/search/stories", query={"query": "{query}", "page_size": "10"}, pick={"path": "data[]", "fields": ["id", "name", "story_type", "completed", "app_url"]}),
         T("list_projects", "List projects.", {}, [], method="GET", path="/projects", pick={"path": "", "fields": ["id", "name", "stats.num_stories"]}),
         T("create_story", "Create a story in a project.", {"name": P("Title"), "project_id": P("Project ID", "integer"), "description": P("Details (optional)"), "story_type": P("feature, bug or chore")}, ["name", "project_id"], True,
           method="POST", path="/stories", body={"name": "{name}", "project_id": "{project_id}", "description": "{description}", "story_type": "{story_type|feature}"})],
        test={"method": "GET", "path": "/member"}),
    app("insightly", "Insightly", "CRM", "💡", "Look up and add contacts and opportunities in Insightly.",
        [F("api_key", "API key", "User settings → API.", secret=True), F("base", "API address", "Shown next to your API key, e.g. https://api.na1.insightly.com/v3.1.", default="https://api.na1.insightly.com/v3.1", required=False, validate=r"https://api\.(na1|na2|eu1|au1)\.insightly\.com/v3\.1")],
        {"type": "basic", "user": "{cfg.api_key}", "pass": ""}, "{cfg.base}",
        [T("list_contacts", "List contacts.", {}, [], method="GET", path="/Contacts", query={"top": "20"}, pick={"path": "", "fields": ["CONTACT_ID", "FIRST_NAME", "LAST_NAME", "EMAIL_ADDRESS"]}),
         T("find_contact", "Find a contact by email.", {"email": P("Email")}, ["email"], method="GET", path="/Contacts/Search", query={"field_name": "EMAIL_ADDRESS", "field_value": "{email}"}, pick={"path": "", "fields": ["CONTACT_ID", "FIRST_NAME", "LAST_NAME", "EMAIL_ADDRESS"]}),
         T("create_contact", "Add a contact.", {"first_name": P("First name"), "last_name": P("Last name"), "email": P("Email")}, ["last_name"], True,
           method="POST", path="/Contacts", body={"FIRST_NAME": "{first_name}", "LAST_NAME": "{last_name}", "EMAIL_ADDRESS": "{email}"}),
         T("list_opportunities", "List opportunities.", {}, [], method="GET", path="/Opportunities", query={"top": "20"}, pick={"path": "", "fields": ["OPPORTUNITY_ID", "OPPORTUNITY_NAME", "BID_AMOUNT", "OPPORTUNITY_STATE"]})],
        test={"method": "GET", "path": "/Users/Me"}),
    app("zoho_books", "Zoho Books", "Payments", "📒", "Look at invoices and customers, and add customers, in Zoho Books.",
        [F("accounts_domain", "Accounts domain", "accounts.zoho.com, or accounts.zoho.eu / .in / .com.au for other regions.", default="accounts.zoho.com", required=False, validate=r"accounts\.zoho\.[a-z.]+"),
         F("base", "API domain", "https://www.zohoapis.com, or the .eu / .in / .com.au version.", default="https://www.zohoapis.com", required=False, validate=r"https://www\.zohoapis\.[a-z.]+"),
         F("organization_id", "Organisation ID", "Zoho Books → Settings → Organisation profile."), F("client_id", "Client ID", "api-console.zoho.com → Self Client."), F("client_secret", "Client secret", secret=True),
         F("refresh_token", "Refresh token", "Generated in the Self Client with scope ZohoBooks.fullaccess.all.", secret=True)],
        {"type": "oauth2_refresh", "scheme": "Zoho-oauthtoken", "token_url": "https://{cfg.accounts_domain}/oauth/v2/token", "data": {"grant_type": "refresh_token", "client_id": "{cfg.client_id}", "client_secret": "{cfg.client_secret}", "refresh_token": "{cfg.refresh_token}"}},
        "{cfg.base}/books/v3",
        [T("list_invoices", "List invoices.", {"status": P("sent, paid, overdue, draft… (optional)")}, [], method="GET", path="/invoices", query={"organization_id": "{cfg.organization_id}", "status": "{status}"}, pick={"path": "invoices[]", "fields": ["invoice_id", "invoice_number", "customer_name", "status", "total", "balance", "due_date"]}),
         T("list_customers", "List customers.", {}, [], method="GET", path="/contacts", query={"organization_id": "{cfg.organization_id}"}, pick={"path": "contacts[]", "fields": ["contact_id", "contact_name", "email", "outstanding_receivable_amount"]}),
         T("create_customer", "Add a customer.", {"name": P("Customer name"), "email": P("Email (optional)")}, ["name"], True,
           method="POST", path="/contacts", query={"organization_id": "{cfg.organization_id}"}, body={"contact_name": "{name}", "contact_persons": [{"email": "{email}"}]})],
        test={"method": "GET", "path": "/organizations"}),

    # ---------------- developer, hosting and monitoring ----------------
    app("circleci", "CircleCI", "Developer tools", "⭕", "See recent pipelines and start a build in CircleCI.",
        [F("token", "Personal API token", "User settings → Personal API tokens.", secret=True)],
        {"type": "header", "name": "Circle-Token", "value": "{cfg.token}"}, "https://circleci.com/api/v2",
        [T("recent_pipelines", "Recent pipelines of a project.", {"project_slug": P("e.g. gh/my-org/my-repo")}, ["project_slug"], method="GET", path="/project/{+project_slug}/pipeline", pick={"path": "items[]", "fields": ["id", "number", "state", "created_at", "vcs.branch"]}),
         T("trigger_pipeline", "Start a pipeline on a branch.", {"project_slug": P("e.g. gh/my-org/my-repo"), "branch": P("Branch name")}, ["project_slug"], True, method="POST", path="/project/{+project_slug}/pipeline", body={"branch": "{branch}"})],
        test={"method": "GET", "path": "/me"}),
    app("newrelic", "New Relic", "Developer tools", "📟", "See application health and open alerts in New Relic.",
        [F("api_key", "User API key", "one.newrelic.com → API keys.", secret=True), F("base", "API address", "https://api.newrelic.com/v2 (US), or the api.eu.newrelic.com version for EU accounts.", default="https://api.newrelic.com/v2", required=False, validate=r"https://api(\.eu)?\.newrelic\.com/v2")],
        {"type": "header", "name": "Api-Key", "value": "{cfg.api_key}"}, "{cfg.base}",
        [T("list_apps", "List applications and their health.", {}, [], method="GET", path="/applications.json", pick={"path": "applications[]", "fields": ["id", "name", "health_status", "reporting", "last_reported_at"]}),
         T("open_violations", "Alert violations that are still open.", {}, [], method="GET", path="/alerts_violations.json", query={"only_open": "true"}, pick={"path": "violations[]", "fields": ["label", "priority", "opened_at", "entity.name"]})],
        test={"method": "GET", "path": "/applications.json"}),
    app("statuspage", "Statuspage", "Developer tools", "🟢", "Post and check incidents on your Statuspage.",
        [F("api_key", "API key", "Statuspage → your avatar → API info.", secret=True)],
        {"type": "header", "name": "Authorization", "value": "OAuth {cfg.api_key}"}, "https://api.statuspage.io/v1",
        [T("list_pages", "List your status pages.", {}, [], method="GET", path="/pages", pick={"path": "", "fields": ["id", "name", "url"]}),
         T("list_incidents", "Recent incidents.", {"page_id": P("Page ID from list_pages")}, ["page_id"], method="GET", path="/pages/{page_id}/incidents", query={"limit": "10"}, pick={"path": "", "fields": ["id", "name", "status", "impact", "created_at", "shortlink"]}),
         T("components", "Components and their status.", {"page_id": P("Page ID")}, ["page_id"], method="GET", path="/pages/{page_id}/components", pick={"path": "", "fields": ["id", "name", "status"]}),
         T("create_incident", "Open an incident.", {"page_id": P("Page ID"), "name": P("Title"), "status": P("investigating, identified, monitoring or resolved"), "body": P("What is happening")}, ["page_id", "name"], True,
           method="POST", path="/pages/{page_id}/incidents", body={"incident": {"name": "{name}", "status": "{status|investigating}", "body": "{body}"}})],
        test={"method": "GET", "path": "/pages"}),
    app("rollbar", "Rollbar", "Developer tools", "🧯", "List active errors in Rollbar.",
        [F("token", "Project read access token", "Project settings → Project access tokens.", secret=True)],
        {"type": "header", "name": "X-Rollbar-Access-Token", "value": "{cfg.token}"}, "https://api.rollbar.com/api/1",
        [T("active_items", "Active error items, most frequent first.", {"level": P("error, warning, critical… (optional)")}, [], method="GET", path="/items", query={"status": "active", "level": "{level}"}, pick={"path": "result.items[]", "fields": ["id", "counter", "title", "level", "total_occurrences", "last_occurrence_timestamp"]})],
        test={"method": "GET", "path": "/items"}),
    app("heroku", "Heroku", "Servers and hosting", "🟣", "List apps and dynos, and restart an app, on Heroku.",
        [F("api_key", "API key", "Account settings → API Key (or a token from the CLI).", secret=True)],
        {"type": "bearer", "field": "api_key"}, "https://api.heroku.com",
        [T("list_apps", "List apps.", {}, [], method="GET", path="/apps", pick={"path": "", "fields": ["name", "region.name", "stack.name", "updated_at"]}),
         T("list_dynos", "Dynos of an app.", {"app": P("App name")}, ["app"], method="GET", path="/apps/{app}/dynos", pick={"path": "", "fields": ["name", "state", "type", "size", "updated_at"]}),
         T("restart_app", "Restart all dynos of an app.", {"app": P("App name")}, ["app"], True, method="DELETE", path="/apps/{app}/dynos")],
        test={"method": "GET", "path": "/account"}, headers={"Accept": "application/vnd.heroku+json; version=3"}),
    app("vultr", "Vultr", "Servers and hosting", "☁️", "List and reboot cloud servers on Vultr.",
        [F("api_key", "API key", "Account → API.", secret=True)],
        {"type": "bearer", "field": "api_key"}, "https://api.vultr.com/v2",
        [T("list_instances", "List servers.", {}, [], method="GET", path="/instances", pick={"path": "instances[]", "fields": ["id", "label", "main_ip", "power_status", "status", "region", "plan"]}),
         T("reboot", "Reboot a server.", {"instance_id": P("Server ID")}, ["instance_id"], True, method="POST", path="/instances/{instance_id}/reboot")],
        test={"method": "GET", "path": "/account"}),
    app("linode", "Linode (Akamai)", "Servers and hosting", "🌐", "List and reboot servers on Linode.",
        [F("token", "Personal access token", "Cloud Manager → Profile → API tokens.", secret=True)],
        {"type": "bearer", "field": "token"}, "https://api.linode.com/v4",
        [T("list_servers", "List servers.", {}, [], method="GET", path="/linode/instances", pick={"path": "data[]", "fields": ["id", "label", "status", "ipv4", "region", "type"]}),
         T("reboot", "Reboot a server.", {"linode_id": P("Server ID")}, ["linode_id"], True, method="POST", path="/linode/instances/{linode_id}/reboot")],
        test={"method": "GET", "path": "/profile"}),
    app("hetzner", "Hetzner Cloud", "Servers and hosting", "🖥️", "List servers on Hetzner Cloud.",
        [F("token", "API token", "Project → Security → API tokens.", secret=True)],
        {"type": "bearer", "field": "token"}, "https://api.hetzner.cloud/v1",
        [T("list_servers", "List servers.", {}, [], method="GET", path="/servers", pick={"path": "servers[]", "fields": ["id", "name", "status", "public_net.ipv4.ip", "datacenter.name", "server_type.name"]})],
        test={"method": "GET", "path": "/servers", "query": {"per_page": "1"}}),

    # ---------------- analytics, maps and weather ----------------
    app("fathom", "Fathom Analytics", "Analytics", "🦅", "See visits and page views from Fathom.",
        [F("api_key", "API key", "Settings → API.", secret=True)],
        {"type": "bearer", "field": "api_key"}, "https://api.usefathom.com/v1",
        [T("list_sites", "List your sites.", {}, [], method="GET", path="/sites", pick={"path": "data[]", "fields": ["id", "name", "sharing"]}),
         T("visits", "Visits, unique visitors and page views by day.", {"site_id": P("Site ID from list_sites"), "date_from": P("YYYY-MM-DD HH:MM:SS (optional)"), "date_to": P("YYYY-MM-DD HH:MM:SS (optional)")}, ["site_id"], method="GET", path="/aggregations",
           query={"entity": "pageview", "entity_id": "{site_id}", "aggregates": "visits,uniques,pageviews", "date_grouping": "day", "date_from": "{date_from}", "date_to": "{date_to}"})],
        test={"method": "GET", "path": "/sites"}),
    app("here_maps", "HERE Maps", "Maps and places", "📌", "Geocode addresses, look up coordinates and work out routes with HERE.",
        [F("api_key", "API key", "platform.here.com → your project → API keys.", secret=True)],
        {"type": "query", "name": "apiKey", "field": "api_key"}, "https://geocode.search.hereapi.com/v1",
        [T("geocode", "Turn an address into coordinates.", {"address": P("Address or place")}, ["address"], method="GET", path="/geocode", query={"q": "{address}", "limit": "3"}, pick={"path": "items[]", "fields": ["title", "address.label", "position.lat", "position.lng"]}),
         T("reverse", "Find the address at coordinates.", {"lat": P("Latitude", "number"), "lng": P("Longitude", "number")}, ["lat", "lng"], method="GET", path="https://revgeocode.search.hereapi.com/v1/revgeocode", query={"at": "{lat},{lng}", "limit": "1"}, pick={"path": "items[]", "fields": ["title", "address.label"]}),
         T("route", "Distance and time between two points.", {"from": P("Start as lat,lng"), "to": P("End as lat,lng"), "mode": P("car, pedestrian, bicycle or truck")}, ["from", "to"], method="GET", path="https://router.hereapi.com/v8/routes",
           query={"transportMode": "{mode|car}", "origin": "{from}", "destination": "{to}", "return": "summary"}, pick={"path": "", "fields": []}, result={"distance_metres": "{s0.routes[0].sections[0].summary.length}", "duration_seconds": "{s0.routes[0].sections[0].summary.duration}"})],
        test={"method": "GET", "path": "/geocode", "query": {"q": "London", "limit": "1"}}),
    app("opencage", "OpenCage Geocoder", "Maps and places", "🧭", "Turn addresses into coordinates and back with OpenCage.",
        [F("api_key", "API key", "opencagedata.com → dashboard.", secret=True)],
        {"type": "query", "name": "key", "field": "api_key"}, "https://api.opencagedata.com/geocode/v1",
        [T("geocode", "Turn an address into coordinates.", {"address": P("Address or place")}, ["address"], method="GET", path="/json", query={"q": "{address}", "limit": "3"}, pick={"path": "results[]", "fields": ["formatted", "geometry.lat", "geometry.lng", "components.country"]}),
         T("reverse", "Find the address at coordinates.", {"lat": P("Latitude", "number"), "lng": P("Longitude", "number")}, ["lat", "lng"], method="GET", path="/json", query={"q": "{lat},{lng}", "limit": "1"}, pick={"path": "results[]", "fields": ["formatted", "components.country"]})],
        test={"method": "GET", "path": "/json", "query": {"q": "London", "limit": "1"}}),
    app("ipinfo", "IPinfo", "Maps and places", "🛰️", "Find the city, company and time zone behind an IP address with IPinfo.",
        [F("token", "Access token", "ipinfo.io → dashboard.", secret=True)],
        {"type": "query", "name": "token", "field": "token"}, "https://ipinfo.io",
        [T("lookup", "Details of an IP address.", {"ip": P("IP address")}, ["ip"], method="GET", path="/{ip}/json")],
        test={"method": "GET", "path": "/8.8.8.8/json"}),
    app("weatherapi", "WeatherAPI", "Maps and places", "🌤️", "Current weather and forecasts for any place with WeatherAPI.com.",
        [F("api_key", "API key", "weatherapi.com → dashboard.", secret=True)],
        {"type": "query", "name": "key", "field": "api_key"}, "https://api.weatherapi.com/v1",
        [T("current", "Weather now.", {"place": P("City, postcode or lat,lng")}, ["place"], method="GET", path="/current.json", query={"q": "{place}"}, pick={"path": "", "fields": []},
           result={"place": "{s0.location.name}", "country": "{s0.location.country}", "temp_c": "{s0.current.temp_c}", "feels_like_c": "{s0.current.feelslike_c}", "condition": "{s0.current.condition.text}", "wind_kph": "{s0.current.wind_kph}", "humidity": "{s0.current.humidity}"}),
         T("forecast", "Forecast for the next days.", {"place": P("City, postcode or lat,lng"), "days": P("1 to 10", "integer")}, ["place"], method="GET", path="/forecast.json", query={"q": "{place}", "days": "{days|3}"},
           pick={"path": "forecast.forecastday", "fields": ["date", "day.maxtemp_c", "day.mintemp_c", "day.condition.text", "day.daily_chance_of_rain"]})],
        test={"method": "GET", "path": "/current.json", "query": {"q": "London"}}),

    # ---------------- money and markets ----------------
    app("openexchangerates", "Open Exchange Rates", "News and markets", "💱", "Current and past exchange rates from Open Exchange Rates.",
        [F("app_id", "App ID", "openexchangerates.org → Account → App IDs.", secret=True)],
        {"type": "query", "name": "app_id", "field": "app_id"}, "https://openexchangerates.org/api",
        [T("latest", "Latest rates against the US dollar.", {"symbols": P("Comma-separated codes, e.g. GBP,EUR (optional)")}, [], method="GET", path="/latest.json", query={"symbols": "{symbols}"}, pick={"path": "", "fields": []}, result={"base": "{s0.base}", "time": "{s0.timestamp}", "rates": "{s0.rates}"}),
         T("historical", "Rates on a past day.", {"date": P("YYYY-MM-DD"), "symbols": P("Comma-separated codes (optional)")}, ["date"], method="GET", path="/historical/{date}.json", query={"symbols": "{symbols}"}, pick={"path": "", "fields": []}, result={"date": "{date}", "base": "{s0.base}", "rates": "{s0.rates}"})],
        test={"method": "GET", "path": "/usage.json"}),
    app("twelvedata", "Twelve Data", "News and markets", "📊", "Share, currency and crypto prices and history from Twelve Data.",
        [F("api_key", "API key", "twelvedata.com → dashboard.", secret=True)],
        {"type": "query", "name": "apikey", "field": "api_key"}, "https://api.twelvedata.com",
        [T("price", "Latest price.", {"symbol": P("e.g. AAPL, EUR/USD, BTC/USD")}, ["symbol"], method="GET", path="/price", query={"symbol": "{symbol}"}),
         T("quote", "Quote with day range and change.", {"symbol": P("e.g. AAPL")}, ["symbol"], method="GET", path="/quote", query={"symbol": "{symbol}"}),
         T("history", "Daily closing history.", {"symbol": P("e.g. AAPL"), "days": P("How many days (max 60)", "integer")}, ["symbol"], method="GET", path="/time_series", query={"symbol": "{symbol}", "interval": "1day", "outputsize": "{days|10}"}, pick="values")],
        test={"method": "GET", "path": "/api_usage"}),

    # ---------------- social, video and AI ----------------
    app("mastodon", "Mastodon", "Social media", "🐘", "Post to Mastodon and read your timeline.",
        [F("url", "Server address", "Your instance, e.g. https://mastodon.social.", validate=r"https://[A-Za-z0-9.-]+"), F("access_token", "Access token", "Preferences → Development → New application (scopes read and write:statuses).", secret=True)],
        {"type": "bearer", "field": "access_token"}, "{cfg.url}",
        [T("post", "Post a status.", {"text": P("What to post (max 500 characters)"), "visibility": P("public, unlisted, private or direct")}, ["text"], True, method="POST", path="/api/v1/statuses", body={"status": "{text}", "visibility": "{visibility|public}"}, pick={"path": "", "fields": []}, result={"id": "{s0.id}", "url": "{s0.url}"}),
         T("home_timeline", "Latest posts from people you follow.", {}, [], method="GET", path="/api/v1/timelines/home", query={"limit": "10"}, pick={"path": "", "fields": ["account.acct", "content", "created_at", "url"]}),
         T("search_posts", "Search posts.", {"query": P("Words to find")}, ["query"], method="GET", path="/api/v2/search", query={"q": "{query}", "type": "statuses", "limit": "8"}, pick={"path": "statuses", "fields": ["account.acct", "content", "created_at", "url"]})],
        test={"method": "GET", "path": "/api/v1/accounts/verify_credentials"}, dynamic_url=True),
    app("twitch", "Twitch", "Social media", "🎮", "Find channels, see who is live and what is popular on Twitch.",
        [F("client_id", "Client ID", "dev.twitch.tv/console → your application."), F("client_secret", "Client secret", secret=True)],
        {"type": "oauth2_client", "token_url": "https://id.twitch.tv/oauth2/token", "data": {"grant_type": "client_credentials", "client_id": "{cfg.client_id}", "client_secret": "{cfg.client_secret}"}}, "https://api.twitch.tv/helix",
        [T("search_channels", "Search channels.", {"query": P("Channel name or topic")}, ["query"], method="GET", path="/search/channels", query={"query": "{query}", "first": "8"}, pick={"path": "data[]", "fields": ["broadcaster_login", "display_name", "is_live", "game_name", "title"]}),
         T("stream_status", "Is a channel live?", {"login": P("Channel login name")}, ["login"], method="GET", path="/streams", query={"user_login": "{login}"}, pick={"path": "data[]", "fields": ["user_name", "title", "game_name", "viewer_count", "started_at"]}),
         T("top_games", "Most watched games right now.", {}, [], method="GET", path="/games/top", query={"first": "10"}, pick={"path": "data[]", "fields": ["id", "name"]})],
        test={"method": "GET", "path": "/games/top", "query": {"first": "1"}}, headers={"Client-Id": "{cfg.client_id}"}),
    app("vimeo", "Vimeo", "Social media", "🎬", "List and search videos on Vimeo.",
        [F("access_token", "Access token", "developer.vimeo.com → your app → Authentication.", secret=True)],
        {"type": "bearer", "field": "access_token"}, "https://api.vimeo.com",
        [T("my_videos", "Your latest videos.", {}, [], method="GET", path="/me/videos", query={"per_page": "10", "sort": "date"}, pick={"path": "data[]", "fields": ["name", "link", "duration", "created_time"]}),
         T("search", "Search public videos.", {"query": P("Words to find")}, ["query"], method="GET", path="/videos", query={"query": "{query}", "per_page": "8"}, pick={"path": "data[]", "fields": ["name", "link", "user.name", "duration"]})],
        test={"method": "GET", "path": "/me"}),
    app("deepgram", "Deepgram", "AI and data", "🎧", "Turn a recording at a web address into text with Deepgram.",
        [F("api_key", "API key", "console.deepgram.com → API keys.", secret=True)],
        {"type": "header", "name": "Authorization", "value": "Token {cfg.api_key}"}, "https://api.deepgram.com/v1",
        [T("transcribe", "Transcribe an audio or video file at a public address.", {"audio_url": P("Address of the recording")}, ["audio_url"], method="POST", path="/listen", query={"model": "nova-2", "smart_format": "true"}, body={"url": "{audio_url}"},
           pick={"path": "", "fields": []}, result={"transcript": "{s0.results.channels[0].alternatives[0].transcript}", "seconds": "{s0.metadata.duration}"})],
        test={"method": "GET", "path": "/projects"}),
    app("replicate", "Replicate", "AI and data", "🔁", "Run an AI model on Replicate (images, audio, text).",
        [F("token", "API token", "replicate.com → Account → API tokens.", secret=True)],
        {"type": "bearer", "field": "token"}, "https://api.replicate.com/v1",
        [T("run_model", "Run a model and wait for the result.", {"owner": P("Model owner, e.g. black-forest-labs"), "model": P("Model name, e.g. flux-schnell"), "input": P("The model's inputs as an object, e.g. {\"prompt\": \"a red bicycle\"}", "object")}, ["owner", "model", "input"], True,
           method="POST", path="/models/{owner}/{model}/predictions", headers={"Prefer": "wait"}, body={"input": "{input}"}, pick={"path": "", "fields": []}, result={"id": "{s0.id}", "status": "{s0.status}", "output": "{s0.output}", "error": "{s0.error}"}),
         T("get_prediction", "Check a prediction.", {"id": P("Prediction ID")}, ["id"], method="GET", path="/predictions/{id}", pick={"path": "", "fields": []}, result={"status": "{s0.status}", "output": "{s0.output}", "error": "{s0.error}"})],
        test={"method": "GET", "path": "/account"}),
    app("ocr_space", "OCR.space", "AI and data", "🔤", "Read the text in an image or scanned document at a web address.",
        [F("api_key", "API key", "ocr.space/ocrapi → free API key.", secret=True)],
        {"type": "query", "name": "apikey", "field": "api_key"}, "https://api.ocr.space",
        [T("read_image", "Extract text from an image or PDF at a public address.", {"url": P("Address of the image or PDF"), "language": P("3-letter code, e.g. eng, fra, deu (default eng)")}, ["url"], method="GET", path="/parse/imageurl",
           query={"url": "{url}", "language": "{language|eng}", "OCREngine": "2"}, pick={"path": "", "fields": []}, result={"text": "{s0.ParsedResults[0].ParsedText}", "error": "{s0.ErrorMessage}"})],
        test={"method": "GET", "path": "/parse/imageurl", "query": {"url": "https://example.com/none.png"}}),

    # ---------------- keyless public data (built in) ----------------
    app("jina_reader", "Web page reader", "Built in", "📄", "Read any web page as clean text, with the menus and adverts removed (Jina Reader).", [], {"type": "none"}, "https://r.jina.ai",
        [T("read", "Read a page as clean text.", {"url": P("The page address, starting with http")}, ["url"], method="GET", path="/{+url}", transform=_jina)], builtin=True, headers={"Accept": "application/json"}),
    app("europe_pmc", "Life-science papers (Europe PMC)", "Built in", "🧪", "Search biomedical and life-science papers, including free full text.", [], {"type": "none"}, "https://www.ebi.ac.uk/europepmc/webservices/rest",
        [T("search", "Search papers.", {"query": P("Topic, title or author")}, ["query"], method="GET", path="/search", query={"query": "{query}", "format": "json", "pageSize": "6", "resultType": "lite"},
           pick={"path": "resultList.result[]", "fields": ["title", "authorString", "journalTitle", "pubYear", "doi", "isOpenAccess"]})], builtin=True),
    app("clinical_trials", "Clinical trials (ClinicalTrials.gov)", "Built in", "🏥", "Find clinical studies by condition or treatment.", [], {"type": "none"}, "https://clinicaltrials.gov/api/v2",
        [T("search", "Search studies.", {"query": P("Condition, drug or keyword")}, ["query"], method="GET", path="/studies", query={"query.term": "{query}", "pageSize": "6"}, transform=_ctgov)], builtin=True),
    app("open_fda", "Medicine labels (openFDA)", "Built in", "💊", "Look up what a medicine is for and its warnings, from US drug labels.", [], {"type": "none"}, "https://api.fda.gov",
        [T("drug_label", "Find a medicine's label.", {"name": P("Brand or generic name, e.g. ibuprofen")}, ["name"], method="GET", path="/drug/label.json", query={"search": "openfda.generic_name:\"{name}\" OR openfda.brand_name:\"{name}\"", "limit": "3"}, transform=_openfda)], builtin=True),
    app("tfl", "London transport (TfL)", "Built in", "🚇", "Live Tube, Overground, DLR and Elizabeth line status, and stop look-ups.", [], {"type": "none"}, "https://api.tfl.gov.uk",
        [T("line_status", "Current status of the lines.", {}, [], method="GET", path="/Line/Mode/tube,overground,dlr,elizabeth-line/Status", transform=_tfl_status),
         T("find_stop", "Find a station or stop.", {"query": P("Name, e.g. King's Cross")}, ["query"], method="GET", path="/StopPoint/Search", query={"query": "{query}"}, transform=_tfl_stops),
         T("arrivals", "Next arrivals at a stop.", {"stop_id": P("Stop ID from find_stop")}, ["stop_id"], method="GET", path="/StopPoint/{stop_id}/Arrivals", pick={"path": "", "fields": ["lineName", "destinationName", "platformName", "timeToStation"]})], builtin=True),
    app("flood_warnings", "UK flood warnings", "Built in", "🌊", "Current flood warnings and alerts in England (Environment Agency).", [], {"type": "none"}, "https://environment.data.gov.uk/flood-monitoring",
        [T("current", "Current warnings and alerts.", {"min_severity": P("1 severe warning … 4 no longer in force (default 3, warnings and alerts)", "integer")}, [], method="GET", path="/id/floods", query={"min-severity": "{min_severity|3}"}, transform=_flood)], builtin=True),
    app("docker_hub", "Docker images (Docker Hub)", "Built in", "🐳", "Search container images and see how popular they are.", [], {"type": "none"}, "https://hub.docker.com/v2",
        [T("search", "Search images.", {"query": P("Image name or topic")}, ["query"], method="GET", path="/search/repositories", query={"query": "{query}", "page_size": "8"}, transform=_docker)], builtin=True),
]


# every action of these keyless apps was run against the live service with real sample inputs when this part was written
_LIVE_TESTED = {"jina_reader", "europe_pmc", "clinical_trials", "open_fda", "tfl", "flood_warnings", "docker_hub"}
for _a in APPS:
    if _a["id"] in _LIVE_TESTED:
        _a["verified"] = True

"""The app catalog: every connectable app described as data (see integrations.py for how it is run).
Written from each service's public API documentation. Apps marked unverified have not been exercised against a live account yet:
use 'Test connection' after connecting. The no-key apps (currency rates, public holidays) are exercised in tests."""


def F(key, label, help="", secret=False, required=True, default=None, validate=None, placeholder=""):
    f = {"key": key, "label": label, "help": help, "secret": secret, "required": required, "placeholder": placeholder}
    if default is not None:
        f["default"] = default
    if validate:
        f["validate"] = validate
    return f


def P(desc, type="string", **kw):
    return {"type": type, "description": desc, **kw}


def T(name, description, params=None, required=None, write=False, **req):
    return {"name": name, "description": description, "params": params or {}, "required": required or [], "write": write, **req}


def app(id, name, category, icon, description, fields, auth, base, tools, test=None, help="", verified=False, **extra):
    return {"id": id, "name": name, "category": category, "icon": icon, "description": description, "fields": fields, "auth": auth, "base": base,
            "tools": tools, "test": test, "help": help, "verified": verified, **extra}


# ---- small helpers for built-in tools that reshape their answer ----
def _events(out, args):
    cur = {e.get("eventAction"): e.get("eventDate") for e in (out.get("events") or []) if isinstance(e, dict)}
    return {"domain": out.get("ldhName"), "registered": cur.get("registration"), "expires": cur.get("expiration"), "last_changed": cur.get("last changed"),
            "status": out.get("status"), "nameservers": [n.get("ldhName") for n in out.get("nameservers", [])][:6]}


def _bank_holidays(out, args):
    import time as _t
    div = args.get("region") or "england-and-wales"
    ev = (out.get(div) or out.get("england-and-wales") or {}).get("events", [])
    today = _t.strftime("%Y-%m-%d")
    return [{"date": e["date"], "name": e["title"]} for e in ev if e["date"] >= today][:12]


def _drive(out, args):
    r = (out.get("routes") or [{}])[0]
    m, s = r.get("distance", 0), r.get("duration", 0)
    return {"distance_km": round(m / 1000, 1), "distance_miles": round(m / 1609.34, 1), "drive_minutes": round(s / 60)}


def _mx(out, args):
    ans = [a for a in (out.get("Answer") or []) if a.get("type") == 15]
    return {"can_receive_email": bool(ans), "mail_servers": [a.get("data") for a in ans][:5]}


def _ip(out, args):
    return {k: out.get(k) for k in ("ip", "success", "country", "country_code", "region", "city", "latitude", "longitude")} | {"timezone": (out.get("timezone") or {}).get("id"), "isp": (out.get("connection") or {}).get("isp")}


_GOOGLE_FIELDS = [
    F("client_id", "OAuth client ID", "From Google Cloud Console → APIs & Services → Credentials (type: Web application)."),
    F("client_secret", "OAuth client secret", secret=True),
    F("refresh_token", "Refresh token", "Create it once at developers.google.com/oauthplayground: tick the gear, 'Use your own OAuth credentials', authorise the scopes below, then copy the refresh token.", secret=True),
]
_GOOGLE_AUTH = {"type": "oauth2_refresh", "token_url": "https://oauth2.googleapis.com/token",
                "data": {"client_id": "{cfg.client_id}", "client_secret": "{cfg.client_secret}", "refresh_token": "{cfg.refresh_token}", "grant_type": "refresh_token"}}
_ADF = lambda var: {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "{" + var + "}"}]}]}

APPS = [
    # ---------------- messaging ----------------
    app("slack", "Slack", "Messaging", "🟣", "Post messages to Slack channels and read recent ones.",
        [F("token", "Bot token", "Starts with xoxb-. Create a Slack app, add chat:write, channels:read and channels:history, install it, copy the Bot User OAuth Token.", secret=True)],
        {"type": "bearer", "field": "token"}, "https://slack.com/api",
        [T("post_message", "Post a message to a channel.", {"channel": P("Channel name like #general or its ID"), "text": P("Message")}, ["channel", "text"], True, method="POST", path="/chat.postMessage", body={"channel": "{channel}", "text": "{text}"}, pick="ts"),
         T("list_channels", "List public channels.", {}, [], method="GET", path="/conversations.list", query={"limit": "100", "types": "public_channel"}, pick={"path": "channels[]", "fields": ["id", "name"]}),
         T("read_channel", "Read the latest messages in a channel (by channel ID).", {"channel_id": P("Channel ID"), "limit": P("How many", "integer")}, ["channel_id"], method="GET", path="/conversations.history", query={"channel": "{channel_id}", "limit": "{limit}"}, pick={"path": "messages[]", "fields": ["user", "text", "ts"]})],
        test={"method": "POST", "path": "/auth.test"}),
    app("discord", "Discord (webhook)", "Messaging", "🎮", "Post messages into a Discord channel.",
        [F("url", "Webhook URL", "Channel settings → Integrations → Webhooks → Copy URL.", secret=True, validate=r"https://(canary\.|ptb\.)?(discord|discordapp)\.com/api/webhooks/.+")],
        {"type": "none"}, "{cfg.url}", [T("post_message", "Post a message.", {"text": P("Message")}, ["text"], True, method="POST", path="", body={"content": "{text}"})]),
    app("teams_webhook", "Microsoft Teams (webhook)", "Messaging", "🟦", "Post messages into a Teams channel.",
        [F("url", "Webhook URL", "Teams channel → Workflows (or Connectors) → incoming webhook → copy the URL.", secret=True, validate=r"https://[A-Za-z0-9.-]+\.(office\.com|office365\.com|azure\.com|powerplatform\.com|logic\.azure\.com)/.+")],
        {"type": "none"}, "{cfg.url}", [T("post_message", "Post a message.", {"text": P("Message")}, ["text"], True, method="POST", path="", body={"text": "{text}"})]),
    app("slack_webhook", "Slack (simple webhook)", "Messaging", "🔔", "Post to one Slack channel without a full app.",
        [F("url", "Incoming webhook URL", "Slack → Apps → Incoming Webhooks → add to a channel → copy URL.", secret=True, validate=r"https://hooks\.slack\.com/services/.+")],
        {"type": "none"}, "{cfg.url}", [T("post_message", "Post a message to the channel.", {"text": P("Message")}, ["text"], True, method="POST", path="", body={"text": "{text}"})]),

    # ---------------- email ----------------
    app("sendgrid", "SendGrid", "Email", "📧", "Send email through SendGrid (for newsletters, notices and bulk mail).",
        [F("api_key", "API key", "SendGrid → Settings → API Keys (Mail Send permission).", secret=True), F("from_email", "From address", "A sender you have verified in SendGrid.")],
        {"type": "bearer", "field": "api_key"}, "https://api.sendgrid.com/v3",
        [T("send_email", "Send an email.", {"to": P("Recipient address"), "subject": P("Subject"), "text": P("Plain-text body")}, ["to", "subject", "text"], True,
           method="POST", path="/mail/send", body={"personalizations": [{"to": [{"email": "{to}"}]}], "from": {"email": "{cfg.from_email}"}, "subject": "{subject}", "content": [{"type": "text/plain", "value": "{text}"}]})],
        test={"method": "GET", "path": "/scopes"}),
    app("mailgun", "Mailgun", "Email", "📮", "Send email through Mailgun.",
        [F("api_key", "Private API key", secret=True), F("domain", "Sending domain", "e.g. mg.yourcompany.com"), F("region", "API host", "Use api.eu.mailgun.net for EU accounts.", default="api.mailgun.net", required=False), F("from_email", "From address", "e.g. hello@mg.yourcompany.com")],
        {"type": "basic", "user": "api", "pass": "{cfg.api_key}"}, "https://{cfg.region}/v3/{cfg.domain}",
        [T("send_email", "Send an email.", {"to": P("Recipient"), "subject": P("Subject"), "text": P("Body")}, ["to", "subject", "text"], True, method="POST", path="/messages", encoding="form", body={"from": "{cfg.from_email}", "to": "{to}", "subject": "{subject}", "text": "{text}"})]),
    app("mailchimp", "Mailchimp", "Email", "🐵", "Manage audiences: look up, add and tag subscribers.",
        [F("api_key", "API key", "Account → Extras → API keys. It ends with a data centre like -us21.", secret=True, validate=r".+-[a-z]+\d+"), ],
        {"type": "basic", "user": "deskmates", "pass": "{cfg.api_key}"}, "https://{cfg.dc}.api.mailchimp.com/3.0",
        [T("list_audiences", "List your audiences.", {}, [], method="GET", path="/lists", query={"count": "20"}, pick={"path": "lists[]", "fields": ["id", "name", "stats.member_count"]}),
         T("add_subscriber", "Add a subscriber to an audience.", {"audience_id": P("Audience ID"), "email": P("Email address"), "first_name": P("First name (optional)")}, ["audience_id", "email"], True,
           method="POST", path="/lists/{audience_id}/members", body={"email_address": "{email}", "status": "subscribed", "merge_fields": {"FNAME": "{first_name}"}})],
        test={"method": "GET", "path": "/ping"}, derive={"dc": ("api_key", r"-([a-z]+\d+)$")}),

    # ---------------- CRM and sales ----------------
    app("hubspot", "HubSpot", "CRM", "🟠", "Look up and update contacts, deals and notes in HubSpot.",
        [F("token", "Private app token", "Settings → Integrations → Private apps. Give it CRM contacts, deals and notes read/write.", secret=True)],
        {"type": "bearer", "field": "token"}, "https://api.hubapi.com",
        [T("search_contacts", "Find contacts by name, email or phone.", {"query": P("Text to search for")}, ["query"], method="POST", path="/crm/v3/objects/contacts/search",
           body={"query": "{query}", "limit": 10, "properties": ["firstname", "lastname", "email", "phone", "lifecyclestage"]}, pick={"path": "results[]", "fields": ["id", "properties"]}),
         T("create_contact", "Create a contact.", {"email": P("Email"), "firstname": P("First name"), "lastname": P("Last name"), "phone": P("Phone")}, ["email"], True,
           method="POST", path="/crm/v3/objects/contacts", body={"properties": {"email": "{email}", "firstname": "{firstname}", "lastname": "{lastname}", "phone": "{phone}"}}, pick="id"),
         T("update_contact", "Update a contact's details.", {"contact_id": P("Contact ID"), "properties": P("Object of HubSpot property names and new values", "object")}, ["contact_id", "properties"], True,
           method="PATCH", path="/crm/v3/objects/contacts/{contact_id}", body={"properties": "{properties}"}),
         T("add_note", "Add a note to a contact.", {"contact_id": P("Contact ID"), "note": P("Note text")}, ["contact_id", "note"], True,
           method="POST", path="/crm/v3/objects/notes", body={"properties": {"hs_note_body": "{note}", "hs_timestamp": "{now}"}, "associations": [{"to": {"id": "{contact_id}"}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}]}),
         T("list_deals", "List recent deals.", {}, [], method="GET", path="/crm/v3/objects/deals", query={"limit": "20", "properties": "dealname,amount,dealstage,closedate"}, pick={"path": "results[]", "fields": ["id", "properties"]}),
         T("create_deal", "Create a deal.", {"name": P("Deal name"), "amount": P("Amount (number)", "number"), "stage": P("Deal stage ID (optional)")}, ["name"], True,
           method="POST", path="/crm/v3/objects/deals", body={"properties": {"dealname": "{name}", "amount": "{amount}", "dealstage": "{stage}"}}, pick="id")],
        test={"method": "GET", "path": "/crm/v3/objects/contacts", "query": {"limit": "1"}}),
    app("pipedrive", "Pipedrive", "CRM", "🟢", "Search people, create deals and add notes in Pipedrive.",
        [F("api_token", "API token", "Personal preferences → API.", secret=True)],
        {"type": "query", "name": "api_token", "field": "api_token"}, "https://api.pipedrive.com/v1",
        [T("search_people", "Search people.", {"term": P("Name, email or phone")}, ["term"], method="GET", path="/persons/search", query={"term": "{term}", "limit": "10"}, pick="data.items"),
         T("create_person", "Create a person.", {"name": P("Full name"), "email": P("Email"), "phone": P("Phone")}, ["name"], True, method="POST", path="/persons", body={"name": "{name}", "email": "{email}", "phone": "{phone}"}, pick="data.id"),
         T("list_deals", "List open deals.", {}, [], method="GET", path="/deals", query={"status": "open", "limit": "20"}, pick={"path": "data[]", "fields": ["id", "title", "value", "currency", "status"]}),
         T("create_deal", "Create a deal.", {"title": P("Deal title"), "value": P("Value", "number"), "person_id": P("Person ID", "integer")}, ["title"], True, method="POST", path="/deals", body={"title": "{title}", "value": "{value}", "person_id": "{person_id}"}, pick="data.id"),
         T("add_note", "Add a note.", {"content": P("Note"), "person_id": P("Person ID", "integer"), "deal_id": P("Deal ID", "integer")}, ["content"], True, method="POST", path="/notes", body={"content": "{content}", "person_id": "{person_id}", "deal_id": "{deal_id}"})],
        test={"method": "GET", "path": "/users/me"}),
    app("zendesk", "Zendesk", "Support", "🎧", "Search, create and update support tickets.",
        [F("subdomain", "Subdomain", "The part before .zendesk.com", validate=r"[a-zA-Z0-9-]+"), F("email", "Agent email"), F("api_token", "API token", "Admin Center → Apps and integrations → APIs → Zendesk API.", secret=True)],
        {"type": "basic", "user": "{cfg.email}/token", "pass": "{cfg.api_token}"}, "https://{cfg.subdomain}.zendesk.com/api/v2",
        [T("search_tickets", "Search tickets.", {"query": P("Zendesk search query, e.g. 'status:open priority:high'")}, ["query"], method="GET", path="/search.json", query={"query": "type:ticket {query}"}, pick={"path": "results[]", "fields": ["id", "subject", "status", "priority", "requester_id"]}),
         T("create_ticket", "Create a ticket.", {"subject": P("Subject"), "body": P("Description"), "requester_email": P("Customer's email")}, ["subject", "body"], True,
           method="POST", path="/tickets.json", body={"ticket": {"subject": "{subject}", "comment": {"body": "{body}"}, "requester": {"email": "{requester_email}"}}}, pick="ticket.id"),
         T("add_comment", "Reply to a ticket.", {"ticket_id": P("Ticket ID", "integer"), "body": P("Reply text"), "public": P("Visible to the customer?", "boolean")}, ["ticket_id", "body"], True,
           method="PUT", path="/tickets/{ticket_id}.json", body={"ticket": {"comment": {"body": "{body}", "public": "{public}"}}})],
        test={"method": "GET", "path": "/users/me.json"}),
    app("shopify", "Shopify", "E-commerce", "🛍️", "Look up orders, customers and products in a Shopify store.",
        [F("shop", "Store handle", "The part before .myshopify.com", validate=r"[a-z0-9-]+"), F("access_token", "Admin API access token", "Settings → Apps → Develop apps → create an app with read orders/customers/products.", secret=True),
         F("api_version", "API version", "Use a current version from Shopify's docs.", default="2026-07", required=False)],
        {"type": "header", "name": "X-Shopify-Access-Token", "value": "{cfg.access_token}"}, "https://{cfg.shop}.myshopify.com/admin/api/{cfg.api_version}",
        [T("list_orders", "List recent orders.", {"status": P("any, open, closed or cancelled")}, [], method="GET", path="/orders.json", query={"status": "{status}", "limit": "20"}, pick={"path": "orders[]", "fields": ["id", "name", "email", "total_price", "financial_status", "fulfillment_status", "created_at"]}),
         T("find_customers", "Search customers.", {"query": P("Name, email or phone")}, ["query"], method="GET", path="/customers/search.json", query={"query": "{query}"}, pick={"path": "customers[]", "fields": ["id", "first_name", "last_name", "email", "orders_count", "total_spent"]}),
         T("list_products", "List products.", {}, [], method="GET", path="/products.json", query={"limit": "30"}, pick={"path": "products[]", "fields": ["id", "title", "status", "vendor"]})],
        test={"method": "GET", "path": "/shop.json"}),
    app("stripe", "Stripe", "Payments", "💳", "Create payment links, look up customers and payments.",
        [F("secret_key", "Secret or restricted key", "Developers → API keys. A restricted key with only what you need is safest.", secret=True, validate=r"(sk|rk)_(live|test)_.+")],
        {"type": "bearer", "field": "secret_key"}, "https://api.stripe.com/v1",
        [T("balance", "Check the account balance.", {}, [], method="GET", path="/balance"),
         T("find_customers", "Find customers by email.", {"email": P("Customer email")}, ["email"], method="GET", path="/customers", query={"email": "{email}", "limit": "5"}, pick={"path": "data[]", "fields": ["id", "name", "email", "created"]}),
         T("create_customer", "Create a customer.", {"name": P("Name"), "email": P("Email")}, ["email"], True, method="POST", path="/customers", encoding="form", body={"name": "{name}", "email": "{email}"}, pick="id"),
         T("create_payment_link", "Create a one-off payment link for an amount.", {"name": P("What it is for"), "amount_cents": P("Amount in the smallest unit, e.g. 4999 for 49.99", "integer"), "currency": P("3-letter code, e.g. gbp")}, ["name", "amount_cents", "currency"], True,
           steps=[{"method": "POST", "path": "/products", "encoding": "form", "body": {"name": "{name}"}},
                  {"method": "POST", "path": "/prices", "encoding": "form", "body": {"unit_amount": "{amount_cents}", "currency": "{currency}", "product": "{s0.id}"}},
                  {"method": "POST", "path": "/payment_links", "encoding": "form", "body": {"line_items": [{"price": "{s1.id}", "quantity": 1}]}}], result={"url": "{s2.url}", "id": "{s2.id}"}),
         T("recent_payments", "List recent payments.", {}, [], method="GET", path="/payment_intents", query={"limit": "10"}, pick={"path": "data[]", "fields": ["id", "amount", "currency", "status", "created"]})],
        test={"method": "GET", "path": "/balance"}),

    # ---------------- calendar and meetings ----------------
    app("google_calendar", "Google Calendar", "Calendar", "🗓️", "Read the calendar, find free time, create and cancel events (with optional Google Meet links).",
        _GOOGLE_FIELDS + [F("calendar_id", "Calendar ID", "Use primary for the main calendar.", default="primary", required=False),
                          F("my_email", "This calendar's own email address", "Needed to accept or decline invitations.", required=False)], _GOOGLE_AUTH, "https://www.googleapis.com/calendar/v3/calendars/{cfg.calendar_id}",
        [T("list_events", "List events between two times.", {"from_time": P("Start, ISO like 2026-10-15T00:00:00Z"), "to_time": P("End, ISO (optional)"), "limit": P("How many", "integer")}, ["from_time"],
           method="GET", path="/events", query={"timeMin": "{from_time}", "timeMax": "{to_time}", "maxResults": "{limit}", "singleEvents": "true", "orderBy": "startTime"},
           pick={"path": "items[]", "fields": ["id", "summary", "start.dateTime", "start.date", "end.dateTime", "location", "htmlLink", "hangoutLink", "attendees"]}),
         T("respond_to_invite", "Accept, decline or tentatively accept an invitation you received (only your own answer changes).", {"event_id": P("Event ID"), "response": P("accepted, declined or tentative")}, ["event_id", "response"], True,
           method="PATCH", path="/events/{event_id}", query={"sendUpdates": "all"}, body={"attendees": [{"email": "{cfg.my_email}", "responseStatus": "{response}"}]}),
         T("create_event", "Create an event.", {"title": P("Title"), "start": P("Start, ISO with offset e.g. 2026-10-15T14:00:00+01:00"), "end": P("End, ISO with offset"), "attendee_email": P("Guest's email (optional)"), "description": P("Notes (optional)"), "location": P("Where (optional)")}, ["title", "start", "end"], True,
           method="POST", path="/events", query={"sendUpdates": "all"}, body={"summary": "{title}", "description": "{description}", "location": "{location}", "start": {"dateTime": "{start}"}, "end": {"dateTime": "{end}"}, "attendees": [{"email": "{attendee_email}"}]},
           pick={"path": "", "fields": []}, result={"id": "{s0.id}", "link": "{s0.htmlLink}"}),
         T("create_event_with_meet", "Create an event with a Google Meet video link.", {"title": P("Title"), "start": P("Start, ISO with offset"), "end": P("End, ISO with offset"), "attendee_email": P("Guest's email (optional)")}, ["title", "start", "end"], True,
           method="POST", path="/events", query={"conferenceDataVersion": "1", "sendUpdates": "all"}, body={"summary": "{title}", "start": {"dateTime": "{start}"}, "end": {"dateTime": "{end}"}, "attendees": [{"email": "{attendee_email}"}],
                                                                                                                  "conferenceData": {"createRequest": {"requestId": "{uuid}", "conferenceSolutionKey": {"type": "hangoutsMeet"}}}},
           result={"id": "{s0.id}", "link": "{s0.htmlLink}", "meet": "{s0.hangoutLink}"}),
         T("cancel_event", "Cancel (delete) an event.", {"event_id": P("Event ID")}, ["event_id"], True, method="DELETE", path="/events/{event_id}", query={"sendUpdates": "all"})],
        test={"method": "GET", "path": "/events", "query": {"maxResults": "1"}}, help="Scope needed: https://www.googleapis.com/auth/calendar"),
    app("google_sheets", "Google Sheets", "Data and documents", "📊", "Read, append and update rows in Google spreadsheets.",
        _GOOGLE_FIELDS, _GOOGLE_AUTH, "https://sheets.googleapis.com/v4/spreadsheets",
        [T("sheet_info", "List the tabs in a spreadsheet.", {"spreadsheet_id": P("The long ID in the sheet's URL")}, ["spreadsheet_id"], method="GET", path="/{spreadsheet_id}", query={"fields": "properties.title,sheets.properties.title"}),
         T("read_range", "Read cells, e.g. range 'Sheet1!A1:D20'.", {"spreadsheet_id": P("Spreadsheet ID"), "range": P("A1 range with tab name")}, ["spreadsheet_id", "range"], method="GET", path="/{spreadsheet_id}/values/{range}", pick="values"),
         T("append_rows", "Add rows at the end of a table.", {"spreadsheet_id": P("Spreadsheet ID"), "range": P("Tab or range, e.g. Sheet1!A1"), "rows": P("List of rows, each a list of cell values", "array")}, ["spreadsheet_id", "range", "rows"], True,
           method="POST", path="/{spreadsheet_id}/values/{range}:append", query={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}, body={"values": "{rows}"}, pick="updates"),
         T("update_range", "Overwrite cells in a range.", {"spreadsheet_id": P("Spreadsheet ID"), "range": P("A1 range with tab name"), "rows": P("List of rows", "array")}, ["spreadsheet_id", "range", "rows"], True,
           method="PUT", path="/{spreadsheet_id}/values/{range}", query={"valueInputOption": "USER_ENTERED"}, body={"values": "{rows}"})],
        help="Scope needed: https://www.googleapis.com/auth/spreadsheets"),
    app("google_drive", "Google Drive and Docs", "Data and documents", "📁", "Search Drive and read the text of Google Docs.",
        _GOOGLE_FIELDS, _GOOGLE_AUTH, "https://www.googleapis.com/drive/v3",
        [T("search_files", "Search files by name or content.", {"text": P("Words to search for")}, ["text"], method="GET", path="/files", query={"q": "fullText contains '{text}' and trashed=false", "pageSize": "20", "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"}, pick="files"),
         T("read_doc_text", "Read a Google Doc as plain text.", {"file_id": P("File ID from search_files")}, ["file_id"], method="GET", path="/files/{file_id}/export", query={"mimeType": "text/plain"})],
        test={"method": "GET", "path": "/about", "query": {"fields": "user"}}, help="Scope needed: https://www.googleapis.com/auth/drive.readonly"),
    app("microsoft365", "Microsoft 365 (Outlook calendar and mail)", "Calendar", "🔷", "Read and create Outlook calendar events and send email from a Microsoft account.",
        [F("tenant", "Tenant ID (or 'common')", "Entra ID → Overview.", default="common", required=False), F("client_id", "Application (client) ID"), F("client_secret", "Client secret", secret=True),
         F("refresh_token", "Refresh token", "Obtained once through the sign-in flow with scopes offline_access Calendars.ReadWrite Mail.Send.", secret=True)],
        {"type": "oauth2_refresh", "token_url": "https://login.microsoftonline.com/{cfg.tenant}/oauth2/v2.0/token",
         "data": {"client_id": "{cfg.client_id}", "client_secret": "{cfg.client_secret}", "refresh_token": "{cfg.refresh_token}", "grant_type": "refresh_token", "scope": "offline_access Calendars.ReadWrite Mail.Send"}},
        "https://graph.microsoft.com/v1.0/me",
        [T("list_events", "List calendar events between two times.", {"from_time": P("Start ISO"), "to_time": P("End ISO")}, ["from_time", "to_time"], method="GET", path="/calendarView", query={"startDateTime": "{from_time}", "endDateTime": "{to_time}", "$top": "30", "$select": "subject,start,end,location"}, pick="value"),
         T("create_event", "Create a calendar event.", {"subject": P("Title"), "start": P("Start ISO, no offset"), "end": P("End ISO, no offset"), "timezone": P("e.g. Europe/London"), "attendee_email": P("Guest (optional)")}, ["subject", "start", "end", "timezone"], True,
           method="POST", path="/events", body={"subject": "{subject}", "start": {"dateTime": "{start}", "timeZone": "{timezone}"}, "end": {"dateTime": "{end}", "timeZone": "{timezone}"}, "attendees": [{"emailAddress": {"address": "{attendee_email}"}, "type": "required"}]}, result={"id": "{s0.id}", "link": "{s0.webLink}"}),
         T("send_mail", "Send an email.", {"to": P("Recipient"), "subject": P("Subject"), "text": P("Body")}, ["to", "subject", "text"], True,
           method="POST", path="/sendMail", body={"message": {"subject": "{subject}", "body": {"contentType": "Text", "content": "{text}"}, "toRecipients": [{"emailAddress": {"address": "{to}"}}]}, "saveToSentItems": True})],
        test={"method": "GET", "path": ""}),
    app("zoom", "Zoom", "Calendar", "🎥", "Create and list Zoom meetings.",
        [F("account_id", "Account ID", "Zoom App Marketplace → build a Server-to-Server OAuth app."), F("client_id", "Client ID"), F("client_secret", "Client secret", secret=True), F("user", "Zoom user email", "The account meetings are created for.")],
        {"type": "oauth2_client", "token_url": "https://zoom.us/oauth/token?grant_type=account_credentials&account_id={cfg.account_id}", "basic": ["{cfg.client_id}", "{cfg.client_secret}"], "data": {}}, "https://api.zoom.us/v2",
        [T("create_meeting", "Schedule a meeting.", {"topic": P("Title"), "start_time": P("ISO start, e.g. 2026-10-15T14:00:00"), "duration_minutes": P("Length", "integer"), "timezone": P("e.g. Europe/London")}, ["topic", "start_time"], True,
           method="POST", path="/users/{cfg.user}/meetings", body={"topic": "{topic}", "type": 2, "start_time": "{start_time}", "duration": "{duration_minutes}", "timezone": "{timezone}"}, result={"id": "{s0.id}", "join_url": "{s0.join_url}", "password": "{s0.password}"}),
         T("list_meetings", "List upcoming meetings.", {}, [], method="GET", path="/users/{cfg.user}/meetings", query={"type": "upcoming"}, pick={"path": "meetings[]", "fields": ["id", "topic", "start_time", "duration", "join_url"]})],
        test={"method": "GET", "path": "/users/{cfg.user}"}),
    app("calendly", "Calendly", "Calendar", "📆", "Create one-off booking links and see scheduled meetings.",
        [F("token", "Personal access token", "Integrations → API & Webhooks → Personal access tokens.", secret=True)], {"type": "bearer", "field": "token"}, "https://api.calendly.com",
        [T("list_event_types", "List your meeting types (with the URIs needed for booking links).", {}, [], steps=[{"method": "GET", "path": "/users/me"}, {"method": "GET", "path": "/event_types", "query": {"user": "{s0.resource.uri}", "active": "true"}}], pick={"path": "collection[]", "fields": ["name", "uri", "duration", "scheduling_url"]}),
         T("create_booking_link", "Create a single-use booking link for a meeting type.", {"event_type_uri": P("URI from list_event_types")}, ["event_type_uri"], True, method="POST", path="/scheduling_links", body={"max_event_count": 1, "owner": "{event_type_uri}", "owner_type": "EventType"}, pick="resource"),
         T("upcoming_meetings", "List upcoming scheduled meetings.", {"from_time": P("ISO start time")}, ["from_time"], steps=[{"method": "GET", "path": "/users/me"}, {"method": "GET", "path": "/scheduled_events", "query": {"user": "{s0.resource.uri}", "min_start_time": "{from_time}", "count": "20", "status": "active"}}],
           pick={"path": "collection[]", "fields": ["name", "start_time", "end_time", "status"]})],
        test={"method": "GET", "path": "/users/me"}),

    # ---------------- projects, docs and developer tools ----------------
    app("notion", "Notion", "Data and documents", "⬜", "Search pages, read them, add text and create database items.",
        [F("token", "Integration token", "notion.so/profile/integrations → new integration. Then share pages or databases with it.", secret=True), F("title_property", "Database title column", "Name of the title column in your databases.", default="Name", required=False)],
        {"type": "bearer", "field": "token"}, "https://api.notion.com/v1", [
            T("search", "Search pages and databases.", {"query": P("Words to look for")}, ["query"], method="POST", path="/search", body={"query": "{query}", "page_size": 10}, pick={"path": "results[]", "fields": ["id", "object", "url"]}),
            T("read_page", "Read the blocks of a page.", {"page_id": P("Page ID")}, ["page_id"], method="GET", path="/blocks/{page_id}/children", query={"page_size": "100"}),
            T("add_text", "Add a paragraph to the end of a page.", {"page_id": P("Page ID"), "text": P("Text")}, ["page_id", "text"], True, method="PATCH", path="/blocks/{page_id}/children",
              body={"children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "{text}"}}]}}]}),
            T("create_database_item", "Add an item to a database.", {"database_id": P("Database ID"), "title": P("Title")}, ["database_id", "title"], True, method="POST", path="/pages",
              body={"parent": {"database_id": "{database_id}"}, "properties": {"{cfg.title_property}": {"title": [{"text": {"content": "{title}"}}]}}}, pick="url")],
        test={"method": "GET", "path": "/users/me"}, headers={"Notion-Version": "2022-06-28"}),
    app("airtable", "Airtable", "Data and documents", "🧮", "Read, add and update records in Airtable bases.",
        [F("token", "Personal access token", "airtable.com/create/tokens with data.records read/write.", secret=True), F("base_id", "Base ID", "Starts with app. See airtable.com/developers/web/api/introduction.", validate=r"app[A-Za-z0-9]+")],
        {"type": "bearer", "field": "token"}, "https://api.airtable.com/v0/{cfg.base_id}",
        [T("list_records", "List records in a table.", {"table": P("Table name"), "limit": P("How many", "integer"), "filter_formula": P("Airtable formula (optional)")}, ["table"], method="GET", path="/{table}", query={"maxRecords": "{limit}", "filterByFormula": "{filter_formula}"}, pick={"path": "records[]", "fields": ["id", "fields"]}),
         T("create_record", "Add a record.", {"table": P("Table name"), "fields": P("Object of field names and values", "object")}, ["table", "fields"], True, method="POST", path="/{table}", body={"fields": "{fields}"}, pick="id"),
         T("update_record", "Update a record.", {"table": P("Table name"), "record_id": P("Record ID"), "fields": P("Fields to change", "object")}, ["table", "record_id", "fields"], True, method="PATCH", path="/{table}/{record_id}", body={"fields": "{fields}"})],
        test={"method": "GET", "path": "https://api.airtable.com/v0/meta/whoami"}),
    app("github", "GitHub", "Developer tools", "🐙", "Read, create and comment on issues in a repository.",
        [F("token", "Personal access token", "Settings → Developer settings → fine-grained token with Issues read/write.", secret=True), F("repo", "Repository", "owner/name, e.g. acme/website", validate=r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")],
        {"type": "bearer", "field": "token"}, "https://api.github.com/repos/{+cfg.repo}",
        [T("list_issues", "List issues.", {"state": P("open, closed or all")}, [], method="GET", path="/issues", query={"state": "{state}", "per_page": "20"}, pick={"path": "", "fields": ["number", "title", "state", "html_url"]}),
         T("create_issue", "Create an issue.", {"title": P("Title"), "body": P("Description")}, ["title"], True, method="POST", path="/issues", body={"title": "{title}", "body": "{body}"}, result={"number": "{s0.number}", "url": "{s0.html_url}"}),
         T("comment_on_issue", "Comment on an issue.", {"issue_number": P("Issue number", "integer"), "body": P("Comment")}, ["issue_number", "body"], True, method="POST", path="/issues/{issue_number}/comments", body={"body": "{body}"})],
        test={"method": "GET", "path": ""}, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}),
    app("linear", "Linear", "Developer tools", "🔺", "List and create issues in Linear.",
        [F("api_key", "API key", "Settings → API → Personal API keys.", secret=True)], {"type": "header", "name": "Authorization", "value": "{cfg.api_key}"}, "https://api.linear.app",
        [T("list_teams", "List teams (with the IDs needed to create issues).", {}, [], method="POST", path="/graphql", body={"query": "{ teams { nodes { id name key } } }"}, pick="data.teams.nodes"),
         T("list_issues", "List recently updated issues.", {}, [], method="POST", path="/graphql", body={"query": "{ issues(first: 20, orderBy: updatedAt) { nodes { identifier title state { name } assignee { name } url } } }"}, pick="data.issues.nodes"),
         T("create_issue", "Create an issue.", {"team_id": P("Team ID from list_teams"), "title": P("Title"), "description": P("Description")}, ["team_id", "title"], True, method="POST", path="/graphql",
           body={"query": "mutation($teamId: String!, $title: String!, $description: String) { issueCreate(input: {teamId: $teamId, title: $title, description: $description}) { success issue { identifier url } } }", "variables": {"teamId": "{team_id}", "title": "{title}", "description": "{description}"}}, pick="data.issueCreate")],
        test={"method": "POST", "path": "/graphql", "body": {"query": "{ viewer { id name } }"}}),
    app("jira", "Jira", "Developer tools", "🔵", "Search issues, create issues and comment in Jira Cloud.",
        [F("site", "Site name", "The part before .atlassian.net", validate=r"[a-zA-Z0-9-]+"), F("email", "Your Atlassian email"), F("api_token", "API token", "id.atlassian.com/manage-profile/security/api-tokens", secret=True)],
        {"type": "basic", "user": "{cfg.email}", "pass": "{cfg.api_token}"}, "https://{cfg.site}.atlassian.net/rest/api/3",
        [T("search_issues", "Search with JQL.", {"jql": P("e.g. project = ABC AND status = 'In Progress'")}, ["jql"], method="GET", path="/search/jql", query={"jql": "{jql}", "fields": "summary,status,assignee", "maxResults": "20"}, pick="issues"),
         T("create_issue", "Create an issue.", {"project_key": P("e.g. ABC"), "summary": P("Title"), "description": P("Description"), "issue_type": P("Task, Bug, Story...")}, ["project_key", "summary", "description", "issue_type"], True,
           method="POST", path="/issue", body={"fields": {"project": {"key": "{project_key}"}, "summary": "{summary}", "issuetype": {"name": "{issue_type}"}, "description": _ADF("description")}}, pick="key"),
         T("add_comment", "Comment on an issue.", {"issue_key": P("e.g. ABC-12"), "comment": P("Text")}, ["issue_key", "comment"], True, method="POST", path="/issue/{issue_key}/comment", body={"body": _ADF("comment")})],
        test={"method": "GET", "path": "/myself"}),
    app("trello", "Trello", "Developer tools", "🟦", "Look at boards and create cards.",
        [F("key", "API key", "trello.com/power-ups/admin → your Power-Up → API key."), F("token", "Token", "Generate a token from the same page.", secret=True)],
        {"type": "query_multi", "params": {"key": "{cfg.key}", "token": "{cfg.token}"}}, "https://api.trello.com/1",
        [T("list_boards", "List your boards.", {}, [], method="GET", path="/members/me/boards", query={"fields": "name,url"}),
         T("list_cards", "List cards in a list.", {"list_id": P("List ID")}, ["list_id"], method="GET", path="/lists/{list_id}/cards", pick={"path": "", "fields": ["id", "name", "desc", "due"]}),
         T("create_card", "Create a card.", {"list_id": P("List ID"), "name": P("Card title"), "description": P("Details")}, ["list_id", "name"], True, method="POST", path="/cards", query={"idList": "{list_id}", "name": "{name}", "desc": "{description}"}, result={"id": "{s0.id}", "url": "{s0.url}"})],
        test={"method": "GET", "path": "/members/me"}),
    app("asana", "Asana", "Developer tools", "🔴", "List and create tasks in Asana.",
        [F("token", "Personal access token", "Developer console → Personal access token.", secret=True), F("workspace_gid", "Workspace ID", "Visible in the URL, or ask the API: GET /workspaces.")],
        {"type": "bearer", "field": "token"}, "https://app.asana.com/api/1.0",
        [T("my_tasks", "List your incomplete tasks.", {}, [], method="GET", path="/tasks", query={"assignee": "me", "workspace": "{cfg.workspace_gid}", "completed_since": "now", "limit": "30", "opt_fields": "name,due_on"}, pick="data"),
         T("create_task", "Create a task.", {"name": P("Title"), "notes": P("Details"), "due_on": P("YYYY-MM-DD (optional)")}, ["name"], True, method="POST", path="/tasks", body={"data": {"name": "{name}", "notes": "{notes}", "due_on": "{due_on}", "workspace": "{cfg.workspace_gid}", "assignee": "me"}}, pick="data.gid")],
        test={"method": "GET", "path": "/users/me"}),

    # ---------------- search and information ----------------
    app("brave_search", "Brave Search", "Search", "🦁", "Search the web and get titles, links and snippets.",
        [F("api_key", "API key", "brave.com/search/api", secret=True)], {"type": "header", "name": "X-Subscription-Token", "value": "{cfg.api_key}"}, "https://api.search.brave.com/res/v1",
        [T("search", "Search the web.", {"query": P("What to search for"), "count": P("Results (max 20)", "integer")}, ["query"], method="GET", path="/web/search", query={"q": "{query}", "count": "{count}"}, pick={"path": "web.results[]", "fields": ["title", "url", "description"]})],
        test={"method": "GET", "path": "/web/search", "query": {"q": "test", "count": "1"}}),
    app("tavily", "Tavily Search", "Search", "🔎", "Web search built for AI, with a short direct answer.",
        [F("api_key", "API key", "tavily.com", secret=True)], {"type": "bearer", "field": "api_key"}, "https://api.tavily.com",
        [T("search", "Search the web and get an answer with sources.", {"query": P("What to find out")}, ["query"], method="POST", path="/search", body={"query": "{query}", "max_results": 5, "include_answer": True}, pick={"path": "", "fields": []})]),
    app("serper", "Serper (Google results)", "Search", "🅶", "Google search results as data.",
        [F("api_key", "API key", "serper.dev", secret=True)], {"type": "header", "name": "X-API-KEY", "value": "{cfg.api_key}"}, "https://google.serper.dev",
        [T("search", "Google search.", {"query": P("What to search for")}, ["query"], method="POST", path="/search", body={"q": "{query}"}, pick={"path": "organic[]", "fields": ["title", "link", "snippet"]})]),
    app("openweather", "OpenWeather", "Search", "⛅", "Current weather for a city (useful for field services and deliveries).",
        [F("api_key", "API key", "openweathermap.org/api", secret=True)], {"type": "query", "name": "appid", "field": "api_key"}, "https://api.openweathermap.org/data/2.5",
        [T("weather", "Current weather.", {"city": P("City, e.g. Leeds,GB")}, ["city"], method="GET", path="/weather", query={"q": "{city}", "units": "metric"}, pick={"path": "", "fields": []})],
        test={"method": "GET", "path": "/weather", "query": {"q": "London", "units": "metric"}}),


    # ---------------- built in: public data, no account or key, available to every employee ----------------
    app("weather", "Weather forecast", "Built in", "⛅", "Weather now and the next days for any place in the world (Open-Meteo).", [], {"type": "none"}, "https://api.open-meteo.com/v1",
        [T("weather_for_place", "Current weather and a daily forecast for a place name. weather_code is a WMO code: 0 clear, 1-3 cloudy, 45-48 fog, 51-67 drizzle or rain, 71-77 snow, 80-99 showers or storms.", {"place": P("Town or city, e.g. Leeds"), "days": P("Forecast days (1-14)", "integer")}, ["place"],
           steps=[{"method": "GET", "path": "https://geocoding-api.open-meteo.com/v1/search", "query": {"name": "{place}", "count": 1}},
                  {"method": "GET", "path": "/forecast", "query": {"latitude": "{s0.results[0].latitude}", "longitude": "{s0.results[0].longitude}", "current": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code",
                                                              "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum", "forecast_days": "{days}", "timezone": "auto"}}],
           result={"place": "{s0.results[0].name}", "country": "{s0.results[0].country}", "current": "{s1.current}", "daily": "{s1.daily}"}),
         T("air_quality", "Air quality now for a place name (European AQI: 0-20 good, 20-40 fair, 40-60 moderate, above poor).", {"place": P("Town or city")}, ["place"],
           steps=[{"method": "GET", "path": "https://geocoding-api.open-meteo.com/v1/search", "query": {"name": "{place}", "count": 1}},
                  {"method": "GET", "path": "https://air-quality-api.open-meteo.com/v1/air-quality", "query": {"latitude": "{s0.results[0].latitude}", "longitude": "{s0.results[0].longitude}", "current": "european_aqi,pm10,pm2_5"}}],
           result={"place": "{s0.results[0].name}", "air": "{s1.current}"})], builtin=True, verified=True),
    app("maps", "Maps and addresses (OpenStreetMap)", "Built in", "🗺️", "Find coordinates for an address, find the address for coordinates, and work out driving distance and time between two places.", [], {"type": "none"}, "https://nominatim.openstreetmap.org",
        [T("find_place", "Find an address or place and its coordinates.", {"query": P("Address or place name")}, ["query"], method="GET", path="/search", query={"q": "{query}", "format": "jsonv2", "limit": "3", "addressdetails": "1"}, pick={"path": "", "fields": ["display_name", "lat", "lon", "type"]}),
         T("address_of", "Find the address at coordinates.", {"latitude": P("Latitude", "number"), "longitude": P("Longitude", "number")}, ["latitude", "longitude"], method="GET", path="/reverse", query={"lat": "{latitude}", "lon": "{longitude}", "format": "jsonv2"}, pick={"path": "", "fields": []}),
         T("drive_time", "Driving distance and time between two places.", {"from_place": P("Start address or place"), "to_place": P("End address or place")}, ["from_place", "to_place"],
           steps=[{"method": "GET", "path": "/search", "query": {"q": "{from_place}", "format": "jsonv2", "limit": "1"}}, {"method": "GET", "path": "/search", "delay": 1.2, "query": {"q": "{to_place}", "format": "jsonv2", "limit": "1"}},
                  {"method": "GET", "path": "https://router.project-osrm.org/route/v1/driving/{+s0.[0].lon},{+s0.[0].lat};{+s1.[0].lon},{+s1.[0].lat}", "delay": 0.3, "query": {"overview": "false"}}],
           transform=_drive)], builtin=True, verified=True),
    app("uk_postcodes", "UK postcodes", "Built in", "📮", "Look up, check and autocomplete UK postcodes, and find the nearest ones to a point (postcodes.io).", [], {"type": "none"}, "https://api.postcodes.io",
        [T("lookup", "Details for a UK postcode: area, council, coordinates.", {"postcode": P("e.g. LS1 1UR")}, ["postcode"], method="GET", path="/postcodes/{postcode}", pick="result"),
         T("is_valid", "Is this a real UK postcode?", {"postcode": P("Postcode")}, ["postcode"], method="GET", path="/postcodes/{postcode}/validate", pick="result"),
         T("autocomplete", "Complete a partial postcode.", {"partial": P("e.g. LS1 1")}, ["partial"], method="GET", path="/postcodes/{partial}/autocomplete", pick="result"),
         T("nearest", "Postcodes near coordinates.", {"latitude": P("Latitude", "number"), "longitude": P("Longitude", "number")}, ["latitude", "longitude"], method="GET", path="/postcodes", query={"lat": "{latitude}", "lon": "{longitude}", "limit": "5"}, pick={"path": "result[]", "fields": ["postcode", "admin_district", "distance"]})], builtin=True, verified=True),
    app("world_postcodes", "Postcodes worldwide", "Built in", "🏤", "Find the place for a postcode or zip code in 60+ countries (Zippopotam).", [], {"type": "none"}, "https://api.zippopotam.us",
        [T("lookup", "Place names for a postcode.", {"country_code": P("2-letter code, e.g. us, gb, de"), "postcode": P("Postcode or zip")}, ["country_code", "postcode"], method="GET", path="/{country_code}/{postcode}")], builtin=True, verified=True),
    app("wikipedia", "Wikipedia", "Built in", "📖", "Look things up: a short summary of any topic, or search for the right article.", [], {"type": "none"}, "https://en.wikipedia.org",
        [T("summary", "A short summary of a Wikipedia article.", {"title": P("Article title, e.g. Combi boiler")}, ["title"], method="GET", path="/api/rest_v1/page/summary/{title}", result={"title": "{s0.title}", "description": "{s0.description}", "summary": "{s0.extract}", "link": "{s0.content_urls.desktop.page}"}),
         T("search", "Search for articles.", {"query": P("What to look for")}, ["query"], method="GET", path="/w/rest.php/v1/search/page", query={"q": "{query}", "limit": "5"}, pick={"path": "pages[]", "fields": ["title", "description", "key"]})], builtin=True, verified=True),
    app("domains_dns", "Domains and email checks", "Built in", "🌐", "Check whether an email domain can receive mail, see a domain's registration, and read DNS records. Good for checking a lead's details.", [], {"type": "none"}, "https://dns.google",
        [T("can_domain_receive_email", "Does this domain have mail servers? (e.g. check an email address's domain before emailing)", {"domain": P("e.g. example.com")}, ["domain"], method="GET", path="/resolve", query={"name": "{domain}", "type": "MX"}, transform=_mx),
         T("dns_records", "DNS records of a type.", {"domain": P("Domain"), "type": P("A, MX, TXT, NS, CNAME")}, ["domain", "type"], method="GET", path="/resolve", query={"name": "{domain}", "type": "{type}"}, pick={"path": "Answer[]", "fields": ["name", "type", "TTL", "data"]}),
         T("domain_registration", "When a domain was registered and when it expires (RDAP).", {"domain": P("e.g. example.com")}, ["domain"], method="GET", path="https://rdap.org/domain/{domain}", transform=_events)], builtin=True, verified=True, redirects=True),
    app("ip_lookup", "IP address lookup", "Built in", "📍", "Country, city, time zone and provider for an IP address.", [], {"type": "none"}, "https://ipwho.is",
        [T("lookup", "Where is this IP address?", {"ip": P("IPv4 or IPv6 address")}, ["ip"], method="GET", path="/{ip}", transform=_ip)], builtin=True, verified=True),
    app("uk_bank_holidays", "UK bank holidays", "Built in", "🇬🇧", "Upcoming bank holidays for England and Wales, Scotland and Northern Ireland (GOV.UK), so calls and deliveries avoid them.", [], {"type": "none"}, "https://www.gov.uk",
        [T("upcoming", "Upcoming UK bank holidays.", {"region": P("england-and-wales, scotland or northern-ireland")}, [], method="GET", path="/bank-holidays.json", transform=_bank_holidays)], builtin=True, verified=True),
    app("public_holidays", "Public holidays (any country)", "Built in", "🎌", "Public holidays and long weekends for most countries, for planning call times and deliveries.", [], {"type": "none"}, "https://date.nager.at/api/v3",
        [T("holidays", "Public holidays for a country and year.", {"country_code": P("2-letter code, e.g. GB"), "year": P("Year", "integer")}, ["country_code", "year"], method="GET", path="/PublicHolidays/{year}/{country_code}", pick={"path": "", "fields": ["date", "localName", "name"]}),
         T("next_holidays", "The next public holidays in a country.", {"country_code": P("2-letter code")}, ["country_code"], method="GET", path="/NextPublicHolidays/{country_code}", pick={"path": "", "fields": ["date", "localName", "name"]}),
         T("long_weekends", "Long weekends in a year.", {"country_code": P("2-letter code"), "year": P("Year", "integer")}, ["country_code", "year"], method="GET", path="/LongWeekend/{year}/{country_code}"),
         T("is_today_a_holiday", "Is it a public holiday today in a country?", {"country_code": P("2-letter code")}, ["country_code"], method="GET", path="/IsTodayPublicHoliday/{country_code}", transform=lambda out, args: {"is_public_holiday_today": out not in ("", None)})], builtin=True, verified=True),
    app("currency_rates", "Currency exchange rates", "Built in", "💱", "Convert amounts, look up exchange rates and see how a rate moved (European Central Bank data).", [], {"type": "none"}, "https://api.frankfurter.dev/v1",
        [T("rates", "Latest exchange rates.", {"base": P("Currency code, e.g. GBP"), "symbols": P("Comma-separated codes, e.g. USD,EUR")}, [], method="GET", path="/latest", query={"base": "{base}", "symbols": "{symbols}"}),
         T("convert", "Convert an amount.", {"amount": P("Amount", "number"), "from": P("From currency code"), "to": P("To currency code")}, ["amount", "from", "to"], method="GET", path="/latest", query={"amount": "{amount}", "base": "{from}", "symbols": "{to}"}),
         T("history", "Rates between two dates.", {"start": P("YYYY-MM-DD"), "end": P("YYYY-MM-DD"), "base": P("Currency code"), "symbols": P("Comma-separated codes")}, ["start", "end", "base", "symbols"], method="GET", path="/{start}..{end}", query={"base": "{base}", "symbols": "{symbols}"}),
         T("currencies", "List the currencies that are supported.", {}, [], method="GET", path="/currencies")], builtin=True, verified=True),
    app("crypto_prices", "Crypto prices", "Built in", "🪙", "Current prices of major cryptocurrencies (CoinGecko).", [], {"type": "none"}, "https://api.coingecko.com/api/v3",
        [T("price", "Current price.", {"ids": P("Coin names, comma-separated, e.g. bitcoin,ethereum"), "currencies": P("Comma-separated, e.g. usd,gbp")}, ["ids", "currencies"], method="GET", path="/simple/price", query={"ids": "{ids}", "vs_currencies": "{currencies}"})], builtin=True, verified=True),
    app("tech_news", "Tech and startup news (Hacker News)", "Built in", "📰", "Search Hacker News stories.", [], {"type": "none"}, "https://hn.algolia.com/api/v1",
        [T("search", "Search stories.", {"query": P("Topic")}, ["query"], method="GET", path="/search", query={"query": "{query}", "tags": "story", "hitsPerPage": "10"}, pick={"path": "hits[]", "fields": ["title", "url", "points", "num_comments", "created_at"]})], builtin=True, verified=True),
    app("books", "Books (Open Library)", "Built in", "📚", "Search books by title, subject or author.", [], {"type": "none"}, "https://openlibrary.org",
        [T("search", "Search books.", {"query": P("Title, subject or author")}, ["query"], method="GET", path="/search.json", query={"q": "{query}", "limit": "8"}, pick={"path": "docs[]", "fields": ["title", "author_name", "first_publish_year"]})], builtin=True, verified=True),
    app("sun_times", "Sunrise and sunset", "Built in", "🌅", "Sunrise, sunset and day length for a place and date, useful for outdoor work and visits.", [], {"type": "none"}, "https://api.sunrise-sunset.org",
        [T("times", "Sunrise and sunset (UTC) for coordinates.", {"latitude": P("Latitude", "number"), "longitude": P("Longitude", "number"), "date": P("YYYY-MM-DD, default today")}, ["latitude", "longitude"], method="GET", path="/json", query={"lat": "{latitude}", "lng": "{longitude}", "date": "{date}", "formatted": "0"}, pick="results")], builtin=True, verified=True),

    # ---------------- real video meetings ----------------
    app("recall", "Real video meetings (Recall.ai bot)", "Meetings", "🤖", "Lets an employee join real Google Meet, Zoom or Teams meetings as a participant: listen, take notes and speak when spoken to.",
        [F("api_key", "Recall.ai API key", "From recall.ai (a paid service that provides meeting bots). Create an API key in their dashboard.", secret=True),
         F("base", "API address", "Use the address for your Recall region.", default="https://us-east-1.recall.ai/api/v1", required=False, validate=r"https://[a-z0-9.-]+\.recall\.ai/api/v1"),
         F("provider", "Transcript source", "meeting_captions uses the meeting's own captions.", default="meeting_captions", required=False)],
        {"type": "header", "name": "Authorization", "value": "Token {cfg.api_key}"}, "{cfg.base}",
        [T("join", "Send a bot into a meeting.", {"meeting_url": P("The meeting link"), "bot_name": P("Name shown in the meeting")}, ["meeting_url", "bot_name"], True,
           method="POST", path="/bot/", body={"meeting_url": "{meeting_url}", "bot_name": "{bot_name}", "recording_config": {"transcript": {"provider": {"{cfg.provider}": {}}}}}, pick="id"),
         T("transcript", "Read what has been said so far.", {"bot_id": P("Bot ID")}, ["bot_id"], method="GET", path="/bot/{bot_id}/transcript/"),
         T("say_audio", "Play spoken audio into the meeting.", {"bot_id": P("Bot ID"), "b64": P("Base64 mp3")}, ["bot_id", "b64"], True, method="POST", path="/bot/{bot_id}/output_audio/", body={"kind": "mp3", "b64_data": "{b64}"}),
         T("status", "See whether the bot is in the call.", {"bot_id": P("Bot ID")}, ["bot_id"], method="GET", path="/bot/{bot_id}/", pick="status_changes"),
         T("leave", "Remove the bot from the meeting.", {"bot_id": P("Bot ID")}, ["bot_id"], True, method="POST", path="/bot/{bot_id}/leave_call/")],
        test={"method": "GET", "path": "/bot/", "query": {"page": "1"}}, help="Unverified against a live account: use Test, then try a short meeting."),

    # ---------------- build your own ----------------
    app("webhook_out", "Zapier, Make or n8n (webhook)", "Automation", "⚡", "Send data to any automation: one webhook can trigger thousands of apps.",
        [F("url", "Webhook URL", "Create a 'Catch Hook' trigger in Zapier, a Webhook in Make, or a Webhook node in n8n and paste its URL.", secret=True, validate=r"https?://.+")],
        {"type": "none"}, "{cfg.url}", [T("trigger", "Send a payload to the automation.", {"payload": P("Object with the data to send", "object")}, ["payload"], True, method="POST", path="", body="{payload}")], multi=True, dynamic_url=True),
    app("custom_rest", "Any other API (custom)", "Automation", "🧩", "Connect any web API: give its address and a key, and agents can call it.",
        [F("url", "Base address", "e.g. https://api.example.com/v1", validate=r"https?://.+"), F("bearer_token", "Bearer token (optional)", secret=True, required=False),
         F("header_name", "Or a custom header name (optional)", required=False, placeholder="X-Api-Key"), F("header_value", "Header value (optional)", secret=True, required=False)],
        {"type": "flex"}, "{cfg.url}",
        [T("get", "Read data (GET).", {"path": P("Path after the base address, e.g. /customers"), "query": P("Query parameters as an object", "object")}, ["path"], method="GET", path="{+path}", query="{query}"),
         T("send", "Send data (POST, PUT, PATCH or DELETE).", {"method": P("POST, PUT, PATCH or DELETE"), "path": P("Path after the base address"), "body": P("JSON body", "object")}, ["method", "path"], True, method="{method}", path="{+path}", body="{body}")],
        multi=True, dynamic_url=True),
]


# the extended catalogue lives in separate files to keep each one readable
from . import catalog_more, catalog_more2, catalog_more3, catalog_more4, catalog_more5, catalog_social  # noqa: E402

APPS.extend(catalog_social.APPS + catalog_more.APPS + catalog_more2.APPS + catalog_more3.APPS + catalog_more4.APPS + catalog_more5.APPS)

# the ones most teams reach for first: shown at the top of Add an app
_POPULAR = {"google_calendar", "google_sheets", "google_drive", "slack", "stripe", "hubspot", "notion", "github", "shopify", "zoom", "twilio", "jira", "trello", "airtable", "mailchimp",
            "whatsapp_cloud", "zendesk", "microsoft365", "asana", "calendly", "linear", "sendgrid", "telegram", "discord"}
for _a in APPS:
    if _a["id"] in _POPULAR:
        _a["popular"] = True


def setup_kind(a):
    """How much the admin has to do to connect it: 'none' (built in), 'key' (paste one key or token) or 'details' (several settings, or a sign-in app)."""
    if a.get("builtin"):
        return "none"
    secrets = [f for f in a["fields"] if f.get("secret")]
    simple = len([f for f in a["fields"] if f.get("required", True)]) <= 1 and len(secrets) == 1
    return "key" if simple and a["auth"].get("type") not in ("oauth2_refresh", "oauth2_client") else "details"


def lint():
    """Check the catalog is self-consistent. Returns a list of problems (empty when fine)."""
    import re
    bad, seen = [], set()
    auth_types = {"none", "bearer", "basic", "header", "query", "query_multi", "oauth2_refresh", "oauth2_client", "flex"}
    for a in APPS:
        if a["id"] in seen:
            bad.append(f"duplicate app {a['id']}")
        seen.add(a["id"])
        keys = {f["key"] for f in a["fields"]} | set((a.get("derive") or {}).keys())
        if a["auth"]["type"] not in auth_types:
            bad.append(f"{a['id']}: unknown auth {a['auth']['type']}")
        m = re.match(r"\{cfg\.([A-Za-z0-9_]+)\}", a["base"])
        if m:  # the address comes from a setting: that setting must be pinned to known hosts by a pattern (or the app checks the address is public)
            fld = next((f for f in a["fields"] if f["key"] == m.group(1)), None)
            if not fld or not (fld.get("validate") or a.get("dynamic_url")):
                bad.append(f"{a['id']}: the base address setting must have a validate pattern")
        elif not a["base"].startswith("https://"):
            bad.append(f"{a['id']}: base must be https")
        names = set()
        blob_all = repr(a["base"]) + repr(a["auth"]) + repr(a.get("headers", {}))
        for t in a["tools"] + ([a["test"]] if a.get("test") else []):
            tn = t.get("name", "test")
            if tn in names:
                bad.append(f"{a['id']}: duplicate tool {tn}")
            names.add(tn)
            blob = blob_all + repr(t.get("path", "")) + repr(t.get("query", "")) + repr(t.get("body", "")) + repr(t.get("steps", "")) + repr(t.get("result", ""))
            for m in re.findall(r"\{cfg\.([A-Za-z0-9_]+)\}", blob):
                if m not in keys:
                    bad.append(f"{a['id']}.{tn}: unknown setting {m}")
            if t.get("name"):
                params = set(t.get("params", {})) | set(t.get("derived", []))
                for m in re.findall(r"\{\+?([A-Za-z0-9_]+)\}", blob):
                    if m not in params and m not in ("now", "uuid") and not re.match(r"^s\d+$", m) and m != "cfg":
                        bad.append(f"{a['id']}.{tn}: input {m} is not declared")
                for r in t.get("required", []):
                    if r not in params:
                        bad.append(f"{a['id']}.{tn}: required {r} not declared")
    return bad

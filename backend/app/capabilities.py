"""What each employee can do, grouped so the admin can switch abilities on and off per person."""
import json

# key, label, plain-English description, tool names
GROUPS = [
    ("calls", "Phone calls and customers", "Place and schedule calls, run campaigns, look up customers and call results. Set up centrally in Settings → Calling.",
     ["get_dashboard_stats", "get_segments", "search_contacts", "create_campaign", "schedule_call", "list_scheduled_calls", "start_campaign", "stop_campaign",
      "list_campaigns", "call_contact", "list_calls", "get_call", "get_call_report", "export_campaign_report", "get_client_report", "update_contact", "weekly_report"]),
    ("email", "Email", "Read and send email from their own address.",
     ["check_inbox", "read_email", "send_email", "reply_email", "search_email", "notify_admin"]),
    ("files", "Files and spreadsheets", "Keep documents and spreadsheets in folders on their own computer, and share them with teammates.",
     ["search_files", "organize_instructions", "create_document", "create_spreadsheet", "edit_sheet", "read_document", "update_document", "list_documents", "list_files", "create_folder", "move_file", "delete_file",
      "share_file", "share_folder", "unshare", "list_shared_with_me", "export_document", "copy_file"]),
    ("calendar", "Calendar", "Put events and reminders on the calendar.", ["create_event", "list_events", "cancel_event"]),
    ("web", "Web reading", "Read public web pages and learn from websites, including the linked company site and its subdomains.", ["read_webpage", "learn_from_website", "learn_company_website"]),
    ("browser", "Real web browser", "Open pages in a real browser, click, scroll, type and sign in with saved logins, like a person at a computer. The company switch (off, read only, full) is in Settings → Browser.",
     ["browser_open", "browser_state", "browser_click", "browser_type", "browser_select", "browser_scroll", "browser_back", "list_saved_logins", "browser_login", "browser_close"]),
    ("knowledge", "Knowledge bases", "Write and search detailed knowledge bases, including facts indexed from the linked company website.",
     ["create_knowledge_base", "add_knowledge", "update_knowledge_base", "search_knowledge", "search_company_website", "list_knowledge_bases"]),
    ("projects", "Projects", "Start projects, invite teammates and track the work together.",
     ["create_project", "invite_to_project", "list_projects", "project_status", "post_update"]),
    ("connectors", "Connected apps", "Use the outside apps connected in Apps: calendars, CRM, texting, payments, spreadsheets, search and more.",
     []),
    ("public", "Public information (built in)", "Weather, maps and driving times, postcodes, Wikipedia, web and news search, exchange rates, holidays, time zones, dates, units and money maths. Needs no account or key.",
     ["world_time", "date_math", "phone_info", "web_search", "news_search", "read_feed", "convert_units", "finance_calc"]),
    ("video", "Real video meetings", "Join real Google Meet, Zoom or Teams meetings as a participant, listen, take notes and speak when spoken to (needs the meeting bot connected).",
     ["join_video_meeting", "say_in_video_meeting", "leave_video_meeting", "video_meeting_notes"]),
    ("messaging", "Texts and WhatsApp", "Send text and WhatsApp messages to customers (needs a messaging app connected).", ["send_sms", "send_whatsapp"]),
    ("chats", "Customer chats and WhatsApp calls", "Answer customers who write on WhatsApp, Instagram, Messenger, text or Telegram, ask permission to call them on WhatsApp, and call them (needs the messaging apps connected).",
     ["list_chats", "read_chat", "reply_chat", "close_chat", "ask_call_permission", "call_on_whatsapp"]),
    ("data", "Data and analysis", "Calculate, summarise and query spreadsheets, make charts, and turn a sheet into customers.", ["calculate", "sheet_summary", "sheet_query", "sheet_to_customers", "make_chart"]),
]
MANAGER = ("platform", "Run the platform", "Manager powers: manage the team, set tasks, change settings, add connectors and approve emails, like the admin, and report back.",
           ["team_overview", "review_agent", "update_agent", "hire_agent", "remove_agent", "set_setting", "list_pending_emails", "approve_email", "reject_email",
            "add_connector", "list_connectors", "toggle_connector", "create_agent", "approve_message", "reject_message", "list_pending_messages",
            "update_company", "toggle_app", "update_project", "delete_project", "clear_inbox", "clear_workspace"])
CORE = ["remember", "update_my_profile", "team_directory", "ask_colleague", "ask_for_help", "request_meeting", "add_todo", "list_todos", "update_todo", "assign_duty",
        "list_duties", "complete_duty", "learn_procedure", "get_procedure", "list_procedures", "forget_procedure",
        "add_discussion_item", "list_discussion_items", "resolve_discussion_item", "request_app", "connected_apps", "use_app"]

_BY_TOOL = {t: g[0] for g in GROUPS for t in g[3]}
_BY_TOOL.update({t: MANAGER[0] for t in MANAGER[3]})


def group_of(tool):
    if tool.startswith("mcp__"):
        return "connectors"
    return _BY_TOOL.get(tool, "core")


def off_list(agent):
    try:
        return set(json.loads((agent or {}).get("caps_off") or "[]"))
    except Exception:
        return set()


def enabled(agent, group):
    if group == "core":
        return True
    if group == "calls":
        from . import voices
        if voices.get_setting("calls_enabled", "1") == "0":
            return False  # switched off for the whole team in Settings → Calling
    if group == "platform":
        return bool((agent or {}).get("is_manager"))
    return group not in off_list(agent)


def allows(agent, tool):
    return enabled(agent, group_of(tool))


def describe(agent):
    """For the profile page: every ability with whether it is on."""
    out = [{"key": k, "label": l, "description": d, "on": enabled(agent, k), "tools": len(t)} for k, l, d, t in GROUPS]
    if (agent or {}).get("is_manager"):
        out.append({"key": MANAGER[0], "label": MANAGER[1], "description": MANAGER[2], "on": True, "tools": len(MANAGER[3]), "locked": True})
    return out

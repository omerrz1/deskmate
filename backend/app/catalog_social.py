"""Messaging and social connectors with everything their public APIs allow: WhatsApp Business (Meta), Instagram, Facebook Pages and Messenger, Twilio and Telegram.
Same format as catalog.py. They are written from each service's public API documentation and have not been exercised against live accounts: use Test after connecting.
Incoming messages and WhatsApp calls are handled by chats.py and wa_call.py (the webhook address is shown under Settings, Chats); the tools here are what employees can do
with each account on their own initiative. Inputs that need arrays (buttons, template parameters) are built by small `prepare` functions."""
import os

from .catalog import F, P, T, app

GRAPH = os.getenv("GRAPH_BASE", "https://graph.facebook.com")  # the three addresses below are only changed in tests, to point at stand-ins
TWILIO = os.getenv("TWILIO_BASE", "https://api.twilio.com")
TELEGRAM_API = os.getenv("TELEGRAM_BASE", "https://api.telegram.org")
API = "v23.0"


# ---------------- inputs that need an array or an object ----------------
def _tpl(a):
    comps = []
    if a.get("header_image"):
        comps.append({"type": "header", "parameters": [{"type": "image", "image": {"link": a["header_image"]}}]})
    if a.get("body_params"):
        comps.append({"type": "body", "parameters": [{"type": "text", "text": str(x)[:1000]} for x in a["body_params"][:20]]})
    return {**a, **({"components": comps} if comps else {})}


def _media(a):
    kind = a.get("kind") if a.get("kind") in ("image", "video", "audio", "document") else "image"
    obj = {"link": a["link"]}
    if a.get("caption") and kind in ("image", "video", "document"):
        obj["caption"] = a["caption"][:1024]
    if a.get("filename") and kind == "document":
        obj["filename"] = a["filename"][:200]
    return {**a, "media_type": kind, "media_obj": obj}


def _buttons(a):
    titles = [str(t).strip()[:20] for t in (a.get("buttons") or []) if str(t).strip()][:3]
    if not titles:
        raise ValueError("Give one to three button labels (up to 20 characters each).")
    inter = {"type": "button", "body": {"text": a["body"][:1024]}, "action": {"buttons": [{"type": "reply", "reply": {"id": f"b{i + 1}", "title": t}} for i, t in enumerate(titles)]}}
    if a.get("header"):
        inter["header"] = {"type": "text", "text": a["header"][:60]}
    if a.get("footer"):
        inter["footer"] = {"text": a["footer"][:60]}
    return {**a, "interactive": inter}


def _list(a):
    secs = []
    for s in (a.get("sections") or [])[:10]:
        rows = [{"id": str(r.get("id") or f"r{i}")[:200], "title": str(r.get("title", ""))[:24], **({"description": str(r["description"])[:72]} if r.get("description") else {})}
                for i, r in enumerate((s.get("rows") or [])[:10]) if isinstance(r, dict) and r.get("title")]
        if rows:
            secs.append({"title": str(s.get("title", ""))[:24], "rows": rows})
    if not secs:
        raise ValueError("Give at least one section with one row (each row needs a title).")
    return {**a, "interactive": {"type": "list", "body": {"text": a["body"][:1024]}, "action": {"button": str(a.get("button_label") or "Choose")[:20], "sections": secs}}}


def _read(a):
    return {**a, **({"typing_indicator": {"type": "text"}} if a.get("typing") else {})}


def _profile(a):
    sites = [str(x) for x in (a.get("websites") or [])][:2]
    return {**a, **({"websites": sites} if sites else {})}


def _quick_replies(a):
    qs = [{"content_type": "text", "title": str(t)[:20], "payload": str(t)[:100]} for t in (a.get("quick_replies") or [])][:13]
    return {**a, **({"qr": qs} if qs else {})}


def _tags(a):
    return {**a, "tag": "HUMAN_AGENT" if a.get("human_agent") else "ACCOUNT_UPDATE"}


# ---------------- WhatsApp Business (Meta) ----------------
_WA_TO = P("The customer's WhatsApp number in international format, digits only (no +), e.g. 447700900123")
_WA_BASE = f"{GRAPH}/{{cfg.api_version}}"


def _wa_msg(extra, **kw):
    return {"method": "POST", "path": "/messages", "body": {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "{to}", **extra}, **kw}


WHATSAPP = app(
    "whatsapp_cloud", "WhatsApp Business (Meta)", "Messaging", "🟢",
    "Chat with customers on WhatsApp both ways (messages, photos, documents, buttons, templates) and call people who message you, through Meta's WhatsApp Business Cloud API.",
    [F("access_token", "Access token", "A permanent token from a System User in Meta Business settings (whatsapp_business_messaging and whatsapp_business_management).", secret=True),
     F("phone_number_id", "Phone number ID", "Meta for Developers → your app → WhatsApp → API setup.", validate=r"\d{6,20}"),
     F("waba_id", "WhatsApp Business Account ID (optional)", "Needed to list message templates. On the same API setup page.", required=False, validate=r"\d{6,25}"),
     F("app_secret", "Meta app secret (optional, recommended)", "App settings → Basic → App secret. With it, Deskmates only accepts incoming messages that Meta has signed.", secret=True, required=False),
     F("api_version", "API version", "Use a current Graph API version.", default=API, required=False)],
    {"type": "bearer", "field": "access_token"}, f"{_WA_BASE}/{{cfg.phone_number_id}}",
    [T("send_text", "Send a WhatsApp text. Free text only works within 24 hours of the customer's last message; otherwise use send_template.",
       {"to": _WA_TO, "text": P("The message (up to 4096 characters)"), "reply_to": P("ID of one of their messages to quote (optional)"), "preview_links": P("Show a preview of a link in the text", "boolean")},
       ["to", "text"], True, **_wa_msg({"context": {"message_id": "{reply_to}"}, "type": "text", "text": {"body": "{text}", "preview_url": "{preview_links}"}})),
     T("send_template", "Send an approved message template. Needed to start a conversation or to write after 24 hours of silence. body_params fill the {{1}}, {{2}} placeholders in order.",
       {"to": _WA_TO, "template": P("Template name"), "language": P("Language code, e.g. en_US or en_GB"), "body_params": P("Values for the template's placeholders, in order", "array", items={"type": "string"}),
        "header_image": P("Public image link for a template with an image header (optional)")}, ["to", "template", "language"], True, prepare=_tpl, derived=["components"],
       **_wa_msg({"type": "template", "template": {"name": "{template}", "language": {"code": "{language}"}, "components": "{components}"}})),
     T("send_media", "Send a photo, video, voice note or document from a public https link.",
       {"to": _WA_TO, "kind": P("image, video, audio or document"), "link": P("Public https link to the file"), "caption": P("Caption (image, video, document)"), "filename": P("File name shown for a document")},
       ["to", "kind", "link"], True, prepare=_media, derived=["media_type", "media_obj"], **_wa_msg({"type": "{media_type}", "{media_type}": "{media_obj}"})),
     T("send_buttons", "Send a message with up to three tap-to-reply buttons.",
       {"to": _WA_TO, "body": P("The question or message"), "buttons": P("One to three button labels, up to 20 characters each", "array", items={"type": "string"}), "header": P("Short header (optional)"), "footer": P("Short footer (optional)")},
       ["to", "body", "buttons"], True, prepare=_buttons, derived=["interactive"], **_wa_msg({"type": "interactive", "interactive": "{interactive}"})),
     T("send_list", "Send a message with a menu of up to 10 choices.",
       {"to": _WA_TO, "body": P("The message"), "button_label": P("Label of the button that opens the menu"),
        "sections": P("Menu sections: [{title, rows:[{id, title, description}]}]", "array", items={"type": "object"})},
       ["to", "body", "sections"], True, prepare=_list, derived=["interactive"], **_wa_msg({"type": "interactive", "interactive": "{interactive}"})),
     T("send_location", "Send a map location.", {"to": _WA_TO, "latitude": P("Latitude", "number"), "longitude": P("Longitude", "number"), "name": P("Place name"), "address": P("Address")},
       ["to", "latitude", "longitude"], True, **_wa_msg({"type": "location", "location": {"latitude": "{latitude}", "longitude": "{longitude}", "name": "{name}", "address": "{address}"}})),
     T("send_reaction", "React to one of their messages with an emoji.", {"to": _WA_TO, "message_id": P("ID of their message"), "emoji": P("One emoji")}, ["to", "message_id", "emoji"], True,
       **_wa_msg({"type": "reaction", "reaction": {"message_id": "{message_id}", "emoji": "{emoji}"}})),
     T("mark_read", "Mark their message as read (shows blue ticks), and optionally show the typing indicator while you write.", {"message_id": P("ID of their message"), "typing": P("Also show 'typing…'", "boolean")},
       ["message_id"], True, prepare=_read, derived=["typing_indicator"], method="POST", path="/messages", body={"messaging_product": "whatsapp", "status": "read", "message_id": "{message_id}", "typing_indicator": "{typing_indicator}"}),
     T("request_call_permission", "Ask the customer's permission to call them on WhatsApp (they tap Allow). Needed before any call.", {"to": _WA_TO, "text": P("Why you would like to call, in one friendly sentence")},
       ["to", "text"], True, **_wa_msg({"type": "interactive", "interactive": {"type": "call_permission_request", "action": {"name": "call_permission_request"}, "body": {"text": "{text}"}}})),
     T("get_call_permission", "Check whether this customer has allowed WhatsApp calls (and until when).", {"to": _WA_TO}, ["to"], method="GET", path="/call_permissions", query={"user_wa_id": "{to}"}),
     T("terminate_call", "Hang up a WhatsApp call.", {"call_id": P("The WhatsApp call ID (starts with wacid.)")}, ["call_id"], True, method="POST", path="/calls",
       body={"messaging_product": "whatsapp", "call_id": "{call_id}", "action": "terminate"}),
     T("calling_settings", "See whether WhatsApp calling is switched on for this number.", {}, [], method="GET", path="/settings"),
     T("enable_calling", "Switch on WhatsApp calling for this number, including asking customers for permission.", {}, [], True, method="POST", path="/settings",
       body={"calling": {"status": "ENABLED", "call_icon_visibility": "DEFAULT", "callback_permission_status": "ENABLED"}}),
     T("get_media_url", "Get the download address of a photo, voice note or file the customer sent.", {"media_id": P("Media ID from the message")}, ["media_id"], method="GET",
       path=f"{_WA_BASE}/{{media_id}}"),
     T("list_templates", "List the message templates on this WhatsApp Business account (needs the account ID).", {"status": P("APPROVED, PENDING or REJECTED (optional)")}, [], method="GET",
       path=f"{_WA_BASE}/{{cfg.waba_id}}/message_templates", query={"limit": "50", "status": "{status}", "fields": "name,status,category,language,components"}, pick="data"),
     T("business_profile", "Read the WhatsApp business profile (about, address, website).", {}, [], method="GET", path="/whatsapp_business_profile",
       query={"fields": "about,address,description,email,websites,vertical,profile_picture_url"}, pick="data"),
     T("update_business_profile", "Change the WhatsApp business profile.", {"about": P("Short 'about' line (up to 139 characters)"), "address": P("Address"), "description": P("Description (up to 512 characters)"),
                                                                           "email": P("Contact email"), "websites": P("Up to two website links", "array", items={"type": "string"})}, [], True,
       prepare=_profile, method="POST", path="/whatsapp_business_profile",
       body={"messaging_product": "whatsapp", "about": "{about}", "address": "{address}", "description": "{description}", "email": "{email}", "websites": "{websites}"})],
    test={"method": "GET", "path": "", "query": {"fields": "display_phone_number,verified_name,quality_rating"}},
    help="Connect the number here, then open Settings → Chats and paste the webhook address and verify token into Meta for Developers → WhatsApp → Configuration, and subscribe to messages and calls.")


# ---------------- Instagram (business account) ----------------
_IG_ID = "{cfg.ig_user_id}"
INSTAGRAM = app(
    "instagram_business", "Instagram (business account)", "Social media", "📷",
    "Reply to Instagram messages and comments, publish photos, reels and stories, and read posts and results on an Instagram business account.",
    [F("access_token", "Access token", "A Page access token (instagram_basic, instagram_manage_messages, instagram_manage_comments, instagram_content_publish, pages_messaging) for the Facebook Page linked to the Instagram account.", secret=True),
     F("ig_user_id", "Instagram user ID", "The numeric ID of the Instagram professional account (Graph API Explorer → me/accounts?fields=instagram_business_account).", validate=r"\d+"),
     F("app_secret", "Meta app secret (optional, recommended)", "App settings → Basic → App secret. With it, Deskmates only accepts incoming messages that Meta has signed.", secret=True, required=False)],
    {"type": "bearer", "field": "access_token"}, f"{GRAPH}/{API}",
    [T("recent_media", "Recent posts with likes and comments.", {"limit": P("How many (max 50)", "integer")}, [], method="GET", path=f"/{_IG_ID}/media",
       query={"fields": "id,caption,media_type,permalink,timestamp,like_count,comments_count", "limit": "{limit|10}"}, pick="data"),
     T("profile", "Followers and profile details.", {}, [], method="GET", path=f"/{_IG_ID}", query={"fields": "username,name,biography,website,followers_count,follows_count,media_count"}),
     T("media_comments", "Comments on one post.", {"media_id": P("Post ID"), "limit": P("How many (max 50)", "integer")}, ["media_id"], method="GET", path="/{media_id}/comments",
       query={"fields": "id,text,username,timestamp,like_count,replies{id,text,username}", "limit": "{limit|25}"}, pick="data"),
     T("reply_to_comment", "Reply publicly to a comment.", {"comment_id": P("Comment ID"), "message": P("The reply")}, ["comment_id", "message"], True, method="POST", path="/{comment_id}/replies", body={"message": "{message}"}),
     T("hide_comment", "Hide (or unhide) a comment.", {"comment_id": P("Comment ID"), "hide": P("true to hide, false to show again", "boolean")}, ["comment_id"], True, method="POST", path="/{comment_id}", body={"hide": "{hide|true}"}),
     T("delete_comment", "Delete a comment on your own post.", {"comment_id": P("Comment ID")}, ["comment_id"], True, method="DELETE", path="/{comment_id}"),
     T("send_dm", "Send a direct message to someone who messaged you (only within 24 hours of their last message).", {"recipient_id": P("The person's Instagram-scoped ID from the conversation"), "text": P("The message")},
       ["recipient_id", "text"], True, method="POST", path="/me/messages", body={"recipient": {"id": "{recipient_id}"}, "message": {"text": "{text}"}}),
     T("send_dm_after_24h", "Send a direct message after the 24-hour window. Only a human may use the human-agent tag (up to 7 days after their message), so use this only when the admin asked for it.",
       {"recipient_id": P("The person's Instagram-scoped ID"), "text": P("The message")}, ["recipient_id", "text"], True, method="POST", path="/me/messages",
       body={"messaging_type": "MESSAGE_TAG", "tag": "HUMAN_AGENT", "recipient": {"id": "{recipient_id}"}, "message": {"text": "{text}"}}),
     T("send_dm_image", "Send an image in a direct message (within 24 hours of their last message).", {"recipient_id": P("The person's Instagram-scoped ID"), "image_url": P("Public https link to the image")},
       ["recipient_id", "image_url"], True, method="POST", path="/me/messages",
       body={"recipient": {"id": "{recipient_id}"}, "message": {"attachment": {"type": "image", "payload": {"url": "{image_url}"}}}}),
     T("private_reply_to_comment", "Send a private message to someone who commented (once per comment, within 7 days).", {"comment_id": P("Comment ID"), "text": P("The message")},
       ["comment_id", "text"], True, method="POST", path="/me/messages", body={"recipient": {"comment_id": "{comment_id}"}, "message": {"text": "{text}"}}),
     T("list_conversations", "Recent direct-message conversations.", {}, [], method="GET", path="/me/conversations",
       query={"platform": "instagram", "fields": "id,updated_time,participants,messages.limit(1){message,from,created_time}", "limit": "20"}, pick="data"),
     T("conversation_messages", "Messages in one conversation.", {"conversation_id": P("Conversation ID")}, ["conversation_id"], method="GET", path="/{conversation_id}",
       query={"fields": "messages.limit(25){message,from,created_time}"}),
     T("publish_photo", "Publish a photo post (the image must be at a public https link).", {"image_url": P("Public https link to a JPEG image"), "caption": P("Caption with #hashtags"), "alt_text": P("Alt text for accessibility")},
       ["image_url"], True, steps=[{"method": "POST", "path": f"/{_IG_ID}/media", "body": {"image_url": "{image_url}", "caption": "{caption}", "alt_text": "{alt_text}"}},
                                   {"method": "POST", "path": f"/{_IG_ID}/media_publish", "body": {"creation_id": "{s0.id}"}}]),
     T("publish_story", "Publish a photo to the story.", {"image_url": P("Public https link to a JPEG image")}, ["image_url"], True,
       steps=[{"method": "POST", "path": f"/{_IG_ID}/media", "body": {"image_url": "{image_url}", "media_type": "STORIES"}},
              {"method": "POST", "path": f"/{_IG_ID}/media_publish", "body": {"creation_id": "{s0.id}"}}]),
     T("create_reel", "Start uploading a reel from a public video link. Videos take a while to process: check with video_status, then call publish_container.",
       {"video_url": P("Public https link to an MP4 video"), "caption": P("Caption with #hashtags")}, ["video_url"], True, method="POST", path=f"/{_IG_ID}/media",
       body={"media_type": "REELS", "video_url": "{video_url}", "caption": "{caption}"}),
     T("video_status", "Check whether an uploaded reel has finished processing.", {"container_id": P("ID returned by create_reel")}, ["container_id"], method="GET", path="/{container_id}", query={"fields": "status_code,status"}),
     T("publish_container", "Publish a processed reel (status FINISHED).", {"container_id": P("ID returned by create_reel")}, ["container_id"], True, method="POST", path=f"/{_IG_ID}/media_publish",
       body={"creation_id": "{container_id}"}),
     T("account_insights", "Account results over a period (reach, views, interactions).", {"metric": P("Comma-separated metrics, e.g. reach,accounts_engaged,total_interactions"), "period": P("day, week or days_28")},
       [], method="GET", path=f"/{_IG_ID}/insights", query={"metric": "{metric|reach,accounts_engaged,total_interactions}", "period": "{period|day}", "metric_type": "total_value"}, pick="data"),
     T("post_insights", "Results for one post.", {"media_id": P("Post ID"), "metric": P("Comma-separated metrics, e.g. reach,likes,comments,shares,saved")}, ["media_id"], method="GET", path="/{media_id}/insights",
       query={"metric": "{metric|reach,likes,comments,shares,saved}"}, pick="data"),
     T("stories", "Your current stories.", {}, [], method="GET", path=f"/{_IG_ID}/stories", query={"fields": "id,media_type,permalink,timestamp"}, pick="data"),
     T("tagged_posts", "Posts where your account was tagged.", {}, [], method="GET", path=f"/{_IG_ID}/tags", query={"fields": "id,caption,permalink,username,timestamp", "limit": "20"}, pick="data"),
     T("hashtag_top_posts", "Top public posts for a hashtag.", {"hashtag": P("Hashtag without #")}, ["hashtag"],
       steps=[{"method": "GET", "path": "/ig_hashtag_search", "query": {"user_id": _IG_ID, "q": "{hashtag}"}},
              {"method": "GET", "path": "/{s0.data[0].id}/top_media", "query": {"user_id": _IG_ID, "fields": "id,caption,media_type,permalink,like_count,comments_count"}}], pick="data")],
    test={"method": "GET", "path": f"/{_IG_ID}", "query": {"fields": "username"}},
    help="Connect the account here, then open Settings → Chats and paste the webhook address and verify token into Meta for Developers → Instagram → Webhooks, and subscribe to messages and comments.")


# ---------------- Facebook Page and Messenger ----------------
_PG = "{cfg.page_id}"
FACEBOOK = app(
    "facebook_page", "Facebook Page and Messenger", "Social media", "👍",
    "Reply to Messenger messages and comments, publish and schedule posts and photos, and read posts, reviews and results on a Facebook Page.",
    [F("page_token", "Page access token", "A Page token (pages_manage_posts, pages_read_engagement, pages_messaging, pages_manage_engagement) from the Graph API Explorer.", secret=True),
     F("page_id", "Page ID", "On the Page's About tab, or from me/accounts.", validate=r"\d+"),
     F("app_secret", "Meta app secret (optional, recommended)", "App settings → Basic → App secret. With it, Deskmates only accepts incoming messages that Meta has signed.", secret=True, required=False)],
    {"type": "bearer", "field": "page_token"}, f"{GRAPH}/{API}",
    [T("page_info", "The Page's details and follower count.", {}, [], method="GET", path=f"/{_PG}", query={"fields": "id,name,about,category,fan_count,followers_count,link,website"}),
     T("recent_posts", "Recent posts on the page.", {"limit": P("How many (max 50)", "integer")}, [], method="GET", path=f"/{_PG}/posts",
       query={"limit": "{limit|10}", "fields": "id,message,created_time,permalink_url,shares,reactions.summary(true),comments.summary(true)"}, pick="data"),
     T("publish", "Publish a post to the page.", {"message": P("The post text"), "link": P("A link to attach (optional)")}, ["message"], True, method="POST", path=f"/{_PG}/feed",
       body={"message": "{message}", "link": "{link}"}),
     T("schedule_post", "Schedule a post for later (between 10 minutes and 30 days ahead).", {"message": P("The post text"), "link": P("A link to attach (optional)"), "publish_at": P("Unix time (seconds) to publish", "integer")},
       ["message", "publish_at"], True, method="POST", path=f"/{_PG}/feed", body={"message": "{message}", "link": "{link}", "published": "false", "scheduled_publish_time": "{publish_at}"}),
     T("publish_photo", "Publish a photo from a public https link.", {"image_url": P("Public https link to the image"), "caption": P("Caption")}, ["image_url"], True, method="POST", path=f"/{_PG}/photos",
       body={"url": "{image_url}", "caption": "{caption}"}),
     T("publish_video", "Publish a video from a public https link.", {"video_url": P("Public https link to the video"), "description": P("Description"), "title": P("Title")}, ["video_url"], True,
       method="POST", path=f"/{_PG}/videos", body={"file_url": "{video_url}", "description": "{description}", "title": "{title}"}),
     T("update_post", "Edit the text of a post.", {"post_id": P("Post ID"), "message": P("The new text")}, ["post_id", "message"], True, method="POST", path="/{post_id}", body={"message": "{message}"}),
     T("delete_post", "Delete a post.", {"post_id": P("Post ID")}, ["post_id"], True, method="DELETE", path="/{post_id}"),
     T("post_comments", "Comments on one post.", {"post_id": P("Post ID"), "limit": P("How many (max 100)", "integer")}, ["post_id"], method="GET", path="/{post_id}/comments",
       query={"fields": "id,message,from,created_time,like_count,comment_count", "limit": "{limit|25}", "order": "reverse_chronological"}, pick="data"),
     T("reply_to_comment", "Reply publicly to a comment.", {"comment_id": P("Comment ID"), "message": P("The reply")}, ["comment_id", "message"], True, method="POST", path="/{comment_id}/comments", body={"message": "{message}"}),
     T("like_comment", "Like a comment as the Page.", {"comment_id": P("Comment ID")}, ["comment_id"], True, method="POST", path="/{comment_id}/likes"),
     T("hide_comment", "Hide (or unhide) a comment.", {"comment_id": P("Comment ID"), "hide": P("true to hide, false to show again", "boolean")}, ["comment_id"], True, method="POST", path="/{comment_id}",
       body={"is_hidden": "{hide|true}"}),
     T("delete_comment", "Delete a comment.", {"comment_id": P("Comment ID")}, ["comment_id"], True, method="DELETE", path="/{comment_id}"),
     T("private_reply_to_comment", "Send a private Messenger message to someone who commented (once per comment, within 7 days).", {"comment_id": P("Comment ID"), "text": P("The message")},
       ["comment_id", "text"], True, method="POST", path="/me/messages", body={"recipient": {"comment_id": "{comment_id}"}, "message": {"text": "{text}"}}),
     T("send_message", "Send a Messenger message to someone who messaged the Page (only within 24 hours of their last message).", {"recipient_id": P("The person's Page-scoped ID from the conversation"),
                                                                                                                                  "text": P("The message (up to 2000 characters)"),
                                                                                                                                  "quick_replies": P("Up to 13 tap-to-reply choices (optional)", "array", items={"type": "string"})},
       ["recipient_id", "text"], True, prepare=_quick_replies, derived=["qr"], method="POST", path="/me/messages",
       body={"messaging_type": "RESPONSE", "recipient": {"id": "{recipient_id}"}, "message": {"text": "{text}", "quick_replies": "{qr}"}}),
     T("send_message_image", "Send an image in Messenger (within 24 hours of their last message).", {"recipient_id": P("The person's Page-scoped ID"), "image_url": P("Public https link to the image")},
       ["recipient_id", "image_url"], True, method="POST", path="/me/messages",
       body={"messaging_type": "RESPONSE", "recipient": {"id": "{recipient_id}"}, "message": {"attachment": {"type": "image", "payload": {"url": "{image_url}", "is_reusable": "true"}}}}),
     T("send_message_after_24h", "Send a Messenger message after the 24-hour window. Only a human may use the human-agent tag (up to 7 days after their message), so use this only when the admin asked for it.",
       {"recipient_id": P("The person's Page-scoped ID"), "text": P("The message"), "human_agent": P("true when a human staff member is sending this", "boolean")}, ["recipient_id", "text"], True,
       prepare=_tags, derived=["tag"], method="POST", path="/me/messages",
       body={"messaging_type": "MESSAGE_TAG", "tag": "{tag}", "recipient": {"id": "{recipient_id}"}, "message": {"text": "{text}"}}),
     T("list_conversations", "Recent Messenger conversations.", {}, [], method="GET", path=f"/{_PG}/conversations",
       query={"fields": "id,updated_time,snippet,unread_count,participants", "limit": "20"}, pick="data"),
     T("conversation_messages", "Messages in one conversation.", {"conversation_id": P("Conversation ID")}, ["conversation_id"], method="GET", path="/{conversation_id}",
       query={"fields": "messages.limit(25){message,from,created_time}"}),
     T("reviews", "Recent reviews and ratings of the Page.", {}, [], method="GET", path=f"/{_PG}/ratings", query={"fields": "created_time,recommendation_type,review_text,reviewer", "limit": "20"}, pick="data"),
     T("page_insights", "Page results over a period.", {"metric": P("Comma-separated metrics, e.g. page_post_engagements,page_views_total,page_fan_adds"), "period": P("day, week or days_28")}, [],
       method="GET", path=f"/{_PG}/insights", query={"metric": "{metric|page_post_engagements,page_views_total,page_fan_adds}", "period": "{period|day}"}, pick="data"),
     T("post_insights", "Results for one post.", {"post_id": P("Post ID"), "metric": P("Comma-separated metrics, e.g. post_impressions_unique,post_clicks,post_reactions_by_type_total")}, ["post_id"],
       method="GET", path="/{post_id}/insights", query={"metric": "{metric|post_impressions_unique,post_clicks,post_reactions_by_type_total}"}, pick="data")],
    test={"method": "GET", "path": f"/{_PG}", "query": {"fields": "id,name,fan_count"}},
    help="Connect the Page here, then open Settings → Chats and paste the webhook address and verify token into Meta for Developers → Webhooks (Page), and subscribe to messages and feed.")


# ---------------- Twilio: texts, WhatsApp and call history ----------------
TWILIO = app(
    "twilio", "Twilio (WhatsApp, phone calls and messaging)", "Messaging", "💬",
    "Make WhatsApp calls and regular phone calls with your AI employees, request WhatsApp calling permission, and send WhatsApp and SMS messages.",
    [F("account_sid", "Account SID", "Starts with AC. Twilio Console → Account info.", validate=r"AC[0-9a-fA-F]{32}"),
     F("auth_token", "Auth token", "Twilio Console → Account info.", secret=True),
     F("from_number", "Phone caller ID", "Required for regular phone calls. Use a Twilio number with Voice capability. Texts also come from it unless you set an SMS sender below, but a voice-only number cannot text.", required=False, validate=r"\+[1-9]\d{6,14}"),
     F("sms_from", "SMS sender (optional)", "Who texts come from when that should differ from the caller ID: an SMS-capable Twilio number (+44…), a Messaging Service SID (starts with MG), or a short name of 3 to 11 letters or digits such as MyCompany (UK and many other countries; people cannot reply to a name).", required=False, validate=r"\+[1-9]\d{6,14}|MG[0-9a-fA-F]{32}|(?=.*[A-Za-z])[A-Za-z0-9 ]{3,11}"),
     F("whatsapp_from", "WhatsApp sender number", "For WhatsApp calls and messages: your registered Twilio WhatsApp sender, e.g. +441234567890.", required=False, validate=r"\+[1-9]\d{6,14}"),
     F("call_permission_content_sid", "Voice Call Request Content SID", "Leave empty to create this automatically from Settings → Chats, or enter your existing Voice Call Request Content SID.", required=False, validate=r"HX[0-9a-fA-F]{32}")],
    {"type": "basic", "user": "{cfg.account_sid}", "pass": "{cfg.auth_token}"}, f"{TWILIO}/2010-04-01/Accounts/{{cfg.account_sid}}",
    [T("send_sms", "Send a text message.", {"to": P("Phone number in international format"), "body": P("Message text")}, ["to", "body"], True,
       method="POST", path="/Messages.json", encoding="form", body={"From": "{cfg.sms_sender}", "MessagingServiceSid": "{cfg.sms_service}", "To": "{to}", "Body": "{body}"}, pick="sid"),
     T("send_mms", "Send a picture message (US and Canada numbers).", {"to": P("Phone number in international format"), "body": P("Message text"), "media_url": P("Public https link to the image")},
       ["to", "media_url"], True, method="POST", path="/Messages.json", encoding="form", body={"From": "{cfg.sms_sender}", "MessagingServiceSid": "{cfg.sms_service}", "To": "{to}", "Body": "{body}", "MediaUrl": "{media_url}"}, pick="sid"),
     T("send_whatsapp", "Send a WhatsApp message (within 24 hours of the customer's last message, or an approved template).", {"to": P("Phone number in international format"), "body": P("Message text")},
       ["to", "body"], True, method="POST", path="/Messages.json", encoding="form", body={"From": "whatsapp:{cfg.whatsapp_from}", "To": "whatsapp:{to}", "Body": "{body}"}, pick="sid"),
     T("send_whatsapp_media", "Send a photo or document on WhatsApp.", {"to": P("Phone number in international format"), "body": P("Caption"), "media_url": P("Public https link to the file")},
       ["to", "media_url"], True, method="POST", path="/Messages.json", encoding="form", body={"From": "whatsapp:{cfg.whatsapp_from}", "To": "whatsapp:{to}", "Body": "{body}", "MediaUrl": "{media_url}"}, pick="sid"),
     T("list_messages", "List recent messages.", {"to": P("Only messages to this number (optional)"), "from_number": P("Only messages from this number (optional)"), "limit": P("How many (max 50)", "integer")}, [],
       method="GET", path="/Messages.json", query={"PageSize": "{limit}", "To": "{to}", "From": "{from_number}"}, pick={"path": "messages[]", "fields": ["sid", "to", "from", "body", "status", "direction", "date_sent"]}),
     T("message_status", "Check whether a message was delivered.", {"sid": P("Message SID from send_sms")}, ["sid"], method="GET", path="/Messages/{sid}.json", pick={"path": "", "fields": ["status", "error_code", "error_message", "date_sent"]}),
     T("list_calls", "Recent phone calls on this account.", {"limit": P("How many (max 50)", "integer")}, [], method="GET", path="/Calls.json", query={"PageSize": "{limit|20}"},
       pick={"path": "calls[]", "fields": ["sid", "to", "from", "status", "duration", "direction", "start_time"]}),
     T("list_numbers", "The phone numbers on this account.", {}, [], method="GET", path="/IncomingPhoneNumbers.json", pick={"path": "incoming_phone_numbers[]", "fields": ["phone_number", "friendly_name", "capabilities"]})],
    test={"method": "GET", "path": ".json"},
    help="Find your Account SID and Auth token on the Twilio Console home page. For incoming messages, set your number's 'A message comes in' webhook to the address shown under Settings → Chats.")


# ---------------- Telegram ----------------
TELEGRAM = app(
    "telegram", "Telegram", "Messaging", "✈️",
    "Chat with people on Telegram both ways through a bot: text, photos, documents and locations.",
    [F("token", "Bot token", "Talk to @BotFather on Telegram, create a bot, copy the token.", secret=True)],
    {"type": "none"}, f"{TELEGRAM_API}/bot{{cfg.token}}",
    [T("send_message", "Send a message to a chat.", {"chat_id": P("Chat ID"), "text": P("Message"), "reply_to": P("ID of a message to reply to (optional)", "integer")}, ["chat_id", "text"], True,
       method="POST", path="/sendMessage", body={"chat_id": "{chat_id}", "text": "{text}", "reply_to_message_id": "{reply_to}"}),
     T("send_photo", "Send a photo from a public https link.", {"chat_id": P("Chat ID"), "photo_url": P("Public https link to the image"), "caption": P("Caption")}, ["chat_id", "photo_url"], True,
       method="POST", path="/sendPhoto", body={"chat_id": "{chat_id}", "photo": "{photo_url}", "caption": "{caption}"}),
     T("send_document", "Send a file from a public https link.", {"chat_id": P("Chat ID"), "document_url": P("Public https link to the file"), "caption": P("Caption")}, ["chat_id", "document_url"], True,
       method="POST", path="/sendDocument", body={"chat_id": "{chat_id}", "document": "{document_url}", "caption": "{caption}"}),
     T("send_location", "Send a map location.", {"chat_id": P("Chat ID"), "latitude": P("Latitude", "number"), "longitude": P("Longitude", "number")}, ["chat_id", "latitude", "longitude"], True,
       method="POST", path="/sendLocation", body={"chat_id": "{chat_id}", "latitude": "{latitude}", "longitude": "{longitude}"}),
     T("get_chat", "Details of a chat.", {"chat_id": P("Chat ID")}, ["chat_id"], method="GET", path="/getChat", query={"chat_id": "{chat_id}"}, pick="result"),
     T("get_updates", "See recent messages sent to the bot (only works while no webhook is set).", {}, [], method="GET", path="/getUpdates", pick="result"),
     T("webhook_info", "Show where Telegram sends incoming messages.", {}, [], method="GET", path="/getWebhookInfo", pick="result")],
    test={"method": "GET", "path": "/getMe"},
    help="After connecting, open Settings → Chats and press 'Connect Telegram' so incoming messages reach your team.")

APPS = [WHATSAPP, INSTAGRAM, FACEBOOK, TWILIO, TELEGRAM]

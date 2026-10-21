# WhatsApp calling through Twilio

Twilio is the preferred provider for new WhatsApp conversations when connected. Existing Meta conversations retain their provider and history. Incoming Twilio WhatsApp messages expose the same permission and call controls as Meta chats.

## Configure

1. Register a WhatsApp sender in Twilio and satisfy Twilio's calling eligibility requirements. The sandbox is not a production calling sender.
2. In Apps → Twilio, enter the Account SID, Auth token and WhatsApp sender in international `+` format. The SMS sender is optional for WhatsApp-only use. Saving opens Settings → Chats.
3. Set `PUBLIC_BASE_URL=https://your-host` to your public origin without a path or query. Forward HTTPS and secure WebSocket upgrades to this backend. Use **one backend worker**: live call sessions currently reside in process memory. No UDP or TURN configuration is needed for Twilio calls.
4. In the company workspace, click **Check connection**. This checks the live account, registered sender, voice app, messaging webhook, permission template, and installed audio dependencies. Each failed check includes the next action.
5. Click **Configure incoming calls and messages**. A company admin can use this to create a dedicated TwiML app and a Voice Call Request template, and route the sender's calls and messages to the company workspace. It replaces that sender's voice app and incoming messaging URL. Other webhook settings are retained. Successful create steps are saved so ordinary partial-failure retries reuse resources.
6. Run the check again if Twilio reports an updating status. Choose whether the assistant answers incoming calls in Settings → Chats, then make a test call.

The setup check verifies configuration, not public endpoint reachability or working audio. It never dials a customer. Calls and messages are never automatically retried after an ambiguous timeout. If setup times out during creation, inspect Twilio Console before retrying because the resource may already exist.

For manual setup, expand **Manual setup and webhook addresses**. Create a TwiML app using the Voice URL (POST), select it under the sender's Voice Endpoint Configuration, and set the sender's messaging webhook (POST). Create a Call to action content template with exactly one Voice Call Request action; enter its `HX…` SID in Apps → Twilio. This is separate from approved messaging templates used to open a conversation.

## Verify with a consenting test customer

Have the customer message the sender, request permission in the chat, and accept it in WhatsApp. Start a call, speak over the assistant to check interruption handling, and end it from the app. Check the Calls page and chat summary. Also test inbound calling and a declined/unanswered call.

Call activity logs report REST acceptance time and answer-to-first-assistant-audio time. Compare these on the deployed host; local mocked tests do not measure live provider latency.

Audio streams in 20 ms mu-law chunks. Playback marks track audio still queued at Twilio; interruption clears both local and remote playback. The existing voice models, tools, transcript and summary are reused. No recording is requested. REST call creation is never retried automatically after an ambiguous timeout, avoiding duplicate calls.

Callbacks require a valid Twilio signature. Media connections also check workspace, call and account identity. Permission webhooks update consent, with temporary grants expiring after seven days and permanent grants retained until revoked. A provider rejection remains authoritative if cached permission was revoked outside the app.

Twilio setup references: [WhatsApp Business Calling](https://www.twilio.com/docs/voice/whatsapp-business-calling) and [Media Streams messages](https://www.twilio.com/docs/voice/media-streams/websocket-messages).

## Connection API

- `GET /api/chats-settings/calling`: authenticated, read-only live diagnostics. Returns `ready`, `can_configure`, `company_workspace` and a `checks` array with `id`, `label`, `ok`, and an actionable `message`. It never returns credentials or starts calls.
- `POST /api/chats-settings/calling/enable`: company-admin-only setup, protected by the existing session and CSRF checks. Creates or reuses the dedicated voice application and permission template, updates the sender, and returns the same diagnostics. Repeated setup reuses saved resources and repairs changed webhook URLs. It does not send a customer message or call.

After rotating the webhook secret or changing the public origin, run setup again to update Twilio. API timeouts are bounded; errors distinguish invalid credentials, rate limits, missing resources, and provider failures without exposing raw provider responses.

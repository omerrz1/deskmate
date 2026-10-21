# Twilio setup guide for Deskmates

This guide configures both regular phone calls and WhatsApp calling. They are separate Twilio routes with different sender requirements and permissions. A Twilio messaging sender by itself does not automatically enable voice calling.

## Before you start

You need:

- A Twilio account with the Account SID and Auth Token for the account that owns your senders/numbers.
- A public HTTPS Deskmates backend URL (`PUBLIC_BASE_URL`) that Twilio can reach, including secure WebSocket upgrades.
- OpenAI connected in Deskmates for the live conversation engine. ElevenLabs is optional: connect it for its voice, or explicitly skip it during setup to use OpenAI voice. Neither voice provider replaces Twilio telephony.
- For production calling, an active Twilio account with billing enabled. A trial account is restricted to verified recipients and calls in its signup country; remaining trial minutes do not remove those limits.

Keep the Account SID, Auth Token and API keys secret. Never paste them in a chat or report.

## 1. Connect the correct Twilio account

1. In Twilio Console, select the project or subaccount that owns the number you will use.
2. Copy that account's **Account SID** (`AC…`) and current **Auth Token**. Deskmates currently authenticates with this pair; do not paste an `SK…` API Key SID or API Key Secret into the Auth Token field.
3. In Deskmates, open **Apps → Twilio**, enter the SID and token, and save. Ensure the integration is enabled and write access is allowed.
4. If the credentials were rotated, replace the saved token in Deskmates. Error **20003** usually means the SID/token pair is wrong, expired, has extra whitespace, or belongs to another account/subaccount.
5. If using a trial account, verify the destination number in Twilio's Voice trial flow before testing. For `07447781753`, enter `+44744781753`. It must be verified on the same account whose credentials are connected.

## 2. Configure regular phone calling

Regular calls from **Campaigns** and direct agent call requests use Twilio Programmable Voice (PSTN). They do not require WhatsApp call permission.

1. In Twilio Console, buy or select an active phone number with **Voice** capability. It must belong to the same account/subaccount as the credentials above. A verified outgoing caller ID can also be used where Twilio permits it.
2. In Deskmates **Apps → Twilio**, set **Phone caller ID** to that number in E.164 format, for example `+441234567890`.
3. If the account is upgraded and you need international destinations, open Twilio **Voice → Settings → Geo Permissions** and enable the relevant destination country.
4. In Deskmates **Settings → Calling**, enable phone calling for the team and choose the chat call route. Note: Campaign calls remain regular Twilio Voice calls, regardless of the chat route setting.
5. Open **Settings → Chats and WhatsApp → Check connection**. Resolve any listed account, sender or server errors.
6. Test with a recipient who has agreed to receive the call. On a Twilio trial, the recipient must also be verified and within the trial's signup country.
7. If Twilio rejects call creation before returning a Call SID, inspect **Twilio Console → Monitor → Debugger** for the REST API error. Such rejected creates may not appear in Call Logs. Deskmates' activity log should also show the provider response.

Common Voice errors:

- **20003**: fix Account SID/Auth Token or account/subaccount mismatch.
- **21210/21212**: caller ID is not valid, assigned, or verified for that account.
- **21219**: trial destination is not verified; verify it or upgrade the account.
- **21215**: destination country is blocked by Voice Geo Permissions.
- **21211/21214/21217**: check E.164 formatting and that the destination is reachable.

Twilio references: [trial Voice](https://www.twilio.com/docs/usage/trials/try-out-voice), [Call resource](https://www.twilio.com/docs/voice/api/call-resource), [error 20003](https://www.twilio.com/docs/api/errors/20003), and [error 21219](https://www.twilio.com/docs/api/errors/21219).

## 3. Configure WhatsApp messaging through Twilio

1. Onboard and activate your WhatsApp Business sender in Twilio. The WhatsApp sandbox is for testing and is not a production sender.
2. In Deskmates **Apps → Twilio**, enter the WhatsApp sender in international format (for example `+441234567890`). Keep the account SID and token from the same Twilio project.
3. Open **Settings → Chats and WhatsApp** and copy the Twilio messaging webhook URL displayed there. In Twilio, set the sender's inbound messaging callback to this URL using **HTTP POST**.
4. Send a test WhatsApp message from a phone to the business sender. Confirm the conversation appears in Deskmates Chats and that an agent reply succeeds.
5. To start a new WhatsApp conversation, use an approved message template and its Content SID (`HX…`) in Deskmates. The customer can then reply in the conversation window. Trial accounts have template and verified-recipient limits.
6. Set **When an employee replies** to **Send automatically** if admins should not need to approve every message. Keep approval mode only when that review is wanted.

## 4. Enable WhatsApp voice calling through Twilio

WhatsApp voice calls are separate from PSTN calls. The customer must allow WhatsApp calling; a WhatsApp messaging opt-in alone is not call permission. WhatsApp calls cannot be connected to PSTN.

1. Confirm your WhatsApp sender is fully registered and active in Twilio, not the Sandbox.
2. Confirm the sender is eligible for WhatsApp Business Calling. Twilio documents Meta Business Verification and a messaging limit of at least 2,000 business-initiated conversations per rolling 24 hours as prerequisites for enabling Voice on the sender. Outbound support also depends on the sender's country.
3. Set `PUBLIC_BASE_URL` on the Deskmates backend to its public HTTPS origin, with no path or query string. Twilio must reach the backend's HTTPS webhook and secure WebSocket endpoint. Run one backend worker because live call sessions are held in process memory.
4. In the Deskmates **company workspace**, open **Settings → Chats and WhatsApp**, then click **Check connection**. Resolve every failed check.
5. Click **Configure incoming calls and messages**. This creates or reuses the TwiML Voice application and Voice Call Request template and attaches the app's webhook to this sender. It replaces the sender's current incoming messaging callback and Voice app; review other systems using that sender first.
6. Run **Check connection** again until the sender, voice application, webhook, public URL and audio checks pass.
7. Have a consenting test customer message your WhatsApp sender so a chat session is active. In the chat, request calling permission and have the customer tap **Allow** in WhatsApp. Wait for the accepted permission to appear in the chat before placing a call.
8. Start a WhatsApp call from that chat and answer it. Verify two-way audio, then check the Calls page and chat for the call summary.

WhatsApp limits permission requests to once per 24 hours and twice per seven days. A grant normally lasts seven days, permits up to five business-initiated calls per 24 hours, and can expire earlier after repeated unanswered calls. Do not retry rejected or ambiguous calls repeatedly.

Twilio references: [WhatsApp Business Calling setup, eligibility and permission](https://www.twilio.com/docs/voice/whatsapp-business-calling), [error 37000: permission missing](https://www.twilio.com/docs/api/errors/37000), [error 37002: sender calling not enabled](https://www.twilio.com/docs/api/errors/37002), and [error 37007: sender country not supported](https://www.twilio.com/docs/api/errors/37007).

## What ElevenLabs does here

ElevenLabs can provide an AI voice/conversation engine. Deskmates still uses Twilio to place PSTN calls and to connect WhatsApp voice; the app falls back to OpenAI voice if ElevenLabs is unavailable. ElevenLabs also offers its own independent Twilio phone-number integration for inbound and outbound calls, but that is a separate calling setup from Deskmates. Connecting ElevenLabs does not fix Twilio account credentials, caller-ID verification, trial limits, WhatsApp sender eligibility or customer call consent.

ElevenLabs references: [native Twilio integration](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration) and [Twilio integration overview](https://elevenlabs.io/docs/help-center/product/eleven-agents/can-i-connect-twilio-to-eleven-agents).

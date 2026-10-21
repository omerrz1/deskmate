"""Read-only Twilio diagnostics and resumable, admin-invoked sender setup."""
import asyncio
import hashlib
import re
import weakref
from urllib.parse import parse_qs, urlsplit

import httpx

from . import db, integrations, voices, wa_call, twilio_call as tw

_locks = weakref.WeakValueDictionary()


def template_valid(content):
    actions = ((content.get('types') or {}).get('twilio/call-to-action') or {}).get('actions') or []
    return len(actions) == 1 and isinstance(actions[0], dict) and actions[0].get('type') == 'VOICE_CALL_REQUEST'


async def sender(cfg, client):
    params = {'Channel': 'whatsapp', 'PageSize': 100}
    for _ in range(10):
        data = await tw.request('Channels/Senders', client=client, cfg=cfg, service='messaging', params=params)
        for item in data.get('senders') or []:
            if item.get('sender_id') == 'whatsapp:' + cfg['whatsapp_from']:
                sid = item.get('sid', '')
                if not re.fullmatch(r'XE[0-9a-fA-F]{32}', sid):
                    raise tw.TwilioError('Twilio returned an invalid sender SID.')
                return await tw.request(f'Channels/Senders/{sid}', client=client, cfg=cfg, service='messaging')
        nxt = (data.get('meta') or {}).get('next_page_url')
        if not nxt:
            break
        # Follow only the page token; never send credentials to a provider-supplied URL.
        token = parse_qs(urlsplit(nxt).query).get('PageToken', [])
        if not token:
            break
        params['PageToken'] = token[0]
    raise tw.TwilioError('This number is not a registered WhatsApp sender in the connected Twilio account. Register it in Twilio Console or correct the number in Apps.')


async def inspect():
    checks = []
    def add(key, label, ok, message):
        checks.append({'id': key, 'label': label, 'ok': bool(ok), 'message': message})
    result = {'provider': 'twilio', 'checks': checks, 'ready': False, 'can_configure': False,
              'company_workspace': db.current() == db.company(),
              'note': 'These checks verify Twilio configuration. A real call is still needed to verify audio and webhook reachability.'}
    try:
        cfg = tw.config()
        add('credentials', 'Connection details', True, 'Twilio credentials and WhatsApp sender are saved.')
    except wa_call.CallError as e:
        add('credentials', 'Connection details', False, str(e))
        return result
    try:
        with db.use(db.company()):
            url = tw.hook_url()
        result['voice_url'] = url
        add('public_url', 'Public address', True, 'Public HTTPS address is configured; reachability is verified by a test call.')
    except wa_call.CallError as e:
        url = None
        add('public_url', 'Public address', False, str(e))
    add('audio', 'Voice engine', wa_call.available(), 'Audio dependencies are installed.' if wa_call.available() else 'Install the backend audio dependencies before calling.')
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as client:
        try:
            account = await tw.request('.json', client=client, cfg=cfg)
            active = account.get('status') == 'active' and account.get('sid') == cfg['account_sid']
            add('account', 'Twilio account', active, 'Account is active.' if active else 'Activate this Twilio account before calling.')
            if not active:
                return result
        except wa_call.CallError as e:
            add('account', 'Twilio account', False, str(e))
            return result
        try:
            phone = await sender(cfg, client)
            add('sender', 'WhatsApp sender', phone.get('status') == 'ONLINE', f"Sender status: {phone.get('status', 'unknown')}.")
            result['can_configure'] = bool(url) and result['company_workspace']
            app_sid = (phone.get('configuration') or {}).get('voice_application_sid') or ''
            if re.fullmatch(r'AP[0-9a-fA-F]{32}', app_sid):
                app = await tw.request(f'Applications/{app_sid}.json', client=client, cfg=cfg)
                ok = bool(url) and app.get('voice_url') == url and app.get('voice_method') == 'POST'
                add('voice', 'Incoming calls', ok, 'Voice app points here.' if ok else 'Run setup to connect the voice app to this workspace.')
            else:
                add('voice', 'Incoming calls', False, 'Run setup to enable a TwiML voice application on this sender.')
            webhook = phone.get('webhook') or {}
            ok = bool(url) and webhook.get('callback_url') == url.removesuffix('/voice') and webhook.get('callback_method') == 'POST'
            add('messaging', 'Messages and permissions', ok, 'Messaging webhook points here.' if ok else 'Run setup to route messages and permission replies here.')
        except wa_call.CallError as e:
            add('sender_configuration', 'Sender configuration', False, str(e))
        sid = cfg.get('call_permission_content_sid', '')
        try:
            if not re.fullmatch(r'HX[0-9a-fA-F]{32}', sid):
                add('template', 'Calling permission template', False, 'Run setup to create the calling permission template.')
            else:
                content = await tw.request(f'Content/{sid}', client=client, cfg=cfg, service='content')
                ok = template_valid(content)
                add('template', 'Calling permission template', ok, 'Voice Call Request template is valid.' if ok else 'Choose a template with exactly one Voice Call Request action in Apps.')
        except wa_call.CallError as e:
            add('template', 'Calling permission template', False, str(e))
    result['ready'] = all(c['ok'] for c in checks)
    return result


async def configure():
    if db.current() != db.company():
        raise wa_call.CallError('Set up the shared Twilio sender from the company workspace.')
    lock = _locks.setdefault(db.current(), asyncio.Lock())
    if lock.locked():
        raise wa_call.CallError('Twilio setup is already running. Wait for it to finish.')
    async with lock:
        cfg = tw.config()
        url = tw.hook_url()
        inst = integrations.get_by_key('twilio')
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as client:
            account = await tw.request('.json', client=client, cfg=cfg)
            if account.get('status') != 'active' or account.get('sid') != cfg['account_sid']:
                raise wa_call.CallError('Activate the connected Twilio account before setup.')
            phone = await sender(cfg, client)
            # Persist each successful create before the next step so ordinary retries reuse it.
            template_sid = cfg.get('call_permission_content_sid')
            if template_sid:
                content = await tw.request(f'Content/{template_sid}', client=client, cfg=cfg, service='content')
                if not template_valid(content):
                    raise wa_call.CallError('The saved Content SID is not a Voice Call Request template. Correct it in Apps before setup.')
            else:
                content = await tw.request('Content', client=client, cfg=cfg, service='content', json_body={
                    'friendly_name': 'Deskmates calling permission', 'language': 'en',
                    'types': {'twilio/call-to-action': {'body': 'May we call you?',
                        'actions': [{'type': 'VOICE_CALL_REQUEST', 'title': 'Allow a call'}]}}})
                template_sid = content.get('sid', '')
                if not re.fullmatch(r'HX[0-9a-fA-F]{32}', template_sid):
                    raise wa_call.CallError('Twilio returned no template SID. Check Content Template Builder before repeating setup.')
                integrations.update(inst['id'], {'call_permission_content_sid': template_sid})
            key = 'twilio_voice_app_' + hashlib.sha256((cfg['account_sid'] + cfg['whatsapp_from']).encode()).hexdigest()[:24]
            app_sid = voices.get_setting(key, '')
            if app_sid:
                try:
                    await tw.request(f'Applications/{app_sid}.json', client=client, cfg=cfg)
                except tw.TwilioError as e:
                    if e.status != 404:
                        raise
                    app_sid = ''
            if not app_sid:
                app = await tw.request('Applications.json', {'FriendlyName': f'Deskmates {db.current()}', 'VoiceUrl': url, 'VoiceMethod': 'POST'}, client, cfg)
                app_sid = app.get('sid', '')
                if not re.fullmatch(r'AP[0-9a-fA-F]{32}', app_sid):
                    raise wa_call.CallError('Twilio returned no voice application SID. Check TwiML Apps before repeating setup.')
                voices.set_setting(key, app_sid)
            else:
                await tw.request(f'Applications/{app_sid}.json', {'VoiceUrl': url, 'VoiceMethod': 'POST'}, client, cfg)
            # Only change this sender's voice app and messaging URL; retain other webhook settings.
            await tw.request(f"Channels/Senders/{phone['sid']}", client=client, cfg=cfg, service='messaging', json_body={
                'configuration': {'voice_application_sid': app_sid},
                'webhook': {**(phone.get('webhook') or {}), 'callback_url': url.removesuffix('/voice'), 'callback_method': 'POST'}})
        return await inspect()

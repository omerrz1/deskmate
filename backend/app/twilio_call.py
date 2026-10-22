"""Twilio WhatsApp voice transport; shares call policy and AI engines with wa_call."""
import asyncio
import base64
import hashlib
import hmac
import json
import ipaddress
import os
import re
import time
import weakref
from urllib.parse import parse_qsl, urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from . import callengine, chats, db, directory, integrations, wa_call

router = APIRouter()
_permission_locks = weakref.WeakValueDictionary()


def config(require_write=True):
    inst = integrations.get_by_key('twilio')
    if not inst or (require_write and (not inst['enabled'] or not inst['allow_write'])):
        raise wa_call.CallError('Connect Twilio and enable changes in Apps to use WhatsApp calling.')
    cfg = integrations._config(inst)
    if not re.fullmatch(r'AC[0-9a-fA-F]{32}', cfg.get('account_sid', '')) or not cfg.get('auth_token'):
        raise wa_call.CallError('Enter a valid Twilio Account SID and Auth token in Apps.')
    if not re.fullmatch(r'\+[1-9]\d{6,14}', cfg.get('whatsapp_from', '')):
        raise wa_call.CallError('Add your voice-enabled WhatsApp sender number in the Twilio app settings.')
    return cfg


def voice_config(require_write=True):
    """Twilio Voice credentials and a verified, voice-capable PSTN caller ID."""
    inst = integrations.get_by_key('twilio')
    if not inst or (require_write and (not inst['enabled'] or not inst['allow_write'])):
        raise wa_call.CallError('Connect Twilio and enable changes in Apps to use phone calling.')
    cfg = integrations._config(inst)
    if not re.fullmatch(r'AC[0-9a-fA-F]{32}', cfg.get('account_sid', '')) or not cfg.get('auth_token'):
        raise wa_call.CallError('Enter a valid Twilio Account SID and Auth token in Apps.')
    if not re.fullmatch(r'\+[1-9]\d{6,14}', cfg.get('from_number', '')):
        raise wa_call.CallError('Add a voice-enabled Twilio phone number in Apps → Twilio → Phone caller ID.')
    base_url()
    return cfg


def base_url():
    base = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    try:
        parsed = urlsplit(base)
        parsed.port  # reject invalid port syntax
    except ValueError:
        raise wa_call.CallError('PUBLIC_BASE_URL is not a valid HTTPS origin.')
    try:
        private_ip = not ipaddress.ip_address(parsed.hostname or '').is_global
    except ValueError:
        private_ip = False
    if private_ip or any(c.isspace() for c in base) or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/') or parsed.hostname in ('localhost', '127.0.0.1', '::1'):
        raise wa_call.CallError('Set PUBLIC_BASE_URL to this server’s public HTTPS origin, without a path or query string.')
    return base


def hook_url():
    return f'{base_url()}/api/hooks/chat/{db.current()}/{chats.secret()}/twilio/voice'


class TwilioError(wa_call.CallError):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code


async def _credentials_work(cfg, client):
    """Check the saved SID/token pair against the read-only Account resource.

    Twilio's 20003 can also describe denied access to a particular resource. A
    successful Account read means we should not tell an operator to rotate
    credentials blindly when call creation itself is what was rejected.
    """
    url = f'https://api.twilio.com/2010-04-01/Accounts/{cfg["account_sid"]}.json'
    try:
        response = await client.get(url, auth=(cfg['account_sid'], cfg['auth_token']),
                                    headers={'Accept': 'application/json'})
        if not 200 <= response.status_code < 300:
            return False
        body = response.json()
        return isinstance(body, dict) and body.get('sid') == cfg['account_sid']
    except (httpx.HTTPError, ValueError):
        return None


async def request(path, data=None, client=None, cfg=None, *, service='voice', json_body=None, params=None):
    """Fixed Twilio hosts, bounded timeouts, no automatic replay of mutations."""
    cfg = cfg or config()
    roots = {'voice': f'https://api.twilio.com/2010-04-01/Accounts/{cfg["account_sid"]}',
             'messaging': 'https://messaging.twilio.com/v2', 'content': 'https://content.twilio.com/v1'}
    url = roots[service] + ('' if path == '.json' else '/') + path
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), follow_redirects=False)
    writing = data is not None or json_body is not None
    try:
        r = await client.request('POST' if writing else 'GET', url, data=data, json=json_body,
            params=params, auth=(cfg['account_sid'], cfg['auth_token']))
        try:
            result = r.json()
        except ValueError:
            result = None
        if not 200 <= r.status_code < 300:
            code = result.get('code') if isinstance(result, dict) else None
            provider_message = result.get('message', '') if isinstance(result, dict) else ''
            hint = integrations.twilio_error_hint(code, provider_message, service)
            if hint:
                raise TwilioError(hint, r.status_code, code)
            if r.status_code in (401, 403):
                if str(code) == '20003':
                    credential_check = await _credentials_work(cfg, client)
                    if credential_check is True:
                        message = ('Twilio authenticated the saved Account SID and Auth Token, but rejected this call request with error 20003. '
                                   'The saved credentials currently work; check Twilio Monitor → Debugger for the request details and confirm this account has Voice API access. '
                                   'Do not replace the credentials unless the Account connection check also fails.')
                    elif credential_check is False:
                        message = ('Twilio error 20003: the saved Account SID/Auth Token pair also failed a read-only Account check. '
                                   'Update the pair in Apps → Twilio, making sure both values belong to the same account or subaccount, then run Connect & check.')
                    else:
                        message = ('Twilio returned error 20003, but the follow-up credential check could not reach its Account endpoint. '
                                   'Check Twilio Monitor → Debugger and run Connect & check before changing the saved credentials.')
                else:
                    message = 'Twilio rejected these credentials or permissions. Check the Account SID and Auth token in Apps.'
            elif r.status_code == 429:
                message = 'Twilio is rate limiting requests. Wait before trying again.'
            elif r.status_code == 404:
                message = 'This Twilio resource was not found in the connected account. Check the sender or template SID.'
            elif r.status_code >= 500:
                message = 'Twilio is temporarily unavailable.'
            else:
                # A failed Calls API create has no Call SID and therefore may
                # not appear in Call Logs. Preserve Twilio's actionable error
                # instead of mislabeling every 4xx as WhatsApp consent.
                detail = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(provider_message)).strip()[:400]
                code_text = f' error {code}' if code not in (None, 0, '0', '') else ''
                message = (f'Twilio Voice rejected call creation (HTTP {r.status_code}{code_text}). ' + detail
                           if service == 'voice' and detail else
                           f'Twilio Voice rejected call creation (HTTP {r.status_code}{code_text}). Check Twilio Monitor → Debugger for the REST API error; a rejected create has no Call SID and may not appear in Call Logs.'
                           if service == 'voice' else 'Twilio rejected the request.')
            if writing and r.status_code >= 500:
                message += ' The request may have been accepted; check Twilio before repeating it.'
            raise TwilioError(message + (f' (code {code})' if isinstance(code, int) else ''), r.status_code, code)
        if not isinstance(result, dict):
            raise TwilioError('Twilio returned an invalid response.' + (' Check Twilio before repeating this action.' if writing else ' Try checking the connection again.'))
        return result
    except httpx.TimeoutException:
        raise TwilioError('Twilio timed out.' + (' The request may have been accepted; check Twilio before trying again.' if writing else ' Try checking the connection again.'))
    except httpx.HTTPError:
        raise TwilioError('Could not reach Twilio.' + (' Check Twilio before repeating this action.' if writing else ' Check your network and try again.'))
    finally:
        if own:
            await client.aclose()


async def sender_check(cfg):
    """Ask Twilio what the text sender really is, so 'not a valid SMS number' comes with the cause and the numbers that would work. Never raises."""
    sender = cfg.get('sms_sender') or cfg.get('sms_service') or ''
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            owned = (await request('IncomingPhoneNumbers.json', client=client, cfg=cfg, params={'PageSize': 50})).get('incoming_phone_numbers') or []
            mine = {n.get('phone_number'): n.get('capabilities') or {} for n in owned}
            if sender.startswith('+') and sender in mine:
                said = (f'Sender check: {sender} is a number on this Twilio account and can send texts, so the cause is elsewhere (permission to text this country, or the number not being registered for UK texting).'
                        if mine[sender].get('sms') else f'Sender check: {sender} is on this Twilio account but has NO SMS capability (voice only), so it cannot send texts.')
            elif sender.startswith('+'):
                verified = (await request('OutgoingCallerIds.json', client=client, cfg=cfg, params={'PhoneNumber': sender})).get('outgoing_caller_ids') or []
                said = (f'Sender check: {sender} is only a Verified Caller ID. Twilio can show it on calls but cannot send texts from it.' if verified
                        else f'Sender check: {sender} is not a phone number on this Twilio account, so Twilio will not text from it.')
            elif sender.startswith('MG'):
                said = 'Sender check: texts go through a Messaging Service. Open it in Twilio Console and make sure its sender pool holds an SMS-capable number.'
            elif sender:
                said = f"Sender check: texts are sent under the name '{sender}'. Trial accounts and some countries refuse name senders."
            else:
                said = 'Sender check: no sender is set. Add an SMS sender (or a caller ID) in Apps → Twilio.'
    except Exception:
        return ''
    good = [n for n, c in mine.items() if n and c.get('sms')]
    return said + ' SMS-capable numbers on this account: ' + (', '.join(good[:5]) if good else 'none. Buy one in Twilio Console → Phone Numbers → Buy a number (tick SMS), then enter it as the SMS sender in Apps → Twilio.')


async def send_permission(chat):
    key = (db.current(), chat['id'])
    lock = _permission_locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        raise wa_call.CallError('A calling permission request is already being sent for this customer.')
    async with lock:
        return await _send_permission(chat)


async def _send_permission(chat):
    chat = chats._chat(chat['id'])
    if not chats.window(chat)[0]:
        raise wa_call.CallError('Ask for calling permission during an active WhatsApp conversation.')
    if time.time() - (chat['call_perm_asked_at'] or 0) < 86400:
        raise wa_call.CallError('Calling permission was already requested in the last 24 hours.')
    recent = db.q1("SELECT COUNT(*) n FROM chat_messages WHERE chat_id=? AND kind='call_permission_request' AND status='sent' AND sent_at>?",
        (chat['id'], time.time() - 7 * 86400))['n']
    if recent >= 2:
        raise wa_call.CallError('Calling permission was already requested twice in the last seven days.')
    base_url()
    cfg = config()
    sid = cfg.get('call_permission_content_sid', '')
    if not re.fullmatch(r'HX[0-9a-fA-F]{32}', sid):
        raise wa_call.CallError('Add a Voice Call Request Content SID in the Twilio app settings.')
    return await request('Messages.json', {'From': 'whatsapp:' + cfg['whatsapp_from'],
        'To': 'whatsapp:+' + chats._digits(chat['ext_id']), 'ContentSid': sid}, cfg=cfg)


def permission_reply(form):
    if form.get('Body') != 'VOICE_CALL_REQUEST' or not form.get('From', '').startswith('whatsapp:'):
        return False
    choice = form.get('ButtonPayload', '')
    if choice not in ('ACCEPTED', 'REJECTED'):
        return True
    chat, row = chats.ingest('whatsapp', 'twilio', chats._digits(form['From']), '',
        name=form.get('ProfileName', ''), msg_id=form.get('MessageSid', ''), kind='call_permission_reply', quiet=True)
    if row:  # repeated webhooks must not extend permission
        granted = choice == 'ACCEPTED'
        permanent = form.get('CallPermissionIsPermanent', '').lower() == 'true'
        until = (253402300799 if permanent else time.time() + 7 * 86400) if granted else 0
        # Twilio may be the voice transport for a chat originally created by Meta.
        # Keep consent attached to that canonical conversation too.
        for other in db.q("SELECT id FROM chats WHERE channel='whatsapp' AND ext_id=?", (chats._digits(form['From']),)):
            db.update('chats', other['id'], call_perm='granted' if granted else 'denied', call_perm_until=until)
    return True


class AudioOutput:
    """Bounded PCM -> mu-law output with Twilio playback acknowledgements."""
    def __init__(self):
        self.src_rate = 24000
        self.queue = asyncio.Queue(maxsize=3000)
        self.pending = set()
        self.serial = 0
        self.generation = 0
        self.rs = wa_call.av.AudioResampler(format='s16', layout='mono', rate=8000)
        self.encoder = wa_call.av.CodecContext.create('pcm_mulaw', 'w')
        self.encoder.sample_rate = 8000
        self.encoder.layout = 'mono'
        self.encoder.format = 's16'

    def set_rate(self, rate):
        if rate != self.src_rate:
            self.src_rate = rate
            self.rs = wa_call.av.AudioResampler(format='s16', layout='mono', rate=8000)

    def push(self, pcm):
        if not pcm:
            return
        pcm = pcm[:len(pcm) // 2 * 2]
        frame = wa_call.av.AudioFrame(format='s16', layout='mono', samples=len(pcm) // 2)
        frame.sample_rate = self.src_rate
        frame.planes[0].update(pcm)
        for f in self.rs.resample(frame):
            for packet in self.encoder.encode(f):
                audio = bytes(packet)
                for offset in range(0, len(audio), 160):
                    self.queue.put_nowait(('media', audio[offset:offset + 160], self.generation))

    def clear(self):
        self.generation += 1
        while not self.queue.empty():
            self.queue.get_nowait()
        self.pending.clear()
        self.queue.put_nowait(('clear', None, self.generation))
        self.rs = wa_call.av.AudioResampler(format='s16', layout='mono', rate=8000)

    @property
    def speaking(self):
        return not self.queue.empty() or bool(self.pending)


class Session(wa_call.Session):
    def __init__(self, *args, pstn=False):
        super().__init__(*args, rtc=False)
        self.pstn = pstn
        self.cfg = voice_config() if pstn else config()
        self.out = AudioOutput()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5))
        self.socket = None
        self.stream_sid = None
        self.first_audio_sent = False
        self.provider_answered = False
        self.decoder = wa_call.av.CodecContext.create('pcm_mulaw', 'r')
        self.decoder.sample_rate = 8000
        self.decoder.layout = 'mono'
        self.resampler = wa_call.av.AudioResampler(format='s16', layout='mono', rate=24000)

    def twiml(self):
        root = Element('Response')
        stream = SubElement(SubElement(root, 'Connect'), 'Stream',
            url=hook_url().replace('https://', 'wss://', 1) + f'/media/{self.call_id}')
        SubElement(stream, 'Parameter', name='call_id', value=str(self.call_id))
        SubElement(root, 'Hangup')
        return tostring(root, encoding='unicode')

    async def place(self):
        started = time.monotonic()
        sender = self.cfg['from_number'] if self.pstn else 'whatsapp:' + self.cfg['whatsapp_from']
        recipient = '+' + chats._digits(self.chat['ext_id'])
        d = await request('Calls.json', {'From': sender,
            'To': recipient if self.pstn else 'whatsapp:' + recipient, 'Twiml': self.twiml(),
            'Timeout': str(wa_call.RING_SECONDS), 'TimeLimit': str(wa_call.MAX_MINUTES * 60),
            'StatusCallback': hook_url() + f'/status/{self.call_id}', 'StatusCallbackMethod': 'POST',
            'StatusCallbackEvent': ['initiated', 'ringing', 'answered', 'completed']},
            self.client, self.cfg)
        sid = d.get('sid', '')
        if not re.fullmatch(r'CA[0-9a-fA-F]{32}', sid):
            raise wa_call.CallError('Twilio returned no valid call SID. Check Twilio call history before calling again.')
        if self.wa_call_id and self.wa_call_id != sid:
            raise wa_call.CallError('Twilio returned an unexpected call SID. Check Twilio call history.')
        self.wa_call_id = sid
        db.log('call', f'Twilio accepted call in {round((time.monotonic() - started) * 1000)} ms', call_id=self.call_id)
        db.update('calls', self.call_id, ext_id=self.wa_call_id)
        self._spawn(self._ring_timeout())

    async def _terminate(self):
        await request(f'Calls/{self.wa_call_id}.json', {'Status': 'completed'}, self.client, self.cfg)

    async def _close_transport(self):
        if self.socket:
            try:
                await self.socket.close()
            except RuntimeError:
                pass
        await self.client.aclose()

    async def send_output(self):
        try:
            while not self.ended:
                kind, payload, generation = await self.out.queue.get()
                if generation != self.out.generation:
                    continue
                if kind == 'clear':
                    await self.socket.send_json({'event': 'clear', 'streamSid': self.stream_sid})
                else:
                    if not self.first_audio_sent:
                        self.first_audio_sent = True
                        delay = round((time.time() - (self.t_answer or time.time())) * 1000)
                        db.log('call', f'Twilio first assistant audio after {delay} ms', call_id=self.call_id)
                    self.out.serial += 1
                    mark = str(self.out.serial)
                    self.out.pending.add(mark)
                    await self.socket.send_json({'event': 'media', 'streamSid': self.stream_sid,
                        'media': {'payload': base64.b64encode(payload).decode()}})
                    await self.socket.send_json({'event': 'mark', 'streamSid': self.stream_sid, 'mark': {'name': mark}})
                    # Keep Twilio's playback buffer short so interruptions take effect promptly.
                    await asyncio.sleep(len(payload) / 8000)
        except Exception:
            await self.end('failed', 'Audio connection failed.')

    async def receive_audio(self, payload):
        if not self.engine:
            return
        packet = wa_call.av.Packet(base64.b64decode(payload, validate=True))
        for frame in self.decoder.decode(packet):
            for f in self.resampler.resample(frame):
                await self.engine.send_audio(bytes(f.planes[0])[:f.samples * 2])


def signature_ok(connection, form, cfg, *, websocket=False):
    url = base_url() + connection.url.path
    if connection.url.query:
        url += '?' + connection.url.query
    if websocket:
        # Twilio signs Media Streams handshakes inconsistently across its edge
        # proxies: some signatures use the HTTPS callback origin, others the
        # WSS origin. It may also normalize the final slash. Validate each
        # canonical variant against the same secret and exact request path.
        ws_url = url.replace('https://', 'wss://', 1)
        candidates = tuple(dict.fromkeys((url, url + '/', ws_url, ws_url + '/')))
    else:
        candidates = (url,)
    supplied = connection.headers.get('x-twilio-signature', '')
    for candidate in candidates:
        data = candidate + ''.join(k + form[k] for k in sorted(form))
        signature = base64.b64encode(hmac.new(cfg['auth_token'].encode(), data.encode(), hashlib.sha1).digest()).decode()
        if hmac.compare_digest(signature, supplied):
            return True
    return False


async def signed_form(request, secret, *, voice=False):
    if not chats.check_secret(secret):
        raise HTTPException(404, 'Not found')
    try:
        # PSTN status callbacks must validate against Voice credentials alone:
        # a WhatsApp sender is optional for regular phone calls.
        cfg = voice_config(require_write=False) if voice else config(require_write=False)
        base_url()
    except wa_call.CallError:
        raise HTTPException(503, 'Twilio calling is not configured')
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(413, 'Too large')
    try:
        pairs = parse_qsl(raw.decode('utf-8'), keep_blank_values=True, max_num_fields=100)
    except ValueError:
        raise HTTPException(400, 'Invalid webhook form')
    form = dict(pairs)
    if len(form) != len(pairs):
        raise HTTPException(400, 'Duplicate webhook fields')
    if not signature_ok(request, form, cfg) or form.get('AccountSid') != cfg['account_sid']:
        raise HTTPException(403, 'Bad signature')
    return form


@router.post('/{ws}/{secret}/twilio/voice/status/{cid}')
async def call_status(ws: str, secret: str, cid: int, request: Request):
    form = await signed_form(request, secret, voice=True)
    sess = wa_call.SESSIONS.get((db.current(), cid))
    call = db.q1('SELECT * FROM calls WHERE id=?', (cid,))
    if call and form.get('CallSid') == (sess.wa_call_id if isinstance(sess, Session) else call.get('ext_id')):
        state = form.get('CallStatus')
        if state == 'answered':
            if isinstance(sess, Session):
                sess.provider_answered = True
            db.update('calls', cid, status='in_progress', answered_at=db.now())
        elif state == 'ringing':
            db.update('calls', cid, status='ringing', ringing_at=db.now())
        elif state in ('completed', 'failed', 'busy', 'no-answer', 'canceled'):
            if isinstance(sess, Session):
                if state == 'completed' and sess.provider_answered and not sess.answered:
                    sess._spawn(sess.end('failed', 'The recipient answered, but the AI audio stream did not connect.'))
                else:
                    sess._spawn(sess.end('remote_ended' if state == 'completed' else 'no_answer' if state in ('busy', 'no-answer') else 'failed'))
            else:
                # Twilio's completed means the phone call ended normally. A missing in-memory
                # stream session is not evidence that the call failed: sessions are removed as
                # teardown begins, so the callback can race the transcript/report finalizer.
                seconds = int(form.get('CallDuration') or 0)
                if state == 'completed':
                    if call.get('status') == 'completed' and call.get('report'):
                        pass  # Duplicate callback; preserve the already finalized report.
                    else:
                        # Let the common finalizer persist contact/campaign bookkeeping and a
                        # structured report whether or not the media stream captured speech.
                        db.update('calls', cid, status='in_progress', duration=seconds,
                                  answered_at=call.get('answered_at') or (db.now() - seconds))
                        await callengine.finalize(cid, 'completed')
                else:
                    status = 'no_answer' if state in ('busy', 'no-answer') else 'failed'
                    note = ('The recipient did not answer (Twilio status: ' + state + ').'
                            if status == 'no_answer' else 'Twilio could not connect the call (status: ' + state + ').')
                    report = {'executive_summary': note, 'objective': call.get('reason') or '',
                              'objective_result': 'unclear', 'conversation_flow': 'The call did not connect.',
                              'customer_needs': [], 'questions_and_answers': [], 'objections_or_concerns': [],
                              'commitments': [], 'key_facts_shared': [], 'follow_up_actions': [],
                              'risks_or_gaps': ['No conversation transcript exists because the call did not connect.'],
                              'outcome': status, 'sentiment': 'unknown', 'transcript_status': 'unavailable'}
                    db.update('calls', cid, status=status, ended_at=db.now(), duration=seconds,
                              outcome=status, summary=note, report=report)
                    from . import files
                    files.update_call_sheet(cid, status, note)
                    callengine.announce_call(cid)
    return Response(status_code=204)


@router.post('/{ws}/{secret}/twilio/voice')
async def inbound(ws: str, secret: str, request: Request):
    form = await signed_form(request, secret)
    reject = Response('<Response><Reject reason="busy"/></Response>', media_type='text/xml')
    if chats.setting('wa_inbound_calls', 'off') != 'answer' or not form.get('From', '').startswith('whatsapp:'):
        return reject
    if form.get('To') != 'whatsapp:' + config(require_write=False)['whatsapp_from'] or not re.fullmatch(r'CA[0-9a-fA-F]{32}', form.get('CallSid', '')):
        return reject
    for (workspace, _), existing in wa_call.SESSIONS.items():
        if workspace == db.current() and isinstance(existing, Session) and existing.wa_call_id == form['CallSid']:
            return Response(existing.twiml(), media_type='text/xml')
    # Do not resurrect completed calls on webhook retries.
    if db.q1('SELECT id FROM calls WHERE ext_id=?', (form['CallSid'],)):
        return reject
    from . import agents
    chat = chats.get_or_create('whatsapp', 'twilio', chats._digits(form['From']))
    agent = agents.get(chat['agent_id'] or chats.default_agent_id() or 0)
    try:
        wa_call._checks(chat, agent)
        contact = chat['contact_id'] or chats.contact_for_phone(chat['ext_id'], chat['name'])
        db.update('chats', chat['id'], contact_id=contact)
        cid = db.run('INSERT INTO calls(contact_id,campaign_id,status,mode,direction,objective,chat_id,language,created_at,ringing_at,ext_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (contact, wa_call._campaign_for(agent), 'ringing', 'whatsapp', 'in', 'Help the customer calling us.', chat['id'], 'English', db.now(), db.now(), form['CallSid']))
        sess = Session(db.current(), cid, chat, agent, 'Help the customer calling us.', 'in')
        sess.wa_call_id = form['CallSid']
        wa_call.SESSIONS[(db.current(), cid)] = sess
        sess._spawn(sess._ring_timeout())
        return Response(sess.twiml(), media_type='text/xml')
    except wa_call.CallError:
        return reject


@router.websocket('/{ws}/{secret}/twilio/voice/media/{cid}')
async def media(socket: WebSocket, ws: str, secret: str, cid: int):
    # HTTP middleware does not run for WebSockets: establish and validate tenant explicitly.
    if not (ws == db.MAIN or directory.ID_RE.fullmatch(ws)) or not directory.is_active(ws):
        await socket.close(code=1008)
        return
    with db.use(ws):
        sess = wa_call.SESSIONS.get((ws, cid))
        reason = ('no_local_call_session' if not isinstance(sess, Session) else
                  'stream_already_attached' if sess.socket else
                  'bad_webhook_secret' if not chats.check_secret(secret) else
                  'signature_mismatch' if not signature_ok(socket, {}, sess.cfg, websocket=True) else '')
        if reason:
            db.log('error', f'Twilio media stream rejected: {reason}', call_id=cid)
            await socket.close(code=1008)
            return
        await socket.accept()
        sess.socket = socket
        try:
            while not sess.ended:
                raw = await asyncio.wait_for(socket.receive_text(), timeout=wa_call.IDLE_SECONDS)
                if len(raw) > 65536:
                    raise ValueError('Media message too large')
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError('Invalid media message')
                kind = event.get('event')
                if sess.stream_sid and kind in ('media', 'mark', 'stop') and event.get('streamSid') != sess.stream_sid:
                    raise ValueError('Unexpected stream')
                if kind == 'start':
                    start = event.get('start', {})
                    if not isinstance(start, dict):
                        raise ValueError('Invalid stream start')
                    fmt = start.get('mediaFormat', {})
                    if not sess.wa_call_id and re.fullmatch(r'CA[0-9a-fA-F]{32}', start.get('callSid', '')):
                        # The signed stream can arrive before the REST response.
                        sess.wa_call_id = start['callSid']
                    if sess.stream_sid or start.get('accountSid') != sess.cfg['account_sid'] or start.get('callSid') != sess.wa_call_id or fmt != {'encoding': 'audio/x-mulaw', 'sampleRate': 8000, 'channels': 1}:
                        await sess.end('failed', 'Unexpected Twilio media stream.')
                        break
                    sess.stream_sid = start['streamSid']
                    await sess.answered_now()
                    sess._spawn(sess.send_output())
                elif kind == 'media' and sess.stream_sid:
                    await sess.receive_audio(event['media']['payload'])
                elif kind == 'mark':
                    sess.out.pending.discard(event.get('mark', {}).get('name'))
                elif kind == 'stop':
                    break
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
            pass
        finally:
            await sess.end('remote_ended')


@router.on_event('shutdown')
async def shutdown():
    sessions = [s for s in list(wa_call.SESSIONS.values()) if isinstance(s, Session)]
    await asyncio.gather(*(wa_call._in_ws(s.ws, s.end('completed', 'Server shutting down.')) for s in sessions), return_exceptions=True)

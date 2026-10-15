"""OpenAI plumbing, mirrors the ai-tutor setup (env key + AsyncOpenAI)."""
import io
import os
import re

import dotenv
from openai import AsyncOpenAI

dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
dotenv.load_dotenv()


def api_key():
    """The company's OpenAI key (Settings → Connections). Environment keys are moved into the app at start-up."""
    from . import keys
    return keys.get("openai")


def eleven_key():
    from . import keys
    return keys.get("eleven")

CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1")          # strong in Arabic/Hindi/Urdu/Bengali, as fast as mini
# Everything that is not a live phone call uses cheap models: meetings, background work, summaries, reports.
CHEAP_MODEL = os.getenv("CHEAP_MODEL", "gpt-4.1-mini")
MEETING_REALTIME_MODEL = os.getenv("MEETING_REALTIME_MODEL", "gpt-realtime-2.1-mini")
STT_MODEL = os.getenv("STT_MODEL", "gpt-4o-transcribe")  # far better than whisper-1 on South Asian + Arabic speech
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")    # steerable, natural, multilingual
AGENT_VOICE = os.getenv("AGENT_VOICE", "marin")
def company_name():
    """The company the team works for, set in Settings → Company (falls back to the COMPANY_NAME env var)."""
    from . import voices
    return voices.get_setting("company_name", "") or os.getenv("COMPANY_NAME", "") or "your company"


def company_about():
    from . import voices
    return voices.get_setting("company_about", "")


def company_tz():
    """The company's time zone (Settings → Company), else this server's."""
    import datetime as dt
    from . import voices
    name = voices.get_setting("timezone", "")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return dt.datetime.now().astimezone().tzinfo
REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-realtime-2.1")  # speech-to-speech: one model hears and speaks
REALTIME_VOICES = ["marin", "cedar", "coral", "sage", "ballad", "verse", "ash", "alloy", "echo", "shimmer"]
VOICES = ["marin", "cedar", "coral", "sage", "ballad", "verse", "ash", "alloy", "echo", "fable", "nova", "onyx", "shimmer"]
# Native-speaker voices (the OpenAI voices are English-native and sound foreign in Arabic/Hindi/Urdu/Bengali).
# Provide ONE of these providers:
ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID")            # default voice; override per language with ELEVENLABS_VOICE_AR, _HI, _UR, _BN ...
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_v4_turbo")  # fast (~0.2s first byte) and covers Arabic, Hindi, Urdu, Bengali
AZURE_KEY = os.getenv("AZURE_SPEECH_KEY")
AZURE_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")
FORCE_EXTERNAL = os.getenv("FORCE_EXTERNAL_TTS", "").lower() == "true"  # testing: route non-English through /api/tts even with no provider

AZURE_VOICES = {
    "ar": ("ar-SA-ZariyahNeural", "ar-SA"), "hi": ("hi-IN-SwaraNeural", "hi-IN"), "ur": ("ur-PK-UzmaNeural", "ur-PK"),
    "bn": ("bn-BD-NabanitaNeural", "bn-BD"), "zh": ("zh-CN-XiaoxiaoNeural", "zh-CN"), "id": ("id-ID-GadisNeural", "id-ID"),
    "ja": ("ja-JP-NanamiNeural", "ja-JP"), "ko": ("ko-KR-SunHiNeural", "ko-KR"), "ta": ("ta-IN-PallaviNeural", "ta-IN"),
    "te": ("te-IN-ShrutiNeural", "te-IN"), "th": ("th-TH-PremwadeeNeural", "th-TH"), "vi": ("vi-VN-HoaiMyNeural", "vi-VN"),
    "fa": ("fa-IR-DilaraNeural", "fa-IR"), "ms": ("ms-MY-YasminNeural", "ms-MY"), "tl": ("fil-PH-BlessicaNeural", "fil-PH"),
    "tr": ("tr-TR-EmelNeural", "tr-TR"), "he": ("he-IL-HilaNeural", "he-IL"), "es": ("es-ES-ElviraNeural", "es-ES"),
    "fr": ("fr-FR-DeniseNeural", "fr-FR"), "de": ("de-DE-KatjaNeural", "de-DE"), "ru": ("ru-RU-SvetlanaNeural", "ru-RU"),
    "pt": ("pt-BR-FranciscaNeural", "pt-BR"), "it": ("it-IT-ElsaNeural", "it-IT"), "pa": ("pa-IN-OjasNeural", "pa-IN"),
}
ELEVEN_MODEL_BY_LANG = {}  # per-language override via ELEVENLABS_MODEL_<CODE> env, e.g. ELEVENLABS_MODEL_UR


def eleven_voice(code):
    return os.getenv(f"ELEVENLABS_VOICE_{(code or '').upper()}") or ELEVEN_VOICE


def native_provider():
    return "elevenlabs" if eleven_key() else "azure" if AZURE_KEY else None


def use_external_voice(language):
    """True when this language should be spoken by a native-voice provider instead of OpenAI's own voices."""
    code = lang_code(language)
    if not code or code == "en":
        return False
    if FORCE_EXTERNAL:
        return True
    prov = native_provider()
    return bool((prov == "elevenlabs" and eleven_voice(code)) or prov == "azure")


_probe = None  # whether a voice works, per workspace (each company has its own ElevenLabs account); created in voice_probe


async def voice_probe(language):
    """(ok, message): can the configured native voice actually speak this language right now? Cached for a minute."""
    import time as _t
    code = lang_code(language) or ""
    global _probe
    if _probe is None:
        from . import db
        _probe = db.TenantDict()
    hit = _probe.get(code)
    if hit and _t.time() - hit[0] < 60:
        return hit[1], hit[2]
    prov = native_provider()
    try:
        await (_eleven(".", language) if prov == "elevenlabs" else _azure(".", language))
        res = (True, f"{prov} voice")
    except Exception as e:
        msg = str(e)
        if "402" in msg or "paid_plan" in msg:
            msg = "ElevenLabs refused the voice: library voices need a paid plan."
        res = (False, msg[:160])
    _probe[code] = (_t.time(), *res)
    return res


LANG_CODES = {
    "english": "en", "arabic": "ar", "hindi": "hi", "urdu": "ur", "bengali": "bn", "chinese": "zh", "japanese": "ja",
    "korean": "ko", "indonesian": "id", "malay": "ms", "tagalog": "tl", "tamil": "ta", "telugu": "te", "thai": "th",
    "vietnamese": "vi", "persian": "fa", "punjabi": "pa", "turkish": "tr", "hebrew": "he", "spanish": "es",
    "french": "fr", "german": "de", "russian": "ru", "portuguese": "pt", "italian": "it",
}


def lang_code(language):
    return LANG_CODES.get((language or "").strip().lower())


_clients = {}  # key -> tracked client (a different key, for another company, gets its own client)


class NoKeyError(RuntimeError):
    pass


def client():
    key = api_key()
    if not key:
        raise NoKeyError("Connect your OpenAI key in Settings → Connections.")
    c = _clients.get(key)
    if c is None:
        if len(_clients) >= 200:  # forget the oldest rather than grow without end
            _clients.pop(next(iter(_clients)))
        c = _clients[key] = _Tracked(AsyncOpenAI(api_key=key, timeout=60.0))
    return c


class _TrackedStream:
    """Passes chunks through unchanged and records the token usage that arrives with the last one."""
    def __init__(self, stream, model, who):
        self._s, self._model, self._who = stream, model, who

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        usage = None
        try:
            async for chunk in self._s:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                yield chunk
        finally:
            _record(self._model, usage, self._who)

    def __getattr__(self, n):
        return getattr(self._s, n)


def _record(model, usage, who):
    try:
        from . import costs
        costs.record_usage(model, usage, *who)
    except Exception:
        pass  # money tracking must never break a call


class _Completions:
    def __init__(self, inner):
        self._i = inner

    async def create(self, **kw):
        from . import costs
        who = costs.current()
        if kw.get("stream") and "stream_options" not in kw:
            kw["stream_options"] = {"include_usage": True}
        r = await self._i.create(**kw)
        if kw.get("stream"):
            return _TrackedStream(r, kw.get("model"), who)
        _record(kw.get("model"), getattr(r, "usage", None), who)
        return r

    def __getattr__(self, n):
        return getattr(self._i, n)


class _Chat:
    def __init__(self, inner):
        self.completions = _Completions(inner.completions)
        self._i = inner

    def __getattr__(self, n):
        return getattr(self._i, n)


class _Embeddings:
    def __init__(self, inner):
        self._i = inner

    async def create(self, **kw):
        from . import costs
        r = await self._i.create(**kw)
        _record(kw.get("model"), getattr(r, "usage", None), costs.current())
        return r

    def __getattr__(self, n):
        return getattr(self._i, n)


class _Speech:
    def __init__(self, inner):
        self._i = inner

    async def create(self, **kw):
        from . import costs
        r = await self._i.create(**kw)
        agent, purpose = costs.current()
        costs.record_estimate(agent, purpose if purpose != "other" else "speech", costs.speech_cost(len(str(kw.get("input", "")))), str(kw.get("model")), len(str(kw.get("input", ""))))
        return r

    def __getattr__(self, n):
        return getattr(self._i, n)


class _Audio:
    def __init__(self, inner):
        self.speech = _Speech(inner.speech)
        self._i = inner

    def __getattr__(self, n):
        return getattr(self._i, n)


class _Tracked:
    """The OpenAI client, with money tracking added to chat, embeddings and speech. Everything else passes straight through."""
    def __init__(self, inner):
        self._i = inner
        self.chat = _Chat(inner.chat)
        self.embeddings = _Embeddings(inner.embeddings)
        self.audio = _Audio(inner.audio)

    def __getattr__(self, n):
        return getattr(self._i, n)


def has_key():
    return bool(api_key())


def _voice_instructions(language):
    lang = language or "English"
    return (f"Voice: warm, friendly and relaxed, like a real person on a phone call. Speak {lang} as a native speaker with a natural "
            f"local accent, intonation and rhythm. Conversational pace with brief natural pauses. Never robotic or like reading aloud.")


async def _eleven(text, language=None):
    import httpx
    code = lang_code(language) or ""
    voice = eleven_voice(code)
    model = os.getenv(f"ELEVENLABS_MODEL_{code.upper()}") or ELEVEN_MODEL_BY_LANG.get(code) or ELEVEN_MODEL
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_64"
    body = {"text": text, "model_id": model}
    if code and model != "eleven_v3":
        body["language_code"] = code
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.post(url, headers={"xi-api-key": eleven_key()}, json=body)
        r.raise_for_status()
        return r.content


async def _azure(text, language=None):
    import httpx
    from xml.sax.saxutils import escape
    code = lang_code(language) or ""
    name, loc = AZURE_VOICES.get(code, ("en-US-JennyNeural", "en-US"))
    name = os.getenv(f"AZURE_VOICE_{code.upper()}") or name
    ssml = f"<speak version='1.0' xml:lang='{loc}'><voice name='{name}'>{escape(text)}</voice></speak>"
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.post(f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1",
                         headers={"Ocp-Apim-Subscription-Key": AZURE_KEY, "Content-Type": "application/ssml+xml",
                                  "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "deskmates"},
                         content=ssml.encode("utf-8"))
        r.raise_for_status()
        return r.content


async def tts(text, voice=None, language=None, purpose=""):
    """Text to speech. ElevenLabs (paid tokens) is only ever used when purpose == 'call'; everything else uses OpenAI."""
    text = text[:4000]
    prov = native_provider() if purpose == "call" else None
    if prov and lang_code(language) != "en" and (prov != "elevenlabs" or eleven_voice(lang_code(language))):
        try:
            return await (_eleven(text, language) if prov == "elevenlabs" else _azure(text, language))
        except Exception:
            pass  # fall back to OpenAI
    voice = voice if voice in VOICES else AGENT_VOICE
    kw = {"instructions": _voice_instructions(language)} if TTS_MODEL.startswith("gpt-4o") else {}
    try:
        resp = await client().audio.speech.create(model=TTS_MODEL, voice=voice, input=text, **kw)
    except Exception:
        resp = await client().audio.speech.create(model="tts-1", voice="nova" if voice not in ("alloy", "echo", "fable", "onyx", "nova", "shimmer") else voice, input=text)
    return resp.content


async def tts_agent(text, voice="marin", instructions=""):
    """A team member's voice in meetings (OpenAI), shaped by the agent's personality."""
    voice = voice if voice in VOICES else "marin"
    resp = await client().audio.speech.create(model="gpt-4o-mini-tts", voice=voice, input=text[:3000], instructions=instructions[:900], response_format="mp3")
    return resp.content


async def realtime_meeting_secret(instructions, voice, tools, listener=True, auto_reply=True):
    """A live speech-to-speech session for one team member in a meeting (OpenAI Realtime over WebRTC)."""
    import httpx
    if not api_key():
        raise NoKeyError("Connect your OpenAI key in Settings → Connections.")
    voice = voice if voice in REALTIME_VOICES else "marin"
    inp = {"turn_detection": {"type": "semantic_vad", "eagerness": "medium", "create_response": auto_reply}, "noise_reduction": {"type": "near_field"}}
    if listener:
        inp["transcription"] = {"model": "gpt-4o-mini-transcribe", "language": "en"}
    body = {"session": {"type": "realtime", "model": MEETING_REALTIME_MODEL, "instructions": instructions, "output_modalities": ["audio"],
                        "audio": {"input": inp, "output": {"voice": voice, "speed": 1.05}},
                        "max_output_tokens": 700, "tools": tools, "tool_choice": "auto"}}
    async with httpx.AsyncClient(timeout=20) as h:
        r = await h.post("https://api.openai.com/v1/realtime/client_secrets", headers={"Authorization": f"Bearer {api_key()}"}, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"Realtime session failed: {r.text[:300]}")
        return {"secret": r.json()["value"], "model": MEETING_REALTIME_MODEL, "voice": voice}


async def stt(data: bytes, filename="audio.webm", language=None):
    f = io.BytesIO(data)
    f.name = filename
    kw = {"language": lang_code(language)} if lang_code(language) else {}
    try:
        resp = await client().audio.transcriptions.create(model=STT_MODEL, file=f, **kw)
    except Exception:
        f.seek(0)
        resp = await client().audio.transcriptions.create(model="whisper-1", file=f)
    return (resp.text or "").strip()


async def complete_json(system, user, model=None):
    resp = await client().chat.completions.create(
        model=model or CHEAP_MODEL,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    import json
    try:
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {}


async def complete_text(system, user, model=None):
    resp = await client().chat.completions.create(
        model=model or CHEAP_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


# sentence ends: ! ? ؟ ۔ । ॥ 。！？ need no space after; a Latin "." needs whitespace (decimals like 3.5)
SENT_RE = re.compile(r"(.+?(?:[!?\u061F\u06D4\u0964\u0965\u3002\uFF01\uFF1F]+[\"')\]]*|\.+[\"')\]]*\s+|\n))", re.S)
CLAUSE_RE = re.compile(r"(.{14,}?[,\u060C\uFF0C\u3001;\u061B:]\s*)", re.S)


def split_sentences(buf, eager=False):
    """Return (complete_sentences, remainder) from a streaming text buffer.
    eager=True also allows a clause break so the very first audio chunk starts sooner."""
    out, pos = [], 0
    for m in SENT_RE.finditer(buf):
        out.append(m.group(1).strip())
        pos = m.end()
    rest = buf[pos:]
    if eager and not out:
        m = CLAUSE_RE.match(rest)
        if m:
            out.append(m.group(1).strip())
            rest = rest[m.end():]
    return [x for x in out if x], rest


from . import agent_tools  # noqa: E402


async def realtime_secret(instructions, language=None, voice=None, external=False):
    """Mint a short-lived key so the browser (later: Twilio bridge) can talk to the Realtime API directly."""
    import httpx
    if not api_key():
        raise NoKeyError("Connect your OpenAI key in Settings → Connections.")
    voice = voice if voice in REALTIME_VOICES else (AGENT_VOICE if AGENT_VOICE in REALTIME_VOICES else "marin")
    transcription = {"model": "gpt-4o-transcribe"}
    if lang_code(language):
        transcription["language"] = lang_code(language)
    body = {"session": {
        "type": "realtime", "model": REALTIME_MODEL, "instructions": instructions,
        "output_modalities": ["text"] if external else ["audio"],
        "audio": {
            "input": {"transcription": transcription, "turn_detection": {"type": "semantic_vad", "eagerness": "medium"}, "noise_reduction": {"type": "near_field"}},
            **({} if external else {"output": {"voice": voice, "speed": 1.1}}),
        },
        "max_output_tokens": 600,
        "tools": [{"type": "function", "name": "lookup_info",
                   "description": "Look up facts about the company: products or services, prices, requirements, dates, how to get started or support. Use before stating any fact.",
                   "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "e.g. 'price of the standard plan'"},
                       "category": {"type": "string", "description": "Optional: Products & services, Pricing & payment, Requirements, How to get started, FAQ, Policies, Objection handling, Contact & locations, Support"}}, "required": ["query"]}},
                  {"type": "function", "name": "end_call",
                   "description": "Hang up. Call it right after you have said your goodbye, when the conversation is finished.",
                   "parameters": {"type": "object", "properties": {}, "required": []}},
                  *agent_tools.realtime_defs()],
        "tool_choice": "auto",
    }}
    async with httpx.AsyncClient(timeout=20) as h:
        r = await h.post("https://api.openai.com/v1/realtime/client_secrets", headers={"Authorization": f"Bearer {api_key()}"}, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"Realtime session failed: {r.text[:300]}")
        return {"secret": r.json()["value"], "model": REALTIME_MODEL, "voice": voice}

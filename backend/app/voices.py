"""Settings + voice profiles. A profile = which voice / ElevenLabs agent / spoken name / dialect to use for a country or language."""
import json

from . import countries, db, llm

db.run("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
db.run("""CREATE TABLE IF NOT EXISTS voice_profiles(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, language TEXT, countries TEXT DEFAULT '[]', voice_id TEXT,
  agent_name TEXT DEFAULT '', dialect TEXT DEFAULT '', created_at REAL)""")
for _stmt in ("ALTER TABLE contacts ADD COLUMN country TEXT", "ALTER TABLE contacts ADD COLUMN lang_source TEXT",
              "ALTER TABLE voice_profiles ADD COLUMN tuning TEXT"):
    try:
        db.run(_stmt)
    except Exception:
        pass

SEEDS = [  # (name, language, calling codes, ElevenLabs voice id, spoken name, dialect)
    ("Hindi (India)", "Hindi", ["91"], "OtEfb2LVzIE45wdYe54M", "Priya", "natural everyday Hindi"),
    ("Urdu (Pakistan)", "Urdu", ["92"], "o85TqPN3F4P7dWae2paA", "Ayesha", "natural everyday Urdu"),
    ("Bengali (Bangladesh)", "Bengali", ["880"], "q4yUdydpWxqfmkQH7gK7", "Nusrat", "natural everyday Bangla"),
    ("Arabic (Gulf)", "Arabic", ["966", "971", "974", "965", "968", "973"], "QsV9PCczMIklRM6xLPAS", "Layla", "Gulf (Khaleeji) Arabic, simple and lightly colloquial"),
    ("Arabic (Egypt)", "Arabic", ["20"], "L10lEremDiJfPicq5CPh", "Salma", "Egyptian Arabic, friendly and colloquial"),
    ("Arabic (Levant)", "Arabic", ["961", "962", "963", "970"], "a1KZUXKFVFDOb33I1uqr", "Rania", "Levantine Arabic, warm and colloquial"),
    ("Arabic (Tunisia)", "Arabic", ["216"], "GLRyn2pNxpZ4FAjmlY3z", "Rima", "Tunisian-friendly simple Arabic"),
    ("Arabic (default)", "Arabic", [], "ML7jGRDg4E9hIl5qEm1Z", "Layla", "clear, simple Modern Standard Arabic with a light colloquial feel"),
    ("English", "English", [], "Xb7hH8MSUJpSbSDYk0k2", "", "natural everyday spoken English, warm, super realistic with natural breathing pauses and conversational flow"),
]


def _seed():
    if db.q1("SELECT id FROM voice_profiles LIMIT 1"):
        return
    for name, lang, codes, vid, agent, dialect in SEEDS:
        code = llm.lang_code(lang)
        # an explicit env voice for the language wins over my defaults
        env = llm.eleven_voice(code) if (llm.eleven_key() and not codes) else None
        db.run("INSERT INTO voice_profiles(name,language,countries,voice_id,agent_name,dialect,created_at) VALUES(?,?,?,?,?,?,?)",
               (name, lang, json.dumps(codes), env or vid, agent, dialect, db.now()))


_seed()


def _ensure_english():
    """Databases created before English had its own voice profile get one (a built-in ElevenLabs voice)."""
    if not any(p["language"] == "English" for p in profiles()):
        db.run("INSERT INTO voice_profiles(name,language,countries,voice_id,agent_name,dialect,created_at) VALUES(?,?,?,?,?,?,?)",
               ("English", "English", "[]", "Xb7hH8MSUJpSbSDYk0k2", "", "natural everyday spoken English, warm, super realistic with natural breathing pauses and conversational flow", db.now()))




# One set of company details for the whole company, whichever of its workspaces is open. A new workspace used to start with only its own name
# as "the company", so its callers introduced themselves as e.g. "Sales team", and it had no company website knowledge.
COMPANY_WIDE = {"company_name", "company_about", "company_website", "company_website_kb_id", "timezone", "default_calling_code", "main_link", "application_link"}


def home_of(key):
    """The workspace a setting is kept in: company-wide details in the company's own workspace, everything else in the open one."""
    if key in COMPANY_WIDE:
        try:
            return db.company()
        except RuntimeError:  # no workspace selected (start-up): behave as before
            pass
    return db.current()


def get_setting(key, default=None):
    with db.use(home_of(key)):
        return _get_setting(key, default)


def _get_setting(key, default=None):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    if key == "voice_engine":
        # "openai" saved before ElevenLabs became the default was the old default, not a decision: only a choice made since then sticks
        if r and (r["value"] != "openai" or db.q1("SELECT 1 x FROM settings WHERE key='voice_engine_chosen'")):
            return r["value"]
        return "elevenlabs"
    if r:
        return r["value"]
    return default


def set_setting(key, value):
    with db.use(home_of(key)):
        db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    if key == "voice_engine":
        db.run("INSERT OR REPLACE INTO settings(key,value) VALUES('voice_engine_chosen','1')")


def profiles():
    rows = db.q("SELECT * FROM voice_profiles ORDER BY language, id")
    # New workspaces get their own database after this module has loaded, so
    # import-time seeding only initializes `main`. Seed each tenant lazily.
    if not rows:
        _seed()
        rows = db.q("SELECT * FROM voice_profiles ORDER BY language, id")
    if not any(r["language"] == "English" for r in rows):
        db.run("INSERT INTO voice_profiles(name,language,countries,voice_id,agent_name,dialect,created_at) VALUES(?,?,?,?,?,?,?)",
               ("English", "English", "[]", "Xb7hH8MSUJpSbSDYk0k2", "", "natural everyday spoken English, warm, super realistic with natural breathing pauses and conversational flow", db.now()))
        rows = db.q("SELECT * FROM voice_profiles ORDER BY language, id")
    for r in rows:
        r["countries"] = db.jload(r["countries"], [])
        r["tuning"] = db.jload(r.get("tuning"), {})
    return rows


def resolve(contact):
    """Best voice profile for a person: same language AND their calling code, else the language's default."""
    lang = (contact.get("language") or "").lower()
    info = countries.lookup(contact.get("phone"))
    cands = [p for p in profiles() if p["language"].lower() == lang]
    if not cands:
        return None
    if info:
        for p in cands:
            if info["code"] in p["countries"]:
                return p
    return next((p for p in cands if not p["countries"]), cands[0])


def english_only():
    return get_setting("english_only", "1") == "1"


def choose_language(phone, sheet_language=None, existing=None):
    """-> (language, source). Mode 'smart' (default): phone code wins when it is reliable; for countries where it says little
    (UK, US...) the spreadsheet's language is used if given. 'phone' = always phone code, 'sheet' = always spreadsheet."""
    if english_only():
        return "English", "english-only"
    mode = get_setting("language_source", "smart")
    info = countries.lookup(phone)
    sheet = (sheet_language or "").strip()
    if mode == "sheet" and sheet:
        return sheet, "sheet"
    if info and (mode == "phone" or info["sure"] or not sheet):
        return info["language"], "phone"
    if sheet:
        return sheet, "sheet"
    return (info["language"], "phone") if info else (existing or "English", "default")


_ensure_english()

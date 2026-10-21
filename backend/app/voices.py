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
    ("English", "English", [], "Xb7hH8MSUJpSbSDYk0k2", "", "natural English, warm, relaxed and conversational"),
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
               ("English", "English", "[]", "Xb7hH8MSUJpSbSDYk0k2", "", "natural English, warm, relaxed and conversational", db.now()))




def get_setting(key, default=None):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


def profiles():
    rows = db.q("SELECT * FROM voice_profiles ORDER BY language, id")
    # New workspaces get their own database after this module has loaded, so
    # import-time seeding only initializes `main`. Seed each tenant lazily.
    if not rows:
        _seed()
        rows = db.q("SELECT * FROM voice_profiles ORDER BY language, id")
    if not any(r["language"] == "English" for r in rows):
        db.run("INSERT INTO voice_profiles(name,language,countries,voice_id,agent_name,dialect,created_at) VALUES(?,?,?,?,?,?,?)",
               ("English", "English", "[]", "Xb7hH8MSUJpSbSDYk0k2", "", "natural English, warm, relaxed and conversational", db.now()))
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

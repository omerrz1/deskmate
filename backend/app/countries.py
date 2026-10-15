"""Calling-code -> country / default language. `sure=False` marks countries where the phone code says little about
the person's language (e.g. UK numbers belong to people speaking Urdu, Bengali, Arabic...)."""

# code: (iso2, country, language, sure)
COUNTRIES = {
    "44": ("GB", "United Kingdom", "English", False), "1": ("US", "United States / Canada", "English", False),
    "353": ("IE", "Ireland", "English", False), "61": ("AU", "Australia", "English", False), "64": ("NZ", "New Zealand", "English", False),
    "27": ("ZA", "South Africa", "English", False), "234": ("NG", "Nigeria", "English", True), "254": ("KE", "Kenya", "English", True),
    "91": ("IN", "India", "Hindi", True), "92": ("PK", "Pakistan", "Urdu", True), "880": ("BD", "Bangladesh", "Bengali", True),
    "94": ("LK", "Sri Lanka", "Tamil", True), "977": ("NP", "Nepal", "Nepali", True), "93": ("AF", "Afghanistan", "Persian", True),
    "966": ("SA", "Saudi Arabia", "Arabic", True), "971": ("AE", "United Arab Emirates", "Arabic", True), "974": ("QA", "Qatar", "Arabic", True),
    "965": ("KW", "Kuwait", "Arabic", True), "968": ("OM", "Oman", "Arabic", True), "973": ("BH", "Bahrain", "Arabic", True),
    "967": ("YE", "Yemen", "Arabic", True), "964": ("IQ", "Iraq", "Arabic", True), "962": ("JO", "Jordan", "Arabic", True),
    "961": ("LB", "Lebanon", "Arabic", True), "963": ("SY", "Syria", "Arabic", True), "970": ("PS", "Palestine", "Arabic", True),
    "20": ("EG", "Egypt", "Arabic", True), "249": ("SD", "Sudan", "Arabic", True), "218": ("LY", "Libya", "Arabic", True),
    "216": ("TN", "Tunisia", "Arabic", True), "213": ("DZ", "Algeria", "Arabic", True), "212": ("MA", "Morocco", "Arabic", True),
    "86": ("CN", "China", "Chinese", True), "852": ("HK", "Hong Kong", "Chinese", True), "886": ("TW", "Taiwan", "Chinese", True),
    "81": ("JP", "Japan", "Japanese", True), "82": ("KR", "South Korea", "Korean", True), "62": ("ID", "Indonesia", "Indonesian", True),
    "60": ("MY", "Malaysia", "Malay", True), "63": ("PH", "Philippines", "Tagalog", True), "66": ("TH", "Thailand", "Thai", True),
    "84": ("VN", "Vietnam", "Vietnamese", True), "90": ("TR", "Turkey", "Turkish", True), "972": ("IL", "Israel", "Hebrew", True),
    "98": ("IR", "Iran", "Persian", True), "34": ("ES", "Spain", "Spanish", True), "52": ("MX", "Mexico", "Spanish", True),
    "54": ("AR", "Argentina", "Spanish", True), "57": ("CO", "Colombia", "Spanish", True), "33": ("FR", "France", "French", True),
    "49": ("DE", "Germany", "German", True), "39": ("IT", "Italy", "Italian", True), "55": ("BR", "Brazil", "Portuguese", True),
    "351": ("PT", "Portugal", "Portuguese", True), "7": ("RU", "Russia / Kazakhstan", "Russian", True), "380": ("UA", "Ukraine", "Ukrainian", True),
    "48": ("PL", "Poland", "Polish", True), "31": ("NL", "Netherlands", "Dutch", True), "30": ("GR", "Greece", "Greek", True),
}
_PREFIXES = sorted(COUNTRIES, key=len, reverse=True)


def normalise(phone):
    p = "".join(ch for ch in str(phone or "") if ch.isdigit() or ch == "+")
    if p.startswith("00"):
        p = "+" + p[2:]
    return p


def lookup(phone):
    """-> dict(code, iso, country, language, sure) or None for numbers without a recognisable international prefix."""
    p = normalise(phone)
    if not p.startswith("+"):
        return None
    digits = p[1:]
    for pre in _PREFIXES:
        if digits.startswith(pre):
            iso, country, lang, sure = COUNTRIES[pre]
            return {"code": pre, "iso": iso, "country": country, "language": lang, "sure": sure}
    return None


def flag(iso):
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in (iso or "").upper()) if iso and len(iso) == 2 else ""


def listing():
    return [{"code": c, "iso": v[0], "country": v[1], "language": v[2], "flag": flag(v[0])} for c, v in sorted(COUNTRIES.items(), key=lambda x: x[1][1])]

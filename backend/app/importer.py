import csv
import io
import json
import re

from openpyxl import load_workbook

from . import countries, db, voices

FIELD_HINTS = {
    "name": ["name", "full name", "customer", "client", "fullname", "שם", "الاسم", "nombre", "nom"],
    "phone": ["phone", "mobile", "tel", "cell", "number", "טלפון", "هاتف", "teléfono", "telephone"],
    "email": ["email", "e-mail", "mail", "מייל", "بريد"],
    "company": ["company", "business", "organization", "organisation", "employer", "חברה", "شركة"],
    "language": ["language", "lang", "locale", "שפה", "لغة", "idioma", "langue"],
    "status": ["status"],
    "tags": ["stage", "tag", "segment", "group", "category", "tier"],
    "notes": ["note", "comment", "remark", "הערה", "ملاحظ"],
}
LANG_ALIASES = {
    "en": "English", "eng": "English", "ar": "Arabic", "he": "Hebrew", "heb": "Hebrew", "es": "Spanish", "fr": "French",
    "de": "German", "ru": "Russian", "pt": "Portuguese", "tr": "Turkish", "it": "Italian", "hi": "Hindi", "ur": "Urdu",
    "bn": "Bengali", "bangla": "Bengali", "zh": "Chinese", "mandarin": "Chinese", "cantonese": "Chinese", "ja": "Japanese",
    "ko": "Korean", "id": "Indonesian", "ms": "Malay", "tl": "Tagalog", "filipino": "Tagalog", "ta": "Tamil", "te": "Telugu",
    "th": "Thai", "vi": "Vietnamese", "fa": "Persian", "farsi": "Persian", "pa": "Punjabi",
}


def read_rows(filename, data: bytes):
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    else:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(list(r))
            if len(rows) > 50001:
                break  # cap rows so a huge or malicious file cannot exhaust memory
    rows = [r for r in rows if any(c not in (None, "") for c in r)]
    if not rows:
        return [], []
    header = [str(c).strip() if c is not None else f"col{i+1}" for i, c in enumerate(rows[0])]
    body = [{header[i]: ("" if c is None else str(c).strip()) for i, c in enumerate(r) if i < len(header)} for r in rows[1:]]
    return header, body


def guess_mapping(header):
    mapping = {}
    for field, hints in FIELD_HINTS.items():
        for col in header:
            low = col.lower()
            if col in mapping.values():
                continue
            if any(h == low for h in hints) or any(h in low for h in hints):
                mapping[field] = col
                break
    return mapping


def norm_lang(v):
    v = (v or "").strip()
    if not v:
        return "English"
    return LANG_ALIASES.get(v.lower(), v[:1].upper() + v[1:])


def norm_phone(v):
    v = (v or "").strip()
    v = re.sub(r"\.0$", "", v)
    keep = re.sub(r"[^\d+]", "", v)
    if keep.startswith("00"):
        keep = "+" + keep[2:]
    return keep


def import_rows(dataset_name, filename, header, body, mapping):
    ds_id = db.run("INSERT INTO datasets(name,filename,columns,row_count,created_at) VALUES(?,?,?,?,?)",
                   (dataset_name, filename, json.dumps(header), 0, db.now()))
    mapped_cols = set(mapping.values())
    rows, skipped = [], 0
    for r in body:
        g = lambda f: r.get(mapping[f], "") if f in mapping else ""
        name, phone = g("name"), norm_phone(g("phone"))
        if not name and not phone:
            skipped += 1
            continue
        extra = {k: v for k, v in r.items() if k not in mapped_cols and v != ""}
        sheet_lang = norm_lang(g("language")) if g("language") else ""
        lang, src = voices.choose_language(phone, sheet_lang)
        info = countries.lookup(phone)
        rows.append((ds_id, name or "Unknown", phone, g("email"), g("company"), lang,
                     (g("status") or "new").lower(), g("tags"), g("notes"), json.dumps(extra, ensure_ascii=False), db.now(),
                     info["country"] if info else "", src))
    db.many("INSERT INTO contacts(dataset_id,name,phone,email,company,language,status,tags,notes,extra,created_at,country,lang_source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.update("datasets", ds_id, row_count=len(rows))
    db.log("dataset", f"Imported {len(rows)} contacts from {filename}", dataset_id=ds_id, skipped=skipped)
    return {"dataset_id": ds_id, "imported": len(rows), "skipped": skipped}

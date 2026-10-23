"""The spreadsheet engine: profile, clean, de-duplicate, merge and query a table, at machine speed.

A table is a list of column names and a list of rows (each a list of text). Nothing here touches the database or a model, so it is fast (a 50,000-row sheet
takes a second or two), and everything it does is counted: every function reports what it changed, how many cells it left alone and why, with examples.

People are matched by phone number (last nine digits, so +44 7447 781753 and 07447 781753 are one person), then by email, then by name; never by row number."""
import datetime
import re
import unicodedata
from collections import Counter, defaultdict

MAX_ROWS = 50_000
MAX_COLS = 40
HEADER_LEN = 80
CELL_LEN = 500
EXAMPLES = 3

EMAIL_RE = re.compile(r"^[^@\s,;<>()\[\]]+@[^@\s,;<>()\[\]]+\.[A-Za-z]{2,}$")
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
YES_NO = {"yes", "no", "y", "n", "true", "false", "1", "0"}


# ---------- text ----------
def s(v):
    return "" if v is None else str(v)


def clean_text(v):
    """Whitespace tidied: no leading/trailing spaces, one space between words, no invisible characters."""
    return re.sub(r"\s+", " ", s(v).replace(" ", " ").replace("​", "").replace("﻿", "")).strip()


def digits(v):
    return re.sub(r"\D", "", s(v))


def fold(v):
    """Text for comparing: no accents, lower case, punctuation as single spaces."""
    t = unicodedata.normalize("NFKD", s(v))
    return re.sub(r"[^\w]+", " ", "".join(c for c in t if not unicodedata.combining(c)).casefold()).strip()


def key_phone(v):
    d = digits(v)
    return d[-9:] if len(d) >= 9 else ""


def key_email(v):
    t = re.sub(r"^mailto:", "", clean_text(v).lower())
    return t if EMAIL_RE.match(t) else ""


def key_name(v):
    t = clean_text(v)
    if "," in t:  # "Smith, John" is John Smith
        a, b = t.split(",", 1)
        t = f"{b} {a}"
    return " ".join(sorted(fold(t).split()))


KEYS = {"phone": key_phone, "email": key_email, "name": key_name}


def key_of(kind):
    return KEYS.get(kind, lambda v: fold(v))


# ---------- what a column is ----------
_ROLES = [
    ("first_name", ["first name", "firstname", "given name", "forename"]),
    ("last_name", ["last name", "lastname", "surname", "family name"]),
    ("email", ["email", "e mail", "mail", "email address"]),
    ("phone", ["phone", "mobile", "tel", "telephone", "cell", "whatsapp", "contact number", "mob", "gsm", "phone number", "mobile number"]),
    ("postcode", ["postcode", "post code", "zip", "zip code", "postal code"]),
    ("company", ["company", "organisation", "organization", "employer", "business", "firm"]),
    ("country", ["country", "nation"]),
    ("city", ["city", "town"]),
    ("status", ["status", "stage", "outcome"]),
    ("notes", ["notes", "note", "comment", "comments", "remarks"]),
    ("date", ["date", "created", "signed up", "dob", "birthday", "joined"]),
    ("name", ["name", "full name", "fullname", "student", "lead", "contact", "customer", "client", "person"]),
]


def header_role(header):
    """What a column header means: phone, email, name, first_name, last_name, company, country, city, postcode, status, notes, date; '' when unclear."""
    t = fold(header)
    if not t:
        return ""
    words = set(t.split())
    for role, names in _ROLES:
        if t in names:
            return role
    for role, names in _ROLES:
        if any((" " in n and n in t) or n in words for n in names):
            return role
    return ""


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _ymd(y, m, d):
    try:
        return datetime.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def _year(y):
    y = int(y)
    return y if y > 99 else (2000 + y if y <= 69 else 1900 + y)


def parse_date(v, dayfirst=True):
    """-> 'YYYY-MM-DD' or None. Understands 2026-10-12, 12/10/2026, 12.10.26, 12 Oct 2026, October 12th 2026. 12/10 is read day-first unless told otherwise."""
    t = clean_text(v)
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T,].*)?", t)
    if m:
        return _ymd(*m.groups())
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?:[ T,].*)?", t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if a > 12:
            return _ymd(y, b, a)
        if b > 12:
            return _ymd(y, a, b)
        return _ymd(y, b, a) if dayfirst else _ymd(y, a, b)
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/]+([A-Za-z]{3,9})\.?,?[\s\-/]+(\d{2,4})(?:[ T,].*)?", t)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _ymd(_year(m.group(3)), _MONTHS[m.group(2)[:3].lower()], m.group(1))
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _ymd(m.group(3), _MONTHS[m.group(1)[:3].lower()], m.group(2))
    return None


def day_first(values):
    """True unless the dates in a column clearly put the month first (12/31/2026)."""
    first = month = 0
    for v in values[:3000]:
        m = re.match(r"\s*(\d{1,2})[-/.](\d{1,2})[-/.]\d{2,4}", s(v))
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            first += a > 12
            month += b > 12
    return not (month and not first)


def parse_number(v):
    t = clean_text(v)
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = re.sub(r"(?i)[£$€¥,\s%]|gbp|usd|eur", "", t.strip("()")).replace("−", "-")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    x = float(t)
    return -x if neg else x


def fmt_number(x):
    return str(int(x)) if x == int(x) else "%.10g" % x


def norm_phone(v, code=""):
    """-> (E.164 number or '', why not). 07447 781753 with code '44' -> +447447781753. Never guesses a country: no code and a local-style number is a 'why not'."""
    raw = clean_text(v)
    if not raw:
        return "", ""
    raw = re.sub(r"(?i)\s*(?:ext\.?|extension|x)\s*\d+$", "", raw)
    if re.search(r"[A-Za-z]{2,}", raw):
        return "", "it has letters in it"
    plus = raw.startswith("+")
    n = digits(re.sub(r"\(0\)", "", raw))
    if not n:
        return "", "it has no digits"
    code = digits(code)
    if plus:
        full = n
        if code and n.startswith(code + "0"):
            full = code + n[len(code) + 1:]  # +44 07447...: the trunk 0 is not part of the number
    elif raw.startswith("00"):
        full = n[2:]
    elif n.startswith("0"):
        if not code:
            return "", "it starts with 0 and no country code is set (Settings → Company → time zone / calling code)"
        if len(n) - 1 < 7:
            return "", "too short to be a phone number"      # 01234 must not become +441234
        full = code + n[1:]
    elif code and n.startswith(code) and len(n) >= 11:
        full = n
    elif code:
        if len(n) < 7:
            return "", "too short to be a phone number"      # 12345 must not become +4412345, which looks real and would be dialed
        full = code + n
    else:
        full = n
    out = "+" + full
    if not E164_RE.match(out):
        return "", f"{'too short' if len(full) < 7 else 'too long' if len(full) > 15 else 'not a valid number'}"
    if out.startswith("+447") and len(out) != 13:
        return "", f"a UK mobile needs 10 digits after +44 and this has {len(out) - 3}"
    return out, ""


def is_phone_like(v):
    t = clean_text(v)
    return bool(re.fullmatch(r"\+?[\d\s().\-/]{6,}", t)) and 7 <= len(digits(t)) <= 15


_PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "du", "la", "le", "bin", "ibn", "al", "el", "del", "della", "dos", "das", "y", "e", "of"}


def title_name(v):
    """JOHN smith / john smith -> John Smith, with O'Neil, Mary-Ann, McDonald, van der Berg. A word already in mixed case (McDonald, DiCaprio) is left alone."""
    t = clean_text(v)
    if not t:
        return t

    def cap(word, first):
        if not first and word.lower() in _PARTICLES:
            return word.lower()
        out = "".join(p if p in ("'", "\u2019", "-") else p.capitalize() for p in re.split(r"(['\u2019\-])", word.lower()))
        return re.sub(r"^(Mc)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    def fine(word):  # O'Neil, Anne-Marie, McDonald, DiCaprio, III: every part starts with a capital and is not all capitals
        if re.fullmatch(r"[IVX]+", word):
            return True
        return all(p == "" or (p[0].isupper() and not (len(p) > 1 and p.isupper())) for p in re.split(r"['\u2019\-]", word))
    return " ".join(w if fine(w) else cap(w, i == 0) for i, w in enumerate(t.split(" ")))


def infer(header, values):
    """-> (type, role) for a column from its header and what is in it. Types: email, phone, date, url, number, yes_no, name, category, text, empty."""
    role = header_role(header)
    vals = [clean_text(v) for v in values if clean_text(v)][:3000]
    n = len(vals)
    if not n:
        return "empty", role
    share = lambda f: sum(1 for v in vals if f(v)) / n  # noqa: E731
    if share(lambda v: bool(key_email(v))) >= 0.7 or (role == "email" and share(lambda v: "@" in v) >= 0.3):
        return "email", role or "email"
    if role != "phone" and share(lambda v: parse_date(v) is not None) >= 0.7:
        return "date", role or "date"
    like = share(is_phone_like)
    formatted = share(lambda v: v[:1] in "+0" or bool(re.search(r"[\s().\-]", v)))
    if (role == "phone" and like >= 0.3) or (like >= 0.9 and formatted >= 0.5):
        return "phone", "phone"
    if share(lambda v: v.lower().startswith(("http://", "https://", "www."))) >= 0.7:
        return "url", role
    if share(lambda v: parse_number(v) is not None) >= 0.85:
        return "number", role
    if {v.lower() for v in vals} <= YES_NO:
        return "yes_no", role
    if role in ("name", "first_name", "last_name"):
        return "name", role
    distinct = len({fold(v) for v in vals})
    if distinct <= 12 or (distinct <= 20 and distinct / n <= 0.5):
        return "category", role
    return "text", role


def col_types(cols, rows):
    """[(type, role)] for every column."""
    return [infer(h, [r[i] if i < len(r) else "" for r in rows[:4000]]) for i, h in enumerate(cols)]


def match_kind(type_, role):
    """How people in this column are matched: phone, email, name, or plain text."""
    if type_ == "phone" or role == "phone":
        return "phone"
    if type_ == "email" or role == "email":
        return "email"
    if role == "name":
        return "name"
    return "text"


# ---------- tables ----------
def pad(cols, rows):
    n = len(cols)
    return [(r + [""] * (n - len(r)))[:n] if len(r) != n else r for r in rows]


def resolve_col(cols, ref, types=None):
    """The index of a column by its header, 1-based number, or role ('phone'). ValueError, listing the columns, when it cannot be told."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit() and fold(ref) not in [fold(c) for c in cols]):
        i = int(ref) - 1
        if 0 <= i < len(cols):
            return i
    t = fold(ref)
    if not t:
        raise ValueError(f"Say which column. The columns are: {', '.join(map(str, cols))}.")
    for i, c in enumerate(cols):
        if fold(c) == t:
            return i
    near = [i for i, c in enumerate(cols) if t and (fold(c).startswith(t) or t in fold(c).split())]
    if len(near) == 1:
        return near[0]
    if types:
        by_role = [i for i, (ty, ro) in enumerate(types) if t in (ty, ro)]
        if by_role:
            return by_role[0]
    raise ValueError(f"There is no column '{ref}'. The columns are: {', '.join(map(str, cols))}.")


def _dedupe_headers(cols):
    seen, out = Counter(), []
    for i, c in enumerate(cols):
        c = clean_text(c)[:HEADER_LEN] or f"Column {i + 1}"
        seen[c.casefold()] += 1
        out.append(c if seen[c.casefold()] == 1 else f"{c} ({seen[c.casefold()]})")
    return out


# ---------- duplicates ----------
class _Groups:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def duplicate_groups(rows, key_cols, max_group=10):
    """-> (groups, shared). groups: lists of row indexes that are the same person (they share a phone, an email or a name: any one of them is enough, and a chain
    counts). shared: values that tie together more than max_group rows (a switchboard number, a shared inbox): those are NOT merged. key_cols: [(index, kind)]."""
    g = _Groups(len(rows))
    via = defaultdict(set)
    for ci, kind in key_cols:
        fn, seen = key_of(kind), {}
        for i, row in enumerate(rows):
            k = fn(row[ci]) if ci < len(row) else ""
            if not k:
                continue
            if k in seen:
                g.union(i, seen[k])
                via[k].add(seen[k])
                via[k].add(i)
            else:
                seen[k] = i
    shared = [(k, len(v)) for k, v in via.items() if len(v) > max_group]
    comps = defaultdict(list)
    for i in range(len(rows)):
        comps[g.find(i)].append(i)
    ok, big = [], []
    for members in comps.values():
        if len(members) < 2:
            continue
        (big if len(members) > max_group else ok).append(members)
    return ok, shared, big


def auto_keys(cols, rows, types=None):
    types = types or col_types(cols, rows)
    return [(i, match_kind(*types[i])) for i in range(len(cols)) if match_kind(*types[i]) in ("phone", "email")]


def differ(a, b, kind="text"):
    """Do two non-empty cells say different things? Phones compare by their last nine digits, emails by lower case, anything else by its words."""
    ka, kb = key_of(kind)(a), key_of(kind)(b)
    return ka != kb if (ka and kb) else fold(a) != fold(b)


def _filled(row):
    return sum(1 for c in row if clean_text(c))


def dedupe(cols, rows, rid, keys, keep="most_complete", max_group=10):
    """Merge rows that are the same person. keep: first | last | most_complete (the fullest row survives, and fills its blanks from the others).
    -> (rows, rid, info). keys: [(index, kind)]."""
    groups, shared, big = duplicate_groups(rows, keys, max_group)
    types = col_types(cols, rows[:2000])
    kinds = [match_kind(*t) for t in types]
    drop, filled, conflicts, examples, cex = set(), 0, 0, [], []
    for members in groups:
        order = sorted(members)
        keeper = order[0] if keep == "first" else order[-1] if keep == "last" else max(order, key=lambda i: (_filled(rows[i]), -i))
        first = order[0]
        for other in (i for i in order if i != keeper):
            for c in range(len(cols)):
                a, b = clean_text(rows[keeper][c]), clean_text(rows[other][c])
                if not a and b:
                    rows[keeper][c] = rows[other][c]
                    filled += 1
                elif a and b and differ(a, b, kinds[c]):
                    conflicts += 1
                    if len(cex) < 8:
                        cex.append({"row": rid[keeper], "column": cols[c], "kept": a[:50], "other": b[:50]})
            drop.add(other)
        if keeper != first:  # the person stays where they first appeared in the sheet
            rows[first] = rows[keeper]
            drop.discard(first)
            drop.add(keeper)
        if len(examples) < EXAMPLES:
            examples.append({"kept_row": rid[first], "merged_rows": [rid[i] for i in order if i != first], "person": " / ".join(clean_text(c) for c in rows[first][:2] if clean_text(c))[:60]})
    info = {"groups": len(groups), "rows_removed": len(drop), "cells_filled_from_duplicates": filled, "disagreements_kept_as_first_choice": conflicts, "disagreement_examples": cex, "examples": examples,
            "not_merged_shared_values": [{"value": k, "rows": n} for k, n in sorted(shared, key=lambda t: -t[1])[:5]], "not_merged_groups_too_large": len(big)}
    keep_idx = [i for i in range(len(rows)) if i not in drop]
    return [rows[i] for i in keep_idx], [rid[i] for i in keep_idx], info


def sort_key(kind):
    """How to order the cells of a column of this type: numbers as numbers, dates as dates, anything else by its words. Blanks sort last."""
    if kind == "number":
        return lambda v: (parse_number(v) is None, parse_number(v) or 0)
    if kind == "date":
        return lambda v: (parse_date(v) is None, parse_date(v) or "")
    return lambda v: (not clean_text(v), fold(v))


# ---------- conditions (queries and row filters) ----------
def _cmp_value(v):
    n = parse_number(v)
    if n is not None:
        return (0, n)
    d = parse_date(v)
    return (1, d) if d else (2, fold(v))


def condition(cols, cond, types=None):
    """A function row -> bool. cond: {"column", "op", "value"} with op in equals, not_equals, contains, not_contains, starts_with, ends_with, empty, not_empty,
    gt, gte, lt, lte, in; or {"any": [cond...]} (any of these); or {"not": cond}."""
    if "any" in cond:
        fns = [condition(cols, c, types) for c in cond["any"]]
        return lambda row: any(f(row) for f in fns)
    if "not" in cond:
        f = condition(cols, cond["not"], types)
        return lambda row: not f(row)
    ci = resolve_col(cols, cond.get("column", ""), types)
    op, want = str(cond.get("op") or "equals").lower(), cond.get("value", "")
    if op in ("empty", "blank"):
        return lambda row: not clean_text(row[ci])
    if op in ("not_empty", "filled"):
        return lambda row: bool(clean_text(row[ci]))
    if op == "in":
        wants = {fold(w) for w in (want if isinstance(want, list) else [want])}
        return lambda row: fold(row[ci]) in wants
    w = fold(want)
    kind = match_kind(*types[ci]) if types else "text"
    if op in ("equals", "not_equals", "eq", "ne"):
        k = key_of(kind)
        wk = k(want) or w
        f = lambda row: (k(row[ci]) or fold(row[ci])) == wk  # noqa: E731
        return f if op in ("equals", "eq") else (lambda row: not f(row))
    if op == "contains":
        return lambda row: w in fold(row[ci])
    if op == "not_contains":
        return lambda row: w not in fold(row[ci])
    if op == "starts_with":
        return lambda row: fold(row[ci]).startswith(w)
    if op == "ends_with":
        return lambda row: fold(row[ci]).endswith(w)
    if op in ("gt", "gte", "lt", "lte", ">", ">=", "<", "<="):
        target = _cmp_value(want)

        def f(row):
            v = _cmp_value(row[ci])
            if not clean_text(row[ci]) or v[0] != target[0]:
                return False
            return {"gt": v > target, ">": v > target, "gte": v >= target, ">=": v >= target, "lt": v < target, "<": v < target, "lte": v <= target, "<=": v <= target}[op]
        return f
    raise ValueError(f"Unknown condition '{op}'. Use equals, not_equals, contains, not_contains, starts_with, ends_with, empty, not_empty, gt, gte, lt, lte or in.")


def select_rows(cols, rows, where, types=None):
    """Indexes of the rows that meet every condition in `where` (a condition or a list of them)."""
    if not where:
        return list(range(len(rows)))
    fns = [condition(cols, c, types) for c in (where if isinstance(where, list) else [where])]
    return [i for i, row in enumerate(rows) if all(f(row) for f in fns)]


# ---------- cleaning ----------
class Cleaner:
    """Applies a list of clean-up operations in order and counts everything it does."""

    def __init__(self, cols, rows, code="", flag_column="Needs checking"):
        self.cols = _dedupe_headers(cols)
        self.rows = [list(r) for r in pad(cols, rows)]
        self.rid = list(range(1, len(self.rows) + 1))   # the row number each row had in the original, for the notes
        self.code = digits(code)
        self.flag_column = flag_column
        self.flags = defaultdict(list)                   # original row number -> what to check
        self.report = []
        self._types = None

    @property
    def types(self):
        if self._types is None:
            self._types = col_types(self.cols, self.rows)
        return self._types

    def _targets(self, op, kinds):
        if op.get("column") in (None, "", "*"):
            if kinds is None:
                return list(range(len(self.cols)))
            return [i for i, (ty, ro) in enumerate(self.types) if ty in kinds or ro in kinds]
        refs = op["column"] if isinstance(op["column"], list) else [op["column"]]
        return [resolve_col(self.cols, r, self.types) for r in refs]

    def _note(self, name, column, changed=0, flagged=0, removed=0, examples=None, note=""):
        entry = {"op": name, "column": column, "changed": changed}
        if flagged:
            entry["flagged_for_checking"] = flagged
        if removed:
            entry["rows_removed"] = removed
        if examples:
            entry["examples"] = examples[:EXAMPLES]
        if note:
            entry["note"] = note
        self.report.append(entry)

    def _map_column(self, name, ci, fn, flag=None):
        """fn(text) -> (new text, why not or ''). A cell with a reason keeps its text and is flagged."""
        changed, flagged, ex = 0, 0, []
        for r, row in zip(self.rid, self.rows):
            old = s(row[ci])
            if not clean_text(old):
                continue
            new, why = fn(old)
            if why:
                flagged += 1
                self.flags[r].append(f"{self.cols[ci]}: {why}"[:120])
                continue
            if new != old:
                row[ci] = new[:CELL_LEN]
                changed += 1
                if len(ex) < EXAMPLES:
                    ex.append([old[:60], new[:60]])
        self._note(name, self.cols[ci], changed, flagged, examples=ex)

    def run(self, ops):
        for op in ops[:30]:
            if not isinstance(op, dict):
                raise ValueError("Each operation must be an object such as {\"op\": \"trim\"}.")
            name = str(op.get("op") or op.get("operation") or "").lower().replace("-", "_")
            fn = getattr(self, f"op_{name}", None)
            if fn is None:
                raise ValueError(f"Unknown operation '{name}'. Use: trim, phone, email, date, number, case, replace, map, fill_blank, split, join, rename, drop_columns, keep_columns, "
                                 "add_column, drop_rows, remove_empty_rows, remove_empty_columns, dedupe, sort.")
            fn(op)
            if name in ("rename", "drop_columns", "keep_columns", "split", "join", "add_column", "remove_empty_columns"):
                self._types = None
        return self

    def result(self):
        cols, rows = list(self.cols), self.rows
        flagged_rows = sum(1 for r in self.rid if r in self.flags)
        if self.flag_column and flagged_rows:
            cols = cols + [self.flag_column]
            rows = [row + ["; ".join(dict.fromkeys(self.flags.get(r, [])))[:CELL_LEN]] for r, row in zip(self.rid, rows)]
        return cols, rows, {"operations": self.report, "rows_after": len(rows), "columns_after": len(cols), "rows_flagged_for_checking": flagged_rows,
                            "flag_column": self.flag_column if (self.flag_column and flagged_rows) else ""}

    # --- operations ---
    def op_trim(self, op):
        before = len(self.report)
        for ci in self._targets(op, None):
            self._map_column("trim", ci, lambda v: (clean_text(v), ""))
        self.report[before:] = [e for e in self.report[before:] if e["changed"]] or [{"op": "trim", "column": "all", "changed": 0, "note": "nothing needed tidying"}]

    def op_phone(self, op):
        code = digits(op.get("country_code")) or self.code
        for ci in self._targets(op, {"phone"}):
            self._map_column("phone", ci, lambda v: norm_phone(v, code))

    def op_email(self, op):
        def fn(v):
            t = re.sub(r"^mailto:", "", clean_text(v).lower())
            return (t, "") if EMAIL_RE.match(t) else (v, "not a valid email address")
        for ci in self._targets(op, {"email"}):
            self._map_column("email", ci, fn)

    def op_date(self, op):
        for ci in self._targets(op, {"date"}):
            dayfirst = op.get("day_first")
            if dayfirst is None:
                dayfirst = day_first([row[ci] for row in self.rows])
            self._map_column("date", ci, lambda v, d=dayfirst: ((p, "") if (p := parse_date(v, d)) else (v, "not a date I can read")))

    def op_number(self, op):
        def fn(v):
            x = parse_number(v)
            return (fmt_number(x), "") if x is not None else (v, "not a number")
        for ci in self._targets(op, {"number"}):
            self._map_column("number", ci, fn)

    def op_case(self, op):
        mode = str(op.get("mode") or "title").lower()
        fns = {"title": title_name, "lower": lambda v: clean_text(v).lower(), "upper": lambda v: clean_text(v).upper(),
               "sentence": lambda v: (lambda t: t[:1].upper() + t[1:].lower())(clean_text(v))}
        if mode not in fns:
            raise ValueError("case mode must be title, lower, upper or sentence.")
        for ci in self._targets(op, {"name", "first_name", "last_name"}):
            self._map_column(f"case ({mode})", ci, lambda v, f=fns[mode]: (f(v), ""))

    def op_replace(self, op):
        find, new = s(op.get("find")), s(op.get("with", op.get("replace_with", "")))
        if not find:
            raise ValueError("replace needs `find`.")
        pat = re.compile(re.escape(find), 0 if op.get("match_case") else re.I)
        for ci in self._targets(op, None):
            self._map_column("replace", ci, lambda v: (pat.sub(new.replace("\\", "\\\\"), v), ""))

    def op_map(self, op):
        mapping = {fold(k): v for k, v in (op.get("mapping") or {}).items()}
        if not mapping:
            raise ValueError("map needs `mapping`, for example {\"UK\": \"United Kingdom\"}.")
        for ci in self._targets(op, None):
            self._map_column("map", ci, lambda v: (s(mapping.get(fold(v), v)), ""))

    def op_fill_blank(self, op):
        ci = resolve_col(self.cols, op.get("column"), self.types)
        src = resolve_col(self.cols, op["from_column"], self.types) if op.get("from_column") else None
        value, changed, ex = s(op.get("value", "")), 0, []
        if src is None and not value:
            raise ValueError("fill_blank needs `value` or `from_column`.")
        for row in self.rows:
            if not clean_text(row[ci]):
                new = s(row[src]) if src is not None else value
                if clean_text(new):
                    row[ci] = new[:CELL_LEN]
                    changed += 1
                    if len(ex) < EXAMPLES:
                        ex.append(["", new[:60]])
        self._note("fill_blank", self.cols[ci], changed, examples=ex)

    def op_split(self, op):
        ci = resolve_col(self.cols, op.get("column"), self.types)
        into = [clean_text(x) for x in (op.get("into") or ["First name", "Last name"])]
        delim = op.get("delimiter")
        changed, ex = 0, []
        base = len(self.cols)
        self.cols += [c for c in into]
        self.cols = _dedupe_headers(self.cols)
        for row in self.rows:
            text = clean_text(row[ci])
            words = text.split(" ") if text else []
            if delim:
                parts = [p.strip() for p in text.split(delim, len(into) - 1)] if text else []
            elif len(into) == 2:  # a full name: everything before the last word, and the last word
                parts = [text, ""] if len(words) == 1 else [" ".join(words[:-1]), words[-1]] if words else []
            else:
                parts = words[:len(into) - 1] + [" ".join(words[len(into) - 1:])] if words else []
            parts = (parts + [""] * len(into))[:len(into)]
            row.extend(parts)
            changed += 1 if text else 0
            if text and len(ex) < EXAMPLES:
                ex.append([text[:50], " | ".join(parts)[:60]])
        self._note("split", self.cols[ci], changed, examples=ex, note=f"new columns: {', '.join(self.cols[base:])}")
        if op.get("drop_original"):
            self.op_drop_columns({"columns": [self.cols[ci]]})

    def op_join(self, op):
        idx = [resolve_col(self.cols, c, self.types) for c in (op.get("columns") or [])]
        if len(idx) < 2:
            raise ValueError("join needs two or more `columns`.")
        into = clean_text(op.get("into")) or " ".join(self.cols[i] for i in idx)
        sep, ex = s(op.get("separator", " ")), []
        self.cols = _dedupe_headers(self.cols + [into])
        for row in self.rows:
            row.append(sep.join(clean_text(row[i]) for i in idx if clean_text(row[i]))[:CELL_LEN])
            if len(ex) < EXAMPLES and row[-1]:
                ex.append([" + ".join(clean_text(row[i]) for i in idx)[:60], row[-1][:60]])
        self._note("join", into, len(self.rows), examples=ex)

    def op_rename(self, op):
        mapping = op.get("mapping") or {}
        if not mapping:
            raise ValueError("rename needs `mapping`, for example {\"Mobile\": \"Phone\"}.")
        for old, new in mapping.items():
            ci = resolve_col(self.cols, old, self.types)
            self._note("rename", self.cols[ci], 0, note=f"now '{clean_text(new)[:HEADER_LEN]}'")
            self.cols[ci] = clean_text(new)[:HEADER_LEN]
        self.cols = _dedupe_headers(self.cols)

    def op_drop_columns(self, op):
        drop = {resolve_col(self.cols, c, self.types) for c in (op.get("columns") or [])}
        keep = [i for i in range(len(self.cols)) if i not in drop]
        self._note("drop_columns", ", ".join(self.cols[i] for i in sorted(drop)), 0, note=f"{len(drop)} column(s) removed")
        self.cols = [self.cols[i] for i in keep]
        self.rows = [[row[i] for i in keep] for row in self.rows]

    def op_keep_columns(self, op):
        order = [resolve_col(self.cols, c, self.types) for c in (op.get("columns") or [])]
        if not order:
            raise ValueError("keep_columns needs `columns` (they are also put in this order).")
        rest = [i for i in range(len(self.cols)) if i not in order] if op.get("others", "drop") == "end" else []
        order += rest
        self._note("keep_columns", ", ".join(self.cols[i] for i in order), 0, note=f"{len(self.cols) - len(order)} column(s) dropped")
        self.cols = [self.cols[i] for i in order]
        self.rows = [[row[i] for i in order] for row in self.rows]

    def op_add_column(self, op):
        name = clean_text(op.get("name"))
        if not name:
            raise ValueError("add_column needs `name`.")
        src = resolve_col(self.cols, op["from_column"], self.types) if op.get("from_column") else None
        self.cols = _dedupe_headers(self.cols + [name])
        for row in self.rows:
            row.append((s(row[src]) if src is not None else s(op.get("value", "")))[:CELL_LEN])
        self._note("add_column", name, len(self.rows))

    def op_drop_rows(self, op):
        where = op.get("where")
        if not where:
            raise ValueError("drop_rows needs `where`, for example [{\"column\": \"Status\", \"op\": \"equals\", \"value\": \"do not call\"}].")
        gone = set(select_rows(self.cols, self.rows, where, self.types))
        self._remove(gone, "drop_rows")

    def op_remove_empty_rows(self, op):
        self._remove({i for i, row in enumerate(self.rows) if not any(clean_text(c) for c in row)}, "remove_empty_rows")

    def op_remove_empty_columns(self, op):
        keep = [i for i in range(len(self.cols)) if any(clean_text(row[i]) for row in self.rows)]
        gone = [self.cols[i] for i in range(len(self.cols)) if i not in keep]
        self._note("remove_empty_columns", ", ".join(gone), 0, note=f"{len(gone)} empty column(s) removed")
        self.cols = [self.cols[i] for i in keep]
        self.rows = [[row[i] for i in keep] for row in self.rows]

    def _remove(self, gone, name):
        ex = [e for e in (" / ".join(clean_text(c) for c in self.rows[i][:2] if clean_text(c))[:50] for i in sorted(gone)[:EXAMPLES]) if e]
        keep = [i for i in range(len(self.rows)) if i not in gone]
        self.rows, self.rid = [self.rows[i] for i in keep], [self.rid[i] for i in keep]
        self._note(name, "", 0, removed=len(gone), examples=ex)

    def op_dedupe(self, op):
        refs = op.get("keys") or op.get("columns")
        if refs:
            keys = [(resolve_col(self.cols, r, self.types), match_kind(*self.types[resolve_col(self.cols, r, self.types)])) for r in (refs if isinstance(refs, list) else [refs])]
        else:
            keys = auto_keys(self.cols, self.rows, self.types)
        if not keys:
            raise ValueError("I found no phone or email column to recognise the same person by. Say which columns identify a person: {\"op\": \"dedupe\", \"keys\": [\"Name\"]}.")
        before = len(self.rows)
        self.rows, self.rid, info = dedupe(self.cols, self.rows, self.rid, keys, str(op.get("keep") or "most_complete"), int(op.get("max_group") or 10))
        self._note("dedupe", ", ".join(self.cols[i] for i, _k in keys), 0, removed=before - len(self.rows), examples=info["examples"],
                   note=f"{info['groups']} person(s) appeared more than once; {info['cells_filled_from_duplicates']} blank cell(s) filled from their duplicates"
                        + (f"; {info['disagreements_kept_as_first_choice']} cell(s) disagreed and the kept row's value stayed" if info["disagreements_kept_as_first_choice"] else "")
                        + (f"; NOT merged, shared by many rows: {info['not_merged_shared_values']}" if info["not_merged_shared_values"] else ""))

    def op_sort(self, op):
        ci = resolve_col(self.cols, op.get("column"), self.types)
        as_ = str(op.get("as") or "").lower() or self.types[ci][0]
        key = sort_key(as_)
        order = sorted(range(len(self.rows)), key=lambda i: key(self.rows[i][ci]), reverse=bool(op.get("descending")))
        if op.get("descending"):  # blanks last either way
            order = [i for i in order if clean_text(self.rows[i][ci])] + [i for i in order if not clean_text(self.rows[i][ci])]
        self.rows, self.rid = [self.rows[i] for i in order], [self.rid[i] for i in order]
        self._note("sort", self.cols[ci], 0, note=f"by {as_ if as_ in ('number', 'date') else 'text'}{', largest first' if op.get('descending') else ''}")


# ---------- profile ----------
def profile(cols, rows, code="", max_group=10):
    """Everything worth knowing about a sheet before touching it: what each column is, what is wrong in it, who appears twice, and the clean-up that fixes it."""
    cols = list(cols)
    rows = pad(cols, rows)
    n = len(rows)
    types = col_types(cols, rows)
    out, suggestions, header_issues = [], [], []
    if len({fold(c) for c in cols}) < len(cols):
        header_issues.append("two columns have the same header (the second is renamed with (2) when you clean)")
    if any(not clean_text(c) for c in cols):
        header_issues.append("a column has no header")
    whitespace_total = 0
    for ci, h in enumerate(cols):
        ty, role = types[ci]
        raw = [row[ci] for row in rows]
        filled = [v for v in raw if clean_text(v)]
        info = {"column": h, "type": ty, "role": role, "filled": len(filled), "blank": n - len(filled), "unique": len({fold(v) for v in filled}), "problems": []}
        ws = sum(1 for v in filled if v != v.strip() or "  " in v or " " in v or "\n" in v or "\t" in v)
        if ws:
            info["problems"].append(f"{ws} cell(s) have extra spaces or hidden characters")
            whitespace_total += ws
        if ty == "phone":
            bad = [(v, norm_phone(v, code)) for v in filled]
            invalid = [v for v, (p, why) in bad if why]
            local = sum(1 for v, (p, why) in bad if not why and p != clean_text(v))
            if local:
                info["problems"].append(f"{local} number(s) are not written in the international +CC form")
                suggestions.append({"op": "phone", "column": h, "why": "write every number as +countrycode…"})
            if invalid:
                info["problems"].append(f"{len(invalid)} number(s) cannot be made valid, for example {', '.join(map(str, invalid[:3]))}")
        elif ty == "email":
            invalid = [v for v in filled if not key_email(v)]
            upper = sum(1 for v in filled if v != v.lower() and key_email(v))
            if invalid:
                info["problems"].append(f"{len(invalid)} are not valid email addresses, for example {', '.join(map(str, invalid[:3]))}")
            if upper or invalid or ws:
                suggestions.append({"op": "email", "column": h, "why": "lower-case, tidy and check every address"})
        elif ty == "date":
            df = day_first(raw)
            parsed = [parse_date(v, df) for v in filled]
            bad = sum(1 for p in parsed if p is None)
            non_iso = sum(1 for v, p in zip(filled, parsed) if p and p != clean_text(v)[:10])
            if non_iso:
                info["problems"].append(f"{non_iso} date(s) are not in YYYY-MM-DD form (read {'day first' if df else 'month first'})")
                suggestions.append({"op": "date", "column": h, "why": "one date format everywhere"})
            if bad:
                info["problems"].append(f"{bad} value(s) are not dates")
        elif ty == "number":
            odd = sum(1 for v in filled if parse_number(v) is not None and clean_text(v) != fmt_number(parse_number(v)))
            if odd:
                info["problems"].append(f"{odd} number(s) carry currency signs, commas or spaces")
                suggestions.append({"op": "number", "column": h, "why": "plain numbers"})
        elif ty == "name" or role in ("name", "first_name", "last_name"):
            shouty = sum(1 for v in filled if v == v.upper() and v != v.lower())
            lower = sum(1 for v in filled if v == v.lower() and v != v.upper())
            if shouty or lower:
                info["problems"].append(f"{shouty + lower} name(s) are all capitals or all lower case")
                suggestions.append({"op": "case", "column": h, "mode": "title", "why": "proper capitals"})
        if ty in ("category", "yes_no"):
            info["top_values"] = [[v, c] for v, c in Counter(clean_text(v) for v in filled).most_common(8)]
        else:
            info["examples"] = [clean_text(v)[:50] for v in filled[:3]]
        if n and info["blank"] == n:
            info["problems"].append("the whole column is empty")
        elif n and info["blank"] / n > 0.5:
            info["problems"].append(f"{round(100 * info['blank'] / n)}% of it is blank")
        out.append(info)
    if whitespace_total:
        suggestions.insert(0, {"op": "trim", "why": f"tidy {whitespace_total} cell(s) with extra spaces"})
    empty_rows = sum(1 for row in rows if not any(clean_text(c) for c in row))
    if empty_rows:
        suggestions.append({"op": "remove_empty_rows", "why": f"{empty_rows} row(s) are completely empty"})
    if any(i["filled"] == 0 for i in out):
        suggestions.append({"op": "remove_empty_columns", "why": "some columns hold nothing"})
    keys = [(i, match_kind(*types[i])) for i in range(len(cols)) if match_kind(*types[i]) in ("phone", "email")]
    dup = {"found_by": [cols[i] for i, _k in keys], "people_appearing_more_than_once": 0, "extra_rows": 0}
    if keys and n:
        groups, shared, big = duplicate_groups(rows, keys, max_group)
        dup["people_appearing_more_than_once"] = len(groups)
        dup["extra_rows"] = sum(len(g) - 1 for g in groups)
        if shared:
            dup["shared_values_not_treated_as_one_person"] = [{"value": k, "rows": c} for k, c in sorted(shared, key=lambda t: -t[1])[:3]]
        if groups:
            suggestions.append({"op": "dedupe", "keys": [cols[i] for i, _k in keys], "keep": "most_complete", "why": f"{dup['extra_rows']} extra row(s) are the same people"})
    exact = n - len({tuple(clean_text(c).casefold() for c in row) for row in rows}) if n else 0
    return {"rows": n, "columns": out, "header_problems": header_issues, "duplicates": dup, "identical_rows": exact, "suggested_operations": suggestions,
            "people_are_matched_by": [cols[i] for i, _k in keys] or "no phone or email column found: say which columns identify a person"}


# ---------- merge ----------
def map_columns(lcols, ltypes, rcols, rtypes, explicit=None):
    """Which column of the right sheet is the same thing as which column of the left: by the names you give, by identical header, then by what they are (phone, email...).
    -> {right index: left index}."""
    pairs, used_l = {}, set()
    for rname, lname in (explicit or {}).items():
        r, l = resolve_col(rcols, rname, rtypes), resolve_col(lcols, lname, ltypes)
        pairs[r] = l
        used_l.add(l)
    lf = {fold(c): i for i, c in enumerate(lcols)}
    for r, c in enumerate(rcols):
        l = lf.get(fold(c))
        if r not in pairs and l is not None and l not in used_l:
            pairs[r] = l
            used_l.add(l)
    for r, (ty, role) in enumerate(rtypes):
        if r in pairs:
            continue
        what = role or (ty if ty in ("phone", "email") else "")
        if not what:
            continue
        for l, (lty, lrole) in enumerate(ltypes):
            if l not in used_l and (lrole or (lty if lty in ("phone", "email") else "")) == what:
                pairs[r] = l
                used_l.add(l)
                break
    return pairs


def merge(lcols, lrows, rcols, rrows, lname="A", rname="B", mode="auto", how="outer", on=None, columns_map=None, conflict="prefer_left", source_column="Source", dedupe_result=True,
          max_group=10):
    """Combine two sheets. mode: join (match rows on keys, like a lookup), stack (put one under the other, then merge the same people) or auto.
    how (join): left | inner | outer | right. conflict (both have a different value): prefer_left | prefer_right | keep_both. -> (cols, rows, report)."""
    lcols, rcols = _dedupe_headers(lcols), _dedupe_headers(rcols)
    lrows, rrows = [list(r) for r in pad(lcols, lrows)], [list(r) for r in pad(rcols, rrows)]
    ltypes, rtypes = col_types(lcols, lrows), col_types(rcols, rrows)
    pairs = map_columns(lcols, ltypes, rcols, rtypes, columns_map)
    right_only = [r for r in range(len(rcols)) if r not in pairs]
    if mode == "auto":
        mode = "stack" if len(pairs) >= 0.6 * min(len(lcols), len(rcols)) and not on else "join"
    # --- keys
    keys = []
    for item in (on or []):
        if isinstance(item, str):
            item = {"left": item, "right": item}
        l = resolve_col(lcols, item.get("left") or item.get("column"), ltypes)
        r = resolve_col(rcols, item.get("right") or item.get("left") or item.get("column"), rtypes)
        keys.append((l, r, match_kind(*ltypes[l]) if item.get("match") in (None, "") else str(item["match"])))
    if not keys:
        for r, l in pairs.items():
            kind = match_kind(*ltypes[l])
            if kind in ("phone", "email"):
                keys.append((l, r, kind))
        keys.sort(key=lambda k: 0 if k[2] == "phone" else 1)
        if not keys:
            for r, l in pairs.items():
                if match_kind(*ltypes[l]) == "name" and header_role(lcols[l]) == "name":
                    keys.append((l, r, "name"))
    if not keys:
        raise ValueError("I cannot tell which columns identify the same person in both sheets (no shared phone, email or name column). "
                         f"{lname} has: {', '.join(lcols)}. {rname} has: {', '.join(rcols)}. Say which ones to match on, for example on=[{{\"left\": \"Phone\", \"right\": \"Mobile\"}}].")
    out_cols = list(lcols) + [rcols[r] for r in right_only]
    out_cols = _dedupe_headers(out_cols)
    nl = len(lcols)
    r_to_out = {r: pairs[r] for r in pairs}
    r_to_out.update({r: nl + k for k, r in enumerate(right_only)})
    both_conflicts, ex = [0], []

    def combine(lrow, rrow):
        row = list(lrow) + [""] * len(right_only) if lrow is not None else [""] * len(out_cols)
        if rrow is None:
            return row
        for r, o in r_to_out.items():
            rv = rrow[r]
            if lrow is None or o >= nl:
                if clean_text(rv):
                    row[o] = rv
                continue
            lv = row[o]
            if not clean_text(lv):
                row[o] = rv
            elif clean_text(rv) and differ(lv, rv, match_kind(*ltypes[o])):
                both_conflicts[0] += 1
                if len(ex) < 8:
                    ex.append({"column": out_cols[o], "left": clean_text(lv)[:50], "right": clean_text(rv)[:50], "kept": "right" if conflict == "prefer_right" else "left"})
                if conflict == "prefer_right":
                    row[o] = rv
        return row
    stats = {"matched": 0, "only_left": 0, "only_right": 0}
    rows_out, source = [], []
    if mode == "join":
        index = [defaultdict(list) for _ in keys]
        for ri, rrow in enumerate(rrows):
            for p, (l, r, kind) in enumerate(keys):
                k = key_of(kind)(rrow[r])
                if k:
                    index[p][k].append(ri)
        used, multi = set(), 0
        for lrow in lrows:
            cand = []
            for p, (l, r, kind) in enumerate(keys):
                k = key_of(kind)(lrow[l])
                if k and index[p].get(k):
                    cand = index[p][k]
                    break
            if cand:
                if len(cand) > 1:
                    multi += 1
                used.update(cand)
                row = combine(lrow, rrows[cand[0]])
                for extra in cand[1:]:  # more than one row on the right for this person: fill what is still blank
                    for r, o in r_to_out.items():
                        if not clean_text(row[o]) and clean_text(rrows[extra][r]):
                            row[o] = rrows[extra][r]
                rows_out.append(row)
                source.append(f"{lname} + {rname}")
                stats["matched"] += 1
            elif how in ("left", "outer"):
                rows_out.append(combine(lrow, None))
                source.append(f"{lname} only")
                stats["only_left"] += 1
        if how in ("outer", "right"):
            for ri, rrow in enumerate(rrows):
                if ri not in used:
                    rows_out.append(combine(None, rrow))
                    source.append(f"{rname} only")
                    stats["only_right"] += 1
        stats["people_with_more_than_one_match_on_the_right"] = multi
    else:
        for lrow in lrows:
            rows_out.append(combine(lrow, None))
            source.append(lname)
        for rrow in rrows:
            rows_out.append(combine(None, rrow))
            source.append(rname)
        stats["stacked_rows"] = {lname: len(lrows), rname: len(rrows)}
    cols_final = out_cols
    if source_column:
        cols_final = _dedupe_headers(out_cols + [source_column])
        rows_out = [r + [src] for r, src in zip(rows_out, source)]
    dup_info = None
    if mode == "stack" and dedupe_result:
        dkeys = [(l, kind) for l, r, kind in keys if kind in ("phone", "email")] or [(l, kind) for l, r, kind in keys]
        rid = list(range(1, len(rows_out) + 1))
        before = len(rows_out)
        if source_column:  # a person who is in both sheets says so
            same, _shared, _big = duplicate_groups(rows_out, dkeys, max_group)
            si = len(cols_final) - 1
            for members in same:
                if len({rows_out[i][si] for i in members}) > 1:
                    for i in members:
                        rows_out[i][si] = f"{lname} + {rname}"
        rows_out, rid, dup_info = dedupe(cols_final, rows_out, rid, dkeys, "last" if conflict == "prefer_right" else "first", max_group)
        stats["people_in_both_sheets_merged"] = before - len(rows_out)
    if len(rows_out) > MAX_ROWS:
        raise ValueError(f"The result would have {len(rows_out)} rows and a sheet holds at most {MAX_ROWS}. Filter or split the inputs first.")
    if len(cols_final) > MAX_COLS:
        raise ValueError(f"The result would have {len(cols_final)} columns and a sheet holds at most {MAX_COLS}. Drop columns you do not need first (sheet_clean keep_columns).")
    report = {"mode": mode, "how": how if mode == "join" else "", "matched_on": [f"{lcols[l]} = {rcols[r]} ({k})" for l, r, k in keys],
              "column_mapping": [{"from": f"{rname}: {rcols[r]}", "into": out_cols[o]} for r, o in sorted(r_to_out.items())],
              "rows": {lname: len(lrows), rname: len(rrows), "result": len(rows_out)}, **stats,
              "conflicts": {"count": both_conflicts[0], "rule": conflict, "examples": ex}}
    if dup_info:
        report["duplicates_merged"] = {k: v for k, v in dup_info.items() if k not in ("examples", "disagreement_examples")}
        report["conflicts"] = {"count": dup_info["disagreements_kept_as_first_choice"], "rule": "the row from the first sheet wins" if conflict != "prefer_right" else "the row from the second sheet wins",
                               "examples": dup_info["disagreement_examples"]}
    if all(k == "name" for _l, _r, k in keys):
        report["warning"] = "People were matched by name only. Two different people with the same name would be joined, so check the result."
    return cols_final, rows_out, report


# ---------- query ----------
def aggregate(cols, rows, indexes, group_by=None, metrics=None, types=None, limit=100):
    """Counts (and sums, averages, minimums, maximums) over the chosen rows, optionally grouped by a column."""
    metrics = metrics or []
    gi = resolve_col(cols, group_by, types) if group_by else None
    groups = defaultdict(list)
    shown = {}
    for i in indexes:
        k = fold(rows[i][gi]) if gi is not None else ""
        groups[k].append(i)
        shown.setdefault(k, clean_text(rows[i][gi]) if gi is not None else "all")
    out = []
    for k, idx in groups.items():
        entry = {"value": shown[k] or "(blank)", "count": len(idx)} if gi is not None else {"count": len(idx)}
        for m in metrics:
            op, ci = str(m.get("op") or "count").lower(), resolve_col(cols, m.get("column"), types) if m.get("column") else None
            if op == "count" or ci is None:
                continue
            nums = [x for x in (parse_number(rows[i][ci]) for i in idx) if x is not None]
            label = f"{op} of {cols[ci]}"
            entry[label] = (fmt_number(sum(nums)) if op == "sum" else round(sum(nums) / len(nums), 4) if op in ("avg", "average", "mean") and nums
                            else fmt_number(min(nums)) if op == "min" and nums else fmt_number(max(nums)) if op == "max" and nums else "")
        out.append(entry)
    out.sort(key=lambda e: -e["count"])
    return out[:limit], len(out)

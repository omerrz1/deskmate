"""Everyday office tools that need no outside account: calculating, spreadsheet analysis, charts, and exporting documents to Word, PDF, HTML, CSV or Excel."""
import ast
import csv
import html
import io
import json
import math
import operator
import re
import uuid
import zipfile

from . import db, files, importer, team

_S = {"type": "string"}
_I = {"type": "integer"}
def file_dir():
    d = db.data_dir() / "files"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d

TOOLS = [
    {"name": "calculate", "description": "Calculate an arithmetic expression exactly, e.g. '(1200*0.2)+45'. Use it instead of doing sums in your head. Supports + - * / ** % // and round(), min(), max(), abs().",
     "parameters": {"type": "object", "properties": {"expression": _S}, "required": ["expression"]}},
    {"name": "sheet_to_customers", "description": "Import the rows of a spreadsheet as customers (columns like name, phone, email, company are recognised). Returns how many were added.",
     "parameters": {"type": "object", "properties": {"id": _I}, "required": ["id"]}},
    {"name": "make_chart", "description": "Draw a chart (bar, line or pie) from two columns of a spreadsheet and file it as an image in your files.",
     "parameters": {"type": "object", "properties": {"id": _I, "kind": {**_S, "enum": ["bar", "line", "pie"]}, "x": {**_S, "description": "label column"}, "y": {**_S, "description": "numbers column"}, "title": _S}, "required": ["id", "kind", "x", "y"]}},
    {"name": "export_document", "description": "Turn a document into a file to send or keep: docx, pdf or html (documents), or xlsx, csv (spreadsheets). The new file appears in the same folder.",
     "parameters": {"type": "object", "properties": {"id": _I, "format": {**_S, "enum": ["docx", "pdf", "html", "xlsx", "csv"]}}, "required": ["id", "format"]}},
]
NAMES = {t["name"] for t in TOOLS}

# ---------- calculate ----------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"round": round, "min": min, "max": max, "abs": abs}


def _eval(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        a, b = _eval(n.left), _eval(n.right)
        if isinstance(n.op, ast.Pow) and abs(b) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(n.op)](a, b)
    if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_eval(n.operand))
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
        return _FUNCS[n.func.id](*[_eval(x) for x in n.args])
    raise ValueError("unsupported")


def calc(expr):
    expr = str(expr).replace(",", "").replace("×", "*").replace("÷", "/").replace("^", "**")[:200]
    try:
        v = _eval(ast.parse(expr, mode="eval").body)
    except ZeroDivisionError:
        return {"error": "division by zero"}
    except Exception:
        return {"error": "I can only do arithmetic like (1200*0.2)+45"}
    return {"result": round(v, 10) if isinstance(v, float) else v}


# ---------- spreadsheets ----------
def _doc(agent, did):
    d = db.q1("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        return None, {"error": "not found"}
    if not team.can_access_doc(agent, d):
        return None, {"error": "That file is private to someone else. Ask them to share it with you."}
    return d, None


def _table(d):
    t = json.loads(d["body"] or "{}")
    return t.get("columns", []), t.get("rows", [])


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("£", "").replace("$", "").replace("€", "").strip())
    except ValueError:
        return None


def _col_index(cols, name):
    for i, c in enumerate(cols):
        if str(c).lower() == str(name).lower():
            return i
    raise KeyError(name)


# ---------- charts (plain SVG, no libraries) ----------
def _esc(s):
    return html.escape(str(s), quote=True)


def chart_svg(kind, labels, values, title):
    W, H, PAD = 640, 380, 50
    palette = ["#111111", "#2f6df6", "#e5484d", "#12a594", "#f5a524", "#8e4ec6", "#e93d82", "#0b8f5a"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui, sans-serif" font-size="12"><rect width="{W}" height="{H}" fill="#fff"/>',
           f'<text x="{W / 2}" y="24" text-anchor="middle" font-size="16" font-weight="600">{_esc(title)}</text>']
    n = len(values)
    if not n:
        return "".join(out) + "</svg>"
    if kind == "pie":
        total = sum(v for v in values if v > 0) or 1
        cx, cy, r, ang = 200, 205, 130, -math.pi / 2
        for i, v in enumerate(values):
            if v <= 0:
                continue
            a2 = ang + 2 * math.pi * v / total
            x1, y1, x2, y2 = cx + r * math.cos(ang), cy + r * math.sin(ang), cx + r * math.cos(a2), cy + r * math.sin(a2)
            out.append(f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {1 if a2 - ang > math.pi else 0} 1 {x2:.1f},{y2:.1f} Z" fill="{palette[i % 8]}"/>')
            ang = a2
            out.append(f'<rect x="380" y="{60 + i * 22}" width="12" height="12" fill="{palette[i % 8]}"/><text x="400" y="{71 + i * 22}">{_esc(str(labels[i])[:24])} ({v:g})</text>')
        return "".join(out) + "</svg>"
    mx = max(max(values), 0) or 1
    mn = min(min(values), 0)
    span = (mx - mn) or 1
    left, right, top, bottom = PAD + 10, W - 20, 50, H - 60
    y_of = lambda v: bottom - (v - mn) / span * (bottom - top)
    for g in range(5):
        gv = mn + span * g / 4
        out.append(f'<line x1="{left}" x2="{right}" y1="{y_of(gv):.1f}" y2="{y_of(gv):.1f}" stroke="#eee"/><text x="{left - 6}" y="{y_of(gv) + 4:.1f}" text-anchor="end" fill="#666">{gv:,.4g}</text>')
    step = (right - left) / n
    if kind == "bar":
        for i, v in enumerate(values):
            x = left + i * step + step * 0.15
            out.append(f'<rect x="{x:.1f}" y="{min(y_of(v), y_of(0)):.1f}" width="{step * 0.7:.1f}" height="{abs(y_of(v) - y_of(0)):.1f}" fill="#111"/>')
    else:
        pts = " ".join(f"{left + (i + 0.5) * step:.1f},{y_of(v):.1f}" for i, v in enumerate(values))
        out.append(f'<polyline points="{pts}" fill="none" stroke="#2f6df6" stroke-width="2.5"/>')
        out.extend(f'<circle cx="{left + (i + 0.5) * step:.1f}" cy="{y_of(v):.1f}" r="3.5" fill="#2f6df6"/>' for i, v in enumerate(values))
    for i, lab in enumerate(labels):
        if n <= 14 or i % math.ceil(n / 14) == 0:
            out.append(f'<text x="{left + (i + 0.5) * step:.1f}" y="{bottom + 16}" text-anchor="middle" fill="#444">{_esc(str(lab)[:12])}</text>')
    return "".join(out) + "</svg>"


# ---------- exports ----------
def _docx(title, text):
    def para(t, bold=False, size=22):
        t = _esc(t)
        return f'<w:p><w:r><w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/></w:rPr><w:t xml:space="preserve">{t}</w:t></w:r></w:p>'
    body = []
    for line in text.split("\n"):
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            body.append(para(m.group(2), True, {1: 36, 2: 30, 3: 26}[len(m.group(1))]))
        elif re.match(r"^\s*[-*]\s+", line):
            body.append(para("• " + re.sub(r"^\s*[-*]\s+", "", line)))
        else:
            body.append(para(re.sub(r"\*\*(.*?)\*\*", r"\1", line)))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + para(title, True, 40) + "".join(body) + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _pdf(title, text):
    from fpdf import FPDF
    clean = lambda s: s.encode("latin-1", "replace").decode("latin-1")
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.multi_cell(0, 9, clean(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    for line in text.split("\n"):
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            pdf.set_font("Helvetica", "B", {1: 16, 2: 13, 3: 11}[len(m.group(1))])
            pdf.ln(2)
            pdf.multi_cell(0, 7, clean(m.group(2)), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font("Helvetica", "", 11)
            t = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
            t = ("  - " + re.sub(r"^\s*[-*]\s+", "", t)) if re.match(r"^\s*[-*]\s+", line) else t
            pdf.multi_cell(0, 6, clean(t) or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _save_file(agent, d, ext, mime, data):
    name = f"{uuid.uuid4().hex}.{ext}"
    path = file_dir() / name
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    did = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,file_path,mime,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (d["agent_id"] if agent is None or d["agent_id"] == (agent or {}).get("id") else (agent or {}).get("id", 0), f"{d['title']}.{ext}", "file", "", d["folder_id"], name, mime, db.now(), db.now()))
    return did


async def run_tool(agent, name, a):
    if name == "calculate":
        return calc(a.get("expression", "")), None
    d, err = _doc(agent, a.get("id"))
    if err:
        return err, None
    if name == "export_document":
        fmt = a.get("format")
        if d["kind"] == "sheet":
            cols, rows = _table(d)
            if fmt == "csv":
                buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols); w.writerows(rows)
                return {"file_id": _save_file(agent, d, "csv", "text/csv", buf.getvalue().encode("utf-8"))}, "/settings/files"
            if fmt == "xlsx":
                from openpyxl import Workbook
                wb = Workbook(); ws = wb.active; ws.append(cols)
                for r in rows:
                    ws.append(r)
                b = io.BytesIO(); wb.save(b)
                return {"file_id": _save_file(agent, d, "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b.getvalue())}, "/settings/files"
            return {"error": "Spreadsheets can be exported as xlsx or csv."}, None
        text = d["body"]
        if fmt == "docx":
            return {"file_id": _save_file(agent, d, "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", _docx(d["title"], text))}, "/settings/files"
        if fmt == "pdf":
            return {"file_id": _save_file(agent, d, "pdf", "application/pdf", _pdf(d["title"], text))}, "/settings/files"
        if fmt == "html":
            body = "".join(f"<p>{_esc(l)}</p>" for l in text.split("\n"))
            page = f"<!doctype html><meta charset=utf-8><title>{_esc(d['title'])}</title><body style='font-family:system-ui;max-width:720px;margin:40px auto'><h1>{_esc(d['title'])}</h1>{body}"
            return {"file_id": _save_file(agent, d, "html", "text/html", page.encode("utf-8"))}, "/settings/files"
        return {"error": "Documents can be exported as docx, pdf or html."}, None
    if d["kind"] != "sheet":
        return {"error": "That is a document, not a spreadsheet."}, None
    cols, rows = _table(d)
    try:
        if name == "sheet_to_customers":
            header = [str(c) for c in cols]
            body = [dict(zip(header, [str(x) for x in r])) for r in rows]
            mapping = importer.guess_mapping(header)
            if "name" not in mapping and "phone" not in mapping:
                return {"error": f"I couldn't tell which columns hold the name and phone number. Columns: {', '.join(header)}. Rename them (for example 'Name' and 'Phone') with edit_sheet and try again."}, None
            res = importer.import_rows(d["title"], d["title"], header, body, mapping, source_doc_id=d["id"])
            return {"imported": res.get("imported", res), "details": {k: v for k, v in res.items() if k != "imported"}}, "/data"
        if name == "make_chart":
            xi, yi = _col_index(cols, a["x"]), _col_index(cols, a["y"])
            pts = [(str(r[xi]) if xi < len(r) else "", _num(r[yi]) if yi < len(r) else None) for r in rows]
            pts = [(l, v) for l, v in pts if v is not None][:60]
            svg = chart_svg(a.get("kind", "bar"), [p[0] for p in pts], [p[1] for p in pts], a.get("title") or f"{a['y']} by {a['x']}")
            did = db.run("INSERT INTO documents(agent_id,title,kind,body,folder_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (d["agent_id"] if d["agent_id"] == (agent or {}).get("id") else (agent or {}).get("id", 0), (a.get("title") or f"Chart: {a['y']} by {a['x']}")[:120], "chart", svg, d["folder_id"], db.now(), db.now()))
            return {"chart_file_id": did, "points": len(pts)}, "/settings/files"
    except KeyError as e:
        return {"error": f"There is no column called {e.args[0]}. Columns: {', '.join(map(str, cols))}"}, None
    return {"error": "unknown tool"}, None

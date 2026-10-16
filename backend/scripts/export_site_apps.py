"""Writes frontend/src/site/apps.json: the app counts, categories and logos the public home page shows.
Run it again whenever apps are added:  python scripts/export_site_apps.py   (it does not touch any company data)"""
import json
import os
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="deskmates-export-"))  # importing the catalog must never open a real company's data
sys.path.insert(0, str(ROOT))
from app import catalog  # noqa: E402

FRONT = ROOT.parent / "frontend" / "public" / "connector-icons" / "index.json"
OUT = ROOT.parent / "frontend" / "src" / "site" / "apps.json"
have = set(json.loads(FRONT.read_text()))

connect = [a for a in catalog.APPS if not a.get("builtin")]
builtin = [a for a in catalog.APPS if a.get("builtin")]
cats = OrderedDict()
for a in connect:
    cats.setdefault(a["category"], []).append(a)
categories = sorted(({"name": c, "count": len(v), "examples": [x["name"] for x in sorted(v, key=lambda x: (not x.get("popular"), x["name"]))[:4]]} for c, v in cats.items()), key=lambda c: (-c["count"], c["name"]))
logos = sorted((a for a in connect if a["id"] in have), key=lambda a: (not a.get("popular"), a["name"].lower()))
data = {"connectable": len(connect), "builtin": len(builtin), "categories": categories,
        "logos": [{"id": a["id"], "name": a["name"]} for a in logos]}
OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
print(f"{len(connect)} connectable apps in {len(categories)} categories, {len(builtin)} built in, {len(logos)} with logos -> {OUT.relative_to(ROOT.parent)}")

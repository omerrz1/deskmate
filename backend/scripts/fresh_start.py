"""Start a company from a clean slate: removes all customers, calls, campaigns, knowledge, agents and their work, files, connected apps and company details.
Keeps the admin accounts and sign-ins. A backup of the database is written to data/backups/ first. Stop the backend before running it.

    python scripts/fresh_start.py --yes
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import workspace  # noqa: E402

if "--yes" not in sys.argv:
    print(__doc__)
    sys.exit(1)
r = workspace.wipe(True)
print("Backup written to", r["backup"])
for t, n in r["cleared"].items():
    print(f"  cleared {t}: {n}")
print("Done. Start the backend; the app opens on the company set-up screen.")

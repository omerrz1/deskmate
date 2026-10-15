"""Forgot the admin password? Run this ON THE SERVER (it needs access to the data folder), then restart the backend.
It removes the admin account and all sessions; the first-run setup page (with a fresh one-time token) appears again.
Customers, calls and recordings are not touched."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402

db.run("DELETE FROM sessions")
db.run("DELETE FROM admins")
db.run("DELETE FROM login_failures")
db.log("security", "Admin account reset from the server")
print("Admin removed. Restart the backend, then read backend/data/SETUP_TOKEN to create a new admin.")

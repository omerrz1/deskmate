"""Tests that import the application. They get their own empty data folder, so they can never see (or change) real company data."""
import os
import tempfile

os.environ["RENDER_PAGES"] = "false"  # the suite must never open a real browser or a real website
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="deskmate-tests-")  # set before `app` is imported: the database opens at import time
os.environ.setdefault("OPENAI_API_KEY", "not-a-real-key")           # the model is always a scripted fake in these tests

from app import security  # noqa: E402  (after DATA_DIR is set)

security.LIMITS["signup_all"] = (10 ** 9, 3600)  # the suite signs up hundreds of companies; the ceiling is tested on its own in test_signup_gate

"""Tests that import the application. They get their own empty data folder, so they can never see (or change) real company data."""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="deskmate-tests-")  # set before `app` is imported: the database opens at import time
os.environ.setdefault("OPENAI_API_KEY", "not-a-real-key")           # the model is always a scripted fake in these tests

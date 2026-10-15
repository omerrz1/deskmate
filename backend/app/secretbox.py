"""Encrypts the few secrets the server must be able to read again (the OpenAI and ElevenLabs keys, two-factor secrets).
The encryption key lives in data/.keyfile (owner-only) and never inside the database, so a copy of the database or a backup file on its own
does not contain usable keys: restoring it on another machine simply asks for the keys again."""
import os

from . import db

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # still works, but stores the value as plain text and says so in the prefix
    Fernet = None
    InvalidToken = Exception

_KEYFILE = db.DATA_DIR / ".keyfile"
_f = None


def _fernet():
    global _f
    if _f is None:
        if not _KEYFILE.exists():
            fd = os.open(str(_KEYFILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(Fernet.generate_key())
        _f = Fernet(_KEYFILE.read_bytes())
    return _f


def seal(text):
    """-> a string that is safe to keep in the database."""
    if not text:
        return ""
    if Fernet is None:
        return "plain:" + text
    return "enc:" + _fernet().encrypt(text.encode()).decode()


def unseal(token):
    """-> the original text, or '' if it cannot be read (for example a database restored on another machine)."""
    if not token:
        return ""
    if token.startswith("plain:"):
        return token[6:]
    if token.startswith("enc:") and Fernet is not None:
        try:
            return _fernet().decrypt(token[4:].encode()).decode()
        except (InvalidToken, ValueError):
            return ""
    return token  # a value saved before encryption existed

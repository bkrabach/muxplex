"""
muxplex authentication — password and signing secret file management.
"""

import secrets
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner


# ---------------------------------------------------------------------------
# Config directory
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    """Return ~/.config/muxplex, creating it (mode 0700) if needed."""
    d = Path.home() / ".config" / "muxplex"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Password file management
# ---------------------------------------------------------------------------


def get_password_path() -> Path:
    """Return the path to the password file: ~/.config/muxplex/password."""
    return Path.home() / ".config" / "muxplex" / "password"


def load_password() -> str | None:
    """Read the password file if it exists, return None otherwise."""
    path = get_password_path()
    if not path.exists():
        return None
    return path.read_text().strip()


def generate_and_save_password() -> str:
    """Generate a random password, write it to the password file (0600), return it."""
    pw = secrets.token_urlsafe(20)
    path = get_password_path()
    _config_dir()  # ensures dir exists with mode 0700
    path.write_text(pw + "\n")
    path.chmod(0o600)
    return pw


# ---------------------------------------------------------------------------
# Secret (signing key) management
# ---------------------------------------------------------------------------


def get_secret_path() -> Path:
    """Return the path to the signing secret file: ~/.config/muxplex/secret."""
    return Path.home() / ".config" / "muxplex" / "secret"


def load_or_create_secret() -> str:
    """Load the signing secret from file, or create one if it doesn't exist."""
    path = get_secret_path()
    if path.exists():
        return path.read_text().strip()
    secret = secrets.token_urlsafe(32)
    _config_dir()  # ensures dir exists with mode 0700, consistent with generate_and_save_password()
    path.write_text(secret + "\n")
    path.chmod(0o600)
    return secret


# ---------------------------------------------------------------------------
# Session cookie signing / verification
# ---------------------------------------------------------------------------


def create_session_cookie(secret: str, ttl_seconds: int) -> str:
    """Create a signed, timestamped session cookie value."""
    signer = TimestampSigner(secret)
    # ttl_seconds is not used at signing time; the timestamp is embedded in
    # the signed value and checked against ttl_seconds during verification.
    return signer.sign("muxplex-session").decode()


def verify_session_cookie(secret: str, cookie: str, ttl_seconds: int) -> bool:
    """Verify a session cookie's signature and expiry. Returns True/False.

    ttl_seconds=0 means session cookie — no server-side expiry check.
    """
    signer = TimestampSigner(secret)
    try:
        max_age = ttl_seconds if ttl_seconds > 0 else None
        signer.unsign(cookie, max_age=max_age)
        return True
    except (BadSignature, SignatureExpired):
        return False

"""
muxplex authentication — password and signing secret file management.
"""

import base64
import hmac
import logging
import secrets
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

_log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# PAM authentication
# ---------------------------------------------------------------------------


def pam_available() -> bool:
    """Check whether the python-pam module is importable."""
    try:
        import pam  # noqa: F401

        return True
    except ImportError:
        return False


def authenticate_pam(username: str, password: str) -> bool:
    """Authenticate via PAM. Username must match the running process owner."""
    import os
    import pwd

    import pam

    running_user = pwd.getpwuid(os.getuid()).pw_name
    if username != running_user:
        return False
    return pam.authenticate(username, password, service="login")


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

# Paths that bypass auth (login page itself, static assets it needs)
_AUTH_EXEMPT_PATHS = {"/login", "/auth/mode", "/auth/logout", "/api/instance-info"}

# File extensions that are always served without auth — the login page needs
# its own CSS, JS, images, and fonts before the user has a session cookie.
_STATIC_EXTENSIONS = {
    ".css",
    ".js",
    ".svg",
    ".png",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
}

# Socket-level localhost addresses — cannot be forged via HTTP headers
_LOCALHOST_ADDRS = {"127.0.0.1", "::1"}


class AuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces authentication on non-localhost requests."""

    def __init__(
        self,
        app,
        auth_mode: str,
        secret: str,
        ttl_seconds: int,
        password: str = "",
        federation_key: str = "",
    ):
        super().__init__(app)
        self.auth_mode = auth_mode
        self.secret = secret
        self.ttl_seconds = ttl_seconds
        self.password = password
        self.federation_key = federation_key

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Localhost bypass — client.host is the socket-level IP and cannot
        # be forged by the client (unlike the HTTP Host header).
        client_host = request.client.host if request.client else ""
        if client_host in _LOCALHOST_ADDRS:
            return await call_next(request)

        # 2. Exempt paths (login page, auth endpoints)
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # 3. Static assets — login page needs its CSS/JS/images before auth
        path = request.url.path
        if any(path.endswith(ext) for ext in _STATIC_EXTENSIONS):
            return await call_next(request)

        # 4. Valid session cookie
        cookie = request.cookies.get("muxplex_session")
        if cookie and verify_session_cookie(self.secret, cookie, self.ttl_seconds):
            return await call_next(request)

        # 4a. Bearer token (server-to-server federation)
        auth_header = request.headers.get("authorization", "")
        if self.federation_key and auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
            if hmac.compare_digest(token, self.federation_key):
                return await call_next(request)
            _log.warning("federation: rejected Bearer from %s", client_host)

        # 5. Authorization: Basic header
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("basic "):
            try:
                # Strip "Basic " prefix (6 chars) before base64-decoding
                decoded = base64.b64decode(auth_header[6:]).decode()
                username, _, pw = decoded.partition(":")
                if self._check_credentials(username, pw):
                    return await call_next(request)
            except Exception:
                pass
            return JSONResponse({"detail": "Invalid credentials"}, status_code=401)

        # 6. No auth — redirect browsers, 401 for API clients
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse(url="/login", status_code=307)

    def _check_credentials(self, username: str, password: str) -> bool:
        """Validate credentials against the configured auth mode."""
        if self.auth_mode == "pam":
            return authenticate_pam(username, password)
        return password == self.password

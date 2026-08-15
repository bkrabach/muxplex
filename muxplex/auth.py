"""
muxplex authentication — password and signing secret file management.
"""

import base64
import hmac
import logging
import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from muxplex.settings import load_federation_key

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ?next= redirect validation
# ---------------------------------------------------------------------------


def validate_next_path(next_value: str | None) -> str:
    """Validate a client-or-request-supplied ``?next=`` redirect target.

    This is the sole guard standing between /login's post-auth redirect and
    becoming an open redirect, so it fails CLOSED: anything that isn't
    unambiguously a same-origin, path-only value degrades to "/" (the
    pre-existing unconditional destination) rather than erroring or
    redirecting anywhere unexpected.

    Rejects:
    - empty / missing / non-string input
    - control characters (defense-in-depth against header/response-splitting
      style tricks riding in on the value)
    - backslashes anywhere -- some browsers normalize a leading "/\\" to
      "//", which is the protocol-relative bypass below via a different
      character
    - anything not starting with a single "/" (relative paths, bare hosts,
      e.g. "evil.com/x")
    - "//..." (protocol-relative -- browsers resolve this as an absolute URL
      to another host, e.g. "//evil.com")
    - any value containing "://" or a known URL scheme prefix
      ("javascript:", "data:", "http:", "https:", "vbscript:", "file:")
    - a parsed scheme or netloc (belt-and-suspenders on the two rules above,
      via ``urllib.parse.urlsplit``)
    - path traversal: a literal ".." path segment

    Returns the original value unchanged when it passes every check --
    callers must not re-derive or loosen this, since accepting an unsafe
    ``next`` would let an attacker redirect an authenticated user's browser
    off-origin after they enter their password.
    """
    if not next_value or not isinstance(next_value, str):
        return "/"
    if any(ord(c) < 0x20 for c in next_value):
        return "/"
    if "\\" in next_value:
        return "/"
    if not next_value.startswith("/") or next_value.startswith("//"):
        return "/"
    lowered = next_value.lower()
    if "://" in lowered:
        return "/"
    for scheme in ("javascript:", "data:", "http:", "https:", "vbscript:", "file:"):
        if scheme in lowered:
            return "/"
    parsed = urlsplit(next_value)
    if parsed.scheme or parsed.netloc:
        return "/"
    if ".." in parsed.path.split("/"):
        return "/"
    return next_value


def build_login_redirect_url(next_value: str | None) -> str:
    """Build the ``/login`` redirect target, appending a validated ``?next=``.

    Used both by AuthMiddleware (redirecting an unauthenticated browser
    request to /login) and by post_login's failure path (preserving the
    intended destination across a wrong-password retry). Returns a bare
    ``/login`` when *next_value* validates to the default "/" -- no reason to
    carry a no-op query string on the common case.
    """
    safe_next = validate_next_path(next_value)
    if safe_next == "/":
        return "/login"
    return f"/login?next={quote(safe_next, safe='')}"


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

# Paths that bypass auth (login page itself, static assets it needs).
# /api/ca is exempt for the same reason /api/instance-info is: it serves a
# CA *public* certificate, which is not a secret (no private key material,
# and it's the trust anchor clients are meant to install to verify this
# server's TLS leaf) \u2014 see main.py's get_ca_certificate() for the full
# rationale. Do not "harden" this into requiring auth; that would defeat
# the endpoint's purpose (a client can't authenticate over TLS it doesn't
# yet trust).
#
# /ca.crt and /setup are exempt for the identical reason: /ca.crt serves
# the SAME bytes as /api/ca (just with the MIME type Android's
# DownloadManager recognizes -- see main.py's get_ca_certificate_for_install
# docstring), and /setup is the onboarding page that links to it -- a user
# who hasn't installed the CA yet, by definition, cannot hold a valid
# session cookie for this server. Each is its own explicit entry because
# this check is an exact-path match, not a prefix match -- adding these two
# does not widen the exemption for any other path.
_AUTH_EXEMPT_PATHS = {
    "/login",
    "/auth/mode",
    "/auth/logout",
    "/api/instance-info",
    "/api/ca",
    "/ca.crt",
    "/setup",
}

# File extensions that are always served without auth — the login page needs
# its own CSS, JS, images, and fonts before the user has a session cookie.
_STATIC_EXTENSIONS = {
    ".css",
    ".js",
    ".json",
    ".svg",
    ".png",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
}

# The frontend's static asset tree — the ONLY thing exemption 3 below is meant
# to expose unauthenticated. Computed independently from main.py's identical
# `_FRONTEND_DIR` (same `Path(__file__).parent / "frontend"`, since this module
# lives beside it) rather than imported, to avoid a circular import (main.py
# imports AuthMiddleware from this module).
_FRONTEND_DIR = (Path(__file__).parent / "frontend").resolve()

def _is_real_static_asset(path: str) -> bool:
    """Return True only if *path* resolves to an actual file inside
    ``_FRONTEND_DIR`` — i.e. something the static-file mount would genuinely
    serve.

    This is the fix for a real incident: the exemption below used to be a
    bare ``path.endswith(ext)`` with no other guard, so it applied to EVERY
    route in the app, not just the static mount -- and the static mount
    happens to be registered at "/" (`app.mount("/", _NoCacheStaticFiles(...))`
    in main.py), so there is no path *prefix* to scope the exemption to
    either. A session literally named e.g. "build.js" (`SESSION_NAME_RE`
    permits ".") made `GET/DELETE /api/sessions/{name}` -- full live
    scrollback capture and session destruction, respectively -- reachable
    with NO credential at all, purely because the URL happened to end in a
    recognized suffix. Confirmed live in a DTU: an unauthenticated,
    non-localhost `GET /api/sessions/probe.js` reached the real endpoint
    (404 "Session not found") instead of being blocked (401), while the
    identical request without ".js" was correctly 401'd.

    Tying the exemption to "does a real file exist here" instead of "does
    the path merely look like an asset" closes that hole structurally: an
    API route's trailing path segment can never coincide with a real file
    on disk under the frontend directory, no matter what a client (or a
    session name) is called. A future route family living outside `/api/`
    inherits the same protection for free -- there is no prefix list to
    keep in sync as the route table grows.

    Resolves *path* against `_FRONTEND_DIR` and requires the result to
    stay inside it (defends the same traversal StaticFiles itself already
    guards against -- this check runs BEFORE that layer, so it must not be
    laxer than it) and to name an existing regular file, not a directory.
    """
    candidate = (_FRONTEND_DIR / path.lstrip("/")).resolve()
    try:
        candidate.relative_to(_FRONTEND_DIR)
    except ValueError:
        return False
    return candidate.is_file()


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
        # NOTE: there used to be a step 1 here that unconditionally trusted
        # any request whose socket peer was 127.0.0.1/::1. It is GONE, on
        # purpose -- see GHSA-7c6r-fvrh-9qp4. muxplex binds 0.0.0.0, so it
        # answers on every address in 127.0.0.0/8, and any userspace-mode
        # proxy (socat, `ssh -L`, an Incus/Docker userspace port-forward)
        # re-originates the connection, so the re-originated socket peer is
        # 127.0.0.1 for a genuinely REMOTE caller too. Measured live: an
        # unauthenticated `GET /api/sessions` through such a proxy returned
        # HTTP 200 with full session data, logged by muxplex itself as
        # `127.0.0.1:<port>`. There is no socket-level signal that
        # distinguishes "the process calling me is truly local" from "a
        # proxy re-originated this for someone remote" -- so no IP-based
        # rule can be correct here. Anything that needs local, credential-
        # free access must get a real credential instead (a session cookie,
        # the federation Bearer key, or HTTP Basic); it does not get a
        # special case, because a special case IS this bypass.
        client_host = request.client.host if request.client else ""

        # 1. Exempt paths (login page, auth endpoints)
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # 2. Static assets — login page needs its CSS/JS/images before auth.
        # The extension check is a cheap pre-filter; `_is_real_static_asset`
        # is the actual security boundary -- see its docstring for the
        # incident this closes. Both must pass: a request must both look
        # like an asset AND resolve to a real file under the frontend's
        # static tree.
        path = request.url.path
        if any(
            path.endswith(ext) for ext in _STATIC_EXTENSIONS
        ) and _is_real_static_asset(path):
            return await call_next(request)

        # 4. Valid session cookie
        cookie = request.cookies.get("muxplex_session")
        if cookie and verify_session_cookie(self.secret, cookie, self.ttl_seconds):
            return await call_next(request)

        # 4a. Bearer token (server-to-server federation).
        # Read the key fresh from disk on every request so a key generated or
        # rotated after startup (via POST /api/federation/generate-key) takes
        # effect immediately without a server restart.
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            federation_key = load_federation_key()
            if not federation_key:
                _log.warning(
                    "federation: Bearer token received from %s but no key configured on this server",
                    client_host,
                )
            else:
                token = auth_header[7:]
                if hmac.compare_digest(token, federation_key):
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
        # Carry the originally-requested path (+ query) through as ?next= so
        # a cold, unauthenticated deep link (e.g. an installed /deck/ PWA
        # launching straight into scope) lands back where it was headed
        # after login, instead of unconditionally at "/". Built from the
        # CURRENT request's own path -- already same-origin by construction
        # -- but still routed through validate_next_path for defense-in-depth
        # and to share one code path with post_login's failure retry.
        requested = request.url.path
        if request.url.query:
            requested = f"{requested}?{request.url.query}"
        login_url = build_login_redirect_url(requested)
        return RedirectResponse(url=login_url, status_code=307)

    def _check_credentials(self, username: str, password: str) -> bool:
        """Validate credentials against the configured auth mode."""
        if self.auth_mode == "pam":
            return authenticate_pam(username, password)
        return password == self.password

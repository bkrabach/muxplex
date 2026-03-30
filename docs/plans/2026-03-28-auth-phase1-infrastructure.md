# Auth Phase 1: Core Infrastructure — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Build the auth module (`auth.py`), middleware, and route stubs so all non-localhost HTTP/WS requests require authentication.

**Architecture:** A new `muxplex/auth.py` module provides password file management, signing secret management, session cookie signing/verification, PAM authentication, and a FastAPI middleware class. The middleware is mounted in `main.py` and gates every request. Localhost clients bypass auth entirely. A stub `/login` page and `/auth/mode` JSON endpoint are added for Phase 2 to build on.

**Tech Stack:** Python 3.11+, FastAPI, itsdangerous (TimestampSigner), python-pam, pytest

**Phase:** 1 of 2 — complete this phase before starting Phase 2 (`2026-03-28-auth-phase2-ui-cli.md`)

**Design doc:** `docs/plans/2026-03-28-auth-design.md`

---

### Task 1: Add dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml` (line 12–17, `[project.dependencies]`)

**Step 1: Add python-pam and itsdangerous to dependencies**

In `pyproject.toml`, add two entries to the `dependencies` list. The existing list looks like:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "aiofiles>=23.0",
    "websockets>=11.0",
]
```

Change it to:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "aiofiles>=23.0",
    "websockets>=11.0",
    "python-pam>=1.8.4",
    "itsdangerous>=2.1.0",
]
```

**Step 2: Install updated dependencies**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && uv pip install -e ".[dev]"`
Expected: installs without errors, both new packages appear in the output

**Step 3: Verify imports work**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -c "import pam; import itsdangerous; print('OK')"`
Expected: prints `OK`

**Step 4: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add pyproject.toml && git commit -m "chore: add python-pam and itsdangerous dependencies"
```

---

### Task 2: Password file management in auth.py

**Files:**
- Create: `muxplex/auth.py`
- Create: `muxplex/tests/test_auth.py`

**Step 1: Write the failing tests**

Create `muxplex/tests/test_auth.py`:

```python
"""Tests for muxplex/auth.py — authentication module."""

import os
import stat
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Password file management
# ---------------------------------------------------------------------------


def test_get_password_path_returns_expected_path(monkeypatch, tmp_path):
    """get_password_path() returns ~/.config/muxplex/password."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import get_password_path

    assert get_password_path() == tmp_path / ".config" / "muxplex" / "password"


def test_load_password_returns_none_when_no_file(monkeypatch, tmp_path):
    """load_password() returns None when password file does not exist."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import load_password

    assert load_password() is None


def test_load_password_reads_existing_file(monkeypatch, tmp_path):
    """load_password() reads and strips the password file contents."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    pw_path = tmp_path / ".config" / "muxplex" / "password"
    pw_path.parent.mkdir(parents=True, exist_ok=True)
    pw_path.write_text("my-secret-password\n")
    pw_path.chmod(0o600)

    from muxplex.auth import load_password

    assert load_password() == "my-secret-password"


def test_generate_and_save_password_creates_file(monkeypatch, tmp_path):
    """generate_and_save_password() creates the file and returns a non-empty string."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import generate_and_save_password

    pw = generate_and_save_password()
    assert isinstance(pw, str)
    assert len(pw) > 10

    pw_path = tmp_path / ".config" / "muxplex" / "password"
    assert pw_path.exists()
    assert pw_path.read_text().strip() == pw


def test_generate_and_save_password_sets_0600_permissions(monkeypatch, tmp_path):
    """generate_and_save_password() sets the file to mode 0600."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import generate_and_save_password

    generate_and_save_password()
    pw_path = tmp_path / ".config" / "muxplex" / "password"
    mode = stat.S_IMODE(pw_path.stat().st_mode)
    assert mode == 0o600
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "password" 2>&1 | head -30`
Expected: FAIL — `ModuleNotFoundError: No module named 'muxplex.auth'`

**Step 3: Write the implementation**

Create `muxplex/auth.py`:

```python
"""
muxplex authentication — password management, secret management,
session cookies, PAM integration, and request middleware.
"""

import os
import secrets
from pathlib import Path


# ---------------------------------------------------------------------------
# Config directory
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    """Return ~/.config/muxplex, creating it (mode 0700) if needed."""
    d = Path.home() / ".config" / "muxplex"
    d.mkdir(parents=True, exist_ok=True)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pw + "\n")
    path.chmod(0o600)
    return pw
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "password"`
Expected: all 5 tests PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/auth.py muxplex/tests/test_auth.py && git commit -m "feat(auth): password file management"
```

---

### Task 3: Secret file management in auth.py

**Files:**
- Modify: `muxplex/auth.py`
- Modify: `muxplex/tests/test_auth.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# Secret file management
# ---------------------------------------------------------------------------


def test_get_secret_path_returns_expected_path(monkeypatch, tmp_path):
    """get_secret_path() returns ~/.config/muxplex/secret."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import get_secret_path

    assert get_secret_path() == tmp_path / ".config" / "muxplex" / "secret"


def test_load_or_create_secret_creates_new_file(monkeypatch, tmp_path):
    """load_or_create_secret() creates a secret file when none exists."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import load_or_create_secret

    secret = load_or_create_secret()
    assert isinstance(secret, str)
    assert len(secret) > 20

    secret_path = tmp_path / ".config" / "muxplex" / "secret"
    assert secret_path.exists()


def test_load_or_create_secret_sets_0600_permissions(monkeypatch, tmp_path):
    """load_or_create_secret() sets the secret file to mode 0600."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import load_or_create_secret

    load_or_create_secret()
    secret_path = tmp_path / ".config" / "muxplex" / "secret"
    mode = stat.S_IMODE(secret_path.stat().st_mode)
    assert mode == 0o600


def test_load_or_create_secret_returns_same_value_on_second_call(monkeypatch, tmp_path):
    """load_or_create_secret() returns the same secret on subsequent calls."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import load_or_create_secret

    first = load_or_create_secret()
    second = load_or_create_secret()
    assert first == second
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "secret" 2>&1 | head -20`
Expected: FAIL — `ImportError: cannot import name 'get_secret_path' from 'muxplex.auth'`

**Step 3: Write the implementation**

Add to `muxplex/auth.py`, after the password management section:

```python
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret + "\n")
    path.chmod(0o600)
    return secret
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "secret"`
Expected: all 4 tests PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/auth.py muxplex/tests/test_auth.py && git commit -m "feat(auth): signing secret file management"
```

---

### Task 4: Session cookie signing and verification in auth.py

**Files:**
- Modify: `muxplex/auth.py`
- Modify: `muxplex/tests/test_auth.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# Session cookie signing / verification
# ---------------------------------------------------------------------------


def test_create_session_cookie_returns_string():
    """create_session_cookie() returns a non-empty string."""
    from muxplex.auth import create_session_cookie

    cookie = create_session_cookie("test-secret", ttl_seconds=3600)
    assert isinstance(cookie, str)
    assert len(cookie) > 0


def test_verify_session_cookie_valid_roundtrip():
    """A cookie created by create_session_cookie verifies successfully."""
    from muxplex.auth import create_session_cookie, verify_session_cookie

    cookie = create_session_cookie("test-secret", ttl_seconds=3600)
    assert verify_session_cookie("test-secret", cookie, ttl_seconds=3600) is True


def test_verify_session_cookie_tampered():
    """A tampered cookie fails verification."""
    from muxplex.auth import create_session_cookie, verify_session_cookie

    cookie = create_session_cookie("test-secret", ttl_seconds=3600)
    tampered = cookie + "X"
    assert verify_session_cookie("test-secret", tampered, ttl_seconds=3600) is False


def test_verify_session_cookie_wrong_secret():
    """A cookie signed with a different secret fails verification."""
    from muxplex.auth import create_session_cookie, verify_session_cookie

    cookie = create_session_cookie("secret-A", ttl_seconds=3600)
    assert verify_session_cookie("secret-B", cookie, ttl_seconds=3600) is False


def test_verify_session_cookie_expired():
    """An expired cookie (max_age=0) fails verification."""
    from muxplex.auth import create_session_cookie, verify_session_cookie

    cookie = create_session_cookie("test-secret", ttl_seconds=3600)
    # Verify with max_age=0 means it's immediately expired
    assert verify_session_cookie("test-secret", cookie, ttl_seconds=0) is False
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "cookie" 2>&1 | head -20`
Expected: FAIL — `ImportError: cannot import name 'create_session_cookie' from 'muxplex.auth'`

**Step 3: Write the implementation**

Add to `muxplex/auth.py`, adding the import at the top and the functions after the secret section:

Add `from itsdangerous import TimestampSigner, BadSignature, SignatureExpired` to the imports at the top of the file.

Then add after the secret management section:

```python
# ---------------------------------------------------------------------------
# Session cookie signing / verification
# ---------------------------------------------------------------------------


def create_session_cookie(secret: str, ttl_seconds: int) -> str:
    """Create a signed, timestamped session cookie value."""
    signer = TimestampSigner(secret)
    return signer.sign("muxplex-session").decode()


def verify_session_cookie(secret: str, cookie: str, ttl_seconds: int) -> bool:
    """Verify a session cookie's signature and expiry. Returns True/False."""
    signer = TimestampSigner(secret)
    try:
        signer.unsign(cookie, max_age=ttl_seconds if ttl_seconds > 0 else None)
        return ttl_seconds > 0 or True  # ttl=0 with valid sig is still valid (session cookie)
    except (BadSignature, SignatureExpired):
        return False
```

Wait — re-reading the design: `--session-ttl 0` means session cookie (clears on browser close). So `ttl_seconds=0` should mean **no server-side expiry check** (the cookie is valid until the browser drops it). But the test says `ttl_seconds=0` should fail. Let me reconsider.

Actually the test `test_verify_session_cookie_expired` needs a different approach. We need to test that a cookie signed *in the past* with a short TTL fails. Let's fix both the test and implementation:

Replace the expired test with:

```python
def test_verify_session_cookie_expired():
    """A cookie verified with a very short TTL fails (simulates expiry)."""
    import time
    from muxplex.auth import create_session_cookie, verify_session_cookie

    cookie = create_session_cookie("test-secret", ttl_seconds=1)
    time.sleep(1.1)  # Wait for it to expire
    assert verify_session_cookie("test-secret", cookie, ttl_seconds=1) is False
```

And the implementation:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "cookie"`
Expected: all 5 tests PASS (the expired test takes ~1.1s)

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/auth.py muxplex/tests/test_auth.py && git commit -m "feat(auth): session cookie signing and verification"
```

---

### Task 5: PAM authentication with running-user check

**Files:**
- Modify: `muxplex/auth.py`
- Modify: `muxplex/tests/test_auth.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# PAM authentication
# ---------------------------------------------------------------------------


def test_pam_available_returns_true_when_pam_importable():
    """pam_available() returns True when python-pam is installed."""
    from muxplex.auth import pam_available

    # python-pam is in our deps, so it should be importable
    assert pam_available() is True


def test_pam_available_returns_false_on_import_error(monkeypatch):
    """pam_available() returns False when pam cannot be imported."""
    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pam":
            raise ImportError("mock: no pam")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    from muxplex.auth import pam_available

    assert pam_available() is False


def test_authenticate_pam_success(monkeypatch):
    """authenticate_pam() returns True when PAM succeeds for the running user."""
    import pwd

    from muxplex.auth import authenticate_pam

    running_user = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setattr("pam.authenticate", lambda u, p, service="login": True)
    assert authenticate_pam(running_user, "correct-password") is True


def test_authenticate_pam_wrong_password(monkeypatch):
    """authenticate_pam() returns False when PAM rejects credentials."""
    import pwd

    from muxplex.auth import authenticate_pam

    running_user = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setattr("pam.authenticate", lambda u, p, service="login": False)
    assert authenticate_pam(running_user, "wrong-password") is False


def test_authenticate_pam_wrong_user_rejected(monkeypatch):
    """authenticate_pam() rejects a different username even if PAM would accept it."""
    from muxplex.auth import authenticate_pam

    # Mock PAM to always return True — but wrong username should still fail
    monkeypatch.setattr("pam.authenticate", lambda u, p, service="login": True)
    assert authenticate_pam("root", "any-password") is False
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "pam" 2>&1 | head -20`
Expected: FAIL — `ImportError: cannot import name 'pam_available' from 'muxplex.auth'`

**Step 3: Write the implementation**

Add to `muxplex/auth.py`, after the cookie section:

```python
# ---------------------------------------------------------------------------
# PAM authentication
# ---------------------------------------------------------------------------


def pam_available() -> bool:
    """Check whether the python-pam module is importable."""
    try:
        import pam  # noqa: F811

        return True
    except ImportError:
        return False


def authenticate_pam(username: str, password: str) -> bool:
    """Authenticate via PAM. Username must match the running process owner."""
    import os as _os
    import pwd

    import pam

    running_user = pwd.getpwuid(_os.getuid()).pw_name
    if username != running_user:
        return False
    return pam.authenticate(username, password, service="login")
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "pam"`
Expected: all 5 PAM tests PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/auth.py muxplex/tests/test_auth.py && git commit -m "feat(auth): PAM authentication with running-user check"
```

---

### Task 6: Auth middleware in auth.py

**Files:**
- Modify: `muxplex/auth.py`
- Modify: `muxplex/tests/test_auth.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from muxplex.auth import AuthMiddleware, create_session_cookie


def _make_test_app(auth_mode: str = "password", password: str = "test-pw") -> FastAPI:
    """Create a minimal FastAPI app with AuthMiddleware for testing."""
    test_app = FastAPI()

    test_app.add_middleware(
        AuthMiddleware,
        auth_mode=auth_mode,
        secret="test-secret",
        ttl_seconds=3600,
        password=password,
    )

    @test_app.get("/protected")
    async def protected():
        return PlainTextResponse("OK")

    return test_app


def test_middleware_localhost_bypasses_auth():
    """Requests from 127.0.0.1 pass through without auth."""
    app = _make_test_app()
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get("/protected")
    assert response.status_code == 200
    assert response.text == "OK"


def test_middleware_valid_session_cookie_passes():
    """Non-localhost request with a valid session cookie passes through."""
    app = _make_test_app()
    cookie = create_session_cookie("test-secret", ttl_seconds=3600)
    client = TestClient(app, base_url="http://192.168.1.1")
    response = client.get("/protected", cookies={"muxplex_session": cookie})
    assert response.status_code == 200
    assert response.text == "OK"


def test_middleware_tampered_cookie_redirects():
    """Non-localhost request with a tampered cookie redirects to /login."""
    app = _make_test_app()
    client = TestClient(app, base_url="http://192.168.1.1", follow_redirects=False)
    response = client.get("/protected", cookies={"muxplex_session": "bad.cookie.value"})
    assert response.status_code == 307
    assert "/login" in response.headers["location"]


def test_middleware_no_cookie_non_localhost_redirects():
    """Non-localhost request with no cookie redirects to /login."""
    app = _make_test_app()
    client = TestClient(app, base_url="http://192.168.1.1", follow_redirects=False)
    response = client.get("/protected")
    assert response.status_code == 307
    assert "/login" in response.headers["location"]


def test_middleware_basic_auth_valid_password():
    """Non-localhost request with valid Basic auth header passes through."""
    app = _make_test_app(auth_mode="password", password="test-pw")
    client = TestClient(app, base_url="http://192.168.1.1")
    creds = base64.b64encode(b":test-pw").decode()
    response = client.get("/protected", headers={"Authorization": f"Basic {creds}"})
    assert response.status_code == 200
    assert response.text == "OK"


def test_middleware_basic_auth_invalid_password():
    """Non-localhost request with wrong Basic auth header returns 401."""
    app = _make_test_app(auth_mode="password", password="test-pw")
    client = TestClient(app, base_url="http://192.168.1.1")
    creds = base64.b64encode(b":wrong-pw").decode()
    response = client.get("/protected", headers={"Authorization": f"Basic {creds}"})
    assert response.status_code == 401


def test_middleware_json_request_gets_401_not_redirect():
    """Non-localhost API request (Accept: application/json) gets 401, not redirect."""
    app = _make_test_app()
    client = TestClient(app, base_url="http://192.168.1.1", follow_redirects=False)
    response = client.get("/protected", headers={"Accept": "application/json"})
    assert response.status_code == 401


def test_middleware_login_path_excluded():
    """/login path is excluded from auth to avoid redirect loops."""
    app = _make_test_app()

    @app.get("/login")
    async def login():
        return PlainTextResponse("login page")

    client = TestClient(app, base_url="http://192.168.1.1")
    response = client.get("/login")
    assert response.status_code == 200
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "middleware" 2>&1 | head -20`
Expected: FAIL — `ImportError: cannot import name 'AuthMiddleware' from 'muxplex.auth'`

**Step 3: Write the implementation**

Add these imports to the top of `muxplex/auth.py`:

```python
import base64

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
```

Then add after the PAM section:

```python
# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

# Paths that bypass auth (login page itself, static assets it needs)
_AUTH_EXEMPT_PATHS = {"/login", "/auth/mode", "/auth/logout"}


class AuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces authentication on non-localhost requests."""

    def __init__(
        self,
        app,
        auth_mode: str,
        secret: str,
        ttl_seconds: int,
        password: str = "",
    ):
        super().__init__(app)
        self.auth_mode = auth_mode
        self.secret = secret
        self.ttl_seconds = ttl_seconds
        self.password = password

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Localhost bypass
        client_host = request.client.host if request.client else "unknown"
        if client_host in ("127.0.0.1", "::1"):
            return await call_next(request)

        # 2. Exempt paths (login page, auth endpoints)
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # 3. Valid session cookie
        cookie = request.cookies.get("muxplex_session")
        if cookie and verify_session_cookie(self.secret, cookie, self.ttl_seconds):
            return await call_next(request)

        # 4. Authorization: Basic header
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                username, _, pw = decoded.partition(":")
                if self._check_credentials(username, pw):
                    return await call_next(request)
            except Exception:
                pass
            return JSONResponse({"detail": "Invalid credentials"}, status_code=401)

        # 5. No auth — redirect browsers, 401 for API clients
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse(url="/login", status_code=307)

    def _check_credentials(self, username: str, password: str) -> bool:
        """Validate credentials against the configured auth mode."""
        if self.auth_mode == "pam":
            return authenticate_pam(username, password)
        return password == self.password
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v -k "middleware"`
Expected: all 8 middleware tests PASS

**Step 5: Run all auth tests**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_auth.py -v`
Expected: all tests PASS (password + secret + cookie + PAM + middleware)

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/auth.py muxplex/tests/test_auth.py && git commit -m "feat(auth): request middleware with localhost bypass and session cookie check"
```

---

### Task 7: Wire middleware into main.py

**Files:**
- Modify: `muxplex/main.py`

**Step 1: Write the failing test**

Append to `muxplex/tests/test_api.py`, at the very end:

```python
# ---------------------------------------------------------------------------
# Auth middleware integration
# ---------------------------------------------------------------------------


def test_non_localhost_without_auth_gets_redirected(monkeypatch):
    """A non-localhost request without credentials is redirected to /login."""
    from fastapi.testclient import TestClient

    from muxplex.main import app

    # Ensure auth is active — set a known password via env
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw-for-api")

    with TestClient(app, base_url="http://192.168.1.1") as c:
        response = c.get("/health", follow_redirects=False)
        # Should be redirected to /login or get 307/401
        assert response.status_code in (307, 401)
```

**Step 2: Run to verify it fails**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py::test_non_localhost_without_auth_gets_redirected -v`
Expected: FAIL — currently returns 200 (no middleware yet)

**Step 3: Wire middleware into main.py**

In `muxplex/main.py`, add auth setup logic. Add these imports near the top (after the existing imports):

```python
import pwd
import sys

from muxplex.auth import (
    AuthMiddleware,
    generate_and_save_password,
    load_or_create_secret,
    load_password,
    pam_available,
)
```

Then, after the `app = FastAPI(...)` line (line 173) and before the request/response models section, add:

```python
# ---------------------------------------------------------------------------
# Auth setup
# ---------------------------------------------------------------------------

def _resolve_auth() -> tuple[str, str]:
    """Determine auth mode and resolve password. Returns (auth_mode, password).

    Fallback chain for non-localhost:
      1. PAM available → ("pam", "")
      2. MUXPLEX_PASSWORD env → ("password", <env value>)
      3. ~/.config/muxplex/password file → ("password", <file value>)
      4. Auto-generate → ("password", <generated>)
    """
    # Explicit override: MUXPLEX_AUTH=password forces password mode
    force_password = os.environ.get("MUXPLEX_AUTH", "").lower() == "password"

    if not force_password and pam_available():
        running_user = pwd.getpwuid(os.getuid()).pw_name
        print(f"  muxplex auth: PAM (user: {running_user})", file=sys.stderr)
        return "pam", ""

    if not force_password:
        print("  muxplex auth: PAM unavailable, using password mode", file=sys.stderr)

    # Password mode — resolve password
    env_pw = os.environ.get("MUXPLEX_PASSWORD")
    if env_pw:
        print("  muxplex auth: password (env)", file=sys.stderr)
        return "password", env_pw

    file_pw = load_password()
    if file_pw:
        print(f"  muxplex auth: password (file: {load_password.__module__})", file=sys.stderr)
        return "password", file_pw

    # Last resort: auto-generate
    generated = generate_and_save_password()
    from muxplex.auth import get_password_path

    print(
        f"  muxplex auth: password generated — {generated} — saved to {get_password_path()}",
        file=sys.stderr,
    )
    return "password", generated


_auth_mode, _auth_password = _resolve_auth()
_auth_secret = load_or_create_secret()
_auth_ttl = int(os.environ.get("MUXPLEX_SESSION_TTL", "604800"))

app.add_middleware(
    AuthMiddleware,
    auth_mode=_auth_mode,
    secret=_auth_secret,
    ttl_seconds=_auth_ttl,
    password=_auth_password,
)
```

**Step 4: Run to verify test passes**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py::test_non_localhost_without_auth_gets_redirected -v`
Expected: PASS

**Step 5: Run the full existing test suite to make sure nothing broke**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py -v`
Expected: all existing tests still PASS (they use `TestClient` with default `base_url="http://testserver"` which resolves to localhost via TestClient internals — **but verify this**. If existing tests break because they're not coming from localhost, the `patch_startup_and_state` fixture needs to also set `MUXPLEX_PASSWORD` or the middleware needs to treat testserver as localhost.)

**Important:** If existing tests fail with 307/401, add this line to the `patch_startup_and_state` fixture in `test_api.py`:

```python
monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
```

This ensures auth resolves without side effects. The TestClient's default host (`testserver`) won't match localhost, so the fixture needs to provide auth context.

Also, if tests fail because `TestClient` doesn't present as localhost, you may need to add the `muxplex_session` cookie to the `client` fixture. **However**, the simpler fix is: TestClient by default uses `base_url="http://testserver"` and `request.client.host` will be `testclient` — which is NOT `127.0.0.1`. Two solutions:

**Option A (recommended):** Update the `client` fixture to pass a valid session cookie:
```python
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        # Authenticate for non-localhost TestClient
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl
        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c
```

**Option B:** Use `base_url="http://127.0.0.1"` on the existing client fixture. But this changes `request.client.host` behavior.

Verify which approach is needed by running the existing tests first. Adapt accordingly.

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: wire auth middleware into FastAPI app"
```

---

### Task 8: GET /login stub and /auth/mode endpoint

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

**Step 1: Write the failing tests**

Append to `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# Login stub and auth mode endpoint
# ---------------------------------------------------------------------------


def test_get_login_returns_200_html(client):
    """GET /login returns 200 with HTML content."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<form" in response.text


def test_get_auth_mode_returns_json(client):
    """GET /auth/mode returns JSON with mode field."""
    response = client.get("/auth/mode")
    assert response.status_code == 200
    data = response.json()
    assert "mode" in data
    assert data["mode"] in ("pam", "password")
```

**Step 2: Run to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py -v -k "login or auth_mode" 2>&1 | head -20`
Expected: FAIL — 404 (routes don't exist yet)

**Step 3: Add the routes to main.py**

Add these imports to the top of `muxplex/main.py` if not already present:

```python
from fastapi.responses import HTMLResponse, JSONResponse as FastJSONResponse
```

Then add these routes **before** the static file mount (before the `_FRONTEND_DIR` line, which must remain the last mount):

```python
# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Stub login page — replaced in Phase 2 with branded login.html."""
    return HTMLResponse(
        "<html><body>"
        "<h1>muxplex login</h1>"
        "<form method='POST' action='/login'>"
        "<input name='password' type='password' placeholder='Password' autocomplete='current-password'>"
        "<button type='submit'>Login</button>"
        "</form>"
        "</body></html>"
    )


@app.get("/auth/mode")
async def auth_mode_endpoint():
    """Return the current auth mode and running username."""
    username = ""
    if _auth_mode == "pam":
        username = pwd.getpwuid(os.getuid()).pw_name
    return {"mode": _auth_mode, "user": username}
```

**Step 4: Run to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_api.py -v -k "login or auth_mode"`
Expected: both tests PASS

**Step 5: Run the full test suite**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/ -v`
Expected: all tests PASS

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add /login stub and /auth/mode endpoint"
```

---

## Phase 1 Complete Checklist

After all 8 tasks:

- [ ] `pyproject.toml` has `python-pam` and `itsdangerous` in `[project.dependencies]`
- [ ] `muxplex/auth.py` exists with: password mgmt, secret mgmt, cookie signing, PAM auth, AuthMiddleware
- [ ] `muxplex/tests/test_auth.py` exists with tests for all of the above
- [ ] `muxplex/main.py` mounts AuthMiddleware, has `/login` stub and `/auth/mode` endpoint
- [ ] All tests in `muxplex/tests/` pass: `python -m pytest muxplex/tests/ -v`
- [ ] 8 clean commits with conventional commit messages

Proceed to Phase 2: `docs/plans/2026-03-28-auth-phase2-ui-cli.md`
"""Tests for muxplex/auth.py — authentication module."""

import base64
import os
import pwd
import stat
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from muxplex.auth import AuthMiddleware, create_session_cookie


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


def test_generate_and_save_password_sets_0700_on_config_dir(monkeypatch, tmp_path):
    """generate_and_save_password() creates the config directory with mode 0700."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    from muxplex.auth import generate_and_save_password

    generate_and_save_password()
    config_dir = tmp_path / ".config" / "muxplex"
    mode = stat.S_IMODE(config_dir.stat().st_mode)
    assert mode == 0o700


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
    """A cookie verified with a very short TTL fails (simulates expiry)."""
    import time
    from muxplex.auth import create_session_cookie, verify_session_cookie

    cookie = create_session_cookie("test-secret", ttl_seconds=1)
    # itsdangerous uses integer-second timestamps; sleep 2s to guarantee
    # age = 2 > max_age = 1, ensuring reliable expiry detection
    time.sleep(2)
    assert verify_session_cookie("test-secret", cookie, ttl_seconds=1) is False


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
    from muxplex.auth import authenticate_pam

    running_user = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setattr("pam.authenticate", lambda u, p, service="login": True)
    assert authenticate_pam(running_user, "correct-password") is True


def test_authenticate_pam_wrong_password(monkeypatch):
    """authenticate_pam() returns False when PAM rejects credentials."""
    from muxplex.auth import authenticate_pam

    running_user = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setattr("pam.authenticate", lambda u, p, service="login": False)
    assert authenticate_pam(running_user, "wrong-password") is False


def test_authenticate_pam_wrong_user_rejected(monkeypatch):
    """authenticate_pam() rejects a different username even if PAM would accept it."""
    from muxplex.auth import authenticate_pam

    # Pick a wrong user that is guaranteed to differ from whoever is running the tests
    running_user = pwd.getpwuid(os.getuid()).pw_name
    wrong_user = "nobody" if running_user == "root" else "root"

    # Mock PAM to always return True — but wrong username should still fail
    monkeypatch.setattr("pam.authenticate", lambda u, p, service="login": True)
    assert authenticate_pam(wrong_user, "any-password") is False


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class _InjectClientMiddleware:
    """Thin ASGI wrapper that injects a fake client address into the scope.

    Starlette's TestClient sets request.client.host to "testclient" regardless
    of base_url.  Wrapping the app with this middleware lets tests supply a
    specific socket-level IP so the AuthMiddleware localhost check is exercised
    with real values rather than relying on the (user-controlled) Host header.
    """

    def __init__(self, app, client_host: str, client_port: int = 50000):
        self.app = app
        self._client = (client_host, client_port)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            scope = {**scope, "client": self._client}
        await self.app(scope, receive, send)


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
    # TestClient always sets request.client.host to "testclient".  Wrap the app
    # with _InjectClientMiddleware to set the socket-level client IP to 127.0.0.1
    # so the middleware's localhost check is exercised with a real address.
    app_with_client = _InjectClientMiddleware(app, "127.0.0.1")
    client = TestClient(app_with_client)
    response = client.get("/protected")
    assert response.status_code == 200
    assert response.text == "OK"


def test_middleware_valid_session_cookie_passes():
    """Non-localhost request with a valid session cookie passes through."""
    app = _make_test_app()
    cookie = create_session_cookie("test-secret", ttl_seconds=3600)
    client = TestClient(app, base_url="http://192.168.1.1")
    client.cookies.set("muxplex_session", cookie)
    response = client.get("/protected")
    assert response.status_code == 200
    assert response.text == "OK"


def test_middleware_tampered_cookie_redirects():
    """Non-localhost request with a tampered cookie redirects to /login."""
    app = _make_test_app()
    client = TestClient(app, base_url="http://192.168.1.1", follow_redirects=False)
    client.cookies.set("muxplex_session", "bad.cookie.value")
    response = client.get("/protected")
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


# ---------------------------------------------------------------------------
# _resolve_auth startup logging
# ---------------------------------------------------------------------------


def test_resolve_auth_pam_mode_logs_pam(monkeypatch, capsys, tmp_path):
    """_resolve_auth() in PAM mode logs 'PAM' to stderr."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("MUXPLEX_AUTH", raising=False)
    monkeypatch.delenv("MUXPLEX_PASSWORD", raising=False)
    monkeypatch.setattr("muxplex.main.pam_available", lambda: True)

    from muxplex.main import _resolve_auth

    mode, pw = _resolve_auth()
    captured = capsys.readouterr()

    assert mode == "pam"
    assert "PAM" in captured.err


def test_resolve_auth_env_password_logs_env(monkeypatch, capsys, tmp_path):
    """_resolve_auth() with MUXPLEX_PASSWORD env var logs 'env' to stderr."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")
    monkeypatch.setenv("MUXPLEX_PASSWORD", "from-env")
    monkeypatch.setattr("muxplex.main.pam_available", lambda: False)

    from muxplex.main import _resolve_auth

    mode, pw = _resolve_auth()
    captured = capsys.readouterr()

    assert mode == "password"
    assert pw == "from-env"
    assert "env" in captured.err


def test_resolve_auth_file_password_logs_file(monkeypatch, capsys, tmp_path):
    """_resolve_auth() with a file password logs the file path to stderr (not module name)."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")
    monkeypatch.delenv("MUXPLEX_PASSWORD", raising=False)
    monkeypatch.setattr("muxplex.main.pam_available", lambda: False)

    # Create password file
    pw_path = tmp_path / ".config" / "muxplex" / "password"
    pw_path.parent.mkdir(parents=True, exist_ok=True)
    pw_path.write_text("file-secret-pw\n")
    pw_path.chmod(0o600)

    from muxplex.main import _resolve_auth

    mode, pw = _resolve_auth()
    captured = capsys.readouterr()

    assert mode == "password"
    assert pw == "file-secret-pw"
    # Should log the actual file path, NOT the module name (Phase 1 bug fix)
    assert "muxplex.auth" not in captured.err
    assert "file" in captured.err or "password" in captured.err


def test_resolve_auth_generates_password_as_last_resort(monkeypatch, capsys, tmp_path):
    """_resolve_auth() generates a password when no env or file password exists."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")
    monkeypatch.delenv("MUXPLEX_PASSWORD", raising=False)
    monkeypatch.setattr("muxplex.main.pam_available", lambda: False)

    from muxplex.main import _resolve_auth

    mode, pw = _resolve_auth()
    captured = capsys.readouterr()

    assert mode == "password"
    assert len(pw) > 10
    assert "generated" in captured.err
    assert pw in captured.err

"""Tests for muxplex/auth.py — authentication module."""

import os
import stat
from pathlib import Path


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

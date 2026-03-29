"""Tests for muxplex/auth.py — authentication module."""

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

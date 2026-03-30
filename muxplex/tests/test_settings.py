"""
Tests for muxplex/settings.py — server-side settings management.
7 acceptance-criteria tests defined here.
"""

import json

import pytest

import muxplex.settings as settings_mod
from muxplex.settings import (
    DEFAULT_SETTINGS,
    load_settings,
    patch_settings,
    save_settings,
)


# ---------------------------------------------------------------------------
# Autouse fixture: redirect SETTINGS_PATH to tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def redirect_settings_path(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH to a temporary file for all tests."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    return fake_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_returns_defaults_when_no_file():
    """load_settings() returns DEFAULT_SETTINGS when no file exists."""
    result = load_settings()
    assert result == DEFAULT_SETTINGS


def test_load_returns_saved_values(tmp_path, monkeypatch):
    """load_settings() merges saved values over defaults."""
    # Write partial settings file
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    fake_path.write_text(json.dumps({"sort_order": "alpha"}))

    result = load_settings()

    # Overridden value
    assert result["sort_order"] == "alpha"
    # Default fallback for unset key
    assert result["default_session"] is None


def test_save_creates_file_and_dirs(tmp_path, monkeypatch):
    """save_settings() creates parent dirs and writes the file."""
    nested_path = tmp_path / "a" / "b" / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", nested_path)

    save_settings({"sort_order": "alpha"})

    assert nested_path.exists()


def test_save_merges_with_defaults(tmp_path, monkeypatch):
    """save_settings() merges data with defaults before writing."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    save_settings({"sort_order": "alpha"})

    written = json.loads(fake_path.read_text())
    # The override should be present
    assert written["sort_order"] == "alpha"
    # The default should also be present (merged)
    assert "default_session" in written
    assert "auto_open_created" in written


def test_load_handles_corrupt_json(tmp_path, monkeypatch):
    """load_settings() returns defaults gracefully on corrupt JSON."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    fake_path.write_text("NOT VALID JSON {{{{")

    result = load_settings()

    assert result == DEFAULT_SETTINGS


def test_patch_settings_merges_single_field(tmp_path, monkeypatch):
    """patch_settings() updates a single known field and returns result."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    result = patch_settings({"sort_order": "alpha"})

    assert result["sort_order"] == "alpha"
    # Other defaults preserved
    assert result["auto_open_created"] is True

    # Also verify persistence
    loaded = load_settings()
    assert loaded["sort_order"] == "alpha"


def test_patch_settings_ignores_unknown_keys(tmp_path, monkeypatch):
    """patch_settings() ignores keys not in DEFAULT_SETTINGS."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    result = patch_settings({"unknown_key": "should_be_ignored", "sort_order": "alpha"})

    assert "unknown_key" not in result
    assert result["sort_order"] == "alpha"

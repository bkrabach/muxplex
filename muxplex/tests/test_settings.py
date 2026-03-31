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


def test_load_returns_defaults_when_no_file(monkeypatch):
    """load_settings() returns DEFAULT_SETTINGS (with hostname fill-in) when no file exists."""
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: "test-host")
    result = load_settings()
    expected = {**DEFAULT_SETTINGS, "device_name": "test-host"}
    assert result == expected


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
    import socket

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    monkeypatch.setattr(socket, "gethostname", lambda: "test-host")
    fake_path.write_text("NOT VALID JSON {{{{")

    result = load_settings()

    expected = {**DEFAULT_SETTINGS, "device_name": "test-host"}
    assert result == expected


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


def test_load_does_not_mutate_default_settings():
    """Mutating the list returned by load_settings() must not corrupt DEFAULT_SETTINGS."""
    result = load_settings()
    # Mutate the returned hidden_sessions list
    result["hidden_sessions"].append("leaked_session")

    # DEFAULT_SETTINGS must be unchanged
    assert DEFAULT_SETTINGS["hidden_sessions"] == []

    # A second load must still return the clean default
    result2 = load_settings()
    assert result2["hidden_sessions"] == []


def test_load_propagates_non_json_errors(monkeypatch):
    """load_settings() must not swallow unexpected errors (e.g. PermissionError)."""
    from unittest.mock import MagicMock

    mock_path = MagicMock()
    mock_path.read_text.side_effect = PermissionError("no read permission")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", mock_path)

    with pytest.raises(PermissionError):
        load_settings()


# ---------------------------------------------------------------------------
# Federation fields tests (task-3-extend-settings-federation-fields)
# ---------------------------------------------------------------------------


def test_defaults_include_remote_instances():
    """DEFAULT_SETTINGS must have 'remote_instances' key initialised to []."""
    assert "remote_instances" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["remote_instances"] == []


def test_defaults_include_device_name():
    """DEFAULT_SETTINGS must have 'device_name' key initialised to empty string."""
    assert "device_name" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["device_name"] == ""


def test_load_returns_hostname_when_device_name_empty(monkeypatch):
    """load_settings() fills empty device_name with socket.gethostname()."""
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: "my-laptop")
    result = load_settings()
    assert result["device_name"] == "my-laptop"


def test_load_preserves_explicit_device_name(tmp_path, monkeypatch):
    """load_settings() keeps an explicitly saved device_name (does not overwrite with hostname)."""
    import socket

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    fake_path.write_text(json.dumps({"device_name": "Work PC"}))
    monkeypatch.setattr(socket, "gethostname", lambda: "should-not-appear")

    result = load_settings()
    assert result["device_name"] == "Work PC"


def test_remote_instances_round_trip(tmp_path, monkeypatch):
    """remote_instances survive a save/load cycle unchanged."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    instances = [
        {"url": "http://host1:7681", "name": "Host 1"},
        {"url": "http://host2:7681", "name": "Host 2"},
    ]
    save_settings({"remote_instances": instances})

    result = load_settings()
    assert result["remote_instances"] == instances


def test_device_name_round_trip(tmp_path, monkeypatch):
    """An explicit device_name survives a save/load cycle unchanged."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    save_settings({"device_name": "My Server"})

    result = load_settings()
    assert result["device_name"] == "My Server"


def test_load_does_not_mutate_default_remote_instances():
    """Mutating the list returned by load_settings() must not corrupt DEFAULT_SETTINGS."""
    result = load_settings()
    result["remote_instances"].append({"url": "http://leaked", "name": "leaked"})

    # DEFAULT_SETTINGS must be unchanged
    assert DEFAULT_SETTINGS["remote_instances"] == []

    # A second load must still return the clean default
    result2 = load_settings()
    assert result2["remote_instances"] == []


# ============================================================
# Delete session template (task: customizable delete command)
# ============================================================


def test_default_settings_include_delete_template():
    """DEFAULT_SETTINGS must include delete_session_template with default tmux kill-session value."""
    assert "delete_session_template" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'delete_session_template'"
    )
    assert (
        DEFAULT_SETTINGS["delete_session_template"] == "tmux kill-session -t {name}"
    ), (
        f"delete_session_template default must be 'tmux kill-session -t {{name}}', "
        f"got: {DEFAULT_SETTINGS['delete_session_template']!r}"
    )


def test_delete_session_template_returned_by_load_settings():
    """load_settings() must return delete_session_template with default value."""
    result = load_settings()
    assert "delete_session_template" in result, (
        "load_settings() must include 'delete_session_template'"
    )
    assert result["delete_session_template"] == "tmux kill-session -t {name}", (
        f"load_settings() delete_session_template must default to 'tmux kill-session -t {{name}}', "
        f"got: {result['delete_session_template']!r}"
    )


def test_delete_session_template_patchable():
    """patch_settings() must accept and persist delete_session_template."""
    custom = "amplifier-dev ~/dev/{name} --destroy"
    result = patch_settings({"delete_session_template": custom})
    assert result["delete_session_template"] == custom, (
        f"patch_settings() must accept delete_session_template, got: {result['delete_session_template']!r}"
    )
    # Verify it was persisted
    loaded = load_settings()
    assert loaded["delete_session_template"] == custom


# ============================================================
# Multi-device enabled flag (task: settings UI reorganization)
# ============================================================


def test_defaults_include_multi_device_enabled():
    """DEFAULT_SETTINGS must include 'multi_device_enabled' key initialised to False."""
    assert "multi_device_enabled" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'multi_device_enabled'"
    )
    assert DEFAULT_SETTINGS["multi_device_enabled"] is False, (
        f"multi_device_enabled default must be False, got: {DEFAULT_SETTINGS['multi_device_enabled']!r}"
    )


def test_load_returns_multi_device_enabled_default():
    """load_settings() must return multi_device_enabled with default value of False."""
    result = load_settings()
    assert "multi_device_enabled" in result, (
        "load_settings() must include 'multi_device_enabled'"
    )
    assert result["multi_device_enabled"] is False, (
        f"load_settings() multi_device_enabled must default to False, got: {result['multi_device_enabled']!r}"
    )


def test_multi_device_enabled_patchable():
    """patch_settings() must accept and persist multi_device_enabled."""
    result = patch_settings({"multi_device_enabled": True})
    assert result["multi_device_enabled"] is True, (
        f"patch_settings() must accept multi_device_enabled=True, got: {result['multi_device_enabled']!r}"
    )
    loaded = load_settings()
    assert loaded["multi_device_enabled"] is True

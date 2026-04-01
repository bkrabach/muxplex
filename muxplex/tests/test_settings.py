"""
Tests for muxplex/settings.py — server-side settings management.
7 acceptance-criteria tests defined here.
"""

import json
from pathlib import Path

import pytest

import muxplex.settings as settings_mod
from muxplex.settings import (
    DEFAULT_SETTINGS,
    load_federation_key,
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


# ============================================================
# Serve keys (task: add host, port, auth, session_ttl)
# ============================================================


def test_default_settings_include_serve_keys():
    """DEFAULT_SETTINGS must include host, port, auth, session_ttl with correct defaults."""
    assert "host" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'host'"
    assert DEFAULT_SETTINGS["host"] == "127.0.0.1", (
        f"host default must be '127.0.0.1', got: {DEFAULT_SETTINGS['host']!r}"
    )
    assert "port" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'port'"
    assert DEFAULT_SETTINGS["port"] == 8088, (
        f"port default must be 8088, got: {DEFAULT_SETTINGS['port']!r}"
    )
    assert "auth" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'auth'"
    assert DEFAULT_SETTINGS["auth"] == "pam", (
        f"auth default must be 'pam', got: {DEFAULT_SETTINGS['auth']!r}"
    )
    assert "session_ttl" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'session_ttl'"
    )
    assert DEFAULT_SETTINGS["session_ttl"] == 604800, (
        f"session_ttl default must be 604800, got: {DEFAULT_SETTINGS['session_ttl']!r}"
    )


def test_load_settings_returns_serve_keys_when_file_missing():
    """load_settings() returns serve keys with correct defaults when file is missing."""
    result = load_settings()
    assert result["host"] == "127.0.0.1", (
        f"load_settings() host must default to '127.0.0.1', got: {result['host']!r}"
    )
    assert result["port"] == 8088, (
        f"load_settings() port must default to 8088, got: {result['port']!r}"
    )
    assert result["auth"] == "pam", (
        f"load_settings() auth must default to 'pam', got: {result['auth']!r}"
    )
    assert result["session_ttl"] == 604800, (
        f"load_settings() session_ttl must default to 604800, got: {result['session_ttl']!r}"
    )


def test_serve_keys_patchable():
    """patch_settings() must accept and persist serve config keys."""
    result = patch_settings(
        {"host": "0.0.0.0", "port": 9000, "auth": "none", "session_ttl": 3600}
    )
    assert result["host"] == "0.0.0.0", (
        f"patch_settings() must accept host, got: {result['host']!r}"
    )
    assert result["port"] == 9000, (
        f"patch_settings() must accept port, got: {result['port']!r}"
    )
    assert result["auth"] == "none", (
        f"patch_settings() must accept auth, got: {result['auth']!r}"
    )
    assert result["session_ttl"] == 3600, (
        f"patch_settings() must accept session_ttl, got: {result['session_ttl']!r}"
    )
    # Verify persistence via load_settings()
    loaded = load_settings()
    assert loaded["host"] == "0.0.0.0"
    assert loaded["port"] == 9000
    assert loaded["auth"] == "none"
    assert loaded["session_ttl"] == 3600


def test_defaults_include_federation_key():
    """DEFAULT_SETTINGS must have 'federation_key' key initialised to empty string."""
    assert "federation_key" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["federation_key"] == ""


def test_old_settings_file_without_serve_keys_loads_correctly(redirect_settings_path):
    """Old settings.json without serve keys loads correctly with defaults filled in."""
    # Write an old-style settings file that has no serve keys
    old_settings = {
        "default_session": "my_session",
        "sort_order": "alpha",
        "hidden_sessions": [],
        "window_size_largest": False,
        "auto_open_created": True,
        "new_session_template": "tmux new-session -d -s {name}",
        "delete_session_template": "tmux kill-session -t {name}",
    }
    redirect_settings_path.write_text(json.dumps(old_settings))

    result = load_settings()

    # Old values are preserved
    assert result["default_session"] == "my_session"
    assert result["sort_order"] == "alpha"
    # New serve keys must be filled in with defaults
    assert result["host"] == "127.0.0.1", (
        f"host must default to '127.0.0.1' for old settings files, got: {result['host']!r}"
    )
    assert result["port"] == 8088, (
        f"port must default to 8088 for old settings files, got: {result['port']!r}"
    )
    assert result["auth"] == "pam", (
        f"auth must default to 'pam' for old settings files, got: {result['auth']!r}"
    )
    assert result["session_ttl"] == 604800, (
        f"session_ttl must default to 604800 for old settings files, got: {result['session_ttl']!r}"
    )


# ============================================================
# load_federation_key tests (task-2)
# ============================================================


def test_load_federation_key_returns_empty_when_no_file(tmp_path, monkeypatch):
    """load_federation_key() returns empty string when the key file does not exist."""
    missing_path = tmp_path / "no_such_federation_key"
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", missing_path)
    monkeypatch.delenv("MUXPLEX_FEDERATION_KEY_FILE", raising=False)

    result = load_federation_key()

    assert result == ""


def test_load_federation_key_reads_existing_file(tmp_path, monkeypatch):
    """load_federation_key() reads and strips the contents of the key file."""
    key_file = tmp_path / "federation_key"
    key_file.write_text("  my-secret-key\n  ")
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", key_file)
    monkeypatch.delenv("MUXPLEX_FEDERATION_KEY_FILE", raising=False)

    result = load_federation_key()

    assert result == "my-secret-key"


def test_load_federation_key_uses_default_path(tmp_path, monkeypatch):
    """load_federation_key() uses ~/.config/muxplex/federation_key when env var is not set."""
    from muxplex.settings import FEDERATION_KEY_PATH

    monkeypatch.delenv("MUXPLEX_FEDERATION_KEY_FILE", raising=False)
    assert FEDERATION_KEY_PATH == Path.home() / ".config" / "muxplex" / "federation_key"
    # Redirect the constant so the function uses a guaranteed-absent path in tmp_path
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", tmp_path / "absent_key")
    # Also verify the function runs without error (returns "" when file absent)
    result = load_federation_key()
    assert isinstance(result, str)
    assert result == ""


def test_load_federation_key_uses_env_var_override(tmp_path, monkeypatch):
    """load_federation_key() reads from MUXPLEX_FEDERATION_KEY_FILE when set."""
    key_file = tmp_path / "custom_key"
    key_file.write_text("env-override-key\n")
    monkeypatch.setenv("MUXPLEX_FEDERATION_KEY_FILE", str(key_file))

    result = load_federation_key()

    assert result == "env-override-key"

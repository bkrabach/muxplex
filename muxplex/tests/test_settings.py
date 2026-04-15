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
    SYNCABLE_KEYS,
    apply_synced_settings,
    get_syncable_settings,
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


# ============================================================
# remote_instances key field (task-6)
# ============================================================


def test_remote_instances_with_key_round_trip(tmp_path, monkeypatch):
    """remote_instances with key fields survive a save/load cycle unchanged."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    instances = [
        {"url": "http://host1:8088", "name": "Host 1", "key": "secret-key-1"},
        {"url": "http://host2:8088", "name": "Host 2", "key": "secret-key-2"},
    ]
    save_settings({"remote_instances": instances})
    result = load_settings()
    assert result["remote_instances"] == instances


# ============================================================
# Federation key preservation during PATCH (redaction bug fix)
# ============================================================


def test_patch_preserves_existing_key_when_patch_sends_empty_key():
    """patch_settings() must NOT wipe a remote instance key when the patch sends empty string.

    This is the core redaction bug: GET /api/settings returns key="" for remote
    instances (security redaction).  When the frontend PATCHes any other field it
    sends the redacted remote_instances array back, which should NOT overwrite the
    real keys stored on disk.
    """
    # Step 1: save settings with a real key present on disk
    save_settings(
        {
            "remote_instances": [
                {
                    "url": "http://spark-2:8088",
                    "name": "spark-2",
                    "key": "h0NtMnGt-real-key",
                },
            ]
        }
    )

    # Step 2: PATCH with the same remote but an empty key (simulating redacted GET response)
    patch_settings(
        {
            "device_name": "new-name",
            "remote_instances": [
                {"url": "http://spark-2:8088", "name": "spark-2", "key": ""},
            ],
        }
    )

    # Step 3: verify the real key was preserved
    loaded = load_settings()
    assert loaded["remote_instances"][0]["key"] == "h0NtMnGt-real-key", (
        "patch_settings() must preserve existing key when patch sends empty string; "
        f"got: {loaded['remote_instances'][0]['key']!r}"
    )
    # Other field changes should still be applied
    assert loaded["device_name"] == "new-name"


def test_patch_overwrites_key_when_patch_sends_non_empty_key():
    """patch_settings() must update a remote instance key when a new non-empty key is provided.

    Intentional key rotation must still work — sending a real new key should
    overwrite the old one.
    """
    # Save initial settings with an old key
    save_settings(
        {
            "remote_instances": [
                {"url": "http://spark-1:8088", "name": "spark-1", "key": "old-key-abc"},
            ]
        }
    )

    # PATCH with a brand-new non-empty key (intentional rotation)
    patch_settings(
        {
            "remote_instances": [
                {"url": "http://spark-1:8088", "name": "spark-1", "key": "new-key-xyz"},
            ]
        }
    )

    loaded = load_settings()
    assert loaded["remote_instances"][0]["key"] == "new-key-xyz", (
        "patch_settings() must accept new non-empty key; "
        f"got: {loaded['remote_instances'][0]['key']!r}"
    )


def test_patch_preserves_key_when_key_field_absent_from_patch():
    """patch_settings() must preserve a remote instance key when the patch omits the key field entirely."""
    save_settings(
        {
            "remote_instances": [
                {
                    "url": "http://tower:8088",
                    "name": "tower",
                    "key": "tower-secret-key",
                },
            ]
        }
    )

    # PATCH with the remote missing the 'key' field entirely
    patch_settings(
        {
            "remote_instances": [
                {"url": "http://tower:8088", "name": "tower"},
            ]
        }
    )

    loaded = load_settings()
    assert loaded["remote_instances"][0].get("key") == "tower-secret-key", (
        "patch_settings() must preserve existing key when patch omits key field; "
        f"got: {loaded['remote_instances'][0].get('key')!r}"
    )


def test_patch_preserves_keys_for_unchanged_remotes_in_multi_remote_list():
    """Keys for all existing remotes are preserved when one unrelated remote is updated."""
    save_settings(
        {
            "remote_instances": [
                {"url": "http://spark-1:8088", "name": "spark-1", "key": "key-spark-1"},
                {"url": "http://spark-2:8088", "name": "spark-2", "key": "key-spark-2"},
                {"url": "http://cortex:8088", "name": "cortex", "key": "key-cortex"},
            ]
        }
    )

    # Frontend sends back all three remotes with redacted empty keys, just changing a name
    patch_settings(
        {
            "remote_instances": [
                {"url": "http://spark-1:8088", "name": "spark-1-renamed", "key": ""},
                {"url": "http://spark-2:8088", "name": "spark-2", "key": ""},
                {"url": "http://cortex:8088", "name": "cortex", "key": ""},
            ]
        }
    )

    loaded = load_settings()
    remotes_by_url = {r["url"]: r for r in loaded["remote_instances"]}

    assert remotes_by_url["http://spark-1:8088"]["key"] == "key-spark-1", (
        f"spark-1 key must be preserved, got: {remotes_by_url['http://spark-1:8088']['key']!r}"
    )
    assert remotes_by_url["http://spark-2:8088"]["key"] == "key-spark-2", (
        f"spark-2 key must be preserved, got: {remotes_by_url['http://spark-2:8088']['key']!r}"
    )
    assert remotes_by_url["http://cortex:8088"]["key"] == "key-cortex", (
        f"cortex key must be preserved, got: {remotes_by_url['http://cortex:8088']['key']!r}"
    )
    # Non-key field change should still be applied
    assert remotes_by_url["http://spark-1:8088"]["name"] == "spark-1-renamed"


def test_patch_new_remote_with_key_is_saved():
    """A newly added remote instance with a key is saved correctly."""
    save_settings({"remote_instances": []})

    patch_settings(
        {
            "remote_instances": [
                {
                    "url": "http://new-host:8088",
                    "name": "new-host",
                    "key": "brand-new-key",
                },
            ]
        }
    )

    loaded = load_settings()
    assert len(loaded["remote_instances"]) == 1
    assert loaded["remote_instances"][0]["key"] == "brand-new-key", (
        f"New remote with key must be saved; got: {loaded['remote_instances'][0]['key']!r}"
    )


# ============================================================
# TLS settings keys (task-1-tls-settings-keys)
# ============================================================


def test_defaults_include_tls_cert():
    """DEFAULT_SETTINGS must have 'tls_cert' key initialised to empty string."""
    assert "tls_cert" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'tls_cert'"
    assert DEFAULT_SETTINGS["tls_cert"] == "", (
        f"tls_cert default must be '', got: {DEFAULT_SETTINGS['tls_cert']!r}"
    )


def test_defaults_include_tls_key():
    """DEFAULT_SETTINGS must have 'tls_key' key initialised to empty string."""
    assert "tls_key" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'tls_key'"
    assert DEFAULT_SETTINGS["tls_key"] == "", (
        f"tls_key default must be '', got: {DEFAULT_SETTINGS['tls_key']!r}"
    )


def test_load_returns_tls_keys_when_file_missing():
    """load_settings() returns tls_cert and tls_key with empty defaults when file is missing."""
    result = load_settings()
    assert "tls_cert" in result, "load_settings() must include 'tls_cert'"
    assert result["tls_cert"] == "", (
        f"load_settings() tls_cert must default to '', got: {result['tls_cert']!r}"
    )
    assert "tls_key" in result, "load_settings() must include 'tls_key'"
    assert result["tls_key"] == "", (
        f"load_settings() tls_key must default to '', got: {result['tls_key']!r}"
    )


def test_tls_keys_patchable():
    """patch_settings() must accept and persist tls_cert and tls_key."""
    result = patch_settings(
        {"tls_cert": "/etc/ssl/cert.pem", "tls_key": "/etc/ssl/key.pem"}
    )
    assert result["tls_cert"] == "/etc/ssl/cert.pem", (
        f"patch_settings() must accept tls_cert, got: {result['tls_cert']!r}"
    )
    assert result["tls_key"] == "/etc/ssl/key.pem", (
        f"patch_settings() must accept tls_key, got: {result['tls_key']!r}"
    )
    # Verify persistence via load_settings()
    loaded = load_settings()
    assert loaded["tls_cert"] == "/etc/ssl/cert.pem"
    assert loaded["tls_key"] == "/etc/ssl/key.pem"


def test_old_settings_file_without_tls_keys_loads_correctly(redirect_settings_path):
    """Old settings.json without TLS keys loads correctly with empty defaults filled in."""
    # Write an old-style settings file that has no TLS keys
    old_settings = {
        "default_session": "my_session",
        "sort_order": "alpha",
        "hidden_sessions": [],
        "window_size_largest": False,
        "auto_open_created": True,
        "new_session_template": "tmux new-session -d -s {name}",
        "delete_session_template": "tmux kill-session -t {name}",
        "host": "127.0.0.1",
        "port": 8088,
        "auth": "pam",
        "session_ttl": 604800,
        "federation_key": "",
    }
    redirect_settings_path.write_text(json.dumps(old_settings))

    result = load_settings()

    # Old values are preserved
    assert result["default_session"] == "my_session"
    assert result["sort_order"] == "alpha"
    # New TLS keys must be filled in with empty defaults
    assert result["tls_cert"] == "", (
        f"tls_cert must default to '' for old settings files, got: {result['tls_cert']!r}"
    )
    assert result["tls_key"] == "", (
        f"tls_key must default to '' for old settings files, got: {result['tls_key']!r}"
    )


# ============================================================
# Display settings keys (task-1-add-display-settings-to-defaults)
# ============================================================


def test_defaults_include_display_settings():
    """DEFAULT_SETTINGS must include all 10 display settings keys with correct defaults."""
    assert "fontSize" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'fontSize'"
    assert DEFAULT_SETTINGS["fontSize"] == 14, (
        f"fontSize default must be 14, got: {DEFAULT_SETTINGS['fontSize']!r}"
    )

    assert "hoverPreviewDelay" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'hoverPreviewDelay'"
    )
    assert DEFAULT_SETTINGS["hoverPreviewDelay"] == 1500, (
        f"hoverPreviewDelay default must be 1500, got: {DEFAULT_SETTINGS['hoverPreviewDelay']!r}"
    )

    assert "gridColumns" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'gridColumns'"
    )
    assert DEFAULT_SETTINGS["gridColumns"] == "auto", (
        f"gridColumns default must be 'auto', got: {DEFAULT_SETTINGS['gridColumns']!r}"
    )

    assert "bellSound" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'bellSound'"
    assert DEFAULT_SETTINGS["bellSound"] is False, (
        f"bellSound default must be False, got: {DEFAULT_SETTINGS['bellSound']!r}"
    )

    assert "viewMode" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'viewMode'"
    assert DEFAULT_SETTINGS["viewMode"] == "auto", (
        f"viewMode default must be 'auto', got: {DEFAULT_SETTINGS['viewMode']!r}"
    )

    assert "showDeviceBadges" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'showDeviceBadges'"
    )
    assert DEFAULT_SETTINGS["showDeviceBadges"] is True, (
        f"showDeviceBadges default must be True, got: {DEFAULT_SETTINGS['showDeviceBadges']!r}"
    )

    assert "showHoverPreview" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'showHoverPreview'"
    )
    assert DEFAULT_SETTINGS["showHoverPreview"] is True, (
        f"showHoverPreview default must be True, got: {DEFAULT_SETTINGS['showHoverPreview']!r}"
    )

    assert "activityIndicator" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'activityIndicator'"
    )
    assert DEFAULT_SETTINGS["activityIndicator"] == "both", (
        f"activityIndicator default must be 'both', got: {DEFAULT_SETTINGS['activityIndicator']!r}"
    )

    assert "gridViewMode" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'gridViewMode'"
    )
    assert DEFAULT_SETTINGS["gridViewMode"] == "flat", (
        f"gridViewMode default must be 'flat', got: {DEFAULT_SETTINGS['gridViewMode']!r}"
    )

    assert "sidebarOpen" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'sidebarOpen'"
    )
    assert DEFAULT_SETTINGS["sidebarOpen"] is None, (
        f"sidebarOpen default must be None, got: {DEFAULT_SETTINGS['sidebarOpen']!r}"
    )


def test_display_settings_round_trip_via_patch():
    """patch_settings() + load_settings() cycle must preserve custom display setting values."""
    custom_values = {
        "fontSize": 18,
        "hoverPreviewDelay": 800,
        "gridColumns": 3,
        "bellSound": True,
        "viewMode": "grid",
        "showDeviceBadges": False,
        "showHoverPreview": False,
        "activityIndicator": "icon",
        "gridViewMode": "grouped",
        "sidebarOpen": True,
    }

    result = patch_settings(custom_values)

    # Verify all custom values are returned by patch_settings
    for key, expected in custom_values.items():
        assert result[key] == expected, (
            f"patch_settings() must return custom {key}={expected!r}, got: {result[key]!r}"
        )

    # Verify all custom values survive a load_settings() round-trip
    loaded = load_settings()
    for key, expected in custom_values.items():
        assert loaded[key] == expected, (
            f"load_settings() must return persisted {key}={expected!r}, got: {loaded[key]!r}"
        )


# ============================================================
# SYNCABLE_KEYS allowlist and settings_updated_at (task-5)
# ============================================================


def test_syncable_keys_is_frozenset():
    """SYNCABLE_KEYS must be a frozenset."""
    assert isinstance(SYNCABLE_KEYS, frozenset)


def test_syncable_keys_contains_display_settings():
    """SYNCABLE_KEYS must include all display preference keys."""
    display_keys = {
        "fontSize",
        "hoverPreviewDelay",
        "gridColumns",
        "bellSound",
        "viewMode",
        "showDeviceBadges",
        "showHoverPreview",
        "activityIndicator",
        "gridViewMode",
        "sidebarOpen",
    }
    assert display_keys.issubset(SYNCABLE_KEYS)


def test_syncable_keys_contains_session_behavior():
    """SYNCABLE_KEYS must include session behavior keys."""
    session_keys = {
        "sort_order",
        "hidden_sessions",
        "default_session",
        "window_size_largest",
        "auto_open_created",
    }
    assert session_keys.issubset(SYNCABLE_KEYS)


def test_syncable_keys_excludes_infrastructure():
    """SYNCABLE_KEYS must NOT include infrastructure/identity keys."""
    infra_keys = {
        "host",
        "port",
        "auth",
        "session_ttl",
        "tls_cert",
        "tls_key",
        "device_name",
        "federation_key",
        "remote_instances",
        "multi_device_enabled",
        "new_session_template",
        "delete_session_template",
    }
    assert SYNCABLE_KEYS.isdisjoint(infra_keys)


def test_syncable_keys_excludes_settings_updated_at():
    """settings_updated_at is metadata, not a syncable key."""
    assert "settings_updated_at" not in SYNCABLE_KEYS


def test_syncable_keys_subset_of_default_settings():
    """Every SYNCABLE_KEY must exist in DEFAULT_SETTINGS."""
    assert SYNCABLE_KEYS.issubset(DEFAULT_SETTINGS.keys())


def test_defaults_include_settings_updated_at():
    """DEFAULT_SETTINGS must include settings_updated_at with default 0.0."""
    assert DEFAULT_SETTINGS["settings_updated_at"] == 0.0


# ============================================================
# Sync-aware patch_settings + apply_synced_settings (task-6)
# ============================================================


def test_patch_settings_bumps_timestamp_for_syncable_key():
    """patch_settings bumps settings_updated_at when a syncable key is patched."""
    patch_settings({"fontSize": 20})
    settings = load_settings()
    assert settings["settings_updated_at"] > 0.0


def test_patch_settings_does_not_bump_timestamp_for_nonsyncable_key():
    """patch_settings does NOT bump settings_updated_at for non-syncable keys."""
    patch_settings({"host": "0.0.0.0"})
    settings = load_settings()
    assert settings["settings_updated_at"] == 0.0


def test_apply_synced_settings_uses_incoming_timestamp():
    """apply_synced_settings sets settings_updated_at to the incoming timestamp."""
    apply_synced_settings({"fontSize": 18}, 1712600000.0)
    settings = load_settings()
    assert settings["fontSize"] == 18
    assert settings["settings_updated_at"] == 1712600000.0


def test_apply_synced_settings_ignores_nonsyncable_keys():
    """apply_synced_settings ignores keys not in SYNCABLE_KEYS."""
    apply_synced_settings({"fontSize": 18, "host": "evil.com"}, 1712600000.0)
    settings = load_settings()
    assert settings["fontSize"] == 18
    assert settings["host"] == "127.0.0.1"  # unchanged from default


def test_get_syncable_settings_returns_only_syncable_keys():
    """get_syncable_settings returns only SYNCABLE_KEYS + settings_updated_at."""
    result = get_syncable_settings()
    for key in result:
        assert key in SYNCABLE_KEYS or key == "settings_updated_at"
    assert "host" not in result
    assert "settings_updated_at" in result


def test_apply_synced_settings_does_not_use_time_now():
    """apply_synced_settings must use the incoming timestamp, not time.time()."""
    old_ts = 1000.0
    apply_synced_settings({"fontSize": 16}, old_ts)
    settings = load_settings()
    assert settings["settings_updated_at"] == old_ts  # exact match, not "close to now"


# ============================================================
# Views key in DEFAULT_SETTINGS and SYNCABLE_KEYS (task-3)
# ============================================================


def test_views_in_default_settings():
    """DEFAULT_SETTINGS must have 'views' key initialised to []."""
    assert "views" in DEFAULT_SETTINGS, "DEFAULT_SETTINGS must include 'views'"
    assert DEFAULT_SETTINGS["views"] == [], (
        f"views default must be [], got: {DEFAULT_SETTINGS['views']!r}"
    )


def test_views_in_syncable_keys():
    """SYNCABLE_KEYS must include 'views'."""
    assert "views" in SYNCABLE_KEYS, "SYNCABLE_KEYS must include 'views'"


def test_views_roundtrip_through_save_and_load():
    """views data with two views containing session arrays survives a save/load cycle."""
    views_data = [
        {"name": "Work", "sessions": ["session-a", "session-b"]},
        {"name": "Personal", "sessions": ["session-c"]},
    ]
    save_settings({"views": views_data})
    result = load_settings()
    assert result["views"] == views_data, (
        f"views must survive save/load roundtrip, got: {result['views']!r}"
    )


def test_patch_settings_syncs_views():
    """patch_settings with 'views' bumps settings_updated_at because views is in SYNCABLE_KEYS."""
    views_data = [{"name": "Dev", "sessions": ["dev-session"]}]
    patch_settings({"views": views_data})
    settings = load_settings()
    assert settings["settings_updated_at"] > 0, (
        "settings_updated_at must be > 0 after patching a syncable key (views)"
    )

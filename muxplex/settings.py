"""
Server-side settings management for muxplex.

Settings are stored at ~/.config/muxplex/settings.json.
"""

import copy
import json
import os
import socket
import time
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "muxplex" / "settings.json"
FEDERATION_KEY_PATH = Path.home() / ".config" / "muxplex" / "federation_key"

DEFAULT_SETTINGS: dict = {
    "host": "127.0.0.1",
    "port": 8088,
    "auth": "pam",
    "session_ttl": 604800,
    "default_session": None,
    "sort_order": "manual",
    "hidden_sessions": [],
    "views": [],
    "window_size_largest": False,
    "auto_open_created": True,
    "new_session_template": "tmux new-session -d -s {name}",
    "remote_instances": [],
    "device_name": "",
    "delete_session_template": "tmux kill-session -t {name}",
    "multi_device_enabled": False,
    "federation_key": "",
    "tls_cert": "",
    "tls_key": "",
    "fontSize": 14,
    "hoverPreviewDelay": 1500,
    "gridColumns": "auto",
    "bellSound": False,
    "viewMode": "auto",
    "showDeviceBadges": True,
    "showHoverPreview": True,
    "activityIndicator": "both",
    "gridViewMode": "flat",
    "sidebarOpen": None,
    "settings_updated_at": 0.0,
}

SYNCABLE_KEYS: frozenset[str] = frozenset(
    {
        # Display preferences
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
        # Session behavior
        "sort_order",
        "hidden_sessions",
        "views",
        "default_session",
        "window_size_largest",
        "auto_open_created",
    }
)


def load_settings() -> dict:
    """Load settings from disk, merging saved values over defaults.

    Returns DEFAULT_SETTINGS if the file does not exist or contains corrupt JSON.
    Unknown keys in the file are ignored.
    """
    result = copy.deepcopy(DEFAULT_SETTINGS)
    try:
        text = SETTINGS_PATH.read_text()
        data = json.loads(text)
        for key in DEFAULT_SETTINGS:
            if key in data:
                result[key] = data[key]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if not result["device_name"]:
        result["device_name"] = socket.gethostname()
    return result


def save_settings(data: dict) -> None:
    """Save settings to disk, merging *data* with defaults first.

    Creates parent directories as needed. Writes JSON with indent=2 and a
    trailing newline.
    """
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in data:
            merged[key] = data[key]
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2) + "\n")


def patch_settings(patch: dict) -> dict:
    """Merge known keys from *patch* into the current settings, save, and return result.

    Unknown keys in *patch* are silently ignored.

    Key-preservation rule: when ``remote_instances`` is included in the patch,
    any remote whose ``key`` field is empty or absent in the patch retains its
    existing key from the on-disk settings.  This prevents the redaction-wipe
    bug where ``GET /api/settings`` returns ``key=""`` for security reasons and
    a subsequent PATCH would silently overwrite real keys with empty strings.
    Only a patch that supplies a *non-empty* key value is treated as an
    intentional key rotation and actually written to disk.
    """
    current = load_settings()

    # Snapshot existing remote keys by URL *and* by position *before* applying
    # the patch so we can restore them if the patch contains redacted (empty)
    # key values.  The URL-based lookup handles the common case (name-only edits).
    # The position-based fallback handles URL edits (e.g. http -> https) where
    # the URL changes but the remote identity is the same.
    existing_remotes = current.get("remote_instances", [])
    existing_remote_keys_by_url: dict[str, str] = {
        r["url"]: r.get("key", "") for r in existing_remotes if r.get("url")
    }
    existing_remote_keys_by_index: list[str] = [
        r.get("key", "") for r in existing_remotes
    ]

    for key in DEFAULT_SETTINGS:
        if key in patch:
            current[key] = patch[key]

    # Restore keys that were stripped by redaction.
    if "remote_instances" in patch:
        for i, remote in enumerate(current["remote_instances"]):
            if remote.get("key"):
                # Non-empty key in the patch = intentional key rotation, keep it.
                continue
            url = remote.get("url", "")
            if url in existing_remote_keys_by_url:
                # URL unchanged -- restore by exact URL match.
                remote["key"] = existing_remote_keys_by_url[url]
            elif (
                i < len(existing_remote_keys_by_index)
                and existing_remote_keys_by_index[i]
            ):
                # URL changed (e.g. http -> https) but position is the same --
                # restore by index so editing a URL doesn't erase the key.
                remote["key"] = existing_remote_keys_by_index[i]

    if any(key in SYNCABLE_KEYS for key in patch if key in DEFAULT_SETTINGS):
        current["settings_updated_at"] = time.time()

    save_settings(current)
    return current


def apply_synced_settings(incoming_settings: dict, incoming_timestamp: float) -> dict:
    """Apply synced settings from a remote server.

    Only applies keys that are in SYNCABLE_KEYS. Sets settings_updated_at
    to the incoming timestamp (NOT time.time()) to prevent sync loops.

    After applying synced keys, enforces the mutual exclusion invariant:
    any session key that appears in both hidden_sessions and a view's sessions
    is removed from hidden_sessions (visibility wins over hiding).
    """
    # Lazy import: avoids potential circular import between settings and views
    from muxplex.views import enforce_mutual_exclusion

    current = load_settings()
    for key in SYNCABLE_KEYS:
        if key in incoming_settings:
            current[key] = incoming_settings[key]
    enforce_mutual_exclusion(current)
    current["settings_updated_at"] = incoming_timestamp
    save_settings(current)
    return current


def get_syncable_settings() -> dict:
    """Return only syncable settings + the settings_updated_at timestamp."""
    settings = load_settings()
    result = {key: settings[key] for key in SYNCABLE_KEYS if key in settings}
    result["settings_updated_at"] = settings.get("settings_updated_at", 0.0)
    return result


def load_federation_key() -> str:
    """Load the federation key from disk or env-overridden path.

    Reads from FEDERATION_KEY_PATH by default; override via
    MUXPLEX_FEDERATION_KEY_FILE env var. Returns empty string when
    the file does not exist.
    """
    env_path = os.environ.get("MUXPLEX_FEDERATION_KEY_FILE")
    path = Path(env_path) if env_path else FEDERATION_KEY_PATH
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""

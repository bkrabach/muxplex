"""
Server-side settings management for muxplex.

Settings are stored at ~/.config/muxplex/settings.json.
"""

import copy
import json
import socket
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "muxplex" / "settings.json"

DEFAULT_SETTINGS: dict = {
    "host": "127.0.0.1",
    "port": 8088,
    "auth": "pam",
    "session_ttl": 604800,
    "default_session": None,
    "sort_order": "manual",
    "hidden_sessions": [],
    "window_size_largest": False,
    "auto_open_created": True,
    "new_session_template": "tmux new-session -d -s {name}",
    "remote_instances": [],
    "device_name": "",
    "delete_session_template": "tmux kill-session -t {name}",
    "multi_device_enabled": False,
}


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
    """
    current = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in patch:
            current[key] = patch[key]
    save_settings(current)
    return current

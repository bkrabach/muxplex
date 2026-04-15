"""
Device Identity Module for muxplex.

Each muxplex instance gets a persistent device_id (UUID v4) stored in
~/.config/muxplex/identity.json (outside federation sync boundary).
"""

import json
import uuid
from pathlib import Path

IDENTITY_PATH = Path.home() / ".config" / "muxplex" / "identity.json"


def load_device_id() -> str:
    """Load the device_id from identity.json.

    If the file is absent, corrupt, or missing the device_id key, a new UUID v4
    is generated, written to the file (creating parent directories as needed),
    and returned.

    Returns:
        A UUID v4 string identifying this muxplex instance.
    """
    try:
        data = json.loads(IDENTITY_PATH.read_text())
        device_id = data["device_id"]
        return device_id
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return _generate_and_save()


def reset_device_id() -> str:
    """Generate a new device_id, write it to identity.json, and return it.

    Overwrites any existing device_id. Creates parent directories if needed.

    Returns:
        The newly generated UUID v4 string.
    """
    return _generate_and_save()


def _generate_and_save() -> str:
    """Generate a new UUID v4, persist it to IDENTITY_PATH, and return it."""
    device_id = str(uuid.uuid4())
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_PATH.write_text(json.dumps({"device_id": device_id}, indent=2) + "\n")
    return device_id

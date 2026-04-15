"""
Tests for muxplex/identity.py — Device Identity Module.

Each muxplex instance gets a persistent device_id (UUID v4) stored in
~/.config/muxplex/identity.json (outside federation sync boundary).
"""

import json
import uuid

from muxplex.identity import load_device_id, reset_device_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid_uuid4(value: str) -> bool:
    """Return True if value is a valid UUID v4 string."""
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# load_device_id tests
# ---------------------------------------------------------------------------


def test_load_creates_file_when_absent(tmp_path, monkeypatch):
    """load_device_id() creates identity.json when it does not exist."""
    identity_path = tmp_path / "identity.json"
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", identity_path)

    device_id = load_device_id()

    assert identity_path.exists(), "identity.json should be created"
    assert _is_valid_uuid4(device_id), f"Expected UUID v4, got: {device_id}"
    data = json.loads(identity_path.read_text())
    assert data["device_id"] == device_id


def test_load_returns_same_id_on_repeated_calls(tmp_path, monkeypatch):
    """load_device_id() returns the same ID on repeated calls (idempotent)."""
    identity_path = tmp_path / "identity.json"
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", identity_path)

    id_first = load_device_id()
    id_second = load_device_id()
    id_third = load_device_id()

    assert id_first == id_second == id_third, "Repeated calls must return the same ID"


def test_load_reads_existing_file(tmp_path, monkeypatch):
    """load_device_id() reads the existing device_id from identity.json."""
    identity_path = tmp_path / "identity.json"
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", identity_path)

    known_id = str(uuid.uuid4())
    identity_path.write_text(json.dumps({"device_id": known_id}))

    result = load_device_id()

    assert result == known_id, "Should return the pre-existing device_id"


def test_load_creates_parent_dirs(tmp_path, monkeypatch):
    """load_device_id() creates parent directories if they do not exist."""
    identity_path = tmp_path / "nested" / "dirs" / "identity.json"
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", identity_path)

    device_id = load_device_id()

    assert identity_path.exists(), "identity.json should be created in nested dirs"
    assert _is_valid_uuid4(device_id)


def test_load_regenerates_on_corrupt_json(tmp_path, monkeypatch):
    """load_device_id() regenerates device_id when identity.json contains corrupt JSON."""
    identity_path = tmp_path / "identity.json"
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", identity_path)

    identity_path.write_text("this is not valid json {{{")

    device_id = load_device_id()

    assert _is_valid_uuid4(device_id), (
        f"Expected UUID v4 after regeneration, got: {device_id}"
    )
    data = json.loads(identity_path.read_text())
    assert data["device_id"] == device_id, (
        "File should be rewritten with the new device_id"
    )


def test_load_regenerates_on_missing_key(tmp_path, monkeypatch):
    """load_device_id() regenerates device_id when identity.json lacks the device_id key."""
    identity_path = tmp_path / "identity.json"
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", identity_path)

    identity_path.write_text(json.dumps({"other_key": "some_value"}))

    device_id = load_device_id()

    assert _is_valid_uuid4(device_id), (
        f"Expected UUID v4 after regeneration, got: {device_id}"
    )
    data = json.loads(identity_path.read_text())
    assert data["device_id"] == device_id, (
        "File should be rewritten with the new device_id"
    )


# ---------------------------------------------------------------------------
# reset_device_id tests
# ---------------------------------------------------------------------------


def test_reset_generates_new_id(tmp_path, monkeypatch):
    """reset_device_id() generates a new device_id different from the existing one."""
    identity_path = tmp_path / "identity.json"
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", identity_path)

    # Establish an initial ID
    original_id = load_device_id()

    # Reset should produce a new, different ID
    new_id = reset_device_id()

    assert _is_valid_uuid4(new_id), f"Expected UUID v4 from reset, got: {new_id}"
    assert new_id != original_id, (
        "reset_device_id() should return a different ID than the original"
    )

    # Verify the file now contains the new ID
    data = json.loads(identity_path.read_text())
    assert data["device_id"] == new_id, "File should contain the newly reset device_id"


def test_reset_creates_parent_dirs(tmp_path, monkeypatch):
    """reset_device_id() creates parent directories if they do not exist."""
    identity_path = tmp_path / "no" / "parent" / "identity.json"
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", identity_path)

    new_id = reset_device_id()

    assert identity_path.exists(), "identity.json should be created in new directories"
    assert _is_valid_uuid4(new_id)
    data = json.loads(identity_path.read_text())
    assert data["device_id"] == new_id

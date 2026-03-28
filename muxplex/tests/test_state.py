"""
Tests for coordinator/state.py — state schema factories and I/O.
All acceptance-criteria tests are defined here.
"""

import asyncio
import time
from pathlib import Path

import pytest

from muxplex.state import (
    empty_bell,
    empty_device,
    empty_state,
    prune_devices,
    read_state,
    register_device,
    state_lock,
    write_state,
)


# ---------------------------------------------------------------------------
# autouse fixture — redirect STATE_DIR and STATE_PATH to a tmp directory
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def use_tmp_state_dir(tmp_path, monkeypatch):
    """Redirect state I/O to a fresh temp directory for every test."""
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)


# ---------------------------------------------------------------------------
# empty_state()
# ---------------------------------------------------------------------------


def test_empty_state_has_required_top_level_keys():
    state = empty_state()
    assert "active_session" in state
    assert "session_order" in state
    assert "sessions" in state
    assert "devices" in state


def test_empty_state_active_session_is_none():
    state = empty_state()
    assert state["active_session"] is None


def test_empty_state_session_order_is_empty_list():
    state = empty_state()
    assert state["session_order"] == []


def test_empty_state_sessions_is_empty_dict():
    state = empty_state()
    assert state["sessions"] == {}


def test_empty_state_devices_is_empty_dict():
    state = empty_state()
    assert state["devices"] == {}


def test_empty_state_returns_independent_dicts():
    """Mutating one state must not affect another."""
    s1 = empty_state()
    s2 = empty_state()

    s1["session_order"].append("my-session")
    s1["sessions"]["foo"] = {}
    s1["devices"]["bar"] = {}

    assert s2["session_order"] == []
    assert s2["sessions"] == {}
    assert s2["devices"] == {}


# ---------------------------------------------------------------------------
# empty_bell()
# ---------------------------------------------------------------------------


def test_empty_bell_has_required_keys():
    bell = empty_bell()
    assert "last_fired_at" in bell
    assert "seen_at" in bell
    assert "unseen_count" in bell


def test_empty_bell_last_fired_at_is_none():
    bell = empty_bell()
    assert bell["last_fired_at"] is None


def test_empty_bell_seen_at_is_none():
    bell = empty_bell()
    assert bell["seen_at"] is None


def test_empty_bell_unseen_count_is_zero():
    bell = empty_bell()
    assert bell["unseen_count"] == 0


# ---------------------------------------------------------------------------
# empty_device(device_id, label)
# ---------------------------------------------------------------------------


def test_empty_device_has_required_keys():
    device = empty_device("dev-1", "My Laptop")
    assert "label" in device
    assert "viewing_session" in device
    assert "view_mode" in device
    assert "last_interaction_at" in device
    assert "last_heartbeat_at" in device


def test_empty_device_label_set_correctly():
    device = empty_device("dev-1", "My Laptop")
    assert device["label"] == "My Laptop"


def test_empty_device_viewing_session_is_none():
    device = empty_device("dev-1", "My Laptop")
    assert device["viewing_session"] is None


def test_empty_device_view_mode_is_grid():
    device = empty_device("dev-1", "My Laptop")
    assert device["view_mode"] == "grid"


def test_empty_device_timestamps_are_recent():
    """last_interaction_at and last_heartbeat_at should be close to now."""
    before = time.time()
    device = empty_device("dev-1", "My Laptop")
    after = time.time()

    assert before <= device["last_interaction_at"] <= after
    assert before <= device["last_heartbeat_at"] <= after


# ---------------------------------------------------------------------------
# state_lock
# ---------------------------------------------------------------------------


def test_state_lock_is_asyncio_lock():
    assert isinstance(state_lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# load_state / save_state / read_state / write_state
# ---------------------------------------------------------------------------


async def test_read_state_returns_empty_when_no_file():
    """read_state() returns empty_state() when STATE_PATH does not exist."""
    state = await read_state()
    assert state == empty_state()


async def test_write_then_read_roundtrip():
    """write_state() persists state; subsequent read_state() returns it intact."""
    original = empty_state()
    original["active_session"] = "my-session"
    original["session_order"] = ["my-session"]
    await write_state(original)
    loaded = await read_state()
    assert loaded == original


async def test_write_creates_state_dir_if_missing():
    """save_state() must create STATE_DIR (and parents) if they do not exist."""
    import muxplex.state as state_mod

    # The tmp "state" subdirectory should not exist yet.
    assert not state_mod.STATE_DIR.exists()
    await write_state(empty_state())
    assert state_mod.STATE_DIR.exists()


async def test_write_is_atomic_no_tmp_file_left():
    """After write_state(), no .tmp file should remain on disk."""
    import muxplex.state as state_mod

    await write_state(empty_state())
    tmp_file = Path(str(state_mod.STATE_PATH) + ".tmp")
    assert not tmp_file.exists()


async def test_concurrent_writes_do_not_corrupt():
    """Two concurrent write_state() calls must leave valid JSON matching one write."""
    state_a = empty_state()
    state_a["active_session"] = "session-a"

    state_b = empty_state()
    state_b["active_session"] = "session-b"

    await asyncio.gather(
        write_state(state_a),
        write_state(state_b),
    )

    final = await read_state()
    # Final state must be exactly one of the two known states — no corruption.
    assert final == state_a or final == state_b


# ---------------------------------------------------------------------------
# register_device()
# ---------------------------------------------------------------------------


def test_register_device_adds_new_device():
    """register_device() creates a new device entry if device_id is not present."""
    state = empty_state()
    register_device(state, "dev-1", "My Laptop", None, "grid", time.time())
    assert "dev-1" in state["devices"]


def test_register_device_updates_existing_device():
    """register_device() updates label, viewing_session, view_mode, last_interaction_at."""
    state = empty_state()
    now = time.time()
    register_device(state, "dev-1", "Old Label", "session-a", "grid", now)

    later = now + 10
    register_device(state, "dev-1", "New Label", "session-b", "fullscreen", later)

    device = state["devices"]["dev-1"]
    assert device["label"] == "New Label"
    assert device["viewing_session"] == "session-b"
    assert device["view_mode"] == "fullscreen"
    assert device["last_interaction_at"] == later


def test_register_device_sets_heartbeat_timestamp():
    """register_device() always refreshes last_heartbeat_at to current time.time()."""
    state = empty_state()
    before = time.time()
    register_device(state, "dev-1", "My Laptop", None, "grid", before)
    after = time.time()

    device = state["devices"]["dev-1"]
    assert before <= device["last_heartbeat_at"] <= after


# ---------------------------------------------------------------------------
# prune_devices()
# ---------------------------------------------------------------------------


def test_prune_devices_removes_stale():
    """prune_devices() removes devices whose last_heartbeat_at is older than ttl."""
    state = empty_state()
    old_time = time.time() - 400  # 400s ago, beyond default 300s TTL
    state["devices"]["stale-dev"] = {
        "label": "Stale",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": old_time,
        "last_heartbeat_at": old_time,
    }
    prune_devices(state)
    assert "stale-dev" not in state["devices"]


def test_prune_devices_keeps_fresh():
    """prune_devices() keeps devices whose last_heartbeat_at is within ttl."""
    state = empty_state()
    recent_time = time.time() - 100  # 100s ago, within default 300s TTL
    state["devices"]["fresh-dev"] = {
        "label": "Fresh",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": recent_time,
        "last_heartbeat_at": recent_time,
    }
    prune_devices(state)
    assert "fresh-dev" in state["devices"]


def test_prune_devices_returns_list_of_removed_ids():
    """prune_devices() returns the list of device IDs that were removed."""
    state = empty_state()
    old_time = time.time() - 400
    state["devices"]["stale-1"] = {
        "label": "Stale 1",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": old_time,
        "last_heartbeat_at": old_time,
    }
    state["devices"]["stale-2"] = {
        "label": "Stale 2",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": old_time,
        "last_heartbeat_at": old_time,
    }
    removed = prune_devices(state)
    assert sorted(removed) == ["stale-1", "stale-2"]

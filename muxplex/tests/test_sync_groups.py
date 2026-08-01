"""
Tests for muxplex/state.py — sync group schema, resolution, and lifecycle.

Covers the §10.1 test plan from the sync-groups spec: group factories,
resolve/read/write dispatch, the "no mirroring" invariant, GC, and
session-vanish cleanup across every group.
"""

import time

import pytest

from muxplex.state import (
    GLOBAL_GROUP,
    clear_missing_active_sessions,
    device_group_id,
    empty_state,
    ensure_group,
    gc_sync_groups,
    normalize_state,
    read_group_state,
    register_device,
    resolve_group,
    write_group_state,
)


@pytest.fixture(autouse=True)
def use_tmp_state_dir(tmp_path, monkeypatch):
    """Redirect state I/O to a fresh temp directory for every test."""
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)


# ---------------------------------------------------------------------------
# 1-2: factories
# ---------------------------------------------------------------------------


def test_empty_state_has_sync_group_schema_keys():
    state = empty_state()
    assert state["sync_groups"] == {}
    assert state["terminal_session"] is None
    assert state["terminal_group"] == GLOBAL_GROUP


def test_empty_device_has_sync_group_global():
    from muxplex.state import empty_device

    device = empty_device("dev-1", "My Laptop")
    assert device["sync_group"] == GLOBAL_GROUP


# ---------------------------------------------------------------------------
# 3-4: normalize_state()
# ---------------------------------------------------------------------------


def test_normalize_state_fills_legacy_dict():
    legacy = {
        "active_session": "sessX",
        "active_remote_id": None,
        "active_view": "all",
        "session_order": [],
        "sessions": {},
        "devices": {
            "d1": {
                "label": "x",
                "viewing_session": None,
                "view_mode": "grid",
                "last_interaction_at": 0.0,
                "last_heartbeat_at": 0.0,
            }
        },
    }
    result = normalize_state(legacy)
    assert result["sync_groups"] == {}
    assert result["devices"]["d1"]["sync_group"] == GLOBAL_GROUP
    assert result["terminal_group"] == GLOBAL_GROUP
    # terminal_session restates the pre-groups invariant: ttyd was always
    # attached to whatever active_session held.
    assert result["terminal_session"] == "sessX"


def test_normalize_state_raises_on_global_in_sync_groups():
    state = empty_state()
    state["sync_groups"][GLOBAL_GROUP] = {
        "active_session": None,
        "active_remote_id": None,
        "active_view": "all",
    }
    with pytest.raises(ValueError):
        normalize_state(state)


def test_normalize_state_preserves_existing_terminal_session():
    """terminal_session already present is left untouched, even if it differs
    from active_session (post-groups states are not migrated again)."""
    state = empty_state()
    state["active_session"] = "sessX"
    state["terminal_session"] = "sessY"
    del state["terminal_group"]
    result = normalize_state(state)
    assert result["terminal_session"] == "sessY"
    assert result["terminal_group"] == GLOBAL_GROUP


# ---------------------------------------------------------------------------
# 5-8: read_group_state / write_group_state
# ---------------------------------------------------------------------------


def test_read_group_state_global_returns_top_level_copy():
    state = empty_state()
    state["active_session"] = "sessX"
    result = read_group_state(state, GLOBAL_GROUP)
    assert result == {
        "active_session": "sessX",
        "active_remote_id": None,
        "active_view": "all",
    }
    # mutating the result must not mutate state
    result["active_session"] = "other"
    assert state["active_session"] == "sessX"


def test_write_group_state_global_writes_top_level_no_mirror():
    state = empty_state()
    write_group_state(state, GLOBAL_GROUP, {"active_session": "sessX"})
    assert state["active_session"] == "sessX"
    assert GLOBAL_GROUP not in state["sync_groups"]


def test_write_group_state_rejects_unknown_field():
    state = empty_state()
    with pytest.raises(ValueError):
        write_group_state(state, GLOBAL_GROUP, {"bogus_field": 1})


def test_read_write_group_state_unknown_non_global_raises_keyerror():
    state = empty_state()
    with pytest.raises(KeyError):
        read_group_state(state, "device:unknown")
    with pytest.raises(KeyError):
        write_group_state(state, "device:unknown", {"active_session": "x"})


def test_write_group_state_non_global_writes_sync_groups_slot():
    state = empty_state()
    group = device_group_id("d1")
    ensure_group(state, group)
    write_group_state(state, group, {"active_view": "hidden"})
    assert state["sync_groups"][group]["active_view"] == "hidden"
    assert state["active_view"] == "all"  # top-level untouched


# ---------------------------------------------------------------------------
# 9: resolve_group()
# ---------------------------------------------------------------------------


def test_resolve_group_none_is_global():
    state = empty_state()
    assert resolve_group(state, None) == GLOBAL_GROUP


def test_resolve_group_unknown_device_raises_keyerror():
    state = empty_state()
    with pytest.raises(KeyError):
        resolve_group(state, "unknown-device")


def test_resolve_group_known_device_returns_its_group():
    state = empty_state()
    register_device(
        state,
        "d1",
        "Laptop",
        None,
        "grid",
        time.time(),
        sync_group=device_group_id("d1"),
    )
    assert resolve_group(state, "d1") == device_group_id("d1")


# ---------------------------------------------------------------------------
# 10: ensure_group() seeds from global
# ---------------------------------------------------------------------------


def test_ensure_group_seeds_from_current_global_values():
    state = empty_state()
    state["active_session"] = "sessX"
    state["active_view"] = "hidden"
    group = device_group_id("d1")
    created = ensure_group(state, group)
    assert created is True
    assert state["sync_groups"][group] == {
        "active_session": "sessX",
        "active_remote_id": None,
        "active_view": "hidden",
    }


def test_ensure_group_noop_for_global():
    state = empty_state()
    assert ensure_group(state, GLOBAL_GROUP) is False
    assert GLOBAL_GROUP not in state["sync_groups"]


def test_ensure_group_noop_if_already_exists():
    state = empty_state()
    group = device_group_id("d1")
    ensure_group(state, group)
    state["sync_groups"][group]["active_view"] = "custom"
    assert ensure_group(state, group) is False
    assert state["sync_groups"][group]["active_view"] == "custom"


# ---------------------------------------------------------------------------
# 11: gc_sync_groups()
# ---------------------------------------------------------------------------


def test_gc_sync_groups_removes_unclaimed_only():
    state = empty_state()
    register_device(
        state, "d1", "L1", None, "grid", time.time(), sync_group=device_group_id("d1")
    )
    register_device(
        state, "d2", "L2", None, "grid", time.time(), sync_group=device_group_id("d2")
    )
    # Manually create an orphaned group nobody claims
    state["sync_groups"]["device:ghost"] = {
        "active_session": None,
        "active_remote_id": None,
        "active_view": "all",
    }

    removed = gc_sync_groups(state)

    assert removed == ["device:ghost"]
    assert device_group_id("d1") in state["sync_groups"]
    assert device_group_id("d2") in state["sync_groups"]


def test_gc_sync_groups_removes_group_after_device_removed():
    state = empty_state()
    register_device(
        state, "d1", "L1", None, "grid", time.time(), sync_group=device_group_id("d1")
    )
    del state["devices"]["d1"]
    removed = gc_sync_groups(state)
    assert removed == [device_group_id("d1")]
    assert state["sync_groups"] == {}


# ---------------------------------------------------------------------------
# 12: clear_missing_active_sessions()
# ---------------------------------------------------------------------------


def test_clear_missing_active_sessions_clears_every_group():
    state = empty_state()
    state["active_session"] = "gone"
    state["active_remote_id"] = "remote-1"
    group = device_group_id("d1")
    ensure_group(state, group)
    state["sync_groups"][group]["active_session"] = "gone"
    state["sync_groups"][group]["active_remote_id"] = "remote-2"

    cleared = clear_missing_active_sessions(state, live={"still-here"})

    assert sorted(cleared) == sorted([GLOBAL_GROUP, group])
    assert state["active_session"] is None
    assert state["sync_groups"][group]["active_session"] is None
    # active_remote_id is deliberately untouched
    assert state["active_remote_id"] == "remote-1"
    assert state["sync_groups"][group]["active_remote_id"] == "remote-2"


def test_clear_missing_active_sessions_leaves_live_sessions():
    state = empty_state()
    state["active_session"] = "still-here"
    cleared = clear_missing_active_sessions(state, live={"still-here"})
    assert cleared == []
    assert state["active_session"] == "still-here"


# ---------------------------------------------------------------------------
# 13: register_device(sync_group=...)
# ---------------------------------------------------------------------------


def test_register_device_sync_group_none_leaves_existing_unchanged():
    state = empty_state()
    group = device_group_id("d1")
    register_device(state, "d1", "L1", None, "grid", time.time(), sync_group=group)
    register_device(state, "d1", "L1", None, "grid", time.time(), sync_group=None)
    assert state["devices"]["d1"]["sync_group"] == group


def test_register_device_sync_group_none_new_device_gets_global():
    state = empty_state()
    register_device(state, "d1", "L1", None, "grid", time.time(), sync_group=None)
    assert state["devices"]["d1"]["sync_group"] == GLOBAL_GROUP


def test_register_device_sync_group_creates_group():
    state = empty_state()
    group = device_group_id("d1")
    register_device(state, "d1", "L1", None, "grid", time.time(), sync_group=group)
    assert group in state["sync_groups"]
    assert state["devices"]["d1"]["sync_group"] == group

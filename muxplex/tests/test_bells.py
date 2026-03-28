"""
Tests for coordinator/bells.py — bell flag polling and unseen_count tracking.
All 17 acceptance-criteria tests are defined here.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from muxplex.bells import (
    _bell_seen,
    apply_bell_clear_rule,
    poll_bell_flag,
    process_bell_flags,
    should_clear_bell,
)
from muxplex.state import empty_bell, empty_state


# ---------------------------------------------------------------------------
# autouse fixture — clear _bell_seen before/after each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_bell_seen():
    """Clear _bell_seen before and after each test for isolation."""
    _bell_seen.clear()
    yield
    _bell_seen.clear()


# ---------------------------------------------------------------------------
# poll_bell_flag tests
# ---------------------------------------------------------------------------


async def test_poll_bell_flag_returns_true_when_flag_is_1():
    """poll_bell_flag returns True when tmux reports window_bell_flag=1."""
    with patch("muxplex.bells.run_tmux", new=AsyncMock(return_value="1\n")):
        result = await poll_bell_flag("my-session")
    assert result is True


async def test_poll_bell_flag_returns_false_when_flag_is_0():
    """poll_bell_flag returns False when tmux reports window_bell_flag=0."""
    with patch("muxplex.bells.run_tmux", new=AsyncMock(return_value="0\n")):
        result = await poll_bell_flag("my-session")
    assert result is False


async def test_poll_bell_flag_returns_false_on_error():
    """poll_bell_flag returns False when run_tmux raises RuntimeError."""
    with patch(
        "muxplex.bells.run_tmux",
        new=AsyncMock(side_effect=RuntimeError("session not found")),
    ):
        result = await poll_bell_flag("my-session")
    assert result is False


# ---------------------------------------------------------------------------
# process_bell_flags tests
# ---------------------------------------------------------------------------


async def test_process_bell_flags_increments_unseen_count_on_new_bell():
    """process_bell_flags increments unseen_count on a 0→1 transition."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}

    with patch("muxplex.bells.poll_bell_flag", new=AsyncMock(return_value=True)):
        changed = await process_bell_flags(["session-a"], state)

    assert changed is True
    assert state["sessions"]["session-a"]["bell"]["unseen_count"] == 1
    assert state["sessions"]["session-a"]["bell"]["last_fired_at"] is not None


async def test_process_bell_flags_does_not_double_count_persistent_flag():
    """process_bell_flags does not increment unseen_count if flag stays at 1."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}

    with patch("muxplex.bells.poll_bell_flag", new=AsyncMock(return_value=True)):
        # First poll — 0→1 transition
        await process_bell_flags(["session-a"], state)
        # Second poll — 1→1 (persistent), should NOT increment again
        changed = await process_bell_flags(["session-a"], state)

    assert changed is False
    assert state["sessions"]["session-a"]["bell"]["unseen_count"] == 1


async def test_process_bell_flags_resets_tracking_when_flag_clears():
    """1→0→1 sequence counts as two separate bells."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}

    # side_effect drives three sequential calls: 0→1, 1→0, 0→1
    with patch(
        "muxplex.bells.poll_bell_flag",
        new=AsyncMock(side_effect=[True, False, True]),
    ):
        for _ in range(3):
            await process_bell_flags(["session-a"], state)

    assert state["sessions"]["session-a"]["bell"]["unseen_count"] == 2


async def test_process_bell_flags_no_change_returns_false():
    """process_bell_flags returns False when no bell state changed."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}

    with patch("muxplex.bells.poll_bell_flag", new=AsyncMock(return_value=False)):
        changed = await process_bell_flags(["session-a"], state)

    assert changed is False
    assert state["sessions"]["session-a"]["bell"]["unseen_count"] == 0


async def test_process_bell_flags_creates_bell_entry_if_missing():
    """process_bell_flags creates the bell sub-dict if session has no bell key."""
    state = empty_state()
    state["sessions"]["session-a"] = {}  # no 'bell' key

    with patch("muxplex.bells.poll_bell_flag", new=AsyncMock(return_value=False)):
        await process_bell_flags(["session-a"], state)

    assert "bell" in state["sessions"]["session-a"]
    assert state["sessions"]["session-a"]["bell"]["unseen_count"] == 0


# ---------------------------------------------------------------------------
# should_clear_bell tests
# ---------------------------------------------------------------------------


def test_should_clear_bell_returns_true_for_fullscreen_recent_interaction():
    """should_clear_bell returns True when a device is fullscreen and interacted recently."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}
    state["devices"]["device-1"] = {
        "label": "Device 1",
        "viewing_session": "session-a",
        "view_mode": "fullscreen",
        "last_interaction_at": time.time() - 10.0,  # 10 seconds ago
        "last_heartbeat_at": time.time(),
    }

    assert should_clear_bell("session-a", state) is True


def test_should_clear_bell_returns_false_for_grid_mode():
    """should_clear_bell returns False when device is in grid mode."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}
    state["devices"]["device-1"] = {
        "label": "Device 1",
        "viewing_session": "session-a",
        "view_mode": "grid",
        "last_interaction_at": time.time() - 10.0,  # recent interaction
        "last_heartbeat_at": time.time(),
    }

    assert should_clear_bell("session-a", state) is False


def test_should_clear_bell_returns_false_when_interaction_too_old():
    """should_clear_bell returns False when last interaction was more than 60s ago."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}
    state["devices"]["device-1"] = {
        "label": "Device 1",
        "viewing_session": "session-a",
        "view_mode": "fullscreen",
        "last_interaction_at": time.time() - 90.0,  # 90 seconds ago (> 60s window)
        "last_heartbeat_at": time.time(),
    }

    assert should_clear_bell("session-a", state) is False


def test_should_clear_bell_returns_false_when_device_viewing_different_session():
    """should_clear_bell returns False when device is viewing a different session."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}
    state["devices"]["device-1"] = {
        "label": "Device 1",
        "viewing_session": "session-b",  # different session
        "view_mode": "fullscreen",
        "last_interaction_at": time.time() - 10.0,
        "last_heartbeat_at": time.time(),
    }

    assert should_clear_bell("session-a", state) is False


def test_should_clear_bell_returns_false_when_no_devices():
    """should_clear_bell returns False when there are no connected devices."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}
    # No devices in state["devices"]

    assert should_clear_bell("session-a", state) is False


# ---------------------------------------------------------------------------
# apply_bell_clear_rule tests
# ---------------------------------------------------------------------------


def test_apply_bell_clear_rule_clears_matching_sessions():
    """apply_bell_clear_rule resets unseen_count to 0 and sets seen_at for qualifying sessions."""
    state = empty_state()
    state["sessions"]["session-a"] = {
        "bell": {
            "unseen_count": 3,
            "last_fired_at": time.time() - 30.0,
            "seen_at": None,
        }
    }
    state["devices"]["device-1"] = {
        "label": "Device 1",
        "viewing_session": "session-a",
        "view_mode": "fullscreen",
        "last_interaction_at": time.time() - 10.0,
        "last_heartbeat_at": time.time(),
    }

    before = time.time()
    apply_bell_clear_rule(state)
    after = time.time()

    bell = state["sessions"]["session-a"]["bell"]
    assert bell["unseen_count"] == 0
    assert bell["seen_at"] is not None
    assert before <= bell["seen_at"] <= after


def test_apply_bell_clear_rule_skips_sessions_with_zero_unseen():
    """apply_bell_clear_rule does not modify sessions that already have unseen_count == 0."""
    state = empty_state()
    state["sessions"]["session-a"] = {
        "bell": {
            "unseen_count": 0,
            "last_fired_at": None,
            "seen_at": None,
        }
    }
    state["devices"]["device-1"] = {
        "label": "Device 1",
        "viewing_session": "session-a",
        "view_mode": "fullscreen",
        "last_interaction_at": time.time() - 10.0,
        "last_heartbeat_at": time.time(),
    }

    result = apply_bell_clear_rule(state)

    assert result == []
    assert state["sessions"]["session-a"]["bell"]["seen_at"] is None


def test_apply_bell_clear_rule_returns_list_of_cleared_session_names():
    """apply_bell_clear_rule returns the names of sessions that were cleared."""
    state = empty_state()
    state["sessions"]["session-a"] = {
        "bell": {"unseen_count": 2, "last_fired_at": time.time() - 5.0, "seen_at": None}
    }
    state["sessions"]["session-b"] = {
        "bell": {"unseen_count": 1, "last_fired_at": time.time() - 5.0, "seen_at": None}
    }
    state["sessions"]["session-c"] = {
        "bell": {"unseen_count": 0, "last_fired_at": None, "seen_at": None}
    }
    state["devices"]["device-1"] = {
        "label": "Device 1",
        "viewing_session": "session-a",
        "view_mode": "fullscreen",
        "last_interaction_at": time.time() - 10.0,
        "last_heartbeat_at": time.time(),
    }
    state["devices"]["device-2"] = {
        "label": "Device 2",
        "viewing_session": "session-b",
        "view_mode": "fullscreen",
        "last_interaction_at": time.time() - 10.0,
        "last_heartbeat_at": time.time(),
    }

    result = apply_bell_clear_rule(state)

    assert sorted(result) == ["session-a", "session-b"]


def test_apply_bell_clear_rule_resets_bell_seen_tracking():
    """apply_bell_clear_rule resets _bell_seen[name] = False for cleared sessions."""
    state = empty_state()
    state["sessions"]["session-a"] = {
        "bell": {"unseen_count": 1, "last_fired_at": time.time() - 5.0, "seen_at": None}
    }
    state["devices"]["device-1"] = {
        "label": "Device 1",
        "viewing_session": "session-a",
        "view_mode": "fullscreen",
        "last_interaction_at": time.time() - 10.0,
        "last_heartbeat_at": time.time(),
    }

    # Pre-seed _bell_seen as if the bell was previously seen
    _bell_seen["session-a"] = True

    apply_bell_clear_rule(state)

    assert _bell_seen.get("session-a") is False

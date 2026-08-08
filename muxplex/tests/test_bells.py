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
    needs_attention,
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
    """poll_bell_flag returns True when tmux reports window_bell_flag=1
    for a single-window session."""
    with patch("tmux_kit.bell.run_tmux", new=AsyncMock(return_value="1\n")):
        result = await poll_bell_flag("my-session")
    assert result is True


async def test_poll_bell_flag_returns_false_when_flag_is_0():
    """poll_bell_flag returns False when tmux reports window_bell_flag=0."""
    with patch("tmux_kit.bell.run_tmux", new=AsyncMock(return_value="0\n")):
        result = await poll_bell_flag("my-session")
    assert result is False


async def test_poll_bell_flag_returns_false_on_error():
    """poll_bell_flag returns False when run_tmux raises RuntimeError."""
    with patch(
        "tmux_kit.bell.run_tmux",
        new=AsyncMock(side_effect=RuntimeError("session not found")),
    ):
        result = await poll_bell_flag("my-session")
    assert result is False


async def test_poll_bell_flag_uses_list_windows_not_display_message():
    """Regression test for the window-scoping bug: poll_bell_flag must call
    `tmux list-windows -t <session> -F #{window_bell_flag}` (every window),
    NOT `display-message -t <session> -p ...` (only the session's current/
    active window -- verified against real tmux to silently miss a bell in
    any other window).
    """
    mock_run_tmux = AsyncMock(return_value="0\n0\n")
    with patch("tmux_kit.bell.run_tmux", mock_run_tmux):
        await poll_bell_flag("my-session")

    mock_run_tmux.assert_awaited_once_with(
        "list-windows", "-t", "my-session", "-F", "#{window_bell_flag}"
    )


async def test_poll_bell_flag_true_when_any_background_window_flag_set():
    """Regression test for the window-scoping bug (verified against real
    tmux): a bell in a BACKGROUND (non-active) window must still be
    detected. The old `display-message -t <session>` implementation only
    ever read the session's currently-active window, so a background-window
    bell -- confirmed live to set that window's own flag to '1' while the
    active window's flag stayed '0' -- went completely undetected. Multiple
    windows' flags are returned one per line by `list-windows`; ANY '1'
    among them must count.
    """
    # window 1 (active) = '0', window 2 (background, where the bell fired) = '1'
    with patch("tmux_kit.bell.run_tmux", new=AsyncMock(return_value="0\n1\n")):
        result = await poll_bell_flag("my-session")
    assert result is True


async def test_poll_bell_flag_false_when_all_windows_clear():
    """No window in the session has its bell flag set -> False."""
    with patch("tmux_kit.bell.run_tmux", new=AsyncMock(return_value="0\n0\n0\n")):
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


async def test_process_bell_flags_stamps_source_poll_on_new_bell():
    """A 0→1 transition detected by the poll fallback stamps
    bell.source == "poll" -- docs/plans/2026-08-07-bell-causality-plan.md §4.1."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}

    with patch("muxplex.bells.poll_bell_flag", new=AsyncMock(return_value=True)):
        await process_bell_flags(["session-a"], state)

    assert state["sessions"]["session-a"]["bell"]["source"] == "poll"


async def test_process_bell_flags_persistent_flag_does_not_change_source():
    """A 1→1 (persistent, no new transition) poll must not re-stamp source --
    it isn't a new bell event at all, so nothing about it should change."""
    state = empty_state()
    state["sessions"]["session-a"] = {"bell": empty_bell()}

    with patch("muxplex.bells.poll_bell_flag", new=AsyncMock(return_value=True)):
        await process_bell_flags(["session-a"], state)  # 0->1, stamps "poll"
        state["sessions"]["session-a"]["bell"]["source"] = "hook"  # simulate a
        # later hook bell having since overwritten it
        await process_bell_flags(["session-a"], state)  # 1->1, no transition

    assert state["sessions"]["session-a"]["bell"]["source"] == "hook"


def test_empty_bell_source_defaults_to_none():
    """empty_bell()'s source key defaults to None -- honest 'unknown
    provenance' for a fresh bell or a pre-feature state.json entry."""
    assert empty_bell()["source"] is None


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


# ---------------------------------------------------------------------------
# needs_attention tests
# ---------------------------------------------------------------------------


def test_needs_attention_false_when_unseen_count_zero():
    """needs_attention is False when unseen_count is 0, regardless of timestamps."""
    bell = {"unseen_count": 0, "last_fired_at": time.time(), "seen_at": None}
    assert needs_attention(bell) is False


def test_needs_attention_true_when_unseen_and_never_seen():
    """needs_attention is True when unseen_count > 0 and seen_at is None."""
    bell = {"unseen_count": 1, "last_fired_at": time.time(), "seen_at": None}
    assert needs_attention(bell) is True


def test_needs_attention_true_when_fired_after_last_seen():
    """needs_attention is True when last_fired_at is newer than seen_at."""
    now = time.time()
    bell = {"unseen_count": 1, "last_fired_at": now, "seen_at": now - 30.0}
    assert needs_attention(bell) is True


def test_needs_attention_false_when_seen_after_last_fired():
    """needs_attention is False when seen_at is newer than (or equal to) last_fired_at."""
    now = time.time()
    bell = {"unseen_count": 1, "last_fired_at": now - 30.0, "seen_at": now}
    assert needs_attention(bell) is False


def test_needs_attention_false_when_last_fired_equals_seen_at():
    """needs_attention is False on the boundary: last_fired_at == seen_at (not '>')."""
    now = time.time()
    bell = {"unseen_count": 1, "last_fired_at": now, "seen_at": now}
    assert needs_attention(bell) is False


def test_needs_attention_defaults_missing_keys_like_empty_bell():
    """A bare {} (no keys at all) is treated as unseen_count=0 -> not needing attention."""
    assert needs_attention({}) is False


def test_needs_attention_false_when_last_fired_at_missing_but_seen_at_set():
    """Defensive case: unseen_count > 0, seen_at set, but last_fired_at missing/None.

    This should not occur in practice (last_fired_at is always set alongside
    an unseen_count increment), but must not raise -- treated as not newer.
    """
    bell = {"unseen_count": 1, "last_fired_at": None, "seen_at": time.time() - 10.0}
    assert needs_attention(bell) is False

"""
Bell flag polling and unseen_count tracking for the tmux-web muxplex.

Based on spike findings: reading the tmux window_bell_flag does NOT clear it.
The flag persists until the window is made active inside tmux.

In-memory state:
    _bell_seen  — tracks whether the bell flag was '1' on the last poll,
                  keyed by session_name. Used to detect 0→1 transitions.

Public API:
    poll_bell_flag(session_name)             → bool
    process_bell_flags(session_names, state) → bool
    should_clear_bell(session_name, state)   → bool
    apply_bell_clear_rule(state)             → list[str]
    needs_attention(bell)                    → bool
"""

import time

from muxplex.sessions import run_tmux
from muxplex.state import empty_bell

# ---------------------------------------------------------------------------
# In-memory tracking: session_name → bool (was flag set on last poll?)
# ---------------------------------------------------------------------------

_bell_seen: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# poll_bell_flag
# ---------------------------------------------------------------------------


async def poll_bell_flag(session_name: str) -> bool:
    """Poll the tmux window_bell_flag for session_name.

    Calls: tmux display-message -t <name> -p #{window_bell_flag}

    Returns True if the output is '1', False otherwise (including on errors).
    Note: reading does NOT clear the tmux bell flag.
    """
    try:
        output = await run_tmux(
            "display-message", "-t", session_name, "-p", "#{window_bell_flag}"
        )
        return output.strip() == "1"
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# process_bell_flags
# ---------------------------------------------------------------------------


async def process_bell_flags(session_names: list[str], state: dict) -> bool:
    """Poll bell flags for all sessions and update state accordingly.

    NOTE: The tmux alert-bell hook (POST /api/sessions/{name}/bell) is the
    primary bell detection mechanism. window_bell_flag is only set when NO
    tmux client is watching the window — with an SSH/WezTerm session attached,
    the flag is never set even though the bell fires. This function serves as
    a fallback for sessions that fired before the coordinator registered the hook.

    Detects 0→1 transitions using _bell_seen and increments unseen_count.
    Persistent '1' flags (1→1) are not double-counted.
    When flag clears (1→0), _bell_seen is reset so the next '1' counts as
    a new, separate bell event.

    Ensures the bell sub-dict exists for each session in state.

    Args:
        session_names: List of session names to poll.
        state:         Mutable state dict (modified in-place).

    Returns:
        True if any bell state changed (new bell detected), False otherwise.
    """
    changed = False

    for name in session_names:
        # Ensure session entry and bell sub-dict exist
        if name not in state["sessions"]:
            state["sessions"][name] = {}
        if "bell" not in state["sessions"][name]:
            state["sessions"][name]["bell"] = empty_bell()

        bell = state["sessions"][name]["bell"]
        flag_set = await poll_bell_flag(name)
        previously_seen = _bell_seen.get(name, False)

        if flag_set and not previously_seen:
            # 0→1 transition: new bell event
            bell["unseen_count"] += 1
            bell["last_fired_at"] = time.time()
            _bell_seen[name] = True
            changed = True
        elif not flag_set and previously_seen:
            # 1→0: flag cleared — reset tracking so next '1' is a new bell
            # Do NOT decrement unseen_count
            _bell_seen[name] = False

    return changed


# ---------------------------------------------------------------------------
# Bell clear rule constants
# ---------------------------------------------------------------------------

_INTERACTION_WINDOW_SECONDS: float = 60.0


# ---------------------------------------------------------------------------
# should_clear_bell
# ---------------------------------------------------------------------------


def should_clear_bell(session_name: str, state: dict) -> bool:
    """Return True if any connected device qualifies to globally acknowledge bells.

    A session's bells should be cleared when ANY device satisfies ALL of:
        - viewing_session == session_name
        - view_mode == 'fullscreen'
        - last_interaction_at > now - _INTERACTION_WINDOW_SECONDS

    Args:
        session_name: Name of the tmux session to check.
        state:        Current application state dict.

    Returns:
        True if at least one device meets all conditions, False otherwise.
    """
    cutoff = time.time() - _INTERACTION_WINDOW_SECONDS
    for device in state["devices"].values():
        if (
            device["viewing_session"] == session_name
            and device["view_mode"] == "fullscreen"
            and device["last_interaction_at"] > cutoff
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# apply_bell_clear_rule
# ---------------------------------------------------------------------------


def needs_attention(bell: dict) -> bool:
    """Return True if a bell sub-dict represents a session needing attention.

    Canonical predicate (previously ported into three identical call sites
    in frontend/app.js -- see AGENTS.md "Semantics external clients
    re-implement"):

        unseen_count > 0 and (seen_at is None or last_fired_at > seen_at)

    A session needs attention when it has at least one unseen bell AND
    that bell fired more recently than the last time any device
    acknowledged it (or it has never been acknowledged at all).

    Args:
        bell: a bell sub-dict as stored at state["sessions"][name]["bell"]
            (see state.empty_bell() for shape/defaults). Missing keys are
            treated the same as empty_bell()'s defaults.

    Returns:
        bool. Defensive on a `last_fired_at is None` combined with a
        non-None `seen_at` (should not occur in practice -- last_fired_at
        is always set in the same update that increments unseen_count --
        but is treated as "not newer than seen_at" rather than raising).
    """
    if bell.get("unseen_count", 0) <= 0:
        return False
    seen_at = bell.get("seen_at")
    if seen_at is None:
        return True
    last_fired_at = bell.get("last_fired_at")
    if last_fired_at is None:
        return False
    return last_fired_at > seen_at


def apply_bell_clear_rule(state: dict) -> list[str]:
    """Check every session with unseen_count > 0 against the active-device gate.

    For each qualifying session (unseen_count > 0 AND should_clear_bell):
        - Resets unseen_count to 0
        - Sets seen_at to now
        - Resets _bell_seen[name] = False

    Args:
        state: Mutable application state dict (modified in-place).

    Returns:
        List of session names whose bells were cleared.
    """
    cleared: list[str] = []
    now = time.time()

    for name, session in state["sessions"].items():
        bell = session.get("bell")
        if bell is None or bell.get("unseen_count", 0) == 0:
            continue
        if should_clear_bell(name, state):
            bell["unseen_count"] = 0
            bell["seen_at"] = now
            _bell_seen[name] = False
            cleared.append(name)

    return cleared

"""
State schema and factory functions for muxplex.

State schema (all values are plain JSON-serialisable dicts):

    {
        "active_session": str | None,
        "active_remote_id": str | None,
        "active_view": str,  # 'all' | 'hidden' | view name
        "session_order": list[str],
        "sessions": {
            "<name>": {
                "bell": {
                    "last_fired_at": float | None,
                    "seen_at": float | None,
                    "unseen_count": int,
                }
            }
        },
        "devices": {
            "<device_id>": {
                "label": str,
                "viewing_session": str | None,
                "view_mode": "fullscreen" | "grid",
                "last_interaction_at": float,
                "last_heartbeat_at": float,
                "sync_group": str,  # "global" | "device:<device_id>"
            }
        },

        # -- sync groups: non-global groups only. NEVER contains "global" --
        # the top-level active_session/active_remote_id/active_view keys ARE
        # group "global"'s one and only storage (no mirroring -- see
        # resolve_group()/read_group_state()/write_group_state() below).
        "sync_groups": {
            "device:<device_id>": {
                "active_session": str | None,
                "active_remote_id": str | None,
                "active_view": str,
            }
        },

        # -- per-request WS fallback target + provenance (see below) --
        "terminal_session": str | None,  # fallback target for a WS with no ?session=
        "terminal_group": str,           # informational: group that last connected it

        # -- per-session follow-up queues (see muxplex/followups.py) --
        # A NEW TOP-LEVEL KEY, deliberately NOT nested under sessions[name] --
        # the poll cycle's free cleanup of sessions[name] for any name absent
        # from enumerate_sessions() is a trap for user-authored queued text
        # (enumerate_sessions() returns [] on a transient tmux hiccup, which
        # is indistinguishable from "zero sessions" -- see
        # docs/plans/2026-08-05-per-session-followup-queue-plan.md §3.2). Absence of a key here means "no
        # queue"; an entry is deleted entirely once its items list is empty
        # and its halt is cleared (see followups.py).
        "followups": {
            "<session name>": {
                "revision": int,
                "items": [
                    {"id": str, "text": str, "enter": bool, "created_at": float}
                ],
                "halted": {
                    "reason": str,
                    "detail": str,
                    "at": float,
                    "item_id": str,
                } | None,
            }
        },
    }

GET /api/state additionally merges in ``settings_updated_at: float`` (mirrors
``settings.settings_updated_at`` from settings.py) into the response at
request time -- it is NOT part of the on-disk state.json schema above, so
empty_state()/load_state()/save_state() are unaffected and unaware of it.
This lets pollers detect any SYNCABLE_KEYS settings change (including view
membership edits) via the same ~1s /api/state poll that already carries
active_session/active_view, without a dedicated settings re-fetch every
tick. See main.py's get_state() and AGENTS.md's "API is a public control
surface" section for the contract.

Sync groups: a device's ``sync_group`` selects which group's
active_session/active_remote_id/active_view it reads and writes. Group
"global" is the shared, server-wide default every pre-existing client
implicitly uses (it is stored in the top-level keys, not under
sync_groups). A device can opt into its own private group
"device:<device_id>" (see device_group_id()) to stop converging with
everyone else. See resolve_group(), read_group_state(),
write_group_state(), and ensure_group() for the read/write dispatch, and
gc_sync_groups()/clear_missing_active_sessions() for lifecycle.

The single ttyd process is a shared, contended resource independent of
sync groups: terminal_session/terminal_group record what it is actually
attached to and which group claimed it, so main.py's terminal-claim gate
and /terminal/ws guard can refuse to relay a session to a group that does
not hold it (see docs/API_SEMANTICS.md).
"""

import asyncio
import json
import os
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_default_state_dir = Path.home() / ".local" / "share" / "muxplex"
STATE_DIR: Path = Path(
    os.environ.get(
        "MUXPLEX_STATE_DIR", os.environ.get("TMUX_WEB_STATE_DIR", _default_state_dir)
    )
)
STATE_PATH: Path = STATE_DIR / "state.json"

# ---------------------------------------------------------------------------
# Sync group constants
# ---------------------------------------------------------------------------

GLOBAL_GROUP: str = "global"
GROUP_FIELDS: tuple[str, ...] = ("active_session", "active_remote_id", "active_view")

# ---------------------------------------------------------------------------
# Global asyncio lock — must be acquired before reading or writing state.
# ---------------------------------------------------------------------------

state_lock: asyncio.Lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def empty_state() -> dict:
    """Return a fresh, empty top-level state dict.

    Every call returns a fully independent object — no shared mutables.
    """
    return {
        "active_session": None,
        "active_remote_id": None,
        "active_view": "all",
        "session_order": [],
        "sessions": {},
        "devices": {},
        "sync_groups": {},
        "terminal_session": None,
        "terminal_group": GLOBAL_GROUP,
    }


def empty_bell() -> dict:
    """Return a fresh bell sub-dict with all fields reset."""
    return {
        "last_fired_at": None,
        "seen_at": None,
        "unseen_count": 0,
    }


def empty_device(device_id: str, label: str) -> dict:
    """Return a fresh device sub-dict.

    Args:
        device_id: Identifier for the device (unused in the dict itself,
                   kept as a parameter for call-site clarity).
        label:     Human-readable name for the device.
    """
    now = time.time()
    return {
        "label": label,
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": now,
        "last_heartbeat_at": now,
        "sync_group": GLOBAL_GROUP,
    }


def empty_group() -> dict:
    """Return a fresh group state dict (all GROUP_FIELDS at defaults)."""
    return {
        "active_session": None,
        "active_remote_id": None,
        "active_view": "all",
    }


# ---------------------------------------------------------------------------
# Sync group helpers
# ---------------------------------------------------------------------------


def device_group_id(device_id: str) -> str:
    """Return the canonical private-group id for *device_id*."""
    return f"device:{device_id}"


def resolve_group(state: dict, device_id: str | None) -> str:
    """Return the group id that *device_id* belongs to.

    ``None`` resolves to GLOBAL_GROUP (today's every-client-omits-it
    default). A *device_id* not present in ``state["devices"]`` raises
    KeyError — this function never falls back to GLOBAL_GROUP for an
    unknown device: silently routing an unrecognised device's request to
    the shared group is precisely the yank this feature exists to prevent.
    """
    if device_id is None:
        return GLOBAL_GROUP
    device = state["devices"][device_id]  # KeyError on unknown device_id
    return device.get("sync_group", GLOBAL_GROUP)


def read_group_state(state: dict, group: str) -> dict:
    """Return a COPY of *group*'s GROUP_FIELDS values.

    GLOBAL_GROUP reads the top-level keys (there is no
    ``sync_groups["global"]`` — see the module docstring's "no mirroring"
    rule). Any other group reads ``state["sync_groups"][group]``; an
    unknown non-global group raises KeyError.
    """
    if group == GLOBAL_GROUP:
        return {field: state[field] for field in GROUP_FIELDS}
    return dict(state["sync_groups"][group])  # KeyError on unknown group


def write_group_state(state: dict, group: str, updates: dict[str, object]) -> None:
    """Apply *updates* (a subset of GROUP_FIELDS) to *group*'s slot, in place.

    GLOBAL_GROUP writes the top-level keys directly. Any other group writes
    ``state["sync_groups"][group]``; an unknown non-global group raises
    KeyError. A key outside GROUP_FIELDS raises ValueError — fail loudly
    rather than silently ignoring a typo'd field name.
    """
    for key in updates:
        if key not in GROUP_FIELDS:
            raise ValueError(f"write_group_state: unknown field {key!r}")

    if group == GLOBAL_GROUP:
        for key, value in updates.items():
            state[key] = value
        return

    target = state["sync_groups"][group]  # KeyError on unknown group
    for key, value in updates.items():
        target[key] = value


def ensure_group(state: dict, group: str) -> bool:
    """Create *group* in ``state["sync_groups"]`` if absent, seeded from global.

    Returns True if the group was created, False if it already existed or
    *group* is GLOBAL_GROUP (a no-op for the global group).

    Seeding (not defaulting) is deliberate: "go independent" means "detach,
    keeping what I'm currently looking at", not "teleport me to the All
    view". It is the exact mirror of rejoin-adopts-global behavior on the
    client side.
    """
    if group == GLOBAL_GROUP:
        return False
    if group in state["sync_groups"]:
        return False
    state["sync_groups"][group] = read_group_state(state, GLOBAL_GROUP)
    return True


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def gc_sync_groups(state: dict) -> list[str]:
    """Delete every ``sync_groups`` key no live device claims.

    Returns the list of removed group ids. The target set is
    ``{d["sync_group"] for d in state["devices"].values()} - {GLOBAL_GROUP}``
    — groups are defined by their membership, so a group with no members is
    garbage by definition, from any cause (prune, toggle-back-to-global,
    device_id regenerated after a localStorage wipe). No new TTL, no new
    timer — this rides the existing prune_devices() call site in the poll
    cycle, and must run AFTER it.
    """
    claimed = {
        device.get("sync_group", GLOBAL_GROUP) for device in state["devices"].values()
    }
    claimed.discard(GLOBAL_GROUP)
    removed = [group for group in list(state["sync_groups"]) if group not in claimed]
    for group in removed:
        del state["sync_groups"][group]
    return removed


def clear_missing_active_sessions(state: dict, live: set[str]) -> list[str]:
    """Null ``active_session`` in every group (global + all sync_groups) when
    it is not in *live*. Returns the group ids that were cleared.

    Replaces the global-only check previously at main.py's poll cycle.
    ``active_remote_id`` is deliberately NOT touched — matching today's
    behavior.
    """
    cleared: list[str] = []

    if state["active_session"] is not None and state["active_session"] not in live:
        state["active_session"] = None
        cleared.append(GLOBAL_GROUP)

    for group, group_state in state["sync_groups"].items():
        session = group_state.get("active_session")
        if session is not None and session not in live:
            group_state["active_session"] = None
            cleared.append(group)

    return cleared


def normalize_state(state: dict) -> dict:
    """Fill absent schema keys (schema-upgrade of a pre-groups state.json).

    Filled only when absent:
        sync_groups            -> {}
        devices[*].sync_group  -> GLOBAL_GROUP
        terminal_group         -> GLOBAL_GROUP
        terminal_session       -> the current active_session value (before
                                   groups, ttyd was always attached to
                                   active_session; this restates that
                                   invariant rather than guessing)
        followups               -> {} (no invariant to enforce -- unlike
                                   sync_groups' GLOBAL_GROUP check above,
                                   there is no reserved key to guard against.
                                   Deliberately no repair of malformed
                                   entries either: a hand-edited or corrupt
                                   entry is caught fail-closed at fire time,
                                   not here -- see followups.py.)

    Raises ValueError if GLOBAL_GROUP is present in state["sync_groups"] —
    that is a bug (the mirroring this schema deliberately avoids) and must
    not be silently repaired.
    """
    if GLOBAL_GROUP in state.get("sync_groups", {}):
        raise ValueError(
            f"normalize_state: {GLOBAL_GROUP!r} must not be a key in sync_groups "
            "(top-level keys ARE its storage)"
        )

    state.setdefault("sync_groups", {})

    for device in state.get("devices", {}).values():
        device.setdefault("sync_group", GLOBAL_GROUP)

    if "terminal_session" not in state:
        state["terminal_session"] = state.get("active_session")
    state.setdefault("terminal_group", GLOBAL_GROUP)
    state.setdefault("followups", {})

    return state


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------


def register_device(
    state: dict,
    device_id: str,
    label: str,
    viewing_session: str | None,
    view_mode: str,
    last_interaction_at: float,
    sync_group: str | None = None,
) -> None:
    """Create or update a device entry in state['devices'].

    For new devices, seeds the entry via empty_device().
    Always refreshes last_heartbeat_at to time.time().
    Updates label, viewing_session, view_mode, last_interaction_at.

    sync_group semantics:
        None -> leave the device's current sync_group unchanged; a
                brand-new device gets GLOBAL_GROUP (empty_device()'s
                default). Version tolerance: a client that doesn't know
                about groups must not be able to reset a group it doesn't
                know exists.
        str  -> set the device's sync_group to it, and call ensure_group()
                so the group exists (seeded from global).

    Does NOT validate the group id — validation is a boundary concern and
    lives at the endpoint (main.py's heartbeat()).
    """
    if device_id not in state["devices"]:
        state["devices"][device_id] = empty_device(device_id, label)

    device = state["devices"][device_id]
    device["label"] = label
    device["viewing_session"] = viewing_session
    device["view_mode"] = view_mode
    device["last_interaction_at"] = last_interaction_at
    device["last_heartbeat_at"] = time.time()

    if sync_group is not None:
        device["sync_group"] = sync_group
        ensure_group(state, sync_group)


def prune_devices(state: dict, ttl_seconds: float = 300.0) -> list[str]:
    """Remove devices whose last_heartbeat_at is older than ttl_seconds.

    Returns the list of removed device IDs.
    """
    cutoff = time.time() - ttl_seconds
    stale = [
        device_id
        for device_id, device in state["devices"].items()
        if device["last_heartbeat_at"] < cutoff
    ]
    for device_id in stale:
        del state["devices"][device_id]
    return stale


# ---------------------------------------------------------------------------
# Sync I/O helpers (no lock — callers must hold state_lock when appropriate)
# ---------------------------------------------------------------------------


def load_state() -> dict:
    """Read and return state from STATE_PATH, schema-normalized.

    Returns normalize_state(empty_state()) if the file does not exist or
    contains invalid JSON; otherwise normalize_state() of the parsed file.
    """
    try:
        with open(STATE_PATH) as f:
            return normalize_state(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return normalize_state(empty_state())


def save_state(state: dict) -> None:
    """Atomically write *state* to STATE_PATH.

    Uses the write-to-tmp-then-os.replace pattern so readers never see a
    partial file.  Creates STATE_DIR (and parents) if it does not exist.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(STATE_PATH) + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_PATH)


# ---------------------------------------------------------------------------
# Async wrappers — acquire state_lock before touching the file
# ---------------------------------------------------------------------------


async def read_state() -> dict:
    """Async read: acquires state_lock, then delegates to load_state()."""
    async with state_lock:
        return load_state()


async def write_state(state: dict) -> None:
    """Async write: acquires state_lock, then delegates to save_state()."""
    async with state_lock:
        save_state(state)

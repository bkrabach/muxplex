"""
State schema and factory functions for the tmux-web muxplex.

State schema (all values are plain JSON-serialisable dicts):

    {
        "active_session": str | None,
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
            }
        },
    }
"""

import asyncio
import json
import os
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_default_state_dir = Path.home() / ".local" / "share" / "tmux-web"
STATE_DIR: Path = Path(os.environ.get("TMUX_WEB_STATE_DIR", _default_state_dir))
STATE_PATH: Path = STATE_DIR / "state.json"

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
        "session_order": [],
        "sessions": {},
        "devices": {},
    }


def empty_bell() -> dict:
    """Return a fresh bell sub-dict with all fields reset."""
    return {
        "last_fired_at": None,
        "seen_at": None,
        "unseen_count": 0,
    }


def empty_device(device_id: str, label: str) -> dict:  # noqa: ARG001
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
    }


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
) -> None:
    """Create or update a device entry in state['devices'].

    For new devices, seeds the entry via empty_device().
    Always refreshes last_heartbeat_at to time.time().
    Updates label, viewing_session, view_mode, last_interaction_at.
    """
    if device_id not in state["devices"]:
        state["devices"][device_id] = empty_device(device_id, label)

    device = state["devices"][device_id]
    device["label"] = label
    device["viewing_session"] = viewing_session
    device["view_mode"] = view_mode
    device["last_interaction_at"] = last_interaction_at
    device["last_heartbeat_at"] = time.time()


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
    """Read and return state from STATE_PATH.

    Returns empty_state() if the file does not exist or contains invalid JSON.
    """
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return empty_state()


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

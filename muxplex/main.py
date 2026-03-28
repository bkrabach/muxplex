"""
FastAPI coordinator application for tmux-web.

Entry point for the coordinator service. Exposes:
    GET /health  →  {"status": "ok"}

Background poll loop reconciles tmux session state every POLL_INTERVAL seconds.
"""

import asyncio
import contextlib
import logging
import os
import pathlib
import time
from typing import Literal

import websockets
import websockets.exceptions
from websockets.typing import Subprotocol

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from muxplex.bells import apply_bell_clear_rule, process_bell_flags
from muxplex.sessions import (
    enumerate_sessions,
    get_session_list,
    get_snapshots,
    run_tmux,
    snapshot_all,
    update_session_cache,
)
from muxplex.state import (
    empty_bell,
    load_state,
    prune_devices,
    read_state,
    register_device,
    save_state,
    state_lock,
)
from muxplex.ttyd import kill_orphan_ttyd, kill_ttyd, spawn_ttyd, TTYD_PORT

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL: float = float(os.environ.get("POLL_INTERVAL", "2.0"))
SERVER_PORT: int = int(os.environ.get("MUXPLEX_PORT", "8088"))

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level task reference
# ---------------------------------------------------------------------------

_poll_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# Poll cycle
# ---------------------------------------------------------------------------


async def _run_poll_cycle() -> None:
    """Perform one full poll cycle, all operations executed under state_lock."""
    async with state_lock:
        # 1. Enumerate live tmux sessions
        names = await enumerate_sessions()
        name_set = set(names)

        # 2. Capture pane snapshots and update in-memory snapshot cache
        new_snapshots = await snapshot_all(names)
        update_session_cache(names, new_snapshots)

        # 3. Load current persisted state
        state = load_state()

        # 4. Reconcile session_order: preserve user ordering, add new, remove deleted
        state["session_order"] = [s for s in state["session_order"] if s in name_set]
        existing_order_set = set(state["session_order"])
        for name in names:
            if name not in existing_order_set:
                state["session_order"].append(name)

        # 5. Ensure bell entries exist for every current session
        for name in names:
            if name not in state["sessions"]:
                state["sessions"][name] = {}
            if "bell" not in state["sessions"][name]:
                state["sessions"][name]["bell"] = empty_bell()

        # 6. Remove state entries for sessions that no longer exist
        deleted = [s for s in list(state["sessions"]) if s not in name_set]
        for name in deleted:
            del state["sessions"][name]

        # 7. Clear active_session if the session is gone
        if state["active_session"] not in name_set:
            state["active_session"] = None

        # 8. Process bell flags (detect 0→1 transitions, update unseen_count)
        await process_bell_flags(names, state)

        # 9. Apply bell clear rule (acknowledge bells when device is watching fullscreen)
        apply_bell_clear_rule(state)

        # 10. Prune devices that haven't sent a heartbeat recently
        prune_devices(state)

        # 11. Atomically persist the updated state
        save_state(state)


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------


async def _poll_loop() -> None:
    """Run _run_poll_cycle() every POLL_INTERVAL seconds, catching all exceptions."""
    while True:
        try:
            await _run_poll_cycle()
        except Exception:
            _log.exception("poll cycle error")
        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _poll_task

    # Startup: kill any orphaned ttyd from a previous coordinator run, then
    # start the background poll loop.
    await kill_orphan_ttyd()
    _poll_task = asyncio.create_task(_poll_loop())

    # Register tmux alert-bell hook so bells are detected even when clients are attached.
    # window_bell_flag is only set when no client watches the window; the hook fires always.
    try:
        await run_tmux(
            "set-hook",
            "-g",
            "alert-bell",
            f"run-shell 'curl -sfo /dev/null -X POST http://localhost:{SERVER_PORT}/api/sessions/#{{session_name}}/bell || true'",
        )
    except Exception:
        pass  # tmux not running at startup is OK; hook will be set on first poll

    yield

    # Shutdown: cancel the poll loop task and wait for it to finish.
    if _poll_task is not None:
        _poll_task.cancel()
        try:
            await _poll_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="tmux-web coordinator", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class StatePatch(BaseModel):
    session_order: list[str]


class HeartbeatPayload(BaseModel):
    device_id: str
    label: str
    viewing_session: str | None
    view_mode: Literal["grid", "fullscreen"]
    last_interaction_at: float


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Simple liveness check."""
    return {"status": "ok"}


@app.get("/api/state")
async def get_state() -> dict:
    """Return the full persistent state."""
    return await read_state()


@app.patch("/api/state")
async def patch_state(patch: StatePatch) -> dict:
    """Update session_order in the persistent state and return the updated state."""
    async with state_lock:
        state = load_state()
        state["session_order"] = patch.session_order
        save_state(state)
        return state


@app.get("/api/sessions")
async def get_sessions() -> list[dict]:
    """Return list of sessions with name, snapshot, and bell data."""
    names = get_session_list()
    snapshots = get_snapshots()
    state = await read_state()

    result = []
    for name in names:
        session_state = state.get("sessions", {}).get(name, {})
        bell = session_state.get("bell", empty_bell())
        result.append(
            {
                "name": name,
                "snapshot": snapshots.get(name, ""),
                "bell": bell,
            }
        )
    return result


@app.post("/api/sessions/{name}/connect")
async def connect_session(name: str) -> dict:
    """Connect to a tmux session via ttyd.

    Kills any existing ttyd process, spawns a new one attached to *name*,
    and updates the active_session in persistent state.

    Returns {active_session: name, ttyd_port: 7682}.
    Raises HTTP 404 if *name* is not in the known session list (when non-empty).
    """
    known = get_session_list()
    if known and name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    await kill_ttyd()
    await spawn_ttyd(name)

    async with state_lock:
        state = load_state()
        state["active_session"] = name
        save_state(state)

    return {"active_session": name, "ttyd_port": TTYD_PORT}


@app.delete("/api/sessions/current")
async def delete_current_session() -> dict:
    """Disconnect the current ttyd session.

    Kills the running ttyd process and clears active_session in persistent state.

    Returns {active_session: None}.
    """
    await kill_ttyd()

    async with state_lock:
        state = load_state()
        state["active_session"] = None
        save_state(state)

    return {"active_session": None}


@app.post("/api/heartbeat")
async def heartbeat(payload: HeartbeatPayload) -> dict:
    """Register or update a device heartbeat.

    Acquires state_lock, loads state, calls register_device() with payload
    fields, saves state.

    Returns {device_id: str, status: 'ok'}.
    Missing device_id or invalid view_mode returns 422 (handled by Pydantic).
    """
    async with state_lock:
        state = load_state()
        register_device(
            state,
            device_id=payload.device_id,
            label=payload.label,
            viewing_session=payload.viewing_session,
            view_mode=payload.view_mode,
            last_interaction_at=payload.last_interaction_at,
        )
        save_state(state)

    return {"device_id": payload.device_id, "status": "ok"}


@app.post("/api/sessions/{name}/bell")
async def receive_bell(name: str) -> dict:
    """Called by tmux alert-bell hook when a bell fires in session *name*.

    This is more reliable than polling window_bell_flag because tmux only
    sets that flag when no client is attached -- with an SSH/WezTerm session
    attached, the flag never gets set even though the bell fires.
    """
    async with state_lock:
        state = load_state()
        if name not in state["sessions"]:
            state["sessions"][name] = {}
        if "bell" not in state["sessions"][name]:
            state["sessions"][name]["bell"] = empty_bell()
        bell = state["sessions"][name]["bell"]
        bell["unseen_count"] = bell.get("unseen_count", 0) + 1
        bell["last_fired_at"] = time.time()
        save_state(state)
    return {"ok": True, "session": name}


@app.post("/api/internal/setup-hooks")
async def setup_hooks() -> dict:
    """Re-register tmux hooks. Call after tmux server restarts."""
    try:
        await run_tmux(
            "set-hook",
            "-g",
            "alert-bell",
            f"run-shell 'curl -sfo /dev/null -X POST http://localhost:{SERVER_PORT}/api/sessions/#{{session_name}}/bell || true'",
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# WebSocket proxy — bridges browser to ttyd (eliminates Caddy dependency)
# ---------------------------------------------------------------------------


@app.websocket("/terminal/ws")
async def terminal_ws_proxy(websocket: WebSocket) -> None:
    """Proxy WebSocket frames between the browser and ttyd.

    Accepts with subprotocol 'tty' (required by ttyd), then opens a connection
    to ws://localhost:{TTYD_PORT}/ws and relays frames bidirectionally.
    """
    await websocket.accept(subprotocol="tty")

    ttyd_url = f"ws://localhost:{TTYD_PORT}/ws"
    try:
        async with websockets.connect(
            ttyd_url, subprotocols=[Subprotocol("tty")]
        ) as ttyd_ws:

            async def client_to_ttyd() -> None:
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        await ttyd_ws.send(data)
                except Exception:
                    pass

            async def ttyd_to_client() -> None:
                try:
                    async for message in ttyd_ws:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(client_to_ttyd(), ttyd_to_client())
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Static file serving — MUST come after all API routes (first-match-wins)
# ---------------------------------------------------------------------------

_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

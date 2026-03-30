"""
muxplex — FastAPI application for the tmux session dashboard.

Entry point for the muxplex server. Exposes:
    GET /health  →  {"status": "ok"}

Background poll loop reconciles tmux session state every POLL_INTERVAL seconds.
"""

import asyncio
import contextlib
import json
import logging
import os
import pathlib
import pwd
import subprocess
import sys
import time
from typing import Literal

import websockets
import websockets.exceptions
from websockets.typing import Subprotocol

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.responses import RedirectResponse

from muxplex.auth import (
    AuthMiddleware,
    authenticate_pam,
    create_session_cookie,
    generate_and_save_password,
    get_password_path,
    load_or_create_secret,
    load_password,
    pam_available,
    verify_session_cookie,
)
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
from muxplex.settings import load_settings, patch_settings
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

    # Startup: kill any orphaned ttyd from a previous muxplex run, then
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

app = FastAPI(title="muxplex", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Auth setup
# ---------------------------------------------------------------------------


def _resolve_auth() -> tuple[str, str]:
    """Determine auth mode and resolve password. Returns (auth_mode, password).

    Fallback chain for non-localhost:
      1. PAM available → ("pam", "")
      2. MUXPLEX_PASSWORD env → ("password", <env value>)
      3. ~/.config/muxplex/password file → ("password", <file value>)
      4. Auto-generate → ("password", <generated>)
    """
    # Explicit override: MUXPLEX_AUTH=password forces password mode
    force_password = os.environ.get("MUXPLEX_AUTH", "").lower() == "password"

    if not force_password and pam_available():
        running_user = pwd.getpwuid(os.getuid()).pw_name
        print(f"  muxplex auth: PAM (user: {running_user})", file=sys.stderr)
        return "pam", ""

    if not force_password:
        print("  muxplex auth: PAM unavailable, using password mode", file=sys.stderr)

    # Password mode — resolve password
    env_pw = os.environ.get("MUXPLEX_PASSWORD")
    if env_pw:
        print("  muxplex auth: password (env)", file=sys.stderr)
        return "password", env_pw

    file_pw = load_password()
    if file_pw:
        print(
            f"  muxplex auth: password (file: {get_password_path()})",
            file=sys.stderr,
        )
        return "password", file_pw

    # Last resort: auto-generate
    generated = generate_and_save_password()
    print(
        f"  muxplex auth: password generated — {generated} — saved to {get_password_path()}",
        file=sys.stderr,
    )
    return "password", generated


_auth_mode, _auth_password = _resolve_auth()
_auth_secret = load_or_create_secret()
_auth_ttl = int(os.environ.get("MUXPLEX_SESSION_TTL", "604800"))

app.add_middleware(
    AuthMiddleware,
    auth_mode=_auth_mode,
    secret=_auth_secret,
    ttl_seconds=_auth_ttl,
    password=_auth_password,
)


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


class CreateSessionPayload(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be empty or whitespace")
        return stripped


# ---------------------------------------------------------------------------
# Frontend directory
# ---------------------------------------------------------------------------

_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"


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


@app.post("/api/sessions")
async def create_session(payload: CreateSessionPayload) -> dict:
    """Create a new tmux session using the new_session_template from settings.

    Substitutes {name} in the template with the validated payload name, then
    runs the command as a fire-and-forget subprocess (stdout/stderr discarded).

    Returns {name: name} regardless of outcome.
    Raises HTTP 422 if name is empty or whitespace (handled by Pydantic).
    """
    name = payload.name
    settings = load_settings()
    command = settings["new_session_template"].replace("{name}", name)
    try:
        subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        _log.warning("Failed to launch new-session command: %r", command)
    return {"name": name}


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


@app.delete("/api/sessions/{name}")
async def delete_session(name: str) -> dict:
    """Kill a tmux session by name.

    Runs `tmux kill-session -t {name}`. Returns {ok: True, name: name}.
    404 if session is not in the known session list (when non-empty).
    Must be declared after DELETE /api/sessions/current so "current" routes correctly.
    """
    known = get_session_list()
    if known and name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    try:
        await run_tmux("kill-session", "-t", name)
    except RuntimeError:
        raise HTTPException(status_code=500, detail=f"Failed to kill session '{name}'")

    return {"ok": True, "name": name}


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


@app.get("/api/settings")
async def get_settings() -> dict:
    """Return the current settings."""
    return load_settings()


@app.patch("/api/settings")
async def update_settings(request: Request) -> dict:
    """Merge known keys from the request body into settings and return updated settings."""
    body = await request.json()
    return patch_settings(body)


# ---------------------------------------------------------------------------
# WebSocket proxy — bridges browser to ttyd (eliminates Caddy dependency)
# ---------------------------------------------------------------------------


@app.websocket("/terminal/ws")
async def terminal_ws_proxy(websocket: WebSocket) -> None:
    """Proxy WebSocket frames between the browser and ttyd.

    Accepts with subprotocol 'tty' (required by ttyd), then opens a connection
    to ws://localhost:{TTYD_PORT}/ws and relays frames bidirectionally.
    """
    # Auth check before accepting — BaseHTTPMiddleware doesn't cover WebSocket scope
    host = websocket.client.host if websocket.client else ""
    if host not in ("127.0.0.1", "::1"):
        session_cookie = websocket.cookies.get("muxplex_session")
        if not session_cookie or not verify_session_cookie(
            _auth_secret, session_cookie, _auth_ttl
        ):
            await websocket.close(code=4001)
            return

    await websocket.accept(subprotocol="tty")

    ttyd_url = f"ws://localhost:{TTYD_PORT}/ws"
    try:
        async with websockets.connect(
            ttyd_url, subprotocols=[Subprotocol("tty")]
        ) as ttyd_ws:

            async def client_to_ttyd() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("bytes"):
                            await ttyd_ws.send(msg["bytes"])
                        elif msg.get("text"):
                            await ttyd_ws.send(msg["text"])
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
# Auth routes
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve branded login.html with injected window.MUXPLEX_AUTH containing auth mode and username."""
    html = (_FRONTEND_DIR / "login.html").read_text()
    username = pwd.getpwuid(os.getuid()).pw_name if _auth_mode == "pam" else ""
    mode_data = json.dumps({"mode": _auth_mode, "user": username})
    html = html.replace(
        "</head>", f"<script>window.MUXPLEX_AUTH = {mode_data};</script></head>"
    )
    return HTMLResponse(html)


@app.post("/login")
async def post_login(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
) -> RedirectResponse:
    """Validate credentials and issue a session cookie on success.

    In PAM mode, delegates to authenticate_pam(username, password).
    In password mode, compares the submitted password to _auth_password.

    On success: redirect to / with a signed muxplex_session cookie.
    On failure: redirect to /login?error=1.
    """
    # Validate credentials
    if _auth_mode == "pam":
        valid = authenticate_pam(username, password)
    else:
        valid = password == _auth_password

    if not valid:
        return RedirectResponse("/login?error=1", status_code=303)

    # Issue session cookie
    cookie_value = create_session_cookie(_auth_secret, _auth_ttl)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "muxplex_session",
        cookie_value,
        httponly=True,
        samesite="strict",
        max_age=_auth_ttl if _auth_ttl > 0 else None,
    )
    return response


@app.get("/auth/logout")
async def logout() -> RedirectResponse:
    """Clear the muxplex_session cookie and redirect to /login."""
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("muxplex_session")
    return response


@app.get("/auth/mode")
async def auth_mode_endpoint():
    """Return the current auth mode and running username."""
    username = ""
    if _auth_mode == "pam":
        username = pwd.getpwuid(os.getuid()).pw_name
    return {"mode": _auth_mode, "user": username}


# ---------------------------------------------------------------------------
# Static file serving — MUST come after all API routes (first-match-wins)
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

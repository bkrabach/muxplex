"""
muxplex — FastAPI application for the tmux session dashboard.

Entry point for the muxplex server. Exposes:
    GET /health  →  {"status": "ok"}

Background poll loop reconciles tmux session state every POLL_INTERVAL seconds.
"""

import asyncio
import contextlib
import copy
import hmac
import json
import logging
import os
import pathlib
import pwd
import socket
import subprocess
import sys
import time
from typing import Literal

import httpx
import websockets
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
from muxplex.settings import load_federation_key, load_settings, patch_settings
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

    app.state.federation_client = httpx.AsyncClient(timeout=5.0, follow_redirects=False)

    yield

    try:
        client = getattr(app.state, "federation_client", None)
        if client is not None:
            await client.aclose()
    except Exception:
        _log.exception("federation_client aclose error")
    finally:
        # Cleanup: cancel the poll loop task and wait for it to finish.
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
_federation_key = load_federation_key()

app.add_middleware(
    AuthMiddleware,
    auth_mode=_auth_mode,
    secret=_auth_secret,
    ttl_seconds=_auth_ttl,
    password=_auth_password,
    federation_key=_federation_key,
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
# Frontend directory + hostname
# ---------------------------------------------------------------------------

_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"

# Short hostname (no domain) injected into page titles so browser tabs show
# which machine each muxplex instance is running on.
_HOSTNAME = socket.gethostname().split(".")[0]


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
    _log.info("Creating session '%s' with command: %s", name, command)
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

    _log.info("Connecting to session '%s'", name)
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
    """Kill/destroy a tmux session using the delete_session_template from settings.

    Reads delete_session_template, substitutes {name}, and runs it synchronously
    (30s timeout) so the caller can rely on the session being gone on return.

    Returns {ok: True, name: name}. Errors are logged as warnings — the endpoint
    always returns 200 so the UI can refresh and reflect the gone session.
    404 if session is not in the known session list (when non-empty).
    Must be declared after DELETE /api/sessions/current so "current" routes correctly.
    """
    known = get_session_list()
    if known and name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    settings = load_settings()
    command = settings.get(
        "delete_session_template", "tmux kill-session -t {name}"
    ).replace("{name}", name)

    _log.info("Deleting session '%s' with command: %s", name, command)
    try:
        result = subprocess.run(
            command,
            shell=True,
            input="y\n",  # auto-confirm interactive prompts (e.g. amplifier-dev --destroy)
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            _log.info("Session '%s' deleted successfully", name)
        else:
            _log.warning(
                "Delete command failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip(),
            )
    except subprocess.TimeoutExpired:
        _log.warning("Delete command timed out after 30s: %r", command)
    except Exception:
        _log.warning("Delete command failed: %r", command)

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


@app.post("/api/sessions/{name}/bell/clear")
async def clear_bell(name: str) -> dict:
    """Clear unseen bell count for session *name*.

    Resets unseen_count to 0 and sets seen_at to now.
    Called by the frontend when a user opens a session to acknowledge bells.
    No-op if the session or bell sub-dict does not exist.
    """
    async with state_lock:
        state = load_state()
        session = state.get("sessions", {}).get(name)
        if session and "bell" in session:
            session["bell"]["unseen_count"] = 0
            session["bell"]["seen_at"] = time.time()
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
    """Return the current settings with sensitive keys redacted."""
    settings = load_settings()
    result = copy.deepcopy(settings)
    result["federation_key"] = ""
    for inst in result.get("remote_instances", []):
        if "key" in inst:
            inst["key"] = ""
    return result


@app.patch("/api/settings")
async def update_settings(request: Request) -> dict:
    """Merge known keys from the request body into settings and return updated settings."""
    body = await request.json()
    return patch_settings(body)


@app.get("/api/instance-info")
async def instance_info() -> dict:
    """Return this instance's display name and version.

    Public endpoint (no auth required) — used by remote instances to
    discover peer names and verify reachability.
    """
    settings = load_settings()
    # Read fresh so the UI reflects key-file changes without requiring a restart.
    fed_key = load_federation_key()
    return {
        "name": settings["device_name"],
        "version": app.version,
        "federation_enabled": bool(fed_key),
    }


# ---------------------------------------------------------------------------
# WebSocket proxy — bridges browser to ttyd (eliminates Caddy dependency)
# ---------------------------------------------------------------------------


def _ttyd_is_listening() -> bool:
    """Return True if something is accepting TCP connections on TTYD_PORT.

    Uses a raw socket connect (no WebSocket handshake, no PTY spawned).
    Takes < 1 ms on localhost when ttyd is running; fails immediately with
    ConnectionRefusedError when it's not.  OSError/TimeoutError are also
    caught so the caller always gets a bool.
    """
    try:
        with socket.create_connection(("127.0.0.1", TTYD_PORT), timeout=0.5):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


async def _ws_auth_check(websocket: WebSocket) -> bool:
    """Return True if the WebSocket caller is authorized.

    Closes the WebSocket with code 4001 and returns False if the caller
    is not authorized.  Localhost connections (127.0.0.1 / ::1) are
    unconditionally trusted.  Remote callers must present a valid
    ``muxplex_session`` cookie OR a Bearer token matching ``_federation_key``.
    """
    host = websocket.client.host if websocket.client else ""
    if host in ("127.0.0.1", "::1"):
        return True
    session_cookie = websocket.cookies.get("muxplex_session")
    cookie_ok = session_cookie and verify_session_cookie(
        _auth_secret, session_cookie, _auth_ttl
    )
    bearer_ok = False
    if _federation_key:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            bearer_ok = hmac.compare_digest(auth_header[7:], _federation_key)
    if not cookie_ok and not bearer_ok:
        await websocket.close(code=4001)
        return False
    return True


@app.websocket("/terminal/ws")
async def terminal_ws_proxy(websocket: WebSocket) -> None:
    """Proxy WebSocket frames between the browser and ttyd.

    Checks that ttyd is alive BEFORE accepting the browser WebSocket.  If ttyd
    is not listening (e.g. after a service restart), auto-spawns it using the
    active_session from state, then waits briefly for it to bind its port.

    Only after ttyd is confirmed reachable does the function call
    websocket.accept() — so the browser's 'open' event only fires once a real
    relay is possible.  This prevents the reconnect-counter bounce bug where
    the proxy accepted immediately (resetting _reconnectAttempts to 0) and
    then closed as soon as it couldn't reach the dead ttyd.
    """
    # Auth check before accepting — BaseHTTPMiddleware doesn't cover WebSocket scope
    if not await _ws_auth_check(websocket):
        return

    # Ensure ttyd is reachable BEFORE accepting the browser WS.
    # After a service restart ttyd is dead but clients reconnect immediately.
    # Auto-spawn from active_session so the browser's 'open' event only fires
    # when a real relay is possible — eliminates the 0→1→0→1 counter bounce.
    if not _ttyd_is_listening():
        try:
            async with state_lock:
                state = load_state()
            session_name = state.get("active_session")
            if session_name:
                _log.info(
                    "WS proxy: ttyd not listening, auto-spawning for '%s'",
                    session_name,
                )
                await kill_ttyd()
                await spawn_ttyd(session_name)
                await asyncio.sleep(0.8)  # wait for ttyd to bind its port
        except Exception as exc:
            _log.warning("WS proxy: failed to auto-spawn ttyd: %s", exc)

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
                except Exception as exc:
                    _log.debug("ws relay closed (client_to_ttyd): %s", exc)

            async def ttyd_to_client() -> None:
                try:
                    async for message in ttyd_ws:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception as exc:
                    _log.debug("ws relay closed (ttyd_to_client): %s", exc)

            await asyncio.gather(client_to_ttyd(), ttyd_to_client())
    except Exception as exc:
        _log.debug("ws proxy closed: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Federation WebSocket proxy — bridges browser to a remote instance's ttyd
# ---------------------------------------------------------------------------


@app.websocket("/federation/{remote_id}/terminal/ws")
async def federation_terminal_ws_proxy(websocket: WebSocket, remote_id: int) -> None:
    """Proxy WebSocket frames between the browser and a remote muxplex ttyd.

    *remote_id* is the integer index into the ``remote_instances`` list in
    settings.  Authenticates to the remote instance using the configured
    ``key`` field via a Bearer header.

    Auth check uses the same cookie + bearer pattern as terminal_ws_proxy.
    Closes with code 4004 if remote_id is out of range.
    """
    # Auth check before accepting — same pattern as terminal_ws_proxy
    if not await _ws_auth_check(websocket):
        return

    # Look up remote instance by index
    settings = load_settings()
    remote_instances: list[dict] = settings.get("remote_instances", [])
    if remote_id < 0 or remote_id >= len(remote_instances):
        await websocket.close(code=4004)
        return

    remote = remote_instances[remote_id]
    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")

    # Convert http(s) URL to ws(s)
    if remote_url.startswith("https://"):
        ws_url = "wss://" + remote_url[8:] + "/terminal/ws"
    elif remote_url.startswith("http://"):
        ws_url = "ws://" + remote_url[7:] + "/terminal/ws"
    else:
        ws_url = remote_url + "/terminal/ws"  # assume already ws:// or wss://

    await websocket.accept(subprotocol="tty")

    try:
        async with websockets.connect(
            ws_url,
            subprotocols=[Subprotocol("tty")],
            additional_headers={"Authorization": f"Bearer {remote_key}"},
        ) as remote_ws:

            async def client_to_remote() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("bytes"):
                            await remote_ws.send(msg["bytes"])
                        elif msg.get("text"):
                            await remote_ws.send(msg["text"])
                except Exception as exc:
                    _log.debug("federation ws relay closed (client_to_remote): %s", exc)

            async def remote_to_client() -> None:
                try:
                    async for message in remote_ws:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception as exc:
                    _log.debug("federation ws relay closed (remote_to_client): %s", exc)

            await asyncio.gather(client_to_remote(), remote_to_client())
    except Exception as exc:
        _log.debug("federation ws proxy closed: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def index_page():
    """Serve index.html with hostname injected into the page title."""
    html = (_FRONTEND_DIR / "index.html").read_text()
    html = html.replace(
        "<title>muxplex</title>",
        f"<title>{_HOSTNAME} \u2014 muxplex</title>",
    )
    return HTMLResponse(html)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve branded login.html with injected window.MUXPLEX_AUTH containing auth mode and username."""
    html = (_FRONTEND_DIR / "login.html").read_text()
    username = pwd.getpwuid(os.getuid()).pw_name if _auth_mode == "pam" else ""
    mode_data = json.dumps({"mode": _auth_mode, "user": username})
    html = html.replace(
        "</head>", f"<script>window.MUXPLEX_AUTH = {mode_data};</script></head>"
    )
    html = html.replace(
        "<title>Sign in \u2014 muxplex</title>",
        f"<title>Sign in \u2014 {_HOSTNAME} \u2014 muxplex</title>",
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


@app.get("/api/federation/sessions")
async def federation_sessions(request: Request) -> list[dict]:
    """Fetch sessions from all instances (local + remotes) and merge.

    Local sessions are tagged with deviceName (from settings) and remoteId=None.
    Remote sessions are fetched concurrently via asyncio.gather with Bearer auth
    headers. Failed remotes produce a status entry with status='unreachable' or
    status='auth_failed'.
    """
    settings = load_settings()
    local_device_name: str = settings.get("device_name", "")
    remote_instances: list[dict] = settings.get("remote_instances", [])

    # Build local sessions with deviceName/remoteId tags
    names = get_session_list()
    snapshots = get_snapshots()
    state = await read_state()
    local_sessions: list[dict] = []
    for name in names:
        session_state = state.get("sessions", {}).get(name, {})
        bell = session_state.get("bell", empty_bell())
        local_sessions.append(
            {
                "name": name,
                "snapshot": snapshots.get(name, ""),
                "bell": bell,
                "deviceName": local_device_name,
                "remoteId": None,
            }
        )

    if not remote_instances:
        return local_sessions

    # Fetch remote sessions concurrently
    http_client: httpx.AsyncClient = request.app.state.federation_client

    async def fetch_remote(i: int, remote: dict) -> list[dict]:
        """Fetch /api/sessions from a remote instance, returning session dicts or a status entry."""
        url: str = remote.get("url", "")
        key: str = remote.get("key", "")
        remote_name: str = remote.get("name", url)
        remote_id: int = i
        try:
            resp = await http_client.get(
                f"{url.rstrip('/')}/api/sessions",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code in (401, 403):
                return [
                    {
                        "status": "auth_failed",
                        "remoteId": remote_id,
                        "deviceName": remote_name,
                    }
                ]
            resp.raise_for_status()
            sessions = resp.json()
            # Tag each session with deviceName, remoteId, and unique sessionKey
            return [
                {
                    **s,
                    "deviceName": remote_name,
                    "remoteId": remote_id,
                    "sessionKey": f"{remote_id}:{s.get('name', '')}",
                }
                for s in sessions
            ]
        except httpx.HTTPStatusError:
            return [
                {
                    "status": "unreachable",
                    "remoteId": remote_id,
                    "deviceName": remote_name,
                }
            ]
        except Exception as exc:
            _log.warning("Unexpected error fetching remote %s: %s", url, exc)
            return [
                {
                    "status": "unreachable",
                    "remoteId": remote_id,
                    "deviceName": remote_name,
                }
            ]

    remote_results: list[list[dict]] = await asyncio.gather(
        *(fetch_remote(i, remote) for i, remote in enumerate(remote_instances))
    )

    all_sessions: list[dict] = list(local_sessions)
    for result in remote_results:
        all_sessions.extend(result)

    return all_sessions


@app.post("/api/federation/generate-key")
async def federation_generate_key() -> dict:
    """Generate a new federation key, write it to FEDERATION_KEY_PATH, and return it.

    Creates the parent directory (mode 0700) if it doesn't exist.
    Writes the key with a trailing newline and sets file mode to 0600.

    Returns {key: str, path: str}.
    """
    import secrets as _secrets
    from muxplex.settings import FEDERATION_KEY_PATH

    key = _secrets.token_urlsafe(32)
    path = FEDERATION_KEY_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(key + "\n")
    path.chmod(0o600)
    return {"key": key, "path": str(path)}


@app.post("/api/federation/{remote_id}/connect/{session_name}")
async def federation_connect(
    remote_id: int, session_name: str, request: Request
) -> dict:
    """Proxy a connect POST to a remote instance to spawn its ttyd.

    Looks up the remote by integer index into ``remote_instances`` in settings,
    sends ``POST {remote_url}/api/sessions/{session_name}/connect`` with a
    Bearer auth header, and returns the remote's JSON response.

    Raises HTTP 404 if ``remote_id`` is not a valid integer index.
    """
    settings = load_settings()
    remotes = settings.get("remote_instances", [])
    if remote_id < 0 or remote_id >= len(remotes):
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{remote_id}' not found",
        )
    remote = remotes[remote_id]

    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions/{session_name}/connect"

    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"},
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Remote returned {exc.response.status_code}",
        )
    except Exception as exc:
        _log.warning("federation_connect: remote %s unreachable: %s", remote_url, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Remote unreachable: {remote_url}",
        )


@app.post("/api/federation/{remote_id}/sessions/{session_name}/bell/clear")
async def federation_bell_clear(
    remote_id: int, session_name: str, request: Request
) -> dict:
    """Proxy a bell-clear POST to a remote instance.

    Looks up the remote by integer index into ``remote_instances`` in settings,
    sends ``POST {remote_url}/api/sessions/{session_name}/bell/clear`` with a
    Bearer auth header, and returns the remote's JSON response.

    Raises HTTP 404 if ``remote_id`` is not a valid integer index.
    """
    settings = load_settings()
    remotes = settings.get("remote_instances", [])
    if remote_id < 0 or remote_id >= len(remotes):
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{remote_id}' not found",
        )
    remote = remotes[remote_id]

    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions/{session_name}/bell/clear"

    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"},
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Remote returned {exc.response.status_code}",
        )
    except Exception as exc:
        _log.warning(
            "federation_bell_clear: remote %s unreachable: %s", remote_url, exc
        )
        raise HTTPException(
            status_code=503,
            detail=f"Remote unreachable: {remote_url}",
        )


@app.post("/api/federation/{remote_id}/sessions")
async def federation_create_session(
    remote_id: int, payload: CreateSessionPayload, request: Request
) -> dict:
    """Proxy a create-session POST to a remote instance.

    Looks up the remote by integer index into ``remote_instances`` in settings,
    sends ``POST {remote_url}/api/sessions`` with a Bearer auth header and JSON
    body ``{name: ...}``, and returns the remote's JSON response.

    Raises HTTP 404 if ``remote_id`` is not a valid integer index,
    503 when remote is unreachable, 502 when remote returns HTTP error.
    """
    settings = load_settings()
    remotes = settings.get("remote_instances", [])
    if remote_id < 0 or remote_id >= len(remotes):
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{remote_id}' not found",
        )
    remote = remotes[remote_id]
    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions"
    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"},
            json={"name": payload.name},
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Remote returned {exc.response.status_code}",
        )
    except Exception as exc:
        _log.warning(
            "federation_create_session: remote %s unreachable: %s", remote_url, exc
        )
        raise HTTPException(
            status_code=503,
            detail=f"Remote unreachable: {remote_url}",
        )


# ---------------------------------------------------------------------------
# Static file serving — MUST come after all API routes (first-match-wins)
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

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
import importlib.metadata
import json
import logging
import os
import pathlib
import pwd
import socket
import ssl
import shutil
import subprocess
import sys
import time
from typing import Literal

import httpx
import websockets
from websockets.typing import Subprotocol

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
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
from muxplex.settings import (
    apply_synced_settings,
    get_syncable_settings,
    load_federation_key,
    load_settings,
    patch_settings,
)
from muxplex.identity import load_device_id
from muxplex.ttyd import kill_orphan_ttyd, kill_ttyd, spawn_ttyd, TTYD_PORT

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL: float = float(os.environ.get("POLL_INTERVAL", "2.0"))
SERVER_PORT: int = int(os.environ.get("MUXPLEX_PORT", "8088"))
SETTINGS_SYNC_INTERVAL: int = 15  # sync every ~30 seconds (15 * 2s poll interval)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level task reference
# ---------------------------------------------------------------------------

_poll_task: asyncio.Task | None = None
_federation_client: httpx.AsyncClient | None = None
_settings_sync_counter: int = 0


# ---------------------------------------------------------------------------
# Settings sync
# ---------------------------------------------------------------------------


async def _sync_settings_with_remotes(
    settings: dict, http_client: httpx.AsyncClient
) -> None:
    """Sync settings with all reachable remote instances.

    For each remote:
    - GET /api/settings/sync to retrieve remote timestamp.
    - If remote is newer: adopt remote settings via apply_synced_settings().
    - If local is newer: push local settings via PUT /api/settings/sync.
    - If equal: no action.

    Errors are caught per-remote so one unreachable peer doesn't abort others.
    404/405 responses from older muxplex instances that lack sync endpoints are
    silently skipped.
    """
    local_sync = get_syncable_settings()
    local_ts = local_sync.get("settings_updated_at", 0.0)

    for remote in settings.get("remote_instances", []):
        url = remote.get("url", "").rstrip("/")
        key = remote.get("key", "")
        if not url:
            continue
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            resp = await http_client.get(
                f"{url}/api/settings/sync", headers=headers, timeout=5.0
            )
            if resp.status_code in (404, 405):
                # Older muxplex instance without sync endpoint — skip silently.
                continue
            resp.raise_for_status()
            remote_data = resp.json()
            remote_ts = remote_data.get("settings_updated_at", 0.0)

            if remote_ts > local_ts:
                # Remote is newer — adopt.
                apply_synced_settings(remote_data.get("settings", {}), remote_ts)
                # Refresh local state so subsequent remotes see the updated ts.
                local_sync = get_syncable_settings()
                local_ts = local_sync.get("settings_updated_at", 0.0)
            elif local_ts > remote_ts:
                # Local is newer — push.
                payload = {
                    "settings": {
                        k: local_sync[k]
                        for k in local_sync
                        if k != "settings_updated_at"
                    },
                    "settings_updated_at": local_ts,
                }
                put_resp = await http_client.put(
                    f"{url}/api/settings/sync",
                    json=payload,
                    headers=headers,
                    timeout=5.0,
                )
                if put_resp.status_code == 409:
                    # Remote is newer — let the next sync cycle pull.
                    _log.debug("Settings sync push to %s: 409 (remote is newer)", url)
                else:
                    put_resp.raise_for_status()
            # If equal: no action.
        except Exception as exc:
            _log.warning("Settings sync with %s failed: %s", url, exc)


# ---------------------------------------------------------------------------
# Poll cycle
# ---------------------------------------------------------------------------


async def _run_poll_cycle() -> None:
    """Perform one full poll cycle, all operations executed under state_lock."""
    global _settings_sync_counter
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

        # 10. Fire bell/clear to the active remote for any device viewing a remote
        # session in fullscreen with recent interaction.  Fire-and-forget: errors
        # are logged and do not abort the rest of the poll cycle.
        if _federation_client is not None:
            active_remote_id = state.get("active_remote_id")
            if active_remote_id is not None:
                remote = _lookup_remote_by_device_id(str(active_remote_id))
                if remote is not None:
                    remote_url: str = remote.get("url", "").rstrip("/")
                    remote_key: str = remote.get("key", "")
                    key = remote_key
                    auth_headers = {"Authorization": f"Bearer {key}"} if key else {}
                    now = time.time()
                    for device in state.get("devices", {}).values():
                        viewing_session = device.get("viewing_session")
                        view_mode = device.get("view_mode")
                        last_interaction_at = device.get("last_interaction_at", 0)
                        if (
                            viewing_session
                            and view_mode == "fullscreen"
                            and (now - last_interaction_at) < 60
                        ):
                            bell_clear_url = f"{remote_url}/api/sessions/{viewing_session}/bell/clear"
                            try:
                                await _federation_client.post(
                                    bell_clear_url,
                                    headers=auth_headers,
                                )
                            except Exception as exc:
                                _log.warning(
                                    "federation bell clear failed for %s at %s: %s",
                                    viewing_session,
                                    bell_clear_url,
                                    exc,
                                )

        # 11. Prune devices that haven't sent a heartbeat recently
        prune_devices(state)

        # 12. Atomically persist the updated state
        save_state(state)

    # 13. Periodically sync settings with remote instances (every SETTINGS_SYNC_INTERVAL
    #     poll cycles, ~30 seconds). Runs outside the state_lock to avoid blocking the
    #     poll cycle while waiting on remote HTTP calls.
    _settings_sync_counter += 1
    if _settings_sync_counter >= SETTINGS_SYNC_INTERVAL:
        _settings_sync_counter = 0
        if _federation_client is not None:
            settings = load_settings()
            try:
                await _sync_settings_with_remotes(settings, _federation_client)
            except Exception:
                _log.exception("settings sync cycle error")


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
    global _federation_client

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

    app.state.federation_client = httpx.AsyncClient(
        timeout=5.0,
        follow_redirects=False,
        verify=False,  # nosec B501 — muxplex is a dev tool for LAN/Tailscale use;
        # self-signed certs from `muxplex setup-tls` must be accepted for federation.
        # Bearer token auth handles authorization. Users who need cert verification
        # should use mkcert (CA-trusted) or Tailscale (LE-trusted) certs.
    )
    _federation_client = app.state.federation_client

    yield

    try:
        client = getattr(app.state, "federation_client", None)
        if client is not None:
            await client.aclose()
            _federation_client = None
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

app = FastAPI(
    title="muxplex",
    version=importlib.metadata.version("muxplex"),
    lifespan=lifespan,
)


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
    session_order: list[str] | None = None
    active_session: str | None = None
    active_remote_id: str | None = None
    active_view: str | None = None


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


class SettingsSyncPayload(BaseModel):
    settings: dict
    settings_updated_at: float


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
    """Update fields in the persistent state and return the updated state.

    Only fields explicitly included in the request body are updated;
    omitted fields are left unchanged. Supports: session_order,
    active_session, active_remote_id, active_view.
    """
    async with state_lock:
        state = load_state()
        changed = patch.model_fields_set
        if "session_order" in changed:
            state["session_order"] = patch.session_order
        if "active_session" in changed:
            state["active_session"] = patch.active_session
        if "active_remote_id" in changed:
            state["active_remote_id"] = patch.active_remote_id
        if "active_view" in changed:
            state["active_view"] = patch.active_view
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
    """Create a new session using the new_session_template from settings.

    Substitutes ``{name}`` in the template with the validated payload name,
    runs the command as an async subprocess, and waits up to 30 seconds for
    it to finish.  Returns ``{name, ok: True}`` on success or
    ``{name, ok: False, error: ...}`` with HTTP 500 on failure so that the
    frontend can surface actionable errors instead of silently timing out.

    Some session commands (e.g. ``amplifier-workspace``) create the tmux
    session and then attempt to *attach* to it, which requires a TTY.  When
    launched from muxplex (no TTY available) the attach step fails with a
    non-zero exit code even though the session was successfully created.  To
    handle this, when the command exits non-zero we check whether a tmux
    session with the requested name now exists -- if it does, we treat it as
    a success.
    """
    name = payload.name
    settings = load_settings()
    template = settings["new_session_template"]

    # Pre-flight: check that the base command is on PATH.
    base_cmd = template.split()[0] if template.strip() else ""
    if base_cmd and not shutil.which(base_cmd):
        _log.error(
            "Session command binary not found on PATH: %r (PATH=%s)",
            base_cmd,
            os.environ.get("PATH", ""),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Command not found: {base_cmd}. "
            "Ensure it is installed and in the server's PATH.",
        )

    command = template.replace("{name}", name)
    _log.info("Creating session '%s' with command: %s", name, command)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=30
        )
        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            # Some commands (amplifier-workspace) create the session then
            # try to attach (which fails without a TTY).  If the session
            # exists despite the non-zero exit, treat it as success.
            sessions = await enumerate_sessions()
            if name in sessions:
                _log.info(
                    "Session command exited %d but session '%s' exists -- "
                    "treating as success (likely a TTY-attach failure)",
                    proc.returncode,
                    name,
                )
            else:
                _log.warning(
                    "Session command exited %d: %s (stderr: %s)",
                    proc.returncode,
                    command,
                    stderr_text,
                )
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Session command failed (exit {proc.returncode}): "
                        f"{stderr_text}"
                    )
                    if stderr_text
                    else f"Session command failed with exit code {proc.returncode}",
                )
    except asyncio.TimeoutError:
        _log.info(
            "Session command still running after 30s (may be long-lived): %s",
            command,
        )
        # Long-running session commands (e.g. amplifier-workspace that
        # spawns background processes) may outlive the 30s window.  This is
        # not necessarily an error -- return success and let the frontend
        # poll for the session to appear.
    except HTTPException:
        raise
    except Exception as exc:
        _log.warning("Failed to launch session command %r: %s", command, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to launch command: {exc}",
        )
    return {"name": name, "ok": True}


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
    """Merge known keys from the request body into settings and return updated settings.

    The response is redacted in the same way as ``GET /api/settings`` so that
    sensitive keys are never leaked to the browser.
    """
    body = await request.json()
    updated = patch_settings(body)
    result = copy.deepcopy(updated)
    result["federation_key"] = ""
    for inst in result.get("remote_instances", []):
        if "key" in inst:
            inst["key"] = ""
    return result


@app.get("/api/settings/sync")
async def get_settings_sync() -> dict:
    """Return syncable settings + timestamp for federation sync.

    Authenticated via federation Bearer token (same auth middleware as all other
    non-exempt endpoints). Returns only the keys in SYNCABLE_KEYS plus the
    settings_updated_at timestamp; infrastructure keys (host, port, federation_key,
    etc.) are never included.
    """
    syncable = get_syncable_settings()
    ts = syncable.get("settings_updated_at", 0.0)
    settings = {k: v for k, v in syncable.items() if k != "settings_updated_at"}
    return {"settings": settings, "settings_updated_at": ts}


@app.put("/api/settings/sync")
async def put_settings_sync(payload: SettingsSyncPayload):
    """Accept synced settings from a remote server (newer-wins).

    Compares the incoming timestamp against the local settings_updated_at.
    If the incoming timestamp is strictly newer, applies only the syncable
    keys via apply_synced_settings() and returns 200 with the final state.
    If the incoming timestamp is equal to or older than the local one, returns
    409 (Conflict) with the current local state so the caller can see what
    this instance has.
    """
    current = load_settings()
    local_ts: float = current.get("settings_updated_at", 0.0)

    if payload.settings_updated_at > local_ts:
        apply_synced_settings(payload.settings, payload.settings_updated_at)
        syncable = get_syncable_settings()
        ts = syncable.get("settings_updated_at", 0.0)
        settings_out = {k: v for k, v in syncable.items() if k != "settings_updated_at"}
        return {"settings": settings_out, "settings_updated_at": ts}
    else:
        syncable = get_syncable_settings()
        ts = syncable.get("settings_updated_at", 0.0)
        settings_out = {k: v for k, v in syncable.items() if k != "settings_updated_at"}
        return JSONResponse(
            status_code=409,
            content={"settings": settings_out, "settings_updated_at": ts},
        )


@app.get("/api/instance-info")
async def instance_info() -> dict:
    """Return this instance's display name, device identity, and version.

    Public endpoint (no auth required) — used by remote instances to
    discover peer names, device identity, and verify reachability.
    """
    settings = load_settings()
    # Read fresh so the UI reflects key-file changes without requiring a restart.
    fed_key = load_federation_key()
    return {
        "name": settings["device_name"],
        "device_id": load_device_id(),
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
# Federation helper utilities
# ---------------------------------------------------------------------------


def _lookup_remote_by_device_id(device_id: str) -> dict | None:
    """Return the first remote instance whose ``device_id`` matches *device_id*.

    Primary lookup: iterate ``remote_instances`` and return the first entry
    where ``remote.get('device_id') == device_id``.

    Fallback (transition compatibility): if *device_id* looks like an integer
    (i.e. ``int(device_id)`` succeeds) treat it as a 0-based index into the
    ``remote_instances`` list and return the remote at that position, provided
    the index is in range.

    Returns ``None`` if no match is found.
    """
    settings = load_settings()
    remotes: list[dict] = settings.get("remote_instances", [])

    # Primary: match by device_id field
    for remote in remotes:
        if remote.get("device_id") == device_id:
            return remote

    # Fallback: index-based lookup for transition compatibility
    try:
        idx = int(device_id)
        if 0 <= idx < len(remotes):
            return remotes[idx]
    except (ValueError, TypeError):
        pass

    return None


# ---------------------------------------------------------------------------
# Federation WebSocket proxy — bridges browser to a remote instance's ttyd
# ---------------------------------------------------------------------------


@app.websocket("/federation/{device_id}/terminal/ws")
async def federation_terminal_ws_proxy(websocket: WebSocket, device_id: str) -> None:
    """Proxy WebSocket frames between the browser and a remote muxplex ttyd.

    *device_id* is the device_id string of the remote instance in
    settings.  Authenticates to the remote instance using the configured
    ``key`` field via a Bearer header.

    Auth check uses the same cookie + bearer pattern as terminal_ws_proxy.
    Closes with code 4004 if device_id does not match any remote.
    """
    # Auth check before accepting — same pattern as terminal_ws_proxy
    if not await _ws_auth_check(websocket):
        return

    # Look up remote instance by device_id
    remote = _lookup_remote_by_device_id(device_id)
    if remote is None:
        await websocket.close(code=4004)
        return
    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")

    # Convert http(s) URL to ws(s)
    if remote_url.startswith("https://"):
        ws_url = "wss://" + remote_url[8:] + "/terminal/ws"
    elif remote_url.startswith("http://"):
        ws_url = "ws://" + remote_url[7:] + "/terminal/ws"
    else:
        ws_url = remote_url + "/terminal/ws"  # assume already ws:// or wss://

    # Build an SSL context that skips verification for self-signed certs on
    # remote instances.  Same rationale as httpx verify=False: federation
    # peers may use self-signed or Tailscale-issued certs that don't pass the
    # system CA store.  None tells websockets to use default behaviour (no
    # TLS) for plain ws:// URLs.
    ssl_context: ssl.SSLContext | None = None
    if ws_url.startswith("wss://"):
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    await websocket.accept(subprotocol="tty")

    auth_headers = {"Authorization": f"Bearer {remote_key}"} if remote_key else {}
    try:
        async with websockets.connect(
            ws_url,
            subprotocols=[Subprotocol("tty")],
            additional_headers=auth_headers,
            ssl=ssl_context,
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


# Module-level cache: remote_device_id → {"sessions": [...], "fail_count": int}
# Populated by fetch_remote() on every successful poll; returned on transient failures
# so a single slow/dropped request doesn't immediately evict a device from the UI.
_federation_cache: dict[str, dict] = {}
_FEDERATION_GRACE_FAILURES = 3  # consecutive failures before marking unreachable


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
    local_device_id: str = load_device_id()
    remote_instances: list[dict] = settings.get("remote_instances", [])

    # Build local sessions with deviceId/deviceName/remoteId/sessionKey tags
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
                "deviceId": local_device_id,
                "deviceName": local_device_name,
                "remoteId": None,
                "sessionKey": f"{local_device_id}:{name}",
            }
        )

    if not remote_instances:
        return local_sessions

    # Fetch remote sessions concurrently
    http_client: httpx.AsyncClient = request.app.state.federation_client

    async def fetch_remote(i: int, remote: dict) -> list[dict]:
        """Fetch /api/sessions from a remote instance, returning session dicts or a status entry.

        On success: cache the result and return tagged sessions (or {status: 'empty'} if none).
        On transient failure: return cached sessions for up to _FEDERATION_GRACE_FAILURES
        consecutive failures before promoting to {status: 'unreachable'}.
        """
        url: str = remote.get("url", "")
        key: str = remote.get("key", "")
        remote_name: str = remote.get("name", url)
        remote_device_id: str = remote.get("device_id", str(i))
        try:
            resp = await http_client.get(
                f"{url.rstrip('/')}/api/sessions",
                headers={"Authorization": f"Bearer {key}"} if key else {},
            )
            if resp.status_code in (401, 403):
                # Auth failure — clear cache so stale data is not served
                _federation_cache.pop(remote_device_id, None)
                return [
                    {
                        "status": "auth_failed",
                        "deviceId": remote_device_id,
                        "remoteId": remote_device_id,
                        "deviceName": remote_name,
                    }
                ]
            resp.raise_for_status()
            sessions = resp.json()
            # Tag each session with deviceId, deviceName, remoteId, and unique sessionKey
            tagged = [
                {
                    **s,
                    "deviceId": remote_device_id,
                    "deviceName": remote_name,
                    "remoteId": remote_device_id,
                    "sessionKey": f"{remote_device_id}:{s.get('name', '')}",
                }
                for s in sessions
            ]
            # Update cache on every successful poll (even empty)
            _federation_cache[remote_device_id] = {"sessions": tagged, "fail_count": 0}
            if not tagged:
                # Device is online but has zero tmux sessions — show a status tile
                # rather than making the device completely invisible.
                return [
                    {
                        "status": "empty",
                        "deviceId": remote_device_id,
                        "remoteId": remote_device_id,
                        "deviceName": remote_name,
                    }
                ]
            return tagged
        except httpx.HTTPStatusError:
            cached = _federation_cache.get(remote_device_id)
            if cached and cached["fail_count"] < _FEDERATION_GRACE_FAILURES:
                cached["fail_count"] += 1
                return cached["sessions"]
            return [
                {
                    "status": "unreachable",
                    "deviceId": remote_device_id,
                    "remoteId": remote_device_id,
                    "deviceName": remote_name,
                }
            ]
        except Exception as exc:
            _log.warning("Unexpected error fetching remote %s: %s", url, exc)
            cached = _federation_cache.get(remote_device_id)
            if cached and cached["fail_count"] < _FEDERATION_GRACE_FAILURES:
                cached["fail_count"] += 1
                return cached["sessions"]
            return [
                {
                    "status": "unreachable",
                    "deviceId": remote_device_id,
                    "remoteId": remote_device_id,
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


@app.post("/api/federation/{device_id}/connect/{session_name}")
async def federation_connect(
    device_id: str, session_name: str, request: Request
) -> dict:
    """Proxy a connect POST to a remote instance to spawn its ttyd.

    Looks up the remote by device_id string via ``_lookup_remote_by_device_id``,
    sends ``POST {remote_url}/api/sessions/{session_name}/connect`` with a
    Bearer auth header, and returns the remote's JSON response.

    Raises HTTP 404 if ``device_id`` does not match any remote instance.
    """
    remote = _lookup_remote_by_device_id(device_id)
    if remote is None:
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{device_id}' not found",
        )

    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions/{session_name}/connect"

    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"} if remote_key else {},
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
            detail=f"Remote unreachable: {remote_url} ({type(exc).__name__}: {exc})",
        )


@app.post("/api/federation/{device_id}/sessions/{session_name}/bell/clear")
async def federation_bell_clear(
    device_id: str, session_name: str, request: Request
) -> dict:
    """Proxy a bell-clear POST to a remote instance.

    Looks up the remote by device_id string via ``_lookup_remote_by_device_id``,
    sends ``POST {remote_url}/api/sessions/{session_name}/bell/clear`` with a
    Bearer auth header, and returns the remote's JSON response.

    Raises HTTP 404 if ``device_id`` does not match any remote instance.
    """
    remote = _lookup_remote_by_device_id(device_id)
    if remote is None:
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{device_id}' not found",
        )

    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions/{session_name}/bell/clear"

    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"} if remote_key else {},
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
            detail=f"Remote unreachable: {remote_url} ({type(exc).__name__}: {exc})",
        )


@app.post("/api/federation/{device_id}/sessions")
async def federation_create_session(
    device_id: str, payload: CreateSessionPayload, request: Request
) -> dict:
    """Proxy a create-session POST to a remote instance.

    Looks up the remote by device_id string via ``_lookup_remote_by_device_id``,
    sends ``POST {remote_url}/api/sessions`` with a Bearer auth header and JSON
    body ``{name: ...}``, and returns the remote's JSON response.

    Raises HTTP 404 if ``device_id`` does not match any remote instance,
    503 when remote is unreachable, 502 when remote returns HTTP error.
    """
    remote = _lookup_remote_by_device_id(device_id)
    if remote is None:
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{device_id}' not found",
        )
    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions"
    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"} if remote_key else {},
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
            detail=f"Remote unreachable: {remote_url} ({type(exc).__name__}: {exc})",
        )


@app.delete("/api/federation/{device_id}/sessions/{session_name}")
async def federation_delete_session(
    device_id: str, session_name: str, request: Request
) -> dict:
    """Proxy a delete-session DELETE to a remote instance.

    Looks up the remote by device_id string via ``_lookup_remote_by_device_id``,
    sends ``DELETE {remote_url}/api/sessions/{session_name}`` with a Bearer auth
    header, and returns the remote's JSON response.

    Raises HTTP 404 if ``device_id`` does not match any remote instance,
    503 when remote is unreachable, 502 when remote returns HTTP error.
    """
    remote = _lookup_remote_by_device_id(device_id)
    if remote is None:
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{device_id}' not found",
        )
    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions/{session_name}"
    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.delete(
            url,
            headers={"Authorization": f"Bearer {remote_key}"} if remote_key else {},
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
            "federation_delete_session: remote %s unreachable: %s", remote_url, exc
        )
        raise HTTPException(
            status_code=503,
            detail=f"Remote unreachable: {remote_url} ({type(exc).__name__}: {exc})",
        )


# ---------------------------------------------------------------------------
# Static file serving — MUST come after all API routes (first-match-wins)
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

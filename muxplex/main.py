"""
muxplex — FastAPI application for the tmux session dashboard.

Entry point for the muxplex server. Exposes:
    GET /health  →  {"status": "ok"}

Background poll loop reconciles tmux session state every POLL_INTERVAL seconds.
"""

import asyncio
import contextlib
import copy
import hashlib
import hmac
import importlib.metadata
import json
import logging
import os
import pathlib
import pwd
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import time
from typing import Literal
from urllib.parse import quote

import httpx
import websockets
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.responses import RedirectResponse, Response
from starlette.types import Scope
from websockets.typing import Subprotocol

from muxplex.auth import (
    AuthMiddleware,
    authenticate_pam,
    create_session_cookie,
    generate_and_save_password,
    get_password_path,
    load_or_create_secret,
    load_password,
    pam_available,
    validate_next_path,
    verify_session_cookie,
)
from muxplex.bells import apply_bell_clear_rule, needs_attention, process_bell_flags
from muxplex.breaker import CircuitBreaker
from muxplex.identity import load_device_id
from muxplex.pruning import load_pruning_state, save_pruning_state
from muxplex.sessions import (
    DEFAULT_CAPTURE_LINES,
    MAX_CAPTURE_LINES,
    capture_pane,
    ensure_history_retention,
    enumerate_sessions,
    get_session_activity,
    get_session_list,
    get_snapshots,
    is_valid_session_name,
    run_tmux,
    snapshot_all,
    tmux_env,
    update_session_cache,
)
from muxplex.settings import (
    DestructiveSettingsWriteRejected,
    apply_synced_settings,
    get_local_ca_cert_path,
    get_syncable_settings,
    load_federation_key,
    load_settings,
    patch_settings,
    resolve_tmux_socket_dir,
    save_settings,
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
from muxplex.terminal_input import (
    ALLOWED_KEYS,
    MAX_KEYS,
    MAX_TEXT_BYTES,
    build_send_key_argv,
    build_send_text_argv,
    redact_preview,
    session_matches_allowlist,
)
from muxplex.tls import get_local_ca_cert_bytes
from muxplex.ttyd import TTYD_PORT, kill_orphan_ttyd, kill_ttyd, spawn_ttyd
from muxplex.views import (
    assess_views_destruction,
    filter_visible,
    normalize_session_keys,
    prune_stale_keys,
)

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

# Bell-hook self-healing state (see _arm_bell_hook()). Starts unarmed;
# _run_poll_cycle() retries registration each cycle ONLY while this is False,
# so a startup failure is retried until it heals, but a steady-state success
# costs nothing further -- no per-cycle tmux subprocess once armed. Exposed
# via GET /api/instance-info so an operator/agent can tell bells are (not)
# armed without grepping logs.
_bell_hook_armed: bool = False
_bell_hook_last_error: str | None = None

# Tasks currently running a terminal WebSocket proxy relay.  Tracked so the
# lifespan shutdown can cancel any still-open relays: a relay blocked on
# ttyd output would otherwise keep uvicorn's "waiting for connections to
# close" phase alive until systemd's stop timeout SIGKILLs the process.
_ws_proxy_tasks: set[asyncio.Task] = set()


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
            # Absent on a peer that predates views_updated_at (see
            # SettingsSyncPayload) -- None here means the same thing it means
            # in apply_synced_settings(): "no signal, fall back to
            # pre-existing behavior."
            remote_views_ts = remote_data.get("views_updated_at")

            if remote_ts > local_ts:
                # Remote is newer — adopt. The destructive-write backstop
                # inside apply_synced_settings() runs unconditionally here
                # too, and a rejection is just another per-remote failure
                # caught by the except below (leaves local untouched, next
                # cycle retries).
                apply_synced_settings(
                    remote_data.get("settings", {}), remote_ts, remote_views_ts
                )
                # Refresh local state so subsequent remotes see the updated ts.
                local_sync = get_syncable_settings()
                local_ts = local_sync.get("settings_updated_at", 0.0)
            elif local_ts > remote_ts:
                # Local is newer — push.
                payload = {
                    "settings": {
                        k: local_sync[k]
                        for k in local_sync
                        if k not in ("settings_updated_at", "views_updated_at")
                    },
                    "settings_updated_at": local_ts,
                    "views_updated_at": local_sync.get("views_updated_at", 0.0),
                }
                put_resp = await http_client.put(
                    f"{url}/api/settings/sync",
                    json=payload,
                    headers=headers,
                    timeout=5.0,
                )
                if put_resp.status_code == 409:
                    # Remote is newer, or rejected our push as destructive —
                    # either way, let the next sync cycle sort it out.
                    _log.debug(
                        "Settings sync push to %s: 409 (%s)",
                        url,
                        "backstop rejection"
                        if put_resp.json().get("backstop")
                        else "remote is newer",
                    )
                else:
                    put_resp.raise_for_status()
            # If equal: no action.
        except Exception as exc:
            _log.warning("Settings sync with %s failed: %s", url, exc)


# ---------------------------------------------------------------------------
# Bell hook registration
# ---------------------------------------------------------------------------


async def _arm_bell_hook() -> bool:
    """(Re-)register tmux's ``alert-bell`` hook so a bell forwards to
    ``POST /api/sessions/{name}/bell`` (see ``receive_bell()``).

    Idempotent: ``set-hook -g`` simply overwrites whatever hook is already
    set, so calling this repeatedly is always safe.

    Self-healing without a steady-state tax: this is retried by
    ``_run_poll_cycle`` every cycle *only while unarmed* (see
    ``_bell_hook_armed``), which is what actually heals the common failure
    -- tmux not running yet when muxplex starts. Once armed, callers stop
    retrying, so a healthy process pays this subprocess cost once, not every
    2s for its lifetime.

    Failure is never silent: every failure is logged at WARNING with the
    tmux error (unlike the previous ``except Exception: pass``), and the
    outcome is recorded in ``_bell_hook_armed`` / ``_bell_hook_last_error``
    so ``GET /api/instance-info`` reflects it without grepping logs.

    Returns:
        True if tmux accepted the hook registration, False otherwise.
    """
    global _bell_hook_armed, _bell_hook_last_error
    try:
        await run_tmux(
            "set-hook",
            "-g",
            "alert-bell",
            f"run-shell 'curl -sfo /dev/null -X POST http://localhost:{SERVER_PORT}/api/sessions/#{{session_name}}/bell || true'",
        )
    except Exception as exc:
        _bell_hook_last_error = str(exc)
        _log.warning(
            "bell hook registration failed, bells will not fire until this "
            "heals (retried each poll cycle while unarmed): %s",
            exc,
        )
        _bell_hook_armed = False
        return False

    _bell_hook_last_error = None
    _bell_hook_armed = True
    return True


# ---------------------------------------------------------------------------
# Poll cycle
# ---------------------------------------------------------------------------


async def _run_poll_cycle() -> None:
    """Perform one full poll cycle, all operations executed under state_lock."""
    global _settings_sync_counter

    # Self-healing bell hook: retry registration only while unarmed, so the
    # cost is paid only during the (typically single 2s cycle) window before
    # tmux comes up -- never a per-cycle tax once armed. See _arm_bell_hook().
    if not _bell_hook_armed:
        await _arm_bell_hook()

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

    # 13b. Normalize bare session-key entries to the canonical device_id:name form.
    #
    #      Phase 1 added normalize_session_keys() in views.py but it was never
    #      wired into the runtime.  Running normalization here (before pruning)
    #      ensures that legacy bare-name entries stored in hidden_sessions or
    #      view.sessions are upgraded to canonical form so the prune step below
    #      can compare them cleanly against the live_keys set.
    try:
        _norm_settings = load_settings()
        _norm_device_id = load_device_id()
        _sessions_for_normalize = [
            {"name": _n, "sessionKey": f"{_norm_device_id}:{_n}"} for _n in names
        ]
        _norm_before = json.dumps(_norm_settings, sort_keys=True)
        normalize_session_keys(_norm_settings, _sessions_for_normalize)
        _norm_after = json.dumps(_norm_settings, sort_keys=True)
        if _norm_before != _norm_after:
            save_settings(_norm_settings)
    except Exception:
        _log.exception("session-key normalize cycle error")

    # 14. Prune stale session keys from views and hidden_sessions.
    #
    #     Federation-aware, positive-knowledge pruning (see views.py's
    #     prune_stale_keys docstring for the full rule). A key
    #     "<device_id>:<name>" is only ever evaluated for pruning when the
    #     owning device's session list is CURRENTLY KNOWN to this instance:
    #       - our own local_device_id: always evaluable (names is authoritative).
    #       - a remote device_id: evaluable ONLY if that device currently has a
    #         fresh entry in _federation_cache (the same cache that backs
    #         GET /api/federation/sessions -- populated whenever any client,
    #         PWA/deck/agent, polls that endpoint) AND its fail streak hasn't
    #         exceeded the reachability grace threshold used elsewhere in this
    #         module. If reachable, its live session keys are merged into
    #         live_keys so "reachable and genuinely absent" starts the grace
    #         clock; "reachable and present" clears it.
    #       - a remote device with NO current cache entry (never polled, or
    #         its entry was popped on auth failure) is UNKNOWN, not dead: its
    #         keys are never pruned and never accrue grace-clock time. This is
    #         what prevents an offline laptop's view membership from being
    #         silently erased by every OTHER device in the fleet, each of
    #         which would otherwise see only ITS OWN local sessions and
    #         conclude (wrongly) that the offline device's sessions are gone.
    #       - legacy bare-name entries (no device_id: prefix) have no
    #         determinable owner and keep the pre-existing behavior
    #         unconditionally (evaluated directly against live_keys).
    #
    #     Pruning bookkeeping (first-missed-at timestamps) is NEVER written to
    #     settings.json and is NEVER sent to peers — it lives in pruning.json.
    #     The prune action (removing dead keys from settings) IS a normal
    #     settings write that syncs via the existing LWW mechanism, and is
    #     still subject to the destructive-write backstop (views.py) like any
    #     other settings write — a mass-prune that would collapse views is
    #     rejected, not silently applied.
    try:
        _prune_settings = load_settings()
        _prune_state = load_pruning_state()
        _grace_hours = float(_prune_settings.get("stale_key_grace_hours", 24.0))
        _grace_seconds = _grace_hours * 3600.0

        _local_device_id = load_device_id()
        _live_keys: set[str] = set()
        for _name in names:
            # Include both the bare name (for legacy stored entries) and the
            # canonical device_id:name form.
            _live_keys.add(_name)
            _live_keys.add(f"{_local_device_id}:{_name}")

        # Merge in live session keys for every remote device CURRENTLY KNOWN
        # to us via the federation session cache, and record which device_ids
        # those are. A device absent from _federation_cache, or whose fail
        # streak has exceeded the reachability grace threshold (same signal
        # GET /api/federation/sessions uses to report "unreachable"), is
        # excluded -- its keys stay "unknown" to the pruner below.
        _known_remote_device_ids: set[str] = set()
        for _remote_device_id, _cache_entry in _federation_cache.items():
            if _cache_entry.get("fail_count", 0) >= _FEDERATION_GRACE_FAILURES:
                continue
            _known_remote_device_ids.add(_remote_device_id)
            for _remote_sess in _cache_entry.get("sessions") or []:
                _remote_key = _remote_sess.get("sessionKey")
                if _remote_key:
                    _live_keys.add(_remote_key)

        # Snapshot pre-prune views so a mass prune can be assessed against the
        # SAME destructive-write backstop that guards PATCH /api/settings and
        # federation sync (views.assess_views_destruction). The prune ACTION
        # writes settings directly via save_settings() -- it does not go
        # through patch_settings()/apply_synced_settings(), so it must run
        # this check itself rather than inherit it for free.
        _views_before_prune = _prune_settings.get("views")

        _prune_settings, _prune_state, _prune_changed = prune_stale_keys(
            _prune_settings,
            _live_keys,
            pruning_state=_prune_state,
            grace_seconds=_grace_seconds,
            local_device_id=_local_device_id,
            known_remote_device_ids=_known_remote_device_ids,
        )

        _prune_destructive = False
        if _prune_changed:
            _prune_assessment = assess_views_destruction(
                _views_before_prune, _prune_settings.get("views")
            )
            _prune_destructive = _prune_assessment.destructive
            if _prune_destructive:
                # Refuse to persist ANYTHING this cycle -- not settings, not
                # pruning_state. Automatic background pruning must never be
                # the thing that collapses views; unlike PATCH /api/settings,
                # there is no `allow_destructive` override on this path (a
                # background loop cannot consent to a bulk deletion on a
                # human's behalf). Leaving pruning_state untouched means the
                # exact same situation reproduces next cycle -- visible in
                # logs, not silently applied and not silently dropped into a
                # half-written state.
                _log.error(
                    "stale-key prune: refusing catastrophic prune (backstop): %s "
                    "(before=%d views/%d members, after=%d views/%d members)",
                    _prune_assessment.reason,
                    _prune_assessment.before_views,
                    _prune_assessment.before_members,
                    _prune_assessment.after_views,
                    _prune_assessment.after_members,
                )

        if not _prune_destructive:
            save_pruning_state(_prune_state)
            if _prune_changed:
                # Stale keys were removed and passed the backstop check —
                # persist (triggers LWW sync on next cycle).
                save_settings(_prune_settings)
    except Exception:
        _log.exception("stale-key prune cycle error")


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

    # One-line frontend identity so "which JS is this server serving?" is a
    # glance at the startup log, not a debugging session.
    _app_js = _FRONTEND_DIR / "app.js"
    _log.info(
        "frontend: app.js %s",
        hashlib.md5(_app_js.read_bytes(), usedforsecurity=False).hexdigest()[:8],
    )

    # Startup: kill any orphaned ttyd from a previous muxplex run, then
    # start the background poll loop.
    await kill_orphan_ttyd()
    _poll_task = asyncio.create_task(_poll_loop())

    # Register tmux alert-bell hook so bells are detected even when clients are
    # attached (window_bell_flag is only set when no client is watching; the
    # hook fires always). tmux commonly isn't running yet at this exact point
    # in a fresh boot, so this failing here is the expected common case, not
    # an edge case -- _arm_bell_hook() records the outcome and logs loudly on
    # failure instead of swallowing it, and _run_poll_cycle() retries every
    # cycle until it heals (self-healing, no per-cycle tax once armed).
    await _arm_bell_hook()

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

    # Shutdown — ordered and bounded so a SIGTERM (systemctl stop/restart)
    # completes in ~1s instead of hanging past systemd's 10s TimeoutStopSec
    # and getting SIGKILLed.  Order: cancel background work first (the poll
    # loop may be mid federation request on the shared client), unblock any
    # open terminal relays (they otherwise keep uvicorn's "waiting for
    # connections to close" phase alive forever), then stop the ttyd child,
    # then close the shared HTTP client.
    to_cancel: list[asyncio.Task] = []
    if _poll_task is not None:
        _poll_task.cancel()
        to_cancel.append(_poll_task)
    n_relays = len(_ws_proxy_tasks)
    for task in list(_ws_proxy_tasks):
        task.cancel()
        to_cancel.append(task)
    if to_cancel:
        # Bounded: cancellation normally completes in milliseconds; don't
        # wait forever on a task stuck in un-cancellable cleanup.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*to_cancel, return_exceptions=True), timeout=2.0
            )
    _poll_task = None

    try:
        await asyncio.wait_for(kill_ttyd(), timeout=3.0)
    except Exception:
        _log.exception("ttyd shutdown error")

    try:
        client = getattr(app.state, "federation_client", None)
        if client is not None:
            await client.aclose()
    except Exception:
        _log.exception("federation_client aclose error")
    _federation_client = None

    _log.info(
        "shutdown: cancelled poll loop, closed %d terminal relay(s), stopped ttyd",
        n_relays,
    )


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


class SessionInputPayload(BaseModel):
    """Body for POST /api/sessions/{name}/input.

    text  -- literal text typed into the pane via `tmux send-keys -l`
             (never interpreted by tmux or a shell; see terminal_input.py).
    enter -- press Enter after text/keys (default False).
    keys  -- named special keys from terminal_input.ALLOWED_KEYS, sent in
             order after *text*. Anything outside the allowlist is a 400.
    lines -- optional read-back depth override for the pane snapshot
             returned in the same response. None (the default) preserves
             the original behavior (sessions.DEFAULT_CAPTURE_LINES, i.e.
             30). Must be within [1, sessions.MAX_CAPTURE_LINES] or the
             request is a 400 -- an agent that just ran a long command
             (e.g. `pytest -v`) can ask for deeper scrollback in the same
             call that triggered it.

    Send order: text -> keys -> enter. At least one of the three must be
    provided (empty text + no keys + enter=False is a 400).
    """

    text: str = ""
    enter: bool = False
    keys: list[str] = []
    lines: int | None = None


class SettingsSyncPayload(BaseModel):
    settings: dict
    settings_updated_at: float
    # Additive, optional: a legacy peer's request simply omits it and
    # apply_synced_settings() falls back to pre-existing behavior (see that
    # function's docstring for the full views-specific-conflict-resolution
    # story). Never required -- Pydantic defaults it to None.
    views_updated_at: float | None = None


# ---------------------------------------------------------------------------
# Frontend directory + hostname
# ---------------------------------------------------------------------------

_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"

# Short hostname (no domain) injected into page titles so browser tabs show
# which machine each muxplex instance is running on.
_HOSTNAME = socket.gethostname().split(".")[0]

# Canonical version string — sourced from package metadata (same as `app.version`
# and the `doctor` command).  Used to append `?v=<version>` to every static-asset
# URL so browsers immediately pick up new code on each release.
_UI_VERSION: str = importlib.metadata.version("muxplex")

# Matches src="/<path>" and href="/<path>" in served HTML, excluding /api/ URLs.
# Used by index_page() to inject cache-busting version query parameters.
_ASSET_URL_RE = re.compile(r'((?:src|href)=")((?!/api/)/[^"?#]*)')


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Simple liveness check."""
    return {"status": "ok"}


@app.get("/api/state")
async def get_state() -> dict:
    """Return the full persistent state, plus settings_updated_at.

    settings_updated_at mirrors settings.settings_updated_at (settings.py) --
    it is merged in here at request time, NOT persisted in state.json. This
    lets any client already polling /api/state (PWA, muxplex-deck, agents)
    detect a settings change -- including view membership edits, which are
    otherwise only visible via a dedicated GET /api/settings fetch -- without
    adding a second poll. Purely additive: existing consumers that don't look
    for this key are unaffected.
    """
    state = await read_state()
    state["settings_updated_at"] = load_settings().get("settings_updated_at", 0.0)
    return state


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
    """Return list of sessions with name, snapshot, bell, and last-activity data."""
    names = get_session_list()
    snapshots = get_snapshots()
    activity = get_session_activity()
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
                "last_activity_at": activity.get(name),
            }
        )
    return result


@app.get("/api/sessions/{name}")
async def get_session_snapshot(name: str, lines: int = DEFAULT_CAPTURE_LINES) -> dict:
    """Return a single session's pane content at a caller-chosen depth.

    Unlike GET /api/sessions -- a shared, ~2s-cycle poll cache fixed at
    DEFAULT_CAPTURE_LINES, consumed simultaneously by the PWA, muxplex-deck,
    and agents alike -- this does ONE fresh, live `capture-pane` call scoped
    to *name*. It exists so a caller (typically an agent that just ran a
    long command via POST .../input, e.g. `pytest -v`) can read deep
    scrollback on demand without waiting for the next poll cycle and without
    changing what every other client sees from the bulk cache.

    `lines` must be within [1, MAX_CAPTURE_LINES] (400 otherwise) -- an
    unbounded value here would let a single request pull arbitrarily large
    scrollback, a real cost on a server that's also polling every other
    session on its own cycle. Sessions are created with their tmux
    `history-limit` raised well above MAX_CAPTURE_LINES (see
    sessions.ensure_history_retention) specifically so a max-depth request
    has real backing data instead of tmux's own, possibly much lower,
    default silently truncating it.

    Raises 404 if *name* is not an exact member of the known session set
    (same fail-closed pattern as connect/delete/input).
    """
    _require_valid_session_name(name)
    if not (1 <= lines <= MAX_CAPTURE_LINES):
        raise HTTPException(
            status_code=400,
            detail=f"lines must be between 1 and {MAX_CAPTURE_LINES} (got {lines})",
        )
    known = get_session_list()
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    snapshot = await capture_pane(name, lines)
    activity = get_session_activity()
    state = await read_state()
    session_state = state.get("sessions", {}).get(name, {})
    bell = session_state.get("bell", empty_bell())
    return {
        "name": name,
        "snapshot": snapshot,
        "lines": lines,
        "bell": bell,
        "last_activity_at": activity.get(name),
    }


def _attention_order(sessions: list[dict]) -> list[dict]:
    """Tiered ordering for GET /api/view?sort=attention.

    Tier 1: needs_attention sessions, ordered by bell.last_fired_at desc.
    Tier 2: the active session, if it wasn't already placed in tier 1
        (at most one entry).
    Tier 3: everything else, ordered by last_activity_at desc (sessions
        with no known activity timestamp sort last).

    All ties (including "all None") preserve the incoming order -- Python's
    sort is stable, and remains so with reverse=True.
    """
    tier1 = sorted(
        (s for s in sessions if s["needs_attention"]),
        key=lambda s: s["bell"].get("last_fired_at") or 0,
        reverse=True,
    )
    tier1_names = {s["name"] for s in tier1}
    remaining = [s for s in sessions if s["name"] not in tier1_names]

    tier2 = [s for s in remaining if s["active"]]
    tier2_names = {s["name"] for s in tier2}
    tier3_source = [s for s in remaining if s["name"] not in tier2_names]

    tier3 = sorted(
        tier3_source,
        key=lambda s: (s["last_activity_at"] is not None, s["last_activity_at"] or 0),
        reverse=True,
    )
    return tier1 + tier2 + tier3


@app.get("/api/view")
async def get_view(sort: str | None = None) -> dict:
    """Return the server-resolved current view: filtered, sorted sessions
    plus view metadata.

    This is the canonical home for view-resolution semantics that the PWA,
    muxplex-deck, and future agent clients would otherwise each have to
    re-implement: `filter_visible` membership/hidden rules, the
    needs-attention bell predicate, and sort ordering. See AGENTS.md
    "Semantics external clients re-implement" for the rationale; new
    clients should prefer this endpoint over re-deriving these rules.

    Query params:
        sort: omitted -> honor `settings.sort_order` the same way the PWA
            does today (`"alphabetical"` sorts by name; any other value
            preserves /api/sessions enumeration order, reported back as
            `"server"`). `"attention"` requests tiered ordering: sessions
            needing attention first (freshest bell first), then the active
            session (if not already surfaced), then the rest ordered by
            last_activity_at descending (unknown activity sorts last). Any
            other value is rejected with 400 -- no silent fallback.

    Response shape:
        {
          "view": <active_view, echoed verbatim>,
          "views": ["all", <user view names, settings order>],
          "sort": "server" | "alphabetical" | "attention",
          "sessions": [
            {"name", "active", "needs_attention", "bell", "last_activity_at"}
          ],
        }

    `views` deliberately excludes "hidden": it remains addressable as an
    active_view value (GET /api/state, PATCH /api/state), but it is not a
    browsable/cyclable view alongside "all" and user-defined views.

    Deliberately light: no pane snapshots here (those stay on
    GET /api/sessions) so this endpoint stays cheap for frequent polling
    (e.g. a Stream Deck dial).

    Scope: local sessions only. Unlike GET /api/federation/sessions, this
    answers "what does *this* device's current view look like" -- remote
    peers are not merged in. The shape is additive, so a federated variant
    can be added later without changing it.

    An unknown/deleted active_view is not an error: `sessions` comes back
    empty while `view` still echoes the (now unresolvable) name, matching
    current PWA behavior for this case.
    """
    if sort is not None and sort != "attention":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sort value: {sort!r}. Use 'attention' or omit the parameter.",
        )

    settings = load_settings()
    state = await read_state()
    active_view: str = state.get("active_view", "all")
    active_session = state.get("active_session")

    device_id = load_device_id()
    names = get_session_list()
    activity = get_session_activity()
    raw_sessions = [
        {
            "name": name,
            "sessionKey": f"{device_id}:{name}",
            "bell": state.get("sessions", {}).get(name, {}).get("bell", empty_bell()),
            "last_activity_at": activity.get(name),
        }
        for name in names
    ]

    visible = filter_visible(raw_sessions, settings, active_view)

    resolved = [
        {
            "name": s["name"],
            "active": s["name"] == active_session,
            "needs_attention": needs_attention(s["bell"]),
            "bell": s["bell"],
            "last_activity_at": s["last_activity_at"],
        }
        for s in visible
    ]

    if sort == "attention":
        resolved = _attention_order(resolved)
        applied_sort = "attention"
    elif settings.get("sort_order") == "alphabetical":
        resolved = sorted(resolved, key=lambda s: s["name"])
        applied_sort = "alphabetical"
    else:
        applied_sort = "server"

    views = ["all"] + [v.get("name", "") for v in (settings.get("views") or [])]

    return {
        "view": active_view,
        "views": views,
        "sort": applied_sort,
        "sessions": resolved,
    }


def _require_valid_session_name(name: str) -> None:
    """Reject a client-supplied session name that fails the safe-charset allowlist.

    This is the security boundary for every endpoint that forwards a
    client-supplied session name to a subprocess (create/delete/connect, and any
    future terminal-input endpoint). A name that passes ``is_valid_session_name``
    contains no shell metacharacters, whitespace, or ``:`` -- so it cannot break
    out of a shell template or mis-target a ``tmux -t`` argument.

    Raises HTTPException(400) BEFORE any substitution or subprocess call. The raw
    name is deliberately NOT echoed back in the error detail (an invalid name may
    contain a payload we don't want to reflect).
    """
    if not is_valid_session_name(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid session name. Allowed characters: letters, digits, "
                "and _ . - (1-64 characters)."
            ),
        )


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
    # Security boundary: reject unsafe names before they reach the shell.
    _require_valid_session_name(name)
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

    # new_session_template is an arbitrary user shell command with a {name}
    # placeholder (default `tmux new-session -d -s {name}`, but users configure
    # e.g. `amplifier-workspace {name}`), so this path stays shell-based to
    # preserve that feature -- switching to a fixed argv list would break every
    # custom template. Injection is closed by two layers: (1) the allowlist
    # (_require_valid_session_name) guarantees the name has no shell
    # metacharacters; (2) shlex.quote() is applied as defense-in-depth in case
    # the allowlist is ever loosened. For an allowlist-valid name shlex.quote()
    # is a no-op, so existing custom templates behave identically.
    command = template.replace("{name}", shlex.quote(name))
    _log.info("Creating session '%s' with command: %s", name, command)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=tmux_env(),
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

    # Raise this session's tmux history-limit so a later caller-controlled
    # deep read (GET /api/sessions/{name}?lines=..., or /input's `lines`
    # field) has real scrollback to return instead of silently truncating
    # at whatever this host's tmux.conf happens to default to. Best-effort:
    # never fails session creation itself (see ensure_history_retention's
    # docstring).
    await ensure_history_retention(name)
    return {"name": name, "ok": True}


@app.post("/api/sessions/{name}/connect")
async def connect_session(name: str) -> dict:
    """Connect to a tmux session via ttyd.

    Kills any existing ttyd process, spawns a new one attached to *name*,
    and updates the active_session in persistent state.

    Returns {active_session: name, ttyd_port: 7682}.
    Raises HTTP 400 if *name* fails the session-name allowlist.
    Raises HTTP 404 if *name* is not an exact match in the known session list.
    """
    _require_valid_session_name(name)
    # Fail closed: reject unless *name* is an exact member of the known set. An
    # empty/unavailable cache means "no known sessions", so every target is
    # rejected -- the guard must not evaporate when the session list is empty
    # (startup or a list-sessions hiccup). Exact `in` membership also prevents
    # tmux `-t` prefix-matching a different session.
    known = get_session_list()
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    # Same-session short-circuit: if *name* is already the active session and
    # ttyd is still accepting connections, kill+respawn would only churn a PTY
    # every attached client already works against. Return current state (~2ms)
    # so redundant client re-connects (PWA follow-open, deck double-press) are
    # free. The <1ms TCP listening probe keeps this restart-safe: a truly-dead
    # ttyd still falls through to a full respawn.
    async with state_lock:
        current = load_state().get("active_session")
    if name == current and _ttyd_is_listening():
        _log.info(
            "Session '%s' already active and ttyd listening; skipping respawn", name
        )
        return {"active_session": name, "ttyd_port": TTYD_PORT}

    _log.info("Connecting to session '%s'", name)
    await kill_ttyd()
    await spawn_ttyd(name)

    async with state_lock:
        state = load_state()
        state["active_session"] = name
        save_state(state)

    return {"active_session": name, "ttyd_port": TTYD_PORT}


@app.post("/api/sessions/{name}/input")
async def send_session_input(name: str, payload: SessionInputPayload) -> dict:
    """Type into a tmux session over the API (remote-agent terminal input).

    SECURITY: this is remote code execution by design -- typing into a shell
    pane runs whatever gets typed. It is fenced accordingly; every fence must
    pass, in order:

    1. ``is_valid_session_name`` on the path param (400) -- same boundary as
       connect/delete.
    2. Global opt-in: settings ``input_enabled`` (default False) -- 403 when
       off, regardless of anything else.
    3. Per-session allowlist: settings ``input_allowed_sessions`` (default
       empty) -- exact names only; a session not on the list is 403 even
       when the feature is enabled. Checked BEFORE existence so the endpoint
       never leaks whether a non-allowlisted session exists.
    4. Fail closed on the known-session set (exact membership, same pattern
       as connect/delete): unknown name or empty/unavailable cache -> 404.

    Auth is the shared middleware (federation Bearer key / localhost /
    session cookie) -- deliberately NOT a second key.

    Input is sent via ``tmux send-keys`` through
    ``asyncio.create_subprocess_exec`` (argv, never a shell); *text* uses
    literal mode (``-l``) so shell metacharacters are typed as characters,
    never interpreted by anything muxplex spawns. Send order:
    text -> keys -> enter.

    After a short settle (~400ms) the session's pane is re-captured and
    returned, so the caller immediately sees the effect of what it typed:
    ``{"ok": true, "session": name, "snapshot": "<pane text>"}``.
    """
    _require_valid_session_name(name)

    settings = load_settings()
    # Strict-typed fence reads, fail CLOSED. Only the boolean True enables
    # the endpoint: a hand-edited settings.json with `"input_enabled":
    # "false"` (a truthy string) must disable, not enable. Likewise the
    # allowlist must be a real list -- a string value would turn `name in
    # allowed` into substring matching and silently widen the fence.
    if settings.get("input_enabled") is not True:
        _log.warning("input: rejected for %r -- input_enabled is false", name)
        raise HTTPException(
            status_code=403,
            detail="Session input is disabled (settings.input_enabled=false)",
        )
    allowed = settings.get("input_allowed_sessions")
    if not isinstance(allowed, list):
        allowed = []
    if not session_matches_allowlist(name, allowed):
        _log.warning("input: rejected for %r -- not in input_allowed_sessions", name)
        raise HTTPException(
            status_code=403,
            detail=f"Session '{name}' does not match any input_allowed_sessions pattern",
        )

    # Fail closed: exact membership in the known set; an empty/unavailable
    # cache rejects everything (same rationale as connect/delete).
    known = get_session_list()
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    # Size/quantity caps: bound a single action well below platform failure
    # modes (a >~128 KiB argv element raises OSError/E2BIG from exec) and
    # stop a huge keys list from forking one subprocess per element.
    if len(payload.text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"text too large (max {MAX_TEXT_BYTES} bytes UTF-8)",
        )
    if len(payload.keys) > MAX_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"too many keys (max {MAX_KEYS})",
        )
    invalid_keys = [k for k in payload.keys if k not in ALLOWED_KEYS]
    if invalid_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported key(s): {invalid_keys!r}. Allowed: {sorted(ALLOWED_KEYS)}"
            ),
        )
    if not payload.text and not payload.keys and not payload.enter:
        raise HTTPException(
            status_code=400,
            detail="No input provided (need text, keys, or enter)",
        )
    if payload.lines is not None and not (1 <= payload.lines <= MAX_CAPTURE_LINES):
        raise HTTPException(
            status_code=400,
            detail=f"lines must be between 1 and {MAX_CAPTURE_LINES} (got {payload.lines})",
        )

    try:
        if payload.text:
            await run_tmux(*build_send_text_argv(name, payload.text))
        for key in payload.keys:
            await run_tmux(*build_send_key_argv(name, key))
        if payload.enter:
            await run_tmux(*build_send_key_argv(name, "Enter"))
    except (RuntimeError, OSError) as exc:
        # RuntimeError: tmux exited non-zero (e.g. session vanished
        # mid-flight). OSError: the exec itself failed (e.g. E2BIG for an
        # oversized argv, ENOENT for a missing tmux binary) -- return a
        # clean 500 instead of an unhandled traceback.
        _log.warning("input: send-keys failed for %r: %s", name, exc)
        raise HTTPException(status_code=500, detail=f"Failed to send input: {exc}")

    # Audit: exactly one info line per accepted action. Short redacted
    # preview only -- the full text may contain secrets and goes to debug.
    _log.info(
        "input: session=%r chars=%d enter=%s keys=%s preview=%r",
        name,
        len(payload.text),
        payload.enter,
        payload.keys,
        redact_preview(payload.text),
    )
    _log.debug("input: session=%r full text=%r", name, payload.text)

    # Read-back: settle briefly, then capture the pane so the caller sees
    # the effect of its input. Depth defaults to DEFAULT_CAPTURE_LINES (same
    # as the /api/sessions cache) unless the caller asked for more via
    # payload.lines (validated above).
    await asyncio.sleep(0.4)
    snapshot = await capture_pane(name, payload.lines or DEFAULT_CAPTURE_LINES)
    return {"ok": True, "session": name, "snapshot": snapshot}


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
    400 if *name* fails the session-name allowlist.
    404 if session is not an exact match in the known session list.
    Must be declared after DELETE /api/sessions/current so "current" routes correctly.
    """
    # Security boundary: reject unsafe names before they reach the shell.
    _require_valid_session_name(name)
    # Fail closed: reject unless *name* is an exact member of the known set. The
    # previous `if known and name not in known` skipped the guard whenever the
    # cache was empty -- allowing a delete against an unvalidated target exactly
    # when the session list was unavailable. Exact `in` membership also prevents
    # tmux `-t` prefix-matching a neighbouring session.
    known = get_session_list()
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    settings = load_settings()
    # create/delete templates are BOTH arbitrary user shell commands with a
    # {name} placeholder (default `tmux kill-session -t {name}`, but users
    # configure e.g. `amplifier-dev --destroy {name}`), so this path stays
    # shell-based to preserve that feature. Injection is closed by two layers:
    # (1) the allowlist above guarantees the name has no shell metacharacters;
    # (2) shlex.quote() is applied as defense-in-depth in case the allowlist is
    # ever loosened. For an allowlist-valid name shlex.quote() is a no-op.
    command = settings.get(
        "delete_session_template", "tmux kill-session -t {name}"
    ).replace("{name}", shlex.quote(name))

    _log.info("Deleting session '%s' with command: %s", name, command)
    try:
        result = subprocess.run(
            command,
            shell=True,
            input="y\n",  # auto-confirm interactive prompts (e.g. amplifier-dev --destroy)
            capture_output=True,
            text=True,
            timeout=30,
            env=tmux_env(),
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
    """Re-register tmux hooks. Call after tmux server restarts.

    Delegates to the same _arm_bell_hook() the poll loop's self-healing
    retry uses, so a manual call here and the automatic retry always agree
    on the recorded armed state (_bell_hook_armed, surfaced at
    GET /api/instance-info).
    """
    if await _arm_bell_hook():
        return {"ok": True}
    return {"ok": False, "error": _bell_hook_last_error}


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
async def update_settings(request: Request):
    """Merge known keys from the request body into settings and return updated settings.

    The response is redacted in the same way as ``GET /api/settings`` so that
    sensitive keys are never leaked to the browser.

    Optimistic concurrency (compare-and-swap): the body MAY include an
    ``expected_settings_updated_at`` float. When present, it must equal the
    server's CURRENT ``settings_updated_at`` or the request is rejected with
    409 and NO write is made. This closes the clobber hazard where a client
    holding a stale in-memory settings snapshot (e.g. an old copy of the
    entire ``views`` array) builds a patch from that stale data and
    overwrites a concurrent edit made by another device/tab -- this is
    exactly how a real incident destroyed 7 of 8 views in one request.
    When omitted, behavior is unchanged (backward compatible with existing
    clients, including federation sync, which don't send it yet). New
    clients SHOULD send it -- see frontend/app.js's ``patchSettingsGuarded``
    for the reference implementation.

    The precondition field is popped out of the body before it reaches
    ``patch_settings()`` -- it's a precondition, not a setting, and isn't a
    key ``patch_settings`` would recognize anyway (it's not in
    DEFAULT_SETTINGS), but popping it explicitly documents the intent and
    avoids depending on that incidental behavior.

    NOTE: there's no Pydantic model backing this endpoint's body today (it
    reads raw JSON because the patch is an open-ended subset of
    DEFAULT_SETTINGS keys) -- this field is handled the same way, as a
    plain dict key, rather than introducing a new model just for one field.

    Destructive-write backstop: independent of the CAS precondition above,
    any patch containing ``views`` is assessed for catastrophic shrinkage
    (see ``views.assess_views_destruction`` / ``settings.patch_settings``).
    A catastrophic write is rejected with 409 -- ``{"detail": <reason>,
    "settings_updated_at": <current>, "backstop": true, "counts": {...}}``
    -- and NO write is made, regardless of whether the CAS precondition
    passed. This is a SEPARATE 409 cause from the CAS mismatch above; the
    response body's ``backstop: true`` field is how a client distinguishes
    them (a CAS 409 means "reload and retry your intent"; a backstop 409
    means "this intent itself is destructive -- do not blindly retry it").
    Pass ``allow_destructive: true`` in the body (also popped before
    reaching ``patch_settings()``, same pattern as the CAS field) to perform
    an intentional bulk deletion.
    """
    body = await request.json()
    expected = body.pop("expected_settings_updated_at", None)
    allow_destructive = bool(body.pop("allow_destructive", False))
    if expected is not None:
        current_ts = load_settings().get("settings_updated_at", 0.0)
        # Exact float equality (not an epsilon comparison): the expected
        # value is always a settings_updated_at we ourselves emitted on a
        # prior GET/PATCH response, round-tripped through JSON with no
        # arithmetic applied to it -- so there's no floating-point drift to
        # tolerate. An epsilon would only paper over a real mismatch.
        if expected != current_ts:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Settings have changed since you last loaded them.",
                    "settings_updated_at": current_ts,
                },
            )
    try:
        updated = patch_settings(body, allow_destructive=allow_destructive)
    except DestructiveSettingsWriteRejected as exc:
        current_ts = load_settings().get("settings_updated_at", 0.0)
        return JSONResponse(
            status_code=409,
            content={
                "detail": exc.reason,
                "settings_updated_at": current_ts,
                "backstop": True,
                "counts": exc.counts,
            },
        )
    result = copy.deepcopy(updated)
    result["federation_key"] = ""
    for inst in result.get("remote_instances", []):
        if "key" in inst:
            inst["key"] = ""
    return result


@app.get("/api/settings/sync")
async def get_settings_sync() -> dict:
    """Return syncable settings + timestamps for federation sync.

    Authenticated via federation Bearer token (same auth middleware as all other
    non-exempt endpoints). Returns only the keys in SYNCABLE_KEYS plus the
    settings_updated_at and views_updated_at timestamps; infrastructure keys
    (host, port, federation_key, etc.) are never included.

    `views_updated_at` is an additive field (see SettingsSyncPayload /
    apply_synced_settings): a peer that predates it simply doesn't look for
    it in this response.
    """
    syncable = get_syncable_settings()
    ts = syncable.get("settings_updated_at", 0.0)
    views_ts = syncable.get("views_updated_at", 0.0)
    settings = {
        k: v
        for k, v in syncable.items()
        if k not in ("settings_updated_at", "views_updated_at")
    }
    return {
        "settings": settings,
        "settings_updated_at": ts,
        "views_updated_at": views_ts,
    }


@app.put("/api/settings/sync")
async def put_settings_sync(payload: SettingsSyncPayload):
    """Accept synced settings from a remote server (newer-wins).

    Compares the incoming timestamp against the local settings_updated_at --
    this IS the sync path's precondition discipline: a peer only gets to
    write when its view of the world (`settings_updated_at`) is strictly
    newer than ours, exactly analogous to the PATCH path's
    `expected_settings_updated_at` CAS check. If the incoming timestamp is
    equal to or older than the local one, returns 409 (Conflict) with the
    current local state so the caller can see what this instance has and
    resync from there -- its view IS stale/inconsistent with ours.

    If strictly newer, applies via apply_synced_settings(), passing through
    `views_updated_at` for the views-specific conflict resolution described
    in that function's docstring (an older peer simply omits the field --
    Pydantic defaults it to None -- and apply_synced_settings() falls back
    to its pre-existing, fully-interoperable behavior).

    The destructive-write backstop is NOT optional here and has no override:
    apply_synced_settings() runs it unconditionally as its first act, before
    any key is applied, for every caller including this endpoint AND the
    periodic background sync loop (_sync_settings_with_remotes). A
    catastrophic incoming `views` -- however the timestamp comparison above
    came out -- is rejected with 409 (`{"backstop": true, ...}`, same shape
    as update_settings()'s equivalent branch) and NO write is made. Unlike
    the PATCH path, a peer can never supply `allow_destructive` to bypass
    this -- see apply_synced_settings()'s docstring for the rationale.
    """
    current = load_settings()
    local_ts: float = current.get("settings_updated_at", 0.0)

    if payload.settings_updated_at > local_ts:
        try:
            apply_synced_settings(
                payload.settings,
                payload.settings_updated_at,
                payload.views_updated_at,
            )
        except DestructiveSettingsWriteRejected as exc:
            syncable = get_syncable_settings()
            ts = syncable.get("settings_updated_at", 0.0)
            settings_out = {
                k: v for k, v in syncable.items() if k != "settings_updated_at"
            }
            return JSONResponse(
                status_code=409,
                content={
                    "detail": exc.reason,
                    "settings": settings_out,
                    "settings_updated_at": ts,
                    "backstop": True,
                    "counts": exc.counts,
                },
            )
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
        # Local filesystem path this instance's tmux sessions live under
        # (see settings.tmux_socket_dir / sessions.tmux_env). Any tool that
        # creates tmux sessions on this host (muxplex-deck, agents, ad-hoc
        # scripts) needs this to land sessions where THIS instance can see
        # them -- otherwise they land on a different tmux server and are
        # silently invisible (see AGENTS.md's "tmux socket" section).
        # Exposing it here is a deliberate judgment call: this endpoint is
        # already unauthenticated and already returns other local-host
        # identifiers (device_name/hostname), and a filesystem path is not
        # a secret in the way federation_key or TLS material would be.
        # Resolved (not the raw setting, which may be "" meaning "inherit
        # from environment") via resolve_tmux_socket_dir() -- and since this
        # code runs INSIDE the live server process, os.environ here is the
        # server's own actual environment, so the resolution is exact (not
        # a best-effort guess, unlike the CLI's `muxplex env`, which infers
        # from its own separate process environment).
        "tmux_socket_dir": resolve_tmux_socket_dir(),
        # Whether the tmux alert-bell hook is currently registered (see
        # _arm_bell_hook()). False means bells are not firing -- e.g. tmux
        # wasn't up yet at startup, and _run_poll_cycle()'s self-healing
        # retry hasn't succeeded yet. This is how an operator/agent tells
        # bells are unarmed without grepping logs.
        "bell_hook_armed": _bell_hook_armed,
    }


@app.get("/api/ca")
async def get_ca_certificate() -> Response:
    """Serve the local CA's public certificate PEM, when the local-CA TLS
    method (`muxplex setup-tls --method ca`) is in use.

    Unauthenticated by design — see auth.py's `_AUTH_EXEMPT_PATHS` comment.
    A CA *public* certificate is not a secret: it contains no private key
    material, and it is precisely the trust anchor clients (muxplex-deck,
    browsers, agents) are meant to install so they can verify this server's
    TLS leaf. Requiring auth here would be circular — a client can't
    authenticate over TLS it doesn't yet trust — and would defeat the one
    job this endpoint exists to do.

    This closes a real onboarding gap: previously the only way to get this
    file was `scp` from the server (requiring SSH access a client may not
    have), and users reliably grabbed the wrong file — `muxplex.crt` (the
    LEAF the server presents on the wire) instead of the CA — producing
    "unable to get local issuer certificate" on the client. See AGENTS.md.

    Reads ONLY the single fixed path `settings.get_local_ca_cert_path()`
    resolves to (`<config_dir>/ca/muxplex-ca.crt`, mirroring cli.py's
    `setup_tls()`); no request input (path/query/body/header) influences
    which file is read — there is no way to turn this into an
    arbitrary-file-read.
    """
    pem_bytes = get_local_ca_cert_bytes(get_local_ca_cert_path())
    if pem_bytes is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No local CA certificate is available. This server may not "
                "be using 'muxplex setup-tls --method ca' (e.g. it's on "
                "Tailscale, mkcert, or self-signed instead), or the file at "
                "the expected CA path is missing or not a valid CA "
                "certificate."
            ),
        )
    return Response(
        content=pem_bytes,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="muxplex-ca.crt"'},
    )


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


async def _client_disconnected(websocket: WebSocket) -> None:
    """Resolve as soon as the client disconnects (or the connection becomes
    otherwise unusable).

    Raced (via asyncio.wait/FIRST_COMPLETED) against the ttyd auto-spawn wait
    in terminal_ws_proxy.  Root cause this guards against: uvicorn's ASGI
    websocket implementation sets its internal "handshake complete"
    bookkeeping to True the moment the underlying TCP connection is lost —
    regardless of whether a real WebSocket handshake ever happened — because
    connection_lost() is a generic asyncio.Protocol callback that doesn't
    know our app hasn't called accept() yet. If terminal_ws_proxy then calls
    websocket.accept() on a connection that died during the pre-accept
    ttyd-respawn wait, uvicorn sees a stray 'websocket.accept' message on
    what it now considers an established connection and raises:
        RuntimeError: Expected ASGI message 'websocket.send' or
        'websocket.close', but got 'websocket.accept'.
    This surfaced in production as recurring "Exception in ASGI application"
    log spam with no user-visible symptom (the browser had already given up
    on the socket). Reproduced with a real uvicorn server (forced onto the
    'websockets-sansio' protocol impl to match the production dependency
    resolution) plus a raw socket that aborts mid-handshake during the
    kill_ttyd/spawn_ttyd/sleep(0.8) window — see test_ws_proxy.py.
    Never raises: any receive() failure is treated the same as an explicit
    disconnect, since either way accept() must not be attempted.
    """
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
    except Exception:
        return


async def _prepare_ttyd_for_reconnect() -> None:
    """Kill and respawn ttyd (best-effort) if it isn't listening, then wait
    briefly for it to bind its port.

    Loads active_session from state itself so it can run as an independent
    task, raced against _client_disconnected() by terminal_ws_proxy.
    """
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

    The ttyd auto-spawn wait (kill_ttyd + spawn_ttyd + a fixed 0.8s settle
    delay) is real wall-clock time during which the browser can disconnect
    (tab closed, navigation, network drop, PWA backgrounding). Calling
    websocket.accept() after that happens raises RuntimeError — see
    _client_disconnected()'s docstring for the exact mechanism — so that wait
    is raced against a disconnect watcher and accept() is skipped entirely if
    the client is already gone.
    """
    # Auth check before accepting — BaseHTTPMiddleware doesn't cover WebSocket scope
    if not await _ws_auth_check(websocket):
        return

    # Register this connection's task so lifespan shutdown can cancel it.
    _task = asyncio.current_task()
    if _task is not None:
        _ws_proxy_tasks.add(_task)

    # Ensure ttyd is reachable BEFORE accepting the browser WS.
    # After a service restart ttyd is dead but clients reconnect immediately.
    # Auto-spawn from active_session so the browser's 'open' event only fires
    # when a real relay is possible — eliminates the 0→1→0→1 counter bounce.
    if not _ttyd_is_listening():
        # Consume the ASGI 'websocket.connect' handshake message up front —
        # this is what websocket.accept() would otherwise do internally —
        # so _client_disconnected() below can observe a 'websocket.disconnect'
        # arriving during the (potentially slow) auto-spawn wait.
        try:
            await websocket.receive()
        except Exception:
            if _task is not None:
                _ws_proxy_tasks.discard(_task)
            return

        prep_task = asyncio.create_task(_prepare_ttyd_for_reconnect())
        disconnect_task = asyncio.create_task(_client_disconnected(websocket))
        done, pending = await asyncio.wait(
            {prep_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for p in pending:
            p.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        if disconnect_task in done:
            # Client vanished before ttyd was confirmed alive. uvicorn has
            # already torn the connection down and will raise RuntimeError on
            # any accept() attempt now — bail out instead of crashing the
            # ASGI app (see _client_disconnected()'s docstring).
            _log.debug(
                "WS proxy: client disconnected during ttyd auto-spawn wait, "
                "skipping accept()"
            )
            if _task is not None:
                _ws_proxy_tasks.discard(_task)
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
                        if msg["type"] == "websocket.disconnect":
                            return
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

            # Relay until EITHER side closes, then cancel the other.
            # gather() (wait for both) would hang shutdown: when the browser
            # side disconnects (e.g. uvicorn closing connections on SIGTERM),
            # ttyd_to_client keeps streaming from the still-live ttyd, the
            # handler never returns, and uvicorn waits on this connection
            # until systemd SIGKILLs the process.
            relay_tasks = [
                asyncio.create_task(client_to_ttyd()),
                asyncio.create_task(ttyd_to_client()),
            ]
            _done, pending = await asyncio.wait(
                relay_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
    except Exception as exc:
        _log.debug("ws proxy closed: %s", exc)
    finally:
        if _task is not None:
            _ws_proxy_tasks.discard(_task)
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
    """Serve index.html with hostname injected into the page title.

    Also appends ``?v=<version>`` to every static-asset URL (script src, link
    href) so browsers immediately pick up new code on each release rather than
    serving stale JS/CSS from the HTTP cache.  API URLs (/api/...) are
    excluded — they are not HTTP-cached by browsers.
    """
    html = (_FRONTEND_DIR / "index.html").read_text()
    html = html.replace(
        "<title>muxplex</title>",
        f"<title>{_HOSTNAME} \u2014 muxplex</title>",
    )
    html = _ASSET_URL_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}?v={_UI_VERSION}",
        html,
    )
    # no-cache = "revalidate before use", NOT "don't cache" — installed PWAs
    # otherwise keep serving a stale app shell across deploys (see the static
    # mount below for the same treatment of app.js and friends).
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/login", response_class=HTMLResponse)
async def login_page(next: str | None = None):
    """Serve branded login.html with injected window.MUXPLEX_AUTH containing auth mode and username.

    ``next`` (query param) is a same-origin, path-only redirect target to
    carry through the login form -- see validate_next_path() for the full
    validation contract. It is injected as window.MUXPLEX_NEXT (JSON, not
    string-interpolated into an HTML attribute) so login.html's script can
    set it on a hidden form field via a JS property assignment rather than
    an innerHTML/attribute substitution, which would otherwise reopen an
    XSS path for a value we already treat as untrusted input.
    """
    html = (_FRONTEND_DIR / "login.html").read_text()
    username = pwd.getpwuid(os.getuid()).pw_name if _auth_mode == "pam" else ""
    mode_data = json.dumps({"mode": _auth_mode, "user": username})
    safe_next = validate_next_path(next)
    next_data = json.dumps(safe_next)
    html = html.replace(
        "</head>",
        f"<script>window.MUXPLEX_AUTH = {mode_data}; "
        f"window.MUXPLEX_NEXT = {next_data};</script></head>",
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
    next: str = Form(default=""),
) -> RedirectResponse:
    """Validate credentials and issue a session cookie on success.

    In PAM mode, delegates to authenticate_pam(username, password).
    In password mode, compares the submitted password to _auth_password.

    On success: redirect to the validated ``next`` target (default "/") with
    a signed muxplex_session cookie. ``next`` comes from a hidden form field
    (see login.html) populated from the ?next= query param on the GET
    request that rendered this form -- itself either the page the user
    originally tried to reach (via AuthMiddleware's redirect) or omitted.
    On failure: redirect to /login?error=1, preserving ``next`` so a wrong
    first attempt doesn't lose the intended destination on retry.
    """
    # Validate credentials
    if _auth_mode == "pam":
        valid = authenticate_pam(username, password)
    else:
        valid = password == _auth_password

    if not valid:
        error_redirect = "/login?error=1"
        safe_next_on_failure = validate_next_path(next)
        if safe_next_on_failure != "/":
            error_redirect += f"&next={quote(safe_next_on_failure, safe='')}"
        return RedirectResponse(error_redirect, status_code=303)

    # Issue session cookie
    cookie_value = create_session_cookie(_auth_secret, _auth_ttl)
    safe_next = validate_next_path(next)
    response = RedirectResponse(safe_next, status_code=303)
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

# Per-remote poll timeout (seconds). The shared federation client keeps its 5s
# default for one-off write proxies (connect/bell/create/delete); the sessions
# poll fan-out uses this tighter cap so one slow remote can't lag the whole UI.
_FEDERATION_POLL_TIMEOUT = 2.0

# Circuit breaker: after 3 consecutive connection failures a remote is skipped
# (no network call, no fan-out latency) for 60s, then probed once per window.
# Threshold matches _FEDERATION_GRACE_FAILURES so the circuit opens exactly when
# the grace window is exhausted — cached-session semantics are unchanged.
# Keyed by remote URL — the network endpoint is the thing that's unreachable.
_federation_breaker = CircuitBreaker(
    threshold=_FEDERATION_GRACE_FAILURES, cooldown=60.0
)


async def _fetch_remote_version(
    http_client: httpx.AsyncClient, url: str, key: str
) -> str | None:
    """Best-effort fetch of a remote's version via its own /api/instance-info.

    Never raises: an unreachable remote, a too-old version that doesn't serve
    this endpoint, or a malformed response all yield None ("unknown"). Callers
    must render None distinctly from "matches the local version" -- an
    unknown that looks like agreement is worse than showing no data.

    /api/instance-info is unauthenticated (see auth._AUTH_EXEMPT_PATHS), so
    this succeeds even when the /api/sessions call it runs alongside is
    auth-rejected.
    """
    try:
        resp = await http_client.get(
            f"{url.rstrip('/')}/api/instance-info",
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=_FEDERATION_POLL_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        version = data.get("version") if isinstance(data, dict) else None
        return version if isinstance(version, str) and version else None
    except Exception:
        return None


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
    activity = get_session_activity()
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
                "last_activity_at": activity.get(name),
                "deviceId": local_device_id,
                "deviceName": local_device_name,
                # This process's own version -- same value /api/instance-info
                # reports. Included so clients can compare local vs remote
                # versions using this single response, without a second fetch.
                "deviceVersion": app.version,
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

        Every returned entry also carries deviceVersion: the remote's
        self-reported version from its own /api/instance-info, fetched
        concurrently with (never blocking on, and never causing this
        function to raise for) the /api/sessions call above. None means
        "unknown" (unreachable, too old to serve the endpoint, or malformed
        response) — deliberately never defaulted to a real version string,
        since an unknown that looked like agreement with the local version
        would be worse than showing no data.
        """
        url: str = remote.get("url", "")
        key: str = remote.get("key", "")
        remote_name: str = remote.get("name", url)
        remote_device_id: str = remote.get("device_id", str(i))
        if not _federation_breaker.should_attempt(url):
            # Circuit open: remote is known-unreachable; skip the network call
            # entirely so it costs the fan-out nothing. Honest status entry,
            # same shape as a fresh connection failure.
            return [
                {
                    "status": "unreachable",
                    "deviceId": remote_device_id,
                    "remoteId": remote_device_id,
                    "deviceName": remote_name,
                    "deviceVersion": None,
                }
            ]
        # Started immediately (not merely scheduled) so it runs concurrently
        # with the /api/sessions call below rather than sequentially after
        # it. Never raises — see _fetch_remote_version — so every exit path
        # from this function can safely await it.
        version_task = asyncio.create_task(_fetch_remote_version(http_client, url, key))
        try:
            resp = await http_client.get(
                f"{url.rstrip('/')}/api/sessions",
                headers={"Authorization": f"Bearer {key}"} if key else {},
                timeout=_FEDERATION_POLL_TIMEOUT,
            )
            # Any HTTP response means the remote is reachable — reset the breaker
            # (auth/HTTP errors are honest states, not connection failures).
            if _federation_breaker.record_success(url):
                _log.info("federation remote %s reachable again; resuming", remote_name)
            if resp.status_code in (401, 403):
                # Auth failure — clear cache so stale data is not served.
                # /api/instance-info is unauthenticated, so the version probe
                # can still succeed even though /api/sessions was rejected.
                _federation_cache.pop(remote_device_id, None)
                return [
                    {
                        "status": "auth_failed",
                        "deviceId": remote_device_id,
                        "remoteId": remote_device_id,
                        "deviceName": remote_name,
                        "deviceVersion": await version_task,
                    }
                ]
            resp.raise_for_status()
            sessions = resp.json()
            remote_version = await version_task
            # Tag each session with deviceId, deviceName, remoteId, deviceVersion, and sessionKey
            tagged = [
                {
                    **s,
                    "deviceId": remote_device_id,
                    "deviceName": remote_name,
                    "deviceVersion": remote_version,
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
                        "deviceVersion": remote_version,
                    }
                ]
            return tagged
        except httpx.HTTPStatusError:
            # The remote responded (reachable) with an HTTP error — honest
            # error state, NOT a connection failure: never circuit-break it.
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
                    "deviceVersion": await version_task,
                }
            ]
        except httpx.TransportError as exc:
            # Connection-level failure (refused, timeout, DNS): the remote is
            # unreachable. Count it toward the circuit breaker so a
            # persistently-dead remote stops costing the fan-out a timeout on
            # every poll.
            if _federation_breaker.record_failure(url):
                _log.warning(
                    "federation remote %s unreachable; skipping for %.0fs",
                    remote_name,
                    _federation_breaker.cooldown,
                )
            else:
                _log.debug(
                    "federation remote %s connection failed: %s", remote_name, exc
                )
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
                    "deviceVersion": await version_task,
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
                    "deviceVersion": await version_task,
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


class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles that forces revalidation on every request.

    Without a Cache-Control header, installed PWAs (Edge/Chrome app windows)
    keep serving a stale cached app shell indefinitely after a deploy —
    ``?v=<version>`` busting only helps on release version bumps, not
    git-main deploys. ``no-cache`` (deliberately NOT ``no-store``) keeps
    caching but requires revalidation; with StaticFiles' built-in
    ETag/Last-Modified support an unchanged file costs a cheap 304.
    API routes are registered before this mount and are unaffected.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount(
    "/", _NoCacheStaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend"
)

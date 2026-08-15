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
import socket
import ssl
import subprocess
import sys
import time
from typing import Literal, NamedTuple
from urllib.parse import quote

import httpx
import websockets
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.responses import RedirectResponse, Response
from starlette.types import Scope
from websockets.asyncio.client import unix_connect
from websockets.typing import Subprotocol

from muxplex import bells as bells_mod
from muxplex import focus, followups, tmux_config
from muxplex import ttyd as ttyd_mod
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
from muxplex.manifest import (
    clear_rename_journal,
    get_created_with,
    load_manifest,
    save_manifest,
    set_created_with,
    start_rename_journal,
    update_manifest,
)
from muxplex.pruning import load_pruning_state, save_pruning_state
from muxplex.sessions import (
    DEFAULT_CAPTURE_LINES,
    MAX_CAPTURE_LINES,
    capture_pane,
    capture_pane_metadata,
    capture_pane_window,
    enumerate_sessions,
    get_session_activity,
    get_session_created_times,
    get_session_cwds,
    get_session_list,
    get_snapshots,
    is_tmux_stable_name,
    is_valid_session_name,
    probe_tmux_epoch,
    rename_tmux_session,
    run_tmux,
    snapshot_all,
    spawn_session_command,
    tmux_env,
    update_session_cache,
)
from muxplex.settings import (
    DEVICE_LABEL_PLACEMENTS,
    RESERVED_COMMAND_ID,
    DestructiveSettingsWriteRejected,
    InvalidViewRuleRejected,
    apply_synced_settings,
    find_session_command,
    get_local_ca_cert_path,
    get_syncable_settings,
    load_federation_key,
    load_settings,
    patch_settings,
    resolve_session_commands,
    resolve_tmux_socket_dir,
    save_settings,
)
from muxplex.setup_page import detect_platform, render_setup_page
from muxplex.state import (
    GLOBAL_GROUP,
    GROUP_FIELDS,
    clear_missing_active_sessions,
    device_group_id,
    empty_bell,
    gc_sync_groups,
    load_state,
    prune_devices,
    read_group_state,
    read_state,
    register_device,
    resolve_group,
    save_state,
    state_lock,
    write_group_state,
)
from muxplex.terminal_input import (
    ALLOWED_KEYS,
    MAX_KEYS,
    MAX_TEXT_BYTES,
    build_send_key_argv,
    build_send_text_argv,
    input_allowed_for_session,
    redact_preview,
)
from muxplex.tls import get_local_ca_cert_bytes
from tmux_kit.bell import build_alert_bell_hook
from muxplex.ttyd import (
    TTYD_PORT,
    TtydCapacityError,
    TtydSpawnError,
    acquire_relay,
    ensure_ttyd,
    kill_all_ttyd,
    kill_ttyd,
    reap_idle_ttyds,
    reap_legacy_ttyd,
    reap_orphan_ttyds,
    relay_count,
    release_relay,
    socket_is_live,
    socket_path_for,
)
from muxplex.views import (
    VIEW_RULE_KEY,
    annotate_view_membership,
    assess_views_destruction,
    filter_visible,
    normalize_session_keys,
    prune_stale_keys,
    validate_view_rules,
    view_patterns,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL: float = float(os.environ.get("POLL_INTERVAL", "2.0"))
SERVER_PORT: int = int(os.environ.get("MUXPLEX_PORT", "8088"))
SETTINGS_SYNC_INTERVAL: int = 15  # sync every ~30 seconds (15 * 2s poll interval)

# Whether cli.py's serve() actually handed uvicorn ssl_certfile/ssl_keyfile.
# cli.py sets MUXPLEX_TLS_ENABLED before importing this module -- the same
# pattern SERVER_PORT above already uses for MUXPLEX_PORT. This is the
# SOURCE OF TRUTH _arm_bell_hook() uses to pick http vs https: the hook must
# dial the scheme uvicorn is actually serving, never assume http (see
# AGENTS.md's bell-hook incident). Defaults to False (plain import, e.g.
# tests using TestClient directly without going through cli.py's serve()),
# matching SERVER_PORT's same import-without-cli fallback behavior.
SERVER_TLS_ENABLED: bool = os.environ.get("MUXPLEX_TLS_ENABLED", "0") == "1"

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level task reference
# ---------------------------------------------------------------------------

_poll_task: asyncio.Task | None = None
_federation_client: httpx.AsyncClient | None = None
_settings_sync_counter: int = 0

# Watermark distinguishing "genuinely created while this instance is
# running" from "merely first observed by this process" (see the "Ensure
# bell entries" step of _run_poll_cycle() below). Set at module import as a
# conservative default (covers tests that call _run_poll_cycle() directly,
# bypassing lifespan()), and reset to the real startup moment inside
# lifespan() for production/TestClient use. A tmux session whose own
# `#{session_created}` timestamp is at or after this watermark was created
# during this process's lifetime; one from before it predates this instance
# and must never be treated as newly created, no matter how it first enters
# state.json (see docs/API_SEMANTICS.md's needs-attention section).
_server_start_time: float = time.time()

# Bell-hook self-healing state (see _arm_bell_hook()). Starts unarmed;
# _run_poll_cycle() retries registration each cycle ONLY while this is False,
# so a startup failure is retried until it heals, but a steady-state success
# costs nothing further -- no per-cycle tmux subprocess once armed. Exposed
# via GET /api/instance-info so an operator/agent can tell bells are (not)
# armed without grepping logs.
#
# HONEST MEANING (deliberately weaker than an earlier revision): "armed"
# means tmux's `set-hook -g` was ACCEPTED -- nothing more. It is NOT proof
# that a bell will actually be delivered (e.g. an http/https scheme mismatch
# can register perfectly and still never deliver a single bell -- see
# AGENTS.md's bell-hook section). A prior revision strengthened this to mean
# "a delivery probe actually arrived," which required firing a one-shot
# `tmux run-shell` at arm time -- and that mechanism re-fired on every retry
# while unarmed (e.g. every poll cycle during a restart window before the
# server is listening), painting `curl ... returned 7` onto the owner's live
# panes. The probe was removed for exactly that reason (never build a `tmux
# run-shell` for any reason, see AGENTS.md's "never render to a pane" rule);
# this field's meaning reverted to what it was before that revision.
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


def _bell_hook_curl(target: str) -> str:
    """Build the curl command used to forward a bell to *target*'s
    ``POST /api/sessions/{target}/bell``.

    Scheme and cert posture are derived from ``SERVER_TLS_ENABLED`` -- the
    same source of truth cli.py's ``serve()`` used to decide whether uvicorn
    got ``ssl_certfile``/``ssl_keyfile`` -- so this NEVER hardcodes ``http``
    while the server is actually speaking TLS (the incident this function
    exists to prevent). Dials ``127.0.0.1`` rather than ``localhost``:
    unambiguous (no DNS/hosts-file/IPv6-vs-IPv4 resolution surprise), and is
    exactly the address the auth middleware's localhost bypass checks (see
    docs/API_SEMANTICS.md / the auth-tls-patterns skill), so this call never
    needs credentials.

    ``-k`` (skip certificate verification) is added whenever TLS is on. This
    loopback call never leaves the host, so there is no MITM this
    verification would meaningfully guard against -- and the cert in use may
    be self-signed, signed by muxplex's own local CA, or (for the Tailscale
    method) issued for a hostname that doesn't cover ``127.0.0.1`` at all.
    This mirrors the identical, already-established pattern in cli.py's
    ``_probe_service_port`` / ``_fetch_local_instance_info`` (both use
    ``ssl.CERT_NONE`` for the same same-host reason).

    Args:
        target: session name (real, or the ``#{session_name}`` tmux format
            placeholder) to forward the bell to.

    DELIBERATELY ALWAYS SILENT -- no parameter exists to make this loud, on
    purpose. This is the ONLY function that builds the hook string tmux runs
    via ``run-shell``, and it fires on every real bell, in every session,
    for the life of the process, with a client very likely attached and
    watching. Silent means: no ``-S`` (so curl's own error text never
    reaches stderr), stderr explicitly redirected to ``/dev/null`` on top of
    that (a second, independent guarantee -- e.g. a shared-library loader
    warning curl itself doesn't control), and ``|| true`` so the shell's own
    exit status is always 0 (tmux's ``run-shell`` would otherwise display
    "returned N"). Three independent silences for one requirement: this
    must never paint a client's screen, regardless of failure mode.

    **Incident, read before touching this function:** an earlier revision
    had a second, loud variant of this function (an arm-time delivery
    PROBE, selected via a ``swallow`` parameter) to make failures
    diagnosable. Because both variants shared this one function, an even
    earlier revision merged them into a single always-loud command
    (``-sSf``, no stderr redirect) -- and every real bell whose curl call
    failed painted curl's error text onto whatever the owner was looking at
    (tmux's ``run-shell``, per its own manual, displays a background
    command's output in view mode on the client's active pane). Confirmed
    live: the owner watched ``returned 52`` replace his screen repeatedly,
    across every live session, for the life of the process. The probe
    variant was later removed entirely (see AGENTS.md's "never render to a
    pane" rule) -- not merely re-silenced -- because the probe RE-FIRED via
    `tmux run-shell` on every retry while unarmed (e.g. every poll cycle
    during a restart window), reproducing the same class of incident a
    second time. There is now no code path in this function that can build
    a loud command at all: loudness belongs in the log, ``GET
    /api/instance-info``, and ``muxplex doctor``, never on a client's
    screen and never behind a `tmux run-shell` call this function builds.
    """
    scheme = "https" if SERVER_TLS_ENABLED else "http"
    insecure = "k" if SERVER_TLS_ENABLED else ""
    # NOTE: never build this via str.format() with a placeholder for the
    # flags -- `target` can itself be the tmux format placeholder
    # `#{session_name}`, and a later `.format()` call would try to resolve
    # THAT `{session_name}` field too, raising KeyError. Two fully separate
    # f-strings (computed directly, no intermediate template) sidestep this
    # entirely.
    url = f"{scheme}://127.0.0.1:{SERVER_PORT}/api/sessions/{target}/bell"
    cmd = f"curl -s{insecure}fo /dev/null -X POST {url} 2>/dev/null"
    return f"{cmd} || true"


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

    HONEST CONTRACT: "armed" means ``set-hook`` was accepted -- nothing
    more. This is NOT proof of delivery (an http/https scheme mismatch, for
    example, can register perfectly and still never deliver a single bell
    -- see ``AGENTS.md``'s bell-hook section for that incident). A prior
    revision fired a one-shot ``tmux run-shell`` delivery PROBE after
    registering, and only reported armed once that probe's HTTP request
    actually reached ``receive_bell()``. That probe was removed: it re-fired
    on every retry while unarmed (e.g. every poll cycle during a restart
    window, before the server was listening), and a failing probe painted
    ``curl ... returned 7`` onto the owner's live tmux panes -- the exact
    class of incident this whole function exists to avoid, reproduced by
    the fix meant to prevent it. There is no replacement mechanism: this
    function does not, and must not, fire any ``tmux run-shell`` other than
    the persistent hook's own registration string. See ``AGENTS.md``'s
    "never render to a pane" rule.

    Failure is never silent: every failure is logged at WARNING with the
    concrete error (unlike a bare ``except Exception: pass``), and the
    outcome is recorded in ``_bell_hook_armed`` / ``_bell_hook_last_error``
    so ``GET /api/instance-info`` (and ``muxplex doctor``) reflect it
    without grepping logs.

    Returns:
        True if ``set-hook`` was accepted, False otherwise.
    """
    global _bell_hook_armed, _bell_hook_last_error
    try:
        await run_tmux(
            "set-hook",
            "-g",
            "alert-bell",
            # The run-shell construction itself lives in the library
            # (tmux_kit.bell -- the ONE legal site, enforced by
            # test_safety_rails.py's recursive AST rail); this app supplies
            # only the always-silent command content.
            build_alert_bell_hook(_bell_hook_curl("#{session_name}")),
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

        # 1b. Update the session-presence manifest -- durable record of
        #     which sessions muxplex has observed alive, keyed by the
        #     identity of the tmux server hosting them (see manifest.py's
        #     module docstring and SESSION_PERSISTENCE_DESIGN.md). This is
        #     PURE OBSERVATION: it never creates, kills, or restores a
        #     tmux session -- it only records presence so an unplanned
        #     tmux-server death (host reboot, OOM, a cgroup-wide SIGKILL)
        #     leaves a durable list behind instead of the incident this
        #     exists to fix, where the 44 lost session names survived only
        #     by accident in pruning.json and were erased by the recovery
        #     itself.
        #
        #     probe_tmux_epoch() is deliberately a SEPARATE tmux call from
        #     enumerate_sessions() above, not a reuse of its result:
        #     enumerate_sessions() conflates "tmux failed" with "zero
        #     sessions" (both return []), which is exactly the ambiguity
        #     the manifest's same-server/cold-start discrimination must
        #     not inherit.
        #
        #     Best-effort and isolated: a failure here must never abort
        #     the rest of the poll cycle (session enumeration, bells, and
        #     everything below all still need to run).
        _manifest: dict = {}
        _epoch_now: dict | None = None
        _rename_kill_old: str | None = None
        try:
            _epoch_now = await probe_tmux_epoch()
            _manifest = load_manifest()

            # 1c. Honor an in-flight session-rename journal, BEFORE
            # update_manifest() touches this same manifest below (see
            # docs/plans/2026-08-07-session-rename-plan.md \u00a76.2). `tmux
            # rename-session` runs outside state_lock (like every other
            # subprocess in this codebase), and THIS cycle's own
            # settings/pruning writes (steps 13b/14 below) also run outside
            # it -- a rename racing a ~2s poll cycle WILL interleave, and
            # the journal is what makes that safe rather than destructive
            # (see the plan's \u00a76.1 for why pure ordering can't work here).
            # The migration function is the SAME one the endpoint calls --
            # idempotent, so calling it here even if the endpoint already
            # completed it is always safe.
            _rj = _manifest.get("rename_in_flight")
            if _rj:
                _rj_from = _rj.get("from")
                _rj_to = _rj.get("to")
                if _rj_to in name_set and _rj_from not in name_set:
                    # tmux confirms the rename happened; complete it.
                    _rj_state = load_state()
                    _rj_settings = load_settings()
                    _rj_pruning = load_pruning_state()
                    _rj_device_id = load_device_id()
                    _manifest, _rj_migrated = _migrate_session_name(
                        _rj_state,
                        _rj_settings,
                        _manifest,
                        _rj_pruning,
                        _rj_from,
                        _rj_to,
                        _rj_device_id,
                    )
                    _manifest = clear_rename_journal(_manifest)
                    save_state(_rj_state)
                    save_settings(_rj_settings)
                    save_pruning_state(_rj_pruning)
                    save_manifest(_manifest)
                    _rename_kill_old = _rj_from
                    _log.info(
                        "rename: poll cycle completed in-flight migration "
                        "%r -> %r (migrated=%s)",
                        _rj_from,
                        _rj_to,
                        _rj_migrated,
                    )
                else:
                    # Either the rename never actually happened (from still
                    # live, to absent) or the session died mid-rename
                    # (neither live) -- both cases: clear the journal and do
                    # nothing else. The cold-start/tombstone paths below
                    # already handle a dead session; a never-happened
                    # rename has nothing to migrate.
                    _manifest = clear_rename_journal(_manifest)
                    save_manifest(_manifest)
                    _log.warning(
                        "rename: clearing stale in-flight journal %r -> %r "
                        "(to_live=%s, from_live=%s)",
                        _rj_from,
                        _rj_to,
                        _rj_to in name_set,
                        _rj_from in name_set,
                    )

            # get_session_cwds() reads the cache enumerate_sessions() just
            # populated above (same tmux call, no extra subprocess) -- see
            # manifest.py's "Restore fidelity" section for why this is
            # recorded at all.
            _manifest, _manifest_changed = update_manifest(
                _manifest, _epoch_now, names, cwds=get_session_cwds()
            )
            if _manifest_changed:
                save_manifest(_manifest)
        except Exception:
            _log.exception("session-presence manifest update error")

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

        # 5. Ensure bell entries exist for every current session.
        #
        # A session whose bell is being seeded for the very first time (no
        # prior "bell" key in state.json) is either (a) genuinely just
        # created -- most likely via POST /api/sessions moments ago -- or
        # (b) simply new to THIS PROCESS's bookkeeping: a muxplex restart
        # with state.json missing/reset, a fresh install observing 50+
        # pre-existing sessions for the first time, or a session that was
        # created (by hand, or by another tool) while muxplex was down and
        # is only now being discovered at startup. All of (b) must fall
        # back to empty_bell() -- seeding them as attention-worthy would
        # mass-flag every pre-existing session at once, exactly the
        # bell-vs-activity regression class this feature must not repeat.
        #
        # The discriminator is tmux's own `#{session_created}` timestamp
        # (get_session_created_times(), sessions.py) compared against
        # _server_start_time, the moment THIS muxplex process actually came
        # up (reset in lifespan()). session_created is intrinsic to the
        # tmux session -- set once, by tmux, never revised -- so unlike
        # anything muxplex itself tracks (state.json, the presence
        # manifest, pruning.json) it is unaffected by any of muxplex's own
        # data being deleted or reset:
        #   - muxplex restart / state file deleted / fresh install: every
        #     pre-existing session's session_created predates this
        #     process's startup -> not flagged, matches pre-fix behavior.
        #   - a session created while muxplex was down, discovered at the
        #     next startup: its session_created is still from before THIS
        #     process started -> not flagged, same bucket as the row above
        #     (this feature is specifically for the live create-and-look
        #     flow, not a startup backfill).
        #   - the real bug this fixes -- POST /api/sessions while muxplex
        #     is already running: session_created is stamped at (or after)
        #     creation time, strictly after _server_start_time -> flagged.
        # Federation peers observing a remote session for the first time
        # never reach this branch at all: bells are local-sessions-only
        # state, and a remote session's bell is governed entirely by the
        # REMOTE instance's own poll cycle (see docs/API_SEMANTICS.md).
        created_times = get_session_created_times()
        for name in names:
            if name not in state["sessions"]:
                state["sessions"][name] = {}
            if "bell" not in state["sessions"][name]:
                created_at = created_times.get(name)
                if created_at is not None and created_at >= _server_start_time:
                    # Seed AS IF the bell had just fired: last_fired_at=now,
                    # unseen_count=1, seen_at=None. This is the ONLY change
                    # -- needs_attention() and _attention_order() are
                    # untouched, so the existing tiered sort already places
                    # a session in this state at the very top of tier 1
                    # (freshest last_fired_at) with no new sorting logic.
                    # Once viewed/selected, apply_bell_clear_rule() clears
                    # this bell exactly like any other, needs_attention()
                    # flips False, and the session falls through to tier 2
                    # (the last_fired_at-ordered remainder) for free -- its
                    # seeded last_fired_at is still fresh relative to
                    # sessions that never belled, so it stays near the top
                    # of that tier rather than sinking immediately.
                    state["sessions"][name]["bell"] = {
                        "last_fired_at": time.time(),
                        "seen_at": None,
                        "unseen_count": 1,
                        # bell.source == "seeded": muxplex manufactured this
                        # bell -- nothing happened in the pane. An agent
                        # triaging bells should skip this class entirely
                        # (docs/plans/2026-08-07-bell-causality-plan.md §4.3).
                        "source": "seeded",
                    }
                else:
                    state["sessions"][name]["bell"] = empty_bell()

        # 6. Remove state entries for sessions that no longer exist
        deleted = [s for s in list(state["sessions"]) if s not in name_set]
        for name in deleted:
            del state["sessions"][name]

        # 6b. Reap follow-up queues for sessions no longer live -- ONLY when
        # tmux is CONFIRMED alive this cycle (_epoch_now is not None, the
        # value already computed at step 1b above). A transient enumeration
        # failure (enumerate_sessions() returning [] because tmux itself is
        # briefly unreachable, not because there are zero sessions) must
        # never be indistinguishable from "every session was deleted" --
        # that ambiguity is exactly why probe_tmux_epoch() is a separate
        # call in the first place (see step 1b's comment and
        # docs/plans/2026-08-05-per-session-followup-queue-plan.md §3.2/§3.4). Dropping user-authored queued
        # text is never silent: one warning per dropped queue.
        if _epoch_now is not None:
            for _dropped_name, _dropped_count in followups.reap_stale_queues(
                state, name_set
            ):
                _log.warning(
                    "followups: reaped queue for vanished session %r (%d item(s))",
                    _dropped_name,
                    _dropped_count,
                )

        # 7. Clear active_session if the session is gone -- every group, not
        # just global. A private group whose session was killed from another
        # machine must not keep pointing at a corpse -- that would strand its
        # /terminal/ws in a permanent handshake-rejection loop (the pre-accept
        # close(4409) never reaches a real client as a WS close code -- it
        # serializes as an HTTP 403 -- see terminal_ws_proxy()'s docstring and
        # docs/API_SEMANTICS.md) with no way for the user to understand why.
        cleared = clear_missing_active_sessions(state, name_set)
        if cleared:
            _log.info("poll: cleared vanished active_session for group(s) %s", cleared)

        # Clear the no-session-param fallback target if it vanished. Does NOT
        # call kill_ttyd() -- a vanished session's ttyd (if any) is reaped
        # normally by the idle reaper below; this only updates the
        # bookkeeping fallback WS /terminal/ws uses when a client sends no
        # ?session= (see ttyd.py's module docstring and docs/API_SEMANTICS.md).
        if state["terminal_session"] not in name_set:
            state["terminal_session"] = None
            state["terminal_group"] = GLOBAL_GROUP

        # 8. Process bell flags (detect 0→1 transitions, update unseen_count).
        # Collect names whose queue may need advancing via THIS path -- see
        # process_bell_flags()'s on_transition docstring for why the
        # callback is wired ONLY while the hook is unarmed (armed, a
        # detached session's bell is independently observed by BOTH the
        # hook and this poll, and advancing from both would drain two
        # items for one physical bell). Advancing happens AFTER this
        # `async with state_lock` block releases (a queue advance runs a
        # subprocess and must never do so while the poll cycle holds the
        # lock) -- see the loop just below the block.
        _followup_poll_candidates: list[str] = []
        await process_bell_flags(
            names,
            state,
            on_transition=(
                _followup_poll_candidates.append if not _bell_hook_armed else None
            ),
        )

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

        # 11. Prune devices that haven't sent a heartbeat recently, then
        # garbage-collect any sync group no surviving device claims any
        # more (must run AFTER prune_devices() -- it derives its target set
        # from the surviving devices). With per-session ttyd there is no
        # contended resource for a pruned group to hold hostage -- an
        # abandoned group's ttyd (if it ever had one) is reclaimed on
        # resource grounds by the idle reaper below within
        # IDLE_REAP_SECONDS, so no explicit release branch is needed here.
        prune_devices(state)
        gc_sync_groups(state)

        # 12. Atomically persist the updated state
        save_state(state)

    # 12a. Advance any follow-up queue whose session had a bell-fired
    # transition detected via the poll path at step 8 above (only ever
    # populated while the hook was unarmed -- see that step's comment and
    # process_bell_flags()'s on_transition docstring). Outside state_lock:
    # each advance re-acquires the lock itself, and a send must not run
    # while the poll cycle holds it.
    for _followup_name in _followup_poll_candidates:
        await _advance_followup_queue(_followup_name)

    # 12b. Resource hygiene: reap idle (relays == 0, past IDLE_REAP_SECONDS)
    # per-session ttyds. No new timer -- rides this poll cycle exactly as
    # gc_sync_groups() rides prune_devices() above. Outside state_lock: the
    # ttyd registry is a separate, disposable structure with no interaction
    # with state.json. Killing an idle ttyd never touches the tmux session
    # it was attached to (see ttyd.py's module docstring).
    try:
        await reap_idle_ttyds()
    except Exception:
        _log.exception("ttyd idle-reap cycle error")

    # 12c. Kill the old ttyd for a rename the poll cycle just completed at
    # step 1c above (docs/plans/2026-08-07-session-rename-plan.md \u00a72.4) --
    # outside state_lock, like every other subprocess call. Never touches
    # the tmux session; the browser's WS drops and reconnects, and the next
    # /connect spawns a correctly-hashed ttyd for the new name.
    if _rename_kill_old is not None:
        try:
            await kill_ttyd(_rename_kill_old)
        except Exception:
            _log.exception(
                "rename: poll-cycle ttyd kill error for %r", _rename_kill_old
            )

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

        # SESSION_PERSISTENCE_DESIGN.md section 7.4: while a restore is
        # pending, our own local session list just became unavailable (not
        # refuted) -- treat local-owned keys the same as an unreachable
        # remote device's ("unknown, not dead") so a cold start doesn't
        # start a real prune countdown on view membership before the user
        # has had a chance to run `muxplex restore`. Self-clearing: once
        # pending_restore empties (restore succeeds, or is abandoned via
        # --forget), this reverts to the normal evaluable behavior on the
        # very next poll cycle -- no separate flag to remember to unset.
        _local_evaluable = not bool(_manifest.get("pending_restore"))

        _prune_settings, _prune_state, _prune_changed = prune_stale_keys(
            _prune_settings,
            _live_keys,
            pruning_state=_prune_state,
            grace_seconds=_grace_seconds,
            local_device_id=_local_device_id,
            known_remote_device_ids=_known_remote_device_ids,
            local_evaluable=_local_evaluable,
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
    global _server_start_time

    # Real startup watermark -- see this name's module-level declaration for
    # why it exists. Reset here (rather than relying solely on the
    # module-import-time default) so it reflects the moment THIS server
    # instance actually came up, which matters for a long-lived Python
    # process (e.g. a test session) that tears down and re-enters lifespan
    # multiple times.
    _server_start_time = time.time()

    # One-line frontend identity so "which JS is this server serving?" is a
    # glance at the startup log, not a debugging session.
    _app_js = _FRONTEND_DIR / "app.js"
    _log.info(
        "frontend: app.js %s",
        hashlib.md5(_app_js.read_bytes(), usedforsecurity=False).hexdigest()[:8],
    )

    # Validate the configured session command pairs once at startup, so a
    # broken settings.json is visible in the service journal at boot without
    # waiting for a request to GET /api/session-commands to surface it.
    _commands, _command_errors = resolve_session_commands()
    if _command_errors:
        for _err in _command_errors:
            _log.error("session_commands config error: %s", _err)
    else:
        _log.info("session_commands: %d pair(s) configured", len(_commands) - 1)

    # Startup, in order (docs/plans/2026-08-02-per-session-ttyd-plan.md §10.1):
    # 1. Validate the ttyd socket dir -- fail loud before anything else. A
    #    bad socket dir must abort startup, not surface later as a
    #    mysterious per-attach failure. Read via the module object (not a
    #    name imported at module load time) so a test's
    #    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", ...) is honored.
    ttyd_mod.validate_socket_dir(ttyd_mod.TTYD_SOCKET_DIR)
    # 2. Reap any per-session ttyds left running across a restart (identity
    #    checked against a fresh `ps` snapshot -- never an unconfirmed kill).
    await reap_orphan_ttyds()
    # 3. One-time migration: reap the pre-upgrade single ttyd if its PID file
    #    survived; detect-and-report (never sweep) a still-live legacy port.
    await reap_legacy_ttyd()
    # 4. Start the background poll loop.
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

    # Separate client (not federation_client) for the amplifier-agent chat
    # proxy: federation_client's 5s timeout is sized for a quick session-list
    # poll, not a model turn. read=None here because the agent's own SSE
    # contract emits a keepalive comment every 3s of silence specifically so
    # a long model turn or internal tool loop never trips a client read
    # timeout (see amplifier-agent docs/spec/http-face.md) -- this client
    # must not impose a shorter one of its own underneath that contract.
    app.state.agent_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
    )

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
        await asyncio.wait_for(kill_all_ttyd(), timeout=3.0)
    except Exception:
        _log.exception("ttyd shutdown error")

    try:
        client = getattr(app.state, "federation_client", None)
        if client is not None:
            await client.aclose()
    except Exception:
        _log.exception("federation_client aclose error")
    _federation_client = None

    try:
        agent_client = getattr(app.state, "agent_client", None)
        if agent_client is not None:
            await agent_client.aclose()
    except Exception:
        _log.exception("agent_client aclose error")

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

# ---------------------------------------------------------------------------
# amplifier-agent chat-panel proxy (POC) -- see /api/agent/chat/completions
# below. muxplex holds this bearer secret so it can call the sidecar's
# OpenAI-compatible HTTP face on the browser's behalf; the sidecar itself
# holds no muxplex credential of any kind (no cookie, no muxplex API key, no
# federation key) and cannot reach muxplex's loopback bypass -- it runs as a
# separate, network-isolated user (see iptables OUTPUT rule dropping that
# user's traffic to muxplex's port). This is the only bridge, and only ever
# flows browser -> muxplex -> agent, never the reverse.
_AGENT_PROXY_URL = os.environ.get("AMPLIFIER_AGENT_URL", "http://127.0.0.1:9099")
_AGENT_PROXY_TOKEN = os.environ.get("AMPLIFIER_AGENT_BEARER_TOKEN", "")

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
    sync_group: str | None = None


class CreateSessionPayload(BaseModel):
    name: str
    # Which configured command pair creates this session. None (the default,
    # and what every pre-existing client sends) resolves to the reserved
    # "default" pair -- i.e. settings.new_session_template -- so a request
    # body of {"name": "x"} is byte-identical to pre-feature behavior. The
    # id is looked up (settings.find_session_command); it is NEVER
    # interpolated into a command. See GET /api/session-commands for the
    # valid ids.
    command_id: str | None = None

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


class FollowupAppendPayload(BaseModel):
    """Body for POST /api/sessions/{name}/followups.

    text  -- typed literally into the pane (same tmux send-keys -l path as
             /input) when this item eventually fires.
    enter -- press Enter after text when this item fires (default True --
             the common case; /input defaults enter to False since it also
             supports a bare `keys` action, but a queued follow-up is
             always "type this line and submit it").
    """

    text: str
    enter: bool = True


class FollowupItemInput(BaseModel):
    """One item within a PUT /api/sessions/{name}/followups body.

    id, when present and matching an existing item, keeps that item's
    identity and created_at (spec §7.1) -- this is what makes reorder/edit
    expressible without the client inventing ids. Absent or unknown id ->
    treated as a new item.
    """

    id: str | None = None
    text: str
    enter: bool = True


class FollowupReplacePayload(BaseModel):
    """Body for PUT /api/sessions/{name}/followups.

    expected_revision is a REQUIRED precondition (unlike PATCH /api/settings'
    optional expected_settings_updated_at) -- the queue mutates itself, so a
    stale PUT built from a snapshot taken before a bell fired could re-add
    an item that has already been typed into the session (spec §7.1).
    """

    expected_revision: int
    items: list[FollowupItemInput]


class RenameSessionPayload(BaseModel):
    """Body for POST /api/sessions/{name}/rename.

    new_name -- the requested new session name. Rejected with 400 if it
        fails the shared charset allowlist OR would be silently mangled by
        tmux (contains '.') -- see sessions.is_tmux_stable_name().
    """

    new_name: str


class ViewRulePreviewPayload(BaseModel):
    """Body for POST /api/views/preview.

    match_names -- a DRAFT (not-yet-saved) list of glob patterns, exactly the
        shape the Manage View rule editor holds in its textarea. Never
        persisted; this endpoint is read-only.
    """

    match_names: list[str] = []


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


def _resolve_group_or_404(state: dict, device_id: str | None) -> str:
    """Resolve *device_id* to a sync group, or raise HTTP 404.

    Thin HTTP-boundary wrapper around state.resolve_group(): an unknown
    device_id must never silently fall back to the shared global group --
    that fall-through is exactly the yank this feature exists to prevent.
    """
    try:
        return resolve_group(state, device_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown device_id {device_id!r}; send POST /api/heartbeat first",
        ) from None


@app.get("/api/state")
async def get_state(device_id: str | None = None) -> dict:
    """Return the full persistent state, plus settings_updated_at.

    settings_updated_at mirrors settings.settings_updated_at (settings.py) --
    it is merged in here at request time, NOT persisted in state.json. This
    lets any client already polling /api/state (PWA, muxplex-deck, agents)
    detect a settings change -- including view membership edits, which are
    otherwise only visible via a dedicated GET /api/settings fetch -- without
    adding a second poll. Purely additive: existing consumers that don't look
    for this key are unaffected.

    device_id (optional query param): selects which sync group's
    active_session/active_remote_id/active_view is projected into the
    response, OVERWRITING those three top-level keys with that group's
    values (the raw per-group data stays visible in sync_groups). Omitting
    it means the shared "global" group -- byte-identical to today's
    behavior plus the additive sync_groups/terminal_session/terminal_group/
    sync_group keys, which are in state.json anyway. Unknown device_id ->
    404 (see _resolve_group_or_404).
    """
    state = await read_state()
    state["settings_updated_at"] = load_settings().get("settings_updated_at", 0.0)

    group = _resolve_group_or_404(state, device_id)
    if group != GLOBAL_GROUP:
        state.update(read_group_state(state, group))
    state["sync_group"] = group
    return state


@app.patch("/api/state")
async def patch_state(patch: StatePatch, device_id: str | None = None) -> dict:
    """Update fields in the persistent state and return the updated state.

    Only fields explicitly included in the request body are updated;
    omitted fields are left unchanged. Supports: session_order,
    active_session, active_remote_id, active_view.

    device_id (optional query param) selects which sync group
    active_session/active_remote_id/active_view route to via
    write_group_state(). session_order is NEVER group-scoped -- it
    describes the sessions, not a view of them -- so it always writes the
    top-level key regardless of device_id. Unknown device_id -> 404.
    """
    async with state_lock:
        state = load_state()
        group = _resolve_group_or_404(state, device_id)

        changed = patch.model_fields_set
        if "session_order" in changed:
            state["session_order"] = patch.session_order

        group_updates = {
            field: getattr(patch, field) for field in GROUP_FIELDS if field in changed
        }
        if group_updates:
            write_group_state(state, group, group_updates)

        save_state(state)

    if group != GLOBAL_GROUP:
        state.update(read_group_state(state, group))
    state["sync_group"] = group
    return state


@app.get("/api/sessions")
async def get_sessions() -> list[dict]:
    """Return list of sessions with name, snapshot, bell, and last-activity data.

    Each entry additionally carries `views`: the resolved list of user-view
    names this session belongs to (pins union glob-rule matches -- see
    `views.annotate_view_membership`). This is the mechanism that lets rule-
    based views reach every client polling this endpoint (PWA grid/counts/
    sidebar/Manage View, the soft deck's picker counts) without each one
    re-deriving membership from raw `settings.views` (docs/plans/2026-08-04-auto-views-plan.md §0.1).

    `created_at` is tmux's own `#{session_created}` (see
    `sessions.get_session_created_times()`) -- the raw timestamp, not a
    derived "is this new" boolean. It is the other half of the rule
    `_run_poll_cycle()` already applies server-side to seed a just-created
    session's bell (see step 5, "Ensure bell entries exist" below): a
    session is genuinely new to THIS process iff `created_at >=
    server_started_at`, where the latter is `GET /api/instance-info`'s
    `server_started_at`. A client needs BOTH values to reproduce that
    comparison; see docs/API_SEMANTICS.md for the full rationale for
    shipping the raw pair instead of a precomputed boolean. Absent exactly
    like `last_activity_at`: the key is always present, `null` when tmux
    reported no parseable `#{session_created}` for that session.

    `cwd` is tmux's own `#{pane_current_path}` for the session's active
    window's active pane (see `sessions.get_session_cwds()`), refreshed
    every poll cycle at zero additional subprocess cost. It is an
    OBSERVATION, not a stable identity: it moves whenever the user (or a
    process in the pane) `cd`s, and for a multi-window session it tracks
    whichever window is currently active. This is how one agent tells
    which repo a sibling session is working in -- see
    docs/API_SEMANTICS.md and
    docs/plans/2026-08-07-agent-surface-additive-plan.md section 6 for the
    full rationale, including the two runtime-measured cases (a TUI-held
    pane; an `amplifier-workspace`-created session) that motivated this
    wording. Same always-present/`null`-when-absent convention as
    `last_activity_at` and `created_at`.
    """
    names = get_session_list()
    snapshots = get_snapshots()
    activity = get_session_activity()
    created_times = get_session_created_times()
    cwds = get_session_cwds()
    state = await read_state()
    settings = load_settings()
    local_device_id = load_device_id()

    result = []
    for name in names:
        session_state = state.get("sessions", {}).get(name, {})
        bell = session_state.get("bell", empty_bell())
        result.append(
            {
                "name": name,
                # Synthetic key, used ONLY to resolve `views` below (a pin
                # stored in canonical `device_id:name` form -- the form
                # normalize_session_keys() produces -- needs a sessionKey to
                # match). NOT part of this endpoint's wire response (§0.3:
                # unchanged, no client relies on one here) -- popped below.
                "sessionKey": f"{local_device_id}:{name}",
                "snapshot": snapshots.get(name, ""),
                "bell": bell,
                "last_activity_at": activity.get(name),
                "created_at": created_times.get(name),
                "followups": followups.summary(state, name),
                "cwd": cwds.get(name),
            }
        )
    annotated = annotate_view_membership(result, settings)
    for s in annotated:
        s.pop("sessionKey", None)
    return annotated


@app.get("/api/sessions/{name}")
async def get_session_snapshot(
    name: str, lines: int = DEFAULT_CAPTURE_LINES, before: int | None = None
) -> dict:
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
    session on its own cycle. `MAX_CAPTURE_LINES` bounds the WINDOW size,
    not the reachable DEPTH -- see `before` below, which pages arbitrarily
    deep at the same per-request cost (measured: `capture-pane` cost is
    O(window requested), not O(depth) -- docs/plans/2026-08-07-scrollback-paging-plan.md §2.5).

    Retention (how much scrollback actually exists behind a request) is
    whatever the host's tmux config provides -- `history-limit 50000` under
    `muxplex tmux install` (tmux_templates/base.conf:28), tmux's
    compiled-in **2000** otherwise. muxplex does not set `history-limit`
    and cannot: the option binds a pane at creation time, not afterward
    (see docs/plans/2026-08-07-scrollback-paging-plan.md §1 for the
    runtime-measured proof that a post-creation `set-option` does not
    take). `saturated` (below) is how a caller learns whether it has hit
    that retention wall rather than the session's true beginning.

    Raises 404 if *name* is not an exact member of the known session set
    (same fail-closed pattern as connect/delete/input).

    Field parity with GET /api/sessions: this single-session read used to
    return only {name, snapshot, lines, bell, last_activity_at}, while the
    bulk read also carried created_at, followups, views, and (now) cwd. A
    caller that has narrowed to one session -- exactly the shape of an
    agent polling its own session -- could not see a halted follow-up
    queue at all. `created_at`, `followups`, `views`, and `cwd` are added
    here so polling ONE session and polling the bulk list never disagree
    about what the session's state is. `lines` keeps its exact existing
    meaning (the depth REQUESTED, not a parity field) and is unaffected.

    ## Scrollback paging: `before`

    `before` is an optional absolute row index (server-defined coordinate
    space: 0 = the oldest row currently retained, growing upward -- see
    docs/plans/2026-08-07-scrollback-paging-plan.md §2.3/§3.1). Omitting it is
    byte-identical to the pre-paging behavior. When given, the response
    contains the `lines` rows immediately OLDER than (exclusive of) `before`
    -- raw `-S`/`-E` passthrough is not offered because tmux's own
    coordinates drift under a live, growing pane and tmux clamps
    out-of-range requests silently (measured: plan §2.2/§2.4); the absolute
    `before` coordinate, converted to tmux's relative coordinates
    server-side on every request, is what avoids both.

    New response fields (always present, `before` or not):

    * `start` -- absolute index of the first row returned. The next
      (older) page is always `?before={start}`.
    * `row_count` -- rows actually returned (may be 0 for `before=0`).
    * `total` -- `history_size + pane_height`, the addressable range right
      now.
    * `has_more` -- whether anything older than `start` remains.
    * `saturated` -- `history_size >= history_limit`; True means this
      pane has evicted its oldest rows, so `has_more: false` here means
      "hit the retention wall", not "this is the true beginning".

    `before` must be in `[0, total]` (400 otherwise, with `total` in the
    message) -- an out-of-range request is always a 400, never a silently
    short/clamped 200, per this endpoint's existing `lines`-bounds
    discipline (docs/AGENT_GUIDE.md §6.3).
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

    if before is None:
        # Unchanged path: the exact `-S -{lines}` shape capture_pane() has
        # always used, with no `-E` (defaults to the bottom of the visible
        # screen) -- byte-identical to pre-paging behavior. Paired with the
        # SAME atomic history_size/pane_height/history_limit read so the
        # new response fields below can still be reported truthfully.
        h, p, hist_limit, snapshot = await capture_pane_window(name, -lines, None)
        total = h + p
        start = max(0, h - lines)
        row_count = total - start
    else:
        h0, p0, hist_limit0 = await capture_pane_metadata(name)
        total0 = h0 + p0
        if not (0 <= before <= total0):
            raise HTTPException(
                status_code=400,
                detail=f"before must be between 0 and {total0} (got {before})",
            )
        row_count = min(lines, before)
        start_guess = before - row_count
        if row_count == 0:
            # before=0: caller has already reached the beginning. A 4xx
            # here would be a lie -- 200 with an empty page is the honest
            # answer (plan §3.2).
            snapshot = ""
            h, p, hist_limit = h0, p0, hist_limit0
            start = start_guess
            total = total0
        else:
            rel_s = start_guess - h0
            rel_e = start_guess + row_count - 1 - h0
            h, p, hist_limit, snapshot = await capture_pane_window(name, rel_s, rel_e)
            # Report using the FRESH h paired with THIS capture, not h0 --
            # truthful regardless of any (growth-only) drift between the
            # probe and this call (plan §3.4).
            start = h + rel_s
            total = h + p

    has_more = start > 0
    saturated = h >= hist_limit

    activity = get_session_activity()
    state = await read_state()
    session_state = state.get("sessions", {}).get(name, {})
    bell = session_state.get("bell", empty_bell())
    settings = load_settings()
    local_device_id = load_device_id()
    entry = {
        "name": name,
        # Synthetic key, used ONLY to resolve `views` below -- same dance
        # as get_sessions(); NOT part of this endpoint's wire response,
        # popped after annotate_view_membership() below.
        "sessionKey": f"{local_device_id}:{name}",
        "snapshot": snapshot,
        "lines": lines,
        "bell": bell,
        "last_activity_at": activity.get(name),
        "created_at": get_session_created_times().get(name),
        "followups": followups.summary(state, name),
        "cwd": get_session_cwds().get(name),
        "start": start,
        "row_count": row_count,
        "total": total,
        "has_more": has_more,
        "saturated": saturated,
    }
    annotated = annotate_view_membership([entry], settings)[0]
    annotated.pop("sessionKey", None)
    return annotated


@app.get("/api/session-commands")
async def list_session_commands() -> dict:
    """Return the resolved, validated, ordered list of session command pairs.

    This is the canonical SERVER-SIDE resolution of `settings.session_commands`
    -- folding the legacy singular new_session_template/delete_session_template
    pair in as the reserved "default" entry (always index 0), excluding
    invalid entries, and surfacing validation errors. Clients MUST use this
    endpoint rather than re-deriving the fold from raw `GET /api/settings`
    data -- same rationale as `GET /api/view` (AGENTS.md: resolve rules
    server-side rather than shipping duplicate logic to every client).

    Auth: the shared middleware (Bearer / localhost bypass / session cookie).
    Deliberately NOT in auth._AUTH_EXEMPT_PATHS -- this discloses server-side
    shell commands.

    Response:
        commands   -- resolved list, never empty; commands[0].id == "default".
        default_id -- always the literal "default" (so clients never hardcode it).
        errors     -- [] when config is clean; otherwise one human-readable
                      string per rejected session_commands entry.
    """
    commands, errors = resolve_session_commands()
    return {"commands": commands, "default_id": RESERVED_COMMAND_ID, "errors": errors}


def _attention_order(sessions: list[dict]) -> list[dict]:
    """Tiered ordering for GET /api/view?sort=attention.

    Tier 1: needs_attention sessions, ordered by bell.last_fired_at desc.
    Tier 2: everything else, ordered by bell.last_fired_at desc (sessions
        that have never belled sort last) -- NOT last_activity_at, because
        that timestamp derives from tmux #{window_activity} and bumps on ANY
        pane output (spinners, redraws, status-line clocks), which reordered
        the grid on every ~2s poll cycle even with no real event; bell fires
        only on the actual agent-turn-completion signal, so ordering is
        stable between bells.

    There is deliberately NO separate "active session" tier. A prior
    revision (v0.38.1, commit e7b3929) added one to fix "the session I'm
    working in sinks to the bottom" -- but that diagnosis was wrong. The
    real cause was `_arm_bell_hook()` curling `http://` at a TLS port, so
    bells never delivered for an attached session and its bell.last_fired_at
    froze; fixed in the same release. With bells actually delivering, the
    actively-worked session rises on bell recency alone -- a dedicated
    active-session tier is not just redundant, it is actively wrong: it
    bumps a session because the user SELECTED it, when this sort's whole
    contract is to track agent-turn-completion events, not user navigation.
    It also masks bell-hook regressions -- if the hook breaks again, an
    active-session tier silently props the session up and hides the
    symptom that would otherwise reveal it. See docs/API_SEMANTICS.md's
    "?sort=attention" entry.

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

    tier2 = sorted(
        remaining,
        key=lambda s: (
            s["bell"].get("last_fired_at") is not None,
            s["bell"].get("last_fired_at") or 0,
        ),
        reverse=True,
    )
    return tier1 + tier2


@app.get("/api/view")
async def get_view(sort: str | None = None, device_id: str | None = None) -> dict:
    """Return the server-resolved current view: filtered, sorted sessions
    plus view metadata.

    This is the canonical home for view-resolution semantics that the PWA,
    muxplex-deck, and future agent clients would otherwise each have to
    re-implement: `filter_visible` membership/hidden rules, the
    needs-attention bell predicate, and sort ordering. See docs/API_SEMANTICS.md
    for the rationale; new clients should prefer this endpoint over re-deriving
    these rules.

    Query params:
        sort: omitted -> honor `settings.sort_order` the same way the PWA
            does today (`"alphabetical"` sorts by name; any other value
            preserves /api/sessions enumeration order, reported back as
            `"server"`). `"attention"` requests tiered ordering: sessions
            needing attention first (freshest bell first), then everything
            else ordered by bell.last_fired_at descending (sessions that
            have never belled sort last). There is no separate tier for the
            active session -- selecting a session does not change its
            position; see `_attention_order()`'s docstring. Any other value
            is rejected with 400 -- no silent fallback.

    Response shape:
        {
          "view": <active_view, echoed verbatim>,
          "views": ["all", <user view names, settings order>, "hidden"],
          "sort": "server" | "alphabetical" | "attention",
          "sessions": [
            {"name", "active", "needs_attention", "bell", "last_activity_at"}
          ],
        }

    `views` is "all" + user-defined views (settings order) + "hidden" last.
    "hidden" is a reserved pseudo-view -- never a member of settings.views
    (validate_view_name rejects it as a user view name) -- but it is
    already addressable as an active_view value (GET/PATCH /api/state) and
    `filter_visible` already treats it as a first-class case. Appending it
    here (rather than continuing to omit it) makes it discoverable to
    clients that build a browsable/cyclable list from this field alone --
    the soft deck's view picker (frontend/deck/deck.js) is the first such
    consumer, and previously had no way to reach "hidden" at all. Ordering
    mirrors the PWA's own view dropdown, which hardcodes "All Sessions"
    first and "Hidden" always last (see `renderViewDropdown()` in
    frontend/app.js) -- that dropdown does NOT read this field (it builds
    from `_serverSettings.views`/`hidden_sessions` directly), so this change
    does not alter the PWA's UI; it only extends what THIS endpoint reports
    to clients that do consume it.

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
    group = _resolve_group_or_404(state, device_id)
    group_state = read_group_state(state, group)
    active_view: str = group_state["active_view"]
    active_session = group_state["active_session"]

    local_device_id = load_device_id()
    names = get_session_list()
    activity = get_session_activity()
    raw_sessions = [
        {
            "name": name,
            "sessionKey": f"{local_device_id}:{name}",
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
            "followups": followups.summary(state, s["name"]),
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

    views = (
        ["all"]
        + [v.get("name", "") for v in (settings.get("views") or [])]
        + ["hidden"]
    )

    return {
        "view": active_view,
        "views": views,
        "sort": applied_sort,
        "sessions": resolved,
        "sync_group": group,
    }


@app.get("/api/views")
async def get_views() -> dict:
    """Return the resolved, validated set of user-defined views and their
    `match_names` rule errors.

    This is the canonical SERVER-SIDE resolution of `settings.views`'
    auto-updating glob rules -- the plural sibling of `GET /api/view`, and a
    one-for-one structural copy of `GET /api/session-commands` (this repo's
    established pattern for "canonical resolution + the validation errors
    that go with it"). Clients MUST use this endpoint rather than deciding
    rule validity themselves from raw `GET /api/settings` data -- same
    rationale as `GET /api/session-commands` (docs/plans/2026-08-04-auto-views-plan.md §5.4/§6.4).

    Reports USER-DEFINED views only -- no "all", no "hidden" (`GET /api/view`
    already publishes the cycle list including the pseudo-views; this
    endpoint is about definitions). `match_names` on each view contains only
    the patterns that will actually be used (invalid ones are absent and
    named in `errors`), so a client never has to decide validity for itself.

    Carries no session data, so it never goes stale as sessions come and go
    and costs nothing to fetch on a settings-change trigger rather than a
    poll -- the PWA's `followRemoteViewDefinitions()` is the reference
    consumer.

    Auth: the shared middleware (Bearer / localhost bypass / session
    cookie). Deliberately NOT in auth._AUTH_EXEMPT_PATHS.

    Response:
        views  -- [{name, sessions, match_names, errors}], each view's own
                  errors alongside its own resolution.
        errors -- flat list, all views, same strings as each view's own
                  `errors` (a client that only wants a single badge count
                  doesn't have to walk `views`).
    """
    settings = load_settings()
    raw_views = settings.get("views") or []
    flat_errors = validate_view_rules(raw_views)

    result_views = []
    for i, v in enumerate(raw_views):
        if not isinstance(v, dict):
            continue
        prefix = f"views[{i}] '{v.get('name', '')}':"
        view_errors = [e for e in flat_errors if e.startswith(prefix)]
        result_views.append(
            {
                "name": v.get("name", ""),
                "sessions": v.get("sessions") or [],
                "match_names": view_patterns(v),
                "errors": view_errors,
            }
        )

    return {"views": result_views, "errors": flat_errors}


@app.post("/api/views/preview")
async def preview_view_rule(payload: ViewRulePreviewPayload) -> dict:
    """Resolve a DRAFT, unsaved `match_names` list against currently-live local
    sessions -- the Manage View rule editor's live-match preview.

    Never writes anything. Validates and matches the SAME way a saved view
    would (`validate_view_rules` / `filter_visible`, exactly as `GET
    /api/views` and `GET /api/sessions` use) by wrapping the draft patterns in
    a throwaway, never-persisted view dict -- there is deliberately no second
    matcher here, per AGENTS.md's "the matcher lives in exactly one place."

    This lets the editor show "these N sessions match" and name a rejected
    pattern's exact reason (e.g. the `':'` rule) as the user types, before
    they ever attempt a save that would 400.

    Scope: local sessions only, same as `GET /api/view` (`\u00a70.1`/scope note in
    that handler) -- a pattern also matches identically-named sessions on
    other devices once saved; this preview only has visibility into this
    device's own current session list.

    Response:
        errors  -- validation error strings for structurally invalid entries
                   in *payload.match_names* (non-str, empty, or containing
                   ':'), same wording `GET /api/views` uses.
        matches -- names of currently-live local sessions any valid pattern
                   matches, order-preserving (session list order).
    """
    draft_view = {"name": "", "sessions": [], VIEW_RULE_KEY: payload.match_names}
    errors = [
        e.replace("views[0] '': ", "", 1) for e in validate_view_rules([draft_view])
    ]

    names = get_session_list()
    raw_sessions = [{"name": name} for name in names]
    matched = filter_visible(
        raw_sessions,
        {"views": [draft_view], "hidden_sessions": []},
        draft_view["name"] or "",
        include_hidden=True,
    )

    return {"errors": errors, "matches": [s["name"] for s in matched]}


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
    """Create a new session using the resolved command pair's
    new_session_template.

    Substitutes ``{name}`` in the template with the validated payload name,
    runs the command as an async subprocess, and waits up to 30 seconds for
    it to finish.  Returns ``{name, ok: True, command_id: ...}`` on success or
    ``{name, ok: False, error: ...}`` with HTTP 500 on failure so that the
    frontend can surface actionable errors instead of silently timing out.

    ``payload.command_id`` (optional) selects a configured session command
    pair -- see GET /api/session-commands. Omitting it (the default, and
    what every pre-existing client sends) resolves to the reserved
    "default" pair, i.e. today's ``new_session_template`` -- byte-identical
    to pre-feature behavior. An unresolvable id is a 400, before any
    subprocess runs.

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

    # Resolve the command pair BEFORE any subprocess -- an unknown id must
    # never spawn anything. Never falls back to the default; see
    # find_session_command()'s docstring.
    command = find_session_command(payload.command_id)
    if command is None:
        commands, _errors = resolve_session_commands()
        raise HTTPException(
            status_code=400,
            detail={
                "detail": (
                    f"Unknown command_id {payload.command_id!r}. Configured: "
                    f"{', '.join(c['id'] for c in commands)}."
                ),
                "unknown_command_id": True,
                "available": [c["id"] for c in commands],
            },
        )

    # The actual subprocess/shell-template logic lives in
    # sessions.spawn_session_command() -- extracted so that `muxplex restore`
    # (which creates sessions from the CLI, not this running server) shares
    # the exact same "how to create a session" implementation rather than a
    # second one that could drift. See its docstring and
    # SESSION_PERSISTENCE_DESIGN.md's "restore fidelity equals create
    # fidelity" principle.
    ok, error = await spawn_session_command(name, command_id=payload.command_id)
    if not ok:
        raise HTTPException(status_code=500, detail=error)

    # Record which pair created this session, so delete can automatically
    # run the matching teardown. Recorded AFTER success -- a failed create
    # writes nothing and leaves no garbage (see manifest.py's created_with
    # concurrency notes). Note: command["id"], not payload.command_id --
    # normalizes None to the literal "default" so the record is always
    # explicit.
    async with state_lock:
        manifest = load_manifest()
        manifest = set_created_with(manifest, name, command["id"])
        save_manifest(manifest)

    return {"name": name, "ok": True, "command_id": command["id"]}


@app.post("/api/sessions/{name}/connect")
async def connect_session(
    name: str, device_id: str | None = None, takeover: bool = False
) -> dict:
    """Ensure *name* has a live, per-session ttyd, and record it as this
    caller's (and the no-`?session=`-fallback's) active session.

    With one ttyd per session, `ensure_ttyd()` is idempotent: connecting to a
    session that's already live is free, and connecting to session X never
    disturbs any OTHER session's ttyd -- that is the entire point of this
    architecture (see docs/plans/2026-08-02-per-session-ttyd-plan.md).

    Returns {active_session: name, ttyd_port: 7682, sync_group, terminal_session}.
    `ttyd_port` is a legacy wire field -- see ttyd.py's module docstring --
    kept solely because `muxplex_client.parse_connect_result()` requires it.

    Raises HTTP 400 if *name* fails the session-name allowlist.
    Raises HTTP 404 if *name* is not an exact match in the known session list,
    or if *device_id* is unknown.
    Raises HTTP 500 if the ttyd fails to spawn/bind (TtydSpawnError) or HTTP
    503 if the server is at its ttyd capacity ceiling (TtydCapacityError) --
    both are new failure modes: today's single-ttyd endpoint never verified
    the spawn, so it could return 200 for a terminal that didn't exist.

    `takeover` is accepted and ignored: with no single shared terminal to
    seize, there is nothing left to take over. Kept in the signature so
    existing clients sending `&takeover=true` (terminal.js) don't 422.
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

    async with state_lock:
        state = load_state()
        group = _resolve_group_or_404(state, device_id)

    try:
        await ensure_ttyd(name)
    except TtydCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except TtydSpawnError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to start terminal for {name!r}: {exc}"
        )

    _log.info("Connecting to session '%s'", name)
    async with state_lock:
        state = load_state()
        write_group_state(state, group, {"active_session": name})
        state["terminal_session"] = name
        state["terminal_group"] = group
        save_state(state)

    return {
        "active_session": name,
        "ttyd_port": TTYD_PORT,
        "sync_group": group,
        "terminal_session": name,
    }


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
       ``["*"]`` -- EVERY session; it used to be empty, see settings.py's
       DEFAULT_SETTINGS comment) -- glob patterns; a session matching none
       of them is 403 even when the feature is enabled. Checked BEFORE
       existence so the endpoint never leaks whether a non-allowlisted
       session exists.
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
    # Strict-typed fence reads, fail CLOSED -- see input_allowed_for_session()
    # (terminal_input.py), the SAME evaluation the terminal WS input gate
    # uses (main.py's terminal_ws_proxy/client_to_ttyd) so the two can never
    # silently diverge. Only the boolean True enables the endpoint: a
    # hand-edited settings.json with `"input_enabled": "false"` (a truthy
    # string) must disable, not enable. Likewise the allowlist must be a
    # real list -- a string value would turn `name in allowed` into
    # substring matching and silently widen the fence.
    if settings.get("input_enabled") is not True:
        _log.warning("input: rejected for %r -- input_enabled is false", name)
        raise HTTPException(
            status_code=403,
            detail="Session input is disabled (settings.input_enabled=false)",
        )
    if not input_allowed_for_session(name, settings):
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


# ---------------------------------------------------------------------------
# Follow-up queues -- see docs/plans/2026-08-05-per-session-followup-queue-plan.md and muxplex/followups.py
# ---------------------------------------------------------------------------


async def _resolve_target_window(name: str) -> str | None:
    """Return ``"<index>:<name>"`` for session *name*'s CURRENT window, or
    None if tmux does not answer.

    Display-only (spec §7.3/§9.1): `tmux send-keys -t <session>` types into
    whatever window is CURRENT for that session at fire time, not
    necessarily the window that belled (§0.3's second finding) -- the queue
    deliberately does not try to be clever about targeting the belled
    window (that would mean storing a per-item window target, which is a
    workflow engine). Instead this is surfaced honestly so a human queuing
    follow-ups knows where they will land.
    """
    try:
        output = await run_tmux(
            "display-message", "-t", name, "-p", "#{window_index}:#{window_name}"
        )
        return output.strip() or None
    except RuntimeError:
        return None


def _followup_not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Session '{name}' not found")


@app.get("/api/sessions/{name}/followups")
async def get_followups(name: str) -> dict:
    """Read session *name*'s follow-up queue.

    Unknown-but-valid session with no queue returns revision 0, empty
    items, no halt -- an empty queue and an absent queue are the same thing
    to a client (spec §7.1).
    """
    _require_valid_session_name(name)
    if name not in get_session_list():
        raise _followup_not_found(name)

    async with state_lock:
        state = load_state()
        entry = followups.get_queue(state, name)

    target_window = await _resolve_target_window(name)
    return {
        "session": name,
        "revision": entry["revision"],
        "items": entry["items"],
        "halted": entry["halted"],
        "target_window": target_window,
    }


@app.post("/api/sessions/{name}/followups")
async def append_followup(name: str, payload: FollowupAppendPayload) -> dict:
    """Append one item to session *name*'s follow-up queue.

    Enqueue-time fence check (spec §6.2): rejects here with the SAME 403
    status/detail wording `/input` uses, so the compose bar's existing
    `_composeErrorMessage()` branches apply unchanged -- this is a UX
    convenience (the user learns now, not after a bell), NOT the safety
    boundary. The safety boundary is the re-evaluation inside
    `_advance_followup_queue()` at fire time, against fresh settings, which
    runs regardless of what was true when this request was accepted.
    """
    _require_valid_session_name(name)
    if name not in get_session_list():
        raise _followup_not_found(name)

    settings = load_settings()
    if settings.get("input_enabled") is not True:
        raise HTTPException(
            status_code=403,
            detail="Session input is disabled (settings.input_enabled=false)",
        )
    if not input_allowed_for_session(name, settings):
        raise HTTPException(
            status_code=403,
            detail=f"Session '{name}' does not match any input_allowed_sessions pattern",
        )
    if not _bell_hook_armed:
        # A queue armed against a dead trigger is worse than no queue at
        # all (spec §0.2/§6.5) -- refuse to accept new items while the
        # bell hook is not even registered with tmux, rather than accepting
        # them into a queue nothing will ever advance. NOTE: "armed" means
        # registration only (see _bell_hook_armed's module comment) -- it
        # does not prove delivery, so this guard cannot catch every case
        # where the hook is registered but silently misconfigured (e.g. a
        # scheme mismatch); it still catches the common case (tmux not up).
        raise HTTPException(
            status_code=409,
            detail={
                "bell_hook_unarmed": True,
                "detail": _bell_hook_last_error
                or "bell hook is not armed -- follow-ups would never fire",
            },
        )
    if len(payload.text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"text too large (max {MAX_TEXT_BYTES} bytes UTF-8)",
        )

    async with state_lock:
        state = load_state()
        existing = followups.get_queue(state, name)
        if len(existing["items"]) >= followups.MAX_FOLLOWUPS:
            raise HTTPException(
                status_code=409,
                detail={"queue_full": True, "max": followups.MAX_FOLLOWUPS},
            )
        item = followups.append_item(state, name, payload.text, payload.enter)
        entry = state["followups"][name]
        save_state(state)

    return {"session": name, "revision": entry["revision"], "item": item}


@app.put("/api/sessions/{name}/followups")
async def replace_followups(name: str, payload: FollowupReplacePayload) -> dict:
    """Replace session *name*'s ENTIRE follow-up list -- edit + reorder +
    remove in one call (spec §7.1). ``expected_revision`` is a REQUIRED
    precondition: a stale PUT built from a snapshot taken before a bell
    fired could re-add an item that has already been typed into the
    session, which is not a lost update -- it is a second execution.
    """
    _require_valid_session_name(name)
    if name not in get_session_list():
        raise _followup_not_found(name)

    if len(payload.items) > followups.MAX_FOLLOWUPS:
        raise HTTPException(
            status_code=409,
            detail={"queue_full": True, "max": followups.MAX_FOLLOWUPS},
        )
    for item_in in payload.items:
        if len(item_in.text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"text too large (max {MAX_TEXT_BYTES} bytes UTF-8)",
            )

    async with state_lock:
        # Closes the last hole in the peek-send-remove race: without this, a
        # PUT landing between peek and remove could resurrect the item
        # currently being sent (spec §7.1).
        if followups.is_sending(name):
            raise HTTPException(status_code=409, detail={"send_in_flight": True})

        state = load_state()
        raw_items = [it.model_dump() for it in payload.items]
        ok, error = followups.replace_items(
            state, name, payload.expected_revision, raw_items
        )
        if not ok:
            raise HTTPException(status_code=409, detail=error)
        save_state(state)
        entry = followups.get_queue(state, name)

    return {
        "session": name,
        "revision": entry["revision"],
        "items": entry["items"],
        "halted": entry["halted"],
    }


@app.delete("/api/sessions/{name}/followups")
async def clear_followups(name: str) -> dict:
    """Clear session *name*'s follow-up queue entirely -- items AND any
    halt (spec §7.1). Distinct from POST .../resume, which clears only the
    halt."""
    _require_valid_session_name(name)
    if name not in get_session_list():
        raise _followup_not_found(name)

    async with state_lock:
        state = load_state()
        followups.clear_queue(state, name)
        save_state(state)

    return {"session": name, "revision": 0, "items": [], "halted": None}


@app.post("/api/sessions/{name}/followups/resume")
async def resume_followups(name: str) -> dict:
    """Clear session *name*'s follow-up halt only, keeping every pending
    item and the current revision (spec §7.1). Nothing else clears a halt
    -- there is no implicit unhalt as a side effect of any edit, because a
    silent unhalt is how an autonomous writer restarts without anyone
    deciding it should.
    """
    _require_valid_session_name(name)
    if name not in get_session_list():
        raise _followup_not_found(name)

    async with state_lock:
        state = load_state()
        followups.resume_queue(state, name)
        entry = followups.get_queue(state, name)
        save_state(state)

    return {
        "session": name,
        "revision": entry["revision"],
        "items": entry["items"],
        "halted": entry["halted"],
    }


def _bell_for_halt(state: dict, name: str) -> None:
    """Ring session *name*'s bell because its follow-up queue just halted.

    Writes state["sessions"][name]["bell"] DIRECTLY, never via receive_bell()
    or process_bell_flags() -- the queue's advance hangs off exactly those two
    functions, so routing this through either would make the queue trigger
    itself. The exclusion is structural (a property of where this code
    lives), identical in kind and rationale to the seeded-bell exclusion
    documented in AGENTS.md's "Follow-up queue" section. Do not route it
    through either "for consistency."

    Cannot loop, for two independent reasons (see
    docs/plans/2026-08-07-bell-causality-plan.md §5.3, both covered by
    test_followups.py):
      1. Structural -- this is a plain state write; nothing here calls
         _advance_followup_queue().
      2. Behavioral -- even if a later real bell arrives,
         followups.acceptance_ok() returns False while the queue's
         ``halted`` is not None, so a halted queue cannot advance at all
         until someone explicitly resumes it.

    Fires at most once per halt transition: set_halted() has exactly one
    production call site (the halt branch below), reached only from this
    advance path, which itself only runs on a bell. Once halted,
    acceptance_ok() is False, so no further advance, so no further halt, so
    no further call to this function for the same halt.
    """
    session = state.setdefault("sessions", {}).setdefault(name, {})
    bell = session.setdefault("bell", empty_bell())
    bell["unseen_count"] = bell.get("unseen_count", 0) + 1
    bell["last_fired_at"] = time.time()
    bell["source"] = "halt"


async def _advance_followup_queue(name: str) -> None:
    """Advance session *name*'s follow-up queue by exactly one item, if a
    bell was just accepted for it (spec §5.2: peek-send-remove, never
    pop-send).

    Called after EVERY bell-fired state transition this process detects --
    both receive_bell() (the tmux hook, main.py) and process_bell_flags()
    (the poll fallback, bells.py, but ONLY while the hook is unarmed -- see
    that function's on_transition docstring for why triggering off BOTH
    unconditionally would double-advance a detached session's queue) -- so
    the queue is not stuck relying on exactly one physical code path.
    NEVER called from the bell-seeding branch in _run_poll_cycle (that
    branch assigns state["sessions"][name]["bell"] directly, never through
    receive_bell()/process_bell_flags()) -- that omission is what
    structurally keeps a freshly-created session's seeded "look at me" bell
    from draining someone's queued follow-ups (spec §4; see
    test_followups.py's seeded-bell isolation test).

    Step 1 (peek, under state_lock), step 2 (fence + send, outside the
    lock -- a subprocess must not run while the poll cycle's lock is
    held), step 3 (remove-by-id or halt, under state_lock again), step 4
    (finally: discard the in-flight marker on every path).
    """
    item: dict | None = None
    async with state_lock:
        state = load_state()
        if not followups.acceptance_ok(state, name):
            return
        entry = state["followups"][name]
        item = entry["items"][0]
        followups._followup_sending.add(name)
        followups._followup_last_send_at[name] = time.time()
        save_state(state)

    assert item is not None  # acceptance_ok() guarantees a non-empty items list
    halt_reason: str | None = None
    halt_detail: str = ""
    try:
        # The queue is a THIRD caller of input_allowed_for_session() -- the
        # same fence /input and the terminal WS gate already both use (spec
        # §6.1). Re-evaluated against FRESH settings at fire time -- this is
        # the evaluation that actually matters; the append-time check is UX
        # only. No bypass, no "the server is trusted."
        settings = load_settings()
        if settings.get("input_enabled") is not True:
            halt_reason = "input_disabled"
            halt_detail = "Session input is disabled (settings.input_enabled=false)"
        elif not input_allowed_for_session(name, settings):
            halt_reason = "input_not_allowed"
            halt_detail = (
                f"Session '{name}' does not match any input_allowed_sessions pattern"
            )
        elif name not in get_session_list():
            halt_reason = "session_missing"
            halt_detail = f"Session '{name}' not found"
        else:
            # Only the actual send is expected to fail this way (a vanished
            # session, a broken tmux). A failure HERE becomes an ordinary
            # halt, retaining the item.
            try:
                await run_tmux(*build_send_text_argv(name, item["text"]))
                if item.get("enter", True):
                    await run_tmux(*build_send_key_argv(name, "Enter"))
            except (RuntimeError, OSError) as exc:
                halt_reason = "send_failed"
                halt_detail = str(exc)
    except BaseException:
        # An exception from the FENCE evaluation itself (e.g.
        # input_allowed_for_session raising) is not a normal send failure --
        # there is exactly one fence implementation and no path here may
        # swallow its errors into an ordinary halt (spec §6.1/T-17). Clean
        # up the in-flight marker and propagate; the item is left exactly
        # where it was peeked (still in the list, not halted) since we do
        # not know what an unexpected fence error actually means.
        followups._followup_sending.discard(name)
        raise

    async with state_lock:
        state = load_state()
        entry = state.get("followups", {}).get(name)
        if entry is None:
            # The queue was cleared (DELETE) while the send was in flight --
            # nothing to record either way; a cleared queue must stay
            # cleared, never resurrected by a race.
            pass
        elif halt_reason is None:
            followups.remove_item_by_id(state, name, item["id"])
            save_state(state)
        else:
            followups.set_halted(state, name, halt_reason, halt_detail, item["id"])
            _log.warning(
                "followups: halted for %r -- %s: %s", name, halt_reason, halt_detail
            )
            _bell_for_halt(state, name)
            save_state(state)
    followups._followup_sending.discard(name)


@app.delete("/api/sessions/current")
async def delete_current_session(device_id: str | None = None) -> dict:
    """Disconnect the caller's own session and, if no one else is relaying
    it, kill that session's ttyd.

    Always clears the caller's own group `active_session`. The ttyd is only
    killed when `relay_count(mine) == 0` -- a structural refcount check, not
    a group-ownership claim: two devices (in the SAME or DIFFERENT groups)
    co-viewing one session share one ttyd, and one of them disconnecting
    must never black out the other's live terminal (AGENTS.md).

    Returns {active_session: None, sync_group, terminal_released}.
    `terminal_released` keeps its meaning exactly: "this call tore down a
    terminal process."
    Raises HTTP 404 if *device_id* is unknown.
    """
    async with state_lock:
        state = load_state()
        group = _resolve_group_or_404(state, device_id)
        mine = read_group_state(state, group)["active_session"]
        write_group_state(state, group, {"active_session": None})
        save_state(state)

    released = False
    if mine is not None and relay_count(mine) == 0:
        released = await kill_ttyd(mine)

    return {
        "active_session": None,
        "sync_group": group,
        "terminal_released": released,
    }


@app.delete("/api/sessions/{name}")
async def delete_session(name: str, force: bool = False) -> dict:
    """Kill/destroy a tmux session using the command pair it was created with.

    Reads the pair's delete_session_template, substitutes {name}, and runs it
    synchronously (30s timeout) so the caller can rely on the session being
    gone on return.

    No `command_id` input on this endpoint -- deliberate, not an oversight.
    The pair a session was created with is looked up automatically
    (manifest.get_created_with); the user should never have to remember what
    made a session, which is what makes it a "pair". Accepting a
    caller-chosen command_id here would also let any authenticated caller
    run pair A's teardown command against a pair-B session -- a capability
    with no use case.

    Resolution:
    - No record for *name* (a pre-existing tmux session, or one created
      outside muxplex): use the "default" pair. This is not a fallback --
      it is today's behavior, unchanged.
    - A record exists but no longer resolves (the pair was deleted/renamed
      in settings): refuse with 409 (`unknown_command_id: true`) and run
      NOTHING, unless `force=true`, which substitutes the default pair and
      logs a warning naming the missing id. Never silently substitutes --
      that is the exact failure this feature exists to prevent.

    Returns {ok: True, name: name, command_id: ...} (+ forced: True on the
    force path). Errors are logged as warnings — the endpoint always
    returns 200 on a run command failure so the UI can refresh and reflect
    the gone session (unchanged contract).
    400 if *name* fails the session-name allowlist.
    404 if session is not an exact match in the known session list.
    409 if the recorded command_id no longer resolves and force is not set.
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
    recorded = get_created_with(load_manifest(), name)
    forced = False
    if recorded is None:
        # No record: a pre-existing / not-muxplex-created session. Use the
        # default pair -- byte-identical to pre-feature behavior.
        command = find_session_command(None, settings)
        assert command is not None  # the "default" entry always resolves
    else:
        command = find_session_command(recorded, settings)
        if command is None:
            if not force:
                commands, _errors = resolve_session_commands(settings)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": (
                            f"Session {name!r} was created with command {recorded!r}, "
                            "which is no longer configured. Restore it in "
                            "~/.config/muxplex/settings.json, or retry with "
                            "?force=true to use the default kill command. "
                            "See Settings \u203a Commands for the configuration error."
                        ),
                        "unknown_command_id": True,
                        "command_id": recorded,
                        "name": name,
                        "available": [c["id"] for c in commands],
                    },
                )
            _log.warning(
                "delete_session: recorded command_id %r for %r no longer "
                "configured; ?force=true -- substituting the default pair",
                recorded,
                name,
            )
            command = find_session_command(None, settings)
            assert command is not None  # the "default" entry always resolves
            forced = True

    # create/delete templates are BOTH arbitrary user shell commands with a
    # {name} placeholder (default `tmux kill-session -t {name}`, but users
    # configure e.g. `amplifier-dev --destroy {name}`), so this path stays
    # shell-based to preserve that feature. Injection is closed by two layers:
    # (1) the allowlist above guarantees the name has no shell metacharacters;
    # (2) shlex.quote() is applied as defense-in-depth in case the allowlist is
    # ever loosened. For an allowlist-valid name shlex.quote() is a no-op.
    command_str = command["delete_session_template"].replace(
        "{name}", shlex.quote(name)
    )

    _log.info("Deleting session '%s' with command: %s", name, command_str)
    try:
        result = subprocess.run(
            command_str,
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
        _log.warning("Delete command timed out after 30s: %r", command_str)
    except Exception:
        _log.warning("Delete command failed: %r", command_str)

    result_body = {"ok": True, "name": name, "command_id": command["id"]}
    if forced:
        result_body["forced"] = True
    return result_body


# ---------------------------------------------------------------------------
# Session rename -- see docs/plans/2026-08-07-session-rename-plan.md
# ---------------------------------------------------------------------------


def _bearer_only_caller(request: Request) -> bool:
    """Classify an already-authorized HTTP request as ``bearer_only`` or not.

    Mirrors ``_ws_auth_check``'s ``WSAuth`` classification for the terminal
    WebSocket (see that function's docstring for the full rationale) -- a
    verified ``muxplex_session`` cookie is never ``bearer_only``; only a
    request authorized SOLELY by the federation Bearer key is. By the time
    this runs, ``AuthMiddleware`` has already confirmed the request is
    authorized by ONE of cookie / Bearer / Basic credentials (see auth.py)
    -- this only determines WHICH, for the one caller (POST .../rename) that
    behaves differently for ``bearer_only`` callers than for every other
    authorized caller.

    A request authorized via HTTP Basic (a script holding real PAM/password
    login credentials, never issued a session cookie) is deliberately
    classified as NOT ``bearer_only`` here, same as a cookie -- it required
    knowing the operator's actual login credentials, strictly MORE trust
    than the shared federation Bearer key this fence exists to constrain.

    NOTE: this used to short-circuit to ``False`` (i.e. "as trusted as a
    cookie") for any socket peer at 127.0.0.1/::1, mirroring the auth
    middleware's now-removed loopback bypass (GHSA-7c6r-fvrh-9qp4). That
    check is gone: a re-originated proxy connection presents the same
    127.0.0.1 peer for a genuinely remote Bearer-only caller, and there is
    no socket-level signal that tells the two apart. A Bearer-only caller
    is ``bearer_only`` regardless of which address it appears to come from.
    """
    cookie = request.cookies.get("muxplex_session")
    if cookie and verify_session_cookie(_auth_secret, cookie, _auth_ttl):
        return False
    bearer_ok = False
    if _federation_key:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            bearer_ok = hmac.compare_digest(auth_header[7:], _federation_key)
    return bearer_ok


def _migrate_session_name(
    state: dict,
    settings: dict,
    manifest: dict,
    pruning_state: dict,
    old_name: str,
    new_name: str,
    local_device_id: str,
) -> tuple[dict, dict[str, object]]:
    """Idempotent migration of every keyspace tracking *old_name* to
    *new_name* (docs/plans/2026-08-07-session-rename-plan.md \u00a72/\u00a76.2).

    Called from BOTH the rename endpoint (after tmux confirms the rename)
    AND the poll cycle's journal-completion branch -- every step below is
    "move key X to key Y if X exists; leave Y alone if it's already right",
    so calling this twice for the same (old_name, new_name) pair is always
    safe, which is exactly what makes the write-ahead journal recoverable.

    Mutates `state`, `settings`, and `pruning_state` IN PLACE -- matching
    this codebase's established mutate-then-save convention for those three
    files (followups.py/views.py's helpers do the same). `manifest` is
    threaded through and RETURNED as a new dict instead, matching
    manifest.py's own pure-function convention (every helper there returns
    a new dict rather than mutating in place) -- callers must reassign
    their local `manifest` variable to the returned value.

    Returns ``(new_manifest, migrated)`` where `migrated` is the
    per-keyspace evidence dict returned to the API caller (\u00a74's response
    shape): ``{bell, followups, view_pins, hidden, created_with, order,
    manifest, pruning}``.

    Tier 3 (deliberately NOT migrated, per \u00a72.3): `input_allowed_sessions`
    globs (migrating would be a privilege escalation) and `views[*]
    .match_names` globs (would violate AGENTS.md's standing prohibition on
    materializing a rule match back into `sessions`). Neither is touched
    anywhere in this function -- there is nothing to call, which is the
    point.
    """
    migrated: dict[str, object] = {
        "bell": False,
        "followups": 0,
        "view_pins": 0,
        "hidden": False,
        "created_with": False,
        "order": False,
        "manifest": False,
        "pruning": 0,
    }

    # ---- state.json: bell (\u00a72.1 item 6) ----
    sessions_state = state.setdefault("sessions", {})
    old_session_entry = sessions_state.pop(old_name, None)
    if old_session_entry is not None:
        sessions_state[new_name] = old_session_entry
        migrated["bell"] = True

    # ---- state.json: session_order (\u00a72.2 item 7) -- .index() replace
    # preserves the user's manual position, unlike main.py's poll cycle,
    # which appends a newly-seen name to the end.
    order = state.setdefault("session_order", [])
    if old_name in order:
        order[order.index(old_name)] = new_name
        migrated["order"] = True

    # ---- state.json: followups (\u00a72.1 item 2) ----
    followups_map = state.setdefault("followups", {})
    old_queue = followups_map.pop(old_name, None)
    if old_queue is not None:
        followups_map[new_name] = old_queue
        migrated["followups"] = len(old_queue.get("items", []))

    # ---- in-memory: bells._bell_seen / followups._followup_last_send_at
    # (\u00a72.2 item 9) -- two-line dict moves in the modules that own them.
    # `_followup_sending` is never touched: the rename endpoint's send-in-
    # flight check (\u00a77.4) guarantees old_name is never a member at this
    # point.
    if old_name in bells_mod._bell_seen:
        bells_mod._bell_seen[new_name] = bells_mod._bell_seen.pop(old_name)
    if old_name in followups._followup_last_send_at:
        followups._followup_last_send_at[new_name] = (
            followups._followup_last_send_at.pop(old_name)
        )

    # ---- settings.json: views[*].sessions / hidden_sessions (\u00a72.1 items
    # 3/4) -- device_id:name pins. Deduped (set-union add, never append) so
    # an orphaned pin already sitting under new_name (\u00a77.2's "Inherit" row)
    # is never duplicated by this replace.
    old_key = f"{local_device_id}:{old_name}"
    new_key = f"{local_device_id}:{new_name}"

    pins_moved = 0
    for view in settings.get("views") or []:
        view_sessions = view.get("sessions")
        if not isinstance(view_sessions, list) or old_key not in view_sessions:
            continue
        seen: set[str] = set()
        deduped: list[str] = []
        for key in view_sessions:
            replaced = new_key if key == old_key else key
            if replaced not in seen:
                seen.add(replaced)
                deduped.append(replaced)
        view["sessions"] = deduped
        pins_moved += 1
    migrated["view_pins"] = pins_moved

    hidden = settings.get("hidden_sessions")
    if isinstance(hidden, list) and old_key in hidden:
        seen = set()
        deduped = []
        for key in hidden:
            replaced = new_key if key == old_key else key
            if replaced not in seen:
                seen.add(replaced)
                deduped.append(replaced)
        settings["hidden_sessions"] = deduped
        migrated["hidden"] = True

    # ---- manifest (sessions.json): created_with (\u00a72.1 item 1) ----
    created_with_map = dict(manifest.get("created_with", {}))
    command_id = created_with_map.pop(old_name, None)
    if command_id is not None:
        created_with_map[new_name] = command_id
        manifest = {**manifest, "created_with": created_with_map}
        migrated["created_with"] = True

    # ---- manifest (sessions.json): sessions[name] + renamed_from (\u00a72.1
    # item 5, \u00a79.3) -- \u00a77.2: a stale sessions[new_name] entry is guaranteed
    # garbage (the poll cycle's tombstone loop would have popped it the
    # instant new_name went live); overwrite outright.
    manifest_sessions = dict(manifest.get("sessions", {}))
    old_entry = manifest_sessions.pop(old_name, None)
    if old_entry is not None:
        new_entry = dict(old_entry)
        new_entry["renamed_from"] = old_name
        manifest_sessions[new_name] = new_entry
        manifest = {**manifest, "sessions": manifest_sessions}
        migrated["manifest"] = True

    # ---- pruning.json: first_missed_at (\u00a72.2 item 8, \u00a77.2 last row) ----
    first_missed = pruning_state.setdefault("first_missed_at", {})
    pruning_moved = 0
    old_prune_key = f"{local_device_id}:{old_name}"
    if old_prune_key in first_missed:
        del first_missed[old_prune_key]
        pruning_moved += 1
    new_prune_key = f"{local_device_id}:{new_name}"
    if new_prune_key in first_missed:
        # The name is live again -- exactly the condition that clears a
        # grace clock, not a collision (\u00a77.2).
        del first_missed[new_prune_key]
        pruning_moved += 1
    migrated["pruning"] = pruning_moved

    return manifest, migrated


@app.post("/api/sessions/{name}/rename")
async def rename_session(
    name: str, payload: RenameSessionPayload, request: Request
) -> dict:
    """Rename a live tmux session, migrating every keyspace that tracks it
    by name (docs/plans/2026-08-07-session-rename-plan.md).

    This is a keyspace migration with a write-ahead journal, not a one-line
    wrapper around `tmux rename-session` -- see the plan's \u00a76 for why a
    journal is the only way the operation can be correct at all under this
    codebase's established no-subprocess-under-`state_lock` discipline.

    Execution order (\u00a711) -- steps 1-5 cost nothing to fail; nothing has
    changed until step 6:
        1. Validate new_name (charset + is_tmux_stable_name)      -> 400
        2. Fence (bearer_only callers only)                       -> 403
        3. Exact membership of {name}                             -> 404
        3b. new_name == name -> no-op (\u00a77.3), nothing migrates    -> 200
        4. Collision pre-flight (\u00a77)                              -> 409
        5. Send-in-flight check (followups.is_sending)             -> 409
        6. Write journal (fsync'd, via save_manifest())
        7. tmux rename-session -t =<old> -- <new> (argv, no shell) -> 409
        8. Re-enumerate and verify the observed name               -> 500
        9. _migrate_session_name(old, observed) -- idempotent
        10. kill_ttyd(old) (\u00a72.4 -- closes the stale-typing-path hole)
        11. Clear the journal
        12. 200 with per-keyspace migration evidence

    Deliberately no `command_id` -- rename runs no template at all, so
    there is nothing to select (same reasoning DELETE's docstring uses).
    """
    new_name = payload.new_name

    # 0/1. Security boundary + charset/mangling validation, BEFORE any
    # subprocess or state read. `name` (the path param) is validated the
    # same way every session-lifecycle endpoint validates a client-supplied
    # name that will reach a subprocess.
    _require_valid_session_name(name)
    if not is_tmux_stable_name(new_name):
        suggested = new_name.replace(".", "_")
        detail: dict[str, object] = {
            "detail": (
                f"{new_name!r} is not a stable tmux session name. "
                "tmux 3.4 silently converts '.' to '_' in session names, or "
                "the name fails the shared charset allowlist entirely; "
                "request a name that survives unchanged."
            ),
            "invalid_session_name": True,
        }
        if is_valid_session_name(suggested):
            detail["suggested"] = suggested
        raise HTTPException(status_code=400, detail=detail)

    # 2. Fence -- rename is the fourth caller of
    # terminal_input.input_allowed_for_session(), evaluated against BOTH
    # names, and ONLY for bearer_only callers (\u00a710.2). input_enabled is
    # already checked inside input_allowed_for_session(), so there is
    # nothing further to re-derive here.
    if _bearer_only_caller(request):
        fence_settings = load_settings()
        if not (
            input_allowed_for_session(name, fence_settings)
            and input_allowed_for_session(new_name, fence_settings)
        ):
            _log.warning(
                "rename: rejected bearer-only caller for %r -> %r -- fence "
                "denies old or new name",
                name,
                new_name,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "detail": (
                        f"Renaming {name!r} to {new_name!r} is not permitted "
                        "for this caller."
                    ),
                    "rename_not_allowed": True,
                },
            )

    # 3. Fail closed: exact membership in the known set.
    known = get_session_list()
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found")

    # 3b. \u00a77.3: after the charset/mangling rejection above, new_name == name
    # can only mean a literal no-op. Nothing to rename, nothing migrates.
    if new_name == name:
        return {"ok": True, "from": name, "name": name, "renamed": False}

    # 4. Collision pre-flight (\u00a77) -- pre-check against the known list AND
    # (below) tmux's own rc=1, since a session can appear between this
    # check and the actual rename call.
    if new_name in known:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": f"Session {new_name!r} already exists.",
                "rename_target_exists": True,
            },
        )

    async with state_lock:
        state = load_state()
        manifest = load_manifest()

        # \u00a77.2: a stale follow-up queue under new_name is user-authored text
        # queued for a DIFFERENT session -- the one keyspace where reusing
        # the name is genuinely dangerous.
        if new_name in state.get("followups", {}):
            raise HTTPException(
                status_code=409,
                detail={
                    "detail": (
                        f"Session {new_name!r} has a queued follow-up queue "
                        "belonging to a different session; refusing to "
                        "reuse the name."
                    ),
                    "queue_target_conflict": True,
                },
            )

        # \u00a77.2: new_name is queued for restore -- taking the name now would
        # make a later `muxplex restore` fail confusingly.
        pending = manifest.get("pending_restore") or {}
        if new_name in (pending.get("sessions") or {}):
            raise HTTPException(
                status_code=409,
                detail={
                    "detail": f"Session {new_name!r} is pending restore.",
                    "pending_restore_conflict": True,
                },
            )

        # 5. Send-in-flight check -- reusing the existing precondition
        # rather than inventing a second one (\u00a77.4). This is also what
        # guarantees `_followup_sending` never contains `name` at migration
        # time, so the in-memory move above only ever has
        # `_followup_last_send_at` to carry.
        if followups.is_sending(name):
            raise HTTPException(
                status_code=409,
                detail={
                    "detail": (
                        f"A follow-up send is currently in flight for {name!r}."
                    ),
                    "rename_send_in_flight": True,
                },
            )

        # ---- 6. Write journal, fsync'd, BEFORE anything else changes ----
        manifest = start_rename_journal(manifest, name, new_name)
        save_manifest(manifest)

    # ---- 7. tmux rename-session -t =<old> -- <new> (argv, no shell) ----
    try:
        await rename_tmux_session(name, new_name)
    except RuntimeError as exc:
        # tmux rc=1 ("duplicate session") -- a session appeared between the
        # pre-flight check and this call. Nothing on tmux's side changed;
        # clear the journal, migrate nothing.
        async with state_lock:
            manifest = load_manifest()
            manifest = clear_rename_journal(manifest)
            save_manifest(manifest)
        _log.warning("rename: tmux refused %r -> %r: %s", name, new_name, exc)
        raise HTTPException(
            status_code=409,
            detail={
                "detail": f"tmux refused to rename {name!r} to {new_name!r}: {exc}",
                "rename_target_exists": True,
            },
        )

    # ---- 8. Verify the observed name (\u00a75.2) -- tmux reports rc=0 even when
    # it silently mangled the result. is_tmux_stable_name() closes the only
    # KNOWN mangling case ('.'); this is belt-and-braces against an unknown
    # future one.
    known_before = set(known)
    observed_names = await enumerate_sessions()
    verification_failed = False
    observed: str | None
    if new_name in observed_names:
        observed = new_name
    else:
        verification_failed = True
        candidates = sorted(set(observed_names) - known_before - {new_name})
        observed = candidates[0] if len(candidates) == 1 else None
        _log.error(
            "rename: verification failed for %r -> %r; tmux reported success "
            "but observed=%r (candidates=%r)",
            name,
            new_name,
            observed,
            candidates,
        )

    if observed is None:
        async with state_lock:
            manifest = load_manifest()
            manifest = clear_rename_journal(manifest)
            save_manifest(manifest)
        raise HTTPException(
            status_code=500,
            detail={
                "detail": (
                    f"tmux reported success renaming {name!r} to {new_name!r}, "
                    "but no observed session name could be determined."
                ),
                "rename_verification_failed": True,
                "observed": None,
            },
        )

    # ---- 9. migrate_session_name(old, observed) -- idempotent, all
    # keyspaces. Completes against the OBSERVED name even in the
    # verification-failed branch (\u00a75.2: "the tmux session is what it is") --
    # the response tells the truth, the keyspaces stay consistent with
    # reality.
    async with state_lock:
        state = load_state()
        settings = load_settings()
        manifest = load_manifest()
        pruning_state = load_pruning_state()
        local_device_id = load_device_id()

        manifest, migrated = _migrate_session_name(
            state, settings, manifest, pruning_state, name, observed, local_device_id
        )
        manifest = clear_rename_journal(manifest)

        save_state(state)
        save_settings(settings)
        save_manifest(manifest)
        save_pruning_state(pruning_state)

    # ---- 10. kill_ttyd(old) (\u00a72.4) -- outside state_lock, like every other
    # subprocess call. Never touches the tmux session; the browser's WS
    # drops and reconnects, and the next /connect spawns a correctly-hashed
    # ttyd for the new name.
    await kill_ttyd(name)

    _log.info(
        "rename: %r -> %r (bearer_only=%s) migrated=%s",
        name,
        observed,
        _bearer_only_caller(request),
        migrated,
    )

    if verification_failed:
        raise HTTPException(
            status_code=500,
            detail={
                "detail": (
                    f"tmux reported success renaming {name!r} to {new_name!r}, "
                    f"but the observed session name was {observed!r}, not the "
                    "requested name. The migration completed against the "
                    "observed name."
                ),
                "rename_verification_failed": True,
                "observed": observed,
            },
        )

    return {"ok": True, "from": name, "name": observed, "migrated": migrated}


@app.post("/api/heartbeat")
async def heartbeat(payload: HeartbeatPayload) -> dict:
    """Register or update a device heartbeat.

    Acquires state_lock, loads state, calls register_device() with payload
    fields, saves state.

    payload.sync_group (optional) selects the device's sync group:
        None                                  -> leave unchanged
        "global"                              -> OK
        f"device:{payload.device_id}"         -> OK (the device's own group)
        anything else                         -> 400

    Rejecting `device:<someone-else>` today is the tight-then-widen choice:
    allowing it would ship untested surface with no consumer; relaxing this
    later (pairing a deck to a browser's group) is purely additive and
    needs no schema change.

    Group creation happens here and only here (via register_device() ->
    ensure_group()), one site, seeded from global.

    Returns {device_id: str, status: 'ok', sync_group: str}.
    Missing device_id or invalid view_mode returns 422 (handled by Pydantic).
    """
    if payload.sync_group is not None and payload.sync_group not in (
        GLOBAL_GROUP,
        device_group_id(payload.device_id),
    ):
        raise HTTPException(
            status_code=400,
            detail="sync_group must be 'global' or 'device:<own device_id>'",
        )

    async with state_lock:
        state = load_state()
        register_device(
            state,
            device_id=payload.device_id,
            label=payload.label,
            viewing_session=payload.viewing_session,
            view_mode=payload.view_mode,
            last_interaction_at=payload.last_interaction_at,
            sync_group=payload.sync_group,
        )
        resolved_group = state["devices"][payload.device_id]["sync_group"]
        save_state(state)

    return {
        "device_id": payload.device_id,
        "status": "ok",
        "sync_group": resolved_group,
    }


@app.post("/api/sessions/{name}/bell")
async def receive_bell(name: str) -> dict:
    """Called by tmux alert-bell hook when a bell fires in session *name*.

    This is more reliable than polling window_bell_flag because tmux only
    sets that flag when no client is attached -- with an SSH/WezTerm session
    attached, the flag never gets set even though the bell fires.

    400 if *name* fails the session-name allowlist -- same guard every other
    session-name endpoint applies (create/connect/delete/input/followups).
    This endpoint had been missing it: an arbitrary string reached
    `state["sessions"][name] = {}` (a garbage-state write for any caller
    holding the Bearer federation key, unbounded by the safe-charset
    allowlist) and `_advance_followup_queue(name)`, the one autonomous
    writer in the system. The queue advance itself stays safe regardless
    (it independently re-checks `input_allowed_for_session()` and exact
    membership in `get_session_list()` against FRESH settings before
    sending anything -- see that function's docstring), so this was not a
    typing/RCE path -- but an unvalidated name has no business reaching
    persisted state at all, and every sibling endpoint already agrees.
    """
    _require_valid_session_name(name)
    async with state_lock:
        state = load_state()
        if name not in state["sessions"]:
            state["sessions"][name] = {}
        if "bell" not in state["sessions"][name]:
            state["sessions"][name]["bell"] = empty_bell()
        bell = state["sessions"][name]["bell"]
        bell["unseen_count"] = bell.get("unseen_count", 0) + 1
        bell["last_fired_at"] = time.time()
        # bell.source == "hook": this endpoint was called. Honest, not
        # necessarily "tmux's alert-bell hook fired" -- muxplex cannot
        # distinguish the hook from a direct Bearer POST to this same route,
        # and does not claim to (docs/plans/2026-08-07-bell-causality-plan.md
        # \u00a74.1). Never read by needs_attention().
        bell["source"] = "hook"
        save_state(state)

    # The follow-up queue's advance hangs off THIS bell-fired transition --
    # see _advance_followup_queue()'s docstring. Outside state_lock (a
    # queue advance re-acquires it itself and runs a subprocess).
    await _advance_followup_queue(name)
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
    GET /api/instance-info). "Armed" means registration was accepted, not
    that delivery is proven -- see _arm_bell_hook()'s docstring.
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
    if (
        "deviceLabelPlacement" in body
        and body["deviceLabelPlacement"] not in DEVICE_LABEL_PLACEMENTS
    ):
        # Reject, never coerce (docs/plans/2026-08-04-device-label-placement-plan.md 2.5): a value that was
        # never valid cannot have a working client behind it, so this is not
        # a breaking change. Checked before patch_settings() is ever called
        # so no partial write occurs. Fourth member of the discriminator
        # convention alongside backstop / terminal_conflict / unknown_command_id.
        return JSONResponse(
            status_code=400,
            content={
                "detail": "unknown deviceLabelPlacement",
                "unknown_device_label_placement": True,
                "allowed": sorted(DEVICE_LABEL_PLACEMENTS),
            },
        )
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
    except InvalidViewRuleRejected as exc:
        # 400, not 409: the body is malformed, not conflicted -- retrying
        # with fresh settings cannot help, so a client must not treat this
        # like a CAS miss (docs/plans/2026-08-04-auto-views-plan.md §5.5). No write was made.
        return JSONResponse(
            status_code=400,
            content={
                "detail": exc.errors[0] if exc.errors else "Invalid view rule",
                "invalid_view_rule": True,
                "errors": exc.errors,
            },
        )
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


def _tmux_config_snapshot(theme: str, copy_mode: str) -> dict:
    """Shared response shape for GET and PATCH /api/tmux-config.

    ``preview`` is built by ``tmux_config.render_preview`` -- a pure read of
    the templates on disk, not the (possibly stale, possibly never-rendered)
    files under ~/.config/muxplex/tmux.d/ -- so it always reflects *theme*
    and *copy_mode* exactly, whether or not muxplex's tmux config has ever
    been installed.
    """
    return {
        "installed": tmux_config.status().installed,
        "theme": theme,
        "available_themes": tmux_config.available_themes(),
        "copy_mode": copy_mode,
        "preview": tmux_config.render_preview(theme, copy_mode),
    }


@app.get("/api/tmux-config")
async def get_tmux_config() -> dict:
    """Return muxplex's tmux config posture: install status, theme, copy
    mode, available themes, and a preview of the fragments muxplex renders.

    ``preview`` is the concatenated text of ONLY the muxplex-owned fragments
    (base + theme + copy-mode) -- it deliberately excludes the user's own
    ``90-local.conf``, which muxplex never writes after first creation.
    """
    settings = load_settings()
    theme = str(settings.get("tmux_theme") or "brand")
    copy_mode = str(settings.get("tmux_copy_mode") or "desktop")
    return _tmux_config_snapshot(theme, copy_mode)


@app.patch("/api/tmux-config")
async def update_tmux_config(request: Request):
    """Change the tmux theme and/or copy-mode scheme, live.

    CONSTRAINED VOCABULARY ONLY -- this is the entire security model. tmux
    config can carry `run-shell` and `default-command` (arbitrary code
    execution), and this endpoint sits behind the same Bearer auth as every
    other write -- the same credential handed to remote agents. ``theme``
    must be one of ``tmux_config.available_themes()``; ``copy_mode`` must be
    one of ``tmux_config.COPY_MODES``. Anything else is rejected with 400
    naming the valid values. There is no free-text field here and there
    must never be one.

    Either field may be omitted; an omitted field keeps its current
    (persisted) value. On success: the new value(s) are persisted via
    ``patch_settings`` (the same path ``PATCH /api/settings`` uses),
    fragments are re-rendered on disk (``tmux_config.render_fragments``),
    and -- if a tmux server is currently running -- the change is sourced
    into it immediately (``tmux_config.apply_live``) so it takes effect
    without waiting for a restart. No running server is not an error;
    ``apply_live`` tolerates it. This endpoint never kills or restarts the
    tmux server -- live user sessions are never touched, only the
    freshly-rendered fragments are re-sourced into them.
    """
    body = await request.json()
    settings = load_settings()
    theme = str(body.get("theme", settings.get("tmux_theme", "brand")))
    copy_mode = str(body.get("copy_mode", settings.get("tmux_copy_mode", "desktop")))

    available_themes = tmux_config.available_themes()
    if theme not in available_themes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown tmux theme {theme!r}. "
                f"Valid values: {', '.join(available_themes)}"
            ),
        )
    if copy_mode not in tmux_config.COPY_MODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown tmux copy mode {copy_mode!r}. "
                f"Valid values: {', '.join(tmux_config.COPY_MODES)}"
            ),
        )

    patch: dict = {}
    if "theme" in body:
        patch["tmux_theme"] = theme
    if "copy_mode" in body:
        patch["tmux_copy_mode"] = copy_mode
    if patch:
        patch_settings(patch)

    tmux_config.render_fragments(theme, copy_mode)
    try:
        tmux_config.apply_live()
    except tmux_config.TmuxConfigError:
        # A setting change must still succeed even if tmux itself is
        # unavailable (e.g. not on PATH in a test/CI environment) -- the
        # fragments and settings are already written; only the live-reload
        # step is best-effort. "no running server" is NOT this branch --
        # apply_live() already tolerates that and returns normally.
        _log.warning(
            "tmux-config: could not apply live (theme=%r, copy_mode=%r)",
            theme,
            copy_mode,
            exc_info=True,
        )

    return _tmux_config_snapshot(theme, copy_mode)


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


@app.post("/api/focus")
async def raise_focus():
    """Bring THIS host's muxplex PWA window to the foreground.

    **No request body. No query parameters. No target of any kind.** This
    is the load-bearing security property of the whole endpoint: the app
    that gets raised is always exactly ``settings["focus_app"]``, the value
    a LOCAL operator wrote to ``~/.config/muxplex/settings.json`` (see
    ``settings.LOCAL_ONLY_KEYS``) -- never anything a caller supplies. A
    caller who fully controls this request can still only trigger the one
    app the operator already chose. See
    ``docs/plans/2026-08-05-focus-grab-plan.md`` \u00a76 for the full security
    argument, including why this needs no fence of its own beyond
    ``focus_app`` being ``LOCAL_ONLY``.

    Auth is the shared middleware (federation Bearer key / localhost bypass
    / session cookie) -- deliberately NOT a second key, same reasoning
    ``AGENTS.md`` records for ``/input``.

    Checks run in this order, and the order is deliberate (\u00a76.4): the
    platform check is public/non-sensitive and must never leak whether an
    operator configured anything on an unsupported host.

    1. Platform capability (``focus.resolve_focus_capability()``) -- ``501
       focus_unsupported_platform`` when this host has no implementation
       (Linux, Wayland, WSL -- see ``focus.py``'s module docstring).
    2. ``settings["focus_app"]`` is a non-empty string -- ``409
       focus_not_configured`` otherwise (fail-closed: a non-string or empty
       value is treated as unconfigured, never crashes the endpoint).
    3. The mechanism ran and failed -- ``502 focus_failed`` carrying the
       real stderr/exception text (the operator needs this verbatim to fix
       a misconfigured ``focus_app``).
    4. Success -- ``200 {"ok": true, "platform": ..., "app": ...}``.

    On macOS, ``open -a`` LAUNCHES the configured app if it is not already
    running -- this is deliberate, not a bug: "bring the PWA to the
    foreground" means that either way, and probing for a running instance
    first would be a second mechanism for a behavior nobody asked for.
    """
    capability = focus.resolve_focus_capability()
    if not capability.supported:
        return JSONResponse(
            status_code=501,
            content={
                "focus_unsupported_platform": True,
                "platform": capability.platform,
                "detail": capability.reason,
            },
        )

    settings = load_settings()
    app_name = settings.get("focus_app")
    if not isinstance(app_name, str) or not app_name:
        return JSONResponse(
            status_code=409,
            content={
                "focus_not_configured": True,
                "detail": (
                    "focus_app is not set in ~/.config/muxplex/settings.json "
                    "on this host"
                ),
            },
        )

    try:
        await focus.raise_window(app_name)
    except focus.FocusFailedError as exc:
        _log.warning("focus: %r failed: %s", app_name, exc.detail)
        return JSONResponse(
            status_code=502,
            content={"focus_failed": True, "detail": exc.detail},
        )

    _log.info("focus: raised %r", app_name)
    return {"ok": True, "platform": capability.platform, "app": app_name}


@app.get("/api/instance-info")
async def instance_info() -> dict:
    """Return this instance's display name, device identity, and version.

    Public endpoint (no auth required) — used by remote instances to
    discover peer names, device identity, and verify reachability.
    """
    settings = load_settings()
    # Read fresh so the UI reflects key-file changes without requiring a restart.
    fed_key = load_federation_key()
    focus_capability = focus.resolve_focus_capability()
    focus_app_value = settings.get("focus_app")
    focus_configured = isinstance(focus_app_value, str) and bool(focus_app_value)
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
        # Whether the tmux alert-bell hook is currently REGISTERED (see
        # _arm_bell_hook()) -- not proof of delivery, see that function's
        # docstring. False means bells are not firing -- e.g. tmux wasn't up
        # yet at startup, and _run_poll_cycle()'s self-healing retry hasn't
        # succeeded yet. This is how an operator/agent tells bells are
        # unarmed without grepping logs.
        "bell_hook_armed": _bell_hook_armed,
        # The moment THIS process actually came up (reset in lifespan()) --
        # the exact watermark `_run_poll_cycle()`'s "Ensure bell entries
        # exist" step already compares each session's `created_at` against
        # to decide whether to seed a just-created session's bell (see that
        # step's comment). Exposed so a client holding a session's
        # `created_at` (GET /api/sessions, docs/API_SEMANTICS.md) has the
        # other half of that comparison and can reproduce it, rather than
        # being handed only a timestamp with no watermark to compare it to.
        # A process-lifetime value, not a persisted one: it changes on every
        # muxplex restart, same as `bell_hook_armed` above.
        "server_started_at": _server_start_time,
        # Capability advertisement for POST /api/focus, so a client can
        # render an honest disabled state instead of a dead key (same
        # purpose as bell_hook_armed above). The focus_app VALUE is
        # deliberately NOT exposed here -- this endpoint is unauthenticated
        # ("Public endpoint" docstring above), and a local-host app name is
        # not a fact an unauthenticated caller needs; an authenticated
        # client that genuinely wants it can read GET /api/settings. See
        # docs/plans/2026-08-05-focus-grab-plan.md \u00a73.3.
        "focus": {
            "supported": focus_capability.supported,
            "configured": focus_configured,
            "platform": focus_capability.platform,
            "mechanism": focus_capability.mechanism,
        },
    }


def _read_local_ca_cert_bytes() -> bytes | None:
    """Read the local CA certificate's PEM bytes, or None if unavailable.

    Thin wrapper around `tls.get_local_ca_cert_bytes` so every consumer of
    "is a servable local CA present right now" — `/api/ca`, `/ca.crt`, and
    `/setup` — shares exactly one resolution path. See that function's
    docstring for the full list of conditions that produce None (missing
    file, unparseable, not a CA cert, etc).
    """
    return get_local_ca_cert_bytes(get_local_ca_cert_path())


def _ca_cert_bytes_or_404() -> bytes:
    """Read the local CA cert bytes or raise the shared 404.

    Used by both download endpoints (`/api/ca`, `/ca.crt`) so the file
    resolution and error message live in exactly one place; a change to the
    detail message only ever needs to happen here.
    """
    pem_bytes = _read_local_ca_cert_bytes()
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
    return pem_bytes


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
    pem_bytes = _ca_cert_bytes_or_404()
    return Response(
        content=pem_bytes,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="muxplex-ca.crt"'},
    )


@app.get("/ca.crt")
async def get_ca_certificate_for_install() -> Response:
    """Serve the same CA certificate as `/api/ca`, at a plain top-level path
    with the MIME type Android's DownloadManager recognizes as a CA
    certificate (`application/x-x509-ca-cert`), so tapping the download can
    route straight into the system certificate installer instead of landing
    as a generic file the user has to locate and open manually themselves.

    Byte-identical to `GET /api/ca` (both read via `_ca_cert_bytes_or_404()`
    — the single fixed path, no request input involved); this endpoint
    exists only because the *download's advertised type*, not the bytes,
    is what changes browser/OS handling. Exists mainly so `GET /setup`'s
    download link and cert-install docs have one canonical, memorable URL.

    Unauthenticated for the same reason as `/api/ca` (see auth.py's
    `_AUTH_EXEMPT_PATHS` comment) — exempted as its own separate entry
    since the exemption check is an exact path match, not a prefix.
    """
    pem_bytes = _ca_cert_bytes_or_404()
    return Response(
        content=pem_bytes,
        media_type="application/x-x509-ca-cert",
        headers={"Content-Disposition": 'attachment; filename="muxplex-ca.crt"'},
    )


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    """Unauthenticated onboarding page: a download link for the local CA
    certificate plus platform-specific install instructions.

    Exists to close the "chicken and egg" gap in self-serve cert install:
    getting the CA file onto a phone previously meant emailing/AirDropping
    it and hunting through OS Settings menus from memory. This page detects
    the visiting platform from `User-Agent` (Android is the priority
    per the design brief; iOS/macOS/Windows are each covered too) and opens
    that platform's instructions by default — the other three stay
    available, just collapsed.

    Detection note: iPadOS 13+ can present a desktop-class User-Agent
    indistinguishable from real macOS Safari (when the user hasn't
    requested the "Mobile Website"); there is no reliable server-side
    signal to disambiguate that case, so an iPad in that mode will see the
    macOS section opened instead of iOS. Both eventually route through
    "download, then trust the cert" so this is a UX rough edge, not a
    functional break — see setup_page.py's `detect_platform` docstring.

    Never echoes the raw `User-Agent` (or any other request input) into
    the response — `detect_platform` maps it to one of a fixed, closed set
    of labels first, so there is nothing here for a hostile header to
    inject into. See setup_page.py's module docstring.

    When no local CA is configured (`ca_available=False`), the page still
    returns 200 with a plain-language explanation rather than 404ing or
    silently rendering an empty download link — a user landing here from a
    cert-warning link deserves an answer, not a dead end.
    """
    platform = detect_platform(request.headers.get("user-agent", ""))
    ca_available = _read_local_ca_cert_bytes() is not None
    html = render_setup_page(platform=platform, ca_available=ca_available)
    # no-cache, matching index_page()/login_page() above: this page is also
    # reachable through an installed-PWA-adjacent flow and should always
    # reflect the server's current CA-availability state, not a cached one.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# WebSocket proxy — bridges browser to ttyd (eliminates Caddy dependency)
# ---------------------------------------------------------------------------


class WSAuth(NamedTuple):
    """Result of a WebSocket authorization check (see ``_ws_auth_check``).

    ``bearer_only`` is True exactly when the ONLY credential that
    authorized this connection was the federation Bearer key -- no valid
    session cookie was presented. This is the caller classification
    ``terminal_ws_proxy`` uses to decide whether the
    ``input_allowed_sessions`` typing fence applies to this connection (see
    that function's docstring and ``docs/API_SEMANTICS.md``'s "terminal WS
    input fence" section). A valid cookie ALWAYS wins this classification
    when present, even alongside a Bearer header: presenting a valid
    ``muxplex_session`` cookie requires knowing ``_auth_secret`` (verified
    via ``verify_session_cookie``), which a Bearer-key holder cannot forge
    -- so "cookie + Bearer both present" is a genuine browser session that
    also happens to send a Bearer header, never a Bearer-only caller
    impersonating one. Narrowing (gating a Bearer-only caller) is safe;
    widening based on a guess never is -- ``ok=True, bearer_only=False``
    only when the classification is certain (a verified cookie).
    """

    ok: bool
    bearer_only: bool


async def _ws_auth_check(websocket: WebSocket) -> WSAuth:
    """Return whether the WebSocket caller is authorized, and how.

    Closes the WebSocket with code 4001 and returns ``ok=False`` if the
    caller is not authorized. Every caller, including one whose socket peer
    is 127.0.0.1/::1, must present a valid ``muxplex_session`` cookie OR a
    Bearer token matching ``_federation_key``.

    NOTE: this used to unconditionally trust any socket peer at
    127.0.0.1/::1, mirroring the auth middleware's now-removed loopback
    bypass -- see GHSA-7c6r-fvrh-9qp4 and auth.py's ``dispatch`` docstring
    for the measured proof that a re-originated proxy connection presents
    that same peer address for a genuinely remote caller. The terminal
    WebSocket carries live scrollback and keystroke input, so an
    unauthenticated bypass here is at least as dangerous as the HTTP one
    that prompted the fix; it is closed for the identical reason and must
    not be reintroduced. See ``WSAuth``'s docstring for what ``bearer_only``
    means and why cookie always wins the classification when both are
    present.
    """
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
        return WSAuth(ok=False, bearer_only=False)
    return WSAuth(ok=True, bearer_only=bool(bearer_ok and not cookie_ok))


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


async def _accept_then_close(websocket: WebSocket, *, code: int) -> None:
    """Accept the WebSocket handshake, then immediately close it with *code*.

    This is the mechanism that fixes the "4409/4404 never reach any real
    client" incident (see terminal_ws_proxy's docstring and
    docs/API_SEMANTICS.md). Per ASGI/uvicorn semantics, calling
    websocket.close() *before* accept() never produces a real WebSocket
    close frame -- the connection was never upgraded, so it serializes as a
    bare HTTP handshake rejection (`403 Forbidden`, confirmed by raw-socket
    probe). Browsers report that as `close` event code 1006 unconditionally
    and never expose the HTTP status or body to JavaScript at all -- this
    is a WHATWG WebSocket API restriction, not an ASGI/uvicorn quirk, so it
    applies no matter what the server sends pre-accept.

    That restriction is *why* uvicorn's WebSocket-denial-response ASGI
    extension (`websocket.http.response.*`, which lets a rejection carry a
    custom HTTP status/body without accepting) was considered and rejected
    for this specific problem: it changes what a script client that reads
    the raw HTTP response (e.g. Python's `websockets` library, which
    surfaces it via `InvalidStatus.response`) can see, but a real browser's
    WebSocket object still never exposes that response to JS -- the denial
    extension cannot make a numeric code, or a JSON body, reach browser
    code. Completing the handshake (accept) is the ONLY way a real close
    frame -- carrying a real code -- can exist to be reported to a browser
    at all. Confirmed against a live instance in the digital twin (see the
    commit this function was introduced in for the raw-socket evidence).

    Trade-off accepted deliberately: the browser's `open` event now fires
    briefly before `close` follows (the handshake genuinely completes), so
    terminal.js's `open` handler runs its no-op auth/resize sends before
    the close arrives. This does not weaken the refusal: this function
    returns immediately after close() without ever touching ttyd or
    reaching the relay loop below, so no session data is ever exchanged on
    this connection regardless of what the client sends after `open` --
    those messages are simply never read.

    Guarded the same way _client_disconnected()'s documented RuntimeError
    case is guarded: if the client already vanished (TCP dropped) during
    the async state lookup immediately before this call, accept() can
    raise -- caught and swallowed here (logged at debug) rather than
    propagating as ASGI-exception log spam, since the refusal already
    holds trivially when the client is gone (there is nothing left to
    observe the close code anyway).
    """
    try:
        await websocket.accept(subprotocol="tty")
    except Exception as exc:
        _log.debug(
            "WS proxy: accept() failed while rejecting with code=%d "
            "(client likely already disconnected): %s",
            code,
            exc,
        )
        return
    try:
        await websocket.close(code=code)
    except Exception as exc:
        _log.debug("WS proxy: close(code=%d) failed after accept: %s", code, exc)


async def _prepare_ttyd(target: str) -> bool:
    """Ensure *target* session's ttyd is live. Returns True on success.

    Replaces `_prepare_ttyd_for_reconnect()`: `ensure_ttyd()` already proves
    the socket is bound and live before returning, so there is no fixed
    settle sleep here at all (the old 0.8s wait for ttyd to bind its port is
    gone -- see docs/plans/2026-08-02-per-session-ttyd-plan.md §3.6, typical readiness is 20-100ms).
    Does not read/write state itself -- the caller already resolved *target*.
    Never raises: a spawn/capacity failure is logged at warning and reported
    as False so the caller can bail out without relaying.
    """
    try:
        await ensure_ttyd(target)
        return True
    except (TtydSpawnError, TtydCapacityError) as exc:
        _log.warning("WS proxy: failed to prepare ttyd for '%s': %s", target, exc)
        return False


@app.websocket("/terminal/ws")
async def terminal_ws_proxy(
    websocket: WebSocket, device_id: str | None = None, session: str | None = None
) -> None:
    """Proxy WebSocket frames between the browser and *this session's* ttyd.

    `session` (new, optional query param) names the target session directly.
    Absent, it falls back to `state["terminal_session"]` -- today's behavior
    byte-for-byte, and what keeps the federation relay (which never sends
    `session` upstream on its own initiative -- see
    `federation_terminal_ws_proxy`) and any pre-this-change client working
    unchanged. With per-session ttyd there is no single "the" ttyd anymore,
    so a WS that names no session has nothing implicit left to attach to
    except this fallback.

    Checks that this session's ttyd is alive BEFORE accepting the browser
    WebSocket. If it is not (first attach, or it died and needs a respawn),
    auto-spawns it via `_prepare_ttyd()`. Only after it is confirmed reachable
    does the function call websocket.accept() -- so the browser's 'open'
    event only fires once a real relay is possible. This prevents the
    reconnect-counter bounce bug where the proxy accepted immediately
    (resetting _reconnectAttempts to 0) and then closed as soon as it
    couldn't reach the dead ttyd.

    The ttyd auto-spawn wait is real wall-clock time during which the
    browser can disconnect (tab closed, navigation, network drop, PWA
    backgrounding). Calling websocket.accept() after that happens raises
    RuntimeError -- see _client_disconnected()'s docstring for the exact
    mechanism -- so that wait is raced against a disconnect watcher and
    accept() is skipped entirely if the client is already gone.

    device_id (optional query param) is the §0 hazard's loud backstop,
    REDEFINED and NARROWER now that there is no single contended terminal to
    arbitrate (docs/plans/2026-08-02-per-session-ttyd-plan.md §7.2): it now means "you asked to
    attach to a session your own group has not selected," a per-request
    consistency check rather than a resource claim.
        - No device_id -> today's path exactly, no new behavior.
        - Unknown device_id -> close(4404).
        - Missing/invalid/unknown *target* session -> close(4404) (widened
          from the old single-ttyd version, which could only ever see one
          possible session).
        - Otherwise: resolve the caller's group; if that group's
          active_session is None or does not equal *target*, close(4409)
          rather than relay -- this device must never be shown, or type
          into, a session it did not select.
    There is no race with the normal client path: openSession() awaits
    /connect (which sets terminal_session, and now also directly names the
    session in the WS URL) before mounting the terminal.

    IMPORTANT -- what a real client actually sees on the wire, and the fix
    that changed it: both rejection branches below call _accept_then_close()
    instead of closing before accept(). Per ASGI/uvicorn semantics, closing
    BEFORE accept() never produces a real WebSocket close frame (the
    connection was never upgraded), so it serializes as a bare HTTP
    handshake rejection instead -- confirmed by raw-socket probe against a
    live instance: `HTTP/1.1 403 Forbidden`, empty body, no numeric code
    visible anywhere on the wire. A browser's WS `close` event reports
    `1006` for ANY failed opening handshake, and the WHATWG WebSocket spec
    gives JavaScript no way to read the underlying HTTP status or body
    either. `_accept_then_close()` completes the handshake first, so the
    close frame -- and its code -- genuinely reaches the wire: a real
    browser's `close` event reports `event.code === 4404`/`4409` instead of
    `1006`. See _accept_then_close()'s docstring for the full mechanism and
    docs/API_SEMANTICS.md for the original incident writeup.

    This does NOT touch the pre-accept ttyd-auto-spawn disconnect race
    documented above and in _client_disconnected()'s docstring: that guard
    only runs in the branch below where device_id is absent/matching and the
    function proceeds toward a real relay. The rejection branches return
    immediately after _accept_then_close(), well before reaching the
    ttyd-liveness check, so they never interact with that wait at all.

    THIRD DOOR TO THE input_allowed_sessions FENCE (closed here) -- see
    AGENTS.md's "Terminal input" section and docs/API_SEMANTICS.md's
    "terminal WS input fence" entry for the full incident writeup. Before
    this fix, this endpoint applied NEITHER `settings.input_enabled` NOR
    `settings.input_allowed_sessions` to the browser<->ttyd byte relay --
    only the group/device_id consistency checks above, which are about NOT
    MISDIRECTING a device to a session it didn't select, not about WHETHER
    a caller may type into a given session at all. A Bearer-key holder (the
    same credential AGENTS.md documents as handed to headless AI agents for
    API access) could therefore type into ANY live session by naming it via
    `?session=`, bypassing both settings keys entirely -- the exact
    RCE-by-design capability `POST /api/sessions/{name}/input` exists to
    fence, reached through a completely different door.

    THE FIX GATES TYPING ONLY, NEVER VIEWING, and ONLY for `bearer_only`
    callers (see `WSAuth`'s docstring: cookie -- a real browser session --
    and localhost are unaffected, exactly as before this fix). This
    asymmetry is deliberate, not partial: a Bearer holder can already read
    every session's live pane content via `GET /api/sessions` (`snapshot`
    field, gated only by the shared auth middleware, same as this
    endpoint) -- gating VIEWING here would add no confidentiality that
    doesn't already leak elsewhere, while gating it WOULD break
    `federation_terminal_ws_proxy`'s legitimate peer-to-peer relay, which
    dials this endpoint with a Bearer header unconditionally (server-to-
    server, never a cookie) whenever a human uses the aggregated PWA to
    watch a REMOTE host's session. See `client_to_ttyd()` below for the
    exact mechanism: it inspects the ttyd wire protocol's leading command
    byte (0x30 = keystroke input; see frontend/terminal.js) and drops ONLY
    that command for a fenced `bearer_only` connection -- the 0x31 resize
    command, the initial text AuthToken handshake, and 100% of the
    ttyd->client output direction all flow unaffected, so a denied
    connection keeps working as a live, resizable VIEWER, it just cannot
    type. This means federation typing (not just viewing) is ALSO now
    subject to the remote host's OWN `input_enabled` / `input_allowed_sessions`
    -- an accepted, deliberate narrowing: the wire is bit-identical between
    "my own federation peer relaying a human's keystrokes" and "a bearer
    holder typing directly," so the two cannot be distinguished, and per
    the fail-safe rule this file follows, an undistinguishable case is
    denied by default. Restoring federation typing to a specific session is
    the same local, settings.json-edit opt-in every other Bearer-only
    typing path already requires -- never a new capability, just closing
    the door that let it be skipped.
    """
    # Auth check before accepting — BaseHTTPMiddleware doesn't cover WebSocket scope
    auth = await _ws_auth_check(websocket)
    if not auth.ok:
        return

    async with state_lock:
        state = load_state()

    group: str | None = None
    if device_id is not None:
        try:
            group = resolve_group(state, device_id)
        except KeyError:
            # accept()-then-close() so this code reaches the wire as a real
            # WS close frame -- see _accept_then_close()'s docstring.
            await _accept_then_close(websocket, code=4404)
            return

    target = session if session is not None else state["terminal_session"]
    if (
        target is None
        or not is_valid_session_name(target)
        or target not in get_session_list()
    ):
        # Fail-closed exact membership -- same pattern as connect/delete/input.
        # An empty/unavailable cache rejects everything.
        await _accept_then_close(websocket, code=4404)
        return

    if group is not None:
        wanted = read_group_state(state, group)["active_session"]
        if wanted is None or wanted != target:
            # Same wire-reachability fix as above -- see _accept_then_close().
            await _accept_then_close(websocket, code=4409)
            return

    # Register this connection's task so lifespan shutdown can cancel it.
    _task = asyncio.current_task()
    if _task is not None:
        _ws_proxy_tasks.add(_task)

    # Ensure this session's ttyd is reachable BEFORE accepting the browser WS.
    # Auto-spawn so the browser's 'open' event only fires when a real relay
    # is possible — eliminates the 0→1→0→1 counter bounce.
    if not socket_is_live(socket_path_for(target)):
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

        prep_task: asyncio.Task = asyncio.create_task(_prepare_ttyd(target))
        disconnect_task: asyncio.Task = asyncio.create_task(
            _client_disconnected(websocket)
        )
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

        if prep_task in done and (
            prep_task.exception() is not None or not prep_task.result()
        ):
            # Spawn failure/capacity ceiling: log + close, no relay attempted
            # -- same shape as an unreachable ttyd always produced.
            if _task is not None:
                _ws_proxy_tasks.discard(_task)
            return

    await websocket.accept(subprotocol="tty")

    # Terminal WS input fence -- see this function's docstring ("THIRD DOOR
    # TO THE input_allowed_sessions FENCE"). Evaluated once per connection
    # (these are LOCAL_ONLY_KEYS -- an operator changing them mid-connection
    # is a rare edit, not a per-message hot path worth re-reading settings
    # for). Irrelevant for cookie/localhost callers -- they were never
    # gated by this fence and stay exactly as before.
    input_gate_open = True
    if auth.bearer_only:
        input_gate_open = input_allowed_for_session(target, load_settings())

    acquire_relay(target)
    try:
        async with unix_connect(
            str(socket_path_for(target)),
            uri="ws://localhost/ws",
            subprotocols=[Subprotocol("tty")],
        ) as ttyd_ws:

            async def client_to_ttyd() -> None:
                # `warned` is scoped to one connection's lifetime -- one log
                # line per denied connection, not per dropped keystroke.
                warned = False
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg["type"] == "websocket.disconnect":
                            return
                        raw_bytes = msg.get("bytes")
                        if (
                            not input_gate_open
                            and raw_bytes
                            # ttyd wire protocol: leading byte 0x30 ('0') is
                            # keystroke/input data (see frontend/terminal.js's
                            # _encodePayload(0x30, ...) call in onData()).
                            # Every other leading byte -- 0x31 resize, any
                            # future control command -- is NOT typing and is
                            # let through unconditionally below, same as the
                            # text-frame AuthToken handshake: this gate blocks
                            # TYPING, never VIEWING.
                            and raw_bytes[0:1] == b"0"
                        ):
                            if not warned:
                                _log.warning(
                                    "WS proxy: dropped keystroke input for "
                                    "bearer-only caller on session %r -- not "
                                    "enabled (settings.input_enabled / "
                                    "input_allowed_sessions)",
                                    target,
                                )
                                warned = True
                            continue
                        if raw_bytes:
                            await ttyd_ws.send(raw_bytes)
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
        release_relay(target)
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
async def federation_terminal_ws_proxy(
    websocket: WebSocket, device_id: str, session: str | None = None
) -> None:
    """Proxy WebSocket frames between the browser and a remote muxplex ttyd.

    *device_id* is the device_id string of the remote instance in
    settings.  Authenticates to the remote instance using the configured
    ``key`` field via a Bearer header.

    This relay dials the remote muxplex's own authenticated ``/terminal/ws``
    endpoint over the network (ws(s)://<remote>) -- it NEVER touches a ttyd
    socket directly. UNIX sockets are process-local by construction, so the
    per-session-ttyd transport change cannot cross an instance boundary even
    in principle, and does not need any socket-handling code here. The only
    change this route needed is `session` (new, optional query param):
    forwarded upstream when supplied so the remote relays the *named*
    session rather than whatever its ``terminal_session`` fallback happens
    to hold. Absent, the upstream call omits it and the remote falls back
    identically to its own no-`session` behavior -- purely additive in both
    directions (docs/plans/2026-08-02-per-session-ttyd-plan.md §11).

    Auth check uses the same cookie + bearer pattern as terminal_ws_proxy.
    Closes with code 4004 if device_id does not match any remote.

    Relay uses the same asyncio.wait(FIRST_COMPLETED) + cancel-the-other-
    direction pattern as terminal_ws_proxy (see AGENTS.md's "clean shutdown
    ordering" and that function's own comment). gather() (wait for BOTH
    directions) hangs forever once one side closes: if the browser
    disconnects, remote_to_client keeps streaming from the still-live
    remote ttyd, the handler never returns, and uvicorn's "waiting for
    connections to close" phase runs until systemd SIGKILLs the process at
    the stop timeout -- with N concurrent federation relays open, that is a
    reliable shutdown hang, not an edge case.

    Also registers this connection's task in `_ws_proxy_tasks` -- the same
    registry terminal_ws_proxy uses -- so lifespan shutdown (main.py's
    `lifespan()`) can cancel this relay directly instead of relying on it to
    notice the browser side's disconnect on its own. Before this fix, an
    open federation relay was invisible to shutdown entirely: the registry
    only ever held local-proxy tasks, so `n_relays = len(_ws_proxy_tasks)`
    at shutdown undercounted, and a federation relay blocked on the (still
    live) remote ttyd was never cancelled -- the same hang the gather() fix
    above addresses, from the other side.
    """
    # Auth check before accepting — same pattern as terminal_ws_proxy. This
    # relay's own `bearer_only` classification is irrelevant here: the input
    # fence this function's docstring describes is enforced by the REMOTE
    # host's terminal_ws_proxy (which sees this relay's outbound connection
    # as Bearer-only, always -- see auth_headers below), not by this hop.
    auth = await _ws_auth_check(websocket)
    if not auth.ok:
        return

    # Look up remote instance by device_id
    remote = _lookup_remote_by_device_id(device_id)
    if remote is None:
        await websocket.close(code=4004)
        return
    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")

    # Convert http(s) URL to ws(s), forwarding ?session= when supplied.
    session_qs = f"?session={quote(session, safe='')}" if session is not None else ""
    if remote_url.startswith("https://"):
        ws_url = "wss://" + remote_url[8:] + "/terminal/ws" + session_qs
    elif remote_url.startswith("http://"):
        ws_url = "ws://" + remote_url[7:] + "/terminal/ws" + session_qs
    else:
        ws_url = (
            remote_url + "/terminal/ws" + session_qs
        )  # assume already ws:// or wss://

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

    # Register this connection's task so lifespan shutdown can cancel it —
    # same registry, same rationale as terminal_ws_proxy (see this
    # function's docstring and main.py's lifespan() shutdown section).
    _task = asyncio.current_task()
    if _task is not None:
        _ws_proxy_tasks.add(_task)

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
                        if msg["type"] == "websocket.disconnect":
                            return
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

            # Relay until EITHER side closes, then cancel the other — see
            # this function's docstring (gather() would hang shutdown).
            relay_tasks = [
                asyncio.create_task(client_to_remote()),
                asyncio.create_task(remote_to_client()),
            ]
            _done, pending = await asyncio.wait(
                relay_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
    except Exception as exc:
        _log.debug("federation ws proxy closed: %s", exc)
    finally:
        if _task is not None:
            _ws_proxy_tasks.discard(_task)
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
    cwds = get_session_cwds()
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
                "followups": followups.summary(state, name),
                # Same cwd observation GET /api/sessions carries (see that
                # route's docstring) -- included so a peer's remote entries
                # (spread with **s below) are never the richer half of a
                # merged fleet view; local entries carry the identical field.
                "cwd": cwds.get(name),
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
        return annotate_view_membership(local_sessions, settings)

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

    # Annotate the FINAL MERGED list, not the per-remote `tagged` lists
    # cached above in `_federation_cache` -- this is what keeps the cache
    # un-annotated (docs/plans/2026-08-04-auto-views-plan.md §5.3). In-place annotation of a
    # cached object would bake a point-in-time membership answer into the
    # cache and serve it after the settings that produced it had changed.
    # Local views apply to remote sessions too, and that is correct:
    # `views` is a synced setting, so view definitions are fleet-global by
    # design; each device annotates the sessions it is showing with the
    # view definitions it holds.
    return annotate_view_membership(all_sessions, settings)


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
    body ``{name: ...}`` (plus ``command_id`` when the caller supplied one),
    and returns the remote's JSON response.

    ``command_id`` is forwarded conditionally, not unconditionally: the id
    namespace belongs to the REMOTE, not to us -- a local id like
    "amplifier" may mean something else, or nothing, on the peer, where an
    unresolvable id produces a 400 there, surfaced as a 502 by this proxy's
    raise_for_status(). Omitting the field entirely when unset keeps the
    proxied request byte-identical to today's for the overwhelmingly common
    case. The frontend must not send command_id when a remote device is
    selected (a local id is meaningless, and likely a 400, on the peer).

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
    body = (
        {"name": payload.name}
        if payload.command_id is None
        else {"name": payload.name, "command_id": payload.command_id}
    )
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"} if remote_key else {},
            json=body,
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


# ---------------------------------------------------------------------------
# amplifier-agent chat-panel proxy (POC)
# ---------------------------------------------------------------------------
#
# Registered before the static-file mount below (route order matters: FastAPI
# matches routes in registration order, and the mount below is "/", which
# would otherwise shadow nothing here since this is an explicit path -- but
# every API route in this file is declared before the mount for the same
# reason). Not in auth.py's _AUTH_EXEMPT_PATHS, so a non-localhost caller must
# already carry a valid muxplex_session cookie to reach it -- same gate as
# every other /api/ route.


@app.post("/api/agent/chat/completions")
async def agent_chat_completions_proxy(request: Request) -> Response:
    """Same-origin proxy to the amplifier-agent HTTP chat-completions sidecar.

    The browser talks only to muxplex's own origin, authenticated by the
    caller's normal muxplex_session cookie (AuthMiddleware already gates this
    route, same as every other /api/ route -- it is not in
    auth.py's _AUTH_EXEMPT_PATHS). muxplex then forwards the request
    server-side to the sidecar's OpenAI-compatible endpoint, attaching the
    sidecar's own bearer secret (_AGENT_PROXY_TOKEN) -- a credential scoped
    only to "muxplex may call the agent's chat API", never the reverse. The
    agent process itself holds no muxplex credential of any kind (no cookie,
    no muxplex API key, no federation key) and is further network-isolated
    (see the deployment notes: it runs as its own unprivileged user with an
    iptables OUTPUT rule dropping that user's traffic to muxplex's port) so
    that this is structurally true, not merely true by convention.

    This is a raw byte relay: the request body is forwarded unmodified and
    the upstream response body (SSE chunks, per amplifier-agent's
    docs/spec/http-face.md) is streamed back unmodified. muxplex does not
    parse or transform the wire format here -- the browser is the OpenAI
    chat-completions client, not this route.
    """
    if not _AGENT_PROXY_TOKEN:
        return JSONResponse(
            {
                "error": {
                    "message": "Agent proxy is not configured on this server "
                    "(AMPLIFIER_AGENT_BEARER_TOKEN unset)",
                    "type": "server_error",
                }
            },
            status_code=503,
        )

    body = await request.body()
    client_session_id = request.headers.get("x-client-session-id", "")

    # Guarded inline (`{...} if key else {}`), matching every other federation
    # Bearer-header call site in this module (see e.g. line ~261, ~4622,
    # ~4839): even though the early `if not _AGENT_PROXY_TOKEN: return` above
    # already makes an empty token unreachable here today, that guarantee is
    # only visible to a reader who traces the control flow up several lines.
    # Writing the guard at the point of use keeps the invariant this route
    # depends on locally verifiable -- and self-enforcing if a future
    # refactor ever moves or removes the early return -- rather than relying
    # solely on a guard elsewhere in the function.
    upstream_headers = {"Content-Type": "application/json"}
    upstream_headers.update(
        {"Authorization": f"Bearer {_AGENT_PROXY_TOKEN}"} if _AGENT_PROXY_TOKEN else {}
    )
    if client_session_id:
        upstream_headers["X-Client-Session-Id"] = client_session_id

    client: httpx.AsyncClient = request.app.state.agent_client
    upstream_url = f"{_AGENT_PROXY_URL.rstrip('/')}/v1/chat/completions"

    async def relay():
        try:
            async with client.stream(
                "POST", upstream_url, content=body, headers=upstream_headers
            ) as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as exc:
            # Loud, visible, in-stream failure -- never a silent empty
            # response. Mirrors the agent's own mid-stream error convention
            # (see http-face.md's "[amplifier-agent error: ...]" shape) so a
            # broken sidecar looks the same to the panel as a broken turn.
            _log.warning("agent proxy: upstream request failed: %s", exc)
            err = {
                "error": {
                    "message": f"agent sidecar unreachable at {_AGENT_PROXY_URL}: {exc}",
                    "type": "server_error",
                }
            }
            yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()

    return StreamingResponse(relay(), media_type="text/event-stream")


app.mount(
    "/", _NoCacheStaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend"
)

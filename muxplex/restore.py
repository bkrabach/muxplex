"""
Restore execution -- SESSION_PERSISTENCE_DESIGN.md's "v1b -- explicit restore"
milestone, built on top of the "v1a -- record only" manifest (manifest.py).

This module is deliberately a plain, synchronous-friendly orchestration layer
that a CLI process can drive with `asyncio.run()` -- it has NO dependency on
FastAPI, the running muxplex server, or any HTTP round trip. That is a
considered departure from the design doc's original sketch (which routed
restore through `POST /api/sessions/restore` so the server would be the
manifest's single writer). The reason: the design's own biggest unresolved
risk (section 11.1) was whether the session-creation command survives many
sequential invocations "from the service context" -- meaning inside the
long-running `muxplex.service` systemd unit, which has no controlling TTY, a
different (service-manager) environment, and is exactly the process that
auto-spawns tmux servers into its own cgroup (the root cause of the 2026-07-29
incident). Routing restore through that same process would inherit both
hazards for no offsetting benefit. Running restore as its own short-lived CLI
process instead executes the creation command in the SAME kind of context
that has already been proven safe: an ordinary foreground process invoked
from a user's own shell (this is exactly how the 44-session manual recovery
on 2026-07-29 was actually performed: `env -u TMUX setsid amplifier-workspace
~/dev/<name> </dev/null`, run 44 times in a row from an interactive shell --
not from inside muxplex.service).

The one thing this design choice must still account for: the manifest file
CAN be concurrently written by a *running* muxplex service's poll loop while
`muxplex restore` is executing (the service does not need to be stopped to
run a restore -- and per the user's explicit instruction, it must never be
stopped/restarted by this feature). See `_persist_restored()` below for how
the write-side race is kept to a near-zero window without requiring a
cross-process lock: every write re-reads the manifest immediately beforehand
and touches ONLY the `pending_restore` field, never `sessions`/`epoch` (which
belong to the poll loop). Losing a poll cycle's `sessions` update to this
race is self-healing (the next ~2s poll cycle simply re-observes the still-
live session); losing track of a name we just restored is not, which is why
that field gets the careful treatment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from muxplex.manifest import (
    RESTORE_MAX_AGE_SECONDS,
    compute_restore_plan,
    get_created_with,
    load_manifest,
    mark_restored,
    save_manifest,
)
from muxplex.sessions import enumerate_sessions, run_tmux, spawn_session_command
from muxplex.settings import find_session_command

Status = Literal["ok", "fail", "warn"]


@dataclass
class SessionResult:
    """Outcome of restoring (or attempting to restore) one session."""

    name: str
    status: Status
    detail: str = ""
    windows: int | None = None


@dataclass
class RestorePlan:
    """What a restore run would do, computed at plan time (dry-run or
    pre-confirmation display) -- see RestoreReport for what actually
    happened after execution.
    """

    detected_at: float | None
    lost_server_pid: int | str
    total_pending: int
    names: list[str]  # already-live names excluded; this IS the plan
    stale: bool  # True if detected_at is older than RESTORE_MAX_AGE_SECONDS


@dataclass
class RestoreReport:
    """Outcome of an executed (non-dry-run) restore."""

    results: list[SessionResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def warn_count(self) -> int:
        return sum(1 for r in self.results if r.status == "warn")

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if r.status == "fail")

    @property
    def any_failed(self) -> bool:
        return self.fail_count > 0


async def load_plan(
    *, force: bool = False, now: float | None = None
) -> RestorePlan | None:
    """Compute the current restore plan against LIVE tmux state.

    Returns None if there is nothing pending at all (no cold start ever
    recorded, or a prior restore already cleared it). Recomputes against
    `enumerate_sessions()` right now -- not a stale snapshot -- so a name
    that came back on its own (or was already restored earlier) is simply
    absent from `.names`, which is what makes both the dry-run view and the
    real execution idempotent by construction.

    `stale` reports whether `detected_at` is older than
    RESTORE_MAX_AGE_SECONDS; the caller (cli.py) decides what to do with
    that (refuse unless `force=True`). This function never refuses on its
    own -- it is a pure read/compute, not a gate.
    """
    manifest = load_manifest()
    pending = manifest.get("pending_restore")
    if not pending:
        return None

    if now is None:
        now = time.time()
    detected_at = pending.get("detected_at")
    stale = bool(detected_at) and (now - detected_at) > RESTORE_MAX_AGE_SECONDS

    live_names = await enumerate_sessions()
    plan_names = compute_restore_plan(manifest, live_names)

    lost_epoch = pending.get("lost_epoch") or {}
    return RestorePlan(
        detected_at=detected_at,
        lost_server_pid=lost_epoch.get("server_pid", "unknown"),
        total_pending=len(pending.get("sessions") or {}),
        names=plan_names,
        stale=stale,
    )


async def _probe_windows(name: str) -> int | None:
    """Best-effort window count for *name*, for the divergence report only.

    Never raises: a probe failure just means the report omits window info
    for this session -- it must never affect PASS/FAIL classification.
    """
    try:
        output = await run_tmux("list-windows", "-t", name, "-F", "#{window_index}")
    except (RuntimeError, FileNotFoundError):
        return None
    lines = [line for line in output.splitlines() if line.strip()]
    return len(lines) or None


async def _persist_restored(restored_names: set[str]) -> None:
    """Clear *restored_names* from `pending_restore`, re-reading the manifest
    immediately beforehand to minimize the window against a concurrently
    running poll loop (see module docstring). Only `pending_restore` is
    touched; `sessions`/`epoch` are carried through UNCHANGED from whatever
    is on disk at write time, so a poll-loop write racing this one is never
    clobbered outside that one field.
    """
    if not restored_names:
        return
    manifest = load_manifest()
    updated = mark_restored(manifest, restored_names)
    save_manifest(updated)


async def forget() -> int:
    """Clear `pending_restore` entirely without creating anything.

    Returns the number of sessions that were pending (for the caller to
    report). A no-op (returns 0) if nothing was pending.
    """
    manifest = load_manifest()
    pending = manifest.get("pending_restore")
    if not pending:
        return 0
    names = set((pending.get("sessions") or {}).keys())
    await _persist_restored(names)
    return len(names)


async def execute_restore(names: list[str]) -> RestoreReport:
    """Actually create each session in *names*, sequentially, verifying each
    one as it goes. This is the only function in this module that creates
    or kills anything.

    Sequential and NOT concurrent, per SESSION_PERSISTENCE_DESIGN.md section
    7.3: N session-creation commands in parallel is a thundering herd on a
    machine that may have just booted, and sequential failure is
    attributable to a specific session (a progress line per name) rather
    than an opaque batch outcome.

    One failure does not abort the rest -- every name in *names* is
    attempted regardless of earlier failures, and the manifest is updated
    to reflect exactly what succeeded (failed names remain in
    `pending_restore` for a future retry; this is deliberate, not an
    oversight -- see mark_restored()'s docstring).
    """
    report = RestoreReport()
    restored: set[str] = set()

    for name in names:
        # Load the manifest INSIDE the loop (not once before it): restore is
        # explicitly designed to run while the poll loop is live, and a
        # per-iteration read is consistent with _persist_restored()'s
        # read-right-before-write discipline.
        recorded = get_created_with(load_manifest(), name)
        if recorded is not None and find_session_command(recorded) is None:
            # The pair this session was created with no longer resolves
            # (deleted or renamed in settings). Not a substitution, not a
            # silent skip: a FAIL. Recreating with the wrong pair (or the
            # default) would silently reintroduce the exact failure
            # AGENTS.md warns about -- "a bare tmux session ... looks
            # restored and isn't."
            report.results.append(
                SessionResult(
                    name=name,
                    status="fail",
                    detail=(
                        f"recorded command {recorded!r} is no longer configured; "
                        "restore it in ~/.config/muxplex/settings.json and re-run"
                    ),
                )
            )
            continue

        ok, error = await spawn_session_command(name, command_id=recorded)
        if not ok:
            report.results.append(
                SessionResult(name=name, status="fail", detail=error or "unknown error")
            )
            continue

        # Verify against LIVE tmux state -- never trust spawn_session_command's
        # own internal "exists" check as the final word; re-probe here so the
        # report reflects reality at the moment of verification, not creation.
        live_now = await enumerate_sessions()
        if name not in live_now:
            report.results.append(
                SessionResult(name=name, status="fail", detail="session did not appear")
            )
            continue

        windows = await _probe_windows(name)
        restored.add(name)
        if windows is not None and windows <= 1:
            report.results.append(
                SessionResult(
                    name=name,
                    status="warn",
                    detail=f"windows {windows} (expected multiple -- template "
                    "may have failed partway; this is a fresh, bare shell)",
                    windows=windows,
                )
            )
        else:
            report.results.append(
                SessionResult(name=name, status="ok", windows=windows)
            )

    # Only successfully-verified names are cleared from pending_restore.
    # Failed names stay pending so a later `muxplex restore` retries them.
    await _persist_restored(restored)
    return report

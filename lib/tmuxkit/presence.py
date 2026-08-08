"""The session-presence rule -- pure functions, no I/O.

Moved verbatim from ``manifest.py`` (tmux-lib extraction stage S1, plan
§7.1 -- docs/plans/2026-08-08-tmux-lib-extraction-plan.md): ``_same_epoch``,
``update_manifest``, ``compute_restore_plan``, ``mark_restored``, and the
schema constant they share. The purity is not incidental -- it is what makes
this the single safest thing in the codebase to move, verified by the
differential harness against recorded real inputs (plan §8.2).

Manifest I/O (``load_manifest``/``save_manifest``) deliberately does NOT
move at S1: it defaults its path to muxplex's ``STATE_DIR`` (an app-side
import). §13.3's injected-path shape lands with the packaging stages.

The unknown-key round-trip contract (extraction stage S4, plan §13.3): the
library owns the CORE top-level keys -- ``schema``, ``epoch``, ``sessions``,
``pending_restore`` (plus, for now, ``created_with``, whose reap rules still
live inside ``update_manifest()`` below) -- and an app writes its own
top-level keys BESIDE them in its own single-writer file. For that to be
safe, every function in this module that returns a rebuilt manifest carries
unknown top-level keys through **verbatim**. Before S4 this was false:
``update_manifest()`` rebuilt the top-level dict from a closed key set, so
an app-owned key survived only by the call-order accident that muxplex's
poll cycle reads and clears ``rename_in_flight`` before calling it. That
accident is now a contract, pinned by the re-recorded differential-harness
case and by test_manifest.py's S4 contract tests.

The presence rule, in one sentence (see manifest.py's module docstring for
the full incident history): a positive record, removed by exactly one thing
-- observed individual death against a live, identity-matched server --
never by a TTL or a sweep.
"""

from __future__ import annotations

import time
from typing import Any

MANIFEST_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# Epoch comparison
# ---------------------------------------------------------------------------


def _same_epoch(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """True iff *a* and *b* identify the same running tmux server.

    Compares socket_path, inode, and server_pid -- see
    sessions.probe_tmux_epoch()'s docstring for why all three matter. A
    missing field on either side (e.g. a hand-edited manifest) means "not
    the same" rather than raising.
    """
    if a is None or b is None:
        return False
    return (
        a.get("socket_path") == b.get("socket_path")
        and a.get("inode") == b.get("inode")
        and a.get("server_pid") == b.get("server_pid")
    )


# ---------------------------------------------------------------------------
# The update rule -- pure function, no I/O
# ---------------------------------------------------------------------------


def update_manifest(
    manifest: dict[str, Any],
    epoch_now: dict[str, Any] | None,
    live_names: list[str],
    *,
    now: float | None = None,
    cwds: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Apply one poll cycle's observation to *manifest*.

    *cwds* (optional, defaults to ``None`` -- byte-identical to pre-fix
    behavior for any caller that doesn't pass it, including every existing
    test in this suite) is ``sessions.get_session_cwds()``'s output: each
    live session's observed working directory this cycle. When provided,
    each live session's ``cwd`` field is set/updated in place -- exactly
    like ``last_seen_at`` below, this NEVER sets ``changed=True`` on its own
    (a session merely continuing to report the same, or a different, cwd is
    not a structural change worth an extra write). A name absent from
    *cwds* simply keeps whatever ``cwd`` (if any) it already had -- tmux
    occasionally omits `#{pane_current_path}` transiently, and losing a
    known-good value to a single blank read would defeat the whole point of
    recording it. See the module docstring's "Restore fidelity" section and
    restore.py for what this field is used for.

    Pure and side-effect-free: the caller decides whether/when to persist
    the result (see main.py's poll-cycle call site, which only calls
    save_manifest() when *changed* is True -- this is what keeps write
    volume near-zero in steady state despite running every ~2s poll cycle,
    per SESSION_PERSISTENCE_DESIGN.md section 10's "< 1 write/minute"
    target).

    Implements the discrimination rule from this module's docstring / from
    SESSION_PERSISTENCE_DESIGN.md section 5.2:

      epoch_now is None       -> tmux is unavailable this cycle. Knowledge
                                  is not refuted, just absent right now.
                                  Return *manifest* completely unchanged.
      manifest.epoch is None  -> first run ever, or first run after an
                                  upgrade from a pre-manifest version.
                                  Adopt this epoch and record whatever is
                                  currently live. Nothing can be "lost"
                                  relative to an epoch we have never seen,
                                  so pending_restore is never populated
                                  here.
      epoch_now == old epoch  -> SAME SERVER: presence is authoritative.
                                  Newly-seen live sessions are recorded.
                                  Any session that WAS recorded and is now
                                  gone was killed against a live, identity-
                                  matched server -- a deliberate kill (via
                                  muxplex's own delete endpoint, `tmux
                                  kill-session` by hand, or the process
                                  simply exiting). It is tombstoned
                                  (removed from the manifest) so it can
                                  never later appear in pending_restore.
      epoch_now != old epoch  -> DIFFERENT SERVER: cold start. Sessions
                                  recorded under the OLD epoch that are not
                                  alive under the new one become
                                  pending_restore -- a frozen snapshot, not
                                  a live view, so the same-server branch on
                                  a LATER cycle does not turn around and
                                  tombstone the very entries just queued
                                  for restore.

    Returns:
        (new_manifest, changed) -- *changed* is True only when something
        was structurally added, removed, or the epoch itself changed. A
        session's mere continued presence across a cycle (the common case)
        does not set *changed*, so callers can skip the write entirely on
        a quiet cycle.
    """
    if now is None:
        now = time.time()

    if epoch_now is None:
        # No tmux server at all right now (e.g. the brief startup window
        # before tmux comes up). Our knowledge is unavailable, not
        # refuted -- do nothing. Never tombstone, never declare a cold
        # start on absence alone; the ARRIVAL of a new server is the event.
        return manifest, False

    epoch_rec = manifest.get("epoch")
    sessions: dict[str, Any] = dict(manifest.get("sessions", {}))
    live_set = set(live_names)

    created_with: dict[str, str] = dict(manifest.get("created_with", {}))

    if epoch_rec is None:
        # First run ever, or first run after upgrade: adopt. Nothing is
        # "lost" relative to an epoch we've never recorded.
        for name in live_names:
            entry: dict[str, Any] = {"first_seen_at": now, "last_seen_at": now}
            if cwds and name in cwds:
                entry["cwd"] = cwds[name]
            sessions[name] = entry
        new_manifest = {
            # S4 (plan §13.3): unknown top-level keys round-trip verbatim.
            # The spread carries every app-owned key; the explicit entries
            # below overwrite exactly the keys this function owns, with
            # values computed exactly as before the contract change.
            **manifest,
            "schema": manifest.get("schema", MANIFEST_SCHEMA_VERSION),
            "epoch": {**epoch_now, "observed_at": now},
            "sessions": sessions,
            "pending_restore": manifest.get("pending_restore"),
            "created_with": created_with,
        }
        return new_manifest, True

    if _same_epoch(epoch_now, epoch_rec):
        # ---- SAME SERVER: presence is authoritative ----
        changed = False
        for name in live_names:
            if name in sessions:
                sessions[name]["last_seen_at"] = now
                if cwds and name in cwds:
                    sessions[name]["cwd"] = cwds[name]
            else:
                entry: dict[str, Any] = {"first_seen_at": now, "last_seen_at": now}
                if cwds and name in cwds:
                    entry["cwd"] = cwds[name]
                sessions[name] = entry
                changed = True
        for name in list(sessions):
            if name not in live_set:
                # Deliberate kill (or muxplex's own delete): tombstone by
                # removal. A tombstoned session is not in the manifest, so
                # it cannot be in pending_restore, so it can never be
                # restored. Reap rule 1: created_with's record for this
                # name is garbage the instant the session it describes is
                # confirmed dead -- pop it as a side effect of this SAME
                # `changed` trigger (do not add a second one; that would
                # break the "< 1 write/minute" steady-state target).
                del sessions[name]
                created_with.pop(name, None)
                changed = True
        new_manifest = {
            # S4 (plan §13.3): unknown top-level keys round-trip verbatim.
            **manifest,
            "schema": manifest.get("schema", MANIFEST_SCHEMA_VERSION),
            "epoch": epoch_rec,
            "sessions": sessions,
            "pending_restore": manifest.get("pending_restore"),
            "created_with": created_with,
        }
        return new_manifest, changed

    # ---- DIFFERENT SERVER: COLD START ----
    lost_names = [name for name in sessions if name not in live_set]
    pending_restore = manifest.get("pending_restore")
    if lost_names:
        # Frozen snapshot, not a live view -- once the new epoch below is
        # adopted, a LATER same-server cycle must not tombstone these very
        # entries just because they're still not live under the new server.
        # Storing them ONLY here (not left behind in `sessions` too) is what
        # makes that safe: a name that isn't in `sessions` can't be found by
        # the same-server branch's tombstone loop in the first place.
        pending_restore = {
            "detected_at": now,
            "lost_epoch": epoch_rec,
            "sessions": {name: sessions[name] for name in lost_names},
        }
    # The old epoch's bookkeeping does NOT carry forward -- a session that
    # was live under the OLD server is either (a) also live under the NEW
    # server (rebuilt fresh below, since a new server means a new process
    # even if the name matches) or (b) captured in pending_restore above.
    # Either way, nothing from the stale `sessions` dict should survive
    # un-frozen, or a later same-server cycle could tombstone a name that
    # was never truly re-observed under this epoch.
    new_sessions: dict[str, Any] = {}
    for name in live_names:
        entry: dict[str, Any] = {"first_seen_at": now, "last_seen_at": now}
        if cwds and name in cwds:
            entry["cwd"] = cwds[name]
        new_sessions[name] = entry
    # Reap rule 2: retain only created_with records for names that are
    # either currently live or frozen into pending_restore -- everything
    # else is garbage-collected here (this is the only place a
    # never-appeared-live session's leaked record is ever cleaned up; see
    # this module's bounded-growth analysis in the spec).
    retained_names = set(live_names) | set(
        (pending_restore or {}).get("sessions") or {}
    )
    created_with = {
        name: cmd_id for name, cmd_id in created_with.items() if name in retained_names
    }
    new_manifest = {
        # S4 (plan §13.3): unknown top-level keys round-trip verbatim --
        # a cold start rebuilds THIS function's keys fresh (new epoch, new
        # observation) but must not eat an app's keys riding beside them.
        **manifest,
        "schema": manifest.get("schema", MANIFEST_SCHEMA_VERSION),
        "epoch": {**epoch_now, "observed_at": now},
        "sessions": new_sessions,
        "pending_restore": pending_restore,
        "created_with": created_with,
    }
    return new_manifest, True


# ---------------------------------------------------------------------------
# Restore plan / restore bookkeeping -- pure functions, no I/O
# ---------------------------------------------------------------------------


def compute_restore_plan(
    manifest: dict[str, Any], live_names: set[str] | list[str]
) -> list[str]:
    """Names that are pending restore and NOT currently live, sorted.

    Pure and side-effect-free -- callers recompute this at whatever moment
    they need an up-to-date plan (SESSION_PERSISTENCE_DESIGN.md section 7.3:
    "plan = pending_restore - live, recomputed at execution time"). Always
    recomputing against the CURRENT live set (rather than trusting a
    snapshot taken earlier) is what makes restore idempotent: a name that
    came back on its own (or was already restored in an earlier run) is
    simply absent from the returned list, nothing extra to check.

    A name that was ever tombstoned (deliberately killed while muxplex was
    running) cannot appear here at all -- tombstoning removes it from
    manifest["sessions"] before any cold start can freeze it into
    pending_restore (see update_manifest()'s same-server branch), so there
    is no path by which a tombstoned name reaches pending_restore in the
    first place. This function has nothing extra to defend against; the
    protection is structural, upstream of this call.
    """
    pending = manifest.get("pending_restore")
    if not pending:
        return []
    pending_names = set((pending.get("sessions") or {}).keys())
    live_set = set(live_names)
    return sorted(pending_names - live_set)


def mark_restored(manifest: dict[str, Any], restored_names: set[str]) -> dict[str, Any]:
    """Remove *restored_names* from ``pending_restore["sessions"]``.

    Pure function -- returns a NEW manifest dict; never mutates *manifest*
    in place, so a caller doing a read-right-before-write (to minimize the
    window against a concurrently-running poll loop -- see restore.py) can
    call this against a freshly-loaded manifest and save the result
    immediately.

    If ``pending_restore`` is already ``None``, or removing the given names
    empties its ``sessions`` map, ``pending_restore`` becomes ``None``
    entirely -- an empty-but-present pending_restore is not a state this
    module wants to represent (mirrors the "None means nothing pending"
    convention update_manifest() already establishes). Names that FAILED to
    restore are simply not passed in, so they remain pending for a future
    `muxplex restore` to retry.
    """
    pending = manifest.get("pending_restore")
    if not pending:
        return manifest
    remaining = {
        name: info
        for name, info in (pending.get("sessions") or {}).items()
        if name not in restored_names
    }
    new_pending = None if not remaining else {**pending, "sessions": remaining}
    return {**manifest, "pending_restore": new_pending}

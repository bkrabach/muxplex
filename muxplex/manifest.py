"""
Session presence manifest -- a durable, device-local record of which tmux
sessions muxplex has observed alive, keyed by the identity of the tmux
server that hosted them.

Why this file exists: on 2026-07-29 a routine ``systemctl --user restart
muxplex.service`` SIGKILLed the tmux server (see AGENTS.md's "Two ways to
destroy every live tmux session on this host") and destroyed 44 live
sessions holding in-flight work. tmux is muxplex's only source of truth for
what sessions exist, so once the server died, muxplex simply reported fewer
sessions -- it had no memory of what had existed a moment before. The 44
names survived only by accident, in pruning.json's ``first_missed_at`` map,
a file whose entire purpose is the OPPOSITE of remembering: it exists to
track absence so stale view-membership keys can eventually be dropped, and
it deletes an entry the moment that session reappears (see pruning.py /
views.prune_stale_keys). Recovering the 44 names required copying that file
out *before* recreating anything, because recreating sessions one at a time
destroyed the recovery list mid-recovery.

This module is the fix for that specific failure: a manifest that records
PRESENCE (the opposite inversion) and is never cleared as a side effect of
sessions coming back. See SESSION_PERSISTENCE_DESIGN.md for the full design;
this is the "v1a -- record only" milestone (section 9.1): epoch probe +
manifest + one poll-loop call site + a read-only dry-run view. It creates,
kills, and restores NOTHING -- pure observation.

The three-way discrimination (SESSION_PERSISTENCE_DESIGN.md section 5):

    ============================  =============================================
    Event                         Manifest behavior
    ============================  =============================================
    muxplex restarts, tmux        Same epoch as before -> presence is
    survives                      authoritative -> no-op (the common case,
                                  handled by the cheapest branch).
    A session is deliberately     Same epoch, session missing from the live
    killed while muxplex runs     set -> tombstoned (removed from the
                                  manifest, AND pruned from pending_restore
                                  if it happened to be sitting there too --
                                  e.g. left behind by an interrupted restore
                                  run). Falls out of the same epoch
                                  comparison the reboot case needs, with no
                                  separate kill-tracking.
    Host reboots / tmux server    Different epoch -> sessions recorded under
    dies                          the OLD epoch that are not alive under the
                                  NEW one become pending_restore.
    No tmux server at all         Knowledge is UNAVAILABLE, not refuted. Do
    (e.g. during tmux's own       nothing: never tombstone, never declare a
    startup window)               cold start on absence alone -- the ARRIVAL
                                  of a new server is the event, not the
                                  absence of one.
    ============================  =============================================

Positive record, not negative -- the whole fix in one sentence: an entry is
removed by exactly one thing -- observed individual death against a live,
identity-matched server. It is never removed by a TTL, a "tidy up" sweep, or
as a side effect of anything else (mirroring the trap in pruning.json that
this module exists to not repeat).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from muxplex.state import STATE_DIR

# ---------------------------------------------------------------------------
# Paths and schema
# ---------------------------------------------------------------------------

MANIFEST_SCHEMA_VERSION = 1

# Beside state.json (STATE_DIR from state.py, honors MUXPLEX_STATE_DIR), NOT
# under ~/.config/muxplex/ -- this is observed state, not configuration.
# Never synced to federation peers -- device-local, like pruning.json.
MANIFEST_PATH: Path = STATE_DIR / "sessions.json"

# How long a pending_restore entry may sit unactioned before `muxplex restore`
# refuses to act on it without --force (SESSION_PERSISTENCE_DESIGN.md section
# 7.3, "never restore stale ghosts"). A module constant rather than a setting
# -- speculative to expose this as configurable before anyone has asked.
RESTORE_MAX_AGE_SECONDS: float = 7 * 86400.0


def _empty_manifest() -> dict[str, Any]:
    """Return a fresh, empty top-level manifest dict.

    Every call returns a fully independent object -- no shared mutables.
    """
    return {
        "schema": MANIFEST_SCHEMA_VERSION,
        "epoch": None,
        "sessions": {},
        "pending_restore": None,
    }


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load_manifest() -> dict[str, Any]:
    """Load the presence manifest from MANIFEST_PATH.

    Returns an empty manifest (schema populated, everything else empty) on
    an absent file or corrupt JSON -- never raises for either condition,
    mirroring pruning.load_pruning_state()'s tolerance. Unexpected errors
    (e.g. PermissionError) propagate.

    Defensive key defaults are applied so a hand-edited or partially-written
    file never causes a KeyError downstream.
    """
    try:
        text = MANIFEST_PATH.read_text(encoding="utf-8", errors="replace")
        data = json.loads(text)
        if not isinstance(data, dict):
            return _empty_manifest()
    except FileNotFoundError:
        return _empty_manifest()
    except (json.JSONDecodeError, ValueError):
        return _empty_manifest()

    data.setdefault("schema", MANIFEST_SCHEMA_VERSION)
    data.setdefault("epoch", None)
    if not isinstance(data.get("sessions"), dict):
        data["sessions"] = {}
    data.setdefault("pending_restore", None)
    return data


def save_manifest(manifest: dict[str, Any]) -> None:
    """Atomically persist *manifest* to MANIFEST_PATH.

    Uses the write-to-tmp-then-os.replace pattern shared with state.py, PLUS
    an explicit fsync of the tmp file's contents before the replace.
    os.replace() gives atomic *visibility* -- a reader never sees a partial
    file -- but it does not by itself guarantee the bytes reached disk
    before a power cut. This file exists specifically to survive an unclean
    shutdown (that is the whole point of a presence manifest), so the one
    extra syscall is the cheapest correctness available and is the one
    place this module deliberately differs from save_state()'s cheaper
    pattern (see SESSION_PERSISTENCE_DESIGN.md section 7.2).
    """
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(MANIFEST_PATH) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(manifest, indent=2) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, MANIFEST_PATH)


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
) -> tuple[dict[str, Any], bool]:
    """Apply one poll cycle's observation to *manifest*.

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

    if epoch_rec is None:
        # First run ever, or first run after upgrade: adopt. Nothing is
        # "lost" relative to an epoch we've never recorded.
        for name in live_names:
            sessions[name] = {"first_seen_at": now, "last_seen_at": now}
        new_manifest = {
            "schema": manifest.get("schema", MANIFEST_SCHEMA_VERSION),
            "epoch": {**epoch_now, "observed_at": now},
            "sessions": sessions,
            "pending_restore": manifest.get("pending_restore"),
        }
        return new_manifest, True

    if _same_epoch(epoch_now, epoch_rec):
        # ---- SAME SERVER: presence is authoritative ----
        changed = False
        tombstoned: set[str] = set()
        for name in live_names:
            if name in sessions:
                sessions[name]["last_seen_at"] = now
            else:
                sessions[name] = {"first_seen_at": now, "last_seen_at": now}
                changed = True
        for name in list(sessions):
            if name not in live_set:
                # Deliberate kill (or muxplex's own delete): tombstone by
                # removal. A tombstoned session is not in `sessions`, so a
                # LATER cold start can never freeze it into pending_restore
                # (see the different-server branch below).
                del sessions[name]
                tombstoned.add(name)
                changed = True

        pending_restore = manifest.get("pending_restore")
        if tombstoned and pending_restore:
            # The invariant this module claims -- "a tombstoned session is
            # not in the manifest, so it cannot be in pending_restore, so it
            # can never be restored" -- only holds going FORWARD from a
            # clean manifest. It does NOT hold if the name is ALREADY
            # sitting in pending_restore when the deliberate kill happens
            # (e.g. left behind by an interrupted `muxplex restore` run, or
            # any other stale pending_restore entry): tombstoning here only
            # ever touched `sessions`, never `pending_restore`, so
            # compute_restore_plan() would still offer the just-killed name
            # right back up for restore. Reuse mark_restored() (the same
            # pure removal `execute_restore()` uses) so this closes the
            # exact same way a successful restore does.
            pending_restore = mark_restored(
                {"pending_restore": pending_restore}, tombstoned
            )["pending_restore"]

        new_manifest = {
            "schema": manifest.get("schema", MANIFEST_SCHEMA_VERSION),
            "epoch": epoch_rec,
            "sessions": sessions,
            "pending_restore": pending_restore,
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
    new_sessions = {
        name: {"first_seen_at": now, "last_seen_at": now} for name in live_names
    }
    new_manifest = {
        "schema": manifest.get("schema", MANIFEST_SCHEMA_VERSION),
        "epoch": {**epoch_now, "observed_at": now},
        "sessions": new_sessions,
        "pending_restore": pending_restore,
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
    running) cannot appear here at all: tombstoning removes it from
    manifest["sessions"] before any cold start can freeze it into
    pending_restore (see update_manifest()'s same-server branch), AND -- if
    the name was already sitting in pending_restore at the moment of the
    kill (e.g. left behind by an interrupted restore run) -- the same
    tombstone event prunes it from there too. This function has nothing
    extra to defend against; the protection lives entirely upstream, in
    update_manifest()'s same-server branch.
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

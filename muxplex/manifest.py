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
                                  manifest) -> can never appear in
                                  pending_restore. This is free: it falls out
                                  of the same epoch comparison the reboot case
                                  needs, with no separate kill-tracking.
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

Restore fidelity: recording an observed working directory
-----------------------------------------------------------

**Incident (2026-08-05):** a real tmux-server death took 52 sessions; 44 came
back on their own, `muxplex restore` recreated 8 more with 0 reported
failures -- but two of those eight came back WRONG, in the exact way
AGENTS.md warns about ("a bare tmux session ... looks restored and isn't"):
long-running hand-started daemons rooted OUTSIDE the `~/dev/<name>`
convention (one at `$HOME`, one at a nested project directory several levels
below `~/dev/`). Neither had a `created_with` record (see below), so restore
fell through to the reserved default session command -- which not only
started the wrong process, it CREATED `~/dev/<name>` for both, directories
that had never existed. The dashboard then showed both green.

The gap: this module recorded PRESENCE faithfully but not HOW a session came
to be, unless it came through a configured `session_commands` pair (see
`created_with` below). The sessions least reconstructible from their name
alone -- hand-started daemons rooted anywhere but the convention -- are
exactly the ones `created_with` has nothing to say about.

The fix, scoped to what is actually observable: every poll cycle,
`sessions.get_session_cwds()` reports each live session's active pane's
current directory (tmux's own `#{pane_current_path}` -- the SAME technique
`~/dotfiles/bin/amplifier-workspace-snapshot` uses via `/proc/<pid>/cwd`, one
layer higher since tmux already resolves it). `update_manifest()` records
this as `cwd` on each session entry, updated in place every cycle exactly
like `last_seen_at` -- never triggering `changed=True` on its own, so the
"< 1 write/minute" steady-state budget is unaffected. When a cold start
freezes a session into `pending_restore`, whatever `cwd` was last observed
freezes with it -- a real, dated fact about where the session was actually
running moments before it was lost.

**What is deliberately NOT attempted: recovering the launch COMMAND.** A
daemon's original command line is only as durable as its own process's
`/proc/<pid>/cmdline` (or tmux's `#{pane_start_command}`, which reflects the
pane's first command, not necessarily anything a user later typed into an
interactive shell) -- and a shell that has since been typed into is not
proof of what it started as. Rather than fabricate a maybe-right command
from an unreliable signal, this module records only the ONE thing that is
genuinely, continuously observable while a session is alive: where it runs
from. `restore.py`'s fidelity check uses this fact to decide between two
honest outcomes -- restore via the default pair (the recorded root matches
the convention it assumes), or REFUSE with an actionable reason (it
doesn't) -- never a silent substitution that fabricates the missing
command. See restore.py's module docstring for the refusal itself, and its
`_default_workspace_root()` / fidelity-check functions for the mechanism.
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

MANIFEST_SCHEMA_VERSION = 2

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
        "created_with": {},
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
    if not isinstance(data.get("created_with"), dict):
        data["created_with"] = {}
    # Forward-only version normalization, mirroring settings.save_settings()'s
    # stance on `_schema_version` ("clients do not get to write older
    # versions"). A v1 manifest read by this code IS a v2 manifest -- the
    # only difference is created_with, which we just materialized as {} --
    # so recording it as v1 would be a lie. Nothing branches on this value;
    # it exists as an honest marker, not a switch.
    data["schema"] = MANIFEST_SCHEMA_VERSION
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


def get_restore_cwd(manifest: dict[str, Any], name: str) -> str | None:
    """Return the ``cwd`` last observed for *name* in a frozen
    ``pending_restore`` snapshot, or None.

    None means "no observed cwd" -- either *name* is not currently pending
    restore at all, or it was pending before this field existed (an
    upgraded manifest whose frozen snapshot predates cwd tracking), or tmux
    never reported a `#{pane_current_path}` for it during any cycle it was
    alive. All three are indistinguishable and deliberately treated the
    same way by restore.py's fidelity check: absence of evidence, not
    evidence of a mismatch -- see that module's docstring.

    Reads ONLY the frozen snapshot (``pending_restore["sessions"][name]``),
    never the live ``sessions[name]`` -- restore only ever needs the cwd a
    session had at the moment it was lost, and by the time restore runs the
    live ``sessions`` entry for a still-dead name has already been dropped
    (see update_manifest()'s cold-start branch: the stale dict does not
    carry forward un-frozen).
    """
    pending = manifest.get("pending_restore")
    if not pending:
        return None
    entry = (pending.get("sessions") or {}).get(name)
    if not entry:
        return None
    cwd = entry.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


# ---------------------------------------------------------------------------
# created_with accessors -- named session command pairs
# ---------------------------------------------------------------------------


def get_created_with(manifest: dict[str, Any], name: str) -> str | None:
    """Return the command_id recorded for *name* at create time, or None.

    None means "muxplex has no record of creating this session" -- a
    pre-existing tmux session, one created outside muxplex, or one created
    before this feature existed. Callers treat None as "use the default
    pair", which is byte-identical to pre-feature behavior. It is NOT the
    same as a recorded-but-unresolvable id (see main.py's delete_session()).
    """
    return manifest.get("created_with", {}).get(name)


def set_created_with(
    manifest: dict[str, Any], name: str, command_id: str
) -> dict[str, Any]:
    """Return a NEW manifest with ``created_with[name] = command_id``.

    Pure -- never mutates *manifest* in place, matching mark_restored()'s
    contract, so a caller doing a read-right-before-write (to minimize the
    window against the concurrently running poll loop -- see restore.py's
    module docstring) can call this on a freshly-loaded manifest and save
    the result immediately.
    """
    created_with = dict(manifest.get("created_with", {}))
    created_with[name] = command_id
    return {**manifest, "created_with": created_with}

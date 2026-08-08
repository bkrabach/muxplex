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

# Tmux-lib extraction stage S1 (plan §7.1): the PURE presence rule --
# _same_epoch / update_manifest / compute_restore_plan / mark_restored and
# the schema constant they share -- moved to tmuxkit.presence and is
# re-exported here so every existing import path keeps working. Manifest
# I/O (load_manifest/save_manifest) stays HERE: it defaults its path to
# muxplex's STATE_DIR, an app-side fact §13.3's injected-path shape removes
# in a later stage, not in a pure move.
from tmuxkit.presence import (  # noqa: F401  (re-exported)
    MANIFEST_SCHEMA_VERSION,
    _same_epoch,
    compute_restore_plan,
    mark_restored,
    update_manifest,
)

# ---------------------------------------------------------------------------
# Paths and schema
# ---------------------------------------------------------------------------

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
        "rename_in_flight": None,
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
    data.setdefault("rename_in_flight", None)
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


# ---------------------------------------------------------------------------
# Rename journal -- write-ahead intent for POST /api/sessions/{name}/rename
# (docs/plans/2026-08-07-session-rename-plan.md \u00a76)
# ---------------------------------------------------------------------------
#
# The rename endpoint touches one irreversible subprocess (`tmux
# rename-session`) plus four independently-atomic file writes, with no
# cross-file transaction. Recording intent here BEFORE anything changes --
# fsync'd, via the same save_manifest() every other manifest write already
# uses -- is what lets a crash mid-rename (process death, or the poll cycle
# observing a half-done rename) converge to a correct end state instead of
# either reverting a rename the caller was told succeeded, or destroying the
# keyspaces of a session that already has its new name. See the plan's \u00a76.1
# for why neither "tmux first" nor "tmux last" ordering is safe without this.


def start_rename_journal(
    manifest: dict[str, Any], old_name: str, new_name: str, *, now: float | None = None
) -> dict[str, Any]:
    """Return a NEW manifest with ``rename_in_flight`` recording an intent to
    rename *old_name* to *new_name*.

    Pure -- never mutates *manifest* in place (matching every other helper
    in this module). Callers must ``save_manifest()`` the result BEFORE
    calling ``tmux rename-session`` or touching any other keyspace -- the
    fsync inside ``save_manifest()`` is what makes this durable across an
    unclean shutdown.
    """
    now = time.time() if now is None else now
    return {
        **manifest,
        "rename_in_flight": {"from": old_name, "to": new_name, "at": now},
    }


def clear_rename_journal(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a NEW manifest with ``rename_in_flight`` cleared (set to None).

    Pure -- never mutates *manifest* in place. Idempotent: clearing an
    already-clear journal produces the same value.
    """
    return {**manifest, "rename_in_flight": None}


def get_rename_in_flight(manifest: dict[str, Any]) -> dict[str, Any] | None:
    """Return the in-flight rename journal entry (``{"from", "to", "at"}``),
    or None if no rename is in flight. Read by the poll cycle at step 1b,
    BEFORE ``update_manifest()`` (see the plan's \u00a76.2 completion table).
    """
    journal = manifest.get("rename_in_flight")
    return journal if isinstance(journal, dict) else None


def get_renamed_from(manifest: dict[str, Any], name: str) -> str | None:
    """Return the name *name* was most recently renamed from, or None.

    None means *name* has no manifest entry, or was never renamed. Read
    from ``manifest["sessions"][name]["renamed_from"]`` -- a field set by
    the rename migration (main.py's ``_migrate_session_name``) and carried
    forward untouched by every subsequent poll cycle's ``update_manifest()``
    call (which only ever updates ``last_seen_at``/``cwd`` in place on an
    existing entry, never replaces it wholesale) and, critically, frozen
    into ``pending_restore`` verbatim if the session is later lost.

    This is what lets ``restore.py``'s fidelity check refuse recreating a
    renamed session via a name-derived default command: renaming a tmux
    session moves nothing on disk, so the OLD name -- not the current one --
    is what actually describes where the session used to run (see the
    rename plan \u00a79.2/\u00a79.3).
    """
    entry = manifest.get("sessions", {}).get(name)
    if not entry:
        return None
    value = entry.get("renamed_from")
    return value if isinstance(value, str) and value else None

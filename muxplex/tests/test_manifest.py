"""
Tests for muxplex/manifest.py -- the session-presence manifest.

Covers:
- load_manifest() tolerance (absent file, corrupt JSON, non-dict JSON,
  partial/legacy content) -- mirrors test_pruning.py's shape.
- save_manifest() round-trip, atomicity (no .tmp left behind), fsync.
- update_manifest()'s three-way discrimination (SESSION_PERSISTENCE_DESIGN.md
  section 5): same-server / cold-start / no-server-available, plus the
  first-run/adopt case.
- The invariant this module exists to enforce: a session's entry is
  removed ONLY by an observed individual death against a live,
  identity-matched server -- never by a TTL, never as a side effect of
  anything else, and it survives the very cold-start event it's designed
  to record (unlike pruning.json, which erases the evidence during
  recovery).
"""

import json
from pathlib import Path

import pytest

import muxplex.manifest as manifest_mod
from muxplex.manifest import (
    RESTORE_MAX_AGE_SECONDS,
    compute_restore_plan,
    get_created_with,
    load_manifest,
    mark_restored,
    save_manifest,
    set_created_with,
    update_manifest,
)

EPOCH_A = {
    "socket_path": "/home/user/.tmux/tmux-1000/default",
    "server_pid": 111,
    "inode": 1,
}
EPOCH_B = {
    "socket_path": "/home/user/.tmux/tmux-1000/default",
    "server_pid": 222,
    "inode": 2,
}
EPOCH_A_SCRATCH = {
    "socket_path": "/tmp/scratch-socket/tmux-1000/default",
    "server_pid": 999,
    "inode": 9,
}


@pytest.fixture(autouse=True)
def redirect_manifest_path(tmp_path, monkeypatch):
    """Redirect MANIFEST_PATH to a temporary file for every test."""
    fake_path = tmp_path / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", fake_path)
    return fake_path


# ---------------------------------------------------------------------------
# load_manifest() -- tolerance
# ---------------------------------------------------------------------------


def test_load_manifest_returns_empty_when_file_absent():
    """load_manifest() returns an empty-but-well-formed manifest when absent.

    schema is 2 (MANIFEST_SCHEMA_VERSION) and created_with is {} -- added
    for named session command pairs (COMMAND_PAIRS_SPEC.md)."""
    result = load_manifest()
    assert result == {
        "schema": 2,
        "epoch": None,
        "sessions": {},
        "pending_restore": None,
        "created_with": {},
    }


def test_load_manifest_returns_empty_on_corrupt_json(redirect_manifest_path):
    """load_manifest() returns an empty manifest on corrupt JSON -- never raises."""
    redirect_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    redirect_manifest_path.write_text("NOT VALID JSON {{{{")

    result = load_manifest()
    assert result["sessions"] == {}
    assert result["epoch"] is None


def test_load_manifest_returns_empty_on_non_dict_json(redirect_manifest_path):
    """load_manifest() returns an empty manifest when JSON parses to a non-dict."""
    redirect_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    redirect_manifest_path.write_text(json.dumps([1, 2, 3]))

    result = load_manifest()
    assert result["sessions"] == {}


def test_load_manifest_applies_defensive_defaults_to_partial_content(
    redirect_manifest_path,
):
    """A hand-edited manifest missing keys still loads with safe defaults."""
    redirect_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    redirect_manifest_path.write_text(json.dumps({"schema": 1}))

    result = load_manifest()
    assert result["sessions"] == {}
    assert result["epoch"] is None
    assert result["pending_restore"] is None


# ---------------------------------------------------------------------------
# save_manifest() -- round-trip, atomicity
# ---------------------------------------------------------------------------


def test_save_then_load_round_trip():
    """save_manifest then load_manifest returns the same data."""
    manifest = {
        "schema": 2,
        "epoch": EPOCH_A,
        "sessions": {"a2a": {"first_seen_at": 100.0, "last_seen_at": 200.0}},
        "pending_restore": None,
        "created_with": {},
    }

    save_manifest(manifest)
    loaded = load_manifest()

    assert loaded == manifest


def test_save_manifest_creates_parent_directories(tmp_path, monkeypatch):
    """save_manifest creates parent directories as needed."""
    nested_path = tmp_path / "a" / "b" / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", nested_path)

    save_manifest(manifest_mod._empty_manifest())

    assert nested_path.exists()


def test_save_manifest_leaves_no_tmp_file(redirect_manifest_path):
    """After save_manifest, no .tmp artifact remains (atomic os.replace)."""
    save_manifest(manifest_mod._empty_manifest())

    tmp_file = Path(str(redirect_manifest_path) + ".tmp")
    assert not tmp_file.exists()
    assert redirect_manifest_path.exists()


def test_save_manifest_writes_valid_json(redirect_manifest_path):
    """save_manifest writes well-formed, parseable JSON."""
    manifest = {"schema": 1, "epoch": None, "sessions": {}, "pending_restore": None}
    save_manifest(manifest)

    parsed = json.loads(redirect_manifest_path.read_text())
    assert parsed == manifest


# ---------------------------------------------------------------------------
# update_manifest() -- no tmux server available
# ---------------------------------------------------------------------------


def test_update_manifest_no_server_is_unchanged():
    """epoch_now=None (no tmux server) leaves the manifest completely untouched.

    Knowledge is unavailable, not refuted -- must never tombstone, never
    declare a cold start on absence alone.
    """
    manifest = {
        "schema": 1,
        "epoch": EPOCH_A,
        "sessions": {"a2a": {"first_seen_at": 1.0, "last_seen_at": 2.0}},
        "pending_restore": None,
    }

    new_manifest, changed = update_manifest(manifest, None, [])

    assert changed is False
    assert new_manifest is manifest
    assert new_manifest["sessions"] == {
        "a2a": {"first_seen_at": 1.0, "last_seen_at": 2.0}
    }


# ---------------------------------------------------------------------------
# update_manifest() -- first run / adopt
# ---------------------------------------------------------------------------


def test_update_manifest_first_run_adopts_epoch_never_populates_pending_restore():
    """First run ever (manifest.epoch is None) adopts the epoch and records
    live sessions, but NEVER populates pending_restore -- nothing can be
    'lost' relative to an epoch we've never recorded."""
    manifest = manifest_mod._empty_manifest()

    new_manifest, changed = update_manifest(
        manifest, EPOCH_A, ["a2a", "bbs"], now=1000.0
    )

    assert changed is True
    assert new_manifest["epoch"]["socket_path"] == EPOCH_A["socket_path"]
    assert new_manifest["epoch"]["server_pid"] == EPOCH_A["server_pid"]
    assert new_manifest["epoch"]["inode"] == EPOCH_A["inode"]
    assert new_manifest["epoch"]["observed_at"] == 1000.0
    assert set(new_manifest["sessions"]) == {"a2a", "bbs"}
    assert new_manifest["sessions"]["a2a"] == {
        "first_seen_at": 1000.0,
        "last_seen_at": 1000.0,
    }
    assert new_manifest["pending_restore"] is None


# ---------------------------------------------------------------------------
# update_manifest() -- same server (the common, cheap, no-op case)
# ---------------------------------------------------------------------------


def test_update_manifest_same_server_unchanged_sessions_is_a_noop():
    """Same epoch, same live set -> changed=False (the common muxplex-restart
    case: no new writes, no pending_restore)."""
    manifest = {
        "schema": 1,
        "epoch": {**EPOCH_A, "observed_at": 500.0},
        "sessions": {"a2a": {"first_seen_at": 100.0, "last_seen_at": 100.0}},
        "pending_restore": None,
    }

    new_manifest, changed = update_manifest(manifest, EPOCH_A, ["a2a"], now=600.0)

    assert changed is False
    assert new_manifest["pending_restore"] is None
    assert "a2a" in new_manifest["sessions"]


def test_update_manifest_same_server_new_session_is_recorded():
    """Same epoch, a new session appears -> recorded, changed=True."""
    manifest = {
        "schema": 1,
        "epoch": {**EPOCH_A, "observed_at": 500.0},
        "sessions": {"a2a": {"first_seen_at": 100.0, "last_seen_at": 100.0}},
        "pending_restore": None,
    }

    new_manifest, changed = update_manifest(
        manifest, EPOCH_A, ["a2a", "new-one"], now=700.0
    )

    assert changed is True
    assert "new-one" in new_manifest["sessions"]
    assert new_manifest["sessions"]["new-one"] == {
        "first_seen_at": 700.0,
        "last_seen_at": 700.0,
    }
    assert new_manifest["pending_restore"] is None


def test_update_manifest_same_server_deliberate_kill_is_tombstoned_not_pending():
    """THE sharpest failure mode this design targets: a session killed while
    muxplex keeps running (same epoch) must be permanently removed from the
    manifest -- NOT queued for restore. A tombstoned session cannot appear
    in pending_restore because it isn't in the manifest to begin with."""
    manifest = {
        "schema": 1,
        "epoch": {**EPOCH_A, "observed_at": 500.0},
        "sessions": {
            "a2a": {"first_seen_at": 100.0, "last_seen_at": 100.0},
            "killed-on-purpose": {"first_seen_at": 100.0, "last_seen_at": 100.0},
        },
        "pending_restore": None,
    }

    # killed-on-purpose is no longer in the live set, but the epoch is
    # IDENTICAL -- the tmux server never died.
    new_manifest, changed = update_manifest(manifest, EPOCH_A, ["a2a"], now=800.0)

    assert changed is True
    assert "killed-on-purpose" not in new_manifest["sessions"]
    assert new_manifest["pending_restore"] is None, (
        "a deliberate kill against a live, identity-matched server must "
        "NEVER populate pending_restore -- resurrecting it would be worse "
        "than not restoring at all"
    )


def test_update_manifest_same_server_multiple_deaths_all_tombstoned():
    """Several sessions disappearing at once (e.g. the user closed a batch)
    under an unchanged epoch are all tombstoned -- none land in
    pending_restore."""
    manifest = {
        "schema": 1,
        "epoch": {**EPOCH_A, "observed_at": 500.0},
        "sessions": {
            "keep-me": {"first_seen_at": 1.0, "last_seen_at": 1.0},
            "gone-1": {"first_seen_at": 1.0, "last_seen_at": 1.0},
            "gone-2": {"first_seen_at": 1.0, "last_seen_at": 1.0},
        },
        "pending_restore": None,
    }

    new_manifest, changed = update_manifest(manifest, EPOCH_A, ["keep-me"], now=900.0)

    assert changed is True
    assert set(new_manifest["sessions"]) == {"keep-me"}
    assert new_manifest["pending_restore"] is None


# ---------------------------------------------------------------------------
# update_manifest() -- different server (cold start)
# ---------------------------------------------------------------------------


def test_update_manifest_different_server_populates_pending_restore():
    """A different tmux server identity (host reboot, cgroup SIGKILL, etc.)
    with sessions recorded under the OLD epoch now missing -> those sessions
    become pending_restore, tagged with the OLD (lost) epoch."""
    manifest = {
        "schema": 1,
        "epoch": {**EPOCH_A, "observed_at": 100.0},
        "sessions": {
            "a2a": {"first_seen_at": 50.0, "last_seen_at": 100.0},
            "bbs": {"first_seen_at": 60.0, "last_seen_at": 100.0},
        },
        "pending_restore": None,
    }

    # New server (EPOCH_B), and neither old session is alive under it.
    new_manifest, changed = update_manifest(manifest, EPOCH_B, [], now=5000.0)

    assert changed is True
    assert new_manifest["epoch"]["server_pid"] == EPOCH_B["server_pid"]
    pending = new_manifest["pending_restore"]
    assert pending is not None
    assert pending["detected_at"] == 5000.0
    assert pending["lost_epoch"]["server_pid"] == EPOCH_A["server_pid"]
    assert set(pending["sessions"]) == {"a2a", "bbs"}
    # New (empty) live set means the manifest's own `sessions` dict is now
    # empty too -- the sessions only survive inside the frozen snapshot.
    assert new_manifest["sessions"] == {}


def test_update_manifest_cold_start_pending_restore_is_frozen_not_live():
    """pending_restore must be a FROZEN snapshot: a later same-server poll
    cycle (now running under the NEW epoch) must not tombstone the entries
    just queued for restore. This is the exact bug pruning.json has -- an
    entry erased the moment the thing it's tracking is no longer live."""
    manifest = {
        "schema": 1,
        "epoch": {**EPOCH_A, "observed_at": 100.0},
        "sessions": {"a2a": {"first_seen_at": 50.0, "last_seen_at": 100.0}},
        "pending_restore": None,
    }

    # Cycle 1: cold start detected.
    manifest, changed1 = update_manifest(manifest, EPOCH_B, [], now=5000.0)
    assert changed1 is True
    assert manifest["pending_restore"] is not None
    assert "a2a" in manifest["pending_restore"]["sessions"]

    # Cycle 2: same (new) server, still no "a2a" live. Because the epoch is
    # now EPOCH_B on both sides, this is the SAME-SERVER branch -- but "a2a"
    # was never in manifest["sessions"] under the new epoch, so there is
    # nothing to tombstone. pending_restore must be untouched.
    manifest, changed2 = update_manifest(manifest, EPOCH_B, [], now=5010.0)
    assert changed2 is False
    assert manifest["pending_restore"] is not None
    assert "a2a" in manifest["pending_restore"]["sessions"], (
        "pending_restore must survive subsequent poll cycles under the new "
        "epoch -- it is a frozen snapshot, not a live view"
    )


def test_update_manifest_cold_start_no_lost_sessions_leaves_pending_restore_none():
    """Different epoch but every previously-recorded session is ALSO alive
    under the new epoch (e.g. tmux socket path churn with sessions somehow
    intact) -> no cold start plan is needed."""
    manifest = {
        "schema": 1,
        "epoch": {**EPOCH_A, "observed_at": 100.0},
        "sessions": {"a2a": {"first_seen_at": 50.0, "last_seen_at": 100.0}},
        "pending_restore": None,
    }

    new_manifest, changed = update_manifest(manifest, EPOCH_B, ["a2a"], now=5000.0)

    assert changed is True  # epoch changed, so the manifest write still happens
    assert new_manifest["pending_restore"] is None


# ---------------------------------------------------------------------------
# Epoch identity -- socket_path is part of equality (scratch-instance safety)
# ---------------------------------------------------------------------------


def test_update_manifest_different_socket_path_is_treated_as_different_server():
    """Two epochs with the same pid/inode by coincidence but a DIFFERENT
    socket_path (e.g. a scratch instance on its own TMUX_TMPDIR) must never
    compare equal -- this is what keeps a scratch run from ever tombstoning
    the live manifest."""
    manifest = {
        "schema": 1,
        "epoch": {**EPOCH_A, "observed_at": 100.0},
        "sessions": {"a2a": {"first_seen_at": 50.0, "last_seen_at": 100.0}},
        "pending_restore": None,
    }
    # Same pid/inode values as EPOCH_A, but a different socket_path.
    scratch_epoch = {**EPOCH_A, "socket_path": "/tmp/other-scratch/tmux-1000/default"}

    new_manifest, changed = update_manifest(manifest, scratch_epoch, [], now=5000.0)

    # Must take the COLD-START branch (different epoch), never the
    # same-server branch, even though pid and inode happen to match.
    assert changed is True
    assert new_manifest["pending_restore"] is not None
    assert "a2a" in new_manifest["pending_restore"]["sessions"]


# ---------------------------------------------------------------------------
# compute_restore_plan() -- the plan is always recomputed against live state
# ---------------------------------------------------------------------------


def _manifest_with_pending(names: list[str], *, detected_at: float = 1000.0) -> dict:
    return {
        "schema": 1,
        "epoch": {**EPOCH_B, "observed_at": detected_at},
        "sessions": {},
        "pending_restore": {
            "detected_at": detected_at,
            "lost_epoch": EPOCH_A,
            "sessions": {
                name: {"first_seen_at": 1.0, "last_seen_at": 2.0} for name in names
            },
        },
    }


def test_compute_restore_plan_no_pending_returns_empty():
    manifest = {"schema": 1, "epoch": EPOCH_A, "sessions": {}, "pending_restore": None}
    assert compute_restore_plan(manifest, []) == []


def test_compute_restore_plan_excludes_already_live_names():
    """A name that's already live (came back on its own, or was already
    restored by an earlier run) must not appear in the plan -- this is
    what makes restore idempotent by construction."""
    manifest = _manifest_with_pending(["a2a", "bbs", "ccc"])
    plan = compute_restore_plan(manifest, live_names=["bbs"])
    assert plan == ["a2a", "ccc"]


def test_compute_restore_plan_is_sorted():
    manifest = _manifest_with_pending(["zzz", "aaa", "mmm"])
    assert compute_restore_plan(manifest, live_names=[]) == ["aaa", "mmm", "zzz"]


def test_compute_restore_plan_all_live_is_empty():
    manifest = _manifest_with_pending(["a2a", "bbs"])
    assert compute_restore_plan(manifest, live_names=["a2a", "bbs"]) == []


def test_compute_restore_plan_tombstoned_name_structurally_absent():
    """A tombstoned session is removed from manifest['sessions'] by
    update_manifest()'s same-server branch BEFORE any cold start can freeze
    it into pending_restore -- so there is no manifest shape in which a
    tombstoned name could appear in pending_restore for compute_restore_plan
    to have to filter out. Proven end-to-end (real tmux) in
    test_integration_manifest.py; this test documents the structural
    argument at the pure-function level: pending_restore's sessions dict
    simply never contains it.
    """
    # Simulate: 'killed-on-purpose' was tombstoned (removed from `sessions`)
    # before the cold start that produced this pending_restore.
    manifest = _manifest_with_pending(["a2a"])  # only the survivor is here
    plan = compute_restore_plan(manifest, live_names=[])
    assert "killed-on-purpose" not in plan
    assert plan == ["a2a"]


# ---------------------------------------------------------------------------
# mark_restored() -- clears successfully-restored names, leaves failures
# ---------------------------------------------------------------------------


def test_mark_restored_removes_given_names():
    manifest = _manifest_with_pending(["a2a", "bbs", "ccc"])
    updated = mark_restored(manifest, {"a2a", "bbs"})
    assert set(updated["pending_restore"]["sessions"]) == {"ccc"}


def test_mark_restored_empties_to_none():
    """When every pending name has been restored, pending_restore becomes
    None entirely -- not an empty-but-present dict."""
    manifest = _manifest_with_pending(["a2a", "bbs"])
    updated = mark_restored(manifest, {"a2a", "bbs"})
    assert updated["pending_restore"] is None


def test_mark_restored_leaves_unmentioned_names_pending():
    """A name that FAILED to restore (not passed in restored_names) must
    remain in pending_restore so a future `muxplex restore` retries it."""
    manifest = _manifest_with_pending(["a2a", "bbs"])
    updated = mark_restored(manifest, {"a2a"})
    assert set(updated["pending_restore"]["sessions"]) == {"bbs"}


def test_mark_restored_noop_when_nothing_pending():
    manifest = {"schema": 1, "epoch": EPOCH_A, "sessions": {}, "pending_restore": None}
    updated = mark_restored(manifest, {"a2a"})
    assert updated["pending_restore"] is None


def test_mark_restored_is_pure_does_not_mutate_input():
    """mark_restored() must return a NEW dict, never mutate the manifest
    passed in -- callers rely on this to do a read-right-before-write
    without aliasing bugs (see restore.py's module docstring)."""
    manifest = _manifest_with_pending(["a2a", "bbs"])
    original_sessions = dict(manifest["pending_restore"]["sessions"])
    mark_restored(manifest, {"a2a"})
    assert manifest["pending_restore"]["sessions"] == original_sessions


# ---------------------------------------------------------------------------
# created_with -- named session command pairs (COMMAND_PAIRS_SPEC.md)
# ---------------------------------------------------------------------------


def test_load_v1_manifest_yields_empty_created_with(redirect_manifest_path):
    """A v1 file on disk (no created_with key) loads with created_with == {}
    and schema forward-normalized to 2."""
    redirect_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    redirect_manifest_path.write_text(
        json.dumps(
            {"schema": 1, "epoch": EPOCH_A, "sessions": {}, "pending_restore": None}
        )
    )
    result = load_manifest()
    assert result["created_with"] == {}
    assert result["schema"] == 2


def test_set_created_with_is_pure():
    manifest = {"schema": 2, "epoch": None, "sessions": {}, "pending_restore": None}
    set_created_with(manifest, "my-session", "amplifier")
    assert "created_with" not in manifest or manifest.get("created_with") in (None, {})


def test_set_created_with_returns_new_manifest_with_record():
    manifest = {
        "schema": 2,
        "epoch": None,
        "sessions": {},
        "pending_restore": None,
        "created_with": {},
    }
    updated = set_created_with(manifest, "my-session", "amplifier")
    assert updated["created_with"] == {"my-session": "amplifier"}
    assert manifest["created_with"] == {}  # original untouched


def test_get_created_with_absent_returns_none():
    manifest = {
        "schema": 2,
        "epoch": None,
        "sessions": {},
        "pending_restore": None,
        "created_with": {},
    }
    assert get_created_with(manifest, "unknown") is None


def test_get_created_with_returns_recorded_id():
    manifest = {
        "schema": 2,
        "epoch": None,
        "sessions": {},
        "pending_restore": None,
        "created_with": {"my-session": "amplifier"},
    }
    assert get_created_with(manifest, "my-session") == "amplifier"


def test_same_epoch_tombstone_reaps_created_with():
    """A session recorded, then absent from live_names at the same epoch,
    is tombstoned from BOTH sessions and created_with."""
    manifest = {
        "schema": 2,
        "epoch": EPOCH_A,
        "sessions": {"gone": {"first_seen_at": 1.0, "last_seen_at": 1.0}},
        "pending_restore": None,
        "created_with": {"gone": "amplifier"},
    }
    new_manifest, changed = update_manifest(manifest, EPOCH_A, [], now=2.0)
    assert changed is True
    assert "gone" not in new_manifest["sessions"]
    assert "gone" not in new_manifest["created_with"]


def test_created_with_survives_before_first_observation():
    """The §0.3 regression guard: a record written for a name never yet in
    live_names must survive a same-epoch cycle (not be tombstoned) -- this
    is the whole reason created_with lives in the manifest and not
    state.json's reap-every-2s sessions map."""
    manifest = {
        "schema": 2,
        "epoch": EPOCH_A,
        "sessions": {},
        "pending_restore": None,
        "created_with": {"not-yet-live": "amplifier"},
    }
    new_manifest, _changed = update_manifest(manifest, EPOCH_A, [], now=2.0)
    assert new_manifest["created_with"] == {"not-yet-live": "amplifier"}


def test_tmux_unavailable_never_reaps_created_with():
    manifest = {
        "schema": 2,
        "epoch": EPOCH_A,
        "sessions": {"x": {"first_seen_at": 1.0, "last_seen_at": 1.0}},
        "pending_restore": None,
        "created_with": {"x": "amplifier"},
    }
    new_manifest, changed = update_manifest(manifest, None, [], now=2.0)
    assert changed is False
    assert new_manifest is manifest
    assert new_manifest["created_with"] == {"x": "amplifier"}


def test_cold_start_retains_live_and_pending_created_with():
    """Cold start: created_with entries for names that are either currently
    live OR frozen into pending_restore are retained; an unrelated,
    never-appeared name is dropped (bounded-growth garbage collection)."""
    manifest = {
        "schema": 2,
        "epoch": EPOCH_A,
        "sessions": {
            "still-live": {"first_seen_at": 1.0, "last_seen_at": 1.0},
            "will-be-pending": {"first_seen_at": 1.0, "last_seen_at": 1.0},
        },
        "pending_restore": None,
        "created_with": {
            "still-live": "amplifier",
            "will-be-pending": "scratch",
            "leaked": "amplifier",
        },
    }
    new_manifest, changed = update_manifest(manifest, EPOCH_B, ["still-live"], now=10.0)
    assert changed is True
    assert new_manifest["created_with"] == {
        "still-live": "amplifier",
        "will-be-pending": "scratch",
    }
    assert "leaked" not in new_manifest["created_with"]


def test_created_with_pop_does_not_add_spurious_change():
    """A cycle whose only difference is a created_with pop accompanying an
    already-counted sessions deletion must not report `changed` a second
    time via a separate trigger, and a genuinely quiet cycle (no sessions
    change at all) must report changed=False."""
    manifest = {
        "schema": 2,
        "epoch": EPOCH_A,
        "sessions": {"x": {"first_seen_at": 1.0, "last_seen_at": 1.0}},
        "pending_restore": None,
        "created_with": {"x": "amplifier"},
    }
    # Quiet cycle: x still live, nothing changes.
    _new_manifest, changed = update_manifest(manifest, EPOCH_A, ["x"], now=2.0)
    assert changed is False


def test_restore_max_age_is_seven_days():
    assert RESTORE_MAX_AGE_SECONDS == pytest.approx(7 * 86400.0)

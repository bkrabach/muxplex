"""
Integration tests for restore.py's restore-fidelity gate (2026-08-05 fix),
using REAL isolated tmux servers -- no mocking of tmux itself.

Companion to test_restore_integration.py (which proves the happy path: a
correctly-rooted session with no recorded command pair restores fine via
the default). This file proves the two things that path did NOT cover
before this fix:

1. A session whose observed pre-crash cwd diverges from the conventional
   `~/dev/<name>` the default pair assumes gets REFUSED with an actionable
   reason -- never silently restored as the wrong thing. The two fixtures
   below (`attention-manager` rooted at $HOME, `vcos-review` rooted several
   directories below `~/dev/`) are the real sessions from the incident that
   motivated this fix -- see AGENTS.md's "Recovering sessions after they
   are lost" and restore.py's module docstring.
2. THE HARD FLOOR: restore never creates a directory that did not already
   exist -- proven directly by asserting the conventional directory is
   absent from disk both before AND after the refused restore attempt.

Isolation strategy mirrors test_restore_integration.py exactly: a per-test
`tmux_socket_dir` under `tmp_path`, and `restore._default_workspace_root()`
redirected to a directory under `tmp_path` standing in for the real `~/dev`
-- never the real `Path.home()`. Per AGENTS.md's "NEVER broad-kill by
process name" and "Running a second instance on one box" sections: every
tmux call here is scoped to its own TMUX_TMPDIR, and teardown targets only
that socket directory's server.

Run with:
    pytest -m integration -v
Default test run (unit tests only, per AGENTS.md):
    pytest -v
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

import muxplex.manifest as manifest_mod
import muxplex.restore as restore_mod
from muxplex.manifest import load_manifest, save_manifest, update_manifest
from muxplex.sessions import enumerate_sessions, get_session_cwds, probe_tmux_epoch

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers (same shape as test_restore_integration.py)
# ---------------------------------------------------------------------------


def _tmux_env(socket_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["TMUX_TMPDIR"] = str(socket_dir)
    env.pop("TMUX", None)
    return env


def _tmux(
    socket_dir: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=check,
        env=_tmux_env(socket_dir),
    )


def _live_names(socket_dir: Path) -> list[str]:
    result = _tmux(socket_dir, "list-sessions", "-F", "#{session_name}", check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Same wiring as test_restore_integration.py's `isolated` fixture:
    manifest redirected to tmp_path, tmux isolated via the `TMUX_TMPDIR`
    environment variable directly (NOT `settings.tmux_socket_dir`, which is
    a `settings.LOCAL_ONLY_KEYS` member and therefore silently ignored by
    `patch_settings()` -- see that fixture's docstring for the full
    rationale), and restore._default_workspace_root() redirected to
    `tmp_path / "home" / "dev"` -- a directory that stands in for the real
    `~/dev` without ever touching Path.home(). `tmp_path / "home"` (its
    PARENT) stands in for the real `$HOME` in these tests.
    """
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(
        restore_mod, "_default_workspace_root", lambda: tmp_path / "home" / "dev"
    )
    socket_dir = tmp_path / "tmux-socket"
    socket_dir.mkdir()
    monkeypatch.setenv("TMUX_TMPDIR", str(socket_dir))
    monkeypatch.delenv("TMUX", raising=False)
    yield socket_dir
    _tmux(socket_dir, "kill-server", check=False)


def _record_pre_crash_session(socket_dir: Path, name: str, cwd: Path) -> int:
    """Create *name* as a real tmux session rooted at *cwd* (the pane's
    ACTUAL current directory, via `-c`), then run one real poll cycle
    (probe_tmux_epoch + enumerate_sessions + get_session_cwds +
    update_manifest + save) so the manifest genuinely observes and records
    that cwd -- exactly what the production poll loop does every ~2s.
    Returns the server's pid.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    _tmux(socket_dir, "new-session", "-d", "-s", name, "-c", str(cwd))

    async def _poll():
        epoch = await probe_tmux_epoch()
        live = await enumerate_sessions()
        cwds = get_session_cwds()
        manifest = load_manifest()
        manifest, _ = update_manifest(manifest, epoch, live, cwds=cwds)
        save_manifest(manifest)
        return epoch

    epoch = asyncio.run(_poll())
    assert epoch is not None
    # Sanity: the manifest really did capture the observed cwd, so a test
    # failure below is about the FIDELITY CHECK, never about the harness
    # failing to observe cwd in the first place.
    recorded_cwd = manifest_mod.load_manifest()["sessions"][name].get("cwd")
    assert recorded_cwd == str(cwd), (
        f"harness setup failed to observe cwd for {name!r}: got {recorded_cwd!r}"
    )
    return epoch["server_pid"]


def _simulate_cold_start(socket_dir: Path, old_pid: int) -> None:
    """Kill the whole tmux server, bring up a fresh one, run one more poll
    cycle so pending_restore gets populated with the frozen cwd."""
    _tmux(socket_dir, "kill-server", check=False)
    gone = asyncio.run(probe_tmux_epoch())
    assert gone is None, "tmux server must be fully down before the new one starts"

    _tmux(socket_dir, "new-session", "-d", "-s", "bootstrap-only")

    async def _poll():
        epoch = await probe_tmux_epoch()
        live = await enumerate_sessions()
        cwds = get_session_cwds()
        manifest = load_manifest()
        manifest, _ = update_manifest(manifest, epoch, live, cwds=cwds)
        save_manifest(manifest)
        return epoch

    new_epoch = asyncio.run(_poll())
    assert new_epoch is not None
    assert new_epoch["server_pid"] != old_pid

    manifest = load_manifest()
    pending = manifest.get("pending_restore")
    assert pending is not None, "cold start must populate pending_restore"


# ---------------------------------------------------------------------------
# Real-incident regression fixtures
# ---------------------------------------------------------------------------


def test_daemon_rooted_at_home_refuses_restore_and_creates_nothing(isolated, tmp_path):
    """`attention-manager`: a hand-started daemon rooted at $HOME, not
    `~/dev/attention-manager` -- one of the two sessions from the real
    incident. Must be REFUSED, with an actionable reason naming the real
    observed root, and must NEVER create `~/dev/attention-manager`."""
    socket_dir = isolated
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    expected_dir = fake_home / "dev" / "attention-manager"

    old_pid = _record_pre_crash_session(socket_dir, "attention-manager", cwd=fake_home)
    _simulate_cold_start(socket_dir, old_pid)

    assert not expected_dir.exists(), "harness sanity: must not pre-exist"

    plan = asyncio.run(restore_mod.load_plan())
    assert plan is not None
    assert "attention-manager" in plan.names

    report = asyncio.run(restore_mod.execute_restore(["attention-manager"]))

    result = report.results[0]
    assert result.status == "fail"
    assert str(fake_home) in result.detail
    assert "attention-manager" not in set(_live_names(socket_dir)), (
        "must not have started ANY session, right or wrong"
    )
    assert not expected_dir.exists(), (
        "THE HARD FLOOR: restore must never create ~/dev/<name> when it "
        "didn't already exist"
    )

    # Failed restore stays pending for a future retry -- never silently
    # dropped.
    reloaded = load_manifest()
    assert "attention-manager" in (reloaded["pending_restore"] or {}).get(
        "sessions", {}
    )


def test_daemon_rooted_below_dev_refuses_restore_and_creates_nothing(
    isolated, tmp_path
):
    """`vcos-review`: a hand-started daemon rooted at
    `~/dev/better-attention/voice-chief-of-staff`, NOT `~/dev/vcos-review`
    -- the second real session from the incident. Same failure mode as
    attention-manager, different divergence shape (nested under the `dev`
    convention rather than outside it entirely) -- proves the fidelity
    check compares the FULL path, not just "is it under ~/dev somewhere"."""
    socket_dir = isolated
    fake_home = tmp_path / "home"
    real_root = fake_home / "dev" / "better-attention" / "voice-chief-of-staff"
    expected_dir = fake_home / "dev" / "vcos-review"

    old_pid = _record_pre_crash_session(socket_dir, "vcos-review", cwd=real_root)
    _simulate_cold_start(socket_dir, old_pid)

    assert not expected_dir.exists(), "harness sanity: must not pre-exist"

    report = asyncio.run(restore_mod.execute_restore(["vcos-review"]))

    result = report.results[0]
    assert result.status == "fail"
    assert str(real_root) in result.detail
    assert "vcos-review" not in set(_live_names(socket_dir))
    assert not expected_dir.exists(), (
        "THE HARD FLOOR: restore must never create ~/dev/vcos-review when "
        "it didn't already exist"
    )


# ---------------------------------------------------------------------------
# The hard floor, proven directly and generally (not just via the two
# named-incident fixtures above)
# ---------------------------------------------------------------------------


def test_restore_never_creates_a_directory_that_did_not_exist(isolated, tmp_path):
    """General form of the hard floor: a created_with=None session with NO
    manifest history at all (no cwd ever recorded -- e.g. an operator
    running `muxplex restore <name>` for a name with zero prior
    observation) whose conventional directory does not exist must be
    refused, and the directory must remain absent -- regardless of whether
    a cwd mismatch was ever detected."""
    expected_dir = tmp_path / "home" / "dev" / "never-seen-before"
    assert not expected_dir.exists()

    report = asyncio.run(restore_mod.execute_restore(["never-seen-before"]))

    assert report.results[0].status == "fail"
    assert not expected_dir.exists(), (
        "restore must never create the directory as a side effect of "
        "even attempting to spawn the default session command"
    )
    assert report.results[0].detail  # actionable, non-empty


# ---------------------------------------------------------------------------
# The legitimate case still works: correctly-rooted sessions restore fine
# ---------------------------------------------------------------------------


def test_correctly_rooted_session_still_restores_via_default(isolated, tmp_path):
    """A session genuinely rooted at the conventional ~/dev/<name> (cwd
    matches, directory pre-exists) restores via the default pair exactly as
    before this fix -- the fidelity check must never over-refuse the common
    case. See test_restore_integration.py for the broader positive-path
    coverage this complements."""
    socket_dir = isolated
    fake_home = tmp_path / "home"
    expected_dir = fake_home / "dev" / "real-project"

    old_pid = _record_pre_crash_session(socket_dir, "real-project", cwd=expected_dir)
    _simulate_cold_start(socket_dir, old_pid)

    report = asyncio.run(restore_mod.execute_restore(["real-project"]))

    assert not report.any_failed, [
        (r.name, r.detail) for r in report.results if r.status == "fail"
    ]
    assert "real-project" in set(_live_names(socket_dir))

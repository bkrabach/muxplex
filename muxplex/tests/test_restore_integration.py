"""
Integration tests for SESSION_PERSISTENCE_DESIGN.md's "v1b -- explicit
restore" milestone (restore.py + cli.py's cmd_restore), using REAL isolated
tmux servers -- no mocking of tmux itself.

Isolation strategy: each test gets its own `TMUX_TMPDIR`-equivalent socket
directory under `tmp_path`, wired through `settings.tmux_socket_dir` (the
SAME mechanism production muxplex uses -- see sessions.tmux_env()). This is
deliberately NOT the `tmux -L <name>` pattern test_integration_manifest.py
uses for probe_tmux_epoch() in isolation: here we exercise the full,
unmodified production call path (spawn_session_command() ->
asyncio.create_subprocess_shell(..., env=tmux_env()), enumerate_sessions(),
probe_tmux_epoch()) exactly as `muxplex restore` really runs it, with zero
internal monkeypatching of muxplex code. Per AGENTS.md's "NEVER broad-kill by
process name" and "Running a second instance on one box" sections: every tmux
call in this file is explicitly scoped to its own TMUX_TMPDIR and every
teardown targets only that socket directory's server -- never the default
socket, never anything resembling the live host's `~/.tmux`.

The `new_session_template` used here is a SYNTHETIC stand-in for
`amplifier-workspace {name}` that produces the exact same observable shape
(4 windows named amplifier/shell/git/files, cwd set to the workspace
directory) via plain tmux CLI chaining, without amplifier-workspace's own
responsibilities (git submodule init, network access, launching a real
`amplifier`/`amplifier resume` process via os.execvp) -- those belong to
amplifier-workspace's own test suite, not muxplex's. What muxplex is
responsible for proving is that it replays `new_session_template` faithfully
and sequentially, which this template makes directly observable.

Run with:
    pytest -m integration -v
Default test run (unit tests only, per AGENTS.md):
    pytest -v
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

import muxplex.manifest as manifest_mod
import muxplex.restore as restore_mod
import muxplex.settings as settings_mod
from muxplex.manifest import load_manifest, save_manifest, update_manifest
from muxplex.sessions import enumerate_sessions, probe_tmux_epoch

pytestmark = pytest.mark.integration

N_FULL_SCALE = 45  # matches the real incident's scale, not a token count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmux_env(socket_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["TMUX_TMPDIR"] = str(socket_dir)
    env.pop("TMUX", None)
    return env


def _tmux(
    socket_dir: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    """Run `tmux <args>` scoped to *socket_dir* via TMUX_TMPDIR -- the SAME
    isolation mechanism settings.tmux_socket_dir wires production muxplex
    through (sessions.tmux_env()), not the `-L <name>` pattern."""
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


def _window_names(socket_dir: Path, session: str) -> list[str]:
    result = _tmux(
        socket_dir, "list-windows", "-t", session, "-F", "#{window_name}", check=False
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _window_cwd(socket_dir: Path, session: str, window: str = "1") -> str:
    result = _tmux(
        socket_dir,
        "display-message",
        "-p",
        "-t",
        f"{session}:{window}",
        "#{pane_current_path}",
        check=False,
    )
    return result.stdout.strip()


def _fake_workspace_template(workspace_root: Path) -> str:
    """Synthetic stand-in for `amplifier-workspace {name}` -- see module
    docstring. Produces one session with 4 windows (amplifier/shell/git/
    files), each `cd`'d into workspace_root/{name}. Exits 0 without ever
    attempting a TTY attach (unlike the real amplifier-workspace, whose
    exit-nonzero-but-session-exists quirk is pre-existing behavior in
    spawn_session_command(), unchanged by this refactor, and covered by
    test_api.py's create_session tests -- not re-proven here).
    """
    root = str(workspace_root)
    return (
        f"mkdir -p {root}/{{name}} && "
        f"tmux new-session -d -s {{name}} -n amplifier -c {root}/{{name}} \\; "
        f"new-window -t {{name}} -n shell -c {root}/{{name}} \\; "
        f"new-window -t {{name}} -n git -c {root}/{{name}} \\; "
        f"new-window -t {{name}} -n files -c {root}/{{name}}"
    )


def _fake_workspace_template_with_failure(workspace_root: Path, fail_name: str) -> str:
    """Same as _fake_workspace_template(), but exits 1 WITHOUT creating
    anything when the substituted name equals *fail_name* -- used to force a
    real, observable partial failure.

    Wrapped in `bash -c '<script>' _ {name}` (name passed as $1, NOT
    string-substituted into the script text) so the whole conditional lives
    inside one already-quoted argument -- spawn_session_command()'s
    PATH pre-flight check inspects `template.split()[0]` as the base
    command, which must be a real executable (`bash`), not a shell
    keyword like `if`.
    """
    root = str(workspace_root)
    script = (
        f'if [ "$1" = "{fail_name}" ]; then exit 1; else '
        f"mkdir -p {root}/$1 && "
        f'tmux new-session -d -s "$1" -n amplifier -c {root}/$1 \\; '
        f'new-window -t "$1" -n shell -c {root}/$1 \\; '
        f'new-window -t "$1" -n git -c {root}/$1 \\; '
        f'new-window -t "$1" -n files -c {root}/$1; fi'
    )
    return "bash -c " + shlex.quote(script) + " _ {name}"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Wire manifest + settings to per-test isolated paths, and return the
    per-test tmux socket directory. Settings.json is already redirected to
    tmp_path by conftest.py's autouse _isolate_settings_path fixture -- this
    fixture layers on top of that by setting tmux_socket_dir explicitly.
    """
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", tmp_path / "sessions.json")
    socket_dir = tmp_path / "tmux-socket"
    socket_dir.mkdir()
    settings_mod.patch_settings({"tmux_socket_dir": str(socket_dir)})
    yield socket_dir
    _tmux(socket_dir, "kill-server", check=False)


def _record_pre_crash_state(socket_dir: Path, names: list[str]) -> int:
    """Create *names* as bare 1-window sessions (the pre-crash state -- their
    internal shape doesn't matter, only that they exist so the manifest
    records them) on a fresh server under *socket_dir*, then run one
    "poll cycle" (probe_tmux_epoch + update_manifest + save) against the
    REAL production functions. Returns the old server's pid.
    """
    for name in names:
        _tmux(socket_dir, "new-session", "-d", "-s", name)

    async def _poll():
        epoch = await probe_tmux_epoch()
        live = await enumerate_sessions()
        manifest = load_manifest()
        manifest, _ = update_manifest(manifest, epoch, live)
        save_manifest(manifest)
        return epoch

    epoch = asyncio.run(_poll())
    assert epoch is not None
    return epoch["server_pid"]


def _simulate_cold_start(socket_dir: Path, old_pid: int) -> None:
    """Kill the ENTIRE tmux server under *socket_dir* (not a session --
    the whole server, mirroring the 2026-07-29 incident), bring up a fresh
    one, and run one more poll cycle so pending_restore gets populated by
    the REAL update_manifest() discrimination rule."""
    _tmux(socket_dir, "kill-server", check=False)
    gone = asyncio.run(probe_tmux_epoch())
    assert gone is None, "tmux server must be fully down before the new one starts"

    _tmux(socket_dir, "new-session", "-d", "-s", "bootstrap-only")

    async def _poll():
        epoch = await probe_tmux_epoch()
        live = await enumerate_sessions()
        manifest = load_manifest()
        manifest, _ = update_manifest(manifest, epoch, live)
        save_manifest(manifest)
        return epoch

    new_epoch = asyncio.run(_poll())
    assert new_epoch is not None
    assert new_epoch["server_pid"] != old_pid

    manifest = load_manifest()
    pending = manifest.get("pending_restore")
    assert pending is not None, "cold start must populate pending_restore"
    assert pending["lost_epoch"]["server_pid"] == old_pid


# ---------------------------------------------------------------------------
# Demonstration 1: full-scale restore with CORRECT STRUCTURE, not just names
# ---------------------------------------------------------------------------


def test_full_scale_restore_recreates_correct_structure(isolated, tmp_path):
    """Kill a real tmux server holding N_FULL_SCALE sessions, restore them,
    and prove they come back with the RIGHT STRUCTURE: 4 windows named
    amplifier/shell/git/files, with window 1's cwd set to the workspace
    directory -- not just the right names. A one-window bare shell would be
    a failed restore that looks like a success; this test would catch it.
    """
    socket_dir = isolated
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    settings_mod.patch_settings(
        {"new_session_template": _fake_workspace_template(workspace_root)}
    )

    names = [f"proj-{i:02d}" for i in range(N_FULL_SCALE)]
    old_pid = _record_pre_crash_state(socket_dir, names)
    _simulate_cold_start(socket_dir, old_pid)

    plan = asyncio.run(restore_mod.load_plan())
    assert plan is not None
    assert set(plan.names) == set(names)

    t0 = time.monotonic()
    report = asyncio.run(restore_mod.execute_restore(plan.names))
    elapsed = time.monotonic() - t0

    assert report.fail_count == 0, [
        (r.name, r.detail) for r in report.results if r.status == "fail"
    ]
    assert report.ok_count == N_FULL_SCALE

    live_now = set(_live_names(socket_dir))
    assert set(names) <= live_now, "every restored session must actually exist in tmux"

    # The structural proof: 4 windows, correctly named, correct cwd -- for
    # EVERY session, not a sample.
    for name in names:
        windows = _window_names(socket_dir, name)
        assert windows == ["amplifier", "shell", "git", "files"], (
            f"{name}: expected 4 windows (amplifier/shell/git/files), got {windows}"
        )
        cwd = _window_cwd(socket_dir, name, "1")
        expected_cwd = str(workspace_root / name)
        assert cwd == expected_cwd, f"{name}: cwd {cwd!r} != expected {expected_cwd!r}"

    # pending_restore must be fully cleared -- every name succeeded.
    reloaded = load_manifest()
    assert reloaded["pending_restore"] is None

    print(
        f"\n[proof] restored {N_FULL_SCALE} sessions with verified 4-window "
        f"structure in {elapsed:.1f}s"
    )


# ---------------------------------------------------------------------------
# Demonstration 2: tombstones stay dead THROUGH a restore
# ---------------------------------------------------------------------------


def test_tombstoned_session_never_returns_via_restore(isolated, tmp_path):
    """Kill ONE session deliberately while the server is alive (tombstoning
    it), THEN kill the whole server (cold start), then restore. The
    deliberately-killed session must NOT come back -- the others must."""
    socket_dir = isolated
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    settings_mod.patch_settings(
        {"new_session_template": _fake_workspace_template(workspace_root)}
    )

    names = ["keep-a", "keep-b", "killed-on-purpose"]
    old_pid = _record_pre_crash_state(socket_dir, names)

    # Deliberate kill: the server is untouched, only this ONE session dies.
    _tmux(socket_dir, "kill-session", "-t", "killed-on-purpose")

    async def _poll_after_kill():
        epoch = await probe_tmux_epoch()
        live = await enumerate_sessions()
        manifest = load_manifest()
        manifest, _ = update_manifest(manifest, epoch, live)
        save_manifest(manifest)

    asyncio.run(_poll_after_kill())

    manifest = load_manifest()
    assert "killed-on-purpose" not in manifest["sessions"], (
        "deliberate kill must tombstone (remove from the manifest) immediately"
    )
    assert manifest["pending_restore"] is None, (
        "a deliberate kill while the server is alive must never populate "
        "pending_restore on its own"
    )

    # NOW the whole server dies -- the cold start.
    _simulate_cold_start(socket_dir, old_pid)

    manifest = load_manifest()
    pending_names = set((manifest["pending_restore"] or {}).get("sessions", {}))
    assert pending_names == {"keep-a", "keep-b"}, (
        "the tombstoned session must be structurally absent from "
        "pending_restore -- it was removed from manifest['sessions'] before "
        "the cold start could ever freeze it in"
    )

    plan = asyncio.run(restore_mod.load_plan())
    assert plan is not None
    assert set(plan.names) == {"keep-a", "keep-b"}
    assert "killed-on-purpose" not in plan.names

    report = asyncio.run(restore_mod.execute_restore(plan.names))
    assert report.fail_count == 0
    assert report.ok_count == 2

    live_now = set(_live_names(socket_dir))
    assert "keep-a" in live_now
    assert "keep-b" in live_now
    assert "killed-on-purpose" not in live_now, (
        "the deliberately-killed session must NOT be resurrected by restore"
    )


# ---------------------------------------------------------------------------
# Demonstration 3: idempotency -- running restore twice creates nothing new
# ---------------------------------------------------------------------------


def test_restore_twice_is_idempotent(isolated, tmp_path):
    socket_dir = isolated
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    settings_mod.patch_settings(
        {"new_session_template": _fake_workspace_template(workspace_root)}
    )

    names = ["a2a", "bbs", "ccc"]
    old_pid = _record_pre_crash_state(socket_dir, names)
    _simulate_cold_start(socket_dir, old_pid)

    plan1 = asyncio.run(restore_mod.load_plan())
    assert plan1 is not None
    assert set(plan1.names) == set(names)
    report1 = asyncio.run(restore_mod.execute_restore(plan1.names))
    assert report1.ok_count == 3
    assert report1.fail_count == 0

    pids_after_first = {
        name: _tmux(
            socket_dir, "display-message", "-p", "-t", name, "#{session_id}"
        ).stdout.strip()
        for name in names
    }

    # Second run: the plan must be recomputed against LIVE state and be
    # EMPTY -- nothing to do, nothing created.
    plan2 = asyncio.run(restore_mod.load_plan())
    assert plan2 is None or plan2.names == [], (
        f"second restore run must have an empty plan, got {plan2 and plan2.names}"
    )

    reloaded = load_manifest()
    assert reloaded["pending_restore"] is None

    # Sessions are unchanged -- same session_id, not recreated.
    pids_after_second = {
        name: _tmux(
            socket_dir, "display-message", "-p", "-t", name, "#{session_id}"
        ).stdout.strip()
        for name in names
    }
    assert pids_after_first == pids_after_second, (
        "a second restore run must not touch already-live sessions at all"
    )


def test_forget_then_restore_is_a_noop(isolated, tmp_path):
    """--forget must clear pending_restore without creating anything, and a
    subsequent restore must then find nothing pending."""
    socket_dir = isolated
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    settings_mod.patch_settings(
        {"new_session_template": _fake_workspace_template(workspace_root)}
    )

    names = ["forgotten-a", "forgotten-b"]
    old_pid = _record_pre_crash_state(socket_dir, names)
    _simulate_cold_start(socket_dir, old_pid)

    cleared = asyncio.run(restore_mod.forget())
    assert cleared == 2

    reloaded = load_manifest()
    assert reloaded["pending_restore"] is None

    plan = asyncio.run(restore_mod.load_plan())
    assert plan is None

    live_now = set(_live_names(socket_dir))
    assert "forgotten-a" not in live_now
    assert "forgotten-b" not in live_now


# ---------------------------------------------------------------------------
# Demonstration 4: partial failure is LOUD, never silently swallowed
# ---------------------------------------------------------------------------


def test_partial_failure_is_named_and_others_still_succeed(isolated, tmp_path):
    """Force ONE session's creation to fail (real subprocess exit 1, real
    tmux) while the others succeed. The report must name the failure
    explicitly, the others must still be restored, and pending_restore must
    retain ONLY the failed name for a future retry."""
    socket_dir = isolated
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    settings_mod.patch_settings(
        {
            "new_session_template": _fake_workspace_template_with_failure(
                workspace_root, fail_name="bad-one"
            )
        }
    )

    names = ["good-one", "bad-one", "also-good"]
    old_pid = _record_pre_crash_state(socket_dir, names)
    _simulate_cold_start(socket_dir, old_pid)

    plan = asyncio.run(restore_mod.load_plan())
    assert plan is not None
    assert set(plan.names) == set(names)

    report = asyncio.run(restore_mod.execute_restore(plan.names))

    by_name = {r.name: r for r in report.results}
    assert by_name["good-one"].status == "ok"
    assert by_name["also-good"].status == "ok"
    assert by_name["bad-one"].status == "fail"
    assert by_name["bad-one"].detail  # a real, non-empty error message

    assert report.ok_count == 2
    assert report.fail_count == 1
    assert report.any_failed is True

    live_now = set(_live_names(socket_dir))
    assert "good-one" in live_now
    assert "also-good" in live_now
    assert "bad-one" not in live_now

    # The failed name must remain pending for a future retry -- the
    # successes must be cleared.
    reloaded = load_manifest()
    pending_names = set((reloaded["pending_restore"] or {}).get("sessions", {}))
    assert pending_names == {"bad-one"}


def test_cli_partial_failure_exits_nonzero_end_to_end(isolated, tmp_path, capsys):
    """The SAME scenario as above, but driven through cmd_restore() (the
    real CLI entry point) end to end, proving the exit code -- not just the
    report object -- reflects the partial failure."""
    from muxplex.cli import cmd_restore

    socket_dir = isolated
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    settings_mod.patch_settings(
        {
            "new_session_template": _fake_workspace_template_with_failure(
                workspace_root, fail_name="bad-one"
            )
        }
    )

    names = ["good-one", "bad-one"]
    old_pid = _record_pre_crash_state(socket_dir, names)
    _simulate_cold_start(socket_dir, old_pid)

    with pytest.raises(SystemExit) as exc_info:
        cmd_restore(dry_run=False, yes=True)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "good-one" in captured.out
    assert "OK" in captured.out
    assert "bad-one" in captured.out
    assert "FAIL" in captured.out

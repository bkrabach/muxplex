"""
Tests for muxplex/ttyd.py -- per-session ttyd over UNIX domain sockets.

Full rewrite for PER_SESSION_TTYD_SPEC.md §12.2. The old single-ttyd,
TCP-port, PID-file tests are gone; this covers the new socket-path
derivation, directory validation, spawn/kill lifecycle, both reapers, and
the relay-refcounted idle reaper.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import stat
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import muxplex.ttyd as ttyd_mod
from muxplex.ttyd import (
    SOCKET_BASENAME_LEN,
    SUN_PATH_BUDGET,
    TTYD_PORT,
    TtydCapacityError,
    TtydSocketDirError,
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
    validate_socket_dir,
)

# ---------------------------------------------------------------------------
# autouse fixture -- isolate the socket dir and registry for every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def use_tmp_socket_dir(tmp_path, monkeypatch):
    """Redirect the ttyd socket dir to a fresh temp directory and clear the
    in-memory registry before and after every test."""
    tmp_socket_dir = tmp_path / "ttyd"
    monkeypatch.setattr(ttyd_mod, "TTYD_SOCKET_DIR", tmp_socket_dir)
    monkeypatch.setattr(
        ttyd_mod, "LEGACY_TTYD_PID_PATH", tmp_path / "legacy" / "ttyd.pid"
    )
    ttyd_mod._ttyds.clear()
    yield
    ttyd_mod._ttyds.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_stat(
    mode: int = stat.S_IFDIR | 0o700, uid: int | None = None
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        st_mode=mode, st_uid=uid if uid is not None else os.getuid()
    )


def _make_mock_proc(pid: int = 12345, returncode: int | None = None) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.wait = AsyncMock(return_value=0)
    return proc


def _patch_successful_spawn(
    monkeypatch, sock_path: Path, pid: int = 12345
) -> MagicMock:
    """Patch create_subprocess_exec to touch *sock_path* (simulating ttyd's
    bind) and patch socket_is_live() to True -- spawn_ttyd()'s two proof
    gates, satisfied without a real ttyd binary."""
    proc = _make_mock_proc(pid=pid)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        sock_path.touch()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(ttyd_mod, "socket_is_live", lambda path: True)
    return proc


# ---------------------------------------------------------------------------
# socket_path_for()
# ---------------------------------------------------------------------------

_ADVERSARIAL_NAMES = [
    "plain-session",
    "unicode-\u00e9\u00e8\u4e2d\u6587",
    "x" * 200,
    "..",
    "-leading-dash",
    "",
    "with spaces and/slash",
]


def test_socket_path_is_sock_suffixed():
    """Ends in .sock for adversarial names (unicode, 200 chars, '..', leading '-')."""
    for name in _ADVERSARIAL_NAMES:
        path = socket_path_for(name)
        assert path.suffix == ".sock", f"failed for name={name!r}: {path}"


def test_socket_path_within_sun_path_budget():
    """len(str(path)) <= 102 for all adversarial names."""
    for name in _ADVERSARIAL_NAMES:
        path = socket_path_for(name)
        assert len(str(path)) <= SUN_PATH_BUDGET, f"failed for name={name!r}: {path}"


def test_socket_path_is_deterministic_and_distinct():
    """Same name -> same path; 1,000 distinct names -> 1,000 distinct paths."""
    assert socket_path_for("stable-session") == socket_path_for("stable-session")

    names = [f"session-{i}" for i in range(1000)]
    paths = {socket_path_for(n) for n in names}
    assert len(paths) == 1000


def test_socket_path_does_not_contain_session_name():
    """Name never appears in the path (hash, not concat)."""
    name = "a-very-distinctive-session-name-xyz123"
    path = socket_path_for(name)
    assert name not in str(path)


def test_socket_basename_len_matches_components():
    assert SOCKET_BASENAME_LEN == len(
        ttyd_mod.SOCKET_PREFIX
    ) + ttyd_mod.SOCKET_HASH_LEN + len(ttyd_mod.SOCKET_SUFFIX)


# ---------------------------------------------------------------------------
# validate_socket_dir()
# ---------------------------------------------------------------------------


def test_validate_accepts_a_normal_fresh_dir(tmp_path):
    d = tmp_path / "sockdir"
    validate_socket_dir(d)
    assert d.is_dir()
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_validate_rejects_drvfs_under_wsl(monkeypatch):
    """Monkeypatch is_wsl() True + /mnt/c/x, mode already 0o700 (as if a
    metadata-enabled DrvFs mount actually persisted our chmod) ->
    TtydSocketDirError mentioning ENOTSUP. Confirms the DrvFs check still
    fires even when the mode check alone would have passed."""
    directory = Path("/mnt/c/some/deep/path")

    monkeypatch.setattr(Path, "mkdir", lambda self, **kw: None)
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)
    monkeypatch.setattr(Path, "lstat", lambda self: _fake_stat())
    monkeypatch.setattr(Path, "resolve", lambda self: self)
    monkeypatch.setattr(ttyd_mod, "is_wsl", lambda: True)

    with pytest.raises(TtydSocketDirError, match="ENOTSUP"):
        validate_socket_dir(directory)


def test_validate_rejects_drvfs_under_wsl_when_chmod_is_silent_noop(monkeypatch):
    """THE REGRESSION GUARD for the real-host bug: on a real WSL2 host with
    the default (metadata-less) DrvFs mount, chmod() on a /mnt/* path is a
    SILENT NO-OP -- it raises nothing, but the directory's mode never
    actually changes. Verified directly on a real WSL2 host (alienware-r13):
    chmod(0o755) then chmod(0o700) on a fresh /mnt/c directory both "succeed"
    and both leave the mode at 0o777.

    Model exactly that here: chmod() is callable and raises nothing, but
    lstat() always reports 0o777 regardless of what was requested -- the
    directory's own chmod(0o700) call inside validate_socket_dir() has no
    effect, same as on the real host.

    Before the fix, the mode check ran BEFORE the DrvFs check, so this raised
    a "must be 0700" message describing a fix (more chmod) that cannot work
    on this filesystem, and the accurate ENOTSUP/DrvFs diagnosis was
    unreachable dead code. This test fails against that ordering and passes
    against the fix.
    """
    directory = Path("/mnt/c/Users/someone/AppData/Local/muxplex/ttyd")

    monkeypatch.setattr(Path, "mkdir", lambda self, **kw: None)
    # chmod "succeeds" (no exception) but never changes what lstat reports --
    # the exact real-host behavior this regression guard is about.
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)
    monkeypatch.setattr(
        Path, "lstat", lambda self: _fake_stat(mode=stat.S_IFDIR | 0o777)
    )
    monkeypatch.setattr(Path, "resolve", lambda self: self)
    monkeypatch.setattr(ttyd_mod, "is_wsl", lambda: True)

    with pytest.raises(TtydSocketDirError, match="ENOTSUP"):
        validate_socket_dir(directory)


def test_validate_rejects_drvfs_under_wsl_with_synthetic_uid(monkeypatch):
    """A metadata-less DrvFs mount reports a synthetic, mount-wide uid/gid,
    not real per-file ownership -- it need not match ``os.getuid()`` even
    though there is no real ownership mismatch to refuse over. The DrvFs
    check must still fire ahead of the ownership check here too, for the
    same reason it must run ahead of the mode check: neither chmod nor chown
    can fix a directory on this filesystem, so the accurate ENOTSUP/DrvFs
    diagnosis must win over a misleading "wrong owner" message.
    """
    directory = Path("/mnt/c/some/deep/path")

    monkeypatch.setattr(Path, "mkdir", lambda self, **kw: None)
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)
    monkeypatch.setattr(
        Path, "lstat", lambda self: _fake_stat(mode=stat.S_IFDIR | 0o700, uid=1000)
    )
    monkeypatch.setattr(Path, "resolve", lambda self: self)
    monkeypatch.setattr(ttyd_mod, "is_wsl", lambda: True)
    monkeypatch.setattr(os, "getuid", lambda: 1000 + 12345)

    with pytest.raises(TtydSocketDirError, match="ENOTSUP"):
        validate_socket_dir(directory)


def test_validate_rejects_overlong_dir(tmp_path):
    """90+-char dir -> raises, message names MUXPLEX_TTYD_SOCKET_DIR."""
    long_dir = tmp_path / ("x" * 200)
    with pytest.raises(TtydSocketDirError, match="MUXPLEX_TTYD_SOCKET_DIR"):
        validate_socket_dir(long_dir)


def test_validate_rejects_group_writable_dir(tmp_path, monkeypatch):
    """chmod 0o770 -> raises.

    validate_socket_dir() always issues its own chmod(0o700) first (defense
    against a stale/loosened dir); this test simulates a filesystem where
    that chmod call silently doesn't take effect (some NFS/network
    filesystem configurations), so the ONLY way to prove the mode check
    itself is real is to make our own corrective chmod a no-op and confirm
    the pre-existing 0o770 is still caught.
    """
    d = tmp_path / "sockdir"
    d.mkdir()
    d.chmod(0o770)
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)

    with pytest.raises(TtydSocketDirError, match="0700"):
        validate_socket_dir(d)


def test_validate_rejects_foreign_uid_dir(tmp_path, monkeypatch):
    """Monkeypatched st_uid (via os.getuid()) -> raises."""
    d = tmp_path / "sockdir"
    d.mkdir()
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 12345)

    with pytest.raises(TtydSocketDirError, match="uid"):
        validate_socket_dir(d)


def test_validate_rejects_symlinked_dir(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real_dir)

    with pytest.raises(TtydSocketDirError, match="symlink"):
        validate_socket_dir(link)


def test_validate_bind_probe_leaves_no_file(tmp_path):
    """.probe.sock absent afterward."""
    d = tmp_path / "sockdir"
    validate_socket_dir(d)
    assert not (d / ".probe.sock").exists()


def test_validate_no_af_unix_platform(monkeypatch):
    """A platform reporting no AF_UNIX support raises immediately."""
    import socket as socket_mod

    monkeypatch.delattr(socket_mod, "AF_UNIX", raising=False)
    with pytest.raises(TtydSocketDirError, match="AF_UNIX"):
        validate_socket_dir(Path("/tmp/whatever"))


# ---------------------------------------------------------------------------
# spawn_ttyd()
# ---------------------------------------------------------------------------


async def test_spawn_argv_uses_socket_not_port(monkeypatch):
    """argv is exactly ["ttyd","-W","-m","3","-i",<sock>,"tmux","attach","-t",name];
    asserts -p and 127.0.0.1 are absent."""
    sock = socket_path_for("my-session")
    captured: dict = {}

    proc = _make_mock_proc()

    async def _fake_create(*args, **kwargs):
        captured["argv"] = args
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(ttyd_mod, "socket_is_live", lambda path: True)

    await ttyd_mod.spawn_ttyd("my-session")

    argv = list(captured["argv"])
    assert argv == [
        "ttyd",
        "-W",
        "-m",
        "3",
        "-i",
        str(sock),
        "tmux",
        "attach",
        "-t",
        "my-session",
    ]
    assert "-p" not in argv
    assert "127.0.0.1" not in argv


async def test_spawn_preserves_cgroup_escape_wrap(monkeypatch):
    """should_escape() True -> argv is scope-prefixed."""
    sock = socket_path_for("scoped-session")
    captured: dict = {}
    proc = _make_mock_proc()

    async def _fake_create(*args, **kwargs):
        captured["argv"] = args
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(ttyd_mod, "socket_is_live", lambda path: True)
    monkeypatch.setattr(ttyd_mod, "should_escape", AsyncMock(return_value=True))

    await ttyd_mod.spawn_ttyd("scoped-session")

    argv = list(captured["argv"])
    assert argv[:6] == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--same-dir",
    ]
    assert argv[6:] == [
        "--",
        "ttyd",
        "-W",
        "-m",
        "3",
        "-i",
        str(sock),
        "tmux",
        "attach",
        "-t",
        "scoped-session",
    ]


async def test_spawn_preserves_tmux_env(monkeypatch):
    """env=tmux_env() passed through."""
    sock = socket_path_for("env-session")
    captured: dict = {}
    proc = _make_mock_proc()

    async def _fake_create(*args, **kwargs):
        captured["kwargs"] = kwargs
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(ttyd_mod, "socket_is_live", lambda path: True)
    monkeypatch.setattr(
        ttyd_mod,
        "tmux_env",
        lambda: {"TMUX_TMPDIR": "/custom/socket/dir", "PATH": "/usr/bin"},
    )

    await ttyd_mod.spawn_ttyd("env-session")

    assert captured["kwargs"]["env"] == {
        "TMUX_TMPDIR": "/custom/socket/dir",
        "PATH": "/usr/bin",
    }
    assert captured["kwargs"]["start_new_session"] is True


async def test_spawn_unlinks_stale_socket_first(monkeypatch):
    """Pre-existing file at the path is removed before spawn."""
    sock = socket_path_for("stale-session")
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_text("stale leftover content")

    unlinked_before_create = {}

    proc = _make_mock_proc()

    async def _fake_create(*args, **kwargs):
        unlinked_before_create["existed"] = sock.exists()
        sock.touch()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(ttyd_mod, "socket_is_live", lambda path: True)

    await ttyd_mod.spawn_ttyd("stale-session")

    assert unlinked_before_create["existed"] is False


async def test_spawn_raises_when_socket_never_appears(monkeypatch):
    """Fake live process, no file -> TtydSpawnError whose message contains "7681"."""
    proc = _make_mock_proc(returncode=None)

    async def _fake_create(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(ttyd_mod, "SPAWN_READY_TIMEOUT", 0.05)
    monkeypatch.setattr(ttyd_mod, "SPAWN_POLL_INTERVAL", 0.01)

    with pytest.raises(TtydSpawnError, match="7681"):
        await ttyd_mod.spawn_ttyd("never-appears")


async def test_spawn_raises_when_process_exits_early(monkeypatch):
    """returncode=1 -> raises promptly, not after the full 5s."""
    proc = _make_mock_proc(returncode=1)

    async def _fake_create(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(ttyd_mod, "SPAWN_READY_TIMEOUT", 5.0)
    monkeypatch.setattr(ttyd_mod, "SPAWN_POLL_INTERVAL", 0.01)

    import time as time_mod

    start = time_mod.monotonic()
    with pytest.raises(TtydSpawnError, match="exited 1"):
        await ttyd_mod.spawn_ttyd("dies-early")
    assert time_mod.monotonic() - start < 1.0


async def test_spawn_raises_on_registry_collision(monkeypatch):
    """A registered session claiming the same socket path blocks a spawn for
    a different session that would hash-collide onto it."""
    sock = socket_path_for("existing-session")
    fake_proc = _make_mock_proc()
    ttyd_mod._ttyds["existing-session"] = ttyd_mod.TtydProc(
        session="existing-session",
        socket_path=sock,
        run_path=sock.with_suffix(".json"),
        pid=1,
        proc=fake_proc,
    )
    monkeypatch.setattr(ttyd_mod, "socket_path_for", lambda name: sock)

    with pytest.raises(TtydSpawnError, match="collision"):
        await ttyd_mod.spawn_ttyd("colliding-session")


async def test_spawn_writes_run_file(monkeypatch):
    sock = socket_path_for("run-file-session")
    _patch_successful_spawn(monkeypatch, sock, pid=54321)

    proc = await ttyd_mod.spawn_ttyd("run-file-session")

    assert proc.run_path.exists()
    record = json.loads(proc.run_path.read_text(encoding="utf-8"))
    assert record == {"pid": 54321, "session": "run-file-session", "socket": str(sock)}


async def test_spawn_chmods_socket_0600(monkeypatch):
    import stat as stat_mod

    sock = socket_path_for("perm-session")
    _patch_successful_spawn(monkeypatch, sock)

    await ttyd_mod.spawn_ttyd("perm-session")

    assert stat_mod.S_IMODE(sock.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# socket_is_live()
# ---------------------------------------------------------------------------


def test_socket_is_live_false_for_stale_file(tmp_path):
    """Create a plain file at the socket path -> False. Guards §0.2 directly."""
    path = tmp_path / "stale.sock"
    path.write_text("not actually a socket")
    assert socket_is_live(path) is False


def test_socket_is_live_false_for_missing_file(tmp_path):
    assert socket_is_live(tmp_path / "does-not-exist.sock") is False


def test_socket_is_live_true_for_real_listener(tmp_path):
    import socket as socket_mod

    path = tmp_path / "live.sock"
    server = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    try:
        assert socket_is_live(path) is True
    finally:
        server.close()


# ---------------------------------------------------------------------------
# kill_ttyd() / kill_all_ttyd()
# ---------------------------------------------------------------------------


async def test_kill_removes_socket_and_run_file(monkeypatch):
    """Both unlinked even when the process needed SIGKILL."""
    sock = socket_path_for("kill-me")
    _patch_successful_spawn(monkeypatch, sock)
    proc = await ttyd_mod.spawn_ttyd("kill-me")

    # Simulate an unresponsive process: wait_for(proc.wait()) times out.
    proc.proc.wait = AsyncMock(side_effect=TimeoutError)
    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))
    monkeypatch.setattr(ttyd_mod, "TERM_TIMEOUT", 0.05)

    result = await kill_ttyd("kill-me")

    assert result is True
    assert not sock.exists()
    assert not proc.run_path.exists()
    assert "kill-me" not in ttyd_mod._ttyds


async def test_kill_unknown_session_returns_false():
    """No raise."""
    assert await kill_ttyd("never-registered") is False


async def test_kill_all_ttyd_kills_every_registered_session(monkeypatch):
    for name in ("a", "b", "c"):
        sock = socket_path_for(name)
        _patch_successful_spawn(monkeypatch, sock, pid=hash(name) % 50000 + 100)
        await ttyd_mod.spawn_ttyd(name)

    count = await kill_all_ttyd()

    assert count == 3
    assert ttyd_mod._ttyds == {}


# ---------------------------------------------------------------------------
# ensure_ttyd() -- idempotency and capacity
# ---------------------------------------------------------------------------


async def test_ensure_is_idempotent(monkeypatch):
    """Second call with a live socket spawns nothing."""
    sock = socket_path_for("idempotent-session")
    proc = _patch_successful_spawn(monkeypatch, sock)

    first = await ensure_ttyd("idempotent-session")

    spawn_calls = []
    real_spawn = ttyd_mod.spawn_ttyd

    async def _counting_spawn(name):
        spawn_calls.append(name)
        return await real_spawn(name)

    monkeypatch.setattr(ttyd_mod, "spawn_ttyd", _counting_spawn)
    monkeypatch.setattr(ttyd_mod, "socket_is_live", lambda path: True)

    second = await ensure_ttyd("idempotent-session")

    assert second is first
    assert spawn_calls == []
    assert proc.pid == first.pid


async def test_cap_reaps_idle_then_spawns(monkeypatch):
    """MAX_TTYDS=2; third ensure reaps the idle one."""
    monkeypatch.setattr(ttyd_mod, "MAX_TTYDS", 2)

    for name in ("a", "b"):
        sock = socket_path_for(name)
        _patch_successful_spawn(monkeypatch, sock, pid=hash(name) % 50000 + 200)
        await ttyd_mod.spawn_ttyd(name)
    # Both idle (relays == 0, idle_since set at spawn) -- "a" is older.
    ttyd_mod._ttyds["a"].idle_since = 1.0
    ttyd_mod._ttyds["b"].idle_since = 2.0

    sock_c = socket_path_for("c")
    _patch_successful_spawn(monkeypatch, sock_c, pid=999)

    await ensure_ttyd("c")

    assert set(ttyd_mod._ttyds) == {"b", "c"}


async def test_cap_raises_when_all_busy(monkeypatch):
    """MAX_TTYDS=2, both relays=1 -> TtydCapacityError."""
    monkeypatch.setattr(ttyd_mod, "MAX_TTYDS", 2)

    for name in ("a", "b"):
        sock = socket_path_for(name)
        _patch_successful_spawn(monkeypatch, sock, pid=hash(name) % 50000 + 300)
        await ttyd_mod.spawn_ttyd(name)
        acquire_relay(name)

    with pytest.raises(TtydCapacityError):
        await ensure_ttyd("c")


# ---------------------------------------------------------------------------
# Relay accounting and idle reaper
# ---------------------------------------------------------------------------


async def test_idle_reaper_spares_active_relays(monkeypatch):
    """relays=1 and 10x the idle timeout -> survives."""
    monkeypatch.setattr(ttyd_mod, "IDLE_REAP_SECONDS", 0.01)
    sock = socket_path_for("busy-session")
    _patch_successful_spawn(monkeypatch, sock)
    await ttyd_mod.spawn_ttyd("busy-session")
    acquire_relay("busy-session")

    await asyncio.sleep(0.1)  # far past 10x the (shortened) idle timeout

    reaped = await reap_idle_ttyds()

    assert reaped == []
    assert "busy-session" in ttyd_mod._ttyds


async def test_idle_reaper_kills_past_timeout(monkeypatch):
    monkeypatch.setattr(ttyd_mod, "IDLE_REAP_SECONDS", 0.01)
    sock = socket_path_for("idle-session")
    _patch_successful_spawn(monkeypatch, sock)
    await ttyd_mod.spawn_ttyd("idle-session")

    await asyncio.sleep(0.05)

    reaped = await reap_idle_ttyds()

    assert reaped == ["idle-session"]
    assert "idle-session" not in ttyd_mod._ttyds


def test_relay_accessors_are_total_for_unknown_session():
    """acquire/release/count are no-ops/0 for an unknown session -- never a raise."""
    acquire_relay("nope")  # must not raise
    release_relay("nope")  # must not raise
    assert relay_count("nope") == 0


# ---------------------------------------------------------------------------
# reap_orphan_ttyds() -- startup
# ---------------------------------------------------------------------------


def _write_run_file(sock: Path, pid: int, session: str) -> Path:
    sock.parent.mkdir(parents=True, exist_ok=True)
    run_path = sock.with_suffix(".json")
    run_path.write_text(
        json.dumps({"pid": pid, "session": session, "socket": str(sock)}),
        encoding="utf-8",
    )
    return run_path


async def test_orphan_reap_skips_pid_not_matching_ps(monkeypatch):
    """Recycled PID: no signal sent, files removed, warning logged."""
    sock = socket_path_for("recycled")
    run_path = _write_run_file(sock, pid=424242, session="recycled")
    sock.touch()

    monkeypatch.setattr(
        ttyd_mod, "_ps_snapshot", AsyncMock(return_value={424242: "unrelated-proc"})
    )
    kill_calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))

    count = await reap_orphan_ttyds()

    assert count == 0
    assert kill_calls == []
    assert not run_path.exists()
    assert not sock.exists()


async def test_orphan_reap_kills_confirmed_ttyd(monkeypatch):
    """ps shows ttyd ... -i <sock> -> SIGTERM sent."""
    sock = socket_path_for("confirmed")
    run_path = _write_run_file(sock, pid=555, session="confirmed")
    sock.touch()

    monkeypatch.setattr(
        ttyd_mod,
        "_ps_snapshot",
        AsyncMock(
            return_value={555: f"ttyd -W -m 3 -i {sock} tmux attach -t confirmed"}
        ),
    )
    kill_calls = []

    def _mock_kill(pid, sig):
        kill_calls.append((pid, sig))
        if sig != 0:
            raise ProcessLookupError  # simulate prompt exit after SIGTERM

    monkeypatch.setattr(os, "kill", _mock_kill)

    count = await reap_orphan_ttyds()

    assert count == 1
    import signal

    assert (555, signal.SIGTERM) in kill_calls
    assert not run_path.exists()
    assert not sock.exists()


async def test_orphan_reap_removes_socket_without_run_file(monkeypatch):
    """Bare .sock cleaned up."""
    sock = socket_path_for("bare")
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.touch()

    monkeypatch.setattr(ttyd_mod, "_ps_snapshot", AsyncMock(return_value={}))

    await reap_orphan_ttyds()

    assert not sock.exists()


async def test_orphan_reap_handles_malformed_run_file(monkeypatch):
    sock = socket_path_for("malformed")
    sock.parent.mkdir(parents=True, exist_ok=True)
    run_path = sock.with_suffix(".json")
    run_path.write_text("not json", encoding="utf-8")
    sock.touch()

    monkeypatch.setattr(ttyd_mod, "_ps_snapshot", AsyncMock(return_value={}))

    count = await reap_orphan_ttyds()

    assert count == 0
    assert not run_path.exists()
    assert not sock.exists()


# ---------------------------------------------------------------------------
# reap_legacy_ttyd() -- one-time migration
# ---------------------------------------------------------------------------


async def test_legacy_reap_returns_false_when_no_pid_file():
    assert await reap_legacy_ttyd() is False


async def test_legacy_reap_kills_confirmed_legacy_ttyd(monkeypatch):
    ttyd_mod.LEGACY_TTYD_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    ttyd_mod.LEGACY_TTYD_PID_PATH.write_text("777")

    monkeypatch.setattr(
        ttyd_mod,
        "_ps_snapshot",
        AsyncMock(return_value={777: "ttyd -W -m 3 -p 7682 -i 127.0.0.1"}),
    )
    kill_calls = []

    def _mock_kill(pid, sig):
        kill_calls.append((pid, sig))
        if sig != 0:
            raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _mock_kill)
    monkeypatch.setattr(ttyd_mod, "_legacy_port_still_live", lambda: False)

    result = await reap_legacy_ttyd()

    assert result is True
    import signal

    assert (777, signal.SIGTERM) in kill_calls
    assert not ttyd_mod.LEGACY_TTYD_PID_PATH.exists()


async def test_legacy_reap_never_sweeps_port(monkeypatch, caplog):
    """Assert lsof is never invoked and no os.kill on an unconfirmed PID; a
    live 7682 produces an ERROR log."""
    ttyd_mod.LEGACY_TTYD_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    ttyd_mod.LEGACY_TTYD_PID_PATH.write_text("888")

    # PID confirmed dead / not a ttyd -- identity check fails.
    monkeypatch.setattr(ttyd_mod, "_ps_snapshot", AsyncMock(return_value={}))

    def _fail_if_kill_called(pid, sig):
        raise AssertionError(
            f"os.kill must not be called for an unconfirmed PID (pid={pid})"
        )

    monkeypatch.setattr(os, "kill", _fail_if_kill_called)
    monkeypatch.setattr(ttyd_mod, "_legacy_port_still_live", lambda: True)

    with caplog.at_level("ERROR"):
        result = await reap_legacy_ttyd()

    assert result is False
    assert any("127.0.0.1:7682" in rec.message for rec in caplog.records)
    assert "lsof" not in inspect.getsource(ttyd_mod)


async def test_legacy_reap_reports_live_port_with_no_pid_file(monkeypatch, caplog):
    """Regression: a live legacy ttyd on 7682 with NO pid file (crash, manual
    kill, or state-dir wipe lost the recorded pid) must still be reported --
    not silently missed. No identity can be confirmed without a recorded pid,
    so this must never reap (that would be the exact port-based sweep this
    migration deletes); it must always report loudly.
    """
    assert not ttyd_mod.LEGACY_TTYD_PID_PATH.exists()

    def _fail_if_kill_called(pid, sig):
        raise AssertionError(
            f"os.kill must not be called with no recorded pid (pid={pid})"
        )

    monkeypatch.setattr(os, "kill", _fail_if_kill_called)
    monkeypatch.setattr(ttyd_mod, "_legacy_port_still_live", lambda: True)

    with caplog.at_level("ERROR"):
        result = await reap_legacy_ttyd()

    assert result is False
    assert any("127.0.0.1:7682" in rec.message for rec in caplog.records), (
        "no-pid-file + live legacy listener must still produce the "
        "UNAUTHENTICATED WRITABLE TERMINAL report"
    )


def test_no_lsof_anywhere_in_module():
    """Source-level: "lsof" does not appear in ttyd.py. Guards the deleted sweep."""
    source = inspect.getsource(ttyd_mod)
    assert "lsof" not in source


# ---------------------------------------------------------------------------
# The wire contract (§0.3)
# ---------------------------------------------------------------------------


def test_ttyd_port_constant_still_7682():
    """Guards the wire contract against a "remove unused constant" cleanup."""
    assert TTYD_PORT == 7682

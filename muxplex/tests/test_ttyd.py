"""
Tests for coordinator/ttyd.py — ttyd process lifecycle management.
All 11 acceptance-criteria tests are defined here.
"""

import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import muxplex.ttyd as ttyd_mod
from muxplex.ttyd import kill_orphan_ttyd, kill_ttyd, spawn_ttyd


# ---------------------------------------------------------------------------
# autouse fixture — redirect PID paths to tmp_path for every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def use_tmp_pid_dir(tmp_path, monkeypatch):
    """Redirect PID file I/O to a fresh temp directory for every test."""
    tmp_pid_dir = tmp_path / "ttyd"
    tmp_pid_path = tmp_pid_dir / "ttyd.pid"
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_DIR", tmp_pid_dir)
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_PATH", tmp_pid_path)


# ---------------------------------------------------------------------------
# Helper for mocking asyncio.create_subprocess_exec
# ---------------------------------------------------------------------------


def _make_mock_ttyd_process(pid: int = 12345):
    """Return a mock ttyd process with the given PID."""
    proc = MagicMock()
    proc.pid = pid
    return proc


# ---------------------------------------------------------------------------
# spawn_ttyd tests
# ---------------------------------------------------------------------------


async def test_spawn_ttyd_writes_pid_file():
    """spawn_ttyd() must write the process PID to TTYD_PID_PATH."""
    mock_proc = _make_mock_ttyd_process(pid=99999)

    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=mock_proc),
    ):
        await spawn_ttyd("my-session")

    pid_path = ttyd_mod.TTYD_PID_PATH
    assert pid_path.exists(), "PID file was not created"
    assert pid_path.read_text().strip() == "99999"


async def test_spawn_ttyd_uses_correct_command():
    """spawn_ttyd() must call ttyd with args: -W -m 3 -p 7682 tmux attach -t <name>."""
    mock_proc = _make_mock_ttyd_process(pid=54321)

    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=mock_proc),
    ) as mock_create:
        await spawn_ttyd("test-session")

    call_args = mock_create.call_args[0]
    assert list(call_args) == [
        "ttyd",
        "-W",
        "-m",
        "3",
        "-p",
        "7682",
        "tmux",
        "attach",
        "-t",
        "test-session",
    ]


async def test_spawn_ttyd_returns_process_object():
    """spawn_ttyd() must return the process object from create_subprocess_exec."""
    mock_proc = _make_mock_ttyd_process(pid=11111)

    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=mock_proc),
    ):
        result = await spawn_ttyd("another-session")

    assert result is mock_proc


# ---------------------------------------------------------------------------
# kill_ttyd tests
# ---------------------------------------------------------------------------


async def test_kill_ttyd_returns_false_when_no_pid_file():
    """kill_ttyd() returns False when no PID file exists."""
    # autouse fixture ensures no PID file is present
    result = await kill_ttyd()
    assert result is False


async def test_kill_ttyd_reads_pid_file_and_sends_sigterm():
    """kill_ttyd() reads the PID file and sends SIGTERM to the running process."""
    pid_path = ttyd_mod.TTYD_PID_PATH
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("12345")

    kill_calls = []

    def mock_os_kill(pid, sig):
        kill_calls.append((pid, sig))
        # First existence-check (sig=0) succeeds; subsequent sig=0 calls raise
        if sig == 0 and sum(1 for _, s in kill_calls if s == 0) > 1:
            raise ProcessLookupError

    with patch("os.kill", side_effect=mock_os_kill):
        result = await kill_ttyd()

    assert result is True
    sigterm_calls = [(pid, sig) for pid, sig in kill_calls if sig == signal.SIGTERM]
    assert len(sigterm_calls) == 1
    assert sigterm_calls[0][0] == 12345


async def test_kill_ttyd_removes_pid_file():
    """kill_ttyd() removes the PID file regardless of whether process was alive."""
    pid_path = ttyd_mod.TTYD_PID_PATH
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("12345")

    def mock_os_kill(pid, sig):
        # All os.kill calls raise ProcessLookupError — simulates already-dead process
        raise ProcessLookupError

    with patch("os.kill", side_effect=mock_os_kill):
        result = await kill_ttyd()

    assert result is True
    assert not pid_path.exists(), "PID file should be removed after kill_ttyd()"


async def test_kill_ttyd_handles_process_already_dead():
    """kill_ttyd() returns True and clears state when process is already gone."""
    pid_path = ttyd_mod.TTYD_PID_PATH
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("99999")

    # Simulate process already dead: os.kill(pid, 0) raises ProcessLookupError
    with patch("os.kill", side_effect=ProcessLookupError):
        result = await kill_ttyd()

    assert result is True
    assert not pid_path.exists(), (
        "PID file should be removed when process was already dead"
    )
    assert ttyd_mod._active_process is None


# ---------------------------------------------------------------------------
# kill_orphan_ttyd tests
#
# kill_orphan_ttyd() is a thin delegation to kill_ttyd(). These tests verify
# both the delegation wiring and that behaviour is consistent with kill_ttyd().
# ---------------------------------------------------------------------------


async def test_kill_orphan_ttyd_returns_false_when_no_pid_file():
    """kill_orphan_ttyd() returns False when no PID file exists (no orphan)."""
    # autouse fixture ensures no PID file is present
    result = await kill_orphan_ttyd()
    assert result is False


async def test_kill_orphan_ttyd_kills_running_process():
    """kill_orphan_ttyd() kills a running orphan process and returns True."""
    pid_path = ttyd_mod.TTYD_PID_PATH
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("55555")

    kill_calls = []

    def mock_os_kill(pid, sig):
        kill_calls.append((pid, sig))
        # First existence-check (sig=0) succeeds; subsequent sig=0 calls raise
        if sig == 0 and sum(1 for _, s in kill_calls if s == 0) > 1:
            raise ProcessLookupError

    with patch("os.kill", side_effect=mock_os_kill):
        result = await kill_orphan_ttyd()

    assert result is True
    assert not pid_path.exists(), "PID file should be removed after kill_orphan_ttyd()"
    sigterm_calls = [(pid, sig) for pid, sig in kill_calls if sig == signal.SIGTERM]
    assert len(sigterm_calls) == 1
    assert sigterm_calls[0][0] == 55555


async def test_kill_orphan_ttyd_handles_pid_file_with_dead_process():
    """kill_orphan_ttyd() handles a stale PID file whose process is already gone."""
    pid_path = ttyd_mod.TTYD_PID_PATH
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("77777")

    with patch("os.kill", side_effect=ProcessLookupError):
        result = await kill_orphan_ttyd()

    assert result is True
    assert not pid_path.exists(), "PID file should be removed after orphan cleanup"


async def test_kill_ttyd_kills_orphan_on_port_when_pid_file_desynced():
    """kill_ttyd() must also kill orphaned ttyd processes on TTYD_PORT.

    If the PID file points to a dead process (desynced), but the REAL ttyd is
    still running on TTYD_PORT (orphaned from a previous spawn), kill_ttyd()
    must find and kill it via lsof -ti :<port>.  This is the belt-and-suspenders
    fallback that prevents the session-switching bug where a new ttyd cannot bind
    the port because the old one is still running.
    """
    pid_path = ttyd_mod.TTYD_PID_PATH
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    # PID file with a dead process (desynced)
    pid_path.write_text("99999")

    killed_pids: list[tuple[int, int]] = []

    def mock_os_kill(pid: int, sig: int) -> None:
        if pid == 99999 and sig == 0:
            raise ProcessLookupError  # PID file process is already dead
        killed_pids.append((pid, sig))

    mock_lsof_result = MagicMock()
    mock_lsof_result.returncode = 0
    mock_lsof_result.stdout = "12345\n"  # orphan PID occupying TTYD_PORT

    def mock_subprocess_run(cmd, **kwargs):  # noqa: ANN001
        result = MagicMock()
        if "lsof" in cmd and "-ti" in cmd:
            return mock_lsof_result
        result.returncode = 1
        result.stdout = ""
        return result

    with (
        patch("os.kill", side_effect=mock_os_kill),
        patch("muxplex.ttyd._subprocess.run", side_effect=mock_subprocess_run),
    ):
        result = await kill_ttyd()

    assert result is True, "kill_ttyd must return True when orphan found on port"
    orphan_killed = any(
        pid == 12345 and sig == signal.SIGTERM for pid, sig in killed_pids
    )
    assert orphan_killed, (
        "kill_ttyd must send SIGTERM to orphan process (12345) found via "
        "lsof on TTYD_PORT when PID file is desynced"
    )


async def test_spawn_ttyd_force_kills_process_on_port_before_binding():
    """spawn_ttyd() must force-kill any process occupying TTYD_PORT before spawning.

    If kill_ttyd() completed but the port is still occupied (race condition),
    spawn_ttyd() must do a final SIGKILL cleanup so the new ttyd can bind.
    """
    mock_proc = _make_mock_ttyd_process(pid=22222)

    # First lsof call returns an occupant; second call (after kill) returns empty
    call_count = 0

    def mock_subprocess_run(cmd, **kwargs):  # noqa: ANN001
        nonlocal call_count
        result = MagicMock()
        if "lsof" in cmd and "-ti" in cmd:
            call_count += 1
            if call_count == 1:
                result.returncode = 0
                result.stdout = "54321\n"  # port still occupied
                return result
        result.returncode = 1
        result.stdout = ""
        return result

    killed_pids: list[tuple[int, int]] = []

    def mock_os_kill(pid: int, sig: int) -> None:
        killed_pids.append((pid, sig))

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)),
        patch("muxplex.ttyd._subprocess.run", side_effect=mock_subprocess_run),
        patch("os.kill", side_effect=mock_os_kill),
    ):
        await spawn_ttyd("test-session")

    force_killed = any(
        pid == 54321 and sig == signal.SIGKILL for pid, sig in killed_pids
    )
    assert force_killed, (
        "spawn_ttyd must SIGKILL any process occupying TTYD_PORT before spawning "
        "to prevent 'address already in use' errors"
    )


async def test_kill_orphan_ttyd_handles_invalid_pid_file_content():
    """kill_orphan_ttyd() gracefully handles a PID file with non-integer content."""
    pid_path = ttyd_mod.TTYD_PID_PATH
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("not-a-pid")

    # Should not raise and should clean up the file.
    # kill_ttyd() calls pid_path.unlink(missing_ok=True) before returning False
    # on invalid content, so the file is removed even though no kill occurred.
    result = await kill_orphan_ttyd()

    assert result is False
    assert not pid_path.exists(), "Invalid PID file should be removed"

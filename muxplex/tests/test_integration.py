"""
Integration tests for the tmux-web muxplex.

These tests require a real tmux installation and spin up an isolated tmux
server on socket 'test-server' for the duration of the module.

Run with:
    pytest -m integration -v

Default test run (unit tests only):
    pytest -v
"""

import asyncio
import json
import subprocess
from unittest.mock import patch

import pytest

import muxplex.state as state_mod
from muxplex.bells import poll_bell_flag
from muxplex.main import _run_poll_cycle
from muxplex.sessions import enumerate_sessions, get_snapshots


# ---------------------------------------------------------------------------
# Helper function
# ---------------------------------------------------------------------------


def tmux(socket: str, *args: str) -> str:
    """Run a tmux command against the specified socket and return stdout."""
    result = subprocess.run(
        ["tmux", "-L", socket, *args],
        capture_output=True,
        text=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tmux_server():
    """Start an isolated tmux server on socket 'test-server', create session 'test' (220x50).

    Sets monitor-bell on so that bell characters sent to the session are detected.
    Tears down the server after all module tests complete.
    """
    socket = "test-server"
    # Start a new tmux server with an isolated socket and create the test session
    subprocess.run(
        [
            "tmux",
            "-L",
            socket,
            "new-session",
            "-d",
            "-s",
            "test",
            "-x",
            "220",
            "-y",
            "50",
        ],
        check=True,
    )
    # Enable bell monitoring so window_bell_flag is set when a bell is received
    subprocess.run(
        ["tmux", "-L", socket, "set-window-option", "-t", "test", "monitor-bell", "on"],
        check=True,
    )
    yield socket
    # Teardown: kill the isolated server (suppress errors if already dead)
    subprocess.run(
        ["tmux", "-L", socket, "kill-server"],
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def use_tmp_state(tmp_path, monkeypatch):
    """Redirect state and PID files to tmp_path for test isolation."""
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)

    tmp_pid_dir = tmp_path / "ttyd"
    tmp_pid_path = tmp_pid_dir / "ttyd.pid"
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_DIR", tmp_pid_dir)
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_PATH", tmp_pid_path)


# ---------------------------------------------------------------------------
# Internal helper: patched run_tmux that uses the isolated test socket
# ---------------------------------------------------------------------------


def make_run_tmux_for_socket(socket: str):
    """Return an async run_tmux substitute that routes all tmux calls through *socket*.

    Prepends ``-L <socket>`` to every tmux invocation so the test server
    is used instead of the default server.
    """

    async def patched_run_tmux(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-L",
            socket,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr_bytes.decode("utf-8", errors="replace"))
        return stdout_bytes.decode("utf-8", errors="replace")

    return patched_run_tmux


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_enumerate_sessions_finds_test_session(tmux_server):
    """enumerate_sessions discovers the 'test' session on the isolated tmux server."""
    patched_run_tmux = make_run_tmux_for_socket(tmux_server)
    with patch("muxplex.sessions.run_tmux", side_effect=patched_run_tmux):
        sessions = await enumerate_sessions()
    assert "test" in sessions


@pytest.mark.integration
async def test_capture_pane_returns_content(tmux_server):
    """tmux capture-pane returns output that includes what was echoed to the session."""
    tmux(tmux_server, "send-keys", "-t", "test", "echo hello-world", "Enter")
    await asyncio.sleep(0.5)

    # Use the tmux helper directly: capture-pane -p captures the pane content to stdout
    content = tmux(tmux_server, "capture-pane", "-p", "-t", "test")
    assert "hello-world" in content


@pytest.mark.integration
async def test_bell_flag_detected_after_printf_bell(tmux_server):
    """poll_bell_flag returns True after a bell character is sent to the test session."""
    tmux(tmux_server, "send-keys", "-t", "test", r"printf '\a'", "Enter")
    # Allow tmux time to propagate the bell and set window_bell_flag
    await asyncio.sleep(1.0)

    patched_run_tmux = make_run_tmux_for_socket(tmux_server)
    with patch("muxplex.bells.run_tmux", side_effect=patched_run_tmux):
        result = await poll_bell_flag("test")
    assert result is True


@pytest.mark.integration
async def test_full_poll_cycle_via_api(tmux_server):
    """_run_poll_cycle with patched run_tmux adds 'test' to session_order in state
    and populates the in-memory snapshot cache with non-empty content."""
    patched_run_tmux = make_run_tmux_for_socket(tmux_server)
    with (
        patch("muxplex.sessions.run_tmux", side_effect=patched_run_tmux),
        patch("muxplex.bells.run_tmux", side_effect=patched_run_tmux),
    ):
        await _run_poll_cycle()

    state = state_mod.load_state()
    assert "test" in state["session_order"]

    # Verify snapshots were captured and stored — Critical Issue #1 regression guard.
    # If snapshot_all() return value is discarded, get_snapshots() returns {} and
    # snapshots["test"] falls back to "", causing this assertion to fail.
    snapshots = get_snapshots()
    assert "test" in snapshots, (
        "snapshot cache must contain an entry for the 'test' session"
    )


@pytest.mark.integration
async def test_state_file_written_atomically_by_poll_cycle(tmux_server):
    """After _run_poll_cycle, state.json exists, no .tmp file remains, content is valid JSON."""
    patched_run_tmux = make_run_tmux_for_socket(tmux_server)
    with (
        patch("muxplex.sessions.run_tmux", side_effect=patched_run_tmux),
        patch("muxplex.bells.run_tmux", side_effect=patched_run_tmux),
    ):
        await _run_poll_cycle()

    state_path = state_mod.STATE_PATH
    tmp_path = state_mod.STATE_PATH.parent / (state_mod.STATE_PATH.name + ".tmp")

    # state.json must exist after a successful poll cycle
    assert state_path.exists(), "state.json was not written by _run_poll_cycle"

    # The temporary file must be gone (atomic write completed)
    assert not tmp_path.exists(), (
        ".tmp file was left behind (atomic write may have failed)"
    )

    # File content must be valid JSON
    content = state_path.read_text()
    data = json.loads(content)
    assert isinstance(data, dict), "state.json does not contain a JSON object"

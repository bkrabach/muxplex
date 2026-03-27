"""
Tests for coordinator/sessions.py — tmux session enumeration and helpers.
All 6 acceptance-criteria tests are defined here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import coordinator.sessions as sessions_mod
from coordinator.sessions import (
    capture_pane,
    enumerate_sessions,
    get_snapshots,
    get_session_list,
    run_tmux,
    snapshot_all,
    update_session_cache,
)


# ---------------------------------------------------------------------------
# Helpers for mocking asyncio.create_subprocess_exec
# ---------------------------------------------------------------------------


def _make_mock_process(stdout: str, stderr: str = "", returncode: int = 0):
    """Return a mock process whose communicate() returns encoded strings."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    return proc


@pytest.fixture
def mock_subprocess():
    """Fixture factory: returns a context-manager patch for asyncio.create_subprocess_exec.

    Usage::

        with mock_subprocess(stdout="...") as mock_create:
            await some_function()
    """

    def _factory(stdout: str = "", stderr: str = "", returncode: int = 0):
        proc = _make_mock_process(stdout, stderr, returncode)
        return patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc))

    return _factory


# ---------------------------------------------------------------------------
# run_tmux tests
# ---------------------------------------------------------------------------


async def test_run_tmux_calls_correct_command(mock_subprocess):
    """run_tmux('list-sessions', '-F', '#{session_name}') must call tmux
    with exactly those positional arguments via asyncio.create_subprocess_exec."""
    with mock_subprocess("session1\nsession2\n") as mock_create:
        await run_tmux("list-sessions", "-F", "#{session_name}")

    # First positional arg must be 'tmux'; rest must be the args we passed.
    call_args = mock_create.call_args[0]
    assert call_args[0] == "tmux"
    assert call_args[1] == "list-sessions"
    assert call_args[2] == "-F"
    assert call_args[3] == "#{session_name}"


async def test_run_tmux_raises_on_nonzero_exit(mock_subprocess):
    """run_tmux() must raise RuntimeError when the subprocess exits non-zero."""
    with mock_subprocess(
        stdout="", stderr="no server running on /tmp/tmux-1000/default", returncode=1
    ):
        with pytest.raises(RuntimeError, match="no server running"):
            await run_tmux("list-sessions", "-F", "#{session_name}")


# ---------------------------------------------------------------------------
# enumerate_sessions tests
# ---------------------------------------------------------------------------


async def test_enumerate_sessions_parses_newline_output(mock_subprocess):
    """enumerate_sessions() splits newline-separated output into a list of names."""
    with mock_subprocess("alpha\nbeta\ngamma\n"):
        result = await enumerate_sessions()

    assert result == ["alpha", "beta", "gamma"]


async def test_enumerate_sessions_returns_empty_list_when_no_sessions(mock_subprocess):
    """enumerate_sessions() returns [] when tmux output is empty."""
    with mock_subprocess(""):
        result = await enumerate_sessions()

    assert result == []


async def test_enumerate_sessions_strips_whitespace(mock_subprocess):
    """enumerate_sessions() strips leading/trailing whitespace from each name."""
    with mock_subprocess("  session1  \n  session2  \n"):
        result = await enumerate_sessions()

    assert result == ["session1", "session2"]


async def test_enumerate_sessions_handles_tmux_error(mock_subprocess):
    """enumerate_sessions() returns [] when run_tmux raises RuntimeError
    (e.g. tmux server not running)."""
    with mock_subprocess(stdout="", stderr="no server running", returncode=1):
        result = await enumerate_sessions()

    assert result == []


# ---------------------------------------------------------------------------
# capture_pane tests
# ---------------------------------------------------------------------------


async def test_capture_pane_returns_output(mock_subprocess):
    """capture_pane() returns the text output from tmux capture-pane."""
    with mock_subprocess("line1\nline2\nline3\n"):
        result = await capture_pane("my-session")

    assert result == "line1\nline2\nline3\n"


async def test_capture_pane_returns_empty_string_on_error(mock_subprocess):
    """capture_pane() returns '' when tmux exits with an error."""
    with mock_subprocess(
        stdout="", stderr="can't find session my-session", returncode=1
    ):
        result = await capture_pane("my-session")

    assert result == ""


async def test_capture_pane_calls_correct_tmux_args(mock_subprocess):
    """capture_pane() calls tmux with: capture-pane -p -t <name> -S -<lines>.

    Uses -S -N (start N lines from bottom) to limit output.
    Does NOT pass -e (escape sequences) or -l (invalid in tmux 3.4).
    """
    with mock_subprocess("output text\n") as mock_create:
        await capture_pane("target-session", lines=50)

    call_args = mock_create.call_args[0]
    assert call_args[0] == "tmux"
    assert call_args[1] == "capture-pane"
    assert call_args[2] == "-p"
    assert call_args[3] == "-t"
    assert call_args[4] == "target-session"
    assert call_args[5] == "-S"
    assert call_args[6] == "-50"
    assert len(call_args) == 7, "No extra args — -e and -l must not be present"


# ---------------------------------------------------------------------------
# snapshot_all tests
# ---------------------------------------------------------------------------


async def test_snapshot_all_returns_dict_keyed_by_name():
    """snapshot_all() returns a dict mapping each session name to its pane output."""

    async def mock_capture(name, lines=30):
        return f"output-for-{name}"

    with patch("coordinator.sessions.capture_pane", side_effect=mock_capture):
        result = await snapshot_all(["alpha", "beta", "gamma"])

    assert result == {
        "alpha": "output-for-alpha",
        "beta": "output-for-beta",
        "gamma": "output-for-gamma",
    }


async def test_snapshot_all_returns_empty_dict_for_empty_input():
    """snapshot_all([]) returns an empty dict without calling capture_pane."""
    with patch("coordinator.sessions.capture_pane", new=AsyncMock()) as mock_capture:
        result = await snapshot_all([])

    assert result == {}
    mock_capture.assert_not_called()


async def test_snapshot_all_returns_empty_string_on_individual_failure():
    """snapshot_all() maps '' for a failing session while others still succeed."""

    async def mock_capture(name, lines=30):
        if name == "bad-session":
            raise RuntimeError("pane not found")
        return f"output-for-{name}"

    with patch("coordinator.sessions.capture_pane", side_effect=mock_capture):
        result = await snapshot_all(["session-a", "bad-session", "session-b"])

    assert result == {
        "session-a": "output-for-session-a",
        "bad-session": "",
        "session-b": "output-for-session-b",
    }


# ---------------------------------------------------------------------------
# update_session_cache tests
# ---------------------------------------------------------------------------


def test_update_session_cache_populates_snapshots():
    """update_session_cache(names, snapshots) must replace _snapshots with provided dict.

    This is the RED test for Critical Issue #1: previously, update_session_cache
    only accepted names and never received the snapshots dict, so _snapshots
    stayed empty forever.
    """
    # Reset module state to simulate a fresh start
    sessions_mod._snapshots = {}
    sessions_mod._session_list = []

    update_session_cache(
        ["sess1", "sess2"], {"sess1": "line1\nline2", "sess2": "hello"}
    )

    result = get_snapshots()
    assert result == {"sess1": "line1\nline2", "sess2": "hello"}


def test_update_session_cache_updates_session_list():
    """update_session_cache() must also replace _session_list with the given names."""
    sessions_mod._snapshots = {}
    sessions_mod._session_list = ["old-session"]

    update_session_cache(["alpha", "beta"], {"alpha": "a", "beta": "b"})

    assert get_session_list() == ["alpha", "beta"]


def test_update_session_cache_empty_names_clears_caches():
    """update_session_cache([], {}) clears both caches."""
    sessions_mod._snapshots = {"stale": "text"}
    sessions_mod._session_list = ["stale"]

    update_session_cache([], {})

    assert get_session_list() == []
    assert get_snapshots() == {}

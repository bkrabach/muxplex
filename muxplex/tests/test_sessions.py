"""
Tests for coordinator/sessions.py — tmux session enumeration and helpers.
All 6 acceptance-criteria tests are defined here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import muxplex.sessions as sessions_mod
from muxplex.sessions import (
    capture_pane,
    enumerate_sessions,
    get_snapshots,
    get_session_activity,
    get_session_list,
    run_tmux,
    snapshot_all,
    tmux_env,
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
# tmux_env tests
# ---------------------------------------------------------------------------


def test_tmux_env_returns_none_when_socket_dir_unset():
    """tmux_env() returns None (inherit ambient env unchanged) when
    tmux_socket_dir is not configured -- fully backward compatible default."""
    with patch("muxplex.sessions.load_settings", return_value={"tmux_socket_dir": ""}):
        assert tmux_env() is None


def test_tmux_env_overrides_tmux_tmpdir_when_configured():
    """tmux_env() returns a copy of os.environ with TMUX_TMPDIR set to the
    configured tmux_socket_dir.

    Regression: a systemd/launchd service does not inherit the user's login
    shell environment. If the user sets TMUX_TMPDIR in their shell rc to keep
    tmux sockets out of /tmp, the muxplex service process never sees it and
    every real session silently becomes invisible.
    """
    with (
        patch(
            "muxplex.sessions.load_settings",
            return_value={"tmux_socket_dir": "/home/user/.tmux"},
        ),
        patch.dict(
            "os.environ", {"PATH": "/usr/bin", "HOME": "/home/user"}, clear=True
        ),
    ):
        env = tmux_env()

    assert env is not None
    assert env["TMUX_TMPDIR"] == "/home/user/.tmux"
    # Other ambient vars (PATH, HOME) must survive -- this overrides ONE key,
    # it doesn't replace the whole environment.
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/user"


def test_tmux_env_strips_tmux_var_when_configured():
    """tmux_env() removes $TMUX from the returned environment.

    tmux gives $TMUX (set on any process descended from an *attached* tmux
    client) priority over TMUX_TMPDIR when resolving which server to talk
    to. Left in place, a muxplex process that happens to be a descendant of
    some other tmux client would silently ignore tmux_socket_dir and keep
    talking to that unrelated server -- the override would appear to have
    no effect at all.
    """
    with (
        patch(
            "muxplex.sessions.load_settings",
            return_value={"tmux_socket_dir": "/home/user/.tmux"},
        ),
        patch.dict(
            "os.environ",
            {"PATH": "/usr/bin", "TMUX": "/tmp/tmux-1000/default,1234,0"},
            clear=True,
        ),
    ):
        env = tmux_env()

    assert env is not None
    assert "TMUX" not in env


async def test_run_tmux_passes_tmux_env_to_subprocess(mock_subprocess):
    """run_tmux() must pass tmux_env()'s result as the subprocess `env` kwarg."""
    with (
        patch(
            "muxplex.sessions.load_settings",
            return_value={"tmux_socket_dir": "/custom/socket/dir"},
        ),
        mock_subprocess("session1\n") as mock_create,
    ):
        await run_tmux("list-sessions", "-F", "#{session_name}")

    assert mock_create.call_args.kwargs["env"]["TMUX_TMPDIR"] == "/custom/socket/dir"


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


async def test_enumerate_sessions_requests_activity_field(mock_subprocess):
    """enumerate_sessions() must ask tmux for #{window_activity} alongside
    #{session_name} so activity data comes from the same subprocess call
    (no second round trip).

    Deliberately NOT #{session_activity}: verified empirically against a
    real tmux server that session_activity only advances while a client is
    attached, so it stays frozen forever for headless/unwatched sessions --
    exactly the sessions this feature most needs to surface. window_activity
    tracks real pane output unconditionally. See the sessions.py module
    docstring for the full rationale.
    """
    with mock_subprocess("alpha\t1700000000\n") as mock_create:
        await enumerate_sessions()

    call_args = mock_create.call_args[0]
    assert call_args[0] == "tmux"
    assert call_args[1] == "list-sessions"
    assert call_args[2] == "-F"
    assert "#{session_name}" in call_args[3]
    assert "#{window_activity}" in call_args[3]
    assert "#{session_activity}" not in call_args[3]


# ---------------------------------------------------------------------------
# session-activity tests (sourced from tmux's #{window_activity})
# ---------------------------------------------------------------------------


async def test_enumerate_sessions_caches_activity(mock_subprocess):
    """enumerate_sessions() parses the tab-separated activity field and
    caches it, keyed by session name, exposed via get_session_activity()."""
    with mock_subprocess("alpha\t1700000000\nbeta\t1700000050\n"):
        names = await enumerate_sessions()

    assert names == ["alpha", "beta"]
    assert get_session_activity() == {"alpha": 1700000000.0, "beta": 1700000050.0}


async def test_enumerate_sessions_activity_replaced_wholesale(mock_subprocess):
    """A later enumerate_sessions() call fully replaces _activity -- a session
    that has since closed must not linger in get_session_activity()."""
    with mock_subprocess("alpha\t1700000000\nbeta\t1700000050\n"):
        await enumerate_sessions()
    assert "beta" in get_session_activity()

    with mock_subprocess("alpha\t1700000100\n"):
        await enumerate_sessions()

    assert get_session_activity() == {"alpha": 1700000100.0}


async def test_enumerate_sessions_missing_activity_field_is_tolerated(
    mock_subprocess,
):
    """A line with no tab (older tmux output, or a mocked test) must not crash
    -- the session name is still returned, just with no activity entry."""
    with mock_subprocess("alpha\nbeta\n"):
        names = await enumerate_sessions()

    assert names == ["alpha", "beta"]
    assert get_session_activity() == {}


async def test_enumerate_sessions_malformed_activity_value_is_skipped_and_logged(
    mock_subprocess, caplog
):
    """A non-numeric activity field is dropped (not crashed on) and logged --
    the session name itself is still returned."""
    with caplog.at_level("WARNING"):
        with mock_subprocess("alpha\tnot-a-number\nbeta\t1700000050\n"):
            names = await enumerate_sessions()

    assert names == ["alpha", "beta"]
    assert get_session_activity() == {"beta": 1700000050.0}
    assert "alpha" in caplog.text


def test_get_session_activity_returns_copy():
    """get_session_activity() must return a copy -- mutating the result must
    not corrupt the module's internal cache."""
    sessions_mod._activity = {"alpha": 1700000000.0}

    result = get_session_activity()
    result["alpha"] = 0.0
    result["injected"] = 999.0

    assert get_session_activity() == {"alpha": 1700000000.0}


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
    """capture_pane() calls tmux with: capture-pane -e -p -t <name> -S -<lines>.

    Uses -e to preserve ANSI escape sequences for color rendering.
    Uses -S -N (start N lines from bottom) to limit output.
    Does NOT pass -l (invalid in tmux 3.4).
    """
    with mock_subprocess("output text\n") as mock_create:
        await capture_pane("target-session", lines=50)

    call_args = mock_create.call_args[0]
    assert call_args[0] == "tmux"
    assert call_args[1] == "capture-pane"
    assert call_args[2] == "-e"
    assert call_args[3] == "-p"
    assert call_args[4] == "-t"
    assert call_args[5] == "target-session"
    assert call_args[6] == "-S"
    assert call_args[7] == "-50"
    assert len(call_args) == 8, "-e must be present; no other extra args"


# ---------------------------------------------------------------------------
# snapshot_all tests
# ---------------------------------------------------------------------------


async def test_snapshot_all_returns_dict_keyed_by_name():
    """snapshot_all() returns a dict mapping each session name to its pane output."""

    async def mock_capture(name, lines=30):
        return f"output-for-{name}"

    with patch("muxplex.sessions.capture_pane", side_effect=mock_capture):
        result = await snapshot_all(["alpha", "beta", "gamma"])

    assert result == {
        "alpha": "output-for-alpha",
        "beta": "output-for-beta",
        "gamma": "output-for-gamma",
    }


async def test_snapshot_all_returns_empty_dict_for_empty_input():
    """snapshot_all([]) returns an empty dict without calling capture_pane."""
    with patch("muxplex.sessions.capture_pane", new=AsyncMock()) as mock_capture:
        result = await snapshot_all([])

    assert result == {}
    mock_capture.assert_not_called()


async def test_snapshot_all_returns_empty_string_on_individual_failure():
    """snapshot_all() maps '' for a failing session while others still succeed."""

    async def mock_capture(name, lines=30):
        if name == "bad-session":
            raise RuntimeError("pane not found")
        return f"output-for-{name}"

    with patch("muxplex.sessions.capture_pane", side_effect=mock_capture):
        result = await snapshot_all(["session-a", "bad-session", "session-b"])

    assert result == {
        "session-a": "output-for-session-a",
        "bad-session": "",
        "session-b": "output-for-session-b",
    }


# ---------------------------------------------------------------------------
# update_session_cache tests
# ---------------------------------------------------------------------------


def test_capture_pane_uses_escape_flag():
    """capture-pane must include -e for ANSI color preservation."""
    import inspect
    from muxplex.sessions import capture_pane

    source = inspect.getsource(capture_pane)
    assert '"-e"' in source, "capture_pane must pass -e flag to preserve ANSI escapes"


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

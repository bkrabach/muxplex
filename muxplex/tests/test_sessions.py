"""
Tests for coordinator/sessions.py — tmux session enumeration and helpers.
All 6 acceptance-criteria tests are defined here.
"""

import json
import os
import shlex
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import muxplex.sessions as sessions_mod
from muxplex.sessions import (
    DEFAULT_CAPTURE_LINES,
    MAX_CAPTURE_LINES,
    SESSION_HISTORY_LIMIT,
    capture_pane,
    ensure_history_retention,
    enumerate_sessions,
    get_session_activity,
    get_session_list,
    get_snapshots,
    probe_tmux_epoch,
    run_tmux,
    snapshot_all,
    spawn_session_command,
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
    with (
        mock_subprocess(
            stdout="",
            stderr="no server running on /tmp/tmux-1000/default",
            returncode=1,
        ),
        pytest.raises(RuntimeError, match="no server running"),
    ):
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


async def test_capture_pane_default_lines_unchanged(mock_subprocess):
    """capture_pane()'s default depth must still be exactly 30 -- no shape change
    for any existing caller that doesn't pass `lines` explicitly."""
    assert DEFAULT_CAPTURE_LINES == 30

    with mock_subprocess("output\n") as mock_create:
        await capture_pane("target-session")

    call_args = mock_create.call_args[0]
    assert call_args[7] == "-30"


async def test_capture_pane_accepts_deep_line_request(mock_subprocess):
    """capture_pane() must forward a caller-requested deep `lines` value untouched
    (bounds enforcement lives at the API boundary, not here)."""
    with mock_subprocess("output\n") as mock_create:
        await capture_pane("target-session", lines=MAX_CAPTURE_LINES)

    call_args = mock_create.call_args[0]
    assert call_args[7] == f"-{MAX_CAPTURE_LINES}"


# ---------------------------------------------------------------------------
# ensure_history_retention tests
# ---------------------------------------------------------------------------


async def test_ensure_history_retention_calls_tmux_set_option(mock_subprocess):
    """ensure_history_retention() must run `tmux set-option -t <name> history-limit <N>`."""
    with mock_subprocess("") as mock_create:
        await ensure_history_retention("target-session")

    call_args = mock_create.call_args[0]
    assert call_args[0] == "tmux"
    assert call_args[1] == "set-option"
    assert call_args[2] == "-t"
    assert call_args[3] == "target-session"
    assert call_args[4] == "history-limit"
    assert call_args[5] == str(SESSION_HISTORY_LIMIT)


async def test_ensure_history_retention_swallows_tmux_failure(mock_subprocess):
    """A tmux failure (e.g. session vanished) must be logged and swallowed,
    never raised -- this is a best-effort scrollback improvement, not a
    correctness requirement, and must never fail session creation."""
    with mock_subprocess(
        stdout="", stderr="can't find session target-session", returncode=1
    ):
        # Must not raise.
        await ensure_history_retention("target-session")


def test_session_history_limit_exceeds_max_capture_lines():
    """SESSION_HISTORY_LIMIT must stay comfortably above MAX_CAPTURE_LINES --
    otherwise a caller's max-depth request could be silently truncated by
    tmux's own retained scrollback, which would be a worse lie than the
    original fixed 30-line ceiling this whole fix replaces."""
    assert SESSION_HISTORY_LIMIT > MAX_CAPTURE_LINES


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


# ---------------------------------------------------------------------------
# probe_tmux_epoch tests
# ---------------------------------------------------------------------------


async def test_probe_tmux_epoch_returns_none_when_no_server_running():
    """probe_tmux_epoch() returns None on a RuntimeError from run_tmux (tmux's
    'no server running' exit status) -- exit status alone is the signal, no
    parsing of tmux's error text."""
    with patch(
        "muxplex.sessions.run_tmux",
        new=AsyncMock(
            side_effect=RuntimeError("no server running on /tmp/tmux-1000/default")
        ),
    ):
        result = await probe_tmux_epoch()

    assert result is None


async def test_probe_tmux_epoch_returns_none_when_tmux_binary_missing():
    """probe_tmux_epoch() returns None if tmux itself is not installed (FileNotFoundError)."""
    with patch(
        "muxplex.sessions.run_tmux",
        new=AsyncMock(side_effect=FileNotFoundError()),
    ):
        result = await probe_tmux_epoch()

    assert result is None


async def test_probe_tmux_epoch_parses_pid_and_socket_path(tmp_path):
    """probe_tmux_epoch() parses '#{pid}\\t#{socket_path}' and stats the
    socket file for its inode."""
    socket_path = tmp_path / "tmux-1000" / "default"
    socket_path.parent.mkdir(parents=True)
    socket_path.write_text("")  # any file is enough to have an inode

    with patch(
        "muxplex.sessions.run_tmux",
        new=AsyncMock(return_value=f"1527873\t{socket_path}\n"),
    ):
        result = await probe_tmux_epoch()

    assert result is not None
    assert result["server_pid"] == 1527873
    assert result["socket_path"] == str(socket_path)
    assert result["inode"] == socket_path.stat().st_ino


async def test_probe_tmux_epoch_returns_none_when_socket_file_missing(tmp_path):
    """If the socket path tmux reports doesn't exist on disk (a stat race),
    probe_tmux_epoch() returns None rather than raising -- unavailable, not
    refuted."""
    missing_socket = tmp_path / "does-not-exist" / "default"

    with patch(
        "muxplex.sessions.run_tmux",
        new=AsyncMock(return_value=f"12345\t{missing_socket}\n"),
    ):
        result = await probe_tmux_epoch()

    assert result is None


async def test_probe_tmux_epoch_returns_none_on_malformed_output():
    """Malformed tmux output (no tab, non-numeric pid) returns None rather
    than raising."""
    with patch(
        "muxplex.sessions.run_tmux",
        new=AsyncMock(return_value="garbage-with-no-tab\n"),
    ):
        result = await probe_tmux_epoch()
    assert result is None

    with patch(
        "muxplex.sessions.run_tmux",
        new=AsyncMock(return_value="not-a-pid\t/some/socket\n"),
    ):
        result = await probe_tmux_epoch()
    assert result is None


# ---------------------------------------------------------------------------
# spawn_session_command() -- cgroup escape wiring
#
# spawn_session_command() runs `new_session_template` (default
# `tmux new-session -d -s {name}`), which starts a brand-new tmux SERVER if
# none is running yet. When muxplex runs under a systemd --user unit, that
# server must not be spawned as a plain child of this process -- see
# cgroup_escape.py and AGENTS.md's "Two ways to destroy every live tmux
# session on this host" (mechanism #1). These tests verify the two branches
# without ever invoking a real systemd-run.
# ---------------------------------------------------------------------------


async def test_spawn_session_command_uses_plain_shell_when_escape_not_needed():
    """When should_escape() is False (the conftest default), behavior is
    UNCHANGED from before this fix: a plain create_subprocess_shell call.

    ensure_history_retention() is mocked away here because it makes its OWN,
    unrelated create_subprocess_exec call (a `set-option history-limit`, via
    run_tmux) -- deliberately never escaped (see cgroup_escape.py: run_tmux()
    is not a tmux-server-parenting site). Isolating it keeps this test
    focused on the one thing it verifies: how the CREATION command is spawned.
    """
    proc = _make_mock_process(stdout="", stderr="", returncode=0)
    with (
        patch("muxplex.sessions.should_escape", new=AsyncMock(return_value=False)),
        patch(
            "muxplex.sessions.asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=proc),
        ) as mock_shell,
        patch(
            "muxplex.sessions.asyncio.create_subprocess_exec",
            new=AsyncMock(),
        ) as mock_exec,
        patch(
            "muxplex.sessions.load_settings",
            return_value={"new_session_template": "tmux new-session -d -s {name}"},
        ),
        patch("shutil.which", return_value="/usr/bin/tmux"),
        patch("muxplex.sessions.ensure_history_retention", new=AsyncMock()),
    ):
        ok, error = await spawn_session_command("my-session")

    assert ok is True
    assert error is None
    mock_shell.assert_called_once()
    mock_exec.assert_not_called()
    assert mock_shell.call_args[0][0] == "tmux new-session -d -s my-session"


async def test_spawn_session_command_wraps_in_systemd_scope_when_escape_needed():
    """When should_escape() is True, the session-creation command must run
    via `systemd-run --user --scope ... -- sh -c <command>` through
    create_subprocess_exec, NOT the plain create_subprocess_shell.

    ensure_history_retention() is mocked away -- see the sibling test's
    docstring for why.
    """
    proc = _make_mock_process(stdout="", stderr="", returncode=0)
    with (
        patch("muxplex.sessions.should_escape", new=AsyncMock(return_value=True)),
        patch(
            "muxplex.sessions.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ) as mock_exec,
        patch(
            "muxplex.sessions.asyncio.create_subprocess_shell",
            new=AsyncMock(),
        ) as mock_shell,
        patch(
            "muxplex.sessions.load_settings",
            return_value={"new_session_template": "tmux new-session -d -s {name}"},
        ),
        patch("shutil.which", return_value="/usr/bin/tmux"),
        patch("muxplex.sessions.ensure_history_retention", new=AsyncMock()),
    ):
        ok, error = await spawn_session_command("my-session")

    assert ok is True
    assert error is None
    mock_shell.assert_not_called()
    mock_exec.assert_called_once()
    called_argv = list(mock_exec.call_args[0])
    assert called_argv == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--same-dir",
        "--",
        "sh",
        "-c",
        "tmux new-session -d -s my-session",
    ]


async def test_spawn_session_command_escaped_still_honors_tty_attach_recovery():
    """The escaped path must preserve the existing "session exists despite
    non-zero exit" recovery (needed for amplifier-workspace's TTY-attach
    failure under a non-interactive process) -- this is NOT specific to the
    unwrapped path."""
    proc = _make_mock_process(
        stdout="", stderr="attach failed: not a terminal", returncode=1
    )
    with (
        patch("muxplex.sessions.should_escape", new=AsyncMock(return_value=True)),
        patch(
            "muxplex.sessions.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        patch(
            "muxplex.sessions.enumerate_sessions",
            new=AsyncMock(return_value=["my-session"]),
        ),
        patch(
            "muxplex.sessions.load_settings",
            return_value={"new_session_template": "amplifier-workspace {name}"},
        ),
        patch("shutil.which", return_value="/usr/bin/amplifier-workspace"),
        patch("muxplex.sessions.ensure_history_retention", new=AsyncMock()),
    ):
        ok, error = await spawn_session_command("my-session")

    assert ok is True
    assert error is None
    assert get_snapshots() == {}


# ---------------------------------------------------------------------------
# spawn_session_command() -- child isolation from the caller's terminal
#
# Regression coverage for the 2026-07-31 incident: `muxplex restore --yes`
# vanished mid-run after 9 of 12 sessions with no summary and no error. Root
# cause -- sessions.py spawned `new_session_template` with no `stdin=` and
# no `start_new_session=True`, so a template that DOES attach to a tty (e.g.
# a real `amplifier-workspace`) inherited the restore CLI's own controlling
# terminal, session, and foreground process group. A live tmux client took
# over the calling process's terminal instead of failing harmlessly the way
# the pre-existing "TTY-attach failure" recovery path assumes.
#
# These tests use a REAL subprocess (a tiny python3 probe script) rather
# than real tmux -- proving child isolation is a property of
# spawn_session_command()'s own subprocess wiring, independent of whatever
# `new_session_template` happens to do. `should_escape()` is forced False by
# conftest.py's autouse fixture, so these exercise the plain
# `create_subprocess_shell` branch (the one the 2026-07-31 incident hit).
# ---------------------------------------------------------------------------


async def test_spawn_session_command_child_is_not_in_callers_session(tmp_path):
    """The spawned child must be its own session/process-group leader
    (start_new_session=True) -- NOT a member of the calling process's
    session, which is what let a TTY-attaching child steal the caller's
    own terminal during the 2026-07-31 incident."""
    result_file = tmp_path / "child-info.json"
    probe = (
        "import json, os; "
        f"json.dump({{'sid': os.getsid(0), 'pgid': os.getpgrp()}}, "
        f"open({str(result_file)!r}, 'w'))"
    )
    template = "python3 -c " + shlex.quote(probe) + " {name}"

    with (
        patch(
            "muxplex.sessions.load_settings",
            return_value={"new_session_template": template},
        ),
        patch("muxplex.sessions.ensure_history_retention", new=AsyncMock()),
    ):
        ok, error = await spawn_session_command("probe-session")

    assert ok is True, error
    info = json.loads(result_file.read_text())
    assert info["sid"] != os.getsid(0), (
        "child must be its own session leader (start_new_session=True) -- "
        "otherwise it can still control/signal the calling process's "
        "terminal, exactly like the 2026-07-31 incident"
    )


async def test_spawn_session_command_child_stdin_is_devnull_not_callers(tmp_path):
    """The spawned child's stdin must be /dev/null, never inherited from
    the calling process -- otherwise a `tmux attach` (or anything else
    reading from stdin) inside the child succeeds against the CALLER's
    real terminal instead of failing harmlessly."""
    result_file = tmp_path / "child-stdin.json"
    probe = (
        "import json, os; "
        f"json.dump({{'stdin_target': os.readlink('/proc/self/fd/0')}}, "
        f"open({str(result_file)!r}, 'w'))"
    )
    template = "python3 -c " + shlex.quote(probe) + " {name}"

    # Give the CALLING process (standing in for `muxplex restore`'s own
    # foreground shell) a known, uniquely-identifiable stdin so the
    # assertion below proves non-inheritance rather than assuming what
    # pytest's own stdin happens to be in this environment.
    marker_path = tmp_path / "parent-stdin-marker"
    marker_path.write_text("parent stdin marker\n")
    marker_fd = os.open(marker_path, os.O_RDONLY)
    saved_stdin_fd = os.dup(0)
    try:
        os.dup2(marker_fd, 0)
        os.close(marker_fd)

        with (
            patch(
                "muxplex.sessions.load_settings",
                return_value={"new_session_template": template},
            ),
            patch("muxplex.sessions.ensure_history_retention", new=AsyncMock()),
        ):
            ok, error = await spawn_session_command("probe-session-2")
    finally:
        os.dup2(saved_stdin_fd, 0)
        os.close(saved_stdin_fd)

    assert ok is True, error
    info = json.loads(result_file.read_text())
    assert info["stdin_target"] != str(marker_path), (
        "child must not inherit the calling process's stdin"
    )
    assert info["stdin_target"] == "/dev/null", (
        "child's stdin must be /dev/null (the `</dev/null` half of "
        "AGENTS.md's proven-safe recovery invocation)"
    )


async def test_spawn_session_command_kills_child_on_timeout(tmp_path, monkeypatch):
    """A session command that outlives SPAWN_TIMEOUT_SECONDS must have its
    child KILLED before spawn_session_command() returns -- not abandoned.
    Journal evidence from the 2026-07-31 incident showed six spawns spaced
    at exactly 30.0s apart: ten live, abandoned tmux clients had
    accumulated, one per un-killed timeout."""
    monkeypatch.setattr(sessions_mod, "SPAWN_TIMEOUT_SECONDS", 0.2)

    pid_file = tmp_path / "child.pid"
    # A single simple command with no shell operators, run via `sh -c` (what
    # create_subprocess_shell always does): standard shells (dash, bash)
    # replace themselves with it via exec rather than forking a child of
    # their own, so the PID asyncio hands back IS the actual sleeping
    # process's PID -- killing it is not lost to an intermediate shell.
    probe = (
        "import os, time; "
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(100)"
    )
    template = "python3 -c " + shlex.quote(probe) + " {name}"

    with (
        patch(
            "muxplex.sessions.load_settings",
            return_value={"new_session_template": template},
        ),
        patch("muxplex.sessions.ensure_history_retention", new=AsyncMock()),
    ):
        t0 = time.monotonic()
        ok, error = await spawn_session_command("probe-session-3")
        elapsed = time.monotonic() - t0

    assert ok is True, error  # a long-lived template is not itself an error
    assert elapsed < 5, (
        f"took {elapsed:.1f}s -- must have used the monkeypatched short "
        "timeout, not the real 30s default"
    )

    child_pid = int(pid_file.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)

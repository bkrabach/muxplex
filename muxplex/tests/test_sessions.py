"""
Tests for coordinator/sessions.py — tmux session enumeration and helpers.
All 6 acceptance-criteria tests are defined here.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import muxplex.sessions as sessions_mod  # noqa: F401  (import path kept working by S1 re-exports)
import muxplex.tmux.observe as observe_mod
import muxplex.tmux.proc as proc_mod
from muxplex.sessions import (
    DEFAULT_CAPTURE_LINES,
    MAX_CAPTURE_LINES,
    capture_pane,
    capture_pane_metadata,
    capture_pane_window,
    enumerate_sessions,
    get_session_activity,
    get_session_created_times,
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


def test_lib_tmux_env_returns_none_when_socket_dir_empty():
    """The LIBRARY form (S2, plan §4.3): tmux_env(socket_dir) is a pure
    function of its injected parameter -- empty/None means "no override",
    returning None so subprocesses inherit the ambient env unchanged."""
    assert proc_mod.tmux_env("") is None
    assert proc_mod.tmux_env(None) is None


def test_tmux_env_returns_none_when_socket_dir_unset():
    """tmux_env() (the APP facade -- S2 resolves the setting app-side and
    injects it, plan §4.3) returns None (inherit ambient env unchanged) when
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
    # And the LIBRARY form produces the byte-identical env from the same
    # injected value -- the app facade adds nothing but the settings read.
    with patch.dict(
        "os.environ", {"PATH": "/usr/bin", "HOME": "/home/user"}, clear=True
    ):
        assert proc_mod.tmux_env("/home/user/.tmux") == env


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


async def test_run_tmux_resolves_env_from_the_app_installed_factory(mock_subprocess):
    """run_tmux() must resolve the subprocess `env` kwarg from the factory
    the APP installed (S2, plan §4.3: sessions.py registers its
    settings-resolving tmux_env() via set_env_factory at import time).

    This exercises the REAL production wiring end to end: run_tmux ->
    default_env() -> sessions.tmux_env -> load_settings -- proving a
    configured tmux_socket_dir still reaches every library-internal tmux
    call after the inversion.
    """
    assert proc_mod._env_factory is sessions_mod.tmux_env, (
        "muxplex.sessions must install its tmux_env() as the library's env "
        "factory at import time -- without it, a configured tmux_socket_dir "
        "is silently ignored by every tmux call and all sessions vanish."
    )
    with (
        patch(
            "muxplex.sessions.load_settings",
            return_value={"tmux_socket_dir": "/custom/socket/dir"},
        ),
        mock_subprocess("session1\n") as mock_create,
    ):
        await run_tmux("list-sessions", "-F", "#{session_name}")

    assert mock_create.call_args.kwargs["env"]["TMUX_TMPDIR"] == "/custom/socket/dir"


async def test_run_tmux_explicit_env_parameter_wins(mock_subprocess):
    """run_tmux(..., env=...) passes the caller-injected env verbatim,
    bypassing the installed factory -- the per-call injection seam."""
    sentinel = {"TMUX_TMPDIR": "/explicit/dir", "SENTINEL": "1"}
    with mock_subprocess("ok\n") as mock_create:
        await run_tmux("list-sessions", env=sentinel)
    assert mock_create.call_args.kwargs["env"] is sentinel


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
    observe_mod._activity = {"alpha": 1700000000.0}

    result = get_session_activity()
    result["alpha"] = 0.0
    result["injected"] = 999.0

    assert get_session_activity() == {"alpha": 1700000000.0}


# ---------------------------------------------------------------------------
# session-created-time tests (sourced from tmux's #{session_created})
# ---------------------------------------------------------------------------


async def test_enumerate_sessions_requests_session_created_field(mock_subprocess):
    """enumerate_sessions() must ask tmux for #{session_created} alongside
    #{session_name} and #{window_activity} so creation-time data comes from
    the same subprocess call (no second round trip). This is the signal
    main.py's poll cycle uses to distinguish a genuinely new session from one
    merely first observed by this process (see main.py's _server_start_time).
    """
    with mock_subprocess("alpha\t1700000000\t1699999000\n") as mock_create:
        await enumerate_sessions()

    call_args = mock_create.call_args[0]
    assert call_args[0] == "tmux"
    assert call_args[1] == "list-sessions"
    assert call_args[2] == "-F"
    assert "#{session_name}" in call_args[3]
    assert "#{window_activity}" in call_args[3]
    assert "#{session_created}" in call_args[3]


async def test_enumerate_sessions_caches_created_times(mock_subprocess):
    """enumerate_sessions() parses the third tab-separated field and caches
    it, keyed by session name, exposed via get_session_created_times()."""
    with mock_subprocess(
        "alpha\t1700000000\t1699999000\nbeta\t1700000050\t1699999100\n"
    ):
        names = await enumerate_sessions()

    assert names == ["alpha", "beta"]
    assert get_session_created_times() == {
        "alpha": 1699999000.0,
        "beta": 1699999100.0,
    }
    # Activity is still parsed correctly alongside it (no cross-contamination).
    assert get_session_activity() == {"alpha": 1700000000.0, "beta": 1700000050.0}


async def test_enumerate_sessions_created_times_replaced_wholesale(mock_subprocess):
    """A later enumerate_sessions() call fully replaces _created -- a session
    that has since closed must not linger in get_session_created_times()."""
    with mock_subprocess(
        "alpha\t1700000000\t1699999000\nbeta\t1700000050\t1699999100\n"
    ):
        await enumerate_sessions()
    assert "beta" in get_session_created_times()

    with mock_subprocess("alpha\t1700000100\t1699999000\n"):
        await enumerate_sessions()

    assert get_session_created_times() == {"alpha": 1699999000.0}


async def test_enumerate_sessions_missing_created_field_is_tolerated(mock_subprocess):
    """A line with no second tab (older tmux output, or a mocked test still
    using the 2-field format) must not crash -- the session name and
    activity are still returned, just with no created-time entry."""
    with mock_subprocess("alpha\t1700000000\nbeta\n"):
        names = await enumerate_sessions()

    assert names == ["alpha", "beta"]
    assert get_session_activity() == {"alpha": 1700000000.0}
    assert get_session_created_times() == {}


async def test_enumerate_sessions_malformed_created_value_is_skipped_and_logged(
    mock_subprocess, caplog
):
    """A non-numeric session_created field is dropped (not crashed on) and
    logged -- the session name itself is still returned."""
    with caplog.at_level("WARNING"):
        with mock_subprocess(
            "alpha\t1700000000\tnot-a-number\nbeta\t1700000050\t1699999100\n"
        ):
            names = await enumerate_sessions()

    assert names == ["alpha", "beta"]
    assert get_session_created_times() == {"beta": 1699999100.0}
    assert "alpha" in caplog.text


def test_get_session_created_times_returns_copy():
    """get_session_created_times() must return a copy -- mutating the result
    must not corrupt the module's internal cache."""
    observe_mod._created = {"alpha": 1699999000.0}

    result = get_session_created_times()
    result["alpha"] = 0.0
    result["injected"] = 999.0

    assert get_session_created_times() == {"alpha": 1699999000.0}


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
# capture_pane_metadata / capture_pane_window
# (docs/plans/2026-08-07-scrollback-paging-plan.md §2.6/§2.7/§3.4)
# ---------------------------------------------------------------------------


async def test_capture_pane_metadata_parses_h_p_l(mock_subprocess):
    """capture_pane_metadata() parses the tab-separated
    history_size/pane_height/history_limit line and issues exactly ONE
    capture-free display-message call."""
    with mock_subprocess("195\t24\t50000\n") as mock_create:
        h, p, limit = await capture_pane_metadata("target-session")

    assert (h, p, limit) == (195, 24, 50000)
    call_args = mock_create.call_args[0]
    assert call_args == (
        "tmux",
        "display-message",
        "-p",
        "-t",
        "target-session",
        "#{history_size}\t#{pane_height}\t#{history_limit}",
    )


async def test_capture_pane_window_no_before_omits_dash_e(mock_subprocess):
    """The unchanged (`before=None`) path must build `-S -{lines}` with NO
    `-E` at all -- byte-identical to capture_pane()'s pre-paging argv, just
    paired with a leading display-message for the new response fields."""
    with mock_subprocess("100\t24\t50000\nline1\nline2\n") as mock_create:
        h, p, limit, text = await capture_pane_window("target-session", -30, None)

    assert (h, p, limit) == (100, 24, 50000)
    assert text == "line1\nline2\n"
    call_args = mock_create.call_args[0]
    assert call_args == (
        "tmux",
        "display-message",
        "-p",
        "-t",
        "target-session",
        "#{history_size}\t#{pane_height}\t#{history_limit}",
        ";",
        "capture-pane",
        "-e",
        "-p",
        "-t",
        "target-session",
        "-S",
        "-30",
    )


async def test_capture_pane_window_before_includes_dash_e(mock_subprocess):
    """A paged request must build BOTH -S and -E, chained atomically (';'
    is its own argv element) with the metadata read -- the argv shape
    guard against a future refactor splitting the two calls apart
    (plan §3.4's footnote; same style as test_input.py's argv assertion)."""
    with mock_subprocess("500\t24\t50000\nrow-a\nrow-b\n") as mock_create:
        h, p, limit, text = await capture_pane_window("target-session", -105, -100)

    assert (h, p, limit) == (500, 24, 50000)
    assert text == "row-a\nrow-b\n"
    call_args = mock_create.call_args[0]
    assert call_args == (
        "tmux",
        "display-message",
        "-p",
        "-t",
        "target-session",
        "#{history_size}\t#{pane_height}\t#{history_limit}",
        ";",
        "capture-pane",
        "-e",
        "-p",
        "-t",
        "target-session",
        "-S",
        "-105",
        "-E",
        "-100",
    )


# ---------------------------------------------------------------------------
# history-limit: muxplex must not pretend it can raise it post-creation
# (see docs/plans/2026-08-07-agent-surface-additive-plan.md §8)
# ---------------------------------------------------------------------------


def test_muxplex_never_sets_history_limit():
    """history-limit binds a pane at creation; muxplex must not pretend otherwise.

    Runtime-measured on tmux 3.4: `set-option -t <s> history-limit 5000` on a
    live session left the pane at 2000 and evicted at ~2000 after 4000 lines
    of output -- the exact failure the removed code claimed to prevent. See
    docs/plans/2026-08-07-agent-surface-additive-plan.md §1.3.

    Guards against a future "fix" that reintroduces the call, or the rejected
    `set-option -g` variant (see that plan's §8.2 and tmux_config.py's
    install-first-so-we-lose posture).

    Scans sessions.py AND the muxplex/tmux/ library subpackage: the capture
    code this guard was written against moved to muxplex/tmux/observe.py at
    extraction stage S1, and an incident guard that stops scanning the code
    it guards is the exact silent-coverage-loss failure test_safety_rails.py
    rail 1 closes for run-shell.
    """
    package_dir = Path(__file__).parent.parent
    scanned = [
        package_dir / "sessions.py",
        *sorted((package_dir / "tmux").glob("*.py")),
    ]
    for path in scanned:
        source = path.read_text(encoding="utf-8")
        assert "history-limit" not in source, path.name


# ---------------------------------------------------------------------------
# snapshot_all tests
# ---------------------------------------------------------------------------


async def test_snapshot_all_returns_dict_keyed_by_name():
    """snapshot_all() returns a dict mapping each session name to its pane output."""

    async def mock_capture(name, lines=30):
        return f"output-for-{name}"

    with patch("muxplex.tmux.observe.capture_pane", side_effect=mock_capture):
        result = await snapshot_all(["alpha", "beta", "gamma"])

    assert result == {
        "alpha": "output-for-alpha",
        "beta": "output-for-beta",
        "gamma": "output-for-gamma",
    }


async def test_snapshot_all_returns_empty_dict_for_empty_input():
    """snapshot_all([]) returns an empty dict without calling capture_pane."""
    with patch("muxplex.tmux.observe.capture_pane", new=AsyncMock()) as mock_capture:
        result = await snapshot_all([])

    assert result == {}
    mock_capture.assert_not_called()


async def test_snapshot_all_returns_empty_string_on_individual_failure():
    """snapshot_all() maps '' for a failing session while others still succeed."""

    async def mock_capture(name, lines=30):
        if name == "bad-session":
            raise RuntimeError("pane not found")
        return f"output-for-{name}"

    with patch("muxplex.tmux.observe.capture_pane", side_effect=mock_capture):
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
    observe_mod._snapshots = {}
    observe_mod._session_list = []

    update_session_cache(
        ["sess1", "sess2"], {"sess1": "line1\nline2", "sess2": "hello"}
    )

    result = get_snapshots()
    assert result == {"sess1": "line1\nline2", "sess2": "hello"}


def test_update_session_cache_updates_session_list():
    """update_session_cache() must also replace _session_list with the given names."""
    observe_mod._snapshots = {}
    observe_mod._session_list = ["old-session"]

    update_session_cache(["alpha", "beta"], {"alpha": "a", "beta": "b"})

    assert get_session_list() == ["alpha", "beta"]


def test_update_session_cache_empty_names_clears_caches():
    """update_session_cache([], {}) clears both caches."""
    observe_mod._snapshots = {"stale": "text"}
    observe_mod._session_list = ["stale"]

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
        "muxplex.tmux.observe.run_tmux",
        new=AsyncMock(
            side_effect=RuntimeError("no server running on /tmp/tmux-1000/default")
        ),
    ):
        result = await probe_tmux_epoch()

    assert result is None


async def test_probe_tmux_epoch_returns_none_when_tmux_binary_missing():
    """probe_tmux_epoch() returns None if tmux itself is not installed (FileNotFoundError)."""
    with patch(
        "muxplex.tmux.observe.run_tmux",
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
        "muxplex.tmux.observe.run_tmux",
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
        "muxplex.tmux.observe.run_tmux",
        new=AsyncMock(return_value=f"12345\t{missing_socket}\n"),
    ):
        result = await probe_tmux_epoch()

    assert result is None


async def test_probe_tmux_epoch_returns_none_on_malformed_output():
    """Malformed tmux output (no tab, non-numeric pid) returns None rather
    than raising."""
    with patch(
        "muxplex.tmux.observe.run_tmux",
        new=AsyncMock(return_value="garbage-with-no-tab\n"),
    ):
        result = await probe_tmux_epoch()
    assert result is None

    with patch(
        "muxplex.tmux.observe.run_tmux",
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
    UNCHANGED from before this fix: a plain create_subprocess_shell call."""
    proc = _make_mock_process(stdout="", stderr="", returncode=0)
    with (
        patch("muxplex.tmux.spawn.should_escape", new=AsyncMock(return_value=False)),
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=proc),
        ) as mock_shell,
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_exec",
            new=AsyncMock(),
        ) as mock_exec,
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [],
            },
        ),
        patch("shutil.which", return_value="/usr/bin/tmux"),
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
    """
    proc = _make_mock_process(stdout="", stderr="", returncode=0)
    with (
        patch("muxplex.tmux.spawn.should_escape", new=AsyncMock(return_value=True)),
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ) as mock_exec,
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_shell",
            new=AsyncMock(),
        ) as mock_shell,
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [],
            },
        ),
        patch("shutil.which", return_value="/usr/bin/tmux"),
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
        patch("muxplex.tmux.spawn.should_escape", new=AsyncMock(return_value=True)),
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        patch(
            "muxplex.tmux.spawn.enumerate_sessions",
            new=AsyncMock(return_value=["my-session"]),
        ),
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "amplifier-workspace {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [],
            },
        ),
        patch("shutil.which", return_value="/usr/bin/amplifier-workspace"),
    ):
        ok, error = await spawn_session_command("my-session")

    assert ok is True
    assert error is None
    assert get_snapshots() == {}


# ---------------------------------------------------------------------------
# spawn_session_command(command_id=...) -- named session command pairs
# (docs/plans/2026-08-02-named-session-command-pairs-plan.md)
# ---------------------------------------------------------------------------


async def test_spawn_default_when_command_id_none():
    """command_id=None resolves to settings.new_session_template -- the
    byte-identity guard for every existing caller."""
    with (
        patch("muxplex.tmux.spawn.should_escape", new=AsyncMock(return_value=False)),
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=_make_mock_process("", "", 0)),
        ) as mock_shell,
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [],
            },
        ),
        patch("shutil.which", return_value="/usr/bin/tmux"),
    ):
        ok, error = await spawn_session_command("my-session")

    assert ok is True
    assert error is None
    assert mock_shell.call_args[0][0] == "tmux new-session -d -s my-session"


async def test_spawn_uses_named_pair():
    """command_id selects the named pair's new_session_template."""
    with (
        patch("muxplex.tmux.spawn.should_escape", new=AsyncMock(return_value=False)),
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=_make_mock_process("", "", 0)),
        ) as mock_shell,
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [
                    {
                        "id": "amplifier",
                        "label": "Amplifier",
                        "new_session_template": "amplifier-workspace {name}",
                        "delete_session_template": "amplifier-dev --destroy {name}",
                    }
                ],
            },
        ),
        patch("shutil.which", return_value="/usr/bin/amplifier-workspace"),
    ):
        ok, error = await spawn_session_command("my-session", command_id="amplifier")

    assert ok is True
    assert error is None
    assert mock_shell.call_args[0][0] == "amplifier-workspace my-session"


async def test_spawn_unknown_command_id_returns_error_without_spawning():
    with (
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_shell", new=AsyncMock()
        ) as mock_shell,
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_exec", new=AsyncMock()
        ) as mock_exec,
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [],
            },
        ),
    ):
        ok, error = await spawn_session_command("my-session", command_id="typo")

    assert ok is False
    assert error is not None
    assert "typo" in error
    mock_shell.assert_not_called()
    mock_exec.assert_not_called()


async def test_spawn_named_pair_still_honors_tty_attach_recovery():
    """Extends the escaped-path TTY-attach recovery test to a NON-default
    pair -- amplifier-workspace is the exemplar non-default pair AND the
    reason that branch exists."""
    proc = _make_mock_process(
        stdout="", stderr="attach failed: not a terminal", returncode=1
    )
    with (
        patch("muxplex.tmux.spawn.should_escape", new=AsyncMock(return_value=False)),
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=proc),
        ),
        patch(
            "muxplex.tmux.spawn.enumerate_sessions",
            new=AsyncMock(return_value=["my-session"]),
        ),
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [
                    {
                        "id": "amplifier",
                        "label": "Amplifier",
                        "new_session_template": "amplifier-workspace {name}",
                        "delete_session_template": "amplifier-dev --destroy {name}",
                    }
                ],
            },
        ),
        patch("shutil.which", return_value="/usr/bin/amplifier-workspace"),
    ):
        ok, error = await spawn_session_command("my-session", command_id="amplifier")

    assert ok is True
    assert error is None


async def test_spawn_named_pair_still_shlex_quotes_name():
    with (
        patch("muxplex.tmux.spawn.should_escape", new=AsyncMock(return_value=False)),
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=_make_mock_process("", "", 0)),
        ) as mock_shell,
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [
                    {
                        "id": "amplifier",
                        "label": "Amplifier",
                        "new_session_template": "amplifier-workspace {name}",
                        "delete_session_template": "amplifier-dev --destroy {name}",
                    }
                ],
            },
        ),
        patch("shutil.which", return_value="/usr/bin/amplifier-workspace"),
    ):
        await spawn_session_command("my-session", command_id="amplifier")

    # An allowlist-valid name is a shlex.quote() no-op; this asserts the
    # substitution path is exercised for a non-default template too.
    assert mock_shell.call_args[0][0] == "amplifier-workspace my-session"


async def test_spawn_named_pair_respects_cgroup_escape():
    """should_escape() True -> wrap_shell_argv receives the NAMED pair's
    command, not the default (guards the 44-session-incident machinery)."""
    proc = _make_mock_process(stdout="", stderr="", returncode=0)
    with (
        patch("muxplex.tmux.spawn.should_escape", new=AsyncMock(return_value=True)),
        patch(
            "muxplex.tmux.spawn.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ) as mock_exec,
        patch(
            "muxplex.sessions.load_settings",
            return_value={
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": [
                    {
                        "id": "amplifier",
                        "label": "Amplifier",
                        "new_session_template": "amplifier-workspace {name}",
                        "delete_session_template": "amplifier-dev --destroy {name}",
                    }
                ],
            },
        ),
        patch("shutil.which", return_value="/usr/bin/amplifier-workspace"),
    ):
        ok, _error = await spawn_session_command("my-session", command_id="amplifier")

    assert ok is True
    called_argv = list(mock_exec.call_args[0])
    assert called_argv[-1] == "amplifier-workspace my-session"

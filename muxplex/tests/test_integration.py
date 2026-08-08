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
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import muxplex.state as state_mod
import muxplex.ttyd as ttyd_mod
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
def use_tmp_state(tmp_path, short_socket_dir, monkeypatch):
    """Redirect state and PID files to tmp_path for test isolation.

    TTYD_SOCKET_DIR specifically uses short_socket_dir (conftest.py), NOT
    tmp_path: these integration tests spawn a real ttyd bound to a real
    AF_UNIX socket under this directory, and tmp_path is deep enough on
    macOS to blow ttyd's 102-byte sun_path budget (see short_socket_dir's
    docstring, and test_ttyd.py's identical fix for the same fixture shape).
    Currently only reachable via `pytest -m integration`, which the macOS CI
    job does not run -- but the fragility is real the moment it does.
    """
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)

    tmp_socket_dir = short_socket_dir / "ttyd"
    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", tmp_socket_dir)


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


# ---------------------------------------------------------------------------
# history-limit: spawn_session_command() must NOT raise it (deletion proof
# for docs/plans/2026-08-07-agent-surface-additive-plan.md §8). Uses its OWN
# isolated `-L` socket + `-f /dev/null` bootstrap rather than the shared
# `tmux_server` fixture above, so the compiled-in default is guaranteed, not
# merely assumed from an unconfigured environment.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_spawn_session_command_leaves_compiled_default_history_limit():
    """A session created via spawn_session_command() must report tmux's
    compiled-in history_limit (2000), never a raised value -- the assertion
    that would have caught the original ensure_history_retention() bug: it
    called `set-option -t <session> history-limit 5000` AFTER the session
    (and its pane) already existed, which does not change the existing
    pane's retained scrollback. See that plan's §1.3 for the runtime proof
    and §8.5 for this test's origin.

    Plain `def`, not `async def` -- spawn_session_command() is driven via
    `asyncio.run()`, same pattern as `test_two_simultaneous_independent_terminals`
    below, so the tmux subprocess calls in this test body stay synchronous
    (ruff's ASYNC221 flags blocking subprocess calls inside an async test).
    """
    socket = f"pr2-history-{uuid.uuid4().hex[:8]}"
    name = "pr2-history-session"
    # Bootstrap the isolated server with -f /dev/null so no host tmux.conf
    # (which could set history-limit itself) is in play -- `-f` is read only
    # at server START, so later `new-session` calls on this same socket
    # inherit the server-wide default this establishes.
    subprocess.run(
        [
            "tmux",
            "-L",
            socket,
            "-f",
            "/dev/null",
            "new-session",
            "-d",
            "-s",
            "bootstrap",
        ],
        check=True,
    )
    try:
        with (
            patch(
                "muxplex.sessions.load_settings",
                return_value={
                    "new_session_template": f"tmux -L {socket} new-session -d -s {{name}}",
                    "delete_session_template": f"tmux -L {socket} kill-session -t {{name}}",
                    "session_commands": [],
                    "tmux_socket_dir": "",
                },
            ),
            patch("shutil.which", return_value="/usr/bin/tmux"),
        ):
            from muxplex.sessions import spawn_session_command

            ok, error = asyncio.run(spawn_session_command(name))
        assert ok is True
        assert error is None

        result = subprocess.run(
            [
                "tmux",
                "-L",
                socket,
                "display-message",
                "-p",
                "-t",
                name,
                "#{history_limit}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "2000", (
            "spawn_session_command() must leave history_limit at tmux's "
            f"compiled-in default (2000); got {result.stdout.strip()!r} -- "
            "a raised value means a set-option-after-creation call has been "
            "reintroduced (see plan §8.2: delete, do not repair)."
        )
    finally:
        # Socket-scoped teardown only -- never a bare kill-server.
        subprocess.run(
            ["tmux", "-L", socket, "kill-server"], capture_output=True, check=False
        )


# ---------------------------------------------------------------------------
# §12.5 -- real tmux, real ttyd, real sockets, through the real ASGI app.
#
# This is the acceptance test for the whole per-session-ttyd architecture
# (docs/plans/2026-08-02-per-session-ttyd-plan.md §12.5, §14.1). It must run against real binaries
# because the failure this change fixes -- one device's connect tearing down
# another device's live terminal -- is a process-lifecycle failure that no
# mock can reproduce.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_tmux_env(tmp_path, monkeypatch):
    """A real, isolated tmux server this test owns exclusively.

    Isolated via TMUX_TMPDIR (never the default socket) so this test can
    never see or touch the developer's/host's real tmux server -- see
    AGENTS.md's "NEVER broad-kill by process name" and the tmux-isolation
    pattern already used by scripts/spike_ttyd_harness.py.
    """
    tmux_tmpdir = tmp_path / "tmux-isolated"
    tmux_tmpdir.mkdir()
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_tmpdir))
    monkeypatch.delenv("TMUX", raising=False)

    label = f"mxtest-{uuid.uuid4().hex[:8]}"
    session_a = f"{label}-a"
    session_b = f"{label}-b"

    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_a, "-x", "80", "-y", "24"],
        check=True,
        env={**__import__("os").environ, "TMUX_TMPDIR": str(tmux_tmpdir)},
    )
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_b, "-x", "80", "-y", "24"],
        check=True,
        env={**__import__("os").environ, "TMUX_TMPDIR": str(tmux_tmpdir)},
    )

    yield session_a, session_b

    # Teardown: kill only this isolated server -- never a bare kill-server,
    # never process-name matching.
    subprocess.run(
        ["tmux", "kill-server"],
        env={**__import__("os").environ, "TMUX_TMPDIR": str(tmux_tmpdir)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


@pytest.fixture
def real_ttyd_app(tmp_path, short_socket_dir, monkeypatch):
    """Real app wiring for the §12.5 proof: real state dir, real ttyd socket
    dir (real validate_socket_dir(), real spawn/kill), poll loop replaced
    with a no-op so the test drives session discovery deterministically via
    one explicit _run_poll_cycle() call instead of racing a background timer.

    TTYD_SOCKET_DIR uses short_socket_dir (conftest.py), NOT tmp_path --
    validate_socket_dir()'s real bind probe and the real ttyd spawn both
    perform a real AF_UNIX bind, and tmp_path is deep enough on macOS to
    blow the 102-byte sun_path budget (same fragility test_ttyd.py's
    identical fixture had; see short_socket_dir's docstring).
    """
    tmp_state_dir = tmp_path / "state"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_dir / "state.json")

    tmp_socket_dir = short_socket_dir / "ttyd-sockets"
    tmp_socket_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ttyd_mod, "TTYD_SOCKET_DIR", tmp_socket_dir)

    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)

    yield

    # Best-effort cleanup of any ttyd this test spawned, by exact PID only.
    asyncio.run(ttyd_mod.kill_all_ttyd())


def _authed_client(app) -> TestClient:
    """A TestClient with a valid session cookie.

    TestClient's default fake host ("testclient") is not in AuthMiddleware's
    localhost bypass list, so unauthenticated requests are redirected to the
    login page instead of reaching the API -- see test_ws_proxy.py's
    _make_authed_client() for the identical WS-side pattern.
    """
    from muxplex.auth import create_session_cookie
    from muxplex.main import _auth_secret, _auth_ttl

    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    client = TestClient(app)
    client.cookies.set("muxplex_session", cookie)
    return client


def _drain_ws(ws, marker: str, timeout: float = 5.0) -> str:
    """Drain text/binary frames from a TestClient WebSocket for up to
    *timeout* seconds, or until *marker* has been seen -- whichever first.
    Returns the concatenated decoded output.

    Starlette's WebSocketTestSession.receive() has no timeout parameter and
    blocks until a message arrives, so each call is bounded from the
    OUTSIDE via a background thread + queue rather than an in-call timeout.
    """
    import queue
    import threading

    q: queue.Queue = queue.Queue()
    stop = threading.Event()

    def _reader() -> None:
        while not stop.is_set():
            try:
                q.put(ws.receive())
            except Exception:
                return

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()

    collected = ""
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = q.get(timeout=remaining)
            except queue.Empty:
                break
            data = msg.get("bytes") if isinstance(msg, dict) else None
            if data is None and isinstance(msg, dict):
                data = msg.get("text")
            if data is None:
                if isinstance(msg, dict) and msg.get("type") == "websocket.disconnect":
                    break
                continue
            if isinstance(data, bytes):
                # ttyd wire format: [0] msg type, [1:] utf-8 payload.
                if len(data) >= 1 and data[0:1] == b"0":
                    collected += data[1:].decode("utf-8", errors="replace")
            elif isinstance(data, str):
                collected += data
            if marker in collected:
                break
    finally:
        stop.set()
    return collected


@pytest.mark.integration
def test_two_simultaneous_independent_terminals(isolated_tmux_env, real_ttyd_app):
    """The acceptance test for this entire change (docs/plans/2026-08-02-per-session-ttyd-plan.md
    §12.5, §14.1): two devices attach to two DIFFERENT sessions simultaneously
    through the real, authenticated ASGI app, each gets its own ttyd on its
    own UNIX socket, neither's keystrokes cross into the other's session, and
    closing one does not disturb the other. This is the exact failure mode of
    today's (pre-change) single-shared-ttyd architecture.
    """
    import muxplex.main as main_mod

    session_a, session_b = isolated_tmux_env

    # Populate the session cache the way production does: run one real poll
    # cycle against the real (isolated) tmux server before issuing any HTTP
    # calls -- get_session_list()/connect_session() read this cache, not
    # tmux directly (AGENT_GUIDE.md's "read model is eventually consistent").
    asyncio.run(main_mod._run_poll_cycle())
    known = main_mod.get_session_list()
    assert session_a in known
    assert session_b in known

    with _authed_client(main_mod.app) as c:
        # 3. Connect two different devices to two different sessions. Both
        # devices register via heartbeat first (required before /connect can
        # resolve device_id -> group), each in its OWN sync group so neither
        # contends with the other's selection -- assert both succeed, no 409.
        c.post(
            "/api/heartbeat",
            json={
                "device_id": "devA",
                "label": "A",
                "viewing_session": None,
                "view_mode": "grid",
                "last_interaction_at": 0.0,
                "sync_group": "device:devA",
            },
        )
        c.post(
            "/api/heartbeat",
            json={
                "device_id": "devB",
                "label": "B",
                "viewing_session": None,
                "view_mode": "grid",
                "last_interaction_at": 0.0,
                "sync_group": "device:devB",
            },
        )
        r_a = c.post(f"/api/sessions/{session_a}/connect?device_id=devA")
        r_b = c.post(f"/api/sessions/{session_b}/connect?device_id=devB")
        assert r_a.status_code == 200, r_a.text
        assert r_b.status_code == 200, r_b.text

        # 4. Both socket files exist, both socket_is_live, and the two paths
        # differ.
        sock_a = ttyd_mod.socket_path_for(session_a)
        sock_b = ttyd_mod.socket_path_for(session_b)
        assert sock_a != sock_b
        assert sock_a.exists() and sock_b.exists()
        assert ttyd_mod.socket_is_live(sock_a)
        assert ttyd_mod.socket_is_live(sock_b)

        # 5-7. Open BOTH WebSockets concurrently, each with its own ?session=
        # and device_id. On each: AuthToken handshake, resize, then type a
        # session-unique marker. Assert A's output contains A's marker and
        # NEVER B's, and vice versa.
        marker_a = f"MARKER-A-{uuid.uuid4().hex[:8]}"
        marker_b = f"MARKER-B-{uuid.uuid4().hex[:8]}"

        with (
            c.websocket_connect(
                f"/terminal/ws?session={session_a}&device_id=devA"
            ) as ws_a,
            c.websocket_connect(
                f"/terminal/ws?session={session_b}&device_id=devB"
            ) as ws_b,
        ):
            for ws in (ws_a, ws_b):
                ws.send_text(json.dumps({"AuthToken": ""}))
                ws.send_bytes(
                    b"1" + json.dumps({"columns": 80, "rows": 24}).encode("utf-8")
                )

            ws_a.send_bytes(b"0" + f"echo {marker_a}\r".encode())
            ws_b.send_bytes(b"0" + f"echo {marker_b}\r".encode())

            out_a = _drain_ws(ws_a, marker_a, timeout=5.0)
            out_b = _drain_ws(ws_b, marker_b, timeout=5.0)

        assert marker_a in out_a, (
            f"A's own marker never appeared in A's output: {out_a!r}"
        )
        assert marker_b not in out_a, (
            f"B's marker leaked into A's terminal output -- cross-session contamination: {out_a!r}"
        )
        assert marker_b in out_b, (
            f"B's own marker never appeared in B's output: {out_b!r}"
        )
        assert marker_a not in out_b, (
            f"A's marker leaked into B's terminal output -- cross-session contamination: {out_b!r}"
        )

        # 8. Close A. Assert B's socket is still live and B still echoes --
        # A's teardown does not disturb B. This is the exact failure mode of
        # today's architecture (one ttyd, shared; killing it for A kills it
        # for B too).
        c.delete("/api/sessions/current?device_id=devA")

        assert ttyd_mod.socket_is_live(sock_b), (
            "B's ttyd must survive A's disconnect/delete -- independent per-session ttyds"
        )

        with c.websocket_connect(
            f"/terminal/ws?session={session_b}&device_id=devB"
        ) as ws_b2:
            ws_b2.send_text(json.dumps({"AuthToken": ""}))
            ws_b2.send_bytes(
                b"1" + json.dumps({"columns": 80, "rows": 24}).encode("utf-8")
            )
            marker_b2 = f"MARKER-B2-{uuid.uuid4().hex[:8]}"
            ws_b2.send_bytes(b"0" + f"echo {marker_b2}\r".encode())
            out_b2 = _drain_ws(ws_b2, marker_b2, timeout=5.0)

        assert marker_b2 in out_b2, (
            f"B's session must still be alive and echoing after A's teardown: {out_b2!r}"
        )


@pytest.mark.integration
def test_ttyd_survives_second_connect_to_same_session(isolated_tmux_env, real_ttyd_app):
    """Same-session reconnect does not churn the PTY (replaces the deleted
    same-session short-circuit's guarantee: ensure_ttyd() is idempotent)."""
    import muxplex.main as main_mod

    session_a, _session_b = isolated_tmux_env
    asyncio.run(main_mod._run_poll_cycle())

    with _authed_client(main_mod.app) as c:
        r1 = c.post(f"/api/sessions/{session_a}/connect")
        assert r1.status_code == 200
        proc1 = ttyd_mod._ttyds[session_a]
        pid1 = proc1.pid

        r2 = c.post(f"/api/sessions/{session_a}/connect")
        assert r2.status_code == 200
        proc2 = ttyd_mod._ttyds[session_a]

        assert proc2.pid == pid1, (
            "reconnecting to the same session must not respawn its ttyd"
        )


@pytest.mark.integration
def test_orphan_reap_kills_real_ttyd_across_restart(isolated_tmux_env, real_ttyd_app):
    """Spawn a real ttyd, drop the registry (simulating a restart), then
    reap_orphan_ttyds() -> process gone, socket gone."""
    session_a, _session_b = isolated_tmux_env

    async def _run():
        proc = await ttyd_mod.spawn_ttyd(session_a)
        sock = proc.socket_path
        pid = proc.pid
        # Simulate a restart: the in-memory registry is gone, but the run
        # file and process survive.
        ttyd_mod._ttyds.clear()
        count = await ttyd_mod.reap_orphan_ttyds()
        return sock, pid, count

    sock, pid, count = asyncio.run(_run())

    assert count == 1
    assert not sock.exists()
    with pytest.raises(ProcessLookupError):
        import os

        os.kill(pid, 0)


@pytest.mark.integration
def test_idle_reaper_kills_real_idle_ttyd(
    isolated_tmux_env, real_ttyd_app, monkeypatch
):
    """IDLE_REAP_SECONDS=0.5, no relay -> reaped; socket gone; tmux session still alive."""
    monkeypatch.setattr(ttyd_mod, "IDLE_REAP_SECONDS", 0.2)
    session_a, _session_b = isolated_tmux_env

    async def _run():
        proc = await ttyd_mod.spawn_ttyd(session_a)
        sock = proc.socket_path
        await asyncio.sleep(0.4)
        reaped = await ttyd_mod.reap_idle_ttyds()
        return sock, reaped

    sock, reaped = asyncio.run(_run())

    assert reaped == [session_a]
    assert not sock.exists()

    # tmux session itself must still be alive -- killing a ttyd is killing a
    # VIEW, never the durable tmux session it was attached to.
    import os

    result = subprocess.run(
        ["tmux", "has-session", "-t", session_a],
        env={**os.environ},
        check=False,
    )
    assert result.returncode == 0, "the tmux session must survive its ttyd being reaped"


@pytest.mark.integration
def test_sigterm_removes_socket_file(isolated_tmux_env, real_ttyd_app):
    """Confirms the spike finding against the shipped spawn path: a clean
    SIGTERM removes the socket file."""
    session_a, _session_b = isolated_tmux_env

    async def _run():
        proc = await ttyd_mod.spawn_ttyd(session_a)
        sock = proc.socket_path
        assert sock.exists()
        result = await ttyd_mod.kill_ttyd(session_a)
        return sock, result

    sock, result = asyncio.run(_run())

    assert result is True
    assert not sock.exists()


@pytest.mark.integration
def test_stale_socket_does_not_block_rebind(isolated_tmux_env, real_ttyd_app):
    """Leave a stale .sock, spawn -> succeeds."""
    session_a, _session_b = isolated_tmux_env

    sock = ttyd_mod.socket_path_for(session_a)
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_text("stale leftover, not a real socket")

    proc = asyncio.run(ttyd_mod.spawn_ttyd(session_a))

    assert proc.socket_path == sock
    assert ttyd_mod.socket_is_live(sock)

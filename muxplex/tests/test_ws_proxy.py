"""
Comprehensive tests for the WebSocket proxy in muxplex/main.py.
"""

import asyncio
import inspect
import threading
import time
import types
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from muxplex.auth import create_session_cookie
from muxplex.main import app, terminal_ws_proxy


# ---------------------------------------------------------------------------
# Polling helper — deterministic alternative to time.sleep() for async relay
# ---------------------------------------------------------------------------


def _wait_for(condition, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """Poll *condition()* until it returns True or *timeout* seconds elapses.

    Returns True if the condition was met, False on timeout.
    Using a polling loop instead of a fixed sleep makes relay tests deterministic:
    on fast machines the loop exits as soon as the relay completes; on slow machines
    it waits up to *timeout* seconds rather than racing against a fixed 200ms budget.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False  # pragma: no cover — timeout branch only on pathological machines


# ---------------------------------------------------------------------------
# autouse fixture — redirect state/PID files to tmp_path, mock startup side-effects
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Redirect state/PID files to tmp_path, mock kill_orphan_ttyd, replace _poll_loop with no-op."""
    # Redirect state files
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)

    # Redirect PID files
    tmp_pid_dir = tmp_path / "ttyd"
    tmp_pid_path = tmp_pid_dir / "ttyd.pid"
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_DIR", tmp_pid_dir)
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_PATH", tmp_pid_path)

    # Mock kill_orphan_ttyd so startup doesn't touch real processes (must be async)
    async def _mock_kill_orphan():
        return False

    monkeypatch.setattr("muxplex.main.kill_orphan_ttyd", _mock_kill_orphan)

    # Replace _poll_loop with a no-op so tests don't spin up real poll cycles
    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)


# ---------------------------------------------------------------------------
# Helper — create TestClient with valid session cookie
# ---------------------------------------------------------------------------


def _make_authed_client():
    """Creates TestClient with valid session cookie."""
    from muxplex.main import _auth_secret, _auth_ttl

    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    client = TestClient(app)
    client.cookies.set("muxplex_session", cookie)
    return client


# ---------------------------------------------------------------------------
# FakeTtydWs — mock ttyd WebSocket for relay testing
# ---------------------------------------------------------------------------


class FakeTtydWs:
    """Mock ttyd WebSocket that stores sent messages and yields pre-loaded responses.

    Supports send(), close(), async iterator, and async context manager.

    ``stay_open=True`` models a real ttyd: after yielding its responses the
    stream stays open (blocks) until close() — required for browser→ttyd relay
    tests, because the proxy correctly ends the relay as soon as the ttyd side
    closes (FIRST_COMPLETED semantics; a closed ttyd means the terminal is gone).
    """

    def __init__(self, responses=None, stay_open=False):
        self.sent = []
        self._responses = list(responses or [])
        self._closed = False
        self._stay_open = stay_open

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self._closed = True

    def __aiter__(self):
        return self._async_gen()

    async def _async_gen(self):
        import asyncio

        for msg in self._responses:
            yield msg
        while self._stay_open and not self._closed:
            await asyncio.sleep(0.01)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False


# ---------------------------------------------------------------------------
# Test 1: regression — proxy source must use receive(), not receive_bytes()
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: ttyd liveness check before websocket.accept
# ---------------------------------------------------------------------------


def test_ttyd_is_listening_function_exists():
    """_ttyd_is_listening() must exist in main.py (TCP probe helper)."""
    # Import will fail if function doesn't exist — that IS the failing test
    from muxplex.main import _ttyd_is_listening  # noqa: F401

    assert callable(_ttyd_is_listening)


def test_ws_proxy_checks_ttyd_before_accepting():
    """terminal_ws_proxy must check _ttyd_is_listening BEFORE websocket.accept.

    Root cause of the reconnect loop: the proxy called websocket.accept() before
    checking if ttyd was alive. The browser's 'open' event fired immediately,
    resetting _reconnectAttempts to 0. The counter bounced 0→1→0→1 forever so
    the client-side /connect POST (at >= 2 attempts) never fired.

    Fix: check _ttyd_is_listening() first. If not listening, auto-spawn ttyd
    THEN accept — so the browser only gets 'open' when ttyd is actually ready.
    """
    source = inspect.getsource(terminal_ws_proxy)
    # Use "await websocket.accept" to avoid matching the docstring mention
    accept_idx = source.index("await websocket.accept")
    ttyd_check_idx = source.index("_ttyd_is_listening")
    assert ttyd_check_idx < accept_idx, (
        "_ttyd_is_listening() must be checked BEFORE await websocket.accept() — "
        "proxy must not accept the browser WS until ttyd is confirmed alive"
    )


def test_ws_proxy_auto_spawns_ttyd_when_dead(monkeypatch):
    """WS proxy must call spawn_ttyd when _ttyd_is_listening returns False."""
    import asyncio

    spawn_calls = []

    async def mock_spawn_ttyd(name: str):
        spawn_calls.append(name)

    async def mock_kill_ttyd():
        pass

    async def mock_sleep(_delay: float):
        pass  # no-op so tests don't actually wait

    # Patch _ttyd_is_listening to report ttyd as dead
    monkeypatch.setattr("muxplex.main._ttyd_is_listening", lambda: False)
    # Patch spawn_ttyd / kill_ttyd so tests don't touch real processes
    monkeypatch.setattr("muxplex.main.spawn_ttyd", mock_spawn_ttyd)
    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill_ttyd)
    # asyncio.sleep is called after spawn — patch to be a no-op
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    # Provide a fake websockets.connect that immediately closes (no real ttyd)
    fake_ws = FakeTtydWs(responses=[])
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    # Patch load_state to return state with terminal_session -- _prepare_ttyd_for_reconnect()
    # reads terminal_session (what ttyd was actually attached to), not any group's
    # active_session, per the sync-groups spec.
    monkeypatch.setattr(
        "muxplex.main.load_state",
        lambda: {
            "active_session": "test-session",
            "terminal_session": "test-session",
            "sessions": {},
            "session_order": [],
        },
    )

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as _:
            pass

    assert spawn_calls == ["test-session"], (
        "spawn_ttyd must be called with active_session when ttyd is not listening"
    )


def test_terminal_ws_proxy_does_not_use_receive_bytes():
    """Regression: receive_bytes() silently drops TEXT frames (like the ttyd auth token).

    terminal.js sends {"AuthToken": ""} as a TEXT WebSocket frame. The original
    proxy used receive_bytes() which fails on text frames, swallowed the exception,
    and exited — meaning ttyd never received the auth token, never started
    streaming, resulting in a permanent black screen and reconnect loop.

    The proxy MUST use receive() and dispatch on message type to handle both
    binary and text frames correctly.
    """
    source = inspect.getsource(terminal_ws_proxy)
    assert "receive_bytes" not in source, (
        "client_to_ttyd must not use receive_bytes() — silently drops text frames "
        'like the ttyd auth token {"AuthToken": ""}'
    )
    assert ".receive()" in source, (
        "client_to_ttyd must use receive() to handle both text and binary frames"
    )


# ---------------------------------------------------------------------------
# Tests 2–3: auth rejection
# ---------------------------------------------------------------------------


def test_ws_auth_rejection_no_cookie():
    """WebSocket from non-localhost without cookie is closed with code 4001."""
    # TestClient default host is "testclient" which is treated as non-localhost
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws") as _:
                pass
    assert exc_info.value.code == 4001


def test_ws_auth_rejection_invalid_cookie():
    """WebSocket from non-localhost with a tampered cookie is closed with code 4001."""
    with TestClient(app) as c:
        c.cookies.set("muxplex_session", "tampered.invalid.cookie.value")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws") as _:
                pass
    assert exc_info.value.code == 4001


# ---------------------------------------------------------------------------
# Test: Bearer token auth accepted
# ---------------------------------------------------------------------------


def test_ws_bearer_auth_accepted(monkeypatch):
    """WebSocket from non-localhost with valid Bearer federation key is NOT rejected with 4001.

    When a valid federation key is provided as 'Authorization: Bearer <key>',
    the WebSocket connection must be accepted (not closed with code 4001).
    """
    fed_key = "test-federation-secret-key"
    monkeypatch.setattr("muxplex.main._federation_key", fed_key)

    fake_ws = FakeTtydWs(responses=[])
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    # Connect without a session cookie but with a valid Bearer token.
    # Should NOT raise WebSocketDisconnect with code 4001.
    with TestClient(app) as c:
        # If Bearer auth is not implemented, this raises WebSocketDisconnect(code=4001)
        with c.websocket_connect(
            "/terminal/ws",
            headers={"Authorization": f"Bearer {fed_key}"},
        ) as _ws:
            pass  # Successfully connected — auth was accepted


# ---------------------------------------------------------------------------
# Tests 4–5: browser → ttyd relay
# ---------------------------------------------------------------------------


def test_browser_text_relayed_to_ttyd(monkeypatch):
    """Text message from browser is forwarded to ttyd via FakeTtydWs.send()."""
    fake_ws = FakeTtydWs(stay_open=True)
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as ws:
            ws.send_text("hello from browser")
            _wait_for(lambda: "hello from browser" in fake_ws.sent)

    assert "hello from browser" in fake_ws.sent


def test_browser_bytes_relayed_to_ttyd(monkeypatch):
    """Binary message from browser is forwarded to ttyd via FakeTtydWs.send()."""
    fake_ws = FakeTtydWs(stay_open=True)
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as ws:
            ws.send_bytes(b"\x00\x01\x02 binary data")
            _wait_for(lambda: b"\x00\x01\x02 binary data" in fake_ws.sent)

    assert b"\x00\x01\x02 binary data" in fake_ws.sent


# ---------------------------------------------------------------------------
# Tests 6–7: ttyd → browser relay
# ---------------------------------------------------------------------------


def test_ttyd_text_relayed_to_browser(monkeypatch):
    """Text message from ttyd is forwarded to browser via websocket.send_text()."""
    fake_ws = FakeTtydWs(responses=["hello from ttyd"])
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as ws:
            msg = ws.receive_text()
    assert msg == "hello from ttyd"


def test_ttyd_bytes_relayed_to_browser(monkeypatch):
    """Binary message from ttyd is forwarded to browser via websocket.send_bytes()."""
    fake_ws = FakeTtydWs(responses=[b"\xde\xad\xbe\xef binary"])
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as ws:
            msg = ws.receive_bytes()
    assert msg == b"\xde\xad\xbe\xef binary"


# ---------------------------------------------------------------------------
# Test 8: ttyd close propagates to browser
# ---------------------------------------------------------------------------


def test_ttyd_close_propagates_to_browser(monkeypatch):
    """When ttyd exhausts its messages, the proxy cleans up and closes the browser WS."""
    fake_ws = FakeTtydWs(responses=[])  # no responses — exhausts immediately
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as _:
            # FakeTtydWs has no responses so ttyd_to_client exhausts immediately.
            # Exiting the context manager closes the browser WS, which causes
            # client_to_ttyd to complete, gather finishes, and the proxy
            # finally-block calls fake_ws.close().
            pass

    # fake_ws should have been closed when the async-with block exited
    assert fake_ws._closed


# ---------------------------------------------------------------------------
# Test 9: ttyd unreachable closes browser WS
# ---------------------------------------------------------------------------


def test_ttyd_unreachable_closes_browser_ws(monkeypatch):
    """OSError on ttyd connect closes the browser WebSocket (no hang, no 4001)."""

    def mock_connect_raises(*args, **kwargs):
        raise OSError("Connection refused — ttyd not running")

    monkeypatch.setattr("muxplex.main.websockets.connect", mock_connect_raises)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as ws:
            # Proxy accepts, then closes after failing to reach ttyd.
            # Receive the close frame — proves the proxy closed (no hang)
            # and that auth was not rejected (which would use code 4001).
            close_frame = ws.receive()
    assert close_frame.get("type") == "websocket.close", (
        "Proxy must close the WebSocket"
    )
    assert close_frame.get("code") != 4001, "Must not be an auth rejection (4001)"


# ---------------------------------------------------------------------------
# Test 10: concurrent sessions don't interfere
# ---------------------------------------------------------------------------


def test_concurrent_ws_sessions(monkeypatch):
    """Two simultaneous proxy sessions relay to separate FakeTtydWs instances."""
    # Create two separate FakeTtydWs instances, one per connection
    ws_pool = [FakeTtydWs(stay_open=True), FakeTtydWs(stay_open=True)]
    call_count = 0
    lock = threading.Lock()

    def mock_connect(*args, **kwargs):
        nonlocal call_count
        with lock:
            idx = call_count % len(ws_pool)
            call_count += 1
        return ws_pool[idx]

    monkeypatch.setattr("muxplex.main.websockets.connect", mock_connect)

    errors = []

    with _make_authed_client() as c:

        def send_msg(text):
            try:
                with c.websocket_connect("/terminal/ws") as ws:
                    ws.send_text(text)
                    _wait_for(lambda: text in ws_pool[0].sent + ws_pool[1].sent)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=send_msg, args=("session_one_msg",))
        t2 = threading.Thread(target=send_msg, args=("session_two_msg",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    assert not errors, f"Concurrent sessions raised errors: {errors}"

    # Both messages must have been relayed (one to each fake_ws)
    all_sent = ws_pool[0].sent + ws_pool[1].sent
    assert "session_one_msg" in all_sent
    assert "session_two_msg" in all_sent


# ---------------------------------------------------------------------------
# Task-11: federation WebSocket proxy route
# ---------------------------------------------------------------------------


def test_federation_ws_proxy_route_exists():
    """App must have a WebSocket route at /federation/{device_id}/terminal/ws."""
    from starlette.routing import WebSocketRoute

    ws_routes = [r for r in app.routes if isinstance(r, WebSocketRoute)]
    paths = [r.path for r in ws_routes]
    assert "/federation/{device_id}/terminal/ws" in paths, (
        "App must have a WebSocket route at /federation/{device_id}/terminal/ws"
    )


def test_federation_ws_proxy_uses_ssl_context_for_wss():
    """Federation WS proxy must pass an SSL context when connecting via wss://.

    Self-signed certs on remote instances (cortex, spark-2, etc.) fail the
    default SSL verification in websockets.connect().  The proxy must build an
    SSLContext with CERT_NONE for wss:// URLs — the same fix already applied
    to the httpx client (verify=False) but for the websockets library.
    """
    from muxplex.main import federation_terminal_ws_proxy

    source = inspect.getsource(federation_terminal_ws_proxy)
    assert "ssl" in source and ("CERT_NONE" in source or "ssl_context" in source), (
        "Federation WS proxy must configure an SSL context (CERT_NONE / ssl_context) "
        "for self-signed cert support on wss:// connections"
    )


# ---------------------------------------------------------------------------
# Regression: client disconnect during the pre-accept ttyd auto-spawn window
# must never reach websocket.accept().
#
# Root cause (reproduced against a real uvicorn server forced onto the
# 'websockets-sansio' protocol implementation, matching what production's
# actual dependency resolution selects): uvicorn's ASGI websocket connection
# object flips its internal "handshake complete" bookkeeping to True the
# moment the underlying TCP connection is lost -- via the generic
# asyncio.Protocol.connection_lost() callback -- regardless of whether a
# real WebSocket handshake ever happened. If terminal_ws_proxy's pre-accept
# ttyd auto-spawn wait (kill_ttyd + spawn_ttyd + asyncio.sleep(0.8)) is still
# in flight when that happens, the LATER call to websocket.accept() looks to
# uvicorn like a stray 'websocket.accept' message on an already-established
# connection, and it raises:
#     RuntimeError: Expected ASGI message 'websocket.send' or
#     'websocket.close', but got 'websocket.accept'.
# This is what production's journal showed recurring several times per hour
# under "Exception in ASGI application".
#
# These tests use a minimal fake WebSocket (no TestClient/uvicorn -- the
# TestClient's in-memory ASGI transport does not reproduce uvicorn's real
# handshake-state bookkeeping) that can simulate a disconnect arriving
# mid-wait, and assert accept() is never called when that happens.
# ---------------------------------------------------------------------------


class FakeWebSocketForRace:
    """Minimal WebSocket test double for the pre-accept disconnect race.

    receive() returns 'websocket.connect' on the first call (matching real
    ASGI ordering), then blocks for `disconnect_after` seconds before
    returning 'websocket.disconnect' -- simulating a client that vanishes
    WHILE the ttyd auto-spawn wait is in flight.  If `disconnect_after` is
    None, receive() blocks "forever" (long enough to never win the race in
    these tests) so the auto-spawn path can complete normally instead.

    accept()/close() just record whether they were called -- the regression
    assertion is that accept() must NOT be called when the client
    disconnects before the auto-spawn wait finishes.
    """

    def __init__(self, disconnect_after: float | None = None):
        self._disconnect_after = disconnect_after
        self._connect_sent = False
        self.accept_called = False
        self.close_called = False
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self.client = types.SimpleNamespace(host="127.0.0.1")  # localhost auth bypass

    async def receive(self):
        if not self._connect_sent:
            self._connect_sent = True
            return {"type": "websocket.connect"}
        # "Never" is stood in by a long real delay so the type stays a plain
        # float; tests always finish (or cancel this task) well before then.
        delay = self._disconnect_after if self._disconnect_after is not None else 3600.0
        await asyncio.sleep(delay)
        return {"type": "websocket.disconnect", "code": 1006}

    async def accept(self, subprotocol=None):
        self.accept_called = True

    async def close(self, code: int = 1000):
        self.close_called = True


def _patch_ttyd_auto_spawn(monkeypatch):
    """Patch ttyd/tmux process management to no-ops (so
    _prepare_ttyd_for_reconnect() never touches real processes) while
    leaving its real 0.8s settle delay (asyncio.sleep(0.8)) untouched.

    Deliberately does NOT monkeypatch asyncio.sleep itself: asyncio.sleep is
    a single process-wide function, so patching it here would also hijack
    FakeWebSocketForRace's own timing in the same test, defeating the race
    it's meant to model. The real 0.8s delay makes the "stays connected"
    control-case test slightly slower but keeps both timings independent
    and honest.
    """

    async def _mock_kill_ttyd():
        return False

    async def _mock_spawn_ttyd(name: str):
        return None

    monkeypatch.setattr("muxplex.main._ttyd_is_listening", lambda: False)
    monkeypatch.setattr("muxplex.main.kill_ttyd", _mock_kill_ttyd)
    monkeypatch.setattr("muxplex.main.spawn_ttyd", _mock_spawn_ttyd)
    monkeypatch.setattr(
        "muxplex.main.load_state",
        lambda: {
            "active_session": "test-session",
            "terminal_session": "test-session",
            "sessions": {},
            "session_order": [],
        },
    )


def test_ws_proxy_skips_accept_when_client_disconnects_during_auto_spawn(monkeypatch):
    """Regression: a client that disconnects WHILE ttyd is being auto-spawned
    must never reach websocket.accept() -- doing so raises the production
    RuntimeError (see module-level comment above).
    """
    _patch_ttyd_auto_spawn(monkeypatch)
    # Disconnects well within the real 0.8s auto-spawn settle delay.
    fake_ws = FakeWebSocketForRace(disconnect_after=0.05)

    asyncio.run(terminal_ws_proxy(cast(Any, fake_ws)))

    assert not fake_ws.accept_called, (
        "terminal_ws_proxy must NOT call websocket.accept() when the client "
        "disconnected during the ttyd auto-spawn wait"
    )


def test_ws_proxy_still_accepts_when_client_stays_connected_during_auto_spawn(
    monkeypatch,
):
    """Control case: when the client does NOT disconnect during the
    auto-spawn wait, terminal_ws_proxy must still reach websocket.accept()
    as before -- the fix must not break the normal auto-spawn path.
    """
    _patch_ttyd_auto_spawn(monkeypatch)
    fake_ws = FakeWebSocketForRace(disconnect_after=None)  # never disconnects

    # ttyd connect will fail (nothing real listening) -- that's fine, we only
    # care whether accept() was reached before the (harmless) relay failure.
    # NOT async: websockets.connect() is used as `async with websockets.connect(...)`,
    # so the mock itself must raise synchronously on call (matching the
    # pattern used by test_ttyd_unreachable_closes_browser_ws above).
    def _mock_connect_raises(*args, **kwargs):
        raise OSError("Connection refused — no real ttyd in this test")

    monkeypatch.setattr("muxplex.main.websockets.connect", _mock_connect_raises)

    asyncio.run(terminal_ws_proxy(cast(Any, fake_ws)))

    assert fake_ws.accept_called, (
        "terminal_ws_proxy must still call websocket.accept() when the "
        "client stays connected through the auto-spawn wait"
    )


# ---------------------------------------------------------------------------
# §0 guard: device_id on /terminal/ws (sync-groups spec §10.3, tests 27-31)
# ---------------------------------------------------------------------------


def _heartbeat(client, device_id, sync_group=None):
    payload = {
        "device_id": device_id,
        "label": "test",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": 0.0,
    }
    if sync_group is not None:
        payload["sync_group"] = sync_group
    return client.post("/api/heartbeat", json=payload)


def test_ws_no_device_id_unaffected(monkeypatch):
    """No device_id -> today's path exactly, no new behavior."""
    fake_ws = FakeTtydWs(stay_open=True)
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as ws:
            ws.send_text("hello")
            _wait_for(lambda: "hello" in fake_ws.sent)

    assert "hello" in fake_ws.sent


def test_ws_unknown_device_id_closes_4404():
    with _make_authed_client() as c:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws?device_id=unknown-device") as _:
                pass
    assert exc_info.value.code == 4404


def test_ws_mismatched_active_session_closes_4409(monkeypatch):
    """Device's active_session != terminal_session -> close(4409), never relay."""
    with _make_authed_client() as c:
        _heartbeat(c, "d1", "device:d1")
        # Give the terminal to global (via connect), leaving device:d1's
        # own active_session at None -- it never selected anything.
        monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["sessX", "sessY"])

        async def _mock_spawn(name):
            class _FakeProc:
                pid = 1

            return _FakeProc()

        async def _mock_kill():
            return True

        monkeypatch.setattr("muxplex.main.spawn_ttyd", _mock_spawn)
        monkeypatch.setattr("muxplex.main.kill_ttyd", _mock_kill)
        monkeypatch.setattr("muxplex.main._ttyd_is_listening", lambda: False)

        c.post("/api/sessions/sessX/connect")  # global claims the terminal

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws?device_id=d1") as _:
                pass
    assert exc_info.value.code == 4409


def test_ws_matching_active_session_relays_normally(monkeypatch):
    """Device's active_session == terminal_session -> normal relay."""
    fake_ws = FakeTtydWs(stay_open=True)
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    async def _mock_spawn(name):
        class _FakeProc:
            pid = 1

        return _FakeProc()

    async def _mock_kill():
        return True

    monkeypatch.setattr("muxplex.main.spawn_ttyd", _mock_spawn)
    monkeypatch.setattr("muxplex.main.kill_ttyd", _mock_kill)
    monkeypatch.setattr("muxplex.main._ttyd_is_listening", lambda: False)
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["sessX"])

    with _make_authed_client() as c:
        _heartbeat(c, "d1", "device:d1")
        c.post("/api/sessions/sessX/connect?device_id=d1")

        with c.websocket_connect("/terminal/ws?device_id=d1") as ws:
            ws.send_text("hello")
            _wait_for(lambda: "hello" in fake_ws.sent)

    assert "hello" in fake_ws.sent


def test_prepare_ttyd_for_reconnect_reads_terminal_session(monkeypatch):
    """_prepare_ttyd_for_reconnect() must respawn for terminal_session, not
    any group's active_session."""
    import muxplex.main as main_mod

    spawn_calls = []

    async def _mock_spawn(name):
        spawn_calls.append(name)

        class _FakeProc:
            pid = 1

        return _FakeProc()

    async def _mock_kill():
        return True

    monkeypatch.setattr(main_mod, "spawn_ttyd", _mock_spawn)
    monkeypatch.setattr(main_mod, "kill_ttyd", _mock_kill)
    monkeypatch.setattr(
        main_mod,
        "load_state",
        lambda: {
            "active_session": "wrong-session",
            "terminal_session": "right-session",
            "sessions": {},
            "session_order": [],
        },
    )

    asyncio.run(main_mod._prepare_ttyd_for_reconnect())

    assert spawn_calls == ["right-session"]

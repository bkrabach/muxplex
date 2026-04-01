"""
Comprehensive tests for the WebSocket proxy in muxplex/main.py.
"""

import inspect
import threading
import time

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
    """

    def __init__(self, responses=None):
        self.sent = []
        self._responses = list(responses or [])
        self._closed = False

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self._closed = True

    def __aiter__(self):
        return self._async_gen()

    async def _async_gen(self):
        for msg in self._responses:
            yield msg

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

    # Patch load_state to return state with active_session
    monkeypatch.setattr(
        "muxplex.main.load_state",
        lambda: {"active_session": "test-session", "sessions": {}, "session_order": []},
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
# Tests 4–5: browser → ttyd relay
# ---------------------------------------------------------------------------


def test_browser_text_relayed_to_ttyd(monkeypatch):
    """Text message from browser is forwarded to ttyd via FakeTtydWs.send()."""
    fake_ws = FakeTtydWs()
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws") as ws:
            ws.send_text("hello from browser")
            _wait_for(lambda: "hello from browser" in fake_ws.sent)

    assert "hello from browser" in fake_ws.sent


def test_browser_bytes_relayed_to_ttyd(monkeypatch):
    """Binary message from browser is forwarded to ttyd via FakeTtydWs.send()."""
    fake_ws = FakeTtydWs()
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
    ws_pool = [FakeTtydWs(), FakeTtydWs()]
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

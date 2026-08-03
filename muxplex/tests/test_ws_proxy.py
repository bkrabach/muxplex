"""
Comprehensive tests for the WebSocket proxy in muxplex/main.py.

Per-session ttyd (PER_SESSION_TTYD_SPEC.md §12.3): dials session-specific
UNIX sockets via `unix_connect` and `socket_is_live` rather than a single
TCP port and `_ttyd_is_listening`. `?session=` is the new addressing
mechanism on both `/terminal/ws` and the federation route.
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
from muxplex.main import app, federation_terminal_ws_proxy, terminal_ws_proxy

DEFAULT_SESSION = "test-session"


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
    """Redirect state/socket dir to tmp_path, mock startup ttyd reapers,
    replace _poll_loop with a no-op, and default this session's target as
    known + already-live so relay tests don't touch a real ttyd process.
    """
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)

    tmp_socket_dir = tmp_path / "ttyd"
    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", tmp_socket_dir)

    async def _mock_reap_orphan():
        return 0

    async def _mock_reap_legacy():
        return False

    monkeypatch.setattr("muxplex.main.reap_orphan_ttyds", _mock_reap_orphan)
    monkeypatch.setattr("muxplex.main.reap_legacy_ttyd", _mock_reap_legacy)
    monkeypatch.setattr("muxplex.main.ttyd_mod.validate_socket_dir", lambda d: None)

    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)

    # This session is known and its ttyd is already live -- most relay tests
    # want to skip the auto-spawn branch entirely and go straight to the
    # relay. Tests that specifically exercise auto-spawn override this.
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [DEFAULT_SESSION])
    monkeypatch.setattr("muxplex.main.socket_is_live", lambda path: True)


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


def _ws_url(session: str | None = DEFAULT_SESSION, device_id: str | None = None) -> str:
    parts = []
    if session is not None:
        parts.append(f"session={session}")
    if device_id is not None:
        parts.append(f"device_id={device_id}")
    return "/terminal/ws" + ("?" + "&".join(parts) if parts else "")


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
# Tests: session-specific socket liveness check before websocket.accept
# ---------------------------------------------------------------------------


def test_socket_is_live_importable():
    """socket_is_live() must exist in main.py (the UNIX-socket liveness probe)."""
    from muxplex.main import socket_is_live

    assert callable(socket_is_live)


def test_ws_proxy_checks_socket_is_live_before_accepting():
    """terminal_ws_proxy must check socket_is_live BEFORE websocket.accept.

    Root cause of the reconnect loop this guards: the proxy called
    websocket.accept() before checking if this session's ttyd was alive.
    The browser's 'open' event fired immediately, resetting
    _reconnectAttempts to 0. The counter bounced 0→1→0→1 forever so the
    client-side /connect POST (at >= 2 attempts) never fired.

    Fix: check socket_is_live() first. If not live, auto-spawn ttyd THEN
    accept — so the browser only gets 'open' when ttyd is actually ready.
    """
    source = inspect.getsource(terminal_ws_proxy)
    # Use "await websocket.accept" to avoid matching the docstring mention
    accept_idx = source.index("await websocket.accept")
    ttyd_check_idx = source.index("socket_is_live")
    assert ttyd_check_idx < accept_idx, (
        "socket_is_live() must be checked BEFORE await websocket.accept() — "
        "proxy must not accept the browser WS until this session's ttyd is confirmed alive"
    )


def test_ws_proxy_auto_spawns_ttyd_when_dead(monkeypatch):
    """WS proxy must call ensure_ttyd(target) when socket_is_live returns False."""
    ensure_calls = []

    async def mock_ensure_ttyd(name: str):
        ensure_calls.append(name)

    monkeypatch.setattr("muxplex.main.socket_is_live", lambda path: False)
    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure_ttyd)

    fake_ws = FakeTtydWs(responses=[])
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as _:
        pass

    assert ensure_calls == [DEFAULT_SESSION], (
        "ensure_ttyd must be called with the resolved target session when "
        "its socket is not live"
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
# Tests: auth rejection
# ---------------------------------------------------------------------------


def test_ws_auth_rejection_no_cookie():
    """WebSocket from non-localhost without cookie is closed with code 4001."""
    # TestClient default host is "testclient" which is treated as non-localhost
    with TestClient(app) as c, pytest.raises(WebSocketDisconnect) as exc_info:
        with c.websocket_connect(_ws_url()) as _:
            pass
    assert exc_info.value.code == 4001


def test_ws_auth_rejection_invalid_cookie():
    """WebSocket from non-localhost with a tampered cookie is closed with code 4001."""
    with TestClient(app) as c:
        c.cookies.set("muxplex_session", "tampered.invalid.cookie.value")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect(_ws_url()) as _:
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
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    # Connect without a session cookie but with a valid Bearer token.
    # Should NOT raise WebSocketDisconnect with code 4001.
    with TestClient(app) as c:
        # If Bearer auth is not implemented, this raises WebSocketDisconnect(code=4001)
        with c.websocket_connect(
            _ws_url(),
            headers={"Authorization": f"Bearer {fed_key}"},
        ) as _ws:
            pass  # Successfully connected — auth was accepted


# ---------------------------------------------------------------------------
# Tests: browser → ttyd relay
# ---------------------------------------------------------------------------


def test_browser_text_relayed_to_ttyd(monkeypatch):
    """Text message from browser is forwarded to ttyd via FakeTtydWs.send()."""
    fake_ws = FakeTtydWs(stay_open=True)
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as ws:
        ws.send_text("hello from browser")
        _wait_for(lambda: "hello from browser" in fake_ws.sent)

    assert "hello from browser" in fake_ws.sent


def test_browser_bytes_relayed_to_ttyd(monkeypatch):
    """Binary message from browser is forwarded to ttyd via FakeTtydWs.send()."""
    fake_ws = FakeTtydWs(stay_open=True)
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as ws:
        ws.send_bytes(b"\x00\x01\x02 binary data")
        _wait_for(lambda: b"\x00\x01\x02 binary data" in fake_ws.sent)

    assert b"\x00\x01\x02 binary data" in fake_ws.sent


# ---------------------------------------------------------------------------
# Tests: ttyd → browser relay
# ---------------------------------------------------------------------------


def test_ttyd_text_relayed_to_browser(monkeypatch):
    """Text message from ttyd is forwarded to browser via websocket.send_text()."""
    fake_ws = FakeTtydWs(responses=["hello from ttyd"])
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as ws:
        msg = ws.receive_text()
    assert msg == "hello from ttyd"


def test_ttyd_bytes_relayed_to_browser(monkeypatch):
    """Binary message from ttyd is forwarded to browser via websocket.send_bytes()."""
    fake_ws = FakeTtydWs(responses=[b"\xde\xad\xbe\xef binary"])
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as ws:
        msg = ws.receive_bytes()
    assert msg == b"\xde\xad\xbe\xef binary"


# ---------------------------------------------------------------------------
# Test: ttyd close propagates to browser
# ---------------------------------------------------------------------------


def test_ttyd_close_propagates_to_browser(monkeypatch):
    """When ttyd exhausts its messages, the proxy cleans up and closes the browser WS."""
    fake_ws = FakeTtydWs(responses=[])  # no responses — exhausts immediately
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as _:
        # FakeTtydWs has no responses so ttyd_to_client exhausts immediately.
        # Exiting the context manager closes the browser WS, which causes
        # client_to_ttyd to complete, gather finishes, and the proxy
        # finally-block calls fake_ws.close().
        pass

    # fake_ws should have been closed when the async-with block exited
    assert fake_ws._closed


# ---------------------------------------------------------------------------
# Test: ttyd unreachable closes browser WS
# ---------------------------------------------------------------------------


def test_ttyd_unreachable_closes_browser_ws(monkeypatch):
    """OSError on ttyd connect closes the browser WebSocket (no hang, no 4001)."""

    def mock_connect_raises(*args, **kwargs):
        raise OSError("Connection refused — ttyd not running")

    monkeypatch.setattr("muxplex.main.unix_connect", mock_connect_raises)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as ws:
        # Proxy accepts, then closes after failing to reach ttyd.
        # Receive the close frame — proves the proxy closed (no hang)
        # and that auth was not rejected (which would use code 4001).
        close_frame = ws.receive()
    assert close_frame.get("type") == "websocket.close", (
        "Proxy must close the WebSocket"
    )
    assert close_frame.get("code") != 4001, "Must not be an auth rejection (4001)"


# ---------------------------------------------------------------------------
# Test: concurrent sessions don't interfere
# ---------------------------------------------------------------------------


def test_concurrent_ws_sessions(monkeypatch):
    """Two simultaneous proxy sessions relay to separate FakeTtydWs instances."""
    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["session-one", "session-two"]
    )
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

    monkeypatch.setattr("muxplex.main.unix_connect", mock_connect)

    errors = []

    with _make_authed_client() as c:

        def send_msg(session_name, text):
            try:
                with c.websocket_connect(_ws_url(session=session_name)) as ws:
                    ws.send_text(text)
                    _wait_for(lambda: text in ws_pool[0].sent + ws_pool[1].sent)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=send_msg, args=("session-one", "session_one_msg"))
        t2 = threading.Thread(target=send_msg, args=("session-two", "session_two_msg"))
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
# federation WebSocket proxy route
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
    source = inspect.getsource(federation_terminal_ws_proxy)
    assert "ssl" in source and ("CERT_NONE" in source or "ssl_context" in source), (
        "Federation WS proxy must configure an SSL context (CERT_NONE / ssl_context) "
        "for self-signed cert support on wss:// connections"
    )


def test_federation_forwards_session_param():
    """Upstream URL contains ?session=X, correctly urlencoded."""
    source = inspect.getsource(federation_terminal_ws_proxy)
    assert "session_qs" in source
    assert "quote(session" in source


def test_federation_omits_session_when_absent():
    """No session in the upstream URL when the caller sent none."""
    source = inspect.getsource(federation_terminal_ws_proxy)
    assert 'if session is not None else ""' in source or "session is not None" in source


def test_federation_ws_still_dials_remote_terminal_ws_never_a_socket():
    """The federation relay must never touch a ttyd socket -- it dials the
    remote muxplex's own authenticated /terminal/ws."""
    source = inspect.getsource(federation_terminal_ws_proxy)
    assert "unix_connect" not in source
    assert "socket_path_for" not in source
    assert "/terminal/ws" in source


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
# ttyd auto-spawn wait is still in flight when that happens, the LATER call
# to websocket.accept() looks to uvicorn like a stray 'websocket.accept'
# message on an already-established connection, and it raises:
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

    def __init__(
        self, disconnect_after: float | None = None, query_string: bytes = b""
    ):
        self._disconnect_after = disconnect_after
        self._connect_sent = False
        self.accept_called = False
        self.close_called = False
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self.client = types.SimpleNamespace(host="127.0.0.1")  # localhost auth bypass
        self.query_params: dict[str, str] = {}
        self.scope = {"query_string": query_string}

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
    """Patch ttyd/tmux process management to no-ops (so `_prepare_ttyd()`
    never touches real processes) with an artificial delay standing in for
    real spawn latency, so the race with a disconnecting client is
    exercisable without a real ttyd/tmux."""

    async def _mock_ensure_ttyd(name: str):
        await asyncio.sleep(0.8)

    monkeypatch.setattr("muxplex.main.socket_is_live", lambda path: False)
    monkeypatch.setattr("muxplex.main.ensure_ttyd", _mock_ensure_ttyd)
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [DEFAULT_SESSION])
    monkeypatch.setattr(
        "muxplex.main.load_state",
        lambda: {
            "active_session": DEFAULT_SESSION,
            "terminal_session": DEFAULT_SESSION,
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
    # Disconnects well within the mocked 0.8s auto-spawn wait.
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

    def _mock_connect_raises(*args, **kwargs):
        raise OSError("Connection refused — no real ttyd in this test")

    monkeypatch.setattr("muxplex.main.unix_connect", _mock_connect_raises)

    asyncio.run(terminal_ws_proxy(cast(Any, fake_ws)))

    assert fake_ws.accept_called, (
        "terminal_ws_proxy must still call websocket.accept() when the "
        "client stays connected through the auto-spawn wait"
    )


def test_spawn_failure_does_not_hang_client(monkeypatch):
    """_prepare_ttyd raising -> handler returns, no accept()."""
    monkeypatch.setattr("muxplex.main.socket_is_live", lambda path: False)
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [DEFAULT_SESSION])
    monkeypatch.setattr(
        "muxplex.main.load_state",
        lambda: {
            "active_session": DEFAULT_SESSION,
            "terminal_session": DEFAULT_SESSION,
            "sessions": {},
            "session_order": [],
        },
    )

    from muxplex.ttyd import TtydSpawnError

    async def _mock_ensure_ttyd(name: str):
        raise TtydSpawnError("boom")

    monkeypatch.setattr("muxplex.main.ensure_ttyd", _mock_ensure_ttyd)

    fake_ws = FakeWebSocketForRace(disconnect_after=None)
    asyncio.run(terminal_ws_proxy(cast(Any, fake_ws)))

    assert not fake_ws.accept_called, (
        "a spawn failure must never reach accept() -- no relay for a terminal "
        "that doesn't exist"
    )


# ---------------------------------------------------------------------------
# §0 guard: device_id on /terminal/ws, now redefined + session addressing
# (PER_SESSION_TTYD_SPEC.md §5.1, §7.2, §12.3)
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
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as ws:
        ws.send_text("hello")
        _wait_for(lambda: "hello" in fake_ws.sent)

    assert "hello" in fake_ws.sent


def test_ws_unknown_device_id_closes_4404():
    """Unknown device_id -> accept()-then-close(4404) (see _accept_then_close()).

    Post-fix, the handshake completes (accept()) before the close, so
    entering the TestClient context manager now succeeds -- the close
    frame is the NEXT ASGI message, observed here via ws.receive_text().
    """
    with _make_authed_client() as c:
        with c.websocket_connect(_ws_url(device_id="unknown-device")) as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
    assert exc_info.value.code == 4404


def test_ws_invalid_session_name_closes_4404():
    """?session=-bad;rm -> 4404."""
    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws?session=-bad%3Brm") as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
    assert exc_info.value.code == 4404


def test_ws_unknown_session_closes_4404(monkeypatch):
    """?session=nope -> 4404, no unix_connect call."""
    connect_calls = []
    monkeypatch.setattr(
        "muxplex.main.unix_connect",
        lambda *a, **kw: connect_calls.append(1) or FakeTtydWs(),
    )

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws?session=nope") as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
    assert exc_info.value.code == 4404
    assert connect_calls == []


def test_ws_no_target_at_all_closes_4404():
    """No param, terminal_session=None -> 4404."""
    with _make_authed_client() as c, c.websocket_connect("/terminal/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
    assert exc_info.value.code == 4404


def test_ws_dials_session_specific_socket(monkeypatch):
    """?session=X -> unix_connect called with socket_path_for("X")."""
    from muxplex.ttyd import socket_path_for

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["sessA", "sessB"])
    captured = {}

    def _mock_unix_connect(path, **kwargs):
        captured["path"] = path
        return FakeTtydWs(responses=[])

    monkeypatch.setattr("muxplex.main.unix_connect", _mock_unix_connect)

    with _make_authed_client() as c:
        with c.websocket_connect("/terminal/ws?session=sessA") as _:
            pass

    assert captured["path"] == str(socket_path_for("sessA"))


def test_ws_falls_back_to_terminal_session(monkeypatch):
    """No session -> dials socket_path_for(state["terminal_session"])."""
    from muxplex.ttyd import socket_path_for

    monkeypatch.setattr(
        "muxplex.main.load_state",
        lambda: {
            "active_session": DEFAULT_SESSION,
            "terminal_session": DEFAULT_SESSION,
            "sessions": {},
            "session_order": [],
        },
    )
    captured = {}

    def _mock_unix_connect(path, **kwargs):
        captured["path"] = path
        return FakeTtydWs(responses=[])

    monkeypatch.setattr("muxplex.main.unix_connect", _mock_unix_connect)

    with _make_authed_client() as c, c.websocket_connect("/terminal/ws") as _:
        pass

    assert captured["path"] == str(socket_path_for(DEFAULT_SESSION))


def test_ws_session_not_selected_by_group_closes_4409(monkeypatch):
    """device_id set, group's active_session != session -> 4409."""
    with _make_authed_client() as c:
        _heartbeat(c, "d1", "device:d1")
        # device:d1 never selected anything -- its own active_session stays None,
        # while the requested ?session=test-session is a known session.
        with c.websocket_connect(_ws_url(device_id="d1")) as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
    assert exc_info.value.code == 4409


def test_ws_matching_active_session_relays_normally(monkeypatch):
    """Device's active_session == resolved target -> normal relay."""
    fake_ws = FakeTtydWs(stay_open=True)
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    async def _mock_ensure_ttyd(name):
        return None

    monkeypatch.setattr("muxplex.main.ensure_ttyd", _mock_ensure_ttyd)

    with _make_authed_client() as c:
        _heartbeat(c, "d1", "device:d1")
        c.post(f"/api/sessions/{DEFAULT_SESSION}/connect?device_id=d1")

        with c.websocket_connect(_ws_url(device_id="d1")) as ws:
            ws.send_text("hello")
            _wait_for(lambda: "hello" in fake_ws.sent)

    assert "hello" in fake_ws.sent


def test_ws_registers_and_discards_relay_task():
    """_ws_proxy_tasks gains then loses the task — guards a brief-named property."""
    source = inspect.getsource(terminal_ws_proxy)
    assert "_ws_proxy_tasks.add(_task)" in source
    assert "_ws_proxy_tasks.discard(_task)" in source


def test_federation_ws_registers_relay_task():
    """Same for the federation route."""
    source = inspect.getsource(federation_terminal_ws_proxy)
    assert "_ws_proxy_tasks.add(_task)" in source
    assert "_ws_proxy_tasks.discard(_task)" in source


def test_relay_uses_first_completed_not_gather():
    """Close the client side; handler returns while the fake ttyd is still
    live — guards the shutdown-hang fix on both routes."""
    source = inspect.getsource(terminal_ws_proxy)
    assert "FIRST_COMPLETED" in source
    assert "await asyncio.gather(client_to_ttyd(), ttyd_to_client())" not in source


def test_relay_refcount_released_on_disconnect(monkeypatch):
    """relay_count(X) == 0 in `finally`, including on an exception."""
    from muxplex.ttyd import relay_count

    fake_ws = FakeTtydWs(stay_open=True)
    monkeypatch.setattr("muxplex.main.unix_connect", lambda *a, **kw: fake_ws)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as ws:
        ws.send_text("hi")
        _wait_for(lambda: "hi" in fake_ws.sent)

    assert relay_count(DEFAULT_SESSION) == 0


def test_relay_refcount_released_on_dial_failure(monkeypatch):
    """A dial failure (unix_connect raises) must not leak a refcount."""
    from muxplex.ttyd import relay_count

    def _raise(*a, **kw):
        raise OSError("dial failed")

    monkeypatch.setattr("muxplex.main.unix_connect", _raise)

    with _make_authed_client() as c, c.websocket_connect(_ws_url()) as ws:
        ws.receive()

    assert relay_count(DEFAULT_SESSION) == 0


def test_prepare_ttyd_calls_ensure_ttyd(monkeypatch):
    """_prepare_ttyd(target) must call ensure_ttyd(target)."""
    import muxplex.main as main_mod

    calls = []

    async def _mock_ensure(name):
        calls.append(name)

    monkeypatch.setattr(main_mod, "ensure_ttyd", _mock_ensure)

    result = asyncio.run(main_mod._prepare_ttyd("some-session"))

    assert calls == ["some-session"]
    assert result is True


def test_prepare_ttyd_returns_false_on_spawn_failure(monkeypatch):
    import muxplex.main as main_mod
    from muxplex.ttyd import TtydSpawnError

    async def _mock_ensure(name):
        raise TtydSpawnError("nope")

    monkeypatch.setattr(main_mod, "ensure_ttyd", _mock_ensure)

    result = asyncio.run(main_mod._prepare_ttyd("some-session"))

    assert result is False

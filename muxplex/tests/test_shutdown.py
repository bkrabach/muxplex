"""
Tests for the lifespan shutdown path in muxplex/main.py.

A SIGTERM (systemctl stop/restart) must shut the server down cleanly in ~1s.
Before the fix, shutdown hung >10s and got SIGKILLed because:
  - open terminal WS relays used gather() (wait for BOTH directions), so a
    still-streaming ttyd kept the connection alive after the browser side
    disconnected, and uvicorn waited on it forever;
  - the ttyd subprocess was never killed on shutdown;
  - the shared httpx client was closed BEFORE the poll loop was cancelled.

These tests pin the shutdown contract: poll task cancelled, ttyd killed,
federation client closed, and the relay terminating on first-side close.
"""

import asyncio
import inspect

import pytest
from fastapi.testclient import TestClient

from muxplex.main import app, terminal_ws_proxy

# ---------------------------------------------------------------------------
# Shared fixtures (mirror test_main.py setup so tests run cleanly in isolation)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Redirect state/socket dir to tmp_path and stub out long-running startup tasks."""
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


# ---------------------------------------------------------------------------
# Lifespan shutdown contract
# ---------------------------------------------------------------------------


def test_shutdown_cancels_poll_task(monkeypatch):
    """The poll loop task must be cancelled (and awaited) on shutdown."""
    record: dict[str, bool] = {}

    async def hanging_poll_loop() -> None:
        record["started"] = True
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            record["cancelled"] = True
            raise

    monkeypatch.setattr("muxplex.main._poll_loop", hanging_poll_loop)

    with TestClient(app):
        pass  # startup + shutdown via lifespan

    assert record.get("started") is True
    assert record.get("cancelled") is True, (
        "lifespan shutdown must cancel the poll loop task — a never-cancelled "
        "task blocks clean process exit"
    )


def test_shutdown_kills_all_ttyd(monkeypatch):
    """Every registered per-session ttyd must be killed on shutdown (it was
    leaked before, in the single-ttyd era)."""
    calls: list[str] = []

    async def mock_kill_all_ttyd() -> int:
        calls.append("kill_all_ttyd")
        return 0

    monkeypatch.setattr("muxplex.main.kill_all_ttyd", mock_kill_all_ttyd)

    with TestClient(app):
        pass

    assert calls == ["kill_all_ttyd"], "lifespan shutdown must call kill_all_ttyd()"


def test_lifespan_kills_all_ttyds(monkeypatch):
    """Three registered ttyds -> all three killed, within the existing 3s
    wait_for budget. kill_all_ttyd() gathers, so wall time stays ~one SIGTERM
    round trip, preserving AGENTS.md's ~0.5s shutdown target."""
    import muxplex.ttyd as ttyd_mod

    killed: list[str] = []

    async def _fake_kill(session_name: str) -> bool:
        killed.append(session_name)
        ttyd_mod._ttyds.pop(session_name, None)
        return True

    for name in ("s1", "s2", "s3"):
        ttyd_mod._ttyds[name] = object()  # type: ignore[assignment]  # presence is all kill_all_ttyd() needs to iterate

    monkeypatch.setattr(ttyd_mod, "kill_ttyd", _fake_kill)
    monkeypatch.setattr("muxplex.main.kill_all_ttyd", ttyd_mod.kill_all_ttyd)

    import time as time_mod

    start = time_mod.monotonic()
    with TestClient(app):
        pass
    elapsed = time_mod.monotonic() - start

    assert sorted(killed) == ["s1", "s2", "s3"]
    assert ttyd_mod._ttyds == {}
    assert elapsed < 3.0


def test_shutdown_closes_federation_client():
    """The shared httpx federation client must be closed on shutdown."""
    with TestClient(app):
        client = app.state.federation_client
        assert not client.is_closed

    assert client.is_closed, "lifespan shutdown must aclose() the federation client"


def test_shutdown_cancels_poll_before_closing_client():
    """Ordering: cancel the poll loop BEFORE closing the shared httpx client.

    The poll loop may be mid federation request on that client; closing the
    client first raises on the in-flight request instead of a clean cancel.
    """
    from muxplex.main import lifespan

    source = inspect.getsource(lifespan)
    shutdown_source = source.split("yield", 1)[1]
    cancel_idx = shutdown_source.index(".cancel()")
    aclose_idx = shutdown_source.index("aclose()")
    assert cancel_idx < aclose_idx, (
        "poll task must be cancelled before the federation client is closed"
    )


# ---------------------------------------------------------------------------
# WS relay termination (the primary shutdown hang)
# ---------------------------------------------------------------------------


def test_ws_relay_terminates_on_first_side_close():
    """The relay must stop when EITHER side closes, not wait for both.

    gather() (wait for both) hangs shutdown: when uvicorn closes the browser
    side on SIGTERM, ttyd keeps streaming so ttyd_to_client never ends, the
    handler never returns, and uvicorn's "waiting for connections to close"
    phase runs until systemd SIGKILLs the process at TimeoutStopSec.
    """
    source = inspect.getsource(terminal_ws_proxy)
    assert "FIRST_COMPLETED" in source, (
        "relay must use asyncio.wait(..., return_when=FIRST_COMPLETED) and "
        "cancel the other direction — gather() waits for both and hangs shutdown"
    )
    assert "await asyncio.gather(client_to_ttyd(), ttyd_to_client())" not in source


def test_ws_relay_handles_disconnect_message():
    """client_to_ttyd must return on a websocket.disconnect message.

    Uvicorn delivers {"type": "websocket.disconnect"} when it closes
    connections during shutdown; the relay must treat it as end-of-stream,
    not fall through and block on the next receive().
    """
    source = inspect.getsource(terminal_ws_proxy)
    assert "websocket.disconnect" in source


def test_ws_proxy_registry_exists_and_empty_when_idle():
    """Open relays are tracked so lifespan shutdown can cancel them."""
    from muxplex.main import _ws_proxy_tasks, lifespan

    assert isinstance(_ws_proxy_tasks, set)
    assert not _ws_proxy_tasks  # nothing open outside a live proxy session
    # And the lifespan shutdown actually uses the registry.
    assert "_ws_proxy_tasks" in inspect.getsource(lifespan)


# ---------------------------------------------------------------------------
# Federation WS relay: same shutdown-hang class of bug, same fix, mirrored
# regression coverage (federation_terminal_ws_proxy previously used gather()
# and never registered in _ws_proxy_tasks -- both fixed to match
# terminal_ws_proxy's already-guarded pattern above).
# ---------------------------------------------------------------------------


def test_federation_ws_relay_terminates_on_first_side_close():
    """The federation relay must stop when EITHER side closes, not wait for
    both -- same rationale as test_ws_relay_terminates_on_first_side_close.

    Before the fix, federation_terminal_ws_proxy called
    `await asyncio.gather(client_to_remote(), remote_to_client())`, which
    hangs shutdown exactly like the pre-fix local proxy did: if the browser
    disconnects, remote_to_client keeps streaming from the still-live
    remote, the handler never returns, and uvicorn's "waiting for
    connections to close" phase runs until systemd SIGKILLs the process.
    """
    from muxplex.main import federation_terminal_ws_proxy

    source = inspect.getsource(federation_terminal_ws_proxy)
    assert "FIRST_COMPLETED" in source, (
        "federation relay must use asyncio.wait(..., return_when=FIRST_COMPLETED) "
        "and cancel the other direction -- gather() waits for both and hangs shutdown"
    )
    assert "await asyncio.gather(client_to_remote(), remote_to_client())" not in source


def test_federation_ws_relay_handles_disconnect_message():
    """client_to_remote must return on a websocket.disconnect message, same
    as terminal_ws_proxy's client_to_ttyd (test_ws_relay_handles_disconnect_message)."""
    from muxplex.main import federation_terminal_ws_proxy

    source = inspect.getsource(federation_terminal_ws_proxy)
    assert "websocket.disconnect" in source


def test_federation_ws_proxy_registers_in_shared_registry():
    """federation_terminal_ws_proxy must register/deregister in the SAME
    `_ws_proxy_tasks` registry terminal_ws_proxy uses, so lifespan shutdown
    can cancel an open federation relay directly.

    Before the fix, this registration was entirely absent: an open
    federation relay was invisible to `n_relays = len(_ws_proxy_tasks)` and
    to the lifespan shutdown cancellation loop, regardless of how the two
    directions were awaited.
    """
    from muxplex.main import federation_terminal_ws_proxy

    source = inspect.getsource(federation_terminal_ws_proxy)
    assert "_ws_proxy_tasks.add(_task)" in source
    assert "_ws_proxy_tasks.discard(_task)" in source

"""
Tests for muxplex/main.py — FastAPI skeleton, lifespan, /health endpoint.
"""

import pytest
from fastapi.testclient import TestClient

from muxplex.main import app


# ---------------------------------------------------------------------------
# autouse fixture — redirect state/PID files, mock startup side-effects
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
# Client fixture — TestClient with lifespan enabled
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient that triggers the app lifespan on entry/exit.

    Sets a valid session cookie so existing tests bypass the AuthMiddleware
    (TestClient uses host='testclient', which is not a localhost address).
    """
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_returns_200(client):
    """GET /health must return HTTP 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_status(client):
    """GET /health must return JSON body {status: 'ok'}."""
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /api/state
# ---------------------------------------------------------------------------


def test_get_state_returns_full_state(client):
    """GET /api/state must return a dict with all 4 top-level keys."""
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert "active_session" in data
    assert "session_order" in data
    assert "sessions" in data
    assert "devices" in data


def test_get_state_active_session_is_none_initially(client):
    """GET /api/state active_session must be None on a fresh state."""
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert data["active_session"] is None


# ---------------------------------------------------------------------------
# PATCH /api/state
# ---------------------------------------------------------------------------


def test_patch_state_updates_session_order(client):
    """PATCH /api/state updates session_order and persists the change."""
    from muxplex.state import load_state, save_state

    # Write initial state with a known session order
    initial_state = {
        "active_session": None,
        "session_order": ["alpha", "beta"],
        "sessions": {},
        "devices": {},
    }
    save_state(initial_state)

    # Patch with reversed order
    response = client.patch("/api/state", json={"session_order": ["beta", "alpha"]})
    assert response.status_code == 200
    data = response.json()
    assert data["session_order"] == ["beta", "alpha"]

    # Verify the update was persisted to disk
    persisted = load_state()
    assert persisted["session_order"] == ["beta", "alpha"]


def test_patch_state_rejects_non_list_session_order(client):
    """PATCH /api/state rejects non-list session_order with HTTP 422."""
    response = client.patch("/api/state", json={"session_order": "not-a-list"})
    assert response.status_code == 422


def test_patch_state_ignores_unknown_fields(client):
    """PATCH /api/state ignores unknown fields in the request body."""
    response = client.patch(
        "/api/state",
        json={"session_order": ["a", "b"], "unknown_field": "should_be_ignored"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "unknown_field" not in data
    assert data["session_order"] == ["a", "b"]


# ---------------------------------------------------------------------------
# GET /api/sessions
# ---------------------------------------------------------------------------


def test_get_sessions_returns_list(client, monkeypatch):
    """GET /api/sessions must return a JSON list."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"alpha": "some text"})

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    assert items[0]["name"] == "alpha"


def test_get_sessions_each_item_has_required_fields(client, monkeypatch):
    """Each item in GET /api/sessions must have name, snapshot, and bell fields."""
    from muxplex.state import save_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["beta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"beta": "output"})
    save_state(
        {
            "active_session": None,
            "session_order": ["beta"],
            "sessions": {
                "beta": {
                    "bell": {"last_fired_at": None, "seen_at": None, "unseen_count": 0}
                }
            },
            "devices": {},
        }
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    item = items[0]
    assert "name" in item
    assert "snapshot" in item
    assert "bell" in item


def test_get_sessions_includes_snapshot_text(client, monkeypatch):
    """GET /api/sessions snapshot field must contain the cached capture-pane text."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["gamma"])
    monkeypatch.setattr(
        "muxplex.main.get_snapshots",
        lambda: {"gamma": "hello from tmux pane"},
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["name"] == "gamma"
    assert items[0]["snapshot"] == "hello from tmux pane"


def test_get_sessions_includes_bell_state(client, monkeypatch):
    """GET /api/sessions bell field must include unseen_count from persistent state."""
    from muxplex.state import save_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["delta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"delta": "pane text"})
    save_state(
        {
            "active_session": None,
            "session_order": ["delta"],
            "sessions": {
                "delta": {
                    "bell": {
                        "last_fired_at": 1234567890.0,
                        "seen_at": None,
                        "unseen_count": 3,
                    }
                }
            },
            "devices": {},
        }
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["bell"]["unseen_count"] == 3


def test_get_sessions_returns_empty_list_when_no_sessions(client, monkeypatch):
    """GET /api/sessions must return an empty list when there are no sessions."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {})

    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# POST /api/sessions/{name}/connect
# ---------------------------------------------------------------------------


def test_connect_session_returns_200(client, monkeypatch):
    """POST /api/sessions/{name}/connect returns 200 and correct body when session exists."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def mock_kill():
        return True

    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill)

    async def mock_spawn(name):
        pass

    monkeypatch.setattr("muxplex.main.spawn_ttyd", mock_spawn)

    response = client.post("/api/sessions/alpha/connect")
    assert response.status_code == 200
    data = response.json()
    assert data["active_session"] == "alpha"
    assert data["ttyd_port"] == 7682


def test_connect_session_sets_active_session(client, monkeypatch):
    """POST /api/sessions/{name}/connect persists active_session to state."""
    from muxplex.state import load_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def mock_kill():
        return True

    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill)

    async def mock_spawn(name):
        pass

    monkeypatch.setattr("muxplex.main.spawn_ttyd", mock_spawn)

    client.post("/api/sessions/alpha/connect")

    state = load_state()
    assert state["active_session"] == "alpha"


def test_connect_session_kills_existing_ttyd(client, monkeypatch):
    """POST /api/sessions/{name}/connect calls kill_ttyd then spawn_ttyd."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    call_order = []

    async def mock_kill():
        call_order.append("kill")
        return True

    async def mock_spawn(name):
        call_order.append(("spawn", name))

    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill)
    monkeypatch.setattr("muxplex.main.spawn_ttyd", mock_spawn)

    response = client.post("/api/sessions/alpha/connect")
    assert response.status_code == 200
    assert call_order == ["kill", ("spawn", "alpha")]


def test_connect_nonexistent_session_returns_404(client, monkeypatch):
    """POST /api/sessions/{name}/connect returns 404 when session is not in list."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])

    response = client.post("/api/sessions/gamma/connect")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/sessions/current
# ---------------------------------------------------------------------------


def test_delete_current_kills_ttyd_and_clears_active(client, monkeypatch):
    """DELETE /api/sessions/current kills ttyd and clears active_session."""
    from muxplex.state import load_state, save_state

    # Set up initial state with active session
    save_state(
        {
            "active_session": "alpha",
            "session_order": ["alpha"],
            "sessions": {},
            "devices": {},
        }
    )

    kill_called = []

    async def mock_kill():
        kill_called.append(True)
        return True

    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill)

    response = client.delete("/api/sessions/current")
    assert response.status_code == 200
    data = response.json()
    assert data["active_session"] is None
    assert len(kill_called) == 1

    # Verify state was persisted
    state = load_state()
    assert state["active_session"] is None


# ---------------------------------------------------------------------------
# POST /api/heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_returns_200(client):
    """POST /api/heartbeat must return HTTP 200 with device_id and status 'ok'."""
    response = client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-abc",
            "label": "My Laptop",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 1234567890.0,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["device_id"] == "dev-abc"
    assert data["status"] == "ok"


def test_heartbeat_registers_new_device(client):
    """POST /api/heartbeat registers a new device visible in GET /api/state."""
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-new",
            "label": "Test Device",
            "viewing_session": "mysession",
            "view_mode": "fullscreen",
            "last_interaction_at": 1111111111.0,
        },
    )

    state_response = client.get("/api/state")
    assert state_response.status_code == 200
    state = state_response.json()
    assert "dev-new" in state["devices"]
    device = state["devices"]["dev-new"]
    assert device["label"] == "Test Device"
    assert device["viewing_session"] == "mysession"
    assert device["view_mode"] == "fullscreen"
    assert device["last_interaction_at"] == 1111111111.0


def test_heartbeat_updates_existing_device(client):
    """Two POST /api/heartbeat calls: second values are persisted."""
    # First heartbeat
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-update",
            "label": "Old Label",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 1000000000.0,
        },
    )
    # Second heartbeat with updated values
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-update",
            "label": "New Label",
            "viewing_session": "session-x",
            "view_mode": "fullscreen",
            "last_interaction_at": 2000000000.0,
        },
    )

    state_response = client.get("/api/state")
    state = state_response.json()
    device = state["devices"]["dev-update"]
    assert device["label"] == "New Label"
    assert device["viewing_session"] == "session-x"
    assert device["view_mode"] == "fullscreen"
    assert device["last_interaction_at"] == 2000000000.0


def test_heartbeat_missing_device_id_returns_422(client):
    """POST /api/heartbeat without device_id must return HTTP 422."""
    response = client.post(
        "/api/heartbeat",
        json={
            "label": "My Laptop",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 1234567890.0,
        },
    )
    assert response.status_code == 422


def test_heartbeat_invalid_view_mode_returns_422(client):
    """POST /api/heartbeat with invalid view_mode must return HTTP 422."""
    response = client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-abc",
            "label": "My Laptop",
            "viewing_session": None,
            "view_mode": "invalid_mode",
            "last_interaction_at": 1234567890.0,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/sessions/{name}/bell
# ---------------------------------------------------------------------------


def test_receive_bell_returns_ok_and_session_name(client):
    """POST /api/sessions/{name}/bell returns {"ok": True, "session": name}."""
    response = client.post("/api/sessions/web-tmux/bell")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session"] == "web-tmux"


def test_receive_bell_increments_unseen_count(client):
    """POST /api/sessions/{name}/bell increments unseen_count in state."""
    from muxplex.state import load_state

    client.post("/api/sessions/my-session/bell")

    state = load_state()
    bell = state["sessions"]["my-session"]["bell"]
    assert bell["unseen_count"] == 1


def test_receive_bell_creates_session_entry_if_absent(client):
    """POST /api/sessions/{name}/bell creates session/bell entries if missing."""
    from muxplex.state import load_state

    # Ensure session does not exist in state yet
    client.post("/api/sessions/brand-new/bell")

    state = load_state()
    assert "brand-new" in state["sessions"]
    assert "bell" in state["sessions"]["brand-new"]


def test_receive_bell_multiple_calls_accumulate(client):
    """Three POST calls to the bell endpoint accumulate unseen_count to 3."""
    from muxplex.state import load_state

    for _ in range(3):
        client.post("/api/sessions/multi-session/bell")

    state = load_state()
    bell = state["sessions"]["multi-session"]["bell"]
    assert bell["unseen_count"] == 3


def test_receive_bell_sets_last_fired_at(client):
    """POST /api/sessions/{name}/bell sets last_fired_at to a recent timestamp."""
    import time

    from muxplex.state import load_state

    before = time.time()
    client.post("/api/sessions/timed-session/bell")
    after = time.time()

    state = load_state()
    bell = state["sessions"]["timed-session"]["bell"]
    assert bell["last_fired_at"] is not None
    assert before <= bell["last_fired_at"] <= after


# ---------------------------------------------------------------------------
# POST /api/internal/setup-hooks
# ---------------------------------------------------------------------------


def test_setup_hooks_returns_ok(client, monkeypatch):
    """POST /api/internal/setup-hooks returns {"ok": True} when tmux hook registers."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr("muxplex.main.run_tmux", AsyncMock(return_value=""))

    response = client.post("/api/internal/setup-hooks")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


def test_setup_hooks_returns_ok_false_on_error(client, monkeypatch):
    """POST /api/internal/setup-hooks returns {"ok": False} when tmux raises."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "muxplex.main.run_tmux",
        AsyncMock(side_effect=RuntimeError("tmux not found")),
    )

    response = client.post("/api/internal/setup-hooks")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "error" in data


def test_setup_hooks_curl_discards_response_body(client, monkeypatch):
    """POST /api/internal/setup-hooks passes curl with -o /dev/null to discard response."""
    from unittest.mock import AsyncMock

    mock_run_tmux = AsyncMock(return_value="")
    monkeypatch.setattr("muxplex.main.run_tmux", mock_run_tmux)

    response = client.post("/api/internal/setup-hooks")
    assert response.status_code == 200

    # Verify run_tmux was called with the correct hook command
    assert mock_run_tmux.called
    call_args = mock_run_tmux.call_args
    # Positional args are: "set-hook", "-g", "alert-bell", <hook_command>
    hook_command = call_args[0][3] if len(call_args[0]) > 3 else None
    assert hook_command is not None
    # Should have -sfo /dev/null, not just -sf
    assert "-sfo /dev/null" in hook_command


def test_lifespan_alert_bell_hook_discards_response(monkeypatch):
    """Lifespan startup registers alert-bell hook with curl -o /dev/null to discard response."""
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    from muxplex.main import app

    # Mock run_tmux to capture the hook command
    mock_run_tmux = AsyncMock(return_value="")
    monkeypatch.setattr("muxplex.main.run_tmux", mock_run_tmux)

    # Trigger lifespan by creating a TestClient
    with TestClient(app) as _:
        pass

    # Verify run_tmux was called during lifespan startup
    assert mock_run_tmux.called
    # Find the call that sets the alert-bell hook
    hook_calls = [
        call
        for call in mock_run_tmux.call_args_list
        if len(call[0]) > 3 and call[0][2] == "alert-bell"
    ]
    assert len(hook_calls) > 0, "alert-bell hook was not set during lifespan"

    # Check the first hook call
    hook_command = hook_calls[0][0][3]
    assert "-sfo /dev/null" in hook_command


# ---------------------------------------------------------------------------
# Static file serving tests
# ---------------------------------------------------------------------------


def test_root_serves_html(client):
    """GET / must return 200 with text/html content-type."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_style_css_served(client):
    """GET /style.css must return 200 with text/css content-type."""
    response = client.get("/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_api_routes_not_shadowed(client):
    """GET /api/sessions must still return 200 with JSON list (not shadowed by StaticFiles)."""
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_terminal_ws_route_exists():
    """The app must have a WebSocket route registered at /terminal/ws."""
    from fastapi.routing import APIRoute, APIWebSocketRoute

    from muxplex.main import app

    ws_routes = [
        r
        for r in app.routes
        if isinstance(r, (APIRoute, APIWebSocketRoute)) and r.path == "/terminal/ws"
    ]
    assert len(ws_routes) == 1, "Expected exactly one /terminal/ws route"


# ---------------------------------------------------------------------------
# Auth middleware integration
# ---------------------------------------------------------------------------


def test_non_localhost_without_auth_gets_redirected(monkeypatch):
    """A non-localhost request without credentials is redirected to /login."""
    from fastapi.testclient import TestClient

    from muxplex.main import app

    # Ensure auth is active — set a known password via env
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw-for-api")

    with TestClient(app, base_url="http://192.168.1.1") as c:
        response = c.get("/health", follow_redirects=False)
        # Should be redirected to /login or get 307/401
        assert response.status_code in (307, 401)


# ---------------------------------------------------------------------------
# Login stub and auth mode endpoint
# ---------------------------------------------------------------------------


def test_get_login_returns_200_html(client):
    """GET /login returns 200 with HTML content."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<form" in response.text


def test_get_auth_mode_returns_json(client):
    """GET /auth/mode returns JSON with mode field."""
    response = client.get("/auth/mode")
    assert response.status_code == 200
    data = response.json()
    assert "mode" in data
    assert data["mode"] in ("pam", "password")

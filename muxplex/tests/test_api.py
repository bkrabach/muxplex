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


def test_get_login_injects_muxplex_auth(client):
    """GET /login returns 200 with MUXPLEX_AUTH injected into HTML."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "MUXPLEX_AUTH" in response.text
    assert '"mode"' in response.text


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------


def test_post_login_correct_password_redirects_to_root(monkeypatch):
    """POST /login with correct password: 303 redirect to / with muxplex_session cookie."""
    import muxplex.main as main_module

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(main_module, "_auth_mode", "password")
    monkeypatch.setattr(main_module, "_auth_password", "test-password")

    with TestClient(app, follow_redirects=False) as c:
        response = c.post(
            "/login", data={"username": "user", "password": "test-password"}
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "muxplex_session" in response.cookies


def test_post_login_wrong_password_redirects_to_login_error(monkeypatch):
    """POST /login with wrong password: 303 redirect to /login?error=1."""
    import muxplex.main as main_module

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(main_module, "_auth_mode", "password")
    monkeypatch.setattr(main_module, "_auth_password", "test-password")

    with TestClient(app, follow_redirects=False) as c:
        response = c.post(
            "/login", data={"username": "user", "password": "wrong-password"}
        )

    assert response.status_code == 303
    assert "error=1" in response.headers["location"]


def test_post_login_pam_mode_correct_creds(monkeypatch):
    """POST /login in PAM mode with correct creds: 303 to / with muxplex_session cookie."""
    import muxplex.main as main_module

    monkeypatch.setattr(main_module, "_auth_mode", "pam")
    monkeypatch.setattr("muxplex.main.authenticate_pam", lambda u, p: True)

    with TestClient(app, follow_redirects=False) as c:
        response = c.post("/login", data={"username": "user", "password": "correct"})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "muxplex_session" in response.cookies


def test_post_login_pam_mode_wrong_creds(monkeypatch):
    """POST /login in PAM mode with wrong creds: 303 redirect to /login?error=1."""
    import muxplex.main as main_module

    monkeypatch.setattr(main_module, "_auth_mode", "pam")
    monkeypatch.setattr("muxplex.main.authenticate_pam", lambda u, p: False)

    with TestClient(app, follow_redirects=False) as c:
        response = c.post("/login", data={"username": "user", "password": "wrong"})

    assert response.status_code == 303
    assert "error=1" in response.headers["location"]


# ---------------------------------------------------------------------------
# GET /auth/logout
#
# Note: these tests intentionally bypass the shared `client` fixture.
# The `client` fixture pre-injects a valid muxplex_session cookie; these
# tests verify that logout works correctly even for an unauthenticated
# (or expired-session) request, so they create their own TestClient.
# ---------------------------------------------------------------------------


def test_logout_redirects_to_login(monkeypatch):
    """GET /auth/logout returns 303 redirect to /login."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(app, follow_redirects=False) as c:
        response = c.get("/auth/logout")

    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_logout_clears_session_cookie(monkeypatch):
    """GET /auth/logout clears muxplex_session cookie (Set-Cookie with max-age=0)."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(app, follow_redirects=False) as c:
        response = c.get("/auth/logout")

    assert response.status_code == 303
    set_cookie = response.headers.get("set-cookie", "")
    assert "muxplex_session" in set_cookie
    assert "max-age=0" in set_cookie.lower()


# ---------------------------------------------------------------------------
# WebSocket auth tests
# ---------------------------------------------------------------------------


def _wrap_with_client_host(wrapped_app, host: str):
    """Return an ASGI wrapper that forces websocket scope client to `host`.

    This lets tests simulate a WebSocket connection appearing to originate
    from a specific IP without touching Starlette internals.
    """

    async def _middleware(scope, receive, send):
        if scope.get("type") == "websocket":
            scope = {**scope, "client": (host, 50000)}
        await wrapped_app(scope, receive, send)

    return _middleware


def test_ws_localhost_no_cookie_bypasses_auth():
    """WebSocket from 127.0.0.1 is accepted even without a session cookie."""
    from starlette.websockets import WebSocketDisconnect

    # Force scope to look like localhost so auth check is bypassed
    localhost_app = _wrap_with_client_host(app, "127.0.0.1")

    with TestClient(localhost_app) as c:
        try:
            with c.websocket_connect("/terminal/ws") as _:
                pass  # connection was accepted — auth bypassed for localhost
        except WebSocketDisconnect as e:
            # The websocket was accepted (auth bypassed); ttyd is not running so
            # the proxy fails and closes with a non-4001 code.
            assert e.code != 4001, (
                f"Localhost WebSocket should not be rejected; got close code {e.code}"
            )


def test_ws_valid_cookie_non_localhost_not_rejected_4001():
    """WebSocket from non-localhost with a valid cookie is not rejected with 4001."""
    from starlette.websockets import WebSocketDisconnect
    from muxplex.auth import create_session_cookie
    from muxplex.main import _auth_secret, _auth_ttl

    cookie = create_session_cookie(_auth_secret, _auth_ttl)

    # TestClient default host is "testclient" — treated as non-localhost
    with TestClient(app) as c:
        c.cookies["muxplex_session"] = cookie
        try:
            with c.websocket_connect("/terminal/ws") as _:
                pass  # connection was accepted — auth passed
        except WebSocketDisconnect as e:
            # Auth passed; ttyd not running → proxy fails → close with code != 4001
            assert e.code != 4001, (
                f"Valid-cookie WebSocket should not be rejected; got close code {e.code}"
            )


def test_ws_no_cookie_non_localhost_rejected_4001():
    """WebSocket from non-localhost without a cookie is closed with code 4001."""
    from starlette.websockets import WebSocketDisconnect

    # TestClient default host "testclient" is treated as non-localhost
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws") as _:
                pass
    assert exc_info.value.code == 4001


def test_ws_invalid_cookie_non_localhost_rejected_4001():
    """WebSocket from non-localhost with a tampered cookie is closed with code 4001."""
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as c:
        c.cookies["muxplex_session"] = "tampered.invalid.cookie.value"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws") as _:
                pass
    assert exc_info.value.code == 4001


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------


def test_get_settings_returns_defaults(client, tmp_path, monkeypatch):
    """GET /api/settings returns 200 with default settings when no file exists."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["sort_order"] == "manual"
    assert data["new_session_template"] == "tmux new-session -d -s {name}"


def test_get_settings_returns_saved_values(client, tmp_path, monkeypatch):
    """GET /api/settings returns saved values when settings.json exists."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Pre-write a settings.json with a custom sort_order
    settings_path.write_text(json.dumps({"sort_order": "alphabetical"}))

    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["sort_order"] == "alphabetical"


# ---------------------------------------------------------------------------
# PATCH /api/settings
# ---------------------------------------------------------------------------


def test_patch_settings_updates_field(client, tmp_path, monkeypatch):
    """PATCH /api/settings with {sort_order: 'alphabetical'} returns 200 with updated sort_order and unchanged default_session."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"sort_order": "alphabetical"})
    assert response.status_code == 200
    data = response.json()
    assert data["sort_order"] == "alphabetical"
    assert data["default_session"] is None


def test_patch_settings_ignores_unknown_keys(client, tmp_path, monkeypatch):
    """PATCH /api/settings with {unknown_key: 'value'} returns 200 without unknown_key."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"unknown_key": "value"})
    assert response.status_code == 200
    data = response.json()
    assert "unknown_key" not in data


# ---------------------------------------------------------------------------
# GET /api/instance-info
# ---------------------------------------------------------------------------


def test_instance_info_returns_200(client):
    """GET /api/instance-info returns 200 with name and version keys."""
    response = client.get("/api/instance-info")
    assert response.status_code == 200
    assert "name" in response.json()


def test_instance_info_returns_name_and_version(client, tmp_path, monkeypatch):
    """GET /api/instance-info returns name='test-host' and version='0.1.0' when hostname is mocked."""
    import socket

    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(socket, "gethostname", lambda: "test-host")

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-host"
    assert data["version"] == "0.1.0"


def test_instance_info_uses_explicit_device_name(client, tmp_path, monkeypatch):
    """GET /api/instance-info uses explicit device_name from settings when set."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"device_name": "My Workstation"}))

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Workstation"
    assert data["version"] == "0.1.0"


def test_instance_info_no_auth_required(tmp_path, monkeypatch):
    """GET /api/instance-info returns 200 even without an auth cookie."""
    import muxplex.settings as settings_mod

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    with TestClient(app) as c:
        # No auth cookie set — endpoint must be accessible without one
        response = c.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data


def test_instance_info_includes_federation_enabled(client, tmp_path, monkeypatch):
    """GET /api/instance-info includes federation_enabled=False when no key file exists."""
    import muxplex.settings as settings_mod

    # Redirect settings path so defaults are used
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    # Redirect federation key path to a nonexistent file
    monkeypatch.setattr(
        settings_mod, "FEDERATION_KEY_PATH", tmp_path / "nonexistent_federation_key"
    )

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert "federation_enabled" in data, (
        f"Response must include 'federation_enabled' key, got: {data}"
    )
    assert data["federation_enabled"] is False, (
        f"federation_enabled must be False when no key file exists, got: {data['federation_enabled']}"
    )


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------


def test_cors_preflight_returns_200(tmp_path, monkeypatch):
    """OPTIONS /api/sessions with CORS preflight headers returns 200 with access-control-allow-origin header.

    NOTE — Spec discrepancy: the acceptance criteria states `access-control-allow-origin: *`,
    but the middleware is configured with `allow_credentials=True`.  The CORS specification
    (RFC 6454 / Fetch standard) forbids the wildcard value when credentials are included;
    Starlette therefore reflects the request Origin instead of emitting "*".  Keeping
    `allow_credentials=True` is intentional for cross-origin federation with session cookies,
    so the assertion uses the reflected origin rather than a literal "*".
    """
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    origin = "http://other-muxplex.local:8088"
    with TestClient(app) as c:
        response = c.options(
            "/api/sessions",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_allows_any_origin(client):
    """GET /api/sessions with Origin header gets access-control-allow-origin header in response.

    NOTE — Spec discrepancy: the acceptance criteria states `access-control-allow-origin: *`,
    but this is incompatible with `allow_credentials=True` (CORS spec forbids the two together).
    Starlette reflects the request Origin instead.  This provides equivalent permissiveness
    (any origin is allowed) while remaining spec-compliant.
    """
    origin = "http://other-muxplex.local:8088"
    response = client.get(
        "/api/sessions",
        headers={"Origin": origin},
    )
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_allows_credentials(client):
    """GET /api/sessions with Origin header includes access-control-allow-credentials: true."""
    response = client.get(
        "/api/sessions",
        headers={"Origin": "http://other-muxplex.local:8088"},
    )
    assert response.headers.get("access-control-allow-credentials") == "true"


# ---------------------------------------------------------------------------
# POST /api/sessions (create new session)
# ---------------------------------------------------------------------------


def test_create_session_returns_200_with_name(client, monkeypatch):
    """POST /api/sessions with valid name returns 200 with {name: name}."""
    from unittest.mock import MagicMock

    mock_popen = MagicMock()
    monkeypatch.setattr("muxplex.main.subprocess.Popen", mock_popen)

    response = client.post("/api/sessions", json={"name": "my-project"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "my-project"


def test_create_session_substitutes_name_in_template(client, tmp_path, monkeypatch):
    """POST /api/sessions substitutes {name} with actual name in new_session_template."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"new_session_template": "echo {name}"}))

    popen_calls = []

    def mock_popen(cmd, **kwargs):
        popen_calls.append(cmd)
        return object()

    monkeypatch.setattr("muxplex.main.subprocess.Popen", mock_popen)

    response = client.post("/api/sessions", json={"name": "my-project"})
    assert response.status_code == 200
    assert len(popen_calls) == 1
    assert popen_calls[0] == "echo my-project"


def test_create_session_rejects_empty_name(client):
    """POST /api/sessions with empty name returns 422."""
    response = client.post("/api/sessions", json={"name": ""})
    assert response.status_code == 422


def test_create_session_rejects_missing_name(client):
    """POST /api/sessions with missing name returns 422."""
    response = client.post("/api/sessions", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/sessions/{name}
# ---------------------------------------------------------------------------


def test_delete_session_success(client, monkeypatch):
    """DELETE /api/sessions/{name} returns 200 with {ok: True, name: name} when session exists."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["my-session", "other"]
    )
    monkeypatch.setattr("muxplex.main.run_tmux", AsyncMock(return_value=""))

    response = client.delete("/api/sessions/my-session")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["name"] == "my-session"


def test_delete_session_calls_kill_session(client, monkeypatch, tmp_path):
    """DELETE /api/sessions/{name} runs 'tmux kill-session -t {name}' via subprocess (default template)."""
    import muxplex.settings as settings_mod
    from unittest.mock import MagicMock, patch

    # Redirect settings to a non-existent path so the default template is used
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "no-settings.json")

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["my-session"])

    captured = []

    def mock_run(cmd, **kwargs):
        captured.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        client.delete("/api/sessions/my-session")

    assert len(captured) == 1, "subprocess.run must be called exactly once"
    executed_cmd = captured[0]
    assert "kill-session" in executed_cmd, (
        f"Default command must include 'kill-session', got: {executed_cmd!r}"
    )
    assert "-t" in executed_cmd, (
        f"Default command must include '-t', got: {executed_cmd!r}"
    )
    assert "my-session" in executed_cmd, (
        f"Command must include session name 'my-session', got: {executed_cmd!r}"
    )


def test_delete_session_not_found(client, monkeypatch):
    """DELETE /api/sessions/{name} returns 404 when session is not in list."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])

    response = client.delete("/api/sessions/nonexistent")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Issue 1: Static assets exempt from auth middleware
# ---------------------------------------------------------------------------


def test_static_asset_accessible_from_non_localhost_without_auth(monkeypatch):
    """Static assets (.svg, .css, .js etc.) are served without auth from non-localhost.

    The login page needs its own CSS/JS/images to render before the user has
    authenticated. The auth middleware must exempt static file extensions.
    """
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw")
    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        response = c.get("/wordmark-on-dark.svg")
    assert response.status_code == 200, (
        f"Expected 200 for static asset from non-localhost, got {response.status_code}"
    )


def test_css_asset_accessible_from_non_localhost_without_auth(monkeypatch):
    """CSS files are served without auth from non-localhost."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw")
    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        response = c.get("/style.css")
    assert response.status_code == 200, (
        f"Expected 200 for CSS from non-localhost, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Issue 2: Hostname in page title
# ---------------------------------------------------------------------------


def test_index_page_title_contains_hostname(client):
    """GET / returns HTML with hostname in page title (e.g. 'myhost — muxplex')."""
    import socket

    hostname = socket.gethostname().split(".")[0]
    response = client.get("/")
    assert response.status_code == 200
    assert hostname in response.text, (
        f"Expected hostname '{hostname}' in title of index page"
    )
    assert "muxplex" in response.text


def test_login_page_title_contains_hostname(client):
    """GET /login returns HTML with hostname in page title (e.g. 'Sign in — myhost — muxplex')."""
    import socket

    hostname = socket.gethostname().split(".")[0]
    response = client.get("/login")
    assert response.status_code == 200
    assert hostname in response.text, (
        f"Expected hostname '{hostname}' in title of login page"
    )


# ---------------------------------------------------------------------------
# DELETE /api/sessions/{name} — custom template (task: customizable delete command)
# ---------------------------------------------------------------------------


def test_delete_session_uses_template_command(client, monkeypatch, tmp_path):
    """DELETE /api/sessions/{name} must execute the delete_session_template from settings.

    The template {name} placeholder must be substituted with the session name.
    The command must be run synchronously via subprocess.run (not run_tmux).
    """
    from unittest.mock import MagicMock, patch

    # Make the session appear to exist so the 404 guard passes
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["myworkspace"])

    # Redirect settings to a temp path so we can write a custom template
    import muxplex.settings as settings_mod

    fake_settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_settings_path)

    # Write a custom template
    import json

    fake_settings_path.write_text(
        json.dumps(
            {
                "delete_session_template": "echo destroy {name}",
            }
        )
    )

    # Capture subprocess.run calls
    captured_commands = []

    def mock_run(cmd, **kwargs):
        captured_commands.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        response = client.delete("/api/sessions/myworkspace")

    assert response.status_code == 200, (
        f"DELETE /api/sessions/myworkspace must return 200, got {response.status_code}"
    )
    data = response.json()
    assert data.get("ok") is True, f"Response must have ok=True, got: {data}"
    assert data.get("name") == "myworkspace", (
        f"Response must have name='myworkspace', got: {data}"
    )

    # Verify template substitution happened
    assert len(captured_commands) == 1, (
        f"subprocess.run must be called exactly once, called {len(captured_commands)} times"
    )
    executed_cmd = captured_commands[0]
    assert "myworkspace" in executed_cmd, (
        f"Executed command must contain session name 'myworkspace', got: {executed_cmd!r}"
    )
    assert "echo destroy" in executed_cmd, (
        f"Executed command must use the custom template, got: {executed_cmd!r}"
    )


def test_delete_session_default_template_is_tmux_kill(client, monkeypatch, tmp_path):
    """DELETE /api/sessions/{name} uses 'tmux kill-session -t {name}' when no custom template is set."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["mysession"])

    # Redirect settings to empty temp file (no settings file = use defaults)
    import muxplex.settings as settings_mod

    fake_settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_settings_path)
    # Don't write any settings — defaults should be used

    captured_commands = []

    def mock_run(cmd, **kwargs):
        captured_commands.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        response = client.delete("/api/sessions/mysession")

    assert response.status_code == 200
    assert len(captured_commands) == 1
    executed_cmd = captured_commands[0]
    # Default template substituted
    assert "mysession" in executed_cmd, (
        f"Default template must substitute session name, got: {executed_cmd!r}"
    )
    assert "kill-session" in executed_cmd, (
        f"Default template must contain 'kill-session', got: {executed_cmd!r}"
    )


# ---------------------------------------------------------------------------
# GET /api/auth/token  (cross-origin federation token relay)
# ---------------------------------------------------------------------------


def test_get_auth_token_returns_token_when_authenticated(client):
    """GET /api/auth/token returns {token: <value>} when request has a valid session cookie."""
    response = client.get("/api/auth/token")
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert isinstance(data["token"], str)
    assert len(data["token"]) > 0


def test_get_auth_token_returns_401_when_not_authenticated(monkeypatch):
    """GET /api/auth/token returns 401 when request has no valid session cookie."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app, base_url="http://192.168.1.1") as c:
        # No cookie set — endpoint must return 401 with application/json accept
        response = c.get("/api/auth/token", headers={"Accept": "application/json"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Federation Bearer token auth
# ---------------------------------------------------------------------------


def test_federation_bearer_auth_accepted(monkeypatch):
    """Request with valid Bearer token gets 200 on /api/sessions when federation key is set.

    Patches the federation key on the AuthMiddleware instance (since the key is
    loaded once at module startup) and verifies that a Bearer-authenticated
    request reaches /api/sessions with HTTP 200.

    Before implementation: fails with ImportError — _federation_key not in main.py.
    After implementation: _federation_key exists, middleware is found and patched,
    Bearer request is accepted.
    """
    from muxplex.main import _federation_key  # ImportError before implementation
    from muxplex.auth import AuthMiddleware
    import muxplex.main as main_module

    federation_key = "test-federation-key-abc123"
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(main_module.app) as c:
        # Traverse the compiled middleware stack to find the AuthMiddleware instance
        stack = main_module.app.middleware_stack
        auth_mw = None
        for _ in range(20):
            if isinstance(stack, AuthMiddleware):
                auth_mw = stack
                break
            stack = getattr(stack, "app", None)
            if stack is None:
                break

        assert auth_mw is not None, "AuthMiddleware not found in middleware stack"
        # Patch the federation key so Bearer token auth is enabled
        auth_mw.federation_key = federation_key

        # A request with a matching Bearer token must pass auth and get 200
        response = c.get(
            "/api/sessions",
            headers={"Authorization": f"Bearer {federation_key}"},
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# httpx.AsyncClient federation client on app.state (task-7)
# ---------------------------------------------------------------------------


def test_federation_client_exists_on_app_state(monkeypatch):
    """app.state.federation_client is set during lifespan and is not None.

    Verifies that the lifespan creates an httpx.AsyncClient and attaches it to
    app.state.federation_client before the application begins serving requests.
    """
    import httpx

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(app) as c:
        # Inside the context manager the lifespan has completed startup,
        # so app.state.federation_client must be set.
        assert hasattr(app.state, "federation_client"), (
            "app.state.federation_client must be set during lifespan startup"
        )
        assert app.state.federation_client is not None, (
            "app.state.federation_client must not be None"
        )
        assert isinstance(app.state.federation_client, httpx.AsyncClient), (
            "app.state.federation_client must be an httpx.AsyncClient instance"
        )
        # Capture reference before lifespan shuts down
        client_ref = app.state.federation_client

    # After lifespan shutdown completes, the client must be closed
    assert client_ref.is_closed, (
        "app.state.federation_client must be closed after lifespan shutdown"
    )


# ---------------------------------------------------------------------------
# GET /api/federation/sessions (task-8)
# ---------------------------------------------------------------------------


def test_federation_sessions_returns_local_sessions(client, monkeypatch, tmp_path):
    """GET /api/federation/sessions returns local sessions tagged with deviceName and remoteId=None.

    Local sessions must have:
    - deviceName from settings device_name
    - remoteId set to None
    - The session fields (name, snapshot, bell) from local /api/sessions
    """
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Write settings with a known device_name and no remote instances
    import json
    settings_path.write_text(json.dumps({"device_name": "my-workstation", "remote_instances": []}))

    # Mock local session data
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["session-one"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"session-one": "pane text"})

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    # Must return a list
    assert isinstance(data, list)

    # Find local sessions (remoteId=None)
    local_sessions = [s for s in data if s.get("remoteId") is None]
    assert len(local_sessions) == 1, f"Expected 1 local session, got: {local_sessions}"

    local = local_sessions[0]
    assert local["name"] == "session-one"
    assert local["deviceName"] == "my-workstation"
    assert local["remoteId"] is None


def test_federation_sessions_includes_remote_failure_status(client, monkeypatch, tmp_path):
    """GET /api/federation/sessions includes a status entry for unreachable remotes.

    When a remote instance cannot be reached (connection error), the result must
    include a status entry with status='unreachable' for that remote.
    """
    import json

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Configure one remote instance that will fail
    settings_path.write_text(json.dumps({
        "device_name": "local-host",
        "remote_instances": [
            {"url": "http://remote-host:8088", "key": "abc123", "name": "remote-host", "id": "remote-1"}
        ],
    }))

    # Mock local sessions (empty for simplicity)
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {})

    # Patch the federation_client to raise a ConnectError (unreachable)
    from unittest.mock import AsyncMock, MagicMock

    async def mock_get(url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    # Must return a list
    assert isinstance(data, list)

    # Find the failure status entry for the remote
    failure_entries = [s for s in data if s.get("status") in ("unreachable", "auth_failed")]
    assert len(failure_entries) == 1, (
        f"Expected 1 failure status entry, got: {failure_entries}. Full data: {data}"
    )
    entry = failure_entries[0]
    assert entry["status"] == "unreachable"
    assert entry.get("remoteId") == "remote-1"

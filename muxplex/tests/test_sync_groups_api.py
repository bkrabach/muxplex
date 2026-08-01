"""
Tests for sync-group endpoint contracts (main.py) — the six endpoints that
gained an optional device_id: GET/PATCH /api/state, GET /api/view,
POST /api/sessions/{name}/connect, DELETE /api/sessions/current,
POST /api/heartbeat. Covers the §10.2 test plan from the sync-groups spec.
"""

import pytest
from fastapi.testclient import TestClient

from muxplex.main import app


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Redirect state/PID files to tmp_path, mock startup side-effects."""
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)

    tmp_pid_dir = tmp_path / "ttyd"
    tmp_pid_path = tmp_pid_dir / "ttyd.pid"
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_DIR", tmp_pid_dir)
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_PATH", tmp_pid_path)

    async def _mock_kill_orphan():
        return False

    monkeypatch.setattr("muxplex.main.kill_orphan_ttyd", _mock_kill_orphan)

    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)

    # Neuter ttyd process management entirely -- these tests exercise the
    # terminal-claim GATE logic, never a real ttyd/tmux process.
    async def _mock_kill_ttyd():
        return True

    async def _mock_spawn_ttyd(name):
        class _FakeProc:
            pid = 99999

        return _FakeProc()

    monkeypatch.setattr("muxplex.main.kill_ttyd", _mock_kill_ttyd)
    monkeypatch.setattr("muxplex.main.spawn_ttyd", _mock_spawn_ttyd)
    monkeypatch.setattr("muxplex.main._ttyd_is_listening", lambda: False)
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["sessX", "sessY"])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


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


# ---------------------------------------------------------------------------
# 14: no-device_id regression armor
# ---------------------------------------------------------------------------


def test_get_state_no_device_id_unchanged(client):
    res = client.get("/api/state")
    assert res.status_code == 200
    data = res.json()
    assert data["active_session"] is None
    assert data["sync_group"] == "global"


def test_patch_state_no_device_id_unchanged(client):
    res = client.patch("/api/state", json={"active_view": "hidden"})
    assert res.status_code == 200
    assert res.json()["active_view"] == "hidden"
    assert res.json()["sync_group"] == "global"


def test_get_view_no_device_id_unchanged(client):
    res = client.get("/api/view")
    assert res.status_code == 200
    assert res.json()["sync_group"] == "global"


# ---------------------------------------------------------------------------
# 15: unknown device_id -> 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/state?device_id=nope"),
        ("get", "/api/view?device_id=nope"),
    ],
)
def test_unknown_device_id_404_get_endpoints(client, method, path):
    res = getattr(client, method)(path)
    assert res.status_code == 404


def test_unknown_device_id_404_patch_state(client):
    res = client.patch("/api/state?device_id=nope", json={"active_view": "hidden"})
    assert res.status_code == 404


def test_unknown_device_id_404_connect(client):
    res = client.post("/api/sessions/sessX/connect?device_id=nope")
    assert res.status_code == 404


def test_unknown_device_id_404_delete_current(client):
    res = client.delete("/api/sessions/current?device_id=nope")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 16-17: PATCH /api/state group scoping
# ---------------------------------------------------------------------------


def test_patch_state_private_active_view_does_not_touch_top_level(client):
    _heartbeat(client, "d1", "device:d1")
    res = client.patch("/api/state?device_id=d1", json={"active_view": "hidden"})
    assert res.status_code == 200
    assert res.json()["active_view"] == "hidden"

    global_state = client.get("/api/state").json()
    assert global_state["active_view"] == "all"
    assert global_state["sync_groups"]["device:d1"]["active_view"] == "hidden"


def test_patch_state_session_order_always_top_level(client):
    _heartbeat(client, "d1", "device:d1")
    res = client.patch("/api/state?device_id=d1", json={"session_order": ["a", "b"]})
    assert res.status_code == 200
    global_state = client.get("/api/state").json()
    assert global_state["session_order"] == ["a", "b"]


# ---------------------------------------------------------------------------
# 18: GET /api/state projects the resolved group
# ---------------------------------------------------------------------------


def test_get_state_projects_group_values(client):
    _heartbeat(client, "d1", "device:d1")
    client.patch(
        "/api/state?device_id=d1",
        json={"active_view": "hidden", "active_session": None},
    )

    res = client.get("/api/state?device_id=d1")
    data = res.json()
    assert data["active_view"] == "hidden"
    assert data["sync_group"] == "device:d1"

    # global unaffected
    assert client.get("/api/state").json()["active_view"] == "all"


# ---------------------------------------------------------------------------
# 19: GET /api/view filters by the resolved group
# ---------------------------------------------------------------------------


def test_get_view_filters_by_group(client):
    _heartbeat(client, "d1", "device:d1")
    client.patch("/api/state?device_id=d1", json={"active_session": "sessX"})

    res = client.get("/api/view?device_id=d1")
    assert res.status_code == 200
    data = res.json()
    assert data["sync_group"] == "device:d1"
    session_names = {s["name"]: s["active"] for s in data["sessions"]}
    assert session_names.get("sessX") is True


# ---------------------------------------------------------------------------
# 20-21: heartbeat sync_group validation
# ---------------------------------------------------------------------------


def test_heartbeat_rejects_someone_elses_group(client):
    res = _heartbeat(client, "d1", "device:some-other-device")
    assert res.status_code == 400


def test_heartbeat_accepts_own_group_and_seeds_from_global(client):
    client.patch("/api/state", json={"active_view": "hidden"})
    res = _heartbeat(client, "d1", "device:d1")
    assert res.status_code == 200
    assert res.json()["sync_group"] == "device:d1"

    state = client.get("/api/state").json()
    assert state["sync_groups"]["device:d1"]["active_view"] == "hidden"


def test_heartbeat_omitting_sync_group_does_not_reset_private_device(client):
    _heartbeat(client, "d1", "device:d1")
    res = _heartbeat(client, "d1", None)
    assert res.status_code == 200
    assert res.json()["sync_group"] == "device:d1"


# ---------------------------------------------------------------------------
# 22-23: terminal-claim gate on connect
# ---------------------------------------------------------------------------


def test_connect_conflict_returns_409_no_state_write(monkeypatch, client):
    _heartbeat(client, "d1", "device:d1")
    r1 = client.post("/api/sessions/sessX/connect?device_id=d1")
    assert r1.status_code == 200

    spawn_called = {"count": 0}

    async def _tracked_spawn(name):
        spawn_called["count"] += 1

        class _FakeProc:
            pid = 1

        return _FakeProc()

    monkeypatch.setattr("muxplex.main.spawn_ttyd", _tracked_spawn)

    # Global (no device_id) tries to open a DIFFERENT session -> conflict
    r2 = client.post("/api/sessions/sessY/connect")
    assert r2.status_code == 409
    body = r2.json()["detail"]
    assert body["terminal_conflict"] is True
    assert body["terminal_session"] == "sessX"
    assert body["terminal_group"] == "device:d1"
    assert spawn_called["count"] == 0

    # terminal_session must still be sessX
    state = client.get("/api/state").json()
    assert state["terminal_session"] == "sessX"


def test_connect_takeover_succeeds(client):
    _heartbeat(client, "d1", "device:d1")
    client.post("/api/sessions/sessX/connect?device_id=d1")

    r2 = client.post("/api/sessions/sessY/connect?takeover=true")
    assert r2.status_code == 200
    assert r2.json()["terminal_session"] == "sessY"

    state = client.get("/api/state").json()
    assert state["terminal_group"] == "global"


# ---------------------------------------------------------------------------
# 24: DELETE /api/sessions/current when caller doesn't hold the terminal
# ---------------------------------------------------------------------------


def test_delete_current_non_owner_does_not_kill_ttyd(monkeypatch, client):
    _heartbeat(client, "d1", "device:d1")
    client.post("/api/sessions/sessX/connect?device_id=d1")

    kill_called = {"count": 0}

    async def _tracked_kill():
        kill_called["count"] += 1
        return True

    monkeypatch.setattr("muxplex.main.kill_ttyd", _tracked_kill)

    # global does not hold the terminal (device:d1 does)
    res = client.delete("/api/sessions/current")
    assert res.status_code == 200
    assert res.json()["terminal_released"] is False
    assert kill_called["count"] == 0

    # caller's own (global) active_session is still cleared
    assert client.get("/api/state").json()["active_session"] is None

    # terminal_session/group untouched
    state = client.get("/api/state").json()
    assert state["terminal_session"] == "sessX"
    assert state["terminal_group"] == "device:d1"


def test_delete_current_owner_kills_ttyd(monkeypatch, client):
    client.post("/api/sessions/sessX/connect")

    kill_called = {"count": 0}

    async def _tracked_kill():
        kill_called["count"] += 1
        return True

    monkeypatch.setattr("muxplex.main.kill_ttyd", _tracked_kill)

    res = client.delete("/api/sessions/current")
    assert res.status_code == 200
    assert res.json()["terminal_released"] is True
    assert kill_called["count"] == 1

    state = client.get("/api/state").json()
    assert state["terminal_session"] is None
    assert state["terminal_group"] == "global"

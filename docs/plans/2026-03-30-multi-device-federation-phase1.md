# Multi-Device Federation — Phase 1: Cleanup + Backend Foundation

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Prepare the backend for multi-device federation by removing dead files, extending settings with federation fields, adding a public `/api/instance-info` route, enabling CORS, and expanding WebSocket proxy test coverage.

**Architecture:** The browser-side federation model requires each muxplex instance to (a) know its own display name and version, (b) allow cross-origin requests, and (c) expose a lightweight public metadata endpoint. This phase adds those backend primitives without touching the frontend. The existing `settings.py` defaults-merge pattern absorbs the new fields with zero migration.

**Tech Stack:** Python 3.11+, FastAPI, pytest, Starlette `CORSMiddleware`

**Design doc:** `docs/plans/2026-03-30-multi-device-federation-design.md`

---

## Orientation — What You Need to Know

### Project layout

```
muxplex/                    ← project root (run all commands from here)
├── pyproject.toml           ← deps, pytest config (asyncio_mode = "auto")
├── Caddyfile                ← DEAD FILE — deleting in Task 1
├── requirements.txt         ← DEAD FILE — deleting in Task 2
└── muxplex/                 ← Python package
    ├── main.py              ← FastAPI app, routes, WebSocket proxy
    ├── settings.py          ← DEFAULT_SETTINGS dict, load/save/patch
    ├── auth.py              ← AuthMiddleware, _AUTH_EXEMPT_PATHS set
    ├── ttyd.py              ← TTYD_PORT = 7682
    └── tests/
        ├── test_api.py      ← 1068 lines, uses `client` fixture w/ auth cookie
        ├── test_settings.py ← 144 lines, redirect_settings_path fixture
        ├── test_auth.py     ← 449 lines, _make_test_app pattern
        └── test_ws_proxy.py ← 28 lines — expanding in Task 7
```

### Test conventions

- **Run tests:** `cd muxplex && pytest muxplex/tests/ -v` (or a specific file/test)
- **Async mode:** `asyncio_mode = "auto"` — any `async def test_*` runs automatically
- **Fixtures:** `autouse=True` fixtures redirect state/settings paths to `tmp_path`
- **Auth in tests:** The `client` fixture in `test_api.py` creates a `TestClient(app)` context manager, generates a real signed cookie via `create_session_cookie(_auth_secret, _auth_ttl)`, and sets it on `c.cookies`
- **Monkeypatching:** Patches at the **import site** — e.g. `monkeypatch.setattr("muxplex.main.get_session_list", ...)`, not `"muxplex.sessions.get_session_list"`

### Key constants you'll encounter

| Constant | Location | Value |
|----------|----------|-------|
| `_AUTH_EXEMPT_PATHS` | `muxplex/auth.py:137` | `{"/login", "/auth/mode", "/auth/logout"}` |
| `SETTINGS_PATH` | `muxplex/settings.py:11` | `Path.home() / ".config" / "muxplex" / "settings.json"` |
| `DEFAULT_SETTINGS` | `muxplex/settings.py:13-20` | 6-key dict (sort_order, hidden_sessions, etc.) |
| `TTYD_PORT` | `muxplex/ttyd.py:32` | `7682` |
| App version | `muxplex/main.py:191` | `"0.1.0"` |

---

## Task 1: Delete Caddyfile

**Files:**
- Delete: `Caddyfile` (project root)

**Why:** Dead artifact. The WebSocket proxy is fully built into FastAPI (`main.py` lines 506-559). The Caddyfile was from an earlier architecture and does nothing.

**Step 1: Verify the file exists and is what we expect**

Run:
```bash
cat Caddyfile
```

Expected: An 11-line Caddy config with `:8088`, `/terminal/*` reverse proxy rules. Confirms this is the dead artifact, not something secretly important.

**Step 2: Delete the file**

Run:
```bash
rm Caddyfile
```

**Step 3: Run tests to confirm nothing depended on it**

Run:
```bash
cd muxplex && pytest muxplex/tests/ -x -q
```

Expected: All tests pass. No test references Caddyfile.

**Step 4: Commit**

Run:
```bash
git add Caddyfile && git commit -m "chore: remove dead Caddyfile

The WebSocket proxy is built into FastAPI (main.py). Caddy is no longer
used. Removing the stale config to avoid confusion."
```

---

## Task 2: Delete requirements.txt

**Files:**
- Delete: `requirements.txt` (project root)

**Why:** Contains only a 6-line comment pointing to `pyproject.toml`. It's a lie on disk — anyone running `pip install -r requirements.txt` gets nothing.

**Step 1: Verify contents**

Run:
```bash
cat requirements.txt
```

Expected: A comment saying "Dependencies are now managed in pyproject.toml" and nothing else.

**Step 2: Delete the file**

Run:
```bash
rm requirements.txt
```

**Step 3: Run tests to confirm nothing depended on it**

Run:
```bash
cd muxplex && pytest muxplex/tests/ -x -q
```

Expected: All tests pass.

**Step 4: Commit**

Run:
```bash
git add requirements.txt && git commit -m "chore: remove stale requirements.txt

Dependencies live in pyproject.toml. The file contained only a comment
pointing there — removing to avoid stale breadcrumbs."
```

---

## Task 3: Extend DEFAULT_SETTINGS with federation fields

**Files:**
- Modify: `muxplex/settings.py` (lines 1-20)
- Test: `muxplex/tests/test_settings.py`

**What:** Add two new keys to `DEFAULT_SETTINGS`:
- `"remote_instances": []` — list of `{url, name}` dicts for peer instances
- `"device_name": ""` — this instance's display name, defaults to hostname at read time

The `device_name` default is `""` in the dict (so JSON serialization is clean), but `load_settings()` replaces `""` with `socket.gethostname()` after loading — this way the hostname is always current even if the machine is renamed.

**Step 1: Write the failing tests**

Add these tests at the bottom of `muxplex/tests/test_settings.py`:

```python
# ---------------------------------------------------------------------------
# Federation field tests
# ---------------------------------------------------------------------------


def test_defaults_include_remote_instances():
    """DEFAULT_SETTINGS must include remote_instances as an empty list."""
    assert "remote_instances" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["remote_instances"] == []


def test_defaults_include_device_name():
    """DEFAULT_SETTINGS must include device_name as an empty string."""
    assert "device_name" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["device_name"] == ""


def test_load_returns_hostname_when_device_name_empty(monkeypatch):
    """load_settings() fills empty device_name with the system hostname."""
    monkeypatch.setattr("muxplex.settings.socket.gethostname", lambda: "my-laptop")

    result = load_settings()

    assert result["device_name"] == "my-laptop"


def test_load_preserves_explicit_device_name(redirect_settings_path, monkeypatch):
    """load_settings() keeps a user-set device_name (does not overwrite with hostname)."""
    import json

    monkeypatch.setattr("muxplex.settings.socket.gethostname", lambda: "my-laptop")
    redirect_settings_path.write_text(json.dumps({"device_name": "Work PC"}))

    result = load_settings()

    assert result["device_name"] == "Work PC"


def test_remote_instances_round_trip(redirect_settings_path):
    """remote_instances survive a save/load cycle unchanged."""
    instances = [
        {"url": "http://workstation:8088", "name": "Workstation"},
        {"url": "https://devserver:8088", "name": "Dev Server"},
    ]
    save_settings({"remote_instances": instances})

    result = load_settings()

    assert result["remote_instances"] == instances


def test_device_name_round_trip(redirect_settings_path, monkeypatch):
    """An explicit device_name survives a save/load cycle."""
    monkeypatch.setattr("muxplex.settings.socket.gethostname", lambda: "fallback-host")
    save_settings({"device_name": "My Server"})

    result = load_settings()

    assert result["device_name"] == "My Server"


def test_load_does_not_mutate_default_remote_instances():
    """Mutating loaded remote_instances must not corrupt DEFAULT_SETTINGS."""
    result = load_settings()
    result["remote_instances"].append({"url": "http://evil:8088", "name": "Evil"})

    assert DEFAULT_SETTINGS["remote_instances"] == []

    result2 = load_settings()
    assert result2["remote_instances"] == []
```

**Step 2: Run tests to verify they fail**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_settings.py -v -k "federation or remote_instances or device_name or hostname"
```

Expected: All 7 new tests FAIL with `KeyError: 'remote_instances'` or `KeyError: 'device_name'` or `AssertionError`.

**Step 3: Implement the settings changes**

In `muxplex/settings.py`, make these changes:

1. Add `import socket` to the imports (after `import json`):

```python
import copy
import json
import socket
from pathlib import Path
```

2. Extend `DEFAULT_SETTINGS` — add the two new keys at the end of the dict:

```python
DEFAULT_SETTINGS: dict = {
    "default_session": None,
    "sort_order": "manual",
    "hidden_sessions": [],
    "window_size_largest": False,
    "auto_open_created": True,
    "new_session_template": "tmux new-session -d -s {name}",
    "remote_instances": [],
    "device_name": "",
}
```

3. In `load_settings()`, add hostname fallback **after** the merge loop. Insert these 2 lines right before `return result`:

```python
def load_settings() -> dict:
    """Load settings from disk, merging saved values over defaults.

    Returns DEFAULT_SETTINGS if the file does not exist or contains corrupt JSON.
    Unknown keys in the file are ignored.
    """
    result = copy.deepcopy(DEFAULT_SETTINGS)
    try:
        text = SETTINGS_PATH.read_text()
        data = json.loads(text)
        for key in DEFAULT_SETTINGS:
            if key in data:
                result[key] = data[key]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Hostname fallback: empty device_name → current hostname
    if not result["device_name"]:
        result["device_name"] = socket.gethostname()
    return result
```

**Step 4: Run tests to verify they pass**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_settings.py -v
```

Expected: ALL tests pass (existing 8 + new 7 = 15 total).

**Step 5: Commit**

Run:
```bash
cd muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: add remote_instances and device_name to settings

Extends DEFAULT_SETTINGS with:
- remote_instances: [] — list of {url, name} for peer muxplex instances
- device_name: '' — this instance's display name, falls back to hostname

The hostname fallback happens at load time so it stays current even if
the machine is renamed. Explicit values are preserved."
```

---

## Task 4: Add /api/instance-info to auth exempt paths

**Files:**
- Modify: `muxplex/auth.py` (line 137)
- Test: `muxplex/tests/test_auth.py`

**What:** Add `"/api/instance-info"` to the `_AUTH_EXEMPT_PATHS` set so the endpoint can be called without authentication. This must happen before the route itself is created (Task 5), because the route handler assumes no auth.

**Step 1: Write the failing test**

Add this test at the end of `muxplex/tests/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# Auth exempt paths — federation
# ---------------------------------------------------------------------------


def test_middleware_instance_info_path_excluded():
    """/api/instance-info is excluded from auth (public metadata endpoint)."""
    app = _make_test_app()

    @app.get("/api/instance-info")
    async def instance_info():
        return PlainTextResponse("info")

    client = TestClient(app, base_url="http://192.168.1.1")
    response = client.get("/api/instance-info")
    assert response.status_code == 200
```

**Step 2: Run the test to verify it fails**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_auth.py::test_middleware_instance_info_path_excluded -v
```

Expected: FAIL — response is 307 redirect to `/login` because `/api/instance-info` is not in the exempt set yet.

**Step 3: Add the path to the exempt set**

In `muxplex/auth.py`, change line 137 from:

```python
_AUTH_EXEMPT_PATHS = {"/login", "/auth/mode", "/auth/logout"}
```

to:

```python
_AUTH_EXEMPT_PATHS = {"/login", "/auth/mode", "/auth/logout", "/api/instance-info"}
```

**Step 4: Run the test to verify it passes**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_auth.py::test_middleware_instance_info_path_excluded -v
```

Expected: PASS.

**Step 5: Run full auth test suite to check for regressions**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_auth.py -v
```

Expected: All tests pass.

**Step 6: Commit**

Run:
```bash
cd muxplex && git add muxplex/auth.py muxplex/tests/test_auth.py && git commit -m "feat: exempt /api/instance-info from auth

The instance-info endpoint is public metadata (like a health check).
Remote instances need to call it without a session cookie to discover
peer names and verify reachability."
```

---

## Task 5: Add GET /api/instance-info route

**Files:**
- Modify: `muxplex/main.py` (add route after the `/api/settings` routes, before the WebSocket proxy section)
- Test: `muxplex/tests/test_api.py`

**What:** `GET /api/instance-info` returns `{"name": "<device_name>", "version": "0.1.0"}`. No auth required (handled by Task 4). The route reads `device_name` from `load_settings()` and the version from `app.version`.

**Step 1: Write the failing tests**

Add these tests in `muxplex/tests/test_api.py`, after the `PATCH /api/settings` test section (after line ~968):

```python
# ---------------------------------------------------------------------------
# GET /api/instance-info
# ---------------------------------------------------------------------------


def test_instance_info_returns_200(client):
    """GET /api/instance-info returns HTTP 200."""
    response = client.get("/api/instance-info")
    assert response.status_code == 200


def test_instance_info_returns_name_and_version(client, tmp_path, monkeypatch):
    """GET /api/instance-info returns JSON with name and version keys."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("muxplex.settings.socket.gethostname", lambda: "test-host")

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-host"
    assert data["version"] == "0.1.0"


def test_instance_info_uses_explicit_device_name(client, tmp_path, monkeypatch):
    """GET /api/instance-info uses explicit device_name from settings."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"device_name": "My Workstation"}))

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Workstation"


def test_instance_info_no_auth_required(monkeypatch):
    """GET /api/instance-info succeeds without a session cookie (non-localhost)."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(app) as c:
        # Do NOT set any auth cookie
        response = c.get(
            "/api/instance-info",
            headers={"Accept": "application/json"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data
```

**Step 2: Run tests to verify they fail**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_api.py -v -k "instance_info"
```

Expected: All 4 tests FAIL with 404 (route doesn't exist yet) or `KeyError`.

**Step 3: Add the route to main.py**

In `muxplex/main.py`, add this route **after** the `PATCH /api/settings` route (after line 498) and **before** the WebSocket proxy section (line 501 comment):

```python
@app.get("/api/instance-info")
async def instance_info() -> dict:
    """Return this instance's display name and version.

    Public endpoint (no auth required) — used by remote instances to
    discover peer names and verify reachability.
    """
    settings = load_settings()
    return {"name": settings["device_name"], "version": app.version}
```

Place it between the existing `update_settings` function and the `# WebSocket proxy` comment block. No new imports needed — `load_settings` is already imported at line 61.

**Step 4: Run tests to verify they pass**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_api.py -v -k "instance_info"
```

Expected: All 4 tests PASS.

**Step 5: Commit**

Run:
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add GET /api/instance-info endpoint

Returns {name, version} for this muxplex instance. No auth required —
remote instances call this to discover peer display names and verify
reachability. The name comes from settings.device_name (falls back to
hostname)."
```

---

## Task 6: Add CORS middleware

**Files:**
- Modify: `muxplex/main.py` (add middleware after auth middleware)
- Test: `muxplex/tests/test_api.py`

**What:** Add Starlette's `CORSMiddleware` to the FastAPI app so browsers on one muxplex instance can make cross-origin requests to another. Allow all origins — these are private network tools, not public APIs.

**Step 1: Write the failing tests**

Add these tests in `muxplex/tests/test_api.py`, right after the instance-info tests you added in Task 5:

```python
# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------


def test_cors_preflight_returns_200(client):
    """An OPTIONS preflight request returns 200 with CORS headers."""
    response = client.options(
        "/api/sessions",
        headers={
            "Origin": "http://workstation:8088",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_cors_allows_any_origin(client):
    """A normal GET with an Origin header gets Access-Control-Allow-Origin: *."""
    response = client.get(
        "/api/sessions",
        headers={"Origin": "http://some-other-host:8088"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_cors_allows_credentials(client):
    """CORS response includes Access-Control-Allow-Credentials: true."""
    response = client.get(
        "/api/sessions",
        headers={"Origin": "http://workstation:8088"},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-credentials") == "true"
```

**Step 2: Run tests to verify they fail**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_api.py -v -k "cors"
```

Expected: All 3 tests FAIL — no `access-control-allow-origin` header in responses. The `test_cors_preflight_returns_200` may also get a 405 or redirect.

**Step 3: Add CORS middleware to main.py**

In `muxplex/main.py`:

1. Add the import. Find the existing import block near line 26-30 and add `CORSMiddleware`:

```python
from starlette.middleware.cors import CORSMiddleware
```

Add this line right after the existing `from starlette.responses import RedirectResponse` import (line 30).

2. Add the middleware **after** the auth middleware block (after line 252). Insert it between the auth middleware and the request/response models section:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

> **Important:** FastAPI middleware executes in reverse registration order (last-added runs first). By adding CORS **after** auth, CORS preflight (OPTIONS) will be handled before the auth check, which is correct — preflight requests don't carry cookies.

**Step 4: Run tests to verify they pass**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_api.py -v -k "cors"
```

Expected: All 3 CORS tests PASS.

**Step 5: Run full test suite to check for regressions**

Run:
```bash
cd muxplex && pytest muxplex/tests/ -v
```

Expected: All tests pass. CORS middleware is additive — it only adds headers, never blocks existing functionality.

**Step 6: Commit**

Run:
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add CORS middleware for cross-origin federation

Adds Starlette CORSMiddleware with allow_origins=['*'] so browsers on
one muxplex instance can fetch sessions from another. Permissive CORS
is appropriate — these are private network tools, not public APIs."
```

---

## Task 7: Expand WebSocket proxy tests — bidirectional relay

**Files:**
- Modify: `muxplex/tests/test_ws_proxy.py`

**What:** Add tests for bidirectional message relay (browser → ttyd and ttyd → browser). This requires mocking the `websockets.connect` call to intercept what gets sent to ttyd.

**Step 1: Write the test file preamble and relay tests**

Replace the **entire contents** of `muxplex/tests/test_ws_proxy.py` with the following. The existing regression test is preserved as-is at the top:

```python
"""
Tests for the WebSocket proxy in muxplex/main.py.

The proxy at /terminal/ws bridges the browser WebSocket to a backend ttyd
WebSocket at ws://localhost:7682/ws. These tests mock the ttyd side using
a real asyncio server so we can verify bidirectional relay, close propagation,
auth rejection, and concurrent sessions.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from muxplex.auth import create_session_cookie
from muxplex.main import app, _auth_secret, _auth_ttl, terminal_ws_proxy


# ---------------------------------------------------------------------------
# autouse fixture — redirect state/PID files, mock startup side-effects
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Redirect state/PID files to tmp_path, mock kill_orphan_ttyd, replace _poll_loop with no-op."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_authed_client() -> TestClient:
    """Create a TestClient with a valid session cookie."""
    c = TestClient(app)
    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    c.cookies.set("muxplex_session", cookie)
    return c


class FakeTtydWs:
    """A fake ttyd WebSocket connection for mocking websockets.connect().

    Stores messages sent to ttyd and yields pre-loaded responses back.
    """

    def __init__(self, responses=None):
        self.sent = []  # messages sent TO ttyd
        self._responses = list(responses or [])
        self._closed = False

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self._closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._responses:
            return self._responses.pop(0)
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self._closed = True


# ---------------------------------------------------------------------------
# Regression test (preserved from original)
# ---------------------------------------------------------------------------


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
# Auth rejection tests
# ---------------------------------------------------------------------------


def test_ws_auth_rejection_no_cookie():
    """WebSocket from non-localhost without a cookie is closed with code 4001."""
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws"):
                pass
    assert exc_info.value.code == 4001


def test_ws_auth_rejection_invalid_cookie():
    """WebSocket from non-localhost with a tampered cookie is closed with code 4001."""
    with TestClient(app) as c:
        c.cookies.set("muxplex_session", "tampered.invalid.cookie")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws"):
                pass
    assert exc_info.value.code == 4001


# ---------------------------------------------------------------------------
# Bidirectional relay tests
# ---------------------------------------------------------------------------


def test_browser_text_relayed_to_ttyd():
    """A text message from the browser is forwarded to ttyd."""
    fake_ttyd = FakeTtydWs()

    with (
        patch("muxplex.main.websockets.connect", return_value=fake_ttyd),
        TestClient(app) as c,
    ):
        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        with c.websocket_connect("/terminal/ws") as ws:
            ws.send_text('{"AuthToken": ""}')
            # Give the relay task a moment to forward
            import time
            time.sleep(0.1)

    assert '{"AuthToken": ""}' in fake_ttyd.sent


def test_browser_bytes_relayed_to_ttyd():
    """A binary message from the browser is forwarded to ttyd."""
    fake_ttyd = FakeTtydWs()

    with (
        patch("muxplex.main.websockets.connect", return_value=fake_ttyd),
        TestClient(app) as c,
    ):
        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        with c.websocket_connect("/terminal/ws") as ws:
            ws.send_bytes(b"\x01hello")
            import time
            time.sleep(0.1)

    assert b"\x01hello" in fake_ttyd.sent


def test_ttyd_text_relayed_to_browser():
    """A text message from ttyd is forwarded to the browser."""
    fake_ttyd = FakeTtydWs(responses=["terminal output"])

    with (
        patch("muxplex.main.websockets.connect", return_value=fake_ttyd),
        TestClient(app) as c,
    ):
        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        with c.websocket_connect("/terminal/ws") as ws:
            msg = ws.receive_text()
            assert msg == "terminal output"


def test_ttyd_bytes_relayed_to_browser():
    """A binary message from ttyd is forwarded to the browser."""
    fake_ttyd = FakeTtydWs(responses=[b"\x01binary-output"])

    with (
        patch("muxplex.main.websockets.connect", return_value=fake_ttyd),
        TestClient(app) as c,
    ):
        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        with c.websocket_connect("/terminal/ws") as ws:
            msg = ws.receive_bytes()
            assert msg == b"\x01binary-output"


# ---------------------------------------------------------------------------
# Close propagation tests
# ---------------------------------------------------------------------------


def test_ttyd_close_propagates_to_browser():
    """When ttyd has no more messages, the browser WebSocket closes cleanly."""
    # FakeTtydWs with no responses will exhaust immediately → proxy should close
    fake_ttyd = FakeTtydWs(responses=[])

    with (
        patch("muxplex.main.websockets.connect", return_value=fake_ttyd),
        TestClient(app) as c,
    ):
        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        # The websocket_connect should terminate without hanging
        try:
            with c.websocket_connect("/terminal/ws") as ws:
                # Try to receive — should get disconnect since ttyd has no data
                ws.receive_text()
        except (WebSocketDisconnect, Exception):
            pass  # Expected — connection closed after ttyd exhausted


# ---------------------------------------------------------------------------
# ttyd unreachable test
# ---------------------------------------------------------------------------


def test_ttyd_unreachable_closes_browser_ws():
    """When ttyd is unreachable, the browser WebSocket is closed (not hung)."""
    with (
        patch(
            "muxplex.main.websockets.connect",
            side_effect=OSError("Connection refused"),
        ),
        TestClient(app) as c,
    ):
        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        try:
            with c.websocket_connect("/terminal/ws") as ws:
                ws.receive_text()
        except (WebSocketDisconnect, Exception):
            pass  # Expected — proxy closes browser WS when ttyd is unreachable


# ---------------------------------------------------------------------------
# Concurrent sessions test
# ---------------------------------------------------------------------------


def test_concurrent_ws_sessions():
    """Two simultaneous WebSocket proxy sessions don't interfere."""
    fake_ttyd_1 = FakeTtydWs(responses=["session-1-output"])
    fake_ttyd_2 = FakeTtydWs(responses=["session-2-output"])

    call_count = 0

    def mock_connect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return fake_ttyd_1 if call_count == 1 else fake_ttyd_2

    with (
        patch("muxplex.main.websockets.connect", side_effect=mock_connect),
        TestClient(app) as c,
    ):
        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)

        # Open first connection and read its message
        with c.websocket_connect("/terminal/ws") as ws1:
            msg1 = ws1.receive_text()
            assert msg1 == "session-1-output"

        # Open second connection and read its message
        with c.websocket_connect("/terminal/ws") as ws2:
            msg2 = ws2.receive_text()
            assert msg2 == "session-2-output"
```

**Step 2: Run the new tests**

Run:
```bash
cd muxplex && pytest muxplex/tests/test_ws_proxy.py -v
```

Expected: All tests PASS. These tests use mocking to simulate ttyd, so they don't require a real ttyd process.

> **Note:** If any relay tests fail due to timing, the `time.sleep(0.1)` calls may need a slight increase (e.g., to `0.2`). The TestClient's synchronous WebSocket interface introduces small timing gaps between send and the async relay task.

**Step 3: Run the full test suite**

Run:
```bash
cd muxplex && pytest muxplex/tests/ -v
```

Expected: All tests pass across all test files.

**Step 4: Commit**

Run:
```bash
cd muxplex && git add muxplex/tests/test_ws_proxy.py && git commit -m "test: expand WebSocket proxy test suite

Replaces the 28-line regression test with a comprehensive suite covering:
- Bidirectional message relay (text + binary, both directions)
- Close propagation (ttyd exhaustion → browser close)
- Auth rejection (no cookie → 4001, invalid cookie → 4001)
- ttyd unreachable (OSError → browser WS closed)
- Concurrent sessions (two proxies don't interfere)

Uses FakeTtydWs mock to simulate the ttyd WebSocket without needing a
real ttyd process."
```

---

## Task 8: Final verification

**No files changed.** This task is a sanity check that everything works together.

**Step 1: Run the entire test suite**

Run:
```bash
cd muxplex && pytest muxplex/tests/ -v
```

Expected: All tests pass. Zero failures, zero errors.

**Step 2: Verify the deleted files are gone**

Run:
```bash
ls -la Caddyfile requirements.txt 2>&1
```

Expected: `No such file or directory` for both.

**Step 3: Verify the new fields are in settings**

Run:
```bash
cd muxplex && python -c "from muxplex.settings import DEFAULT_SETTINGS; print(sorted(DEFAULT_SETTINGS.keys()))"
```

Expected output includes `device_name` and `remote_instances` among the 8 keys.

**Step 4: Verify instance-info route exists**

Run:
```bash
cd muxplex && python -c "from muxplex.main import app; print([r.path for r in app.routes if hasattr(r, 'path') and 'instance' in r.path])"
```

Expected: `['/api/instance-info']`

**Step 5: Check git log**

Run:
```bash
git log --oneline -7
```

Expected: 7 commits in order:
1. `test: expand WebSocket proxy test suite`
2. `feat: add CORS middleware for cross-origin federation`
3. `feat: add GET /api/instance-info endpoint`
4. `feat: exempt /api/instance-info from auth`
5. `feat: add remote_instances and device_name to settings`
6. `chore: remove stale requirements.txt`
7. `chore: remove dead Caddyfile`

---

## Summary

| Task | What | Tests Added |
|------|------|-------------|
| 1 | Delete `Caddyfile` | — (deletion) |
| 2 | Delete `requirements.txt` | — (deletion) |
| 3 | Extend `DEFAULT_SETTINGS` with `remote_instances` + `device_name` | 7 tests in `test_settings.py` |
| 4 | Add `/api/instance-info` to auth exempt paths | 1 test in `test_auth.py` |
| 5 | Add `GET /api/instance-info` route | 4 tests in `test_api.py` |
| 6 | Add CORS middleware | 3 tests in `test_api.py` |
| 7 | Expand `test_ws_proxy.py` to full suite | 11 tests (replaces 1) |
| 8 | Final verification | — (sanity check) |

**Total new tests:** 26
**Files modified:** 5 (`settings.py`, `main.py`, `auth.py`, `test_settings.py`, `test_api.py`, `test_ws_proxy.py`)
**Files deleted:** 2 (`Caddyfile`, `requirements.txt`)

After this phase the backend is fully federation-ready: settings accept remote instances, each instance knows its name, CORS is enabled, and a public metadata endpoint exists. The frontend is untouched — that's Phase 2.
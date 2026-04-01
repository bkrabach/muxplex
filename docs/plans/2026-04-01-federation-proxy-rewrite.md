# Federation Proxy Rewrite Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Replace browser-direct cross-origin federation with server-to-server proxy so the browser only talks to its local muxplex instance.

**Architecture:** The local muxplex server fetches sessions from remote instances on behalf of the browser and proxies WebSocket terminal connections. The browser never makes cross-origin requests — it hits same-origin `/api/federation/*` endpoints. Each remote instance authenticates the local server via a shared federation key (Bearer token, `hmac.compare_digest`), stored in a separate file (`~/.config/muxplex/federation_key`).

**Tech Stack:** Python 3.11+, FastAPI, httpx (async HTTP client), websockets, Node.js test runner, vanilla JS frontend.

**Supersedes:** The browser-direct approach in `docs/plans/2026-03-30-multi-device-federation-design.md`. This plan removes CORS, X-Muxplex-Token, popup auth, and multi-source frontend code.

---

## Phase 1: Backend Proxy Endpoints (Tasks 1–15)

---

### Task 1: Add `federation_key` to DEFAULT_SETTINGS

**Files:**
- Modify: `muxplex/settings.py`
- Test: `muxplex/tests/test_settings.py`

**Step 1: Write the failing test**

Add to the bottom of `muxplex/tests/test_settings.py`:

```python
# ============================================================
# Federation key in DEFAULT_SETTINGS
# ============================================================


def test_defaults_include_federation_key():
    """DEFAULT_SETTINGS must include 'federation_key' initialised to empty string."""
    assert "federation_key" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["federation_key"] == ""
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_settings.py::test_defaults_include_federation_key -x --timeout=30
```
Expected: FAIL — `"federation_key" not in DEFAULT_SETTINGS`

**Step 3: Write minimal implementation**

In `muxplex/settings.py`, add `"federation_key": ""` to `DEFAULT_SETTINGS`, after `"multi_device_enabled"`:

```python
    "multi_device_enabled": False,
    "federation_key": "",
```

**Step 4: Run test to verify it passes**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_settings.py::test_defaults_include_federation_key -x --timeout=30
```
Expected: PASS

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: add federation_key to DEFAULT_SETTINGS"
```

---

### Task 2: Add `load_federation_key()` function

**Files:**
- Modify: `muxplex/settings.py`
- Test: `muxplex/tests/test_settings.py`

Reads federation key from `~/.config/muxplex/federation_key` file (mode 0600). Path configurable via `MUXPLEX_FEDERATION_KEY_FILE` env var.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_settings.py`:

```python
# ============================================================
# Federation key file management
# ============================================================


def test_load_federation_key_returns_empty_when_no_file(tmp_path, monkeypatch):
    """load_federation_key() returns empty string when key file does not exist."""
    from muxplex.settings import load_federation_key

    monkeypatch.setenv("MUXPLEX_FEDERATION_KEY_FILE", str(tmp_path / "nonexistent"))
    assert load_federation_key() == ""


def test_load_federation_key_reads_existing_file(tmp_path, monkeypatch):
    """load_federation_key() reads and strips the key file contents."""
    from muxplex.settings import load_federation_key

    key_file = tmp_path / "federation_key"
    key_file.write_text("my-secret-key\n")
    monkeypatch.setenv("MUXPLEX_FEDERATION_KEY_FILE", str(key_file))
    assert load_federation_key() == "my-secret-key"


def test_load_federation_key_uses_default_path(tmp_path, monkeypatch):
    """load_federation_key() uses ~/.config/muxplex/federation_key when env var is not set."""
    from muxplex.settings import load_federation_key, FEDERATION_KEY_PATH
    from pathlib import Path

    monkeypatch.delenv("MUXPLEX_FEDERATION_KEY_FILE", raising=False)
    monkeypatch.setattr("muxplex.settings.FEDERATION_KEY_PATH", tmp_path / "federation_key")
    key_file = tmp_path / "federation_key"
    key_file.write_text("default-path-key\n")
    assert load_federation_key() == "default-path-key"
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_settings.py::test_load_federation_key_returns_empty_when_no_file muxplex/tests/test_settings.py::test_load_federation_key_reads_existing_file muxplex/tests/test_settings.py::test_load_federation_key_uses_default_path -x --timeout=30
```
Expected: FAIL — `ImportError: cannot import name 'load_federation_key'`

**Step 3: Write minimal implementation**

Add to `muxplex/settings.py`, after the existing imports:

```python
import os
```

Then add after `SETTINGS_PATH`:

```python
FEDERATION_KEY_PATH = Path.home() / ".config" / "muxplex" / "federation_key"
```

Then add after the `patch_settings` function:

```python
def load_federation_key() -> str:
    """Load the federation key from its dedicated file.

    Returns empty string if the file does not exist.
    Path can be overridden via MUXPLEX_FEDERATION_KEY_FILE env var.
    """
    env_path = os.environ.get("MUXPLEX_FEDERATION_KEY_FILE")
    path = Path(env_path) if env_path else FEDERATION_KEY_PATH
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_settings.py -k "load_federation_key" -x --timeout=30
```
Expected: PASS (all 3 tests)

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: add load_federation_key() with env var override"
```

---

### Task 3: Add `generate-federation-key` CLI command

**Files:**
- Modify: `muxplex/cli.py`
- Test: `muxplex/tests/test_cli.py`

**Step 1: Write the failing test**

Add to `muxplex/tests/test_cli.py` (follow the existing pattern — look for `test_reset_secret_*` or `test_show_password_*` as reference):

```python
# ============================================================
# generate-federation-key
# ============================================================


def test_generate_federation_key_creates_file(tmp_path, monkeypatch, capsys):
    """'muxplex generate-federation-key' creates the key file with mode 0600."""
    import stat
    import muxplex.settings as settings_mod

    key_file = tmp_path / "federation_key"
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", key_file)
    monkeypatch.delenv("MUXPLEX_FEDERATION_KEY_FILE", raising=False)

    from muxplex.cli import generate_federation_key
    generate_federation_key()

    assert key_file.exists()
    content = key_file.read_text().strip()
    assert len(content) > 20  # secrets.token_urlsafe(32) produces ~43 chars
    mode = stat.S_IMODE(key_file.stat().st_mode)
    assert mode == 0o600

    captured = capsys.readouterr()
    assert "federation_key" in captured.out.lower() or content in captured.out
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_cli.py::test_generate_federation_key_creates_file -x --timeout=30
```
Expected: FAIL — `ImportError: cannot import name 'generate_federation_key'`

**Step 3: Write minimal implementation**

In `muxplex/cli.py`, add the function:

```python
def generate_federation_key() -> None:
    """Generate a random federation key and write it to the key file."""
    import muxplex.settings as settings_mod

    path = settings_mod.FEDERATION_KEY_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = _secrets.token_urlsafe(32)
    path.write_text(key + "\n")
    path.chmod(0o600)
    print(f"Federation key written to {path}")
    print(f"Key: {key}")
```

Then wire it into `main()` — in the subparsers section, add:

```python
    sub.add_parser("generate-federation-key", help="Generate a federation key for server-to-server auth")
```

And in the command dispatch section (after `elif args.command == "reset-secret":`):

```python
    elif args.command == "generate-federation-key":
        generate_federation_key()
```

**Step 4: Run test to verify it passes**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_cli.py::test_generate_federation_key_creates_file -x --timeout=30
```
Expected: PASS

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: add 'muxplex generate-federation-key' CLI command"
```

---

### Task 4: Add Bearer token auth to AuthMiddleware

**Files:**
- Modify: `muxplex/auth.py`
- Test: `muxplex/tests/test_auth.py`

Add Bearer token check between cookie check and Basic auth check in `AuthMiddleware.dispatch()`. Uses `hmac.compare_digest()` for timing-safe comparison.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# Bearer token auth (server-to-server federation)
# ---------------------------------------------------------------------------


def test_middleware_valid_bearer_token_passes():
    """Non-localhost request with valid Bearer token passes through."""
    test_app = FastAPI()
    test_app.add_middleware(
        AuthMiddleware,
        auth_mode="password",
        secret="test-secret",
        ttl_seconds=3600,
        password="test-pw",
        federation_key="my-federation-key",
    )

    @test_app.get("/protected")
    async def protected():
        return PlainTextResponse("OK")

    client = TestClient(test_app, base_url="http://192.168.1.1")
    response = client.get("/protected", headers={"Authorization": "Bearer my-federation-key"})
    assert response.status_code == 200
    assert response.text == "OK"


def test_middleware_invalid_bearer_token_falls_through():
    """Non-localhost request with wrong Bearer token falls through to redirect/401."""
    test_app = FastAPI()
    test_app.add_middleware(
        AuthMiddleware,
        auth_mode="password",
        secret="test-secret",
        ttl_seconds=3600,
        password="test-pw",
        federation_key="correct-key",
    )

    @test_app.get("/protected")
    async def protected():
        return PlainTextResponse("OK")

    client = TestClient(test_app, base_url="http://192.168.1.1", follow_redirects=False)
    response = client.get("/protected", headers={
        "Authorization": "Bearer wrong-key",
        "Accept": "application/json",
    })
    assert response.status_code == 401


def test_middleware_bearer_skipped_when_no_federation_key():
    """When federation_key is empty, Bearer check is skipped entirely."""
    test_app = FastAPI()
    test_app.add_middleware(
        AuthMiddleware,
        auth_mode="password",
        secret="test-secret",
        ttl_seconds=3600,
        password="test-pw",
        federation_key="",  # empty = disabled
    )

    @test_app.get("/protected")
    async def protected():
        return PlainTextResponse("OK")

    client = TestClient(test_app, base_url="http://192.168.1.1", follow_redirects=False)
    # Bearer with any value should NOT pass when federation_key is empty
    response = client.get("/protected", headers={
        "Authorization": "Bearer anything",
        "Accept": "application/json",
    })
    assert response.status_code == 401
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_auth.py::test_middleware_valid_bearer_token_passes muxplex/tests/test_auth.py::test_middleware_invalid_bearer_token_falls_through muxplex/tests/test_auth.py::test_middleware_bearer_skipped_when_no_federation_key -x --timeout=30
```
Expected: FAIL — `TypeError: AuthMiddleware.__init__() got an unexpected keyword argument 'federation_key'`

**Step 3: Write minimal implementation**

In `muxplex/auth.py`, add `import hmac` to the imports and `import logging` if not already present.

Modify `AuthMiddleware.__init__`:

```python
    def __init__(
        self,
        app,
        auth_mode: str,
        secret: str,
        ttl_seconds: int,
        password: str = "",
        federation_key: str = "",
    ):
        super().__init__(app)
        self.auth_mode = auth_mode
        self.secret = secret
        self.ttl_seconds = ttl_seconds
        self.password = password
        self.federation_key = federation_key
```

In `dispatch()`, add between the cookie check (step 4) and the X-Muxplex-Token check (step 4b):

```python
        # 4a. Bearer token (server-to-server federation)
        auth_header = request.headers.get("authorization", "")
        if self.federation_key and auth_header.lower().startswith("bearer "):
            token = auth_header[7:]  # strip "Bearer " prefix
            if hmac.compare_digest(token, self.federation_key):
                return await call_next(request)
            _log.warning("federation: rejected Bearer from %s", client_host)
```

Add `_log = logging.getLogger(__name__)` near the top of the auth module (after imports) if not already present.

Note: The existing step 5 (`Authorization: Basic`) also reads `auth_header`, so after adding the Bearer check, rename the existing `auth_header` variable in the Basic block, OR just reuse it since it's already been read. The simplest approach: move the `auth_header` read up to before step 4a, and use it in both 4a and 5.

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_auth.py -k "bearer" -x --timeout=30
```
Expected: PASS (all 3 tests)

Then run ALL auth tests to verify no regressions:
```bash
cd muxplex && python3 -m pytest muxplex/tests/test_auth.py -x --timeout=30
```
Expected: PASS (all tests)

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/auth.py muxplex/tests/test_auth.py && git commit -m "feat: add Bearer token auth for federation in AuthMiddleware"
```

---

### Task 5: Wire federation key into app startup

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_api.py`

Load federation key at startup and pass it to `AuthMiddleware`.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# Federation key auth integration
# ---------------------------------------------------------------------------


def test_federation_bearer_auth_accepted(client, tmp_path, monkeypatch):
    """A request with valid Bearer federation key is accepted."""
    import muxplex.settings as settings_mod
    import muxplex.main as main_module

    # Set up a federation key
    key_file = tmp_path / "federation_key"
    key_file.write_text("test-fed-key\n")
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", key_file)
    monkeypatch.delenv("MUXPLEX_FEDERATION_KEY_FILE", raising=False)

    # The key is loaded at module level, so we need to patch the middleware's stored key
    for mw in main_module.app.user_middleware:
        if hasattr(mw, 'kwargs') and 'federation_key' in mw.kwargs:
            monkeypatch.setitem(mw.kwargs, 'federation_key', 'test-fed-key')

    response = client.get("/api/sessions", headers={"Authorization": "Bearer test-fed-key"})
    assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_bearer_auth_accepted -x --timeout=30
```
Expected: FAIL (middleware doesn't have `federation_key` yet)

**Step 3: Write minimal implementation**

In `muxplex/main.py`, add after `from muxplex.settings import load_settings, patch_settings`:

```python
from muxplex.settings import load_federation_key
```

Add after `_auth_ttl = ...`:

```python
_federation_key = load_federation_key()
```

Modify the `app.add_middleware(AuthMiddleware, ...)` call to include:

```python
app.add_middleware(
    AuthMiddleware,
    auth_mode=_auth_mode,
    secret=_auth_secret,
    ttl_seconds=_auth_ttl,
    password=_auth_password,
    federation_key=_federation_key,
)
```

**Step 4: Run test to verify it passes**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_bearer_auth_accepted -x --timeout=30
```
Expected: PASS

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: wire federation key into AuthMiddleware at startup"
```

---

### Task 6: Add `key` field to remote_instances config

**Files:**
- Test: `muxplex/tests/test_settings.py`

This is a documentation/test task. The `remote_instances` list already stores arbitrary dicts. We just need to test that a `key` field round-trips correctly.

**Step 1: Write the test**

Add to `muxplex/tests/test_settings.py`:

```python
def test_remote_instances_with_key_round_trip(tmp_path, monkeypatch):
    """remote_instances with key field survive a save/load cycle unchanged."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    instances = [
        {"url": "http://host1:8088", "name": "Host 1", "key": "secret-key-1"},
        {"url": "http://host2:8088", "name": "Host 2", "key": "secret-key-2"},
    ]
    save_settings({"remote_instances": instances})
    result = load_settings()
    assert result["remote_instances"] == instances
```

**Step 2: Run test**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_settings.py::test_remote_instances_with_key_round_trip -x --timeout=30
```
Expected: PASS (dicts are stored as-is)

**Step 3: Commit**
```bash
cd muxplex && git add muxplex/tests/test_settings.py && git commit -m "test: verify remote_instances key field round-trips"
```

---

### Task 7: Create `httpx.AsyncClient` in lifespan

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_api.py`

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`:

```python
def test_federation_client_exists_on_app_state(client):
    """app.state.federation_client must be set during lifespan."""
    from muxplex.main import app
    assert hasattr(app.state, "federation_client")
    assert app.state.federation_client is not None
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_client_exists_on_app_state -x --timeout=30
```
Expected: FAIL — `AttributeError: 'State' object has no attribute 'federation_client'`

**Step 3: Write minimal implementation**

Add `import httpx` to the imports in `muxplex/main.py`.

In the `lifespan()` function, add before `yield`:

```python
    # Federation HTTP client for server-to-server proxy
    app.state.federation_client = httpx.AsyncClient(
        timeout=5.0, follow_redirects=False
    )
```

Add after `yield`, before the poll task cleanup:

```python
    # Shutdown: close federation client
    await app.state.federation_client.aclose()
```

**Step 4: Run test to verify it passes**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_client_exists_on_app_state -x --timeout=30
```
Expected: PASS

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: create httpx.AsyncClient in lifespan for federation"
```

---

### Task 8: Add `GET /api/federation/sessions` endpoint

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_api.py`

This is the core proxy endpoint. Fetches `GET /api/sessions` from each remote, merges with local sessions, tags each with `deviceName` and `remoteId`.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# GET /api/federation/sessions
# ---------------------------------------------------------------------------


def test_federation_sessions_returns_local_sessions(client, monkeypatch, tmp_path):
    """GET /api/federation/sessions includes local sessions tagged with deviceName."""
    import socket
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(socket, "gethostname", lambda: "my-laptop")
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"alpha": "snap"})

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    local = [s for s in data if s.get("deviceName") == "my-laptop"]
    assert len(local) == 1
    assert local[0]["name"] == "alpha"
    assert local[0]["remoteId"] is None  # local sessions have no remoteId


def test_federation_sessions_includes_remote_failure_status(client, monkeypatch, tmp_path):
    """GET /api/federation/sessions includes status entry for unreachable remote."""
    import json
    import muxplex.settings as settings_mod
    import httpx

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({
        "multi_device_enabled": True,
        "remote_instances": [
            {"url": "http://unreachable:8088", "name": "Ghost", "key": "k"}
        ],
    }))
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {})

    # Mock the federation client to raise a connect error
    from unittest.mock import AsyncMock
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    from muxplex.main import app
    monkeypatch.setattr(app.state, "federation_client", mock_client)

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()
    statuses = [s for s in data if "status" in s]
    assert len(statuses) == 1
    assert statuses[0]["deviceName"] == "Ghost"
    assert statuses[0]["status"] in ("unreachable", "auth_failed")
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_sessions_returns_local_sessions muxplex/tests/test_api.py::test_federation_sessions_includes_remote_failure_status -x --timeout=30
```
Expected: FAIL — 404 (route doesn't exist)

**Step 3: Write minimal implementation**

Add to `muxplex/main.py`, before the WebSocket proxy section:

```python
# ---------------------------------------------------------------------------
# Federation proxy endpoints
# ---------------------------------------------------------------------------


@app.get("/api/federation/sessions")
async def federation_sessions() -> list[dict]:
    """Aggregate sessions from local instance and all configured remotes.

    Each session is tagged with deviceName and remoteId.
    Remote failures are included as status entries.
    """
    settings = load_settings()
    device_name = settings["device_name"]

    # Local sessions
    names = get_session_list()
    snapshots = get_snapshots()
    state = await read_state()
    local_sessions = []
    for name in names:
        session_state = state.get("sessions", {}).get(name, {})
        bell = session_state.get("bell", empty_bell())
        local_sessions.append({
            "name": name,
            "snapshot": snapshots.get(name, ""),
            "bell": bell,
            "deviceName": device_name,
            "remoteId": None,
        })

    # Remote sessions
    remotes = settings.get("remote_instances", [])
    if not settings.get("multi_device_enabled") and not remotes:
        return local_sessions

    http = app.state.federation_client

    async def fetch_remote(idx: int, remote: dict) -> list[dict]:
        url = remote.get("url", "").rstrip("/")
        name = remote.get("name", url)
        key = remote.get("key", "")
        remote_id = str(idx)
        headers = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        headers["Accept"] = "application/json"
        try:
            resp = await http.get(f"{url}/api/sessions", headers=headers)
            if resp.status_code == 401 or resp.status_code == 403:
                return [{"deviceName": name, "remoteId": remote_id, "status": "auth_failed", "lastError": f"HTTP {resp.status_code}"}]
            resp.raise_for_status()
            sessions = resp.json()
            return [
                {**s, "deviceName": name, "remoteId": remote_id}
                for s in sessions
            ]
        except Exception as exc:
            return [{"deviceName": name, "remoteId": remote_id, "status": "unreachable", "lastError": str(exc)}]

    tasks = [fetch_remote(i, r) for i, r in enumerate(remotes)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_sessions = list(local_sessions)
    for result in results:
        if isinstance(result, Exception):
            continue
        all_sessions.extend(result)

    return all_sessions
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_sessions_returns_local_sessions muxplex/tests/test_api.py::test_federation_sessions_includes_remote_failure_status -x --timeout=30
```
Expected: PASS

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add GET /api/federation/sessions proxy endpoint"
```

---

### Task 9: Enhance `GET /api/instance-info` with `federation_enabled`

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_api.py`

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`:

```python
def test_instance_info_includes_federation_enabled(client, tmp_path, monkeypatch):
    """GET /api/instance-info includes federation_enabled boolean."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    # No federation key file = federation disabled
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", tmp_path / "nonexistent")
    monkeypatch.delenv("MUXPLEX_FEDERATION_KEY_FILE", raising=False)

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert "federation_enabled" in data
    assert data["federation_enabled"] is False
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_instance_info_includes_federation_enabled -x --timeout=30
```
Expected: FAIL — `"federation_enabled" not in data`

**Step 3: Write minimal implementation**

Modify the `instance_info()` route in `muxplex/main.py`:

```python
@app.get("/api/instance-info")
async def instance_info() -> dict:
    """Return this instance's display name, version, and federation status."""
    settings = load_settings()
    fed_key = load_federation_key()
    return {
        "name": settings["device_name"],
        "version": app.version,
        "federation_enabled": bool(fed_key),
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py -k "instance_info" -x --timeout=30
```
Expected: PASS (all instance-info tests)

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add federation_enabled to instance-info endpoint"
```

---

### Task 10: Add Bearer token check to WebSocket auth

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_ws_proxy.py`

The existing `terminal_ws_proxy` checks cookies for auth. Add Bearer token check so the federation proxy server can connect to remote WebSockets.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_ws_proxy.py`:

```python
def test_ws_bearer_auth_accepted(monkeypatch):
    """WebSocket from non-localhost with valid Bearer federation key is not rejected 4001."""
    import muxplex.main as main_module

    # Set federation key on the module
    monkeypatch.setattr(main_module, "_federation_key", "ws-fed-key")

    fake_ws = FakeTtydWs(responses=[])
    monkeypatch.setattr("muxplex.main.websockets.connect", lambda *a, **kw: fake_ws)

    # TestClient default host "testclient" is non-localhost
    with TestClient(app) as c:
        try:
            with c.websocket_connect(
                "/terminal/ws",
                headers={"Authorization": "Bearer ws-fed-key"},
            ) as _:
                pass
        except WebSocketDisconnect as e:
            # Should NOT be 4001 (auth rejection)
            assert e.code != 4001, f"Bearer auth should be accepted, got close code {e.code}"
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_ws_proxy.py::test_ws_bearer_auth_accepted -x --timeout=30
```
Expected: FAIL — `4001` close code (Bearer not checked)

**Step 3: Write minimal implementation**

In `muxplex/main.py`, in the `terminal_ws_proxy` function, modify the auth check section. Currently it looks like:

```python
    if host not in ("127.0.0.1", "::1"):
        session_cookie = websocket.cookies.get("muxplex_session")
        if not session_cookie or not verify_session_cookie(
            _auth_secret, session_cookie, _auth_ttl
        ):
            await websocket.close(code=4001)
            return
```

Change to:

```python
    if host not in ("127.0.0.1", "::1"):
        session_cookie = websocket.cookies.get("muxplex_session")
        cookie_ok = session_cookie and verify_session_cookie(
            _auth_secret, session_cookie, _auth_ttl
        )
        bearer_ok = False
        if _federation_key:
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                import hmac
                bearer_ok = hmac.compare_digest(auth_header[7:], _federation_key)
        if not cookie_ok and not bearer_ok:
            await websocket.close(code=4001)
            return
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_ws_proxy.py -x --timeout=30
```
Expected: PASS (all tests including the new one)

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_ws_proxy.py && git commit -m "feat: add Bearer token auth to WebSocket proxy for federation"
```

---

### Task 11: Add `WS /federation/{remote_id}/terminal/ws` proxy

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_ws_proxy.py`

New WebSocket endpoint that proxies to a remote instance's `/terminal/ws`.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_ws_proxy.py`:

```python
def test_federation_ws_proxy_route_exists():
    """The app must have a WebSocket route at /federation/{remote_id}/terminal/ws."""
    from fastapi.routing import APIRoute, APIWebSocketRoute

    ws_routes = [
        r for r in app.routes
        if isinstance(r, (APIRoute, APIWebSocketRoute))
        and "/federation/" in r.path
        and "/terminal/ws" in r.path
    ]
    assert len(ws_routes) == 1, f"Expected one federation WS route, found {len(ws_routes)}"
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_ws_proxy.py::test_federation_ws_proxy_route_exists -x --timeout=30
```
Expected: FAIL — no matching route

**Step 3: Write minimal implementation**

Add to `muxplex/main.py`, after the `federation_sessions` endpoint:

```python
@app.websocket("/federation/{remote_id}/terminal/ws")
async def federation_terminal_ws_proxy(websocket: WebSocket, remote_id: str) -> None:
    """Proxy WebSocket terminal connection to a remote muxplex instance.

    remote_id is the index into the remote_instances list.
    Authenticates to the remote using the configured key.
    """
    # Auth check (same as terminal_ws_proxy)
    host = websocket.client.host if websocket.client else ""
    if host not in ("127.0.0.1", "::1"):
        session_cookie = websocket.cookies.get("muxplex_session")
        cookie_ok = session_cookie and verify_session_cookie(
            _auth_secret, session_cookie, _auth_ttl
        )
        bearer_ok = False
        if _federation_key:
            auth_hdr = websocket.headers.get("authorization", "")
            if auth_hdr.lower().startswith("bearer "):
                import hmac as _hmac
                bearer_ok = _hmac.compare_digest(auth_hdr[7:], _federation_key)
        if not cookie_ok and not bearer_ok:
            await websocket.close(code=4001)
            return

    # Look up the remote instance
    settings = load_settings()
    remotes = settings.get("remote_instances", [])
    try:
        idx = int(remote_id)
        remote = remotes[idx]
    except (ValueError, IndexError):
        await websocket.close(code=4004)
        return

    remote_url = remote.get("url", "").rstrip("/")
    remote_key = remote.get("key", "")
    ws_url = remote_url.replace("http://", "ws://").replace("https://", "wss://") + "/terminal/ws"

    extra_headers = {}
    if remote_key:
        extra_headers["Authorization"] = f"Bearer {remote_key}"

    await websocket.accept(subprotocol="tty")

    try:
        async with websockets.connect(
            ws_url,
            subprotocols=[Subprotocol("tty")],
            additional_headers=extra_headers,
        ) as remote_ws:

            async def client_to_remote() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("bytes"):
                            await remote_ws.send(msg["bytes"])
                        elif msg.get("text"):
                            await remote_ws.send(msg["text"])
                except Exception:
                    pass

            async def remote_to_client() -> None:
                try:
                    async for message in remote_ws:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(client_to_remote(), remote_to_client())
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_ws_proxy.py -x --timeout=30
```
Expected: PASS (all tests)

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_ws_proxy.py && git commit -m "feat: add WS /federation/{remote_id}/terminal/ws proxy endpoint"
```

---

### Task 12: Add `POST /api/federation/{remote_id}/connect` proxy

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_api.py`

Proxies the connect POST to a remote instance to spawn its ttyd.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`:

```python
def test_federation_connect_proxies_to_remote(client, monkeypatch, tmp_path):
    """POST /api/federation/0/connect/my-session proxies connect to the remote."""
    import json
    import muxplex.settings as settings_mod
    from unittest.mock import AsyncMock
    import httpx

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({
        "multi_device_enabled": True,
        "remote_instances": [
            {"url": "http://remote1:8088", "name": "Remote 1", "key": "rkey1"}
        ],
    }))

    # Mock the federation client's post method
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = lambda: {"active_session": "my-session", "ttyd_port": 7682}
    mock_response.raise_for_status = lambda: None

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    from muxplex.main import app
    monkeypatch.setattr(app.state, "federation_client", mock_client)

    response = client.post("/api/federation/0/connect/my-session")
    assert response.status_code == 200
    mock_client.post.assert_called_once()
    call_url = mock_client.post.call_args[0][0]
    assert "remote1:8088" in call_url
    assert "my-session" in call_url
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_connect_proxies_to_remote -x --timeout=30
```
Expected: FAIL — 404/405 (route doesn't exist)

**Step 3: Write minimal implementation**

Add to `muxplex/main.py`, after the `federation_sessions` endpoint:

```python
@app.post("/api/federation/{remote_id}/connect/{session_name}")
async def federation_connect(remote_id: str, session_name: str) -> dict:
    """Proxy a connect request to a remote muxplex instance."""
    settings = load_settings()
    remotes = settings.get("remote_instances", [])
    try:
        idx = int(remote_id)
        remote = remotes[idx]
    except (ValueError, IndexError):
        raise HTTPException(status_code=404, detail=f"Remote instance '{remote_id}' not found")

    remote_url = remote.get("url", "").rstrip("/")
    remote_key = remote.get("key", "")
    headers = {"Accept": "application/json"}
    if remote_key:
        headers["Authorization"] = f"Bearer {remote_key}"

    http = app.state.federation_client
    try:
        resp = await http.post(
            f"{remote_url}/api/sessions/{session_name}/connect",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Remote connect failed: {exc}")
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_connect_proxies_to_remote -x --timeout=30
```
Expected: PASS

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add POST /api/federation/{remote_id}/connect proxy"
```

---

### Task 13: Add `POST /api/federation/generate-key` endpoint

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_api.py`

For the settings UI — generates a federation key and returns it.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`:

```python
def test_federation_generate_key_creates_file(client, tmp_path, monkeypatch):
    """POST /api/federation/generate-key creates key file and returns key."""
    import muxplex.settings as settings_mod

    key_file = tmp_path / "federation_key"
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", key_file)
    monkeypatch.delenv("MUXPLEX_FEDERATION_KEY_FILE", raising=False)

    response = client.post("/api/federation/generate-key")
    assert response.status_code == 200
    data = response.json()
    assert "key" in data
    assert len(data["key"]) > 20
    assert key_file.exists()
    assert key_file.read_text().strip() == data["key"]
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_generate_key_creates_file -x --timeout=30
```
Expected: FAIL — 404/405

**Step 3: Write minimal implementation**

Add to `muxplex/main.py`:

```python
@app.post("/api/federation/generate-key")
async def federation_generate_key() -> dict:
    """Generate a new federation key and save it to the key file."""
    import secrets as _secrets
    from muxplex.settings import FEDERATION_KEY_PATH

    key = _secrets.token_urlsafe(32)
    path = FEDERATION_KEY_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(key + "\n")
    path.chmod(0o600)
    return {"key": key, "path": str(path)}
```

**Step 4: Run test to verify it passes**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_federation_generate_key_creates_file -x --timeout=30
```
Expected: PASS

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add POST /api/federation/generate-key endpoint"
```

---

### Task 14: Redact federation key from settings API

**Files:**
- Modify: `muxplex/main.py`
- Test: `muxplex/tests/test_api.py`

`GET /api/settings` must NOT return `federation_key`. Must also redact `key` field from each item in `remote_instances`.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_api.py`:

```python
def test_get_settings_redacts_federation_key(client, tmp_path, monkeypatch):
    """GET /api/settings must not return the federation_key value."""
    import json
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"federation_key": "secret-should-not-appear"}))

    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    # federation_key should be absent or empty
    assert data.get("federation_key", "") == ""


def test_get_settings_redacts_remote_instance_keys(client, tmp_path, monkeypatch):
    """GET /api/settings must redact key field from remote_instances."""
    import json
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({
        "remote_instances": [
            {"url": "http://host1:8088", "name": "Host 1", "key": "secret-key"},
        ],
    }))

    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    for inst in data.get("remote_instances", []):
        assert "key" not in inst or inst["key"] == ""
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_get_settings_redacts_federation_key muxplex/tests/test_api.py::test_get_settings_redacts_remote_instance_keys -x --timeout=30
```
Expected: FAIL — federation_key and remote instance keys are returned as-is

**Step 3: Write minimal implementation**

Modify the `get_settings()` route in `muxplex/main.py`:

```python
@app.get("/api/settings")
async def get_settings() -> dict:
    """Return the current settings with sensitive fields redacted."""
    import copy
    settings = load_settings()
    result = copy.deepcopy(settings)
    # Redact federation key
    result["federation_key"] = ""
    # Redact key field from each remote instance
    for inst in result.get("remote_instances", []):
        if "key" in inst:
            inst["key"] = ""
    return result
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py -k "redact" -x --timeout=30
```
Expected: PASS

Then run ALL API tests to verify no regressions:
```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py -x --timeout=30
```
Expected: PASS

**Step 5: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: redact federation/remote keys from GET /api/settings"
```

---

### Task 15: Run full backend test suite

**Files:** None (verification only)

**Step 1: Run all Python tests**

```bash
cd muxplex && python3 -m pytest muxplex/tests/ -x --timeout=30
```
Expected: ALL PASS

**Step 2: Commit (if any fixups needed)**
```bash
cd muxplex && git add -A && git commit -m "chore: Phase 1 complete — all backend proxy tests pass"
```

---

## Phase 2: Frontend — Switch to Proxy (Tasks 16–22)

---

### Task 16: Simplify `pollSessions()` to single endpoint

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/frontend/tests/test_app.mjs`

Replace multi-source parallel polling with a single `GET /api/federation/sessions` call when multi-device is enabled.

**Step 1: Modify `pollSessions()` in `muxplex/frontend/app.js`**

Replace the entire multi-source polling block (lines ~305–377) with:

```javascript
async function pollSessions() {
  try {
    // When multi-device is enabled, use the federation proxy endpoint
    // (server merges local + remote sessions). Otherwise, local only.
    var endpoint = (_serverSettings && _serverSettings.multi_device_enabled)
      ? '/api/federation/sessions'
      : '/api/sessions';
    const res = await api('GET', endpoint);
    const sessions = await res.json();
    const prev = _currentSessions;
    _currentSessions = sessions;
    _pollFailCount = 0;
    setConnectionStatus('ok');
    renderGrid(sessions);
    renderSidebar(sessions, _viewingSession);
    handleBellTransitions(prev, sessions);
    updateSessionPill(sessions);
    updateFaviconBadge();
  } catch (err) {
    _pollFailCount++;
    setConnectionStatus(_pollFailCount <= 2 ? 'warn' : 'err');
  }
}
```

**Step 2: Write a test for the new behavior**

Add to `muxplex/frontend/tests/test_app.mjs`:

```javascript
test('pollSessions uses /api/federation/sessions when multi_device_enabled', async () => {
  // This is a structural test — verify pollSessions references the federation endpoint
  const source = app.pollSessions.toString();
  assert.ok(source.includes('/api/federation/sessions'), 'pollSessions should reference federation endpoint');
  assert.ok(source.includes('multi_device_enabled'), 'pollSessions should check multi_device_enabled');
});
```

**Step 3: Run JS tests**

```bash
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs
```
Expected: PASS (may need to add `pollSessions` to module.exports)

**Step 4: Commit**
```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: simplify pollSessions to use federation proxy endpoint"
```

---

### Task 17: Simplify terminal connection to use proxy path

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/terminal.js`

When opening a remote session:
1. POST to `/api/federation/{remoteId}/connect/{name}` instead of the remote URL directly
2. WebSocket connects to `ws://localhost/federation/{remoteId}/terminal/ws` (same origin)

**Step 1: Modify `openSession()` in `app.js`**

Find the section in `openSession()` that does the remote connect (around line 1358–1365). Change the remote connect logic from:

```javascript
    if (_sourceUrl) {
      var remoteConnectUrl = _sourceUrl.replace(/\/+$/, '') + '/api/sessions/' + encodeURIComponent(name) + '/connect';
```

To use the federation proxy:

```javascript
    var _remoteId = opts.remoteId || '';
    if (_remoteId) {
      var remoteConnectUrl = '/api/federation/' + encodeURIComponent(_remoteId) + '/connect/' + encodeURIComponent(name);
```

Update all callers of `openSession` to pass `remoteId` instead of `sourceUrl`. Search for `sourceUrl` in click handlers and sidebar bindings.

**Step 2: Modify `connectWebSocket()` in `terminal.js`**

Change the `connectWebSocket` function signature from `connectWebSocket(name, sourceUrl)` to `connectWebSocket(name, remoteId)`:

```javascript
function connectWebSocket(name, remoteId) {
  var url;
  if (remoteId) {
    // Remote session: use federation proxy (same origin)
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url = proto + '//' + location.host + '/federation/' + encodeURIComponent(remoteId) + '/terminal/ws';
  } else {
    // Local session: same origin
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url = proto + '//' + location.host + '/terminal/ws';
  }
```

Update `openTerminal()` to pass `remoteId` instead of `sourceUrl`:

```javascript
function openTerminal(sessionName, remoteId) {
  // ... existing cleanup code ...
  connectWebSocket(sessionName, remoteId);
  // ...
}
```

Update the `window._openTerminal` call in `app.js` to pass `remoteId`:

```javascript
  if (window._openTerminal) window._openTerminal(name, _remoteId);
```

**Step 3: Run JS tests**

```bash
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_terminal.mjs
```
Expected: PASS (tests may need minor updates for the parameter rename)

**Step 4: Commit**
```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/terminal.js muxplex/frontend/tests/ && git commit -m "feat: route remote terminal connections through federation proxy"
```

---

### Task 18: Fix UI bug — device badge overlapping close button

**Files:**
- Modify: `muxplex/frontend/style.css`

**Step 1: Fix the CSS**

The `×` button is absolutely positioned at `top:8px right:8px`. The `.tile-meta` span needs right-padding to avoid overlap. Find `.tile-meta` in `style.css` (line ~223) and add:

```css
.tile-meta {
  font-size: 11px;
  color: var(--text-muted);
  padding-right: 24px;  /* space for the × close button */
}
```

**Step 2: Verify visually (manual)**

Open muxplex in a browser with multi-device enabled and verify device badges don't overlap close buttons.

**Step 3: Commit**
```bash
cd muxplex && git add muxplex/frontend/style.css && git commit -m "fix: prevent device badge from overlapping close button"
```

---

### Task 19: Fix UI bug — missing left border on non-active sidebar items

**Files:**
- Modify: `muxplex/frontend/style.css`

**Step 1: Fix the CSS**

Find `.sidebar-item` (line ~530) and change:
```css
  border-left: 3px solid transparent;  /* edge bar — always present, transparent by default */
```
to:
```css
  border-left: 3px solid var(--border);  /* edge bar — matches other borders by default */
```

Do the same for `.session-tile` (line ~174):
```css
  border-left: 3px solid var(--border);  /* edge bar — matches other borders by default */
```

The bell/active overrides (`.session-tile--edge-bell`, `.sidebar-item--active`) still override this with their colors.

**Step 2: Run CSS tests**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_frontend_css.py -x --timeout=30
```
Expected: PASS

**Step 3: Commit**
```bash
cd muxplex && git add muxplex/frontend/style.css && git commit -m "fix: make left border visible on non-active tiles and sidebar items"
```

---

### Task 20: Revert sidebar to single-line header

**Files:**
- Modify: `muxplex/frontend/app.js` (sidebar rendering function)
- Modify: `muxplex/frontend/style.css`

**Step 1: Modify sidebar item rendering**

Find the `buildSidebarItemHTML` or equivalent function in `app.js` that builds the sidebar HTML. Change the two-line stacked header to a single-line format: `name [badge] [×]`.

The sidebar item header should be a single row with the session name on the left, device badge (if multi-device) inline, and × close button on the right.

```javascript
// Single-line sidebar header: name + badge + ×
'<div class="sidebar-item-header">' +
  '<span class="sidebar-item-name">' + escapedName + '</span>' +
  badgeHtml +
  closeBtn +
'</div>' +
```

**Step 2: Update CSS**

Ensure `.sidebar-item-header` is `display: flex; align-items: center; gap: 6px;` with badge right-aligned and × on hover.

**Step 3: Run JS tests**

```bash
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs
```
Expected: PASS

**Step 4: Commit**
```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/style.css && git commit -m "fix: revert sidebar to single-line header with inline badge"
```

---

### Task 21: Add federation key UI to settings

**Files:**
- Modify: `muxplex/frontend/app.js` (settings panel rendering)

Add to the Multi-Device settings tab:
1. A "Key" password-masked input per remote instance row
2. A "Generate Federation Key" button that calls `POST /api/federation/generate-key`
3. Show the local federation key (masked) so the user can copy it

**Step 1: Update `_buildRemoteInstanceRow()` in `app.js`**

Add a password-type input for the key field after the name input:

```javascript
  var keyInput = document.createElement('input');
  keyInput.type = 'password';
  keyInput.className = 'settings-remote-key';
  keyInput.placeholder = 'Federation key';
  keyInput.value = key || '';
  keyInput.setAttribute('aria-label', 'Remote instance federation key');
  row.appendChild(urlInput);
  row.appendChild(nameInput);
  row.appendChild(keyInput);
  row.appendChild(removeBtn);
```

Update `_saveRemoteInstances()` to include the key field:

```javascript
    var keyEl = row.querySelector('.settings-remote-key');
    var key = (keyEl && keyEl.value) ? keyEl.value.trim() : '';
    if (url) {
      instances.push({ url: url, name: name, key: key });
    }
```

**Step 2: Add "Generate Federation Key" button**

In the multi-device settings tab rendering, add a button that calls `POST /api/federation/generate-key`:

```javascript
var genKeyBtn = document.createElement('button');
genKeyBtn.textContent = 'Generate Federation Key';
genKeyBtn.className = 'btn btn-secondary';
genKeyBtn.addEventListener('click', async function() {
  try {
    var res = await api('POST', '/api/federation/generate-key');
    var data = await res.json();
    // Show the key in a masked input so user can copy it
    var keyDisplay = document.getElementById('setting-federation-key-display');
    if (keyDisplay) keyDisplay.value = data.key;
    showToast('Federation key generated');
  } catch (err) {
    showToast('Failed to generate key');
  }
});
```

**Step 3: Run JS tests**

```bash
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs
```
Expected: PASS

**Step 4: Commit**
```bash
cd muxplex && git add muxplex/frontend/app.js && git commit -m "feat: add federation key UI to settings panel"
```

---

### Task 22: Run full test suite for Phase 2

**Files:** None (verification only)

**Step 1: Run all Python tests**
```bash
cd muxplex && python3 -m pytest muxplex/tests/ -x --timeout=30
```
Expected: ALL PASS

**Step 2: Run all JS tests**
```bash
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_terminal.mjs
```
Expected: ALL PASS

**Step 3: Commit**
```bash
cd muxplex && git add -A && git commit -m "chore: Phase 2 complete — frontend switched to proxy"
```

---

## Phase 3: Cleanup (Tasks 23–30)

---

### Task 23: Remove CORS middleware

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

**Step 1: Remove CORS middleware from `main.py`**

Delete these lines from `muxplex/main.py`:

```python
from starlette.middleware.cors import CORSMiddleware
```

And delete the entire CORS middleware block (lines ~256–267):

```python
# CORS: allow_origins=["*"] with allow_credentials=True is intentional for
# self-hosted federation. ...
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Step 2: Remove CORS tests from `test_api.py`**

Delete these test functions:
- `test_cors_preflight_returns_200`
- `test_cors_allows_any_origin`
- `test_cors_allows_credentials`

**Step 3: Run tests**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py -x --timeout=30
```
Expected: PASS

**Step 4: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "chore: remove CORS middleware — no longer needed with proxy"
```

---

### Task 24: Remove X-Muxplex-Token auth from middleware

**Files:**
- Modify: `muxplex/auth.py`
- Modify: `muxplex/tests/test_auth.py`

**Step 1: Remove the X-Muxplex-Token check from `AuthMiddleware.dispatch()`**

Delete the `# 4b. X-Muxplex-Token header` block (lines ~195–199):

```python
        # 4b. X-Muxplex-Token header (for cross-origin federation)
        token_header = request.headers.get("x-muxplex-token")
        if token_header:
            if verify_session_cookie(self.secret, token_header, self.ttl_seconds):
                return await call_next(request)
```

**Step 2: Remove X-Muxplex-Token tests from `test_auth.py`**

Delete:
- `test_middleware_valid_token_header_passes`
- `test_middleware_invalid_token_header_falls_through_to_redirect`

**Step 3: Run tests**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_auth.py -x --timeout=30
```
Expected: PASS

**Step 4: Commit**
```bash
cd muxplex && git add muxplex/auth.py muxplex/tests/test_auth.py && git commit -m "chore: remove X-Muxplex-Token auth — replaced by Bearer federation key"
```

---

### Task 25: Remove `/api/auth/token` route

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

**Step 1: Delete the route from `main.py`**

Delete the entire `get_auth_token()` function (lines ~736–746):

```python
@app.get("/api/auth/token")
async def get_auth_token(request: Request):
    """Return the current session token for federation relay..."""
    ...
```

**Step 2: Delete the tests**

Delete from `test_api.py`:
- `test_get_auth_token_returns_token_when_authenticated`
- `test_get_auth_token_returns_401_when_not_authenticated`

**Step 3: Run tests**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_api.py -x --timeout=30
```
Expected: PASS

**Step 4: Commit**
```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "chore: remove /api/auth/token route — no longer needed"
```

---

### Task 26: Remove cross-origin auth code from frontend

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Delete these functions/blocks from `app.js`:**

1. `storeFederationToken()` function (lines ~216–222)
2. The `window.addEventListener('message', ...)` block that listens for `muxplex-auth-token` postMessage (lines ~224–236)
3. `openLoginPopup()` function (lines ~666–669)
4. `buildAuthTileHTML()` function (lines ~618–628)
5. `formatLastSeen()` function (lines ~635–642)
6. All references to `muxplex.federation_tokens` in `localStorage` (search for `federation_tokens`)
7. The `_sources.forEach(function(source) { if (source.status === 'auth_required') ...` blocks that generate auth tiles

**Step 2: Simplify `api()` function**

Remove the `baseUrl` parameter and all cross-origin logic:

```javascript
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}: ${res.statusText}`);
    err.status = res.status;
    throw err;
  }
  return res;
}
```

**Step 3: Remove from `module.exports`**

Remove these from the exports block at the bottom of `app.js`:
- `buildSources`
- `tagSessions`
- `mergeSources`
- `buildAuthTileHTML`
- `openLoginPopup`
- `formatLastSeen`
- `storeFederationToken`
- `_setSources`
- `_getSources`

**Step 4: Update JS tests**

Delete all tests in `test_app.mjs` that reference the removed functions. Search for:
- `buildSources`
- `tagSessions`
- `mergeSources`
- `buildAuthTileHTML`
- `openLoginPopup`
- `formatLastSeen`
- `storeFederationToken`
- `_setSources`
- `_getSources`

**Step 5: Run JS tests**

```bash
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs
```
Expected: PASS

**Step 6: Commit**
```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "chore: remove cross-origin auth and multi-source frontend code"
```

---

### Task 27: Remove `_sources` state management

**Files:**
- Modify: `muxplex/frontend/app.js`

**Step 1: Delete remaining `_sources` references**

1. Delete `let _sources = [];` declaration (line ~136)
2. Delete `buildSources()` function (lines ~1473–1498)
3. Delete all `_sources = buildSources(...)` calls
4. Delete `_saveRemoteInstances()` federation token pruning block (that references `federation_tokens`)
5. Delete `buildOfflineTileHTML()` function if still present
6. Remove the `_sources.length > 1` checks in tile/sidebar rendering that controlled badge visibility. Instead, check if `session.deviceName` exists (the server now sets this).
7. Delete the `_setActiveFilterDevice` test helper if no longer used

**Step 2: Update badge visibility check**

Replace instances of:
```javascript
if (_sources.length > 1 && session.deviceName && ds.showDeviceBadges !== false) {
```
with:
```javascript
if (session.deviceName && ds.showDeviceBadges !== false && _serverSettings && _serverSettings.multi_device_enabled) {
```

**Step 3: Run JS tests**

```bash
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs
```
Expected: PASS

**Step 4: Commit**
```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "chore: remove _sources state management — server handles multi-device"
```

---

### Task 28: Remove popup relay script from index.html

**Files:**
- Modify: `muxplex/frontend/index.html`

**Step 1: Check and remove**

Search `index.html` for any `postMessage`, `muxplex-auth-token`, or popup relay `<script>` blocks. If present, delete them.

**Step 2: Run HTML tests**

```bash
cd muxplex && python3 -m pytest muxplex/tests/test_frontend_html.py -x --timeout=30
```
Expected: PASS

**Step 3: Commit**
```bash
cd muxplex && git add muxplex/frontend/index.html && git commit -m "chore: remove popup auth relay script from index.html"
```

---

### Task 29: Update tests referencing removed functions

**Files:**
- Modify: `muxplex/tests/test_api.py`
- Modify: `muxplex/tests/test_auth.py`
- Modify: `muxplex/frontend/tests/test_app.mjs`
- Modify: `muxplex/frontend/tests/test_terminal.mjs`

**Step 1: Search for broken references**

```bash
cd muxplex && grep -rn "sourceUrl\|_sources\|storeFederationToken\|openLoginPopup\|buildAuthTile\|formatLastSeen\|X-Muxplex-Token\|auth/token\|mergeSources\|tagSessions\|buildSources" muxplex/tests/ muxplex/frontend/tests/
```

Fix any remaining references to deleted functions or removed API endpoints.

**Step 2: Run all tests**

```bash
cd muxplex && python3 -m pytest muxplex/tests/ -x --timeout=30
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_terminal.mjs
```
Expected: ALL PASS

**Step 3: Commit**
```bash
cd muxplex && git add -A && git commit -m "chore: clean up test references to removed functions"
```

---

### Task 30: Final verification

**Files:** None (verification only)

**Step 1: Run ALL tests**

```bash
cd muxplex && python3 -m pytest muxplex/tests/ -x --timeout=30
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_terminal.mjs
```
Expected: ALL PASS

**Step 2: Verify no stale cross-origin references remain**

```bash
cd muxplex && grep -rn "CORSMiddleware\|X-Muxplex-Token\|muxplex.federation_tokens\|openLoginPopup\|storeFederationToken\|/api/auth/token" muxplex/ --include="*.py" --include="*.js" --include="*.html" | grep -v "node_modules\|__pycache__\|.pyc"
```
Expected: No matches (or only comments explaining what was removed)

**Step 3: Final commit**
```bash
cd muxplex && git add -A && git commit -m "chore: federation proxy rewrite complete — all tests pass"
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| **Phase 1** | 1–15 | Backend proxy: federation key, Bearer auth, httpx client, `/api/federation/sessions`, WS proxy, `/api/federation/connect`, key generation, settings redaction |
| **Phase 2** | 16–22 | Frontend: simplify polling to single endpoint, route terminal through proxy, CSS bug fixes, settings UI |
| **Phase 3** | 23–30 | Cleanup: remove CORS, X-Muxplex-Token, `/api/auth/token`, cross-origin frontend code, popup relay |

**Total tasks:** 30
**Estimated time:** 90–120 minutes

## Key Files Modified

| File | Changes |
|------|---------|
| `muxplex/settings.py` | `federation_key` default, `load_federation_key()`, `FEDERATION_KEY_PATH` |
| `muxplex/auth.py` | Bearer token check in middleware, `federation_key` param, remove X-Muxplex-Token |
| `muxplex/main.py` | Federation endpoints, httpx client, WS federation proxy, remove CORS, remove `/api/auth/token` |
| `muxplex/cli.py` | `generate-federation-key` command |
| `muxplex/frontend/app.js` | Simplify polling, remove multi-source code, remove cross-origin auth |
| `muxplex/frontend/terminal.js` | Route through federation proxy WS |
| `muxplex/frontend/style.css` | Device badge overlap, left border, sidebar header |
| `muxplex/frontend/index.html` | Remove popup relay script |

## Test Commands Quick Reference

```bash
# Python tests
cd muxplex && python3 -m pytest muxplex/tests/ -x --timeout=30

# JS app tests
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_app.mjs

# JS terminal tests
/home/brkrabac/.nvm/versions/node/v24.14.1/bin/node --test muxplex/frontend/tests/test_terminal.mjs
```
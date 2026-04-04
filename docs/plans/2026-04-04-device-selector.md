# Device Selector for New Session Dialog — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** When multi-device is enabled with remote instances configured, the "new session" dialog shows a device dropdown so users can create sessions on any device — local or remote.

**Architecture:** Add a `POST /api/federation/{remote_id}/sessions` proxy endpoint on the backend (same pattern as existing `federation_connect` and `federation_bell_clear`). On the frontend, inject a `<select>` element into both `showNewSessionInput()` (desktop/sidebar) and `showFabSessionInput()` (mobile FAB), route `createNewSession()` through the federation proxy when a remote device is selected, and fix the auto-open polling to match remote sessions by `sessionKey`.

**Tech Stack:** Python/FastAPI (backend), vanilla JS (frontend), pytest + monkeypatch (backend tests), node:test + source inspection (frontend tests)

---

### Task 1: Backend — Add `POST /api/federation/{remote_id}/sessions` proxy endpoint

**Files:**
- Modify: `muxplex/main.py:1085` (insert before the static file mount)
- Test: `muxplex/tests/test_api.py` (append)

**Step 1: Write the failing tests**

Append these tests to the end of `muxplex/tests/test_api.py`:

```python


# --- Device selector: federation session create proxy ---


def test_federation_create_session_proxies_to_remote(client, monkeypatch, tmp_path):
    """POST /api/federation/{remote_id}/sessions proxies POST to remote's create-session endpoint.

    Looks up remote by integer index, sends POST {remote_url}/api/sessions
    with Bearer auth header and JSON body {name: ...}, and returns the remote's JSON response.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    # Track what POST was called with
    post_calls = []

    async def mock_post(url, **kwargs):
        post_calls.append({"url": url, "kwargs": kwargs})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "my-project"}
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/sessions", json={"name": "my-project"})
    assert response.status_code == 200

    # Verify the POST was made to the correct URL
    assert len(post_calls) == 1, f"Expected exactly 1 POST call, got {len(post_calls)}"
    call = post_calls[0]
    assert (
        call["url"] == "http://remote-host:8088/api/sessions"
    ), f"Expected POST to remote sessions URL, got: {call['url']}"

    # Verify Bearer auth was included
    headers = call["kwargs"].get("headers", {})
    assert headers.get("Authorization") == "Bearer secret-key-123", (
        f"Expected Bearer auth header, got: {headers}"
    )

    # Verify JSON body was forwarded
    assert call["kwargs"].get("json") == {"name": "my-project"}, (
        f"Expected JSON body with name, got: {call['kwargs'].get('json')}"
    )

    # Verify the response is the remote's JSON
    data = response.json()
    assert data["name"] == "my-project"


def test_federation_create_session_returns_404_for_invalid_remote(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/sessions returns 404 when remote_id is out of range."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # No remote instances configured
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.post("/api/federation/0/sessions", json={"name": "test"})
    assert response.status_code == 404


def test_federation_create_session_returns_503_when_remote_unreachable(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/sessions returns 503 when remote is unreachable."""
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                    }
                ],
            }
        )
    )

    async def mock_post(url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/sessions", json={"name": "test"})
    assert response.status_code == 503


def test_federation_create_session_returns_502_when_remote_returns_error(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/sessions returns 502 when remote returns HTTP error."""
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "wrong-key",
                        "name": "remote-host",
                    }
                ],
            }
        )
    )

    async def mock_post(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "422", request=MagicMock(), response=mock_resp
        )
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/sessions", json={"name": "test"})
    assert response.status_code == 502
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && python -m pytest muxplex/tests/test_api.py::test_federation_create_session_proxies_to_remote muxplex/tests/test_api.py::test_federation_create_session_returns_404_for_invalid_remote muxplex/tests/test_api.py::test_federation_create_session_returns_503_when_remote_unreachable muxplex/tests/test_api.py::test_federation_create_session_returns_502_when_remote_returns_error -v`

Expected: All 4 FAIL (404/405 — no route exists yet)

**Step 3: Implement the endpoint**

In `muxplex/main.py`, insert the following **before** the comment block `# ---------------------------------------------------------------------------` / `# Static file serving` (line 1087). That is, insert it between the end of `federation_bell_clear` (line 1085) and the static mount comment (line 1087):

```python


@app.post("/api/federation/{remote_id}/sessions")
async def federation_create_session(
    remote_id: int, payload: CreateSessionPayload, request: Request
) -> dict:
    """Proxy a session-create POST to a remote instance.

    Looks up the remote by integer index into ``remote_instances`` in settings,
    sends ``POST {remote_url}/api/sessions`` with a Bearer auth header and
    the JSON body ``{name: ...}``, and returns the remote's JSON response.

    Raises HTTP 404 if ``remote_id`` is not a valid integer index.
    """
    settings = load_settings()
    remotes = settings.get("remote_instances", [])
    if remote_id < 0 or remote_id >= len(remotes):
        raise HTTPException(
            status_code=404,
            detail=f"Remote instance '{remote_id}' not found",
        )
    remote = remotes[remote_id]

    remote_url: str = remote.get("url", "").rstrip("/")
    remote_key: str = remote.get("key", "")
    url = f"{remote_url}/api/sessions"

    http_client: httpx.AsyncClient = request.app.state.federation_client
    try:
        resp = await http_client.post(
            url,
            headers={"Authorization": f"Bearer {remote_key}"},
            json={"name": payload.name},
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Remote returned {exc.response.status_code}",
        )
    except Exception as exc:
        _log.warning(
            "federation_create_session: remote %s unreachable: %s", remote_url, exc
        )
        raise HTTPException(
            status_code=503,
            detail=f"Remote unreachable: {remote_url}",
        )
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && python -m pytest muxplex/tests/test_api.py::test_federation_create_session_proxies_to_remote muxplex/tests/test_api.py::test_federation_create_session_returns_404_for_invalid_remote muxplex/tests/test_api.py::test_federation_create_session_returns_503_when_remote_unreachable muxplex/tests/test_api.py::test_federation_create_session_returns_502_when_remote_returns_error -v`

Expected: All 4 PASS

**Step 5: Commit**

`cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add POST /api/federation/{remote_id}/sessions proxy endpoint"`

---

### Task 2: Frontend — Add device `<select>` helper and wire into `showNewSessionInput()`

**Files:**
- Modify: `muxplex/frontend/app.js:1902-1945` (the `_createSessionInput` and `showNewSessionInput` functions)
- Test: `muxplex/frontend/tests/test_app.mjs` (append)

**Step 1: Write the failing tests**

Append these tests to the end of `muxplex/frontend/tests/test_app.mjs`:

```javascript

// --- Device selector in new session dialog ---

test('_createDeviceSelect builds a <select> with Local + remote options', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(source.includes('function _createDeviceSelect('), '_createDeviceSelect helper must exist');
  const start = source.indexOf('function _createDeviceSelect(');
  const snippet = source.slice(start, start + 800);
  assert.ok(snippet.includes("createElement('select')"), '_createDeviceSelect must create a <select> element');
  assert.ok(snippet.includes('remote_instances'), '_createDeviceSelect must read remote_instances from settings');
  assert.ok(snippet.includes('device_name'), '_createDeviceSelect must use device_name for local option label');
});

test('showNewSessionInput creates device select when multi_device_enabled with remotes', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('function showNewSessionInput(');
  assert.ok(start !== -1, 'showNewSessionInput must exist');
  const snippet = source.slice(start, start + 1200);
  assert.ok(snippet.includes('_createDeviceSelect'), 'showNewSessionInput must call _createDeviceSelect');
});

test('showNewSessionInput passes remoteId from device select to createNewSession', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('function showNewSessionInput(');
  const snippet = source.slice(start, start + 1200);
  // The Enter handler must read select.value and pass it to createNewSession
  assert.ok(
    snippet.includes('createNewSession(name,') || snippet.includes('createNewSession(name ,'),
    'showNewSessionInput must pass a second argument (remoteId) to createNewSession'
  );
});
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20`

Expected: The 3 new tests FAIL

**Step 3: Implement the device select helper and update `showNewSessionInput`**

In `muxplex/frontend/app.js`, insert a new `_createDeviceSelect()` function immediately **after** the `_createSessionInput()` function (after line 1910, before line 1912):

```javascript

/**
 * Create a device <select> dropdown for choosing where to create a new session.
 * Only returns an element when multi_device_enabled is true and remote_instances
 * has at least one entry. Returns null otherwise (single-device setups).
 * @returns {HTMLSelectElement|null}
 */
function _createDeviceSelect() {
  var ss = _serverSettings || {};
  if (!ss.multi_device_enabled) return null;
  var remotes = ss.remote_instances || [];
  if (remotes.length === 0) return null;

  var select = document.createElement('select');
  select.className = 'new-session-device-select';

  // Local option — use device_name from settings, fallback to 'Local'
  var localOpt = document.createElement('option');
  localOpt.value = '';
  localOpt.textContent = ss.device_name || 'Local';
  select.appendChild(localOpt);

  // Remote options — one per configured remote instance
  for (var i = 0; i < remotes.length; i++) {
    var opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = remotes[i].name || remotes[i].url || ('Remote ' + i);
    select.appendChild(opt);
  }

  // Pre-select based on current device filter (if user is viewing a specific device)
  if (_activeFilterDevice !== 'all') {
    // Find the remote whose name matches the active filter
    for (var j = 0; j < remotes.length; j++) {
      if (remotes[j].name === _activeFilterDevice) {
        select.value = String(j);
        break;
      }
    }
    // If active filter matches local device name, leave default (empty string)
  }

  return select;
}
```

Then update `showNewSessionInput()` to create and insert the device select. Replace the entire `showNewSessionInput` function (lines 1920–1945) with:

```javascript
function showNewSessionInput(btn) {
  var select = _createDeviceSelect();
  var input = _createSessionInput();

  function cleanup() {
    if (select && select.parentNode) select.parentNode.removeChild(select);
    if (input.parentNode) input.parentNode.removeChild(input);
    btn.style.display = '';
  }

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      var name = input.value.trim();
      var remoteId = select ? select.value : '';
      cleanup();
      if (name) createNewSession(name, remoteId);
    } else if (e.key === 'Escape') {
      cleanup();
    }
  });

  input.addEventListener('blur', function () {
    setTimeout(cleanup, 150);
  });

  btn.style.display = 'none';
  if (select) btn.parentNode.insertBefore(select, btn);
  btn.parentNode.insertBefore(input, btn);
  input.focus();
}
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20`

Expected: All new tests PASS, no regressions

**Step 5: Commit**

`cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add device select dropdown to showNewSessionInput"`

---

### Task 3: Frontend — Add device `<select>` to `showFabSessionInput()` (mobile)

**Files:**
- Modify: `muxplex/frontend/app.js:1953-1987` (the `showFabSessionInput` function)
- Test: `muxplex/frontend/tests/test_app.mjs` (append)

**Step 1: Write the failing test**

Append to `muxplex/frontend/tests/test_app.mjs`:

```javascript

test('showFabSessionInput creates device select when multi_device_enabled with remotes', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('function showFabSessionInput(');
  assert.ok(start !== -1, 'showFabSessionInput must exist');
  const snippet = source.slice(start, start + 1200);
  assert.ok(snippet.includes('_createDeviceSelect'), 'showFabSessionInput must call _createDeviceSelect');
  assert.ok(
    snippet.includes('createNewSession(name,') || snippet.includes('createNewSession(name ,'),
    'showFabSessionInput must pass remoteId to createNewSession'
  );
});
```

**Step 2: Run test to verify it fails**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10`

Expected: New test FAILS

**Step 3: Update `showFabSessionInput` to include device select**

Replace the entire `showFabSessionInput` function (lines 1953–1987, note: line numbers may have shifted after Task 2 edits) with:

```javascript
function showFabSessionInput() {
  if (document.querySelector('.fab-input-overlay')) return;

  var fab = $('new-session-fab');

  var overlay = document.createElement('div');
  overlay.className = 'fab-input-overlay';

  var select = _createDeviceSelect();
  var input = _createSessionInput();

  if (select) overlay.appendChild(select);
  overlay.appendChild(input);

  function cleanup() {
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    if (fab) fab.style.display = '';
  }

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      var name = input.value.trim();
      var remoteId = select ? select.value : '';
      cleanup();
      if (name) createNewSession(name, remoteId);
    } else if (e.key === 'Escape') {
      cleanup();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(cleanup, 150);
  });

  if (fab) fab.style.display = 'none';
  document.body.appendChild(overlay);
  input.focus();
}
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10`

Expected: New test PASSES, no regressions

**Step 5: Commit**

`cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add device select to mobile FAB session input"`

---

### Task 4: Frontend — Update `createNewSession()` to route through federation proxy

**Files:**
- Modify: `muxplex/frontend/app.js:1998-2054` (the `createNewSession` function)
- Test: `muxplex/frontend/tests/test_app.mjs` (append)

**Step 1: Write the failing tests**

Append to `muxplex/frontend/tests/test_app.mjs`:

```javascript

test('createNewSession accepts remoteId parameter and routes to federation endpoint', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function createNewSession(');
  assert.ok(start !== -1, 'createNewSession must exist');
  const snippet = source.slice(start, start + 3000);

  // Function signature must accept remoteId
  assert.ok(
    snippet.startsWith("async function createNewSession(name, remoteId") ||
    snippet.startsWith("async function createNewSession(name,remoteId"),
    'createNewSession must accept a remoteId parameter'
  );

  // Must route to federation endpoint when remoteId is set
  assert.ok(
    snippet.includes('/api/federation/'),
    'createNewSession must POST to /api/federation/ endpoint when remoteId is set'
  );

  // Must still support local endpoint
  assert.ok(
    snippet.includes("'/api/sessions'"),
    'createNewSession must still POST to /api/sessions for local sessions'
  );
});

test('createNewSession passes remoteId through to openSession for auto-open', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function createNewSession(');
  const snippet = source.slice(start, start + 3000);

  // Must pass remoteId to openSession in the poll callback
  assert.ok(
    snippet.includes('openSession(sessionName, { remoteId') ||
    snippet.includes('openSession(sessionName, {remoteId'),
    'createNewSession must pass { remoteId } opts to openSession when auto-opening'
  );
});

test('createNewSession matches remote sessions by sessionKey in poll loop', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function createNewSession(');
  const snippet = source.slice(start, start + 3000);

  // When remoteId is set, must match by sessionKey (remoteId:name) not just name
  assert.ok(
    snippet.includes('sessionKey'),
    'createNewSession must match remote sessions by sessionKey in the polling loop'
  );
});
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20`

Expected: The 3 new tests FAIL

**Step 3: Update `createNewSession` to accept and use `remoteId`**

Replace the entire `createNewSession` function (from `async function createNewSession(name)` through its closing `}`) with:

```javascript
async function createNewSession(name, remoteId) {
  remoteId = remoteId || '';
  try {
    var endpoint = remoteId
      ? '/api/federation/' + encodeURIComponent(remoteId) + '/sessions'
      : '/api/sessions';
    const res = await api('POST', endpoint, { name });
    const data = await res.json();
    const sessionName = data.name || name;
    showToast('Creating session \'' + sessionName + '\'\u2026');

    // Inject a loading placeholder tile so the user sees feedback immediately
    var loadingTile = null;
    var grid = document.getElementById('session-grid');
    if (grid) {
      loadingTile = document.createElement('div');
      loadingTile.className = 'session-tile tile--loading';
      loadingTile.id = 'loading-tile-' + sessionName;
      loadingTile.innerHTML =
        '<div class="tile-header"><span class="tile-name">' + escapeHtml(sessionName) + '</span>' +
        '<span class="tile-meta">Creating...</span></div>' +
        '<div class="tile-body"><pre class="loading-pulse"></pre></div>';
      grid.appendChild(loadingTile);
    }

    function removeLoadingTile() {
      var tile = document.getElementById('loading-tile-' + sessionName);
      if (tile) tile.remove();
    }

    const ss = _serverSettings || {};
    if (ss.auto_open_created === false) {
      // Auto-open disabled — just do one refresh
      await pollSessions();
      removeLoadingTile();
      return;
    }

    // For remote sessions, the sessionKey is "remoteId:name"
    var expectedKey = remoteId ? (remoteId + ':' + sessionName) : sessionName;

    // Poll until the session appears in _currentSessions (max 30s, every 2s)
    var attempts = 0;
    var maxAttempts = 15;
    var pollForSession = setInterval(async function() {
      attempts++;
      await pollSessions();
      var found = _currentSessions && _currentSessions.find(function(s) {
        return (s.sessionKey || s.name) === expectedKey;
      });
      if (found) {
        clearInterval(pollForSession);
        removeLoadingTile();
        showToast('Session \'' + sessionName + '\' ready');
        openSession(sessionName, { remoteId: remoteId });
      } else if (attempts >= maxAttempts) {
        clearInterval(pollForSession);
        removeLoadingTile();
        showToast('Session \'' + sessionName + '\' is taking longer than expected');
      }
    }, 2000);
  } catch (err) {
    showToast(err.message || 'Failed to create session');
  }
}
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20`

Expected: All new tests PASS, no regressions

**Step 5: Commit**

`cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: route createNewSession through federation proxy for remote devices"`

---

### Task 5: CSS — Style the device select dropdown

**Files:**
- Modify: `muxplex/frontend/style.css` (after `.new-session-input::placeholder` rule, ~line 1523)
- Test: `muxplex/frontend/tests/test_app.mjs` (append)

**Step 1: Write the failing test**

Append to `muxplex/frontend/tests/test_app.mjs`:

```javascript

test('CSS has new-session-device-select styling', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('.new-session-device-select'), 'style.css must have .new-session-device-select rule');
});
```

**Step 2: Run test to verify it fails**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5`

Expected: FAIL — CSS rule doesn't exist yet

**Step 3: Add the CSS rule**

In `muxplex/frontend/style.css`, insert immediately after the `.new-session-input::placeholder` rule (after line 1523):

```css

.new-session-device-select {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 12px;
  font-family: var(--font-ui);
  padding: 4px 6px;
  color: var(--text);
  outline: none;
  margin-right: 6px;
  vertical-align: middle;
}

.fab-input-overlay .new-session-device-select {
  display: block;
  width: 100%;
  margin-bottom: 6px;
  margin-right: 0;
}
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5`

Expected: PASS

**Step 5: Commit**

`cd muxplex && git add muxplex/frontend/style.css muxplex/frontend/tests/test_app.mjs && git commit -m "feat: style device select dropdown for new session dialog"`

---

### Task 6: Full test suite verification

**Files:**
- No new files — verification only

**Step 1: Run the full backend test suite**

Run: `cd muxplex && python -m pytest muxplex/tests/test_api.py -v --tb=short 2>&1 | tail -30`

Expected: All tests PASS (including the 4 new federation_create_session tests)

**Step 2: Run the full frontend test suite**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -30`

Expected: All tests PASS (including the 5 new device-selector tests)

**Step 3: Run Python quality checks**

Run: `cd muxplex && python -m ruff check muxplex/main.py muxplex/tests/test_api.py && python -m ruff format --check muxplex/main.py muxplex/tests/test_api.py`

Expected: No issues

**Step 4: Commit (if any lint/format fixes were needed)**

`cd muxplex && git add -A && git commit -m "chore: lint/format fixes" --allow-empty`

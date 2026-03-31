# Multi-Device Federation — Phase 2: Frontend Multi-Origin + View Modes

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Make the muxplex dashboard display sessions from multiple muxplex instances in a single grid, with device badges, three view modes (flat/grouped/filtered), and settings UI for managing remote instances.

**Architecture:** The browser fetches `/api/sessions` from every configured muxplex instance in parallel, tags each session with its source device name and URL, merges them into a single list, and renders them using the existing tile/sidebar system augmented with device badges and view-mode logic. All cross-origin requests use `credentials: "include"` so the browser sends cookies for each remote origin. Unreachable sources are tracked with exponential backoff but no placeholder tiles yet (Phase 3). No new backend routes — this phase is pure frontend (app.js, style.css, index.html).

**Tech Stack:** Vanilla JS (app.js), CSS (style.css), HTML (index.html), Node.js `--test` runner for JS tests

**Design doc:** `docs/plans/2026-03-30-multi-device-federation-design.md`

---

## Orientation — What You Need to Know

### Phase 1 delivers (already done before you start)

- `settings.py` has `remote_instances` (list of `{url, name}`) and `device_name` (string) in `DEFAULT_SETTINGS`
- `GET /api/instance-info` route exists — no auth required, returns `{"name": "DeviceName", "version": "0.1.0"}`
- CORS middleware is active on all instances (allows any origin)
- `/api/instance-info` is in `_AUTH_EXEMPT_PATHS` (no cookie needed)

### Project layout

```
muxplex/                    ← project root (run all commands from here)
├── muxplex/
│   ├── main.py              ← FastAPI app, routes (NOT modified in Phase 2)
│   ├── settings.py          ← DEFAULT_SETTINGS dict w/ remote_instances, device_name
│   └── frontend/
│       ├── app.js           ← 1724 lines, vanilla JS, CommonJS export shim at bottom
│       ├── style.css        ← 1388 lines, CSS custom properties on :root
│       ├── index.html       ← 183 lines, settings dialog, grid, sidebar
│       └── tests/
│           └── test_app.mjs ← 1955 lines, Node.js --test runner, globalThis stubs
```

### Key patterns in app.js you MUST follow

1. **No framework, no bundler** — plain functions, no classes, no imports
2. **DOM helpers:** `$(id)` → `document.getElementById(id)`, `on(el, ev, fn)`
3. **API wrapper:** `async function api(method, path, body)` — line 160, currently uses relative URLs
4. **State globals:** `_deviceId`, `_currentSessions`, `_viewingSession`, `_viewMode` (`'grid'|'fullscreen'`), `_serverSettings`, `_pollFailCount`
5. **Poll pattern:** `pollSessions()` → `GET /api/sessions` → `renderGrid` → `renderSidebar` → `handleBellTransitions` → `updateSessionPill`
6. **Settings:** `loadServerSettings()` → `GET /api/settings` → caches in `_serverSettings`. `patchServerSetting(key, value)` → `PATCH /api/settings`
7. **Display settings:** `loadDisplaySettings()` / `saveDisplaySettings()` use `localStorage` key `muxplex.display`
8. **CommonJS export shim** at bottom (line 1660): `if (typeof module !== 'undefined' && module.exports) { module.exports = { ... }; }` — every new function you want to test MUST be added here
9. **Test-only helpers:** `_setCurrentSessions(sessions)`, `_setViewMode(mode)`, `_setViewingSession(name)` — for setting internal state from tests

### Key patterns in test_app.mjs you MUST follow

1. **Import:** `const app = require(join(__dirname, '..', 'app.js'));`
2. **Stub globals BEFORE import** at top of file — `globalThis.localStorage`, `globalThis.document`, `globalThis.window`, `globalThis.fetch`
3. **Per-test mocking pattern:** Save original → replace → run test → restore original
4. **Flat `test()` calls** (not nested `describe/it`) for most tests
5. **`assert.*`** from `node:assert/strict`
6. **Run command:** `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs`

### Key patterns in style.css

1. **CSS custom properties on `:root`:** `--bg`, `--accent`, `--bell`, `--tile-height`, `--tile-min-width`, etc.
2. **Sections delimited by** `/* ============================================================ */` comments
3. **Grid:** `.session-grid` uses `repeat(auto-fill, minmax(var(--tile-min-width), 1fr))`
4. **File ends at line 1388** with the `@media (max-width: 959px)` block — add new sections BEFORE this final responsive block

### Critical constraint: backward compatibility

- `api()` must keep working with existing relative-URL calls (e.g. `api('GET', '/api/sessions')`)
- `openSession(name)` calls `window._openTerminal(name)` — do NOT break this for local sessions
- `getVisibleSessions()` filters by `hidden_sessions` server setting — hidden filtering must still work after session tagging
- `pollSessions()` currently returns early in fullscreen mode with `_viewMode === 'fullscreen'` check — BUT actually it doesn't, it always polls. Keep it always polling.

### Session identity model (critical)

Two different machines may have sessions named "main". After this phase, every session object gets two extra fields:
- `deviceName` — e.g. `"Laptop"`, `"Workstation"`
- `sourceUrl` — e.g. `""` (local), `"http://work:8088"` (remote)

The unique key for a session is the tuple `(sourceUrl, name)`. Use `sourceUrl + '::' + name` as a string key whenever you need a unique identifier (e.g. in `data-session-key` attributes, `hidden_sessions` dedup).

---

## Task 1: Refactor `api()` to accept optional base URL

**Files:**
- Modify: `muxplex/frontend/app.js` (lines 159–171)
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing tests**

Add these tests at the end of `muxplex/frontend/tests/test_app.mjs` (before the final empty line):

```javascript
// --- api() with baseUrl ---

test('api with no baseUrl uses relative path (backward compat)', async () => {
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => ({}) };
  };

  await app.api('GET', '/api/sessions');

  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].url, '/api/sessions');
  assert.strictEqual(calls[0].opts.credentials, undefined);
  globalThis.fetch = origFetch;
});

test('api with baseUrl prepends it to path and sets credentials include', async () => {
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => ({}) };
  };

  await app.api('GET', '/api/sessions', undefined, 'http://work:8088');

  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].url, 'http://work:8088/api/sessions');
  assert.strictEqual(calls[0].opts.credentials, 'include');
  globalThis.fetch = origFetch;
});

test('api with baseUrl and trailing slash does not double-slash', async () => {
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => ({}) };
  };

  await app.api('GET', '/api/sessions', undefined, 'http://work:8088/');

  assert.strictEqual(calls[0].url, 'http://work:8088/api/sessions');
  globalThis.fetch = origFetch;
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: FAIL — `app.api` currently only accepts 3 args, the baseUrl parameter is ignored.

**Step 3: Implement the `api()` refactor**

In `muxplex/frontend/app.js`, replace the `api` function (lines 160–171) with:

```javascript
async function api(method, path, body, baseUrl) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let url = path;
  if (baseUrl) {
    // Strip trailing slash from baseUrl to avoid double-slash
    url = baseUrl.replace(/\/+$/, '') + path;
    opts.credentials = 'include';
  }
  const res = await fetch(url, opts);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return res;
}
```

Also add `api` to the `module.exports` block (it's not currently exported). Find the line `pollSessions,` inside the `module.exports` block and add `api,` right before it:

```javascript
    // Fetch wrapper
    api,
    pollSessions,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: refactor api() to accept optional baseUrl for cross-origin requests"
```

---

## Task 2: Build sources model from server settings

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing tests**

Add to end of `test_app.mjs`:

```javascript
// --- buildSources ---

test('buildSources returns only local source when no remote_instances', () => {
  const sources = app.buildSources({ device_name: 'Laptop' });
  assert.strictEqual(sources.length, 1);
  assert.deepStrictEqual(sources[0], { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 });
});

test('buildSources returns local + remote sources from remote_instances', () => {
  const sources = app.buildSources({
    device_name: 'Laptop',
    remote_instances: [
      { url: 'http://work:8088', name: 'Workstation' },
      { url: 'https://dev:8088', name: 'Dev Server' },
    ],
  });
  assert.strictEqual(sources.length, 3);
  assert.strictEqual(sources[0].type, 'local');
  assert.strictEqual(sources[0].name, 'Laptop');
  assert.strictEqual(sources[1].type, 'remote');
  assert.strictEqual(sources[1].url, 'http://work:8088');
  assert.strictEqual(sources[1].name, 'Workstation');
  assert.strictEqual(sources[1].status, 'authenticated');
  assert.strictEqual(sources[2].url, 'https://dev:8088');
});

test('buildSources uses hostname fallback when device_name is empty', () => {
  const sources = app.buildSources({});
  assert.strictEqual(sources.length, 1);
  assert.strictEqual(sources[0].name, 'This device');
});

test('buildSources strips trailing slash from remote URLs', () => {
  const sources = app.buildSources({
    device_name: 'Laptop',
    remote_instances: [{ url: 'http://work:8088/', name: 'Work' }],
  });
  assert.strictEqual(sources[1].url, 'http://work:8088');
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — `app.buildSources is not a function`.

**Step 3: Implement `buildSources`**

In `muxplex/frontend/app.js`, add this function right after the `api()` function (after line ~175 post-Task 1):

```javascript
/**
 * Build the list of session sources from server settings.
 * Local source is always first with url: "".
 * @param {object} settings - server settings object
 * @returns {object[]} array of source objects
 */
function buildSources(settings) {
  var localName = (settings && settings.device_name) || 'This device';
  var sources = [
    { url: '', name: localName, type: 'local', status: 'authenticated', backoffMs: 2000 },
  ];
  var remotes = (settings && settings.remote_instances) || [];
  for (var i = 0; i < remotes.length; i++) {
    var r = remotes[i];
    if (r && r.url) {
      sources.push({
        url: r.url.replace(/\/+$/, ''),
        name: r.name || r.url,
        type: 'remote',
        status: 'authenticated',
        backoffMs: 2000,
      });
    }
  }
  return sources;
}
```

Add a new global right after the existing `let _pollFailCount = 0;` line (around line 127):

```javascript
let _sources = [];
```

Add `buildSources` and `_sources` to the `module.exports` block. Find the `api,` line you added in Task 1 and add after it:

```javascript
    buildSources,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add buildSources to construct session source list from settings"
```

---

## Task 3: Add test-only helpers for new state

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/frontend/tests/test_app.mjs`

We need test helpers to set `_sources` and `_serverSettings` from tests, plus a `_getGridViewMode()` getter for later tasks.

**Step 1: Write the failing tests**

Add to end of `test_app.mjs`:

```javascript
// --- test-only helpers for federation state ---

test('_setSources sets internal _sources array', () => {
  const sources = [{ url: '', name: 'Test', type: 'local', status: 'authenticated', backoffMs: 2000 }];
  app._setSources(sources);
  // Verify via buildSources that _sources is set (we'll use it in later tests)
  assert.ok(true, '_setSources should not throw');
});

test('_setServerSettings sets internal _serverSettings', () => {
  app._setServerSettings({ device_name: 'TestDevice', remote_instances: [] });
  assert.ok(true, '_setServerSettings should not throw');
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — `app._setSources is not a function`.

**Step 3: Implement helpers**

In `muxplex/frontend/app.js`, add these after the existing `_setViewMode` function (around line 1636):

```javascript
/** Test-only: set _sources directly. */
function _setSources(sources) {
  _sources = sources;
}

/** Test-only: set _serverSettings directly. */
function _setServerSettings(settings) {
  _serverSettings = settings;
}

/** Test-only: get current _gridViewMode. */
function _getGridViewMode() {
  return _gridViewMode;
}

/** Test-only: get current _sources. */
function _getSources() {
  return _sources;
}
```

Also add a new global after `let _sources = [];` (from Task 2):

```javascript
let _gridViewMode = 'flat';
let _activeFilterDevice = 'all';
```

Add to `module.exports`:

```javascript
    _setSources,
    _setServerSettings,
    _getGridViewMode,
    _getSources,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add test-only helpers for federation state (_setSources, _setServerSettings)"
```

---

## Task 4: Multi-source parallel polling with session tagging

**Files:**
- Modify: `muxplex/frontend/app.js` (the `pollSessions` function)
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing tests**

Add to end of `test_app.mjs`:

```javascript
// --- tagSessions ---

test('tagSessions adds deviceName and sourceUrl to each session', () => {
  const sessions = [{ name: 'main' }, { name: 'dev' }];
  const tagged = app.tagSessions(sessions, 'Laptop', '');
  assert.strictEqual(tagged[0].deviceName, 'Laptop');
  assert.strictEqual(tagged[0].sourceUrl, '');
  assert.strictEqual(tagged[1].deviceName, 'Laptop');
});

test('tagSessions adds sessionKey as sourceUrl::name', () => {
  const sessions = [{ name: 'main' }];
  const tagged = app.tagSessions(sessions, 'Work', 'http://work:8088');
  assert.strictEqual(tagged[0].sessionKey, 'http://work:8088::main');
});

test('tagSessions returns empty array for empty input', () => {
  const tagged = app.tagSessions([], 'Laptop', '');
  assert.deepStrictEqual(tagged, []);
});

// --- mergeSources ---

test('mergeSources combines sessions from multiple sources', () => {
  const results = [
    { source: { url: '', name: 'Laptop' }, sessions: [{ name: 'main' }] },
    { source: { url: 'http://work:8088', name: 'Work' }, sessions: [{ name: 'main' }, { name: 'dev' }] },
  ];
  const merged = app.mergeSources(results);
  assert.strictEqual(merged.length, 3);
  assert.strictEqual(merged[0].sessionKey, '::main');
  assert.strictEqual(merged[1].sessionKey, 'http://work:8088::main');
  assert.strictEqual(merged[2].sessionKey, 'http://work:8088::dev');
});

test('mergeSources returns empty array when all sources failed', () => {
  const merged = app.mergeSources([]);
  assert.deepStrictEqual(merged, []);
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — `app.tagSessions is not a function`.

**Step 3: Implement `tagSessions` and `mergeSources`**

In `muxplex/frontend/app.js`, add these functions after `buildSources`:

```javascript
/**
 * Tag each session with its source device name, URL, and a unique key.
 * Does NOT mutate originals — returns new objects.
 * @param {object[]} sessions
 * @param {string} deviceName
 * @param {string} sourceUrl
 * @returns {object[]}
 */
function tagSessions(sessions, deviceName, sourceUrl) {
  return (sessions || []).map(function(s) {
    return Object.assign({}, s, {
      deviceName: deviceName,
      sourceUrl: sourceUrl,
      sessionKey: sourceUrl + '::' + (s.name || ''),
    });
  });
}

/**
 * Merge tagged session arrays from multiple successful source fetches.
 * @param {Array<{source: object, sessions: object[]}>} results
 * @returns {object[]}
 */
function mergeSources(results) {
  var all = [];
  for (var i = 0; i < results.length; i++) {
    var r = results[i];
    var tagged = tagSessions(r.sessions, r.source.name, r.source.url);
    for (var j = 0; j < tagged.length; j++) {
      all.push(tagged[j]);
    }
  }
  return all;
}
```

Add both to `module.exports`:

```javascript
    tagSessions,
    mergeSources,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add tagSessions and mergeSources for multi-origin session merging"
```

---

## Task 5: Rewrite `pollSessions` for multi-source polling

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing tests**

Add to end of `test_app.mjs`:

```javascript
// --- pollSessions multi-source ---

test('pollSessions fetches from all sources and merges results', async () => {
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'connection-status') return { textContent: '', className: '' };
    if (id === 'session-grid') return { innerHTML: '' };
    if (id === 'empty-state') return { classList: { add: () => {}, remove: () => {} } };
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  // Set up 2 sources
  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'http://work:8088', name: 'Work', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);

  const fetchCalls = [];
  globalThis.fetch = async (url, opts) => {
    fetchCalls.push(url);
    if (url === '/api/sessions') {
      return { ok: true, json: async () => [{ name: 'local-sess' }] };
    }
    if (url === 'http://work:8088/api/sessions') {
      return { ok: true, json: async () => [{ name: 'remote-sess' }] };
    }
    return { ok: true, json: async () => [] };
  };

  await app.pollSessions();

  assert.ok(fetchCalls.includes('/api/sessions'), 'should fetch local sessions');
  assert.ok(fetchCalls.includes('http://work:8088/api/sessions'), 'should fetch remote sessions');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
  // Reset sources to default
  app._setSources([]);
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL or unexpected behavior — the current `pollSessions` doesn't use `_sources`.

**Step 3: Rewrite `pollSessions`**

In `muxplex/frontend/app.js`, replace the entire `pollSessions` function (lines 239–255) with:

```javascript
async function pollSessions() {
  // If no sources configured yet, fall back to local-only
  var sources = _sources.length > 0 ? _sources : [{ url: '', name: 'This device', type: 'local', status: 'authenticated', backoffMs: 2000 }];

  // Fetch each source in parallel
  var promises = sources.map(function(source) {
    // Skip sources that are in backoff (unreachable with nextRetryAt in the future)
    if (source.nextRetryAt && Date.now() < source.nextRetryAt) {
      return Promise.resolve(null);
    }
    return api('GET', '/api/sessions', undefined, source.url || undefined)
      .then(function(res) { return res.json(); })
      .then(function(sessions) {
        source.status = 'authenticated';
        source.backoffMs = 2000;
        delete source.nextRetryAt;
        return { source: source, sessions: sessions };
      })
      .catch(function(err) {
        // Check if it's a 401/403
        if (err.message && (err.message.includes('401') || err.message.includes('403'))) {
          source.status = 'auth_required';
        } else {
          source.status = 'unreachable';
          // Exponential backoff: 2s → 4s → 8s → cap at 30s
          source.nextRetryAt = Date.now() + source.backoffMs;
          source.backoffMs = Math.min(source.backoffMs * 2, 30000);
        }
        return null;
      });
  });

  var results = await Promise.all(promises);
  var successful = results.filter(function(r) { return r !== null; });
  var merged = mergeSources(successful);

  var prev = _currentSessions;
  _currentSessions = merged;

  // Connection status based on local source health
  var localSource = sources.find(function(s) { return s.type === 'local'; });
  if (localSource && localSource.status === 'authenticated') {
    _pollFailCount = 0;
    setConnectionStatus('ok');
  } else {
    _pollFailCount++;
    setConnectionStatus(_pollFailCount <= 2 ? 'warn' : 'err');
  }

  renderGrid(merged);
  renderSidebar(merged, _viewingSession);
  handleBellTransitions(prev, merged);
  updateSessionPill(merged);
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass (including existing `pollSessions` tests which still work because `_sources` defaults to empty so local-only fallback triggers).

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: rewrite pollSessions for multi-source parallel polling"
```

---

## Task 6: Wire sources initialization into startup

**Files:**
- Modify: `muxplex/frontend/app.js`

No new test for this task — it wires existing tested functions into the DOMContentLoaded handler.

**Step 1: Modify `loadServerSettings` to rebuild sources**

In `muxplex/frontend/app.js`, find the `loadServerSettings` function (around line 954). Replace it with:

```javascript
async function loadServerSettings() {
  try {
    const res = await api('GET', '/api/settings');
    _serverSettings = await res.json();
  } catch (err) {
    console.warn('[loadServerSettings] failed:', err);
    if (!_serverSettings) _serverSettings = {};
  }
  // Rebuild sources from settings
  _sources = buildSources(_serverSettings);
  return _serverSettings;
}
```

**Step 2: Run all tests**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 3: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js && git commit -m "feat: rebuild sources list when server settings load"
```

---

## Task 7: Update `getVisibleSessions` for session keys

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/frontend/tests/test_app.mjs`

The `getVisibleSessions` function currently filters by `s.name` — but with federation, two sessions from different devices can share a name. We need it to only hide LOCAL sessions matching the hidden list (remote sessions with the same name should still show).

**Step 1: Write the failing test**

Add to end of `test_app.mjs`:

```javascript
// --- getVisibleSessions with federation ---

test('getVisibleSessions exported and filters hidden sessions', () => {
  assert.strictEqual(typeof app.getVisibleSessions, 'function');
});

test('getVisibleSessions hides local sessions by name but not remote sessions with same name', () => {
  app._setServerSettings({ hidden_sessions: ['main'] });

  const sessions = [
    { name: 'main', sourceUrl: '', sessionKey: '::main' },
    { name: 'main', sourceUrl: 'http://work:8088', sessionKey: 'http://work:8088::main' },
    { name: 'dev', sourceUrl: '', sessionKey: '::dev' },
  ];

  const visible = app.getVisibleSessions(sessions);
  assert.strictEqual(visible.length, 2, 'should hide local main but keep remote main');
  assert.ok(visible.some(function(s) { return s.sessionKey === 'http://work:8088::main'; }));
  assert.ok(visible.some(function(s) { return s.sessionKey === '::dev'; }));

  app._setServerSettings(null);
});
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — current `getVisibleSessions` would hide BOTH "main" sessions.

**Step 3: Update `getVisibleSessions`**

In `muxplex/frontend/app.js`, replace the `getVisibleSessions` function (around line 459) with:

```javascript
function getVisibleSessions(sessions) {
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  return (sessions || []).filter(function(s) {
    // hidden_sessions only applies to local sessions (sourceUrl is empty or absent)
    if (hidden.length > 0 && (!s.sourceUrl) && hidden.includes(s.name)) {
      return false;
    }
    return true;
  });
}
```

Add `getVisibleSessions` to `module.exports` (it's not currently exported):

```javascript
    getVisibleSessions,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: getVisibleSessions only hides local sessions, not remote with same name"
```

---

## Task 8: CSS for device badge, group header, filter bar

**Files:**
- Modify: `muxplex/frontend/style.css`

No TDD step for CSS — add the styles needed for Tasks 9–13.

**Step 1: Add new CSS sections**

In `muxplex/frontend/style.css`, add this new section **BEFORE** the final responsive overlay sidebar block (before line 1350 which starts `/* ============================================================ Responsive overlay sidebar`):

```css
/* ============================================================
   Device badge (on tiles and sidebar items)
   ============================================================ */

.device-badge {
  display: inline-block;
  font-size: 9px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-dim);
  border-radius: 3px;
  padding: 1px 5px;
  margin-left: 6px;
  white-space: nowrap;
  max-width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: middle;
  line-height: 14px;
}

/* ============================================================
   Device group header (grouped view mode)
   ============================================================ */

.device-group-header {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  padding: 12px 0 4px 0;
  margin: 0;
  grid-column: 1 / -1;
  border-bottom: 1px solid var(--border-subtle);
}

.device-group-header:first-child {
  padding-top: 0;
}

/* ============================================================
   Filter bar (filtered view mode)
   ============================================================ */

.filter-bar {
  display: flex;
  gap: 6px;
  padding: 0 var(--grid-padding) 8px;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.filter-pill {
  font-size: 12px;
  font-family: var(--font-ui);
  padding: 4px 12px;
  border-radius: 14px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
  white-space: nowrap;
}

.filter-pill:hover {
  border-color: var(--accent);
  color: var(--text);
}

.filter-pill--active {
  border-color: var(--accent);
  background: var(--accent-dim);
  color: var(--accent);
  font-weight: 600;
}

/* ============================================================
   Sidebar device group header
   ============================================================ */

.sidebar-device-header {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 10px 12px 4px;
  margin: 0;
}

.sidebar-device-header:first-child {
  padding-top: 6px;
}
```

**Step 2: Verify CSS parses correctly**

```bash
cd muxplex && python3 -c "
css = open('muxplex/frontend/style.css').read()
for cls in ['.device-badge', '.device-group-header', '.filter-bar', '.filter-pill', '.filter-pill--active', '.sidebar-device-header']:
    assert cls in css, f'Missing {cls}'
print('All new CSS classes present')
"
```

Expected: `All new CSS classes present`

**Step 3: Commit**

```bash
cd muxplex && git add muxplex/frontend/style.css && git commit -m "feat: add CSS for device badge, group headers, and filter bar"
```

---

## Task 9: Device badge on tiles

**Files:**
- Modify: `muxplex/frontend/app.js` (`buildTileHTML`)
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing tests**

Add to end of `test_app.mjs`:

```javascript
// --- buildTileHTML device badge ---

test('buildTileHTML shows device-badge when session has deviceName and multiple sources', () => {
  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'http://work:8088', name: 'Work', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'main', deviceName: 'Laptop', sourceUrl: '', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('device-badge'), 'should include device-badge span');
  assert.ok(html.includes('Laptop'), 'badge should show device name');
  app._setSources([]);
});

test('buildTileHTML omits device-badge when only one source configured', () => {
  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'main', deviceName: 'Laptop', sourceUrl: '', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('device-badge'), 'should NOT include device-badge with single source');
  app._setSources([]);
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — current `buildTileHTML` doesn't produce `device-badge`.

**Step 3: Update `buildTileHTML`**

In `muxplex/frontend/app.js`, find the `buildTileHTML` function (around line 378). Replace the tile header line inside the return statement. Find:

```javascript
    `<div class="tile-header">` +
    `<span class="tile-name">${escapeHtml(name)}</span>` +
    `<span class="tile-meta">${bellHtml}<span class="tile-time">${escapeHtml(timeStr)}</span></span>` +
    `</div>` +
```

Replace with:

```javascript
    `<div class="tile-header">` +
    `<span class="tile-name">${escapeHtml(name)}${_sources.length > 1 && session.deviceName ? '<span class="device-badge">' + escapeHtml(session.deviceName) + '</span>' : ''}</span>` +
    `<span class="tile-meta">${bellHtml}<span class="tile-time">${escapeHtml(timeStr)}</span></span>` +
    `</div>` +
```

Also update the `data-session` attribute in the tile to use `sessionKey` when available (for unique identification). Find:

```javascript
    `<article class="${classes}" data-session="${escapedName}" tabindex="0" role="listitem" aria-label="${escapedName}">` +
```

Replace with:

```javascript
    `<article class="${classes}" data-session="${escapedName}" data-session-key="${escapeHtml(session.sessionKey || name)}" data-source-url="${escapeHtml(session.sourceUrl || '')}" tabindex="0" role="listitem" aria-label="${escapedName}">` +
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add device badge to session tiles when multiple sources configured"
```

---

## Task 10: Device badge on sidebar items

**Files:**
- Modify: `muxplex/frontend/app.js` (`buildSidebarHTML`)
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing test**

Add to end of `test_app.mjs`:

```javascript
// --- buildSidebarHTML device badge ---

test('buildSidebarHTML shows device-badge when multiple sources configured', () => {
  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'http://work:8088', name: 'Work', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'main', deviceName: 'Work', sourceUrl: 'http://work:8088', snapshot: '' };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('device-badge'), 'should include device-badge');
  assert.ok(html.includes('Work'), 'badge should show Work');
  app._setSources([]);
});
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — `buildSidebarHTML` doesn't produce `device-badge`.

**Step 3: Update `buildSidebarHTML`**

In `muxplex/frontend/app.js`, find the `buildSidebarHTML` function (around line 420). Find the sidebar name span:

```javascript
    `<span class="sidebar-item-name">${escapedName}</span>` +
```

Replace with:

```javascript
    `<span class="sidebar-item-name">${escapedName}${_sources.length > 1 && session.deviceName ? '<span class="device-badge">' + escapeHtml(session.deviceName) + '</span>' : ''}</span>` +
```

Also update the data attribute for session key. Find:

```javascript
    `<article class="${classes}" data-session="${escapedName}" tabindex="0" role="listitem">` +
```

Replace with:

```javascript
    `<article class="${classes}" data-session="${escapedName}" data-source-url="${escapeHtml(session.sourceUrl || '')}" tabindex="0" role="listitem">` +
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add device badge to sidebar items when multiple sources configured"
```

---

## Task 11: Grouped view mode rendering

**Files:**
- Modify: `muxplex/frontend/app.js` (`renderGrid`)
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing test**

Add to end of `test_app.mjs`:

```javascript
// --- renderGrid grouped view mode ---

test('renderGrid in grouped mode produces device-group-header elements', () => {
  const mockGrid = { innerHTML: '' };
  const mockEmpty = { classList: { add: () => {}, remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    if (id === 'filter-bar') return null;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'http://work:8088', name: 'Work', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  app._setServerSettings({});

  // Temporarily set grid view mode
  app._setGridViewMode('grouped');

  const sessions = [
    { name: 'sess-a', deviceName: 'Laptop', sourceUrl: '', snapshot: '', sessionKey: '::sess-a' },
    { name: 'sess-b', deviceName: 'Work', sourceUrl: 'http://work:8088', snapshot: '', sessionKey: 'http://work:8088::sess-b' },
  ];
  app.renderGrid(sessions);

  assert.ok(mockGrid.innerHTML.includes('device-group-header'), 'grouped mode should include device-group-header');
  assert.ok(mockGrid.innerHTML.includes('Laptop'), 'should show Laptop header');
  assert.ok(mockGrid.innerHTML.includes('Work'), 'should show Work header');

  app._setGridViewMode('flat');
  app._setSources([]);
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
});
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — `app._setGridViewMode is not a function` (or no device-group-header in output).

**Step 3: Implement**

First, add a test-only setter for `_gridViewMode`. In app.js, after `_getGridViewMode`:

```javascript
/** Test-only: set _gridViewMode directly. */
function _setGridViewMode(mode) {
  _gridViewMode = mode;
}
```

Add `_setGridViewMode` to `module.exports`.

Now update `renderGrid`. Replace the entire `renderGrid` function with:

```javascript
function renderGrid(sessions) {
  var grid = $('session-grid');
  var emptyState = $('empty-state');
  var filterBar = $('filter-bar');

  var visible = getVisibleSessions(sessions);

  // In filtered mode, apply device filter
  if (_gridViewMode === 'filtered' && _activeFilterDevice !== 'all') {
    visible = visible.filter(function(s) { return s.deviceName === _activeFilterDevice; });
  }

  if (visible.length === 0) {
    if (grid) grid.innerHTML = '';
    if (emptyState) emptyState.classList.remove('hidden');
    // Show filter bar even when filtered to empty (so user can switch back)
    if (filterBar) {
      if (_gridViewMode === 'filtered') {
        renderFilterBar(filterBar, sessions);
      } else {
        filterBar.innerHTML = '';
      }
    }
    return;
  }

  if (emptyState) emptyState.classList.add('hidden');

  // Apply sort order from server settings
  var sortOrder = _serverSettings && _serverSettings.sort_order;
  var mobile = isMobile();
  var ordered;
  if (sortOrder === 'alphabetical') {
    ordered = visible.slice().sort(function(a, b) { return (a.name || '').localeCompare(b.name || ''); });
  } else {
    ordered = mobile ? sortByPriority(visible) : visible;
  }

  var html;
  if (_gridViewMode === 'grouped') {
    html = renderGroupedGrid(ordered, mobile);
  } else {
    html = ordered.map(function(session, index) { return buildTileHTML(session, index, mobile); }).join('');
  }

  if (grid) grid.innerHTML = html;

  // Render filter bar
  if (filterBar) {
    if (_gridViewMode === 'filtered') {
      renderFilterBar(filterBar, sessions);
    } else {
      filterBar.innerHTML = '';
    }
  }

  // Bind interaction handlers on each tile
  document.querySelectorAll('.session-tile').forEach(function(tile) {
    on(tile, 'click', function() { openSession(tile.dataset.session, { sourceUrl: tile.dataset.sourceUrl }); });
    on(tile, 'keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        openSession(tile.dataset.session, { sourceUrl: tile.dataset.sourceUrl });
      }
    });
  });

  if (_viewMode === 'fullscreen') {
    updatePillBell();
  }
}
```

Add the `renderGroupedGrid` helper right before `renderGrid`:

```javascript
/**
 * Render sessions grouped by device name. Returns HTML string.
 * @param {object[]} sessions - sorted, visible sessions
 * @param {boolean} mobile
 * @returns {string}
 */
function renderGroupedGrid(sessions, mobile) {
  // Group by deviceName
  var groups = {};
  var groupOrder = [];
  for (var i = 0; i < sessions.length; i++) {
    var dn = sessions[i].deviceName || 'Unknown';
    if (!groups[dn]) {
      groups[dn] = [];
      groupOrder.push(dn);
    }
    groups[dn].push(sessions[i]);
  }

  var html = '';
  for (var g = 0; g < groupOrder.length; g++) {
    var name = groupOrder[g];
    html += '<h3 class="device-group-header">' + escapeHtml(name) + '</h3>';
    var groupSessions = groups[name];
    for (var j = 0; j < groupSessions.length; j++) {
      html += buildTileHTML(groupSessions[j], j, mobile);
    }
  }
  return html;
}
```

Add `renderGroupedGrid` to `module.exports`.

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add grouped view mode to renderGrid with device-group-header sections"
```

---

## Task 12: Filtered view mode with pill bar

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/index.html`
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Add filter bar container to index.html**

In `muxplex/frontend/index.html`, find line 29:

```html
    <div id="session-grid" class="session-grid" role="list"></div>
```

Add the filter bar container BEFORE it:

```html
    <div id="filter-bar" class="filter-bar"></div>
    <div id="session-grid" class="session-grid" role="list"></div>
```

**Step 2: Write the failing test**

Add to end of `test_app.mjs`:

```javascript
// --- renderFilterBar ---

test('renderFilterBar produces pill buttons for each device plus All', () => {
  const mockBar = { innerHTML: '' };
  const sessions = [
    { name: 'a', deviceName: 'Laptop', sourceUrl: '' },
    { name: 'b', deviceName: 'Work', sourceUrl: 'http://work:8088' },
  ];
  app.renderFilterBar(mockBar, sessions);

  assert.ok(mockBar.innerHTML.includes('filter-pill'), 'should contain filter-pill elements');
  assert.ok(mockBar.innerHTML.includes('All'), 'should contain All pill');
  assert.ok(mockBar.innerHTML.includes('Laptop'), 'should contain Laptop pill');
  assert.ok(mockBar.innerHTML.includes('Work'), 'should contain Work pill');
});

test('renderFilterBar marks active device pill with filter-pill--active', () => {
  const mockBar = { innerHTML: '' };
  app._setActiveFilterDevice('Work');
  const sessions = [
    { name: 'a', deviceName: 'Laptop', sourceUrl: '' },
    { name: 'b', deviceName: 'Work', sourceUrl: 'http://work:8088' },
  ];
  app.renderFilterBar(mockBar, sessions);

  // The "Work" pill should have filter-pill--active
  assert.ok(mockBar.innerHTML.includes('filter-pill--active'), 'active pill should have active class');
  app._setActiveFilterDevice('all');
});
```

**Step 3: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — `app.renderFilterBar is not a function`.

**Step 4: Implement `renderFilterBar` and helpers**

In `muxplex/frontend/app.js`, add after `renderGroupedGrid`:

```javascript
/**
 * Render the device filter pill bar into the given container element.
 * @param {HTMLElement} container
 * @param {object[]} allSessions - unfiltered sessions (for device name extraction)
 */
function renderFilterBar(container, allSessions) {
  // Collect unique device names in order
  var devices = [];
  var seen = {};
  for (var i = 0; i < (allSessions || []).length; i++) {
    var dn = allSessions[i].deviceName || 'Unknown';
    if (!seen[dn]) {
      seen[dn] = true;
      devices.push(dn);
    }
  }

  var html = '<button class="filter-pill' + (_activeFilterDevice === 'all' ? ' filter-pill--active' : '') + '" data-filter-device="all">All</button>';
  for (var d = 0; d < devices.length; d++) {
    var active = _activeFilterDevice === devices[d] ? ' filter-pill--active' : '';
    html += '<button class="filter-pill' + active + '" data-filter-device="' + escapeHtml(devices[d]) + '">' + escapeHtml(devices[d]) + '</button>';
  }
  container.innerHTML = html;
}
```

Add a test-only setter for `_activeFilterDevice`:

```javascript
/** Test-only: set _activeFilterDevice directly. */
function _setActiveFilterDevice(device) {
  _activeFilterDevice = device;
}
```

Add to `module.exports`:

```javascript
    renderFilterBar,
    _setActiveFilterDevice,
    renderGroupedGrid,
```

**Step 5: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 6: Bind filter pill click handlers**

In `muxplex/frontend/app.js`, find the `bindStaticEventListeners` function. Add this block at the end of the function body (just before the closing `}`):

```javascript
  // Filter bar pill clicks (delegated \u2014 pills are re-rendered each poll)
  var filterBarEl = $('filter-bar');
  if (filterBarEl) {
    filterBarEl.addEventListener('click', function(e) {
      var pill = e.target.closest('.filter-pill');
      if (!pill) return;
      _activeFilterDevice = pill.dataset.filterDevice || 'all';
      // Re-render with current sessions
      renderGrid(_currentSessions);
    });
  }
```

**Step 7: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/index.html muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add filtered view mode with device pill bar"
```

---

## Task 13: View preference storage

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing tests**

Add to end of `test_app.mjs`:

```javascript
// --- view preference storage ---

test('loadGridViewMode returns flat by default', () => {
  _localStorageStore = {};
  const mode = app.loadGridViewMode();
  assert.strictEqual(mode, 'flat');
});

test('loadGridViewMode reads from localStorage when scope is local', () => {
  _localStorageStore = {};
  _localStorageStore['muxplex.display'] = JSON.stringify({ viewPreferenceScope: 'local', gridViewMode: 'grouped' });
  const mode = app.loadGridViewMode();
  assert.strictEqual(mode, 'grouped');
  _localStorageStore = {};
});

test('loadGridViewMode reads from serverSettings when scope is server', () => {
  _localStorageStore = {};
  _localStorageStore['muxplex.display'] = JSON.stringify({ viewPreferenceScope: 'server' });
  app._setServerSettings({ grid_view_mode: 'filtered' });
  const mode = app.loadGridViewMode();
  assert.strictEqual(mode, 'filtered');
  app._setServerSettings(null);
  _localStorageStore = {};
});

test('saveGridViewMode stores to localStorage when scope is local', () => {
  _localStorageStore = {};
  _localStorageStore['muxplex.display'] = JSON.stringify({ viewPreferenceScope: 'local' });
  app.saveGridViewMode('grouped');
  const ds = JSON.parse(_localStorageStore['muxplex.display']);
  assert.strictEqual(ds.gridViewMode, 'grouped');
  _localStorageStore = {};
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — `app.loadGridViewMode is not a function`.

**Step 3: Implement**

In `muxplex/frontend/app.js`, add after the `saveDisplaySettings` function:

```javascript
/**
 * Load the grid view mode preference based on the view_preference_scope setting.
 * @returns {'flat'|'grouped'|'filtered'}
 */
function loadGridViewMode() {
  var ds = loadDisplaySettings();
  var scope = ds.viewPreferenceScope || 'local';
  if (scope === 'server') {
    return (_serverSettings && _serverSettings.grid_view_mode) || 'flat';
  }
  return ds.gridViewMode || 'flat';
}

/**
 * Save the grid view mode preference based on current scope.
 * @param {'flat'|'grouped'|'filtered'} mode
 */
function saveGridViewMode(mode) {
  var ds = loadDisplaySettings();
  var scope = ds.viewPreferenceScope || 'local';
  if (scope === 'server') {
    patchServerSetting('grid_view_mode', mode);
  } else {
    ds.gridViewMode = mode;
    saveDisplaySettings(ds);
  }
  _gridViewMode = mode;
}
```

Wire the view mode into startup. In the `DOMContentLoaded` handler, after `applyDisplaySettings(loadDisplaySettings());` (line ~1640), add:

```javascript
  _gridViewMode = loadGridViewMode();
```

Add to `module.exports`:

```javascript
    loadGridViewMode,
    saveGridViewMode,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add view preference storage with local/server scope"
```

---

## Task 14: Settings dialog — view mode selector and scope toggle

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/app.js` (`openSettings`, `bindStaticEventListeners`)

**Step 1: Add view mode controls to Display tab in index.html**

In `muxplex/frontend/index.html`, find the end of the Display settings panel (the `</div>` that closes `data-tab="display"`, around line 123). Add BEFORE that closing `</div>`:

```html
          <div class="settings-field">
            <label class="settings-label" for="setting-view-mode">Grid View Mode</label>
            <select id="setting-view-mode" class="settings-select">
              <option value="flat">Flat</option>
              <option value="grouped">Grouped by device</option>
              <option value="filtered">Filtered by device</option>
            </select>
          </div>
          <div class="settings-field">
            <label class="settings-label" for="setting-view-scope">View Preference Scope</label>
            <select id="setting-view-scope" class="settings-select">
              <option value="local">This browser only</option>
              <option value="server">Sync across browsers</option>
            </select>
          </div>
```

**Step 2: Populate view mode controls in `openSettings`**

In `muxplex/frontend/app.js`, find the `openSettings` function. After the line that sets `gridColumnsEl` value (around line 1096), add:

```javascript
  // View mode selector
  var viewModeEl = $('setting-view-mode');
  if (viewModeEl) viewModeEl.value = _gridViewMode || 'flat';
  var viewScopeEl = $('setting-view-scope');
  if (viewScopeEl) viewScopeEl.value = settings.viewPreferenceScope || 'local';
```

**Step 3: Bind change handlers in `bindStaticEventListeners`**

In the `bindStaticEventListeners` function, after the existing display settings bindings (after the `on($('setting-grid-columns'), 'change', onDisplaySettingChange);` line), add:

```javascript
  // View mode selector — save preference and re-render grid
  on($('setting-view-mode'), 'change', function() {
    var el = $('setting-view-mode');
    if (el) {
      saveGridViewMode(el.value);
      renderGrid(_currentSessions);
      renderSidebar(_currentSessions, _viewingSession);
    }
  });

  // View preference scope toggle
  on($('setting-view-scope'), 'change', function() {
    var el = $('setting-view-scope');
    if (el) {
      var ds = loadDisplaySettings();
      ds.viewPreferenceScope = el.value;
      saveDisplaySettings(ds);
      // Migrate current mode to new scope
      saveGridViewMode(_gridViewMode);
    }
  });
```

**Step 4: Run all tests**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/index.html muxplex/frontend/app.js && git commit -m "feat: add view mode selector and scope toggle to settings dialog"
```

---

## Task 15: Settings dialog — Remote Instances management

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/app.js`

**Step 1: Add Remote Instances section to Sessions tab in index.html**

In `muxplex/frontend/index.html`, find the Sessions settings panel (`data-tab="sessions"`). Add BEFORE the closing `</div>` of that panel:

```html
          <div class="settings-field settings-field--column">
            <label class="settings-label">Device Name</label>
            <input type="text" id="setting-device-name" class="settings-input" placeholder="e.g. Laptop" />
            <span class="settings-helper">How this instance appears on other dashboards</span>
          </div>
          <div class="settings-field settings-field--column">
            <label class="settings-label">Remote Instances</label>
            <div id="setting-remote-instances" class="settings-remote-list"></div>
            <button id="add-remote-instance-btn" class="settings-action-btn">+ Add Instance</button>
          </div>
```

**Step 2: Populate remote instances in `openSettings`**

In `muxplex/frontend/app.js`, inside the `openSettings` function, inside the `.then(function(ss) {` callback, after the auto-open section (around line 1162), add:

```javascript
    // Device name
    var deviceNameEl = $('setting-device-name');
    if (deviceNameEl) {
      deviceNameEl.value = (ss && ss.device_name) || '';
    }

    // Remote instances list
    var remoteEl = $('setting-remote-instances');
    if (remoteEl) {
      remoteEl.innerHTML = '';
      var instances = (ss && ss.remote_instances) || [];
      for (var i = 0; i < instances.length; i++) {
        var inst = instances[i];
        var row = document.createElement('div');
        row.className = 'settings-remote-row';
        row.innerHTML =
          '<input type="text" class="settings-input settings-remote-url" value="' + escapeHtml(inst.url || '') + '" placeholder="http://host:8088" />' +
          '<input type="text" class="settings-input settings-remote-name" value="' + escapeHtml(inst.name || '') + '" placeholder="Name" />' +
          '<button class="settings-remote-remove" aria-label="Remove">&times;</button>';
        remoteEl.appendChild(row);
      }
    }
```

**Step 3: Bind change handlers in `bindStaticEventListeners`**

In `bindStaticEventListeners`, add these blocks:

```javascript
  // Device name — debounced save
  var _deviceNameTimer;
  on($('setting-device-name'), 'input', function() {
    clearTimeout(_deviceNameTimer);
    var val = this.value;
    _deviceNameTimer = setTimeout(function() {
      patchServerSetting('device_name', val);
    }, 500);
  });

  // Add remote instance button
  on($('add-remote-instance-btn'), 'click', function() {
    var container = $('setting-remote-instances');
    if (!container) return;
    var row = document.createElement('div');
    row.className = 'settings-remote-row';
    row.innerHTML =
      '<input type="text" class="settings-input settings-remote-url" value="" placeholder="http://host:8088" />' +
      '<input type="text" class="settings-input settings-remote-name" value="" placeholder="Name" />' +
      '<button class="settings-remote-remove" aria-label="Remove">&times;</button>';
    container.appendChild(row);
  });

  // Remote instances — delegated handlers for remove + change
  var remoteContainer = $('setting-remote-instances');
  if (remoteContainer) {
    // Remove button
    remoteContainer.addEventListener('click', function(e) {
      var removeBtn = e.target.closest('.settings-remote-remove');
      if (!removeBtn) return;
      var row = removeBtn.closest('.settings-remote-row');
      if (row) row.remove();
      _saveRemoteInstances();
    });

    // Save on input change (debounced)
    var _remoteTimer;
    remoteContainer.addEventListener('input', function() {
      clearTimeout(_remoteTimer);
      _remoteTimer = setTimeout(_saveRemoteInstances, 500);
    });
  }
```

Add a `_saveRemoteInstances` helper function before `bindStaticEventListeners`:

```javascript
/**
 * Read current remote instance rows from the settings dialog and save to server.
 */
function _saveRemoteInstances() {
  var container = $('setting-remote-instances');
  if (!container) return;
  var rows = container.querySelectorAll('.settings-remote-row');
  var instances = [];
  rows.forEach(function(row) {
    var urlInput = row.querySelector('.settings-remote-url');
    var nameInput = row.querySelector('.settings-remote-name');
    var url = urlInput ? urlInput.value.trim() : '';
    var name = nameInput ? nameInput.value.trim() : '';
    if (url) {
      instances.push({ url: url, name: name || url });
    }
  });
  patchServerSetting('remote_instances', instances);
  // Rebuild sources immediately
  if (_serverSettings) {
    _serverSettings.remote_instances = instances;
    _sources = buildSources(_serverSettings);
  }
}
```

**Step 4: Add minimal CSS for remote instance rows**

In `muxplex/frontend/style.css`, add before the responsive overlay sidebar block:

```css
/* ============================================================
   Settings: Remote instances
   ============================================================ */

.settings-remote-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.settings-remote-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.settings-remote-url {
  flex: 2;
}

.settings-remote-name {
  flex: 1;
}

.settings-remote-remove {
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--err);
  cursor: pointer;
  width: 28px;
  height: 28px;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.settings-remote-remove:hover {
  border-color: var(--err);
  background: rgba(248, 81, 73, 0.1);
}

.settings-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text);
  padding: 6px 8px;
  font-size: 13px;
  font-family: var(--font-ui);
}

.settings-input:focus {
  border-color: var(--accent);
  outline: none;
}
```

**Step 5: Run all tests**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 6: Commit**

```bash
cd muxplex && git add muxplex/frontend/index.html muxplex/frontend/app.js muxplex/frontend/style.css && git commit -m "feat: add remote instances management to settings dialog"
```

---

## Task 16: Sidebar device grouping

**Files:**
- Modify: `muxplex/frontend/app.js` (`renderSidebar`)
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the failing test**

Add to end of `test_app.mjs`:

```javascript
// --- renderSidebar device grouping ---

test('renderSidebar groups sessions by device when multiple sources configured', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'sidebar-list') return mockList;
    return null;
  };

  app._setViewMode('fullscreen');
  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'http://work:8088', name: 'Work', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  app._setServerSettings({});

  const sessions = [
    { name: 'sess-a', deviceName: 'Laptop', sourceUrl: '', snapshot: '', sessionKey: '::sess-a' },
    { name: 'sess-b', deviceName: 'Work', sourceUrl: 'http://work:8088', snapshot: '', sessionKey: 'http://work:8088::sess-b' },
  ];

  app.renderSidebar(sessions, null);

  assert.ok(capturedHTML.includes('sidebar-device-header'), 'should include device group headers');
  assert.ok(capturedHTML.includes('Laptop'), 'should include Laptop header');
  assert.ok(capturedHTML.includes('Work'), 'should include Work header');

  app._setViewMode('grid');
  app._setSources([]);
  globalThis.document.getElementById = origGetById;
});

test('renderSidebar does NOT group when only one source configured', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'sidebar-list') return mockList;
    return null;
  };

  app._setViewMode('fullscreen');
  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
  ]);
  app._setServerSettings({});

  const sessions = [
    { name: 'sess-a', deviceName: 'Laptop', sourceUrl: '', snapshot: '', sessionKey: '::sess-a' },
  ];

  app.renderSidebar(sessions, null);

  assert.ok(!capturedHTML.includes('sidebar-device-header'), 'should NOT include device group headers with single source');

  app._setViewMode('grid');
  app._setSources([]);
  globalThis.document.getElementById = origGetById;
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: FAIL — current `renderSidebar` doesn't produce `sidebar-device-header`.

**Step 3: Update `renderSidebar`**

Replace the entire `renderSidebar` function in `muxplex/frontend/app.js`:

```javascript
function renderSidebar(sessions, currentSession) {
  if (_viewMode !== 'fullscreen') return;

  var list = $('sidebar-list');
  if (!list) return;

  var visible = getVisibleSessions(sessions);

  if (visible.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">No sessions</div>';
    return;
  }

  var html;
  if (_sources.length > 1) {
    // Group by deviceName
    var groups = {};
    var groupOrder = [];
    for (var i = 0; i < visible.length; i++) {
      var dn = visible[i].deviceName || 'Unknown';
      if (!groups[dn]) {
        groups[dn] = [];
        groupOrder.push(dn);
      }
      groups[dn].push(visible[i]);
    }
    html = '';
    for (var g = 0; g < groupOrder.length; g++) {
      html += '<h4 class="sidebar-device-header">' + escapeHtml(groupOrder[g]) + '</h4>';
      var groupSessions = groups[groupOrder[g]];
      for (var j = 0; j < groupSessions.length; j++) {
        html += buildSidebarHTML(groupSessions[j], currentSession);
      }
    }
  } else {
    html = visible.map(function(session) { return buildSidebarHTML(session, currentSession); }).join('');
  }

  list.innerHTML = html;

  // Bind click handlers on each sidebar item
  if (typeof list.querySelectorAll === 'function') {
    list.querySelectorAll('.sidebar-item').forEach(function(item) {
      var name = item.dataset.session;
      var sourceUrl = item.dataset.sourceUrl || '';
      on(item, 'click', function() {
        if (name !== currentSession) openSession(name, { sourceUrl: sourceUrl });
      });
    });
  }
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: add device grouping to sidebar when multiple sources configured"
```

---

## Task 17: Update `openSession` to pass sourceUrl through

**Files:**
- Modify: `muxplex/frontend/app.js`

Phase 3 will handle remote terminal connections. For now, `openSession` needs to accept `sourceUrl` in its options and store it, but only actually connect/open terminal for local sessions.

**Step 1: Update `openSession` signature**

In `muxplex/frontend/app.js`, find the `openSession` function (around line 827). The function signature already has `opts = {}`. That's fine. Update the connect call to use sourceUrl when it's provided.

Find this block:

```javascript
  // Connect to session (kill old ttyd, spawn new one for this session)
  try {
    if (!opts.skipConnect) {
      await api('POST', `/api/sessions/${name}/connect`);
    }
  } catch (err) {
```

Replace with:

```javascript
  // Connect to session (kill old ttyd, spawn new one for this session)
  var sourceUrl = opts.sourceUrl || '';
  try {
    if (!opts.skipConnect) {
      // Phase 2: only connect to local sessions. Remote connect is Phase 3.
      if (!sourceUrl) {
        await api('POST', `/api/sessions/${name}/connect`);
      }
    }
  } catch (err) {
```

And update the terminal mount to also skip for remote:

Find:

```javascript
  // Mount terminal NOW — /connect has completed, new ttyd is serving the correct session
  if (window._openTerminal) window._openTerminal(name);
```

Replace with:

```javascript
  // Mount terminal NOW — /connect has completed, new ttyd is serving the correct session
  // Phase 2: only open terminal for local sessions. Remote terminal is Phase 3.
  if (!sourceUrl && window._openTerminal) window._openTerminal(name);
```

**Step 2: Run all tests**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass (existing openSession tests don't set sourceUrl, so they exercise the local path).

**Step 3: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js && git commit -m "feat: openSession accepts sourceUrl opt, skips connect/terminal for remote (Phase 3)"
```

---

## Task 18: Update `detectBellTransitions` for session keys

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/frontend/tests/test_app.mjs`

`detectBellTransitions` currently deduplicates by `s.name` — with federation, two devices can have sessions named "main". Use `sessionKey` instead.

**Step 1: Write the failing test**

Add to end of `test_app.mjs`:

```javascript
// --- detectBellTransitions with sessionKey ---

test('detectBellTransitions uses sessionKey to distinguish same-name sessions across devices', () => {
  const prev = [
    { name: 'main', sessionKey: '::main', bell: { unseen_count: 0 } },
    { name: 'main', sessionKey: 'http://work:8088::main', bell: { unseen_count: 0 } },
  ];
  const next = [
    { name: 'main', sessionKey: '::main', bell: { unseen_count: 0 } },
    { name: 'main', sessionKey: 'http://work:8088::main', bell: { unseen_count: 3 } },
  ];
  const transitions = app.detectBellTransitions(prev, next);
  // Should fire for remote main but not local main
  assert.strictEqual(transitions.length, 1);
  assert.strictEqual(transitions[0], 'main');
});
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: Might pass or fail depending on how `detectBellTransitions` uses name vs sessionKey. Let's check — the current implementation uses `s.name` as map key, which would collapse both "main" sessions into one. If prev has count 0 for "main" and next has count 3 for "main", it would fire once. But we need to make sure it uses `sessionKey` for accurate tracking.

**Step 3: Update `detectBellTransitions`**

In `muxplex/frontend/app.js`, replace the `detectBellTransitions` function:

```javascript
function detectBellTransitions(prev, next) {
  var prevMap = new Map(
    (prev || []).map(function(s) {
      return [s.sessionKey || s.name, (s.bell && s.bell.unseen_count) || 0];
    }),
  );
  return (next || [])
    .filter(function(s) {
      var unseen = s.bell && s.bell.unseen_count;
      if (!unseen || unseen <= 0) return false;
      var key = s.sessionKey || s.name;
      var prevCount = prevMap.has(key) ? prevMap.get(key) : 0;
      return unseen > prevCount;
    })
    .map(function(s) { return s.name; });
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: All tests pass (existing tests don't set sessionKey, so fallback to `s.name` still works).

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: detectBellTransitions uses sessionKey to distinguish cross-device sessions"
```

---

## Task 19: Full integration test — all exports present

**Files:**
- Test: `muxplex/frontend/tests/test_app.mjs`

**Step 1: Write the integration test**

Add to end of `test_app.mjs`:

```javascript
// --- Phase 2 integration: all new exports present ---

test('app.js exports all Phase 2 federation functions', () => {
  const phase2Functions = [
    'api',
    'buildSources',
    'tagSessions',
    'mergeSources',
    'getVisibleSessions',
    'renderGroupedGrid',
    'renderFilterBar',
    'loadGridViewMode',
    'saveGridViewMode',
    '_setSources',
    '_getSources',
    '_setServerSettings',
    '_getGridViewMode',
    '_setGridViewMode',
    '_setActiveFilterDevice',
  ];

  for (const fn of phase2Functions) {
    assert.ok(fn in app, `app.js should export "${fn}"`);
    assert.strictEqual(typeof app[fn], 'function', `"${fn}" should be a function`);
  }
});

test('Phase 2 end-to-end: buildSources → tagSessions → mergeSources produces valid merged list', () => {
  const settings = {
    device_name: 'Laptop',
    remote_instances: [{ url: 'http://work:8088', name: 'Work' }],
  };
  const sources = app.buildSources(settings);
  assert.strictEqual(sources.length, 2);

  const localSessions = [{ name: 'dev' }];
  const remoteSessions = [{ name: 'dev' }, { name: 'prod' }];

  const results = [
    { source: sources[0], sessions: localSessions },
    { source: sources[1], sessions: remoteSessions },
  ];
  const merged = app.mergeSources(results);

  assert.strictEqual(merged.length, 3);
  // Both "dev" sessions should have different sessionKeys
  const devSessions = merged.filter(function(s) { return s.name === 'dev'; });
  assert.strictEqual(devSessions.length, 2);
  assert.notStrictEqual(devSessions[0].sessionKey, devSessions[1].sessionKey);
});
```

**Step 2: Run all tests**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -10
```

Expected: All tests pass.

**Step 3: Commit**

```bash
cd muxplex && git add muxplex/frontend/tests/test_app.mjs && git commit -m "test: add Phase 2 integration tests for federation exports and end-to-end flow"
```

---

## Summary Checklist

| Task | What it does | Key files |
|------|-------------|-----------|
| 1 | `api()` accepts `baseUrl` for cross-origin | `app.js` |
| 2 | `buildSources()` from settings | `app.js` |
| 3 | Test-only helpers for new state | `app.js` |
| 4 | `tagSessions()` + `mergeSources()` | `app.js` |
| 5 | Rewrite `pollSessions()` for multi-source | `app.js` |
| 6 | Wire sources into startup | `app.js` |
| 7 | `getVisibleSessions()` federation-aware | `app.js` |
| 8 | CSS for badges, headers, pills | `style.css` |
| 9 | Device badge on tiles | `app.js` |
| 10 | Device badge on sidebar items | `app.js` |
| 11 | Grouped view mode | `app.js` |
| 12 | Filtered view mode + pill bar | `app.js`, `index.html` |
| 13 | View preference storage | `app.js` |
| 14 | Settings: view mode + scope | `index.html`, `app.js` |
| 15 | Settings: remote instances | `index.html`, `app.js`, `style.css` |
| 16 | Sidebar device grouping | `app.js` |
| 17 | `openSession` sourceUrl passthrough | `app.js` |
| 18 | `detectBellTransitions` sessionKey | `app.js` |
| 19 | Integration test — all exports | `test_app.mjs` |

**Phase 2 end state:** Dashboard shows sessions from multiple instances with device tags and three view modes. Clicking a remote tile opens the fullscreen view but does NOT connect a terminal yet (Phase 3). Remote sources that return 401 are tracked as `auth_required` but no login UI is shown.
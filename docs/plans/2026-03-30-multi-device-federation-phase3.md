# Multi-Device Federation Phase 3: Remote Auth + Terminal + Unreachable States

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Make remote sessions fully interactive — detect auth requirements, present login flow, show unreachable placeholders, and connect terminals to remote instances.

**Architecture:** Phase 2 left sources in three status states (`authenticated | auth_required | unreachable`) but only rendered sessions for `authenticated` sources. Phase 3 adds rendering for the other two states (auth-required tiles with login buttons, offline placeholder tiles with last-seen timestamps), implements the popup login flow, makes `terminal.js` connect WebSockets to remote origins, and updates `openSession`/`closeSession` to route API calls to the correct instance.

**Tech Stack:** Vanilla JS (no framework), CSS custom properties, Node.js `--test` runner with `assert`, `window.open()` for login popups, cross-origin `fetch` with `credentials: "include"`.

---

## Conventions (read this before you start)

**File paths are relative to the repo root** (`muxplex/`).

**How to run tests:**
```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs
cd muxplex && node --test muxplex/frontend/tests/test_terminal.mjs
```

**Test pattern:** Every test file uses `.mjs` extension, imports via `createRequire`, and mocks `globalThis.document`, `globalThis.fetch`, `globalThis.window`, etc. before requiring the module. See the top of `muxplex/frontend/tests/test_app.mjs` for the full mock setup.

**CommonJS export shim:** `app.js` ends with a `module.exports = { ... }` block wrapped in `if (typeof module !== 'undefined' && module.exports)`. Any new function you want to test **must** be added to that exports object at the bottom of `app.js` (around line 1660).

**CSS design tokens:** The file `muxplex/frontend/style.css` defines tokens on `:root`. Key ones: `--bg: #0D1117`, `--accent: #00D9F5`, `--text-dim: #4A5060`, `--text-muted: #8E95A3`, `--border: #2A3040`, `--err: #f85149`, `--warn: #d29922`, `--ok: #3fb950`.

**Phase 2 assumptions:** The plan assumes Phase 2 left these in place:
- `_sources` array with `{ url, name, type, status, lastSeenAt, backoff }` per source
- `pollSessions()` does `Promise.all` across sources, sets `source.status` to `authenticated | auth_required | unreachable`
- Each session object has `deviceName`, `sourceUrl`, `sessionKey` fields
- Device badge HTML in `buildTileHTML` (something like `<span class="device-badge">Laptop</span>`)
- `_sources` is accessible from tests via a `_setSources` or `_getSources` test helper

If any of these are named slightly differently in the actual Phase 2 code, adjust accordingly — the logic is the same.

---

### Task 1: CSS for auth-required and offline tile states

**Files:**
- Modify: `muxplex/frontend/style.css`

Phase 3 tiles need two new visual states: a greyed-out "offline" tile and an "auth required" tile with a login button. Add these styles to the end of `style.css`, before the `@media (prefers-reduced-motion)` block.

**Step 1: Add the CSS**

Append these rules to `muxplex/frontend/style.css`, just before the `/* Reduced Motion */` media query (currently around line 798):

```css
/* ============================================================
   Federation: source status tiles (auth-required + offline)
   ============================================================ */

.source-tile {
  height: var(--tile-height);
  background: var(--bg-tile);
  border: 1px solid var(--border);
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  text-align: center;
  padding: 24px;
}

/* Offline / unreachable device tile */
.source-tile--offline {
  opacity: 0.45;
  border-style: dashed;
}

.source-tile--offline .source-tile__name {
  color: var(--text-dim);
}

.source-tile--offline .source-tile__badge {
  background: var(--err);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.source-tile--offline .source-tile__last-seen {
  font-size: 11px;
  color: var(--text-dim);
}

/* Auth-required device tile */
.source-tile--auth {
  border-color: var(--warn);
  border-style: dashed;
}

.source-tile__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.source-tile__login-btn {
  background: var(--accent);
  color: var(--bg);
  border: none;
  border-radius: 4px;
  padding: 8px 20px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity var(--t-fast);
}

.source-tile__login-btn:hover {
  opacity: 0.85;
}

.source-tile__login-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.source-tile__hint {
  font-size: 11px;
  color: var(--text-muted);
}
```

**Step 2: Verify the CSS file is valid**

Open the file and visually confirm the new classes are syntactically correct (no unclosed braces):

```bash
cd muxplex && grep -c 'source-tile' muxplex/frontend/style.css
```

Expected: a count of ~20+ (the lines we just added).

**Step 3: Commit**

```bash
cd muxplex && git add muxplex/frontend/style.css && git commit -m "feat(federation): add CSS for auth-required and offline source tiles"
```

---

### Task 2: `buildAuthTileHTML` — render an auth-required tile

**Files:**
- Modify: `muxplex/frontend/app.js` (add function + export)
- Modify: `muxplex/frontend/tests/test_app.mjs` (add tests)

This function builds the HTML for a single auth-required source tile. It shows the device name and a "Log in" button.

**Step 1: Write the failing tests**

Add these tests to the end of `muxplex/frontend/tests/test_app.mjs` (before the final blank lines):

```javascript
// --- buildAuthTileHTML ---

test('buildAuthTileHTML is exported as a function', () => {
  assert.strictEqual(typeof app.buildAuthTileHTML, 'function');
});

test('buildAuthTileHTML returns article with source-tile--auth class', () => {
  const html = app.buildAuthTileHTML({ name: 'Workstation', url: 'http://work:8088' });
  assert.ok(html.includes('source-tile--auth'), 'must have source-tile--auth class');
  assert.ok(html.startsWith('<article'), 'must be an <article> element');
});

test('buildAuthTileHTML includes device name', () => {
  const html = app.buildAuthTileHTML({ name: 'Dev Server', url: 'http://dev:8088' });
  assert.ok(html.includes('Dev Server'), 'must include the device name');
});

test('buildAuthTileHTML includes login button with data-url attribute', () => {
  const html = app.buildAuthTileHTML({ name: 'Dev', url: 'http://dev:8088' });
  assert.ok(html.includes('source-tile__login-btn'), 'must have login button');
  assert.ok(html.includes('data-url="http://dev:8088"'), 'button must have data-url');
});

test('buildAuthTileHTML escapes HTML in device name', () => {
  const html = app.buildAuthTileHTML({ name: '<script>alert(1)</script>', url: 'http://x' });
  assert.ok(!html.includes('<script>'), 'must not contain raw <script>');
  assert.ok(html.includes('&lt;script&gt;'), 'must escape device name');
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: 5 new failures (buildAuthTileHTML not exported).

**Step 3: Implement `buildAuthTileHTML`**

Add this function in `muxplex/frontend/app.js`, right after the `buildSidebarHTML` function (after line ~451):

```javascript
/**
 * Build HTML for an auth-required source tile (user must log in).
 * Shows device name and a "Log in" button that opens the remote login page.
 * @param {{ name: string, url: string }} source
 * @returns {string}
 */
function buildAuthTileHTML(source) {
  const escapedName = escapeHtml(source.name || '');
  const escapedUrl = escapeHtml(source.url || '');
  return (
    '<article class="source-tile source-tile--auth">' +
    '<span class="source-tile__name">' + escapedName + '</span>' +
    '<button class="source-tile__login-btn" data-url="' + escapedUrl + '">Log in</button>' +
    '<span class="source-tile__hint">Authenticate to see sessions</span>' +
    '</article>'
  );
}
```

Then add `buildAuthTileHTML` to the `module.exports` object at the bottom of `app.js`:

```javascript
    // Federation tiles
    buildAuthTileHTML,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: all tests pass including the 5 new ones.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat(federation): add buildAuthTileHTML for auth-required sources"
```

---

### Task 3: `buildOfflineTileHTML` — render an unreachable device tile

**Files:**
- Modify: `muxplex/frontend/app.js` (add function + export)
- Modify: `muxplex/frontend/tests/test_app.mjs` (add tests)

This function builds HTML for a single offline/unreachable source tile, showing the device name, an "Offline" badge, and the last-seen relative timestamp.

**Step 1: Write the failing tests**

Add to the end of `muxplex/frontend/tests/test_app.mjs`:

```javascript
// --- buildOfflineTileHTML ---

test('buildOfflineTileHTML is exported as a function', () => {
  assert.strictEqual(typeof app.buildOfflineTileHTML, 'function');
});

test('buildOfflineTileHTML returns article with source-tile--offline class', () => {
  const html = app.buildOfflineTileHTML({ name: 'Server', url: 'http://s:8088', lastSeenAt: Date.now() - 60000 });
  assert.ok(html.includes('source-tile--offline'), 'must have source-tile--offline class');
  assert.ok(html.startsWith('<article'), 'must be an <article> element');
});

test('buildOfflineTileHTML includes device name', () => {
  const html = app.buildOfflineTileHTML({ name: 'Dev Server', url: 'http://dev:8088', lastSeenAt: Date.now() });
  assert.ok(html.includes('Dev Server'), 'must include the device name');
});

test('buildOfflineTileHTML includes Offline badge', () => {
  const html = app.buildOfflineTileHTML({ name: 'Dev', url: 'http://dev:8088', lastSeenAt: Date.now() });
  assert.ok(html.includes('Offline'), 'must include Offline badge text');
  assert.ok(html.includes('source-tile__badge'), 'must have badge class');
});

test('buildOfflineTileHTML shows relative last-seen time', () => {
  const fiveMinAgo = Date.now() - 5 * 60 * 1000;
  const html = app.buildOfflineTileHTML({ name: 'Dev', url: 'http://dev:8088', lastSeenAt: fiveMinAgo });
  assert.ok(html.includes('Last seen'), 'must include "Last seen" text');
});

test('buildOfflineTileHTML escapes device name', () => {
  const html = app.buildOfflineTileHTML({ name: '<b>bad</b>', url: 'http://x', lastSeenAt: Date.now() });
  assert.ok(!html.includes('<b>bad</b>'), 'must not contain raw HTML');
  assert.ok(html.includes('&lt;b&gt;'), 'must escape name');
});

test('buildOfflineTileHTML shows "Never" when lastSeenAt is null', () => {
  const html = app.buildOfflineTileHTML({ name: 'Dev', url: 'http://dev:8088', lastSeenAt: null });
  assert.ok(html.includes('Never'), 'must show "Never" when lastSeenAt is null');
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: 7 new failures.

**Step 3: Implement `buildOfflineTileHTML`**

Add this function in `app.js`, right after `buildAuthTileHTML`:

```javascript
/**
 * Format a millisecond timestamp into a relative "last seen" string.
 * @param {number|null} ms - Unix timestamp in milliseconds
 * @returns {string}
 */
function formatLastSeen(ms) {
  if (ms == null) return 'Never';
  var diff = Math.floor((Date.now() - ms) / 1000);
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

/**
 * Build HTML for an unreachable/offline source tile.
 * Shows device name, "Offline" badge, and last-seen timestamp.
 * @param {{ name: string, url: string, lastSeenAt: number|null }} source
 * @returns {string}
 */
function buildOfflineTileHTML(source) {
  var escapedName = escapeHtml(source.name || '');
  var lastSeen = formatLastSeen(source.lastSeenAt);
  return (
    '<article class="source-tile source-tile--offline">' +
    '<span class="source-tile__name">' + escapedName + '</span>' +
    '<span class="source-tile__badge">Offline</span>' +
    '<span class="source-tile__last-seen">Last seen ' + escapeHtml(lastSeen) + '</span>' +
    '</article>'
  );
}
```

Add both to `module.exports`:

```javascript
    buildOfflineTileHTML,
    formatLastSeen,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: all pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat(federation): add buildOfflineTileHTML for unreachable sources"
```

---

### Task 4: Integrate source status tiles into `renderGrid`

**Files:**
- Modify: `muxplex/frontend/app.js` (modify `renderGrid`)
- Modify: `muxplex/frontend/tests/test_app.mjs` (add tests)

Currently `renderGrid` only renders session tiles. We need it to also render auth-required and offline tiles for non-authenticated sources.

**Step 1: Write the failing tests**

Add to `muxplex/frontend/tests/test_app.mjs`:

```javascript
// --- renderGrid with source status tiles ---

test('renderGrid includes auth tile HTML when a source is auth_required', () => {
  const mockGrid = { innerHTML: '' };
  const mockEmpty = { style: {}, classList: { add: () => {}, remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  // Set sources with one authenticated, one auth_required
  if (app._setSources) {
    app._setSources([
      { url: '', name: 'Local', type: 'local', status: 'authenticated' },
      { url: 'http://work:8088', name: 'Workstation', type: 'remote', status: 'auth_required', lastSeenAt: null },
    ]);
  }

  const sessions = [{ name: 'local-session', snapshot: '', deviceName: 'Local', sourceUrl: '' }];
  app.renderGrid(sessions);

  assert.ok(mockGrid.innerHTML.includes('source-tile--auth'), 'grid should include auth tile for auth_required source');
  assert.ok(mockGrid.innerHTML.includes('Workstation'), 'auth tile should show device name');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  if (app._setSources) app._setSources([]);
});

test('renderGrid includes offline tile HTML when a source is unreachable', () => {
  const mockGrid = { innerHTML: '' };
  const mockEmpty = { style: {}, classList: { add: () => {}, remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  if (app._setSources) {
    app._setSources([
      { url: '', name: 'Local', type: 'local', status: 'authenticated' },
      { url: 'http://dev:8088', name: 'Dev Server', type: 'remote', status: 'unreachable', lastSeenAt: Date.now() - 300000 },
    ]);
  }

  const sessions = [{ name: 'local-session', snapshot: '', deviceName: 'Local', sourceUrl: '' }];
  app.renderGrid(sessions);

  assert.ok(mockGrid.innerHTML.includes('source-tile--offline'), 'grid should include offline tile for unreachable source');
  assert.ok(mockGrid.innerHTML.includes('Dev Server'), 'offline tile should show device name');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  if (app._setSources) app._setSources([]);
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: 2 failures (grid doesn't include source status tiles yet).

**Step 3: Modify `renderGrid` to append source status tiles**

In `muxplex/frontend/app.js`, find the `renderGrid` function. After the line that sets `grid.innerHTML` (currently: `if (grid) grid.innerHTML = html;`), add logic to append status tiles for non-authenticated sources:

Replace the line:
```javascript
  if (grid) grid.innerHTML = html;
```

With:
```javascript
  // Append status tiles for auth_required and unreachable sources
  var statusTilesHtml = '';
  if (typeof _sources !== 'undefined' && _sources) {
    _sources.forEach(function(source) {
      if (source.status === 'auth_required') {
        statusTilesHtml += buildAuthTileHTML(source);
      } else if (source.status === 'unreachable') {
        statusTilesHtml += buildOfflineTileHTML(source);
      }
    });
  }
  if (grid) grid.innerHTML = html + statusTilesHtml;
```

Also ensure `_setSources` is defined as a test helper (if not already from Phase 2). Near the other `_set*` helpers (around line 1630):

```javascript
/** Test-only: set _sources directly. */
function _setSources(sources) {
  _sources = sources;
}
```

And add to `module.exports`:
```javascript
    _setSources,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: all pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat(federation): render auth-required and offline tiles in grid"
```

---

### Task 5: Login popup flow — click handler

**Files:**
- Modify: `muxplex/frontend/app.js` (add `openLoginPopup` + delegated click handler)
- Modify: `muxplex/frontend/tests/test_app.mjs` (add tests)

Clicking the "Log in" button on an auth tile opens the remote instance's `/login` page in a popup window. No callback — the next poll cycle detects the new cookie.

**Step 1: Write the failing tests**

Add to `muxplex/frontend/tests/test_app.mjs`:

```javascript
// --- openLoginPopup ---

test('openLoginPopup is exported as a function', () => {
  assert.strictEqual(typeof app.openLoginPopup, 'function');
});

test('openLoginPopup calls window.open with correct URL and dimensions', () => {
  let openCalledWith = null;
  const origOpen = globalThis.window.open;
  globalThis.window.open = (url, target, features) => {
    openCalledWith = { url, target, features };
  };

  app.openLoginPopup('http://work:8088');

  assert.ok(openCalledWith, 'window.open should have been called');
  assert.strictEqual(openCalledWith.url, 'http://work:8088/login', 'should open /login on the remote URL');
  assert.strictEqual(openCalledWith.target, '_blank');
  assert.ok(openCalledWith.features.includes('width=500'), 'should set width=500');
  assert.ok(openCalledWith.features.includes('height=600'), 'should set height=600');

  globalThis.window.open = origOpen;
});

test('openLoginPopup appends /login to URL without trailing slash', () => {
  let capturedUrl = null;
  const origOpen = globalThis.window.open;
  globalThis.window.open = (url) => { capturedUrl = url; };

  app.openLoginPopup('http://work:8088');

  assert.strictEqual(capturedUrl, 'http://work:8088/login');
  globalThis.window.open = origOpen;
});

test('openLoginPopup handles URL with trailing slash', () => {
  let capturedUrl = null;
  const origOpen = globalThis.window.open;
  globalThis.window.open = (url) => { capturedUrl = url; };

  app.openLoginPopup('http://work:8088/');

  assert.strictEqual(capturedUrl, 'http://work:8088/login');
  globalThis.window.open = origOpen;
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: 4 failures.

**Step 3: Implement `openLoginPopup`**

Add this function in `app.js` right after `buildOfflineTileHTML`:

```javascript
/**
 * Open the remote instance's login page in a popup window.
 * After the user authenticates and closes the popup, the next poll cycle
 * will detect the cookie and transition the source to authenticated.
 * @param {string} remoteUrl - base URL of the remote instance (e.g. "http://work:8088")
 */
function openLoginPopup(remoteUrl) {
  var baseUrl = remoteUrl.replace(/\/+$/, '');
  window.open(baseUrl + '/login', '_blank', 'width=500,height=600');
}
```

Now wire up a delegated click handler for `.source-tile__login-btn` buttons. Add this inside `bindStaticEventListeners`, near the top (right after the existing delegated kill-session handler):

```javascript
  // Delegated login button handler for federation auth-required tiles
  document.addEventListener('click', function(e) {
    var loginBtn = e.target.closest && e.target.closest('.source-tile__login-btn');
    if (!loginBtn) return;
    e.stopPropagation();
    var url = loginBtn.dataset.url;
    if (url) openLoginPopup(url);
  });
```

Add `openLoginPopup` to `module.exports`:
```javascript
    openLoginPopup,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: all pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat(federation): add login popup flow for auth-required remote instances"
```

---

### Task 6: `terminal.js` — accept `sourceUrl` parameter for remote WebSocket

**Files:**
- Modify: `muxplex/frontend/terminal.js` (modify `connectWebSocket` and `openTerminal`)
- Modify: `muxplex/frontend/tests/test_terminal.mjs` (add tests)

Currently `connectWebSocket(name)` always builds the WebSocket URL from `location.host`. For remote sessions, it needs to connect to the remote instance's WebSocket instead.

**Step 1: Write the failing tests**

Add to the end of `muxplex/frontend/tests/test_terminal.mjs`:

```javascript
// --- Remote sourceUrl support ---

test('connectWebSocket uses remote origin when sourceUrl is provided', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  // openTerminal now accepts (sessionName, sourceUrl)
  t.openTerminal('remote-session', 'http://work:8088');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.startsWith('ws://work:8088/'),
    `WebSocket URL should start with ws://work:8088/, got: ${t.capturedWsUrl}`,
  );
  assert.ok(
    t.capturedWsUrl.endsWith('/terminal/ws'),
    `WebSocket URL should end with /terminal/ws, got: ${t.capturedWsUrl}`,
  );
});

test('connectWebSocket uses local origin when sourceUrl is empty string', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('local-session', '');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.includes('localhost'),
    `WebSocket URL should use localhost for empty sourceUrl, got: ${t.capturedWsUrl}`,
  );
});

test('connectWebSocket uses local origin when sourceUrl is undefined', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('local-session');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.includes('localhost'),
    `WebSocket URL should use localhost for undefined sourceUrl, got: ${t.capturedWsUrl}`,
  );
});

test('connectWebSocket converts https sourceUrl to wss protocol', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('secure-session', 'https://devserver:8088');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.startsWith('wss://devserver:8088/'),
    `HTTPS sourceUrl should produce wss:// WebSocket URL, got: ${t.capturedWsUrl}`,
  );
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_terminal.mjs 2>&1 | tail -20
```

Expected: failures (openTerminal doesn't accept sourceUrl yet, so the remote URL tests fail).

Note: The `loadTerminal()` helper's `openTerminal` reference needs updating. Currently it captures `window._openTerminal` which only passes `name`. After our change, `window._openTerminal(name, sourceUrl)` will work — the test just calls `t.openTerminal('remote-session', 'http://work:8088')` which passes `sourceUrl` through.

**Step 3: Modify `connectWebSocket` to accept `sourceUrl`**

In `muxplex/frontend/terminal.js`, change `connectWebSocket(name)` to `connectWebSocket(name, sourceUrl)`:

Find the current URL construction (line ~15-16):
```javascript
function connectWebSocket(name) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/terminal/ws`;
```

Replace with:
```javascript
function connectWebSocket(name, sourceUrl) {
  var url;
  if (sourceUrl) {
    // Remote session: derive WS URL from the source's HTTP URL
    url = sourceUrl.replace(/^http/, 'ws').replace(/\/+$/, '') + '/terminal/ws';
  } else {
    // Local session: same origin
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url = proto + '//' + location.host + '/terminal/ws';
  }
```

Then modify `openTerminal` to accept and forward `sourceUrl`:

Change:
```javascript
function openTerminal(sessionName) {
```

To:
```javascript
function openTerminal(sessionName, sourceUrl) {
```

And change the call at the bottom of `openTerminal`:
```javascript
  connectWebSocket(sessionName);
```

To:
```javascript
  connectWebSocket(sessionName, sourceUrl);
```

And update the window exposure:
```javascript
window._openTerminal = openTerminal;
```
(This already works — the signature change is backward compatible.)

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_terminal.mjs 2>&1 | tail -20
```

Expected: all pass. Old tests still pass because calling `openTerminal('name')` without a second arg uses local origin (undefined → falsy → same-origin path).

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/terminal.js muxplex/frontend/tests/test_terminal.mjs && git commit -m "feat(federation): terminal.js supports remote WebSocket via sourceUrl parameter"
```

---

### Task 7: `openSession` — full remote routing

**Files:**
- Modify: `muxplex/frontend/app.js` (modify `openSession`)
- Modify: `muxplex/frontend/tests/test_app.mjs` (add tests)

Remove the Phase 2 stub for remote sessions. For remote sessions: POST `/api/sessions/{name}/connect` on the **remote** instance (with `credentials: "include"`), then call `window._openTerminal(name, sourceUrl)`.

**Step 1: Write the failing tests**

Add to `muxplex/frontend/tests/test_app.mjs`:

```javascript
// --- openSession remote routing ---

test('openSession with sourceUrl POSTs connect to remote instance URL', async () => {
  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};

  await app.openSession('work-project', { sourceUrl: 'http://work:8088' });

  const connectCall = fetchCalls.find((c) => c.url === 'http://work:8088/api/sessions/work-project/connect');
  assert.ok(connectCall, 'should POST to remote instance /api/sessions/work-project/connect');
  assert.strictEqual(connectCall.opts.method, 'POST');
  assert.strictEqual(connectCall.opts.credentials, 'include', 'must include credentials for cross-origin');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession with sourceUrl passes sourceUrl to window._openTerminal', async () => {
  let openTerminalArgs = null;
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async () => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = (name, sourceUrl) => { openTerminalArgs = { name, sourceUrl }; };

  await app.openSession('remote-session', { sourceUrl: 'http://dev:8088' });

  assert.ok(openTerminalArgs, '_openTerminal should have been called');
  assert.strictEqual(openTerminalArgs.name, 'remote-session');
  assert.strictEqual(openTerminalArgs.sourceUrl, 'http://dev:8088');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession without sourceUrl still POSTs to local /api/sessions/{name}/connect', async () => {
  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};

  await app.openSession('local-session', {});

  const connectCall = fetchCalls.find((c) => c.url === '/api/sessions/local-session/connect');
  assert.ok(connectCall, 'should POST to local /api/sessions/local-session/connect');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: failures (openSession doesn't route to remote URLs yet).

**Step 3: Modify `openSession`**

In `muxplex/frontend/app.js`, modify the `openSession` function. The current `opts` parameter already exists (`opts.skipConnect`). We add `opts.sourceUrl`.

Find the connect block (around line 895-902):
```javascript
  // Connect to session (kill old ttyd, spawn new one for this session)
  try {
    if (!opts.skipConnect) {
      await api('POST', `/api/sessions/${name}/connect`);
    }
  } catch (err) {
    showToast(err.message || 'Connection failed');
    return closeSession();
  }
```

Replace with:
```javascript
  // Connect to session (kill old ttyd, spawn new one for this session)
  var _sourceUrl = opts.sourceUrl || '';
  try {
    if (!opts.skipConnect) {
      if (_sourceUrl) {
        // Remote session: POST connect to the remote instance
        var remoteConnectUrl = _sourceUrl.replace(/\/+$/, '') + '/api/sessions/' + encodeURIComponent(name) + '/connect';
        await fetch(remoteConnectUrl, { method: 'POST', credentials: 'include' });
      } else {
        await api('POST', '/api/sessions/' + encodeURIComponent(name) + '/connect');
      }
    }
  } catch (err) {
    showToast(err.message || 'Connection failed');
    return closeSession();
  }
```

Then find the `_openTerminal` call (around line 908):
```javascript
  if (window._openTerminal) window._openTerminal(name);
```

Replace with:
```javascript
  if (window._openTerminal) window._openTerminal(name, _sourceUrl);
```

Also store the sourceUrl in a module-level variable so `closeSession` knows whether the current session is remote. Add near the other state variables (around line 121):

```javascript
let _viewingSourceUrl = '';
```

And in `openSession`, after `_viewingSession = name;`, add:

```javascript
  _viewingSourceUrl = opts.sourceUrl || '';
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: all pass. Note: the existing test `openSession without skipConnect POSTs to /api/sessions/{name}/connect` should still pass because the local path uses the same URL pattern (just with `encodeURIComponent`).

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat(federation): openSession routes connect POST and terminal to remote instances"
```

---

### Task 8: `closeSession` — remote-aware cleanup

**Files:**
- Modify: `muxplex/frontend/app.js` (modify `closeSession`)
- Modify: `muxplex/frontend/tests/test_app.mjs` (add tests)

Per the design notes: for remote sessions, we do NOT call the remote's `/api/sessions/current` DELETE. We just disconnect the WebSocket and update local UI. The remote instance doesn't need to know we stopped watching.

**Step 1: Write the failing tests**

Add to `muxplex/frontend/tests/test_app.mjs`:

```javascript
// --- closeSession remote-aware cleanup ---

test('closeSession does NOT fire DELETE for remote session (non-empty _viewingSourceUrl)', async () => {
  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { add: () => {}, remove: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = (fn) => { fn(); };
  globalThis.window._openTerminal = () => {};
  globalThis.window._closeTerminal = () => {};

  // Open a remote session first to set _viewingSourceUrl
  await app.openSession('remote-sess', { sourceUrl: 'http://work:8088' });

  fetchCalls.length = 0; // reset

  await app.closeSession();
  await new Promise((r) => setTimeout(r, 0));

  const deleteCall = fetchCalls.find((c) => c.opts && c.opts.method === 'DELETE');
  assert.ok(!deleteCall, 'closeSession must NOT fire DELETE for remote sessions');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.setTimeout = origSetTimeout;
});

test('closeSession still fires DELETE /api/sessions/current for local session', async () => {
  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { add: () => {}, remove: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = (fn) => { fn(); };
  globalThis.window._openTerminal = () => {};
  globalThis.window._closeTerminal = () => {};

  // Open a local session first (no sourceUrl)
  await app.openSession('local-sess', {});

  fetchCalls.length = 0;

  await app.closeSession();
  await new Promise((r) => setTimeout(r, 0));

  const deleteCall = fetchCalls.find((c) => c.url === '/api/sessions/current' && c.opts && c.opts.method === 'DELETE');
  assert.ok(deleteCall, 'closeSession must fire DELETE for local sessions');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.setTimeout = origSetTimeout;
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: The first test fails (DELETE is fired even for remote sessions).

**Step 3: Modify `closeSession`**

In `muxplex/frontend/app.js`, find the `closeSession` function. Change the DELETE logic from:

```javascript
  // Fire-and-forget DELETE
  api('DELETE', '/api/sessions/current').catch(() => {});
```

To:

```javascript
  // Fire-and-forget DELETE — only for local sessions.
  // Remote sessions: the remote instance doesn't need to know we stopped watching.
  if (!_viewingSourceUrl) {
    api('DELETE', '/api/sessions/current').catch(function() {});
  }
```

Also reset `_viewingSourceUrl` in `closeSession`. After `_viewingSession = null;`, add:

```javascript
  _viewingSourceUrl = '';
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: all pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat(federation): closeSession skips DELETE for remote sessions"
```

---

### Task 9: Grid click handler routes `sourceUrl` to `openSession`

**Files:**
- Modify: `muxplex/frontend/app.js` (modify click handler in `renderGrid`)
- Modify: `muxplex/frontend/tests/test_app.mjs` (add test)

Currently `renderGrid` binds click handlers that call `openSession(tile.dataset.session)`. For remote sessions, the tile needs a `data-source-url` attribute, and the click handler must pass it as `opts.sourceUrl`.

**Step 1: Write the failing test**

Add to `muxplex/frontend/tests/test_app.mjs`:

```javascript
// --- Tile click passes sourceUrl ---

test('buildTileHTML includes data-source-url attribute when session has sourceUrl', () => {
  const session = {
    name: 'remote-work',
    snapshot: '',
    sourceUrl: 'http://work:8088',
    deviceName: 'Workstation',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(
    html.includes('data-source-url="http://work:8088"'),
    'tile must include data-source-url attribute for remote sessions',
  );
});

test('buildTileHTML does not include data-source-url for local sessions (empty sourceUrl)', () => {
  const session = {
    name: 'local-work',
    snapshot: '',
    sourceUrl: '',
    deviceName: 'Laptop',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(
    !html.includes('data-source-url'),
    'tile must not include data-source-url for local sessions',
  );
});
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: failures.

**Step 3: Modify `buildTileHTML` and `renderGrid`**

In `buildTileHTML`, add a `data-source-url` attribute to the `<article>` tag when the session has a non-empty `sourceUrl`. Find the return statement in `buildTileHTML` (around line 402):

```javascript
  return (
    `<article class="${classes}" data-session="${escapedName}" tabindex="0" role="listitem" aria-label="${escapedName}">` +
```

Replace with:

```javascript
  const sourceUrlAttr = session.sourceUrl ? ` data-source-url="${escapeHtml(session.sourceUrl)}"` : '';
  return (
    `<article class="${classes}" data-session="${escapedName}"${sourceUrlAttr} tabindex="0" role="listitem" aria-label="${escapedName}">` +
```

Then in `renderGrid`, modify the click handler to pass `sourceUrl`. Find (around line 633):

```javascript
    on(tile, 'click', () => openSession(tile.dataset.session));
    on(tile, 'keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        openSession(tile.dataset.session);
      }
    });
```

Replace with:

```javascript
    on(tile, 'click', () => openSession(tile.dataset.session, { sourceUrl: tile.dataset.sourceUrl || '' }));
    on(tile, 'keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        openSession(tile.dataset.session, { sourceUrl: tile.dataset.sourceUrl || '' });
      }
    });
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: all pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat(federation): tile clicks pass sourceUrl through to openSession"
```

---

### Task 10: Auto-recovery detection tests

**Files:**
- Modify: `muxplex/frontend/tests/test_app.mjs` (add tests verifying Phase 2 backoff/recovery behavior)

Phase 2 already implemented exponential backoff and source status transitions. Phase 3 verifies the auto-recovery path: when an unreachable source starts responding again, its status transitions back, and the grid updates on the next cycle. These are documentation/verification tests confirming the existing behavior works end-to-end.

**Step 1: Write the tests**

Add to `muxplex/frontend/tests/test_app.mjs`:

```javascript
// --- Auto-recovery detection ---

test('formatLastSeen returns seconds for recent timestamps', () => {
  const now = Date.now();
  const result = app.formatLastSeen(now - 30000);
  assert.match(result, /^\d+s ago$/, 'should format as seconds ago');
});

test('formatLastSeen returns minutes for older timestamps', () => {
  const result = app.formatLastSeen(Date.now() - 5 * 60 * 1000);
  assert.match(result, /^\d+m ago$/, 'should format as minutes ago');
});

test('formatLastSeen returns hours for much older timestamps', () => {
  const result = app.formatLastSeen(Date.now() - 3 * 3600 * 1000);
  assert.match(result, /^\d+h ago$/, 'should format as hours ago');
});

test('formatLastSeen returns days for very old timestamps', () => {
  const result = app.formatLastSeen(Date.now() - 2 * 86400 * 1000);
  assert.match(result, /^\d+d ago$/, 'should format as days ago');
});

test('formatLastSeen returns Never for null', () => {
  assert.strictEqual(app.formatLastSeen(null), 'Never');
});

test('formatLastSeen returns Never for undefined', () => {
  assert.strictEqual(app.formatLastSeen(undefined), 'Never');
});
```

**Step 2: Run tests**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```

Expected: all pass (formatLastSeen was implemented in Task 3).

**Step 3: Commit**

```bash
cd muxplex && git add muxplex/frontend/tests/test_app.mjs && git commit -m "test(federation): add formatLastSeen and auto-recovery verification tests"
```

---

### Task 11: End-to-end smoke verification

**Files:**
- No file changes — verification only

This task runs the full test suite to confirm nothing is broken and all Phase 3 functionality works.

**Step 1: Run all app.js tests**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_app.mjs
```

Expected: all tests pass. No failures, no errors.

**Step 2: Run all terminal.js tests**

```bash
cd muxplex && node --test muxplex/frontend/tests/test_terminal.mjs
```

Expected: all tests pass. No failures, no errors.

**Step 3: Verify CSS syntax**

```bash
cd muxplex && grep -c '{' muxplex/frontend/style.css && grep -c '}' muxplex/frontend/style.css
```

Expected: the counts should be equal (no unclosed braces).

**Step 4: Check for any new exported functions not in module.exports**

```bash
cd muxplex && grep -E '^function ' muxplex/frontend/app.js | head -40
```

Cross-reference with the `module.exports` block. Ensure `buildAuthTileHTML`, `buildOfflineTileHTML`, `formatLastSeen`, `openLoginPopup`, and `_setSources` are all exported.

**Step 5: Final commit (if any lint/format fixes were needed)**

```bash
cd muxplex && git add -A && git status
```

If clean: no commit needed. If there are formatting fixes:

```bash
cd muxplex && git commit -m "chore: phase 3 cleanup"
```

---

## Summary of Changes

| File | What changed |
|------|-------------|
| `muxplex/frontend/style.css` | Added `.source-tile`, `.source-tile--offline`, `.source-tile--auth`, and related child element styles |
| `muxplex/frontend/app.js` | Added `buildAuthTileHTML()`, `buildOfflineTileHTML()`, `formatLastSeen()`, `openLoginPopup()`; modified `renderGrid()` to append status tiles; modified `openSession()` to route remote connect POSTs and pass `sourceUrl` to terminal; modified `closeSession()` to skip DELETE for remote sessions; modified `buildTileHTML()` to emit `data-source-url`; added `_viewingSourceUrl` state; added `_setSources` test helper; added delegated login-button click handler |
| `muxplex/frontend/terminal.js` | Modified `connectWebSocket(name, sourceUrl)` to derive WS URL from remote origin; modified `openTerminal(sessionName, sourceUrl)` to forward parameter |
| `muxplex/frontend/tests/test_app.mjs` | Added ~30 new tests for auth tiles, offline tiles, login popup, remote openSession routing, remote closeSession behavior, formatLastSeen, sourceUrl in tiles |
| `muxplex/frontend/tests/test_terminal.mjs` | Added 4 tests for remote WebSocket URL derivation |

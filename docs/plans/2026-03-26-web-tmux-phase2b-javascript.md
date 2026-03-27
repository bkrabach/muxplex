# Web-Tmux Dashboard Phase 2b — JavaScript Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Implement complete JavaScript behavior for the tmux web dashboard — session polling, tile rendering, bell notifications, zoom transitions, command palette, and xterm.js terminal with WebSocket connection.

**Architecture:** Vanilla ES5-compatible JavaScript in `app.js` and `terminal.js`. Pure functions at the top of `app.js` are exported conditionally for Node.js unit testing — the export block lives at the very bottom of `app.js` and remains there across all tasks. DOM manipulation uses `getElementById`. `xterm.js` and `FitAddon` are loaded from CDN in `index.html`. `terminal.js` exposes `openTerminal`/`closeTerminal` via `window._openTerminal`/`window._closeTerminal` to avoid circular dependency with `app.js`.

**Tech Stack:** Vanilla JavaScript (no framework, no build step), xterm.js 5.3.0 + xterm-addon-fit 0.8.0 (CDN), Node.js 18+ `node:test` (unit tests for pure functions only), WebSocket API (native browser).

---

## Quick Reference

| Files changed | What |
|---|---|
| `frontend/tests/test_app.mjs` | Node.js unit tests — pure functions only |
| `frontend/app.js` | Complete app — polling, rendering, keyboard, transitions |
| `frontend/terminal.js` | xterm.js Terminal + WebSocket + visualViewport |

**Test command (tasks 1-3):** `node --test frontend/tests/test_app.mjs`

**No Python tests change.** The 118 pytest tests from Phase 2a continue to pass and are never touched.

## Architecture Note: How `app.js` Grows Across Tasks

Tasks 1-3 build the **pure-function layer** at the top of `app.js`.
Tasks 4-15 add **runtime/DOM functions** above the conditional export block.

The conditional export block is written in Task 1 and **stays at the very bottom of `app.js` unchanged** for the rest of the plan. Every subsequent task that modifies `app.js` adds code _above_ that block.

The final structure of `app.js` (from top to bottom):
1. Pure functions (Tasks 1-3)
2. State variables (Task 4)
3. DOM helpers / `api` wrapper (Task 4)
4. `initDeviceId`, `trackInteraction`, `DOMContentLoaded` (Task 4)
5. `restoreState` (Task 5)
6. `startPolling`, `pollSessions`, `setConnectionStatus` (Task 6)
7. `renderGrid`, `buildTileHTML`, `escapeHtml`, `isMobile` (Task 7)
8. `requestNotificationPermission`, `handleBellTransitions` (Task 8)
9. `startHeartbeat`, `sendHeartbeat` (Task 9)
10. `openSession`, `closeSession`, `showToast` (Task 10)
11. `bindStaticEventListeners`, palette functions (Task 11)
12. `openBottomSheet`, `closeBottomSheet`, `renderSheetList` (Task 15)
13. Conditional CommonJS export block (Task 1 — never move this)

---

## Task 1: Test infrastructure + minimal `app.js` stub

**Files:**
- Create: `frontend/tests/test_app.mjs`
- Modify: `frontend/app.js` (replace single-line stub)

---

### Step 1: Write the failing test

Create file `frontend/tests/test_app.mjs`:

```javascript
// frontend/tests/test_app.mjs
// Node.js 18+ built-in test runner. Run: node --test frontend/tests/test_app.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

// Stub browser globals that app.js references at module scope.
// Must be set BEFORE require('app.js') runs.
global.document = {
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
global.window = {
  innerWidth: 1280,
  localStorage: { getItem: () => null, setItem: () => {} },
};
global.Notification = { permission: 'denied', requestPermission: async () => 'denied' };
global.navigator = { userAgent: 'TestAgent/1.0' };

// Load app.js (uses conditional CommonJS export).
const app = require(path.join(__dirname, '../app.js'));

const {
  formatTimestamp,
  sessionPriority,
  sortByPriority,
  filterByQuery,
  detectBellTransitions,
  generateDeviceId,
  buildHeartbeatPayload,
} = app;

// ── Task 1: smoke test ──────────────────────────────────────────────────────
test('app module loads without error', () => {
  assert.ok(typeof formatTimestamp === 'function', 'formatTimestamp should be exported');
  assert.ok(typeof sessionPriority === 'function', 'sessionPriority should be exported');
  assert.ok(typeof sortByPriority === 'function', 'sortByPriority should be exported');
  assert.ok(typeof filterByQuery === 'function', 'filterByQuery should be exported');
  assert.ok(typeof detectBellTransitions === 'function', 'detectBellTransitions should be exported');
  assert.ok(typeof generateDeviceId === 'function', 'generateDeviceId should be exported');
  assert.ok(typeof buildHeartbeatPayload === 'function', 'buildHeartbeatPayload should be exported');
});
```

---

### Step 2: Run the test — expect FAIL

```
node --test frontend/tests/test_app.mjs
```

Expected failure (existing `app.js` is a comment, not a module):
```
TypeError: Cannot read properties of undefined (reading 'formatTimestamp')
```
or similar. Good — the test correctly fails against the stub.

---

### Step 3: Replace `frontend/app.js` with minimal stubs + export block

**Replace the entire file** with:

```javascript
/* app.js — tmux web dashboard (Phase 2b)
 * Pure functions first, then runtime code, then conditional export at bottom.
 * NEVER move the export block — it must remain the last lines of this file. */

// ── Pure functions ──────────────────────────────────────────────────────────
// These stubs will be replaced in Tasks 2-3. They exist now so the export
// block can reference them and Task 1's smoke test passes.

function formatTimestamp(ts) { return ts == null ? '—' : ''; }
function sessionPriority(session) { return 'idle'; }
function sortByPriority(sessions) { return sessions.slice(); }
function filterByQuery(sessions, query) { return sessions; }
function detectBellTransitions(prevSessions, nextSessions) { return []; }
function generateDeviceId() { return 'dev-0'; }
function buildHeartbeatPayload(deviceId, viewingSession, viewMode, lastInteractionAt) {
  return { device_id: deviceId, label: 'stub', viewing_session: viewingSession,
           view_mode: viewMode, last_interaction_at: lastInteractionAt };
}

// ── Conditional CommonJS export for Node.js unit tests ─────────────────────
// In the browser, `module` is undefined — this block never executes.
// KEEP THIS BLOCK AT THE VERY BOTTOM OF app.js. NEVER MOVE IT.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    formatTimestamp,
    sessionPriority,
    sortByPriority,
    filterByQuery,
    detectBellTransitions,
    generateDeviceId,
    buildHeartbeatPayload,
  };
}
```

---

### Step 4: Run the test — expect PASS

```
node --test frontend/tests/test_app.mjs
```

Expected output (Node.js TAP format):
```
TAP version 13
# Subtest: app module loads without error
ok 1 - app module loads without error
...
# tests 1
# pass 1
# fail 0
```

---

### Step 5: Commit

```
git add frontend/tests/test_app.mjs frontend/app.js
git commit -m "feat: add Node.js test infrastructure for frontend pure functions"
```

---

## Task 2: Pure functions — `formatTimestamp`, `sessionPriority`, `sortByPriority`

**Files:**
- Modify: `frontend/tests/test_app.mjs` (add tests after Task 1 smoke test)
- Modify: `frontend/app.js` (replace first 3 stubs with real implementations)

---

### Step 1: Add failing tests to `test_app.mjs`

Add these test blocks **after** the Task 1 smoke test block (before the end of the file):

```javascript
// ── Task 2: formatTimestamp ─────────────────────────────────────────────────
test('formatTimestamp: null returns em-dash', () => {
  assert.strictEqual(formatTimestamp(null), '—');
  assert.strictEqual(formatTimestamp(undefined), '—');
});
test('formatTimestamp: seconds ago', () => {
  const now = Date.now() / 1000;
  assert.match(formatTimestamp(now - 5), /^5s ago$/);
});
test('formatTimestamp: minutes ago', () => {
  const now = Date.now() / 1000;
  assert.match(formatTimestamp(now - 125), /^2m ago$/);
});
test('formatTimestamp: hours ago', () => {
  const now = Date.now() / 1000;
  assert.match(formatTimestamp(now - 7260), /^2h ago$/);
});

// ── Task 2: sessionPriority ─────────────────────────────────────────────────
test('sessionPriority: returns bell when unseen_count > 0 and seen_at is null', () => {
  const s = { bell: { last_fired_at: 100, seen_at: null, unseen_count: 1 } };
  assert.strictEqual(sessionPriority(s), 'bell');
});
test('sessionPriority: returns bell when last_fired_at > seen_at', () => {
  const s = { bell: { last_fired_at: 200, seen_at: 100, unseen_count: 2 } };
  assert.strictEqual(sessionPriority(s), 'bell');
});
test('sessionPriority: returns idle when unseen_count is 0', () => {
  const s = { bell: { last_fired_at: null, seen_at: null, unseen_count: 0 } };
  assert.strictEqual(sessionPriority(s), 'idle');
});
test('sessionPriority: returns idle when seen_at >= last_fired_at', () => {
  const s = { bell: { last_fired_at: 100, seen_at: 100, unseen_count: 1 } };
  assert.strictEqual(sessionPriority(s), 'idle');
});

// ── Task 2: sortByPriority ──────────────────────────────────────────────────
test('sortByPriority: bell sessions sort before idle sessions', () => {
  const sessions = [
    { name: 'idle', bell: { last_fired_at: null, seen_at: null, unseen_count: 0 } },
    { name: 'bell', bell: { last_fired_at: 100, seen_at: null, unseen_count: 2 } },
  ];
  const sorted = sortByPriority(sessions);
  assert.strictEqual(sorted[0].name, 'bell');
  assert.strictEqual(sorted[1].name, 'idle');
});
test('sortByPriority: does not mutate input array', () => {
  const sessions = [
    { name: 'a', bell: { last_fired_at: null, seen_at: null, unseen_count: 0 } },
  ];
  const orig = sessions;
  sortByPriority(sessions);
  assert.strictEqual(sessions, orig, 'original array reference must not change');
});
```

---

### Step 2: Run tests — expect FAIL

```
node --test frontend/tests/test_app.mjs
```

Expected: the smoke test passes, but the new tests fail because:
- `formatTimestamp(now - 5)` returns `''` instead of `'5s ago'`
- `sessionPriority` always returns `'idle'`
- `sortByPriority` returns input array reference (mutates), not a sorted copy

---

### Step 3: Replace the first 3 stubs in `frontend/app.js`

Replace the three stub function bodies (leave everything else untouched, especially the export block):

```javascript
function formatTimestamp(ts) {
  if (ts == null) return '—';
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function sessionPriority(session) {
  const b = session.bell;
  const hasBell = b &&
    b.unseen_count > 0 &&
    (b.seen_at === null || b.last_fired_at > b.seen_at);
  if (hasBell) return 'bell';
  return 'idle';
}

function sortByPriority(sessions) {
  const order = { bell: 0, active: 1, idle: 2 };
  return sessions.slice().sort(
    (a, b) => (order[sessionPriority(a)] ?? 2) - (order[sessionPriority(b)] ?? 2)
  );
}
```

---

### Step 4: Run tests — expect PASS

```
node --test frontend/tests/test_app.mjs
```

Expected:
```
# tests 11
# pass 11
# fail 0
```

---

### Step 5: Commit

```
git add frontend/app.js frontend/tests/test_app.mjs
git commit -m "feat: add formatTimestamp, sessionPriority, sortByPriority pure functions"
```

---

## Task 3: Pure functions — `filterByQuery`, `detectBellTransitions`, `generateDeviceId`, `buildHeartbeatPayload`

**Files:**
- Modify: `frontend/tests/test_app.mjs` (add tests)
- Modify: `frontend/app.js` (replace remaining 4 stubs with real implementations)

---

### Step 1: Add failing tests to `test_app.mjs`

Append these blocks after the Task 2 tests:

```javascript
// ── Task 3: filterByQuery ───────────────────────────────────────────────────
test('filterByQuery: empty query returns all sessions', () => {
  const sessions = [{ name: 'a' }, { name: 'b' }];
  assert.strictEqual(filterByQuery(sessions, '').length, 2);
  assert.strictEqual(filterByQuery(sessions, null).length, 2);
});
test('filterByQuery: matches by substring, case-insensitive', () => {
  const sessions = [{ name: 'work' }, { name: 'web-tmux' }, { name: 'logs' }];
  const result = filterByQuery(sessions, 'W').map(s => s.name);
  assert.deepStrictEqual(result, ['work', 'web-tmux']);
});
test('filterByQuery: no match returns empty array', () => {
  const sessions = [{ name: 'alpha' }, { name: 'beta' }];
  assert.strictEqual(filterByQuery(sessions, 'zzz').length, 0);
});

// ── Task 3: detectBellTransitions ──────────────────────────────────────────
test('detectBellTransitions: detects new bell on existing session', () => {
  const prev = [{ name: 'a', bell: { unseen_count: 0, last_fired_at: null, seen_at: null } }];
  const next = [{ name: 'a', bell: { unseen_count: 1, last_fired_at: 100, seen_at: null } }];
  assert.deepStrictEqual(detectBellTransitions(prev, next), ['a']);
});
test('detectBellTransitions: no change returns empty array', () => {
  const sessions = [{ name: 'a', bell: { unseen_count: 0, last_fired_at: null, seen_at: null } }];
  assert.deepStrictEqual(detectBellTransitions(sessions, sessions), []);
});
test('detectBellTransitions: new session with bell fires', () => {
  const prev = [];
  const next = [{ name: 'b', bell: { unseen_count: 1, last_fired_at: 200, seen_at: null } }];
  assert.deepStrictEqual(detectBellTransitions(prev, next), ['b']);
});
test('detectBellTransitions: count increase fires, count decrease does not', () => {
  const prev = [{ name: 'a', bell: { unseen_count: 2, last_fired_at: 100, seen_at: null } }];
  const next_increase = [{ name: 'a', bell: { unseen_count: 3, last_fired_at: 200, seen_at: null } }];
  const next_same     = [{ name: 'a', bell: { unseen_count: 2, last_fired_at: 100, seen_at: null } }];
  assert.deepStrictEqual(detectBellTransitions(prev, next_increase), ['a']);
  assert.deepStrictEqual(detectBellTransitions(prev, next_same), []);
});

// ── Task 3: generateDeviceId ────────────────────────────────────────────────
test('generateDeviceId: returns a string matching d-[a-z0-9]+', () => {
  assert.match(generateDeviceId(), /^d-[a-z0-9]+$/);
});
test('generateDeviceId: returns unique IDs on repeated calls', () => {
  const ids = new Set(Array.from({ length: 10 }, generateDeviceId));
  assert.ok(ids.size > 1, 'generateDeviceId should return different values');
});

// ── Task 3: buildHeartbeatPayload ───────────────────────────────────────────
test('buildHeartbeatPayload: returns correct shape', () => {
  const p = buildHeartbeatPayload('d-abc', 'work', 'fullscreen', 123.45);
  assert.strictEqual(p.device_id, 'd-abc');
  assert.strictEqual(p.viewing_session, 'work');
  assert.strictEqual(p.view_mode, 'fullscreen');
  assert.strictEqual(p.last_interaction_at, 123.45);
  assert.ok(typeof p.label === 'string', 'label must be a string');
  assert.ok(p.label.length > 0, 'label must not be empty');
});
test('buildHeartbeatPayload: viewing_session can be null', () => {
  const p = buildHeartbeatPayload('d-xyz', null, 'grid', 0);
  assert.strictEqual(p.viewing_session, null);
  assert.strictEqual(p.view_mode, 'grid');
});
```

---

### Step 2: Run tests — expect FAIL

```
node --test frontend/tests/test_app.mjs
```

Expected: the 11 existing tests still pass; the 14 new ones fail because the stubs return wrong values.

---

### Step 3: Replace the remaining 4 stubs in `frontend/app.js`

Replace the four stub bodies (above the export block):

```javascript
function filterByQuery(sessions, query) {
  if (!query) return sessions;
  const q = query.toLowerCase();
  return sessions.filter(s => s.name.toLowerCase().includes(q));
}

function detectBellTransitions(prevSessions, nextSessions) {
  const prevMap = new Map(
    prevSessions.map(s => [s.name, s.bell ? s.bell.unseen_count : 0])
  );
  return nextSessions
    .filter(s => {
      const b = s.bell;
      if (!b || b.unseen_count === 0) return false;
      const prevCount = prevMap.has(s.name) ? prevMap.get(s.name) : 0;
      return b.unseen_count > prevCount;
    })
    .map(s => s.name);
}

function generateDeviceId() {
  const rand = Math.random().toString(36).slice(2, 10);
  return 'd-' + rand;
}

function buildHeartbeatPayload(deviceId, viewingSession, viewMode, lastInteractionAt) {
  var label = (typeof navigator !== 'undefined' && navigator.userAgent)
    ? navigator.userAgent.slice(0, 50)
    : 'unknown';
  return {
    device_id: deviceId,
    label: label,
    viewing_session: viewingSession,
    view_mode: viewMode,
    last_interaction_at: lastInteractionAt,
  };
}
```

---

### Step 4: Run tests — expect PASS

```
node --test frontend/tests/test_app.mjs
```

Expected:
```
# tests 25
# pass 25
# fail 0
```

---

### Step 5: Commit

```
git add frontend/app.js frontend/tests/test_app.mjs
git commit -m "feat: add filterByQuery, detectBellTransitions, generateDeviceId, buildHeartbeatPayload"
```

---

## Task 4: App initialization — device ID, fetch wrapper, interaction tracking, DOMContentLoaded

**Files:**
- Modify: `frontend/app.js` (add state variables, helpers, DOMContentLoaded handler)

No unit tests — requires DOM. Manual verification below.

---

### Step 1: Add state variables and runtime code to `frontend/app.js`

Add this block **below all the pure functions and above the conditional export block**:

```javascript
// ── Constants ───────────────────────────────────────────────────────────────
var POLL_MS = 2000;
var HEARTBEAT_MS = 5000;
var MOBILE_THRESHOLD = 600;

// ── Runtime state ───────────────────────────────────────────────────────────
var _deviceId = '';
var _currentSessions = [];
var _viewingSession = null;   // name of session open in expanded view, or null
var _viewMode = 'grid';       // 'grid' | 'fullscreen'
var _lastInteractionAt = Date.now() / 1000;
var _pollingTimer = null;
var _heartbeatTimer = null;
var _notificationPermission = 'default';

// ── DOM helpers ──────────────────────────────────────────────────────────────
function $(id) { return document.getElementById(id); }
function on(el, ev, fn) { if (el) el.addEventListener(ev, fn); }

// ── isMobile ─────────────────────────────────────────────────────────────────
function isMobile() { return window.innerWidth < MOBILE_THRESHOLD; }

// ── API fetch wrapper ────────────────────────────────────────────────────────
function api(method, path, body) {
  var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  return fetch(path, opts).then(function(res) {
    if (!res.ok) throw new Error('API ' + method + ' ' + path + ' failed: ' + res.status);
    return res.json();
  });
}

// ── Device ID ────────────────────────────────────────────────────────────────
function initDeviceId() {
  _deviceId = window.localStorage.getItem('tmux-web-device-id');
  if (!_deviceId) {
    _deviceId = generateDeviceId();
    window.localStorage.setItem('tmux-web-device-id', _deviceId);
  }
}

// ── Interaction tracking ──────────────────────────────────────────────────────
function trackInteraction() {
  _lastInteractionAt = Date.now() / 1000;
}

// ── DOMContentLoaded ──────────────────────────────────────────────────────────
// restoreState, startPolling, startHeartbeat, requestNotificationPermission,
// and bindStaticEventListeners are defined in Tasks 5, 6, 8, 9, 11.
// JavaScript hoists function declarations — calling them here is safe.
document.addEventListener('DOMContentLoaded', function() {
  initDeviceId();
  document.addEventListener('keydown', trackInteraction);
  document.addEventListener('click', trackInteraction);
  document.addEventListener('touchstart', trackInteraction, { passive: true });

  restoreState().then(function() {
    startPolling();
    startHeartbeat();
    requestNotificationPermission();
    bindStaticEventListeners();
  });
});
```

---

### Step 2: Run the Node.js unit tests — verify they still pass

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0` — the new runtime code doesn't break any pure-function tests because the `document.addEventListener` call runs only in the browser (in Node.js, `document.addEventListener` is a no-op via our global stub and `DOMContentLoaded` never fires).

---

### Step 3: Manual browser verification

Start the coordinator:
```
cd /home/bkrabach/dev/web-tmux
uvicorn coordinator.main:app --reload --port 8000
```

Open `http://localhost:8000` in a browser. Check:
1. DevTools Console — no errors on load
2. DevTools Application → Local Storage — key `tmux-web-device-id` exists with a value like `d-a3f9b2c1`
3. Console — no "restoreState is not defined" or "startPolling is not defined" errors yet (you will see "TypeError: restoreState is not a function" — that is EXPECTED; it will be fixed in Task 5)

> **Note:** Until Tasks 5-11 are complete, the browser console will show `TypeError: restoreState is not a function`. That is correct — those functions don't exist yet. The plan adds them in order.

---

### Step 4: Commit

```
git add frontend/app.js
git commit -m "feat: add app initialization — device_id, fetch wrapper, interaction tracking"
```

---

## Task 5: State restoration — `GET /api/state` on load

**Files:**
- Modify: `frontend/app.js` (add `restoreState`)

No unit tests — requires fetch. Manual verification below.

---

### Step 1: Add `restoreState` to `frontend/app.js`

Add this block **above the DOMContentLoaded listener** (keep it below the `trackInteraction` function, and above the export block):

```javascript
// ── State restoration ─────────────────────────────────────────────────────────
// Called once on page load. If the coordinator knows about an active session
// (a previous browser tab connected), re-open it without calling /connect again
// (ttyd is already running). skipConnect: true skips the POST /connect call.
function restoreState() {
  return api('GET', '/api/state').then(function(state) {
    if (state.active_session) {
      return openSession(state.active_session, { skipConnect: true });
    }
  }).catch(function(err) {
    console.warn('restoreState: failed to fetch state', err);
  });
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

Start the coordinator and open the browser. Now:
1. Console — `restoreState is not a function` error **is gone**
2. Network tab — `GET /api/state` request fires on page load
3. If `active_session` is null (no prior session): stays on grid view
4. If `active_session` is set: should attempt `openSession` (which still fails gracefully because `openSession` doesn't exist yet — that is expected until Task 10)

---

### Step 4: Commit

```
git add frontend/app.js
git commit -m "feat: add state restoration on page load"
```

---

## Task 6: Session polling — `GET /api/sessions` every 2s

**Files:**
- Modify: `frontend/app.js` (add `startPolling`, `pollSessions`, `setConnectionStatus`)

No unit tests — requires fetch and DOM. Manual verification below.

---

### Step 1: Add polling functions to `frontend/app.js`

Add this block **below `restoreState`** (above DOMContentLoaded, above export block):

```javascript
// ── Connection status ─────────────────────────────────────────────────────────
function setConnectionStatus(level) {
  // level: 'ok' | 'warn' | 'err'
  var el = $('connection-status');
  if (!el) return;
  el.className = 'connection-status connection-status--' + level;
  if (level === 'ok')   el.textContent = '●';
  if (level === 'warn') el.textContent = '◌ slow';
  if (level === 'err')  el.textContent = '✕ offline';
}

// ── Session polling ───────────────────────────────────────────────────────────
var _pollFailCount = 0;

function startPolling() {
  if (_pollingTimer) return;
  pollSessions(); // immediate first fetch
  _pollingTimer = setInterval(pollSessions, POLL_MS);
}

function pollSessions() {
  api('GET', '/api/sessions').then(function(sessions) {
    var prev = _currentSessions;
    _currentSessions = sessions;
    _pollFailCount = 0;
    setConnectionStatus('ok');
    renderGrid(sessions);
    handleBellTransitions(prev, sessions);
  }).catch(function(err) {
    _pollFailCount++;
    setConnectionStatus(_pollFailCount > 2 ? 'err' : 'warn');
    console.warn('pollSessions failed', err);
  });
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

Open `http://localhost:8000`. Check:
1. Network tab — `GET /api/sessions` fires on page load and every ~2 seconds
2. `#connection-status` element in header shows `●` (green dot) when requests succeed
3. Console — no errors (ignore "renderGrid is not defined" — that is Task 7)

If you stop the coordinator and wait 3 polls:
- Status changes to `◌ slow` after first failure, then `✕ offline` after the third

---

### Step 4: Commit

```
git add frontend/app.js
git commit -m "feat: add session polling loop — GET /api/sessions every 2s"
```

---

## Task 7: `renderGrid` — tile HTML for desktop grid and mobile list

**Files:**
- Modify: `frontend/app.js` (add `renderGrid`, `buildTileHTML`, `escapeHtml`)

No unit tests — requires DOM. Manual verification below.

---

### Step 1: Add grid rendering functions to `frontend/app.js`

Add this block **below the polling functions** (above DOMContentLoaded, above export block):

```javascript
// ── Grid rendering ────────────────────────────────────────────────────────────
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function buildTileHTML(session, index, mobile) {
  var b = session.bell || {};
  var hasBell = b.unseen_count > 0 && (b.seen_at === null || b.last_fired_at > b.seen_at);
  var snap = (session.snapshot || '').trimEnd();
  // Show last 20 lines of snapshot
  var lines = snap ? snap.split('\n').slice(-20).join('\n') : '(no output)';
  var priority = sessionPriority(session);

  var bellClass = hasBell ? ' session-tile--bell' : '';
  var tierClass = mobile ? ' session-tile--tier-' + priority : '';
  var bellCount = b.unseen_count > 9 ? '9+' : b.unseen_count;
  var bellDot = hasBell
    ? '<span class="tile-bell" aria-hidden="true"></span>' +
      '<span class="tile-bell-count">' + bellCount + '</span>'
    : '';

  return '<article class="session-tile' + bellClass + tierClass + '"' +
    ' data-session="' + escapeHtml(session.name) + '"' +
    ' tabindex="0"' +
    ' role="listitem"' +
    ' aria-label="Session ' + escapeHtml(session.name) + (hasBell ? ', has activity' : '') + '">' +
    '<div class="tile-header">' +
      '<span class="tile-name">' + escapeHtml(session.name) + '</span>' +
      '<span class="tile-meta">' + bellDot +
        '<span class="tile-time">' + formatTimestamp(b.last_fired_at || null) + '</span>' +
      '</span>' +
    '</div>' +
    '<div class="tile-body">' +
      '<pre class="tile-pre">' + escapeHtml(lines) + '</pre>' +
    '</div>' +
  '</article>';
}

function renderGrid(sessions) {
  var grid = $('session-grid');
  var emptyState = $('empty-state');
  if (!grid) return;

  if (!sessions || sessions.length === 0) {
    grid.innerHTML = '';
    if (emptyState) emptyState.classList.remove('hidden');
    return;
  }
  if (emptyState) emptyState.classList.add('hidden');

  var mobile = isMobile();
  var ordered = mobile ? sortByPriority(sessions) : sessions;

  grid.innerHTML = ordered.map(function(s, i) {
    return buildTileHTML(s, i + 1, mobile);
  }).join('');

  // Bind click handlers on newly-inserted tiles
  var tiles = grid.querySelectorAll('.session-tile');
  for (var i = 0; i < tiles.length; i++) {
    (function(tile) {
      tile.addEventListener('click', function() { openSession(tile.dataset.session); });
      tile.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSession(tile.dataset.session); }
      });
    })(tiles[i]);
  }

  // If in expanded view, update the pill bell badge for "other sessions"
  if (_viewMode === 'fullscreen') updatePillBell();
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

Open `http://localhost:8000`. You should now see:
1. Session tiles rendered in the grid (one per tmux session)
2. Each tile shows session name, last activity time, and terminal snapshot text
3. Run `printf '\a'` in a tmux session — within 2 seconds the tile gets amber border glow (`.session-tile--bell` class)
4. On mobile-size viewport (< 600px wide), tiles reorder: bell first, then active, then idle

---

### Step 4: Commit

```
git add frontend/app.js
git commit -m "feat: add renderGrid — tile HTML for desktop grid and mobile list"
```

---

## Task 8: Bell indicators + Browser Notifications

**Files:**
- Modify: `frontend/app.js` (add `requestNotificationPermission`, `handleBellTransitions`)

No unit tests — requires Notification API and DOM. Manual verification below.

---

### Step 1: Add bell functions to `frontend/app.js`

Add this block **below `renderGrid`** (above DOMContentLoaded, above export block):

```javascript
// ── Browser Notifications ─────────────────────────────────────────────────────
function requestNotificationPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    _notificationPermission = 'granted';
    return;
  }
  if (Notification.permission === 'default') {
    Notification.requestPermission().then(function(perm) {
      _notificationPermission = perm;
    });
  } else {
    _notificationPermission = Notification.permission; // 'denied'
  }
}

function handleBellTransitions(prevSessions, nextSessions) {
  var transitions = detectBellTransitions(prevSessions, nextSessions);
  if (transitions.length === 0) return;
  transitions.forEach(function(name) {
    if (_notificationPermission === 'granted' && document.hidden) {
      // `tag` deduplicates: one notification per session, not per bell event.
      new Notification('Activity in: ' + name, {
        body: 'tmux session needs attention',
        tag: 'tmux-bell-' + name,
      });
    }
  });
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

1. Open `http://localhost:8000` — browser should prompt for notification permission. Allow it.
2. Open another browser tab (so the dashboard tab is backgrounded: `document.hidden === true`).
3. In a tmux session: `printf '\a'`
4. Within 2 seconds: an OS notification appears: "Activity in: \<session-name\>"
5. Switch back to dashboard tab — tile has amber glow

---

### Step 4: Commit

```
git add frontend/app.js
git commit -m "feat: add bell indicators and browser notification on bell transition"
```

---

## Task 9: Heartbeat sender — `POST /api/heartbeat` every 5s

**Files:**
- Modify: `frontend/app.js` (add `startHeartbeat`, `sendHeartbeat`)

No unit tests — requires fetch. Manual verification below.

---

### Step 1: Add heartbeat functions to `frontend/app.js`

Add this block **below `handleBellTransitions`** (above DOMContentLoaded, above export block):

```javascript
// ── Heartbeat ─────────────────────────────────────────────────────────────────
function startHeartbeat() {
  if (_heartbeatTimer) return;
  sendHeartbeat(); // immediate first send
  _heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_MS);
}

function sendHeartbeat() {
  var payload = buildHeartbeatPayload(
    _deviceId,
    _viewingSession,
    _viewMode,
    _lastInteractionAt
  );
  api('POST', '/api/heartbeat', payload).catch(function(err) {
    console.warn('heartbeat failed', err);
  });
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

Open `http://localhost:8000`. Check:
1. Network tab — `POST /api/heartbeat` fires on page load and every ~5 seconds
2. Request body includes: `device_id`, `label`, `viewing_session` (null when on grid), `view_mode` ("grid"), `last_interaction_at` (unix timestamp)
3. Response: `{"device_id": "d-...", "status": "ok"}`
4. `GET /api/state` → check `devices` key — your device should appear

---

### Step 4: Commit

```
git add frontend/app.js
git commit -m "feat: add heartbeat sender — POST /api/heartbeat every 5s"
```

---

## Task 10: `openSession`/`closeSession` — zoom transition and view switching

**Files:**
- Modify: `frontend/app.js` (add `openSession`, `closeSession`, `showToast`)

No unit tests — requires DOM. Manual verification below.

---

### Step 1: Add session transition functions to `frontend/app.js`

Add this block **below `sendHeartbeat`** (above DOMContentLoaded, above export block):

```javascript
// ── Toast notification ────────────────────────────────────────────────────────
function showToast(msg) {
  var toast = $('toast');
  if (!toast) return;
  toast.textContent = msg;
  toast.classList.remove('hidden');
  setTimeout(function() { toast.classList.add('hidden'); }, 3000);
}

// ── updatePillBell ────────────────────────────────────────────────────────────
// Updates the bell badge on the floating session pill.
// Called from renderGrid (when polling) and openSession.
function updatePillBell() {
  var pillBell = $('session-pill-bell');
  if (!pillBell) return;
  var othersWithBell = _currentSessions.filter(function(s) {
    return s.name !== _viewingSession && s.bell && s.bell.unseen_count > 0;
  });
  if (othersWithBell.length > 0) pillBell.classList.remove('hidden');
  else pillBell.classList.add('hidden');
}

// ── openSession ───────────────────────────────────────────────────────────────
// Opens a session in the expanded terminal view.
// opts.skipConnect = true: don't POST /connect (ttyd already running after restore).
function openSession(name, opts) {
  opts = opts || {};
  var skipConnect = opts.skipConnect === true;

  // Update app state
  _viewingSession = name;
  _viewMode = 'fullscreen';

  var nameEl = $('expanded-session-name');
  if (nameEl) nameEl.textContent = name;

  // Find tile for zoom origin (may not exist if view was already expanded)
  var tile = document.querySelector('[data-session="' + CSS.escape(name) + '"]');
  var grid = $('session-grid');

  if (tile && grid) {
    var rect = tile.getBoundingClientRect();
    // Pin tile at its current position, then animate to fullscreen
    tile.style.cssText = 'position:fixed;top:' + rect.top + 'px;left:' + rect.left +
      'px;width:' + rect.width + 'px;height:' + rect.height +
      'px;z-index:50;transition:all 250ms ease-in-out;';
    tile.classList.add('session-tile--expanding');
    grid.classList.add('session-grid--dimming');

    // Force reflow so the browser registers the starting position
    void tile.getBoundingClientRect();

    // Animate to full viewport
    requestAnimationFrame(function() {
      tile.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;' +
        'z-index:50;transition:all 250ms ease-in-out;border-radius:0;';
    });
  }

  // Wait for animation, then switch views
  return new Promise(function(resolve) {
    setTimeout(function() {
      var viewOverview = $('view-overview');
      var viewExpanded = $('view-expanded');
      if (viewOverview) { viewOverview.classList.remove('view--active'); viewOverview.classList.add('hidden'); }
      if (viewExpanded) { viewExpanded.classList.remove('hidden'); viewExpanded.classList.add('view--active'); }

      // Show mobile session pill
      if (isMobile()) {
        var pill = $('session-pill');
        var pillLabel = $('session-pill-label');
        if (pill) pill.classList.remove('hidden');
        if (pillLabel) pillLabel.textContent = name;
        updatePillBell();
      }

      // Connect to session (spawns ttyd)
      if (skipConnect) {
        if (window._openTerminal) window._openTerminal(name);
        resolve();
        return;
      }

      api('POST', '/api/sessions/' + encodeURIComponent(name) + '/connect')
        .then(function() {
          if (window._openTerminal) window._openTerminal(name);
          resolve();
        })
        .catch(function(err) {
          console.error('openSession: connect failed', err);
          showToast("Couldn't connect to session '" + name + "'");
          closeSession().then(resolve);
        });
    }, 260);
  });
}

// ── closeSession ──────────────────────────────────────────────────────────────
function closeSession() {
  _viewMode = 'grid';
  _viewingSession = null;

  // Close xterm.js terminal
  if (window._closeTerminal) window._closeTerminal();

  // Kill ttyd connection
  api('DELETE', '/api/sessions/current').catch(function() {});

  // Switch back to overview
  var viewExpanded = $('view-expanded');
  var viewOverview = $('view-overview');
  if (viewExpanded) { viewExpanded.classList.remove('view--active'); viewExpanded.classList.add('hidden'); }
  if (viewOverview) { viewOverview.classList.remove('hidden'); viewOverview.classList.add('view--active'); }

  // Hide mobile pill
  var pill = $('session-pill');
  if (pill) pill.classList.add('hidden');

  return Promise.resolve();
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

Open `http://localhost:8000`. Check:
1. Click a session tile — tile animates (zoom expand) to fill the viewport
2. Expanded header shows session name
3. Network — `POST /api/sessions/<name>/connect` fires, returns `{"active_session": ..., "ttyd_port": 7682}`
4. Terminal area shows (empty — `terminal.js` isn't wired yet, added in Tasks 12-13)
5. Click the `←` back button — returns to grid view
6. Grid tiles reappear
7. Network — `DELETE /api/sessions/current` fires

If ttyd is not running, a toast appears: "Couldn't connect to session '...'" and the view returns to grid.

---

### Step 4: Commit

```
git add frontend/app.js
git commit -m "feat: add openSession/closeSession — zoom transition and view switching"
```

---

## Task 11: Command palette — Ctrl+K, keyboard nav, filter, Escape, G-to-grid

**Files:**
- Modify: `frontend/app.js` (add `bindStaticEventListeners`, all palette functions)

No unit tests — requires DOM. Manual verification below.

---

### Step 1: Add command palette functions to `frontend/app.js`

Add this block **below `closeSession`** (above DOMContentLoaded, above export block):

```javascript
// ── Command palette ────────────────────────────────────────────────────────────
var _paletteSelectedIndex = 0;
var _paletteFilteredSessions = [];

function openPalette() {
  var palette = $('command-palette');
  if (!palette) return;
  palette.classList.remove('hidden');

  _paletteFilteredSessions = _currentSessions.slice();
  renderPaletteList(_paletteFilteredSessions);
  _paletteSelectedIndex = 0;
  highlightPaletteItem(0);

  var input = $('palette-input');
  if (input) {
    input.value = '';
    input.focus();
    input.addEventListener('input', onPaletteInput);
  }
}

function closePalette() {
  var palette = $('command-palette');
  if (palette) palette.classList.add('hidden');
  var input = $('palette-input');
  if (input) input.removeEventListener('input', onPaletteInput);
}

function onPaletteInput(e) {
  _paletteFilteredSessions = filterByQuery(_currentSessions, e.target.value);
  renderPaletteList(_paletteFilteredSessions);
  _paletteSelectedIndex = 0;
  highlightPaletteItem(0);
}

function renderPaletteList(sessions) {
  var list = $('palette-list');
  if (!list) return;
  var items = sessions.slice(0, 9);
  list.innerHTML = items.map(function(s, i) {
    var hasBell = s.bell && s.bell.unseen_count > 0;
    return '<li class="palette-item" role="option"' +
      ' data-session="' + escapeHtml(s.name) + '" tabindex="-1">' +
      '<span class="palette-item__index">' + (i + 1) + '</span>' +
      '<span class="palette-item__name">' + escapeHtml(s.name) + '</span>' +
      (hasBell ? '<span class="palette-item__bell" aria-hidden="true">🔔</span>' : '') +
      '<span class="palette-item__time">' + formatTimestamp(s.bell ? s.bell.last_fired_at : null) + '</span>' +
    '</li>';
  }).join('');

  list.querySelectorAll('.palette-item').forEach(function(item) {
    item.addEventListener('click', function() {
      var name = item.dataset.session;
      closePalette();
      openSession(name);
    });
  });
}

function highlightPaletteItem(index) {
  var items = $('palette-list') ? $('palette-list').querySelectorAll('.palette-item') : [];
  for (var i = 0; i < items.length; i++) {
    items[i].classList.toggle('palette-item--selected', i === index);
  }
}

function handlePaletteKeydown(e) {
  var list = $('palette-list');
  var items = list ? Array.prototype.slice.call(list.querySelectorAll('.palette-item')) : [];
  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault();
      _paletteSelectedIndex = Math.min(_paletteSelectedIndex + 1, items.length - 1);
      highlightPaletteItem(_paletteSelectedIndex);
      break;
    case 'ArrowUp':
      e.preventDefault();
      _paletteSelectedIndex = Math.max(_paletteSelectedIndex - 1, 0);
      highlightPaletteItem(_paletteSelectedIndex);
      break;
    case 'Enter':
      e.preventDefault();
      var selected = items[_paletteSelectedIndex];
      if (selected) { closePalette(); openSession(selected.dataset.session); }
      break;
    case 'Escape':
      e.preventDefault();
      closePalette();
      break;
    case 'g':
    case 'G':
      e.preventDefault();
      closePalette();
      closeSession();
      break;
    default:
      // Number keys 1-9 jump directly to session
      if (/^[1-9]$/.test(e.key)) {
        e.preventDefault();
        var item = items[parseInt(e.key, 10) - 1];
        if (item) { closePalette(); openSession(item.dataset.session); }
      }
  }
}

// ── Global keyboard handler ────────────────────────────────────────────────────
function handleGlobalKeydown(e) {
  var palette = $('command-palette');
  var paletteOpen = palette && !palette.classList.contains('hidden');

  if (paletteOpen) {
    handlePaletteKeydown(e);
    return;
  }

  if (_viewMode === 'fullscreen') {
    // Backtick ` or Ctrl+K → open palette
    if (e.key === '`' || (e.ctrlKey && e.key === 'k')) {
      e.preventDefault();
      openPalette();
      return;
    }
    // Escape → back to grid (when palette is closed)
    if (e.key === 'Escape') {
      closeSession();
      return;
    }
  }
}

// ── Static event listener binding ─────────────────────────────────────────────
// Called once from DOMContentLoaded after all functions are defined.
function bindStaticEventListeners() {
  // Back button (← in expanded header)
  on($('back-btn'), 'click', function() { closeSession(); });

  // ⌘K button in expanded header
  on($('palette-trigger'), 'click', function() { openPalette(); });

  // Palette backdrop — click outside to close
  on($('palette-backdrop'), 'click', function() { closePalette(); });

  // Global keyboard shortcuts
  document.addEventListener('keydown', handleGlobalKeydown);

  // Session pill → open bottom sheet (Task 15 adds body of openBottomSheet)
  on($('session-pill'), 'click', function() { openBottomSheet(); });

  // Bottom sheet backdrop
  on($('sheet-backdrop'), 'click', function() { closeBottomSheet(); });
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

Open `http://localhost:8000`. Open a session by clicking a tile. Then:
1. Press Ctrl+K or `` ` `` — command palette dialog opens
2. Type a session name fragment — list filters in real time
3. Arrow Down/Up — highlighted item moves
4. Press `2` — second session opens directly
5. Press Escape — palette closes, stays in expanded view
6. Press Ctrl+K again to reopen, press `G` — returns to grid view
7. In expanded view, press Escape (palette closed) — returns to grid view

---

### Step 4: Commit

```
git add frontend/app.js
git commit -m "feat: add command palette — Ctrl+K, keyboard navigation, filter"
```

---

## Task 12: `terminal.js` — xterm.js Terminal + FitAddon initialization

**Files:**
- Modify: `frontend/terminal.js` (replace stub entirely with xterm.js init)

No unit tests — requires xterm.js CDN (browser-only). Manual verification below.

---

### Step 1: Replace `frontend/terminal.js` with xterm.js init code

**Replace the entire file** with:

```javascript
/* terminal.js — xterm.js terminal management for tmux-web
 *
 * Loaded after app.js (see index.html script order). Exposes openTerminal and
 * closeTerminal to app.js via window._ to avoid circular dependencies.
 *
 * Dependencies: xterm@5.3.0 + xterm-addon-fit@0.8.0, both loaded from CDN
 * in index.html before this script runs. They are available as window.Terminal
 * and window.FitAddon.FitAddon.
 */

var SCROLLBACK_MOBILE = 500;
var SCROLLBACK_DESKTOP = 5000;
var MOBILE_THRESHOLD = 600;

var _term = null;
var _fitAddon = null;
var _ws = null;
var _reconnectTimer = null;
var _currentSession = null;
var _vpHandler = null;

function isMobileTerm() { return window.innerWidth < MOBILE_THRESHOLD; }

// ── createTerminal ────────────────────────────────────────────────────────────
// Disposes any existing terminal, creates a fresh xterm.js Terminal instance.
function createTerminal() {
  if (_term) {
    try { _term.dispose(); } catch (e) {}
    _term = null;
    _fitAddon = null;
  }

  var Terminal = window.Terminal;
  var FitAddonClass = window.FitAddon && window.FitAddon.FitAddon;

  if (!Terminal) {
    console.error('terminal.js: xterm.js not loaded from CDN');
    return;
  }

  _term = new Terminal({
    cursorBlink: true,
    fontSize: isMobileTerm() ? 12 : 14,
    fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
    theme: {
      background: '#000000',
      foreground: '#c9d1d9',
      cursor: '#58a6ff',
      selectionBackground: 'rgba(88, 166, 255, 0.3)',
    },
    scrollback: isMobileTerm() ? SCROLLBACK_MOBILE : SCROLLBACK_DESKTOP,
    allowProposedApi: true,
  });

  if (FitAddonClass) {
    _fitAddon = new FitAddonClass();
    _term.loadAddon(_fitAddon);
  }
}

// ── openTerminal ──────────────────────────────────────────────────────────────
// Called by app.js openSession() after the expanded view is visible.
function openTerminal(sessionName) {
  _currentSession = sessionName;

  var container = document.getElementById('terminal-container');
  if (!container) {
    console.error('terminal.js: #terminal-container not found');
    return;
  }

  createTerminal();
  if (!_term) return;

  _term.open(container);
  if (_fitAddon) {
    try { _fitAddon.fit(); } catch (e) {}
  }

  connectWebSocket(sessionName);
  initVisualViewport();
}

// ── closeTerminal ─────────────────────────────────────────────────────────────
// Called by app.js closeSession().
function closeTerminal() {
  if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
  if (_ws) { try { _ws.close(); } catch (e) {} _ws = null; }
  if (_term) { try { _term.dispose(); } catch (e) {} _term = null; }
  if (_vpHandler && window.visualViewport) {
    window.visualViewport.removeEventListener('resize', _vpHandler);
    _vpHandler = null;
  }
  _currentSession = null;

  // Clear the container so stale canvas doesn't flash on next open
  var container = document.getElementById('terminal-container');
  if (container) container.innerHTML = '';
}

// ── connectWebSocket and initVisualViewport are defined in Tasks 13 and 14 ──
// Forward stubs so closeTerminal above compiles without reference errors.
function connectWebSocket(sessionName) {}
function initVisualViewport() {}

// ── Expose to app.js via window ───────────────────────────────────────────────
// app.js checks window._openTerminal and window._closeTerminal before calling.
window._openTerminal = openTerminal;
window._closeTerminal = closeTerminal;
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0` (terminal.js is not loaded by Node.js tests)

---

### Step 3: Manual browser verification

Open `http://localhost:8000`. Click a session tile. In the expanded view:
1. DevTools Console — no `xterm.js not loaded` error
2. `#terminal-container` has a `canvas` element inside it (xterm.js rendered)
3. Terminal cursor is visible and blinking
4. The terminal is sized to fill the container (FitAddon working)
5. Note: typing produces no output yet — WebSocket is stubbed until Task 13

---

### Step 4: Commit

```
git add frontend/terminal.js
git commit -m "feat: add terminal.js — xterm.js Terminal and FitAddon initialization"
```

---

## Task 13: WebSocket connection to ttyd via `/terminal/`

**Files:**
- Modify: `frontend/terminal.js` (replace `connectWebSocket` stub with real implementation)

No unit tests — requires WebSocket + browser. Manual verification below.

---

### Step 1: Replace the `connectWebSocket` stub in `frontend/terminal.js`

Find this line in `terminal.js`:
```javascript
function connectWebSocket(sessionName) {}
```

Replace it with:

```javascript
// ── WebSocket to ttyd ─────────────────────────────────────────────────────────
// ttyd runs on port 7682; Caddy proxies /terminal/ → ws://localhost:7682
function connectWebSocket(sessionName) {
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var url = proto + '//' + location.host + '/terminal/';
  var reconnectOverlay = document.getElementById('reconnect-overlay');

  function connect() {
    if (!_currentSession) return; // session was closed before connect resolved
    _ws = new WebSocket(url);
    _ws.binaryType = 'arraybuffer';

    _ws.addEventListener('open', function() {
      if (reconnectOverlay) reconnectOverlay.classList.add('hidden');
    });

    _ws.addEventListener('message', function(e) {
      if (!_term) return;
      if (e.data instanceof ArrayBuffer) {
        _term.write(new Uint8Array(e.data));
      } else {
        _term.write(e.data);
      }
    });

    _ws.addEventListener('close', function() {
      if (!_currentSession) return; // intentional close via closeTerminal()
      if (reconnectOverlay) reconnectOverlay.classList.remove('hidden');
      _reconnectTimer = setTimeout(connect, 2000);
    });

    _ws.addEventListener('error', function(e) {
      console.warn('terminal.js: WebSocket error', e);
    });

    // Forward terminal keystrokes → ttyd
    if (_term) {
      _term.onData(function(data) {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          _ws.send(data);
        }
      });
    }

    // Notify ttyd of terminal size changes
    if (_term) {
      _term.onResize(function(size) {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          // ttyd accepts resize as a JSON message
          _ws.send(JSON.stringify({ columns: size.cols, rows: size.rows }));
        }
      });
    }
  }

  connect();
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

Open `http://localhost:8000`. Click a session tile. In the expanded view:
1. Terminal renders and shows the tmux session output
2. Type commands — they execute in the real tmux session
3. Close browser tab (or manually close the WebSocket via DevTools Network → WS → X)
   - "Reconnecting…" overlay appears on `#reconnect-overlay`
   - After 2 seconds, it automatically reconnects and overlay disappears
4. Navigate back to grid (← button), then click the same session again
   - Terminal re-opens cleanly with no stale canvas

---

### Step 4: Commit

```
git add frontend/terminal.js
git commit -m "feat: add WebSocket connection to ttyd via /terminal/"
```

---

## Task 14: `visualViewport` mobile keyboard resize

**Files:**
- Modify: `frontend/terminal.js` (replace `initVisualViewport` stub with real implementation)

No unit tests — requires browser + mobile emulation. Manual verification below.

---

### Step 1: Replace the `initVisualViewport` stub in `frontend/terminal.js`

Find this line in `terminal.js`:
```javascript
function initVisualViewport() {}
```

Replace it with:

```javascript
// ── visualViewport keyboard resize ────────────────────────────────────────────
// On mobile, when the software keyboard appears, window.innerHeight shrinks but
// the layout doesn't reflow automatically. We use window.visualViewport to detect
// the actual visible height and resize #terminal-container to fit above the keyboard.
function initVisualViewport() {
  if (!window.visualViewport) return;

  // Remove any previous listener to avoid stacking on reconnect
  if (_vpHandler) window.visualViewport.removeEventListener('resize', _vpHandler);

  _vpHandler = function() {
    if (!_term || !_fitAddon) return;
    var container = document.getElementById('terminal-container');
    if (!container) return;

    var vvh = window.visualViewport.height;
    var headerHeight = 44; // matches CSS --header-height var
    var termHeight = Math.max(100, vvh - headerHeight);
    container.style.height = termHeight + 'px';

    try { _fitAddon.fit(); } catch (e) {}
  };

  window.visualViewport.addEventListener('resize', _vpHandler);

  // Fire immediately to handle the case where the keyboard was already open
  _vpHandler();
}
```

---

### Step 2: Run Node.js tests — verify still passing

```
node --test frontend/tests/test_app.mjs
```

Expected: `# pass 25  # fail 0`

---

### Step 3: Manual browser verification

1. Open `http://localhost:8000` in Chrome DevTools → Toggle device toolbar (set to iPhone-size, < 600px wide)
2. Click a session tile to open expanded view
3. Tap in the terminal area — software keyboard appears
4. Verify terminal is still visible above the keyboard (container height adjusted)
5. Type a command — it executes

Without this fix, the terminal would be partially hidden behind the keyboard on mobile.

---

### Step 4: Commit

```
git add frontend/terminal.js
git commit -m "feat: add visualViewport keyboard resize for mobile terminal"
```

---

## Task 15: Mobile bottom sheet + session pill

**Files:**
- Modify: `frontend/app.js` (add `openBottomSheet`, `closeBottomSheet`, `renderSheetList`)

No unit tests — requires DOM. Manual verification below.

---

### Step 1: Add bottom sheet functions to `frontend/app.js`

Add this block **below `bindStaticEventListeners`** (above the conditional export block):

```javascript
// ── Mobile bottom sheet ────────────────────────────────────────────────────────
// Displayed when the user taps the session pill while in expanded view.
// Shows all sessions ordered by priority. Tapping switches to that session.

function openBottomSheet() {
  var sheet = $('bottom-sheet');
  if (!sheet) return;
  renderSheetList();
  sheet.classList.remove('hidden');
}

function closeBottomSheet() {
  var sheet = $('bottom-sheet');
  if (sheet) sheet.classList.add('hidden');
}

function renderSheetList() {
  var list = $('sheet-list');
  if (!list) return;
  var sorted = sortByPriority(_currentSessions);
  list.innerHTML = sorted.map(function(s) {
    var hasBell = s.bell && s.bell.unseen_count > 0;
    var isActive = s.name === _viewingSession;
    return '<li class="sheet-item' + (isActive ? ' sheet-item--active' : '') + '"' +
      ' data-session="' + escapeHtml(s.name) + '" role="option">' +
      '<span class="sheet-item__name">' + escapeHtml(s.name) + '</span>' +
      (hasBell ? '<span class="sheet-item__bell" aria-hidden="true">🔔</span>' : '') +
      '<span class="sheet-item__time">' + formatTimestamp(s.bell ? s.bell.last_fired_at : null) + '</span>' +
    '</li>';
  }).join('');

  list.querySelectorAll('.sheet-item').forEach(function(item) {
    item.addEventListener('click', function() {
      var name = item.dataset.session;
      closeBottomSheet();
      if (name !== _viewingSession) {
        // Close current session first, then open new one
        closeSession().then(function() { openSession(name); });
      }
    });
  });
}
```

---

### Step 2: Run all Node.js tests — final check

```
node --test frontend/tests/test_app.mjs
```

Expected:
```
# tests 25
# pass 25
# fail 0
```

---

### Step 3: Run all Python tests — verify nothing broke

```
cd /home/bkrabach/dev/web-tmux
python -m pytest coordinator/tests/ --ignore=coordinator/tests/test_integration.py -q
```

Expected: **118 passed, 0 failed**

---

### Step 4: Manual browser verification (mobile)

1. Open `http://localhost:8000` in DevTools mobile mode (< 600px wide)
2. Click a session tile — expanded view opens, floating pill appears at bottom with session name
3. Tap the pill — bottom sheet slides up showing all sessions, sorted (bell first)
4. Active session has `.sheet-item--active` class (visually distinguished)
5. Tap a different session — sheet closes, new session opens

On desktop (> 600px wide):
- Pill is hidden (`.hidden` class stays on `#session-pill`)
- Bottom sheet does not appear

---

### Step 5: Commit

```
git add frontend/app.js
git commit -m "feat: add mobile bottom sheet and session pill"
```

---

## Final Verification Checklist

After all 15 tasks are complete, run this full verification sequence:

### Check 1: Node.js unit tests
```
node --test frontend/tests/test_app.mjs
```
Expected: `# tests 25  # pass 25  # fail 0`

### Check 2: Python test suite
```
cd /home/bkrabach/dev/web-tmux
python -m pytest coordinator/tests/ --ignore=coordinator/tests/test_integration.py -q
```
Expected: `118 passed, 0 failed`

### Check 3: Ruff linter
```
ruff check coordinator/ && ruff format --check coordinator/
```
Expected: `All checks passed.`

### Check 4: File existence
```
ls -la frontend/app.js frontend/terminal.js frontend/tests/test_app.mjs
```
All three files must exist and be non-empty.

### Check 5: Behavior smoke test
```
uvicorn coordinator.main:app --port 8000
```
Then open `http://localhost:8000` and verify:
- Session tiles render
- `●` connection status shown
- Clicking a tile opens expanded view with working terminal
- Ctrl+K opens command palette
- Back button returns to grid

---

## Scope Boundaries — Phase 2b Does NOT Include

| Deferred to | What |
|---|---|
| Phase 3 | Caddy configuration (`/terminal/` WebSocket proxy) |
| Phase 3 | systemd service unit |
| Phase 3 | Service worker / offline PWA support |
| Phase 3 | Browser-tester E2E (Playwright) |
| Phase 3 | Push notifications beyond Browser Notifications API |
| v2 | Session creation / renaming UI |
| v2 | Drag-to-reorder tiles |

> **Note on Caddy:** Until Caddy is configured (Phase 3), the WebSocket to `/terminal/` will fail in production. In development, run ttyd manually on port 7682 and add a `--proxy-headers` Caddy route, OR test via the uvicorn dev server with a manually-started ttyd process attached to a tmux session by name.

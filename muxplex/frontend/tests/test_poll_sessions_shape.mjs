// Tests for pollSessions()'s session-array boundary guard (muxplex-k3o).
//
// WHY THESE ARE BEHAVIORAL, NOT SOURCE-GREP TESTS: the defect is entirely
// about what a running poll does with a body it did not expect. api() throws
// on non-2xx and a broken body makes res.json() throw -- both land in
// pollSessions' own catch, which is why _currentSessions survives those. A
// 200 carrying well-formed JSON that simply is not a session list is neither:
// it used to be assigned straight through, and the only complaint came later,
// from renderGrid() calling an array method that wasn't there -- swallowed by
// that same catch. None of that is visible in the source text. So this file
// loads app.js into its own vm context per test (the shared-global loading
// model index.html uses, see test_shared_scope.mjs) and drives pollSessions()
// against a scripted api() stub.
//
// The retained value is observed through handleBellTransitions(prev, sessions)
// rather than through a test-only getter: on the poll AFTER the malformed one,
// `prev` IS _currentSessions as the malformed poll left it. That makes the
// assertion a statement about behavior the app actually depends on, using only
// exports that already existed before this fix -- so a run against unmodified
// code fails for the real reason, not because a new export is missing.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const appJsPath = join(__dirname, '..', 'app.js');
const appJsSource = fs.readFileSync(appJsPath, 'utf-8');

/** A DOM element stub that answers any unknown method with a no-op. */
function elementStub() {
  return new Proxy(
    {
      style: {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      textContent: '',
      dataset: {},
      querySelector: () => null,
      querySelectorAll: () => [],
    },
    {
      get(target, prop) {
        if (prop in target) return target[prop];
        return () => undefined;
      },
      set(target, prop, value) { target[prop] = value; return true; },
    },
  );
}

/**
 * Load a fresh app.js into its own vm context with a scripted sessions feed.
 *
 * @param {object} opts
 * @param {any[]} opts.bodies - one GET body per poll, in order. Each is
 *   returned verbatim by res.json(), so a non-array here is exactly the 200
 *   an intermediary would produce.
 * @param {boolean} [opts.multiDevice] - drive the federation endpoint instead.
 */
function loadApp(opts) {
  const bodies = opts.bodies;
  const toasts = [];
  const warnings = [];
  const requestedPaths = [];
  const gridRenders = [];
  const bellTransitions = [];
  const connectionStatuses = [];
  let pollIndex = 0;

  const sandbox = {
    console: {
      warn: (...args) => warnings.push(args.map(String).join(' ')),
      error: () => {},
      log: () => {},
    },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    TextEncoder,
    TextDecoder,
    performance: { now: () => Date.now() },
    navigator: { userAgent: 'node-poll-shape-test' },
    location: { protocol: 'http:', host: 'localhost', href: '' },
    Math,
    localStorage: {
      _store: {},
      getItem(k) { return Object.prototype.hasOwnProperty.call(this._store, k) ? this._store[k] : null; },
      setItem(k, v) { this._store[k] = String(v); },
      removeItem(k) { delete this._store[k]; },
    },
    document: {
      // #session-pill-bell must EXIST, or updatePillBell() early-returns on
      // `if (!el) return;` and never reaches its unguarded
      // _currentSessions.some() -- which would make the consumer-safety test
      // below pass vacuously, against fixed and broken code alike. Everything
      // else stays null: pollSessions' own collaborators are stubbed on the
      // context, and a blanket element stub would pull unrelated render paths
      // into these tests.
      getElementById: (id) => (id === 'session-pill-bell' ? elementStub() : null),
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: elementStub,
      addEventListener() {},
      removeEventListener() {},
      body: elementStub(),
    },
    Notification: { permission: 'default', requestPermission: async () => 'default' },
    addEventListener() {},
    removeEventListener() {},
    module: { exports: {} },
  };
  sandbox.window = sandbox;

  const ctx = vm.createContext(sandbox);
  vm.runInContext(appJsSource, ctx, { filename: 'app.js' });
  const app = ctx.module.exports;

  // --- Scripted server ---------------------------------------------------
  // Always a 200: api() only throws on non-2xx, and that path is already
  // safe. The whole point is the body that arrives WITH a success status.
  ctx.api = async function api(method, path) {
    requestedPaths.push(method + ' ' + path);
    const body = bodies[Math.min(pollIndex, bodies.length - 1)];
    pollIndex++;
    return { json: async () => body };
  };

  // Collaborators pollSessions calls on the success path. Recorded rather
  // than stubbed blind, so a test can assert what the renderer was HANDED.
  ctx.renderGrid = function renderGrid(sessions) { gridRenders.push(sessions); };
  ctx.renderSidebar = function renderSidebar() {};
  ctx.handleBellTransitions = function handleBellTransitions(prev, sessions) {
    bellTransitions.push({ prev, sessions });
  };
  ctx.updateSessionPill = function updateSessionPill() {};
  ctx.updateFaviconBadge = function updateFaviconBadge() {};
  ctx.updatePageTitle = function updatePageTitle() {};
  ctx.setConnectionStatus = function setConnectionStatus(level) { connectionStatuses.push(level); };
  ctx.showToast = function showToast(msg) { toasts.push(String(msg)); };

  app._setServerSettings({ multi_device_enabled: !!opts.multiDevice, views: [] });

  return { app, ctx, toasts, warnings, requestedPaths, gridRenders, bellTransitions, connectionStatuses };
}

const SEEDED = [{ name: 'alpha', bell: { unseen_count: 0 } }];
const FRESH = [
  { name: 'alpha', bell: { unseen_count: 0 } },
  { name: 'beta', bell: { unseen_count: 0 } },
];

// A captive portal / CDN error envelope: a perfectly well-formed JSON object
// answered with 200. res.json() resolves, so nothing throws at the boundary.
const ERROR_ENVELOPE = { error: 'not authenticated', code: 401 };

// ─── 1. The retained value ───────────────────────────────────────────────
// The core of the item: a non-array 200 must not become _currentSessions.
// Observed on the NEXT poll, whose `prev` is whatever the malformed poll
// left behind.

test('a 200 with a non-array body does not become _currentSessions', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE, FRESH] });

  await h.app.pollSessions(); // seeds a real list
  await h.app.pollSessions(); // the malformed one
  await h.app.pollSessions(); // recovery -- carries `prev` out for us

  const recovery = h.bellTransitions[h.bellTransitions.length - 1];
  assert.ok(Array.isArray(recovery.prev), 'the retained value must still be an array');
  assert.deepEqual(
    recovery.prev,
    SEEDED,
    'the malformed body replaced the previous session list',
  );
});

// ─── 2. Nothing downstream is handed the bad value ───────────────────────

test('a non-array 200 is never handed to renderGrid', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE] });

  await h.app.pollSessions();
  await h.app.pollSessions();

  for (const rendered of h.gridRenders) {
    assert.ok(Array.isArray(rendered), 'renderGrid was handed a non-array body');
  }
  assert.equal(h.gridRenders.length, 1, 'the malformed poll should not have re-rendered');
});

// ─── 3. The acceptance criterion, verbatim ───────────────────────────────
// "no consumer of _currentSessions throws on a missing array method".
// updatePillBell() calls _currentSessions.some() with no guard at all --
// it is the consumer that proves the boundary held.

test('consumers calling array methods on _currentSessions still work after a non-array 200', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE] });

  await h.app.pollSessions();
  await h.app.pollSessions();

  assert.doesNotThrow(
    () => h.app.updatePillBell(),
    'updatePillBell threw -- _currentSessions is not an array',
  );
});

// ─── 4. Surfaced, not swallowed ──────────────────────────────────────────
// The existing catch degrades the connection indicator and says nothing
// else. A malformed 200 is the case where that indicator alone misleads:
// the request SUCCEEDED, so "offline" does not describe what happened.

test('a non-array 200 is surfaced to the user rather than silently swallowed', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE] });

  await h.app.pollSessions();
  await h.app.pollSessions();

  assert.equal(h.toasts.length, 1, 'the user was told nothing about the malformed response');
  assert.match(h.toasts[0], /session/i);
});

test('a non-array 200 is logged with the shape that actually arrived', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE] });

  await h.app.pollSessions();
  await h.app.pollSessions();

  const warning = h.warnings.join('\n');
  assert.match(warning, /pollSessions/, 'no diagnosable warning was logged');
  assert.match(warning, /\/api\/sessions/, 'the warning does not name the endpoint');
  assert.match(warning, /error|code/, 'the warning does not describe the body that arrived');
});

test('a malformed poll degrades the connection indicator like any other failed poll', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE] });

  await h.app.pollSessions();
  await h.app.pollSessions();

  assert.equal(
    h.connectionStatuses[h.connectionStatuses.length - 1],
    'warn',
    'a poll that yielded no usable session list still reported a healthy connection',
  );
});

// ─── 5. The toast is once per episode, not once per tick ─────────────────
// pollSessions runs every ~2s. An un-latched notice would be ~30 toasts a
// minute for as long as the intermediary misbehaves.

test('a persistent malformed feed notifies once, not on every poll', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE] });

  await h.app.pollSessions();
  for (let i = 0; i < 5; i++) await h.app.pollSessions();

  assert.equal(h.toasts.length, 1, 'the malformed-response notice repeated on every tick');
});

test('the notice can fire again after the feed recovers and breaks a second time', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE, FRESH, ERROR_ENVELOPE] });

  await h.app.pollSessions(); // good
  await h.app.pollSessions(); // bad   -> notice
  await h.app.pollSessions(); // good  -> latch clears
  await h.app.pollSessions(); // bad   -> notice again

  assert.equal(h.toasts.length, 2, 'a second, separate outage went unreported');
});

// ─── 6. Every non-array shape, and no false positives ────────────────────

test('every non-array JSON body is refused, not just objects', async () => {
  for (const body of [{ detail: 'nope' }, 'a string', 42, true, null]) {
    const h = loadApp({ bodies: [SEEDED, body, FRESH] });

    await h.app.pollSessions();
    await h.app.pollSessions();
    await h.app.pollSessions();

    const recovery = h.bellTransitions[h.bellTransitions.length - 1];
    assert.deepEqual(
      recovery.prev,
      SEEDED,
      'a ' + (body === null ? 'null' : typeof body) + ' body replaced the session list',
    );
  }
});

test('an empty array is a legitimate answer and is still accepted', async () => {
  const h = loadApp({ bodies: [SEEDED, []] });

  await h.app.pollSessions();
  await h.app.pollSessions();

  assert.equal(h.toasts.length, 0, 'an empty session list was misreported as malformed');
  assert.equal(h.gridRenders.length, 2, 'an empty session list was not rendered');
  assert.deepEqual(h.gridRenders[1], [], 'the empty list did not reach the grid');
  assert.equal(h.connectionStatuses[h.connectionStatuses.length - 1], 'ok');
});

// ─── 7. The guard covers both endpoints ──────────────────────────────────
// The endpoint is chosen per-poll from multi_device_enabled; a guard that
// only covered one of them would leave the federated user exposed.

test('the guard covers the federation endpoint too', async () => {
  const h = loadApp({ bodies: [SEEDED, ERROR_ENVELOPE, FRESH], multiDevice: true });

  await h.app.pollSessions();
  await h.app.pollSessions();
  await h.app.pollSessions();

  assert.ok(
    h.requestedPaths.every((p) => p.includes('/api/federation/sessions')),
    'the fixture did not actually exercise the federation endpoint',
  );
  const recovery = h.bellTransitions[h.bellTransitions.length - 1];
  assert.deepEqual(recovery.prev, SEEDED);
  assert.match(h.warnings.join('\n'), /\/api\/federation\/sessions/);
});

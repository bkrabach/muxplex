// Tests for createNewSession's auto-add-to-view path and the settings CAS
// retry policy behind it (muxplex-htg).
//
// WHY THESE ARE BEHAVIORAL, NOT SOURCE-GREP TESTS: the bug being fixed here
// is entirely about what happens on the failure paths -- a second CAS
// conflict, a peer with no device_id, a create submitted before
// /api/instance-info has landed. None of those are visible in the source
// text; they only show up when the code actually runs against a server that
// says no. So this file loads app.js into its own vm context per test (the
// same shared-global loading model index.html uses, see test_shared_scope.mjs)
// and drives createNewSession() with a scripted api() stub.
//
// app.js's collaborators (api, showToast, loadServerSettings, pollSessions,
// _rerenderViewDependentUI) are top-level `function` declarations, so in a
// classic script they are properties of the global object -- overriding them
// on the context AFTER evaluation is what lets each test script a server's
// answers without touching app.js's own module state. The `let`-scoped state
// (_serverSettings, _activeView, _localDeviceId) is NOT reachable that way,
// which is why _setServerSettings/_setActiveView (already exported for tests)
// are used instead, and why _localDeviceId is exercised by leaving it at its
// real load-time value (null) rather than being poked directly.

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

const LOCAL_DEVICE_ID = 'dev-local-uuid';

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
 * Load a fresh app.js into its own vm context and wire a scripted server.
 *
 * @param {object} opts
 * @param {object[]} opts.views - initial `views` in server settings.
 * @param {string} opts.activeView - the view the user is looking at.
 * @param {function(number): ('ok'|'conflict'|'backstop')} opts.patchOutcome -
 *   given the 1-based PATCH attempt number, what the server answers.
 * @param {object|null} [opts.instanceInfo] - body of GET /api/instance-info.
 */
function loadApp(opts) {
  const views = JSON.parse(JSON.stringify(opts.views));
  const patchCalls = [];
  const toasts = [];
  const warnings = [];
  let settingsUpdatedAt = 100;

  // Server truth, as this fixture's stubbed loadServerSettings() reports it.
  let serverSettings = {
    views: JSON.parse(JSON.stringify(views)),
    auto_open_created: false, // skip the readiness poll -- a sibling owns it
    settings_updated_at: settingsUpdatedAt,
  };

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
    navigator: { userAgent: 'node-auto-add-test' },
    location: { protocol: 'http:', host: 'localhost', href: '' },
    Math,
    localStorage: {
      _store: {},
      getItem(k) { return Object.prototype.hasOwnProperty.call(this._store, k) ? this._store[k] : null; },
      setItem(k, v) { this._store[k] = String(v); },
      removeItem(k) { delete this._store[k]; },
    },
    document: {
      getElementById: () => null, // no #session-grid -> no loading tile
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

  // --- Scripted server -------------------------------------------------
  function httpError(status, body) {
    const err = new Error('HTTP ' + status);
    err.status = status;
    err.body = body;
    return err;
  }

  ctx.api = async function api(method, path, body) {
    if (method === 'POST' && /\/sessions$/.test(path)) {
      return { json: async () => ({ name: JSON.parse(JSON.stringify(body)).name }) };
    }
    if (method === 'GET' && path === '/api/instance-info') {
      const info = Object.prototype.hasOwnProperty.call(opts, 'instanceInfo')
        ? opts.instanceInfo
        : { device_id: LOCAL_DEVICE_ID, version: '0.0.0-test' };
      return { json: async () => info };
    }
    if (method === 'PATCH' && path === '/api/settings') {
      patchCalls.push(JSON.parse(JSON.stringify(body)));
      const outcome = opts.patchOutcome(patchCalls.length);
      if (outcome === 'conflict') {
        // Somebody else won the race: server truth moves on, exactly as it
        // would under a concurrent create.
        settingsUpdatedAt += 1;
        serverSettings = Object.assign({}, serverSettings, { settings_updated_at: settingsUpdatedAt });
        throw httpError(409, { detail: 'settings changed since read' });
      }
      if (outcome === 'backstop') {
        throw httpError(409, { backstop: true, detail: 'destructive write rejected' });
      }
      settingsUpdatedAt += 1;
      serverSettings = Object.assign({}, serverSettings, {
        views: JSON.parse(JSON.stringify(body.views || serverSettings.views)),
        settings_updated_at: settingsUpdatedAt,
      });
      return { json: async () => JSON.parse(JSON.stringify(serverSettings)) };
    }
    throw httpError(404, { detail: 'unexpected ' + method + ' ' + path });
  };

  ctx.loadServerSettings = async function loadServerSettings() {
    const snapshot = JSON.parse(JSON.stringify(serverSettings));
    app._setServerSettings(snapshot);
    return snapshot;
  };
  ctx.showToast = function showToast(msg) { toasts.push(String(msg)); };
  ctx.pollSessions = async function pollSessions() {};
  ctx._rerenderViewDependentUI = function _rerenderViewDependentUI() {};
  // Keep the backoff out of wall-clock time. Harmless on a build that has no
  // such hook -- it just adds an unused global.
  ctx._settingsCasBackoffMs = function _settingsCasBackoffMs() { return 0; };

  app._setServerSettings(JSON.parse(JSON.stringify(serverSettings)));
  app._setActiveView(opts.activeView);

  return {
    app,
    ctx,
    patchCalls,
    toasts,
    warnings,
    get serverViews() { return JSON.parse(JSON.stringify(serverSettings.views)); },
  };
}

/** Poll until `predicate()` holds or the deadline passes. The auto-add is
 * deliberately fire-and-forget (it must never delay the create UX), so a
 * test cannot await it directly -- it waits for the observable effect. */
async function settle(predicate, { timeoutMs = 2000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((r) => setTimeout(r, 5));
  }
  return predicate();
}

/** The last PATCH body's session list for `viewName`, or null. */
function pinnedSessions(patchBody, viewName) {
  const list = (patchBody && patchBody.views) || [];
  for (const v of list) if (v.name === viewName) return v.sessions;
  return null;
}

const BASE_VIEWS = [
  { name: 'work', sessions: ['existing'] },
  { name: 'other', sessions: [] },
];

// ─── 1. Two consecutive CAS conflicts must still land the pin ───────────
// This is the routine case for a user who creates sessions in parallel: two
// concurrent creates both PATCH /api/settings, and the loser sees a stale
// baseline TWICE in a row. Before this fix, patchSettingsGuarded retried
// exactly once and then gave up, silently losing the view assignment.

test('auto-add survives two consecutive CAS conflicts and still lands the pin', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: (n) => (n <= 2 ? 'conflict' : 'ok'),
  });

  await h.app.createNewSession('s1', '', undefined);
  await settle(() => h.patchCalls.length >= 3);

  assert.ok(
    h.patchCalls.length >= 3,
    'two consecutive CAS conflicts must not end the attempt -- expected a third ' +
      `PATCH, saw ${h.patchCalls.length}`,
  );
  const landed = pinnedSessions(h.patchCalls[h.patchCalls.length - 1], 'work');
  assert.ok(landed, 'the final PATCH must still carry the "work" view');
  assert.ok(
    landed.includes(LOCAL_DEVICE_ID + ':s1'),
    `the pin must survive the conflicts -- got ${JSON.stringify(landed)}`,
  );
  assert.deepEqual(
    h.toasts.filter((t) => /not added|failed to add/i.test(t)),
    [],
    'a create that eventually succeeded must not warn the user about the view',
  );
});

// ─── 2. A pin that truly cannot land must be surfaced, not swallowed ────

test('auto-add failure is surfaced to the user, not just console.warn', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: () => 'conflict', // never resolves
  });

  await h.app.createNewSession('s1', '', undefined);
  await settle(() => h.toasts.some((t) => /not added|failed to add/i.test(t)));

  const notice = h.toasts.find((t) => /not added|failed to add/i.test(t));
  assert.ok(
    notice,
    'the user must be told the session was created but not added to the view; ' +
      `toasts were ${JSON.stringify(h.toasts)}`,
  );
  assert.ok(notice.includes('s1'), 'the notice must name the session: ' + notice);
  assert.ok(notice.includes('work'), 'the notice must name the view: ' + notice);
});

test('a destructive-write backstop rejection is surfaced too', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: () => 'backstop',
  });

  await h.app.createNewSession('s1', '', undefined);
  await settle(() => h.toasts.some((t) => /not added|failed to add/i.test(t)));

  assert.ok(
    h.toasts.some((t) => /not added|failed to add/i.test(t)),
    'a backstop rejection loses the view assignment just as visibly as a CAS ' +
      `conflict does, and must say so; toasts were ${JSON.stringify(h.toasts)}`,
  );
  assert.equal(h.patchCalls.length, 1, 'a backstop rejection must never be retried');
});

// ─── 3. A peer with no device_id must not produce an index-keyed pin ────
// _createCommandSelect falls back to the array index (`String(i)`) when a
// peer reports no device_id. A pin like "0:s1" can never match the server's
// "<device_id>:<name>" key -- writing it is strictly worse than writing
// nothing, because it silently pollutes the view definition forever.

test('a peer with no device_id produces no index-keyed pin, and the user is told', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: () => 'ok',
  });

  await h.app.createNewSession('s1', '0', undefined);
  await settle(() => h.toasts.some((t) => /not added|failed to add/i.test(t)));

  for (const body of h.patchCalls) {
    const list = pinnedSessions(body, 'work') || [];
    for (const key of list) {
      assert.ok(
        !/^\d+:/.test(key),
        `no pin may be keyed on an array index -- found ${JSON.stringify(key)}`,
      );
    }
  }
  assert.ok(
    h.toasts.some((t) => /not added|failed to add/i.test(t)),
    'refusing to write the pin must still tell the user the session was not ' +
      `auto-tagged; toasts were ${JSON.stringify(h.toasts)}`,
  );
});

test('the refusal notice is not overwritten by the create toast in the same tick', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: () => 'ok',
  });

  await h.app.createNewSession('s1', '0', undefined);
  await settle(() => h.toasts.some((t) => /not added|failed to add/i.test(t)));

  // The toast element is a single slot -- the LAST message written is the
  // one a user actually sees. The refusal path has no await before its
  // notice, so if the auto-add ran before the 'Creating session…' toast the
  // notice would be clobbered in the same tick and the user would be told
  // nothing at all.
  const creatingIdx = h.toasts.findIndex((t) => /Creating session/i.test(t));
  const noticeIdx = h.toasts.findIndex((t) => /not added|failed to add/i.test(t));
  assert.ok(creatingIdx !== -1, 'the create toast must still be shown');
  assert.ok(
    noticeIdx > creatingIdx,
    'the "not added" notice must come AFTER the create toast, not before it; ' +
      `toasts were ${JSON.stringify(h.toasts)}`,
  );
});

test('a peer WITH a device_id still gets its pin written', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: () => 'ok',
  });

  await h.app.createNewSession('s1', 'peer-uuid-abc', undefined);
  await settle(() => h.patchCalls.length >= 1);

  const landed = pinnedSessions(h.patchCalls[h.patchCalls.length - 1], 'work');
  assert.ok(
    landed && landed.includes('peer-uuid-abc:s1'),
    `a real peer device_id must still pin normally -- got ${JSON.stringify(landed)}`,
  );
});

// ─── 4. The _localDeviceId startup race ─────────────────────────────────
// _localDeviceId is filled in fire-and-forget from GET /api/instance-info at
// load time. A create submitted before that lands used to write a bare-name
// pin, which only survives because the server later normalizes it.

test('a create before /api/instance-info lands still writes a device-qualified pin', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: () => 'ok',
  });
  // NOTE: nothing here sets _localDeviceId -- app.js was loaded but its
  // load-time instance-info fetch never ran, so this is exactly the race.

  await h.app.createNewSession('s1', '', undefined);
  await settle(() => h.patchCalls.length >= 1);

  const landed = pinnedSessions(h.patchCalls[h.patchCalls.length - 1], 'work');
  assert.ok(landed, 'the PATCH must carry the "work" view');
  assert.ok(
    landed.includes(LOCAL_DEVICE_ID + ':s1'),
    'the pin must be device-qualified even when _localDeviceId has not loaded ' +
      `yet -- got ${JSON.stringify(landed)}`,
  );
});

test('an unreachable /api/instance-info degrades to the bare-name pin, not a crash', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: () => 'ok',
    instanceInfo: null, // server answers, but with no device_id
  });

  await h.app.createNewSession('s1', '', undefined);
  await settle(() => h.patchCalls.length >= 1);

  const landed = pinnedSessions(h.patchCalls[h.patchCalls.length - 1], 'work');
  assert.ok(
    landed && landed.includes('s1'),
    'with no device id available at all, the bare name is the correct fallback ' +
      `(the server normalizes it) -- got ${JSON.stringify(landed)}`,
  );
});

// ─── 5. The retry policy itself ─────────────────────────────────────────

test('buildSessionKey is the single shared key constructor', () => {
  const h = loadApp({ views: BASE_VIEWS, activeView: 'work', patchOutcome: () => 'ok' });
  assert.equal(typeof h.app.buildSessionKey, 'function', 'buildSessionKey must be exported');
  assert.equal(h.app.buildSessionKey('dev-1', 's1'), 'dev-1:s1');
  assert.equal(h.app.buildSessionKey('', 's1'), 's1');
  assert.equal(h.app.buildSessionKey(null, 's1'), 's1');
});

test('CAS backoff grows and stays bounded', () => {
  const h = loadApp({ views: BASE_VIEWS, activeView: 'work', patchOutcome: () => 'ok' });
  const backoff = h.app._settingsCasBackoffMs;
  assert.equal(typeof backoff, 'function', '_settingsCasBackoffMs must be exported');
  // Jittered, so compare floors across a spread of samples rather than
  // single values -- an assertion on one sample would be flaky by design.
  const floorAt = (n) => Math.min(...Array.from({ length: 50 }, () => backoff(n)));
  assert.ok(floorAt(0) >= 0, 'backoff must never be negative');
  assert.ok(floorAt(1) > floorAt(0), 'backoff must grow with the attempt number');
  assert.ok(floorAt(3) > floorAt(1), 'backoff must keep growing');
  for (let n = 0; n < 8; n++) {
    assert.ok(backoff(n) <= 2000, `backoff must stay bounded, got ${backoff(n)} at attempt ${n}`);
  }
});

test('the CAS retry budget is bounded -- a permanently conflicting server terminates', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: () => 'conflict',
  });

  await h.app.createNewSession('s1', '', undefined);
  await settle(() => h.toasts.some((t) => /not added|failed to add/i.test(t)));
  const settledCount = h.patchCalls.length;
  await new Promise((r) => setTimeout(r, 120));

  assert.equal(h.patchCalls.length, settledCount, 'the retry loop must stop, not spin');
  assert.ok(settledCount >= 3, `expected more than the old single retry, saw ${settledCount}`);
  assert.ok(settledCount <= 8, `the retry budget must be bounded, saw ${settledCount}`);
});

// ─── 6. Unrelated views must never be collateral damage ─────────────────

test('auto-add leaves other views untouched across retries', async () => {
  const h = loadApp({
    views: BASE_VIEWS,
    activeView: 'work',
    patchOutcome: (n) => (n <= 2 ? 'conflict' : 'ok'),
  });

  await h.app.createNewSession('s1', '', undefined);
  await settle(() => h.patchCalls.length >= 3);

  assert.equal(h.patchCalls.length, 3, 'expected two conflicts then a success');
  const other = pinnedSessions(h.patchCalls[h.patchCalls.length - 1], 'other');
  assert.deepEqual(other, [], 'the "other" view must be sent back unchanged');
  const work = pinnedSessions(h.patchCalls[h.patchCalls.length - 1], 'work');
  assert.ok(work.includes('existing'), 'pre-existing members must be preserved');
  assert.deepEqual(
    work, ['existing', LOCAL_DEVICE_ID + ':s1'],
    'the retry must APPEND to server truth, never rebuild the view from a ' +
      'stale snapshot -- rebuilding is the settings-clobber incident',
  );
});

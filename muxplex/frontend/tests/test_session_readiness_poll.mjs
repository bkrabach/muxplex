// createNewSession()'s readiness poll (muxplex-9zp).
//
// The bug this file pins down: the poll built ONE expected key
// (`deviceId ? deviceId + ':' + name : name`) but pollSessions() switches
// endpoints on the multi_device_enabled flag, and the two endpoints describe
// the SAME local session differently -- GET /api/sessions pops sessionKey
// (bare `name`), GET /api/federation/sessions keeps it
// ('<localDeviceId>:<name>'). With multi-device on, a local create therefore
// searched for `mysession` in a list that only ever contained
// `<uuid>:mysession`, never matched, and fired "taking longer than expected"
// on every single create -- for sessions that had come up perfectly fine.
//
// These are behavioural tests: they run the real createNewSession() against a
// stubbed fetch/timer environment (the pattern test_command_pairs.mjs
// established) and drive the poll interval by hand.

// ─── Browser global stubs -- must be set before importing app.js ────────────

let _localStorageStore = {};
globalThis.localStorage = {
  getItem: (key) => (Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null),
  setItem: (key, value) => { _localStorageStore[key] = String(value); },
  removeItem: (key) => { delete _localStorageStore[key]; },
};

// Every showToast() call lands here: showToast writes the message to
// #toast's textContent, so a recording setter captures the whole sequence.
const _toasts = [];
const _elements = {
  toast: {
    _text: '',
    get textContent() { return this._text; },
    set textContent(v) { this._text = v; _toasts.push(v); },
    classList: { add: () => {}, remove: () => {}, contains: () => false },
  },
};

globalThis.document = {
  // No 'session-grid' entry: the loading-placeholder tile is another lane's
  // concern, and leaving it unstubbed keeps this file focused on the poll.
  getElementById: (id) => _elements[id] || null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => ({
    style: {},
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    appendChild: () => {},
    addEventListener: () => {},
  }),
  addEventListener: () => {},
  removeEventListener: () => {},
  activeElement: null,
};

globalThis.window = { addEventListener: () => {}, location: { href: '' }, innerWidth: 1024 };
globalThis.Notification = { permission: 'default', requestPermission: async () => 'default' };
Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'test-agent' },
  writable: true,
  configurable: true,
});
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });

import { createRequire } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const app = require(join(__dirname, '..', 'app.js'));

// On a match the poll calls the REAL openSession(), which walks a lot of DOM
// this harness does not stub. It is fired without await, so a throw surfaces
// as an unhandled rejection that would fail the run for reasons unrelated to
// the poll. Swallow those specifically; every assertion below reads the toast
// sequence, which is written before openSession() is ever called.
process.on('unhandledRejection', () => {});

/**
 * Run createNewSession() with stubbed timers and fetch, then drive the poll
 * interval(s) by hand.
 *
 * @param {object} opts
 *   name          session name to create
 *   remoteId      '' for a local create, a peer device id for a remote one
 *   multiDevice   value of _serverSettings.multi_device_enabled
 *   sessionsAt    (tick) => the array GET /api/(federation/)sessions returns
 *   ticks         how many interval firings to drive
 */
async function runCreate(opts) {
  _toasts.length = 0;
  const timers = [];
  const pollEndpoints = [];
  const errors = [];
  let tick = 0;

  const orig = {
    fetch: globalThis.fetch,
    setInterval: globalThis.setInterval,
    clearInterval: globalThis.clearInterval,
    setTimeout: globalThis.setTimeout,
  };

  globalThis.setInterval = (fn, ms) => {
    const t = { token: Symbol('timer'), fn, ms, cleared: false };
    timers.push(t);
    return t.token;
  };
  globalThis.clearInterval = (token) => {
    const t = timers.find((x) => x.token === token);
    if (t) t.cleared = true;
  };
  // showToast() schedules its own auto-hide; a real timer would just keep the
  // process alive after the assertions are done.
  globalThis.setTimeout = () => Symbol('timeout');
  globalThis.fetch = async (url, o) => {
    const method = (o && o.method) || 'GET';
    if (method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ name: opts.name }) };
    }
    pollEndpoints.push(url);
    return { ok: true, status: 200, json: async () => opts.sessionsAt(tick) };
  };

  try {
    app._setServerSettings({
      multi_device_enabled: !!opts.multiDevice,
      auto_open_created: true,
      views: [],
    });
    // 'all' skips the auto-add-to-view branch -- a different lane's region.
    app._setActiveView('all');
    app._setCurrentSessions([]);

    await app.createNewSession(opts.name, opts.remoteId || '', '');

    for (let i = 0; i < (opts.ticks || 1); i++) {
      tick = i + 1;
      // Recomputed each round so a watcher registered during this round is
      // driven from the NEXT round, exactly as a real timer would be.
      const live = timers.filter((t) => !t.cleared);
      for (const t of live) {
        try {
          await t.fn();
        } catch (err) {
          errors.push(err);
        }
      }
    }
  } finally {
    globalThis.fetch = orig.fetch;
    globalThis.setInterval = orig.setInterval;
    globalThis.clearInterval = orig.clearInterval;
    globalThis.setTimeout = orig.setTimeout;
  }

  return { toasts: _toasts.slice(), timers, pollEndpoints, errors };
}

const has = (toasts, needle) => toasts.some((t) => t.includes(needle));

// A local session as GET /api/federation/sessions describes it (main.py:5045):
// sessionKey carries the local device id, remoteId is null.
const federationLocal = (name) => ({
  name,
  deviceId: 'local-device-uuid',
  deviceName: 'This box',
  remoteId: null,
  sessionKey: 'local-device-uuid:' + name,
  bell: { unseen_count: 0 },
  snapshot: '',
});

// A local session as GET /api/sessions describes it (main.py:1529-1531 pops
// sessionKey): bare name, no device tags at all.
const plainLocal = (name) => ({ name, bell: { unseen_count: 0 }, snapshot: '' });

// A PEER's session, same name, as federation tags it.
const federationRemote = (name, peerId) => ({
  name,
  deviceId: peerId,
  deviceName: 'Other box',
  remoteId: peerId,
  sessionKey: peerId + ':' + name,
  bell: { unseen_count: 0 },
  snapshot: '',
});

// ─── The reported bug ───────────────────────────────────────────────────────

test('local create with multi_device_enabled true is found on the first poll', async () => {
  const r = await runCreate({
    name: 'mysession',
    remoteId: '',
    multiDevice: true,
    sessionsAt: () => [federationLocal('mysession')],
    ticks: 1,
  });

  // Premise check: multi-device on really does change the endpoint, so the
  // list really is in the '<deviceId>:<name>' key-space.
  assert.equal(r.pollEndpoints[0], '/api/federation/sessions',
    'multi_device_enabled must route the readiness poll at the federation endpoint');
  assert.ok(has(r.toasts, "Session 'mysession' ready"),
    'a local session that is already present must be recognised on the first poll');
  assert.ok(!has(r.toasts, 'taking longer') && !has(r.toasts, 'still starting'),
    'no give-up toast may fire for a session that came up fine');
  assert.ok(r.timers[0].cleared, 'the poll interval must be cleared once the session is found');
});

test('local create with multi_device_enabled true never reaches the give-up branch', async () => {
  // Same as above but driven well past the 15-attempt window: a match must
  // stop the loop, not merely delay the false toast.
  const r = await runCreate({
    name: 'mysession',
    remoteId: '',
    multiDevice: true,
    sessionsAt: () => [federationLocal('mysession')],
    ticks: 20,
  });

  assert.equal(r.toasts.filter((t) => t.includes('ready')).length, 1,
    'the ready toast must fire exactly once');
  assert.ok(!has(r.toasts, 'taking longer') && !has(r.toasts, 'still starting')
    && !has(r.toasts, 'may have failed'),
    'no failure or slow-start toast may follow a successful match');
});

// ─── Unchanged behaviour ────────────────────────────────────────────────────

test('local create with multi_device_enabled false is unchanged (bare-name key-space)', async () => {
  const r = await runCreate({
    name: 'mysession',
    remoteId: '',
    multiDevice: false,
    sessionsAt: () => [plainLocal('mysession')],
    ticks: 1,
  });

  assert.equal(r.pollEndpoints[0], '/api/sessions',
    'multi-device off must keep polling the plain sessions endpoint');
  assert.ok(has(r.toasts, "Session 'mysession' ready"),
    'a bare-name local session must still match');
  assert.ok(!has(r.toasts, 'still starting'), 'no give-up toast on the happy path');
});

test('remote create still matches on the peer device key', async () => {
  const r = await runCreate({
    name: 'mysession',
    remoteId: 'peer-1',
    multiDevice: true,
    sessionsAt: () => [federationLocal('other'), federationRemote('mysession', 'peer-1')],
    ticks: 1,
  });

  assert.ok(has(r.toasts, "Session 'mysession' ready"),
    "a remote create must match the peer's '<deviceId>:<name>' entry");
});

test("local create does not match a peer's same-named session", async () => {
  // The dangerous false positive: loosening the match must not make a create
  // on THIS box auto-open a session of the same name on another device.
  const r = await runCreate({
    name: 'mysession',
    remoteId: '',
    multiDevice: true,
    sessionsAt: () => [federationRemote('mysession', 'peer-1')],
    ticks: 15,
  });

  assert.ok(!has(r.toasts, "Session 'mysession' ready"),
    "a peer's session must never be mistaken for the local one just created");
  assert.ok(has(r.toasts, 'still starting'),
    'with nothing local to find, the poll must reach its give-up branch');
});

// ─── Give-up semantics ──────────────────────────────────────────────────────

test('the give-up message says still-starting, not failed, and points at the All list', async () => {
  const r = await runCreate({
    name: 'slowone',
    remoteId: '',
    multiDevice: true,
    sessionsAt: () => [],
    ticks: 15,
  });

  const giveUp = r.toasts.find((t) => t.includes('slowone') && !t.startsWith('Creating'));
  assert.ok(giveUp, 'the poll must say something when its window expires');
  assert.ok(giveUp.includes('still starting'),
    'the POST already succeeded, so the message must not imply the create failed');
  assert.ok(giveUp.includes('All list'),
    'the message must tell the user where the session will show up');
  assert.ok(!giveUp.includes('failed'),
    'a session that may still be starting must not be reported as failed');
});

test('a late arrival is recovered instead of leaving the create hanging', async () => {
  // Absent for the whole 30s window, present afterwards -- the tmux_kit
  // 30s-budget-plus-cached-list case the give-up branch cannot rule out.
  const r = await runCreate({
    name: 'slowone',
    remoteId: '',
    multiDevice: true,
    sessionsAt: (tick) => (tick > 15 ? [federationLocal('slowone')] : []),
    ticks: 17,
  });

  assert.equal(r.timers.length, 2,
    'giving up must hand off to a second, slower watcher rather than stop looking');
  assert.ok(r.timers[1].ms > r.timers[0].ms,
    'the late-arrival watcher must poll at a slower cadence than the initial loop');
  assert.ok(has(r.toasts, 'ready now'),
    'the UI must recover when the session turns up after the window closed');
  assert.ok(!has(r.toasts, "Session 'slowone' ready"),
    'a late arrival must not silently yank the user into fullscreen minutes later');
  assert.ok(r.timers[1].cleared, 'the late watcher must clear itself once the session arrives');
});

test('a session that never arrives is finally reported as a probable failure', async () => {
  const r = await runCreate({
    name: 'ghost',
    remoteId: '',
    multiDevice: true,
    sessionsAt: () => [],
    ticks: 15 + 24 + 1,
  });

  assert.ok(has(r.toasts, 'may have failed'),
    'after both windows expire the user must be told it probably did not work');
  assert.ok(r.timers.every((t) => t.cleared),
    'no watcher may be left running once the outcome is known');
});

// ─── Source-level guard ─────────────────────────────────────────────────────

test('the poll matches in the key-space the active endpoint returns', () => {
  // The behavioural tests above exercise the fallback branch, because
  // _localDeviceId is only ever populated by init()'s /api/instance-info
  // fetch and is unreachable from this harness. This assertion covers the
  // primary path: the key is built the same way the view pin at the top of
  // createNewSession builds it, so the two halves cannot disagree again.
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function createNewSession(');
  assert.ok(start !== -1, 'createNewSession must exist');
  const body = source.slice(start, source.indexOf('\nfunction killSession', start));

  assert.ok(body.includes("_localDeviceId + ':' + sessionName"),
    'the readiness poll must know the federation key-space uses the local device id');
  assert.ok(!/var expectedKey = deviceId \? \(deviceId \+ ':' \+ sessionName\) : sessionName;/.test(body),
    'the single-key-space expectedKey is the bug -- it must be gone');
});

// ─── Timer teardown (muxplex-2pv) ────────────────────────────────────────────
//
// A poll tick that throws is a leak, not just a lost tick: setInterval's
// callback here is async, so the throw rejects a promise nobody awaits (it is
// invisible) AND it kills the tick before its own clearInterval, leaving a
// timer rescheduling itself every 2s for the life of the page. The tests below
// therefore assert on the TIMERS, not only on the toast sequence.

/** Run fn with console.error silenced -- the backstop path reports there. */
async function quietly(fn) {
  const origError = console.error;
  console.error = () => {};
  try {
    return await fn();
  } finally {
    console.error = origError;
  }
}

test('a non-array session list does not throw out of the poll tick', async () => {
  // pollSessions() assigns `await res.json()` straight to _currentSessions with
  // no shape check, so a 200 carrying an object rather than a list lands there
  // intact -- and `_currentSessions && _currentSessions.find(...)` accepted it
  // as truthy, then called a method it does not have.
  const r = await quietly(() => runCreate({
    name: 'objsession',
    remoteId: '',
    multiDevice: true,
    sessionsAt: () => ({ detail: 'not a list' }),
    ticks: 15,
  }));

  assert.deepEqual(r.errors, [],
    'a non-array session list must not throw out of the readiness poll');
  assert.ok(has(r.toasts, 'still starting'),
    'a non-array list means "not found", so the poll must reach its ordinary give-up branch');
  assert.ok(r.timers[0].cleared,
    'the poll interval must still be cleared on the give-up path');
});

test('neither timer survives a session list that is never an array', async () => {
  const r = await quietly(() => runCreate({
    name: 'objsession',
    remoteId: '',
    multiDevice: true,
    sessionsAt: () => ({}),
    ticks: 15 + 24 + 1,
  }));

  assert.equal(r.timers.length, 2,
    'giving up must still hand off to the late-arrival watcher');
  assert.ok(r.timers.every((t) => t.cleared),
    'no timer may be left rescheduling itself once both windows have closed');
  assert.ok(has(r.toasts, 'may have failed'),
    'both windows expiring must still tell the user what happened');
});

test('a tick that throws for any other reason still stops its own timer', async () => {
  // The type guard fixes the ONE known throw; this pins the structural half --
  // teardown must not depend on the tick body reaching a clearInterval. A
  // session entry whose `name` getter throws blows up isCreatedSession itself,
  // from inside .find on a perfectly well-formed array.
  const poisoned = { get name() { throw new Error('poisoned session entry'); } };

  const r = await quietly(() => runCreate({
    name: 'boom',
    remoteId: '',
    multiDevice: true,
    sessionsAt: () => [poisoned],
    ticks: 5,
  }));

  assert.deepEqual(r.errors, [],
    'the tick must contain its own failure rather than reject into the void');
  assert.ok(r.timers[0].cleared,
    'a tick that throws must still stop its timer');
  assert.ok(has(r.toasts, 'status is unknown'),
    'a poll that cannot finish must say so rather than strand a "Creating..." tile');
});

test('the readiness poll no longer reads .find off an unchecked _currentSessions', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function createNewSession(');
  assert.ok(start !== -1, 'createNewSession must exist');
  const body = source.slice(start, source.indexOf('\nfunction killSession', start));

  assert.ok(!body.includes('_currentSessions && _currentSessions.find'),
    'the truthiness-only guard is the bug -- a non-array is truthy and has no .find');
  assert.ok(body.includes('Array.isArray(_currentSessions)'),
    'the poll must check the shape of what it is about to call .find on');
});

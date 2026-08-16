// Tests for the mobile compose bar (app.js's _compose* functions).
//
// The compose bar is a plain UI client of the existing, unmodified
// POST /api/sessions/{name}/input -- there is no new server endpoint and no
// fence change (see AGENTS.md's "Mobile compose bar" note; a superseded
// draft spec proposed a new /compose endpoint gated on caller class, which
// was rejected by security review and never built). These tests exercise
// only the client-side behavior: preference resolution, the toggle, Enter
// vs Ctrl+Enter, text normalization, the enabled/disabled render based on
// settings.input_enabled, error-message mapping for every failure mode of
// a real POST /input response, and that no path silently swallows a
// failure.

// --- localStorage stub -- must be set before importing app.js, and must
// support per-test "throw on get/set" behavior (see the throwing-storage
// test below) without permanently breaking every other test. ---
let _localStorageStore = {};
let _localStorageThrowsOnGet = false;
let _localStorageThrowsOnSet = false;
globalThis.localStorage = {
  getItem(key) {
    if (_localStorageThrowsOnGet) throw new Error('blocked');
    return Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null;
  },
  setItem(key, value) {
    if (_localStorageThrowsOnSet) throw new Error('blocked');
    _localStorageStore[key] = String(value);
  },
  removeItem(key) { delete _localStorageStore[key]; },
};

// --- DOM stub -- a small registry of mutable, stateful elements for every
// id the compose bar touches, plus a catch-all null for everything else
// (matching test_app.mjs's existing pattern). ---
function makeClassList(initial) {
  const classes = new Set(initial || []);
  return {
    add(...cs) { for (const c of cs) classes.add(c); },
    remove(...cs) { for (const c of cs) classes.delete(c); },
    toggle(c, force) {
      if (force === undefined) {
        if (classes.has(c)) { classes.delete(c); return false; }
        classes.add(c); return true;
      }
      if (force) classes.add(c); else classes.delete(c);
      return !!force;
    },
    contains(c) { return classes.has(c); },
    _set: classes,
  };
}

function makeStubElement(initialClasses) {
  const attrs = {};
  const listeners = {};
  return {
    style: {},
    value: '',
    disabled: false,
    textContent: '',
    scrollHeight: 20,
    classList: makeClassList(initialClasses),
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k); },
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    _fire(ev, evObj) { (listeners[ev] || []).forEach((fn) => fn(evObj)); },
    focus() { this._focused = true; },
    _focused: false,
  };
}

const elements = {
  'compose-bar': makeStubElement(['hidden']),
  'compose-notice': makeStubElement(['hidden']),
  'compose-error': makeStubElement(['hidden']),
  'compose-input': makeStubElement([]),
  'compose-send-btn': makeStubElement([]),
  'compose-toggle-btn': makeStubElement([]),
};

globalThis.document = {
  getElementById: (id) => Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => makeStubElement([]),
  addEventListener: () => {},
  removeEventListener: () => {},
};

// --- window stub -- innerWidth mutable per test (isMobile()), plus a spy
// for _refitTerminal (terminal.js's exposed refit hook). ---
let _refitCallCount = 0;
globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
  innerWidth: 1024, // desktop by default
  _refitTerminal: () => { _refitCallCount++; },
};

globalThis.Notification = {
  permission: 'default',
  requestPermission: async () => 'default',
};

Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'test-agent' },
  writable: true,
  configurable: true,
});

// Stubs for functions app.js's top-level code references from other
// (not-required-here) files, mirroring test_app.mjs.
globalThis.renderGrid = () => {};
globalThis.handleBellTransitions = () => {};
globalThis.openSession = () => {};
globalThis.updatePillBell = () => {};

import { createRequire } from 'node:module';
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const require = createRequire(import.meta.url);
const app = require(join(__dirname, '..', 'app.js'));
const appSource = fs.readFileSync(join(__dirname, '..', 'app.js'), 'utf-8');

function resetDom() {
  elements['compose-bar'] = makeStubElement(['hidden']);
  elements['compose-notice'] = makeStubElement(['hidden']);
  elements['compose-error'] = makeStubElement(['hidden']);
  elements['compose-input'] = makeStubElement([]);
  elements['compose-send-btn'] = makeStubElement([]);
  elements['compose-toggle-btn'] = makeStubElement([]);
  _refitCallCount = 0;
}

// --- server-settings persistence capture (v0.49.0) -----------------------
//
// The compose bar's on/off preference is no longer a per-device
// localStorage value. It persists through settings.py's `composeBarOpen`
// via GET/PATCH /api/settings -- the SAME mechanism as
// sidebarOpen/agentPanelOpen. So "did the preference persist?" is asserted
// here against the PATCH body this stub records, not against
// localStorage. The old key survives in exactly one role, as a one-time
// migration SOURCE; that role has its own tests below.
//
// patchServerSetting() is async and deliberately not awaited by
// _composeSetPref() (the UI re-renders immediately off the in-memory
// value). Tests that assert on the wire therefore await a macrotask tick
// via settled(); tests that assert on resolution stay synchronous.
let _patchedSettings = [];

function installSettingsFetchStub() {
  _patchedSettings = [];
  globalThis.fetch = async (_path, opts) => {
    if (opts && opts.method === 'PATCH' && opts.body) {
      try { _patchedSettings.push(JSON.parse(opts.body)); } catch (_) { /* not our patch */ }
    }
    return { ok: true, json: async () => ({}) };
  };
}

/** Let the un-awaited patchServerSetting() promise chain settle. */
function settled() {
  return new Promise((r) => setTimeout(r, 0));
}

/**
 * The most recently PATCHed value for `key`, or the sentinel NEVER if no
 * PATCH ever carried it. A sentinel (not undefined) so "never sent" can
 * never be confused with "sent as undefined".
 */
const NEVER = Symbol('never-patched');
function lastPatched(key) {
  for (let i = _patchedSettings.length - 1; i >= 0; i--) {
    if (Object.prototype.hasOwnProperty.call(_patchedSettings[i], key)) {
      return _patchedSettings[i][key];
    }
  }
  return NEVER;
}

beforeEach(() => {
  resetDom();
  _localStorageStore = {};
  _localStorageThrowsOnGet = false;
  _localStorageThrowsOnSet = false;
  globalThis.window.innerWidth = 1024;
  installSettingsFetchStub();
  app._setViewingSession(null);
  app._setDeviceId('dev-1');
  // Start every test from "server settings loaded, composeBarOpen never
  // explicitly set" -- the real post-load state initComposePref() is
  // written against. (This used to seed the retired string preference
  // 'auto', which after the mechanism change silently persisted a truthy
  // STRING into composeBarOpen and made several assertions below pass or
  // fail for reasons unrelated to what they claimed to test.)
  app._setServerSettings({});
  app._composeClearDraft();
});

// --- Preference resolution (composeBarOpen tri-state) ---
//
// v0.49.0 replaced the retired per-device localStorage tri-state
// ('auto'|'on'|'off', where 'auto' deferred to isMobile()) with a single
// server-persisted composeBarOpen (null|true|false). The behavioural
// change these tests now pin: a never-toggled preference resolves to
// VISIBLE at EVERY width -- not visible-on-mobile-only. The old
// width-dependent assertions are gone because the width dependency is
// gone, not because they were inconvenient.

test('never toggled resolves to visible on desktop width', () => {
  app._setServerSettings({});
  app.initComposePref();
  assert.strictEqual(app._composeEffectiveOn(), true);
});

test('never toggled resolves to visible on mobile width too -- no width dependence', () => {
  globalThis.window.innerWidth = 400;
  app._setServerSettings({});
  app.initComposePref();
  assert.strictEqual(app._composeEffectiveOn(), true);
});

test('an explicit false wins over the on-by-default, at any width', () => {
  app._setServerSettings({ composeBarOpen: false });
  app.initComposePref();
  assert.strictEqual(app._composeEffectiveOn(), false);
  globalThis.window.innerWidth = 400;
  assert.strictEqual(app._composeEffectiveOn(), false);
});

test('an explicit true stays on, at any width', () => {
  app._setServerSettings({ composeBarOpen: true });
  app.initComposePref();
  assert.strictEqual(app._composeEffectiveOn(), true);
  globalThis.window.innerWidth = 400;
  assert.strictEqual(app._composeEffectiveOn(), true);
});

test('initComposePref does not re-persist when the server already holds an explicit value', async () => {
  app._setServerSettings({ composeBarOpen: false });
  app.initComposePref();
  await settled();
  // Nothing to resolve -- an explicit value must not be rewritten, which
  // would also stomp a concurrent change from another device.
  assert.strictEqual(lastPatched('composeBarOpen'), NEVER);
});

test('initComposePref persists the resolved default so it stops being implicit', async () => {
  app._setServerSettings({});
  app.initComposePref();
  await settled();
  assert.strictEqual(lastPatched('composeBarOpen'), true);
});

test('blocked localStorage does not throw and falls back to the on-by-default', () => {
  _localStorageThrowsOnGet = true;
  app._setServerSettings({});
  assert.doesNotThrow(() => app.initComposePref());
  assert.strictEqual(app._composeEffectiveOn(), true);
});

// --- Legacy localStorage migration (one-time, v0.49.0) ---
//
// The retired key survives in exactly one role: a migration SOURCE, read
// once, only when the server has never held an explicit value. This is the
// part with real user-visible risk -- someone who deliberately hid the bar
// must not have it silently reopened by the new on-by-default -- and it
// had no coverage at all before this release.

test('legacy "off" migrates to an explicit false instead of the new on-by-default', () => {
  _localStorageStore['muxplex-compose-bar'] = 'off';
  app._setServerSettings({});
  app.initComposePref();
  // The whole point: the new default is ON, but this user already said off.
  assert.strictEqual(app._composeEffectiveOn(), false);
});

test('legacy "off" migration is persisted to the server, not just held in memory', async () => {
  _localStorageStore['muxplex-compose-bar'] = 'off';
  app._setServerSettings({});
  app.initComposePref();
  await settled();
  assert.strictEqual(lastPatched('composeBarOpen'), false);
});

test('legacy "on" migrates to an explicit true', async () => {
  _localStorageStore['muxplex-compose-bar'] = 'on';
  app._setServerSettings({});
  app.initComposePref();
  await settled();
  assert.strictEqual(app._composeEffectiveOn(), true);
  assert.strictEqual(lastPatched('composeBarOpen'), true);
});

test('legacy "auto" carries no explicit intent and is not migrated', async () => {
  _localStorageStore['muxplex-compose-bar'] = 'auto';
  app._setServerSettings({});
  app.initComposePref();
  await settled();
  // Falls through to the new default rather than being translated into a
  // width guess -- 'auto' never was an explicit user choice.
  assert.strictEqual(app._composeEffectiveOn(), true);
  assert.strictEqual(lastPatched('composeBarOpen'), true);
});

test('an unrecognised legacy value is ignored and falls back to the default', () => {
  _localStorageStore['muxplex-compose-bar'] = 'bogus';
  app._setServerSettings({});
  app.initComposePref();
  assert.strictEqual(app._composeEffectiveOn(), true);
});

test('a legacy value never overrides an explicit server value', async () => {
  // The server is authoritative once it holds a real choice: a stale
  // per-device key from before the migration must not win over it.
  _localStorageStore['muxplex-compose-bar'] = 'on';
  app._setServerSettings({ composeBarOpen: false });
  app.initComposePref();
  await settled();
  assert.strictEqual(app._composeEffectiveOn(), false);
  assert.strictEqual(lastPatched('composeBarOpen'), NEVER);
});

test('migration runs once: a later toggle is not re-overridden by the legacy key', async () => {
  _localStorageStore['muxplex-compose-bar'] = 'off';
  const settings = {};
  app._setServerSettings(settings);
  app.initComposePref();          // migrates -> false
  assert.strictEqual(app._composeEffectiveOn(), false);
  app._composeToggle();           // user turns it back on -> true
  assert.strictEqual(app._composeEffectiveOn(), true);
  app.initComposePref();          // a later init must be a no-op now
  assert.strictEqual(app._composeEffectiveOn(), true);
  await settled();
});

// --- Toggle ---

test('toggle from effective-on persists false', async () => {
  app._composeSetPref(true);
  app._composeToggle();
  assert.strictEqual(app._composeEffectiveOn(), false);
  await settled();
  assert.strictEqual(lastPatched('composeBarOpen'), false);
});

test('toggle from effective-off persists true', async () => {
  app._composeSetPref(false);
  app._composeToggle();
  assert.strictEqual(app._composeEffectiveOn(), true);
  await settled();
  assert.strictEqual(lastPatched('composeBarOpen'), true);
});

test('toggle persists a real boolean, never a legacy string', async () => {
  app._composeSetPref(true);
  app._composeToggle(); // -> false
  app._composeToggle(); // -> true
  await settled();
  const sent = lastPatched('composeBarOpen');
  assert.strictEqual(typeof sent, 'boolean');
  assert.strictEqual(sent, true);
});

test('toggle persists via PATCH /api/settings -- the sidebarOpen mechanism, not localStorage', async () => {
  app._composeSetPref(true);
  app._composeToggle();
  await settled();
  assert.strictEqual(lastPatched('composeBarOpen'), false);
  // The retired key must not be written any more -- it is a migration
  // source only. A second, parallel persistence scheme is exactly what
  // this change removed.
  assert.strictEqual(_localStorageStore['muxplex-compose-bar'], undefined);
});

test('toggle updates aria-pressed and header-btn--active together', () => {
  app._composeSetPref(false);
  app._composeToggle();
  const btn = elements['compose-toggle-btn'];
  assert.strictEqual(btn.getAttribute('aria-pressed'), 'true');
  assert.strictEqual(btn.classList.contains('header-btn--active'), true);
});

// --- Enter vs Ctrl+Enter ---

test('bare Enter does not send and does not preventDefault', () => {
  let sent = false;
  let prevented = false;
  const orig = app._composeSend;
  const e = { key: 'Enter', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, preventDefault: () => { prevented = true; } };
  app._composeKeydown(e);
  assert.strictEqual(prevented, false);
});

test('Ctrl+Enter sends and calls preventDefault', () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hello';
  let prevented = false;
  const e = { key: 'Enter', ctrlKey: true, metaKey: false, shiftKey: false, altKey: false, preventDefault: () => { prevented = true; } };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, session: 's1' }) });
  app._composeKeydown(e);
  assert.strictEqual(prevented, true);
  globalThis.fetch = origFetch;
});

test('Cmd+Enter (metaKey) sends and calls preventDefault', () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hello';
  let prevented = false;
  const e = { key: 'Enter', ctrlKey: false, metaKey: true, shiftKey: false, altKey: false, preventDefault: () => { prevented = true; } };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, session: 's1' }) });
  app._composeKeydown(e);
  assert.strictEqual(prevented, true);
  globalThis.fetch = origFetch;
});

// --- Normalization ---

test('normalizes CRLF and strips trailing newlines', () => {
  assert.strictEqual(app._composeNormalizeText('a\r\nb\n\n'), 'a\nb');
});

test('normalizes bare CR', () => {
  assert.strictEqual(app._composeNormalizeText('a\rb'), 'a\nb');
});

test('whitespace-only draft sends no request and shows an error', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = '   \n  \n';
  let fetchCalled = false;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => { fetchCalled = true; return { ok: true, json: async () => ({}) }; };
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.strictEqual(fetchCalled, false);
  assert.strictEqual(elements['compose-error'].classList.contains('hidden'), false);
});

test('send request body is exactly {text, enter: true}', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'a\r\nb\n\n';
  let capturedBody = null;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (_path, opts) => {
    capturedBody = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ ok: true, session: 's1' }) };
  };
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.deepEqual(capturedBody, { text: 'a\nb', enter: true });
});

test('send hits POST /api/sessions/{name}/input with device_id', async () => {
  app._setViewingSession('my-session');
  elements['compose-input'].value = 'hi';
  let capturedPath = null;
  let capturedMethod = null;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (path, opts) => {
    capturedPath = path;
    capturedMethod = opts.method;
    return { ok: true, json: async () => ({ ok: true, session: 'my-session' }) };
  };
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.strictEqual(capturedMethod, 'POST');
  assert.ok(capturedPath.startsWith('/api/sessions/my-session/input'), capturedPath);
  assert.ok(capturedPath.includes('device_id=dev-1'), capturedPath);
});

// --- Failure surfacing (one row per docs/plans/2026-08-05-mobile-compose-bar-plan.md
// §7.4-equivalent table, adapted for the real /input endpoint's actual response shapes) ---

test('403 input_enabled=false surfaces host-editing guidance', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hi';
  const origFetch = globalThis.fetch;
  // api() constructs { status, body } from a non-ok fetch Response itself
  // (see app.js's api()) -- mock the Response shape, not the thrown Error.
  globalThis.fetch = async () => ({
    ok: false, status: 403, statusText: 'Forbidden',
    json: async () => ({ detail: 'Session input is disabled (settings.input_enabled=false)' }),
  });
  await app._composeSend();
  globalThis.fetch = origFetch;
  const msg = elements['compose-error'].textContent;
  assert.match(msg, /input_enabled/);
  assert.match(msg, /settings\.json/);
  assert.strictEqual(elements['compose-input'].value, 'hi', 'draft must survive a failure');
});

test('403 allowlist mismatch surfaces input_allowed_sessions guidance', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hi';
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false, status: 403, statusText: 'Forbidden',
    json: async () => ({ detail: "Session 's1' does not match any input_allowed_sessions pattern" }),
  });
  await app._composeSend();
  globalThis.fetch = origFetch;
  const msg = elements['compose-error'].textContent;
  assert.match(msg, /input_allowed_sessions/);
  assert.strictEqual(elements['compose-input'].value, 'hi');
});

test('404 surfaces session-no-longer-exists', async () => {
  app._setViewingSession('gone');
  elements['compose-input'].value = 'hi';
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({ detail: 'not found' }) });
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.match(elements['compose-error'].textContent, /gone.*no longer exists/);
});

test('413 surfaces the 8 KiB limit', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hi';
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 413, statusText: 'Payload Too Large', json: async () => ({ detail: 'too large' }) });
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.match(elements['compose-error'].textContent, /8 KiB/);
});

test('400 surfaces server detail verbatim', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hi';
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 400, statusText: 'Bad Request', json: async () => ({ detail: 'No input provided' }) });
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.strictEqual(elements['compose-error'].textContent, 'No input provided');
});

test('500 surfaces a wrapped detail', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hi';
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 500, statusText: 'Server Error', json: async () => ({ detail: 'tmux exited' }) });
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.match(elements['compose-error'].textContent, /couldn.t send.*tmux exited/i);
});

test('network/fetch rejection surfaces "could not reach server"', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hi';
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError('network failure'); };
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.match(elements['compose-error'].textContent, /Couldn.t reach the server/);
  assert.strictEqual(elements['compose-input'].value, 'hi', 'draft must survive a network failure');
});

test('unmapped status falls back to a generic message naming the status', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hi';
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 418, statusText: "I'm a teapot", json: async () => ({}) });
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.match(elements['compose-error'].textContent, /418/);
});

// --- Success path ---

test('success clears textarea, hides error, refits terminal, keeps focus', async () => {
  app._setViewingSession('s1');
  const input = elements['compose-input'];
  input.value = 'hello';
  input.style.height = '80px';
  elements['compose-error'].classList.remove('hidden');
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, session: 's1' }) });
  await app._composeSend();
  globalThis.fetch = origFetch;
  assert.strictEqual(input.value, '');
  assert.strictEqual(input.style.height, '');
  assert.strictEqual(elements['compose-error'].classList.contains('hidden'), true);
  assert.strictEqual(input._focused, true);
  assert.ok(_refitCallCount > 0, 'window._refitTerminal should have been called');
});

// --- In-flight state: exactly one request at a time ---

test('a second send while one is pending is ignored', async () => {
  app._setViewingSession('s1');
  elements['compose-input'].value = 'hi';
  let callCount = 0;
  let resolveFetch;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    callCount++;
    return new Promise((resolve) => { resolveFetch = resolve; });
  };
  const p1 = app._composeSend();
  const p2 = app._composeSend(); // should be a no-op -- already in flight
  resolveFetch({ ok: true, json: async () => ({ ok: true, session: 's1' }) });
  await Promise.all([p1, p2]);
  globalThis.fetch = origFetch;
  assert.strictEqual(callCount, 1);
});

// --- Enabled/disabled render from settings.input_enabled ---

test('input_enabled=true enables the textarea and send button, hides the notice', async () => {
  app._setViewingSession('s1');
  app._composeSetPref(true);
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ input_enabled: true }) });
  await app.loadServerSettings();
  globalThis.fetch = origFetch;
  app._composeRender();
  assert.strictEqual(elements['compose-input'].disabled, false);
  assert.strictEqual(elements['compose-send-btn'].disabled, false);
  assert.strictEqual(elements['compose-notice'].classList.contains('hidden'), true);
  assert.strictEqual(elements['compose-bar'].classList.contains('compose-bar--disabled'), false);
});

test('input_enabled=false (default) disables controls and shows the notice naming both settings keys', async () => {
  app._setViewingSession('s1');
  app._composeSetPref(true);
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ input_enabled: false, input_allowed_sessions: [] }) });
  await app.loadServerSettings();
  globalThis.fetch = origFetch;
  app._composeRender();
  assert.strictEqual(elements['compose-input'].disabled, true);
  assert.strictEqual(elements['compose-send-btn'].disabled, true);
  assert.strictEqual(elements['compose-notice'].classList.contains('hidden'), false);
  assert.strictEqual(elements['compose-bar'].classList.contains('compose-bar--disabled'), true);
});

test('bar itself still renders (not hidden) even when input is disabled -- discoverable, not a dead button', async () => {
  app._setViewingSession('s1');
  app._composeSetPref(true);
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ input_enabled: false }) });
  await app.loadServerSettings();
  globalThis.fetch = origFetch;
  app._composeRender();
  assert.strictEqual(elements['compose-bar'].classList.contains('hidden'), false);
});

// --- Visibility / lifecycle ---

test('bar hides when no session is open', () => {
  app._setViewingSession(null);
  app._composeSetPref(true);
  assert.strictEqual(elements['compose-bar'].classList.contains('hidden'), true);
});

test('bar hides when preference is off even with a session open', () => {
  app._setViewingSession('s1');
  app._composeSetPref(false);
  assert.strictEqual(elements['compose-bar'].classList.contains('hidden'), true);
});

test('bar shows when a session is open and preference is on', () => {
  app._setViewingSession('s1');
  app._composeSetPref(true);
  assert.strictEqual(elements['compose-bar'].classList.contains('hidden'), false);
});

test('_composeOnSessionOpen clears any stale draft from a previous session', () => {
  elements['compose-input'].value = 'leftover draft';
  app._composeOnSessionOpen();
  assert.strictEqual(elements['compose-input'].value, '');
});

test('_composeOnSessionClose hides the bar and clears the draft', () => {
  app._setViewingSession('s1');
  app._composeSetPref(true);
  elements['compose-input'].value = 'draft';
  app._setViewingSession(null); // mirrors closeSession()'s ordering
  app._composeOnSessionClose();
  assert.strictEqual(elements['compose-input'].value, '');
  assert.strictEqual(elements['compose-bar'].classList.contains('hidden'), true);
});

test('refit is called on show, on hide, and on auto-grow', () => {
  app._setViewingSession('s1');
  _refitCallCount = 0;
  app._composeSetPref(true); // show
  assert.ok(_refitCallCount > 0, 'expected a refit on show');

  _refitCallCount = 0;
  app._composeSetPref(false); // hide
  assert.ok(_refitCallCount > 0, 'expected a refit on hide');

  _refitCallCount = 0;
  app._composeAutoGrow(elements['compose-input']);
  assert.ok(_refitCallCount > 0, 'expected a refit on auto-grow');
});

// --- No silent swallow ---

test('_composeSend has no empty catch handler and always renders on failure', () => {
  const startIdx = appSource.indexOf('async function _composeSend()');
  assert.notStrictEqual(startIdx, -1, 'could not locate _composeSend in app.js source');
  // Extract the function body by brace-matching from the first '{' after the signature.
  const braceStart = appSource.indexOf('{', startIdx);
  let depth = 0;
  let endIdx = -1;
  for (let i = braceStart; i < appSource.length; i++) {
    if (appSource[i] === '{') depth++;
    else if (appSource[i] === '}') {
      depth--;
      if (depth === 0) { endIdx = i; break; }
    }
  }
  assert.notStrictEqual(endIdx, -1, 'could not find the end of _composeSend');
  const body = appSource.slice(braceStart, endIdx + 1);

  assert.doesNotMatch(body, /catch\s*\([^)]*\)\s*\{\s*\}/, 'empty catch block found in _composeSend');
  assert.doesNotMatch(body, /\.catch\(function\s*\(\s*\)\s*\{\s*\}\)/, 'empty .catch(function(){}) found in _composeSend');
  assert.match(body, /_composeShowError/, 'the catch branch must render an error, not swallow it');
});

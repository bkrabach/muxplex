// Tests for the one-time first-run welcome dialog (app.js's _firstRun*
// functions).
//
// What this covers is the PURE logic and the storage contract, not the
// pixels: the show/don't-show decision (_firstRunShouldShow), the
// localStorage flag round-trip through _firstRunMaybeShow(), and the one
// invariant that actually matters for "it must never nag again" -- that
// EVERY exit path (all three buttons, plus merely having been shown)
// marks the flag seen.
//
// The dialog itself is deliberately thin: it reuses the existing
// <dialog> + separate-backdrop-div pattern from #settings-dialog, and
// its "Enable typing" button delegates to the SAME
// _enableFederationTypingForFleet() the Multi-Device tab's one-click
// button uses (covered by test_input_settings.mjs) rather than carrying
// a second copy of that policy.

// --- localStorage stub -- must be set before importing app.js, and must
// support per-test "throw on get/set" behavior (blocked-storage tests)
// without permanently breaking every other test. Same shape as
// test_compose.mjs's stub. ---
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

// --- DOM stub: a stateful registry for the ids the welcome dialog
// touches, plus a catch-all null for everything else (test_app.mjs /
// test_compose.mjs pattern). #settings-dialog is deliberately NOT
// registered, so openSettings()'s own showModal() call takes its
// null-guarded path. ---
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
  };
}

function makeStubElement(initialClasses) {
  const listeners = {};
  return {
    style: {},
    value: '',
    disabled: false,
    textContent: '',
    open: false,
    classList: makeClassList(initialClasses),
    setAttribute() {},
    getAttribute() { return null; },
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    _fire(ev, evObj) { (listeners[ev] || []).forEach((fn) => fn(evObj)); },
    showModal() { this.open = true; this._showModalCalls = (this._showModalCalls || 0) + 1; },
    close() { this.open = false; this._closeCalls = (this._closeCalls || 0) + 1; },
    _showModalCalls: 0,
    _closeCalls: 0,
  };
}

let elements = {};
function resetDom() {
  elements = {
    'firstrun-dialog': makeStubElement([]),
    'firstrun-backdrop': makeStubElement(['hidden']),
  };
}
resetDom();

globalThis.document = {
  title: '',
  getElementById: (id) => (Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null),
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => makeStubElement([]),
  addEventListener: () => {},
  removeEventListener: () => {},
};

globalThis.window = {
  addEventListener: () => {},
  location: { href: '', hostname: 'testhost' },
  innerWidth: 1024,
};

Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'test-agent' },
  writable: true,
  configurable: true,
});

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

// --- PATCH-capture fetch stub (same pattern as test_compose.mjs /
// test_input_settings.mjs), so "Enable typing" can be asserted on the
// wire and "Not now" can be asserted to write NOTHING. ---
let _patchedSettings = [];
function installSettingsFetchStub() {
  _patchedSettings = [];
  globalThis.fetch = async (_path, opts) => {
    if (opts && opts.method === 'PATCH' && opts.body) {
      let parsed = {};
      try { parsed = JSON.parse(opts.body); } catch (_) { /* not our patch */ }
      _patchedSettings.push(parsed);
      const responseBody = Object.assign({}, parsed);
      delete responseBody.expected_settings_updated_at;
      return { ok: true, json: async () => responseBody };
    }
    return { ok: true, json: async () => ({}) };
  };
}
const NEVER = Symbol('never-patched');
function lastPatched(key) {
  for (let i = _patchedSettings.length - 1; i >= 0; i--) {
    if (Object.prototype.hasOwnProperty.call(_patchedSettings[i], key)) {
      return _patchedSettings[i][key];
    }
  }
  return NEVER;
}
function settled() {
  return new Promise((r) => setTimeout(r, 0));
}

/** The raw stored flag, read past the stub (never through app.js). */
function storedFlag() {
  const key = app.FIRSTRUN_STORAGE_KEY;
  return Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null;
}

beforeEach(() => {
  resetDom();
  _localStorageStore = {};
  _localStorageThrowsOnGet = false;
  _localStorageThrowsOnSet = false;
  installSettingsFetchStub();
  app._setServerSettings({});
});

// --- Exports + the flag name itself ---

test('app.js exports the first-run welcome functions and its storage key', () => {
  const expected = [
    '_firstRunShouldShow',
    '_firstRunMarkSeen',
    '_firstRunOpen',
    '_firstRunClose',
    '_firstRunMaybeShow',
    '_firstRunEnableTyping',
    '_firstRunOpenFederationSettings',
    '_firstRunDismiss',
  ];
  for (const fn of expected) {
    assert.ok(fn in app, `app.js should export "${fn}"`);
    assert.strictEqual(typeof app[fn], 'function', `"${fn}" should be a function`);
  }
  assert.strictEqual(app.FIRSTRUN_STORAGE_KEY, 'muxplex_firstrun_seen');
});

// --- The pure show/don't-show decision ---

test('_firstRunShouldShow: an absent flag (null) means show', () => {
  assert.strictEqual(app._firstRunShouldShow(null), true);
});

test('_firstRunShouldShow: an undefined flag means show', () => {
  assert.strictEqual(app._firstRunShouldShow(undefined), true);
});

test('_firstRunShouldShow: an empty-string flag means show', () => {
  assert.strictEqual(app._firstRunShouldShow(''), true);
});

test('_firstRunShouldShow: the written flag ("1") means do NOT show', () => {
  assert.strictEqual(app._firstRunShouldShow('1'), false);
});

test('_firstRunShouldShow: ANY recorded value means do NOT show', () => {
  // Deliberately not `=== '1'`: an unrecognised marker (a future
  // timestamp/version) still means "this browser has seen it", and must
  // never resurrect the dialog for someone who already dismissed it.
  assert.strictEqual(app._firstRunShouldShow('seen'), false);
  assert.strictEqual(app._firstRunShouldShow('2026-08-16T00:00:00Z'), false);
  assert.strictEqual(app._firstRunShouldShow('0'), false);
});

// --- _firstRunMaybeShow: the trigger + the flag round-trip ---

test('_firstRunMaybeShow shows the dialog on a browser that has never seen it', () => {
  assert.strictEqual(app._firstRunMaybeShow(), true);
  assert.strictEqual(elements['firstrun-dialog'].open, true, 'dialog should be modal-open');
  assert.strictEqual(elements['firstrun-backdrop'].classList.contains('hidden'), false, 'backdrop should be visible');
});

test('_firstRunMaybeShow records the flag the first time it shows', () => {
  app._firstRunMaybeShow();
  assert.strictEqual(storedFlag(), '1');
});

test('_firstRunMaybeShow does not show again once the flag is present', () => {
  _localStorageStore[app.FIRSTRUN_STORAGE_KEY] = '1';
  assert.strictEqual(app._firstRunMaybeShow(), false);
  assert.strictEqual(elements['firstrun-dialog'].open, false, 'dialog must stay closed');
});

test('_firstRunMaybeShow never nags: a second call in the same browser is a no-op', () => {
  assert.strictEqual(app._firstRunMaybeShow(), true);
  app._firstRunClose();
  resetDom(); // simulate a fresh page load, same browser/localStorage
  assert.strictEqual(app._firstRunMaybeShow(), false);
  assert.strictEqual(elements['firstrun-dialog'].open, false);
});

test('_firstRunOpen marks seen even if the user never presses a button', () => {
  // Closing the tab on the dialog still counts as having seen it --
  // re-showing next load would be exactly the nagging this avoids.
  app._firstRunOpen();
  assert.strictEqual(storedFlag(), '1');
});

// --- Blocked localStorage ---

test('_firstRunMaybeShow with a blocked localStorage read stays silent instead of nagging every load', () => {
  _localStorageThrowsOnGet = true;
  assert.strictEqual(app._firstRunMaybeShow(), false);
  assert.strictEqual(elements['firstrun-dialog'].open, false);
});

test('_firstRunMarkSeen does not throw when localStorage writes are blocked', () => {
  _localStorageThrowsOnSet = true;
  assert.doesNotThrow(() => app._firstRunMarkSeen());
});

test('a blocked localStorage write does not stop the action from proceeding', () => {
  _localStorageThrowsOnSet = true;
  assert.doesNotThrow(() => app._firstRunDismiss());
  assert.strictEqual(elements['firstrun-dialog']._closeCalls > 0 || elements['firstrun-dialog'].open === false, true);
});

// --- All three actions mark the flag seen ---

test('action "Enable typing for this fleet" marks the flag seen', async () => {
  app._setServerSettings({ input_enabled: false, input_allowed_sessions: [] });
  await app._firstRunEnableTyping();
  assert.strictEqual(storedFlag(), '1');
});

test('action "Enable typing for this fleet" actually enables typing (delegates to the shared one-click path)', async () => {
  app._setServerSettings({ input_enabled: false, input_allowed_sessions: [] });
  await app._firstRunEnableTyping();
  assert.strictEqual(lastPatched('input_enabled'), true);
  assert.deepStrictEqual(lastPatched('input_allowed_sessions'), ['*']);
});

test('action "Enable typing for this fleet" closes the dialog', async () => {
  app._firstRunOpen();
  await app._firstRunEnableTyping();
  assert.strictEqual(elements['firstrun-dialog'].open, false);
  assert.strictEqual(elements['firstrun-backdrop'].classList.contains('hidden'), true);
});

test('action "Open federation settings" marks the flag seen', async () => {
  app._firstRunOpenFederationSettings();
  assert.strictEqual(storedFlag(), '1');
  await settled(); // let openSettings()'s own async settings load finish
});

test('action "Open federation settings" closes the welcome dialog', async () => {
  app._firstRunOpen();
  app._firstRunOpenFederationSettings();
  assert.strictEqual(elements['firstrun-dialog'].open, false);
  await settled();
});

test('action "Not now" marks the flag seen', () => {
  app._firstRunDismiss();
  assert.strictEqual(storedFlag(), '1');
});

test('action "Not now" changes nothing on the server', async () => {
  app._firstRunOpen();
  app._firstRunDismiss();
  await settled();
  assert.strictEqual(lastPatched('input_enabled'), NEVER, '"Not now" must never write input_enabled');
  assert.strictEqual(_patchedSettings.length, 0, '"Not now" must send no PATCH at all');
});

test('action "Not now" closes the dialog and hides the backdrop', () => {
  app._firstRunOpen();
  app._firstRunDismiss();
  assert.strictEqual(elements['firstrun-dialog'].open, false);
  assert.strictEqual(elements['firstrun-backdrop'].classList.contains('hidden'), true);
});

test('_firstRunClose is safe to call when the dialog was never opened', () => {
  assert.doesNotThrow(() => app._firstRunClose());
});

// --- Wiring (source-text assertions, mirroring test_app.mjs's own
// structural checks for handlers that can't be exercised headlessly) ---

test('init shows the welcome only AFTER bindStaticEventListeners, so its buttons are live', () => {
  const bindIdx = appSource.indexOf('      bindStaticEventListeners();');
  const showIdx = appSource.indexOf('      _firstRunMaybeShow();');
  assert.ok(bindIdx !== -1, 'init must call bindStaticEventListeners()');
  assert.ok(showIdx !== -1, 'init must call _firstRunMaybeShow()');
  assert.ok(showIdx > bindIdx, '_firstRunMaybeShow() must run after bindStaticEventListeners()');
});

test('every dismissal route (Escape/cancel, backdrop click) goes through _firstRunDismiss', () => {
  const fnStart = appSource.indexOf('function bindStaticEventListeners(');
  assert.ok(fnStart !== -1, 'bindStaticEventListeners must exist');
  const body = appSource.slice(fnStart);
  assert.ok(body.includes("on($('firstrun-dismiss-btn'), 'click', _firstRunDismiss)"), 'Not now button must be wired');
  assert.ok(body.includes("on($('firstrun-backdrop'), 'click', _firstRunDismiss)"), 'backdrop click must be wired');
  assert.ok(body.includes("firstrunDialog.addEventListener('cancel', _firstRunDismiss)"), 'Escape/cancel must be wired');
  assert.ok(body.includes("on($('firstrun-enable-typing-btn'), 'click', _firstRunEnableTyping)"), 'Enable typing must be wired');
  assert.ok(body.includes("on($('firstrun-open-federation-btn'), 'click', _firstRunOpenFederationSettings)"), 'Open federation settings must be wired');
});

test('the welcome dialog markup exists with all three action buttons', () => {
  const html = fs.readFileSync(join(__dirname, '..', 'index.html'), 'utf-8');
  assert.ok(html.includes('id="firstrun-dialog"'), 'index.html must contain #firstrun-dialog');
  assert.ok(html.includes('id="firstrun-backdrop"'), 'index.html must contain #firstrun-backdrop');
  assert.ok(html.includes('id="firstrun-enable-typing-btn"'), 'index.html must contain the enable-typing button');
  assert.ok(html.includes('id="firstrun-open-federation-btn"'), 'index.html must contain the open-settings button');
  assert.ok(html.includes('id="firstrun-dismiss-btn"'), 'index.html must contain the dismiss button');
});

test('the welcome dialog reuses the settings backdrop treatment rather than a parallel one', () => {
  const html = fs.readFileSync(join(__dirname, '..', 'index.html'), 'utf-8');
  const idx = html.indexOf('id="firstrun-backdrop"');
  assert.ok(idx !== -1);
  const line = html.slice(html.lastIndexOf('<', idx), html.indexOf('>', idx) + 1);
  assert.ok(line.includes('settings-backdrop'), '#firstrun-backdrop must reuse .settings-backdrop');
});

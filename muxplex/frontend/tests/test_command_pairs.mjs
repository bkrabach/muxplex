// Browser global stubs -- must be set before importing app.js (mirrors test_app.mjs).
let _localStorageStore = {};
globalThis.localStorage = {
  getItem: (key) => (Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null),
  setItem: (key, value) => { _localStorageStore[key] = String(value); },
  removeItem: (key) => { delete _localStorageStore[key]; },
};

const _elements = {};
function _stubEl() {
  return {
    style: {},
    classList: { add: () => {}, remove: () => {}, contains: () => false },
    innerHTML: '',
    appendChild: () => {},
    addEventListener: () => {},
    parentNode: { insertBefore: () => {}, removeChild: () => {} },
    focus: () => {},
  };
}

globalThis.document = {
  getElementById: (id) => _elements[id] || null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => _stubEl(),
  addEventListener: () => {},
  removeEventListener: () => {},
  activeElement: null,
};

globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
  innerWidth: 1024,
};

globalThis.Notification = { permission: 'default', requestPermission: async () => 'default' };

Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'test-agent' },
  writable: true,
});

globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

import { createRequire } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const app = require(join(__dirname, '..', 'app.js'));

// ---------------------------------------------------------------------------
// _createCommandSelect()
// ---------------------------------------------------------------------------

test('_createCommandSelect returns null with 0 pairs configured', () => {
  app._setSessionCommands(null, []);
  assert.equal(app._createCommandSelect(), null);
});

test('_createCommandSelect returns null with exactly 1 pair configured (the UI-unchanged invariant)', () => {
  app._setSessionCommands([{ id: 'default', label: 'Default', new_session_template: 'tmux new-session -d -s {name}' }], []);
  assert.equal(app._createCommandSelect(), null);
});

test('_createCommandSelect renders a warning row when there are config errors, even at <2 pairs', () => {
  app._setSessionCommands(
    [{ id: 'default', label: 'Default', new_session_template: 'tmux new-session -d -s {name}' }],
    ["session_commands[0]: 'id' must match [a-z0-9][a-z0-9_-]{0,31} (got None)"]
  );

  const created = [];
  const realCreateElement = globalThis.document.createElement;
  globalThis.document.createElement = (tag) => {
    const el = { tagName: tag.toUpperCase(), children: [], appendChild(child) { this.children.push(child); } };
    created.push(el);
    return el;
  };

  const select = app._createCommandSelect();
  globalThis.document.createElement = realCreateElement;

  assert.notEqual(select, null, 'a config error must produce a select even with only 1 valid pair');
  assert.equal(select.children.length, 2); // the 1 real pair + the warning row
  const warnOpt = select.children[1];
  assert.equal(warnOpt.disabled, true);
  assert.ok(warnOpt.textContent.includes('1 pair failed to load'));
  assert.ok(warnOpt.textContent.includes('Settings'));
});

test('_createCommandSelect returns a select with options at 2+ pairs', () => {
  const pairs = [
    { id: 'default', label: 'Default', new_session_template: 'tmux new-session -d -s {name}' },
    { id: 'amplifier', label: 'Amplifier workspace', new_session_template: 'amplifier-workspace {name}' },
  ];
  app._setSessionCommands(pairs, []);

  const created = [];
  const realCreateElement = globalThis.document.createElement;
  globalThis.document.createElement = (tag) => {
    const el = { tagName: tag.toUpperCase(), children: [], appendChild(child) { this.children.push(child); } };
    created.push(el);
    return el;
  };

  const select = app._createCommandSelect();
  globalThis.document.createElement = realCreateElement;

  assert.notEqual(select, null);
  assert.equal(select.children.length, 2);
  assert.equal(select.children[0].value, 'default');
  assert.equal(select.children[1].value, 'amplifier');
  assert.equal(select.children[1].textContent, 'Amplifier workspace');
  assert.equal(select.children[1].title, 'amplifier-workspace {name}');
  assert.equal(select.value, 'default');
});

// ---------------------------------------------------------------------------
// createNewSession(name, remoteId, commandId) body construction
// ---------------------------------------------------------------------------

// createNewSession() schedules a `setInterval` poll loop (pollForSession) once
// its initial POST resolves successfully -- the same "fire and forget" pattern
// already exercised for startPolling/startHeartbeat elsewhere in this suite
// (see tests/test_app.mjs's "guards against double-start" tests). In a real
// browser this loop is bounded (max 30s, self-clears on match or timeout); in
// this stub environment the interval callback re-derives `_currentSessions`
// from whatever `globalThis.fetch` returns and calls `.find()` on it, which
// throws (TypeError) when the stub returns `{}` instead of an array -- and
// since that throw happens before the loop's own `clearInterval()` call, the
// interval is NEVER cleared and reschedules itself forever, leaking a live
// `Timeout` handle that keeps the process alive indefinitely after the test
// file's own assertions finish (confirmed via `process.getActiveResourcesInfo()`
// showing a `Timeout` entry with these tests uninstrumented, and none with the
// stub below in place). Stubbing `globalThis.setInterval` here -- exactly the
// pattern used for startPolling/startHeartbeat -- prevents any real timer from
// ever being scheduled, so there is nothing left to leak.
test('createNewSession omits command_id when unset', async () => {
  let capturedBody = null;
  const origFetch = globalThis.fetch;
  const origSetInterval = globalThis.setInterval;
  globalThis.setInterval = () => Symbol('timer');
  globalThis.fetch = async (url, opts) => {
    capturedBody = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ name: 'x', ok: true, command_id: 'default' }) };
  };
  try {
    await app.createNewSession('x', '', '');
  } catch (_e) {
    // downstream DOM code may throw on stubs; only the fetch body matters here
  } finally {
    globalThis.fetch = origFetch;
    globalThis.setInterval = origSetInterval;
  }
  assert.deepEqual(capturedBody, { name: 'x' });
});

test('createNewSession sends command_id when picked locally (no remote device)', async () => {
  let capturedBody = null;
  const origFetch = globalThis.fetch;
  const origSetInterval = globalThis.setInterval;
  globalThis.setInterval = () => Symbol('timer');
  globalThis.fetch = async (url, opts) => {
    capturedBody = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ name: 'x', ok: true, command_id: 'amplifier' }) };
  };
  try {
    await app.createNewSession('x', '', 'amplifier');
  } catch (_e) {
    // ignore downstream DOM errors
  } finally {
    globalThis.fetch = origFetch;
    globalThis.setInterval = origSetInterval;
  }
  assert.deepEqual(capturedBody, { name: 'x', command_id: 'amplifier' });
});

test('createNewSession omits command_id for a remote create even when a pair is picked', async () => {
  let capturedUrl = null;
  let capturedBody = null;
  const origFetch = globalThis.fetch;
  const origSetInterval = globalThis.setInterval;
  globalThis.setInterval = () => Symbol('timer');
  globalThis.fetch = async (url, opts) => {
    capturedUrl = url;
    capturedBody = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ name: 'x', ok: true }) };
  };
  try {
    await app.createNewSession('x', 'remote-device-1', 'amplifier');
  } catch (_e) {
    // ignore downstream DOM errors
  } finally {
    globalThis.fetch = origFetch;
    globalThis.setInterval = origSetInterval;
  }
  assert.ok(capturedUrl.includes('/api/federation/'));
  assert.deepEqual(capturedBody, { name: 'x' });
});

// ---------------------------------------------------------------------------
// renderCommandPairsSettings() -- always includes "default", no PATCH call
// ---------------------------------------------------------------------------

// _stubEl()-based createElement (from the top of this file) is enough for
// renderCommandPairsSettings(): it builds real rows via _buildCommandPairRow()
// (title/code/buttons), which only needs .appendChild / .classList / .style /
// .addEventListener -- all present on _stubEl(). No need for the bespoke
// createElement stub the old version of these two tests used.

test('renderCommandPairsSettings renders ALL pairs including default, no patch call anywhere in source', () => {
  const fs = require('node:fs');
  const source = fs.readFileSync(join(__dirname, '..', 'app.js'), 'utf8');
  assert.ok(
    !source.includes("patchServerSetting('session_commands'") &&
    !source.includes('patchServerSetting("session_commands"'),
    'app.js source must never call patchServerSetting for session_commands'
  );

  const pairsList = { innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
  const pairsField = { style: {} };
  const composer = { classList: { add: () => {}, remove: () => {} }, innerHTML: '' };
  const errorsList = { innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
  const errorsField = { style: {} };
  _elements['settings-command-pairs'] = pairsList;
  _elements['settings-command-pairs-field'] = pairsField;
  _elements['settings-command-composer'] = composer;
  _elements['settings-command-errors'] = errorsList;
  _elements['settings-command-errors-field'] = errorsField;

  app._setSessionCommands(
    [
      { id: 'default', label: 'Default', new_session_template: 'tmux new-session -d -s {name}', delete_session_template: 'tmux kill-session -t {name}' },
      { id: 'amplifier', label: 'Amplifier', new_session_template: 'amplifier-workspace {name}', delete_session_template: 'amplifier-dev --destroy {name}' },
    ],
    ['session_commands[0]: bad entry']
  );

  app.renderCommandPairsSettings();

  assert.equal(pairsList.children.length, 2); // default AND the extra pair
  assert.notEqual(pairsField.style.display, 'none');
  assert.equal(errorsList.children.length, 1);
  assert.notEqual(errorsField.style.display, 'none');
});

test('renderCommandPairsSettings still shows the pairs field with ONLY the default pair (the discoverability fix)', () => {
  const pairsList = { innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
  const pairsField = { style: {} };
  const composer = { classList: { add: () => {}, remove: () => {} }, innerHTML: '' };
  const errorsList = { innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
  const errorsField = { style: {} };
  _elements['settings-command-pairs'] = pairsList;
  _elements['settings-command-pairs-field'] = pairsField;
  _elements['settings-command-composer'] = composer;
  _elements['settings-command-errors'] = errorsList;
  _elements['settings-command-errors-field'] = errorsField;

  app._setSessionCommands(
    [{ id: 'default', label: 'Default', new_session_template: 'tmux new-session -d -s {name}', delete_session_template: 'tmux kill-session -t {name}' }],
    []
  );
  app.renderCommandPairsSettings();

  // Before this fix, zero non-default pairs meant the field hid entirely --
  // the user with no extras configured saw nothing about the feature at all.
  assert.equal(pairsList.children.length, 1);
  assert.notEqual(pairsField.style.display, 'none');
  assert.equal(errorsField.style.display, 'none');
});

// ---------------------------------------------------------------------------
// Duplicate composer + copy helpers
// ---------------------------------------------------------------------------

test('_commandsAddInvocation builds a shell-quoted muxplex commands add line', () => {
  const line = app._commandsAddInvocation({
    id: 'dev-alpha',
    label: "alpha's box",
    new_session_template: 'tmux new-session -d -s {name} -c /home/b/dev/alpha',
    delete_session_template: 'tmux kill-session -t {name}',
  });
  assert.equal(
    line,
    "muxplex commands add --id 'dev-alpha' --label 'alpha'\\''s box' " +
      "--create 'tmux new-session -d -s {name} -c /home/b/dev/alpha' " +
      "--delete 'tmux kill-session -t {name}'"
  );
});

test('_openCommandPairComposer prefills id with a -copy suffix for a non-default source', () => {
  const composer = {
    innerHTML: '',
    classList: { add: () => {}, remove: () => {} },
    appendChild(_c) {},
  };
  _elements['settings-command-composer'] = composer;

  const inputs = {};
  const realCreateElement = globalThis.document.createElement;
  globalThis.document.createElement = (tag) => {
    const el = {
      tagName: tag.toUpperCase(),
      classList: { add: () => {}, remove: () => {} },
      style: {},
      appendChild() {},
      addEventListener() {},
      setAttribute() {},
      focus() {},
    };
    if (tag === 'input') inputs[Object.keys(inputs).length] = el;
    return el;
  };

  app._setSessionCommands([{ id: 'amplifier', label: 'Amplifier', new_session_template: 'amplifier-workspace {name}', delete_session_template: 'amplifier-dev --destroy {name}' }], []);
  app._openCommandPairComposer({ id: 'amplifier', label: 'Amplifier', new_session_template: 'amplifier-workspace {name}', delete_session_template: 'amplifier-dev --destroy {name}' });
  globalThis.document.createElement = realCreateElement;

  // First input created is the id field (see _openCommandPairComposer order).
  // The label is prefilled as-is (not modified) -- only id needs
  // disambiguating, since 'amplifier' would otherwise collide with itself.
  assert.equal(inputs[0].value, 'amplifier-copy');
  assert.equal(inputs[1].value, 'Amplifier');

  app._closeCommandPairComposer();
});

// ---------------------------------------------------------------------------
// _updateCommandErrorBadges() -- outside-the-dialog signal (§6 item 5)
// ---------------------------------------------------------------------------

test('_updateCommandErrorBadges shows count and hides at zero, on all three badge elements', () => {
  function badgeStub() {
    return { textContent: '', classList: { _hidden: true, add() { this._hidden = true; }, remove() { this._hidden = false; } } };
  }
  const gearBadge = badgeStub();
  const gearBadgeExpanded = badgeStub();
  const tabBadge = badgeStub();
  _elements['settings-error-badge'] = gearBadge;
  _elements['settings-error-badge-expanded'] = gearBadgeExpanded;
  _elements['settings-tab-command-errors-badge'] = tabBadge;

  app._setSessionCommands([{ id: 'default', label: 'Default' }], ['bad entry 1', 'bad entry 2']);
  app._updateCommandErrorBadges();
  assert.equal(gearBadge.textContent, '2');
  assert.equal(gearBadge.classList._hidden, false);
  assert.equal(gearBadgeExpanded.textContent, '2');
  assert.equal(tabBadge.textContent, '2');

  app._setSessionCommands([{ id: 'default', label: 'Default' }], []);
  app._updateCommandErrorBadges();
  assert.equal(gearBadge.classList._hidden, true);
  assert.equal(gearBadgeExpanded.classList._hidden, true);
  assert.equal(tabBadge.classList._hidden, true);
});

test('_shellQuote escapes embedded single quotes for POSIX shells', () => {
  assert.equal(app._shellQuote("it's"), "'it'\\''s'");
  assert.equal(app._shellQuote(''), "''");
});

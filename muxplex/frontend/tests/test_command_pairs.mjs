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

test('createNewSession omits command_id when unset', async () => {
  let capturedBody = null;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    capturedBody = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ name: 'x', ok: true, command_id: 'default' }) };
  };
  try {
    await app.createNewSession('x', '', '');
  } catch (_e) {
    // downstream DOM code may throw on stubs; only the fetch body matters here
  }
  globalThis.fetch = origFetch;
  assert.deepEqual(capturedBody, { name: 'x' });
});

test('createNewSession sends command_id when picked locally (no remote device)', async () => {
  let capturedBody = null;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    capturedBody = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ name: 'x', ok: true, command_id: 'amplifier' }) };
  };
  try {
    await app.createNewSession('x', '', 'amplifier');
  } catch (_e) {
    // ignore downstream DOM errors
  }
  globalThis.fetch = origFetch;
  assert.deepEqual(capturedBody, { name: 'x', command_id: 'amplifier' });
});

test('createNewSession omits command_id for a remote create even when a pair is picked', async () => {
  let capturedUrl = null;
  let capturedBody = null;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    capturedUrl = url;
    capturedBody = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ name: 'x', ok: true }) };
  };
  try {
    await app.createNewSession('x', 'remote-device-1', 'amplifier');
  } catch (_e) {
    // ignore downstream DOM errors
  }
  globalThis.fetch = origFetch;
  assert.ok(capturedUrl.includes('/api/federation/'));
  assert.deepEqual(capturedBody, { name: 'x' });
});

// ---------------------------------------------------------------------------
// renderCommandPairsSettings() -- read-only rendering, no editable control
// ---------------------------------------------------------------------------

test('renderCommandPairsSettings renders non-default pairs read-only, no patch call anywhere in source', () => {
  const fs = require('node:fs');
  const source = fs.readFileSync(join(__dirname, '..', 'app.js'), 'utf8');
  assert.ok(
    !source.includes("patchServerSetting('session_commands'") &&
    !source.includes('patchServerSetting("session_commands"'),
    'app.js source must never call patchServerSetting for session_commands'
  );

  const pairsList = { innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
  const pairsField = { style: {} };
  const errorsList = { innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
  const errorsField = { style: {} };
  _elements['settings-command-pairs'] = pairsList;
  _elements['settings-command-pairs-field'] = pairsField;
  _elements['settings-command-errors'] = errorsList;
  _elements['settings-command-errors-field'] = errorsField;

  const realCreateElement = globalThis.document.createElement;
  globalThis.document.createElement = (tag) => ({
    tagName: tag.toUpperCase(),
    children: [],
    appendChild(child) { this.children.push(child); },
  });

  app._setSessionCommands(
    [
      { id: 'default', label: 'Default', new_session_template: 'tmux new-session -d -s {name}', delete_session_template: 'tmux kill-session -t {name}' },
      { id: 'amplifier', label: 'Amplifier', new_session_template: 'amplifier-workspace {name}', delete_session_template: 'amplifier-dev --destroy {name}' },
    ],
    ['session_commands[0]: bad entry']
  );

  app.renderCommandPairsSettings();
  globalThis.document.createElement = realCreateElement;

  assert.equal(pairsList.children.length, 1); // only the non-default entry
  assert.notEqual(pairsField.style.display, 'none');
  assert.equal(errorsList.children.length, 1);
  assert.notEqual(errorsField.style.display, 'none');
});

test('renderCommandPairsSettings hides the errors field when there are none', () => {
  const pairsList = { innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
  const pairsField = { style: {} };
  const errorsList = { innerHTML: '', children: [], appendChild(c) { this.children.push(c); } };
  const errorsField = { style: {} };
  _elements['settings-command-pairs'] = pairsList;
  _elements['settings-command-pairs-field'] = pairsField;
  _elements['settings-command-errors'] = errorsList;
  _elements['settings-command-errors-field'] = errorsField;

  app._setSessionCommands(
    [{ id: 'default', label: 'Default', new_session_template: 'tmux new-session -d -s {name}', delete_session_template: 'tmux kill-session -t {name}' }],
    []
  );
  app.renderCommandPairsSettings();

  assert.equal(pairsField.style.display, 'none');
  assert.equal(errorsField.style.display, 'none');
});

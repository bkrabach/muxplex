// Tests for the follow-up queue's client-side behavior (app.js's
// _followups* functions and the compose bar's queue button/shortcut).
// See FOLLOWUP_QUEUE_SPEC.md §9 and §10.3.

let _localStorageStore = {};
globalThis.localStorage = {
  getItem(key) { return Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null; },
  setItem(key, value) { _localStorageStore[key] = String(value); },
  removeItem(key) { delete _localStorageStore[key]; },
};

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
  const attrs = {};
  const listeners = {};
  return {
    style: {},
    value: '',
    disabled: false,
    textContent: '',
    innerHTML: '',
    scrollHeight: 20,
    dataset: {},
    classList: makeClassList(initialClasses),
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; },
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    _fire(ev, evObj) { (listeners[ev] || []).forEach((fn) => fn(evObj)); },
    querySelectorAll: () => [],
    closest: () => null,
    focus() {},
  };
}

const elements = {
  'compose-bar': makeStubElement(['hidden']),
  'compose-notice': makeStubElement(['hidden']),
  'compose-error': makeStubElement(['hidden']),
  'compose-input': makeStubElement([]),
  'compose-send-btn': makeStubElement([]),
  'compose-queue-btn': makeStubElement([]),
  'compose-toggle-btn': makeStubElement([]),
  'followups-panel': makeStubElement(['hidden']),
  'followups-header': makeStubElement([]),
  'followups-halt-banner': makeStubElement(['hidden']),
  'followups-halt-text': makeStubElement([]),
  'followups-list': makeStubElement([]),
  'followups-resume-btn': makeStubElement([]),
  'followups-remove-halted-btn': makeStubElement([]),
  'followups-clear-btn': makeStubElement([]),
};

globalThis.document = {
  getElementById: (id) => Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => makeStubElement([]),
  addEventListener: () => {},
  removeEventListener: () => {},
};

globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
  innerWidth: 1024,
  _refitTerminal: () => {},
  prompt: () => null,
};

globalThis.Notification = { permission: 'default', requestPermission: async () => 'default' };
Object.defineProperty(globalThis, 'navigator', { value: { userAgent: 'test-agent' }, writable: true, configurable: true });

globalThis.renderGrid = () => {};
globalThis.handleBellTransitions = () => {};
globalThis.openSession = () => {};
globalThis.updatePillBell = () => {};

import { createRequire } from 'node:module';
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const require = createRequire(import.meta.url);
const app = require(join(__dirname, '..', 'app.js'));

// --- fetch stub: records requests, returns queued canned responses ---
let _fetchCalls = [];
let _fetchResponses = [];
function queueResponse(status, body) {
  _fetchResponses.push({ status, body });
}
globalThis.fetch = async (url, opts) => {
  _fetchCalls.push({ url, opts });
  const next = _fetchResponses.shift() || { status: 200, body: {} };
  return {
    ok: next.status >= 200 && next.status < 300,
    status: next.status,
    json: async () => next.body,
  };
};

function resetAll() {
  for (const key of Object.keys(elements)) {
    elements[key] = makeStubElement(elements[key].classList.contains('hidden') ? ['hidden'] : []);
  }
  elements['compose-bar'].classList.add('hidden');
  elements['compose-notice'].classList.add('hidden');
  elements['compose-error'].classList.add('hidden');
  elements['followups-panel'].classList.add('hidden');
  elements['followups-halt-banner'].classList.add('hidden');
  _localStorageStore = {};
  _fetchCalls = [];
  _fetchResponses = [];
  app._setViewingSession(null);
  app._setDeviceId('dev-1');
  app._setViewingRemoteId('');
}

beforeEach(() => {
  resetAll();
});

// --- Keyboard: Ctrl+Shift+Enter queues, Ctrl+Enter still sends now ---

test('Ctrl+Shift+Enter queues and calls preventDefault, does not send-now', async () => {
  app._setViewingSession('sess');
  elements['compose-input'].value = 'queue me';
  queueResponse(200, { session: 'sess', revision: 1, item: { id: 'a', text: 'queue me', enter: true } });
  queueResponse(200, { session: 'sess', revision: 1, items: [], halted: null, target_window: '1:amplifier' });

  let prevented = false;
  const pending = app._composeKeydown({
    key: 'Enter', ctrlKey: true, shiftKey: true, altKey: false, metaKey: false,
    preventDefault() { prevented = true; },
  });
  if (pending && typeof pending.then === 'function') await pending;

  assert.strictEqual(prevented, true);
  // First call is the queue POST, never /input.
  assert.ok(_fetchCalls[0].url.includes('/followups'));
  assert.ok(!_fetchCalls[0].url.includes('/input'));
});

test('bare Ctrl+Enter still sends now (unchanged)', async () => {
  app._setViewingSession('sess');
  elements['compose-input'].value = 'send me';
  queueResponse(200, { ok: true, session: 'sess', snapshot: '' });

  let prevented = false;
  const pending = app._composeKeydown({
    key: 'Enter', ctrlKey: true, shiftKey: false, altKey: false, metaKey: false,
    preventDefault() { prevented = true; },
  });
  if (pending && typeof pending.then === 'function') await pending;

  assert.strictEqual(prevented, true);
  assert.ok(_fetchCalls[0].url.includes('/input'));
});

// --- Queue button gating ---

test('queue button disabled when input_enabled is false', () => {
  app._setViewingSession('sess');
  app._setServerSettings({ input_enabled: false });
  app._composeRenderEnabledState();
  assert.strictEqual(elements['compose-queue-btn'].disabled, true);
});

test('queue button disabled while viewing a remote session', () => {
  app._setViewingSession('sess');
  app._setViewingRemoteId('remote-1');
  app._setServerSettings({ input_enabled: true });
  app._composeRenderEnabledState();
  assert.strictEqual(elements['compose-queue-btn'].disabled, true);
});

test('queue button enabled when input_enabled true and local session', () => {
  app._setViewingSession('sess');
  app._setServerSettings({ input_enabled: true });
  app._composeRenderEnabledState();
  assert.strictEqual(elements['compose-queue-btn'].disabled, false);
});

// --- Draft survives failure, cleared only on success ---

test('queue draft cleared only on success (2xx)', async () => {
  app._setViewingSession('sess');
  elements['compose-input'].value = 'will fail';
  queueResponse(403, { detail: 'Session input is disabled (settings.input_enabled=false)' });

  await app._followupsQueueDraft();
  assert.strictEqual(elements['compose-input'].value, 'will fail'); // NOT cleared
  assert.strictEqual(elements['compose-error'].classList.contains('hidden'), false);
});

test('queue draft cleared on 2xx', async () => {
  app._setViewingSession('sess');
  elements['compose-input'].value = 'will succeed';
  queueResponse(200, { session: 'sess', revision: 1, item: { id: 'a', text: 'will succeed', enter: true } });
  queueResponse(200, { session: 'sess', revision: 1, items: [{ id: 'a', text: 'will succeed', enter: true }], halted: null, target_window: '1:amplifier' });

  await app._followupsQueueDraft();
  assert.strictEqual(elements['compose-input'].value, '');
});

// --- Rendering: header line, target window, halted banner ---

test('renders header with shared-device line and target window whenever list is shown', () => {
  app._followupsSetDataForTests({
    session: 'sess', revision: 1,
    items: [{ id: 'a', text: 'do X', enter: true }],
    halted: null, target_window: '1:amplifier',
  });
  app._followupsRender();
  assert.strictEqual(elements['followups-panel'].classList.contains('hidden'), false);
  assert.ok(elements['followups-header'].textContent.includes('shared with every device'));
  assert.ok(elements['followups-header'].textContent.includes('1:amplifier'));
});

test('halted banner renders reason text and is visible', () => {
  app._followupsSetDataForTests({
    session: 'sess', revision: 2,
    items: [{ id: 'a', text: 'stuck', enter: true }],
    halted: { reason: 'input_not_allowed', detail: 'not allowlisted', at: 0, item_id: 'a' },
    target_window: null,
  });
  app._followupsRender();
  assert.strictEqual(elements['followups-halt-banner'].classList.contains('hidden'), false);
  assert.ok(elements['followups-halt-text'].textContent.includes('not allowlisted'));
});

test('panel hidden when no items and no halt', () => {
  app._followupsSetDataForTests({ session: 'sess', revision: 0, items: [], halted: null, target_window: null });
  app._followupsRender();
  assert.strictEqual(elements['followups-panel'].classList.contains('hidden'), true);
});

// --- resume() calls the resume endpoint ---

test('resume calls POST .../followups/resume', async () => {
  app._setViewingSession('sess');
  queueResponse(200, {});
  queueResponse(200, { session: 'sess', revision: 2, items: [], halted: null, target_window: null });
  await app._followupsResume();
  assert.ok(_fetchCalls[0].url.includes('/followups/resume'));
  assert.strictEqual(_fetchCalls[0].opts.method, 'POST');
});

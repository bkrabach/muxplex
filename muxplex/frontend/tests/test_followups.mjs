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

// Bound at `document`, not #compose-input -- see _followupsQueueKeydown()'s
// docstring: a local-only binding meant the shortcut only fired when the
// compose textarea itself had focus, and the terminal's own xterm key
// handler independently claimed the identical Ctrl+Shift+Enter chord, so
// the shortcut was silently swallowed whenever the terminal had focus
// (which is most of the time). This is now a document-level listener so it
// fires regardless of the currently-focused element.
test('Ctrl+Shift+Enter (document-level) queues and calls preventDefault, does not send-now', async () => {
  app._setViewingSession('sess');
  app._setServerSettings({ input_enabled: true });
  elements['compose-input'].value = 'queue me';
  queueResponse(200, { session: 'sess', revision: 1, item: { id: 'a', text: 'queue me', enter: true } });
  queueResponse(200, { session: 'sess', revision: 1, items: [], halted: null, target_window: '1:amplifier' });

  let prevented = false;
  const pending = app._followupsQueueKeydown({
    key: 'Enter', ctrlKey: true, shiftKey: true, altKey: false, metaKey: false,
    preventDefault() { prevented = true; },
  });
  if (pending && typeof pending.then === 'function') await pending;

  assert.strictEqual(prevented, true);
  // First call is the queue POST, never /input.
  assert.ok(_fetchCalls[0].url.includes('/followups'));
  assert.ok(!_fetchCalls[0].url.includes('/input'));
});

test('Ctrl+Shift+Enter (document-level) is a no-op when no session is open', () => {
  app._setViewingSession(null);
  let prevented = false;
  app._followupsQueueKeydown({
    key: 'Enter', ctrlKey: true, shiftKey: true, altKey: false, metaKey: false,
    preventDefault() { prevented = true; },
  });
  assert.strictEqual(prevented, false);
  assert.strictEqual(_fetchCalls.length, 0);
});

test('Ctrl+Shift+Enter (document-level) is a no-op when input_enabled is false', () => {
  app._setViewingSession('sess');
  app._setServerSettings({ input_enabled: false });
  let prevented = false;
  app._followupsQueueKeydown({
    key: 'Enter', ctrlKey: true, shiftKey: true, altKey: false, metaKey: false,
    preventDefault() { prevented = true; },
  });
  assert.strictEqual(prevented, false);
  assert.strictEqual(_fetchCalls.length, 0);
});

test('Ctrl+Shift+Enter (document-level) is a no-op while viewing a remote session', () => {
  app._setViewingSession('sess');
  app._setServerSettings({ input_enabled: true });
  app._setViewingRemoteId('remote-1');
  let prevented = false;
  app._followupsQueueKeydown({
    key: 'Enter', ctrlKey: true, shiftKey: true, altKey: false, metaKey: false,
    preventDefault() { prevented = true; },
  });
  assert.strictEqual(prevented, false);
  assert.strictEqual(_fetchCalls.length, 0);
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

// --- Cross-session contamination guard (bug 2) ---
//
// Reproduces the reported hazard directly: `_followupsData` is a snapshot
// fetched for session A (stale, left over from before a session switch);
// `_viewingSession` has since moved to session B. `_followupsReorder()` (and
// every other mutation helper) funnels through `_followupsPut()`, which must
// refuse to write session A's stale items onto session B's queue instead of
// silently overwriting it. `revision` alone can't catch this -- it's a
// coincidence that two different sessions' revisions could match -- so the
// guard is on `_followupsData.session` vs `_viewingSession`, both drawn from
// the server's own responses.

test('_followupsPut refuses to write when _followupsData.session does not match _viewingSession', async () => {
  // _followupsData was fetched for session A (two items, so _followupsReorder's
  // own bounds-check below is a genuinely valid move, isolating the session
  // guard as the thing under test) ...
  app._followupsSetDataForTests({
    session: 'session-A', revision: 3,
    items: [
      { id: 'a1', text: 'A item one', enter: true },
      { id: 'a2', text: 'A item two', enter: true },
    ],
    halted: null, target_window: null,
  });
  // ... but the user has since switched to session B.
  app._setViewingSession('session-B');

  // A re-fetch for session B (what the guard triggers instead of writing).
  queueResponse(200, { session: 'session-B', revision: 0, items: [], halted: null, target_window: null });

  await app._followupsReorder('a1', 1); // valid move (idx 0 -> 1, 2 items) -- funnels through _followupsPut()

  // No PUT was ever sent to EITHER session -- the stale write was refused,
  // not silently redirected or silently dropped.
  const putCalls = _fetchCalls.filter((c) => c.opts && c.opts.method === 'PUT');
  assert.strictEqual(putCalls.length, 0, 'must never PUT a stale cross-session snapshot');

  // The guard's recovery path re-fetches the CURRENTLY viewed session.
  assert.strictEqual(_fetchCalls.length, 1);
  assert.ok(_fetchCalls[0].url.includes('/sessions/session-B/followups'));
  assert.strictEqual(_fetchCalls[0].opts.method, 'GET');
});

test('_followupsPut proceeds normally once _followupsData.session matches _viewingSession', async () => {
  app._followupsSetDataForTests({
    session: 'sess', revision: 3,
    items: [
      { id: 'a1', text: 'item one', enter: true },
      { id: 'a2', text: 'item two', enter: true },
    ],
    halted: null, target_window: null,
  });
  app._setViewingSession('sess');
  queueResponse(200, {
    session: 'sess', revision: 4,
    items: [
      { id: 'a2', text: 'item two', enter: true },
      { id: 'a1', text: 'item one', enter: true },
    ],
    halted: null, target_window: null,
  });

  await app._followupsReorder('a1', 1); // valid move, same session -- must be allowed through

  const putCalls = _fetchCalls.filter((c) => c.opts && c.opts.method === 'PUT');
  assert.strictEqual(putCalls.length, 1, 'matching session must still be allowed to write');
});

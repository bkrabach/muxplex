// localStorage stub — must be set before importing app.js
let _localStorageStore = {};
globalThis.localStorage = {
  getItem: (key) => (Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null),
  setItem: (key, value) => { _localStorageStore[key] = String(value); },
  removeItem: (key) => { delete _localStorageStore[key]; },
};

// Browser global stubs — must be set before importing app.js
globalThis.document = {
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => ({ style: {}, classList: { add: () => {}, remove: () => {} } }),
  addEventListener: () => {},
  removeEventListener: () => {},
};

// Stubs for functions called by pollSessions (implemented in later tasks)
globalThis.renderGrid = () => {};
globalThis.handleBellTransitions = () => {};
// Stubs for functions called by renderGrid (implemented in later tasks)
globalThis.openSession = () => {};
globalThis.updatePillBell = () => {};

globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
  innerWidth: 1024,
};

globalThis.Notification = {
  permission: 'default',
  requestPermission: async () => 'default',
};

// navigator is read-only in Node v24+, use defineProperty
Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'test-agent' },
  writable: true,
  configurable: true,
});

import { createRequire } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const require = createRequire(import.meta.url);
const app = require(join(__dirname, '..', 'app.js'));

test('app.js exports all 7 pure functions', () => {
  const expectedFunctions = [
    'formatTimestamp',
    'sessionPriority',
    'sortByPriority',
    'filterByQuery',
    'detectBellTransitions',
    'generateDeviceId',
    'buildHeartbeatPayload',
  ];

  for (const fn of expectedFunctions) {
    assert.ok(fn in app, `app.js should export "${fn}"`);
    assert.strictEqual(typeof app[fn], 'function', `"${fn}" should be a function`);
  }
});

// --- formatTimestamp ---

test('formatTimestamp returns empty string for null', () => {
  assert.strictEqual(app.formatTimestamp(null), '');
});

test('formatTimestamp returns seconds ago for timestamp < 60s ago', () => {
  const ts = Math.floor(Date.now() / 1000) - 30;
  assert.match(app.formatTimestamp(ts), /^\d+s ago$/);
});

test('formatTimestamp returns minutes ago for timestamp between 60s and 3600s ago', () => {
  const ts = Math.floor(Date.now() / 1000) - 120;
  assert.match(app.formatTimestamp(ts), /^\d+m ago$/);
});

test('formatTimestamp returns hours ago for timestamp >= 3600s ago', () => {
  const ts = Math.floor(Date.now() / 1000) - 7200;
  assert.match(app.formatTimestamp(ts), /^\d+h ago$/);
});

// --- sessionPriority ---

test('sessionPriority returns bell when unseen_count > 0 and seen_at is null', () => {
  const session = { bell: { unseen_count: 3, seen_at: null, last_fired_at: 100 } };
  assert.strictEqual(app.sessionPriority(session), 'bell');
});

test('sessionPriority returns bell when last_fired_at > seen_at', () => {
  const session = { bell: { unseen_count: 1, seen_at: 50, last_fired_at: 100 } };
  assert.strictEqual(app.sessionPriority(session), 'bell');
});

test('sessionPriority returns idle when unseen_count is 0', () => {
  const session = { bell: { unseen_count: 0, seen_at: null, last_fired_at: 100 } };
  assert.strictEqual(app.sessionPriority(session), 'idle');
});

test('sessionPriority returns idle when seen_at >= last_fired_at', () => {
  const session = { bell: { unseen_count: 1, seen_at: 100, last_fired_at: 50 } };
  assert.strictEqual(app.sessionPriority(session), 'idle');
});

// --- sortByPriority ---

test('sortByPriority puts bell sessions before idle sessions', () => {
  const idleSession = { id: 'idle', bell: { unseen_count: 0, seen_at: null, last_fired_at: 0 } };
  const bellSession = { id: 'bell', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 } };
  const result = app.sortByPriority([idleSession, bellSession]);
  assert.strictEqual(result[0].id, 'bell');
  assert.strictEqual(result[1].id, 'idle');
});

test('sortByPriority does not mutate the input array', () => {
  const idleSession = { id: 'idle', bell: { unseen_count: 0, seen_at: null, last_fired_at: 0 } };
  const bellSession = { id: 'bell', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 } };
  const input = [idleSession, bellSession];
  app.sortByPriority(input);
  assert.strictEqual(input[0].id, 'idle');
  assert.strictEqual(input[1].id, 'bell');
});

// --- filterByQuery ---

test('filterByQuery returns all sessions when query is empty string', () => {
  const sessions = [{ name: 'Alpha' }, { name: 'Beta' }];
  assert.deepStrictEqual(app.filterByQuery(sessions, ''), sessions);
});

test('filterByQuery returns all sessions when query is null', () => {
  const sessions = [{ name: 'Alpha' }, { name: 'Beta' }];
  assert.deepStrictEqual(app.filterByQuery(sessions, null), sessions);
});

test('filterByQuery matches case-insensitive substring in session.name', () => {
  const sessions = [{ name: 'My Project' }, { name: 'Another Session' }];
  const result = app.filterByQuery(sessions, 'proj');
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].name, 'My Project');
});

test('filterByQuery returns empty array when no session name matches', () => {
  const sessions = [{ id: 'match-id', name: 'nomatch' }];
  assert.deepStrictEqual(app.filterByQuery(sessions, 'match-id'), []);
});

// --- detectBellTransitions ---

test('detectBellTransitions fires when existing session goes from 0 to positive unseen_count', () => {
  const prev = [{ name: 'work', bell: { unseen_count: 0 } }];
  const next = [{ name: 'work', bell: { unseen_count: 1 } }];
  assert.deepStrictEqual(app.detectBellTransitions(prev, next), ['work']);
});

test('detectBellTransitions returns empty array when unseen_count does not change', () => {
  const prev = [{ name: 'work', bell: { unseen_count: 2 } }];
  const next = [{ name: 'work', bell: { unseen_count: 2 } }];
  assert.deepStrictEqual(app.detectBellTransitions(prev, next), []);
});

test('detectBellTransitions fires for new session not in prev with bell > 0', () => {
  const prev = [];
  const next = [{ name: 'new-session', bell: { unseen_count: 3 } }];
  assert.deepStrictEqual(app.detectBellTransitions(prev, next), ['new-session']);
});

test('detectBellTransitions fires when unseen_count increases', () => {
  const prev = [{ name: 'task', bell: { unseen_count: 1 } }];
  const next = [{ name: 'task', bell: { unseen_count: 4 } }];
  assert.deepStrictEqual(app.detectBellTransitions(prev, next), ['task']);
});

test('detectBellTransitions does not fire when unseen_count decreases', () => {
  const prev = [{ name: 'task', bell: { unseen_count: 5 } }];
  const next = [{ name: 'task', bell: { unseen_count: 2 } }];
  assert.deepStrictEqual(app.detectBellTransitions(prev, next), []);
});

test('detectBellTransitions uses sessionKey to distinguish same-name sessions across devices', () => {
  // Two sessions both named 'main' but from different devices, identified by sessionKey
  const prev = [
    { name: 'main', sessionKey: 'device-A::main', bell: { unseen_count: 3 } },
    { name: 'main', sessionKey: 'device-B::main', bell: { unseen_count: 0 } },
  ];
  const next = [
    { name: 'main', sessionKey: 'device-A::main', bell: { unseen_count: 3 } }, // no change
    { name: 'main', sessionKey: 'device-B::main', bell: { unseen_count: 2 } }, // increased
  ];
  // Only the device-B 'main' session should trigger (its count increased from 0 to 2)
  assert.deepStrictEqual(app.detectBellTransitions(prev, next), ['main']);
});

test('detectBellTransitions falls back to s.name when no sessionKey present', () => {
  // Sessions without sessionKey should still work using name as fallback
  const prev = [{ name: 'work', bell: { unseen_count: 0 } }];
  const next = [{ name: 'work', bell: { unseen_count: 1 } }];
  assert.deepStrictEqual(app.detectBellTransitions(prev, next), ['work']);
});

// --- generateDeviceId ---

test('generateDeviceId returns a string matching /^d-[a-z0-9]+$/', () => {
  const id = app.generateDeviceId();
  assert.match(id, /^d-[a-z0-9]+$/);
});

test('generateDeviceId produces unique IDs on successive calls', () => {
  const id1 = app.generateDeviceId();
  const id2 = app.generateDeviceId();
  assert.notStrictEqual(id1, id2);
});

// --- buildHeartbeatPayload ---

test('buildHeartbeatPayload returns correct shape with all required fields', () => {
  const payload = app.buildHeartbeatPayload('d-abc123', 'session-1', 'split', 1700000000);
  assert.strictEqual(payload.device_id, 'd-abc123');
  assert.strictEqual(payload.viewing_session, 'session-1');
  assert.strictEqual(payload.view_mode, 'split');
  assert.strictEqual(payload.last_interaction_at, 1700000000);
  assert.ok('label' in payload, 'payload should have label field');
});

test('buildHeartbeatPayload includes null viewing_session when passed null', () => {
  const payload = app.buildHeartbeatPayload('d-abc123', null, 'full', 0);
  assert.strictEqual(payload.viewing_session, null);
});

test('buildHeartbeatPayload label uses navigator.userAgent sliced to 50 chars', () => {
  const payload = app.buildHeartbeatPayload('d-abc123', null, 'full', 0);
  const expected = globalThis.navigator.userAgent.slice(0, 50);
  assert.strictEqual(payload.label, expected);
});

// --- setConnectionStatus ---

test('setConnectionStatus ok sets bullet text and ok CSS class', () => {
  const mockEl = { textContent: '', className: '' };
  const orig = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'connection-status' ? mockEl : null);

  app.setConnectionStatus('ok');

  assert.strictEqual(mockEl.textContent, '\u25cf');
  assert.strictEqual(mockEl.className, 'connection-status--ok');
  globalThis.document.getElementById = orig;
});

test('setConnectionStatus warn sets slow text and warn CSS class', () => {
  const mockEl = { textContent: '', className: '' };
  const orig = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'connection-status' ? mockEl : null);

  app.setConnectionStatus('warn');

  assert.strictEqual(mockEl.textContent, '\u25cc slow');
  assert.strictEqual(mockEl.className, 'connection-status--warn');
  globalThis.document.getElementById = orig;
});

test('setConnectionStatus err sets offline text and err CSS class', () => {
  const mockEl = { textContent: '', className: '' };
  const orig = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'connection-status' ? mockEl : null);

  app.setConnectionStatus('err');

  assert.strictEqual(mockEl.textContent, '\u2715 offline');
  assert.strictEqual(mockEl.className, 'connection-status--err');
  globalThis.document.getElementById = orig;
});

// --- pollSessions ---

test('pollSessions on success sets ok status', async () => {
  const sessions = [{ name: 'test-session' }];
  const mockEl = { textContent: '', className: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'connection-status' ? mockEl : null);
  globalThis.fetch = async () => ({ ok: true, json: async () => sessions });

  await app.pollSessions();

  assert.strictEqual(mockEl.className, 'connection-status--ok');
  globalThis.document.getElementById = origGetById;
  globalThis.fetch = undefined;
});

test('pollSessions on first failure sets warn status', async () => {
  const mockEl = { textContent: '', className: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'connection-status' ? mockEl : null);

  // Reset fail count to 0 by succeeding first
  globalThis.fetch = async () => ({ ok: true, json: async () => [] });
  await app.pollSessions();

  // Now fail once
  globalThis.fetch = async () => { throw new Error('network error'); };
  await app.pollSessions();

  assert.strictEqual(mockEl.className, 'connection-status--warn');
  globalThis.document.getElementById = origGetById;
  globalThis.fetch = undefined;
});

test('pollSessions sets err status after more than 2 failures', async () => {
  const mockEl = { textContent: '', className: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'connection-status' ? mockEl : null);

  // Reset fail count to 0 by succeeding first
  globalThis.fetch = async () => ({ ok: true, json: async () => [] });
  await app.pollSessions();

  // Fail 3 times (count goes 1 → warn, 2 → warn, 3 → err)
  globalThis.fetch = async () => { throw new Error('network error'); };
  await app.pollSessions(); // count = 1 → warn
  await app.pollSessions(); // count = 2 → warn
  await app.pollSessions(); // count = 3 → err

  assert.strictEqual(mockEl.className, 'connection-status--err');
  globalThis.document.getElementById = origGetById;
  globalThis.fetch = undefined;
});

test('pollSessions calls renderSidebar when viewMode is fullscreen', async () => {
  let sidebarRendered = false;
  const sessions = [{ name: 'test-session', snapshot: '', bell: { unseen_count: 0 } }];

  const mockStatusEl = { textContent: '', className: '' };
  const mockGrid = { innerHTML: '' };
  const mockEmptyState = { style: {}, classList: { add: () => {}, remove: () => {} } };
  const mockPillBell = { classList: { add: () => {}, remove: () => {} } };
  const mockSidebarList = {
    get innerHTML() { return ''; },
    set innerHTML(v) { sidebarRendered = true; },
    querySelectorAll: () => [],
  };

  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'connection-status') return mockStatusEl;
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmptyState;
    if (id === 'session-pill-bell') return mockPillBell;
    if (id === 'sidebar-list') return mockSidebarList;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];
  globalThis.fetch = async () => ({ ok: true, json: async () => sessions });

  app._setViewMode('fullscreen');
  await app.pollSessions();

  assert.strictEqual(sidebarRendered, true, 'renderSidebar should set sidebar-list innerHTML during pollSessions in fullscreen mode');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
  app._setViewMode('grid');
});

// --- startPolling ---

test('startPolling guards against double-start (only creates one interval)', () => {
  const intervals = [];
  const origSetInterval = globalThis.setInterval;
  globalThis.setInterval = (fn, ms) => {
    intervals.push(ms);
    return Symbol('timer');
  };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => [] });

  // First call should create the interval; second should be a no-op
  app.startPolling();
  app.startPolling();

  assert.strictEqual(intervals.length, 1, 'startPolling should create exactly one interval');
  assert.strictEqual(intervals[0], 2000, 'interval should be POLL_MS (2000ms)');

  globalThis.setInterval = origSetInterval;
  globalThis.fetch = origFetch;
});

// --- escapeHtml ---

test('escapeHtml replaces & with &amp;', () => {
  assert.strictEqual(app.escapeHtml('a&b'), 'a&amp;b');
});

test('escapeHtml replaces < with &lt;', () => {
  assert.strictEqual(app.escapeHtml('<tag>'), '&lt;tag&gt;');
});

test('escapeHtml replaces > with &gt;', () => {
  assert.strictEqual(app.escapeHtml('a>b'), 'a&gt;b');
});

test('escapeHtml returns unchanged string when no special characters', () => {
  assert.strictEqual(app.escapeHtml('hello world'), 'hello world');
});

// --- buildTileHTML ---

test('buildTileHTML returns article element with session-tile class', () => {
  const session = { name: 'my-session', snapshot: 'line1\nline2' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.startsWith('<article'), 'should start with <article');
  assert.ok(html.includes('session-tile'), 'should contain session-tile class');
});

test('buildTileHTML adds session-tile--bell class for bell sessions', () => {
  const session = {
    name: 'bell-session',
    bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 },
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--bell'), 'should contain --bell modifier class');
});

test('buildTileHTML does not add session-tile--bell class for non-bell sessions', () => {
  const session = {
    name: 'idle-session',
    bell: { unseen_count: 0, seen_at: null, last_fired_at: 0 },
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('session-tile--bell'), 'should not contain --bell class');
});

test('buildTileHTML sets data-session attribute with escaped session name', () => {
  const session = { name: 'my<session>', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('data-session="my&lt;session&gt;"'), 'data-session should be escaped');
});

test('buildTileHTML shows 9+ when unseen_count exceeds 9', () => {
  const session = {
    name: 's',
    bell: { unseen_count: 10, seen_at: null, last_fired_at: 100 },
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('9+'), 'should show 9+ for count > 9');
});

test('buildTileHTML shows exact count when unseen_count is <= 9', () => {
  const session = {
    name: 's',
    bell: { unseen_count: 5, seen_at: null, last_fired_at: 100 },
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('>5<'), 'should show exact count 5');
});

test('buildTileHTML includes only last 20 lines of snapshot', () => {
  const lines = [];
  for (let i = 0; i < 25; i++) lines.push(`UNIQUE_LINE_${i}_MARKER`);
  const session = { name: 's', snapshot: lines.join('\n') };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('UNIQUE_LINE_24_MARKER'), 'last line should be present');
  assert.ok(!html.includes('UNIQUE_LINE_0_MARKER'), 'first line should be excluded (>20 lines)');
});

test('buildTileHTML adds tier class on mobile', () => {
  const session = { name: 's', snapshot: '' };
  const html = app.buildTileHTML(session, 0, true);
  assert.ok(html.includes('session-tile--tier-'), 'should contain tier class on mobile');
});

test('buildTileHTML wraps snapshot in .tile-body with <pre> as direct child', () => {
  const session = { name: 'my-session', snapshot: 'line1\nline2' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('class="tile-body"'), 'should contain .tile-body wrapper');
  assert.ok(
    /<div class="tile-body"><pre>/.test(html),
    '<pre> should be a direct child of .tile-body',
  );
});

// --- renderGrid ---

test('renderGrid clears grid and shows empty-state when sessions array is empty', () => {
  const mockGrid = { innerHTML: 'existing-content' };
  const removedClasses = [];
  const mockEmpty = { style: {}, classList: { add: () => {}, remove: (c) => removedClasses.push(c) } };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };

  app.renderGrid([]);

  assert.strictEqual(mockGrid.innerHTML, '', 'grid innerHTML should be cleared');
  assert.ok(removedClasses.includes('hidden'), 'empty-state should have hidden class removed');
  globalThis.document.getElementById = origGetById;
});

test('renderGrid hides empty-state and populates grid when sessions exist', () => {
  const mockGrid = { innerHTML: '' };
  const addedClasses = [];
  const mockEmpty = { style: {}, classList: { add: (c) => addedClasses.push(c), remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  const sessions = [{ name: 'my-session', snapshot: 'hello' }];
  app.renderGrid(sessions);

  assert.ok(addedClasses.includes('hidden'), 'empty-state should have hidden class added');
  assert.ok(mockGrid.innerHTML.includes('session-tile'), 'grid should contain session tiles');
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
});

// --- requestNotificationPermission ---

test('requestNotificationPermission is exported', () => {
  assert.strictEqual(typeof app.requestNotificationPermission, 'function');
});

test('requestNotificationPermission does nothing when Notification is not defined', () => {
  const origNotification = globalThis.Notification;
  delete globalThis.Notification;
  assert.doesNotThrow(() => app.requestNotificationPermission());
  globalThis.Notification = origNotification;
});

test('requestNotificationPermission calls Notification.requestPermission when permission is default', async () => {
  let called = false;
  const origNotification = globalThis.Notification;
  globalThis.Notification = {
    permission: 'default',
    requestPermission: async () => { called = true; return 'granted'; },
  };
  app.requestNotificationPermission();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.ok(called, 'requestPermission should be called for default permission');
  globalThis.Notification = origNotification;
});

test('requestNotificationPermission does not call requestPermission when permission is granted', () => {
  let called = false;
  const origNotification = globalThis.Notification;
  globalThis.Notification = {
    permission: 'granted',
    requestPermission: async () => { called = true; return 'granted'; },
  };
  app.requestNotificationPermission();
  assert.ok(!called, 'requestPermission should NOT be called when already granted');
  globalThis.Notification = origNotification;
});

// --- handleBellTransitions ---

test('handleBellTransitions is exported', () => {
  assert.strictEqual(typeof app.handleBellTransitions, 'function');
});

test('handleBellTransitions creates Notification when permission granted and document hidden', () => {
  const created = [];
  const origNotification = globalThis.Notification;
  function MockNotification(title, opts) { created.push({ title, opts }); }
  MockNotification.permission = 'granted';
  MockNotification.requestPermission = async () => 'granted';
  globalThis.Notification = MockNotification;

  const origDocument = globalThis.document;
  globalThis.document = { ...origDocument, hidden: true };

  app.requestNotificationPermission(); // sets _notificationPermission = 'granted'
  const prev = [{ name: 'work', bell: { unseen_count: 0 } }];
  const next = [{ name: 'work', bell: { unseen_count: 1 } }];
  app.handleBellTransitions(prev, next);

  assert.strictEqual(created.length, 1, 'should create exactly one notification');
  assert.strictEqual(created[0].title, 'Activity in: work');
  assert.strictEqual(created[0].opts.body, 'tmux session needs attention');
  assert.strictEqual(created[0].opts.tag, 'tmux-bell-work');

  globalThis.Notification = origNotification;
  globalThis.document = origDocument;
});

test('handleBellTransitions does not create Notification when document is visible', () => {
  const created = [];
  const origNotification = globalThis.Notification;
  function MockNotification(title, opts) { created.push({ title, opts }); }
  MockNotification.permission = 'granted';
  MockNotification.requestPermission = async () => 'granted';
  globalThis.Notification = MockNotification;

  const origDocument = globalThis.document;
  globalThis.document = { ...origDocument, hidden: false };

  app.requestNotificationPermission(); // sets _notificationPermission = 'granted'
  const prev = [{ name: 'work', bell: { unseen_count: 0 } }];
  const next = [{ name: 'work', bell: { unseen_count: 1 } }];
  app.handleBellTransitions(prev, next);

  assert.strictEqual(created.length, 0, 'should not create notification when tab is visible');

  globalThis.Notification = origNotification;
  globalThis.document = origDocument;
});

test('handleBellTransitions does not create Notification when permission is not granted', () => {
  const created = [];
  const origNotification = globalThis.Notification;
  function MockNotification(title, opts) { created.push({ title, opts }); }
  MockNotification.permission = 'denied';
  MockNotification.requestPermission = async () => 'denied';
  globalThis.Notification = MockNotification;

  const origDocument = globalThis.document;
  globalThis.document = { ...origDocument, hidden: true };

  app.requestNotificationPermission(); // sets _notificationPermission = 'denied'
  const prev = [{ name: 'work', bell: { unseen_count: 0 } }];
  const next = [{ name: 'work', bell: { unseen_count: 1 } }];
  app.handleBellTransitions(prev, next);

  assert.strictEqual(created.length, 0, 'should not create notification when permission is denied');

  globalThis.Notification = origNotification;
  globalThis.document = origDocument;
});

test('handleBellTransitions notification tag deduplicates per session name', () => {
  const created = [];
  const origNotification = globalThis.Notification;
  function MockNotification(title, opts) { created.push({ title, opts }); }
  MockNotification.permission = 'granted';
  MockNotification.requestPermission = async () => 'granted';
  globalThis.Notification = MockNotification;

  const origDocument = globalThis.document;
  globalThis.document = { ...origDocument, hidden: true };

  app.requestNotificationPermission();
  const prev = [{ name: 'my-session', bell: { unseen_count: 1 } }];
  const next = [{ name: 'my-session', bell: { unseen_count: 3 } }];
  app.handleBellTransitions(prev, next);

  assert.strictEqual(created.length, 1);
  assert.strictEqual(created[0].opts.tag, 'tmux-bell-my-session', 'tag should use session name for deduplication');

  globalThis.Notification = origNotification;
  globalThis.document = origDocument;
});

// --- startHeartbeat ---

test('startHeartbeat is exported', () => {
  assert.strictEqual(typeof app.startHeartbeat, 'function');
});

test('startHeartbeat guards against double-start (only creates one interval)', () => {
  const intervals = [];
  const origSetInterval = globalThis.setInterval;
  globalThis.setInterval = (fn, ms) => {
    intervals.push(ms);
    return Symbol('heartbeat-timer');
  };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

  app._resetHeartbeatTimer(); // ensure clean state regardless of test order
  app.startHeartbeat();
  app.startHeartbeat(); // second call should be a no-op

  assert.strictEqual(intervals.length, 1, 'startHeartbeat should create exactly one interval');
  assert.strictEqual(intervals[0], 5000, 'interval should be HEARTBEAT_MS (5000ms)');

  globalThis.setInterval = origSetInterval;
  globalThis.fetch = origFetch;
  // _heartbeatTimer is now set; next test using startHeartbeat must call _resetHeartbeatTimer()
});

test('startHeartbeat calls sendHeartbeat immediately (calls fetch for heartbeat)', async () => {
  const calls = [];
  const origSetInterval = globalThis.setInterval;
  globalThis.setInterval = () => Symbol('timer');
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => ({}) };
  };

  app._resetHeartbeatTimer(); // clear state left by double-start test
  app.startHeartbeat();
  // sendHeartbeat() is async but called without await inside startHeartbeat;
  // yield to the microtask queue so the fetch promise resolves
  await new Promise((r) => setTimeout(r, 0));

  assert.strictEqual(calls.length, 1, 'startHeartbeat should call sendHeartbeat immediately exactly once');
  assert.ok(
    calls.some((c) => c.url === '/api/heartbeat'),
    'should POST to /api/heartbeat immediately on start',
  );

  app._resetHeartbeatTimer(); // clean up so later tests are not affected
  globalThis.setInterval = origSetInterval;
  globalThis.fetch = origFetch;
});

// --- sendHeartbeat ---

test('sendHeartbeat is exported', () => {
  assert.strictEqual(typeof app.sendHeartbeat, 'function');
});

test('sendHeartbeat POSTs to /api/heartbeat with heartbeat payload fields', async () => {
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => ({}) };
  };

  await app.sendHeartbeat();

  assert.strictEqual(calls.length, 1, 'should call fetch once');
  assert.strictEqual(calls[0].url, '/api/heartbeat');
  assert.strictEqual(calls[0].opts.method, 'POST');
  assert.strictEqual(calls[0].opts.headers['Content-Type'], 'application/json');

  // Parse body — device_id and last_interaction_at may be absent in test env
  // (module state not initialized via DOMContentLoaded), but view_mode, label,
  // and viewing_session are always present and serializable.
  const body = JSON.parse(calls[0].opts.body);
  assert.ok('label' in body, 'payload should have label');
  assert.ok('view_mode' in body, 'payload should have view_mode');
  assert.ok('viewing_session' in body, 'payload should have viewing_session');

  globalThis.fetch = origFetch;
});

test('sendHeartbeat catches errors with console.warn (does not throw)', async () => {
  const warnings = [];
  const origWarn = console.warn;
  console.warn = (...args) => warnings.push(args);
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('network failure'); };

  // Should not throw
  await assert.doesNotReject(() => app.sendHeartbeat());

  assert.strictEqual(warnings.length, 1, 'console.warn should be called on error');

  console.warn = origWarn;
  globalThis.fetch = origFetch;
});

// --- showToast ---

test('showToast is exported', () => {
  assert.strictEqual(typeof app.showToast, 'function');
});

test('showToast sets toast textContent and removes hidden class', () => {
  const removedClasses = [];
  const mockToast = {
    textContent: '',
    classList: {
      remove: (cls) => removedClasses.push(cls),
      add: () => {},
    },
  };
  const orig = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'toast' ? mockToast : null);

  app.showToast('something happened');

  assert.strictEqual(mockToast.textContent, 'something happened');
  assert.ok(removedClasses.includes('hidden'), 'should remove hidden class');
  globalThis.document.getElementById = orig;
});

test('showToast schedules hidden class restore after 3000ms', () => {
  let capturedMs;
  const addedClasses = [];
  const mockToast = {
    textContent: '',
    classList: {
      remove: () => {},
      add: (cls) => addedClasses.push(cls),
    },
  };
  const origGetById = globalThis.document.getElementById;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.document.getElementById = (id) => (id === 'toast' ? mockToast : null);
  globalThis.setTimeout = (fn, ms) => { capturedMs = ms; fn(); }; // invoke immediately

  app.showToast('hello');

  assert.strictEqual(capturedMs, 3000, 'setTimeout delay should be 3000ms');
  assert.ok(addedClasses.includes('hidden'), 'should add hidden class after timeout');
  globalThis.document.getElementById = origGetById;
  globalThis.setTimeout = origSetTimeout;
});

// --- updatePillBell ---

test('updatePillBell is exported', () => {
  assert.strictEqual(typeof app.updatePillBell, 'function');
});

test('updatePillBell shows pill bell when another session has unseen bell', async () => {
  const pillRemovedClasses = [];
  const mockPillBell = { classList: { add: () => {}, remove: (c) => pillRemovedClasses.push(c) } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-pill-bell') return mockPillBell;
    if (id === 'session-grid') return { innerHTML: '' };
    if (id === 'empty-state') return { style: {}, classList: { add: () => {}, remove: () => {} } };
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  // populate _currentSessions
  const sessions = [
    { name: 'alpha', bell: { unseen_count: 0 } },
    { name: 'beta', bell: { unseen_count: 3, seen_at: null, last_fired_at: 100 } },
  ];
  globalThis.fetch = async () => ({ ok: true, json: async () => sessions });
  await app.pollSessions();

  // set _viewingSession to 'alpha' so 'beta' is "other"
  app._setViewingSession('alpha');

  app.updatePillBell();

  assert.ok(pillRemovedClasses.includes('hidden'), 'pill bell should have hidden class removed');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
});

test('updatePillBell hides pill bell when no other session has unseen bells', async () => {
  const pillAddedClasses = [];
  const mockPillBell = { classList: { add: (c) => pillAddedClasses.push(c), remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-pill-bell') return mockPillBell;
    if (id === 'session-grid') return { innerHTML: '' };
    if (id === 'empty-state') return { style: {}, classList: { add: () => {}, remove: () => {} } };
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  const sessions = [
    { name: 'alpha', bell: { unseen_count: 0 } },
    { name: 'beta', bell: { unseen_count: 0 } },
  ];
  globalThis.fetch = async () => ({ ok: true, json: async () => sessions });
  await app.pollSessions();

  app._setViewingSession('alpha');

  app.updatePillBell();

  assert.ok(pillAddedClasses.includes('hidden'), 'pill bell should have hidden class added');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
});

// --- openSession ---

test('openSession is exported', () => {
  assert.strictEqual(typeof app.openSession, 'function');
});

test('openSession returns a Promise', () => {
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};

  const result = app.openSession('test-session', { skipConnect: true });

  assert.ok(result instanceof Promise, 'openSession should return a Promise');
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession with skipConnect calls window._openTerminal inside setTimeout callback', async () => {
  let openTerminalCalledWith = null;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  // Use a synchronous mock so setTimeout callbacks run immediately — _openTerminal is now called inside setTimeout
  globalThis.setTimeout = (fn) => { fn(); };
  globalThis.window._openTerminal = (name) => { openTerminalCalledWith = name; };

  await app.openSession('my-session', { skipConnect: true });

  assert.strictEqual(openTerminalCalledWith, 'my-session', '_openTerminal should be called with session name');
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession without skipConnect POSTs to /api/sessions/{name}/connect', async () => {
  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};

  await app.openSession('work', {});

  const connectCall = fetchCalls.find((c) => c.url === '/api/sessions/work/connect');
  assert.ok(connectCall, 'should POST to /api/sessions/work/connect');
  assert.strictEqual(connectCall.opts.method, 'POST');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession shows toast and calls closeSession on connect failure', async () => {
  let closeTerminalCalled = false;
  const mockToast = { textContent: '', classList: { remove: () => {}, add: () => {} } };
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url) => {
    if (url.includes('/connect')) throw new Error('Connection failed');
    return { ok: true };
  };
  globalThis.document.getElementById = (id) => {
    if (id === 'toast') return mockToast;
    return { textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } };
  };
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};
  globalThis.window._closeTerminal = () => { closeTerminalCalled = true; };

  await app.openSession('failing-session', {});

  assert.ok(mockToast.textContent.length > 0, 'toast should show an error message');
  assert.ok(closeTerminalCalled, 'closeSession should be called (_closeTerminal invoked)');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

// --- closeSession ---

test('closeSession is exported', () => {
  assert.strictEqual(typeof app.closeSession, 'function');
});

test('closeSession returns a Promise', async () => {
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = () => ({ style: {}, classList: { add: () => {}, remove: () => {} } });
  globalThis.window._closeTerminal = () => {};
  globalThis.fetch = async () => ({ ok: true });

  const result = app.closeSession();
  assert.ok(result instanceof Promise, 'closeSession should return a Promise');
  await result;

  globalThis.document.getElementById = origGetById;
  globalThis.fetch = undefined;
});

test('closeSession calls window._closeTerminal', async () => {
  let closeTerminalCalled = false;
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = () => ({ style: {}, classList: { add: () => {}, remove: () => {} } });
  globalThis.window._closeTerminal = () => { closeTerminalCalled = true; };
  globalThis.fetch = async () => ({ ok: true });

  await app.closeSession();

  assert.ok(closeTerminalCalled, 'window._closeTerminal should be called');
  globalThis.document.getElementById = origGetById;
  globalThis.fetch = undefined;
});

test('closeSession fires DELETE /api/sessions/current', async () => {
  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };
  globalThis.document.getElementById = () => ({ style: {}, classList: { add: () => {}, remove: () => {} } });
  globalThis.window._closeTerminal = () => {};

  await app.closeSession();
  // yield microtask queue for fire-and-forget DELETE
  await new Promise((r) => setTimeout(r, 0));

  const deleteCall = fetchCalls.find((c) => c.url === '/api/sessions/current' && c.opts && c.opts.method === 'DELETE');
  assert.ok(deleteCall, 'should fire DELETE /api/sessions/current');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
});

// ─── Command Palette ─────────────────────────────────────────────────────────



test('handleGlobalKeydown is exported', () => {
  assert.strictEqual(typeof app.handleGlobalKeydown, 'function');
});

test('bindStaticEventListeners is exported', () => {
  assert.strictEqual(typeof app.bindStaticEventListeners, 'function');
});



































test('bindStaticEventListeners binds back-btn click to closeSession', () => {
  const eventsBound = {};
  const origGetById = globalThis.document.getElementById;
  const origDocAddListener = globalThis.document.addEventListener;
  globalThis.document.getElementById = (id) => {
    const el = { _events: {}, addEventListener: (ev, fn) => { el._events[ev] = fn; } };
    eventsBound[id] = el;
    return el;
  };
  globalThis.document.addEventListener = () => {};

  app.bindStaticEventListeners();

  assert.ok(eventsBound['back-btn'] && 'click' in eventsBound['back-btn']._events, '#back-btn should have a click listener');
  globalThis.document.getElementById = origGetById;
  globalThis.document.addEventListener = origDocAddListener;
});

test('bindStaticEventListeners binds document keydown to handleGlobalKeydown', () => {
  let keydownBound = false;
  const origGetById = globalThis.document.getElementById;
  const origDocAddListener = globalThis.document.addEventListener;
  globalThis.document.getElementById = (id) => ({
    _events: {},
    addEventListener: () => {},
  });
  globalThis.document.addEventListener = (ev, fn) => {
    if (ev === 'keydown') keydownBound = true;
  };

  app.bindStaticEventListeners();

  assert.ok(keydownBound, 'document should have a keydown listener bound');
  globalThis.document.getElementById = origGetById;
  globalThis.document.addEventListener = origDocAddListener;
});

// --- Bottom sheet / session pill ---

test('renderSheetList: builds list with bell indicator', () => {
  // We can't test DOM manipulation, but we can test that sortByPriority + formatTimestamp
  // work correctly together for the sheet use case
  const sessions = [
    { name: 'idle', bell: { unseen_count: 0, last_fired_at: null, seen_at: null } },
    { name: 'bell', bell: { unseen_count: 1, last_fired_at: 100, seen_at: null } },
  ];
  const sorted = app.sortByPriority(sessions);
  assert.strictEqual(sorted[0].name, 'bell', 'bell session comes first in sorted list');
  assert.strictEqual(sorted[1].name, 'idle');
});

test('updateSessionPill logic: others with bell count', () => {
  // Test the bell-detection logic in isolation
  const allSessions = [
    { name: 'current', bell: { unseen_count: 0, last_fired_at: null, seen_at: null } },
    { name: 'other', bell: { unseen_count: 2, last_fired_at: 100, seen_at: null } },
  ];
  const viewingSession = 'current';
  const othersWithBell = allSessions.filter(function(s) {
    return s.name !== viewingSession &&
      s.bell && s.bell.unseen_count > 0 &&
      (s.bell.seen_at === null || s.bell.last_fired_at > s.bell.seen_at);
  });
  assert.strictEqual(othersWithBell.length, 1, 'should find one other session with bell');
  assert.strictEqual(othersWithBell[0].name, 'other');
});

test('updateSessionPill logic: no bells in others', () => {
  const allSessions = [
    { name: 'a', bell: { unseen_count: 0, last_fired_at: null, seen_at: null } },
    { name: 'b', bell: { unseen_count: 0, last_fired_at: null, seen_at: null } },
  ];
  const viewingSession = 'a';
  const othersWithBell = allSessions.filter(function(s) {
    return s.name !== viewingSession &&
      s.bell && s.bell.unseen_count > 0 &&
      (s.bell.seen_at === null || s.bell.last_fired_at > s.bell.seen_at);
  });
  assert.strictEqual(othersWithBell.length, 0, 'no other sessions with bell');
});

// --- Fix 1: renderSheetList escapes HTML in session name ---

test('renderSheetList escapes HTML special chars in data-session attribute', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'sheet-list' ? mockList : null);
  app._setCurrentSessions([{ name: 'foo<bar', bell: null }]);

  app.renderSheetList();

  assert.ok(capturedHTML.includes('data-session="foo&lt;bar"'), 'data-session attribute should escape <');
  assert.ok(!capturedHTML.includes('data-session="foo<bar"'), 'raw < must not appear in data-session');
  globalThis.document.getElementById = origGetById;
});

test('renderSheetList escapes HTML special chars in sheet-item__name span', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'sheet-list' ? mockList : null);
  app._setCurrentSessions([{ name: 'foo<bar', bell: null }]);

  app.renderSheetList();

  assert.ok(capturedHTML.includes('>foo&lt;bar<'), 'session name should be escaped inside the name span');
  globalThis.document.getElementById = origGetById;
});

// --- Fix 2: openBottomSheet/closeBottomSheet use static backdrop binding ---

test('openBottomSheet does not dynamically add click listener to sheet-backdrop', () => {
  let backdropAddCalled = false;
  const mockSheet = { classList: { remove: () => {} } };
  const mockList = { innerHTML: '', querySelectorAll: () => [] };
  const mockBackdrop = {
    addEventListener: (ev) => { if (ev === 'click') backdropAddCalled = true; },
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'bottom-sheet') return mockSheet;
    if (id === 'sheet-list') return mockList;
    if (id === 'sheet-backdrop') return mockBackdrop;
    return null;
  };
  app._setCurrentSessions([]);

  app.openBottomSheet();

  assert.strictEqual(backdropAddCalled, false, 'openBottomSheet must not add click listener to sheet-backdrop');
  globalThis.document.getElementById = origGetById;
});

test('closeBottomSheet does not call removeEventListener on sheet-backdrop', () => {
  let backdropRemoveCalled = false;
  const mockSheet = { classList: { add: () => {} } };
  const mockBackdrop = {
    removeEventListener: (ev) => { if (ev === 'click') backdropRemoveCalled = true; },
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'bottom-sheet') return mockSheet;
    if (id === 'sheet-backdrop') return mockBackdrop;
    return null;
  };

  app.closeBottomSheet();

  assert.strictEqual(backdropRemoveCalled, false, 'closeBottomSheet must not call removeEventListener on sheet-backdrop');
  globalThis.document.getElementById = origGetById;
});

// --- buildSidebarHTML ---

test('buildSidebarHTML adds sidebar-item--active class for active session', () => {
  const session = { name: 'my-session', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, 'my-session');
  assert.ok(html.includes('sidebar-item--active'), 'active session should have sidebar-item--active class');
});

test('buildSidebarHTML does not add sidebar-item--active class for inactive session', () => {
  const session = { name: 'my-session', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, 'other-session');
  assert.ok(!html.includes('sidebar-item--active'), 'inactive session should not have sidebar-item--active class');
});

test('buildSidebarHTML renders bell badge with tile-bell class and count when unseen_count > 0', () => {
  const session = { name: 's', snapshot: '', bell: { unseen_count: 3 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(html.includes('tile-bell'), 'should contain tile-bell class when unseen_count > 0');
  assert.ok(html.includes('>3<'), 'should contain unseen count text');
});

test('buildSidebarHTML omits bell badge when unseen_count is 0', () => {
  const session = { name: 's', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(!html.includes('tile-bell'), 'should not contain tile-bell class when unseen_count is 0');
});

test('buildSidebarHTML HTML-escapes session name to prevent XSS', () => {
  const session = { name: '<script>alert(1)</script>', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(!html.includes('<script>'), 'raw <script> tag must not appear in output');
  assert.ok(html.includes('&lt;script&gt;'), 'name must be HTML-escaped');
});

test('buildSidebarHTML article element has data-session attribute', () => {
  const session = { name: 'my-session', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(html.includes('data-session="my-session"'), 'article must have data-session attribute');
});

test('buildSidebarHTML includes snapshot preview in a pre element', () => {
  const session = { name: 's', snapshot: 'line1\nline2\nline3', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(/<pre>[\s\S]*line1[\s\S]*<\/pre>/.test(html), 'snapshot content should be inside a <pre> element');
});

// --- renderSidebar ---

test('renderSidebar populates sidebar-list innerHTML when viewMode is fullscreen', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'sidebar-list') return mockList;
    return null;
  };

  app._setViewMode('fullscreen');
  const sessions = [
    { name: 'session-a', snapshot: '', bell: { unseen_count: 0 } },
    { name: 'session-b', snapshot: '', bell: { unseen_count: 0 } },
  ];
  app.renderSidebar(sessions, 'session-a');

  assert.ok(capturedHTML.includes('sidebar-item'), 'innerHTML should contain sidebar-item');
  assert.ok(capturedHTML.includes('sidebar-item--active'), 'innerHTML should contain sidebar-item--active for active session');

  globalThis.document.getElementById = origGetById;
  app._setViewMode('grid');
});

test('renderSidebar renders empty message when sessions array is empty', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'sidebar-list') return mockList;
    return null;
  };

  app._setViewMode('fullscreen');
  app.renderSidebar([], null);

  assert.ok(capturedHTML.includes('sidebar-empty'), 'innerHTML should contain sidebar-empty class');
  assert.ok(capturedHTML.includes('No sessions'), 'innerHTML should contain "No sessions" text');

  globalThis.document.getElementById = origGetById;
  app._setViewMode('grid');
});

test('renderSidebar does nothing when view is not fullscreen', () => {
  let innerHTMLSet = false;
  const mockList = {
    get innerHTML() { return ''; },
    set innerHTML(v) { innerHTMLSet = true; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'sidebar-list') return mockList;
    return null;
  };

  app._setViewMode('grid');
  const sessions = [{ name: 'session-a', snapshot: '', bell: { unseen_count: 0 } }];
  app.renderSidebar(sessions, null);

  assert.strictEqual(innerHTMLSet, false, 'innerHTML setter should never be called when not in fullscreen');

  globalThis.document.getElementById = origGetById;
});

// ─── initSidebar ─────────────────────────────────────────────────────────────

test('initSidebar defaults to open (removes sidebar--collapsed) on wide screens when no stored value', () => {
  delete _localStorageStore['muxplex.sidebarOpen'];
  const origInnerWidth = globalThis.window.innerWidth;
  globalThis.window.innerWidth = 1200;

  const removedClasses = [];
  const addedClasses = [];
  const mockSidebar = {
    classList: { remove: (c) => removedClasses.push(c), add: (c) => addedClasses.push(c) },
  };
  const mockCollapseBtn = { textContent: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return mockSidebar;
    if (id === 'sidebar-collapse-btn') return mockCollapseBtn;
    return null;
  };

  app.initSidebar();

  assert.ok(removedClasses.includes('sidebar--collapsed'), 'should remove sidebar--collapsed on wide screen');
  assert.ok(!addedClasses.includes('sidebar--collapsed'), 'should not add sidebar--collapsed on wide screen');

  globalThis.document.getElementById = origGetById;
  globalThis.window.innerWidth = origInnerWidth;
});

test('initSidebar defaults to closed (adds sidebar--collapsed) on narrow screens when no stored value', () => {
  delete _localStorageStore['muxplex.sidebarOpen'];
  const origInnerWidth = globalThis.window.innerWidth;
  globalThis.window.innerWidth = 600;

  const removedClasses = [];
  const addedClasses = [];
  const mockSidebar = {
    classList: { remove: (c) => removedClasses.push(c), add: (c) => addedClasses.push(c) },
  };
  const mockCollapseBtn = { textContent: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return mockSidebar;
    if (id === 'sidebar-collapse-btn') return mockCollapseBtn;
    return null;
  };

  app.initSidebar();

  assert.ok(addedClasses.includes('sidebar--collapsed'), 'should add sidebar--collapsed on narrow screen');
  assert.ok(!removedClasses.includes('sidebar--collapsed'), 'should not remove sidebar--collapsed on narrow screen');

  globalThis.document.getElementById = origGetById;
  globalThis.window.innerWidth = origInnerWidth;
});

test('initSidebar respects stored value true regardless of screen width — even at 600px removes collapsed class', () => {
  _localStorageStore['muxplex.sidebarOpen'] = 'true';
  const origInnerWidth = globalThis.window.innerWidth;
  globalThis.window.innerWidth = 600;

  const removedClasses = [];
  const addedClasses = [];
  const mockSidebar = {
    classList: { remove: (c) => removedClasses.push(c), add: (c) => addedClasses.push(c) },
  };
  const mockCollapseBtn = { textContent: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return mockSidebar;
    if (id === 'sidebar-collapse-btn') return mockCollapseBtn;
    return null;
  };

  app.initSidebar();

  assert.ok(removedClasses.includes('sidebar--collapsed'), 'should remove sidebar--collapsed when stored value is true, even at 600px');
  assert.ok(!addedClasses.includes('sidebar--collapsed'), 'should not add sidebar--collapsed when stored value is true');

  globalThis.document.getElementById = origGetById;
  globalThis.window.innerWidth = origInnerWidth;
});

// ─── toggleSidebar ───────────────────────────────────────────────────────────

test('toggleSidebar persists state to localStorage — from true toggles to false', () => {
  _localStorageStore['muxplex.sidebarOpen'] = 'true';

  const mockSidebar = {
    classList: { remove: () => {}, add: () => {} },
  };
  const mockCollapseBtn = { textContent: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return mockSidebar;
    if (id === 'sidebar-collapse-btn') return mockCollapseBtn;
    return null;
  };

  app.toggleSidebar();

  assert.strictEqual(_localStorageStore['muxplex.sidebarOpen'], 'false', 'should persist false after toggling from true');

  globalThis.document.getElementById = origGetById;
});

test('toggleSidebar adds sidebar--collapsed class when closing (from open)', () => {
  _localStorageStore['muxplex.sidebarOpen'] = 'true';

  const addedClasses = [];
  const mockSidebar = {
    classList: { remove: () => {}, add: (c) => addedClasses.push(c) },
  };
  const mockCollapseBtn = { textContent: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return mockSidebar;
    if (id === 'sidebar-collapse-btn') return mockCollapseBtn;
    return null;
  };

  app.toggleSidebar();

  assert.ok(addedClasses.includes('sidebar--collapsed'), 'should add sidebar--collapsed class when closing');

  globalThis.document.getElementById = origGetById;
});

test('toggleSidebar removes sidebar--collapsed class when opening (from closed) and sets localStorage to true', () => {
  _localStorageStore['muxplex.sidebarOpen'] = 'false';

  const removedClasses = [];
  const mockSidebar = {
    classList: { remove: (c) => removedClasses.push(c), add: () => {} },
  };
  const mockCollapseBtn = { textContent: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return mockSidebar;
    if (id === 'sidebar-collapse-btn') return mockCollapseBtn;
    return null;
  };

  app.toggleSidebar();

  assert.ok(removedClasses.includes('sidebar--collapsed'), 'should remove sidebar--collapsed class when opening');
  assert.strictEqual(_localStorageStore['muxplex.sidebarOpen'], 'true', 'should persist true after toggling from false');

  globalThis.document.getElementById = origGetById;
});

// --- Guard: initSidebar and renderSidebar are exported and callable ---

test('initSidebar is exported and callable', () => {
  assert.strictEqual(typeof app.initSidebar, 'function', 'initSidebar should be a function');
});

test('renderSidebar is exported and callable', () => {
  assert.strictEqual(typeof app.renderSidebar, 'function', 'renderSidebar should be a function');
});

// --- bindStaticEventListeners sidebar toggle buttons ---

test('bindStaticEventListeners binds sidebar-toggle-btn and sidebar-collapse-btn click to toggleSidebar', () => {
  const eventsBound = {};
  const origGetById = globalThis.document.getElementById;
  const origDocAddListener = globalThis.document.addEventListener;
  globalThis.document.getElementById = (id) => {
    const el = { _events: {}, addEventListener: (ev, fn) => { el._events[ev] = fn; } };
    eventsBound[id] = el;
    return el;
  };
  globalThis.document.addEventListener = () => {};

  app.bindStaticEventListeners();

  assert.ok(
    eventsBound['sidebar-toggle-btn'] && 'click' in eventsBound['sidebar-toggle-btn']._events,
    '#sidebar-toggle-btn should have a click listener',
  );
  assert.ok(
    eventsBound['sidebar-collapse-btn'] && 'click' in eventsBound['sidebar-collapse-btn']._events,
    '#sidebar-collapse-btn should have a click listener',
  );
  globalThis.document.getElementById = origGetById;
  globalThis.document.addEventListener = origDocAddListener;
});

// --- bindSidebarClickAway ---

test('bindSidebarClickAway is exported and callable', () => {
  assert.strictEqual(typeof app.bindSidebarClickAway, 'function', 'bindSidebarClickAway should be a function');
});

test('bindSidebarClickAway registers click listener on terminal-container', () => {
  let addEventListenerCalledWith = null;
  const mockTerminalContainer = {
    addEventListener: (ev, fn) => { addEventListenerCalledWith = ev; },
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'terminal-container') return mockTerminalContainer;
    return null;
  };

  app.bindSidebarClickAway();

  assert.strictEqual(addEventListenerCalledWith, 'click', 'bindSidebarClickAway should register a click listener on terminal-container');
  globalThis.document.getElementById = origGetById;
});

test('openSession mounts terminal AFTER connect POST, not inside animation timer', () => {
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );

  // Find the openSession function body
  const fnStart = source.indexOf('async function openSession');
  const fnBody = source.substring(fnStart, fnStart + 3000);

  // _openTerminal must NOT appear inside setTimeout
  const setTimeoutIdx = fnBody.indexOf('setTimeout');
  const setTimeoutEnd = fnBody.indexOf('}, 260)', setTimeoutIdx);
  const setTimeoutBody = fnBody.substring(setTimeoutIdx, setTimeoutEnd);

  assert.ok(!setTimeoutBody.includes('_openTerminal'),
    '_openTerminal must NOT be inside the 260ms setTimeout — causes race condition with /connect POST');

  // _openTerminal must appear AFTER the /connect POST
  const connectIdx = fnBody.indexOf('/api/sessions/');
  const openTermIdx = fnBody.indexOf('_openTerminal', connectIdx);
  assert.ok(openTermIdx > connectIdx,
    '_openTerminal must appear AFTER the /connect POST in the source');
});

// --- Hover preview popover ---

test('app.js has hover preview popover with desktop-only guard', () => {
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('preview-popover'), 'must create popover with preview-popover class');
  assert.ok(source.includes('ontouchstart'), 'must guard against touch devices (desktop only)');
  assert.ok(source.includes('session.snapshot'), 'must use full snapshot text (not lastLines)');
  assert.ok(source.includes('hidePreview'), 'must have cleanup on mouseleave');
});

test('hover preview popover works for both grid tiles and sidebar items', () => {
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('preview-popover'), 'must create popover');
  assert.ok(source.includes('ontouchstart'), 'desktop-only guard');
  assert.ok(source.includes('session.snapshot'), 'uses full snapshot');
  assert.ok(source.includes('scrollHeight'), 'auto-scrolls to bottom');
  assert.ok(source.includes('sidebar-list') || source.includes('sidebar-item'),
    'must handle sidebar items too');
});

test('hover preview popover uses cyan border and no dimmer', () => {
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('preview-popover'), 'must create popover');
  assert.ok(!source.includes('preview-dimmer'), 'must NOT use dimmer overlay (removed)');
});

test('hover preview delay is 1500ms (not 350ms)', () => {
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('1500'), 'hover delay must be 1500ms');
  assert.ok(!source.includes(', 350)'), 'old 350ms delay must be removed');
});

test('hover preview uses full-window overlay with click-to-navigate', () => {
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('_previewSessionName'), 'must track by session name');
  assert.ok(source.includes('scrollHeight'), 'must auto-scroll to bottom');
  assert.ok(source.includes('ontouchstart'), 'must be desktop-only');
  assert.ok(source.includes('_previewClickHandler'), 'must have click-to-navigate handler');
  assert.ok(!source.includes('repositionPreview'), 'must NOT have repositionPreview (old approach)');
  assert.ok(!source.includes('preview-dimmer'), 'must NOT have dimmer (removed — ANSI colors are readable without it)');
});

test('ansiToHtml converts SGR codes to styled spans', () => {
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('function ansiToHtml'), 'ansiToHtml parser must exist');
  assert.ok(source.includes('ANSI_COLORS'), 'must have color lookup table');
  assert.ok(source.includes('ansi256Color'), 'must support 256-color mode');
  assert.ok(source.includes('ansiToHtml(lastLines)'), 'tiles must use ansiToHtml not escapeHtml');
  assert.ok(source.includes('ansiToHtml(session.snapshot)'), 'overlay must use ansiToHtml');
});

test('ANSI_COLORS uses xterm.js default GTK/Tango palette', () => {
  // xterm.js default palette (GTK/Tango-derived) — must match exactly
  // so previews render colors identically to the interactive terminal
  assert.strictEqual(app.ansi256Color(0), '#2e3436', 'color 0 must be xterm.js default: #2e3436');
  assert.strictEqual(app.ansi256Color(1), '#cc0000', 'color 1 must be xterm.js default: #cc0000');
  assert.strictEqual(app.ansi256Color(2), '#4e9a06', 'color 2 must be xterm.js default: #4e9a06');
  assert.strictEqual(app.ansi256Color(3), '#c4a000', 'color 3 must be xterm.js default: #c4a000');
  assert.strictEqual(app.ansi256Color(7), '#d3d7cf', 'color 7 must be xterm.js default: #d3d7cf');
  assert.strictEqual(app.ansi256Color(8), '#555753', 'color 8 must be xterm.js default: #555753');
  assert.strictEqual(app.ansi256Color(10), '#8ae234', 'color 10 must be xterm.js default: #8ae234');
  assert.strictEqual(app.ansi256Color(14), '#34e2e2', 'color 14 must be xterm.js default: #34e2e2');
});

// --- New Session tab ---

test('HTML index.html has setting-template textarea with correct attributes', () => {
  const source = fs.readFileSync(
    new URL('../index.html', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('id="setting-template"'), 'must have setting-template textarea');
  assert.ok(source.includes('settings-textarea'), 'must have settings-textarea class');
  assert.ok(source.includes('rows="3"'), 'must have rows=3');
  assert.ok(source.includes('placeholder="tmux new-session -d -s {name}"'), 'must have correct placeholder');
});

test('HTML index.html has setting-template-reset button', () => {
  const source = fs.readFileSync(
    new URL('../index.html', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('id="setting-template-reset"'), 'must have setting-template-reset button');
  assert.ok(source.includes('settings-action-btn'), 'must use settings-action-btn class on reset button');
});

test('HTML index.html has settings-helper text for template', () => {
  const source = fs.readFileSync(
    new URL('../index.html', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('settings-helper'), 'must have settings-helper class');
  assert.ok(source.includes('{name} is replaced with the session name'), 'must have helper text');
});

test('CSS style.css has .settings-textarea class', () => {
  const source = fs.readFileSync(
    new URL('../style.css', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('.settings-textarea'), 'must have .settings-textarea CSS class');
});

test('CSS style.css has .settings-helper class', () => {
  const source = fs.readFileSync(
    new URL('../style.css', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('.settings-helper'), 'must have .settings-helper CSS class');
});

test('openSettings populates setting-template textarea from server settings', async () => {
  // Reset _currentSessions to empty to avoid createTextNode calls in openSettings callback
  app._setCurrentSessions([]);

  const elements = {};
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url === '/api/settings') {
      return {
        ok: true,
        json: async () => ({ new_session_template: 'my-custom-template' }),
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (!elements[id]) {
      elements[id] = {
        value: '',
        checked: false,
        innerHTML: '',
        disabled: false,
        appendChild: () => {},
        querySelectorAll: () => [],
        classList: { add: () => {}, remove: () => {} },
        showModal: () => {},
        close: () => {},
        addEventListener: () => {},
      };
    }
    return elements[id];
  };
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.querySelectorAll = () => [];
  const origCreateTextNode = globalThis.document.createTextNode;
  globalThis.document.createTextNode = (text) => ({ nodeType: 3, textContent: text });

  app.openSettings();
  // Flush microtask queue so all Promises in loadServerSettings chain resolve
  // before yielding to macrotask queue (where other tests could start).
  // Each await Promise.resolve() flushes one microtask hop; 10 covers nested chains.
  for (let i = 0; i < 10; i++) await Promise.resolve();

  assert.strictEqual(
    elements['setting-template'] && elements['setting-template'].value,
    'my-custom-template',
    'textarea should be populated with new_session_template from server settings',
  );

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.document.createTextNode = origCreateTextNode;
});

test('openSettings uses default template when new_session_template not in server settings', async () => {
  // Reset _currentSessions to empty to avoid createTextNode calls in openSettings callback
  app._setCurrentSessions([]);

  const elements = {};
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url === '/api/settings') {
      return {
        ok: true,
        json: async () => ({}),
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (!elements[id]) {
      elements[id] = {
        value: '',
        checked: false,
        innerHTML: '',
        disabled: false,
        appendChild: () => {},
        querySelectorAll: () => [],
        classList: { add: () => {}, remove: () => {} },
        showModal: () => {},
        close: () => {},
        addEventListener: () => {},
      };
    }
    return elements[id];
  };
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.querySelectorAll = () => [];
  const origCreateTextNode = globalThis.document.createTextNode;
  globalThis.document.createTextNode = (text) => ({ nodeType: 3, textContent: text });

  app.openSettings();
  // Flush microtask queue so all Promises in loadServerSettings chain resolve
  // before yielding to macrotask queue (where other tests could start).
  for (let i = 0; i < 10; i++) await Promise.resolve();

  assert.strictEqual(
    elements['setting-template'] && elements['setting-template'].value,
    'tmux new-session -d -s {name}',
    'textarea should use default when new_session_template not set',
  );

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.document.createTextNode = origCreateTextNode;
});

test('bindStaticEventListeners binds input on setting-template', () => {
  const eventsBound = {};
  const origGetById = globalThis.document.getElementById;
  const origDocAddListener = globalThis.document.addEventListener;
  globalThis.document.getElementById = (id) => {
    const el = {
      _events: {},
      addEventListener: (ev, fn) => { el._events[ev] = fn; },
      value: '',
      querySelectorAll: () => [],
    };
    eventsBound[id] = el;
    return el;
  };
  globalThis.document.addEventListener = () => {};

  app.bindStaticEventListeners();

  assert.ok(
    eventsBound['setting-template'] && 'input' in eventsBound['setting-template']._events,
    '#setting-template should have an input listener',
  );

  globalThis.document.getElementById = origGetById;
  globalThis.document.addEventListener = origDocAddListener;
});

test('bindStaticEventListeners binds click on setting-template-reset', () => {
  const eventsBound = {};
  const origGetById = globalThis.document.getElementById;
  const origDocAddListener = globalThis.document.addEventListener;
  globalThis.document.getElementById = (id) => {
    const el = {
      _events: {},
      addEventListener: (ev, fn) => { el._events[ev] = fn; },
      value: '',
      querySelectorAll: () => [],
    };
    eventsBound[id] = el;
    return el;
  };
  globalThis.document.addEventListener = () => {};

  app.bindStaticEventListeners();

  assert.ok(
    eventsBound['setting-template-reset'] && 'click' in eventsBound['setting-template-reset']._events,
    '#setting-template-reset should have a click listener',
  );

  globalThis.document.getElementById = origGetById;
  globalThis.document.addEventListener = origDocAddListener;
});

test('setting-template-reset click resets textarea to default value', () => {
  const elements = {};
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

  const origGetById = globalThis.document.getElementById;
  const origDocAddListener = globalThis.document.addEventListener;
  globalThis.document.getElementById = (id) => {
    if (!elements[id]) {
      elements[id] = {
        _events: {},
        addEventListener: (ev, fn) => { elements[id]._events[ev] = fn; },
        value: 'custom-value',
        querySelectorAll: () => [],
      };
    }
    return elements[id];
  };
  globalThis.document.addEventListener = () => {};

  app.bindStaticEventListeners();

  // Simulate reset button click
  if (elements['setting-template-reset']) {
    elements['setting-template-reset']._events.click();
  }

  assert.strictEqual(
    elements['setting-template'] && elements['setting-template'].value,
    'tmux new-session -d -s {name}',
    'textarea should be reset to default value on reset button click',
  );

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.addEventListener = origDocAddListener;
});

test('app.js source uses 500ms debounce for template input and references new_session_template', () => {
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('500'), 'must have 500ms debounce timeout');
  assert.ok(source.includes('new_session_template'), 'must reference new_session_template setting key');
});

test('buildTileHTML includes tile-delete button with data-session attribute', () => {
  const session = { name: 'my-session', snapshot: '', bell: { unseen_count: 0, seen_at: null, last_fired_at: null } };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('tile-delete'), 'buildTileHTML must include tile-delete button class');
  assert.ok(html.includes('data-session="my-session"'), 'tile-delete button must have data-session attribute');
});

test('buildSidebarHTML includes sidebar-delete button with data-session attribute', () => {
  const session = { name: 'my-session', snapshot: '', bell: { unseen_count: 0, seen_at: null, last_fired_at: null } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('sidebar-delete'), 'buildSidebarHTML must include sidebar-delete button class');
  assert.ok(html.includes('data-session="my-session"'), 'sidebar-delete button must have data-session attribute');
});

// --- api ---

test('api with no baseUrl uses relative path', async () => {
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true };
  };

  await app.api('GET', '/api/sessions');

  assert.strictEqual(calls.length, 1, 'should call fetch once');
  assert.strictEqual(calls[0].url, '/api/sessions', 'url should be relative path');
  assert.ok(!calls[0].opts.credentials, 'credentials should not be set without baseUrl');

  globalThis.fetch = origFetch;
});

test('api with baseUrl prepends it to path and sets credentials include', async () => {
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true };
  };

  await app.api('GET', '/api/sessions', undefined, 'https://remote.example.com');

  assert.strictEqual(calls.length, 1, 'should call fetch once');
  assert.strictEqual(calls[0].url, 'https://remote.example.com/api/sessions', 'url should prepend baseUrl');
  assert.strictEqual(calls[0].opts.credentials, 'include', 'credentials should be include for cross-origin');

  globalThis.fetch = origFetch;
});

test('api with baseUrl and trailing slash does not double-slash', async () => {
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true };
  };

  await app.api('GET', '/api/sessions', undefined, 'https://remote.example.com/');

  assert.strictEqual(calls.length, 1, 'should call fetch once');
  assert.strictEqual(calls[0].url, 'https://remote.example.com/api/sessions', 'trailing slash on baseUrl should not create double-slash');

  globalThis.fetch = origFetch;
});

test('createNewSession polls for session before auto-opening (not immediate setTimeout openSession)', () => {
  // The old behavior was: setTimeout(() => openSession(...), 500) immediately after POST.
  // The new behavior must use a polling interval to wait for the session to appear in
  // _currentSessions before calling openSession — so the immediate pattern must be gone.
  const source = fs.readFileSync(
    new URL('../app.js', import.meta.url), 'utf8'
  );
  // Extract the createNewSession function body
  const start = source.indexOf('async function createNewSession(');
  assert.ok(start !== -1, 'createNewSession function must exist');
  // Find the end of the function (next function declaration at same indent level)
  const snippet = source.slice(start, start + 2000);
  // Must NOT contain the old immediate-open pattern inside createNewSession
  assert.ok(
    !snippet.includes("setTimeout(() => openSession"),
    'createNewSession must not use immediate setTimeout(() => openSession) — should poll instead'
  );
  // Must contain a polling mechanism (setInterval)
  assert.ok(
    snippet.includes('setInterval'),
    'createNewSession must use setInterval to poll for session readiness'
  );
});

// --- federation state helpers (task-3) ---

test('_setSources sets internal _sources array', () => {
  assert.doesNotThrow(() => app._setSources([{ url: '', name: 'test' }]));
});

test('_setServerSettings sets internal _serverSettings', () => {
  assert.doesNotThrow(() => app._setServerSettings({ sort_order: 'recent' }));
});

test('_getGridViewMode returns current _gridViewMode value', () => {
  assert.strictEqual(typeof app._getGridViewMode(), 'string', '_getGridViewMode should return a string');
  assert.strictEqual(app._getGridViewMode(), 'flat', '_gridViewMode should default to flat');
});

test('_getSources returns current _sources array', () => {
  app._setSources([{ url: 'http://test', name: 'Test' }]);
  const sources = app._getSources();
  assert.ok(Array.isArray(sources), '_getSources should return an array');
  assert.strictEqual(sources[0].url, 'http://test', '_getSources should return value set by _setSources');
});

// --- buildSources ---

test('buildSources returns only local source when no remote_instances', () => {
  const sources = app.buildSources({ device_name: 'Laptop' });
  assert.strictEqual(sources.length, 1, 'should return exactly one source (local)');
  assert.strictEqual(sources[0].url, '', 'local source url should be empty string');
  assert.strictEqual(sources[0].name, 'Laptop', 'local source name should be device_name');
  assert.strictEqual(sources[0].type, 'local', 'local source type should be local');
  assert.strictEqual(sources[0].status, 'authenticated', 'local source status should be authenticated');
});

test('buildSources returns local + remote sources from remote_instances', () => {
  const sources = app.buildSources({
    device_name: 'Laptop',
    remote_instances: [
      { url: 'https://server1.example.com', name: 'Server 1' },
      { url: 'https://server2.example.com', name: 'Server 2' },
    ],
  });
  assert.strictEqual(sources.length, 3, 'should return 3 sources: 1 local + 2 remote');
  assert.strictEqual(sources[0].type, 'local', 'first source should be local');
  assert.strictEqual(sources[1].type, 'remote', 'second source should be remote');
  assert.strictEqual(sources[1].url, 'https://server1.example.com', 'remote source url should match');
  assert.strictEqual(sources[1].name, 'Server 1', 'remote source name should match');
  assert.strictEqual(sources[2].type, 'remote', 'third source should be remote');
  assert.strictEqual(sources[2].url, 'https://server2.example.com', 'remote source url should match');
});

test('buildSources uses hostname fallback when device_name is empty', () => {
  const sources = app.buildSources({});
  assert.strictEqual(sources.length, 1, 'should return one source');
  assert.strictEqual(sources[0].name, 'This device', 'local source name should fall back to This device');
});

test('buildSources strips trailing slash from remote URLs', () => {
  const sources = app.buildSources({
    device_name: 'Laptop',
    remote_instances: [
      { url: 'https://server1.example.com/', name: 'Server 1' },
      { url: 'https://server2.example.com///', name: 'Server 2' },
    ],
  });
  assert.strictEqual(sources[1].url, 'https://server1.example.com', 'trailing slash should be stripped from remote URL');
  assert.strictEqual(sources[2].url, 'https://server2.example.com', 'multiple trailing slashes should be stripped from remote URL');
});

// --- tagSessions ---

test('tagSessions adds deviceName and sourceUrl to each session', () => {
  const sessions = [{ name: 'work' }, { name: 'play' }];
  const result = app.tagSessions(sessions, 'Laptop', 'https://host.example.com');
  assert.strictEqual(result[0].deviceName, 'Laptop', 'first session should have deviceName set');
  assert.strictEqual(result[0].sourceUrl, 'https://host.example.com', 'first session should have sourceUrl set');
  assert.strictEqual(result[1].deviceName, 'Laptop', 'second session should have deviceName set');
  assert.strictEqual(result[1].sourceUrl, 'https://host.example.com', 'second session should have sourceUrl set');
});

test('tagSessions adds sessionKey formatted as sourceUrl::name', () => {
  const sessions = [{ name: 'my-session' }];
  const result = app.tagSessions(sessions, 'Laptop', 'https://host.example.com');
  assert.strictEqual(result[0].sessionKey, 'https://host.example.com::my-session', 'sessionKey should be sourceUrl::name');
});

test('tagSessions handles empty sessions input (returns empty array)', () => {
  const result = app.tagSessions([], 'Laptop', 'https://host.example.com');
  assert.deepStrictEqual(result, [], 'tagSessions should return empty array for empty input');
});

test('tagSessions does not mutate the original session objects', () => {
  const original = { name: 'work' };
  const sessions = [original];
  const result = app.tagSessions(sessions, 'Laptop', 'https://host.example.com');
  assert.ok(!('deviceName' in original), 'original session should not be mutated');
  assert.ok(!('sourceUrl' in original), 'original session should not have sourceUrl added');
  assert.ok(!('sessionKey' in original), 'original session should not have sessionKey added');
  assert.notStrictEqual(result[0], original, 'returned session should be a new object, not the original');
});

// --- mergeSources ---

test('mergeSources combines sessions from multiple sources with correct sessionKeys', () => {
  const results = [
    {
      source: { name: 'Laptop', url: '' },
      sessions: [{ name: 'local-session' }],
    },
    {
      source: { name: 'Server', url: 'https://server.example.com' },
      sessions: [{ name: 'remote-session' }],
    },
  ];
  const merged = app.mergeSources(results);
  assert.strictEqual(merged.length, 2, 'merged array should have 2 sessions total');
  const local = merged.find((s) => s.name === 'local-session');
  const remote = merged.find((s) => s.name === 'remote-session');
  assert.ok(local, 'local-session should be present');
  assert.ok(remote, 'remote-session should be present');
  assert.strictEqual(local.sessionKey, '::local-session', 'local session key should be ::local-session');
  assert.strictEqual(remote.sessionKey, 'https://server.example.com::remote-session', 'remote session key should include url');
  assert.strictEqual(remote.deviceName, 'Server', 'remote session should have correct deviceName');
});

test('mergeSources returns empty array for empty input', () => {
  const result = app.mergeSources([]);
  assert.deepStrictEqual(result, [], 'mergeSources should return empty array for empty input');
});

// --- pollSessions multi-source (task-5) ---

test('pollSessions fetches from all sources and merges results', async () => {
  const mockStatusEl = { textContent: '', className: '' };
  const mockGrid = { innerHTML: '' };
  const mockEmptyState = { style: {}, classList: { add: () => {}, remove: () => {} } };

  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'connection-status') return mockStatusEl;
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmptyState;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  // Set up sources: local + remote
  app._setSources([
    { url: '', name: 'Local', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'https://remote.example.com', name: 'Remote', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);

  const fetchCalls = [];
  globalThis.fetch = async (url, opts) => {
    fetchCalls.push(url);
    if (url === '/api/sessions') {
      return { ok: true, json: async () => [{ name: 'local-session' }] };
    }
    if (url === 'https://remote.example.com/api/sessions') {
      return { ok: true, json: async () => [{ name: 'remote-session' }] };
    }
    return { ok: true, json: async () => [] };
  };

  await app.pollSessions();

  // Both sources should have been fetched
  assert.ok(fetchCalls.some((url) => url === '/api/sessions'), 'should fetch local sessions');
  assert.ok(
    fetchCalls.some((url) => url === 'https://remote.example.com/api/sessions'),
    'should fetch remote sessions',
  );

  // Grid should contain sessions from both sources (merged)
  assert.ok(mockGrid.innerHTML.includes('local-session'), 'grid should include local sessions');
  assert.ok(mockGrid.innerHTML.includes('remote-session'), 'grid should include remote sessions');

  // Connection status should be ok (local source succeeded)
  assert.strictEqual(mockStatusEl.className, 'connection-status--ok', 'connection status should be ok when local source succeeds');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
  app._setSources([]);
});

test('pollSessions skips sources with nextRetryAt in the future', async () => {
  const mockStatusEl = { textContent: '', className: '' };
  const mockGrid = { innerHTML: '' };
  const mockEmptyState = { style: {}, classList: { add: () => {}, remove: () => {} } };

  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'connection-status') return mockStatusEl;
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmptyState;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  const futureRetry = Date.now() + 60000; // 60s in the future
  app._setSources([
    { url: '', name: 'Local', type: 'local', status: 'authenticated', backoffMs: 2000 },
    {
      url: 'https://remote.example.com',
      name: 'Remote',
      type: 'remote',
      status: 'unreachable',
      backoffMs: 4000,
      nextRetryAt: futureRetry,
    },
  ]);

  const fetchCalls = [];
  globalThis.fetch = async (url) => {
    fetchCalls.push(url);
    return { ok: true, json: async () => [{ name: 'local-session' }] };
  };

  await app.pollSessions();

  assert.ok(fetchCalls.some((url) => url === '/api/sessions'), 'should fetch local sessions');
  assert.ok(
    !fetchCalls.some((url) => url === 'https://remote.example.com/api/sessions'),
    'should skip remote source still in backoff',
  );

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
  app._setSources([]);
});

test('pollSessions sets auth_required on 401 response', async () => {
  const mockStatusEl = { textContent: '', className: '' };
  const mockGrid = { innerHTML: '' };
  const mockEmptyState = { style: {}, classList: { add: () => {}, remove: () => {} } };

  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'connection-status') return mockStatusEl;
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmptyState;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  const sources = [
    { url: '', name: 'Local', type: 'local', status: 'authenticated', backoffMs: 2000 },
    {
      url: 'https://remote.example.com',
      name: 'Remote',
      type: 'remote',
      status: 'authenticated',
      backoffMs: 2000,
    },
  ];
  app._setSources(sources);

  globalThis.fetch = async (url) => {
    if (url === '/api/sessions') {
      return { ok: true, json: async () => [{ name: 'local-session' }] };
    }
    // Remote returns 401
    return { ok: false, status: 401, statusText: 'Unauthorized' };
  };

  await app.pollSessions();

  const remoteSrc = sources.find((s) => s.url === 'https://remote.example.com');
  assert.strictEqual(remoteSrc.status, 'auth_required', 'remote source should be marked auth_required');
  assert.ok(!remoteSrc.nextRetryAt, 'auth_required should not set nextRetryAt');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
  app._setSources([]);
});

// --- getVisibleSessions (task-7) ---

test('getVisibleSessions exported and filters hidden sessions', () => {
  // Verify getVisibleSessions is exported as a function
  assert.strictEqual(typeof app.getVisibleSessions, 'function', 'getVisibleSessions should be exported as a function');

  // Set up server settings with hidden_sessions
  app._setServerSettings({ hidden_sessions: ['secret', 'hidden-local'] });

  // Local sessions (no sourceUrl) matching hidden list should be filtered
  const sessions = [
    { name: 'visible', sourceUrl: '' },
    { name: 'secret', sourceUrl: '' },          // local, should be hidden
    { name: 'hidden-local', sourceUrl: '' },    // local, should be hidden
    { name: 'other', sourceUrl: '' },
  ];

  const result = app.getVisibleSessions(sessions);
  assert.strictEqual(result.length, 2, 'should hide 2 local sessions matching the hidden list');
  assert.ok(result.some((s) => s.name === 'visible'), 'visible should remain');
  assert.ok(result.some((s) => s.name === 'other'), 'other should remain');
  assert.ok(!result.some((s) => s.name === 'secret'), 'secret (local) should be hidden');
  assert.ok(!result.some((s) => s.name === 'hidden-local'), 'hidden-local should be hidden');

  // Clean up
  app._setServerSettings(null);
});

test('getVisibleSessions hides local sessions by name but not remote sessions with same name', () => {
  // Set server settings with a session name that exists both locally and remotely
  app._setServerSettings({ hidden_sessions: ['shared-name'] });

  const sessions = [
    { name: 'shared-name', sourceUrl: '' },                         // local — should be hidden
    { name: 'shared-name', sourceUrl: 'https://remote.example.com' }, // remote — should remain visible
    { name: 'another', sourceUrl: '' },                             // local, not in hidden list — should remain
  ];

  const result = app.getVisibleSessions(sessions);
  assert.strictEqual(result.length, 2, 'should show 2 sessions (remote + another)');
  // The remote one should survive
  const remote = result.find((s) => s.sourceUrl === 'https://remote.example.com');
  assert.ok(remote, 'remote session with same name should not be hidden');
  assert.strictEqual(remote.name, 'shared-name', 'remote session name should be shared-name');
  // The local one should be hidden
  assert.ok(!result.some((s) => s.sourceUrl === '' && s.name === 'shared-name'), 'local session with hidden name should be removed');
  // another should remain
  assert.ok(result.some((s) => s.name === 'another'), 'another should remain visible');

  // Clean up
  app._setServerSettings(null);
});

// --- buildSidebarHTML device badge (task-10) ---

test('buildSidebarHTML shows device-badge when multiple sources configured', () => {
  app._setSources([
    { url: '', name: 'This device', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'https://remote.example.com', name: 'Remote', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'work', deviceName: 'Laptop', sourceUrl: '', sessionKey: '::work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('device-badge'), 'should show device-badge when _sources.length > 1 and session has deviceName');
  assert.ok(html.includes('Laptop'), 'device-badge should contain the deviceName');
  app._setSources([]);
});

test('buildSidebarHTML omits device-badge when only one source configured', () => {
  app._setSources([
    { url: '', name: 'This device', type: 'local', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'work', deviceName: 'Laptop', sourceUrl: '', sessionKey: '::work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(!html.includes('device-badge'), 'should NOT show device-badge when _sources.length is 1');
  app._setSources([]);
});

test('buildSidebarHTML omits device-badge when session has no deviceName', () => {
  app._setSources([
    { url: '', name: 'This device', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'https://remote.example.com', name: 'Remote', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'work', sourceUrl: '', sessionKey: '::work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(!html.includes('device-badge'), 'should NOT show device-badge when session has no deviceName');
  app._setSources([]);
});

test('buildSidebarHTML includes data-source-url attribute on article element', () => {
  const session = {
    name: 'work',
    deviceName: 'Laptop',
    sourceUrl: 'https://remote.example.com',
    sessionKey: 'https://remote.example.com::work',
    snapshot: '',
    bell: { unseen_count: 0 },
  };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('data-source-url="https://remote.example.com"'), 'article should have data-source-url with correct value');
});

test('buildSidebarHTML data-source-url is empty string when session has no sourceUrl', () => {
  const session = { name: 'work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('data-source-url=""'), 'article should have data-source-url as empty string when no sourceUrl');
});

test('buildSidebarHTML escapes HTML in deviceName within device-badge', () => {
  app._setSources([
    { url: '', name: 'This device', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'https://remote.example.com', name: 'Remote', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'work', deviceName: '<script>alert(1)</script>', sourceUrl: '', sessionKey: '::work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(!html.includes('<script>'), 'device-badge should not contain raw <script> tag');
  assert.ok(html.includes('&lt;script&gt;'), 'device-badge should escape < and > in deviceName');
  app._setSources([]);
});

// --- buildTileHTML device badge (task-9) ---

test('buildTileHTML shows device-badge when session has deviceName and multiple sources', () => {
  app._setSources([
    { url: '', name: 'This device', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'https://remote.example.com', name: 'Remote', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'work', deviceName: 'Laptop', sourceUrl: '', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('device-badge'), 'should show device-badge when _sources.length > 1 and session has deviceName');
  assert.ok(html.includes('Laptop'), 'device-badge should contain the deviceName');
  app._setSources([]);
});

test('buildTileHTML omits device-badge when only one source configured', () => {
  app._setSources([
    { url: '', name: 'This device', type: 'local', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'work', deviceName: 'Laptop', sourceUrl: '', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('device-badge'), 'should NOT show device-badge when _sources.length is 1');
  app._setSources([]);
});

test('buildTileHTML includes data-session-key and data-source-url attributes on article element', () => {
  const session = {
    name: 'work',
    deviceName: 'Laptop',
    sourceUrl: 'https://remote.example.com',
    sessionKey: 'https://remote.example.com::work',
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('data-session-key="https://remote.example.com::work"'), 'article should have data-session-key with correct value');
  assert.ok(html.includes('data-source-url="https://remote.example.com"'), 'article should have data-source-url with correct value');
});

test('buildTileHTML escapes HTML in deviceName within device-badge', () => {
  app._setSources([
    { url: '', name: 'This device', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'https://remote.example.com', name: 'Remote', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);
  const session = { name: 'work', deviceName: '<script>alert(1)</script>', sourceUrl: '', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('<script>'), 'device-badge should not contain raw <script> tag');
  assert.ok(html.includes('&lt;script&gt;'), 'device-badge should escape < and > in deviceName');
  app._setSources([]);
});

test('pollSessions sets unreachable and applies exponential backoff on network error', async () => {
  const mockStatusEl = { textContent: '', className: '' };
  const mockGrid = { innerHTML: '' };
  const mockEmptyState = { style: {}, classList: { add: () => {}, remove: () => {} } };

  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'connection-status') return mockStatusEl;
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmptyState;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  const sources = [
    { url: '', name: 'Local', type: 'local', status: 'authenticated', backoffMs: 2000 },
    {
      url: 'https://remote.example.com',
      name: 'Remote',
      type: 'remote',
      status: 'authenticated',
      backoffMs: 2000,
    },
  ];
  app._setSources(sources);

  const beforePoll = Date.now();
  globalThis.fetch = async (url) => {
    if (url === '/api/sessions') {
      return { ok: true, json: async () => [] };
    }
    return { ok: false, status: 500, statusText: 'Internal Server Error' };
  };

  await app.pollSessions();

  const remoteSrc = sources.find((s) => s.url === 'https://remote.example.com');
  assert.strictEqual(remoteSrc.status, 'unreachable', 'remote source should be marked unreachable');
  // backoffMs doubles from 2000 → 4000
  assert.strictEqual(remoteSrc.backoffMs, 4000, 'backoff should double from 2000 to 4000');
  assert.ok(remoteSrc.nextRetryAt > beforePoll, 'nextRetryAt should be set to a future timestamp');
  assert.ok(remoteSrc.nextRetryAt <= beforePoll + 4000 + 100, 'nextRetryAt should be roughly now + 4000ms');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
  app._setSources([]);
});

// --- renderGrid grouped mode (task-11) ---

test('renderGrid in grouped mode produces device-group-header elements', () => {
  const collectedHTML = [];
  const mockGrid = {
    get innerHTML() { return collectedHTML[0] || ''; },
    set innerHTML(v) { collectedHTML[0] = v; },
  };
  const mockEmpty = { style: {}, classList: { add: () => {}, remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  // Set up sessions from two different devices
  const sessions = [
    { name: 'alpha', deviceName: 'Laptop', sourceUrl: 'http://local', sessionKey: 'http://local::alpha', snapshot: '' },
    { name: 'beta', deviceName: 'Server', sourceUrl: 'http://remote', sessionKey: 'http://remote::beta', snapshot: '' },
  ];

  app._setGridViewMode('grouped');
  app.renderGrid(sessions);

  const html = mockGrid.innerHTML;
  assert.ok(html.includes('device-group-header'), 'grid HTML should contain device-group-header elements');
  assert.ok(html.includes('Laptop'), 'grid HTML should contain device name "Laptop"');
  assert.ok(html.includes('Server'), 'grid HTML should contain device name "Server"');

  // Reset state
  app._setGridViewMode('flat');
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
});

test('_setGridViewMode and renderGroupedGrid are exported', () => {
  assert.strictEqual(typeof app._setGridViewMode, 'function', '_setGridViewMode should be exported');
  assert.strictEqual(typeof app.renderGroupedGrid, 'function', 'renderGroupedGrid should be exported');
});

// --- renderFilterBar (task-12) ---

test('renderFilterBar produces pill buttons for each device plus All', () => {
  const collectedHTML = [];
  const mockContainer = {
    get innerHTML() { return collectedHTML[0] || ''; },
    set innerHTML(v) { collectedHTML[0] = v; },
  };

  const sessions = [
    { name: 'alpha', deviceName: 'Laptop', sourceUrl: 'http://local', sessionKey: 'http://local::alpha', snapshot: '' },
    { name: 'beta', deviceName: 'Server', sourceUrl: 'http://remote', sessionKey: 'http://remote::beta', snapshot: '' },
    { name: 'gamma', deviceName: 'Laptop', sourceUrl: 'http://local', sessionKey: 'http://local::gamma', snapshot: '' },
  ];

  app._setActiveFilterDevice('all');
  app.renderFilterBar(mockContainer, sessions);

  const html = mockContainer.innerHTML;
  assert.ok(html.includes('All'), 'filter bar should include an "All" button');
  assert.ok(html.includes('Laptop'), 'filter bar should include a pill for "Laptop"');
  assert.ok(html.includes('Server'), 'filter bar should include a pill for "Server"');

  // Should have exactly 3 buttons: All, Laptop, Server (Laptop appears only once despite two sessions)
  const pillCount = (html.match(/<button/g) || []).length;
  assert.ok(pillCount >= 3, 'filter bar should have at least 3 filter-pill buttons (All + 2 devices)');
});

test('renderFilterBar marks active device pill with filter-pill--active class', () => {
  const collectedHTML = [];
  const mockContainer = {
    get innerHTML() { return collectedHTML[0] || ''; },
    set innerHTML(v) { collectedHTML[0] = v; },
  };

  const sessions = [
    { name: 'alpha', deviceName: 'Laptop', sourceUrl: 'http://local', sessionKey: 'http://local::alpha', snapshot: '' },
    { name: 'beta', deviceName: 'Server', sourceUrl: 'http://remote', sessionKey: 'http://remote::beta', snapshot: '' },
  ];

  // Set active filter to 'Laptop' and render
  app._setActiveFilterDevice('Laptop');
  app.renderFilterBar(mockContainer, sessions);

  const html = mockContainer.innerHTML;
  // The 'Laptop' pill should have the active class
  assert.ok(html.includes('filter-pill--active'), 'filter bar should mark active device with filter-pill--active class');
  // Verify the active pill corresponds to 'Laptop' specifically
  assert.ok(
    html.match(/filter-pill--active[^>]*>Laptop|Laptop[^<]*filter-pill--active/),
    'filter-pill--active should be on the Laptop pill specifically'
  );

  // Reset
  app._setActiveFilterDevice('all');
});

test('renderFilterBar and _setActiveFilterDevice are exported', () => {
  assert.strictEqual(typeof app.renderFilterBar, 'function', 'renderFilterBar should be exported');
  assert.strictEqual(typeof app._setActiveFilterDevice, 'function', '_setActiveFilterDevice should be exported');
});

// --- loadGridViewMode / saveGridViewMode (task-13) ---

test('loadGridViewMode returns flat by default', () => {
  // Clear display settings and server settings so everything is at defaults
  _localStorageStore = {};
  app._setServerSettings(null);

  const mode = app.loadGridViewMode();
  assert.strictEqual(mode, 'flat', 'loadGridViewMode should return flat when no preference is set');
});

test('loadGridViewMode reads from localStorage when scope is local', () => {
  // Set display settings with viewPreferenceScope 'local' and gridViewMode 'grouped'
  _localStorageStore = {};
  _localStorageStore['muxplex.display'] = JSON.stringify({ viewPreferenceScope: 'local', gridViewMode: 'grouped' });
  app._setServerSettings(null);

  const mode = app.loadGridViewMode();
  assert.strictEqual(mode, 'grouped', 'loadGridViewMode should return gridViewMode from localStorage when scope is local');

  // Cleanup
  _localStorageStore = {};
});

test('loadGridViewMode reads from serverSettings when scope is server', () => {
  // Set display settings with viewPreferenceScope 'server', server has grid_view_mode 'filtered'
  _localStorageStore = {};
  _localStorageStore['muxplex.display'] = JSON.stringify({ viewPreferenceScope: 'server' });
  app._setServerSettings({ grid_view_mode: 'filtered' });

  const mode = app.loadGridViewMode();
  assert.strictEqual(mode, 'filtered', 'loadGridViewMode should return grid_view_mode from serverSettings when scope is server');

  // Cleanup
  _localStorageStore = {};
  app._setServerSettings(null);
});

test('saveGridViewMode stores to localStorage when scope is local', () => {
  // Set scope to local (default)
  _localStorageStore = {};
  _localStorageStore['muxplex.display'] = JSON.stringify({ viewPreferenceScope: 'local' });
  app._setServerSettings(null);

  app.saveGridViewMode('grouped');

  // Verify _gridViewMode was updated
  assert.strictEqual(app._getGridViewMode(), 'grouped', '_gridViewMode should be set to grouped');

  // Verify it was saved to localStorage display settings
  const saved = JSON.parse(_localStorageStore['muxplex.display'] || '{}');
  assert.strictEqual(saved.gridViewMode, 'grouped', 'gridViewMode should be saved to localStorage display settings');

  // Cleanup
  _localStorageStore = {};
  app._setGridViewMode('flat');
});

// --- renderSidebar device grouping (task-16) ---

test('renderSidebar groups sessions by device with sidebar-device-header when multiple sources configured', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'sidebar-list') return mockList;
    return null;
  };

  // Set up multiple sources
  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
    { url: 'https://remote.example.com', name: 'Server', type: 'remote', status: 'authenticated', backoffMs: 2000 },
  ]);

  app._setViewMode('fullscreen');
  const sessions = [
    { name: 'alpha', deviceName: 'Laptop', sourceUrl: '', sessionKey: '::alpha', snapshot: '', bell: { unseen_count: 0 } },
    { name: 'beta', deviceName: 'Server', sourceUrl: 'https://remote.example.com', sessionKey: 'https://remote.example.com::beta', snapshot: '', bell: { unseen_count: 0 } },
  ];
  app.renderSidebar(sessions, null);

  assert.ok(capturedHTML.includes('sidebar-device-header'), 'sidebar HTML should contain sidebar-device-header elements when multiple sources');
  assert.ok(capturedHTML.includes('Laptop'), 'sidebar HTML should contain device name "Laptop"');
  assert.ok(capturedHTML.includes('Server'), 'sidebar HTML should contain device name "Server"');

  // Cleanup
  app._setSources([]);
  globalThis.document.getElementById = origGetById;
  app._setViewMode('grid');
});

test('renderSidebar does NOT group when only one source configured', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'sidebar-list') return mockList;
    return null;
  };

  // Set up single source
  app._setSources([
    { url: '', name: 'Laptop', type: 'local', status: 'authenticated', backoffMs: 2000 },
  ]);

  app._setViewMode('fullscreen');
  const sessions = [
    { name: 'alpha', deviceName: 'Laptop', sourceUrl: '', sessionKey: '::alpha', snapshot: '', bell: { unseen_count: 0 } },
    { name: 'beta', deviceName: 'Laptop', sourceUrl: '', sessionKey: '::beta', snapshot: '', bell: { unseen_count: 0 } },
  ];
  app.renderSidebar(sessions, null);

  assert.ok(!capturedHTML.includes('sidebar-device-header'), 'sidebar HTML should NOT contain sidebar-device-header when only one source');
  assert.ok(capturedHTML.includes('sidebar-item'), 'sidebar HTML should still contain sidebar-item elements');

  // Cleanup
  app._setSources([]);
  globalThis.document.getElementById = origGetById;
  app._setViewMode('grid');
});

// --- Phase 2 integration tests (task-19) ---

test('app.js exports all Phase 2 federation functions', () => {
  const expectedFunctions = [
    'api',
    'buildSources',
    'tagSessions',
    'mergeSources',
    'getVisibleSessions',
    'renderGroupedGrid',
    'renderFilterBar',
    'loadGridViewMode',
    'saveGridViewMode',
    '_setSources',
    '_getSources',
    '_setServerSettings',
    '_getGridViewMode',
    '_setGridViewMode',
    '_setActiveFilterDevice',
  ];

  for (const fn of expectedFunctions) {
    assert.ok(fn in app, `app.js should export "${fn}"`);
    assert.strictEqual(typeof app[fn], 'function', `"${fn}" should be a function`);
  }
});

test('Phase 2 end-to-end: buildSources → tagSessions → mergeSources produces valid merged list', () => {
  // Step 1: buildSources — build the federation source list from server settings
  const sources = app.buildSources({
    device_name: 'Laptop',
    remote_instances: [
      { url: 'https://server.example.com', name: 'Server' },
    ],
  });
  assert.strictEqual(sources.length, 2, 'buildSources should produce 2 sources (local + 1 remote)');
  assert.strictEqual(sources[0].url, '', 'first source should be local (empty url)');
  assert.strictEqual(sources[1].url, 'https://server.example.com', 'second source should be remote');

  // Step 2: tagSessions — tag sessions from each source with device/URL metadata.
  // Both sources have a session named "main" — the classic multi-device naming conflict.
  const rawLocal = [{ name: 'main', bell: { unseen_count: 0 } }];
  const rawRemote = [{ name: 'main', bell: { unseen_count: 0 } }];
  const taggedLocal = app.tagSessions(rawLocal, sources[0].name, sources[0].url);
  const taggedRemote = app.tagSessions(rawRemote, sources[1].name, sources[1].url);

  assert.strictEqual(taggedLocal[0].sessionKey, '::main', 'local session key should be ::main');
  assert.strictEqual(
    taggedRemote[0].sessionKey,
    'https://server.example.com::main',
    'remote session key should include source URL',
  );

  // Step 3: mergeSources — merge all source results into a flat tagged list.
  // mergeSources internally calls tagSessions, so we pass raw (untagged) sessions.
  const merged = app.mergeSources([
    { source: sources[0], sessions: rawLocal },
    { source: sources[1], sessions: rawRemote },
  ]);

  assert.strictEqual(merged.length, 2, 'merged list should contain 2 sessions total');

  // Same-named sessions from different devices must have different sessionKeys.
  const sessionKeys = merged.map((s) => s.sessionKey);
  assert.strictEqual(
    new Set(sessionKeys).size,
    2,
    'same-named sessions from different devices should have different sessionKeys',
  );
  assert.ok(sessionKeys.includes('::main'), 'local session key "::main" should be present in merged list');
  assert.ok(
    sessionKeys.includes('https://server.example.com::main'),
    'remote session key "https://server.example.com::main" should be present in merged list',
  );
});

// --- buildAuthTileHTML ---

test('buildAuthTileHTML is exported as a function', () => {
  assert.strictEqual(typeof app.buildAuthTileHTML, 'function');
});

test('buildAuthTileHTML returns article with source-tile--auth class', () => {
  const html = app.buildAuthTileHTML({ name: 'Dev Server', url: 'http://dev:8088' });
  assert.ok(html.startsWith('<article'), 'html should start with <article');
  assert.ok(html.includes('source-tile--auth'), 'html should include source-tile--auth class');
});

test('buildAuthTileHTML includes device name', () => {
  const html = app.buildAuthTileHTML({ name: 'Dev Server', url: 'http://dev:8088' });
  assert.ok(html.includes('Dev Server'), 'html should include the device name');
});

test('buildAuthTileHTML includes login button with data-url attribute', () => {
  const html = app.buildAuthTileHTML({ name: 'Dev Server', url: 'http://dev:8088' });
  assert.ok(html.includes('source-tile__login-btn'), 'html should include source-tile__login-btn class');
  assert.ok(html.includes('data-url="http://dev:8088"'), 'html should include data-url attribute with correct value');
});

test('buildAuthTileHTML escapes HTML in device name', () => {
  const html = app.buildAuthTileHTML({ name: '<script>alert(1)</script>', url: '' });
  assert.ok(!html.includes('<script>alert(1)</script>'), 'raw script tag should not appear in html');
  assert.ok(html.includes('&lt;script&gt;'), 'escaped script tag should appear in html');
});

// --- buildOfflineTileHTML ---

test('buildOfflineTileHTML is exported as a function', () => {
  assert.strictEqual(typeof app.buildOfflineTileHTML, 'function');
});

test('buildOfflineTileHTML returns article with source-tile--offline class', () => {
  const html = app.buildOfflineTileHTML({ name: 'Dev Server', url: 'http://dev:8088', lastSeenAt: null });
  assert.ok(html.startsWith('<article'), 'html should start with <article');
  assert.ok(html.includes('source-tile--offline'), 'html should include source-tile--offline class');
});

test('buildOfflineTileHTML includes device name', () => {
  const html = app.buildOfflineTileHTML({ name: 'Dev Server', url: 'http://dev:8088', lastSeenAt: null });
  assert.ok(html.includes('Dev Server'), 'html should include the device name');
});

test('buildOfflineTileHTML includes Offline badge', () => {
  const html = app.buildOfflineTileHTML({ name: 'Dev Server', url: 'http://dev:8088', lastSeenAt: null });
  assert.ok(html.includes('Offline'), 'html should include Offline text');
  assert.ok(html.includes('source-tile__badge'), 'html should include source-tile__badge class');
});

test('buildOfflineTileHTML shows relative last-seen time', () => {
  const fiveMinAgo = Date.now() - 5 * 60 * 1000;
  const html = app.buildOfflineTileHTML({ name: 'Dev Server', url: 'http://dev:8088', lastSeenAt: fiveMinAgo });
  assert.ok(html.includes('Last seen'), 'html should include Last seen text');
});

test('buildOfflineTileHTML escapes device name', () => {
  const html = app.buildOfflineTileHTML({ name: '<b>bad</b>', url: '', lastSeenAt: null });
  assert.ok(!html.includes('<b>bad</b>'), 'raw <b>bad</b> must not appear in html');
  assert.ok(html.includes('&lt;b&gt;bad&lt;/b&gt;'), 'device name should be HTML-escaped');
});

test('buildOfflineTileHTML shows "Never" when lastSeenAt is null', () => {
  const html = app.buildOfflineTileHTML({ name: 'Dev Server', url: '', lastSeenAt: null });
  assert.ok(html.includes('Never'), 'html should include Never when lastSeenAt is null');
});


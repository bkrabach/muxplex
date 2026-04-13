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

// --- pollSessions federation endpoint ---

test('pollSessions source includes /api/federation/sessions', () => {
  assert.ok(
    app.pollSessions.toString().includes('/api/federation/sessions'),
    'pollSessions must reference /api/federation/sessions endpoint',
  );
});

test('pollSessions source includes multi_device_enabled check', () => {
  assert.ok(
    app.pollSessions.toString().includes('multi_device_enabled'),
    'pollSessions must check multi_device_enabled flag',
  );
});

test('pollSessions uses /api/federation/sessions when multi_device_enabled is true', async () => {
  const fetchedUrls = [];
  const mockEl = { textContent: '', className: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'connection-status' ? mockEl : null);
  globalThis.fetch = async (url) => {
    fetchedUrls.push(url);
    return { ok: true, json: async () => [] };
  };

  app._setServerSettings({ multi_device_enabled: true });
  await app.pollSessions();
  app._setServerSettings(null);

  assert.ok(
    fetchedUrls.some((u) => u === '/api/federation/sessions'),
    'should fetch /api/federation/sessions when multi_device_enabled is true',
  );
  globalThis.document.getElementById = origGetById;
  globalThis.fetch = undefined;
});

test('pollSessions uses /api/sessions when multi_device_enabled is false', async () => {
  const fetchedUrls = [];
  const mockEl = { textContent: '', className: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'connection-status' ? mockEl : null);
  globalThis.fetch = async (url) => {
    fetchedUrls.push(url);
    return { ok: true, json: async () => [] };
  };

  app._setServerSettings({ multi_device_enabled: false });
  await app.pollSessions();
  app._setServerSettings(null);

  assert.ok(
    fetchedUrls.some((u) => u === '/api/sessions'),
    'should fetch /api/sessions when multi_device_enabled is false',
  );
  assert.ok(
    !fetchedUrls.some((u) => u === '/api/federation/sessions'),
    'should NOT fetch /api/federation/sessions when multi_device_enabled is false',
  );
  globalThis.document.getElementById = origGetById;
  globalThis.fetch = undefined;
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

test('buildTileHTML shows edge-bell class (not count text) when unseen_count exceeds 9', () => {
  const session = {
    name: 's',
    bell: { unseen_count: 10, seen_at: null, last_fired_at: 100 },
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--edge-bell'), 'should have edge-bell class for count > 9');
  assert.ok(!html.includes('9+'), 'should NOT show 9+ count text (edge bar only)');
});

test('buildTileHTML shows edge-bell class (not count text) when unseen_count is <= 9', () => {
  const session = {
    name: 's',
    bell: { unseen_count: 5, seen_at: null, last_fired_at: 100 },
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--edge-bell'), 'should have edge-bell class for count > 0');
  assert.ok(!html.includes('>5<'), 'should NOT show exact count 5 (edge bar only)');
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

test('buildTileHTML includes data-remote-id attribute when session has remoteId', () => {
  const session = { name: 'work-project', remoteId: 'fed-abc123', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('data-remote-id="fed-abc123"'), 'article should have data-remote-id with the session remoteId');
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

test('renderGrid includes auth tile HTML when a session has auth_failed status', () => {
  const mockGrid = { innerHTML: '' };
  const mockEmpty = { style: {}, classList: { add: () => {}, remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  const sessions = [
    { name: 'my-session', snapshot: 'hello' },
    { name: 'Workstation', status: 'auth_failed' },
  ];
  app.renderGrid(sessions);

  assert.ok(mockGrid.innerHTML.includes('source-tile--auth'), 'grid should include auth tile class');
  assert.ok(mockGrid.innerHTML.includes('Workstation'), 'grid should include device name Workstation');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
});

test('renderGrid includes offline tile HTML when a session has unreachable status', () => {
  const mockGrid = { innerHTML: '' };
  const mockEmpty = { style: {}, classList: { add: () => {}, remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  const sessions = [
    { name: 'my-session', snapshot: 'hello' },
    { name: 'Dev Server', status: 'unreachable' },
  ];
  app.renderGrid(sessions);

  assert.ok(mockGrid.innerHTML.includes('source-tile--offline'), 'grid should include offline tile class');
  assert.ok(mockGrid.innerHTML.includes('Dev Server'), 'grid should include device name Dev Server');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
});

test('renderGrid shows auth tile and hides empty-state when session has auth_failed status', () => {
  const addedClasses = [];
  const removedClasses = [];
  const mockGrid = { innerHTML: '' };
  const mockEmpty = { style: {}, classList: { add: (c) => addedClasses.push(c), remove: (c) => removedClasses.push(c) } };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };

  // Pass status entry as a session — early-return path (no regular sessions)
  app.renderGrid([{ name: 'Workstation', status: 'auth_failed' }]);

  assert.ok(mockGrid.innerHTML.includes('source-tile--auth'), 'grid should show auth tile even when no regular sessions');
  assert.ok(addedClasses.includes('hidden'), 'empty-state should be hidden when status tiles are present');
  assert.ok(!removedClasses.includes('hidden'), 'empty-state hidden class should NOT be removed when status tiles are present');

  globalThis.document.getElementById = origGetById;
});

test('renderGrid shows offline tile and hides empty-state when session has unreachable status', () => {
  const addedClasses = [];
  const mockGrid = { innerHTML: '' };
  const mockEmpty = { style: {}, classList: { add: (c) => addedClasses.push(c), remove: () => {} } };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    return null;
  };

  // Pass status entry as a session — early-return path
  app.renderGrid([{ name: 'Dev Box', status: 'unreachable' }]);

  assert.ok(mockGrid.innerHTML.includes('source-tile--offline'), 'grid should show offline tile even when no regular sessions');
  assert.ok(addedClasses.includes('hidden'), 'empty-state should be hidden when status tiles are present');

  globalThis.document.getElementById = origGetById;
});

test('_previewClickHandler looks up remoteId from _currentSessions before calling openSession', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const handlerIdx = source.indexOf('function _previewClickHandler');
  assert.ok(handlerIdx >= 0, '_previewClickHandler must exist');
  // Extract from function declaration to its closing brace
  const handlerEnd = source.indexOf('\n}', handlerIdx) + 2;
  const handlerBody = source.slice(handlerIdx, handlerEnd);
  assert.ok(handlerBody.includes('_currentSessions'), '_previewClickHandler must look up session from _currentSessions to recover remoteId');
  assert.ok(handlerBody.includes('remoteId'), '_previewClickHandler must forward remoteId when calling openSession');
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

test('openSession with skipAnimation calls window._openTerminal after connect POST', async () => {
  // After the fix: skipAnimation only skips the zoom animation, connect always fires.
  let openTerminalCalledWith = null;
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url, opts) => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  // Use a synchronous mock so setTimeout callbacks run immediately
  globalThis.setTimeout = (fn) => { fn(); };
  globalThis.window._openTerminal = (name) => { openTerminalCalledWith = name; };

  await app.openSession('my-session', { skipAnimation: true });

  assert.strictEqual(openTerminalCalledWith, 'my-session', '_openTerminal should be called with session name');
  globalThis.fetch = origFetch;
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

test('openSession with remoteId POSTs connect to federation proxy URL', async () => {
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

  await app.openSession('work-project', { remoteId: 'fed-abc123' });

  const connectCall = fetchCalls.find((c) => c.url === '/api/federation/fed-abc123/connect/work-project');
  assert.ok(connectCall, 'should POST to /api/federation/fed-abc123/connect/work-project');
  assert.strictEqual(connectCall.opts.method, 'POST');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession with remoteId passes remoteId to window._openTerminal', async () => {
  let openTerminalArgs = null;
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async () => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = (fn) => { fn(); };
  globalThis.window._openTerminal = (...args) => { openTerminalArgs = args; };

  await app.openSession('my-session', { remoteId: 'fed-abc123' });

  assert.ok(openTerminalArgs !== null, '_openTerminal should have been called');
  assert.strictEqual(openTerminalArgs[0], 'my-session', '_openTerminal first arg should be session name');
  assert.strictEqual(openTerminalArgs[1], 'fed-abc123', '_openTerminal second arg should be remoteId');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession passes getDisplaySettings().fontSize to window._openTerminal as third argument', async () => {
  // Verify openSession passes getDisplaySettings().fontSize to _openTerminal as third argument.
  let openTerminalArgs = null;
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async () => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = (fn) => { fn(); };
  globalThis.window._openTerminal = (...args) => { openTerminalArgs = args; };

  app._setServerSettings({ fontSize: 18 });
  await app.openSession('my-session', { skipAnimation: true });
  app._setServerSettings(null);

  assert.ok(openTerminalArgs !== null, '_openTerminal should have been called');
  assert.strictEqual(openTerminalArgs[2], 18,
    '_openTerminal third arg should be fontSize from getDisplaySettings()');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession for local session still POSTs to local /api/sessions/{name}/connect', async () => {
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

  await app.openSession('local-session', {});

  const connectCall = fetchCalls.find((c) => c.url === '/api/sessions/local-session/connect');
  assert.ok(connectCall, 'should POST to /api/sessions/local-session/connect');
  assert.strictEqual(connectCall.opts.method, 'POST');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession bails early when name is empty string', async () => {
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

  await app.openSession('', {});

  // Should NOT make any fetch calls to /connect
  const connectCall = fetchCalls.find((c) => c.url && c.url.includes('/connect'));
  assert.ok(!connectCall, 'should NOT call connect when name is empty string');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession bails early when name is whitespace only', async () => {
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

  await app.openSession('  \t\n  ', {});

  // Should NOT make any fetch calls to /connect
  const connectCall = fetchCalls.find((c) => c.url && c.url.includes('/connect'));
  assert.ok(!connectCall, 'should NOT call connect when name is whitespace only');
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

test('closeSession does NOT fire DELETE for remote session (non-empty _viewingRemoteId)', async () => {
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;

  // Setup to call openSession with remote remoteId
  globalThis.fetch = async () => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};
  globalThis.window._closeTerminal = () => {};

  // Open a remote session - this sets _viewingRemoteId = 'fed-abc123'
  await app.openSession('remote-sess', { remoteId: 'fed-abc123' });

  // Restore setTimeout so Promise-based yielding works
  globalThis.setTimeout = origSetTimeout;

  // Reset fetch tracking
  const fetchCalls = [];
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };

  // Close session
  await app.closeSession();
  // yield microtask queue for any fire-and-forget calls
  await new Promise((r) => setTimeout(r, 0));

  const deleteCall = fetchCalls.find((c) => c.url === '/api/sessions/current' && c.opts && c.opts.method === 'DELETE');
  assert.ok(!deleteCall, 'closeSession should NOT fire DELETE for remote session');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('closeSession still fires DELETE /api/sessions/current for local session', async () => {
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;

  // Setup to call openSession for a local session (no remoteId)
  globalThis.fetch = async () => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};
  globalThis.window._closeTerminal = () => {};

  // Open a local session - this sets _viewingRemoteId = ''
  await app.openSession('local-sess', {});

  // Restore setTimeout so Promise-based yielding works
  globalThis.setTimeout = origSetTimeout;

  // Reset fetch tracking
  const fetchCalls = [];
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };

  // Close session
  await app.closeSession();
  // yield microtask queue for fire-and-forget DELETE
  await new Promise((r) => setTimeout(r, 0));

  const deleteCall = fetchCalls.find((c) => c.url === '/api/sessions/current' && c.opts && c.opts.method === 'DELETE');
  assert.ok(deleteCall, 'closeSession should fire DELETE /api/sessions/current for local session');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
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

test('buildSidebarHTML shows bell indicator class when unseen_count > 0', () => {
  const session = { name: 's', snapshot: '', bell: { unseen_count: 3 } };
  const html = app.buildSidebarHTML(session, '');
  // activityIndicator defaults to 'both' so sidebar-item--bell and sidebar-item--edge-bell should appear
  assert.ok(html.includes('sidebar-item--bell'), 'should have sidebar-item--bell class when unseen_count > 0');
  assert.ok(!html.includes('>3<'), 'should NOT contain unseen count text (edge bar only)');
});

test('buildSidebarHTML omits bell indicator classes when unseen_count is 0', () => {
  const session = { name: 's', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(!html.includes('sidebar-item--bell'), 'should not have sidebar-item--bell when unseen_count is 0');
  assert.ok(!html.includes('sidebar-item--edge-bell'), 'should not have sidebar-item--edge-bell when unseen_count is 0');
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

// --- getVisibleSessions ---

test('getVisibleSessions filters out entries with unreachable status (no name)', () => {
  const sessions = [
    { name: 'real-session', snapshot: '' },
    { status: 'unreachable', remoteId: 0, deviceName: 'alienware-r13' },
  ];
  const result = app.getVisibleSessions(sessions);
  assert.strictEqual(result.length, 1, 'should return only the real session');
  assert.strictEqual(result[0].name, 'real-session');
});

test('getVisibleSessions filters out entries with auth_failed status', () => {
  const sessions = [
    { name: 'real-session', snapshot: '' },
    { name: 'Workstation', status: 'auth_failed' },
  ];
  const result = app.getVisibleSessions(sessions);
  assert.strictEqual(result.length, 1, 'auth_failed entry should be filtered out');
  assert.strictEqual(result[0].name, 'real-session');
});

test('getVisibleSessions passes through sessions with no status field', () => {
  const sessions = [
    { name: 'alpha', snapshot: '' },
    { name: 'beta', snapshot: '' },
  ];
  const result = app.getVisibleSessions(sessions);
  assert.strictEqual(result.length, 2, 'normal sessions should all pass through');
});

// ─── initSidebar ─────────────────────────────────────────────────────────────

test('initSidebar defaults to open (removes sidebar--collapsed) on wide screens when no stored value', () => {
  app._setServerSettings(null); // ensure no stored sidebarOpen value in server settings
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
  app._setServerSettings(null);  // ensure no stored sidebarOpen value in server settings
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
  app._setServerSettings({ sidebarOpen: true });  // stored value = true in server settings
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
  app._setServerSettings(null);
});

// ─── toggleSidebar ───────────────────────────────────────────────────────────

test('toggleSidebar persists state to _serverSettings — from open toggles to closed', () => {
  app._setServerSettings({});  // non-null so sidebarOpen assignment takes effect

  // Sidebar is open (no sidebar--collapsed class) — contains returns false
  const mockSidebar = {
    classList: { remove: () => {}, add: () => {}, contains: () => false },
  };
  const mockCollapseBtn = { textContent: '' };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return mockSidebar;
    if (id === 'sidebar-collapse-btn') return mockCollapseBtn;
    return null;
  };

  app.toggleSidebar();

  assert.strictEqual(app._getServerSettings().sidebarOpen, false, 'should set sidebarOpen=false in _serverSettings after toggling from open');

  globalThis.document.getElementById = origGetById;
  app._setServerSettings(null);
});

test('toggleSidebar adds sidebar--collapsed class when closing (from open)', () => {
  app._setServerSettings({});  // non-null so sidebarOpen assignment takes effect

  const addedClasses = [];
  // Sidebar is open (no sidebar--collapsed class) — contains returns false
  const mockSidebar = {
    classList: { remove: () => {}, add: (c) => addedClasses.push(c), contains: () => false },
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
  app._setServerSettings(null);
});

test('toggleSidebar removes sidebar--collapsed class when opening (from closed) and persists to _serverSettings', () => {
  app._setServerSettings({});  // non-null so sidebarOpen assignment takes effect

  const removedClasses = [];
  // Sidebar is closed (has sidebar--collapsed class) — contains returns true
  const mockSidebar = {
    classList: { remove: (c) => removedClasses.push(c), add: () => {}, contains: (c) => c === 'sidebar--collapsed' },
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
  assert.strictEqual(app._getServerSettings().sidebarOpen, true, 'should set sidebarOpen=true in _serverSettings after toggling from closed');

  globalThis.document.getElementById = origGetById;
  app._setServerSettings(null);
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
  const fnBody = source.substring(fnStart, fnStart + 4000);

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
        style: {},
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
        style: {},
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

test('_setServerSettings sets internal _serverSettings', () => {
  assert.doesNotThrow(() => app._setServerSettings({ sort_order: 'recent' }));
});

test('_getGridViewMode returns current _gridViewMode value', () => {
  assert.strictEqual(typeof app._getGridViewMode(), 'string', '_getGridViewMode should return a string');
  assert.strictEqual(app._getGridViewMode(), 'flat', '_gridViewMode should default to flat');
});

// --- getVisibleSessions (task-7) ---

test('getVisibleSessions exported and filters hidden sessions', () => {
  // Verify getVisibleSessions is exported as a function
  assert.strictEqual(typeof app.getVisibleSessions, 'function', 'getVisibleSessions should be exported as a function');

  // Set up server settings with hidden_sessions
  app._setServerSettings({ hidden_sessions: ['secret', 'hidden-local'] });

  // Local sessions (no remoteId) matching hidden list should be filtered
  const sessions = [
    { name: 'visible' },
    { name: 'secret' },          // local, should be hidden
    { name: 'hidden-local' },    // local, should be hidden
    { name: 'other' },
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

test('getVisibleSessions hides remote sessions with matching name (federation-aware)', () => {
  // hidden_sessions syncs across federation nodes, so the filter must apply to
  // ALL sessions regardless of remoteId — both local and federated.
  app._setServerSettings({ hidden_sessions: ['shared-name'] });

  const sessions = [
    { name: 'shared-name' },                         // local (no remoteId) — should be hidden
    { name: 'shared-name', remoteId: 1 },            // remote — should ALSO be hidden
    { name: 'another' },                             // local, not in hidden list — should remain
  ];

  const result = app.getVisibleSessions(sessions);
  assert.strictEqual(result.length, 1, 'should show only 1 session (another); both local and remote shared-name are hidden');
  assert.ok(result.some((s) => s.name === 'another'), 'another should remain visible');
  assert.ok(!result.some((s) => s.name === 'shared-name'), 'all sessions named shared-name (local or remote) should be hidden');

  // Clean up
  app._setServerSettings(null);
});

// --- buildSidebarHTML device badge (task-10) ---

test('buildSidebarHTML shows device-badge when multi_device_enabled', () => {
  app._setServerSettings({ multi_device_enabled: true });
  const session = { name: 'work', deviceName: 'Laptop', sessionKey: '::work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('device-badge'), 'should show device-badge when multi_device_enabled and session has deviceName');
  assert.ok(html.includes('Laptop'), 'device-badge should contain the deviceName');
  app._setServerSettings(null);
});

test('buildSidebarHTML omits device-badge when multi_device_enabled is false', () => {
  app._setServerSettings({ multi_device_enabled: false });
  const session = { name: 'work', deviceName: 'Laptop', sessionKey: '::work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(!html.includes('device-badge'), 'should NOT show device-badge when multi_device_enabled is false');
  app._setServerSettings(null);
});

test('buildSidebarHTML omits device-badge when session has no deviceName', () => {
  app._setServerSettings({ multi_device_enabled: true });
  const session = { name: 'work', sessionKey: '::work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(!html.includes('device-badge'), 'should NOT show device-badge when session has no deviceName');
  app._setServerSettings(null);
});

test('buildSidebarHTML includes data-remote-id attribute on article element', () => {
  const session = {
    name: 'work',
    deviceName: 'Laptop',
    remoteId: 'fed-abc123',
    sessionKey: 'fed-abc123::work',
    snapshot: '',
    bell: { unseen_count: 0 },
  };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('data-remote-id="fed-abc123"'), 'article should have data-remote-id with correct value');
});

test('buildSidebarHTML data-remote-id is empty string when session has no remoteId', () => {
  const session = { name: 'work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('data-remote-id=""'), 'article should have data-remote-id as empty string when no remoteId');
});

test('buildSidebarHTML escapes HTML in deviceName within device-badge', () => {
  app._setServerSettings({ multi_device_enabled: true });
  const session = { name: 'work', deviceName: '<script>alert(1)</script>', sessionKey: '::work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(!html.includes('<script>'), 'device-badge should not contain raw <script> tag');
  assert.ok(html.includes('&lt;script&gt;'), 'device-badge should escape < and > in deviceName');
  app._setServerSettings(null);
});

// --- buildTileHTML device badge (task-9) ---

test('buildTileHTML shows device-badge when session has deviceName and multi_device_enabled', () => {
  app._setServerSettings({ multi_device_enabled: true });
  const session = { name: 'work', deviceName: 'Laptop', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('device-badge'), 'should show device-badge when multi_device_enabled and session has deviceName');
  assert.ok(html.includes('Laptop'), 'device-badge should contain the deviceName');
  app._setServerSettings(null);
});

test('buildTileHTML omits device-badge when multi_device_enabled is false', () => {
  app._setServerSettings({ multi_device_enabled: false });
  const session = { name: 'work', deviceName: 'Laptop', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('device-badge'), 'should NOT show device-badge when multi_device_enabled is false');
  app._setServerSettings(null);
});

test('buildTileHTML includes data-session-key and data-remote-id attributes on article element', () => {
  const session = {
    name: 'work',
    deviceName: 'Laptop',
    remoteId: 'fed-abc123',
    sessionKey: 'fed-abc123::work',
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('data-session-key="fed-abc123::work"'), 'article should have data-session-key with correct value');
  assert.ok(html.includes('data-remote-id="fed-abc123"'), 'article should have data-remote-id with correct value');
});

test('buildTileHTML escapes HTML in deviceName within device-badge', () => {
  app._setServerSettings({ multi_device_enabled: true });
  const session = { name: 'work', deviceName: '<script>alert(1)</script>', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('<script>'), 'device-badge should not contain raw <script> tag');
  assert.ok(html.includes('&lt;script&gt;'), 'device-badge should escape < and > in deviceName');
  app._setServerSettings(null);
});

// --- buildTileHTML device badge placement (task-3) ---

test('buildTileHTML places device-badge inside tile-meta span', () => {
  app._setServerSettings({ multi_device_enabled: true });
  const session = { name: 'work', deviceName: 'Laptop', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  const tileMetaStart = html.indexOf('<span class="tile-meta">');
  // Note: finds the first </span> after tileMetaStart, which is device-badge's closing tag
  // (not tile-meta's own close), but the assertion still holds because device-badge
  // opens and closes before tile-time within the tile-meta container.
  const tileMetaEnd = html.indexOf('</span>', tileMetaStart);
  assert.ok(tileMetaStart !== -1, 'tile-meta span should exist');
  const deviceBadgePos = html.indexOf('device-badge');
  assert.ok(
    deviceBadgePos > tileMetaStart && deviceBadgePos < tileMetaEnd,
    `device-badge should be inside tile-meta span (tile-meta starts at ${tileMetaStart}, device-badge at ${deviceBadgePos}, tile-meta closes at ${tileMetaEnd})`
  );
  app._setServerSettings(null);
});

test('buildTileHTML includes tile-meta-sep with middle dot when badge present', () => {
  app._setServerSettings({ multi_device_enabled: true });
  const session = { name: 'work', deviceName: 'Laptop', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('tile-meta-sep'), 'should include tile-meta-sep element when badge is present');
  assert.ok(html.includes('\u00b7'), 'should include middle dot separator (\u00b7)');
  app._setServerSettings(null);
});

test('buildTileHTML does not include tile-meta-sep when no badge', () => {
  app._setServerSettings({ multi_device_enabled: false });
  const session = { name: 'work', deviceName: 'Laptop', sessionKey: '::work', snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('tile-meta-sep'), 'should NOT include tile-meta-sep when no badge');
  app._setServerSettings(null);
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
    { name: 'alpha', deviceName: 'Laptop', sessionKey: 'http://local::alpha', snapshot: '' },
    { name: 'beta', deviceName: 'Server', sessionKey: 'http://remote::beta', snapshot: '' },
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
    { name: 'alpha', deviceName: 'Laptop', sessionKey: 'http://local::alpha', snapshot: '' },
    { name: 'beta', deviceName: 'Server', sessionKey: 'http://remote::beta', snapshot: '' },
    { name: 'gamma', deviceName: 'Laptop', sessionKey: 'http://local::gamma', snapshot: '' },
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
    { name: 'alpha', deviceName: 'Laptop', sessionKey: 'http://local::alpha', snapshot: '' },
    { name: 'beta', deviceName: 'Server', sessionKey: 'http://remote::beta', snapshot: '' },
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

test('loadGridViewMode reads gridViewMode from _serverSettings', () => {
  // gridViewMode is now stored in server settings
  _localStorageStore = {};
  app._setServerSettings({ gridViewMode: 'grouped' });

  const mode = app.loadGridViewMode();
  assert.strictEqual(mode, 'grouped', 'loadGridViewMode should return gridViewMode from _serverSettings');

  // Cleanup
  app._setServerSettings(null);
});

test('loadGridViewMode reads gridViewMode from _serverSettings (not localStorage)', () => {
  // viewPreferenceScope is removed — loadGridViewMode reads from _serverSettings via getDisplaySettings()
  _localStorageStore = {};
  app._setServerSettings({ gridViewMode: 'filtered' });

  const mode = app.loadGridViewMode();
  // Must return _serverSettings value ('filtered')
  assert.strictEqual(mode, 'filtered', 'loadGridViewMode should return gridViewMode from _serverSettings');

  // Cleanup
  _localStorageStore = {};
  app._setServerSettings(null);
});

test('saveGridViewMode stores to _serverSettings', () => {
  // gridViewMode is now stored in server settings
  _localStorageStore = {};
  app._setServerSettings({});

  app.saveGridViewMode('grouped');

  // Verify _gridViewMode was updated
  assert.strictEqual(app._getGridViewMode(), 'grouped', '_gridViewMode should be set to grouped');

  // Verify it was saved to _serverSettings
  assert.strictEqual(app._getServerSettings().gridViewMode, 'grouped', 'gridViewMode should be saved to _serverSettings');

  // Cleanup
  app._setServerSettings(null);
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

  // Set up multi-device enabled
  app._setServerSettings({ multi_device_enabled: true });

  app._setViewMode('fullscreen');
  const sessions = [
    { name: 'alpha', deviceName: 'Laptop', sessionKey: '::alpha', snapshot: '', bell: { unseen_count: 0 } },
    { name: 'beta', deviceName: 'Server', remoteId: 1, sessionKey: 'https://remote.example.com::beta', snapshot: '', bell: { unseen_count: 0 } },
  ];
  app.renderSidebar(sessions, null);

  assert.ok(capturedHTML.includes('sidebar-device-header'), 'sidebar HTML should contain sidebar-device-header elements when multiple sources');
  assert.ok(capturedHTML.includes('Laptop'), 'sidebar HTML should contain device name "Laptop"');
  assert.ok(capturedHTML.includes('Server'), 'sidebar HTML should contain device name "Server"');

  // Cleanup
  app._setServerSettings(null);
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

  // Multi-device disabled
  app._setServerSettings(null);

  app._setViewMode('fullscreen');
  const sessions = [
    { name: 'alpha', deviceName: 'Laptop', sessionKey: '::alpha', snapshot: '', bell: { unseen_count: 0 } },
    { name: 'beta', deviceName: 'Laptop', sessionKey: '::beta', snapshot: '', bell: { unseen_count: 0 } },
  ];
  app.renderSidebar(sessions, null);

  assert.ok(!capturedHTML.includes('sidebar-device-header'), 'sidebar HTML should NOT contain sidebar-device-header when only one source');
  assert.ok(capturedHTML.includes('sidebar-item'), 'sidebar HTML should still contain sidebar-item elements');

  // Cleanup
  globalThis.document.getElementById = origGetById;
  app._setViewMode('grid');
});

// --- Phase 2 integration tests (task-19) ---

test('app.js exports all Phase 2 federation functions', () => {
  const expectedFunctions = [
    'api',
    'buildStatusTileHTML',
    'getVisibleSessions',
    'renderGroupedGrid',
    'renderFilterBar',
    'loadGridViewMode',
    'saveGridViewMode',
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

// --- buildStatusTileHTML ---

test('buildStatusTileHTML is exported as a function', () => {
  assert.strictEqual(typeof app.buildStatusTileHTML, 'function');
});

test('buildStatusTileHTML returns article element with correct statusClass', () => {
  const html = app.buildStatusTileHTML('My Device', 'Offline', 'offline');
  assert.ok(html.startsWith('<article'), 'html should start with <article');
  assert.ok(html.includes('source-tile--offline'), 'html should include the statusClass');
});

test('buildStatusTileHTML escapes XSS in deviceName', () => {
  const html = app.buildStatusTileHTML('<script>alert(1)</script>', 'Offline', 'offline');
  assert.ok(!html.includes('<script>alert(1)</script>'), 'raw script tag should not appear in html');
  assert.ok(html.includes('&lt;script&gt;'), 'escaped script tag should appear in html');
});

test('buildStatusTileHTML renders statusText in badge span', () => {
  const html = app.buildStatusTileHTML('My Device', 'Auth required', 'auth');
  assert.ok(html.includes('source-tile--auth'), 'html should include source-tile--auth class');
  assert.ok(html.includes('Auth required'), 'html should include the statusText');
  assert.ok(html.includes('source-tile__badge'), 'html should include source-tile__badge class');
});

// --- Issue 1: Loading placeholder tile ---

test('createNewSession injects tile--loading placeholder after POST succeeds', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function createNewSession(');
  assert.ok(start !== -1, 'createNewSession must exist');
  const snippet = source.slice(start, start + 2500);
  assert.ok(snippet.includes('tile--loading'), 'createNewSession must inject tile--loading placeholder class');
  assert.ok(snippet.includes('loading-tile-'), 'createNewSession must use loading-tile- id prefix for the placeholder');
});

test('createNewSession removes loading placeholder when session is found', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const start = source.indexOf('async function createNewSession(');
  const snippet = source.slice(start, start + 2500);
  assert.ok(
    snippet.includes('loadingTile') && snippet.includes('.remove()'),
    'createNewSession must remove the loading tile (loadingTile.remove()) when session is found'
  );
});

test('CSS style.css has tile--loading and shimmer animation', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('tile--loading'), 'style.css must have .tile--loading rule');
  assert.ok(source.includes('shimmer'), 'style.css must have shimmer animation');
});

// --- Issue 2: Always call connect on restore ---

test('openSession always POSTs to connect even when skipConnect option is passed', async () => {
  // Before the fix, skipConnect:true skipped the connect POST entirely.
  // After the fix, connect is always called; the option is renamed to skipAnimation.
  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };
  globalThis.document.getElementById = () => ({ textContent: '', style: { removeProperty: () => {} }, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};

  await app.openSession('work', { skipConnect: true });

  const connectCall = fetchCalls.find((c) => c.url === '/api/sessions/work/connect');
  assert.ok(connectCall, 'skipConnect:true must NOT prevent connect POST — connect always fires after fix');
  assert.strictEqual(connectCall.opts.method, 'POST');
  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

// --- Issue 3: Notification permission on user click only ---

test('DOMContentLoaded handler does NOT call requestNotificationPermission at startup', () => {
  // Browsers require notification permission to be requested in response to a user gesture.
  // Auto-calling at startup is silently blocked, leaving the permission in a broken state.
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const domContentLoadedIdx = source.indexOf("document.addEventListener('DOMContentLoaded'");
  assert.ok(domContentLoadedIdx !== -1, 'DOMContentLoaded handler must exist');
  // Extract the DOMContentLoaded handler body (next ~600 chars covers the entire handler)
  const handlerBody = source.substring(domContentLoadedIdx, domContentLoadedIdx + 600);
  assert.ok(
    !handlerBody.includes('requestNotificationPermission'),
    'requestNotificationPermission must NOT be called automatically in DOMContentLoaded — only on user click'
  );
});

// --- Issue 4: Apply font size to live terminal without reconnecting ---

test('applyDisplaySettings calls window._setTerminalFontSize when available', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function applyDisplaySettings(');
  assert.ok(fnStart !== -1, 'applyDisplaySettings must exist');
  const fnBody = source.substring(fnStart, fnStart + 600);
  assert.ok(
    fnBody.includes('_setTerminalFontSize'),
    'applyDisplaySettings must call window._setTerminalFontSize to update live terminal font size'
  );
});



// --- Issue: Dynamic favicon badge with activity dot ---

test('updateFaviconBadge function exists in app.js', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes('function updateFaviconBadge'),
    'app.js must define updateFaviconBadge function'
  );
});

test('pollSessions calls updateFaviconBadge', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const pollStart = source.indexOf('async function pollSessions()');
  assert.ok(pollStart !== -1, 'pollSessions must exist');
  // Find the closing brace of pollSessions (next line starting with "}")
  const pollEnd = source.indexOf('\n}', pollStart);
  const pollBody = source.substring(pollStart, pollEnd + 2);
  assert.ok(
    pollBody.includes('updateFaviconBadge'),
    'pollSessions must call updateFaviconBadge — update favicon on every poll cycle'
  );
});

test('updateFaviconBadge caches Image object instead of fetching every call', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(source.includes('_faviconImage'), 'must cache favicon Image object');
  assert.ok(source.includes('_drawFaviconBadge'), 'must use extracted draw helper');
  // The old pattern: new Image() inside updateFaviconBadge should be gone
  const fnStart = source.indexOf('function updateFaviconBadge');
  const fnBody = source.substring(fnStart, fnStart + 1000);
  assert.ok(!fnBody.includes('new Image()'), 'must NOT create new Image on every call');
});


// --- Delete session template (task: customizable delete command) ---

test('app.js defines DELETE_SESSION_DEFAULT_TEMPLATE constant', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes('DELETE_SESSION_DEFAULT_TEMPLATE'),
    'app.js must define DELETE_SESSION_DEFAULT_TEMPLATE constant'
  );
});

test('DELETE_SESSION_DEFAULT_TEMPLATE value is tmux kill-session -t {name}', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes("'tmux kill-session -t {name}'") || source.includes('"tmux kill-session -t {name}"'),
    "DELETE_SESSION_DEFAULT_TEMPLATE must be set to 'tmux kill-session -t {name}'"
  );
});

test('openSettings loads delete_session_template from server settings', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function openSettings(');
  assert.ok(fnStart !== -1, 'openSettings must exist');
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1);
  const fnBody = source.substring(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 3000);
  assert.ok(
    fnBody.includes('setting-delete-template'),
    'openSettings must populate #setting-delete-template from server settings'
  );
});

test('bindStaticEventListeners wires delete template input to save', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function bindStaticEventListeners(');
  assert.ok(fnStart !== -1, 'bindStaticEventListeners must exist');
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1);
  const fnBody = source.substring(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 6000);
  assert.ok(
    fnBody.includes('setting-delete-template'),
    'bindStaticEventListeners must wire #setting-delete-template input event to save'
  );
});

test('bindStaticEventListeners wires delete template reset button', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function bindStaticEventListeners(');
  assert.ok(fnStart !== -1, 'bindStaticEventListeners must exist');
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1);
  const fnBody = source.substring(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 6000);
  assert.ok(
    fnBody.includes('setting-delete-template-reset'),
    'bindStaticEventListeners must wire #setting-delete-template-reset click handler'
  );
});

// --- View mode cycling (Auto / Fit / Compact) ---

test('app.js has VIEW_MODES array with auto and fit only (no compact)', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(source.includes("'auto'"), "must include 'auto' mode");
  assert.ok(source.includes("'fit'"), "must include 'fit' mode");
  assert.ok(source.includes('VIEW_MODES'), 'must define VIEW_MODES');
  // Compact mode was removed — VIEW_MODES must only have two entries
  const viewModesMatch = source.match(/var VIEW_MODES\s*=\s*\[([^\]]+)\]/);
  assert.ok(viewModesMatch, 'VIEW_MODES array must be defined');
  assert.ok(!viewModesMatch[1].includes("'compact'"), "VIEW_MODES must NOT include 'compact'");
});

test('app.js exports cycleViewMode function', () => {
  assert.ok('cycleViewMode' in app, 'app.js must export cycleViewMode');
  assert.strictEqual(typeof app.cycleViewMode, 'function', 'cycleViewMode must be a function');
});

test('app.js exports applyFitLayout function', () => {
  assert.ok('applyFitLayout' in app, 'app.js must export applyFitLayout');
  assert.strictEqual(typeof app.applyFitLayout, 'function', 'applyFitLayout must be a function');
});

test('DISPLAY_DEFAULTS includes viewMode: auto', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // DISPLAY_DEFAULTS should define viewMode
  const defaultsStart = source.indexOf('DISPLAY_DEFAULTS');
  assert.ok(defaultsStart !== -1, 'DISPLAY_DEFAULTS must exist');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  assert.ok(defaultsBody.includes('viewMode'), 'DISPLAY_DEFAULTS must include viewMode');
});

test('applyDisplaySettings handles fit mode by adding session-grid--fit class', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function applyDisplaySettings(');
  assert.ok(fnStart !== -1, 'applyDisplaySettings must exist');
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1);
  const fnBody = source.substring(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 2000);
  assert.ok(
    fnBody.includes('session-grid--fit'),
    'applyDisplaySettings must apply session-grid--fit class for fit mode'
  );
});

test('applyDisplaySettings does NOT handle compact mode (compact was removed)', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function applyDisplaySettings(');
  assert.ok(fnStart !== -1, 'applyDisplaySettings must exist');
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1);
  const fnBody = source.substring(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 2000);
  assert.ok(
    !fnBody.includes("'compact'"),
    "applyDisplaySettings must NOT reference 'compact' mode — compact was removed"
  );
});

test('cycleViewMode cycles through auto -> fit -> auto (two modes, compact removed)', () => {
  // Reset server settings so viewMode starts at 'auto'
  app._setServerSettings({ viewMode: 'auto' });

  // First cycle: auto -> fit
  app.cycleViewMode();
  const ds1 = app.getDisplaySettings();
  assert.strictEqual(ds1.viewMode, 'fit', 'first cycle should go auto -> fit');

  // Second cycle: fit -> auto (wraps, compact is gone)
  app.cycleViewMode();
  const ds2 = app.getDisplaySettings();
  assert.strictEqual(ds2.viewMode, 'auto', 'second cycle should wrap fit -> auto (only two modes)');

  // Cleanup
  app._setServerSettings(null);
});

test('bindStaticEventListeners wires view-mode-btn click to cycleViewMode', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function bindStaticEventListeners(');
  assert.ok(fnStart !== -1, 'bindStaticEventListeners must exist');
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1);
  const fnBody = source.substring(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 6000);
  assert.ok(
    fnBody.includes('view-mode-btn'),
    'bindStaticEventListeners must wire #view-mode-btn click handler'
  );
});

test('applyFitLayout is called directly (no requestAnimationFrame needed — pure arithmetic)', () => {
  // Pure arithmetic applyFitLayout is safe to call synchronously at any time —
  // it does not measure DOM dimensions so there is no layout-timing dependency.
  // Call sites (renderGrid, closeSession, applyDisplaySettings, resize handler)
  // should call it directly, not via requestAnimationFrame.
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Verify applyFitLayout exists as a direct call (not only inside rAF callbacks)
  assert.ok(
    source.includes('applyFitLayout'),
    'app.js must define and call applyFitLayout'
  );
  // The function body itself must not measure DOM — verified by the separate
  // "does NOT measure DOM dimensions" test. Here just confirm the function exists.
  assert.ok(
    source.includes('function applyFitLayout('),
    'applyFitLayout function must be declared'
  );
});

// --- Fit view bug fixes: closeSession reapply, more lines, bottom-anchor ---

test('closeSession reapplies fit layout when returning to dashboard', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function closeSession');
  assert.ok(fnStart !== -1, 'closeSession function must exist');
  const fnBody = source.substring(fnStart, fnStart + 1500);
  assert.ok(
    fnBody.includes('applyFitLayout'),
    'closeSession must call applyFitLayout for fit mode when returning to dashboard'
  );
});

test('buildTileHTML shows up to 80 lines in fit mode', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes('-80') || source.includes('(-80)'),
    'app.js must use -80 slice for fit mode to show up to 80 lines'
  );
});

test('CSS style.css uses base position:absolute bottom:0 for fit mode content anchoring (no flex override)', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  // Reverted approach: base CSS position:absolute + bottom:0 anchors content to bottom.
  // The flex + justify-content:flex-end approach failed because <pre> filled 100% of parent,
  // making flex-end a no-op (content started at top, excess clipped at bottom).
  // The fit-mode tile-body flex override must be REMOVED.
  assert.ok(
    !source.includes('.session-grid--fit .tile-body {'),
    'style.css must NOT have .session-grid--fit .tile-body flex override — base position:absolute handles anchoring'
  );
  // The pre static-positioning override must also be removed
  assert.ok(
    !source.includes('.session-grid--fit .tile-body pre {'),
    'style.css must NOT have .session-grid--fit .tile-body pre override — base position:absolute + bottom:0 is correct'
  );
  // Base .tile-body pre must still use position:absolute + bottom:0
  assert.ok(
    source.includes('position: absolute') && source.includes('bottom: 0'),
    'base .tile-body pre must retain position:absolute and bottom:0 for content anchoring'
  );
});


// --- document.title quality fixes ---

test('device name input handler updates title via updatePageTitle when value is cleared', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Handler now calls updatePageTitle() (which uses location.hostname fallback) instead of
  // directly setting document.title. Verify the old direct-assignment is gone and the
  // centralised helper is used.
  assert.ok(
    !source.includes("document.title = val || 'muxplex'"),
    "device name input handler must NOT directly set document.title = val || 'muxplex' (use updatePageTitle)"
  );
  assert.ok(
    !source.includes("if (val) document.title = val"),
    "device name input handler must NOT use conditional 'if (val) document.title = val' (skips restore)"
  );
  // The handler must update _serverSettings.device_name before calling updatePageTitle()
  assert.ok(
    source.includes('_serverSettings.device_name = val'),
    "device name input handler must update _serverSettings.device_name before calling updatePageTitle()"
  );
});

test('openSettings updates title via updatePageTitle (not direct assignment)', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // The old direct assignment is replaced by updatePageTitle() in the settings panel loader
  assert.ok(
    !source.includes("document.title = (ss && ss.device_name) || 'muxplex'"),
    "openSettings must NOT directly set document.title — use updatePageTitle() instead"
  );
  assert.ok(
    !source.includes("if (ss && ss.device_name) {\n      document.title = ss.device_name;\n    }"),
    "openSettings must NOT use conditional block that skips restore when device_name is absent"
  );
  // The settings loader area (where it updates device_name field) must call updatePageTitle
  assert.ok(
    source.includes('updatePageTitle'),
    "app.js must define and use updatePageTitle for title management"
  );
});

test('remote instance event listener comments say Multi-Device tab not Sessions tab', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    !source.includes('// Sessions tab \u2014 add remote instance button'),
    "comment for add-remote-instance-btn must say 'Multi-Device tab', not 'Sessions tab'"
  );
  assert.ok(
    !source.includes('// Sessions tab \u2014 delegated remove handler'),
    "comment for delegated remove handler must say 'Multi-Device tab', not 'Sessions tab'"
  );
  assert.ok(
    source.includes('// Multi-Device tab \u2014 add remote instance button'),
    "add-remote-instance-btn comment must say '// Multi-Device tab \u2014 add remote instance button'"
  );
  assert.ok(
    source.includes('// Multi-Device tab \u2014 delegated remove handler'),
    "delegated remove handler comment must say '// Multi-Device tab \u2014 delegated remove handler'"
  );
});

test('DOMContentLoaded sets page title via updatePageTitle after loadServerSettings resolves', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Find the DOMContentLoaded block
  const domIdx = source.indexOf("document.addEventListener('DOMContentLoaded'");
  assert.ok(domIdx !== -1, 'DOMContentLoaded handler must exist');
  const domBlock = source.substring(domIdx, domIdx + 800);
  // The old direct assignment is replaced by updatePageTitle()
  assert.ok(
    !domBlock.includes("document.title = _serverSettings.device_name || 'muxplex'"),
    "DOMContentLoaded must NOT directly set document.title — delegate to updatePageTitle()"
  );
  assert.ok(
    domBlock.includes("updatePageTitle"),
    "DOMContentLoaded must call updatePageTitle() after loadServerSettings resolves"
  );
});


// --- Fit view layout: applyFitLayout sets grid template via pure arithmetic ---

test('applyFitLayout sets gridTemplateColumns and gridTemplateRows via pure arithmetic', () => {
  const assignedProps = {};

  const mockTile = { style: { removeProperty: () => {} } };

  const mockGrid = {
    style: new Proxy(assignedProps, {
      set(target, prop, value) { target[prop] = value; return true; },
      get(target, prop) { return target[prop]; },
    }),
    querySelectorAll: (sel) => {
      if (sel === '.session-tile') return [mockTile, mockTile, mockTile, mockTile];
      return [];
    },
  };

  app.applyFitLayout(mockGrid);

  assert.ok(
    typeof assignedProps.gridTemplateColumns === 'string' && assignedProps.gridTemplateColumns.includes('1fr'),
    'applyFitLayout must set gridTemplateColumns with 1fr tracks'
  );
  assert.ok(
    typeof assignedProps.gridTemplateRows === 'string' && assignedProps.gridTemplateRows.includes('1fr'),
    'applyFitLayout must set gridTemplateRows with 1fr tracks'
  );
});

// --- Pure CSS fit layout: applyFitLayout must not measure DOM ---

test('applyFitLayout does NOT measure DOM dimensions (pure arithmetic)', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function applyFitLayout(');
  assert.ok(fnStart !== -1, 'applyFitLayout function must exist');
  // Find end of function: next \n} at the same nesting level
  let depth = 0;
  let pos = source.indexOf('{', fnStart);
  const fnBodyStart = pos;
  while (pos < source.length) {
    if (source[pos] === '{') depth++;
    else if (source[pos] === '}') {
      depth--;
      if (depth === 0) break;
    }
    pos++;
  }
  const fnBody = source.substring(fnBodyStart, pos + 1);

  assert.ok(!fnBody.includes('clientHeight'),
    'applyFitLayout must NOT read clientHeight — causes wrong values when container is display:none');
  assert.ok(!fnBody.includes('clientWidth'),
    'applyFitLayout must NOT read clientWidth — pure 1fr CSS handles width');
  assert.ok(!fnBody.includes('getComputedStyle'),
    'applyFitLayout must NOT call getComputedStyle — pure 1fr CSS handles gap/padding');
  assert.ok(!fnBody.includes('.style.height'),
    'applyFitLayout must NOT set inline tile heights — CSS grid 1fr rows handle sizing');
});

// ─── Settings tab reorganization (4 tabs) ────────────────────────────────────

test('HTML settings dialog has exactly 4 tab buttons', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const tabMatches = source.match(/class="settings-tab[^"]*"\s+data-tab=/g) || [];
  assert.strictEqual(tabMatches.length, 4, 'settings dialog must have exactly 4 tab buttons (not 5)');
});

test('HTML index.html has no Notifications tab button', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(!source.includes('data-tab="notifications"'), 'Notifications tab button must be removed');
});

test('HTML Sessions panel contains bell-sound checkbox', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  // Find the sessions PANEL div specifically (not the tab button)
  const sessionsPanelStart = source.indexOf('<div class="settings-panel hidden" data-tab="sessions"');
  assert.ok(sessionsPanelStart !== -1, 'sessions panel div must exist');
  // Find the end: next settings-panel div
  const nextPanel = source.indexOf('<div class="settings-panel', sessionsPanelStart + 1);
  const sessionsPanelContent = source.substring(sessionsPanelStart, nextPanel !== -1 ? nextPanel : sessionsPanelStart + 4000);
  assert.ok(sessionsPanelContent.includes('setting-bell-sound'), 'bell-sound checkbox must be in sessions panel');
});

test('HTML Sessions panel contains desktop notifications request button', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  // Find the sessions PANEL div specifically (not the tab button)
  const sessionsPanelStart = source.indexOf('<div class="settings-panel hidden" data-tab="sessions"');
  assert.ok(sessionsPanelStart !== -1, 'sessions panel div must exist');
  const nextPanel = source.indexOf('<div class="settings-panel', sessionsPanelStart + 1);
  const sessionsPanelContent = source.substring(sessionsPanelStart, nextPanel !== -1 ? nextPanel : sessionsPanelStart + 4000);
  assert.ok(sessionsPanelContent.includes('notification-request-btn'), 'notification-request-btn must be in sessions panel');
});

test('HTML Display panel contains device name input outside #multi-device-fields', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  // Find the display PANEL div specifically (not the tab button)
  const displayPanelStart = source.indexOf('<div class="settings-panel" data-tab="display"');
  assert.ok(displayPanelStart !== -1, 'display panel div must exist');
  const nextPanel = source.indexOf('<div class="settings-panel', displayPanelStart + 1);
  const displayPanelContent = source.substring(displayPanelStart, nextPanel !== -1 ? nextPanel : displayPanelStart + 3000);
  assert.ok(displayPanelContent.includes('setting-device-name'), 'device name input must be in display panel');
  // Must NOT be inside #multi-device-fields
  const multiDeviceIdx = displayPanelContent.indexOf('multi-device-fields');
  const deviceNameIdx = displayPanelContent.indexOf('setting-device-name');
  if (multiDeviceIdx !== -1) {
    assert.ok(deviceNameIdx < multiDeviceIdx, 'device name input must NOT be inside #multi-device-fields');
  }
});

test('HTML index.html has no setting-view-scope select element', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(!source.includes('setting-view-scope'), '#setting-view-scope must be removed from HTML');
});

test('loadGridViewMode always reads from _serverSettings (view scope removed, localStorage ignored)', () => {
  // After removing view scope, loadGridViewMode always reads from _serverSettings
  _localStorageStore = {};
  app._setServerSettings({ gridViewMode: 'grouped' });

  const mode = app.loadGridViewMode();
  // Must return the _serverSettings value ('grouped')
  assert.strictEqual(mode, 'grouped', 'loadGridViewMode must always read from _serverSettings — view scope removed');

  // Cleanup
  _localStorageStore = {};
  app._setServerSettings(null);
});

test('saveGridViewMode always writes to _serverSettings (view scope removed)', () => {
  _localStorageStore = {};
  app._setServerSettings({});

  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true, json: async () => ({}) }; };

  app.saveGridViewMode('filtered');

  // Must call fetch to PATCH server settings
  assert.ok(fetchCalls.length > 0, 'saveGridViewMode must call fetch to PATCH server settings');
  // Must write to _serverSettings
  assert.strictEqual(app._getServerSettings().gridViewMode, 'filtered', 'gridViewMode must be saved to _serverSettings');

  // Cleanup
  globalThis.fetch = origFetch;
  _localStorageStore = {};
  app._setGridViewMode('flat');
  app._setServerSettings(null);
});

test('bindStaticEventListeners does NOT bind change event to setting-view-scope', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function bindStaticEventListeners(');
  assert.ok(fnStart !== -1, 'bindStaticEventListeners must exist');
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1);
  const fnBody = source.substring(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 8000);
  assert.ok(
    !fnBody.includes('setting-view-scope'),
    'bindStaticEventListeners must NOT reference setting-view-scope — view scope removed',
  );
});

// ─── Activity dot / sidebar glow / config toggles (UI/UX improvements) ───────

test('buildTileHTML edge-bar: session-tile--edge-bell class on article element (not a separate DOM element)', () => {
  const session = {
    name: 's',
    bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 },
    snapshot: '',
  };
  const html = app.buildTileHTML(session, 0, false);
  // Edge bar is CSS-only (border-left-color on the article element), no separate DOM element
  assert.ok(html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must be on the article element');
  assert.ok(!html.includes('tile-bell-dot'), 'tile-bell-dot must NOT be present — replaced by edge bar CSS');
  // The class must appear on the opening article tag, not buried in content
  const articleTag = html.substring(0, html.indexOf('>'));
  assert.ok(articleTag.includes('session-tile--edge-bell'), 'edge-bell class must be on the article element');
});

test('buildTileHTML omits bell indicator classes when unseen_count is 0', () => {
  const session = { name: 's', bell: { unseen_count: 0 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('tile-bell-dot'), 'no tile-bell-dot when unseen_count is 0');
  assert.ok(!html.includes('session-tile--edge-bell'), 'no session-tile--edge-bell when unseen_count is 0');
});

test('buildSidebarHTML adds sidebar-item--bell class for bell sessions', () => {
  const session = { name: 's', snapshot: '', bell: { unseen_count: 3, seen_at: null, last_fired_at: 100 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(html.includes('sidebar-item--bell'), 'sidebar item should have sidebar-item--bell class when bell is active');
});

test('buildSidebarHTML does not add sidebar-item--bell when no unseen', () => {
  const session = { name: 's', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(!html.includes('sidebar-item--bell'), 'should NOT have sidebar-item--bell when unseen_count is 0');
});

test('DISPLAY_DEFAULTS includes showDeviceBadges key', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const defaultsStart = source.indexOf('DISPLAY_DEFAULTS');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  assert.ok(defaultsBody.includes('showDeviceBadges'), 'DISPLAY_DEFAULTS must include showDeviceBadges');
});

test('DISPLAY_DEFAULTS does not include showActivityGlow (replaced by activityIndicator)', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const defaultsStart = source.indexOf('DISPLAY_DEFAULTS');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  assert.ok(!defaultsBody.includes('showActivityGlow'), 'DISPLAY_DEFAULTS must NOT include showActivityGlow — replaced by activityIndicator');
});

test('DISPLAY_DEFAULTS includes showHoverPreview key', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const defaultsStart = source.indexOf('DISPLAY_DEFAULTS');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  assert.ok(defaultsBody.includes('showHoverPreview'), 'DISPLAY_DEFAULTS must include showHoverPreview');
});

test('DISPLAY_DEFAULTS does not include showActivityDot (replaced by activityIndicator)', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const defaultsStart = source.indexOf('DISPLAY_DEFAULTS');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  assert.ok(!defaultsBody.includes('showActivityDot'), 'DISPLAY_DEFAULTS must NOT include showActivityDot — replaced by activityIndicator');
});

test('HTML index.html has setting-show-device-badges checkbox', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(source.includes('setting-show-device-badges'), 'Display panel must have setting-show-device-badges checkbox');
});

test('HTML index.html does not have setting-show-activity-glow checkbox (replaced by activity-indicator)', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(!source.includes('setting-show-activity-glow'), 'Display panel must NOT have setting-show-activity-glow — replaced by setting-activity-indicator');
});

test('HTML index.html has setting-show-hover-preview checkbox', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(source.includes('setting-show-hover-preview'), 'Display panel must have setting-show-hover-preview checkbox');
});

test('HTML index.html does not have setting-show-activity-dot checkbox (replaced by activity-indicator)', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(!source.includes('setting-show-activity-dot'), 'Display panel must NOT have setting-show-activity-dot — replaced by setting-activity-indicator');
});

test('CSS style.css has .tile-bell-dot rule', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('.tile-bell-dot'), 'style.css must have .tile-bell-dot rule');
});

test('CSS style.css has .sidebar-item--bell rule', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('.sidebar-item--bell'), 'style.css must have .sidebar-item--bell rule');
});

test('CSS style.css has .sidebar-bell-dot rule', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('.sidebar-bell-dot'), 'style.css must have .sidebar-bell-dot rule');
});

test('showPreview checks showHoverPreview setting before showing popover', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function showPreview(');
  assert.ok(fnStart !== -1, 'showPreview must exist');
  const fnEnd = source.indexOf('\n}', fnStart);
  const fnBody = source.substring(fnStart, fnEnd + 2);
  assert.ok(fnBody.includes('showHoverPreview'), 'showPreview must check showHoverPreview setting before showing popover');
});

test('bindStaticEventListeners binds change events for display toggle controls', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function bindStaticEventListeners(');
  assert.ok(fnStart !== -1, 'bindStaticEventListeners must exist');
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1);
  const fnBody = source.substring(fnStart, fnEnd > fnStart ? fnEnd : fnStart + 10000);
  assert.ok(fnBody.includes('setting-show-device-badges'), 'must bind setting-show-device-badges');
  assert.ok(fnBody.includes('setting-show-hover-preview'), 'must bind setting-show-hover-preview');
  assert.ok(fnBody.includes('setting-activity-indicator'), 'must bind setting-activity-indicator');
});

// --- Activity indicator dropdown (replaces showActivityGlow + showActivityDot toggles) ---

test('DISPLAY_DEFAULTS includes activityIndicator key with value both', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const defaultsStart = source.indexOf('DISPLAY_DEFAULTS');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  assert.ok(defaultsBody.includes('activityIndicator'), 'DISPLAY_DEFAULTS must include activityIndicator');
  assert.ok(defaultsBody.includes("'both'") || defaultsBody.includes('"both"'), "DISPLAY_DEFAULTS activityIndicator default must be 'both'");
});

test('HTML index.html has setting-activity-indicator select element', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(source.includes('setting-activity-indicator'), 'Display panel must have setting-activity-indicator select');
  assert.ok(source.includes('value="both"'), 'must have Dot + Glow option with value both');
  assert.ok(source.includes('value="glow"'), 'must have Glow only option');
  assert.ok(source.includes('value="dot"'), 'must have Dot only option');
  assert.ok(source.includes('value="none"'), 'must have None option');
});

test('buildTileHTML shows session-tile--edge-bell class when activityIndicator is dot (legacy test updated)', () => {
  app._setServerSettings({ activityIndicator: 'dot' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must appear when activityIndicator is dot');
  assert.ok(!html.includes('session-tile--bell'), 'session-tile--bell (glow) must NOT appear when activityIndicator is dot only');
  app._setServerSettings(null);
});

test('buildTileHTML shows both session-tile--bell and session-tile--edge-bell when activityIndicator is both (legacy test updated)', () => {
  app._setServerSettings({ activityIndicator: 'both' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--bell'), 'session-tile--bell must appear when activityIndicator is both');
  assert.ok(html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must appear when activityIndicator is both');
  app._setServerSettings(null);
});

test('buildTileHTML omits all bell indicator classes when activityIndicator is none', () => {
  app._setServerSettings({ activityIndicator: 'none' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('session-tile--bell'), 'session-tile--bell must NOT appear when activityIndicator is none');
  assert.ok(!html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must NOT appear when activityIndicator is none');
  app._setServerSettings(null);
});

test('buildTileHTML omits session-tile--edge-bell when activityIndicator is glow (glow only, no edge bar)', () => {
  app._setServerSettings({ activityIndicator: 'glow' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--bell'), 'session-tile--bell must appear when activityIndicator is glow');
  assert.ok(!html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must NOT appear when activityIndicator is glow');
  app._setServerSettings(null);
});

test('buildTileHTML adds session-tile--bell when activityIndicator is glow', () => {
  app._setServerSettings({ activityIndicator: 'glow' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--bell'), 'session-tile--bell must appear when activityIndicator is glow');
  app._setServerSettings(null);
});

test('buildTileHTML adds session-tile--bell when activityIndicator is both', () => {
  app._setServerSettings({ activityIndicator: 'both' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--bell'), 'session-tile--bell must appear when activityIndicator is both');
  app._setServerSettings(null);
});

test('buildTileHTML omits session-tile--bell when activityIndicator is none', () => {
  app._setServerSettings({ activityIndicator: 'none' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('session-tile--bell'), 'session-tile--bell must NOT appear when activityIndicator is none');
  app._setServerSettings(null);
});

test('CSS style.css has #setting-device-name max-width rule', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('#setting-device-name'), 'style.css must have #setting-device-name rule');
  assert.ok(source.includes('max-width'), 'style.css #setting-device-name rule must include max-width');
});

test('CSS style.css .session-tile--edge-bell sets border-left-color to var(--bell)', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  // Find the .session-tile--edge-bell rule block
  const ruleStart = source.indexOf('.session-tile--edge-bell {');
  assert.ok(ruleStart !== -1, '.session-tile--edge-bell rule must exist');
  const ruleEnd = source.indexOf('}', ruleStart);
  const ruleBody = source.substring(ruleStart, ruleEnd + 1);
  assert.ok(ruleBody.includes('border-left-color') || ruleBody.includes('border-left'), '.session-tile--edge-bell must set border-left-color');
  assert.ok(ruleBody.includes('var(--bell)'), '.session-tile--edge-bell must use var(--bell) color');
});

test('HTML Display panel device name field appears before font size field', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const displayPanelStart = source.indexOf('<div class="settings-panel" data-tab="display"');
  assert.ok(displayPanelStart !== -1, 'display panel must exist');
  const nextPanel = source.indexOf('<div class="settings-panel', displayPanelStart + 1);
  const displayPanelContent = source.substring(displayPanelStart, nextPanel !== -1 ? nextPanel : displayPanelStart + 3000);
  const deviceNameIdx = displayPanelContent.indexOf('setting-device-name');
  const fontSizeIdx = displayPanelContent.indexOf('setting-font-size');
  assert.ok(deviceNameIdx !== -1, 'device name field must be in display panel');
  assert.ok(fontSizeIdx !== -1, 'font size field must be in display panel');
  assert.ok(deviceNameIdx < fontSizeIdx, 'device name must appear before font size in display panel');
});

test('HTML Sessions panel hidden sessions field appears after bell sound', () => {
  const source = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const sessionsPanelStart = source.indexOf('<div class="settings-panel hidden" data-tab="sessions"');
  assert.ok(sessionsPanelStart !== -1, 'sessions panel must exist');
  const nextPanel = source.indexOf('<div class="settings-panel', sessionsPanelStart + 1);
  const sessionsPanelContent = source.substring(sessionsPanelStart, nextPanel !== -1 ? nextPanel : sessionsPanelStart + 4000);
  const hiddenIdx = sessionsPanelContent.indexOf('setting-hidden-sessions');
  const bellSoundIdx = sessionsPanelContent.indexOf('setting-bell-sound');
  assert.ok(hiddenIdx !== -1, 'hidden sessions must be in sessions panel');
  assert.ok(bellSoundIdx !== -1, 'bell sound must be in sessions panel');
  assert.ok(hiddenIdx > bellSoundIdx, 'hidden sessions must appear after bell sound (i.e., near the end)');
});

// --- Verification: cross-origin code removed ---

test('app.js does not export storeFederationToken', () => {
  assert.strictEqual(app.storeFederationToken, undefined, 'storeFederationToken must not be exported');
});

test('app.js does not export buildAuthTileHTML', () => {
  assert.strictEqual(app.buildAuthTileHTML, undefined, 'buildAuthTileHTML must not be exported');
});

test('app.js does not export buildOfflineTileHTML', () => {
  assert.strictEqual(app.buildOfflineTileHTML, undefined, 'buildOfflineTileHTML must not be exported');
});

test('app.js does not export openLoginPopup', () => {
  assert.strictEqual(app.openLoginPopup, undefined, 'openLoginPopup must not be exported');
});

test('app.js does not export formatLastSeen', () => {
  assert.strictEqual(app.formatLastSeen, undefined, 'formatLastSeen must not be exported');
});

test('api() is same-origin only (no baseUrl parameter support)', async () => {
  const calls = [];
  globalThis.fetch = (url, opts) => {
    calls.push({ url, opts });
    return Promise.resolve({ ok: true, json: async () => ([]) });
  };

  await app.api('GET', '/api/sessions');

  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].url, '/api/sessions', 'should call local path directly');
  assert.ok(!calls[0].opts.credentials, 'no credentials:include for same-origin');
  globalThis.fetch = undefined;
});

// ─── Edge-bar design: failing tests added before implementation ───

test('buildTileHTML does NOT include tile-bell-dot in HTML (edge bar replaces dot)', () => {
  app._setServerSettings({ activityIndicator: 'both' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('tile-bell-dot'), 'tile-bell-dot must NOT appear in HTML — edge bar replaces it');
  app._setServerSettings(null);
});

test('buildTileHTML adds session-tile--edge-bell class when activityIndicator is dot', () => {
  app._setServerSettings({ activityIndicator: 'dot' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must appear when activityIndicator is dot');
  app._setServerSettings(null);
});

test('buildTileHTML adds session-tile--edge-bell class when activityIndicator is both', () => {
  app._setServerSettings({ activityIndicator: 'both' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must appear when activityIndicator is both');
  app._setServerSettings(null);
});

test('buildTileHTML does NOT add session-tile--edge-bell when activityIndicator is glow', () => {
  app._setServerSettings({ activityIndicator: 'glow' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must NOT appear when activityIndicator is glow');
  app._setServerSettings(null);
});

test('buildTileHTML does NOT add session-tile--edge-bell when activityIndicator is none', () => {
  app._setServerSettings({ activityIndicator: 'none' });
  const session = { name: 's', bell: { unseen_count: 1, seen_at: null, last_fired_at: 100 }, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(!html.includes('session-tile--edge-bell'), 'session-tile--edge-bell must NOT appear when activityIndicator is none');
  app._setServerSettings(null);
});

test('buildSidebarHTML has single-line header with name, badge, and delete button', () => {
  app._setServerSettings({ multi_device_enabled: true });
  const session = { name: 'my-session', deviceName: 'Laptop', remoteId: 'fed-abc', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  const headerStart = html.indexOf('sidebar-item-header');
  const headerEnd = html.indexOf('</div>', headerStart);
  const headerContent = html.substring(headerStart, headerEnd);
  assert.ok(headerContent.includes('device-badge'), 'device-badge must be inside sidebar-item-header');
  assert.ok(headerContent.includes('sidebar-delete'), 'sidebar-delete must be inside sidebar-item-header');
  app._setServerSettings(null);
});

test('buildSidebarHTML does not have sidebar-item-meta element', () => {
  const session = { name: 'my-session', snapshot: '', bell: { unseen_count: 0 }, last_activity_at: null };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(!html.includes('sidebar-item-meta'), 'sidebar-item-meta must NOT exist in sidebar HTML');
  assert.ok(!html.includes('sidebar-meta-sep'), 'sidebar-meta-sep must NOT exist in sidebar HTML');
  assert.ok(!html.includes('sidebar-item-time'), 'sidebar-item-time must NOT exist in sidebar HTML');
});

test('buildSidebarHTML adds sidebar-item--edge-bell when activityIndicator is dot', () => {
  app._setServerSettings({ activityIndicator: 'dot' });
  const session = { name: 's', snapshot: '', bell: { unseen_count: 2 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(html.includes('sidebar-item--edge-bell'), 'sidebar-item--edge-bell must appear when activityIndicator is dot');
  app._setServerSettings(null);
});

test('buildSidebarHTML adds sidebar-item--edge-bell when activityIndicator is both', () => {
  app._setServerSettings({ activityIndicator: 'both' });
  const session = { name: 's', snapshot: '', bell: { unseen_count: 2 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(html.includes('sidebar-item--edge-bell'), 'sidebar-item--edge-bell must appear when activityIndicator is both');
  app._setServerSettings(null);
});

test('buildSidebarHTML does NOT add sidebar-item--edge-bell when activityIndicator is glow', () => {
  app._setServerSettings({ activityIndicator: 'glow' });
  const session = { name: 's', snapshot: '', bell: { unseen_count: 2 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(!html.includes('sidebar-item--edge-bell'), 'sidebar-item--edge-bell must NOT appear when activityIndicator is glow');
  app._setServerSettings(null);
});

test('buildSidebarHTML does NOT include tile-bell-dot in HTML', () => {
  app._setServerSettings({ activityIndicator: 'both' });
  const session = { name: 's', snapshot: '', bell: { unseen_count: 2 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(!html.includes('tile-bell-dot'), 'tile-bell-dot must NOT appear in sidebar HTML — edge bar replaces it');
  app._setServerSettings(null);
});

test('CSS style.css has .session-tile--edge-bell rule', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('.session-tile--edge-bell'), 'style.css must have .session-tile--edge-bell rule');
});

test('CSS style.css has .sidebar-item--edge-bell rule', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('.sidebar-item--edge-bell'), 'style.css must have .sidebar-item--edge-bell rule');
});

test('CSS style.css .session-tile has border-left for edge bar', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  const tileStart = source.indexOf('.session-tile {');
  assert.ok(tileStart !== -1, '.session-tile rule must exist');
  const tileEnd = source.indexOf('}', tileStart);
  const tileBody = source.substring(tileStart, tileEnd + 1);
  assert.ok(tileBody.includes('border-left'), '.session-tile must have border-left for edge bar');
});

test('CSS style.css has .sidebar-item-header .device-badge rule for badge right-alignment', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('.sidebar-item-header .device-badge'), 'style.css must have .sidebar-item-header .device-badge rule for badge alignment in single-line header');
});

test('CSS style.css .tile-meta has opacity transition for crossfade (badge + timestamp together)', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(
    source.includes("session-tile:hover .tile-meta"),
    'style.css must have session-tile:hover .tile-meta for crossfade'
  );
  assert.ok(
    source.includes("session-tile:focus-within .tile-meta"),
    'style.css must have session-tile:focus-within .tile-meta for crossfade'
  );
});

test('CSS style.css has .tile-meta-sep style', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(source.includes('.tile-meta-sep'), 'style.css must have .tile-meta-sep rule');
});

// --- Trailing blank line trimming in snapshot previews ---

test('buildTileHTML trims trailing blank lines so pre ends with last content line', () => {
  // Simulates a session where cursor is near the top: 2 content lines, then 18 blank lines.
  // slice(-20) grabs all 20 lines (2 content + 18 blanks).
  // Without trimming, the <pre> ends with blank lines (last meaningful content is invisible).
  // With trimming, the <pre> ends with the last non-blank line.
  const contentLines = ['$ tunnel up', '  forwarding 8443...'];
  const blankLines = new Array(18).fill('');
  const snapshot = contentLines.concat(blankLines).join('\n');

  const session = { name: 'tunnel', snapshot };
  const html = app.buildTileHTML(session, 0, false);

  // The pre element must contain the content lines
  assert.ok(html.includes('tunnel up'), 'tile preview must include content line');
  assert.ok(html.includes('forwarding 8443'), 'tile preview must include second content line');

  // The pre should NOT end with blank lines (trailing whitespace trimmed)
  const preMatch = html.match(/<pre>([\s\S]*?)<\/pre>/);
  assert.ok(preMatch, '<pre> element must exist in tile HTML');
  const preContent = preMatch[1];
  const lastLine = preContent.split('\n').pop();
  assert.ok(lastLine.trim() !== '',
    'last line of pre content must not be blank after trimming trailing blank lines');
});

test('buildSidebarHTML trims trailing blank lines so pre ends with last content line', () => {
  // Same scenario: 2 content lines at top, 18 blank lines below.
  const contentLines = ['$ tunnel up', '  forwarding 8443...'];
  const blankLines = new Array(18).fill('');
  const snapshot = contentLines.concat(blankLines).join('\n');

  const session = { name: 'tunnel', snapshot, bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');

  assert.ok(html.includes('tunnel up'), 'sidebar preview must include content line');
  assert.ok(html.includes('forwarding 8443'), 'sidebar preview must include second content line');

  const preMatch = html.match(/<pre>([\s\S]*?)<\/pre>/);
  assert.ok(preMatch, '<pre> element must exist in sidebar HTML');
  const preContent = preMatch[1];
  const lastLine = preContent.split('\n').pop();
  assert.ok(lastLine.trim() !== '',
    'last line of sidebar pre content must not be blank after trimming trailing blank lines');
});

test('buildTileHTML renders empty pre when snapshot is entirely blank lines', () => {
  // Edge case: completely empty session — pre should be empty, not show garbage
  const snapshot = new Array(30).fill('').join('\n');
  const session = { name: 'empty-session', snapshot };
  const html = app.buildTileHTML(session, 0, false);
  const preMatch = html.match(/<pre>([\s\S]*?)<\/pre>/);
  assert.ok(preMatch, '<pre> element must exist');
  // pre content should be empty string (all blanks trimmed away)
  assert.strictEqual(preMatch[1], '', 'pre should be empty when snapshot has only blank lines');
});

test('buildSidebarHTML renders empty pre when snapshot is entirely blank lines', () => {
  const snapshot = new Array(25).fill('').join('\n');
  const session = { name: 'empty-session', snapshot, bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  const preMatch = html.match(/<pre>([\s\S]*?)<\/pre>/);
  assert.ok(preMatch, '<pre> element must exist');
  assert.strictEqual(preMatch[1], '', 'sidebar pre should be empty when snapshot has only blank lines');
});

// --- Trim BEFORE slice: 40-row terminal with content at top ---
// The real bug: a 40-row terminal with content at rows 1-2 and rows 3-40 blank.
// slice(-20) grabs the LAST 20 rows → all blank. Then trim-after-slice removes
// everything → empty pre. Fix: trim trailing blanks from the full snapshot FIRST,
// then slice the last 20 of what remains.

test('buildTileHTML shows content from top of 40-row terminal (trim BEFORE slice)', () => {
  // 40 lines: 2 content lines + 38 trailing blank lines (realistic 40-row terminal,
  // cursor near top — e.g. a fresh ssh tunnel session)
  const snapshot = 'TUNNEL_CONTENT_LINE\nstatus: connected\n' + '\n'.repeat(38);
  const session = { name: 'tunnel', snapshot };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(
    html.includes('TUNNEL_CONTENT_LINE'),
    'tile must show content from top of terminal even with 38 trailing blank lines — trim full snapshot BEFORE slice(-20)',
  );
});

test('buildSidebarHTML shows content from top of 40-row terminal (trim BEFORE slice)', () => {
  const snapshot = 'TUNNEL_CONTENT_LINE\nstatus: connected\n' + '\n'.repeat(38);
  const session = { name: 'tunnel', snapshot, bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(
    html.includes('TUNNEL_CONTENT_LINE'),
    'sidebar must show content from top of terminal even with 38 trailing blank lines — trim full snapshot BEFORE slice(-20)',
  );
});

test('buildTileHTML trim happens BEFORE slice in source (structural order check)', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function buildTileHTML');
  const fnBody = source.substring(fnStart, fnStart + 2000);
  const trimIdx = fnBody.indexOf('.pop()');
  const sliceIdx = fnBody.indexOf('.slice(');
  assert.ok(
    trimIdx < sliceIdx,
    'trailing blank trim (.pop()) must appear BEFORE .slice() in buildTileHTML — trim the full snapshot first, then slice the last N lines',
  );
});

test('buildSidebarHTML trim happens BEFORE slice in source (structural order check)', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function buildSidebarHTML');
  const fnBody = source.substring(fnStart, fnStart + 2000);
  const trimIdx = fnBody.indexOf('.pop()');
  const sliceIdx = fnBody.indexOf('.slice(');
  assert.ok(
    trimIdx < sliceIdx,
    'trailing blank trim (.pop()) must appear BEFORE .slice() in buildSidebarHTML — trim the full snapshot first, then slice the last 20 lines',
  );
});

// --- federation key: remote instance input listener includes .settings-remote-key ---

test('remote instance debounced input listener selector includes .settings-remote-key', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes(".settings-remote-url, .settings-remote-name, .settings-remote-key"),
    'debounced input listener on #setting-remote-instances must include .settings-remote-key so key-only edits trigger _saveRemoteInstances()',
  );
});

// --- Bug fixes: delete UX ---

test('killSession closes active session and returns to dashboard', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Find killSession function body using brace-counting extraction
  const fnStart = source.indexOf('function killSession');
  const afterStart = source.indexOf('{', fnStart);
  let depth = 0, bodyEnd = -1;
  for (let i = afterStart; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') { depth--; if (depth === 0) { bodyEnd = i; break; } }
  }
  const fnBody = source.substring(fnStart, bodyEnd + 1);
  assert.ok(fnBody.includes('_viewingSession'), 'killSession must check if deleted session is the active one');
  assert.ok(fnBody.includes('closeSession'), 'killSession must call closeSession when deleting the active session');
});

test('sidebar click handler ignores clicks on delete button', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes("closest('.sidebar-delete')"),
    "sidebar click handler must guard against clicks on .sidebar-delete button"
  );
});

test('tile click handler ignores clicks on tile-delete button', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes("closest('.tile-delete')"),
    "tile click handler must guard against clicks on .tile-delete button"
  );
});

// --- index.html: self-hosted vendor libs (no CDN) ---

test('index.html loads xterm.css from local /vendor/ path (not CDN)', () => {
  const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(
    html.includes('href="/vendor/xterm.css"'),
    'index.html must reference /vendor/xterm.css (not CDN)',
  );
  assert.ok(
    !html.includes('cdn.jsdelivr.net'),
    'index.html must NOT reference cdn.jsdelivr.net',
  );
});

test('index.html loads all 5 xterm JS scripts from local /vendor/ paths (not CDN)', () => {
  const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const vendorScripts = [
    '/vendor/xterm.js',
    '/vendor/xterm-addon-fit.js',
    '/vendor/xterm-addon-web-links.js',
    '/vendor/xterm-addon-search.js',
    '/vendor/addon-image.js',
  ];
  for (const script of vendorScripts) {
    assert.ok(
      html.includes(`src="${script}"`),
      `index.html must include <script src="${script}">`,
    );
  }
  assert.ok(
    !html.includes('cdn.jsdelivr.net'),
    'index.html must NOT reference cdn.jsdelivr.net',
  );
});

// --- remoteId=0 falsy-zero bug fixes ---
// remoteId is an integer index (0, 1, 2...). When remoteId=0, all || '' patterns
// evaluate to '' because 0 is falsy in JS — causing the first remote to be treated
// as a local session (404 on connect).

test('buildTileHTML with integer remoteId=0 includes data-remote-id="0" attribute', () => {
  const session = { name: 'alienware-session', remoteId: 0, snapshot: '' };
  const html = app.buildTileHTML(session, 0, false);
  assert.ok(
    html.includes('data-remote-id="0"'),
    'buildTileHTML must emit data-remote-id="0" when session.remoteId === 0 (not omit it)',
  );
});

test('buildSidebarHTML with integer remoteId=0 includes data-remote-id="0" (not empty)', () => {
  const session = { name: 'alienware-session', remoteId: 0, snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, '');
  assert.ok(
    html.includes('data-remote-id="0"'),
    'buildSidebarHTML must emit data-remote-id="0" when session.remoteId === 0',
  );
  assert.ok(
    !html.includes('data-remote-id=""'),
    'buildSidebarHTML must NOT emit data-remote-id="" when session.remoteId === 0',
  );
});

test('openSession with integer remoteId=0 POSTs to federation proxy URL, not local', async () => {
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

  // remoteId is the integer 0 — as returned by /api/federation/sessions for the first remote
  await app.openSession('muxplex-updates', { remoteId: 0 });

  const federationCall = fetchCalls.find((c) => c.url === '/api/federation/0/connect/muxplex-updates');
  const localCall = fetchCalls.find((c) => c.url === '/api/sessions/muxplex-updates/connect');

  assert.ok(federationCall, 'should POST to /api/federation/0/connect/muxplex-updates (not local endpoint)');
  assert.ok(!localCall, 'must NOT POST to /api/sessions/muxplex-updates/connect (local endpoint gives 404)');
  assert.strictEqual(federationCall.opts.method, 'POST');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('openSession with integer remoteId=0 passes 0 to window._openTerminal as second arg', async () => {
  let openTerminalArgs = null;
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async () => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = (fn) => { fn(); };
  globalThis.window._openTerminal = (...args) => { openTerminalArgs = args; };

  await app.openSession('muxplex-updates', { remoteId: 0 });

  assert.ok(openTerminalArgs !== null, '_openTerminal should have been called');
  assert.strictEqual(openTerminalArgs[0], 'muxplex-updates', '_openTerminal first arg should be session name');
  assert.ok(openTerminalArgs[1] === 0 || openTerminalArgs[1] === '0', '_openTerminal second arg should be remoteId 0');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('closeSession after openSession with remoteId=0 does NOT fire DELETE /api/sessions/current', async () => {
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;

  // Open a remote session with remoteId=0 — sets _viewingRemoteId = 0
  globalThis.fetch = async () => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = () => {};
  globalThis.window._openTerminal = () => {};
  globalThis.window._closeTerminal = () => {};

  await app.openSession('muxplex-updates', { remoteId: 0 });

  // Restore setTimeout so Promise-based yielding works
  globalThis.setTimeout = origSetTimeout;

  // Reset fetch tracking
  const fetchCalls = [];
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };

  await app.closeSession();
  await new Promise((r) => setTimeout(r, 0));

  const deleteCall = fetchCalls.find((c) => c.url === '/api/sessions/current' && c.opts && c.opts.method === 'DELETE');
  assert.ok(!deleteCall, 'closeSession must NOT fire DELETE for remoteId=0 session (it is a remote session)');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('updatePageTitle function exists and uses activity count', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(source.includes('function updatePageTitle'), 'must have updatePageTitle function');
  assert.ok(source.includes('unseen_count'), 'must count unseen bells for title');
  assert.ok(source.includes('document.title'), 'must set document.title');
  assert.ok(source.includes('location.hostname'), 'must fall back to location.hostname');
});

test('updatePageTitle is called from pollSessions', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Use 700 chars — the call is ~562 chars into the function after comments/whitespace
  const pollFn = source.substring(source.indexOf('async function pollSessions'), source.indexOf('async function pollSessions') + 700);
  assert.ok(pollFn.includes('updatePageTitle'), 'pollSessions must call updatePageTitle');
});

// --- _createDeviceSelect and showNewSessionInput multi-device tests ---

test('_createDeviceSelect builds a <select> with Local + remote options', () => {
  // Verify function is exported
  assert.strictEqual(typeof app._createDeviceSelect, 'function', '_createDeviceSelect must be exported');

  // Set up server settings with multi_device_enabled + remote_instances + device_name
  app._setServerSettings({
    multi_device_enabled: true,
    remote_instances: [{ name: 'Remote A', url: 'http://a' }],
    device_name: 'MyDevice',
  });

  // Mock document.createElement to capture created elements
  const origCE = globalThis.document.createElement;
  const builtOptions = [];
  let selectEl = null;

  globalThis.document.createElement = (tag) => {
    if (tag === 'select') {
      selectEl = {
        tagName: 'SELECT',
        className: '',
        value: '',
        options: builtOptions,
        appendChild: (child) => { builtOptions.push(child); },
        addEventListener: () => {},
      };
      return selectEl;
    }
    // option elements
    return { tagName: tag.toUpperCase(), value: '', textContent: '', selected: false };
  };

  const result = app._createDeviceSelect();
  globalThis.document.createElement = origCE;

  assert.ok(result !== null, '_createDeviceSelect must return non-null when multi_device_enabled + remotes present');
  assert.strictEqual(result.className, 'new-session-device-select', 'select must have className new-session-device-select');
  assert.strictEqual(builtOptions.length, 2, 'must have 2 options: local + 1 remote');
  assert.strictEqual(builtOptions[0].value, '', 'first option value must be empty string (local)');
  assert.strictEqual(builtOptions[0].textContent, 'MyDevice', 'first option text must use device_name');
  assert.strictEqual(builtOptions[1].value, '0', 'remote option value must be "0" (String(index))');
  assert.strictEqual(builtOptions[1].textContent, 'Remote A', 'remote option text must use remote.name');
});

test('showNewSessionInput creates device select when multi_device_enabled with remotes', () => {
  app._setServerSettings({
    multi_device_enabled: true,
    remote_instances: [{ name: 'Remote B', url: 'http://b' }],
    device_name: 'LocalDev',
  });

  const origCE = globalThis.document.createElement;
  const createdTags = [];
  const insertedEls = [];

  globalThis.document.createElement = (tag) => {
    createdTags.push(tag);
    return {
      tagName: tag.toUpperCase(),
      className: '',
      type: '',
      placeholder: '',
      autocomplete: '',
      spellcheck: false,
      value: '',
      style: {},
      options: [],
      appendChild: () => {},
      addEventListener: () => {},
      focus: () => {},
    };
  };

  const btn = {
    style: {},
    parentNode: {
      insertBefore: (el) => { insertedEls.push(el); },
    },
  };

  app.showNewSessionInput(btn);
  globalThis.document.createElement = origCE;

  assert.ok(createdTags.includes('select'), 'showNewSessionInput must create a <select> element when multi_device_enabled');
});

test('showNewSessionInput passes remoteId from device select to createNewSession', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Extract showNewSessionInput function body
  const fnStart = source.indexOf('function showNewSessionInput(');
  assert.ok(fnStart !== -1, 'showNewSessionInput function must exist');
  const fnBody = source.substring(fnStart, fnStart + 1200);
  // Must call _createDeviceSelect
  assert.ok(fnBody.includes('_createDeviceSelect'), 'showNewSessionInput must call _createDeviceSelect');
  // Must read remoteId from select.value (or equivalent)
  assert.ok(
    fnBody.includes('remoteId') && (fnBody.includes('select.value') || fnBody.includes('sel.value')),
    'showNewSessionInput Enter handler must read remoteId from select element value',
  );
  // Must call createNewSession with two arguments (name and remoteId)
  assert.ok(
    fnBody.includes('createNewSession(name') && fnBody.includes('remoteId'),
    'showNewSessionInput must call createNewSession with name and remoteId arguments',
  );
});

test('showFabSessionInput creates device select when multi_device_enabled with remotes', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Extract showFabSessionInput function body
  const fnStart = source.indexOf('function showFabSessionInput(');
  assert.ok(fnStart !== -1, 'showFabSessionInput function must exist');
  const fnBody = source.substring(fnStart, fnStart + 1200);
  // Must call _createDeviceSelect
  assert.ok(fnBody.includes('_createDeviceSelect'), 'showFabSessionInput must call _createDeviceSelect');
  // Must read remoteId from select.value (or equivalent)
  assert.ok(
    fnBody.includes('remoteId') && (fnBody.includes('select.value') || fnBody.includes('sel.value')),
    'showFabSessionInput Enter handler must read remoteId from select element value',
  );
  // Must call createNewSession with two arguments (name and remoteId)
  assert.ok(
    fnBody.includes('createNewSession(name') && fnBody.includes('remoteId'),
    'showFabSessionInput must call createNewSession with name and remoteId arguments',
  );
});

// --- createNewSession federation routing (task-4) ---

test('createNewSession accepts remoteId parameter and routes to federation endpoint', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Extract createNewSession function body
  const fnStart = source.indexOf('async function createNewSession(');
  assert.ok(fnStart !== -1, 'createNewSession function must exist');
  const fnBody = source.substring(fnStart, fnStart + 2000);
  // Must accept remoteId parameter
  assert.ok(
    fnBody.includes('remoteId'),
    'createNewSession must accept remoteId parameter',
  );
  // Must include federation endpoint path
  assert.ok(
    fnBody.includes('/api/federation/'),
    'createNewSession must include /api/federation/ endpoint path for remote routing',
  );
  // Must still have local /api/sessions endpoint
  assert.ok(
    fnBody.includes('/api/sessions'),
    'createNewSession must still use /api/sessions for local sessions',
  );
});

test('createNewSession passes remoteId through to openSession for auto-open', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('async function createNewSession(');
  assert.ok(fnStart !== -1, 'createNewSession function must exist');
  // Use 3000 chars to cover the full function including the polling interval callback
  const fnBody = source.substring(fnStart, fnStart + 3000);
  // Must call openSession with remoteId option
  assert.ok(
    fnBody.includes('openSession') && fnBody.includes('remoteId'),
    'createNewSession must call openSession with remoteId option',
  );
  // Must pass remoteId as an option object to openSession
  assert.ok(
    fnBody.includes('{ remoteId') || fnBody.includes('{ remoteId:'),
    'createNewSession must pass remoteId as option object to openSession',
  );
});

test('createNewSession matches remote sessions by sessionKey in poll loop', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('async function createNewSession(');
  assert.ok(fnStart !== -1, 'createNewSession function must exist');
  const fnBody = source.substring(fnStart, fnStart + 2000);
  // Must use sessionKey in the match logic (with fallback to name)
  assert.ok(
    fnBody.includes('sessionKey'),
    'createNewSession poll loop must match remote sessions by sessionKey',
  );
  // Must use expectedKey or similar computed key for comparison
  assert.ok(
    fnBody.includes('expectedKey') || (fnBody.includes('sessionKey') && fnBody.includes('remoteId')),
    'createNewSession must compute expected sessionKey for remote session matching',
  );
});

test('CSS has new-session-device-select styling', () => {
  const source = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');
  assert.ok(
    source.includes('.new-session-device-select'),
    'style.css must have .new-session-device-select rule',
  );
});

// --- Fix: blur handler guards against closing when clicking device select ---

test('showNewSessionInput blur does not close when clicking device select', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  // Find the blur handler in showNewSessionInput
  const fnStart = source.indexOf('function showNewSessionInput');
  const fnBody = source.substring(fnStart, fnStart + 2000);
  // Must check activeElement before cleanup
  assert.ok(fnBody.includes('activeElement'), 'blur handler must check activeElement before cleanup');
});

test('showFabSessionInput blur does not close when clicking device select', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function showFabSessionInput');
  const fnBody = source.substring(fnStart, fnStart + 2000);
  assert.ok(fnBody.includes('activeElement'), 'FAB blur handler must check activeElement before cleanup');
});

test('showNewSessionInput device select has blur handler that guards against closing when focus returns to input', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function showNewSessionInput');
  const fnBody = source.substring(fnStart, fnStart + 2000);
  // The select element should also have a blur handler
  // We check that there's a blur listener added to the select element
  const selectBlurIdx = fnBody.indexOf("select.addEventListener('blur'");
  assert.ok(selectBlurIdx !== -1, 'select element must have a blur handler in showNewSessionInput');
});

test('showNewSessionInput device select has keydown handler for Escape', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const fnStart = source.indexOf('function showNewSessionInput');
  const fnBody = source.substring(fnStart, fnStart + 2000);
  const selectKeydownIdx = fnBody.indexOf("select.addEventListener('keydown'");
  assert.ok(selectKeydownIdx !== -1, 'select element must have a keydown handler in showNewSessionInput');
});

// --- Fix: remote sessions fail to connect because openSession call sites don't pass remoteId ---

test('restoreState reads active_remote_id from state and passes it to openSession', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const restoreIdx = source.indexOf('async function restoreState');
  assert.ok(restoreIdx >= 0, 'restoreState must exist');
  const restoreEnd = source.indexOf('\n}', restoreIdx) + 2;
  const restoreBody = source.slice(restoreIdx, restoreEnd);
  assert.ok(
    restoreBody.includes('active_remote_id'),
    'restoreState must read active_remote_id from persisted state',
  );
  assert.ok(
    restoreBody.includes('remoteId'),
    'restoreState must pass remoteId to openSession so remote sessions restore correctly',
  );
});

test('renderSheetList click handler passes remoteId to openSession', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const sheetIdx = source.indexOf('function renderSheetList');
  assert.ok(sheetIdx >= 0, 'renderSheetList must exist');
  // Extract the full function body (up to the closing brace)
  const sheetEnd = source.indexOf('\n}', sheetIdx) + 2;
  const sheetBody = source.slice(sheetIdx, sheetEnd);
  assert.ok(
    sheetBody.includes('remoteId'),
    'renderSheetList click handler must pass remoteId to openSession',
  );
});

test('renderSheetList item HTML includes data-remote-id attribute for remote sessions', () => {
  let capturedHTML = '';
  const mockList = {
    get innerHTML() { return capturedHTML; },
    set innerHTML(v) { capturedHTML = v; },
    querySelectorAll: () => [],
  };
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => (id === 'sheet-list' ? mockList : null);
  app._setCurrentSessions([{ name: 'remote-sess', remoteId: 'fed-abc123', bell: null }]);

  app.renderSheetList();

  assert.ok(
    capturedHTML.includes('data-remote-id="fed-abc123"'),
    'sheet item should have data-remote-id attribute for remote sessions',
  );
  globalThis.document.getElementById = origGetById;
});

test('openSession PATCHes /api/state with active_remote_id after successful connect', async () => {
  const fetchCalls = [];
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = (fn) => { fn(); };  // invoke immediately so animation resolves
  globalThis.window._openTerminal = () => {};

  await app.openSession('remote-session', { remoteId: 'fed-abc123' });

  const patchCall = fetchCalls.find((c) => c.url === '/api/state' && c.opts && c.opts.method === 'PATCH');
  assert.ok(patchCall, 'openSession should PATCH /api/state after successful connect');
  const body = JSON.parse(patchCall.opts.body);
  assert.strictEqual(body.active_remote_id, 'fed-abc123', 'PATCH body should include active_remote_id');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

test('closeSession PATCHes /api/state to clear active_remote_id', async () => {
  const origFetch = globalThis.fetch;
  const origGetById = globalThis.document.getElementById;
  const origQS = globalThis.document.querySelector;
  const origSetTimeout = globalThis.setTimeout;

  // Open a remote session first so _viewingRemoteId is set
  globalThis.fetch = async () => ({ ok: true });
  globalThis.document.getElementById = () => ({ textContent: '', style: {}, classList: { remove: () => {}, add: () => {} } });
  globalThis.document.querySelector = () => null;
  globalThis.setTimeout = (fn) => { fn(); };
  globalThis.window._openTerminal = () => {};
  globalThis.window._closeTerminal = () => {};

  await app.openSession('remote-sess', { remoteId: 'fed-abc123' });

  globalThis.setTimeout = origSetTimeout;

  // Reset fetch tracking
  const fetchCalls = [];
  globalThis.fetch = async (url, opts) => { fetchCalls.push({ url, opts }); return { ok: true }; };

  await app.closeSession();
  await new Promise((r) => setTimeout(r, 0));

  const patchCall = fetchCalls.find((c) => c.url === '/api/state' && c.opts && c.opts.method === 'PATCH');
  assert.ok(patchCall, 'closeSession should PATCH /api/state to clear remote session state');
  const body = JSON.parse(patchCall.opts.body);
  assert.strictEqual(body.active_remote_id, null, 'PATCH should clear active_remote_id to null');

  globalThis.fetch = origFetch;
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelector = origQS;
  globalThis.setTimeout = origSetTimeout;
});

// ─── Task 2: Replace localStorage storage layer with server-settings-cache ───

test('DISPLAY_SETTINGS_KEY constant is deleted from app.js source', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    !source.includes('const DISPLAY_SETTINGS_KEY'),
    'DISPLAY_SETTINGS_KEY constant declaration must be deleted from app.js'
  );
});

test('SIDEBAR_KEY constant declaration is deleted from app.js source', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  assert.ok(
    !source.includes('const SIDEBAR_KEY'),
    'SIDEBAR_KEY constant declaration must be deleted from app.js'
  );
});

test('DISPLAY_DEFAULTS does not include notificationPermission', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const defaultsStart = source.indexOf('const DISPLAY_DEFAULTS');
  assert.ok(defaultsStart !== -1, 'DISPLAY_DEFAULTS must exist');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  assert.ok(
    !defaultsBody.includes('notificationPermission'),
    'DISPLAY_DEFAULTS must NOT include notificationPermission'
  );
});

test('DISPLAY_DEFAULTS includes gridViewMode with default flat', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const defaultsStart = source.indexOf('const DISPLAY_DEFAULTS');
  assert.ok(defaultsStart !== -1, 'DISPLAY_DEFAULTS must exist');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  assert.ok(defaultsBody.includes('gridViewMode'), 'DISPLAY_DEFAULTS must include gridViewMode');
  assert.ok(
    defaultsBody.includes("'flat'") || defaultsBody.includes('"flat"'),
    "DISPLAY_DEFAULTS gridViewMode default must be 'flat'"
  );
});

test('DISPLAY_DEFAULTS has exactly 9 keys', () => {
  const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const defaultsStart = source.indexOf('const DISPLAY_DEFAULTS');
  assert.ok(defaultsStart !== -1, 'DISPLAY_DEFAULTS must exist');
  const defaultsEnd = source.indexOf('};', defaultsStart);
  const defaultsBody = source.substring(defaultsStart, defaultsEnd + 2);
  const keyMatches = defaultsBody.match(/^\s+\w+:/gm);
  assert.ok(keyMatches, 'DISPLAY_DEFAULTS must have keys');
  assert.strictEqual(keyMatches.length, 9, `DISPLAY_DEFAULTS must have exactly 9 keys, got ${keyMatches.length}`);
});

test('getDisplaySettings is exported from app.js', () => {
  assert.ok('getDisplaySettings' in app, 'app.js must export getDisplaySettings');
  assert.strictEqual(typeof app.getDisplaySettings, 'function', 'getDisplaySettings must be a function');
});

test('loadDisplaySettings is NOT exported from app.js', () => {
  assert.ok(!('loadDisplaySettings' in app), 'app.js must NOT export loadDisplaySettings (replaced by getDisplaySettings)');
});

test('saveDisplaySettings is NOT exported from app.js', () => {
  assert.ok(!('saveDisplaySettings' in app), 'app.js must NOT export saveDisplaySettings (deleted)');
});

test('getDisplaySettings returns DISPLAY_DEFAULTS when _serverSettings is null', () => {
  app._setServerSettings(null);
  const ds = app.getDisplaySettings();
  assert.strictEqual(ds.fontSize, 14, 'getDisplaySettings must return default fontSize');
  assert.strictEqual(ds.hoverPreviewDelay, 1500, 'getDisplaySettings must return default hoverPreviewDelay');
  assert.strictEqual(ds.gridColumns, 'auto', 'getDisplaySettings must return default gridColumns');
  assert.strictEqual(ds.bellSound, false, 'getDisplaySettings must return default bellSound');
  assert.strictEqual(ds.viewMode, 'auto', 'getDisplaySettings must return default viewMode');
  assert.strictEqual(ds.showDeviceBadges, true, 'getDisplaySettings must return default showDeviceBadges');
  assert.strictEqual(ds.showHoverPreview, true, 'getDisplaySettings must return default showHoverPreview');
  assert.strictEqual(ds.activityIndicator, 'both', 'getDisplaySettings must return default activityIndicator');
  assert.strictEqual(ds.gridViewMode, 'flat', 'getDisplaySettings must return default gridViewMode');
});

test('getDisplaySettings reads display keys from _serverSettings with DISPLAY_DEFAULTS fallback', () => {
  app._setServerSettings({ fontSize: 18, viewMode: 'fit', unknownKey: 'ignored' });
  const ds = app.getDisplaySettings();
  assert.strictEqual(ds.fontSize, 18, 'getDisplaySettings must use fontSize from _serverSettings');
  assert.strictEqual(ds.viewMode, 'fit', 'getDisplaySettings must use viewMode from _serverSettings');
  assert.strictEqual(ds.hoverPreviewDelay, 1500, 'getDisplaySettings must fall back to default hoverPreviewDelay');
  assert.strictEqual(ds.gridViewMode, 'flat', 'getDisplaySettings must fall back to default gridViewMode');
  assert.ok(!('unknownKey' in ds), 'getDisplaySettings must not include keys not in DISPLAY_DEFAULTS');
  app._setServerSettings(null);
});

// --- getVisibleSessions: remoteId=0 falsy-zero bug fix ---

test('getVisibleSessions hides remote sessions with remoteId=0 when name is in hidden_sessions', () => {
  // The filter no longer guards on remoteId — hidden_sessions applies to all sessions,
  // local and federated alike (settings now sync across nodes).
  app._setServerSettings({ hidden_sessions: ['work'] });
  const sessions = [{ name: 'work', remoteId: 0 }];
  const visible = app.getVisibleSessions(sessions);
  assert.strictEqual(visible.length, 0, 'session with remoteId=0 must be hidden when its name is in hidden_sessions');
  app._setServerSettings(null);
});

test('getVisibleSessions hides all sessions (local and remoteId=0) with names in hidden_sessions', () => {
  // Both the local session (remoteId=null) and the remote session (remoteId=0)
  // should be hidden when hidden_sessions syncs across federation nodes.
  app._setServerSettings({ hidden_sessions: ['work'] });
  const sessions = [
    { name: 'work', remoteId: null },
    { name: 'work', remoteId: 0 },
  ];
  const visible = app.getVisibleSessions(sessions);
  assert.strictEqual(visible.length, 0, 'both local and remote sessions named "work" should be hidden');
  app._setServerSettings(null);
});

// --- task-2: updatePageTitle and updateFaviconBadge filter hidden sessions ---

test('updatePageTitle excludes hidden sessions from bell count', () => {
  // Set up server settings with hidden_sessions
  app._setServerSettings({ hidden_sessions: ['hidden-build'] });

  // Create two sessions: visible-dev (unseen_count 2) and hidden-build (unseen_count 5)
  app._setCurrentSessions([
    { name: 'visible-dev', bell: { unseen_count: 2 } },
    { name: 'hidden-build', bell: { unseen_count: 5 } },
  ]);

  // Call updatePageTitle
  app.updatePageTitle();

  // Assert title starts with '(1)' — only visible-dev counts, not hidden-build
  assert.ok(
    document.title.startsWith('(1)'),
    `title must start with '(1)' when only 1 visible session has bells, got '${document.title}'`
  );

  // Clean up
  app._setServerSettings(null);
  app._setCurrentSessions([]);
});

test('updateFaviconBadge does not show activity for only-hidden sessions with bells', () => {
  // Set up server settings with hidden_sessions
  app._setServerSettings({ hidden_sessions: ['hidden-build'] });

  // Create only a hidden session with bell activity
  app._setCurrentSessions([
    { name: 'hidden-build', bell: { unseen_count: 5 } },
  ]);

  // Mock document.querySelector to return a fake link element
  const origQS = globalThis.document.querySelector;
  const faviconHref = 'http://localhost/favicon.ico';
  const fakeLink = { href: faviconHref };
  globalThis.document.querySelector = (sel) => {
    if (sel && sel.includes('icon')) return fakeLink;
    return null;
  };

  // Call updateFaviconBadge — should NOT draw badge since no visible sessions have bells
  app.updateFaviconBadge();

  // favicon href must not change — no badge should be applied
  assert.strictEqual(
    fakeLink.href,
    faviconHref,
    'favicon href must not change when only hidden sessions have bell activity'
  );

  // Restore mocks and state
  globalThis.document.querySelector = origQS;
  app._setServerSettings(null);
  app._setCurrentSessions([]);
});

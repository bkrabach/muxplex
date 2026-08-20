// Session-name filter (muxplex-4h9): fnmatch-style glob matching that
// narrows the grid/sidebar down to sessions whose name matches a
// user-typed pattern, without muting the favicon badge / page title /
// mobile bottom sheet for a filtered-out session.
//
// Group A consumes the SAME cases as:
// - muxplex/tests/test_session_filter_fixture.py (`views.matches_name_pattern()`), same repo
//
// docs/API_SEMANTICS.md's `settings.session_filter` entry requires the
// client-side glob mirror to agree with the server's real fnmatch-backed
// matcher for every case. This fixture is the mechanism that turns a drift
// between the two into a test FAILURE instead of a silent divergence.

// Browser global stubs -- must be set before importing app.js (mirrors test_attention_fixture.mjs).
globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

globalThis.document = {
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => ({ style: {}, classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false } }),
  addEventListener: () => {},
  removeEventListener: () => {},
  activeElement: null,
};

globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
  innerWidth: 1024,
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

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const require = createRequire(import.meta.url);
const app = require(join(__dirname, '..', 'app.js'));

const fixturePath = join(__dirname, '..', '..', 'tests', 'fixtures', 'session_filter_cases.json');
const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

// ---------------------------------------------------------------------------
// Group A -- fixture-driven matcher agreement
// ---------------------------------------------------------------------------

test('Group A: matchesNamePattern matches the shared cross-implementation fixture for every case', () => {
  assert.ok(fixture.cases.length >= 22, `fixture must have at least 22 cases, found ${fixture.cases.length}`);

  for (const c of fixture.cases) {
    const result = app.matchesNamePattern(c.name, c.pattern);
    assert.strictEqual(
      result,
      c.expected,
      `case name=${JSON.stringify(c.name)} pattern=${JSON.stringify(c.pattern)}: ` +
        `matchesNamePattern() returned ${result}, expected ${c.expected} ` +
        `(${c.why || 'no reason given'}) (see ${fixturePath})`
    );
  }
});

// ---------------------------------------------------------------------------
// Group B -- filterByNamePattern contract
// ---------------------------------------------------------------------------

test('Group B: filterByNamePattern returns sessions unchanged for a blank pattern (empty string)', () => {
  const sessions = [{ name: 'alpha' }, { name: 'beta' }];
  assert.deepStrictEqual(app.filterByNamePattern(sessions, ''), sessions);
});

test('Group B: filterByNamePattern returns sessions unchanged for a whitespace-only pattern', () => {
  const sessions = [{ name: 'alpha' }, { name: 'beta' }];
  assert.deepStrictEqual(app.filterByNamePattern(sessions, '   '), sessions);
});

test('Group B: filterByNamePattern returns sessions unchanged for a null pattern', () => {
  const sessions = [{ name: 'alpha' }, { name: 'beta' }];
  assert.deepStrictEqual(app.filterByNamePattern(sessions, null), sessions);
});

test('Group B: filterByNamePattern returns sessions unchanged for an undefined pattern', () => {
  const sessions = [{ name: 'alpha' }, { name: 'beta' }];
  assert.deepStrictEqual(app.filterByNamePattern(sessions, undefined), sessions);
});

test('Group B: filterByNamePattern narrows to sessions whose name matches a non-blank pattern', () => {
  const sessions = [{ name: 'foo-1' }, { name: 'foo-2' }, { name: 'bar-1' }];
  const result = app.filterByNamePattern(sessions, 'foo-*');
  assert.deepStrictEqual(result.map((s) => s.name), ['foo-1', 'foo-2']);
});

test('Group B: filterByNamePattern matches against session.name, NEVER session.sessionKey', () => {
  // sessionKey is a device-qualified "<device_id>:<name>" UUID-bearing key --
  // nobody would type it as a filter pattern. A pattern that matches the
  // sessionKey but not the bare name must NOT select the session; a pattern
  // that matches the bare name but not the (very different) sessionKey MUST
  // select it.
  const sessions = [
    { name: 'foo', sessionKey: '11111111-2222-3333-4444-555555555555:foo' },
  ];
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, '1111*'),
    [],
    'a pattern matching only the sessionKey must not select the session'
  );
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, 'foo'),
    sessions,
    'a pattern matching the bare name must select the session'
  );
});

test('Group B: filterByNamePattern excludes a session whose name is not a string', () => {
  const sessions = [{ name: 'foo' }, { name: undefined }, { name: 42 }, { name: null }];
  const result = app.filterByNamePattern(sessions, '*');
  assert.deepStrictEqual(result.map((s) => s.name), ['foo']);
});

// ---------------------------------------------------------------------------
// Group B2 -- partial (substring) matching for a bare pattern with no glob
// metacharacters (*, ?, [): "mux" must find "muxplex" without requiring
// "mux*". A pattern that DOES contain a glob metacharacter keeps the
// existing anchored fnmatch behavior unchanged.
// ---------------------------------------------------------------------------

test('Group B2: filterByNamePattern substring-matches a bare prefix pattern (no glob metachars)', () => {
  const sessions = [{ name: 'muxplex' }];
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, 'mux').map((s) => s.name),
    ['muxplex'],
    '"mux" (no metachars) must match "muxplex" as a substring, not require an anchored prefix'
  );
});

test('Group B2: filterByNamePattern substring-matches a mid/suffix bare pattern', () => {
  const sessions = [{ name: 'muxplex' }];
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, 'plex').map((s) => s.name),
    ['muxplex'],
    '"plex" must match "muxplex" -- substring mode is not anchored to the start'
  );
});

test('Group B2: filterByNamePattern substring match is case-insensitive', () => {
  const sessions = [{ name: 'muxplex' }];
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, 'MUX').map((s) => s.name),
    ['muxplex'],
    '"MUX" must match "muxplex" case-insensitively, same as the glob path'
  );
});

test('Group B2: filterByNamePattern substring match excludes a non-matching bare pattern', () => {
  const sessions = [{ name: 'muxplex' }];
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, 'nope'),
    [],
    '"nope" is not a substring of "muxplex" and must exclude it'
  );
});

test('Group B2: a pattern containing a glob metacharacter stays anchored, not substring -- "mux*" matches "muxplex" but not "amuxplex"', () => {
  const sessions = [{ name: 'muxplex' }, { name: 'amuxplex' }];
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, 'mux*').map((s) => s.name),
    ['muxplex'],
    '"mux*" is anchored (prefix match via fnmatch) -- "amuxplex" does not start with "mux" so must be excluded, ' +
      'proving the metacharacter selects the anchored glob path rather than a substring/contains check'
  );
});

test('Group B2: a pattern containing a glob metacharacter stays anchored -- "*plex" matches "muxplex" (suffix)', () => {
  const sessions = [{ name: 'muxplex' }];
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, '*plex').map((s) => s.name),
    ['muxplex']
  );
});

test('Group B2: a pattern containing a glob metacharacter does NOT fall back to substring -- "a*" does not match "xa"', () => {
  const sessions = [{ name: 'xa' }];
  assert.deepStrictEqual(
    app.filterByNamePattern(sessions, 'a*'),
    [],
    '"a*" is an anchored prefix match ("starts with a"); "xa" does not start with "a" so must be excluded -- ' +
      'if this instead matched, it would mean metachar patterns silently fell back to a substring/contains check'
  );
});

// ---------------------------------------------------------------------------
// Group C -- compose: getFilteredSessions = view filter \u2218 name filter;
// grid and sidebar must produce IDENTICAL name sets (anti-drift); composes
// with sort.
// ---------------------------------------------------------------------------

function extractDataSessionOrder(html) {
  const names = [];
  const re = /<article[^>]*\sdata-session="([^"]*)"/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    names.push(m[1]);
  }
  return names;
}

test('Group C: getFilteredSessions composes the active view with the name filter', () => {
  app._setServerSettingsForTests({
    sort_order: 'manual',
    hidden_sessions: [],
    views: [],
    session_filter: 'foo-*',
  });

  const sessions = [
    { name: 'foo-1', sessionKey: 'foo-1' },
    { name: 'foo-2', sessionKey: 'foo-2' },
    { name: 'bar-1', sessionKey: 'bar-1' },
  ];

  const result = app.getFilteredSessions(sessions);
  assert.deepStrictEqual(result.map((s) => s.name), ['foo-1', 'foo-2']);
});

test('Group C: renderGrid and renderSidebar produce identical, filtered, sorted name sets (no drift)', () => {
  app._setServerSettingsForTests({
    sort_order: 'alphabetical',
    hidden_sessions: [],
    views: [],
    session_filter: 'foo-*',
  });
  app._setViewMode('fullscreen');

  const sessions = [
    { name: 'foo-2', sessionKey: 'foo-2', bell: { unseen_count: 0 } },
    { name: 'bar-1', sessionKey: 'bar-1', bell: { unseen_count: 0 } },
    { name: 'foo-1', sessionKey: 'foo-1', bell: { unseen_count: 0 } },
  ];

  const mockGrid = { innerHTML: '' };
  const mockEmpty = { textContent: '', classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false } };
  const mockFilterBar = { innerHTML: '' };
  const mockList = {
    innerHTML: '',
    querySelectorAll: () => [],
  };

  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-grid') return mockGrid;
    if (id === 'empty-state') return mockEmpty;
    if (id === 'filter-bar') return mockFilterBar;
    if (id === 'sidebar-list') return mockList;
    return null;
  };
  globalThis.document.querySelectorAll = () => [];

  app.renderGrid(sessions);
  app.renderSidebar(sessions, null, '');

  const gridNames = extractDataSessionOrder(mockGrid.innerHTML);
  const sidebarNames = extractDataSessionOrder(mockList.innerHTML);

  assert.deepStrictEqual(gridNames, ['foo-1', 'foo-2'], 'grid must show only filtered+sorted matches');
  assert.deepStrictEqual(sidebarNames, ['foo-1', 'foo-2'], 'sidebar must show only filtered+sorted matches');
  assert.deepStrictEqual(gridNames, sidebarNames, 'grid and sidebar must never disagree about which sessions the filter+sort selected');

  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
});

// ---------------------------------------------------------------------------
// Group D -- badges NOT muted: getVisibleSessions() (which feeds the favicon
// badge / page title / mobile bottom sheet) must still return a
// filtered-out session, bell and all.
// ---------------------------------------------------------------------------

test('Group D: getVisibleSessions ignores session_filter -- a filtered-out session with a bell is still returned', () => {
  app._setServerSettingsForTests({
    sort_order: 'manual',
    hidden_sessions: [],
    views: [],
    session_filter: 'foo-*',
  });

  const sessions = [
    { name: 'foo-1', sessionKey: 'foo-1', bell: { unseen_count: 0 } },
    { name: 'bar-1', sessionKey: 'bar-1', bell: { unseen_count: 3 } },
  ];

  const visible = app.getVisibleSessions(sessions);
  assert.deepStrictEqual(
    visible.map((s) => s.name).sort(),
    ['bar-1', 'foo-1'],
    'getVisibleSessions must ignore session_filter entirely -- it feeds the favicon badge/title/mobile sheet, which must not be muted by the name filter'
  );

  const belled = visible.find((s) => s.name === 'bar-1');
  assert.ok(belled, 'the filtered-out session must still be present');
  assert.strictEqual(belled.bell.unseen_count, 3, 'its bell state must be untouched');

  // But the RENDER layer (getFilteredSessions) DOES narrow it out.
  const filtered = app.getFilteredSessions(sessions);
  assert.deepStrictEqual(filtered.map((s) => s.name), ['foo-1']);
});

// ---------------------------------------------------------------------------
// Group E -- exports present, including regression guards for the
// still-exported (but functionally dead) filterByQuery/renderFilterBar.
// ---------------------------------------------------------------------------

test('Group E: all session-filter functions are exported from app.js', () => {
  assert.strictEqual(typeof app.matchesNamePattern, 'function');
  assert.strictEqual(typeof app.filterByNamePattern, 'function');
  assert.strictEqual(typeof app.getFilteredSessions, 'function');
  assert.strictEqual(typeof app.applySessionFilter, 'function');
  assert.strictEqual(typeof app.syncSessionFilterControls, 'function');
});

test('Group E: filterByQuery and renderFilterBar remain exported (regression guard)', () => {
  assert.strictEqual(typeof app.filterByQuery, 'function', 'filterByQuery must remain exported');
  assert.strictEqual(typeof app.renderFilterBar, 'function', 'renderFilterBar must remain exported');
});

// ---------------------------------------------------------------------------
// Group F -- clear-button affordance: syncSessionFilterControls() must keep
// each input's .quick-filter wrapper's 'quick-filter--has-value' class in
// lockstep with session_filter, so the overlaid "x" (style.css's
// .quick-filter--has-value .quick-filter__clear rule) only shows when there
// is something to clear.
// ---------------------------------------------------------------------------

function makeClassListStub() {
  const classes = new Set();
  return {
    classes,
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      toggle: (c, force) => {
        if (force) classes.add(c);
        else classes.delete(c);
      },
      contains: (c) => classes.has(c),
    },
  };
}

test('Group F: syncSessionFilterControls toggles quick-filter--has-value on both wrappers to match session_filter', () => {
  const headerWrapper = makeClassListStub();
  const sidebarWrapper = makeClassListStub();
  const headerInput = { value: '', closest: (sel) => (sel === '.quick-filter' ? headerWrapper : null) };
  const sidebarInput = { value: '', closest: (sel) => (sel === '.quick-filter' ? sidebarWrapper : null) };

  const origGetById = globalThis.document.getElementById;
  const origActiveElement = globalThis.document.activeElement;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-filter-input') return headerInput;
    if (id === 'sidebar-session-filter-input') return sidebarInput;
    return null;
  };
  globalThis.document.activeElement = null;

  try {
    app._setServerSettingsForTests({ sort_order: 'manual', hidden_sessions: [], views: [], session_filter: 'foo' });
    app.syncSessionFilterControls();
    assert.strictEqual(headerWrapper.classList.contains('quick-filter--has-value'), true, 'header wrapper must gain quick-filter--has-value for a non-empty filter');
    assert.strictEqual(sidebarWrapper.classList.contains('quick-filter--has-value'), true, 'sidebar wrapper must gain quick-filter--has-value for a non-empty filter');

    app._setServerSettingsForTests({ sort_order: 'manual', hidden_sessions: [], views: [], session_filter: '' });
    app.syncSessionFilterControls();
    assert.strictEqual(headerWrapper.classList.contains('quick-filter--has-value'), false, 'header wrapper must lose quick-filter--has-value once the filter is cleared');
    assert.strictEqual(sidebarWrapper.classList.contains('quick-filter--has-value'), false, 'sidebar wrapper must lose quick-filter--has-value once the filter is cleared');
  } finally {
    globalThis.document.getElementById = origGetById;
    globalThis.document.activeElement = origActiveElement;
  }
});

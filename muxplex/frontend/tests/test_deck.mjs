// Tests for frontend/deck/deck.js's pure logic (no DOM dependency — these
// functions are exported unconditionally; the DOM-wiring block only runs
// when `document` exists, which it deliberately doesn't in this file).

import { createRequire } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const require = createRequire(import.meta.url);
const deck = require(join(__dirname, '..', 'deck', 'deck.js'));

test('deck.js exports all pure functions', () => {
  const expected = [
    'classifyStaleness',
    'formatAge',
    'formatLastActivity',
    'nextPreviousActive',
    'gridOverflows',
    'previewLines',
    'attentionSessions',
    'tileVisualState',
    'lockLandscapeOrientation',
    'registerServiceWorker',
  ];
  for (const fn of expected) {
    assert.ok(fn in deck, `deck.js should export "${fn}"`);
    assert.strictEqual(typeof deck[fn], 'function', `"${fn}" should be a function`);
  }
});

// ─── classifyStaleness ────────────────────────────────────────────────────

test('classifyStaleness: fresh under 6s with a successful poll', () => {
  assert.strictEqual(deck.classifyStaleness(0, true), 'fresh');
  assert.strictEqual(deck.classifyStaleness(5999, true), 'fresh');
});

test('classifyStaleness: warn between 6s and 30s', () => {
  assert.strictEqual(deck.classifyStaleness(6001, true), 'warn');
  assert.strictEqual(deck.classifyStaleness(29999, true), 'warn');
});

test('classifyStaleness: err past 30s even if the poll technically succeeded', () => {
  assert.strictEqual(deck.classifyStaleness(30001, true), 'err');
});

test('classifyStaleness: err immediately on a failed poll regardless of age', () => {
  assert.strictEqual(deck.classifyStaleness(0, false), 'err');
  assert.strictEqual(deck.classifyStaleness(1, false), 'err');
});

// ─── formatAge / formatLastActivity ───────────────────────────────────────

test('formatAge: seconds, minutes, hours, days', () => {
  assert.strictEqual(deck.formatAge(5000), '5s');
  assert.strictEqual(deck.formatAge(65000), '1m');
  assert.strictEqual(deck.formatAge(3 * 3600 * 1000), '3h');
  assert.strictEqual(deck.formatAge(2 * 86400 * 1000), '2d');
});

test('formatAge: negative/null returns empty string', () => {
  assert.strictEqual(deck.formatAge(-1), '');
  assert.strictEqual(deck.formatAge(null), '');
});

test('formatLastActivity: null renders the reserved-band dash', () => {
  assert.strictEqual(deck.formatLastActivity(null), '\u2014');
  assert.strictEqual(deck.formatLastActivity(undefined), '\u2014');
});

test('formatLastActivity: under 10s renders "now"', () => {
  const now = 1_700_000_000_000;
  const activityAt = now / 1000 - 5; // 5s ago, unix seconds
  assert.strictEqual(deck.formatLastActivity(activityAt, now), 'now');
});

test('formatLastActivity: older activity renders a relative age', () => {
  const now = 1_700_000_000_000;
  const activityAt = now / 1000 - 240; // 4 minutes ago
  assert.strictEqual(deck.formatLastActivity(activityAt, now), '4m');
});

// ─── nextPreviousActive (toggle-last tracking) ────────────────────────────

test('nextPreviousActive: unchanged active session keeps the prior previous', () => {
  assert.strictEqual(deck.nextPreviousActive('a', 'z', 'a'), 'z');
});

test('nextPreviousActive: a genuine switch promotes the prior active', () => {
  assert.strictEqual(deck.nextPreviousActive('a', 'z', 'b'), 'a');
});

test('nextPreviousActive: no prior active session leaves previous untouched', () => {
  assert.strictEqual(deck.nextPreviousActive(null, null, 'a'), null);
});

test('nextPreviousActive: first-ever switch from a known active session', () => {
  assert.strictEqual(deck.nextPreviousActive('a', null, 'b'), 'a');
});

// ─── gridOverflows ─────────────────────────────────────────────────────────

test('gridOverflows: true only when scrollHeight exceeds clientHeight', () => {
  assert.strictEqual(deck.gridOverflows(500, 400), true);
  assert.strictEqual(deck.gridOverflows(400, 400), false);
  assert.strictEqual(deck.gridOverflows(300, 400), false);
});

// ─── previewLines ──────────────────────────────────────────────────────────

test('previewLines: returns the last N lines, newest last', () => {
  const snapshot = ['a', 'b', 'c', 'd', 'e', 'f'].join('\n');
  assert.strictEqual(deck.previewLines(snapshot, 3), 'd\ne\nf');
});

test('previewLines: shorter snapshot than the budget returns unchanged', () => {
  assert.strictEqual(deck.previewLines('a\nb', 5), 'a\nb');
});

test('previewLines: trailing blank lines are dropped before budgeting', () => {
  const snapshot = 'a\nb\n\n\n';
  assert.strictEqual(deck.previewLines(snapshot, 5), 'a\nb');
});

test('previewLines: empty/falsy input returns empty string', () => {
  assert.strictEqual(deck.previewLines('', 5), '');
  assert.strictEqual(deck.previewLines(null, 5), '');
});

// ─── attentionSessions ─────────────────────────────────────────────────────

test('attentionSessions: filters to needs_attention only, preserving order', () => {
  const sessions = [
    { name: 'a', needs_attention: false },
    { name: 'b', needs_attention: true },
    { name: 'c', needs_attention: true },
  ];
  assert.deepStrictEqual(
    deck.attentionSessions(sessions).map((s) => s.name),
    ['b', 'c']
  );
});

test('attentionSessions: empty/null input returns empty array', () => {
  assert.deepStrictEqual(deck.attentionSessions(null), []);
  assert.deepStrictEqual(deck.attentionSessions([]), []);
});

// ─── tileVisualState — the "never lie" state machine ──────────────────────

test('tileVisualState: server-active with no local overrides is active', () => {
  const state = deck.tileVisualState({
    serverActive: true,
    pendingName: null,
    tileName: 'a',
    failedUntil: null,
    nowMs: 1000,
  });
  assert.strictEqual(state, 'active');
});

test('tileVisualState: not active and no overrides is idle', () => {
  const state = deck.tileVisualState({
    serverActive: false,
    pendingName: null,
    tileName: 'a',
    failedUntil: null,
    nowMs: 1000,
  });
  assert.strictEqual(state, 'idle');
});

test('tileVisualState: a matching pending name overrides idle to pending', () => {
  const state = deck.tileVisualState({
    serverActive: false,
    pendingName: 'a',
    tileName: 'a',
    failedUntil: null,
    nowMs: 1000,
  });
  assert.strictEqual(state, 'pending');
});

test('tileVisualState: pending on a DIFFERENT tile does not affect this one', () => {
  const state = deck.tileVisualState({
    serverActive: false,
    pendingName: 'b',
    tileName: 'a',
    failedUntil: null,
    nowMs: 1000,
  });
  assert.strictEqual(state, 'idle');
});

test('tileVisualState: an unexpired failed marker beats everything, including server-active', () => {
  const state = deck.tileVisualState({
    serverActive: true,
    pendingName: null,
    tileName: 'a',
    failedUntil: 2000,
    nowMs: 1500,
  });
  assert.strictEqual(state, 'failed');
});

test('tileVisualState: an expired failed marker no longer applies', () => {
  const state = deck.tileVisualState({
    serverActive: true,
    pendingName: null,
    tileName: 'a',
    failedUntil: 1000,
    nowMs: 1500,
  });
  assert.strictEqual(state, 'active');
});

test('tileVisualState: failed outranks pending too (poll-proof — DESIGN_TILE.md §6.2)', () => {
  const state = deck.tileVisualState({
    serverActive: false,
    pendingName: 'a',
    tileName: 'a',
    failedUntil: 2000,
    nowMs: 1500,
  });
  assert.strictEqual(state, 'failed');
});

// ─── .deck-grid row-track sizing — regression guard for the phone-portrait
// tile-overlap bug ──────────────────────────────────────────────────────
//
// HONESTY NOTE, read before "fixing" this test: the actual defect (rendered
// tile height disagreeing with the grid's *computed* row-track height) is a
// live CSS Grid track-sizing computation — it only exists once a real layout
// engine resolves `.deck-grid`'s implicit row tracks against `.session-tile`'s
// `aspect-ratio: 1`. This zero-dependency `node --test` suite has no DOM and
// no layout engine (by convention — AGENTS.md: "these suites... use only
// node: builtins"), so it CANNOT compute a grid track size and cannot assert
// the geometric relationship directly. That check was done with a real
// Chromium (Playwright) against the live `/deck/` route at 390×844, 844×390
// and 1024×768 — see the delivery report, not this file, for those
// measurements.
//
// What THIS test can do, and honestly all it does: guard against regressing
// to the specific broken declaration. The bug was `.deck-grid` relying on
// the implicit `grid-auto-rows: auto` default, under which Chromium sizes an
// implicit row track from each item's own pre-stretch max-content
// contribution (≈ its longest unbroken text segment) rather than the
// column-track-stretched width `aspect-ratio: 1` actually renders at — so a
// 183px-tall square tile landed in a ~96.5px-tall row track and the next row
// rendered on top of it. `min-content`/`max-content` make the row-track
// sizing pass use the item's real (post-stretch) box, which is the actual
// fix. This is a source-shape assertion (same category as
// `test_frontend_js.py`'s regex checks — see that file's docstring on
// deliberately pinning shape over behavior when behavior isn't mechanically
// checkable here): it fails loudly if someone reverts to `auto` (or deletes
// the declaration, which is equivalent), but it does NOT — cannot — prove
// the row track and tile height actually match at runtime.
test('.deck-grid sets an explicit non-"auto" grid-auto-rows (regression guard for the phone-portrait tile-overlap bug)', () => {
  const cssPath = join(__dirname, '..', 'deck', 'deck.css');
  // Strip CSS comments FIRST. Without this, the rule-matching regex below can
  // be fooled two ways: (1) the fix's own explanatory comment mentions
  // "grid-auto-rows: min-content" in prose, which would otherwise be picked
  // up as if it were the live declaration; (2) a compound selector elsewhere
  // in the file that merely CONTAINS the substring ".deck-grid" (e.g.
  // `.deck-body.stale .deck-grid { ... }`) can satisfy an unanchored rule
  // match before the real `.deck-grid { ... }` rule is reached. Both bit this
  // test during authoring — comment-stripping plus a start-of-line anchor
  // fixes both.
  const css = fs.readFileSync(cssPath, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

  const gridRuleMatch = css.match(/^\.deck-grid\s*\{([^}]*)\}/m);
  assert.ok(gridRuleMatch, '.deck-grid rule should exist in deck.css');
  const ruleBody = gridRuleMatch[1];

  const autoRowsMatch = ruleBody.match(/grid-auto-rows\s*:\s*([^;]+);/);
  assert.ok(
    autoRowsMatch,
    '.deck-grid must explicitly declare grid-auto-rows — the implicit `auto` ' +
      'default is exactly the value that produced the phone-portrait overlap bug'
  );

  const value = autoRowsMatch[1].trim();
  assert.notStrictEqual(
    value,
    'auto',
    'grid-auto-rows: auto is the reverted/broken state — Chromium under-sizes the ' +
      'implicit row track relative to the aspect-ratio-driven tile height at this value'
  );
  assert.ok(
    value === 'min-content' || value === 'max-content',
    `expected grid-auto-rows to be min-content or max-content (verified fixes), got "${value}". ` +
      'If a different value was chosen deliberately, it must be re-verified with a real ' +
      'browser at 390×844 (the reproduction viewport) before this assertion is updated.'
  );
});

// ─── lockLandscapeOrientation / registerServiceWorker ─────────────────────
//
// These touch browser-only globals (`screen`, `navigator.serviceWorker`)
// that don't exist in a plain Node test environment. The contract under
// test here is narrower but load-bearing: both functions must degrade
// silently (never throw, never produce an unhandled rejection) when those
// globals are absent or reject -- that's what makes it safe to call them
// unconditionally from boot() on every platform, including ones with no
// Screen Orientation API at all (see deck.js's boot()).

test('lockLandscapeOrientation does not throw when `screen` is undefined (Node/older browsers)', () => {
  assert.strictEqual(typeof globalThis.screen, 'undefined');
  assert.doesNotThrow(() => deck.lockLandscapeOrientation());
});

test('lockLandscapeOrientation does not throw when screen.orientation.lock rejects', async () => {
  globalThis.screen = {
    orientation: { lock: () => Promise.reject(new Error('not allowed')) },
  };
  try {
    assert.doesNotThrow(() => deck.lockLandscapeOrientation());
    // Let the rejected promise's .catch() run before the test exits, so a
    // regression that removes the .catch() surfaces as an unhandled
    // rejection instead of a silently-passing test.
    await new Promise((resolve) => setTimeout(resolve, 0));
  } finally {
    delete globalThis.screen;
  }
});

test('lockLandscapeOrientation is a no-op when screen.orientation is absent', () => {
  globalThis.screen = {};
  try {
    assert.doesNotThrow(() => deck.lockLandscapeOrientation());
  } finally {
    delete globalThis.screen;
  }
});

test('registerServiceWorker does not throw when `navigator.serviceWorker` is absent', () => {
  assert.doesNotThrow(() => deck.registerServiceWorker());
});

test('registerServiceWorker registers /deck/sw.js when serviceWorker is available', () => {
  let registeredPath = null;
  const originalNavigator = globalThis.navigator;
  globalThis.navigator = {
    serviceWorker: {
      register: (path) => {
        registeredPath = path;
        return Promise.resolve({});
      },
    },
  };
  try {
    deck.registerServiceWorker();
    assert.strictEqual(registeredPath, '/deck/sw.js');
  } finally {
    globalThis.navigator = originalNavigator;
  }
});

test('registerServiceWorker does not throw when registration rejects', async () => {
  const originalNavigator = globalThis.navigator;
  globalThis.navigator = {
    serviceWorker: { register: () => Promise.reject(new Error('nope')) },
  };
  try {
    assert.doesNotThrow(() => deck.registerServiceWorker());
    await new Promise((resolve) => setTimeout(resolve, 0));
  } finally {
    globalThis.navigator = originalNavigator;
  }
});

// ─── sw.js ─────────────────────────────────────────────────────────────

test('sw.js caches nothing (regression guard: this project has shipped stale-frontend bugs from caching before)', () => {
  const swPath = join(__dirname, '..', 'deck', 'sw.js');
  const src = fs.readFileSync(swPath, 'utf8');
  const lowered = src.toLowerCase();
  assert.ok(!lowered.includes('caches.open'), 'sw.js must never open a Cache Storage cache');
  assert.ok(!lowered.includes('cache.put'), 'sw.js must never write to a cache');
  assert.ok(!lowered.includes('cache.addall'), 'sw.js must never pre-cache assets');
  assert.ok(src.includes("addEventListener('fetch'"), 'sw.js must have a fetch handler (required for the install prompt)');
});

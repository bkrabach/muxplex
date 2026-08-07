// Tests for frontend/deck/deck.js's pure logic (no DOM dependency -- these
// functions are exported unconditionally; the DOM-wiring block only runs
// when `document` exists, which it deliberately doesn't in this file).
//
// Governed by DESIGN_SOFTDECK.md. The grid/token/reserved-key numbers below
// are cross-checked against that document's \u00a75 worked examples wherever
// possible -- see each test's comment for which example it reproduces.

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
    'previewLines',
    'tileVisualState',
    'computeGrid',
    'deriveTokens',
    'reservedControlKeys',
    'sessionSlotIndices',
    'pageCount',
    'pageSlice',
    'clampPage',
    'controlKeyContent',
    'pickerOptionContent',
    'viewSessionCounts',
    'fitLabel',
    'computeKeyPlan',
    'faceClassName',
    'findBlankControlFaces',
    'lockLandscapeOrientation',
    'registerServiceWorker',
    // Settings menu (BACKLOG.md item 2)
    'parseControlAddress',
    'validActionsForAddress',
    'isValidBinding',
    'sanitizeBindings',
    'keyBindingsFromConfig',
    'dialBindingsFromConfig',
    'actionKeyContent',
    'pageItemLabels',
    'pageOptionContent',
    'dialDragTicks',
    'isDialTap',
    'applyRelativeTicks',
    'defaultDeckSettings',
    'mergeDeckSettings',
    'loadDeckSettings',
    'saveDeckSettings',
    'exportSettingsJSON',
    'importSettingsJSON',
    'computeGridForShape',
    'computeEffectiveGrid',
    // Settings recovery (BACKLOG.md item 4)
    'persistableDeckSettings',
    'gridOverrideReachability',
    'settingsReachability',
    'contentBoxForDials',
    'buildStripStatusMessage',
    'buildStripPickerStatusMessage',
    // Soft deck settings menu defects (BACKLOG.md item 2)
    'bindingApplicability',
  ];
  for (const fn of expected) {
    assert.ok(fn in deck, `deck.js should export "${fn}"`);
    assert.strictEqual(typeof deck[fn], 'function', `"${fn}" should be a function`);
  }
});

// ─── classifyStaleness ──────────────────────────────────────────────────────

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

// ─── formatAge / formatLastActivity ─────────────────────────────────────────

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

// ─── previewLines ────────────────────────────────────────────────────────────

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

// ─── tileVisualState -- the "never lie" state machine ───────────────────────

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

test('tileVisualState: failed outranks pending too (poll-proof)', () => {
  const state = deck.tileVisualState({
    serverActive: false,
    pendingName: 'a',
    tileName: 'a',
    failedUntil: 2000,
    nowMs: 1500,
  });
  assert.strictEqual(state, 'failed');
});

// ─── computeGrid -- DESIGN_SOFTDECK.md \u00a71.3, cross-checked against \u00a75 ───────

test('computeGrid: compact phone landscape 750x344 content box -> 3x7 (DESIGN_SOFTDECK.md \u00a75.1)', () => {
  const g = deck.computeGrid(750, 344);
  assert.strictEqual(g.rows, 3);
  assert.strictEqual(g.cols, 7);
  assert.strictEqual(g.letterboxed, false);
  assert.ok(Math.abs(g.cellW - 97.71) < 0.1, `cellW ${g.cellW}`);
  assert.ok(Math.abs(g.cellH - 107.33) < 0.1, `cellH ${g.cellH}`);
  assert.ok(Math.abs(g.s - 97.71) < 0.1, `s ${g.s}`);
  assert.strictEqual(g.gap, 11);
});

test('computeGrid: flagship phone landscape 870x396 content box -> 4x8 = 32 (DESIGN_SOFTDECK.md \u00a75.2, the XL-geometry finding)', () => {
  const g = deck.computeGrid(870, 396);
  assert.strictEqual(g.rows, 4);
  assert.strictEqual(g.cols, 8);
  assert.strictEqual(g.rows * g.cols, 32);
  assert.strictEqual(g.letterboxed, false);
  assert.ok(Math.abs(g.s - 90.75) < 0.1, `s ${g.s}`);
});

test('computeGrid: tablet landscape 1264x784 content box -> 4x7=28, S clamped at S_MAX (DESIGN_SOFTDECK.md \u00a75.3)', () => {
  const g = deck.computeGrid(1264, 784);
  assert.strictEqual(g.rows, 4);
  assert.strictEqual(g.cols, 7);
  assert.strictEqual(g.rows * g.cols, 28);
  assert.ok(g.rows * g.cols <= deck.N_MAX);
  assert.strictEqual(g.s, deck.S_MAX, 'S must clamp at S_MAX even though the actual cell is bigger');
  assert.ok(g.cellH > deck.S_MAX, 'the actual cell must NOT be clamped, only the f(S) token scalar');
});

test('computeGrid: unforced portrait 374x812 content box -> 3x8, letterboxed (DESIGN_SOFTDECK.md \u00a75.4)', () => {
  const g = deck.computeGrid(374, 812);
  assert.strictEqual(g.rows, 8);
  assert.strictEqual(g.cols, 3);
  assert.strictEqual(g.letterboxed, true);
  assert.ok(Math.abs(g.cellW - g.cellH) < 0.001, 'letterboxed cells must be square');
  assert.ok(Math.abs(g.s - 91.875) < 0.1, `s ${g.s}`);
});

test('computeGrid: never exceeds N_MAX keys', () => {
  const boxes = [
    [1264, 784],
    [2560, 1440],
    [3000, 2000],
    [1920, 1080],
  ];
  for (const [w, h] of boxes) {
    const g = deck.computeGrid(w, h);
    assert.ok(g.rows * g.cols <= deck.N_MAX, `${w}x${h} -> ${g.rows}x${g.cols} exceeds N_MAX`);
  }
});

test('computeGrid: degrades gracefully (tooSmall) rather than crashing on a tiny/zero box', () => {
  assert.strictEqual(deck.computeGrid(0, 0).tooSmall, true);
  assert.strictEqual(deck.computeGrid(-10, 500).tooSmall, true);
  const tiny = deck.computeGrid(40, 40);
  assert.strictEqual(tiny.tooSmall, true);
});

test('computeGrid: S never exceeds S_MAX', () => {
  const g = deck.computeGrid(4000, 3000);
  assert.ok(g.s <= deck.S_MAX);
});

// ─── deriveTokens -- KEY_DESIGN_SYSTEM.md \u00a71-\u00a72, DESIGN_SOFTDECK.md \u00a74.2 ──────

test('deriveTokens: matches DESIGN_SOFTDECK.md \u00a75.1\'s worked token table (S\u224897.71)', () => {
  const t = deck.deriveTokens(97.71, 107.33);
  assert.strictEqual(t.b, 3);
  assert.strictEqual(t.m, 5);
  assert.strictEqual(t.nameH, 27);
  assert.strictEqual(t.stateH, 19);
  assert.strictEqual(t.primary, 22);
  assert.strictEqual(t.secondary, 15);
  assert.strictEqual(t.texture, 11);
});

test('deriveTokens: matches DESIGN_SOFTDECK.md \u00a75.2\'s worked token table (S\u224890.75)', () => {
  const t = deck.deriveTokens(90.75, 90.75);
  assert.strictEqual(t.b, 3);
  assert.strictEqual(t.m, 5);
  assert.strictEqual(t.nameH, 25);
  assert.strictEqual(t.stateH, 17);
  assert.strictEqual(t.primary, 20);
  assert.strictEqual(t.secondary, 14);
});

test('deriveTokens: matches DESIGN_SOFTDECK.md \u00a75.3\'s worked token table (S clamped at 160)', () => {
  const t = deck.deriveTokens(160, 182.5);
  assert.strictEqual(t.b, 5);
  assert.strictEqual(t.m, 9);
  assert.strictEqual(t.nameH, 45);
  assert.strictEqual(t.stateH, 30);
  assert.strictEqual(t.primary, 36);
  assert.strictEqual(t.secondary, 24);
});

test('deriveTokens: B has a 2px floor even at very small S', () => {
  assert.strictEqual(deck.deriveTokens(10).b, 2);
});

test('deriveTokens: TEXTURE never scales with S (fixed 11)', () => {
  assert.strictEqual(deck.deriveTokens(72).texture, 11);
  assert.strictEqual(deck.deriveTokens(160).texture, 11);
});

// ─── reservedControlKeys -- port of muxplex-deck's layout.py ────────────────

test('reservedControlKeys: corners on a 3x7 grid (DESIGN_SOFTDECK.md \u00a75.1 -- key.0/14/20)', () => {
  const r = deck.reservedControlKeys(3, 7);
  assert.strictEqual(r.mode, 'corners');
  assert.strictEqual(r.view, 0);
  assert.strictEqual(r.prev, 14);
  assert.strictEqual(r.next, 20);
});

test('reservedControlKeys: corners on a 4x8 grid (DESIGN_SOFTDECK.md \u00a75.2 -- key.0/24/31)', () => {
  const r = deck.reservedControlKeys(4, 8);
  assert.strictEqual(r.mode, 'corners');
  assert.strictEqual(r.view, 0);
  assert.strictEqual(r.prev, 24);
  assert.strictEqual(r.next, 31);
});

test('reservedControlKeys: bottom row on a 3-column grid (DESIGN_SOFTDECK.md \u00a75.4 -- 8x3 portrait)', () => {
  const r = deck.reservedControlKeys(8, 3);
  assert.strictEqual(r.mode, 'bottom-row');
  assert.strictEqual(r.prev, 21);
  assert.strictEqual(r.view, 22);
  assert.strictEqual(r.next, 23);
});

test('reservedControlKeys: bottom row matches the hardware Mini fixture (3x2 -- CONTROL_MAPPING_DESIGN.md \u00a74.2)', () => {
  const r = deck.reservedControlKeys(2, 3);
  assert.strictEqual(r.mode, 'bottom-row');
  assert.strictEqual(r.prev, 3);
  assert.strictEqual(r.view, 4);
  assert.strictEqual(r.next, 5);
});

test('reservedControlKeys: degenerate below 4 keys', () => {
  assert.strictEqual(deck.reservedControlKeys(1, 3).mode, 'degenerate');
  assert.strictEqual(deck.reservedControlKeys(2, 1).mode, 'degenerate');
});

test('reservedControlKeys: degenerate on a single row or single column (colliding corners)', () => {
  assert.strictEqual(deck.reservedControlKeys(1, 8).mode, 'degenerate');
  assert.strictEqual(deck.reservedControlKeys(8, 1).mode, 'degenerate');
});

test('reservedControlKeys: the three keys are always distinct when not degenerate', () => {
  const shapes = [
    [3, 7],
    [4, 8],
    [8, 3],
    [2, 3],
    [5, 5],
    [1, 5],
  ];
  for (const [rows, cols] of shapes) {
    const r = deck.reservedControlKeys(rows, cols);
    if (r.mode === 'degenerate') continue;
    const set = new Set([r.view, r.prev, r.next]);
    assert.strictEqual(set.size, 3, `${rows}x${cols} -> collision in ${JSON.stringify(r)}`);
  }
});

// ─── sessionSlotIndices ──────────────────────────────────────────────────────

test('sessionSlotIndices: 18 session slots on the 3x7/21-key grid (DESIGN_SOFTDECK.md \u00a75.1)', () => {
  const r = deck.reservedControlKeys(3, 7);
  const slots = deck.sessionSlotIndices(3, 7, r);
  assert.strictEqual(slots.length, 18);
  assert.ok(!slots.includes(0));
  assert.ok(!slots.includes(14));
  assert.ok(!slots.includes(20));
});

test('sessionSlotIndices: 29 session slots on the 4x8/32-key grid (DESIGN_SOFTDECK.md \u00a75.2)', () => {
  const r = deck.reservedControlKeys(4, 8);
  const slots = deck.sessionSlotIndices(4, 8, r);
  assert.strictEqual(slots.length, 29);
});

test('sessionSlotIndices: degenerate grid has every key as a session slot', () => {
  const r = deck.reservedControlKeys(1, 3);
  const slots = deck.sessionSlotIndices(1, 3, r);
  assert.deepStrictEqual(slots, [0, 1, 2]);
});

// ─── pageCount / pageSlice / clampPage ──────────────────────────────────────

test('pageCount: 43 sessions over 29 slots -> 2 pages (DESIGN_SOFTDECK.md \u00a75.2\'s worked pagination)', () => {
  assert.strictEqual(deck.pageCount(43, 29), 2);
});

test('pageCount: always at least 1 page, even with zero items', () => {
  assert.strictEqual(deck.pageCount(0, 18), 1);
});

test('pageSlice: short last page returns fewer than perPage items (blank faces, no reflow)', () => {
  const items = Array.from({ length: 43 }, (_, i) => 'session-' + i);
  const page0 = deck.pageSlice(items, 0, 29);
  const page1 = deck.pageSlice(items, 1, 29);
  assert.strictEqual(page0.length, 29);
  assert.strictEqual(page1.length, 14);
});

test('clampPage: never goes below 0 or above pageCount-1 (page_prev/page_next are clamped, not wrapping)', () => {
  assert.strictEqual(deck.clampPage(0, -1, 3), 0);
  assert.strictEqual(deck.clampPage(2, 1, 3), 2);
  assert.strictEqual(deck.clampPage(1, 1, 3), 2);
  assert.strictEqual(deck.clampPage(1, -1, 3), 0);
});

// ─── controlKeyContent -- KEY_DESIGN_SYSTEM.md \u00a76.2/\u00a76.4 ─────────────────────

test('controlKeyContent: the critical inversion -- direction small (NAME), noun big (BODY)', () => {
  const prev = deck.controlKeyContent('prev', { pagePosition: '1/2' });
  assert.strictEqual(prev.name, '< PREV');
  assert.strictEqual(prev.body, 'PAGE');
  assert.strictEqual(prev.state, '1/2');

  const next = deck.controlKeyContent('next', { pagePosition: '1/2' });
  assert.strictEqual(next.name, 'NEXT >');
  assert.strictEqual(next.body, 'PAGE');
});

test('controlKeyContent: view key carries the current view name in BODY', () => {
  const view = deck.controlKeyContent('view', { viewName: 'work', pagePosition: '1/2' });
  assert.strictEqual(view.name, 'VIEW');
  assert.strictEqual(view.body, 'work');
});

test('controlKeyContent: back key (picker mode) reads < BACK / VIEW', () => {
  const back = deck.controlKeyContent('back', {});
  assert.strictEqual(back.name, '< BACK');
  assert.strictEqual(back.body, 'VIEW');
});

// ─── pickerOptionContent -- KEY_DESIGN_SYSTEM.md \u00a76.3 + the one enrichment ────

test('pickerOptionContent: NAME stays empty, BODY carries the view name', () => {
  const c = deck.pickerOptionContent('work', null);
  assert.strictEqual(c.name, '');
  assert.strictEqual(c.body, 'work');
  assert.strictEqual(c.state, '');
});

test('pickerOptionContent: STATE carries a session count when known, singular/plural', () => {
  assert.strictEqual(deck.pickerOptionContent('work', 1).state, '1 session');
  assert.strictEqual(deck.pickerOptionContent('work', 5).state, '5 sessions');
  assert.strictEqual(deck.pickerOptionContent('work', 0).state, '0 sessions');
});

// ─── viewSessionCounts -- the documented ":name" suffix rule only ───────────

test('viewSessionCounts: counts local sessions matching the ":name" suffix rule', () => {
  const names = ['alpha', 'beta', 'gamma'];
  const viewsList = [
    { name: 'work', sessions: ['device-1:alpha', 'device-1:beta'] },
    { name: 'personal', sessions: ['device-1:gamma'] },
  ];
  const counts = deck.viewSessionCounts(names, viewsList);
  assert.strictEqual(counts.work, 2);
  assert.strictEqual(counts.personal, 1);
});

test('viewSessionCounts: bare (unprefixed) legacy entries also match', () => {
  const names = ['alpha'];
  const viewsList = [{ name: 'work', sessions: ['alpha'] }];
  assert.strictEqual(deck.viewSessionCounts(names, viewsList).work, 1);
});

test('viewSessionCounts: sessions not present locally are not counted', () => {
  const names = ['alpha'];
  const viewsList = [{ name: 'work', sessions: ['device-1:alpha', 'device-1:missing'] }];
  assert.strictEqual(deck.viewSessionCounts(names, viewsList).work, 1);
});

test('viewSessionCounts: empty/missing membership returns an empty map, never throws', () => {
  assert.deepStrictEqual(deck.viewSessionCounts(['a'], []), {});
  assert.deepStrictEqual(deck.viewSessionCounts(['a'], null), {});
  assert.deepStrictEqual(deck.viewSessionCounts(null, null), {});
});

// --- viewSessionCounts: annotated shape (docs/plans/2026-08-04-auto-views-plan.md §9.4) ---
//
// When entries carry the server's resolved `views` (from GET /api/sessions),
// counts are read straight from the annotation -- the ONLY path that counts
// rule-matched sessions correctly, since they are never written into
// view.sessions.

test('viewSessionCounts: annotated sessions count from s.views, matching the server\'s own resolved membership', () => {
  const sessionsWithViews = [
    { name: 'amplifier-foo', views: ['Auto'] },
    { name: 'amplifier-bar', views: ['Auto'] },
    { name: 'unrelated', views: [] },
  ];
  // Deliberately give the view an EMPTY `sessions` array (rule-only view) --
  // the legacy suffix-matching path would report 0 here; the annotated
  // path must report 2, matching the server's GET /api/sessions annotation.
  const viewsList = [{ name: 'Auto', sessions: [] }];
  const counts = deck.viewSessionCounts(sessionsWithViews, viewsList);
  assert.strictEqual(counts.Auto, 2);
});

test('viewSessionCounts: annotated shape handles a session in multiple views', () => {
  const sessionsWithViews = [{ name: 'x', views: ['A', 'B'] }];
  const viewsList = [{ name: 'A', sessions: [] }, { name: 'B', sessions: [] }];
  const counts = deck.viewSessionCounts(sessionsWithViews, viewsList);
  assert.strictEqual(counts.A, 1);
  assert.strictEqual(counts.B, 1);
});

test('viewSessionCounts: legacy bare-name-array calling shape still works unchanged', () => {
  // Regression: the pre-existing calling convention (array of name strings)
  // must still produce the pre-existing suffix-matched result.
  const names = ['alpha', 'beta'];
  const viewsList = [{ name: 'work', sessions: ['device-1:alpha'] }];
  assert.strictEqual(deck.viewSessionCounts(names, viewsList).work, 1);
});

// ─── fitLabel -- pixel-measured truncation (port of rendering.py's _fit_label) ─
//
// `measureWidth` is a fake: N characters -> N "pixels". This is enough to
// exercise the ALGORITHM (drop from the end until `text + ellipsis` fits)
// without needing a real Canvas, matching this file's no-DOM-dependency
// convention for pure-logic tests.

function charWidthMeasure(text) {
  return text.length;
}

test('fitLabel: returns the text unchanged when it already fits', () => {
  assert.strictEqual(deck.fitLabel('short', 10, charWidthMeasure), 'short');
});

test('fitLabel: drops characters from the END, keeping a stable prefix, and appends a trailing ellipsis', () => {
  // maxWidth 5 -- "abcdefghij" (10 chars) must shrink to fit "prefix\u2026" <= 5.
  const result = deck.fitLabel('abcdefghij', 5, charWidthMeasure);
  assert.strictEqual(result, 'abcd\u2026');
  assert.ok(result.length <= 5);
  assert.ok(result.endsWith('\u2026'));
});

test('fitLabel: a longer name with the same short prefix truncates to the SAME prefix (deterministic)', () => {
  const a = deck.fitLabel('amplifier-session-one', 5, charWidthMeasure);
  const b = deck.fitLabel('amplifier-session-two-longer', 5, charWidthMeasure);
  assert.strictEqual(a, 'ampl\u2026');
  assert.strictEqual(b, 'ampl\u2026');
});

test('fitLabel: empty text returns empty text, never a bare ellipsis', () => {
  assert.strictEqual(deck.fitLabel('', 5, charWidthMeasure), '');
});

test('fitLabel: falls back to just the ellipsis when even one character does not fit', () => {
  assert.strictEqual(deck.fitLabel('abcdef', 0, charWidthMeasure), '\u2026');
});

// ─── computeKeyPlan -- the ONE decision point for "what is on key N" ─────
//
// DECK_PARITY_ARCHITECTURE.md \u00a72.2: `controlKeyContent` had nine green tests
// and zero call sites. These tests assert on the PLAN a real render would
// produce, which is exactly the layer that bug lived one hop above --
// asserting the plan is what would have caught it (a unit test on
// `controlKeyContent` alone cannot).

function basePlanParams(overrides) {
  const grid = { rows: 4, cols: 8 };
  const reserved = deck.reservedControlKeys(grid.rows, grid.cols);
  const base = {
    grid: grid,
    reserved: reserved,
    mode: 'grid',
    sessions: [],
    viewName: 'all',
    viewsList: ['all'],
    page: 0,
    pickerPage: 0,
    viewCounts: {},
    pendingName: null,
    failedByName: {},
    snapshots: {},
    previewLinesMax: 20,
    nowMs: 1000,
  };
  return Object.assign(base, overrides);
}

test('computeKeyPlan: grid mode -- the three control keys are never blank (regression guard for the historical wiring bug)', () => {
  const result = deck.computeKeyPlan(basePlanParams({ viewName: 'work' }));
  const offenders = deck.findBlankControlFaces(result.plan);
  assert.deepStrictEqual(offenders, [], 'no control-role key should have both empty NAME and empty BODY');

  const reserved = deck.reservedControlKeys(4, 8);
  const viewFace = result.plan[reserved.view];
  assert.strictEqual(viewFace.role, 'view');
  assert.strictEqual(viewFace.name, 'VIEW');
  assert.strictEqual(viewFace.body, 'work');

  const prevFace = result.plan[reserved.prev];
  assert.strictEqual(prevFace.name, '< PREV');
  const nextFace = result.plan[reserved.next];
  assert.strictEqual(nextFace.name, 'NEXT >');
});

test('computeKeyPlan: picker mode -- BACK replaces VIEW, view-options fill session slots, current view is flagged', () => {
  const result = deck.computeKeyPlan(
    basePlanParams({ mode: 'picker', viewName: 'work', viewsList: ['all', 'work', 'hidden'] })
  );
  const offenders = deck.findBlankControlFaces(result.plan);
  assert.deepStrictEqual(offenders, []);

  const reserved = deck.reservedControlKeys(4, 8);
  assert.strictEqual(result.plan[reserved.view].role, 'back');
  assert.strictEqual(result.plan[reserved.view].name, '< BACK');

  const slots = deck.sessionSlotIndices(4, 8, reserved);
  const optionFaces = slots.map((i) => result.plan[i]).filter((f) => f.role === 'view-option');
  const names = optionFaces.map((f) => f.body);
  assert.deepStrictEqual(names, ['all', 'work', 'hidden'], '"hidden" flows through untouched -- the deck does no special-casing of its own');
  const workFace = optionFaces.find((f) => f.body === 'work');
  assert.strictEqual(workFace.flags.currentView, true);
  const allFace = optionFaces.find((f) => f.body === 'all');
  assert.strictEqual(allFace.flags.currentView, false);
});

// ─── SETTINGS key on the view picker (settings-discoverability fix) ───────
//
// The 2026-07 "couldn't find it" incident: the ONLY entry point to Settings
// was a 600ms zero-tolerance long-press on the VIEW key, which the user who
// commissioned the feature could not find. These tests guard the fix: a
// real, always-on-every-page SETTINGS key on the view picker (never the
// page picker), present whenever the grid has room for controls at all, and
// absent -- honestly, not silently -- when it doesn't.

test('computeKeyPlan: view picker reserves an always-visible SETTINGS key (settings-discoverability fix)', () => {
  const result = deck.computeKeyPlan(
    basePlanParams({ mode: 'picker', pickerKind: 'view', viewName: 'work', viewsList: ['all', 'work', 'hidden'] })
  );
  const offenders = deck.findBlankControlFaces(result.plan);
  assert.deepStrictEqual(offenders, []);

  const reserved = deck.reservedControlKeys(4, 8);
  const slots = deck.sessionSlotIndices(4, 8, reserved);
  const settingsFace = result.plan[slots[0]];
  assert.strictEqual(settingsFace.role, 'settings', 'the first session slot on the view picker should be the SETTINGS key');
  assert.strictEqual(settingsFace.name, 'SETTINGS');

  // Every OTHER session slot still resolves to a real view option -- the
  // SETTINGS key must never crowd out the picker's actual job.
  const optionFaces = slots.slice(1).map((i) => result.plan[i]).filter((f) => f.role === 'view-option');
  assert.deepStrictEqual(optionFaces.map((f) => f.body), ['all', 'work', 'hidden']);
});

test('computeKeyPlan: SETTINGS stays on every page of the view picker, not just page 0', () => {
  // 4x8 grid, 29 session slots, minus 1 for SETTINGS = 28 view-option slots
  // per page -- comfortably more views than exist here, so this just proves
  // the settings slot is pinned (like BACK/PREV/NEXT) rather than paged.
  const manyViews = Array.from({ length: 40 }, (_, i) => 'view' + i);
  const result = deck.computeKeyPlan(
    basePlanParams({ mode: 'picker', pickerKind: 'view', viewName: 'view0', viewsList: manyViews, pickerPage: 1 })
  );
  const reserved = deck.reservedControlKeys(4, 8);
  const slots = deck.sessionSlotIndices(4, 8, reserved);
  assert.strictEqual(result.plan[slots[0]].role, 'settings', 'SETTINGS must still occupy the same slot on page 2');
});

test('computeKeyPlan: the page picker (pickerKind "page") gets no SETTINGS key -- it is not the settings entry point', () => {
  const result = deck.computeKeyPlan(
    basePlanParams({ mode: 'picker', pickerKind: 'page', pagePickerCount: 3, page: 0 })
  );
  const reserved = deck.reservedControlKeys(4, 8);
  const slots = deck.sessionSlotIndices(4, 8, reserved);
  const roles = slots.map((i) => result.plan[i].role);
  assert.ok(!roles.includes('settings'), 'the page picker should never carve out a SETTINGS slot');
});

test('computeKeyPlan: degenerate grid has no room for controls, so no SETTINGS key either -- said honestly, not papered over', () => {
  const grid = { rows: 1, cols: 5 };
  const reserved = deck.reservedControlKeys(grid.rows, grid.cols);
  assert.strictEqual(reserved.mode, 'degenerate');
  const result = deck.computeKeyPlan(
    basePlanParams({ grid: grid, reserved: reserved, mode: 'picker', pickerKind: 'view', viewsList: ['all', 'work'] })
  );
  const roles = result.plan.map((f) => f.role);
  assert.ok(!roles.includes('settings'), 'a degenerate grid has no controls at all -- SETTINGS is not exempt from that rule');
});

test('computeKeyPlan: a picker with only ONE free slot after BACK/PREV/NEXT keeps that slot for a view option, not SETTINGS', () => {
  // 2x2 grid: reservedControlKeys still reaches 'corners' mode (view=0,
  // prev=2, next=3, all distinct) -- but that leaves exactly ONE session
  // slot. Sacrificing it to SETTINGS would leave zero slots to actually
  // switch views, breaking the picker's primary job to fix a secondary
  // one. This is the edge case the >= 2 guard in computeKeyPlan exists for.
  const grid = { rows: 2, cols: 2 };
  const reserved = deck.reservedControlKeys(grid.rows, grid.cols);
  assert.strictEqual(reserved.mode, 'corners');
  const slots = deck.sessionSlotIndices(grid.rows, grid.cols, reserved);
  assert.strictEqual(slots.length, 1, 'sanity check on the edge-case fixture');

  const result = deck.computeKeyPlan(
    basePlanParams({ grid: grid, reserved: reserved, mode: 'picker', pickerKind: 'view', viewsList: ['all', 'work'] })
  );
  assert.strictEqual(result.plan[slots[0]].role, 'view-option', 'the one free slot must stay a real view option');
  assert.ok(!result.plan.some((f) => f.role === 'settings'), 'no SETTINGS key when there is no room to spare');
});

test('findBlankControlFaces: a settings-role face with real NAME is not flagged (sanity check for the new role)', () => {
  const plan = [{ index: 0, role: 'settings', name: 'SETTINGS', body: '', state: '', flags: {} }];
  assert.deepStrictEqual(deck.findBlankControlFaces(plan), []);
});

test('controlKeyContent("settings", {}) returns a non-blank NAME (regression guard, mirrors the view/prev/next/back cases)', () => {
  assert.deepStrictEqual(deck.controlKeyContent('settings', {}), { name: 'SETTINGS', body: '', state: '' });
});

test('computeKeyPlan: session tiles carry name/state/target straight from the sessions array', () => {
  const result = deck.computeKeyPlan(
    basePlanParams({
      sessions: [
        { name: 'alpha', active: true, needs_attention: false, last_activity_at: null },
        { name: 'beta', active: false, needs_attention: true, last_activity_at: 500 },
      ],
    })
  );
  const reserved = deck.reservedControlKeys(4, 8);
  const slots = deck.sessionSlotIndices(4, 8, reserved);
  const alphaFace = result.plan[slots[0]];
  assert.strictEqual(alphaFace.role, 'session');
  assert.strictEqual(alphaFace.name, 'alpha');
  assert.strictEqual(alphaFace.target, 'alpha');
  assert.strictEqual(alphaFace.flags.active, true);

  const betaFace = result.plan[slots[1]];
  assert.strictEqual(betaFace.flags.needsAttention, true);
});

test('computeKeyPlan: a FAILED tile overrides state text and sets the failed flag', () => {
  const result = deck.computeKeyPlan(
    basePlanParams({
      sessions: [{ name: 'alpha', active: false, needs_attention: false, last_activity_at: null }],
      failedByName: { alpha: 5000 },
      nowMs: 1000,
    })
  );
  const reserved = deck.reservedControlKeys(4, 8);
  const slots = deck.sessionSlotIndices(4, 8, reserved);
  const face = result.plan[slots[0]];
  assert.strictEqual(face.flags.failed, true);
  assert.strictEqual(face.state, 'FAILED');
});

test('computeKeyPlan: degenerate grid returns an all-empty plan (no controls fit)', () => {
  const grid = { rows: 1, cols: 5 };
  const reserved = deck.reservedControlKeys(grid.rows, grid.cols);
  assert.strictEqual(reserved.mode, 'degenerate');
  const result = deck.computeKeyPlan(
    basePlanParams({ grid: grid, reserved: reserved, sessions: [{ name: 'a', active: false }] })
  );
  // Every key is a session slot on a degenerate grid -- but with no
  // sessions supplied beyond one, the rest stay role:'empty'.
  assert.strictEqual(result.plan.length, 5);
  assert.strictEqual(result.plan[0].role, 'session');
  for (let i = 1; i < result.plan.length; i++) {
    assert.strictEqual(result.plan[i].role, 'empty');
  }
});

test('computeKeyPlan: clamps an out-of-range page back into bounds and reports it back to the caller', () => {
  const sessions = Array.from({ length: 5 }, (_, i) => ({ name: 's' + i, active: false }));
  const result = deck.computeKeyPlan(basePlanParams({ sessions: sessions, page: 99 }));
  assert.strictEqual(result.page, 0); // only 1 page of 29-slot capacity for 5 sessions
});

test('findBlankControlFaces: flags a control-role face with empty NAME and BODY (the exact historical bug shape)', () => {
  const plan = [
    { index: 0, role: 'view', name: '', body: '', state: '', flags: {} },
    { index: 1, role: 'session', name: '', body: '', state: '', flags: {} }, // sessions may legitimately have no body
  ];
  const offenders = deck.findBlankControlFaces(plan);
  assert.strictEqual(offenders.length, 1);
  assert.strictEqual(offenders[0].role, 'view');
});

// ─── Layer B golden fixture (DECK_PARITY_ARCHITECTURE.md §6.2) ───────────
//
// deck-layout.fixtures.json pins reserved-control-key geometry, paging, and
// control-key content as a function of (rows, cols) ONLY -- shared with the
// muxplex-deck hardware sidecar's own test suite (see the fixture's
// "asserted_by" field). A case failing here means this client's math
// disagrees with the pinned answer -- exactly the kind of drift
// DECK_PARITY_ARCHITECTURE.md \u00a74.3 says a shared fixture turns into a red
// test instead of a phone glance.

const fixturePath = join(__dirname, '..', 'deck', 'layout.fixtures.json');
const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

test('layout.fixtures.json: reserved-control-key geometry matches deck.js for every case that applies to the soft deck', () => {
  for (const c of fixture.cases) {
    if (!c.applies_to.includes('soft-deck')) continue;
    const reserved = deck.reservedControlKeys(c.caps.key_rows, c.caps.key_cols);
    assert.strictEqual(reserved.mode, c.expect.mode, c.id + ': mode');
    if (c.expect.mode !== 'degenerate') {
      assert.strictEqual(reserved.view, c.expect.view_key, c.id + ': view_key');
      assert.strictEqual(reserved.prev, c.expect.prev_key, c.id + ': prev_key');
      assert.strictEqual(reserved.next, c.expect.next_key, c.id + ': next_key');
    }
    const slots =
      c.expect.mode === 'degenerate'
        ? Array.from({ length: c.caps.key_rows * c.caps.key_cols }, (_, i) => i)
        : deck.sessionSlotIndices(c.caps.key_rows, c.caps.key_cols, reserved);
    assert.deepStrictEqual(slots, c.expect.session_slots, c.id + ': session_slots');
    assert.strictEqual(slots.length, c.expect.sessions_per_page, c.id + ': sessions_per_page');
  }
});

test('layout.fixtures.json: paging cases match pageCount/pageSlice/clampPage', () => {
  for (const p of fixture.paging) {
    if (p.expect_page_count !== undefined) {
      assert.strictEqual(deck.pageCount(p.items, p.per_page), p.expect_page_count, p.id);
    }
    if (p.expect_page_0 !== undefined) {
      assert.deepStrictEqual(deck.pageSlice([], 0, p.per_page), p.expect_page_0, p.id);
    }
    if (p.expect_page_1_len !== undefined) {
      const items = Array.from({ length: p.items }, (_, i) => i);
      assert.strictEqual(deck.pageSlice(items, 1, p.per_page).length, p.expect_page_1_len, p.id);
    }
    if (p.expect !== undefined && p.delta !== undefined) {
      assert.strictEqual(deck.clampPage(p.page, p.delta, p.count), p.expect, p.id);
    }
  }
});

test('layout.fixtures.json: control_key_content cases match controlKeyContent', () => {
  for (const c of fixture.control_key_content) {
    const actual = deck.controlKeyContent(c.role, c.ctx);
    assert.deepStrictEqual(actual, c.expect, c.id);
  }
});

test('layout.fixtures.json: spec_version is present and this file is served statically at /deck/layout.fixtures.json', () => {
  assert.strictEqual(fixture.spec_version, '1');
  assert.strictEqual(fixture.served_at, '/deck/layout.fixtures.json');
});

// ─── deck.css: the grid is fixed/computed, never an auto-fit responsive one ─
//
// This project shipped a prior bug where the grid's row-track sizing
// disagreed with the rendered tile size under `grid-auto-rows: auto` -- see
// git history. That whole class of bug is now structurally impossible: the
// fixed-grid redesign has no `auto-fit`/`auto-fill`/`aspect-ratio`-driven
// sizing left in `.deck-grid` at all. deck.js computes exact `--cell-w`/
// `--cell-h` pixel values every time the grid is (re)computed, and CSS just
// reads them. This test guards against a regression back toward a
// responsive/implicit grid.
test('.deck-grid uses fixed, JS-computed pixel tracks -- not an auto-fit/auto-fill responsive grid', () => {
  const cssPath = join(__dirname, '..', 'deck', 'deck.css');
  const css = fs.readFileSync(cssPath, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

  const gridRuleMatch = css.match(/^#deck-grid\s*\{([^}]*)\}/m);
  assert.ok(gridRuleMatch, '#deck-grid rule should exist in deck.css');
  const ruleBody = gridRuleMatch[1];

  assert.ok(!/auto-fit|auto-fill/.test(ruleBody), '#deck-grid must not use auto-fit/auto-fill -- the grid is a fixed R x C, computed once by deck.js, not a responsive reflow');
  assert.ok(/grid-template-columns\s*:\s*repeat\(var\(--cols\)/.test(ruleBody), '#deck-grid must size columns from the JS-computed --cols/--cell-w custom properties');
  assert.ok(/grid-template-rows\s*:\s*repeat\(var\(--rows\)/.test(ruleBody), '#deck-grid must size rows from the JS-computed --rows/--cell-h custom properties');
});

test('deck.css: no scrollable ancestor anywhere on the deck surface, EXCEPT the settings panel (DESIGN_SOFTDECK.md \u00a78 -- scroll prohibition)', () => {
  const cssPath = join(__dirname, '..', 'deck', 'deck.css');
  const css = fs.readFileSync(cssPath, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(/overflow\s*:\s*hidden/.test(css), 'the root surface must declare overflow: hidden');

  // #deck-settings is a DELIBERATE, scoped exception (BACKLOG.md item 2):
  // it's form data entry (bindings editor, JSON export/import), not the
  // deck's key-grid game surface -- see deck.css's own "Settings panel"
  // section comment. Every OTHER rule in the file must still forbid
  // scrolling; strip #deck-settings's own rule block before asserting.
  const settingsRuleMatch = css.match(/#deck-settings\s*\{[^}]*\}/);
  assert.ok(settingsRuleMatch, '#deck-settings rule should exist in deck.css');
  assert.ok(/overflow-y\s*:\s*auto/.test(settingsRuleMatch[0]), '#deck-settings should be the one scrollable element');
  const cssWithoutSettingsPanel = css.replace(settingsRuleMatch[0], '');

  assert.ok(!/overflow-y\s*:\s*auto/.test(cssWithoutSettingsPanel), 'no element OTHER than #deck-settings should be scrollable (overflow-y: auto)');
  assert.ok(!/overflow-x\s*:\s*auto/.test(cssWithoutSettingsPanel), 'no element should be scrollable (overflow-x: auto)');
});

// ─── Settings menu (BACKLOG.md item 2) ─────────────────────────────────────

// Golden-fixture drift tripwire (DECK_PARITY_ARCHITECTURE.md §6.2 style):
// this literal table mirrors muxplex-deck's controls.py `ACTIONS` dict
// (name -> kind) verbatim. If either repo adds/removes/reclassifies an
// action without updating the other, this test fails loudly instead of
// the two clients silently disagreeing on what "view_cycle" means.
const MUXPLEX_DECK_ACTION_KINDS = {
  session: 'momentary',
  view_picker: 'momentary',
  page_picker: 'momentary',
  page_prev: 'momentary',
  page_next: 'momentary',
  none: 'momentary',
  view_cycle: 'relative',
  page_cycle: 'relative',
  view_all: 'momentary',
  page_first: 'momentary',
  page_last: 'momentary',
  view_prev: 'momentary',
  view_next: 'momentary',
  focus_app: 'momentary',
  refresh_now: 'momentary',
  toggle_last: 'momentary',
  brightness_up: 'momentary',
  brightness_down: 'momentary',
  brightness_cycle: 'relative',
};

test('ACTION_CATALOG mirrors muxplex-deck controls.py\'s 19-action catalog exactly (name + kind)', () => {
  const expectedNames = Object.keys(MUXPLEX_DECK_ACTION_KINDS).sort();
  const actualNames = Object.keys(deck.ACTION_CATALOG).sort();
  assert.deepEqual(actualNames, expectedNames, 'ACTION_CATALOG action names must match muxplex-deck\'s controls.py ACTIONS exactly');
  for (const name of expectedNames) {
    assert.strictEqual(deck.ACTION_CATALOG[name].kind, MUXPLEX_DECK_ACTION_KINDS[name], `action ${name} kind should match muxplex-deck`);
  }
});

test('parseControlAddress: valid key/dial forms', () => {
  assert.deepEqual(deck.parseControlAddress('key.0'), { control: 'key', index: 0, sub: null, text: 'key.0' });
  assert.deepEqual(deck.parseControlAddress('key.31'), { control: 'key', index: 31, sub: null, text: 'key.31' });
  assert.deepEqual(deck.parseControlAddress('dial.2.turn'), { control: 'dial', index: 2, sub: 'turn', text: 'dial.2.turn' });
  assert.deepEqual(deck.parseControlAddress('dial.0.push'), { control: 'dial', index: 0, sub: 'push', text: 'dial.0.push' });
});

test('parseControlAddress: rejects malformed addresses (never throws)', () => {
  assert.strictEqual(deck.parseControlAddress('key.01'), null); // leading zero
  assert.strictEqual(deck.parseControlAddress('key.-1'), null); // sign
  assert.strictEqual(deck.parseControlAddress('key.'), null);
  assert.strictEqual(deck.parseControlAddress('dial.1.spin'), null); // unknown sub
  assert.strictEqual(deck.parseControlAddress('button.3'), null);
  assert.strictEqual(deck.parseControlAddress(''), null);
  assert.strictEqual(deck.parseControlAddress(null), null);
  assert.strictEqual(deck.parseControlAddress(42), null);
});

test('validActionsForAddress: key/dial.push get MOMENTARY + none; dial.turn gets RELATIVE + none', () => {
  const keyActions = deck.validActionsForAddress(deck.parseControlAddress('key.3'));
  assert.ok(keyActions.includes('page_prev'));
  assert.ok(keyActions.includes('none'));
  assert.ok(!keyActions.includes('view_cycle'), 'a RELATIVE action must not be valid on key.N');

  const pushActions = deck.validActionsForAddress(deck.parseControlAddress('dial.0.push'));
  assert.ok(pushActions.includes('refresh_now'));
  assert.ok(!pushActions.includes('page_cycle'));

  const turnActions = deck.validActionsForAddress(deck.parseControlAddress('dial.0.turn'));
  assert.ok(turnActions.includes('view_cycle'));
  assert.ok(turnActions.includes('page_cycle'));
  assert.ok(turnActions.includes('brightness_cycle'));
  assert.ok(turnActions.includes('none'), '"none" must always be valid regardless of kind');
  assert.ok(!turnActions.includes('page_prev'), 'a MOMENTARY action must not be valid on dial.N.turn');
});

test('validActionsForAddress: null address (unparseable) returns empty', () => {
  assert.deepEqual(deck.validActionsForAddress(null), []);
});

test('isValidBinding: combines parse + catalog + kind check', () => {
  assert.strictEqual(deck.isValidBinding('key.3', 'refresh_now'), true);
  assert.strictEqual(deck.isValidBinding('dial.0.turn', 'page_cycle'), true);
  assert.strictEqual(deck.isValidBinding('dial.0.turn', 'page_prev'), false, 'kind mismatch');
  assert.strictEqual(deck.isValidBinding('key.3', 'not_a_real_action'), false);
  assert.strictEqual(deck.isValidBinding('not.an.address', 'none'), false);
});

test('sanitizeBindings: drops invalid entries, keeps valid ones', () => {
  const result = deck.sanitizeBindings({
    'key.3': 'refresh_now',
    'key.5': 'not_a_real_action', // invalid action
    'dial.0.turn': 'page_cycle',
    'dial.0.push': 'view_cycle', // kind mismatch -- relative on a push
    'bogus': 'none', // unparseable address
  });
  assert.deepEqual(result, { 'key.3': 'refresh_now', 'dial.0.turn': 'page_cycle' });
});

test('sanitizeBindings: non-object input returns empty', () => {
  assert.deepEqual(deck.sanitizeBindings(null), {});
  assert.deepEqual(deck.sanitizeBindings('not an object'), {});
  assert.deepEqual(deck.sanitizeBindings(undefined), {});
});

test('keyBindingsFromConfig: extracts key.N entries, filters out-of-range and "session"', () => {
  const result = deck.keyBindingsFromConfig(
    { 'key.2': 'refresh_now', 'key.50': 'none', 'key.1': 'session', 'dial.0.turn': 'page_cycle' },
    32 // keyCount
  );
  assert.deepEqual(result, { 2: 'refresh_now' }, 'key.50 out of range and key.1=session (default) are both excluded');
});

test('dialBindingsFromConfig: dense array, default {turn: none, push: none}', () => {
  const result = deck.dialBindingsFromConfig({ 'dial.0.turn': 'page_cycle', 'dial.1.push': 'refresh_now' }, 3);
  assert.deepEqual(result, [
    { turn: 'page_cycle', push: 'none' },
    { turn: 'none', push: 'refresh_now' },
    { turn: 'none', push: 'none' },
  ]);
});

test('actionKeyContent: splits the catalog label into NAME/BODY, STATE always blank', () => {
  assert.deepEqual(deck.actionKeyContent('refresh_now'), { name: 'REFRESH', body: 'NOW', state: '' });
  assert.deepEqual(deck.actionKeyContent('none'), { name: '', body: '', state: '' });
  assert.deepEqual(deck.actionKeyContent('not_a_real_action'), { name: '', body: '', state: '' });
});

test('pageItemLabels: 1-indexed human labels', () => {
  assert.deepEqual(deck.pageItemLabels(3), ['Page 1', 'Page 2', 'Page 3']);
  assert.deepEqual(deck.pageItemLabels(0), []);
});

test('pageOptionContent: NAME blank, BODY is label, STATE marks current', () => {
  assert.deepEqual(deck.pageOptionContent('Page 2', true), { name: '', body: 'Page 2', state: 'current' });
  assert.deepEqual(deck.pageOptionContent('Page 2', false), { name: '', body: 'Page 2', state: '' });
});

test('dialDragTicks: upward drag (negative deltaY) yields positive ticks', () => {
  assert.strictEqual(deck.dialDragTicks(-deck.DIAL_PX_PER_TICK), 1);
  assert.strictEqual(deck.dialDragTicks(-2 * deck.DIAL_PX_PER_TICK), 2);
});

test('dialDragTicks: downward drag yields negative ticks', () => {
  assert.strictEqual(deck.dialDragTicks(deck.DIAL_PX_PER_TICK), -1);
});

test('dialDragTicks: sub-threshold movement yields zero ticks', () => {
  assert.strictEqual(deck.dialDragTicks(5), 0);
  assert.strictEqual(deck.dialDragTicks(-5), 0);
});

test('isDialTap: small + fast release is a tap', () => {
  assert.strictEqual(deck.isDialTap(3, 100), true);
});

test('isDialTap: large displacement is not a tap even if fast', () => {
  assert.strictEqual(deck.isDialTap(50, 100), false);
});

test('isDialTap: small displacement but slow is not a tap', () => {
  assert.strictEqual(deck.isDialTap(3, 500), false);
});

test('applyRelativeTicks: page_cycle clamps via the same rule as page_prev/page_next', () => {
  const ctx = { page: 1, pageCount: 3, viewIndex: 0, viewCount: 1, brightness: 100 };
  assert.deepEqual(deck.applyRelativeTicks('page_cycle', 1, ctx), { page: 2 });
  assert.deepEqual(deck.applyRelativeTicks('page_cycle', 5, ctx), { page: 2 }, 'clamped, never wraps');
  assert.deepEqual(deck.applyRelativeTicks('page_cycle', -5, ctx), { page: 0 });
});

test('applyRelativeTicks: view_cycle clamps an index into viewsList', () => {
  const ctx = { page: 0, pageCount: 1, viewIndex: 1, viewCount: 3, brightness: 100 };
  assert.deepEqual(deck.applyRelativeTicks('view_cycle', 1, ctx), { viewIndex: 2 });
  assert.deepEqual(deck.applyRelativeTicks('view_cycle', 10, ctx), { viewIndex: 2 });
});

test('applyRelativeTicks: brightness_cycle steps by 10%, clamped [10,100]', () => {
  const ctx = { page: 0, pageCount: 1, viewIndex: 0, viewCount: 1, brightness: 95 };
  assert.deepEqual(deck.applyRelativeTicks('brightness_cycle', 1, ctx), { brightness: 100 });
  assert.deepEqual(deck.applyRelativeTicks('brightness_cycle', -20, ctx), { brightness: 10 });
});

test('applyRelativeTicks: zero ticks or a non-relative action is a no-op', () => {
  const ctx = { page: 0, pageCount: 3, viewIndex: 0, viewCount: 1, brightness: 100 };
  assert.deepEqual(deck.applyRelativeTicks('page_cycle', 0, ctx), {});
  assert.deepEqual(deck.applyRelativeTicks('refresh_now', 1, ctx), {});
});

// ─── Emulated touch strip (BACKLOG.md item 2) ───────────────────────────
//
// Verifies the "strip can be functional today" reasoning against the real
// ACTION_CATALOG (see deck.js's own "Emulated touch strip" section
// comment): swipe/tap reuse existing MOMENTARY actions, drag reuses the
// existing RELATIVE actions via a generalized dialDragTicks, and exactly
// one new CONTINUOUS action (brightness_set, in the deliberately separate
// STRIP_ACTION_CATALOG) is added for the strip's absolute-position use
// case -- kept out of ACTION_CATALOG so the cross-repo parity fixture test
// above is untouched.

test('ACTION_CATALOG is untouched by the strip feature -- still exactly the mirrored 19 actions', () => {
  assert.strictEqual(Object.keys(deck.ACTION_CATALOG).length, 19);
  assert.strictEqual('brightness_set' in deck.ACTION_CATALOG, false, 'the new continuous action must live only in STRIP_ACTION_CATALOG');
});

test('STRIP_ACTION_CATALOG: exactly one soft-deck-only CONTINUOUS action, not a family of them', () => {
  const names = Object.keys(deck.STRIP_ACTION_CATALOG);
  assert.deepEqual(names, ['brightness_set']);
  assert.strictEqual(deck.STRIP_ACTION_CATALOG.brightness_set.kind, deck.ACTION_CONTINUOUS);
});

test('catalogSpecFor: finds actions in either catalog', () => {
  assert.strictEqual(deck.catalogSpecFor('refresh_now').kind, deck.ACTION_MOMENTARY);
  assert.strictEqual(deck.catalogSpecFor('view_cycle').kind, deck.ACTION_RELATIVE);
  assert.strictEqual(deck.catalogSpecFor('brightness_set').kind, deck.ACTION_CONTINUOUS);
  assert.strictEqual(deck.catalogSpecFor('not_a_real_action'), undefined);
});

test('parseControlAddress: valid strip forms (zone tap/drag, whole-strip swipe)', () => {
  assert.deepEqual(deck.parseControlAddress('strip.0.tap'), { control: 'strip', index: 0, sub: 'tap', text: 'strip.0.tap' });
  assert.deepEqual(deck.parseControlAddress('strip.3.drag'), { control: 'strip', index: 3, sub: 'drag', text: 'strip.3.drag' });
  assert.deepEqual(deck.parseControlAddress('strip.swipe.left'), { control: 'strip', index: null, sub: 'swipe-left', text: 'strip.swipe.left' });
  assert.deepEqual(deck.parseControlAddress('strip.swipe.right'), { control: 'strip', index: null, sub: 'swipe-right', text: 'strip.swipe.right' });
});

test('parseControlAddress: rejects malformed strip addresses (never throws)', () => {
  assert.strictEqual(deck.parseControlAddress('strip.01.tap'), null); // leading zero
  assert.strictEqual(deck.parseControlAddress('strip.-1.tap'), null); // sign
  assert.strictEqual(deck.parseControlAddress('strip.1.spin'), null); // unknown sub
  assert.strictEqual(deck.parseControlAddress('strip.swipe.up'), null); // unknown direction
  assert.strictEqual(deck.parseControlAddress('strip.swipe'), null); // missing direction
  assert.strictEqual(deck.parseControlAddress('strip.tap'), null); // missing zone index
});

test('validActionsForAddress: strip.N.tap and strip.swipe.left/right get MOMENTARY + none, like key.N', () => {
  const tapActions = deck.validActionsForAddress(deck.parseControlAddress('strip.0.tap'));
  assert.ok(tapActions.includes('page_prev'));
  assert.ok(tapActions.includes('none'));
  assert.ok(!tapActions.includes('view_cycle'), 'a RELATIVE action must not be valid on strip.N.tap');
  assert.ok(!tapActions.includes('brightness_set'), 'a CONTINUOUS action must not be valid on strip.N.tap');

  const swipeActions = deck.validActionsForAddress(deck.parseControlAddress('strip.swipe.left'));
  assert.ok(swipeActions.includes('view_prev'));
  assert.ok(swipeActions.includes('none'));
  assert.ok(!swipeActions.includes('page_cycle'));
});

test('validActionsForAddress: strip.N.drag gets RELATIVE (shared) + CONTINUOUS (strip-only) + none', () => {
  const dragActions = deck.validActionsForAddress(deck.parseControlAddress('strip.0.drag'));
  assert.ok(dragActions.includes('view_cycle'), 'RELATIVE actions from ACTION_CATALOG are valid');
  assert.ok(dragActions.includes('page_cycle'));
  assert.ok(dragActions.includes('brightness_cycle'));
  assert.ok(dragActions.includes('brightness_set'), 'the one CONTINUOUS action is valid on strip.N.drag');
  assert.ok(dragActions.includes('none'));
  assert.ok(!dragActions.includes('page_prev'), 'a MOMENTARY action must not be valid on strip.N.drag');
});

test('isValidBinding: strip addresses combine parse + either-catalog + kind check', () => {
  assert.strictEqual(deck.isValidBinding('strip.0.tap', 'refresh_now'), true);
  assert.strictEqual(deck.isValidBinding('strip.0.drag', 'page_cycle'), true);
  assert.strictEqual(deck.isValidBinding('strip.0.drag', 'brightness_set'), true);
  assert.strictEqual(deck.isValidBinding('strip.0.tap', 'brightness_set'), false, 'CONTINUOUS is only valid on drag');
  assert.strictEqual(deck.isValidBinding('strip.swipe.left', 'view_prev'), true);
  assert.strictEqual(deck.isValidBinding('strip.swipe.left', 'page_cycle'), false, 'kind mismatch');
});

test('sanitizeBindings: drops invalid strip entries, keeps valid ones (including the continuous action)', () => {
  const result = deck.sanitizeBindings({
    'strip.0.tap': 'refresh_now',
    'strip.0.drag': 'brightness_set',
    'strip.1.drag': 'page_prev', // kind mismatch -- momentary on a drag
    'strip.swipe.left': 'view_prev',
    'strip.swipe.up': 'view_prev', // unparseable
  });
  assert.deepEqual(result, {
    'strip.0.tap': 'refresh_now',
    'strip.0.drag': 'brightness_set',
    'strip.swipe.left': 'view_prev',
  });
});

test('stripZoneBindingsFromConfig: dense array, default {tap: none, drag: none}, filters out-of-range', () => {
  const result = deck.stripZoneBindingsFromConfig(
    { 'strip.0.tap': 'refresh_now', 'strip.1.drag': 'brightness_set', 'strip.5.tap': 'view_prev', 'strip.swipe.left': 'view_prev' },
    3
  );
  assert.deepEqual(result, [
    { tap: 'refresh_now', drag: 'none' },
    { tap: 'none', drag: 'brightness_set' },
    { tap: 'none', drag: 'none' },
  ]);
});

test('stripSwipeBindingsFromConfig: extracts the whole-strip pair, default none, ignores zone entries', () => {
  const result = deck.stripSwipeBindingsFromConfig({
    'strip.swipe.left': 'view_prev',
    'strip.0.tap': 'refresh_now',
  });
  assert.deepEqual(result, { left: 'view_prev', right: 'none' });
});

test('stripSwipeBindingsFromConfig: no strip bindings at all returns both none', () => {
  assert.deepEqual(deck.stripSwipeBindingsFromConfig({}), { left: 'none', right: 'none' });
});

test('stripDragTicks: rightward drag (positive deltaX) yields positive ticks -- NOT a sign-flip of dialDragTicks', () => {
  assert.strictEqual(deck.stripDragTicks(deck.DIAL_PX_PER_TICK), 1);
  assert.strictEqual(deck.stripDragTicks(2 * deck.DIAL_PX_PER_TICK), 2);
});

test('stripDragTicks: leftward drag yields negative ticks', () => {
  assert.strictEqual(deck.stripDragTicks(-deck.DIAL_PX_PER_TICK), -1);
});

test('stripDragTicks: sub-threshold movement yields zero ticks', () => {
  assert.strictEqual(deck.stripDragTicks(5), 0);
  assert.strictEqual(deck.stripDragTicks(-5), 0);
});

test('isStripSwipe: large AND fast is a swipe', () => {
  assert.strictEqual(deck.isStripSwipe(deck.STRIP_SWIPE_PX_THRESHOLD, 100), true);
  assert.strictEqual(deck.isStripSwipe(-deck.STRIP_SWIPE_PX_THRESHOLD, 100), true, 'direction-agnostic');
});

test('isStripSwipe: small displacement is never a swipe, even if fast (that shape is a tap)', () => {
  assert.strictEqual(deck.isStripSwipe(3, 50), false);
});

test('isStripSwipe: large but slow displacement is not a swipe (a deliberate scrub, not a flick)', () => {
  assert.strictEqual(deck.isStripSwipe(deck.STRIP_SWIPE_PX_THRESHOLD, deck.STRIP_SWIPE_MS_THRESHOLD + 200), false);
});

test('gesture disambiguation: tap/swipe/drag shapes are mutually exclusive classifications', () => {
  // A tap: small AND fast.
  assert.strictEqual(deck.isDialTap(3, 100), true);
  assert.strictEqual(deck.isStripSwipe(3, 100), false);
  // A swipe: large AND fast (but not small).
  assert.strictEqual(deck.isDialTap(80, 150), false);
  assert.strictEqual(deck.isStripSwipe(80, 150), true);
  // A slow scrub-drag: large displacement, slow -- neither tap nor swipe;
  // this is exactly the shape that should fall through to progressive
  // tick/absolute emission during pointermove rather than a release-time
  // classification (see wireTouchStrip's pointermove handler).
  assert.strictEqual(deck.isDialTap(80, 900), false);
  assert.strictEqual(deck.isStripSwipe(80, 900), false);
});

test('stripAbsoluteFraction: clamped [0,1] linear position within a zone rect', () => {
  assert.strictEqual(deck.stripAbsoluteFraction(0, 0, 100), 0);
  assert.strictEqual(deck.stripAbsoluteFraction(50, 0, 100), 0.5);
  assert.strictEqual(deck.stripAbsoluteFraction(100, 0, 100), 1);
});

test('stripAbsoluteFraction: clamps outside the zone rect (finger dragged past the edge)', () => {
  assert.strictEqual(deck.stripAbsoluteFraction(-20, 0, 100), 0);
  assert.strictEqual(deck.stripAbsoluteFraction(150, 0, 100), 1);
});

test('stripAbsoluteFraction: zero-width rect (not yet measured) is a safe zero, never divides by zero', () => {
  assert.strictEqual(deck.stripAbsoluteFraction(50, 0, 0), 0);
});

test('applyContinuousValue: brightness_set maps fraction 0..1 onto the [10,100] range', () => {
  assert.deepEqual(deck.applyContinuousValue('brightness_set', 0), { brightness: 10 });
  assert.deepEqual(deck.applyContinuousValue('brightness_set', 1), { brightness: 100 });
  assert.deepEqual(deck.applyContinuousValue('brightness_set', 0.5), { brightness: 55 });
});

test('applyContinuousValue: unknown/non-continuous action is a no-op', () => {
  assert.deepEqual(deck.applyContinuousValue('refresh_now', 0.5), {});
  assert.deepEqual(deck.applyContinuousValue('not_a_real_action', 0.5), {});
});

test('contentBoxForStrip: zero stripCount is a no-op (byte-identical geometry to before the feature existed)', () => {
  assert.deepEqual(deck.contentBoxForStrip({ w: 400, h: 800 }, 0), { w: 400, h: 800 });
});

test('contentBoxForStrip: positive stripCount reserves TOUCH_STRIP_H from height only', () => {
  assert.deepEqual(deck.contentBoxForStrip({ w: 400, h: 800 }, 2), { w: 400, h: 800 - deck.TOUCH_STRIP_H });
});

test('contentBoxForStrip and contentBoxForDials compose independently (both reservations stack)', () => {
  const afterStrip = deck.contentBoxForStrip({ w: 400, h: 800 }, 1);
  const afterBoth = deck.contentBoxForDials(afterStrip, 1);
  assert.deepEqual(afterBoth, { w: 400, h: 800 - deck.TOUCH_STRIP_H - deck.DIAL_STRIP_H });
});

// ─── stripReservationOffsets ───────────────────────────────────────────────
//
// Regression coverage for the grid/strip overlap bug: #deck-dial-strip and
// #deck-touch-strip are `position: fixed` (out of flow), so #deck-root's
// flex centering only ever saw #deck-grid -- the grid centered against the
// FULL viewport height while the reserved band overlapped it at the bottom.
// stripReservationOffsets is the pure arithmetic applyStripOffsets (DOM-
// bound, untestable here) writes onto #deck-root as --reserved-bottom /
// --touch-strip-bottom. No DOM/CSS layout engine is available in this test
// file (deliberately -- no jsdom/playwright dependency), so real rect
// measurement lives in the real-Chromium scratch verification instead; this
// pins the numbers that feed it.

test('stripReservationOffsets: neither dials nor strip -- both offsets zero (byte-identical to before either feature existed)', () => {
  assert.deepEqual(deck.stripReservationOffsets(0, 0), { reservedBottom: 0, touchStripBottom: 0 });
});

test('stripReservationOffsets: dials only -- reserves DIAL_STRIP_H (touchStripBottom is DIAL_STRIP_H too, but #deck-touch-strip is hidden via stripCount<=0 so it has no visible effect)', () => {
  assert.deepEqual(deck.stripReservationOffsets(4, 0), {
    reservedBottom: deck.DIAL_STRIP_H,
    touchStripBottom: deck.DIAL_STRIP_H,
  });
});

test('stripReservationOffsets: strip only -- reserves TOUCH_STRIP_H, touch strip stays at bottom:0', () => {
  assert.deepEqual(deck.stripReservationOffsets(0, 4), {
    reservedBottom: deck.TOUCH_STRIP_H,
    touchStripBottom: 0,
  });
});

test('stripReservationOffsets: both enabled -- reserves the sum, and touch strip stacks ABOVE the dial strip', () => {
  assert.deepEqual(deck.stripReservationOffsets(4, 4), {
    reservedBottom: deck.DIAL_STRIP_H + deck.TOUCH_STRIP_H,
    touchStripBottom: deck.DIAL_STRIP_H,
  });
});

test('stripReservationOffsets: dialCount/stripCount magnitude does not change the reservation (only enabled/disabled matters, matching contentBoxForDials/contentBoxForStrip)', () => {
  assert.deepEqual(deck.stripReservationOffsets(2, 2), deck.stripReservationOffsets(4, 4));
});

// ─── buildStripStatusMessage / buildStripPickerStatusMessage ───────────────
//
// The strip's LIVE STATUS content (BACKLOG.md "use the strip like Stream
// Deck+" parity work) -- soft-deck analogue of muxplex-deck's
// `_build_strip_message` / `_build_picker_strip_message` (main.py).
// Investigation established the real hardware's touch strip is a single
// continuously-live status headline (rendering.py's `render_status_strip`
// draws exactly one centered line), NOT per-dial labels/values, and that
// touch input on the physical strip is unassigned in v1 (`_on_touch`) --
// see deck.js's buildStripStatusMessage doc comment for the full citation.

test('buildStripStatusMessage: single page omits the page indicator (matches the session-strip convention)', () => {
  const msg = deck.buildStripStatusMessage({
    viewLabel: 'work',
    page: 0,
    pageCount: 1,
    sessionCount: 3,
    activeName: 'shell',
  });
  assert.strictEqual(msg, 'work \u00b7 3 sessions \u00b7 ACTIVE: shell');
});

test('buildStripStatusMessage: multi-page shows a 1-indexed "pN/total" segment', () => {
  const msg = deck.buildStripStatusMessage({
    viewLabel: 'all',
    page: 1,
    pageCount: 4,
    sessionCount: 40,
    activeName: 'build',
  });
  assert.strictEqual(msg, 'all \u00b7 p2/4 \u00b7 40 sessions \u00b7 ACTIVE: build');
});

test('buildStripStatusMessage: singular "session" for exactly one session', () => {
  const msg = deck.buildStripStatusMessage({
    viewLabel: 'work',
    page: 0,
    pageCount: 1,
    sessionCount: 1,
    activeName: null,
  });
  assert.match(msg, /\b1 session\b(?! s)/);
  assert.doesNotMatch(msg, /1 sessions/);
});

test('buildStripStatusMessage: no active session reads "ACTIVE: none", never blank', () => {
  const msg = deck.buildStripStatusMessage({
    viewLabel: 'work',
    page: 0,
    pageCount: 1,
    sessionCount: 0,
    activeName: null,
  });
  assert.match(msg, /ACTIVE: none$/);
});

test('buildStripStatusMessage: missing viewLabel falls back to "all", not blank/undefined', () => {
  const msg = deck.buildStripStatusMessage({
    viewLabel: '',
    page: 0,
    pageCount: 1,
    sessionCount: 0,
    activeName: null,
  });
  assert.ok(msg.startsWith('all \u00b7'));
});

test('buildStripPickerStatusMessage: single-page picker omits the range hint', () => {
  const msg = deck.buildStripPickerStatusMessage({ kind: 'VIEW', start: 0, total: 3, pageSize: 6 });
  assert.strictEqual(msg, 'VIEW PICKER -- tap to choose');
});

test('buildStripPickerStatusMessage: multi-page picker shows a "first-last/total" window', () => {
  const msg = deck.buildStripPickerStatusMessage({ kind: 'PAGE', start: 6, total: 20, pageSize: 6 });
  assert.strictEqual(msg, 'PAGE PICKER -- tap to choose \u00b7 7-12/20');
});

test('buildStripPickerStatusMessage: last window clamps to total, never overshoots', () => {
  const msg = deck.buildStripPickerStatusMessage({ kind: 'VIEW', start: 18, total: 20, pageSize: 6 });
  assert.strictEqual(msg, 'VIEW PICKER -- tap to choose \u00b7 19-20/20');
});

test('computeKeyPlan: picker mode returns pickerTotal/pickerPageSize matching its own reservation-aware pagination (single source of truth for the strip status line)', () => {
  const g = deck.computeGridForShape(4, 6);
  const reserved = deck.reservedControlKeys(g.rows, g.cols);
  const result = deck.computeKeyPlan(
    basePlanParams({
      mode: 'picker',
      pickerKind: 'view',
      pickerPage: 0,
      viewsList: ['all', 'work', 'personal', 'scratch', 'ops'],
      grid: g,
      reserved: reserved,
    })
  );
  const slots = deck.sessionSlotIndices(g.rows, g.cols, reserved, {});
  // View picker reserves one settings slot when >=2 slots exist (see
  // computeKeyPlan's own comment) -- pickerPageSize must reflect that, not
  // the raw slot count, or the strip's window hint would silently disagree
  // with what the grid itself is showing.
  const expectedPageSize = slots.length >= 2 ? slots.length - 1 : slots.length;
  assert.strictEqual(result.pickerTotal, 5);
  assert.strictEqual(result.pickerPageSize, expectedPageSize);
});

test('defaultDeckSettings: stripCount defaults to 0, independent of dialCount', () => {
  const d = deck.defaultDeckSettings();
  assert.strictEqual(d.stripCount, 0);
});

test('mergeDeckSettings: valid stripCount is adopted', () => {
  const merged = deck.mergeDeckSettings(deck.defaultDeckSettings(), { stripCount: 3 });
  assert.strictEqual(merged.stripCount, 3);
});

test('mergeDeckSettings: out-of-range stripCount falls back to default', () => {
  const merged = deck.mergeDeckSettings(deck.defaultDeckSettings(), { stripCount: 99 });
  assert.strictEqual(merged.stripCount, deck.defaultDeckSettings().stripCount);
});

test('defaultDeckSettings: sane, valid-by-construction defaults', () => {
  const d = deck.defaultDeckSettings();
  assert.strictEqual(d.sort, 'attention');
  assert.strictEqual(d.gridOverride, null);
  assert.strictEqual(d.dialCount, 0);
  assert.strictEqual(d.brightness, 100);
  assert.deepEqual(d.bindings, {});
});

test('mergeDeckSettings: valid incoming fields are adopted', () => {
  const merged = deck.mergeDeckSettings(deck.defaultDeckSettings(), {
    sort: 'server',
    pollIntervalMs: 3000,
    gridOverride: { rows: 3, cols: 5 },
    dialCount: 2,
    // brightness deliberately NOT asserted "adopted" here: BACKLOG.md item 4
    // (docs/plans/2026-08-06-settings-recovery-plan.md §6.1) made brightness
    // session-local -- mergeDeckSettings never reads it from `incoming` at
    // all now. See the dedicated
    // "mergeDeckSettings: ignores incoming brightness entirely" test below.
    bindings: { 'key.1': 'refresh_now' },
  });
  assert.strictEqual(merged.sort, 'server');
  assert.strictEqual(merged.pollIntervalMs, 3000);
  assert.deepEqual(merged.gridOverride, { rows: 3, cols: 5 });
  assert.strictEqual(merged.dialCount, 2);
  assert.strictEqual(merged.brightness, 100);
  assert.deepEqual(merged.bindings, { 'key.1': 'refresh_now' });
});

test('mergeDeckSettings: out-of-range / wrong-typed fields fall back to defaults individually', () => {
  const merged = deck.mergeDeckSettings(deck.defaultDeckSettings(), {
    sort: 'bogus',
    pollIntervalMs: 1, // too low
    gridOverride: { rows: 99, cols: 99 }, // exceeds N_MAX
    dialCount: 10, // exceeds max
    brightness: 5, // below floor
  });
  const defaults = deck.defaultDeckSettings();
  assert.strictEqual(merged.sort, defaults.sort);
  assert.strictEqual(merged.pollIntervalMs, defaults.pollIntervalMs);
  assert.strictEqual(merged.gridOverride, null);
  assert.strictEqual(merged.dialCount, defaults.dialCount);
  assert.strictEqual(merged.brightness, defaults.brightness);
});

test('mergeDeckSettings: non-object incoming returns defaults', () => {
  assert.deepEqual(deck.mergeDeckSettings(deck.defaultDeckSettings(), null), deck.defaultDeckSettings());
});

// Fake localStorage-shaped object for loadDeckSettings/saveDeckSettings.
function fakeStorage(initial) {
  const map = new Map(Object.entries(initial || {}));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    _map: map,
  };
}

test('loadDeckSettings: null storage (as in node --test) returns defaults', () => {
  assert.deepEqual(deck.loadDeckSettings(null), deck.defaultDeckSettings());
});

test('loadDeckSettings: corrupt JSON in storage falls back to defaults', () => {
  const storage = fakeStorage({ [deck.DECK_SETTINGS_KEY]: 'not json{' });
  assert.deepEqual(deck.loadDeckSettings(storage), deck.defaultDeckSettings());
});

test('saveDeckSettings + loadDeckSettings round-trip', () => {
  const storage = fakeStorage();
  const settings = deck.mergeDeckSettings(deck.defaultDeckSettings(), { sort: 'server', dialCount: 2 });
  deck.saveDeckSettings(storage, settings);
  const loaded = deck.loadDeckSettings(storage);
  assert.strictEqual(loaded.sort, 'server');
  assert.strictEqual(loaded.dialCount, 2);
});

test('saveDeckSettings: a throwing storage (full/private-browsing) is swallowed', () => {
  const throwingStorage = {
    setItem: () => {
      throw new Error('QuotaExceededError');
    },
  };
  assert.doesNotThrow(() => deck.saveDeckSettings(throwingStorage, deck.defaultDeckSettings()));
});

test('exportSettingsJSON + importSettingsJSON: round-trips a settings object', () => {
  const settings = deck.mergeDeckSettings(deck.defaultDeckSettings(), {
    sort: 'server',
    bindings: { 'key.2': 'refresh_now' },
  });
  const text = deck.exportSettingsJSON(settings);
  const result = deck.importSettingsJSON(text);
  assert.strictEqual(result.error, null);
  assert.strictEqual(result.settings.sort, 'server');
  assert.deepEqual(result.settings.bindings, { 'key.2': 'refresh_now' });
});

test('importSettingsJSON: invalid JSON returns an error, never throws', () => {
  const result = deck.importSettingsJSON('{not valid json');
  assert.strictEqual(result.settings, null);
  assert.ok(result.error && result.error.includes('Invalid JSON'));
});

test('computeGridForShape: forced 3x5 shape at a comfortably large box fills without letterboxing', () => {
  const g = deck.computeGridForShape(1000, 600, 3, 5);
  assert.strictEqual(g.rows, 3);
  assert.strictEqual(g.cols, 5);
  assert.ok(g.cellW > 0 && g.cellH > 0);
});

test('computeGridForShape: zero/negative box returns the same degenerate shape as computeGrid', () => {
  const g = deck.computeGridForShape(0, 600, 3, 5);
  assert.strictEqual(g.rows, 0);
  assert.strictEqual(g.tooSmall, true);
});

test('computeEffectiveGrid: no override falls back to plain computeGrid (byte-identical for existing devices)', () => {
  const a = deck.computeGrid(844, 390);
  const b = deck.computeEffectiveGrid(844, 390, null);
  assert.deepEqual(a, b);
});

test('computeEffectiveGrid: a valid override forces the shape', () => {
  const g = deck.computeEffectiveGrid(1000, 600, { rows: 2, cols: 4 });
  assert.strictEqual(g.rows, 2);
  assert.strictEqual(g.cols, 4);
});

// ─── Settings recovery (BACKLOG.md item 4 / docs/plans/2026-08-06-settings-recovery-plan.md) ───

// U3: gridOverrideReachability -- false for every degenerate shape and for
// 2x2; true for 2x3 and 3x2. Table taken verbatim from the plan's §2.3
// fixture (verified against the real exported functions).
test('gridOverrideReachability: refuses every degenerate shape and 2x2, accepts 2x3/3x2 and larger', () => {
  const refused = [
    [1, 1],
    [1, 4],
    [4, 1],
    [2, 2],
  ];
  for (const [rows, cols] of refused) {
    const r = deck.gridOverrideReachability(rows, cols);
    assert.strictEqual(r.ok, false, `${rows}x${cols} must be refused`);
    assert.ok(r.reason, `${rows}x${cols} must carry a reason`);
  }
  const accepted = [
    [2, 3],
    [3, 2],
    [12, 2],
    [2, 12],
    [8, 4],
    [4, 8],
  ];
  for (const [rows, cols] of accepted) {
    const r = deck.gridOverrideReachability(rows, cols);
    assert.strictEqual(r.ok, true, `${rows}x${cols} must be accepted`);
    assert.strictEqual(r.reason, '');
  }
});

// U1: settingsReachability -- the plan's §2.3 fixture table, verbatim, run
// through computeEffectiveGrid at the same 844x390 landscape-phone content
// box the plan verified against, then through settingsReachability. Expected
// levels are derived from §3's definition table.
test('settingsReachability: matches the §2.3 fixture table at an 844x390 content box', () => {
  const CONTENT_W = 844;
  const CONTENT_H = 390;
  const fixture = [
    { rows: 1, cols: 1, level: 'none', reason: 'grid-degenerate' },
    { rows: 1, cols: 4, level: 'none', reason: 'grid-degenerate' },
    { rows: 4, cols: 1, level: 'none', reason: 'grid-degenerate' },
    { rows: 2, cols: 2, level: 'longpress-only', reason: 'grid-too-few-keys' },
    { rows: 2, cols: 3, level: 'full', reason: null },
    { rows: 3, cols: 2, level: 'full', reason: null },
    { rows: 12, cols: 2, level: 'none', reason: 'grid-too-small' },
    { rows: 2, cols: 12, level: 'none', reason: 'grid-too-small' },
    { rows: 8, cols: 4, level: 'none', reason: 'grid-too-small' },
    { rows: 4, cols: 8, level: 'full', reason: null },
  ];
  for (const row of fixture) {
    const g = deck.computeEffectiveGrid(CONTENT_W, CONTENT_H, { rows: row.rows, cols: row.cols });
    const reach = deck.settingsReachability({
      rows: g.rows,
      cols: g.cols,
      tooSmall: g.tooSmall,
      boundKeys: {},
      gridOverride: { rows: row.rows, cols: row.cols },
    });
    assert.strictEqual(
      reach.level,
      row.level,
      `${row.rows}x${row.cols}: expected level "${row.level}", got "${reach.level}" (reasons: ${JSON.stringify(reach.reasons)})`
    );
    if (row.reason) {
      assert.ok(
        reach.reasons.includes(row.reason),
        `${row.rows}x${row.cols}: expected reason "${row.reason}" in ${JSON.stringify(reach.reasons)}`
      );
    } else {
      assert.deepEqual(reach.reasons, []);
    }
  }
});

// U2: settingsReachability distinguishes 'grid-too-few-keys' (the grid
// itself has no room, even unbound) from 'bindings-consumed-slots' (the
// grid WOULD have room, but bindings ate it) -- same 3x2 grid, evaluated
// once bare and once with two of its three open slots bound.
test('settingsReachability: distinguishes grid-too-few-keys from bindings-consumed-slots on the same grid', () => {
  const bare = deck.settingsReachability({ rows: 3, cols: 2, tooSmall: false, boundKeys: {}, gridOverride: { rows: 3, cols: 2 } });
  assert.strictEqual(bare.level, 'full');
  assert.deepEqual(bare.reasons, []);

  // 3x2 corners mode: reserved = {view:0, prev:4, next:5}; open slots are 1,2,3
  // (3 slots). Binding two of them (1 and 2) leaves only 1 open slot -- below
  // the 2 the picker needs to place SETTINGS alongside a session tile.
  const bound = deck.settingsReachability({
    rows: 3,
    cols: 2,
    tooSmall: false,
    boundKeys: { 1: 'refresh_now', 2: 'refresh_now' },
    gridOverride: { rows: 3, cols: 2 },
  });
  assert.strictEqual(bound.level, 'longpress-only');
  assert.deepEqual(bound.reasons, ['bindings-consumed-slots']);
});

// Regression test: gridOverride 12x2 on a landscape phone (844x390 content
// box) must never produce a completely blank black screen. This pins the
// live bug the plan's §2.3 B section documents: computeGridForShape can
// return `tooSmall: true` with NON-ZERO rows/cols, which defeated the old
// render() guard (`grid.rows === 0 || grid.cols === 0`) entirely -- CSS hid
// #deck-surface via `.too-small` while the takeover never appeared. This
// suite has no DOM (see file header), so it cannot execute the real
// render()/boot() DOM closure directly; instead it pins the exact guard
// PREDICATE render() now uses (`!grid || grid.rows === 0 || grid.cols === 0
// || grid.tooSmall`, deck.js's render()) against the real, exported
// computeEffectiveGrid()/settingsReachability() outputs for this shape --
// proving both that the bug precondition reproduces and that the fixed
// guard (unlike the old one) fires for it. The DOM-level assertion that the
// takeover actually renders is smoke test S1 (see plan §8) -- run against a
// real installed PWA.
test('regression: 12x2 gridOverride on a landscape phone reproduces tooSmall with non-zero rows/cols (deck.js:2241 blank-screen bug)', () => {
  const g = deck.computeEffectiveGrid(844, 390, { rows: 12, cols: 2 });

  // Reproduce the bug's exact precondition, verbatim against the plan's own
  // verified table (§2.3 B): tooSmall is true, but rows/cols are NOT zero.
  assert.strictEqual(g.rows, 12);
  assert.strictEqual(g.cols, 2);
  assert.strictEqual(g.tooSmall, true);

  // The OLD guard (pre-fix) would never have shown the takeover for this
  // shape -- this is the bug itself, pinned so it cannot silently return.
  const oldGuardShowsTakeover = g.rows === 0 || g.cols === 0;
  assert.strictEqual(oldGuardShowsTakeover, false, 'reproduces the blank-screen bug precondition exactly');

  // The FIXED guard (deck.js's render(), current source) must show the
  // takeover instead.
  const fixedGuardShowsTakeover = !g || g.rows === 0 || g.cols === 0 || g.tooSmall;
  assert.strictEqual(fixedGuardShowsTakeover, true, 'the fixed render() guard must show the takeover, not a blank screen');

  // And the boot-time detector must independently agree this is unreachable,
  // for the right reason -- not just "no keys rendered" but "here is why."
  const reach = deck.settingsReachability({ rows: g.rows, cols: g.cols, tooSmall: g.tooSmall, boundKeys: {}, gridOverride: { rows: 12, cols: 2 } });
  assert.deepEqual(reach, { level: 'none', reasons: ['grid-too-small'] });
});

// ─── Soft deck settings menu defects (BACKLOG.md item 2 / docs/plans/2026-08-06-soft-deck-settings-menu-plan.md) ───

// U1: bindingApplicability -- key.20 is out-of-range on a 3x4 (12-key)
// grid; key.1 on the same grid applies.
test('bindingApplicability: key-out-of-range for key.N beyond the grid, applies for an in-range key', () => {
  const shape = { rows: 3, cols: 4, dialCount: 0, stripCount: 0 };
  const entries = deck.bindingApplicability({ 'key.20': 'refresh_now', 'key.1': 'refresh_now' }, shape);
  const byAddr = Object.fromEntries(entries.map((e) => [e.address, e]));
  assert.strictEqual(byAddr['key.20'].applies, false);
  assert.strictEqual(byAddr['key.20'].reason, 'key-out-of-range');
  assert.strictEqual(byAddr['key.1'].applies, true);
  assert.strictEqual(byAddr['key.1'].reason, '');
});

// U2 (design's F1-D, the highest-value finding): key.0 is the VIEW control
// on a 3x2 corners-mode grid -- computeKeyPlan paints the bound face, then
// _setControlFace silently overwrites it (deck.js:1651-1653). Bound but
// dead. The SAME index on a 2x3 bottom-row-mode grid is NOT reserved, and
// must apply -- this is the case that MOVES with grid mode (\u00a70.1.2 point 1),
// so both directions are pinned, not just one.
test('bindingApplicability (F1-D): key.0 is the reserved VIEW control in corners mode, but NOT in bottom-row mode', () => {
  const corners = deck.bindingApplicability({ 'key.0': 'refresh_now' }, { rows: 3, cols: 2, dialCount: 0, stripCount: 0 });
  assert.strictEqual(corners[0].applies, false);
  assert.strictEqual(corners[0].reason, 'key-is-reserved-control');

  const bottomRow = deck.bindingApplicability({ 'key.0': 'refresh_now' }, { rows: 2, cols: 3, dialCount: 0, stripCount: 0 });
  assert.strictEqual(bottomRow[0].applies, true);
  assert.strictEqual(bottomRow[0].reason, '');
});

// U3: dial.N.* -- no-dials at dialCount 0, dial-out-of-range beyond the
// configured count, applies within range.
test('bindingApplicability: no-dials at dialCount 0, dial-out-of-range beyond count, applies within range', () => {
  const noDials = deck.bindingApplicability({ 'dial.0.turn': 'view_cycle' }, { rows: 3, cols: 3, dialCount: 0, stripCount: 0 });
  assert.strictEqual(noDials[0].applies, false);
  assert.strictEqual(noDials[0].reason, 'no-dials');

  const outOfRange = deck.bindingApplicability({ 'dial.2.push': 'refresh_now' }, { rows: 3, cols: 3, dialCount: 2, stripCount: 0 });
  assert.strictEqual(outOfRange[0].applies, false);
  assert.strictEqual(outOfRange[0].reason, 'dial-out-of-range');

  const applies = deck.bindingApplicability({ 'dial.1.turn': 'view_cycle' }, { rows: 3, cols: 3, dialCount: 2, stripCount: 0 });
  assert.strictEqual(applies[0].applies, true);
  assert.strictEqual(applies[0].reason, '');
});

// U4: strip.N.* and strip.swipe.* -- no-strip at stripCount 0 covers BOTH
// the zone-scoped and whole-strip-swipe sub-cases (F1-C's distinct
// sub-case: stripSwipeBindingsFromConfig resolves regardless of
// stripCount, but the strip element itself is hidden at stripCount 0).
test('bindingApplicability: no-strip covers both zone and swipe addresses; strip-zone-out-of-range beyond count; applies within range', () => {
  const noStripZone = deck.bindingApplicability({ 'strip.0.tap': 'refresh_now' }, { rows: 3, cols: 3, dialCount: 0, stripCount: 0 });
  assert.strictEqual(noStripZone[0].applies, false);
  assert.strictEqual(noStripZone[0].reason, 'no-strip');

  const noStripSwipe = deck.bindingApplicability({ 'strip.swipe.left': 'page_next' }, { rows: 3, cols: 3, dialCount: 0, stripCount: 0 });
  assert.strictEqual(noStripSwipe[0].applies, false);
  assert.strictEqual(noStripSwipe[0].reason, 'no-strip');

  const outOfRange = deck.bindingApplicability({ 'strip.3.drag': 'view_cycle' }, { rows: 3, cols: 3, dialCount: 0, stripCount: 2 });
  assert.strictEqual(outOfRange[0].applies, false);
  assert.strictEqual(outOfRange[0].reason, 'strip-zone-out-of-range');

  const appliesSwipe = deck.bindingApplicability({ 'strip.swipe.left': 'page_next' }, { rows: 3, cols: 3, dialCount: 0, stripCount: 1 });
  assert.strictEqual(appliesSwipe[0].applies, true);
  assert.strictEqual(appliesSwipe[0].reason, '');
});

// U5 (F4): focus_app reports unsupported-on-soft-deck at a valid address --
// but evaluation order is address-level FIRST: key.20 -> focus_app on a
// 3x4 grid must report key-out-of-range, NOT the action reason, because
// the address problem is the more actionable fix (\u00a77.1).
test('bindingApplicability (F4): focus_app is unsupported-on-soft-deck at a valid address, but address problems win the evaluation order', () => {
  const valid = deck.bindingApplicability({ 'key.1': 'focus_app' }, { rows: 3, cols: 4, dialCount: 0, stripCount: 0 });
  assert.strictEqual(valid[0].applies, false);
  assert.strictEqual(valid[0].reason, 'unsupported-on-soft-deck');

  const outOfRange = deck.bindingApplicability({ 'key.20': 'focus_app' }, { rows: 3, cols: 4, dialCount: 0, stripCount: 0 });
  assert.strictEqual(outOfRange[0].applies, false);
  assert.strictEqual(outOfRange[0].reason, 'key-out-of-range', 'address-level reason must win over the action-level reason');
});

// U6: ascending by address, one entry per configured binding, no drops, no
// duplicates.
test('bindingApplicability: output is ascending by address, one entry per binding, no drops or duplicates', () => {
  const bindings = { 'key.5': 'refresh_now', 'key.1': 'page_next', 'dial.0.turn': 'view_cycle' };
  const entries = deck.bindingApplicability(bindings, { rows: 3, cols: 3, dialCount: 1, stripCount: 0 });
  assert.strictEqual(entries.length, Object.keys(bindings).length);
  const addrs = entries.map((e) => e.address);
  assert.deepEqual(addrs, [...addrs].sort());
  assert.deepEqual(new Set(addrs).size, addrs.length);
});

// U7: bindingApplicability does not mutate its bindings argument.
test('bindingApplicability: does not mutate its bindings argument', () => {
  const bindings = { 'key.0': 'refresh_now', 'key.20': 'page_next' };
  const before = JSON.parse(JSON.stringify(bindings));
  deck.bindingApplicability(bindings, { rows: 3, cols: 2, dialCount: 0, stripCount: 0 });
  assert.deepEqual(bindings, before);
});

// U8: sanitizeBindings is unaffected by this change -- it still accepts
// out-of-range addresses regardless of shape. Guard against the tempting
// wrong fix: filtering applicability at write time. sanitizeBindings has
// no shape parameter at all, so this pins the absence of one.
test('sanitizeBindings (guard): still accepts key.20/dial.2.turn/strip.3.tap regardless of any shape -- it has no shape parameter', () => {
  assert.strictEqual(deck.sanitizeBindings.length <= 1, true, 'sanitizeBindings must take no shape/device parameter');
  const result = deck.sanitizeBindings({ 'key.20': 'refresh_now', 'dial.2.turn': 'view_cycle', 'strip.3.tap': 'page_next' });
  assert.deepEqual(result, { 'key.20': 'refresh_now', 'dial.2.turn': 'view_cycle', 'strip.3.tap': 'page_next' });
});

// U9: defaultDeckSettings() is unchanged by this item -- no new setting.
// This discharges docs/plans/2026-08-06-settings-recovery-plan.md \u00a710's
// standing rule vacuously (\u00a710 of the settings-menu plan).
test('defaultDeckSettings (U9): unchanged shape -- this item adds no new soft-deck setting', () => {
  const d = deck.defaultDeckSettings();
  assert.deepEqual(Object.keys(d).sort(), ['bindings', 'brightness', 'dialCount', 'gridOverride', 'pollIntervalMs', 'sort', 'stripCount', 'version']);
  assert.deepEqual(d.bindings, {});
  assert.strictEqual(d.brightness, 100);
  assert.strictEqual(d.dialCount, 0);
  assert.strictEqual(d.stripCount, 0);
  assert.strictEqual(d.gridOverride, null);
  assert.strictEqual(d.pollIntervalMs > 0, true);
  assert.strictEqual(d.version, 1);
});

// U10: ACTION_CATALOG/STRIP_ACTION_CATALOG are untouched by this item --
// still exactly 19 + 1. (The cross-repo mirror fixture test itself lives
// elsewhere in this file; this is a cheap local re-assertion that this
// item did not add a 20th action.)
test('ACTION_CATALOG/STRIP_ACTION_CATALOG (U10): untouched by the settings-menu-defects item -- still 19 + 1', () => {
  assert.strictEqual(Object.keys(deck.ACTION_CATALOG).length, 19);
  assert.strictEqual(Object.keys(deck.STRIP_ACTION_CATALOG).length, 1);
});

// F3 regression, the design doc's own repro (\u00a75.3): 3x2 corners mode with
// TWO bindings on the three open session slots drops the picker below the
// 2 slots it needs to place SETTINGS -- 'longpress-only', reason
// 'bindings-consumed-slots'. Bare and one-binding stay 'full'.
test('regression (F3): 3x2 grid + two bindings strands to longpress-only via bindings-consumed-slots', () => {
  const bare = deck.settingsReachability({ rows: 3, cols: 2, tooSmall: false, boundKeys: {}, gridOverride: { rows: 3, cols: 2 } });
  assert.strictEqual(bare.level, 'full');

  const oneBound = deck.settingsReachability({
    rows: 3,
    cols: 2,
    tooSmall: false,
    boundKeys: { 1: 'refresh_now' },
    gridOverride: { rows: 3, cols: 2 },
  });
  assert.strictEqual(oneBound.level, 'full');

  const twoBound = deck.settingsReachability({
    rows: 3,
    cols: 2,
    tooSmall: false,
    boundKeys: { 1: 'refresh_now', 2: 'page_next' },
    gridOverride: { rows: 3, cols: 2 },
  });
  assert.strictEqual(twoBound.level, 'longpress-only');
  assert.deepEqual(twoBound.reasons, ['bindings-consumed-slots']);

  // gridOverrideReachability must still say the SHAPE is fine -- this is a
  // bindings problem, not a grid problem (\u00a75.3's own distinction).
  assert.strictEqual(deck.gridOverrideReachability(3, 2).ok, true);
});

// U4: persistableDeckSettings has no brightness key; every other key
// round-trips unchanged.
test('persistableDeckSettings: excludes brightness, keeps every other key unchanged', () => {
  const settings = deck.mergeDeckSettings(deck.defaultDeckSettings(), {
    sort: 'server',
    pollIntervalMs: 2000,
    gridOverride: { rows: 3, cols: 5 },
    dialCount: 2,
    stripCount: 1,
    bindings: { 'key.2': 'refresh_now' },
  });
  settings.brightness = 42; // simulate a session-local dim, in-memory only
  const persisted = deck.persistableDeckSettings(settings);
  assert.strictEqual('brightness' in persisted, false);
  assert.strictEqual(persisted.sort, 'server');
  assert.strictEqual(persisted.pollIntervalMs, 2000);
  assert.deepEqual(persisted.gridOverride, { rows: 3, cols: 5 });
  assert.strictEqual(persisted.dialCount, 2);
  assert.strictEqual(persisted.stripCount, 1);
  assert.deepEqual(persisted.bindings, { 'key.2': 'refresh_now' });
});

// U5: mergeDeckSettings ignores an incoming brightness, always yielding 100.
test('mergeDeckSettings: ignores incoming brightness entirely, always yields 100', () => {
  const merged = deck.mergeDeckSettings(deck.defaultDeckSettings(), { brightness: 10 });
  assert.strictEqual(merged.brightness, 100);
  const merged2 = deck.mergeDeckSettings(deck.defaultDeckSettings(), { brightness: 55, sort: 'server' });
  assert.strictEqual(merged2.brightness, 100);
  assert.strictEqual(merged2.sort, 'server');
});

// U6: JSON.parse(exportSettingsJSON(s)) has no brightness key.
test('exportSettingsJSON: exported JSON has no brightness key', () => {
  const settings = deck.mergeDeckSettings(deck.defaultDeckSettings(), { sort: 'server' });
  settings.brightness = 33;
  const parsed = JSON.parse(deck.exportSettingsJSON(settings));
  assert.strictEqual('brightness' in parsed, false);
  assert.strictEqual(parsed.sort, 'server');
});

test('contentBoxForDials: dialCount 0 is a no-op (byte-identical box)', () => {
  const box = { w: 800, h: 400 };
  assert.deepEqual(deck.contentBoxForDials(box, 0), box);
});

test('contentBoxForDials: dialCount > 0 reserves DIAL_STRIP_H from height', () => {
  const box = { w: 800, h: 400 };
  const result = deck.contentBoxForDials(box, 2);
  assert.strictEqual(result.w, 800);
  assert.strictEqual(result.h, 400 - deck.DIAL_STRIP_H);
});

test('sessionSlotIndices: excludes explicitly-bound key indices in addition to reserved control keys', () => {
  const reserved = deck.reservedControlKeys(4, 8); // view:0, prev:24, next:31
  const withoutBindings = deck.sessionSlotIndices(4, 8, reserved);
  const withBindings = deck.sessionSlotIndices(4, 8, reserved, { 2: 'refresh_now', 5: 'none' });
  assert.ok(withoutBindings.includes(2) && withoutBindings.includes(5));
  assert.ok(!withBindings.includes(2) && !withBindings.includes(5));
  assert.strictEqual(withBindings.length, withoutBindings.length - 2);
});

test('computeKeyPlan: a bound key renders role "bound" with action content, and is excluded from session slots', () => {
  const reserved = deck.reservedControlKeys(4, 8);
  const sessions = [{ name: 'alpha', active: false, needs_attention: false, last_activity_at: null }];
  const result = deck.computeKeyPlan({
    grid: { rows: 4, cols: 8 },
    reserved: reserved,
    mode: 'grid',
    sessions: sessions,
    viewName: 'all',
    viewsList: ['all'],
    page: 0,
    pickerPage: 0,
    boundKeys: { 1: 'refresh_now' },
    nowMs: Date.now(),
  });
  const boundFace = result.plan[1];
  assert.strictEqual(boundFace.role, 'bound');
  assert.strictEqual(boundFace.target, 'refresh_now');
  assert.strictEqual(boundFace.name, 'REFRESH');
  // The session should NOT have landed on the bound index -- it lands on
  // the next available slot instead.
  assert.notStrictEqual(result.plan[1].role, 'session');
});

test('computeKeyPlan: pickerKind "page" renders page items with correct target and current-page marker', () => {
  const reserved = deck.reservedControlKeys(4, 8);
  const result = deck.computeKeyPlan({
    grid: { rows: 4, cols: 8 },
    reserved: reserved,
    mode: 'picker',
    pickerKind: 'page',
    page: 1,
    pickerPage: 0,
    pagePickerCount: 3,
    viewName: 'all',
    viewsList: ['all'],
    nowMs: Date.now(),
  });
  const slots = deck.sessionSlotIndices(4, 8, reserved);
  // slots[0] should hold "Page 1", slots[1] "Page 2" (the current page,
  // since p.page=1 is 0-indexed page 2), slots[2] "Page 3".
  assert.strictEqual(result.plan[slots[0]].body, 'Page 1');
  assert.strictEqual(result.plan[slots[0]].target, '0');
  assert.strictEqual(result.plan[slots[1]].body, 'Page 2');
  assert.strictEqual(result.plan[slots[1]].flags.currentView, true, 'page 2 (index 1) should be marked current since p.page=1');
  assert.strictEqual(result.plan[slots[0]].flags.currentView, false);
});

// ─── lockLandscapeOrientation / registerServiceWorker ──────────────────────
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
  // Node 21+ ships a built-in read-only `navigator` global (Web platform
  // compat), so a plain assignment throws `Cannot set property navigator of
  // #<Object> which has only a getter`. Redefine it via defineProperty for
  // the duration of this test, then restore the original descriptor.
  const originalDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      serviceWorker: {
        register: (path) => {
          registeredPath = path;
          return Promise.resolve({});
        },
      },
    },
  });
  try {
    deck.registerServiceWorker();
    assert.strictEqual(registeredPath, '/deck/sw.js');
  } finally {
    if (originalDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalDescriptor);
    } else {
      delete globalThis.navigator;
    }
  }
});

test('registerServiceWorker does not throw when registration rejects', async () => {
  const originalDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      serviceWorker: { register: () => Promise.reject(new Error('nope')) },
    },
  });
  try {
    assert.doesNotThrow(() => deck.registerServiceWorker());
    await new Promise((resolve) => setTimeout(resolve, 0));
  } finally {
    if (originalDescriptor) {
      Object.defineProperty(globalThis, 'navigator', originalDescriptor);
    } else {
      delete globalThis.navigator;
    }
  }
});

// ─── sw.js ───────────────────────────────────────────────────────────────────

test('sw.js caches nothing (regression guard: this project has shipped stale-frontend bugs from caching before)', () => {
  const swPath = join(__dirname, '..', 'deck', 'sw.js');
  const src = fs.readFileSync(swPath, 'utf8');
  const lowered = src.toLowerCase();
  assert.ok(!lowered.includes('caches.open'), 'sw.js must never open a Cache Storage cache');
  assert.ok(!lowered.includes('cache.put'), 'sw.js must never write to a cache');
  assert.ok(!lowered.includes('cache.addall'), 'sw.js must never pre-cache assets');
  assert.ok(src.includes("addEventListener('fetch'"), 'sw.js must have a fetch handler (required for the install prompt)');
});

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

test('deck.css: no scrollable ancestor anywhere on the surface (DESIGN_SOFTDECK.md \u00a78 -- scroll prohibition)', () => {
  const cssPath = join(__dirname, '..', 'deck', 'deck.css');
  const css = fs.readFileSync(cssPath, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(/overflow\s*:\s*hidden/.test(css), 'the root surface must declare overflow: hidden');
  assert.ok(!/overflow-y\s*:\s*auto/.test(css), 'no element should be scrollable (overflow-y: auto)');
  assert.ok(!/overflow-x\s*:\s*auto/.test(css), 'no element should be scrollable (overflow-x: auto)');
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

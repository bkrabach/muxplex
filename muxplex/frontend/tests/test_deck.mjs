// Tests for frontend/deck/deck.js's pure logic (no DOM dependency — these
// functions are exported unconditionally; the DOM-wiring block only runs
// when `document` exists, which it deliberately doesn't in this file).

import { createRequire } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

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

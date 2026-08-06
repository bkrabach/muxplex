// Cross-implementation agreement: sortByAttention() vs. the shared fixture.
//
// This consumes the SAME cases as:
// - muxplex/tests/test_attention_order_fixture.py (`_attention_order()`), same repo
// - muxplex-deck/tests/test_attention_fixture.py (`apply_attention_sort()`),
//   which carries a byte-for-byte duplicate of the fixture in that separate repo
//
// docs/API_SEMANTICS.md's "?sort=attention" entry requires all three
// implementations to move together. This fixture is the mechanism that
// turns a drift in any one of them into a test FAILURE instead of a silent
// divergence discovered later in production.

// Browser global stubs -- must be set before importing app.js (mirrors test_app.mjs).
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

const fixturePath = join(__dirname, '..', '..', 'tests', 'fixtures', 'attention_sort_cases.json');
const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

test('sortByAttention matches the shared cross-implementation fixture for every case', () => {
  assert.ok(fixture.cases.length > 0, 'fixture must not be empty -- an empty fixture would pass vacuously');

  for (const testCase of fixture.cases) {
    // sortByAttention() no longer takes an active-session argument at all --
    // active_session in the fixture exists only to prove it has no effect.
    const ordered = app.sortByAttention(testCase.sessions);
    const names = ordered.map((s) => s.name);
    assert.deepStrictEqual(
      names,
      testCase.expected_order,
      `case ${JSON.stringify(testCase.name)}: sortByAttention() produced ${JSON.stringify(names)}, ` +
        `expected ${JSON.stringify(testCase.expected_order)} (see ${fixturePath})`
    );
  }
});

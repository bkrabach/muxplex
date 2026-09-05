// Session-name normalization (muxplex-pfp).
//
// USER REPORT: "if someone types in an invalid name, such as using spaces
// instead of dashes, it should fix it with the right naming, replacing spaces
// with dashes." Sharpened afterwards to: auto-replace LIVE, while typing and
// while pasting -- "no surprises: user cannot create invalid values, so they
// see/know what they are doing/getting."
//
// Before this lane both new-session entry points did exactly `.trim()` and
// POSTed the raw string, and the server answered `HTTP 400: Bad Request`
// (SESSION_NAME_RE, tmux_kit/names.py:43). Typing "my session name" was a dead
// end with no hint about what to do.
//
// The rules mirror amplifier_workspace/tmux.py:54 `session_name_from_path()`,
// which is what actually names the session under the default
// `new_session_template`:
//     re.sub(r"[ :./\\]", "-", name)   # separators -> dash
//     re.sub(r"-{2,}", "-", name)      # collapse runs of dashes
//     name.strip("-")                  # strip leading/trailing dashes
// Deliberately NO case-folding -- amplifier-workspace does not lowercase, so
// lowercasing here would reintroduce the very divergence this removes.
//
// Most of the file drives REAL event handlers against a fake input element
// rather than asserting on source text, because the interesting failures here
// (caret teleporting to the end, a trailing dash getting eaten mid-word, an IME
// buffer corrupted) are behavioral and a source-text assertion cannot see them.

// Browser global stubs -- must be set before importing app.js.
globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

globalThis.document = {
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  // Rich enough that _createSessionInput() can actually build and wire an
  // input, so the factory itself can be exercised rather than merely read.
  createElement: () => makeFakeInput(),
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

// ─── Fake input element ──────────────────────────────────────────────────────

/**
 * A minimal stand-in for HTMLInputElement covering exactly the surface the
 * normalization handler touches: value, the selection pair, setSelectionRange,
 * and addEventListener. `dispatch` fires listeners the way the browser would.
 */
function makeFakeInput() {
  return {
    tagName: 'INPUT',
    type: '',
    value: '',
    className: '',
    placeholder: '',
    spellcheck: true,
    maxLength: -1,
    selectionStart: 0,
    selectionEnd: 0,
    style: {},
    attributes: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    _listeners: {},
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null;
    },
    addEventListener(type, fn) {
      (this._listeners[type] || (this._listeners[type] = [])).push(fn);
    },
    removeEventListener() {},
    setSelectionRange(s, e) { this.selectionStart = s; this.selectionEnd = e; },
    focus() {},
    appendChild() {},
    dispatch(type, ev) {
      for (const fn of this._listeners[type] || []) fn(ev || { type });
    },
  };
}

/**
 * Insert text at the caret exactly as a browser would, then fire the `input`
 * event -- which the browser fires AFTER the value has already been mutated.
 * That ordering is the whole reason a single `input` listener can cover typing
 * and pasting alike.
 */
function insertAtCaret(el, text, inputType) {
  const s = el.selectionStart;
  const e = el.selectionEnd;
  el.value = el.value.slice(0, s) + text + el.value.slice(e);
  const caret = s + text.length;
  el.selectionStart = caret;
  el.selectionEnd = caret;
  el.dispatch('input', { type: 'input', inputType: inputType || 'insertText' });
}

/** Type a string one character at a time, as a human does. */
function typeString(el, str) {
  for (const ch of str) insertAtCaret(el, ch, 'insertText');
}

/** Put the caret at an offset, as clicking back into the middle of a name does. */
function placeCaret(el, offset) {
  el.selectionStart = offset;
  el.selectionEnd = offset;
}

/** A fake input already wired with live normalization. */
function wiredInput() {
  const el = makeFakeInput();
  app._attachSessionNameNormalization(el);
  return el;
}

const SOURCE = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');

// ─── Group A: the helpers exist and are shared ───────────────────────────────

test('app.js exports the session-name normalization helpers', () => {
  for (const name of [
    '_normalizeSessionName',
    '_normalizeSessionNameLive',
    '_attachSessionNameNormalization',
    '_createSessionInput',
  ]) {
    assert.strictEqual(
      typeof app[name], 'function',
      `${name} must be exported so both new-session flows can share one implementation`,
    );
  }
});

// ─── Group B: _normalizeSessionName -- the full submit-time rules ────────────

test('_normalizeSessionName matches amplifier_workspace session_name_from_path()', () => {
  const cases = [
    // [input, expected, why]
    ['my new session', 'my-new-session', 'spaces become dashes -- the reported bug'],
    ['feat/thing v2..final', 'feat-thing-v2-final', 'slashes/dots/spaces -> dash, runs collapse'],
    ['a:b', 'a-b', 'colon is in the separator class'],
    ['a\\b', 'a-b', 'backslash is in the separator class'],
    ['-lead-and-trail-', 'lead-and-trail', 'leading/trailing dashes stripped'],
    ['a---b', 'a-b', 'runs of dashes collapse to one'],
    ['///', '', 'all separators normalizes to nothing at all'],
    ['   ', '', 'whitespace-only normalizes to nothing at all'],
    ['', '', 'empty stays empty'],
    ['already-fine_1', 'already-fine_1', 'a valid name is left completely alone'],
  ];
  for (const [input, expected, why] of cases) {
    assert.strictEqual(app._normalizeSessionName(input), expected, `${JSON.stringify(input)}: ${why}`);
  }
});

test('_normalizeSessionName does NOT case-fold', () => {
  // amplifier-workspace does not lowercase. Lowercasing here would put the name
  // the user sees out of step with the name tmux creates -- the exact bug class
  // this normalization exists to remove.
  assert.strictEqual(app._normalizeSessionName('My New Session'), 'My-New-Session');
  assert.strictEqual(app._normalizeSessionName('ALLCAPS'), 'ALLCAPS');
});

test('_normalizeSessionName tolerates null/undefined without throwing', () => {
  assert.strictEqual(app._normalizeSessionName(null), '');
  assert.strictEqual(app._normalizeSessionName(undefined), '');
});

// ─── Group C: _normalizeSessionNameLive -- per-keystroke rules only ──────────

test('_normalizeSessionNameLive substitutes separators but does NOT collapse or strip', () => {
  // Collapsing/stripping per keystroke is what makes a compound name
  // impossible to type: it eats the dash the user just typed on the way to
  // "my-thing", and it prevents "a--b" from ever being reached.
  assert.strictEqual(app._normalizeSessionNameLive('my '), 'my-', 'space -> dash immediately');
  assert.strictEqual(app._normalizeSessionNameLive('my-'), 'my-', 'trailing dash survives');
  assert.strictEqual(app._normalizeSessionNameLive('a--b'), 'a--b', 'runs are NOT collapsed live');
  assert.strictEqual(app._normalizeSessionNameLive('-x'), '-x', 'leading dash is NOT stripped live');
});

test('_normalizeSessionNameLive is strictly length-preserving', () => {
  // This is what lets the caret offsets carry straight over. If a future rule
  // change breaks it, this test says so before a user finds their cursor at
  // the wrong end of the field.
  for (const s of ['my new session', 'a:b/c.d\\e', '   ', 'plain']) {
    assert.strictEqual(
      app._normalizeSessionNameLive(s).length, s.length,
      `live normalization must be 1 char -> 1 char for ${JSON.stringify(s)}`,
    );
  }
});

// ─── Group D: live typing ────────────────────────────────────────────────────

test('typing a space puts a dash in the field immediately', () => {
  const el = wiredInput();
  typeString(el, 'my session');
  assert.strictEqual(el.value, 'my-session', 'the field can never hold the invalid value');
});

test('typing does not corrupt a name that needs no fixing', () => {
  const el = wiredInput();
  typeString(el, 'already-fine_1');
  assert.strictEqual(el.value, 'already-fine_1');
});

test('typing a trailing dash does not eat it', () => {
  // The naive implementation applies strip("-") per keystroke, deletes the dash
  // the instant it is typed, and makes a compound name impossible to enter.
  const el = wiredInput();
  typeString(el, 'my-');
  assert.strictEqual(el.value, 'my-', 'the dash the user just typed must survive');
  typeString(el, 'thing');
  assert.strictEqual(el.value, 'my-thing');
});

test('typing a run of dashes is not collapsed mid-word', () => {
  const el = wiredInput();
  typeString(el, 'a--b');
  assert.strictEqual(el.value, 'a--b', 'collapse is a submit/blur rule, not a keystroke rule');
});

test('caret stays where the user put it when a mid-string space is normalized', () => {
  // The bug this pins: rewriting input.value parks the caret at the end, so
  // clicking back into the middle of a name and typing a space teleports the
  // cursor to the end of the field.
  const el = wiredInput();
  el.value = 'mysession';
  placeCaret(el, 2);              // caret sits right after "my"
  insertAtCaret(el, ' ');         // user types a space there

  assert.strictEqual(el.value, 'my-session', 'the space became a dash');
  assert.strictEqual(el.selectionStart, 3, 'caret must stay just after the new dash, not jump to the end');
  assert.strictEqual(el.selectionEnd, 3);
});

test('caret is untouched when nothing needed normalizing', () => {
  const el = wiredInput();
  el.value = 'mysession';
  placeCaret(el, 2);
  insertAtCaret(el, 'X');
  assert.strictEqual(el.value, 'myXsession');
  assert.strictEqual(el.selectionStart, 3, 'a no-op normalization must not move the caret either');
});

test('continuing to type from a restored caret keeps building the right name', () => {
  const el = wiredInput();
  typeString(el, 'session');
  placeCaret(el, 0);
  typeString(el, 'my ');   // prepend "my " at the front
  assert.strictEqual(el.value, 'my-session');
  assert.strictEqual(el.selectionStart, 3, 'caret follows the inserted text');
});

// ─── Group E: paste ──────────────────────────────────────────────────────────

test('pasting a name with spaces normalizes the whole paste immediately', () => {
  const el = wiredInput();
  insertAtCaret(el, 'my new session', 'insertFromPaste');
  assert.strictEqual(el.value, 'my-new-session', 'a paste must be fixed on arrival, not on submit');
});

test('pasting mixed separators normalizes every one of them', () => {
  const el = wiredInput();
  insertAtCaret(el, 'feat/thing v2..final', 'insertFromPaste');
  // Live rules only: substitution happens now, collapsing waits for blur/submit.
  assert.strictEqual(el.value, 'feat-thing-v2--final');
  assert.strictEqual(
    app._normalizeSessionName(el.value), 'feat-thing-v2-final',
    'and submit-time tidying finishes the job',
  );
});

test('pasting into the middle of an existing name keeps the caret after the paste', () => {
  const el = wiredInput();
  el.value = 'pre';
  placeCaret(el, 3);
  insertAtCaret(el, ' mid ', 'insertFromPaste');
  assert.strictEqual(el.value, 'pre-mid-');
  assert.strictEqual(el.selectionStart, 8, 'caret sits at the end of the pasted run, not the end of the field');
});

// ─── Group F: IME composition ────────────────────────────────────────────────

test('the buffer is not rewritten mid-composition', () => {
  // Rewriting input.value between compositionstart and compositionend corrupts
  // input for anyone typing a language that needs an IME. The composition text
  // here carries a separator purely so the guard is observable.
  const el = wiredInput();
  el.dispatch('compositionstart', { type: 'compositionstart' });
  insertAtCaret(el, 'abc def');
  assert.strictEqual(el.value, 'abc def', 'no rewrite may happen while composing');
});

test('composition result is normalized once, at compositionend', () => {
  const el = wiredInput();
  el.dispatch('compositionstart', { type: 'compositionstart' });
  insertAtCaret(el, 'abc def');
  el.dispatch('compositionend', { type: 'compositionend' });
  assert.strictEqual(el.value, 'abc-def', 'the finished composition is normalized exactly once');
});

test('typing after a composition ends is normalized live again', () => {
  const el = wiredInput();
  el.dispatch('compositionstart', { type: 'compositionstart' });
  insertAtCaret(el, 'abc');
  el.dispatch('compositionend', { type: 'compositionend' });
  typeString(el, ' tail');
  assert.strictEqual(el.value, 'abc-tail', 'the composing flag must be cleared, not left stuck on');
});

// ─── Group G: blur tidying ───────────────────────────────────────────────────

test('blur applies the tidying that is unsafe per keystroke', () => {
  const el = wiredInput();
  el.value = '-my--new-name-';
  el.dispatch('blur', { type: 'blur' });
  assert.strictEqual(el.value, 'my-new-name', 'collapse and strip land once the user stops typing');
});

test('blur leaves an already-tidy name alone', () => {
  const el = wiredInput();
  el.value = 'my-name';
  el.dispatch('blur', { type: 'blur' });
  assert.strictEqual(el.value, 'my-name');
});

// ─── Group H: the factory wires it, so BOTH flows get it ────────────────────

test('_createSessionInput returns an input with live normalization attached', () => {
  // Both showNewSessionInput (inline +) and showFabSessionInput (mobile FAB)
  // build their input through this one factory, so wiring it here is what stops
  // the two flows drifting apart.
  const el = app._createSessionInput();
  typeString(el, 'my session');
  assert.strictEqual(el.value, 'my-session', 'the factory must attach the live handler');
});

test('_createSessionInput keeps its existing autofill suppression', () => {
  const el = app._createSessionInput();
  assert.strictEqual(el.getAttribute('autocomplete'), 'off', 'normalization must not displace _suppressAutofill');
  assert.strictEqual(el.spellcheck, false);
  assert.strictEqual(el.className, 'new-session-input');
});

test('both Enter handlers normalize through the shared helper, not a bare trim', () => {
  for (const fnName of ['function showNewSessionInput(', 'function showFabSessionInput(']) {
    const start = SOURCE.indexOf(fnName);
    assert.ok(start !== -1, `${fnName} must exist`);
    const body = SOURCE.substring(start, start + 1600);
    assert.ok(
      body.includes('_normalizeSessionName(input.value)'),
      `${fnName} Enter handler must submit the normalized name via the shared helper`,
    );
    assert.ok(
      !body.includes('const name = input.value.trim();'),
      `${fnName} must no longer submit the raw trimmed value -- that is what 400s`,
    );
  }
});

test('the separator rule is written down exactly once', () => {
  // One home for the rule is what keeps the two flows -- and the client and
  // amplifier-workspace -- from drifting.
  const occurrences = SOURCE.split('/[ :./\\\\]/g').length - 1;
  assert.strictEqual(occurrences, 1, 'the separator regex literal must appear in exactly one place');
});

test('no length cap is hardcoded in the input factory', () => {
  // This lane (muxplex-pfp) left SESSION_NAME_MAX_LENGTH as a deliberately
  // unset seam because the real number had to be agreed across three repos
  // first. muxplex-1vz filled it in at 255 once muxplex-27o
  // (amplifier-workspace, was 32) and muxplex-i1r (tmux-kit, was 64) had both
  // landed on the same number. What this test still guards is the shape, not
  // the value: the cap lives in ONE named constant and the factory reads it,
  // rather than a literal buried in the handler. The number itself and the
  // bytes-vs-characters reasoning behind it are covered by
  // test_session_name_length_cap.mjs.
  assert.strictEqual(
    typeof app.SESSION_NAME_MAX_LENGTH, 'number',
    'SESSION_NAME_MAX_LENGTH is the seam the cap is set through',
  );
  const start = SOURCE.indexOf('function _createSessionInput(');
  const body = SOURCE.substring(start, start + 600);
  assert.ok(
    !/maxLength\s*=\s*\d/.test(body),
    '_createSessionInput must not hardcode a maxLength literal',
  );
});

test('a name that is only separators is refused with a message, not POSTed', () => {
  assert.strictEqual(app._normalizeSessionName('///'), '', 'nothing survives normalization');
  assert.strictEqual(
    typeof app.SESSION_NAME_ALL_SEPARATORS_MSG, 'string',
    'both flows must share one wording for the refusal',
  );
  for (const fnName of ['function showNewSessionInput(', 'function showFabSessionInput(']) {
    const start = SOURCE.indexOf(fnName);
    const body = SOURCE.substring(start, start + 1600);
    assert.ok(
      body.includes('SESSION_NAME_ALL_SEPARATORS_MSG'),
      `${fnName} must tell the user why nothing happened rather than closing silently`,
    );
  }
});

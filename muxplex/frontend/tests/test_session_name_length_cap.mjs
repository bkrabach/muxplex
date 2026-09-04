// Session-name length cap in the input (muxplex-1vz).
//
// USER REPORT, in their words: "Let's extend that tool support to the max
// *actual* character length supported by tmux and filesystem... If we DO have
// to put a limit in place, then we should set the character limit on the input
// field. The theme here? No surprises -- user cannot create invalid values, so
// they see/know what they are doing/getting."
//
// So this is PREVENTION in the field, not a warning after the fact. Three
// repos had to agree on one number first:
//
//   muxplex-27o  amplifier-workspace  32 (silent truncate) -> 255 BYTES
//                                     (os.pathconf PC_NAME_MAX at call time)
//   muxplex-i1r  tmux-kit             64 (reject)          -> 255 characters
//                                     (SESSION_NAME_RE built from the constant)
//   muxplex-1vz  muxplex (here)       unset seam           -> 255
//
// Re-verified independently on this host before writing any of this:
//   getconf NAME_MAX /home/bkrabach/dev  -> 255
//   mkdir 254 chars OK / 255 OK / 256 ENAMETOOLONG
//   mkdir 85 CJK chars (255 bytes) OK / 86 CJK chars (258 bytes) FAIL
//     ^ NAME_MAX counts BYTES. 86 is far below 255 CHARACTERS, so a
//       character-only check would have wrongly accepted it.
//   tmux 3.4, isolated -L socket + private TMUX_TMPDIR: 255, 256 and 300
//     character names all created rc=0 and round-tripped EXACT. tmux imposes
//     no limit at all; the filesystem is the binding constraint, because the
//     configured new_session_template names a DIRECTORY after the session
//     (`amplifier-workspace ~/dev/{name}`).
//
// The bytes-vs-characters gap is the interesting part and is what most of this
// file is about: HTML `maxlength` counts UTF-16 code units, the filesystem
// counts UTF-8 bytes, and for a non-ASCII name those are different numbers.

// ─── Browser global stubs (must precede the app.js import) ──────────────────

/**
 * A fake element covering the surface this feature touches. Doubles as the
 * <input> and as the hint <span>, since createElement can't know which is
 * wanted and both are exercised here.
 */
function makeFakeEl(tag) {
  return {
    tagName: String(tag || 'input').toUpperCase(),
    type: '',
    value: '',
    className: '',
    placeholder: '',
    title: '',
    textContent: '',
    spellcheck: true,
    maxLength: -1,
    selectionStart: 0,
    selectionEnd: 0,
    style: {},
    attributes: {},
    parentNode: null,
    nextSibling: null,
    _classes: new Set(),
    classList: {
      add(c) { this._owner._classes.add(c); },
      remove(c) { this._owner._classes.delete(c); },
      toggle(c, on) { if (on) this._owner._classes.add(c); else this._owner._classes.delete(c); },
      contains(c) { return this._owner._classes.has(c); },
    },
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
    appendChild(child) { child.parentNode = this; return child; },
    dispatch(type, ev) {
      for (const fn of this._listeners[type] || []) fn(ev || { type });
    },
  };
}

/** A parent node with just enough DOM to host the input and its hint sibling. */
function makeFakeParent() {
  const parent = makeFakeEl('div');
  parent.children = [];
  parent.insertBefore = function (node, ref) {
    const at = ref == null ? this.children.length : this.children.indexOf(ref);
    this.children.splice(at < 0 ? this.children.length : at, 0, node);
    node.parentNode = this;
    return node;
  };
  parent.removeChild = function (node) {
    const at = this.children.indexOf(node);
    if (at >= 0) this.children.splice(at, 1);
    node.parentNode = null;
    return node;
  };
  return parent;
}

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

globalThis.document = {
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => {
    const el = makeFakeEl(tag);
    el.classList._owner = el;
    return el;
  },
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

const SOURCE = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
const STYLE = fs.readFileSync(new URL('../style.css', import.meta.url), 'utf8');

/** Build an input through the real factory, mounted in a real-ish parent. */
function mountedInput() {
  const input = app._createSessionInput();
  input.classList._owner = input;
  const parent = makeFakeParent();
  parent.insertBefore(input, null);
  return { input, parent };
}

/** Set the value and fire `input`, the way a browser does after a keystroke. */
function setValue(input, value) {
  input.value = value;
  input.selectionStart = value.length;
  input.selectionEnd = value.length;
  input.dispatch('input', { type: 'input', inputType: 'insertText' });
}

/** The hint element currently mounted next to the input, or null. */
function hintOf(parent) {
  for (const child of parent.children) {
    if (child.className && String(child.className).indexOf('new-session-length-hint') === 0) {
      return child;
    }
  }
  return null;
}

// ─── Group A: the number, and that there is exactly one of it ───────────────

test('the cap is 255 -- the same number both sibling lanes landed', () => {
  // muxplex-27o: 255 BYTES from os.pathconf(PC_NAME_MAX).
  // muxplex-i1r: SESSION_NAME_MAX_LEN = 255, SESSION_NAME_RE built from it.
  // A third independent guess here is the same bug, relocated.
  assert.strictEqual(app.SESSION_NAME_MAX_BYTES, 255, 'the filesystem NAME_MAX limit, in bytes');
  assert.strictEqual(app.SESSION_NAME_MAX_LENGTH, 255, "the input's maxlength attribute");
});

test('maxLength is DERIVED from the byte cap, not a second literal', () => {
  // Two numbers that happen to match today is exactly how three repos ended up
  // with 32, 64 and 255. One home for the value, one derivation from it.
  assert.match(
    SOURCE,
    /const SESSION_NAME_MAX_LENGTH = SESSION_NAME_MAX_BYTES;/,
    'SESSION_NAME_MAX_LENGTH must be assigned FROM SESSION_NAME_MAX_BYTES',
  );
  const literals = SOURCE.split(/const SESSION_NAME_MAX_BYTES = 255;/).length - 1;
  assert.strictEqual(literals, 1, 'the number 255 is written down exactly once');
});

test('the factory sets maxLength from the constant, never a literal', () => {
  const start = SOURCE.indexOf('function _createSessionInput(');
  assert.ok(start !== -1, '_createSessionInput must exist');
  const body = SOURCE.substring(start, SOURCE.indexOf('\n}', start));
  assert.ok(
    !/maxLength\s*=\s*\d/.test(body),
    '_createSessionInput must not hardcode a maxLength literal',
  );
  assert.ok(
    body.includes('SESSION_NAME_MAX_LENGTH'),
    '_createSessionInput must source its cap from the named constant',
  );
});

test('an input built by the factory actually carries the cap', () => {
  // The seam being *set* is the whole point of this lane -- before it,
  // SESSION_NAME_MAX_LENGTH was null and this assignment never ran.
  const input = app._createSessionInput();
  assert.strictEqual(input.maxLength, 255, 'the field must refuse the 256th character itself');
});

// ─── Group B: bytes vs characters -- the gap `maxlength` cannot close ───────

test('_sessionNameByteLength agrees with TextEncoder, character for character', () => {
  // The hand-rolled counter exists to avoid allocating a Uint8Array on every
  // keystroke. It is only worth having if it is exactly right.
  const encoder = new TextEncoder();
  const cases = [
    '',
    'a',
    'plain-ascii-session-name',
    '\u00e9',              // U+00E9 e-acute -- 2 bytes
    '\u4e2d\u6587',        // CJK -- 3 bytes each
    '\ud83d\ude00',        // emoji, a surrogate PAIR -- 4 bytes
    'mixed-\u4e2d-\ud83d\ude00-end',
    'a'.repeat(255),
    '\u4e2d'.repeat(85),   // exactly 255 bytes
  ];
  for (const s of cases) {
    assert.strictEqual(
      app._sessionNameByteLength(s),
      encoder.encode(s).length,
      `byte length of ${JSON.stringify(s.slice(0, 24))} must match TextEncoder`,
    );
  }
});

test('_sessionNameByteLength treats null/undefined as empty, not as "null"', () => {
  assert.strictEqual(app._sessionNameByteLength(null), 0);
  assert.strictEqual(app._sessionNameByteLength(undefined), 0);
});

test('255 ASCII characters is exactly 255 bytes -- the equivalence the cap rests on', () => {
  // tmux-kit's charset is [A-Za-z0-9_.-], every member of which is one UTF-8
  // byte. That is the ONLY reason a character-counting `maxlength` is a
  // correct byte cap. Pin it here too, as muxplex-i1r pinned it there.
  const name = 'a'.repeat(app.SESSION_NAME_MAX_BYTES);
  assert.strictEqual(name.length, 255);
  assert.strictEqual(app._sessionNameByteLength(name), 255);
});

test('a 255-CHARACTER non-ASCII name is over the BYTE limit, and is shown as such', () => {
  // This is the gap. maxlength=255 lets all 255 of these characters be typed;
  // the filesystem measures 765 bytes and refuses. The counter reports the
  // byte cost, so the user sees the overflow instead of discovering it in a
  // 400 they cannot decode.
  const name = '\u4e2d'.repeat(255);
  assert.strictEqual(name.length, 255, 'maxlength=255 would happily allow this');
  assert.strictEqual(app._sessionNameByteLength(name), 765, 'but it is 765 bytes');
  const text = app._sessionNameLengthHintText(name);
  assert.match(text, /765/, 'the hint must show the real byte cost');
  assert.match(text, /over the limit/, 'and say plainly that it is over');
  assert.match(text, /255 characters/, 'while still naming the character count, so both are visible');
});

test('the 85-CJK-character boundary measured on this filesystem is reproduced exactly', () => {
  // mkdir proved it live: 85 CJK chars (255 bytes) OK, 86 (258 bytes) FAIL.
  assert.strictEqual(app._sessionNameByteLength('\u4e2d'.repeat(85)), 255);
  assert.strictEqual(app._sessionNameByteLength('\u4e2d'.repeat(86)), 258);
  assert.match(app._sessionNameLengthHintText('\u4e2d'.repeat(85)), /at the limit/);
  assert.match(app._sessionNameLengthHintText('\u4e2d'.repeat(86)), /over the limit/);
});

// ─── Group C: the user is told BEFORE the cap bites ────────────────────────

test('the hint says nothing at ordinary name lengths', () => {
  // A counter on every session name would itself be a surprise. Silence is
  // correct for the 99% case.
  for (const name of ['', 'work', 'muxplex-fixes-team-ci', 'a'.repeat(120)]) {
    assert.strictEqual(
      app._sessionNameLengthHintText(name), '',
      `no hint for a ${name.length}-character name`,
    );
  }
});

test('the hint appears with runway left, not at the moment keystrokes vanish', () => {
  // The failure being removed is a field that silently stops accepting input.
  // The counter has to arrive BEFORE that, or it has told the user nothing.
  const approaching = 'a'.repeat(app.SESSION_NAME_MAX_BYTES - 10);
  const text = app._sessionNameLengthHintText(approaching);
  assert.notStrictEqual(text, '', 'a name 10 short of the cap must be counted');
  assert.match(text, /245\/255/, 'and must name both the count and the limit');
  assert.ok(!/over the limit/.test(text), 'it is not over yet, so do not say so');
});

test('at exactly the cap the hint says so, because that is when input stops', () => {
  const atCap = 'a'.repeat(app.SESSION_NAME_MAX_BYTES);
  assert.match(app._sessionNameLengthHintText(atCap), /255\/255/);
  assert.match(app._sessionNameLengthHintText(atCap), /at the limit/);
});

test('the hint mounts next to the input and tracks what is typed', () => {
  const { input, parent } = mountedInput();
  assert.strictEqual(hintOf(parent), null, 'nothing rendered before there is anything to say');

  setValue(input, 'a'.repeat(250));
  const hint = hintOf(parent);
  assert.ok(hint, 'the counter must be mounted once the name approaches the cap');
  assert.match(String(hint.textContent), /250\/255/);

  setValue(input, 'a'.repeat(255));
  assert.match(String(hintOf(parent).textContent), /at the limit/);

  setValue(input, 'short-again');
  assert.strictEqual(hintOf(parent), null, 'and taken back down when it is no longer relevant');
});

test('going over the byte limit flags the hint, and the class has a CSS rule', () => {
  const { input, parent } = mountedInput();
  setValue(input, '\u4e2d'.repeat(100));  // 300 bytes
  const hint = hintOf(parent);
  assert.ok(hint, 'an over-limit name must be counted');
  assert.ok(
    hint.classList.contains('new-session-length-hint--over'),
    'over-limit must be visually distinct, not just differently worded',
  );
  // The "toggled class has no CSS rule" bug class -- test_css_class_definitions
  // guards this globally; assert it here too so the failure names this feature.
  assert.ok(STYLE.includes('.new-session-length-hint'), 'the hint needs a stylesheet rule');
  assert.ok(STYLE.includes('.new-session-length-hint--over'), 'so does its over-limit state');
});

test('the hint takes itself down on Enter, Escape and blur', () => {
  // It is a sibling neither showNewSessionInput's nor showFabSessionInput's
  // cleanup() knows about, so it must clean up on the same events they do --
  // otherwise it is left orphaned in the header after the input is gone.
  for (const ev of [{ key: 'Enter' }, { key: 'Escape' }]) {
    const { input, parent } = mountedInput();
    setValue(input, 'a'.repeat(250));
    assert.ok(hintOf(parent), 'mounted');
    input.dispatch('keydown', ev);
    assert.strictEqual(hintOf(parent), null, `hint must be removed on ${ev.key}`);
  }
});

test('the hint is removed after blur settles', async () => {
  const { input, parent } = mountedInput();
  setValue(input, 'a'.repeat(250));
  assert.ok(hintOf(parent), 'mounted');
  input.dispatch('blur', { type: 'blur' });
  await new Promise((r) => setTimeout(r, 250));
  assert.strictEqual(hintOf(parent), null, 'hint must not outlive the input it annotates');
});

// ─── Group D: the safety net -- a silent rename must stay impossible ────────

test('createNewSession tells the user when the created name is not the one asked for', () => {
  // muxplex-n8q made POST /api/sessions return the name tmux ACTUALLY created.
  // With the field capped this should be unreachable -- but "unreachable" is
  // what everyone believed about the 32-char truncation too, so the client
  // still says it out loud rather than quietly adopting a different name.
  const start = SOURCE.indexOf('async function createNewSession(');
  assert.ok(start !== -1, 'createNewSession must exist');
  const body = SOURCE.substring(start, SOURCE.indexOf('\n}', start));
  assert.ok(
    body.includes('const sessionName = data.name || name;'),
    'the observed name from the server is still what everything downstream keys on',
  );
  assert.ok(
    /sessionName === name/.test(body),
    'createNewSession must compare the observed name against the requested one',
  );
  assert.ok(
    /was changed to fit/.test(body),
    'and must say so in words the user can act on, naming the name they typed',
  );
  assert.ok(
    /Creating session/.test(body),
    'the ordinary path must keep its existing toast unchanged',
  );
});

test('the rename notice replaces the create toast rather than racing it', () => {
  // Two showToast calls in the same tick means the second silently overwrites
  // the first -- the exact hazard the comment above the auto-add call already
  // warns about. One call site, branching on the message.
  //
  // Standing guard, not proof of this lane's fix: this one also passed before
  // the change (there was one toast then too). It exists so the OBVIOUS way to
  // add a rename notice -- a second showToast next to the first -- turns red.
  const start = SOURCE.indexOf('async function createNewSession(');
  const body = SOURCE.substring(start, SOURCE.indexOf('\n}', start));
  const head = body.substring(0, body.indexOf('Auto-add to active user view'));
  const toastCalls = head.split('showToast(').length - 1;
  assert.strictEqual(
    toastCalls, 1,
    'exactly one toast may fire between the POST and the auto-add, or they overwrite each other',
  );
});

// ─── Group E: exports ──────────────────────────────────────────────────────

test('app.js exports the length-cap helpers so both flows share one implementation', () => {
  for (const name of ['_sessionNameByteLength', '_sessionNameLengthHintText', '_attachSessionNameLengthHint']) {
    assert.strictEqual(
      typeof app[name], 'function',
      `${name} must be exported`,
    );
  }
  assert.strictEqual(typeof app.SESSION_NAME_MAX_BYTES, 'number');
  assert.strictEqual(typeof app.SESSION_NAME_MAX_LENGTH, 'number');
});

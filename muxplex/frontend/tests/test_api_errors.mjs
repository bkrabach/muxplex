// Tests for server-error PRESENTATION: api()'s derivation of a human-readable
// message from a failed response body, and createNewSession's use of it.
//
// The bug these lock down: api() built `new Error("HTTP 400: Bad Request")` and
// attached the parsed body as err.body, so every caller that did
// showToast(err.message) threw away the server's actual explanation. The server
// has always sent one -- _require_valid_session_name (main.py) sends the
// allowed-characters sentence, and the rename endpoint (main.py) sends a
// STRUCTURED detail carrying both an explanation and a `suggested` corrected
// name that no user had ever seen.
//
// Two shapes matter and both must work without ever rendering "[object Object]"
// at a user:
//   {"detail": "a sentence"}                                  <- plain
//   {"detail": {"detail": "...", "suggested": "build_js"}}    <- rename
// plus FastAPI's 422 validation array, and flat bodies that carry `detail`
// alongside sibling flags (the invalid_view_rule / backstop JSONResponse shape).
//
// The derivation is asserted through api() itself -- not a private helper --
// because "every caller benefits" is the actual requirement.

// --- localStorage stub -- must be set before importing app.js. ---
let _localStorageStore = {};
globalThis.localStorage = {
  getItem: (key) => (Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null),
  setItem: (key, value) => { _localStorageStore[key] = String(value); },
  removeItem: (key) => { delete _localStorageStore[key]; },
};

// --- DOM stub: a stateful #toast so the surfaced message can be read back,
// plus a catch-all null for everything else (test_firstrun.mjs pattern). ---
function makeClassList(initial) {
  const classes = new Set(initial || []);
  return {
    add(...cs) { for (const c of cs) classes.add(c); },
    remove(...cs) { for (const c of cs) classes.delete(c); },
    toggle(c, force) {
      if (force === undefined) {
        if (classes.has(c)) { classes.delete(c); return false; }
        classes.add(c); return true;
      }
      if (force) classes.add(c); else classes.delete(c);
      return !!force;
    },
    contains(c) { return classes.has(c); },
  };
}

function makeStubElement(initialClasses) {
  return {
    style: {},
    value: '',
    disabled: false,
    textContent: '',
    classList: makeClassList(initialClasses),
    setAttribute() {},
    getAttribute() { return null; },
    addEventListener() {},
    appendChild() {},
    remove() {},
  };
}

let elements = {};
function resetDom() {
  elements = { toast: makeStubElement(['hidden']) };
}
resetDom();

globalThis.document = {
  title: '',
  getElementById: (id) => (Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null),
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => makeStubElement([]),
  addEventListener: () => {},
  removeEventListener: () => {},
};

// window.confirm is the one-click-correction prompt. Each test sets
// _confirmAnswer and reads _confirmPrompts back.
let _confirmAnswer = false;
let _confirmPrompts = [];
globalThis.window = {
  addEventListener: () => {},
  location: { href: '', hostname: 'testhost' },
  innerWidth: 1024,
  confirm: (msg) => { _confirmPrompts.push(String(msg)); return _confirmAnswer; },
};

globalThis.Notification = { permission: 'default', requestPermission: async () => 'default' };

Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'test-agent' },
  writable: true,
  configurable: true,
});

import { createRequire } from 'node:module';
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const require = createRequire(import.meta.url);
const app = require(join(__dirname, '..', 'app.js'));
const appSource = fs.readFileSync(join(__dirname, '..', 'app.js'), 'utf-8');

/** Install a fetch stub that fails every request with one canned response. */
function failWith(status, statusText, jsonBody) {
  globalThis.fetch = async () => ({
    ok: false,
    status,
    statusText,
    json: async () => {
      if (jsonBody === undefined) throw new SyntaxError('Unexpected end of JSON input');
      return jsonBody;
    },
  });
}

/** Call api() and return the Error it threw (failing the test if it resolved). */
async function apiError(method, path, body) {
  try {
    await app.api(method || 'POST', path || '/api/sessions', body);
  } catch (err) {
    return err;
  }
  throw new assert.AssertionError({ message: 'api() should have thrown on a non-ok response' });
}

const ALLOWED_CHARS_DETAIL =
  'Invalid session name. Allowed characters: letters, digits, and _ . - (1-255 characters).';

let _origFetch;
beforeEach(() => {
  resetDom();
  _confirmAnswer = false;
  _confirmPrompts = [];
  _origFetch = globalThis.fetch;
});

// ---------------------------------------------------------------------------
// api() -- the derivation itself
// ---------------------------------------------------------------------------

test('api() prefers a string detail over the generic HTTP status line', async () => {
  failWith(400, 'Bad Request', { detail: ALLOWED_CHARS_DETAIL });
  const err = await apiError();
  globalThis.fetch = _origFetch;

  assert.strictEqual(err.message, ALLOWED_CHARS_DETAIL);
  assert.doesNotMatch(
    err.message,
    /HTTP 400/,
    'the opaque status line must not be what the user is shown when the server explained itself',
  );
});

test('api() reads the sentence out of a STRUCTURED detail and exposes `suggested`', async () => {
  // The rename endpoint's real shape (main.py): detail is a dict, and the
  // human sentence lives one level in, alongside the corrected name.
  failWith(400, 'Bad Request', {
    detail: {
      detail: "'build.js' is not a stable tmux session name. tmux 3.4 silently converts '.' to '_'.",
      invalid_session_name: true,
      suggested: 'build_js',
    },
  });
  const err = await apiError();
  globalThis.fetch = _origFetch;

  assert.match(err.message, /not a stable tmux session name/);
  assert.doesNotMatch(err.message, /\[object Object\]/, 'a dict detail must never be stringified at a user');
  assert.strictEqual(err.suggested, 'build_js', 'the server-computed correction must reach callers');
});

test('api() never renders [object Object] for a structured detail with no sentence', async () => {
  // A structured detail carrying only flags -- there is no sentence to show,
  // so the generic status line is the correct fallback, NOT "[object Object]".
  failWith(409, 'Conflict', { detail: { some_flag: true } });
  const err = await apiError();
  globalThis.fetch = _origFetch;

  assert.doesNotMatch(err.message, /\[object Object\]/);
  assert.match(err.message, /HTTP 409/, 'with nothing human-readable in the body, fall back to the status line');
});

test("api() flattens FastAPI's 422 validation-error array into readable text", async () => {
  failWith(422, 'Unprocessable Entity', {
    detail: [
      { loc: ['body', 'name'], msg: 'field required', type: 'value_error.missing' },
    ],
  });
  const err = await apiError();
  globalThis.fetch = _origFetch;

  assert.match(err.message, /field required/);
  assert.doesNotMatch(err.message, /\[object Object\]/);
});

test('api() handles a flat body that carries `detail` alongside sibling flags', async () => {
  // The invalid_view_rule / backstop JSONResponse shape (main.py): `detail` is
  // a top-level string next to its flags, not nested.
  failWith(400, 'Bad Request', {
    detail: 'Invalid view rule',
    invalid_view_rule: true,
    errors: ['bad glob'],
  });
  const err = await apiError();
  globalThis.fetch = _origFetch;

  assert.strictEqual(err.message, 'Invalid view rule');
});

test('api() falls back to the generic status line when the body carries nothing useful', async () => {
  failWith(418, "I'm a teapot", {});
  const err = await apiError();
  globalThis.fetch = _origFetch;

  assert.strictEqual(err.message, "HTTP 418: I'm a teapot");
});

test('api() falls back to the generic status line when the body is not JSON at all', async () => {
  failWith(502, 'Bad Gateway', undefined); // json() throws
  const err = await apiError();
  globalThis.fetch = _origFetch;

  assert.strictEqual(err.message, 'HTTP 502: Bad Gateway');
  assert.strictEqual(err.body, undefined);
});

test('api() still exposes status and body for callers that branch on them', async () => {
  // Existing callers distinguish a CAS conflict from a backstop rejection by
  // reading err.status / err.body -- deriving a message must not disturb that.
  failWith(409, 'Conflict', { detail: 'stale baseline', backstop: true, settings_updated_at: 12 });
  const err = await apiError();
  globalThis.fetch = _origFetch;

  assert.strictEqual(err.status, 409);
  assert.strictEqual(err.body.backstop, true);
  assert.strictEqual(err.body.settings_updated_at, 12);
  assert.strictEqual(err.message, 'stale baseline');
});

// ---------------------------------------------------------------------------
// createNewSession -- what the user actually sees
// ---------------------------------------------------------------------------

test('createNewSession surfaces the server explanation, not "HTTP 400: Bad Request"', async () => {
  failWith(400, 'Bad Request', { detail: ALLOWED_CHARS_DETAIL });
  await app.createNewSession('my session', '', '');
  globalThis.fetch = _origFetch;

  assert.match(elements.toast.textContent, /Allowed characters/);
  assert.doesNotMatch(elements.toast.textContent, /HTTP 400/);
});

test('createNewSession offers the suggested name and retries with it on confirm', async () => {
  const attempted = [];
  globalThis.fetch = async (_path, opts) => {
    const sent = JSON.parse(opts.body);
    attempted.push(sent.name);
    if (sent.name === 'build.js') {
      return {
        ok: false,
        status: 400,
        statusText: 'Bad Request',
        json: async () => ({
          detail: {
            detail: "'build.js' is not a stable tmux session name.",
            invalid_session_name: true,
            suggested: 'build_js',
          },
        }),
      };
    }
    // Second attempt: fail plainly, so the test never reaches the poll loop.
    return {
      ok: false,
      status: 409,
      statusText: 'Conflict',
      json: async () => ({ detail: 'Session already exists' }),
    };
  };
  _confirmAnswer = true;

  await app.createNewSession('build.js', '', '');
  globalThis.fetch = _origFetch;

  assert.deepStrictEqual(attempted, ['build.js', 'build_js'], 'confirming must retry with the server-suggested name');
  assert.strictEqual(_confirmPrompts.length, 1, 'the user must be asked exactly once');
  assert.match(_confirmPrompts[0], /build_js/, 'the prompt must name the suggested correction');
  assert.match(_confirmPrompts[0], /not a stable tmux session name/, 'the prompt must also explain the rejection');
});

test('createNewSession still shows the suggestion when the user declines the retry', async () => {
  const attempted = [];
  globalThis.fetch = async (_path, opts) => {
    attempted.push(JSON.parse(opts.body).name);
    return {
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: async () => ({
        detail: { detail: 'Not a stable tmux session name.', suggested: 'build_js' },
      }),
    };
  };
  _confirmAnswer = false;

  await app.createNewSession('build.js', '', '');
  globalThis.fetch = _origFetch;

  assert.deepStrictEqual(attempted, ['build.js'], 'declining must not create anything');
  assert.match(elements.toast.textContent, /build_js/, 'a declined suggestion is still worth showing');
});

test('createNewSession does not offer a suggestion identical to the name just submitted', async () => {
  // A server that echoes the submitted name back as `suggested` would otherwise
  // prompt-and-retry forever.
  const attempted = [];
  globalThis.fetch = async (_path, opts) => {
    attempted.push(JSON.parse(opts.body).name);
    return {
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: async () => ({ detail: { detail: 'Rejected.', suggested: 'build_js' } }),
    };
  };
  _confirmAnswer = true;

  await app.createNewSession('build_js', '', '');
  globalThis.fetch = _origFetch;

  assert.deepStrictEqual(attempted, ['build_js'], 'a no-op suggestion must not trigger a retry');
  assert.strictEqual(_confirmPrompts.length, 0, 'a no-op suggestion must not prompt');
});

// ---------------------------------------------------------------------------
// Centralization -- the derivation lives in ONE place
// ---------------------------------------------------------------------------

test('the message derivation lives in api(), not hand-rolled in the createNewSession catch', () => {
  const apiStart = appSource.indexOf('async function api(');
  assert.notStrictEqual(apiStart, -1, 'api() must exist');
  const apiBody = appSource.slice(apiStart, apiStart + 1800);
  assert.ok(
    /err\.message\s*=/.test(apiBody),
    'api() must derive err.message from the response body so every caller benefits',
  );

  const createStart = appSource.indexOf('async function createNewSession(');
  assert.notStrictEqual(createStart, -1, 'createNewSession() must exist');
  // Bound the slice by the NEXT top-level function declaration rather than a
  // fixed character offset. A magic window silently stops covering the function
  // the moment a sibling change grows it -- which is exactly what happened when
  // muxplex-9zp's poll rewrite pushed the catch past a hardcoded 5200.
  const afterCreate = appSource.slice(createStart + 'async function createNewSession('.length);
  const nextFn = afterCreate.search(/\n(?:async )?function [A-Za-z_]/);
  const createBody = appSource.slice(
    createStart,
    nextFn === -1 ? appSource.length : createStart + 'async function createNewSession('.length + nextFn,
  );
  const catchIdx = createBody.lastIndexOf('} catch (err) {');
  assert.notStrictEqual(catchIdx, -1, 'createNewSession must still have its catch');
  const catchBody = createBody.slice(catchIdx);
  assert.ok(
    !/err\.body/.test(catchBody),
    'the catch must not re-parse the response body itself -- api() already derived the text',
  );
});

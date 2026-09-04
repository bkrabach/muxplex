// Tests for the session-RENAME call path: the UI that reaches
// POST /api/sessions/{name}/rename, and what the user sees when it refuses.
//
// The gap these lock down: the rename endpoint has existed and been tested
// server-side for a while -- it validates, and on a bad name returns a
// STRUCTURED 400 carrying both an explanation and a `suggested` corrected name
// (main.py). But NOTHING in the frontend ever called it. Grep across
// frontend/*.js and *.html found no caller at all; the only "rename" surfaces
// were device rename (_patchDeviceDisplayName) and view rename, both different
// endpoints. So a whole tested server capability -- including the correction
// the server computes for you -- was unreachable to a user.
//
// Three things are asserted, in the order a user meets them:
//
//   1. The session tile's flyout menu OFFERS rename at all (data-action=
//      "rename"), for every view type, and does NOT offer it as an actionable
//      item for a REMOTE session -- there is no federation rename route
//      (main.py has POST/DELETE/GET .../federation/{id}/sessions..., but no
//      rename), so offering it there would 404 at the user.
//   2. renameSession() actually POSTs the documented body to the documented
//      path, and reports the name tmux ACTUALLY ended up with (the response's
//      `name`, the server's OBSERVED name) rather than the one that was asked
//      for -- those two differing is the whole umbrella bug this batch exists
//      to fix.
//   3. A rejected rename shows the server's own sentence and offers
//      `err.suggested` as a one-click correction -- the same shape
//      createNewSession already uses (muxplex-ctx), reused rather than
//      re-invented.

// --- localStorage stub -- must be set before importing app.js. ---
let _localStorageStore = {};
globalThis.localStorage = {
  getItem: (key) => (Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null),
  setItem: (key, value) => { _localStorageStore[key] = String(value); },
  removeItem: (key) => { delete _localStorageStore[key]; },
};

// --- DOM stub: a stateful #toast so the surfaced message can be read back,
// plus a catch-all null for everything else (test_api_errors.mjs pattern). ---
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

let _origFetch;
beforeEach(() => {
  resetDom();
  _confirmAnswer = false;
  _confirmPrompts = [];
  _origFetch = globalThis.fetch;
});

/**
 * Record every rename request and reply with one canned response.
 * Anything that is NOT a rename POST (e.g. the pollSessions() refresh that
 * follows a success) gets a benign empty-list 200 so the test never depends
 * on the poll loop.
 */
function stubRename(reply) {
  const calls = [];
  globalThis.fetch = async (path, opts) => {
    if (typeof path === 'string' && path.indexOf('/rename') !== -1) {
      const sent = opts && opts.body ? JSON.parse(opts.body) : {};
      calls.push({ path, method: opts && opts.method, new_name: sent.new_name });
      return reply(sent.new_name, calls.length);
    }
    return { ok: true, status: 200, statusText: 'OK', json: async () => [] };
  };
  return calls;
}

function ok(body) {
  return { ok: true, status: 200, statusText: 'OK', json: async () => body };
}

function rejectWith(detail, status) {
  return {
    ok: false,
    status: status || 400,
    statusText: 'Bad Request',
    json: async () => ({ detail }),
  };
}

// ---------------------------------------------------------------------------
// 1. The menu offers rename at all
// ---------------------------------------------------------------------------

test('the session flyout menu offers a rename action in every view type', () => {
  const map = app.FLYOUT_MENU_MAP;
  assert.ok(map, 'FLYOUT_MENU_MAP must be exported so the menu can be asserted on');

  for (const viewType of ['all', 'user', 'hidden']) {
    const items = map[viewType] || [];
    const rename = items.find((i) => i.action === 'rename');
    assert.ok(
      rename,
      `the '${viewType}' flyout must offer rename -- otherwise the endpoint stays unreachable in that view`,
    );
    assert.match(rename.label, /rename/i, 'the item must be recognisable as rename');
  }
});

test('_buildFlyoutMenuItems emits a clickable rename item for a LOCAL session', () => {
  const html = app._buildFlyoutMenuItems('');
  assert.match(html, /data-action="rename"/, 'a local session must get a rename menu item');
  assert.doesNotMatch(
    html.slice(html.indexOf('data-action="rename"') - 200, html.indexOf('data-action="rename"') + 40),
    /disabled/,
    'a local session\'s rename item must not be disabled',
  );
});

test('_buildFlyoutMenuItems does NOT offer an actionable rename for a REMOTE session', () => {
  // There is no POST /api/federation/{device_id}/sessions/{name}/rename in
  // main.py -- only create, delete, get and bell/clear are proxied. An
  // enabled rename item on a remote tile would therefore 404 at the user,
  // which is exactly the class of surprise this batch is removing.
  const html = app._buildFlyoutMenuItems('some-remote-device');
  const idx = html.indexOf('data-action="rename"');
  if (idx === -1) return; // omitted entirely is also an acceptable answer
  const item = html.slice(html.lastIndexOf('<button', idx), html.indexOf('</button>', idx));
  assert.match(item, /disabled/, 'a remote session\'s rename item must be disabled, not silently broken');
});

// ---------------------------------------------------------------------------
// 2. renameSession actually reaches the endpoint
// ---------------------------------------------------------------------------

test('renameSession POSTs new_name to /api/sessions/{name}/rename', async () => {
  const calls = stubRename(() => ok({ ok: true, from: 'old-name', name: 'new-name', migrated: {} }));

  await app.renameSession('old-name', 'new-name');
  globalThis.fetch = _origFetch;

  assert.strictEqual(calls.length, 1, 'exactly one rename request');
  assert.strictEqual(calls[0].method, 'POST');
  assert.strictEqual(calls[0].path, '/api/sessions/old-name/rename');
  assert.strictEqual(calls[0].new_name, 'new-name', 'the body key the server reads is new_name');
});

test('renameSession percent-encodes the session name in the path', async () => {
  const calls = stubRename(() => ok({ ok: true, from: 'a b', name: 'c', migrated: {} }));

  await app.renameSession('has space', 'clean-name');
  globalThis.fetch = _origFetch;

  assert.strictEqual(calls[0].path, '/api/sessions/has%20space/rename');
});

test('renameSession reports the OBSERVED name the server returned, not the one asked for', async () => {
  // The endpoint re-enumerates tmux and returns the name that actually exists
  // (`name`), which can differ from the requested one. Reporting the request
  // back at the user is precisely the see-one-thing-get-another divergence
  // this batch exists to remove.
  stubRename(() => ok({ ok: true, from: 'old', name: 'what-tmux-actually-made', migrated: {} }));

  await app.renameSession('old', 'what-i-asked-for');
  globalThis.fetch = _origFetch;

  assert.match(elements.toast.textContent, /what-tmux-actually-made/);
  assert.doesNotMatch(
    elements.toast.textContent,
    /what-i-asked-for/,
    'the requested name must not be reported as fact when the server observed a different one',
  );
});

// ---------------------------------------------------------------------------
// 3. Rejection: the server's sentence, and its one-click correction
// ---------------------------------------------------------------------------

test('a rejected rename shows the server explanation, not "HTTP 400: Bad Request"', async () => {
  stubRename(() => rejectWith({
    detail: "'build.js' is not a stable tmux session name. tmux 3.4 silently converts '.' to '_'.",
    invalid_session_name: true,
  }));

  await app.renameSession('old', 'build.js');
  globalThis.fetch = _origFetch;

  assert.match(elements.toast.textContent, /not a stable tmux session name/);
  assert.doesNotMatch(elements.toast.textContent, /HTTP 400/);
  assert.doesNotMatch(elements.toast.textContent, /\[object Object\]/);
});

test('a rejected rename offers the server-suggested name and retries with it on confirm', async () => {
  const calls = stubRename((newName) => {
    if (newName === 'build.js') {
      return rejectWith({
        detail: "'build.js' is not a stable tmux session name.",
        invalid_session_name: true,
        suggested: 'build_js',
      });
    }
    return ok({ ok: true, from: 'old', name: newName, migrated: {} });
  });
  _confirmAnswer = true;

  await app.renameSession('old', 'build.js');
  globalThis.fetch = _origFetch;

  assert.deepStrictEqual(
    calls.map((c) => c.new_name),
    ['build.js', 'build_js'],
    'confirming must retry with the server-suggested name',
  );
  assert.strictEqual(_confirmPrompts.length, 1, 'the user must be asked exactly once');
  assert.match(_confirmPrompts[0], /build_js/, 'the prompt must name the suggested correction');
  assert.match(_confirmPrompts[0], /not a stable tmux session name/, 'the prompt must also explain the rejection');
});

test('a declined suggestion is still shown in the toast', async () => {
  const calls = stubRename(() => rejectWith({
    detail: 'Not a stable tmux session name.',
    suggested: 'build_js',
  }));
  _confirmAnswer = false;

  await app.renameSession('old', 'build.js');
  globalThis.fetch = _origFetch;

  assert.deepStrictEqual(calls.map((c) => c.new_name), ['build.js'], 'declining must not rename anything');
  assert.match(elements.toast.textContent, /build_js/, 'a declined suggestion is still worth showing');
});

test('a suggestion identical to the name just submitted does not prompt or retry', async () => {
  // A server echoing the submitted name back as `suggested` would otherwise
  // prompt-and-retry forever -- the same loop guard createNewSession carries.
  const calls = stubRename(() => rejectWith({ detail: 'Rejected.', suggested: 'build_js' }));
  _confirmAnswer = true;

  await app.renameSession('old', 'build_js');
  globalThis.fetch = _origFetch;

  assert.deepStrictEqual(calls.map((c) => c.new_name), ['build_js'], 'a no-op suggestion must not trigger a retry');
  assert.strictEqual(_confirmPrompts.length, 0, 'a no-op suggestion must not prompt');
});

test('renaming to the name it already has does not hit the network at all', async () => {
  const calls = stubRename(() => ok({ ok: true, from: 'same', name: 'same', migrated: {} }));

  await app.renameSession('same', 'same');
  globalThis.fetch = _origFetch;

  assert.strictEqual(calls.length, 0, 'a no-op rename is not worth a round trip');
});

// ---------------------------------------------------------------------------
// Reuse -- create and rename must normalize identically
// ---------------------------------------------------------------------------

test('the rename field is built by the SAME factory as the new-session field', () => {
  // _createSessionInput is where muxplex-pfp attached live normalization. A
  // rename field that built its own <input> would normalize differently from
  // the create field -- two rules for one concept, which is its own surprise.
  const start = appSource.indexOf('function _doRenameSessionInline(');
  assert.notStrictEqual(start, -1, '_doRenameSessionInline() must exist');
  const body = appSource.slice(start, start + 3000);
  assert.match(
    body,
    /_createSessionInput\(/,
    'the rename field must come from the shared input factory, not a hand-rolled input',
  );
});

test('the rename submit path runs the same full normalization as create', () => {
  const start = appSource.indexOf('function _doRenameSessionInline(');
  const body = appSource.slice(start, start + 3000);
  assert.match(
    body,
    /_normalizeSessionName\(/,
    'submit must apply the collapse/strip tidying, exactly as the create flows do',
  );
});

test('renameSession does not re-parse the error body -- api() already derived it', () => {
  const start = appSource.indexOf('async function renameSession(');
  assert.notStrictEqual(start, -1, 'renameSession() must exist');
  const body = appSource.slice(start, start + 3000);
  const catchIdx = body.lastIndexOf('} catch (err) {');
  assert.notStrictEqual(catchIdx, -1, 'renameSession must have a catch');
  assert.ok(
    !/err\.body/.test(body.slice(catchIdx)),
    'the catch must use err.message / err.suggested, not hand-roll a second extraction',
  );
});

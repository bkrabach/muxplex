// Tests for the agent chat panel (chat.js).
//
// chat.js is a closed strict-mode IIFE that exports exactly ONE global:
// window.muxplexAgentPrefs. Everything else -- the tool-call executor, the
// 403 fence classifier, the confirmation gate, the composer key handling --
// is private. The only way to exercise it is to load the real source into a
// stubbed DOM/global environment (via node:vm) and drive it through real DOM
// events and a stubbed fetch, exactly as a browser would. No mocks of the
// code under test itself; only the environment around it is stubbed.
//
// Covers:
//   GROUP 1 (muxplex-9n9)  the 403 fence classifier -- BOTH branches, plus an
//                          explicit consistency check between the rendered
//                          detail and the rendered prose.
//   GROUP 2 (muxplex-ixl)  model-facing guidance must never reach the DOM,
//                          but must still reach the model (the continuation
//                          request body).
//   GROUP 3 (muxplex-18f)  configurable send/newline modes, both the
//                          localStorage-backed preference AND the real
//                          keydown behavior, plus hint/behavior agreement.
//   GROUP 4 (muxplex-2qs)  init() must not require a #chat-close-btn.
//
// Harness design note: unlike tests/test_compose.mjs's flat id->element
// registry (sufficient for app.js's compose bar), chat.js dynamically
// creates elements, assigns them an id (e.g. the empty-state placeholder),
// and later re-finds them via document.getElementById. A flat registry
// snapshot cannot see those. So this file's stub DOM implements a real
// id->element registry backed by a `.id` accessor on every stub node
// (set on assignment, exactly like a live DOM), and getElementById reads
// live from that registry -- the same "follow the DOM, not a snapshot"
// property the real thing has.

import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CHAT_JS_PATH = path.join(__dirname, '..', 'chat.js');
const chatJsSource = fs.readFileSync(CHAT_JS_PATH, 'utf-8');

// ---------------------------------------------------------------------
// Minimal stub DOM (shared shape with test_compose.mjs's makeClassList).
// ---------------------------------------------------------------------

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
    _set: classes,
  };
}

/** A fresh DOM environment: its own id registry, its own StubNode class
 * closed over that registry (so `.id = "x"` really registers the node,
 * and getElementById really finds it -- including elements chat.js creates
 * and ids itself after load, e.g. #chat-empty-state). */
function createDomEnvironment() {
  const idRegistry = new Map();

  class StubNode {
    constructor(tag) {
      this.tagName = String(tag || 'div').toUpperCase();
      this._children = [];
      this._text = '';
      this._attrs = {};
      this._listeners = {};
      this.style = {};
      this.classList = makeClassList([]);
      this.parentNode = null;
      // Form-control-ish properties every stub node carries, harmlessly
      // unused by non-input elements.
      this.value = '';
      this.selectionStart = 0;
      this.selectionEnd = 0;
      this.scrollTop = 0;
      this.scrollHeight = 20;
      this.disabled = false;
      this._focused = false;
      this._id = '';
    }
    get id() { return this._id; }
    set id(v) {
      if (this._id && idRegistry.get(this._id) === this) idRegistry.delete(this._id);
      this._id = String(v == null ? '' : v);
      if (this._id) idRegistry.set(this._id, this);
    }
    get textContent() {
      // Mirrors real DOM: reading textContent recurses through children
      // (which is what makes a collapsed <details> still report its full
      // text -- CSS display has no bearing on textContent). A leaf that
      // only ever had textContent assigned (never appendChild'd) returns
      // its own assigned text.
      if (this._children.length === 0) return this._text;
      return this._children.map((c) => c.textContent).join('');
    }
    set textContent(v) {
      // Mirrors real DOM: assigning textContent clears any children.
      this._text = v == null ? '' : String(v);
      this._children = [];
    }
    appendChild(child) {
      this._children.push(child);
      child.parentNode = this;
      return child;
    }
    removeChild(child) {
      const i = this._children.indexOf(child);
      if (i !== -1) this._children.splice(i, 1);
      child.parentNode = null;
      return child;
    }
    setAttribute(k, v) { this._attrs[k] = String(v); }
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
    }
    removeAttribute(k) { delete this._attrs[k]; }
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k); }
    addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); }
    removeEventListener(ev, fn) {
      if (!this._listeners[ev]) return;
      this._listeners[ev] = this._listeners[ev].filter((f) => f !== fn);
    }
    _fire(ev, evObj) {
      (this._listeners[ev] || []).slice().forEach((fn) => fn(evObj || {}));
    }
    click() { this._fire('click', {}); }
    focus() { this._focused = true; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
  }

  function createTextNode(text) {
    const n = new StubNode('#text');
    n.textContent = text;
    return n;
  }

  /** A <dialog>-shaped stub: showModal()/close() plus the 'open' flag and
   * native 'close' event that resolveConfirm()'s wiring depends on. */
  function makeDialog() {
    const el = new StubNode('dialog');
    el.open = false;
    el.showModal = function () { this.open = true; };
    el.close = function () {
      this.open = false;
      this._fire('close', {});
    };
    return el;
  }

  const bodyEl = new StubNode('body');

  const documentStub = {
    readyState: 'complete', // chat.js must call init() immediately, not wait for DOMContentLoaded
    title: 'muxplex test',
    visibilityState: 'visible',
    body: bodyEl,
    getElementById: (id) => idRegistry.get(id) || null,
    createElement: (tag) => new StubNode(tag),
    createTextNode,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
  };

  const windowStub = {
    innerWidth: 1024,
    innerHeight: 768,
    addEventListener: () => {},
    removeEventListener: () => {},
    matchMedia: () => ({ matches: false, addListener() {}, removeListener() {} }),
  };

  return { StubNode, createTextNode, makeDialog, document: documentStub, window: windowStub, idRegistry };
}

/** A localStorage stub supporting per-instance throw-on-get/set, matching
 * test_compose.mjs's convention but scoped to one harness instance (each
 * test gets its own fresh store -- no cross-test bleed). */
function makeLocalStorageStub() {
  let store = {};
  let throwOnGet = false;
  let throwOnSet = false;
  const api = {
    getItem(key) {
      if (throwOnGet) throw new Error('blocked');
      return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
    },
    setItem(key, value) {
      if (throwOnSet) throw new Error('blocked');
      store[key] = String(value);
    },
    removeItem(key) { delete store[key]; },
  };
  return {
    api,
    get store() { return store; },
    setThrowOnGet(v) { throwOnGet = v; },
    setThrowOnSet(v) { throwOnSet = v; },
  };
}

// Every id chat.js's init() treats as REQUIRED -- i.e. every id it pushes
// onto its `__missing` list and then throws over. This array must stay in
// lockstep with that check: `test_init_required_ids_match_chat_js` below
// reads chat.js's source and fails if the two ever drift, so a future
// required element cannot silently become "optional" here by omission.
//
// v0.49.0 added the six gate/chrome ids: init() now hard-fails without the
// unconfigured-Agent gate, deliberately. If those elements are absent the
// panel would come up with a working-looking composer and no gate --
// exactly the "submit a turn to find out it's broken" failure the gate
// exists to prevent. The harness supplies them rather than the contract
// relaxing to tolerate their absence.
const REQUIRED_IDS = [
  'chat-panel', 'chat-messages', 'chat-input', 'chat-send-btn', 'chat-new-btn',
  'chat-open-btn', 'chat-export-btn',
  'chat-panel-header', 'chat-composer', 'chat-byline',
  'chat-gate', 'chat-gate-text', 'chat-gate-settings-btn',
  'chat-confirm-backdrop',
  'chat-confirm-session', 'chat-confirm-text', 'chat-confirm-keys',
  'chat-confirm-cancel-btn', 'chat-confirm-send-btn',
];
const OPTIONAL_IDS = ['chat-live', 'chat-key-hint', 'chat-export-link'];

/** Load a fresh copy of chat.js into its own vm context, with its own DOM,
 * localStorage, and fetch stub. Every test gets a brand-new context --
 * chat.js's module state (messages, clientSessionId, statusEl, the
 * confirmation gate...) is closed over per-load, so this is the only way
 * to get real per-test isolation. */
function loadChatPanel({ fetchImpl, includeCloseBtn = false } = {}) {
  const env = createDomEnvironment();
  const els = {};

  REQUIRED_IDS.concat(OPTIONAL_IDS).forEach((id) => {
    const el = new env.StubNode(id === 'chat-input' ? 'textarea' : 'div');
    el.id = id;
    els[id] = el;
  });
  // The confirm dialog needs showModal()/close()/open -- replace the plain
  // div created above with a real dialog-shaped stub, same id.
  els['chat-confirm-dialog'] = env.makeDialog();
  els['chat-confirm-dialog'].id = 'chat-confirm-dialog';

  if (includeCloseBtn) {
    const btn = new env.StubNode('button');
    btn.id = 'chat-close-btn';
    els['chat-close-btn'] = btn;
  }

  const storage = makeLocalStorageStub();
  const fetchCalls = [];
  const fetchFn = async (url, opts) => {
    fetchCalls.push({ url, opts });
    return fetchImpl(url, opts, fetchCalls.length);
  };

  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    window: env.window,
    document: env.document,
    localStorage: storage.api,
    fetch: fetchFn,
    navigator: { userAgent: 'test-agent' },
    location: { href: 'http://localhost/test' },
    performance: { now: () => Date.now() },
    Blob: class {
      constructor(parts, opts) { this.parts = parts; this.type = opts && opts.type; }
    },
    URL: { createObjectURL: () => 'blob:fake-url', revokeObjectURL: () => {} },
    setTimeout,
    clearTimeout,
    TextEncoder,
    TextDecoder,
  };

  const context = vm.createContext(sandbox);
  vm.runInContext(chatJsSource, context, { filename: 'chat.js' });

  return {
    els,
    document: context.document,
    // The sandboxed `window` object itself -- e.g. so a test can stub
    // window.getFocusedSessionName (muxplex-h2f) the same way app.js would
    // define it for real, without this file needing a second sandbox shape
    // just for that one function.
    window: context.window,
    storage,
    fetchCalls,
    prefs: context.window.muxplexAgentPrefs,
    // muxplex-fx1: chat.js's second exposed global (see window.muxplexAgentCredential
    // in chat.js) -- refreshStatus/bindForm/recheckGate, the same
    // "chat.js owns the implementation, app.js calls the exposed name" shape
    // muxplexAgentPrefs above already has.
    credential: context.window.muxplexAgentCredential,
  };
}

/** Register the Settings -> Agent credential form's five elements
 * (agent-credential-form/-key/-provider/-result/-submit-btn) into a loaded
 * panel's DOM -- NOT part of REQUIRED_IDS/OPTIONAL_IDS (chat.js's init()
 * does not require them; only _bindAgentCredentialForm() looks them up),
 * so tests that exercise the credential form build them explicitly here
 * rather than growing the shared fixture for a form only some tests use. */
function addAgentCredentialFormEls(panel) {
  const form = panel.document.createElement('form');
  form.id = 'agent-credential-form';
  const keyInput = panel.document.createElement('input');
  keyInput.id = 'agent-credential-key';
  const providerSelect = panel.document.createElement('select');
  providerSelect.id = 'agent-credential-provider';
  providerSelect.value = 'anthropic';
  const resultEl = panel.document.createElement('div');
  resultEl.id = 'agent-credential-result';
  const submitBtn = panel.document.createElement('button');
  submitBtn.id = 'agent-credential-submit-btn';
  return { form, keyInput, providerSelect, resultEl, submitBtn };
}

/** Fetch stub for the Settings -> Agent credential form AND the chat
 * panel's own gate (muxplex-fx1) -- both read the SAME
 * GET /api/agent/provider-credential contract (checkAgentGate() reads only
 * `state`; the form additionally reads `providers`/`sidecar`). `initialState`
 * seeds what GET returns; a successful (non-no_op) POST flips the in-memory
 * state to "configured" for every GET after that, mirroring the real
 * server persisting the key before the next status check. Submitting the
 * literal key "bad-key" simulates the provider rejecting it (400); any
 * other non-empty key simulates acceptance. Also answers GET/PATCH
 * /api/settings (agentPanelOpen) so the harness's own init-time restore
 * always has something to talk to, exactly like settingsFetch() above. */
function agentCredentialFetch(initialState) {
  var state = initialState; // "not_configured" | "configured"
  var settings = { agentPanelOpen: false };
  return async (url, opts) => {
    var method = (opts && opts.method) || 'GET';
    if (url === '/api/agent/provider-credential') {
      if (method === 'GET') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ state: state, providers: {}, sidecar: 'running' }),
        };
      }
      if (method === 'POST') {
        var body = JSON.parse(opts.body);
        if (body.api_key === 'bad-key') {
          return {
            ok: false,
            status: 400,
            json: async () => ({ detail: 'Rejected: the provider reported this key as invalid (test).' }),
          };
        }
        state = 'configured';
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            provider: body.provider,
            no_op: false,
            restarted: false,
            detail: 'Takes effect on the next turn -- no restart needed (embedded mode runs in-process).',
          }),
        };
      }
    }
    if (url === '/api/settings') {
      if (method === 'GET') return { ok: true, status: 200, text: async () => JSON.stringify(settings) };
      if (method === 'PATCH') {
        Object.assign(settings, JSON.parse(opts.body));
        return { ok: true, status: 200, text: async () => JSON.stringify(settings) };
      }
    }
    throw new Error('unexpected fetch url/method in test: ' + method + ' ' + url);
  };
}

function fullText(el) {
  return el.textContent;
}

async function waitUntil(fn, { timeout = 2000, interval = 5, label = 'condition' } = {}) {
  const start = Date.now();
  for (;;) {
    if (fn()) return;
    if (Date.now() - start > timeout) {
      throw new Error(`waitUntil: timed out after ${timeout}ms waiting for: ${label}`);
    }
    await new Promise((r) => setTimeout(r, interval));
  }
}

// ---------------------------------------------------------------------
// SSE response builders (fetch stub helpers for /api/agent/chat/completions)
// ---------------------------------------------------------------------

function sseChunksResponse(chunkObjs) {
  const raw = chunkObjs.map((c) => `data: ${JSON.stringify(c)}\n\n`).join('') + 'data: [DONE]\n\n';
  const bytes = new TextEncoder().encode(raw);
  let delivered = false;
  return {
    ok: true,
    status: 200,
    text: async () => raw,
    body: {
      getReader() {
        return {
          async read() {
            if (delivered) return { done: true, value: undefined };
            delivered = true;
            return { done: false, value: bytes };
          },
        };
      },
    },
  };
}

function toolCallTurnChunks(toolName, argsObj, callId) {
  return [
    {
      id: 'chunk-1',
      choices: [{
        delta: {
          tool_calls: [{
            index: 0,
            id: callId,
            type: 'function',
            function: { name: toolName, arguments: JSON.stringify(argsObj) },
          }],
        },
      }],
    },
    { id: 'chunk-1', choices: [{ delta: {}, finish_reason: 'tool_calls' }] },
  ];
}

function finalAnswerChunks(text) {
  return [
    { id: 'chunk-2', choices: [{ delta: { content: text } }] },
    { id: 'chunk-2', choices: [{ delta: {}, finish_reason: 'stop' }] },
  ];
}

/** Fetch stub for a full send_muxplex_session_input turn: first completions
 * call requests the tool, the /input call refuses with a 403 carrying
 * `fenceDetail` as the server's `detail`, and the continuation completions
 * call finishes the turn with plain text (no further tool calls). */
function makeInputFenceFetch({ fenceDetail, sessionName = 'counter' }) {
  let completionsCalls = 0;
  return async (url) => {
    if (url === '/api/agent/chat/completions') {
      completionsCalls++;
      if (completionsCalls === 1) {
        return sseChunksResponse(
          toolCallTurnChunks('send_muxplex_session_input', { session_name: sessionName, text: 'ls', enter: true }, 'call_1')
        );
      }
      return sseChunksResponse(finalAnswerChunks('Okay.'));
    }
    if (url === `/api/sessions/${sessionName}/input`) {
      return { ok: false, status: 403, text: async () => JSON.stringify({ detail: fenceDetail }) };
    }
    throw new Error('unexpected fetch url in test: ' + url);
  };
}

/** Fetch stub for a plain, tool-free turn that finishes immediately --
 * used by the send/newline keydown tests, which only care whether a
 * request was made at all. */
function simpleFinalFetch(text) {
  return async (url) => {
    if (url === '/api/agent/chat/completions') {
      return sseChunksResponse(finalAnswerChunks(text || 'ok'));
    }
    throw new Error('unexpected fetch url in test: ' + url);
  };
}

function neverFetch() {
  return async (url) => { throw new Error('fetch should not have been called for: ' + url); };
}

/** Fetch stub for GET/PATCH /api/settings (muxplex-2qs persistence tests).
 * `initial` seeds what GET returns; a PATCH merges its body into that same
 * in-memory object, mirroring the real server's merge-and-return contract
 * (muxplex/settings.py's patch_settings()) closely enough for these tests.
 * `gate`, if given, is awaited before the GET response is returned -- used
 * to simulate a slow-to-resolve initial load racing against a user click. */
function settingsFetch(initial, { gate } = {}) {
  var current = Object.assign({}, initial);
  return async (url, opts) => {
    if (url !== '/api/settings') {
      throw new Error('unexpected fetch url in test: ' + url);
    }
    var method = (opts && opts.method) || 'GET';
    if (method === 'GET') {
      if (gate) await gate.promise;
      return { ok: true, status: 200, text: async () => JSON.stringify(current) };
    }
    if (method === 'PATCH') {
      Object.assign(current, JSON.parse(opts.body));
      return { ok: true, status: 200, text: async () => JSON.stringify(current) };
    }
    throw new Error('unexpected method in test: ' + method);
  };
}

/** A manually-resolvable gate for delaying a stubbed fetch response. */
function makeGate() {
  var resolve;
  var promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

/** Drive a full user turn through the real DOM: type text, click Send,
 * wait for the confirmation gate to open (the panel says so on screen),
 * click the dialog's Send, then wait for the whole chain (including the
 * continuation request) to finish. */
async function driveSendAndConfirm(panel, userText) {
  panel.els['chat-input'].value = userText;
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(
    () => fullText(panel.els['chat-messages']).includes('awaiting your confirmation'),
    { label: 'confirmation gate to open' }
  );
  panel.els['chat-confirm-send-btn']._fire('click');
  await waitUntil(
    () => panel.els['chat-send-btn'].disabled === false,
    { label: 'turn (including continuation) to finish' }
  );
}

// =======================================================================
// GROUP 1 (muxplex-9n9) -- the 403 fence classifier, both branches
// =======================================================================

test('9n9: 403 GLOBAL switch names input_enabled and does not claim the allowlist', async () => {
  const fenceDetail = 'Session input is disabled (settings.input_enabled=false)';
  const panel = loadChatPanel({ fetchImpl: makeInputFenceFetch({ fenceDetail }) });
  await driveSendAndConfirm(panel, 'type ls into counter');
  const rendered = fullText(panel.els['chat-messages']);

  assert.match(rendered, /input_enabled/, 'must name the global switch');
  assert.doesNotMatch(rendered, /not on the allowlist/i, 'must not claim the session is missing from an allowlist');
  assert.match(rendered, /any session yet/, 'GLOBAL headline must be used');
  assert.doesNotMatch(rendered, /that particular session/, 'ALLOWLIST headline must not appear');
  // Raw server response still visible (transparency preserved).
  assert.ok(rendered.includes(fenceDetail), 'the real server detail must still be shown');
});

test('9n9: 403 ALLOWLIST names the allowlist and not the global-switch remedy', async () => {
  const fenceDetail = "Session 'counter' does not match any input_allowed_sessions pattern";
  const panel = loadChatPanel({ fetchImpl: makeInputFenceFetch({ fenceDetail }) });
  await driveSendAndConfirm(panel, 'type ls into counter');
  const rendered = fullText(panel.els['chat-messages']);

  assert.match(rendered, /input_allowed_sessions/, 'must name the allowlist setting');
  assert.match(rendered, /not on the allowlist/i, 'this IS the allowlist case -- the phrase belongs here');
  assert.doesNotMatch(rendered, /any session yet/, 'GLOBAL headline must not appear');
  assert.doesNotMatch(rendered, /turn it on by editing/i, 'GLOBAL-only remedy phrasing must not appear');
  assert.ok(rendered.includes(fenceDetail), 'the real server detail must still be shown');
});

test('9n9: rendered prose and rendered technical detail never disagree about which fence tripped', async () => {
  const cases = [
    { fenceDetail: 'Session input is disabled (settings.input_enabled=false)', expectGlobal: true },
    { fenceDetail: "Session 'counter' does not match any input_allowed_sessions pattern", expectGlobal: false },
  ];
  for (const { fenceDetail, expectGlobal } of cases) {
    const panel = loadChatPanel({ fetchImpl: makeInputFenceFetch({ fenceDetail }) });
    await driveSendAndConfirm(panel, 'type ls into counter');
    const rendered = fullText(panel.els['chat-messages']);

    const detailSaysGlobal = /input_enabled=false/.test(rendered);
    const proseSaysGlobal = /any session yet/.test(rendered);
    const proseSaysAllowlist = /that particular session/.test(rendered);

    assert.strictEqual(detailSaysGlobal, expectGlobal, 'sanity: mock detail matches the intended branch');
    // The exact contradiction muxplex-9n9 shipped: detail says one fence,
    // prose claims the other. Assert they always agree.
    if (detailSaysGlobal) {
      assert.ok(proseSaysGlobal, `detail says GLOBAL but prose does not, for: ${fenceDetail}`);
      assert.ok(!proseSaysAllowlist, `detail says GLOBAL but prose ALSO claims ALLOWLIST, for: ${fenceDetail}`);
    } else {
      assert.ok(proseSaysAllowlist, `detail says ALLOWLIST but prose does not, for: ${fenceDetail}`);
      assert.ok(!proseSaysGlobal, `detail says ALLOWLIST but prose ALSO claims GLOBAL, for: ${fenceDetail}`);
    }
  }
});

// =======================================================================
// GROUP 2 (muxplex-ixl) -- model-facing guidance must never reach the DOM
// =======================================================================

test('ixl: model-directed guidance never reaches the rendered panel', async () => {
  const fenceDetail = 'Session input is disabled (settings.input_enabled=false)';
  const panel = loadChatPanel({ fetchImpl: makeInputFenceFetch({ fenceDetail }) });
  await driveSendAndConfirm(panel, 'type ls into counter');
  const rendered = fullText(panel.els['chat-messages']);

  for (const phrase of ['TELL THE USER', 'Do NOT retry', 'Do NOT tell them', 'This is NOT a dead end']) {
    assert.ok(!rendered.includes(phrase), `rendered panel text must not contain: "${phrase}"`);
  }
  // Transparency is still a feature: the raw server response is one click away.
  assert.ok(rendered.includes(fenceDetail), 'the technical-detail block must still show the real server response');
});

test('ixl: the model still receives the full guidance, via the continuation request body', async () => {
  const fenceDetail = 'Session input is disabled (settings.input_enabled=false)';
  const panel = loadChatPanel({ fetchImpl: makeInputFenceFetch({ fenceDetail }) });
  await driveSendAndConfirm(panel, 'type ls into counter');

  const completionsCalls = panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions');
  assert.strictEqual(completionsCalls.length, 2, 'expected an initial POST and one continuation POST');

  const continuationBody = JSON.parse(completionsCalls[1].opts.body);
  const toolMessages = continuationBody.messages.filter((m) => m.role === 'tool');
  assert.ok(toolMessages.length >= 1, 'expected at least one role:"tool" message in the continuation body');

  const combined = toolMessages.map((m) => m.content).join('\n');
  assert.match(combined, /TELL THE USER/, 'the model must still receive the guidance -- it was separated, not deleted');
  assert.match(combined, /Do NOT retry this call/);
});

// =======================================================================
// GROUP 3 (muxplex-18f) -- configurable send/newline modes
// =======================================================================

test('18f: default mode with nothing stored is enter-newline', () => {
  const panel = loadChatPanel({ fetchImpl: neverFetch() });
  assert.strictEqual(panel.prefs.getSendMode(), panel.prefs.SEND_MODE_NEWLINE);
});

test('18f: an unrecognised stored value falls back to enter-newline', () => {
  const panel = loadChatPanel({ fetchImpl: neverFetch() });
  panel.storage.api.setItem('muxplex-agent-send-mode', 'bogus-value');
  assert.strictEqual(panel.prefs.getSendMode(), panel.prefs.SEND_MODE_NEWLINE);
});

test('18f: setSendMode persists to localStorage and a fresh read returns it', () => {
  const panel = loadChatPanel({ fetchImpl: neverFetch() });
  const stored = panel.prefs.setSendMode(panel.prefs.SEND_MODE_SEND);
  assert.strictEqual(stored, panel.prefs.SEND_MODE_SEND);
  assert.strictEqual(panel.storage.store['muxplex-agent-send-mode'], panel.prefs.SEND_MODE_SEND);
  assert.strictEqual(panel.prefs.getSendMode(), panel.prefs.SEND_MODE_SEND);
});

test('18f: getSendMode does not throw when localStorage throws, and returns the default', () => {
  const panel = loadChatPanel({ fetchImpl: neverFetch() });
  panel.storage.setThrowOnGet(true);
  let result;
  assert.doesNotThrow(() => { result = panel.prefs.getSendMode(); });
  assert.strictEqual(result, panel.prefs.SEND_MODE_NEWLINE);
});

test('18f: Mode A (default) -- plain Enter does not send and does not preventDefault', () => {
  const panel = loadChatPanel({ fetchImpl: neverFetch() });
  panel.els['chat-input'].value = 'hello';
  let prevented = false;
  panel.els['chat-input']._fire('keydown', {
    key: 'Enter', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert.strictEqual(prevented, false);
  assert.strictEqual(
    panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 0,
    'bare Enter in Mode A must not send'
  );
});

test('18f: Mode A (default) -- Ctrl+Enter sends', async () => {
  const panel = loadChatPanel({ fetchImpl: simpleFinalFetch('ok') });
  panel.els['chat-input'].value = 'hello';
  let prevented = false;
  panel.els['chat-input']._fire('keydown', {
    key: 'Enter', ctrlKey: true, metaKey: false, shiftKey: false, altKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert.strictEqual(prevented, true);
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });
  assert.strictEqual(
    panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 1,
    'Ctrl+Enter in Mode A must send'
  );
});

test('18f: Mode A (default) -- Cmd+Enter (metaKey) sends', async () => {
  const panel = loadChatPanel({ fetchImpl: simpleFinalFetch('ok') });
  panel.els['chat-input'].value = 'hello';
  let prevented = false;
  panel.els['chat-input']._fire('keydown', {
    key: 'Enter', ctrlKey: false, metaKey: true, shiftKey: false, altKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert.strictEqual(prevented, true);
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });
  assert.strictEqual(
    panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 1,
    'Cmd+Enter in Mode A must send'
  );
});

test('18f: Mode A (default) -- Ctrl+J does not send and inserts a newline', () => {
  const panel = loadChatPanel({ fetchImpl: neverFetch() });
  const input = panel.els['chat-input'];
  input.value = 'ab';
  input.selectionStart = 1;
  input.selectionEnd = 1;
  let prevented = false;
  input._fire('keydown', {
    key: 'j', ctrlKey: true, metaKey: false, shiftKey: false, altKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert.strictEqual(prevented, true, 'Ctrl+J must be intercepted (browser default opens downloads panel)');
  assert.strictEqual(input.value, 'a\nb');
  assert.strictEqual(
    panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 0,
    'Ctrl+J must never send'
  );
});

test('18f: Mode B -- plain Enter sends', async () => {
  const panel = loadChatPanel({ fetchImpl: simpleFinalFetch('ok') });
  panel.prefs.setSendMode(panel.prefs.SEND_MODE_SEND);
  panel.els['chat-input'].value = 'hello';
  let prevented = false;
  panel.els['chat-input']._fire('keydown', {
    key: 'Enter', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert.strictEqual(prevented, true);
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });
  assert.strictEqual(
    panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 1,
    'bare Enter in Mode B must send'
  );
});

test('18f: Mode B -- Shift+Enter does not send (newline escape hatch)', () => {
  const panel = loadChatPanel({ fetchImpl: neverFetch() });
  panel.prefs.setSendMode(panel.prefs.SEND_MODE_SEND);
  panel.els['chat-input'].value = 'hello';
  let prevented = false;
  panel.els['chat-input']._fire('keydown', {
    key: 'Enter', ctrlKey: false, metaKey: false, shiftKey: true, altKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert.strictEqual(prevented, false);
  assert.strictEqual(
    panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 0,
    'Shift+Enter in Mode B must not send'
  );
});

test('18f: Mode B -- Ctrl+J does not send', () => {
  const panel = loadChatPanel({ fetchImpl: neverFetch() });
  panel.prefs.setSendMode(panel.prefs.SEND_MODE_SEND);
  const input = panel.els['chat-input'];
  input.value = 'ab';
  input.selectionStart = 1;
  input.selectionEnd = 1;
  input._fire('keydown', {
    key: 'j', ctrlKey: true, metaKey: false, shiftKey: false, altKey: false,
    preventDefault: () => {},
  });
  assert.strictEqual(input.value, 'a\nb');
  assert.strictEqual(
    panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 0,
    'Ctrl+J in Mode B must not send'
  );
});

test('18f: the hint names the chord that actually sends, and behavior agrees, in BOTH modes', async () => {
  // Mode A (default): hint says Ctrl+Enter to send; bare Enter must NOT send.
  {
    const panel = loadChatPanel({ fetchImpl: neverFetch() });
    assert.match(panel.els['chat-key-hint'].textContent, /Ctrl\+Enter to send/);
    panel.els['chat-input'].value = 'hello';
    let prevented = false;
    panel.els['chat-input']._fire('keydown', {
      key: 'Enter', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false,
      preventDefault: () => { prevented = true; },
    });
    assert.strictEqual(prevented, false);
    assert.strictEqual(
      panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 0,
      'hint said Ctrl+Enter to send -- bare Enter must not send'
    );
  }
  // Mode B: hint says Enter to send; bare Enter MUST send.
  {
    const panel = loadChatPanel({ fetchImpl: simpleFinalFetch('ok') });
    panel.prefs.setSendMode(panel.prefs.SEND_MODE_SEND);
    assert.match(panel.els['chat-key-hint'].textContent, /^Enter to send$/);
    panel.els['chat-input'].value = 'hello';
    let prevented = false;
    panel.els['chat-input']._fire('keydown', {
      key: 'Enter', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false,
      preventDefault: () => { prevented = true; },
    });
    assert.strictEqual(prevented, true);
    await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });
    assert.strictEqual(
      panel.fetchCalls.filter((c) => c.url === '/api/agent/chat/completions').length, 1,
      'hint said Enter to send -- bare Enter must send'
    );
  }
});

// =======================================================================
// GROUP 4 (muxplex-2qs) -- no close X
// =======================================================================

test('2qs: init() does not require a #chat-close-btn element', () => {
  // Every harness in this file already omits #chat-close-btn (it is not in
  // REQUIRED_IDS or OPTIONAL_IDS) -- this test makes that omission explicit
  // and asserts init() still completes without throwing.
  let panel;
  assert.doesNotThrow(() => {
    panel = loadChatPanel({ fetchImpl: neverFetch(), includeCloseBtn: false });
  });
  assert.strictEqual(panel.document.getElementById('chat-close-btn'), null);
  // And the panel is otherwise fully wired (init() ran to completion, not
  // short-circuited before the fatal-missing-element check).
  assert.strictEqual(typeof panel.prefs.getSendMode, 'function');
});

// =======================================================================
// GROUP 5 (muxplex-2qs) -- persisted open/closed state via GET/PATCH
// /api/settings. This is the SAME server-side mechanism app.js's left
// session sidebar uses (settings.sidebarOpen in muxplex/settings.py --
// see DEFAULT_SETTINGS/SYNCABLE_KEYS), just fetched independently here
// since chat.js is a closed IIFE that does not reach into app.js's
// _serverSettings/patchServerSetting internals (see the file's own
// "ONE deliberate exception" note near its end). Key name: agentPanelOpen.
// =======================================================================

test('2qs: agentPanelOpen:true from the server opens the panel on load', async () => {
  const panel = loadChatPanel({ fetchImpl: settingsFetch({ agentPanelOpen: true }) });
  await waitUntil(
    () => panel.els['chat-open-btn'].getAttribute('aria-pressed') === 'true',
    { label: 'panel to open from persisted settings' }
  );
  assert.strictEqual(panel.els['chat-panel'].classList.contains('hidden'), false);
});

test('2qs: agentPanelOpen:false from the server leaves the panel closed', async () => {
  const panel = loadChatPanel({ fetchImpl: settingsFetch({ agentPanelOpen: false }) });
  await new Promise((r) => setTimeout(r, 10)); // let the init-time GET settle
  assert.strictEqual(panel.els['chat-open-btn'].getAttribute('aria-pressed'), 'false');
  assert.strictEqual(panel.els['chat-panel'].classList.contains('hidden'), true);
});

test('2qs: agentPanelOpen:null (never toggled) leaves the panel closed -- no width auto-detect like the sidebar has', async () => {
  const panel = loadChatPanel({ fetchImpl: settingsFetch({ agentPanelOpen: null }) });
  await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(panel.els['chat-open-btn'].getAttribute('aria-pressed'), 'false');
  assert.strictEqual(panel.els['chat-panel'].classList.contains('hidden'), true);
});

test('2qs: clicking the Agent button PATCHes agentPanelOpen with the new value', async () => {
  const panel = loadChatPanel({ fetchImpl: settingsFetch({ agentPanelOpen: false }) });
  await new Promise((r) => setTimeout(r, 10)); // let the init-time GET settle first
  panel.els['chat-open-btn']._fire('click');
  await waitUntil(
    () => panel.fetchCalls.some((c) => c.url === '/api/settings' && c.opts && c.opts.method === 'PATCH'),
    { label: 'PATCH /api/settings after toggle' }
  );
  const patchCall = panel.fetchCalls.find((c) => c.url === '/api/settings' && c.opts.method === 'PATCH');
  assert.deepStrictEqual(JSON.parse(patchCall.opts.body), { agentPanelOpen: true });
  // Toggling closed again PATCHes false.
  panel.els['chat-open-btn']._fire('click');
  await waitUntil(
    () => panel.fetchCalls.filter((c) => c.url === '/api/settings' && c.opts.method === 'PATCH').length === 2,
    { label: 'second PATCH /api/settings after re-toggle' }
  );
  const secondPatch = panel.fetchCalls.filter((c) => c.url === '/api/settings' && c.opts.method === 'PATCH')[1];
  assert.deepStrictEqual(JSON.parse(secondPatch.opts.body), { agentPanelOpen: false });
});

test('2qs: a user toggle that lands before the init-time GET resolves is not clobbered by the stale read', async () => {
  const gate = makeGate();
  const panel = loadChatPanel({ fetchImpl: settingsFetch({ agentPanelOpen: false }, { gate }) });
  // The user opens the panel before the init-time GET (agentPanelOpen:
  // false, fetched before this click) has resolved.
  panel.els['chat-open-btn']._fire('click');
  assert.strictEqual(panel.els['chat-open-btn'].getAttribute('aria-pressed'), 'true');
  // Now let the stale GET resolve.
  gate.resolve();
  await new Promise((r) => setTimeout(r, 10));
  // The user's own toggle must win -- the panel must still be open, not
  // reverted to the stale false the GET was carrying.
  assert.strictEqual(panel.els['chat-open-btn'].getAttribute('aria-pressed'), 'true');
  assert.strictEqual(panel.els['chat-panel'].classList.contains('hidden'), false);
});

// ---------------------------------------------------------------------
// Harness/contract drift guard
// ---------------------------------------------------------------------
//
// WHY THIS EXISTS: v0.49.0 added six required elements to chat.js's init()
// (the unconfigured-Agent gate and the chrome it hides). REQUIRED_IDS above
// was not updated in the same change, so every test in this file died at
// load with "chat panel BROKEN -- missing DOM element(s)" -- 23 failures
// that named the real cause but were only caught by CI, after the Python
// suite had gone green and the release looked ready to tag.
//
// The lesson is not "remember to update the fixture." It is that the
// fixture encoded init()'s required-element list a SECOND time, by hand,
// with nothing checking the copy against the original. This test makes the
// duplication self-checking: it reads chat.js's own `__missing.push("...")`
// calls -- the single source of truth -- and fails if REQUIRED_IDS drifts
// from them in either direction.
//
// Deliberately parses source text rather than importing a list: init()'s
// checks are inline `if (!el) __missing.push("id")` statements, and
// exporting a list purely for tests would be a second copy again -- the
// exact thing this guards against. A source-text tripwire per AGENTS.md.
test('REQUIRED_IDS matches every id chat.js init() actually requires', () => {
  const declared = [...chatJsSource.matchAll(/__missing\.push\("([^"]+)"\)/g)]
    .map((m) => m[1]);

  assert.ok(
    declared.length > 0,
    'could not find any __missing.push("id") calls in chat.js -- this guard ' +
    'has been silently disarmed by a refactor of init()\'s required-element ' +
    'check; re-point it at the new shape rather than deleting it'
  );

  const fromSource = new Set(declared);
  // chat-confirm-dialog is required by init() but is deliberately NOT in
  // REQUIRED_IDS: it needs showModal()/close()/open, so loadChatPanel()
  // builds it from env.makeDialog() instead of the plain StubNode loop.
  // It is still provided by the harness, so it belongs in this comparison
  // -- listed explicitly rather than by relaxing the check, so a genuinely
  // missing element can never hide behind a loosened guard.
  const fromHarness = new Set([...REQUIRED_IDS, 'chat-confirm-dialog']);

  const missingFromHarness = [...fromSource].filter((id) => !fromHarness.has(id));
  const staleInHarness = [...fromHarness].filter((id) => !fromSource.has(id));

  assert.deepStrictEqual(
    missingFromHarness, [],
    'chat.js init() requires element(s) the harness never creates, so every ' +
    'test in this file would fail at load: ' + missingFromHarness.join(', ')
  );
  assert.deepStrictEqual(
    staleInHarness, [],
    'the harness creates element(s) init() no longer requires -- stale ' +
    'fixture entries hide the fact that the contract shrank: ' +
    staleInHarness.join(', ')
  );
});

// =======================================================================
// GROUP 6 (muxplex-fx1 stale-gate fix) -- a successful credential save (or
// a settings-dialog close) must re-validate the chat panel's OWN "Agent
// isn't set up" gate immediately, without requiring the user to close and
// reopen the panel. Owner's report: "once I added a key and closed
// settings, it still said it needed to be configured in the panel."
// =======================================================================

test('fx1: a successful credential save immediately clears the chat panel gate (no reopen)', async () => {
  const panel = loadChatPanel({ fetchImpl: agentCredentialFetch('not_configured') });
  await new Promise((r) => setTimeout(r, 10)); // let the init-time /api/settings GET settle

  const formEls = addAgentCredentialFormEls(panel);
  panel.credential.bindForm();

  // Put the panel into the SAME gated state a real open-while-unconfigured
  // would (checkAgentGate() is exposed as recheckGate() -- see chat.js's
  // window.muxplexAgentCredential). This is the harness's stand-in for
  // "the user opened the panel once before adding a key."
  await panel.credential.recheckGate();
  assert.strictEqual(panel.els['chat-composer'].classList.contains('hidden'), true, 'composer must start hidden (gated)');
  assert.strictEqual(panel.els['chat-gate'].classList.contains('hidden'), false, 'gate must start visible');

  // Submit a valid key -- entirely through the real form-submit handler,
  // never by calling recheckGate() ourselves again. If the fix regresses,
  // this is the only thing that would leave the gate stuck.
  formEls.keyInput.value = 'a-valid-looking-key';
  formEls.form._fire('submit', { preventDefault: () => {} });
  await waitUntil(() => formEls.submitBtn.disabled === false, { label: 'credential submit to finish' });

  assert.strictEqual(panel.els['chat-composer'].classList.contains('hidden'), false, 'composer must be visible immediately after a successful save -- no reopen required');
  assert.strictEqual(panel.els['chat-gate'].classList.contains('hidden'), true, 'gate must be hidden immediately after a successful save');
  assert.match(fullText(formEls.resultEl), /Key saved/);
});

test('fx1: a rejected (bad) key leaves the chat panel gate exactly as it was', async () => {
  const panel = loadChatPanel({ fetchImpl: agentCredentialFetch('not_configured') });
  await new Promise((r) => setTimeout(r, 10));

  const formEls = addAgentCredentialFormEls(panel);
  panel.credential.bindForm();
  await panel.credential.recheckGate();
  assert.strictEqual(panel.els['chat-composer'].classList.contains('hidden'), true);

  formEls.keyInput.value = 'bad-key';
  formEls.form._fire('submit', { preventDefault: () => {} });
  await waitUntil(() => formEls.submitBtn.disabled === false, { label: 'credential submit to finish' });

  assert.match(fullText(formEls.resultEl), /Rejected/);
  // The gate must NOT have been cleared -- a rejected key changes nothing
  // about whether the Agent is actually configured.
  assert.strictEqual(panel.els['chat-composer'].classList.contains('hidden'), true, 'composer must stay hidden after a rejected key');
  assert.strictEqual(panel.els['chat-gate'].classList.contains('hidden'), false, 'gate must stay visible after a rejected key');
});

test('fx1: window.muxplexAgentCredential.recheckGate is the real checkAgentGate (settings-close seam)', async () => {
  // app.js's closeSettings() calls window.muxplexAgentCredential.recheckGate()
  // as a belt-and-suspenders re-check. This pins that the exposed function
  // really does drive the SAME gate checkAgentGate()/setGateState() owns --
  // not a inert stand-in -- by exercising it with no form involved at all.
  const panel = loadChatPanel({ fetchImpl: agentCredentialFetch('not_configured') });
  await new Promise((r) => setTimeout(r, 10));

  assert.strictEqual(typeof panel.credential.recheckGate, 'function');
  await panel.credential.recheckGate();
  assert.strictEqual(panel.els['chat-composer'].classList.contains('hidden'), true, 'unconfigured -> gated');

  await panel.credential.recheckGate();
  assert.strictEqual(panel.els['chat-composer'].classList.contains('hidden'), true, 'repeat calls do not flip state on their own');
});

// =======================================================================
// GROUP 7 (muxplex-h2f) -- the chat panel's focus awareness: the browser's
// own currently-open/zoomed session (app.js's getFocusedSessionName(),
// NOT _activeView -- see that function's docstring for why) is surfaced
// to the model two ways: (a) annotated onto list_muxplex_sessions' own
// result, (b) a per-turn context line built fresh on every request. Owner's
// report: "I don't actually have a way to see which session is currently
// focused/active in your dashboard."
// =======================================================================

/** Fetch stub for a full list_muxplex_sessions turn: the completions call
 * requests the tool, GET /api/sessions answers with two sessions, and the
 * continuation completions call captures whatever was actually POSTed
 * (both the tool's own {role:"tool"} result AND the system message) so the
 * test can inspect both muxplex-h2f mechanisms in one real turn. */
function makeListSessionsFetch(sessionsPayload) {
  const completionsRequests = [];
  let completionsCalls = 0;
  return {
    completionsRequests,
    fetchImpl: async (url, opts) => {
      if (url === '/api/agent/chat/completions') {
        completionsCalls++;
        if (opts && opts.body) completionsRequests.push(JSON.parse(opts.body));
        if (completionsCalls === 1) {
          return sseChunksResponse(toolCallTurnChunks('list_muxplex_sessions', {}, 'call_1'));
        }
        return sseChunksResponse(finalAnswerChunks('Done.'));
      }
      if (url === '/api/sessions') {
        // list_muxplex_sessions goes through apiFetch(), which reads
        // `.text()` (not `.json()`) -- see apiFetch()'s own docstring.
        return { ok: true, status: 200, text: async () => JSON.stringify(sessionsPayload) };
      }
      throw new Error('unexpected fetch url in test: ' + url);
    },
  };
}

test('h2f: list_muxplex_sessions marks the browser-focused session with focused:true', async () => {
  const sessions = [
    { name: 'alpha', last_activity_at: 1, created_at: 1, cwd: '/a' },
    { name: 'beta', last_activity_at: 2, created_at: 2, cwd: '/b' },
  ];
  const { completionsRequests, fetchImpl } = makeListSessionsFetch(sessions);
  const panel = loadChatPanel({ fetchImpl });
  // Simulate the browser having "beta" open/zoomed in -- app.js's own
  // function, stubbed here exactly the way it is really defined for real
  // (a plain function on window), matching chat.js's guarded
  // `typeof window.getFocusedSessionName === "function"` read.
  panel.window.getFocusedSessionName = () => 'beta';

  panel.els['chat-input'].value = 'what sessions do I have?';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  // Second completions request is the continuation carrying the tool
  // result -- find the {role:"tool"} message and parse its JSON body.
  const continuation = completionsRequests[1];
  assert.ok(continuation, 'expected a continuation request after the tool call');
  const toolMsg = continuation.messages.find((m) => m.role === 'tool');
  assert.ok(toolMsg, 'expected a {role:"tool"} message in the continuation');
  const parsed = JSON.parse(toolMsg.content);
  const betaEntry = parsed.find((s) => s.name === 'beta');
  const alphaEntry = parsed.find((s) => s.name === 'alpha');
  assert.strictEqual(betaEntry.focused, true, 'the focused session must carry focused:true');
  assert.strictEqual(alphaEntry.focused, undefined, 'a non-focused session must not carry a focused key at all');
  // Purely additive: the pre-existing fields must be completely unchanged.
  assert.strictEqual(betaEntry.last_activity_at, 2);
  assert.strictEqual(betaEntry.cwd, '/b');
});

test('h2f: list_muxplex_sessions marks no entry as focused when nothing is open (all-sessions dashboard)', async () => {
  const sessions = [
    { name: 'alpha', last_activity_at: 1, created_at: 1, cwd: '/a' },
    { name: 'beta', last_activity_at: 2, created_at: 2, cwd: '/b' },
  ];
  const { completionsRequests, fetchImpl } = makeListSessionsFetch(sessions);
  const panel = loadChatPanel({ fetchImpl });
  panel.window.getFocusedSessionName = () => null; // grid overview, nothing zoomed in

  panel.els['chat-input'].value = 'what sessions do I have?';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  const continuation = completionsRequests[1];
  const toolMsg = continuation.messages.find((m) => m.role === 'tool');
  const parsed = JSON.parse(toolMsg.content);
  assert.ok(parsed.every((s) => s.focused === undefined), 'no entry should be marked focused');
});

test('h2f: the per-turn system prompt names the focused session', async () => {
  const { completionsRequests, fetchImpl } = makeListSessionsFetch([]);
  const panel = loadChatPanel({ fetchImpl });
  panel.window.getFocusedSessionName = () => 'sort-check';

  panel.els['chat-input'].value = 'what about the one in focus?';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  const first = completionsRequests[0];
  const systemMsg = first.messages.find((m) => m.role === 'system');
  assert.match(systemMsg.content, /"sort-check"/, 'system prompt must name the focused session');
  assert.match(systemMsg.content, /open\/expanded/i);
});

test('h2f: the per-turn system prompt honestly reports no single focus on the all-sessions dashboard', async () => {
  const { completionsRequests, fetchImpl } = makeListSessionsFetch([]);
  const panel = loadChatPanel({ fetchImpl });
  panel.window.getFocusedSessionName = () => null;

  panel.els['chat-input'].value = 'what about the one in focus?';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  const first = completionsRequests[0];
  const systemMsg = first.messages.find((m) => m.role === 'system');
  assert.match(systemMsg.content, /no single session/i);
  assert.match(systemMsg.content, /all-sessions dashboard/i);
});

test('h2f: a second request within the SAME turn re-reads focus live (tool-call round trip)', async () => {
  // Regression guard for "read live, not a stale snapshot" -- the focus
  // line is computed once per HTTP request (runTurn() is called again for
  // the continuation), so a focus change mid-turn is reflected on the very
  // next request rather than carried over from the first.
  let focused = 'alpha';
  const { completionsRequests, fetchImpl } = makeListSessionsFetch([
    { name: 'alpha', last_activity_at: 1, created_at: 1, cwd: '/a' },
  ]);
  const panel = loadChatPanel({
    fetchImpl: async (url, opts) => {
      if (url === '/api/agent/chat/completions' && opts && opts.body) {
        // Flip focus after the FIRST request is sent, before the second is
        // built -- simulates the user switching sessions mid-turn.
        if (JSON.parse(opts.body).messages.some((m) => m.role === 'user')) focused = 'beta';
      }
      return fetchImpl(url, opts);
    },
  });
  panel.window.getFocusedSessionName = () => focused;

  panel.els['chat-input'].value = 'hello';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  const firstSystem = completionsRequests[0].messages.find((m) => m.role === 'system').content;
  const secondSystem = completionsRequests[1].messages.find((m) => m.role === 'system').content;
  assert.match(firstSystem, /"alpha"/);
  assert.match(secondSystem, /"beta"/);
});

test('h2f: focus context line is omitted entirely when getFocusedSessionName is unavailable', async () => {
  // Older frontend build / chat.js loaded standalone -- must not fabricate
  // either "focused" or "not focused"; the line is simply absent.
  const { completionsRequests, fetchImpl } = makeListSessionsFetch([]);
  const panel = loadChatPanel({ fetchImpl });
  // Deliberately do NOT set panel.window.getFocusedSessionName.

  panel.els['chat-input'].value = 'hello';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  const systemMsg = completionsRequests[0].messages.find((m) => m.role === 'system').content;
  assert.doesNotMatch(systemMsg, /Currently in focus/);
});

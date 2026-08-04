// Tests for terminal.js — WebSocket + xterm.js integration

import { createRequire } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const require = createRequire(import.meta.url);

// ─── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Load a fresh copy of terminal.js with isolated module-level state.
 * Returns { window } after the script has executed.
 */
function loadTerminal() {
  // Delete from require cache so each test gets fresh module-level state
  const modulePath = join(__dirname, '..', 'terminal.js');
  delete require.cache[require.resolve(modulePath)];

  // terminal.js reads: location.protocol, location.host, document.getElementById,
  // window.Terminal, window.FitAddon, window.innerWidth
  let capturedCloseHandler = null;
  let capturedReconnectFn = null;
  let capturedWsProtocols = null;
  let capturedOnDataFn = null;
  let capturedOnResizeFn = null;
  let termWriteMessages = [];
  let lastWsInstance = null;
  let capturedOscHandler = null;
  let clipboardWrites = [];

  let capturedWsUrl = null;
  let onDataCallCount = 0;
  let onResizeCallCount = 0;
  let focusCallCount = 0;

  const mockTerm = {
    cols: 80,
    rows: 24,
    open: () => {},
    onData: (fn) => { onDataCallCount++; capturedOnDataFn = fn; },
    onResize: (fn) => { onResizeCallCount++; capturedOnResizeFn = fn; },
    loadAddon: () => {},
    dispose: () => {},
    write: (data) => { termWriteMessages.push(data); },
    focus: () => { focusCallCount++; },
    attachCustomKeyEventHandler: () => {},
    getSelection: () => '',
    onSelectionChange: () => {},
    parser: {
      registerOscHandler: (code, handler) => {
        if (code === 52) capturedOscHandler = handler;
      },
    },
  };

  // Capture all messages sent via WebSocket.send()
  const sentMessages = [];

  // WebSocket mock — captures 'close' and 'open' handlers so we can fire them manually
  class MockWebSocket {
    constructor(_url, _protocols) {
      this.readyState = 1; // OPEN
      this.binaryType = '';
      this._handlers = {};
      lastWsInstance = this;
    }
    addEventListener(event, handler) {
      this._handlers[event] = handler;
      if (event === 'close') capturedCloseHandler = handler;
    }
    close() {}
    send(data) { sentMessages.push(data); }
  }
  MockWebSocket.OPEN = 1;

  // setTimeout mock: capture reconnect callback so we can fire it synchronously
  const origSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => {
    capturedReconnectFn = fn;
    return 0;
  };

  globalThis.WebSocket = MockWebSocket;
  globalThis.location = { protocol: 'http:', host: 'localhost' };

  // Stateful reconnect-overlay/takeover-button mocks (persistent objects, not
  // recreated per getElementById call) so tests can observe the actual
  // visibility/text state _showTerminalConflictOverlay() leaves behind.
  let overlayHidden = true;
  let overlayText = '';
  let takeoverBtnHidden = true;
  const overlayEl = {
    classList: {
      add: (c) => { if (c === 'hidden') overlayHidden = true; },
      remove: (c) => { if (c === 'hidden') overlayHidden = false; },
    },
  };
  const overlayTextEl = {
    get textContent() { return overlayText; },
    set textContent(v) { overlayText = v; },
  };
  const takeoverBtnEl = {
    classList: {
      add: (c) => { if (c === 'hidden') takeoverBtnHidden = true; },
      remove: (c) => { if (c === 'hidden') takeoverBtnHidden = false; },
    },
    onclick: null,
  };

  globalThis.document = {
    getElementById: (id) => {
      if (id === 'terminal-container') return { appendChild: () => {}, addEventListener: () => {} };
      if (id === 'reconnect-overlay') return overlayEl;
      if (id === 'reconnect-overlay-text') return overlayTextEl;
      if (id === 'reconnect-overlay-takeover-btn') return takeoverBtnEl;
      return null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    createElement: () => ({ style: {}, classList: { add: () => {}, remove: () => {} } }),
  };
  globalThis.window = {
    addEventListener: () => {},
    location: { href: '' },
    innerWidth: 1024,
    Terminal: function Terminal() { return mockTerm; },
    FitAddon: {
      FitAddon: function FitAddon() { return { fit: () => {} }; },
    },
    _openTerminal: undefined,
    _closeTerminal: undefined,
  };
  // Node 21+ ships a built-in read-only `navigator` global (Web platform
  // compat), so a plain assignment throws. Redefine it for the duration of
  // this module load.
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      clipboard: {
        writeText: (text) => { clipboardWrites.push(text); return Promise.resolve(); },
      },
    },
  });

  require(modulePath);

  // Restore setTimeout
  globalThis.setTimeout = origSetTimeout;

  // Find the most recently created MockWebSocket instance's open handler
  // by pulling it from the instance created during openTerminal() call.
  // We expose a fireOpen() helper so tests can simulate WebSocket connection.
  let lastOpenHandler = null;
  let wsConstructedCount = 0;
  const OrigMockWS = globalThis.WebSocket;
  globalThis.WebSocket = function MockWSTracker(url, protocols) {
    wsConstructedCount++;
    capturedWsUrl = url;
    capturedWsProtocols = protocols;
    const inst = new OrigMockWS(url);
    const origAddListener = inst.addEventListener.bind(inst);
    inst.addEventListener = function(event, handler) {
      if (event === 'open') lastOpenHandler = handler;
      origAddListener(event, handler);
    };
    lastWsInstance = inst;
    return inst;
  };
  globalThis.WebSocket.OPEN = 1;

  return {
    openTerminal: globalThis.window._openTerminal,
    closeTerminal: globalThis.window._closeTerminal,
    get onDataCallCount() { return onDataCallCount; },
    get onResizeCallCount() { return onResizeCallCount; },
    get sentMessages() { return sentMessages; },
    get capturedWsUrl() { return capturedWsUrl; },
    get capturedWsProtocols() { return capturedWsProtocols; },
    get capturedOnDataFn() { return capturedOnDataFn; },
    get capturedOnResizeFn() { return capturedOnResizeFn; },
    get termWriteMessages() { return termWriteMessages; },
    get focusCallCount() { return focusCallCount; },
    get clipboardWrites() { return clipboardWrites; },
    wsConstructedCount() { return wsConstructedCount; },
    overlayVisible() { return !overlayHidden; },
    overlayText() { return overlayText; },
    takeoverBtnVisible() { return !takeoverBtnHidden; },
    fireClose(event) { if (capturedCloseHandler) capturedCloseHandler(event); },
    fireOpen() { if (lastOpenHandler) lastOpenHandler(); },
    fireOsc52(base64Payload) {
      // xterm.js's registerOscHandler(52, cb) invokes cb with the payload
      // AFTER the OSC number -- i.e. "Pc;Pd" (selection target + base64
      // text), not the full "52;Pc;Pd" sequence.
      if (capturedOscHandler) capturedOscHandler('c;' + base64Payload);
    },
    fireMessage(data) {
      if (lastWsInstance && lastWsInstance._handlers['message']) {
        lastWsInstance._handlers['message']({ data });
      }
    },
    fireReconnect() { if (capturedReconnectFn) { capturedReconnectFn(); capturedReconnectFn = null; } },
    // Expose so we can re-patch setTimeout for the actual calls
    patchTimeout(fn) {
      const orig = globalThis.setTimeout;
      globalThis.setTimeout = (cb, _ms) => { capturedReconnectFn = cb; return 0; };
      fn();
      globalThis.setTimeout = orig;
    },
  };
}

// ─── Tests ───────────────────────────────────────────────────────────────────────

test('onData is registered exactly once after initial connect (no reconnect)', () => {
  const t = loadTerminal();

  // Patch setTimeout so reconnect callbacks are captured but not auto-run
  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => 0;

  t.openTerminal('my-session');

  globalThis.setTimeout = orig;

  assert.strictEqual(t.onDataCallCount, 1, 'onData should be registered exactly once');
});

test('onResize is registered exactly once after initial connect (no reconnect)', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => 0;

  t.openTerminal('my-session');

  globalThis.setTimeout = orig;

  assert.strictEqual(t.onResizeCallCount, 1, 'onResize should be registered exactly once');
});

test('onData is NOT re-registered after reconnect — count stays at 1', () => {
  let reconnectFn = null;
  const orig = globalThis.setTimeout;

  const t = loadTerminal();

  // Patch setTimeout to capture reconnect callback
  globalThis.setTimeout = (fn, _ms) => { reconnectFn = fn; return 0; };

  t.openTerminal('my-session');

  // Simulate WebSocket dropping — triggers close handler which schedules reconnect
  t.fireClose();

  // Fire the reconnect (calls connect() again)
  if (reconnectFn) reconnectFn();

  globalThis.setTimeout = orig;

  assert.strictEqual(
    t.onDataCallCount,
    1,
    'onData should still be registered exactly once after a reconnect',
  );
});

test('onResize is NOT re-registered after reconnect — count stays at 1', () => {
  let reconnectFn = null;
  const orig = globalThis.setTimeout;

  const t = loadTerminal();

  globalThis.setTimeout = (fn, _ms) => { reconnectFn = fn; return 0; };

  t.openTerminal('my-session');

  t.fireClose();
  if (reconnectFn) reconnectFn();

  globalThis.setTimeout = orig;

  assert.strictEqual(
    t.onResizeCallCount,
    1,
    'onResize should still be registered exactly once after a reconnect',
  );
});

test('onData count stays at 1 after multiple reconnects', () => {
  let reconnectFn = null;
  const orig = globalThis.setTimeout;

  const t = loadTerminal();

  globalThis.setTimeout = (fn, _ms) => { reconnectFn = fn; return 0; };

  t.openTerminal('my-session');

  // Reconnect 3 times
  for (let i = 0; i < 3; i++) {
    t.fireClose();
    if (reconnectFn) { reconnectFn(); reconnectFn = null; }
  }

  globalThis.setTimeout = orig;

  assert.strictEqual(
    t.onDataCallCount,
    1,
    'onData should be registered exactly once even after 3 reconnects',
  );
});

test('_fitAddon is nulled out when closeTerminal is called', () => {
  // This is a whitebox test: verify no crash on dispose + null
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => 0;

  t.openTerminal('my-session');
  // Should not throw
  assert.doesNotThrow(() => t.closeTerminal(), 'closeTerminal should not throw');

  globalThis.setTimeout = orig;
});

test('initVisualViewport returns early without error when window.visualViewport is undefined', () => {
  // Guard test: non-mobile environments have no visualViewport — must not throw
  const t = loadTerminal();

  // globalThis.window has no visualViewport (see loadTerminal setup)
  assert.strictEqual(globalThis.window.visualViewport, undefined,
    'test pre-condition: window.visualViewport must be undefined');

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => 0;

  // openTerminal internally calls initVisualViewport — must not throw
  assert.doesNotThrow(() => t.openTerminal('test-session'),
    'openTerminal (and initVisualViewport) should not throw when window.visualViewport is undefined');

  globalThis.setTimeout = orig;
});

// ─── Multi-session helpers ────────────────────────────────────────────────────

/**
 * Load a fresh terminal.js with a multi-WS-instance-aware environment.
 * Unlike loadTerminal(), this tracks ALL WebSocket instances in order so tests
 * can inspect individual connections after multiple openTerminal() calls.
 */
function createMultiSessionEnv() {
  const modulePath = join(__dirname, '..', 'terminal.js');
  delete require.cache[require.resolve(modulePath)];

  const wsInstances = [];   // all WS objects created, in order
  const termInstances = []; // all Terminal objects created, in order

  class MockWS {
    constructor(url, protocols) {
      this.url = url;
      this.protocols = protocols;
      this.readyState = 1; // OPEN
      this.binaryType = '';
      this._handlers = {};
      this.closeCalled = false;
      this.sentMessages = [];
      wsInstances.push(this);
    }
    addEventListener(event, fn) { this._handlers[event] = fn; }
    fire(event, arg) { if (this._handlers[event]) this._handlers[event](arg); }
    close() { this.closeCalled = true; }
    send(data) { this.sentMessages.push(data); }
  }
  MockWS.OPEN = 1;
  MockWS.CONNECTING = 0;

  function makeMockTerm() {
    const t = {
      cols: 80, rows: 24,
      open: () => {},
      onData: () => {},
      onResize: () => {},
      loadAddon: () => {},
      dispose: () => {},
      focus: () => {},
      attachCustomKeyEventHandler: () => {},
      getSelection: () => '',
      onSelectionChange: () => {},
      parser: { registerOscHandler: () => {} },
      writeMessages: [],
    };
    t.write = (data) => t.writeMessages.push(data);
    termInstances.push(t);
    return t;
  }

  let capturedReconnectFn = null;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => { capturedReconnectFn = fn; return 0; };
  globalThis.WebSocket = MockWS;
  globalThis.location = { protocol: 'http:', host: 'localhost' };
  globalThis.document = {
    getElementById: (id) => {
      if (id === 'terminal-container') return { appendChild: () => {}, addEventListener: () => {} };
      if (id === 'reconnect-overlay') return { classList: { add: () => {}, remove: () => {} } };
      if (id === 'reconnect-overlay-text') return { textContent: '' };
      if (id === 'reconnect-overlay-takeover-btn') return { classList: { add: () => {}, remove: () => {} }, onclick: null };
      return null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    createElement: () => ({ style: {}, classList: { add: () => {}, remove: () => {} } }),
  };
  globalThis.window = {
    addEventListener: () => {},
    location: { href: '' },
    innerWidth: 1024,
    Terminal: function() { return makeMockTerm(); },
    FitAddon: { FitAddon: function() { return { fit: () => {} }; } },
    _openTerminal: undefined,
    _closeTerminal: undefined,
  };

  require(modulePath);
  globalThis.setTimeout = origSetTimeout;

  const env = {
    get wsInstances() { return wsInstances; },
    get termInstances() { return termInstances; },
    get capturedReconnectFn() { return capturedReconnectFn; },

    /** Call fn() with setTimeout mocked so reconnect timers are captured but not auto-run. */
    withTimeout(fn) {
      const orig = globalThis.setTimeout;
      globalThis.setTimeout = (cb, _ms) => { capturedReconnectFn = cb; return 0; };
      fn();
      globalThis.setTimeout = orig;
    },

    openTerminal(name) { env.withTimeout(() => globalThis.window._openTerminal(name)); },
    closeTerminal() { globalThis.window._closeTerminal(); },

    /** Fire the pending reconnect callback (if any), capturing any new reconnect it schedules. */
    fireReconnect() {
      if (!capturedReconnectFn) return;
      const fn = capturedReconnectFn;
      capturedReconnectFn = null;
      env.withTimeout(() => fn());
    },
  };

  return env;
}

// ─── Bug-fix regression tests ─────────────────────────────────────────────────
// Bug 1 — double keystrokes on switch-away-and-back
// Bug 2 — "Still in CONNECTING state" crash loop

test('openTerminal closes previous WebSocket before opening new connection (bug: stale WS double output)', () => {
  const env = createMultiSessionEnv();

  env.openTerminal('session-a');
  assert.strictEqual(env.wsInstances.length, 1, 'First openTerminal should create exactly 1 WS');
  const ws1 = env.wsInstances[0];

  env.openTerminal('session-b');

  // Bug 1: without the fix, ws1.close() is never called — the old socket stays alive and
  // both WS1 and WS2 write to the same xterm terminal, producing doubled keystrokes.
  assert.ok(ws1.closeCalled,
    'Bug 1: openTerminal must call close() on the previous WebSocket to prevent stale writes');
  assert.strictEqual(env.wsInstances.length, 2, 'Second openTerminal should have created a second WS');
});

test('stale open handler is a no-op after session switch (bug: crash loop)', () => {
  const env = createMultiSessionEnv();

  env.openTerminal('session-a');
  const ws1 = env.wsInstances[0];
  // Capture WS1's open handler before the switch displaces it
  const openHandler1 = ws1._handlers['open'];
  assert.ok(openHandler1, 'WS1 must have had an open handler registered');

  env.openTerminal('session-b');
  const ws2 = env.wsInstances[1];

  // Simulate WS1's open event arriving late (browser timing — arrives after WS2 is live).
  // Bug 2: without the stale guard, the handler does _ws.send() where _ws is now WS2
  // (which is CONNECTING) → WebSocket error → WS2 close → reconnect → infinite loop.
  if (openHandler1) openHandler1();

  assert.strictEqual(ws2.sentMessages.length, 0,
    'Bug 2: stale open handler for WS1 must not send auth/resize on the new WS2');
});

test('stale close handler does not trigger reconnect after session switch (bug: crash loop)', () => {
  const env = createMultiSessionEnv();

  env.openTerminal('session-a');
  const ws1 = env.wsInstances[0];
  const closeHandler1 = ws1._handlers['close'];
  assert.ok(closeHandler1, 'WS1 must have had a close handler registered');

  env.openTerminal('session-b');

  // After the switch: _ws = WS2, _currentSession = 'session-b'
  // Simulate WS1's close event arriving late (server finishes closing the old socket).
  let reconnectScheduled = false;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => { reconnectScheduled = true; return 0; };

  if (closeHandler1) closeHandler1();

  globalThis.setTimeout = origSetTimeout;

  // Bug 2: without stale guard, !_currentSession is false ('session-b' is set), so the
  // handler schedules connect() — a fresh WS replaces _ws while WS2 is CONNECTING → loop.
  // With stale guard: ws1 !== _ws (WS2) → return early → no reconnect.
  assert.ok(!reconnectScheduled,
    'Bug 2: stale close handler for WS1 must not schedule a reconnect after switching sessions');
});

// ─── ttyd protocol tests ──────────────────────────────────────────────────────
// ttyd 1.7.7 requires:
//   1. WebSocket subprotocol 'tty' — without it ttyd never starts the PTY
//   2. First message on open: TEXT frame '{"AuthToken":""}'
//   3. Second message on open: BINARY frame [0x31] + UTF-8({"columns":N,"rows":M})
//   4. Input keystrokes: BINARY [0x30] + UTF-8(keystroke)
//   5. Resize: BINARY [0x31] + UTF-8({"columns":N,"rows":M})
//   6. Received frames: 1-byte type prefix — 0x30=output (write to xterm), 0x31/0x32=ignore

test('connectWebSocket uses tty subprotocol', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');

  globalThis.setTimeout = orig;

  assert.deepStrictEqual(
    t.capturedWsProtocols,
    ['tty'],
    "WebSocket must be constructed with ['tty'] subprotocol — without it ttyd never starts the PTY",
  );
});

test('connectWebSocket sends text auth init as first message on open', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');
  t.fireOpen();

  globalThis.setTimeout = orig;

  assert.ok(t.sentMessages.length >= 1, 'should have sent at least one message on open');

  const firstMsg = t.sentMessages[0];
  assert.strictEqual(typeof firstMsg, 'string',
    `first message must be a text string (auth frame), got ${Object.prototype.toString.call(firstMsg)}`);

  const parsed = JSON.parse(firstMsg);
  assert.strictEqual(parsed.AuthToken, '', 'AuthToken must be empty string');
  assert.ok(!('columns' in parsed), 'auth-only TEXT frame should NOT contain columns');
  assert.ok(!('rows' in parsed), 'auth-only TEXT frame should NOT contain rows');
});

test('connectWebSocket sends binary resize with 0x31 prefix as second message on open', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');
  t.fireOpen();

  globalThis.setTimeout = orig;

  assert.ok(t.sentMessages.length >= 2, 'should have sent at least two messages on open (auth + resize)');

  const resizeMsg = t.sentMessages[1];
  assert.ok(resizeMsg instanceof Uint8Array,
    `resize message must be binary Uint8Array, got ${Object.prototype.toString.call(resizeMsg)}`);
  assert.strictEqual(resizeMsg[0], 0x31, 'first byte of resize message must be 0x31 (resize type)');

  const payload = JSON.parse(Buffer.from(resizeMsg.slice(1)).toString('utf-8'));
  assert.ok('columns' in payload, 'resize payload must contain columns');
  assert.ok('rows' in payload, 'resize payload must contain rows');
  assert.ok(typeof payload.columns === 'number' && payload.columns > 0,
    `columns must be a positive number, got ${payload.columns}`);
  assert.ok(typeof payload.rows === 'number' && payload.rows > 0,
    `rows must be a positive number, got ${payload.rows}`);
});

test('onData sends input with 0x30 type prefix as binary frame', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');
  t.fireOpen();

  const initCount = t.sentMessages.length;

  assert.ok(t.capturedOnDataFn, 'onData callback must have been registered');
  t.capturedOnDataFn('a');

  globalThis.setTimeout = orig;

  assert.strictEqual(t.sentMessages.length, initCount + 1, 'onData should send exactly one message');

  const msg = t.sentMessages[initCount];
  assert.ok(msg instanceof Uint8Array, 'keystroke message must be binary Uint8Array');
  assert.strictEqual(msg[0], 0x30, 'first byte of input message must be 0x30 (input type)');

  const text = Buffer.from(msg.slice(1)).toString('utf-8');
  assert.strictEqual(text, 'a', 'payload after type byte must be the keystroke string');
});

test('onResize sends resize with 0x31 type prefix as binary frame', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');
  t.fireOpen();

  const initCount = t.sentMessages.length;

  assert.ok(t.capturedOnResizeFn, 'onResize callback must have been registered');
  t.capturedOnResizeFn({ cols: 100, rows: 30 });

  globalThis.setTimeout = orig;

  assert.strictEqual(t.sentMessages.length, initCount + 1, 'onResize should send exactly one message');

  const msg = t.sentMessages[initCount];
  assert.ok(msg instanceof Uint8Array, 'resize message must be binary Uint8Array');
  assert.strictEqual(msg[0], 0x31, 'first byte of resize message must be 0x31 (resize type)');

  const payload = JSON.parse(Buffer.from(msg.slice(1)).toString('utf-8'));
  assert.strictEqual(payload.columns, 100, 'columns must match the resize event cols');
  assert.strictEqual(payload.rows, 30, 'rows must match the resize event rows');
});

test('message handler strips type byte and writes output for type 0x30', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');

  globalThis.setTimeout = orig;

  // Simulate receiving a terminal output frame: [0x30] + UTF-8('hello')
  const encoder = new TextEncoder();
  const hello = encoder.encode('hello');
  const msg = new Uint8Array(1 + hello.length);
  msg[0] = 0x30;
  msg.set(hello, 1);

  t.fireMessage(msg.buffer); // Pass as ArrayBuffer

  assert.strictEqual(t.termWriteMessages.length, 1, 'term.write should be called exactly once');

  const written = t.termWriteMessages[0];
  // After the UTF-8 fix: payload is decoded via TextDecoder before write(),
  // so xterm.js receives a string (not raw Uint8Array).
  // xterm.js write(Uint8Array) treated each byte as Latin-1 — TextDecoder fixes this.
  assert.strictEqual(typeof written, 'string',
    'data written to xterm must be a decoded string (TextDecoder fix for Latin-1 garbling)');
  assert.strictEqual(written, 'hello',
    'decoded output must match the original ASCII payload');
});

test('message handler ignores title type (0x31) — does not call term.write', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');

  globalThis.setTimeout = orig;

  const encoder = new TextEncoder();
  const title = encoder.encode('my session title');
  const msg = new Uint8Array(1 + title.length);
  msg[0] = 0x31;
  msg.set(title, 1);

  t.fireMessage(msg.buffer);

  assert.strictEqual(t.termWriteMessages.length, 0,
    'term.write must NOT be called for type 0x31 (window title)');
});

test('message handler ignores prefs type (0x32) — does not call term.write', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');

  globalThis.setTimeout = orig;

  const encoder = new TextEncoder();
  const prefs = encoder.encode('{}');
  const msg = new Uint8Array(1 + prefs.length);
  msg[0] = 0x32;
  msg.set(prefs, 1);

  t.fireMessage(msg.buffer);

  assert.strictEqual(t.termWriteMessages.length, 0,
    'term.write must NOT be called for type 0x32 (preferences)');
});

test('connectWebSocket URL uses /terminal/ws path', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('my-session');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.includes('/terminal/ws'),
    `WebSocket URL should include /terminal/ws, got: ${t.capturedWsUrl}`,
  );
});

// --- ?session= addressing (PER_SESSION_TTYD_SPEC.md §9.1) --------------------

test('connectWebSocket local branch includes ?session= with the session name', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('my-session');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.includes('?session=my-session'),
    `local WS URL must name the target session via ?session=, got: ${t.capturedWsUrl}`,
  );
});

test('connectWebSocket local branch appends &device_id= after ?session=', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('my-session', undefined, undefined, 'dev-42');

  globalThis.setTimeout = orig;

  assert.ok(
    t.capturedWsUrl.includes('?session=my-session&device_id=dev-42'),
    `expected ?session= then &device_id=, got: ${t.capturedWsUrl}`,
  );
});

test('connectWebSocket federation branch includes ?session= with the session name', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('remote-session', 'fed-abc123');

  globalThis.setTimeout = orig;

  assert.strictEqual(
    t.capturedWsUrl,
    'ws://localhost/federation/fed-abc123/terminal/ws?session=remote-session',
    `expected federation URL with ?session=, got: ${t.capturedWsUrl}`,
  );
});

test('initVisualViewport registers resize handler on window.visualViewport when present', () => {
  // RED test: stub does nothing; real impl must call addEventListener('resize', fn)
  const t = loadTerminal();

  let addedEvent = null;
  globalThis.window.visualViewport = {
    addEventListener: (event, _fn) => { addedEvent = event; },
    removeEventListener: (_event, _fn) => {},
  };

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => 0;

  t.openTerminal('test-session');

  globalThis.setTimeout = orig;
  delete globalThis.window.visualViewport;

  assert.strictEqual(addedEvent, 'resize',
    '_vpHandler should be registered as a resize listener on window.visualViewport');
});

test('terminal is auto-focused when WebSocket opens', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');
  t.fireOpen();

  globalThis.setTimeout = orig;

  assert.strictEqual(t.focusCallCount, 1,
    '_term.focus() should be called exactly once when the WebSocket open event fires');
});

// --- remoteId / federation proxy WebSocket tests ----------------------------

test('connectWebSocket uses federation proxy path when remoteId is provided', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('remote-session', 'fed-abc123');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  // Stale-assertion fix: this exact-match literal predates 6f44325 ("address
  // terminal WebSocket by session, not implicit state"), which deliberately
  // added `?session=<name>` to BOTH the local and federation branches
  // (PER_SESSION_TTYD_SPEC.md §9.1) -- the federation branch is no longer
  // exempt from session-addressing. That commit added a dedicated exact-match
  // test for the new shape ("connectWebSocket federation branch includes
  // ?session= with the session name", above) but did not update this
  // pre-existing test, leaving two contradictory exact-match assertions for
  // the identical openTerminal('remote-session', 'fed-abc123') call. The
  // behavior (URL includes ?session=remote-session) is correct and already
  // covered by that newer test; this one was simply never updated to match.
  // Per AGENTS.md's test_frontend_js.py precedent: fix the stale assertion to
  // follow the new structure rather than leave two tests asserting mutually
  // exclusive outcomes for the same call.
  assert.strictEqual(
    t.capturedWsUrl,
    'ws://localhost/federation/fed-abc123/terminal/ws?session=remote-session',
    `WebSocket URL should be ws://localhost/federation/fed-abc123/terminal/ws?session=remote-session, got: ${t.capturedWsUrl}`,
  );
});

test('connectWebSocket uses same-origin for remote sessions (no cross-origin WS)', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('remote-session', 'remote-device-1');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.startsWith('ws://localhost/'),
    `WebSocket URL for remote session must stay on same origin (ws://localhost/), got: ${t.capturedWsUrl}`,
  );
  assert.ok(
    t.capturedWsUrl.includes('/federation/remote-device-1/terminal/ws'),
    `WebSocket URL must include federation path, got: ${t.capturedWsUrl}`,
  );
});

test('connectWebSocket uses local origin when remoteId is empty string', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('local-session', '');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.includes('localhost'),
    `WebSocket URL should include localhost for empty remoteId, got: ${t.capturedWsUrl}`,
  );
  assert.ok(
    !t.capturedWsUrl.includes('/federation/'),
    `WebSocket URL must NOT include /federation/ for empty remoteId, got: ${t.capturedWsUrl}`,
  );
});

test('connectWebSocket uses local origin when remoteId is undefined', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('local-session');

  globalThis.setTimeout = orig;

  assert.ok(t.capturedWsUrl, 'WebSocket URL should have been captured');
  assert.ok(
    t.capturedWsUrl.includes('localhost'),
    `WebSocket URL should include localhost when remoteId is undefined, got: ${t.capturedWsUrl}`,
  );
  assert.ok(
    !t.capturedWsUrl.includes('/federation/'),
    `WebSocket URL must NOT include /federation/ when remoteId is undefined, got: ${t.capturedWsUrl}`,
  );
});

// --- Android touch scroll ---------------------------------------------------

test('terminal.js Android touch scroll is UA-gated', () => {
  const source = fs.readFileSync(
    new URL('../terminal.js', import.meta.url), 'utf8'
  );
  assert.ok(source.includes('Android'), 'must UA-detect Android before adding handlers');
  assert.ok(source.includes('requestAnimationFrame'), 'must use rAF to batch scroll dispatch');
  assert.ok(source.includes('e.preventDefault'), 'touchmove must preventDefault to block outer scroll');
  assert.ok(source.includes('WheelEvent'), 'must dispatch WheelEvent to xterm viewport');
  assert.ok(source.includes('passive: false'), 'touchmove must be non-passive');
  assert.ok(!source.includes('scrollLines'), 'must NOT use scrollLines (scrolls local buffer not PTY)');
});

// --- WebSocket reconnect + ttyd respawn ---

test('terminal.js WebSocket reconnect calls /connect after 2 failed attempts', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(source.includes('_reconnectAttempts'), 'must track reconnect attempts');
  assert.ok(source.includes('/api/sessions/'), 'must call connect API to respawn ttyd');
  assert.ok(source.includes('Math.pow'), 'must use exponential backoff');
});

test('terminal.js WebSocket reconnect awaits /connect before creating WS', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(source.includes('_reconnectAttempts'), 'must track reconnect attempts');
  // WS creation must be extracted into a separate helper — not inlined in connect()
  assert.ok(source.includes('_connectWebSocket'), 'must extract WS creation into _connectWebSocket helper');
  // The /connect fetch must use .then() to chain WS creation — not fire-and-forget
  assert.ok(source.includes('.then('), '/connect fetch must chain via .then() before WS creation');
  // connect() must return after scheduling the fetch chain, to prevent falling through to immediate WS creation
  const connectFn = source.substring(
    source.indexOf('function connect()'),
    source.indexOf('function _connectWebSocket'),
  );
  assert.ok(connectFn.includes('return;'), 'connect() must return after fetch to prevent falling through to immediate WS creation');
});

// --- Reconnect counter: must reset on message, not on open ---

test('terminal.js resets _reconnectAttempts on first message, not on open', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');

  // Find the open handler body (between "addEventListener('open'" and its closing "})")
  const openStart = source.indexOf("addEventListener('open'");
  assert.ok(openStart !== -1, "must have an open handler");
  // Find the matching closing "})" for the open handler — walk from openStart
  let depth = 0;
  let openBodyEnd = -1;
  for (let i = openStart; i < source.length - 1; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) { openBodyEnd = i; break; }
    }
  }
  assert.ok(openBodyEnd !== -1, "must find the end of the open handler");
  const openBody = source.substring(openStart, openBodyEnd + 1);

  // _reconnectAttempts = 0 must NOT appear in the open handler
  // (the proxy accepts before ttyd is alive, so open doesn't prove ttyd is up)
  assert.ok(
    !openBody.includes('_reconnectAttempts = 0'),
    '_reconnectAttempts must NOT be reset in the open handler — ' +
    'the proxy accepts the WS before confirming ttyd is alive; ' +
    'reset must happen on first message (proves ttyd is sending data)',
  );

  // _reconnectAttempts reset must appear in the message handler instead
  const msgStart = source.indexOf("addEventListener('message'");
  assert.ok(msgStart !== -1, "must have a message handler");
  let msgDepth = 0;
  let msgBodyEnd = -1;
  for (let i = msgStart; i < source.length - 1; i++) {
    if (source[i] === '{') msgDepth++;
    else if (source[i] === '}') {
      msgDepth--;
      if (msgDepth === 0) { msgBodyEnd = i; break; }
    }
  }
  assert.ok(msgBodyEnd !== -1, "must find the end of the message handler");
  const msgBody = source.substring(msgStart, msgBodyEnd + 1);
  assert.ok(
    msgBody.includes('_reconnectAttempts'),
    '_reconnectAttempts must be reset inside the message handler ' +
    '(first data message proves ttyd is alive and relaying)',
  );
});

// --- Clipboard integration ---

test('terminal.js has clipboard integration with Ctrl+Shift+C (copy) and native paste support', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(source.includes('attachCustomKeyEventHandler'), 'must register custom key handler');
  assert.ok(source.includes('getSelection'), 'must use getSelection() for copy');
  assert.ok(source.includes('clipboard'), 'must interact with clipboard API');
  assert.ok(source.includes('Shift'), 'must use Shift modifier to avoid conflict with terminal Ctrl+C/V');
  assert.ok(source.includes('_copyToClipboard') || source.includes('writeText'), 'must have copy mechanism');
});

// --- Issue 4: setTerminalFontSize ---

test('terminal.js exposes window._setTerminalFontSize function', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes('window._setTerminalFontSize'),
    'terminal.js must expose window._setTerminalFontSize for live font size updates'
  );
});

test('_setTerminalFontSize sets _term.options.fontSize and calls _fitAddon.fit()', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  // The function body must update _term.options.fontSize
  assert.ok(
    source.includes('_term.options.fontSize = size'),
    '_setTerminalFontSize must assign _term.options.fontSize = size'
  );
  // And call _fitAddon.fit()
  assert.ok(
    source.includes('_fitAddon.fit()'),
    '_setTerminalFontSize must call _fitAddon.fit() to reflow the terminal'
  );
});

// --- Clipboard Issue 1: auto-copy mouse selection via onSelectionChange ---

test('terminal.js auto-copies mouse selection to clipboard via onSelectionChange', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes('onSelectionChange'),
    'must register onSelectionChange handler to auto-copy mouse selection to clipboard',
  );
});

// --- Clipboard Issue 2: OSC 52 handler bridges tmux clipboard to browser ---

test('terminal.js registers OSC 52 handler for tmux clipboard bridge', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(
    source.includes('registerOscHandler'),
    'must call parser.registerOscHandler to intercept tmux OSC 52 clipboard sequences',
  );
  assert.ok(
    source.includes('atob'),
    'must decode base64 OSC 52 clipboard payload with atob()',
  );
});

// --- Clickable URLs via xterm-addon-web-links ---

test('terminal.js loads xterm-addon-web-links for clickable URLs', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(source.includes('WebLinksAddon'), 'must reference WebLinksAddon');
  assert.ok(
    source.includes('ctrlKey') || source.includes('metaKey'),
    'must check modifier key for link clicks',
  );
  assert.ok(source.includes('window.open'), 'must open URLs in new tab');
});

// --- Search addon (xterm-addon-search) ---

test('terminal.js loads xterm-addon-search', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(source.includes('SearchAddon'), 'must reference SearchAddon');
  assert.ok(source.includes('findNext') || source.includes('findPrevious'), 'must have search functions');
});

test('terminal.js has Ctrl+F search shortcut', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(source.includes('_openSearch'), 'must have search open function');
  assert.ok(source.includes('_closeSearch'), 'must have search close function');
});

// --- Image addon (xterm-addon-image) ---

test('terminal.js loads xterm-addon-image for inline graphics', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(source.includes('ImageAddon'), 'must reference ImageAddon');
});

// --- Ctrl+Shift+V: xterm.js handles paste natively, no custom interception ---

test('terminal.js does NOT intercept Ctrl+Shift+V in attachCustomKeyEventHandler', () => {
  // COE review: every custom paste handler we built caused either double-paste or encoding issues.
  // On Linux, Ctrl+Shift+V is a native browser paste shortcut — it fires a paste event on the
  // focused textarea, xterm.js catches it natively. On macOS, Cmd+V does the same.
  // Zero custom paste code needed.
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  const handlerStart = source.indexOf('attachCustomKeyEventHandler');
  const handlerEnd = source.indexOf('onSelectionChange', handlerStart);
  const handlerBlock = source.substring(handlerStart, handlerEnd);
  // Must NOT have any V key interception
  assert.ok(!handlerBlock.includes("e.key === 'V'"),
    'must NOT intercept Ctrl+Shift+V — xterm.js handles paste natively via browser events');
  assert.ok(!handlerBlock.includes("e.code === 'KeyV'"),
    'must NOT intercept KeyV — xterm.js handles paste natively via browser events');
});

// --- Shift+Enter / Ctrl+Enter: encoded as CSI-u modified Enter ---

test('terminal.js encodes Shift+Enter and Ctrl+Enter as CSI-u, not as a bare newline', () => {
  // A legacy terminal cannot express a modifier on Enter — Enter, Shift+Enter and
  // Ctrl+Enter all collapse to 0x0D. We are a browser and DO have the modifier on
  // the event, so we send the real kitty-protocol encoding. tmux decodes it and our
  // shipped base.conf rewrites it to C-j for apps that only speak legacy.
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  const handlerStart = source.indexOf('attachCustomKeyEventHandler');
  const handlerEnd = source.indexOf('onSelectionChange', handlerStart);
  const handlerBlock = source.substring(handlerStart, handlerEnd);

  assert.ok(handlerBlock.includes("e.key === 'Enter'"),
    'must intercept the Enter key in the custom key handler');
  assert.ok(handlerBlock.includes('\\x1b[13;2u'),
    'Shift+Enter must be sent as CSI-u \\x1b[13;2u');
  assert.ok(handlerBlock.includes('\\x1b[13;5u'),
    'Ctrl+Enter must be sent as CSI-u \\x1b[13;5u');
  assert.ok(handlerBlock.includes('_encodePayload(0x30'),
    'must send via the ttyd 0x30 INPUT frame, same path as onData');

  // Alt+Enter and Cmd+Enter must fall through: Alt+Enter already has a working
  // legacy encoding (ESC CR) that apps rely on, and hijacking it would break them.
  assert.ok(handlerBlock.includes('!e.altKey') && handlerBlock.includes('!e.metaKey'),
    'must NOT intercept Alt+Enter or Cmd+Enter — Alt+Enter has a working legacy encoding');
});

test('Shift+Enter branch calls preventDefault (regression: measured 0a 0d double-send)', () => {
  // Returning false only stops xterm.js's own key handling. Without preventDefault
  // the browser's default action still delivers Enter to xterm's hidden textarea,
  // which re-emits it through onData. Measured in a live muxplex pane before the
  // fix: the app received 0a 0d — our C-j followed by a stray CR, i.e. a newline
  // and then an immediate submit. Both bytes, one keypress.
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  const handlerStart = source.indexOf('attachCustomKeyEventHandler');
  const handlerEnd = source.indexOf('onSelectionChange', handlerStart);
  const handlerBlock = source.substring(handlerStart, handlerEnd);
  const enterIdx = handlerBlock.indexOf("e.key === 'Enter'");
  assert.ok(enterIdx !== -1, 'must have an Enter branch to check');
  const enterBranch = handlerBlock.substring(enterIdx);
  const returnIdx = enterBranch.indexOf('return false');
  assert.ok(returnIdx !== -1, 'Enter branch must return false');
  assert.ok(enterBranch.substring(0, returnIdx).includes('e.preventDefault()'),
    'Enter branch MUST call e.preventDefault() before returning false — ' +
    'otherwise the browser default re-delivers Enter and the app gets 0a 0d');
});

test('terminal.js does NOT send a bare newline for Shift+Enter', () => {
  // Sending '\n' would be indistinguishable from Ctrl+J and would lie to any app
  // that wants to know the real key. The translation to C-j is tmux's job.
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  const handlerStart = source.indexOf('attachCustomKeyEventHandler');
  const handlerEnd = source.indexOf('onSelectionChange', handlerStart);
  const handlerBlock = source.substring(handlerStart, handlerEnd);
  const enterIdx = handlerBlock.indexOf("e.key === 'Enter'");
  assert.ok(enterIdx !== -1, 'must have an Enter branch to check');
  const enterBranch = handlerBlock.substring(enterIdx, enterIdx + 400);
  assert.ok(!enterBranch.includes("_encodePayload(0x30, '\\n')"),
    "must not send a bare '\\n' — that is Ctrl+J, not Shift+Enter");
});

// --- UTF-8 output decoding via TextDecoder ---

test('terminal.js uses TextDecoder to decode UTF-8 WebSocket output before writing to xterm', () => {
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  assert.ok(source.includes('TextDecoder'), 'must create a TextDecoder for UTF-8 output decoding');
  // Find the message handler block for type 0x30 and verify decode() is used
  const msgIdx = source.indexOf('msgType === 0x30');
  assert.ok(msgIdx !== -1, 'must have a type 0x30 output handler');
  const writeBlock = source.substring(msgIdx, msgIdx + 200);
  assert.ok(
    writeBlock.includes('decode') || writeBlock.includes('Decoder'),
    'output handler must decode Uint8Array to string before _term.write() — ' +
    'xterm.js write(Uint8Array) treats bytes as Latin-1 not UTF-8, ' +
    'causing box-drawing chars like ─ (E2 94 80) to render as â',
  );
});

test('message handler writes decoded UTF-8 string (not raw Uint8Array) to xterm', () => {
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  t.openTerminal('test-session');

  globalThis.setTimeout = orig;

  // Simulate receiving a terminal output frame with a box-drawing character: ─ (U+2500)
  // UTF-8 bytes for ─: E2 94 80
  const encoder = new TextEncoder();
  const boxChar = encoder.encode('─');  // [0xE2, 0x94, 0x80]
  const msg = new Uint8Array(1 + boxChar.length);
  msg[0] = 0x30;
  msg.set(boxChar, 1);

  t.fireMessage(msg.buffer);

  assert.strictEqual(t.termWriteMessages.length, 1, 'term.write should be called exactly once');

  const written = t.termWriteMessages[0];
  assert.strictEqual(typeof written, 'string',
    'data written to xterm must be a decoded string, not a Uint8Array — ' +
    'xterm.js write(Uint8Array) interprets bytes as Latin-1 causing garbled box-drawing chars');
  assert.strictEqual(written, '─',
    'decoded output must be the original Unicode character ─, not garbled â bytes');
});

test('OSC 52 clipboard handler UTF-8-decodes the base64 payload (not atob() raw bytes)', () => {
  // Regression for the OSC 52 clipboard bridge (tmux `set-clipboard on` -> browser
  // clipboard). atob() returns a "binary string" -- one JS char per raw byte, i.e.
  // Latin-1 -- so multi-byte UTF-8 characters (box-drawing, bullets, em dashes,
  // emoji) must be re-wrapped into a byte array and decoded with the same
  // TextDecoder used for the primary WebSocket output path (see the decoder
  // test above). Without that, "─" becomes "â", "•" becomes "â¢", etc.
  const t = loadTerminal();

  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;
  t.openTerminal('test-session');
  globalThis.setTimeout = orig;

  const sample = '─── • bullet — dash × times 📊 chart';
  const base64Payload = Buffer.from(sample, 'utf-8').toString('base64');

  t.fireOsc52(base64Payload);

  assert.strictEqual(t.clipboardWrites.length, 1,
    'OSC 52 handler should write exactly one value to the clipboard');
  assert.strictEqual(t.clipboardWrites[0], sample,
    'clipboard text must be the original Unicode string, not atob()\'s Latin-1-mangled bytes');
});

// --- Federation reconnect routing ---

test('terminal.js reconnect uses federation connect path for remote sessions', () => {
  // Regression: connect() inside connectWebSocket() always called local
  // /api/sessions/{name}/connect even when remoteId was set, causing 404 for remote sessions.
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');

  // Find the connect() function inside connectWebSocket
  const connectFnIdx = source.indexOf('function connect()');
  assert.ok(connectFnIdx !== -1, 'must have a connect() function inside connectWebSocket');
  // Extract enough chars to cover the full reconnect block (incl. long comment preamble)
  const connectFn = source.substring(connectFnIdx, connectFnIdx + 1000);

  assert.ok(
    connectFn.includes('remoteId'),
    'reconnect connect() must check remoteId to choose federation vs local routing',
  );
  assert.ok(
    connectFn.includes('/api/federation/'),
    'reconnect connect() must use /api/federation/{remoteId}/connect/{name} for remote sessions',
  );
});

// --- fontSize: must come from server settings, NOT localStorage ---

test('terminal.js createTerminal does not read fontSize from localStorage', () => {
  // Verify createTerminal accepts fontSize as a parameter (no localStorage dependency).
  const source = fs.readFileSync(new URL('../terminal.js', import.meta.url), 'utf8');
  const createTermIdx = source.indexOf('function createTerminal(');
  assert.ok(createTermIdx !== -1, 'createTerminal function must exist');
  // Extract createTerminal body (up to next top-level function)
  const afterStart = source.indexOf('{', createTermIdx);
  let depth = 0;
  let bodyEnd = -1;
  for (let i = afterStart; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) { bodyEnd = i; break; }
    }
  }
  const createTermBody = source.substring(createTermIdx, bodyEnd + 1);
  assert.ok(
    !createTermBody.includes('localStorage'),
    'createTerminal must NOT read from localStorage — fontSize must come from the server settings parameter',
  );
});

test('openTerminal uses passed fontSize to configure xterm.js Terminal constructor', () => {
  // Verify openTerminal forwards fontSize parameter to createTerminal.
  const modulePath = join(__dirname, '..', 'terminal.js');
  delete require.cache[require.resolve(modulePath)];

  let capturedTerminalOptions = null;
  const mockTerm = {
    cols: 80, rows: 24,
    open: () => {},
    onData: () => {},
    onResize: () => {},
    loadAddon: () => {},
    dispose: () => {},
    write: () => {},
    focus: () => {},
    attachCustomKeyEventHandler: () => {},
    getSelection: () => '',
    onSelectionChange: () => {},
    parser: { registerOscHandler: () => {} },
    options: { fontSize: 14 },
  };

  globalThis.WebSocket = class MockWS {
    constructor() { this.readyState = 1; this.binaryType = ''; }
    addEventListener() {}
    close() {}
    send() {}
  };
  globalThis.WebSocket.OPEN = 1;
  globalThis.location = { protocol: 'http:', host: 'localhost' };
  globalThis.document = {
    getElementById: (id) => {
      if (id === 'terminal-container') return { appendChild: () => {}, addEventListener: () => {} };
      if (id === 'reconnect-overlay') return { classList: { add: () => {}, remove: () => {} } };
      if (id === 'reconnect-overlay-text') return { textContent: '' };
      if (id === 'reconnect-overlay-takeover-btn') return { classList: { add: () => {}, remove: () => {} }, onclick: null };
      return null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    createElement: () => ({ style: {}, classList: { add: () => {}, remove: () => {} } }),
  };
  globalThis.window = {
    addEventListener: () => {},
    location: { href: '' },
    innerWidth: 1024,
    Terminal: function Terminal(options) {
      capturedTerminalOptions = options;
      return mockTerm;
    },
    FitAddon: { FitAddon: function FitAddon() { return { fit: () => {} }; } },
  };

  const origSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  require(modulePath);

  globalThis.setTimeout = origSetTimeout;

  const openTerminal = globalThis.window._openTerminal;

  const origST2 = globalThis.setTimeout;
  globalThis.setTimeout = (_fn, _ms) => 0;

  openTerminal('session', '', 20);

  globalThis.setTimeout = origST2;

  assert.ok(capturedTerminalOptions !== null, 'Terminal constructor must have been called');
  assert.strictEqual(
    capturedTerminalOptions.fontSize, 20,
    'openTerminal must pass the fontSize argument to the xterm.js Terminal constructor',
  );
});

// ─── §0/§7 guard: per-session-ttyd session-desync conflict (formerly the ───
// ─── shared-terminal "terminal-claim conflict", sync-groups spec §10.4)  ───
//
// PER_SESSION_TTYD_SPEC.md §7.2: WS 4409 no longer means "another device
// holds the one shared terminal" (that resource no longer exists) -- it
// means "this device asked to attach to a session its own sync group has
// not selected," a state desync rather than a transient conflict. The tests
// below guard both the immediate behavior (no reconnect scheduled) AND the
// structural claim that made 6f44325 an unbounded, OOM-inducing loop: that
// _showTerminalConflictOverlay() cannot itself trigger any further network
// call or reconnect, no matter how many times or from how many call sites
// it fires.

test('close handler with event.code === 4409 schedules NO reconnect', () => {
  const t = loadTerminal();
  const orig = globalThis.setTimeout;
  let reconnectScheduled = false;
  globalThis.setTimeout = (fn, _ms) => { reconnectScheduled = true; return 0; };

  t.openTerminal('my-session', '', 14, 'd-abc123');
  reconnectScheduled = false; // ignore any scheduling during openTerminal itself

  t.fireClose({ code: 4409 });

  globalThis.setTimeout = orig;
  assert.strictEqual(reconnectScheduled, false, '4409 must never schedule a reconnect loop');
});

test('close handler with a normal code still schedules a reconnect (regression guard)', () => {
  const t = loadTerminal();
  const orig = globalThis.setTimeout;
  let reconnectScheduled = false;
  globalThis.setTimeout = (fn, _ms) => { reconnectScheduled = true; return 0; };

  t.openTerminal('my-session', '', 14, 'd-abc123');
  reconnectScheduled = false;

  t.fireClose({ code: 1006 });

  globalThis.setTimeout = orig;
  assert.strictEqual(reconnectScheduled, true, 'a non-4409 close must still schedule a reconnect');
});

test('4409 close never calls fetch (structural loop guard on the direct path)', () => {
  const t = loadTerminal();
  let fetchCallCount = 0;
  globalThis.fetch = async () => {
    fetchCallCount++;
    throw new Error('_showTerminalConflictOverlay must never call fetch -- see its docstring');
  };

  t.openTerminal('my-session', '', 14, 'd-abc123');
  t.fireClose({ code: 4409 });

  assert.strictEqual(fetchCallCount, 0, 'a 4409 close must not trigger any fetch call');
});

test('4409 close shows an honest, non-"reconnecting" overlay message with no Take-over affordance', () => {
  const t = loadTerminal();
  globalThis.fetch = async () => { throw new Error('must not be called'); };

  t.openTerminal('my-session', '', 14, 'd-abc123');
  t.fireClose({ code: 4409 });

  assert.strictEqual(t.overlayVisible(), true, 'overlay must be shown');
  assert.strictEqual(t.takeoverBtnVisible(), false, 'Take-over button must stay hidden -- nothing left to take over');
  // Must not claim a reconnect is in progress, since none is attempted.
  assert.ok(
    !/reconnecting/i.test(t.overlayText()),
    `overlay text must not claim reconnection is happening, got: ${JSON.stringify(t.overlayText())}`,
  );
});

// The direct regression test for the bug: bisected to 6f44325, this reproduced
// as an unbounded promise-microtask loop (escalation POST -> 409/terminal_conflict
// -> _showTerminalConflictOverlay -> unconditional re-POST -> connectWebSocket()
// -> connect() sees _reconnectAttempts still >= 2 -> escalation POST -> ...)
// with no setTimeout and no cap anywhere in the chain, so it ran until the V8
// heap was exhausted (~6 min, SIGABRT) rather than failing an assertion. This
// test drains the microtask queue far beyond what even a single extra
// recursion would require and asserts the fetch count stays flat -- it must
// complete in milliseconds, not minutes, and it must never grow.
test('escalation POST returning 409 terminal_conflict does not spawn an unbounded reconnect loop (regression, bisected 6f44325)', async () => {
  const t = loadTerminal();

  let fetchCallCount = 0;
  globalThis.fetch = async () => {
    fetchCallCount++;
    return {
      status: 409,
      json: async () => ({ terminal_conflict: true, terminal_session: 'other-session' }),
    };
  };

  // Force the escalation path: 2+ failed reconnect attempts + a current session.
  let capturedTimeoutFn = null;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => { capturedTimeoutFn = fn; return 0; };

  t.openTerminal('my-session', '', 14, 'd-abc123');
  // Simulate two prior failed attempts by firing close twice with a normal code.
  t.fireClose({ code: 1006 });
  if (capturedTimeoutFn) { const fn = capturedTimeoutFn; capturedTimeoutFn = null; fn(); } // -> _reconnectAttempts=1
  t.fireClose({ code: 1006 });
  if (capturedTimeoutFn) { const fn = capturedTimeoutFn; capturedTimeoutFn = null; fn(); } // _reconnectAttempts>=2 -> escalation POST fires (in flight)

  globalThis.setTimeout = origSetTimeout;

  // Drain the microtask queue generously -- before the fix, each drained
  // tick fed another escalation fetch via the overlay's unconditional
  // connectWebSocket() re-entry. 200 ticks is far more than one legitimate
  // escalation could ever produce.
  for (let i = 0; i < 200; i++) {
    await Promise.resolve();
  }

  assert.strictEqual(
    fetchCallCount, 1,
    `expected exactly 1 escalation fetch, got ${fetchCallCount} -- unbounded reconnect loop regression`,
  );
});

test('escalation POST returning 409 terminal_conflict shows the overlay and never opens a new WebSocket', async () => {
  const t = loadTerminal();

  globalThis.fetch = async () => ({
    status: 409,
    json: async () => ({ terminal_conflict: true, terminal_session: 'other-session' }),
  });

  let capturedTimeoutFn = null;
  const origSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms) => { capturedTimeoutFn = fn; return 0; };

  t.openTerminal('my-session', '', 14, 'd-abc123');
  t.fireClose({ code: 1006 });
  if (capturedTimeoutFn) { const fn = capturedTimeoutFn; capturedTimeoutFn = null; fn(); }
  t.fireClose({ code: 1006 });
  const wsCountBefore = t.wsConstructedCount();
  if (capturedTimeoutFn) { const fn = capturedTimeoutFn; capturedTimeoutFn = null; fn(); } // escalation POST path

  globalThis.setTimeout = origSetTimeout;

  for (let i = 0; i < 50; i++) {
    await Promise.resolve();
  }

  assert.strictEqual(t.overlayVisible(), true, 'overlay must be shown on 409 terminal_conflict');
  assert.strictEqual(
    t.wsConstructedCount(), wsCountBefore,
    'a 409 terminal_conflict response must never proceed to open a new WebSocket',
  );
});



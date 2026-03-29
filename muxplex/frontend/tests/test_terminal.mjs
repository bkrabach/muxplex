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
  globalThis.document = {
    getElementById: (id) => {
      if (id === 'terminal-container') return { appendChild: () => {} };
      if (id === 'reconnect-overlay') return { classList: { add: () => {}, remove: () => {} } };
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

  require(modulePath);

  // Restore setTimeout
  globalThis.setTimeout = origSetTimeout;

  // Find the most recently created MockWebSocket instance's open handler
  // by pulling it from the instance created during openTerminal() call.
  // We expose a fireOpen() helper so tests can simulate WebSocket connection.
  let lastOpenHandler = null;
  const OrigMockWS = globalThis.WebSocket;
  globalThis.WebSocket = function MockWSTracker(url, protocols) {
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
    fireClose() { if (capturedCloseHandler) capturedCloseHandler(); },
    fireOpen() { if (lastOpenHandler) lastOpenHandler(); },
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
      if (id === 'terminal-container') return { appendChild: () => {} };
      if (id === 'reconnect-overlay') return { classList: { add: () => {}, remove: () => {} } };
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
  assert.ok(written instanceof Uint8Array, 'data written to xterm must be a Uint8Array');
  assert.strictEqual(written[0], 'h'.charCodeAt(0),
    'first byte written must be "h" (0x68), not the type byte 0x30');
  assert.strictEqual(written.length, hello.length,
    'written data length must equal payload length (type byte stripped)');
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
    t.capturedWsUrl.endsWith('/terminal/ws'),
    `WebSocket URL should end with /terminal/ws, got: ${t.capturedWsUrl}`,
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



// Phase 2b implementation — terminal.js
// xterm.js Terminal + FitAddon initialization (task-12)

// ─── Module-level state ───────────────────────────────────────────────────────
let _term = null;
let _fitAddon = null;
let _ws = null;
let _reconnectTimer = null;
let _currentSession = null;
let _vpHandler = null;

// ─── Forward declarations ─────────────────────────────────────────────────────

function connectWebSocket(name, sourceUrl) {
  var url;
  if (sourceUrl) {
    // Remote session: derive WS URL from the source's HTTP URL
    url = sourceUrl.replace(/^http/, 'ws').replace(/\/+$/, '') + '/terminal/ws';
  } else {
    // Local session: same origin
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url = proto + '//' + location.host + '/terminal/ws';
  }
  const reconnectOverlay = document.getElementById('reconnect-overlay');
  const encoder = typeof TextEncoder !== 'undefined' ? new TextEncoder() : null;

  function encodePayload(typeChar, str) {
    // Returns Uint8Array: [typeCharCode, ...utf8bytes]
    var strBytes = encoder ? encoder.encode(str) : new Uint8Array(Array.from(str).map(function(c) { return c.charCodeAt(0); }));
    var payload = new Uint8Array(1 + strBytes.length);
    payload[0] = typeChar;
    payload.set(strBytes, 1);
    return payload;
  }

  // Register terminal event handlers once on this _term instance.
  // These handlers read the module-level _ws at call time (not a captured reference),
  // so they always target the live socket. createTerminal() disposes _term before
  // the next session, removing these handlers automatically.
  if (_term) {
    _term.onData(function(data) {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        // ttyd protocol: input is type 0x30 ('0') + UTF-8 keystroke bytes
        _ws.send(encodePayload(0x30, data));
      }
    });
    _term.onResize(function(size) {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        // ttyd protocol: resize is type 0x31 ('1') + UTF-8 JSON
        _ws.send(encodePayload(0x31, JSON.stringify({ columns: size.cols, rows: size.rows })));
      }
    });
  }

  function connect() {
    // 'tty' subprotocol is REQUIRED — without it ttyd never starts the PTY.
    // Confirmed via raw Python WebSocket tests: ttyd accepts the TCP upgrade but
    // sits completely silent (no child process spawned) when subprotocol is omitted.
    //
    // Local const `ws` captures this specific instance so each handler can check
    // `if (ws !== _ws) return;` (stale guard). Without it, rapid reconnects or
    // session switches cause old handlers to fire on the new _ws while it is still
    // CONNECTING → send error → close → reconnect → infinite loop (Bug 2).
    const ws = new WebSocket(url, ['tty']);
    _ws = ws;
    ws.binaryType = 'arraybuffer';

    ws.addEventListener('open', function() {
      if (ws !== _ws) return; // stale connection — superseded by a newer one, ignore
      if (reconnectOverlay) reconnectOverlay.classList.add('hidden');
      // Step 1: TEXT frame auth handshake — ttyd checks AuthToken before starting PTY
      ws.send(JSON.stringify({ AuthToken: '' }));
      // Step 2: BINARY frame with initial terminal dimensions — [0x31] + JSON({columns, rows})
      if (_term) {
        ws.send(encodePayload(0x31, JSON.stringify({ columns: _term.cols, rows: _term.rows })));
      }
      // Auto-focus the terminal so user can type immediately without clicking
      if (_term) _term.focus();
    });

    ws.addEventListener('message', function(e) {
      if (ws !== _ws) return; // stale connection — superseded by a newer one, ignore
      if (!_term) return;
      if (e.data instanceof ArrayBuffer) {
        var msg = new Uint8Array(e.data);
        if (msg.length < 1) return;
        var msgType = msg[0];
        var payload = msg.slice(1);
        if (msgType === 0x30) {  // '0' = terminal output — write to xterm.js
          _term.write(payload);
        }
        // 0x31 ('1') = window title, 0x32 ('2') = preferences — ignore for now
      } else if (typeof e.data === 'string') {
        _term.write(e.data);  // fallback for text frames
      }
    });

    ws.addEventListener('close', function() {
      if (ws !== _ws) return; // stale connection — don't reconnect for old sockets
      if (!_currentSession) return; // intentional close — don't reconnect
      if (reconnectOverlay) reconnectOverlay.classList.remove('hidden');
      _reconnectTimer = setTimeout(connect, 2000);
    });

    ws.addEventListener('error', function() {
      if (ws !== _ws) return; // stale connection — ignore
      console.warn('tmux-web: WebSocket error on', url);
    });
  }

  connect();
}
function initVisualViewport() {
  if (!window.visualViewport) return;
  if (_vpHandler) window.visualViewport.removeEventListener('resize', _vpHandler);

  _vpHandler = function() {
    if (!_term || !_fitAddon) return;
    var container = document.getElementById('terminal-container');
    if (!container) return;

    // Resize container to fill visual viewport above keyboard
    var headerHeight = 44; // matches --header-height CSS custom property
    var vvh = window.visualViewport.height;
    var termHeight = Math.max(100, vvh - headerHeight);
    container.style.height = termHeight + 'px';

    // Refit xterm.js to new container size
    try { _fitAddon.fit(); } catch (_) {}
  };

  window.visualViewport.addEventListener('resize', _vpHandler);
}

// ─── Terminal creation ────────────────────────────────────────────────────────

/**
 * Create (or recreate) the xterm.js Terminal and FitAddon instances.
 * Disposes any existing terminal first.
 * Stores the results in module-level _term and _fitAddon.
 */
function createTerminal() {
  // Dispose any existing instance
  if (_term) {
    _term.dispose();
    _term = null;
    _fitAddon = null;
  }

  // Read font size from display settings (localStorage key 'muxplex.display')
  var storedFontSize = 14;
  try {
    var raw = localStorage.getItem('muxplex.display');
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed && parsed.fontSize) storedFontSize = parsed.fontSize;
    }
  } catch (_) { /* use default 14 */ }

  const mobile = window.innerWidth < 600; // matches MOBILE_THRESHOLD in app.js
  const fontSize = mobile ? Math.min(storedFontSize, 12) : storedFontSize;

  _term = new window.Terminal({
    cursorBlink: true,
    fontSize: fontSize,
    fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
    theme: {
      background: '#000000',
      foreground: '#c9d1d9',
      cursor: '#58a6ff',
    },
    scrollback: mobile ? 500 : 5000,
    allowProposedApi: true,
  });

  _fitAddon = new window.FitAddon.FitAddon();
  _term.loadAddon(_fitAddon);
}

// ─── Open / close ─────────────────────────────────────────────────────────────

/**
 * Open a terminal session inside #terminal-container.
 * @param {string} sessionName
 * @param {string} [sourceUrl]  Optional HTTP URL of the remote muxplex instance.
 *   When provided, the WebSocket connects to that remote host instead of the
 *   current page origin.
 */
function openTerminal(sessionName, sourceUrl) {
  // Null _currentSession first so any in-flight close handler on the old WS won't
  // schedule a reconnect (it checks `if (!_currentSession) return;`).
  _currentSession = null;

  // Cancel any pending reconnect timer from the previous session.
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }

  // Close existing WebSocket so it can't write to the new terminal (Bug 1 fix).
  if (_ws) {
    _ws.close();
    _ws = null;
  }

  _currentSession = sessionName;

  const container = document.getElementById('terminal-container');
  if (!container) {
    console.warn('[openTerminal] #terminal-container not found');
    return;
  }

  createTerminal();

  _term.open(container);

  if (_fitAddon) {
    // requestAnimationFrame guarantees one full browser layout pass after the flex
    // container becomes visible before fit() measures dimensions.
    // iOS Safari defers flex layout — calling fit() synchronously here gives 0px width
    // → 2-column terminal. The RAF and 500ms fallback fix this race condition.
    // Falls back to immediate execution in Node.js test environments where RAF is absent.
    const fitAddonRef = _fitAddon;
    const raf = typeof requestAnimationFrame !== 'undefined' ? requestAnimationFrame : (fn) => fn();
    raf(function() {
      try { fitAddonRef.fit(); } catch (_) {}
      // 500ms fallback for slow mobile layout engines (e.g. first paint on low-end devices)
      setTimeout(function() {
        try { if (_fitAddon) _fitAddon.fit(); } catch (_) {}
      }, 500);
    });
  }

  connectWebSocket(sessionName, sourceUrl);
  initVisualViewport(); /* defined in Task 14 */
}

/**
 * Close the current terminal session and clean up all resources.
 */
function closeTerminal() {
  if (_vpHandler) {
    if (window.visualViewport) window.visualViewport.removeEventListener('resize', _vpHandler);
    _vpHandler = null;
  }

  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }

  if (_ws) {
    _ws.close();
    _ws = null;
  }

  if (_term) {
    _term.dispose();
    _term = null;
    _fitAddon = null;
  }

  _currentSession = null;
}

// ─── Expose to app.js ─────────────────────────────────────────────────────────
window._openTerminal = openTerminal;
window._closeTerminal = closeTerminal;

// ---------------------------------------------------------------------------
// setTerminalFontSize — live font-size update without reconnecting
// ---------------------------------------------------------------------------

/**
 * Update the terminal font size at runtime without reconnecting.
 * Modifies _term.options.fontSize and refits the terminal to recalculate dimensions.
 * No-op when no terminal is open.
 * @param {number} size - font size in pixels
 */
function setTerminalFontSize(size) {
  if (!_term) return;
  _term.options.fontSize = size;
  if (_fitAddon) {
    try { _fitAddon.fit(); } catch (_) {}
  }
}

window._setTerminalFontSize = setTerminalFontSize;

// ---------------------------------------------------------------------------
// Android touch scroll — rAF-batched WheelEvent dispatch
// Android batches touchmove events irregularly; dispatching one WheelEvent
// per frame (via requestAnimationFrame) smooths over burst delivery.
// UA-gated: iOS and macOS are unaffected (they use mouse wheel natively).
// ---------------------------------------------------------------------------
;(function initAndroidTerminalScroll() {
  if (!/Android/i.test(navigator.userAgent)) return;

  var container = document.getElementById('terminal-container');
  if (!container) return;

  var _lastY      = 0;
  var _accumulated = 0;  // pixel debt between rAF ticks
  var _rafId       = null;
  var SCROLL_PX    = 20; // pixels of touch movement = one WheelEvent dispatch

  function flushScroll() {
    _rafId = null;
    if (!_term || Math.abs(_accumulated) < SCROLL_PX) return;

    var viewport = container.querySelector('.xterm-viewport');
    if (!viewport) { _accumulated = 0; return; }

    // One WheelEvent per frame — dir * 120 = one standard scroll click
    var dir = _accumulated > 0 ? 1 : -1;
    viewport.dispatchEvent(new WheelEvent('wheel', {
      deltaY: dir * 120,
      deltaMode: WheelEvent.DOM_DELTA_PIXEL,
      bubbles: true,
      cancelable: true,
    }));
    _accumulated -= dir * SCROLL_PX;

    // Self-schedule until remainder is consumed
    if (Math.abs(_accumulated) >= SCROLL_PX) {
      _rafId = requestAnimationFrame(flushScroll);
    }
  }

  container.addEventListener('touchstart', function (e) {
    _lastY       = e.touches[0].clientY;
    _accumulated = 0;
    if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
  }, { passive: true });

  container.addEventListener('touchmove', function (e) {
    if (!_term) return;
    e.preventDefault(); // block outer-container scroll

    var y      = e.touches[0].clientY;
    _accumulated += _lastY - y;   // positive = swipe up = newer content
    _lastY = y;

    if (!_rafId) {
      _rafId = requestAnimationFrame(flushScroll);
    }
  }, { passive: false }); // passive:false required for preventDefault

  container.addEventListener('touchend', function () {
    _lastY       = 0;
    _accumulated = 0;
    if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
  }, { passive: true });
})();



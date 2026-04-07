// Phase 2b implementation — terminal.js
// xterm.js Terminal + FitAddon initialization (task-12)

// ─── Module-level state ───────────────────────────────────────────────────────
let _term = null;
let _fitAddon = null;
let _ws = null;
let _reconnectTimer = null;
let _currentSession = null;
let _vpHandler = null;
let _reconnectAttempts = 0; // tracks consecutive failed reconnect attempts for backoff + ttyd respawn
let _searchAddon = null;

// ─── Module-level encoding helpers ──────────────────────────────────────────
// Hoisted here so the clipboard key handler (in openTerminal) can also use them.
const _encoder = typeof TextEncoder !== 'undefined' ? new TextEncoder() : null;

function _encodePayload(typeChar, str) {
  // Returns Uint8Array: [typeCharCode, ...utf8bytes]
  var strBytes = _encoder ? _encoder.encode(str) : new Uint8Array(Array.from(str).map(function(c) { return c.charCodeAt(0); }));
  var payload = new Uint8Array(1 + strBytes.length);
  payload[0] = typeChar;
  payload.set(strBytes, 1);
  return payload;
}

// ─── Clipboard helpers ───────────────────────────────────────────────────────
// Ctrl+Shift+C: copy terminal selection to system clipboard
// Ctrl+Shift+V: paste from system clipboard into terminal

function _copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(function() {});
  } else {
    // Fallback for non-HTTPS contexts (HTTP over LAN)
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch(e) {}
    document.body.removeChild(ta);
  }
}

// ─── Forward declarations ─────────────────────────────────────────────────────

function connectWebSocket(name, remoteId) {
  // Always connect to the same origin — remote sessions route through the
  // federation proxy (ws://host/federation/{remoteId}/terminal/ws) so that
  // no cross-origin WebSocket connections are made from the browser.
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var url;
  if (remoteId) {
    // Remote session via federation proxy — same origin, different path
    url = proto + '//' + location.host + '/federation/' + remoteId + '/terminal/ws';
  } else {
    // Local session: same origin
    url = proto + '//' + location.host + '/terminal/ws';
  }
  const reconnectOverlay = document.getElementById('reconnect-overlay');
  // Use module-level _encodePayload (hoisted above connectWebSocket)
  var encodePayload = _encodePayload;

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

  // _connectWebSocket — creates the WebSocket instance and registers all event handlers.
  // Called directly for normal reconnects (ttyd still alive), or after a brief delay
  // following the /connect POST (ttyd was dead and needed respawning).
  //
  // Local const `ws` captures this specific instance so each handler can check
  // `if (ws !== _ws) return;` (stale guard). Without it, rapid reconnects or
  // session switches cause old handlers to fire on the new _ws while it is still
  // CONNECTING → send error → close → reconnect → infinite loop (Bug 2).
  function _connectWebSocket() {
    // 'tty' subprotocol is REQUIRED — without it ttyd never starts the PTY.
    // Confirmed via raw Python WebSocket tests: ttyd accepts the TCP upgrade but
    // sits completely silent (no child process spawned) when subprotocol is omitted.
    const ws = new WebSocket(url, ['tty']);
    _ws = ws;
    ws.binaryType = 'arraybuffer';

    ws.addEventListener('open', function() {
      if (ws !== _ws) return; // stale connection — superseded by a newer one, ignore
      // NOTE: do NOT reset _reconnectAttempts here. The server-side proxy accepts
      // the WS before confirming ttyd is alive (auto-spawning if needed), but the
      // browser 'open' event fires as soon as the proxy accepts — not when ttyd
      // is actually ready. Resetting here caused the 0→1→0→1 bounce. Instead,
      // reset on first data message (proves ttyd is alive and relaying).
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
      // First data message proves ttyd is alive and relaying — safe to reset counter.
      // We deliberately do NOT reset in the 'open' handler: the server-side proxy
      // accepts the browser WS before ttyd is fully confirmed alive, so 'open'
      // firing alone doesn't mean data will flow. Resetting here prevents the
      // 0→1→0→1 bounce that kept the reconnect loop from escalating to /connect.
      if (_reconnectAttempts > 0) _reconnectAttempts = 0;
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
      _reconnectAttempts++;
      // Exponential backoff: 1s, 2s, 4s, 8s, cap at 15s. Add jitter to avoid thundering herd.
      var delay = Math.min(1000 * Math.pow(2, _reconnectAttempts - 1), 15000);
      delay += Math.random() * 500; // jitter
      _reconnectTimer = setTimeout(connect, delay);
    });

    ws.addEventListener('error', function() {
      if (ws !== _ws) return; // stale connection — ignore
      console.warn('tmux-web: WebSocket error on', url);
    });
  }

  function connect() {
    // After 2 failed WS attempts, ttyd is likely dead (e.g. after service restart).
    // AWAIT the /connect POST before opening the WebSocket — ttyd must be alive first.
    // fetch() includes cookies automatically for same-origin requests so auth is transparent.
    //
    // Critical: this path uses .then() so _connectWebSocket() runs only AFTER the POST
    // response (plus an 800ms settle delay for ttyd to bind its port). The early return
    // prevents falling through to the direct _connectWebSocket() call below.
    if (_reconnectAttempts >= 2 && _currentSession) {
      var connectPath;
      if (remoteId) {
        // Remote session: route through federation proxy
        connectPath = '/api/federation/' + encodeURIComponent(remoteId) + '/connect/' + encodeURIComponent(_currentSession);
      } else {
        // Local session
        connectPath = '/api/sessions/' + encodeURIComponent(_currentSession) + '/connect';
      }
      fetch(connectPath, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
        .catch(function() { return null; })
        .then(function() {
          // Brief delay for ttyd to bind its port after /connect spawns it
          setTimeout(_connectWebSocket, 800);
        });
      return; // Don't fall through — .then() handles the WebSocket creation
    }

    _connectWebSocket();
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

  // Clickable URLs — Ctrl+Click (Windows/Linux) or Cmd+Click (macOS) opens in new tab.
  // xterm-addon-web-links auto-detects URLs and adds hover underlines.
  // Plain click is preserved for normal terminal text selection.
  var WebLinksAddon = window.WebLinksAddon && window.WebLinksAddon.WebLinksAddon;
  if (WebLinksAddon) {
    _term.loadAddon(new WebLinksAddon(function(event, uri) {
      if (event.ctrlKey || event.metaKey) {
        window.open(uri, '_blank');
      }
    }));
  }

  // Search addon — Ctrl+F to find text in terminal buffer
  var SearchAddon = window.SearchAddon && window.SearchAddon.SearchAddon;
  if (SearchAddon) {
    _searchAddon = new SearchAddon();
    _term.loadAddon(_searchAddon);
  }

  // Image addon — inline image rendering (Sixel, iTerm2 IIP, Kitty graphics)
  // Needed for tools like yazi file manager that use graphic protocols
  var ImageAddon = window.ImageAddon && window.ImageAddon.ImageAddon;
  if (ImageAddon) {
    _term.loadAddon(new ImageAddon());
  }
}

// ─── Search helpers ──────────────────────────────────────────────────────────────────────────────────────────────────

function _openSearch() {
  var bar = document.getElementById('terminal-search-bar');
  var input = document.getElementById('terminal-search-input');
  if (bar) {
    bar.classList.remove('hidden');
    if (input) {
      input.focus();
      input.select();
    }
  }
}

function _closeSearch() {
  var bar = document.getElementById('terminal-search-bar');
  if (bar) bar.classList.add('hidden');
  if (_searchAddon) _searchAddon.clearDecorations();
  if (_term) _term.focus();
}

function _searchNext() {
  var input = document.getElementById('terminal-search-input');
  if (input && input.value && _searchAddon) {
    _searchAddon.findNext(input.value);
  }
}

function _searchPrev() {
  var input = document.getElementById('terminal-search-input');
  if (input && input.value && _searchAddon) {
    _searchAddon.findPrevious(input.value);
  }
}

// ─── Open / close ─────────────────────────────────────────────────────────────

/**
 * Open a terminal session inside #terminal-container.
 * @param {string} sessionName
 * @param {string} [remoteId]  Optional federation remote ID.
 *   When provided, the WebSocket connects via the federation proxy path
 *   ws://host/federation/{remoteId}/terminal/ws (same origin, no cross-origin).
 */
function openTerminal(sessionName, remoteId) {
  // Null _currentSession first so any in-flight close handler on the old WS won't
  // schedule a reconnect (it checks `if (!_currentSession) return;`).
  _currentSession = null;
  _reconnectAttempts = 0; // reset backoff on new session open

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

  // --- Clipboard integration ---
  // Ctrl+Shift+C: copy selection to system clipboard (Ctrl+C still sends SIGINT)
  // Ctrl+Shift+V: paste from system clipboard (Ctrl+V still sends literal bytes)
  _term.attachCustomKeyEventHandler(function(e) {
    if (e.type !== 'keydown') return true;

    // Ctrl+Shift+C → copy selection to clipboard
    if (e.ctrlKey && e.shiftKey && (e.key === 'C' || e.code === 'KeyC')) {
      var sel = _term.getSelection();
      if (sel) _copyToClipboard(sel);
      return false;  // prevent xterm from processing
    }

    // Ctrl+Shift+V → paste from clipboard into terminal
    if (e.ctrlKey && e.shiftKey && (e.key === 'V' || e.code === 'KeyV')) {
      e.preventDefault();  // suppress browser native paste event (prevents double-paste via xterm textarea)
      if (navigator.clipboard && navigator.clipboard.readText) {
        navigator.clipboard.readText().then(function(text) {
          if (text && _ws && _ws.readyState === WebSocket.OPEN) {
            _ws.send(_encodePayload(0x30, text));
          }
        }).catch(function() {});
      }
      return false;  // prevent xterm from processing
    }

    // Ctrl+F → open search bar
    if (e.ctrlKey && !e.shiftKey && (e.key === 'f' || e.key === 'F' || e.code === 'KeyF')) {
      _openSearch();
      return false;
    }

    return true;  // let xterm handle all other keys normally
  });

  // Auto-copy: when mouse selection ends, copy to system clipboard.
  // Matches terminal emulator conventions (iTerm2, WezTerm, ttyd native).
  // onSelectionChange fires whenever selection changes — copy if text is selected.
  // When selection is cleared (empty string), we skip the clipboard write.
  _term.onSelectionChange(function() {
    var sel = _term.getSelection();
    if (sel) {
      _copyToClipboard(sel);
    }
  });

  // OSC 52 clipboard integration — bridges tmux clipboard to the browser.
  // When tmux copies text (with `set-clipboard on` in .tmux.conf), it sends
  // an OSC 52 escape sequence to the terminal. xterm.js surfaces this via the
  // parser API. We intercept and write the decoded text to the system clipboard
  // so that: Ctrl+B [ → select → Enter (tmux copy) → system clipboard receives it.
  _term.parser.registerOscHandler(52, function(data) {
    // OSC 52 format: Pc ; Pd — Pc = selection target (c/p/q/s/0-7), Pd = base64 text
    var parts = data.split(';');
    if (parts.length >= 2) {
      try {
        var text = atob(parts[1]);
        _copyToClipboard(text);
      } catch (e) {
        // Invalid base64 or unsupported — silently ignore
      }
    }
    return true;  // Handled — don't pass to xterm's default handler
  });

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

  // Wire search bar buttons + keyboard handlers (idempotent — elements are static)
  var searchInput = document.getElementById('terminal-search-input');
  var searchClose = document.getElementById('terminal-search-close');
  var searchNextBtn = document.getElementById('terminal-search-next');
  var searchPrevBtn = document.getElementById('terminal-search-prev');

  if (searchInput) {
    // Remove old listeners by replacing with cloned element (avoids duplicate handlers on reconnect)
    var newInput = searchInput.cloneNode(true);
    searchInput.parentNode.replaceChild(newInput, searchInput);
    searchInput = newInput;
    searchInput.addEventListener('input', function() {
      if (_searchAddon && searchInput.value) {
        _searchAddon.findNext(searchInput.value);
      } else if (_searchAddon) {
        _searchAddon.clearDecorations();
      }
    });
    searchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (e.shiftKey) _searchPrev(); else _searchNext();
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        _closeSearch();
      }
    });
  }
  if (searchClose) {
    var newClose = searchClose.cloneNode(true);
    searchClose.parentNode.replaceChild(newClose, searchClose);
    newClose.addEventListener('click', _closeSearch);
  }
  if (searchNextBtn) {
    var newNext = searchNextBtn.cloneNode(true);
    searchNextBtn.parentNode.replaceChild(newNext, searchNextBtn);
    newNext.addEventListener('click', _searchNext);
  }
  if (searchPrevBtn) {
    var newPrev = searchPrevBtn.cloneNode(true);
    searchPrevBtn.parentNode.replaceChild(newPrev, searchPrevBtn);
    newPrev.addEventListener('click', _searchPrev);
  }

  connectWebSocket(sessionName, remoteId);
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
    _searchAddon = null;
  }

  _closeSearch();
  _currentSession = null;
  _reconnectAttempts = 0; // reset backoff on intentional close
}

// ─── Expose to app.js ─────────────────────────────────────────────────────────
window._openTerminal = openTerminal;
window._closeTerminal = closeTerminal;
window._openSearch = _openSearch;
window._closeSearch = _closeSearch;

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



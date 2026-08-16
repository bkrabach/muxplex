// Phase 2b implementation — terminal.js
// xterm.js Terminal + FitAddon initialization (task-12)

// ─── Module-level state ───────────────────────────────────────────────────────
let _term = null;
let _fitAddon = null;
let _ws = null;
let _reconnectTimer = null;
let _currentSession = null;
// Handle returned by _trackVisualViewportHeight() (see below) -- module-level
// (not local to initVisualViewport()) so closeTerminal() can tear it down.
let _vpTracker = null;
let _reconnectAttempts = 0; // tracks consecutive failed reconnect attempts for backoff + ttyd respawn
let _searchAddon = null;
let _resizeObserver = null;
// This browser's own device_id (distinct from remoteId, a federation
// concept). Empty string when unknown/unset -- treated as "no device_id",
// matching today's behavior exactly (see the §0 hazard's residual gap:
// a terminal client that supplies none gets no server-side guard).
//
// Named `_termOwnDeviceId` (not `_ownDeviceId`) deliberately: app.js declares
// a top-level `function _ownDeviceId()` (a getter, called at app.js:3536).
// Classic <script> tags share one global scope (see index.html), so a `let
// _ownDeviceId` here previously collided with that function declaration --
// `let` cannot redeclare an existing global binding -- and threw
// `SyntaxError: Identifier '_ownDeviceId' has already been declared` at
// parse time, which meant this entire file (and thus the terminal) never
// ran. See AGENTS.md's "Frontend classic scripts share one global scope"
// note and tests/test_shared_scope.mjs, which guards this class of bug.
let _termOwnDeviceId = '';
// True only while the user has confirmed a takeover after a terminal
// conflict -- reachable via either the WS-side 4409 close (see the
// close-handler comment further down: the server now accept()s then
// close()s with the real code, so this fires against a real server) or
// the HTTP 409 `terminal_conflict` path below -- consumed (reset to
// false) by the next connect attempt so it can never silently apply to an
// unrelated future conflict.
let _pendingTakeover = false;

// ─── Module-level encoding helpers ──────────────────────────────────────────
// Hoisted here so the clipboard key handler (in openTerminal) can also use them.
const _encoder = typeof TextEncoder !== 'undefined' ? new TextEncoder() : null;
// TextDecoder: used to decode UTF-8 bytes received from ttyd before writing to xterm.js.
// xterm.js write(Uint8Array) treats each byte as Latin-1, not UTF-8 — multi-byte characters
// like ─ (U+2500, bytes E2 94 80) render as â (Latin-1 0xE2) without decoding first.
// Matches ttyd's official client pattern: textDecoder.decode(payload) → _term.write(string).
const _decoder = typeof TextDecoder !== 'undefined' ? new TextDecoder() : null;

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
// Ctrl+Shift+V: handled natively by xterm.js (browser paste event → xterm → WebSocket)

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

function connectWebSocket(name, remoteId, ownDeviceId) {
  // Always connect to the same origin — remote sessions route through the
  // federation proxy (ws://host/federation/{remoteId}/terminal/ws) so that
  // no cross-origin WebSocket connections are made from the browser.
  //
  // ?session= (both branches) names the target session directly -- required
  // now that each session has its own ttyd; there is no longer an implicit
  // "the" terminal a session-less WS could fall back to on this browser's
  // behalf. `name` is the session this UI believes it is showing, which is
  // exactly the point: the URL now STATES the target instead of inheriting
  // it from server-side state. See docs/plans/2026-08-02-per-session-ttyd-plan.md §9.1.
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var url;
  if (remoteId) {
    // Remote session via federation proxy — same origin, different path
    url = proto + '//' + location.host + '/federation/' + remoteId + '/terminal/ws'
        + '?session=' + encodeURIComponent(name);
  } else {
    // Local session: same origin. device_id lets the server refuse to relay
    // a session this device did not select (the §0 keystroke-misdirection
    // guard) — omitted entirely when unknown, matching today's behavior.
    // NOTE the separator flips from '?' to '&' here since ?session= is now
    // always present on this branch.
    url = proto + '//' + location.host + '/terminal/ws'
        + '?session=' + encodeURIComponent(name);
    if (ownDeviceId) {
      url += '&device_id=' + encodeURIComponent(ownDeviceId);
    }
  }
  const reconnectOverlay = document.getElementById('reconnect-overlay');
  const reconnectOverlayText = document.getElementById('reconnect-overlay-text');
  const takeoverBtn = document.getElementById('reconnect-overlay-takeover-btn');
  // Use module-level _encodePayload (hoisted above connectWebSocket)
  var encodePayload = _encodePayload;

  // Register terminal event handlers once on this _term instance.
  // These handlers read the module-level _ws at call time (not a captured reference),
  // so they always target the live socket. createTerminal() disposes _term before
  // the next session, removing these handlers automatically.
  //
  // ─── Resize-dispatch throttle (mobile scroll corruption fix) ───────────
  // A burst of term.resize() calls in quick succession -- the mobile
  // viewport animating during keyboard open/close, the browser's own
  // dynamic toolbar hiding/showing while the user scrolls, or the compose
  // bar/dictation transcript auto-growing (app.js's window._refitTerminal
  // calls, all funneled through this same onResize) -- each tells the
  // server to resize the PTY. That makes tmux redraw its ENTIRE pane via a
  // fresh SIGWINCH, addressed with cursor positions computed for whatever
  // size tmux believes is current. If another resize (and thus another
  // full redraw) fires before the client and tmux have settled on the
  // previous one, the newly-arriving redraw can land against a buffer that
  // has since moved to a different size -- corrupting the visible screen:
  // duplicated lines (variable count), or a region that freezes while the
  // rest scrolls around it. Confirmed directly against a real xterm.js
  // Terminal buffer under a synthetic resize-storm harness: an uncapped
  // resize-dispatch rate reproduced rows containing two overlapping "line"
  // labels; throttling the dispatch (below) roughly halved the corrupted
  // rows in the same adversarial test (see tests/test_terminal.mjs).
  //
  // This got dramatically worse for mobile after v0.44.0 (b7186b0) added an
  // immediate, undebounced refit on visualViewport's `scroll` event
  // specifically to avoid a one-frame lag while the keyboard animates --
  // `scroll` fires far more often on mobile (touch-scrolling, on-screen
  // keyboards, dynamic address-bar hide/show) than the rare `resize` a
  // desktop window drag produces, so the SAME bypass floods the server
  // with far more resize requests on mobile than on desktop. See
  // initVisualViewport()'s own height-unchanged guard below for the other
  // half of this fix.
  //
  // The throttle lives HERE (not in initVisualViewport) so it protects
  // every resize source uniformly, not just the viewport one. Local
  // reflow (term.resize() itself, called by FitAddon.fit()) always happens
  // immediately -- no visible lag for the user looking at the terminal.
  // Only the SERVER-bound "resize the PTY" dispatch is throttled: the
  // leading edge of a burst fires instantly (an isolated resize -- the
  // common case -- is completely unaffected), and a rapid follow-up is
  // coalesced into a single trailing send once the burst settles. 50ms
  // matches the ResizeObserver debounce already used elsewhere in this
  // file (below, in openTerminal()).
  var _lastResizeSendAt = 0;
  var _pendingResizeSend = null;
  var RESIZE_SEND_THROTTLE_MS = 50;

  function _sendResizeToServer(cols, rows) {
    _lastResizeSendAt = Date.now();
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      // ttyd protocol: resize is type 0x31 ('1') + UTF-8 JSON
      _ws.send(encodePayload(0x31, JSON.stringify({ columns: cols, rows: rows })));
    }
  }

  if (_term) {
    _term.onData(function(data) {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        // ttyd protocol: input is type 0x30 ('0') + UTF-8 keystroke bytes
        _ws.send(encodePayload(0x30, data));
      }
    });
    _term.onResize(function(size) {
      clearTimeout(_pendingResizeSend);
      if (Date.now() - _lastResizeSendAt >= RESIZE_SEND_THROTTLE_MS) {
        _sendResizeToServer(size.cols, size.rows);
      } else {
        _pendingResizeSend = setTimeout(function() {
          _sendResizeToServer(size.cols, size.rows);
        }, RESIZE_SEND_THROTTLE_MS);
      }
    });
  }

  // _scheduleReconnectRetry — the ONE place that arms the next reconnect
  // attempt: shows the overlay, advances the backoff counter, and arms
  // _reconnectTimer. Both retry-triggering paths in this file call this
  // exact function rather than duplicating its logic:
  //   1. the WebSocket 'close' handler below (the original, always-worked
  //      path — a closed WS always retries this way), and
  //   2. connect()'s /connect-escalation fetch chain, on a genuine network
  //      rejection (see that function's trailing .catch() for why this
  //      matters — a rejected fetch used to dead-end with no scheduled
  //      retry at all, because it is the one reconnect trigger in this file
  //      that does NOT originate from a WebSocket close event).
  // Factored out (rather than duplicated) because both callers need the
  // identical overlay-then-backoff-then-setTimeout(connect, delay) sequence,
  // and a duplicated copy is exactly the kind of drift hazard that let path
  // 2 silently diverge from path 1 in the first place.
  function _scheduleReconnectRetry() {
    if (reconnectOverlay) {
      reconnectOverlay.classList.remove('hidden');
      if (reconnectOverlayText) reconnectOverlayText.textContent = 'Reconnecting…';
      if (takeoverBtn) takeoverBtn.classList.add('hidden');
    }
    _reconnectAttempts++;
    // Exponential backoff: 1s, 2s, 4s, 8s, cap at 15s. Add jitter to avoid thundering herd.
    var delay = Math.min(1000 * Math.pow(2, _reconnectAttempts - 1), 15000);
    delay += Math.random() * 500; // jitter
    _reconnectTimer = setTimeout(connect, delay);
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
          // decode: Uint8Array → UTF-8 string. write(Uint8Array) treats bytes as Latin-1.
          _term.write(_decoder ? _decoder.decode(payload) : payload);
        }
        // 0x31 ('1') = window title, 0x32 ('2') = preferences — ignore for now
      } else if (typeof e.data === 'string') {
        _term.write(e.data);  // fallback for text frames
      }
    });

    ws.addEventListener('close', function(event) {
      if (ws !== _ws) return; // stale connection — don't reconnect for old sockets
      if (!_currentSession) return; // intentional close — don't reconnect
      if (event && event.code === 4409) {
        // Per-session-ttyd guard fired (docs/plans/2026-08-02-per-session-ttyd-plan.md §7.2): this
        // device asked to attach to a session its own sync group has not
        // selected -- a state desync, not a resource conflict (there is no
        // longer a single shared terminal to contend over). Retrying the
        // identical request cannot fix a desync, so this must NOT loop or
        // auto-retry -- show an honest overlay and stop
        // (_showTerminalConflictOverlay's docstring has the full argument).
        //
        // REACHABLE against a real server as of the accept()-then-close()
        // fix in main.py's terminal_ws_proxy (see _accept_then_close() and
        // that function's docstring, and docs/API_SEMANTICS.md's
        // "4409/4404 never reach any real client" incident for the prior
        // bug and its fix). Previously the server closed this WebSocket
        // BEFORE calling accept(), which per ASGI/uvicorn semantics never
        // produces a real WS close frame -- it serialized as a bare HTTP
        // 403 handshake rejection instead, and a real browser's `close`
        // event reports code 1006 for any failed handshake, never 4409.
        // The server now completes the handshake (accept()) before closing
        // with the real code, so a real browser's `close` event genuinely
        // reports 4409 here. tests/test_terminal.mjs still exercises this
        // branch with a synthetic close event (it does not spin up a real
        // server), which remains valid coverage of the client-side logic
        // either way. The HTTP 409 `terminal_conflict` body on the
        // /connect escalation POST below is a second, independent path to
        // the same overlay -- kept as a version-tolerant no-op for an
        // older/federated peer; it's a normal HTTP response, unaffected by
        // any of the WS wire-encoding issue above.
        _showTerminalConflictOverlay(reconnectOverlay, reconnectOverlayText, takeoverBtn, name, remoteId, ownDeviceId);
        return;
      }
      if (event && event.code === 4404) {
        // Unknown device_id, or the target session itself is gone
        // (main.py's terminal_ws_proxy: unknown device_id -> 4404; missing/
        // invalid/unknown target session -> 4404 too). The common real-world
        // cause is a multi-minute sleep: prune_devices(ttl_seconds=300.0)
        // (main.py, run every poll cycle) forgets this device after 5 minutes
        // with no heartbeat, so the device_id this WS was opened with is now
        // unknown to the server -- and every retry up to now has kept
        // reconnecting with that SAME stale id, so without this branch it
        // would retry forever and never heal.
        //
        // app.js already self-heals the identical situation for its own
        // /api/state 404 (see pollActiveState()/restoreState(): "Device aged
        // out of the registry ... Re-register"). Follow that exact pattern
        // here rather than inventing a parallel one: re-register via the
        // SAME sendHeartbeat() app.js's poll loop uses (a plain global call --
        // app.js and terminal.js are classic <script>s sharing one global
        // scope, see index.html) and then fall through to the normal backoff
        // retry below, same as any other close code. `typeof` guard: unit
        // tests load this file without app.js, so sendHeartbeat may not
        // exist in that context -- must not throw either way.
        if (typeof sendHeartbeat === 'function') {
          sendHeartbeat().catch(function() {});
        }
      }
      _scheduleReconnectRetry();
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
        if (ownDeviceId) {
          connectPath += '?device_id=' + encodeURIComponent(ownDeviceId);
          if (_pendingTakeover) connectPath += '&takeover=true';
        }
      }
      _pendingTakeover = false; // consumed -- must not silently apply to a future, unrelated conflict
      fetch(connectPath, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
        .then(function(res) {
          if (res.status === 409) {
            return res.json().catch(function() { return {}; }).then(function(body) {
              if (body && body.terminal_conflict) {
                // Must inspect the status and stop here rather than proceeding
                // to open the WebSocket -- this is the client half of the §0
                // guard, and today the ONLY half that actually fires against
                // a real server: this is a normal HTTP response (unlike the
                // WS-side 4409 close, which never reaches the wire -- see the
                // close handler's comment above). Surface the same honest
                // overlay the WS-side 4409 close handler would show.
                _showTerminalConflictOverlay(reconnectOverlay, reconnectOverlayText, takeoverBtn, name, remoteId, ownDeviceId);
              }
              return null; // never fall through to _connectWebSocket on 409
            });
          }
          return true;
        })
        .then(function(proceed) {
          if (!proceed) return;
          // Brief delay for ttyd to bind its port after /connect spawns it
          setTimeout(_connectWebSocket, 800);
        })
        .catch(function() {
          // fetch() rejects ONLY on a genuine network failure (e.g.
          // ERR_NETWORK_CHANGED, DNS, TLS) -- HTTP error statuses resolve
          // normally, which is why only 409 is branched on above. This used
          // to be `.catch(function() { return null; })` sitting BETWEEN the
          // two .then()s: it mapped the rejection to a resolved `null`, the
          // next .then() saw `proceed = null` and just returned -- no
          // WebSocket was ever created, so no 'close' event could ever fire,
          // and _scheduleReconnectRetry() (the only other place that arms
          // _reconnectTimer) is reached exclusively from a WebSocket's own
          // 'close' handler. The retry chain died permanently and silently
          // right here: exactly the wake-from-sleep window, where Wi-Fi
          // re-association / DHCP renewal / a VPN tunnel re-establishing is
          // still in flight when this fetch fires. Schedule the next
          // attempt the same way a closed WebSocket would, instead of
          // dying quietly. This .catch() is now placed AFTER both .then()s
          // (not between them) so it never intercepts the 409 branch's
          // intentional `return null` -- that path must still stop without
          // retrying (an honest conflict overlay, not a transient failure).
          _scheduleReconnectRetry();
        });
      return; // Don't fall through — the promise chain handles the WebSocket creation
    }

    _connectWebSocket();
  }

  connect();
}

/**
 * Show the reconnect overlay in "session changed elsewhere" mode: an honest
 * message (no Take-over affordance -- with per-session ttyd there is no
 * single shared terminal left to take over), and (critically) NO
 * auto-reconnect loop -- looping here would hammer the server and never
 * recover on its own.
 *
 * Under per-session ttyd, WS 4409 no longer means "another device holds the
 * one shared terminal" (docs/plans/2026-08-02-per-session-ttyd-plan.md §7.2) -- there is no shared
 * resource left to contend over. It means "this device asked to attach to a
 * session its own sync group has not selected": a STATE DESYNC, not a
 * transient conflict. Retrying the identical request cannot resolve a
 * desync -- and re-POSTing /connect for `name` would make it actively
 * worse: connect_session() unconditionally writes
 * `{active_session: name}` for the group (main.py), so "retrying" here
 * would silently overwrite whatever the OTHER device just (correctly)
 * selected, fighting the very guard this code path exists to enforce.
 *
 * So this function does exactly one thing: show the message and stop.
 * No fetch, no reconnect, no timer -- deliberately, so no code path here
 * can ever recurse or loop, no matter how a server responds (this is what
 * makes an unbounded loop structurally impossible rather than merely
 * capped or backed off). Recovery happens through the channel that
 * already exists and is already correct: app.js's poll loop
 * (`followRemoteActiveSession`) picks up the group's real `active_session`
 * on its next tick and calls `openTerminal()` with the right target, which
 * resets all reconnect state cleanly (`_reconnectAttempts = 0`, fresh
 * `_currentSession`, fresh WebSocket). A user action (re-selecting a
 * session) does the same thing immediately. This mirrors the
 * pre-per-session-ttyd behavior, which gated any reconnect here behind a
 * Take-over BUTTON CLICK (a user gesture) rather than firing automatically
 * -- the button is gone (nothing left to take over), and the "no automatic
 * loop" invariant it enforced is preserved by doing nothing instead of
 * substituting an unconditional fetch.
 *
 * INCIDENT (fixed here, bisected to 6f44325): the previous version of this
 * function unconditionally re-POSTed /connect and then called
 * connectWebSocket(name, ...) regardless of that POST's outcome.
 * _reconnectAttempts is module-level and is reset only by a successful
 * data message or a fresh openTerminal() call, so a second 409/4409
 * response re-entered THIS SAME function from connectWebSocket()'s own
 * escalation path (`connect()`'s `_reconnectAttempts >= 2` branch) --
 * with no setTimeout, no cap, and nothing gating the recursion, this was
 * an unbounded promise-microtask loop (overlay -> fetch -> connectWebSocket
 * -> escalation -> overlay -> ...) that ran until the JS heap was
 * exhausted. See tests/test_terminal.mjs's
 * "_showTerminalConflictOverlay never re-enters itself" test.
 *
 * The HTTP 409 `terminal_conflict` branch below (in connect()'s escalation
 * POST) calls this same function and is therefore covered by the same fix.
 * That branch is dead against THIS server (§7.1: the 409 gate is deleted
 * server-side, so /connect can no longer return it) but is kept as a
 * version-tolerant no-op for an older or federated peer that might still
 * send one (AGENTS.md: clients tolerate responses a current server no
 * longer emits).
 */
function _showTerminalConflictOverlay(reconnectOverlay, reconnectOverlayText, takeoverBtn, _name, _remoteId, _ownDeviceId) {
  if (!reconnectOverlay) return;
  reconnectOverlay.classList.remove('hidden');
  if (reconnectOverlayText) {
    reconnectOverlayText.textContent = 'Session changed on another device';
  }
  if (takeoverBtn) takeoverBtn.classList.add('hidden');
  // Deliberately no fetch, no connectWebSocket() call, no setTimeout here.
  // See docstring above.
}
/**
 * Refit the terminal to its current container size. Exposed on `window` as
 * `_refitTerminal` so other classic scripts (app.js's compose bar) can
 * request a refit after something OTHER than a visualViewport event changes
 * the terminal's available space -- e.g. showing/hiding the compose bar,
 * or its textarea auto-growing. A no-op when no terminal is open.
 */
function _termRefit() {
  if (!_fitAddon) return;
  try { _fitAddon.fit(); } catch (_) {}
}

window._refitTerminal = _termRefit;

/**
 * Generic core of muxplex's visualViewport-tracking mechanism: mirror
 * `window.visualViewport.height` into a CSS custom property (`cssVarName`)
 * on `el`, with two guards proven out here first for the terminal and now
 * shared by every consumer:
 *
 * HEIGHT-UNCHANGED GUARD (mobile scroll corruption fix, v0.47.2): `scroll`
 * fires far more often than the viewport genuinely changes height -- it
 * also fires on ordinary content panning while the keyboard/browser
 * toolbar is already settled (mobile-only; a desktop `resize` from a
 * window drag has no such noisy sibling). Every one of those events used
 * to still run an unconditional CSS write + refit, which -- via FitAddon's
 * own fit() -> term.resize() -- could dispatch a PTY resize to the server
 * on every single scroll tick during a touch-scroll gesture. See
 * connectWebSocket()'s _sendResizeToServer/RESIZE_SEND_THROTTLE_MS comment
 * for the full mechanism (tmux SIGWINCH-redraw races) and
 * tests/test_terminal.mjs for the reproduction. Bailing out here when the
 * height genuinely hasn't changed removes the large majority of that
 * traffic at the source, for free -- it is a strict no-op in the case that
 * matters (a real height change still applies immediately, exactly as
 * before).
 *
 * PER-FRAME onChange COALESCING (mobile scroll SMOOTHNESS, v0.47.10): the
 * height-unchanged guard above only filters out no-op events -- a real
 * mobile toolbar-collapse/keyboard-open animation still fires several
 * GENUINELY different heights in quick succession (one per animation
 * tick). The CSS write itself stays immediate and unconditional on every
 * genuine change (cheap; keeps whatever the property drives glued to the
 * viewport in real time with zero added lag) -- only the caller-supplied
 * `onChange` (the expensive part -- for the terminal, FitAddon.fit(),
 * which forces a synchronous layout read right after the CSS write just
 * invalidated it) is coalesced to at most once per rendered animation
 * frame via `requestAnimationFrame`, using the same schedule-if-not-
 * already-scheduled pattern as `initMobileTerminalScroll()`'s rAF-batched
 * wheel dispatch further down this file. Any events that arrive before the
 * queued frame runs are absorbed into that single call. Falls back to an
 * immediate, synchronous call when `requestAnimationFrame` is unavailable
 * -- the same fallback idiom already used in createTerminal() below --
 * which keeps this byte-for-byte identical to the pre-fix behavior in the
 * Node test environment (no rAF there), so the v0.47.2 corruption
 * regression tests are unaffected.
 *
 * ORIGIN: both guards were originally inline in this file's own
 * initVisualViewport() (below), written for its sole consumer at the time
 * (#view-expanded / --app-viewport-height, refitting xterm.js). Extracted
 * into this standalone, terminal-agnostic function once chat.js's agent
 * panel became a second, independent consumer of the exact same technique
 * (--agent-panel-visual-h on #chat-panel, with no refit-equivalent
 * `onChange` needed -- see muxplex-m3n). Both now share this ONE
 * implementation rather than each carrying its own copy of the same two
 * guards -- exposed on `window` (like `_refitTerminal` above) so chat.js,
 * a separate classic <script>, can call it directly.
 *
 * `el` may be null/absent at call time (e.g. the target element doesn't
 * exist in the caller's view yet) -- listeners are still registered so a
 * later genuine change is tracked correctly; until then the write step is
 * simply a no-op, exactly like the pre-extraction inline handler's own
 * `if (!expandedView) return;` guard.
 *
 * TOP-OFFSET CORRECTION: the written value is `visualViewport.height` MINUS
 * `el`'s own current `getBoundingClientRect().top`, not the raw height.
 * `#view-expanded` (this file's own consumer) happens to always sit at
 * viewport y=0, so for it this is a no-op adjustment (top is always 0) --
 * but a second consumer is not guaranteed that position. chat.js's
 * `#chat-panel` does NOT start at y=0 (it is nested below the app's own
 * page-level header), and setting its height to the RAW visualViewport
 * height overshoots the visible region by exactly that header's rendered
 * height -- a real, measured regression caught via simulated-keyboard
 * browser verification while building the chat.js consumer (muxplex-m3n):
 * the panel (and Send with it) extended past the actually-visible area by
 * the header's height, in BOTH the no-keyboard and keyboard-open cases.
 * Subtracting the element's own top makes the written value mean "space
 * remaining below this element's current top edge, in the visible region"
 * -- correct for a consumer anchored at y=0 (top=0, no change) and for one
 * that is not (top>0, correctly shrunk). Guarded by a `typeof` check so
 * environments where `el` has no `getBoundingClientRect` (this file's own
 * unit-test mocks, tests/test_terminal.mjs) fall back to top=0 -- i.e. the
 * exact pre-existing raw-height behavior, unchanged.
 *
 * Registers `resize`/`scroll` listeners immediately if
 * `window.visualViewport` exists, and seeds the property once,
 * synchronously, before returning -- callers never wait for the first
 * event. Returns `null` (no listeners registered, nothing to tear down)
 * when `window.visualViewport` is unavailable, so the caller's plain CSS
 * `var(..., fallback)` governs untouched.
 *
 * @returns {{teardown: function(): void}|null}
 */
function _trackVisualViewportHeight(el, cssVarName, onChange) {
  if (!window.visualViewport) return null;
  var lastHeight = null;
  var rafId = null;

  function scheduleOnChange() {
    if (!onChange) return;
    if (typeof requestAnimationFrame === 'undefined') {
      onChange();
      return;
    }
    if (rafId !== null) return; // already queued for the next frame
    rafId = requestAnimationFrame(function() {
      rafId = null;
      onChange();
    });
  }

  function handler() {
    if (!el) return;
    var top = (typeof el.getBoundingClientRect === 'function')
      ? el.getBoundingClientRect().top
      : 0;
    var h = Math.max(0, window.visualViewport.height - top);
    if (h === lastHeight) return; // no genuine change -- true no-op, see docstring above
    lastHeight = h;
    el.style.setProperty(cssVarName, h + 'px');
    scheduleOnChange();
  }

  window.visualViewport.addEventListener('resize', handler);
  window.visualViewport.addEventListener('scroll', handler);
  handler(); // seed the initial value immediately, don't wait for the first event

  function teardown() {
    window.visualViewport.removeEventListener('resize', handler);
    window.visualViewport.removeEventListener('scroll', handler);
    if (rafId !== null) {
      if (typeof cancelAnimationFrame !== 'undefined') cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (el) el.style.removeProperty(cssVarName);
  }

  return { teardown: teardown };
}

window._trackVisualViewportHeight = _trackVisualViewportHeight;

/**
 * Track the visual viewport (the space actually visible above an on-screen
 * keyboard, search engines, etc.) for the terminal view, via
 * _trackVisualViewportHeight() above: mirrors its height into
 * `--app-viewport-height` (consumed by `#view-expanded` in style.css) and
 * refits xterm.js, coalesced to at most once per animation frame.
 *
 * REWORKED (was: `container.style.height = (vvh - headerHeight) + 'px'`).
 * That approach hardcoded a single subtracted height (the 44px header) and
 * had no way to also account for whatever ELSE is stacked in the same
 * column -- #terminal-search-bar when open, and now #compose-bar when
 * shown. The old code silently mis-sized the terminal whenever the search
 * bar was visible (a pre-existing bug, not introduced here) and would have
 * mis-sized it again for the compose bar had this rework not happened.
 *
 * The fix moves the measurement one level UP: instead of computing the
 * terminal's own height, set `--app-viewport-height` to the visual
 * viewport's height. Flexbox then does the subtraction for every sibling
 * in the column (.expanded-header, .terminal-search-bar,
 * #terminal-container, #compose-bar) automatically and correctly, no
 * matter how many of them are visible or how tall each one is.
 * #terminal-container itself gets NO inline height at all any more -- it
 * is sized purely by its `flex: 1` rule plus its siblings' actual
 * rendered heights.
 */
function initVisualViewport() {
  // Tear down any tracker from a previous session before creating a new
  // one -- mirrors the old implementation's own remove-then-recreate
  // guard, now via the shared teardown() rather than manually managing
  // individual listener/rAF handles.
  if (_vpTracker) {
    _vpTracker.teardown();
    _vpTracker = null;
  }
  var expandedView = document.getElementById('view-expanded');
  _vpTracker = _trackVisualViewportHeight(expandedView, '--app-viewport-height', _termRefit);
}

// ─── Terminal creation ────────────────────────────────────────────────────────

/**
 * Create (or recreate) the xterm.js Terminal and FitAddon instances.
 * Disposes any existing terminal first.
 * Stores the results in module-level _term and _fitAddon.
 * @param {number} [fontSize=14] - font size in pixels, from server display settings
 */
function createTerminal(fontSize) {
  // Dispose any existing instance
  if (_term) {
    _term.dispose();
    _term = null;
    _fitAddon = null;
  }

  // Use the fontSize passed from app.js (getDisplaySettings().fontSize), defaulting to 14.
  var storedFontSize = (typeof fontSize === 'number' && fontSize > 0) ? fontSize : 14;

  const mobile = window.innerWidth < 600; // matches MOBILE_THRESHOLD in app.js
  const effectiveFontSize = mobile ? Math.min(storedFontSize, 12) : storedFontSize;

  _term = new window.Terminal({
    cursorBlink: true,
    fontSize: effectiveFontSize,
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
function openTerminal(sessionName, remoteId, fontSize, ownDeviceId) {
  // Null _currentSession first so any in-flight close handler on the old WS won't
  // schedule a reconnect (it checks `if (!_currentSession) return;`).
  _currentSession = null;
  _reconnectAttempts = 0; // reset backoff on new session open
  _termOwnDeviceId = ownDeviceId || '';
  _pendingTakeover = false;

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

  createTerminal(fontSize);

  _term.open(container);

  // --- Auto-refit on container resize (sidebar toggle, etc.) ---
  // xterm.js FitAddon only resizes on explicit fit() calls. A ResizeObserver
  // on the container handles ALL layout changes: sidebar toggle, window resize,
  // and any future CSS geometry change. Debounced to coalesce rapid events
  // (e.g. during CSS transition animation frames).
  if (_resizeObserver) { _resizeObserver.disconnect(); _resizeObserver = null; }
  if (typeof ResizeObserver !== 'undefined') {
    var _roTimer = null;
    _resizeObserver = new ResizeObserver(function() {
      clearTimeout(_roTimer);
      _roTimer = setTimeout(function() {
        if (_fitAddon) try { _fitAddon.fit(); } catch (_) {}
      }, 50);
    });
    _resizeObserver.observe(container);
  }

  // --- Clipboard integration ---
  // Copy: Ctrl+Shift+C intercepts and copies selection to system clipboard
  // Paste: handled natively by xterm.js (browser paste event → hidden textarea → onData → WebSocket)
  //   Cmd+V (macOS) and Ctrl+Shift+V (Linux) both trigger native browser paste events
  _term.attachCustomKeyEventHandler(function(e) {
    if (e.type !== 'keydown') return true;

    // Ctrl+Shift+C → copy selection to clipboard
    if (e.ctrlKey && e.shiftKey && (e.key === 'C' || e.code === 'KeyC')) {
      var sel = _term.getSelection();
      if (sel) _copyToClipboard(sel);
      return false;  // prevent xterm from processing
    }

    // Ctrl+F → open search bar
    if (e.ctrlKey && !e.shiftKey && (e.key === 'f' || e.key === 'F' || e.code === 'KeyF')) {
      _openSearch();
      return false;
    }

    // Ctrl+Shift+Enter is reserved for the compose bar's follow-up-queue
    // shortcut (app.js's document-level _followupsQueueKeydown listener) --
    // never encode or forward this exact combo into the pty, regardless of
    // which element currently has focus. This carves out ZERO existing
    // terminal capability: the branch below already collapses Shift+Enter
    // and Ctrl+Shift+Enter to the IDENTICAL CSI-u sequence (`e.shiftKey ?
    // '\x1b[13;2u' : ...` picks the shift-encoded form whenever shiftKey is
    // true, irrespective of ctrlKey) -- so a user who wants that encoded
    // Shift+Enter still gets it via plain Shift+Enter, and plain Ctrl+Enter
    // (below) is untouched. preventDefault() only suppresses the
    // bare-newline browser default (stops it reaching the shell as a stray
    // Enter); it does NOT stop propagation, so the same native keydown
    // event still bubbles to `document`, where app.js's queue shortcut acts
    // on it -- this is what makes the queue gesture work no matter where
    // focus is (terminal or compose textarea). See AGENTS.md's follow-up
    // queue section / docs/plans/2026-08-05-per-session-followup-queue-plan.md §9.2.
    if (e.key === 'Enter' && e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      return false;
    }

    // Shift+Enter / Ctrl+Enter (alone) → encode as a real modified Enter
    // (kitty keyboard protocol, CSI-u). A legacy terminal CANNOT express
    // these: the encoding has no field for a modifier on Enter, so Enter,
    // Shift+Enter and Ctrl+Enter all collapse to 0x0D and every chat TUI has
    // to fall back to Ctrl+J. We are a browser, not a legacy terminal -- the
    // modifier is right there on the event, so send it faithfully instead of
    // throwing it away.
    //
    // Downstream, tmux decodes CSI-u and our shipped config rewrites these to
    // C-j for apps that only speak the legacy encoding (tmux_templates/
    // base.conf). Apps that understand CSI-u natively get a true Shift+Enter.
    //
    // Deliberately NOT sending a bare '\n' here: that would be indistinguishable
    // from Ctrl+J and would lie to any app that wants the real key.
    if (e.key === 'Enter' && !e.altKey && !e.metaKey && (e.shiftKey || e.ctrlKey)) {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(_encodePayload(0x30, e.shiftKey ? '\x1b[13;2u' : '\x1b[13;5u'));
      }
      // preventDefault is load-bearing, NOT belt-and-braces. Returning false only
      // stops xterm.js's own key handling -- it does not suppress the browser's
      // default action, so Enter still reaches xterm's hidden textarea and arrives
      // a second time through onData. Measured before this line existed: the app
      // received 0a 0d -- our C-j AND a stray CR, i.e. a newline immediately
      // followed by a submit. The other branches in this handler (Ctrl+Shift+C,
      // Ctrl+F) never hit this because those chords produce no text input.
      e.preventDefault();
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
        // atob() returns a "binary string" — one JS char per decoded byte
        // (effectively Latin-1). tmux's OSC 52 payload is UTF-8 bytes, so
        // multi-byte characters (box-drawing, bullets, em dashes, emoji)
        // must be re-wrapped into a byte array and passed through the same
        // TextDecoder used for the WebSocket output path — otherwise they
        // decode as mojibake (e.g. "─" becomes "â") even though the primary
        // terminal-output path (see _decoder.decode(payload) above) is
        // already UTF-8 correct.
        var binary = atob(parts[1]);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) {
          bytes[i] = binary.charCodeAt(i);
        }
        var text = _decoder ? _decoder.decode(bytes) : binary;
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

  // --- Right-click context menu ---
  // Suppress the browser context menu on plain right-click inside the terminal
  // so tmux's own menu (when `set -g mouse on`) isn't covered by the browser's.
  // Shift+RMB and Ctrl+RMB still open the browser context menu as escape hatches.
  container.addEventListener('contextmenu', function(e) {
    if (e.shiftKey || e.ctrlKey || e.metaKey) return; // let modified clicks through
    e.preventDefault();
  });

  connectWebSocket(sessionName, remoteId, ownDeviceId);
  initVisualViewport(); /* defined in Task 14 */
}

/**
 * Close the current terminal session and clean up all resources.
 */
function closeTerminal() {
  // Tear down the visualViewport tracker (see _trackVisualViewportHeight):
  // removes the resize/scroll listeners, cancels a still-pending coalesced
  // refit so a stray callback from THIS session never fires an extra
  // (harmless but wasted) fit() against whatever terminal/session comes
  // next, and clears --app-viewport-height -- #view-expanded falls back to
  // its CSS default (100dvh) the moment it's set again, but an explicit
  // clear here means the overview view (which never reads this property,
  // but shares no ambiguity either way) never inherits a stale pixel value
  // from the last session's keyboard state.
  if (_vpTracker) {
    _vpTracker.teardown();
    _vpTracker = null;
  }

  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }

  if (_ws) {
    _ws.close();
    _ws = null;
  }

  if (_resizeObserver) { _resizeObserver.disconnect(); _resizeObserver = null; }

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
// Mobile touch scroll — rAF-batched WheelEvent dispatch
// Mobile devices batch touchmove events irregularly; dispatching one WheelEvent
// per frame (via requestAnimationFrame) smooths over burst delivery.
// Applies to Android, iOS, and iPadOS touch devices.
// ---------------------------------------------------------------------------
;(function initMobileTerminalScroll() {
  var isTouchDevice = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ||
                      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (!isTouchDevice) return;

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



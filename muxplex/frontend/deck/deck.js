// muxplex soft deck — deck.js
//
// Vanilla JS, no build step, no framework, no dependency on app.js. Consumes
// GET /api/view + GET /api/sessions, POST /api/sessions/{name}/connect, and
// PATCH /api/state — the same server-resolved semantics the PWA and
// muxplex-deck sidecar use (AGENTS.md: "clients must not re-derive view
// membership, the bell predicate, or sort order"). This file never
// recomputes needs_attention or view filtering itself.
//
// v1 scope: switch session, change view, toggle-last, liveness/staleness,
// wake lock, PWA install. No create/delete/clear-bell (SOFT_DECK_DESIGN.md
// §7.1 / DESIGN_RESPONSIVE.md §2.3 — no irreversible action is reachable
// from a single tap on this surface).

// ─── Pure logic (exported for node --test; no DOM dependency) ───────────

var POLL_INTERVAL_MS = 2000;
var STALE_WARN_MS = 6000; // ~3 poll cycles
var STALE_ERR_MS = 30000;
var PENDING_TIMEOUT_MS = 2500;
var FAILED_MIN_VISIBLE_MS = 3000;
var PRESS_MIN_HOLD_MS = 120;

/**
 * Classify liveness/staleness from the age of the last successful poll.
 * Mirrors DESIGN_LAYOUT.md §6's three-tier table. `lastOk` is a boolean —
 * a request in flight or errored counts as "not ok" independent of age.
 * @param {number} ageMs - milliseconds since the last successful poll
 * @param {boolean} lastOk - whether the most recent poll attempt succeeded
 * @returns {'fresh'|'warn'|'err'}
 */
function classifyStaleness(ageMs, lastOk) {
  if (!lastOk) return 'err';
  if (ageMs > STALE_ERR_MS) return 'err';
  if (ageMs > STALE_WARN_MS) return 'warn';
  return 'fresh';
}

/**
 * Format an age in milliseconds as a short relative string for the header
 * ("18s") or a tile's STATE band ("4m"). Mirrors app.js's formatTimestamp
 * rounding but takes an age directly (deck.js's callers already have one).
 * @param {number} ageMs
 * @returns {string}
 */
function formatAge(ageMs) {
  if (ageMs == null || ageMs < 0) return '';
  var s = Math.floor(ageMs / 1000);
  if (s < 60) return s + 's';
  var m = Math.floor(s / 60);
  if (m < 60) return m + 'm';
  var h = Math.floor(m / 60);
  if (h < 24) return h + 'h';
  var d = Math.floor(h / 24);
  return d + 'd';
}

/**
 * Format a `last_activity_at` unix-seconds timestamp (may be null/undefined)
 * as a tile STATE line. Absent activity renders as an em dash — the band is
 * still reserved (DESIGN_TILE.md §2), never blank-collapsed.
 * @param {number|null|undefined} lastActivityAt
 * @param {number} [nowMs] - injectable for tests
 * @returns {string}
 */
function formatLastActivity(lastActivityAt, nowMs) {
  if (lastActivityAt == null) return '\u2014';
  var now = nowMs != null ? nowMs : Date.now();
  var ageMs = now - lastActivityAt * 1000;
  if (ageMs < 10000) return 'now';
  return formatAge(ageMs);
}

/**
 * Track the "previous active session" for the toggle-last chip. Called once
 * per poll with the prior known active name and the freshly-resolved one.
 * Mirrors the physical deck's `toggle_last` semantics: the session that was
 * active immediately before the current one. Returns the (possibly
 * unchanged) previous-active value to store.
 * @param {string|null} priorActive - what activeName was before this poll
 * @param {string|null} priorPrevious - the previously-tracked previous value
 * @param {string|null} newActive - what the server now reports as active
 * @returns {string|null}
 */
function nextPreviousActive(priorActive, priorPrevious, newActive) {
  if (newActive === priorActive) return priorPrevious;
  if (priorActive == null) return priorPrevious;
  return priorActive;
}

/**
 * Decide whether the attention strip should be reserved. Rule: reserved
 * only when the grid currently overflows its viewport (DESIGN_LAYOUT.md
 * §1.2) — never merely because attention exists. Pure wrapper around a
 * scrollHeight/clientHeight comparison so the decision is unit-testable.
 * @param {number} scrollHeight
 * @param {number} clientHeight
 * @returns {boolean}
 */
function gridOverflows(scrollHeight, clientHeight) {
  return scrollHeight > clientHeight;
}

/**
 * Compute the last N non-empty-trimmed lines of a pane snapshot for the
 * tile preview field, newest line last (bottom-anchored — DESIGN_TILE.md
 * §2: "terminal output's newest line is last"). Does not trim internal
 * whitespace inside a line (column structure is the point).
 * @param {string} snapshot
 * @param {number} maxLines
 * @returns {string}
 */
function previewLines(snapshot, maxLines) {
  if (!snapshot) return '';
  var lines = snapshot.split('\n');
  // Drop trailing fully-blank lines (common with a prompt-only pane) so the
  // budget isn't spent on nothing, but preserve blank lines *between*
  // real content.
  while (lines.length > 0 && lines[lines.length - 1].trim() === '') {
    lines.pop();
  }
  if (lines.length <= maxLines) return lines.join('\n');
  return lines.slice(lines.length - maxLines).join('\n');
}

/**
 * Build the sorted, deduplicated list of session names currently in the
 * attention strip: sessions with needs_attention true, in the order the
 * server returned them (server enumeration order, never re-sorted here —
 * AGENTS.md's needs-attention predicate is authoritative and already
 * applied server-side; this just filters).
 * @param {Array<object>} sessions - GET /api/view's `sessions` array
 * @returns {Array<object>}
 */
function attentionSessions(sessions) {
  return (sessions || []).filter(function (s) {
    return !!s.needs_attention;
  });
}

/**
 * Decide the tile's visual state class given (a) authoritative server data
 * for this tile and (b) locally-tracked optimistic/failed overlays. Server
 * truth wins UNLESS a not-yet-expired local override says otherwise, per
 * the two "must never lie" rules in the brief:
 *   - a failed marker must survive at least one poll cycle (DESIGN_TILE.md
 *     §6.2's "poll-proof" requirement)
 *   - a pending marker must never be indistinguishable from confirmed
 *     active until the server confirms it (resolution #5 in deck.css)
 * @param {object} params
 * @param {boolean} params.serverActive
 * @param {string|null} params.pendingName - name currently in the pending window, or null
 * @param {string} params.tileName
 * @param {number|null} params.failedUntil - epoch ms the failed marker expires, or null
 * @param {number} params.nowMs
 * @returns {'active'|'pending'|'failed'|'idle'}
 */
function tileVisualState(params) {
  var failedUntil = params.failedUntil;
  if (failedUntil != null && params.nowMs < failedUntil) {
    return 'failed';
  }
  if (params.pendingName != null && params.pendingName === params.tileName) {
    return 'pending';
  }
  if (params.serverActive) return 'active';
  return 'idle';
}

// ─── Fullscreen orientation lock ───────────────────────────────────────
//
// User request: game-like forced landscape, not a manual unlock/rotate/
// relock dance every time the deck opens. `screen.orientation.lock()`
// only succeeds when the manifest's `display` mode is `fullscreen`
// (Chromium's ScreenOrientationProvider rejects it with
// FULLSCREEN_REQUIRED under `standalone` -- see manifest.json).
// Belt-and-braces: unsupported browsers/form factors and rejected locks
// (e.g. desktop, or no user-gesture context yet) are swallowed quietly --
// this must never be able to break the page. Defined at module scope
// (not inside the DOM-wiring IIFE below) so it is both callable from
// boot() and exportable for node --test, matching the pure-logic
// functions above.

function lockLandscapeOrientation() {
  if (
    typeof screen === 'undefined' ||
    !screen.orientation ||
    typeof screen.orientation.lock !== 'function'
  ) {
    return;
  }
  screen.orientation.lock('landscape').catch(function () {
    // Expected in plenty of legitimate cases (desktop browser, no
    // fullscreen display mode yet, no transient user activation) -- never
    // let this reject into an unhandled rejection or break boot.
  });
}

// ─── Service worker registration ───────────────────────────────────────
//
// Not required for the "Install app" menu item since Chrome 108, but
// Chrome's own installability docs still require a `fetch` handler for
// the automatic install prompt/banner to appear. sw.js deliberately
// caches nothing -- see its header comment.

function registerServiceWorker() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
    return;
  }
  navigator.serviceWorker.register('/deck/sw.js').catch(function () {
    // Non-fatal -- the deck works the same with or without a SW.
  });
}

// ─── DOM wiring (browser only) ───────────────────────────────────────────

if (typeof document !== 'undefined') {
  (function () {
    'use strict';

    var PREVIEW_LINES_REGULAR = 5;

    // ── State ──
    var sessions = []; // last-known GET /api/view sessions[], server order
    var snapshots = {}; // name -> pane text, from GET /api/sessions
    var viewName = 'all';
    var viewsList = ['all'];
    var activeName = null;
    var previousActiveName = null;
    var lastPollOkAt = null; // epoch ms of last successful poll
    var lastPollFailed = false;
    var pendingName = null; // optimistic tap in flight
    var pendingSince = null;
    var failedByName = {}; // name -> epoch ms the FAILED marker expires
    var pollTimer = null;
    var wakeSentinel = null;
    var wakeState = 'unsupported'; // held | refused | off | unsupported
    var tileEls = {}; // name -> tile <button> element (stable across polls)

    // ── DOM refs ──
    var root = document.getElementById('deck-root');
    var body = document.getElementById('deck-body');
    var grid = document.getElementById('deck-grid');
    var emptyEl = document.getElementById('deck-empty');
    var loadingEl = document.getElementById('deck-loading');
    var livenessDot = document.getElementById('liveness-dot');
    var stalenessAge = document.getElementById('staleness-age');
    var viewButton = document.getElementById('view-button');
    var viewButtonLabel = document.getElementById('view-button-label');
    var wakeIndicator = document.getElementById('wake-indicator');
    var toggleLastChip = document.getElementById('toggle-last-chip');
    var attentionStrip = document.getElementById('attention-strip');
    var viewSheetBackdrop = document.getElementById('view-sheet-backdrop');
    var viewSheet = document.getElementById('view-sheet');
    var toast = document.getElementById('deck-toast');
    var toastTimer = null;

    // ── Fetch helpers ──
    // Deliberately NOT a generic `api()` clone of app.js's helper — kept
    // isolated per SOFT_DECK_DESIGN.md §7 ("stays out of app.js"; a broken
    // deck must never share a failure mode with the terminal client).
    function getJSON(path) {
      return fetch(path, { headers: { Accept: 'application/json' } }).then(function (res) {
        if (!res.ok) {
          var err = new Error('HTTP ' + res.status);
          err.status = res.status;
          throw err;
        }
        return res.json();
      });
    }

    function postJSON(path, body, signal) {
      return fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
        signal: signal,
      }).then(function (res) {
        if (!res.ok) {
          var err = new Error('HTTP ' + res.status);
          err.status = res.status;
          throw err;
        }
        return res.json();
      });
    }

    function patchJSON(path, body) {
      return fetch(path, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      }).then(function (res) {
        if (!res.ok) {
          var err = new Error('HTTP ' + res.status);
          err.status = res.status;
          throw err;
        }
        return res.json();
      });
    }

    // ── Polling ──

    function poll() {
      var viewReq = getJSON('/api/view');
      var sessReq = getJSON('/api/sessions');
      return Promise.all([viewReq, sessReq])
        .then(function (results) {
          var viewData = results[0];
          var sessData = results[1];
          lastPollOkAt = Date.now();
          lastPollFailed = false;

          viewName = viewData.view;
          viewsList = viewData.views || ['all'];

          var newActive = null;
          for (var i = 0; i < viewData.sessions.length; i++) {
            if (viewData.sessions[i].active) {
              newActive = viewData.sessions[i].name;
              break;
            }
          }
          previousActiveName = nextPreviousActive(activeName, previousActiveName, newActive);
          activeName = newActive;

          sessions = viewData.sessions;
          snapshots = {};
          for (var j = 0; j < sessData.length; j++) {
            snapshots[sessData[j].name] = sessData[j].snapshot || '';
          }

          // A confirmed active flips a pending marker into a settled ring.
          if (pendingName != null && pendingName === activeName) {
            pendingName = null;
            pendingSince = null;
          }

          render();
        })
        .catch(function () {
          lastPollFailed = true;
          render();
        });
    }

    function schedulePoll() {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(function () {
        poll().then(schedulePoll);
      }, POLL_INTERVAL_MS);
    }

    function stopPolling() {
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
    }

    // ── Rendering ──

    function updateLiveness() {
      var now = Date.now();
      var ageMs = lastPollOkAt == null ? Infinity : now - lastPollOkAt;
      var cls = classifyStaleness(ageMs, !lastPollFailed);
      livenessDot.classList.remove('warn', 'err');
      if (cls === 'warn') livenessDot.classList.add('warn');
      if (cls === 'err') {
        livenessDot.classList.add('err');
        body.classList.add('stale');
      } else {
        body.classList.remove('stale');
      }
      stalenessAge.textContent = cls === 'fresh' || lastPollOkAt == null ? '' : formatAge(ageMs);
    }

    function updateHeader() {
      var unresolvable = viewsList.indexOf(viewName) === -1 && viewName !== 'hidden';
      viewButtonLabel.textContent = viewName;
      viewButton.classList.toggle('unknown-view', unresolvable);

      if (previousActiveName && previousActiveName !== activeName) {
        toggleLastChip.classList.remove('hidden');
        toggleLastChip.querySelector('.chip-name').textContent = previousActiveName;
      } else {
        toggleLastChip.classList.add('hidden');
      }
    }

    function updateAttentionStrip() {
      var needing = attentionSessions(sessions);
      attentionStrip.innerHTML = '';
      needing.forEach(function (s) {
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'attention-chip';
        chip.textContent = s.name;
        chip.addEventListener('click', function () {
          connectTo(s.name);
        });
        attentionStrip.appendChild(chip);
      });

      // Overflow-conditional (DESIGN_LAYOUT.md §1.2): only reserve the band
      // once the grid itself overflows. Measure AFTER the grid has its
      // current tile set (updateGrid runs before this in render()).
      var overflow = needing.length > 0 && gridOverflows(grid.scrollHeight, grid.clientHeight);
      attentionStrip.classList.toggle('hidden', !overflow);
    }

    function buildTile(name) {
      var tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'session-tile';
      tile.dataset.name = name;

      var nameEl = document.createElement('div');
      nameEl.className = 'tile-name';
      tile.appendChild(nameEl);

      var previewEl = document.createElement('div');
      previewEl.className = 'tile-preview';
      tile.appendChild(previewEl);

      var stateEl = document.createElement('div');
      stateEl.className = 'tile-state';
      tile.appendChild(stateEl);

      var pressTimer = null;
      var pressedAt = 0;
      tile.addEventListener('pointerdown', function () {
        pressedAt = Date.now();
        tile.classList.add('is-pressed');
      });
      var releasePress = function () {
        var held = Date.now() - pressedAt;
        var wait = Math.max(0, PRESS_MIN_HOLD_MS - held);
        if (pressTimer) clearTimeout(pressTimer);
        pressTimer = setTimeout(function () {
          tile.classList.remove('is-pressed');
        }, wait);
      };
      tile.addEventListener('pointerup', releasePress);
      tile.addEventListener('pointercancel', function () {
        tile.classList.remove('is-pressed');
      });
      tile.addEventListener('click', function () {
        connectTo(name);
      });

      return tile;
    }

    function updateTile(tile, s) {
      var name = s.name;
      var nameEl = tile.querySelector('.tile-name');
      var previewEl = tile.querySelector('.tile-preview');
      var stateEl = tile.querySelector('.tile-state');

      nameEl.textContent = name;
      previewEl.textContent = previewLines(snapshots[name] || '', PREVIEW_LINES_REGULAR);

      tile.classList.toggle('needs-attention', !!s.needs_attention);

      var now = Date.now();
      var visual = tileVisualState({
        serverActive: !!s.active,
        pendingName: pendingName,
        tileName: name,
        failedUntil: failedByName[name] || null,
        nowMs: now,
      });

      tile.classList.toggle('is-active', visual === 'active');
      tile.classList.toggle('is-pending', visual === 'pending');
      tile.classList.toggle('is-failed', visual === 'failed');

      if (visual === 'failed') {
        stateEl.textContent = 'FAILED';
      } else {
        stateEl.textContent = formatLastActivity(s.last_activity_at, now);
      }
    }

    function updateGrid() {
      var seen = {};
      sessions.forEach(function (s) {
        seen[s.name] = true;
        var tile = tileEls[s.name];
        if (!tile) {
          tile = buildTile(s.name);
          tileEls[s.name] = tile;
        }
        updateTile(tile, s);
      });

      // Reconcile DOM order to match server enumeration order (stable —
      // AGENTS.md/SOFT_DECK_DESIGN.md §2.4: never `sort=attention` here) —
      // and drop tiles for sessions no longer present. This is a diff, not
      // a rebuild, so scroll position and in-flight press/pending/failed
      // state on unaffected tiles survive a poll (DESIGN_LAYOUT.md §4).
      var frag = document.createDocumentFragment();
      sessions.forEach(function (s) {
        frag.appendChild(tileEls[s.name]);
      });
      grid.innerHTML = '';
      grid.appendChild(frag);

      Object.keys(tileEls).forEach(function (name) {
        if (!seen[name]) delete tileEls[name];
      });

      var isLoading = lastPollOkAt == null && !lastPollFailed;
      loadingEl.classList.toggle('hidden', !isLoading);
      emptyEl.classList.toggle('hidden', isLoading || sessions.length > 0);
      grid.classList.toggle('hidden', isLoading || sessions.length === 0);

      if (sessions.length === 0 && !isLoading) {
        var unresolvable = viewsList.indexOf(viewName) === -1 && viewName !== 'hidden';
        emptyEl.innerHTML =
          '<span' +
          (unresolvable ? ' class="view-name-warn"' : '') +
          '>' +
          escapeHtml(viewName) +
          '</span> \u2014 no sessions';
      }
    }

    function escapeHtml(s) {
      var div = document.createElement('div');
      div.textContent = s;
      return div.innerHTML;
    }

    function render() {
      updateLiveness();
      updateHeader();
      updateGrid();
      updateAttentionStrip();
    }

    // ── Renders on a fast tick so relative "STATE" ages / liveness age
    //    stay current between polls, without re-fetching. ──
    setInterval(function () {
      updateLiveness();
      // Re-render tile STATE ages without touching scroll/press state.
      sessions.forEach(function (s) {
        var tile = tileEls[s.name];
        if (tile) updateTile(tile, s);
      });
    }, 1000);

    // ── Tap-to-connect (optimistic, three layers — DESIGN_TILE.md §6.2) ──

    function connectTo(name) {
      if (!name) return;
      var previousPending = pendingName;
      pendingName = name;
      pendingSince = Date.now();
      // Reflect the pending state immediately so the previously-active tile
      // loses its ring in the same frame a new one gains it (the one-ring
      // invariant, DESIGN_TILE.md §4.2).
      render();

      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timeoutId = setTimeout(function () {
        if (controller) controller.abort();
      }, PENDING_TIMEOUT_MS);

      postJSON('/api/sessions/' + encodeURIComponent(name) + '/connect', {}, controller ? controller.signal : undefined)
        .then(function () {
          clearTimeout(timeoutId);
          // Do not locally mark this tile "active" — that would make the
          // client a second source of truth (DESIGN_RESPONSIVE.md §6.3).
          // The next poll (already scheduled on its normal cadence, and we
          // also fire one immediately for snappier reconciliation) confirms
          // it for real.
          poll();
        })
        .catch(function () {
          clearTimeout(timeoutId);
          if (pendingName === name) {
            pendingName = previousPending;
            pendingSince = null;
          }
          failedByName[name] = Date.now() + FAILED_MIN_VISIBLE_MS;
          showToast('Could not switch to ' + name);
          render();
        });
    }

    function showToast(message) {
      toast.textContent = message;
      toast.classList.remove('hidden');
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(function () {
        toast.classList.add('hidden');
      }, 3000);
    }

    // ── View sheet ──

    function openViewSheet() {
      viewSheet.innerHTML = '';
      viewsList.forEach(function (v) {
        var row = document.createElement('button');
        row.type = 'button';
        row.className = 'view-sheet-row' + (v === viewName ? ' current' : '');
        row.textContent = v;
        row.addEventListener('click', function () {
          closeViewSheet();
          patchJSON('/api/state', { active_view: v })
            .then(function () {
              return poll();
            })
            .catch(function () {
              showToast('Could not switch view');
            });
        });
        viewSheet.appendChild(row);
      });
      viewSheetBackdrop.classList.remove('hidden');
      viewSheet.classList.remove('hidden');
    }

    function closeViewSheet() {
      viewSheetBackdrop.classList.add('hidden');
      viewSheet.classList.add('hidden');
    }

    viewButton.addEventListener('click', openViewSheet);
    viewSheetBackdrop.addEventListener('click', closeViewSheet);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeViewSheet();
    });

    toggleLastChip.addEventListener('click', function () {
      if (previousActiveName) connectTo(previousActiveName);
    });

    // ── Wake lock (RESPONSIVE §3) ──

    function updateWakeIndicator() {
      if (wakeState === 'unsupported') {
        wakeIndicator.classList.add('hidden');
        return;
      }
      wakeIndicator.classList.remove('hidden');
      wakeIndicator.classList.toggle('held', wakeState === 'held');
      wakeIndicator.textContent =
        wakeState === 'held'
          ? 'awake'
          : wakeState === 'refused'
            ? 'asleep \u2014 tap to retry'
            : 'asleep';
    }

    function requestWakeLock() {
      if (!('wakeLock' in navigator)) {
        wakeState = 'unsupported';
        updateWakeIndicator();
        return;
      }
      navigator.wakeLock
        .request('screen')
        .then(function (sentinel) {
          wakeSentinel = sentinel;
          wakeState = 'held';
          updateWakeIndicator();
          sentinel.addEventListener('release', function () {
            // The system can release without visibilitychange firing first
            // (RESPONSIVE §3.2 step 2) -- the indicator must read this,
            // never a variable set only at request time.
            if (wakeSentinel === sentinel) {
              wakeState = wakeState === 'off' ? 'off' : 'refused';
              updateWakeIndicator();
            }
          });
        })
        .catch(function () {
          wakeState = 'refused';
          updateWakeIndicator();
        });
    }

    function releaseWakeLock(markOff) {
      if (wakeSentinel) {
        var s = wakeSentinel;
        wakeSentinel = null;
        s.release().catch(function () {
          /* already released */
        });
      }
      if (markOff) {
        wakeState = 'off';
        updateWakeIndicator();
      }
    }

    wakeIndicator.addEventListener('click', function () {
      if (wakeState === 'held') {
        releaseWakeLock(true);
      } else {
        requestWakeLock();
      }
    });

    // ── Visibility handling (RESPONSIVE §3.2 / §7.1) ──

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        stopPolling();
        releaseWakeLock(false);
      } else {
        requestWakeLock();
        poll().then(schedulePoll);
      }
    });

    // ── Boot ──
    //
    // lockLandscapeOrientation() and registerServiceWorker() are defined at
    // module scope above (not inside this IIFE) so they're both callable
    // here via closure AND exportable for node --test.

    function boot() {
      render();
      requestWakeLock();
      lockLandscapeOrientation();
      registerServiceWorker();
      poll().then(schedulePoll);
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', boot);
    } else {
      boot();
    }
  })();
}

// ─── Exports for node --test (frontend/tests/*.mjs) ──────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    classifyStaleness: classifyStaleness,
    formatAge: formatAge,
    formatLastActivity: formatLastActivity,
    nextPreviousActive: nextPreviousActive,
    gridOverflows: gridOverflows,
    previewLines: previewLines,
    attentionSessions: attentionSessions,
    tileVisualState: tileVisualState,
    lockLandscapeOrientation: lockLandscapeOrientation,
    registerServiceWorker: registerServiceWorker,
    POLL_INTERVAL_MS: POLL_INTERVAL_MS,
    STALE_WARN_MS: STALE_WARN_MS,
    STALE_ERR_MS: STALE_ERR_MS,
    PENDING_TIMEOUT_MS: PENDING_TIMEOUT_MS,
    FAILED_MIN_VISIBLE_MS: FAILED_MIN_VISIBLE_MS,
    PRESS_MIN_HOLD_MS: PRESS_MIN_HOLD_MS,
  };
}

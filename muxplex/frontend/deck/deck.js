// muxplex soft deck -- deck.js
//
// Vanilla JS, no build step, no framework, no dependency on app.js. Consumes
// GET /api/view + GET /api/sessions, POST /api/sessions/{name}/connect, and
// PATCH /api/state -- the same server-resolved semantics the PWA and
// muxplex-deck sidecar use (AGENTS.md: "clients must not re-derive view
// membership, the bell predicate, or sort order"). This file never
// recomputes needs_attention or view filtering itself.
//
// DESIGN_SOFTDECK.md is the governing spec: a phone is a deck with
// dial_count=0, is_touch=false, and an R x C grid derived purely from its
// own viewport. Every control fits on screen at once -- no scroll, no
// dropdown menus, no slide-up sheets. Paging (not scrolling) absorbs
// overflow, exactly as the hardware family does.

// ─── Pure logic (exported for node --test; no DOM dependency) ─────────────

var POLL_INTERVAL_MS = 2000;
var STALE_WARN_MS = 6000; // ~3 poll cycles -- dim the grid
var STALE_ERR_MS = 30000; // -- surface takeover (disconnected)
var PENDING_TIMEOUT_MS = 2500;
var FAILED_MIN_VISIBLE_MS = 3000;
var PRESS_MIN_HOLD_MS = 100; // DESIGN_SOFTDECK.md \u00a74.2: 100ms press feedback

// Grid derivation constants -- DESIGN_SOFTDECK.md \u00a71.
var S_TARGET = 88;
var S_MIN = 72;
var S_MAX = 160;
var N_MAX = 32;
var ASPECT_TOLERANCE = 1.15;
var S_STEP = 4;

/**
 * Classify liveness/staleness from the age of the last successful poll.
 * `lastOk` is a boolean -- a request in flight or errored counts as "not ok"
 * independent of age.
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
 * Format an age in milliseconds as a short relative string for a STATE band
 * ("4m").
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
 * as a session tile STATE line. Absent activity renders as an em dash -- the
 * band is still reserved, never blank-collapsed.
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
 * Compute the last N non-empty-trimmed lines of a pane snapshot for the
 * session tile's TEXTURE preview field, newest line last (bottom-anchored).
 * @param {string} snapshot
 * @param {number} maxLines
 * @returns {string}
 */
function previewLines(snapshot, maxLines) {
  if (!snapshot) return '';
  var lines = snapshot.split('\n');
  while (lines.length > 0 && lines[lines.length - 1].trim() === '') {
    lines.pop();
  }
  if (lines.length <= maxLines) return lines.join('\n');
  return lines.slice(lines.length - maxLines).join('\n');
}

/**
 * Decide the tile's visual state class given (a) authoritative server data
 * for this tile and (b) locally-tracked optimistic/failed overlays. Server
 * truth wins UNLESS a not-yet-expired local override says otherwise:
 *   - a failed marker must survive at least one poll cycle
 *   - a pending marker must never be indistinguishable from confirmed
 *     active until the server confirms it
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

// ─── Capability derivation: the grid (DESIGN_SOFTDECK.md \u00a71.3) ────────────

/**
 * Derive the fixed R x C key grid from a content box (viewport minus
 * safe-area insets), following DESIGN_SOFTDECK.md \u00a71.3 exactly:
 *
 *   S_fit starts at S_TARGET and only ever grows (never shrinks) -- floor()
 *   in the column/row computation always errs toward bigger keys, fewer of
 *   them; paging absorbs the rest. If the box would hold more than N_MAX
 *   keys even so, keys grow instead of adding more (the "grow the keys,
 *   don't add them" rule) up to S_MAX, after which a row or column is
 *   dropped -- whichever keeps the grid's aspect ratio closest to the box's.
 *
 *   Once the count is settled, the leftover space is distributed: if the
 *   resulting cell's aspect ratio is close enough to square (<= 1.15), the
 *   grid stretches to fill the box (cells imperceptibly non-square).
 *   Otherwise the grid letterboxes at the smaller of the two cell dimensions
 *   and is centered, with the remainder becoming bezel.
 *
 *   `S` (the scalar every `f(S)` token formula reads) is the smaller of the
 *   final cell's width/height, clamped to S_MAX -- NOT necessarily equal to
 *   the actual rendered cell size once letterboxing/clamping is involved.
 *
 * @param {number} contentW - content box width in CSS px (post safe-area-inset)
 * @param {number} contentH - content box height in CSS px (post safe-area-inset)
 * @returns {{rows:number, cols:number, cellW:number, cellH:number, s:number,
 *            gap:number, letterboxed:boolean, tooSmall:boolean}}
 */
function computeGrid(contentW, contentH) {
  if (!(contentW > 0) || !(contentH > 0)) {
    return { rows: 0, cols: 0, cellW: 0, cellH: 0, s: 0, gap: 0, letterboxed: false, tooSmall: true };
  }

  var sFit = S_TARGET;
  var gap = Math.round(sFit / 8);
  var cols = 1;
  var rows = 1;
  while (true) {
    gap = Math.round(sFit / 8);
    var pitch = sFit + gap;
    cols = Math.max(1, Math.floor((contentW + gap) / pitch));
    rows = Math.max(1, Math.floor((contentH + gap) / pitch));
    if (cols * rows <= N_MAX || sFit >= S_MAX) break;
    sFit += S_STEP;
  }

  // Ceiling still exceeded at S_MAX: drop whichever dimension keeps the
  // grid's aspect ratio closest to the box's own aspect ratio.
  var boxAspect = contentW / contentH;
  while (cols * rows > N_MAX && cols > 1 && rows > 1) {
    var aspectDropCol = (cols - 1) / rows;
    var aspectDropRow = cols / (rows - 1);
    if (Math.abs(aspectDropCol - boxAspect) <= Math.abs(aspectDropRow - boxAspect)) {
      cols -= 1;
    } else {
      rows -= 1;
    }
  }

  var cellW = (contentW - (cols - 1) * gap) / cols;
  var cellH = (contentH - (rows - 1) * gap) / rows;
  var ratio = Math.max(cellW, cellH) / Math.min(cellW, cellH);
  var letterboxed = ratio > ASPECT_TOLERANCE;
  if (letterboxed) {
    var both = Math.min(cellW, cellH);
    cellW = both;
    cellH = both;
  }
  var s = Math.min(cellW, cellH, S_MAX);

  return {
    rows: rows,
    cols: cols,
    cellW: cellW,
    cellH: cellH,
    s: s,
    gap: gap,
    letterboxed: letterboxed,
    tooSmall: s < S_MIN,
  };
}

/**
 * Derive the key face's geometry/typography tokens from S, per
 * KEY_DESIGN_SYSTEM.md \u00a71-\u00a72 and DESIGN_SOFTDECK.md \u00a74.2's medium-forced
 * deltas (thicker ring, weight becomes available).
 * @param {number} s - the grid's derived S scalar
 * @param {number} [cellH] - actual (possibly non-clamped) cell height, for BODY_H
 * @returns {{b:number, m:number, nameH:number, stateH:number, bodyH:number,
 *            primary:number, secondary:number, texture:number}}
 */
function deriveTokens(s, cellH) {
  var b = Math.max(2, Math.round(s / 30));
  var m = Math.round(s / 18);
  var nameH = Math.round(0.28 * s);
  var stateH = Math.round(0.19 * s);
  var primary = Math.round((2 * s) / 9);
  var secondary = Math.round((11 * s) / 72);
  var bodyH = cellH != null ? Math.round(cellH - 2 * m - nameH - stateH) : null;
  return {
    b: b,
    m: m,
    nameH: nameH,
    stateH: stateH,
    bodyH: bodyH,
    primary: primary,
    secondary: secondary,
    texture: 11,
  };
}

/**
 * Port of muxplex-deck's `layout.py::_reserved_control_keys` -- the three
 * navigation keys are a constant at every grid size, never a fraction, so
 * they never move when the viewport changes (DESIGN_SOFTDECK.md \u00a73).
 *
 *   - key_count < 4, or the three positions would collide (degenerate on a
 *     single row or single column): no controls, every key a session tile.
 *   - cols === 3 and rows >= 2: bottom control row, PREV/VIEW/NEXT left to
 *     right on the last row.
 *   - otherwise: corners -- VIEW top-left (key 0), PREV bottom-left, NEXT
 *     bottom-right.
 * @param {number} rows
 * @param {number} cols
 * @returns {{mode:'corners'|'bottom-row'|'degenerate', view:number|null,
 *            prev:number|null, next:number|null}}
 */
function reservedControlKeys(rows, cols) {
  var keyCount = rows * cols;
  if (keyCount < 4) {
    return { mode: 'degenerate', view: null, prev: null, next: null };
  }
  if (cols === 3 && rows >= 2) {
    var base = (rows - 1) * cols;
    return { mode: 'bottom-row', prev: base, view: base + 1, next: base + 2 };
  }
  var view = 0;
  var prev = (rows - 1) * cols;
  var next = rows * cols - 1;
  if (view === prev || view === next || prev === next) {
    return { mode: 'degenerate', view: null, prev: null, next: null };
  }
  return { mode: 'corners', view: view, prev: prev, next: next };
}

/**
 * The ascending key indices available as session tiles -- every index
 * 0..key_count-1 that isn't one of the three reserved control keys.
 * @param {number} rows
 * @param {number} cols
 * @param {{view:number|null, prev:number|null, next:number|null}} reserved
 * @returns {number[]}
 */
function sessionSlotIndices(rows, cols, reserved) {
  var reservedSet = {};
  if (reserved.view != null) reservedSet[reserved.view] = true;
  if (reserved.prev != null) reservedSet[reserved.prev] = true;
  if (reserved.next != null) reservedSet[reserved.next] = true;
  var slots = [];
  for (var i = 0; i < rows * cols; i++) {
    if (!reservedSet[i]) slots.push(i);
  }
  return slots;
}

/**
 * Total page count for a list of `itemCount` items filling `perPage` slots.
 * Always at least 1 (an empty list is still "page 1 of 1", all slots blank).
 * @param {number} itemCount
 * @param {number} perPage
 * @returns {number}
 */
function pageCount(itemCount, perPage) {
  if (perPage <= 0) return 1;
  return Math.max(1, Math.ceil(itemCount / perPage));
}

/**
 * The slice of `items` shown on `page` (0-indexed), `perPage` per page.
 * Short pages return fewer than `perPage` items -- callers render the
 * remainder as blank (#000000) faces rather than reflowing.
 * @param {Array} items
 * @param {number} page
 * @param {number} perPage
 * @returns {Array}
 */
function pageSlice(items, page, perPage) {
  if (perPage <= 0) return [];
  var start = page * perPage;
  return items.slice(start, start + perPage);
}

/**
 * Clamp a page-turn to [0, pageCount-1] -- page_prev/page_next are
 * "clamped", never wrapping (CONTROL_MAPPING_DESIGN.md \u00a72.1).
 * @param {number} page
 * @param {number} delta
 * @param {number} count
 * @returns {number}
 */
function clampPage(page, delta, count) {
  var next = page + delta;
  if (next < 0) return 0;
  if (next > count - 1) return count - 1;
  return next;
}

/**
 * Content for the three reserved control keys, per
 * KEY_DESIGN_SYSTEM.md \u00a76.2 (grid mode) and \u00a76.4 (picker mode). The
 * critical inversion from \u00a76.2 is preserved verbatim: the noun (VIEW/PAGE)
 * goes big (BODY, PRIMARY), the direction (< PREV / NEXT >) goes small
 * (NAME, SECONDARY) -- this is what keeps view_prev and page_prev from
 * being confusable, which is worse on soft keys than on bezel-separated
 * hardware ones.
 * @param {'view'|'prev'|'next'|'back'} role
 * @param {{viewName?:string, pagePosition?:string}} ctx
 * @returns {{name:string, body:string, state:string}}
 */
function controlKeyContent(role, ctx) {
  var c = ctx || {};
  switch (role) {
    case 'view':
      return { name: 'VIEW', body: c.viewName || '', state: c.pagePosition || '' };
    case 'prev':
      return { name: '< PREV', body: 'PAGE', state: c.pagePosition || '' };
    case 'next':
      return { name: 'NEXT >', body: 'PAGE', state: c.pagePosition || '' };
    case 'back':
      return { name: '< BACK', body: 'VIEW', state: '' };
    default:
      return { name: '', body: '', state: '' };
  }
}

/**
 * Content for a picker option key (KEY_DESIGN_SYSTEM.md \u00a76.3): NAME stays
 * reserved-but-empty (every option is the same category), BODY carries the
 * view name at PRIMARY size, STATE carries the "one deliberate enrichment"
 * DESIGN_SOFTDECK.md \u00a76.2 adds -- a session count, when known.
 * @param {string} viewName
 * @param {number|null} sessionCount
 * @returns {{name:string, body:string, state:string}}
 */
function pickerOptionContent(viewName, sessionCount) {
  var state = '';
  if (sessionCount != null) {
    state = sessionCount + (sessionCount === 1 ? ' session' : ' sessions');
  }
  return { name: '', body: viewName, state: state };
}

/**
 * Per-view session counts for the picker's STATE enrichment, computed from
 * already-fetched, already-documented data only:
 *   - `allSessionNames`: this device's current session names (GET /api/sessions)
 *   - `viewsList`: raw `settings.views` shape -- a list of
 *     `{name: string, sessions: string[]}` objects (views.py's `filter_visible`
 *     is the canonical reader of this same shape).
 *
 * Matches by the ":<name>" suffix -- the exact rule AGENTS.md documents as
 * safe for clients to port ("View membership entries are normalized to
 * 'device_id:name' form; clients match by the ':<name>' suffix"), with a
 * bare-name fallback for legacy pre-normalization entries (mirrors
 * views.py's own dual-lookup). This is NOT a re-derivation of the
 * needs-attention bell predicate or of view filtering semantics -- just a
 * membership count, using the one matching rule the server explicitly
 * blesses for client reuse.
 * @param {string[]} allSessionNames
 * @param {Array<{name:string, sessions:string[]}>} viewsList
 * @returns {Object<string, number>}
 */
function viewSessionCounts(allSessionNames, viewsList) {
  var counts = {};
  var names = allSessionNames || [];
  var views = viewsList || [];
  views.forEach(function (v) {
    if (!v || !v.name) return;
    var members = v.sessions || [];
    var count = 0;
    for (var i = 0; i < names.length; i++) {
      var suffix = ':' + names[i];
      var matched = false;
      for (var j = 0; j < members.length; j++) {
        if (members[j] === names[i] || members[j].slice(-suffix.length) === suffix) {
          matched = true;
          break;
        }
      }
      if (matched) count++;
    }
    counts[v.name] = count;
  });
  return counts;
}

// ─── Fullscreen orientation lock ───────────────────────────────────────────
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

/**
 * Shorten `text` (appending an ellipsis) until `measureWidth(text)` fits
 * `maxWidthPx`. Deterministic prefix + trailing "\u2026" -- port of
 * muxplex-deck's `rendering.py::_fit_label` (pixel-measured truncation, not
 * a fixed character-count cap -- a cap tuned for one face size is either
 * dead weight or wrong at every other size; see DECK_PARITY_ARCHITECTURE.md
 * \u00a72.3/\u00a74.1).
 *
 * `measureWidth` is injected rather than baked in as a Canvas call so this
 * stays a pure, DOM-free function testable the same way as every other
 * function in this file (frontend/tests/test_deck.mjs passes a fake
 * character-counting measurer). The real browser painter
 * (`_buildMeasureContext` below) supplies a canvas-measureText-backed
 * implementation using the face's actual font.
 * @param {string} text
 * @param {number} maxWidthPx
 * @param {(s: string) => number} measureWidth
 * @returns {string}
 */
function fitLabel(text, maxWidthPx, measureWidth) {
  if (!text) return '';
  if (measureWidth(text) <= maxWidthPx) return text;
  var t = text;
  while (t.length > 0 && measureWidth(t + '\u2026') > maxWidthPx) {
    t = t.slice(0, -1);
  }
  return t + '\u2026';
}

// ─── KeyPlan: state → KeyPlan[] → painter (DECK_PARITY_ARCHITECTURE.md §6.3) ─
//
// The blank-control-key bug (nine tests green on `controlKeyContent`, zero
// call sites) was a WIRING failure, not a logic failure: a second path
// existed between server state and a key face, and that second path never
// called the function that computed real content. The fix here is
// structural, not just the one call site: `computeKeyPlan` is now the ONLY
// function that decides what appears on any key, producing a plain,
// index-addressed array (`KeyPlan`, length rows*cols) covering every key on
// the surface -- reserved control keys, session tiles, picker options, and
// blank slots alike. The painter (`paintKeyFace`, in the DOM section below)
// reads NOTHING else: no second lookup into `sessions`/`viewsList`/state, no
// helper it might forget to call. A key with no content is only possible if
// the plan itself says `role: 'empty'` -- there is no other way to reach
// the screen.
//
// Deliberately NOT resolved here: pixel-measured label truncation. That
// needs the real font/canvas, which only exists in the DOM painter (see
// `fitLabel` above and `_buildMeasureContext` below) -- `name`/`body` on a
// KeyFace carry the full, untruncated string. This keeps `computeKeyPlan`
// itself testable with zero DOM/Canvas dependency, matching every other
// pure function in this file.

/**
 * @typedef {{active: boolean, pending: boolean, failed: boolean,
 *            needsAttention: boolean, currentView: boolean}} KeyFaceFlags
 * @typedef {{index: number,
 *            role: 'session'|'view'|'prev'|'next'|'back'|'view-option'|'empty',
 *            name: string, body: string, state: string, preview: string,
 *            target: string|null, flags: KeyFaceFlags}} KeyFace
 */

function _emptyFlags() {
  return { active: false, pending: false, failed: false, needsAttention: false, currentView: false };
}

/** A blank key face -- the only shape that ever renders as empty. */
function _emptyFace(index) {
  return { index: index, role: 'empty', name: '', body: '', state: '', preview: '', target: null, flags: _emptyFlags() };
}

function _clampToCount(value, count) {
  if (value > count - 1) value = count - 1;
  if (value < 0) value = 0;
  return value;
}

/**
 * The face class for a role -- shared by the painter's className and (for
 * documentation/testability) exported alongside computeKeyPlan.
 * @param {string} role
 * @returns {string}
 */
function faceClassName(role) {
  if (role === 'empty') return 'is-empty';
  if (role === 'session') return 'is-session';
  if (role === 'view-option') return 'is-picker-option';
  return 'is-control'; // view | prev | next | back
}

/**
 * Compute the full KeyPlan for the current UI state. This is the single
 * decision point for "what is on key N" -- see the section comment above.
 *
 * @param {object} p
 * @param {{rows:number, cols:number}} p.grid
 * @param {{mode:string, view:?number, prev:?number, next:?number}} p.reserved
 * @param {'grid'|'picker'} p.mode
 * @param {Array<{name:string,active:boolean,needs_attention:boolean,last_activity_at:?number}>} p.sessions
 *   current view's sessions, server order (grid mode)
 * @param {string} p.viewName - current active_view name
 * @param {string[]} p.viewsList - browsable view names (picker mode)
 * @param {number} p.page - current session grid page
 * @param {number} p.pickerPage - current view-picker page
 * @param {Object<string, number>} [p.viewCounts] - picker STATE enrichment
 * @param {string|null} [p.pendingName]
 * @param {Object<string, number>} [p.failedByName] - name -> expiry epoch ms
 * @param {Object<string, string>} [p.snapshots] - name -> pane text
 * @param {number} [p.previewLinesMax]
 * @param {number} p.nowMs
 * @returns {{plan: KeyFace[], page: number, pickerPage: number}}
 */
function computeKeyPlan(p) {
  var rows = p.grid.rows;
  var cols = p.grid.cols;
  var keyCount = rows * cols;
  var plan = [];
  for (var i = 0; i < keyCount; i++) plan.push(_emptyFace(i));

  // A degenerate grid (< 4 keys, or corners would collide on a single row/
  // column) has no room for reserved controls -- but per
  // reservedControlKeys' own contract ("no controls, every key a session
  // tile"), session/view-option slots still cover the WHOLE grid in that
  // case, they just never lose any keys to controls. Only the three
  // _setControlFace calls below are conditional on this; slot computation
  // and the session/view-option loops always run.
  var reserved = p.reserved || { mode: 'degenerate', view: null, prev: null, next: null };
  var page = p.page;
  var pickerPage = p.pickerPage;
  var hasControls = reserved.mode !== 'degenerate';

  var slots = sessionSlotIndices(rows, cols, reserved);

  if (p.mode === 'picker') {
    var viewsList = p.viewsList || [];
    var pc = pageCount(viewsList.length, slots.length);
    pickerPage = _clampToCount(pickerPage, pc);
    var pageViews = pageSlice(viewsList, pickerPage, slots.length);
    var pagePosition = pc > 1 ? pickerPage + 1 + '/' + pc : '';

    if (hasControls) {
      _setControlFace(plan, reserved.view, 'back', controlKeyContent('back', {}));
      _setControlFace(plan, reserved.prev, 'prev', controlKeyContent('prev', { pagePosition: pagePosition }));
      _setControlFace(plan, reserved.next, 'next', controlKeyContent('next', { pagePosition: pagePosition }));
    }

    for (var vi = 0; vi < slots.length; vi++) {
      var name = pageViews[vi];
      if (!name) continue;
      var count = p.viewCounts && p.viewCounts[name] != null ? p.viewCounts[name] : null;
      var content = pickerOptionContent(name, count);
      plan[slots[vi]] = {
        index: slots[vi],
        role: 'view-option',
        name: content.name,
        body: content.body,
        state: content.state,
        preview: '',
        target: name,
        flags: _mergeFlags({ currentView: name === p.viewName }),
      };
    }
    return { plan: plan, page: page, pickerPage: pickerPage };
  }

  // grid mode
  var sessions = p.sessions || [];
  var pc2 = pageCount(sessions.length, slots.length);
  page = _clampToCount(page, pc2);
  var pageSessions = pageSlice(sessions, page, slots.length);
  var pagePosition2 = pc2 > 1 ? page + 1 + '/' + pc2 : '';

  _setControlFace(plan, reserved.view, 'view', controlKeyContent('view', { viewName: p.viewName, pagePosition: pagePosition2 }));
  _setControlFace(plan, reserved.prev, 'prev', controlKeyContent('prev', { pagePosition: pagePosition2 }));
  _setControlFace(plan, reserved.next, 'next', controlKeyContent('next', { pagePosition: pagePosition2 }));

  var snapshots = p.snapshots || {};
  var previewMax = p.previewLinesMax != null ? p.previewLinesMax : 20;
  var failedByName = p.failedByName || {};

  for (var si = 0; si < slots.length; si++) {
    var s = pageSessions[si];
    if (!s) continue;
    var visual = tileVisualState({
      serverActive: !!s.active,
      pendingName: p.pendingName != null ? p.pendingName : null,
      tileName: s.name,
      failedUntil: failedByName[s.name] || null,
      nowMs: p.nowMs,
    });
    plan[slots[si]] = {
      index: slots[si],
      role: 'session',
      name: s.name,
      body: '',
      state: visual === 'failed' ? 'FAILED' : formatLastActivity(s.last_activity_at, p.nowMs),
      preview: previewLines(snapshots[s.name] || '', previewMax),
      target: s.name,
      flags: _mergeFlags({
        active: visual === 'active',
        pending: visual === 'pending',
        failed: visual === 'failed',
        needsAttention: !!s.needs_attention,
      }),
    };
  }

  return { plan: plan, page: page, pickerPage: pickerPage };
}

function _mergeFlags(overrides) {
  var flags = _emptyFlags();
  for (var key in overrides) {
    if (Object.prototype.hasOwnProperty.call(overrides, key)) flags[key] = overrides[key];
  }
  return flags;
}

function _setControlFace(plan, index, role, content) {
  plan[index] = {
    index: index,
    role: role,
    name: content.name,
    body: content.body,
    state: content.state,
    preview: '',
    target: null,
    flags: _emptyFlags(),
  };
}

/**
 * Regression guard for the exact bug this file shipped: a control-role key
 * (view/prev/next/back) whose NAME *and* BODY are both empty has no content
 * a user can read at all -- the "blank blue key" symptom. `controlKeyContent`
 * never returns that shape for a real role, so this only fires if a future
 * change reintroduces a wiring gap between the plan and the content table.
 * @param {KeyFace[]} plan
 * @returns {KeyFace[]} any offending faces (empty array = plan is clean)
 */
function findBlankControlFaces(plan) {
  var controlRoles = { view: true, prev: true, next: true, back: true };
  var offenders = [];
  for (var i = 0; i < plan.length; i++) {
    var face = plan[i];
    if (controlRoles[face.role] && !face.name && !face.body) offenders.push(face);
  }
  return offenders;
}

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

// ─── Service worker registration ───────────────────────────────────────────
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

// ─── DOM wiring (browser only) ──────────────────────────────────────────────

if (typeof document !== 'undefined') {
  (function () {
    'use strict';

    var PREVIEW_LINES_MAX = 20; // generous; CSS clips to the actual box

    // ── State ──
    var sessions = []; // last-known GET /api/view sessions[] for the CURRENT view, server order
    var allSessionNames = []; // last-known GET /api/sessions names (all local sessions)
    var snapshots = {}; // name -> pane text, from GET /api/sessions
    var viewName = 'all';
    var viewsList = ['all'];
    var activeName = null;
    var lastPollOkAt = null; // epoch ms of last successful poll
    var lastPollFailed = false;
    var pendingName = null; // optimistic tap in flight
    var failedByName = {}; // name -> epoch ms the FAILED marker expires
    var pollTimer = null;
    var wakeSentinel = null;

    var mode = 'grid'; // 'grid' | 'picker'
    var page = 0; // current session grid page
    var pickerPage = 0; // current view-picker page
    var grid = null; // last computeGrid() result
    var reserved = null; // last reservedControlKeys() result
    var tokens = null; // last deriveTokens() result -- feeds the label-measure font/box
    var keyEls = []; // R*C key <button> elements, index-addressed
    var currentShape = null; // 'RxC' string, to detect when a rebuild is needed
    var viewCounts = {}; // best-effort enrichment, see loadViewCounts()

    // ── DOM refs ──
    var root = document.getElementById('deck-root');
    var surface = document.getElementById('deck-surface');
    var gridEl = document.getElementById('deck-grid');
    var disconnectedEl = document.getElementById('deck-disconnected');
    var disconnectedMessageEl = document.getElementById('deck-disconnected-message');
    var retryButton = document.getElementById('deck-retry');

    // ── Fetch helpers ──
    // Deliberately NOT a generic `api()` clone of app.js's helper -- kept
    // isolated (a broken deck must never share a failure mode with the
    // terminal client).
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
      // sort=attention is hardcoded, not a soft-deck config knob: this
      // surface's whole purpose is Stream Deck parity (DESIGN_SOFTDECK.md),
      // the hardware sidecar defaults to attention ordering
      // (muxplex-deck/config.py's `sort` default), and unlike the sidecar
      // this page has no persisted per-device config file or settings UI to
      // put a knob on -- adding one would be a config surface with no real
      // consumer today (DECK_PARITY_ARCHITECTURE.md \u00a72.1/\u00a76.1: sort order
      // is Layer A, one server-owned answer, not a per-client preference).
      var viewReq = getJSON('/api/view?sort=attention');
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
          activeName = newActive;
          sessions = viewData.sessions;

          allSessionNames = sessData.map(function (s) {
            return s.name;
          });
          snapshots = {};
          for (var j = 0; j < sessData.length; j++) {
            snapshots[sessData[j].name] = sessData[j].snapshot || '';
          }

          // A confirmed active flips a pending marker into a settled ring.
          if (pendingName != null && pendingName === activeName) {
            pendingName = null;
          }

          // Clamp the current page if the session list shrank underneath us.
          if (reserved) {
            var slots = sessionSlotIndices(grid.rows, grid.cols, reserved);
            var pc = pageCount(sessions.length, slots.length);
            if (page > pc - 1) page = pc - 1;
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

    // ── Grid (re)computation ──
    //
    // Re-derived only on a settled resize (debounced) and never while the
    // picker is open (DESIGN_SOFTDECK.md \u00a711 item 8) -- a grid whose shape
    // changes mid-session is not a deck; the picker in particular must not
    // have its geometry yanked out from under an open tap.

    function contentBox() {
      return { w: root.clientWidth, h: root.clientHeight };
    }

    function applyGridTokens(g, t) {
      var style = root.style;
      style.setProperty('--cols', g.cols);
      style.setProperty('--rows', g.rows);
      style.setProperty('--cell-w', g.cellW + 'px');
      style.setProperty('--cell-h', g.cellH + 'px');
      style.setProperty('--gap', g.gap + 'px');
      style.setProperty('--b', t.b + 'px');
      style.setProperty('--m', t.m + 'px');
      style.setProperty('--name-h', t.nameH + 'px');
      style.setProperty('--state-h', t.stateH + 'px');
      style.setProperty('--body-h', (t.bodyH != null ? t.bodyH : 0) + 'px');
      style.setProperty('--primary', t.primary + 'px');
      style.setProperty('--secondary', t.secondary + 'px');
      style.setProperty('--texture', t.texture + 'px');
    }

    function buildKeyElement(index) {
      var key = document.createElement('button');
      key.type = 'button';
      key.className = 'deck-key';
      key.dataset.index = String(index);

      var nameEl = document.createElement('div');
      nameEl.className = 'key-name';
      key.appendChild(nameEl);

      var previewEl = document.createElement('div');
      previewEl.className = 'key-preview';
      key.appendChild(previewEl);

      var bodyEl = document.createElement('div');
      bodyEl.className = 'key-body';
      key.appendChild(bodyEl);

      var stateEl = document.createElement('div');
      stateEl.className = 'key-state';
      key.appendChild(stateEl);

      var pressTimer = null;
      var pressedAt = 0;
      key.addEventListener('pointerdown', function () {
        pressedAt = Date.now();
        key.classList.add('is-pressed');
      });
      var releasePress = function () {
        var held = Date.now() - pressedAt;
        var wait = Math.max(0, PRESS_MIN_HOLD_MS - held);
        if (pressTimer) clearTimeout(pressTimer);
        pressTimer = setTimeout(function () {
          key.classList.remove('is-pressed');
        }, wait);
      };
      key.addEventListener('pointerup', releasePress);
      key.addEventListener('pointercancel', function () {
        key.classList.remove('is-pressed');
      });
      key.addEventListener('click', function () {
        onKeyTap(index);
      });

      return key;
    }

    function rebuildGridIfNeeded(g) {
      var shape = g.rows + 'x' + g.cols;
      if (shape === currentShape) return;
      currentShape = shape;
      gridEl.innerHTML = '';
      keyEls = [];
      for (var i = 0; i < g.rows * g.cols; i++) {
        var el = buildKeyElement(i);
        gridEl.appendChild(el);
        keyEls.push(el);
      }
    }

    function recomputeGrid() {
      if (mode === 'picker') return; // never regrid under an open picker
      var box = contentBox();
      var g = computeGrid(box.w, box.h);
      grid = g;
      reserved = reservedControlKeys(g.rows, g.cols);
      tokens = deriveTokens(g.s, g.cellH);
      applyGridTokens(g, tokens);
      rebuildGridIfNeeded(g);
      root.classList.toggle('too-small', !!g.tooSmall);
    }

    var resizeTimer = null;
    function scheduleRecompute() {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        recomputeGrid();
        render();
      }, 150);
    }
    window.addEventListener('resize', scheduleRecompute);
    window.addEventListener('orientationchange', scheduleRecompute);

    // ── Rendering: state → KeyPlan[] → painter ──
    //
    // computeKeyPlan (pure, shared with node --test) decides content; the
    // painter below reads ONLY the KeyFace it's given -- see the "KeyPlan"
    // section comment above computeKeyPlan's definition for why that's what
    // makes the blank-control-key bug class structurally impossible now.

    var _measureCanvas = null;
    var _FACE_FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

    /**
     * Build a label-measurement context from the current grid/tokens, for
     * the one font size (`--primary`) truncatable text is ever painted at
     * (session NAME, the VIEW key's BODY, a picker option's BODY). Returns
     * null outside a real DOM (no canvas to measure with) -- callers must
     * treat a null measure context as "skip truncation," which is exactly
     * what happens in `node --test` today since nothing there paints.
     * @returns {{maxWidth:number, width:(s:string)=>number}|null}
     */
    function buildMeasureContext() {
      if (!grid || !tokens || typeof document === 'undefined') return null;
      if (!_measureCanvas) _measureCanvas = document.createElement('canvas');
      var ctx = _measureCanvas.getContext('2d');
      if (!ctx) return null;
      var font = '600 ' + tokens.primary + 'px ' + _FACE_FONT_FAMILY;
      return {
        maxWidth: grid.cellW - 2 * tokens.m,
        width: function (text) {
          ctx.font = font;
          return ctx.measureText(text).width;
        },
      };
    }

    /**
     * The ONE function that turns a KeyFace into pixels. Reads nothing but
     * `face` (plus the injectable `measure` context for truncation) -- no
     * second lookup into `sessions`/`viewsList`/module state. This is the
     * structural guarantee DECK_PARITY_ARCHITECTURE.md \u00a76.3 calls for: a
     * helper the painter "forgets to call" cannot exist, because the
     * painter has nothing else to call.
     * @param {HTMLElement} el
     * @param {KeyFace} face
     * @param {{maxWidth:number, width:(s:string)=>number}|null} measure
     */
    function paintKeyFace(el, face, measure) {
      el.className = 'deck-key ' + faceClassName(face.role);
      if (face.flags.active) el.classList.add('is-active');
      if (face.flags.pending) el.classList.add('is-pending');
      if (face.flags.failed) el.classList.add('is-failed');
      if (face.flags.needsAttention) el.classList.add('needs-attention');
      if (face.flags.currentView) el.classList.add('is-active');

      var nameText = face.name;
      var bodyText = face.body;
      if (measure) {
        // Truncation is scoped to exactly the bands that carry a
        // user-controlled, unbounded-length string -- see fitLabel's
        // doc comment for why this can't happen in computeKeyPlan.
        if (face.role === 'session') nameText = fitLabel(nameText, measure.maxWidth, measure.width);
        if (face.role === 'view' || face.role === 'view-option') {
          bodyText = fitLabel(bodyText, measure.maxWidth, measure.width);
        }
      }

      el.querySelector('.key-name').textContent = nameText;
      el.querySelector('.key-preview').textContent = face.preview || '';
      el.querySelector('.key-body').textContent = bodyText;
      el.querySelector('.key-state').textContent = face.state;
      el.dataset.role = face.role;
      el.dataset.name = face.target || '';
    }

    function renderKeys() {
      var result = computeKeyPlan({
        grid: grid,
        reserved: reserved,
        mode: mode,
        sessions: sessions,
        viewName: viewName,
        viewsList: viewsList,
        page: page,
        pickerPage: pickerPage,
        viewCounts: viewCounts,
        pendingName: pendingName,
        failedByName: failedByName,
        snapshots: snapshots,
        previewLinesMax: PREVIEW_LINES_MAX,
        nowMs: Date.now(),
      });
      page = result.page;
      pickerPage = result.pickerPage;

      var measure = buildMeasureContext();
      for (var i = 0; i < keyEls.length; i++) {
        paintKeyFace(keyEls[i], result.plan[i], measure);
      }
    }

    function render() {
      if (!grid || grid.rows === 0 || grid.cols === 0) {
        showDisconnected('Screen too small for the deck.');
        return;
      }

      var ageMs = lastPollOkAt == null ? Infinity : Date.now() - lastPollOkAt;
      var staleness = classifyStaleness(ageMs, !lastPollFailed);

      if (staleness === 'err') {
        showDisconnected("Can't reach muxplex \u2014 check your connection.");
        return;
      }
      hideDisconnected();
      surface.classList.toggle('is-stale', staleness === 'warn');

      renderKeys();
    }

    function showDisconnected(message) {
      surface.classList.add('hidden');
      disconnectedEl.classList.remove('hidden');
      disconnectedMessageEl.textContent = message;
    }

    function hideDisconnected() {
      disconnectedEl.classList.add('hidden');
      surface.classList.remove('hidden');
    }

    retryButton.addEventListener('click', function () {
      poll();
    });

    // Re-render on a fast tick so relative STATE ages stay current between
    // polls, without re-fetching or rebuilding the grid.
    setInterval(function () {
      if (mode === 'grid' && grid && grid.rows > 0) render();
    }, 1000);

    // ── Tap dispatch ──

    function onKeyTap(index) {
      var el = keyEls[index];
      if (!el) return;
      var role = el.dataset.role;
      if (!role || role === 'empty') return;

      if (role === 'session') {
        connectTo(el.dataset.name);
      } else if (role === 'view') {
        openPicker();
      } else if (role === 'back') {
        closePicker();
      } else if (role === 'prev') {
        if (mode === 'picker') {
          var slotsP = sessionSlotIndices(grid.rows, grid.cols, reserved);
          pickerPage = clampPage(pickerPage, -1, pageCount(viewsList.length, slotsP.length));
        } else {
          var slotsG = sessionSlotIndices(grid.rows, grid.cols, reserved);
          page = clampPage(page, -1, pageCount(sessions.length, slotsG.length));
        }
        render();
      } else if (role === 'next') {
        if (mode === 'picker') {
          var slotsP2 = sessionSlotIndices(grid.rows, grid.cols, reserved);
          pickerPage = clampPage(pickerPage, 1, pageCount(viewsList.length, slotsP2.length));
        } else {
          var slotsG2 = sessionSlotIndices(grid.rows, grid.cols, reserved);
          page = clampPage(page, 1, pageCount(sessions.length, slotsG2.length));
        }
        render();
      } else if (role === 'view-option') {
        selectView(el.dataset.name);
      }
    }

    function openPicker() {
      mode = 'picker';
      pickerPage = 0;
      loadViewCounts();
      render();
    }

    function closePicker() {
      mode = 'grid';
      recomputeGrid(); // pick up any resize that happened while the picker had it deferred
      render();
    }

    function loadViewCounts() {
      // Best-effort enrichment only (DESIGN_SOFTDECK.md \u00a76.2's "one
      // deliberate enrichment"). A fetch failure simply leaves counts
      // blank -- it must never fabricate a number, and it must never block
      // the picker from opening.
      getJSON('/api/settings')
        .then(function (settings) {
          // "hidden" is fed through the same suffix-matching helper as a
          // one-entry pseudo-view list, rather than a bespoke count -- it's
          // membership-shaped data (settings.hidden_sessions) using the
          // exact same ":<name>" rule as settings.views (AGENTS.md).
          var lists = (settings.views || []).concat([
            { name: 'hidden', sessions: settings.hidden_sessions || [] },
          ]);
          viewCounts = viewSessionCounts(allSessionNames, lists);
          viewCounts.all = allSessionNames.length;
          if (mode === 'picker') render();
        })
        .catch(function () {
          // Leave viewCounts as-is (likely empty) -- STATE bands render blank.
        });
    }

    function selectView(name) {
      closePicker();
      patchJSON('/api/state', { active_view: name }).then(function () {
        return poll();
      });
      // Deliberately no .catch()-driven UI here: a failed view switch is
      // caught by the next poll cycle's staleness/reconciliation, same as
      // every other write on this surface -- there is no toast/banner
      // affordance on a hardware-faithful deck (DESIGN_SOFTDECK.md \u00a77).
    }

    // ── Tap-to-connect (optimistic, three layers) ──

    function connectTo(name) {
      if (!name) return;
      var previousPending = pendingName;
      pendingName = name;
      render();

      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timeoutId = setTimeout(function () {
        if (controller) controller.abort();
      }, PENDING_TIMEOUT_MS);

      postJSON('/api/sessions/' + encodeURIComponent(name) + '/connect', {}, controller ? controller.signal : undefined)
        .then(function () {
          clearTimeout(timeoutId);
          // Do not locally mark this tile "active" -- that would make the
          // client a second source of truth. The next poll (already
          // scheduled, and fired immediately here for snappier
          // reconciliation) confirms it for real.
          poll();
        })
        .catch(function () {
          clearTimeout(timeoutId);
          if (pendingName === name) {
            pendingName = previousPending;
          }
          // A failed tap stays visible: the tile itself turns FAILED (ring +
          // STATE label) for FAILED_MIN_VISIBLE_MS -- no separate toast/
          // banner chrome, matching the hardware's no-affordance-beyond-the-
          // face rule.
          failedByName[name] = Date.now() + FAILED_MIN_VISIBLE_MS;
          render();
        });
    }

    // ── Wake lock ──
    //
    // Runs silently in the background -- no visible indicator. A wake-lock
    // status chip was header chrome; the hardware has no equivalent, so
    // neither does this surface (DESIGN_SOFTDECK.md \u00a77).

    function requestWakeLock() {
      if (!('wakeLock' in navigator)) return;
      navigator.wakeLock
        .request('screen')
        .then(function (sentinel) {
          wakeSentinel = sentinel;
          sentinel.addEventListener('release', function () {
            if (wakeSentinel === sentinel) wakeSentinel = null;
          });
        })
        .catch(function () {
          // Refused (e.g. backgrounded) -- re-requested on next visibilitychange.
        });
    }

    function releaseWakeLock() {
      if (wakeSentinel) {
        var s = wakeSentinel;
        wakeSentinel = null;
        s.release().catch(function () {
          /* already released */
        });
      }
    }

    // ── Visibility handling ──

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        stopPolling();
        releaseWakeLock();
      } else {
        requestWakeLock();
        recomputeGrid();
        poll().then(schedulePoll);
      }
    });

    // ── Boot ──

    function boot() {
      recomputeGrid();
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

// ─── Exports for node --test (frontend/tests/*.mjs) ────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    classifyStaleness: classifyStaleness,
    formatAge: formatAge,
    formatLastActivity: formatLastActivity,
    previewLines: previewLines,
    tileVisualState: tileVisualState,
    computeGrid: computeGrid,
    deriveTokens: deriveTokens,
    reservedControlKeys: reservedControlKeys,
    sessionSlotIndices: sessionSlotIndices,
    pageCount: pageCount,
    pageSlice: pageSlice,
    clampPage: clampPage,
    controlKeyContent: controlKeyContent,
    pickerOptionContent: pickerOptionContent,
    viewSessionCounts: viewSessionCounts,
    fitLabel: fitLabel,
    computeKeyPlan: computeKeyPlan,
    faceClassName: faceClassName,
    findBlankControlFaces: findBlankControlFaces,
    lockLandscapeOrientation: lockLandscapeOrientation,
    registerServiceWorker: registerServiceWorker,
    POLL_INTERVAL_MS: POLL_INTERVAL_MS,
    STALE_WARN_MS: STALE_WARN_MS,
    STALE_ERR_MS: STALE_ERR_MS,
    PENDING_TIMEOUT_MS: PENDING_TIMEOUT_MS,
    FAILED_MIN_VISIBLE_MS: FAILED_MIN_VISIBLE_MS,
    PRESS_MIN_HOLD_MS: PRESS_MIN_HOLD_MS,
    S_TARGET: S_TARGET,
    S_MIN: S_MIN,
    S_MAX: S_MAX,
    N_MAX: N_MAX,
  };
}

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

// ─── Settings menu: grid override, dial strip, action catalog, bindings ────
//
// docs/BACKLOG.md item 2. Everything in this section is soft-deck-ONLY
// state, stored in localStorage (see loadDeckSettings/saveDeckSettings
// below) -- never sent to the server, never synced via /api/settings.
//
// Why local rather than synced (the backlog explicitly asks this to be
// scrutinized, not adopted on the strength of being "obvious"):
//   1. Bindings are addressed by grid position (`key.N`), and the grid
//      shape itself is viewport-derived (computeGrid) -- N has no stable
//      cross-device meaning. Syncing bindings while the grid differs per
//      device reproduces the exact "indistinguishable divergence" bug
//      class DECK_PARITY_ARCHITECTURE.md documents, self-inflicted this
//      time by a sync feature rather than an unported rule.
//   2. A single bad synced config would brick every installed soft deck
//      simultaneously -- multiplying, not containing, blast radius. That
//      directly fights the non-negotiable escape-hatch requirement below.
//   3. The real downside of local-only (lost on PWA reinstall, invisible to
//      backup) is answered directly by Export/Import (see
//      exportSettingsJSON/importSettingsJSON) rather than by taking on
//      /api/settings CAS/LWW/federation-sync complexity for a personal
//      per-screen preference.
// See the settings-panel wiring below for where this is surfaced.

var DECK_SETTINGS_KEY = 'muxplex-deck-settings';
var DECK_SETTINGS_VERSION = 1;

var DIAL_STRIP_H = 100; // px reserved for the dial strip when dialCount > 0
var DIAL_PX_PER_TICK = 24; // px of vertical drag per emitted relative tick
var DIAL_TAP_PX_THRESHOLD = 8; // below this net drag, a release is a tap (push)
var DIAL_TAP_MS_THRESHOLD = 300; // and only if it was also this quick

// Emulated touch strip (BACKLOG.md item 2 -- see the "Emulated touch strip"
// section below, which replaces the earlier "decorative, not functional"
// verdict once the actual ACTION_CATALOG was checked against the strip's
// real gesture vocabulary). STRIP_MAX_ZONES mirrors dialCount's 0-4 range
// for symmetry -- a zone is the strip's analogue of one dial.
var TOUCH_STRIP_H = 72; // px reserved for the touch strip when stripCount > 0
var STRIP_MAX_ZONES = 4;
// Reuses DIAL_PX_PER_TICK verbatim for drag-to-tick conversion (task
// guidance: "reuse the discipline rather than inventing new numbers") --
// same physical distance means the same "one tick" everywhere on the deck.
var STRIP_SWIPE_PX_THRESHOLD = 60; // net horizontal displacement of a deliberate flick
var STRIP_SWIPE_MS_THRESHOLD = 400; // must complete at least this fast to count as a swipe

var ACTION_MOMENTARY = 'momentary';
var ACTION_RELATIVE = 'relative';
// CONTINUOUS is deliberately NOT part of ACTION_CATALOG (see
// STRIP_ACTION_CATALOG below) -- it exists for exactly one soft-deck-only
// action and must never leak into the cross-repo mirrored catalog.
var ACTION_CONTINUOUS = 'continuous';

/**
 * The soft deck's action catalog -- mirrors muxplex-deck's `controls.py`
 * ACTIONS table (same 19 names, same kind per name) so the two clients
 * agree on what "view_cycle" or "page_next" means, per
 * DECK_PARITY_ARCHITECTURE.md \u00a76.2's "shared answer, independently
 * implemented, tested against a literal fixture" prescription for Layer B.
 * `label` is a soft-deck-only rendering detail (split on \n into NAME/BODY
 * for a bound key's face; hardware has its own display strings in
 * `main._control_key_display` and does not need to agree on this part).
 * See test_deck.mjs's "ACTION_CATALOG mirrors muxplex-deck's 19-action
 * catalog" fixture test for the drift tripwire.
 * @type {Object<string, {kind: string, label: string}>}
 */
var ACTION_CATALOG = {
  session: { kind: ACTION_MOMENTARY, label: '' }, // not user-bindable; see keyBindingsFromConfig
  view_picker: { kind: ACTION_MOMENTARY, label: 'VIEW\nPICKER' },
  page_picker: { kind: ACTION_MOMENTARY, label: 'PAGE\nPICKER' },
  page_prev: { kind: ACTION_MOMENTARY, label: '< PAGE\n' },
  page_next: { kind: ACTION_MOMENTARY, label: 'PAGE >\n' },
  none: { kind: ACTION_MOMENTARY, label: '' },
  view_cycle: { kind: ACTION_RELATIVE, label: 'TURN\nVIEW' },
  page_cycle: { kind: ACTION_RELATIVE, label: 'TURN\nPAGE' },
  view_all: { kind: ACTION_MOMENTARY, label: 'ALL\nVIEWS' },
  page_first: { kind: ACTION_MOMENTARY, label: 'FIRST\nPAGE' },
  page_last: { kind: ACTION_MOMENTARY, label: 'LAST\nPAGE' },
  view_prev: { kind: ACTION_MOMENTARY, label: '< VIEW\n' },
  view_next: { kind: ACTION_MOMENTARY, label: 'VIEW >\n' },
  focus_app: { kind: ACTION_MOMENTARY, label: 'FOCUS\nAPP' },
  refresh_now: { kind: ACTION_MOMENTARY, label: 'REFRESH\nNOW' },
  toggle_last: { kind: ACTION_MOMENTARY, label: 'LAST\nSESSION' },
  brightness_up: { kind: ACTION_MOMENTARY, label: 'BRIGHT\n+10%' },
  brightness_down: { kind: ACTION_MOMENTARY, label: 'BRIGHT\n-10%' },
  brightness_cycle: { kind: ACTION_RELATIVE, label: 'TURN\nBRIGHT' },
};

/**
 * A SECOND, deliberately separate catalog for the one continuous action the
 * emulated touch strip's absolute-drag gesture can drive (see the "Emulated
 * touch strip" section below for the gesture reasoning). This is NOT merged
 * into ACTION_CATALOG: that table is a byte-for-byte mirror of
 * muxplex-deck's `controls.py` ACTIONS dict, asserted exactly (name + kind)
 * by test_deck.mjs's "ACTION_CATALOG mirrors muxplex-deck..." fixture test.
 * The hardware sidecar has no continuous-value control surface today
 * (DECK_PARITY_ARCHITECTURE.md \u00a75 lists "FULL mode (dials, touch strip)" as
 * per-device, uncoordinated) and no such action in its own ACTIONS dict --
 * adding `brightness_set` to ACTION_CATALOG would silently break that
 * mirrored-19-action fixture, or force a soft-deck-only action into a table
 * whose entire purpose is cross-repo parity. Keeping it in a second table
 * makes "this is soft-deck-only" structural rather than a comment someone
 * can miss.
 * @type {Object<string, {kind: string, label: string}>}
 */
var STRIP_ACTION_CATALOG = {
  brightness_set: { kind: ACTION_CONTINUOUS, label: 'SET\nBRIGHT' },
};

/**
 * Look up an action's catalog spec regardless of which catalog it lives in
 * -- the one place that needs to know both tables exist.
 * @param {string} action
 * @returns {{kind:string, label:string}|undefined}
 */
function catalogSpecFor(action) {
  return ACTION_CATALOG[action] || STRIP_ACTION_CATALOG[action];
}

/**
 * Parse a control address string. `key.N` / `dial.N.turn` / `dial.N.push`
 * are identical grammar to muxplex-deck's `controls.py::parse_address` (no
 * leading zeros, no sign). `strip.N.tap` / `strip.N.drag` (zone-scoped) and
 * `strip.swipe.left` / `strip.swipe.right` (whole-strip, no zone index) are
 * new -- see the "Emulated touch strip" section below for why the shape is
 * `strip.N.sub` for per-zone controls (mirroring `dial.N.sub`) but
 * `strip.swipe.<direction>` for the one gesture that spans the whole strip
 * rather than a single zone (there is exactly one strip, so no index is
 * needed, the same reasoning `key.N` needs an index and a hypothetical
 * single "back" control wouldn't). These are soft-deck-only addresses with
 * no sidecar equivalent -- DECK_PARITY_ARCHITECTURE.md \u00a75 already
 * classifies dials/touch-strip as per-device, and \u00a76.2's layout fixture
 * explicitly marks its one touch-strip case "HARDWARE ONLY... structurally
 * inapplicable to the JS harness" -- so this grammar is a forward-compatible
 * superset of the shared grammar, not a divergence from it. Returns null
 * (never throws) on any grammar violation -- a live settings-editing UI
 * rejects bad input inline rather than failing loud the way a config-file
 * load does.
 * @param {string} text
 * @returns {{control:'key'|'dial'|'strip', index:number|null, sub:string|null, text:string}|null}
 */
function parseControlAddress(text) {
  if (typeof text !== 'string') return null;
  var m = /^key\.(0|[1-9][0-9]*)$/.exec(text);
  if (m) return { control: 'key', index: parseInt(m[1], 10), sub: null, text: 'key.' + m[1] };
  m = /^dial\.(0|[1-9][0-9]*)\.(turn|push)$/.exec(text);
  if (m) {
    return {
      control: 'dial',
      index: parseInt(m[1], 10),
      sub: m[2],
      text: 'dial.' + m[1] + '.' + m[2],
    };
  }
  m = /^strip\.(0|[1-9][0-9]*)\.(tap|drag)$/.exec(text);
  if (m) {
    return {
      control: 'strip',
      index: parseInt(m[1], 10),
      sub: m[2],
      text: 'strip.' + m[1] + '.' + m[2],
    };
  }
  m = /^strip\.swipe\.(left|right)$/.exec(text);
  if (m) {
    return {
      control: 'strip',
      index: null,
      sub: 'swipe-' + m[1],
      text: 'strip.swipe.' + m[1],
    };
  }
  return null;
}

/**
 * Actions legal for a given address -- mirrors
 * `controls.py::valid_actions_for_address` for `key`/`dial` (kind must
 * match; `none` is always legal regardless of kind, same judgment call
 * documented there), and extends the same rule to `strip`:
 *   - `strip.N.tap`, `strip.swipe.left/right` -- MOMENTARY (from
 *     ACTION_CATALOG), same bucket as `key.N`: a tap or a swipe fires once,
 *     just like a key press.
 *   - `strip.N.drag` -- RELATIVE (from ACTION_CATALOG, the same tick-based
 *     actions `dial.N.turn` accepts) OR CONTINUOUS (from
 *     STRIP_ACTION_CATALOG). Both are legitimate interpretations of "drag
 *     along a strip zone" -- which one applies is decided at runtime by the
 *     bound action's own kind (see the pointermove handler in the
 *     "Emulated touch strip" section), not by the address.
 * @param {{control:string, sub:?string}|null} address
 * @returns {string[]} sorted action names
 */
function validActionsForAddress(address) {
  if (!address) return [];
  if (address.control === 'strip' && address.sub === 'drag') {
    var dragNames = [];
    for (var rn in ACTION_CATALOG) {
      if (Object.prototype.hasOwnProperty.call(ACTION_CATALOG, rn) && ACTION_CATALOG[rn].kind === ACTION_RELATIVE) {
        dragNames.push(rn);
      }
    }
    for (var cn in STRIP_ACTION_CATALOG) {
      if (
        Object.prototype.hasOwnProperty.call(STRIP_ACTION_CATALOG, cn) &&
        STRIP_ACTION_CATALOG[cn].kind === ACTION_CONTINUOUS
      ) {
        dragNames.push(cn);
      }
    }
    if (dragNames.indexOf('none') === -1) dragNames.push('none');
    return dragNames.sort();
  }
  var wantKind = address.control === 'dial' && address.sub === 'turn' ? ACTION_RELATIVE : ACTION_MOMENTARY;
  var names = [];
  for (var name in ACTION_CATALOG) {
    if (Object.prototype.hasOwnProperty.call(ACTION_CATALOG, name) && ACTION_CATALOG[name].kind === wantKind) {
      names.push(name);
    }
  }
  if (names.indexOf('none') === -1) names.push('none');
  return names.sort();
}

/**
 * Full validation of one (address, action) pair -- parse + catalog
 * membership (either catalog, via catalogSpecFor) + kind match. Used by
 * both `sanitizeBindings` and the settings panel's live add-binding form.
 * @param {string} addressText
 * @param {string} action
 * @returns {boolean}
 */
function isValidBinding(addressText, action) {
  var address = parseControlAddress(addressText);
  if (!address) return false;
  if (typeof action !== 'string' || !catalogSpecFor(action)) return false;
  return validActionsForAddress(address).indexOf(action) !== -1;
}

/**
 * Filter a raw (possibly hand-edited or imported) bindings object down to
 * only valid entries. Fails soft -- invalid entries are dropped silently
 * (the caller surfaces a count/diff if it wants to), never thrown, since
 * this runs on every settings load, not just a one-time config-file read.
 * @param {object} raw
 * @returns {Object<string,string>}
 */
function sanitizeBindings(raw) {
  var out = {};
  if (raw && typeof raw === 'object') {
    for (var key in raw) {
      if (!Object.prototype.hasOwnProperty.call(raw, key)) continue;
      if (isValidBinding(key, raw[key])) out[key] = raw[key];
    }
  }
  return out;
}

/**
 * Extract just the `key.N` overrides from a bindings map, filtered to
 * in-range indices and excluding the `session` action (which means "no
 * override, behave as a normal auto-assigned session tile" -- the same
 * thing an absent entry means, so it is never treated as a real bound
 * face). Result indices are excluded from `sessionSlotIndices`'s pool and
 * rendered via `actionKeyContent` instead of a session tile.
 * @param {Object<string,string>} bindings
 * @param {number} keyCount
 * @returns {Object<number,string>}
 */
function keyBindingsFromConfig(bindings, keyCount) {
  var out = {};
  for (var addr in bindings) {
    if (!Object.prototype.hasOwnProperty.call(bindings, addr)) continue;
    var a = parseControlAddress(addr);
    if (!a || a.control !== 'key') continue;
    if (a.index < 0 || a.index >= keyCount) continue;
    if (bindings[addr] === 'session') continue;
    out[a.index] = bindings[addr];
  }
  return out;
}

/**
 * Extract `dial.N.turn`/`dial.N.push` bindings into a dense array of
 * `{turn, push}` (default `'none'`), one entry per configured dial.
 * @param {Object<string,string>} bindings
 * @param {number} dialCount
 * @returns {Array<{turn:string, push:string}>}
 */
function dialBindingsFromConfig(bindings, dialCount) {
  var out = [];
  for (var i = 0; i < dialCount; i++) out.push({ turn: 'none', push: 'none' });
  for (var addr in bindings) {
    if (!Object.prototype.hasOwnProperty.call(bindings, addr)) continue;
    var a = parseControlAddress(addr);
    if (!a || a.control !== 'dial') continue;
    if (a.index < 0 || a.index >= dialCount) continue;
    out[a.index][a.sub] = bindings[addr];
  }
  return out;
}

/**
 * Extract `strip.N.tap`/`strip.N.drag` bindings into a dense array of
 * `{tap, drag}` (default `'none'`), one entry per configured zone -- the
 * touch-strip twin of `dialBindingsFromConfig`.
 * @param {Object<string,string>} bindings
 * @param {number} zoneCount
 * @returns {Array<{tap:string, drag:string}>}
 */
function stripZoneBindingsFromConfig(bindings, zoneCount) {
  var out = [];
  for (var i = 0; i < zoneCount; i++) out.push({ tap: 'none', drag: 'none' });
  for (var addr in bindings) {
    if (!Object.prototype.hasOwnProperty.call(bindings, addr)) continue;
    var a = parseControlAddress(addr);
    if (!a || a.control !== 'strip' || a.index == null) continue;
    if (a.index < 0 || a.index >= zoneCount) continue;
    out[a.index][a.sub] = bindings[addr];
  }
  return out;
}

/**
 * Extract the two whole-strip `strip.swipe.left` / `strip.swipe.right`
 * bindings -- unlike zone bindings these have no index (there is exactly
 * one strip), so the result is a single `{left, right}` pair rather than a
 * dense array.
 * @param {Object<string,string>} bindings
 * @returns {{left:string, right:string}}
 */
function stripSwipeBindingsFromConfig(bindings) {
  var out = { left: 'none', right: 'none' };
  for (var addr in bindings) {
    if (!Object.prototype.hasOwnProperty.call(bindings, addr)) continue;
    var a = parseControlAddress(addr);
    if (!a || a.control !== 'strip' || a.index != null) continue;
    if (a.sub === 'swipe-left') out.left = bindings[addr];
    if (a.sub === 'swipe-right') out.right = bindings[addr];
  }
  return out;
}

/**
 * Content for a key face whose slot is bound to a fixed action (as opposed
 * to an auto-assigned session tile). NAME/BODY come from the catalog's
 * `label` (split on the one `\n`); STATE is always blank -- action keys
 * carry no per-poll enrichment the way a session tile's last-activity does.
 * @param {string} action
 * @returns {{name:string, body:string, state:string}}
 */
function actionKeyContent(action) {
  var spec = ACTION_CATALOG[action];
  if (!spec) return { name: '', body: '', state: '' };
  var parts = (spec.label || '').split('\n');
  return { name: parts[0] || '', body: parts[1] || '', state: '' };
}

/**
 * Ascending page/view labels for the generic item picker's "page" flavor
 * (`pickerKind: 'page'` in computeKeyPlan) -- "Page 1".."Page N", 1-indexed
 * for humans even though `page` state is 0-indexed everywhere else.
 * @param {number} n
 * @returns {string[]}
 */
function pageItemLabels(n) {
  var out = [];
  for (var i = 0; i < n; i++) out.push('Page ' + (i + 1));
  return out;
}

/**
 * Content for a page-picker option key -- structurally the picker-option
 * twin of `pickerOptionContent`, but simpler: no per-item session count,
 * just whether this is the current page.
 * @param {string} label
 * @param {boolean} isCurrent
 * @returns {{name:string, body:string, state:string}}
 */
function pageOptionContent(label, isCurrent) {
  return { name: '', body: label, state: isCurrent ? 'current' : '' };
}

/**
 * Vertical-drag-to-ticks for an emulated dial (DESIGN: a rotary-angle drag
 * on a small touch target is fiddly and error-prone to get right and to
 * test; a vertical scrub gesture -- the same convention as an iOS picker
 * wheel -- is unambiguous, and reduces to a pure function of pixel delta).
 * Upward drag (negative deltaY) yields positive ticks, matching "turn the
 * dial up/clockwise to increase" for every RELATIVE action's natural
 * direction (next view, next page, brighter).
 * @param {number} deltaYpx
 * @returns {number} signed integer tick count (may be 0)
 */
function dialDragTicks(deltaYpx) {
  // `|| 0` squashes a possible -0 result (e.g. Math.trunc(-0 / N) or a tiny
  // negative deltaYpx below one tick) -- a negative-zero tick count is
  // semantically meaningless and would fail a strict (Object.is-based)
  // equality check against the plain 0 callers expect for "no tick".
  return (-Math.trunc(deltaYpx / DIAL_PX_PER_TICK) || 0);
}

/**
 * Whether a completed drag on a dial should be treated as a tap (push
 * action) rather than a turn (turn action already fired incrementally
 * during the drag via dialDragTicks) -- both a small net displacement AND
 * a short duration, so a slow, small, deliberate turn isn't misread as a
 * push.
 * @param {number} deltaYpx - net displacement over the whole gesture
 * @param {number} elapsedMs
 * @returns {boolean}
 */
function isDialTap(deltaYpx, elapsedMs) {
  return Math.abs(deltaYpx) < DIAL_TAP_PX_THRESHOLD && elapsedMs < DIAL_TAP_MS_THRESHOLD;
}

/**
 * Horizontal-drag-to-ticks for an emulated touch-strip zone -- the strip's
 * twin of `dialDragTicks`, generalized to a horizontal surface per the
 * task's own suggestion ("dialDragTicks may generalize"). Rightward drag
 * (positive deltaX) yields positive ticks: the natural reading-direction
 * convention for "forward" (next view, next page, brighter), the
 * horizontal equivalent of the dial's "up = increase". Deliberately NOT a
 * sign-flip of dialDragTicks -- right and up are both "the positive
 * direction" on their own axis, so the two functions differ only in which
 * axis they read, not in a hidden orientation inversion.
 * @param {number} deltaXpx
 * @returns {number} signed integer tick count (may be 0)
 */
function stripDragTicks(deltaXpx) {
  return Math.trunc(deltaXpx / DIAL_PX_PER_TICK) || 0; // `|| 0` squashes -0, see dialDragTicks
}

/**
 * Whether a completed strip gesture is a deliberate flick (swipe) -- large
 * AND fast, the opposite shape from `isDialTap` (small AND fast). A slow
 * drag across the same distance is NOT a swipe, by design: swipe requires
 * speed, which is what distinguishes "flick to page over" from "scrub
 * through a continuous/relative range." Only meaningful for a zone whose
 * `drag` binding is unbound (`none`) -- see the "Emulated touch strip"
 * section for why a bound drag zone never reaches this check at all
 * (its gesture is already fully consumed, progressively, as ticks or an
 * absolute value).
 * @param {number} deltaXpx - net horizontal displacement over the whole gesture
 * @param {number} elapsedMs
 * @returns {boolean}
 */
function isStripSwipe(deltaXpx, elapsedMs) {
  return Math.abs(deltaXpx) >= STRIP_SWIPE_PX_THRESHOLD && elapsedMs <= STRIP_SWIPE_MS_THRESHOLD;
}

/**
 * Absolute touch position along a strip zone as a clamped [0,1] fraction --
 * the input to the strip's one CONTINUOUS action (`brightness_set`). Pure:
 * takes the already-measured zone rect rather than touching the DOM itself,
 * so it's testable under `node --test` with a fake rect.
 * @param {number} clientX
 * @param {number} rectLeft
 * @param {number} rectWidth
 * @returns {number}
 */
function stripAbsoluteFraction(clientX, rectLeft, rectWidth) {
  if (!(rectWidth > 0)) return 0;
  var f = (clientX - rectLeft) / rectWidth;
  if (f < 0) f = 0;
  if (f > 1) f = 1;
  return f;
}

/**
 * Apply an absolute [0,1] fraction to the strip's one CONTINUOUS action --
 * the touch-strip's canonical "set brightness by touch position" gesture
 * (hardware Stream Deck+ touch-strip's own signature use), and the
 * CONTINUOUS counterpart of `applyRelativeTicks`. `fraction` maps linearly
 * onto the existing [10,100] brightness range (never onto [0,100] --
 * brightness_up/down/cycle already enforce a 10% floor, unreachable-off is
 * deliberate, see setBrightness's own clamp).
 * @param {string} action - 'brightness_set' (others are no-ops)
 * @param {number} fraction - 0..1
 * @returns {object} partial update, e.g. {brightness: n} or {} if the action doesn't apply
 */
function applyContinuousValue(action, fraction) {
  if (action === 'brightness_set') {
    var v = Math.round(10 + fraction * 90);
    if (v > 100) v = 100;
    if (v < 10) v = 10;
    return { brightness: v };
  }
  return {};
}

/**
 * Apply a signed relative tick count to one of the three RELATIVE actions.
 * Pure -- reads/writes only the small `ctx` slice relevant to the action,
 * returns a partial-update object the caller merges into real state. All
 * three "cycle" actions clamp (never wrap), matching the existing
 * page_prev/page_next convention (CONTROL_MAPPING_DESIGN.md \u00a72.1) --
 * applied uniformly here rather than inventing a second (wrapping)
 * convention for view/brightness.
 * @param {string} action - 'page_cycle' | 'view_cycle' | 'brightness_cycle' (others are no-ops)
 * @param {number} ticks
 * @param {{page:number, pageCount:number, viewIndex:number, viewCount:number, brightness:number}} ctx
 * @returns {object} partial update, e.g. {page: n} or {} if the action doesn't apply or ticks is 0
 */
function applyRelativeTicks(action, ticks, ctx) {
  if (!ticks) return {};
  if (action === 'page_cycle') {
    return { page: clampPage(ctx.page, ticks, ctx.pageCount) };
  }
  if (action === 'view_cycle') {
    return { viewIndex: clampPage(ctx.viewIndex, ticks, ctx.viewCount) };
  }
  if (action === 'brightness_cycle') {
    var next = ctx.brightness + ticks * 10;
    if (next > 100) next = 100;
    if (next < 10) next = 10;
    return { brightness: next };
  }
  return {};
}

/**
 * The soft deck's own default settings -- see the section header above for
 * why these live in localStorage rather than server-synced settings.
 * @returns {object}
 */
function defaultDeckSettings() {
  return {
    version: DECK_SETTINGS_VERSION,
    sort: 'attention',
    pollIntervalMs: POLL_INTERVAL_MS,
    gridOverride: null, // {rows, cols} | null (null = auto, computeGrid)
    dialCount: 0, // 0-4
    stripCount: 0, // 0-4 touch-strip zones (independent of dialCount)
    brightness: 100, // 10-100, applied as a CSS filter on the whole surface
    bindings: {}, // address (key.N | dial.N.turn | dial.N.push | strip.N.tap | strip.N.drag | strip.swipe.left | strip.swipe.right) -> action
  };
}

/**
 * Merge a possibly-partial, possibly-hostile `incoming` object onto
 * defaults, validating every field individually -- an out-of-range or
 * wrong-typed field is silently dropped in favor of its default rather
 * than rejecting the whole object (an import/localStorage read should
 * recover as much of a partially-valid settings blob as it safely can).
 * @param {object} defaults - defaultDeckSettings(), or another rebase point
 * @param {object} incoming
 * @returns {object}
 */
function mergeDeckSettings(defaults, incoming) {
  var out = defaultDeckSettings();
  if (!incoming || typeof incoming !== 'object') return out;
  if (incoming.sort === 'attention' || incoming.sort === 'server') out.sort = incoming.sort;
  if (
    typeof incoming.pollIntervalMs === 'number' &&
    incoming.pollIntervalMs >= 500 &&
    incoming.pollIntervalMs <= 60000
  ) {
    out.pollIntervalMs = incoming.pollIntervalMs;
  }
  if (
    incoming.gridOverride &&
    typeof incoming.gridOverride === 'object' &&
    Number.isInteger(incoming.gridOverride.rows) &&
    Number.isInteger(incoming.gridOverride.cols) &&
    incoming.gridOverride.rows >= 1 &&
    incoming.gridOverride.rows <= 12 &&
    incoming.gridOverride.cols >= 1 &&
    incoming.gridOverride.cols <= 12 &&
    incoming.gridOverride.rows * incoming.gridOverride.cols <= N_MAX
  ) {
    out.gridOverride = { rows: incoming.gridOverride.rows, cols: incoming.gridOverride.cols };
  }
  if (Number.isInteger(incoming.dialCount) && incoming.dialCount >= 0 && incoming.dialCount <= 4) {
    out.dialCount = incoming.dialCount;
  }
  if (Number.isInteger(incoming.stripCount) && incoming.stripCount >= 0 && incoming.stripCount <= STRIP_MAX_ZONES) {
    out.stripCount = incoming.stripCount;
  }
  if (Number.isInteger(incoming.brightness) && incoming.brightness >= 10 && incoming.brightness <= 100) {
    out.brightness = incoming.brightness;
  }
  out.bindings = sanitizeBindings(incoming.bindings);
  return out;
}

/**
 * Load deck settings from a storage-like object (`localStorage`-shaped:
 * `getItem`/`setItem`). `storage` is injectable so this is testable under
 * `node --test` with no real `localStorage` -- passing `null`/`undefined`
 * (as node --test does) returns defaults, exactly like a fresh install.
 * @param {{getItem:function(string):?string}|null|undefined} storage
 * @returns {object}
 */
function loadDeckSettings(storage) {
  var defaults = defaultDeckSettings();
  if (!storage) return defaults;
  var raw;
  try {
    raw = storage.getItem(DECK_SETTINGS_KEY);
  } catch (e) {
    return defaults;
  }
  if (!raw) return defaults;
  var parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    return defaults;
  }
  return mergeDeckSettings(defaults, parsed);
}

/**
 * Persist deck settings. Best-effort: a full/unavailable storage (e.g.
 * private browsing) is swallowed, never thrown -- losing a settings write
 * must not break the deck itself.
 * @param {{setItem:function(string,string):void}|null|undefined} storage
 * @param {object} settings
 */
function saveDeckSettings(storage, settings) {
  if (!storage) return;
  try {
    storage.setItem(DECK_SETTINGS_KEY, JSON.stringify(settings));
  } catch (e) {
    // best-effort; see docstring
  }
}

/**
 * Serialize settings for the settings panel's Export field -- the backup
 * story for a local-only, per-device settings model (see section header).
 * @param {object} settings
 * @returns {string}
 */
function exportSettingsJSON(settings) {
  return JSON.stringify(settings, null, 2);
}

/**
 * Parse + validate a pasted settings blob for the Import field. Never
 * throws -- returns `{settings: null, error: <message>}` on bad JSON, or
 * `{settings: <merged>, error: null}` on success (individual bad fields
 * inside otherwise-valid JSON are dropped by `mergeDeckSettings`, not
 * rejected wholesale -- same recovery posture as `loadDeckSettings`).
 * @param {string} text
 * @param {object} [defaults]
 * @returns {{settings:?object, error:?string}}
 */
function importSettingsJSON(text, defaults) {
  var parsed;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    return { settings: null, error: 'Invalid JSON: ' + e.message };
  }
  return { settings: mergeDeckSettings(defaults || defaultDeckSettings(), parsed), error: null };
}

/**
 * Fixed-shape grid variant of `computeGrid` -- used when the user has set
 * an explicit rows/cols override (DESIGN: "Layout as a setting... the
 * clearest example of a setting that is inherently per-device",
 * BACKLOG.md item 2). Skips the S-search loop entirely (rows/cols are
 * already decided) and reuses the same fill/letterbox tail computeGrid
 * uses once ITS search settles, so the two paths agree on what "S" and
 * "letterboxed" mean for a given cell size.
 * @param {number} contentW
 * @param {number} contentH
 * @param {number} rows
 * @param {number} cols
 * @returns {{rows:number, cols:number, cellW:number, cellH:number, s:number,
 *            gap:number, letterboxed:boolean, tooSmall:boolean}}
 */
function computeGridForShape(contentW, contentH, rows, cols) {
  if (!(contentW > 0) || !(contentH > 0) || !(rows > 0) || !(cols > 0)) {
    return { rows: 0, cols: 0, cellW: 0, cellH: 0, s: 0, gap: 0, letterboxed: false, tooSmall: true };
  }
  var gap = Math.round(S_TARGET / 8);
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
 * The grid the deck actually renders: the user's explicit override when
 * set and valid, otherwise the ordinary viewport-derived `computeGrid`.
 * @param {number} contentW
 * @param {number} contentH
 * @param {{rows:number, cols:number}|null} override
 * @returns {ReturnType<typeof computeGrid>}
 */
function computeEffectiveGrid(contentW, contentH, override) {
  if (override && override.rows > 0 && override.cols > 0) {
    return computeGridForShape(contentW, contentH, override.rows, override.cols);
  }
  return computeGrid(contentW, contentH);
}

/**
 * Shrink a content box to make room for the dial strip when `dialCount` is
 * configured -- called BEFORE `computeEffectiveGrid` so the key grid never
 * overlaps the dial strip; a dialCount of 0 (the default) is a no-op, so
 * every existing device with no dials configured sees byte-identical
 * geometry to before this feature existed.
 * @param {{w:number, h:number}} box
 * @param {number} dialCount
 * @returns {{w:number, h:number}}
 */
function contentBoxForDials(box, dialCount) {
  if (!dialCount || dialCount <= 0) return box;
  return { w: box.w, h: Math.max(0, box.h - DIAL_STRIP_H) };
}

/**
 * The touch-strip twin of `contentBoxForDials` -- shrinks a content box to
 * make room for the emulated touch strip when `stripCount` is configured.
 * Independent of dial reservation: the two compose by calling both in
 * sequence (see `recomputeGrid`), so a deck with both dials and a strip
 * reserves both heights, a deck with only one reserves only that one, and
 * a deck with neither (the default) sees byte-identical geometry to before
 * either feature existed.
 * @param {{w:number, h:number}} box
 * @param {number} stripCount
 * @returns {{w:number, h:number}}
 */
function contentBoxForStrip(box, stripCount) {
  if (!stripCount || stripCount <= 0) return box;
  return { w: box.w, h: Math.max(0, box.h - TOUCH_STRIP_H) };
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
 * 0..key_count-1 that isn't one of the three reserved control keys, AND
 * (new) isn't explicitly bound to a fixed action via the settings menu's
 * bindings (`keyBindingsFromConfig`). `boundIndices` is optional and
 * defaults to excluding nothing -- every pre-existing caller (and every
 * existing test) that passes only 3 arguments is unaffected.
 * @param {number} rows
 * @param {number} cols
 * @param {{view:number|null, prev:number|null, next:number|null}} reserved
 * @param {Object<number,string>} [boundIndices] - index -> action, from keyBindingsFromConfig
 * @returns {number[]}
 */
function sessionSlotIndices(rows, cols, reserved, boundIndices) {
  var reservedSet = {};
  if (reserved.view != null) reservedSet[reserved.view] = true;
  if (reserved.prev != null) reservedSet[reserved.prev] = true;
  if (reserved.next != null) reservedSet[reserved.next] = true;
  var bound = boundIndices || {};
  var slots = [];
  for (var i = 0; i < rows * cols; i++) {
    if (!reservedSet[i] && !(i in bound)) slots.push(i);
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
 *            role: 'session'|'view'|'prev'|'next'|'back'|'view-option'|'bound'|'empty',
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
  if (role === 'bound') return 'is-bound';
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
 * @param {string[]} p.viewsList - browsable view names (picker mode, pickerKind='view')
 * @param {'view'|'page'} [p.pickerKind] - which generic-picker flavor is showing (default 'view')
 * @param {number} p.page - current session grid page
 * @param {number} p.pickerPage - current view/page-picker page
 * @param {Object<string, number>} [p.viewCounts] - picker STATE enrichment
 * @param {string|null} [p.pendingName]
 * @param {Object<string, number>} [p.failedByName] - name -> expiry epoch ms
 * @param {Object<string, string>} [p.snapshots] - name -> pane text
 * @param {number} [p.previewLinesMax]
 * @param {Object<number,string>} [p.boundKeys] - index -> action, from keyBindingsFromConfig
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
  var boundKeys = p.boundKeys || {};

  var slots = sessionSlotIndices(rows, cols, reserved, boundKeys);

  // Bound-action faces render identically whether the deck is in grid or
  // picker mode -- a fixed action key doesn't stop being fixed just
  // because the view picker is open over the session slots around it.
  for (var bi in boundKeys) {
    if (!Object.prototype.hasOwnProperty.call(boundKeys, bi)) continue;
    var boundIndex = Number(bi);
    var action = boundKeys[bi];
    var boundContent = actionKeyContent(action);
    plan[boundIndex] = {
      index: boundIndex,
      role: 'bound',
      name: boundContent.name,
      body: boundContent.body,
      state: boundContent.state,
      preview: '',
      target: action,
      flags: _emptyFlags(),
    };
  }

  if (p.mode === 'picker') {
    var pickerKind = p.pickerKind || 'view';
    var isPagePicker = pickerKind === 'page';
    var viewsList = p.viewsList || [];
    var pageLabels = isPagePicker ? pageItemLabels(p.pagePickerCount != null ? p.pagePickerCount : 1) : [];
    var itemsList = isPagePicker ? pageLabels : viewsList;
    var pc = pageCount(itemsList.length, slots.length);
    pickerPage = _clampToCount(pickerPage, pc);
    var pageItems = pageSlice(itemsList, pickerPage, slots.length);
    var pagePosition = pc > 1 ? pickerPage + 1 + '/' + pc : '';

    if (hasControls) {
      _setControlFace(plan, reserved.view, 'back', controlKeyContent('back', {}));
      _setControlFace(plan, reserved.prev, 'prev', controlKeyContent('prev', { pagePosition: pagePosition }));
      _setControlFace(plan, reserved.next, 'next', controlKeyContent('next', { pagePosition: pagePosition }));
    }

    for (var vi = 0; vi < slots.length; vi++) {
      var name = pageItems[vi];
      if (!name) continue;
      var content;
      var target;
      var isCurrentItem;
      if (isPagePicker) {
        var itemIndex = pickerPage * slots.length + vi;
        isCurrentItem = itemIndex === p.page;
        content = pageOptionContent(name, isCurrentItem);
        target = String(itemIndex);
      } else {
        var count = p.viewCounts && p.viewCounts[name] != null ? p.viewCounts[name] : null;
        isCurrentItem = name === p.viewName;
        content = pickerOptionContent(name, count);
        target = name;
      }
      plan[slots[vi]] = {
        index: slots[vi],
        role: 'view-option',
        name: content.name,
        body: content.body,
        state: content.state,
        preview: '',
        target: target,
        flags: _mergeFlags({ currentView: isCurrentItem }),
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

    var mode = 'grid'; // 'grid' | 'picker' | 'settings'
    var pickerKind = 'view'; // 'view' | 'page' -- which generic-picker flavor is open
    var page = 0; // current session grid page
    var pickerPage = 0; // current view/page-picker page
    var grid = null; // last computeGrid()/computeEffectiveGrid() result
    var reserved = null; // last reservedControlKeys() result
    var tokens = null; // last deriveTokens() result -- feeds the label-measure font/box
    var keyEls = []; // R*C key <button> elements, index-addressed
    var currentShape = null; // 'RxC' string, to detect when a rebuild is needed
    var viewCounts = {}; // best-effort enrichment, see loadViewCounts()
    var lastActiveName = null; // previously-active session, for the toggle_last action

    // ── Settings menu state (BACKLOG.md item 2) -- see the deck.js pure-logic
    // section for why this is local (localStorage), not server-synced. ──
    var deckSettings = loadDeckSettings(safeLocalStorage());
    var boundKeys = keyBindingsFromConfig(deckSettings.bindings, 0); // recomputed per recomputeGrid
    var dialBindings = dialBindingsFromConfig(deckSettings.bindings, deckSettings.dialCount);
    var dialEls = [];
    var dialStripEl = document.getElementById('deck-dial-strip');
    var stripZoneBindings = stripZoneBindingsFromConfig(deckSettings.bindings, deckSettings.stripCount);
    var stripSwipeBindings = stripSwipeBindingsFromConfig(deckSettings.bindings);
    var stripZoneEls = [];
    var stripStripEl = document.getElementById('deck-touch-strip');
    var stripSwipeLeftLabelEl = document.getElementById('deck-strip-swipe-left');
    var stripSwipeRightLabelEl = document.getElementById('deck-strip-swipe-right');
    var settingsEl = document.getElementById('deck-settings');

    /**
     * `localStorage` is unavailable (throws) in some private-browsing modes
     * and inside a sandboxed iframe -- probe once at boot rather than
     * letting every read/write site guard separately.
     * @returns {Storage|null}
     */
    function safeLocalStorage() {
      try {
        var key = '__muxplex_deck_probe__';
        window.localStorage.setItem(key, '1');
        window.localStorage.removeItem(key);
        return window.localStorage;
      } catch (e) {
        return null;
      }
    }
    var storage = safeLocalStorage();

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
      // The sort param now follows the soft deck's OWN local `sort` setting
      // (BACKLOG.md item 2), not a hardcoded value -- mirrors muxplex-deck's
      // own `config.sort`, which is ALSO local sidecar config, independent
      // of the dashboard's server-synced `sort_order` (settings.py). This
      // surface deliberately does NOT follow the dashboard's `sort_order`:
      // the two have always been separate knobs for separate clients (the
      // hardware sidecar ignores `sort_order` too), and per-screen sort
      // preference is exactly the kind of thing that's cheap to lose on
      // reinstall and genuinely per-device (DECK_PARITY_ARCHITECTURE.md
      // \u00a72.1/\u00a76.1: which sessions/order is Layer A -- one server-owned
      // ANSWER -- but WHICH of the server's answers a client requests is a
      // client preference, same as muxplex-deck's own `sort` field).
      var sortParam = deckSettings.sort === 'server' ? '' : '?sort=attention';
      var viewReq = getJSON('/api/view' + sortParam);
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
          // Track the previously-active session for the toggle_last action
          // -- only when it's a real change to a different, non-null prior
          // value, so the very first poll (activeName still null) doesn't
          // record a bogus "previous" session.
          if (newActive !== activeName && activeName != null) {
            lastActiveName = activeName;
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

    function applyBrightness() {
      // A soft analogue of the hardware's LED-backlight brightness action:
      // dims the whole deck surface via a CSS filter. Meaningful for the
      // "phone left on a desk overnight" case BACKLOG.md item 2 names.
      root.style.filter = deckSettings.brightness >= 100 ? '' : 'brightness(' + deckSettings.brightness / 100 + ')';
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
      // Entry point to Settings (BACKLOG.md item 2 -- "subtle and out of the
      // way"): long-press on the VIEW control key. Chosen over a dedicated
      // gear icon because it spends NO key slot by default -- the icon
      // approach costs one of a small, precious grid; long-press reuses a
      // key that already exists in every non-degenerate layout. Only armed
      // when this key's CURRENT role is 'view' (checked live via
      // dataset.role, which paintKeyFace keeps current) -- long-pressing a
      // session tile must never accidentally open settings.
      var longPressTimer = null;
      var longPressFired = false;
      var LONG_PRESS_MS = 600;
      key.addEventListener('pointerdown', function () {
        pressedAt = Date.now();
        key.classList.add('is-pressed');
        longPressFired = false;
        if (key.dataset.role === 'view') {
          longPressTimer = setTimeout(function () {
            longPressFired = true;
            openSettings();
          }, LONG_PRESS_MS);
        }
      });
      var releasePress = function () {
        var held = Date.now() - pressedAt;
        var wait = Math.max(0, PRESS_MIN_HOLD_MS - held);
        if (pressTimer) clearTimeout(pressTimer);
        pressTimer = setTimeout(function () {
          key.classList.remove('is-pressed');
        }, wait);
        if (longPressTimer) {
          clearTimeout(longPressTimer);
          longPressTimer = null;
        }
      };
      key.addEventListener('pointerup', releasePress);
      key.addEventListener('pointercancel', function () {
        key.classList.remove('is-pressed');
        if (longPressTimer) {
          clearTimeout(longPressTimer);
          longPressTimer = null;
        }
      });
      key.addEventListener('click', function () {
        if (longPressFired) {
          // The long-press already dispatched (opened settings); suppress
          // the click that follows the same physical press/release.
          longPressFired = false;
          return;
        }
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
      var box = contentBoxForDials(contentBoxForStrip(contentBox(), deckSettings.stripCount), deckSettings.dialCount);
      var g = computeEffectiveGrid(box.w, box.h, deckSettings.gridOverride);
      grid = g;
      reserved = reservedControlKeys(g.rows, g.cols);
      tokens = deriveTokens(g.s, g.cellH);
      applyGridTokens(g, tokens);
      rebuildGridIfNeeded(g);
      root.classList.toggle('too-small', !!g.tooSmall);
      boundKeys = keyBindingsFromConfig(deckSettings.bindings, g.rows * g.cols);
      dialBindings = dialBindingsFromConfig(deckSettings.bindings, deckSettings.dialCount);
      rebuildDialStripIfNeeded();
      stripZoneBindings = stripZoneBindingsFromConfig(deckSettings.bindings, deckSettings.stripCount);
      stripSwipeBindings = stripSwipeBindingsFromConfig(deckSettings.bindings);
      rebuildTouchStripIfNeeded();
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
      var slotsNow = sessionSlotIndices(grid.rows, grid.cols, reserved, boundKeys);
      var result = computeKeyPlan({
        grid: grid,
        reserved: reserved,
        mode: mode,
        pickerKind: pickerKind,
        sessions: sessions,
        viewName: viewName,
        viewsList: viewsList,
        page: page,
        pickerPage: pickerPage,
        pagePickerCount: pageCount(sessions.length, slotsNow.length),
        viewCounts: viewCounts,
        pendingName: pendingName,
        failedByName: failedByName,
        snapshots: snapshots,
        previewLinesMax: PREVIEW_LINES_MAX,
        boundKeys: boundKeys,
        nowMs: Date.now(),
      });
      page = result.page;
      pickerPage = result.pickerPage;

      var measure = buildMeasureContext();
      for (var i = 0; i < keyEls.length; i++) {
        paintKeyFace(keyEls[i], result.plan[i], measure);
      }
      renderDialLabels();
      renderStripLabels();
    }

    function render() {
      if (mode === 'settings') return; // settings panel owns the surface entirely
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
      } else if (role === 'bound') {
        dispatchAction(el.dataset.name);
      } else if (role === 'view') {
        openPicker();
      } else if (role === 'back') {
        closePicker();
      } else if (role === 'prev') {
        pageTurn(-1);
      } else if (role === 'next') {
        pageTurn(1);
      } else if (role === 'view-option') {
        if (pickerKind === 'page') {
          selectPage(parseInt(el.dataset.name, 10));
        } else {
          selectView(el.dataset.name);
        }
      }
    }

    /**
     * Shared page/picker-turn logic for the 'prev'/'next' control keys AND
     * the page_prev/page_next bound actions -- kept as one function so the
     * two entry points can never drift on the clamped-never-wrapping rule.
     * @param {number} delta -1 or 1
     */
    function pageTurn(delta) {
      if (mode === 'picker') {
        var itemsLen = pickerKind === 'page' ? pageCount(sessions.length, sessionSlotIndices(grid.rows, grid.cols, reserved, boundKeys).length) : viewsList.length;
        var slotsP = sessionSlotIndices(grid.rows, grid.cols, reserved, boundKeys);
        pickerPage = clampPage(pickerPage, delta, pageCount(itemsLen, slotsP.length));
      } else {
        var slotsG = sessionSlotIndices(grid.rows, grid.cols, reserved, boundKeys);
        page = clampPage(page, delta, pageCount(sessions.length, slotsG.length));
      }
      render();
    }

    function openPicker() {
      mode = 'picker';
      pickerKind = 'view';
      pickerPage = 0;
      loadViewCounts();
      render();
    }

    /** page_picker action -- the generic-picker twin of openPicker for pages. */
    function openPagePicker() {
      mode = 'picker';
      pickerKind = 'page';
      pickerPage = 0;
      render();
    }

    function closePicker() {
      mode = 'grid';
      pickerKind = 'view';
      recomputeGrid(); // pick up any resize that happened while the picker had it deferred
      render();
    }

    /** page-picker option tap: jump straight to the tapped page. */
    function selectPage(index) {
      page = index;
      closePicker();
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

    // ── Action dispatch (bound keys + dials) ──
    //
    // Central handler for every action in ACTION_CATALOG reached via a
    // bound key, a dial push, or a dial turn (via applyRelativeTicks for
    // the three RELATIVE actions). This is what makes the emulated dials'
    // relative actions (view_cycle/page_cycle/brightness_cycle) actually
    // reachable -- BACKLOG.md item 2's core argument for building dials at
    // all: "the relative actions exist and currently have nowhere to live
    // on a device with no dials."

    function dispatchAction(action) {
      var slots = sessionSlotIndices(grid.rows, grid.cols, reserved, boundKeys);
      switch (action) {
        case 'view_picker':
          if (mode === 'picker') closePicker();
          else openPicker();
          return;
        case 'page_picker':
          if (mode === 'picker') closePicker();
          else openPagePicker();
          return;
        case 'page_prev':
          pageTurn(-1);
          return;
        case 'page_next':
          pageTurn(1);
          return;
        case 'page_first':
          page = 0;
          render();
          return;
        case 'page_last':
          page = pageCount(sessions.length, slots.length) - 1;
          render();
          return;
        case 'view_all':
          if (viewsList.indexOf('all') !== -1) selectView('all');
          return;
        case 'view_prev':
          stepView(-1);
          return;
        case 'view_next':
          stepView(1);
          return;
        case 'refresh_now':
          poll();
          return;
        case 'toggle_last':
          if (lastActiveName) connectTo(lastActiveName);
          return;
        case 'brightness_up':
          setBrightness(deckSettings.brightness + 10);
          return;
        case 'brightness_down':
          setBrightness(deckSettings.brightness - 10);
          return;
        case 'focus_app':
          // Not yet implemented -- BACKLOG.md item 3 moves focus-grabbing
          // server-side so any client (including this one, over the
          // network) can request it. Until that lands this is a documented
          // no-op rather than a silent dead binding.
          if (typeof console !== 'undefined' && console.info) {
            console.info('muxplex deck: focus_app is not yet implemented (see BACKLOG.md item 3)');
          }
          return;
        case 'none':
        case 'session':
        default:
          return; // explicit no-op
      }
    }

    function stepView(delta) {
      var idx = viewsList.indexOf(viewName);
      if (idx === -1) return;
      var next = clampPage(idx, delta, viewsList.length);
      if (next !== idx) selectView(viewsList[next]);
    }

    function setBrightness(value) {
      if (value > 100) value = 100;
      if (value < 10) value = 10;
      deckSettings.brightness = value;
      saveDeckSettings(storage, deckSettings);
      applyBrightness();
    }

    /**
     * A relative tick from a dial turn: apply it via the pure
     * `applyRelativeTicks`, then commit whichever piece of state it
     * touched back into the real (mutable) module state.
     * @param {number} dialIndex
     * @param {number} ticks
     */
    /**
     * Apply a signed relative tick count to whichever RELATIVE action a
     * control (dial turn OR strip zone drag) is bound to, and commit
     * whichever piece of state it touched back into real (mutable) module
     * state. Factored out of onDialTurn so the emulated touch strip's
     * tick-based drag interpretation reuses the exact same commit logic
     * rather than a second, easily-diverging copy.
     * @param {string} action
     * @param {number} ticks
     */
    function applyAndCommitRelative(action, ticks) {
      var slots = sessionSlotIndices(grid.rows, grid.cols, reserved, boundKeys);
      var viewIndex = viewsList.indexOf(viewName);
      var update = applyRelativeTicks(action, ticks, {
        page: page,
        pageCount: pageCount(sessions.length, slots.length),
        viewIndex: viewIndex === -1 ? 0 : viewIndex,
        viewCount: viewsList.length,
        brightness: deckSettings.brightness,
      });
      if ('page' in update) {
        page = update.page;
        render();
      }
      if ('viewIndex' in update && viewsList[update.viewIndex] != null) {
        selectView(viewsList[update.viewIndex]);
      }
      if ('brightness' in update) {
        setBrightness(update.brightness);
      }
    }

    /**
     * CONTINUOUS-action twin of applyAndCommitRelative -- currently only
     * ever `brightness_set` (see applyContinuousValue), kept as its own
     * named commit step so the parallel with applyAndCommitRelative
     * documents the extension point.
     * @param {string} action
     * @param {number} fraction - 0..1
     */
    function applyAndCommitContinuous(action, fraction) {
      var update = applyContinuousValue(action, fraction);
      if ('brightness' in update) {
        setBrightness(update.brightness);
      }
    }

    function onDialTurn(dialIndex, ticks) {
      var binding = dialBindings[dialIndex];
      if (!binding) return;
      applyAndCommitRelative(binding.turn, ticks);
    }

    function onDialPush(dialIndex) {
      var binding = dialBindings[dialIndex];
      if (!binding) return;
      dispatchAction(binding.push);
    }

    // ── Emulated dial strip (BACKLOG.md item 2 -- "emulated touch screen
    // bars and dials like the Stream Deck+") ──
    //
    // A dial occupies a fixed-height strip below the key grid (reserved by
    // contentBoxForDials before the grid is computed, so the two surfaces
    // never overlap). Turn is a vertical drag reduced to signed ticks by
    // dialDragTicks (see that function's doc comment for why a scrub
    // gesture rather than a rotary-angle one); a short, small-displacement
    // release is instead treated as a push (isDialTap). One control, both
    // halves of the dial.N.turn / dial.N.push address pair.

    function buildDialElement(index) {
      var dial = document.createElement('div');
      dial.className = 'deck-dial';
      dial.dataset.index = String(index);

      var face = document.createElement('div');
      face.className = 'deck-dial-face';
      dial.appendChild(face);

      var turnLabel = document.createElement('div');
      turnLabel.className = 'deck-dial-turn-label';
      dial.appendChild(turnLabel);

      var pushLabel = document.createElement('div');
      pushLabel.className = 'deck-dial-push-label';
      dial.appendChild(pushLabel);

      var dragging = false;
      var startY = 0;
      var lastTickY = 0;
      var startTime = 0;

      dial.addEventListener('pointerdown', function (ev) {
        dragging = true;
        startY = ev.clientY;
        lastTickY = ev.clientY;
        startTime = Date.now();
        dial.classList.add('is-active');
        if (dial.setPointerCapture) {
          try {
            dial.setPointerCapture(ev.pointerId);
          } catch (e) {
            /* ignore -- not all browsers require/support capture here */
          }
        }
      });
      dial.addEventListener('pointermove', function (ev) {
        if (!dragging) return;
        var deltaFromLastTick = ev.clientY - lastTickY;
        var ticks = dialDragTicks(deltaFromLastTick);
        if (ticks !== 0) {
          lastTickY += -ticks * DIAL_PX_PER_TICK;
          onDialTurn(index, ticks);
        }
      });
      var endDrag = function (ev) {
        if (!dragging) return;
        dragging = false;
        dial.classList.remove('is-active');
        var totalDelta = ev.clientY - startY;
        var elapsed = Date.now() - startTime;
        if (isDialTap(totalDelta, elapsed)) {
          onDialPush(index);
        }
      };
      dial.addEventListener('pointerup', endDrag);
      dial.addEventListener('pointercancel', endDrag);

      return dial;
    }

    function rebuildDialStripIfNeeded() {
      if (!dialStripEl) return;
      var count = deckSettings.dialCount;
      dialStripEl.classList.toggle('hidden', count <= 0);
      if (dialEls.length === count) return;
      dialStripEl.innerHTML = '';
      dialEls = [];
      for (var i = 0; i < count; i++) {
        var el = buildDialElement(i);
        dialStripEl.appendChild(el);
        dialEls.push(el);
      }
      renderDialLabels();
    }

    function renderDialLabels() {
      for (var i = 0; i < dialEls.length; i++) {
        var binding = dialBindings[i] || { turn: 'none', push: 'none' };
        var turnSpec = ACTION_CATALOG[binding.turn];
        var pushSpec = ACTION_CATALOG[binding.push];
        var turnEl = dialEls[i].querySelector('.deck-dial-turn-label');
        var pushEl = dialEls[i].querySelector('.deck-dial-push-label');
        if (turnEl) turnEl.textContent = 'TURN: ' + (turnSpec ? turnSpec.label.replace('\n', ' ') : 'NONE');
        if (pushEl) pushEl.textContent = 'PUSH: ' + (pushSpec ? pushSpec.label.replace('\n', ' ') : 'NONE');
      }
    }

    // -- Emulated touch strip (BACKLOG.md item 2 -- "emulated touch screen
    // bars and dials like the Stream Deck+") --
    //
    // Verified before building: every action in ACTION_CATALOG is momentary
    // or a signed-tick RELATIVE action -- true, but that only makes an
    // *absolute-position* strip decorative. A real Stream Deck+ touch strip
    // has a richer gesture vocabulary, and three of its gestures map onto
    // the existing catalog with zero new actions:
    //   - swipe left/right (whole strip)  -> any MOMENTARY action, exactly
    //     like a key press (strip.swipe.left / strip.swipe.right).
    //   - drag within a zone              -> the exact same signed ticks
    //     dialDragTicks emits, generalized to a horizontal axis
    //     (stripDragTicks), consumed by the same *_cycle RELATIVE actions
    //     dial.N.turn already binds (strip.N.drag).
    //   - tap at a position               -> a positional MOMENTARY
    //     binding, one per zone -- structurally a mini key row
    //     (strip.N.tap).
    // So the strip IS functional on the existing 19-action catalog; only
    // one thing is genuinely missing -- an ABSOLUTE-position consumer,
    // which is the touch strip's own canonical hardware use case (a
    // volume-style slider). That's the ONE new action this adds:
    // `brightness_set` (STRIP_ACTION_CATALOG, kind CONTINUOUS) -- and only
    // that one; see the catalog's own comment for why it deliberately does
    // NOT enter the cross-repo-mirrored ACTION_CATALOG.
    //
    // Gesture disambiguation, one pointer stream per whole strip container
    // (not per zone -- swipe is a whole-strip gesture, so the container is
    // the natural listener target; the pressed zone is resolved from the
    // event target):
    //   - TAP: checked first, unconditionally (isDialTap's exact existing
    //     8px/300ms discipline, reused verbatim per the task's own
    //     guidance -- "reuse the discipline rather than inventing new
    //     numbers"). Fires the pressed zone's `tap` binding.
    //   - DRAG (ticks or absolute): fires PROGRESSIVELY during
    //     pointermove, exactly like the dial does today, whenever the
    //     pressed zone's `drag` binding is not 'none'. Once a zone has a
    //     real drag binding, that zone's gesture vocabulary is drag --
    //     swipe is never checked for it (see below), so a fast drag can
    //     never double-fire both a tick update AND a swipe action.
    //   - SWIPE: only reachable when the pressed zone's `drag` binding IS
    //     'none' (checked via catalogSpecFor's kind, not a string compare,
    //     so it also covers a corrupted/unknown action name the same way
    //     'none' does) -- i.e. swipe is the fallback interpretation of a
    //     fast, large horizontal motion on a zone that has delegated its
    //     continuous gesture vocabulary to the whole-strip swipe binding.
    //     Classified at release via isStripSwipe (large AND fast -- the
    //     inverse shape of isDialTap's small AND fast).

    /**
     * Resolve which zone index a pointer event landed on, or null if it
     * didn't land inside any `.deck-strip-zone` (e.g. a gap/edge pixel).
     * @param {Event} ev
     * @returns {number|null}
     */
    function stripZoneIndexFromEvent(ev) {
      var target = ev.target && ev.target.closest ? ev.target.closest('.deck-strip-zone') : null;
      if (!target) return null;
      var idx = parseInt(target.dataset.index, 10);
      return Number.isInteger(idx) ? idx : null;
    }

    function buildStripZoneElement(index) {
      var zone = document.createElement('div');
      zone.className = 'deck-strip-zone';
      zone.dataset.index = String(index);

      var tapLabel = document.createElement('div');
      tapLabel.className = 'deck-strip-tap-label';
      zone.appendChild(tapLabel);

      var dragLabel = document.createElement('div');
      dragLabel.className = 'deck-strip-drag-label';
      zone.appendChild(dragLabel);

      return zone;
    }

    function rebuildTouchStripIfNeeded() {
      if (!stripStripEl) return;
      var count = deckSettings.stripCount;
      stripStripEl.classList.toggle('hidden', count <= 0);
      if (stripZoneEls.length === count) return;
      // Rebuild only the zone elements -- the container's own pointer
      // listeners (wired once, below) persist across this rebuild.
      while (stripStripEl.firstChild && stripStripEl.firstChild !== stripSwipeLeftLabelEl) {
        stripStripEl.removeChild(stripStripEl.firstChild);
      }
      stripStripEl.innerHTML = '';
      stripZoneEls = [];
      if (stripSwipeLeftLabelEl) stripStripEl.appendChild(stripSwipeLeftLabelEl);
      for (var i = 0; i < count; i++) {
        var el = buildStripZoneElement(i);
        stripStripEl.appendChild(el);
        stripZoneEls.push(el);
      }
      if (stripSwipeRightLabelEl) stripStripEl.appendChild(stripSwipeRightLabelEl);
      renderStripLabels();
    }

    function swipeLabelText(action) {
      var spec = catalogSpecFor(action);
      return spec ? spec.label.replace('\n', ' ') : 'NONE';
    }

    function renderStripLabels() {
      for (var i = 0; i < stripZoneEls.length; i++) {
        var binding = stripZoneBindings[i] || { tap: 'none', drag: 'none' };
        var tapSpec = catalogSpecFor(binding.tap);
        var dragSpec = catalogSpecFor(binding.drag);
        var tapEl = stripZoneEls[i].querySelector('.deck-strip-tap-label');
        var dragEl = stripZoneEls[i].querySelector('.deck-strip-drag-label');
        if (tapEl) tapEl.textContent = 'TAP: ' + (tapSpec ? tapSpec.label.replace('\n', ' ') : 'NONE');
        if (dragEl) dragEl.textContent = 'DRAG: ' + (dragSpec ? dragSpec.label.replace('\n', ' ') : 'NONE');
      }
      if (stripSwipeLeftLabelEl) stripSwipeLeftLabelEl.textContent = '\u2039 ' + swipeLabelText(stripSwipeBindings.left);
      if (stripSwipeRightLabelEl) stripSwipeRightLabelEl.textContent = swipeLabelText(stripSwipeBindings.right) + ' \u203a';
    }

    function onStripTap(zoneIndex) {
      var binding = stripZoneBindings[zoneIndex];
      if (!binding) return;
      dispatchAction(binding.tap);
    }

    function onStripSwipe(direction) {
      var action = direction === 'left' ? stripSwipeBindings.left : stripSwipeBindings.right;
      dispatchAction(action);
    }

    var stripPointerId = null;
    var stripActiveZone = null; // index or null
    var stripStartX = 0;
    var stripStartY = 0;
    var stripLastTickX = 0;
    var stripStartTime = 0;
    var stripActiveZoneRect = null; // measured at pointerdown, for absolute-fraction drags

    function stripDragKind(zoneIndex) {
      var binding = stripZoneBindings[zoneIndex];
      if (!binding) return null;
      var spec = catalogSpecFor(binding.drag);
      return spec ? spec.kind : null;
    }

    function wireTouchStrip() {
      if (!stripStripEl) return;
      stripStripEl.addEventListener('pointerdown', function (ev) {
        stripPointerId = ev.pointerId;
        stripActiveZone = stripZoneIndexFromEvent(ev);
        stripStartX = ev.clientX;
        stripStartY = ev.clientY;
        stripLastTickX = ev.clientX;
        stripStartTime = Date.now();
        stripActiveZoneRect = null;
        if (stripActiveZone != null && stripZoneEls[stripActiveZone]) {
          stripActiveZoneRect = stripZoneEls[stripActiveZone].getBoundingClientRect();
        }
        if (stripStripEl.setPointerCapture) {
          try {
            stripStripEl.setPointerCapture(ev.pointerId);
          } catch (e) {
            /* ignore -- not all browsers require/support capture here */
          }
        }
      });

      stripStripEl.addEventListener('pointermove', function (ev) {
        if (stripActiveZone == null || ev.pointerId !== stripPointerId) return;
        var kind = stripDragKind(stripActiveZone);
        if (kind === ACTION_RELATIVE) {
          var deltaFromLastTick = ev.clientX - stripLastTickX;
          var ticks = stripDragTicks(deltaFromLastTick);
          if (ticks !== 0) {
            stripLastTickX += ticks * DIAL_PX_PER_TICK;
            applyAndCommitRelative(stripZoneBindings[stripActiveZone].drag, ticks);
          }
        } else if (kind === ACTION_CONTINUOUS && stripActiveZoneRect) {
          var fraction = stripAbsoluteFraction(ev.clientX, stripActiveZoneRect.left, stripActiveZoneRect.width);
          applyAndCommitContinuous(stripZoneBindings[stripActiveZone].drag, fraction);
        }
        // kind === null/MOMENTARY ('none'): no progressive emission --
        // deferred to release-time tap/swipe classification below.
      });

      var endStripGesture = function (ev) {
        if (stripActiveZone == null && stripActiveZone !== 0) return;
        if (ev.pointerId !== stripPointerId) return;
        var totalDeltaX = ev.clientX - stripStartX;
        var elapsed = Date.now() - stripStartTime;
        var kind = stripDragKind(stripActiveZone);
        if (isDialTap(totalDeltaX, elapsed)) {
          onStripTap(stripActiveZone);
        } else if (kind !== ACTION_RELATIVE && kind !== ACTION_CONTINUOUS) {
          if (isStripSwipe(totalDeltaX, elapsed)) {
            onStripSwipe(totalDeltaX < 0 ? 'left' : 'right');
          }
        }
        stripActiveZone = null;
        stripPointerId = null;
        stripActiveZoneRect = null;
      };
      stripStripEl.addEventListener('pointerup', endStripGesture);
      stripStripEl.addEventListener('pointercancel', endStripGesture);
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

    // ── Settings panel (BACKLOG.md item 2) ──
    //
    // A full-surface overlay, not a KeyPlan/painter surface -- settings is
    // form data entry (address/action bindings, numeric fields, JSON
    // export/import), which is a legitimate, deliberate exception to "no
    // scrolling anywhere on this surface" (deck.css's own header comment):
    // #deck-settings is the ONE element allowed overflow-y:auto, because a
    // form that doesn't fit a landscape phone screen needs to scroll rather
    // than truncate silently.

    var settingsWired = false;

    function openSettings() {
      mode = 'settings';
      if (!settingsWired) {
        wireSettingsPanel();
        settingsWired = true;
      }
      populateSettingsForm();
      if (settingsEl) settingsEl.classList.remove('hidden');
      if (surface) surface.classList.add('hidden');
      if (dialStripEl) dialStripEl.classList.add('hidden');
      if (stripStripEl) stripStripEl.classList.add('hidden');
    }

    function closeSettings() {
      mode = 'grid';
      if (settingsEl) settingsEl.classList.add('hidden');
      if (surface) surface.classList.remove('hidden');
      recomputeGrid(); // pick up any grid-override/dial-count/strip-count change made in settings
      render();
    }

    function populateSettingsForm() {
      if (!settingsEl) return;
      var sortSel = settingsEl.querySelector('#settings-sort');
      var pollInput = settingsEl.querySelector('#settings-poll');
      var rowsInput = settingsEl.querySelector('#settings-rows');
      var colsInput = settingsEl.querySelector('#settings-cols');
      var autoBtn = settingsEl.querySelector('#settings-grid-auto');
      var dialInput = settingsEl.querySelector('#settings-dial-count');
      var stripInput = settingsEl.querySelector('#settings-strip-count');
      var brightInput = settingsEl.querySelector('#settings-brightness');
      var exportArea = settingsEl.querySelector('#settings-export');

      if (sortSel) sortSel.value = deckSettings.sort;
      if (pollInput) pollInput.value = String(deckSettings.pollIntervalMs);
      if (rowsInput) rowsInput.value = deckSettings.gridOverride ? String(deckSettings.gridOverride.rows) : '';
      if (colsInput) colsInput.value = deckSettings.gridOverride ? String(deckSettings.gridOverride.cols) : '';
      if (autoBtn) autoBtn.textContent = deckSettings.gridOverride ? 'Use Auto Grid' : 'Auto (current)';
      if (dialInput) dialInput.value = String(deckSettings.dialCount);
      if (stripInput) stripInput.value = String(deckSettings.stripCount);
      if (brightInput) brightInput.value = String(deckSettings.brightness);
      if (exportArea) exportArea.value = exportSettingsJSON(deckSettings);

      renderBindingsList();
    }

    function renderBindingsList() {
      var list = settingsEl.querySelector('#settings-bindings-list');
      if (!list) return;
      list.innerHTML = '';
      var addrs = Object.keys(deckSettings.bindings).sort();
      for (var i = 0; i < addrs.length; i++) {
        (function (addr) {
          var row = document.createElement('div');
          row.className = 'settings-binding-row';
          var label = document.createElement('span');
          label.textContent = addr + ' \u2192 ' + deckSettings.bindings[addr];
          row.appendChild(label);
          var removeBtn = document.createElement('button');
          removeBtn.type = 'button';
          removeBtn.textContent = 'Remove';
          removeBtn.addEventListener('click', function () {
            delete deckSettings.bindings[addr];
            saveDeckSettings(storage, deckSettings);
            renderBindingsList();
          });
          row.appendChild(removeBtn);
          list.appendChild(row);
        })(addrs[i]);
      }
    }

    function wireSettingsPanel() {
      if (!settingsEl) return;

      var closeBtn = settingsEl.querySelector('#settings-close');
      if (closeBtn) closeBtn.addEventListener('click', closeSettings);

      var resetBtn = settingsEl.querySelector('#settings-reset');
      if (resetBtn) {
        resetBtn.addEventListener('click', function () {
          deckSettings = defaultDeckSettings();
          saveDeckSettings(storage, deckSettings);
          applyBrightness();
          populateSettingsForm();
        });
      }

      var sortSel = settingsEl.querySelector('#settings-sort');
      if (sortSel) {
        sortSel.addEventListener('change', function () {
          deckSettings.sort = sortSel.value === 'server' ? 'server' : 'attention';
          saveDeckSettings(storage, deckSettings);
        });
      }

      var pollInput = settingsEl.querySelector('#settings-poll');
      if (pollInput) {
        pollInput.addEventListener('change', function () {
          var v = parseInt(pollInput.value, 10);
          if (Number.isFinite(v) && v >= 500 && v <= 60000) {
            deckSettings.pollIntervalMs = v;
            saveDeckSettings(storage, deckSettings);
          } else {
            pollInput.value = String(deckSettings.pollIntervalMs);
          }
        });
      }

      var rowsInput = settingsEl.querySelector('#settings-rows');
      var colsInput = settingsEl.querySelector('#settings-cols');
      var applyGridOverride = function () {
        var r = parseInt(rowsInput.value, 10);
        var c = parseInt(colsInput.value, 10);
        if (Number.isFinite(r) && Number.isFinite(c) && r >= 1 && c >= 1 && r <= 12 && c <= 12 && r * c <= N_MAX) {
          deckSettings.gridOverride = { rows: r, cols: c };
          saveDeckSettings(storage, deckSettings);
          populateSettingsForm();
        }
      };
      if (rowsInput) rowsInput.addEventListener('change', applyGridOverride);
      if (colsInput) colsInput.addEventListener('change', applyGridOverride);

      var autoBtn = settingsEl.querySelector('#settings-grid-auto');
      if (autoBtn) {
        autoBtn.addEventListener('click', function () {
          deckSettings.gridOverride = null;
          saveDeckSettings(storage, deckSettings);
          populateSettingsForm();
        });
      }

      var dialInput = settingsEl.querySelector('#settings-dial-count');
      if (dialInput) {
        dialInput.addEventListener('change', function () {
          var v = parseInt(dialInput.value, 10);
          if (Number.isFinite(v) && v >= 0 && v <= 4) {
            deckSettings.dialCount = v;
            saveDeckSettings(storage, deckSettings);
          } else {
            dialInput.value = String(deckSettings.dialCount);
          }
        });
      }

      var stripInput = settingsEl.querySelector('#settings-strip-count');
      if (stripInput) {
        stripInput.addEventListener('change', function () {
          var v = parseInt(stripInput.value, 10);
          if (Number.isFinite(v) && v >= 0 && v <= STRIP_MAX_ZONES) {
            deckSettings.stripCount = v;
            saveDeckSettings(storage, deckSettings);
          } else {
            stripInput.value = String(deckSettings.stripCount);
          }
        });
      }

      var brightInput = settingsEl.querySelector('#settings-brightness');
      if (brightInput) {
        brightInput.addEventListener('input', function () {
          var v = parseInt(brightInput.value, 10);
          if (Number.isFinite(v)) setBrightness(v);
        });
      }

      var addAddrInput = settingsEl.querySelector('#settings-add-address');
      var addActionSel = settingsEl.querySelector('#settings-add-action');
      var addBtn = settingsEl.querySelector('#settings-add-binding');
      var addError = settingsEl.querySelector('#settings-add-error');
      var refreshActionOptions = function () {
        if (!addAddrInput || !addActionSel) return;
        var address = parseControlAddress(addAddrInput.value.trim());
        var valid = validActionsForAddress(address);
        addActionSel.innerHTML = '';
        for (var i = 0; i < valid.length; i++) {
          var opt = document.createElement('option');
          opt.value = valid[i];
          opt.textContent = valid[i];
          addActionSel.appendChild(opt);
        }
        addActionSel.disabled = !address;
      };
      if (addAddrInput) addAddrInput.addEventListener('input', refreshActionOptions);
      if (addBtn) {
        addBtn.addEventListener('click', function () {
          var addrText = addAddrInput.value.trim();
          var action = addActionSel.value;
          if (!isValidBinding(addrText, action)) {
            if (addError) addError.textContent = 'Invalid address/action combination.';
            return;
          }
          if (addError) addError.textContent = '';
          var address = parseControlAddress(addrText);
          deckSettings.bindings[address.text] = action;
          deckSettings.bindings = sanitizeBindings(deckSettings.bindings);
          saveDeckSettings(storage, deckSettings);
          addAddrInput.value = '';
          renderBindingsList();
        });
      }

      var copyBtn = settingsEl.querySelector('#settings-export-copy');
      var exportArea = settingsEl.querySelector('#settings-export');
      if (copyBtn && exportArea) {
        copyBtn.addEventListener('click', function () {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(exportArea.value).catch(function () {
              /* best-effort -- the text is already selectable in the textarea */
            });
          }
          exportArea.select();
        });
      }

      var importArea = settingsEl.querySelector('#settings-import');
      var importBtn = settingsEl.querySelector('#settings-import-apply');
      var importError = settingsEl.querySelector('#settings-import-error');
      if (importBtn && importArea) {
        importBtn.addEventListener('click', function () {
          var result = importSettingsJSON(importArea.value, defaultDeckSettings());
          if (result.error) {
            if (importError) importError.textContent = result.error;
            return;
          }
          if (importError) importError.textContent = '';
          deckSettings = result.settings;
          saveDeckSettings(storage, deckSettings);
          applyBrightness();
          populateSettingsForm();
        });
      }
    }

    /**
     * The non-optional escape hatch (BACKLOG.md item 2): `?settings=1`
     * always opens Settings regardless of any binding/grid state; `?reset=1`
     * additionally wipes local settings back to defaults first. Checked
     * independently of every other affordance (long-press, bindings) so a
     * user who has configured themselves into a corner (every key bound
     * away, a degenerate 1xN override, dial-only grid) can always recover
     * by typing a URL -- the one thing that doesn't depend on the deck's
     * own UI being reachable.
     * @returns {boolean} whether Settings should be opened after boot
     */
    function checkURLEscapeHatch() {
      var params;
      try {
        params = new URLSearchParams(window.location.search);
      } catch (e) {
        return false;
      }
      var reset = params.has('reset');
      if (reset) {
        deckSettings = defaultDeckSettings();
        saveDeckSettings(storage, deckSettings);
      }
      return reset || params.has('settings');
    }

    // ── Boot ──

    function boot() {
      applyBrightness();
      wireTouchStrip();
      var wantsSettings = checkURLEscapeHatch();
      recomputeGrid();
      render();
      requestWakeLock();
      lockLandscapeOrientation();
      registerServiceWorker();
      poll().then(schedulePoll);
      if (wantsSettings) openSettings();
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
    // Settings menu (BACKLOG.md item 2)
    ACTION_CATALOG: ACTION_CATALOG,
    ACTION_MOMENTARY: ACTION_MOMENTARY,
    ACTION_RELATIVE: ACTION_RELATIVE,
    parseControlAddress: parseControlAddress,
    validActionsForAddress: validActionsForAddress,
    isValidBinding: isValidBinding,
    sanitizeBindings: sanitizeBindings,
    keyBindingsFromConfig: keyBindingsFromConfig,
    dialBindingsFromConfig: dialBindingsFromConfig,
    actionKeyContent: actionKeyContent,
    pageItemLabels: pageItemLabels,
    pageOptionContent: pageOptionContent,
    dialDragTicks: dialDragTicks,
    isDialTap: isDialTap,
    applyRelativeTicks: applyRelativeTicks,
    defaultDeckSettings: defaultDeckSettings,
    mergeDeckSettings: mergeDeckSettings,
    loadDeckSettings: loadDeckSettings,
    saveDeckSettings: saveDeckSettings,
    exportSettingsJSON: exportSettingsJSON,
    importSettingsJSON: importSettingsJSON,
    computeGridForShape: computeGridForShape,
    computeEffectiveGrid: computeEffectiveGrid,
    contentBoxForDials: contentBoxForDials,
    DECK_SETTINGS_KEY: DECK_SETTINGS_KEY,
    DIAL_STRIP_H: DIAL_STRIP_H,
    DIAL_PX_PER_TICK: DIAL_PX_PER_TICK,
    // Emulated touch strip (BACKLOG.md item 2)
    ACTION_CONTINUOUS: ACTION_CONTINUOUS,
    STRIP_ACTION_CATALOG: STRIP_ACTION_CATALOG,
    STRIP_MAX_ZONES: STRIP_MAX_ZONES,
    TOUCH_STRIP_H: TOUCH_STRIP_H,
    STRIP_SWIPE_PX_THRESHOLD: STRIP_SWIPE_PX_THRESHOLD,
    STRIP_SWIPE_MS_THRESHOLD: STRIP_SWIPE_MS_THRESHOLD,
    catalogSpecFor: catalogSpecFor,
    stripZoneBindingsFromConfig: stripZoneBindingsFromConfig,
    stripSwipeBindingsFromConfig: stripSwipeBindingsFromConfig,
    stripDragTicks: stripDragTicks,
    isStripSwipe: isStripSwipe,
    stripAbsoluteFraction: stripAbsoluteFraction,
    applyContinuousValue: applyContinuousValue,
    contentBoxForStrip: contentBoxForStrip,
  };
}

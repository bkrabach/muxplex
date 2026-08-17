// Phase 2b implementation — app.js

/**
 * Format a Unix timestamp (seconds) into a relative time string.
 * @param {number|null|undefined} ts - Unix timestamp in seconds
 * @returns {string}
 */
function formatTimestamp(ts) {
  if (ts == null) return '';
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

/**
 * Return the priority label for a session object.
 * @param {object} session
 * @returns {'bell'|'idle'}
 */
function sessionPriority(session) {
  const bell = session && session.bell;
  if (bell && bell.unseen_count > 0 && (bell.seen_at === null || bell.last_fired_at > bell.seen_at)) {
    return 'bell';
  }
  return 'idle';
}

/** Priority rank map used by sortByPriority. Lower rank = higher priority. */
const PRIORITY_RANK = { bell: 0, active: 1, idle: 2 };

/**
 * Sort an array of sessions by priority (ascending rank).
 * Returns a new array; does not mutate the original.
 * @param {object[]} sessions
 * @returns {object[]}
 */
function sortByPriority(sessions) {
  return sessions.slice().sort((a, b) => {
    const rankA = PRIORITY_RANK[sessionPriority(a)] ?? 2;
    const rankB = PRIORITY_RANK[sessionPriority(b)] ?? 2;
    return rankA - rankB;
  });
}

/**
 * Sort sessions attention-first: bell/needs-attention sessions first (newest
 * bell fire first), then everything else by bell.last_fired_at descending
 * (sessions that have never belled sort last). Mirrors the two-tier ordering
 * already implemented server-side (main.py's _attention_order(), used by
 * GET /api/view?sort=attention) and in muxplex-deck's attention.py
 * (apply_attention_sort()) -- all three must move together.
 *
 * Tier 2 deliberately keys off bell.last_fired_at, NOT last_activity_at:
 * that timestamp derives from tmux #{window_activity} and bumps on ANY pane
 * output (spinners, redraws, status-line clocks), so it reordered the grid
 * on every ~2s poll cycle even with nothing meaningful happening. A bell
 * only fires on the actual agent-turn-completion signal, so this ordering
 * is stable between bells.
 *
 * There is deliberately NO separate "currently-open session" tier. An
 * earlier revision added one to fix "the session I'm working in sinks to
 * the bottom" -- but that diagnosis was wrong. The real cause was the
 * server's bell hook curling the wrong scheme at a TLS port, so bells never
 * delivered for an attached session and its bell.last_fired_at froze; that
 * was fixed server-side in the same release. With bells actually
 * delivering, the actively-worked session rises on bell recency alone, and
 * a dedicated tier is not just redundant -- it is wrong: it bumps a session
 * because the user SELECTED it, when this sort's whole contract is to
 * track agent-turn-completion events, not user navigation. It also masks
 * bell-hook regressions: if the hook breaks again, an active-session tier
 * silently props the session up and hides the symptom that would otherwise
 * reveal it. See docs/API_SEMANTICS.md's "?sort=attention" entry and the
 * "selecting a session must not change its position" test below.
 *
 * Returns a new array; does not mutate the original. Array.prototype.sort
 * is stable, so ties (including "no bell, ever" for two sessions) preserve
 * incoming order.
 * @param {object[]} sessions
 * @returns {object[]}
 */
function sortByAttention(sessions) {
  const tier1 = sessions.filter((s) => sessionPriority(s) === 'bell');
  tier1.sort((a, b) => {
    const aFired = (a.bell && a.bell.last_fired_at) || 0;
    const bFired = (b.bell && b.bell.last_fired_at) || 0;
    return bFired - aFired;
  });
  const tier1Keys = new Set(tier1.map((s) => s.sessionKey || s.name));
  const remaining = sessions.filter((s) => !tier1Keys.has(s.sessionKey || s.name));

  const tier2 = remaining.slice();
  tier2.sort((a, b) => {
    const aTime = a.bell && a.bell.last_fired_at;
    const bTime = b.bell && b.bell.last_fired_at;
    if (aTime == null) return bTime == null ? 0 : 1;
    if (bTime == null) return -1;
    return bTime - aTime;
  });
  return tier1.concat(tier2);
}

/**
 * Apply the shared sort_order setting to a visible/filtered session array.
 * Single implementation used by BOTH renderGrid() and renderSidebar() so the
 * two surfaces can never drift into disagreeing about ordering -- previously
 * renderSidebar applied no sort_order logic at all and always showed
 * server-provided order regardless of the setting.
 * @param {object[]} visible - already view-filtered sessions
 * @param {string|undefined} sortOrder - _serverSettings.sort_order value
 * @param {boolean} mobile - isMobile() result for the current viewport
 * @returns {object[]}
 */
function applySortOrder(visible, sortOrder, mobile) {
  if (sortOrder === 'alphabetical') {
    return visible.slice().sort(function(a, b) { return (a.name || '').localeCompare(b.name || ''); });
  }
  if (sortOrder === 'attention') {
    // Unlike 'recent', attention already puts bell/urgent sessions first --
    // the same signal sortByPriority's mobile substitute exists to surface --
    // so it applies identically on mobile and desktop; no carve-out needed.
    return sortByAttention(visible);
  }
  if (sortOrder === 'recent' && !mobile) {
    // Sort by last_activity_at descending (most recently active first); sessions
    // with no known activity timestamp sort last. Array.prototype.sort is stable,
    // so ties (including sessions that are all null) preserve server-provided order.
    return visible.slice().sort(function(a, b) {
      var aTime = a.last_activity_at;
      var bTime = b.last_activity_at;
      if (aTime == null) return bTime == null ? 0 : 1;
      if (bTime == null) return -1;
      return bTime - aTime;
    });
  }
  // 'recent' (mobile), 'manual', and default use server-provided order; priority sort on mobile
  return mobile ? sortByPriority(visible) : visible;
}

/**
 * Filter an array of sessions by a search query string.
 * Matches against session.name (case-insensitive substring match).
 * Returns all sessions when query is empty or null.
 * @param {object[]} sessions
 * @param {string|null} query
 * @returns {object[]}
 */
function filterByQuery(sessions, query) {
  if (!query) return sessions;
  const q = query.toLowerCase();
  return sessions.filter((s) => (s.name || '').toLowerCase().includes(q));
}

/**
 * Detect which sessions have transitioned to a new or increased bell/alert state.
 * Builds a Map of previous session keys (sessionKey || name) to their unseen_count, then returns
 * the names of next sessions whose bell.unseen_count > 0 AND > the previous count.
 *
 * A session with NO entry in `prev` (first appearance -- e.g. one the user just
 * created, or a pre-existing bell surfacing on initial page load) is deliberately
 * excluded even if its unseen_count > 0: it is a NEW SESSION, not a NEW BELL, and
 * must not fire a notification the user didn't ask for. Only a key that was
 * already tracked in `prev` (so a real increase can be measured against it) can
 * produce a transition -- this is why the filter checks `prevMap.has(key)` before
 * comparing counts, rather than defaulting an absent key to 0.
 * @param {object[]} prev - previous sessions array
 * @param {object[]} next - updated sessions array
 * @returns {string[]} names of sessions that newly have or increased bell count
 */
function detectBellTransitions(prev, next) {
  const prevMap = new Map(
    (prev || []).map((s) => [s.sessionKey || s.name, (s.bell && s.bell.unseen_count) || 0]),
  );
  return (next || [])
    .filter((s) => {
      const unseen = s.bell && s.bell.unseen_count;
      if (!unseen || unseen <= 0) return false;
      const key = s.sessionKey || s.name;
      if (!prevMap.has(key)) return false; // first appearance: new session, not a new bell
      const prevCount = prevMap.get(key);
      return unseen > prevCount;
    })
    .map((s) => s.name);
}

/**
 * Generate a pseudo-random device ID string.
 * Format: 'd-' followed by 8 alphanumeric characters.
 * @returns {string}
 */
function generateDeviceId() {
  return 'd-' + Math.random().toString(36).padEnd(10, '0').slice(2, 10);
}

/**
 * Build a heartbeat payload object for the current device/view state.
 * @param {string} device_id - The generated device identifier
 * @param {string|null} viewing_session - The session currently being viewed, or null
 * @param {string} view_mode - Current view mode (e.g. 'split', 'full')
 * @param {number} last_interaction_at - Unix timestamp of last user interaction
 * @returns {object}
 */
function buildHeartbeatPayload(device_id, viewing_session, view_mode, last_interaction_at, sync_group) {
  const label =
    typeof navigator !== 'undefined' && navigator.userAgent
      ? navigator.userAgent.slice(0, 50)
      : 'unknown';
  return {
    device_id,
    label,
    viewing_session,
    view_mode,
    last_interaction_at,
    sync_group,
  };
}

// ─── Runtime constants ────────────────────────────────────────────────────────
const POLL_MS = 2000;
const STATE_POLL_MS = 1000;
const HEARTBEAT_MS = 5000;
const MOBILE_THRESHOLD = 600;
// Step 6 (design doc §6.2.7): a real network fan-out to every federation
// peer, not a same-process poll -- kept an order of magnitude slower than
// POLL_MS so this control never becomes the thing hammering a fleet of
// remotes every couple seconds. The server's own circuit breaker/cache
// (main.py's _federation_breaker/_federation_devices_cache) is the other
// half of keeping this cheap even at this cadence.
const FEDERATED_DEVICES_POLL_MS = 10000;

// ─── App state ────────────────────────────────────────────────────────────────
let _deviceId = '';
// Sync group MODE (not the resolved id): 'global' | 'device'. Storing the
// mode rather than the full group id means a regenerated _deviceId
// (localStorage wipe) re-derives 'device:<newId>' correctly instead of
// stranding a stale 'device:<oldId>' that would 400 on every heartbeat.
const SYNC_GROUP_STORAGE_KEY = 'muxplex-sync-group';
let _syncGroup = 'global';

// ── Follows: registered-device pairing (Step 4) ──────────────────────────
// New capability layered ON TOP OF the two escape hatches above: this
// browser can additionally FOLLOW A SPECIFIC OTHER REGISTERED DEVICE (a
// Stream Deck or Soft Deck), not just pick global vs. its own independent
// group. Ported from frontend/deck/deck.js's prototyped "Follows" model
// (design doc §9.3) -- same functions, same wording, same sticky-and-
// degraded policy (§7.2/§9.1), not reinvented here. See buildFollowsMenu(),
// computeFollowsDegraded(), etc. below.
//
// _syncGroup ('global'/'device') above still governs the two escape
// hatches with byte-identical wire behavior to pre-Step-4; _followTarget
// is null unless the user has picked a THIRD-SECTION (registered device)
// entry, in which case it takes priority for what the heartbeat sends
// (see resolveSyncGroupForWire()). currentFollows() is the one place the
// two state models meet.
const FOLLOW_TARGET_STORAGE_KEY = 'muxplex-follow-target';
let _followTarget = null;        // {targetId: string, targetLabel: ?string} | null
let _lastHeartbeatGoneId = null; // wire-level fallback marker, mirrors deck.js
let _devicesRegistry = {};       // last-known GET /api/state `devices` map
let _lastDevicesSnapshot = '';   // JSON snapshot of _devicesRegistry, for pollActiveState's change-detection guard (no re-render churn on an unchanged poll)
let _serverName = '';            // GET /api/instance-info's `name`
let _lastFollowsMenu = null;     // most recent buildFollowsMenu() result (for followsCandidateFromValue's raw-label recovery)

// ── Step 6: federated device discovery (§6.2.7-§6.2.10, §8.1 #11) ──────────
// Raw GET /api/federation/devices response, refreshed async and NEVER
// blocking the local Follows menu/Decks tab render (§6.2.7 -- "render
// local, fill federated async"). Starts empty so the very first render of
// this session shows local sections only, exactly like today, until the
// first fetch resolves.
let _federatedDevicesRaw = [];
let _federatedDevicesPollTimer;

let _currentSessions = [];
let _viewingSession = null;
let _viewingRemoteId = '';
let _viewMode = 'grid';
let _lastInteractionAt = Date.now() / 1000;
// Count of LOCAL session switches (sidebar/grid/sheet click, auto-open after
// create) whose server-side write hasn't been confirmed yet. openSession()
// sets _viewingSession synchronously, but the server's active_session doesn't
// catch up until the /connect POST resolves AND the follow-up PATCH
// /api/state settles -- a real window in which the dedicated ~1s state poll
// (STATE_POLL_MS) can read the OLD value and, without this guard, mistake it
// for a genuine remote switch and yank the user back to the session they just
// switched away from. Incremented when a local switch begins, decremented
// when its own write attempt settles (success or failure) -- driven by
// actual completion, not a wall-clock guess, so it can't outlive the switch
// it guards nor leak between unrelated switches.
let _pendingLocalSwitches = 0;
// Same guard, same reason, for active_view (the reported "flicker" bug):
// switchView() (and the two other call sites that set _activeView -- delete-
// active-view and rename-active-view) update _activeView synchronously, but
// the server's active_view doesn't catch up until PATCH /api/state settles.
// followRemoteActiveView() runs on every ~1s state-poll tick and compares
// the server's active_view against local _activeView by raw equality --
// unlike active_session (which is only ever adopted while a session is open,
// see _viewingSession's guard), there is no such secondary gate here, so a
// poll landing inside this window used to read the OLD value, conclude a
// remote device had switched views, and revert -- then the NEXT tick, once
// the write lands, flip forward again (new -> old -> new). Incremented when
// a local write begins, decremented once that write settles (success or
// failure) -- see persistActiveView().
let _pendingViewSwitches = 0;
let _pollingTimer;
let _statePollTimer;
// Set while the tab is hidden, by handleVisibilityChange() only. Both poll
// loops consult it in their tail before re-arming -- see the "Visibility
// handling" section's "the re-arm race" note for why clearing the pending
// timer is only half the job.
let _visibilityPaused = false;
let _heartbeatTimer;
let _notificationPermission = 'default';
let _pollFailCount = 0;
let _previewPopover = null;
let _previewTimer = null;

var _previewSessionName = null;  // track by NAME, not DOM element

// Flyout menu state
let _flyoutMenuEl = null;
let _flyoutSubmenuEl = null;
let _flyoutSessionKey = null;
let _flyoutSessionName = null;
let _flyoutRemoteId = null;

/**
 * Data map of menu item definitions keyed by view type.
 * Each entry is an array of item config objects with:
 *   { label, action, className?, separator? }
 * The 'user' view type uses a unified Views submenu (no separate Remove item).
 */
const FLYOUT_MENU_MAP = {
  'all': [
    { label: 'Add to View\u2026', action: 'add-to-view', className: 'flyout-menu__item--has-submenu' },
    { label: 'Hide', action: 'hide' },
    { separator: true },
    { label: 'Kill Session', action: 'kill', className: 'flyout-menu__item--danger' },
  ],
  'user': [
    { label: 'Add to View\u2026', action: 'add-to-view', className: 'flyout-menu__item--has-submenu' },
    { label: 'Hide', action: 'hide' },
    { separator: true },
    { label: 'Kill Session', action: 'kill', className: 'flyout-menu__item--danger' },
  ],
  'hidden': [
    { label: 'Unhide', action: 'unhide' },
    { label: 'Unhide & Add to View\u2026', action: 'unhide-add-to-view', className: 'flyout-menu__item--has-submenu' },
    { separator: true },
    { label: 'Kill Session', action: 'kill', className: 'flyout-menu__item--danger' },
  ],
};

/**
 * Build the flyout menu HTML string based on the active view type.
 * Uses FLYOUT_MENU_MAP to generate items — no if/else chains.
 * @returns {string} HTML for the menu items
 */
function _buildFlyoutMenuItems() {
  // Determine view type: 'all', 'hidden', or 'user'
  var viewType = _activeView;
  if (viewType !== 'all' && viewType !== 'hidden') {
    viewType = 'user';
  }

  var items = FLYOUT_MENU_MAP[viewType] || FLYOUT_MENU_MAP['all'];
  var html = '';

  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (item.separator) {
      html += '<div class="flyout-menu__separator" role="separator"></div>';
      continue;
    }

    var label = item.label;
    // Inject view name for "Remove from {viewName}"
    if (label.indexOf('{viewName}') !== -1) {
      var displayName = _activeView;
      if (displayName.length > 20) {
        displayName = displayName.substring(0, 20) + '\u2026';
      }
      label = label.replace('{viewName}', escapeHtml(displayName));
    }

    var cls = 'flyout-menu__item';
    if (item.className) cls += ' ' + item.className;

    var titleAttr = '';
    if (item.action === 'remove-from-view' && _activeView && _activeView.length > 20) {
      titleAttr = ' title="Remove from ' + escapeHtml(_activeView) + '"';
    }

    html += '<button class="' + cls + '" role="menuitem" data-action="' + item.action + '"' + titleAttr + '>';
    html += label;
    html += '</button>';
  }

  return html;
}

// ─── Settings state ───────────────────────────────────────────────────────────
let _settingsOpen = false;
let _serverSettings = null;
// Last response from GET/PATCH /api/tmux-config (Settings > Terminal tab).
// null until that endpoint has been fetched at least once.
let _tmuxConfig = null;
// Last-seen settings_updated_at (from /api/state's poll payload), used by
// followRemoteViewDefinitions() to detect a settings change (e.g. view
// membership edited on another device) without re-fetching /api/settings
// every tick. Seeded from the initial loadServerSettings() at page load so
// the very first poll doesn't trigger a redundant re-fetch. null means
// "not yet seeded" (only true before the DOMContentLoaded init runs).
let _lastSettingsUpdatedAt = null;
let _gridViewMode = 'flat';
let _activeFilterDevice = 'all';
let _activeView = 'all';
let _localDeviceId = null;
// This server's own reported version (from /api/instance-info), for the
// read-only "Version" field in Settings > Display. null until that fetch
// resolves; never falls back to a guessed value.
let _localVersion = null;
const DISPLAY_DEFAULTS = {
  fontSize: 14,
  hoverPreviewDelay: 1500,
  gridColumns: 'auto',
  bellSound: false,
  viewMode: 'auto',
  showDeviceBadges: true,        // DERIVED mirror of deviceLabelPlacement; not read by the renderer
  deviceLabelPlacement: 'titlebar', // 'titlebar' | 'corner' | 'off' -- authoritative
  activityIndicator: 'both',     // 'none' | 'glow' | 'dot' | 'both'
  gridViewMode: 'flat',          // 'flat' | 'grouped'
};

var VIEW_MODES = ['auto', 'fit'];
const NEW_SESSION_DEFAULT_TEMPLATE = 'tmux new-session -d -s {name}';
const DELETE_SESSION_DEFAULT_TEMPLATE = 'tmux kill-session -t {name}';
// Resolved session command pairs from GET /api/session-commands, or null
// before that fetch resolves (or on fetch failure -- see loadSessionCommands()).
// A null/short list degrades to _createCommandSelect()'s one-pair path,
// which is today's create-session UI unchanged.
let _sessionCommands = null;
// Human-readable strings from that response's `errors` (rejected
// session_commands entries) -- rendered read-only in Settings > Commands.
let _sessionCommandErrors = [];
// Human-readable strings from GET /api/views' `errors` (rejected
// match_names rules -- docs/plans/2026-08-04-auto-views-plan.md §9.2) -- rendered read-only in
// Settings > Views, mirroring _sessionCommandErrors exactly.
let _viewRuleErrors = [];

// --- Manage View rule editor state (docs/plans/2026-08-04-auto-views-plan.md §9.3) ---
// True once the user has typed in the rule textarea since it was last
// populated from _serverSettings -- guards against a background re-render
// (a poll pushing a remote settings change, e.g. followRemoteViewDefinitions)
// clobbering an in-progress, not-yet-saved edit.
let _manageViewRulesDirty = false;
// Debounce handle for the live-preview POST /api/views/preview call.
let _manageViewRulesPreviewTimer = null;
// Monotonic token: incremented on every new preview request so a slow,
// out-of-order response can never overwrite a newer one's render.
let _manageViewRulesPreviewToken = 0;

// Test-only: cancel a pending debounced preview timer without waiting the
// full 300ms. Prevents a test that only wants to exercise the "dirty" flag
// (via a direct oninput() call) from leaking a real setTimeout that later
// fires -- and calls the then-current global fetch -- during an unrelated,
// later test.
function _clearManageViewRulesPreviewTimer() {
  if (_manageViewRulesPreviewTimer) {
    clearTimeout(_manageViewRulesPreviewTimer);
    _manageViewRulesPreviewTimer = null;
  }
}

// ─── DOM helpers ──────────────────────────────────────────────────────────────
function $(id) {
  return document.getElementById(id);
}

function on(el, ev, fn) {
  if (el) el.addEventListener(ev, fn);
}

function isMobile() {
  return window.innerWidth < MOBILE_THRESHOLD;
}

// ─── Fetch wrapper ────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}: ${res.statusText}`);
    err.status = res.status;
    // Best-effort: attach the parsed error body so callers can distinguish
    // WHY a 409 happened (e.g. patchSettingsGuarded telling a stale-baseline
    // CAS conflict apart from a destructive-write backstop rejection --
    // they require different recovery behavior). A non-JSON or empty body
    // just leaves err.body undefined; callers already tolerate that.
    try {
      err.body = await res.json();
    } catch (parseErr) {
      // no-op: no usable JSON body on this error response
    }
    throw err;
  }
  return res;
}

// ─── Device ID ────────────────────────────────────────────────────────────────

function initDeviceId() {
  const STORAGE_KEY = 'tmux-web-device-id';
  try {
    let id = localStorage.getItem(STORAGE_KEY);
    if (!id) {
      id = generateDeviceId();
      try { localStorage.setItem(STORAGE_KEY, id); } catch (_) { /* blocked — ok */ }
    }
    _deviceId = id;
  } catch (_) {
    // localStorage blocked (Tracking Prevention, private browsing, etc.)
    // Fall back to a session-only device ID — not persisted but functional
    if (!_deviceId) _deviceId = generateDeviceId();
  }
}

// ─── Sync groups ────────────────────────────────────────────────────────────
// Opt-out of the server-global session/view selection. Default ('global')
// is byte-identical to today's behavior for every existing client — this
// only changes anything when a device explicitly flips to 'device' mode.

/**
 * Resolve the current sync-group MODE to the wire-format group id.
 * @returns {string} 'global' or 'device:<deviceId>'
 */
function syncGroupId() {
  return _syncGroup === 'device' ? 'device:' + _deviceId : 'global';
}

/**
 * Restore the sync-group mode from localStorage. Same try/catch shape as
 * initDeviceId(): localStorage may be blocked (Tracking Prevention, private
 * browsing) — in that case stay 'global' for the session, no persistence.
 */
function initSyncGroup() {
  try {
    const stored = localStorage.getItem(SYNC_GROUP_STORAGE_KEY);
    if (stored === 'device' || stored === 'global') {
      _syncGroup = stored;
    }
  } catch (_) {
    // localStorage blocked — stay 'global' for this session
  }
}

/**
 * Append device_id=<deviceId> to *path*, correctly handling whether *path*
 * already has a query string. Applied unconditionally — even in 'global'
 * mode — so there is exactly one code path: a 'global'-mode device still
 * resolves to the global group, so semantics are identical to a client that
 * sends no device_id at all.
 * @param {string} path
 * @returns {string}
 */
function withDevice(path) {
  return path + (path.indexOf('?') === -1 ? '?' : '&') + 'device_id=' + encodeURIComponent(_deviceId);
}

/**
 * Return this browser's own device_id. Exists so callers whose local scope
 * shadows the module-level `_deviceId` name (e.g. openSession()'s local
 * `_deviceId`, which actually holds the federation remoteId) can still reach
 * the real value — this function's body resolves `_deviceId` via its OWN
 * (module-level) lexical scope, unaffected by any caller's local shadowing.
 * @returns {string}
 */
function _ownDeviceId() {
  return _deviceId;
}

/**
 * Switch this device's sync group mode and persist the choice.
 *
 * Rejoining 'global' ADOPTS the shared selection, it does not push: no
 * PATCH happens here. After the heartbeat lands, the next
 * GET /api/state?device_id=... returns global's values, and the existing
 * followRemoteActiveView()/followRemoteActiveSession() apply them on the
 * very next pollActiveState() tick. An accidental PATCH here would push
 * this device's private selection onto everyone else -- precisely the bug
 * this feature exists to prevent.
 *
 * Leaving global SEEDS from global -- server-side, in ensure_group() -- so
 * going independent doesn't teleport the user to the "All" view.
 *
 * @param {'global'|'device'} mode
 */
async function setSyncGroup(mode) {
  _syncGroup = mode;
  try { localStorage.setItem(SYNC_GROUP_STORAGE_KEY, mode); } catch (_) { /* blocked — ok */ }
  renderSyncGroupControls();
  await sendHeartbeat();    // assert the new group immediately, don't wait 5s
  await pollActiveState();  // adopt the new group's selection on the next read
}

// ── Follows: ported deck.js "Follows" model (Step 4) ─────────────────────
// Every function in this section is a direct port of the equivalent one in
// frontend/deck/deck.js (design doc §9.3: "prototype the UX here first,
// port the proven model"). Logic and wording are unchanged; only the
// escape-hatch VALUE SPACE ('global'/'none') is shared with deck.js while
// this browser's own internal escape-hatch state stays _syncGroup
// ('global'/'device') for backward compatibility -- currentFollows() below
// is the one place the two meet.

/**
 * Human label for a device record -- display_name (human override) wins,
 * then label (client self-report), then the bare device_id.
 * Ported verbatim from frontend/deck/deck.js's deviceDisplayLabel.
 * @param {?object} deviceRecord
 * @param {?string} fallbackId
 * @returns {string}
 */
function deviceDisplayLabel(deviceRecord, fallbackId) {
  if (deviceRecord) {
    if (deviceRecord.display_name) return deviceRecord.display_name;
    if (deviceRecord.label) return deviceRecord.label;
  }
  return fallbackId || 'device';
}

/**
 * Append the closed-widget degraded suffix (§6.2.10/§9.1: "state that
 * requires a hover to discover is not state the user has" -- the suffix
 * must be part of the OPTION TEXT itself, since that's what a native
 * `<select>` shows while collapsed). Ported verbatim from deck.js.
 * @param {string} label
 * @returns {string}
 */
function degradedOptionLabel(label) {
  return label + ' \u2014 offline';
}

/**
 * Whether *targetId* (a registered-device follow pick) should render/behave
 * as degraded ("sticky and loud", §7.2/§9.1): no longer present in the live
 * registry (GC pruned it after its TTL), or the most recent heartbeat
 * attempt against it was rejected (409 target_gone / 400
 * target_not_self_owning). Adapted from deck.js's computeFollowsDegraded --
 * takes a bare targetId rather than a full follows object, since this
 * browser only ever calls it for an actual device pick (the two escape
 * hatches can never be degraded).
 * @param {?string} targetId
 * @param {object} devices - last-known GET /api/state `devices{}` map
 * @param {?string} lastHeartbeatGoneId - targetId of the most recent
 *   409/400-rejected heartbeat attempt, or null
 * @returns {boolean}
 */
function computeFollowsDegraded(targetId, devices, lastHeartbeatGoneId) {
  if (!targetId) return false;
  var stillLive = !!(devices && Object.prototype.hasOwnProperty.call(devices, targetId));
  var confirmedGone = lastHeartbeatGoneId === targetId;
  return confirmedGone || !stillLive;
}

/**
 * Build the data for the browser's "Follows" `<select>` -- two escape
 * hatches (always first, never alphabetized, §9.1) plus every OTHER device
 * registered with THIS server. No federated/"Elsewhere" section (§10 Step
 * 6 -- not built here at all, not even as a stub). Ported verbatim from
 * frontend/deck/deck.js's buildFollowsMenu.
 *
 * If the CURRENTLY-selected device target is degraded, its entry's label
 * gets the offline suffix (degradedOptionLabel). If that target has been
 * pruned from `devices` entirely (the common case), a synthetic placeholder
 * entry is appended so the `<select>`'s value still resolves to something
 * selected -- never silently reverting the visible pick to "<server>
 * (shared)" (§7.2's sticky-and-loud policy; the exact anti-pattern the
 * v0.48.3 icon bug already taught this codebase not to repeat).
 * @param {{devices:?object, ownDeviceId:string, serverName:?string,
 *          follows:{mode:string,targetId:?string,targetLabel:?string},
 *          degraded:boolean}} params
 * @returns {{escapeHatches:Array<{mode:string,value:string,label:string}>,
 *            registeredHeader:string,
 *            registered:Array<{mode:string,targetId:string,value:string,label:string,rawLabel:string,degraded:boolean}>,
 *            selectedValue:string}}
 */
function buildFollowsMenu(params) {
  var p = params || {};
  var devices = p.devices || {};
  var ownId = p.ownDeviceId;
  var serverName = p.serverName || 'this server';
  var follows = p.follows || { mode: 'global', targetId: null, targetLabel: null };
  var degraded = !!p.degraded;

  var escapeHatches = [
    { mode: 'global', targetId: null, value: 'global', label: serverName + ' (shared)' },
    { mode: 'none', targetId: null, value: 'none', label: 'Nothing \u2014 just me' },
  ];

  var registered = [];
  for (var id in devices) {
    if (!Object.prototype.hasOwnProperty.call(devices, id)) continue;
    if (id === ownId) continue; // never offer following yourself
    var label = deviceDisplayLabel(devices[id], id);
    registered.push({
      mode: 'device',
      targetId: id,
      value: 'device:' + id,
      label: label,
      rawLabel: label,
      degraded: false,
    });
  }

  var selectedValue = follows.mode === 'device' && follows.targetId ? 'device:' + follows.targetId : follows.mode;

  if (follows.mode === 'device' && follows.targetId && degraded) {
    var found = false;
    for (var i = 0; i < registered.length; i++) {
      if (registered[i].targetId === follows.targetId) {
        registered[i].label = degradedOptionLabel(registered[i].rawLabel);
        registered[i].degraded = true;
        found = true;
        break;
      }
    }
    if (!found) {
      // Gone from the live registry entirely (GC already pruned it) --
      // synthesize a placeholder from the cached targetLabel so the
      // dropdown still HAS an entry to point the sticky selection at.
      var fallbackLabel = follows.targetLabel || follows.targetId;
      registered.push({
        mode: 'device',
        targetId: follows.targetId,
        value: 'device:' + follows.targetId,
        label: degradedOptionLabel(fallbackLabel),
        rawLabel: fallbackLabel,
        degraded: true,
      });
    }
  }

  return {
    escapeHatches: escapeHatches,
    registeredHeader: 'Registered with ' + serverName,
    registered: registered,
    selectedValue: selectedValue,
  };
}

/**
 * Parse a `<select>`'s chosen `value` (as produced by buildFollowsMenu,
 * 'global' | 'none' | 'device:<id>') back into a follows candidate,
 * recovering the RAW (undecorated) label from the last-built menu's
 * `registered` list so a fresh pick never persists an "\u2014 offline"
 * suffix as if it were the device's real name. Ported verbatim from
 * deck.js's followsCandidateFromValue.
 * @param {string} value
 * @param {Array<{targetId:string, rawLabel:string}>} registered - the
 *   `registered` array from the most recent buildFollowsMenu() call
 * @returns {{mode:string, targetId:?string, targetLabel:?string}}
 */
function followsCandidateFromValue(value, registered) {
  if (value === 'none') return { mode: 'none', targetId: null, targetLabel: null };
  if (typeof value === 'string' && value.indexOf('device:') === 0) {
    var id = value.slice('device:'.length);
    var rawLabel = null;
    var list = registered || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].targetId === id) {
        rawLabel = list[i].rawLabel;
        break;
      }
    }
    return { mode: 'device', targetId: id, targetLabel: rawLabel };
  }
  return { mode: 'global', targetId: null, targetLabel: null };
}

/**
 * Render the explanation for a 400 target_not_self_owning rejection
 * (§6.2.5/§7.0(b)/§8.1 #8 -- the cycle guard): naming WHO is already
 * following this browser, from the error body's `controlled_by` device_id,
 * resolved to a human label via the live registry when possible. Ported
 * verbatim from deck.js's targetNotSelfOwningMessage.
 * @param {{controlled_by:?string}|null|undefined} detail - the INNER
 *   `detail` object from the 400 response body (err.body.detail)
 * @param {object} devices - last-known GET /api/state `devices{}` map
 * @returns {string}
 */
function targetNotSelfOwningMessage(detail, devices) {
  var followerId = detail && detail.controlled_by;
  if (!followerId) return "Can't follow another device while something is following you.";
  var label = deviceDisplayLabel(devices && devices[followerId], followerId);
  return "Can't follow \u2014 " + label + ' is already following you';
}

/**
 * Compose this browser's current "follows" preference as the same
 * {mode, targetId, targetLabel} shape frontend/deck/deck.js uses
 * internally (§9.3), from the pre-existing _syncGroup toggle (escape
 * hatches -- byte-identical wire behavior, unchanged) plus the new
 * _followTarget layer (registered-device pairing, §9.1's third section).
 * This is the ONLY place the two state models meet -- buildFollowsMenu()
 * and everything else above operates purely on this shape.
 * @returns {{mode:string, targetId:?string, targetLabel:?string}}
 */
function currentFollows() {
  if (_followTarget && _followTarget.targetId) {
    return { mode: 'device', targetId: _followTarget.targetId, targetLabel: _followTarget.targetLabel };
  }
  return { mode: _syncGroup === 'device' ? 'none' : 'global', targetId: null, targetLabel: null };
}

/**
 * Validate/sanitize a possibly-partial, possibly-hostile persisted follow
 * target -- same recovery posture as the rest of this file's settings
 * loaders (drop an invalid value in favor of null rather than throwing).
 * @param {*} raw
 * @returns {?{targetId: string, targetLabel: ?string}}
 */
function sanitizeFollowTarget(raw) {
  if (!raw || typeof raw !== 'object') return null;
  if (typeof raw.targetId !== 'string' || raw.targetId.length === 0) return null;
  return { targetId: raw.targetId, targetLabel: typeof raw.targetLabel === 'string' ? raw.targetLabel : null };
}

/**
 * Persist (or clear) the current follow-target pick to localStorage.
 * Best-effort -- localStorage may be blocked (Tracking Prevention, private
 * browsing); an unpersisted pick still works for this session.
 * @param {?{targetId: string, targetLabel: ?string}} candidate
 */
function persistFollowTarget(candidate) {
  try {
    if (candidate) localStorage.setItem(FOLLOW_TARGET_STORAGE_KEY, JSON.stringify(candidate));
    else localStorage.removeItem(FOLLOW_TARGET_STORAGE_KEY);
  } catch (_) { /* blocked — ok */ }
}

/**
 * Restore a persisted follow-target pick from localStorage. Same try/catch
 * shape as initSyncGroup(): localStorage may be blocked, in which case this
 * returns null (no follow target for this session, escape hatches only).
 * @returns {?{targetId: string, targetLabel: ?string}}
 */
function loadFollowTarget() {
  try {
    var raw = localStorage.getItem(FOLLOW_TARGET_STORAGE_KEY);
    if (!raw) return null;
    return sanitizeFollowTarget(JSON.parse(raw));
  } catch (_) {
    return null;
  }
}

/**
 * Resolve the actual `sync_group` value to send on THIS heartbeat request.
 * The registered-device follow (_followTarget) takes priority when present
 * and not degraded; a degraded follow target falls back to 'global' at the
 * wire level while the UI stays sticky (§6.2.4/§7.2 -- the same policy
 * deck.js's resolveHeartbeatSyncGroup already proved; recovery is
 * user-initiated via a fresh pick, not automatic -- see
 * attemptFollowTarget()). When no follow target is set at all, this
 * defers entirely to the pre-existing syncGroupId() -- byte-identical to
 * pre-Step-4 behavior for every existing client.
 * @returns {string}
 */
function resolveSyncGroupForWire() {
  if (_followTarget && _followTarget.targetId) {
    var degraded = computeFollowsDegraded(_followTarget.targetId, _devicesRegistry, _lastHeartbeatGoneId);
    if (!degraded) return 'device:' + _followTarget.targetId;
    return 'global';
  }
  return syncGroupId();
}

/**
 * Attempt to switch this browser's follow preference to *candidate* (a
 * registered device pick from the dropdown's third section), asserting it
 * against the server immediately -- ported 3-outcome policy from deck.js's
 * attemptFollowsChange (§9.3):
 *   - success: candidate becomes the persisted _followTarget.
 *   - 409 target_gone: candidate is adopted anyway (sticky), marked
 *     degraded -- §7.2/§9.1's policy applies even to a selection made the
 *     instant the target vanished (a render race), not only to a target
 *     that goes stale later.
 *   - 400 target_not_self_owning: candidate is REJECTED outright -- the
 *     persisted follow target is left unchanged, the `<select>` reverts to
 *     it on the next renderSyncGroupControls(), and the naming message is
 *     shown via toast (§6.2.5's cycle guard).
 * @param {{targetId:string, targetLabel:?string}} candidate
 * @returns {Promise<void>}
 */
async function attemptFollowTarget(candidate) {
  var payload = buildHeartbeatPayload(_deviceId, _viewingSession, _viewMode, _lastInteractionAt, 'device:' + candidate.targetId);
  try {
    await api('POST', '/api/heartbeat', payload);
    _followTarget = candidate;
    _lastHeartbeatGoneId = null;
    persistFollowTarget(candidate);
    renderSyncGroupControls();
    await pollActiveState(); // adopt the new group's selection on the next read
  } catch (err) {
    if (err && err.status === 409) {
      _followTarget = candidate;
      _lastHeartbeatGoneId = candidate.targetId;
      persistFollowTarget(candidate);
      renderSyncGroupControls();
      return;
    }
    if (err && err.status === 400 && err.body && err.body.detail && err.body.detail.target_not_self_owning) {
      renderSyncGroupControls(); // reverts the <select> to the still-current pick
      showToast(targetNotSelfOwningMessage(err.body.detail, _devicesRegistry));
      return;
    }
    renderSyncGroupControls();
    showToast('Couldn\'t reach the server \u2014 try again.');
  }
}

/**
 * Handle a `change` event on either Follows `<select>` (overview + expanded
 * headers share one preference). The two escape hatches route through the
 * pre-existing setSyncGroup() (byte-identical wire behavior); a registered-
 * device pick routes through attemptFollowTarget(). Picking an escape hatch
 * while a foreign device is being followed clears that follow target.
 * @param {string} value - the selected `<option>`'s value
 */
function handleFollowsSelectChange(value) {
  // Step 6: the federated optgroup's rows are action encodings, never a
  // real Follows selection (§9.1 -- "not selectable"). Perform the action,
  // then revert the <select> to whatever is actually persisted right now.
  if (typeof value === 'string' && value.indexOf('federated-open:') === 0) {
    openFederatedPeer(value.slice('federated-open:'.length));
    renderSyncGroupControls();
    return;
  }
  if (typeof value === 'string' && value.indexOf('federated-retry:') === 0) {
    fetchFederatedDevices(); // re-fetch now rather than waiting for the next poll tick
    renderSyncGroupControls();
    return;
  }
  if (value === 'global' || value === 'none') {
    if (_followTarget) {
      _followTarget = null;
      _lastHeartbeatGoneId = null;
      persistFollowTarget(null);
    }
    setSyncGroup(value === 'none' ? 'device' : 'global');
    return;
  }
  var candidate = followsCandidateFromValue(value, (_lastFollowsMenu && _lastFollowsMenu.registered) || []);
  if (candidate.mode === 'device' && candidate.targetId) {
    attemptFollowTarget({ targetId: candidate.targetId, targetLabel: candidate.targetLabel });
  }
}

/**
 * Update the persistent, un-dismissable "Controlled by" chip (§7.3): shown
 * whenever this browser's OWN devices{} entry has a non-null controlled_by
 * (i.e. some other registered device is following this tab). Not a button;
 * no click action -- purely informational, the flip side of the sync-group
 * picker (without it, a user on the browser doesn't know a deck has
 * claimed their selection).
 */
function renderControlledByChip() {
  var ownRecord = _devicesRegistry && _devicesRegistry[_deviceId];
  var followerId = ownRecord ? ownRecord.controlled_by : null;
  ['controlled-by-chip', 'controlled-by-chip-expanded'].forEach(function(id) {
    var chip = $(id);
    if (!chip) return;
    if (followerId) {
      chip.textContent = 'Controlled by: ' + deviceDisplayLabel(_devicesRegistry[followerId], followerId);
      chip.classList.remove('hidden');
    } else {
      chip.textContent = '';
      chip.classList.add('hidden');
    }
  });
}

// ── Federated device discovery (Step 6: §6.2.7-§6.2.10, §8.1 #11/#12) ──────
// "Elsewhere in your federation" -- the third, INFORMATIONAL section (§9.1)
// below the two local sections rendered above. Sourced from
// GET /api/federation/devices, a server-side read-only fan-out that
// carries ONLY peer devices (this server's own devices{} registry already
// surfaces via _devicesRegistry/buildFollowsMenu's "registered" section).

/**
 * Resolve a federated device entry's home-server URL from this browser's
 * already-loaded `_serverSettings.remote_instances` -- the same
 * device_id-or-index convention the server itself uses
 * (main.py's `remote.get("device_id", str(i))`, also mirrored by
 * `_createDeviceSelect()` above) so a peer with no explicit `device_id`
 * configured still resolves via its list position.
 * @param {string} homeDeviceId
 * @returns {?string} the peer's configured URL, or null if unresolvable
 */
function resolveFederatedPeerUrl(homeDeviceId) {
  var remotes = (_serverSettings && _serverSettings.remote_instances) || [];
  for (var i = 0; i < remotes.length; i++) {
    var id = (remotes[i].device_id != null && remotes[i].device_id !== '') ? remotes[i].device_id : String(i);
    if (id === homeDeviceId) return remotes[i].url || null;
  }
  return null;
}

/**
 * Turn a raw GET /api/federation/devices response into rendering-ready
 * rows. Pure data-shaping -- no DOM, no fetch -- so it is independently
 * testable (tests/test_app.mjs).
 *
 * Device entries (carry `device_id`) are keyed
 * `<homeDeviceId>:<device_id>` (§6.2.8's composite key): this is what lets
 * the SAME logical device, registered on two different peers during a
 * ≤300s move-between-servers window, render as two clearly-labeled rows
 * ("via spark-1" / "via alienware") instead of colliding under one bare
 * device_id.
 *
 * Per-peer status entries (carry `status`, no `device_id`) become a single
 * un-clickable row per §6.2.10, UNLESS status is 'empty' -- a REACHABLE
 * peer with nothing registered is not a failure, so (unlike
 * 'unreachable'/'auth_failed') it is omitted rather than adding a
 * permanently-empty, non-actionable row for every quiet peer.
 *
 * @param {Array<object>} rawEntries - GET /api/federation/devices response
 * @returns {Array<{key:string, kind:('device'|'status'), label:string,
 *   deviceId:?string, homeDeviceId:string, homeDeviceName:string,
 *   reachable:boolean}>}
 */
function buildFederatedDevicesSection(rawEntries) {
  var rows = [];
  (rawEntries || []).forEach(function(entry) {
    if (!entry) return;
    if (entry.status) {
      if (entry.status === 'empty') return; // reachable, nothing to show -- not a failure
      rows.push({
        key: 'status:' + entry.homeDeviceId,
        kind: 'status',
        label: 'Couldn\'t reach ' + (entry.homeDeviceName || entry.homeDeviceId),
        deviceId: null,
        homeDeviceId: entry.homeDeviceId,
        homeDeviceName: entry.homeDeviceName,
        reachable: false,
      });
      return;
    }
    if (!entry.device_id) return; // malformed/unexpected shape -- ignore defensively
    var ownLabel = entry.display_name || entry.device_id;
    rows.push({
      key: entry.homeDeviceId + ':' + entry.device_id,
      kind: 'device',
      label: ownLabel + ' \u2014 via ' + (entry.homeDeviceName || entry.homeDeviceId),
      deviceId: entry.device_id,
      homeDeviceId: entry.homeDeviceId,
      homeDeviceName: entry.homeDeviceName,
      reachable: true,
    });
  });
  return rows;
}

/**
 * Navigate to a federated peer's own PWA (§6.2.9's "Open on X" link
 * action). Opens in a new tab (matches the existing external-link
 * convention in index.html's Decks tab -- "Open Soft Deck"/"Set up a
 * physical Stream Deck" both use target="_blank" rel="noopener"). A peer
 * whose URL can't be resolved (e.g. remote_instances hasn't loaded yet)
 * fails loud via toast rather than silently doing nothing.
 * @param {string} homeDeviceId
 */
function openFederatedPeer(homeDeviceId) {
  var url = resolveFederatedPeerUrl(homeDeviceId);
  if (!url) {
    showToast('Could not find that server\'s address \u2014 try again.');
    return;
  }
  window.open(url, '_blank', 'noopener');
}

/**
 * Fetch GET /api/federation/devices and re-render (§6.2.7: "render local,
 * fill federated async" -- never blocks or delays the local Follows
 * menu/Decks tab, which have already rendered by the time this resolves).
 * Best-effort: a failed fetch leaves the last-known `_federatedDevicesRaw`
 * in place rather than clearing it, so a transient blip doesn't flash the
 * whole "Elsewhere" section away (same posture as refreshDevicesRegistry's
 * deck.js counterpart).
 * @returns {Promise<void>}
 */
async function fetchFederatedDevices() {
  try {
    const res = await api('GET', '/api/federation/devices');
    const data = await res.json();
    _federatedDevicesRaw = Array.isArray(data) ? data : [];
    renderSyncGroupControls();
  } catch (err) {
    console.warn('[fetchFederatedDevices] could not refresh federated devices:', err);
  }
}

/**
 * Start the periodic federated-devices refresh (FEDERATED_DEVICES_POLL_MS).
 * Guards against double-start; self-scheduling setTimeout, same shape as
 * startPolling()/startStatePolling() so a slow fan-out never overlaps the
 * next tick.
 */
function startFederatedDevicesPolling() {
  if (_federatedDevicesPollTimer) return;
  _federatedDevicesPollTimer = true; // sentinel: prevents double-start before first setTimeout fires
  async function tick() {
    await fetchFederatedDevices();
    if (!_visibilityPaused) _federatedDevicesPollTimer = setTimeout(tick, FEDERATED_DEVICES_POLL_MS);
  }
  tick();
}

/**
 * Keep every sync-group UI widget (header `<select>` x2, controlled-by chip
 * x2, Settings checkbox, Decks tab list) in sync with the current follows
 * state — same pattern as syncSortOrderControls(). Missing elements (e.g.
 * Settings dialog not yet opened) are skipped silently.
 *
 * Step 4 (design doc §9.4) rewrite: the link/broken-link icon toggle button
 * fixed in v0.48.3 is replaced by a native `<select>` (buildFollowsMenu's
 * two escape hatches plus the registered-device section) — same function
 * name/call sites as before, new implementation. See
 * tests/test_app.mjs's "Follows select" coverage (successor to the retired
 * "sync-group toggle button" coverage this replaces) and
 * tests/test_css_class_definitions.mjs (cross-checks every class this
 * function applies against style.css).
 */
function renderSyncGroupControls() {
  var follows = currentFollows();
  var degraded = follows.mode === 'device'
    ? computeFollowsDegraded(follows.targetId, _devicesRegistry, _lastHeartbeatGoneId)
    : false;
  var menu = buildFollowsMenu({
    devices: _devicesRegistry,
    ownDeviceId: _deviceId,
    serverName: _serverName,
    follows: follows,
    degraded: degraded,
  });
  _lastFollowsMenu = menu;

  var title = follows.mode === 'global'
    ? 'Following this server\'s view'
    : follows.mode === 'none'
      ? 'Independent — not following this server\'s view'
      : (degraded
        ? 'Follow target is offline — fell back to global'
        : 'Following ' + (menu.registered.filter(function(r) { return r.targetId === follows.targetId; })[0] || {}).rawLabel);

  ['sync-group-select', 'sync-group-select-expanded'].forEach(function(id) {
    var sel = $(id);
    if (!sel) return;
    sel.innerHTML = '';
    menu.escapeHatches.forEach(function(opt) {
      var o = document.createElement('option');
      o.value = opt.value;
      o.textContent = opt.label;
      sel.appendChild(o);
    });
    if (menu.registered.length > 0) {
      var group = document.createElement('optgroup');
      group.label = menu.registeredHeader;
      menu.registered.forEach(function(opt) {
        var o = document.createElement('option');
        o.value = opt.value;
        o.textContent = opt.label;
        group.appendChild(o);
      });
      sel.appendChild(group);
    }
    // Step 6 (§9.1's third section): "Elsewhere in your federation" --
    // informational, never the actual Follows selection. A native
    // <select> has no way to attach an inline "[Open on X]"/"[Retry]"
    // button to one row, so each federated row's VALUE instead encodes the
    // action itself (see handleFollowsSelectChange) -- picking a device
    // row opens that peer's PWA, picking a status row retries the fetch,
    // and either way the <select> is immediately reverted to the real
    // current follows value right after (never persisted as a selection,
    // matching "not selectable").
    var federatedRows = buildFederatedDevicesSection(_federatedDevicesRaw);
    if (federatedRows.length > 0) {
      var federatedGroup = document.createElement('optgroup');
      federatedGroup.label = 'Elsewhere in your federation';
      federatedRows.forEach(function(row) {
        var o = document.createElement('option');
        if (row.kind === 'status') {
          o.value = 'federated-retry:' + row.homeDeviceId;
          o.textContent = '\u26a0 ' + row.label;
        } else {
          o.value = 'federated-open:' + row.homeDeviceId;
          o.textContent = row.label;
        }
        federatedGroup.appendChild(o);
      });
      sel.appendChild(federatedGroup);
    }
    sel.value = menu.selectedValue;
    sel.title = title;
  });

  // The pre-existing Multi-Device settings checkbox still reflects the
  // _syncGroup escape hatch only (unchanged pre-existing behavior) --
  // following a registered device is a distinct, additional state it was
  // never designed to represent.
  var checkbox = $('setting-independent-view');
  if (checkbox) checkbox.checked = (_syncGroup === 'device') && !_followTarget;

  renderControlledByChip();
  renderDecksSettingsTab();
}

/**
 * Describe a device's own `sync_group` in the same human terms the
 * dropdown uses ("<server> (shared)", "Nothing — just me", or a resolved
 * device label) — for the Decks tab's registered-device list (§9.5).
 * @param {object} device
 * @param {string} deviceId
 * @returns {string}
 */
function _describeDeviceFollows(device, deviceId) {
  var group = device && device.sync_group;
  if (!group || group === 'global') return (_serverName || 'this server') + ' (shared)';
  if (group.indexOf('device:') === 0) {
    var targetId = group.slice('device:'.length);
    if (targetId === deviceId) return 'Nothing \u2014 just me'; // self-claim
    return deviceDisplayLabel(_devicesRegistry[targetId], targetId);
  }
  return group;
}

/**
 * Build one row of the Decks tab's registered-device list (§9.5): an
 * inline-editable display_name (PATCH /api/devices/{id} on blur/Enter,
 * §8.1 #3/#7 -- ported handling, see _patchDeviceDisplayName), what it
 * Follows (rendered in the same human terms as the header dropdown), and
 * last-seen (relative, via the existing formatTimestamp() helper).
 * @param {string} deviceId
 * @param {object} device
 * @returns {Element}
 */
function _buildDecksDeviceRow(deviceId, device) {
  var row = document.createElement('div');
  row.className = 'decks-device-row';

  var nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'decks-device-name settings-input';
  nameInput.value = deviceDisplayLabel(device, deviceId);
  nameInput.setAttribute('aria-label', 'Device name');
  nameInput.setAttribute('data-device-id', deviceId);
  _suppressAutofill(nameInput);
  row.appendChild(nameInput);

  var followsSpan = document.createElement('span');
  followsSpan.className = 'decks-device-follows';
  followsSpan.textContent = _describeDeviceFollows(device, deviceId);
  row.appendChild(followsSpan);

  var lastSeenSpan = document.createElement('span');
  lastSeenSpan.className = 'decks-device-last-seen';
  lastSeenSpan.textContent = (device && device.last_heartbeat_at != null)
    ? formatTimestamp(device.last_heartbeat_at)
    : 'never';
  row.appendChild(lastSeenSpan);

  return row;
}

/**
 * Build one row of the Decks tab's federated ("Elsewhere in your
 * federation") list (§9.5, same rules as §9.1's picker section). Unlike
 * the header `<select>`'s action-encoded-as-a-value workaround, this is a
 * full custom-rendered panel, so each row gets a REAL clickable action:
 * a device row's "Open on X" is an `<a target="_blank">` (same convention
 * as the tab's existing "Open Soft Deck"/"Set up a physical Stream Deck"
 * links), a status row's "Retry" is a `<button>`. Never selectable --
 * neither carries a radio/checkbox, matching §9.1's "informational" rule.
 * @param {{key:string, kind:('device'|'status'), label:string,
 *   deviceId:?string, homeDeviceId:string, homeDeviceName:string,
 *   reachable:boolean}} row
 * @returns {Element}
 */
function _buildDecksFederatedRow(row) {
  var el = document.createElement('div');
  el.className = 'decks-federated-row' + (row.reachable ? '' : ' decks-federated-row--unreachable');

  var labelSpan = document.createElement('span');
  labelSpan.className = 'decks-federated-label';
  labelSpan.textContent = (row.reachable ? '' : '\u26a0 ') + row.label;
  el.appendChild(labelSpan);

  if (row.reachable) {
    var openLink = document.createElement('a');
    openLink.className = 'settings-action-btn settings-action-link decks-federated-action';
    openLink.textContent = 'Open on ' + (row.homeDeviceName || row.homeDeviceId);
    openLink.href = resolveFederatedPeerUrl(row.homeDeviceId) || '#';
    openLink.target = '_blank';
    openLink.rel = 'noopener';
    openLink.addEventListener('click', function(ev) {
      if (!resolveFederatedPeerUrl(row.homeDeviceId)) {
        ev.preventDefault();
        showToast('Could not find that server\'s address \u2014 try again.');
      }
    });
    el.appendChild(openLink);
  } else {
    var retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.className = 'settings-action-btn decks-federated-action';
    retryBtn.textContent = 'Retry';
    retryBtn.addEventListener('click', function() {
      fetchFederatedDevices();
    });
    el.appendChild(retryBtn);
  }

  return el;
}

/**
 * Populate (or repopulate) the Settings > Decks tab's registered-device
 * list (§9.5) from the current _devicesRegistry/_serverName, PLUS the
 * federated "Elsewhere in your federation" section (§9.5/§9.1, sourced
 * from _federatedDevicesRaw -- see fetchFederatedDevices). Renders every
 * OTHER device registered with this server (excluding this browser's own
 * entry, which manages its own name via Display > Device Name instead).
 * No-op if the tab's DOM isn't present. Skips the LOCAL list's rebuild
 * entirely while a rename is in progress (focus is inside a
 * .decks-device-name input) so a ~1s poll tick can never clobber an
 * in-flight edit -- the next tick after the input blurs picks up any
 * registry changes. The federated section has no editable inputs, so it
 * is never subject to that guard.
 */
function renderDecksSettingsTab() {
  var listEl = $('decks-registered-list');
  var emptyEl = $('decks-registered-empty');
  var headingEl = $('decks-registered-heading');
  if (listEl) {
    var active = typeof document !== 'undefined' ? document.activeElement : null;
    var renamingInProgress = active && active.classList && active.classList.contains('decks-device-name');

    if (!renamingInProgress) {
      if (headingEl) headingEl.textContent = 'Registered with ' + (_serverName || 'this server');

      var entries = [];
      for (var id in _devicesRegistry) {
        if (!Object.prototype.hasOwnProperty.call(_devicesRegistry, id)) continue;
        if (id === _deviceId) continue; // this device -- not offered in the manage-others list
        entries.push({ id: id, device: _devicesRegistry[id] });
      }

      listEl.innerHTML = '';
      if (entries.length === 0) {
        if (emptyEl) emptyEl.classList.remove('hidden');
      } else {
        if (emptyEl) emptyEl.classList.add('hidden');
        entries.forEach(function(entry) {
          listEl.appendChild(_buildDecksDeviceRow(entry.id, entry.device));
        });
      }
    }
  }

  // Step 6 (§9.5): federated section, same rows/rules as the header
  // dropdown's third section (§9.1) -- just rendered as a real clickable
  // list instead of a <select>'s action-encoded options.
  var federatedListEl = $('decks-federated-list');
  var federatedEmptyEl = $('decks-federated-empty');
  if (federatedListEl) {
    var federatedRows = buildFederatedDevicesSection(_federatedDevicesRaw);
    federatedListEl.innerHTML = '';
    if (federatedRows.length === 0) {
      if (federatedEmptyEl) federatedEmptyEl.classList.remove('hidden');
    } else {
      if (federatedEmptyEl) federatedEmptyEl.classList.add('hidden');
      federatedRows.forEach(function(row) {
        federatedListEl.appendChild(_buildDecksFederatedRow(row));
      });
    }
  }
}

/**
 * Save a Decks-tab device-name-input's current value as that device's
 * display_name via PATCH /api/devices/{id} (§8.1 #3/#7) -- an empty string
 * clears display_name back to null (falls back to the device's own
 * self-reported label). Optimistically updates the local registry cache so
 * the next render reflects the saved value even before the following poll
 * tick's GET /api/state confirms it.
 * @param {Element} input
 * @returns {Promise<void>}
 */
async function _patchDeviceDisplayName(input) {
  var deviceId = input.getAttribute('data-device-id');
  if (!deviceId) return;
  var value = input.value.trim();
  try {
    const res = await api('PATCH', '/api/devices/' + encodeURIComponent(deviceId), { display_name: value === '' ? null : value });
    const result = await res.json();
    if (_devicesRegistry[deviceId]) _devicesRegistry[deviceId].display_name = result.display_name;
    showToast('Device renamed');
    renderSyncGroupControls();
  } catch (err) {
    console.warn('[_patchDeviceDisplayName] rename failed:', err);
    showToast('Could not rename device \u2014 try again.');
  }
}

// ─── Interaction tracking ─────────────────────────────────────────────────────
function trackInteraction() {
  _lastInteractionAt = Math.floor(Date.now() / 1000);
}

// ─── State restoration ───────────────────────────────────────────────────────
/**
 * Restore application state from the server on page load.
 * Calls GET /api/state and, if an active session exists, re-opens it,
 * skipping only the zoom animation (ttyd is re-spawned to handle service restarts).
 * Always resolves — errors are logged as warnings so the app can start normally.
 * @returns {Promise<void>}
 */
async function restoreState() {
  try {
    const res = await api('GET', withDevice('/api/state'));
    const state = await res.json();
    if (state.active_view) {
      _activeView = state.active_view;
    }
    if (state.active_session) {
      await openSession(state.active_session, {
        skipAnimation: true,
        remoteId: state.active_remote_id || '',
        isFollow: true, // adopting server truth on load, not a fresh local decision
      });
    }
  } catch (err) {
    if (err && err.status === 404) {
      // Device aged out of the registry (or never registered before this
      // request raced ahead of the first heartbeat). Re-register and let
      // the caller's own retry/poll cadence pick this up next tick — no
      // fallback to an un-scoped request, which would silently rejoin
      // global.
      sendHeartbeat().catch(function() {});
      return;
    }
    console.warn('[restoreState] could not restore previous session:', err);
  }
}

// ─── Connection status ──────────────────────────────────────────────────────────────────────────
/**
 * Update the #connection-status indicator element.
 * @param {'ok'|'warn'|'err'} level
 */
function setConnectionStatus(level) {
  const el = $('connection-status');
  if (!el) return;
  const map = {
    ok:   { text: '●',        cls: 'connection-status--ok' },
    warn: { text: '◌ slow',   cls: 'connection-status--warn' },
    err:  { text: '✕ offline', cls: 'connection-status--err' },
  };
  const s = map[level];
  if (!s) return;
  el.textContent = s.text;
  el.className = s.cls;
}

// ─── Session polling ─────────────────────────────────────────────────────────────────────────────
/**
 * Fetch sessions from the appropriate endpoint and update the UI.
 * Uses /api/federation/sessions when multi_device_enabled is true,
 * /api/sessions otherwise.
 * Called by startPolling.
 * @returns {Promise<void>}
 */
async function pollSessions() {
  try {
    var endpoint = (_serverSettings && _serverSettings.multi_device_enabled)
      ? '/api/federation/sessions'
      : '/api/sessions';
    // NOTE: session-follow (followRemoteActiveSession) deliberately does NOT
    // live here. The federation fetch can take seconds when remotes are down
    // (per-remote timeout x gather), which made deck->PWA follows ~8s late.
    // The dedicated pollActiveState() loop owns following on a fresh snapshot.
    const res = await api('GET', endpoint);
    const sessions = await res.json();
    const prev = _currentSessions;
    _currentSessions = sessions;
    _pollFailCount = 0;
    setConnectionStatus('ok');
    renderGrid(sessions);
    renderSidebar(sessions, _viewingSession, _viewingRemoteId);
    handleBellTransitions(prev, sessions);
    updateSessionPill(sessions);
    updateFaviconBadge();
    updatePageTitle();
  } catch (err) {
    _pollFailCount++;
    setConnectionStatus(_pollFailCount <= 2 ? 'warn' : 'err');
  }
}

/**
 * Follow a session switch made by another device (Stream Deck, agent, another
 * browser). active_session/active_remote_id are server-global (last writer
 * wins); when they change remotely we re-open the new session through the
 * exact path restoreState() uses on page load — openSession() with
 * skipAnimation — which re-renders the sidebar selection and re-attaches the
 * terminal directly (no "Reconnecting…" overlay).
 *
 * Conservative policy (option a): only auto-follow when a session is already
 * open in fullscreen (_viewingSession non-null). If the user is on the
 * grid/overview, a remote switch does NOT yank them into fullscreen.
 * Alternative (option b), if remote devices should fully drive the view:
 * drop the _viewingSession guard so the grid also follows.
 *
 * Self-initiated switches naturally no-op: openSession() updates
 * _viewingSession/_viewingRemoteId synchronously before its PATCH lands, so
 * the next poll sees no difference.
 *
 * @param {object|null} state - GET /api/state body, or null on fetch failure
 */
function followRemoteActiveSession(state) {
  if (!state || !state.active_session) return;
  if (_viewingSession == null) return; // option (a): never force-open from the grid
  var remoteId = state.active_remote_id || '';
  if (state.active_session === _viewingSession && remoteId === _viewingRemoteId) return;
  // A local switch may still be in flight (see _pendingLocalSwitches' comment):
  // the server hasn't confirmed it yet, so THIS divergence is stale, not a
  // genuine remote switch. Suppress until every in-flight local switch
  // settles; once the server does catch up, the equality check above exits
  // early on its own, so this guard never needs to be cleared explicitly.
  if (_pendingLocalSwitches > 0) return;
  // Same opts shape restoreState() uses (app.js restoreState) — skip the tile zoom animation.
  // Fire-and-forget: must not delay the poll loop or count as a poll failure.
  openSession(state.active_session, {
    skipAnimation: true,
    remoteId: remoteId,
    isFollow: true,
  }).catch(function (err) {
    console.warn('[followRemoteActiveSession] could not follow remote switch:', err);
  });
}

/**
 * Follow a view switch made by another device — Stream Deck, agent, another
 * browser — detected via the same /api/state poll that drives session-follow.
 * active_view is server-global (last writer wins); this tab applies the
 * received value locally and does NOT PATCH it back: we are echoing a value
 * we just received FROM the server, so re-PATCHing would be redundant (and a
 * feedback-loop hazard). User-initiated switches still PATCH via switchView().
 *
 * Self-initiated switches naturally no-op: persistActiveView() updates
 * _activeView synchronously (via applyViewLocally(), called by every one of
 * its callers before the PATCH fires) before its write lands, so the next
 * poll sees no difference once the write is confirmed.
 *
 * A local write may still be in flight (see _pendingViewSwitches' comment):
 * the server hasn't confirmed it yet, so a divergence THIS tick is stale,
 * not a genuine remote switch -- applying it would revert the UI to the
 * value the user just switched away from, then flip forward again once the
 * write lands (the reported flicker). Suppress until every in-flight local
 * write settles; once the server does catch up, the equality check below
 * exits early on its own, so this guard never needs to be cleared
 * explicitly.
 *
 * An unknown/deleted view renders as honestly empty (filterVisible returns
 * [] for a view it can't resolve) — same behavior as everywhere else.
 *
 * @param {object|null} state - GET /api/state body, or null on fetch failure
 */
function followRemoteActiveView(state) {
  if (!state || !state.active_view) return;
  if (state.active_view === _activeView) return;
  if (_pendingViewSwitches > 0) return;
  applyViewLocally(state.active_view);
}

/**
 * Persist active_view to the server -- the ONE place every active_view PATCH
 * goes through (switchView(), the delete-active-view fallback in
 * renderViewsSettingsTab(), and the rename-active-view path in
 * openManageViewPanel()'s commitRename()), so the race guard, device
 * scoping, and failure handling below can never drift between call sites
 * the way the styling rules once did.
 *
 * Race guard: increments _pendingViewSwitches before the request and
 * decrements it once the request settles (success OR failure) -- see that
 * variable's comment and followRemoteActiveView() above for the flicker
 * this closes. Driven by actual completion, not a wall-clock guess, so it
 * can't outlive the write it guards nor leak between unrelated switches
 * (matches openSession()'s _pendingLocalSwitches, the proven mechanism for
 * the identical class of bug on active_session).
 *
 * Uses withDevice() like every other /api/state write in this file -- the
 * two older call sites this replaces had drifted from that convention, so a
 * device in its own private sync group was writing active_view to the
 * shared "global" group instead of its own.
 *
 * Failure handling: a rejected/failed write must not leave the UI silently
 * showing a value the server never accepted. Fails loud (toast +
 * console.warn) and reconciles with whatever the server's active_view
 * actually is right now -- render-only via applyViewLocally(), same as
 * followRemoteActiveView(), never re-PATCHed. That value may be the one
 * from before this attempt, or a genuine change from another device that
 * raced in in the meantime; either way the server is authoritative and
 * this tab adopts it.
 *
 * Caller contract: apply the change locally (applyViewLocally()) BEFORE
 * calling this -- this function only persists; it does not render.
 *
 * @param {string} viewName - 'all', 'hidden', or a user view name.
 * @returns {Promise<void>}
 */
function persistActiveView(viewName) {
  _pendingViewSwitches++;
  return api('PATCH', withDevice('/api/state'), { active_view: viewName })
    .then(function() {
      _pendingViewSwitches--;
    })
    .catch(function(err) {
      _pendingViewSwitches--;
      console.warn('[persistActiveView] failed to persist active_view:', err);
      showToast('Failed to save view selection');
      return api('GET', withDevice('/api/state'))
        .then(function(res) { return res.json(); })
        .then(function(state) {
          if (state && state.active_view) applyViewLocally(state.active_view);
        })
        .catch(function() {
          // Reconciliation fetch also failed -- nothing more we can do here;
          // the next regular ~1s state poll will still self-correct once
          // connectivity returns, since _pendingViewSwitches is already
          // decremented above.
        });
    });
}

/**
 * Follow a settings/view-DEFINITION change made by another device or tab \u2014
 * e.g. a session added to (or removed from) a view via PATCH /api/settings.
 * Same class of bug as followRemoteActiveView() (which follows the active
 * *selection*), one layer deeper: this follows the view *membership data*
 * itself, which previously was fetched exactly once at page load
 * (loadServerSettings() in the DOMContentLoaded handler) and never refreshed,
 * so _serverSettings.views went stale until a hard page reload.
 *
 * Uses settings_updated_at (now carried on every /api/state poll response,
 * see main.py get_state()) as an efficient change signal instead of
 * re-fetching /api/settings every tick: only when the timestamp actually
 * differs from the last-seen value do we re-fetch (via the existing
 * loadServerSettings(), no duplicated fetch logic) and re-render the
 * view-dependent UI. An unchanged timestamp is a no-op \u2014 no fetch, no
 * re-render, no per-second churn.
 *
 * Render-only, like followRemoteActiveView(): it NEVER PATCHes anything
 * back. We are applying a settings snapshot we just received FROM the
 * server, so writing it back would be redundant and a feedback-loop hazard.
 * A tab's own settings PATCH also bumps settings_updated_at server-side, so
 * the next poll re-fetches and re-renders once more \u2014 that's correct/
 * idempotent (the server is authoritative) and matches what a second tab
 * would see.
 *
 * Robust to an older server that doesn't send settings_updated_at: absence
 * is treated as "no signal" \u2014 no fetch, no crash, no behavior change from
 * before this function existed.
 *
 * @param {object|null} state - GET /api/state body, or null on fetch failure
 * @returns {Promise<void>|undefined}
 */
function followRemoteViewDefinitions(state) {
  if (!state) return;
  var ts = state.settings_updated_at;
  if (ts === undefined || ts === null) return; // older server: no signal
  if (_lastSettingsUpdatedAt !== null && ts === _lastSettingsUpdatedAt) return; // unchanged
  _lastSettingsUpdatedAt = ts;
  return loadServerSettings().then(function() {
    renderViewDropdown();
    renderGrid(_currentSessions || []);
    renderSidebar(_currentSessions || [], _viewingSession, _viewingRemoteId);
    if (_settingsOpen) renderViewsSettingsTab();
    var manageViewPanel = $('manage-view-panel');
    if (manageViewPanel && !manageViewPanel.classList.contains('hidden')) {
      renderManageViewList();
      _renderManageViewRuleEditor(false); // false: don't clobber an unsaved in-progress edit
    }
  }).catch(function(err) {
    console.warn('[followRemoteViewDefinitions] could not refresh settings:', err);
  });
}

/**
 * Start the session polling loop. Guards against double-start.
 * Uses self-scheduling setTimeout so at most one poll is in-flight at a time.
 * If a poll takes longer than POLL_MS, the next poll starts POLL_MS after it
 * finishes — never while it is still running.
 */
function startPolling() {
  if (_pollingTimer) return;
  _pollingTimer = true; // sentinel: prevents double-start before first setTimeout fires
  async function pollLoop() {
    await pollSessions();
    // Re-arm only if the tab did not hide while that poll was in flight --
    // see the re-arm race note in the "Visibility handling" section below.
    if (!_visibilityPaused) _pollingTimer = setTimeout(pollLoop, POLL_MS);
  }
  pollLoop();
}

/**
 * Stop the session polling loop (cancels the pending reschedule, if any) and
 * clear the sentinel so a later startPolling() call is not treated as a
 * double-start no-op. Used by handleVisibilityChange() when the tab is
 * hidden, and as a test-reset helper.
 */
function stopPolling() {
  if (_pollingTimer) {
    clearTimeout(_pollingTimer);
    _pollingTimer = undefined;
  }
}

/**
 * Dedicated lightweight follow poll: fetch ONLY /api/state (~3ms server-side)
 * and hand the FRESH snapshot to followRemoteActiveSession().
 *
 * Deliberately independent of pollSessions(): when multi_device_enabled, the
 * sessions poll fetches /api/federation/sessions, which blocks for the full
 * per-remote timeout (seconds) whenever a federation remote is down. Following
 * on that cadence made deck->PWA session switches take ~8s. This poll never
 * touches federation, so a remote switch is detected within ~STATE_POLL_MS.
 *
 * Errors are swallowed (skip the tick): connection-status UI is owned by
 * pollSessions(), and a transient /api/state failure just delays the follow
 * by one tick.
 * @returns {Promise<void>}
 */
async function pollActiveState() {
  try {
    const res = await api('GET', withDevice('/api/state'));
    const state = await res.json();
    followRemoteActiveSession(state);
    followRemoteActiveView(state);
    followRemoteViewDefinitions(state);
    // Step 4: piggyback on this ALREADY-polled snapshot for the Follows
    // dropdown/controlled-by chip/Decks tab registry -- same "don't add a
    // second poll" discipline main.py's own settings_updated_at comment
    // documents. `devices` is present unconditionally on every REAL
    // /api/state response (state.py's empty_bootstrap) -- guarded on its
    // PRESENCE here (not just truthiness) so a minimal test mock that
    // omits it entirely is a true no-op, preserving "no re-render churn on
    // an unchanged poll" (tests/test_app.mjs). Change-detected via a cheap
    // JSON snapshot so a genuinely unchanged REAL registry is ALSO a
    // no-op -- only an actual change touches the DOM.
    if (Object.prototype.hasOwnProperty.call(state, 'devices')) {
      var devicesSnapshot = JSON.stringify(state.devices || {});
      if (devicesSnapshot !== _lastDevicesSnapshot) {
        _lastDevicesSnapshot = devicesSnapshot;
        _devicesRegistry = state.devices || {};
        renderSyncGroupControls();
      }
    }
  } catch (err) {
    if (err && err.status === 404) {
      // Device aged out of the registry (e.g. laptop slept past the prune
      // TTL). Re-register; the next tick (<=1s later) succeeds. No fallback
      // to an un-scoped request -- that would silently rejoin global.
      sendHeartbeat().catch(function() {});
      return;
    }
    // Transient failure: skip this tick; next one retries in STATE_POLL_MS.
  }
}

/**
 * Start the dedicated /api/state follow-poll loop. Guards against
 * double-start. Self-scheduling setTimeout (same pattern as startPolling)
 * so a slow response never overlaps the next tick.
 */
function startStatePolling() {
  if (_statePollTimer) return;
  _statePollTimer = true; // sentinel: prevents double-start before first setTimeout fires
  async function statePollLoop() {
    await pollActiveState();
    // Re-arm only if the tab did not hide while that poll was in flight --
    // see the re-arm race note in the "Visibility handling" section below.
    if (!_visibilityPaused) _statePollTimer = setTimeout(statePollLoop, STATE_POLL_MS);
  }
  statePollLoop();
}

/**
 * Stop the dedicated /api/state follow-poll loop (cancels the pending
 * reschedule, if any) and clear the sentinel so a later startStatePolling()
 * call is not treated as a double-start no-op. Used by
 * handleVisibilityChange() when the tab is hidden, and as a test-reset
 * helper.
 */
function stopStatePolling() {
  if (_statePollTimer) {
    clearTimeout(_statePollTimer);
    _statePollTimer = undefined;
  }
}

// ─── Visibility handling ────────────────────────────────────────────────────
//
// A backgrounded (or occluded-but-not-hidden, in the PWA case -- see
// deck.js's identical caveat) tab must not keep polling at full rate: with
// no visibilitychange handling at all, the session poll (POLL_MS=2000) and
// the dedicated state poll (STATE_POLL_MS=1000) kept running unthrottled,
// pinning the main thread against xterm/DOM work and contributing to a
// user-visible "beachball" stall whenever the tab regained focus. Mirrors
// deck.js's existing stopPolling()/releaseWakeLock() visibilitychange
// pattern (deck/deck.js's "Visibility handling" section).
//
// On hidden: pause both poll loops via the stop functions above.
// On visible: resume both loops (each fires its own single immediate poll
// as part of its existing start-up sequence -- see pollLoop()/
// statePollLoop() above, no separate "kick" needed) AND explicitly
// re-register via the existing sendHeartbeat() global. That re-register is
// deliberate, not redundant: the server prunes a device from its registry
// after ~300s without a heartbeat (see pollActiveState()'s 404 handling
// above), and browsers throttle or fully suspend background timers
// unpredictably during long backgrounding -- so a device can age out of the
// registry despite startHeartbeat()'s own loop nominally still running.
// Firing sendHeartbeat() immediately on resume (rather than waiting for
// whatever remains of the next natural HEARTBEAT_MS tick) re-registers
// before the resumed state poll's first tick, so a pruned device heals
// cleanly instead of surfacing one avoidable 404.
//
// THE RE-ARM RACE (why _visibilityPaused exists, and why stopPolling()
// alone is not enough). Both loops are self-scheduling: each awaits its own
// fetch, then arms the next timer. stopPolling()/stopStatePolling() can only
// cancel a timer that is ALREADY pending -- they have nothing to cancel when
// the loop is parked mid-await. So a hide that lands while a poll is in
// flight used to be silently undone: the clear ran first, then the
// in-flight poll resolved and re-armed the loop behind it, and the hidden
// tab kept polling forever. That is not an exotic interleaving -- at a 1s/2s
// cadence against a server whose federation fetch can block for seconds on a
// down remote (see pollSessions()'s note), it is the COMMON case, which
// means the visibility fix would have done nothing in exactly the situation
// it exists for. The flag closes it from the other side: hiding both cancels
// the pending timer AND refuses the next re-arm. Belt and braces, on purpose
// -- neither half is sufficient alone.
//
// Ordering is load-bearing on resume: the flag is cleared BEFORE
// startPolling()/startStatePolling(), or the fresh loops would immediately
// refuse to re-arm and die after a single tick. Only this function ever
// writes the flag; stopPolling()/stopStatePolling() stay pure timer stops so
// they remain usable as plain "stop this loop" helpers (and as test resets)
// without implying anything about visibility.
function handleVisibilityChange() {
  if (document.hidden) {
    _visibilityPaused = true;
    stopPolling();
    stopStatePolling();
  } else {
    _visibilityPaused = false;
    sendHeartbeat().catch(function() {});
    startPolling();
    startStatePolling();
  }
}
document.addEventListener('visibilitychange', handleVisibilityChange);

// ─── Grid rendering ──────────────────────────────────────────────────────────

/**
 * Escape HTML special characters to safe entities.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// ---------------------------------------------------------------------------
// ANSI escape → HTML span converter (SGR codes only)
// Converts terminal color sequences to <span> tags with inline styles.
// ---------------------------------------------------------------------------
var ANSI_COLORS = [
  '#2e3436','#cc0000','#4e9a06','#c4a000','#3465a4','#75507b','#06989a','#d3d7cf',
  '#555753','#ef2929','#8ae234','#fce94f','#729fcf','#ad7fa8','#34e2e2','#eeeeec'
];

function ansiToHtml(raw) {
  if (!raw) return '';
  var out = '';
  var spans = 0;
  var i = 0;
  var len = raw.length;

  while (i < len) {
    // Look for ESC [ ... m  (SGR sequence)
    if (raw[i] === '\x1b' && raw[i + 1] === '[') {
      var j = i + 2;
      while (j < len && raw[j] !== 'm' && j - i < 20) j++;
      if (j < len && raw[j] === 'm') {
        var params = raw.substring(i + 2, j).split(';');
        var style = ansiParamsToStyle(params);
        if (style === 'reset') {
          // Close all open spans
          while (spans > 0) { out += '</span>'; spans--; }
        } else if (style) {
          out += '<span style="' + style + '">';
          spans++;
        }
        i = j + 1;
        continue;
      }
    }
    // Escape HTML characters
    var ch = raw[i];
    if (ch === '<') out += '&lt;';
    else if (ch === '>') out += '&gt;';
    else if (ch === '&') out += '&amp;';
    else if (ch === '"') out += '&quot;';
    else out += ch;
    i++;
  }
  while (spans > 0) { out += '</span>'; spans--; }
  return out;
}

function ansiParamsToStyle(params) {
  var styles = [];
  var k = 0;
  while (k < params.length) {
    var p = parseInt(params[k], 10) || 0;
    if (p === 0) return 'reset';
    if (p === 1) styles.push('font-weight:bold');
    else if (p === 2) styles.push('opacity:0.7');
    else if (p === 3) styles.push('font-style:italic');
    else if (p === 4) styles.push('text-decoration:underline');
    else if (p === 7) styles.push('filter:invert(1)');
    else if (p === 9) styles.push('text-decoration:line-through');
    else if (p >= 30 && p <= 37) styles.push('color:' + ANSI_COLORS[p - 30]);
    else if (p === 38 && params[k + 1] === '5') {
      var c = parseInt(params[k + 2], 10) || 0;
      styles.push('color:' + ansi256Color(c));
      k += 2;
    }
    else if (p === 39) styles.push('color:inherit');
    else if (p >= 40 && p <= 47) styles.push('background:' + ANSI_COLORS[p - 40]);
    else if (p === 48 && params[k + 1] === '5') {
      var c2 = parseInt(params[k + 2], 10) || 0;
      styles.push('background:' + ansi256Color(c2));
      k += 2;
    }
    else if (p === 49) styles.push('background:inherit');
    else if (p >= 90 && p <= 97) styles.push('color:' + ANSI_COLORS[p - 90 + 8]);
    else if (p >= 100 && p <= 107) styles.push('background:' + ANSI_COLORS[p - 100 + 8]);
    k++;
  }
  return styles.length ? styles.join(';') : '';
}

function ansi256Color(n) {
  if (n < 16) return ANSI_COLORS[n];
  if (n >= 232) { var g = 8 + (n - 232) * 10; return 'rgb(' + g + ',' + g + ',' + g + ')'; }
  n -= 16;
  var r = Math.floor(n / 36) * 51;
  var g2 = Math.floor((n % 36) / 6) * 51;
  var b = (n % 6) * 51;
  return 'rgb(' + r + ',' + g2 + ',' + b + ')';
}

/**
 * Format a device's version for display (tooltip/badge text).
 * Returns 'version unknown' for null/undefined/empty rather than falling
 * back to any guessed value -- an unknown that looked like agreement with
 * the local version would be worse than showing no data at all.
 * @param {string|null|undefined} version
 * @returns {string}
 */
function formatDeviceVersion(version) {
  return version ? ('v' + version) : 'version unknown';
}

// Closed vocabulary for deviceLabelPlacement. An unknown value (a hand-edited
// settings.json, a peer from a future version) resolves to 'titlebar' -- today's
// behavior -- exactly as activityIndicator resolves unknown values to 'both'.
const DEVICE_LABEL_PLACEMENTS = ['titlebar', 'corner', 'off'];

/**
 * Resolve the effective device-label placement from display settings.
 * @param {object} ds - getDisplaySettings() result
 * @returns {'titlebar'|'corner'|'off'}
 */
function deviceLabelPlacement(ds) {
  var v = ds && ds.deviceLabelPlacement;
  return DEVICE_LABEL_PLACEMENTS.indexOf(v) !== -1 ? v : 'titlebar';
}

/**
 * Build the HTML string for a single session tile.
 * @param {object} session
 * @param {number} index
 * @param {boolean} mobile
 * @returns {string}
 */
function buildTileHTML(session, index, mobile) {
  const priority = sessionPriority(session);
  const isBell = priority === 'bell';

  var ds = getDisplaySettings();
  var actIndicator = ds.activityIndicator !== undefined ? ds.activityIndicator : 'both';

  let classes = 'session-tile';
  // Glow (full border + inner glow): applied when actIndicator is 'glow' or 'both'
  if (isBell && (actIndicator === 'glow' || actIndicator === 'both')) classes += ' session-tile--bell';
  // Edge bar only (left border amber, no glow): applied when actIndicator is 'dot' or 'both'
  if (isBell && (actIndicator === 'dot' || actIndicator === 'both')) classes += ' session-tile--edge-bell';
  if (mobile) classes += ` session-tile--tier-${priority}`;

  const name = session.name || '';
  const escapedName = escapeHtml(name);
  const timeStr = formatTimestamp(session.last_activity_at || null);

  // Device label — placement governed by deviceLabelPlacement (see
  // docs/plans/2026-08-04-device-label-placement-plan.md). The multi_device_enabled + deviceName guard is
  // unchanged from the showDeviceBadges era: a single-device install draws
  // no label in any placement.
  var placement = deviceLabelPlacement(ds);
  var showDeviceLabel = !!(_serverSettings && _serverSettings.multi_device_enabled
    && session.deviceName) && placement !== 'off';
  let badgeHtml = '';
  let cornerHtml = '';
  if (showDeviceLabel && placement === 'titlebar') {
    badgeHtml = `<span class="device-badge" title="${escapeHtml(formatDeviceVersion(session.deviceVersion))}">${escapeHtml(session.deviceName)}</span>`;
  } else if (showDeviceLabel && placement === 'corner') {
    cornerHtml = `<span class="tile-device-tag">${escapeHtml(session.deviceName)}</span>`;
  }

  // Last N lines of snapshot — show more in fit mode so tall tiles fill
  const snapshot = session.snapshot || '';
  var _lineCount = (ds.viewMode === 'fit') ? -80 : -20;
  // Trim trailing blank lines from the FULL snapshot FIRST — sessions with the cursor
  // near the top (e.g. fresh tunnel/ssh session) have content at rows 1-2 and rows 3-40
  // blank. slice(-20) would grab the last 20 rows (all blank); trimming after slice
  // then removes everything → empty preview. Trim first so slice sees only content rows.
  var allLines = snapshot.split('\n');
  while (allLines.length > 0 && allLines[allLines.length - 1].trim() === '') {
    allLines.pop();
  }
  const lastLines = allLines.slice(_lineCount).join('\n');

  // Use remoteId (null for local sessions, device_id string for remote sessions) so
  // openSession() can correctly distinguish local vs federation routing.
  // deviceId is the local device's own UUID for local sessions — using it here would
  // cause openSession() to route local sessions through /api/federation/{deviceId}/…
  // which returns 404 because the local device is not a registered remote instance.
  const remoteIdAttr = session.remoteId != null ? ` data-remote-id="${escapeHtml(String(session.remoteId))}"` : '';
  // aria-label carries the device name unconditionally across all three
  // placements (Q3's accessibility guarantee): assistive tech never loses
  // the disambiguator even when the pixels do.
  const ariaLabel = (_serverSettings && _serverSettings.multi_device_enabled && session.deviceName)
    ? `${name} on ${session.deviceName}`
    : name;
  return (
    `<article class="${classes}" data-session="${escapedName}" data-session-key="${escapeHtml(session.sessionKey || name)}"${remoteIdAttr} tabindex="0" role="listitem" aria-label="${escapeHtml(ariaLabel)}">` +
    `<div class="tile-header">` +
    `<span class="tile-name">${escapeHtml(name)}</span>` +
    `${badgeHtml}` +
    `<span class="tile-meta">${escapeHtml(timeStr)}</span>` +
    `<button class="tile-options-btn" data-session="${escapedName}" aria-label="Session options" aria-haspopup="true">&#8942;</button>` +
    `</div>` +
    `<div class="tile-body"><pre>${ansiToHtml(lastLines)}</pre>${cornerHtml}</div>` +
    `</article>`
  );
}

/**
 * Build the HTML string for a single session sidebar card.
 * @param {object} session
 * @param {string} currentSession - name of the currently active session
 * @param {string} currentRemoteId - remoteId of the currently active session
 * @returns {string}
 */
function buildSidebarHTML(session, currentSession, currentRemoteId) {
  const name = session.name || '';
  const escapedName = escapeHtml(name);
  const isActive = name === currentSession && (session.remoteId ?? '') === (currentRemoteId ?? '');

  var ds = getDisplaySettings();
  var actIndicator = ds.activityIndicator !== undefined ? ds.activityIndicator : 'both';

  const isBell = sessionPriority(session) === 'bell';

  let classes = 'sidebar-item';
  if (isActive) classes += ' sidebar-item--active';
  // Glow (full border + inner glow): applied when actIndicator is 'glow' or 'both'
  if (isBell && (actIndicator === 'glow' || actIndicator === 'both')) classes += ' sidebar-item--bell';
  // Edge bar only (left border amber, no glow): applied when actIndicator is 'dot' or 'both'
  if (isBell && (actIndicator === 'dot' || actIndicator === 'both')) classes += ' sidebar-item--edge-bell';

  // Device label — placement governed by deviceLabelPlacement (see
  // docs/plans/2026-08-04-device-label-placement-plan.md). Identical semantics to buildTileHTML: the label
  // is drawn in the preview, lower-right, everywhere a preview is drawn.
  var placement = deviceLabelPlacement(ds);
  var showDeviceLabel = !!(_serverSettings && _serverSettings.multi_device_enabled
    && session.deviceName) && placement !== 'off';
  let badgeHtml = '';
  let cornerHtml = '';
  if (showDeviceLabel && placement === 'titlebar') {
    badgeHtml = `<span class="device-badge" title="${escapeHtml(formatDeviceVersion(session.deviceVersion))}">${escapeHtml(session.deviceName)}</span>`;
  } else if (showDeviceLabel && placement === 'corner') {
    cornerHtml = `<span class="tile-device-tag">${escapeHtml(session.deviceName)}</span>`;
  }

  // Last 20 lines of snapshot — trim trailing blanks from the FULL snapshot FIRST,
  // then slice. Sessions with the cursor near the top have content at rows 1-2 and
  // rows 3-40 blank; slice(-20) would return only blank rows, then trim-after-slice
  // removes everything → empty preview. Trim first to keep meaningful content.
  const snapshot = session.snapshot || '';
  var allLines = snapshot.split('\n');
  while (allLines.length > 0 && allLines[allLines.length - 1].trim() === '') {
    allLines.pop();
  }
  const lastLines = allLines.slice(-20).join('\n');

  // Use remoteId (null for local sessions, device_id string for remote sessions).
  // Do NOT use deviceId here: for local sessions deviceId is the local machine's UUID
  // which is not a registered remote_instance, so routing through federation would 404.
  var _sidebarEffRemoteId = session.remoteId != null ? String(session.remoteId) : '';
  return (
    `<article class="${classes}" data-session="${escapedName}" data-session-key="${escapeHtml(session.sessionKey || name)}" data-remote-id="${escapeHtml(_sidebarEffRemoteId)}" tabindex="0" role="listitem">` +
    `<div class="sidebar-item-header">` +
    `<span class="sidebar-item-name">${escapedName}</span>` +
    badgeHtml +
    `<button class="tile-options-btn" data-session="${escapedName}" aria-label="Session options" aria-haspopup="true">&#8942;</button>` +
    `</div>` +
    `<div class="sidebar-item-body"><pre>${ansiToHtml(lastLines)}</pre>${cornerHtml}</div>` +
    `</article>`
  );
}

/**
 * Build the HTML string for a generic status tile (auth_failed or unreachable).
 * @param {string} deviceName
 * @param {string} statusText
 * @param {string} statusClass
 * @param {string|null} [deviceVersion] - remote's reported version, or null/undefined if unknown
 * @returns {string}
 */
function buildStatusTileHTML(deviceName, statusText, statusClass, deviceVersion) {
  return (
    '<article class="source-tile source-tile--' + statusClass + '">' +
    '<span class="source-tile__name">' + escapeHtml(deviceName || '') + '</span>' +
    '<span class="source-tile__badge">' + escapeHtml(statusText || '') + '</span>' +
    '<span class="source-tile__version">' + escapeHtml(formatDeviceVersion(deviceVersion)) + '</span>' +
    '</article>'
  );
}

// ---------------------------------------------------------------------------
// v2 visibility helpers — single source of truth for session filtering.
// See docs/plans/2026-05-17-hidden-state-redesign-design.md
// ---------------------------------------------------------------------------

// Returns true if the session key is in settings.hidden_sessions.
function isHidden(key, settings) {
  var hidden = (settings && settings.hidden_sessions) || [];
  return hidden.indexOf(key) !== -1;
}

// Canonical session-list filter. Single source of truth for "what is in this
// view right now". See docs/plans/2026-05-17-hidden-state-redesign-design.md.
// view: "all" | "hidden" | <user view name>
// options.includeHidden: when true, hidden sessions are NOT filtered out of
//   "all" or user views. Ignored for "hidden" (always shows only hidden).
function filterVisible(sessions, settings, view, options) {
  options = options || {};
  var includeHidden = options.includeHidden === true;
  var hiddenList = (settings && settings.hidden_sessions) || [];
  var live = (sessions || []).filter(function (s) { return !s.status; });

  function keyOf(s) { return s.sessionKey || s.name; }
  function isSessionHidden(s) {
    return hiddenList.indexOf(keyOf(s)) !== -1 || hiddenList.indexOf(s.name) !== -1;
  }

  if (view === "hidden") {
    return live.filter(isSessionHidden);
  }
  if (view === "all") {
    if (includeHidden) return live.slice();
    return live.filter(function (s) { return !isSessionHidden(s); });
  }

  var views = (settings && settings.views) || [];
  var userView = null;
  for (var i = 0; i < views.length; i++) {
    if (views[i].name === view) { userView = views[i]; break; }
  }
  if (!userView) return []; // keep: the documented "unknown view -> empty" contract

  // Membership is a lookup against the server's resolved answer, not a
  // client-side re-derivation (docs/plans/2026-08-04-auto-views-plan.md §0.1/§9.1): every session
  // dict from GET /api/sessions and GET /api/federation/sessions carries
  // `views` (pins union glob-rule matches). No fallback to the old dual-key
  // search against `userView.sessions` -- deliberately: the PWA is served by
  // the SAME server that annotates (with Cache-Control: no-cache, load-
  // bearing per AGENTS.md), and the local server annotates remote sessions
  // too, so the annotation is present regardless of any peer's version. A
  // missing `s.views` is therefore a server bug, and a silent dual path
  // would hide it. Provenance (pinned vs. matched) is read from
  // `userView.sessions` at its OWN call sites (the view pickers), not here.
  function inView(s) {
    return (s.views || []).indexOf(view) !== -1;
  }
  if (includeHidden) {
    return live.filter(inView);
  }
  return live.filter(function (s) { return inView(s) && !isSessionHidden(s); });
}

function visibleCount(sessions, settings, view, options) {
  return filterVisible(sessions, settings, view, options).length;
}

/**
 * Returns sessions filtered by the active view.
 *
 * Thin wrapper around filterVisible() — the canonical filter that is the
 * single source of truth for "what is in this view right now".
 * See docs/plans/2026-05-17-hidden-state-redesign-design.md.
 *
 * @param {object[]} sessions
 * @returns {object[]}
 */
function getVisibleSessions(sessions) {
  return filterVisible(sessions, _serverSettings, _activeView);
}

// =============================================================================
// Operation layer (Phase 2)
//
// Two layers:
//   1. Pure data ops: _opAddMembership/_opRemoveMembership/_opHide/_opUnhide —
//      narrow, composable, no side effects beyond their name. Operate on a
//      local settings object (typically a clone of _serverSettings) so callers
//      can compose multiple operations before PATCHing.
//   2. User-intent ops: hideSessionOp/unhideSessionOp/addSessionToViewOp/
//      removeSessionFromViewOp — express what the *user* meant. Some compose
//      multiple pure ops:
//        - hideSessionOp  = hide + removeFromAllViews  (asymmetric: v1
//          federation-safe; matches current UX)
//        - addSessionToViewOp = unhide + addMembership  (auto-unhide on add,
//          explicit composition)
//        - unhideSessionOp = unhide (orthogonal — does NOT touch membership)
//        - removeSessionFromViewOp = removeMembership (orthogonal — does NOT
//          hide)
//
//   The asymmetry between hideSessionOp (which removes from views) and
//   addSessionToViewOp (which unhides) is intentional. See
//   docs/plans/2026-05-17-hidden-state-redesign-design.md.
// =============================================================================

// --- Pure data ops ---

// Add `key` to view's session list if absent. No-op if view doesn't exist.
function _opAddMembership(state, viewName, key) {
  var views = state.views || [];
  for (var i = 0; i < views.length; i++) {
    if (views[i].name === viewName) {
      var sessions = views[i].sessions || [];
      if (sessions.indexOf(key) === -1) {
        sessions.push(key);
        views[i].sessions = sessions;
      }
      break;
    }
  }
}

// Remove `key` from view's session list. No-op if view or key absent.
function _opRemoveMembership(state, viewName, key) {
  var views = state.views || [];
  for (var i = 0; i < views.length; i++) {
    if (views[i].name === viewName) {
      var sessions = views[i].sessions || [];
      var pos = sessions.indexOf(key);
      if (pos !== -1) {
        sessions.splice(pos, 1);
        views[i].sessions = sessions;
      }
      break;
    }
  }
}

// Remove `key` from every view's session list.
function _opRemoveFromAllViews(state, key) {
  var views = state.views || [];
  for (var i = 0; i < views.length; i++) {
    var sessions = views[i].sessions || [];
    var pos = sessions.indexOf(key);
    if (pos !== -1) {
      sessions.splice(pos, 1);
      views[i].sessions = sessions;
    }
  }
}

// Append `key` to hidden_sessions if absent.
function _opHide(state, key) {
  if (state.hidden_sessions.indexOf(key) === -1) {
    state.hidden_sessions.push(key);
  }
}

// Remove `key` from hidden_sessions. No-op if absent.
function _opUnhide(state, key) {
  var pos = state.hidden_sessions.indexOf(key);
  if (pos !== -1) {
    state.hidden_sessions.splice(pos, 1);
  }
}

// Helper: deep-clone the bits we mutate (avoid touching the cached
// _serverSettings before the PATCH confirms).
function _cloneOpState(settings) {
  return JSON.parse(JSON.stringify({
    hidden_sessions: (settings && settings.hidden_sessions) || [],
    views: (settings && settings.views) || []
  }));
}

// --- User-intent ops ---
// Each returns the patch body for /api/settings.

// hideSessionOp: hide(k) + removeFromAllViews(k).
// Asymmetric — removes from all views. This is the v1 federation-safe
// behaviour; matching the current UX. Returns { hidden_sessions, views }.
function hideSessionOp(settings, key) {
  var state = _cloneOpState(settings);
  _opHide(state, key);
  _opRemoveFromAllViews(state, key);
  return { hidden_sessions: state.hidden_sessions, views: state.views };
}

// unhideSessionOp: unhide(k) only.
// Orthogonal — does NOT touch view membership. Returns { hidden_sessions }.
function unhideSessionOp(settings, key) {
  var state = _cloneOpState(settings);
  _opUnhide(state, key);
  return { hidden_sessions: state.hidden_sessions };
}

// addSessionToViewOp: unhide(k) + addMembership(k, viewName).
// Auto-unhide on add is explicit composition here, not an invariant
// enforced elsewhere. Returns { hidden_sessions, views }.
function addSessionToViewOp(settings, viewName, key) {
  var state = _cloneOpState(settings);
  _opUnhide(state, key);
  _opAddMembership(state, viewName, key);
  return { hidden_sessions: state.hidden_sessions, views: state.views };
}

// removeSessionFromViewOp: removeMembership(k, viewName) only.
// Orthogonal — does NOT hide the session. Returns { views }.
function removeSessionFromViewOp(settings, viewName, key) {
  var state = _cloneOpState(settings);
  _opRemoveMembership(state, viewName, key);
  return { views: state.views };
}

/**
 * Resolve the active view name against the known views list.
 *
 * If active_view is "all" or "hidden" it is always valid and returned as-is.
 * If active_view matches a view name in the views list it is returned as-is.
 * Otherwise (e.g. the view was deleted while this device was offline) fall back
 * to "all" so the user always sees sessions rather than an empty/broken state.
 *
 * @param {string} activeView - The stored active_view value from state.
 * @param {object[]} views - The views array from settings (each has a .name field).
 * @returns {string} Resolved view name — always "all", "hidden", or a known view name.
 */
function _resolveActiveView(activeView, views) {
  if (activeView === 'all' || activeView === 'hidden') return activeView;
  var list = views || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i].name === activeView) return activeView;
  }
  return 'all';
}

/**
 * Render the session sidebar list. Only renders in fullscreen view.
 * Shows empty state when no sessions exist.
 * Binds click handlers on each sidebar-item to switch sessions.
 * @param {object[]} sessions
 * @param {string|null} currentSession - name of the currently active session
 * @param {string} [currentRemoteId] - remoteId of the currently active session
 */
function renderSidebar(sessions, currentSession, currentRemoteId) {
  if (_viewMode !== 'fullscreen') return;

  const list = $('sidebar-list');
  if (!list) return;

  const visible = getVisibleSessions(sessions);

  if (visible.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">No sessions</div>';
    return;
  }

  // Apply the same sort_order setting as renderGrid, via the shared
  // applySortOrder() helper -- previously the sidebar applied no sort_order
  // logic at all and always showed server-provided order, so it silently
  // disagreed with the grid whenever a non-default sort was selected.
  // currentSession/currentRemoteId (this render's own params) are NOT
  // forwarded to applySortOrder -- 'attention' sort has no currently-open-
  // session tier (see sortByAttention()'s docstring); they're used below
  // only for buildSidebarHTML()'s `isActive` highlight, an unrelated concern.
  const sortOrder = _serverSettings && _serverSettings.sort_order;
  const mobile = isMobile();
  const ordered = applySortOrder(visible, sortOrder, mobile);

  let html = '';

  if (_serverSettings && _serverSettings.multi_device_enabled) {
    // Group sessions by deviceName when multi_device_enabled. Order within
    // each group follows `ordered` (groups are built by iterating it in
    // order, and Map preserves insertion order), so e.g. attention sort still
    // surfaces the freshest bell first within each device's section.
    const groups = new Map();
    for (const session of ordered) {
      const deviceName = session.deviceName || 'Unknown';
      if (!groups.has(deviceName)) groups.set(deviceName, []);
      groups.get(deviceName).push(session);
    }

    for (const [deviceName, deviceSessions] of groups) {
      const groupVersion = deviceSessions.length > 0 ? deviceSessions[0].deviceVersion : null;
      html += `<h4 class="sidebar-device-header">${escapeHtml(deviceName)} <span class="sidebar-device-header__version">${escapeHtml(formatDeviceVersion(groupVersion))}</span></h4>`;
      html += deviceSessions.map((session) => buildSidebarHTML(session, currentSession, currentRemoteId)).join('');
    }
  } else {
    // Single source: flat list with no device headers
    html = ordered.map((session) => buildSidebarHTML(session, currentSession, currentRemoteId)).join('');
  }

  list.innerHTML = html;

  // Bind click handlers on each sidebar item, passing remoteId
  if (typeof list.querySelectorAll === 'function') {
    list.querySelectorAll('.sidebar-item').forEach((item) => {
      const name = item.dataset.session;
      const remoteId = item.dataset.remoteId || '';
      on(item, 'click', (e) => {
        if (e.target.closest && e.target.closest('.tile-options-btn')) return;
        if (name !== currentSession || remoteId !== (currentRemoteId ?? '')) openSession(name, { remoteId });
      });
    });
  }

}

const SIDEBAR_NARROW_THRESHOLD = 960;

/**
 * Initialise sidebar open/closed state on page load.
 * Reads sidebarOpen from _serverSettings cache.
 * Defaults to open on wide screens (innerWidth >= 960) when no stored value.
 * Applies sidebar--collapsed class accordingly and persists the initial state.
 */
function initSidebar() {
  var stored = _serverSettings ? _serverSettings.sidebarOpen : null;
  var isOpen;

  if (stored !== null && stored !== undefined) {
    isOpen = !!stored;
  } else {
    isOpen = window.innerWidth >= SIDEBAR_NARROW_THRESHOLD;
    // Persist the auto-detected value (fire-and-forget)
    if (_serverSettings) _serverSettings.sidebarOpen = isOpen;
    patchServerSetting('sidebarOpen', isOpen);
  }

  var sidebar = $('session-sidebar');
  if (sidebar) {
    if (isOpen) {
      sidebar.classList.remove('sidebar--collapsed');
    } else {
      sidebar.classList.add('sidebar--collapsed');
    }
  }
}

/**
 * Toggle the sidebar open/closed state.
 * Derives current state from DOM class, inverts it, persists to server,
 * and applies the sidebar--collapsed class. #sidebar-collapse-btn (the
 * old in-sidebar chevron this function used to also update) was removed
 * from the DOM in this pass -- the expanded header's #sidebar-toggle-btn
 * hamburger is the only trigger for this now, so there is no second
 * button's text to keep in sync.
 */
function toggleSidebar() {
  var sidebar = $('session-sidebar');
  if (!sidebar) return;

  var isOpen = !sidebar.classList.contains('sidebar--collapsed');
  isOpen = !isOpen;

  if (isOpen) {
    sidebar.classList.remove('sidebar--collapsed');
  } else {
    sidebar.classList.add('sidebar--collapsed');
  }

  if (_serverSettings) _serverSettings.sidebarOpen = isOpen;
  patchServerSetting('sidebarOpen', isOpen);
}

/**
 * Bind a click-away handler on #terminal-container that collapses the sidebar
 * when the user taps outside of it in overlay mode (window.innerWidth < 960).
 * Returns early without collapsing if:
 *   - the screen is wide enough that the sidebar is not in overlay mode (>= 960px)
 *   - the sidebar element is missing
 *   - the sidebar is already collapsed
 */
function bindSidebarClickAway() {
  var container = $('terminal-container');
  if (!container) return;
  container.addEventListener('click', function() {
    if (window.innerWidth >= SIDEBAR_NARROW_THRESHOLD) return;
    var sidebar = $('session-sidebar');
    if (!sidebar) return;
    if (sidebar.classList.contains('sidebar--collapsed')) return;
    sidebar.classList.add('sidebar--collapsed');
    if (_serverSettings) _serverSettings.sidebarOpen = false;
    patchServerSetting('sidebarOpen', false);
  });
}

/**
 * Render the session grid. Shows empty state when no sessions exist.
 * On mobile, sorts sessions by priority before rendering.
 * Binds click and keydown handlers on each tile.
 * @param {object[]} sessions
 */

/**
 * Render sessions grouped by device name. Returns HTML string.
 * @param {object[]} sessions - sorted, visible sessions
 * @param {boolean} mobile
 * @returns {string}
 */
function renderGroupedGrid(sessions, mobile) {
  // Group by deviceName
  var groups = {};
  var groupOrder = [];
  for (var i = 0; i < sessions.length; i++) {
    var dn = sessions[i].deviceName || 'Unknown';
    if (!groups[dn]) {
      groups[dn] = [];
      groupOrder.push(dn);
    }
    groups[dn].push(sessions[i]);
  }

  var html = '';
  for (var g = 0; g < groupOrder.length; g++) {
    var name = groupOrder[g];
    var groupSessions = groups[name];
    // Skip device entirely when it has no visible sessions to render.
    // This prevents empty device headers from appearing in the grouped grid
    // (e.g. when all of a device's sessions are hidden in the current view).
    if (groupSessions.length === 0) continue;
    html += '<h3 class="device-group-header">' + escapeHtml(name) + '</h3>';
    for (var j = 0; j < groupSessions.length; j++) {
      html += buildTileHTML(groupSessions[j], j, mobile);
    }
  }
  return html;
}

/**
 * Render the filter pill bar into the given container element.
 * Generates one 'All' pill plus one pill per unique device name found in allSessions.
 * The currently active device pill is marked with the `filter-pill--active` class.
 * @param {Element} container - The DOM element to render pills into.
 * @param {Array} allSessions - Full (unfiltered) session list used to derive device names.
 */
function renderFilterBar(container, allSessions) {
  // Dead code: filter bar replaced by Views feature. Kept as empty stub for export compatibility.
}

// ---------------------------------------------------------------------------
// Quick dropdown controller -- shared open/close/toggle/keyboard/click-away
// MECHANISM for every "quick control" popup: a <button aria-haspopup="true"
// aria-expanded="..."> trigger plus a sibling role="menu" popup. Used by all
// four instances below (header + sidebar view switcher, header + sidebar
// sort control).
//
// v0.47.9: the sort controls used to be native <select> elements -- four
// rounds of CSS-only fixes (see the "Quick link" section of style.css)
// could not make a <select>'s hover/focus/open behavior genuinely match a
// <button>'s, because a <select>'s own popup, focus ring, and value display
// are rendered by the browser, outside CSS's (and JS's) reach. Converting
// the sort controls to the same button+menu shape the view dropdown already
// used made "one implementation, four instances" possible for the first
// time -- this section IS that one implementation. What's shared is the
// MECHANISM (open/close/toggle, aria-expanded sync, fixed positioning for
// the two sidebar instances that must escape the sidebar's own
// overflow:hidden, arrow/Enter/Escape keyboard navigation, mutual
// exclusivity so opening one closes any other that's open); each instance's
// MENU CONTENT stays distinct (renderViewDropdown/renderSidebarViewDropdown
// vs renderSortDropdown/renderSidebarSortDropdown) because the view menu's
// counts/separators/manage-actions are genuinely different data from the
// sort menu's four static options -- content is policy, the popup mechanics
// are the mechanism (see KERNEL_PHILOSOPHY.md).
// ---------------------------------------------------------------------------

/** Registry of every quick dropdown created via createQuickDropdown(). */
var _quickDropdowns = [];

/**
 * Create a controller for one quick-dropdown instance.
 * @param {object} cfg
 * @param {string} cfg.triggerId - id of the <button> trigger
 * @param {string} cfg.menuId - id of the role="menu" popup
 * @param {function():void} [cfg.render] - populates menu content; called on open
 * @param {boolean} [cfg.fixedPosition] - sidebar instances: position via
 *   getBoundingClientRect() to escape the sidebar's own overflow:hidden
 *   clipping (mirrors the pre-v0.47.9 toggleSidebarViewDropdown() behavior)
 * @param {function(Element):void} [cfg.onClose] - cleanup before hiding the
 *   menu (e.g. the view dropdown removes its transient new-view input)
 * @returns {object} the registered entry -- pass to openQuickDropdown() /
 *   closeQuickDropdown() / toggleQuickDropdown() / isQuickDropdownOpen()
 */
function createQuickDropdown(cfg) {
  var entry = {
    triggerId: cfg.triggerId,
    menuId: cfg.menuId,
    render: cfg.render,
    fixedPosition: !!cfg.fixedPosition,
    onClose: cfg.onClose,
  };
  _quickDropdowns.push(entry);
  return entry;
}

function isQuickDropdownOpen(entry) {
  var menu = $(entry.menuId);
  return !!menu && !menu.classList.contains('hidden');
}

/**
 * Open one quick dropdown, closing every other registered one first (a menu
 * system shows at most one popup at a time -- e.g. the header's view and
 * sort dropdowns sit side by side and must not both be open together).
 */
function openQuickDropdown(entry) {
  _quickDropdowns.forEach(function(other) {
    if (other !== entry) closeQuickDropdown(other);
  });
  var menu = $(entry.menuId);
  var trigger = $(entry.triggerId);
  if (!menu) return;
  if (entry.fixedPosition && trigger) {
    // Fixed (not absolute) positioning escapes the sidebar's own
    // overflow:hidden, which would otherwise clip an absolutely-positioned
    // popup to the sidebar's own bounds.
    var rect = trigger.getBoundingClientRect();
    menu.style.top = (rect.bottom + 2) + 'px';
    menu.style.left = rect.left + 'px';
  }
  menu.classList.remove('hidden');
  if (trigger) trigger.setAttribute('aria-expanded', 'true');
  if (entry.render) entry.render();
}

function closeQuickDropdown(entry) {
  var menu = $(entry.menuId);
  var trigger = $(entry.triggerId);
  if (menu) {
    if (entry.onClose) entry.onClose(menu);
    menu.classList.add('hidden');
  }
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
}

function toggleQuickDropdown(entry) {
  if (isQuickDropdownOpen(entry)) closeQuickDropdown(entry);
  else openQuickDropdown(entry);
}

/**
 * Arrow/Enter/Escape keyboard handling for whichever registered quick
 * dropdown is currently open (at most one, per openQuickDropdown()'s mutual
 * exclusivity). Returns true if it handled the key -- callers (e.g.
 * handleGlobalKeydown) should stop further processing in that case.
 * @param {KeyboardEvent} e
 * @returns {boolean}
 */
function handleQuickDropdownKeydown(e) {
  var entry = null;
  for (var i = 0; i < _quickDropdowns.length; i++) {
    if (isQuickDropdownOpen(_quickDropdowns[i])) { entry = _quickDropdowns[i]; break; }
  }
  if (!entry) return false;
  var menu = $(entry.menuId);
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    var items = Array.from(menu.querySelectorAll('[role="menuitem"]'));
    if (items.length > 0) {
      var focusedEl = document.activeElement;
      var itemIdx = items.indexOf(focusedEl);
      if (e.key === 'ArrowDown') {
        itemIdx = (itemIdx + 1) % items.length;
      } else {
        itemIdx = (itemIdx - 1 + items.length) % items.length;
      }
      items[itemIdx].focus();
    }
    return true;
  }
  if (e.key === 'Enter') {
    var focused = document.activeElement;
    if (menu.contains(focused)) { focused.click(); return true; }
  }
  if (e.key === 'Escape') {
    closeQuickDropdown(entry);
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// View dropdown — render, open/close, view switching
// ---------------------------------------------------------------------------

/**
 * Populate #view-dropdown-menu with the full view list and update the label.
 * Called on open and after a view switch.
 */
function renderViewDropdown() {
  var menu = $('view-dropdown-menu');
  if (!menu) return;

  var views = (_serverSettings && _serverSettings.views) || [];
  var hiddenCount = visibleCount(_currentSessions, _serverSettings, "hidden");

  var html = '';

  // — All Sessions (always first) — show count of non-hidden sessions
  var allCount = visibleCount(_currentSessions, _serverSettings, "all");
  var allActive = _activeView === 'all' ? ' view-dropdown__item--active' : '';
  html += '<button class="view-dropdown__item' + allActive + '" role="menuitem" data-view="all">All Sessions <span class="view-dropdown__count">' + allCount + '</span></button>';

  // — User views
  if (views.length > 0) {
    html += '<div class="view-dropdown__separator"></div>';
    for (var i = 0; i < views.length && i < 7; i++) {
      var v = views[i];
      var vActive = _activeView === v.name ? ' view-dropdown__item--active' : '';
      html += '<button class="view-dropdown__item' + vActive + '" role="menuitem" data-view="' + escapeHtml(v.name) + '">' + escapeHtml(v.name) + ' <span class="view-dropdown__count">' + visibleCount(_currentSessions, _serverSettings, v.name) + '</span></button>';
    }
  }

  // — Hidden (N) (always last system view)
  html += '<div class="view-dropdown__separator"></div>';
  var hiddenActive = _activeView === 'hidden' ? ' view-dropdown__item--active' : '';
  html += '<button class="view-dropdown__item' + hiddenActive + '" role="menuitem" data-view="hidden">Hidden <span class="view-dropdown__count">' + hiddenCount + '</span></button>';

  // — Actions (stronger separator)
  html += '<div class="view-dropdown__separator view-dropdown__separator--strong"></div>';
  // Only show "Manage [ViewName]\u2026" when a user view is active
  if (_activeView !== 'all' && _activeView !== 'hidden') {
    var displayViewName = _activeView.length > 20 ? _activeView.substring(0, 20) + '\u2026' : _activeView;
    html += '<button class="view-dropdown__item view-dropdown__action" role="menuitem" data-action="manage-view">Manage \u201c' + escapeHtml(displayViewName) + '\u201d\u2026</button>';
  }
  html += '<button class="view-dropdown__item view-dropdown__action" role="menuitem" data-action="manage-views">Manage All Views\u2026</button>';
  html += '<button class="view-dropdown__item view-dropdown__action" role="menuitem" data-action="new-view">+ New View</button>';

  menu.innerHTML = html;

  // Update the label
  var label = $('view-dropdown-label');
  if (label) {
    if (_activeView === 'all') {
      label.textContent = 'All Sessions';
    } else if (_activeView === 'hidden') {
      label.textContent = 'Hidden';
    } else {
      label.textContent = _activeView;
    }
  }
}

/**
 * Quick-dropdown instance for the header view switcher -- see the "Quick
 * dropdown controller" section above for what this mechanism shares across
 * all four quick controls.
 */
var _viewDropdownQD = createQuickDropdown({
  triggerId: 'view-dropdown-trigger',
  menuId: 'view-dropdown-menu',
  render: renderViewDropdown,
  onClose: function(menu) {
    var newViewInput = menu.querySelector('.view-dropdown__new-input');
    if (newViewInput) newViewInput.remove();
  },
});

/**
 * Toggle the view dropdown open/closed.
 * Calls renderViewDropdown() when opening to ensure fresh content.
 */
function toggleViewDropdown() {
  toggleQuickDropdown(_viewDropdownQD);
}

/**
 * Close the view dropdown. Removes inline new-view input if present.
 */
function closeViewDropdown() {
  closeQuickDropdown(_viewDropdownQD);
}

/**
 * Render the sidebar view dropdown menu (same data as the header dropdown,
 * but no action buttons — navigation only).
 */
function renderSidebarViewDropdown() {
  var menu = $('sidebar-view-dropdown-menu');
  if (!menu) return;

  var views = (_serverSettings && _serverSettings.views) || [];
  var hiddenCount = visibleCount(_currentSessions, _serverSettings, "hidden");

  var html = '';

  // — All Sessions (always first) — show count of non-hidden sessions
  var sbAllCount = visibleCount(_currentSessions, _serverSettings, "all");
  var allActive = _activeView === 'all' ? ' view-dropdown__item--active' : '';
  html += '<button class="view-dropdown__item' + allActive + '" role="menuitem" data-view="all">All Sessions <span class="view-dropdown__count">' + sbAllCount + '</span></button>';

  // — User views
  if (views.length > 0) {
    html += '<div class="view-dropdown__separator"></div>';
    for (var i = 0; i < views.length && i < 7; i++) {
      var v = views[i];
      var vActive = _activeView === v.name ? ' view-dropdown__item--active' : '';
      html += '<button class="view-dropdown__item' + vActive + '" role="menuitem" data-view="' + escapeHtml(v.name) + '">' + escapeHtml(v.name) + ' <span class="view-dropdown__count">' + visibleCount(_currentSessions, _serverSettings, v.name) + '</span></button>';
    }
  }

  // — Hidden (N) (always last system view)
  html += '<div class="view-dropdown__separator"></div>';
  var hiddenActive = _activeView === 'hidden' ? ' view-dropdown__item--active' : '';
  html += '<button class="view-dropdown__item' + hiddenActive + '" role="menuitem" data-view="hidden">Hidden <span class="view-dropdown__count">' + hiddenCount + '</span></button>';

  // — Actions (stronger separator)
  html += '<div class="view-dropdown__separator view-dropdown__separator--strong"></div>';
  // Only show "Manage [ViewName]…" when a user view is active
  if (_activeView !== 'all' && _activeView !== 'hidden') {
    var sbDisplayViewName = _activeView.length > 20 ? _activeView.substring(0, 20) + '…' : _activeView;
    html += '<button class="view-dropdown__item view-dropdown__action" role="menuitem" data-action="manage-view">Manage “' + escapeHtml(sbDisplayViewName) + '”…</button>';
  }
  html += '<button class="view-dropdown__item view-dropdown__action" role="menuitem" data-action="manage-views">Manage All Views…</button>';
  html += '<button class="view-dropdown__item view-dropdown__action" role="menuitem" data-action="new-view">+ New View</button>';

  menu.innerHTML = html;
}

/**
 * Quick-dropdown instance for the sidebar view switcher. fixedPosition:true
 * positions the menu via getBoundingClientRect() (see createQuickDropdown's
 * docstring) to escape this sidebar's own overflow:hidden clipping.
 */
var _sidebarViewDropdownQD = createQuickDropdown({
  triggerId: 'sidebar-view-dropdown-trigger',
  menuId: 'sidebar-view-dropdown-menu',
  render: renderSidebarViewDropdown,
  fixedPosition: true,
});

/**
 * Toggle the sidebar view dropdown open/closed.
 * Calls renderSidebarViewDropdown() when opening to ensure fresh content.
 */
function toggleSidebarViewDropdown() {
  toggleQuickDropdown(_sidebarViewDropdownQD);
}

/**
 * Show an inline text input inside the view dropdown for creating a new view.
 * Replaces the '+ New View' button with a text input inside the dropdown menu.
 * - Removes any existing input and re-focuses it if already present.
 * - On Enter: validates name (not empty, not reserved, not duplicate),
 *   then PATCHes /api/settings with the new view appended to views,
 *   updates _serverSettings.views on success, and calls switchView(name).
 * - On Escape: closes the dropdown.
 * - On blur: closes the dropdown after 150ms if input is no longer focused.
 */
function showNewViewInput() {
  var menu = $('view-dropdown-menu');
  if (!menu) return;

  // Re-focus existing input instead of creating a duplicate
  var existing = menu.querySelector('.view-dropdown__new-input');
  if (existing) {
    existing.focus();
    return;
  }

  // Find the '+ New View' button to replace
  var newViewBtn = menu.querySelector('[data-action="new-view"]');
  if (!newViewBtn) return;

  // Create the inline text input
  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'view-dropdown__new-input';
  input.placeholder = 'View name';
  input.maxLength = 30;
  input.setAttribute('aria-label', 'New view name');
  _suppressAutofill(input);

  // Replace the '+ New View' button with the input
  newViewBtn.parentNode.replaceChild(input, newViewBtn);
  input.focus();

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      var name = input.value.trim();

      // Validate: not empty
      if (!name) return;

      // Validate: not reserved (case-insensitive)
      if (name.toLowerCase() === 'all' || name.toLowerCase() === 'hidden') {
        showToast('Cannot use reserved name \'' + name + '\'');
        return;
      }

      // Validate: not duplicate
      var views = (_serverSettings && _serverSettings.views) || [];
      if (views.find(function(v) { return v.name === name; })) {
        showToast('View \'' + name + '\' already exists');
        return;
      }

      // Create view and PATCH /api/settings
      var updatedViews = views.concat([{ name: name, sessions: [] }]);
      patchSettingsGuarded(function() { return { views: updatedViews }; })
        .then(function(body) {
          if (_serverSettings) _serverSettings.views = body.views;
          switchView(name);
          openManageViewPanel();
        })
        .catch(function() {
          showToast('Failed to create view');
        });
    } else if (e.key === 'Escape') {
      closeViewDropdown();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(function() {
      if (document.activeElement !== input) {
        closeViewDropdown();
      }
    }, 150);
  });
}

/**
 * Show an inline text input inside the SIDEBAR view dropdown for creating a new view.
 * Targets #sidebar-view-dropdown-menu instead of #view-dropdown-menu.
 * - On Enter: validates, PATCHes /api/settings, calls switchView + openManageViewPanel.
 * - On Escape / blur: closes the sidebar dropdown.
 */
function showSidebarNewViewInput() {
  var menu = $('sidebar-view-dropdown-menu');
  if (!menu) return;

  // Re-focus existing input instead of creating a duplicate
  var existing = menu.querySelector('.view-dropdown__new-input');
  if (existing) {
    existing.focus();
    return;
  }

  // Find the '+ New View' button to replace
  var newViewBtn = menu.querySelector('[data-action="new-view"]');
  if (!newViewBtn) return;

  // Create the inline text input
  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'view-dropdown__new-input';
  input.placeholder = 'View name';
  input.maxLength = 30;
  input.setAttribute('aria-label', 'New view name');
  _suppressAutofill(input);

  // Replace the '+ New View' button with the input
  newViewBtn.parentNode.replaceChild(input, newViewBtn);
  input.focus();

  function closeSidebarDropdown() {
    menu.classList.add('hidden');
    var trigger = $('sidebar-view-dropdown-trigger');
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
  }

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      var name = input.value.trim();

      // Validate: not empty
      if (!name) return;

      // Validate: not reserved (case-insensitive)
      if (name.toLowerCase() === 'all' || name.toLowerCase() === 'hidden') {
        showToast('Cannot use reserved name \'' + name + '\'');
        return;
      }

      // Validate: not duplicate
      var views = (_serverSettings && _serverSettings.views) || [];
      if (views.find(function(v) { return v.name === name; })) {
        showToast('View \'' + name + '\' already exists');
        return;
      }

      // Create view and PATCH /api/settings
      var updatedViews = views.concat([{ name: name, sessions: [] }]);
      patchSettingsGuarded(function() { return { views: updatedViews }; })
        .then(function(body) {
          if (_serverSettings) _serverSettings.views = body.views;
          closeSidebarDropdown();
          switchView(name);
          openManageViewPanel();
        })
        .catch(function() {
          showToast('Failed to create view');
        });
    } else if (e.key === 'Escape') {
      closeSidebarDropdown();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(function() {
      if (document.activeElement !== input) {
        closeSidebarDropdown();
      }
    }, 150);
  });
}

// ---------------------------------------------------------------------------
// Quick sort dropdown — header + sidebar
// ---------------------------------------------------------------------------
// v0.47.9: converted from native <select> elements to the same button+menu
// mechanism as the view dropdown above (see the "Quick dropdown controller"
// section's comment for why). #sort-order-select and
// #sidebar-sort-order-select are now <button> triggers (same ids, same
// quick-sort-select/quick-link classes -- see style.css) rather than
// <select>s; #sort-order-menu / #sidebar-sort-order-menu are their
// role="menu" popups. The Settings > Sessions sort select
// (#setting-sort-order) is UNCHANGED and stays a native <select> -- it lives
// in a settings form, not a quick control, and was never part of the
// owner's "these four should be the same component" ask.

/** The four sort_order values, in display order -- shared by both menus. */
var SORT_OPTIONS = [
  { value: 'manual', label: 'Manual' },
  { value: 'alphabetical', label: 'Alphabetical' },
  { value: 'recent', label: 'Recent' },
  { value: 'attention', label: 'Attention' },
];

/** Display label for a sort_order value; falls back to 'Manual' for an unknown/missing value. */
function _sortOptionLabel(value) {
  for (var i = 0; i < SORT_OPTIONS.length; i++) {
    if (SORT_OPTIONS[i].value === value) return SORT_OPTIONS[i].label;
  }
  return 'Manual';
}

/**
 * Build the shared sort-menu content (identical for header + sidebar).
 * Reuses .view-dropdown__item / .view-dropdown__item--active -- the sort
 * menu's four static options need no separators, counts, or actions, so it
 * stays its own (simpler) render function rather than sharing
 * renderViewDropdown()'s content-building code, which does need all three.
 * @param {string} activeValue - current sort_order value
 * @returns {string}
 */
function _buildSortMenuHTML(activeValue) {
  var html = '';
  for (var i = 0; i < SORT_OPTIONS.length; i++) {
    var opt = SORT_OPTIONS[i];
    var active = activeValue === opt.value ? ' view-dropdown__item--active' : '';
    html += '<button class="view-dropdown__item' + active + '" role="menuitem" data-sort="' + opt.value + '">' + opt.label + '</button>';
  }
  return html;
}

/** Populate #sort-order-menu with the four sort options. Called on open. */
function renderSortDropdown() {
  var menu = $('sort-order-menu');
  if (!menu) return;
  var value = (_serverSettings && _serverSettings.sort_order) || 'manual';
  menu.innerHTML = _buildSortMenuHTML(value);
}

/** Populate #sidebar-sort-order-menu with the four sort options. Called on open. */
function renderSidebarSortDropdown() {
  var menu = $('sidebar-sort-order-menu');
  if (!menu) return;
  var value = (_serverSettings && _serverSettings.sort_order) || 'manual';
  menu.innerHTML = _buildSortMenuHTML(value);
}

/** Quick-dropdown instance for the header sort control. */
var _sortDropdownQD = createQuickDropdown({
  triggerId: 'sort-order-select',
  menuId: 'sort-order-menu',
  render: renderSortDropdown,
});

function toggleSortDropdown() { toggleQuickDropdown(_sortDropdownQD); }
function closeSortDropdown() { closeQuickDropdown(_sortDropdownQD); }

/**
 * Quick-dropdown instance for the sidebar sort control. fixedPosition:true,
 * same as _sidebarViewDropdownQD -- escapes the sidebar's own overflow:hidden.
 */
var _sidebarSortDropdownQD = createQuickDropdown({
  triggerId: 'sidebar-sort-order-select',
  menuId: 'sidebar-sort-order-menu',
  render: renderSidebarSortDropdown,
  fixedPosition: true,
});

function toggleSidebarSortDropdown() { toggleQuickDropdown(_sidebarSortDropdownQD); }
function closeSidebarSortDropdown() { closeQuickDropdown(_sidebarSortDropdownQD); }

/**
 * Apply a new sort_order value (selected from either quick dropdown's menu):
 * update state, re-sync every sort control (both quick dropdowns + the
 * Settings select), re-render the grid/sidebar, and persist. This is the
 * delegate for onSortOrderChange() (the Settings select's own change
 * handler) as well as the two quick dropdowns' menu-item clicks -- one
 * place, one behavior, regardless of which of the three sort surfaces the
 * user acted on.
 * @param {string} value
 */
function selectSortOrder(value) {
  if (!value) return;
  if (_serverSettings) _serverSettings.sort_order = value;
  syncSortOrderControls();
  renderGrid(_currentSessions || []);
  renderSidebar(_currentSessions || [], _viewingSession, _viewingRemoteId);
  patchServerSetting('sort_order', value);
}

/**
 * Save updated views array via PATCH /api/settings, update _serverSettings,
 * re-render the views settings tab, and re-render the view dropdown.
 * @param {Array} updatedViews - New views array to save.
 */
function _saveViewsAndRerender(updatedViews) {
  return patchSettingsGuarded(function() { return { views: updatedViews }; })
    .then(function(body) {
      if (_serverSettings) _serverSettings.views = body.views;
      renderViewsSettingsTab();
      renderViewDropdown();
    })
    .catch(function() {
      showToast('Failed to save views');
    });
}

/**
 * Render the Views settings tab content.
 * Reads views from _serverSettings and builds an interactive list
 * with inline rename, up/down reorder, and delete with confirmation.
 */
function renderViewsSettingsTab() {
  var listEl = $('views-settings-list');
  var emptyEl = $('views-settings-empty');
  if (!listEl) return;

  var views = (_serverSettings && _serverSettings.views) || [];

  if (views.length === 0) {
    listEl.innerHTML = '';
    if (emptyEl) emptyEl.style.display = '';
    return;
  }

  if (emptyEl) emptyEl.style.display = 'none';

  // Build the list of view rows (no inline rename — rename is in Manage View panel)
  listEl.innerHTML = '';
  views.forEach(function(view, idx) {
    var sessionCount = visibleCount(_currentSessions, _serverSettings, view.name);
    var inclHidden = visibleCount(_currentSessions, _serverSettings, view.name, { includeHidden: true });
    var hiddenInViewCount = inclHidden - sessionCount;

    var row = document.createElement('div');
    row.className = 'views-settings-row';
    row.setAttribute('data-view-idx', String(idx));

    // Name span (not clickable for rename — rename is in Manage View panel)
    var nameSpan = document.createElement('span');
    nameSpan.className = 'views-settings-name';
    nameSpan.textContent = view.name;

    // Session count — show "(M hidden)" suffix when M > 0
    var countSpan = document.createElement('span');
    countSpan.className = 'views-settings-count';
    countSpan.textContent = sessionCount + (sessionCount === 1 ? ' session' : ' sessions') + (hiddenInViewCount > 0 ? ' (' + hiddenInViewCount + ' hidden)' : '');

    // Actions container
    var actionsDiv = document.createElement('div');
    actionsDiv.className = 'views-settings-actions';

    // Up button
    var upBtn = document.createElement('button');
    upBtn.className = 'views-settings-btn';
    upBtn.textContent = '\u25b2';
    upBtn.title = 'Move up';
    upBtn.setAttribute('data-action', 'move-up');
    upBtn.setAttribute('data-idx', String(idx));
    if (idx === 0) upBtn.disabled = true;

    // Down button
    var downBtn = document.createElement('button');
    downBtn.className = 'views-settings-btn';
    downBtn.textContent = '\u25bc';
    downBtn.title = 'Move down';
    downBtn.setAttribute('data-action', 'move-down');
    downBtn.setAttribute('data-idx', String(idx));
    if (idx === views.length - 1) downBtn.disabled = true;

    // Manage button (opens Manage View panel — close settings first)
    var manageBtn = document.createElement('button');
    manageBtn.className = 'views-settings-btn views-settings-btn--manage';
    manageBtn.textContent = 'Manage';
    manageBtn.setAttribute('data-action', 'manage');
    manageBtn.setAttribute('data-idx', String(idx));

    // Delete button
    var deleteBtn = document.createElement('button');
    deleteBtn.className = 'views-settings-btn views-settings-btn--danger';
    deleteBtn.textContent = 'Delete';
    deleteBtn.setAttribute('data-action', 'delete');
    deleteBtn.setAttribute('data-idx', String(idx));

    actionsDiv.appendChild(upBtn);
    actionsDiv.appendChild(downBtn);
    actionsDiv.appendChild(manageBtn);
    actionsDiv.appendChild(deleteBtn);

    row.appendChild(nameSpan);
    row.appendChild(countSpan);
    row.appendChild(actionsDiv);
    listEl.appendChild(row);
  });

  // Add "+ New View" button at the bottom
  var newViewRow = document.createElement('div');
  newViewRow.className = 'views-settings-new-row';
  var newViewBtn = document.createElement('button');
  newViewBtn.className = 'views-settings-btn views-settings-btn--new';
  newViewBtn.textContent = '+ New View';
  newViewBtn.setAttribute('data-action', 'new-view-in-settings');
  newViewRow.appendChild(newViewBtn);
  listEl.appendChild(newViewRow);

  // Delegated click handler on the list
  listEl.onclick = function(e) {
    var views = (_serverSettings && _serverSettings.views) || [];
    var target = e.target;

    // Move up
    if (target.getAttribute('data-action') === 'move-up') {
      var idx = parseInt(target.getAttribute('data-idx'), 10);
      if (idx > 0) {
        var updated = views.slice();
        var tmp = updated[idx - 1];
        updated[idx - 1] = updated[idx];
        updated[idx] = tmp;
        _saveViewsAndRerender(updated);
      }
      return;
    }

    // Move down
    if (target.getAttribute('data-action') === 'move-down') {
      var idx = parseInt(target.getAttribute('data-idx'), 10);
      if (idx < views.length - 1) {
        var updated = views.slice();
        var tmp = updated[idx + 1];
        updated[idx + 1] = updated[idx];
        updated[idx] = tmp;
        _saveViewsAndRerender(updated);
      }
      return;
    }

    // Manage: close settings, switch to that view, open Manage View panel
    if (target.getAttribute('data-action') === 'manage') {
      var idx = parseInt(target.getAttribute('data-idx'), 10);
      var viewName = views[idx] && views[idx].name;
      if (!viewName) return;
      closeSettings();
      switchView(viewName);
      openManageViewPanel();
      return;
    }

    // Delete: show inline confirm
    if (target.getAttribute('data-action') === 'delete') {
      var idx = parseInt(target.getAttribute('data-idx'), 10);
      var row = listEl.querySelector('[data-view-idx="' + idx + '"]');
      if (!row) return;

      // Replace delete button with "Sure? [Yes] [No]"
      var actionsDiv = row.querySelector('.views-settings-actions');
      if (!actionsDiv) return;
      actionsDiv.innerHTML = '';

      var confirmSpan = document.createElement('span');
      confirmSpan.className = 'views-settings-confirm';
      confirmSpan.textContent = 'Sure? ';

      var yesBtn = document.createElement('button');
      yesBtn.className = 'views-settings-btn views-settings-btn--danger';
      yesBtn.textContent = 'Yes';
      yesBtn.setAttribute('data-action', 'confirm-delete');
      yesBtn.setAttribute('data-idx', String(idx));

      var noBtn = document.createElement('button');
      noBtn.className = 'views-settings-btn';
      noBtn.textContent = 'No';
      noBtn.setAttribute('data-action', 'cancel-delete');

      confirmSpan.appendChild(yesBtn);
      confirmSpan.appendChild(document.createTextNode(' '));
      confirmSpan.appendChild(noBtn);
      actionsDiv.appendChild(confirmSpan);
      return;
    }

    // Confirm delete
    if (target.getAttribute('data-action') === 'confirm-delete') {
      var idx = parseInt(target.getAttribute('data-idx'), 10);
      var updated = views.slice();
      updated.splice(idx, 1);
      // If deleting the active view, fall back to 'all'
      if (_activeView === views[idx].name) {
        _activeView = 'all';
        persistActiveView(_activeView);
      }
      _saveViewsAndRerender(updated);
      return;
    }

    // Cancel delete: re-render
    if (target.getAttribute('data-action') === 'cancel-delete') {
      renderViewsSettingsTab();
      return;
    }

    // + New View: create a new view and open Manage View panel
    if (target.getAttribute('data-action') === 'new-view-in-settings') {
      var newName = prompt('View name:');
      if (!newName || !newName.trim()) return;
      newName = newName.trim();
      if (newName.toLowerCase() === 'all' || newName.toLowerCase() === 'hidden') {
        showToast('Cannot use reserved name \'' + newName + '\'');
        return;
      }
      if (views.find(function(v) { return v.name === newName; })) {
        showToast('View \'' + newName + '\' already exists');
        return;
      }
      var updatedViews = views.concat([{ name: newName, sessions: [] }]);
      patchSettingsGuarded(function() { return { views: updatedViews }; })
        .then(function(body) {
          if (_serverSettings) _serverSettings.views = body.views;
          renderViewsSettingsTab();
          renderViewDropdown();
          // Close settings and open Manage View panel for the new view
          closeSettings();
          switchView(newName);
          openManageViewPanel();
        })
        .catch(function() {
          showToast('Failed to create view');
        });
      return;
    }
  };
}

/**
 * Apply a view change locally: update _activeView, re-render the grid and
 * sidebar, and update the dropdown/sidebar labels. Does NOT touch the server.
 * Shared by switchView() (user-initiated: PATCHes afterwards) and
 * followRemoteActiveView() (server-initiated: must NOT PATCH back).
 * @param {string} viewName - 'all', 'hidden', or a user view name.
 */
function applyViewLocally(viewName) {
  _activeView = viewName;
  renderGrid(_currentSessions || []);
  renderSidebar(_currentSessions || [], _viewingSession, _viewingRemoteId);
  renderViewDropdown();
  // Update sidebar view label to match the active view
  var sidebarLabel = $('sidebar-view-label');
  if (sidebarLabel) {
    if (viewName === 'all') {
      sidebarLabel.textContent = 'All Sessions';
    } else if (viewName === 'hidden') {
      sidebarLabel.textContent = 'Hidden';
    } else {
      sidebarLabel.textContent = viewName;
    }
  }
}

/**
 * Switch to a named view (user-initiated). Applies the change locally via
 * applyViewLocally() and persists it via PATCH /api/state — active_view is
 * server-global, so this propagates to every other device (deck, other tabs).
 * @param {string} viewName - 'all', 'hidden', or a user view name.
 */
function switchView(viewName) {
  closeViewDropdown();
  applyViewLocally(viewName);
  // Persist active view via the shared, race-guarded helper -- see
  // persistActiveView()'s docstring.
  persistActiveView(viewName);
}

function renderGrid(sessions) {
  var grid = $('session-grid');
  var emptyState = $('empty-state');
  var filterBar = $('filter-bar');

  // Close flyout if the targeted session no longer exists
  if (_flyoutSessionKey) {
    var flyoutStillExists = (sessions || []).some(function(s) {
      return (s.sessionKey || s.name) === _flyoutSessionKey;
    });
    if (!flyoutStillExists) {
      closeFlyoutMenu();
    }
  }

  var visible = getVisibleSessions(sessions);

  if (visible.length === 0) {
    // Build status tiles for auth_failed/unreachable sessions even when no regular sessions exist.
    // status:empty sentinels are intentionally ignored — a remote with zero tmux sessions
    // produces no visible tile in any view mode (flat, grouped, or otherwise).
    var statusTilesHtml = '';
    (sessions || []).forEach(function(session) {
      if (session.status === 'auth_failed') statusTilesHtml += buildStatusTileHTML(session.deviceName, 'Auth required', 'auth', session.deviceVersion);
      else if (session.status === 'unreachable') statusTilesHtml += buildStatusTileHTML(session.deviceName, 'Offline', 'offline', session.deviceVersion);
    });
    if (grid) grid.innerHTML = statusTilesHtml;
    // Only show empty-state when there are truly no tiles at all
    if (emptyState) {
      if (statusTilesHtml) emptyState.classList.add('hidden');
      else emptyState.classList.remove('hidden');
    }
    if (filterBar) filterBar.innerHTML = '';
    return;
  }

  if (emptyState) emptyState.classList.add('hidden');

  // Apply sort order from server settings. Shared with renderSidebar() via
  // applySortOrder() so the two surfaces can never disagree about ordering.
  // 'attention' sort has no currently-open-session tier (see
  // sortByAttention()'s docstring), so there is nothing session-specific to
  // forward here.
  var sortOrder = _serverSettings && _serverSettings.sort_order;
  var mobile = isMobile();
  var ordered = applySortOrder(visible, sortOrder, mobile);

  var html;
  if (_gridViewMode === 'grouped') {
    html = renderGroupedGrid(ordered, mobile);
  } else {
    html = ordered.map(function(session, index) { return buildTileHTML(session, index, mobile); }).join('');
  }

  // Append status tiles for auth_failed and unreachable sessions.  status:empty sentinels are
  // intentionally ignored in all view modes — a remote with zero tmux sessions produces no
  // visible tile.  auth_failed and unreachable are actionable error states and are always shown.
  var statusTilesHtml = '';
  (sessions || []).forEach(function(session) {
    if (session.status === 'auth_failed') statusTilesHtml += buildStatusTileHTML(session.deviceName, 'Auth required', 'auth', session.deviceVersion);
    else if (session.status === 'unreachable') statusTilesHtml += buildStatusTileHTML(session.deviceName, 'Offline', 'offline', session.deviceVersion);
  });
  if (grid) grid.innerHTML = html + statusTilesHtml;

  // Clear filter bar (filtered mode removed; bar is a no-op for flat/grouped)
  if (filterBar) filterBar.innerHTML = '';

  // Bind interaction handlers on each tile
  document.querySelectorAll('.session-tile').forEach(function(tile) {
    on(tile, 'click', (e) => {
      // Don't navigate when clicking the options button inside the tile
      if (e.target.closest && e.target.closest('.tile-options-btn')) return;
      // Don't open error/status tiles (unreachable, auth_failed)
      if (tile.classList.contains('source-tile--error') || !tile.dataset.session) return;
      openSession(tile.dataset.session, { remoteId: tile.dataset.remoteId || '' });
    });
    on(tile, 'keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        // Don't open error/status tiles (unreachable, auth_failed)
        if (tile.classList.contains('source-tile--error') || !tile.dataset.session) return;
        openSession(tile.dataset.session, { remoteId: tile.dataset.remoteId || '' });
      }
    });
  });

  if (_viewMode === 'fullscreen') {
    updatePillBell();
  }

  // Reapply view mode layout after grid HTML is rebuilt
  var currentDs = getDisplaySettings();
  var currentMode = currentDs.viewMode || 'auto';
  if (currentMode === 'fit' && grid) {
    grid.classList.add('session-grid--fit');
    applyFitLayout(grid);
  }

}

// ---------------------------------------------------------------------------
// Hover preview popover (desktop only — no hover on touch devices)
// ---------------------------------------------------------------------------

// Click handler registered while preview is showing — navigates to the previewed session
function _previewClickHandler(e) {
  e.preventDefault();
  e.stopPropagation();
  var name = _previewSessionName;
  hidePreview();
  if (name) {
    var session = _currentSessions && _currentSessions.find(function(s) { return s.name === name; });
    openSession(name, { remoteId: (session != null && session.remoteId != null) ? session.remoteId : '' });
  }
}

function showPreview(name) {
  if (!name || !_currentSessions) return;
  // Off is expressed entirely by hoverPreviewDelay === 0 (see DISPLAY_DEFAULTS
  // and the mouseenter handlers in bindStaticEventListeners, which only ever
  // arm the timer that calls showPreview() when delay > 0). The second,
  // independent popover-disable checkbox this function used to also check
  // here was retired in v0.47.0 in favor of folding "off" into this one
  // control -- there is nothing else to gate on.
  var session = _currentSessions.find(function (s) { return s.name === name; });
  if (!session || !session.snapshot) return;

  // If already showing this session, just update content
  if (_previewPopover && _previewSessionName === name) {
    var pre = _previewPopover.querySelector('pre');
    if (pre) pre.innerHTML = ansiToHtml(session.snapshot);
    return;
  }

  hidePreviewDOM();
  _previewSessionName = name;

  // Full-window overlay
  var popover = document.createElement('div');
  popover.className = 'preview-popover';
  var pre = document.createElement('pre');
  pre.innerHTML = ansiToHtml(session.snapshot);
  popover.appendChild(pre);
  document.body.appendChild(popover);
  _previewPopover = popover;

  // Auto-scroll to bottom (prompt area)
  popover.scrollTop = popover.scrollHeight;

  // Click anywhere navigates to previewed session
  document.addEventListener('click', _previewClickHandler, true);
}

// hidePreviewDOM: removes the visual elements only (no render trigger)
function hidePreviewDOM() {
  document.removeEventListener('click', _previewClickHandler, true);
  if (_previewPopover) {
    _previewPopover.remove();
    _previewPopover = null;
  }
}

// hidePreview: full cleanup including timer and session name
function hidePreview() {
  if (_previewTimer) {
    clearTimeout(_previewTimer);
    _previewTimer = null;
  }
  hidePreviewDOM();
  _previewSessionName = null;
}

// ── Tile Flyout Menu ──────────────────────────────────────────────────────────

/**
 * Open the flyout menu for a session tile's ⋮ button.
 * Creates a floating menu appended to document.body, positioned relative to
 * the trigger button via getBoundingClientRect. On mobile, renders as a
 * bottom action sheet instead.
 * @param {HTMLElement} triggerEl - The .tile-options-btn element that was clicked
 */
function openFlyoutMenu(triggerEl) {
  closeFlyoutMenu();

  // Read session info from the tile
  var tile = triggerEl.closest('[data-session-key]');
  if (!tile) return;
  _flyoutSessionKey = tile.dataset.sessionKey || '';
  _flyoutSessionName = tile.dataset.session || '';
  _flyoutRemoteId = tile.dataset.remoteId || '';

  if (isMobile()) {
    _openFlyoutSheet();
    return;
  }

  // Build menu items based on active view type
  var menuHtml = _buildFlyoutMenuItems();

  var menu = document.createElement('div');
  menu.className = 'flyout-menu';
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', 'Session options');
  menu.innerHTML = menuHtml;
  document.body.appendChild(menu);
  _flyoutMenuEl = menu;

  // Position relative to trigger
  var rect = triggerEl.getBoundingClientRect();
  var menuWidth = menu.offsetWidth;
  var menuHeight = menu.offsetHeight;

  // Default: below and to the left of the trigger
  var top = rect.bottom + 4;
  var left = rect.right - menuWidth;

  // Keep within viewport
  if (left < 8) left = 8;
  if (top + menuHeight > window.innerHeight - 8) {
    top = rect.top - menuHeight - 4;
  }
  if (top < 8) top = 8;

  menu.style.top = top + 'px';
  menu.style.left = left + 'px';

  // Delegated click handler on the flyout
  menu.addEventListener('click', _handleFlyoutClick);

  // Close on click-outside (next tick to avoid the opening click)
  setTimeout(function() {
    document.addEventListener('click', _flyoutOutsideClickHandler, true);
  }, 0);
}

/**
 * Close the flyout menu and any open submenu.
 */
function closeFlyoutMenu() {
  if (_flyoutSubmenuEl) {
    _flyoutSubmenuEl.remove();
    _flyoutSubmenuEl = null;
  }
  if (_flyoutMenuEl) {
    _flyoutMenuEl.removeEventListener('click', _handleFlyoutClick);
    _flyoutMenuEl.remove();
    _flyoutMenuEl = null;
  }
  // Remove mobile sheet if open
  var sheet = document.querySelector('.flyout-sheet');
  if (sheet) sheet.remove();

  document.removeEventListener('click', _flyoutOutsideClickHandler, true);
  _flyoutSessionKey = null;
  _flyoutSessionName = null;
  _flyoutRemoteId = null;
}

/**
 * Open a bottom action sheet for the flyout menu (mobile).
 * Same actions as the desktop flyout, but renders as a full-width bottom sheet.
 */
function _openFlyoutSheet() {
  var viewType = _activeView;
  if (viewType !== 'all' && viewType !== 'hidden') viewType = 'user';

  var items = FLYOUT_MENU_MAP[viewType] || FLYOUT_MENU_MAP['all'];

  var html = '<div class="flyout-sheet__backdrop"></div>';
  html += '<div class="flyout-sheet__panel" aria-label="Session options" role="menu">';
  html += '<div class="flyout-sheet__handle" aria-hidden="true"></div>';

  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (item.separator) {
      html += '<div class="flyout-sheet__separator"></div>';
      continue;
    }

    var label = item.label;
    if (label.indexOf('{viewName}') !== -1) {
      var displayName = _activeView;
      if (displayName.length > 20) displayName = displayName.substring(0, 20) + '\u2026';
      label = label.replace('{viewName}', escapeHtml(displayName));
    }

    var cls = 'flyout-sheet__item';
    if (item.className && item.className.indexOf('danger') !== -1) cls += ' flyout-sheet__item--danger';

    html += '<button class="' + cls + '" role="menuitem" data-action="' + item.action + '">';
    html += label;
    html += '</button>';
  }

  html += '</div>';

  var sheet = document.createElement('div');
  sheet.className = 'flyout-sheet';
  sheet.setAttribute('role', 'dialog');
  sheet.setAttribute('aria-modal', 'true');
  sheet.innerHTML = html;
  document.body.appendChild(sheet);

  // Backdrop closes
  var backdrop = sheet.querySelector('.flyout-sheet__backdrop');
  if (backdrop) {
    backdrop.addEventListener('click', closeFlyoutMenu);
  }

  // Delegated action handler
  var panel = sheet.querySelector('.flyout-sheet__panel');
  if (panel) {
    panel.addEventListener('click', function(e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;

      var action = btn.dataset.action;
      if (action === 'add-to-view' || action === 'unhide-add-to-view') {
        // On mobile, show a view picker sheet (not the Add Sessions panel which is for the active view)
        var sessionKey = _flyoutSessionKey;
        var sessionName = _flyoutSessionName;
        var unhideFirst = action === 'unhide-add-to-view';
        closeFlyoutMenu();
        _openMobileViewPicker(sessionKey, sessionName, unhideFirst);
      } else if (action === 'kill') {
        // Show a confirmation sheet — consistent with the existing sheet pattern
        var killName = _flyoutSessionName;
        var killRemoteId = _flyoutRemoteId;
        closeFlyoutMenu();
        _openMobileKillConfirm(killName, killRemoteId);
      } else {
        // Dispatch directly
        _handleFlyoutClick(e);
      }
    });
  }
}

/**
 * Show a confirmation bottom sheet before killing a session on mobile.
 * Shows "Kill [sessionName]?" with Kill and Cancel buttons.
 * @param {string} sessionName
 * @param {string} remoteId
 */
function _openMobileKillConfirm(sessionName, remoteId) {
  var sheet = document.createElement('div');
  sheet.className = 'flyout-sheet';

  var html = '<div class="flyout-sheet__backdrop"></div>';
  html += '<div class="flyout-sheet__panel" aria-label="Confirm kill session" role="alertdialog">';
  html += '<div class="flyout-sheet__handle" aria-hidden="true"></div>';
  html += '<div class="flyout-sheet__title">Kill ' + escapeHtml(sessionName) + '?</div>';
  html += '<button class="flyout-sheet__item flyout-sheet__item--danger" data-action="confirm-kill" role="button">Kill</button>';
  html += '<button class="flyout-sheet__item" data-action="cancel" role="button">Cancel</button>';
  html += '</div>';

  sheet.innerHTML = html;
  document.body.appendChild(sheet);

  var backdrop = sheet.querySelector('.flyout-sheet__backdrop');
  if (backdrop) backdrop.addEventListener('click', function() { sheet.remove(); });

  var panel = sheet.querySelector('.flyout-sheet__panel');
  if (panel) {
    panel.addEventListener('click', function(e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      sheet.remove();
      if (btn.dataset.action === 'confirm-kill') {
        killSession(sessionName, remoteId);
      }
    });
  }
}

/**
 * Open a bottom sheet listing all user views for the session (mobile view picker).
 * Same toggle behaviour as the desktop submenu — each tap fires a PATCH immediately.
 * The sheet has a "Done" button that closes it.
 * @param {string} sessionKey
 * @param {string} sessionName
 * @param {boolean} unhideFirst - If true, also unhide the session on first add
 */
function _openMobileViewPicker(sessionKey, sessionName, unhideFirst) {
  var views = (_serverSettings && _serverSettings.views) || [];
  if (views.length === 0) {
    showToast('No user views. Create one from the header dropdown.');
    return;
  }

  // Same provenance distinction as the desktop flyout submenu (§9.3):
  // pinned members can be toggled off; rule-matched members cannot (they
  // aren't pinned, so the toggle would be a silent no-op) and are shown
  // disabled with a label instead.
  var mobilePickerSession = (_currentSessions || []).find(function(s) {
    return (s.sessionKey || s.name) === sessionKey;
  });
  var mobileSessionViews = (mobilePickerSession && mobilePickerSession.views) || [];

  var sheet = document.createElement('div');
  sheet.className = 'flyout-sheet';

  var html = '<div class="flyout-sheet__backdrop"></div>';
  html += '<div class="flyout-sheet__panel" aria-label="Add to View" role="menu">';
  html += '<div class="flyout-sheet__handle" aria-hidden="true"></div>';

  for (var i = 0; i < views.length; i++) {
    var v = views[i];
    var isPinned = (v.sessions || []).indexOf(sessionKey) !== -1;
    var isMatched = !isPinned && mobileSessionViews.indexOf(v.name) !== -1;
    var isIn = isPinned || isMatched;
    html += '<button class="flyout-sheet__item' + (isMatched ? ' flyout-sheet__item--matched' : '') + '"' +
      (isMatched ? ' disabled title="Matched by rule -- not pinned, so it can\u2019t be removed here"' : '') +
      ' role="menuitem" data-view-index="' + i + '">';
    html += '<span style="margin-right:8px">' + (isIn ? '\u2713' : '\u00a0\u00a0') + '</span>';
    html += escapeHtml(v.name);
    if (isMatched) html += ' <span class="flyout-sheet__matched-label">(rule)</span>';
    html += '</button>';
  }

  html += '<div class="flyout-sheet__separator"></div>';
  html += '<button class="flyout-sheet__item" data-action="done" role="menuitem">Done</button>';
  html += '</div>';

  sheet.innerHTML = html;
  document.body.appendChild(sheet);

  var backdrop = sheet.querySelector('.flyout-sheet__backdrop');
  if (backdrop) backdrop.addEventListener('click', function() { sheet.remove(); });

  var panel = sheet.querySelector('.flyout-sheet__panel');
  if (panel) {
    panel.addEventListener('click', function(e) {
      var btn = e.target.closest('[data-action="done"]');
      if (btn) { sheet.remove(); return; }

      var viewBtn = e.target.closest('[data-view-index]');
      if (!viewBtn) return;

      var idx = parseInt(viewBtn.dataset.viewIndex, 10);
      var views = (_serverSettings && _serverSettings.views) || [];
      var view = views[idx];
      if (!view) return;

      var sessions = view.sessions || [];
      var isAlreadyInView = sessions.indexOf(sessionKey) !== -1;

      var patch;
      var nowIn;
      if (isAlreadyInView) {
        patch = removeSessionFromViewOp(_serverSettings, view.name, sessionKey);
        nowIn = false;
      } else {
        patch = addSessionToViewOp(_serverSettings, view.name, sessionKey);
        nowIn = true;
      }

      // Update checkmark immediately for responsiveness
      var checkEl = viewBtn.querySelector('span');
      if (checkEl) checkEl.textContent = nowIn ? '\u2713' : '\u00a0\u00a0';

      patchSettingsGuarded(function(fresh) {
        return isAlreadyInView
          ? removeSessionFromViewOp(fresh, view.name, sessionKey)
          : addSessionToViewOp(fresh, view.name, sessionKey);
      })
        .then(function(body) {
          if (_serverSettings) {
            _serverSettings.views = body.views;
            if (body.hidden_sessions) _serverSettings.hidden_sessions = body.hidden_sessions;
          }
          if (nowIn && patch.hidden_sessions) renderGrid(_currentSessions || []);
        })
        .catch(function(err) {
          showToast('Couldn\u2019t save \u2014 try again');
          // Revert checkmark
          if (checkEl) checkEl.textContent = nowIn ? '\u00a0\u00a0' : '\u2713';
          console.warn('[_openMobileViewPicker] PATCH failed:', err);
        });
    });
  }
}

/**
 * Click-outside handler for the flyout menu.
 * @param {MouseEvent} e
 */
function _flyoutOutsideClickHandler(e) {
  if (_flyoutMenuEl && !_flyoutMenuEl.contains(e.target) &&
      (!_flyoutSubmenuEl || !_flyoutSubmenuEl.contains(e.target))) {
    closeFlyoutMenu();
  }
}

/**
 * Delegated click handler for the flyout menu.
 * Dispatches based on data-action attribute.
 * @param {MouseEvent} e
 */
function _handleFlyoutClick(e) {
  var item = e.target.closest('[data-action]');
  if (!item) return;

  var action = item.dataset.action;

  switch (action) {
    case 'add-to-view':
    case 'unhide-add-to-view':
      _openFlyoutSubmenu(item, action === 'unhide-add-to-view');
      break;
    case 'remove-from-view':
      _doRemoveFromView();
      break;
    case 'hide':
      _doHideSession();
      break;
    case 'unhide':
      _doUnhideSession();
      break;
    case 'kill':
      _doKillSessionInline(item);
      break;
    default:
      break;
  }
}

/**
 * Open the "Add to View" submenu next to a flyout menu item.
 * Lists all user-created views with checkmarks for views the session is already in.
 * Clicking a view toggles membership immediately via PATCH /api/settings.
 * The flyout stays open after submenu actions.
 * @param {HTMLElement} triggerItem - The menu item that triggered the submenu
 * @param {boolean} unhideFirst - If true, also unhide the session (for "Unhide & Add to View")
 */
function _openFlyoutSubmenu(triggerItem, unhideFirst) {
  // Close existing submenu
  if (_flyoutSubmenuEl) {
    _flyoutSubmenuEl.remove();
    _flyoutSubmenuEl = null;
  }

  var views = (_serverSettings && _serverSettings.views) || [];

  var sessionKey = _flyoutSessionKey;
  // Session's server-resolved view membership (pins union glob-rule
  // matches -- docs/plans/2026-08-04-auto-views-plan.md §9.3). Used to distinguish "pinned"
  // (togglable) from "matched by rule" (member, but the toggle would
  // silently do nothing since it's not pinned -- so it's disabled and
  // labeled instead of offered as a no-op).
  var flyoutSession = (_currentSessions || []).find(function(s) {
    return (s.sessionKey || s.name) === sessionKey;
  });
  var sessionViews = (flyoutSession && flyoutSession.views) || [];
  // Show ALL user views with checkmarks — unified Views submenu
  var html = '';
  for (var i = 0; i < views.length; i++) {
    var v = views[i];
    var isPinned = (v.sessions || []).indexOf(sessionKey) !== -1;
    var isMatched = !isPinned && sessionViews.indexOf(v.name) !== -1;
    var isIn = isPinned || isMatched;
    html += '<button class="flyout-submenu__item' + (isMatched ? ' flyout-submenu__item--matched' : '') + '"' +
      (isMatched ? ' disabled title="Matched by rule -- not pinned, so it can\u2019t be removed here"' : '') +
      ' role="menuitem" data-view-index="' + i + '">';
    html += '<span class="flyout-submenu__check">' + (isIn ? '\u2713' : '') + '</span>';
    html += escapeHtml(v.name);
    if (isMatched) html += '<span class="flyout-submenu__matched-label"> (rule)</span>';
    html += '</button>';
  }
  // — Always show "+ New View" option at the bottom
  if (views.length > 0) {
    html += '<div class="flyout-menu__separator" role="separator"></div>';
  }
  html += '<button class="flyout-submenu__item" role="menuitem" data-action="new-view-in-flyout">+ New View</button>';

  var submenu = document.createElement('div');
  submenu.className = 'flyout-submenu';
  submenu.setAttribute('role', 'menu');
  submenu.innerHTML = html;
  document.body.appendChild(submenu);
  _flyoutSubmenuEl = submenu;

  // Position to the right of the trigger item (or left if no space)
  if (_flyoutMenuEl) {
    var menuRect = _flyoutMenuEl.getBoundingClientRect();
    var subWidth = submenu.offsetWidth;
    var subHeight = submenu.offsetHeight;
    var itemRect = triggerItem.getBoundingClientRect();

    var left = menuRect.right + 4;
    if (left + subWidth > window.innerWidth - 8) {
      left = menuRect.left - subWidth - 4;
    }
    var top = itemRect.top;
    if (top + subHeight > window.innerHeight - 8) {
      top = window.innerHeight - subHeight - 8;
    }
    if (top < 8) top = 8;

    submenu.style.top = top + 'px';
    submenu.style.left = left + 'px';
  }

  // Click handler — toggle view membership via PATCH /api/settings
  submenu.addEventListener('click', function(e) {
    // Handle '+ New View' action
    var newViewAction = e.target.closest('[data-action="new-view-in-flyout"]');
    if (newViewAction) {
      var capturedKey = sessionKey;
      closeFlyoutMenu();
      var newName = prompt('View name:');
      if (!newName || !newName.trim()) return;
      newName = newName.trim();
      if (newName.toLowerCase() === 'all' || newName.toLowerCase() === 'hidden') {
        showToast('Cannot use reserved name \'' + newName + '\'');
        return;
      }
      var existViews = (_serverSettings && _serverSettings.views) || [];
      if (existViews.find(function(v) { return v.name === newName; })) {
        showToast('View \'' + newName + '\' already exists');
        return;
      }
      // New-view creation: addSessionToViewOp doesn't model view creation, but
      // we use it on a temp settings (with the new view already appended) so
      // that the hidden_sessions update is expressed via the op layer.
      patchSettingsGuarded(function(fresh) {
        var freshExistViews = (fresh && fresh.views) || [];
        var newView = { name: newName, sessions: [capturedKey] };
        var newViews = freshExistViews.concat([newView]);
        var tempSettings = {
          hidden_sessions: (fresh && fresh.hidden_sessions) || [],
          views: newViews
        };
        return addSessionToViewOp(tempSettings, newName, capturedKey);
      })
        .then(function(body) {
          if (_serverSettings) {
            _serverSettings.views = body.views;
            _serverSettings.hidden_sessions = body.hidden_sessions;
          }
          switchView(newName);
        })
        .catch(function() {
          showToast('Failed to create view');
        });
      return;
    }

    var btn = e.target.closest('[data-view-index]');
    if (!btn) return;
    var idx = parseInt(btn.dataset.viewIndex, 10);

    var views = (_serverSettings && _serverSettings.views) || [];
    var view = views[idx];
    if (!view) return;

    var sessions = view.sessions || [];
    var isAlreadyInView = sessions.indexOf(sessionKey) !== -1;

    patchSettingsGuarded(function(fresh) {
      return isAlreadyInView
        ? removeSessionFromViewOp(fresh, view.name, sessionKey)
        : addSessionToViewOp(fresh, view.name, sessionKey);
    })
      .then(function(body) {
        if (_serverSettings) {
          _serverSettings.views = body.views;
          if (body.hidden_sessions) _serverSettings.hidden_sessions = body.hidden_sessions;
        }
        // Update checkmarks in submenu
        if (_flyoutSubmenuEl) {
          var checkItems = _flyoutSubmenuEl.querySelectorAll('[data-view-index]');
          for (var ci = 0; ci < checkItems.length; ci++) {
            var vi = parseInt(checkItems[ci].dataset.viewIndex, 10);
            var checkEl = checkItems[ci].querySelector('.flyout-submenu__check');
            var updViews = (_serverSettings && _serverSettings.views) || [];
            if (checkEl && updViews[vi]) {
              checkEl.textContent = (updViews[vi].sessions || []).indexOf(sessionKey) !== -1 ? '\u2713' : '';
            }
          }
        }
        if (!isAlreadyInView && body.hidden_sessions) {
          renderGrid(_currentSessions || []);
        }
      })
      .catch(function(err) {
        showToast('Couldn\u2019t save \u2014 try again');
        console.warn('[_openFlyoutSubmenu] PATCH failed:', err);
      });
  });
}

/**
 * Hide a session: add to hidden_sessions and remove from ALL views.
 * Closes the flyout and re-renders the grid.
 */
function _doHideSession() {
  var sessionKey = _flyoutSessionKey;
  if (!sessionKey) return;

  closeFlyoutMenu();

  patchSettingsGuarded(function(fresh) { return hideSessionOp(fresh, sessionKey); })
    .then(function(body) {
      if (_serverSettings) {
        _serverSettings.hidden_sessions = body.hidden_sessions;
        _serverSettings.views = body.views;
      }
      renderGrid(_currentSessions || []);
      renderViewDropdown();
    })
    .catch(function(err) {
      showToast('Couldn\u2019t save \u2014 try again');
      console.warn('[_doHideSession] PATCH failed:', err);
    });
}

/**
 * Unhide a session: remove from hidden_sessions.
 * Closes the flyout and re-renders the grid.
 */
function _doUnhideSession() {
  var sessionKey = _flyoutSessionKey;
  if (!sessionKey) return;

  // Early-exit if session is not hidden (no PATCH needed).
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  if (hidden.indexOf(sessionKey) === -1) { closeFlyoutMenu(); return; }

  closeFlyoutMenu();

  patchSettingsGuarded(function(fresh) { return unhideSessionOp(fresh, sessionKey); })
    .then(function(body) {
      if (_serverSettings) _serverSettings.hidden_sessions = body.hidden_sessions;
      renderGrid(_currentSessions || []);
      renderViewDropdown();
    })
    .catch(function(err) {
      showToast('Couldn\u2019t save \u2014 try again');
      console.warn('[_doUnhideSession] PATCH failed:', err);
    });
}

/**
 * Remove a session from the currently active user view.
 * Closes the flyout and re-renders the grid.
 */
function _doRemoveFromView() {
  var sessionKey = _flyoutSessionKey;
  if (!sessionKey || _activeView === 'all' || _activeView === 'hidden') return;

  closeFlyoutMenu();

  patchSettingsGuarded(function(fresh) { return removeSessionFromViewOp(fresh, _activeView, sessionKey); })
    .then(function(body) {
      if (_serverSettings) _serverSettings.views = body.views;
      renderGrid(_currentSessions || []);
    })
    .catch(function(err) {
      showToast('Couldn\u2019t save \u2014 try again');
      console.warn('[_doRemoveFromView] PATCH failed:', err);
    });
}

/**
 * Show inline kill confirmation inside the flyout menu.
 * Replaces the "Kill Session" item with "Kill? [Yes] [No]".
 * No timeout — stays until click-outside closes the menu.
 * On error: "Failed" for 2 seconds then reverts.
 * @param {HTMLElement} killItem - The "Kill Session" menu item element
 */
function _doKillSessionInline(killItem) {
  var sessionName = _flyoutSessionName;
  var remoteId = _flyoutRemoteId;

  // Replace the kill item with confirmation UI
  var confirmHtml =
    '<div class="flyout-menu__confirm">' +
    '<span>Kill?</span>' +
    '<button class="flyout-menu__confirm-btn flyout-menu__confirm-btn--yes" data-action="confirm-kill">Yes</button>' +
    '<button class="flyout-menu__confirm-btn" data-action="cancel-kill">No</button>' +
    '</div>';

  killItem.outerHTML = confirmHtml;

  // Re-attach handlers on the confirm/cancel buttons
  if (!_flyoutMenuEl) return;

  var confirmBtn = _flyoutMenuEl.querySelector('[data-action="confirm-kill"]');
  var cancelBtn = _flyoutMenuEl.querySelector('[data-action="cancel-kill"]');

  if (confirmBtn) {
    confirmBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      _executeKill(sessionName, remoteId);
    });
  }

  if (cancelBtn) {
    cancelBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      closeFlyoutMenu();
    });
  }
}

/**
 * Execute the kill session API call from the flyout inline confirmation.
 * On success: closes flyout, shows toast, refreshes sessions.
 * On error: shows "Failed" for 2s in the confirm area, then reverts.
 * @param {string} name
 * @param {string} remoteId
 */
function _executeKill(name, remoteId) {
  var endpoint = remoteId
    ? '/api/federation/' + encodeURIComponent(remoteId) + '/sessions/' + encodeURIComponent(name)
    : '/api/sessions/' + encodeURIComponent(name);

  api('DELETE', endpoint)
    .then(function() {
      closeFlyoutMenu();
      showToast('Session \'' + name + '\' killed');
      if (_viewingSession === name && (_viewingRemoteId ?? '') === (remoteId || '')) {
        closeSession();
      }
      pollSessions();
    })
    .catch(function(err) {
      // Show "Failed" for 2 seconds
      var confirmDiv = _flyoutMenuEl && _flyoutMenuEl.querySelector('.flyout-menu__confirm');
      if (confirmDiv) {
        confirmDiv.innerHTML = '<span style="color:var(--err)">Failed</span>';
        setTimeout(function() {
          // Revert to original kill button if menu is still open
          if (_flyoutMenuEl && confirmDiv.parentNode) {
            confirmDiv.outerHTML =
              '<button class="flyout-menu__item flyout-menu__item--danger" role="menuitem" data-action="kill">Kill Session</button>';
          }
        }, 2000);
      }
    });
}

// ─── Manage View Panel ──────────────────────────────────────────────────────────────────────────

/**
 * Open the Manage View panel for the active user view.
 * Only available for user views (not "All" or "Hidden").
 * Renders the view name (clickable to rename) and a delete button in the header.
 */
function openManageViewPanel() {
  if (_activeView === 'all' || _activeView === 'hidden') return;

  var panel = $('manage-view-panel');
  if (!panel) return;

  // Rebuild the name-row with view name + delete button
  var nameRow = panel.querySelector('.manage-view-panel__name-row');
  if (nameRow) {
    nameRow.innerHTML =
      '<h2 id="manage-view-name" class="manage-view-panel__name">' + escapeHtml(_activeView) + '</h2>' +
      '<button id="manage-view-delete-btn" class="manage-view-panel__delete-btn" ' +
        'title="Delete this view" aria-label="Delete view">\u2715</button>';
  }

  renderManageViewList();
  _renderManageViewRuleEditor(true); // true: always repopulate from server truth on open
  panel.classList.remove('hidden');

  // Close on backdrop click
  var backdrop = $('manage-view-backdrop');
  if (backdrop) backdrop.onclick = closeManageViewPanel;

  // Close button at bottom
  var closeBtn = $('manage-view-close');
  if (closeBtn) closeBtn.onclick = closeManageViewPanel;

  // — Rename click handler on the view name —
  var nameEl = $('manage-view-name');
  if (nameEl) {
    nameEl.onclick = function() {
      var currentName = _activeView;
      // Replace h2 with an inline input for rename
      var input = document.createElement('input');
      input.type = 'text';
      input.className = 'manage-view-panel__name-input';
      input.value = currentName;
      input.maxLength = 30;
      input.setAttribute('aria-label', 'View name');
      _suppressAutofill(input);
      if (nameEl.parentNode) nameEl.parentNode.replaceChild(input, nameEl);
      input.focus();
      input.select();

      var _committed = false;

      function commitRename() {
        if (_committed) return;
        var newName = input.value.trim();
        if (!newName || newName === currentName) { revertRename(); return; }
        if (newName.toLowerCase() === 'all' || newName.toLowerCase() === 'hidden') {
          showToast('Cannot use reserved name \'' + newName + '\'');
          input.focus(); return;
        }
        var views = (_serverSettings && _serverSettings.views) || [];
        if (views.find(function(v) { return v.name === newName; })) {
          showToast('View \'' + newName + '\' already exists');
          input.focus(); return;
        }
        _committed = true;
        patchSettingsGuarded(function(fresh) {
          var freshViews = (fresh && fresh.views) || [];
          return {
            views: freshViews.map(function(v) {
              return v.name === currentName ? { name: newName, sessions: v.sessions || [] } : v;
            })
          };
        })
          .then(function(body) {
            if (_serverSettings) _serverSettings.views = body.views;
            _activeView = newName;
            persistActiveView(newName);
            renderViewDropdown();
            openManageViewPanel();
          })
          .catch(function() {
            showToast('Failed to rename view');
            _committed = false;
            revertRename();
          });
      }

      function revertRename() {
        if (_committed) return;
        openManageViewPanel();
      }

      input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); commitRename(); }
        else if (e.key === 'Escape') { revertRename(); }
      });
      input.addEventListener('blur', function() {
        setTimeout(function() {
          if (document.activeElement !== input) {
            var newName = input.value.trim();
            if (newName && newName !== currentName) commitRename();
            else revertRename();
          }
        }, 150);
      });
    };
  }

  // — Delete button handler —
  var deleteBtn = $('manage-view-delete-btn');
  if (deleteBtn) {
    deleteBtn.onclick = function(e) {
      e.stopPropagation();
      // Show inline confirmation in the name-row
      var nameRow2 = panel.querySelector('.manage-view-panel__name-row');
      if (!nameRow2) return;
      nameRow2.innerHTML =
        '<span class="manage-view-panel__confirm-text">Delete this view?</span>' +
        '<button id="manage-view-confirm-yes" class="manage-view-panel__close-btn" ' +
          'style="margin-left:8px">Yes</button>' +
        '<button id="manage-view-confirm-no" class="manage-view-panel__close-btn" ' +
          'style="margin-left:4px">No</button>';
      var yesBtn = $('manage-view-confirm-yes');
      var noBtn = $('manage-view-confirm-no');
      if (yesBtn) {
        yesBtn.onclick = function() {
          var viewToDelete = _activeView;
          patchSettingsGuarded(function(fresh) {
            var freshViews = (fresh && fresh.views) || [];
            return { views: freshViews.filter(function(v) { return v.name !== viewToDelete; }) };
          })
            .then(function(body) {
              if (_serverSettings) _serverSettings.views = body.views;
              closeManageViewPanel();
              switchView('all');
              showToast('View \'' + viewToDelete + '\' deleted');
              renderViewDropdown();
            })
            .catch(function() { showToast('Failed to delete view'); });
        };
      }
      if (noBtn) {
        noBtn.onclick = function() { openManageViewPanel(); };
      }
    };
  }
}

/**
 * Close the Manage View panel.
 */
function closeManageViewPanel() {
  var panel = $('manage-view-panel');
  if (panel) panel.classList.add('hidden');
}

/**
 * Render the session list inside the Manage View panel.
 * Shows ALL sessions: checked = in this view, unchecked = not in this view.
 * Checked items sorted first, then unchecked. Within each group, alphabetical by device.
 * Immediate-commit: each checkbox toggle fires PATCH /api/settings immediately.
 * Hidden sessions: dimmed with "hidden" badge. Static note below for hidden items.
 */
function renderManageViewList() {
  var listEl = $('manage-view-list');
  var summaryEl = $('manage-view-summary');
  if (!listEl) return;

  var views = (_serverSettings && _serverSettings.views) || [];

  // Find the active view's session list
  var activeViewObj = null;
  for (var i = 0; i < views.length; i++) {
    if (views[i].name === _activeView) {
      activeViewObj = views[i];
      break;
    }
  }
  if (!activeViewObj) { listEl.innerHTML = ''; return; }
  var viewSessions = activeViewObj.sessions || [];

  // Get all real sessions (not status entries)
  var allSessions = (_currentSessions || []).filter(function(s) {
    return !s.status;
  });

  // Update summary line
  if (summaryEl) {
    summaryEl.textContent = allSessions.length + ' sessions · ' + visibleCount(_currentSessions, _serverSettings, _activeView, { includeHidden: true }) + ' in this view';
  }

  // Membership per session: pinned (in viewSessions -- togglable) or
  // matched-by-rule (in s.views but not pinned -- docs/plans/2026-08-04-auto-views-plan.md §9.3,
  // shown as a member but not removable here, since there's no pin to
  // remove). Both count as "in view" for partitioning purposes.
  function isPinned(s) {
    var key = s.sessionKey || s.name;
    return viewSessions.indexOf(key) !== -1 || viewSessions.indexOf(s.name) !== -1;
  }
  function isMatchedByRule(s) {
    return !isPinned(s) && (s.views || []).indexOf(_activeView) !== -1;
  }
  var inView = allSessions.filter(function(s) { return isPinned(s) || isMatchedByRule(s); });
  var notInView = allSessions.filter(function(s) { return !isPinned(s) && !isMatchedByRule(s); });

  // Sort each group: alphabetical, grouped by device
  function sortByDeviceAlpha(arr) {
    return arr.slice().sort(function(a, b) {
      var da = (_getDeviceDisplayName(a) || '').toLowerCase();
      var db = (_getDeviceDisplayName(b) || '').toLowerCase();
      if (da !== db) return da < db ? -1 : 1;
      var na = (a.name || '').toLowerCase();
      var nb = (b.name || '').toLowerCase();
      return na < nb ? -1 : na > nb ? 1 : 0;
    });
  }

  var sorted = sortByDeviceAlpha(inView).concat(sortByDeviceAlpha(notInView));

  var html = '';
  for (var j = 0; j < sorted.length; j++) {
    var s = sorted[j];
    var key = s.sessionKey || s.name;
    var isInView = viewSessions.indexOf(key) !== -1 || viewSessions.indexOf(s.name) !== -1;
    // Phase 5: use the isHidden() helper (Phase 1) — do not inline a hidden check here.
    // The manage-view-item--hidden class triggers opacity + CSS ::after "(hidden)" badge.
    var sessionIsHidden = isHidden(key, _serverSettings);
    var escapedName = escapeHtml(s.name || '');
    var deviceName = escapeHtml(_getDeviceDisplayName(s) || '');

    html += '<label class="manage-view-item' + (sessionIsHidden ? ' manage-view-item--hidden' : '') + '">';
    html += '<input type="checkbox" class="manage-view-item__checkbox" data-session-key="' + escapeHtml(key) + '"' + (isInView ? ' checked' : '') + (sessionIsHidden ? ' data-is-hidden="1"' : '') + ' />';
    html += '<span class="manage-view-item__name">' + escapedName + '</span>';
    if (deviceName) html += '<span class="manage-view-item__device">' + deviceName + '</span>';
    html += '</label>';
    if (sessionIsHidden) {
      html += '<div class="manage-view-item__disclosure">Adding will unhide this session</div>';
    }
  }

  listEl.innerHTML = html;

  // Delegated change handler for immediate-commit checkboxes
  listEl.onchange = function(e) {
    var cb = e.target.closest('.manage-view-item__checkbox');
    if (!cb) return;
    var sessionKey = cb.dataset.sessionKey;
    var isChecked = cb.checked;

    patchSettingsGuarded(function(fresh) {
      return isChecked
        ? addSessionToViewOp(fresh, _activeView, sessionKey)
        : removeSessionFromViewOp(fresh, _activeView, sessionKey);
    })
      .then(function(body) {
        if (_serverSettings) {
          _serverSettings.views = body.views;
          if (body.hidden_sessions) _serverSettings.hidden_sessions = body.hidden_sessions;
        }
        // Update summary count in-place — do NOT re-render the full list (avoids layout thrash)
        var summaryEl = $('manage-view-summary');
        if (summaryEl) {
          var latestViews = (_serverSettings && _serverSettings.views) || [];
          var latestViewObj = null;
          for (var si = 0; si < latestViews.length; si++) {
            if (latestViews[si].name === _activeView) { latestViewObj = latestViews[si]; break; }
          }
          var latestViewSessions = (latestViewObj && latestViewObj.sessions) || [];
          var latestAllSessions = (_currentSessions || []).filter(function(s) { return !s.status; });
          summaryEl.textContent = latestAllSessions.length + ' sessions \u00b7 ' + latestViewSessions.length + ' in this view';
        }
        renderGrid(_currentSessions || []);
      })
      .catch(function(err) {
        showToast('Couldn’t save — try again');
        if (cb) cb.checked = !isChecked;
        console.warn('[renderManageViewList] PATCH failed:', err);
      });
  };
}

// ─── Manage View rule editor (docs/plans/2026-08-04-auto-views-plan.md §9.3) ─────────────────────
//
// One textarea, one pattern per line, blank lines ignored. Saved through the
// existing patchSettingsGuarded() path (so CAS and the destructive-write
// backstop come for free) as view.match_names -- newline-separated, not
// comma-separated, because tmux permits commas in session names but not
// newlines.
//
// Validation and match preview both ask the server (POST /api/views/preview)
// rather than re-implementing the glob matcher client-side (AGENTS.md's "the
// matcher lives in exactly one place" -- this frontend must never import a
// glob-matching library or hand-roll one). This is the SAME reason
// GET /api/views exists for the read-only error badges above; the preview
// endpoint is its write-side sibling.

/**
 * Parse the rule textarea's raw value into a pattern list: one pattern per
 * line, blank lines ignored, no trimming of interior whitespace within a
 * non-blank line (a leading/trailing space is trimmed per line since it can
 * never be a meaningful part of a glob and stray whitespace from
 * copy-paste is the overwhelmingly likely case; a pattern that is ONLY
 * whitespace is indistinguishable from a blank line and is dropped, same
 * as a truly empty line).
 * @param {string} text
 * @returns {string[]}
 */
function _parseViewRulePatterns(text) {
  return (text || '')
    .split('\n')
    .map(function(line) { return line.trim(); })
    .filter(function(line) { return line.length > 0; });
}

/**
 * Render the rule-editor's error list and match-preview line from a
 * `{errors, matches}` result (either GET /api/views' per-view errors, used
 * as the initial/at-rest state, or POST /api/views/preview's live result
 * while the user is typing -- same shape, same renderer).
 * @param {string[]} errors
 * @param {string[]|null} matches - null means "not yet computed" (initial
 *   render before the first preview call resolves) vs [] meaning "computed,
 *   zero live sessions match".
 * @param {number} patternCount - how many patterns are currently in the
 *   textarea, to distinguish "no patterns yet" from "patterns but no match".
 */
function _renderManageViewRuleFeedback(errors, matches, patternCount) {
  var errorsEl = $('manage-view-rules-errors');
  var previewEl = $('manage-view-rules-preview');
  var saveBtn = $('manage-view-rules-save');

  if (errorsEl) {
    if (errors && errors.length > 0) {
      errorsEl.innerHTML = errors.map(function(e) {
        return '<li>' + escapeHtml(e) + '</li>';
      }).join('');
      errorsEl.classList.remove('hidden');
    } else {
      errorsEl.innerHTML = '';
      errorsEl.classList.add('hidden');
    }
  }

  if (previewEl) {
    if (patternCount === 0) {
      previewEl.textContent = 'No patterns yet \u2014 sessions must be pinned manually below.';
    } else if (matches === null) {
      previewEl.textContent = 'Checking matches\u2026';
    } else if (matches.length === 0) {
      previewEl.textContent = 'Matches no currently running sessions.';
    } else {
      previewEl.textContent =
        'Matches ' + matches.length + ' running session' + (matches.length === 1 ? '' : 's') +
        ': ' + matches.join(', ');
    }
  }

  if (saveBtn) {
    // Never offer Save while a pattern is known-invalid -- validation
    // feedback belongs in the editor, before a save attempt, not as a
    // relayed 400 (docs/plans/2026-08-04-auto-views-plan.md §9.3 / this task's non-negotiables).
    saveBtn.disabled = !_manageViewRulesDirty || (errors && errors.length > 0);
  }
}

/**
 * Ask the server what a DRAFT pattern list would match, via
 * POST /api/views/preview -- never a client-side matcher. Debounced by the
 * caller (oninput handler below); this function itself fires immediately.
 * A stale, out-of-order response (a fast second call resolving out of
 * order with a slow first one) is dropped via the token check.
 * @param {string[]} patterns
 */
function _previewManageViewRule(patterns) {
  var token = ++_manageViewRulesPreviewToken;
  if (patterns.length === 0) {
    // Nothing to ask the server -- render the "no patterns yet" state
    // directly and skip the round trip.
    _renderManageViewRuleFeedback([], [], 0);
    return;
  }
  api('POST', '/api/views/preview', { match_names: patterns })
    .then(function(res) { return res.json(); })
    .then(function(body) {
      if (token !== _manageViewRulesPreviewToken) return; // superseded
      _renderManageViewRuleFeedback(body.errors || [], body.matches || [], patterns.length);
    })
    .catch(function(err) {
      if (token !== _manageViewRulesPreviewToken) return;
      console.warn('[_previewManageViewRule] failed:', err);
      // Leave the previous render in place rather than clobbering it with
      // an empty/misleading state on a transient network error.
    });
}

/**
 * Populate and wire the Manage View panel's rule editor for the active view.
 * Called on open (forceReset: true, always repopulates from server truth)
 * and on background re-renders that might reflect a remote settings change
 * (forceReset: false, skips repopulating while the user has an unsaved,
 * in-progress edit -- see _manageViewRulesDirty).
 * @param {boolean} forceReset
 */
function _renderManageViewRuleEditor(forceReset) {
  var textarea = $('manage-view-rules-input');
  var saveBtn = $('manage-view-rules-save');
  if (!textarea) return;

  var views = (_serverSettings && _serverSettings.views) || [];
  var view = null;
  for (var i = 0; i < views.length; i++) {
    if (views[i].name === _activeView) { view = views[i]; break; }
  }
  if (!view) return; // "all"/"hidden" or a just-deleted view -- nothing to edit

  if (forceReset || !_manageViewRulesDirty) {
    textarea.value = (view.match_names || []).join('\n');
    _manageViewRulesDirty = false;
    // At-rest state: reuse the errors this view already reported via
    // GET /api/views (loadViewRules(), fetched at page load / openSettings)
    // rather than firing an immediate preview round trip on every open.
    // Matched by the `'<name>':` fragment every views[i] error line contains
    // (see main.py's get_views()) -- name-based, not index-based, since this
    // render doesn't know the server's own array index for this view.
    var restErrors = (_viewRuleErrors || []).filter(function(e) {
      return e.indexOf("'" + view.name + "':") !== -1;
    });
    _renderManageViewRuleFeedback(restErrors, view.match_names || [], (view.match_names || []).length);
  }

  var viewNameAtRender = view.name;

  textarea.oninput = function() {
    _manageViewRulesDirty = true;
    if (saveBtn) saveBtn.disabled = true; // re-enabled once a clean preview resolves
    var patterns = _parseViewRulePatterns(textarea.value);
    if (_manageViewRulesPreviewTimer) clearTimeout(_manageViewRulesPreviewTimer);
    _manageViewRulesPreviewTimer = setTimeout(function() {
      _manageViewRulesPreviewTimer = null;
      _previewManageViewRule(patterns);
    }, 300);
  };

  if (saveBtn) {
    saveBtn.onclick = function() {
      if (saveBtn.disabled) return;
      var patterns = _parseViewRulePatterns(textarea.value);
      saveBtn.disabled = true;
      patchSettingsGuarded(function(fresh) {
        var freshViews = (fresh && fresh.views) || [];
        return {
          views: freshViews.map(function(v) {
            return v.name === viewNameAtRender ? Object.assign({}, v, { match_names: patterns }) : v;
          })
        };
      })
        .then(function(body) {
          if (_serverSettings) _serverSettings.views = body.views;
          _manageViewRulesDirty = false;
          showToast('Rules saved');
          renderViewsSettingsTab();
          renderManageViewList();
          renderGrid(_currentSessions || []);
          loadViewRules(); // refresh the Settings gear/tab error badges
        })
        .catch(function(err) {
          if (err && err.body && err.body.invalid_view_rule) {
            _renderManageViewRuleFeedback(err.body.errors || [err.body.detail], null, patterns.length);
            showToast('Couldn\u2019t save \u2014 fix the highlighted pattern');
          } else if (err && err.body && err.body.backstop) {
            showToast('Server rejected this as too large a change \u2014 see console');
            console.warn('[manage-view-rules save] destructive-write backstop:', err.body.detail);
          } else {
            showToast('Couldn\u2019t save \u2014 try again');
            console.warn('[manage-view-rules save] PATCH failed:', err);
          }
          saveBtn.disabled = false;
        });
    };
  }
}

/**
 * Get a human-readable display name for the device a session belongs to.
 * Priority: friendly name → hostname → truncated device_id → empty string.
 * @param {object} session
 * @returns {string}
 */
function _getDeviceDisplayName(session) {
  if (!session) return '';
  if (session.device_name) return session.device_name;
  if (session.deviceName) return session.deviceName;
  if (session.hostname) return session.hostname;
  if (session.device_id) return session.device_id.slice(0, 8);
  return '';
}


// ─── Notification permission ────────────────────────────────────────────────

/**
 * Request browser notification permission on first load.
 * - If the Notification API is not available, returns immediately.
 * - If already granted, records the state synchronously.
 * - If default (not yet asked), calls requestPermission() and stores the result.
 * - Otherwise (e.g. denied), stores the current permission value.
 */
function requestNotificationPermission() {
  if (typeof Notification === 'undefined') return;
  if (Notification.permission === 'granted') {
    _notificationPermission = 'granted';
  } else if (Notification.permission === 'default') {
    Notification.requestPermission().then((permission) => {
      _notificationPermission = permission;
    });
  } else {
    _notificationPermission = Notification.permission;
  }
}

// ─── Bell transition notifications ─────────────────────────────────────────

/**
 * Fire OS notifications for sessions that have newly received a bell event.
 * Only fires when the Notification permission is granted AND the browser tab
 * is currently hidden (document.hidden === true).
 * Uses a per-session tag so the OS deduplicates multiple bells into one
 * notification per session.
 * @param {object[]} prevSessions - sessions array from the previous poll
 * @param {object[]} nextSessions - sessions array from the current poll
 */
function handleBellTransitions(prevSessions, nextSessions) {
  const transitions = detectBellTransitions(prevSessions, nextSessions);
  for (const name of transitions) {
    if (_notificationPermission === 'granted' && document.hidden) {
      // eslint-disable-next-line no-new
      new Notification('Activity in: ' + name, {
        body: 'tmux session needs attention',
        tag: 'tmux-bell-' + name,
      });
    }
  }
}

// ─── Heartbeat ──────────────────────────────────────────────────────────────────

/**
 * Send a single heartbeat POST to /api/heartbeat.
 * Catches errors and logs them as warnings — never throws.
 * @returns {Promise<void>}
 */
async function sendHeartbeat() {
  try {
    // When the browser tab is hidden (user switched tabs or minimized), report
    // viewing_session as null.  This prevents the server from clearing bells on
    // the session — the user isn't actually looking at it, so activity should
    // accumulate and show in the favicon badge / tab indicators.
    var effectiveSession = (typeof document !== 'undefined' && document.hidden)
      ? null
      : _viewingSession;
    // resolveSyncGroupForWire() defers to the pre-existing syncGroupId()
    // whenever no registered-device follow target is set (_followTarget
    // null) -- byte-identical to pre-Step-4 behavior for every existing
    // client. It only diverges when a Step-4 follow target is active.
    const payload = buildHeartbeatPayload(_deviceId, effectiveSession, _viewMode, _lastInteractionAt, resolveSyncGroupForWire());
    await api('POST', '/api/heartbeat', payload);
  } catch (err) {
    // A background re-heartbeat for an active follow target that gets
    // rejected (409 target_gone / 400 target_not_self_owning) marks it
    // degraded -- same wire-level fallback deck.js's applyHeartbeatOutcome
    // uses (§6.2.4): stop resending the foreign claim, stay sticky+degraded
    // until the human acts (§7.2/§9.1), rather than throwing further.
    if (_followTarget && err && (err.status === 409 || err.status === 400)) {
      _lastHeartbeatGoneId = _followTarget.targetId;
      renderSyncGroupControls();
    }
    console.warn('[sendHeartbeat] heartbeat failed:', err);
  }
}

/**
 * Start the heartbeat loop. Guards against double-start.
 * Uses self-scheduling setTimeout so at most one heartbeat is in-flight at a time.
 * Calls sendHeartbeat() immediately, then HEARTBEAT_MS after each completion.
 */
function startHeartbeat() {
  if (_heartbeatTimer) return;
  _heartbeatTimer = true; // sentinel: prevents double-start before first setTimeout fires
  async function heartbeatLoop() {
    await sendHeartbeat();
    _heartbeatTimer = setTimeout(heartbeatLoop, HEARTBEAT_MS);
  }
  heartbeatLoop();
}

/** Test-only helper: reset heartbeat timer state so tests can exercise startHeartbeat cleanly. */
function _resetHeartbeatTimer() {
  if (_heartbeatTimer) clearTimeout(_heartbeatTimer);
  _heartbeatTimer = undefined;
}

// ─── Toast notification ─────────────────────────────────────────────────────

/**
 * Show a brief toast message.
 * Removes the 'hidden' class immediately, then restores it after 3000ms.
 * @param {string} msg
 */
function showToast(msg) {
  const el = $('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 3000);
}

// ─── Session pill bell ───────────────────────────────────────────────────────

/**
 * Update the floating session-pill bell indicator.
 * Shows #session-pill-bell if any session other than _viewingSession has unseen bells.
 */
function updatePillBell() {
  const el = $('session-pill-bell');
  if (!el) return;
  const viewingKey = _viewingRemoteId ? (_viewingRemoteId + ':' + _viewingSession) : _viewingSession;
  const hasBell = _currentSessions.some(
    (s) => (s.sessionKey || s.name) !== viewingKey && s.bell && s.bell.unseen_count > 0,
  );
  if (hasBell) el.classList.remove('hidden'); else el.classList.add('hidden');
}

// ---------------------------------------------------------------------------
// Dynamic favicon — activity dot overlay
// ---------------------------------------------------------------------------

var _originalFavicon = null; // cached original favicon href
var _faviconImage = null;    // cached Image object for favicon badge compositing — avoids re-fetching every poll

/**
 * Draw the favicon activity badge onto the <link> element.
 * Owns the _faviconImage lifecycle: lazily creates it once (caching it in the module-level
 * variable) and reuses it on all subsequent calls. This avoids re-fetching favicon-32.png
 * on every poll cycle (previously new Image() was created inside updateFaviconBadge every 2s).
 * If the image is not yet loaded, registers an onload callback to retry automatically.
 */
function _drawFaviconBadge() {
  // Lazy-init: create the Image object once and cache it — subsequent calls reuse it
  if (!_faviconImage) {
    _faviconImage = new Image();
    // No crossOrigin: favicon is same-origin; crossOrigin on same-origin images can
    // cause cache misses when the browser has the asset cached without CORS headers.
    _faviconImage.src = _originalFavicon;
  }

  // If image is not yet loaded, wait for it (onload will call us back)
  if (!_faviconImage.complete || _faviconImage.naturalWidth === 0) {
    _faviconImage.onload = function() { _drawFaviconBadge(); };
    return;
  }

  var link = document.querySelector('link[rel="icon"][sizes="32x32"]') ||
             document.querySelector('link[rel="icon"]');
  if (!link) return;

  var canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  ctx.drawImage(_faviconImage, 0, 0, 32, 32);

  // Activity dot — brand amber (same as bell indicator)
  ctx.beginPath();
  ctx.arc(24, 8, 7, 0, 2 * Math.PI); // top-right area
  ctx.fillStyle = '#F1A640';           // var(--bell-color)
  ctx.fill();
  ctx.strokeStyle = '#0D1117';         // var(--bg) — border for contrast
  ctx.lineWidth = 2;
  ctx.stroke();

  link.href = canvas.toDataURL('image/png');
}

/**
 * Update the favicon with an activity dot if any session has unseen bells.
 * Uses a 32x32 canvas to draw the original favicon + a colored circle overlay.
 * Restores the original favicon when there are no unseen bells.
 * Delegates drawing to _drawFaviconBadge which manages the cached Image object.
 */
function updateFaviconBadge() {
  var visible = getVisibleSessions(_currentSessions);
  var hasActivity = visible.length > 0 && visible.some(function (s) {
    return s.bell && s.bell.unseen_count > 0;
  });

  var link = document.querySelector('link[rel="icon"][sizes="32x32"]') ||
             document.querySelector('link[rel="icon"]');
  if (!link) return;

  // Cache the original favicon href on first call
  if (!_originalFavicon) _originalFavicon = link.href;

  if (!hasActivity) {
    // Restore original favicon when no activity
    if (link.href !== _originalFavicon) link.href = _originalFavicon;
    return;
  }

  _drawFaviconBadge();
}

/**
 * Update the page title with an optional activity count prefix and the hostname.
 * Format: "(N) hostname - muxplex" when N sessions have unseen bells, otherwise
 * "hostname - muxplex". Hostname is device_name from server settings, falling back
 * to location.hostname so even unconfigured installs show something useful.
 * Call from pollSessions() on every tick, and whenever server settings change.
 */
function updatePageTitle() {
  var hostname = (_serverSettings && _serverSettings.device_name) ||
                 (typeof location !== 'undefined' ? location.hostname : null) ||
                 'muxplex';
  var visible = getVisibleSessions(_currentSessions);
  var count = visible.filter(function(s) {
    return s.bell && s.bell.unseen_count > 0;
  }).length;
  var prefix = count > 0 ? '(' + count + ') ' : '';
  document.title = prefix + hostname + ' - muxplex';
}

// ─── Session open / close ────────────────────────────────────────────────────

/**
 * Open a session in fullscreen view with a zoom transition.
 * @param {string} name - session name
 * @param {object} [opts]
 * @param {boolean} [opts.skipAnimation] - if true, skip the zoom animation (e.g. on page restore)
 * @returns {Promise<void>}
 */
async function openSession(name, opts = {}) {
  if (!name || !name.trim()) return;
  // A LOCAL switch (as opposed to adopting a value the server already told us
  // about -- restoreState() on page load, or followRemoteActiveSession()
  // echoing a remote switch, both of which pass isFollow:true). Mark this
  // switch pending until its own server write settles (see the two places
  // below that decrement), so a stale /api/state read that lands before then
  // isn't mistaken for a genuine remote switch and doesn't yank the user back
  // to the session they just switched away from.
  var isLocal = !opts.isFollow;
  if (isLocal) _pendingLocalSwitches++;
  hidePreview();
  _viewingSession = name;
  _viewingRemoteId = opts.remoteId != null ? opts.remoteId : '';
  _viewMode = 'fullscreen';

  // Pre-render sidebar with current sessions before first poll tick
  initSidebar();
  renderSidebar(_currentSessions, name, _viewingRemoteId);

  // Update expanded header
  const nameEl = $('expanded-session-name');
  if (nameEl) nameEl.textContent = name;

  // Zoom animation: pin tile at current position, then animate to full viewport
  // Skipped on restore (skipAnimation:true) — no tile DOM element to zoom from
  const tile = opts.skipAnimation ? null : document.querySelector(`[data-session="${name}"]`);
  if (tile) {
    const rect = tile.getBoundingClientRect();
    tile.style.position = 'fixed';
    tile.style.top = rect.top + 'px';
    tile.style.left = rect.left + 'px';
    tile.style.width = rect.width + 'px';
    tile.style.height = rect.height + 'px';
    tile.style.transition = 'none';
    // Force reflow
    void tile.offsetWidth;
    tile.style.transition = 'all 250ms ease';
    tile.style.top = '0';
    tile.style.left = '0';
    tile.style.width = '100vw';
    tile.style.height = '100vh';
  }

  // Start animation concurrently with /connect POST — resolve when view is ready
  var animDone = new Promise(function (resolve) {
    var timerId = setTimeout(function () {
      var overview = $('view-overview');
      var expanded = $('view-expanded');
      if (overview) overview.style.display = 'none';
      if (expanded) {
        expanded.classList.remove('hidden');     // must remove class — !important wins over style.display
        expanded.classList.add('view--active');  // makes it display:flex
      }
      // Re-render sidebar after DOM is visible and dimensions are correct
      initSidebar();
      renderSidebar(_currentSessions, name, _viewingRemoteId);
      resolve();
    }, opts.skipAnimation ? 0 : 260);
    // If setTimeout is stubbed (e.g. in test env), resolve immediately so we don't hang
    if (timerId == null) resolve();
  });

  // Mobile pill
  if (isMobile()) {
    const pill = $('session-pill');
    if (pill) {
      pill.classList.remove('hidden');         // pill starts with hidden class
      const pillLabel = $('session-pill-label');
      if (pillLabel) pillLabel.textContent = name;
    }
    updatePillBell();
    updateSessionPill(_currentSessions);
  }

  // Hide FAB during fullscreen session view
  const fab = $('new-session-fab');
  if (fab) fab.classList.add('hidden');

  // Always spawn ttyd for this session — ensures correct session after service restart or page restore
  // _deviceId holds the device_id string (was integer remoteId index in old protocol)
  var _deviceId = opts.remoteId != null ? opts.remoteId : '';
  try {
    if (_deviceId !== '') {
      // Remote session: route connect POST through same-origin federation proxy
      await api('POST', '/api/federation/' + encodeURIComponent(_deviceId) + '/connect/' + encodeURIComponent(name));
    } else {
      await api('POST', withDevice('/api/sessions/' + encodeURIComponent(name) + '/connect'));
    }
  } catch (err) {
    if (isLocal) _pendingLocalSwitches--;
    if (err && err.status === 409 && err.body && err.body.terminal_conflict) {
      showTerminalConflictDialog(name, err.body);
      return closeSession();
    }
    showToast(err.message || 'Connection failed');
    return closeSession();
  }

  // Persist active_remote_id so restoreState() can reopen remote sessions after page refresh.
  // Fire-and-forget for the caller (never awaited -- must not delay terminal mount below), but
  // still tracked so a LOCAL switch's pending flag clears the moment the server confirms this
  // write (success or failure), rather than lingering indefinitely.
  var statePatch = api('PATCH', withDevice('/api/state'), { active_session: name, active_remote_id: _deviceId || null }).catch(function() {});
  if (isLocal) {
    statePatch.then(function() { _pendingLocalSwitches--; });
  }

  // Fire-and-forget bell-clear for remote sessions — acknowledge bells on the remote server
  if (_deviceId !== '') {
    api('POST', '/api/federation/' + encodeURIComponent(_deviceId) + '/sessions/' + encodeURIComponent(name) + '/bell/clear').catch(function() {});
  }

  // Wait for animation to finish (may already be done if /connect was slow)
  await animDone;

  // Mount terminal NOW — /connect has completed, new ttyd is serving the correct session
  if (window._openTerminal) window._openTerminal(name, _deviceId, getDisplaySettings().fontSize, _ownDeviceId());
  _composeOnSessionOpen();
}

/**
 * Close the current session and return to the grid view.
 * @returns {Promise<void>}
 */
function closeSession() {
  _viewMode = 'grid';
  _viewingSession = null;
  _composeOnSessionClose();

  if (window._closeTerminal) window._closeTerminal();

  // Fire-and-forget DELETE — skip for remote sessions (they don't need to know we stopped watching)
  if (_viewingRemoteId === '') {
    api('DELETE', withDevice('/api/sessions/current')).catch(function() {});
  }
  // Clear active_remote_id so a page refresh does not attempt to reopen the remote session
  api('PATCH', withDevice('/api/state'), { active_session: null, active_remote_id: null }).catch(function() {});
  _viewingRemoteId = '';

  const expanded = $('view-expanded');
  const overview = $('view-overview');
  if (expanded) {
    expanded.classList.add('hidden');
    expanded.classList.remove('view--active');
  }
  if (overview) overview.style.display = '';  // overview uses view--active (no !important), style.display clears fine

  // Reapply fit layout after overview becomes visible again
  var _closDs = getDisplaySettings();
  if ((_closDs.viewMode || 'auto') === 'fit') {
    var _closGrid = document.getElementById('session-grid');
    if (_closGrid) {
      _closGrid.classList.add('session-grid--fit');
      applyFitLayout(_closGrid);
    }
  }

  const pill = $('session-pill');
  if (pill) pill.classList.add('hidden');

  // Restore FAB when returning to overview
  const fab = $('new-session-fab');
  if (fab) fab.classList.remove('hidden');

  return Promise.resolve();
}

/**
 * Honest dialog for a terminal-claim conflict (POST /connect -> 409
 * terminal_conflict). The single shared ttyd is already showing another
 * device's session; taking over would move that device's terminal. Never
 * silently proceeds -- the user must explicitly confirm.
 * @param {string} name - the session this device tried to open
 * @param {object} body - the 409 response body ({terminal_session, ...})
 */
function showTerminalConflictDialog(name, body) {
  var otherSession = (body && body.terminal_session) || 'another session';
  var proceed = window.confirm(
    otherSession + ' is open on another device. Opening ' + name +
    ' here will move that device\'s terminal.\n\nTake over?'
  );
  if (proceed) {
    openSession(name, { skipAnimation: true, takeover: true });
  }
}

/**
 * Name of the session currently open/zoomed-in in this browser (the one
 * `view-expanded` is showing full-screen), or null when the user is on the
 * grid overview with nothing open -- i.e. this browser's own notion of
 * "focus" (muxplex-h2f). Read live: `_viewingSession` is only ever set by
 * openSession() and cleared by closeSession(), so a caller that reads this
 * right before use always gets the current state, never a stale snapshot.
 *
 * Deliberately NOT `_activeView` (the dashboard's grid VIEW FILTER --
 * 'all'/'hidden'/a named view; see its own declaration a few hundred lines
 * up). The two are independent: `_activeView` can sit on 'all' while a
 * single session is genuinely open (confirmed empirically against a live
 * instance -- GET /api/state returned `active_session:"sort-check"` next to
 * `active_view:"all"` at the same moment), so treating 'all' as "nothing is
 * focused" would be plain wrong whenever that's true. This is why the chat
 * panel's focus-awareness (chat.js) reads THIS function, not `_activeView`.
 *
 * Scoped to a LOCAL session on purpose: a session opened from a federation
 * peer (`_viewingRemoteId` non-empty) is not one of this instance's own
 * sessions, and `list_muxplex_sessions` (the chat panel's session-listing
 * tool) is local-only -- so a remote focus is reported here as "no local
 * focus" rather than naming a session that tool's own result would not
 * contain.
 * @returns {string|null}
 */
function getFocusedSessionName() {
  if (_viewingRemoteId) return null;
  return _viewingSession;
}

/** Test-only helper: set _viewingSession directly. */
function _setViewingSession(name) {
  _viewingSession = name;
}

/**
 * Test helper: set _viewingRemoteId directly.
 * @param {string} remoteId
 */
function _setViewingRemoteId(remoteId) {
  _viewingRemoteId = remoteId;
}

/**
 * Test helper: set _pendingLocalSwitches directly (bypasses openSession's
 * real increment/decrement so tests can exercise the guard deterministically).
 */
function _setPendingLocalSwitches(n) {
  _pendingLocalSwitches = n;
}

/**
 * Test helper: set _pendingViewSwitches directly (bypasses
 * persistActiveView()'s real increment/decrement so tests can exercise the
 * guard deterministically).
 */
function _setPendingViewSwitches(n) {
  _pendingViewSwitches = n;
}

/** Test-only helper: set _syncGroup directly, bypassing localStorage/heartbeat. */
function _setSyncGroupMode(mode) {
  _syncGroup = mode;
}

/** Test-only helper: set _deviceId directly, bypassing initDeviceId(). */
function _setDeviceId(id) {
  _deviceId = id;
}

/** Test-only helper: set _followTarget directly, bypassing localStorage/heartbeat. */
function _setFollowTargetForTests(target) {
  _followTarget = target;
}

/** Test-only helper: set _devicesRegistry directly, bypassing a real /api/state poll. */
function _setDevicesRegistryForTests(devices) {
  _devicesRegistry = devices || {};
}

/** Test-only helper: set _serverName directly, bypassing a real /api/instance-info fetch. */
function _setServerNameForTests(name) {
  _serverName = name || '';
}

/** Test-only helper: set _federatedDevicesRaw directly, bypassing a real GET /api/federation/devices fetch. */
function _setFederatedDevicesRawForTests(rawEntries) {
  _federatedDevicesRaw = rawEntries || [];
}

/** Test-only helper: set _serverSettings directly, bypassing a real GET /api/settings fetch -- used by resolveFederatedPeerUrl's remote_instances lookup. */
function _setServerSettingsForTests(settings) {
  _serverSettings = settings || null;
}

/** Test-only helper: set _lastHeartbeatGoneId directly. */
function _setLastHeartbeatGoneIdForTests(id) {
  _lastHeartbeatGoneId = id;
}

// ─── Compose bar ─────────────────────────────────────────────────────────
//
// A mobile text-compose bar below the terminal in the expanded (session)
// view: type or dictate multiline text, then Send. It is a PLAIN UI CLIENT
// of the existing, unmodified POST /api/sessions/{name}/input -- the exact
// same endpoint an agent, muxplex-deck, or curl already calls, with the
// exact same fences (settings.input_enabled / settings.input_allowed_sessions,
// both LOCAL-FILE-ONLY -- see AGENTS.md's "Terminal input" section). There
// is no new endpoint and no fence change here; an earlier draft spec
// (docs/plans/2026-08-05-mobile-compose-bar-plan.md) proposed a new POST
// .../compose endpoint gated on cookie-vs-Bearer caller class -- that was
// rejected by security review (possession of a cookie is not proof of human
// presence) and none of it was built. See AGENTS.md's "Mobile
// compose bar" note.
//
// Consequence, deliberately not hidden from the user: on a default install
// this 403s, because input_enabled defaults to false and
// input_allowed_sessions defaults to []. _composeRenderEnabledState() below
// reads the ALREADY-LOADED _serverSettings.input_enabled (GET /api/settings
// does not redact these two keys -- only PATCH fences them, per
// settings.LOCAL_ONLY_KEYS) and disables the input+send controls with a
// persistent, host-editing-specific explanation INSTEAD OF letting the user
// discover the 403 by pressing Send. If input_enabled flips true but this
// specific session isn't allow-listed, the real 403 from /input still
// happens on Send -- that one is surfaced inline via _composeErrorMessage()
// (see the 403 branch), never silently.
//
// Render-when-disabled decision: the bar (and its header toggle) render
// ALWAYS when the user's preference is effectively on -- never hidden
// outright -- but with its controls actually `disabled` (not merely
// styled to look disabled) whenever input_enabled is false, so there is no
// clickable control that could ever produce a silent or surprising 403 for
// that case. This keeps the feature discoverable (a user on a fresh
// install sees the bar and the exact reason it's inert, naming the two
// settings keys and that they live in settings.json on the host) without
// presenting a button that looks live but always fails.
//
// muxplex-fx1: preference now persists through settings.py's
// composeBarOpen -- the SAME sidebarOpen/agentPanelOpen mechanism
// (GET/PATCH /api/settings via patchServerSetting), not a second,
// parallel localStorage scheme. "make sure the toggle state ... [is]
// remembered across visits ... follow [the sidebarOpen/agentPanelOpen
// pattern] exactly -- do not invent a second persistence mechanism."
//
// Tri-state on ONE persisted value (null | true | false), same shape as
// sidebarOpen:
//   null  -- never explicitly toggled. Resolves to VISIBLE on every
//            device width (see _composeEffectiveOn). This is a change
//            from the prior 'auto' behaviour (mobile-only default,
//            hidden on desktop) -- the owner's ask was "make the compose
//            bar on by default so that it's more discoverable", which
//            reads as discoverable everywhere, not only on phones.
//   true / false -- an explicit choice. Always wins over the default
//            above, on any device width -- a remembered preference beats
//            a new default, so nobody who deliberately hid the bar gets
//            it silently reopened out from under them.
//
// LEGACY MIGRATION: a prior build stored this per-device in localStorage
// under COMPOSE_PREF_STORAGE_KEY ('auto'|'on'|'off'). initComposePref()
// migrates an explicit legacy 'on'/'off' into the new server setting
// exactly once -- only when the server has never had an explicit value
// of its own -- so that earlier explicit choice survives the mechanism
// change instead of being silently reset to the new on-by-default.
// ('auto' carried no explicit intent and is not migrated.) The old key is
// left in place afterward, unread from now on -- harmless, not worth a
// second write path just to clear it.
const COMPOSE_PREF_STORAGE_KEY = 'muxplex-compose-bar'; // legacy key, migration-only -- see initComposePref
let _composeSendInFlight = false;

/**
 * Resolve the effective on/off state from the loaded server setting.
 * `_serverSettings.composeBarOpen` missing/null ("never toggled") resolves
 * to true (on by default, every width). An explicit true/false always wins.
 * @returns {boolean}
 */
function _composeEffectiveOn() {
  var stored = _serverSettings ? _serverSettings.composeBarOpen : null;
  if (stored === null || stored === undefined) return true;
  return !!stored;
}

/**
 * One-time migration + initial resolve+persist, mirroring initSidebar()'s
 * own "read the setting; if it was never set, resolve a default and
 * persist it" shape. Must run AFTER loadServerSettings() has populated
 * _serverSettings (see the DOMContentLoaded handler below) -- unlike
 * sidebarOpen (re-derived at session-open time, well after load),
 * composeBarOpen's toggle button is rendered once at page load, so this
 * cannot defer the same way.
 */
function initComposePref() {
  var stored = _serverSettings ? _serverSettings.composeBarOpen : null;
  if (stored !== null && stored !== undefined) return; // already explicit -- nothing to resolve

  // Never explicitly set on the server. Check for a pre-migration,
  // per-device localStorage choice before falling back to the new
  // on-by-default -- see the block comment above.
  var legacy = null;
  try {
    var raw = localStorage.getItem(COMPOSE_PREF_STORAGE_KEY);
    if (raw === 'on' || raw === 'off') legacy = raw === 'on';
  } catch (_) {
    // localStorage blocked -- nothing to migrate, fall through to the default
  }

  var resolved = legacy !== null ? legacy : true;
  if (_serverSettings) _serverSettings.composeBarOpen = resolved;
  patchServerSetting('composeBarOpen', resolved);
}

/**
 * Set and persist the compose preference, then re-render every dependent
 * widget.
 * @param {boolean} on
 */
function _composeSetPref(on) {
  if (_serverSettings) _serverSettings.composeBarOpen = on;
  patchServerSetting('composeBarOpen', on);
  _composeRenderToggle();
  _composeRender();
}

/** Flip the effective on/off state and persist the explicit choice. */
function _composeToggle() {
  _composeSetPref(!_composeEffectiveOn());
}

/**
 * Keep the header toggle button's visual/ARIA state in sync with the
 * effective preference -- same pattern as renderSyncGroupControls().
 */
function _composeRenderToggle() {
  var btn = $('compose-toggle-btn');
  if (!btn) return;
  var on = _composeEffectiveOn();
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  btn.classList.toggle('header-btn--active', on);
  btn.title = on ? 'Hide compose bar' : 'Show compose bar';
}

/**
 * Show/hide #compose-bar for the currently-viewed session and (when shown)
 * refresh its enabled/disabled state from _serverSettings. A no-op-safe
 * hide when no session is open or the effective preference is off.
 */
function _composeRender() {
  var bar = $('compose-bar');
  if (!bar) return;
  if (!_viewingSession || !_composeEffectiveOn()) {
    bar.classList.add('hidden');
    // Refit even on hide -- the terminal just reclaimed the compose bar's
    // share of the flex column and should refit to fill it immediately
    // rather than waiting on the ResizeObserver's 50ms debounce.
    if (window._refitTerminal) window._refitTerminal();
    return;
  }
  bar.classList.remove('hidden');
  _composeRenderEnabledState();
  if (window._refitTerminal) window._refitTerminal();
}

/**
 * Reflect settings.input_enabled (read from the already-loaded
 * _serverSettings -- GET /api/settings does not redact this key, only
 * PATCH fences it) into the bar's disabled state and notice text. Does
 * NOT attempt to evaluate settings.input_allowed_sessions client-side --
 * that fence uses casefold+fnmatch glob matching
 * (terminal_input.session_matches_allowlist), and re-deriving it here
 * would duplicate a fence the codebase deliberately keeps in exactly one
 * place (AGENTS.md's "Auto-updating views" note makes the same call for a
 * different pair of matchers). A session-specific allowlist mismatch is
 * instead surfaced honestly, per real 403, by _composeErrorMessage() below.
 */
function _composeRenderEnabledState() {
  var bar = $('compose-bar');
  var input = $('compose-input');
  var sendBtn = $('compose-send-btn');
  var queueBtn = $('compose-queue-btn');
  var notice = $('compose-notice');
  if (!bar) return;
  var enabled = !!(_serverSettings && _serverSettings.input_enabled === true);
  bar.classList.toggle('compose-bar--disabled', !enabled);
  if (input) input.disabled = !enabled;
  if (sendBtn) sendBtn.disabled = !enabled || _composeSendInFlight;
  if (queueBtn) {
    // Follow-ups run only on the host that owns the session (spec §8) --
    // never offered for a remote-viewed session.
    queueBtn.disabled = !enabled || !!_viewingRemoteId;
    queueBtn.title = _viewingRemoteId
      ? 'Follow-ups run on the host that owns the session'
      : 'Add to follow-ups (Ctrl+Shift+Enter)';
  }
  if (notice) notice.classList.toggle('hidden', enabled);
  _sttRenderButton();
}

/** Hide the inline error (role=alert) box. */
function _composeHideError() {
  var err = $('compose-error');
  if (!err) return;
  err.textContent = '';
  err.classList.add('hidden');
}

/**
 * Show the inline error box. Never auto-hides (unlike showToast(), which
 * self-hides after 3000ms) -- a user watching the keyboard or mid-dictation
 * would miss a toast; this persists until the next successful send, the
 * next attempt, or a session switch.
 * @param {string} msg
 */
function _composeShowError(msg) {
  var err = $('compose-error');
  if (!err) return;
  err.textContent = msg;
  err.classList.remove('hidden');
}

/**
 * Clear the draft and any error, and reset in-flight bookkeeping. Called
 * on session open (a NEW session never inherits a previous one's draft --
 * see docs/plans/2026-08-05-mobile-compose-bar-plan.md §7.6, deliberately
 * in-memory only, never localStorage) and on session close.
 */
function _composeClearDraft() {
  var input = $('compose-input');
  if (input) {
    input.value = '';
    input.style.height = '';
  }
  _composeHideError();
  _composeSendInFlight = false;
  // A live dictation session belongs to the session/draft being cleared --
  // see _sttForceStop()'s docstring for why this is abort(), not stop().
  _sttForceStop();
  // A pending cloud-consent gate belongs to the same draft: leaving it
  // showing across a session switch would let a click meant for the NEW
  // session silently grant cloud consent triggered by the OLD one.
  _sttHideCloudConsent();
}

/**
 * Called from openSession(): clear any stale draft, render for the new
 * session, and refresh the follow-up panel from the server.
 *
 * The `_followupsRefresh()` call is load-bearing, not decorative: before
 * this fix, `_followupsRefresh()` was only ever called from inside the
 * follow-up mutation handlers (queue/reorder/edit/remove/clear/resume) --
 * never on the session-open path. Switching sessions left `_followupsData`
 * holding the PREVIOUS session's queue while `_viewingSession` already
 * pointed at the new one, so the panel showed stale, wrong-session data
 * until the user happened to trigger some mutation. See `_followupsPut()`'s
 * `_followupsData.session` guard for the second half of this fix -- it
 * refuses to let a stale snapshot be written back over the WRONG session.
 */
function _composeOnSessionOpen() {
  _composeClearDraft();
  _composeRender();
  _followupsRefresh();
}

/**
 * Called from closeSession(): clear the draft and hide the bar.
 * closeSession() sets `_viewingSession = null` BEFORE calling this, so
 * _composeRender()'s own "no session open" branch is what hides it and
 * triggers the refit -- one hide path, not two. `_followupsRefresh()`
 * mirrors that: called with `_viewingSession` already null, its own
 * no-session branch clears `_followupsData` and hides the panel -- the
 * same one-path pattern, applied to the follow-up panel instead of the bar.
 */
function _composeOnSessionClose() {
  _composeClearDraft();
  _composeRender();
  _followupsRefresh();
}

/**
 * Normalize a textarea's raw value before sending: CRLF/CR -> LF (a
 * dictation engine or a pasted source may use either), then strip
 * trailing newlines (the newline the user typed to reach a fresh line
 * before pressing Send is not part of the message -- it's how they got
 * there). Example: "a\r\nb\n\n" -> "a\nb".
 * @param {string} raw
 * @returns {string}
 */
function _composeNormalizeText(raw) {
  return raw.replace(/\r\n|\r/g, '\n').replace(/\n+$/, '');
}

/**
 * Auto-grow a textarea to fit its content, capped by CSS max-height
 * (style.css's .compose-bar__input; overflow-y:auto takes over past the
 * cap, giving internal scroll rather than unbounded growth).
 * @param {HTMLTextAreaElement} el
 */
function _composeAutoGrow(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
  if (window._refitTerminal) window._refitTerminal();
}

/**
 * Keydown handler for #compose-input. A bare Enter is left to the
 * browser's own textarea default (inserts a newline) -- composing before
 * sending is the entire point of this feature. Ctrl+Enter or Cmd+Enter is
 * the separate send action; preventDefault() is load-bearing here for the
 * same reason terminal.js's Shift+Enter branch needs it (see AGENTS.md's
 * "attachCustomKeyEventHandler" note) -- without it, Enter would still
 * insert a newline in the textarea in addition to triggering send.
 *
 * The QUEUE shortcut (Ctrl/Cmd+Shift+Enter) is deliberately NOT handled
 * here -- see `_followupsQueueKeydown()` below, bound at `document` instead
 * of this element. A local-only binding meant the shortcut only worked
 * when #compose-input itself had focus; in practice the terminal has focus
 * almost all the time, and terminal.js's own xterm key handler was ALSO
 * bound to Ctrl/Shift+Enter (for its unrelated CSI-u passthrough feature),
 * so the terminal silently swallowed the identical chord before it ever
 * reached this element -- the queue action fired for nobody watching the
 * terminal. Root-caused via a real Playwright browser session: with the
 * terminal focused, Ctrl+Shift+Enter produced zero `/followups` requests
 * and the typed text landed in the terminal, not the compose draft.
 * @param {KeyboardEvent} e
 */
function _composeKeydown(e) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
    e.preventDefault();
    _composeSend();
  }
}

/**
 * Document-level keydown listener for the follow-up queue shortcut
 * (Ctrl/Cmd+Shift+Enter, spec §9.2) -- bound at `document` (not
 * #compose-input) so it fires regardless of which element currently has
 * focus. This is the fix for the queue shortcut being silently swallowed
 * by the terminal (see _composeKeydown()'s docstring and terminal.js's
 * matching carve-out for this exact chord): terminal.js now excludes
 * Ctrl+Shift+Enter from its own handling and calls preventDefault() without
 * stopPropagation(), so the native keydown event still bubbles here even
 * when the terminal itself had focus when the chord was pressed.
 *
 * Gating mirrors `_composeRenderEnabledState()`'s own queue-button
 * enablement exactly (session open, not a remote view, input_enabled
 * true) so the shortcut and the button always agree on when queuing is
 * possible -- one source of truth for "can this session queue right now",
 * read from the same `_serverSettings`/`_viewingSession`/`_viewingRemoteId`
 * state the button's render already uses.
 * @param {KeyboardEvent} e
 */
function _followupsQueueKeydown(e) {
  if (e.key !== 'Enter' || !(e.ctrlKey || e.metaKey) || !e.shiftKey || e.altKey) return;
  if (!_viewingSession || _viewingRemoteId) return; // no session open, or a remote view
  var enabled = !!(_serverSettings && _serverSettings.input_enabled === true);
  if (!enabled) return; // matches compose-queue-btn's own disabled state
  e.preventDefault();
  _followupsQueueDraft();
}

/**
 * Map a failed POST /api/sessions/{name}/input into an honest, specific,
 * inline message. Every branch returns something specific -- there is no
 * silent-failure path through this function or its caller (_composeSend()'s
 * catch always calls this and always renders the result).
 * @param {Error & {status?: number, body?: any}} err
 * @returns {string}
 */
function _composeErrorMessage(err) {
  if (!err || err.status == null) {
    // fetch() itself rejected (offline, DNS, TLS, CORS) -- no HTTP response at all.
    return "Couldn't reach the server. Your text is still here.";
  }
  var detail = (err.body && err.body.detail) || '';
  switch (err.status) {
    case 403:
      if (detail.indexOf('input_enabled') !== -1) {
        return 'Session input is disabled on this server (input_enabled is false). ' +
          'An operator can turn it on by editing input_enabled and input_allowed_sessions ' +
          'in ~/.config/muxplex/settings.json on the host -- not from this UI.';
      }
      return 'This session is not on the server\u2019s input_allowed_sessions list. ' +
        'An operator can add it by editing input_allowed_sessions in ' +
        '~/.config/muxplex/settings.json on the host -- not from this UI.';
    case 404:
      return 'Session \u2018' + _viewingSession + '\u2019 no longer exists.';
    case 413:
      return 'Too long \u2014 the limit is 8 KiB.';
    case 400:
      return detail || 'Invalid request.';
    case 409:
      // Queue-specific 409s (POST/PUT .../followups) -- /input never returns
      // 409, so these two branches only ever fire from the follow-up queue.
      if (err.body && err.body.bell_hook_unarmed) {
        return 'Follow-ups are refused right now: the bell hook is not armed, ' +
          'so a queued item would never fire. ' + (err.body.detail || '');
      }
      if (err.body && err.body.queue_full) {
        return 'Follow-up queue is full (max ' + (err.body.max || 16) + ' items).';
      }
      return detail || 'Conflict (HTTP 409).';
    case 500:
      return 'The server couldn\u2019t send that: ' + detail;
    default:
      return 'Send failed (HTTP ' + err.status + ').';
  }
}

/**
 * Send the current draft via POST /api/sessions/{name}/input -- the same
 * unmodified, fenced endpoint every other caller uses (see the section
 * banner above). Exactly one request in flight at a time (the send button
 * is disabled for the duration; a second Ctrl+Enter while pending is a
 * no-op). The draft is cleared ONLY on a 200 response -- a user who just
 * dictated a paragraph must never lose it to a 403.
 */
async function _composeSend() {
  if (_composeSendInFlight) return;
  var input = $('compose-input');
  if (!input) return;
  var normalized = _composeNormalizeText(input.value);
  if (!normalized.trim()) {
    _composeShowError('Nothing to send.');
    return;
  }
  if (!_viewingSession) {
    _composeShowError('No session is open.');
    return;
  }

  _composeSendInFlight = true;
  var sendBtn = $('compose-send-btn');
  if (sendBtn) sendBtn.disabled = true;
  input.setAttribute('aria-busy', 'true');

  try {
    await api(
      'POST',
      withDevice('/api/sessions/' + encodeURIComponent(_viewingSession) + '/input'),
      { text: normalized, enter: true },
    );
    input.value = '';
    input.style.height = '';
    _composeHideError();
    if (window._refitTerminal) window._refitTerminal();
    input.focus();
  } catch (err) {
    _composeShowError(_composeErrorMessage(err));
  } finally {
    _composeSendInFlight = false;
    input.removeAttribute('aria-busy');
    if (sendBtn) sendBtn.disabled = !(_serverSettings && _serverSettings.input_enabled === true);
  }
}

/**
 * Wire the compose bar's static DOM elements. Called once, from the app's
 * main static-event-listener binder (see its own call site further down).
 * NOTE: deliberately does not spell that function's name in this comment --
 * test_frontend_js.py's test_flyout_delegated_on_tile_container locates
 * that function's body via a literal source.split() on its name, and a
 * comment repeating the name earlier in the file would shift the split
 * point (see AGENTS.md's "source-text tripwire" note).
 */
function _bindComposeEventListeners() {
  on($('compose-toggle-btn'), 'click', _composeToggle);
  on($('compose-send-btn'), 'click', _composeSend);
  on($('compose-queue-btn'), 'click', _followupsQueueDraft);
  on($('followups-clear-btn'), 'click', _followupsClearAll);
  on($('followups-resume-btn'), 'click', _followupsResume);
  on($('followups-remove-halted-btn'), 'click', _followupsRemoveHalted);
  var input = $('compose-input');
  if (input) {
    input.addEventListener('keydown', _composeKeydown);
    input.addEventListener('input', function() { _composeAutoGrow(input); });
  }
  // Queue shortcut is document-level, not local to #compose-input -- see
  // _followupsQueueKeydown()'s docstring for why (the terminal has focus
  // almost all the time; a local-only binding meant the shortcut only ever
  // fired for the rare case where the user had already clicked into the
  // compose textarea first).
  document.addEventListener('keydown', _followupsQueueKeydown);
  on($('compose-mic-btn'), 'click', _sttHandleClick);
  on($('compose-cloud-consent-allow-btn'), 'click', _sttCloudConsentAllow);
  on($('compose-cloud-consent-cancel-btn'), 'click', _sttCloudConsentCancel);
}

// ─── On-device dictation (STT) ──────────────────────────────────────────
//
// Click-to-dictate into #compose-input using the Web Speech API
// (Chrome/Edge 139+, desktop only). Gate: on-device preferred, cloud by
// explicit opt-in.
//
// This spike originally shipped fail-closed-to-nothing: the button only
// ever appeared for on-device (processLocally:true) recognition, with no
// cloud path at all, because that felt like the safe/private default. It
// wasn't -- on real hardware (macOS Edge 151, Windows Edge 150) on-device
// recognition is unavailable and cloud is the only thing either machine
// ever reports, so the original gate could never open where its owner
// actually works. A gate that can only ever stay closed isn't a safety
// property, it's a dead feature. See the PR/report for the full reasoning;
// the short version: "no silent fallback" is about a user believing X is
// happening while Y actually is. A disclosed, explicitly-opted-into cloud
// mode is not that -- it's a choice, made in the open, exactly once per
// device, the same way this owner already chooses to run cloud LLM agents
// in his terminal sessions.
//
// _sttCheckAvailability() tries on-device (processLocally:true) FIRST; only
// if that's unavailable does it try cloud (processLocally:false). Whichever
// wins becomes `_sttMode` ('ondevice' | 'cloud') for the rest of this
// button's lifetime until the next full `_sttInit()` (i.e. the next page
// load) -- the mode is never re-decided or silently upgraded mid-session.
// If on-device recognition degrades after the check (e.g. the model is
// evicted -- surfaces as the 'language-not-supported' error), the gate
// closes entirely (`_sttStatus`/`_sttMode` both reset to null); it does
// NOT fall through to cloud. That would be exactly the silent-substitution
// failure mode the "no fallback" rule actually guards against.
//
// #compose-mic-btn (static markup in index.html, starts `hidden`) is
// un-hidden whenever EITHER path resolves 'available'/'downloadable'. Every
// other case -- no SpeechRecognition at all, a pre-139 Chrome with no
// `.available` signal, Safari, Firefox, or Chrome-Android (neither
// processLocally value is meaningful on mobile Chromium; the OS keyboard's
// own dictation is better and free there anyway, so this single capability
// gate still excludes every mobile browser with no separate width/
// user-agent sniffing needed) -- leaves the button hidden: it does not
// exist, exactly as before.
//
// On-device dictation starts the instant the button is clicked -- silent,
// no prompt, unchanged from the original design. Cloud dictation shows
// #compose-cloud-consent (static markup, starts `hidden`) on its FIRST use
// per device, naming plainly where the audio goes; only after the user
// clicks "Use cloud dictation" does recognition.start() ever run with
// `processLocally: false`. The choice is then remembered in localStorage
// (STT_CLOUD_CONSENT_STORAGE_KEY) -- per-device, like
// COMPOSE_PREF_STORAGE_KEY above, deliberately never a federated/server
// setting.
//
// The active mode is visible at a glance whenever the mic is live:
// `_sttRenderButton()` applies a distinct `.compose-bar__mic--cloud`
// modifier (present any time `_sttMode === 'cloud'`, not just while
// listening) and a mode-specific title/aria-label, and the LISTENING pulse
// itself renders in a different color for cloud than for on-device (see
// style.css) -- a live mic already picks up whoever else is in the room or
// a call in the background, so cloud specifically (audio actually leaving
// the device) gets the more conspicuous treatment. A true push-to-talk
// (hold-to-record) interaction would sharpen this further -- a deliberate,
// separately-justified redesign of the whole click-to-toggle model, not
// something this change bundles in.
//
// Click-only. No keyboard shortcut in v1: terminal.js's own
// attachCustomKeyEventHandler already owns several Ctrl/Shift+Enter chords
// (AGENTS.md's "attachCustomKeyEventHandler" note; see also this file's own
// _followupsQueueKeydown() carve-out for the queue shortcut above), and the
// terminal holds focus almost all the time. A dictation shortcut is a
// separately-justified v2 cost, not a v1 default.
//
// Interim results are shown live, replaced in place as they firm up, so a
// user speaking into the field can see it working; a FINAL result is
// committed permanently and the insertion point advances past it, so
// continued speech never overwrites already-committed text (see
// _sttApplyTranscript()).
//
// Two documented Chrome traps, both deliberately NOT worked around by
// auto-restarting: (1) a continuous session is ended by the browser after
// about a minute of silence -- whether that surfaces as an 'error' event, a
// bare 'end', or both, this code never calls .start() again from
// onend/onerror, only an explicit click does (_sttHandleClick -> _sttStart).
// (2) auto-restarting on 'end'/'no-speech' gets the origin RATE-LIMITED by
// the browser -- a naive restart loop is exactly the bug this avoids. Every
// stop, explicit or not, surfaces an honest, specific, inline reason via
// #compose-error -- never silent (_sttHandleError / _sttHandleEnd).
//
// NOT PROVEN BY THIS SPIKE'S OWN TEST SUITE: that real speech becomes real
// text. There is no microphone and no Chromium 139+ available to drive in
// this environment. The tests below prove the gating logic, the DOM
// wiring, the transcript-insertion algorithm (against synthetic
// SpeechRecognitionEvent-shaped objects), and every error path. The actual
// browser<->on-device-model round trip needs a real device and a human (or
// a browser-driving agent) to verify -- see the report, not implied here.

/** SpeechRecognitionPhrase boost (0.0-10.0) applied to every sourced term. */
const STT_PHRASE_BOOST = 8.0;

/** Cap on how many phrase-biasing terms are sent per session -- keeps the
 * call cheap and stops one huge session list from dominating the boost set. */
const STT_MAX_PHRASES = 24;

/** localStorage key for the per-device cloud-dictation opt-in. Same
 * precedent as COMPOSE_PREF_STORAGE_KEY/SYNC_GROUP_STORAGE_KEY above:
 * deliberately NOT a settings key, NOT federated -- a device that hasn't
 * granted this consent must ask again, even if another of the owner's
 * devices already has. */
const STT_CLOUD_CONSENT_STORAGE_KEY = 'muxplex-stt-cloud-consent';

let _sttStatus = null;              // null | 'available' | 'downloadable' -- from the availability check; null means the button stays hidden
let _sttMode = null;                // null | 'ondevice' | 'cloud' -- which path _sttStatus came from; fixed by _sttInit(), never re-decided mid-session
let _sttLang = null;                // resolved BCP-47 language tag used for both the check and recognition itself
let _sttState = 'idle';             // 'idle' | 'listening' | 'downloading'
let _sttRecognition = null;         // the live SpeechRecognition instance, or null
let _sttInsertPos = 0;              // fixed anchor for the WHOLE recognition session -- set once by _sttStart(), never advanced by result handling (see _sttApplyTranscript())
let _sttInterimLength = 0;          // length of the text currently written at _sttInsertPos for this session (finals + trailing interim preview together, not just the interim tail -- see _sttApplyTranscript())
let _sttUserStopped = false;        // true only across an explicit _sttStop()/_sttForceStop() call
let _sttSuppressEndMessage = false; // true once onerror (or a forced stop) already rendered a specific message
let _sttConsentPending = false;     // true while #compose-cloud-consent is showing, awaiting the user's choice

/**
 * The constructor the current browser exposes, or null. A tiny indirection
 * so tests can stub `window.SpeechRecognition` without a real one existing.
 * @returns {Function|null}
 */
function _sttCtor() {
  return (typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition)) || null;
}

/**
 * Feature-detect dictation availability for the user's language --
 * on-device FIRST, cloud only if on-device isn't there. Resolves null (no
 * button, ever) unless the browser exposes SpeechRecognition AND its
 * static `.available()` reports 'available' or 'downloadable' for EITHER
 * `{processLocally: true}` (checked first -- with processLocally=true and
 * no local model, start() fails with 'language-not-supported' rather than
 * silently using the cloud, so this branch never risks a surprise network
 * call) or `{processLocally: false}` (checked only when the first call
 * comes back neither 'available' nor 'downloadable'). Whichever branch
 * resolves wins outright -- there is no "prefer cloud" or "merge both"
 * path, and the loser is never consulted again for this check.
 * 'downloading' (someone else's install already in flight) and
 * 'unavailable' resolve null for a given branch -- this spike does not
 * poll a third state to completion; see the report's "not proven" list.
 * @returns {Promise<{status: 'available'|'downloadable', lang: string, mode: 'ondevice'|'cloud'}|null>}
 */
async function _sttCheckAvailability() {
  try {
    var SR = _sttCtor();
    if (!SR || typeof SR.available !== 'function') return null; // no Speech API, or a build with no availability signal at all
    var lang = (typeof navigator !== 'undefined' && navigator.language) || 'en-US';
    var onDeviceStatus = await SR.available({ langs: [lang], processLocally: true });
    if (onDeviceStatus === 'available' || onDeviceStatus === 'downloadable') {
      return { status: onDeviceStatus, lang: lang, mode: 'ondevice' };
    }
    var cloudStatus = await SR.available({ langs: [lang], processLocally: false });
    if (cloudStatus === 'available' || cloudStatus === 'downloadable') {
      return { status: cloudStatus, lang: lang, mode: 'cloud' };
    }
    return null;
  } catch (_) {
    return null; // any failure here means "cannot prove dictation works" -- never show the button
  }
}

/**
 * Sourced phrase-biasing terms for SpeechRecognition.phrases (same
 * processLocally-generation feature). Sourced from REAL, currently-loaded
 * data -- live session names (a terminal tool's session names are
 * frequently command/hostname-shaped identifiers a user is likely to say)
 * and this device's own configured hostname (_serverSettings.device_name)
 * -- never a hardcoded word list. Pure function of already-loaded state,
 * so it's cheap to call fresh before every _sttStart().
 * @returns {string[]} de-duplicated, non-empty terms, capped at STT_MAX_PHRASES
 */
function _sttPhraseSourceTerms() {
  var terms = [];
  var seen = {};
  function add(t) {
    if (typeof t !== 'string') return;
    var v = t.trim();
    if (!v || seen[v]) return;
    seen[v] = true;
    terms.push(v);
  }
  (_currentSessions || []).forEach(function(s) { if (s && s.name) add(s.name); });
  if (_serverSettings && _serverSettings.device_name) add(_serverSettings.device_name);
  return terms.slice(0, STT_MAX_PHRASES);
}

/**
 * Build SpeechRecognitionPhrase instances for the current recognition
 * session, or null if the browser doesn't expose the `phrases` /
 * SpeechRecognitionPhrase feature (checked independently -- never assumed
 * present just because processLocally is) or there are no real terms to
 * offer. Failures here are non-fatal: dictation still works without
 * biasing.
 * @returns {Array<object>|null}
 */
function _sttBuildPhrases() {
  try {
    var Ctor = typeof window !== 'undefined' ? window.SpeechRecognitionPhrase : undefined;
    if (typeof Ctor !== 'function') return null;
    var terms = _sttPhraseSourceTerms();
    if (!terms.length) return null;
    return terms.map(function(t) { return new Ctor(t, STT_PHRASE_BOOST); });
  } catch (_) {
    return null;
  }
}

/**
 * Reflect `_sttStatus`/`_sttMode`/`_sttState` onto #compose-mic-btn:
 * visibility (hidden unless a capability was ever detected), disabled
 * state (downloading, or the compose row itself is disabled), the
 * listening/downloading/cloud modifier classes, aria-pressed, and title.
 * `.compose-bar__mic--cloud` is applied whenever `_sttMode === 'cloud'`
 * REGARDLESS of state -- a persistent, at-rest signal that this button
 * goes off-device, not only a warning that appears once listening starts.
 * Called after every state transition and from
 * _composeRenderEnabledState() so it stays in sync with the rest of the
 * compose bar's enabled/disabled logic.
 */
function _sttRenderButton() {
  var btn = $('compose-mic-btn');
  if (!btn) return;
  if (!_sttStatus) {
    btn.classList.add('hidden');
    return;
  }
  btn.classList.remove('hidden');
  var listening = _sttState === 'listening';
  var downloading = _sttState === 'downloading';
  var cloud = _sttMode === 'cloud';
  btn.classList.toggle('compose-bar__mic--listening', listening);
  btn.classList.toggle('compose-bar__mic--downloading', downloading);
  btn.classList.toggle('compose-bar__mic--cloud', cloud);
  btn.setAttribute('aria-pressed', listening ? 'true' : 'false');
  btn.disabled = downloading || !(_serverSettings && _serverSettings.input_enabled === true);
  if (downloading) {
    btn.title = cloud ? 'Downloading\u2026' : 'Downloading on-device speech model\u2026';
  } else if (listening) {
    btn.title = cloud ? 'Stop dictation (cloud \u2014 your voice is leaving this device)' : 'Stop dictation (on-device)';
  } else if (cloud) {
    btn.title = 'Dictate (cloud speech-to-text \u2014 sends audio off this device)';
  } else {
    btn.title = 'Dictate (on-device speech-to-text)';
  }
}

/**
 * Move `_sttState` to `next` and re-render the button.
 * @param {'idle'|'listening'|'downloading'} next
 */
function _sttSetState(next) {
  _sttState = next;
  _sttRenderButton();
}

// --- Cloud-dictation opt-in gate -------------------------------------------
//
// Cloud dictation is never started without a recorded, explicit choice.
// _sttHandleClick() consults _sttCloudConsentGranted() before ever
// constructing a SpeechRecognition with processLocally:false; if consent
// hasn't been granted on this device yet, it shows #compose-cloud-consent
// instead of starting anything. The two functions below are the ONLY path
// that can flip the stored flag to granted -- there is no server-side or
// federated equivalent, matching COMPOSE_PREF_STORAGE_KEY's precedent.

/**
 * Has this device already opted in to cloud dictation? Same defensive
 * localStorage-may-be-blocked shape as initComposePref().
 * @returns {boolean}
 */
function _sttCloudConsentGranted() {
  try {
    return localStorage.getItem(STT_CLOUD_CONSENT_STORAGE_KEY) === 'granted';
  } catch (_) {
    return false; // localStorage blocked -- ask again every time, never assume consent
  }
}

/** Show the cloud-consent gate and re-render it. */
function _sttShowCloudConsent() {
  _sttConsentPending = true;
  _sttRenderConsent();
}

/** Hide the cloud-consent gate (cancel, session switch, or gate re-close). */
function _sttHideCloudConsent() {
  _sttConsentPending = false;
  _sttRenderConsent();
}

/** Reflect `_sttConsentPending` onto #compose-cloud-consent's visibility. */
function _sttRenderConsent() {
  var panel = $('compose-cloud-consent');
  if (!panel) return;
  panel.classList.toggle('hidden', !_sttConsentPending);
}

/**
 * User clicked "Use cloud dictation": persist the opt-in for this device,
 * hide the gate, and proceed exactly as a normal click would have (the
 * downloadable/available branch _sttHandleClick() would have taken had
 * consent already been granted).
 */
function _sttCloudConsentAllow() {
  try { localStorage.setItem(STT_CLOUD_CONSENT_STORAGE_KEY, 'granted'); } catch (_) { /* blocked -- ok, this session still proceeds */ }
  _sttHideCloudConsent();
  _sttProceedToStart();
}

/**
 * User clicked "Not now": hide the gate, stay idle. Not treated as a
 * dictation failure -- no error message, since declining is an ordinary,
 * expected choice, not something gone wrong.
 */
function _sttCloudConsentCancel() {
  _sttHideCloudConsent();
}

/**
 * The actual start branch (download-then-start for 'downloadable', start
 * for 'available') -- factored out of _sttHandleClick() so the consent
 * gate's "Use cloud dictation" button can resume exactly where the click
 * left off, without duplicating the branching logic.
 */
function _sttProceedToStart() {
  if (_sttStatus === 'downloadable') { _sttInstallThenStart(); return; }
  if (_sttStatus === 'available') { _sttStart(); return; }
}

/**
 * Apply a SpeechRecognitionEvent's ENTIRE `results` list into #compose-input
 * at the session's fixed insertion anchor (`_sttInsertPos`, set once by
 * _sttStart() and never advanced here). Every call REBUILDS the session's
 * whole dictated region from scratch -- concatenating every finalized
 * result's transcript (space-joined), then any still-interim transcript
 * after it -- and replaces whatever this session had previously written at
 * that anchor (tracked by `_sttInterimLength`, which despite the name now
 * tracks the length of the ENTIRE current-session region: committed text
 * and interim preview together, not just the interim tail). A single
 * trailing space is added when the region ends on committed (non-interim)
 * text, so the next word/result -- or anything the user types after
 * stopping -- doesn't run on.
 *
 * Rebuilding from the full list on every event (rather than applying only
 * what looks "new") is what makes this idempotent: the result is a pure
 * function of the anchor plus whatever `results` currently says, never of
 * what a PRIOR call did. That holds regardless of how the engine re-delivers
 * results:
 *   - A spec-compliant engine's `results` list is genuinely cumulative --
 *     every previously finalized entry stays in the list forever, and new
 *     entries are appended after it. Rebuilding just re-concatenates the
 *     same finals plus whatever's new: identical output to committing
 *     incrementally, and replaying the same event twice is a no-op.
 *   - Android Chrome's cloud engine (the confirmed field bug -- see
 *     _sttHandleResult()'s docstring) instead re-delivers a single,
 *     still-growing entry marked `isFinal` on every 'result' event: "what",
 *     then "what needs", then "what needs to", etc. An incremental
 *     "commit-and-advance" implementation appends each of these after the
 *     last, producing the ladder of every intermediate utterance state.
 *     Rebuilding instead REPLACES the same region with the new, longer
 *     transcript every time, so only the final, complete sentence survives.
 *
 * The caret is left at the end of whatever was just written, so the
 * textarea visibly tracks along with speech -- the same way native OS
 * dictation looks while it's working.
 * @param {HTMLTextAreaElement} input
 * @param {SpeechRecognitionResultList|Array} results
 */
function _sttApplyTranscript(input, results) {
  var finals = [];
  var interims = [];
  for (var i = 0; i < results.length; i++) {
    var result = results[i];
    var alt = result && result[0];
    var transcript = alt ? String(alt.transcript || '') : '';
    if (result && result.isFinal) {
      var committed = transcript.replace(/\s+$/, '');
      if (committed) finals.push(committed);
    } else if (transcript) {
      interims.push(transcript);
    }
  }
  var combined = finals.join(' ');
  if (combined && interims.length) combined += ' ';
  combined += interims.join(' ');
  if (finals.length && !interims.length && combined) combined += ' ';

  var value = input.value;
  var before = value.slice(0, _sttInsertPos);
  var after = value.slice(_sttInsertPos + _sttInterimLength);
  input.value = before + combined + after;
  _sttInterimLength = combined.length;

  var caret = _sttInsertPos + _sttInterimLength;
  if (typeof input.setSelectionRange === 'function') input.setSelectionRange(caret, caret);
}

/**
 * SpeechRecognition 'result' handler: rebuilds the entire dictated region
 * from `event.results` on every event (see _sttApplyTranscript()) and
 * auto-grows the textarea the same way manual typing does.
 *
 * Deliberately does NOT use `event.resultIndex` to slice to "just the new
 * results" -- that incremental approach was this handler's ORIGINAL design,
 * on the assumption (once documented right here) that "a spec-compliant
 * implementation never re-sends an already-committed prior result". That
 * assumption is false in the field: dictating "what needs to be worked on
 * next" on Android Chrome (cloud mode) delivered a 'result' event per
 * intermediate utterance state -- "what", "what needs", "what needs to",
 * ... "what needs to be worked on next" -- EACH marked `isFinal`. Committing
 * and advancing past each one in turn appended every intermediate state
 * after the last, landing "what what needs what needs to ... what needs to
 * be worked on next" in the compose bar instead of the one sentence the
 * owner actually said. Treating `event.results` as the complete, current
 * authoritative state of the session -- and always rebuilding the whole
 * region from it -- fixes this without any Android-specific branch, and
 * without changing behavior for an engine that never re-delivers results.
 * @param {SpeechRecognitionEvent} event
 */
function _sttHandleResult(event) {
  var input = $('compose-input');
  if (!input || !event || !event.results) return;
  _sttApplyTranscript(input, event.results);
  _composeAutoGrow(input);
}

/**
 * Map a SpeechRecognitionErrorEvent's `.error` code to an honest, specific,
 * inline message -- dictation has no silent-failure path either, mirroring
 * _composeErrorMessage()'s discipline for the send path. Sets
 * `_sttSuppressEndMessage` so the 'end' event that always follows an error
 * doesn't ALSO render a second, more generic message for the same failure.
 * @param {SpeechRecognitionErrorEvent} event
 */
function _sttHandleError(event) {
  _sttSuppressEndMessage = true;
  var code = event && event.error;
  switch (code) {
    case 'no-speech':
      _composeShowError('No speech detected \u2014 dictation stopped. Tap the mic to try again.');
      break;
    case 'not-allowed':
    case 'service-not-allowed':
      _composeShowError('Microphone access was blocked. Allow microphone access for this site, then tap the mic to try again.');
      break;
    case 'audio-capture':
      _composeShowError('No microphone was found.');
      break;
    case 'language-not-supported':
      // Hitting this means the mode this session started in (usually
      // on-device) regressed after the check (e.g. the model was
      // evicted). Re-close the gate entirely -- both _sttStatus AND
      // _sttMode -- rather than leaving a button that will only fail
      // again. Never fall through to the OTHER mode, not even implicitly
      // by leaving a broken button visible: that would be exactly the
      // silent mid-session mode switch this design forbids.
      _composeShowError((_sttMode === 'cloud'
        ? 'The cloud speech-recognition service no longer supports this language.'
        : 'The on-device speech model for this language is no longer available.') + ' Dictation has been disabled.');
      _sttStatus = null;
      _sttMode = null;
      _sttHideCloudConsent();
      break;
    case 'aborted':
      _composeShowError('Dictation was interrupted.');
      break;
    case 'network':
      _composeShowError('Dictation error: network. (Unexpected for on-device recognition.)');
      break;
    default:
      _composeShowError('Dictation error: ' + (code || 'unknown') + '.');
  }
}

/**
 * SpeechRecognition 'end' handler: always fires, whether the session ended
 * because the user clicked stop, because of an error, or because Chrome
 * silently closed it after about a minute of continuous silence. This
 * function NEVER calls _sttStart() again -- auto-restarting on
 * 'end'/'no-speech' is a documented trap that gets the origin
 * rate-limited by the browser. Only an explicit click resumes dictation.
 */
function _sttHandleEnd() {
  var wasUserStopped = _sttUserStopped;
  var suppressed = _sttSuppressEndMessage;
  _sttRecognition = null;
  _sttUserStopped = false;
  _sttSuppressEndMessage = false;
  _sttSetState('idle');
  if (!wasUserStopped && !suppressed) {
    _composeShowError('Dictation stopped (Chrome ends listening after about a minute of silence). Tap the mic to resume.');
  }
}

/**
 * Start a new recognition session. Captures the textarea's current cursor
 * position (or its end) as the insertion point so dictated text lands
 * where the user was about to type, not blindly appended.
 */
function _sttStart() {
  if (_sttRecognition || _sttState !== 'idle') return;
  var input = $('compose-input');
  if (!input || input.disabled) return;
  var SR = _sttCtor();
  if (!SR) {
    _composeShowError((_sttMode === 'cloud' ? 'Cloud' : 'On-device') + ' dictation is no longer available in this browser.');
    _sttStatus = null;
    _sttMode = null;
    _sttRenderButton();
    return;
  }

  _sttInsertPos = typeof input.selectionStart === 'number' ? input.selectionStart : input.value.length;
  _sttInterimLength = 0;
  _sttUserStopped = false;
  _sttSuppressEndMessage = false;

  var recognition;
  try {
    recognition = new SR();
    recognition.lang = _sttLang || (typeof navigator !== 'undefined' && navigator.language) || 'en-US';
    // Explicit either way -- never left ambiguous/omitted. `_sttMode` was
    // fixed once by _sttInit() (or, for cloud, confirmed again by the
    // consent gate) and is never re-decided here.
    recognition.processLocally = _sttMode !== 'cloud';
    recognition.continuous = true;
    recognition.interimResults = true;
    var phrases = _sttBuildPhrases();
    if (phrases) {
      try { recognition.phrases = phrases; } catch (_) { /* optional biasing -- ignore if the setter rejects it */ }
    }
    recognition.onresult = _sttHandleResult;
    recognition.onerror = _sttHandleError;
    recognition.onend = _sttHandleEnd;
    recognition.start();
  } catch (e) {
    _sttRecognition = null;
    _sttSetState('idle');
    _composeShowError('Could not start dictation: ' + (e && e.message ? e.message : e));
    return;
  }
  _sttRecognition = recognition;
  _composeHideError();
  _sttSetState('listening');
}

/**
 * User-initiated stop -- graceful (.stop(), not .abort()) so any final
 * result already spoken is still delivered before 'end' fires.
 */
function _sttStop() {
  if (!_sttRecognition) return;
  _sttUserStopped = true;
  try { _sttRecognition.stop(); } catch (_) { /* already stopping/stopped */ }
}

/**
 * Force dictation off without waiting for the browser's own 'end' event --
 * used when the compose bar's target session changes or closes out from
 * under a live recognition session (see _composeClearDraft()). Deliberately
 * abort() rather than stop(): the in-flight utterance belongs to a session
 * the user is leaving, and letting its eventual final result land in a
 * DIFFERENT session's draft (or a hidden bar) would be a silent
 * cross-session leak, not a courtesy. Not treated as a dictation failure --
 * no error message, since switching sessions is an ordinary action.
 */
function _sttForceStop() {
  if (!_sttRecognition) return;
  _sttUserStopped = true;
  _sttSuppressEndMessage = true;
  try { _sttRecognition.abort(); } catch (_) { /* already stopped */ }
}

/**
 * Download flow for a 'downloadable' (not yet 'available') model --
 * on-device or cloud, per `_sttMode` (a cloud model is not expected to
 * ever report 'downloadable' since there is nothing to download, but the
 * install flow is kept symmetric rather than guessed away, so an
 * unexpected 'downloadable' from a cloud check still resolves cleanly
 * instead of falling into an unhandled state). Disables the button for
 * the duration; on success, immediately starts dictation (the download
 * was the only thing blocking it); on failure or rejection, returns to
 * idle with an inline reason.
 */
async function _sttInstallThenStart() {
  var SR = _sttCtor();
  if (!SR || typeof SR.install !== 'function') {
    _sttStatus = null;
    _sttMode = null;
    _sttRenderButton();
    _composeShowError('This browser cannot install the speech model.');
    return;
  }
  _sttSetState('downloading');
  try {
    var ok = await SR.install({ langs: [_sttLang || 'en-US'], processLocally: _sttMode !== 'cloud' });
    if (!ok) {
      _sttSetState('idle');
      _composeShowError('The speech model could not be installed.');
      return;
    }
    _sttStatus = 'available';
    _sttSetState('idle');
    _sttStart();
  } catch (e) {
    _sttSetState('idle');
    _composeShowError('The speech model could not be installed: ' + (e && e.message ? e.message : e));
  }
}

/**
 * Click handler for #compose-mic-btn. On-device dictation starts
 * immediately, same as always. Cloud dictation is gated on
 * `_sttCloudConsentGranted()`: the FIRST time this device tries cloud
 * dictation, this shows #compose-cloud-consent instead of starting
 * anything -- `_sttProceedToStart()` only runs once that gate is passed
 * (here, because it was already granted on a prior use; or later, from
 * `_sttCloudConsentAllow()`, if the user grants it right now).
 */
function _sttHandleClick() {
  if (_sttState === 'downloading') return; // already in flight -- button is `disabled` too, this is belt-and-suspenders
  if (_sttState === 'listening') { _sttStop(); return; }
  if (_sttConsentPending) return; // gate is already showing -- its own buttons drive the next step
  if (_sttStatus !== 'downloadable' && _sttStatus !== 'available') {
    _composeShowError('Dictation is not available.'); // should be unreachable -- the button is hidden otherwise
    return;
  }
  if (_sttMode === 'cloud' && !_sttCloudConsentGranted()) {
    _sttShowCloudConsent();
    return;
  }
  _sttProceedToStart();
}

/**
 * Run once at startup (called from the DOMContentLoaded handler, after
 * initComposePref()). Resolves the availability check and shows/hides the
 * mic button accordingly, fixing `_sttMode` for the rest of this page
 * load -- see this section's banner for the full "on-device preferred,
 * cloud by explicit opt-in, never re-decided mid-session" rationale.
 * Never throws: any detection failure leaves the button hidden, matching
 * "no button" being the safe default.
 */
async function _sttInit() {
  var result = null;
  try {
    result = await _sttCheckAvailability();
  } catch (_) {
    result = null;
  }
  if (result) {
    _sttStatus = result.status;
    _sttMode = result.mode;
    _sttLang = result.lang;
  } else {
    _sttStatus = null;
    _sttMode = null;
    _sttLang = null;
  }
  _sttRenderButton();
}

// ─── Follow-up queue ────────────────────────────────────────────────────
//
// A per-session, server-side, SHARED list of pending text items -- see
// docs/plans/2026-08-05-per-session-followup-queue-plan.md. Items fire one at a time, each when that
// session's bell rings, until the list drains. Unlike the compose bar's
// send-now action (which stays byte-identical), queuing arms an
// UNATTENDED write -- deliberately a second, explicit button/shortcut,
// never a toggled mode (spec §9.2).
//
// State is fetched fresh on session open and after every mutating action
// -- there is no client-side prediction of server state (a queue this
// small, at most MAX_FOLLOWUPS=16 items, costs nothing to re-fetch).

let _followupsData = null; // last GET .../followups response for _viewingSession, or null

/**
 * Fetch and render the current session's follow-up queue. No-op (clears
 * the panel) when no session is open or when viewing a remote session --
 * follow-ups run on the host that owns the session (spec §8), so the
 * queue affordance is absent, not present-and-failing, for a remote view.
 */
async function _followupsRefresh() {
  if (!_viewingSession || _viewingRemoteId) {
    _followupsData = null;
    _followupsRender();
    return;
  }
  try {
    const res = await api('GET', '/api/sessions/' + encodeURIComponent(_viewingSession) + '/followups');
    _followupsData = await res.json();
  } catch (err) {
    console.warn('[_followupsRefresh] failed:', err);
    _followupsData = null;
  }
  _followupsRender();
}

/**
 * Render #followups-panel from `_followupsData`. Hidden entirely when
 * there is nothing to show (no items and no halt) -- an empty queue and
 * an absent queue look identical to the user, matching the server's own
 * "absence means no queue" convention.
 */
function _followupsRender() {
  var panel = $('followups-panel');
  if (!panel) return;
  var data = _followupsData;
  var hasContent = !!(data && (data.items.length > 0 || data.halted));
  panel.classList.toggle('hidden', !hasContent);
  if (!hasContent) return;

  var header = $('followups-header');
  if (header) {
    var target = data.target_window ? ('will type into ' + data.target_window) : 'target window unknown';
    header.textContent = 'Follow-ups \u00b7 shared with every device \u00b7 ' + target;
  }

  var banner = $('followups-halt-banner');
  var haltText = $('followups-halt-text');
  if (banner) banner.classList.toggle('hidden', !data.halted);
  if (data.halted && haltText) {
    haltText.textContent = 'Halted: ' + (data.halted.detail || data.halted.reason);
  }

  var list = $('followups-list');
  if (list) {
    list.innerHTML = data.items.map(function(item, idx) {
      var isHaltedItem = data.halted && data.halted.item_id === item.id;
      return '<li class="followups-panel__item' + (isHaltedItem ? ' followups-panel__item--halted' : '') + '" data-id="' + escapeHtml(item.id) + '">' +
        '<span class="followups-panel__ordinal">' + (idx + 1) + '</span>' +
        '<span class="followups-panel__text">' + escapeHtml(item.text) + '</span>' +
        '<button class="followups-panel__up" type="button" data-action="up" aria-label="Move up">\u2191</button>' +
        '<button class="followups-panel__down" type="button" data-action="down" aria-label="Move down">\u2193</button>' +
        '<button class="followups-panel__edit" type="button" data-action="edit" aria-label="Edit">\u270e</button>' +
        '<button class="followups-panel__remove" type="button" data-action="remove" aria-label="Remove">\u2715</button>' +
        '</li>';
    }).join('');
    list.querySelectorAll('button[data-action]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var id = btn.closest('li').dataset.id;
        var action = btn.dataset.action;
        if (action === 'up') _followupsReorder(id, -1);
        else if (action === 'down') _followupsReorder(id, 1);
        else if (action === 'edit') _followupsEditItem(id);
        else if (action === 'remove') _followupsRemoveItem(id);
      });
    });
  }
}

/**
 * PUT the whole item list with the current revision precondition -- the
 * single write path every mutating queue action (reorder/edit/remove)
 * funnels through (spec §7.1: the precondition is REQUIRED here, unlike
 * PATCH /api/settings' optional one, because the queue mutates itself).
 * On a 409 revision mismatch: re-fetch, then surface server truth (a
 * single silent retry for send_in_flight, matching patchSettingsGuarded's
 * pattern -- spec §9.3).
 *
 * Structural guard against cross-session contamination: `_followupsData`
 * must have been fetched FOR the session we're about to write to. Without
 * this, a stale `_followupsData` left over from a previous session (see
 * `_composeOnSessionOpen()`'s refresh-on-switch fix above) could be
 * PUT onto a DIFFERENT session's queue the instant the user reorders/edits/
 * removes an item before the panel catches up -- silently overwriting
 * session B's real queue with session A's stale snapshot. `revision` alone
 * does not catch this: revision is only unique WITHIN one session's queue,
 * so a coincidentally-matching revision on the wrong session would sail
 * straight through the `expected_revision` precondition on the server.
 * Every GET/PUT response already carries `session` (the server's own
 * source of truth for which queue a snapshot belongs to) -- this reuses
 * that field rather than inventing new client-side state to track it.
 */
async function _followupsPut(items, opts) {
  opts = opts || {};
  if (!_viewingSession || !_followupsData) return;
  if (_followupsData.session !== _viewingSession) {
    // Refuse rather than silently writing the wrong session's items --
    // re-fetch so the panel (and any retry) reflects the CURRENT session.
    console.warn(
      '[_followupsPut] refused: snapshot is for session ' +
      JSON.stringify(_followupsData.session) + ' but viewing ' +
      JSON.stringify(_viewingSession),
    );
    await _followupsRefresh();
    return;
  }
  try {
    const res = await api(
      'PUT',
      '/api/sessions/' + encodeURIComponent(_viewingSession) + '/followups',
      { expected_revision: _followupsData.revision, items: items },
    );
    _followupsData = await res.json();
    _followupsRender();
  } catch (err) {
    if (err && err.status === 409 && !opts.retried) {
      await _followupsRefresh();
      if (err.body && err.body.send_in_flight) {
        // Single short retry, then surface server truth either way.
        await new Promise(function(r) { setTimeout(r, 300); });
        return _followupsPut(items, { retried: true });
      }
      return;
    }
    console.warn('[_followupsPut] failed:', err);
  }
}

function _followupsReorder(id, delta) {
  if (!_followupsData) return;
  var items = _followupsData.items.slice();
  var idx = items.findIndex(function(it) { return it.id === id; });
  var newIdx = idx + delta;
  if (idx < 0 || newIdx < 0 || newIdx >= items.length) return;
  var tmp = items[idx];
  items[idx] = items[newIdx];
  items[newIdx] = tmp;
  _followupsPut(items);
}

function _followupsEditItem(id) {
  if (!_followupsData) return;
  var items = _followupsData.items.slice();
  var idx = items.findIndex(function(it) { return it.id === id; });
  if (idx < 0) return;
  var next = window.prompt('Edit follow-up text:', items[idx].text);
  if (next === null) return; // cancelled
  var normalized = _composeNormalizeText(next);
  if (!normalized.trim()) return;
  items[idx] = { id: items[idx].id, text: normalized, enter: items[idx].enter };
  _followupsPut(items);
}

function _followupsRemoveItem(id) {
  if (!_followupsData) return;
  var items = _followupsData.items.filter(function(it) { return it.id !== id; });
  _followupsPut(items);
}

/** Clear all -- DELETE, wiping items AND any halt (spec §7.1/§9.3). */
async function _followupsClearAll() {
  if (!_viewingSession) return;
  try {
    await api('DELETE', '/api/sessions/' + encodeURIComponent(_viewingSession) + '/followups');
  } catch (err) {
    console.warn('[_followupsClearAll] failed:', err);
  }
  await _followupsRefresh();
}

/** Resume -- clears the halt only, keeping every pending item (spec §9.4). */
async function _followupsResume() {
  if (!_viewingSession) return;
  try {
    await api('POST', '/api/sessions/' + encodeURIComponent(_viewingSession) + '/followups/resume');
  } catch (err) {
    console.warn('[_followupsResume] failed:', err);
  }
  await _followupsRefresh();
}

/**
 * "Remove this item" on the halted banner -- a PUT dropping the halted
 * item, which leaves the halt SET so Resume stays a separate, explicit
 * action (spec §9.4: nothing clears a halt as a side effect of an edit).
 */
function _followupsRemoveHalted() {
  if (!_followupsData || !_followupsData.halted) return;
  var itemId = _followupsData.halted.item_id;
  var items = _followupsData.items.filter(function(it) { return it.id !== itemId; });
  _followupsPut(items);
}

/**
 * Queue the current compose draft instead of sending it now -- POST
 * .../followups. No precondition (appending is commutative, spec §7.1).
 * Mirrors _composeSend()'s draft-survives-failure behavior: cleared only
 * on success.
 */
/** Test-only helper: set `_followupsData` directly, bypassing the fetch. */
function _followupsSetDataForTests(data) {
  _followupsData = data;
}

async function _followupsQueueDraft() {
  var input = $('compose-input');
  if (!input) return;
  var normalized = _composeNormalizeText(input.value);
  if (!normalized.trim()) {
    _composeShowError('Nothing to queue.');
    return;
  }
  if (!_viewingSession) {
    _composeShowError('No session is open.');
    return;
  }
  try {
    await api(
      'POST',
      '/api/sessions/' + encodeURIComponent(_viewingSession) + '/followups',
      { text: normalized, enter: true },
    );
    input.value = '';
    input.style.height = '';
    _composeHideError();
    if (window._refitTerminal) window._refitTerminal();
    await _followupsRefresh();
  } catch (err) {
    // A failed queue attempt must be impossible to miss: the inline
    // compose-error box is easy to overlook (no auto-dismiss, but also no
    // attention-grabbing motion) -- especially since this shortcut is meant
    // to work while the user's attention is on the TERMINAL, not the
    // compose bar. showToast() is the same loud, transient, hard-to-miss
    // surface used elsewhere in this file for other errors; using it here
    // too means "nothing happened" and "it failed" are never visually
    // identical outcomes.
    var msg = _composeErrorMessage(err);
    _composeShowError(msg);
    showToast('Follow-up not queued: ' + msg);
  }
}

// ─── Server settings ─────────────────────────────────────────────────────────

/**
 * Load server settings from GET /api/settings and cache in _serverSettings.
 * Always resolves — errors are logged as warnings.
 * @returns {Promise<object>}
 */
async function loadServerSettings() {
  try {
    const res = await api('GET', '/api/settings');
    _serverSettings = await res.json();
  } catch (err) {
    console.warn('[loadServerSettings] failed:', err);
    if (!_serverSettings) _serverSettings = {};
  }
  return _serverSettings;
}

/**
 * Load the resolved session command pairs from GET /api/session-commands
 * into _sessionCommands / _sessionCommandErrors. Fetched once at page load
 * (not polled -- pairs change only when the operator edits settings.json,
 * a rarely-changing local file).
 *
 * On failure, leaves _sessionCommands as null and logs to console -- never
 * toasts. A null list degrades to _createCommandSelect()'s one-pair path,
 * i.e. today's create-session UI, so a failed fetch costs the picker, never
 * the ability to create a session.
 * @returns {Promise<void>}
 */
async function loadSessionCommands() {
  try {
    const res = await api('GET', '/api/session-commands');
    const body = await res.json();
    _sessionCommands = body.commands || null;
    _sessionCommandErrors = body.errors || [];
  } catch (err) {
    console.warn('[loadSessionCommands] failed:', err);
    _sessionCommands = null;
    _sessionCommandErrors = [];
  }
  // Update the outside-the-dialog error badges from THIS fetch, not just
  // when renderCommandPairsSettings() runs -- this function is called once
  // at page load (before Settings has ever been opened), so the badge must
  // be current without requiring the user to open Settings first.
  _updateConfigErrorBadges();
}

/**
 * Load view-rule validation errors from GET /api/views into
 * _viewRuleErrors -- the same "fail loud" treatment as
 * loadSessionCommands()/_sessionCommandErrors (docs/plans/2026-08-04-auto-views-plan.md §9.2).
 * Called from the same two places loadSessionCommands() is (page load and
 * every openSettings()), PLUS from followRemoteViewDefinitions()'s
 * settings-changed branch -- so a rule that arrives by federation sync or
 * a direct file edit surfaces without a reload.
 * @returns {Promise<void>}
 */
async function loadViewRules() {
  try {
    const res = await api('GET', '/api/views');
    const body = await res.json();
    _viewRuleErrors = body.errors || [];
  } catch (err) {
    console.warn('[loadViewRules] failed:', err);
    _viewRuleErrors = [];
  }
  _updateConfigErrorBadges();
}

/**
 * Render the Settings > Terminal tab's install status, hint, and preview
 * from a GET/PATCH /api/tmux-config response *cfg*. Shared by
 * loadTmuxConfigSettings() (initial populate) and patchTmuxConfig() (after
 * a change), so both paths keep the tab in agreement with the server.
 */
function _renderTmuxConfigTab(cfg) {
  const statusEl = $('tmux-config-status');
  if (statusEl) statusEl.textContent = cfg && cfg.installed ? 'Installed' : 'Not installed';
  const hintEl = $('tmux-config-install-hint');
  if (hintEl) hintEl.style.display = cfg && cfg.installed ? 'none' : '';
  // #setting-tmux-preview is a <pre>, not a <textarea> (see style.css's
  // .settings-tmux-preview comment for why) -- set textContent, not .value.
  const previewEl = $('setting-tmux-preview');
  if (previewEl) previewEl.textContent = (cfg && cfg.preview) || '';
}

/**
 * Populate the Settings > Terminal tab from GET /api/tmux-config: rebuilds
 * the theme <select> options, sets the copy-mode radio, and calls
 * _renderTmuxConfigTab() for the install status/hint/preview.
 */
function loadTmuxConfigSettings() {
  return api('GET', '/api/tmux-config')
    .then(function(res) { return res.json(); })
    .then(function(cfg) {
      _tmuxConfig = cfg;
      const themeEl = $('setting-tmux-theme');
      if (themeEl) {
        themeEl.innerHTML = '';
        (cfg.available_themes || []).forEach(function(name) {
          const opt = document.createElement('option');
          opt.value = name;
          opt.textContent = name;
          if (name === cfg.theme) opt.selected = true;
          themeEl.appendChild(opt);
        });
      }
      const desktopRadio = $('setting-tmux-copy-mode-desktop');
      const viRadio = $('setting-tmux-copy-mode-vi');
      const isVi = cfg.copy_mode === 'vi';
      if (desktopRadio) desktopRadio.checked = !isVi;
      if (viRadio) viRadio.checked = isVi;
      _renderTmuxConfigTab(cfg);
    })
    .catch(function(err) {
      console.warn('[loadTmuxConfigSettings] failed:', err);
    });
}

/**
 * PATCH /api/tmux-config with *patch* ({theme?, copy_mode?}). The server
 * re-renders fragments and (if a tmux server is running) reloads it live,
 * then returns the same shape as GET -- used to refresh the tab so the
 * preview always reflects what the server actually rendered, not an
 * optimistic guess.
 */
function patchTmuxConfig(patch) {
  return api('PATCH', '/api/tmux-config', patch)
    .then(function(res) { return res.json(); })
    .then(function(cfg) {
      _tmuxConfig = cfg;
      _renderTmuxConfigTab(cfg);
      showToast('Terminal settings updated');
    })
    .catch(function(err) {
      console.warn('[patchTmuxConfig] failed:', err);
      showToast('Failed to update terminal settings');
    });
}

/**
 * Re-render every view-dependent surface from current _serverSettings.
 * Shared by followRemoteViewDefinitions() and the guarded-PATCH conflict
 * path below -- both situations are "server truth changed out from under
 * us, redraw everything that depends on view membership."
 */
function _rerenderViewDependentUI() {
  renderViewDropdown();
  renderGrid(_currentSessions || []);
  renderSidebar(_currentSessions || [], _viewingSession, _viewingRemoteId);
  syncSortOrderControls();
  if (_settingsOpen) renderViewsSettingsTab();
  var manageViewPanel = $('manage-view-panel');
  if (manageViewPanel && !manageViewPanel.classList.contains('hidden')) {
    renderManageViewList();
    _renderManageViewRuleEditor(false); // false: don't clobber an unsaved in-progress edit
  }
}

/**
 * Set every sort-order control's displayed value from _serverSettings.sort_order.
 * There are three surfaces backed by the SAME server setting (the header quick-sort
 * dropdown, the sidebar quick-sort dropdown, and the Settings > Sessions select) --
 * this is the single place that keeps them in agreement, called after any event
 * that changes or (re)loads _serverSettings (initial load, a remote settings
 * change via followRemoteViewDefinitions -> _rerenderViewDependentUI, opening
 * Settings, or a local change on any one of the three).
 *
 * v0.47.9: the two quick surfaces are button+menu dropdowns now, not
 * <select>s (see the "Quick dropdown controller" section), so there's no
 * single `.value` to set on them -- their trigger's label span gets updated
 * instead, and their menu content is refreshed in place if currently open
 * (so the active-item checkmark stays correct without waiting for the next
 * open). Missing elements (e.g. Settings dialog not yet opened) are skipped
 * silently, same as before.
 */
function syncSortOrderControls() {
  var value = (_serverSettings && _serverSettings.sort_order) || 'manual';

  var settingSelect = $('setting-sort-order');
  if (settingSelect) settingSelect.value = value;

  var label = $('sort-order-label');
  if (label) label.textContent = _sortOptionLabel(value);
  var sidebarLabel = $('sidebar-sort-order-label');
  if (sidebarLabel) sidebarLabel.textContent = _sortOptionLabel(value);

  if (isQuickDropdownOpen(_sortDropdownQD)) renderSortDropdown();
  if (isQuickDropdownOpen(_sidebarSortDropdownQD)) renderSidebarSortDropdown();
}

/**
 * Settings > Sessions sort-order select's own change handler -- the ONLY
 * remaining native <select> among the three sort surfaces (the header and
 * sidebar quick controls became button+menu dropdowns in v0.47.9; see
 * createQuickDropdown()'s docstring). Delegates to selectSortOrder(), the
 * shared apply-and-sync logic every sort surface now funnels through, so a
 * change made here is reflected on the two quick dropdowns immediately too.
 */
function onSortOrderChange() {
  var value = this && this.value;
  selectSortOrder(value);
}

/**
 * PATCH /api/settings with optimistic-concurrency protection against the
 * settings-clobber bug: a tab holding a STALE `_serverSettings` snapshot
 * (e.g. an old copy of the entire `views` array) building a patch from
 * that stale data and overwriting a concurrent edit from another
 * device/tab. This is exactly how a real incident destroyed 7 of 8 views
 * in one PATCH request.
 *
 * The server (as of the `expected_settings_updated_at` PATCH precondition)
 * rejects the write with 409 when the caller's expectation is stale,
 * making NO write -- see main.py's update_settings(). This helper is the
 * one place that precondition is attached and the 409 retry is handled,
 * so every call site gets the protection for free instead of re-deriving
 * it per call site.
 *
 * ALSO re-fetches settings from the server IMMEDIATELY BEFORE building any
 * patch that touches `views`/`hidden_sessions` -- never trusting a possibly
 * long-lived `_serverSettings` cache as the baseline for a views mutation.
 * This is the frontend half of closing the settings-clobber incident: the
 * CAS precondition above stops a stale write from being ACCEPTED, but a page
 * left open for hours would still keep BUILDING views patches from ancient
 * data until it happened to 409 and recover. Detection is a cheap two-step:
 * call `mutateFn` once against whatever baseline is on hand to see what it
 * WOULD write; if that draft touches `views`/`hidden_sessions`, re-fetch and
 * call `mutateFn` again against the fresh copy, discarding the draft. A
 * patch that never touches those keys (e.g. a plain `fontSize` change) skips
 * the extra round-trip entirely.
 *
 * A second, distinct failure mode gets different treatment: a 409 whose
 * body has `backstop: true` (see main.py's update_settings()) means the
 * write was rejected by the destructive-write backstop, not a stale
 * baseline -- the intent itself (e.g. "replace views with this array")
 * would catastrophically shrink view definitions. Retrying would just
 * resend the same destructive payload, so this case never retries: it
 * reloads server truth, re-renders, and logs a warning instead.
 *
 * @param {function(object): object} mutateFn - Given a deep copy of the
 *   CURRENT `_serverSettings` (freshly re-fetched when the resulting patch
 *   touches views/hidden_sessions; see above), returns the PATCH BODY to
 *   send, e.g. `{ views: [...] }`. May be called up to three times: once to
 *   detect intent, once (only if that intent touches views/hidden_sessions)
 *   against a freshly re-fetched snapshot, and -- only on exactly one
 *   stale-baseline 409 -- once more with an even-fresher snapshot.
 * @param {object} [opts]
 * @param {boolean} [opts.retry=true] - Internal: false on the retry attempt
 *   itself, so a second consecutive 409 does not loop.
 * @returns {Promise<object>} the parsed PATCH response body (redacted
 *   settings, same shape GET /api/settings returns).
 */
async function patchSettingsGuarded(mutateFn, opts) {
  var retry = !opts || opts.retry !== false;
  // The retry attempt (opts.retry === false) already has a guaranteed-fresh
  // baseline -- the 409 handler below just re-fetched it moments ago
  // specifically so the retry could rebuild against server truth. Skip the
  // detection re-fetch in that case; doing it anyway would just be a
  // redundant extra round-trip against data that hasn't changed.
  var isRetryAttempt = !!(opts && opts.retry === false);
  var baseline = _serverSettings ? JSON.parse(JSON.stringify(_serverSettings)) : {};
  var patch = mutateFn(baseline);

  if (!isRetryAttempt &&
      (Object.prototype.hasOwnProperty.call(patch, 'views') ||
       Object.prototype.hasOwnProperty.call(patch, 'hidden_sessions'))) {
    // This patch touches view membership/visibility -- never build it from
    // a baseline that might be stale. Re-fetch and rebuild before sending.
    await loadServerSettings();
    _lastSettingsUpdatedAt = (_serverSettings && _serverSettings.settings_updated_at) || _lastSettingsUpdatedAt;
    baseline = _serverSettings ? JSON.parse(JSON.stringify(_serverSettings)) : {};
    patch = mutateFn(baseline);
  }
  patch.expected_settings_updated_at = _lastSettingsUpdatedAt;

  try {
    const res = await api('PATCH', '/api/settings', patch);
    const responseBody = await res.json();
    if (typeof responseBody.settings_updated_at === 'number') {
      _lastSettingsUpdatedAt = responseBody.settings_updated_at;
    }
    return responseBody;
  } catch (err) {
    if (err.status === 409 && err.body && err.body.backstop === true) {
      // Destructive-write backstop rejection: NOT a stale baseline -- the
      // mutation itself would catastrophically shrink `views`. Retrying
      // would just resend the same destructive payload, so reload from
      // server truth and stop here instead of recursing.
      await loadServerSettings();
      _lastSettingsUpdatedAt = (_serverSettings && _serverSettings.settings_updated_at) || _lastSettingsUpdatedAt;
      _rerenderViewDependentUI();
      console.warn(
        '[patchSettingsGuarded] destructive write rejected by server backstop:',
        err.body.detail,
      );
      throw err;
    }
    if (err.status === 409 && retry) {
      // Stale baseline: re-fetch server truth, re-apply the SAME intent to
      // the FRESH copy, and retry exactly once (retry:false below means a
      // second consecutive 409 falls to the else-branch, not another retry).
      await loadServerSettings();
      _lastSettingsUpdatedAt = (_serverSettings && _serverSettings.settings_updated_at) || _lastSettingsUpdatedAt;
      return patchSettingsGuarded(mutateFn, { retry: false });
    }
    if (err.status === 409) {
      // Second consecutive 409: don't loop. Re-render from server truth and
      // surface a brief non-blocking notice (no dedicated toast text here --
      // this is an edge case a normal user is unlikely to hit twice in a
      // row -- console.warn is the existing fallback pattern used elsewhere
      // in this file, e.g. loadServerSettings()'s own catch).
      await loadServerSettings();
      _lastSettingsUpdatedAt = (_serverSettings && _serverSettings.settings_updated_at) || _lastSettingsUpdatedAt;
      _rerenderViewDependentUI();
      console.warn('[patchSettingsGuarded] conflict persisted after retry; reloaded from server');
    }
    throw err;
  }
}

/**
 * Send a PATCH to /api/settings with a single key/value update.
 * Shows a toast on success or failure.
 * @param {string} key
 * @param {*} value
 * @returns {Promise<void>}
 */
async function patchServerSetting(key, value) {
  try {
    await patchSettingsGuarded(function() { return { [key]: value }; });
    _serverSettings = Object.assign({}, _serverSettings, { [key]: value });
    showToast('Setting saved');
  } catch (err) {
    showToast('Failed to save setting');
    console.warn('[patchServerSetting] failed:', err);
  }
}

/**
 * Build a single remote instance row element with URL input, name input, key input, and remove button.
 * @param {string} url - remote instance URL
 * @param {string} name - remote instance display name
 * @param {string} key - federation key for the remote instance
 * @returns {HTMLDivElement}
 */
function _buildRemoteInstanceRow(url, name, key) {
  var row = document.createElement('div');
  row.className = 'settings-remote-row';
  var urlInput = document.createElement('input');
  urlInput.type = 'text';
  urlInput.className = 'settings-remote-url';
  urlInput.placeholder = 'http://192.168.1.x:8000';
  urlInput.value = url || '';
  urlInput.setAttribute('aria-label', 'Remote instance URL');
  _suppressAutofill(urlInput);
  var nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'settings-remote-name';
  nameInput.placeholder = 'Device name';
  nameInput.value = name || '';
  nameInput.setAttribute('aria-label', 'Remote instance display name');
  _suppressAutofill(nameInput);
  // keyInput intentionally does NOT get _suppressAutofill: it's a real secret
  // (type="password") a user may deliberately want their password manager to
  // remember, unlike the name-ish fields above.
  var keyInput = document.createElement('input');
  keyInput.type = 'password';
  keyInput.className = 'settings-remote-key';
  keyInput.placeholder = 'Federation key';
  keyInput.value = key || '';
  keyInput.setAttribute('aria-label', 'Federation key for remote instance');
  var removeBtn = document.createElement('button');
  removeBtn.className = 'settings-remote-remove';
  removeBtn.textContent = '\u00d7';
  removeBtn.setAttribute('aria-label', 'Remove remote instance');
  row.appendChild(urlInput);
  row.appendChild(nameInput);
  row.appendChild(keyInput);
  row.appendChild(removeBtn);
  return row;
}

/**
 * Read remote instance rows from the DOM and save to server settings.
 */
function _saveRemoteInstances() {
  var container = $('setting-remote-instances');
  if (!container) return;
  var instances = [];
  container.querySelectorAll('.settings-remote-row').forEach(function(row) {
    var urlEl = row.querySelector('.settings-remote-url');
    var nameEl = row.querySelector('.settings-remote-name');
    var keyEl = row.querySelector('.settings-remote-key');
    var url = (urlEl && urlEl.value) ? urlEl.value.trim() : '';
    var name = (nameEl && nameEl.value) ? nameEl.value.trim() : '';
    var key = (keyEl && keyEl.value) ? keyEl.value.trim() : '';
    if (url) {
      instances.push({ url: url, name: name, key: key });
    }
  });
  patchServerSetting('remote_instances', instances);
}

// ─── Generic list editor (reusable add/remove string-list rows) ──────
//
// Modeled on _buildRemoteInstanceRow()/_saveRemoteInstances() above,
// collapsed to a single field per row instead of three (url/name/key).
// Deliberately named after the MECHANISM, not the one setting it is wired
// to today (input_allowed_sessions) -- so a future string-list setting can
// reuse these two functions instead of hand-rolling another row builder.

/**
 * Build a single generic list-editor row: one text input + remove button.
 * @param {string} value
 * @param {string} [placeholder]
 * @returns {HTMLDivElement}
 */
function _buildListEditorRow(value, placeholder) {
  var row = document.createElement('div');
  row.className = 'settings-list-editor-row';
  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'settings-list-editor-value';
  input.placeholder = placeholder || '';
  input.value = value || '';
  input.setAttribute('aria-label', 'List entry');
  _suppressAutofill(input);
  var removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'settings-list-editor-remove';
  removeBtn.textContent = '\u00d7';
  removeBtn.setAttribute('aria-label', 'Remove entry');
  row.appendChild(input);
  row.appendChild(removeBtn);
  return row;
}

/**
 * Read a generic list-editor container's rows into a trimmed,
 * blank-filtered string array. An empty (or all-blank) list serializes as
 * `[]`, never `[""]` -- same "only push if truthy after trim" rule
 * _saveRemoteInstances() applies to its own url field.
 * @param {HTMLElement|null} container - element with `.settings-list-editor-row` children
 * @returns {string[]}
 */
function _serializeListEditor(container) {
  var values = [];
  if (!container) return values;
  container.querySelectorAll('.settings-list-editor-row').forEach(function(row) {
    var input = row.querySelector('.settings-list-editor-value');
    var value = (input && input.value) ? input.value.trim() : '';
    if (value) values.push(value);
  });
  return values;
}

/**
 * Read #setting-input-allowed-sessions rows and save to
 * settings.input_allowed_sessions -- case-insensitive glob patterns naming
 * which sessions may receive typed input (see
 * terminal_input.session_matches_allowlist; this file never re-derives
 * that matcher, per AGENTS.md's "the matcher lives in exactly one place").
 */
function _saveInputAllowedSessions() {
  var container = $('setting-input-allowed-sessions');
  patchServerSetting('input_allowed_sessions', _serializeListEditor(container));
}

// ─── Agent Terminal Input (input_enabled / input_allowed_sessions) ───
//
// SECURITY: these two settings.py keys are OPERATOR_SETTABLE_LOCAL_KEYS --
// PATCH /api/settings accepts them ONLY from a real operator credential
// (browser cookie / HTTP Basic), never from a caller authorized solely by
// the federation Bearer key (the SAME credential handed to remote agents).
// This file relies entirely on that server-side fence: nothing here needs
// to know or check the caller's credential type -- the normal
// patchServerSetting()/patchSettingsGuarded() path already 403s for a
// Bearer-only caller, exactly like every other settings write.

/**
 * Show/hide the allowed-sessions list editor alongside the input_enabled
 * checkbox -- it is meaningless (and disabled server-side) while input is
 * off.
 * @param {boolean} enabled
 */
function _updateInputAllowedSessionsFieldVisibility(enabled) {
  var field = $('settings-input-allowed-sessions-field');
  if (!field) return;
  field.classList.toggle('hidden', !enabled);
}

/**
 * Pure patch-body builder for the one-click "Enable typing for this
 * fleet" button. Always turns input_enabled on. ALSO widens
 * input_allowed_sessions to `["*"]` -- but ONLY when it is currently
 * empty -- so a deliberately-narrowed allow-list (e.g. `["agent-*"]`) is
 * never silently clobbered back to "every session".
 * @param {object} baseline - current settings, as patchSettingsGuarded's
 *   mutateFn receives them (a deep copy of _serverSettings)
 * @returns {{input_enabled: true, input_allowed_sessions?: string[]}}
 */
function _enableFederationTypingPatch(baseline) {
  var patch = { input_enabled: true };
  var current = (baseline && baseline.input_allowed_sessions) || [];
  if (!current.length) patch.input_allowed_sessions = ['*'];
  return patch;
}

/**
 * One-click handler: enable input for the whole fleet in a single PATCH
 * (see _enableFederationTypingPatch()), then reflect the new state into
 * the checkbox, the allowed-sessions list editor, and the compose-bar
 * gate (_composeRenderEnabledState()) -- no page reload required.
 * @returns {Promise<void>}
 */
async function _enableFederationTypingForFleet() {
  try {
    var body = await patchSettingsGuarded(_enableFederationTypingPatch);
    _serverSettings = Object.assign({}, _serverSettings);
    if (typeof body.input_enabled === 'boolean') _serverSettings.input_enabled = body.input_enabled;
    if (body.input_allowed_sessions) _serverSettings.input_allowed_sessions = body.input_allowed_sessions;

    var checkboxEl = $('setting-input-enabled');
    if (checkboxEl) checkboxEl.checked = !!_serverSettings.input_enabled;
    var listEl = $('setting-input-allowed-sessions');
    if (listEl && body.input_allowed_sessions) {
      listEl.innerHTML = '';
      body.input_allowed_sessions.forEach(function(pattern) {
        listEl.appendChild(_buildListEditorRow(pattern));
      });
    }
    _updateInputAllowedSessionsFieldVisibility(true);
    _composeRenderEnabledState();
    showToast('Typing enabled for this fleet');
  } catch (err) {
    showToast('Failed to enable typing');
    console.warn('[_enableFederationTypingForFleet] failed:', err);
  }
}

// ─── Multi-Device helper ──────────────────────────────────────────────────────────

/**
 * Enable or disable all Multi-Device tab fields (except the enable toggle itself).
 * When disabled, the fields container gets opacity: 0.5 and inputs/selects/buttons
 * are disabled so users cannot interact with them.
 * @param {boolean} enabled
 */
function _updateMultiDeviceFieldsState(enabled) {
  var fieldsContainer = $('multi-device-fields');
  if (!fieldsContainer) return;
  var controls = fieldsContainer.querySelectorAll('input, select, button');
  controls.forEach(function(ctrl) {
    ctrl.disabled = !enabled;
  });
  fieldsContainer.style.opacity = enabled ? '' : '0.5';
}


// ─── Settings dialog ──────────────────────────────────────────────────────────

/**
 * Get display settings from the server-settings cache (_serverSettings),
 * falling back to DISPLAY_DEFAULTS for any missing keys.
 * Only includes keys defined in DISPLAY_DEFAULTS.
 * @returns {object}
 */
function getDisplaySettings() {
  const result = Object.assign({}, DISPLAY_DEFAULTS);
  const ss = _serverSettings || {};
  for (const key of Object.keys(DISPLAY_DEFAULTS)) {
    if (Object.prototype.hasOwnProperty.call(ss, key)) {
      result[key] = ss[key];
    }
  }
  return result;
}

/**
 * Set grid template for fit mode based on tile count.
 * Pure arithmetic — no DOM measurement, no getComputedStyle, no clientHeight.
 * Safe to call at any time regardless of display state or layout phase.
 *
 * The grid already has a definite height from CSS (flex: 1 inside height: 100dvh).
 * Setting grid-template-rows: repeat(rows, 1fr) lets the browser divide that height
 * equally without JS needing to know the pixel dimensions.  Tiles use height: auto
 * (set in CSS) so they fill their grid cells without inline style overrides.
 *
 * @param {Element} grid - The session grid element
 */
function applyFitLayout(grid) {
  var count = grid.querySelectorAll('.session-tile').length;
  if (count === 0) {
    grid.style.removeProperty('grid-template-columns');
    grid.style.removeProperty('grid-template-rows');
    return;
  }

  // Calculate optimal cols/rows — start with square root
  var cols = Math.ceil(Math.sqrt(count));
  var rows = Math.ceil(count / cols);

  // Prefer wider layouts (more cols, fewer rows) since tiles are landscape
  if (rows > 1 && cols < count) {
    var altCols = cols + 1;
    var altRows = Math.ceil(count / altCols);
    if (altRows < rows) {
      cols = altCols;
      rows = altRows;
    }
  }

  grid.style.gridTemplateColumns = 'repeat(' + cols + ', 1fr)';
  grid.style.gridTemplateRows = 'repeat(' + rows + ', 1fr)';
}

/**
 * Cycle the dashboard view mode: auto → fit → auto.
 * Persists to server settings and reapplies display settings.
 */
function cycleViewMode() {
  var ds = getDisplaySettings();
  var idx = VIEW_MODES.indexOf(ds.viewMode || 'auto');
  ds.viewMode = VIEW_MODES[(idx + 1) % VIEW_MODES.length];
  if (_serverSettings) _serverSettings.viewMode = ds.viewMode;
  patchServerSetting('viewMode', ds.viewMode);
  applyDisplaySettings(ds);

  // Update button label
  var btn = document.getElementById('view-mode-btn');
  if (btn) btn.title = 'View: ' + ds.viewMode;
}

/**
 * Apply display settings to the live DOM.
 * Sets --preview-font-size CSS custom property and updates #session-grid
 * grid-template-columns based on the gridColumns setting and viewMode.
 * @param {object} ds - display settings object
 */
function applyDisplaySettings(ds) {
  // Apply font size as CSS custom property (tile previews)
  if (document.documentElement) {
    document.documentElement.style.setProperty('--preview-font-size', ds.fontSize + 'px');
  }

  // Apply font size to the live xterm.js terminal without reconnecting
  if (window._setTerminalFontSize) {
    window._setTerminalFontSize(ds.fontSize);
  }

  // Apply view mode to grid
  var grid = document.getElementById('session-grid');
  if (!grid) return;

  var mode = ds.viewMode || 'auto';

  // Remove all mode classes
  grid.classList.remove('session-grid--fit');

  // Reset any inline styles from previous fit calculation
  grid.style.removeProperty('grid-template-rows');
  grid.style.removeProperty('grid-template-columns');

  if (mode === 'auto') {
    // Restore grid columns setting
    if (ds.gridColumns === 'auto' || !ds.gridColumns) {
      grid.style.removeProperty('grid-template-columns');
    } else {
      grid.style.gridTemplateColumns = 'repeat(' + ds.gridColumns + ', 1fr)';
    }

  } else if (mode === 'fit') {
    grid.classList.add('session-grid--fit');
    applyFitLayout(grid);
  }
}

/**
 * Load grid view mode preference from display settings (server).
 * Returns 'flat' as default.
 * @returns {string}
 */
function loadGridViewMode() {
  var ds = getDisplaySettings();
  var mode = ds.gridViewMode || 'flat';
  // 'filtered' was removed in the Views feature — fall back to 'flat'
  if (mode === 'filtered') mode = 'flat';
  return mode;
}

/**
 * Save grid view mode preference to server settings and update _gridViewMode.
 * @param {string} mode - The grid view mode to save.
 */
function saveGridViewMode(mode) {
  if (_serverSettings) _serverSettings.gridViewMode = mode;
  patchServerSetting('gridViewMode', mode);
  _gridViewMode = mode;
}

/**
 * Handle a change event on any Display settings control.
 * Reads current values from form elements, saves via server settings PATCH,
 * and applies via applyDisplaySettings immediately.
 */
function onDisplaySettingChange() {
  var ds = getDisplaySettings();

  var fontSizeEl = document.getElementById('setting-font-size');
  if (fontSizeEl) ds.fontSize = parseInt(fontSizeEl.value, 10) || ds.fontSize;

  var hoverDelayEl = document.getElementById('setting-hover-delay');
  if (hoverDelayEl) ds.hoverPreviewDelay = parseInt(hoverDelayEl.value, 10);

  var gridColumnsEl = document.getElementById('setting-grid-columns');
  if (gridColumnsEl) {
    var raw = gridColumnsEl.value;
    ds.gridColumns = raw === 'auto' ? 'auto' : parseInt(raw, 10);
  }

  var deviceLabelPlacementEl = document.getElementById('setting-device-label-placement');
  if (deviceLabelPlacementEl) ds.deviceLabelPlacement = deviceLabelPlacementEl.value;

  var activityIndicatorEl = document.getElementById('setting-activity-indicator');
  if (activityIndicatorEl) ds.activityIndicator = activityIndicatorEl.value;

  var patch = {
    fontSize: ds.fontSize,
    hoverPreviewDelay: ds.hoverPreviewDelay,
    gridColumns: ds.gridColumns,
    deviceLabelPlacement: ds.deviceLabelPlacement,
    activityIndicator: ds.activityIndicator,
  };
  Object.assign(_serverSettings, patch);
  patchSettingsGuarded(function() { return patch; })
    .then(function() { showToast('Settings saved'); })
    .catch(function(err) { console.warn('[onDisplaySettingChange] failed:', err); });
  applyDisplaySettings(ds);
  _updateDeviceLabelAmbiguityNote(ds);
}

/**
 * Show the "devices will look identical" consequence line under the device-label
 * control when, and only when, the user has chosen 'off' AND this install actually
 * aggregates more than one device. Making the consequence visible at the moment of
 * the decision is deliberately the ONLY place this is surfaced -- the render path
 * never second-guesses the setting (see docs/plans/2026-08-04-device-label-placement-plan.md, Q3).
 * @param {object} ds - display settings (or server settings; only the one key is read)
 */
function _updateDeviceLabelAmbiguityNote(ds) {
  var el = document.getElementById('device-label-ambiguity-note');
  if (!el || !el.classList) return;
  var ambiguous = deviceLabelPlacement(ds) === 'off'
    && !!(_serverSettings && _serverSettings.multi_device_enabled);
  if (ambiguous) {
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
  }
}

/**
 * Update notification UI controls to reflect the current permission state.
 * @param {Element} statusEl  - The status text element.
 * @param {Element} reqBtn    - The request-permission button.
 * @param {string}  permission - Notification.permission value, or 'unsupported'.
 */
function _updateNotificationUI(statusEl, reqBtn, permission) {
  if (!statusEl || !reqBtn) return;
  if (permission === 'granted') {
    statusEl.textContent = 'Granted';
    reqBtn.disabled = true;
  } else if (permission === 'denied') {
    statusEl.textContent = 'Denied (check browser settings)';
    reqBtn.disabled = true;
  } else if (permission === 'unsupported') {
    statusEl.textContent = 'Not supported';
    reqBtn.disabled = true;
  } else {
    statusEl.textContent = 'Not requested';
    reqBtn.disabled = false;
  }
}

/**
 * Open the settings dialog.
 * Sets _settingsOpen, calls dialog.showModal(), removes hidden from backdrop,
 * and loads current display settings into form controls.
 */
/**
 * Populate the Agent tab (muxplex-3lr) from the panel's own per-device
 * preferences.
 *
 * These are browser-local values, NOT server settings: nothing here goes
 * through patchServerSetting()/patchSettingsGuarded(), nothing here is
 * federation-synced, and a phone and a desktop are expected to disagree.
 *
 * The values live in chat.js (window.muxplexAgentPrefs) rather than here, and
 * that indirection is the point: chat.js's keydown handler branches on
 * getSendMode() and its byline hint is written from the same call, so a copy
 * of the storage key or the default in this file would be a second source of
 * truth for a setting whose entire acceptance criterion is that the hint and
 * the handler agree. If the panel script failed to load, the tab renders
 * nothing rather than writing a key nobody reads.
 */
function _renderAgentSettingsTab() {
  const prefs = window.muxplexAgentPrefs;
  if (!prefs) {
    console.warn('[settings] Agent tab: chat.js preferences unavailable -- ' +
      'send/newline choice not rendered');
    return;
  }
  const mode = prefs.getSendMode();
  const newlineRadio = $('setting-agent-send-mode-newline');
  const sendRadio = $('setting-agent-send-mode-send');
  const sends = mode === prefs.SEND_MODE_SEND;
  if (newlineRadio) newlineRadio.checked = !sends;
  if (sendRadio) sendRadio.checked = sends;
}

function openSettings() {
  _settingsOpen = true;
  const dialog = $('settings-dialog');
  if (dialog) dialog.showModal();
  const backdrop = $('settings-backdrop');
  if (backdrop) backdrop.classList.remove('hidden');
  const settings = getDisplaySettings();
  const fontSizeEl = $('setting-font-size');
  if (fontSizeEl) fontSizeEl.value = String(settings.fontSize);
  const hoverDelayEl = $('setting-hover-delay');
  if (hoverDelayEl) hoverDelayEl.value = String(settings.hoverPreviewDelay);
  const gridColumnsEl = $('setting-grid-columns');
  if (gridColumnsEl) gridColumnsEl.value = String(settings.gridColumns);
  const viewModeEl = $('setting-view-mode');
  if (viewModeEl) viewModeEl.value = loadGridViewMode();

  // Populate display toggle controls
  const deviceLabelPlacementEl = $('setting-device-label-placement');
  if (deviceLabelPlacementEl) deviceLabelPlacementEl.value = deviceLabelPlacement(settings);
  _updateDeviceLabelAmbiguityNote(settings);
  const activityIndicatorEl = $('setting-activity-indicator');
  if (activityIndicatorEl) activityIndicatorEl.value = settings.activityIndicator || 'both';

  // Populate Sessions tab / bell sound from display settings
  const bellSoundEl = $('setting-bell-sound');
  if (bellSoundEl) bellSoundEl.checked = !!settings.bellSound;

  // Populate Agent tab from the panel's per-device (localStorage) prefs.
  // Synchronous on purpose -- these never involve the server, so they must
  // not be inside the loadServerSettings() promise below.
  _renderAgentSettingsTab();

  // Agent provider credential status (docs/designs/agent-credentials.md) --
  // owned by chat.js (window.muxplexAgentCredential), same "read from
  // chat.js, don't reimplement here" discipline as the send-mode prefs
  // above. bindForm() is idempotent-guarded internally by binding once
  // per page load (via a module-level closure in chat.js), so calling it
  // every time the dialog opens is safe.
  if (window.muxplexAgentCredential) {
    window.muxplexAgentCredential.bindForm();
    window.muxplexAgentCredential.refreshStatus();
  }

  // Update notification permission status text/button
  const statusEl = $('notification-status-text');
  const reqBtn = $('notification-request-btn');
  if (statusEl && reqBtn) {
    const permission = typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
    _updateNotificationUI(statusEl, reqBtn, permission);
  }

  // Populate Sessions tab from server settings
  loadServerSettings().then(function(ss) {
    // Default session dropdown
    const defaultSessionEl = $('setting-default-session');
    if (defaultSessionEl) {
      // Rebuild options from current sessions
      defaultSessionEl.innerHTML = '<option value="">(none)</option>';
      (_currentSessions || []).forEach(function(s) {
        const opt = document.createElement('option');
        opt.value = s.name || '';
        opt.textContent = s.name || '';
        if (ss && ss.default_session === s.name) opt.selected = true;
        defaultSessionEl.appendChild(opt);
      });
    }

    // Sort order -- also syncs the header/sidebar quick-sort selects (same
    // underlying setting, three widgets).
    syncSortOrderControls();

    // Window size largest
    const windowSizeEl = $('setting-window-size-largest');
    if (windowSizeEl) {
      windowSizeEl.checked = !!(ss && ss.window_size_largest);
    }

    // Auto-open
    const autoOpenEl = $('setting-auto-open');
    if (autoOpenEl) {
      autoOpenEl.checked = ss && ss.auto_open_created !== undefined ? !!ss.auto_open_created : true;
    }

    // Device name
    const deviceNameEl = $('setting-device-name');
    if (deviceNameEl) {
      deviceNameEl.value = (ss && ss.device_name) || '';
    }

    // Update document.title from device_name setting
    updatePageTitle();

    // Multi-device enabled checkbox (with smart default: checked if remote_instances non-empty)
    const multiDeviceEnabledEl = $('setting-multi-device-enabled');
    if (multiDeviceEnabledEl) {
      var remoteList = (ss && ss.remote_instances) || [];
      multiDeviceEnabledEl.checked = !!(ss && ss.multi_device_enabled) ||
        remoteList.length > 0;
      _updateMultiDeviceFieldsState(multiDeviceEnabledEl.checked);
    }

    // Remote instances
    const remoteInstancesEl = $('setting-remote-instances');
    if (remoteInstancesEl) {
      remoteInstancesEl.innerHTML = '';
      var remotes = (ss && ss.remote_instances) || [];
      remotes.forEach(function(r) {
        remoteInstancesEl.appendChild(_buildRemoteInstanceRow(r.url || '', r.name || '', r.key || ''));
      });
    }

    // Agent Terminal Input (input_enabled / input_allowed_sessions) --
    // see settings.py's OPERATOR_SETTABLE_LOCAL_KEYS comment block for why
    // GET /api/settings reports these two keys unredacted (only PATCH
    // fences them) and why this UI needs no credential-type check of its
    // own.
    const inputEnabledEl = $('setting-input-enabled');
    const inputEnabled = !!(ss && ss.input_enabled === true);
    if (inputEnabledEl) inputEnabledEl.checked = inputEnabled;
    _updateInputAllowedSessionsFieldVisibility(inputEnabled);
    const inputAllowedSessionsEl = $('setting-input-allowed-sessions');
    if (inputAllowedSessionsEl) {
      inputAllowedSessionsEl.innerHTML = '';
      var allowedSessions = (ss && ss.input_allowed_sessions) || [];
      allowedSessions.forEach(function(pattern) {
        inputAllowedSessionsEl.appendChild(_buildListEditorRow(pattern));
      });
    }

    // Commands tab - populate create template textarea
    const templateEl = $('setting-template');
    if (templateEl) {
      templateEl.value = (ss && ss.new_session_template) || NEW_SESSION_DEFAULT_TEMPLATE;
    }

    // Commands tab - populate delete template textarea
    const deleteTemplateEl = $('setting-delete-template');
    if (deleteTemplateEl) {
      deleteTemplateEl.value = (ss && ss.delete_session_template) || DELETE_SESSION_DEFAULT_TEMPLATE;
    }

    // Commands tab - render additional command pairs + config errors (read-only)
    renderCommandPairsSettings();

    // Views tab
    renderViewsSettingsTab();
  });

  // Terminal tab (tmux theme/copy-mode) -- separate endpoint, own fetch.
  loadTmuxConfigSettings();

  // Commands tab -- re-fetch (not just re-render from possibly-stale module
  // state left over from the one-time page-load fetch) so "apply, then
  // reopen Settings" reflects what's actually on disk. Mirrors
  // loadTmuxConfigSettings() immediately above (see
  // docs/plans/2026-08-02-named-session-command-pairs-ui-design.md
  // §6 item 2 -- this is what closes the apply→verify loop for command pairs).
  loadSessionCommands().then(renderCommandPairsSettings);

  // Views tab errors -- same re-fetch-on-reopen rationale as Commands
  // immediately above (docs/plans/2026-08-04-auto-views-plan.md §9.2).
  loadViewRules().then(renderViewsSettingsTab);
}

/**
 * Single-quote *value* for safe paste into a POSIX shell argv, matching what
 * `muxplex commands add` (an argparse CLI, not a shell) expects on the other
 * end. This is purely a display/clipboard helper for a line the user pastes
 * into THEIR OWN shell -- muxplex itself never executes anything built here.
 * @param {*} value
 * @returns {string}
 */
function _shellQuote(value) {
  return "'" + String(value == null ? '' : value).replace(/'/g, "'\\''") + "'";
}

/**
 * Build the ready-to-run `muxplex commands add ...` line for *cmd* (an
 * existing pair, or an in-progress composer draft) -- see
 * docs/plans/2026-08-02-named-session-command-pairs-ui-design.md's verdict: "the thing you copy should be a
 * command, not JSON."
 * @param {{id: string, label: string, new_session_template: string, delete_session_template: string}} cmd
 * @returns {string}
 */
function _commandsAddInvocation(cmd) {
  return 'muxplex commands add --id ' + _shellQuote(cmd.id) +
    ' --label ' + _shellQuote(cmd.label) +
    ' --create ' + _shellQuote(cmd.new_session_template) +
    ' --delete ' + _shellQuote(cmd.delete_session_template);
}

/**
 * Copy *text* to the clipboard via the async Clipboard API, falling back to
 * a hidden-textarea + document.execCommand('copy') for contexts where
 * navigator.clipboard is unavailable (e.g. plain http:// LAN access, which
 * this app explicitly supports -- see README.md -- and where Clipboard API
 * is restricted to secure contexts in some browsers). On completion, briefly
 * flashes *btn*'s label to confirm success/failure, then restores it.
 * @param {string} text
 * @param {HTMLButtonElement} [btn]
 * @param {string} [restoreLabel]
 */
function _copyToClipboard(text, btn, restoreLabel) {
  function flash(ok) {
    if (!btn) return;
    btn.textContent = ok ? 'Copied!' : 'Copy failed';
    setTimeout(function() { btn.textContent = restoreLabel || 'Copy'; }, 1500);
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      function() { flash(true); },
      function() { flash(false); }
    );
    return;
  }
  try {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    var ok = document.execCommand('copy');
    document.body.removeChild(ta);
    flash(ok);
  } catch (_e) {
    flash(false);
  }
}

// Id of the pair the currently-open composer was duplicated from (test/debug
// visibility only -- the composer never reads this to decide behavior).
var _commandComposerSourceId = null;

/**
 * Recompute the composer's advisory warnings, `muxplex commands add` line,
 * and JSON preview from its current field values. Advisory-only, by design
 * (docs/plans/2026-08-02-named-session-command-pairs-ui-design.md §4): these checks exist to catch obvious typos
 * before copying, never to gate the copy buttons -- the authoritative
 * verdict is the server's own GET /api/session-commands `errors[]` after the
 * user actually applies the command and reopens Settings > Commands.
 */
function _renderCommandComposerOutput() {
  var idEl = $('composer-id');
  var labelEl = $('composer-label');
  var createEl = $('composer-create');
  var deleteEl = $('composer-delete');
  if (!idEl || !labelEl || !createEl || !deleteEl) return;

  var draft = {
    id: (idEl.value || '').trim(),
    label: (labelEl.value || '').trim(),
    new_session_template: createEl.value || '',
    delete_session_template: deleteEl.value || '',
  };

  var warnings = [];
  if (!draft.id) {
    warnings.push('id is required');
  } else if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(draft.id)) {
    warnings.push('id must be lowercase alphanumeric (plus _ or -), starting with a letter or digit, max 32 chars');
  } else if (draft.id === 'default') {
    warnings.push("id 'default' is reserved for the built-in pair");
  } else if ((_sessionCommands || []).some(function(c) { return c.id === draft.id && c.id !== _commandComposerSourceId; })) {
    warnings.push('id \u2018' + draft.id + '\u2019 is already in use');
  }
  if (!draft.label) warnings.push('label is required');
  if (!draft.new_session_template || draft.new_session_template.indexOf('{name}') === -1) {
    warnings.push('create command must contain {name}');
  }
  if (!draft.delete_session_template || draft.delete_session_template.indexOf('{name}') === -1) {
    warnings.push('delete command must contain {name}');
  }

  var warningEl = $('composer-warning');
  if (warningEl) {
    if (warnings.length > 0) {
      warningEl.textContent = '\u26a0 ' + warnings.join('; ') + '. Checked again by the server when you apply.';
      warningEl.style.display = '';
    } else {
      warningEl.style.display = 'none';
    }
  }

  var lineEl = $('composer-command-line');
  if (lineEl) lineEl.textContent = _commandsAddInvocation(draft);

  var jsonEl = $('composer-json');
  if (jsonEl) {
    jsonEl.textContent = JSON.stringify(
      {
        id: draft.id,
        label: draft.label,
        new_session_template: draft.new_session_template,
        delete_session_template: draft.delete_session_template,
      },
      null,
      2
    );
  }
}

/**
 * Close (and empty) the Duplicate composer, if open.
 */
function _closeCommandPairComposer() {
  var container = $('settings-command-composer');
  if (!container) return;
  container.classList.add('hidden');
  container.innerHTML = '';
  _commandComposerSourceId = null;
}

/**
 * Open the Duplicate composer, prefilled from *sourceCmd*. Builds an
 * editable id/label/create/delete form, a live-updating copyable
 * `muxplex commands add ...` line, and a "Show JSON" disclosure for
 * hand-editors -- see docs/plans/2026-08-02-named-session-command-pairs-ui-design.md §6 item 3. There is
 * deliberately NO Save button anywhere in this composer and nothing in it
 * ever calls patchServerSetting() / api('PATCH', ...) -- editing the fields
 * only updates what gets copied; applying it is the user's own shell.
 * @param {{id: string, label: string, new_session_template: string, delete_session_template: string}} sourceCmd
 */
function _openCommandPairComposer(sourceCmd) {
  var container = $('settings-command-composer');
  if (!container) return;
  _commandComposerSourceId = sourceCmd.id;
  container.innerHTML = '';

  var heading = document.createElement('label');
  heading.className = 'settings-label';
  heading.textContent = 'Duplicate \u2014 based on ' + sourceCmd.label + ' (' + sourceCmd.id + ')';
  container.appendChild(heading);

  var idInput = document.createElement('input');
  idInput.type = 'text';
  idInput.id = 'composer-id';
  idInput.className = 'settings-command-composer__input';
  idInput.placeholder = 'id (e.g. dev-alpha)';
  idInput.value = sourceCmd.id === 'default' ? '' : sourceCmd.id + '-copy';
  idInput.setAttribute('aria-label', 'New command pair id');
  _suppressAutofill(idInput);
  container.appendChild(idInput);

  var labelInput = document.createElement('input');
  labelInput.type = 'text';
  labelInput.id = 'composer-label';
  labelInput.className = 'settings-command-composer__input';
  labelInput.placeholder = 'Label';
  labelInput.value = sourceCmd.label;
  labelInput.setAttribute('aria-label', 'New command pair label');
  _suppressAutofill(labelInput);
  container.appendChild(labelInput);

  var createInput = document.createElement('textarea');
  createInput.id = 'composer-create';
  createInput.className = 'settings-textarea';
  createInput.rows = 2;
  createInput.value = sourceCmd.new_session_template;
  createInput.setAttribute('aria-label', 'New create command template');
  container.appendChild(createInput);

  var deleteInput = document.createElement('textarea');
  deleteInput.id = 'composer-delete';
  deleteInput.className = 'settings-textarea';
  deleteInput.rows = 2;
  deleteInput.value = sourceCmd.delete_session_template;
  deleteInput.setAttribute('aria-label', 'New delete command template');
  container.appendChild(deleteInput);

  var warningEl = document.createElement('p');
  warningEl.id = 'composer-warning';
  warningEl.className = 'settings-command-composer__warning';
  container.appendChild(warningEl);

  var outputRow = document.createElement('div');
  outputRow.className = 'settings-command-composer__output';
  var lineEl = document.createElement('code');
  lineEl.id = 'composer-command-line';
  lineEl.className = 'settings-command-pair__template';
  outputRow.appendChild(lineEl);
  var copyCmdBtn = document.createElement('button');
  copyCmdBtn.type = 'button';
  copyCmdBtn.className = 'settings-action-btn';
  copyCmdBtn.textContent = 'Copy command';
  copyCmdBtn.addEventListener('click', function() {
    _copyToClipboard(lineEl.textContent, copyCmdBtn, 'Copy command');
  });
  outputRow.appendChild(copyCmdBtn);
  container.appendChild(outputRow);

  var details = document.createElement('details');
  details.className = 'settings-command-composer__json';
  var summary = document.createElement('summary');
  summary.textContent = 'Show JSON';
  details.appendChild(summary);
  var jsonEl = document.createElement('pre');
  jsonEl.id = 'composer-json';
  details.appendChild(jsonEl);
  var copyJsonBtn = document.createElement('button');
  copyJsonBtn.type = 'button';
  copyJsonBtn.className = 'settings-action-btn';
  copyJsonBtn.textContent = 'Copy JSON';
  copyJsonBtn.addEventListener('click', function() {
    _copyToClipboard(jsonEl.textContent, copyJsonBtn, 'Copy JSON');
  });
  details.appendChild(copyJsonBtn);
  container.appendChild(details);

  var applyNote = document.createElement('span');
  applyNote.className = 'settings-helper';
  applyNote.textContent = 'These run shell commands on the server, so the browser composes them and ' +
    'your shell applies them. Paste this into any shell \u2014 including the terminal in this app. ' +
    '~/.config/muxplex/settings.json';
  var copyPathBtn = document.createElement('button');
  copyPathBtn.type = 'button';
  copyPathBtn.className = 'settings-action-btn';
  copyPathBtn.textContent = 'Copy path';
  copyPathBtn.addEventListener('click', function() {
    _copyToClipboard('~/.config/muxplex/settings.json', copyPathBtn, 'Copy path');
  });
  applyNote.appendChild(copyPathBtn);
  container.appendChild(applyNote);

  var closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'settings-action-btn';
  closeBtn.textContent = 'Close';
  closeBtn.addEventListener('click', _closeCommandPairComposer);
  container.appendChild(closeBtn);

  [idInput, labelInput, createInput, deleteInput].forEach(function(el) {
    el.addEventListener('input', _renderCommandComposerOutput);
  });

  container.classList.remove('hidden');
  _renderCommandComposerOutput();
  idInput.focus();
}

/**
 * Build a single command-pair row for Settings > Commands: id/label
 * (built-in default visually marked), the create/delete templates
 * (read-only), and the Duplicate.../Copy command actions (§6 item 3).
 * @param {{id: string, label: string, new_session_template: string, delete_session_template: string}} cmd
 * @returns {HTMLDivElement}
 */
function _buildCommandPairRow(cmd) {
  var isDefault = cmd.id === 'default';
  var row = document.createElement('div');
  row.className = 'settings-command-pair' + (isDefault ? ' settings-command-pair--builtin' : '');

  var title = document.createElement('div');
  title.className = 'settings-command-pair__title';
  title.textContent = cmd.label + ' (' + cmd.id + ')' + (isDefault ? ' \u2014 built-in' : '');
  row.appendChild(title);

  var createEl = document.createElement('code');
  createEl.className = 'settings-command-pair__template';
  createEl.textContent = cmd.new_session_template;
  row.appendChild(createEl);

  var deleteEl = document.createElement('code');
  deleteEl.className = 'settings-command-pair__template';
  deleteEl.textContent = cmd.delete_session_template;
  row.appendChild(deleteEl);

  var actions = document.createElement('div');
  actions.className = 'settings-command-pair__actions';

  var dupBtn = document.createElement('button');
  dupBtn.type = 'button';
  dupBtn.className = 'settings-action-btn';
  dupBtn.textContent = 'Duplicate\u2026';
  dupBtn.addEventListener('click', function() { _openCommandPairComposer(cmd); });
  actions.appendChild(dupBtn);

  var copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'settings-action-btn';
  copyBtn.textContent = 'Copy command';
  copyBtn.addEventListener('click', function() {
    _copyToClipboard(_commandsAddInvocation(cmd), copyBtn, 'Copy command');
  });
  actions.appendChild(copyBtn);

  row.appendChild(actions);
  return row;
}

/**
 * Reflect config-error counts as small badges OUTSIDE the Settings dialog
 * (both gear buttons) and on each relevant tab button itself, so a config
 * error is visible without opening Settings at all --
 * docs/plans/2026-08-02-named-session-command-pairs-ui-design.md
 * §6 item 5 ("fail loud" for a config error the user otherwise cannot see),
 * extended by docs/plans/2026-08-04-auto-views-plan.md §9.2 to cover view-rule errors too.
 *
 * The two GEAR badges show the SUM across every error source (so the user
 * always sees "something needs attention" from one glance at the gear);
 * each TAB badge shows only its OWN source (so opening Settings takes them
 * straight to the right tab). Generalizes the former
 * `_updateCommandErrorBadges` (kept as a thin alias below since some call
 * sites/tests may still reference the old name) to also read
 * `_viewRuleErrors`. Called from loadSessionCommands() AND loadViewRules()
 * so either fetch keeps both gear badges current.
 */
function _updateConfigErrorBadges() {
  var commandCount = (_sessionCommandErrors || []).length;
  var viewRuleCount = (_viewRuleErrors || []).length;
  var totalCount = commandCount + viewRuleCount;

  ['settings-error-badge', 'settings-error-badge-expanded'].forEach(function(id) {
    var el = $(id);
    if (!el) return;
    if (totalCount > 0) {
      el.textContent = String(totalCount);
      el.classList.remove('hidden');
    } else {
      el.classList.add('hidden');
    }
  });

  var tabBadges = [
    ['settings-tab-command-errors-badge', commandCount],
    ['settings-tab-view-errors-badge', viewRuleCount],
  ];
  tabBadges.forEach(function(pair) {
    var el = $(pair[0]);
    if (!el) return;
    if (pair[1] > 0) {
      el.textContent = String(pair[1]);
      el.classList.remove('hidden');
    } else {
      el.classList.add('hidden');
    }
  });
}

// Thin alias: kept for any pre-existing external reference to the old name.
function _updateCommandErrorBadges() {
  _updateConfigErrorBadges();
}

/**
 * Render the "Command pairs" (ALL configured pairs, including the built-in
 * `default` -- see docs/plans/2026-08-02-named-session-command-pairs-ui-design.md §6 item 1) and "Command pair
 * configuration errors" sections in Settings > Commands, from the module
 * state populated by loadSessionCommands() (_sessionCommands,
 * _sessionCommandErrors).
 *
 * Each row's Duplicate.../Copy command actions can populate editable text
 * fields (the composer) and copy strings to the clipboard, but NOTHING in
 * this file calls patchServerSetting()/api('PATCH', ...) for
 * session_commands -- it is in settings.LOCAL_ONLY_KEYS (server-executed
 * shell commands); the only way to actually apply a change is the user's
 * own shell, via the copyable `muxplex commands add` line or hand-editing
 * ~/.config/muxplex/settings.json. See
 * test_command_pairs.mjs's "no patch call anywhere in source" test, which
 * greps this file for exactly that.
 */
function renderCommandPairsSettings() {
  _closeCommandPairComposer();

  const pairsField = $('settings-command-pairs-field');
  const pairsList = $('settings-command-pairs');
  if (pairsList) {
    pairsList.innerHTML = '';
    var pairs = _sessionCommands || [];
    pairs.forEach(function(cmd) {
      pairsList.appendChild(_buildCommandPairRow(cmd));
    });
    // Always show the field once pairs have loaded -- resolve_session_commands()
    // guarantees `pairs` is never empty (element 0 is always "default"), so
    // this is effectively "show once loaded", not "show only when non-default
    // pairs exist". A user who has never configured an extra pair still sees
    // the built-in default and its Duplicate action -- the single biggest
    // discoverability fix in the design doc.
    if (pairsField) pairsField.style.display = pairs.length > 0 ? '' : 'none';
  }

  const errorsField = $('settings-command-errors-field');
  const errorsList = $('settings-command-errors');
  if (errorsList) {
    errorsList.innerHTML = '';
    (_sessionCommandErrors || []).forEach(function(err) {
      var li = document.createElement('li');
      li.textContent = err;
      errorsList.appendChild(li);
    });
    if (errorsField) {
      errorsField.style.display = (_sessionCommandErrors || []).length > 0 ? '' : 'none';
    }
  }

  _updateCommandErrorBadges();
}

/**
 * Close the settings dialog.
 * Sets _settingsOpen to false, calls dialog.close(), adds hidden to backdrop.
 */
function closeSettings() {
  _settingsOpen = false;
  const dialog = $('settings-dialog');
  if (dialog) dialog.close();
  const backdrop = $('settings-backdrop');
  if (backdrop) backdrop.classList.add('hidden');

  // muxplex-fx1 stale-gate fix: belt-and-suspenders re-check of the chat
  // panel's own "Agent isn't set up" gate. _bindAgentCredentialForm() (in
  // chat.js) already re-checks the gate the moment a credential save
  // succeeds, which is the tightest correct trigger -- this call covers
  // every other way the Agent tab's status could have changed while
  // Settings was open (e.g. an operator setting a provider env var out of
  // band) without requiring the user to close and reopen the chat panel.
  // Guarded and reached only through chat.js's own exposed global, the same
  // "chat.js owns the implementation, app.js calls the exposed name" shape
  // this file already uses for muxplexAgentPrefs and (just above, in
  // openSettings()) window.muxplexAgentCredential itself -- never reaches
  // into chat.js's private checkAgentGate()/setGateState() directly.
  if (window.muxplexAgentCredential && typeof window.muxplexAgentCredential.recheckGate === 'function') {
    window.muxplexAgentCredential.recheckGate();
  }
}

/**
 * Switch the active settings tab.
 * Toggles settings-tab--active class and aria-selected on tab buttons,
 * toggles hidden class on settings-panel elements by matching data-tab.
 * @param {string} tabName
 */
function switchSettingsTab(tabName) {
  document.querySelectorAll('.settings-tab').forEach(function(tab) {
    const isActive = tab.dataset.tab === tabName;
    if (isActive) {
      tab.classList.add('settings-tab--active');
      tab.setAttribute('aria-selected', 'true');
    } else {
      tab.classList.remove('settings-tab--active');
      tab.setAttribute('aria-selected', 'false');
    }
  });
  document.querySelectorAll('.settings-panel').forEach(function(panel) {
    const panelTab = panel.dataset.tab;
    if (panelTab === tabName) {
      panel.classList.remove('hidden');
    } else {
      panel.classList.add('hidden');
    }
  });
}

// ─── First-run welcome (one-time) ────────────────────────────────────
//
// muxplex has no onboarding flow, and until now nothing ever told a new
// user that federation/agent typing into this host's sessions is OFF by
// default (settings.py's `input_enabled`). The only way to discover that
// safety default was to try typing and collect a 403 -- which is exactly
// how this feature's own owner found it, across six machines. This dialog
// is that missing signpost, and nothing more.
//
// SCOPE, deliberately small: it is shown at most ONCE per browser, it
// never blocks anything, and its default action changes nothing. It is
// NOT an onboarding framework -- there is no step sequencing, no progress
// state, no server-side "has this user been onboarded" concept. If a
// second first-run message is ever wanted, that is the moment to decide
// whether this becomes a real onboarding component; do not grow this one
// into one by accident.
//
// STORAGE: browser-local only (localStorage), per-device, never a server
// setting and never federation-synced -- the same discipline
// COMPOSE_PREF_STORAGE_KEY and STT_CLOUD_CONSENT_STORAGE_KEY follow. "Has
// this browser seen the welcome?" is a property of this browser, not of
// the host; a second device SHOULD see it once too. It deliberately does
// NOT go through patchServerSetting().
const FIRSTRUN_STORAGE_KEY = 'muxplex_firstrun_seen';

/**
 * Pure decision: should the first-run welcome be shown, given the raw
 * stored flag? Kept separate from the localStorage read so the policy is
 * testable without a storage stub.
 *
 * Absent (null/undefined) or empty means "never seen" -> show. ANY other
 * value means seen -> don't. Deliberately not `=== '1'`: if a future
 * version ever writes a different marker (a timestamp, a version string),
 * the honest reading of "there is something recorded here" is still
 * "this browser has seen it", and a value we don't recognise must never
 * cause the dialog to reappear for a user who already dismissed it.
 *
 * @param {string|null|undefined} storedFlag - raw localStorage value
 * @returns {boolean}
 */
function _firstRunShouldShow(storedFlag) {
  return storedFlag === null || storedFlag === undefined || storedFlag === '';
}

/**
 * Record that this browser has seen the welcome. Defensive about a
 * blocked/throwing localStorage in the same shape as initComposePref()
 * and _sttCloudConsentAllow(): a failed write is not fatal, the dialog's
 * action still proceeds.
 */
function _firstRunMarkSeen() {
  try {
    localStorage.setItem(FIRSTRUN_STORAGE_KEY, '1');
  } catch (_) {
    // localStorage blocked -- nothing to record. _firstRunMaybeShow()
    // treats a blocked READ as "already seen" for exactly this reason,
    // so this cannot turn into a dialog that reappears every load.
  }
}

/** Open the welcome dialog (modal) and mark it seen immediately. */
function _firstRunOpen() {
  var dialog = $('firstrun-dialog');
  var backdrop = $('firstrun-backdrop');
  // Marked seen on SHOW, not only on action: if the user closes the tab
  // without touching a button, they have still seen it, and re-showing it
  // next load would be the nagging this is supposed to avoid.
  _firstRunMarkSeen();
  if (backdrop) backdrop.classList.remove('hidden');
  if (dialog && dialog.showModal) dialog.showModal();
}

/** Close the welcome dialog. Safe to call when it was never opened. */
function _firstRunClose() {
  var dialog = $('firstrun-dialog');
  var backdrop = $('firstrun-backdrop');
  if (dialog && dialog.close && dialog.open) dialog.close();
  if (backdrop) backdrop.classList.add('hidden');
}

/**
 * Show the welcome dialog if this browser has never seen it. Called once
 * at init, after bindStaticEventListeners() -- its buttons must already
 * be wired before it can be shown.
 * @returns {boolean} whether it was shown
 */
function _firstRunMaybeShow() {
  var stored;
  try {
    stored = localStorage.getItem(FIRSTRUN_STORAGE_KEY);
  } catch (_) {
    // localStorage blocked: we could show it, but we could never record
    // the dismissal -- so it would reappear on EVERY load. Treat blocked
    // storage as "already seen" and stay quiet. Note this is the opposite
    // call from _sttCloudConsentGranted(), correctly: that gate protects a
    // privacy decision (never assume consent), this one is a one-time
    // informational notice (never nag).
    return false;
  }
  if (!_firstRunShouldShow(stored)) return false;
  _firstRunOpen();
  return true;
}

/**
 * Welcome action 1 -- "Enable typing for this fleet". Delegates to the
 * SAME _enableFederationTypingForFleet() the Multi-Device tab's one-click
 * button uses (input_enabled=true, plus ["*"] only when the allow-list is
 * empty); this path adds no second copy of that policy.
 * @returns {Promise<void>}
 */
function _firstRunEnableTyping() {
  _firstRunMarkSeen();
  _firstRunClose();
  return _enableFederationTypingForFleet();
}

/**
 * Welcome action 2 -- "Open federation settings". Deep-links into the
 * Multi-Device tab, where both the Federation Key block and the new Agent
 * Terminal Input controls live. Same openSettings()+switchSettingsTab()
 * pair chat.js's agent-gate link already uses.
 */
function _firstRunOpenFederationSettings() {
  _firstRunMarkSeen();
  _firstRunClose();
  openSettings();
  switchSettingsTab('devices');
}

/** Welcome action 3 -- "Not now". Marks seen, closes, changes nothing. */
function _firstRunDismiss() {
  _firstRunMarkSeen();
  _firstRunClose();
}

/**
 * Global keydown handler.
 * Settings open: Escape closes settings, return.
 * Ignore shortcuts when typing in INPUT/TEXTAREA/SELECT.
 * Comma key (not in inputs, no ctrl/meta) opens settings.
 * Grid overview only: backtick toggles dropdown, number keys 1-9 switch views.
 * Arrow keys + Enter navigate within open dropdown.
 * Escape closes open dropdown.
 * Fullscreen: Escape calls closeSession().
 * @param {KeyboardEvent} e
 */
function handleGlobalKeydown(e) {
  // Settings open: only Escape closes it, then bail
  if (_settingsOpen) {
    if (e.key === 'Escape') { closeSettings(); }
    return;
  }
  // Determine if focus is inside a text input
  const tag = document.activeElement && document.activeElement.tagName;
  const inInput = (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT');
  // Comma key (not in inputs, no ctrl/meta) opens settings
  if (e.key === ',' && !e.ctrlKey && !e.metaKey && !inInput) {
    openSettings();
    return;
  }
  // View dropdown shortcuts — grid overview only, not in input, no ctrl/meta
  if (_viewMode === 'grid' && !inInput && !e.ctrlKey && !e.metaKey) {
    // Backtick toggles the view dropdown
    if (e.code === 'Backquote') {
      e.preventDefault();
      toggleViewDropdown();
      return;
    }
    // Number keys 1-9: 1=all, 9=hidden, 2-8=user views by index (num-2)
    if (e.code && e.code.startsWith('Digit')) {
      const num = parseInt(e.key, 10);
      if (num >= 1 && num <= 9) {
        const views = (_serverSettings && _serverSettings.views) || [];
        if (num === 1) {
          switchView('all');
        } else if (num === 9) {
          switchView('hidden');
        } else {
          const vIdx = num - 2;
          if (vIdx < views.length) { switchView(views[vIdx].name); }
        }
        return;
      }
    }
  }
  // Arrow/Enter/Escape navigation within whichever quick dropdown (view or
  // sort, header or sidebar) is currently open -- shared mechanism, see
  // handleQuickDropdownKeydown()'s own docstring. Previously this block only
  // ever checked #view-dropdown-menu, so the sidebar view dropdown had no
  // arrow-key navigation at all; delegating here extends the same
  // navigation to all four quick dropdowns, closing that gap as a side
  // effect of unifying the mechanism (v0.47.9).
  if (handleQuickDropdownKeydown(e)) return;
  // Fullscreen: Escape calls closeSession
  if (_viewMode === 'fullscreen' && e.key === 'Escape') {
    e.preventDefault();
    closeSession();
  }
}

/**
 * Open the bottom sheet (mobile session switcher).
 * Renders the current session list and removes the 'hidden' class.
 */
function openBottomSheet() {
  var sheet = $('bottom-sheet');
  if (!sheet) return;
  renderSheetList();
  sheet.classList.remove('hidden');
}

/**
 * Close the bottom sheet.
 * Adds the 'hidden' class and removes the dynamic backdrop listener.
 */
function closeBottomSheet() {
  var sheet = $('bottom-sheet');
  if (sheet) sheet.classList.add('hidden');
}

/**
 * Render the session list inside #sheet-list for the mobile bottom sheet.
 * Sorts sessions by priority, builds <li> elements with bell indicator and timestamp,
 * and binds click handlers to switch sessions.
 */
function renderSheetList() {
  var list = $('sheet-list');
  if (!list) return;
  var sorted = sortByPriority(getVisibleSessions(_currentSessions));
  list.innerHTML = sorted.map(function(s) {
    var hasBell = s.bell && s.bell.unseen_count > 0 &&
      (s.bell.seen_at === null || s.bell.last_fired_at > s.bell.seen_at);
    var isActive = s.name === _viewingSession && (s.remoteId ?? '') === (_viewingRemoteId ?? '');
    var escapedName = escapeHtml(s.name || '');
    var remoteIdAttr = s.remoteId != null ? ' data-remote-id="' + escapeHtml(s.remoteId) + '"' : '';
    return '<li class="sheet-item' + (isActive ? ' sheet-item--active' : '') + '"' +
      ' data-session="' + escapedName + '"' + remoteIdAttr + ' role="option">' +
      '<span class="sheet-item__name">' + escapedName + '</span>' +
      (hasBell ? '<span class="sheet-item__bell">\uD83D\uDD14</span>' : '') +
      '<span class="sheet-item__time">' + formatTimestamp(s.bell && s.bell.last_fired_at) + '</span>' +
      '</li>';
  }).join('');

  list.querySelectorAll('.sheet-item').forEach(function(item) {
    item.addEventListener('click', function() {
      closeBottomSheet();
      var name = item.dataset.session;
      var remoteId = item.dataset.remoteId || '';
      if (name !== _viewingSession || remoteId !== (_viewingRemoteId ?? '')) openSession(name, { remoteId: remoteId });
    });
  });
}

/**
 * Update the session pill bell badge when in fullscreen view.
 * Shows #session-pill-bell if any other session (not currently viewed) has unseen bells.
 * @param {object[]} sessions - full sessions array
 */
function updateSessionPill(sessions) {
  if (_viewMode !== 'fullscreen') return;
  var pillBell = $('session-pill-bell');
  if (!pillBell) return;
  var viewingKey = _viewingRemoteId ? (_viewingRemoteId + ':' + _viewingSession) : _viewingSession;
  var othersWithBell = sessions.filter(function(s) {
    return (s.sessionKey || s.name) !== viewingKey &&
      s.bell && s.bell.unseen_count > 0 &&
      (s.bell.seen_at === null || s.bell.last_fired_at > s.bell.seen_at);
  });
  if (othersWithBell.length > 0) {
    pillBell.classList.remove('hidden');
  } else {
    pillBell.classList.add('hidden');
  }
}

// ─── Header + button with inline name input ────────────────────────────────────

/**
 * Attribute/value pairs that suppress browser and password-manager autofill
 * on a bare, form-less text input.
 *
 * `autocomplete="off"` alone is NOT enough. These fields are form-less text
 * inputs with name-ish placeholders (a session name, a view name, a device
 * name) served from an origin that also serves a real login form
 * (login.html) — which is exactly the shape password managers heuristically
 * treat as a username field, and they ignore `autocomplete="off"` on that
 * shape by design. So we also send each vendor's documented per-field
 * opt-out attribute. The autocorrect / autocapitalize pair additionally
 * covers the mobile PWA path (e.g. the FAB new-session overlay), where iOS
 * otherwise capitalizes and "corrects" names as you type them.
 *
 * This object is the single source of truth for the attribute list. The two
 * static inputs in index.html (terminal search, device name) duplicate this
 * list as literal HTML attributes ON PURPOSE — Chrome scans the DOM for
 * autofill targets at parse time, before any of our JS runs, so attributes
 * applied later via `_suppressAutofill` would be too late for those two
 * fields. A test pins the markup copies in sync with this constant so they
 * can't silently drift apart.
 *
 * Do NOT apply this to genuine credential fields — the federation key input
 * in `_buildRemoteInstanceRow` and both inputs on login.html are the
 * deliberate exceptions where a password manager is wanted.
 *
 * @type {Record<string, string>}
 */
const AUTOFILL_SUPPRESSION_ATTRS = {
  autocomplete: 'off',
  autocorrect: 'off',
  autocapitalize: 'off',
  'data-1p-ignore': 'true',   // 1Password
  'data-lpignore': 'true',    // LastPass
  'data-bwignore': 'true',    // Bitwarden
  'data-form-type': 'other',  // Dashlane
};

/**
 * Apply autofill suppression to a form-less text input by setting every
 * attribute in AUTOFILL_SUPPRESSION_ATTRS plus disabling spellcheck (these
 * fields hold names/URLs, not prose). See AUTOFILL_SUPPRESSION_ATTRS for why
 * `autocomplete="off"` alone isn't sufficient.
 *
 * @param {HTMLInputElement} input
 * @returns {HTMLInputElement} the same input, for chaining
 */
function _suppressAutofill(input) {
  for (const attr of Object.keys(AUTOFILL_SUPPRESSION_ATTRS)) {
    input.setAttribute(attr, AUTOFILL_SUPPRESSION_ATTRS[attr]);
  }
  input.spellcheck = false;
  return input;
}

/**
 * Create a new session name input element with shared base configuration.
 * Used by both showNewSessionInput (inline) and showFabSessionInput (overlay)
 * to avoid duplicating the setup properties.
 *
 * @returns {HTMLInputElement}
 */
function _createSessionInput() {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'new-session-input';
  input.placeholder = 'Session name\u2026';
  return _suppressAutofill(input);
}

/**
 * Create an optional device <select> for multi-device session creation.
 * Returns null when multi_device_enabled is false or remote_instances is empty.
 * @returns {HTMLSelectElement|null}
 */
function _createDeviceSelect() {
  const ss = _serverSettings || {};
  const remotes = ss.remote_instances;
  if (!ss.multi_device_enabled || !remotes || remotes.length === 0) {
    return null;
  }

  const select = document.createElement('select');
  select.className = 'new-session-device-select';

  // Local device option
  const localOpt = document.createElement('option');
  localOpt.value = '';
  localOpt.textContent = ss.device_name || 'Local';
  select.appendChild(localOpt);

  // Remote instance options
  for (var i = 0; i < remotes.length; i++) {
    var opt = document.createElement('option');
    opt.value = remotes[i].device_id || String(i);
    opt.textContent = remotes[i].name || remotes[i].url || 'Remote ' + i;
    if (_activeFilterDevice === remotes[i].name || _activeFilterDevice === remotes[i].url) {
      opt.selected = true;
      select.value = remotes[i].device_id || String(i);
    }
    select.appendChild(opt);
  }

  return select;
}

/**
 * Create an optional command-pair <select> for session creation.
 * Returns null when fewer than two pairs are configured (or the list has not
 * loaded) -- at one pair the create control is byte-identical to pre-feature
 * muxplex, which is the point. Deliberately the same shape and same
 * null-means-omit contract as _createDeviceSelect() above, so both callers
 * (showNewSessionInput, showFabSessionInput) handle them identically.
 * @returns {HTMLSelectElement|null}
 */
function _createCommandSelect() {
  // §6 item 6 of the design doc: a malformed pair doesn't just vanish from
  // this picker silently (indistinguishable from "never configured") -- if
  // there are configuration errors, render the select (even at <2 valid
  // pairs) so a non-selectable warning row can point at Settings > Commands.
  var hasErrors = (_sessionCommandErrors || []).length > 0;
  if ((!_sessionCommands || _sessionCommands.length < 2) && !hasErrors) {
    return null;
  }

  const select = document.createElement('select');
  select.className = 'new-session-command-select';

  var cmds = _sessionCommands || [];
  for (var i = 0; i < cmds.length; i++) {
    var cmd = cmds[i];
    var opt = document.createElement('option');
    opt.value = cmd.id;
    opt.textContent = cmd.label;
    // Resolves the "Default" label ambiguity: hovering shows the actual
    // create command, regardless of what the label says.
    opt.title = cmd.new_session_template;
    select.appendChild(opt);
  }
  if (hasErrors) {
    var warnOpt = document.createElement('option');
    warnOpt.disabled = true;
    var n = _sessionCommandErrors.length;
    warnOpt.textContent = '\u26a0 ' + n + ' pair' + (n === 1 ? '' : 's') +
      ' failed to load \u2014 see Settings \u203a Commands';
    select.appendChild(warnOpt);
  }
  // First real option ("default") selected by default -- no persistence of
  // the last choice; a sticky selection that silently changes what "create"
  // does is a footgun nobody has asked for.
  select.value = cmds.length ? cmds[0].id : '';

  return select;
}

/**
 * Replace the header + button with an inline text input (and optional device
 * select) for session naming. Hides the button, inserts controls before it,
 * and focuses the input.
 * On Enter: if name is non-empty after trim, calls createNewSession(name, remoteId).
 * On Escape: restores the button (cleanup only).
 * On blur: delayed cleanup (150ms) to allow click handlers.
 * @param {HTMLElement} btn - The button element to replace temporarily.
 */
function showNewSessionInput(btn) {
  const select = _createDeviceSelect();
  const cmdSelect = _createCommandSelect();
  const input = _createSessionInput();

  function cleanup() {
    if (select && select.parentNode) select.parentNode.removeChild(select);
    if (cmdSelect && cmdSelect.parentNode) cmdSelect.parentNode.removeChild(cmdSelect);
    if (input.parentNode) input.parentNode.removeChild(input);
    btn.style.display = '';
  }

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      const name = input.value.trim();
      const remoteId = select ? select.value : '';
      const commandId = cmdSelect ? cmdSelect.value : '';
      cleanup();
      if (name) createNewSession(name, remoteId, commandId);
    } else if (e.key === 'Escape') {
      cleanup();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(function() {
      // Don't close if focus moved to the device select dropdown or the
      // command-pair select
      if (select && document.activeElement === select) return;
      if (cmdSelect && document.activeElement === cmdSelect) return;
      cleanup();
    }, 150);
  });

  if (select) {
    select.addEventListener('blur', function() {
      setTimeout(function() {
        // Don't close if focus moved back to the name input or the
        // command-pair select
        if (document.activeElement === input) return;
        if (cmdSelect && document.activeElement === cmdSelect) return;
        cleanup();
      }, 150);
    });
    select.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') { cleanup(); }
    });
    // Disabling (not hiding -- no layout shift) the command select when a
    // remote device is chosen is the honest UI: a local command_id is
    // meaningless (and likely a 400) on a remote peer (spec §7.4).
    if (cmdSelect) {
      select.addEventListener('change', function() {
        cmdSelect.disabled = !!select.value;
      });
    }
  }

  if (cmdSelect) {
    cmdSelect.addEventListener('blur', function() {
      setTimeout(function() {
        // Don't close if focus moved back to the name input or the device select
        if (document.activeElement === input) return;
        if (select && document.activeElement === select) return;
        cleanup();
      }, 150);
    });
    cmdSelect.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') { cleanup(); }
    });
  }

  btn.style.display = 'none';
  if (select) btn.parentNode.insertBefore(select, btn);
  if (cmdSelect) btn.parentNode.insertBefore(cmdSelect, btn);
  btn.parentNode.insertBefore(input, btn);
  input.focus();
}

/**
 * Show a fixed-position input overlay for creating a new session from the mobile FAB.
 * Unlike showNewSessionInput (which inserts inline into btn.parentNode), this renders
 * a fixed-position overlay appended directly to document.body — ensuring it is always
 * visible on mobile regardless of body/view overflow:hidden constraints.
 */
function showFabSessionInput() {
  if (document.querySelector('.fab-input-overlay')) return;

  const fab = $('new-session-fab');

  const overlay = document.createElement('div');
  overlay.className = 'fab-input-overlay';

  const select = _createDeviceSelect();
  const cmdSelect = _createCommandSelect();
  const input = _createSessionInput();

  if (select) overlay.appendChild(select);
  if (cmdSelect) overlay.appendChild(cmdSelect);
  overlay.appendChild(input);

  function cleanup() {
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    if (fab) fab.style.display = '';
  }

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      const name = input.value.trim();
      const remoteId = select ? select.value : '';
      const commandId = cmdSelect ? cmdSelect.value : '';
      cleanup();
      if (name) createNewSession(name, remoteId, commandId);
    } else if (e.key === 'Escape') {
      cleanup();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(function() {
      // Don't close if focus moved to the device select dropdown or the
      // command-pair select
      if (select && document.activeElement === select) return;
      if (cmdSelect && document.activeElement === cmdSelect) return;
      cleanup();
    }, 150);
  });

  if (select) {
    select.addEventListener('blur', function() {
      setTimeout(function() {
        // Don't close if focus moved back to the name input or the
        // command-pair select
        if (document.activeElement === input) return;
        if (cmdSelect && document.activeElement === cmdSelect) return;
        cleanup();
      }, 150);
    });
    select.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') { cleanup(); }
    });
    if (cmdSelect) {
      select.addEventListener('change', function() {
        cmdSelect.disabled = !!select.value;
      });
    }
  }

  if (cmdSelect) {
    cmdSelect.addEventListener('blur', function() {
      setTimeout(function() {
        // Don't close if focus moved back to the name input or the device select
        if (document.activeElement === input) return;
        if (select && document.activeElement === select) return;
        cleanup();
      }, 150);
    });
    cmdSelect.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') { cleanup(); }
    });
  }

  if (fab) fab.style.display = 'none';
  document.body.appendChild(overlay);
  input.focus();
}

/**
 * Create a new tmux session via POST /api/sessions.
 * Shows a toast, then polls _currentSessions until the session name appears
 * (or times out after 30s) before calling openSession — this handles commands
 * that take time to create the tmux session (e.g. cloning repos, setup scripts).
 * If auto_open_created is false in server settings, skips the auto-open.
 * @param {string} name - The session name to create.
 * @returns {Promise<void>}
 */
async function createNewSession(name, remoteId, commandId) {
  var deviceId = remoteId || '';  // Accept device_id string (was integer index in old protocol)
  try {
    var endpoint = deviceId ? '/api/federation/' + encodeURIComponent(deviceId) + '/sessions' : '/api/sessions';
    var body = { name: name };
    // Omit entirely when unset -- a pre-feature-shaped body is what keeps an
    // un-picked create byte-identical to before. Also omit for a REMOTE
    // create: command ids are namespaced to the host that defines them, so
    // OUR id is meaningless (and likely a 400) on the peer. The remote uses
    // its own default -- identical to today.
    if (commandId && !deviceId) body.command_id = commandId;
    const res = await api('POST', endpoint, body);
    const data = await res.json();
    const sessionName = data.name || name;

    // Auto-add to active user view (not 'all' or 'hidden')
    if (_activeView !== 'all' && _activeView !== 'hidden') {
      var views = (_serverSettings && _serverSettings.views) || [];
      var viewIdx = -1;
      for (var vi = 0; vi < views.length; vi++) {
        if (views[vi].name === _activeView) { viewIdx = vi; break; }
      }
      if (viewIdx >= 0) {
        var newSessionKey = remoteId ? (remoteId + ':' + sessionName) : sessionName;
        if (!remoteId && _localDeviceId) {
          newSessionKey = _localDeviceId + ':' + sessionName;
        }
        patchSettingsGuarded(function(fresh) {
          var freshViews = JSON.parse(JSON.stringify((fresh && fresh.views) || []));
          var freshIdx = -1;
          for (var fi = 0; fi < freshViews.length; fi++) {
            if (freshViews[fi].name === _activeView) { freshIdx = fi; break; }
          }
          if (freshIdx >= 0 && !freshViews[freshIdx].sessions.includes(newSessionKey)) {
            freshViews[freshIdx].sessions.push(newSessionKey);
          }
          return { views: freshViews };
        })
          .then(function(body) {
            if (_serverSettings) _serverSettings.views = body.views;
          })
          .catch(function(err) {
            console.warn('[createNewSession] auto-add to view failed:', err);
          });
      }
    }

    showToast('Creating session \'' + sessionName + '\'…');

    // Inject a loading placeholder tile so the user sees feedback immediately
    var loadingTile = null;
    var grid = document.getElementById('session-grid');
    if (grid) {
      loadingTile = document.createElement('div');
      loadingTile.className = 'session-tile tile--loading';
      loadingTile.id = 'loading-tile-' + sessionName;
      loadingTile.innerHTML =
        '<div class="tile-header"><span class="tile-name">' + escapeHtml(sessionName) + '</span>' +
        '<span class="tile-meta">Creating...</span></div>' +
        '<div class="tile-body"><pre class="loading-pulse"></pre></div>';
      grid.appendChild(loadingTile);
    }

    function removeLoadingTile() {
      var tile = document.getElementById('loading-tile-' + sessionName);
      if (tile) tile.remove();
    }

    const ss = _serverSettings || {};
    if (ss.auto_open_created === false) {
      // Auto-open disabled — just do one refresh
      await pollSessions();
      removeLoadingTile();
      return;
    }

    // Compute expectedKey: for remote sessions, use 'deviceId:sessionName' (sessionKey format)
    var expectedKey = deviceId ? (deviceId + ':' + sessionName) : sessionName;

    // Poll until the session appears in _currentSessions (max 30s, every 2s)
    var attempts = 0;
    var maxAttempts = 15;
    var pollForSession = setInterval(async function() {
      attempts++;
      await pollSessions();
      var found = _currentSessions && _currentSessions.find(function(s) {
        return (s.sessionKey || s.name) === expectedKey;
      });
      if (found) {
        clearInterval(pollForSession);
        removeLoadingTile();
        showToast('Session \'' + sessionName + '\' ready');
        openSession(sessionName, { remoteId: deviceId });
      } else if (attempts >= maxAttempts) {
        clearInterval(pollForSession);
        removeLoadingTile();
        showToast('Session \'' + sessionName + '\' is taking longer than expected');
      }
    }, 2000);
  } catch (err) {
    showToast(err.message || 'Failed to create session');
  }
}

/**
 * Kill a tmux session by name via DELETE /api/sessions/{name}.
 * For remote sessions, proxies through the federation delete route.
 * Shows a confirmation dialog before killing. Refreshes the session list on success.
 * @param {string} name - The session name to kill.
 * @param {string} [remoteId] - Remote instance index (empty or absent for local).
 */
function killSession(name, remoteId) {
  var endpoint = remoteId
    ? '/api/federation/' + encodeURIComponent(remoteId) + '/sessions/' + encodeURIComponent(name)
    : '/api/sessions/' + encodeURIComponent(name);
  api('DELETE', endpoint)
    .then(function() {
      showToast('Session \'' + name + '\' killed');
      // If we deleted the session we're currently viewing, return to dashboard
      if (_viewingSession === name && (_viewingRemoteId ?? '') === (remoteId || '')) {
        closeSession();
      }
      pollSessions();
    })
    .catch(function(err) {
      showToast('Failed to kill session: ' + (err.message || 'unknown error'));
    });
}

/**
 * Bind all static (once-only) event listeners for the app UI.
 * Called once after restoreState() resolves.
 */
function bindStaticEventListeners() {
  // Delegated ⋮ options button handler (tiles are re-rendered each poll)
  document.addEventListener('click', function(e) {
    var optionsBtn = e.target.closest && e.target.closest('.tile-options-btn');
    if (!optionsBtn) return;
    openFlyoutMenu(optionsBtn);
  });

  on($('back-btn'), 'click', closeSession);

  // View dropdown — trigger opens/closes, delegated item clicks switch view
  var viewDropdownTrigger = $('view-dropdown-trigger');
  if (viewDropdownTrigger) on(viewDropdownTrigger, 'click', toggleViewDropdown);

  var viewDropdownMenu = $('view-dropdown-menu');
  if (viewDropdownMenu) {
    viewDropdownMenu.addEventListener('click', function(e) {
      var item = e.target.closest('[data-view]');
      if (item) {
        switchView(item.dataset.view);
        return;
      }
      var action = e.target.closest('[data-action]');
      if (action) {
        if (action.dataset.action === 'new-view') {
          showNewViewInput();
        } else if (action.dataset.action === 'manage-view') {
          closeViewDropdown();
          openManageViewPanel();
        } else if (action.dataset.action === 'manage-views') {
          closeViewDropdown();
          openSettings();
          switchSettingsTab('views');
        } else {
          closeViewDropdown();
        }
      }
    });
  }

  // Sidebar view dropdown — trigger opens/closes, delegated item clicks switch view
  var sidebarViewTrigger = $('sidebar-view-dropdown-trigger');
  if (sidebarViewTrigger) on(sidebarViewTrigger, 'click', toggleSidebarViewDropdown);

  var sidebarViewMenu = $('sidebar-view-dropdown-menu');
  if (sidebarViewMenu) {
    sidebarViewMenu.addEventListener('click', function(e) {
      var item = e.target.closest('[data-view]');
      if (item) {
        switchView(item.dataset.view);
        // Close sidebar dropdown after selection
        sidebarViewMenu.classList.add('hidden');
        if (sidebarViewTrigger) sidebarViewTrigger.setAttribute('aria-expanded', 'false');
        return;
      }
      var action = e.target.closest('[data-action]');
      if (action) {
        if (action.dataset.action === 'new-view') {
          showSidebarNewViewInput();
        } else if (action.dataset.action === 'manage-view') {
          sidebarViewMenu.classList.add('hidden');
          if (sidebarViewTrigger) sidebarViewTrigger.setAttribute('aria-expanded', 'false');
          openManageViewPanel();
        } else if (action.dataset.action === 'manage-views') {
          sidebarViewMenu.classList.add('hidden');
          if (sidebarViewTrigger) sidebarViewTrigger.setAttribute('aria-expanded', 'false');
          openSettings();
          switchSettingsTab('views');
        } else {
          sidebarViewMenu.classList.add('hidden');
          if (sidebarViewTrigger) sidebarViewTrigger.setAttribute('aria-expanded', 'false');
        }
      }
    });
  }

  // Click-outside closes the header view dropdown
  document.addEventListener('click', function(e) {
    var dropdown = $('view-dropdown-menu');
    if (!dropdown || dropdown.classList.contains('hidden')) return;
    var trigger = $('view-dropdown-trigger');
    if (trigger && trigger.contains(e.target)) return;
    // Don't close if a new-view input was just created (replaceChild removes the click target from DOM)
    if (dropdown.querySelector('.view-dropdown__new-input')) return;
    if (!dropdown.contains(e.target)) closeViewDropdown();
  });

  // Click-outside closes the sidebar view dropdown
  document.addEventListener('click', function(e) {
    var sidebarDropdown = $('sidebar-view-dropdown-menu');
    if (!sidebarDropdown || sidebarDropdown.classList.contains('hidden')) return;
    var sidebarTrigger = $('sidebar-view-dropdown-trigger');
    if (sidebarTrigger && sidebarTrigger.contains(e.target)) return;
    // Don't close if a new-view input was just created (replaceChild removes the click target from DOM)
    if (sidebarDropdown.querySelector('.view-dropdown__new-input')) return;
    if (!sidebarDropdown.contains(e.target)) {
      sidebarDropdown.classList.add('hidden');
      if (sidebarTrigger) sidebarTrigger.setAttribute('aria-expanded', 'false');
    }
  });

  // Sort dropdown (header) — trigger opens/closes, delegated item clicks apply
  // sort. Same pattern as the view dropdown above (v0.47.9: converted from a
  // native <select>'s 'change' event to this click-based dropdown mechanism).
  var sortDropdownTrigger = $('sort-order-select');
  if (sortDropdownTrigger) on(sortDropdownTrigger, 'click', toggleSortDropdown);

  var sortDropdownMenu = $('sort-order-menu');
  if (sortDropdownMenu) {
    sortDropdownMenu.addEventListener('click', function(e) {
      var item = e.target.closest('[data-sort]');
      if (!item) return;
      selectSortOrder(item.dataset.sort);
      closeSortDropdown();
    });
  }

  // Sort dropdown (sidebar) — same pattern, fixed-positioned like the sidebar view dropdown.
  var sidebarSortDropdownTrigger = $('sidebar-sort-order-select');
  if (sidebarSortDropdownTrigger) on(sidebarSortDropdownTrigger, 'click', toggleSidebarSortDropdown);

  var sidebarSortDropdownMenu = $('sidebar-sort-order-menu');
  if (sidebarSortDropdownMenu) {
    sidebarSortDropdownMenu.addEventListener('click', function(e) {
      var item = e.target.closest('[data-sort]');
      if (!item) return;
      selectSortOrder(item.dataset.sort);
      closeSidebarSortDropdown();
    });
  }

  // Click-outside closes the header sort dropdown
  document.addEventListener('click', function(e) {
    var dropdown = $('sort-order-menu');
    if (!dropdown || dropdown.classList.contains('hidden')) return;
    var trigger = $('sort-order-select');
    if (trigger && trigger.contains(e.target)) return;
    if (!dropdown.contains(e.target)) closeSortDropdown();
  });

  // Click-outside closes the sidebar sort dropdown
  document.addEventListener('click', function(e) {
    var sidebarDropdown = $('sidebar-sort-order-menu');
    if (!sidebarDropdown || sidebarDropdown.classList.contains('hidden')) return;
    var sidebarTrigger = $('sidebar-sort-order-select');
    if (sidebarTrigger && sidebarTrigger.contains(e.target)) return;
    if (!sidebarDropdown.contains(e.target)) closeSidebarSortDropdown();
  });

  var newSessionBtn = $('new-session-btn');
  if (newSessionBtn) on(newSessionBtn, 'click', function() { showNewSessionInput(newSessionBtn); });
  var sidebarNewSessionBtn = $('sidebar-new-session-btn');
  if (sidebarNewSessionBtn) on(sidebarNewSessionBtn, 'click', function() { showNewSessionInput(sidebarNewSessionBtn); });
  var newSessionFab = $('new-session-fab');
  if (newSessionFab) on(newSessionFab, 'click', showFabSessionInput);
  on($('sidebar-toggle-btn'), 'click', toggleSidebar);
  bindSidebarClickAway();
  document.addEventListener('keydown', handleGlobalKeydown);
  on($('session-pill'), 'click', openBottomSheet);
  on($('sheet-backdrop'), 'click', closeBottomSheet);

  // Settings dialog bindings
  on($('view-mode-btn'), 'click', cycleViewMode);
  on($('settings-btn'), 'click', openSettings);
  on($('settings-btn-expanded'), 'click', openSettings);
  on($('settings-close-btn'), 'click', closeSettings);
  on($('settings-backdrop'), 'click', closeSettings);
  const settingsDialog = $('settings-dialog');
  if (settingsDialog) {
    settingsDialog.addEventListener('cancel', closeSettings);
    // Click on the ::backdrop area (outside dialog content) dismisses settings
    settingsDialog.addEventListener('click', function(e) {
      if (e.target === settingsDialog) closeSettings();
    });
  }
  document.querySelectorAll('.settings-tab').forEach(function(tab) {
    on(tab, 'click', function() { switchSettingsTab(tab.dataset.tab); });
  });

  // First-run welcome dialog — three actions, all of which mark the flag
  // seen (see _firstRunMarkSeen()). Escape (<dialog>'s native 'cancel'
  // event) and a backdrop click both route to the same "Not now" path, so
  // every possible way out is a no-op that still never nags again.
  on($('firstrun-enable-typing-btn'), 'click', _firstRunEnableTyping);
  on($('firstrun-open-federation-btn'), 'click', _firstRunOpenFederationSettings);
  on($('firstrun-dismiss-btn'), 'click', _firstRunDismiss);
  on($('firstrun-backdrop'), 'click', _firstRunDismiss);
  const firstrunDialog = $('firstrun-dialog');
  if (firstrunDialog) {
    firstrunDialog.addEventListener('cancel', _firstRunDismiss);
    firstrunDialog.addEventListener('click', function(e) {
      if (e.target === firstrunDialog) _firstRunDismiss();
    });
  }

  // Hover preview — delegated on grid container (tiles are re-rendered each poll)
  var gridEl = $('session-grid');
  if (gridEl && !('ontouchstart' in window)) {  // desktop only
    gridEl.addEventListener('mouseenter', function (e) {
      var tile = e.target.closest('.session-tile');
      if (!tile) return;
      if (_previewTimer) { clearTimeout(_previewTimer); _previewTimer = null; }
      var name = tile.dataset.session;
      var delay = getDisplaySettings().hoverPreviewDelay;
      if (delay > 0) _previewTimer = setTimeout(function () { showPreview(name); }, delay);
    }, true);  // useCapture: true for delegation with mouseenter

    gridEl.addEventListener('mouseleave', function (e) {
      var tile = e.target.closest('.session-tile');
      if (!tile) return;
      hidePreview();
    }, true);
  }

  // Hover preview — delegated on sidebar list (items are re-rendered each poll)
  var sidebarListEl = $('sidebar-list');
  if (sidebarListEl && !('ontouchstart' in window)) {  // desktop only
    sidebarListEl.addEventListener('mouseenter', function (e) {
      var item = e.target.closest('.sidebar-item');
      if (!item) return;
      if (_previewTimer) { clearTimeout(_previewTimer); _previewTimer = null; }
      var name = item.dataset.session;
      var delay = getDisplaySettings().hoverPreviewDelay;
      if (delay > 0) _previewTimer = setTimeout(function () { showPreview(name); }, delay);
    }, true);

    sidebarListEl.addEventListener('mouseleave', function (e) {
      var item = e.target.closest('.sidebar-item');
      if (!item) return;
      hidePreview();
    }, true);
  }

  // Display settings — bind change events for immediate apply
  on($('setting-font-size'), 'change', onDisplaySettingChange);
  on($('setting-hover-delay'), 'change', onDisplaySettingChange);
  on($('setting-grid-columns'), 'change', onDisplaySettingChange);
  on($('setting-device-label-placement'), 'change', onDisplaySettingChange);
  on($('setting-activity-indicator'), 'change', onDisplaySettingChange);
  on($('setting-view-mode'), 'change', function() {
    var el = $('setting-view-mode');
    if (el) {
      saveGridViewMode(el.value);
      renderGrid(_currentSessions || []);
    }
  });


  // Sessions settings — bind change events for server-side persistence
  on($('setting-default-session'), 'change', function() {
    var el = $('setting-default-session');
    if (el) patchServerSetting('default_session', el.value);
  });
  // Shared handler: all three sort-order selects (header quick-sort, sidebar
  // quick-sort, this Settings one) write the same sort_order setting and
  // re-sync each other -- see onSortOrderChange()/syncSortOrderControls().
  on($('setting-sort-order'), 'change', onSortOrderChange);
  on($('setting-window-size-largest'), 'change', function() {
    var el = $('setting-window-size-largest');
    if (el) patchServerSetting('window_size_largest', el.checked);
  });
  on($('setting-auto-open'), 'change', function() {
    var el = $('setting-auto-open');
    if (el) patchServerSetting('auto_open_created', el.checked);
  });

  // Notifications settings — bell sound toggle persists to server settings
  on($('setting-bell-sound'), 'change', function() {
    if (_serverSettings) _serverSettings.bellSound = this.checked;
    patchServerSetting('bellSound', this.checked);
  });

  // Notifications settings — permission request button
  on($('notification-request-btn'), 'click', function() {
    if (typeof Notification === 'undefined') return;
    Notification.requestPermission().then(function(permission) {
      _notificationPermission = permission;
      // Update UI state
      const statusEl = $('notification-status-text');
      const reqBtn = $('notification-request-btn');
      if (statusEl && reqBtn) {
        _updateNotificationUI(statusEl, reqBtn, permission);
      }
    }).catch(function(err) {
      console.error('Notification.requestPermission() failed:', err);
    });
  });

  // Commands tab — create template textarea is READ-ONLY: new_session_template
  // is a server-side shell command (settings.LOCAL_ONLY_KEYS), so PATCH
  // /api/settings silently ignores it. No input/reset handlers are bound here
  // on purpose — the textarea only ever displays the current server value
  // (see openSettings()); it is edited by hand in settings.json.

  // Terminal tab -- tmux theme select and copy-mode radios.
  // CONSTRAINED VOCABULARY ONLY: the <select> options come exclusively from
  // GET /api/tmux-config's available_themes (server-owned list), and the
  // two radios are the only two values PATCH /api/tmux-config accepts.
  // There is deliberately no free-text field here -- see main.py's
  // update_tmux_config() docstring for why.
  on($('setting-tmux-theme'), 'change', function() {
    patchTmuxConfig({ theme: this.value });
  });
  on($('setting-tmux-copy-mode-desktop'), 'change', function() {
    if (this.checked) patchTmuxConfig({ copy_mode: 'desktop' });
  });
  on($('setting-tmux-copy-mode-vi'), 'change', function() {
    if (this.checked) patchTmuxConfig({ copy_mode: 'vi' });
  });

  // Agent tab -- send/newline chord (muxplex-18f). Writes through
  // window.muxplexAgentPrefs, which persists to localStorage AND immediately
  // re-writes the composer's byline hint, aria-keyshortcuts and Send tooltip.
  // Deliberately NOT patchServerSetting(): this is a per-device preference and
  // must never reach the server or federation sync. Deliberately NOT a direct
  // localStorage.setItem() either -- chat.js owns the key and the redraw, and
  // a second writer here would let the hint and the handler disagree.
  on($('setting-agent-send-mode-newline'), 'change', function() {
    const prefs = window.muxplexAgentPrefs;
    if (this.checked && prefs) prefs.setSendMode(prefs.SEND_MODE_NEWLINE);
  });
  on($('setting-agent-send-mode-send'), 'change', function() {
    const prefs = window.muxplexAgentPrefs;
    if (this.checked && prefs) prefs.setSendMode(prefs.SEND_MODE_SEND);
  });

  // Terminal tab -- "Show the generated config" disclosure. Collapsed by default
  // (see index.html): the preview is an advanced/optional reference, not something
  // the target (non-technical) audience needs open by default.
  on($('tmux-preview-toggle-btn'), 'click', function() {
    const wrap = $('tmux-preview-wrap');
    if (!wrap) return;
    const nowHidden = !wrap.classList.contains('hidden');
    wrap.classList.toggle('hidden', nowHidden);
    this.setAttribute('aria-expanded', String(!nowHidden));
    this.textContent = nowHidden ? 'Show the generated config' : 'Hide the generated config';
  });

  // Multi-Device tab — enable/disable toggle
  on($('setting-multi-device-enabled'), 'change', function() {
    var enabled = this.checked;
    _updateMultiDeviceFieldsState(enabled);
    patchServerSetting('multi_device_enabled', enabled);
  });

  // Devices tab — "Independent view" checkbox. Deliberately OUTSIDE
  // #multi-device-fields: that block is gated on multi_device_enabled (the
  // federation display toggle), which has nothing to do with sync groups.
  on($('setting-independent-view'), 'change', function() {
    setSyncGroup(this.checked ? 'device' : 'global');
  });

  // Header "Follows" <select>s (overview + expanded header) -- Step 4
  // replacement for the link/broken-link icon toggle buttons (§9.4).
  // Both route through handleFollowsSelectChange(); renderSyncGroupControls()
  // keeps every widget in sync -- same pattern as the three sort selects
  // (syncSortOrderControls()).
  on($('sync-group-select'), 'change', function() {
    handleFollowsSelectChange(this.value);
  });
  on($('sync-group-select-expanded'), 'change', function() {
    handleFollowsSelectChange(this.value);
  });

  // Decks tab (§9.5): inline device-name rename, PATCH-on-blur/Enter.
  // blur does not bubble, so this listener is registered in the capture
  // phase on the static container (rebuilt via innerHTML on every render,
  // so delegation here -- not per-input binding -- is required).
  var decksListEl = $('decks-registered-list');
  if (decksListEl) {
    decksListEl.addEventListener('blur', function(e) {
      var input = e.target;
      if (input && input.classList && input.classList.contains('decks-device-name')) {
        _patchDeviceDisplayName(input);
      }
    }, true);
    decksListEl.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && e.target && e.target.classList && e.target.classList.contains('decks-device-name')) {
        e.target.blur();
      }
    });
  }

  // Compose bar -- toggle button, send button, textarea keydown/auto-grow.
  _bindComposeEventListeners();

  // Multi-Device tab — device name with 500ms debounce; updates document.title immediately
  var _deviceNameDebounceTimer;
  on($('setting-device-name'), 'input', function() {
    clearTimeout(_deviceNameDebounceTimer);
    var val = this.value;
    // Update cached setting immediately so updatePageTitle() sees the new value
    if (_serverSettings) _serverSettings.device_name = val;
    updatePageTitle();
    _deviceNameDebounceTimer = setTimeout(function() {
      patchServerSetting('device_name', val);
    }, 500);
  });

  // Multi-Device tab — federation generate key button
  on($('federation-generate-btn'), 'click', function() {
    api('POST', '/api/federation/generate-key')
      .then(function(res) { return res.json(); })
      .then(function(data) {
        var displayEl = $('federation-key-display');
        if (displayEl && data && data.key) {
          displayEl.textContent = data.key;
          displayEl.classList.add('settings-key-display--visible');
        }
        showToast('Federation key generated');
      }).catch(function() {
        showToast('Failed to generate federation key');
      });
  });

  // Multi-Device tab — one-click "Enable typing for this fleet" button.
  // See _enableFederationTypingForFleet() for the patch logic (turns
  // input_enabled on, widens input_allowed_sessions to ["*"] only if it
  // was empty) and why no credential check is needed here (the server-side
  // OPERATOR_SETTABLE_LOCAL_KEYS fence already does it).
  on($('federation-enable-typing-btn'), 'click', function() {
    _enableFederationTypingForFleet();
  });

  // Devices tab — Agent Terminal Input enable/disable toggle. See this
  // key's SECURITY note above _updateInputAllowedSessionsFieldVisibility()
  // for why this file needs no credential-type check of its own: PATCH
  // /api/settings already 403s a Bearer-only (federation/agent) caller for
  // this key server-side.
  on($('setting-input-enabled'), 'change', function() {
    var enabled = this.checked;
    _updateInputAllowedSessionsFieldVisibility(enabled);
    patchServerSetting('input_enabled', enabled).then(function() {
      // Refresh the compose-bar gate immediately -- no page reload -- now
      // that _serverSettings.input_enabled reflects the (attempted) write.
      _composeRenderEnabledState();
    });
  });

  // Devices tab — add allowed-session glob pattern button
  on($('add-input-allowed-session-btn'), 'click', function() {
    var container = $('setting-input-allowed-sessions');
    if (container) container.appendChild(_buildListEditorRow('', '* or agent-*'));
  });

  // Devices tab — delegated remove + debounced-save handlers on the
  // allowed-sessions list editor, mirroring the remote-instances handlers
  // immediately below.
  var inputAllowedSessionsContainer = $('setting-input-allowed-sessions');
  if (inputAllowedSessionsContainer) {
    inputAllowedSessionsContainer.addEventListener('click', function(e) {
      var removeBtn = e.target.closest && e.target.closest('.settings-list-editor-remove');
      if (!removeBtn) return;
      var row = removeBtn.closest('.settings-list-editor-row');
      if (row) {
        row.remove();
        _saveInputAllowedSessions();
      }
    });

    let _inputAllowedSessionsDebounceTimer;
    inputAllowedSessionsContainer.addEventListener('input', function(e) {
      var input = e.target.closest && e.target.closest('.settings-list-editor-value');
      if (!input) return;
      clearTimeout(_inputAllowedSessionsDebounceTimer);
      _inputAllowedSessionsDebounceTimer = setTimeout(function() {
        _saveInputAllowedSessions();
      }, 500);
    });
  }

  // Multi-Device tab — add remote instance button
  on($('add-remote-instance-btn'), 'click', function() {
    var container = $('setting-remote-instances');
    if (container) {
      container.appendChild(_buildRemoteInstanceRow('', '', ''));
    }
  });

  // Multi-Device tab — delegated remove handler on remote instances container
  var remoteInstancesContainer = $('setting-remote-instances');
  if (remoteInstancesContainer) {
    remoteInstancesContainer.addEventListener('click', function(e) {
      var removeBtn = e.target.closest && e.target.closest('.settings-remote-remove');
      if (!removeBtn) return;
      var row = removeBtn.closest('.settings-remote-row');
      if (row) {
        row.remove();
        _saveRemoteInstances();
      }
    });

    // Delegated input save with debounce for remote instance URL/name fields
    let _remoteDebounceTimer;
    remoteInstancesContainer.addEventListener('input', function(e) {
      var input = e.target.closest && e.target.closest('.settings-remote-url, .settings-remote-name, .settings-remote-key');
      if (!input) return;
      clearTimeout(_remoteDebounceTimer);
      _remoteDebounceTimer = setTimeout(function() {
        _saveRemoteInstances();
      }, 500);
    });
  }

  // Commands tab — delete template textarea is READ-ONLY: delete_session_template
  // is a server-side shell command (settings.LOCAL_ONLY_KEYS), so PATCH
  // /api/settings silently ignores it. No input/reset handlers are bound here
  // on purpose — the textarea only ever displays the current server value
  // (see openSettings()); it is edited by hand in settings.json.
}

// ─── Test-only helpers ────────────────────────────────────────────────────────

/** Test-only: set _currentSessions directly. */
function _setCurrentSessions(sessions) {
  _currentSessions = sessions;
}

/** Test-only: set _viewMode directly. */
function _setViewMode(mode) {
  _viewMode = mode;
}

/** Test-only: set _serverSettings directly. */
function _setServerSettings(settings) {
  _serverSettings = settings;
}

/** Test-only: set _sttStatus directly, bypassing the async availability
 * check -- lets tests drive _sttRenderButton()/_sttHandleClick() without a
 * real SpeechRecognition.available(). */
function _setSttStatus(status) {
  _sttStatus = status;
}

/** Test-only: get _sttStatus. */
function _getSttStatus() {
  return _sttStatus;
}

/** Test-only: set _sttMode directly ('ondevice' | 'cloud' | null),
 * bypassing the async availability check. */
function _setSttMode(mode) {
  _sttMode = mode;
}

/** Test-only: get _sttMode. */
function _getSttMode() {
  return _sttMode;
}

/** Test-only: get the current _sttState. */
function _getSttState() {
  return _sttState;
}

/** Test-only: get whether #compose-cloud-consent is currently pending. */
function _getSttConsentPending() {
  return _sttConsentPending;
}

/** Test-only: get/set the live _sttRecognition handle, so a test can
 * install a fake recognition object and assert _sttStop()/_sttForceStop()
 * call the right method on it. */
function _setSttRecognition(recognition) {
  _sttRecognition = recognition;
}
function _getSttRecognition() {
  return _sttRecognition;
}

/** Test-only: reset the transcript-insertion tracking directly (normally
 * only set by _sttStart() at the top of a real session), so each test can
 * start _sttApplyTranscript()/_sttHandleResult() from a known position
 * instead of inheriting whatever the previous test left behind. */
function _setSttInsertState(pos, interimLength) {
  _sttInsertPos = pos;
  _sttInterimLength = interimLength || 0;
}

/** Test-only: set _sessionCommands / _sessionCommandErrors directly,
 * bypassing loadSessionCommands(). */
function _setSessionCommands(commands, errors) {
  _sessionCommands = commands;
  _sessionCommandErrors = errors || [];
}

/** Test-only: get _serverSettings. */
function _getServerSettings() {
  return _serverSettings;
}

/** Test-only: get _gridViewMode. */
function _getGridViewMode() {
  return _gridViewMode;
}

/** Test-only: set _gridViewMode directly. */
function _setGridViewMode(mode) {
  // 'filtered' was removed in the Views feature — fall back to 'flat'
  if (mode === 'filtered') mode = 'flat';
  _gridViewMode = mode;
}

/** Test-only: set _activeFilterDevice directly. */
function _setActiveFilterDevice(device) {
  _activeFilterDevice = device;
}

/** Test-only: get current _activeView value. */
function _getActiveView() { return _activeView; }

/** Test-only: set _activeView directly. */
function _setActiveView(view) { _activeView = view; }

// Recalculate fit layout on window resize
window.addEventListener('resize', function() {
  var ds = getDisplaySettings();
  if ((ds.viewMode || 'auto') === 'fit') {
    var grid = document.getElementById('session-grid');
    if (grid) applyFitLayout(grid);
  }
  // A width change can flip isMobile() for an 'auto'-mode compose preference
  // (e.g. rotating a tablet, or resizing a desktop window past the
  // threshold) -- recompute visibility and re-render the toggle so both
  // stay correct without requiring the user to touch anything.
  _composeRenderToggle();
  _composeRender();
});

// ─── Release any inherited screen-orientation lock ─────────────────────────
//
// CORRECTED (post-v0.26.0): v0.26.0 shipped this unlock() call while the
// manifest still declared "orientation": "any" and reasoned that the
// manifest was "not the cause" of forced/free rotation. That reasoning was
// wrong. Per the Web App Manifest spec (orientation member), "any"
// affirmatively "allows the app to rotate freely to match the orientation
// of the device" -- on Android this is baked into the installed PWA's
// WebAPK Activity as a sensor-based screenOrientation, and Android's own
// docs on those modes are explicit that "the sensor is used even if the
// user has locked sensor-based rotation." That is precisely the reported
// bug: free rotation that overrides the phone's rotation lock. The
// manifest (../manifest.json) now omits "orientation" entirely, which per
// spec makes the app "typically use the device's natural orientation and
// any user or system-level orientation settings" -- i.e. behave like a
// normal browser tab (matching Edge) and honor the system rotation lock.
//
// This call is KEPT as defense-in-depth, not as the fix. This dashboard
// and /deck/ are two separate PWAs served from the same origin; the main
// manifest's scope is "/" (a superset of /deck/'s "/deck/" scope), so
// reaching /deck/ from inside this installed app's own window can stay in
// the same top-level browsing context. /deck/'s boot calls
// screen.orientation.lock('landscape') (see deck/deck.js) and there is no
// unlock() there. Spec text says browsers "revert to this default
// orientation whenever the top-level browsing context is navigated," which
// should clear a prior lock on returning here -- but that is "typically",
// not a guarantee on every engine/OS combination, and this app's manifest
// no longer declares any orientation preference for unlock() to revert to,
// so calling it unconditionally on boot is a safe no-op that cannot
// reintroduce free rotation. Same defensive guards as deck.js's lock call:
// never assume the API exists, never let this throw and break boot
// (unlock() can throw InvalidStateError/SecurityError per spec).
function releaseInheritedOrientationLock() {
  if (
    typeof screen === 'undefined' ||
    !screen.orientation ||
    typeof screen.orientation.unlock !== 'function'
  ) {
    return;
  }
  try {
    screen.orientation.unlock();
  } catch (err) {
    // Expected whenever nothing was locked or the context disallows it --
    // never let this break boot.
  }
}
releaseInheritedOrientationLock();

document.addEventListener('DOMContentLoaded', async function() {
  initDeviceId();
  initSyncGroup();
  _followTarget = loadFollowTarget();
  // Fire-and-forget: _sttInit() never throws (every failure inside it
  // resolves to "leave the mic button hidden"), so nothing here needs to
  // await or .catch() it.
  _sttInit();

  // Load ALL settings (now includes display + sidebar) before first render
  await loadServerSettings();

  // muxplex-fx1: composeBarOpen (like sidebarOpen) lives in _serverSettings,
  // so its resolve-a-default-and-migrate-legacy-localStorage step must run
  // AFTER loadServerSettings() above, not before it -- unlike sidebarOpen,
  // whose equivalent step (initSidebar()) runs later still, at first
  // session-open, the compose toggle button is rendered right here at page
  // load, so this cannot be deferred the same way.
  initComposePref();
  _composeRenderToggle();
  // Resolved session command pairs -- not polled (pairs change only when the
  // operator edits settings.json). A failed fetch degrades to the one-pair
  // create UI (today's behavior), never blocks session creation.
  loadSessionCommands();
  // Resolved view-rule validation errors -- same "not polled, fetched once
  // plus on relevant triggers" treatment as loadSessionCommands() above.
  loadViewRules();
  // Seed the change-detection baseline so the first /api/state poll doesn't
  // trigger a redundant re-fetch in followRemoteViewDefinitions().
  _lastSettingsUpdatedAt = (_serverSettings && _serverSettings.settings_updated_at) || 0;
  // Initialize the header/sidebar quick-sort selects from the loaded setting.
  syncSortOrderControls();

  // Cache local device_id + version from /api/instance-info. device_id feeds
  // session key construction; version populates the read-only Settings >
  // Display "Version" field (reference info, not fetched again on dialog
  // open — set directly on the element the moment this resolves).
  api('GET', '/api/instance-info').then(function(res) {
    return res.json();
  }).then(function(info) {
    if (info && info.device_id) _localDeviceId = info.device_id;
    if (info && info.version) {
      _localVersion = info.version;
      var versionEl = $('setting-app-version');
      if (versionEl) versionEl.textContent = 'v' + info.version;
    }
    // §4.2/§9.1: this server's own display name, for the "<name> (shared)"
    // escape hatch and the Decks tab's "Registered with <name>" heading.
    // Best-effort: buildFollowsMenu/renderDecksSettingsTab already fall
    // back to 'this server' when empty.
    if (info && info.name) {
      _serverName = info.name;
      renderSyncGroupControls();
    }
  }).catch(function() { /* non-critical — local session key falls back to plain name */ });

  var _initDs = getDisplaySettings();
  applyDisplaySettings(_initDs);
  _gridViewMode = loadGridViewMode();

  // Initialize view mode button title
  var vmBtn = document.getElementById('view-mode-btn');
  if (vmBtn) vmBtn.title = 'View: ' + (_initDs.viewMode || 'auto');

  document.addEventListener('keydown', trackInteraction);
  document.addEventListener('click', trackInteraction);
  document.addEventListener('touchstart', trackInteraction);

  restoreState()
    .then(function() {
      startPolling();
      startStatePolling();
      updatePageTitle();
      startHeartbeat();
      bindStaticEventListeners();
      // First-run welcome -- MUST come after bindStaticEventListeners(),
      // which wires its three buttons; showing it before that would put a
      // modal on screen whose controls do nothing. Shows at most once per
      // browser (see _firstRunMaybeShow()).
      _firstRunMaybeShow();
      renderSyncGroupControls();
      renderViewDropdown();
      // Step 6 (§6.2.7): local sections have just rendered above via
      // renderSyncGroupControls(); the federated section starts empty and
      // fills in whenever this first fetch resolves -- never blocking or
      // delaying anything above.
      startFederatedDevicesPolling();
      // Update sidebar label after restoreState sets _activeView (Issue 7)
      var sidebarLabelEl = $('sidebar-view-label');
      if (sidebarLabelEl) {
        if (_activeView === 'all') sidebarLabelEl.textContent = 'All Sessions';
        else if (_activeView === 'hidden') sidebarLabelEl.textContent = 'Hidden';
        else sidebarLabelEl.textContent = _activeView;
      }
    })
    .catch(function(err) {
      console.error('[init] restoreState failed, retrying in 5s:', err);
      setTimeout(function() { startPolling(); startStatePolling(); }, POLL_MS);
    });
});

// Conditional CommonJS export — must remain at the very bottom of this file.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    formatTimestamp,
    sessionPriority,
    sortByPriority,
    sortByAttention,
    applySortOrder,
    filterByQuery,
    detectBellTransitions,
    generateDeviceId,
    buildHeartbeatPayload,
    // Sync groups
    syncGroupId,
    initSyncGroup,
    withDevice,
    setSyncGroup,
    renderSyncGroupControls,
    // Follows: registered-device pairing (Step 4)
    FOLLOW_TARGET_STORAGE_KEY,
    deviceDisplayLabel,
    degradedOptionLabel,
    computeFollowsDegraded,
    buildFollowsMenu,
    followsCandidateFromValue,
    targetNotSelfOwningMessage,
    currentFollows,
    sanitizeFollowTarget,
    persistFollowTarget,
    loadFollowTarget,
    resolveSyncGroupForWire,
    attemptFollowTarget,
    handleFollowsSelectChange,
    renderControlledByChip,
    renderDecksSettingsTab,
    _describeDeviceFollows,
    _buildDecksDeviceRow,
    _patchDeviceDisplayName,
    _setFollowTargetForTests,
    _setDevicesRegistryForTests,
    _setServerNameForTests,
    _setLastHeartbeatGoneIdForTests,
    // Federated device discovery (Step 6: §6.2.7-§6.2.10, §8.1 #11/#12)
    FEDERATED_DEVICES_POLL_MS,
    resolveFederatedPeerUrl,
    buildFederatedDevicesSection,
    openFederatedPeer,
    fetchFederatedDevices,
    startFederatedDevicesPolling,
    _buildDecksFederatedRow,
    _setFederatedDevicesRawForTests,
    _setServerSettingsForTests,
    // Compose bar
    COMPOSE_PREF_STORAGE_KEY,
    initComposePref,
    _composeEffectiveOn,
    _composeSetPref,
    _composeToggle,
    _composeRenderToggle,
    _composeRender,
    _composeRenderEnabledState,
    _composeHideError,
    _composeShowError,
    _composeClearDraft,
    _composeOnSessionOpen,
    _composeOnSessionClose,
    _composeNormalizeText,
    _composeAutoGrow,
    _composeKeydown,
    _followupsQueueKeydown,
    _composeErrorMessage,
    _composeSend,
    _bindComposeEventListeners,
    // On-device dictation (STT)
    STT_PHRASE_BOOST,
    STT_MAX_PHRASES,
    STT_CLOUD_CONSENT_STORAGE_KEY,
    _sttCtor,
    _sttCheckAvailability,
    _sttPhraseSourceTerms,
    _sttBuildPhrases,
    _sttRenderButton,
    _sttSetState,
    _sttCloudConsentGranted,
    _sttShowCloudConsent,
    _sttHideCloudConsent,
    _sttRenderConsent,
    _sttCloudConsentAllow,
    _sttCloudConsentCancel,
    _sttProceedToStart,
    _sttApplyTranscript,
    _sttHandleResult,
    _sttHandleError,
    _sttHandleEnd,
    _sttStart,
    _sttStop,
    _sttForceStop,
    _sttInstallThenStart,
    _sttHandleClick,
    _sttInit,
    _setSttStatus,
    _getSttStatus,
    _setSttMode,
    _getSttMode,
    _getSttState,
    _getSttConsentPending,
    _setSttRecognition,
    _getSttRecognition,
    _setSttInsertState,
    _followupsRefresh,
    _followupsRender,
    _followupsQueueDraft,
    _followupsReorder,
    _followupsEditItem,
    _followupsRemoveItem,
    _followupsClearAll,
    _followupsResume,
    _followupsRemoveHalted,
    _followupsSetDataForTests,
    showTerminalConflictDialog,
    setConnectionStatus,
    pollSessions,
    followRemoteActiveSession,
    followRemoteActiveView,
    followRemoteViewDefinitions,
    pollActiveState,
    startPolling,
    stopPolling,
    startStatePolling,
    stopStatePolling,
    handleVisibilityChange,
    escapeHtml,
    formatDeviceVersion,
    deviceLabelPlacement,
    DEVICE_LABEL_PLACEMENTS,
    buildTileHTML,
    buildSidebarHTML,
    getVisibleSessions,
    renderSidebar,
    initSidebar,
    toggleSidebar,
    bindSidebarClickAway,
    renderGrid,
    renderGroupedGrid,
    requestNotificationPermission,
    handleBellTransitions,
    sendHeartbeat,
    startHeartbeat,
    _resetHeartbeatTimer,
    showToast,
    updatePillBell,
    openSession,
    closeSession,
    getFocusedSessionName,
    _setViewingSession,
    _setViewingRemoteId,
    _setPendingLocalSwitches,
    _setPendingViewSwitches,
    _setSyncGroupMode,
    _setDeviceId,
    handleGlobalKeydown,
    bindStaticEventListeners,
    openBottomSheet,
    closeBottomSheet,
    renderSheetList,
    updateSessionPill,
    updatePageTitle,
    updateFaviconBadge,
    // ANSI color rendering
    ansiToHtml,
    ansiParamsToStyle,
    ansi256Color,
    // Hover preview popover
    showPreview,
    hidePreview,
    // Settings
    getDisplaySettings,
    applyDisplaySettings,
    loadGridViewMode,
    saveGridViewMode,
    applyFitLayout,
    cycleViewMode,
    onDisplaySettingChange,
    openSettings,
    closeSettings,
    switchSettingsTab,
    // Server settings
    loadServerSettings,
    patchServerSetting,
    patchSettingsGuarded,
    // Generic list editor + Agent Terminal Input (input_enabled / input_allowed_sessions)
    _buildListEditorRow,
    _serializeListEditor,
    _saveInputAllowedSessions,
    _updateInputAllowedSessionsFieldVisibility,
    _enableFederationTypingPatch,
    _enableFederationTypingForFleet,
    // First-run welcome (one-time)
    FIRSTRUN_STORAGE_KEY,
    _firstRunShouldShow,
    _firstRunMarkSeen,
    _firstRunOpen,
    _firstRunClose,
    _firstRunMaybeShow,
    _firstRunEnableTyping,
    _firstRunOpenFederationSettings,
    _firstRunDismiss,
    // Fetch wrapper
    api,
    // Header + button with inline name input
    AUTOFILL_SUPPRESSION_ATTRS,
    _suppressAutofill,
    _createDeviceSelect,
    showNewSessionInput,
    showFabSessionInput,
    createNewSession,
    // Kill session
    killSession,
    // Manage View panel
    openManageViewPanel,
    closeManageViewPanel,
    renderManageViewList,
    // Manage View rule editor (§9.3)
    _parseViewRulePatterns,
    _renderManageViewRuleFeedback,
    _renderManageViewRuleEditor,
    _previewManageViewRule,
    _clearManageViewRulesPreviewTimer,
    // Flyout menu
    openFlyoutMenu,
    closeFlyoutMenu,
    // Filter bar
    renderFilterBar,
    // Quick dropdown controller (shared mechanism -- view + sort, header + sidebar)
    createQuickDropdown,
    openQuickDropdown,
    closeQuickDropdown,
    toggleQuickDropdown,
    isQuickDropdownOpen,
    handleQuickDropdownKeydown,
    // View dropdown
    renderViewDropdown,
    toggleViewDropdown,
    closeViewDropdown,
    showNewViewInput,
    switchView,
    applyViewLocally,
    persistActiveView,
    // Sidebar view dropdown
    renderSidebarViewDropdown,
    toggleSidebarViewDropdown,
    showSidebarNewViewInput,
    // Quick sort dropdown (header + sidebar)
    SORT_OPTIONS,
    renderSortDropdown,
    toggleSortDropdown,
    closeSortDropdown,
    renderSidebarSortDropdown,
    toggleSidebarSortDropdown,
    closeSidebarSortDropdown,
    selectSortOrder,
    syncSortOrderControls,
    onSortOrderChange,
    // Manage Views settings tab
    renderViewsSettingsTab,
    _saveViewsAndRerender,
    // v2 visibility helpers (Phase 1)
    isHidden,
    filterVisible,
    visibleCount,
    // Operation layer (Phase 2) — pure data ops
    _opAddMembership,
    _opRemoveMembership,
    _opRemoveFromAllViews,
    _opHide,
    _opUnhide,
    _cloneOpState,
    // Operation layer (Phase 2) — user-intent ops
    hideSessionOp,
    unhideSessionOp,
    addSessionToViewOp,
    removeSessionFromViewOp,
    // Federation tiles
    buildStatusTileHTML,
    // Constants
    NEW_SESSION_DEFAULT_TEMPLATE,
    DELETE_SESSION_DEFAULT_TEMPLATE,
    _createCommandSelect,
    createNewSession,
    renderCommandPairsSettings,
    _buildCommandPairRow,
    _shellQuote,
    _commandsAddInvocation,
    _copyToClipboard,
    _openCommandPairComposer,
    _closeCommandPairComposer,
    _renderCommandComposerOutput,
    _updateCommandErrorBadges,
    _setSessionCommands,
    // Test-only helpers
    _setCurrentSessions,
    _setViewMode,
    _setServerSettings,
    _getServerSettings,
    _getGridViewMode,
    _setGridViewMode,
    _setActiveFilterDevice,
    _getActiveView,
    _setActiveView,
  };
}

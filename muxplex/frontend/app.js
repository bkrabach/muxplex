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
 * Builds a Map of previous session names to their unseen_count, then returns
 * the names of next sessions whose bell.unseen_count > 0 AND > the previous count.
 * @param {object[]} prev - previous sessions array
 * @param {object[]} next - updated sessions array
 * @returns {string[]} names of sessions that newly have or increased bell count
 */
function detectBellTransitions(prev, next) {
  const prevMap = new Map(
    (prev || []).map((s) => [s.name, (s.bell && s.bell.unseen_count) || 0]),
  );
  return (next || [])
    .filter((s) => {
      const unseen = s.bell && s.bell.unseen_count;
      if (!unseen || unseen <= 0) return false;
      const prevCount = prevMap.has(s.name) ? prevMap.get(s.name) : 0;
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
function buildHeartbeatPayload(device_id, viewing_session, view_mode, last_interaction_at) {
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
  };
}

// ─── Runtime constants ────────────────────────────────────────────────────────
const POLL_MS = 2000;
const HEARTBEAT_MS = 5000;
const MOBILE_THRESHOLD = 600;

// ─── App state ────────────────────────────────────────────────────────────────
let _deviceId = '';
let _currentSessions = [];
let _viewingSession = null;
let _viewMode = 'grid';
let _lastInteractionAt = Date.now() / 1000;
let _pollingTimer;
let _heartbeatTimer;
let _notificationPermission = 'default';
let _pollFailCount = 0;
let _previewPopover = null;
let _previewTimer = null;

var _previewSessionName = null;  // track by NAME, not DOM element

// ─── Settings state ───────────────────────────────────────────────────────────
let _settingsOpen = false;
let _serverSettings = null;
const DISPLAY_SETTINGS_KEY = 'muxplex.display';
const DISPLAY_DEFAULTS = {
  fontSize: 14,
  hoverPreviewDelay: 1500,
  gridColumns: 'auto',
  bellSound: false,
  notificationPermission: 'default',
  viewMode: 'auto',
};

var VIEW_MODES = ['auto', 'fit'];
const NEW_SESSION_DEFAULT_TEMPLATE = 'tmux new-session -d -s {name}';
const DELETE_SESSION_DEFAULT_TEMPLATE = 'tmux kill-session -t {name}';

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
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
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
    const res = await api('GET', '/api/state');
    const state = await res.json();
    if (state.active_session) {
      await openSession(state.active_session, { skipAnimation: true });
    }
  } catch (err) {
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
 * Fetch /api/sessions and update the UI. Called by startPolling.
 * @returns {Promise<void>}
 */
async function pollSessions() {
  try {
    const res = await api('GET', '/api/sessions');
    const sessions = await res.json();
    const prev = _currentSessions;
    _currentSessions = sessions;
    _pollFailCount = 0;
    setConnectionStatus('ok');
    renderGrid(sessions);
    renderSidebar(sessions, _viewingSession);
    handleBellTransitions(prev, sessions);
    updateSessionPill(sessions);
    updateFaviconBadge();
  } catch (err) {
    _pollFailCount++;
    setConnectionStatus(_pollFailCount <= 2 ? 'warn' : 'err');
  }
}

/**
 * Start the session polling interval. Guards against double-start.
 */
function startPolling() {
  if (_pollingTimer) return;
  pollSessions();
  _pollingTimer = setInterval(pollSessions, POLL_MS);
}

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
 * Build the HTML string for a single session tile.
 * @param {object} session
 * @param {number} index
 * @param {boolean} mobile
 * @returns {string}
 */
function buildTileHTML(session, index, mobile) {
  const priority = sessionPriority(session);
  const isBell = priority === 'bell';
  const unseen = session.bell && session.bell.unseen_count;

  let classes = 'session-tile';
  if (isBell) classes += ' session-tile--bell';
  if (mobile) classes += ` session-tile--tier-${priority}`;

  const name = session.name || '';
  const escapedName = escapeHtml(name);
  const timeStr = formatTimestamp(session.last_activity_at || null);

  // Bell indicator
  let bellHtml = '';
  if (unseen && unseen > 0) {
    const countStr = unseen > 9 ? '9+' : String(unseen);
    bellHtml = `<span class="tile-bell">${countStr}</span>`;
  }

  // Last N lines of snapshot — show more in fit mode so tall tiles fill
  const snapshot = session.snapshot || '';
  var _tileDs = loadDisplaySettings();
  var _lineCount = (_tileDs.viewMode === 'fit') ? -80 : -20;
  const lastLines = snapshot.split('\n').slice(_lineCount).join('\n');

  return (
    `<article class="${classes}" data-session="${escapedName}" tabindex="0" role="listitem" aria-label="${escapedName}">` +
    `<div class="tile-header">` +
    `<span class="tile-name">${escapeHtml(name)}</span>` +
    `<span class="tile-meta">${bellHtml}<span class="tile-time">${escapeHtml(timeStr)}</span></span>` +
    `</div>` +
    `<div class="tile-body"><pre>${ansiToHtml(lastLines)}</pre></div>` +
    `<button class="tile-delete" data-session="${escapedName}" aria-label="Kill session">&times;</button>` +
    `</article>`
  );
}

/**
 * Build the HTML string for a single session sidebar card.
 * @param {object} session
 * @param {string} currentSession - name of the currently active session
 * @returns {string}
 */
function buildSidebarHTML(session, currentSession) {
  const name = session.name || '';
  const escapedName = escapeHtml(name);
  const isActive = name === currentSession;

  let classes = 'sidebar-item';
  if (isActive) classes += ' sidebar-item--active';

  const unseen = session.bell && session.bell.unseen_count;

  // Bell indicator
  let bellHtml = '';
  if (unseen && unseen > 0) {
    const countStr = unseen > 9 ? '9+' : String(unseen);
    bellHtml = `<span class="tile-bell">${countStr}</span>`;
  }

  // Last 20 lines of snapshot
  const snapshot = session.snapshot || '';
  const lastLines = snapshot.split('\n').slice(-20).join('\n');

  return (
    `<article class="${classes}" data-session="${escapedName}" tabindex="0" role="listitem">` +
    `<div class="sidebar-item-header">` +
    `<span class="sidebar-item-name">${escapedName}</span>` +
    `${bellHtml}` +
    `<button class="sidebar-delete" data-session="${escapedName}" aria-label="Kill session">&times;</button>` +
    `</div>` +
    `<div class="sidebar-item-body"><pre>${ansiToHtml(lastLines)}</pre></div>` +
    `</article>`
  );
}

/**
 * Returns sessions with hidden session names removed.
 * Consolidates the hidden-session filter used by all render paths.
 * @param {object[]} sessions
 * @returns {object[]}
 */
function getVisibleSessions(sessions) {
  const hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  return (sessions || []).filter((s) => !hidden.includes(s.name));
}

/**
 * Render the session sidebar list. Only renders in fullscreen view.
 * Shows empty state when no sessions exist.
 * Binds click handlers on each sidebar-item to switch sessions.
 * @param {object[]} sessions
 * @param {string|null} currentSession - name of the currently active session
 */
function renderSidebar(sessions, currentSession) {
  if (_viewMode !== 'fullscreen') return;

  const list = $('sidebar-list');
  if (!list) return;

  const visible = getVisibleSessions(sessions);

  if (visible.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">No sessions</div>';
    return;
  }

  list.innerHTML = visible.map((session) => buildSidebarHTML(session, currentSession)).join('');

  // Bind click handlers on each sidebar item
  if (typeof list.querySelectorAll === 'function') {
    list.querySelectorAll('.sidebar-item').forEach((item) => {
      const name = item.dataset.session;
      on(item, 'click', () => {
        if (name !== currentSession) openSession(name);
      });
    });
  }

}

const SIDEBAR_KEY = 'muxplex.sidebarOpen';
const SIDEBAR_NARROW_THRESHOLD = 960;

/**
 * Initialise sidebar open/closed state on page load.
 * Reads muxplex.sidebarOpen from localStorage (JSON.parse with try/catch).
 * Defaults to open on wide screens (innerWidth >= 960) when no stored value.
 * Applies sidebar--collapsed class accordingly and persists the initial state.
 */
function initSidebar() {
  let isOpen;
  try {
    const stored = localStorage.getItem(SIDEBAR_KEY);
    if (stored !== null) {
      isOpen = JSON.parse(stored);
    } else {
      isOpen = window.innerWidth >= SIDEBAR_NARROW_THRESHOLD;
    }
  } catch (_) {
    isOpen = window.innerWidth >= SIDEBAR_NARROW_THRESHOLD;
  }

  const sidebar = $('session-sidebar');
  if (sidebar) {
    if (isOpen) {
      sidebar.classList.remove('sidebar--collapsed');
    } else {
      sidebar.classList.add('sidebar--collapsed');
    }
  }

  // Persist initial state
  try {
    localStorage.setItem(SIDEBAR_KEY, JSON.stringify(isOpen));
  } catch (_) { /* blocked — ok */ }
}

/**
 * Toggle the sidebar open/closed state.
 * Reads current state from localStorage, inverts it, persists, applies
 * sidebar--collapsed class, and updates the collapse button text.
 * Button shows ‹ when open, › when closed.
 */
function toggleSidebar() {
  let isOpen;
  try {
    const stored = localStorage.getItem(SIDEBAR_KEY);
    isOpen = stored !== null ? JSON.parse(stored) : true;
  } catch (_) {
    isOpen = true;
  }

  // Invert state
  isOpen = !isOpen;

  // Persist
  try {
    localStorage.setItem(SIDEBAR_KEY, JSON.stringify(isOpen));
  } catch (_) { /* blocked — ok */ }

  // Apply class
  const sidebar = $('session-sidebar');
  if (sidebar) {
    if (isOpen) {
      sidebar.classList.remove('sidebar--collapsed');
    } else {
      sidebar.classList.add('sidebar--collapsed');
    }
  }

  // Update collapse button text (‹ when open, › when closed)
  const collapseBtn = $('sidebar-collapse-btn');
  if (collapseBtn) {
    collapseBtn.textContent = isOpen ? '\u2039' : '\u203a';
  }
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
  const container = $('terminal-container');
  if (!container) return;
  container.addEventListener('click', () => {
    if (window.innerWidth >= SIDEBAR_NARROW_THRESHOLD) return;
    const sidebar = $('session-sidebar');
    if (!sidebar) return;
    if (sidebar.classList.contains('sidebar--collapsed')) return;
    sidebar.classList.add('sidebar--collapsed');
    try {
      localStorage.setItem(SIDEBAR_KEY, JSON.stringify(false));
    } catch (_) { /* blocked — ok */ }
  });
}

/**
 * Render the session grid. Shows empty state when no sessions exist.
 * On mobile, sorts sessions by priority before rendering.
 * Binds click and keydown handlers on each tile.
 * @param {object[]} sessions
 */
function renderGrid(sessions) {
  const grid = $('session-grid');
  const emptyState = $('empty-state');

  const visible = getVisibleSessions(sessions);

  if (visible.length === 0) {
    if (grid) grid.innerHTML = '';
    if (emptyState) emptyState.classList.remove('hidden');
    return;
  }

  if (emptyState) emptyState.classList.add('hidden');

  // Apply sort order from server settings
  const sortOrder = _serverSettings && _serverSettings.sort_order;
  const mobile = isMobile();
  let ordered;
  if (sortOrder === 'alphabetical') {
    ordered = visible.slice().sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  } else {
    // 'recent', 'manual', and default use server-provided order; priority sort on mobile
    ordered = mobile ? sortByPriority(visible) : visible;
  }
  const html = ordered.map((session, index) => buildTileHTML(session, index, mobile)).join('');
  if (grid) grid.innerHTML = html;

  // Bind interaction handlers on each tile
  document.querySelectorAll('.session-tile').forEach((tile) => {
    on(tile, 'click', () => openSession(tile.dataset.session));
    on(tile, 'keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        openSession(tile.dataset.session);
      }
    });
  });

  if (_viewMode === 'fullscreen') {
    updatePillBell();
  }

  // Reapply view mode layout after grid HTML is rebuilt
  var currentDs = loadDisplaySettings();
  var currentMode = currentDs.viewMode || 'auto';
  if (currentMode === 'fit' && grid) {
    grid.classList.add('session-grid--fit');
    requestAnimationFrame(function() {
      applyFitLayout(grid);
      // No scrollTop hack needed — CSS flex + justify-content:flex-end anchors content to bottom
    });
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
  if (name) openSession(name);
}

function showPreview(name) {
  if (!name || !_currentSessions) return;
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
    const payload = buildHeartbeatPayload(_deviceId, effectiveSession, _viewMode, _lastInteractionAt);
    await api('POST', '/api/heartbeat', payload);
  } catch (err) {
    console.warn('[sendHeartbeat] heartbeat failed:', err);
  }
}

/**
 * Start the heartbeat interval. Guards against double-start.
 * Calls sendHeartbeat() immediately, then every HEARTBEAT_MS milliseconds.
 */
function startHeartbeat() {
  if (_heartbeatTimer) return;
  sendHeartbeat();
  _heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_MS);
}

/** Test-only helper: reset heartbeat timer state so tests can exercise startHeartbeat cleanly. */
function _resetHeartbeatTimer() {
  if (_heartbeatTimer) clearInterval(_heartbeatTimer);
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
  const hasBell = _currentSessions.some(
    (s) => s.name !== _viewingSession && s.bell && s.bell.unseen_count > 0,
  );
  if (hasBell) el.classList.remove('hidden'); else el.classList.add('hidden');
}

// ---------------------------------------------------------------------------
// Dynamic favicon — activity dot overlay
// ---------------------------------------------------------------------------

var _originalFavicon = null; // cached original favicon href

/**
 * Update the favicon with an activity dot if any session has unseen bells.
 * Uses a 32x32 canvas to draw the original favicon + a colored circle overlay.
 * Restores the original favicon when there are no unseen bells.
 */
function updateFaviconBadge() {
  var hasActivity = _currentSessions && _currentSessions.some(function (s) {
    return s.bell && s.bell.unseen_count > 0;
  });

  var link = document.querySelector('link[rel="icon"][sizes="32x32"]') ||
             document.querySelector('link[rel="icon"]');
  if (!link) return;

  // Cache the original favicon on first call
  if (!_originalFavicon) _originalFavicon = link.href;

  if (!hasActivity) {
    // Restore original favicon when no activity
    if (link.href !== _originalFavicon) link.href = _originalFavicon;
    return;
  }

  // Draw favicon + activity dot on canvas
  var canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  var img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = function () {
    ctx.drawImage(img, 0, 0, 32, 32);

    // Activity dot — brand amber (same as bell indicator)
    ctx.beginPath();
    ctx.arc(24, 8, 7, 0, 2 * Math.PI); // top-right area
    ctx.fillStyle = '#F1A640';           // var(--bell-color)
    ctx.fill();
    ctx.strokeStyle = '#0D1117';         // var(--bg) — border for contrast
    ctx.lineWidth = 2;
    ctx.stroke();

    link.href = canvas.toDataURL('image/png');
  };
  img.src = _originalFavicon;
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
  hidePreview();
  _viewingSession = name;
  _viewMode = 'fullscreen';

  // Pre-render sidebar with current sessions before first poll tick
  initSidebar();
  renderSidebar(_currentSessions, name);

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
      renderSidebar(_currentSessions, name);
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
  try {
    await api('POST', `/api/sessions/${name}/connect`);
  } catch (err) {
    showToast(err.message || 'Connection failed');
    return closeSession();
  }

  // Wait for animation to finish (may already be done if /connect was slow)
  await animDone;

  // Mount terminal NOW — /connect has completed, new ttyd is serving the correct session
  if (window._openTerminal) window._openTerminal(name);
}

/**
 * Close the current session and return to the grid view.
 * @returns {Promise<void>}
 */
function closeSession() {
  _viewMode = 'grid';
  _viewingSession = null;

  if (window._closeTerminal) window._closeTerminal();

  // Fire-and-forget DELETE
  api('DELETE', '/api/sessions/current').catch(() => {});

  const expanded = $('view-expanded');
  const overview = $('view-overview');
  if (expanded) {
    expanded.classList.add('hidden');
    expanded.classList.remove('view--active');
  }
  if (overview) overview.style.display = '';  // overview uses view--active (no !important), style.display clears fine

  // Reapply fit layout after overview becomes visible again
  var _closDs = loadDisplaySettings();
  if ((_closDs.viewMode || 'auto') === 'fit') {
    var _closGrid = document.getElementById('session-grid');
    if (_closGrid) {
      _closGrid.classList.add('session-grid--fit');
      requestAnimationFrame(function() { applyFitLayout(_closGrid); });
    }
  }

  const pill = $('session-pill');
  if (pill) pill.classList.add('hidden');

  // Restore FAB when returning to overview
  const fab = $('new-session-fab');
  if (fab) fab.classList.remove('hidden');

  return Promise.resolve();
}

/** Test-only helper: set _viewingSession directly. */
function _setViewingSession(name) {
  _viewingSession = name;
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
 * Send a PATCH to /api/settings with a single key/value update.
 * Shows a toast on success or failure.
 * @param {string} key
 * @param {*} value
 * @returns {Promise<void>}
 */
async function patchServerSetting(key, value) {
  try {
    await api('PATCH', '/api/settings', { [key]: value });
    _serverSettings = Object.assign({}, _serverSettings, { [key]: value });
    showToast('Setting saved');
  } catch (err) {
    showToast('Failed to save setting');
    console.warn('[patchServerSetting] failed:', err);
  }
}

// ─── Settings dialog ──────────────────────────────────────────────────────────

/**
 * Load display settings from localStorage, merging with DISPLAY_DEFAULTS.
 * Returns defaults on any error.
 * @returns {object}
 */
function loadDisplaySettings() {
  try {
    const raw = localStorage.getItem(DISPLAY_SETTINGS_KEY);
    if (raw === null) return Object.assign({}, DISPLAY_DEFAULTS);
    const saved = JSON.parse(raw);
    return Object.assign({}, DISPLAY_DEFAULTS, saved);
  } catch (_) {
    return Object.assign({}, DISPLAY_DEFAULTS);
  }
}

/**
 * Save display settings to localStorage.
 * @param {object} settings
 */
function saveDisplaySettings(settings) {
  try {
    localStorage.setItem(DISPLAY_SETTINGS_KEY, JSON.stringify(settings));
  } catch (_) { /* blocked — ok */ }
}

/**
 * Calculate and apply grid layout to fill the viewport exactly (Fit mode).
 * Determines optimal cols × rows based on tile count and available space.
 * @param {Element} grid - The session grid element
 */
function applyFitLayout(grid) {
  var count = grid.querySelectorAll('.session-tile').length;
  if (count === 0) return;

  // Available space — use grid's parent container
  var parent = grid.parentElement;
  var availH = parent ? parent.clientHeight : window.innerHeight;
  var availW = grid.clientWidth;

  // Subtract padding and gap
  var style = getComputedStyle(grid);
  var padT = parseFloat(style.paddingTop) || 0;
  var padB = parseFloat(style.paddingBottom) || 0;
  var padL = parseFloat(style.paddingLeft) || 0;
  var padR = parseFloat(style.paddingRight) || 0;
  var gap = parseFloat(style.gap) || 8;

  var innerW = availW - padL - padR;
  var innerH = availH - padT - padB;

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

  // Tile height from available space
  var tileH = (innerH - gap * (rows - 1)) / rows;

  grid.style.gridTemplateColumns = 'repeat(' + cols + ', 1fr)';
  grid.style.gridTemplateRows = 'repeat(' + rows + ', 1fr)';

  // Override tile height so tiles fill the grid rows
  grid.querySelectorAll('.session-tile').forEach(function(t) {
    t.style.height = tileH + 'px';
  });
}

/**
 * Cycle the dashboard view mode: auto → fit → auto.
 * Persists to localStorage and reapplies display settings.
 */
function cycleViewMode() {
  var ds = loadDisplaySettings();
  var idx = VIEW_MODES.indexOf(ds.viewMode || 'auto');
  ds.viewMode = VIEW_MODES[(idx + 1) % VIEW_MODES.length];
  saveDisplaySettings(ds);
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
  grid.querySelectorAll('.session-tile').forEach(function(t) {
    t.style.removeProperty('height');
  });

  if (mode === 'auto') {
    // Restore grid columns setting
    if (ds.gridColumns === 'auto' || !ds.gridColumns) {
      grid.style.removeProperty('grid-template-columns');
    } else {
      grid.style.gridTemplateColumns = 'repeat(' + ds.gridColumns + ', 1fr)';
    }

  } else if (mode === 'fit') {
    grid.classList.add('session-grid--fit');
    requestAnimationFrame(function() { applyFitLayout(grid); });
  }
}

/**
 * Handle a change event on any Display settings control.
 * Reads current values from form elements, saves via saveDisplaySettings,
 * and applies via applyDisplaySettings immediately.
 */
function onDisplaySettingChange() {
  var ds = loadDisplaySettings();

  var fontSizeEl = document.getElementById('setting-font-size');
  if (fontSizeEl) ds.fontSize = parseInt(fontSizeEl.value, 10) || ds.fontSize;

  var hoverDelayEl = document.getElementById('setting-hover-delay');
  if (hoverDelayEl) ds.hoverPreviewDelay = parseInt(hoverDelayEl.value, 10);

  var gridColumnsEl = document.getElementById('setting-grid-columns');
  if (gridColumnsEl) {
    var raw = gridColumnsEl.value;
    ds.gridColumns = raw === 'auto' ? 'auto' : parseInt(raw, 10);
  }

  saveDisplaySettings(ds);
  applyDisplaySettings(ds);
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
function openSettings() {
  _settingsOpen = true;
  const dialog = $('settings-dialog');
  if (dialog) dialog.showModal();
  const backdrop = $('settings-backdrop');
  if (backdrop) backdrop.classList.remove('hidden');
  const settings = loadDisplaySettings();
  const fontSizeEl = $('setting-font-size');
  if (fontSizeEl) fontSizeEl.value = String(settings.fontSize);
  const hoverDelayEl = $('setting-hover-delay');
  if (hoverDelayEl) hoverDelayEl.value = String(settings.hoverPreviewDelay);
  const gridColumnsEl = $('setting-grid-columns');
  if (gridColumnsEl) gridColumnsEl.value = String(settings.gridColumns);

  // Populate Notifications tab from display settings
  const bellSoundEl = $('setting-bell-sound');
  if (bellSoundEl) bellSoundEl.checked = !!settings.bellSound;

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

    // Sort order
    const sortOrderEl = $('setting-sort-order');
    if (sortOrderEl && ss && ss.sort_order) {
      sortOrderEl.value = ss.sort_order;
    }

    // Hidden sessions checkboxes
    const hiddenSessionsEl = $('setting-hidden-sessions');
    if (hiddenSessionsEl) {
      hiddenSessionsEl.innerHTML = '';
      const hiddenList = (ss && ss.hidden_sessions) || [];
      (_currentSessions || []).forEach(function(s) {
        const name = s.name || '';
        const item = document.createElement('label');
        item.className = 'settings-checkbox-item';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'settings-checkbox';
        cb.value = name;
        cb.checked = hiddenList.includes(name);
        item.appendChild(cb);
        item.appendChild(document.createTextNode(' ' + name));
        hiddenSessionsEl.appendChild(item);
      });
    }

    // Window size largest
    const windowSizeEl = $('setting-window-size-largest');
    if (windowSizeEl) {
      windowSizeEl.checked = !!(ss && ss.window_size_largest);
    }

    // Auto-open
    const autoOpenEl = $('setting-auto-open');
    if (autoOpenEl) {
      autoOpenEl.checked = ss && ss.auto_open !== undefined ? !!ss.auto_open : true;
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
  });
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

/**
 * Global keydown handler.
 * If settings are open: Escape closes settings.
 * Comma key (not in inputs) opens settings.
 * In fullscreen: Escape returns to grid.
 * @param {KeyboardEvent} e
 */
function handleGlobalKeydown(e) {
  if (_settingsOpen) {
    if (e.key === 'Escape') {
      closeSettings();
    }
    return;
  }
  if (e.key === ',' && !e.ctrlKey && !e.metaKey) {
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
      openSettings();
      return;
    }
  }
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
    var isActive = s.name === _viewingSession;
    var escapedName = escapeHtml(s.name || '');
    return '<li class="sheet-item' + (isActive ? ' sheet-item--active' : '') + '"' +
      ' data-session="' + escapedName + '" role="option">' +
      '<span class="sheet-item__name">' + escapedName + '</span>' +
      (hasBell ? '<span class="sheet-item__bell">\uD83D\uDD14</span>' : '') +
      '<span class="sheet-item__time">' + formatTimestamp(s.bell && s.bell.last_fired_at) + '</span>' +
      '</li>';
  }).join('');

  list.querySelectorAll('.sheet-item').forEach(function(item) {
    item.addEventListener('click', function() {
      closeBottomSheet();
      var name = item.dataset.session;
      if (name !== _viewingSession) openSession(name);
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
  var othersWithBell = sessions.filter(function(s) {
    return s.name !== _viewingSession &&
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
 * Create a new session name input element with shared base configuration.
 * Used by both showNewSessionInput (inline) and showFabSessionInput (overlay)
 * to avoid duplicating the five setup properties.
 * @returns {HTMLInputElement}
 */
function _createSessionInput() {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'new-session-input';
  input.placeholder = 'Session name\u2026';
  input.autocomplete = 'off';
  input.spellcheck = false;
  return input;
}

/**
 * Replace the header + button with an inline text input for session naming.
 * Hides the button, inserts the input before it, and focuses it.
 * On Enter: if name is non-empty after trim, calls createNewSession(name).
 * On Escape: restores the button (cleanup only).
 * On blur: delayed cleanup (150ms) to allow click handlers.
 * @param {HTMLElement} btn - The button element to replace temporarily.
 */
function showNewSessionInput(btn) {
  const input = _createSessionInput();

  function cleanup() {
    if (input.parentNode) input.parentNode.removeChild(input);
    btn.style.display = '';
  }

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      const name = input.value.trim();
      cleanup();
      if (name) createNewSession(name);
    } else if (e.key === 'Escape') {
      cleanup();
    }
  });

  input.addEventListener('blur', function () {
    setTimeout(cleanup, 150);
  });

  btn.style.display = 'none';
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

  const input = _createSessionInput();

  overlay.appendChild(input);

  function cleanup() {
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    if (fab) fab.style.display = '';
  }

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      const name = input.value.trim();
      cleanup();
      if (name) createNewSession(name);
    } else if (e.key === 'Escape') {
      cleanup();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(cleanup, 150);
  });

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
async function createNewSession(name) {
  try {
    const res = await api('POST', '/api/sessions', { name });
    const data = await res.json();
    const sessionName = data.name || name;
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

    // Poll until the session appears in _currentSessions (max 30s, every 2s)
    var attempts = 0;
    var maxAttempts = 15;
    var pollForSession = setInterval(async function() {
      attempts++;
      await pollSessions();
      var found = _currentSessions && _currentSessions.find(function(s) {
        return s.name === sessionName;
      });
      if (found) {
        clearInterval(pollForSession);
        removeLoadingTile();
        showToast('Session \'' + sessionName + '\' ready');
        openSession(sessionName);
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
 * Shows a confirmation dialog before killing. Refreshes the session list on success.
 * @param {string} name - The session name to kill.
 */
function killSession(name) {
  if (!confirm('Kill session "' + name + '"?')) return;
  api('DELETE', '/api/sessions/' + name)
    .then(function() {
      showToast('Session \'' + name + '\' killed');
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
  // Delegated kill-session handler (tiles + sidebar items are re-rendered each poll)
  document.addEventListener('click', function(e) {
    var deleteBtn = e.target.closest && e.target.closest('.tile-delete, .sidebar-delete');
    if (!deleteBtn) return;
    e.stopPropagation();
    var name = deleteBtn.dataset.session;
    if (name) killSession(name);
  });

  on($('back-btn'), 'click', closeSession);
  var newSessionBtn = $('new-session-btn');
  if (newSessionBtn) on(newSessionBtn, 'click', function() { showNewSessionInput(newSessionBtn); });
  var sidebarNewSessionBtn = $('sidebar-new-session-btn');
  if (sidebarNewSessionBtn) on(sidebarNewSessionBtn, 'click', function() { showNewSessionInput(sidebarNewSessionBtn); });
  var newSessionFab = $('new-session-fab');
  if (newSessionFab) on(newSessionFab, 'click', showFabSessionInput);
  on($('sidebar-toggle-btn'), 'click', toggleSidebar);
  on($('sidebar-collapse-btn'), 'click', toggleSidebar);
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

  // Hover preview — delegated on grid container (tiles are re-rendered each poll)
  var gridEl = $('session-grid');
  if (gridEl && !('ontouchstart' in window)) {  // desktop only
    gridEl.addEventListener('mouseenter', function (e) {
      var tile = e.target.closest('.session-tile');
      if (!tile) return;
      if (_previewTimer) { clearTimeout(_previewTimer); _previewTimer = null; }
      var name = tile.dataset.session;
      var delay = loadDisplaySettings().hoverPreviewDelay;
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
      var delay = loadDisplaySettings().hoverPreviewDelay;
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

  // Sessions settings — bind change events for server-side persistence
  on($('setting-default-session'), 'change', function() {
    var el = $('setting-default-session');
    if (el) patchServerSetting('default_session', el.value);
  });
  on($('setting-sort-order'), 'change', function() {
    var el = $('setting-sort-order');
    if (el) patchServerSetting('sort_order', el.value);
  });
  on($('setting-window-size-largest'), 'change', function() {
    var el = $('setting-window-size-largest');
    if (el) patchServerSetting('window_size_largest', el.checked);
  });
  on($('setting-auto-open'), 'change', function() {
    var el = $('setting-auto-open');
    if (el) patchServerSetting('auto_open', el.checked);
  });

  // Hidden sessions — delegated handler on container (checkboxes are dynamic)
  var hiddenSessionsContainer = $('setting-hidden-sessions');
  if (hiddenSessionsContainer) {
    hiddenSessionsContainer.addEventListener('change', function(e) {
      var cb = e.target.closest('input[type="checkbox"]');
      if (!cb) return;
      var hidden = [];
      hiddenSessionsContainer.querySelectorAll('input[type="checkbox"]').forEach(function(c) {
        if (c.checked) hidden.push(c.value);
      });
      patchServerSetting('hidden_sessions', hidden);
    });
  }

  // Notifications settings — bell sound toggle persists to display settings localStorage
  on($('setting-bell-sound'), 'change', function() {
    const ds = loadDisplaySettings();
    ds.bellSound = this.checked;
    saveDisplaySettings(ds);
  });

  // Notifications settings — permission request button
  on($('notification-request-btn'), 'click', function() {
    if (typeof Notification === 'undefined') return;
    Notification.requestPermission().then(function(permission) {
      _notificationPermission = permission;
      const ds = loadDisplaySettings();
      ds.notificationPermission = permission;
      saveDisplaySettings(ds);
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

  // Commands tab — create template textarea with 500ms debounce
  var _templateDebounceTimer;
  on($('setting-template'), 'input', function() {
    clearTimeout(_templateDebounceTimer);
    var val = this.value;
    _templateDebounceTimer = setTimeout(function() {
      patchServerSetting('new_session_template', val);
    }, 500);
  });

  // Commands tab — create template reset button restores default
  on($('setting-template-reset'), 'click', function() {
    var el = $('setting-template');
    if (el) el.value = NEW_SESSION_DEFAULT_TEMPLATE;
    patchServerSetting('new_session_template', NEW_SESSION_DEFAULT_TEMPLATE);
  });

  // Commands tab — delete template textarea with 500ms debounce
  var _deleteTemplateDebounceTimer;
  on($('setting-delete-template'), 'input', function() {
    clearTimeout(_deleteTemplateDebounceTimer);
    var val = this.value;
    _deleteTemplateDebounceTimer = setTimeout(function() {
      patchServerSetting('delete_session_template', val);
    }, 500);
  });

  // Commands tab — delete template reset button restores default
  on($('setting-delete-template-reset'), 'click', function() {
    var el = $('setting-delete-template');
    if (el) el.value = DELETE_SESSION_DEFAULT_TEMPLATE;
    patchServerSetting('delete_session_template', DELETE_SESSION_DEFAULT_TEMPLATE);
  });
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

// Recalculate fit layout on window resize
window.addEventListener('resize', function() {
  var ds = loadDisplaySettings();
  if ((ds.viewMode || 'auto') === 'fit') {
    var grid = document.getElementById('session-grid');
    if (grid) requestAnimationFrame(function() { applyFitLayout(grid); });
  }
});

document.addEventListener('DOMContentLoaded', () => {
  initDeviceId();
  var _initDs = loadDisplaySettings();
  applyDisplaySettings(_initDs);

  // Initialize view mode button title
  var vmBtn = document.getElementById('view-mode-btn');
  if (vmBtn) vmBtn.title = 'View: ' + (_initDs.viewMode || 'auto');

  document.addEventListener('keydown', trackInteraction);
  document.addEventListener('click', trackInteraction);
  document.addEventListener('touchstart', trackInteraction);

  restoreState()
    .then(() => {
      startPolling();
      loadServerSettings();
      startHeartbeat();
      bindStaticEventListeners();
    })
    .catch((err) => {
      console.error('[init] restoreState failed, retrying in 5s:', err);
      setTimeout(() => startPolling(), POLL_MS);
    });
});

// Conditional CommonJS export — must remain at the very bottom of this file.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    formatTimestamp,
    sessionPriority,
    sortByPriority,
    filterByQuery,
    detectBellTransitions,
    generateDeviceId,
    buildHeartbeatPayload,
    setConnectionStatus,
    pollSessions,
    startPolling,
    escapeHtml,
    buildTileHTML,
    buildSidebarHTML,
    renderSidebar,
    initSidebar,
    toggleSidebar,
    bindSidebarClickAway,
    renderGrid,
    requestNotificationPermission,
    handleBellTransitions,
    sendHeartbeat,
    startHeartbeat,
    _resetHeartbeatTimer,
    showToast,
    updatePillBell,
    openSession,
    closeSession,
    _setViewingSession,
    handleGlobalKeydown,
    bindStaticEventListeners,
    openBottomSheet,
    closeBottomSheet,
    renderSheetList,
    updateSessionPill,
    // ANSI color rendering
    ansiToHtml,
    ansiParamsToStyle,
    ansi256Color,
    // Hover preview popover
    showPreview,
    hidePreview,
    // Settings
    loadDisplaySettings,
    saveDisplaySettings,
    applyDisplaySettings,
    applyFitLayout,
    cycleViewMode,
    onDisplaySettingChange,
    openSettings,
    closeSettings,
    switchSettingsTab,
    // Server settings
    loadServerSettings,
    patchServerSetting,
    // Header + button with inline name input
    showNewSessionInput,
    showFabSessionInput,
    createNewSession,
    // Kill session
    killSession,
    // Constants
    NEW_SESSION_DEFAULT_TEMPLATE,
    DELETE_SESSION_DEFAULT_TEMPLATE,
    // Test-only helpers
    _setCurrentSessions,
    _setViewMode,
  };
}

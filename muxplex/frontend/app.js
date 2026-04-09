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
 * Builds a Map of previous session keys (sessionKey || name) to their unseen_count, then returns
 * the names of next sessions whose bell.unseen_count > 0 AND > the previous count.
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
      const prevCount = prevMap.has(key) ? prevMap.get(key) : 0;
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
let _viewingRemoteId = '';
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
let _gridViewMode = 'flat';
let _activeFilterDevice = 'all';
const DISPLAY_DEFAULTS = {
  fontSize: 14,
  hoverPreviewDelay: 1500,
  gridColumns: 'auto',
  bellSound: false,
  viewMode: 'auto',
  showDeviceBadges: true,        // show device name labels on tiles/sidebar
  showHoverPreview: true,        // show hover preview popover on tile hover
  activityIndicator: 'both',     // 'none' | 'glow' | 'dot' | 'both'
  gridViewMode: 'flat',          // 'flat' | 'grouped'
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
    const err = new Error(`HTTP ${res.status}: ${res.statusText}`);
    err.status = res.status;
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
      await openSession(state.active_session, {
        skipAnimation: true,
        remoteId: state.active_remote_id || '',
      });
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
    const res = await api('GET', endpoint);
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
    updatePageTitle();
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

  // Device badge — shown inside tile-meta, before timestamp with · separator
  // Shown when multiple sources configured AND session has a device name
  let badgeHtml = '';
  if (_serverSettings && _serverSettings.multi_device_enabled && session.deviceName && ds.showDeviceBadges !== false) {
    badgeHtml = `<span class="device-badge">${escapeHtml(session.deviceName)}</span>`;
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

  const remoteIdAttr = session.remoteId != null ? ` data-remote-id="${escapeHtml(session.remoteId)}"` : '';
  return (
    `<article class="${classes}" data-session="${escapedName}" data-session-key="${escapeHtml(session.sessionKey || name)}"${remoteIdAttr} tabindex="0" role="listitem" aria-label="${escapedName}">` +
    `<div class="tile-header">` +
    `<span class="tile-name">${escapeHtml(name)}</span>` +
    `<span class="tile-meta">${badgeHtml}${badgeHtml ? `<span class="tile-meta-sep">\xb7</span>` : ''}<span class="tile-time">${escapeHtml(timeStr)}</span></span>` +
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

  var ds = getDisplaySettings();
  var actIndicator = ds.activityIndicator !== undefined ? ds.activityIndicator : 'both';

  const unseen = session.bell && session.bell.unseen_count;
  const isBell = unseen && unseen > 0;

  let classes = 'sidebar-item';
  if (isActive) classes += ' sidebar-item--active';
  // Glow (full border + inner glow): applied when actIndicator is 'glow' or 'both'
  if (isBell && (actIndicator === 'glow' || actIndicator === 'both')) classes += ' sidebar-item--bell';
  // Edge bar only (left border amber, no glow): applied when actIndicator is 'dot' or 'both'
  if (isBell && (actIndicator === 'dot' || actIndicator === 'both')) classes += ' sidebar-item--edge-bell';

  // Device badge — shown in header line when multi_device_enabled
  let badgeHtml = '';
  if (_serverSettings && _serverSettings.multi_device_enabled && session.deviceName && ds.showDeviceBadges !== false) {
    badgeHtml = `<span class="device-badge">${escapeHtml(session.deviceName)}</span>`;
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

  return (
    `<article class="${classes}" data-session="${escapedName}" data-remote-id="${escapeHtml(session.remoteId != null ? session.remoteId : '')}" tabindex="0" role="listitem">` +
    `<div class="sidebar-item-header">` +
    `<span class="sidebar-item-name">${escapedName}</span>` +
    badgeHtml +
    `<button class="sidebar-delete" data-session="${escapedName}" aria-label="Kill session">&times;</button>` +
    `</div>` +
    `<div class="sidebar-item-body"><pre>${ansiToHtml(lastLines)}</pre></div>` +
    `</article>`
  );
}

/**
 * Build the HTML string for a generic status tile (auth_failed or unreachable).
 * @param {string} deviceName
 * @param {string} statusText
 * @param {string} statusClass
 * @returns {string}
 */
function buildStatusTileHTML(deviceName, statusText, statusClass) {
  return (
    '<article class="source-tile source-tile--' + statusClass + '">' +
    '<span class="source-tile__name">' + escapeHtml(deviceName || '') + '</span>' +
    '<span class="source-tile__badge">' + escapeHtml(statusText || '') + '</span>' +
    '</article>'
  );
}

/**
 * Returns sessions with hidden session names removed.
 * Only hides LOCAL sessions (those with no remoteId) matching the
 * hidden_sessions list. Remote sessions with the same name remain visible.
 * Consolidates the hidden-session filter used by all render paths.
 * @param {object[]} sessions
 * @returns {object[]}
 */
function getVisibleSessions(sessions) {
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  return (sessions || []).filter(function(s) {
    if (hidden.length > 0 && s.remoteId == null && hidden.includes(s.name)) {
      return false;
    }
    return true;
  });
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

  let html = '';

  if (_serverSettings && _serverSettings.multi_device_enabled) {
    // Group sessions by deviceName when multi_device_enabled
    const groups = new Map();
    for (const session of visible) {
      const deviceName = session.deviceName || 'Unknown';
      if (!groups.has(deviceName)) groups.set(deviceName, []);
      groups.get(deviceName).push(session);
    }

    for (const [deviceName, deviceSessions] of groups) {
      html += `<h4 class="sidebar-device-header">${escapeHtml(deviceName)}</h4>`;
      html += deviceSessions.map((session) => buildSidebarHTML(session, currentSession)).join('');
    }
  } else {
    // Single source: flat list with no device headers
    html = visible.map((session) => buildSidebarHTML(session, currentSession)).join('');
  }

  list.innerHTML = html;

  // Bind click handlers on each sidebar item, passing remoteId
  if (typeof list.querySelectorAll === 'function') {
    list.querySelectorAll('.sidebar-item').forEach((item) => {
      const name = item.dataset.session;
      const remoteId = item.dataset.remoteId || '';
      on(item, 'click', (e) => {
        // Don't navigate when clicking the delete button inside the item
        if (e.target.closest && e.target.closest('.sidebar-delete')) return;
        if (name !== currentSession) openSession(name, { remoteId });
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
 * applies sidebar--collapsed class, and updates the collapse button text.
 * Button shows ‹ when open, › when closed.
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

  var collapseBtn = $('sidebar-collapse-btn');
  if (collapseBtn) collapseBtn.textContent = isOpen ? '\u2039' : '\u203a';
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
    html += '<h3 class="device-group-header">' + escapeHtml(name) + '</h3>';
    var groupSessions = groups[name];
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
  allSessions = allSessions || [];
  // Collect unique device names preserving insertion order
  var devices = [];
  var seen = {};
  for (var i = 0; i < allSessions.length; i++) {
    var dn = allSessions[i].deviceName || 'Unknown';
    if (!seen[dn]) {
      seen[dn] = true;
      devices.push(dn);
    }
  }

  // Build HTML: 'All' pill first, then one pill per device
  var allActive = _activeFilterDevice === 'all' ? ' filter-pill--active' : '';
  var html = '<button class="filter-pill' + allActive + '" data-device="all">All</button>';
  for (var j = 0; j < devices.length; j++) {
    var active = _activeFilterDevice === devices[j] ? ' filter-pill--active' : '';
    html += '<button class="filter-pill' + active + '" data-device="' + escapeHtml(devices[j]) + '">' + escapeHtml(devices[j]) + '</button>';
  }

  container.innerHTML = html;
}

function renderGrid(sessions) {
  var grid = $('session-grid');
  var emptyState = $('empty-state');
  var filterBar = $('filter-bar');

  var visible = getVisibleSessions(sessions);

  // In filtered mode, apply device filter
  if (_gridViewMode === 'filtered' && _activeFilterDevice !== 'all') {
    visible = visible.filter(function(s) { return s.deviceName === _activeFilterDevice; });
  }

  if (visible.length === 0) {
    // Build status tiles for auth_failed/unreachable sessions even when no regular sessions exist
    var statusTilesHtml = '';
    (sessions || []).forEach(function(session) {
      if (session.status === 'auth_failed') statusTilesHtml += buildStatusTileHTML(session.name, 'Auth required', 'auth');
      else if (session.status === 'unreachable') statusTilesHtml += buildStatusTileHTML(session.name, 'Offline', 'offline');
    });
    if (grid) grid.innerHTML = statusTilesHtml;
    // Only show empty-state when there are truly no tiles at all
    if (emptyState) {
      if (statusTilesHtml) emptyState.classList.add('hidden');
      else emptyState.classList.remove('hidden');
    }
    // Show filter bar even when filtered to empty (so user can switch back)
    if (filterBar) {
      if (_gridViewMode === 'filtered') {
        renderFilterBar(filterBar, sessions);
      } else {
        filterBar.innerHTML = '';
      }
    }
    return;
  }

  if (emptyState) emptyState.classList.add('hidden');

  // Apply sort order from server settings
  var sortOrder = _serverSettings && _serverSettings.sort_order;
  var mobile = isMobile();
  var ordered;
  if (sortOrder === 'alphabetical') {
    ordered = visible.slice().sort(function(a, b) { return (a.name || '').localeCompare(b.name || ''); });
  } else {
    // 'recent', 'manual', and default use server-provided order; priority sort on mobile
    ordered = mobile ? sortByPriority(visible) : visible;
  }

  var html;
  if (_gridViewMode === 'grouped') {
    html = renderGroupedGrid(ordered, mobile);
  } else {
    html = ordered.map(function(session, index) { return buildTileHTML(session, index, mobile); }).join('');
  }

  // Append status tiles for auth_failed and unreachable sessions
  var statusTilesHtml = '';
  (sessions || []).forEach(function(session) {
    if (session.status === 'auth_failed') statusTilesHtml += buildStatusTileHTML(session.name, 'Auth required', 'auth');
    else if (session.status === 'unreachable') statusTilesHtml += buildStatusTileHTML(session.name, 'Offline', 'offline');
  });
  if (grid) grid.innerHTML = html + statusTilesHtml;

  // Render filter bar
  if (filterBar) {
    if (_gridViewMode === 'filtered') {
      renderFilterBar(filterBar, sessions);
    } else {
      filterBar.innerHTML = '';
    }
  }

  // Bind interaction handlers on each tile
  document.querySelectorAll('.session-tile').forEach(function(tile) {
    on(tile, 'click', (e) => {
      // Don't navigate when clicking the delete button inside the tile
      if (e.target.closest && e.target.closest('.tile-delete')) return;
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
  var _previewDs = getDisplaySettings();
  if (_previewDs.showHoverPreview === false) return;
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
  var hasActivity = _currentSessions && _currentSessions.some(function (s) {
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
  var count = (_currentSessions || []).filter(function(s) {
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
  hidePreview();
  _viewingSession = name;
  _viewingRemoteId = opts.remoteId != null ? opts.remoteId : '';
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
  var _remoteId = opts.remoteId != null ? opts.remoteId : '';
  try {
    if (_remoteId !== '') {
      // Remote session: route connect POST through same-origin federation proxy
      await api('POST', '/api/federation/' + encodeURIComponent(_remoteId) + '/connect/' + encodeURIComponent(name));
    } else {
      await api('POST', '/api/sessions/' + encodeURIComponent(name) + '/connect');
    }
  } catch (err) {
    showToast(err.message || 'Connection failed');
    return closeSession();
  }

  // Persist active_remote_id so restoreState() can reopen remote sessions after page refresh
  api('PATCH', '/api/state', { active_session: name, active_remote_id: _remoteId || null }).catch(function() {});

  // Fire-and-forget bell-clear for remote sessions — acknowledge bells on the remote server
  if (_remoteId !== '') {
    api('POST', '/api/federation/' + encodeURIComponent(_remoteId) + '/sessions/' + encodeURIComponent(name) + '/bell/clear').catch(function() {});
  }

  // Wait for animation to finish (may already be done if /connect was slow)
  await animDone;

  // Mount terminal NOW — /connect has completed, new ttyd is serving the correct session
  if (window._openTerminal) window._openTerminal(name, _remoteId, getDisplaySettings().fontSize);
}

/**
 * Close the current session and return to the grid view.
 * @returns {Promise<void>}
 */
function closeSession() {
  _viewMode = 'grid';
  _viewingSession = null;

  if (window._closeTerminal) window._closeTerminal();

  // Fire-and-forget DELETE — skip for remote sessions (they don't need to know we stopped watching)
  if (_viewingRemoteId === '') {
    api('DELETE', '/api/sessions/current').catch(function() {});
  }
  // Clear active_remote_id so a page refresh does not attempt to reopen the remote session
  api('PATCH', '/api/state', { active_session: null, active_remote_id: null }).catch(function() {});
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
  var nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'settings-remote-name';
  nameInput.placeholder = 'Device name';
  nameInput.value = name || '';
  nameInput.setAttribute('aria-label', 'Remote instance display name');
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
  return ds.gridViewMode || 'flat';
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

  var showDeviceBadgesEl = document.getElementById('setting-show-device-badges');
  if (showDeviceBadgesEl) ds.showDeviceBadges = showDeviceBadgesEl.checked;

  var showHoverPreviewEl = document.getElementById('setting-show-hover-preview');
  if (showHoverPreviewEl) ds.showHoverPreview = showHoverPreviewEl.checked;

  var activityIndicatorEl = document.getElementById('setting-activity-indicator');
  if (activityIndicatorEl) ds.activityIndicator = activityIndicatorEl.value;

  var patch = {
    fontSize: ds.fontSize,
    hoverPreviewDelay: ds.hoverPreviewDelay,
    gridColumns: ds.gridColumns,
    showDeviceBadges: ds.showDeviceBadges,
    showHoverPreview: ds.showHoverPreview,
    activityIndicator: ds.activityIndicator,
  };
  Object.assign(_serverSettings, patch);
  api('PATCH', '/api/settings', patch)
    .then(function() { showToast('Settings saved'); })
    .catch(function(err) { console.warn('[onDisplaySettingChange] failed:', err); });
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
  const showDeviceBadgesEl = $('setting-show-device-badges');
  if (showDeviceBadgesEl) showDeviceBadgesEl.checked = settings.showDeviceBadges !== false;
  const showHoverPreviewEl = $('setting-show-hover-preview');
  if (showHoverPreviewEl) showHoverPreviewEl.checked = settings.showHoverPreview !== false;
  const activityIndicatorEl = $('setting-activity-indicator');
  if (activityIndicatorEl) activityIndicatorEl.value = settings.activityIndicator || 'both';

  // Populate Sessions tab / bell sound from display settings
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
    var remoteIdAttr = s.remoteId ? ' data-remote-id="' + escapeHtml(s.remoteId) + '"' : '';
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
      if (name !== _viewingSession) openSession(name, { remoteId: remoteId });
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
    opt.value = String(i);
    opt.textContent = remotes[i].name || remotes[i].url || 'Remote ' + i;
    if (_activeFilterDevice === remotes[i].name || _activeFilterDevice === remotes[i].url) {
      opt.selected = true;
      select.value = String(i);
    }
    select.appendChild(opt);
  }

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
  const input = _createSessionInput();

  function cleanup() {
    if (select && select.parentNode) select.parentNode.removeChild(select);
    if (input.parentNode) input.parentNode.removeChild(input);
    btn.style.display = '';
  }

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      const name = input.value.trim();
      const remoteId = select ? select.value : '';
      cleanup();
      if (name) createNewSession(name, remoteId);
    } else if (e.key === 'Escape') {
      cleanup();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(function() {
      // Don't close if focus moved to the device select dropdown
      if (select && document.activeElement === select) return;
      cleanup();
    }, 150);
  });

  if (select) {
    select.addEventListener('blur', function() {
      setTimeout(function() {
        // Don't close if focus moved back to the name input
        if (document.activeElement === input) return;
        cleanup();
      }, 150);
    });
    select.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') { cleanup(); }
    });
  }

  btn.style.display = 'none';
  if (select) btn.parentNode.insertBefore(select, btn);
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
  const input = _createSessionInput();

  if (select) overlay.appendChild(select);
  overlay.appendChild(input);

  function cleanup() {
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    if (fab) fab.style.display = '';
  }

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      const name = input.value.trim();
      const remoteId = select ? select.value : '';
      cleanup();
      if (name) createNewSession(name, remoteId);
    } else if (e.key === 'Escape') {
      cleanup();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(function() {
      // Don't close if focus moved to the device select dropdown
      if (select && document.activeElement === select) return;
      cleanup();
    }, 150);
  });

  if (select) {
    select.addEventListener('blur', function() {
      setTimeout(function() {
        // Don't close if focus moved back to the name input
        if (document.activeElement === input) return;
        cleanup();
      }, 150);
    });
    select.addEventListener('keydown', function(e) {
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
async function createNewSession(name, remoteId) {
  remoteId = remoteId || '';
  try {
    var endpoint = remoteId ? '/api/federation/' + encodeURIComponent(remoteId) + '/sessions' : '/api/sessions';
    const res = await api('POST', endpoint, { name });
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

    // Compute expectedKey: for remote sessions, use 'remoteId:sessionName' (sessionKey format)
    var expectedKey = remoteId ? (remoteId + ':' + sessionName) : sessionName;

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
        openSession(sessionName, { remoteId: remoteId });
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
  if (!confirm('Kill session "' + name + '"?')) return;
  var endpoint = remoteId
    ? '/api/federation/' + encodeURIComponent(remoteId) + '/sessions/' + encodeURIComponent(name)
    : '/api/sessions/' + encodeURIComponent(name);
  api('DELETE', endpoint)
    .then(function() {
      showToast('Session \'' + name + '\' killed');
      // If we deleted the session we're currently viewing, return to dashboard
      if (_viewingSession === name) {
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
  // Delegated kill-session handler (tiles + sidebar items are re-rendered each poll)
  document.addEventListener('click', function(e) {
    var deleteBtn = e.target.closest && e.target.closest('.tile-delete, .sidebar-delete');
    if (!deleteBtn) return;
    e.stopPropagation();
    var name = deleteBtn.dataset.session;
    // Walk up to the tile/sidebar-item to get remoteId for federation routing
    var container = deleteBtn.closest('[data-remote-id]');
    var remoteId = container ? container.dataset.remoteId : '';
    if (name) killSession(name, remoteId);
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
  on($('setting-show-device-badges'), 'change', onDisplaySettingChange);
  on($('setting-show-hover-preview'), 'change', onDisplaySettingChange);
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
    if (el) patchServerSetting('auto_open_created', el.checked);
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

  // Multi-Device tab — enable/disable toggle
  on($('setting-multi-device-enabled'), 'change', function() {
    var enabled = this.checked;
    _updateMultiDeviceFieldsState(enabled);
    patchServerSetting('multi_device_enabled', enabled);
  });

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

  // Filter bar — delegated click handler (pills are re-rendered each poll)
  var filterBarEl = $('filter-bar');
  if (filterBarEl) {
    filterBarEl.addEventListener('click', function(e) {
      var pill = e.target.closest && e.target.closest('.filter-pill');
      if (!pill) return;
      _activeFilterDevice = pill.dataset.device || 'all';
      renderGrid(_currentSessions || []);
    });
  }

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

/** Test-only: set _serverSettings directly. */
function _setServerSettings(settings) {
  _serverSettings = settings;
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
  _gridViewMode = mode;
}

/** Test-only: set _activeFilterDevice directly. */
function _setActiveFilterDevice(device) {
  _activeFilterDevice = device;
}

// Recalculate fit layout on window resize
window.addEventListener('resize', function() {
  var ds = getDisplaySettings();
  if ((ds.viewMode || 'auto') === 'fit') {
    var grid = document.getElementById('session-grid');
    if (grid) applyFitLayout(grid);
  }
});

document.addEventListener('DOMContentLoaded', async function() {
  initDeviceId();

  // Load ALL settings (now includes display + sidebar) before first render
  await loadServerSettings();

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
      updatePageTitle();
      startHeartbeat();
      bindStaticEventListeners();
    })
    .catch(function(err) {
      console.error('[init] restoreState failed, retrying in 5s:', err);
      setTimeout(function() { startPolling(); }, POLL_MS);
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
    _setViewingSession,
    handleGlobalKeydown,
    bindStaticEventListeners,
    openBottomSheet,
    closeBottomSheet,
    renderSheetList,
    updateSessionPill,
    updatePageTitle,
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
    // Fetch wrapper
    api,
    // Header + button with inline name input
    _createDeviceSelect,
    showNewSessionInput,
    showFabSessionInput,
    createNewSession,
    // Kill session
    killSession,
    // Filter bar
    renderFilterBar,
    // Federation tiles
    buildStatusTileHTML,
    // Constants
    NEW_SESSION_DEFAULT_TEMPLATE,
    DELETE_SESSION_DEFAULT_TEMPLATE,
    // Test-only helpers
    _setCurrentSessions,
    _setViewMode,
    _setServerSettings,
    _getServerSettings,
    _getGridViewMode,
    _setGridViewMode,
    _setActiveFilterDevice,
  };
}

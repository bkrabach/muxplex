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
let _viewingSourceUrl = '';
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
let _sources = [];
let _settingsOpen = false;
let _serverSettings = null;
let _gridViewMode = 'flat';
let _activeFilterDevice = 'all';
const DISPLAY_SETTINGS_KEY = 'muxplex.display';
const DISPLAY_DEFAULTS = {
  fontSize: 14,
  hoverPreviewDelay: 1500,
  gridColumns: 'auto',
  bellSound: false,
  notificationPermission: 'default',
};
const NEW_SESSION_DEFAULT_TEMPLATE = 'tmux new-session -d -s {name}';

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
async function api(method, path, body, baseUrl) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let url = path;
  if (baseUrl) {
    url = baseUrl.replace(/\/+$/, '') + path;
    opts.credentials = 'include';
  }
  const res = await fetch(url, opts);
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
 * Calls GET /api/state and, if an active session exists, re-opens it
 * without POSTing to /connect (ttyd is already running).
 * Always resolves — errors are logged as warnings so the app can start normally.
 * @returns {Promise<void>}
 */
async function restoreState() {
  try {
    const res = await api('GET', '/api/state');
    const state = await res.json();
    if (state.active_session) {
      await openSession(state.active_session, { skipConnect: true });
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
 * Fetch sessions from all configured _sources in parallel and update the UI.
 * Falls back to local-only polling when _sources is empty.
 * Called by startPolling.
 * @returns {Promise<void>}
 */
async function pollSessions() {
  // Falls back to local-only if _sources is empty
  if (_sources.length === 0) {
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
    } catch (err) {
      _pollFailCount++;
      setConnectionStatus(_pollFailCount <= 2 ? 'warn' : 'err');
    }
    return;
  }

  // Multi-source parallel polling
  const now = Date.now();
  const fetchResults = await Promise.all(
    _sources.map(async (source) => {
      // Skip sources currently in backoff (nextRetryAt in future)
      if (source.nextRetryAt && now < source.nextRetryAt) {
        return null;
      }
      try {
        const res = await api('GET', '/api/sessions', undefined, source.url || undefined);
        const sessions = await res.json();
        // Reset source status on success
        source.status = 'authenticated';
        source.backoffMs = 2000;
        delete source.nextRetryAt;
        return { source, sessions };
      } catch (err) {
        if (err.status === 401 || err.status === 403) {
          source.status = 'auth_required';
        } else {
          source.status = 'unreachable';
          // Exponential backoff: current * 2, capped at 30s
          const newBackoff = Math.min((source.backoffMs || 2000) * 2, 30000);
          source.backoffMs = newBackoff;
          source.nextRetryAt = Date.now() + newBackoff;
        }
        return null;
      }
    }),
  );

  // Filter skipped/failed sources and merge results
  const validResults = fetchResults.filter((r) => r !== null);
  const merged = mergeSources(validResults);

  // Connection status is based on local source health
  const localSource = _sources.find((s) => s.type === 'local' || s.url === '');
  if (localSource && localSource.status === 'authenticated') {
    _pollFailCount = 0;
    setConnectionStatus('ok');
  } else {
    _pollFailCount++;
    setConnectionStatus(_pollFailCount <= 2 ? 'warn' : 'err');
  }

  const prev = _currentSessions;
  _currentSessions = merged;
  renderGrid(merged);
  renderSidebar(merged, _viewingSession);
  handleBellTransitions(prev, merged);
  updateSessionPill(merged);
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

  // Device badge (shown when multiple sources configured and session has a device name)
  const badgeHtml = _sources.length > 1 && session.deviceName
    ? `<span class="device-badge">${escapeHtml(session.deviceName)}</span>`
    : '';

  // Last 20 lines of snapshot
  const snapshot = session.snapshot || '';
  const lastLines = snapshot.split('\n').slice(-20).join('\n');

  return (
    `<article class="${classes}" data-session="${escapedName}" data-session-key="${escapeHtml(session.sessionKey || name)}" data-source-url="${escapeHtml(session.sourceUrl || '')}" tabindex="0" role="listitem" aria-label="${escapedName}">` +
    `<div class="tile-header">` +
    `<span class="tile-name">${escapeHtml(name)}${badgeHtml}</span>` +
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
    `<article class="${classes}" data-session="${escapedName}" data-source-url="${escapeHtml(session.sourceUrl || '')}" tabindex="0" role="listitem">` +
    `<div class="sidebar-item-header">` +
    `<span class="sidebar-item-name">${escapedName}${_sources.length > 1 && session.deviceName ? '<span class="device-badge">' + escapeHtml(session.deviceName) + '</span>' : ''}</span>` +
    `${bellHtml}` +
    `<button class="sidebar-delete" data-session="${escapedName}" aria-label="Kill session">&times;</button>` +
    `</div>` +
    `<div class="sidebar-item-body"><pre>${ansiToHtml(lastLines)}</pre></div>` +
    `</article>`
  );
}

/**
 * Build the HTML string for an auth-required source tile.
 * @param {{ name: string, url: string }} source
 * @returns {string}
 */
function buildAuthTileHTML(source) {
  const escapedName = escapeHtml(source.name || '');
  const escapedUrl = escapeHtml(source.url || '');
  return (
    '<article class="source-tile source-tile--auth">' +
    '<span class="source-tile__name">' + escapedName + '</span>' +
    '<button class="source-tile__login-btn" data-url="' + escapedUrl + '">Log in</button>' +
    '<span class="source-tile__hint">Authenticate to see sessions</span>' +
    '</article>'
  );
}

/**
 * Format a millisecond timestamp into a relative 'last seen' string.
 * @param {number|null} ms - Millisecond timestamp
 * @returns {string}
 */
function formatLastSeen(ms) {
  if (ms == null) return 'Never';
  var diff = Math.floor((Date.now() - ms) / 1000);
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

/**
 * Build the HTML string for an offline (unreachable) source tile.
 * @param {{ name: string, url: string, lastSeenAt: number|null }} source
 * @returns {string}
 */
function buildOfflineTileHTML(source) {
  var escapedName = escapeHtml(source.name || '');
  var lastSeen = formatLastSeen(source.lastSeenAt);
  return (
    '<article class="source-tile source-tile--offline">' +
    '<span class="source-tile__name">' + escapedName + '</span>' +
    '<span class="source-tile__badge">Offline</span>' +
    '<span class="source-tile__last-seen">Last seen ' + escapeHtml(lastSeen) + '</span>' +
    '</article>'
  );
}

/**
 * Open a login popup window for a remote muxplex instance.
 * Strips trailing slashes from remoteUrl before appending /login.
 * @param {string} remoteUrl - The base URL of the remote instance
 */
function openLoginPopup(remoteUrl) {
  var baseUrl = remoteUrl.replace(/\/+$/, '');
  window.open(baseUrl + '/login', '_blank', 'width=500,height=600');
}

/**
 * Returns sessions with hidden session names removed.
 * Only hides LOCAL sessions (those with empty/absent sourceUrl) matching the
 * hidden_sessions list. Remote sessions with the same name remain visible.
 * Consolidates the hidden-session filter used by all render paths.
 * @param {object[]} sessions
 * @returns {object[]}
 */
function getVisibleSessions(sessions) {
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  return (sessions || []).filter(function(s) {
    if (hidden.length > 0 && (!s.sourceUrl) && hidden.includes(s.name)) {
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

  if (_sources.length > 1) {
    // Group sessions by deviceName when multiple sources configured
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

  // Bind click handlers on each sidebar item, passing sourceUrl
  if (typeof list.querySelectorAll === 'function') {
    list.querySelectorAll('.sidebar-item').forEach((item) => {
      const name = item.dataset.session;
      const sourceUrl = item.dataset.sourceUrl || '';
      on(item, 'click', () => {
        if (name !== currentSession) openSession(name, { sourceUrl });
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
    if (grid) grid.innerHTML = '';
    if (emptyState) emptyState.classList.remove('hidden');
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

  // Append status tiles for auth_required and unreachable sources
  var statusTilesHtml = '';
  if (typeof _sources !== 'undefined' && _sources) {
    _sources.forEach(function(source) {
      if (source.status === 'auth_required') {
        statusTilesHtml += buildAuthTileHTML(source);
      } else if (source.status === 'unreachable') {
        statusTilesHtml += buildOfflineTileHTML(source);
      }
    });
  }
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
    on(tile, 'click', function() { openSession(tile.dataset.session, { sourceUrl: tile.dataset.sourceUrl }); });
    on(tile, 'keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        openSession(tile.dataset.session, { sourceUrl: tile.dataset.sourceUrl });
      }
    });
  });

  if (_viewMode === 'fullscreen') {
    updatePillBell();
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
    const payload = buildHeartbeatPayload(_deviceId, _viewingSession, _viewMode, _lastInteractionAt);
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

// ─── Session open / close ────────────────────────────────────────────────────

/**
 * Open a session in fullscreen view with a zoom transition.
 * @param {string} name - session name
 * @param {object} [opts]
 * @param {boolean} [opts.skipConnect] - if true, skip the /connect POST (ttyd already running)
 * @returns {Promise<void>}
 */
async function openSession(name, opts = {}) {
  hidePreview();
  _viewingSession = name;
  _viewingSourceUrl = opts.sourceUrl || '';
  _viewMode = 'fullscreen';

  // Pre-render sidebar with current sessions before first poll tick
  initSidebar();
  renderSidebar(_currentSessions, name);

  // Update expanded header
  const nameEl = $('expanded-session-name');
  if (nameEl) nameEl.textContent = name;

  // Zoom animation: pin tile at current position, then animate to full viewport
  const tile = document.querySelector(`[data-session="${name}"]`);
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
    }, 260);
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

  // Connect to session (kill old ttyd, spawn new one for this session)
  var _sourceUrl = opts.sourceUrl || '';
  try {
    if (!opts.skipConnect) {
      if (_sourceUrl) {
        var remoteConnectUrl = _sourceUrl.replace(/\/+$/, '') + '/api/sessions/' + encodeURIComponent(name) + '/connect';
        await fetch(remoteConnectUrl, { method: 'POST', credentials: 'include' });
      } else {
        await api('POST', '/api/sessions/' + encodeURIComponent(name) + '/connect');
      }
    }
  } catch (err) {
    showToast(err.message || 'Connection failed');
    return closeSession();
  }

  // Wait for animation to finish (may already be done if /connect was slow)
  await animDone;

  // Mount terminal NOW — /connect has completed, new ttyd is serving the correct session
  if (window._openTerminal) window._openTerminal(name, _sourceUrl);
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
  if (!_viewingSourceUrl) {
    api('DELETE', '/api/sessions/current').catch(function() {});
  }
  _viewingSourceUrl = '';

  const expanded = $('view-expanded');
  const overview = $('view-overview');
  if (expanded) {
    expanded.classList.add('hidden');
    expanded.classList.remove('view--active');
  }
  if (overview) overview.style.display = '';  // overview uses view--active (no !important), style.display clears fine

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
  _sources = buildSources(_serverSettings);
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
 * Build the list of session sources from server settings.
 * Local source is always first with url: ''.
 * Remote instances come from settings.remote_instances array.
 * Trailing slashes are stripped from remote URLs.
 * Default device name is 'This device' when device_name is empty.
 * @param {object} settings - server settings object
 * @returns {object[]} array of source objects with {url, name, type, status, backoffMs}
 */
function buildSources(settings) {
  var localName = (settings && settings.device_name) || 'This device';
  var sources = [
    { url: '', name: localName, type: 'local', status: 'authenticated', backoffMs: 2000 },
  ];
  var remotes = (settings && settings.remote_instances) || [];
  for (var i = 0; i < remotes.length; i++) {
    var r = remotes[i];
    if (r && r.url) {
      sources.push({
        url: r.url.replace(/\/+$/, ''),
        name: r.name || r.url,
        type: 'remote',
        status: 'authenticated',
        backoffMs: 2000,
      });
    }
  }
  return sources;
}

/**
 * Build a single remote instance row element with URL input, name input, and remove button.
 * @param {string} url - remote instance URL
 * @param {string} name - remote instance display name
 * @returns {HTMLDivElement}
 */
function _buildRemoteInstanceRow(url, name) {
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
  var removeBtn = document.createElement('button');
  removeBtn.className = 'settings-remote-remove';
  removeBtn.textContent = '\u00d7';
  removeBtn.setAttribute('aria-label', 'Remove remote instance');
  row.appendChild(urlInput);
  row.appendChild(nameInput);
  row.appendChild(removeBtn);
  return row;
}

/**
 * Read remote instance rows from the DOM and save to server settings.
 * Rebuilds _sources after saving so polling updates immediately.
 */
function _saveRemoteInstances() {
  var container = $('setting-remote-instances');
  if (!container) return;
  var instances = [];
  container.querySelectorAll('.settings-remote-row').forEach(function(row) {
    var urlEl = row.querySelector('.settings-remote-url');
    var nameEl = row.querySelector('.settings-remote-name');
    var url = (urlEl && urlEl.value) ? urlEl.value.trim() : '';
    var name = (nameEl && nameEl.value) ? nameEl.value.trim() : '';
    if (url) {
      instances.push({ url: url, name: name });
    }
  });
  patchServerSetting('remote_instances', instances).then(function() {
    _sources = buildSources(_serverSettings);
  });
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
 * Apply display settings to the live DOM.
 * Sets --preview-font-size CSS custom property and updates #session-grid
 * grid-template-columns based on the gridColumns setting.
 * @param {object} ds - display settings object
 */
function applyDisplaySettings(ds) {
  // Apply font size as CSS custom property
  document.documentElement.style.setProperty('--preview-font-size', ds.fontSize + 'px');

  // Apply grid columns
  var grid = document.getElementById('session-grid');
  if (grid) {
    if (ds.gridColumns === 'auto') {
      grid.style.removeProperty('grid-template-columns');
    } else {
      grid.style.gridTemplateColumns = 'repeat(' + ds.gridColumns + ', 1fr)';
    }
  }
}

/**
 * Load grid view mode preference based on viewPreferenceScope in display settings.
 * When scope is 'local' (default), reads gridViewMode from display settings (localStorage).
 * When scope is 'server', reads grid_view_mode from _serverSettings.
 * Returns 'flat' as default.
 * @returns {string}
 */
function loadGridViewMode() {
  var ds = loadDisplaySettings();
  var scope = ds.viewPreferenceScope || 'local';
  if (scope === 'server') {
    return (_serverSettings && _serverSettings.grid_view_mode) || 'flat';
  }
  return ds.gridViewMode || 'flat';
}

/**
 * Save grid view mode preference to the appropriate scope and update _gridViewMode.
 * When scope is 'local', saves gridViewMode to display settings (localStorage).
 * When scope is 'server', patches the server setting via patchServerSetting.
 * @param {string} mode - The grid view mode to save.
 */
function saveGridViewMode(mode) {
  var ds = loadDisplaySettings();
  var scope = ds.viewPreferenceScope || 'local';
  if (scope === 'server') {
    patchServerSetting('grid_view_mode', mode);
  } else {
    ds.gridViewMode = mode;
    saveDisplaySettings(ds);
  }
  _gridViewMode = mode;
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
  const viewModeEl = $('setting-view-mode');
  if (viewModeEl) viewModeEl.value = loadGridViewMode();
  const viewScopeEl = $('setting-view-scope');
  if (viewScopeEl) viewScopeEl.value = settings.viewPreferenceScope || 'local';

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

    // Device name
    const deviceNameEl = $('setting-device-name');
    if (deviceNameEl) {
      deviceNameEl.value = (ss && ss.device_name) || '';
    }

    // Remote instances
    const remoteInstancesEl = $('setting-remote-instances');
    if (remoteInstancesEl) {
      remoteInstancesEl.innerHTML = '';
      var remotes = (ss && ss.remote_instances) || [];
      remotes.forEach(function(r) {
        remoteInstancesEl.appendChild(_buildRemoteInstanceRow(r.url || '', r.name || ''));
      });
    }

    // New Session tab - populate template textarea
    const templateEl = $('setting-template');
    if (templateEl) {
      templateEl.value = (ss && ss.new_session_template) || NEW_SESSION_DEFAULT_TEMPLATE;
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

    const ss = _serverSettings || {};
    if (ss.auto_open_created === false) {
      // Auto-open disabled — just do one refresh
      await pollSessions();
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
        showToast('Session \'' + sessionName + '\' ready');
        openSession(sessionName);
      } else if (attempts >= maxAttempts) {
        clearInterval(pollForSession);
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

  document.addEventListener('click', function(e) {
    var loginBtn = e.target.closest && e.target.closest('.source-tile__login-btn');
    if (!loginBtn) return;
    e.stopPropagation();
    var url = loginBtn.dataset.url;
    if (url) openLoginPopup(url);
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
  on($('settings-btn'), 'click', openSettings);
  on($('settings-btn-expanded'), 'click', openSettings);
  on($('settings-close-btn'), 'click', closeSettings);
  on($('settings-backdrop'), 'click', closeSettings);
  const settingsDialog = $('settings-dialog');
  if (settingsDialog) settingsDialog.addEventListener('cancel', closeSettings);
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
  on($('setting-view-mode'), 'change', function() {
    var el = $('setting-view-mode');
    if (el) {
      saveGridViewMode(el.value);
      renderGrid(_currentSessions || []);
    }
  });
  on($('setting-view-scope'), 'change', function() {
    var el = $('setting-view-scope');
    if (!el) return;
    var newScope = el.value;
    var currentMode = _gridViewMode;
    var ds = loadDisplaySettings();
    ds.viewPreferenceScope = newScope;
    saveDisplaySettings(ds);
    // Migrate current mode to new scope
    saveGridViewMode(currentMode);
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

  // New Session tab — template textarea with 500ms debounce
  var _templateDebounceTimer;
  on($('setting-template'), 'input', function() {
    clearTimeout(_templateDebounceTimer);
    var val = this.value;
    _templateDebounceTimer = setTimeout(function() {
      patchServerSetting('new_session_template', val);
    }, 500);
  });

  // New Session tab — reset button restores default template
  on($('setting-template-reset'), 'click', function() {
    var el = $('setting-template');
    if (el) el.value = NEW_SESSION_DEFAULT_TEMPLATE;
    patchServerSetting('new_session_template', NEW_SESSION_DEFAULT_TEMPLATE);
  });

  // Sessions tab — device name with 500ms debounce
  var _deviceNameDebounceTimer;
  on($('setting-device-name'), 'input', function() {
    clearTimeout(_deviceNameDebounceTimer);
    var val = this.value;
    _deviceNameDebounceTimer = setTimeout(function() {
      patchServerSetting('device_name', val).then(function() {
        _sources = buildSources(_serverSettings);
      });
    }, 500);
  });

  // Sessions tab — add remote instance button
  on($('add-remote-instance-btn'), 'click', function() {
    var container = $('setting-remote-instances');
    if (container) {
      container.appendChild(_buildRemoteInstanceRow('', ''));
    }
  });

  // Sessions tab — delegated remove handler on remote instances container
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
    var _remoteDebounceTimer;
    remoteInstancesContainer.addEventListener('input', function(e) {
      var input = e.target.closest && e.target.closest('.settings-remote-url, .settings-remote-name');
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
}

// ─── Test-only helpers ────────────────────────────────────────────────────────

// ─── Multi-source parallel polling ─────────────────────────────────────────

/**
 * Tag each session in the array with deviceName, sourceUrl, and sessionKey.
 * Returns new session objects; does NOT mutate originals.
 * sessionKey format: sourceUrl + '::' + name
 * @param {object[]} sessions
 * @param {string} deviceName
 * @param {string} sourceUrl
 * @returns {object[]}
 */
function tagSessions(sessions, deviceName, sourceUrl) {
  return (sessions || []).map((s) => Object.assign({}, s, {
    deviceName,
    sourceUrl,
    sessionKey: sourceUrl + '::' + (s.name || ''),
  }));
}

/**
 * Merge sessions from multiple sources into a single flat array.
 * Each result is an object with {source, sessions}.
 * Tags each source's sessions with deviceName/sourceUrl/sessionKey.
 * @param {Array<{source: {name: string, url: string}, sessions: object[]}>} results
 * @returns {object[]}
 */
function mergeSources(results) {
  return (results || []).reduce((all, r) => all.concat(tagSessions(r.sessions, r.source.name, r.source.url)), []);
}

/** Test-only: set _currentSessions directly. */
function _setCurrentSessions(sessions) {
  _currentSessions = sessions;
}

/** Test-only: set _viewMode directly. */
function _setViewMode(mode) {
  _viewMode = mode;
}

/** Test-only: set _sources directly. */
function _setSources(sources) {
  _sources = sources;
}

/** Test-only: set _serverSettings directly. */
function _setServerSettings(settings) {
  _serverSettings = settings;
}

/** Test-only: get _gridViewMode. */
function _getGridViewMode() {
  return _gridViewMode;
}

/** Test-only: set _gridViewMode directly. */
function _setGridViewMode(mode) {
  _gridViewMode = mode;
}

/** Test-only: get _sources. */
function _getSources() {
  return _sources;
}

/** Test-only: set _activeFilterDevice directly. */
function _setActiveFilterDevice(device) {
  _activeFilterDevice = device;
}

document.addEventListener('DOMContentLoaded', () => {
  initDeviceId();
  applyDisplaySettings(loadDisplaySettings());
  _gridViewMode = loadGridViewMode();
  document.addEventListener('keydown', trackInteraction);
  document.addEventListener('click', trackInteraction);
  document.addEventListener('touchstart', trackInteraction);

  restoreState()
    .then(() => {
      startPolling();
      loadServerSettings();
      startHeartbeat();
      requestNotificationPermission();
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
    loadGridViewMode,
    saveGridViewMode,
    onDisplaySettingChange,
    openSettings,
    closeSettings,
    switchSettingsTab,
    // Server settings
    loadServerSettings,
    patchServerSetting,
    buildSources,
    // Fetch wrapper
    api,
    // Header + button with inline name input
    showNewSessionInput,
    showFabSessionInput,
    createNewSession,
    // Kill session
    killSession,
    // Multi-source parallel polling
    tagSessions,
    mergeSources,
    // Filter bar
    renderFilterBar,
    // Federation tiles
    buildAuthTileHTML,
    buildOfflineTileHTML,
    openLoginPopup,
    formatLastSeen,
    // Test-only helpers
    _setCurrentSessions,
    _setViewMode,
    _setSources,
    _setServerSettings,
    _getGridViewMode,
    _setGridViewMode,
    _getSources,
    _setActiveFilterDevice,
  };
}

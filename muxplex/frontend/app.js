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

  // Last 20 lines of snapshot
  const snapshot = session.snapshot || '';
  const lastLines = snapshot.split('\n').slice(-20).join('\n');

  return (
    `<article class="${classes}" data-session="${escapedName}" tabindex="0" role="listitem" aria-label="${escapedName}">` +
    `<div class="tile-header">` +
    `<span class="tile-name">${escapeHtml(name)}</span>` +
    `<span class="tile-meta">${bellHtml}<span class="tile-time">${escapeHtml(timeStr)}</span></span>` +
    `</div>` +
    `<div class="tile-body"><pre>${escapeHtml(lastLines)}</pre></div>` +
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
    `</div>` +
    `<div class="sidebar-item-body"><pre>${escapeHtml(lastLines)}</pre></div>` +
    `</article>`
  );
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

  if (!sessions || sessions.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">No sessions</div>';
    return;
  }

  list.innerHTML = sessions.map((session) => buildSidebarHTML(session, currentSession)).join('');

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

  if (!sessions || sessions.length === 0) {
    if (grid) grid.innerHTML = '';
    if (emptyState) emptyState.classList.remove('hidden');
    return;
  }

  if (emptyState) emptyState.classList.add('hidden');

  const mobile = isMobile();
  const ordered = mobile ? sortByPriority(sessions) : sessions;
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
  _viewingSession = name;
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

  // Connect to session (kill old ttyd, spawn new one for this session)
  try {
    if (!opts.skipConnect) {
      await api('POST', `/api/sessions/${name}/connect`);
    }
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

  const pill = $('session-pill');
  if (pill) pill.classList.add('hidden');

  return Promise.resolve();
}

/** Test-only helper: set _viewingSession directly. */
function _setViewingSession(name) {
  _viewingSession = name;
}

// ─── Command palette state ────────────────────────────────────────────────────
const PALETTE_MAX_ITEMS = 9;
let _paletteSelectedIndex = 0;
let _paletteFilteredSessions = [];
let _paletteOpen = false;
let _paletteInputListener = null;

// ─── Command palette functions ────────────────────────────────────────────────

/**
 * Render the filtered session list inside #palette-list.
 * Shows up to 9 items. Each item is a <li> with index number,
 * session name, optional bell emoji, and timestamp.
 */
function renderPaletteList() {
  const list = $('palette-list');
  if (!list) return;

  const items = _paletteFilteredSessions.slice(0, PALETTE_MAX_ITEMS);
  list.innerHTML = items
    .map((session, i) => {
      const isBell = sessionPriority(session) === 'bell';
      const bell = isBell ? ' 🔔' : '';
      const time = formatTimestamp(session.last_activity_at || null);
      const name = escapeHtml(session.name || '');
      return `<li class="palette-item" data-index="${i}">${i + 1} ${name}${bell} ${escapeHtml(time)}</li>`;
    })
    .join('');

  // Bind click handlers on each item
  list.querySelectorAll('.palette-item').forEach((item) => {
    on(item, 'click', () => {
      const idx = parseInt(item.dataset.index, 10);
      const session = _paletteFilteredSessions[idx];
      if (session) {
        closePalette();
        openSession(session.name).catch((err) => console.error('[renderPaletteList]', err));
      }
    });
  });

  highlightPaletteItem(_paletteSelectedIndex);
}

/**
 * Toggle the palette-item--selected class on the item at `index`.
 * @param {number} index
 */
function highlightPaletteItem(index) {
  const list = $('palette-list');
  if (!list) return;
  list.querySelectorAll('.palette-item').forEach((item, i) => {
    if (i === index) {
      item.classList.add('palette-item--selected');
    } else {
      item.classList.remove('palette-item--selected');
    }
  });
}

/**
 * Open the command palette.
 * Shows #command-palette, copies _currentSessions to _paletteFilteredSessions,
 * renders the list, resets selection index, focuses #palette-input, and binds
 * the input event listener.
 */
function openPalette() {
  _paletteOpen = true;
  _paletteFilteredSessions = _currentSessions.slice();
  _paletteSelectedIndex = 0;

  const palette = $('command-palette');
  if (palette) palette.classList.remove('hidden');  // palette starts with hidden class

  renderPaletteList();

  const input = $('palette-input');
  if (input) {
    input.value = '';
    input.focus();
    if (_paletteInputListener) {
      input.removeEventListener('input', _paletteInputListener);
    }
    _paletteInputListener = onPaletteInput;
    input.addEventListener('input', _paletteInputListener);
  }
}

/**
 * Close the command palette.
 * Hides #command-palette and removes the input event listener.
 */
function closePalette() {
  _paletteOpen = false;

  const palette = $('command-palette');
  if (palette) palette.classList.add('hidden');

  const input = $('palette-input');
  if (input && _paletteInputListener) {
    input.removeEventListener('input', _paletteInputListener);
    _paletteInputListener = null;
  }
}

/**
 * Handle input events on #palette-input.
 * Filters sessions by the current query, re-renders the list, resets selection.
 * @param {Event} e
 */
function onPaletteInput(e) {
  const query = e && e.target ? e.target.value : '';
  _paletteFilteredSessions = filterByQuery(_currentSessions, query);
  _paletteSelectedIndex = 0;
  renderPaletteList();
}

/**
 * Handle keydown events inside the command palette.
 * ArrowDown/Up moves selection, Enter opens selected session,
 * Escape closes palette, G closes palette + returns to grid,
 * number keys 1-9 jump directly to that item.
 * @param {KeyboardEvent} e
 * @returns {Promise<void>}
 */
async function handlePaletteKeydown(e) {
  const visibleCount = Math.min(_paletteFilteredSessions.length, PALETTE_MAX_ITEMS);

  if (e.key === 'Escape') {
    e.preventDefault();
    closePalette();
  } else if (e.key === 'g' || e.key === 'G') {
    e.preventDefault();
    closePalette();
    await closeSession();
  } else if (visibleCount > 0) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _paletteSelectedIndex = (_paletteSelectedIndex + 1) % visibleCount;
      highlightPaletteItem(_paletteSelectedIndex);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _paletteSelectedIndex = (_paletteSelectedIndex - 1 + visibleCount) % visibleCount;
      highlightPaletteItem(_paletteSelectedIndex);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const session = _paletteFilteredSessions[_paletteSelectedIndex];
      if (session) {
        closePalette();
        await openSession(session.name);
      }
    } else if (e.key >= '1' && e.key <= '9') {
      const idx = parseInt(e.key, 10) - 1;
      if (idx < visibleCount) {
        e.preventDefault();
        const session = _paletteFilteredSessions[idx];
        closePalette();
        await openSession(session.name);
      }
    }
  }
}

/**
 * Global keydown handler.
 * When palette is open: delegates to handlePaletteKeydown.
 * When in fullscreen with palette closed: backtick or Ctrl+K opens palette,
 * Escape returns to grid.
 * @param {KeyboardEvent} e
 */
function handleGlobalKeydown(e) {
  if (_paletteOpen) {
    handlePaletteKeydown(e).catch((err) => console.error('[handleGlobalKeydown]', err));
    return;
  }

  if (_viewMode === 'fullscreen') {
    if (e.key === '`' || (e.ctrlKey && e.key === 'k')) {
      e.preventDefault();
      openPalette();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeSession();
    }
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
  var sorted = sortByPriority(_currentSessions);
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

/**
 * Bind all static (once-only) event listeners for the app UI.
 * Called once after restoreState() resolves.
 */
function bindStaticEventListeners() {
  on($('back-btn'), 'click', closeSession);
  on($('sidebar-toggle-btn'), 'click', toggleSidebar);
  on($('sidebar-collapse-btn'), 'click', toggleSidebar);
  bindSidebarClickAway();
  on($('palette-trigger'), 'click', openPalette);
  on($('palette-backdrop'), 'click', closePalette);
  document.addEventListener('keydown', handleGlobalKeydown);
  on($('session-pill'), 'click', openBottomSheet);
  on($('sheet-backdrop'), 'click', closeBottomSheet);
}

// ─── Test-only helpers ────────────────────────────────────────────────────────

/** Test-only: set _currentSessions directly. */
function _setCurrentSessions(sessions) {
  _currentSessions = sessions;
}

/** Test-only: set _paletteFilteredSessions directly. */
function _setPaletteFilteredSessions(sessions) {
  _paletteFilteredSessions = sessions;
}

/** Test-only: get _paletteFilteredSessions. */
function _getPaletteFilteredSessions() {
  return _paletteFilteredSessions;
}

/** Test-only: set _paletteSelectedIndex directly. */
function _setPaletteSelectedIndex(index) {
  _paletteSelectedIndex = index;
}

/** Test-only: get _paletteSelectedIndex. */
function _getPaletteSelectedIndex() {
  return _paletteSelectedIndex;
}

/** Test-only: set _paletteOpen directly. */
function _setPaletteOpen(val) {
  _paletteOpen = val;
}

/** Test-only: get _paletteOpen. */
function _isPaletteOpen() {
  return _paletteOpen;
}

/** Test-only: set _viewMode directly. */
function _setViewMode(mode) {
  _viewMode = mode;
}

document.addEventListener('DOMContentLoaded', () => {
  initDeviceId();
  document.addEventListener('keydown', trackInteraction);
  document.addEventListener('click', trackInteraction);
  document.addEventListener('touchstart', trackInteraction);

  restoreState()
    .then(() => {
      startPolling();
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
    // Command palette
    renderPaletteList,
    highlightPaletteItem,
    openPalette,
    closePalette,
    onPaletteInput,
    handlePaletteKeydown,
    handleGlobalKeydown,
    bindStaticEventListeners,
    openBottomSheet,
    closeBottomSheet,
    renderSheetList,
    updateSessionPill,
    // Test-only helpers
    _setCurrentSessions,
    _setPaletteFilteredSessions,
    _getPaletteFilteredSessions,
    _setPaletteSelectedIndex,
    _getPaletteSelectedIndex,
    _setPaletteOpen,
    _isPaletteOpen,
    _setViewMode,
  };
}

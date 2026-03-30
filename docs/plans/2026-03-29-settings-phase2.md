# Settings Panel — Phase 2: Remaining Tabs + New Session UI

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Phase 2 of 2** — complete [Phase 1](./2026-03-29-settings-phase1.md) before starting this phase.

**Design doc:** [`docs/plans/2026-03-29-settings-design.md`](./2026-03-29-settings-design.md)

**Goal:** Complete all four settings tabs and build the new session creation UI — `+` button, inline name input, mobile FAB, and end-to-end creation flow.

**Architecture:** Sessions/New Session tabs use the `GET/PATCH /api/settings` and `POST /api/sessions` endpoints from Phase 1. The `+` button uses inline name input (header, sidebar, FAB) that calls the create endpoint. Display/Notifications tabs use localStorage. All controls apply immediately — no save button.

**Tech Stack:** Vanilla JS, CSS custom properties, FastAPI backend (already deployed from Phase 1).

**Prereqs from Phase 1:**
- `muxplex/settings.py` — `load_settings()`, `save_settings()`, `patch_settings()`
- `GET/PATCH /api/settings`, `POST /api/sessions` endpoints in `main.py`
- Settings dialog HTML skeleton with tab navigation in `index.html`
- `openSettings()`, `closeSettings()`, `switchSettingsTab()`, `loadDisplaySettings()`, `saveDisplaySettings()` in `app.js`
- `.settings-*` CSS classes in `style.css`

---

### Task 1: Sessions tab — server settings UI

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/app.js`

**Step 1: Add Sessions tab HTML**

In `muxplex/frontend/index.html`, replace the empty `#settings-panel-sessions` div:

```html
        <!-- Sessions tab -->
        <div id="settings-panel-sessions" class="settings-panel hidden" role="tabpanel" data-tab="sessions">
          <h3 class="settings-panel-title">Sessions</h3>
          <label class="settings-field">
            <span class="settings-label">Default session</span>
            <select id="setting-default-session" class="settings-select">
              <option value="">None</option>
            </select>
          </label>
          <label class="settings-field">
            <span class="settings-label">Sort order</span>
            <select id="setting-sort-order" class="settings-select">
              <option value="manual">Manual</option>
              <option value="alphabetical">Alphabetical</option>
              <option value="recent">Recent activity</option>
            </select>
          </label>
          <div class="settings-field settings-field--column">
            <span class="settings-label">Hidden sessions</span>
            <div id="setting-hidden-sessions" class="settings-checkbox-list"></div>
          </div>
          <label class="settings-field">
            <span class="settings-label">Auto-set window-size largest</span>
            <input type="checkbox" id="setting-window-size-largest" class="settings-checkbox">
          </label>
          <label class="settings-field">
            <span class="settings-label">Auto-open created sessions</span>
            <input type="checkbox" id="setting-auto-open" class="settings-checkbox" checked>
          </label>
        </div>
```

**Step 2: Add server settings load/save functions to app.js**

In `muxplex/frontend/app.js`, add after the `onDisplaySettingChange` function:

```javascript
// ─── Server settings ─────────────────────────────────────────────────────────
let _serverSettings = null;

/**
 * Load server settings via GET /api/settings.
 * Caches the result in _serverSettings for local reads.
 * @returns {Promise<object>}
 */
async function loadServerSettings() {
  try {
    var res = await api('GET', '/api/settings');
    _serverSettings = await res.json();
  } catch (err) {
    console.warn('[loadServerSettings]', err);
    _serverSettings = _serverSettings || {};
  }
  return _serverSettings;
}

/**
 * Patch a single server setting field.
 * @param {string} key
 * @param {*} value
 * @returns {Promise<void>}
 */
async function patchServerSetting(key, value) {
  var patch = {};
  patch[key] = value;
  try {
    var res = await api('PATCH', '/api/settings', patch);
    _serverSettings = await res.json();
    showToast('Setting saved');
  } catch (err) {
    console.warn('[patchServerSetting]', err);
    showToast('Failed to save setting');
  }
}
```

**Step 3: Populate Sessions tab when settings opens**

Update the `openSettings` function. After the display settings load, add server settings loading:

```javascript
  // Load server settings and populate Sessions tab
  loadServerSettings().then(function(ss) {
    // Default session dropdown: populate with current session list
    var defaultSel = $('setting-default-session');
    if (defaultSel) {
      var current = ss.default_session || '';
      defaultSel.innerHTML = '<option value="">None</option>' +
        _currentSessions.map(function(s) {
          var name = escapeHtml(s.name);
          var selected = s.name === current ? ' selected' : '';
          return '<option value="' + name + '"' + selected + '>' + name + '</option>';
        }).join('');
    }

    // Sort order
    var sortSel = $('setting-sort-order');
    if (sortSel) sortSel.value = ss.sort_order || 'manual';

    // Hidden sessions checkboxes
    var hiddenDiv = $('setting-hidden-sessions');
    if (hiddenDiv) {
      var hiddenList = ss.hidden_sessions || [];
      hiddenDiv.innerHTML = _currentSessions.map(function(s) {
        var name = escapeHtml(s.name);
        var checked = hiddenList.indexOf(s.name) !== -1 ? ' checked' : '';
        return '<label class="settings-checkbox-item">' +
          '<input type="checkbox" data-session="' + name + '"' + checked + '> ' + name +
          '</label>';
      }).join('');
    }

    // Checkboxes
    var wslEl = $('setting-window-size-largest');
    if (wslEl) wslEl.checked = !!ss.window_size_largest;
    var aoEl = $('setting-auto-open');
    if (aoEl) aoEl.checked = ss.auto_open_created !== false;
  });
```

**Step 4: Bind change handlers for Sessions tab in bindStaticEventListeners**

Add to `bindStaticEventListeners`:

```javascript
  // Sessions tab change handlers
  on($('setting-default-session'), 'change', function() {
    patchServerSetting('default_session', this.value || null);
  });
  on($('setting-sort-order'), 'change', function() {
    patchServerSetting('sort_order', this.value);
  });
  on($('setting-window-size-largest'), 'change', function() {
    patchServerSetting('window_size_largest', this.checked);
  });
  on($('setting-auto-open'), 'change', function() {
    patchServerSetting('auto_open_created', this.checked);
  });

  // Hidden sessions: delegated handler on container (checkboxes are dynamic)
  var hiddenContainer = $('setting-hidden-sessions');
  if (hiddenContainer) {
    hiddenContainer.addEventListener('change', function() {
      var checkboxes = hiddenContainer.querySelectorAll('input[type="checkbox"]');
      var hidden = [];
      checkboxes.forEach(function(cb) {
        if (cb.checked) hidden.push(cb.dataset.session);
      });
      patchServerSetting('hidden_sessions', hidden);
    });
  }
```

**Step 5: Add CSS for column-layout fields and checkbox list**

Append to `muxplex/frontend/style.css`:

```css
.settings-field--column {
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.settings-checkbox-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-left: 4px;
}

.settings-checkbox-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
}

.settings-checkbox {
  accent-color: var(--accent);
  cursor: pointer;
}
```

**Step 6: Add exports**

Add to `module.exports`:

```javascript
    loadServerSettings,
    patchServerSetting,
```

**Step 7: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 8: Commit**

```bash
git add -A && git commit -m "feat: add Sessions settings tab with server-side persistence"
```

---

### Task 2: Notifications tab

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/style.css`

**Step 1: Add Notifications tab HTML**

In `muxplex/frontend/index.html`, replace the empty `#settings-panel-notifications` div:

```html
        <!-- Notifications tab -->
        <div id="settings-panel-notifications" class="settings-panel hidden" role="tabpanel" data-tab="notifications">
          <h3 class="settings-panel-title">Notifications</h3>
          <label class="settings-field">
            <span class="settings-label">Bell sound</span>
            <input type="checkbox" id="setting-bell-sound" class="settings-checkbox">
          </label>
          <div class="settings-field">
            <span class="settings-label">Desktop notifications</span>
            <div class="settings-notification-status">
              <span id="notification-status-text" class="settings-status-text">Not requested</span>
              <button id="notification-request-btn" class="settings-action-btn">Request permission</button>
            </div>
          </div>
        </div>
```

**Step 2: Add CSS for notification status and action button**

Append to `muxplex/frontend/style.css`:

```css
.settings-notification-status {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}

.settings-status-text {
  font-size: 12px;
  color: var(--text-muted);
}

.settings-action-btn {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text);
  font-size: 12px;
  font-family: var(--font-ui);
  padding: 6px 12px;
  cursor: pointer;
  transition: border-color var(--t-fast);
}

.settings-action-btn:hover {
  border-color: var(--accent);
}

.settings-action-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
```

**Step 3: Add notification handlers to app.js**

Update `openSettings` to populate notification controls (add inside the function):

```javascript
  // Notifications tab
  var ds2 = loadDisplaySettings();
  var bellSoundEl = $('setting-bell-sound');
  if (bellSoundEl) bellSoundEl.checked = !!ds2.bellSound;

  var notifText = $('notification-status-text');
  var notifBtn = $('notification-request-btn');
  if (notifText && notifBtn) {
    var perm = typeof Notification !== 'undefined' ? Notification.permission : 'unavailable';
    if (perm === 'granted') {
      notifText.textContent = 'Granted';
      notifBtn.textContent = 'Granted';
      notifBtn.disabled = true;
    } else if (perm === 'denied') {
      notifText.textContent = 'Denied (change in browser settings)';
      notifBtn.textContent = 'Denied';
      notifBtn.disabled = true;
    } else if (perm === 'unavailable') {
      notifText.textContent = 'Not supported';
      notifBtn.disabled = true;
    } else {
      notifText.textContent = 'Not requested';
      notifBtn.textContent = 'Request permission';
      notifBtn.disabled = false;
    }
  }
```

**Step 4: Bind notification change handlers in bindStaticEventListeners**

Add to `bindStaticEventListeners`:

```javascript
  // Notifications tab
  on($('setting-bell-sound'), 'change', function() {
    var ds = loadDisplaySettings();
    ds.bellSound = this.checked;
    saveDisplaySettings(ds);
  });

  on($('notification-request-btn'), 'click', function() {
    if (typeof Notification === 'undefined') return;
    Notification.requestPermission().then(function(perm) {
      _notificationPermission = perm;
      var ds = loadDisplaySettings();
      ds.notificationPermission = perm;
      saveDisplaySettings(ds);
      // Update UI
      var notifText = $('notification-status-text');
      var notifBtn = $('notification-request-btn');
      if (perm === 'granted') {
        if (notifText) notifText.textContent = 'Granted';
        if (notifBtn) { notifBtn.textContent = 'Granted'; notifBtn.disabled = true; }
      } else if (perm === 'denied') {
        if (notifText) notifText.textContent = 'Denied (change in browser settings)';
        if (notifBtn) { notifBtn.textContent = 'Denied'; notifBtn.disabled = true; }
      }
    });
  });
```

**Step 5: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add -A && git commit -m "feat: add Notifications settings tab"
```

---

### Task 3: New Session tab — template textarea

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/style.css`

**Step 1: Add New Session tab HTML**

In `muxplex/frontend/index.html`, replace the empty `#settings-panel-new-session` div:

```html
        <!-- New Session tab -->
        <div id="settings-panel-new-session" class="settings-panel hidden" role="tabpanel" data-tab="new-session">
          <h3 class="settings-panel-title">New Session</h3>
          <div class="settings-field settings-field--column">
            <span class="settings-label">Command template</span>
            <textarea id="setting-template" class="settings-textarea" rows="3"
              placeholder="tmux new-session -d -s {name}"></textarea>
            <span class="settings-helper">{name} is replaced with the session name</span>
          </div>
          <button id="setting-template-reset" class="settings-action-btn">Reset to default</button>
        </div>
```

**Step 2: Add CSS for textarea and helper text**

Append to `muxplex/frontend/style.css`:

```css
.settings-textarea {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text);
  font-size: 13px;
  font-family: var(--font-mono);
  padding: 10px;
  resize: vertical;
  min-height: 60px;
}

.settings-textarea:focus {
  outline: 1px solid var(--accent);
  border-color: var(--accent);
}

.settings-helper {
  font-size: 12px;
  color: var(--text-muted);
  font-style: italic;
}
```

**Step 3: Populate template on settings open**

Inside `openSettings`, in the `loadServerSettings().then(...)` callback, add:

```javascript
    // New Session tab
    var templateEl = $('setting-template');
    if (templateEl) templateEl.value = ss.new_session_template || 'tmux new-session -d -s {name}';
```

**Step 4: Bind template change handlers in bindStaticEventListeners**

Add to `bindStaticEventListeners`:

```javascript
  // New Session tab: save template on blur (not every keystroke)
  var templateEl = $('setting-template');
  if (templateEl) {
    var _templateDebounce = null;
    templateEl.addEventListener('input', function() {
      clearTimeout(_templateDebounce);
      _templateDebounce = setTimeout(function() {
        patchServerSetting('new_session_template', templateEl.value);
      }, 500);
    });
  }

  on($('setting-template-reset'), 'click', function() {
    var templateEl = $('setting-template');
    if (templateEl) templateEl.value = 'tmux new-session -d -s {name}';
    patchServerSetting('new_session_template', 'tmux new-session -d -s {name}');
  });
```

**Step 5: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add -A && git commit -m "feat: add New Session settings tab with template textarea"
```

---

### Task 4: Header `+` button with inline name input

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/style.css`

**Step 1: Add new session creation function**

In `muxplex/frontend/app.js`, add after the server settings functions:

```javascript
// ─── New session creation ────────────────────────────────────────────────────

/**
 * Show an inline name input replacing the given button element.
 * On Enter: call createNewSession(). On Escape: cancel and restore button.
 * @param {HTMLElement} btn - the button to replace with input
 */
function showNewSessionInput(btn) {
  if (!btn) return;
  var container = btn.parentElement;
  if (!container) return;

  // Create inline input
  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'new-session-input';
  input.placeholder = 'Session name\u2026';
  input.setAttribute('autocomplete', 'off');
  input.setAttribute('spellcheck', 'false');

  // Replace button with input
  btn.style.display = 'none';
  container.insertBefore(input, btn);
  input.focus();

  function cleanup() {
    input.remove();
    btn.style.display = '';
  }

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      var name = input.value.trim();
      if (name) {
        cleanup();
        createNewSession(name);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cleanup();
    }
  });

  // Cancel on blur (click away)
  input.addEventListener('blur', function() {
    // Delay to allow click handlers to fire first
    setTimeout(cleanup, 150);
  });
}

/**
 * Create a new session by POSTing to /api/sessions.
 * Shows toast, triggers poll refresh, auto-opens if setting enabled.
 * @param {string} name
 * @returns {Promise<void>}
 */
async function createNewSession(name) {
  try {
    var res = await api('POST', '/api/sessions', { name: name });
    var data = await res.json();
    showToast("Session '" + data.name + "' created");

    // Trigger immediate poll refresh to pick up the new session
    await pollSessions();

    // Auto-open if setting enabled
    var ss = _serverSettings || await loadServerSettings();
    if (ss.auto_open_created !== false) {
      // Small delay to let tmux create the session
      setTimeout(function() {
        openSession(data.name);
      }, 500);
    }
  } catch (err) {
    showToast(err.message || 'Failed to create session');
  }
}
```

**Step 2: Add CSS for inline input**

Append to `muxplex/frontend/style.css`:

```css
/* ============================================================
   New session inline input
   ============================================================ */

.new-session-input {
  background: var(--bg);
  border: 1px solid var(--accent);
  border-radius: 4px;
  color: var(--text);
  font-size: 13px;
  font-family: var(--font-ui);
  padding: 4px 10px;
  width: 180px;
  outline: none;
}

.new-session-input::placeholder {
  color: var(--text-dim);
}
```

**Step 3: Bind the header `+` button**

Add to `bindStaticEventListeners`:

```javascript
  // New session: header + button
  on($('new-session-btn'), 'click', function() {
    showNewSessionInput($('new-session-btn'));
  });
```

**Step 4: Add exports**

Add to `module.exports`:

```javascript
    showNewSessionInput,
    createNewSession,
```

**Step 5: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add -A && git commit -m "feat: add header + button with inline name input for session creation"
```

---

### Task 5: Sidebar `+ New` sticky footer

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/style.css`

**Step 1: Add sidebar footer HTML**

In `muxplex/frontend/index.html`, add a footer inside the `#session-sidebar` div, after `#sidebar-list`:

```html
      <div id="session-sidebar" class="session-sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">Sessions</span>
          <button id="sidebar-collapse-btn" class="sidebar-collapse-btn" aria-label="Collapse session list">&#8249;</button>
        </div>
        <div id="sidebar-list" class="sidebar-list"></div>
        <div class="sidebar-footer">
          <button id="sidebar-new-session-btn" class="sidebar-new-btn">+ New</button>
        </div>
      </div>
```

**Step 2: Add sidebar footer CSS**

Append to `muxplex/frontend/style.css`:

```css
.sidebar-footer {
  padding: 8px;
  border-top: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.sidebar-new-btn {
  width: 100%;
  background: none;
  border: 1px dashed var(--border);
  border-radius: 4px;
  color: var(--text-dim);
  font-size: 12px;
  font-family: var(--font-ui);
  padding: 8px;
  cursor: pointer;
  transition: color var(--t-fast), border-color var(--t-fast);
}

.sidebar-new-btn:hover {
  color: var(--text);
  border-color: var(--text-muted);
}
```

**Step 3: Bind sidebar button**

Add to `bindStaticEventListeners`:

```javascript
  // New session: sidebar footer button
  on($('sidebar-new-session-btn'), 'click', function() {
    showNewSessionInput($('sidebar-new-session-btn'));
  });
```

**Step 4: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: add sidebar + New sticky footer with inline input"
```

---

### Task 6: Mobile FAB

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/style.css`

**Step 1: Add FAB HTML**

In `muxplex/frontend/index.html`, add before the toast element:

```html
  <!-- ── Mobile FAB (new session) ─────────────────────────────── -->
  <button id="new-session-fab" class="new-session-fab" aria-label="New session">+</button>
```

**Step 2: Add FAB CSS**

Append to `muxplex/frontend/style.css`:

```css
/* ============================================================
   Mobile FAB (new session)
   ============================================================ */

.new-session-fab {
  display: none;  /* hidden by default, shown on mobile */
  position: fixed;
  bottom: 16px;
  right: 16px;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--accent);
  color: var(--bg);
  border: none;
  font-size: 28px;
  font-weight: 300;
  line-height: 1;
  cursor: pointer;
  z-index: 100;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  transition: transform var(--t-fast), box-shadow var(--t-fast);
}

.new-session-fab:active {
  transform: scale(0.95);
}

@media (max-width: 959px) {
  .new-session-fab {
    display: flex;
    align-items: center;
    justify-content: center;
  }

  /* Hide the header + button on mobile since FAB replaces it */
  #new-session-btn {
    display: none;
  }
}
```

**Step 3: Bind FAB click**

Add to `bindStaticEventListeners`:

```javascript
  // New session: mobile FAB
  on($('new-session-fab'), 'click', function() {
    showNewSessionInput($('new-session-fab'));
  });
```

**Step 4: Hide FAB when in fullscreen view**

In `openSession`, add after switching views:

```javascript
  var fab = $('new-session-fab');
  if (fab) fab.classList.add('hidden');
```

In `closeSession`, restore the FAB:

```javascript
  var fab = $('new-session-fab');
  if (fab) fab.classList.remove('hidden');
```

**Step 5: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add -A && git commit -m "feat: add mobile FAB for new session creation"
```

---

### Task 7: Apply settings effects — font size, grid columns, hover delay, hidden sessions, sort order

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/terminal.js`

**Step 1: Font size affects terminal.js**

In `muxplex/frontend/terminal.js`, update the `createTerminal` function to read font size from localStorage:

Replace the hardcoded fontSize in `createTerminal()`:

```javascript
  // Read font size from display settings (localStorage)
  var storedFontSize = 14;
  try {
    var raw = localStorage.getItem('muxplex.display');
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed.fontSize) storedFontSize = parsed.fontSize;
    }
  } catch (_) {}

  const mobile = window.innerWidth < 600;

  _term = new window.Terminal({
    cursorBlink: true,
    fontSize: mobile ? Math.min(storedFontSize, 12) : storedFontSize,
    fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
    // ... rest unchanged
  });
```

**Step 2: Hidden sessions filter in renderGrid**

In `muxplex/frontend/app.js`, update `renderGrid` to filter hidden sessions. At the top of the function, before computing `ordered`:

```javascript
  // Filter hidden sessions (server settings)
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  var visible = sessions;
  if (hidden.length > 0) {
    visible = sessions.filter(function(s) {
      return hidden.indexOf(s.name) === -1;
    });
  }
```

Then use `visible` instead of `sessions` for the rest of the function. Update the empty-state check and the `ordered` computation:

```javascript
  if (!visible || visible.length === 0) {
    if (grid) grid.innerHTML = '';
    if (emptyState) emptyState.classList.remove('hidden');
    return;
  }

  if (emptyState) emptyState.classList.add('hidden');

  const mobile = isMobile();
  const ordered = mobile ? sortByPriority(visible) : visible;
```

**Step 3: Sort order support in renderGrid**

Update `renderGrid` to apply sort order from server settings. After the hidden filter, before the mobile check:

```javascript
  // Apply sort order from server settings
  var sortOrder = (_serverSettings && _serverSettings.sort_order) || 'manual';
  if (sortOrder === 'alphabetical') {
    visible = visible.slice().sort(function(a, b) {
      return (a.name || '').localeCompare(b.name || '');
    });
  }
  // 'recent' and 'manual' use the server-provided order (which is already the default)
```

**Step 4: Hidden sessions filter in renderSidebar**

In `renderSidebar`, apply the same hidden filter:

```javascript
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  var visible = sessions;
  if (hidden.length > 0) {
    visible = sessions.filter(function(s) {
      return hidden.indexOf(s.name) === -1;
    });
  }
```

Use `visible` instead of `sessions` for the sidebar rendering.

**Step 5: Load server settings on page init**

In the `DOMContentLoaded` handler, after `startPolling()`, add a call to load server settings:

```javascript
  restoreState()
    .then(() => {
      startPolling();
      startHeartbeat();
      requestNotificationPermission();
      loadServerSettings();  // <-- add this line
      bindStaticEventListeners();
    })
```

**Step 6: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 7: Commit**

```bash
git add -A && git commit -m "feat: apply settings effects — font size, grid columns, hidden sessions, sort order"
```

---

### Task 8: Frontend tests for settings

**Files:**
- Modify: `muxplex/tests/test_frontend_html.py`
- Modify: `muxplex/tests/test_frontend_css.py`

**Step 1: Add HTML tests for settings elements**

Add to `muxplex/tests/test_frontend_html.py`:

```python
def test_html_settings_dialog() -> None:
    """id=settings-dialog, settings-backdrop, settings-btn."""
    soup = _SOUP
    for id_ in ("settings-dialog", "settings-backdrop", "settings-btn"):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_settings_tabs() -> None:
    """Settings dialog contains four tab buttons: display, sessions, notifications, new-session."""
    soup = _SOUP
    tabs = soup.select(".settings-tab")
    tab_names = [t.get("data-tab") for t in tabs]
    assert "display" in tab_names
    assert "sessions" in tab_names
    assert "notifications" in tab_names
    assert "new-session" in tab_names


def test_html_display_tab_controls() -> None:
    """Display tab has font-size, hover-delay, and grid-columns selects."""
    soup = _SOUP
    for id_ in ("setting-font-size", "setting-hover-delay", "setting-grid-columns"):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_new_session_btn() -> None:
    """id=new-session-btn, new-session-fab exist."""
    soup = _SOUP
    assert soup.find(id="new-session-btn"), "Missing element with id='new-session-btn'"
    assert soup.find(id="new-session-fab"), "Missing element with id='new-session-fab'"


def test_html_sidebar_new_session_btn() -> None:
    """id=sidebar-new-session-btn exists inside the sidebar."""
    soup = _SOUP
    assert soup.find(id="sidebar-new-session-btn"), "Missing element with id='sidebar-new-session-btn'"


def test_html_sessions_tab_controls() -> None:
    """Sessions tab has default-session, sort-order selects and checkboxes."""
    soup = _SOUP
    for id_ in ("setting-default-session", "setting-sort-order", "setting-window-size-largest", "setting-auto-open"):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"


def test_html_notifications_tab_controls() -> None:
    """Notifications tab has bell-sound checkbox and permission button."""
    soup = _SOUP
    assert soup.find(id="setting-bell-sound"), "Missing id='setting-bell-sound'"
    assert soup.find(id="notification-request-btn"), "Missing id='notification-request-btn'"


def test_html_new_session_tab_controls() -> None:
    """New Session tab has template textarea and reset button."""
    soup = _SOUP
    assert soup.find(id="setting-template"), "Missing id='setting-template'"
    assert soup.find(id="setting-template-reset"), "Missing id='setting-template-reset'"
```

**Step 2: Add CSS tests for settings styles**

Add to `muxplex/tests/test_frontend_css.py`:

```python
def test_css_settings_dialog():
    css = read_css()
    assert ".settings-dialog" in css
    assert ".settings-tabs" in css
    assert ".settings-tab--active" in css
    assert ".settings-content" in css
    assert ".settings-field" in css
    assert ".settings-select" in css


def test_css_header_btn():
    css = read_css()
    assert ".header-btn" in css
    assert ".header-actions" in css


def test_css_new_session_fab():
    css = read_css()
    assert ".new-session-fab" in css


def test_css_new_session_input():
    css = read_css()
    assert ".new-session-input" in css


def test_css_settings_textarea():
    css = read_css()
    assert ".settings-textarea" in css


def test_css_sidebar_footer():
    css = read_css()
    assert ".sidebar-footer" in css
    assert ".sidebar-new-btn" in css
```

**Step 3: Run all tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass (both old and new).

**Step 4: Commit**

```bash
git add -A && git commit -m "test: add frontend tests for settings dialog and new session UI"
```

---

## Phase 2 Complete

After completing all 8 tasks, the full settings feature is done:

1. **Sessions tab** — default session, sort order, hidden sessions, window-size largest, auto-open
2. **Notifications tab** — bell sound toggle, desktop notification permission button
3. **New Session tab** — command template textarea with reset button
4. **Header `+` button** — inline name input, creates session via API
5. **Sidebar `+ New`** — sticky footer with same inline input
6. **Mobile FAB** — 56px floating action button, <960px only
7. **Settings effects applied** — font size, grid columns, hover delay, hidden sessions, sort order
8. **Frontend tests** — HTML structure and CSS coverage for all new elements

### Open questions (from design doc — decide during implementation):
1. Hidden sessions: completely removed from grid/sidebar (current implementation)
2. Session name validation: accepts anything the user types (tmux will reject invalid names)
3. Settings pre-auth: not accessible from login page (v1)
# Consolidate Settings Server-Side — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Move all browser localStorage settings to server-side `settings.json` so the only remaining localStorage usage is `tmux-web-device-id`.

**Architecture:** Add 10 flat keys to `DEFAULT_SETTINGS` in Python (zero infrastructure changes — existing `load/save/patch_settings` iterate `DEFAULT_SETTINGS` automatically). Replace the localStorage read/write layer in `app.js` with a `getDisplaySettings()` function that reads from the `_serverSettings` in-memory cache. Eager-load settings on startup via `await loadServerSettings()` before first render.

**Tech Stack:** Python (FastAPI server, pytest), vanilla JS (no framework, no build system, Node.js test runner)

**Design doc:** `docs/plans/2026-04-08-server-side-settings-design.md`

---

## Codebase orientation

All paths are relative to the `muxplex/` submodule root at `/home/bkrabach/dev/muxplex-settings/muxplex/`.

| File | Role |
|------|------|
| `muxplex/settings.py` | `DEFAULT_SETTINGS` dict (line 16–34), `load_settings()`, `save_settings()`, `patch_settings()` |
| `muxplex/frontend/app.js` | ~2613 lines, vanilla JS, all frontend logic |
| `muxplex/tests/test_settings.py` | pytest suite, uses `tmp_path` + `monkeypatch` to redirect `SETTINGS_PATH` |
| `muxplex/frontend/tests/test_app.mjs` | Node.js `node:test` runner, ~4495 lines, CJS `require()` import of `app.js` |
| `muxplex/main.py` | FastAPI routes — **NO changes needed** (`GET/PATCH /api/settings` auto-handles new keys) |

**Run commands:**
- Python tests: `cd /home/bkrabach/dev/muxplex-settings/muxplex && python3 -m pytest muxplex/tests/test_settings.py -v`
- JS tests: `cd /home/bkrabach/dev/muxplex-settings/muxplex && node --test muxplex/frontend/tests/test_app.mjs`

**Note on `auto_open` fix:** The design doc mentions fixing `ss.auto_open` → `ss.auto_open_created`. This has already been corrected in the current codebase (all 4 references use `auto_open_created`). Task 3 includes a verification grep to confirm.

---

## Task 1: Add display settings to `DEFAULT_SETTINGS`

**Files:**
- Modify: `muxplex/settings.py` (lines 16–34)
- Test: `muxplex/tests/test_settings.py`

### Step 1: Write the failing tests

Add the following two tests at the **bottom** of `muxplex/tests/test_settings.py`, before any final blank line:

```python
# ============================================================
# Display settings (task: consolidate settings server-side)
# ============================================================


def test_defaults_include_display_settings():
    """DEFAULT_SETTINGS must include all 10 display/UI keys with correct defaults."""
    expected = {
        "fontSize": 14,
        "hoverPreviewDelay": 1500,
        "gridColumns": "auto",
        "bellSound": False,
        "viewMode": "auto",
        "showDeviceBadges": True,
        "showHoverPreview": True,
        "activityIndicator": "both",
        "gridViewMode": "flat",
        "sidebarOpen": None,
    }
    for key, value in expected.items():
        assert key in DEFAULT_SETTINGS, (
            f"DEFAULT_SETTINGS must include '{key}'"
        )
        assert DEFAULT_SETTINGS[key] == value, (
            f"DEFAULT_SETTINGS['{key}'] must be {value!r}, got: {DEFAULT_SETTINGS[key]!r}"
        )


def test_display_settings_round_trip_via_patch():
    """Display settings survive a patch_settings() + load_settings() cycle."""
    custom = {
        "fontSize": 18,
        "hoverPreviewDelay": 500,
        "gridColumns": 3,
        "bellSound": True,
        "viewMode": "fit",
        "showDeviceBadges": False,
        "showHoverPreview": False,
        "activityIndicator": "glow",
        "gridViewMode": "grouped",
        "sidebarOpen": True,
    }
    result = patch_settings(custom)
    for key, value in custom.items():
        assert result[key] == value, (
            f"patch_settings() must accept '{key}={value!r}', got: {result[key]!r}"
        )

    loaded = load_settings()
    for key, value in custom.items():
        assert loaded[key] == value, (
            f"load_settings() must return '{key}={value!r}' after patch, got: {loaded[key]!r}"
        )
```

### Step 2: Run tests to verify they fail

Run:
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && python3 -m pytest muxplex/tests/test_settings.py::test_defaults_include_display_settings muxplex/tests/test_settings.py::test_display_settings_round_trip_via_patch -v
```

Expected: **FAIL** — `fontSize` not in `DEFAULT_SETTINGS`.

### Step 3: Add the 10 new keys to `DEFAULT_SETTINGS`

In `muxplex/settings.py`, add these 10 entries to the `DEFAULT_SETTINGS` dict (after the `"tls_key": ""` line, before the closing `}`):

```python
    # Display preferences (consumed by frontend)
    "fontSize": 14,
    "hoverPreviewDelay": 1500,
    "gridColumns": "auto",
    "bellSound": False,
    "viewMode": "auto",
    "showDeviceBadges": True,
    "showHoverPreview": True,
    "activityIndicator": "both",
    "gridViewMode": "flat",
    # UI state
    "sidebarOpen": None,
```

The dict should now have 27 keys total (17 existing + 10 new).

### Step 4: Run tests to verify they pass

Run:
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && python3 -m pytest muxplex/tests/test_settings.py -v
```

Expected: **ALL PASS** — both new tests and all existing tests.

### Step 5: Commit

```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: add display settings keys to DEFAULT_SETTINGS

Add 10 new flat keys for display preferences (fontSize,
hoverPreviewDelay, gridColumns, bellSound, viewMode, showDeviceBadges,
showHoverPreview, activityIndicator, gridViewMode) and UI state
(sidebarOpen) to DEFAULT_SETTINGS.

No changes needed to load/save/patch functions — they iterate
DEFAULT_SETTINGS keys automatically."
```

---

## Task 2: Replace frontend storage layer

**Files:**
- Modify: `muxplex/frontend/app.js`

This task deletes the localStorage-based storage functions and replaces them with a server-settings-cache-based accessor. No callsites are updated yet (that's Task 3).

### Step 1: Delete `DISPLAY_SETTINGS_KEY` constant

Find this line (around line 140):

```javascript
const DISPLAY_SETTINGS_KEY = 'muxplex.display';
```

Delete the entire line.

### Step 2: Remove `notificationPermission` from `DISPLAY_DEFAULTS` and add `gridViewMode`

Find the `DISPLAY_DEFAULTS` object (around lines 141–151). Replace it with:

```javascript
const DISPLAY_DEFAULTS = {
  fontSize: 14,
  hoverPreviewDelay: 1500,
  gridColumns: 'auto',
  bellSound: false,
  viewMode: 'auto',
  showDeviceBadges: true,
  showHoverPreview: true,
  activityIndicator: 'both',
  gridViewMode: 'flat',
};
```

Changes: removed `notificationPermission: 'default'`, added `gridViewMode: 'flat'`.

### Step 3: Delete `SIDEBAR_KEY` constant

Find this line (around line 598):

```javascript
const SIDEBAR_KEY = 'muxplex.sidebarOpen';
```

Delete the entire line.

### Step 4: Replace `loadDisplaySettings()` with `getDisplaySettings()`

Find the `loadDisplaySettings` function (around lines 1435–1444):

```javascript
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
```

Replace the entire function with:

```javascript
/**
 * Get display settings from the server settings cache, falling back to
 * DISPLAY_DEFAULTS for any missing keys or before the initial fetch.
 * @returns {object}
 */
function getDisplaySettings() {
  var result = Object.assign({}, DISPLAY_DEFAULTS);
  if (_serverSettings) {
    for (var key in DISPLAY_DEFAULTS) {
      if (Object.prototype.hasOwnProperty.call(DISPLAY_DEFAULTS, key) && _serverSettings[key] !== undefined) {
        result[key] = _serverSettings[key];
      }
    }
  }
  return result;
}
```

### Step 5: Delete `saveDisplaySettings()`

Find the `saveDisplaySettings` function (immediately after `getDisplaySettings`, around lines 1446–1454):

```javascript
/**
 * Save display settings to localStorage.
 * @param {object} settings
 */
function saveDisplaySettings(settings) {
  try {
    localStorage.setItem(DISPLAY_SETTINGS_KEY, JSON.stringify(settings));
  } catch (_) { /* blocked — ok */ }
}
```

Delete the entire function (JSDoc comment and all).

### Step 6: Update module.exports

Find the exports block at the bottom of `app.js` (around lines 2527–2613). In the `module.exports` object:

1. Replace `loadDisplaySettings,` with `getDisplaySettings,`
2. Delete `saveDisplaySettings,`

### Step 7: Verify the file parses

Run:
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && node -e "require('./muxplex/frontend/app.js')" 2>&1 | head -5
```

Expected: Errors about `loadDisplaySettings is not defined` at various callsites. This is expected — callsites are updated in Task 3.

### Step 8: Commit

```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && git add muxplex/frontend/app.js && git commit -m "refactor: replace localStorage storage layer with server-settings cache

Delete DISPLAY_SETTINGS_KEY, SIDEBAR_KEY constants.
Delete loadDisplaySettings() and saveDisplaySettings() functions.
Add getDisplaySettings() that reads from _serverSettings cache
with DISPLAY_DEFAULTS fallback.
Remove notificationPermission from DISPLAY_DEFAULTS (browser API
is the source of truth). Add gridViewMode to DISPLAY_DEFAULTS.

Callsites are updated in the next commit — this commit will have
broken references until then."
```

---

## Task 3: Update all frontend callsites

**Files:**
- Modify: `muxplex/frontend/app.js`

This task is entirely mechanical: rename all `loadDisplaySettings()` calls to `getDisplaySettings()`, and replace all `saveDisplaySettings()` calls with `patchServerSetting()` equivalents. Here is every single callsite.

### Step 1: Rename all `loadDisplaySettings()` → `getDisplaySettings()` reads

There are 15 read callsites (excluding the deleted definition and the already-updated export). Find and replace each one. You can use a global find-and-replace for this since the function name is unique:

**Replace all occurrences** of `loadDisplaySettings()` with `getDisplaySettings()` in `app.js`.

The affected lines (approximate, may shift after Task 2 edits) are:

| Line | Function | What it reads |
|------|----------|---------------|
| ~407 | `buildTileHTML()` | `activityIndicator`, `showDeviceBadges` |
| ~465 | `buildSidebarHTML()` | `activityIndicator` |
| ~863 | `renderGrid()` | `viewMode` |
| ~890 | `showPreview()` | `showHoverPreview` |
| ~1289 | `closeSession()` | `viewMode` |
| ~1499 | `cycleViewMode()` | `viewMode` |
| ~1560 | `loadGridViewMode()` | `gridViewMode` |
| ~1569 | `saveGridViewMode()` | all (reads before writing) |
| ~1581 | `onDisplaySettingChange()` | all display controls |
| ~1642 | `openSettings()` | all display controls |
| ~2238 | hover handler (grid) | `hoverPreviewDelay` |
| ~2257 | hover handler (sidebar) | `hoverPreviewDelay` |
| ~2318 | bell sound handler | all (reads before writing) |
| ~2328 | notification permission handler | all (reads before writing) |
| ~2491 | resize handler | `viewMode` |
| ~2500 | `DOMContentLoaded` | all (initial apply) |

### Step 2: Replace `saveDisplaySettings()` in `cycleViewMode()`

Find `cycleViewMode()` (around line 1498). The current code is:

```javascript
function cycleViewMode() {
  var ds = getDisplaySettings();
  var idx = VIEW_MODES.indexOf(ds.viewMode || 'auto');
  ds.viewMode = VIEW_MODES[(idx + 1) % VIEW_MODES.length];
  saveDisplaySettings(ds);
  applyDisplaySettings(ds);

  // Update button label
  var btn = document.getElementById('view-mode-btn');
  if (btn) btn.title = 'View: ' + ds.viewMode;
}
```

Replace `saveDisplaySettings(ds);` with:

```javascript
  if (_serverSettings) _serverSettings.viewMode = ds.viewMode;
  patchServerSetting('viewMode', ds.viewMode);
```

The full function becomes:

```javascript
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
```

### Step 3: Rewrite `saveGridViewMode()`

Find `saveGridViewMode()` (around line 1568). Replace the entire function:

**Before:**
```javascript
function saveGridViewMode(mode) {
  var ds = getDisplaySettings();
  ds.gridViewMode = mode;
  saveDisplaySettings(ds);
  _gridViewMode = mode;
}
```

**After:**
```javascript
function saveGridViewMode(mode) {
  if (_serverSettings) _serverSettings.gridViewMode = mode;
  patchServerSetting('gridViewMode', mode);
  _gridViewMode = mode;
}
```

### Step 4: Rewrite `onDisplaySettingChange()` to batch-PATCH

Find `onDisplaySettingChange()` (around line 1580). Replace the entire function:

**Before:**
```javascript
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

  saveDisplaySettings(ds);
  applyDisplaySettings(ds);
}
```

**After:**
```javascript
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

  // Batch-update server settings cache and persist
  var patch = {
    fontSize: ds.fontSize,
    hoverPreviewDelay: ds.hoverPreviewDelay,
    gridColumns: ds.gridColumns,
    showDeviceBadges: ds.showDeviceBadges,
    showHoverPreview: ds.showHoverPreview,
    activityIndicator: ds.activityIndicator,
  };
  if (_serverSettings) Object.assign(_serverSettings, patch);
  api('PATCH', '/api/settings', patch).then(function() {
    showToast('Setting saved');
  }).catch(function(err) {
    showToast('Failed to save setting');
    console.warn('[onDisplaySettingChange] failed:', err);
  });

  applyDisplaySettings(ds);
}
```

### Step 5: Replace `saveDisplaySettings()` in the bell sound handler

Find the bell sound change handler in `bindStaticEventListeners()` (around line 2317):

**Before:**
```javascript
  // Notifications settings — bell sound toggle persists to display settings localStorage
  on($('setting-bell-sound'), 'change', function() {
    const ds = getDisplaySettings();
    ds.bellSound = this.checked;
    saveDisplaySettings(ds);
  });
```

**After:**
```javascript
  // Notifications settings — bell sound toggle persists to server settings
  on($('setting-bell-sound'), 'change', function() {
    if (_serverSettings) _serverSettings.bellSound = this.checked;
    patchServerSetting('bellSound', this.checked);
  });
```

### Step 6: Simplify the notification permission handler

Find the notification request button handler (around line 2324):

**Before:**
```javascript
  on($('notification-request-btn'), 'click', function() {
    if (typeof Notification === 'undefined') return;
    Notification.requestPermission().then(function(permission) {
      _notificationPermission = permission;
      const ds = getDisplaySettings();
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
```

**After** (remove the `getDisplaySettings` / `saveDisplaySettings` lines — `notificationPermission` is no longer stored):
```javascript
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
```

### Step 7: Verify `auto_open_created` is correct

Run:
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && grep -n 'auto_open' muxplex/frontend/app.js
```

Expected: All occurrences should show `auto_open_created` — no bare `auto_open` references. (This was already fixed in a prior commit.)

### Step 8: Verify no `saveDisplaySettings` references remain

Run:
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && grep -n 'saveDisplaySettings' muxplex/frontend/app.js
```

Expected: **Zero matches.**

### Step 9: Commit

```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && git add muxplex/frontend/app.js && git commit -m "refactor: update all display settings callsites to use server settings

Replace 15 loadDisplaySettings() calls with getDisplaySettings().
Replace saveDisplaySettings() calls in cycleViewMode, saveGridViewMode,
onDisplaySettingChange, and bell sound handler with patchServerSetting()
or batch PATCH.
Remove notificationPermission storage from notification request handler
(browser API is the source of truth).
Verify auto_open_created alignment is correct."
```

---

## Task 4: Rewrite sidebar functions

**Files:**
- Modify: `muxplex/frontend/app.js`

The three sidebar functions (`initSidebar`, `toggleSidebar`, `bindSidebarClickAway`) currently read/write `localStorage` via `SIDEBAR_KEY`. Rewrite them to use `_serverSettings.sidebarOpen` with `patchServerSetting()`.

### Step 1: Rewrite `initSidebar()`

Find `initSidebar()` (around line 607, after `SIDEBAR_NARROW_THRESHOLD`). Replace the entire function:

**Before:**
```javascript
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
```

**After:**
```javascript
/**
 * Initialise sidebar open/closed state on page load.
 * Reads sidebarOpen from server settings cache.
 * When null (never set by user), defaults to open on wide screens (>= 960px).
 * Applies sidebar--collapsed class accordingly.
 */
function initSidebar() {
  var sidebarSetting = _serverSettings ? _serverSettings.sidebarOpen : null;
  var isOpen;
  if (sidebarSetting === true || sidebarSetting === false) {
    isOpen = sidebarSetting;
  } else {
    // null or undefined = auto-detect from screen width
    isOpen = window.innerWidth >= SIDEBAR_NARROW_THRESHOLD;
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
```

### Step 2: Rewrite `toggleSidebar()`

Find `toggleSidebar()` (around line 641). Replace the entire function:

**Before:**
```javascript
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
```

**After:**
```javascript
/**
 * Toggle the sidebar open/closed state.
 * Reads current state from server settings cache, inverts it, persists
 * via patchServerSetting (fire-and-forget), applies sidebar--collapsed
 * class, and updates the collapse button text.
 * Button shows ‹ when open, › when closed.
 */
function toggleSidebar() {
  var sidebarSetting = _serverSettings ? _serverSettings.sidebarOpen : null;
  var isOpen;
  if (sidebarSetting === true || sidebarSetting === false) {
    isOpen = sidebarSetting;
  } else {
    isOpen = true;  // default to open if never set
  }

  // Invert state
  isOpen = !isOpen;

  // Update cache and persist (fire-and-forget)
  if (_serverSettings) _serverSettings.sidebarOpen = isOpen;
  patchServerSetting('sidebarOpen', isOpen);

  // Apply class
  var sidebar = $('session-sidebar');
  if (sidebar) {
    if (isOpen) {
      sidebar.classList.remove('sidebar--collapsed');
    } else {
      sidebar.classList.add('sidebar--collapsed');
    }
  }

  // Update collapse button text (‹ when open, › when closed)
  var collapseBtn = $('sidebar-collapse-btn');
  if (collapseBtn) {
    collapseBtn.textContent = isOpen ? '\u2039' : '\u203a';
  }
}
```

### Step 3: Rewrite `bindSidebarClickAway()`

Find `bindSidebarClickAway()` (around line 683). Replace the entire function:

**Before:**
```javascript
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
```

**After:**
```javascript
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
```

### Step 4: Verify no localStorage references remain (except `tmux-web-device-id`)

Run:
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && grep -n 'localStorage' muxplex/frontend/app.js
```

Expected: Only 3 occurrences, all inside `initDeviceId()` (around lines 191, 194, 200):
```
191:    let id = localStorage.getItem(STORAGE_KEY);
194:      try { localStorage.setItem(STORAGE_KEY, id); } catch (_) { /* blocked — ok */ }
200:    if (!_deviceId) _deviceId = generateDeviceId();
```

No other `localStorage` references should exist.

### Step 5: Verify no `SIDEBAR_KEY` or `DISPLAY_SETTINGS_KEY` references remain

Run:
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && grep -n 'DISPLAY_SETTINGS_KEY\|SIDEBAR_KEY\|muxplex\.display\|muxplex\.sidebarOpen' muxplex/frontend/app.js
```

Expected: **Zero matches.**

### Step 6: Commit

```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && git add muxplex/frontend/app.js && git commit -m "refactor: rewrite sidebar functions to use server settings

Rewrite initSidebar(), toggleSidebar(), bindSidebarClickAway() to read
from _serverSettings.sidebarOpen instead of localStorage.
Persist sidebar state via patchServerSetting() (fire-and-forget).
sidebarOpen=null triggers screen-width auto-detect (>= 960px = open).

Only remaining localStorage usage: tmux-web-device-id in initDeviceId()."
```

---

## Task 5: Update startup sequence and tests

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

### Step 1: Rewrite the `DOMContentLoaded` handler

Find the handler (around line 2498). Replace the entire block:

**Before:**
```javascript
document.addEventListener('DOMContentLoaded', () => {
  initDeviceId();
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
    .then(() => {
      startPolling();
      loadServerSettings().then(function() {
        updatePageTitle();
      });
      startHeartbeat();
      bindStaticEventListeners();
    })
    .catch((err) => {
      console.error('[init] restoreState failed, retrying in 5s:', err);
      setTimeout(() => startPolling(), POLL_MS);
    });
});
```

**After:**
```javascript
document.addEventListener('DOMContentLoaded', async () => {
  initDeviceId();
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
    .then(() => {
      startPolling();
      updatePageTitle();
      startHeartbeat();
      bindStaticEventListeners();
    })
    .catch((err) => {
      console.error('[init] restoreState failed, retrying in 5s:', err);
      setTimeout(() => startPolling(), POLL_MS);
    });
});
```

Key changes:
1. Added `async` to the arrow function
2. Added `await loadServerSettings()` **before** `getDisplaySettings()` — settings must be loaded before first render
3. `loadDisplaySettings()` was already renamed to `getDisplaySettings()` in Task 3
4. Removed the nested `loadServerSettings().then(...)` inside the `.then()` chain (it's now at the top)
5. Moved `updatePageTitle()` directly into the `.then()` chain (no longer needs its own `.then`)

### Step 2: Update the `cycleViewMode` test in `test_app.mjs`

Find the test at around line 3080 in `muxplex/frontend/tests/test_app.mjs`:

**Before:**
```javascript
test('cycleViewMode cycles through auto -> fit -> auto (two modes, compact removed)', () => {
  // Reset display settings to auto
  const ds = app.loadDisplaySettings();
  ds.viewMode = 'auto';
  app.saveDisplaySettings(ds);

  // First cycle: auto -> fit
  app.cycleViewMode();
  const ds1 = app.loadDisplaySettings();
  assert.strictEqual(ds1.viewMode, 'fit', 'first cycle should go auto -> fit');

  // Second cycle: fit -> auto (wraps, compact is gone)
  app.cycleViewMode();
  const ds2 = app.loadDisplaySettings();
  assert.strictEqual(ds2.viewMode, 'auto', 'second cycle should wrap fit -> auto (only two modes)');
});
```

**After:**
```javascript
test('cycleViewMode cycles through auto -> fit -> auto (two modes, compact removed)', () => {
  // Reset display settings to auto via server settings cache
  app._setServerSettings({ viewMode: 'auto' });

  // First cycle: auto -> fit
  app.cycleViewMode();
  const ds1 = app.getDisplaySettings();
  assert.strictEqual(ds1.viewMode, 'fit', 'first cycle should go auto -> fit');

  // Second cycle: fit -> auto (wraps, compact is gone)
  app.cycleViewMode();
  const ds2 = app.getDisplaySettings();
  assert.strictEqual(ds2.viewMode, 'auto', 'second cycle should wrap fit -> auto (only two modes)');
});
```

### Step 3: Search for any other test references to the old functions

Run:
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && grep -n 'loadDisplaySettings\|saveDisplaySettings' muxplex/frontend/tests/test_app.mjs
```

Expected: **Zero matches** after the edits in Step 2.

### Step 4: Run the full verification checklist

Run each command and verify the expected output:

**Check 1: localStorage only used for device ID**
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && grep -n 'localStorage' muxplex/frontend/app.js
```
Expected: Only lines inside `initDeviceId()` (3 occurrences around lines 191, 194, 200).

**Check 2: No old constants or key strings**
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && grep -n 'DISPLAY_SETTINGS_KEY\|SIDEBAR_KEY\|muxplex\.display\|muxplex\.sidebarOpen' muxplex/frontend/app.js
```
Expected: **Zero matches.**

**Check 3: No old function names**
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && grep -n 'loadDisplaySettings\|saveDisplaySettings' muxplex/frontend/app.js
```
Expected: **Zero matches.**

**Check 4: Python tests pass**
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && python3 -m pytest muxplex/tests/test_settings.py -v
```
Expected: **ALL PASS.**

**Check 5: JS tests pass**
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -20
```
Expected: Tests pass (check for `# pass` count and no unexpected failures).

**Check 6: app.js parses without errors**
```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && node -e "const app = require('./muxplex/frontend/app.js'); console.log('getDisplaySettings' in app ? 'OK: getDisplaySettings exported' : 'FAIL: getDisplaySettings missing'); console.log('loadDisplaySettings' in app ? 'FAIL: loadDisplaySettings still exported' : 'OK: loadDisplaySettings removed'); console.log('saveDisplaySettings' in app ? 'FAIL: saveDisplaySettings still exported' : 'OK: saveDisplaySettings removed');"
```
Expected:
```
OK: getDisplaySettings exported
OK: loadDisplaySettings removed
OK: saveDisplaySettings removed
```

### Step 5: Commit

```bash
cd /home/bkrabach/dev/muxplex-settings/muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs && git commit -m "feat: eager-load server settings on startup, update tests

Make DOMContentLoaded handler async and await loadServerSettings()
before first render so display settings (fontSize, gridColumns, etc.)
are available before anything renders.
Remove redundant loadServerSettings() call from restoreState chain.
Update cycleViewMode test to use _setServerSettings/getDisplaySettings
instead of localStorage-based functions."
```

---

## Summary of all changes

| File | What changed |
|------|-------------|
| `muxplex/settings.py` | +10 keys in `DEFAULT_SETTINGS` |
| `muxplex/tests/test_settings.py` | +2 tests (defaults exist, round-trip via patch) |
| `muxplex/frontend/app.js` | Delete `DISPLAY_SETTINGS_KEY`, `SIDEBAR_KEY`, `loadDisplaySettings()`, `saveDisplaySettings()`. Add `getDisplaySettings()`. Rewrite `initSidebar()`, `toggleSidebar()`, `bindSidebarClickAway()`. Update `cycleViewMode()`, `saveGridViewMode()`, `onDisplaySettingChange()`, bell/notification handlers. Async DOMContentLoaded with eager settings load. |
| `muxplex/frontend/tests/test_app.mjs` | Update `cycleViewMode` test to use server settings cache |
| `muxplex/main.py` | **No changes** |

**After this work, the only remaining `localStorage` usage is `tmux-web-device-id` in `initDeviceId()` — per-browser device identity for heartbeats, not a user setting.**

## Future work

- **Federation settings sync**: Investigate syncing user preferences across federated servers (deferred — each server owns its own settings for now).

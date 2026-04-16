# Views Feature — Phase 3: Tile Flyout Menu + Add Sessions Panel + Final Integration

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Build the tile-level interaction UI (flyout `⋮` menu with context-dependent actions, inline kill confirmation) and the Add Sessions panel, then verify all three phases work together end-to-end.

**Architecture:** An always-visible `⋮` button on every session tile opens a floating flyout menu (appended to `document.body` with `position: fixed` and JS-calculated coordinates to avoid z-index/stacking issues). Menu items are generated from a data map keyed by view type (`all`, `user`, `hidden`) — not if/else chains. The flyout replaces the old `confirm()` kill dialog and `.tile-delete` button entirely. A separate Add Sessions panel is an overlay for bulk-adding sessions to a user view with immediate-commit checkboxes. On mobile (`window.innerWidth < 600`), the flyout renders as a bottom action sheet and the Add Sessions panel renders as a full-screen sheet.

**Tech Stack:** Python 3.12+ / FastAPI / pytest + pytest-asyncio / vanilla JS / CSS

**Design reference:** `docs/plans/2026-04-15-views-design.md`
**Phase 1 reference:** `docs/plans/2026-04-15-views-phase1-implementation.md`
**Phase 2 reference:** `docs/plans/2026-04-15-views-phase2-implementation.md`

**Assumes Phase 1+2 are complete:** `identity.py` exists, session keys use `device_id:name`, `views` is in `DEFAULT_SETTINGS` and `SYNCABLE_KEYS`, `active_view` is in state schema, `views.py` has `enforce_mutual_exclusion()` and `validate_view_name()`, header dropdown works with `renderViewDropdown()` / `toggleViewDropdown()` / `switchView()`, Manage Views tab exists in settings, `getVisibleSessions()` honors active view, `_activeView` state variable exists, `filtered` gridViewMode removed, hidden sessions checkbox removed from settings, `_setActiveView` / `_getActiveView` test helpers exported.

---

## Task 1: Flyout Menu Base Component — CSS

**Files:**
- Modify: `muxplex/frontend/style.css`
- Test: `muxplex/tests/test_frontend_css.py`

**Step 1: Write the failing tests**

Add to the end of `muxplex/tests/test_frontend_css.py`:

```python
# ---------------------------------------------------------------------------
# Tile flyout menu styles
# ---------------------------------------------------------------------------


def test_flyout_menu_styled() -> None:
    """style.css must contain .flyout-menu styles."""
    css = read_css()
    assert ".flyout-menu" in css, "style.css must style .flyout-menu"


def test_flyout_menu_item_styled() -> None:
    """style.css must contain .flyout-menu__item styles."""
    css = read_css()
    assert ".flyout-menu__item" in css, "style.css must style .flyout-menu__item"


def test_flyout_trigger_styled() -> None:
    """style.css must contain .tile-options-btn styles."""
    css = read_css()
    assert ".tile-options-btn" in css, "style.css must style .tile-options-btn"


def test_flyout_submenu_styled() -> None:
    """style.css must contain .flyout-submenu styles."""
    css = read_css()
    assert ".flyout-submenu" in css, "style.css must style .flyout-submenu"


def test_flyout_bottom_sheet_styled() -> None:
    """style.css must contain .flyout-sheet styles for mobile."""
    css = read_css()
    assert ".flyout-sheet" in css, "style.css must style .flyout-sheet (mobile bottom action sheet)"
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_css.py::test_flyout_menu_styled -v
```
Expected: FAIL — `.flyout-menu` not in CSS

**Step 3: Add styles to `muxplex/frontend/style.css`**

Append before the media query sections (before line ~1645). Insert after the views settings tab styles:

```css
/* —— Tile Flyout Menu ————————————————————————————————————————————————— */

.tile-options-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-tile);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  color: var(--text-muted);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  z-index: 2;
  transition: border-color var(--t-fast), color var(--t-fast), background var(--t-fast);
}

.tile-options-btn:hover,
.tile-options-btn:focus-visible {
  border-color: var(--border);
  color: var(--text);
  background: var(--bg-surface);
}

.flyout-menu {
  position: fixed;
  min-width: 200px;
  max-width: 280px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px 0;
  z-index: 300;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
}

.flyout-menu__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 12px;
  background: transparent;
  border: none;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  position: relative;
}

.flyout-menu__item:hover,
.flyout-menu__item:focus-visible {
  background: var(--bg-surface);
}

.flyout-menu__item--danger {
  color: var(--err);
}

.flyout-menu__item--danger:hover {
  background: rgba(248, 81, 73, 0.1);
}

.flyout-menu__item--has-submenu::after {
  content: "\25B8";
  margin-left: auto;
  font-size: 10px;
  color: var(--text-dim);
}

.flyout-menu__separator {
  height: 1px;
  margin: 4px 0;
  background: var(--border-subtle);
}

.flyout-menu__confirm {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  font-size: 13px;
  color: var(--text-muted);
}

.flyout-menu__confirm-btn {
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
}

.flyout-menu__confirm-btn:hover {
  background: var(--bg-surface);
}

.flyout-menu__confirm-btn--yes {
  border-color: var(--err);
  color: var(--err);
}

.flyout-menu__confirm-btn--yes:hover {
  background: rgba(248, 81, 73, 0.15);
}

/* —— Flyout Submenu (Add to View) ————————————————————————————————————— */

.flyout-submenu {
  position: fixed;
  min-width: 180px;
  max-width: 240px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px 0;
  z-index: 310;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
}

.flyout-submenu__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 12px;
  background: transparent;
  border: none;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
}

.flyout-submenu__item:hover {
  background: var(--bg-surface);
}

.flyout-submenu__check {
  width: 14px;
  flex-shrink: 0;
  color: var(--accent);
  font-size: 12px;
}

/* —— Mobile: Flyout as bottom action sheet ————————————————————————————— */

.flyout-sheet {
  position: fixed;
  inset: 0;
  z-index: 300;
  display: flex;
  align-items: flex-end;
}

.flyout-sheet__backdrop {
  position: absolute;
  inset: 0;
  background: var(--bg-overlay);
}

.flyout-sheet__panel {
  position: relative;
  width: 100%;
  background: var(--bg-header);
  border-top: 1px solid var(--border);
  border-radius: 12px 12px 0 0;
  max-height: 70vh;
  overflow-y: auto;
  animation: sheet-up var(--t-zoom) ease;
  padding-bottom: env(safe-area-inset-bottom, 8px);
}

.flyout-sheet__handle {
  width: 36px;
  height: 4px;
  background: var(--border);
  border-radius: 2px;
  margin: 10px auto 6px;
}

.flyout-sheet__item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 14px 20px;
  background: transparent;
  border: none;
  color: var(--text);
  font-size: 15px;
  cursor: pointer;
  text-align: left;
}

.flyout-sheet__item:active {
  background: var(--bg-surface);
}

.flyout-sheet__item--danger {
  color: var(--err);
}

.flyout-sheet__separator {
  height: 1px;
  margin: 4px 0;
  background: var(--border-subtle);
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_css.py -k "flyout" -v
```
Expected: All 5 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/style.css muxplex/tests/test_frontend_css.py && git commit -m "feat: add flyout menu, submenu, and mobile bottom sheet CSS"
```

---

## Task 2: Add ⋮ Button to Session Tiles + Remove Old Kill Button

**Files:**
- Modify: `muxplex/frontend/app.js` (the `buildTileHTML()` function, lines 409–458)
- Test: `muxplex/tests/test_frontend_js.py`

The existing `.tile-delete` `×` button (line 455 of app.js) is replaced by a `⋮` options button. Kill session moves to the flyout menu (Task 7).

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
# ---------------------------------------------------------------------------
# Tile options button (⋮) replaces tile-delete
# ---------------------------------------------------------------------------


def test_tile_has_options_button() -> None:
    """buildTileHTML must include a .tile-options-btn button."""
    fn_body = _JS.split("function buildTileHTML")[1].split("\nfunction ")[0]
    assert "tile-options-btn" in fn_body, (
        "buildTileHTML must render a .tile-options-btn element"
    )


def test_tile_options_btn_has_aria() -> None:
    """The ⋮ button must have aria-label='Session options' and aria-haspopup='true'."""
    fn_body = _JS.split("function buildTileHTML")[1].split("\nfunction ")[0]
    assert 'aria-label' in fn_body and 'Session options' in fn_body, (
        "tile-options-btn must have aria-label='Session options'"
    )
    assert 'aria-haspopup' in fn_body, (
        "tile-options-btn must have aria-haspopup='true'"
    )


def test_tile_delete_button_removed() -> None:
    """buildTileHTML must NOT include the old .tile-delete button."""
    fn_body = _JS.split("function buildTileHTML")[1].split("\nfunction ")[0]
    assert "tile-delete" not in fn_body, (
        "buildTileHTML must not render the old .tile-delete button (kill moved to flyout)"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_tile_has_options_button muxplex/tests/test_frontend_js.py::test_tile_delete_button_removed -v
```
Expected: FAIL — `tile-options-btn` not in `buildTileHTML`, and `tile-delete` still present

**Step 3: Apply the change to `muxplex/frontend/app.js`**

In `buildTileHTML()` (line 455), replace the old `.tile-delete` button:

```javascript
// Before (line 455):
    `<button class="tile-delete" data-session="${escapedName}" aria-label="Kill session">&times;</button>` +

// After:
    `<button class="tile-options-btn" data-session="${escapedName}" aria-label="Session options" aria-haspopup="true">&#8942;</button>` +
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "tile_has_options or tile_options_btn_has_aria or tile_delete_button_removed" -v
```
Expected: All 3 PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: replace tile-delete button with ⋮ options button on session tiles"
```

---

## Task 3: Flyout Menu Base JS — Open, Position, Close, Event Delegation

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/tests/test_frontend_js.py`

Implement `openFlyoutMenu(sessionKey, triggerEl)`, `closeFlyoutMenu()`, and the delegated click listener on the tile container that opens the flyout when a `⋮` button is clicked. Also remove the old `.tile-delete` delegated click handler from `bindStaticEventListeners()` (lines 2185–2194).

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_open_flyout_menu_function_exists() -> None:
    """app.js must define an openFlyoutMenu function."""
    assert "function openFlyoutMenu" in _JS, (
        "app.js must contain an openFlyoutMenu function"
    )


def test_close_flyout_menu_function_exists() -> None:
    """app.js must define a closeFlyoutMenu function."""
    assert "function closeFlyoutMenu" in _JS, (
        "app.js must contain a closeFlyoutMenu function"
    )


def test_flyout_menu_uses_fixed_positioning() -> None:
    """openFlyoutMenu must use position:fixed and getBoundingClientRect for positioning."""
    fn_body = _JS.split("function openFlyoutMenu")[1].split("\nfunction ")[0]
    assert "getBoundingClientRect" in fn_body, (
        "openFlyoutMenu must use getBoundingClientRect to calculate position"
    )


def test_flyout_delegated_on_tile_container() -> None:
    """A delegated click listener must handle .tile-options-btn clicks."""
    assert "tile-options-btn" in _JS.split("bindStaticEventListeners")[1], (
        "bindStaticEventListeners must handle .tile-options-btn clicks via delegation"
    )


def test_old_tile_delete_handler_removed() -> None:
    """The old delegated .tile-delete click handler must be removed."""
    bind_body = _JS.split("function bindStaticEventListeners")[1].split("\nfunction ")[0]
    assert "tile-delete" not in bind_body, (
        "The old .tile-delete delegated handler must be removed from bindStaticEventListeners"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_open_flyout_menu_function_exists -v
```
Expected: FAIL — function does not exist

**Step 3: Apply the changes to `muxplex/frontend/app.js`**

Change 1 — Add a module-level state variable in the "App state" section (after `_previewSessionName` on line 133):
```javascript
// Flyout menu state
let _flyoutMenuEl = null;
let _flyoutSubmenuEl = null;
let _flyoutSessionKey = null;
let _flyoutSessionName = null;
let _flyoutRemoteId = null;
```

Change 2 — Add the flyout functions. Insert after the `showPreview` / `hidePreview` section (after line ~926):

```javascript
// ── Tile Flyout Menu ─────────────────────────────────────────────────────────

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
 * Click-outside handler for the flyout menu.
 * @param {MouseEvent} e
 */
function _flyoutOutsideClickHandler(e) {
  if (_flyoutMenuEl && !_flyoutMenuEl.contains(e.target) &&
      (!_flyoutSubmenuEl || !_flyoutSubmenuEl.contains(e.target))) {
    closeFlyoutMenu();
  }
}
```

Change 3 — Remove the old `.tile-delete` delegated handler. In `bindStaticEventListeners()` (lines 2185–2194), **replace** the entire block:

```javascript
// Before (lines 2184-2194):
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

// After:
  // Delegated ⋮ options button handler (tiles are re-rendered each poll)
  document.addEventListener('click', function(e) {
    var optionsBtn = e.target.closest && e.target.closest('.tile-options-btn');
    if (!optionsBtn) return;
    e.stopPropagation();
    openFlyoutMenu(optionsBtn);
  });
```

Change 4 — Export the new functions in `window.MuxplexApp` (after `killSession,`):
```javascript
    // Flyout menu
    openFlyoutMenu,
    closeFlyoutMenu,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "flyout_menu or tile_delete_handler" -v
```
Expected: All 5 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add flyout menu open/close/position base, replace old tile-delete handler"
```

---

## Task 4: Context-Dependent Menu Items — Data Map

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/tests/test_frontend_js.py`

Define `FLYOUT_MENU_MAP` — a data map keyed by view type (`all`, `user`, `hidden`) that returns the menu item configuration for each context. Implement `_buildFlyoutMenuItems()` which reads `_activeView` and the map to generate HTML.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_flyout_menu_map_exists() -> None:
    """app.js must define a FLYOUT_MENU_MAP data structure."""
    assert "FLYOUT_MENU_MAP" in _JS, (
        "app.js must contain a FLYOUT_MENU_MAP data structure"
    )


def test_flyout_menu_map_has_three_view_types() -> None:
    """FLYOUT_MENU_MAP must have keys for 'all', 'user', and 'hidden'."""
    # The map should reference all three view types
    map_section = _JS.split("FLYOUT_MENU_MAP")[1].split("};")[0]
    assert "'all'" in map_section or '"all"' in map_section, (
        "FLYOUT_MENU_MAP must include an 'all' key"
    )
    assert "'user'" in map_section or '"user"' in map_section, (
        "FLYOUT_MENU_MAP must include a 'user' key"
    )
    assert "'hidden'" in map_section or '"hidden"' in map_section, (
        "FLYOUT_MENU_MAP must include a 'hidden' key"
    )


def test_build_flyout_menu_items_function_exists() -> None:
    """app.js must define a _buildFlyoutMenuItems function."""
    assert "function _buildFlyoutMenuItems" in _JS, (
        "app.js must contain a _buildFlyoutMenuItems function"
    )


def test_build_flyout_uses_menu_map() -> None:
    """_buildFlyoutMenuItems must reference FLYOUT_MENU_MAP."""
    fn_body = _JS.split("function _buildFlyoutMenuItems")[1].split("\nfunction ")[0]
    assert "FLYOUT_MENU_MAP" in fn_body, (
        "_buildFlyoutMenuItems must reference FLYOUT_MENU_MAP (data-driven, not if/else)"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_flyout_menu_map_exists -v
```
Expected: FAIL — `FLYOUT_MENU_MAP` not in JS

**Step 3: Add the data map and builder to `muxplex/frontend/app.js`**

Insert after the flyout state variables (after `_flyoutRemoteId`):

```javascript
/**
 * Data map of menu item definitions keyed by view type.
 * Each entry is an array of item config objects with:
 *   { label, action, className?, separator? }
 * The 'user' view type gets the active view name injected at render time.
 */
var FLYOUT_MENU_MAP = {
  'all': [
    { label: 'Add to View\u2026', action: 'add-to-view', className: 'flyout-menu__item--has-submenu' },
    { label: 'Hide', action: 'hide' },
    { separator: true },
    { label: 'Kill Session', action: 'kill', className: 'flyout-menu__item--danger' },
  ],
  'user': [
    { label: 'Add to View\u2026', action: 'add-to-view', className: 'flyout-menu__item--has-submenu' },
    { label: 'Remove from {viewName}', action: 'remove-from-view' },
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
      var fullName = _activeView;
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
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "flyout_menu_map or build_flyout" -v
```
Expected: All 4 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add FLYOUT_MENU_MAP data map and _buildFlyoutMenuItems builder"
```

---

## Task 5: Flyout Click Handler — Dispatch Actions + "Add to View" Submenu

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/tests/test_frontend_js.py`

Implement `_handleFlyoutClick(e)` that dispatches to the correct action based on `data-action`. Implement `_openFlyoutSubmenu(triggerItem)` for the "Add to View" submenu with checkmark toggles.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_handle_flyout_click_function_exists() -> None:
    """app.js must define a _handleFlyoutClick function."""
    assert "function _handleFlyoutClick" in _JS, (
        "app.js must contain a _handleFlyoutClick function"
    )


def test_handle_flyout_click_dispatches_actions() -> None:
    """_handleFlyoutClick must check data-action for dispatching."""
    fn_body = _JS.split("function _handleFlyoutClick")[1].split("\nfunction ")[0]
    assert "data-action" in fn_body or "dataset.action" in fn_body, (
        "_handleFlyoutClick must read data-action from the clicked element"
    )


def test_open_flyout_submenu_function_exists() -> None:
    """app.js must define a _openFlyoutSubmenu function."""
    assert "function _openFlyoutSubmenu" in _JS, (
        "app.js must contain a _openFlyoutSubmenu function"
    )


def test_submenu_toggles_view_membership() -> None:
    """_openFlyoutSubmenu must PATCH /api/settings to toggle view membership."""
    fn_body = _JS.split("function _openFlyoutSubmenu")[1].split("\nfunction ")[0]
    assert "views" in fn_body and ("PATCH" in fn_body or "api(" in fn_body), (
        "_openFlyoutSubmenu must PATCH /api/settings to add/remove session from view"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_handle_flyout_click_function_exists -v
```
Expected: FAIL

**Step 3: Add the functions to `muxplex/frontend/app.js`**

Insert after `_buildFlyoutMenuItems()`:

```javascript
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
  if (views.length === 0) {
    showToast('No user views. Create one from the header dropdown.');
    return;
  }

  var sessionKey = _flyoutSessionKey;
  var html = '';
  for (var i = 0; i < views.length; i++) {
    var v = views[i];
    var isIn = (v.sessions || []).indexOf(sessionKey) !== -1;
    html += '<button class="flyout-submenu__item" role="menuitem" data-view-index="' + i + '">';
    html += '<span class="flyout-submenu__check">' + (isIn ? '\u2713' : '') + '</span>';
    html += escapeHtml(v.name);
    html += '</button>';
  }

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

  // Click handler for submenu items
  submenu.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-view-index]');
    if (!btn) return;
    var idx = parseInt(btn.dataset.viewIndex, 10);
    _toggleViewMembership(idx, sessionKey, unhideFirst);
  });
}

/**
 * Toggle a session's membership in a view.
 * @param {number} viewIndex - Index in the views array
 * @param {string} sessionKey - The session key to toggle
 * @param {boolean} unhideFirst - If true, also remove from hidden_sessions
 */
function _toggleViewMembership(viewIndex, sessionKey, unhideFirst) {
  var views = (_serverSettings && _serverSettings.views) || [];
  var updatedViews = JSON.parse(JSON.stringify(views));
  var view = updatedViews[viewIndex];
  if (!view) return;

  var sessions = view.sessions || [];
  var idx = sessions.indexOf(sessionKey);
  if (idx !== -1) {
    // Remove from view
    sessions.splice(idx, 1);
  } else {
    // Add to view
    sessions.push(sessionKey);
  }
  view.sessions = sessions;

  var patch = { views: updatedViews };

  // If unhiding, also update hidden_sessions
  if (unhideFirst) {
    var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
    var hiddenIdx = hidden.indexOf(sessionKey);
    if (hiddenIdx !== -1) {
      var updatedHidden = hidden.slice();
      updatedHidden.splice(hiddenIdx, 1);
      patch.hidden_sessions = updatedHidden;
    }
  }

  api('PATCH', '/api/settings', patch)
    .then(function() {
      if (_serverSettings) {
        _serverSettings.views = updatedViews;
        if (patch.hidden_sessions) _serverSettings.hidden_sessions = patch.hidden_sessions;
      }
      // Re-render the submenu to update checkmarks
      if (_flyoutSubmenuEl) {
        var checkItems = _flyoutSubmenuEl.querySelectorAll('[data-view-index]');
        for (var i = 0; i < checkItems.length; i++) {
          var vi = parseInt(checkItems[i].dataset.viewIndex, 10);
          var checkEl = checkItems[i].querySelector('.flyout-submenu__check');
          if (checkEl && updatedViews[vi]) {
            checkEl.textContent = (updatedViews[vi].sessions || []).indexOf(sessionKey) !== -1 ? '\u2713' : '';
          }
        }
      }
      // Refresh grid if needed (unhiding changes visible sessions)
      if (unhideFirst) {
        renderGrid(_currentSessions || []);
      }
    })
    .catch(function(err) {
      showToast('Couldn\u2019t save \u2014 try again');
      console.warn('[_toggleViewMembership] PATCH failed:', err);
    });
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "handle_flyout_click or open_flyout_submenu or submenu_toggles" -v
```
Expected: All 4 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add flyout click handler, Add to View submenu with toggle"
```

---

## Task 6: Hide/Unhide/Remove Actions

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/tests/test_frontend_js.py`

Implement `_doHideSession()`, `_doUnhideSession()`, and `_doRemoveFromView()`. These are the non-kill flyout actions.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_do_hide_session_function_exists() -> None:
    """app.js must define a _doHideSession function."""
    assert "function _doHideSession" in _JS, (
        "app.js must contain a _doHideSession function"
    )


def test_do_unhide_session_function_exists() -> None:
    """app.js must define a _doUnhideSession function."""
    assert "function _doUnhideSession" in _JS, (
        "app.js must contain a _doUnhideSession function"
    )


def test_do_remove_from_view_function_exists() -> None:
    """app.js must define a _doRemoveFromView function."""
    assert "function _doRemoveFromView" in _JS, (
        "app.js must contain a _doRemoveFromView function"
    )


def test_hide_session_removes_from_all_views() -> None:
    """_doHideSession must update both hidden_sessions AND views (remove from all views)."""
    fn_body = _JS.split("function _doHideSession")[1].split("\nfunction ")[0]
    assert "hidden_sessions" in fn_body, (
        "_doHideSession must add session to hidden_sessions"
    )
    assert "views" in fn_body, (
        "_doHideSession must remove session from all views (mutual exclusion)"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_do_hide_session_function_exists -v
```
Expected: FAIL

**Step 3: Add the functions to `muxplex/frontend/app.js`**

Insert after `_toggleViewMembership()`:

```javascript
/**
 * Hide a session: add to hidden_sessions and remove from ALL views.
 * Closes the flyout and re-renders the grid.
 */
function _doHideSession() {
  var sessionKey = _flyoutSessionKey;
  if (!sessionKey) return;

  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  var views = (_serverSettings && _serverSettings.views) || [];

  // Add to hidden_sessions
  var updatedHidden = hidden.slice();
  if (updatedHidden.indexOf(sessionKey) === -1) {
    updatedHidden.push(sessionKey);
  }

  // Remove from all views (mutual exclusion)
  var updatedViews = JSON.parse(JSON.stringify(views));
  for (var i = 0; i < updatedViews.length; i++) {
    var sessions = updatedViews[i].sessions || [];
    var idx = sessions.indexOf(sessionKey);
    if (idx !== -1) sessions.splice(idx, 1);
  }

  closeFlyoutMenu();

  api('PATCH', '/api/settings', { hidden_sessions: updatedHidden, views: updatedViews })
    .then(function() {
      if (_serverSettings) {
        _serverSettings.hidden_sessions = updatedHidden;
        _serverSettings.views = updatedViews;
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

  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  var idx = hidden.indexOf(sessionKey);
  if (idx === -1) { closeFlyoutMenu(); return; }

  var updatedHidden = hidden.slice();
  updatedHidden.splice(idx, 1);

  closeFlyoutMenu();

  api('PATCH', '/api/settings', { hidden_sessions: updatedHidden })
    .then(function() {
      if (_serverSettings) _serverSettings.hidden_sessions = updatedHidden;
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

  var views = (_serverSettings && _serverSettings.views) || [];
  var updatedViews = JSON.parse(JSON.stringify(views));

  // Find the active view and remove the session
  for (var i = 0; i < updatedViews.length; i++) {
    if (updatedViews[i].name === _activeView) {
      var sessions = updatedViews[i].sessions || [];
      var idx = sessions.indexOf(sessionKey);
      if (idx !== -1) sessions.splice(idx, 1);
      break;
    }
  }

  closeFlyoutMenu();

  api('PATCH', '/api/settings', { views: updatedViews })
    .then(function() {
      if (_serverSettings) _serverSettings.views = updatedViews;
      renderGrid(_currentSessions || []);
    })
    .catch(function(err) {
      showToast('Couldn\u2019t save \u2014 try again');
      console.warn('[_doRemoveFromView] PATCH failed:', err);
    });
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "do_hide or do_unhide or do_remove_from_view" -v
```
Expected: All 4 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add hide, unhide, and remove-from-view flyout actions"
```

---

## Task 7: Kill Session Inline Confirmation

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/tests/test_frontend_js.py`

Replace the old `confirm()` dialog with inline confirmation inside the flyout: clicking "Kill Session" replaces the item with "Kill? [Yes] [No]". On error shows "Failed" for 2 seconds. On success closes menu. Also update `killSession()` to no longer use `confirm()`.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_do_kill_session_inline_function_exists() -> None:
    """app.js must define a _doKillSessionInline function."""
    assert "function _doKillSessionInline" in _JS, (
        "app.js must contain a _doKillSessionInline function"
    )


def test_kill_session_no_confirm_dialog() -> None:
    """killSession must NOT use window.confirm() (replaced by inline confirmation)."""
    fn_body = _JS.split("function killSession")[1].split("\nfunction ")[0]
    assert "confirm(" not in fn_body, (
        "killSession must not use confirm() — replaced by inline flyout confirmation"
    )


def test_do_kill_inline_shows_confirmation_buttons() -> None:
    """_doKillSessionInline must render Yes/No confirmation buttons."""
    fn_body = _JS.split("function _doKillSessionInline")[1].split("\nfunction ")[0]
    assert "Yes" in fn_body and "No" in fn_body, (
        "_doKillSessionInline must show 'Kill? [Yes] [No]' inline"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_do_kill_session_inline_function_exists -v
```
Expected: FAIL

**Step 3: Apply the changes to `muxplex/frontend/app.js`**

Change 1 — Add `_doKillSessionInline()` after `_doRemoveFromView()`:

```javascript
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
      if (_viewingSession === name) {
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
```

Change 2 — Update `killSession()` (line 2160) to remove the `confirm()` call. It is now only used internally (sidebar kill still works through it):

```javascript
// Before (line 2160-2161):
function killSession(name, remoteId) {
  if (!confirm('Kill session "' + name + '"?')) return;

// After:
function killSession(name, remoteId) {
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "kill_session_inline or kill_session_no_confirm" -v
```
Expected: All 3 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add inline kill confirmation in flyout, remove old confirm() dialog"
```

---

## Task 8: Add Sessions Panel — HTML + CSS

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/style.css`
- Test: `muxplex/tests/test_frontend_html.py`
- Test: `muxplex/tests/test_frontend_css.py`

Add the Add Sessions panel overlay HTML and CSS. The panel shows all sessions NOT in the active user view, with immediate-commit checkboxes.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_html.py`:

```python
def test_add_sessions_panel_exists() -> None:
    """index.html must contain an add-sessions-panel element."""
    soup = _SOUP
    assert soup.find(id="add-sessions-panel"), (
        "index.html must contain an element with id='add-sessions-panel'"
    )


def test_add_sessions_panel_has_role_dialog() -> None:
    """add-sessions-panel must have role='dialog' and aria-modal='true'."""
    soup = _SOUP
    panel = soup.find(id="add-sessions-panel")
    assert panel, "Missing #add-sessions-panel"
    assert panel.get("role") == "dialog", (
        "add-sessions-panel must have role='dialog'"
    )
    assert panel.get("aria-modal") == "true", (
        "add-sessions-panel must have aria-modal='true'"
    )
```

Add to `muxplex/tests/test_frontend_css.py`:

```python
def test_add_sessions_panel_styled() -> None:
    """style.css must contain .add-sessions-panel styles."""
    css = read_css()
    assert ".add-sessions-panel" in css, "style.css must style .add-sessions-panel"


def test_add_sessions_item_styled() -> None:
    """style.css must contain .add-sessions-item styles."""
    css = read_css()
    assert ".add-sessions-item" in css, "style.css must style .add-sessions-item"
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py::test_add_sessions_panel_exists muxplex/tests/test_frontend_css.py::test_add_sessions_panel_styled -v
```
Expected: FAIL

**Step 3: Add HTML to `muxplex/frontend/index.html`**

Insert after the bottom-sheet div (after line 74, before the session-pill button):

```html
  <!-- —— Add Sessions panel ———————————————————————————————————————————— -->
  <div id="add-sessions-panel" class="add-sessions-panel hidden" role="dialog" aria-modal="true" aria-label="Add sessions to view">
    <div class="add-sessions-panel__backdrop" id="add-sessions-backdrop"></div>
    <div class="add-sessions-panel__content">
      <div class="add-sessions-panel__header">
        <h2 id="add-sessions-title" class="add-sessions-panel__title">Add Sessions</h2>
        <button id="add-sessions-close" class="add-sessions-panel__close" aria-label="Close">&times;</button>
      </div>
      <div id="add-sessions-list" class="add-sessions-panel__list"></div>
      <p id="add-sessions-empty" class="add-sessions-panel__empty" style="display:none">All sessions are already in this view.</p>
    </div>
  </div>
```

**Step 4: Add CSS to `muxplex/frontend/style.css`**

Append after the flyout sheet styles:

```css
/* —— Add Sessions Panel ——————————————————————————————————————————————— */

.add-sessions-panel {
  position: fixed;
  inset: 0;
  z-index: 250;
  display: flex;
  align-items: center;
  justify-content: center;
}

.add-sessions-panel__backdrop {
  position: absolute;
  inset: 0;
  background: var(--bg-overlay);
}

.add-sessions-panel__content {
  position: relative;
  width: 90%;
  max-width: 440px;
  max-height: 70vh;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.add-sessions-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-subtle);
}

.add-sessions-panel__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}

.add-sessions-panel__close {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
  border-radius: 4px;
}

.add-sessions-panel__close:hover {
  background: var(--bg-surface);
  color: var(--text);
}

.add-sessions-panel__list {
  overflow-y: auto;
  padding: 8px 0;
  flex: 1;
}

.add-sessions-panel__empty {
  padding: 24px 16px;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}

.add-sessions-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  cursor: pointer;
}

.add-sessions-item:hover {
  background: var(--bg-surface);
}

.add-sessions-item--hidden {
  opacity: 0.5;
}

.add-sessions-item__checkbox {
  flex-shrink: 0;
  accent-color: var(--accent);
}

.add-sessions-item__name {
  font-size: 13px;
  color: var(--text);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.add-sessions-item__device {
  font-size: 11px;
  color: var(--text-dim);
  white-space: nowrap;
}

.add-sessions-item__badge {
  font-size: 10px;
  color: var(--text-dim);
  background: var(--bg);
  border: 1px solid var(--border-subtle);
  border-radius: 3px;
  padding: 1px 5px;
}

.add-sessions-item__disclosure {
  width: 100%;
  padding: 2px 16px 6px 42px;
  font-size: 11px;
  color: var(--text-dim);
  font-style: italic;
  display: none;
}

/* Mobile: full-screen sheet for Add Sessions */
@media (max-width: 599px) {
  .add-sessions-panel__content {
    width: 100%;
    max-width: 100%;
    max-height: 100%;
    height: 100%;
    border-radius: 0;
  }
}
```

**Step 5: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py -k "add_sessions" muxplex/tests/test_frontend_css.py -k "add_sessions" -v
```
Expected: All 4 new tests PASS

**Step 6: Commit**

```bash
cd muxplex && git add muxplex/frontend/index.html muxplex/frontend/style.css muxplex/tests/test_frontend_html.py muxplex/tests/test_frontend_css.py && git commit -m "feat: add Add Sessions panel HTML and CSS"
```

---

## Task 9: Add Sessions Panel — JS Logic

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/tests/test_frontend_js.py`

Implement `openAddSessionsPanel()`, `closeAddSessionsPanel()`, and `renderAddSessionsList()`. The panel shows sessions not in the active view with immediate-commit checkboxes.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_open_add_sessions_panel_function_exists() -> None:
    """app.js must define an openAddSessionsPanel function."""
    assert "function openAddSessionsPanel" in _JS, (
        "app.js must contain an openAddSessionsPanel function"
    )


def test_close_add_sessions_panel_function_exists() -> None:
    """app.js must define a closeAddSessionsPanel function."""
    assert "function closeAddSessionsPanel" in _JS, (
        "app.js must contain a closeAddSessionsPanel function"
    )


def test_render_add_sessions_list_function_exists() -> None:
    """app.js must define a renderAddSessionsList function."""
    assert "function renderAddSessionsList" in _JS, (
        "app.js must contain a renderAddSessionsList function"
    )


def test_add_sessions_uses_immediate_commit() -> None:
    """renderAddSessionsList must PATCH immediately on checkbox change (no batch Done)."""
    fn_body = _JS.split("function renderAddSessionsList")[1].split("\nfunction ")[0]
    assert "PATCH" in fn_body or "api(" in fn_body, (
        "renderAddSessionsList must fire PATCH on each checkbox change (immediate commit)"
    )


def test_add_sessions_shows_device_name() -> None:
    """renderAddSessionsList must show device name next to each session."""
    fn_body = _JS.split("function renderAddSessionsList")[1].split("\nfunction ")[0]
    assert "deviceName" in fn_body or "device" in fn_body, (
        "renderAddSessionsList must show device name for disambiguation"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_open_add_sessions_panel_function_exists -v
```
Expected: FAIL

**Step 3: Add the functions to `muxplex/frontend/app.js`**

Insert after `_executeKill()`:

```javascript
// ── Add Sessions Panel ───────────────────────────────────────────────────────

/**
 * Open the Add Sessions panel for the active user view.
 * Only available for user views (not "All" or "Hidden").
 */
function openAddSessionsPanel() {
  if (_activeView === 'all' || _activeView === 'hidden') return;

  var panel = $('add-sessions-panel');
  if (!panel) return;

  // Update title
  var titleEl = $('add-sessions-title');
  if (titleEl) titleEl.textContent = 'Add Sessions to \u201c' + _activeView + '\u201d';

  renderAddSessionsList();
  panel.classList.remove('hidden');

  // Close on backdrop click
  var backdrop = $('add-sessions-backdrop');
  if (backdrop) {
    backdrop.onclick = closeAddSessionsPanel;
  }

  // Close button
  var closeBtn = $('add-sessions-close');
  if (closeBtn) {
    closeBtn.onclick = closeAddSessionsPanel;
  }
}

/**
 * Close the Add Sessions panel.
 */
function closeAddSessionsPanel() {
  var panel = $('add-sessions-panel');
  if (panel) panel.classList.add('hidden');
}

/**
 * Render the session list inside the Add Sessions panel.
 * Shows all sessions NOT currently in the active view.
 * Hidden sessions are shown dimmed with a "hidden" badge and disclosure text.
 * Grouped by device, alphabetical within each group.
 * Immediate-commit checkboxes — each change fires a PATCH immediately.
 */
function renderAddSessionsList() {
  var listEl = $('add-sessions-list');
  var emptyEl = $('add-sessions-empty');
  if (!listEl) return;

  var views = (_serverSettings && _serverSettings.views) || [];
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];

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

  // Get all real sessions (not status entries), excluding those already in the view
  var allSessions = (_currentSessions || []).filter(function(s) {
    return !s.status;
  });

  var notInView = allSessions.filter(function(s) {
    var key = s.sessionKey || s.name;
    return viewSessions.indexOf(key) === -1;
  });

  if (notInView.length === 0) {
    listEl.innerHTML = '';
    if (emptyEl) emptyEl.style.display = '';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  // Sort: group by deviceName, alphabetical within each group
  notInView.sort(function(a, b) {
    var da = (a.deviceName || '').toLowerCase();
    var db = (b.deviceName || '').toLowerCase();
    if (da !== db) return da < db ? -1 : 1;
    var na = (a.name || '').toLowerCase();
    var nb = (b.name || '').toLowerCase();
    return na < nb ? -1 : na > nb ? 1 : 0;
  });

  var html = '';
  for (var j = 0; j < notInView.length; j++) {
    var s = notInView[j];
    var key = s.sessionKey || s.name;
    var isHidden = hidden.indexOf(key) !== -1 || hidden.indexOf(s.name) !== -1;
    var escapedName = escapeHtml(s.name || '');
    var deviceName = escapeHtml(s.deviceName || '');

    html += '<label class="add-sessions-item' + (isHidden ? ' add-sessions-item--hidden' : '') + '">';
    html += '<input type="checkbox" class="add-sessions-item__checkbox" data-session-key="' + escapeHtml(key) + '"' + (isHidden ? ' data-is-hidden="1"' : '') + ' />';
    html += '<span class="add-sessions-item__name">' + escapedName + '</span>';
    if (deviceName) html += '<span class="add-sessions-item__device">' + deviceName + '</span>';
    if (isHidden) html += '<span class="add-sessions-item__badge">hidden</span>';
    html += '</label>';
    if (isHidden) {
      html += '<div class="add-sessions-item__disclosure">This will make it visible again.</div>';
    }
  }

  listEl.innerHTML = html;

  // Delegated change handler for immediate-commit checkboxes
  listEl.onchange = function(e) {
    var cb = e.target.closest('.add-sessions-item__checkbox');
    if (!cb) return;
    var sessionKey = cb.dataset.sessionKey;
    var isHiddenSession = cb.dataset.isHidden === '1';

    if (cb.checked) {
      // Add to view (and unhide if hidden)
      _addSessionToActiveView(sessionKey, isHiddenSession, cb);
    } else {
      // Should not normally happen (unchecking means removing), but handle gracefully
      cb.checked = false;
    }
  };

  // Show/hide disclosure on hover for hidden items
  listEl.onmouseover = function(e) {
    var item = e.target.closest('.add-sessions-item--hidden');
    if (item) {
      var disc = item.nextElementSibling;
      if (disc && disc.classList.contains('add-sessions-item__disclosure')) {
        disc.style.display = '';
      }
    }
  };
  listEl.onmouseout = function(e) {
    var item = e.target.closest('.add-sessions-item--hidden');
    if (item) {
      var disc = item.nextElementSibling;
      if (disc && disc.classList.contains('add-sessions-item__disclosure')) {
        disc.style.display = 'none';
      }
    }
  };
}

/**
 * Add a session to the active user view via PATCH.
 * If the session is hidden, also unhide it.
 * On error: show toast, revert checkbox.
 * @param {string} sessionKey
 * @param {boolean} unhideFirst
 * @param {HTMLInputElement} checkbox
 */
function _addSessionToActiveView(sessionKey, unhideFirst, checkbox) {
  var views = (_serverSettings && _serverSettings.views) || [];
  var updatedViews = JSON.parse(JSON.stringify(views));

  // Find active view and add session
  for (var i = 0; i < updatedViews.length; i++) {
    if (updatedViews[i].name === _activeView) {
      var sessions = updatedViews[i].sessions || [];
      if (sessions.indexOf(sessionKey) === -1) {
        sessions.push(sessionKey);
      }
      updatedViews[i].sessions = sessions;
      break;
    }
  }

  var patch = { views: updatedViews };

  if (unhideFirst) {
    var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
    var hiddenIdx = hidden.indexOf(sessionKey);
    if (hiddenIdx !== -1) {
      var updatedHidden = hidden.slice();
      updatedHidden.splice(hiddenIdx, 1);
      patch.hidden_sessions = updatedHidden;
    }
  }

  api('PATCH', '/api/settings', patch)
    .then(function() {
      if (_serverSettings) {
        _serverSettings.views = updatedViews;
        if (patch.hidden_sessions) _serverSettings.hidden_sessions = patch.hidden_sessions;
      }
      // Re-render the list (session is now in the view, so it disappears from the list)
      renderAddSessionsList();
      // Refresh the grid behind the panel
      renderGrid(_currentSessions || []);
    })
    .catch(function(err) {
      showToast('Couldn\u2019t save \u2014 try again');
      if (checkbox) checkbox.checked = false;
      console.warn('[_addSessionToActiveView] PATCH failed:', err);
    });
}
```

Change 2 — Export the new functions in `window.MuxplexApp`:
```javascript
    // Add Sessions panel
    openAddSessionsPanel,
    closeAddSessionsPanel,
    renderAddSessionsList,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "add_sessions" -v
```
Expected: All 5 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add Add Sessions panel JS logic with immediate-commit checkboxes"
```

---

## Task 10: Mobile Variants — Bottom Action Sheet + Full-Screen Panels

**Files:**
- Modify: `muxplex/frontend/app.js`
- Test: `muxplex/tests/test_frontend_js.py`

On mobile (`isMobile()`), the `⋮` tap opens a bottom action sheet instead of a floating menu. "Add to View" on mobile opens a full-height picker sheet. The Add Sessions panel already handles mobile via the CSS `@media` rule from Task 8.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_open_flyout_sheet_function_exists() -> None:
    """app.js must define a _openFlyoutSheet function for mobile."""
    assert "function _openFlyoutSheet" in _JS, (
        "app.js must contain a _openFlyoutSheet function for mobile bottom sheet"
    )


def test_open_flyout_menu_checks_mobile() -> None:
    """openFlyoutMenu must check isMobile() to decide between flyout and sheet."""
    fn_body = _JS.split("function openFlyoutMenu")[1].split("\nfunction ")[0]
    assert "isMobile" in fn_body, (
        "openFlyoutMenu must check isMobile() to branch between flyout and sheet"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_open_flyout_sheet_function_exists -v
```
Expected: FAIL

**Step 3: Add the mobile sheet function to `muxplex/frontend/app.js`**

Insert after `closeFlyoutMenu()`:

```javascript
/**
 * Open a bottom action sheet for the flyout menu (mobile).
 * Same actions as the desktop flyout, but renders as a full-width bottom sheet.
 */
function _openFlyoutSheet() {
  var viewType = _activeView;
  if (viewType !== 'all' && viewType !== 'hidden') viewType = 'user';

  var items = FLYOUT_MENU_MAP[viewType] || FLYOUT_MENU_MAP['all'];

  var html = '<div class="flyout-sheet__backdrop"></div>';
  html += '<div class="flyout-sheet__panel">';
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

    html += '<button class="' + cls + '" data-action="' + item.action + '">';
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
        // On mobile, close sheet and open Add Sessions panel
        closeFlyoutMenu();
        openAddSessionsPanel();
      } else if (action === 'kill') {
        // Simple confirm on mobile (inline doesn't work well in sheets)
        closeFlyoutMenu();
        killSession(_flyoutSessionName, _flyoutRemoteId);
      } else {
        // Dispatch directly
        _handleFlyoutClick(e);
      }
    });
  }
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "flyout_sheet or open_flyout_menu_checks_mobile" -v
```
Expected: Both PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add mobile bottom action sheet for flyout menu"
```

---

## Task 11: Wire Up "Add Sessions" Entry Point in Grid

**Files:**
- Modify: `muxplex/frontend/app.js` (inside `renderGrid()`)
- Modify: `muxplex/frontend/style.css`
- Test: `muxplex/tests/test_frontend_js.py`

When in a user-created view, show an "Add Sessions" affordance tile at the end of the grid that opens the Add Sessions panel. This gives users a discoverable entry point beyond the flyout submenu.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_render_grid_has_add_sessions_affordance() -> None:
    """renderGrid must include an 'Add Sessions' affordance when in a user view."""
    fn_body = _JS.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "add-sessions" in fn_body.lower() or "openAddSessionsPanel" in fn_body, (
        "renderGrid must render an 'Add Sessions' affordance for user views"
    )
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_render_grid_has_add_sessions_affordance -v
```
Expected: FAIL

**Step 3: Apply the changes to `muxplex/frontend/app.js`**

In `renderGrid()`, after the grid HTML is assembled (right before `grid.innerHTML = html;`), add the "Add Sessions" affordance tile if in a user view:

```javascript
    // Add Sessions affordance tile — shown in user views only
    if (_activeView !== 'all' && _activeView !== 'hidden') {
      var viewsArr = (_serverSettings && _serverSettings.views) || [];
      var isUserView = false;
      for (var vi = 0; vi < viewsArr.length; vi++) {
        if (viewsArr[vi].name === _activeView) { isUserView = true; break; }
      }
      if (isUserView) {
        html += '<button class="add-sessions-tile" onclick="window.MuxplexApp.openAddSessionsPanel()" aria-label="Add sessions to this view">';
        html += '<span class="add-sessions-tile__icon">+</span>';
        html += '<span class="add-sessions-tile__label">Add Sessions</span>';
        html += '</button>';
      }
    }
```

Add CSS to `muxplex/frontend/style.css` (after the Add Sessions panel styles):

```css
/* —— Add Sessions affordance tile ————————————————————————————————————— */

.add-sessions-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 120px;
  background: transparent;
  border: 2px dashed var(--border-subtle);
  border-radius: 8px;
  color: var(--text-dim);
  font-size: 13px;
  cursor: pointer;
  transition: border-color var(--t-fast), color var(--t-fast);
}

.add-sessions-tile:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.add-sessions-tile__icon {
  font-size: 24px;
  line-height: 1;
}

.add-sessions-tile__label {
  font-size: 12px;
}
```

**Step 4: Run test to verify it passes**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_render_grid_has_add_sessions_affordance -v
```
Expected: PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/style.css muxplex/tests/test_frontend_js.py && git commit -m "feat: add 'Add Sessions' affordance tile in user views grid"
```

---

## Task 12: Session Death Detection — Close Flyout if Session Dies

**Files:**
- Modify: `muxplex/frontend/app.js` (the poll/render cycle)
- Test: `muxplex/tests/test_frontend_js.py`

If the session being targeted by the flyout dies (disappears from `_currentSessions` during a poll), close the flyout. This handles the edge case where a user has the kill confirmation showing and the session dies externally.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_render_grid_closes_stale_flyout() -> None:
    """renderGrid must close the flyout if the targeted session no longer exists."""
    fn_body = _JS.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "_flyoutSessionKey" in fn_body or "closeFlyoutMenu" in fn_body, (
        "renderGrid must check if the flyout's target session still exists and close if not"
    )
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_render_grid_closes_stale_flyout -v
```
Expected: FAIL

**Step 3: Apply the change to `muxplex/frontend/app.js`**

In `renderGrid()`, at the beginning of the function (after the `var grid = $('session-grid');` check), add:

```javascript
  // Close flyout if the targeted session no longer exists
  if (_flyoutSessionKey) {
    var flyoutStillExists = (sessions || []).some(function(s) {
      return (s.sessionKey || s.name) === _flyoutSessionKey;
    });
    if (!flyoutStillExists) {
      closeFlyoutMenu();
    }
  }
```

**Step 4: Run test to verify it passes**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_render_grid_closes_stale_flyout -v
```
Expected: PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: close flyout when targeted session disappears"
```

---

## Task 13: Final Integration — Run Full Test Suite

**Files:** None (verification only)

**Step 1: Run the complete test suite**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v --timeout=120
```
Expected: All tests PASS

**Step 2: Run quality checks**

```bash
cd muxplex && python -m ruff check muxplex/
cd muxplex && python -m ruff format --check muxplex/
```
Expected: No errors. Fix any formatting or lint issues.

**Step 3: Verify the Phase 3 feature chain end-to-end**

Check that the full chain works by listing what Phase 3 established:

1. Flyout CSS — `.flyout-menu`, `.flyout-submenu`, `.flyout-sheet`, `.tile-options-btn` all styled ✓
2. `⋮` button on session tiles — replaces old `.tile-delete` button in `buildTileHTML()` ✓
3. Flyout base JS — `openFlyoutMenu()` / `closeFlyoutMenu()` with `position:fixed` + `getBoundingClientRect` positioning ✓
4. Context-dependent menu items — `FLYOUT_MENU_MAP` data map with `all`/`user`/`hidden` keys, no if/else ✓
5. "Add to View" submenu — `_openFlyoutSubmenu()` with checkmark toggle, immediate PATCH, flyout stays open ✓
6. Hide/Unhide/Remove actions — `_doHideSession()` (removes from all views + adds to hidden), `_doUnhideSession()`, `_doRemoveFromView()` ✓
7. Kill session inline confirmation — `_doKillSessionInline()` replaces item with "Kill? [Yes] [No]", "Failed" for 2s on error ✓
8. Add Sessions panel — HTML overlay with immediate-commit checkboxes, dimmed hidden sessions with badge, device names, alphabetical grouped by device, empty state message ✓
9. Add Sessions panel JS — `openAddSessionsPanel()` / `renderAddSessionsList()` with PATCH on each checkbox ✓
10. Mobile variants — `_openFlyoutSheet()` bottom action sheet, Add Sessions panel is full-screen via CSS media query ✓
11. "Add Sessions" affordance tile — dashed-border tile in grid for user views ✓
12. Session death detection — flyout closes if target session disappears during poll ✓

**Step 4: Verify all three phases work together**

Run the full test suite one more time to confirm no cross-phase regressions:

```bash
cd muxplex && python -m pytest muxplex/tests/ -v --timeout=180
```

**Step 5: Commit any remaining fixes**

```bash
cd muxplex && git add -A && git status
```
If there are uncommitted changes, commit them:
```bash
cd muxplex && git commit -m "chore: phase 3 integration fixes"
```

---

## Summary: Phase 3 delivers 12 implementation tasks + 1 verification task

| Task | What | Files |
|---|---|---|
| 1 | Flyout CSS (menu, submenu, mobile sheet, trigger) | `style.css`, `tests/test_frontend_css.py` |
| 2 | ⋮ button on tiles, remove old `.tile-delete` | `app.js`, `tests/test_frontend_js.py` |
| 3 | Flyout base JS (open, position, close, delegation) | `app.js`, `tests/test_frontend_js.py` |
| 4 | `FLYOUT_MENU_MAP` data map + `_buildFlyoutMenuItems()` | `app.js`, `tests/test_frontend_js.py` |
| 5 | Click handler + "Add to View" submenu with toggle | `app.js`, `tests/test_frontend_js.py` |
| 6 | Hide, Unhide, Remove from View actions | `app.js`, `tests/test_frontend_js.py` |
| 7 | Kill session inline confirmation (replaces `confirm()`) | `app.js`, `tests/test_frontend_js.py` |
| 8 | Add Sessions panel HTML + CSS | `index.html`, `style.css`, `tests/test_frontend_html.py`, `tests/test_frontend_css.py` |
| 9 | Add Sessions panel JS logic | `app.js`, `tests/test_frontend_js.py` |
| 10 | Mobile bottom action sheet for flyout | `app.js`, `tests/test_frontend_js.py` |
| 11 | "Add Sessions" affordance tile in grid | `app.js`, `style.css`, `tests/test_frontend_js.py` |
| 12 | Session death detection (close stale flyout) | `app.js`, `tests/test_frontend_js.py` |
| 13 | Final integration — full test suite | verification only |

**After Phase 3, the Views feature is complete.** Users can:
- Switch views via the header dropdown (Phase 2)
- Right-click `⋮` on any tile to add/remove from views, hide/unhide, or kill (Phase 3)
- Use the Add Sessions panel to bulk-add sessions to a view (Phase 3)
- All actions use immediate-commit PATCH with error recovery (Phase 3)
- Mobile gets bottom action sheets and full-screen panels (Phase 3)

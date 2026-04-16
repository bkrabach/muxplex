# Views Feature — Phase 2: Views Backend Logic + Header Dropdown UI

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Wire up the views filtering/switching logic and build the primary view-switching dropdown UI, so users can create, switch between, and manage views from the header.

**Architecture:** The frontend's `getVisibleSessions()` is rewritten to honor the `active_view` state field — filtering sessions to the current view's membership list (user view), everything not hidden ("All"), or everything hidden ("Hidden"). Views are created/renamed/reordered/deleted via `PATCH /api/settings` (views lives in settings.json). The `active_view` is persisted per-device via `PATCH /api/state`. A header dropdown provides the view switcher with keyboard shortcuts, inline "New View" creation, and a link to a new "Views" tab in the settings dialog for rename/reorder/delete management.

**Tech Stack:** Python 3.12+ / FastAPI / pytest + pytest-asyncio / vanilla JS / CSS

**Design reference:** `docs/plans/2026-04-15-views-design.md`
**Phase 1 reference:** `docs/plans/2026-04-15-views-phase1-implementation.md`

**Assumes Phase 1 is complete:** `identity.py` exists, session keys use `device_id:name`, federation endpoints use device_id lookup, `views` is in `DEFAULT_SETTINGS` (empty array) and `SYNCABLE_KEYS`, `active_view` is in state schema (default `"all"`), `views.py` has `enforce_mutual_exclusion()` and `validate_view_name()`, `gridViewMode` no longer accepts `"filtered"` in settings.

---

## Task 1: Add `active_view` to `StatePatch` Model

**Files:**
- Modify: `muxplex/main.py` (lines ~425–502)
- Modify: `muxplex/tests/test_api.py`

The backend `PATCH /api/state` endpoint does not yet accept `active_view`. The `StatePatch` Pydantic model needs a new optional field, and the `patch_state` handler needs to write it through.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_api.py`, after the existing state tests:

```python
# ---------------------------------------------------------------------------
# active_view in PATCH /api/state
# ---------------------------------------------------------------------------


def test_patch_state_sets_active_view(client, tmp_path, monkeypatch):
    """PATCH /api/state with active_view persists the value."""
    import muxplex.state as state_mod

    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state" / "state.json")

    response = client.patch("/api/state", json={"active_view": "Work Project"})
    assert response.status_code == 200
    data = response.json()
    assert data["active_view"] == "Work Project"

    # Verify persistence
    response2 = client.get("/api/state")
    assert response2.json()["active_view"] == "Work Project"


def test_patch_state_active_view_defaults_to_all(client, tmp_path, monkeypatch):
    """GET /api/state returns active_view='all' by default."""
    import muxplex.state as state_mod

    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state" / "state.json")

    response = client.get("/api/state")
    assert response.status_code == 200
    assert response.json()["active_view"] == "all"
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_patch_state_sets_active_view -v
```
Expected: FAIL — `active_view` not accepted by `StatePatch`

**Step 3: Apply the changes to `muxplex/main.py`**

Change 1 — Add `active_view` to the `StatePatch` model (line ~425):
```python
class StatePatch(BaseModel):
    session_order: list[str] | None = None
    active_session: str | None = None
    active_remote_id: str | None = None
    active_view: str | None = None
```

Change 2 — Add `active_view` handling to `patch_state` (line ~492, after the `active_remote_id` block):
```python
        if "active_view" in changed:
            state["active_view"] = patch.active_view
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_patch_state_sets_active_view muxplex/tests/test_api.py::test_patch_state_active_view_defaults_to_all -v
```
Expected: Both PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add active_view to StatePatch model for PATCH /api/state"
```

---

## Task 2: Rewrite `getVisibleSessions()` to Honor Active View

**Files:**
- Modify: `muxplex/frontend/app.js` (lines ~537–547)
- Modify: `muxplex/tests/test_frontend_js.py`

The current `getVisibleSessions()` only filters out hidden sessions. It must now also filter by the active view: "All" shows everything not hidden, "Hidden" shows only hidden sessions, and a user view shows only sessions whose `sessionKey` is in that view's sessions list (plus status tiles).

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
# ---------------------------------------------------------------------------
# getVisibleSessions respects active view
# ---------------------------------------------------------------------------


def test_get_visible_sessions_all_view_excludes_hidden() -> None:
    """In 'all' view, getVisibleSessions excludes hidden sessions."""
    assert "function getVisibleSessions" in _JS
    # Check that the function references _activeView or active_view
    # to determine filtering behavior
    assert "_activeView" in _JS, (
        "getVisibleSessions must reference _activeView state variable"
    )


def test_active_view_state_variable_exists() -> None:
    """app.js must declare an _activeView state variable."""
    assert re.search(r"let\s+_activeView\s*=\s*'all'", _JS) or \
           re.search(r"var\s+_activeView\s*=\s*'all'", _JS), (
        "_activeView state variable must be declared with default 'all'"
    )


def test_get_visible_sessions_user_view_filters_by_session_key() -> None:
    """getVisibleSessions must check view sessions list using sessionKey."""
    assert "sessionKey" in _JS.split("function getVisibleSessions")[1].split("function ")[0], (
        "getVisibleSessions must use sessionKey for view membership check"
    )


def test_get_visible_sessions_hidden_view_shows_hidden() -> None:
    """In 'hidden' view, getVisibleSessions shows only hidden sessions."""
    # The hidden view logic must invert the filter
    fn_body = _JS.split("function getVisibleSessions")[1].split("function ")[0]
    assert "'hidden'" in fn_body or '"hidden"' in fn_body, (
        "getVisibleSessions must handle the 'hidden' view case"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_active_view_state_variable_exists -v
```
Expected: FAIL — `_activeView` does not exist yet

**Step 3: Apply the changes to `muxplex/frontend/app.js`**

Change 1 — Add the `_activeView` state variable in the "App state" section (after `_activeFilterDevice` on line 139):
```javascript
let _activeView = 'all';
```

Change 2 — Rewrite `getVisibleSessions()` (replace lines 537–547):
```javascript
/**
 * Returns sessions filtered by the active view.
 * - "all": everything not in hidden_sessions (default)
 * - "hidden": only sessions in hidden_sessions
 * - user view name: only sessions whose sessionKey is in that view's sessions list
 * Status entries (unreachable, auth_failed, empty) are always excluded here —
 * they are rendered separately as status tiles by renderGrid().
 * @param {object[]} sessions
 * @returns {object[]}
 */
function getVisibleSessions(sessions) {
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  var all = (sessions || []).filter(function(s) { return !s.status; });

  if (_activeView === 'hidden') {
    // Show only hidden sessions
    return all.filter(function(s) {
      var key = s.sessionKey || s.name;
      return hidden.includes(key) || hidden.includes(s.name);
    });
  }

  if (_activeView === 'all') {
    // Show everything NOT hidden
    return all.filter(function(s) {
      var key = s.sessionKey || s.name;
      return !(hidden.length > 0 && (hidden.includes(key) || hidden.includes(s.name)));
    });
  }

  // User view: find the view by name, filter to its session list
  var views = (_serverSettings && _serverSettings.views) || [];
  var activeViewObj = null;
  for (var i = 0; i < views.length; i++) {
    if (views[i].name === _activeView) {
      activeViewObj = views[i];
      break;
    }
  }

  if (!activeViewObj) {
    // View no longer exists — fall back to "all"
    _activeView = 'all';
    return all.filter(function(s) {
      var key = s.sessionKey || s.name;
      return !(hidden.length > 0 && (hidden.includes(key) || hidden.includes(s.name)));
    });
  }

  var viewSessions = activeViewObj.sessions || [];
  return all.filter(function(s) {
    var key = s.sessionKey || s.name;
    return viewSessions.includes(key);
  });
}
```

Change 3 — Add test helpers at the bottom of app.js, in the test-only section (before the `window.MuxplexApp` export):
```javascript
/** Test-only: get _activeView. */
function _getActiveView() {
  return _activeView;
}

/** Test-only: set _activeView directly. */
function _setActiveView(view) {
  _activeView = view;
}
```

Change 4 — Export the new test helpers in `window.MuxplexApp` (add after `_setActiveFilterDevice`):
```javascript
    _getActiveView,
    _setActiveView,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "active_view" -v
```
Expected: All new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: rewrite getVisibleSessions to filter by active view"
```

---

## Task 3: Auto-Add Session to Active View on Creation

**Files:**
- Modify: `muxplex/frontend/app.js` (the `createNewSession()` function, line ~2089)
- Modify: `muxplex/tests/test_frontend_js.py`

When a new session is created via the `+` button while a user view is active, auto-add the new session's key to that view's sessions list. Only for sessions created through the UI, not those appearing from federation polls.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_create_new_session_references_active_view() -> None:
    """createNewSession must check _activeView to auto-add to the active user view."""
    fn_body = _JS.split("async function createNewSession")[1].split("\nasync function ")[0]
    assert "_activeView" in fn_body, (
        "createNewSession must reference _activeView for auto-add behavior"
    )
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_create_new_session_references_active_view -v
```
Expected: FAIL — `_activeView` not referenced in `createNewSession`

**Step 3: Apply the change to `muxplex/frontend/app.js`**

In `createNewSession()` (line ~2089), after the successful `api('POST', endpoint, { name })` call and before polling, add the auto-add logic. Insert after `const sessionName = data.name || name;` (line ~2095):

```javascript
    // Auto-add to active user view (not "all" or "hidden")
    if (_activeView !== 'all' && _activeView !== 'hidden') {
      var views = (_serverSettings && _serverSettings.views) || [];
      var viewIdx = -1;
      for (var vi = 0; vi < views.length; vi++) {
        if (views[vi].name === _activeView) { viewIdx = vi; break; }
      }
      if (viewIdx >= 0) {
        // Build the sessionKey for the new session
        var newSessionKey = remoteId ? (remoteId + ':' + sessionName) : sessionName;
        // If the server already gave us a deviceId-based key, prefer that
        if (!remoteId && _serverSettings && _serverSettings.device_id) {
          newSessionKey = _serverSettings.device_id + ':' + sessionName;
        }
        var updatedViews = JSON.parse(JSON.stringify(views));
        if (!updatedViews[viewIdx].sessions.includes(newSessionKey)) {
          updatedViews[viewIdx].sessions.push(newSessionKey);
          api('PATCH', '/api/settings', { views: updatedViews }).catch(function(err) {
            console.warn('[createNewSession] auto-add to view failed:', err);
          });
          if (_serverSettings) _serverSettings.views = updatedViews;
        }
      }
    }
```

**Step 4: Run test to verify it passes**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_create_new_session_references_active_view -v
```
Expected: PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: auto-add new sessions to active user view on creation"
```

---

## Task 4: Header Dropdown — HTML Structure

**Files:**
- Modify: `muxplex/frontend/index.html` (lines 20–28, the `<header class="app-header">` section)
- Modify: `muxplex/tests/test_frontend_html.py`

Add the view dropdown trigger element and the dropdown container to the header, between the wordmark and the header-actions div.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_html.py`:

```python
def test_view_dropdown_trigger_exists() -> None:
    """The header must contain a view-dropdown trigger button."""
    assert 'id="view-dropdown-trigger"' in _HTML, (
        "index.html must contain a #view-dropdown-trigger element"
    )


def test_view_dropdown_container_exists() -> None:
    """The header must contain a view-dropdown-menu container."""
    assert 'id="view-dropdown-menu"' in _HTML, (
        "index.html must contain a #view-dropdown-menu element"
    )


def test_view_dropdown_trigger_has_aria() -> None:
    """The view dropdown trigger must have aria-haspopup and aria-expanded."""
    assert 'aria-haspopup="true"' in _HTML.split('view-dropdown-trigger')[1][:300], (
        "view-dropdown-trigger must have aria-haspopup='true'"
    )
    assert 'aria-expanded="false"' in _HTML.split('view-dropdown-trigger')[1][:300], (
        "view-dropdown-trigger must have aria-expanded='false'"
    )


def test_view_dropdown_menu_has_role_menu() -> None:
    """The dropdown menu must have role='menu'."""
    assert 'role="menu"' in _HTML.split('view-dropdown-menu')[1][:300], (
        "view-dropdown-menu must have role='menu'"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py::test_view_dropdown_trigger_exists -v
```
Expected: FAIL — `view-dropdown-trigger` not in HTML

**Step 3: Apply the changes to `muxplex/frontend/index.html`**

Replace the header section (lines 20–28) with:
```html
    <header class="app-header">
      <h1 class="app-wordmark"><img src="/wordmark-on-dark.svg" alt="muxplex" height="24" /></h1>
      <div class="view-dropdown" id="view-dropdown">
        <button id="view-dropdown-trigger" class="view-dropdown__trigger" aria-haspopup="true" aria-expanded="false" aria-controls="view-dropdown-menu">
          <span id="view-dropdown-label">All Sessions</span>
          <span class="view-dropdown__caret" aria-hidden="true">&#9662;</span>
        </button>
        <div id="view-dropdown-menu" class="view-dropdown__menu hidden" role="menu" aria-label="Switch view"></div>
      </div>
      <div class="header-actions">
        <button id="new-session-btn" class="header-btn" aria-label="New session">+</button>
        <button id="view-mode-btn" class="header-btn" aria-label="Toggle view mode" title="View: auto">&#9638;</button>
        <button id="settings-btn" class="header-btn" aria-label="Settings">&#9881;</button>
        <span id="connection-status"></span>
      </div>
    </header>
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py -k "view_dropdown" -v
```
Expected: All 4 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/index.html muxplex/tests/test_frontend_html.py && git commit -m "feat: add view dropdown HTML structure to header"
```

---

## Task 5: Header Dropdown — CSS Styles

**Files:**
- Modify: `muxplex/frontend/style.css`
- Modify: `muxplex/tests/test_frontend_css.py`

Style the view dropdown trigger, menu, and items. The dropdown is absolutely positioned below the trigger.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_css.py`:

```python
def test_view_dropdown_trigger_styled() -> None:
    """style.css must contain .view-dropdown__trigger styles."""
    assert ".view-dropdown__trigger" in _CSS, (
        "style.css must style .view-dropdown__trigger"
    )


def test_view_dropdown_menu_styled() -> None:
    """style.css must contain .view-dropdown__menu styles."""
    assert ".view-dropdown__menu" in _CSS, (
        "style.css must style .view-dropdown__menu"
    )


def test_view_dropdown_item_styled() -> None:
    """style.css must contain .view-dropdown__item styles."""
    assert ".view-dropdown__item" in _CSS, (
        "style.css must style .view-dropdown__item"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_css.py::test_view_dropdown_trigger_styled -v
```
Expected: FAIL — `.view-dropdown__trigger` not in CSS

**Step 3: Add styles to `muxplex/frontend/style.css`**

Add before the `/* Filter bar */` section (before line ~1645). Insert after the header styles:

```css
/* ── View Dropdown ─────────────────────────────────────────────────── */

.view-dropdown {
  position: relative;
  display: flex;
  align-items: center;
  margin-left: 12px;
}

.view-dropdown__trigger {
  display: flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 4px 8px;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color 0.15s, background 0.15s;
}

.view-dropdown__trigger:hover,
.view-dropdown__trigger[aria-expanded="true"] {
  border-color: var(--border);
  background: var(--bg-surface);
}

.view-dropdown__caret {
  font-size: 10px;
  color: var(--text-muted);
}

.view-dropdown__menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  min-width: 200px;
  max-width: 280px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px 0;
  z-index: 100;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}

.view-dropdown__item {
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
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.view-dropdown__item:hover,
.view-dropdown__item:focus-visible {
  background: var(--bg-surface);
}

.view-dropdown__item--active {
  color: var(--accent);
}

.view-dropdown__item--active::before {
  content: "\2713";
  width: 14px;
  flex-shrink: 0;
}

.view-dropdown__item:not(.view-dropdown__item--active)::before {
  content: "";
  width: 14px;
  flex-shrink: 0;
}

.view-dropdown__shortcut {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-dim);
  font-family: monospace;
}

.view-dropdown__separator {
  height: 1px;
  margin: 4px 0;
  background: var(--border-subtle);
}

.view-dropdown__action {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 12px;
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
}

.view-dropdown__action:hover {
  background: var(--bg-surface);
  color: var(--text);
}

.view-dropdown__new-input {
  width: calc(100% - 24px);
  margin: 4px 12px;
  padding: 4px 8px;
  background: var(--bg);
  border: 1px solid var(--accent);
  border-radius: 4px;
  color: var(--text);
  font-size: 13px;
  outline: none;
}

.view-dropdown__count {
  font-size: 11px;
  color: var(--text-dim);
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_css.py -k "view_dropdown" -v
```
Expected: All 3 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/style.css muxplex/tests/test_frontend_css.py && git commit -m "feat: add view dropdown CSS styles"
```

---

## Task 6: Header Dropdown — JS Render + Open/Close + View Switching

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/tests/test_frontend_js.py`

Implement `renderViewDropdown()` which populates the dropdown menu with: All Sessions, user views, Hidden (count), a separator, + New View, and Manage Views. Implement open/close toggle, click-outside dismiss, and view switching that updates `active_view` via `PATCH /api/state` and re-renders the grid.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_render_view_dropdown_function_exists() -> None:
    """app.js must define a renderViewDropdown function."""
    assert "function renderViewDropdown" in _JS, (
        "app.js must contain a renderViewDropdown function"
    )


def test_render_view_dropdown_exported() -> None:
    """renderViewDropdown must be exported on window.MuxplexApp."""
    assert "renderViewDropdown" in _JS.split("window.MuxplexApp")[1], (
        "renderViewDropdown must be exported on window.MuxplexApp"
    )


def test_toggle_view_dropdown_function_exists() -> None:
    """app.js must define a toggleViewDropdown function."""
    assert "function toggleViewDropdown" in _JS, (
        "app.js must contain a toggleViewDropdown function"
    )


def test_switch_view_function_exists() -> None:
    """app.js must define a switchView function."""
    assert "function switchView" in _JS, (
        "app.js must contain a switchView function"
    )


def test_switch_view_patches_state() -> None:
    """switchView must PATCH /api/state with the new active_view."""
    fn_body = _JS.split("function switchView")[1].split("\nfunction ")[0]
    assert "active_view" in fn_body, (
        "switchView must include active_view in the state PATCH"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_render_view_dropdown_function_exists -v
```
Expected: FAIL — function does not exist

**Step 3: Add the functions to `muxplex/frontend/app.js`**

Insert after the `renderFilterBar()` function (after line ~752), before `renderGrid()`:

```javascript
// ── View Dropdown ─────────────────────────────────────────────────────────

/**
 * Render the view dropdown menu contents.
 * Populates #view-dropdown-menu with: All Sessions, user views (in array order),
 * Hidden (count), separator, + New View, Manage Views.
 */
function renderViewDropdown() {
  var menu = $('view-dropdown-menu');
  if (!menu) return;

  var views = (_serverSettings && _serverSettings.views) || [];
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  var hiddenCount = hidden.length;

  var html = '';

  // "All Sessions" — always first, shortcut: 1
  var allActive = _activeView === 'all' ? ' view-dropdown__item--active' : '';
  html += '<button class="view-dropdown__item' + allActive + '" role="menuitem" data-view="all">';
  html += 'All Sessions';
  html += '<span class="view-dropdown__shortcut">1</span>';
  html += '</button>';

  // Separator before user views (if any)
  if (views.length > 0) {
    html += '<div class="view-dropdown__separator" role="separator"></div>';
  }

  // User views — in array order, shortcuts 2–8
  for (var i = 0; i < views.length; i++) {
    var vActive = _activeView === views[i].name ? ' view-dropdown__item--active' : '';
    html += '<button class="view-dropdown__item' + vActive + '" role="menuitem" data-view="' + escapeHtml(views[i].name) + '">';
    html += escapeHtml(views[i].name);
    if (i < 7) {
      html += '<span class="view-dropdown__shortcut">' + (i + 2) + '</span>';
    }
    html += '</button>';
  }

  // Separator before Hidden
  html += '<div class="view-dropdown__separator" role="separator"></div>';

  // "Hidden (N)" — always last system view, shortcut: 9
  var hiddenActive = _activeView === 'hidden' ? ' view-dropdown__item--active' : '';
  html += '<button class="view-dropdown__item' + hiddenActive + '" role="menuitem" data-view="hidden">';
  html += 'Hidden';
  if (hiddenCount > 0) html += ' <span class="view-dropdown__count">(' + hiddenCount + ')</span>';
  html += '<span class="view-dropdown__shortcut">9</span>';
  html += '</button>';

  // Separator before actions
  html += '<div class="view-dropdown__separator" role="separator"></div>';

  // "+ New View" action
  html += '<button class="view-dropdown__action" role="menuitem" data-action="new-view">+ New View</button>';

  // "Manage Views..." action
  html += '<button class="view-dropdown__action" role="menuitem" data-action="manage-views">Manage Views\u2026</button>';

  menu.innerHTML = html;

  // Update the trigger label
  var label = $('view-dropdown-label');
  if (label) {
    if (_activeView === 'all') label.textContent = 'All Sessions';
    else if (_activeView === 'hidden') label.textContent = 'Hidden';
    else label.textContent = _activeView;
  }
}

/**
 * Toggle the view dropdown open/closed.
 */
function toggleViewDropdown() {
  var menu = $('view-dropdown-menu');
  var trigger = $('view-dropdown-trigger');
  if (!menu) return;

  var isOpen = !menu.classList.contains('hidden');
  if (isOpen) {
    closeViewDropdown();
  } else {
    renderViewDropdown();
    menu.classList.remove('hidden');
    if (trigger) trigger.setAttribute('aria-expanded', 'true');
  }
}

/**
 * Close the view dropdown.
 */
function closeViewDropdown() {
  var menu = $('view-dropdown-menu');
  var trigger = $('view-dropdown-trigger');
  if (menu) menu.classList.add('hidden');
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
  // Remove any inline "New View" input
  var existingInput = menu && menu.querySelector('.view-dropdown__new-input');
  if (existingInput) existingInput.remove();
}

/**
 * Switch to a different view by name.
 * Updates _activeView, persists via PATCH /api/state, updates the dropdown label,
 * closes the dropdown, and re-renders the grid.
 * @param {string} viewName - "all", "hidden", or a user view name
 */
function switchView(viewName) {
  _activeView = viewName;
  closeViewDropdown();
  renderGrid(_currentSessions || []);
  renderSidebar(_currentSessions || [], _viewingSession);

  // Update dropdown label immediately
  var label = $('view-dropdown-label');
  if (label) {
    if (viewName === 'all') label.textContent = 'All Sessions';
    else if (viewName === 'hidden') label.textContent = 'Hidden';
    else label.textContent = viewName;
  }

  // Persist to server state (fire-and-forget)
  api('PATCH', '/api/state', { active_view: viewName }).catch(function(err) {
    console.warn('[switchView] failed to persist active_view:', err);
  });
}
```

Change 2 — Add the new functions to the `window.MuxplexApp` export (after `renderFilterBar,`):
```javascript
    // View dropdown
    renderViewDropdown,
    toggleViewDropdown,
    closeViewDropdown,
    switchView,
```

Change 3 — Bind event listeners in `bindStaticEventListeners()`. Add after the settings dialog bindings section (after line ~2226):

```javascript
  // View dropdown
  on($('view-dropdown-trigger'), 'click', toggleViewDropdown);

  // View dropdown — delegated click handler for menu items
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
        } else if (action.dataset.action === 'manage-views') {
          closeViewDropdown();
          openSettings();
          switchSettingsTab('views');
        }
      }
    });
  }

  // Click-outside dismiss for view dropdown
  document.addEventListener('click', function(e) {
    var dropdown = $('view-dropdown');
    if (!dropdown) return;
    var menu = $('view-dropdown-menu');
    if (menu && !menu.classList.contains('hidden') && !dropdown.contains(e.target)) {
      closeViewDropdown();
    }
  });
```

Change 4 — Restore `_activeView` from server state in `restoreState()` (line ~216). After `const state = await res.json();` and before the `if (state.active_session)` block, add:

```javascript
    // Restore active_view from server state
    if (state.active_view) {
      _activeView = state.active_view;
      renderViewDropdown();
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "view_dropdown or switch_view or toggle_view" -v
```
Expected: All 5 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: implement view dropdown render, toggle, and view switching"
```

---

## Task 7: Keyboard Shortcuts — Backtick, Number Keys, Arrow Navigation

**Files:**
- Modify: `muxplex/frontend/app.js` (the `handleGlobalKeydown()` function, line ~1812)
- Modify: `muxplex/tests/test_frontend_js.py`

Backtick opens/closes the dropdown on the grid page only (not in fullscreen). Number keys 1–9 switch views directly. Arrow keys + Enter navigate within the open dropdown.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_handle_global_keydown_has_backtick_handler() -> None:
    """handleGlobalKeydown must handle the backtick key for view dropdown."""
    fn_body = _JS.split("function handleGlobalKeydown")[1].split("\nfunction ")[0]
    assert "`" in fn_body or "Backquote" in fn_body or "backtick" in fn_body.lower(), (
        "handleGlobalKeydown must handle the backtick key"
    )


def test_handle_global_keydown_has_number_key_shortcuts() -> None:
    """handleGlobalKeydown must handle number keys 1-9 for view switching."""
    fn_body = _JS.split("function handleGlobalKeydown")[1].split("\nfunction ")[0]
    assert "switchView" in fn_body, (
        "handleGlobalKeydown must call switchView for number key shortcuts"
    )


def test_backtick_only_on_grid_not_fullscreen() -> None:
    """Backtick shortcut must check that _viewMode is 'grid' (not fullscreen)."""
    fn_body = _JS.split("function handleGlobalKeydown")[1].split("\nfunction ")[0]
    # Must guard backtick handler to grid mode only
    assert "_viewMode" in fn_body, (
        "handleGlobalKeydown backtick handler must check _viewMode"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_handle_global_keydown_has_backtick_handler -v
```
Expected: FAIL — backtick handling not in `handleGlobalKeydown`

**Step 3: Apply the changes to `muxplex/frontend/app.js`**

Rewrite `handleGlobalKeydown()` (replace lines ~1812–1830):

```javascript
/**
 * Global keydown handler.
 * If settings are open: Escape closes settings.
 * Comma key (not in inputs) opens settings.
 * Backtick opens/closes view dropdown (grid overview only).
 * Number keys 1-9 switch views directly (grid overview only).
 * Arrow keys navigate within the open dropdown.
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

  // Ignore shortcuts when typing in an input/textarea/select
  var tag = document.activeElement && document.activeElement.tagName;
  var inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';

  if (e.key === ',' && !e.ctrlKey && !e.metaKey && !inInput) {
    openSettings();
    return;
  }

  // View dropdown shortcuts — grid overview only, not fullscreen (backtick is a real character)
  if (_viewMode === 'grid' && !inInput && !e.ctrlKey && !e.metaKey) {
    // Backtick toggles dropdown
    if (e.key === '`') {
      e.preventDefault();
      toggleViewDropdown();
      return;
    }

    // Number keys 1-9 switch views directly
    var num = parseInt(e.key, 10);
    if (num >= 1 && num <= 9) {
      var views = (_serverSettings && _serverSettings.views) || [];
      if (num === 1) {
        switchView('all');
      } else if (num === 9) {
        switchView('hidden');
      } else if (num - 2 < views.length) {
        switchView(views[num - 2].name);
      }
      return;
    }
  }

  // Arrow key navigation within open dropdown
  var dropdownMenu = $('view-dropdown-menu');
  if (dropdownMenu && !dropdownMenu.classList.contains('hidden')) {
    var items = dropdownMenu.querySelectorAll('[role="menuitem"]');
    if (items.length > 0) {
      var current = dropdownMenu.querySelector('[role="menuitem"]:focus');
      var idx = Array.prototype.indexOf.call(items, current);

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        var next = idx < items.length - 1 ? idx + 1 : 0;
        items[next].focus();
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        var prev = idx > 0 ? idx - 1 : items.length - 1;
        items[prev].focus();
        return;
      }
      if (e.key === 'Enter' && current) {
        current.click();
        return;
      }
    }
    if (e.key === 'Escape') {
      closeViewDropdown();
      return;
    }
  }

  if (_viewMode === 'fullscreen' && e.key === 'Escape') {
    e.preventDefault();
    closeSession();
  }
}
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "backtick or number_key" -v
```
Expected: All 3 new tests PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add backtick and number key shortcuts for view dropdown"
```

---

## Task 8: "New View" Inline Creation Flow

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/tests/test_frontend_js.py`

Clicking "+ New View" in the dropdown shows an inline text input. Enter creates the view via `PATCH /api/settings`, closes the dropdown, and switches to the new view.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_show_new_view_input_function_exists() -> None:
    """app.js must define a showNewViewInput function."""
    assert "function showNewViewInput" in _JS, (
        "app.js must contain a showNewViewInput function"
    )


def test_show_new_view_input_patches_settings() -> None:
    """showNewViewInput handler must PATCH /api/settings with the new view."""
    fn_body = _JS.split("function showNewViewInput")[1].split("\nfunction ")[0]
    assert "views" in fn_body and "PATCH" in fn_body, (
        "showNewViewInput must PATCH /api/settings with updated views"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_show_new_view_input_function_exists -v
```
Expected: FAIL — function does not exist

**Step 3: Add the function to `muxplex/frontend/app.js`**

Insert after `closeViewDropdown()`:

```javascript
/**
 * Show an inline text input in the view dropdown for creating a new view.
 * Enter creates the view, Escape cancels.
 */
function showNewViewInput() {
  var menu = $('view-dropdown-menu');
  if (!menu) return;

  // Remove existing input if any
  var existing = menu.querySelector('.view-dropdown__new-input');
  if (existing) { existing.focus(); return; }

  // Hide the "+ New View" action button
  var newBtn = menu.querySelector('[data-action="new-view"]');

  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'view-dropdown__new-input';
  input.placeholder = 'View name';
  input.maxLength = 30;
  input.setAttribute('aria-label', 'New view name');

  // Insert before the "Manage Views" action
  var manageBtn = menu.querySelector('[data-action="manage-views"]');
  if (newBtn) newBtn.replaceWith(input);
  else if (manageBtn) menu.insertBefore(input, manageBtn);
  else menu.appendChild(input);

  input.focus();

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      var name = input.value.trim();
      if (!name) return;

      // Validate: check reserved names and duplicates
      var lower = name.toLowerCase();
      if (lower === 'all' || lower === 'hidden') {
        showToast('"' + name + '" is a reserved name');
        return;
      }
      var views = (_serverSettings && _serverSettings.views) || [];
      for (var i = 0; i < views.length; i++) {
        if (views[i].name === name) {
          showToast('A view named "' + name + '" already exists');
          return;
        }
      }

      // Create the view
      var updatedViews = views.concat([{ name: name, sessions: [] }]);
      api('PATCH', '/api/settings', { views: updatedViews })
        .then(function() {
          if (_serverSettings) _serverSettings.views = updatedViews;
          switchView(name);
        })
        .catch(function(err) {
          showToast('Failed to create view');
          console.warn('[showNewViewInput] PATCH failed:', err);
        });
    } else if (e.key === 'Escape') {
      closeViewDropdown();
    }
  });

  input.addEventListener('blur', function() {
    // Small delay to allow click-on-manage-views to fire first
    setTimeout(function() {
      if (document.activeElement !== input) {
        closeViewDropdown();
      }
    }, 150);
  });
}
```

Add `showNewViewInput` to the `window.MuxplexApp` export (after `switchView,`):
```javascript
    showNewViewInput,
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py -k "new_view_input" -v
```
Expected: Both PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/tests/test_frontend_js.py && git commit -m "feat: add inline New View creation in dropdown"
```

---

## Task 9: "Manage Views" Settings Tab — HTML + JS

**Files:**
- Modify: `muxplex/frontend/index.html` (add Views tab button and panel)
- Modify: `muxplex/frontend/app.js` (add `renderViewsSettingsTab()`)
- Modify: `muxplex/tests/test_frontend_html.py`
- Modify: `muxplex/tests/test_frontend_js.py`

Add a new "Views" tab in the settings dialog with: list of user views, inline rename (click to edit), up/down arrow buttons for reorder, delete with inline "Sure? [Yes] [No]" confirmation.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_html.py`:

```python
def test_settings_has_views_tab_button() -> None:
    """Settings dialog must have a 'Views' tab button."""
    assert 'data-tab="views"' in _HTML, (
        "Settings dialog must have a tab button with data-tab='views'"
    )


def test_settings_has_views_panel() -> None:
    """Settings dialog must have a views panel."""
    assert 'class="settings-panel' in _HTML and 'data-tab="views"' in _HTML, (
        "Settings dialog must have a settings-panel with data-tab='views'"
    )
```

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_render_views_settings_tab_function_exists() -> None:
    """app.js must define a renderViewsSettingsTab function."""
    assert "function renderViewsSettingsTab" in _JS, (
        "app.js must contain a renderViewsSettingsTab function"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py::test_settings_has_views_tab_button muxplex/tests/test_frontend_js.py::test_render_views_settings_tab_function_exists -v
```
Expected: FAIL — no views tab or function

**Step 3: Apply the HTML changes to `muxplex/frontend/index.html`**

Change 1 — Add the "Views" tab button in the settings tabs nav (after the "Sessions" button, line ~98):
```html
        <button class="settings-tab" data-tab="sessions">Sessions</button>
        <button class="settings-tab" data-tab="views">Views</button>
```

Change 2 — Add the views panel (after the sessions panel closing `</div>`, before the `new-session` panel, after line ~194):
```html
        <div class="settings-panel hidden" data-tab="views">
          <div id="views-settings-list" class="views-settings-list"></div>
          <p class="settings-helper" id="views-settings-empty" style="display:none">No user-created views yet. Use the dropdown in the header to create one.</p>
        </div>
```

**Step 4: Apply the JS changes to `muxplex/frontend/app.js`**

Insert after `showNewViewInput()`:

```javascript
/**
 * Render the Views tab content in the settings dialog.
 * Shows a list of user views with: inline rename, up/down reorder buttons,
 * and delete with inline "Sure? [Yes] [No]" confirmation.
 */
function renderViewsSettingsTab() {
  var container = $('views-settings-list');
  var emptyMsg = $('views-settings-empty');
  if (!container) return;

  var views = (_serverSettings && _serverSettings.views) || [];

  if (views.length === 0) {
    container.innerHTML = '';
    if (emptyMsg) emptyMsg.style.display = '';
    return;
  }
  if (emptyMsg) emptyMsg.style.display = 'none';

  var html = '';
  for (var i = 0; i < views.length; i++) {
    var v = views[i];
    html += '<div class="views-settings-row" data-view-index="' + i + '">';
    html += '<span class="views-settings-name" data-view-name="' + escapeHtml(v.name) + '" tabindex="0" role="button" title="Click to rename">' + escapeHtml(v.name) + '</span>';
    html += '<span class="views-settings-count">' + (v.sessions ? v.sessions.length : 0) + ' sessions</span>';
    html += '<div class="views-settings-actions">';
    html += '<button class="views-settings-btn" data-action="move-up" title="Move up"' + (i === 0 ? ' disabled' : '') + '>&uarr;</button>';
    html += '<button class="views-settings-btn" data-action="move-down" title="Move down"' + (i === views.length - 1 ? ' disabled' : '') + '>&darr;</button>';
    html += '<button class="views-settings-btn views-settings-btn--danger" data-action="delete" title="Delete view">&times;</button>';
    html += '</div>';
    html += '</div>';
  }

  container.innerHTML = html;

  // Delegated handlers
  container.onclick = function(e) {
    var target = e.target;

    // Move up
    if (target.dataset.action === 'move-up') {
      var row = target.closest('[data-view-index]');
      var idx = parseInt(row.dataset.viewIndex, 10);
      if (idx > 0) {
        var updatedViews = JSON.parse(JSON.stringify(views));
        var tmp = updatedViews[idx - 1];
        updatedViews[idx - 1] = updatedViews[idx];
        updatedViews[idx] = tmp;
        _saveViewsAndRerender(updatedViews);
      }
      return;
    }

    // Move down
    if (target.dataset.action === 'move-down') {
      var row = target.closest('[data-view-index]');
      var idx = parseInt(row.dataset.viewIndex, 10);
      if (idx < views.length - 1) {
        var updatedViews = JSON.parse(JSON.stringify(views));
        var tmp = updatedViews[idx + 1];
        updatedViews[idx + 1] = updatedViews[idx];
        updatedViews[idx] = tmp;
        _saveViewsAndRerender(updatedViews);
      }
      return;
    }

    // Delete — show inline confirmation
    if (target.dataset.action === 'delete') {
      var actionsDiv = target.closest('.views-settings-actions');
      if (!actionsDiv) return;
      actionsDiv.innerHTML =
        '<span class="views-settings-confirm">Sure?</span> ' +
        '<button class="views-settings-btn views-settings-btn--danger" data-action="confirm-delete">Yes</button> ' +
        '<button class="views-settings-btn" data-action="cancel-delete">No</button>';
      return;
    }

    // Confirm delete
    if (target.dataset.action === 'confirm-delete') {
      var row = target.closest('[data-view-index]');
      var idx = parseInt(row.dataset.viewIndex, 10);
      var deletedName = views[idx].name;
      var updatedViews = JSON.parse(JSON.stringify(views));
      updatedViews.splice(idx, 1);
      // If the deleted view is the active view, fall back to "all"
      if (_activeView === deletedName) {
        switchView('all');
      }
      _saveViewsAndRerender(updatedViews);
      return;
    }

    // Cancel delete
    if (target.dataset.action === 'cancel-delete') {
      renderViewsSettingsTab();
      return;
    }

    // Click on name — inline rename
    var nameEl = target.closest('.views-settings-name');
    if (nameEl) {
      var currentName = nameEl.dataset.viewName;
      var input = document.createElement('input');
      input.type = 'text';
      input.className = 'settings-input views-settings-rename-input';
      input.value = currentName;
      input.maxLength = 30;
      nameEl.replaceWith(input);
      input.focus();
      input.select();

      function commitRename() {
        var newName = input.value.trim();
        if (!newName || newName === currentName) {
          renderViewsSettingsTab();
          return;
        }
        var lower = newName.toLowerCase();
        if (lower === 'all' || lower === 'hidden') {
          showToast('"' + newName + '" is a reserved name');
          renderViewsSettingsTab();
          return;
        }
        // Check duplicate
        for (var j = 0; j < views.length; j++) {
          if (views[j].name === newName) {
            showToast('A view named "' + newName + '" already exists');
            renderViewsSettingsTab();
            return;
          }
        }
        var updatedViews = JSON.parse(JSON.stringify(views));
        var row = input.closest('[data-view-index]');
        var idx = parseInt(row.dataset.viewIndex, 10);
        updatedViews[idx].name = newName;
        // If the renamed view is the active view, update active view name
        if (_activeView === currentName) {
          _activeView = newName;
          api('PATCH', '/api/state', { active_view: newName }).catch(function() {});
        }
        _saveViewsAndRerender(updatedViews);
      }

      input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { commitRename(); }
        else if (e.key === 'Escape') { renderViewsSettingsTab(); }
      });
      input.addEventListener('blur', function() {
        setTimeout(commitRename, 100);
      });
    }
  };
}

/**
 * Save updated views array via PATCH /api/settings and re-render the Views tab.
 * @param {Array} updatedViews
 */
function _saveViewsAndRerender(updatedViews) {
  api('PATCH', '/api/settings', { views: updatedViews })
    .then(function() {
      if (_serverSettings) _serverSettings.views = updatedViews;
      renderViewsSettingsTab();
      renderViewDropdown();
    })
    .catch(function(err) {
      showToast('Failed to save views');
      console.warn('[_saveViewsAndRerender] PATCH failed:', err);
      renderViewsSettingsTab();
    });
}
```

Change 2 — Call `renderViewsSettingsTab()` inside `openSettings()` (line ~1670, after the `loadServerSettings().then(function(ss) {` block, at the end of the `.then()` callback before the closing `});`):

```javascript
    // Render Views tab content
    renderViewsSettingsTab();
```

Change 3 — Add `renderViewsSettingsTab` and `_saveViewsAndRerender` to `window.MuxplexApp`:
```javascript
    renderViewsSettingsTab,
```

**Step 5: Add CSS for the views settings rows to `muxplex/frontend/style.css`**

Append after the view dropdown styles:

```css
/* ── Views Settings Tab ────────────────────────────────────────────── */

.views-settings-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.views-settings-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--bg);
}

.views-settings-row:hover {
  background: var(--bg-surface);
}

.views-settings-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.views-settings-name:hover {
  text-decoration: underline;
  text-decoration-style: dotted;
}

.views-settings-count {
  font-size: 11px;
  color: var(--text-dim);
  white-space: nowrap;
}

.views-settings-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.views-settings-btn {
  width: 24px;
  height: 24px;
  padding: 0;
  background: transparent;
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.views-settings-btn:hover:not(:disabled) {
  border-color: var(--border);
  color: var(--text);
}

.views-settings-btn:disabled {
  opacity: 0.3;
  cursor: default;
}

.views-settings-btn--danger:hover:not(:disabled) {
  border-color: var(--err);
  color: var(--err);
}

.views-settings-confirm {
  font-size: 12px;
  color: var(--text-muted);
}

.views-settings-rename-input {
  flex: 1;
  min-width: 0;
}
```

**Step 6: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py -k "views_tab or views_panel" muxplex/tests/test_frontend_js.py::test_render_views_settings_tab_function_exists -v
```
Expected: All 3 new tests PASS

**Step 7: Commit**

```bash
cd muxplex && git add muxplex/frontend/index.html muxplex/frontend/app.js muxplex/frontend/style.css muxplex/tests/test_frontend_html.py muxplex/tests/test_frontend_js.py && git commit -m "feat: add Manage Views settings tab with rename, reorder, delete"
```

---

## Task 10: Sidebar View Switcher in Fullscreen Mode

**Files:**
- Modify: `muxplex/frontend/index.html` (sidebar header, line ~44)
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/style.css`
- Modify: `muxplex/tests/test_frontend_html.py`

Add a `▾` dropdown to the fullscreen sidebar header, same render function as the header dropdown but a separate DOM instance.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_html.py`:

```python
def test_sidebar_view_dropdown_exists() -> None:
    """Sidebar must contain a view-dropdown trigger for fullscreen view switching."""
    assert 'id="sidebar-view-dropdown-trigger"' in _HTML, (
        "index.html must contain a #sidebar-view-dropdown-trigger element"
    )


def test_sidebar_view_dropdown_menu_exists() -> None:
    """Sidebar must contain a view-dropdown-menu container."""
    assert 'id="sidebar-view-dropdown-menu"' in _HTML, (
        "index.html must contain a #sidebar-view-dropdown-menu element"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py::test_sidebar_view_dropdown_exists -v
```
Expected: FAIL

**Step 3: Apply the HTML change to `muxplex/frontend/index.html`**

Replace the sidebar-header div (lines ~44–47):
```html
        <div class="sidebar-header">
          <div class="sidebar-view-dropdown" id="sidebar-view-dropdown">
            <button id="sidebar-view-dropdown-trigger" class="sidebar-view-trigger" aria-haspopup="true" aria-expanded="false" aria-controls="sidebar-view-dropdown-menu">
              <span id="sidebar-view-label">All Sessions</span>
              <span class="view-dropdown__caret" aria-hidden="true">&#9662;</span>
            </button>
            <div id="sidebar-view-dropdown-menu" class="view-dropdown__menu hidden" role="menu" aria-label="Switch view"></div>
          </div>
          <button id="sidebar-collapse-btn" class="sidebar-collapse-btn" aria-label="Collapse session list">&#8249;</button>
        </div>
```

**Step 4: Apply the JS changes to `muxplex/frontend/app.js`**

Add a sidebar-specific render and toggle function. Insert after `closeViewDropdown()`:

```javascript
/**
 * Render the sidebar view dropdown (fullscreen mode).
 * Uses the same data as the header dropdown but targets #sidebar-view-dropdown-menu.
 */
function renderSidebarViewDropdown() {
  var menu = $('sidebar-view-dropdown-menu');
  if (!menu) return;

  // Reuse the same rendering logic by temporarily swapping target IDs
  var headerMenu = $('view-dropdown-menu');
  var headerLabel = $('view-dropdown-label');

  // Render into the sidebar menu directly (same HTML structure)
  var views = (_serverSettings && _serverSettings.views) || [];
  var hidden = (_serverSettings && _serverSettings.hidden_sessions) || [];
  var hiddenCount = hidden.length;

  var html = '';
  var allActive = _activeView === 'all' ? ' view-dropdown__item--active' : '';
  html += '<button class="view-dropdown__item' + allActive + '" role="menuitem" data-view="all">All Sessions</button>';

  if (views.length > 0) html += '<div class="view-dropdown__separator" role="separator"></div>';
  for (var i = 0; i < views.length; i++) {
    var vActive = _activeView === views[i].name ? ' view-dropdown__item--active' : '';
    html += '<button class="view-dropdown__item' + vActive + '" role="menuitem" data-view="' + escapeHtml(views[i].name) + '">' + escapeHtml(views[i].name) + '</button>';
  }

  html += '<div class="view-dropdown__separator" role="separator"></div>';
  var hiddenActive = _activeView === 'hidden' ? ' view-dropdown__item--active' : '';
  html += '<button class="view-dropdown__item' + hiddenActive + '" role="menuitem" data-view="hidden">Hidden';
  if (hiddenCount > 0) html += ' <span class="view-dropdown__count">(' + hiddenCount + ')</span>';
  html += '</button>';

  menu.innerHTML = html;

  // Update sidebar label
  var label = $('sidebar-view-label');
  if (label) {
    if (_activeView === 'all') label.textContent = 'All Sessions';
    else if (_activeView === 'hidden') label.textContent = 'Hidden';
    else label.textContent = _activeView;
  }
}

/**
 * Toggle the sidebar view dropdown open/closed.
 */
function toggleSidebarViewDropdown() {
  var menu = $('sidebar-view-dropdown-menu');
  var trigger = $('sidebar-view-dropdown-trigger');
  if (!menu) return;
  var isOpen = !menu.classList.contains('hidden');
  if (isOpen) {
    menu.classList.add('hidden');
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
  } else {
    renderSidebarViewDropdown();
    menu.classList.remove('hidden');
    if (trigger) trigger.setAttribute('aria-expanded', 'true');
  }
}
```

Change 2 — Bind the sidebar dropdown events in `bindStaticEventListeners()`. Add after the header dropdown bindings:

```javascript
  // Sidebar view dropdown (fullscreen)
  on($('sidebar-view-dropdown-trigger'), 'click', toggleSidebarViewDropdown);
  var sidebarViewMenu = $('sidebar-view-dropdown-menu');
  if (sidebarViewMenu) {
    sidebarViewMenu.addEventListener('click', function(e) {
      var item = e.target.closest('[data-view]');
      if (item) {
        switchView(item.dataset.view);
        // Close sidebar dropdown
        sidebarViewMenu.classList.add('hidden');
        var trigger = $('sidebar-view-dropdown-trigger');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
      }
    });
  }
```

Change 3 — Update `switchView()` to also update the sidebar label. Add after the existing label update:

```javascript
  // Update sidebar label too
  var sidebarLabel = $('sidebar-view-label');
  if (sidebarLabel) {
    if (viewName === 'all') sidebarLabel.textContent = 'All Sessions';
    else if (viewName === 'hidden') sidebarLabel.textContent = 'Hidden';
    else sidebarLabel.textContent = viewName;
  }
```

**Step 5: Add sidebar dropdown CSS to `muxplex/frontend/style.css`**

Add after the view dropdown styles:

```css
.sidebar-view-dropdown {
  position: relative;
  flex: 1;
  min-width: 0;
}

.sidebar-view-trigger {
  display: flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: none;
  padding: 2px 4px;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.sidebar-view-trigger:hover {
  color: var(--accent);
}

.sidebar-view-dropdown .view-dropdown__menu {
  left: 0;
  top: calc(100% + 2px);
  min-width: 180px;
}
```

**Step 6: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py -k "sidebar_view" -v
```
Expected: Both new tests PASS

**Step 7: Commit**

```bash
cd muxplex && git add muxplex/frontend/index.html muxplex/frontend/app.js muxplex/frontend/style.css muxplex/tests/test_frontend_html.py && git commit -m "feat: add sidebar view switcher dropdown in fullscreen mode"
```

---

## Task 11: Remove `filtered` gridViewMode Rendering Code

**Files:**
- Modify: `muxplex/frontend/app.js` (lines ~761–764, ~780–787, ~820–827)
- Modify: `muxplex/frontend/index.html` (line ~220, the "Filtered" option)
- Modify: `muxplex/tests/test_frontend_js.py`

Phase 1 removed `filtered` from the settings value but may not have removed the rendering code. This task removes the filter pill bar rendering, the `_activeFilterDevice` filter logic in `renderGrid()`, and the "Filtered" option from the HTML select.

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_frontend_js.py`:

```python
def test_render_grid_no_filtered_mode_check() -> None:
    """renderGrid must not check for _gridViewMode === 'filtered'."""
    fn_body = _JS.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "'filtered'" not in fn_body and '"filtered"' not in fn_body, (
        "renderGrid must not contain any 'filtered' mode checks"
    )


def test_no_active_filter_device_in_render_grid() -> None:
    """renderGrid must not reference _activeFilterDevice."""
    fn_body = _JS.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "_activeFilterDevice" not in fn_body, (
        "renderGrid must not reference _activeFilterDevice"
    )
```

Add to `muxplex/tests/test_frontend_html.py`:

```python
def test_no_filtered_option_in_view_mode_select() -> None:
    """The view mode select must not have a 'filtered' option."""
    # Find the setting-view-mode select
    assert '>Filtered<' not in _HTML, (
        "The view mode select must not have a 'Filtered' option"
    )
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_render_grid_no_filtered_mode_check -v
```
Expected: FAIL — `'filtered'` is still in `renderGrid`

**Step 3: Apply the changes**

Change 1 — In `muxplex/frontend/app.js`, remove filtered mode code from `renderGrid()`:

Remove lines ~761–764 (the filtered device filter block):
```javascript
  // DELETE THIS BLOCK:
  // In filtered mode, apply device filter
  if (_gridViewMode === 'filtered' && _activeFilterDevice !== 'all') {
    visible = visible.filter(function(s) { return s.deviceName === _activeFilterDevice; });
  }
```

Remove lines ~780–787 (the filter bar rendering in the empty-state branch):
```javascript
  // DELETE THIS BLOCK:
    // Show filter bar even when filtered to empty (so user can switch back)
    if (filterBar) {
      if (_gridViewMode === 'filtered') {
        renderFilterBar(filterBar, sessions);
      } else {
        filterBar.innerHTML = '';
      }
    }
```
Replace with:
```javascript
    if (filterBar) filterBar.innerHTML = '';
```

Remove lines ~820–827 (the filter bar rendering in the normal branch):
```javascript
  // DELETE THIS BLOCK:
  // Render filter bar
  if (filterBar) {
    if (_gridViewMode === 'filtered') {
      renderFilterBar(filterBar, sessions);
    } else {
      filterBar.innerHTML = '';
    }
  }
```
Replace with:
```javascript
  if (filterBar) filterBar.innerHTML = '';
```

Change 2 — In `loadGridViewMode()` (line ~1547), add the fallback guard if not already present from Phase 1:
```javascript
function loadGridViewMode() {
  var ds = getDisplaySettings();
  var mode = ds.gridViewMode || 'flat';
  if (mode === 'filtered') mode = 'flat';
  return mode;
}
```

Change 3 — In `_setGridViewMode()` test helper, add the guard:
```javascript
function _setGridViewMode(mode) {
  if (mode === 'filtered') mode = 'flat';
  _gridViewMode = mode;
}
```

Change 4 — In `muxplex/frontend/index.html`, remove the "Filtered" option from the view mode select (line ~220):
```html
<!-- DELETE THIS LINE: -->
                <option value="filtered">Filtered</option>
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_js.py::test_render_grid_no_filtered_mode_check muxplex/tests/test_frontend_js.py::test_no_active_filter_device_in_render_grid muxplex/tests/test_frontend_html.py::test_no_filtered_option_in_view_mode_select -v
```
Expected: All 3 PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/index.html muxplex/tests/test_frontend_js.py muxplex/tests/test_frontend_html.py && git commit -m "feat: remove filtered gridViewMode rendering code and filter pill bar"
```

---

## Task 12: Remove Hidden Sessions Section from Settings Panel

**Files:**
- Modify: `muxplex/frontend/index.html` (lines ~190–193)
- Modify: `muxplex/frontend/app.js` (the hidden sessions checkbox population in `openSettings()`)
- Modify: `muxplex/tests/test_frontend_html.py`

The "Hidden Sessions" checkbox list in the Sessions settings tab is being replaced by the Hidden view + tile flyout (Phase 3). Remove it now since the Hidden view already exists via the dropdown.

**Step 1: Write the failing test**

Add to `muxplex/tests/test_frontend_html.py`:

```python
def test_no_hidden_sessions_checkbox_list_in_settings() -> None:
    """The settings panel must not contain the hidden-sessions checkbox section."""
    assert 'id="setting-hidden-sessions"' not in _HTML, (
        "The settings panel must not contain #setting-hidden-sessions — it has been replaced by the Hidden view"
    )
```

**Step 2: Run test to verify it fails**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py::test_no_hidden_sessions_checkbox_list_in_settings -v
```
Expected: FAIL — `setting-hidden-sessions` still in HTML

**Step 3: Apply the HTML change to `muxplex/frontend/index.html`**

Remove lines ~190–193 (the Hidden Sessions field in the sessions panel):
```html
<!-- DELETE THIS BLOCK: -->
          <div class="settings-field settings-field--column">
            <label class="settings-label">Hidden Sessions</label>
            <div id="setting-hidden-sessions" class="settings-checkbox-list"></div>
          </div>
```

**Step 4: Apply the JS change to `muxplex/frontend/app.js`**

In `openSettings()`, remove the hidden sessions checkbox population block (lines ~1692–1710):
```javascript
    // DELETE THIS BLOCK:
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
```

Also remove the hidden sessions delegated event handler in `bindStaticEventListeners()` (lines ~2300–2312):
```javascript
  // DELETE THIS BLOCK:
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
```

**Step 5: Run test to verify it passes**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py::test_no_hidden_sessions_checkbox_list_in_settings -v
```
Expected: PASS

**Step 6: Commit**

```bash
cd muxplex && git add muxplex/frontend/index.html muxplex/frontend/app.js muxplex/tests/test_frontend_html.py && git commit -m "feat: remove hidden sessions checkbox section from settings (replaced by Hidden view)"
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

**Step 3: Verify the Phase 2 feature chain end-to-end**

Check that the full chain works by listing what Phase 2 established:

1. `PATCH /api/state` accepts `active_view` field ✓
2. `getVisibleSessions()` filters by active view (all/hidden/user view) ✓
3. New sessions auto-add to active user view on creation ✓
4. Header dropdown HTML with ARIA attributes ✓
5. Header dropdown CSS ✓
6. `renderViewDropdown()` / `toggleViewDropdown()` / `switchView()` ✓
7. Keyboard shortcuts: backtick (grid only), 1–9 number keys, arrow navigation ✓
8. Inline "New View" creation from dropdown ✓
9. "Manage Views" settings tab (rename, reorder, delete) ✓
10. Sidebar view switcher in fullscreen mode ✓
11. `filtered` gridViewMode rendering code removed ✓
12. Hidden sessions checkbox section removed from settings ✓

**Step 4: Commit any remaining fixes**

```bash
cd muxplex && git add -A && git status
```
If there are uncommitted changes, commit them:
```bash
cd muxplex && git commit -m "chore: phase 2 integration fixes"
```

---

## Deferred to Phase 3

The following are explicitly NOT in Phase 2:

- **Tile flyout menu** (`⋮` button, context menu, Add to View submenu, Remove from View, Hide/Unhide)
- **Add Sessions panel** (overlay with checkboxes for adding sessions to a view)
- **Kill session inline confirmation** (replacing the current `confirm()` dialog with inline Yes/No)
- **Hide/Unhide actions via flyout** (currently only accessible via the Hidden view dropdown)
- **Mobile bottom sheet variants** for the tile flyout and Add Sessions panel
- **Session key migration** (rewriting old positional `remoteId:name` keys — the integer fallback from Phase 1 provides backward compatibility)
- **Config path file migration** (moving files from `~/.local/share/tmux-web/` to `~/.local/share/muxplex/`)
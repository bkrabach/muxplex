# Settings Panel — Phase 1: Backend + Palette Removal + Settings Infrastructure

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Phase 1 of 2** — complete this phase before starting [Phase 2](./2026-03-29-settings-phase2.md).

**Design doc:** [`docs/plans/2026-03-29-settings-design.md`](./2026-03-29-settings-design.md)

**Goal:** Remove the dead command palette, add the backend settings API + new-session endpoint, and build the settings modal shell with the Display tab wired up for immediate visual feedback.

**Architecture:** Server-side settings live in `~/.config/muxplex/settings.json` (loaded/saved via a new `muxplex/settings.py` module). Three new endpoints: `GET/PATCH /api/settings` and `POST /api/sessions` (create). The frontend settings modal is a `<dialog>` element with tabbed navigation. Display tab fields (font size, hover delay, grid columns) use localStorage and apply immediately.

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic, vanilla JS, CSS custom properties.

---

### Task 1: Remove command palette — HTML + CSS

**Files:**
- Modify: `muxplex/frontend/index.html` (lines 35, 50-58)
- Modify: `muxplex/frontend/style.css` (lines 356-364, 690-778)
- Modify: `muxplex/tests/test_frontend_html.py` (lines 60-76, 243)
- Modify: `muxplex/tests/test_frontend_css.py` (lines 69-74)

**Step 1: Remove palette trigger button from expanded header**

In `muxplex/frontend/index.html`, remove the `#palette-trigger` button from the expanded header (line 35):

```html
<!-- REMOVE this line: -->
<button id="palette-trigger" class="palette-trigger" aria-label="Open command palette">&#8984;K</button>
```

The expanded header should now end after `<span id="expanded-session-name">`:

```html
    <header class="expanded-header">
      <button id="back-btn" class="back-btn" aria-label="Back">&#8592;</button>
      <button id="sidebar-toggle-btn" class="sidebar-toggle-btn" aria-label="Toggle session list">&#9776;</button>
      <span id="expanded-session-name" class="expanded-session-name"></span>
    </header>
```

**Step 2: Remove command palette HTML block**

In `muxplex/frontend/index.html`, remove the entire command palette block (lines 50-58):

```html
<!-- REMOVE this entire block: -->
  <!-- ── Command palette ───────────────────────────────────────── -->
  <div id="command-palette" class="command-palette hidden" role="dialog" aria-modal="true" aria-label="Switch session">
    <div class="command-palette__backdrop" id="palette-backdrop"></div>
    <div class="command-palette__dialog">
      <input id="palette-input" class="command-palette__input" type="text"
             placeholder="Jump to session…" autocomplete="off" spellcheck="false">
      <ul id="palette-list" class="command-palette__list" role="listbox" aria-label="Sessions"></ul>
    </div>
  </div>
```

**Step 3: Remove palette CSS**

In `muxplex/frontend/style.css`, remove the `.palette-trigger` block (lines 356-364):

```css
/* REMOVE this block: */
.palette-trigger {
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-dim);
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
}
```

Remove the entire "Command palette overlay" CSS section (lines 690-778 — from the comment block through `.palette-item__time`):

```css
/* REMOVE everything from this comment through .palette-item__time { ... } */
/* ============================================================
   Command palette overlay (desktop session switching)
   ============================================================ */

.command-palette { ... }
.command-palette__backdrop { ... }
.command-palette__dialog { ... }
.command-palette__input { ... }
.command-palette__input::placeholder { ... }
.command-palette__list { ... }
.palette-item { ... }
.palette-item:hover,
.palette-item--selected { ... }
.palette-item__index { ... }
.palette-item__name { ... }
.palette-item__bell { ... }
.palette-item__time { ... }
```

**Step 4: Update HTML tests**

In `muxplex/tests/test_frontend_html.py`, update `test_html_expanded_view_elements` (line 60-69) to remove `palette-trigger`:

```python
def test_html_expanded_view_elements() -> None:
    """id=back-btn, expanded-session-name, reconnect-overlay."""
    soup = _SOUP
    for id_ in (
        "back-btn",
        "expanded-session-name",
        "reconnect-overlay",
    ):
        assert soup.find(id=id_), f"Missing element with id='{id_}'"
```

Remove `test_html_command_palette` entirely (lines 72-76):

```python
# DELETE this entire function:
def test_html_command_palette() -> None:
    """id=command-palette, palette-input, palette-list, palette-backdrop."""
    ...
```

In the `test_html_elements_carry_their_css_classes` test (around line 243), remove the palette-trigger entry:

```python
# REMOVE this tuple from the cases list:
        ("palette-trigger", "palette-trigger", "needs border and hover styles"),
```

**Step 5: Update CSS tests**

In `muxplex/tests/test_frontend_css.py`, remove `test_css_command_palette` (lines 69-74):

```python
# DELETE this entire function:
def test_css_command_palette():
    css = read_css()
    assert ".command-palette__dialog" in css
    assert ".command-palette__input" in css
    assert ".palette-item" in css
    assert ".palette-item--selected" in css
```

**Step 6: Run tests to verify nothing breaks**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py muxplex/tests/test_frontend_css.py -v
```

Expected: All tests pass (palette tests removed, no other test depends on palette HTML/CSS).

**Step 7: Commit**

```bash
git add -A && git commit -m "refactor: remove command palette HTML and CSS"
```

---

### Task 2: Remove command palette — JavaScript

**Files:**
- Modify: `muxplex/frontend/app.js` (lines 901-1086, 1169-1170, 1219-1247, 1274-1332)

**Step 1: Remove palette state variables**

In `muxplex/frontend/app.js`, remove the command palette state block (lines 901-906):

```javascript
// REMOVE this entire block:
// ─── Command palette state ────────────────────────────────────────────────
const PALETTE_MAX_ITEMS = 9;
let _paletteSelectedIndex = 0;
let _paletteFilteredSessions = [];
let _paletteOpen = false;
let _paletteInputListener = null;
```

**Step 2: Remove palette functions**

Remove the entire "Command palette functions" section (lines 908-1062):
- `renderPaletteList`
- `highlightPaletteItem`
- `openPalette`
- `closePalette`
- `onPaletteInput`
- `handlePaletteKeydown`

**Step 3: Simplify handleGlobalKeydown**

Replace the current `handleGlobalKeydown` function (lines 1071-1086) with a version that removes palette references. Keep only the Escape-to-close-session behavior:

```javascript
/**
 * Global keydown handler.
 * In fullscreen: Escape returns to grid.
 * @param {KeyboardEvent} e
 */
function handleGlobalKeydown(e) {
  if (_viewMode === 'fullscreen') {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeSession();
    }
  }
}
```

**Step 4: Remove palette event bindings from bindStaticEventListeners**

In `bindStaticEventListeners` (around lines 1164-1210), remove these two lines:

```javascript
// REMOVE these two lines:
  on($('palette-trigger'), 'click', openPalette);
  on($('palette-backdrop'), 'click', closePalette);
```

**Step 5: Remove palette test-only helpers**

Remove these test-only helpers (lines 1219-1247):

```javascript
// REMOVE all of these:
/** Test-only: set _paletteFilteredSessions directly. */
function _setPaletteFilteredSessions(sessions) { ... }
/** Test-only: get _paletteFilteredSessions. */
function _getPaletteFilteredSessions() { ... }
/** Test-only: set _paletteSelectedIndex directly. */
function _setPaletteSelectedIndex(index) { ... }
/** Test-only: get _paletteSelectedIndex. */
function _getPaletteSelectedIndex() { ... }
/** Test-only: set _paletteOpen directly. */
function _setPaletteOpen(val) { ... }
/** Test-only: get _paletteOpen. */
function _isPaletteOpen() { ... }
```

**Step 6: Remove palette exports from module.exports**

In the `module.exports` block (starting around line 1274), remove these entries:

```javascript
// REMOVE these exports:
    // Command palette
    renderPaletteList,
    highlightPaletteItem,
    openPalette,
    closePalette,
    onPaletteInput,
    handlePaletteKeydown,
    // Test-only helpers (palette)
    _setPaletteFilteredSessions,
    _getPaletteFilteredSessions,
    _setPaletteSelectedIndex,
    _getPaletteSelectedIndex,
    _setPaletteOpen,
    _isPaletteOpen,
```

Keep `handleGlobalKeydown` and `bindStaticEventListeners` in exports — they still exist.

**Step 7: Run full test suite**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 8: Commit**

```bash
git add -A && git commit -m "refactor: remove command palette JavaScript"
```

---

### Task 3: Server-side settings module

**Files:**
- Create: `muxplex/settings.py`
- Create: `muxplex/tests/test_settings.py`

**Step 1: Write the failing tests**

Create `muxplex/tests/test_settings.py`:

```python
"""Tests for muxplex/settings.py — settings file management."""

import json

import pytest

from muxplex import settings as settings_mod


@pytest.fixture(autouse=True)
def redirect_settings_path(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH to tmp_path so tests don't touch real config."""
    tmp_settings = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_settings)


def test_load_returns_defaults_when_no_file():
    """load_settings() returns DEFAULT_SETTINGS when file doesn't exist."""
    result = settings_mod.load_settings()
    assert result == settings_mod.DEFAULT_SETTINGS


def test_load_returns_saved_values(tmp_path, monkeypatch):
    """load_settings() reads values from existing settings.json."""
    path = settings_mod.SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sort_order": "alphabetical"}))
    result = settings_mod.load_settings()
    assert result["sort_order"] == "alphabetical"
    # Missing keys filled with defaults
    assert result["default_session"] is None


def test_save_creates_file_and_dirs():
    """save_settings() creates parent dirs and writes JSON."""
    settings_mod.save_settings({"sort_order": "recent"})
    assert settings_mod.SETTINGS_PATH.exists()
    data = json.loads(settings_mod.SETTINGS_PATH.read_text())
    assert data["sort_order"] == "recent"


def test_save_merges_with_defaults():
    """save_settings() merges partial data with defaults."""
    settings_mod.save_settings({"sort_order": "alphabetical"})
    data = json.loads(settings_mod.SETTINGS_PATH.read_text())
    assert data["sort_order"] == "alphabetical"
    assert data["new_session_template"] == "tmux new-session -d -s {name}"


def test_load_handles_corrupt_json(tmp_path):
    """load_settings() returns defaults if JSON is corrupt."""
    path = settings_mod.SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json {{{")
    result = settings_mod.load_settings()
    assert result == settings_mod.DEFAULT_SETTINGS


def test_patch_settings_merges_single_field():
    """patch_settings() merges a single field into existing settings."""
    settings_mod.save_settings(settings_mod.DEFAULT_SETTINGS.copy())
    result = settings_mod.patch_settings({"sort_order": "alphabetical"})
    assert result["sort_order"] == "alphabetical"
    assert result["default_session"] is None  # unchanged


def test_patch_settings_ignores_unknown_keys():
    """patch_settings() ignores keys not in DEFAULT_SETTINGS."""
    settings_mod.save_settings(settings_mod.DEFAULT_SETTINGS.copy())
    result = settings_mod.patch_settings({"bogus_key": "ignored"})
    assert "bogus_key" not in result
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'muxplex.settings'`

**Step 3: Write the implementation**

Create `muxplex/settings.py`:

```python
"""
muxplex settings — server-side configuration file management.

Settings are stored in ~/.config/muxplex/settings.json.
"""

import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------

SETTINGS_PATH: Path = Path.home() / ".config" / "muxplex" / "settings.json"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: dict = {
    "default_session": None,
    "sort_order": "manual",
    "hidden_sessions": [],
    "window_size_largest": False,
    "auto_open_created": True,
    "new_session_template": "tmux new-session -d -s {name}",
}

# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load_settings() -> dict:
    """Load settings from disk, returning defaults for missing keys or corrupt files."""
    defaults = DEFAULT_SETTINGS.copy()
    if not SETTINGS_PATH.exists():
        return defaults
    try:
        data = json.loads(SETTINGS_PATH.read_text())
        if not isinstance(data, dict):
            return defaults
    except (json.JSONDecodeError, OSError):
        _log.warning("Corrupt settings file at %s, using defaults", SETTINGS_PATH)
        return defaults
    # Merge: file values override defaults, unknown keys ignored
    for key in defaults:
        if key in data:
            defaults[key] = data[key]
    return defaults


def save_settings(data: dict) -> None:
    """Write settings to disk, merging with defaults for any missing keys."""
    merged = DEFAULT_SETTINGS.copy()
    for key in merged:
        if key in data:
            merged[key] = data[key]
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2) + "\n")


def patch_settings(patch: dict) -> dict:
    """Load current settings, merge patch (known keys only), save, and return result."""
    current = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in patch:
            current[key] = patch[key]
    save_settings(current)
    return current
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_settings.py -v
```

Expected: All 8 tests pass.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: add server-side settings module"
```

---

### Task 4: Settings API endpoints

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

**Step 1: Write the failing tests**

Add to the bottom of `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------


def test_get_settings_returns_defaults(client, monkeypatch):
    """GET /api/settings returns default settings when no file exists."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", client.app.state._tmp_path / "settings.json")
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["sort_order"] == "manual"
    assert data["new_session_template"] == "tmux new-session -d -s {name}"


def test_get_settings_returns_saved_values(client, tmp_path, monkeypatch):
    """GET /api/settings returns previously saved settings."""
    import json
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"sort_order": "alphabetical"}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["sort_order"] == "alphabetical"


# ---------------------------------------------------------------------------
# PATCH /api/settings
# ---------------------------------------------------------------------------


def test_patch_settings_updates_field(client, tmp_path, monkeypatch):
    """PATCH /api/settings merges a single field and returns updated settings."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"sort_order": "alphabetical"})
    assert response.status_code == 200
    data = response.json()
    assert data["sort_order"] == "alphabetical"
    assert data["default_session"] is None  # unchanged default


def test_patch_settings_ignores_unknown_keys(client, tmp_path, monkeypatch):
    """PATCH /api/settings ignores keys not in the schema."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"unknown_key": "value"})
    assert response.status_code == 200
    assert "unknown_key" not in response.json()
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_get_settings_returns_defaults -v
```

Expected: FAIL — 404 (route doesn't exist yet).

**Step 3: Add the endpoints to main.py**

In `muxplex/main.py`, add the import at the top (after existing imports):

```python
from muxplex.settings import load_settings, patch_settings
```

Add the route handlers after the `setup_hooks` endpoint and before the WebSocket proxy section:

```python
# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.get("/api/settings")
async def get_settings() -> dict:
    """Return server-side settings."""
    return load_settings()


@app.patch("/api/settings")
async def update_settings(request: Request) -> dict:
    """Partial update of server-side settings. Merges known keys only."""
    body = await request.json()
    return patch_settings(body)
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py -k "settings" -v
```

Expected: All 4 settings tests pass.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: add GET/PATCH /api/settings endpoints"
```

---

### Task 5: POST /api/sessions (create new session)

**Files:**
- Modify: `muxplex/main.py`
- Modify: `muxplex/tests/test_api.py`

**Step 1: Write the failing tests**

Add to the bottom of `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# POST /api/sessions (create new session)
# ---------------------------------------------------------------------------


def test_create_session_returns_200_with_name(client, tmp_path, monkeypatch):
    """POST /api/sessions returns 200 with the session name."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    # Mock subprocess so nothing actually runs
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: None)

    response = client.post("/api/sessions", json={"name": "my-project"})
    assert response.status_code == 200
    assert response.json()["name"] == "my-project"


def test_create_session_substitutes_name_in_template(client, tmp_path, monkeypatch):
    """POST /api/sessions substitutes {name} in the template command."""
    import json
    import subprocess
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "new_session_template": "echo {name}"
    }))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    captured_cmd = []
    original_popen = subprocess.Popen

    def mock_popen(*args, **kwargs):
        captured_cmd.append(args[0] if args else kwargs.get("args"))
        return None

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    client.post("/api/sessions", json={"name": "test-proj"})
    assert len(captured_cmd) == 1
    assert "test-proj" in captured_cmd[0]


def test_create_session_rejects_empty_name(client):
    """POST /api/sessions with empty name returns 422."""
    response = client.post("/api/sessions", json={"name": ""})
    assert response.status_code == 422


def test_create_session_rejects_missing_name(client):
    """POST /api/sessions without name field returns 422."""
    response = client.post("/api/sessions", json={})
    assert response.status_code == 422
```

**Step 2: Run tests to verify they fail**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py::test_create_session_returns_200_with_name -v
```

Expected: FAIL — 405 or 404 (route doesn't exist yet).

**Step 3: Add the endpoint to main.py**

Add to the imports at the top of `muxplex/main.py`:

```python
import subprocess
```

Add a new Pydantic model near the existing models:

```python
class CreateSessionPayload(BaseModel):
    name: str

    @property
    def validated_name(self) -> str:
        if not self.name or not self.name.strip():
            raise ValueError("name must not be empty")
        return self.name.strip()
```

Add the route handler after the settings endpoints:

```python
# ---------------------------------------------------------------------------
# New session creation
# ---------------------------------------------------------------------------


@app.post("/api/sessions")
async def create_session(payload: CreateSessionPayload) -> dict:
    """Create a new session by executing the configured template command.

    Substitutes {name} in the template, runs it as a fire-and-forget subprocess.
    No existence check — handles create-or-reattach patterns.
    Returns 200 with {"name": "..."} regardless of outcome.
    """
    name = payload.validated_name
    template = load_settings().get(
        "new_session_template", "tmux new-session -d -s {name}"
    )
    command = template.replace("{name}", name)
    try:
        subprocess.Popen(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        _log.warning("Failed to execute new session command: %s", command)
    return {"name": name}
```

**Note:** The Pydantic validator on `name` gives us automatic 422 for empty/missing `name`. We need to add a custom validator. Actually, Pydantic v2 with `str` type accepts empty strings, so use a `field_validator`:

```python
from pydantic import BaseModel, field_validator

class CreateSessionPayload(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()
```

**Step 4: Run tests to verify they pass**

```bash
cd muxplex && python -m pytest muxplex/tests/test_api.py -k "create_session" -v
```

Expected: All 4 create_session tests pass.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: add POST /api/sessions endpoint for new session creation"
```

---

### Task 6: Settings modal HTML + CSS skeleton

**Files:**
- Modify: `muxplex/frontend/index.html`
- Modify: `muxplex/frontend/style.css`

**Step 1: Add gear icon and `+` button to the overview header**

In `muxplex/frontend/index.html`, replace the overview header (lines 21-24):

```html
    <header class="app-header">
      <h1 class="app-wordmark"><img src="/wordmark-on-dark.svg" alt="muxplex" height="24" /></h1>
      <div class="header-actions">
        <button id="new-session-btn" class="header-btn" aria-label="New session" title="New session">+</button>
        <button id="settings-btn" class="header-btn" aria-label="Settings" title="Settings">&#9881;</button>
        <span id="connection-status"></span>
      </div>
    </header>
```

**Step 2: Add the same buttons to the expanded header**

Update the expanded header to include the gear icon (after `expanded-session-name`):

```html
    <header class="expanded-header">
      <button id="back-btn" class="back-btn" aria-label="Back">&#8592;</button>
      <button id="sidebar-toggle-btn" class="sidebar-toggle-btn" aria-label="Toggle session list">&#9776;</button>
      <span id="expanded-session-name" class="expanded-session-name"></span>
      <button id="settings-btn-expanded" class="header-btn" aria-label="Settings" title="Settings">&#9881;</button>
    </header>
```

**Step 3: Add the settings dialog HTML**

Add after the toast element (before the `<!-- ── Scripts -->` comment):

```html
  <!-- ── Settings dialog ──────────────────────────────────────── -->
  <div id="settings-backdrop" class="settings-backdrop hidden"></div>
  <dialog id="settings-dialog" class="settings-dialog">
    <div class="settings-layout">
      <nav class="settings-tabs" role="tablist">
        <button class="settings-tab settings-tab--active" data-tab="display" role="tab" aria-selected="true">Display</button>
        <button class="settings-tab" data-tab="sessions" role="tab" aria-selected="false">Sessions</button>
        <button class="settings-tab" data-tab="notifications" role="tab" aria-selected="false">Notifications</button>
        <button class="settings-tab" data-tab="new-session" role="tab" aria-selected="false">New Session</button>
      </nav>
      <div class="settings-content">
        <!-- Display tab -->
        <div id="settings-panel-display" class="settings-panel" role="tabpanel" data-tab="display">
          <h3 class="settings-panel-title">Display</h3>
          <label class="settings-field">
            <span class="settings-label">Font size</span>
            <select id="setting-font-size" class="settings-select">
              <option value="11">11px</option>
              <option value="12">12px</option>
              <option value="13">13px</option>
              <option value="14" selected>14px</option>
              <option value="16">16px</option>
            </select>
          </label>
          <label class="settings-field">
            <span class="settings-label">Hover preview delay</span>
            <select id="setting-hover-delay" class="settings-select">
              <option value="0">Off</option>
              <option value="1000">1s</option>
              <option value="1500" selected>1.5s</option>
              <option value="2000">2s</option>
              <option value="3000">3s</option>
            </select>
          </label>
          <label class="settings-field">
            <span class="settings-label">Grid columns</span>
            <select id="setting-grid-columns" class="settings-select">
              <option value="auto" selected>Auto</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
            </select>
          </label>
        </div>
        <!-- Sessions tab (Phase 2) -->
        <div id="settings-panel-sessions" class="settings-panel hidden" role="tabpanel" data-tab="sessions"></div>
        <!-- Notifications tab (Phase 2) -->
        <div id="settings-panel-notifications" class="settings-panel hidden" role="tabpanel" data-tab="notifications"></div>
        <!-- New Session tab (Phase 2) -->
        <div id="settings-panel-new-session" class="settings-panel hidden" role="tabpanel" data-tab="new-session"></div>
      </div>
    </div>
  </dialog>
```

**Step 4: Add settings CSS**

Append to `muxplex/frontend/style.css` (at the bottom, before any final closing comments):

```css
/* ============================================================
   Header action buttons (+ and gear)
   ============================================================ */

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-dim);
  font-size: 16px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: color var(--t-fast), border-color var(--t-fast);
}

.header-btn:hover {
  color: var(--text);
  border-color: var(--text-muted);
}

/* ============================================================
   Settings dialog
   ============================================================ */

.settings-backdrop {
  position: fixed;
  inset: 0;
  background: var(--bg-overlay);
  backdrop-filter: blur(2px);
  z-index: 299;
}

.settings-dialog {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(600px, 90vw);
  height: min(480px, 80vh);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  padding: 0;
  overflow: hidden;
  z-index: 300;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5);
}

.settings-dialog::backdrop {
  background: transparent;  /* we use our own backdrop for blur */
}

.settings-layout {
  display: flex;
  height: 100%;
}

.settings-tabs {
  width: 140px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 16px 0;
  border-right: 1px solid var(--border);
  background: var(--bg);
}

.settings-tab {
  background: none;
  border: none;
  border-left: 2px solid transparent;
  color: var(--text-muted);
  font-size: 13px;
  font-family: var(--font-ui);
  padding: 8px 16px;
  text-align: left;
  cursor: pointer;
  transition: color var(--t-fast);
}

.settings-tab:hover {
  color: var(--text);
}

.settings-tab--active {
  color: var(--accent);
  border-left-color: var(--accent);
}

.settings-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.settings-panel-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 20px;
  color: var(--text);
}

.settings-field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-subtle);
}

.settings-label {
  font-size: 13px;
  color: var(--text);
}

.settings-select {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text);
  font-size: 13px;
  font-family: var(--font-ui);
  padding: 6px 10px;
  cursor: pointer;
}

.settings-select:focus {
  outline: 1px solid var(--accent);
  border-color: var(--accent);
}

/* Mobile: bottom half-sheet */
@media (max-width: 599px) {
  .settings-dialog {
    top: auto;
    bottom: 0;
    left: 0;
    right: 0;
    transform: none;
    width: 100%;
    height: 85vh;
    border-radius: 12px 12px 0 0;
  }

  .settings-layout {
    flex-direction: column;
  }

  .settings-tabs {
    width: 100%;
    flex-direction: row;
    border-right: none;
    border-bottom: 1px solid var(--border);
    padding: 0;
    overflow-x: auto;
  }

  .settings-tab {
    border-left: none;
    border-bottom: 2px solid transparent;
    padding: 12px 16px;
    white-space: nowrap;
    min-height: 48px;
  }

  .settings-tab--active {
    border-bottom-color: var(--accent);
    border-left-color: transparent;
  }
}
```

**Step 5: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/test_frontend_html.py muxplex/tests/test_frontend_css.py -v
```

Expected: All tests pass (new elements don't conflict with existing tests).

**Step 6: Commit**

```bash
git add -A && git commit -m "feat: add settings dialog HTML structure and CSS"
```

---

### Task 7: Settings JS infrastructure — open/close, tabs, localStorage

**Files:**
- Modify: `muxplex/frontend/app.js`

**Step 1: Add settings state variables**

In `muxplex/frontend/app.js`, add after the existing app state block (after `let _previewSessionName = null;`):

```javascript
// ─── Settings state ──────────────────────────────────────────────────────────
let _settingsOpen = false;
const DISPLAY_SETTINGS_KEY = 'muxplex.display';
const DISPLAY_DEFAULTS = {
  fontSize: 14,
  hoverPreviewDelay: 1500,
  gridColumns: 'auto',
  bellSound: false,
  notificationPermission: 'default',
};
```

**Step 2: Add settings open/close functions**

Add after the existing `closeSession` function:

```javascript
// ─── Settings dialog ─────────────────────────────────────────────────────────

/**
 * Load display settings from localStorage, merged with defaults.
 * @returns {object}
 */
function loadDisplaySettings() {
  try {
    const raw = localStorage.getItem(DISPLAY_SETTINGS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return Object.assign({}, DISPLAY_DEFAULTS, parsed);
    }
  } catch (_) { /* blocked or corrupt — use defaults */ }
  return Object.assign({}, DISPLAY_DEFAULTS);
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
 * Open the settings dialog. Loads current values into form controls.
 */
function openSettings() {
  _settingsOpen = true;
  const dialog = $('settings-dialog');
  const backdrop = $('settings-backdrop');
  if (dialog && typeof dialog.showModal === 'function') {
    dialog.showModal();
  }
  if (backdrop) backdrop.classList.remove('hidden');

  // Load current display settings into form controls
  const ds = loadDisplaySettings();
  var fontSel = $('setting-font-size');
  var delaySel = $('setting-hover-delay');
  var colsSel = $('setting-grid-columns');
  if (fontSel) fontSel.value = String(ds.fontSize);
  if (delaySel) delaySel.value = String(ds.hoverPreviewDelay);
  if (colsSel) colsSel.value = String(ds.gridColumns);
}

/**
 * Close the settings dialog.
 */
function closeSettings() {
  _settingsOpen = false;
  const dialog = $('settings-dialog');
  const backdrop = $('settings-backdrop');
  if (dialog && typeof dialog.close === 'function') {
    try { dialog.close(); } catch (_) {}
  }
  if (backdrop) backdrop.classList.add('hidden');
}

/**
 * Switch to a settings tab by name.
 * @param {string} tabName - one of 'display', 'sessions', 'notifications', 'new-session'
 */
function switchSettingsTab(tabName) {
  // Update tab buttons
  document.querySelectorAll('.settings-tab').forEach(function(btn) {
    if (btn.dataset.tab === tabName) {
      btn.classList.add('settings-tab--active');
      btn.setAttribute('aria-selected', 'true');
    } else {
      btn.classList.remove('settings-tab--active');
      btn.setAttribute('aria-selected', 'false');
    }
  });
  // Show/hide panels
  document.querySelectorAll('.settings-panel').forEach(function(panel) {
    if (panel.dataset.tab === tabName) {
      panel.classList.remove('hidden');
    } else {
      panel.classList.add('hidden');
    }
  });
}
```

**Step 3: Update handleGlobalKeydown for settings**

Replace the `handleGlobalKeydown` function to add `,` shortcut and Escape for settings:

```javascript
/**
 * Global keydown handler.
 * Comma opens settings. Escape closes settings or returns to grid.
 * @param {KeyboardEvent} e
 */
function handleGlobalKeydown(e) {
  // Settings dialog
  if (_settingsOpen) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeSettings();
    }
    return;  // don't process other shortcuts while settings is open
  }

  // Comma opens settings (unless typing in an input)
  if (e.key === ',' && !e.ctrlKey && !e.metaKey) {
    var tag = e.target && e.target.tagName;
    if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') {
      e.preventDefault();
      openSettings();
      return;
    }
  }

  if (_viewMode === 'fullscreen') {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeSession();
    }
  }
}
```

**Step 4: Wire up event listeners in bindStaticEventListeners**

Add to `bindStaticEventListeners` (after the existing bindings, before the hover preview section):

```javascript
  // Settings
  on($('settings-btn'), 'click', openSettings);
  on($('settings-btn-expanded'), 'click', openSettings);
  on($('settings-backdrop'), 'click', closeSettings);

  // Settings dialog: close on Escape via dialog's built-in cancel event
  var settingsDialog = $('settings-dialog');
  if (settingsDialog) {
    settingsDialog.addEventListener('cancel', function(e) {
      e.preventDefault();
      closeSettings();
    });
  }

  // Settings tab switching
  document.querySelectorAll('.settings-tab').forEach(function(tab) {
    on(tab, 'click', function() {
      switchSettingsTab(tab.dataset.tab);
    });
  });
```

**Step 5: Add settings to module.exports**

In the `module.exports` block, add:

```javascript
    // Settings
    loadDisplaySettings,
    saveDisplaySettings,
    openSettings,
    closeSettings,
    switchSettingsTab,
```

**Step 6: Run tests**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 7: Commit**

```bash
git add -A && git commit -m "feat: add settings dialog open/close, tab switching, localStorage management"
```

---

### Task 8: Wire Display tab — font size, hover delay, grid columns

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/style.css`

**Step 1: Add display settings change handlers**

In `muxplex/frontend/app.js`, add after the `switchSettingsTab` function:

```javascript
/**
 * Apply display settings to the live UI.
 * Called on page load and whenever a display setting changes.
 * @param {object} ds - display settings object
 */
function applyDisplaySettings(ds) {
  // Font size: update CSS custom property (grid previews use it)
  document.documentElement.style.setProperty('--preview-font-size', ds.fontSize + 'px');

  // Grid columns: set CSS custom property on session-grid
  var grid = $('session-grid');
  if (grid) {
    if (ds.gridColumns === 'auto') {
      grid.style.removeProperty('grid-template-columns');
    } else {
      grid.style.gridTemplateColumns = 'repeat(' + ds.gridColumns + ', 1fr)';
    }
  }
}

/**
 * Handle changes to display setting <select> elements.
 * Reads the value, updates localStorage, and applies the change immediately.
 */
function onDisplaySettingChange() {
  var ds = loadDisplaySettings();

  var fontSel = $('setting-font-size');
  var delaySel = $('setting-hover-delay');
  var colsSel = $('setting-grid-columns');

  if (fontSel) ds.fontSize = parseInt(fontSel.value, 10);
  if (delaySel) ds.hoverPreviewDelay = parseInt(delaySel.value, 10);
  if (colsSel) ds.gridColumns = colsSel.value === 'auto' ? 'auto' : parseInt(colsSel.value, 10);

  saveDisplaySettings(ds);
  applyDisplaySettings(ds);
}
```

**Step 2: Update the hover preview timer to use settings**

In `bindStaticEventListeners`, update the hover preview mouseenter handlers. Replace the hardcoded `1500` in both the grid and sidebar hover handlers with a dynamic lookup:

Find these two occurrences in `bindStaticEventListeners`:
```javascript
      _previewTimer = setTimeout(function () { showPreview(name); }, 1500);
```

Replace each with:
```javascript
      var delay = loadDisplaySettings().hoverPreviewDelay;
      if (delay > 0) {
        _previewTimer = setTimeout(function () { showPreview(name); }, delay);
      }
```

This makes "Off" (value 0) disable preview entirely, and other values use the configured delay.

**Step 3: Bind change listeners for Display tab controls in bindStaticEventListeners**

Add to `bindStaticEventListeners` (after the settings tab switching code):

```javascript
  // Display settings change handlers
  on($('setting-font-size'), 'change', onDisplaySettingChange);
  on($('setting-hover-delay'), 'change', onDisplaySettingChange);
  on($('setting-grid-columns'), 'change', onDisplaySettingChange);
```

**Step 4: Apply display settings on page load**

In the `DOMContentLoaded` handler, add `applyDisplaySettings(loadDisplaySettings())` right after `initDeviceId()`:

```javascript
document.addEventListener('DOMContentLoaded', () => {
  initDeviceId();
  applyDisplaySettings(loadDisplaySettings());  // <-- add this line
  document.addEventListener('keydown', trackInteraction);
  // ... rest unchanged
});
```

**Step 5: Add grid column override CSS support**

In `muxplex/frontend/style.css`, find the `.session-grid` rule (it should contain `display: grid` and `grid-template-columns`). Verify it uses `auto-fill` or `auto-fit` — the inline `style.gridTemplateColumns` from JS will override it when set. No CSS change needed if the existing grid uses the standard `grid-template-columns: repeat(auto-fill, minmax(...))` pattern.

**Step 6: Add exports**

Add to `module.exports`:

```javascript
    applyDisplaySettings,
    onDisplaySettingChange,
```

**Step 7: Run full test suite**

```bash
cd muxplex && python -m pytest muxplex/tests/ -v
```

Expected: All tests pass.

**Step 8: Commit**

```bash
git add -A && git commit -m "feat: wire Display tab — font size, hover delay, grid columns with immediate apply"
```

---

## Phase 1 Complete

After completing all 8 tasks, Phase 1 delivers:

1. **Command palette fully removed** — no dead code remaining
2. **Server-side settings module** — `settings.py` with load/save/patch
3. **Three new API endpoints** — `GET/PATCH /api/settings`, `POST /api/sessions`
4. **Settings modal** — centered dialog (desktop) / bottom sheet (mobile) with tab navigation
5. **Display tab functional** — font size, hover delay, grid columns all apply immediately via localStorage
6. **Gear icon and + button** in both headers (ready for Phase 2 wiring)

Proceed to [Phase 2](./2026-03-29-settings-phase2.md) for the remaining tabs and new session UI flow.
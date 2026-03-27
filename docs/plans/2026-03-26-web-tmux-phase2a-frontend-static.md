# Web-Tmux Dashboard Phase 2a — Frontend Static Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add static file serving to the coordinator and create the complete HTML structure and CSS for the tmux web dashboard — all views, components, and responsive behavior — with no JavaScript behavior.

**Architecture:** FastAPI `StaticFiles` mount serves the `frontend/` directory from the project root. A vanilla HTML shell contains all views (overview grid, expanded terminal, command palette, bottom sheet) as pre-existing DOM elements that Phase 2b JavaScript will show/hide. CSS handles all visual structure, responsiveness, animations, and theming across 8 incremental chunks. Phase 2b will replace the stub `app.js` and `terminal.js` with full implementations.

**Tech Stack:** Python FastAPI `StaticFiles` + `aiofiles`, vanilla HTML5/CSS3 (no framework, no build step), xterm.js 5.3.0 (CDN loaded in HTML for Phase 2b use), `pytest` + `httpx` (already installed) for static-serving and CSS structure tests.

---

## Context for the Implementer

You are building Phase 2a of a browser-based tmux session dashboard. Phase 1 (already complete) built the FastAPI backend at `coordinator/`. It has 92 passing tests. Your job is to:

1. Add `aiofiles` (required by FastAPI's static file server)
2. Create the `frontend/` directory with stub JS files + complete HTML and CSS
3. Wire `StaticFiles` into `main.py` so the coordinator serves the frontend
4. Write complete, production-quality HTML and CSS that Phase 2b can immediately drop JavaScript into

**Key constraint:** FastAPI's `StaticFiles` mount checks that the directory exists when `coordinator.main` is imported. This means `frontend/` **must exist on disk before** you add the `StaticFiles` line to `main.py` (Task 2 before Task 3 — never swap the order).

**Working directory for all commands:** `/home/bkrabach/dev/web-tmux`

**Run unit tests with:**
```bash
pytest -q
```
Expected baseline (before you start): `92 passed in ~0.2s`

---

## Task 1: Add aiofiles to requirements.txt and install it

**Files:**
- Modify: `requirements.txt`

### Step 1: Read the file before editing
```bash
cat requirements.txt
```
Expected output:
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

### Step 2: Add aiofiles
Open `requirements.txt` and add one line so the file reads:
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
aiofiles>=23.0
```

### Step 3: Install it
```bash
pip install aiofiles
```
Expected: `Successfully installed aiofiles-...` (or `Requirement already satisfied`)

### Step 4: Verify the import works
```bash
python -c "import aiofiles; print('aiofiles ok')"
```
Expected output: `aiofiles ok`

### Step 5: Confirm tests still pass
```bash
pytest -q
```
Expected: `92 passed`

### Step 6: Commit
```bash
git add requirements.txt
git commit -m "feat: add aiofiles>=23.0 dependency for StaticFiles serving"
```

---

## Task 2: Create frontend/ stub files

The `frontend/` directory and its stub files must exist before Task 3 adds the `StaticFiles` mount to `main.py`. FastAPI raises `RuntimeError` at import time if the directory is missing.

**Files:**
- Create: `frontend/index.html` (minimal stub)
- Create: `frontend/style.css` (empty stub)
- Create: `frontend/app.js` (stub — Phase 2b fills this)
- Create: `frontend/terminal.js` (stub — Phase 2b fills this)

### Step 1: Create the directory
```bash
mkdir -p frontend
```

### Step 2: Create frontend/index.html (stub)
Create `frontend/index.html` with this content:
```html
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>tmux web</title></head><body></body></html>
```

### Step 3: Create frontend/style.css (stub)
Create `frontend/style.css` with this content:
```css
/* Phase 2a stub — CSS built incrementally in Tasks 6–13 */
```

### Step 4: Create frontend/app.js (stub)
Create `frontend/app.js` with this content:
```javascript
// Phase 2b implementation — placeholder to allow static serving tests to pass
```

### Step 5: Create frontend/terminal.js (stub)
Create `frontend/terminal.js` with this content:
```javascript
// Phase 2b implementation — placeholder to allow static serving tests to pass
```

### Step 6: Verify the directory looks right
```bash
ls -la frontend/
```
Expected output (4 files):
```
-rw-r--r-- app.js
-rw-r--r-- index.html
-rw-r--r-- style.css
-rw-r--r-- terminal.js
```

### Step 7: Confirm tests still pass
```bash
pytest -q
```
Expected: `92 passed`

### Step 8: Commit
```bash
git add frontend/
git commit -m "feat: add frontend/ stub files (index.html, style.css, app.js, terminal.js)"
```

---

## Task 3: Add StaticFiles mount to coordinator/main.py and 3 API tests

**Files:**
- Modify: `coordinator/main.py`
- Modify: `coordinator/tests/test_api.py`

### Step 1: Write the 3 failing tests first

Open `coordinator/tests/test_api.py`. The file already has the `client` fixture at line 50. Add the following 3 test functions at the **very end** of the file (after line 472):

```python
# ---------------------------------------------------------------------------
# Static file serving (StaticFiles mount)
# ---------------------------------------------------------------------------


def test_root_serves_html(client):
    """GET / serves index.html with text/html content-type."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_style_css_served(client):
    """GET /style.css returns CSS content."""
    response = client.get("/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_api_routes_not_shadowed(client):
    """API routes still work after StaticFiles mount — first-match-wins ordering."""
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

### Step 2: Run only the new tests to verify they fail

```bash
pytest coordinator/tests/test_api.py::test_root_serves_html coordinator/tests/test_api.py::test_style_css_served coordinator/tests/test_api.py::test_api_routes_not_shadowed -v
```

Expected: All 3 tests **FAIL** — `GET /` currently returns 404 because there is no static file route yet. The failure looks like:
```
FAILED ... test_root_serves_html — assert 404 == 200
```

### Step 3: Add the StaticFiles mount to coordinator/main.py

Open `coordinator/main.py`. You need to make two edits:

**Edit A — add imports** at the top of the file. After the existing imports block (after line 17, `from fastapi import FastAPI, HTTPException`), add two new imports. The updated imports section should look like:

```python
import asyncio
import contextlib
import logging
import os
import pathlib
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
```

**Edit B — add frontend directory path and mount** at the very bottom of the file (after line 285, the last line of the `heartbeat` route). Add:

```python


# ---------------------------------------------------------------------------
# Static files — MUST come AFTER all API routes (first-match-wins in FastAPI)
# ---------------------------------------------------------------------------

_FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"

# html=True makes GET / serve index.html automatically
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
```

### Step 4: Run the new tests to verify they pass

```bash
pytest coordinator/tests/test_api.py::test_root_serves_html coordinator/tests/test_api.py::test_style_css_served coordinator/tests/test_api.py::test_api_routes_not_shadowed -v
```

Expected: All 3 tests **PASS**.

### Step 5: Run the full test suite to confirm nothing broke

```bash
pytest -q
```

Expected: `95 passed` (92 original + 3 new)

### Step 6: Commit

```bash
git add coordinator/main.py coordinator/tests/test_api.py
git commit -m "feat: add StaticFiles mount and 3 static-serving tests"
```

---

## Task 4: Create frontend/manifest.json

**Files:**
- Create: `frontend/manifest.json`

No test needed for this task — manifest validity is validated by the browser at PWA install time. The file structure is verified when index.html references it (Task 5).

### Step 1: Create frontend/manifest.json

Create `frontend/manifest.json` with this content exactly:

```json
{
  "name": "tmux web",
  "short_name": "tmux web",
  "description": "Browser-based tmux session dashboard",
  "start_url": "/",
  "display": "standalone",
  "orientation": "any",
  "background_color": "#0a0e14",
  "theme_color": "#0a0e14",
  "icons": [
    {
      "src": "/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    }
  ]
}
```

Note: The icon files (`/icons/icon-192.png`, `/icons/icon-512.png`) are deferred to Phase 3. The manifest is valid without them for development — browsers will simply show no icon.

### Step 2: Verify it is valid JSON

```bash
python -c "import json; json.load(open('frontend/manifest.json')); print('manifest.json valid')"
```

Expected: `manifest.json valid`

### Step 3: Verify it is served correctly

```bash
uvicorn coordinator.main:app --host 127.0.0.1 --port 8099 &
sleep 1
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8099/manifest.json
kill %1
```

Expected HTTP status: `200`

### Step 4: Commit

```bash
git add frontend/manifest.json
git commit -m "feat: add PWA manifest.json"
```

---

## Task 5: Complete frontend/index.html — full HTML shell

Replace the stub `index.html` with the complete HTML that contains every view and DOM element Phase 2b JavaScript will manipulate.

**Files:**
- Modify: `frontend/index.html`
- Create: `coordinator/tests/test_frontend_html.py`

### Step 1: Write the failing HTML tests first

Create `coordinator/tests/test_frontend_html.py`:

```python
"""
Smoke tests for frontend/index.html — verifies all DOM elements and
meta tags required by Phase 2b JavaScript are present.
No browser required — pure file content assertions.
"""

import pathlib

HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "frontend" / "index.html"


def read_html() -> str:
    return HTML_PATH.read_text()


def test_html_pwa_meta():
    """index.html has required PWA meta tags."""
    html = read_html()
    assert 'name="apple-mobile-web-app-capable"' in html
    assert 'rel="manifest"' in html
    assert "theme-color" in html
    assert 'name="apple-mobile-web-app-status-bar-style"' in html


def test_html_viewport_suppresses_pinch_zoom():
    """Viewport meta suppresses pinch-zoom (required for terminal UX)."""
    html = read_html()
    assert "maximum-scale=1.0" in html
    assert "user-scalable=no" in html


def test_html_required_views():
    """index.html contains both top-level views and the session grid."""
    html = read_html()
    assert 'id="view-overview"' in html
    assert 'id="view-expanded"' in html
    assert 'id="session-grid"' in html
    assert 'id="terminal-container"' in html
    assert 'id="empty-state"' in html


def test_html_expanded_view_elements():
    """Expanded view has back button, session name, and palette trigger."""
    html = read_html()
    assert 'id="back-btn"' in html
    assert 'id="expanded-session-name"' in html
    assert 'id="palette-trigger"' in html
    assert 'id="reconnect-overlay"' in html


def test_html_command_palette():
    """Command palette overlay has input and list."""
    html = read_html()
    assert 'id="command-palette"' in html
    assert 'id="palette-input"' in html
    assert 'id="palette-list"' in html
    assert 'id="palette-backdrop"' in html


def test_html_bottom_sheet():
    """Bottom sheet and session pill are present."""
    html = read_html()
    assert 'id="bottom-sheet"' in html
    assert 'id="sheet-list"' in html
    assert 'id="sheet-backdrop"' in html
    assert 'id="session-pill"' in html
    assert 'id="session-pill-label"' in html
    assert 'id="session-pill-bell"' in html


def test_html_toast():
    """Toast notification element is present."""
    html = read_html()
    assert 'id="toast"' in html
    assert 'aria-live="polite"' in html


def test_html_scripts():
    """app.js, terminal.js, and xterm.js CDN scripts are loaded."""
    html = read_html()
    assert 'src="/app.js"' in html
    assert 'src="/terminal.js"' in html
    assert "xterm" in html  # xterm.js CDN


def test_html_xterm_css():
    """xterm.js CSS is loaded from CDN."""
    html = read_html()
    assert "xterm.css" in html


def test_html_style_css():
    """Local style.css is linked."""
    html = read_html()
    assert 'href="/style.css"' in html
```

### Step 2: Run the new tests to verify they fail on the stub

```bash
pytest coordinator/tests/test_frontend_html.py -v
```

Expected: All tests **FAIL** — the stub `index.html` is a bare minimum HTML5 skeleton with none of the required elements.

### Step 3: Replace frontend/index.html with the complete HTML

Overwrite `frontend/index.html` with this complete content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <meta name="theme-color" content="#0a0e14">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <link rel="manifest" href="/manifest.json">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css">
  <link rel="stylesheet" href="/style.css">
  <title>tmux web</title>
</head>
<body>

  <!-- ── Overview (default view) ── -->
  <div id="view-overview" class="view view--active">
    <header class="app-header">
      <h1 class="app-title">tmux web</h1>
      <span id="connection-status" class="connection-status" aria-live="polite"></span>
    </header>
    <div id="session-grid" class="session-grid" role="list"></div>
    <div id="empty-state" class="empty-state hidden">
      No active tmux sessions — will update automatically
    </div>
  </div>

  <!-- ── Expanded terminal view ── -->
  <div id="view-expanded" class="view hidden">
    <div class="expanded-header">
      <button id="back-btn" class="back-btn" aria-label="Return to overview">←</button>
      <span id="expanded-session-name" class="expanded-session-name"></span>
      <button id="palette-trigger" class="palette-trigger"
              aria-label="Switch session (Ctrl+K)" title="Switch session (Ctrl+K)">⌘K</button>
    </div>
    <div id="terminal-container" class="terminal-container"></div>
    <!-- Reconnecting overlay — shown by JS when WebSocket drops -->
    <div id="reconnect-overlay" class="reconnect-overlay hidden">
      <span class="reconnect-overlay__text">Reconnecting…</span>
    </div>
  </div>

  <!-- ── Command palette (desktop) ── -->
  <div id="command-palette" class="command-palette hidden"
       role="dialog" aria-modal="true" aria-label="Switch session">
    <div class="command-palette__backdrop" id="palette-backdrop"></div>
    <div class="command-palette__dialog">
      <input id="palette-input" class="command-palette__input" type="text"
             placeholder="Jump to session…" autocomplete="off" spellcheck="false">
      <ul id="palette-list" class="command-palette__list" role="listbox"
          aria-label="Sessions"></ul>
    </div>
  </div>

  <!-- ── Bottom sheet (mobile session switcher) ── -->
  <div id="bottom-sheet" class="bottom-sheet hidden"
       role="dialog" aria-modal="true" aria-label="Switch session">
    <div class="bottom-sheet__backdrop" id="sheet-backdrop"></div>
    <div class="bottom-sheet__panel">
      <div class="bottom-sheet__handle" aria-hidden="true"></div>
      <ul id="sheet-list" class="bottom-sheet__list" role="listbox"
          aria-label="Sessions"></ul>
    </div>
  </div>

  <!-- ── Floating session pill (mobile, shown in expanded view) ── -->
  <button id="session-pill" class="session-pill hidden" aria-label="Switch session">
    <span id="session-pill-label" class="session-pill__label"></span>
    <span id="session-pill-bell" class="session-pill__bell hidden" aria-hidden="true">🔔</span>
  </button>

  <!-- ── Toast notification ── -->
  <div id="toast" class="toast hidden"
       role="status" aria-live="polite" aria-atomic="true"></div>

  <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
  <script src="/app.js" defer></script>
  <script src="/terminal.js" defer></script>
</body>
</html>
```

### Step 4: Run the HTML tests to verify they pass

```bash
pytest coordinator/tests/test_frontend_html.py -v
```

Expected: All 10 tests **PASS**.

### Step 5: Run the full test suite to confirm nothing broke

```bash
pytest -q
```

Expected: `105 passed` (95 from Task 3 + 10 new HTML tests)

### Step 6: Commit

```bash
git add frontend/index.html coordinator/tests/test_frontend_html.py
git commit -m "feat: add complete index.html dashboard shell with all views and components"
```

---

## Task 6: style.css — Design tokens, dark base, typography

**Files:**
- Create: `coordinator/tests/test_frontend_css.py`
- Modify: `frontend/style.css`

### Step 1: Write the failing CSS token test

Create `coordinator/tests/test_frontend_css.py`:

```python
"""
Smoke tests for frontend/style.css — verifies CSS structure without a browser.
Each test function checks that specific selectors and properties are present
after their corresponding CSS task has been implemented.
"""

import pathlib

CSS_PATH = pathlib.Path(__file__).parent.parent.parent / "frontend" / "style.css"


def read_css() -> str:
    return CSS_PATH.read_text()


# ── Task 6: Design tokens ─────────────────────────────────────────────────────


def test_css_design_tokens():
    """CSS file defines all required custom properties."""
    css = read_css()
    assert "--bg:" in css
    assert "--bell:" in css
    assert "--font-mono:" in css
    assert "--tile-height:" in css
    assert "--t-zoom:" in css
```

### Step 2: Run the test to verify it fails on the stub

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_design_tokens -v
```

Expected: **FAIL** — the stub CSS contains none of these custom properties.

### Step 3: Replace frontend/style.css with the design tokens + dark base

Overwrite `frontend/style.css` with:

```css
/* ── Design Tokens ── */
:root {
  /* Background */
  --bg:            #0a0e14;
  --bg-secondary:  #0d1117;
  --bg-tile:       #0f1419;
  --bg-header:     #161b22;
  --bg-overlay:    rgba(10, 14, 20, 0.85);

  /* Text */
  --text:          #c9d1d9;
  --text-dim:      #6e7681;
  --text-muted:    #8b949e;

  /* Borders */
  --border:        #21262d;
  --border-subtle: #161b22;

  /* Accent */
  --accent:        #58a6ff;
  --accent-dim:    rgba(88, 166, 255, 0.15);

  /* Bell (amber) */
  --bell:          #E8A040;
  --bell-glow:     rgba(232, 160, 64, 0.25);
  --bell-border:   rgba(232, 160, 64, 0.6);

  /* Status */
  --ok:            #3fb950;
  --warn:          #d29922;
  --err:           #f85149;

  /* Layout */
  --tile-height:     300px;
  --tile-min-width:  360px;
  --grid-gap:        8px;
  --grid-padding:    16px;
  --header-height:   44px;

  /* Transitions */
  --t-zoom:   250ms ease-in-out;
  --t-fast:   150ms ease;
  --t-fade:   200ms ease;

  /* Fonts */
  --font-ui:   system-ui, -apple-system, 'Segoe UI', sans-serif;
  --font-mono: 'SF Mono', 'Fira Code', 'Consolas', 'Menlo', monospace;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-ui);
  font-size: 14px;
  overflow: hidden;
}

.hidden { display: none !important; }
```

### Step 4: Run the test to verify it passes

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_design_tokens -v
```

Expected: **PASS**.

### Step 5: Run the full test suite

```bash
pytest -q
```

Expected: `106 passed`

### Step 6: Commit

```bash
git add frontend/style.css coordinator/tests/test_frontend_css.py
git commit -m "feat: add style.css — design tokens, dark theme, typography"
```

---

## Task 7: style.css — Session grid, tile, bell pulse

**Files:**
- Modify: `frontend/style.css` (append)
- Modify: `coordinator/tests/test_frontend_css.py` (add 3 test functions)

### Step 1: Add 3 failing tests to test_frontend_css.py

Open `coordinator/tests/test_frontend_css.py` and append at the end:

```python
# ── Task 7: Session grid + tile + bell pulse ──────────────────────────────────


def test_css_session_grid():
    """Session grid uses auto-fill with minmax for fluid columns."""
    css = read_css()
    assert "auto-fill" in css
    assert "minmax" in css


def test_css_tile_height():
    """Session tile has fixed height using --tile-height token."""
    css = read_css()
    assert ".session-tile" in css
    assert "var(--tile-height)" in css


def test_css_bell_indicator():
    """Bell indicator has amber pulse animation and tile border glow."""
    css = read_css()
    assert "bell-pulse" in css
    assert ".session-tile--bell" in css
    assert ".tile-bell" in css
```

### Step 2: Run the new tests to verify they fail

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_session_grid coordinator/tests/test_frontend_css.py::test_css_tile_height coordinator/tests/test_frontend_css.py::test_css_bell_indicator -v
```

Expected: All 3 **FAIL**.

### Step 3: Append grid + tile CSS to frontend/style.css

Append the following to the end of `frontend/style.css`:

```css

/* ── App layout ── */
.view { height: 100vh; overflow: hidden; }
.view--active { display: flex; flex-direction: column; }
.view.hidden  { display: none; }

.app-header {
  height: var(--header-height);
  padding: 0 var(--grid-padding);
  background: var(--bg-header);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.app-title { font-size: 15px; font-weight: 600; letter-spacing: 0.02em; }

.connection-status { font-size: 11px; color: var(--text-dim); }

/* ── Session grid ── */
.session-grid {
  flex: 1;
  overflow-y: auto;
  padding: var(--grid-padding);
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--tile-min-width), 1fr));
  gap: var(--grid-gap);
  align-content: start;
}

/* ── Session tile ── */
.session-tile {
  height: var(--tile-height);
  background: var(--bg-tile);
  border: 1px solid var(--border);
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  cursor: pointer;
  overflow: hidden;
  position: relative;
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}

.session-tile:hover,
.session-tile:focus-visible {
  border-color: var(--accent);
  outline: none;
}

/* Tile with bell — amber border glow */
.session-tile--bell {
  border-color: var(--bell-border);
  box-shadow: 0 0 0 1px var(--bell-border), inset 0 0 12px var(--bell-glow);
}

.tile-header {
  height: 32px;
  padding: 0 10px;
  background: var(--bg-header);
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.tile-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 65%;
}

.tile-meta {
  font-size: 11px;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

/* Bell dot — pulsing amber circle in tile header */
.tile-bell {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--bell);
  flex-shrink: 0;
  animation: bell-pulse 1.4s ease-in-out infinite;
}

@keyframes bell-pulse {
  0%, 100% { opacity: 1;   transform: scale(1); }
  50%       { opacity: 0.5; transform: scale(0.8); }
}

/* Tile body — terminal output */
.tile-body {
  flex: 1;
  overflow: hidden;
  position: relative;
}

.tile-pre {
  position: absolute;
  inset: 0;
  padding: 6px 8px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.4;
  color: var(--text);
  white-space: pre;
  overflow: hidden;
  /* Show bottom of content — newest lines appear at the bottom */
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

/* Fade top edge of tile body so content appears to scroll out of view */
.tile-body::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 32px;
  background: linear-gradient(to bottom, var(--bg-tile), transparent);
  pointer-events: none;
  z-index: 1;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-dim);
  font-size: 14px;
}
```

### Step 4: Run the new tests to verify they pass

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_session_grid coordinator/tests/test_frontend_css.py::test_css_tile_height coordinator/tests/test_frontend_css.py::test_css_bell_indicator -v
```

Expected: All 3 **PASS**.

### Step 5: Run the full test suite

```bash
pytest -q
```

Expected: `109 passed`

### Step 6: Commit

```bash
git add frontend/style.css coordinator/tests/test_frontend_css.py
git commit -m "feat: add style.css — session grid, tile, and bell pulse animation"
```

---

## Task 8: style.css — Responsive breakpoints

**Files:**
- Modify: `frontend/style.css` (append)
- Modify: `coordinator/tests/test_frontend_css.py` (add 1 test function)

### Step 1: Add the failing breakpoint test

Append to `coordinator/tests/test_frontend_css.py`:

```python
# ── Task 8: Responsive breakpoints ───────────────────────────────────────────


def test_css_breakpoints():
    """Responsive breakpoints at 599px and 899px are defined."""
    css = read_css()
    assert "599px" in css
    assert "899px" in css
```

### Step 2: Run the test to verify it fails

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_breakpoints -v
```

Expected: **FAIL**.

### Step 3: Append breakpoint CSS to frontend/style.css

```css

/* ── Responsive breakpoints ── */

/* 1200px+: 4+ cols (auto-fill handles this naturally with --tile-min-width: 360px) */

/* 900–1199px: slightly wider tile minimum forces 2 columns at most */
@media (max-width: 1199px) and (min-width: 900px) {
  :root { --tile-min-width: 420px; }
}

/* 600–899px: single-column wide tiles (auto-fill yields 1 col anyway) */
@media (max-width: 899px) and (min-width: 600px) {
  .session-grid {
    grid-template-columns: 1fr;
    padding: 8px;
  }
  .session-tile { height: 200px; }
}

/* < 600px: switch to flex list layout (mobile) */
@media (max-width: 599px) {
  .session-grid {
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: 0;
  }
  .session-tile {
    height: auto;
    border-radius: 0;
    border-left: none;
    border-right: none;
  }
}

/* Landscape hint for mobile terminal */
@media (max-width: 599px) and (orientation: landscape) {
  .session-grid { padding: 0; }
}
```

### Step 4: Run the test to verify it passes

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_breakpoints -v
```

Expected: **PASS**.

### Step 5: Run the full test suite

```bash
pytest -q
```

Expected: `110 passed`

### Step 6: Commit

```bash
git add frontend/style.css coordinator/tests/test_frontend_css.py
git commit -m "feat: add style.css — responsive breakpoints (600/900/1200px)"
```

---

## Task 9: style.css — Zoom-in-place expand/collapse transition

**Files:**
- Modify: `frontend/style.css` (append)
- Modify: `coordinator/tests/test_frontend_css.py` (add 1 test function)

### Step 1: Add the failing zoom transition test

Append to `coordinator/tests/test_frontend_css.py`:

```python
# ── Task 9: Zoom-in-place transition ─────────────────────────────────────────


def test_css_zoom_transition():
    """Zoom-in-place transition classes for tile expand/collapse are defined."""
    css = read_css()
    assert ".session-tile--expanding" in css
    assert "session-tile--expanded" in css
    assert ".session-grid--dimming" in css
```

### Step 2: Run the test to verify it fails

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_zoom_transition -v
```

Expected: **FAIL**.

### Step 3: Append zoom transition CSS to frontend/style.css

```css

/* ── Expanded terminal view ── */
.expanded-header {
  height: var(--header-height);
  padding: 0 12px;
  background: var(--bg-header);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.back-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-muted);
  font-size: 18px;
  width: 36px; height: 36px;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: border-color var(--t-fast), color var(--t-fast);
  flex-shrink: 0;
}
.back-btn:hover { border-color: var(--accent); color: var(--text); }

.expanded-session-name {
  flex: 1;
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.palette-trigger {
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-dim);
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
  flex-shrink: 0;
  transition: border-color var(--t-fast), color var(--t-fast);
}
.palette-trigger:hover { border-color: var(--accent); color: var(--text); }

.terminal-container {
  flex: 1;
  overflow: hidden;
  background: #000;
}

.reconnect-overlay {
  position: absolute;
  inset: var(--header-height) 0 0;
  background: var(--bg-overlay);
  display: flex; align-items: center; justify-content: center;
  z-index: 10;
}
.reconnect-overlay__text { color: var(--text-muted); font-size: 14px; }

/* Expanded view is position: relative so overlay inset works */
#view-expanded { position: relative; }

/* ── Zoom-in-place: tile expands from grid position to fill viewport ── */

/* Phase 1 of animation: tile detaches from grid, starts expanding */
.session-tile--expanding {
  position: fixed;
  z-index: 50;
  border-radius: 4px;
  transition: top var(--t-zoom), left var(--t-zoom),
              width var(--t-zoom), height var(--t-zoom),
              border-radius var(--t-zoom);
}

/* Phase 2: tile fills viewport (JS sets these after rAF) */
.session-tile--expanded {
  top: 0 !important; left: 0 !important;
  width: 100vw !important; height: 100vh !important;
  border-radius: 0;
}

/* Siblings fade while the selected tile is expanding */
.session-grid--dimming .session-tile:not(.session-tile--expanding) {
  opacity: 0;
  transition: opacity var(--t-fade);
}
```

### Step 4: Run the test to verify it passes

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_zoom_transition -v
```

Expected: **PASS**.

### Step 5: Run the full test suite

```bash
pytest -q
```

Expected: `111 passed`

### Step 6: Commit

```bash
git add frontend/style.css coordinator/tests/test_frontend_css.py
git commit -m "feat: add style.css — zoom-in-place expand/collapse transition"
```

---

## Task 10: style.css — Bell count badge, connection status, toast

**Files:**
- Modify: `frontend/style.css` (append)
- Modify: `coordinator/tests/test_frontend_css.py` (add 1 test function)

### Step 1: Add the failing bell count test

Append to `coordinator/tests/test_frontend_css.py`:

```python
# ── Task 10: Bell count badge + connection status + toast ─────────────────────


def test_css_bell_count_and_toast():
    """Bell count badge, connection status states, and toast are defined."""
    css = read_css()
    assert ".tile-bell-count" in css
    assert ".connection-status--ok" in css
    assert ".connection-status--err" in css
    assert ".toast" in css
```

### Step 2: Run the test to verify it fails

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_bell_count_and_toast -v
```

Expected: **FAIL**.

### Step 3: Append badge + status + toast CSS to frontend/style.css

```css

/* ── Bell count badge (shown next to bell dot in tile header) ── */
.tile-bell-count {
  font-size: 10px;
  font-weight: 600;
  color: var(--bell);
  min-width: 16px;
  text-align: right;
}

/* ── Connection status indicator ── */
.connection-status--ok   { color: var(--ok); }
.connection-status--warn { color: var(--warn); }
.connection-status--err  { color: var(--err); }

/* ── Toast notification ── */
.toast {
  position: fixed;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--bg-header);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 16px;
  font-size: 13px;
  color: var(--text-muted);
  z-index: 100;
  pointer-events: none;
  animation: toast-in var(--t-fast) ease;
}
@keyframes toast-in {
  from { opacity: 0; transform: translateX(-50%) translateY(8px); }
  to   { opacity: 1; transform: translateX(-50%) translateY(0); }
}
```

### Step 4: Run the test to verify it passes

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_bell_count_and_toast -v
```

Expected: **PASS**.

### Step 5: Run the full test suite

```bash
pytest -q
```

Expected: `112 passed`

### Step 6: Commit

```bash
git add frontend/style.css coordinator/tests/test_frontend_css.py
git commit -m "feat: add style.css — bell count badge, connection status states, toast"
```

---

## Task 11: style.css — Mobile three-tier priority list

**Files:**
- Modify: `frontend/style.css` (append)
- Modify: `coordinator/tests/test_frontend_css.py` (add 1 test function)

### Step 1: Add the failing mobile tier test

Append to `coordinator/tests/test_frontend_css.py`:

```python
# ── Task 11: Mobile three-tier priority list ──────────────────────────────────


def test_css_mobile_tiers():
    """Mobile three-tier CSS classes (bell/active/idle) are defined."""
    css = read_css()
    assert "session-tile--tier-bell" in css
    assert "session-tile--tier-active" in css
    assert "session-tile--tier-idle" in css
```

### Step 2: Run the test to verify it fails

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_mobile_tiers -v
```

Expected: **FAIL**.

### Step 3: Append mobile tier CSS to frontend/style.css

```css

/* ── Mobile list view — three-tier priority layout (< 600px) ── */
@media (max-width: 599px) {

  /* Tier 1: Bell sessions — expanded with 4–6 line preview */
  .session-tile--tier-bell .tile-body { height: 90px; }
  .session-tile--tier-bell { min-height: 126px; }

  /* Tier 2: Recently active (< 5 min) — 1 line of preview */
  .session-tile--tier-active .tile-body { height: 24px; }
  .session-tile--tier-active { min-height: 60px; }
  .session-tile--tier-active .tile-pre {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Tier 3: Idle — name + timestamp only, no content preview */
  .session-tile--tier-idle .tile-body { display: none; }
  .session-tile--tier-idle { min-height: 44px; }
  .session-tile--tier-idle .tile-header { height: 44px; }
  .session-tile--tier-idle .tile-name { color: var(--text-dim); }
}
```

### Step 4: Run the test to verify it passes

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_mobile_tiers -v
```

Expected: **PASS**.

### Step 5: Run the full test suite

```bash
pytest -q
```

Expected: `113 passed`

### Step 6: Commit

```bash
git add frontend/style.css coordinator/tests/test_frontend_css.py
git commit -m "feat: add style.css — mobile three-tier priority list"
```

---

## Task 12: style.css — Command palette overlay (desktop)

**Files:**
- Modify: `frontend/style.css` (append)
- Modify: `coordinator/tests/test_frontend_css.py` (add 1 test function)

### Step 1: Add the failing command palette test

Append to `coordinator/tests/test_frontend_css.py`:

```python
# ── Task 12: Command palette overlay ─────────────────────────────────────────


def test_css_command_palette():
    """Command palette overlay has dialog, input, and list item classes."""
    css = read_css()
    assert ".command-palette__dialog" in css
    assert ".command-palette__input" in css
    assert ".palette-item" in css
    assert ".palette-item--selected" in css
```

### Step 2: Run the test to verify it fails

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_command_palette -v
```

Expected: **FAIL**.

### Step 3: Append command palette CSS to frontend/style.css

```css

/* ── Command palette (desktop) ── */
.command-palette {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 15vh;
}

.command-palette__backdrop {
  position: absolute;
  inset: 0;
  background: var(--bg-overlay);
  backdrop-filter: blur(2px);
}

.command-palette__dialog {
  position: relative;
  width: min(440px, 90vw);
  background: var(--bg-header);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5);
}

.command-palette__input {
  width: 100%;
  padding: 14px 16px;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  font-size: 14px;
  font-family: var(--font-ui);
  outline: none;
}
.command-palette__input::placeholder { color: var(--text-dim); }

.command-palette__list {
  list-style: none;
  max-height: 320px;
  overflow-y: auto;
}

.palette-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-muted);
  transition: background var(--t-fast);
}
.palette-item:hover,
.palette-item--selected { background: var(--accent-dim); color: var(--text); }

.palette-item__index {
  font-size: 11px;
  color: var(--text-dim);
  width: 16px;
  flex-shrink: 0;
}
.palette-item__name { flex: 1; }
.palette-item__bell { color: var(--bell); font-size: 11px; }
.palette-item__time { font-size: 11px; color: var(--text-dim); }
```

### Step 4: Run the test to verify it passes

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_command_palette -v
```

Expected: **PASS**.

### Step 5: Run the full test suite

```bash
pytest -q
```

Expected: `114 passed`

### Step 6: Commit

```bash
git add frontend/style.css coordinator/tests/test_frontend_css.py
git commit -m "feat: add style.css — command palette overlay"
```

---

## Task 13: style.css — Mobile bottom sheet, floating session pill, reduced motion

This is the final CSS task. It also verifies the entire Phase 2a CSS test suite passes cleanly.

**Files:**
- Modify: `frontend/style.css` (append)
- Modify: `coordinator/tests/test_frontend_css.py` (add 3 test functions)

### Step 1: Add the 3 failing tests

Append to `coordinator/tests/test_frontend_css.py`:

```python
# ── Task 13: Bottom sheet + session pill + reduced motion ─────────────────────


def test_css_bottom_sheet():
    """Bottom sheet panel, handle, and list items are defined."""
    css = read_css()
    assert ".bottom-sheet__panel" in css
    assert ".bottom-sheet__handle" in css
    assert ".sheet-item" in css


def test_css_session_pill():
    """Floating session pill button for mobile expanded view is defined."""
    css = read_css()
    assert ".session-pill" in css
    assert ".session-pill__label" in css


def test_css_reduced_motion():
    """prefers-reduced-motion media query disables all animations."""
    css = read_css()
    assert "prefers-reduced-motion" in css
```

### Step 2: Run the new tests to verify they fail

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_bottom_sheet coordinator/tests/test_frontend_css.py::test_css_session_pill coordinator/tests/test_frontend_css.py::test_css_reduced_motion -v
```

Expected: All 3 **FAIL**.

### Step 3: Append bottom sheet + pill + reduced motion CSS to frontend/style.css

```css

/* ── Bottom sheet (mobile session switcher) ── */
.bottom-sheet {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  align-items: flex-end;
}

.bottom-sheet__backdrop {
  position: absolute;
  inset: 0;
  background: var(--bg-overlay);
}

.bottom-sheet__panel {
  position: relative;
  width: 100%;
  background: var(--bg-header);
  border-top: 1px solid var(--border);
  border-radius: 12px 12px 0 0;
  max-height: 70vh;
  overflow-y: auto;
  animation: sheet-up var(--t-zoom) ease;
}
@keyframes sheet-up {
  from { transform: translateY(100%); }
  to   { transform: translateY(0); }
}

/* Drag handle visual cue */
.bottom-sheet__handle {
  width: 36px; height: 4px;
  background: var(--border);
  border-radius: 2px;
  margin: 10px auto 6px;
}

.bottom-sheet__list { list-style: none; padding-bottom: 8px; }

.sheet-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  height: 56px;
  cursor: pointer;
  font-size: 14px;
  color: var(--text);
  border-bottom: 1px solid var(--border-subtle);
  transition: background var(--t-fast);
}
.sheet-item:hover { background: var(--accent-dim); }
.sheet-item__name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sheet-item__bell { color: var(--bell); flex-shrink: 0; }
.sheet-item__time { font-size: 12px; color: var(--text-dim); flex-shrink: 0; }

/* ── Floating session pill (mobile, shown in expanded view) ── */
.session-pill {
  position: fixed;
  bottom: 24px;
  right: 16px;
  z-index: 50;
  background: var(--bg-header);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 8px 14px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-muted);
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  transition: border-color var(--t-fast), color var(--t-fast), opacity var(--t-fast);
  opacity: 0.75;
}
.session-pill:hover { opacity: 1; border-color: var(--accent); color: var(--text); }
.session-pill__label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px; }
.session-pill__bell { color: var(--bell); }

/* ── Prefers reduced motion — disable all animations ── */
@media (prefers-reduced-motion: reduce) {
  .tile-bell               { animation: none; }
  .session-tile--expanding { transition: none; }
  .session-grid--dimming .session-tile:not(.session-tile--expanding) { transition: none; }
  .bottom-sheet__panel     { animation: none; }
  .toast                   { animation: none; }
}
```

### Step 4: Run the new tests to verify they pass

```bash
pytest coordinator/tests/test_frontend_css.py::test_css_bottom_sheet coordinator/tests/test_frontend_css.py::test_css_session_pill coordinator/tests/test_frontend_css.py::test_css_reduced_motion -v
```

Expected: All 3 **PASS**.

### Step 5: Run the complete CSS test suite

```bash
pytest coordinator/tests/test_frontend_css.py -v
```

Expected: All 12 CSS tests **PASS** — one per CSS task covering tokens, grid, breakpoints, zoom, bell, mobile tiers, command palette, bottom sheet, pill, and reduced motion.

### Step 6: Run the complete test suite — final Phase 2a check

```bash
pytest -q
```

Expected: **`117 passed`** (92 original backend + 3 static serving + 10 HTML + 12 CSS)

If the count is different, run `pytest -v` to identify any failures before proceeding.

### Step 7: Commit

```bash
git add frontend/style.css coordinator/tests/test_frontend_css.py
git commit -m "feat: add style.css — mobile bottom sheet, session pill, reduced motion"
```

---

## Phase 2a Complete — Verification Checklist

Run this before declaring Phase 2a done:

```bash
# 1. All tests pass
pytest -q
# Expected: 117 passed, 0 failed, 0 errors

# 2. frontend/ contains all expected files
ls -la frontend/
# Expected: app.js  index.html  manifest.json  style.css  terminal.js

# 3. Server starts cleanly
uvicorn coordinator.main:app --host 127.0.0.1 --port 8091 &
sleep 1

# 4. Root serves HTML
curl -s -o /dev/null -w "GET /: %{http_code}\n" http://127.0.0.1:8091/

# 5. CSS is served
curl -s -o /dev/null -w "GET /style.css: %{http_code}\n" http://127.0.0.1:8091/style.css

# 6. Manifest is served
curl -s -o /dev/null -w "GET /manifest.json: %{http_code}\n" http://127.0.0.1:8091/manifest.json

# 7. API routes still work (not shadowed by StaticFiles)
curl -s -o /dev/null -w "GET /api/sessions: %{http_code}\n" http://127.0.0.1:8091/api/sessions

kill %1
```

Expected output:
```
GET /: 200
GET /style.css: 200
GET /manifest.json: 200
GET /api/sessions: 200
```

---

## Scope Boundaries — Phase 2a Does NOT Include

Do not implement any of the following. They are Phase 2b or Phase 3:

- Any JavaScript implementation in `app.js` or `terminal.js` (stubs only in this phase)
- `xterm.js` `Terminal` object initialization (Phase 2b)
- WebSocket connection logic to ttyd (Phase 2b)
- API polling — `GET /api/sessions` calls from the browser (Phase 2b)
- Bell detection JavaScript and browser Notifications API (Phase 2b)
- Session heartbeat sending from browser (Phase 2b)
- Grid ↔ expanded view transitions via JavaScript (Phase 2b)
- Service worker / offline support (Phase 3)
- Caddy reverse proxy configuration (Phase 3)
- systemd service file (Phase 3)
- Icon files (`/icons/icon-192.png`, `/icons/icon-512.png`) (Phase 3)
- Browser/E2E tests (Phase 3)

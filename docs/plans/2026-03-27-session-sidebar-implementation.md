# Session Sidebar Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Add a collapsible left-side sidebar to the terminal view showing a live session list with snapshot previews, usable as a session switcher without leaving the active terminal.

**Architecture:** Pure frontend — no backend changes. Sidebar consumes session data already fetched by the poll loop. CSS-only animations (width transition on wide, transform slide on narrow). Responsive at 960px: side-by-side on wide screens, fixed overlay with click-away on narrow.

**Tech stack:** Vanilla JS, CSS custom properties, Node test runner (`node:test`), no build step.

**Design doc:** `muxplex/docs/plans/2026-03-27-session-sidebar-design.md`

---

## Conventions

- **Working directory for git:** `/home/bkrabach/dev/web-tmux`
- **Working directory for tests:** `/home/bkrabach/dev/web-tmux/muxplex`
- **Test command:** `node --test frontend/tests/test_app.mjs`
- **Test framework:** `node:test` + `node:assert/strict` (no Jest/Vitest)
- **Test file:** `muxplex/frontend/tests/test_app.mjs` — all sidebar tests go here
- **Import pattern:** CJS shim — `const app = require(join(__dirname, '..', 'app.js'))`
- **DOM stubs:** injected onto `globalThis.document` before require
- **App state naming:** `_camelCase` with leading underscore (`_viewingSession`, `_viewMode`)
- **Helper `$`:** `document.getElementById` alias — use throughout app.js
- **Helper `on`:** `element.addEventListener` alias — use for all event binding
- **Exports:** app.js has a `module.exports` block at lines 887–935 — add all new public functions there
- **Commits:** conventional format, atomic per task, run from `/home/bkrabach/dev/web-tmux`

---

## Task 1: HTML structure — sidebar markup, toggle button, view-body wrapper

**Files:**
- Modify: `muxplex/frontend/index.html`

### Step 1: Add sidebar toggle button to expanded-header

In `muxplex/frontend/index.html`, find lines 31–35 (the `expanded-header`). Insert the sidebar toggle button after `#back-btn` and before `#expanded-session-name`:

Replace:
```html
    <header class="expanded-header">
      <button id="back-btn" class="back-btn" aria-label="Back">&#8592;</button>
      <span id="expanded-session-name" class="expanded-session-name"></span>
```

With:
```html
    <header class="expanded-header">
      <button id="back-btn" class="back-btn" aria-label="Back">&#8592;</button>
      <button id="sidebar-toggle-btn" class="sidebar-toggle-btn" aria-label="Toggle session list">&#9776;</button>
      <span id="expanded-session-name" class="expanded-session-name"></span>
```

### Step 2: Wrap terminal-container in view-body and add sidebar

Replace line 36:
```html
    <div id="terminal-container" class="terminal-container"></div>
```

With:
```html
    <div class="view-body">
      <div id="session-sidebar" class="session-sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">Sessions</span>
          <button id="sidebar-collapse-btn" class="sidebar-collapse-btn" aria-label="Collapse sidebar">&#8249;</button>
        </div>
        <div id="sidebar-list" class="sidebar-list"></div>
      </div>
      <div id="terminal-container" class="terminal-container"></div>
    </div>
```

**Important:** `#reconnect-overlay` (line 37) must remain OUTSIDE `.view-body` — it stays as a sibling of `.view-body` inside `#view-expanded`.

### Step 3: Verify the final structure

The `#view-expanded` section should now look like:
```html
  <div id="view-expanded" class="view hidden">
    <header class="expanded-header">
      <button id="back-btn" class="back-btn" aria-label="Back">&#8592;</button>
      <button id="sidebar-toggle-btn" class="sidebar-toggle-btn" aria-label="Toggle session list">&#9776;</button>
      <span id="expanded-session-name" class="expanded-session-name"></span>
      <button id="palette-trigger" class="palette-trigger" aria-label="Open command palette">&#8984;K</button>
    </header>
    <div class="view-body">
      <div id="session-sidebar" class="session-sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">Sessions</span>
          <button id="sidebar-collapse-btn" class="sidebar-collapse-btn" aria-label="Collapse sidebar">&#8249;</button>
        </div>
        <div id="sidebar-list" class="sidebar-list"></div>
      </div>
      <div id="terminal-container" class="terminal-container"></div>
    </div>
    <div id="reconnect-overlay" class="reconnect-overlay hidden" aria-live="polite">Reconnecting&hellip;</div>
  </div>
```

### Step 4: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/index.html
git commit -m "feat: add sidebar markup, toggle button, and view-body wrapper to expanded view"
```

---

## Task 2: CSS — view-body flex layout and --bg-surface variable

**Files:**
- Modify: `muxplex/frontend/style.css`

### Step 1: Add --bg-surface CSS variable

In `muxplex/frontend/style.css`, find the `:root` block (line 3). Add `--bg-surface` after line 10 (`--bg-tile-hover`):

Replace:
```css
  --bg-tile-hover: #1A1F2B; /* --color-bg-surface */
```

With:
```css
  --bg-tile-hover: #1A1F2B; /* --color-bg-surface */
  --bg-surface: #1A1F2B;    /* sidebar hover & active card background */
```

### Step 2: Add view-body layout rules

Find the `.terminal-container` rule (lines 367–372). Insert `.view-body` rules immediately BEFORE it:

Insert before `.terminal-container {`:
```css
/* View body: flex-row region below the expanded header */
.view-body {
  display: flex;
  flex-direction: row;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

```

### Step 3: Add flex properties to .terminal-container

The existing `.terminal-container` rule (lines 367–372) already has `flex: 1` and `overflow: hidden`. Verify it contains:

```css
.terminal-container {
  flex: 1;
  overflow: hidden;
  background: #000;
  padding: 0 4px;
}
```

Add `min-width: 0;` to prevent flex overflow:

```css
.terminal-container {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  background: #000;
  padding: 0 4px;
}
```

### Step 4: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/style.css
git commit -m "feat: add view-body flex layout and --bg-surface variable"
```

---

## Task 3: CSS — sidebar container and collapse animation

**Files:**
- Modify: `muxplex/frontend/style.css`

### Step 1: Add sidebar container styles

In `muxplex/frontend/style.css`, insert the following block immediately after the `.view-body` rules added in Task 2 (before `.terminal-container`):

```css
/* ============================================================
   Session sidebar
   ============================================================ */

/* Sidebar container — wide screens (>=960px): side-by-side */
.session-sidebar {
  width: 200px;
  min-width: 200px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  flex-shrink: 0;
  transition: width 0.25s ease, min-width 0.25s ease;
}

.session-sidebar.sidebar--collapsed {
  width: 0;
  min-width: 0;
}

/* Sidebar header */
.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.sidebar-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
}

.sidebar-list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

.sidebar-collapse-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 18px;
  padding: 2px 6px;
  line-height: 1;
  border-radius: 4px;
}
.sidebar-collapse-btn:hover { color: var(--text); }

/* Sidebar toggle in expanded header */
.sidebar-toggle-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-muted);
  font-size: 14px;
  width: 36px;
  height: 36px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 8px;
  flex-shrink: 0;
}
.sidebar-toggle-btn:hover {
  border-color: var(--accent);
  color: var(--text);
}

```

### Step 2: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/style.css
git commit -m "feat: add sidebar container styles and collapse animation"
```

---

## Task 4: CSS — sidebar session card styles

**Files:**
- Modify: `muxplex/frontend/style.css`

### Step 1: Add session card styles

In `muxplex/frontend/style.css`, append the following immediately after the sidebar toggle button rules (after `.sidebar-toggle-btn:hover`), still inside the "Session sidebar" section:

```css
/* Sidebar session cards */
.sidebar-item {
  height: 120px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-subtle);
  cursor: pointer;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  position: relative;
}

.sidebar-item:hover { background: var(--bg-surface); }

.sidebar-item--active {
  background: var(--bg-surface);
  border-left: 3px solid var(--accent);
}

.sidebar-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 8px 4px;
  flex-shrink: 0;
  height: 32px;
  gap: 4px;
}

.sidebar-item-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.sidebar-item-body {
  flex: 1;
  position: relative;
  overflow: hidden;
}

.sidebar-item-body pre {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 10px;
  line-height: 1.4;
  color: var(--text-muted);
  white-space: pre;
  overflow: hidden;
  margin: 0;
  padding: 0 8px 6px;
}

.sidebar-empty {
  padding: 16px 12px;
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}

```

### Step 2: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/style.css
git commit -m "feat: add sidebar session card styles"
```

---

## Task 5: CSS — responsive overlay at <960px

**Files:**
- Modify: `muxplex/frontend/style.css`

### Step 1: Add responsive media query

In `muxplex/frontend/style.css`, add the following at the very end of the file (after the `.app-wordmark img` rule, which is the last rule currently at line 762):

```css

/* ============================================================
   Responsive: sidebar becomes overlay at <960px
   ============================================================ */

@media (max-width: 959px) {
  .session-sidebar {
    position: fixed;
    left: 0;
    top: 0;
    height: 100%;
    z-index: 200;
    width: 240px;
    min-width: 240px;
    transition: transform 0.25s ease;
    transform: translateX(0);
    box-shadow: 2px 0 16px rgba(0, 0, 0, 0.5);
  }

  .session-sidebar.sidebar--collapsed {
    width: 240px;
    min-width: 240px;
    transform: translateX(-100%);
  }

  /* Hide internal chevron in overlay mode — header button is the only toggle */
  .sidebar-collapse-btn {
    display: none;
  }
}
```

### Step 2: Add sidebar to reduced-motion overrides

Find the existing `@media (prefers-reduced-motion: reduce)` block (around line 726). Add the sidebar transition override inside it:

After `  .toast { animation: none; }` and before the closing `}`, add:

```css

  .session-sidebar {
    transition: none;
  }
```

### Step 3: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/style.css
git commit -m "feat: add responsive sidebar overlay at <960px and reduced-motion support"
```

---

## Task 6: `buildSidebarHTML` — session card HTML builder (TDD)

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

### Step 1: Write the failing tests

In `muxplex/frontend/tests/test_app.mjs`, add the following test group at the very end of the file (after the last `closeBottomSheet` test, around line 1648):

```js

// ─── buildSidebarHTML ────────────────────────────────────────────────────

test('buildSidebarHTML marks active session with sidebar-item--active class', () => {
  const session = { name: 'work', snapshot: '', bell: null };
  const html = app.buildSidebarHTML(session, 'work');
  assert.ok(html.includes('sidebar-item--active'), 'active class missing');
});

test('buildSidebarHTML does NOT mark inactive session with sidebar-item--active', () => {
  const session = { name: 'work', snapshot: '', bell: null };
  const html = app.buildSidebarHTML(session, 'other');
  assert.ok(!html.includes('sidebar-item--active'), 'active class should not be present');
});

test('buildSidebarHTML renders bell badge when unseen_count > 0', () => {
  const session = { name: 'work', snapshot: '', bell: { unseen_count: 3 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('tile-bell'), 'bell badge missing');
  assert.ok(html.includes('>3<'), 'bell count not rendered');
});

test('buildSidebarHTML omits bell badge when unseen_count is 0', () => {
  const session = { name: 'work', snapshot: '', bell: { unseen_count: 0 } };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(!html.includes('tile-bell'), 'bell badge should be absent');
});

test('buildSidebarHTML HTML-escapes the session name', () => {
  const session = { name: '<script>xss</script>', snapshot: '', bell: null };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(!html.includes('<script>'), 'XSS not escaped');
  assert.ok(html.includes('&lt;script&gt;'), 'expected escaped name');
});

test('buildSidebarHTML returns article element with data-session attribute', () => {
  const session = { name: 'my-session', snapshot: 'line1\nline2', bell: null };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('data-session="my-session"'), 'data-session missing');
  assert.ok(html.startsWith('<article'), 'should be an article element');
});

test('buildSidebarHTML includes snapshot preview in pre element', () => {
  const session = { name: 'x', snapshot: 'hello\nworld', bell: null };
  const html = app.buildSidebarHTML(session, null);
  assert.ok(html.includes('sidebar-item-body'), 'preview body missing');
  assert.ok(html.includes('<pre>'), 'pre element missing');
});
```

### Step 2: Run tests — expect FAIL

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -15
```

Expected: FAIL — `app.buildSidebarHTML is not a function` (or similar TypeError).

### Step 3: Implement buildSidebarHTML

In `muxplex/frontend/app.js`, add the following function immediately after the `buildTileHTML` function (after line 301, before the `renderGrid` function):

```js

/**
 * Build HTML for a single sidebar session card.
 * @param {object} session
 * @param {string|null} currentSession - name of the currently open session
 * @returns {string}
 */
function buildSidebarHTML(session, currentSession) {
  const name = session.name || '';
  const escapedName = escapeHtml(name);
  const isActive = name === currentSession;
  const classes = 'sidebar-item' + (isActive ? ' sidebar-item--active' : '');

  const unseen = session.bell && session.bell.unseen_count;
  let bellHtml = '';
  if (unseen && unseen > 0) {
    const countStr = unseen > 9 ? '9+' : String(unseen);
    bellHtml = `<span class="tile-bell">${countStr}</span>`;
  }

  const snapshot = session.snapshot || '';
  const lastLines = snapshot.split('\n').slice(-20).join('\n');

  return (
    `<article class="${classes}" data-session="${escapedName}" tabindex="0" role="listitem" aria-label="${escapedName}">` +
    `<div class="sidebar-item-header">` +
    `<span class="sidebar-item-name">${escapeHtml(name)}</span>` +
    `${bellHtml}` +
    `</div>` +
    `<div class="sidebar-item-body"><pre>${escapeHtml(lastLines)}</pre></div>` +
    `</article>`
  );
}
```

### Step 4: Export buildSidebarHTML

In the `module.exports` block at the bottom of `muxplex/frontend/app.js` (around line 900), add `buildSidebarHTML` after the `buildTileHTML` line:

Find:
```js
    buildTileHTML,
    renderGrid,
```

Replace with:
```js
    buildTileHTML,
    buildSidebarHTML,
    renderGrid,
```

### Step 5: Run tests — expect PASS

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: all tests pass (existing + 7 new sidebar tests), 0 failures.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs
git commit -m "feat: add buildSidebarHTML — session card HTML builder for sidebar"
```

---

## Task 7: `renderSidebar` — populate sidebar list from session data (TDD)

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

### Step 1: Write the failing tests

In `muxplex/frontend/tests/test_app.mjs`, add after the `buildSidebarHTML` tests:

```js

// ─── renderSidebar ───────────────────────────────────────────────────────

test('renderSidebar populates sidebar-list when view is fullscreen', () => {
  let capturedHtml = '';
  const fakeList = {
    querySelectorAll: () => [],
    get innerHTML() { return capturedHtml; },
    set innerHTML(v) { capturedHtml = v; },
  };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => id === 'sidebar-list' ? fakeList : null;
  app._setViewMode('fullscreen');

  const sessions = [
    { name: 'work', snapshot: 'line', bell: null },
    { name: 'play', snapshot: '', bell: { unseen_count: 1 } },
  ];
  app.renderSidebar(sessions, 'work');

  assert.ok(capturedHtml.includes('sidebar-item'), 'no sidebar items rendered');
  assert.ok(capturedHtml.includes('sidebar-item--active'), 'active session not marked');
  globalThis.document.getElementById = origGetEl;
  app._setViewMode('grid');
});

test('renderSidebar renders empty message when sessions array is empty', () => {
  let capturedHtml = '';
  const fakeList = {
    querySelectorAll: () => [],
    get innerHTML() { return capturedHtml; },
    set innerHTML(v) { capturedHtml = v; },
  };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => id === 'sidebar-list' ? fakeList : null;
  app._setViewMode('fullscreen');

  app.renderSidebar([], null);

  assert.ok(capturedHtml.includes('sidebar-empty'), 'empty state message missing');
  assert.ok(capturedHtml.includes('No sessions'), 'empty text missing');
  globalThis.document.getElementById = origGetEl;
  app._setViewMode('grid');
});

test('renderSidebar does nothing when view is not fullscreen', () => {
  let rendered = false;
  const fakeList = {
    set innerHTML(v) { rendered = true; },
    querySelectorAll: () => [],
  };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => id === 'sidebar-list' ? fakeList : null;
  app._setViewMode('grid');

  app.renderSidebar([{ name: 'x', snapshot: '', bell: null }], null);

  assert.ok(!rendered, 'renderSidebar should not render in grid mode');
  globalThis.document.getElementById = origGetEl;
});
```

### Step 2: Run tests — expect FAIL

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | grep -E "FAIL|renderSidebar" | head -10
```

Expected: FAIL — `app.renderSidebar is not a function`.

### Step 3: Implement renderSidebar

In `muxplex/frontend/app.js`, add the following function immediately after `buildSidebarHTML` (before `renderGrid`):

```js

/**
 * Render the sidebar session list. No-ops when dashboard is active.
 * @param {object[]} sessions
 * @param {string|null} currentSession
 */
function renderSidebar(sessions, currentSession) {
  if (_viewMode !== 'fullscreen') return;
  const list = $('sidebar-list');
  if (!list) return;

  if (!sessions || sessions.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">No sessions</div>';
    return;
  }

  list.innerHTML = sessions.map((s) => buildSidebarHTML(s, currentSession)).join('');

  list.querySelectorAll('.sidebar-item').forEach((item) => {
    on(item, 'click', () => {
      const name = item.dataset.session;
      if (name && name !== currentSession) openSession(name);
    });
  });
}
```

### Step 4: Export renderSidebar

In the `module.exports` block, add `renderSidebar` after `buildSidebarHTML`:

Find:
```js
    buildSidebarHTML,
    renderGrid,
```

Replace with:
```js
    buildSidebarHTML,
    renderSidebar,
    renderGrid,
```

### Step 5: Run tests — expect PASS

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: all tests pass, 0 failures.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs
git commit -m "feat: add renderSidebar — populates sidebar list from session data"
```

---

## Task 8: `initSidebar` + `toggleSidebar` with localStorage persistence (TDD)

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

### Step 1: Add localStorage and window.innerWidth stubs to test file

In `muxplex/frontend/tests/test_app.mjs`, add the following BEFORE the import statements (after the existing `globalThis.Notification` stub around line 25, before the `navigator` defineProperty):

```js

const _localStorageStore = {};
globalThis.localStorage = {
  getItem: (k) => _localStorageStore[k] ?? null,
  setItem: (k, v) => { _localStorageStore[k] = String(v); },
  removeItem: (k) => { delete _localStorageStore[k]; },
};
```

Also add `innerWidth` to the existing `globalThis.window` object. Find:

```js
globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
};
```

Replace with:

```js
globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
  innerWidth: 1024,
};
```

### Step 2: Write the failing tests

Add at the end of `muxplex/frontend/tests/test_app.mjs`:

```js

// ─── initSidebar + toggleSidebar ──────────────────────────────────────────

test('initSidebar defaults to open on wide screens when no stored value', () => {
  delete _localStorageStore['muxplex.sidebarOpen'];
  let removedClass = null;
  const fakeSidebar = {
    classList: {
      add: () => {},
      remove: (c) => { removedClass = c; },
    },
  };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => id === 'session-sidebar' ? fakeSidebar : null;
  globalThis.window.innerWidth = 1200;

  app.initSidebar();

  assert.strictEqual(removedClass, 'sidebar--collapsed', 'should remove collapsed class on wide screen');
  globalThis.document.getElementById = origGetEl;
});

test('initSidebar defaults to closed on narrow screens when no stored value', () => {
  delete _localStorageStore['muxplex.sidebarOpen'];
  let addedClass = null;
  const fakeSidebar = {
    classList: {
      add: (c) => { addedClass = c; },
      remove: () => {},
    },
  };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => id === 'session-sidebar' ? fakeSidebar : null;
  globalThis.window.innerWidth = 600;

  app.initSidebar();

  assert.strictEqual(addedClass, 'sidebar--collapsed', 'should add collapsed class on narrow screen');
  globalThis.document.getElementById = origGetEl;
});

test('initSidebar respects stored value true regardless of screen width', () => {
  _localStorageStore['muxplex.sidebarOpen'] = 'true';
  let removedClass = null;
  const fakeSidebar = {
    classList: {
      add: () => {},
      remove: (c) => { removedClass = c; },
    },
  };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => id === 'session-sidebar' ? fakeSidebar : null;
  globalThis.window.innerWidth = 600; // narrow, but stored says open

  app.initSidebar();

  assert.strictEqual(removedClass, 'sidebar--collapsed', 'should respect stored true value');
  globalThis.document.getElementById = origGetEl;
});

test('toggleSidebar persists state to localStorage', () => {
  _localStorageStore['muxplex.sidebarOpen'] = 'true';
  const fakeSidebar = { classList: { add: () => {}, remove: () => {} } };
  const fakeBtn = { textContent: '' };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return fakeSidebar;
    if (id === 'sidebar-collapse-btn') return fakeBtn;
    return null;
  };

  app.toggleSidebar();

  assert.strictEqual(_localStorageStore['muxplex.sidebarOpen'], 'false', 'should toggle to false');
  globalThis.document.getElementById = origGetEl;
});

test('toggleSidebar adds sidebar--collapsed class when closing', () => {
  _localStorageStore['muxplex.sidebarOpen'] = 'true';
  let addedClass = null;
  const fakeSidebar = {
    classList: {
      add: (c) => { addedClass = c; },
      remove: () => {},
    },
  };
  const fakeBtn = { textContent: '' };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return fakeSidebar;
    if (id === 'sidebar-collapse-btn') return fakeBtn;
    return null;
  };

  app.toggleSidebar();

  assert.strictEqual(addedClass, 'sidebar--collapsed');
  globalThis.document.getElementById = origGetEl;
});

test('toggleSidebar removes sidebar--collapsed class when opening', () => {
  _localStorageStore['muxplex.sidebarOpen'] = 'false';
  let removedClass = null;
  const fakeSidebar = {
    classList: {
      add: () => {},
      remove: (c) => { removedClass = c; },
    },
  };
  const fakeBtn = { textContent: '' };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'session-sidebar') return fakeSidebar;
    if (id === 'sidebar-collapse-btn') return fakeBtn;
    return null;
  };

  app.toggleSidebar();

  assert.strictEqual(removedClass, 'sidebar--collapsed');
  assert.strictEqual(_localStorageStore['muxplex.sidebarOpen'], 'true', 'should toggle to true');
  globalThis.document.getElementById = origGetEl;
});
```

### Step 3: Run tests — expect FAIL

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | grep -E "FAIL|initSidebar|toggleSidebar" | head -10
```

Expected: FAIL — `app.initSidebar is not a function`.

### Step 4: Implement initSidebar and toggleSidebar

In `muxplex/frontend/app.js`, add the following two functions after `renderSidebar` (before `renderGrid`):

```js

/**
 * Initialise sidebar open/closed state from localStorage.
 * Defaults: open on wide screens (>=960px), closed on narrow.
 */
function initSidebar() {
  const isNarrow = window.innerWidth < 960;
  let open;
  try { open = JSON.parse(localStorage.getItem('muxplex.sidebarOpen')); } catch { open = null; }
  if (open === null) open = !isNarrow;

  const sidebar = $('session-sidebar');
  if (sidebar) {
    if (open) {
      sidebar.classList.remove('sidebar--collapsed');
    } else {
      sidebar.classList.add('sidebar--collapsed');
    }
  }
  try { localStorage.setItem('muxplex.sidebarOpen', JSON.stringify(open)); } catch {}
}

/**
 * Toggle sidebar open/closed; persists new state to localStorage.
 */
function toggleSidebar() {
  let open;
  try { open = JSON.parse(localStorage.getItem('muxplex.sidebarOpen')); } catch { open = null; }
  const newOpen = open === null ? false : !open;

  try { localStorage.setItem('muxplex.sidebarOpen', JSON.stringify(newOpen)); } catch {}

  const sidebar = $('session-sidebar');
  if (sidebar) {
    if (newOpen) {
      sidebar.classList.remove('sidebar--collapsed');
    } else {
      sidebar.classList.add('sidebar--collapsed');
    }
  }

  const collapseBtn = $('sidebar-collapse-btn');
  if (collapseBtn) collapseBtn.textContent = newOpen ? '\u2039' : '\u203a';
}
```

### Step 5: Export both functions

In the `module.exports` block, add `initSidebar` and `toggleSidebar`. Find:

```js
    renderSidebar,
    renderGrid,
```

Replace with:
```js
    renderSidebar,
    initSidebar,
    toggleSidebar,
    renderGrid,
```

### Step 6: Run tests — expect PASS

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: all tests pass, 0 failures.

### Step 7: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs
git commit -m "feat: add initSidebar and toggleSidebar with localStorage persistence"
```

---

## Task 9: Wire toggle button click handlers

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

### Step 1: Write the failing test

Add at the end of `muxplex/frontend/tests/test_app.mjs`:

```js

// ─── Sidebar toggle wiring ───────────────────────────────────────────────

test('bindStaticEventListeners binds sidebar-toggle-btn click', () => {
  const eventsBound = {};
  const origGetById = globalThis.document.getElementById;
  const origDocAddListener = globalThis.document.addEventListener;
  globalThis.document.getElementById = (id) => {
    const el = { _events: {}, addEventListener: (ev, fn) => { el._events[ev] = fn; } };
    eventsBound[id] = el;
    return el;
  };
  globalThis.document.addEventListener = () => {};

  app.bindStaticEventListeners();

  assert.ok(
    eventsBound['sidebar-toggle-btn'] && 'click' in eventsBound['sidebar-toggle-btn']._events,
    '#sidebar-toggle-btn should have a click listener',
  );
  assert.ok(
    eventsBound['sidebar-collapse-btn'] && 'click' in eventsBound['sidebar-collapse-btn']._events,
    '#sidebar-collapse-btn should have a click listener',
  );
  globalThis.document.getElementById = origGetById;
  globalThis.document.addEventListener = origDocAddListener;
});
```

### Step 2: Run tests — expect FAIL

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | grep "sidebar-toggle-btn" | head -5
```

Expected: FAIL — sidebar-toggle-btn does not have a click listener.

### Step 3: Add toggle bindings to bindStaticEventListeners

In `muxplex/frontend/app.js`, find the `bindStaticEventListeners` function (around line 817). Add the two sidebar toggle bindings. Find:

```js
function bindStaticEventListeners() {
  on($('back-btn'), 'click', closeSession);
  on($('palette-trigger'), 'click', openPalette);
```

Replace with:
```js
function bindStaticEventListeners() {
  on($('back-btn'), 'click', closeSession);
  on($('sidebar-toggle-btn'), 'click', toggleSidebar);
  on($('sidebar-collapse-btn'), 'click', toggleSidebar);
  on($('palette-trigger'), 'click', openPalette);
```

### Step 4: Run tests — expect PASS

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: all tests pass, 0 failures.

### Step 5: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs
git commit -m "feat: wire sidebar toggle button click handlers"
```

---

## Task 10: Pre-render sidebar on openSession

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

### Step 1: Write the failing test

Add at the end of `muxplex/frontend/tests/test_app.mjs`:

```js

// ─── Sidebar integration with openSession ────────────────────────────────

test('initSidebar and renderSidebar are exported for openSession integration', () => {
  assert.strictEqual(typeof app.initSidebar, 'function', 'initSidebar must be exported');
  assert.strictEqual(typeof app.renderSidebar, 'function', 'renderSidebar must be exported');
});
```

### Step 2: Run tests — expect PASS (this is a guard test)

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: PASS — both functions already exported from earlier tasks.

### Step 3: Add sidebar pre-render to openSession

In `muxplex/frontend/app.js`, find the `openSession` function (around line 459). Add sidebar initialization right after the `_viewMode` assignment. Find:

```js
async function openSession(name, opts = {}) {
  _viewingSession = name;
  _viewMode = 'fullscreen';

  // Update expanded header
```

Replace with:
```js
async function openSession(name, opts = {}) {
  _viewingSession = name;
  _viewMode = 'fullscreen';

  // Pre-render sidebar with current sessions before first poll tick
  initSidebar();
  renderSidebar(_currentSessions, name);

  // Update expanded header
```

### Step 4: Add sidebar render after view becomes visible

In the same `openSession` function, find the `setTimeout` callback (around line 487). Add sidebar render at the end of the callback body, AFTER the `_openTerminal` call. Find:

```js
    // Mount terminal AFTER view is visible so FitAddon measures real dimensions
    if (window._openTerminal) window._openTerminal(name);
  }, 260);
```

Replace with:
```js
    // Mount terminal AFTER view is visible so FitAddon measures real dimensions
    if (window._openTerminal) window._openTerminal(name);
    // Re-render sidebar after DOM is visible and dimensions are correct
    initSidebar();
    renderSidebar(_currentSessions, name);
  }, 260);
```

### Step 5: Run all tests

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: all tests pass, 0 failures. The existing `openSession` tests should continue to pass because they mock `setTimeout` and `document.getElementById` to return stubs.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs
git commit -m "feat: pre-render sidebar on openSession before first poll tick"
```

---

## Task 11: Wire renderSidebar into the poll loop

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

### Step 1: Write the failing test

Add at the end of `muxplex/frontend/tests/test_app.mjs`:

```js

// ─── Sidebar in poll loop ────────────────────────────────────────────────

test('pollSessions calls renderSidebar (verified via sidebar-list innerHTML)', async () => {
  let sidebarRendered = false;
  const origGetById = globalThis.document.getElementById;
  const origQSA = globalThis.document.querySelectorAll;
  globalThis.document.querySelectorAll = () => [];
  globalThis.document.getElementById = (id) => {
    if (id === 'sidebar-list') {
      return {
        querySelectorAll: () => [],
        set innerHTML(v) { sidebarRendered = true; },
        get innerHTML() { return ''; },
      };
    }
    if (id === 'session-grid') return { innerHTML: '' };
    if (id === 'empty-state') return { classList: { add: () => {}, remove: () => {} } };
    if (id === 'connection-status') return { textContent: '', className: '' };
    if (id === 'session-pill-bell') return { classList: { add: () => {}, remove: () => {} } };
    return null;
  };

  const sessions = [{ name: 'test', snapshot: 'hello', bell: null }];
  globalThis.fetch = async () => ({ ok: true, json: async () => sessions });
  app._setViewMode('fullscreen');

  await app.pollSessions();

  assert.ok(sidebarRendered, 'renderSidebar should be called during pollSessions in fullscreen mode');
  globalThis.document.getElementById = origGetById;
  globalThis.document.querySelectorAll = origQSA;
  globalThis.fetch = undefined;
  app._setViewMode('grid');
});
```

### Step 2: Run tests — expect FAIL

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | grep "pollSessions calls renderSidebar"
```

Expected: FAIL — `sidebarRendered` is still false because `pollSessions` doesn't call `renderSidebar` yet.

### Step 3: Add renderSidebar call to pollSessions

In `muxplex/frontend/app.js`, find the `pollSessions` function. Add `renderSidebar` after `renderGrid`. Find:

```js
    setConnectionStatus('ok');
    renderGrid(sessions);
    handleBellTransitions(prev, sessions);
```

Replace with:
```js
    setConnectionStatus('ok');
    renderGrid(sessions);
    renderSidebar(sessions, _viewingSession);
    handleBellTransitions(prev, sessions);
```

### Step 4: Run tests — expect PASS

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -5
```

Expected: all tests pass, 0 failures.

### Step 5: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs
git commit -m "feat: wire renderSidebar into poll loop for live sidebar updates"
```

---

## Task 12: Click-away handler for overlay mode

**Files:**
- Modify: `muxplex/frontend/app.js`
- Modify: `muxplex/frontend/tests/test_app.mjs`

### Step 1: Write the failing test

Add at the end of `muxplex/frontend/tests/test_app.mjs`:

```js

// ─── bindSidebarClickAway ────────────────────────────────────────────────

test('bindSidebarClickAway is exported and callable', () => {
  assert.strictEqual(typeof app.bindSidebarClickAway, 'function');
});

test('bindSidebarClickAway registers click listener on terminal-container', () => {
  let clickBound = false;
  const fakeTC = {
    addEventListener: (ev) => { if (ev === 'click') clickBound = true; },
  };
  const origGetEl = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => id === 'terminal-container' ? fakeTC : null;

  app.bindSidebarClickAway();

  assert.ok(clickBound, 'should register click on terminal-container');
  globalThis.document.getElementById = origGetEl;
});
```

### Step 2: Run tests — expect FAIL

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | grep "bindSidebarClickAway" | head -5
```

Expected: FAIL — `app.bindSidebarClickAway is not a function`.

### Step 3: Implement bindSidebarClickAway

In `muxplex/frontend/app.js`, add the following function after `toggleSidebar` (before `renderGrid`):

```js

/**
 * In overlay mode (<960px), clicking the terminal area closes the sidebar.
 */
function bindSidebarClickAway() {
  const tc = $('terminal-container');
  if (!tc) return;
  on(tc, 'click', () => {
    if (window.innerWidth >= 960) return;
    const sidebar = $('session-sidebar');
    if (!sidebar || sidebar.classList.contains('sidebar--collapsed')) return;
    sidebar.classList.add('sidebar--collapsed');
    try { localStorage.setItem('muxplex.sidebarOpen', JSON.stringify(false)); } catch {}
  });
}
```

### Step 4: Export and wire bindSidebarClickAway

Add to `module.exports`:

Find:
```js
    toggleSidebar,
    renderGrid,
```

Replace with:
```js
    toggleSidebar,
    bindSidebarClickAway,
    renderGrid,
```

Add the call to `bindStaticEventListeners`. Find:

```js
  on($('sidebar-collapse-btn'), 'click', toggleSidebar);
  on($('palette-trigger'), 'click', openPalette);
```

Replace with:
```js
  on($('sidebar-collapse-btn'), 'click', toggleSidebar);
  bindSidebarClickAway();
  on($('palette-trigger'), 'click', openPalette);
```

### Step 5: Run ALL tests — final verification

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
node --test frontend/tests/test_app.mjs 2>&1 | tail -8
```

Expected: all tests pass, 0 failures.

Also verify no backend tests are broken:
```bash
cd /home/bkrabach/dev/web-tmux/muxplex
python -m pytest coordinator/tests/ --ignore=coordinator/tests/test_integration.py -q 2>&1 | tail -3
```

Expected: all pass, 0 failures.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/web-tmux
git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs
git commit -m "feat: add overlay click-away handler for sidebar on narrow screens

Sidebar now live-updates on every poll tick alongside the dashboard.
Clicking the terminal area in overlay mode (<960px) auto-closes sidebar."
```

---

## Summary of changes by file

| File | Tasks | Lines added (approx) |
|------|-------|---------------------|
| `muxplex/frontend/index.html` | 1 | ~12 |
| `muxplex/frontend/style.css` | 2, 3, 4, 5 | ~180 |
| `muxplex/frontend/app.js` | 6, 7, 8, 9, 10, 11, 12 | ~110 |
| `muxplex/frontend/tests/test_app.mjs` | 6, 7, 8, 9, 10, 11, 12 | ~210 |

**Total commits:** 12 (one per task)
**Backend changes:** None
**New files:** None
**New dependencies:** None
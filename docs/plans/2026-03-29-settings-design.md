# Settings Panel & New Session Design

## Goal

Add a Settings panel and New Session feature to muxplex, removing the command palette (now redundant with the sidebar).

## Background

The command palette was the original mechanism for session switching, but the sidebar + hover preview + click-to-navigate flow now covers the same functionality. The header space freed by removing the palette is better used for settings access and session creation — two features muxplex currently lacks.

Settings need to live in two places: per-device preferences (font size, notifications) in localStorage, and per-user configuration (session defaults, templates) in a server-side file that persists across devices.

## Approach

- **Settings** as a modal dialog (desktop) / bottom sheet (mobile), triggered by a gear icon in the header. Controls apply immediately with debounced persistence — no Save button.
- **New Session** as an inline name input that replaces the `+` button on click. A configurable command template in settings controls what actually runs on the server.
- **Command palette removal** — delete all palette HTML, CSS, and JS.

---

## Architecture

### Settings Access

Gear icon (⚙) in the header, right side, next to a `+` (new session) button and connection status indicator. Same position in both dashboard and expanded terminal views.

Keyboard shortcut: `,` (comma, VS Code convention).

### Desktop Layout (≥600px)

Centered modal dialog — same pattern as the now-removed command palette (backdrop, escape-to-close, centered panel):
- Width: `min(600px, 90vw)`
- Height: `min(480px, 80vh)`
- Left tab list: 140px fixed width
- Active tab: `color: var(--accent)` with 2px left border in `var(--accent)`

Tabs: **Display**, **Sessions**, **Notifications**, **New Session**.

### Mobile Layout (<600px)

Bottom half-sheet (slides up from bottom, drag-to-dismiss downward):
- Same gear icon trigger
- 48px minimum touch targets for all controls
- Dim overlay behind
- Max height ~85vh

### Storage Split

**localStorage (per-device):** Font size, grid columns, hover preview delay, bell sound, desktop notification permission.

**Server (`~/.config/muxplex/settings.json`):** Default session, sort order, hidden sessions, window-size largest, auto-open created sessions, new session command template.

No sync between the two. If localStorage is cleared, the user gets defaults. Server settings persist across devices via the API.

### Controls Behavior

All controls apply immediately — no Save button. Each change debounces 500ms then persists (PATCH to server or localStorage write). A subtle toast confirms the save. This matches developer tool conventions (VS Code, terminal settings).

---

## Components

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/settings` | Returns server-side `settings.json` contents |
| `PATCH` | `/api/settings` | Partial update — merges single-field saves |
| `POST` | `/api/sessions` | Creates a new session (`{"name": "my-project"}`) |

### Server-Side Settings File

`~/.config/muxplex/settings.json`:

```json
{
  "default_session": null,
  "sort_order": "manual",
  "hidden_sessions": [],
  "window_size_largest": false,
  "auto_open_created": true,
  "new_session_template": "tmux new-session -d -s {name}"
}
```

### localStorage Key

`muxplex.display` — single JSON blob:

```json
{
  "fontSize": 14,
  "hoverPreviewDelay": 1500,
  "gridColumns": "auto",
  "bellSound": false,
  "notificationPermission": "default"
}
```

---

## Settings Categories & Fields

### Display Tab (localStorage)

| Field | Control | Default |
|---|---|---|
| Font size | `<select>` (11, 12, 13, 14, 16px) | 14 |
| Hover preview delay | `<select>` (Off, 1s, 1.5s, 2s, 3s) | 1.5s |
| Grid columns | `<select>` (Auto, 2, 3, 4) | Auto |

### Sessions Tab (server)

| Field | Control | Default |
|---|---|---|
| Default session | `<select>` (None + current session list) | None |
| Sort order | `<select>` (Manual, Alphabetical, Recent activity) | Manual |
| Hidden sessions | Checkbox list of session names | None hidden |
| Auto-set window-size largest | Checkbox | Off |
| Auto-open created sessions | Checkbox | On |

### Notifications Tab (localStorage)

| Field | Control | Default |
|---|---|---|
| Bell sound | Checkbox | Off |
| Desktop notifications | Button: "Request permission" / status text | Not requested |

### New Session Tab (server)

| Field | Control | Default |
|---|---|---|
| Command template | `<textarea>` monospace, 3 rows | `tmux new-session -d -s {name}` |
| | Helper text: *`{name}` is replaced with the session name* | |
| | `[ Reset to default ]` button | |

---

## New Session Feature

### Button Placement

- **Dashboard header:** `[+]` button, right side, between wordmark and gear icon. Styled same as gear — `border: 1px solid var(--border)`, `color: var(--text-dim)`.
- **Expanded view sidebar:** `+ New` sticky footer below session list.
- **Mobile (<960px):** 56px FAB, bottom-right, 16px from edges. `+` icon. Replaces header button on mobile.

### Creation Flow — Inline Name Input

Click `+` → button transforms into a text input inline in the header. Auto-focus, placeholder "Session name…", Enter to create, Escape to cancel. One field only — everything else comes from the template.

Sidebar footer: same pattern — button becomes input field inline.

### Template Execution

Configured in Settings → New Session tab. Default: `tmux new-session -d -s {name}`. The `{name}` variable is the only substitution — replaced at creation time.

### Backend

`POST /api/sessions` with `{"name": "my-project"}`.

Server reads template from `settings.json`, substitutes `{name}`, executes via subprocess. Returns 200 with `{"name": "..."}`.

**No existence check** — this deliberately handles create-or-reattach patterns (e.g., `amplifier-dev ~/dev/{name}` creates new or reattaches to existing). If the command fails, the session won't appear and the user sees the dashboard unchanged.

### Post-Creation Behavior

1. Show toast: "Session '{name}' created"
2. Trigger immediate `pollSessions()` refresh
3. Auto-open the session if "auto-open created sessions" is enabled in settings

---

## Command Palette Removal

Remove the entire command palette:
- **HTML:** `#command-palette` div + `#palette-trigger` button
- **CSS:** `.command-palette*`, `.palette-*`
- **JS:** palette state variables, `openPalette`, `closePalette`, `renderPaletteList`, `highlightPaletteItem`, palette keyboard handler, palette event bindings

The sidebar + hover preview + click-to-navigate already provides the same session switching functionality. The header space freed by removing the `⌘K` button is where the `+` and `⚙` buttons go.

---

## Security

The new session template is a shell command the user writes and the server executes. PAM auth already proves they own the machine — this is equivalent to typing in their own terminal. No command sanitization needed.

---

## Implementation Notes

### Files to Create

- `muxplex/frontend/settings.js` — settings modal/sheet logic, tab switching, field rendering, localStorage + API integration
- `muxplex/frontend/settings.html` (or inline in `index.html`) — settings dialog markup

### Files to Modify

- `muxplex/frontend/index.html` — remove command palette HTML, add settings dialog HTML, add `+` and `⚙` header buttons
- `muxplex/frontend/style.css` — remove palette CSS, add settings modal/sheet CSS, add FAB CSS, add inline input CSS
- `muxplex/frontend/app.js` — remove all palette JS, add settings open/close, add new session creation flow, wire up `+` button and FAB
- `muxplex/main.py` — add `GET/PATCH /api/settings` endpoints, add `POST /api/sessions` endpoint
- `muxplex/cli.py` — load `settings.json` at startup for template default

---

## Open Questions

1. Should hidden sessions still be visible with a "hidden" badge, or completely removed from the grid?
2. Should the new session input validate the name (alphanumeric + dash only) or accept anything tmux accepts?
3. Should the gear/settings be accessible from the login page (pre-auth) for display preferences? Probably not for v1.

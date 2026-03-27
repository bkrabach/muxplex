# Session Sidebar Design

## Goal

Add a collapsible left-side sidebar to the terminal (expanded) view that shows a live-updating session list with snapshot previews, usable as a session switcher while actively working in a terminal session.

## Background

The current workflow requires returning to the dashboard to switch sessions or see what's happening in other terminals. A persistent sidebar in the terminal view eliminates that round-trip, giving users at-a-glance visibility into all sessions while keeping the active terminal front and center.

## Approach

Pure frontend addition — no backend or coordinator changes. The sidebar consumes the same `sessions` data already fetched by the poll loop, reuses the existing `openSession()` function for switching, and relies entirely on CSS for layout transitions and responsive behaviour. New code is limited to a `renderSidebar()` function and associated styles.

## Architecture

### Layout

`view-expanded` becomes a flex row with two children:

```
┌──────────────────────────────────────────────────┐
│ view-expanded  (display: flex; flex-direction: row) │
│ ┌────────────┐ ┌──────────────────────────────┐  │
│ │  .session-  │ │  .terminal-container         │  │
│ │   sidebar   │ │  (flex: 1)                   │  │
│ │  (200px)    │ │                              │  │
│ │             │ │                              │  │
│ │  .sidebar-  │ │                              │  │
│ │   header    │ │                              │  │
│ │  .sidebar-  │ │                              │  │
│ │   list      │ │                              │  │
│ └────────────┘ └──────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### Data Flow

```
poll tick
 ├── renderGrid(sessions)          ← dashboard (unchanged)
 └── renderSidebar(sessions, name) ← sidebar (new, same data)
```

No extra network requests. `renderSidebar` skips work when the dashboard view is active.

## Components

### 1 — Layout and Structure

- `view-expanded` container: `display: flex; flex-direction: row`.
- `.session-sidebar`: fixed `width: 200px`, positioned left.
  - `.sidebar-header`: title text + toggle button.
  - `.sidebar-list`: `overflow-y: auto`, scrollable card list.
- `.terminal-container`: `flex: 1`, fills remaining width.
- Collapse: toggling `.sidebar--collapsed` sets `width: 0; overflow: hidden` with a CSS transition (`0.25s ease`).
- Toggle button: sidebar icon (`⋮⋮` or hamburger) in the expanded view header bar next to the existing back button. Secondary `‹`/`›` chevron at the sidebar's right edge in wide (side-by-side) mode only.
- State persisted in `localStorage('muxplex.sidebarOpen')`.

### 2 — Session Cards

Each entry in `.sidebar-list` is a compact card:

```
┌─────────────────────────────────┐
│ ● session-name        [bell 2] │  ← header row: name + badge
├─────────────────────────────────┤
│ last line of output             │
│ $ some command running...       │  ← snapshot preview, bottom-anchored
│ (blank)                         │
└─────────────────────────────────┘
```

- Card height: ~120px (header ~32px + 4–6 lines of 11px monospace preview).
- Snapshot preview: same `lastLines` data already on each session, same `position: absolute; bottom: 0` bottom-anchoring CSS already shipped, restyled for narrower column.
- Active session card: 3px left border in `var(--accent-cyan)`, slightly elevated background (`var(--bg-surface)`).
- Click handler: calls existing `openSession(name)`. Clicking the active session's card is a no-op.
- Session order: mirrors coordinator response order (bell-priority on mobile, natural order on desktop) — no new sorting logic.

### 3 — Data Flow and State

Two pieces of state:

| Variable | Type | Source | Purpose |
|---|---|---|---|
| `currentSession` | `string \| null` | Set by `openSession(name)`, cleared by `closeSession()` | Determines which card gets active highlight |
| `sidebarOpen` | `boolean` | `localStorage('muxplex.sidebarOpen')` | Controls `.sidebar--collapsed` CSS class |

- `renderSidebar(sessions, currentSession)` is called alongside the existing `renderGrid(sessions)` on every poll tick — same data, zero extra network requests.
- `renderSidebar` only does visible work when the terminal view is active. If the dashboard is showing, the sidebar DOM exists but rendering is skipped.
- On `openSession`, the sidebar gets an initial render before the first poll tick catches up.
- `sidebarOpen` toggle applies/removes `.sidebar--collapsed` — animation is CSS-only, no JS timers. Value written back to localStorage on every toggle.

### 4 — Responsive / Overlay Behaviour

**Breakpoint: 960px** viewport width.

| Mode | Width | Sidebar Position | Animation | Chevron |
|---|---|---|---|---|
| Side-by-side | ≥960px | In-flow, flex child | `width: 0/200px` | `‹`/`›` at right edge |
| Overlay | <960px | `position: fixed; left: 0; top: 0; height: 100%` | `transform: translateX(-100%/0)` 0.25s ease | None |

- In overlay mode the terminal stays full-width — nothing shrinks.
- Clicking anywhere on `.terminal-container` closes the overlay.
- Single toggle button always lives in the expanded header bar regardless of mode.
- Default `sidebarOpen`: `true` on wide screens, `false` on narrow (when no localStorage value exists). Stored value respected on subsequent visits regardless of current screen width.

## Error Handling

- Empty sessions array: `renderSidebar` produces an empty list — no crash, no error state.
- Session names are HTML-escaped before rendering to prevent injection.
- `localStorage` read/write wrapped defensively — if unavailable, defaults apply and toggle still works for the current page session.

## Testing Strategy

Unit tests in `frontend/tests/test_app.mjs`, three groups:

### `buildSidebarHTML` output
- Active session card gets `.sidebar-item--active` class; non-active cards do not.
- Bell badge renders when `unseen_count > 0`; absent when 0 or null.
- Session name is HTML-escaped (injection check).
- Empty sessions array produces an empty list, no crash.
- Snapshot preview uses bottom-anchored last-lines approach.

### Sidebar state
- `sidebarOpen` written to `localStorage` on every toggle.
- Default value: `true` when no stored value exists and viewport is wide; `false` when narrow.
- `.sidebar--collapsed` class added/removed in sync with stored value.

### Session switching
- Sidebar card click calls `openSession(name)` with the correct session name.
- Clicking the active session's card is a no-op.

### Not unit-tested
CSS breakpoint behaviour and overlay slide animation are verified by visual inspection during implementation — not unit-testable.

## Open Questions

None — all sections validated.

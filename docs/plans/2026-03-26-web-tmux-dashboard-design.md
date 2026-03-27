# Web-Tmux Dashboard Design

## Goal

Build a browser-based dashboard for monitoring and interacting with ~12 running tmux sessions on a home/dev device, accessible from multiple devices (laptop, mobile, tablet) over Tailscale.

## Background

Managing a dozen tmux sessions across multiple devices requires constant SSH-ing and manual `tmux attach` commands. There's no unified view of what's running, no bell notifications when something needs attention, and no way to glance at session output from a phone. This project solves that by providing a live overview grid with capture-pane snapshots and on-demand interactive terminal access through the browser.

## Tool Decision: ttyd

Six tools were evaluated for browser-based terminal access:

| Tool | Verdict | Reason |
|------|---------|--------|
| **ttyd** | **Chosen** | Single C binary, wraps `tmux attach` natively with no SSH layer. ~5-10 MB memory. Actively maintained (11.3k stars, March 2024 release). Built-in basic auth, mTLS support, header-based auth for proxy delegation, `-m N` connection limits, `-W` writable mode. |
| WeTTY | Rejected | Requires SSH daemon + Node.js runtime. Higher complexity for the same outcome. |
| GoTTY | Rejected | Original abandoned 2017. Active fork has smaller community than ttyd. |
| Sshwifty | Rejected | Browser SSH client, not a terminal server. Solves a different problem. |
| Guacamole | Rejected | Java/Tomcat + 3 containers. Enterprise-scale overkill. |
| shellinabox | Rejected | No WebSocket support. Effectively a dead project. |

## Approach

**Capture-pane overview + single live ttyd session.**

The overview grid renders text snapshots via `tmux capture-pane -p -t <session>` (polled every 2s) into styled monospace `<pre>` blocks. A single `ttyd` instance handles whichever session is currently expanded. This avoids resize conflicts and simultaneous WebSocket pile-ups. ttyd stays running persistently (not tied to browser sessions).

## Architecture

Three components running directly on the host (no Docker — need access to host tmux sessions), plus a reverse proxy:

```
┌─────────────────────────────────────────────────────────────┐
│  Caddy (reverse proxy)                                      │
│  - HTTPS termination                                        │
│  - Basic auth (basicauth directive)                         │
│  - /  and /api/* → Coordinator (port 8080)                  │
│  - /terminal/*   → active ttyd (port 7682)                  │
└─────────────┬──────────────────────────────┬────────────────┘
              │                              │
              ▼                              ▼
┌──────────────────────────┐   ┌──────────────────────────────┐
│  Session Coordinator     │   │  ttyd (C binary)             │
│  Python server (~150 LOC)│   │  Spawned on demand           │
│  Port 8080               │   │  Port 7682                   │
│  - Dashboard HTML/JS/CSS │   │  - One instance at a time    │
│  - JSON API              │   │  - tmux attach -t {name}     │
│  - State management      │   │  - -m 3 (multi-device)       │
│  - Bell detection        │   │  - -W (writable)             │
│  - ttyd lifecycle        │   │                              │
└──────────────────────────┘   └──────────────────────────────┘
              │
              ▼
┌──────────────────────────┐
│  Dashboard Frontend      │
│  Vanilla HTML/CSS/JS     │
│  - CSS auto-fill grid    │
│  - xterm.js terminal     │
│  - Bell notifications    │
│  - PWA support           │
└──────────────────────────┘
```

**Supervisor:** The Coordinator runs as a systemd service for automatic restart after crashes.

**Network access:** Tailscale handles network-level access control. Caddy's basic auth serves as a local backstop.

## Components

### Session Coordinator (Python Server)

A lightweight Python server (~150 lines) serving the dashboard and exposing a JSON API:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/state` | `GET` | Returns full persistent state (active session, session order, bell state) |
| `/api/sessions` | `GET` | Returns all running tmux sessions with capture-pane snapshots and bell flags |
| `/api/sessions/{name}/connect` | `POST` | Kills current ttyd, spawns fresh one on port 7682 for named session, updates state |
| `/api/sessions/current` | `DELETE` | Terminates active ttyd, clears active session from state |
| `/api/state` | `PATCH` | Updates session ordering |

The coordinator owns all state. Browsers are views; the coordinator is the source of truth.

### ttyd Instance

One instance at a time, always on port 7682:

```bash
ttyd -W -m 3 -p 7682 tmux attach -t {name}
```

- `-W` — writable mode
- `-m 3` — allows laptop + tablet + phone simultaneously
- Killed and respawned when switching sessions
- Stays running when browser disconnects (persists until explicitly closed)

### Dashboard Frontend

Vanilla HTML/CSS/JS (no framework).

**Desktop layout:**
- CSS `auto-fill` grid with `minmax(360px, 1fr)` — column count emerges from viewport (4 at 2560px, 2 at 900px, 1 below 600px)
- Fixed 300px tile height
- `capture-pane` text rendered in `<pre>` blocks, showing bottom of output (most recent lines), faded top edge, no scrollbars

**Mobile layout:**
- Three-tier priority list:
  1. Sessions with bells — expanded, 4-6 output lines
  2. Recently active (< 5 min) — 1 line preview
  3. Idle — name + timestamp only
- Tap → full-screen terminal

**Mode transition:** Zoom-in-place — selected tile expands from grid position to fill viewport (~250ms), siblings fade. Reverse on return to grid.

### Caddy (Reverse Proxy)

- HTTPS termination
- `basicauth` directive for authentication
- Routes `/` and `/api/*` → Coordinator (port 8080)
- Routes `/terminal/*` → active ttyd (port 7682)

## Data Flow

### Persistent State

State file location: `~/.local/share/tmux-web/state.json`

```json
{
  "active_session": "work",
  "session_order": ["main", "work", "logs", "music"],
  "sessions": {
    "logs-prod": {
      "bell": {
        "last_fired_at": 1711425900.0,
        "seen_at": null,
        "unseen_count": 3
      }
    }
  },
  "devices": {
    "d-a1b2c3": {
      "label": "Laptop Chrome",
      "viewing_session": "dev-server",
      "view_mode": "fullscreen",
      "last_interaction_at": 1711425950.0,
      "last_heartbeat_at": 1711425958.0
    }
  }
}
```

**Atomic writes:** All `state.json` writes use `os.replace()` (write to temp, rename) — atomic on POSIX.

**Concurrent write safety:** Coordinator uses `asyncio` with a single write lock around `state.json` updates. All async paths serialize through this lock before calling `os.replace()`.

### Browser Connection Flow

1. New browser calls `GET /api/state`
2. If `active_session` is set, frontend immediately connects to already-running ttyd (no "reopen" needed)
3. Browser is a view; coordinator owns the truth

### ttyd Lifecycle

- Spawned when user explicitly opens a session via `POST /api/sessions/{name}/connect`
- Stays running even when browser disconnects
- New connections on any device attach to same running PTY
- Killed only when user explicitly closes the session or switches to another

### Multi-Device Connections

`-m 3` allows phone + tablet + desktop simultaneously. When a new device connects with a different terminal size, the coordinator issues:

```bash
tmux resize-window -t {session} -x {cols} -y {rows}
```

### Client Heartbeat

Every 5 seconds (via WebSocket or poll):

```json
{
  "device_id": "d-a1b2c3",
  "viewing_session": "dev-server",
  "view_mode": "fullscreen",
  "last_interaction_at": 1711425950.0
}
```

Devices are pruned from state after 5 minutes of silence.

## Bell Notification & Acknowledgement

### Core Model

One user, one awareness. Bell state is global — not per-device.

### Detection

Coordinator polls `tmux display-message -t {name} -p "#{window_bell_flag}"` alongside `capture-pane` every 2s. Transitions from false → true increment `unseen_count` and set `last_fired_at`.

### Clear Rule

A bell clears globally (on every device) when:

1. That session is open **full-screen** on a device (`view_mode == "fullscreen"`), AND
2. That device has had a **user interaction** within the last **60 seconds** (`last_interaction_at > now - 60s`)

If laptop is sitting idle on session #1, bells persist everywhere. When user taps session #1 on phone and interacts, it clears on all devices.

### `unseen_count`

Tracks rapid successive bells — shows "3 bells while you were away."

### Visual Indicators

- **Overview tiles:** Amber (#E8A040) pulsing dot/border on tiles with `unseen_count > 0`
- **Mobile list:** Amber badge next to session name; bell sessions sort to top automatically

### Browser Notifications API

On first load, request permission. When poll cycle detects bell transition (false → true), fire `new Notification("Activity in: " + name)`. Shows in OS notification center even when tab is backgrounded. Works on mobile when added to home screen (iOS Safari 16.4+, Android Chrome).

## UI/UX Design

### Session Tile Information Hierarchy

1. **Terminal content** (~90% of tile area) — the `<pre>` IS the tile
2. **Bell indicator** — amber (#E8A040) pulsing dot, top-right of header. Only chrome that interrupts scanning.
3. **Session name** — top-left, 12-13px, medium weight
4. **Last activity time** — top-right, 11-12px, dim. "2s ago" / "5m ago"

### Responsive Breakpoints

| Viewport | Columns | Notes |
|----------|---------|-------|
| < 600px | 1 (list) | Mobile priority list |
| 600-899px | 1 | Single-column tiles |
| 900-1199px | 2 | NOT 768px — at 768px with 2 columns, monospace tiles are only ~26 chars wide (unreadable) |
| 1200px+ | 4 | Full grid |

### Session Switcher

**Desktop:** Command palette triggered by `Ctrl+K`. Arrow keys + number keys 1-9 for navigation, type to fuzzy-filter by name, `G` to return to grid. No sidebar — sidebars steal terminal columns.

**Mobile:** Bottom sheet triggered by floating pill button (48x48px, bottom-right, semi-transparent when idle). 56px row height. Dismiss via swipe-down or tap outside.

### Connection Status

Minimal inline indicators — no persistent status bar. Polling freshness shown via tile timestamp staleness. WebSocket status shown only when degraded (brief "reconnecting..." overlay on the active terminal).

### xterm.js on Mobile

- Use `visualViewport` API (not `window.innerHeight`) to resize terminal rows when keyboard opens
- Never let terminal scroll behind keyboard — pin to fill visual viewport exactly
- `scrollback`: 500 rows on mobile (vs 5000 on desktop)
- Hint toward landscape in expanded mode (~80+ columns)

### PWA Configuration

- `display: standalone` — removes browser chrome
- `theme-color` matched to dark theme — prevents white flash on launch
- `orientation: any` — don't lock orientation
- `apple-mobile-web-app-capable` + `apple-mobile-web-app-status-bar-style: black-translucent` for iOS
- Suppress pinch-zoom on terminal view only

### Accessibility

- `prefers-reduced-motion: reduce` — respect for activity pulse animations
- Arrow keys to navigate grid tiles, Enter to expand, Escape to return
- Focus moves to xterm.js when session expands; returns to tile when closed

## Error Handling

| Scenario | Behavior |
|----------|----------|
| **Session disappears between polls** | Dropped from grid on next poll. If user was connected, ttyd exits, WebSocket closes, frontend returns to overview grid with "session ended" message. |
| **ttyd fails to start** | `POST /connect` returns error JSON. Frontend shows toast: "Couldn't connect to session 'work'". No crashed state. |
| **Orphaned ttyd on coordinator restart** | Startup routine reads PID file, checks if process is still running, kills any orphaned ttyd before registering new state. |
| **WebSocket drops mid-session** | ttyd reconnects on brief drops (ping/pong). Longer outages show "reconnecting..." overlay. On reconnect, frontend re-calls `/connect` for fresh ttyd process. |
| **No tmux sessions running** | Empty state: "No active tmux sessions — will update automatically." |
| **Stale bell state on coordinator restart** | Bell state in memory only; restart treats all bells as fresh. Worst case: see a bell already addressed. No missed bells. |
| **Concurrent state writes** | `asyncio` single write lock serializes all async paths before `os.replace()`. |

## Testing Strategy

### 1. Coordinator Unit Tests (Python)

Session enumeration logic, ttyd process lifecycle (spawn, kill, orphan detection), state file atomic writes, bell detection and acknowledgement logic, active-device gate rule. Mock `subprocess` calls to tmux. No tmux or browser required. Fast, always runnable.

### 2. Integration Tests (Coordinator + tmux)

Spin up real tmux server in test environment (`tmux -L test-server`), create named sessions, verify full polling cycle — capture-pane output, bell flag detection and clearing, ttyd spawns on correct session, state file updated atomically. Requires tmux installed, no browser.

### 3. Browser/E2E Tests

Verify dashboard renders, tiles appear and update, bell indicators show/clear correctly, session expansion works, session switcher opens, mobile layout at 375px and 768px viewport widths.

### Manual Smoke Test Checklist

Run after each deploy:

- [ ] Open two browser tabs; verify state is consistent across both
- [ ] Close browser, reopen on different device; verify state restored correctly
- [ ] Trigger tmux bell with `printf '\a'`; verify appears on non-focused device within one poll cycle
- [ ] Let laptop sit idle on session #1 for 90s; trigger bell in session #1; verify mobile shows bell indicator
- [ ] Actively use session #1 on phone; verify bell clears on laptop within one poll cycle

## Open Questions

1. **Escape key strategy** — double-Escape, `Ctrl+Shift+G`, or hold-duration? Affects daily usability in terminal-heavy workflows (Vim, less, fzf all use Escape).
2. **Tile density toggle** — single density or compact/comfortable toggle?
3. **Session reordering** — drag-to-reorder tiles, or always match tmux session creation order?
4. **Bell flag read behavior** — verify whether `tmux display-message -p "#{window_bell_flag}"` clears the flag or only reads it. Must be tested empirically before implementing bell logic.
5. **Polling interval** — 2s chosen, but 4-5s may be imperceptible for overview scanning and halves subprocess load. Worth benchmarking.

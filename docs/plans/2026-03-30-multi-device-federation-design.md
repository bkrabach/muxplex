# Multi-Device Federation Design

## Goal

Add multi-device federation to muxplex so that any instance can aggregate and display tmux sessions from multiple machines running muxplex, with flexible view options, on a single dashboard.

## Background

muxplex currently operates as a single-machine tool — it shows tmux sessions from the host it runs on. Users who work across multiple machines (laptop, workstation, dev server) have no way to see all their sessions in one place. Federation lets any muxplex instance pull in sessions from other instances and display them in a unified grid, without requiring a dedicated aggregator or centralized server.

## Approach

Peer-to-peer aggregation: any muxplex instance can be configured to fetch sessions from any other instance. The browser fetches directly from each remote instance (no server-side proxy), leveraging the fact that the user's browser has direct network access to all their machines. Each remote instance remains completely unaware it's being aggregated — it just serves its normal API to an authenticated browser.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Browser (aggregator UI on Laptop:8088)             │
│                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ Local source │  │ Remote:      │  │ Remote:    │ │
│  │ (Laptop)     │  │ Workstation  │  │ Dev Server │ │
│  │ url: ""      │  │ url: work:   │  │ url: dev:  │ │
│  │              │  │      8088    │  │      8088  │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬─────┘ │
│         │                 │                 │       │
│         ▼                 ▼                 ▼       │
│  ┌─────────────────────────────────────────────────┐│
│  │          Merged session grid (app.js)           ││
│  │   Sessions tagged with deviceName + sourceUrl   ││
│  │   View modes: flat / grouped / filtered         ││
│  └─────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────┘

     │ fetch              │ fetch              │ fetch
     ▼                    ▼                    ▼
┌──────────┐       ┌──────────────┐     ┌────────────┐
│ Laptop   │       │ Workstation  │     │ Dev Server │
│ :8088    │       │ :8088        │     │ :8088      │
│ (local)  │       │ (remote)     │     │ (remote)   │
└──────────┘       └──────────────┘     └────────────┘
```

Key architectural properties:
- **No server-side proxy** — the browser connects directly to each instance for both REST and WebSocket traffic
- **Each instance is independent** — remote instances don't know they're being aggregated
- **Auth is per-instance** — the browser authenticates to each instance independently using that instance's existing auth
- **CORS enabled on all instances** — required since the browser makes cross-origin requests

## Components

### 1. Backend — Settings & Remote Instance Registry

**Files:** `settings.py`, `main.py`

The existing `settings.json` schema (`~/.config/muxplex/settings.json`) gets two new keys:

```json
{
  "sort_order": "alphabetical",
  "hidden_sessions": [],
  "remote_instances": [
    { "url": "http://workstation:8088", "name": "Workstation" },
    { "url": "https://devserver:8088", "name": "Dev Server" }
  ],
  "device_name": "Laptop"
}
```

- `remote_instances` — list of remote muxplex URLs + display names. Managed via the existing `GET/PATCH /api/settings` endpoints. No new routes needed for CRUD.
- `device_name` — this instance's own display name (shown on other instances that aggregate it). Defaults to the system hostname if not set.

The existing `settings.py` already has a defaults-merge pattern (`load_settings` merges saved values over defaults), so adding these fields is straightforward — just extend the defaults dict.

**New route:** `GET /api/instance-info` — returns `{ "name": "Laptop", "version": "1.0.0" }`. This is what remote instances call to discover a peer's display name and verify reachability. Lightweight, no auth required (public metadata, like a health check).

**CORS middleware:** A simple CORS middleware added to FastAPI that allows requests from any origin. These are private network tools, not public APIs — permissive CORS is appropriate.

### 2. Frontend — Multi-Origin API Layer

**File:** `app.js`

Currently `app.js` makes all API calls to the same origin (relative URLs like `/api/sessions`). The core change is introducing a concept of "sources" — each source is an origin URL the frontend fetches from.

**Source model:**

```javascript
sources = [
  { url: "",                  name: "Laptop",      type: "local"  },
  { url: "http://work:8088",  name: "Workstation",  type: "remote" },
  { url: "https://dev:8088",  name: "Dev Server",   type: "remote" }
]
```

The local instance is always source `""` (relative URLs, same as today). Remote sources are absolute URLs. On startup, the frontend fetches `/api/settings` to get `remote_instances`, then builds the sources list.

**Polling loop change:** The existing 2-second poll calls `GET /api/sessions` for snapshots. The new loop calls each source's `/api/sessions` endpoint in parallel (`Promise.all`), then merges results. Each session object gets tagged with its source's `name` and `url` so the renderer knows where it came from.

**Error isolation:** If a remote source fails (network timeout, auth required), that source is marked `status: "unreachable"` — the rest of the grid continues to work. Failed sources get retried on the next poll cycle with exponential backoff (2s → 4s → 8s → cap at 30s) to avoid hammering a dead host.

### 3. Frontend — View Modes & Device Tagging

**File:** `app.js`

Every session object in the frontend gets two new fields from the merge step:
- `deviceName` — e.g. "Laptop", "Workstation"
- `sourceUrl` — e.g. `""` (local), `"http://work:8088"` (remote)

**Tile rendering:** Each grid tile gets a small device tag badge — a subtle label in the corner showing the device name. For the local instance, this is only shown when remote instances are configured (no point labeling everything "Local" when there's only one source).

**Three view modes:**

| Mode | Behavior |
|------|----------|
| **Flat** (default) | All sessions in a single grid, sorted by the existing sort preference. Device tags on each tile. |
| **Grouped** | Sessions grouped under device name headers ("Laptop", "Workstation", etc.). Within each group, same sort order applies. |
| **Filtered** | A dropdown/pill bar at the top: "All" \| "Laptop" \| "Workstation" \| "Dev Server". Selecting one shows only that device's sessions. "All" is the flat view. |

**View preference storage:** A new setting `grid_view_mode` with values `flat | grouped | filtered`. The user chooses where this preference lives:
- A display setting `view_preference_scope` in localStorage: `"local"` (default) or `"server"`.
- When `"local"` → `grid_view_mode` stored in localStorage (per-browser).
- When `"server"` → `grid_view_mode` stored via `PATCH /api/settings` (shared across browsers).

The sidebar (session list in fullscreen mode) also gets device grouping — sessions listed under device name headers, matching whatever view mode is active.

### 4. Frontend — Remote Auth Flow

**File:** `app.js`

1. On startup / settings change, the frontend calls `GET /api/instance-info` on each remote URL (no auth required — public metadata endpoint).
2. If it gets a 200 → instance is reachable. Next, try `GET /api/sessions` with `credentials: "include"` (sends any existing cookies for that origin).
3. If sessions call returns 401/403 → the user isn't authenticated to that instance yet. Show the device tile in an "authenticate" state — a tile with the device name and a "Log in" button.
4. Clicking "Log in" opens that instance's `/login` page in a popup/new tab. The user authenticates normally (PAM or password). Once they get the auth cookie, they close the tab and the dashboard retries automatically on the next poll cycle.
5. If `instance-info` itself fails (network error, timeout) → mark the source as `"unreachable"`, show the greyed-out offline placeholder tile. Retry with backoff.

**Cookie lifetime:** Each instance's existing cookie expiry applies. The aggregator doesn't manage remote auth state — it just detects 401s and prompts re-login.

No new auth machinery on the backend — the remote instances don't know or care that they're being aggregated. They just serve their normal API to an authenticated browser.

### 5. Frontend — Terminal Connection for Remote Sessions

**File:** `terminal.js`

**Current behavior:** Click a tile → `POST /api/sessions/{name}/connect` (spawns ttyd) → WebSocket to `/terminal/ws` on the same origin.

**New behavior for remote sessions:** Click a remote tile →

1. Call `POST /api/sessions/{name}/connect` on the **remote** instance's URL (e.g. `http://work:8088/api/sessions/myproject/connect`). This tells the remote instance to spawn its ttyd.
2. Open xterm.js WebSocket directly to `ws://work:8088/terminal/ws`. The browser already has an auth cookie for that origin from the login flow.
3. The aggregator UI chrome (header, sidebar) stays on the local instance — only the terminal connection goes remote.

**Session switching:** When switching from a remote session back to a local one (or a different remote), the existing teardown logic in `terminal.js` disconnects the WebSocket and the stale-guard pattern prevents race conditions — this works unchanged. The only difference is the next WebSocket URL might be a different origin.

**Disconnect from remote session:** Calls DELETE or disconnect on the remote instance's API, same as today but with the remote URL.

### 6. Unreachable Instance Handling

**File:** `app.js`

**Detection:** On each poll cycle, the frontend tracks per-source status. If a fetch to `/api/instance-info` or `/api/sessions` fails (network error, timeout after ~5s), the source transitions to `unreachable`.

**Display:** An unreachable device shows a single greyed-out placeholder tile with:
- The device name ("Dev Server")
- An "Offline" badge
- Last-seen timestamp ("Last seen 5 min ago")

In grouped view mode, the device header itself gets dimmed with the offline indicator. In filtered view, the device still appears in the filter bar but with a visual indicator (dimmed text or a dot).

**Recovery:** Unreachable sources are retried with backoff (2s → 4s → 8s → capped at 30s). When a source comes back, it transitions to either `authenticated` (sessions load normally) or `auth_required` (got a 401), and the grid updates on the next cycle. No manual refresh needed.

No toasts or banners — the tile state itself communicates the problem. Avoids notification fatigue when a machine is intentionally powered off.

### 7. Cleanup

Housekeeping changes bundled with this work:

1. **Remove `Caddyfile`** — dead artifact. The WebSocket proxy is fully built into FastAPI.
2. **Remove `requirements.txt`** — contains only a comment pointing to `pyproject.toml`.
3. **Expand `test_ws_proxy.py`** — currently 28 lines. Needs tests for:
   - Bidirectional message relay (browser → ttyd and ttyd → browser)
   - Connection close propagation (one side closes, other side gets closed)
   - Auth rejection (unauthenticated WebSocket gets 4001 close code)
   - Error handling (ttyd unreachable, connection drops mid-session)
   - Concurrent sessions (two WebSocket proxies active simultaneously)

   These tests mock the ttyd WebSocket endpoint and use FastAPI's TestClient WebSocket support, consistent with existing test patterns.

## Data Flow

### Poll cycle (every 2 seconds)

```
1. For each source in parallel:
   ├─ GET {source.url}/api/sessions
   ├─ On success: tag each session with { deviceName, sourceUrl }
   ├─ On 401/403: mark source as "auth_required"
   └─ On network error: mark source as "unreachable", apply backoff

2. Merge all successful responses into a single session list

3. Render grid based on active view mode:
   ├─ flat:     sort all sessions together
   ├─ grouped:  bucket by deviceName, sort within buckets
   └─ filtered: show only selected device's sessions
```

### Terminal connection (on tile click)

```
1. Determine source URL from session's sourceUrl field
2. POST {sourceUrl}/api/sessions/{name}/connect  →  spawns ttyd on that host
3. Open WebSocket to ws://{sourceUrl}/terminal/ws
4. xterm.js ↔ WebSocket ↔ remote ttyd ↔ remote tmux
5. On disconnect: close WebSocket, call disconnect on sourceUrl
```

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Remote instance unreachable | Greyed-out placeholder tile, retry with exponential backoff (2s→4s→8s→30s cap) |
| Remote instance returns 401 | "Log in" button tile, user authenticates in popup, auto-retry on next poll |
| Remote instance returns 500 | Treated same as unreachable — placeholder tile, retry with backoff |
| Network timeout during poll | 5-second timeout per source, other sources unaffected |
| Remote WebSocket drops mid-session | Existing terminal.js teardown handles this — user sees disconnect, can reconnect |
| Remote ttyd fails to spawn | Connect endpoint returns error, surfaced to user same as local ttyd failure |
| CORS misconfigured on remote | Fetch fails with network error — shows as unreachable, user checks remote instance config |

## Testing Strategy

### WebSocket proxy tests (cleanup — `test_ws_proxy.py`)

- Bidirectional message relay (browser → ttyd and ttyd → browser)
- Connection close propagation (one side closes, other side gets closed)
- Auth rejection (unauthenticated WebSocket gets 4001 close code)
- Error handling (ttyd unreachable, connection drops mid-session)
- Concurrent sessions (two WebSocket proxies active simultaneously)

All tests mock the ttyd WebSocket endpoint and use FastAPI's TestClient WebSocket support.

### Federation-specific tests (new)

- Settings schema: `remote_instances` and `device_name` round-trip through load/save
- `/api/instance-info` returns correct name and version, requires no auth
- CORS middleware allows cross-origin requests
- Frontend source merging logic (unit tests if extracted to a module)

### Manual integration testing

- Two muxplex instances on different ports/machines, one aggregating the other
- Auth flow: unauthenticated remote → "Log in" tile → authenticate → sessions appear
- Unreachable handling: stop remote instance → offline tile → restart → auto-recovery
- Terminal: click remote tile → interactive terminal on remote host
- View modes: flat/grouped/filtered all render correctly with mixed sources

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Aggregation model | Any instance can aggregate (peer-to-peer) | Natural given existing architecture; no dedicated aggregator needed |
| Terminal connection | Browser connects directly to remote WebSocket | No proxy overhead; browser already has direct access per prerequisite |
| Auth | Browser authenticates to each instance independently | No new auth machinery; each instance keeps its own auth unchanged |
| Discovery | Manual config via settings (not auto-discovery) | Simple, explicit, works across any network topology |
| CORS | Allow all origins | Private network tool; no public API exposure |
| View preference storage | User's choice: localStorage or server-side | Maximum flexibility per user preference |

## Open Questions

None — all design decisions resolved during design review.

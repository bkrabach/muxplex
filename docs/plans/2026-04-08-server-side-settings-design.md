# Consolidate Settings Server-Side Design

## Goal

Move all user-configurable settings from browser localStorage to server-side `~/.config/muxplex/settings.json`, eliminating the split storage model. After this change, the only remaining localStorage usage is `tmux-web-device-id` (per-browser device identity for the heartbeat system, not a setting).

## Background

Muxplex currently splits settings across two storage layers:

- **Server-side** (`~/.config/muxplex/settings.json`): Infrastructure and session behavior settings (host, port, auth, sort_order, federation config, etc.) -- 17 keys total.
- **Client-side** (browser localStorage): Display/UX preferences stored across 3 keys:
  - `muxplex.display` -- fontSize, gridColumns, bellSound, hoverPreviewDelay, viewMode, showDeviceBadges, showHoverPreview, activityIndicator, gridViewMode, notificationPermission
  - `muxplex.sidebarOpen` -- sidebar toggle state
  - `tmux-web-device-id` -- per-browser device identity (not a setting)

This split complicates the mental model and means display preferences don't roam with the server -- if you reinstall your browser or clear localStorage, your preferences are lost even though the server is untouched.

## Approach

**Flat keys (Approach A)** -- add 10 new keys directly as flat top-level entries in `DEFAULT_SETTINGS` in `settings.py`.

This requires zero changes to `load_settings()`, `save_settings()`, or `patch_settings()` -- they all iterate `DEFAULT_SETTINGS` keys, so new keys work automatically through the existing `GET/PATCH /api/settings` API.

### Alternatives Considered

- **Nested `display` sub-object**: Would require deep-merge logic in `patch_settings()` and frontend changes to `patchServerSetting()`. More code for the same result. Rejected.
- **Separate `display.json` file**: New file, new API endpoints, new load/save functions. Over-engineered for a single-user tmux UI. Rejected.

## Architecture

### Multi-Device / Federation Settings Model

Each muxplex server owns its own `settings.json`. Federation is for session aggregation, not config replication. No settings sync across servers.

When browsing through Server A (the aggregator), all sessions -- local and federated -- render using Server A's display settings. Server B's settings are irrelevant; it only provides raw session data and WebSocket proxying. Server B's settings only matter when someone browses directly to Server B, which is a separate context where different settings may be desirable (e.g., different font size for different monitors).

Syncing would introduce disproportionate complexity (conflict resolution, partial connectivity, auth scope expansion, bootstrap problem) for minimal UX benefit in a single-user tool.

### Data Flow

```
Browser ──GET /api/settings──> Server A (settings.json)
   │                              │
   │  _serverSettings cache       │  fontSize: 16, gridColumns: 3, ...
   │  (in-memory, immediate)      │  + all infra/session settings
   │                              │
   │──PATCH /api/settings──>      │  patch_settings() merges into file
   │  (fire-and-forget)           │
   │                              │
   │                              ├── Local tmux sessions
   │                              ├── Server B sessions (federation proxy)
   │                              └── Server C sessions (federation proxy)
   │
   │  All sessions render using Server A's display settings.
   │  Server B/C settings are irrelevant from this browser.
```

## Components

### Server-Side Changes (`muxplex/settings.py`)

Add 10 new keys to `DEFAULT_SETTINGS`:

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
"sidebarOpen": None,       # None = auto-detect from screen width
```

`sidebarOpen` defaults to `None` (not `True`/`False`) so the frontend can distinguish "never set by user" from "explicitly set" and fall back to screen-width detection (open at >= 960px, closed below) on first load.

**No changes needed to:**

- `load_settings()`, `save_settings()`, `patch_settings()` -- they iterate `DEFAULT_SETTINGS` keys automatically.
- `muxplex/main.py` -- `GET/PATCH /api/settings` endpoints already handle any keys in `DEFAULT_SETTINGS`. None of the new keys need redaction (no sensitive data).

**Intentionally excluded:** `notificationPermission` is NOT added. The browser `Notification.permission` API is the source of truth; the cached localStorage value was never meaningfully consumed.

### Frontend Changes (`muxplex/frontend/app.js`)

#### Startup Sequence

Move `await loadServerSettings()` to the top of `DOMContentLoaded`, before first render. Currently it's called lazily when opening the settings panel. Display settings like `fontSize` and `gridColumns` affect rendering, so they must be available before anything renders (~50ms on localhost; page is blank until `restoreState` anyway).

#### Replace the Storage Layer

- **Delete** `DISPLAY_SETTINGS_KEY` constant (`'muxplex.display'`) and `SIDEBAR_KEY` constant (`'muxplex.sidebarOpen'`).
- **Delete** `loadDisplaySettings()` function (reads from localStorage).
- **Delete** `saveDisplaySettings()` function (writes to localStorage).
- **Add** `getDisplaySettings()` function that reads from `_serverSettings` cache, falls back to `DISPLAY_DEFAULTS` for anything missing or before the initial fetch.
- **Remove** `notificationPermission` from `DISPLAY_DEFAULTS` (keep the runtime `_notificationPermission` variable for the browser API state).

#### Update All Callsites

- All ~16 `loadDisplaySettings()` calls become `getDisplaySettings()` (pure rename).
- All ~6 `saveDisplaySettings(ds)` calls become `patchServerSetting(key, value)` calls with an in-memory `_serverSettings` cache update so the UI stays responsive without waiting for the server round-trip.

#### Sidebar Functions

Rewrite `initSidebar()`, `toggleSidebar()`, and `bindSidebarClickAway()`:

- Read `_serverSettings.sidebarOpen` instead of localStorage.
- Write via `patchServerSetting('sidebarOpen', isOpen)` (fire-and-forget, non-blocking).
- Keep the `None` -> screen-width auto-detect logic for first-time users.

#### Bonus Fix: `auto_open` -> `auto_open_created`

The `openSettings()` function reads the server setting as `ss.auto_open` but the canonical server-side key is `auto_open_created`. Fix this alignment as part of this work since we're already touching that code path.

#### Only Remaining localStorage Usage

`tmux-web-device-id` in `initDeviceId()` (3 occurrences) -- untouched. This is per-browser device identity for the heartbeat/bell system, not a user setting.

## Error Handling

### Race on Startup

`loadServerSettings()` is `await`ed before first render, so display settings are always available. If the fetch fails (server down, network issue), `getDisplaySettings()` falls back to `DISPLAY_DEFAULTS` -- the app still loads with sensible defaults, just not personalized.

### Fire-and-Forget Writes

Setting changes call `patchServerSetting()` which updates the in-memory `_serverSettings` cache immediately (so the UI is responsive), then fires the `PATCH /api/settings` request. If the PATCH fails, a "Failed to save setting" toast appears. The in-memory cache stays updated for the current session, but the change won't persist across page refreshes. This matches the current behavior for existing server settings.

### `sidebarOpen: None` Auto-Detect

On first load (or after a settings reset), `sidebarOpen` is `None`. The frontend detects this and uses screen-width heuristic (open at >= 960px, closed below). Once the user explicitly toggles the sidebar, it writes `true`/`false` and the auto-detect is bypassed from then on.

### No Migration

No migration of existing localStorage values. Users get defaults and set preferences again. Old localStorage keys (`muxplex.display`, `muxplex.sidebarOpen`) become orphans -- harmless, ignored by the new code.

## Testing Strategy

### Server-Side Tests (`muxplex/tests/test_settings.py`)

- One test verifying the 10 new keys exist in `DEFAULT_SETTINGS` with correct default values.
- One test verifying display keys round-trip through `patch_settings()` (write then read back).
- Existing test infrastructure for `load_settings`/`save_settings` already covers the mechanics.

### Frontend Tests (`muxplex/frontend/tests/test_app.mjs`)

- Update any existing tests that reference `loadDisplaySettings` or `saveDisplaySettings` to use `getDisplaySettings`.
- Mock `_serverSettings` instead of `localStorage` for display settings tests.

### Verification Checklist (manual, post-implementation)

1. `grep -r 'localStorage' app.js` returns only `tmux-web-device-id` references.
2. `grep -r 'DISPLAY_SETTINGS_KEY\|SIDEBAR_KEY\|muxplex\.display\|muxplex\.sidebarOpen' app.js` returns zero matches.
3. `grep -r 'loadDisplaySettings\|saveDisplaySettings' app.js` returns zero matches.
4. `pytest muxplex/tests/test_settings.py` passes.
5. Manual: change font size -> refresh -> persists.
6. Manual: toggle sidebar -> refresh -> persists.
7. `cat ~/.config/muxplex/settings.json` shows the new display keys.

## Open Questions

None -- all design decisions have been validated.

## Future Work

- **Federation settings sync**: Investigate syncing user preferences (fontSize, sort_order, etc.) across federated servers. Deferred -- each server owns its own settings for now. Standard sysadmin solutions (copy the file, ansible, dotfiles repo) work fine in the meantime.

## Compatibility

Verified against muxplex v0.2.0 (commit `1b5207b`). The v0.2.0 release added a `DELETE /api/federation/{remote_id}/sessions/{session_name}` endpoint and updated `killSession()` -- neither affects settings storage. `DEFAULT_SETTINGS` in `settings.py` is stable at 17 keys; none of the new display keys conflict with existing keys.

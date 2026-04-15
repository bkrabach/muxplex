# Views Design

## Goal

Add user-defined "Views" to muxplex — curated collections of sessions that let users focus on subsets of their terminal sessions across devices, replacing the current `filtered` gridViewMode with something strictly more powerful.

## Background

Muxplex currently organizes sessions by device name (`grouped` mode), a flat sorted grid (`flat` mode), or a per-device filter pill bar (`filtered` mode). There is no way to create a custom collection of sessions that spans devices. Users working on projects that involve sessions across multiple machines (a hobby project on a desktop and a Pi, a work project on a laptop and a server) have no way to create a focused view of just those sessions.

The existing `hidden_sessions` feature is a flat list of session names buried in the Settings panel. Hiding by name means hiding "main" hides it on all devices, and there is no way to see what you've hidden without opening Settings.

Views solve both problems: user-curated session sets for focused work, and a first-class "Hidden" destination that replaces the buried settings checkbox.

## Prerequisite: Stable Device Identity

The current `remoteId` is a positional array index from `enumerate(remote_instances)` — it shifts when the list is reordered. This is a pre-existing fragility that views would make worse, since view session lists need stable keys that work identically regardless of which device is running the web session. This prerequisite fixes device identity system-wide.

### Identity File

Each muxplex instance gets a `device_id` (UUID v4) stored in a new file:

**`~/.config/muxplex/identity.json`**
```json
{
  "device_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

- Stored outside the federation settings sync boundary entirely. Never synced, never overwritten by settings propagation.
- Generated once on first startup via `uuid.uuid4()` if the file is absent.
- Never regenerated automatically. Not user-editable through the UI.

### Session Key Format

All session keys change from `"remoteId:name"` (positional index) to `"device_id:name"` uniformly — for both local AND remote sessions. No asymmetry. The local device knows its own `device_id` at startup, so local sessions use `"local_device_id:session_name"` just like remotes.

| Session Type | Old Format | New Format |
|---|---|---|
| Local | `"name"` | `"device_id:name"` |
| Remote | `"remoteId:name"` (e.g. `"1:dev-server"`) | `"device_id:name"` (e.g. `"abc-123:dev-server"`) |

### Remote Instance Changes

- `remote_instances` entries gain a `device_id` field, populated by querying the remote's `/api/instance-info` endpoint.
- `/api/instance-info` response extended to include `device_id`:

```json
{
  "name": "my-laptop",
  "device_id": "a1b2c3d4-...",
  "version": "0.3.7",
  "federation_enabled": true
}
```

- `active_remote_id` in `state.json` changes from integer index to `device_id` string.
- All frontend session key construction/parsing (`sessionKey`, `data-remote-id`, API URL construction, bell tracking, etc.) updated to use `device_id` instead of positional index.
- All backend federation proxy endpoints updated to accept `device_id`-based lookup instead of integer index.

### Device-ID Change Detection

If a remote's `/api/instance-info` returns a different `device_id` than what's stored in `remote_instances`, log a warning. Do NOT silently update — require operator acknowledgment before accepting the change.

### CLI Addition

`muxplex --reset-device-id` — generates a new UUID, warns about orphaned session keys in views and `hidden_sessions`. For the "I copied my dotfiles to a new machine" scenario.

### Config Path Alignment

Migrate state storage from `~/.local/share/tmux-web/` to `~/.local/share/muxplex/` (legacy `tmux-web` name). Handle both paths during transition: check new path first, fall back to old path, migrate on first write.

### Migration

Migration is incremental and per-remote, NOT blocking:

1. On startup, check each remote instance entry: if it lacks a `device_id`, query its `/api/instance-info`.
2. If the remote is reachable, store its `device_id` and rewrite any session keys in state/settings that used the old positional index.
3. If unreachable, skip that remote. Retry on next startup.
4. Track migration status per-remote so a crash mid-migration doesn't re-run already-migrated remotes.
5. Support both old `"remoteId:name"` and new `"device_id:name"` key formats during the transition window.

## Data Model

### Settings (`~/.config/muxplex/settings.json`)

New and changed fields:

```json
{
  "views": [
    { "name": "Work Project", "sessions": ["abc-123:dev-server", "def-456:monitoring"] },
    { "name": "Hobby", "sessions": ["abc-123:3d-printer", "abc-123:dev-server"] }
  ],
  "hidden_sessions": ["abc-123:old-experiment", "def-456:stale-build"]
}
```

- `views` is an ordered list of `{name: string, sessions: string[]}` objects. Array order = dropdown order.
- Session keys use `device_id:name` format uniformly.
- `views` and `hidden_sessions` are in `SYNCABLE_KEYS` (synced across federation).
- The old `gridViewMode` setting's `"filtered"` value is removed. Only `"flat"` and `"grouped"` remain.

### State (`~/.local/share/muxplex/state.json`)

New field:

```json
{
  "active_view": "all"
}
```

- `active_view` is `"all"` (default), `"hidden"`, or a view name string.
- Stored in `state.json`, NOT `settings.json` — per-device, NOT synced via federation. Prevents switching views on one device from changing the view on another device.
- If `active_view` references a view name that no longer exists (deleted on another device via federation sync), fall back to `"all"`.

### View Name Validation

- Non-empty, max 30 characters, whitespace-trimmed, unique among user views.
- Names `"all"` and `"hidden"` are reserved (case-insensitive).

## Architecture

### Three View Tiers

| Tier | Name | Stored? | Editable? | Behavior |
|---|---|---|---|---|
| System | "All" | No — virtual | No | Shows every session NOT in `hidden_sessions` |
| User | e.g. "Work", "Hobby" | Yes — `views[]` in settings | Full CRUD | User-curated session sets |
| System | "Hidden" | No — virtual | Sessions can be unhidden from here | Shows everything in `hidden_sessions` |

"All" and "Hidden" are never stored in the `views` array. "All" is computed as "everything not in `hidden_sessions`". "Hidden" is computed as "everything in `hidden_sessions`".

### Session Lifecycle Within Views

- **New sessions auto-appear in "All"** — they're not hidden, so they're visible. No action needed.
- **New sessions created via the `+` button while a user view is active** — auto-added to that view AND visible in "All". Only applies to sessions created through the muxplex UI (the `+` button), NOT sessions that appear on federated remotes during a poll cycle.
- **Hiding a session** — removes it from ALL user views, adds to `hidden_sessions`. It now only appears in the "Hidden" view.
- **Unhiding a session** (from Hidden view) — removes from `hidden_sessions`, reappears in "All". Does NOT auto-restore to previous user views.
- **Adding a hidden session to a user view** (from the Add Sessions panel) — removes from `hidden_sessions`, adds to the view. Now visible in "All" and that view.
- **Deleting a view** — if the deleted view is the active view, fall back to "All". Sessions that were in the deleted view remain wherever else they are (in "All" and any other views). They don't become orphaned.
- **Killing a session** (via flyout) — removes it from all views and `hidden_sessions` automatically (the session no longer exists).

### Mutual Exclusion Invariant

`hidden_sessions` and any `views[].sessions` never share a session key. Every write path enforces this:

- Hiding removes from all views.
- Adding a hidden session to a view removes from hidden.
- An invariant-repair function runs after every federation settings sync. If a session key appears in both `hidden_sessions` and any view (possible due to concurrent writes on different devices), remove it from `hidden_sessions` (favor visibility over hiding).

### Stale Session Keys

When a view's session list contains a key for a session that no longer exists (tmux session died, remote unreachable), the session is silently omitted from rendering. The key stays in the view's session list so it reappears if the session comes back (e.g., tmux session recreated with the same name on the same device).

### Layout Mode Changes

- `gridViewMode` retains only `"flat"` and `"grouped"` values. The `"filtered"` value is removed entirely.
- `flat`/`grouped` is a global toggle that applies within whatever view is active. "Grouped" groups sessions by device within the current view's filtered session set.
- The current device filter pill bar (rendered when `gridViewMode === "filtered"`) is removed from the UI.

## UI: Header Dropdown (View Switcher)

### Location

In the header bar, between the wordmark and the action buttons. Shows the current view name as a clickable label (e.g., "All Sessions ▾").

### Dropdown Structure

```
┌─────────────────────────┐
│  ✓ All Sessions         │  ← always first, checkmark on active
│─────────────────────────│
│    Work Project          │  ← user views, in array order
│    Hobby                 │
│    Monitoring            │
│─────────────────────────│
│    Hidden (3)            │  ← always last, shows count of hidden sessions
│─────────────────────────│
│  + New View              │  ← inline text input on click
│  Manage Views...         │  ← opens settings tab
└─────────────────────────┘
```

"Hidden" shows the count of `hidden_sessions` in parentheses.

### Keyboard

- Backtick (`` ` ``) opens the dropdown — ONLY on the grid overview page, NOT in fullscreen terminal mode (backtick is a real character users type in shells).
- Arrow keys navigate, Enter selects, Escape closes.
- Number keys `1`-`9` jump directly: 1=All, 2–8=up to 7 user views, 9=Hidden. Views beyond position 7 don't have shortcuts. No hard cap on view count; shortcuts just stop at 9.

### "New View" Flow

Click `+ New View` → inline text input appears in the dropdown. Type name, press Enter → empty view created, dropdown closes, switches to the new view.

### "Manage Views..."

Opens a new "Views" tab in the existing settings dialog:

- List of user views with inline rename (click name to edit).
- Up/down arrow buttons for reorder (no drag-to-reorder — ship with arrow buttons, add drag later if users ask).
- Delete with inline confirmation: clicking delete replaces the button with "Sure? [Yes] [No]" in place — no nested modal.

### Fullscreen Sidebar

The sidebar header shows the current view name with a small `▾` that opens the same dropdown. Implemented as two independent DOM instances with a shared render function — not DOM teleportation. Allows view switching without leaving fullscreen.

### Mobile

Header label truncated if needed. Tapping opens a bottom sheet (consistent with existing mobile patterns) instead of a floating dropdown.

### Deleted Active View Fallback

If `active_view` references a view name that no longer exists (deleted on another device, or deleted via Manage Views), fall back to "All". Sessions that were in the deleted view remain in "All" and any other views they belong to.

## UI: Tile Flyout Menu

### Trigger

Always-visible `⋮` button in the top-right corner of every session tile. Click to open a floating menu.

### Accessibility

- `⋮` button: `aria-label="Session options"`, `aria-haspopup="true"`.
- Menu: `role="menu"` with `role="menuitem"` children.

### Event Handling

Use event delegation — one click listener on the tile container, not one per `⋮` button.

### Menu Items

Menu item sets are defined as a data map keyed by view type, not procedural if/else chains. Adding a new view type means adding an entry to the map.

**In the "All" view:**
```
┌─────────────────────────┐
│  Add to View...     ▸   │
│  Hide                    │
│─────────────────────────│
│  Kill Session            │
└─────────────────────────┘
```

**In a user-created view:**
```
┌─────────────────────────┐
│  Add to View...     ▸   │
│  Remove from [ViewName]  │
│  Hide                    │
│─────────────────────────│
│  Kill Session            │
└─────────────────────────┘
```

View name in "Remove from [ViewName]" is truncated to 20 characters with ellipsis; full name in the `title` attribute.

**In the "Hidden" view:**
```
┌─────────────────────────┐
│  Unhide                  │
│  Unhide & Add to View... │
│─────────────────────────│
│  Kill Session            │
└─────────────────────────┘
```

"Unhide & Add to View..." makes the dual action explicit — selecting a view from the submenu unhides the session AND adds it to that view.

### "Add to View" Submenu

Lists all user-created views (or all OTHER user views when in a user view). Views the session is already in are shown with a checkmark — clicking toggles membership. Clicking a view the session is not in adds it immediately (single PATCH). The flyout stays open after submenu actions so multiple views can be toggled.

### Kill Session Confirmation

Clicking "Kill Session" replaces the menu item inline with "Kill? [Yes] [No]". No timeout — stays until click-outside closes the menu. On error (network failure, session already dead), shows "Failed" in the menu item for 2 seconds then reverts. If the session dies while the confirmation is showing, the menu closes.

### Mobile

The `⋮` tap opens a bottom action sheet instead of a floating menu. For "Add to View...", tapping opens a full-height picker sheet with checkboxes for each view plus a "Done" button.

### Kill Session Transition

The existing kill-session UI location is removed when the flyout ships. The flyout is the single location for kill session.

### Z-index / Stacking Context

Known implementation hazard. The flyout must render above tiles, headers, and any other overlapping elements. Left to implementer to resolve with appropriate z-index layering.

## UI: Add Sessions Panel

### Entry Point

When in a user-created view, an "Add Sessions" affordance in the grid area opens the panel. Also reachable from the tile flyout's "Add to View..." submenu.

### Panel Design

An overlay panel showing all sessions NOT currently in the active view:

```
┌───────────────────────────────────┐
│  Add Sessions to "Work Project"   │
│───────────────────────────────────│
│  ☐ dev-server        [laptop]     │
│  ☐ api-logs          [pi]         │
│  ☐ build-runner      [desktop]    │
│  ☐ old-experiment    [laptop] dim │  ← hidden session, dimmed with badge
│     "This will make it visible"   │  ← inline disclosure
│───────────────────────────────────│
│           [Close]                 │
└───────────────────────────────────┘
```

### Behaviors

- **Immediate commit** — each checkbox change fires a PATCH immediately. No batch "Done" button; just a "Close" button. Same commit model as the flyout submenu.
- **Single flat list** — no two-section cutline. Hidden sessions appear dimmed with a small "hidden" badge. Selecting a hidden session shows a brief inline note: "This will make it visible again."
- **Device name** shown next to each session for disambiguation. Uses friendly device name from settings → falls back to hostname → falls back to truncated `device_id`. Shared utility function across all components.
- **List ordering** — alphabetical, grouped by device (consistent with the grid's "grouped" mode).
- **Empty state** — if all sessions are already in the view: "All sessions are already in this view."
- **Error handling** — if a PATCH fails, show a brief error toast ("Couldn't save — try again"). Panel stays open, checkbox reverts.

### Mobile

Panel becomes a full-screen sheet (same content, scrollable).

### System View Restriction

"All" and "Hidden" are system views — the Add Sessions panel is not available for them. To add sessions to a user view from "All", use the tile flyout → "Add to View..." submenu.

## Error Handling

### Network Failures

- **PATCH failures** (adding/removing from views, hiding, unhiding): UI element reverts to previous state. Brief error toast. No retry loop.
- **Kill session failure**: "Failed" text in the flyout menu item for 2 seconds, then reverts to "Kill Session".
- **Settings sync failure**: Existing 3-strike grace window for federation heartbeat applies. Views data is no different from other synced settings.

### Data Invariant Violations

- **Post-sync repair**: After every federation settings sync, run the mutual exclusion invariant check. If a session key appears in both `hidden_sessions` and any view, remove it from `hidden_sessions` (favor visibility).
- **Duplicate session keys in a view**: Deduplicate silently on load.
- **Invalid view names**: Reject at creation time via validation. If an invalid name arrives via federation sync, accept it (don't break sync) but flag it in the Manage Views panel.

### Migration Failures

- Per-remote migration is retried on each startup until successful.
- Both old and new key formats are supported during the transition window so partial migration doesn't break functionality.

## Testing Strategy

### Unit Tests

- View CRUD operations (create, rename, reorder, delete).
- Mutual exclusion invariant: hiding removes from views, adding to view unhides.
- Session key format migration (old positional → new `device_id`-based).
- View name validation (empty, too long, reserved names, duplicates).
- `active_view` fallback when referenced view is deleted.
- Stale session key handling (silently omitted from render, retained in data).
- Invariant repair after simulated concurrent edits.

### Integration Tests

- Federation sync round-trip: create a view on device A, verify it appears on device B.
- `active_view` isolation: switching views on device A does NOT change device B.
- Device identity: `identity.json` generated on first run, survives settings sync.
- Migration: start with old-format session keys, verify rewrite after remote becomes reachable.
- `--reset-device-id` CLI flag: generates new UUID, old session keys become stale.

### Manual / Exploratory

- Create views, switch between them, verify grid renders correct session subset.
- Hide/unhide sessions from various views, verify mutual exclusion.
- Kill session from flyout, verify removal from views and hidden.
- Create session via `+` while in user view, verify auto-add.
- Mobile: bottom sheets for dropdown and flyout, full-screen sheet for Add Sessions panel.
- Fullscreen sidebar: view switcher dropdown works without leaving fullscreen.

## Known Limitations

1. **Session renames break view membership.** `tmux rename-session` changes the name component of the session key. The old key becomes stale in views; the renamed session appears in "All" but not in any previous views. Rename detection would require tmux hooks or a separate identity layer — both out of scope.

2. **Federation sync is atomic on the `views` array.** Concurrent edits to different views on different devices may result in one set of changes being dropped (newer-wins on the whole array). Per-view keyed sync is the correct long-term solution but out of scope for the initial release.

3. **Number-key shortcuts stop at 9.** Users with more than 7 user views don't get keyboard shortcuts for the extras. No hard cap on view count; the shortcuts just stop.
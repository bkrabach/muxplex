# Federation State Propagation Design

## Goal

Make federation a seamless experience where settings, hidden sessions, and activity state propagate across all connected muxplex servers. After this work, a user browsing through any server sees a consistent, unified experience -- settings follow you, hidden sessions disappear everywhere, and bell/activity indicators clear correctly regardless of which server owns the session.

## Background

Muxplex federation currently aggregates tmux sessions from multiple servers into a single dashboard, but state management remains per-server:

- **Settings are local.** Changing `fontSize` on Server A has no effect on Server B. A user who accesses both must configure each independently.
- **Hidden sessions only apply locally.** Hiding a session on Server A only hides it on Server A's dashboard. The same session remains visible if browsing Server B directly, or if the session lives on Server B and is viewed via federation from A.
- **Bell/activity clearing doesn't cross federation boundaries.** When viewing a remote session through federation, the remote server doesn't know anyone is watching. Its `should_clear_bell()` never fires, so activity indicators persist indefinitely. Browser-level indicators (tab title count, favicon badge) also don't filter hidden sessions from their counts.
- **A JS falsy-0 bug** in `getVisibleSessions()` causes sessions from the first remote instance (`remoteId: 0`) to be incorrectly subject to the hidden filter.

## Approach

Two-phase implementation. Phase 1 fixes four targeted bugs that cause the most visible UX pain without any new protocol. Phase 2 adds a minimal P2P settings sync protocol. The phases are independent -- Phase 1 can ship and provide immediate value, Phase 2 builds on top.

The key architectural insight (from COE review): muxplex is one person with multiple servers, not a distributed multi-user system. This means:

- **Settings sync** uses full-document push with a single timestamp per server, not per-key CRDTs. Simple and correct for the single-user case.
- **Bells are not a sync problem.** The bell data already flows through `/api/federation/sessions` on every 2-second poll. The fix is extending the heartbeat/presence system to work across federation boundaries.
- **Hidden sessions** are just another settings key that flows through the settings sync. No special treatment needed.

## Architecture

```
Phase 1: Fix existing federation gaps (no new protocol)

  Browser ──heartbeat──> Server A ──poll cycle──> Server B
                                   (bell/clear)
  
  getVisibleSessions() fixed: s.remoteId == null (not !s.remoteId)
  updatePageTitle() / updateFaviconBadge() filter through getVisibleSessions()

Phase 2: P2P settings sync (new protocol, piggybacks on poll cycle)

  Server A ──GET /api/settings/sync──> Server B
           <── {settings, settings_updated_at} ──
  
  If remote newer: adopt remote settings locally
  If local newer:  PUT /api/settings/sync to remote
  If equal:        no action
  
  Timestamps converge → no sync loops
  Offline servers catch up automatically on reconnect
```

## Components

### Phase 1: Federation Bug Fixes

#### Fix 1: `getVisibleSessions()` Falsy-0 Bug

**File:** `app.js` (~line 533)

Change `!s.remoteId` to `s.remoteId == null`. Currently `remoteId: 0` (first remote instance) is treated as falsy in JavaScript, causing sessions from the first remote to be incorrectly subject to the hidden filter when their name appears in `hidden_sessions`. One-line fix.

#### Fix 2: Heartbeat-Driven Bell Clearing Across Federation

**File:** `main.py` (poll cycle in `_run_poll_cycle`)

The core problem: the browser's heartbeat only goes to Server A (the one the browser is connected to). When viewing a remote session on Server B in fullscreen, Server B doesn't know anyone is watching. Its `should_clear_bell()` never fires for federation viewers.

The fix: in the poll loop on Server A, when the local heartbeat indicates a device is viewing a remote session in fullscreen with recent interaction, Server A fires `POST /api/federation/{remote_id}/sessions/{session}/bell/clear` to the remote. This piggybacks on the existing 2-second poll cycle and the existing federation bell/clear endpoint -- no new API surface needed. The remote server's bell state updates, and the next federation sessions poll reflects the cleared bell.

#### Fix 3: Browser Indicators Filter Hidden Sessions

**File:** `app.js` (`updatePageTitle()` and `updateFaviconBadge()`)

Both functions currently count bells from `_currentSessions` raw. Change both to use `getVisibleSessions(_currentSessions)` before counting, so hidden sessions don't contribute to the `(N)` tab title or amber favicon dot.

#### Fix 4: Sustained Bell Clearing While Viewing Remote Sessions

Currently `bell/clear` fires once on `openSession()`. If Server B fires a new bell while the user is actively watching via federation, it won't auto-clear. Fix 2 handles this -- the poll-cycle-driven bell/clear fires every 2 seconds as long as the user is viewing the remote session, not just on initial open.

### Phase 2: Federation Settings Sync Protocol

#### Syncable vs Local-Only Keys

Defined as an allowlist constant `SYNCABLE_KEYS` in `settings.py`.

| Syncs (user experience preferences) | Never syncs (per-machine identity/infra) |
|---|---|
| `fontSize` | `host` |
| `hoverPreviewDelay` | `port` |
| `gridColumns` | `auth` |
| `bellSound` | `session_ttl` |
| `viewMode` | `tls_cert` |
| `showDeviceBadges` | `tls_key` |
| `showHoverPreview` | `device_name` |
| `activityIndicator` | `federation_key` |
| `gridViewMode` | `remote_instances` |
| `sidebarOpen` | `multi_device_enabled` |
| `sort_order` | `new_session_template` |
| `hidden_sessions` | `delete_session_template` |
| `default_session` | |
| `window_size_largest` | |
| `auto_open_created` | |

Rule of thumb: if the setting is about "how I want my muxplex experience to look/behave" it syncs. If it's about "what this specific machine is and how it connects" it doesn't. Command templates don't sync because different machines might have different tmux setups.

#### `settings_updated_at` Timestamp

**File:** `settings.py`

Add `settings_updated_at` to `DEFAULT_SETTINGS` (default: `0.0`). Whenever `patch_settings()` is called for a syncable key, this bumps to `time.time()`. This is the single version clock for last-write-wins conflict resolution.

Critical for sync loop prevention: when applying synced settings from a remote, set `settings_updated_at` to the **incoming** timestamp, not `time.time()`. This ensures timestamps converge and the next poll sees them as equal.

#### New API Endpoints

**`GET /api/settings/sync`** -- Returns `{settings: {syncable keys only}, settings_updated_at: float}`. Authenticated via federation Bearer token (same auth as existing federation endpoints).

**`PUT /api/settings/sync`** -- Accepts `{settings: {syncable keys only}, settings_updated_at: float}`. If the incoming `settings_updated_at` is newer than the local one, applies the incoming syncable keys and adopts the incoming timestamp. If local is newer, responds with 409 and the local state so the caller knows it lost. Returns the final state either way. Authenticated via federation Bearer token.

#### Sync Logic in Poll Cycle

**File:** `main.py` (`_run_poll_cycle`)

Piggybacks on the existing 2-second poll cycle. After fetching federation sessions from each reachable remote:

1. `GET /api/settings/sync` from remote
2. Compare remote `settings_updated_at` vs local `settings_updated_at`
3. If remote is newer: adopt remote syncable keys locally, update local timestamp to remote's timestamp
4. If local is newer: `PUT /api/settings/sync` to remote with local syncable keys + timestamp
5. If equal: no action

#### Frontend Impact

No frontend changes needed for sync itself. The frontend already reads from `GET /api/settings` and the `_serverSettings` cache. When sync updates the server's `settings.json`, the next `loadServerSettings()` call (or the next time the user opens the settings dialog) picks up the synced values.

## Data Flow

### Phase 1: Bell Clearing Across Federation

```
1. User opens remote session (Server B) via Server A's UI
2. Server A fires one-time POST /api/federation/{B}/sessions/{name}/bell/clear
3. Every 2 seconds, Server A's poll cycle checks local heartbeat registry:
   - Device D is viewing session "build" on remoteId 1 in fullscreen
   - Last interaction < 60s ago
4. Server A fires POST /api/federation/1/sessions/build/bell/clear
5. Server B clears the bell in its state.json
6. Next poll: Server A fetches /api/federation/sessions → bell is cleared
7. Browser updates: title count decreases, favicon badge updates
```

### Phase 2: Settings Sync

```
1. User changes fontSize to 18 on Server A via the settings dialog
2. app.js calls PATCH /api/settings {fontSize: 18}
3. Server A's patch_settings() updates settings.json, bumps settings_updated_at to t=1712600000.0
4. Next poll cycle (within 2 seconds):
   a. Server A fetches GET /api/settings/sync from Server B
   b. Server B responds: {settings: {...}, settings_updated_at: 1712599990.0}
   c. Server A's timestamp (1712600000.0) > Server B's (1712599990.0)
   d. Server A sends PUT /api/settings/sync to Server B with its settings + timestamp
   e. Server B applies the syncable keys, sets its settings_updated_at to 1712600000.0
5. Timestamps now equal → no further sync until the next user edit
```

### Offline Catch-Up

```
1. Server B goes offline at t=100 (settings_updated_at: 100)
2. User changes settings on Server A at t=200 (settings_updated_at: 200)
3. Server B comes back online
4. Next poll cycle: Server A fetches GET /api/settings/sync from B
5. Server A (200) > Server B (100) → push to B
6. Server B adopts settings + timestamp 200
7. Fully caught up in one poll cycle
```

## Error Handling

### Phase 1

- **Bell clear when remote is unreachable:** Fire-and-forget, same as the existing `openSession()` bell/clear. If the remote is down, the bell stays uncleaned -- it clears on the next successful poll after the remote comes back.
- **Heartbeat-driven bell clear race:** The 2-second poll might fire a bell/clear just as the remote fires a new bell. The next poll cycle sees the new bell and clears it again. No atomicity concerns for a single-user system.
- **`remoteId: 0` fix backward compat:** Pure behavior correction. If someone had accidentally hidden a remote-0 session, it now correctly becomes visible.

### Phase 2

- **Sync loop prevention:** When applying synced settings, set `settings_updated_at` to the **incoming** timestamp, not `time.time()`. Timestamps converge and the next poll sees them as equal.
- **Partial reachability:** A can reach B but not C. B can reach C but not A. Settings flow A→B→C transitively through successive poll cycles. Eventually consistent -- the newest timestamp propagates across the mesh.
- **Concurrent edits:** User changes `fontSize` on A at t=100, then changes `sort_order` on B at t=101. B's document wins entirely -- A's `fontSize` change is lost. Acceptable for a single-user system where edits are infrequent. Full-document approach avoids per-key merge complexity.
- **`hidden_sessions` list merge:** Full-document last-write-wins applies to the entire list. If A hides "foo" at t=100 and B hides "bar" at t=101, B's list wins (only "bar" is hidden). Known limitation, acceptable for single-user.
- **Version mismatch:** If Server A is on a newer version with new syncable keys and Server B is older, the sync `PUT` sends keys B doesn't know about. B's `patch_settings()` already ignores unknown keys -- safe by default.
- **Rolling upgrades:** The `GET/PUT /api/settings/sync` endpoints will return 404/405 on older muxplex instances. The poll loop catches these errors and skips sync for that remote, enabling rolling upgrades without breakage.

## Testing Strategy

### Phase 1 Tests

- **`getVisibleSessions()` falsy-0 fix:** Test that a session with `remoteId: 0` is NOT hidden when its name doesn't appear in `hidden_sessions`, and IS visible even when a local session with the same name IS hidden.
- **Browser indicators filter hidden sessions:** Test that `updatePageTitle()` and `updateFaviconBadge()` count bells only from visible sessions.
- **Heartbeat-driven bell clear for remote sessions:** Python test verifying that the poll cycle calls the federation bell/clear endpoint when a device is viewing a remote session in fullscreen with recent interaction.

### Phase 2 Tests

- **`SYNCABLE_KEYS` allowlist:** Python test verifying the allowlist contains the expected keys and excludes local-only keys.
- **`settings_updated_at` bumps on patch:** Python test that `patch_settings()` updates `settings_updated_at` to a recent timestamp.
- **`GET /api/settings/sync`:** Returns only syncable keys + timestamp. Authenticated via federation key.
- **`PUT /api/settings/sync`:** Applies incoming settings when incoming timestamp is newer; rejects (409) when local is newer.
- **Sync loop prevention:** Test that applying synced settings uses the incoming timestamp (not `time.time()`), so two servers with the same timestamp don't ping-pong.
- **Unknown keys in sync payload ignored:** Test that a sync payload with keys not in `SYNCABLE_KEYS` are silently dropped.

### Manual Verification Checklist

1. Change `fontSize` on Server A → verify it appears on Server B within ~4 seconds
2. Hide a local session on Server A → verify it disappears on Server B's dashboard
3. Hide a Server B session from Server A's UI → verify it propagates to Server B and hides there too
4. View a remote session in fullscreen → verify bell clears on the remote within one poll cycle
5. Tab title shows correct `(N)` count excluding hidden sessions
6. Disconnect Server B → change settings on A → reconnect B → verify B catches up

## Compatibility

Builds on top of the settings consolidation work (v0.2.1). The new `GET/PUT /api/settings/sync` endpoints gracefully fail when connecting to older muxplex instances that don't have them -- the poll loop catches HTTP 404/405 and skips sync for that remote. This enables rolling upgrades.

## Open Questions

None -- all design decisions were validated during the brainstorm phase.

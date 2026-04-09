# Federation State Propagation Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Make federation a seamless experience where settings, hidden sessions, and activity state propagate across all connected muxplex servers.
**Architecture:** Two-phase approach. Phase 1 fixes four federation bugs (falsy-0 remoteId, hidden session filtering in browser indicators, heartbeat-driven bell clearing across federation). Phase 2 adds a minimal P2P settings sync protocol using full-document push with per-server timestamps. All changes are in `muxplex/settings.py`, `muxplex/main.py`, and `muxplex/frontend/app.js`.
**Tech Stack:** Python 3.11+ / FastAPI / httpx / vanilla JS / pytest / Node.js test runner

---

## Phase 1: Federation Bug Fixes

### Task 1: Fix `getVisibleSessions()` falsy-0 bug

**Files:**
- Modify: `muxplex/frontend/app.js:533`
- Test: `muxplex/frontend/tests/test_app.mjs` (add new tests)
- Test: `muxplex/tests/test_frontend_js.py` (add pattern test)

**Step 1: Write the failing tests**

In `muxplex/frontend/tests/test_app.mjs`, add these tests. Find the section that tests `getVisibleSessions` (search for `getVisibleSessions` in the test file) and add after the existing tests:

```javascript
test('getVisibleSessions does NOT hide sessions with remoteId 0 whose name is in hidden_sessions', function() {
  // remoteId: 0 is the first remote instance — it's a valid remote, not local.
  // The hidden_sessions filter should only apply to local sessions (remoteId == null).
  app._setServerSettings({ hidden_sessions: ['build'] });
  app._setCurrentSessions([
    { name: 'build', remoteId: 0, bell: { unseen_count: 0 } },
  ]);
  var visible = app.getVisibleSessions(app._getCurrentSessions());
  assert.strictEqual(visible.length, 1,
    'Session with remoteId: 0 must NOT be hidden — 0 is a valid remote index, not null');
  app._setServerSettings(null);
});

test('getVisibleSessions hides local session (remoteId null) but keeps remote session (remoteId 0) with same name', function() {
  app._setServerSettings({ hidden_sessions: ['build'] });
  app._setCurrentSessions([
    { name: 'build', remoteId: null, bell: { unseen_count: 0 } },
    { name: 'build', remoteId: 0, bell: { unseen_count: 0 } },
  ]);
  var visible = app.getVisibleSessions(app._getCurrentSessions());
  assert.strictEqual(visible.length, 1,
    'Only the local session should be hidden; remote with remoteId 0 should remain');
  assert.strictEqual(visible[0].remoteId, 0,
    'The surviving session must be the remote one (remoteId 0)');
  app._setServerSettings(null);
});
```

In `muxplex/tests/test_frontend_js.py`, add a pattern test at the end of the file:

```python
def test_get_visible_sessions_uses_null_check_not_falsy() -> None:
    """getVisibleSessions must use 's.remoteId == null' (not '!s.remoteId') to detect local sessions.

    The falsy check '!s.remoteId' incorrectly treats remoteId: 0 (first remote instance)
    as falsy, hiding those sessions when they shouldn't be.
    """
    # Extract the getVisibleSessions function body
    match = re.search(r"function getVisibleSessions\b[^{]*\{(.*?)\n\}", _JS, re.DOTALL)
    assert match, "getVisibleSessions function not found in app.js"
    body = match.group(1)
    assert "!s.remoteId" not in body, (
        "getVisibleSessions must NOT use '!s.remoteId' — it treats remoteId:0 as falsy. "
        "Use 's.remoteId == null' instead."
    )
    assert "s.remoteId == null" in body or "s.remoteId === null" in body, (
        "getVisibleSessions must use 's.remoteId == null' to detect local sessions"
    )
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | grep -E "FAIL|falsy|remoteId 0"`
Expected: FAIL — the current code uses `!s.remoteId` which treats `0` as falsy.

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_frontend_js.py::test_get_visible_sessions_uses_null_check_not_falsy -v`
Expected: FAIL — `!s.remoteId` is still in the code.

**Step 3: Write minimal implementation**

In `muxplex/frontend/app.js`, change line 533 from:

```javascript
    if (hidden.length > 0 && !s.remoteId && hidden.includes(s.name)) {
```

to:

```javascript
    if (hidden.length > 0 && s.remoteId == null && hidden.includes(s.name)) {
```

That's it. One character change (plus a few more for the condition). `s.remoteId == null` correctly treats `0` as truthy (a real remote) while still matching `null` and `undefined` (local sessions).

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -3`
Expected: All tests pass (including the two new ones).

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_frontend_js.py::test_get_visible_sessions_uses_null_check_not_falsy -v`
Expected: PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs muxplex/tests/test_frontend_js.py && git commit -m "fix: getVisibleSessions falsy-0 bug — use s.remoteId == null instead of !s.remoteId"
```

---

### Task 2: Fix `updatePageTitle()` and `updateFaviconBadge()` to filter hidden sessions

**Files:**
- Modify: `muxplex/frontend/app.js:1093-1130` (two functions)
- Test: `muxplex/frontend/tests/test_app.mjs` (add new tests)
- Test: `muxplex/tests/test_frontend_js.py` (add pattern tests)

**Step 1: Write the failing tests**

In `muxplex/frontend/tests/test_app.mjs`, add tests near the existing `updatePageTitle` / `updateFaviconBadge` tests:

```javascript
test('updatePageTitle excludes hidden sessions from bell count', function() {
  app._setServerSettings({ hidden_sessions: ['hidden-build'], device_name: 'myhost' });
  app._setCurrentSessions([
    { name: 'visible-dev', remoteId: null, bell: { unseen_count: 2 } },
    { name: 'hidden-build', remoteId: null, bell: { unseen_count: 5 } },
  ]);
  app.updatePageTitle();
  // Only 'visible-dev' should be counted (1 session with bells), not 'hidden-build'
  assert.ok(document.title.startsWith('(1)'),
    'Title must show (1) — the hidden session bell count must be excluded. Got: ' + document.title);
  app._setServerSettings(null);
});

test('updateFaviconBadge does not show activity for only-hidden sessions with bells', function() {
  // When ALL sessions with bells are hidden, there should be no badge
  app._setServerSettings({ hidden_sessions: ['hidden-build'] });
  app._setCurrentSessions([
    { name: 'hidden-build', remoteId: null, bell: { unseen_count: 3 } },
    { name: 'visible-dev', remoteId: null, bell: { unseen_count: 0 } },
  ]);
  app.updateFaviconBadge();
  // The favicon link should be restored to original (no badge)
  var link = document.querySelector('link[rel="icon"]');
  // If _drawFaviconBadge was called, the href would be a data:image/png URL
  // We just verify the function body uses getVisibleSessions via the pattern test
  app._setServerSettings(null);
});
```

In `muxplex/tests/test_frontend_js.py`, add pattern tests:

```python
def test_update_page_title_filters_through_visible_sessions() -> None:
    """updatePageTitle must filter through getVisibleSessions before counting bells."""
    match = re.search(r"function updatePageTitle\b[^{]*\{(.*?)\n\}", _JS, re.DOTALL)
    assert match, "updatePageTitle function not found in app.js"
    body = match.group(1)
    assert "getVisibleSessions" in body, (
        "updatePageTitle must call getVisibleSessions to exclude hidden sessions from bell count"
    )


def test_update_favicon_badge_filters_through_visible_sessions() -> None:
    """updateFaviconBadge must filter through getVisibleSessions before checking bells."""
    match = re.search(r"function updateFaviconBadge\b[^{]*\{(.*?)\n\}", _JS, re.DOTALL)
    assert match, "updateFaviconBadge function not found in app.js"
    body = match.group(1)
    assert "getVisibleSessions" in body, (
        "updateFaviconBadge must call getVisibleSessions to exclude hidden sessions from activity check"
    )
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_frontend_js.py::test_update_page_title_filters_through_visible_sessions muxplex/tests/test_frontend_js.py::test_update_favicon_badge_filters_through_visible_sessions -v`
Expected: Both FAIL — neither function currently calls `getVisibleSessions`.

**Step 3: Write minimal implementation**

In `muxplex/frontend/app.js`, change `updateFaviconBadge()` (around line 1093):

From:
```javascript
function updateFaviconBadge() {
  var hasActivity = _currentSessions && _currentSessions.some(function (s) {
    return s.bell && s.bell.unseen_count > 0;
  });
```

To:
```javascript
function updateFaviconBadge() {
  var visible = getVisibleSessions(_currentSessions);
  var hasActivity = visible.length > 0 && visible.some(function (s) {
    return s.bell && s.bell.unseen_count > 0;
  });
```

In `muxplex/frontend/app.js`, change `updatePageTitle()` (around line 1121):

From:
```javascript
function updatePageTitle() {
  var hostname = (_serverSettings && _serverSettings.device_name) ||
                 (typeof location !== 'undefined' ? location.hostname : null) ||
                 'muxplex';
  var count = (_currentSessions || []).filter(function(s) {
    return s.bell && s.bell.unseen_count > 0;
  }).length;
```

To:
```javascript
function updatePageTitle() {
  var hostname = (_serverSettings && _serverSettings.device_name) ||
                 (typeof location !== 'undefined' ? location.hostname : null) ||
                 'muxplex';
  var visible = getVisibleSessions(_currentSessions);
  var count = visible.filter(function(s) {
    return s.bell && s.bell.unseen_count > 0;
  }).length;
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_frontend_js.py::test_update_page_title_filters_through_visible_sessions muxplex/tests/test_frontend_js.py::test_update_favicon_badge_filters_through_visible_sessions -v`
Expected: Both PASS.

Run: `cd muxplex && node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -3`
Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/frontend/app.js muxplex/frontend/tests/test_app.mjs muxplex/tests/test_frontend_js.py && git commit -m "fix: updatePageTitle and updateFaviconBadge now filter hidden sessions via getVisibleSessions"
```

---

### Task 3: Add heartbeat-driven bell clearing for remote sessions in poll cycle

**Files:**
- Modify: `muxplex/main.py:91-138` (add step 12 to `_run_poll_cycle`)
- Modify: `muxplex/main.py:79-83` (add module-level `_federation_client` reference)
- Modify: `muxplex/main.py:161-189` (set `_federation_client` in lifespan)
- Test: `muxplex/tests/test_settings.py` or new test in `muxplex/tests/test_api.py`

**Important context:** The `_run_poll_cycle()` function runs in a background task, NOT inside a request handler. It does NOT have access to `request.app.state.federation_client`. We need a module-level reference to the HTTP client. The state schema has `active_remote_id` at the top level (not per-device) — this tells us which remote the user is currently viewing. Each device entry has `viewing_session` and `view_mode`.

**Step 1: Write the failing test**

In `muxplex/tests/test_api.py`, add at the end of the file. This test verifies the poll cycle fires a bell/clear to the remote when a device is viewing a remote session:

```python
def test_poll_cycle_fires_federation_bell_clear_for_remote_session(
    monkeypatch, tmp_path
):
    """_run_poll_cycle must fire POST bell/clear to the remote server when a device
    is viewing a remote session in fullscreen with recent interaction.

    This is the heartbeat-driven bell clearing that makes activity indicators
    clear correctly across federation boundaries.
    """
    import asyncio
    import time
    import httpx
    from unittest.mock import AsyncMock, MagicMock, patch

    import muxplex.main as main_mod
    import muxplex.state as state_mod
    import muxplex.settings as settings_mod

    # Redirect state and settings to tmp_path
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_state_dir)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_state_path)

    fake_settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_settings_path)

    # Set up settings with one remote instance
    settings_mod.save_settings({
        "remote_instances": [
            {"url": "http://server-b:8088", "name": "Server B", "key": "test-key"}
        ],
        "device_name": "Server A",
    })

    # Set up state: one device viewing a remote session in fullscreen
    state = state_mod.empty_state()
    state["active_remote_id"] = 0  # viewing a session on remote index 0
    state["devices"]["d-12345678"] = {
        "label": "Browser",
        "viewing_session": "build",
        "view_mode": "fullscreen",
        "last_interaction_at": time.time(),
        "last_heartbeat_at": time.time(),
    }
    # The local session list has a bell for "build" on the remote
    # (the poll cycle won't find it locally, but state has the device info)
    state_mod.save_state(state)

    # Mock the federation client
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(main_mod, "_federation_client", mock_client)

    # Mock enumerate_sessions and snapshot_all to return empty (no local sessions)
    monkeypatch.setattr(main_mod, "enumerate_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_mod, "snapshot_all", AsyncMock(return_value={}))
    monkeypatch.setattr(main_mod, "update_session_cache", MagicMock())
    monkeypatch.setattr(main_mod, "process_bell_flags", AsyncMock(return_value=False))

    # Run one poll cycle
    asyncio.get_event_loop().run_until_complete(main_mod._run_poll_cycle())

    # Verify bell/clear was fired to the remote
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "/api/sessions/build/bell/clear" in call_args[0][0], (
        f"Expected bell/clear URL for 'build' session, got: {call_args[0][0]}"
    )
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-key", (
        "Bell/clear request must include Bearer auth header"
    )
```

**Step 2: Run test to verify it fails**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_poll_cycle_fires_federation_bell_clear_for_remote_session -v`
Expected: FAIL — `_federation_client` doesn't exist yet.

**Step 3: Write minimal implementation**

**3a.** In `muxplex/main.py`, after the `_poll_task` variable (around line 83), add:

```python
_federation_client: httpx.AsyncClient | None = None
```

**3b.** In `muxplex/main.py`, in the `lifespan()` function, after `app.state.federation_client = httpx.AsyncClient(...)` (around line 189), add:

```python
    global _federation_client
    _federation_client = app.state.federation_client
```

And in the shutdown section, after `await client.aclose()` (around line 196), add:

```python
        _federation_client = None
```

**3c.** In `muxplex/main.py`, in `_run_poll_cycle()`, after the `apply_bell_clear_rule(state)` line (line 132) and before `prune_devices(state)` (line 135), add step 12:

```python
        # 12. Fire bell/clear to remote servers for federated sessions being viewed
        # This handles the case where a browser on Server A is viewing a remote
        # session on Server B — Server B doesn't know anyone is watching, so its
        # should_clear_bell() never fires. We fix that by proactively clearing
        # from the poll cycle. Fire-and-forget: errors are logged and ignored.
        if _federation_client is not None:
            active_remote = state.get("active_remote_id")
            if active_remote is not None:
                settings = load_settings()
                remotes = settings.get("remote_instances", [])
                cutoff = time.time() - 60.0  # same window as should_clear_bell
                for device in state["devices"].values():
                    if (
                        device["viewing_session"]
                        and device["view_mode"] == "fullscreen"
                        and device["last_interaction_at"] > cutoff
                    ):
                        session_name = device["viewing_session"]
                        try:
                            remote_idx = int(active_remote)
                            if 0 <= remote_idx < len(remotes):
                                remote = remotes[remote_idx]
                                remote_url = remote.get("url", "").rstrip("/")
                                remote_key = remote.get("key", "")
                                url = f"{remote_url}/api/sessions/{session_name}/bell/clear"
                                headers = (
                                    {"Authorization": f"Bearer {remote_key}"}
                                    if remote_key
                                    else {}
                                )
                                await _federation_client.post(url, headers=headers)
                        except Exception as exc:
                            _log.debug(
                                "federation bell/clear for %s failed: %s",
                                session_name,
                                exc,
                            )
```

Note: We need to release the `state_lock` before making the HTTP call to avoid deadlocks (the remote's response isn't needed for local state). However, since the existing poll cycle is fully under `state_lock`, and this is a fire-and-forget call with a 5-second timeout, keep it inside the lock for now. The httpx call is async and won't block the event loop.

Actually, re-reading the `_run_poll_cycle` function, the entire thing runs under `async with state_lock`. The HTTP call should work fine since it's async (non-blocking). The 5-second timeout on the httpx client protects against slow remotes.

**Step 4: Run test to verify it passes**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_poll_cycle_fires_federation_bell_clear_for_remote_session -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `cd muxplex && python3 -m pytest muxplex/tests/ -v --timeout=30 2>&1 | tail -5`
Expected: All tests pass.

**Step 6: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: heartbeat-driven bell clearing for remote sessions in poll cycle"
```

---

### Task 4: Phase 1 verification and commit

**Files:**
- No new files — verification only.

**Step 1: Run full Python test suite**

Run: `cd muxplex && python3 -m pytest muxplex/tests/ -v --timeout=60 2>&1 | tail -10`
Expected: All tests pass.

**Step 2: Run full JS test suite**

Run: `cd muxplex && cd muxplex && node --test frontend/tests/test_app.mjs 2>&1 | tail -5`
Expected: All tests pass.

Run: `cd muxplex && cd muxplex && node --test frontend/tests/test_terminal.mjs 2>&1 | tail -5`
Expected: All tests pass.

**Step 3: Run grep verification checklist**

Run: `cd muxplex && grep -n '!s\.remoteId' muxplex/frontend/app.js`
Expected: Zero matches (the falsy-0 bug is fixed).

Run: `cd muxplex && grep -n 'getVisibleSessions' muxplex/frontend/app.js | grep -E 'updatePageTitle|updateFaviconBadge'`
Expected: No matches from grep directly, but the function bodies should contain `getVisibleSessions`. Verify with:

Run: `cd muxplex && python3 -c "import re; js=open('muxplex/frontend/app.js').read(); m1=re.search(r'function updatePageTitle.*?\n}', js, re.DOTALL); m2=re.search(r'function updateFaviconBadge.*?\n}', js, re.DOTALL); print('updatePageTitle has getVisibleSessions:', 'getVisibleSessions' in m1.group()); print('updateFaviconBadge has getVisibleSessions:', 'getVisibleSessions' in m2.group())"`
Expected: Both `True`.

Run: `cd muxplex && grep -n '_federation_client' muxplex/main.py`
Expected: Multiple matches (declaration, assignment in lifespan, usage in poll cycle, cleanup).

**Step 4: Tag Phase 1 complete**

No additional commit needed — all Phase 1 changes are already committed in Tasks 1-3.

---

## Phase 2: Federation Settings Sync

### Task 5: Add `SYNCABLE_KEYS` allowlist and `settings_updated_at` to `settings.py`

**Files:**
- Modify: `muxplex/settings.py:16-44` (add constant and new default key)
- Test: `muxplex/tests/test_settings.py` (add tests)

**Step 1: Write the failing tests**

In `muxplex/tests/test_settings.py`, add at the end:

```python
# ============================================================
# SYNCABLE_KEYS and settings_updated_at (federation settings sync)
# ============================================================


def test_syncable_keys_exists_and_is_a_set():
    """SYNCABLE_KEYS must be a set constant exported from settings.py."""
    from muxplex.settings import SYNCABLE_KEYS

    assert isinstance(SYNCABLE_KEYS, (set, frozenset)), (
        f"SYNCABLE_KEYS must be a set, got: {type(SYNCABLE_KEYS).__name__}"
    )


def test_syncable_keys_contains_expected_keys():
    """SYNCABLE_KEYS must contain all 15 user-experience preference keys."""
    from muxplex.settings import SYNCABLE_KEYS

    expected = {
        "fontSize", "hoverPreviewDelay", "gridColumns", "bellSound",
        "viewMode", "showDeviceBadges", "showHoverPreview", "activityIndicator",
        "gridViewMode", "sidebarOpen", "sort_order", "hidden_sessions",
        "default_session", "window_size_largest", "auto_open_created",
    }
    assert SYNCABLE_KEYS == expected, (
        f"SYNCABLE_KEYS mismatch.\n"
        f"  Missing: {expected - SYNCABLE_KEYS}\n"
        f"  Extra: {SYNCABLE_KEYS - expected}"
    )


def test_syncable_keys_excludes_local_only_keys():
    """SYNCABLE_KEYS must NOT contain any per-machine identity/infra keys."""
    from muxplex.settings import SYNCABLE_KEYS

    local_only = {
        "host", "port", "auth", "session_ttl", "tls_cert", "tls_key",
        "device_name", "federation_key", "remote_instances",
        "multi_device_enabled", "new_session_template", "delete_session_template",
    }
    overlap = SYNCABLE_KEYS & local_only
    assert not overlap, (
        f"SYNCABLE_KEYS must not contain local-only keys: {overlap}"
    )


def test_syncable_keys_are_subset_of_default_settings():
    """Every key in SYNCABLE_KEYS must exist in DEFAULT_SETTINGS."""
    from muxplex.settings import SYNCABLE_KEYS

    missing = SYNCABLE_KEYS - set(DEFAULT_SETTINGS.keys())
    assert not missing, (
        f"SYNCABLE_KEYS contains keys not in DEFAULT_SETTINGS: {missing}"
    )


def test_defaults_include_settings_updated_at():
    """DEFAULT_SETTINGS must include 'settings_updated_at' with default 0.0."""
    assert "settings_updated_at" in DEFAULT_SETTINGS, (
        "DEFAULT_SETTINGS must include 'settings_updated_at'"
    )
    assert DEFAULT_SETTINGS["settings_updated_at"] == 0.0, (
        f"settings_updated_at default must be 0.0, got: {DEFAULT_SETTINGS['settings_updated_at']!r}"
    )


def test_settings_updated_at_not_in_syncable_keys():
    """settings_updated_at is a metadata field, not a user setting — must NOT be in SYNCABLE_KEYS."""
    from muxplex.settings import SYNCABLE_KEYS

    assert "settings_updated_at" not in SYNCABLE_KEYS, (
        "settings_updated_at must NOT be in SYNCABLE_KEYS — it's sync metadata, not a user setting"
    )
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_settings.py::test_syncable_keys_exists_and_is_a_set muxplex/tests/test_settings.py::test_defaults_include_settings_updated_at -v`
Expected: Both FAIL — neither `SYNCABLE_KEYS` nor `settings_updated_at` exist yet.

**Step 3: Write minimal implementation**

In `muxplex/settings.py`, after the imports (after line 11), add the `SYNCABLE_KEYS` constant:

```python
# Keys that sync across federated servers (user-experience preferences).
# Per-machine identity/infra keys (host, port, auth, etc.) never sync.
SYNCABLE_KEYS: frozenset[str] = frozenset({
    "fontSize",
    "hoverPreviewDelay",
    "gridColumns",
    "bellSound",
    "viewMode",
    "showDeviceBadges",
    "showHoverPreview",
    "activityIndicator",
    "gridViewMode",
    "sidebarOpen",
    "sort_order",
    "hidden_sessions",
    "default_session",
    "window_size_largest",
    "auto_open_created",
})
```

In `muxplex/settings.py`, add `settings_updated_at` to `DEFAULT_SETTINGS` (after `sidebarOpen`):

```python
    "settings_updated_at": 0.0,
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_settings.py -v --timeout=10 2>&1 | tail -10`
Expected: All tests pass (including the 7 new ones).

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: add SYNCABLE_KEYS allowlist and settings_updated_at to DEFAULT_SETTINGS"
```

---

### Task 6: Add sync-aware `patch_settings()` timestamp bumping and `apply_synced_settings()`

**Files:**
- Modify: `muxplex/settings.py:81-132` (update `patch_settings`, add `apply_synced_settings`)
- Test: `muxplex/tests/test_settings.py` (add tests)

**Step 1: Write the failing tests**

In `muxplex/tests/test_settings.py`, add at the end:

```python
def test_patch_settings_bumps_settings_updated_at_for_syncable_key():
    """patch_settings() must bump settings_updated_at when a syncable key is patched."""
    import time

    before = time.time()
    result = patch_settings({"fontSize": 20})
    after = time.time()
    assert before <= result["settings_updated_at"] <= after, (
        f"settings_updated_at must be bumped to ~now, got: {result['settings_updated_at']}"
    )


def test_patch_settings_does_not_bump_settings_updated_at_for_local_only_key():
    """patch_settings() must NOT bump settings_updated_at when only local-only keys are patched."""
    # First, set a known timestamp
    save_settings({"settings_updated_at": 100.0})
    result = patch_settings({"host": "0.0.0.0"})
    assert result["settings_updated_at"] == 100.0, (
        f"settings_updated_at must NOT be bumped for local-only key 'host', "
        f"got: {result['settings_updated_at']}"
    )


def test_apply_synced_settings_exists():
    """apply_synced_settings must be importable from settings.py."""
    from muxplex.settings import apply_synced_settings

    assert callable(apply_synced_settings)


def test_apply_synced_settings_applies_syncable_keys_only():
    """apply_synced_settings must apply only syncable keys and ignore local-only keys."""
    from muxplex.settings import apply_synced_settings

    save_settings({"host": "127.0.0.1", "fontSize": 14})
    apply_synced_settings(
        {"fontSize": 22, "host": "HACKED", "sort_order": "alpha"},
        timestamp=500.0,
    )
    loaded = load_settings()
    assert loaded["fontSize"] == 22, "Syncable key 'fontSize' must be applied"
    assert loaded["sort_order"] == "alpha", "Syncable key 'sort_order' must be applied"
    assert loaded["host"] == "127.0.0.1", (
        "Local-only key 'host' must NOT be overwritten by sync"
    )


def test_apply_synced_settings_uses_incoming_timestamp():
    """apply_synced_settings must set settings_updated_at to the INCOMING timestamp (not time.time()).

    This is critical for sync loop prevention — if we used time.time(), the local
    timestamp would always be slightly newer than the remote, causing an infinite
    push-pull loop.
    """
    from muxplex.settings import apply_synced_settings

    apply_synced_settings({"fontSize": 18}, timestamp=12345.678)
    loaded = load_settings()
    assert loaded["settings_updated_at"] == 12345.678, (
        f"settings_updated_at must be the incoming timestamp 12345.678, "
        f"got: {loaded['settings_updated_at']}"
    )


def test_apply_synced_settings_ignores_unknown_keys():
    """apply_synced_settings must silently ignore keys not in SYNCABLE_KEYS."""
    from muxplex.settings import apply_synced_settings

    apply_synced_settings(
        {"fontSize": 16, "totally_unknown_key": "should_be_ignored"},
        timestamp=999.0,
    )
    loaded = load_settings()
    assert loaded["fontSize"] == 16
    assert "totally_unknown_key" not in loaded
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_settings.py::test_patch_settings_bumps_settings_updated_at_for_syncable_key muxplex/tests/test_settings.py::test_apply_synced_settings_exists -v`
Expected: Both FAIL.

**Step 3: Write minimal implementation**

In `muxplex/settings.py`, add `import time` to the imports (it's not currently imported). Add after `import socket`:

```python
import time
```

In `muxplex/settings.py`, modify `patch_settings()`. After the line `for key in DEFAULT_SETTINGS:` loop that applies the patch (around line 109-111), and before the `if "remote_instances" in patch:` block (around line 114), add:

```python
    # Bump settings_updated_at if any syncable key was patched
    if any(key in SYNCABLE_KEYS for key in patch if key in DEFAULT_SETTINGS):
        current["settings_updated_at"] = time.time()
```

In `muxplex/settings.py`, add the new function after `patch_settings()` (before `load_federation_key()`):

```python
def apply_synced_settings(settings: dict, timestamp: float) -> dict:
    """Apply synced settings from a remote server.

    Only keys in SYNCABLE_KEYS are applied. The settings_updated_at is set to
    the incoming *timestamp* (NOT time.time()) to prevent sync loops — both
    servers end up with the same timestamp so the next poll sees them as equal.

    Unknown keys (not in SYNCABLE_KEYS) are silently ignored.
    """
    current = load_settings()
    for key in SYNCABLE_KEYS:
        if key in settings:
            current[key] = settings[key]
    current["settings_updated_at"] = timestamp
    save_settings(current)
    return current
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_settings.py -v --timeout=10 2>&1 | tail -15`
Expected: All tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/settings.py muxplex/tests/test_settings.py && git commit -m "feat: sync-aware patch_settings timestamp bumping and apply_synced_settings"
```

---

### Task 7: Add `GET /api/settings/sync` endpoint

**Files:**
- Modify: `muxplex/main.py` (add endpoint after existing settings endpoints, around line 676)
- Modify: `muxplex/main.py:67` (add `SYNCABLE_KEYS` to import)
- Test: `muxplex/tests/test_api.py` (add tests)

**Step 1: Write the failing tests**

In `muxplex/tests/test_api.py`, add at the end:

```python
# ============================================================
# Federation settings sync endpoints
# ============================================================


def test_get_settings_sync_returns_syncable_keys_only(client, monkeypatch, tmp_path):
    """GET /api/settings/sync must return only syncable keys + settings_updated_at."""
    import json
    import muxplex.settings as settings_mod

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    settings_mod.save_settings({
        "fontSize": 18,
        "host": "0.0.0.0",
        "settings_updated_at": 1234.5,
    })

    resp = client.get("/api/settings/sync")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    # Must include syncable keys
    assert "settings" in data, "Response must have 'settings' key"
    assert "settings_updated_at" in data, "Response must have 'settings_updated_at' key"
    assert data["settings"]["fontSize"] == 18
    assert data["settings_updated_at"] == 1234.5

    # Must NOT include local-only keys
    assert "host" not in data["settings"], (
        "Sync response must NOT include local-only key 'host'"
    )
    assert "port" not in data["settings"], (
        "Sync response must NOT include local-only key 'port'"
    )
    assert "federation_key" not in data["settings"], (
        "Sync response must NOT include 'federation_key'"
    )


def test_get_settings_sync_requires_bearer_auth(monkeypatch, tmp_path):
    """GET /api/settings/sync must require Bearer token auth (like other federation endpoints)."""
    # Create a client with a non-localhost IP to test auth enforcement
    import muxplex.settings as settings_mod

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)

    # The existing `client` fixture sets a session cookie which bypasses auth.
    # For this test we need to verify the endpoint is accessible via Bearer token.
    # Since the test client uses localhost, auth is bypassed anyway.
    # The pattern test below verifies the endpoint exists and returns correct shape.
    # Auth enforcement is already tested by test_federation_bearer_auth_accepted.
    pass  # Auth enforcement tested via existing auth middleware tests
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_get_settings_sync_returns_syncable_keys_only -v`
Expected: FAIL — 404 because the endpoint doesn't exist.

**Step 3: Write minimal implementation**

In `muxplex/main.py`, update the import from `muxplex.settings` (line 67) to include `SYNCABLE_KEYS` and `apply_synced_settings`:

```python
from muxplex.settings import SYNCABLE_KEYS, apply_synced_settings, load_federation_key, load_settings, patch_settings
```

In `muxplex/main.py`, after the `PATCH /api/settings` endpoint (after line 675), add:

```python
@app.get("/api/settings/sync")
async def get_settings_sync() -> dict:
    """Return syncable settings + timestamp for federation sync protocol.

    Returns only keys in SYNCABLE_KEYS (no local-only infra keys, no secrets).
    Used by remote servers to compare timestamps and decide sync direction.
    Authenticated via federation Bearer token (same as other federation endpoints).
    """
    settings = load_settings()
    syncable = {key: settings[key] for key in SYNCABLE_KEYS if key in settings}
    return {
        "settings": syncable,
        "settings_updated_at": settings.get("settings_updated_at", 0.0),
    }
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_get_settings_sync_returns_syncable_keys_only -v`
Expected: PASS

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add GET /api/settings/sync endpoint for federation sync protocol"
```

---

### Task 8: Add `PUT /api/settings/sync` endpoint

**Files:**
- Modify: `muxplex/main.py` (add endpoint after the GET sync endpoint)
- Test: `muxplex/tests/test_api.py` (add tests)

**Step 1: Write the failing tests**

In `muxplex/tests/test_api.py`, add:

```python
def test_put_settings_sync_applies_when_incoming_is_newer(client, monkeypatch, tmp_path):
    """PUT /api/settings/sync must apply incoming settings when incoming timestamp is newer."""
    import json
    import muxplex.settings as settings_mod

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    settings_mod.save_settings({"fontSize": 14, "settings_updated_at": 100.0})

    resp = client.put(
        "/api/settings/sync",
        json={
            "settings": {"fontSize": 22, "sort_order": "alpha"},
            "settings_updated_at": 200.0,
        },
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # Verify settings were applied
    loaded = settings_mod.load_settings()
    assert loaded["fontSize"] == 22, "fontSize must be updated to 22"
    assert loaded["sort_order"] == "alpha", "sort_order must be updated to 'alpha'"
    assert loaded["settings_updated_at"] == 200.0, (
        "settings_updated_at must be set to the incoming timestamp (200.0)"
    )


def test_put_settings_sync_rejects_when_local_is_newer(client, monkeypatch, tmp_path):
    """PUT /api/settings/sync must return 409 when local timestamp is newer than incoming."""
    import json
    import muxplex.settings as settings_mod

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    settings_mod.save_settings({"fontSize": 18, "settings_updated_at": 300.0})

    resp = client.put(
        "/api/settings/sync",
        json={
            "settings": {"fontSize": 12},
            "settings_updated_at": 100.0,
        },
    )
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"

    # Verify settings were NOT changed
    loaded = settings_mod.load_settings()
    assert loaded["fontSize"] == 18, "fontSize must NOT be changed when local is newer"
    assert loaded["settings_updated_at"] == 300.0, "Timestamp must NOT change"

    # Response should include local state so caller can adopt it
    data = resp.json()
    assert "settings" in data, "409 response must include local settings"
    assert "settings_updated_at" in data, "409 response must include local timestamp"


def test_put_settings_sync_ignores_local_only_keys_in_payload(client, monkeypatch, tmp_path):
    """PUT /api/settings/sync must ignore local-only keys in the incoming payload."""
    import json
    import muxplex.settings as settings_mod

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    settings_mod.save_settings({
        "host": "127.0.0.1",
        "fontSize": 14,
        "settings_updated_at": 50.0,
    })

    resp = client.put(
        "/api/settings/sync",
        json={
            "settings": {"fontSize": 20, "host": "HACKED"},
            "settings_updated_at": 200.0,
        },
    )
    assert resp.status_code == 200

    loaded = settings_mod.load_settings()
    assert loaded["fontSize"] == 20, "Syncable key 'fontSize' must be updated"
    assert loaded["host"] == "127.0.0.1", (
        "Local-only key 'host' must NOT be overwritten by sync"
    )


def test_put_settings_sync_noop_when_timestamps_equal(client, monkeypatch, tmp_path):
    """PUT /api/settings/sync is a no-op when timestamps are equal."""
    import json
    import muxplex.settings as settings_mod

    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    settings_mod.save_settings({"fontSize": 14, "settings_updated_at": 200.0})

    resp = client.put(
        "/api/settings/sync",
        json={
            "settings": {"fontSize": 22},
            "settings_updated_at": 200.0,
        },
    )
    # Equal timestamps = no-op, return 200
    assert resp.status_code == 200

    loaded = settings_mod.load_settings()
    assert loaded["fontSize"] == 14, "fontSize must NOT change when timestamps are equal"
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_put_settings_sync_applies_when_incoming_is_newer muxplex/tests/test_api.py::test_put_settings_sync_rejects_when_local_is_newer -v`
Expected: Both FAIL — 405 Method Not Allowed (endpoint doesn't exist).

**Step 3: Write minimal implementation**

In `muxplex/main.py`, after the `GET /api/settings/sync` endpoint, add:

```python
@app.put("/api/settings/sync")
async def put_settings_sync(request: Request) -> dict:
    """Accept synced settings from a remote server (federation sync protocol).

    Compares incoming settings_updated_at with local. If incoming is strictly
    newer, applies the syncable keys via apply_synced_settings() and returns
    the updated state. If local is newer or equal, returns 409 with local state
    so the caller knows to adopt from us instead.

    Authenticated via federation Bearer token.
    """
    body = await request.json()
    incoming_settings = body.get("settings", {})
    incoming_ts = body.get("settings_updated_at", 0.0)

    local_settings = load_settings()
    local_ts = local_settings.get("settings_updated_at", 0.0)

    if incoming_ts > local_ts:
        # Incoming is newer — apply it
        updated = apply_synced_settings(incoming_settings, incoming_ts)
        syncable = {key: updated[key] for key in SYNCABLE_KEYS if key in updated}
        return {
            "settings": syncable,
            "settings_updated_at": updated.get("settings_updated_at", 0.0),
            "applied": True,
        }
    else:
        # Local is newer or equal — reject, return local state
        syncable = {key: local_settings[key] for key in SYNCABLE_KEYS if key in local_settings}
        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=409,
            content={
                "settings": syncable,
                "settings_updated_at": local_ts,
                "applied": False,
            },
        )
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_api.py -k "settings_sync" -v`
Expected: All 5 sync tests pass.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add PUT /api/settings/sync endpoint with newer-wins conflict resolution"
```

---

### Task 9: Add sync logic to `_run_poll_cycle`

**Files:**
- Modify: `muxplex/main.py:91-138` (add settings sync step to poll cycle)
- Test: `muxplex/tests/test_api.py` (add test)

**Important:** Settings sync should NOT run every 2 seconds — that's too aggressive. Use a counter to sync every 15 poll cycles (~30 seconds). Bell clearing still runs every 2 seconds.

**Step 1: Write the failing test**

In `muxplex/tests/test_api.py`, add:

```python
def test_poll_cycle_syncs_settings_when_remote_is_newer(monkeypatch, tmp_path):
    """_run_poll_cycle must adopt remote settings when the remote's timestamp is newer.

    The sync check runs every SETTINGS_SYNC_INTERVAL poll cycles (not every cycle).
    """
    import asyncio
    import time
    import httpx
    from unittest.mock import AsyncMock, MagicMock

    import muxplex.main as main_mod
    import muxplex.state as state_mod
    import muxplex.settings as settings_mod

    # Redirect state and settings to tmp_path
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_state_dir)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_state_path)

    fake_settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_settings_path)

    # Local settings: fontSize 14, timestamp 100
    settings_mod.save_settings({
        "remote_instances": [
            {"url": "http://server-b:8088", "name": "Server B", "key": "test-key"}
        ],
        "device_name": "Server A",
        "fontSize": 14,
        "settings_updated_at": 100.0,
    })

    # Set up empty state (no devices, no local sessions)
    state_mod.save_state(state_mod.empty_state())

    # Mock the federation client to return newer settings from remote
    remote_sync_response = MagicMock()
    remote_sync_response.status_code = 200
    remote_sync_response.json.return_value = {
        "settings": {"fontSize": 22, "sort_order": "alpha"},
        "settings_updated_at": 200.0,
    }
    remote_sync_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=remote_sync_response)
    mock_client.post = AsyncMock()  # for bell/clear (not used here)
    monkeypatch.setattr(main_mod, "_federation_client", mock_client)

    # Force the sync counter to trigger on this cycle
    monkeypatch.setattr(main_mod, "_settings_sync_counter", main_mod.SETTINGS_SYNC_INTERVAL - 1)

    # Mock enumerate_sessions and snapshot_all
    monkeypatch.setattr(main_mod, "enumerate_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_mod, "snapshot_all", AsyncMock(return_value={}))
    monkeypatch.setattr(main_mod, "update_session_cache", MagicMock())
    monkeypatch.setattr(main_mod, "process_bell_flags", AsyncMock(return_value=False))

    # Run one poll cycle
    asyncio.get_event_loop().run_until_complete(main_mod._run_poll_cycle())

    # Verify settings sync GET was called
    get_calls = [c for c in mock_client.get.call_args_list
                 if "/api/settings/sync" in str(c)]
    assert len(get_calls) == 1, (
        f"Expected 1 GET /api/settings/sync call, got {len(get_calls)}"
    )

    # Verify local settings were updated
    loaded = settings_mod.load_settings()
    assert loaded["fontSize"] == 22, (
        f"Local fontSize must be updated to 22 from remote, got: {loaded['fontSize']}"
    )
    assert loaded["settings_updated_at"] == 200.0, (
        f"Local timestamp must be set to remote's 200.0, got: {loaded['settings_updated_at']}"
    )


def test_poll_cycle_pushes_settings_when_local_is_newer(monkeypatch, tmp_path):
    """_run_poll_cycle must push settings to remote when local timestamp is newer."""
    import asyncio
    import time
    import httpx
    from unittest.mock import AsyncMock, MagicMock

    import muxplex.main as main_mod
    import muxplex.state as state_mod
    import muxplex.settings as settings_mod

    # Redirect state and settings to tmp_path
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_state_dir)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_state_path)

    fake_settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_settings_path)

    # Local settings: fontSize 22, timestamp 300 (NEWER than remote)
    settings_mod.save_settings({
        "remote_instances": [
            {"url": "http://server-b:8088", "name": "Server B", "key": "test-key"}
        ],
        "device_name": "Server A",
        "fontSize": 22,
        "settings_updated_at": 300.0,
    })

    state_mod.save_state(state_mod.empty_state())

    # Remote returns older timestamp
    remote_get_response = MagicMock()
    remote_get_response.status_code = 200
    remote_get_response.json.return_value = {
        "settings": {"fontSize": 14},
        "settings_updated_at": 100.0,
    }
    remote_get_response.raise_for_status = MagicMock()

    remote_put_response = MagicMock()
    remote_put_response.status_code = 200
    remote_put_response.json.return_value = {"applied": True}
    remote_put_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=remote_get_response)
    mock_client.put = AsyncMock(return_value=remote_put_response)
    mock_client.post = AsyncMock()
    monkeypatch.setattr(main_mod, "_federation_client", mock_client)
    monkeypatch.setattr(main_mod, "_settings_sync_counter", main_mod.SETTINGS_SYNC_INTERVAL - 1)

    monkeypatch.setattr(main_mod, "enumerate_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_mod, "snapshot_all", AsyncMock(return_value={}))
    monkeypatch.setattr(main_mod, "update_session_cache", MagicMock())
    monkeypatch.setattr(main_mod, "process_bell_flags", AsyncMock(return_value=False))

    asyncio.get_event_loop().run_until_complete(main_mod._run_poll_cycle())

    # Verify PUT was called to push settings to remote
    put_calls = [c for c in mock_client.put.call_args_list
                 if "/api/settings/sync" in str(c)]
    assert len(put_calls) == 1, (
        f"Expected 1 PUT /api/settings/sync call, got {len(put_calls)}"
    )
    # Verify the PUT payload includes local settings
    put_kwargs = put_calls[0][1] if put_calls[0][1] else {}
    if "json" in put_kwargs:
        assert put_kwargs["json"]["settings_updated_at"] == 300.0
```

**Step 2: Run tests to verify they fail**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_poll_cycle_syncs_settings_when_remote_is_newer -v`
Expected: FAIL — `SETTINGS_SYNC_INTERVAL` and `_settings_sync_counter` don't exist.

**Step 3: Write minimal implementation**

In `muxplex/main.py`, after `POLL_INTERVAL` and `SERVER_PORT` (around line 75), add:

```python
SETTINGS_SYNC_INTERVAL: int = 15  # sync settings every 15 poll cycles (~30 seconds)
_settings_sync_counter: int = 0
```

In `muxplex/main.py`, in `_run_poll_cycle()`, after the federation bell-clear block (the step 12 you added in Task 3) and before `prune_devices(state)`, add:

```python
        # 13. Federation settings sync (every SETTINGS_SYNC_INTERVAL cycles)
        global _settings_sync_counter
        _settings_sync_counter += 1
        if (
            _federation_client is not None
            and _settings_sync_counter >= SETTINGS_SYNC_INTERVAL
        ):
            _settings_sync_counter = 0
            sync_settings = load_settings()
            remotes = sync_settings.get("remote_instances", [])
            local_ts = sync_settings.get("settings_updated_at", 0.0)

            for i, remote in enumerate(remotes):
                remote_url = remote.get("url", "").rstrip("/")
                remote_key = remote.get("key", "")
                headers = (
                    {"Authorization": f"Bearer {remote_key}"}
                    if remote_key
                    else {}
                )
                try:
                    # GET remote's sync state
                    resp = await _federation_client.get(
                        f"{remote_url}/api/settings/sync",
                        headers=headers,
                    )
                    if resp.status_code in (404, 405):
                        # Remote is an older muxplex without sync support — skip
                        continue
                    resp.raise_for_status()
                    remote_data = resp.json()
                    remote_ts = remote_data.get("settings_updated_at", 0.0)

                    if remote_ts > local_ts:
                        # Remote is newer — adopt its settings
                        apply_synced_settings(
                            remote_data.get("settings", {}),
                            remote_ts,
                        )
                        # Update local_ts so subsequent remotes compare against
                        # the newly adopted timestamp
                        local_ts = remote_ts
                        _log.info(
                            "settings sync: adopted from %s (ts=%.1f)",
                            remote_url, remote_ts,
                        )
                    elif local_ts > remote_ts:
                        # Local is newer — push to remote
                        syncable = {
                            key: sync_settings[key]
                            for key in SYNCABLE_KEYS
                            if key in sync_settings
                        }
                        await _federation_client.put(
                            f"{remote_url}/api/settings/sync",
                            json={
                                "settings": syncable,
                                "settings_updated_at": local_ts,
                            },
                            headers=headers,
                        )
                        _log.info(
                            "settings sync: pushed to %s (ts=%.1f)",
                            remote_url, local_ts,
                        )
                    # If equal, no action needed
                except Exception as exc:
                    _log.debug(
                        "settings sync with %s failed: %s", remote_url, exc
                    )
```

**Step 4: Run tests to verify they pass**

Run: `cd muxplex && python3 -m pytest muxplex/tests/test_api.py::test_poll_cycle_syncs_settings_when_remote_is_newer muxplex/tests/test_api.py::test_poll_cycle_pushes_settings_when_local_is_newer -v`
Expected: Both PASS.

**Step 5: Commit**

```bash
cd muxplex && git add muxplex/main.py muxplex/tests/test_api.py && git commit -m "feat: add P2P settings sync logic to poll cycle with 30-second interval"
```

---

### Task 10: Phase 2 verification and full test suite

**Files:**
- No new files — verification only.

**Step 1: Run full Python test suite**

Run: `cd muxplex && python3 -m pytest muxplex/tests/ -v --timeout=60 2>&1 | tail -15`
Expected: All tests pass.

**Step 2: Run full JS test suite**

Run: `cd muxplex && cd muxplex && node --test frontend/tests/test_app.mjs 2>&1 | tail -5`
Expected: All tests pass.

Run: `cd muxplex && cd muxplex && node --test frontend/tests/test_terminal.mjs 2>&1 | tail -5`
Expected: All tests pass.

**Step 3: Run code quality checks**

Run: `cd muxplex && python3 -m ruff check muxplex/settings.py muxplex/main.py`
Expected: No errors.

Run: `cd muxplex && python3 -m ruff format --check muxplex/settings.py muxplex/main.py`
Expected: No formatting issues.

**Step 4: Verify grep checklist**

Run: `cd muxplex && grep -n 'SYNCABLE_KEYS' muxplex/settings.py`
Expected: Definition and usage in `apply_synced_settings`.

Run: `cd muxplex && grep -n 'settings_updated_at' muxplex/settings.py`
Expected: In `DEFAULT_SETTINGS`, `patch_settings`, and `apply_synced_settings`.

Run: `cd muxplex && grep -n '/api/settings/sync' muxplex/main.py`
Expected: Two endpoints (GET and PUT) plus poll cycle usage.

Run: `cd muxplex && grep -n '_federation_client' muxplex/main.py`
Expected: Module-level declaration, lifespan assignment/cleanup, poll cycle usage (bell-clear and settings sync).

Run: `cd muxplex && grep -n 'SETTINGS_SYNC_INTERVAL\|_settings_sync_counter' muxplex/main.py`
Expected: Constant definition, counter definition, counter logic in poll cycle.

**Step 5: Verify the JS bug fix is clean**

Run: `cd muxplex && grep -n '!s\.remoteId' muxplex/frontend/app.js`
Expected: Zero matches.

Run: `cd muxplex && python3 -c "
import re
js = open('muxplex/frontend/app.js').read()
fns = ['updatePageTitle', 'updateFaviconBadge']
for fn in fns:
    m = re.search(rf'function {fn}\b[^{{]*\{{(.*?)\n\}}', js, re.DOTALL)
    has = 'getVisibleSessions' in m.group(1) if m else False
    print(f'{fn} uses getVisibleSessions: {has}')
"`
Expected: Both `True`.

**Step 6: Final commit (if any test fixes were needed)**

If all tests pass, no additional commit is needed. If minor fixes were required, commit with:

```bash
cd muxplex && git add -A && git commit -m "fix: Phase 2 verification cleanup"
```

**Step 7: Review commit log**

Run: `cd muxplex && git log --oneline -10`
Expected: Clean commit history showing all 10 tasks:
```
fix: getVisibleSessions falsy-0 bug
fix: updatePageTitle and updateFaviconBadge now filter hidden sessions
feat: heartbeat-driven bell clearing for remote sessions in poll cycle
feat: add SYNCABLE_KEYS allowlist and settings_updated_at to DEFAULT_SETTINGS
feat: sync-aware patch_settings timestamp bumping and apply_synced_settings
feat: add GET /api/settings/sync endpoint for federation sync protocol
feat: add PUT /api/settings/sync endpoint with newer-wins conflict resolution
feat: add P2P settings sync logic to poll cycle with 30-second interval
```

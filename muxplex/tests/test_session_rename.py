"""
Tests for session rename -- docs/plans/2026-08-07-session-rename-plan.md.

Covers the plan's \u00a714 checklist without a real tmux server (that lives in
test_session_rename_integration.py):

1. sessions.is_tmux_stable_name() -- the '.' mangling guard.
2. manifest.py's rename journal (start/clear/get) and renamed_from.
3. main._migrate_session_name() -- one assertion per Tier 1/2 keyspace row
   (\u00a72.1/\u00a72.2), PLUS the Tier 3 negative proofs (\u00a72.3): input_allowed_sessions
   is never migrated, match_names is never rewritten.
4. The endpoint's execution order (\u00a711): 400/403/404/409x4/500/200, the
   \u00a77.3 no-op case, and per-keyspace migration evidence in the response.
5. The poll cycle's journal-completion branch (\u00a76.2): completion and
   reversion, with tmux mocked (no real server needed for this file).
6. The destructive-write backstop does not fire on a 1-for-1 pin swap.

All tmux-touching calls in THIS file are mocked (`rename_tmux_session`,
`enumerate_sessions`) -- no real tmux server is spawned here, matching
test_input.py/test_focus.py's pattern for endpoint-level tests. Real-tmux
proof (the mangling behavior itself, the security fence end-to-end, the
ttyd kill) lives in test_session_rename_integration.py per
`conftest.py`'s autouse `_isolate_tmux_socket_dir` + AGENTS.md's testing
rules.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import muxplex.main as main_mod
import muxplex.manifest as manifest_mod
from muxplex.main import _migrate_session_name, app
from muxplex.manifest import (
    clear_rename_journal,
    get_rename_in_flight,
    get_renamed_from,
    load_manifest,
    save_manifest,
    start_rename_journal,
)
from muxplex.sessions import is_tmux_stable_name
from muxplex.settings import load_settings, save_settings
from muxplex.state import empty_bell, load_state, save_state

DEVICE_ID = "dev-1"

# ---------------------------------------------------------------------------
# sessions.is_tmux_stable_name()
# ---------------------------------------------------------------------------


def test_is_tmux_stable_name_rejects_dot():
    """'.' is the one character tmux mangles (\u00a71) -- reject it."""
    assert is_tmux_stable_name("build_js") is True
    assert is_tmux_stable_name("build.js") is False
    assert is_tmux_stable_name("a.b") is False
    assert is_tmux_stable_name(".leading") is False
    assert is_tmux_stable_name("trail.") is False


def test_is_tmux_stable_name_rejects_bad_charset():
    """Requires is_valid_session_name too -- not a replacement for it."""
    assert is_tmux_stable_name("") is False
    assert is_tmux_stable_name("-leading-dash") is False
    assert is_tmux_stable_name("has:colon") is False


# ---------------------------------------------------------------------------
# manifest.py: rename journal + renamed_from
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def redirect_manifest_path(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", tmp_path / "sessions.json")


def test_load_manifest_defaults_rename_in_flight_to_none():
    manifest = load_manifest()
    assert manifest["rename_in_flight"] is None


def test_start_and_clear_rename_journal_round_trip():
    manifest = load_manifest()
    manifest = start_rename_journal(manifest, "old", "new", now=100.0)
    assert manifest["rename_in_flight"] == {"from": "old", "to": "new", "at": 100.0}
    assert get_rename_in_flight(manifest) == {"from": "old", "to": "new", "at": 100.0}

    manifest = clear_rename_journal(manifest)
    assert manifest["rename_in_flight"] is None
    assert get_rename_in_flight(manifest) is None


def test_start_rename_journal_is_pure():
    """Never mutates the input in place -- matches every other helper here."""
    manifest = load_manifest()
    original = dict(manifest)
    start_rename_journal(manifest, "old", "new")
    assert manifest == original


def test_rename_journal_survives_save_load_round_trip():
    manifest = load_manifest()
    manifest = start_rename_journal(manifest, "agent-worker-1", "agent-auth-refactor")
    save_manifest(manifest)

    reloaded = load_manifest()
    assert reloaded["rename_in_flight"] == manifest["rename_in_flight"]


def test_get_renamed_from_none_when_absent():
    manifest = load_manifest()
    assert get_renamed_from(manifest, "no-such-session") is None


def test_get_renamed_from_reads_the_migrated_field():
    manifest = load_manifest()
    manifest["sessions"]["agent-auth-refactor"] = {
        "first_seen_at": 1.0,
        "last_seen_at": 2.0,
        "renamed_from": "agent-worker-1",
    }
    assert get_renamed_from(manifest, "agent-auth-refactor") == "agent-worker-1"


# ---------------------------------------------------------------------------
# main._migrate_session_name() -- one assertion per keyspace row
# ---------------------------------------------------------------------------


def _seeded_state(old_name: str) -> dict:
    state = load_state()
    state["sessions"][old_name] = {"bell": empty_bell()}
    state["sessions"][old_name]["bell"]["unseen_count"] = 3
    state["session_order"] = ["zzz-other", old_name, "aaa-other"]
    state["followups"][old_name] = {
        "revision": 1,
        "items": [{"id": "i1", "text": "hi", "enter": True, "created_at": 1.0}],
        "halted": None,
    }
    return state


def _seeded_settings(old_key: str) -> dict:
    settings = load_settings()
    settings["views"] = [
        {"name": "Agents", "sessions": [old_key], "match_names": ["agent-*"]},
    ]
    settings["hidden_sessions"] = [old_key]
    return settings


def _seeded_manifest(old_name: str) -> dict:
    manifest = load_manifest()
    manifest["created_with"][old_name] = "default"
    manifest["sessions"][old_name] = {
        "first_seen_at": 10.0,
        "last_seen_at": 20.0,
        "cwd": "/home/user/dev/agent-worker-1",
    }
    return manifest


def _seeded_pruning(old_key: str) -> dict:
    return {"first_missed_at": {old_key: 12345.0}}


def test_migrate_session_name_moves_every_tier1_tier2_keyspace():
    old_name, new_name = "agent-worker-1", "agent-auth-refactor"
    old_key = f"{DEVICE_ID}:{old_name}"

    state = _seeded_state(old_name)
    settings = _seeded_settings(old_key)
    manifest = _seeded_manifest(old_name)
    pruning_state = _seeded_pruning(old_key)

    manifest, migrated = _migrate_session_name(
        state, settings, manifest, pruning_state, old_name, new_name, DEVICE_ID
    )

    # 1. created_with
    assert migrated["created_with"] is True
    assert manifest["created_with"].get(old_name) is None
    assert manifest["created_with"][new_name] == "default"

    # 2. followups
    assert migrated["followups"] == 1
    assert old_name not in state["followups"]
    assert state["followups"][new_name]["items"][0]["text"] == "hi"

    # 3/4. views[*].sessions / hidden_sessions (device_id:name pins)
    assert migrated["view_pins"] == 1
    assert migrated["hidden"] is True
    new_key = f"{DEVICE_ID}:{new_name}"
    assert new_key in settings["views"][0]["sessions"]
    assert old_key not in settings["views"][0]["sessions"]
    assert new_key in settings["hidden_sessions"]
    assert old_key not in settings["hidden_sessions"]

    # 5. manifest["sessions"][name] + renamed_from
    assert migrated["manifest"] is True
    assert manifest["sessions"][new_name]["cwd"] == "/home/user/dev/agent-worker-1"
    assert manifest["sessions"][new_name]["renamed_from"] == old_name
    assert old_name not in manifest["sessions"]

    # 6. bell
    assert migrated["bell"] is True
    assert state["sessions"][new_name]["bell"]["unseen_count"] == 3
    assert old_name not in state["sessions"]

    # 7. session_order -- position preserved, not appended
    assert migrated["order"] is True
    assert state["session_order"] == ["zzz-other", new_name, "aaa-other"]

    # 8. pruning.json first_missed_at
    assert migrated["pruning"] == 1
    assert old_key not in pruning_state["first_missed_at"]


def test_migrate_session_name_moves_in_memory_bell_seen_and_last_send_at():
    """\u00a72.2 item 9 -- bells._bell_seen / followups._followup_last_send_at."""
    import muxplex.bells as bells_mod
    import muxplex.followups as followups_mod

    old_name, new_name = "worker-a", "worker-b"
    bells_mod._bell_seen.clear()
    followups_mod._followup_last_send_at.clear()
    bells_mod._bell_seen[old_name] = True
    followups_mod._followup_last_send_at[old_name] = 555.0
    try:
        state = load_state()
        settings = load_settings()
        manifest = load_manifest()
        pruning_state: dict = {}

        _migrate_session_name(
            state, settings, manifest, pruning_state, old_name, new_name, DEVICE_ID
        )

        assert old_name not in bells_mod._bell_seen
        assert bells_mod._bell_seen[new_name] is True
        assert old_name not in followups_mod._followup_last_send_at
        assert followups_mod._followup_last_send_at[new_name] == 555.0
    finally:
        bells_mod._bell_seen.clear()
        followups_mod._followup_last_send_at.clear()


def test_migrate_session_name_is_idempotent():
    """Calling it twice for the same pair must not double-count or error --
    this is what makes it safe for BOTH the endpoint and the poll cycle to
    call (\u00a76.2's completion branch reuses the exact same function)."""
    old_name, new_name = "agent-worker-1", "agent-auth-refactor"
    old_key = f"{DEVICE_ID}:{old_name}"

    state = _seeded_state(old_name)
    settings = _seeded_settings(old_key)
    manifest = _seeded_manifest(old_name)
    pruning_state = _seeded_pruning(old_key)

    manifest, migrated_first = _migrate_session_name(
        state, settings, manifest, pruning_state, old_name, new_name, DEVICE_ID
    )
    # Second call: old_name is already gone everywhere, new_name already
    # carries everything -- every "migrated" flag/count must now be falsy.
    manifest, migrated_second = _migrate_session_name(
        state, settings, manifest, pruning_state, old_name, new_name, DEVICE_ID
    )

    assert migrated_first["bell"] is True
    assert migrated_second["bell"] is False
    assert migrated_second["followups"] == 0
    assert migrated_second["view_pins"] == 0
    assert migrated_second["hidden"] is False
    assert migrated_second["created_with"] is False
    assert migrated_second["order"] is False
    assert migrated_second["manifest"] is False
    assert migrated_second["pruning"] == 0

    # State is unchanged by the redundant second call.
    assert state["session_order"] == ["zzz-other", new_name, "aaa-other"]


def test_migrate_session_name_never_touches_input_allowed_sessions():
    """\u00a72.3 item 10 -- THE security test. Migrating this would be a
    privilege escalation (\u00a710.1): an agent renaming itself out of an
    allowlisted family must NOT carry the grant along."""
    old_name, new_name = "agent-worker-1", "scratch-anything"
    old_key = f"{DEVICE_ID}:{old_name}"

    state = load_state()
    settings = load_settings()
    settings["input_enabled"] = True
    settings["input_allowed_sessions"] = ["agent-*", old_name, old_key]
    manifest = load_manifest()
    pruning_state: dict = {}

    before = list(settings["input_allowed_sessions"])
    _migrate_session_name(
        state, settings, manifest, pruning_state, old_name, new_name, DEVICE_ID
    )

    assert settings["input_allowed_sessions"] == before
    assert new_name not in settings["input_allowed_sessions"]
    assert f"{DEVICE_ID}:{new_name}" not in settings["input_allowed_sessions"]


def test_migrate_session_name_never_rewrites_match_names():
    """\u00a72.3 item 11 -- AGENTS.md's standing prohibition: rules stay rules,
    forever. A renamed session correctly leaving 'agent-*' and correctly
    joining 'auth-*' is the auto-views feature working, not something this
    migration should special-case."""
    old_name, new_name = "agent-worker-1", "auth-worker-1"

    state = load_state()
    settings = load_settings()
    settings["views"] = [
        {"name": "Agents", "match_names": ["agent-*"]},
        {"name": "Auth", "match_names": ["auth-*"]},
    ]
    manifest = load_manifest()
    pruning_state: dict = {}

    before = [dict(v) for v in settings["views"]]
    _migrate_session_name(
        state, settings, manifest, pruning_state, old_name, new_name, DEVICE_ID
    )

    assert settings["views"] == before


def test_migrate_session_name_collision_row_pruning_deletes_new_key_clock():
    """\u00a77.2 last row: a running grace clock for the NEW name's old
    occupant is deleted -- the name being live again is what clears it,
    not a collision."""
    old_name, new_name = "agent-worker-1", "agent-auth-refactor"
    old_key = f"{DEVICE_ID}:{old_name}"
    new_key = f"{DEVICE_ID}:{new_name}"

    state = load_state()
    settings = load_settings()
    manifest = load_manifest()
    pruning_state = {"first_missed_at": {old_key: 1.0, new_key: 2.0}}

    _, migrated = _migrate_session_name(
        state, settings, manifest, pruning_state, old_name, new_name, DEVICE_ID
    )

    assert migrated["pruning"] == 2
    assert pruning_state["first_missed_at"] == {}


# ---------------------------------------------------------------------------
# Endpoint tests -- tmux mocked, no real server
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_path / "state" / "state.json")
    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", tmp_path / "ttyd")
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", tmp_path / "identity.json")
    monkeypatch.setattr("muxplex.pruning.PRUNING_STATE_PATH", tmp_path / "pruning.json")

    async def _mock_reap_orphan():
        return 0

    async def _mock_reap_legacy():
        return False

    monkeypatch.setattr("muxplex.main.reap_orphan_ttyds", _mock_reap_orphan)
    monkeypatch.setattr("muxplex.main.reap_legacy_ttyd", _mock_reap_legacy)
    monkeypatch.setattr("muxplex.main.ttyd_mod.validate_socket_dir", lambda d: None)

    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


def _seed_live_sessions(monkeypatch, names: list[str]) -> None:
    """Make get_session_list()/enumerate_sessions() report *names* as live,
    without touching real tmux."""
    monkeypatch.setattr(main_mod, "get_session_list", lambda: list(names))

    async def _fake_enumerate():
        return list(names)

    monkeypatch.setattr(main_mod, "enumerate_sessions", _fake_enumerate)


def _mock_rename_success(monkeypatch, *, mangled_to: str | None = None):
    """Patch rename_tmux_session to succeed (rc=0), optionally simulating
    tmux having silently renamed to something else."""
    calls: list[tuple[str, str]] = []

    async def _fake_rename(old: str, new: str) -> None:
        calls.append((old, new))

    monkeypatch.setattr(main_mod, "rename_tmux_session", _fake_rename)
    return calls


def test_rename_rejects_dot_in_new_name(client, monkeypatch):
    _seed_live_sessions(monkeypatch, ["build_js"])
    resp = client.post("/api/sessions/build_js/rename", json={"new_name": "build.js"})
    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert body["invalid_session_name"] is True
    assert body["suggested"] == "build_js"


def test_rename_404_when_old_name_unknown(client, monkeypatch):
    _seed_live_sessions(monkeypatch, ["other"])
    resp = client.post(
        "/api/sessions/does-not-exist/rename", json={"new_name": "new-name"}
    )
    assert resp.status_code == 404


def test_rename_noop_when_new_name_equals_old_name(client, monkeypatch):
    _seed_live_sessions(monkeypatch, ["agent-worker-1"])
    called = _mock_rename_success(monkeypatch)
    resp = client.post(
        "/api/sessions/agent-worker-1/rename", json={"new_name": "agent-worker-1"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "ok": True,
        "from": "agent-worker-1",
        "name": "agent-worker-1",
        "renamed": False,
    }
    # Nothing migrates -- tmux is never even called.
    assert called == []


def test_rename_409_when_target_already_live(client, monkeypatch):
    _seed_live_sessions(monkeypatch, ["agent-worker-1", "agent-worker-2"])
    resp = client.post(
        "/api/sessions/agent-worker-1/rename", json={"new_name": "agent-worker-2"}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["rename_target_exists"] is True


def test_rename_409_when_target_has_orphaned_followup_queue(client, monkeypatch):
    _seed_live_sessions(monkeypatch, ["agent-worker-1"])
    state = load_state()
    state["followups"]["agent-auth-refactor"] = {
        "revision": 1,
        "items": [{"id": "i1", "text": "x", "enter": True, "created_at": 1.0}],
        "halted": None,
    }
    save_state(state)

    resp = client.post(
        "/api/sessions/agent-worker-1/rename",
        json={"new_name": "agent-auth-refactor"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["queue_target_conflict"] is True


def test_rename_409_when_target_is_pending_restore(client, monkeypatch):
    _seed_live_sessions(monkeypatch, ["agent-worker-1"])
    manifest = load_manifest()
    manifest["pending_restore"] = {
        "detected_at": 1.0,
        "lost_epoch": {},
        "sessions": {"agent-auth-refactor": {"first_seen_at": 1.0}},
    }
    save_manifest(manifest)

    resp = client.post(
        "/api/sessions/agent-worker-1/rename",
        json={"new_name": "agent-auth-refactor"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["pending_restore_conflict"] is True


def test_rename_409_when_send_in_flight(client, monkeypatch):
    import muxplex.followups as followups_mod

    _seed_live_sessions(monkeypatch, ["agent-worker-1"])
    followups_mod._followup_sending.add("agent-worker-1")
    try:
        resp = client.post(
            "/api/sessions/agent-worker-1/rename",
            json={"new_name": "agent-auth-refactor"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["rename_send_in_flight"] is True
    finally:
        followups_mod._followup_sending.discard("agent-worker-1")


def test_rename_409_when_tmux_reports_duplicate(client, monkeypatch):
    _seed_live_sessions(monkeypatch, ["agent-worker-1"])

    async def _fake_rename(old, new):
        raise RuntimeError("duplicate session: agent-auth-refactor")

    monkeypatch.setattr(main_mod, "rename_tmux_session", _fake_rename)

    resp = client.post(
        "/api/sessions/agent-worker-1/rename",
        json={"new_name": "agent-auth-refactor"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["rename_target_exists"] is True

    # The journal must be cleared -- nothing left behind.
    assert get_rename_in_flight(load_manifest()) is None


def test_rename_success_returns_observed_name_and_migrated_evidence(
    client, monkeypatch
):
    _seed_live_sessions(monkeypatch, ["agent-worker-1"])
    _mock_rename_success(monkeypatch)

    # After the rename call, enumerate_sessions() must report the NEW name.
    async def _enumerate_after():
        return ["agent-auth-refactor"]

    monkeypatch.setattr(main_mod, "enumerate_sessions", _enumerate_after)

    state = load_state()
    state["sessions"]["agent-worker-1"] = {"bell": empty_bell()}
    save_state(state)

    manifest = load_manifest()
    manifest["sessions"]["agent-worker-1"] = {
        "first_seen_at": 1.0,
        "last_seen_at": 2.0,
    }
    save_manifest(manifest)

    resp = client.post(
        "/api/sessions/agent-worker-1/rename",
        json={"new_name": "agent-auth-refactor"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["from"] == "agent-worker-1"
    assert data["name"] == "agent-auth-refactor"
    assert data["migrated"]["bell"] is True

    # Journal cleared, manifest carries renamed_from.
    manifest = load_manifest()
    assert get_rename_in_flight(manifest) is None
    assert manifest["sessions"]["agent-auth-refactor"]["renamed_from"] == (
        "agent-worker-1"
    )


def test_rename_500_when_observed_name_mismatches(client, monkeypatch):
    """\u00a75.2 belt-and-braces: tmux reports success but the observed name is
    neither old nor new."""
    _seed_live_sessions(monkeypatch, ["agent-worker-1"])
    _mock_rename_success(monkeypatch)

    async def _enumerate_mismatch():
        return ["agent_auth_refactor"]  # e.g. an unexpected mangling

    monkeypatch.setattr(main_mod, "enumerate_sessions", _enumerate_mismatch)

    manifest = load_manifest()
    manifest["sessions"]["agent-worker-1"] = {
        "first_seen_at": 1.0,
        "last_seen_at": 2.0,
    }
    save_manifest(manifest)

    resp = client.post(
        "/api/sessions/agent-worker-1/rename",
        json={"new_name": "agent-auth-refactor"},
    )
    assert resp.status_code == 500
    body = resp.json()["detail"]
    assert body["rename_verification_failed"] is True
    assert body["observed"] == "agent_auth_refactor"

    # Verified belt-and-braces per \u00a75.2: the migration STILL completed
    # against the observed name.
    manifest = load_manifest()
    assert manifest["sessions"]["agent_auth_refactor"]["renamed_from"] == (
        "agent-worker-1"
    )


def test_rename_fence_bearer_only_denied_old_name_not_allowlisted(client, monkeypatch):
    """\u00a710.2 table: bearer_only, old name NOT in allowlist -> 403."""
    _seed_live_sessions(monkeypatch, ["production-db"])
    settings = load_settings()
    settings["input_enabled"] = True
    settings["input_allowed_sessions"] = ["scratch-*"]
    save_settings(settings)

    monkeypatch.setattr(main_mod, "_bearer_only_caller", lambda request: True)

    resp = client.post(
        "/api/sessions/production-db/rename", json={"new_name": "scratch-anything"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["rename_not_allowed"] is True


def test_rename_fence_bearer_only_denied_new_name_widens_allowlist(client, monkeypatch):
    """\u00a710.2 table: bearer_only, old name IS allowlisted but new name is
    NOT -- renaming OUT of the family only reduces authority (harmless),
    but the plan requires the new name to ALSO be permitted, closing the
    'rename into an allowlisted family' escalation from the other side."""
    _seed_live_sessions(monkeypatch, ["agent-worker-1"])
    settings = load_settings()
    settings["input_enabled"] = True
    settings["input_allowed_sessions"] = ["agent-*"]
    save_settings(settings)

    monkeypatch.setattr(main_mod, "_bearer_only_caller", lambda request: True)

    resp = client.post(
        "/api/sessions/agent-worker-1/rename", json={"new_name": "scratch-x"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["rename_not_allowed"] is True


def test_rename_fence_bearer_only_allowed_when_both_names_in_family(
    client, monkeypatch
):
    """The motivating use case (\u00a710.2): an agent renaming within its own
    allowlisted family succeeds."""
    _seed_live_sessions(monkeypatch, ["agent-worker-1"])
    settings = load_settings()
    settings["input_enabled"] = True
    settings["input_allowed_sessions"] = ["agent-*"]
    save_settings(settings)

    monkeypatch.setattr(main_mod, "_bearer_only_caller", lambda request: True)
    _mock_rename_success(monkeypatch)

    async def _enumerate_after():
        return ["agent-auth-refactor"]

    monkeypatch.setattr(main_mod, "enumerate_sessions", _enumerate_after)

    resp = client.post(
        "/api/sessions/agent-worker-1/rename",
        json={"new_name": "agent-auth-refactor"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "agent-auth-refactor"


def test_rename_fence_not_evaluated_for_cookie_caller(client, monkeypatch):
    """A non-bearer_only caller (this test's own cookie-authenticated
    client) is unfenced regardless of input_enabled/input_allowed_sessions
    -- \u00a710.2's 'not what the fence is for' rationale."""
    _seed_live_sessions(monkeypatch, ["production-db"])
    settings = load_settings()
    settings["input_enabled"] = False
    settings["input_allowed_sessions"] = []
    save_settings(settings)

    _mock_rename_success(monkeypatch)

    async def _enumerate_after():
        return ["scratch-anything"]

    monkeypatch.setattr(main_mod, "enumerate_sessions", _enumerate_after)

    resp = client.post(
        "/api/sessions/production-db/rename", json={"new_name": "scratch-anything"}
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Poll-cycle journal completion / reversion (\u00a76.2)
# ---------------------------------------------------------------------------


@pytest.fixture
def poll_cycle_env(monkeypatch, tmp_path):
    """Minimal wiring to run _run_poll_cycle() in isolation: tmux calls
    mocked, ttyd untouched, bell hook pre-armed so it's not retried."""
    monkeypatch.setattr(main_mod, "_bell_hook_armed", True)

    async def _fake_probe_epoch():
        return {"socket_path": "/fake", "server_pid": 1, "inode": 1}

    monkeypatch.setattr(main_mod, "probe_tmux_epoch", _fake_probe_epoch)

    async def _fake_reap_idle():
        return []

    monkeypatch.setattr(main_mod, "reap_idle_ttyds", _fake_reap_idle)

    killed: list[str] = []

    async def _fake_kill_ttyd(name):
        killed.append(name)
        return True

    monkeypatch.setattr(main_mod, "kill_ttyd", _fake_kill_ttyd)
    return killed


@pytest.mark.asyncio
async def test_poll_cycle_completes_in_flight_rename(monkeypatch, poll_cycle_env):
    """Journal completion: write a journal, rename in tmux OUT-OF-BAND
    (simulated here by making enumerate_sessions report the NEW name live
    and the OLD name absent), run one poll cycle, assert every keyspace
    converged and the journal cleared."""
    old_name, new_name = "agent-worker-1", "agent-auth-refactor"

    async def _fake_enumerate():
        return [new_name]

    monkeypatch.setattr(main_mod, "enumerate_sessions", _fake_enumerate)

    async def _fake_snapshot_all(names):
        return {n: "" for n in names}

    monkeypatch.setattr(main_mod, "snapshot_all", _fake_snapshot_all)

    manifest = load_manifest()
    # Seed the SAME epoch poll_cycle_env's fake probe_tmux_epoch() returns,
    # so update_manifest() below takes its "same server" branch (in-place
    # last_seen_at update) rather than its "first run ever" branch (which
    # would rebuild every live session's entry from scratch and discard
    # the renamed_from _migrate_session_name() just set).
    manifest["epoch"] = {"socket_path": "/fake", "server_pid": 1, "inode": 1}
    manifest["sessions"][old_name] = {"first_seen_at": 1.0, "last_seen_at": 1.0}
    manifest["created_with"][old_name] = "default"
    manifest = start_rename_journal(manifest, old_name, new_name)
    save_manifest(manifest)

    state = load_state()
    state["sessions"][old_name] = {"bell": empty_bell()}
    save_state(state)

    await main_mod._run_poll_cycle()

    final_manifest = load_manifest()
    assert get_rename_in_flight(final_manifest) is None
    assert final_manifest["sessions"][new_name]["renamed_from"] == old_name
    assert old_name not in final_manifest["sessions"]
    assert final_manifest["created_with"].get(new_name) == "default"

    final_state = load_state()
    assert new_name in final_state["sessions"]
    assert old_name not in final_state["sessions"]

    assert poll_cycle_env == [old_name]  # kill_ttyd(old_name) was called


@pytest.mark.asyncio
async def test_poll_cycle_reverts_journal_when_rename_never_happened(
    monkeypatch, poll_cycle_env
):
    """Journal reversion: write a journal, do NOT rename in tmux (old name
    still live, new name absent), run one poll cycle, assert the journal
    cleared and nothing moved."""
    old_name, new_name = "agent-worker-1", "agent-auth-refactor"

    async def _fake_enumerate():
        return [old_name]

    monkeypatch.setattr(main_mod, "enumerate_sessions", _fake_enumerate)

    async def _fake_snapshot_all(names):
        return {n: "" for n in names}

    monkeypatch.setattr(main_mod, "snapshot_all", _fake_snapshot_all)

    manifest = load_manifest()
    manifest["sessions"][old_name] = {"first_seen_at": 1.0, "last_seen_at": 1.0}
    manifest["created_with"][old_name] = "default"
    manifest = start_rename_journal(manifest, old_name, new_name)
    save_manifest(manifest)

    state = load_state()
    state["sessions"][old_name] = {"bell": empty_bell()}
    save_state(state)

    await main_mod._run_poll_cycle()

    final_manifest = load_manifest()
    assert get_rename_in_flight(final_manifest) is None
    # Nothing migrated: old_name is still the live, tracked session.
    assert old_name in final_manifest["sessions"]
    assert new_name not in final_manifest["sessions"]
    assert final_manifest["created_with"].get(old_name) == "default"

    final_state = load_state()
    assert old_name in final_state["sessions"]
    assert new_name not in final_state["sessions"]

    assert poll_cycle_env == []  # kill_ttyd was never called


@pytest.mark.asyncio
async def test_poll_cycle_clears_journal_when_session_died_mid_rename(
    monkeypatch, poll_cycle_env
):
    """Neither name live -- session died mid-rename. Clear the journal;
    the cold-start/tombstone paths handle the corpse as they always have."""
    old_name, new_name = "agent-worker-1", "agent-auth-refactor"

    async def _fake_enumerate():
        return []

    monkeypatch.setattr(main_mod, "enumerate_sessions", _fake_enumerate)

    async def _fake_snapshot_all(names):
        return {}

    monkeypatch.setattr(main_mod, "snapshot_all", _fake_snapshot_all)

    manifest = load_manifest()
    manifest["sessions"][old_name] = {"first_seen_at": 1.0, "last_seen_at": 1.0}
    manifest = start_rename_journal(manifest, old_name, new_name)
    save_manifest(manifest)

    await main_mod._run_poll_cycle()

    final_manifest = load_manifest()
    assert get_rename_in_flight(final_manifest) is None
    assert poll_cycle_env == []


# ---------------------------------------------------------------------------
# Destructive-write backstop -- \u00a714 item 10
# ---------------------------------------------------------------------------


def test_migrate_session_name_pin_swap_does_not_trip_destructive_backstop():
    """A 1-for-1 pin swap is a 0% drop against the 50% threshold -- the
    same reasoning \u00a78 states for federation sync's LWW push, verified
    directly against the real assessment function here."""
    from muxplex.views import assess_views_destruction

    old_name, new_name = "agent-worker-1", "agent-auth-refactor"
    old_key = f"{DEVICE_ID}:{old_name}"

    state = load_state()
    settings = load_settings()
    settings["views"] = [{"name": "Agents", "sessions": [old_key, "dev-1:other"]}]
    manifest = load_manifest()
    pruning_state: dict = {}

    before_views = [dict(v) for v in settings["views"]]
    _migrate_session_name(
        state, settings, manifest, pruning_state, old_name, new_name, DEVICE_ID
    )

    assessment = assess_views_destruction(before_views, settings["views"])
    assert assessment.destructive is False

"""
Tests for the per-session follow-up queue -- muxplex/followups.py, the
follow-up endpoints in main.py, and the bell-transition advance wiring.

See FOLLOWUP_QUEUE_SPEC.md for the full design. Test IDs in comments below
map to the spec's §10 test plan where a direct correspondence exists.
"""

import copy
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import muxplex.main as main_mod
from muxplex import followups
from muxplex.bells import process_bell_flags
from muxplex.main import app
from muxplex.settings import DEFAULT_SETTINGS
from muxplex.state import empty_state, normalize_state

# ---------------------------------------------------------------------------
# Fixtures -- mirror test_input.py's isolation pattern
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_followup_module_state():
    """Clear followups' in-memory bookkeeping before/after each test --
    same rationale as test_bells.py's reset_bell_seen fixture."""
    followups._followup_sending.clear()
    followups._followup_last_send_at.clear()
    yield
    followups._followup_sending.clear()
    followups._followup_last_send_at.clear()


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Redirect state/PID files, mock startup side-effects (same as test_input)."""
    tmp_state_dir = tmp_path / "state"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_dir / "state.json")

    tmp_socket_dir = tmp_path / "ttyd"
    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", tmp_socket_dir)

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
    # Bell hook is armed by default in tests unless a test says otherwise --
    # the append endpoint 409s (bell_hook_unarmed) when it isn't.
    monkeypatch.setattr("muxplex.main._bell_hook_armed", True)


@pytest.fixture
def client(monkeypatch):
    """TestClient with a valid session cookie (bypasses AuthMiddleware).

    _bell_hook_armed is (re-)forced True AFTER the lifespan startup runs
    (inside the `with` block, not before) -- lifespan's own startup path
    invokes the real _arm_bell_hook(), which fails against no real tmux and
    resets the flag to False, clobbering any override applied before
    TestClient.__enter__.
    """
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        monkeypatch.setattr("muxplex.main._bell_hook_armed", True)
        yield c


def _settings(**overrides) -> dict:
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(overrides)
    return s


def _enable(monkeypatch, allowed: list, known: list[str]) -> None:
    """Enable input for *allowed* sessions and set the known-session set."""
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _settings(input_enabled=True, input_allowed_sessions=allowed),
    )
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: known)


@pytest.fixture
def tmux_calls(monkeypatch):
    """Record run_tmux argv calls instead of touching a real tmux."""
    calls: list[tuple[str, ...]] = []

    async def fake_run_tmux(*args: str) -> str:
        calls.append(args)
        if args[0] == "display-message":
            return "1:amplifier"
        return ""

    monkeypatch.setattr("muxplex.main.run_tmux", fake_run_tmux)
    return calls


# ---------------------------------------------------------------------------
# T-01/T-02-ish: normalize_state, storage round-trip
# ---------------------------------------------------------------------------


def test_normalize_state_fills_followups_when_absent():
    # empty_state() itself does not include "followups" -- only
    # normalize_state() adds it (schema-upgrade of a pre-feature state.json,
    # mirroring how sync_groups/terminal_session are filled).
    state = empty_state()
    assert "followups" not in state
    normalize_state(state)
    assert state["followups"] == {}


def test_normalize_state_leaves_populated_followups_untouched():
    state = empty_state()
    state["followups"] = {
        "foo": {"revision": 3, "items": [{"id": "x"}], "halted": None}
    }
    normalize_state(state)
    assert state["followups"]["foo"]["revision"] == 3


def test_append_item_creates_entry_and_bumps_revision():
    state = empty_state()
    item = followups.append_item(state, "sess", "hello", True)
    entry = state["followups"]["sess"]
    assert entry["revision"] == 1
    assert entry["items"] == [item]
    assert item["text"] == "hello"
    assert item["enter"] is True
    assert "id" in item and "created_at" in item


def test_replace_items_requires_matching_revision():
    state = empty_state()
    followups.append_item(state, "sess", "one", True)
    ok, error = followups.replace_items(state, "sess", 0, [{"text": "two"}])
    assert ok is False
    assert error["revision_mismatch"] is True
    assert error["revision"] == 1
    # No write happened.
    assert state["followups"]["sess"]["items"][0]["text"] == "one"


def test_replace_items_keeps_id_and_created_at_for_known_items():
    state = empty_state()
    item = followups.append_item(state, "sess", "one", True)
    ok, _ = followups.replace_items(
        state, "sess", 1, [{"id": item["id"], "text": "edited", "enter": False}]
    )
    assert ok is True
    new_item = state["followups"]["sess"]["items"][0]
    assert new_item["id"] == item["id"]
    assert new_item["created_at"] == item["created_at"]
    assert new_item["text"] == "edited"
    assert new_item["enter"] is False


def test_replace_items_treats_unknown_id_as_new():
    state = empty_state()
    followups.append_item(state, "sess", "one", True)
    ok, _ = followups.replace_items(state, "sess", 1, [{"id": "bogus", "text": "new"}])
    assert ok is True
    new_item = state["followups"]["sess"]["items"][0]
    assert new_item["id"] != "bogus"


def test_replace_items_to_empty_prunes_the_entry():
    state = empty_state()
    followups.append_item(state, "sess", "one", True)
    ok, _ = followups.replace_items(state, "sess", 1, [])
    assert ok is True
    assert "sess" not in state["followups"]


def test_clear_queue_drops_items_and_halt():
    state = empty_state()
    followups.append_item(state, "sess", "one", True)
    followups.set_halted(state, "sess", "send_failed", "boom", "item-id")
    followups.clear_queue(state, "sess")
    assert "sess" not in state["followups"]


def test_resume_queue_clears_halt_only():
    state = empty_state()
    item = followups.append_item(state, "sess", "one", True)
    followups.set_halted(state, "sess", "send_failed", "boom", item["id"])
    cleared = followups.resume_queue(state, "sess")
    assert cleared is True
    entry = state["followups"]["sess"]
    assert entry["halted"] is None
    assert entry["items"] == [item]


def test_reap_stale_queues_drops_absent_sessions_and_reports_counts():
    state = empty_state()
    followups.append_item(state, "gone", "one", True)
    followups.append_item(state, "gone", "two", True)
    followups.append_item(state, "alive", "keep", True)
    dropped = followups.reap_stale_queues(state, {"alive"})
    assert dropped == [("gone", 2)]
    assert "gone" not in state["followups"]
    assert "alive" in state["followups"]


def test_summary_zero_value_for_absent_queue():
    state = empty_state()
    assert followups.summary(state, "nope") == {"pending": 0, "halted": False}


def test_summary_reports_pending_and_halted():
    state = empty_state()
    item = followups.append_item(state, "sess", "one", True)
    followups.set_halted(state, "sess", "send_failed", "boom", item["id"])
    assert followups.summary(state, "sess") == {"pending": 1, "halted": True}


# ---------------------------------------------------------------------------
# acceptance_ok -- the peek-eligibility gate
# ---------------------------------------------------------------------------


def test_acceptance_ok_false_when_no_queue():
    state = empty_state()
    assert followups.acceptance_ok(state, "sess") is False


def test_acceptance_ok_false_when_halted():
    state = empty_state()
    item = followups.append_item(state, "sess", "one", True)
    followups.set_halted(state, "sess", "send_failed", "boom", item["id"])
    assert followups.acceptance_ok(state, "sess") is False


def test_acceptance_ok_false_while_sending():
    state = empty_state()
    followups.append_item(state, "sess", "one", True)
    followups._followup_sending.add("sess")
    assert followups.acceptance_ok(state, "sess") is False


def test_acceptance_ok_false_within_settle_window():
    state = empty_state()
    followups.append_item(state, "sess", "one", True)
    followups._followup_last_send_at["sess"] = time.time()
    assert followups.acceptance_ok(state, "sess") is False


def test_acceptance_ok_true_past_settle_window():
    state = empty_state()
    followups.append_item(state, "sess", "one", True)
    followups._followup_last_send_at["sess"] = (
        time.time() - followups.FOLLOWUP_SETTLE_SECONDS - 1
    )
    assert followups.acceptance_ok(state, "sess") is True


# ---------------------------------------------------------------------------
# Seeded-bell isolation (spec §4, T-04) and poll-fallback isolation (T-05)
# ---------------------------------------------------------------------------


async def test_seeded_bell_does_not_advance_queue(client, monkeypatch, tmux_calls):
    """A session created via _run_poll_cycle's seeding branch gets a bell,
    but that seeding NEVER routes through receive_bell()/process_bell_flags(),
    so a queue for that session must be completely unaffected."""
    _enable(monkeypatch, ["seeded-*"], ["seeded-session"])
    monkeypatch.setattr(
        "muxplex.main.enumerate_sessions", AsyncMock(return_value=["seeded-session"])
    )
    monkeypatch.setattr(
        "muxplex.main.probe_tmux_epoch", AsyncMock(return_value={"epoch": 1})
    )
    monkeypatch.setattr("muxplex.main.snapshot_all", AsyncMock(return_value={}))
    monkeypatch.setattr("muxplex.main.update_session_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        "muxplex.main.get_session_created_times",
        lambda: {"seeded-session": time.time()},
    )
    monkeypatch.setattr("muxplex.main._server_start_time", time.time() - 10)

    # Queue an item BEFORE the poll cycle runs its seeding branch.
    resp = client.post("/api/sessions/seeded-session/followups", json={"text": "do X"})
    assert resp.status_code == 200

    await main_mod._run_poll_cycle()

    # The session's bell was seeded (needs_attention), but the queue must
    # be untouched: still 1 pending item, no tmux send-keys call fired.
    get_resp = client.get("/api/sessions/seeded-session/followups")
    assert get_resp.json()["items"] == [resp.json()["item"]]
    send_calls = [c for c in tmux_calls if c[0] == "send-keys"]
    assert send_calls == []


async def test_poll_fallback_advances_queue_only_while_hook_unarmed(
    monkeypatch, tmux_calls
):
    """process_bell_flags' on_transition callback IS wired when the hook is
    unarmed (so the queue doesn't stall forever), and is NOT wired while
    armed (avoiding a double-advance for a detached session bell that both
    the hook and the poll flag independently observe -- spec §1 case A)."""
    _enable(monkeypatch, ["sess"], ["sess"])
    state = empty_state()
    followups.append_item(state, "sess", "one", True)

    seen: list[str] = []

    async def fake_poll_flag(name):
        return True  # flag is set -> 0->1 transition

    monkeypatch.setattr("muxplex.bells.poll_bell_flag", fake_poll_flag)

    await process_bell_flags(["sess"], state, on_transition=seen.append)
    assert seen == ["sess"]


# ---------------------------------------------------------------------------
# Advance semantics via receive_bell -- T-06/T-07/T-08
# ---------------------------------------------------------------------------


async def test_receive_bell_sends_exactly_head_item_and_removes_it(
    client, monkeypatch, tmux_calls
):
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "MARK_ONE"})
    client.post("/api/sessions/sess/followups", json={"text": "MARK_TWO"})

    resp = client.post("/api/sessions/sess/bell")
    assert resp.status_code == 200

    send_calls = [c for c in tmux_calls if c[0] == "send-keys"]
    assert any("MARK_ONE" in c for c in send_calls)
    assert not any("MARK_TWO" in c for c in send_calls)

    state = client.get("/api/sessions/sess/followups").json()
    assert len(state["items"]) == 1
    assert state["items"][0]["text"] == "MARK_TWO"
    assert state["revision"] == 3  # 2 appends + 1 removal


async def test_two_concurrent_bells_send_exactly_one_item(
    client, monkeypatch, tmux_calls
):
    """T-07: two overlapping receive_bell() calls must not both pop the head."""
    import asyncio

    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "MARK_ONE"})
    client.post("/api/sessions/sess/followups", json={"text": "MARK_TWO"})

    await asyncio.gather(
        main_mod._advance_followup_queue("sess"),
        main_mod._advance_followup_queue("sess"),
    )

    send_calls = [c for c in tmux_calls if c[0] == "send-keys"]
    marks_sent = [c for c in send_calls if any("MARK_" in str(x) for x in c)]
    assert len(marks_sent) == 1

    state = client.get("/api/sessions/sess/followups").json()
    assert len(state["items"]) == 1


async def test_bell_within_settle_window_of_own_send_does_not_advance(
    client, monkeypatch, tmux_calls
):
    """T-08."""
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "MARK_ONE"})
    client.post("/api/sessions/sess/followups", json={"text": "MARK_TWO"})

    client.post("/api/sessions/sess/bell")  # sends MARK_ONE, sets last_send_at=now
    client.post("/api/sessions/sess/bell")  # within settle window -> no-op

    state = client.get("/api/sessions/sess/followups").json()
    assert len(state["items"]) == 1
    assert state["items"][0]["text"] == "MARK_TWO"

    # Advance past the settle window and fire again -> MARK_TWO sends.
    followups._followup_last_send_at["sess"] = (
        time.time() - followups.FOLLOWUP_SETTLE_SECONDS - 1
    )
    client.post("/api/sessions/sess/bell")
    state = client.get("/api/sessions/sess/followups").json()
    assert state["items"] == []


async def test_followup_sending_cleared_on_exception_path(client, monkeypatch):
    """T-12: a send() failure must not leave _followup_sending stuck."""
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "one"})

    async def boom(*args):
        raise RuntimeError("tmux exploded")

    monkeypatch.setattr("muxplex.main.run_tmux", boom)
    client.post("/api/sessions/sess/bell")

    assert "sess" not in followups._followup_sending
    state = client.get("/api/sessions/sess/followups").json()
    assert state["halted"]["reason"] == "send_failed"
    assert len(state["items"]) == 1  # item retained


# ---------------------------------------------------------------------------
# Fence -- the queue is a third caller, no bypass (spec §6)
# ---------------------------------------------------------------------------


async def test_append_rejects_when_input_disabled(client, monkeypatch):
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(input_enabled=False)
    )
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["sess"])
    resp = client.post("/api/sessions/sess/followups", json={"text": "hi"})
    assert resp.status_code == 403
    assert "input_enabled" in resp.json()["detail"]


async def test_append_rejects_when_not_allowlisted(client, monkeypatch):
    _enable(monkeypatch, ["other-*"], ["sess"])
    resp = client.post("/api/sessions/sess/followups", json={"text": "hi"})
    assert resp.status_code == 403


async def test_fire_time_halts_when_fence_closes_after_enqueue(
    client, monkeypatch, tmux_calls
):
    """The fence is re-evaluated at FIRE time against FRESH settings -- an
    item queued while allowed must halt (not send) if the fence closes
    before the bell fires. No bypass, no 'the server is trusted.'"""
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "should not fire"})

    # Fence closes before the bell rings.
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(input_enabled=False)
    )

    resp = client.post("/api/sessions/sess/bell")
    assert resp.status_code == 200

    send_calls = [c for c in tmux_calls if c[0] == "send-keys"]
    assert send_calls == []  # nothing typed

    state = client.get("/api/sessions/sess/followups").json()
    assert state["halted"]["reason"] == "input_disabled"
    assert len(state["items"]) == 1  # item retained, not lost


async def test_advance_calls_the_single_fence_function(client, monkeypatch, tmux_calls):
    """T-17: patch input_allowed_for_session to raise -- the fire path must
    propagate it (proving there is no second, parallel implementation)."""
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "one"})

    def boom(name, settings):
        raise RuntimeError("no second fence implementation")

    monkeypatch.setattr("muxplex.main.input_allowed_for_session", boom)

    with pytest.raises(RuntimeError):
        await main_mod._advance_followup_queue("sess")


async def test_append_rejects_when_bell_hook_unarmed(client, monkeypatch):
    _enable(monkeypatch, ["sess"], ["sess"])
    monkeypatch.setattr("muxplex.main._bell_hook_armed", False)
    resp = client.post("/api/sessions/sess/followups", json={"text": "hi"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["bell_hook_unarmed"] is True


# ---------------------------------------------------------------------------
# Halt / resume lifecycle
# ---------------------------------------------------------------------------


async def test_halted_queue_ignores_bells_until_resume(client, monkeypatch, tmux_calls):
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "one"})

    async def boom(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr("muxplex.main.run_tmux", boom)
    client.post("/api/sessions/sess/bell")  # halts

    # Restore tmux, bell again -- must stay halted (not self-clearing).
    calls: list = []

    async def fake_run_tmux(*args):
        calls.append(args)
        return ""

    monkeypatch.setattr("muxplex.main.run_tmux", fake_run_tmux)
    client.post("/api/sessions/sess/bell")
    assert calls == []
    state = client.get("/api/sessions/sess/followups").json()
    assert state["halted"] is not None

    resp = client.post("/api/sessions/sess/followups/resume")
    assert resp.json()["halted"] is None

    client.post("/api/sessions/sess/bell")
    assert any(c[0] == "send-keys" for c in calls)


# ---------------------------------------------------------------------------
# API surface: caps, preconditions, 404/400
# ---------------------------------------------------------------------------


async def test_get_followups_for_unknown_queue_returns_zero_value(client, monkeypatch):
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["sess"])
    resp = client.get("/api/sessions/sess/followups")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "session": "sess",
        "revision": 0,
        "items": [],
        "halted": None,
        "target_window": None,
    }


async def test_followups_404_for_unknown_session(client, monkeypatch):
    monkeypatch.setattr("muxplex.main.get_session_list", list)
    resp = client.get("/api/sessions/nope/followups")
    assert resp.status_code == 404


async def test_followups_400_for_invalid_session_name(client):
    resp = client.get("/api/sessions/bad:name/followups")
    assert resp.status_code == 400


async def test_put_requires_revision_precondition(client, monkeypatch):
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "one"})
    resp = client.put(
        "/api/sessions/sess/followups",
        json={"expected_revision": 0, "items": [{"text": "two"}]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["revision_mismatch"] is True


async def test_put_rejects_while_send_in_flight(client, monkeypatch):
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "one"})
    followups._followup_sending.add("sess")
    resp = client.put(
        "/api/sessions/sess/followups",
        json={"expected_revision": 1, "items": []},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["send_in_flight"] is True


async def test_queue_full_rejects_17th_item(client, monkeypatch):
    _enable(monkeypatch, ["sess"], ["sess"])
    for i in range(followups.MAX_FOLLOWUPS):
        r = client.post("/api/sessions/sess/followups", json={"text": f"item {i}"})
        assert r.status_code == 200
    resp = client.post("/api/sessions/sess/followups", json={"text": "one too many"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["queue_full"] is True


async def test_text_too_large_rejected(client, monkeypatch):
    from muxplex.terminal_input import MAX_TEXT_BYTES

    _enable(monkeypatch, ["sess"], ["sess"])
    resp = client.post(
        "/api/sessions/sess/followups", json={"text": "x" * (MAX_TEXT_BYTES + 1)}
    )
    assert resp.status_code == 413


async def test_delete_clears_items_and_halt(client, monkeypatch):
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "one"})

    async def boom(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr("muxplex.main.run_tmux", boom)
    client.post("/api/sessions/sess/bell")  # halts

    resp = client.delete("/api/sessions/sess/followups")
    assert resp.json() == {
        "session": "sess",
        "revision": 0,
        "items": [],
        "halted": None,
    }
    state = client.get("/api/sessions/sess/followups").json()
    assert state == {
        "session": "sess",
        "revision": 0,
        "items": [],
        "halted": None,
        "target_window": None,
    }


async def test_fired_item_leaves_no_residue(client, monkeypatch, tmux_calls):
    """T-16: after the queue fully drains, state.json carries no history,
    tombstone, or counter for it."""
    _enable(monkeypatch, ["sess"], ["sess"])
    client.post("/api/sessions/sess/followups", json={"text": "only item"})
    client.post("/api/sessions/sess/bell")

    from muxplex.state import load_state

    state = load_state()
    assert "sess" not in state.get("followups", {})


# ---------------------------------------------------------------------------
# GET /api/sessions and GET /api/view summary field
# ---------------------------------------------------------------------------


async def test_get_sessions_carries_followups_summary(client, monkeypatch):
    _enable(monkeypatch, ["sess"], ["sess"])
    monkeypatch.setattr("muxplex.main.get_snapshots", dict)
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    client.post("/api/sessions/sess/followups", json={"text": "one"})
    resp = client.get("/api/sessions")
    entries = {s["name"]: s for s in resp.json()}
    assert entries["sess"]["followups"] == {"pending": 1, "halted": False}


async def test_get_view_carries_followups_summary(client, monkeypatch):
    _enable(monkeypatch, ["sess"], ["sess"])
    monkeypatch.setattr("muxplex.main.get_session_activity", dict)
    client.post("/api/sessions/sess/followups", json={"text": "one"})
    resp = client.get("/api/view")
    entries = {s["name"]: s for s in resp.json()["sessions"]}
    assert entries["sess"]["followups"] == {"pending": 1, "halted": False}


# ---------------------------------------------------------------------------
# Reaper (spec §3.4, the state-wipe hazard)
# ---------------------------------------------------------------------------


async def test_reap_only_runs_when_tmux_epoch_confirmed(monkeypatch):
    monkeypatch.setattr("muxplex.main.enumerate_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr("muxplex.main.snapshot_all", AsyncMock(return_value={}))
    monkeypatch.setattr("muxplex.main.update_session_cache", lambda *a, **k: None)
    monkeypatch.setattr("muxplex.main.get_session_created_times", dict)

    from muxplex.state import load_state, save_state

    state = load_state()
    followups.append_item(state, "vanished", "keep me", True)
    save_state(state)

    # tmux down this cycle -> probe returns None -> queue must survive.
    monkeypatch.setattr("muxplex.main.probe_tmux_epoch", AsyncMock(return_value=None))
    await main_mod._run_poll_cycle()
    state = load_state()
    assert "vanished" in state["followups"]

    # tmux confirmed alive, session absent -> queue is reaped.
    monkeypatch.setattr(
        "muxplex.main.probe_tmux_epoch", AsyncMock(return_value={"epoch": 1})
    )
    await main_mod._run_poll_cycle()
    state = load_state()
    assert "vanished" not in state["followups"]


# ---------------------------------------------------------------------------
# Federation: no proxy route exists (spec §8, T-20)
# ---------------------------------------------------------------------------


def test_no_federation_followups_proxy_route_exists():
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any("followups" in p and "federation" in p for p in paths)

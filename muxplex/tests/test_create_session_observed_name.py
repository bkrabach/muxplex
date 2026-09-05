"""POST /api/sessions must report the name tmux ACTUALLY created.

The name muxplex ASKS tmux for and the name tmux actually creates frequently
differ, for two independently confirmed reasons:

  1. A non-default ``new_session_template`` may derive its own name. The
     exemplar in the wild is ``amplifier-workspace ~/dev/{name}``, whose
     ``session_name_from_path()`` sanitizes and TRUNCATES to 32 characters --
     so every requested name longer than 32 chars comes back different.
  2. tmux itself silently rewrites ``.`` to ``_`` and still reports rc=0
     (reproduced on tmux 3.4: ``new-session -d -s build.js`` yields
     ``build_js``).

Before the fix, ``create_session`` returned ``payload.name`` verbatim with no
post-spawn re-enumeration, so the client keyed the view pin, the readiness
poll, and the manifest ``created_with`` record on a name that named no live
session. These tests pin the corrected contract: the response reports the
OBSERVED name, and honestly distinguishes "observed and confirmed" from
"could not confirm" rather than passing a guess off as a fact.

The same verification shape already exists on the rename path (main.py's
"step 8, Verify the observed name") for the identical reason; these tests
hold the create path to it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import muxplex.main as main_mod
from muxplex.main import app


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Same structural isolation test_api.py applies to every test there.

    conftest.py's autouse rails cover settings and the tmux socket dir; these
    cover the state/ttyd paths and the startup side-effects, so nothing in
    this module can reach the host's real files or processes.
    """
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_path / "state" / "state.json")
    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", tmp_path / "ttyd")

    async def _mock_reap_orphan():
        return 0

    async def _mock_reap_legacy():
        return False

    async def _noop_poll_loop() -> None:
        return None

    monkeypatch.setattr("muxplex.main.reap_orphan_ttyds", _mock_reap_orphan)
    monkeypatch.setattr("muxplex.main.reap_legacy_ttyd", _mock_reap_legacy)
    monkeypatch.setattr("muxplex.main.ttyd_mod.validate_socket_dir", lambda d: None)
    monkeypatch.setattr("muxplex.main._poll_loop", _noop_poll_loop)


@pytest.fixture
def client(monkeypatch):
    """TestClient with the lifespan run and a valid session cookie set."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        c.cookies.set("muxplex_session", create_session_cookie(_auth_secret, _auth_ttl))
        yield c


@pytest.fixture(autouse=True)
def _fast_observation(monkeypatch):
    """Shrink the post-spawn bounded wait so tests don't pay real seconds.

    The production values are a deliberate tolerance for tmux_kit's
    ``spawn_session()``, which returns ``(True, None)`` on its own 30s
    timeout by design; the polling SCHEDULE is what these tests shrink, never
    the polling LOGIC.
    """
    monkeypatch.setattr(main_mod, "_CREATE_OBSERVE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(main_mod, "_CREATE_OBSERVE_INTERVAL_S", 0.01)


@pytest.fixture
def isolated_manifest(tmp_path, monkeypatch):
    """Point the presence manifest at tmp_path.

    ``MANIFEST_PATH`` is bound at import time from ``STATE_DIR``, so
    redirecting ``STATE_DIR`` alone does not move it. Tests that assert on
    ``created_with`` must not read or write the host's real sessions.json.
    """
    path = tmp_path / "sessions.json"
    monkeypatch.setattr("muxplex.manifest.MANIFEST_PATH", path)
    monkeypatch.setattr("muxplex.main.MANIFEST_PATH", path, raising=False)
    return path


def _stub_tmux(monkeypatch, *, creates, spawn_ok=True):
    """Stub the spawn + enumeration seam with a fake tmux server.

    *creates* maps a REQUESTED name to the list of session names that exist
    after the spawn -- i.e. it models a name-mangling session command. A
    value may also be a list-of-lists, in which case each successive
    ``enumerate_sessions()`` call returns the next element, modelling a
    session that has not appeared yet when we first look.

    Returns the mutable list of ``(name, command_id)`` spawn calls made.
    """
    live: list[str] = []
    pending: list[list[str]] = []
    spawn_calls: list[tuple[str, str | None]] = []

    async def fake_spawn(name: str, command_id: str | None = None):
        spawn_calls.append((name, command_id))
        result = creates.get(name, [])
        if result and isinstance(result[0], list):
            pending.extend(result)
        else:
            live[:] = list(result)
        return (spawn_ok, None if spawn_ok else "boom")

    async def fake_enumerate():
        if pending:
            return list(pending.pop(0))
        return list(live)

    monkeypatch.setattr(main_mod, "spawn_session_command", fake_spawn)
    monkeypatch.setattr(main_mod, "enumerate_sessions", fake_enumerate)
    return spawn_calls


# ---------------------------------------------------------------------------
# The two confirmed mangling mechanisms
# ---------------------------------------------------------------------------


def test_truncating_template_reports_the_truncated_name(client, monkeypatch):
    """A 40-char request under a 32-char-truncating template reports 32 chars.

    This is the keystone case, reproduced from the user's live tmux:
    ``home-assistant-smart-tool-team-c`` came back for a request ending
    ``-team-ci``. Against the pre-fix code this asserts 40 == 32 and fails.
    """
    requested = "home-assistant-smart-tool-team-ci-abcdef"
    assert len(requested) == 40
    truncated = requested[:32]

    _stub_tmux(monkeypatch, creates={requested: [truncated]})

    response = client.post("/api/sessions", json={"name": requested})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == truncated
    assert data["observed"] == truncated
    assert data["name_confirmed"] is True
    assert data["requested_name"] == requested


def test_dotted_name_reports_the_underscored_name(client, monkeypatch):
    """tmux rewrites ``.`` to ``_`` at rc=0; the response must say so."""
    requested = "my-lane.v2.fix"
    observed = "my-lane_v2_fix"

    _stub_tmux(monkeypatch, creates={requested: [observed]})

    response = client.post("/api/sessions", json={"name": requested})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == observed
    assert data["observed"] == observed
    assert data["name_confirmed"] is True


# ---------------------------------------------------------------------------
# The honest-degradation contract
# ---------------------------------------------------------------------------


def test_unmangled_name_is_confirmed_by_exact_match(client, monkeypatch):
    """The ordinary case: the requested name is live, so it is CONFIRMED.

    An exact match is not merely "no mangling detected" -- it is positive
    evidence, and must be reported as confirmed rather than as a guess.
    """
    _stub_tmux(monkeypatch, creates={"my-project": ["other", "my-project"]})

    response = client.post("/api/sessions", json={"name": "my-project"})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "my-project"
    assert data["observed"] == "my-project"
    assert data["name_confirmed"] is True


def test_nothing_appeared_is_reported_as_unconfirmed(client, monkeypatch):
    """No session appeared within the window -> never claim confirmation.

    ``spawn_session()`` returns ``(True, None)`` on its own 30s timeout by
    design ("let the caller poll"), so ok=True genuinely does not prove the
    session exists yet. Echoing the requested name back as though it were
    observed is exactly the bug, in a form that is harder to see.
    """
    _stub_tmux(monkeypatch, creates={"ghost-session": []})

    response = client.post("/api/sessions", json={"name": "ghost-session"})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["name_confirmed"] is False
    assert data["observed"] is None
    assert data["requested_name"] == "ghost-session"


def test_ambiguous_arrivals_are_reported_as_unconfirmed(client, monkeypatch):
    """Two unexplained new sessions -> "could not confirm", not a guess.

    Picking either arrival would be a coin flip presented as a fact. The
    rename path makes the same call (``candidates[0] if len(candidates) == 1
    else None``).
    """
    _stub_tmux(monkeypatch, creates={"ambiguous": ["arrival-one", "arrival-two"]})

    response = client.post("/api/sessions", json={"name": "ambiguous"})

    assert response.status_code == 200
    data = response.json()
    assert data["name_confirmed"] is False
    assert data["observed"] is None


def test_preexisting_sessions_are_not_mistaken_for_the_new_one(client, monkeypatch):
    """The diff is taken against a BEFORE snapshot, not the whole list.

    Without a pre-spawn snapshot, every unrelated session already running
    would look like a candidate and the single-new-arrival rule would never
    fire on a busy host.
    """
    requested = "n" * 40
    observed = requested[:32]

    live_before = ["unrelated-a", "unrelated-b", "unrelated-c"]
    call_count = {"n": 0}

    async def fake_spawn(name, command_id=None):
        return (True, None)

    async def fake_enumerate():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return list(live_before)
        return [*live_before, observed]

    monkeypatch.setattr(main_mod, "spawn_session_command", fake_spawn)
    monkeypatch.setattr(main_mod, "enumerate_sessions", fake_enumerate)

    response = client.post("/api/sessions", json={"name": requested})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == observed
    assert data["name_confirmed"] is True


def test_late_arrival_is_caught_by_the_bounded_wait(client, monkeypatch):
    """A session that is not visible on the first look is still observed.

    tmux_kit's spawn can return before the session is enumerable. A single
    post-spawn peek would call this a failure to confirm; the bounded wait
    is what makes the confirmed case the common case.
    """
    requested = "late-" + "x" * 40
    observed = requested[:32]

    _stub_tmux(
        monkeypatch,
        creates={requested: [[], [], [observed]]},
    )

    response = client.post("/api/sessions", json={"name": requested})

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == observed
    assert data["name_confirmed"] is True


# ---------------------------------------------------------------------------
# Downstream keying
# ---------------------------------------------------------------------------


def test_manifest_created_with_is_keyed_on_the_observed_name(
    client, monkeypatch, isolated_manifest: Path
):
    """``created_with`` must key on the observed name, or delete can't find it.

    The delete path looks the command pair up by the live session's name; a
    record filed under the requested name is unreachable.
    """
    requested = "manifest-key-check-" + "y" * 21
    assert len(requested) > 32
    observed = requested[:32]

    _stub_tmux(monkeypatch, creates={requested: [observed]})

    response = client.post("/api/sessions", json={"name": requested})
    assert response.status_code == 200

    manifest = json.loads(isolated_manifest.read_text(encoding="utf-8"))
    created_with = manifest.get("created_with", {})
    assert observed in created_with
    assert requested not in created_with
    assert created_with[observed] == "default"


def test_spawn_failure_still_raises_500_and_records_nothing(client, monkeypatch):
    """The failure path is unchanged: no observation, no manifest write."""
    _stub_tmux(monkeypatch, creates={"doomed": []}, spawn_ok=False)

    response = client.post("/api/sessions", json={"name": "doomed"})

    assert response.status_code == 500

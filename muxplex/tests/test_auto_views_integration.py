"""
Integration tests for auto-updating (rule-based) views -- the self-healing
proof (AUTO_VIEWS_SPEC.md §11.4).

Real tmux, an isolated named socket (`-L auto-views-test`), driven through
the real ASGI app via `TestClient`. This is the actual evidence the feature
works: a rule-based view's membership tracks live tmux state across
create/kill cycles WITHOUT any write to settings.json, and a killed
rule-matched session never enters the pruning ledger -- the mechanical
reason it cannot decay, in contrast to a pinned session in the SAME
situation (the contrast arm), which does.

Run with:
    pytest -m integration -v
"""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import muxplex.pruning as pruning_mod
from muxplex.identity import load_device_id
from muxplex.main import _run_poll_cycle, app
from muxplex.settings import SETTINGS_PATH, load_settings, save_settings

_SOCKET = "auto-views-test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def redirect_pruning_state_path(tmp_path, monkeypatch):
    """Redirect PRUNING_STATE_PATH to a temporary file for all tests in
    this module.

    MUST be here, verbatim in spirit (copied from test_pruning.py's
    identical fixture): conftest.py's autouse rails redirect SETTINGS_PATH
    but NOT this path. Without it, step 5's assertions would read (and the
    poll cycle would write) the real ~/.config/muxplex/pruning.json --
    which, per AGENTS.md's recovery section, is the ONLY record of lost
    session names after a real incident. Clobbering it here would be
    exactly the "a test that destroys its host still passes" failure
    conftest.py exists to stop.
    """
    fake_path = tmp_path / "pruning.json"
    monkeypatch.setattr(pruning_mod, "PRUNING_STATE_PATH", fake_path)
    return fake_path


@pytest.fixture
def tmux_socket():
    """An isolated tmux server on a unique named socket, with no sessions
    pre-created (tests create their own). Torn down via the exact socket
    name -- never a bare `kill-server`, never a name-matched kill
    (AGENTS.md "Two ways to destroy every live tmux session")."""
    subprocess.run(
        ["tmux", "-L", _SOCKET, "start-server"],
        check=True,
        capture_output=True,
    )
    yield _SOCKET
    subprocess.run(
        ["tmux", "-L", _SOCKET, "kill-server"],
        capture_output=True,
        check=False,
    )


@pytest.fixture(autouse=True)
def use_tmp_state(tmp_path, monkeypatch):
    """Redirect state.json to tmp_path for test isolation (mirrors
    test_integration.py's use_tmp_state, without ttyd -- this module never
    spawns one).

    Also redirects `manifest.MANIFEST_PATH` -- it is computed ONCE at
    import time as `STATE_DIR / "sessions.json"`, so patching
    `state.STATE_DIR` alone does NOT move it (module-level values are
    bound at import, not re-read live). Without this, `load_manifest()`
    in `_run_poll_cycle` reads whatever real manifest exists in the DTU
    container; if it happens to carry `pending_restore` from an unrelated
    prior run, `_local_evaluable` goes False and NO local-owned key (rule
    OR pin) ever accrues `first_missed_at` -- silently making step 5's
    "never pruned" assertion vacuously true and breaking the step 6
    contrast arm that is supposed to prove it isn't.
    """
    tmp_state_dir = tmp_path / "state"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_dir / "state.json")
    monkeypatch.setattr(
        "muxplex.manifest.MANIFEST_PATH", tmp_state_dir / "sessions.json"
    )


@pytest.fixture(autouse=True)
def patch_startup(tmp_path, monkeypatch):
    """Neutralize the real app lifespan's startup side effects (mirrors
    test_api.py's patch_startup_and_state) -- WITHOUT this, `TestClient(app)`
    in api_client below triggers a real `validate_socket_dir()` against the
    default ttyd socket dir, real `reap_orphan_ttyds()`/`reap_legacy_ttyd()`
    subprocess probes, and a REAL background `_poll_loop()` racing this
    module's explicit `_run_poll_cycle()` calls against the isolated tmux
    socket -- any of which can hang or corrupt state under pytest.
    """
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


def make_run_tmux_for_socket(socket: str):
    """Return an async run_tmux substitute that routes all tmux calls
    through *socket*. Identical in shape to test_integration.py's helper of
    the same name (kept local rather than imported -- test modules are not
    a shared library surface)."""

    async def patched_run_tmux(*args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-L",
            socket,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr_bytes.decode("utf-8", errors="replace"))
        return stdout_bytes.decode("utf-8", errors="replace")

    return patched_run_tmux


def tmux(socket: str, *args: str) -> str:
    result = subprocess.run(
        ["tmux", "-L", socket, *args], capture_output=True, text=True, check=False
    )
    return result.stdout


@pytest.fixture
def api_client(monkeypatch):
    """A TestClient authenticated the same way test_api.py's `client`
    fixture is, without triggering the real app lifespan (no ttyd reapers,
    no real poll loop) -- this module drives poll cycles explicitly via
    _run_poll_cycle()."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


async def _poll(socket: str) -> None:
    """Run one real poll cycle against the isolated tmux socket."""
    from unittest.mock import patch

    patched_run_tmux = make_run_tmux_for_socket(socket)
    with (
        patch("muxplex.sessions.run_tmux", side_effect=patched_run_tmux),
        patch("muxplex.bells.run_tmux", side_effect=patched_run_tmux),
    ):
        await _run_poll_cycle()


def _view_names(client: TestClient, active_view: str) -> list[str]:
    resp = client.get("/api/view")
    assert resp.status_code == 200
    body = resp.json()
    assert body["view"] == active_view
    return [s["name"] for s in body["sessions"]]


def _sessions_by_name(client: TestClient) -> dict:
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    return {s["name"]: s for s in resp.json()}


# ---------------------------------------------------------------------------
# §11.4 -- the self-healing proof
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_auto_view_self_heals_across_create_and_kill_with_contrast_and_union_arms(
    tmux_socket, api_client, redirect_pruning_state_path
):
    device_id = load_device_id()

    # --- Step 1: create av-alpha and unrelated-one on the isolated socket ---
    # `pinned-only` is a THIRD session, deliberately named so it does NOT
    # match the "av-*" rule -- it exists solely to give the contrast arm
    # (step 6) a session that is PINNED but never rule-matched, so the two
    # mechanisms (rule vs. pin) can be told apart in the pruning ledger.
    # Sharing one session between both arms would make step 5's "never
    # pruned" assertion and step 6's "pruned" assertion indistinguishable
    # (both would key off the same "...:av-alpha" pruning entry).
    tmux(tmux_socket, "new-session", "-d", "-s", "av-alpha", "-x", "80", "-y", "24")
    tmux(
        tmux_socket, "new-session", "-d", "-s", "unrelated-one", "-x", "80", "-y", "24"
    )
    tmux(tmux_socket, "new-session", "-d", "-s", "pinned-only", "-x", "80", "-y", "24")

    # --- Step 2: write settings -- one rule-only view, active_view=Auto,
    #     plus a second, PIN-only view for the contrast arm.
    #     Record views_updated_at and the exact on-disk `views` JSON. ---
    save_settings(
        {
            "views": [
                {"name": "Auto", "sessions": [], "match_names": ["av-*"]},
                {"name": "Pinned", "sessions": [f"{device_id}:pinned-only"]},
            ],
        }
    )
    from muxplex.state import save_state

    save_state(
        {
            "active_view": "Auto",
            "active_session": None,
            "active_remote_id": None,
            "session_order": [],
            "sessions": {},
            "devices": {},
        }
    )

    views_updated_at_before = load_settings()["views_updated_at"]
    on_disk_views_before = SETTINGS_PATH.read_text()

    # --- Step 3: run one poll cycle. GET /api/view -> ["av-alpha"].
    #     GET /api/sessions -> av-alpha.views == ["Auto"],
    #     pinned-only.views == ["Pinned"], unrelated-one.views == []. ---
    asyncio.run(_poll(tmux_socket))

    assert _view_names(api_client, "Auto") == ["av-alpha"]
    sessions = _sessions_by_name(api_client)
    assert sessions["av-alpha"]["views"] == ["Auto"]
    assert sessions["pinned-only"]["views"] == ["Pinned"]
    assert sessions["unrelated-one"]["views"] == []

    # --- Step 4: create av-beta; poll. GET /api/view -> ["av-alpha", "av-beta"].
    #     Assert views_updated_at UNCHANGED and on-disk `views` JSON
    #     BYTE-IDENTICAL -- the view healed without a settings write. ---
    tmux(tmux_socket, "new-session", "-d", "-s", "av-beta", "-x", "80", "-y", "24")
    asyncio.run(_poll(tmux_socket))

    assert _view_names(api_client, "Auto") == ["av-alpha", "av-beta"]
    assert load_settings()["views_updated_at"] == views_updated_at_before
    assert SETTINGS_PATH.read_text() == on_disk_views_before

    # --- Step 5: kill av-alpha (rule-matched) AND pinned-only (pinned);
    #     poll TWICE (clear the ~2s session-list cache). GET /api/view ->
    #     ["av-beta"]. Assert views_updated_at unchanged, on-disk `views`
    #     byte-identical, and pruning.json's first_missed_at has NO entry
    #     for av-alpha -- a rule-matched session never enters the pruning
    #     ledger (the mechanical reason it cannot decay). ---
    tmux(tmux_socket, "kill-session", "-t", "av-alpha")
    tmux(tmux_socket, "kill-session", "-t", "pinned-only")
    asyncio.run(_poll(tmux_socket))
    asyncio.run(_poll(tmux_socket))

    assert _view_names(api_client, "Auto") == ["av-beta"]
    assert load_settings()["views_updated_at"] == views_updated_at_before
    assert SETTINGS_PATH.read_text() == on_disk_views_before

    pruning_state = json.loads(redirect_pruning_state_path.read_text())
    first_missed = pruning_state.get("first_missed_at", {})
    assert not any("av-alpha" in key for key in first_missed), (
        f"a rule-matched session must never accrue pruning bookkeeping; "
        f"found: {first_missed}"
    )

    # --- Step 6: CONTRAST ARM -- the PINNED "Pinned" view's pinned-only
    #     entry DOES get a first_missed_at entry after the same kill (step 5
    #     ran above; this asserts against that same pruning_state). This
    #     proves the two mechanisms are genuinely different, and that the
    #     "no pruning entry" assertion above is not vacuous (pruning
    #     bookkeeping is demonstrably live and working in this exact test). ---
    pinned_key = f"{device_id}:pinned-only"
    assert pinned_key in first_missed, (
        "a PINNED (not rule-matched) session's key must start the pruning "
        "clock once genuinely missing -- if this fails, the contrast arm "
        "is broken and the rule-based 'never pruned' assertion is vacuous"
    )

    # --- Step 7: UNION ARM -- pin unrelated-one into Auto (alongside the
    #     rule); poll; assert Auto = ["av-beta", "unrelated-one"] with no
    #     duplicates, and unrelated-one.views == ["Auto"]. ---
    current = load_settings()
    for v in current["views"]:
        if v["name"] == "Auto":
            v["sessions"] = [f"{device_id}:unrelated-one"]
    save_settings(current)

    asyncio.run(_poll(tmux_socket))

    names = _view_names(api_client, "Auto")
    assert sorted(names) == ["av-beta", "unrelated-one"]
    assert len(names) == len(set(names))

    sessions_after_union = _sessions_by_name(api_client)
    assert sessions_after_union["unrelated-one"]["views"] == ["Auto"]

"""
Integration tests for session rename (docs/plans/2026-08-07-session-rename-plan.md)
against a REAL, isolated tmux server -- no mocking of tmux itself.

Proves the claims a mocked test cannot: that '.' is genuinely mangled by
THIS host's tmux, that `rename-session -t =<old> -- <new>` genuinely
targets exact-match, that a live security fence genuinely refuses/allows a
rename in both directions end-to-end, and that the old ttyd is genuinely
killed after a real rename.

Isolation: an isolated `TMUX_TMPDIR`-equivalent socket directory per test
via `settings.tmux_socket_dir` (the SAME mechanism production muxplex uses
-- see sessions.tmux_env()), mirroring test_restore_integration.py's
`isolated` fixture. `short_socket_dir` (conftest.py) is used for the ttyd
socket directory specifically, since real AF_UNIX sockets are bound here.

Run with:
    pytest -m integration -v
Default test run (unit tests only, per AGENTS.md):
    pytest -v
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import muxplex.main as main_mod
from muxplex.main import app
from muxplex.manifest import get_rename_in_flight, load_manifest
from muxplex.sessions import is_tmux_stable_name, rename_tmux_session
from muxplex.settings import load_settings, save_settings

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers (mirrors test_restore_integration.py's shape)
# ---------------------------------------------------------------------------


def _tmux_env(socket_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["TMUX_TMPDIR"] = str(socket_dir)
    env.pop("TMUX", None)
    return env


def _tmux(
    socket_dir: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=check,
        env=_tmux_env(socket_dir),
    )


def _live_names(socket_dir: Path) -> list[str]:
    result = _tmux(socket_dir, "list-sessions", "-F", "#{session_name}", check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture
def socket_dir(tmp_path):
    """A real, isolated tmux socket directory -- torn down (this socket
    ONLY) at the end of the test, never the ambient server (AGENTS.md's
    'NEVER broad-kill by process name')."""
    d = tmp_path / "tmux-socket"
    d.mkdir()
    yield d
    _tmux(d, "kill-server", check=False)


# ---------------------------------------------------------------------------
# sessions.rename_tmux_session() / is_tmux_stable_name() -- real tmux
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dot_is_genuinely_mangled_by_this_hosts_tmux(socket_dir, monkeypatch):
    """The empirical claim \u00a71 of the plan rests on: this host's tmux
    silently converts '.' to '_'. If this ever stops being true, THIS test
    fails for the reason (tmux's behavior changed), not the policy test
    (is_tmux_stable_name rejecting '.') silently passing for a new one.
    """
    monkeypatch.setattr("muxplex.sessions.tmux_env", lambda: _tmux_env(socket_dir))
    _tmux(socket_dir, "new-session", "-d", "-s", "build.js")
    try:
        live = _live_names(socket_dir)
        assert live == ["build_js"], (
            "tmux's '.' -> '_' mangling behavior changed on this host/version "
            f"-- observed {live!r}. If tmux no longer mangles this, "
            "is_tmux_stable_name()'s rejection may be over-cautious."
        )
    finally:
        _tmux(socket_dir, "kill-server", check=False)


@pytest.mark.asyncio
async def test_rename_tmux_session_exact_match_targeting(socket_dir, monkeypatch):
    """`-t =<old> -- <new>` targets exactly the named session, not a
    prefix match against a similarly-named neighbour (\u00a71's `app`/`app2`
    finding)."""
    monkeypatch.setattr("muxplex.sessions.tmux_env", lambda: _tmux_env(socket_dir))
    _tmux(socket_dir, "new-session", "-d", "-s", "app")
    _tmux(socket_dir, "new-session", "-d", "-s", "app2")

    await rename_tmux_session("app", "app-renamed")

    live = set(_live_names(socket_dir))
    assert live == {"app-renamed", "app2"}


@pytest.mark.asyncio
async def test_rename_tmux_session_raises_on_duplicate(socket_dir, monkeypatch):
    monkeypatch.setattr("muxplex.sessions.tmux_env", lambda: _tmux_env(socket_dir))
    _tmux(socket_dir, "new-session", "-d", "-s", "one")
    _tmux(socket_dir, "new-session", "-d", "-s", "two")

    with pytest.raises(RuntimeError, match="duplicate session"):
        await rename_tmux_session("one", "two")

    # Nothing changed -- tmux's own rc=1 guarantee.
    assert set(_live_names(socket_dir)) == {"one", "two"}


def test_is_tmux_stable_name_matches_real_tmux_behavior(socket_dir):
    """Companion to the mangling test above: is_tmux_stable_name's
    rejection rule (charset allows '.', tmux mangles it) is directly
    checked against what this host's tmux actually does for a sweep of
    the allowlist's own charset."""
    _tmux(socket_dir, "new-session", "-d", "-s", "a.b", check=False)
    live = _live_names(socket_dir)
    assert live == ["a_b"]
    assert is_tmux_stable_name("a.b") is False
    assert is_tmux_stable_name("a_b") is True
    assert is_tmux_stable_name("a-b") is True


# ---------------------------------------------------------------------------
# Full endpoint round-trip against real tmux + real ttyd (short_socket_dir)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, short_socket_dir, monkeypatch):
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_path / "state" / "state.json")
    # validate_socket_dir() -- mocked to a no-op below -- is normally what
    # creates this directory at real startup. With it neutralized, a real
    # ttyd spawn's `-i <dir>/mx-<hash>.sock` bind fails silently (missing
    # parent directory), and ttyd falls back to an unauthenticated TCP
    # listener instead of raising -- spawn_ttyd()'s readiness poll then
    # times out never having seen the socket file, surfacing as a bare 500
    # from `/connect` with no indication the real cause was a missing
    # directory. Create it explicitly here, the same way
    # test_integration.py's `real_ttyd_app` fixture already does.
    ttyd_socket_dir = short_socket_dir / "ttyd"
    ttyd_socket_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", ttyd_socket_dir)
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("muxplex.identity.IDENTITY_PATH", tmp_path / "identity.json")
    monkeypatch.setattr("muxplex.pruning.PRUNING_STATE_PATH", tmp_path / "pruning.json")
    monkeypatch.setattr("muxplex.manifest.MANIFEST_PATH", tmp_path / "sessions.json")

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
def client(monkeypatch, socket_dir):
    """TestClient wired to the real, isolated tmux socket via
    settings.tmux_socket_dir -- the production mechanism (sessions.tmux_env()),
    not an internal monkeypatch of muxplex's own rename/enumerate calls."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    settings = load_settings()
    settings["tmux_socket_dir"] = str(socket_dir)
    save_settings(settings)
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


@pytest.mark.asyncio
async def test_rename_endpoint_real_tmux_full_roundtrip_kills_old_ttyd(
    client, socket_dir
):
    """Full proof against real tmux + a real per-session ttyd: the rename
    succeeds, tmux reflects the new name, the response carries per-keyspace
    evidence, and the OLD name's ttyd is genuinely killed (\u00a72.4) -- proven
    by asserting its socket is no longer live, not by mocking kill_ttyd."""
    _tmux(socket_dir, "new-session", "-d", "-s", "agent-worker-1")

    # Prime muxplex's own session cache (get_session_list()) the same way
    # the poll loop would -- calling the real enumerate path once.
    await main_mod._run_poll_cycle()
    assert "agent-worker-1" in main_mod.get_session_list()

    # Spawn a real per-session ttyd for the OLD name via the real endpoint.
    connect_resp = client.post("/api/sessions/agent-worker-1/connect")
    assert connect_resp.status_code == 200
    from muxplex.ttyd import socket_is_live, socket_path_for

    old_sock = socket_path_for("agent-worker-1")
    assert socket_is_live(old_sock) is True

    resp = client.post(
        "/api/sessions/agent-worker-1/rename",
        json={"new_name": "agent-auth-refactor"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["from"] == "agent-worker-1"
    assert data["name"] == "agent-auth-refactor"
    assert data["migrated"]["bell"] is True

    assert set(_live_names(socket_dir)) == {"agent-auth-refactor"}
    assert socket_is_live(old_sock) is False  # \u00a72.4: old ttyd genuinely killed

    manifest = load_manifest()
    assert get_rename_in_flight(manifest) is None
    assert manifest["sessions"]["agent-auth-refactor"]["renamed_from"] == (
        "agent-worker-1"
    )


@pytest.mark.asyncio
async def test_rename_endpoint_security_fence_real_tmux_both_directions(
    client, socket_dir, monkeypatch
):
    """Real-tmux proof of \u00a710's security fence, both directions, against
    the actual endpoint (not a mocked classifier): a bearer_only caller
    cannot rename a session OUT of its allowlisted family, and cannot
    rename a DIFFERENT session INTO it either."""
    _tmux(socket_dir, "new-session", "-d", "-s", "agent-worker-1")
    _tmux(socket_dir, "new-session", "-d", "-s", "production-db")
    await main_mod._run_poll_cycle()

    settings = load_settings()
    settings["input_enabled"] = True
    settings["input_allowed_sessions"] = ["agent-*"]
    save_settings(settings)

    # Force bearer_only classification for this client's requests, exactly
    # as a real Bearer-authenticated caller with no session cookie would be
    # classified (_bearer_only_caller mirrors _ws_auth_check's WSAuth).
    monkeypatch.setattr(main_mod, "_bearer_only_caller", lambda request: True)

    # Direction 1: renaming OUT of the allowlist to acquire authority over a
    # session the fence never granted (\u00a710.1's motivating attack).
    denied_out = client.post(
        "/api/sessions/production-db/rename", json={"new_name": "agent-pwn"}
    )
    assert denied_out.status_code == 403
    assert denied_out.json()["detail"]["rename_not_allowed"] is True
    assert set(_live_names(socket_dir)) == {"agent-worker-1", "production-db"}

    # Direction 2: renaming an ALREADY-allowlisted session to a name outside
    # the family is conservatively denied too (new name must also match).
    denied_in = client.post(
        "/api/sessions/agent-worker-1/rename", json={"new_name": "scratch-x"}
    )
    assert denied_in.status_code == 403
    assert denied_in.json()["detail"]["rename_not_allowed"] is True
    assert set(_live_names(socket_dir)) == {"agent-worker-1", "production-db"}

    # The motivating use case still works: both names inside the family.
    allowed = client.post(
        "/api/sessions/agent-worker-1/rename",
        json={"new_name": "agent-auth-refactor"},
    )
    assert allowed.status_code == 200
    assert set(_live_names(socket_dir)) == {"agent-auth-refactor", "production-db"}

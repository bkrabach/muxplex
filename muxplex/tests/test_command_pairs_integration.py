"""
Real-tmux end-to-end proof for named session command pairs
(docs/plans/2026-08-02-named-session-command-pairs-plan.md §13.8).

Unit tests with mocked subprocesses (test_sessions.py, test_api.py) prove the
WIRING. They cannot prove that pair B's teardown ran and pair A's did not,
against a real tmux server -- only a real subprocess run against a real
tmux socket can prove that. This module does.

Safety rails (AGENTS.md "NEVER broad-kill by process name"):
- An isolated tmux_socket_dir (TMUX_TMPDIR) is used per test, via
  settings.tmux_socket_dir -- never the host's default socket dir.
- Cleanup is `tmux -S <that socket> kill-server`, socket-scoped, ignoring
  "no server running" -- never a bare `tmux kill-server`.
- No `pkill`/`killall` anywhere in this file.
- All markers live under tmp_path.
- Uses in-process TestClient(app) -- no separate uvicorn process exists to
  mis-kill.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import muxplex.manifest as manifest_mod
import muxplex.settings as settings_mod
from muxplex.main import app
from muxplex.sessions import enumerate_sessions, snapshot_all, update_session_cache

pytestmark = pytest.mark.integration


def _tmux_env(tmux_socket_dir: Path) -> dict:
    import os

    env = dict(os.environ)
    env["TMUX_TMPDIR"] = str(tmux_socket_dir)
    env.pop("TMUX", None)
    return env


def _tmux(tmux_socket_dir: Path, *args: str, check: bool = True):
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        env=_tmux_env(tmux_socket_dir),
        check=check,
    )


def _tmux_list_sessions(tmux_socket_dir: Path) -> list[str]:
    import os

    env = dict(os.environ)
    env["TMUX_TMPDIR"] = str(tmux_socket_dir)
    env.pop("TMUX", None)
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _template(marker_dir: Path, phase: str, pair_id: str) -> str:
    """A create OR delete template that touches a distinguishable marker
    file before running the real tmux command -- the crux of proving WHICH
    pair ran, not merely that A pair ran."""
    marker = str(marker_dir) + f"/{phase}-{pair_id}-{{name}}"
    if phase == "created":
        tmux_cmd = "tmux new-session -d -s {name}"
    else:
        tmux_cmd = "tmux kill-session -t {name}"
    return f"sh -c 'touch {marker}; {tmux_cmd}'"


@pytest.fixture
def pairs_env(tmp_path, monkeypatch):
    """Real tmux, isolated socket dir, isolated manifest, three configured
    command pairs (default/alpha/beta) each with distinguishable create+kill
    markers. Yields (marker_dir, tmux_socket_dir)."""
    tmux_socket_dir = tmp_path / "tmux-socket"
    tmux_socket_dir.mkdir()
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()

    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", tmp_path / "sessions.json")

    settings_mod.save_settings(
        {
            "tmux_socket_dir": str(tmux_socket_dir),
            "new_session_template": _template(marker_dir, "created", "default"),
            "delete_session_template": _template(marker_dir, "deleted", "default"),
            "session_commands": [
                {
                    "id": "alpha",
                    "label": "Alpha",
                    "new_session_template": _template(marker_dir, "created", "alpha"),
                    "delete_session_template": _template(
                        marker_dir, "deleted", "alpha"
                    ),
                },
                {
                    "id": "beta",
                    "label": "Beta",
                    "new_session_template": _template(marker_dir, "created", "beta"),
                    "delete_session_template": _template(marker_dir, "deleted", "beta"),
                },
            ],
        }
    )

    yield marker_dir, tmux_socket_dir

    # Teardown: socket-scoped kill-server, ignoring "no server running".
    _tmux(tmux_socket_dir, "kill-server", check=False)


@pytest.fixture
def client(monkeypatch):
    """In-process TestClient -- no separate uvicorn process exists to
    mis-kill. Mocks only startup side-effects unrelated to tmux (ttyd,
    poll-loop scheduling); the poll loop itself is replaced with a no-op so
    the test controls exactly when the session cache is refreshed."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    async def _mock_reap_orphan():
        return 0

    monkeypatch.setattr("muxplex.main.reap_orphan_ttyds", _mock_reap_orphan)

    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)

    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


def _refresh_session_cache():
    """Seed the module-level session-list cache from REAL tmux -- the
    fail-closed `name in get_session_list()` check at main.py's delete
    handler otherwise 404s on the ~2s read-model lag (API_SEMANTICS.md,
    'eventually consistent'). Spec §13.8 step 5: required, not optional."""
    names = asyncio.run(enumerate_sessions())
    snapshots = asyncio.run(snapshot_all(names))
    update_session_cache(names, snapshots)


def _markers(marker_dir: Path) -> set[str]:
    return {p.name for p in marker_dir.iterdir()}


# ---------------------------------------------------------------------------
# Scenario 1: pair matching across create and delete (the headline proof)
# ---------------------------------------------------------------------------


def test_pair_matching_create_and_delete_real_tmux(client, pairs_env):
    marker_dir, tmux_socket_dir = pairs_env

    # 1. Create with command_id=beta.
    resp = client.post("/api/sessions", json={"name": "e2e-beta", "command_id": "beta"})
    assert resp.status_code == 200
    assert resp.json()["command_id"] == "beta"

    # 2. Filesystem: created-beta-e2e-beta exists; alpha/default do not.
    markers = _markers(marker_dir)
    assert "created-beta-e2e-beta" in markers
    assert not any(m.startswith("created-alpha-") for m in markers)
    assert not any(m.startswith("created-default-") for m in markers)

    # 3. tmux: list-sessions contains e2e-beta.
    assert "e2e-beta" in _tmux_list_sessions(tmux_socket_dir)

    # 4. Manifest: created_with["e2e-beta"] == "beta".
    manifest = manifest_mod.load_manifest()
    assert manifest["created_with"]["e2e-beta"] == "beta"

    # 5. Seed the session cache (required -- see _refresh_session_cache doc).
    _refresh_session_cache()

    # 6. Delete.
    resp = client.delete("/api/sessions/e2e-beta")
    assert resp.status_code == 200
    assert resp.json()["command_id"] == "beta"

    # 7. THE headline assertion: deleted-beta-* exists; alpha/default do not.
    #    A wrong-pair implementation passes every other check and fails here.
    markers = _markers(marker_dir)
    assert "deleted-beta-e2e-beta" in markers
    assert not any(m.startswith("deleted-alpha-") for m in markers)
    assert not any(m.startswith("deleted-default-") for m in markers)

    # 8. tmux: session gone.
    assert "e2e-beta" not in _tmux_list_sessions(tmux_socket_dir)


# ---------------------------------------------------------------------------
# Scenario 2: the default pair is untouched (byte-identity, live)
# ---------------------------------------------------------------------------


def test_default_pair_byte_identical_real_tmux(client, pairs_env):
    marker_dir, _tmux_socket_dir = pairs_env

    resp = client.post("/api/sessions", json={"name": "e2e-plain"})
    assert resp.status_code == 200
    assert resp.json()["command_id"] == "default"

    _refresh_session_cache()
    resp = client.delete("/api/sessions/e2e-plain")
    assert resp.status_code == 200
    assert resp.json()["command_id"] == "default"

    markers = _markers(marker_dir)
    assert "created-default-e2e-plain" in markers
    assert "deleted-default-e2e-plain" in markers
    assert not any(m.startswith(("created-alpha-", "created-beta-")) for m in markers)
    assert not any(
        m.startswith("deleted-alpha-") or m.startswith("deleted-beta-") for m in markers
    )


# ---------------------------------------------------------------------------
# Scenario 3: a session muxplex did not create
# ---------------------------------------------------------------------------


def test_outside_created_session_uses_default_real_tmux(client, pairs_env):
    marker_dir, tmux_socket_dir = pairs_env

    _tmux(tmux_socket_dir, "new-session", "-d", "-s", "e2e-outside")
    _refresh_session_cache()

    resp = client.delete("/api/sessions/e2e-outside")
    assert resp.status_code == 200
    assert resp.json()["command_id"] == "default"
    assert "deleted-default-e2e-outside" in _markers(marker_dir)
    assert "e2e-outside" not in _tmux_list_sessions(tmux_socket_dir)


# ---------------------------------------------------------------------------
# Scenario 4: the recorded pair disappears -- both branches
# ---------------------------------------------------------------------------


def test_recorded_pair_removed_refuses_then_force_real_tmux(client, pairs_env):
    marker_dir, tmux_socket_dir = pairs_env

    resp = client.post(
        "/api/sessions", json={"name": "e2e-orphan", "command_id": "alpha"}
    )
    assert resp.status_code == 200
    _refresh_session_cache()

    # Remove the "alpha" entry from settings.json.
    settings_mod.save_settings(
        {
            "tmux_socket_dir": str(tmux_socket_dir),
            "new_session_template": _template(marker_dir, "created", "default"),
            "delete_session_template": _template(marker_dir, "deleted", "default"),
            "session_commands": [
                {
                    "id": "beta",
                    "label": "Beta",
                    "new_session_template": _template(marker_dir, "created", "beta"),
                    "delete_session_template": _template(marker_dir, "deleted", "beta"),
                },
            ],
        }
    )

    # 409, no command run: session still alive, no deleted-* marker at all.
    resp = client.delete("/api/sessions/e2e-orphan")
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["unknown_command_id"] is True
    assert "e2e-orphan" in _tmux_list_sessions(tmux_socket_dir)
    assert not any(m.startswith("deleted-") for m in _markers(marker_dir))

    # force=true: substitutes default, session gone.
    resp = client.delete("/api/sessions/e2e-orphan?force=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["forced"] is True
    assert body["command_id"] == "default"
    assert "deleted-default-e2e-orphan" in _markers(marker_dir)
    assert "e2e-orphan" not in _tmux_list_sessions(tmux_socket_dir)

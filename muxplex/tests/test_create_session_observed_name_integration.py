"""Real-tmux proof that POST /api/sessions reports the OBSERVED name.

test_create_session_observed_name.py stubs the spawn/enumerate seam and so
proves the WIRING. It cannot prove the PREMISE -- that the name muxplex asks
for and the name tmux creates actually diverge, at rc=0, with no error
anywhere. Only a real subprocess against a real tmux socket can prove that.
This module does, for both confirmed mechanisms:

  1. tmux silently rewriting ``.`` to ``_`` (tmux's own behavior, no template
     involvement at all).
  2. A ``new_session_template`` that derives its own, shorter name. The
     exemplar in the wild is ``amplifier-workspace ~/dev/{name}``, whose
     ``session_name_from_path()`` truncates to 32 characters; the template
     here reproduces that shape with ``cut -c1-32`` so the test needs no
     third-party binary installed.

Safety rails (AGENTS.md "NEVER broad-kill by process name"), matching
test_command_pairs_integration.py:
- An isolated ``tmux_socket_dir`` (TMUX_TMPDIR) per test, via
  settings.tmux_socket_dir -- never the host's default socket dir.
- Cleanup is `tmux -S <that socket> kill-server`, socket-scoped, ignoring
  "no server running" -- never a bare `tmux kill-server`.
- No `pkill`/`killall` anywhere in this file.
- In-process TestClient(app) -- no separate uvicorn process to mis-kill.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import muxplex.manifest as manifest_mod
import muxplex.settings as settings_mod
from muxplex.main import app

pytestmark = pytest.mark.integration


def _tmux_env(tmux_socket_dir: Path) -> dict:
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


def _live_sessions(tmux_socket_dir: Path) -> list[str]:
    result = _tmux(
        tmux_socket_dir, "list-sessions", "-F", "#{session_name}", check=False
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture
def tmux_env_with_template(tmp_path, monkeypatch):
    """Real tmux on an isolated socket, isolated manifest, caller-set template.

    Yields a callable that installs a ``new_session_template`` and returns the
    socket dir.
    """
    tmux_socket_dir = tmp_path / "tmux-socket"
    tmux_socket_dir.mkdir()
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", tmp_path / "sessions.json")

    def install(new_session_template: str) -> Path:
        settings_mod.save_settings(
            {
                "tmux_socket_dir": str(tmux_socket_dir),
                "new_session_template": new_session_template,
            }
        )
        return tmux_socket_dir

    yield install

    _tmux(tmux_socket_dir, "kill-server", check=False)


@pytest.fixture
def client(monkeypatch):
    """In-process TestClient with the tmux-unrelated startup work stubbed."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")

    async def _mock_reap_orphan():
        return 0

    async def _noop_poll_loop() -> None:
        return None

    monkeypatch.setattr("muxplex.main.reap_orphan_ttyds", _mock_reap_orphan)
    monkeypatch.setattr("muxplex.main._poll_loop", _noop_poll_loop)

    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        c.cookies.set("muxplex_session", create_session_cookie(_auth_secret, _auth_ttl))
        yield c


def test_real_tmux_dot_rewrite_is_reported(client, tmux_env_with_template):
    """tmux turns ``.`` into ``_`` at rc=0; the API must report the real name.

    ``SESSION_NAME_RE`` permits dots, so muxplex accepts a name tmux will not
    keep. Nothing anywhere reports an error -- which is precisely why the
    response has to carry the observed name.
    """
    socket_dir = tmux_env_with_template("tmux new-session -d -s {name}")

    requested = "my-lane.v2.fix"
    response = client.post("/api/sessions", json={"name": requested})
    assert response.status_code == 200
    data = response.json()

    live = _live_sessions(socket_dir)
    # The premise: tmux really did create a DIFFERENT name than we asked for.
    assert requested not in live
    assert "my-lane_v2_fix" in live

    # The contract: the response names the session that actually exists.
    assert data["name"] == "my-lane_v2_fix"
    assert data["observed"] == "my-lane_v2_fix"
    assert data["name_confirmed"] is True
    assert data["requested_name"] == requested


def test_real_tmux_truncating_template_is_reported(client, tmux_env_with_template):
    """A template that derives a shorter name -- the user's live 32-char case.

    ``cut -c1-32`` stands in for ``amplifier-workspace``'s
    ``session_name_from_path()``, which sanitizes a path basename and
    truncates at ``SESSION_NAME_MAX = 32``. The mechanism under test is the
    same either way: the template, not muxplex, decides the final name.
    """
    socket_dir = tmux_env_with_template(
        "sh -c 'tmux new-session -d -s \"$(printf %s {name} | cut -c1-32)\"'"
    )

    requested = "home-assistant-smart-tool-team-ci-abcdef"
    assert len(requested) == 40
    truncated = requested[:32]

    response = client.post("/api/sessions", json={"name": requested})
    assert response.status_code == 200
    data = response.json()

    live = _live_sessions(socket_dir)
    assert requested not in live
    assert truncated in live

    assert data["name"] == truncated
    assert data["observed"] == truncated
    assert data["name_confirmed"] is True


def test_real_tmux_unmangled_name_round_trips_confirmed(client, tmux_env_with_template):
    """A name tmux keeps verbatim is reported as CONFIRMED, not merely echoed."""
    socket_dir = tmux_env_with_template("tmux new-session -d -s {name}")

    response = client.post("/api/sessions", json={"name": "plain-name"})
    assert response.status_code == 200
    data = response.json()

    assert "plain-name" in _live_sessions(socket_dir)
    assert data["name"] == "plain-name"
    assert data["observed"] == "plain-name"
    assert data["name_confirmed"] is True

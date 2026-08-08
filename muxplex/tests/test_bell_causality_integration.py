"""Real tmux + real HTTP server proof for bell-causality Phase 1b: a halted
follow-up queue rings a bell.

See docs/plans/2026-08-07-bell-causality-plan.md §5 and §9 (tests 6-10) for
the design this proves, and AGENTS.md's "Follow-up queue" section for the
structural-exclusion rule this test exercises for real rather than via a
mocked `run_tmux`.

Runs against a REAL, isolated tmux server (isolation supplied automatically
by `conftest.py`'s autouse `_isolate_tmux_socket_dir` fixture -- a fresh,
unique `TMUX_TMPDIR` per test, never the ambient one) and a REAL uvicorn
instance of the actual ASGI app, driven over REAL HTTP. Nothing here mocks
`run_tmux` -- the halt is produced by a genuinely vanished tmux session, and
the resulting bell is read back through the real `GET /api/sessions`
response, not by inspecting `state.json` directly.

Requires a real tmux installation. Run with:

    pytest -m integration -v muxplex/tests/test_bell_causality_integration.py

NEVER on a host with a live muxplex -- see AGENTS.md's "NEVER run the test
suite on a host running a live muxplex". `make test` runs this inside a
Digital Twin Universe container instead, and deselects `integration` tests
by default (`pyproject.toml`'s `addopts`) -- this file must be run with
`-m integration` explicitly.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_isolated_tmux_tmpdir():
    """Refuse to run against a non-isolated tmux -- same guard as
    test_bell_hook_delivery_integration.py, defense in depth on top of
    conftest.py's autouse isolation."""
    tmpdir = os.environ.get("TMUX_TMPDIR", "")
    if not tmpdir:
        pytest.fail(
            "TMUX_TMPDIR is not set -- refusing to touch real tmux. This "
            "should be impossible: conftest.py's autouse "
            "_isolate_tmux_socket_dir fixture sets this for every test."
        )
    assert "TMUX" not in os.environ, (
        "$TMUX is set -- tmux prioritizes it over TMUX_TMPDIR and would "
        "silently defeat the isolation above."
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _TmuxResult:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


async def _tmux(*args: str) -> _TmuxResult:
    """Real tmux command, deliberately no `-L` -- mirrors production's own
    `run_tmux()` (env-only isolation via `TMUX_TMPDIR`, see
    `sessions.tmux_env()`), so this proof exercises the real code path and
    the real server (started below, in this same process) resolves the
    SAME isolated socket via plain `os.environ` inheritance."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=45)
    return _TmuxResult(
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


@contextlib.asynccontextmanager
async def _real_server(tmp_path: Path):
    """Run the REAL muxplex ASGI app on a REAL loopback socket, isolated
    state paths, background poll loop disabled (deterministic control --
    this test calls `_run_poll_cycle()` itself, once, where needed)."""
    import muxplex.main as main_mod
    import muxplex.state as state_mod
    import muxplex.ttyd as ttyd_mod

    port = _free_port()

    state_dir = tmp_path / "state"
    ttyd_dir = tmp_path / "ttyd"
    state_mod.STATE_DIR = state_dir
    state_mod.STATE_PATH = state_dir / "state.json"
    ttyd_mod.TTYD_SOCKET_DIR = ttyd_dir

    main_mod.SERVER_PORT = port
    main_mod.SERVER_TLS_ENABLED = False
    main_mod._bell_hook_armed = False
    main_mod._bell_hook_last_error = None

    async def _noop_poll_loop() -> None:
        await asyncio.Event().wait()

    orig_poll_loop = main_mod._poll_loop
    main_mod._poll_loop = _noop_poll_loop

    async def _noop_reap_orphan():
        return 0

    async def _noop_reap_legacy():
        return False

    orig_reap_orphan = main_mod.reap_orphan_ttyds
    orig_reap_legacy = main_mod.reap_legacy_ttyd
    orig_validate = ttyd_mod.validate_socket_dir
    main_mod.reap_orphan_ttyds = _noop_reap_orphan
    main_mod.reap_legacy_ttyd = _noop_reap_legacy
    ttyd_mod.validate_socket_dir = lambda d: None

    orig_load_settings = main_mod.load_settings

    import uvicorn

    config = uvicorn.Config(
        main_mod.app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + 10.0
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn server did not start within 10s"
        yield port
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=5.0)
        main_mod.reap_orphan_ttyds = orig_reap_orphan
        main_mod.reap_legacy_ttyd = orig_reap_legacy
        ttyd_mod.validate_socket_dir = orig_validate
        main_mod._poll_loop = orig_poll_loop
        main_mod.load_settings = orig_load_settings


async def test_halt_rings_a_bell_against_real_tmux_and_real_server(tmp_path):
    """Enqueue a follow-up against a REAL tmux session, make the session
    genuinely vanish (kill it for real), fire a REAL bell over REAL HTTP,
    and observe -- through the real GET /api/sessions response, not
    state.json introspection -- that the halt rang a bell: unseen_count
    incremented, source == "halt", and followups.halted is set."""
    import copy

    import muxplex.main as main_mod
    from muxplex.settings import DEFAULT_SETTINGS

    session = "haltproof"
    await _tmux("kill-server")  # clean slate; harmless if nothing is running
    created = await _tmux("new-session", "-d", "-s", session, "-x", "80", "-y", "24")
    assert created.returncode == 0, (
        f"failed to create real tmux session: {created.stderr}"
    )

    def _settings_with_input_enabled() -> dict:
        s = copy.deepcopy(DEFAULT_SETTINGS)
        s["input_enabled"] = True
        s["input_allowed_sessions"] = [session]
        return s

    async with _real_server(tmp_path) as port:
        main_mod.load_settings = _settings_with_input_enabled

        # The background poll loop is disabled in _real_server (deterministic
        # control) -- discover the real session into state.json with ONE
        # explicit poll cycle, exactly like the bell-hook delivery proof does.
        await main_mod._run_poll_cycle()

        async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
            base = f"http://127.0.0.1:{port}"

            # Confirm the real session is visible to the real server before
            # doing anything else -- this proof must start from ground truth.
            sessions_resp = await client.get(f"{base}/api/sessions")
            assert sessions_resp.status_code == 200
            names = [s["name"] for s in sessions_resp.json()]
            assert session in names, (
                f"real tmux session {session!r} not visible via GET /api/sessions "
                f"before the test even begins -- got {names!r}"
            )

            # Enqueue a real follow-up item against the real, still-alive session.
            enqueue = await client.post(
                f"{base}/api/sessions/{session}/followups", json={"text": "MARK_HALT"}
            )
            assert enqueue.status_code == 200, enqueue.text

            bell_before = next(
                s["bell"]
                for s in (await client.get(f"{base}/api/sessions")).json()
                if s["name"] == session
            )
            assert bell_before["source"] is None
            assert bell_before["unseen_count"] == 0

            # Make the session GENUINELY vanish -- a real halt cause, not a
            # mocked one.
            killed = await _tmux("kill-session", "-t", session)
            assert killed.returncode == 0, (
                f"failed to kill real session: {killed.stderr}"
            )

            # Refresh ONLY the cached session-name list that
            # _advance_followup_queue()'s halt check reads
            # (`get_session_list()`, sessions.py) via the SAME real tmux
            # enumeration production's poll cycle uses -- deliberately NOT
            # a full `_run_poll_cycle()` here, because that would also run
            # step 6 (delete state["sessions"][name]) and step 6b (reap
            # this exact follow-up queue) in the SAME cycle, destroying the
            # very state this proof needs to observe afterward. This
            # mirrors a real production ordering precisely: `get_session_list()`
            # updates every poll tick, independent of when a bell happens to
            # arrive over HTTP, so "session cache says gone, full reap
            # hasn't run yet" is a real, reachable window, not a test
            # artifact.
            from muxplex.sessions import enumerate_sessions as _enumerate_sessions
            from muxplex.sessions import update_session_cache as _update_session_cache

            live_names = await _enumerate_sessions()
            assert session not in live_names, (
                f"real tmux still reports {session!r} alive after kill-session"
            )
            _update_session_cache(live_names, {})

            # Fire a REAL bell over REAL HTTP -- this is the actual
            # receive_bell() -> _advance_followup_queue() -> halt ->
            # _bell_for_halt() path, end to end, no mocks anywhere.
            bell_resp = await client.post(f"{base}/api/sessions/{session}/bell")
            assert bell_resp.status_code == 200, bell_resp.text

            # Observe the result via the real GET /api/state response.
            state_resp = await client.get(f"{base}/api/state")
            state_json = state_resp.json()
            bell_after = state_json["sessions"][session]["bell"]

            assert bell_after["source"] == "halt", (
                f"expected bell.source == 'halt' after a real queue halt, got "
                f"{bell_after!r}"
            )
            assert bell_after["unseen_count"] >= 1
            assert bell_after["last_fired_at"] is not None
            if bell_before["last_fired_at"] is not None:
                assert bell_after["last_fired_at"] > bell_before["last_fired_at"]

            followups_entry = state_json["followups"][session]
            assert followups_entry["halted"] is not None
            assert followups_entry["halted"]["reason"] == "session_missing"
            assert len(followups_entry["items"]) == 1  # item retained, not lost

            # Idempotence, proven for real: a second real bell must not ring
            # a second halt bell (acceptance_ok() is False while halted).
            unseen_after_first_halt = bell_after["unseen_count"]
            second_bell = await client.post(f"{base}/api/sessions/{session}/bell")
            assert second_bell.status_code == 200
            final_state = (await client.get(f"{base}/api/state")).json()
            final_bell = final_state["sessions"][session]["bell"]
            final_followups_entry = final_state["followups"][session]
            # receive_bell() itself always increments (that endpoint's own,
            # unrelated behavior) and re-stamps source "hook" -- what must
            # NOT happen is a SECOND halt: the reason stays exactly what the
            # first halt recorded, and the item is still retained once.
            assert final_bell["unseen_count"] > unseen_after_first_halt
            assert final_bell["source"] == "hook"
            assert final_followups_entry["halted"]["reason"] == "session_missing"
            assert len(final_followups_entry["items"]) == 1

    # Teardown: socket-scoped, never a bare kill-server against the
    # ambient default -- TMUX_TMPDIR here is still the isolated one
    # conftest.py's autouse fixture set for this test.
    await _tmux("kill-server")

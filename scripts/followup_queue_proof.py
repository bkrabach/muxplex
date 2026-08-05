#!/usr/bin/env python3
"""
Standalone, real-tmux integration proof for the follow-up queue.

Run ONLY inside an isolated environment (a DTU container, never a host
serving a live muxplex) -- see AGENTS.md. This script:

  1. Spins up an ISOLATED tmux server (tmux -L <unique>, scratch TMUX_TMPDIR)
     -- never the ambient/default tmux server.
  2. Creates a real session on it.
  3. Runs the muxplex FastAPI app in-process (Starlette TestClient) with
     input_enabled=true / input_allowed_sessions matching the test session,
     with REAL (unmocked) tmux calls targeting the isolated server above.
     Deliberately synchronous top-to-bottom (no asyncio.run wrapper): the
     app's own background poll loop runs inside TestClient's internally
     managed event loop, and mixing that with an externally driven asyncio
     loop for direct _run_poll_cycle() calls binds muxplex.state.state_lock
     to two different event loops and raises. Relying on the REAL poll
     loop (never mocked here) is also more honest as a proof: it exercises
     the exact same code path production runs.
  4. Proof A: queues 3 items, fires 3 real bells (tmux send-keys printf '\\a'),
     and shows them landing one per bell, in order, via capture-pane and
     GET .../followups.
  5. Proof B: flips input_enabled to false, queues+bells again, and shows
     the item is refused, the halt is visible (not silent), and nothing is
     typed into the pane.

Teardown is socket-scoped (`tmux -L <name> kill-server`), never a bare
`kill-server` and never `pkill`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

POLL_SETTLE_S = 2.6  # > POLL_INTERVAL (2.0s default) so the real poll loop runs


def log(msg: str) -> None:
    print(f"[proof] {msg}", flush=True)


def main() -> int:
    # NOTE: muxplex's own run_tmux() calls plain `tmux ...` -- the DEFAULT
    # socket name -- and isolates ONLY via TMUX_TMPDIR (see
    # sessions.tmux_env()/AGENTS.md's "Running a second instance on one
    # box"). A custom `-L <name>` socket (the isolation muxplex ITSELF
    # uses when it must never interact with the session, e.g. proving the
    # bell-hook-arming hazard) would put our session on a DIFFERENT tmux
    # server than the one muxplex's own subprocess calls resolve to in the
    # SAME TMUX_TMPDIR directory -- invisible to it. Here we WANT muxplex
    # to see and interact with this session, so isolation is TMUX_TMPDIR
    # alone, default socket name, exactly matching muxplex's own calls.
    tmux_tmpdir = Path(tempfile.mkdtemp(prefix="muxq-tmuxdir-"))
    scratch_home = Path(tempfile.mkdtemp(prefix="muxq-home-"))
    session_name = "itest-followup"

    env = os.environ.copy()
    env["TMUX_TMPDIR"] = str(tmux_tmpdir)
    env.pop("TMUX", None)

    def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux", *args],
            env=env,
            capture_output=True,
            text=True,
            check=check,
        )

    log(f"isolated TMUX_TMPDIR={tmux_tmpdir} (default socket, isolated by directory)")
    tmux("new-session", "-d", "-s", session_name, "-x", "80", "-y", "24")
    log(f"created isolated session {session_name!r}")

    # --- point muxplex's own tmux calls at the SAME isolated server -----
    os.environ["TMUX_TMPDIR"] = str(tmux_tmpdir)
    os.environ.pop("TMUX", None)
    os.environ["MUXPLEX_STATE_DIR"] = str(scratch_home / "state")
    os.environ["HOME"] = str(scratch_home)  # settings.json etc. isolated too

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    import muxplex.settings as settings_mod
    import muxplex.state as state_mod

    settings_mod.SETTINGS_PATH = scratch_home / "settings.json"
    state_mod.STATE_DIR = scratch_home / "state"
    state_mod.STATE_PATH = state_mod.STATE_DIR / "state.json"

    import muxplex.main as main_mod
    from starlette.testclient import TestClient

    settings_mod.save_settings(
        {
            **settings_mod.load_settings(),
            "input_enabled": True,
            "input_allowed_sessions": ["itest-*"],
        }
    )

    results: dict = {}
    proof_a: list[dict] = []
    proof_a_final_pending = None

    with TestClient(main_mod.app) as client:
        from muxplex.auth import create_session_cookie

        cookie = create_session_cookie(main_mod._auth_secret, main_mod._auth_ttl)
        client.cookies.set("muxplex_session", cookie)

        # Let the app's REAL background poll loop (started by lifespan,
        # never mocked here) discover the session and attempt to arm the
        # bell hook against the isolated server.
        time.sleep(POLL_SETTLE_S)
        results["bell_hook_armed"] = main_mod._bell_hook_armed
        log(f"bell_hook_armed = {main_mod._bell_hook_armed!r}")

        # ---------------- Proof A: drains across real bells -------------
        for text in ("MARK_ONE", "MARK_TWO", "MARK_THREE"):
            r = client.post(
                f"/api/sessions/{session_name}/followups", json={"text": text}
            )
            assert r.status_code == 200, r.text
        log("queued MARK_ONE, MARK_TWO, MARK_THREE")

        for expected in ("MARK_ONE", "MARK_TWO", "MARK_THREE"):
            tmux("send-keys", "-t", session_name, "printf '\\a'", "Enter")
            time.sleep(0.5)  # settle for send-keys + state write
            pane = tmux("capture-pane", "-t", session_name, "-p").stdout
            state = client.get(f"/api/sessions/{session_name}/followups").json()
            proof_a.append(
                {
                    "expected": expected,
                    "pane_contains_expected": expected in pane,
                    "pane_tail": pane.strip().splitlines()[-3:],
                    "pending": len(state["items"]),
                    "revision": state["revision"],
                }
            )
            log(
                f"bell -> expected={expected!r} present={expected in pane!r} "
                f"pending={len(state['items'])} revision={state['revision']}"
            )
            # Past the settle window before the next bell.
            main_mod.followups._followup_last_send_at[session_name] = (
                time.time() - main_mod.followups.FOLLOWUP_SETTLE_SECONDS - 0.5
            )

        final_state = client.get(f"/api/sessions/{session_name}/followups").json()
        proof_a_final_pending = len(final_state["items"])
        log(f"after 3 bells: pending={proof_a_final_pending} (expect 0)")

        # Fourth bell: no-op, no error.
        tmux("send-keys", "-t", session_name, "printf '\\a'", "Enter")
        time.sleep(0.5)
        r = client.get(f"/api/sessions/{session_name}/followups")
        assert r.status_code == 200
        results["proof_a"] = proof_a
        results["proof_a_final_pending"] = proof_a_final_pending

        # ---------------- Proof B: fence refuses, halt is visible -------
        pane_before = tmux("capture-pane", "-t", session_name, "-p").stdout

        settings_mod.save_settings(
            {**settings_mod.load_settings(), "input_enabled": False}
        )
        r = client.post(
            f"/api/sessions/{session_name}/followups", json={"text": "SHOULD_NOT_FIRE"}
        )
        results["proof_b_enqueue_status"] = r.status_code
        log(f"enqueue while input_enabled=false -> {r.status_code} (expect 403)")

        # Force it directly into state to also test the FIRE-time re-check
        # independent of the enqueue-time UX check (spec section 6.2/6.3).
        state = state_mod.load_state()
        main_mod.followups.append_item(
            state, session_name, "SHOULD_NOT_FIRE_FIRE_TIME", True
        )
        state_mod.save_state(state)

        tmux("send-keys", "-t", session_name, "printf '\\a'", "Enter")
        time.sleep(0.5)
        pane_after = tmux("capture-pane", "-t", session_name, "-p").stdout
        halted_state = client.get(f"/api/sessions/{session_name}/followups").json()

        results["proof_b_pane_unchanged"] = pane_before.strip() == pane_after.strip()
        results["proof_b_halted"] = halted_state["halted"]
        results["proof_b_pending_after"] = len(halted_state["items"])
        log(f"pane unchanged: {results['proof_b_pane_unchanged']}")
        log(f"halted (visible, not silent): {json.dumps(halted_state['halted'])}")
        log(f"pending retained: {results['proof_b_pending_after']} (expect 1)")

        # Restore, resume, confirm it fires now.
        settings_mod.save_settings(
            {**settings_mod.load_settings(), "input_enabled": True}
        )
        client.post(f"/api/sessions/{session_name}/followups/resume")
        main_mod.followups._followup_last_send_at.pop(session_name, None)
        tmux("send-keys", "-t", session_name, "printf '\\a'", "Enter")
        time.sleep(0.5)
        resumed_state = client.get(f"/api/sessions/{session_name}/followups").json()
        results["proof_b_fires_after_resume"] = len(resumed_state["items"]) == 0
        log(f"fires after resume: {results['proof_b_fires_after_resume']}")

    # ---------------- teardown: socket-scoped, never bare kill-server ---
    tmux("kill-server", check=False)
    shutil.rmtree(tmux_tmpdir, ignore_errors=True)
    shutil.rmtree(scratch_home, ignore_errors=True)

    print("\n=== RESULTS JSON ===")
    print(json.dumps(results, indent=2, default=str))

    ok = (
        all(p["pane_contains_expected"] for p in proof_a)
        and proof_a_final_pending == 0
        and results["proof_b_enqueue_status"] == 403
        and results["proof_b_pane_unchanged"] is True
        and results["proof_b_halted"] is not None
        and results["proof_b_pending_after"] == 1
        and results["proof_b_fires_after_resume"] is True
    )
    log(f"OVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

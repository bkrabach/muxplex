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

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def log(msg: str) -> None:
    print(f"[proof] {msg}", flush=True)


async def main() -> int:
    socket_name = f"muxq-proof-{uuid.uuid4().hex[:8]}"
    tmux_tmpdir = Path(tempfile.mkdtemp(prefix="muxq-tmuxdir-"))
    scratch_home = Path(tempfile.mkdtemp(prefix="muxq-home-"))
    session_name = "itest-followup"

    env = os.environ.copy()
    env["TMUX_TMPDIR"] = str(tmux_tmpdir)
    env.pop("TMUX", None)

    def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux", "-L", socket_name, *args],
            env=env,
            capture_output=True,
            text=True,
            check=check,
        )

    log(f"isolated tmux socket={socket_name} TMUX_TMPDIR={tmux_tmpdir}")
    tmux("new-session", "-d", "-s", session_name, "-x", "80", "-y", "24")
    log(f"created isolated session {session_name!r}")

    # --- point muxplex's own tmux calls at the SAME isolated server -----
    os.environ["TMUX_TMPDIR"] = str(tmux_tmpdir)
    os.environ.pop("TMUX", None)
    os.environ["MUXPLEX_STATE_DIR"] = str(scratch_home / "state")
    os.environ["HOME"] = str(scratch_home)  # settings.json etc. isolated too

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import importlib

    import muxplex.settings as settings_mod
    import muxplex.state as state_mod

    settings_mod.SETTINGS_PATH = scratch_home / "settings.json"
    state_mod.STATE_DIR = scratch_home / "state"
    state_mod.STATE_PATH = state_mod.STATE_DIR / "state.json"

    import muxplex.main as main_mod

    importlib.reload(main_mod)  # pick up the env vars set above

    from starlette.testclient import TestClient

    # Enable input for the test session, arm nothing external (bell hook
    # arming would try to set-hook against the isolated server, which is
    # fine since it's isolated -- but the probe self-check needs the app
    # actually serving; TestClient's lifespan handles startup).
    settings_mod.save_settings(
        {
            **settings_mod.load_settings(),
            "input_enabled": True,
            "input_allowed_sessions": ["itest-*"],
        }
    )

    results: dict = {}

    with TestClient(main_mod.app) as client:
        from muxplex.auth import create_session_cookie

        cookie = create_session_cookie(main_mod._auth_secret, main_mod._auth_ttl)
        client.cookies.set("muxplex_session", cookie)

        # One poll cycle so the session is known to the app.
        await main_mod._run_poll_cycle()

        results["bell_hook_armed"] = main_mod._bell_hook_armed
        log(f"bell_hook_armed = {main_mod._bell_hook_armed!r}")

        # ---------------- Proof A: drains across real bells -------------
        for text in ("MARK_ONE", "MARK_TWO", "MARK_THREE"):
            r = client.post(
                f"/api/sessions/{session_name}/followups", json={"text": text}
            )
            assert r.status_code == 200, r.text
        log("queued MARK_ONE, MARK_TWO, MARK_THREE")

        proof_a: list[dict] = []
        for expected in ("MARK_ONE", "MARK_TWO", "MARK_THREE"):
            tmux("send-keys", "-t", session_name, "printf '\\a'", "Enter")
            await asyncio.sleep(0.3)  # settle for send-keys + state write
            pane = tmux("capture-pane", "-t", session_name, "-p").stdout
            state = client.get(f"/api/sessions/{session_name}/followups").json()
            proof_a.append(
                {
                    "expected": expected,
                    "pane_contains_expected": expected in pane,
                    "pane": pane.strip().splitlines()[-3:],
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
        await asyncio.sleep(0.3)
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
        await asyncio.sleep(0.3)
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
        await asyncio.sleep(0.3)
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
    raise SystemExit(asyncio.run(main()))

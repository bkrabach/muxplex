#!/usr/bin/env python3
"""
Standalone, real-tmux, real-server integration proof for the follow-up queue.

Run ONLY inside an isolated environment (a DTU container, never a host
serving a live muxplex) -- see AGENTS.md.

Unlike an in-process TestClient, this launches a REAL muxplex server
(uvicorn, bound to a scratch loopback port) as a subprocess: the bell
hook's arm-time delivery PROBE (`_arm_bell_hook()`) does a real HTTP
round-trip to the server's own port, which only exists when something is
actually listening -- an in-process ASGI transport has no such thing, so
`_bell_hook_armed` can never go True against one. This is also the
faithful shape of FOLLOWUP_QUEUE_SPEC.md's own T-40 ("start a muxplex
instance... on a monkeypatched port").

Steps:
  1. Spin up an ISOLATED tmux server (TMUX_TMPDIR-scoped, default socket
     name -- muxplex's own run_tmux() calls plain `tmux ...` and isolates
     via TMUX_TMPDIR alone; a custom -L name would put the session
     somewhere muxplex can't see, per sessions.tmux_env()).
  2. Create a real tmux session on it.
  3. Launch a real muxplex server subprocess (uvicorn) bound to a scratch
     port, with input_enabled=true/input_allowed_sessions matching the
     test session pre-seeded in settings.json before startup.
  4. Wait for the server to be ready and the bell hook to actually arm
     (GET /api/instance-info's bell_hook_armed) -- proving the probe from
     §0.1 of the spec, not just that registration succeeded.
  5. Proof A: queue 3 items, fire 3 real bells (tmux send-keys printf
     '\\a'), show them landing one per bell, in order (capture-pane +
     GET .../followups after each).
  6. Proof B: flip input_enabled false, show enqueue is refused (403),
     force an item directly into state to also exercise the FIRE-time
     re-check, fire a bell, show the pane is UNCHANGED and the halt is
     VISIBLE (not silent) via GET .../followups with the item retained,
     then restore + resume + fire and show it drains.

Teardown: kill the exact subprocess PID (never pkill), and
`tmux kill-server` scoped by our own isolated TMUX_TMPDIR env (never a
bare kill-server against the ambient server).
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

import httpx

POLL_INTERVAL_S = 2.0  # matches main.py's default POLL_INTERVAL
READY_TIMEOUT_S = 20.0
ARM_TIMEOUT_S = 20.0


def log(msg: str) -> None:
    print(f"[proof] {msg}", flush=True)


def fire_bell(tmux, session_name: str) -> None:
    """Fire a real tmux bell in *session_name*: literal-mode send of the
    shell command text (matching muxplex's own build_send_text_argv --
    argv, never a shell, so no quoting is needed or wanted), then a
    SEPARATE named-key send of Enter (matching build_send_key_argv)."""
    tmux("send-keys", "-l", "-t", session_name, "--", "printf '\\a'")
    tmux("send-keys", "-t", session_name, "Enter")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    tmux_tmpdir = Path(tempfile.mkdtemp(prefix="muxq-tmuxdir-"))
    scratch_home = Path(tempfile.mkdtemp(prefix="muxq-home-"))
    session_name = "itest-followup"
    port = 18089  # scratch, never 8088

    tmux_env = os.environ.copy()
    tmux_env["TMUX_TMPDIR"] = str(tmux_tmpdir)
    tmux_env.pop("TMUX", None)

    def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux", *args],
            env=tmux_env,
            capture_output=True,
            text=True,
            check=check,
        )

    log(f"isolated TMUX_TMPDIR={tmux_tmpdir} (default socket, isolated by directory)")
    tmux("new-session", "-d", "-s", session_name, "-x", "80", "-y", "24")
    log(f"created isolated session {session_name!r}")

    # --- pre-seed settings.json BEFORE the server starts ----------------
    settings_path = scratch_home / ".config" / "muxplex" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "input_enabled": True,
                "input_allowed_sessions": ["itest-*"],
                "password": "",
            }
        )
    )

    server_env = os.environ.copy()
    server_env["TMUX_TMPDIR"] = str(tmux_tmpdir)
    server_env.pop("TMUX", None)
    server_env["HOME"] = str(scratch_home)
    server_env["MUXPLEX_PORT"] = str(port)
    server_env["MUXPLEX_PASSWORD"] = "test-password"
    server_env["PYTHONUNBUFFERED"] = "1"

    log(f"starting real muxplex server on 127.0.0.1:{port}")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "muxplex.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=repo_root,
        env=server_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log(f"server pid={proc.pid}")

    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base, timeout=5.0)

    results: dict = {}
    proof_a: list[dict] = []
    proof_a_final_pending = None
    exit_code = 1

    try:
        # ---- wait for readiness -----------------------------------------
        deadline = time.time() + READY_TIMEOUT_S
        ready = False
        while time.time() < deadline:
            try:
                r = client.get("/api/instance-info")
                if r.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early with code {proc.returncode}")
            time.sleep(0.3)
        if not ready:
            raise RuntimeError("server never became ready")
        log("server ready")

        # ---- wait for the bell hook to actually ARM (the real proof) ----
        deadline = time.time() + ARM_TIMEOUT_S
        armed = False
        last_info = {}
        while time.time() < deadline:
            last_info = client.get("/api/instance-info").json()
            if last_info.get("bell_hook_armed"):
                armed = True
                break
            time.sleep(0.5)
        results["bell_hook_armed"] = armed
        results["bell_hook_last_error"] = last_info.get("bell_hook_last_error")
        log(
            f"bell_hook_armed = {armed!r} (last_error={last_info.get('bell_hook_last_error')!r})"
        )
        if not armed:
            raise RuntimeError(
                "bell hook never armed -- see bell_hook_last_error above"
            )

        # ---------------- Proof A: drains across real bells -------------
        for text in ("MARK_ONE", "MARK_TWO", "MARK_THREE"):
            r = client.post(
                f"/api/sessions/{session_name}/followups", json={"text": text}
            )
            assert r.status_code == 200, r.text
        log("queued MARK_ONE, MARK_TWO, MARK_THREE")

        for expected in ("MARK_ONE", "MARK_TWO", "MARK_THREE"):
            fire_bell(tmux, session_name)
            time.sleep(0.6)  # settle for the hook's own curl round-trip + state write
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
            time.sleep(2.1)  # past FOLLOWUP_SETTLE_SECONDS before the next bell

        final_state = client.get(f"/api/sessions/{session_name}/followups").json()
        proof_a_final_pending = len(final_state["items"])
        log(f"after 3 bells: pending={proof_a_final_pending} (expect 0)")

        # Fourth bell: no-op, no error.
        fire_bell(tmux, session_name)
        time.sleep(0.6)
        r = client.get(f"/api/sessions/{session_name}/followups")
        assert r.status_code == 200
        results["proof_a"] = proof_a
        results["proof_a_final_pending"] = proof_a_final_pending

        # ---------------- Proof B: fence refuses, halt is visible -------
        pane_before = tmux("capture-pane", "-t", session_name, "-p").stdout

        settings_now = client.get("/api/settings").json()
        settings_now["input_enabled"] = False
        r = client.patch("/api/settings", json=settings_now)
        log(f"PATCH input_enabled=false -> {r.status_code}")

        r = client.post(
            f"/api/sessions/{session_name}/followups", json={"text": "SHOULD_NOT_FIRE"}
        )
        results["proof_b_enqueue_status"] = r.status_code
        log(f"enqueue while input_enabled=false -> {r.status_code} (expect 403)")

        time.sleep(2.1)  # past the settle window from proof A's last send
        fire_bell(tmux, session_name)
        time.sleep(0.6)
        pane_after = tmux("capture-pane", "-t", session_name, "-p").stdout
        halted_state = client.get(f"/api/sessions/{session_name}/followups").json()

        results["proof_b_pane_unchanged"] = pane_before.strip() == pane_after.strip()
        results["proof_b_halted"] = halted_state["halted"]
        results["proof_b_pending_after"] = len(halted_state["items"])
        log(f"pane unchanged: {results['proof_b_pane_unchanged']}")
        log(f"halted (visible, not silent): {json.dumps(halted_state['halted'])}")
        log(f"pending retained: {results['proof_b_pending_after']} (expect >=1)")

        # Restore, resume, confirm it fires now.
        settings_now = client.get("/api/settings").json()
        settings_now["input_enabled"] = True
        client.patch("/api/settings", json=settings_now)
        client.post(f"/api/sessions/{session_name}/followups/resume")
        time.sleep(2.1)
        fire_bell(tmux, session_name)
        time.sleep(0.6)
        resumed_state = client.get(f"/api/sessions/{session_name}/followups").json()
        results["proof_b_fires_after_resume"] = len(resumed_state["items"]) == 0
        log(f"fires after resume: {results['proof_b_fires_after_resume']}")

        ok = (
            all(p["pane_contains_expected"] for p in proof_a)
            and proof_a_final_pending == 0
            and results["proof_b_enqueue_status"] == 403
            and results["proof_b_pane_unchanged"] is True
            and results["proof_b_halted"] is not None
            and results["proof_b_pending_after"] >= 1
            and results["proof_b_fires_after_resume"] is True
        )
        exit_code = 0 if ok else 1
    except Exception as exc:  # noqa: BLE001 -- top-level proof harness, log and exit nonzero
        results["error"] = str(exc)
        log(f"FAILED: {exc}")
    finally:
        client.close()
        # ---- teardown: exact PID, socket-scoped tmux kill -----------------
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
        server_log_tail = "\n".join((out or "").splitlines()[-40:])
        tmux("kill-server", check=False)
        shutil.rmtree(tmux_tmpdir, ignore_errors=True)
        shutil.rmtree(scratch_home, ignore_errors=True)

    print("\n=== RESULTS JSON ===")
    print(json.dumps(results, indent=2, default=str))
    print("\n=== SERVER LOG TAIL ===")
    print(server_log_tail)

    log(f"OVERALL: {'PASS' if exit_code == 0 else 'FAIL'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

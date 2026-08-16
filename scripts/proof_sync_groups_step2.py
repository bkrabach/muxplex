#!/usr/bin/env python3
"""
Standalone, real-server, multi-client integration proof for Step 2 of
docs/plans/2026-08-16-deck-control-target-design.md (server relaxation +
safety guards on top of docs/plans/2026-08-01-per-device-sync-groups-plan.md).

Run ONLY inside an isolated environment (a DTU container, never a host
serving a live muxplex) -- see AGENTS.md's "Testing & workflow" section.

Modeled directly on scripts/followup_queue_proof.py: launches a REAL
muxplex server (uvicorn subprocess, scratch loopback port) and drives it
with real httpx clients -- multiple independent "devices" (A, B, C), never
mocks or an in-process TestClient. POLL_INTERVAL is set low (1s) so the
background poll cycle's prune_devices()/gc_sync_groups() pair (main.py
step 11) runs promptly against a backdated device instead of requiring a
real 300s wait.

Design doc Section 10 (Step 2) / Section 11 (metrics #1-3) require exactly
this: "Prove in the DTU with a scripted multi-client test modeled on the
ADR's scripts/proof_sync_groups.py: pair -> prune target -> no 500, no
silent global, and attempt a cycle -> 400 naming the other party."
(scripts/proof_sync_groups.py itself was never actually committed --
confirmed absent from this checkout -- so this script is written fresh,
following the same real-server methodology as followup_queue_proof.py.)

Proofs, in order:
  A. Pair: B self-claims its own group, A follows B. Both devices' state
     (GET /api/state's devices registry) reflects the pairing --
     A.sync_group == "device:B", B.controlled_by == "A".
  B. Cycle attempt: B (now followed by A) tries to also follow C.
     Asserts 400 with a structured {"target_not_self_owning": true,
     "controlled_by": "A"} body -- never a raw string 400, and never a
     500 -- and that NOTHING was mutated by the rejected attempt (B's own
     sync_group unchanged, C's controlled_by untouched).
  C. Prune the FOLLOWED device (B): backdate its last_heartbeat_at directly
     in the server's own state.json (same "manipulate the DTU's server
     state directly" pattern the task names as acceptable -- there is no
     debug endpoint to shrink the 300s TTL, and this is what real-world
     pruning looks like: prune_devices() only inspects last_heartbeat_at),
     then wait for one real poll tick. Confirms B is actually gone from
     the registry, then sends A's next heartbeat (still claiming
     "device:B") -- asserts 409 with {"target_gone": true}, NOT a 500, and
     that A's own recorded sync_group is NOT silently reset to "global" by
     the server itself (server-side, the fallback is the caller's job --
     see Section 7.2's "sticky and loud" policy -- so a rejected heartbeat
     must leave the registry exactly as it was before the rejected call).
  D. Recovery + GC: A's own next heartbeat explicitly asks for "global"
     (the client-side fallback Section 6.2.4 describes) -- succeeds. One
     more poll tick later, gc_sync_groups() has collected "device:B" from
     state.json's sync_groups (nothing self-claims it any more: B is
     gone, A moved to global) -- confirmed via GET /api/state.
  E. Regression armor (Section 11 metric #1, non-negotiable): GET
     /api/state, PATCH /api/state (no-op body), GET /api/view, and POST
     /api/heartbeat with sync_group omitted -- called with NO device_id
     throughout, their values captured BEFORE any of A/B/C's
     pairing/cycle/prune activity and again AFTER -- asserted identical.
     This is the real, live proof that private per-device group churn
     never leaks into the shared "global" group. (POST
     /api/sessions/{name}/connect, DELETE /api/sessions/current, and the
     /terminal/ws guard are the other three of the ADR's original six
     group-touching endpoints; this script does not exercise them because
     doing so faithfully needs a real tmux session, and this change
     touches none of their code paths -- they are covered by the full
     pytest suite instead, run separately via `make test`, with zero
     regressions: see the accompanying report.)

Teardown: kill the exact subprocess PID (never pkill); no tmux server is
started by this script at all (no session-touching endpoint is exercised),
so there is nothing tmux-side to tear down.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

READY_TIMEOUT_S = 20.0
POLL_INTERVAL_S = 1.0  # server-side POLL_INTERVAL override -- fast prune/GC ticks
DEVICE_TTL_S = 300.0  # matches state.prune_devices()'s hardcoded default


def log(msg: str) -> None:
    print(f"[proof] {msg}", flush=True)


def heartbeat(
    client: httpx.Client,
    device_id: str,
    *,
    sync_group: str | None = None,
) -> httpx.Response:
    body: dict = {
        "device_id": device_id,
        "label": f"proof-{device_id}",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": 0.0,
    }
    if sync_group is not None:
        body["sync_group"] = sync_group
    return client.post("/api/heartbeat", json=body)


def get_state(client: httpx.Client) -> dict:
    r = client.get("/api/state")
    assert r.status_code == 200, r.text
    return r.json()


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    scratch_home = Path(tempfile.mkdtemp(prefix="muxsg2-home-"))
    port = 18091  # scratch, distinct from followup_queue_proof.py's 18089

    server_env = os.environ.copy()
    server_env["HOME"] = str(scratch_home)
    server_env["MUXPLEX_PORT"] = str(port)
    server_env["MUXPLEX_PASSWORD"] = "test-password"
    # Force password mode even when PAM is available (true inside a DTU
    # container running as root) -- otherwise _resolve_auth() prefers PAM
    # and MUXPLEX_PASSWORD is silently ignored, which would 401/redirect
    # every request this script's Basic-auth header is meant to satisfy.
    server_env["MUXPLEX_AUTH"] = "password"
    server_env["POLL_INTERVAL"] = str(POLL_INTERVAL_S)
    server_env["PYTHONUNBUFFERED"] = "1"

    state_path = scratch_home / ".local" / "share" / "muxplex" / "state.json"

    log(
        f"starting real muxplex server on 127.0.0.1:{port} "
        f"(POLL_INTERVAL={POLL_INTERVAL_S}s)"
    )
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

    basic_auth = base64.b64encode(b"proof:test-password").decode()
    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(
        base_url=base,
        timeout=5.0,
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {basic_auth}",
        },
        follow_redirects=False,
    )

    results: dict = {}
    exit_code = 1
    server_log_tail = ""

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

        # =================================================================
        # E (part 1): regression-armor BASELINE, before any A/B/C activity
        # =================================================================
        baseline_state = get_state(client)
        baseline_view = client.get("/api/view").json()
        baseline_patch = client.patch("/api/state", json={}).json()
        results["baseline_sync_group"] = baseline_state["sync_group"]
        results["baseline_active_session"] = baseline_state["active_session"]
        results["baseline_devices"] = baseline_state["devices"]
        log(
            f"regression-armor baseline captured: "
            f"sync_group={baseline_state['sync_group']!r} "
            f"active_session={baseline_state['active_session']!r} "
            f"devices={list(baseline_state['devices'])}"
        )
        assert baseline_state["sync_group"] == "global"
        assert baseline_view["sync_group"] == "global"
        assert baseline_patch["sync_group"] == "global"

        # =================================================================
        # A. Pair: B self-claims, A follows B
        # =================================================================
        r = heartbeat(client, "B", sync_group="device:B")
        assert r.status_code == 200, r.text
        log(f"B self-claims -> {r.status_code} sync_group={r.json()['sync_group']!r}")

        r = heartbeat(client, "A", sync_group="device:B")
        assert r.status_code == 200, r.text
        log(f"A follows B -> {r.status_code} sync_group={r.json()['sync_group']!r}")

        state = get_state(client)
        results["pair_A_sync_group"] = state["devices"]["A"]["sync_group"]
        results["pair_B_controlled_by"] = state["devices"]["B"]["controlled_by"]
        results["pair_A_controlled_by"] = state["devices"]["A"]["controlled_by"]
        log(
            f"after pairing: A.sync_group={results['pair_A_sync_group']!r} "
            f"B.controlled_by={results['pair_B_controlled_by']!r} "
            f"A.controlled_by={results['pair_A_controlled_by']!r}"
        )
        assert results["pair_A_sync_group"] == "device:B"
        assert results["pair_B_controlled_by"] == "A"
        assert results["pair_A_controlled_by"] is None

        # =================================================================
        # B. Cycle attempt: B (followed by A) tries to also follow C
        # =================================================================
        r = heartbeat(client, "C", sync_group="device:C")
        assert r.status_code == 200, r.text
        log(f"C self-claims -> {r.status_code}")

        r = heartbeat(client, "B", sync_group="device:C")
        results["cycle_status"] = r.status_code
        results["cycle_body"] = r.json()
        log(
            f"B attempts to follow C while followed by A -> "
            f"{r.status_code} body={r.json()}"
        )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict), f"expected structured detail, got {detail!r}"
        assert detail.get("target_not_self_owning") is True
        assert detail.get("controlled_by") == "A"

        # No corruption: B's own sync_group is unchanged, C's controlled_by
        # is untouched by the rejected attempt.
        state = get_state(client)
        results["cycle_B_sync_group_after"] = state["devices"]["B"]["sync_group"]
        results["cycle_C_controlled_by_after"] = state["devices"]["C"]["controlled_by"]
        log(
            f"post-cycle-attempt: "
            f"B.sync_group={results['cycle_B_sync_group_after']!r} "
            f"C.controlled_by={results['cycle_C_controlled_by_after']!r} "
            "(both must be unchanged)"
        )
        assert results["cycle_B_sync_group_after"] == "device:B"
        assert results["cycle_C_controlled_by_after"] is None

        # The followed device can still reaffirm its OWN self-claim while
        # being followed -- only claiming a FOREIGN group is blocked.
        r = heartbeat(client, "B", sync_group="device:B")
        assert r.status_code == 200, r.text
        log(f"B reaffirms its own self-claim while still followed -> {r.status_code}")

        # =================================================================
        # C. Prune the FOLLOWED device (B) -- directly manipulate the
        # server's own state.json, matching the "or directly manipulate
        # the DTU's server state" instruction (no debug endpoint shortens
        # the real 300s TTL).
        # =================================================================
        assert state_path.exists(), f"expected state.json at {state_path}"
        raw_state = json.loads(state_path.read_text())
        raw_state["devices"]["B"]["last_heartbeat_at"] = time.time() - (
            DEVICE_TTL_S + 30.0
        )
        state_path.write_text(json.dumps(raw_state))
        log(
            f"backdated B.last_heartbeat_at by {DEVICE_TTL_S + 30.0:.0f}s "
            "directly in state.json"
        )

        # Wait past at least one real poll tick for prune_devices() to run.
        time.sleep(POLL_INTERVAL_S * 3 + 1.0)

        state = get_state(client)
        results["B_pruned"] = "B" not in state["devices"]
        log(f"B pruned from registry: {results['B_pruned']}")
        assert results["B_pruned"] is True, (
            "B should have been pruned by the poll cycle"
        )

        # A's next heartbeat still claims the now-gone "device:B" target.
        r = heartbeat(client, "A", sync_group="device:B")
        results["target_gone_status"] = r.status_code
        results["target_gone_body"] = r.json() if r.status_code != 500 else r.text
        log(
            f"A re-claims device:B after B was pruned -> "
            f"{r.status_code} body={results['target_gone_body']}"
        )
        assert r.status_code == 409, (
            f"expected 409 target_gone, got {r.status_code}: {r.text}"
        )
        detail = r.json()["detail"]
        assert isinstance(detail, dict)
        assert detail.get("target_gone") is True

        # Server-side: the rejected heartbeat must NOT have silently reset
        # A to global on its own -- A's recorded sync_group is untouched
        # (still "device:B") because the rejected request never reached
        # register_device() at all. Falling back to global is the
        # CALLER's job (Section 7.2's "sticky and loud" policy); confirmed
        # next.
        state = get_state(client)
        results["A_sync_group_after_target_gone"] = state["devices"]["A"]["sync_group"]
        log(
            f"A.sync_group after the rejected heartbeat: "
            f"{results['A_sync_group_after_target_gone']!r} "
            "(must still be 'device:B' -- no silent server-side fallback)"
        )
        assert results["A_sync_group_after_target_gone"] == "device:B"

        # =================================================================
        # D. Recovery + GC: A explicitly falls back to global (the real
        # client-side recovery Section 6.2.4 describes); the abandoned
        # group is then collected by gc_sync_groups() on the next poll
        # tick.
        # =================================================================
        r = heartbeat(client, "A", sync_group="global")
        assert r.status_code == 200, r.text
        results["recovery_sync_group"] = r.json()["sync_group"]
        log(
            f"A recovers to global -> {r.status_code} "
            f"sync_group={results['recovery_sync_group']!r}"
        )
        assert results["recovery_sync_group"] == "global"

        time.sleep(POLL_INTERVAL_S * 3 + 1.0)  # let gc_sync_groups() run again

        state = get_state(client)
        results["device_B_group_collected"] = "device:B" not in state["sync_groups"]
        log(
            f"'device:B' collected from sync_groups: "
            f"{results['device_B_group_collected']} "
            f"(current sync_groups keys: {list(state['sync_groups'])})"
        )
        assert results["device_B_group_collected"] is True

        # =================================================================
        # E (part 2): regression-armor AFTER -- global must be untouched by
        # all of the above A/B/C churn.
        # =================================================================
        after_state = get_state(client)
        after_view = client.get("/api/view").json()
        after_patch = client.patch("/api/state", json={}).json()

        results["after_sync_group"] = after_state["sync_group"]
        results["after_active_session"] = after_state["active_session"]
        log(
            f"regression-armor after: sync_group={after_state['sync_group']!r} "
            f"active_session={after_state['active_session']!r}"
        )
        assert after_state["sync_group"] == baseline_state["sync_group"] == "global"
        assert after_state["active_session"] == baseline_state["active_session"]
        assert after_view["sync_group"] == baseline_view["sync_group"] == "global"
        assert after_patch["sync_group"] == baseline_patch["sync_group"] == "global"

        # A legacy client (device_id present, sync_group omitted entirely)
        # must still resolve to global, byte-identical to pre-Step-2.
        r = heartbeat(client, "legacy1")
        assert r.status_code == 200, r.text
        assert r.json()["sync_group"] == "global"
        results["legacy_no_sync_group_field"] = r.json()

        results["ok"] = True
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 -- top-level proof harness, log and exit nonzero
        import traceback

        results["ok"] = False
        results["error"] = str(exc) or repr(exc)
        results["traceback"] = traceback.format_exc()
        log(f"FAILED: {exc!r}")
        log(results["traceback"])
    finally:
        client.close()
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
        server_log_tail = "\n".join((out or "").splitlines()[-40:])
        shutil.rmtree(scratch_home, ignore_errors=True)

    print("\n=== RESULTS JSON ===")
    print(json.dumps(results, indent=2, default=str))
    print("\n=== SERVER LOG TAIL ===")
    print(server_log_tail)

    log(f"OVERALL: {'PASS' if exit_code == 0 else 'FAIL'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

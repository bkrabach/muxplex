#!/usr/bin/env python3
"""Spike Q3 -- do two per-session ttyds actually stay isolated?

Platform-verification probe for the per-session-ttyd architecture. This is
the load-bearing one: the whole point of moving from one shared ttyd to one
ttyd per session is that two devices can view two DIFFERENT sessions at the
same time. If output leaks between them, the architecture is unsound and the
keystroke-misdirection hazard documented in
docs/plans/2026-08-01-per-device-sync-groups-plan.md is still live.

Spins up two ttyd processes on two UNIX sockets attached to two different
tmux sessions, runs two CONCURRENT relays, has each type a marker only it
should ever see, and asserts neither relay observes the other's marker.

Usage:
    python3 scripts/spike_ttyd_session_isolation.py                # self-provisioning
    python3 scripts/spike_ttyd_session_isolation.py <sockA> <sockB>

Exit status: 0 only if BOTH relays see their own marker and NEITHER sees the
other's. Verified on Linux (ttyd 1.7.4) and macOS 26.6 arm64 (ttyd 1.7.7).
"""

import asyncio
import json
import sys
from contextlib import ExitStack
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_ttyd_harness import ttyd_session

OUTPUT = 0x30
RESIZE = 0x31

MARKER_A = "ALPHA_ONLY_TOKEN_7f3a"
MARKER_B = "BETA_ONLY_TOKEN_9c21"


async def drain(ws, seconds: float) -> str:
    chunks: list[str] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if isinstance(frame, (bytes, bytearray)) and frame and frame[0] == OUTPUT:
            chunks.append(bytes(frame[1:]).decode(errors="replace"))
    return "".join(chunks)


async def relay(sock_path: str, marker: str, other_marker: str) -> dict:
    uri = "ws://localhost/ws"
    async with websockets.unix_connect(sock_path, uri, subprotocols=["tty"]) as ws:
        await ws.send(json.dumps({"AuthToken": ""}))
        await ws.send(
            bytes([RESIZE]) + json.dumps({"columns": 80, "rows": 24}).encode()
        )

        await drain(ws, 1.0)  # discard banner/prompt noise

        await ws.send(bytes([OUTPUT]) + f"echo {marker}\n".encode())
        echoed = await drain(ws, 2.0)

        return {
            "marker": marker,
            "other_marker": other_marker,
            "sees_own_marker": marker in echoed,
            "sees_other_marker": other_marker in echoed,
            "raw": echoed,
        }


async def run(sock_a: str, sock_b: str) -> bool:
    result_a, result_b = await asyncio.gather(
        relay(sock_a, MARKER_A, MARKER_B),
        relay(sock_b, MARKER_B, MARKER_A),
    )

    for label, result in (("A (alpha)", result_a), ("B (beta)", result_b)):
        print(f"=== Session {label} relay result ===")
        print(f"  sees own marker ({result['marker']}): {result['sees_own_marker']}")
        print(
            f"  sees OTHER marker ({result['other_marker']}): {result['sees_other_marker']}"
        )
        print(f"  raw: {result['raw']!r}")
        print()

    no_crosstalk = (
        result_a["sees_own_marker"]
        and not result_a["sees_other_marker"]
        and result_b["sees_own_marker"]
        and not result_b["sees_other_marker"]
    )
    print(f"RESULT: no_crosstalk={no_crosstalk}")
    return no_crosstalk


def main() -> int:
    if len(sys.argv) > 2:
        return 0 if asyncio.run(run(sys.argv[1], sys.argv[2])) else 1

    with ExitStack() as stack:
        sock_a = stack.enter_context(ttyd_session("q3a"))
        sock_b = stack.enter_context(ttyd_session("q3b"))
        print(f"[harness] ttyd A on {sock_a}")
        print(f"[harness] ttyd B on {sock_b}\n")
        return 0 if asyncio.run(run(str(sock_a), str(sock_b))) else 1


if __name__ == "__main__":
    sys.exit(main())

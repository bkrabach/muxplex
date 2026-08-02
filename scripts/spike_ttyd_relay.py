#!/usr/bin/env python3
"""Spike Q2 -- can a Python client speak ttyd's real wire protocol over a
UNIX socket?

Platform-verification probe for the per-session-ttyd architecture. Q1 proves
the WebSocket upgrade is real; this proves the connection is *useful*: it
completes the `tty` subprotocol handshake, negotiates a resize, reads back
real PTY bytes, and types a marker that comes back echoed.

Wire protocol (from muxplex/frontend/terminal.js -- ttyd's actual protocol):
  1. Client -> Server: TEXT frame   {"AuthToken": ""}
  2. Client -> Server: BINARY frame 0x31 + JSON({columns, rows})   (resize)
  3. Server -> Client: BINARY frames, first byte 0x30 = PTY output
  4. Client -> Server: BINARY frame 0x30 + UTF-8 keystrokes        (input)

Uses the top-level `websockets.unix_connect`, which resolves to the legacy
client on websockets < 14 (the pyproject floor is >=11.0) and to
`websockets.asyncio.client.unix_connect` on >= 14. Both accept the
`(path, uri, subprotocols=[...])` call shape used here, so this probe works
across the whole supported range. Do NOT import from `websockets.legacy` or
`websockets.asyncio` directly -- that pins one half of the range.

Usage:
    python3 scripts/spike_ttyd_relay.py                     # spawns its own ttyd
    python3 scripts/spike_ttyd_relay.py <sock> [marker]     # existing ttyd

Verified on Linux (ttyd 1.7.4) and macOS 26.6 arm64 (ttyd 1.7.7).
"""

import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_ttyd_harness import ttyd_session

OUTPUT = 0x30
RESIZE = 0x31


async def drain(ws, seconds: float) -> str:
    """Collect PTY output frames for `seconds`, ignoring other frame types."""
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


async def relay(sock_path: str, marker: str) -> str:
    uri = "ws://localhost/ws"  # host/path are irrelevant over a unix socket

    async with websockets.unix_connect(sock_path, uri, subprotocols=["tty"]) as ws:
        print(f"[{marker}] connected, negotiated subprotocol: {ws.subprotocol!r}")

        # Step 1: auth handshake (ttyd requires this before it spawns the PTY)
        await ws.send(json.dumps({"AuthToken": ""}))

        # Step 2: initial size (binary frame, 0x31 prefix + JSON)
        await ws.send(
            bytes([RESIZE]) + json.dumps({"columns": 80, "rows": 24}).encode()
        )

        # Step 3: read real PTY bytes
        combined = await drain(ws, 2.0)
        print(f"[{marker}] collected {len(combined)} bytes of PTY output")
        print(f"[{marker}] raw output (repr): {combined!r}")

        # Step 4: type a distinctive marker, read it back
        await ws.send(bytes([OUTPUT]) + (marker + "\n").encode())
        echoed = await drain(ws, 2.0)
        print(f"[{marker}] echoed back after sending marker: {echoed!r}")

        return combined + echoed


def report(result: str, marker: str) -> int:
    prompt_like = ("$" in result) or ("#" in result) or (marker in result)
    got_bytes = bool(result.strip())
    print(f"\nRESULT: got_real_bytes={got_bytes} sees_marker_or_prompt={prompt_like}")
    return 0 if (got_bytes and prompt_like) else 1


def main() -> int:
    if len(sys.argv) > 1:
        marker = sys.argv[2] if len(sys.argv) > 2 else "MARKER"
        return report(asyncio.run(relay(sys.argv[1], marker)), marker)

    marker = "MARKER"
    with ttyd_session("q2") as sock:
        print(f"[harness] ttyd on {sock}\n")
        return report(asyncio.run(relay(str(sock), marker)), marker)


if __name__ == "__main__":
    sys.exit(main())

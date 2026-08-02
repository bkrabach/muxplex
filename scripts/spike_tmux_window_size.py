#!/usr/bin/env python3
"""Spike Q4 -- what does tmux do when two differently-sized clients attach?

Platform-verification probe for the per-session-ttyd architecture. Once each
device gets its own ttyd, two devices can attach to the SAME tmux session with
different terminal geometry. tmux's `window-size` option decides who wins, and
the answer determines whether a phone attaching to a session silently shrinks
the laptop's view of it.

Attaches two ttyds (two UNIX sockets) to ONE tmux session at different sizes
and samples tmux's own resolved window geometry at three points:

  1. only the large client attached
  2. both clients attached      <- the contended case
  3. only the large client again (small client released)

Merges the two original spike scripts (`q4_hold_resize.py` and
`q4_windowsize_client.py`), which held the connections open so a human could
run `tmux list-windows` in another terminal. Sampling inline makes the probe
self-contained and actually re-runnable.

Usage:
    python3 scripts/spike_tmux_window_size.py
    python3 scripts/spike_tmux_window_size.py --large 200x50 --small 80x24
    python3 scripts/spike_tmux_window_size.py --hold 30   # hold both attached
                                                          # for external observation

Verified on Linux (ttyd 1.7.4) and macOS 26.6 arm64 (ttyd 1.7.7).
"""

import argparse
import asyncio
import json
import subprocess
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spike_ttyd_harness import tmux_argv, tmux_query, ttyd_session

RESIZE = 0x31
SESSION_LABEL = "q4"


def parse_size(text: str) -> tuple[int, int]:
    cols, _, rows = text.partition("x")
    return int(cols), int(rows)


def matches(geometry: str, size: tuple[int, int]) -> bool:
    """Does tmux's reported geometry correspond to a client of `size`?

    tmux reports the WINDOW, which is one row shorter than the client when the
    status line is on -- so 80x24 client shows up as an 80x23 window.
    """
    if "x" not in geometry:
        return False
    cols, rows = parse_size(geometry)
    return cols == size[0] and abs(rows - size[1]) <= 1


def window_size_option() -> str:
    """The `window-size` option value tmux will apply to contended clients."""
    result = subprocess.run(
        [*tmux_argv(SESSION_LABEL), "show-options", "-g", "-v", "window-size"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "<unset>"


def observe(moment: str) -> str:
    """Ask tmux itself what the window geometry currently resolves to."""
    geometry = tmux_query(SESSION_LABEL, "#{window_width}x#{window_height}")
    attached = tmux_query(SESSION_LABEL, "#{session_attached}")
    print(
        f"  [{moment}] tmux window={geometry or '<none>'} "
        f"attached_clients={attached or '?'}"
    )
    return geometry


async def attach(stack: AsyncExitStack, sock: Path, cols: int, rows: int, label: str):
    """Open a ttyd WS connection and negotiate a size. Returns the ws object."""
    ws = await stack.enter_async_context(
        websockets.unix_connect(str(sock), "ws://localhost/ws", subprotocols=["tty"])
    )
    await ws.send(json.dumps({"AuthToken": ""}))
    await ws.send(
        bytes([RESIZE]) + json.dumps({"columns": cols, "rows": rows}).encode()
    )
    print(f"  [{label}] attached at {cols}x{rows}")
    return ws


async def pump(ws, seconds: float) -> None:
    """Read and discard frames so kernel buffers don't back up while holding."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(ws.recv(), timeout=min(0.25, remaining))
        except asyncio.TimeoutError:
            continue
        except websockets.exceptions.ConnectionClosed:
            return


async def run(
    sock_large: Path,
    sock_small: Path,
    large: tuple[int, int],
    small: tuple[int, int],
    hold: float,
) -> int:
    print("\n--- phase 1: large client only ---")
    async with AsyncExitStack() as stack:
        ws_large = await attach(stack, sock_large, large[0], large[1], "LARGE")
        await pump(ws_large, 1.0)
        geo_large_only = observe("large only")

        print("\n--- phase 2: both clients attached (contended) ---")
        async with AsyncExitStack() as inner:
            ws_small = await attach(inner, sock_small, small[0], small[1], "SMALL")
            held = max(1.0, hold)
            await asyncio.gather(pump(ws_large, held), pump(ws_small, held))
            geo_both = observe("both")

        print("\n--- phase 3: small client released ---")
        await pump(ws_large, 1.5)
        geo_after = observe("large only again")

    print()
    print(
        f"RESULT: large_only={geo_large_only} both={geo_both} "
        f"after_small_left={geo_after}"
    )
    print(f"        window-size={window_size_option()}")
    if matches(geo_both, small):
        print("        -> tmux followed the SMALL client")
    elif matches(geo_both, large):
        print("        -> tmux followed the LARGE client")
    else:
        print("        -> matched neither client; inspect the window-size option")
    return 0 if geo_both else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe tmux window-size behavior with two differently-sized clients."
    )
    parser.add_argument(
        "--large", default="200x50", help="COLSxROWS for the large client"
    )
    parser.add_argument(
        "--small", default="80x24", help="COLSxROWS for the small client"
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=1.5,
        help="seconds to hold both clients attached (raise for external observation)",
    )
    args = parser.parse_args()

    large = parse_size(args.large)
    small = parse_size(args.small)

    with (
        ttyd_session(
            "q4-large", session_label=SESSION_LABEL, width=large[0], height=large[1]
        ) as sock_large,
        ttyd_session("q4-small", session_label=SESSION_LABEL) as sock_small,
    ):
        print(f"[harness] tmux server: {' '.join(tmux_argv(SESSION_LABEL))}")
        print(f"[harness] global window-size option: {window_size_option()}")
        print(f"[harness] ttyd LARGE on {sock_large}")
        print(f"[harness] ttyd SMALL on {sock_small}")
        return asyncio.run(run(sock_large, sock_small, large, small, args.hold))


if __name__ == "__main__":
    sys.exit(main())

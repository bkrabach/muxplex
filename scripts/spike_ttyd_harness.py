#!/usr/bin/env python3
"""Shared harness for the per-session ttyd UNIX-socket spike probes.

Not a probe itself. Provides the setup/teardown every probe in this directory
needs: a throwaway tmux session on its own tmux socket, plus a ttyd process
bound to a UNIX domain socket attached to it.

The two hard-won findings from the original spike are encoded here as
executable guards rather than prose, because both are silent failures:

1. **ttyd only treats `-i <path>` as a UNIX socket if the path ends in
   `.sock`.** With any other suffix ttyd does NOT error. It logs
   `iface ... DOESN'T EXIST`, silently falls back to listening on **TCP port
   7681**, and stays alive with exit status 0 -- which reopens exactly the
   unauthenticated-writable-terminal exposure that `-i 127.0.0.1` was added
   to close (see AGENTS.md, "ttyd is loopback-only by design").
   `socket_path()` refuses to build a non-`.sock` path, and `ttyd_session()`
   verifies the socket file actually exists after launch.
   **Never trust ttyd's exit code or liveness as proof it bound a socket.**

2. **`sun_path` is short.** Linux allows 108 bytes, macOS 104 (103 usable,
   and only **102** survive the trip through ttyd). A raw session name in a
   deep temp dir overruns that. `socket_path()` therefore hashes the label
   into a short fixed-width name and range-checks the result.

Safety (AGENTS.md, "NEVER broad-kill by process name"): this harness kills
ONLY the exact ttyd PID it spawned, and only ever runs `kill-server` against
its own uniquely named `tmux -L` socket. It never pattern-matches a process
name and never touches the default tmux server, port 7682, or port 8088.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Kernel limits on sockaddr_un.sun_path, measured on real hosts.
SUN_PATH_MAX = {"Linux": 108, "Darwin": 104}
# macOS reports 104 but only 103 are usable, and ttyd's own handling costs one
# more byte -- 102 is the longest path that actually worked through ttyd.
TTYD_SUN_PATH_MAX = {"Linux": 107, "Darwin": 102}

# ttyd's UNIX-socket detection is suffix-based. See finding 1 above.
REQUIRED_SOCKET_SUFFIX = ".sock"

# A short, predictable base dir keeps us far under sun_path everywhere.
# Override for a host with an unusual /tmp policy.
DEFAULT_SOCKET_DIR = Path(os.environ.get("MUXPLEX_SPIKE_SOCKET_DIR", "/tmp"))


def sun_path_budget() -> int:
    """Largest socket path length that reliably works through ttyd here."""
    return TTYD_SUN_PATH_MAX.get(platform.system(), 102)


def socket_path(label: str, socket_dir: Path | None = None) -> Path:
    """Build a short, hashed, `.sock`-suffixed socket path for `label`.

    Deliberately does NOT interpolate the label itself: session names are
    arbitrary-length and would blow the sun_path budget (finding 2).
    """
    directory = Path(socket_dir) if socket_dir is not None else DEFAULT_SOCKET_DIR
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:10]
    path = directory / f"mxspk-{digest}{REQUIRED_SOCKET_SUFFIX}"

    if path.suffix != REQUIRED_SOCKET_SUFFIX:
        raise ValueError(
            f"socket path must end in {REQUIRED_SOCKET_SUFFIX!r}; ttyd silently "
            f"falls back to TCP :7681 otherwise. Got: {path}"
        )
    budget = sun_path_budget()
    if len(str(path)) > budget:
        raise ValueError(
            f"socket path is {len(str(path))} bytes, over this platform's "
            f"{budget}-byte ttyd sun_path budget: {path}"
        )
    return path


def tmux_socket_name(session_label: str) -> str:
    """Name of the dedicated `tmux -L` socket for `session_label`."""
    return f"mxspk-{session_label}"


def tmux_session_name(session_label: str) -> str:
    """Name of the throwaway tmux session for `session_label`."""
    return f"mxspk-{session_label}"


def tmux_argv(session_label: str) -> list[str]:
    """Base argv addressing ONLY this harness's own tmux server."""
    return ["tmux", "-L", tmux_socket_name(session_label), "-f", "/dev/null"]


def tmux_query(session_label: str, fmt: str) -> str:
    """Run `display-message -p <fmt>` against this harness's tmux server."""
    result = subprocess.run(
        [
            *tmux_argv(session_label),
            "display-message",
            "-p",
            "-t",
            tmux_session_name(session_label),
            fmt,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def require_binaries() -> None:
    """Fail early and legibly if ttyd or tmux is missing."""
    missing = [b for b in ("ttyd", "tmux") if shutil.which(b) is None]
    if missing:
        raise RuntimeError(
            f"missing required binaries: {', '.join(missing)}. "
            "These probes need a real ttyd and a real tmux."
        )


def _ensure_tmux_session(session_label: str, width: int, height: int) -> None:
    """Create the throwaway session if it isn't already there.

    Idempotent so two ttyds can share one session (the window-size probe).
    """
    base = tmux_argv(session_label)
    session = tmux_session_name(session_label)
    exists = subprocess.run(
        [*base, "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if exists.returncode == 0:
        return
    subprocess.run(
        [
            *base,
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            str(width),
            "-y",
            str(height),
        ],
        check=True,
    )


@contextmanager
def ttyd_session(
    label: str,
    *,
    session_label: str | None = None,
    socket_dir: Path | None = None,
    width: int = 80,
    height: int = 24,
    startup_timeout: float = 5.0,
) -> Iterator[Path]:
    """Run one ttyd bound to a UNIX socket, attached to a throwaway tmux session.

    `label` names this ttyd (and so its socket file). `session_label` names the
    tmux server + session; pass the same value from two `ttyd_session()` blocks
    to attach two ttyds to ONE session.

    Yields the socket path. On exit, kills only the exact PID it spawned and
    only its own tmux socket.
    """
    require_binaries()

    session_label = session_label if session_label is not None else label
    sock = socket_path(label, socket_dir)
    sock.unlink(missing_ok=True)

    _ensure_tmux_session(session_label, width, height)

    proc = subprocess.Popen(
        [
            "ttyd",
            "-W",
            "-m",
            "3",
            "-i",
            str(sock),
            "tmux",
            "-L",
            tmux_socket_name(session_label),
            "attach",
            "-t",
            tmux_session_name(session_label),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    try:
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if sock.exists():
                break
            if proc.poll() is not None:
                raise RuntimeError(
                    f"ttyd exited with status {proc.returncode} before creating {sock}"
                )
            time.sleep(0.05)
        else:
            # Finding 1: ttyd is probably ALIVE here, listening on TCP :7681.
            raise RuntimeError(
                f"ttyd is running (pid {proc.pid}) but {sock} does not exist. "
                "ttyd almost certainly fell back to TCP :7681 -- an "
                "unauthenticated writable terminal. Liveness and exit status "
                "are NOT proof that a socket was bound; check the file."
            )
        yield sock
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)  # exact PID only, never by name
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        subprocess.run(
            # socket-scoped; never a bare `tmux kill-server`
            [*tmux_argv(session_label), "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        sock.unlink(missing_ok=True)


def main() -> int:
    require_binaries()
    print(f"platform: {platform.system()}")
    print(f"sun_path kernel max: {SUN_PATH_MAX.get(platform.system(), 'unknown')}")
    print(f"usable budget through ttyd: {sun_path_budget()}")
    with ttyd_session("selfcheck") as sock:
        print(
            f"ttyd bound UNIX socket: {sock} "
            f"(exists={sock.exists()}, len={len(str(sock))})"
        )
    print("torn down cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

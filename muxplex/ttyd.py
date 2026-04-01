"""
ttyd process lifecycle management for the tmux-web muxplex.

Constants:
    TTYD_PID_DIR  — directory for the PID file (default: ~/.local/share/tmux-web/)
    TTYD_PID_PATH — full path to the PID file (TTYD_PID_DIR / 'ttyd.pid')
    TTYD_PORT     — port ttyd listens on (7682)

Module state:
    _active_process — the currently running ttyd subprocess (or None)

Public API:
    spawn_ttyd(session_name)  — spawn ttyd attached to a tmux session, write PID file
    kill_ttyd()               — kill the running ttyd process, clean up PID file
    kill_orphan_ttyd()        — kill any orphaned ttyd from a previous coordinator run
"""

import asyncio
import os
import signal
import subprocess as _subprocess
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

_default_ttyd_pid_dir = Path.home() / ".local" / "share" / "tmux-web"
TTYD_PID_DIR: Path = Path(os.environ.get("TMUX_WEB_STATE_DIR", _default_ttyd_pid_dir))
TTYD_PID_PATH: Path = TTYD_PID_DIR / "ttyd.pid"

TTYD_PORT: int = 7682

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_active_process: asyncio.subprocess.Process | None = None

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _kill_pids_on_port(port: int, sig: int) -> bool:
    """Find and signal all processes listening on *port* via lsof.

    Returns True if at least one PID was found and signalled.
    Silently ignores lsof unavailability and already-dead processes.
    """
    try:
        result = _subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        sent = False
        for pid_str in result.stdout.strip().split("\n"):
            pid_str = pid_str.strip()
            if not pid_str:
                continue
            try:
                orphan_pid = int(pid_str)
                os.kill(orphan_pid, sig)
                sent = True
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        return sent
    except Exception:  # noqa: BLE001
        # lsof not available, timed out, or other unexpected failure
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def kill_ttyd() -> bool:
    """Kill the running ttyd process and clean up the PID file.

    Belt-and-suspenders strategy:

    Strategy 1 — PID file:
        Reads the PID from TTYD_PID_PATH.  If no PID file exists, returns False.
        If the file content is not a valid integer, removes the file and returns
        False.  Checks whether the process is alive via ``os.kill(pid, 0)``.  If
        already gone (ProcessLookupError), cleans up and proceeds.  Otherwise
        sends SIGTERM and polls every 0.1 s for up to 2 s.

    Strategy 2 — port-based fallback:
        After the PID-file kill, finds and kills any process still listening on
        TTYD_PORT via ``lsof -ti :<port>``.  This catches orphaned ttyd processes
        whose PID was never recorded in the file (e.g. after a coordinator crash).
        A brief 0.3 s wait is added to let the OS release the port.

    The PID file and ``_active_process`` are cleared in all cases before
    returning.

    Returns:
        True  — a process was killed (or was already dead) via either strategy.
        False — no PID file found and no process was listening on the port.
    """
    global _active_process

    killed = False

    # -------------------------------------------------------------------
    # Strategy 1: PID file
    # -------------------------------------------------------------------
    if TTYD_PID_PATH.exists():
        try:
            pid = int(TTYD_PID_PATH.read_text().strip())
        except ValueError:
            TTYD_PID_PATH.unlink(missing_ok=True)
            pid = None
        else:
            # Check whether the process is still alive.
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                # Already dead — clean up and note success.
                TTYD_PID_PATH.unlink(missing_ok=True)
                killed = True
                pid = None
            else:
                # Process is alive — ask it to terminate.
                os.kill(pid, signal.SIGTERM)

                # Poll up to 2 s for the process to exit.
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        os.kill(pid, 0)
                    except (ProcessLookupError, PermissionError):
                        break
                    await asyncio.sleep(0.1)

                TTYD_PID_PATH.unlink(missing_ok=True)
                killed = True
                pid = None  # noqa: F841 (intentional)

    # -------------------------------------------------------------------
    # Strategy 2: port-based fallback — catch orphans not in PID file
    # -------------------------------------------------------------------
    if _kill_pids_on_port(TTYD_PORT, signal.SIGTERM):
        killed = True
        # Brief pause so the OS can release the port before the next spawn.
        await asyncio.sleep(0.3)

    _active_process = None
    return killed


async def kill_orphan_ttyd() -> bool:
    """Kill any orphaned ttyd process left over from a previous coordinator run.

    On coordinator startup, checks for a stale PID file from a previous run.
    If found, kills the process (if still running) and removes the PID file.
    Prevents two ttyd instances running simultaneously after a coordinator
    restart or crash.

    Delegates to kill_ttyd() for all process management and PID file cleanup.

    Returns:
        True  — an orphan was found (process was dead or alive).
        False — no PID file found, or PID file contained invalid content.
    """
    return await kill_ttyd()


async def spawn_ttyd(session_name: str) -> asyncio.subprocess.Process:
    """Spawn a ttyd process attached to *session_name* via ``tmux attach``.

    Runs::

        ttyd -W -m 3 -p 7682 tmux attach -t <session_name>

    Before spawning, verifies that TTYD_PORT is free.  If any process is still
    listening on the port (e.g. a race between kill_ttyd() and spawn_ttyd()),
    it sends SIGKILL to force-free the port immediately.

    stdout and stderr are discarded (DEVNULL).  The PID is written to
    TTYD_PID_PATH.  The process handle is stored in ``_active_process``.

    Args:
        session_name: The tmux session name to attach to.

    Returns:
        The asyncio.subprocess.Process object for the spawned ttyd.
    """
    global _active_process

    # Final port-free guard — catches races where kill_ttyd() returned but
    # the old ttyd hasn't fully released the socket yet.
    if _kill_pids_on_port(TTYD_PORT, signal.SIGKILL):
        await asyncio.sleep(0.3)

    proc = await asyncio.create_subprocess_exec(
        "ttyd",
        "-W",
        "-m",
        "3",
        "-p",
        str(TTYD_PORT),
        "tmux",
        "attach",
        "-t",
        session_name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,  # detach from parent process group so ttyd survives independently
    )

    # Write PID file (create parent dirs if needed)
    TTYD_PID_DIR.mkdir(parents=True, exist_ok=True)
    TTYD_PID_PATH.write_text(str(proc.pid))

    _active_process = proc
    return proc

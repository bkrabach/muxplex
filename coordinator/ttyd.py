"""
ttyd process lifecycle management for the tmux-web coordinator.

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
# Public API
# ---------------------------------------------------------------------------


async def kill_ttyd() -> bool:
    """Kill the running ttyd process and clean up the PID file.

    Reads the PID from TTYD_PID_PATH.  If no PID file exists, returns False.
    If the file content is not a valid integer, removes the file and returns False.

    Checks whether the process is alive via ``os.kill(pid, 0)``.  If the
    process is already gone (ProcessLookupError), cleans up and returns True.
    Otherwise sends SIGTERM and polls every 0.1 s for up to 2 s waiting for
    the process to exit.  The PID file and ``_active_process`` are cleared in
    all cases before returning.

    Uses ``asyncio.sleep`` for polling to avoid blocking the event loop.

    Returns:
        True  — process was killed or was already dead.
        False — no PID file found, or PID file contained invalid content.
    """
    global _active_process

    if not TTYD_PID_PATH.exists():
        return False

    try:
        pid = int(TTYD_PID_PATH.read_text().strip())
    except ValueError:
        TTYD_PID_PATH.unlink(missing_ok=True)
        return False

    # Check whether the process is still alive.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # Already dead — clean up and report success.
        TTYD_PID_PATH.unlink(missing_ok=True)
        _active_process = None
        return True

    # Process is alive — ask it to terminate.
    os.kill(pid, signal.SIGTERM)

    # Poll up to 2 s for the process to exit, yielding to the event loop each iteration.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            break
        await asyncio.sleep(0.1)

    # Always clean up regardless of whether the process exited in time.
    TTYD_PID_PATH.unlink(missing_ok=True)
    _active_process = None
    return True


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

    stdout and stderr are discarded (DEVNULL).  The PID is written to
    TTYD_PID_PATH.  The process handle is stored in ``_active_process``.

    Args:
        session_name: The tmux session name to attach to.

    Returns:
        The asyncio.subprocess.Process object for the spawned ttyd.
    """
    global _active_process

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
    )

    # Write PID file (create parent dirs if needed)
    TTYD_PID_DIR.mkdir(parents=True, exist_ok=True)
    TTYD_PID_PATH.write_text(str(proc.pid))

    _active_process = proc
    return proc

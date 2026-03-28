"""
tmux session enumeration and snapshot helpers for the tmux-web muxplex.

In-memory cache:
    _session_list  — most-recently-enumerated list of session names.
    _snapshots     — most-recently-captured pane text, keyed by session name.

Public API:
    get_session_list()                    → list[str]
    get_snapshots()                       → dict[str, str]
    update_session_cache(names, snapshots) → None
    run_tmux(*args)                       → str   (raises RuntimeError on nonzero exit)
    enumerate_sessions()                  → list[str]
    capture_pane(name, lines)             → str
    snapshot_all(names)                   → dict[str, str]
"""

import asyncio

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_session_list: list[str] = []
_snapshots: dict[str, str] = {}


def get_session_list() -> list[str]:
    """Return a copy of the cached session name list."""
    return list(_session_list)


def get_snapshots() -> dict[str, str]:
    """Return a copy of the cached pane-snapshot dict."""
    return dict(_snapshots)


def update_session_cache(names: list[str], snapshots: dict[str, str]) -> None:
    """Replace the in-memory caches with fresh data.

    Sets _session_list to *names* and _snapshots to the provided *snapshots* dict.
    Callers must pass the return value of snapshot_all() as *snapshots*.
    """
    global _session_list, _snapshots
    _session_list = list(names)
    _snapshots = snapshots


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


async def run_tmux(*args: str) -> str:
    """Run `tmux <args>` in a subprocess and return stdout as a string.

    Raises:
        RuntimeError: If the process exits with a nonzero return code.
                      The error message contains the decoded stderr output.
    """
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr_bytes.decode("utf-8", errors="replace"))
    return stdout_bytes.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Session enumeration
# ---------------------------------------------------------------------------


async def enumerate_sessions() -> list[str]:
    """Return the list of currently running tmux session names.

    Calls ``tmux list-sessions -F #{session_name}``, splits on newlines,
    and strips whitespace from each entry.

    Returns [] if tmux is not running (RuntimeError from run_tmux).
    """
    try:
        output = await run_tmux("list-sessions", "-F", "#{session_name}")
    except RuntimeError:
        return []

    names = [line.strip() for line in output.splitlines() if line.strip()]
    return names


# ---------------------------------------------------------------------------
# Pane capture
# ---------------------------------------------------------------------------


async def capture_pane(session_name: str, lines: int = 30) -> str:
    """Capture the last *lines* lines of output from *session_name*.

    Returns the captured text, or '' on any error.
    """
    try:
        return await run_tmux(
            "capture-pane",
            "-p",
            "-t",
            session_name,
            "-S",
            f"-{lines}",
        )
    except RuntimeError:
        return ""


async def snapshot_all(names: list[str]) -> dict[str, str]:
    """Capture all sessions concurrently and return a name→text mapping.

    Uses asyncio.gather with return_exceptions=True so that individual
    failures do not abort the whole batch.  Failed sessions map to ''.

    Note: this function does not mutate module state — it does not update the module cache.
    Callers are responsible for passing the result to update_session_cache.
    """
    if not names:
        return {}
    results = await asyncio.gather(
        *[capture_pane(name) for name in names],
        return_exceptions=True,
    )
    snapshots: dict[str, str] = {}
    for name, result in zip(names, results):
        if isinstance(result, BaseException):
            snapshots[name] = ""
        else:
            snapshots[name] = result
    return snapshots

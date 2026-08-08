"""tmux observation: epoch probe, session enumeration, pane capture.

Moved verbatim from ``sessions.py`` (tmux-lib extraction stage S1, plan
§7.1 -- docs/plans/2026-08-08-tmux-lib-extraction-plan.md). The in-memory
caches move WITH ``enumerate_sessions()`` because it populates them as a
side effect of its one tmux call; splitting function from cache would not
be a pure move.

In-memory cache:
    _session_list  — most-recently-enumerated list of session names.
    _snapshots     — most-recently-captured pane text, keyed by session name.
    _activity      — most-recently-enumerated last-output-activity timestamp
                     (unix epoch seconds), keyed by session name.
    _created       — most-recently-enumerated tmux `#{session_created}`
                     timestamp (unix epoch seconds), keyed by session name.
    _cwds          — most-recently-enumerated tmux `#{pane_current_path}`
                     (the active window's active pane's current working
                     directory), keyed by session name.

Note on _activity/_created: unlike _session_list/_snapshots (which are only
ever swapped together, atomically, via update_session_cache), _activity and
_created are populated directly by enumerate_sessions() as a side effect of
parsing tmux's output. They come from the exact same `tmux list-sessions`
call that produces the name list, so there's no second subprocess round trip
and no consistency dependency on the (separately captured) pane snapshots.
Each call fully replaces both dicts, so entries for sessions that have since
closed are dropped on the next poll, same as the other caches.

`_created` (tmux `#{session_created}`) is intrinsic to the tmux session
itself -- set once, by tmux, at the moment the session was actually created
-- and is therefore the one signal in this module that survives muxplex
restarting, its state.json being deleted, or a fresh install: none of those
events touch tmux's own bookkeeping. This is what lets main.py's poll cycle
distinguish "genuinely just created" from "merely first observed by this
process" when deciding whether to seed a session's bell as needing
attention (see main.py's `_server_start_time` and the "Ensure bell entries"
step of `_run_poll_cycle()`).

Why `#{window_activity}` and not `#{session_activity}`: tmux's session-level
`session_activity` only advances when a *client is attached* to the session
(verified empirically: sending real output to a headless, never-attached
session left `session_activity` frozen at its creation time indefinitely,
while `window_activity` advanced immediately). Since muxplex's whole point
is surfacing sessions producing output *unattended* -- e.g. a build running
in a session nobody has open in a browser tab right now -- `session_activity`
would silently fail to track exactly the sessions this feature most needs to
surface. `window_activity` tracks real pane output regardless of client
attachment. It resolves correctly (matching `list-windows -a` for the same
window) when queried in a `list-sessions -F` context, which implicitly
selects each session's active window -- consistent with capture_pane()
elsewhere in this module, which likewise only ever looks at a session's
active window/pane.
"""

from __future__ import annotations

import asyncio
import logging
import os

from tmux_kit.proc import run_tmux

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_session_list: list[str] = []
_snapshots: dict[str, str] = {}
_activity: dict[str, float] = {}
_created: dict[str, float] = {}
_cwds: dict[str, str] = {}


def get_session_list() -> list[str]:
    """Return a copy of the cached session name list."""
    return list(_session_list)


def get_snapshots() -> dict[str, str]:
    """Return a copy of the cached pane-snapshot dict."""
    return dict(_snapshots)


def get_session_activity() -> dict[str, float]:
    """Return a copy of the cached session-activity dict.

    Values are unix epoch seconds (tmux's `#{window_activity}` for each
    session's active window), the last time the session's pane produced
    output -- tracked regardless of whether a client is currently attached.
    Sessions tmux didn't report an activity value for are simply absent
    from the dict.
    """
    return dict(_activity)


def get_session_created_times() -> dict[str, float]:
    """Return a copy of the cached session-creation-time dict.

    Values are unix epoch seconds (tmux's `#{session_created}`), set once
    by tmux at the moment each session was actually created. Unlike
    `_activity`, this timestamp is intrinsic to the tmux session itself and
    never changes for the life of the session -- it survives muxplex
    restarting, its state.json being deleted, or a fresh install, none of
    which touch tmux's own bookkeeping. Sessions tmux didn't report a
    creation time for are simply absent from the dict.
    """
    return dict(_created)


def get_session_cwds() -> dict[str, str]:
    """Return a copy of the cached session-cwd dict.

    Values are tmux's `#{pane_current_path}` for each session's active
    window's active pane -- the directory the session is (or, for a bare
    shell that has since `cd`'d elsewhere, currently appears to be) running
    from. Observed, not asserted: this is the SAME technique
    `~/dotfiles/bin/amplifier-workspace-snapshot` uses via `/proc/<pid>/cwd`
    (see manifest.py's module docstring for why this observation exists --
    the session-presence manifest's restore-fidelity check). tmux resolves
    `#{pane_current_path}` itself (no `/proc` read needed here); it tracks
    the pane's REAL current directory, so a long-running daemon that never
    `cd`s reports its true root faithfully, while a plain interactive shell
    reports wherever it happens to be right now -- an honest limitation
    manifest.py's restore-fidelity check accounts for explicitly (a typed-
    into shell is not proof of a session's original launch directory, only
    of where it is at observation time). Sessions tmux didn't report a cwd
    for are simply absent from the dict.
    """
    return dict(_cwds)


def update_session_cache(names: list[str], snapshots: dict[str, str]) -> None:
    """Replace the in-memory caches with fresh data.

    Sets _session_list to *names* and _snapshots to the provided *snapshots* dict.
    Callers must pass the return value of snapshot_all() as *snapshots*.
    """
    global _session_list, _snapshots
    _session_list = list(names)
    _snapshots = snapshots


# ---------------------------------------------------------------------------
# tmux-server epoch probe
# ---------------------------------------------------------------------------


async def probe_tmux_epoch() -> dict | None:
    """Identify the tmux server this process is currently talking to.

    This is the discriminator the session-presence manifest (manifest.py)
    uses to tell "muxplex restarted, tmux survived" apart from "the tmux
    server itself died" -- see SESSION_PERSISTENCE_DESIGN.md section 5.1.
    It deliberately does NOT reuse enumerate_sessions(), because that
    function conflates "tmux failed" with "zero sessions" (both return
    ``[]``). This probe distinguishes them cleanly via exit status alone:
    ``tmux display-message`` exits non-zero with "no server running" when
    there is no server, and exits 0 with the requested fields otherwise --
    no parsing of tmux's error text is involved.

    Returns:
        None if no tmux server is currently running (or the probe's own
        socket-file stat races the server disappearing between the tmux
        call and the stat -- treated identically to "no server", per the
        "unknown, not dead" principle: absence of evidence here must never
        be misread as evidence of absence).

        Otherwise a dict identifying the live server::

            {"socket_path": str, "server_pid": int, "inode": int}

        Two epochs are the SAME running server iff all three fields are
        equal:
          - socket_path: catches a different TMUX_TMPDIR (e.g. a scratch
            instance, or a misconfigured tmux_socket_dir) -- a different
            socket is always a different world and must never be compared
            against the recorded epoch as if it were the same server.
          - inode: a new server creates a new socket file even when the
            path is reused, so the same path with a new inode is a new
            server.
          - server_pid: belt-and-braces against inode reuse by the OS.
    """
    try:
        output = await run_tmux("display-message", "-p", "#{pid}\t#{socket_path}")
    except (RuntimeError, FileNotFoundError):
        return None

    line = output.strip()
    if not line:
        return None
    pid_field, _, socket_path = line.partition("\t")
    socket_path = socket_path.strip()
    if not socket_path:
        return None
    try:
        server_pid = int(pid_field.strip())
    except ValueError:
        return None

    try:
        inode = os.stat(socket_path).st_ino
    except OSError:
        # Socket file vanished between the tmux call and the stat (race).
        # Unavailable, not refuted -- treat as "no server" this cycle.
        return None

    return {"socket_path": socket_path, "server_pid": server_pid, "inode": inode}


# ---------------------------------------------------------------------------
# Session enumeration
# ---------------------------------------------------------------------------


async def enumerate_sessions() -> list[str]:
    """Return the list of currently running tmux session names.

    Calls ``tmux list-sessions -F
    #{session_name}<TAB>#{window_activity}<TAB>#{session_created}<TAB>#{pane_current_path}``,
    splits on newlines, and strips whitespace from each entry. As a side
    effect, caches each session's last-activity epoch timestamp (see
    get_session_activity()), its tmux-assigned creation epoch (see
    get_session_created_times()), and its active pane's current working
    directory (see get_session_cwds()) -- all parsed from the same tmux
    call, so no second subprocess round trip is needed just to learn any of
    them.

    Uses `#{window_activity}` (the session's active window), NOT
    `#{session_activity}`: empirically, tmux only advances session_activity
    while a client is attached, so a headless session producing output with
    nobody watching would appear permanently frozen at its creation time.
    window_activity tracks real pane output unconditionally. See the module
    docstring for the full rationale.

    `#{session_created}` is tmux's own record of when the session was
    created -- set once, by tmux, and never revised for the life of the
    session. See get_session_created_times()'s docstring for why that
    intrinsic-to-tmux property matters.

    `#{pane_current_path}` is the active window's active pane's current
    directory -- see get_session_cwds()'s docstring for what this is used
    for (the session-presence manifest's restore-fidelity check) and its
    honest limitations.

    A line with fewer than 3 tabs (unexpected tmux output, or a caller/mock
    still using an older field format) is tolerated: the name is still
    returned, just with no activity/created/cwd entry for the missing
    field(s). A non-numeric activity or created field is dropped and logged
    rather than raising -- one malformed session must not break enumeration
    of the rest. An empty cwd field is simply omitted (not logged -- tmux
    can legitimately report an empty path for a pane in a transient state).

    Returns [] if tmux is not running (RuntimeError from run_tmux).
    """
    try:
        output = await run_tmux(
            "list-sessions",
            "-F",
            "#{session_name}\t#{window_activity}\t#{session_created}\t#{pane_current_path}",
        )
    except (RuntimeError, FileNotFoundError):
        return []

    names: list[str] = []
    activity: dict[str, float] = {}
    created: dict[str, float] = {}
    cwds: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, _, rest = line.partition("\t")
        name = name.strip()
        if not name:
            continue
        names.append(name)
        activity_field, _, rest2 = rest.partition("\t")
        created_field, _, cwd_field = rest2.partition("\t")
        activity_field = activity_field.strip()
        if activity_field:
            try:
                activity[name] = float(activity_field)
            except ValueError:
                _log.warning(
                    "enumerate_sessions: malformed window_activity for %r: %r",
                    name,
                    activity_field,
                )
        cwd_field = cwd_field.strip()
        if cwd_field:
            cwds[name] = cwd_field
        created_field = created_field.strip()
        if created_field:
            try:
                created[name] = float(created_field)
            except ValueError:
                _log.warning(
                    "enumerate_sessions: malformed session_created for %r: %r",
                    name,
                    created_field,
                )

    global _activity, _created, _cwds
    _activity = activity
    _created = created
    _cwds = cwds
    return names


# ---------------------------------------------------------------------------
# Pane capture
# ---------------------------------------------------------------------------

# Default read depth -- unchanged from muxplex's original behavior. Every
# existing caller that doesn't pass `lines` explicitly (the background poll
# cycle's snapshot_all(), and any pre-existing /input read-back) keeps this
# exact shape.
DEFAULT_CAPTURE_LINES = 30

# Upper bound on a caller-controlled `lines` request (GET
# /api/sessions/{name} and POST /api/sessions/{name}/input's `lines` field).
# Callers asking for more than this get a 400, not a silently-clamped
# result -- an unbounded value would let a single request capture arbitrarily
# large scrollback (CPU/memory cost proportional to the request), which is a
# denial-of-service surface against a server the same process also has to
# keep polling ~38 other sessions on.
MAX_CAPTURE_LINES = 2000


async def capture_pane(session_name: str, lines: int = DEFAULT_CAPTURE_LINES) -> str:
    """Capture the last *lines* lines of output from *session_name*.

    Returns the captured text, or '' on any error. *lines* is caller-trusted
    here (bounds enforcement lives at the API boundary in main.py, alongside
    the other /input size caps) -- this function only performs the tmux call.
    """
    try:
        return await run_tmux(
            "capture-pane",
            "-e",  # preserve ANSI escape sequences for color rendering
            "-p",
            "-t",
            session_name,
            "-S",
            f"-{lines}",
        )
    except RuntimeError:
        return ""


# ---------------------------------------------------------------------------
# Scrollback paging (docs/plans/2026-08-07-scrollback-paging-plan.md)
#
# tmux's `capture-pane -S/-E` coordinates are RELATIVE to the current top of
# the visible screen (0 = first visible row, negative = history) -- see the
# module's own `man tmux` entry, confirmed empirically in the plan (\u00a72.1).
# There is no absolute-addressing mode. Converting an absolute row index
# (`before`, defined as `history_size + rel` -- \u00a72.3) into the `-S`/`-E`
# tmux expects therefore REQUIRES knowing the CURRENT `history_size` before
# the `capture-pane` argv can even be built -- and a single tmux invocation
# cannot feed one chained command's output into another's arguments. So
# converting a caller-supplied `before` is necessarily two tmux round trips:
#
#   1. capture_pane_metadata() -- a cheap, capture-free probe for the
#      CURRENT history_size/pane_height/history_limit, used ONLY to convert
#      `before` into a relative `-S`/`-E` pair.
#   2. capture_pane_window(), using the coordinates from (1) -- one atomic
#      invocation that reads history_size/pane_height/history_limit AGAIN,
#      paired in the SAME tmux command loop tick as the actual capture
#      (\u00a72.7). This second, paired reading is what the response's
#      `start`/`total`/`saturated` fields are computed from, so they are
#      always truthful for whatever was actually captured -- never the
#      (marginally staler) value used only to pick the coordinates. history
#      only grows (or pins at saturation), never shrinks, so any drift
#      between (1) and (2) can only shift the returned window towards MORE
#      recent content (\u00a72.4) -- overlap with adjacent pages, never a gap.
#
# The `before=None` (unchanged, legacy) path needs no probe at all: its `-S`
# is the literal `-{lines}` used since before this feature existed, entirely
# independent of history_size.
# ---------------------------------------------------------------------------


async def capture_pane_metadata(session_name: str) -> tuple[int, int, int]:
    """Read *session_name*'s current ``(history_size, pane_height,
    history_limit)`` via one capture-free `display-message` call.

    Used as the probe half of the two-step conversion described in the
    module-level comment above: to turn a caller-supplied absolute `before`
    into tmux's own relative `-S`/`-E` coordinates, the current
    `history_size` must be known BEFORE the `capture-pane` argv can be
    built. Costs nothing beyond a single cheap subprocess spawn -- no
    capture window is requested here at all.

    Raises RuntimeError if tmux/the session is unreachable (same as
    `run_tmux`) -- callers are expected to have already confirmed the
    session exists via `get_session_list()`.
    """
    output = await run_tmux(
        "display-message",
        "-p",
        "-t",
        session_name,
        "#{history_size}\t#{pane_height}\t#{history_limit}",
    )
    h_str, _, rest = output.partition("\t")
    p_str, _, l_str = rest.partition("\t")
    return int(h_str.strip()), int(p_str.strip()), int(l_str.strip())


async def capture_pane_window(
    session_name: str, s: int, e: int | None
) -> tuple[int, int, int, str]:
    """Atomically read ``(history_size, pane_height, history_limit)``
    together with a `capture-pane` window at tmux-relative coordinates
    *s* (`-S`) and *e* (`-E`, omitted entirely when ``None`` -- the
    pre-existing "capture down to the bottom of the visible screen"
    behavior every caller of `capture_pane()` already relies on).

    The two tmux commands are chained with a literal ``;`` argv element
    into ONE subprocess invocation, so they are processed in the same tmux
    server command-loop tick and observe the same grid state (plan \u00a72.7)
    -- there is no race between reading history_size and capturing. This
    is what lets a caller report `start`/`total`/`saturated` truthfully:
    they are computed from the H returned HERE, paired with the capture
    that H actually produced, never a value read moments earlier.

    Returns ``(history_size, pane_height, history_limit, text)``. Raises
    RuntimeError if tmux/the session is unreachable (same as `run_tmux`).
    """
    args = [
        "display-message",
        "-p",
        "-t",
        session_name,
        "#{history_size}\t#{pane_height}\t#{history_limit}",
        ";",
        "capture-pane",
        "-e",  # preserve ANSI escape sequences for color rendering
        "-p",
        "-t",
        session_name,
        "-S",
        str(s),
    ]
    if e is not None:
        args += ["-E", str(e)]
    output = await run_tmux(*args)
    header, _, text = output.partition("\n")
    h_str, _, rest = header.partition("\t")
    p_str, _, l_str = rest.partition("\t")
    return int(h_str.strip()), int(p_str.strip()), int(l_str.strip()), text


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

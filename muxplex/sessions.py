"""
tmux session enumeration and snapshot helpers for the tmux-web muxplex.

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

Public API:
    get_session_list()                    → list[str]
    get_snapshots()                       → dict[str, str]
    get_session_activity()                → dict[str, float]
    get_session_created_times()           → dict[str, float]
    get_session_cwds()                    → dict[str, str]
    update_session_cache(names, snapshots) → None
    run_tmux(*args)                       → str   (raises RuntimeError on nonzero exit)
    enumerate_sessions()                  → list[str]
    capture_pane(name, lines)             → str
    snapshot_all(names)                   → dict[str, str]

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

import asyncio
import logging
import os
import re
import shlex
import shutil

from muxplex.cgroup_escape import should_escape, wrap_shell_argv
from muxplex.settings import find_session_command, load_settings

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session-name validation (security boundary)
# ---------------------------------------------------------------------------

# Canonical allowlist for client-supplied session names. A name that matches
# this pattern contains no shell metacharacters, whitespace, or the tmux target
# separator (`:`), so it is safe to substitute into a shell template
# (create/delete session commands) and safe as a `tmux -t` target.
#
# This is the PRIMARY defense against shell injection via session names. Every
# API endpoint that accepts a client-supplied session name and forwards it to a
# subprocess (create, delete, connect, and any future input endpoint) MUST run
# the name through `is_valid_session_name()` at the boundary, BEFORE any
# substitution or subprocess call.
#
# Charset rationale: tmux forbids `:` in session names (it's the
# session:window.pane target separator), so excluding it costs nothing. All 68
# of the deployment's live session names pass this pattern; it does not reject
# any legitimate existing name.
# The first character MUST be alphanumeric or underscore. This is deliberate and
# security-load-bearing: a leading ``-`` would let a valid name be parsed as an
# OPTION by tmux or by a user-configurable template command (argument injection),
# and ``shlex.quote`` does NOT neutralize that -- quoting stops shell-metacharacter
# interpretation, but a quoted ``-C`` or ``--destroy`` is still a flag to the
# invoked program. Forbidding a leading ``-`` (and leading ``.``/``..`` path
# traversal) closes that class. ``\A...\Z`` (not ``^...$``) is required because
# ``$`` also matches just before a trailing newline, so ``"name\n"`` would slip
# through ``^...$``. All 68 live session names pass this pattern.
SESSION_NAME_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")


def is_valid_session_name(name: str) -> bool:
    """Return True if *name* is a safe session name per ``SESSION_NAME_RE``.

    Safe means: 1-64 chars drawn only from ASCII letters, digits, and the
    ``_ . -`` set, with an alphanumeric-or-underscore FIRST character -- no
    whitespace (including a trailing newline), no shell metacharacters, no
    ``:``, and no leading ``-`` (argument injection) or leading ``.``/``..``
    (path traversal). Callers at the API boundary reject names that fail this
    check with HTTP 400 before the name reaches any subprocess.
    """
    return bool(SESSION_NAME_RE.match(name))


def is_tmux_stable_name(name: str) -> bool:
    """Return True if tmux would create/rename a session to EXACTLY *name*,
    with no silent character mangling.

    ``SESSION_NAME_RE`` (``is_valid_session_name``) permits ``.`` in a
    session name, but tmux 3.4 silently converts ``.`` to ``_`` at
    creation/rename time -- verified empirically against a real, isolated
    tmux server (see docs/plans/2026-08-07-session-rename-plan.md \u00a71): a
    session named via ``build.js`` actually comes out as ``build_js``, with
    exit code 0 and no error. That gap is the entire mangling problem: a
    caller requesting ``build.js`` cannot tell from the response alone that
    it got ``build_js`` instead.

    This predicate lets a caller REJECT such a request outright rather than
    predict tmux's mangling rule. Rejecting is deliberately preferred over
    modeling the substitution (``requested.replace(".", "_")``): a wrong
    prediction produces a wrong collision check and a silently mis-keyed
    session, whereas over-rejecting on a hypothetical tmux that would not
    mangle the name costs the caller one retry.

    Requires *name* to ALSO pass ``is_valid_session_name`` first -- this is
    an ADDITIONAL, stricter check for names that must survive tmux
    unchanged (currently: ``POST /api/sessions/{name}/rename``'s
    ``new_name``), not a replacement for the charset boundary every
    session-name-accepting endpoint already enforces via
    ``is_valid_session_name``. Deliberately NOT applied to the create path
    (``POST /api/sessions``) -- that is a separate, pre-existing, and
    breaking fix left for the owner to decide on its own (see the rename
    plan \u00a73/\u00a715).
    """
    return is_valid_session_name(name) and "." not in name


async def rename_tmux_session(old_name: str, new_name: str) -> None:
    """Run ``tmux rename-session -t =<old_name> -- <new_name>`` (argv, no
    shell).

    Raises RuntimeError (via ``run_tmux`` -- tmux's own stderr, e.g.
    ``duplicate session: <new_name>``) if tmux refuses, notably rc=1 when
    *new_name* is already a live session.

    Uses ``=<old_name>`` -- tmux's EXACT-match target form, verified live
    to work for ``rename-session`` (unlike a ``send-keys`` pane target;
    see ``terminal_input.session_target``'s docstring) -- plus ``--``
    end-of-options, giving this call a STRONGER targeting guarantee than
    ``/input`` achieves: tmux resolves an exact-match target before any
    prefix matching, so this cannot land on a differently-named neighbour.
    This is the first session-lifecycle subprocess with no shell path at
    all -- callers still validate both names first
    (``is_valid_session_name`` / ``is_tmux_stable_name``), same discipline
    as every other tmux-touching endpoint.

    tmux reports rc=0 even when it silently mangles the resulting name
    (see ``is_tmux_stable_name``'s docstring) -- callers MUST re-enumerate
    and verify the observed name after this call succeeds; this function
    only reports whether tmux accepted the request, never what the
    session ended up named.
    """
    await run_tmux("rename-session", "-t", f"={old_name}", "--", new_name)


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
# Subprocess helpers
# ---------------------------------------------------------------------------


def tmux_env() -> dict[str, str] | None:
    """Build the environment for tmux subprocess calls, honoring `tmux_socket_dir`.

    A systemd/launchd service does NOT inherit the user's interactive login
    shell environment. If the user sets TMUX_TMPDIR in their shell rc (common
    when keeping sockets out of the shared, world-writable /tmp), the muxplex
    *service* process never sees it -- tmux silently falls back to its
    compiled-in default (/tmp/tmux-$UID) and every real session becomes
    invisible to muxplex, even though `tmux list-sessions` works fine when
    run interactively by the same user.

    Returns:
        None if `tmux_socket_dir` is unset/empty -- callers should pass
        `env=None` to the subprocess call, inheriting the process's own
        environment unchanged (fully backward compatible).
        Otherwise, a copy of `os.environ` with `TMUX_TMPDIR` overridden to
        the configured directory. Copying (not replacing) preserves PATH,
        HOME, and everything else the subprocess needs.

        Also removes `TMUX` from the returned environment. tmux gives `$TMUX`
        (set whenever a process is a descendant of an *attached* tmux client)
        priority over `TMUX_TMPDIR` when resolving which server socket to
        talk to -- if it were left in place, a muxplex process that happens
        to be a descendant of some other tmux client (e.g. started manually
        from inside a tmux pane while debugging) would silently ignore this
        override and keep talking to that other server. The muxplex *service*
        itself is never an attached tmux client, so this is a no-op in the
        normal (systemd/launchd) deployment -- it only matters for robustness
        in atypical invocation contexts.
    """
    tmpdir = load_settings().get("tmux_socket_dir", "")
    if not tmpdir:
        return None
    env = dict(os.environ)
    env["TMUX_TMPDIR"] = tmpdir
    env.pop("TMUX", None)
    return env


async def run_tmux(*args: str) -> str:
    """Run `tmux <args>` in a subprocess and return stdout as a string.

    Honors the `tmux_socket_dir` setting (see `tmux_env()`) so tmux looks in
    the configured socket directory instead of always defaulting to
    /tmp/tmux-$UID.

    Raises:
        RuntimeError: If the process exits with a nonzero return code.
                      The error message contains the decoded stderr output.
    """
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=tmux_env(),
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr_bytes.decode("utf-8", errors="replace"))
    return stdout_bytes.decode("utf-8", errors="replace")


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


async def spawn_session_command(
    name: str, command_id: str | None = None
) -> tuple[bool, str | None]:
    """Run the resolved pair's `new_session_template` (with `{name}`
    substituted) to create a tmux session named *name*. Returns ``(ok, error)``.

    This is the SINGLE source of truth for "how to create a session" --
    extracted from main.py's `create_session()` API handler so that both the
    API endpoint and `muxplex restore` (which needs to create sessions from
    the CLI, not the running server) share one implementation rather than two
    that could drift (see SESSION_PERSISTENCE_DESIGN.md's "restore fidelity
    equals create fidelity" principle). Callers at the API boundary MUST
    validate the name first (`is_valid_session_name` / the API's
    `_require_valid_session_name`) -- this function does not, so it stays
    usable from a plain CLI process with no HTTP framework in scope.

    *command_id* selects a configured session command pair (see
    settings.resolve_session_commands / find_session_command).
    ``command_id=None`` selects the reserved ``"default"`` pair -- i.e.
    ``settings.new_session_template`` -- so this is byte-identical to
    pre-feature behavior for every existing caller. An unresolvable
    *command_id* returns ``(False, <message>)`` WITHOUT spawning anything --
    this is defense-in-depth, not the primary gate: `create_session()`
    validates first so it can return a 400 with the available-ids list; this
    function is also called from `restore.py`, which has no HTTP boundary.

    The resolved pair's `new_session_template` is an arbitrary user shell
    command with a `{name}` placeholder (default `tmux new-session -d -s
    {name}`, but users configure e.g. `amplifier-workspace {name}`), so this
    stays shell-based to preserve that feature. Injection is closed by two
    layers: (1) the caller's allowlist check guarantees the name has no
    shell metacharacters; (2) `shlex.quote()` here is defense-in-depth in
    case the allowlist is ever loosened -- for an allowlist-valid name it's
    a no-op.

    Some session commands (e.g. `amplifier-workspace`) create the tmux
    session and then attempt to *attach* to it, which requires a TTY. When
    launched with no TTY available (the muxplex service, or a non-interactive
    CLI invocation) the attach step fails with a non-zero exit code even
    though the session was successfully created. To handle this, when the
    command exits non-zero we check whether a tmux session with the
    requested name now exists -- if it does, we treat it as a success. This
    is unchanged and load-bearing for non-default pairs too: the exemplar
    non-default pair (`amplifier-workspace`) is the very command whose
    behavior this branch exists for.

    Returns:
        (True, None) on success.
        (False, <error message>) on failure -- the caller decides how to
        surface it (HTTPException for the API, a printed FAIL line for the
        CLI).
    """
    settings = load_settings()
    command = find_session_command(command_id, settings)
    if command is None:
        return False, (
            f"Unknown command_id {command_id!r}: no such configured session command."
        )
    template = command["new_session_template"]

    # Pre-flight: check that the base command is on PATH.
    base_cmd = template.split()[0] if template.strip() else ""
    if base_cmd and not shutil.which(base_cmd):
        _log.error(
            "Session command binary not found on PATH: %r (PATH=%s)",
            base_cmd,
            os.environ.get("PATH", ""),
        )
        return False, (
            f"Command not found: {base_cmd}. "
            "Ensure it is installed and in the server's PATH."
        )

    command = template.replace("{name}", shlex.quote(name))
    _log.info("Creating session '%s' with command: %s", name, command)
    try:
        # This command may start a brand-new tmux SERVER (e.g. the default
        # template `tmux new-session -d -s {name}`, or a user's own
        # `amplifier-workspace {name}`, both start one if none is running
        # yet). If we are running under a systemd --user unit, that server
        # must NOT be spawned as a plain child of this process -- see
        # cgroup_escape.py's module docstring and AGENTS.md's "Two ways to
        # destroy every live tmux session on this host" (mechanism #1).
        if await should_escape():
            proc = await asyncio.create_subprocess_exec(
                *wrap_shell_argv(command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=tmux_env(),
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=tmux_env(),
            )
        _stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=30
        )
        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            # Some commands (amplifier-workspace) create the session then
            # try to attach (which fails without a TTY). If the session
            # exists despite the non-zero exit, treat it as success.
            sessions = await enumerate_sessions()
            if name in sessions:
                _log.info(
                    "Session command exited %d but session '%s' exists -- "
                    "treating as success (likely a TTY-attach failure)",
                    proc.returncode,
                    name,
                )
            else:
                _log.warning(
                    "Session command exited %d: %s (stderr: %s)",
                    proc.returncode,
                    command,
                    stderr_text,
                )
                return False, (
                    f"Session command failed (exit {proc.returncode}): {stderr_text}"
                    if stderr_text
                    else f"Session command failed with exit code {proc.returncode}"
                )
    except asyncio.TimeoutError:
        _log.info(
            "Session command still running after 30s (may be long-lived): %s",
            command,
        )
        # Long-running session commands (e.g. amplifier-workspace that
        # spawns background processes) may outlive the 30s window. This is
        # not necessarily an error -- return success and let the caller
        # poll for the session to appear.
    except Exception as exc:
        _log.warning("Failed to launch session command %r: %s", command, exc)
        return False, f"Failed to launch command: {exc}"

    return True, None


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

"""
tmux session enumeration and snapshot helpers for the tmux-web muxplex.

In-memory cache:
    _session_list  — most-recently-enumerated list of session names.
    _snapshots     — most-recently-captured pane text, keyed by session name.
    _activity      — most-recently-enumerated last-output-activity timestamp
                     (unix epoch seconds), keyed by session name.

Public API:
    get_session_list()                    → list[str]
    get_snapshots()                       → dict[str, str]
    get_session_activity()                → dict[str, float]
    update_session_cache(names, snapshots) → None
    run_tmux(*args)                       → str   (raises RuntimeError on nonzero exit)
    enumerate_sessions()                  → list[str]
    capture_pane(name, lines)             → str
    snapshot_all(names)                   → dict[str, str]

Note on _activity: unlike _session_list/_snapshots (which are only ever
swapped together, atomically, via update_session_cache), _activity is
populated directly by enumerate_sessions() as a side effect of parsing
tmux's output. It comes from the exact same `tmux list-sessions` call that
produces the name list, so there's no second subprocess round trip and no
consistency dependency on the (separately captured) pane snapshots. Each
call fully replaces _activity, so entries for sessions that have since
closed are dropped on the next poll, same as the other caches.

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


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_session_list: list[str] = []
_snapshots: dict[str, str] = {}
_activity: dict[str, float] = {}


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

    Calls ``tmux list-sessions -F #{session_name}<TAB>#{window_activity}``,
    splits on newlines, and strips whitespace from each entry. As a side
    effect, caches each session's last-activity epoch timestamp (see
    get_session_activity()) -- parsed from the same tmux call, so no second
    subprocess round trip is needed just to learn activity times.

    Uses `#{window_activity}` (the session's active window), NOT
    `#{session_activity}`: empirically, tmux only advances session_activity
    while a client is attached, so a headless session producing output with
    nobody watching would appear permanently frozen at its creation time.
    window_activity tracks real pane output unconditionally. See the module
    docstring for the full rationale.

    A line with no tab (unexpected tmux output, or a caller/mock still using
    the old single-field format) is tolerated: the name is still returned,
    just with no activity entry. A non-numeric activity field is dropped and
    logged rather than raising -- one malformed session must not break
    enumeration of the rest.

    Returns [] if tmux is not running (RuntimeError from run_tmux).
    """
    try:
        output = await run_tmux(
            "list-sessions", "-F", "#{session_name}\t#{window_activity}"
        )
    except (RuntimeError, FileNotFoundError):
        return []

    names: list[str] = []
    activity: dict[str, float] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, _, activity_field = line.partition("\t")
        name = name.strip()
        if not name:
            continue
        names.append(name)
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

    global _activity
    _activity = activity
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

# tmux `history-limit` applied to every session muxplex creates (see
# ensure_history_retention()). Deliberately set well above MAX_CAPTURE_LINES:
# tmux's own compiled-in default is 2000 lines, and a host's ~/.tmux.conf may
# set it lower still -- if a caller's max-depth request (2000 lines) landed
# on a session whose retained scrollback was smaller than that, tmux would
# silently return fewer lines than asked for, which would be a worse lie
# than the original 30-line ceiling this fix replaces (an explicit ceiling
# you can raise vs. an invisible one you can't). Setting this explicitly per
# session decouples the read-depth contract from whatever tmux.conf happens
# to be on the host.
SESSION_HISTORY_LIMIT = 5000


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


async def ensure_history_retention(session_name: str) -> None:
    """Raise *session_name*'s tmux `history-limit` to SESSION_HISTORY_LIMIT.

    Called once, right after a new session is confirmed to exist (see
    main.py's create_session()). Best-effort: a failure (e.g. the session
    vanished between creation and this call, or tmux is momentarily
    unavailable) is logged and swallowed -- this is a scrollback-depth
    improvement, not a correctness requirement, and must never fail session
    creation itself.
    """
    try:
        await run_tmux(
            "set-option",
            "-t",
            session_name,
            "history-limit",
            str(SESSION_HISTORY_LIMIT),
        )
    except RuntimeError as exc:
        _log.warning(
            "ensure_history_retention: failed to set history-limit for %r: %s",
            session_name,
            exc,
        )


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

    # Raise this session's tmux history-limit so a later deep read has real
    # scrollback to return instead of silently truncating. Best-effort:
    # never fails session creation itself.
    await ensure_history_retention(name)
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

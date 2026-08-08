"""
tmux session helpers for the tmux-web muxplex.

Tmux-lib extraction stage S1 (plan §7.1 --
docs/plans/2026-08-08-tmux-lib-extraction-plan.md): the pure tmux code that
used to live here moved into the ``muxplex/tmux/`` library boundary and is
re-exported below, so every existing import path keeps working untouched:

    muxplex.tmux.proc     -- run_tmux(), tmux_env()
    muxplex.tmux.names    -- SESSION_NAME_RE, is_valid_session_name,
                             is_tmux_stable_name, rename_tmux_session
    muxplex.tmux.observe  -- probe_tmux_epoch, enumerate_sessions,
                             capture_pane / capture_pane_metadata /
                             capture_pane_window / snapshot_all, the
                             DEFAULT_CAPTURE_LINES / MAX_CAPTURE_LINES caps,
                             and the in-memory observation caches
                             (get_session_list / get_snapshots /
                             get_session_activity /
                             get_session_created_times / get_session_cwds /
                             update_session_cache)

What stays HERE (app-side): ``spawn_session_command()`` -- it resolves a
muxplex settings template (``session_commands`` /
``new_session_template``), which is exactly the coupling stage S2 inverts
before the spawn's general half (cgroup-escaped spawn, the
exists-despite-exit-code TTY-attach tolerance) can move (plan §15.1).
"""

import asyncio
import logging
import os
import shlex
import shutil

from muxplex.settings import find_session_command, load_settings
from muxplex.tmux.cgroup import should_escape, wrap_shell_argv
from muxplex.tmux.names import (
    SESSION_NAME_RE,
    is_tmux_stable_name,
    is_valid_session_name,
    rename_tmux_session,
)
from muxplex.tmux.observe import (
    DEFAULT_CAPTURE_LINES,
    MAX_CAPTURE_LINES,
    capture_pane,
    capture_pane_metadata,
    capture_pane_window,
    enumerate_sessions,
    get_session_activity,
    get_session_created_times,
    get_session_cwds,
    get_session_list,
    get_snapshots,
    probe_tmux_epoch,
    snapshot_all,
    update_session_cache,
)
from muxplex.tmux.proc import run_tmux, tmux_env

__all__ = [
    "DEFAULT_CAPTURE_LINES",
    "MAX_CAPTURE_LINES",
    "SESSION_NAME_RE",
    "capture_pane",
    "capture_pane_metadata",
    "capture_pane_window",
    "enumerate_sessions",
    "get_session_activity",
    "get_session_created_times",
    "get_session_cwds",
    "get_session_list",
    "get_snapshots",
    "is_tmux_stable_name",
    "is_valid_session_name",
    "probe_tmux_epoch",
    "rename_tmux_session",
    "run_tmux",
    "snapshot_all",
    "spawn_session_command",
    "tmux_env",
    "update_session_cache",
]

_log = logging.getLogger(__name__)


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
        # muxplex/tmux/cgroup.py's module docstring and AGENTS.md's "Two
        # ways to destroy every live tmux session on this host" (mechanism #1).
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

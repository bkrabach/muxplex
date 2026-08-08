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

Stage S2 (plan §13.2 stage 3, §4.3) inverted the settings dependency:
configuration is INJECTED into the library, never read by it. This module
is where muxplex does the injecting -- it is the app-side facade:

- ``tmux_env()`` (no args, the pre-S2 signature every app caller keeps
  using) resolves ``tmux_socket_dir`` from muxplex's settings FRESH on
  every call and passes it into the library's pure
  ``muxplex.tmux.proc.tmux_env(socket_dir)``.
- The same resolver is installed as the library's process-wide env
  factory (``set_env_factory``, at import time below), so every
  ``run_tmux()`` call made from INSIDE the library -- enumeration,
  capture, bells, rename -- honors the setting exactly as before the
  inversion.
- ``spawn_session_command()`` resolves WHICH template to run
  (``session_commands`` / ``new_session_template`` -- muxplex config) and
  delegates the general half (cgroup-escaped spawn, the
  exists-despite-exit-code TTY-attach tolerance) to the library's
  ``muxplex.tmux.spawn.spawn_session(name, template, env=...)`` with the
  template caller-resolved (plan §15.1).
"""

import logging

from muxplex.settings import find_session_command, load_settings
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
from muxplex.tmux.proc import run_tmux, set_env_factory
from muxplex.tmux.proc import tmux_env as _lib_tmux_env
from muxplex.tmux.spawn import spawn_session

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

    Stage S2 (plan §13.2 stage 3): this function is now the APP HALF only --
    it resolves WHICH template to run (muxplex settings) and injects the
    resolved template plus the settings-resolved subprocess environment into
    the library's ``muxplex.tmux.spawn.spawn_session()``, which owns the
    general half (PATH pre-flight, ``shlex.quote()`` substitution, cgroup
    escape, the TTY-attach and 30s tolerances). Behavior is byte-identical
    for every caller.

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
    return await spawn_session(name, command["new_session_template"], env=tmux_env())


def _tmux_socket_dir_from_settings() -> str:
    """Resolve muxplex's `tmux_socket_dir` setting, FRESH on every call.

    Same cadence as the pre-S2 read that lived inside the library: a
    settings edit takes effect on the next tmux call, no restart required.
    Resolved via this module's `load_settings` binding so existing test
    seams (`patch("muxplex.sessions.load_settings", ...)`) keep working.
    """
    return load_settings().get("tmux_socket_dir", "")


def tmux_env() -> dict[str, str] | None:
    """App-side facade keeping the pre-S2 zero-arg signature.

    Stage S2 (plan §4.3) made the library's `tmux_env(socket_dir)` a pure
    function of an INJECTED socket dir. muxplex's injection lives here:
    resolve `tmux_socket_dir` from settings and pass it in. Every app
    caller (`main.py`'s delete path, `ttyd.spawn_ttyd`, and this module's
    spawn) keeps calling plain `tmux_env()` exactly as before, with
    byte-identical results -- see the library docstring
    (`muxplex.tmux.proc.tmux_env`) for the systemd-environment semantics.
    """
    return _lib_tmux_env(_tmux_socket_dir_from_settings())


# Install muxplex's env factory into the library (plan §4.3: configuration
# is injected, never read). This is the one-time, construction-time wiring
# that lets every run_tmux() call made from INSIDE the library
# (enumeration, capture, bells, rename) keep honoring `tmux_socket_dir`
# without the library ever knowing muxplex's settings file exists. Every
# app entry point (main.py, restore.py, ttyd.py, cli.py) imports this
# module, so the factory is always installed before any tmux call.
set_env_factory(tmux_env)

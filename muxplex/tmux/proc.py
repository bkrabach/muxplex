"""tmux subprocess plumbing: ``run_tmux()`` and ``tmux_env()``.

Moved verbatim from ``sessions.py`` at stage S1; stage S2 (plan §13.2
stage 3 -- docs/plans/2026-08-08-tmux-lib-extraction-plan.md) inverted the
one wrong-way import arrow this module carried: it used to read muxplex's
settings file (``load_settings()`` -- ``sessions.py:294`` pre-move). Per
plan §4.3, **configuration is injected, never read**: this module has no
idea any settings file exists.

Two injection points, both owned by the host application:

- ``tmux_env(socket_dir)`` is now a PURE function of its parameter (plus
  ``os.environ``): the caller resolves the socket directory from wherever
  its config lives and passes the value in.
- ``set_env_factory(factory)`` installs a process-wide callable the app
  registers once at construction time (muxplex does so in ``sessions.py``,
  its app-side facade). Every ``run_tmux()`` call that is not given an
  explicit ``env=`` resolves the subprocess environment from that factory
  AT CALL TIME -- preserving the pre-S2 semantics exactly, where every
  tmux invocation re-read the setting fresh. With no factory installed
  (a bare library consumer that wants the ambient environment, or a unit
  test), the default is ``None`` -- inherit the process environment
  unchanged, which is also what the pre-S2 code did when
  ``tmux_socket_dir`` was unset.

Every tmux invocation in the package goes through ``run_tmux()``; this
module is the one door (plan §1.2).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

# The app-installed environment factory (plan §4.3). ``None`` means "no
# override installed": subprocesses inherit this process's environment,
# byte-identical to the pre-S2 behavior with `tmux_socket_dir` unset.
_env_factory: Callable[[], dict[str, str] | None] | None = None

# Sentinel distinguishing "caller passed no env" from the meaningful
# ``env=None`` ("inherit the ambient environment, explicitly").
_UNSET: object = object()


def set_env_factory(factory: Callable[[], dict[str, str] | None] | None) -> None:
    """Install the host application's subprocess-environment factory.

    The factory is called on EVERY ``run_tmux()`` invocation that does not
    pass an explicit ``env=``, so config re-resolution stays per-call (the
    pre-inversion cadence -- a settings edit takes effect on the next tmux
    call, no restart required). muxplex installs
    ``tmux_env(<socket dir from its settings>)`` here; a second app
    installs its own. Pass ``None`` to uninstall.
    """
    global _env_factory
    _env_factory = factory


def default_env() -> dict[str, str] | None:
    """Resolve the subprocess environment from the injected factory.

    Returns ``None`` (inherit this process's environment unchanged) when no
    factory is installed.
    """
    return _env_factory() if _env_factory is not None else None


def tmux_env(socket_dir: str | None) -> dict[str, str] | None:
    """Build the environment for tmux subprocess calls, honoring *socket_dir*.

    S2 inversion (plan §4.3, §7.1): the socket directory is PASSED IN by
    the caller -- this function no longer reads any configuration. muxplex
    resolves its ``tmux_socket_dir`` setting app-side and injects it here.

    Why the override exists at all: a systemd/launchd service does NOT
    inherit the user's interactive login shell environment. If the user
    sets TMUX_TMPDIR in their shell rc (common when keeping sockets out of
    the shared, world-writable /tmp), the muxplex *service* process never
    sees it -- tmux silently falls back to its compiled-in default
    (/tmp/tmux-$UID) and every real session becomes invisible to muxplex,
    even though `tmux list-sessions` works fine when run interactively by
    the same user.

    Returns:
        None if *socket_dir* is unset/empty -- callers should pass
        `env=None` to the subprocess call, inheriting the process's own
        environment unchanged (fully backward compatible).
        Otherwise, a copy of `os.environ` with `TMUX_TMPDIR` overridden to
        the given directory. Copying (not replacing) preserves PATH,
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
    if not socket_dir:
        return None
    env = dict(os.environ)
    env["TMUX_TMPDIR"] = socket_dir
    env.pop("TMUX", None)
    return env


async def run_tmux(*args: str, env: dict[str, str] | None | object = _UNSET) -> str:
    """Run `tmux <args>` in a subprocess and return stdout as a string.

    The subprocess environment is INJECTED config (plan §4.3): pass it
    explicitly via ``env=`` (e.g. the result of ``tmux_env(socket_dir)``),
    or omit it to use the app-installed factory (``set_env_factory``),
    which is how the host's socket-dir override reaches every tmux call
    made from inside the library. ``env=None`` explicitly inherits this
    process's environment.

    Raises:
        RuntimeError: If the process exits with a nonzero return code.
                      The error message contains the decoded stderr output.
    """
    if env is _UNSET:
        env = default_env()
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,  # type: ignore[arg-type]
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr_bytes.decode("utf-8", errors="replace"))
    return stdout_bytes.decode("utf-8", errors="replace")

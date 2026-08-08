"""Session spawning: the general half of "create a tmux session from a
shell-command template".

Split out of ``sessions.spawn_session_command()`` at stage S2 (plan §13.2
stage 3, §15.1 -- docs/plans/2026-08-08-tmux-lib-extraction-plan.md). The
original plan left the whole spawn path app-side because it read muxplex's
settings (``sessions.py:691`` pre-move); the S2 inversion makes the
TEMPLATE CALLER-RESOLVED, which frees the general half -- the PATH
pre-flight, the ``{name}`` substitution with ``shlex.quote()``
defense-in-depth, the cgroup escape (the 44-session incident, see
``tmux/cgroup.py``), the 30s long-lived-command tolerance, and the
exists-despite-nonzero-exit TTY-attach tolerance -- to live behind the
library boundary, where a second app creating sessions needs it and must
not rediscover it.

What deliberately stays app-side (plan §4.3: configuration is injected,
never read): resolving WHICH template to run. muxplex resolves its
``session_commands`` / ``new_session_template`` settings in
``sessions.spawn_session_command()`` and passes the resolved template
string in; a second app resolves its own config its own way.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil

from tmuxkit.cgroup import should_escape, wrap_shell_argv
from tmuxkit.observe import enumerate_sessions

_log = logging.getLogger(__name__)


async def spawn_session(
    name: str, template: str, *, env: dict[str, str] | None = None
) -> tuple[bool, str | None]:
    """Run *template* (with ``{name}`` substituted) to create a tmux session
    named *name*. Returns ``(ok, error)``.

    *template* is a CALLER-RESOLVED arbitrary user shell command with a
    ``{name}`` placeholder (e.g. ``tmux new-session -d -s {name}``, or a
    user's own ``amplifier-workspace {name}``), so this stays shell-based to
    preserve that feature. Injection is closed by two layers: (1) the
    caller's name validation/allowlist guarantees the name has no shell
    metacharacters; (2) ``shlex.quote()`` here is defense-in-depth in case
    that is ever loosened -- for an allowlist-valid name it's a no-op.
    Callers at an API boundary MUST validate the name first
    (``is_valid_session_name``) -- this function does not, so it stays
    usable from a plain CLI process with no HTTP framework in scope.

    *env* is INJECTED config (plan §4.3): the subprocess environment for the
    spawn (muxplex passes its settings-resolved ``tmux_env()``). ``None``
    inherits this process's environment unchanged.

    The command may start a brand-new tmux SERVER (both the default template
    and e.g. ``amplifier-workspace`` start one if none is running yet). If we
    are running under a systemd --user unit, that server must NOT be spawned
    as a plain child of this process -- see ``lib/tmuxkit/cgroup.py``'s
    module docstring and AGENTS.md's "Two ways to destroy every live tmux
    session on this host" (mechanism #1).

    Some session commands (e.g. ``amplifier-workspace``) create the tmux
    session and then attempt to *attach* to it, which requires a TTY. When
    launched with no TTY available (a service process, or a non-interactive
    CLI invocation) the attach step fails with a non-zero exit code even
    though the session was successfully created. To handle this, when the
    command exits non-zero we check whether a tmux session with the
    requested name now exists -- if it does, we treat it as a success.

    Returns:
        (True, None) on success.
        (False, <error message>) on failure -- the caller decides how to
        surface it (HTTPException for an API, a printed FAIL line for a
        CLI).
    """
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
        if await should_escape():
            proc = await asyncio.create_subprocess_exec(
                *wrap_shell_argv(command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
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

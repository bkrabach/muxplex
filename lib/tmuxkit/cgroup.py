"""
Keep a newly-forked tmux SERVER process out of muxplex's own cgroup.

Background -- read `AGENTS.md`'s "Two ways to destroy every live tmux
session on this host" (mechanism #1) before touching this file.

When muxplex runs as a systemd --user unit (``muxplex.service``) and one of
its subprocesses forks a brand-new tmux *server* (this happens inside
``tmux/spawn.py``'s ``spawn_session()``: its caller-resolved template command
-- e.g. ``tmux new-session -d -s {name}`` or a user's own
``amplifier-workspace {name}`` -- starts a tmux server if none is running
yet), that server inherits ``muxplex.service``'s cgroup. systemd's default
``KillMode`` (``control-group``, and the less aggressive ``mixed``) SIGKILLs
every process still in a unit's cgroup on stop/restart -- so a routine
``systemctl --user restart muxplex.service`` becomes a mass kill of every
live tmux session on the host. This destroyed 44 sessions on 2026-07-29.

``KillMode=process`` (shipped in the unit template since v0.24.0) is a guard
on the unit's OWN kill behavior, not a fix for the parent/child relationship
itself -- anyone who hand-edits their unit, or supervises muxplex with a
different tool/KillMode, re-arms the hazard. This module is the actual fix:
it keeps the tmux server (and its descendants) out of muxplex's cgroup in
the first place, so the unit's KillMode stops mattering for this hazard.

``setsid`` / ``start_new_session=True`` does NOT achieve this. cgroup
membership is inherited across ``fork()`` and is entirely unaffected by
``setsid()``, which creates a new *session/process group* -- a different
kernel concept from a cgroup. Proof already in this repo: ``ttyd.py``
passes ``start_new_session=True``, and the tmux server ttyd's ``tmux
attach`` parented was still sitting in ``muxplex.service``'s cgroup when it
was SIGKILLed on 2026-07-29. Only an EXPLICIT cgroup move escapes --
``systemd-run --user --scope`` (used here), or writing a PID into a
different cgroup's ``cgroup.procs``.

Three environments, three behaviors -- see ``should_escape()``:

* Linux with a usable systemd --user session: wrap the subprocess in a
  transient scope of its own (``systemd-run --user --scope``). This is
  the fix, and it applies whenever a usable session exists -- regardless
  of whether muxplex itself happens to be running under a systemd unit
  right now, because the escaped tmux server should never depend on how
  *this* process was started.
* Linux without a usable systemd --user session (e.g. muxplex run as
  root via a plain boot script with no systemd unit at all, such as this
  project's `tower` host) -- there is no service cgroup to escape from,
  so nothing is needed.
* Any non-Linux platform (macOS/launchd) -- cgroups do not exist on that
  platform, so the hazard itself does not exist.

The one thing this module refuses to do silently: detect that escape looks
POSSIBLE (a systemd --user session appears present) and have the actual
mechanism fail anyway (dbus down, no user manager reachable, etc). That
combination is logged at CRITICAL, exactly once per process, and is never
swallowed -- the caller still gets a working (unprotected) subprocess so a
transient systemd hiccup does not block session creation outright, but the
operator is told, unambiguously, that this session's tmux server (if a new
one was needed) is not protected against a future service restart.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from typing import Literal

_log = logging.getLogger(__name__)

EnvironmentMode = Literal["scope-candidate", "not-applicable"]

# Cached across the process lifetime -- see should_escape()'s docstring for
# why a one-time probe (rather than a probe per session-creation call) is
# the right cost/benefit tradeoff here.
_probed_available: bool | None = None


def environment_mode() -> EnvironmentMode:
    """Cheap, synchronous, static classification of this host/process.

    Does NOT attempt to actually run anything -- this only inspects the
    environment for the preconditions a usable ``systemd --user`` session
    requires. ``should_escape()`` layers a real (but cached) capability
    probe on top of this for the "looks available but isn't" case.
    """
    if sys.platform != "linux":
        # macOS (launchd) and anything else: no cgroups exist on this
        # platform at all, so the hazard this module exists for cannot
        # occur here.
        return "not-applicable"
    if not os.environ.get("XDG_RUNTIME_DIR"):
        # No usable systemd --user session -- e.g. this project's `tower`
        # host, which runs muxplex as root from a plain boot script with
        # no systemd unit at all. There is no service cgroup to escape
        # from, so there is nothing to do.
        return "not-applicable"
    if not shutil.which("systemd-run"):
        return "not-applicable"
    return "scope-candidate"


async def _probe_scope_available() -> bool:
    """Real (but cheap, one-time) capability check: can we actually get
    ``systemd-run --user --scope`` to run something?

    Only ever called when ``environment_mode()`` already says
    "scope-candidate" -- this exists purely to catch the case the static
    heuristic cannot see: a systemd --user session that *looks* present
    (``XDG_RUNTIME_DIR`` set, ``systemd-run`` on PATH) but cannot actually
    be reached (e.g. the user's systemd instance/dbus isn't up). A failure
    here is exactly the "needed but unavailable" case this module must
    never handle silently.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            "--",
            "true",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=5)
    except (OSError, asyncio.TimeoutError) as exc:
        _log.critical(
            "cgroup escape self-test FAILED to even launch (%s). A systemd "
            "--user session looked available (XDG_RUNTIME_DIR set, "
            "systemd-run on PATH) but could not be used. Tmux servers this "
            "process spawns will NOT be moved out of muxplex's own cgroup -- "
            "a future `systemctl restart`/`stop` of this unit could "
            "mass-kill live tmux sessions again, exactly as on 2026-07-29. "
            "See AGENTS.md 'Two ways to destroy every live tmux session on "
            "this host'.",
            exc,
        )
        return False

    if proc.returncode != 0:
        _log.critical(
            "cgroup escape self-test FAILED (systemd --user session looked "
            "available but 'systemd-run --user --scope' exited %d: %s). "
            "Tmux servers this process spawns will NOT be moved out of "
            "muxplex's own cgroup -- a future `systemctl restart`/`stop` of "
            "this unit could mass-kill live tmux sessions again, exactly as "
            "on 2026-07-29. See AGENTS.md 'Two ways to destroy every live "
            "tmux session on this host'.",
            proc.returncode,
            stderr_bytes.decode("utf-8", errors="replace").strip(),
        )
        return False

    _log.info(
        "cgroup escape available: tmux-server-spawning subprocesses will "
        "run in their own transient systemd --user scope, isolated from "
        "this unit's cgroup."
    )
    return True


async def should_escape() -> bool:
    """Whether subprocess spawns that might parent a tmux server should be
    wrapped via ``wrap_exec_argv`` / ``wrap_shell_argv``.

    Combines the cheap static check (``environment_mode()``) with a real,
    one-time capability probe, cached for the life of the process (a
    module-level flag, not re-checked per call) -- session creation and
    ttyd spawn happen far too often to pay a live ``systemd-run`` round
    trip on every single one just to re-confirm what was already proven
    true or false. If systemd genuinely comes up/down mid-process this
    cached value can go stale; that tradeoff is accepted deliberately in
    favor of a simple, cheap, and predictable check -- see this module's
    docstring.
    """
    global _probed_available

    if environment_mode() == "not-applicable":
        return False

    if _probed_available is None:
        _probed_available = await _probe_scope_available()
    return _probed_available


def reset_probe_cache_for_tests() -> None:
    """Test-only: clear the cached probe result so each test starts fresh."""
    global _probed_available
    _probed_available = None


# ---------------------------------------------------------------------------
# argv wrapping
# ---------------------------------------------------------------------------

# --quiet    suppress systemd-run's own "Running as unit: run-xxxx.scope"
#            announcement.
# --collect  unload the transient unit from systemd's memory once it exits,
#            so repeated session creates/ttyd spawns don't accumulate
#            "run-xxxx.scope" objects forever.
# --same-dir run the unit with the CALLING process's current working
#            directory (matches the default cwd-inherits-from-parent
#            behavior of a plain subprocess spawn, which this replaces).
#
# Deliberately NOT using --setenv: in `--scope` mode, systemd-run does not
# ask the systemd user *manager* to fork the target command from the
# manager's own environment -- it registers the scope over dbus and then
# execs the wrapped command IN THE SAME PROCESS. That exec inherits
# whatever environment was given to the systemd-run subprocess call itself
# (the `env=` kwarg the caller already passes), exactly like an unwrapped
# spawn. Verified empirically (see this fix's DTU proof) -- no --setenv
# plumbing is needed for correct environment passthrough.
_SCOPE_PREFIX = [
    "systemd-run",
    "--user",
    "--scope",
    "--quiet",
    "--collect",
    "--same-dir",
    "--",
]


def wrap_exec_argv(argv: list[str]) -> list[str]:
    """Prepend the systemd scope wrapper to an exec-style argv.

    Caller must have already awaited ``should_escape()`` and only call this
    when it returned True. Kept as a plain, synchronous function (no event
    loop needed) so it is trivially unit-testable.
    """
    return [*_SCOPE_PREFIX, *argv]


def wrap_shell_argv(command: str) -> list[str]:
    """Build an exec-style argv that runs *command* (an arbitrary shell
    string) inside the systemd scope wrapper.

    Used by callers that would otherwise use ``create_subprocess_shell``
    (i.e. ``tmux/spawn.py``'s ``spawn_session``, whose caller-resolved
    template is arbitrary user shell text) -- ``sh -c`` preserves normal shell
    semantics for *command* while the outer call stays exec-style, which is
    what ``systemd-run --user --scope --``'s own argv requires.
    """
    return [*_SCOPE_PREFIX, "sh", "-c", command]

"""
Per-session ttyd process lifecycle management for muxplex.

ARCHITECTURE: one ttyd PER SESSION, each bound to its own UNIX domain socket
under ``ttyd_socket_dir()``. This superseded the single, server-wide ttyd on
a hardcoded TCP port (7682) that made two devices on two different sessions
a *conflict* muxplex had to detect and refuse -- see
``docs/plans/2026-08-01-per-device-sync-groups-plan.md`` §0 and
``docs/plans/2026-08-02-per-session-ttyd-plan.md`` for the full history and design.

SECURITY -- carried forward verbatim in substance from the single-ttyd era,
because it is still exactly as true: ttyd runs ``-W`` (writable) with no
``-c`` credential configured, so it is an **unauthenticated, writable
terminal server** -- anyone who can reach a ttyd's socket can both view and
*type into* whatever tmux session it is attached to, with zero interaction
with muxplex's auth stack (``_ws_auth_check``, the cookie/Bearer middleware,
TLS). All legitimate access goes through muxplex's own authenticated
``WS /terminal/ws`` (or federation) proxy in ``main.py``, which dials a
ttyd's UNIX socket directly -- never a public interface.

**Incident (pre-UNIX-socket era):** this process previously bound TCP with no
``-i``/bind flag at all. ttyd's default bind with no ``-i`` is ``INADDR_ANY``
(``0.0.0.0``) -- confirmed live: ``ss -ltnp`` showed ``0.0.0.0:7682``, `curl`
from another host on the LAN and separately over Tailscale both got a real
ttyd terminal client (``200``, full HTML), and ``GET /token`` returned
``{"token": ""}`` (no credential configured). Any device reachable on the LAN
or tailnet could open the port in a browser and type into the host's live
tmux session. Fixed at the time by adding ``-i 127.0.0.1`` (``TTYD_BIND_ADDRESS``,
now deleted along with the TCP transport it fenced).

``AF_UNIX`` supersedes ``-i 127.0.0.1`` with a *strictly stronger* fence:
filesystem permissions (0700 dir, 0600 socket, uid-checked) and no network
namespace involvement at all -- there is no port to scan, no interface to
misconfigure, nothing Tailscale or a LAN peer can ever reach. ``SOCKET_SUFFIX``
is the successor to that old ``-i`` fence: a non-``.sock`` path does not make
ttyd error -- it logs ``iface ... DOESN'T EXIST``, silently falls back to
listening on **TCP port 7681** on ``INADDR_ANY``, and stays alive with exit
status 0. That fallback is the *identical* exposure the loopback bind flag
was originally added to close, just on a different port. This is why
``socket_path_for()`` can never produce a non-``.sock`` path, and why
``validate_socket_dir()``/the spawn readiness gate below check the actual
socket file rather than trusting ttyd's liveness or its own log line.

**Never make ttyd's bind target configurable/PATCHable.** If a future need
ever justifies exposing it, any such setting must join
``settings.LOCAL_ONLY_KEYS`` (see AGENTS.md's "Terminal input" section) --
same fence rationale as ``new_session_template`` et al.

See ``docs/API_SEMANTICS.md``'s "single shared ttyd process" section (now
retitled) for how this interacts with sync groups' ``terminal_session``/
``terminal_group`` bookkeeping, which is now provenance metadata rather than
a resource claim.

Public API:
    ttyd_socket_dir()     -- resolve the socket directory (env override or STATE_DIR/ttyd)
    validate_socket_dir() -- fail-loud startup check: symlink, WSL/DrvFs, ownership, perms, sun_path, bind probe
    socket_path_for()     -- deterministic, hashed .sock path for a session name
    socket_is_live()      -- real AF_UNIX connect() liveness probe (never Path.exists())
    ensure_ttyd()         -- idempotent get-or-spawn, the normal entry point
    spawn_ttyd()          -- unconditional spawn with collision guard + readiness gate
    kill_ttyd()           -- kill one session's ttyd, unlink its socket + run file
    kill_all_ttyd()       -- kill every registered ttyd (lifespan shutdown)
    reap_orphan_ttyds()   -- startup: identity-checked reap from run files across a restart
    reap_legacy_ttyd()    -- one-time migration: reap the pre-upgrade single ttyd, report-never-sweep 7682
    acquire_relay() / release_relay() / relay_count() -- refcounting for the idle reaper
    reap_idle_ttyds()     -- resource hygiene: kill idle (relays == 0) ttyds past IDLE_REAP_SECONDS
    ttyd_stats()          -- introspection: {"count": int, "sessions": [...]}
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import platform
import signal
import socket
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from muxplex.cgroup_escape import should_escape, wrap_exec_argv
from muxplex.sessions import tmux_env
from muxplex.state import STATE_DIR  # no import cycle: state.py imports only stdlib

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOCKET_SUFFIX: str = (
    ".sock"  # ttyd's UNIX-socket detection is suffix-based (Finding 1).
)
SOCKET_PREFIX: str = "mx-"
SOCKET_HASH_LEN: int = 12  # hex chars of sha256; basename is fixed-width
SOCKET_BASENAME_LEN: int = 20  # len("mx-") + 12 + len(".sock") == 3 + 12 + 5
assert SOCKET_BASENAME_LEN == len(SOCKET_PREFIX) + SOCKET_HASH_LEN + len(SOCKET_SUFFIX)

# The only sun_path budget safe on Linux, WSL2, AND macOS (through ttyd) --
# see scripts/README.md's sun_path table. Portable code targets the tightest
# platform (macOS: 102), never the host's own kernel limit.
SUN_PATH_BUDGET: int = 102

DRVFS_MOUNT_PREFIX: str = "/mnt/"

SPAWN_READY_TIMEOUT: float = 5.0
SPAWN_POLL_INTERVAL: float = 0.005
TERM_POLL_INTERVAL: float = 0.05
TERM_TIMEOUT: float = 2.0

MAX_TTYDS: int = 32  # backstop against a reaper bug, not user behavior
IDLE_REAP_SECONDS: float = 60.0

# LEGACY WIRE FIELD ONLY. ttyd no longer binds a TCP port at all. This
# constant exists solely because POST /api/sessions/{name}/connect returns
# `ttyd_port`, and client/muxplex_client/_protocol.py:147 reads it as a
# REQUIRED int with no default -- omitting it raises KeyError inside
# muxplex-deck (the Stream Deck sidecar vendors muxplex_client into its own
# venv). DO NOT DELETE AS UNUSED, and do not repurpose the value.
TTYD_PORT: int = 7682

# One-time migration target: the pre-per-session ttyd wrote its PID here.
LEGACY_TTYD_PID_PATH: Path = (
    Path(
        os.environ.get(
            "TMUX_WEB_STATE_DIR", Path.home() / ".local" / "share" / "tmux-web"
        )
    ).expanduser()
    / "ttyd.pid"
)


# ---------------------------------------------------------------------------
# Socket directory
# ---------------------------------------------------------------------------


def ttyd_socket_dir() -> Path:
    """Resolve the directory that holds every per-session ttyd UNIX socket.

    Resolution order (first match wins), each ``.expanduser()``'d:
      1. ``MUXPLEX_TTYD_SOCKET_DIR`` env var -- the sun_path escape hatch.
      2. ``STATE_DIR / "ttyd"`` (default: ``~/.local/share/muxplex/ttyd``).

    Deliberately NOT a settings key: a settings key naming a filesystem path
    the server writes and then connects to would have to join
    ``settings.LOCAL_ONLY_KEYS`` (same class as ``tmux_socket_dir``,
    ``tls_cert``). An env var sidesteps the whole LOCAL_ONLY_KEYS/SYNCABLE_KEYS
    question entirely and cannot be reached by a federation Bearer-key holder
    at all -- matching the existing ``MUXPLEX_STATE_DIR``/``TMUX_WEB_STATE_DIR``
    precedent.
    """
    override = os.environ.get("MUXPLEX_TTYD_SOCKET_DIR")
    if override:
        return Path(override).expanduser()
    return (STATE_DIR / "ttyd").expanduser()


# Module-level default, computed once at import time -- callers/tests
# monkeypatch this attribute directly (same pattern as the old TTYD_PID_DIR),
# rather than the env var, so it's stable within a process/test.
TTYD_SOCKET_DIR: Path = ttyd_socket_dir()


class TtydSocketDirError(RuntimeError):
    """Raised when the ttyd socket directory fails validation at startup."""


def is_wsl() -> bool:
    """True when running under WSL, where ``/mnt/*`` is a DrvFs/9p mount that
    cannot host an ``AF_UNIX`` socket (see module docstring, Finding 3)."""
    return "microsoft" in platform.uname().release.lower()


def validate_socket_dir(directory: Path) -> None:
    """Fail-loud startup validation of *directory* as a ttyd socket home.

    Called ONCE at startup, before the poll loop. Raises ``TtydSocketDirError``
    with an actionable message on any failure -- never degrades, never falls
    back silently. This directory hosts unauthenticated writable terminals,
    so every check here is a security boundary, not a convenience check.

    Order matters here in a way that is not obvious from reading each check
    in isolation: on a WSL2 host with the (default) metadata-less DrvFs
    mount, ``chmod()`` on a ``/mnt/*`` path is a *silent no-op* -- it returns
    success but the directory's reported mode never actually changes, and
    DrvFs also reports a synthetic, mount-wide uid/gid rather than real
    per-file ownership. Left in the original order, both the ownership check
    and the mode check below would fire on such a directory for reasons that
    have nothing to do with an attacker or a misconfigured umask -- and
    neither is fixable by chmod/chown, because the filesystem never persists
    those bits. The WSL/DrvFs check therefore runs BEFORE both, so the
    accurate, actionable diagnosis (this filesystem cannot host an AF_UNIX
    bind at all -- move the directory) surfaces instead of a misleading
    "just fix the permissions/ownership" message describing a fix that
    cannot work here. The symlink check is unaffected by any of this (it
    reads the file-type bit, not a permission or ownership bit) and stays
    first, since a directory that is itself a symlink is a strictly worse
    finding than anything downstream.
    """
    if not hasattr(socket, "AF_UNIX"):
        raise TtydSocketDirError(
            "muxplex requires AF_UNIX; this platform has none. Per-session "
            "ttyd cannot bind a UNIX domain socket here."
        )

    # mkdir's mode= is umask-masked and silently skipped when the dir already
    # exists -- chmod is not, so it always runs unconditionally after. On a
    # metadata-less WSL DrvFs mount this chmod is itself a silent no-op (see
    # the docstring above); it is kept anyway because it is the correct,
    # harmless action on every filesystem that DOES honor it (the common case).
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)

    st = directory.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise TtydSocketDirError(
            f"ttyd socket dir {directory} is a symlink; refusing to use it. This "
            "directory hosts unauthenticated writable terminals -- it must be a "
            "real, owned directory, never a link an attacker could redirect."
        )

    # Resolve and check for a WSL/DrvFs mount BEFORE ownership/mode, so a
    # symlink or `..` into /mnt is caught too, and so the DrvFs-specific
    # diagnosis pre-empts the generic ownership/mode checks below (see the
    # docstring). This is a heuristic for one known-bad case; the bind probe
    # at the end of this function is the real, filesystem-agnostic proof.
    resolved = directory.resolve()
    if is_wsl() and str(resolved).startswith(DRVFS_MOUNT_PREFIX):
        raise TtydSocketDirError(
            f"ttyd socket dir {resolved} resolves under {DRVFS_MOUNT_PREFIX!r}, a "
            "WSL DrvFs/9p mount. AF_UNIX bind() fails ENOTSUP there, and ttyd does "
            "NOT treat that as fatal -- it busy-retries the bind roughly every "
            "10ms, forever, alive and burning CPU, with no socket ever created and "
            "no TCP fallback. On the default (metadata-less) DrvFs mount, chmod/chown "
            "on this path are also silent no-ops, so no permission or ownership fix "
            "can make this directory usable here. Set MUXPLEX_TTYD_SOCKET_DIR to a "
            "Linux-native path (e.g. under $HOME, never under /mnt)."
        )

    if st.st_uid != os.getuid():
        raise TtydSocketDirError(
            f"ttyd socket dir {directory} is owned by uid {st.st_uid}, not this "
            f"process's uid {os.getuid()}. Refusing a directory this process does "
            "not own -- it hosts unauthenticated writable terminals."
        )
    if st.st_mode & 0o077:
        # This process just called chmod(0o700) on this exact directory a few
        # lines above -- reaching here with group/other bits still set means
        # that chmod call had no effect. (A TOCTOU race is possible in
        # principle, but the confirmed, durable cause is a filesystem that
        # does not persist Unix permission bits at all: WSL DrvFs without the
        # `metadata` mount option, with FAT/exFAT or some network filesystems
        # as plausible others.) Say that plainly instead of instructing the
        # operator to do the one thing this process just tried and failed to do.
        raise TtydSocketDirError(
            f"ttyd socket dir {directory} is group/other accessible (mode "
            f"{oct(st.st_mode & 0o777)}) even though this process just called "
            "chmod(0700) on it -- that chmod call had no effect. This directory "
            "hosts unauthenticated writable terminals, so group/other access is a "
            "full RCE handoff, and a chmod that silently does not persist means no "
            "amount of retrying chmod will secure this directory to 0700 here. This "
            "is a known filesystem limitation (e.g. WSL DrvFs, FAT/exFAT, some "
            "network mounts), not a permissions mistake to fix in place. Set "
            "MUXPLEX_TTYD_SOCKET_DIR to a path on a filesystem that persists Unix "
            "permission bits (e.g. under $HOME on a native Linux filesystem)."
        )

    projected_len = len(str(resolved)) + 1 + SOCKET_BASENAME_LEN
    if projected_len > SUN_PATH_BUDGET:
        raise TtydSocketDirError(
            f"ttyd socket dir {resolved} is too long: a socket path under it would "
            f"be {projected_len} bytes, over the {SUN_PATH_BUDGET}-byte sun_path "
            "budget ttyd needs on the tightest supported platform (macOS). Set "
            "MUXPLEX_TTYD_SOCKET_DIR to a shorter path."
        )

    # The real proof (see module docstring): the /mnt/ prefix check above is a
    # heuristic for one known-bad case; this bind probe catches every other
    # filesystem that cannot host an AF_UNIX socket (a DrvFs mounted
    # elsewhere, some NFS configurations, exotic overlays).
    probe_path = resolved / ".probe.sock"
    probe_path.unlink(missing_ok=True)
    probe_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe_sock.bind(str(probe_path))
    except OSError as exc:
        raise TtydSocketDirError(
            f"ttyd socket dir {resolved} failed a real AF_UNIX bind probe: "
            f"[Errno {exc.errno}] {exc.strerror}. This filesystem cannot host a "
            "UNIX domain socket."
        ) from exc
    finally:
        probe_sock.close()
        probe_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Socket path derivation
# ---------------------------------------------------------------------------


def socket_path_for(session_name: str) -> Path:
    """Deterministic, hashed ``.sock`` path for *session_name*.

    Pure, no I/O, no validation (the directory was validated once at startup
    and the basename is fixed-width, so per-call re-checking is dead weight).
    Hash, never interpolate: session names are arbitrary-length and
    user-chosen, and a raw name in a deep directory would overrun
    ``SUN_PATH_BUDGET``. The hash also means the filename never reveals the
    session name -- see ``spawn_ttyd()``'s log line and the run file for
    where that information is recorded instead.
    """
    digest = hashlib.sha256(session_name.encode("utf-8")).hexdigest()[:SOCKET_HASH_LEN]
    return TTYD_SOCKET_DIR / f"{SOCKET_PREFIX}{digest}{SOCKET_SUFFIX}"


def _run_path_for(sock: Path) -> Path:
    """Sibling run-record path for a socket path: same hash, ``.json`` suffix."""
    return sock.with_suffix(".json")


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


def socket_is_live(path: Path) -> bool:
    """Real ``AF_UNIX connect()`` liveness probe. Never raises, always a bool.

    A stale socket file left behind by a SIGKILLed ttyd answers
    ``Path.exists() == True`` forever while nothing is listening --
    ``ConnectionRefusedError`` (folded into the generic ``OSError`` branch)
    is exactly how that case is distinguished from a live listener. NEVER
    use ``Path.exists()`` for liveness; that check appears exactly once in
    this module, in the spawn-readiness poll below, where the precondition
    (an unlink immediately before spawn) makes it a valid proof of *this*
    process's bind rather than a liveness claim.
    """
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(str(path))
        return True
    except (OSError, TimeoutError):
        return False
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class TtydProc:
    session: str
    socket_path: Path
    run_path: Path
    pid: int
    proc: asyncio.subprocess.Process
    relays: int = 0
    idle_since: float | None = None  # time.monotonic(); None while relays > 0
    started_at: float = 0.0  # time.monotonic()


_ttyds: dict[str, TtydProc] = {}  # keyed by session name


class TtydSpawnError(RuntimeError):
    """Raised when a ttyd process fails to spawn or bind its UNIX socket."""


class TtydCapacityError(RuntimeError):
    """Raised when MAX_TTYDS is reached and no idle ttyd could be reaped."""


# ---------------------------------------------------------------------------
# Spawn
# ---------------------------------------------------------------------------


async def spawn_ttyd(session_name: str) -> TtydProc:
    """Unconditionally spawn a ttyd bound to ``socket_path_for(session_name)``.

    Never returns a half-working ttyd and never swallows a failure -- every
    error path raises ``TtydSpawnError`` with a diagnosis. Callers translate:
    ``/connect`` -> HTTP 500; WS proxy -> log + close (no relay attempted).
    """
    sock = socket_path_for(session_name)

    # Collision guard (spec §3.3): 48 bits of hash over ~dozens of sessions is
    # astronomically unlikely to collide, but "negligible" is not "enforced" --
    # a silent collision means two sessions sharing one ttyd, exactly the
    # keystroke-misdirection hazard this whole architecture exists to kill.
    for existing in _ttyds.values():
        if existing.socket_path == sock and existing.session != session_name:
            raise TtydSpawnError(
                f"socket collision: session {session_name!r} hashes to the same "
                f"path as already-registered session {existing.session!r} ({sock}); "
                "refusing to spawn to avoid a silent cross-session terminal"
            )

    # Required: bind() fails EADDRINUSE on an existing path regardless of
    # liveness, and unlinking first is what makes the readiness poll below a
    # valid proof of THIS process's bind. A leftover orphan still listening
    # on the old inode becomes harmlessly unreachable (verified in the
    # platform spikes: a stale socket file does not block a rebind).
    sock.unlink(missing_ok=True)

    # Exactly the argv shape the three platform spikes qualified
    # (scripts/spike_ttyd_harness.py). -p and -i 127.0.0.1 are gone; -i now
    # carries the socket path.
    argv = [
        "ttyd",
        "-W",
        "-m",
        "3",
        "-i",
        str(sock),
        "tmux",
        "attach",
        "-t",
        session_name,
    ]
    # should_escape()/wrap_exec_argv() preserved verbatim: ttyd's per-client
    # `tmux attach` can still be what CREATES the tmux server, and cgroup
    # membership is inherited by that child. AGENTS.md's 44-lost-sessions
    # incident is the cost of getting this wrong. proc.pid remains ttyd's own
    # pid under the wrapper (systemd-run --scope execs in place -- verified).
    if await should_escape():
        argv = wrap_exec_argv(argv)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,  # detach from parent process group
        # Honor `tmux_socket_dir` (see sessions.tmux_env docstring): ttyd
        # execs `tmux attach` itself, so the TMUX_TMPDIR override must be in
        # ttyd's own environment for it to reach the tmux client it spawns.
        env=tmux_env(),
    )

    # Readiness gate: sock.exists() is valid proof of bind ONLY because we
    # just unlinked it above -- this is the one place in this module
    # Path.exists() is used for anything liveness-adjacent, and the
    # precondition is what makes it correct (see socket_is_live()'s docstring).
    deadline = time.monotonic() + SPAWN_READY_TIMEOUT
    while time.monotonic() < deadline:
        if sock.exists():
            break
        if proc.returncode is not None:
            raise TtydSpawnError(
                f"ttyd exited {proc.returncode} before creating {sock}"
            )
        await asyncio.sleep(SPAWN_POLL_INTERVAL)
    else:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise TtydSpawnError(
            f"ttyd is running (pid {proc.pid}) but {sock} does not exist. It "
            "almost certainly fell back to TCP :7681 -- an unauthenticated "
            "writable terminal. Liveness and exit status are NOT proof a socket "
            "was bound, and neither is ttyd's own log."
        )

    # Belt-and-braces: catches the pathological "file appeared, listener didn't".
    if not socket_is_live(sock):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise TtydSpawnError(f"ttyd bound {sock} but is not accepting connections")

    # Directory is already 0700; this is defense in depth for a misconfigured
    # umask or an operator who loosens the directory later.
    sock.chmod(0o600)

    run_path = _run_path_for(sock)
    run_path.write_text(
        json.dumps({"pid": proc.pid, "session": session_name, "socket": str(sock)}),
        encoding="utf-8",
    )

    ttyd_proc = TtydProc(
        session=session_name,
        socket_path=sock,
        run_path=run_path,
        pid=proc.pid,
        proc=proc,
        relays=0,
        idle_since=time.monotonic(),
        started_at=time.monotonic(),
    )
    _ttyds[session_name] = ttyd_proc
    # The hash means the filename never reveals the session name -- log it
    # here (and record it in the run file) so an operator can tell what's
    # actually running without inverting sha256.
    _log.info("ttyd: spawned session=%r socket=%s pid=%d", session_name, sock, proc.pid)
    return ttyd_proc


async def ensure_ttyd(session_name: str) -> TtydProc:
    """Idempotent get-or-spawn -- the normal entry point for every caller."""
    proc = _ttyds.get(session_name)
    if proc is not None:
        if socket_is_live(proc.socket_path):
            return proc
        # It died under us -- tear down our bookkeeping before respawning.
        await kill_ttyd(session_name)

    if len(_ttyds) >= MAX_TTYDS:
        await reap_idle_ttyds(force_one=True)
        if len(_ttyds) >= MAX_TTYDS:
            raise TtydCapacityError(
                f"at capacity ({MAX_TTYDS} ttyds), every one actively relaying; "
                f"cannot spawn a new terminal for {session_name!r}"
            )

    return await spawn_ttyd(session_name)


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


async def kill_ttyd(session_name: str) -> bool:
    """Kill *session_name*'s ttyd (if registered) and unlink its artifacts.

    Returns False (no-op, no raise) for an unregistered session -- callers
    (the idle reaper, a racing relay teardown) must never crash on a ttyd
    that's already gone.
    """
    proc = _ttyds.pop(session_name, None)
    if proc is None:
        return False

    with contextlib.suppress(ProcessLookupError):
        os.kill(proc.pid, signal.SIGTERM)

    try:
        await asyncio.wait_for(proc.proc.wait(), timeout=TERM_TIMEOUT)
    except (TimeoutError, asyncio.TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            os.kill(proc.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await proc.proc.wait()

    # Unconditional: a clean SIGTERM removes the socket, a SIGKILL does not,
    # and we must not depend on which one happened.
    proc.socket_path.unlink(missing_ok=True)
    proc.run_path.unlink(missing_ok=True)
    _log.info("ttyd: killed session=%r pid=%d", session_name, proc.pid)
    return True


async def kill_all_ttyd() -> int:
    """Kill every registered ttyd (lifespan shutdown). Returns the count killed."""
    sessions = list(_ttyds)
    if not sessions:
        return 0
    results = await asyncio.gather(
        *(kill_ttyd(s) for s in sessions), return_exceptions=True
    )
    return sum(1 for r in results if r is True)


# ---------------------------------------------------------------------------
# Process-identity helpers (shared by both reapers)
# ---------------------------------------------------------------------------


async def _ps_snapshot() -> dict[int, str]:
    """One-shot ``ps -eo pid=,command=`` snapshot: pid -> full command line.

    Returns {} on ANY failure (ps missing, timeout, non-zero exit) rather
    than raising. Callers must treat an empty snapshot as "cannot confirm
    identity" and fail safe -- never signal a PID whose identity is unverified.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps",
            "-eo",
            "pid=,command=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (OSError, TimeoutError, asyncio.TimeoutError):
        return {}
    if proc.returncode != 0:
        return {}

    snapshot: dict[int, str] = {}
    for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, cmdline = parts
        try:
            snapshot[int(pid_str)] = cmdline
        except ValueError:
            continue
    return snapshot


async def _terminate_pid(pid: int) -> None:
    """SIGTERM *pid*, poll ``os.kill(pid, 0)`` up to TERM_TIMEOUT, SIGKILL if needed.

    Only ever called after a positive identity confirmation -- see the two
    reapers below. Never raises on an already-gone process.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + TERM_TIMEOUT
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        await asyncio.sleep(TERM_POLL_INTERVAL)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


# ---------------------------------------------------------------------------
# Orphan reap (startup, every restart)
# ---------------------------------------------------------------------------


async def reap_orphan_ttyds() -> int:
    """Startup-only: identity-checked reap of ttyds left running across a restart.

    Replaces ``kill_orphan_ttyd()``. Runs once in lifespan, BEFORE the poll loop.

    The identity check is not optional: run files survive a reboot, and their
    PIDs are then almost certainly recycled into unrelated processes. Signal
    only if the recorded pid is (a) present in a fresh `ps` snapshot, (b) its
    cmdline mentions "ttyd", AND (c) its cmdline mentions the exact recorded
    socket path. On any mismatch: unlink both files, log a warning, and never
    signal -- an unkillable orphan is a leak (an unlinked inode + a tmux
    client); a mis-signalled innocent process is a catastrophe.
    """
    snapshot = await _ps_snapshot()
    count = 0
    seen_run_files: set[Path] = set()

    for run_path in sorted(TTYD_SOCKET_DIR.glob(f"{SOCKET_PREFIX}*.json")):
        seen_run_files.add(run_path)
        sock_path = run_path.with_suffix(SOCKET_SUFFIX)

        try:
            record = json.loads(run_path.read_text(encoding="utf-8"))
            pid = int(record["pid"])
            session = str(record["session"])
            socket_str = str(record["socket"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            run_path.unlink(missing_ok=True)
            sock_path.unlink(missing_ok=True)
            _log.warning(
                "ttyd: malformed run file %s; removed without signalling", run_path
            )
            continue

        cmdline = snapshot.get(pid, "")
        confirmed = bool(cmdline) and "ttyd" in cmdline and socket_str in cmdline

        if confirmed:
            await _terminate_pid(pid)
            count += 1
            _log.info(
                "ttyd: reaped orphan session=%r pid=%d socket=%s",
                session,
                pid,
                socket_str,
            )
        else:
            _log.warning(
                "ttyd: stale run file for session=%r (pid %d not ours); removed "
                "without signalling",
                session,
                pid,
            )

        run_path.unlink(missing_ok=True)
        sock_path.unlink(missing_ok=True)

    # A bare .sock with no matching .json is a crash between bind and
    # run-file write -- clean it up, no signal possible (no recorded pid).
    for sock_path in sorted(TTYD_SOCKET_DIR.glob(f"{SOCKET_PREFIX}*{SOCKET_SUFFIX}")):
        run_path = sock_path.with_suffix(".json")
        if run_path in seen_run_files:
            continue
        sock_path.unlink(missing_ok=True)

    return count


# ---------------------------------------------------------------------------
# Legacy reap (one-time migration from the single-ttyd era)
# ---------------------------------------------------------------------------


def _legacy_port_still_live() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", TTYD_PORT), timeout=0.5):
            return True
    except OSError:
        return False


async def reap_legacy_ttyd() -> bool:
    """One-time migration: reap the pre-upgrade single ttyd if its PID file
    survived and its identity is confirmed, then unconditionally
    detect-and-report (never sweep) a still-live legacy port -- regardless of
    whether a PID file existed at all.

    A live 7682 with NO recorded PID (crash, manual kill, or state-dir wipe
    lost the file) gets the identical loud report as every other unconfirmed
    case below: without a recorded PID there is no identity to check, and
    reaping by "whatever is listening on 7682" is precisely the port-based
    sweep this migration exists to delete. Report, never sweep.

    Runs once in lifespan, immediately after ``reap_orphan_ttyds()``.
    """
    reaped = False

    if LEGACY_TTYD_PID_PATH.exists():
        try:
            pid: int | None = int(LEGACY_TTYD_PID_PATH.read_text().strip())
        except (ValueError, OSError):
            pid = None
        LEGACY_TTYD_PID_PATH.unlink(missing_ok=True)

        if pid is not None:
            snapshot = await _ps_snapshot()
            cmdline = snapshot.get(pid, "")
            confirmed = (
                bool(cmdline)
                and "ttyd" in cmdline
                and (f"-p {TTYD_PORT}" in cmdline or f":{TTYD_PORT}" in cmdline)
            )
            if confirmed:
                await _terminate_pid(pid)
                reaped = True
                _log.info("ttyd: reaped legacy single-ttyd pid=%d", pid)

    # Unconditional detect-and-report, never a sweep: killing an unidentified
    # PID on a hardcoded port is precisely the dangerous sweep this migration
    # exists to delete (AGENTS.md's "unpatched second instance WILL kill the
    # first instance's ttyd" hazard). Runs regardless of which path above got
    # us here -- no PID file, a corrupt one, an unconfirmed identity, or a
    # confirmed reap that didn't actually take -- so a live legacy listener is
    # never silently missed.
    if _legacy_port_still_live():
        _log.error(
            "A process is still listening on 127.0.0.1:%d. This is very likely "
            "a pre-upgrade muxplex ttyd -- an UNAUTHENTICATED WRITABLE TERMINAL "
            "attached to a live tmux session. muxplex no longer manages that "
            "port and will not kill an unidentified process. Identify and stop "
            "it manually (find the process bound to that port and terminate it).",
            TTYD_PORT,
        )

    return reaped


# ---------------------------------------------------------------------------
# Relay accounting and the idle reaper
# ---------------------------------------------------------------------------


def acquire_relay(session_name: str) -> None:
    """relays += 1; idle_since = None. No-op for an unknown session."""
    proc = _ttyds.get(session_name)
    if proc is None:
        return
    proc.relays += 1
    proc.idle_since = None


def release_relay(session_name: str) -> None:
    """relays = max(0, relays - 1); if 0, start the idle clock. No-op for
    an unknown session -- a ttyd can be reaped out from under a racing
    relay, and a raise in a `finally` block would mask the real error."""
    proc = _ttyds.get(session_name)
    if proc is None:
        return
    proc.relays = max(0, proc.relays - 1)
    if proc.relays == 0:
        proc.idle_since = time.monotonic()


def relay_count(session_name: str) -> int:
    """0 for an unknown session -- never a raise."""
    proc = _ttyds.get(session_name)
    return proc.relays if proc is not None else 0


async def reap_idle_ttyds(*, force_one: bool = False) -> list[str]:
    """Kill idle (relays == 0) ttyds. Returns the session names reaped.

    Normal mode: every entry idle for at least IDLE_REAP_SECONDS.
    force_one=True (capacity backstop): the single oldest-idle entry with
    relays == 0, regardless of age. Returns [] if every entry is busy.

    Called from the poll cycle -- no new timer, riding the existing ~1s
    cycle exactly as gc_sync_groups() rides prune_devices(). Reaping is safe
    because a ttyd is a VIEW, not the durable thing: killing it detaches its
    `tmux attach` client(s) and leaves the tmux session untouched.
    """
    idle_candidates: list[tuple[float, TtydProc]] = [
        (p.idle_since, p)
        for p in _ttyds.values()
        if p.relays == 0 and p.idle_since is not None
    ]

    to_reap: list[TtydProc]
    if force_one:
        to_reap = (
            [min(idle_candidates, key=lambda pair: pair[0])[1]]
            if idle_candidates
            else []
        )
    else:
        now = time.monotonic()
        to_reap = [
            p
            for idle_since, p in idle_candidates
            if now - idle_since >= IDLE_REAP_SECONDS
        ]

    reaped: list[str] = []
    for p in to_reap:
        if await kill_ttyd(p.session):
            reaped.append(p.session)
            _log.info("ttyd: idle-reaped session=%r", p.session)
    return reaped


def ttyd_stats() -> dict:
    """Introspection: {"count": int, "sessions": [{session, relays, idle_s}]}."""
    now = time.monotonic()
    return {
        "count": len(_ttyds),
        "sessions": [
            {
                "session": p.session,
                "relays": p.relays,
                "idle_s": (now - p.idle_since) if p.idle_since is not None else None,
            }
            for p in _ttyds.values()
        ],
    }

"""Safety rails for the muxplex test suite.

WHY THIS FILE EXISTS -- do not delete it, and do not weaken the guard:

On 2026-07-25 this suite was run repeatedly on a developer host that was also
running a live muxplex instance. It caused real production damage, twice over:

  1. A test that wrote settings without redirecting ``SETTINGS_PATH`` overwrote
     the host's real ``~/.config/muxplex/settings.json``, replacing an 8-view
     production configuration with its own fixture data
     (``{"name": "Focus", "sessions": ["alpha"]}``). Recovered from backups.

  2. Six tests called the real ``serve()`` without mocking
     ``_kill_stale_port_holder`` and without pinning a port. The port therefore
     resolved to ``DEFAULT_SETTINGS["port"]`` (8088), and the real killer ran
     ``lsof -ti :8088``, found the live server's PID, and SIGTERMed it. systemd
     restarted it each time, so the symptom was a server that "kept resetting"
     with clean graceful shutdowns, no crash, and no systemd ``Stopping`` line
     -- almost impossible to diagnose from the logs alone.

Neither failure was noticed for hours, because from inside the suite everything
looked green. That is the point: a test that damages its host still passes.

The guard below makes the whole class fail LOUD and up front instead. It is a
mechanism, not a reminder -- a future session with none of this context still
gets stopped. ``test_safety_rails.py`` fails if this file or its guard goes
missing, so removing it cannot pass silently either.

The correct way to run this suite is inside an isolated environment (a Digital
Twin Universe container). See ``AGENTS.md`` -> "Running the test suite".
"""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Escape hatch: set this when you are certain nothing live is at risk (CI
# runners, a fresh container, a machine with no muxplex). It is deliberately
# verbose -- typing it should feel like a decision, not a reflex.
_OVERRIDE_ENV = "MUXPLEX_TEST_ALLOW_LIVE_HOST"


def _something_is_listening(
    port: int, host: str = "127.0.0.1", timeout: float = 0.5
) -> bool:
    """True if *port* accepts a TCP connection on *host*.

    Deliberately dependency-free (no lsof) and deliberately dumb: any listener
    at all is treated as a hazard. We are not trying to identify muxplex
    specifically -- if something owns the port the suite targets by default,
    that is reason enough to stop.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _default_port() -> int:
    """The port an unmocked ``serve()`` would resolve to."""
    try:
        from muxplex.settings import DEFAULT_SETTINGS

        return int(DEFAULT_SETTINGS.get("port", 8088))
    except Exception:
        return 8088


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse to run when a live server could be harmed.

    Fails the whole session before a single test is collected, rather than
    letting a careless test discover the live server mid-run.
    """
    if os.environ.get(_OVERRIDE_ENV) == "1":
        return

    port = _default_port()
    if not _something_is_listening(port):
        return

    raise pytest.UsageError(
        f"\n"
        f"REFUSING TO RUN: something is already serving 127.0.0.1:{port}.\n"
        f"\n"
        f"This suite has previously destroyed a live muxplex's settings.json and\n"
        f"SIGTERMed the running server (see muxplex/tests/conftest.py for the\n"
        f"full history). Tests that call the real serve() resolve to this port\n"
        f"and will signal whatever process owns it.\n"
        f"\n"
        f"Run the suite in an isolated environment instead -- see AGENTS.md,\n"
        f"'Running the test suite'.\n"
        f"\n"
        f"If you are certain nothing live is at risk (fresh container, CI runner,\n"
        f"no muxplex on this host), override explicitly:\n"
        f"\n"
        f"    {_OVERRIDE_ENV}=1 pytest\n"
    )


@pytest.fixture(autouse=True)
def _isolate_settings_path(tmp_path, monkeypatch):
    """Point ``SETTINGS_PATH`` at a per-test temp file for EVERY test.

    Defense in depth behind the session guard. Individual tests may still
    redirect it themselves (many do, explicitly); this only guarantees that a
    test which *forgets* to cannot reach the real user config. It is the
    difference between "tests are supposed to isolate" and "tests cannot fail
    to isolate."
    """
    try:
        import muxplex.settings as settings_mod
    except Exception:  # pragma: no cover - import shape changed
        yield
        return

    monkeypatch.setattr(
        settings_mod, "SETTINGS_PATH", tmp_path / "settings.json", raising=False
    )
    yield


@pytest.fixture(autouse=True)
def _isolate_tmux_socket_dir(monkeypatch):
    """Force every test's REAL (unmocked) tmux subprocess calls onto an
    isolated, per-test socket directory -- never the ambient default, which
    on a developer box that sources ``muxplex env`` in its shell rc (see
    AGENTS.md's "Running a second instance on one box") IS the live
    production tmux server.

    **Incident this closes:** a bell-hook delivery proof called the real
    ``_arm_bell_hook()`` -- which issues tmux's ``set-hook -g``, GLOBAL to
    the whole tmux server -- against a scratch TLS port, but never
    overrode ``TMUX_TMPDIR`` or the ``tmux_socket_dir`` setting. Because the
    proof's own shell had ``TMUX_TMPDIR`` exported (from the owner's
    ``muxplex env`` integration), ``tmux_env()`` resolved to the OWNER'S
    REAL tmux server. Every real bell across 53 live sessions then curled
    a dead scratch port for as long as the hook stayed armed. Hand-repaired
    once; this fixture is what stops it from ever happening again.

    Belt-and-suspenders with ``_isolate_settings_path`` above: that isolates
    the *settings-driven* ``tmux_socket_dir`` `tmux_env()` reads first; this
    isolates the *environment* fallback it falls back to when no setting is
    configured (``env=None`` -> the subprocess inherits ``os.environ``,
    which is exactly the value a test or an ad-hoc verification script can
    forget to override). Every test gets a real, unique, throwaway,
    guaranteed-empty socket directory by default -- reaching the ambient
    tmux server now requires an explicit, reviewable override, not silence.

    **Deliberately NOT built on pytest's own ``tmp_path``.** tmux derives its
    actual AF_UNIX socket path from ``TMUX_TMPDIR`` as
    ``$TMUX_TMPDIR/tmux-<uid>/<socket-name>``, and that path must fit the
    kernel's ``sun_path`` budget (~104 bytes on macOS -- see ``ttyd.py``'s
    ``SUN_PATH_BUDGET`` and its module docstring). ``tmp_path`` on macOS
    (CI and real hardware alike) resolves to a long, deeply-nested path
    (``/private/var/folders/<x>/<y>/T/pytest-of-<user>/pytest-<n>/<test
    name>0/...``) that is already 100+ bytes before a socket filename is
    even appended -- the exact class of bug ``short_socket_dir`` below was
    added to avoid for ttyd's own sockets. Stacking `/tmux-isolated` and
    then tmux's own `/tmux-<uid>/<socket-name>` on top of that pushes the
    total well past budget, and tmux's ``new-session`` then fails with
    ``error connecting to ... (File name too long)`` -- indistinguishable,
    to a caller reading an option back, from tmux never having started at
    all. Confirmed live on macOS (arm64, tmux 3.6a): a 115-byte
    ``tmp_path``-derived socket path failed with exactly that error; every
    ``test_tmux_config.py`` assertion reading a real tmux option back then
    saw an empty string. **Incident:** this is what broke CI job
    ``test (macOS, arm64)`` -- 12 failures in ``test_tmux_config.py``,
    Linux unaffected because its ``tmp_path`` is short enough to stay
    under budget by coincidence, not by design.

    Fixed the same way ``short_socket_dir`` already fixes it for ttyd: mkdtemp
    directly under ``/tmp`` (not ``tempfile.gettempdir()``, which macOS sets
    to the deep ``$TMPDIR`` path above) -- short on every supported platform,
    with its own explicit cleanup since it is no longer inside pytest's
    managed ``tmp_path`` tree.

    A test that wants a REAL, working isolated tmux server on top of this
    (e.g. to fire an actual bell) still layers its own explicit isolation --
    see ``test_integration.py``'s ``tmux_server`` fixture (``tmux -L
    <unique-name>``) -- this fixture only guarantees the *default* everyone
    else gets is never the ambient one.
    """
    tmux_dir = Path(tempfile.mkdtemp(prefix="tmux-isolated-", dir="/tmp"))
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
    monkeypatch.delenv("TMUX", raising=False)
    try:
        yield
    finally:
        shutil.rmtree(tmux_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _neutralize_port_killer(request, monkeypatch):
    """No test may invoke the REAL ``_kill_stale_port_holder`` by accident.

    This is the last line of defence, and the one that closes the class rather
    than the six known instances. The session guard above can be overridden
    with an env var; the settings isolation does not help here, because the
    port still resolves to ``DEFAULT_SETTINGS["port"]`` when a test calls
    ``serve()`` without pinning one. That is exactly how six tests in
    ``test_cli.py`` came to run ``lsof -ti :8088`` against a developer's live
    server and SIGTERM it.

    Tests that legitimately exercise the killer opt in explicitly:

        @pytest.mark.allow_real_port_killer

    A future test author who forgets is silently safe. One who needs the real
    thing has to say so in a way a reviewer can see.
    """
    if "allow_real_port_killer" in request.keywords:
        yield
        return
    try:
        import muxplex.cli as cli_mod
    except Exception:  # pragma: no cover - import shape changed
        yield
        return
    monkeypatch.setattr(
        cli_mod, "_kill_stale_port_holder", lambda *a, **k: None, raising=False
    )
    yield


@pytest.fixture(autouse=True)
def _default_service_ready_wait(request, monkeypatch):
    """Default ``_wait_for_service_ready`` to instantly-ready for every test.

    Same rationale, same shape as ``_neutralize_port_killer`` above: without
    this, ``upgrade()``'s post-restart readiness poll (added to fix the
    "Verifying..." race -- see ``cli._wait_for_service_ready``) would attempt
    a REAL network probe against whatever port ``load_settings()`` resolves
    to, for every test that drives ``upgrade()`` end-to-end -- and poll for
    the real ceiling when nothing is listening, turning dozens of
    otherwise-instant CLI tests into multi-second waits.

    Tests that want to exercise the real poll loop opt in explicitly:

        @pytest.mark.allow_real_service_ready_wait
    """
    if "allow_real_service_ready_wait" in request.keywords:
        yield
        return
    try:
        import muxplex.cli as cli_mod
    except Exception:  # pragma: no cover - import shape changed
        yield
        return
    monkeypatch.setattr(
        cli_mod, "_wait_for_service_ready", lambda *a, **k: True, raising=False
    )
    yield


@pytest.fixture
def short_socket_dir():
    """A scratch directory short enough to host a real AF_UNIX socket.

    ``ttyd``'s socket path must fit ``SUN_PATH_BUDGET`` (102 bytes, the
    tightest limit across Linux/WSL/macOS -- see ``ttyd.py``'s module
    docstring and AGENTS.md's "ttyd is loopback-only by design"). pytest's
    own ``tmp_path`` is NOT safe for this: on macOS CI it resolves to
    something like
    ``/private/var/folders/df/<random>/T/pytest-of-runner/pytest-0/<test>0/``,
    which is already ~120 bytes before a socket filename is even appended --
    comfortably over budget. Real usage
    (``~/.local/share/muxplex/ttyd/``) is nowhere near that; only the test
    fixture was too deep, which is why this was invisible on the Linux CI
    job (whose ``tmp_path`` is short) and only ever fired on macOS.

    ``/tmp`` (not ``tempfile.gettempdir()``, which macOS CI sets to the deep
    path above via ``$TMPDIR``) is short on every supported platform --
    Linux, macOS (a symlink to ``/private/tmp``, still short after
    ``resolve()``), and WSL (a real, short, ext4-backed path, never
    ``/mnt/*``) -- so no platform branch is needed; a short path is simply
    correct everywhere. Same base directory ``scripts/spike_ttyd_harness.py``
    already uses for the identical reason.
    """
    base = Path(tempfile.mkdtemp(prefix="mxt-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


# The modules whose namespaces BIND ``should_escape`` for a live production
# call site (a ``from ... import should_escape`` followed by a call). This
# list must track code moves: monkeypatching resolves per-module bindings,
# so patching a module that no longer calls it protects nothing, and the
# live caller keeps hitting the real function. ``test_safety_rails.py``'s
# ``test_cgroup_escape_default_covers_every_live_call_site`` derives the
# true set from an AST scan of both production trees and fails if this
# constant drifts from reality -- see the 2026-08-08 incident in the
# fixture docstring below.
SHOULD_ESCAPE_BINDING_MODULES = ("tmux_kit.spawn", "muxplex.ttyd")


@pytest.fixture(autouse=True)
def _default_cgroup_escape_disabled(monkeypatch):
    """Default ``should_escape()`` to False for every test.

    Without this, ``tmux_kit.spawn.spawn_session()`` / ``ttyd.spawn_ttyd()``
    would call the REAL ``cgroup.should_escape()`` -- which, on any
    dev/CI host that happens to have a usable systemd --user session, spawns
    a REAL ``systemd-run --user --scope`` probe process as a test side
    effect, and then routes the spawn through ``create_subprocess_exec``
    (the scope-wrapped branch) instead of ``create_subprocess_shell``. That
    is exactly the kind of host-touching behavior this suite's other
    autouse fixtures exist to prevent (see this file's module docstring).
    Tests that specifically exercise the escape-ENABLED path override this
    within the test body -- see test_cgroup_escape.py and the relevant
    cases in test_sessions.py / test_ttyd.py.

    INCIDENT (2026-08-08, the reason this fixture fails LOUD now): the
    tmux-lib extraction (S1-S3) moved the spawn body out of
    ``muxplex.sessions`` into ``tmux_kit.spawn``, which binds its own
    ``should_escape`` (``from tmux_kit.cgroup import should_escape``). This
    fixture's patch list still named ``muxplex.sessions`` -- which no
    longer had the attribute -- and a ``try/except AttributeError: pass``
    swallowed the miss silently. Result: on CI's Linux runners (working
    ``systemd --user`` session, unlike macOS and the DTU container) every
    ``POST /api/sessions`` test really executed its session template via
    ``systemd-run --user --scope -- sh -c ...``, returned 200, and never
    touched the test's ``create_subprocess_shell`` mock -- five test_api
    tests red on CI while ``make test`` stayed green. A module listed here
    that stops binding ``should_escape`` must therefore FAIL the suite
    (monkeypatch.setattr raises), never be skipped: the swallow was the
    bug's camouflage.
    """
    from tmux_kit import cgroup as cgroup_mod

    cgroup_mod.reset_probe_cache_for_tests()
    for module_name in SHOULD_ESCAPE_BINDING_MODULES:
        monkeypatch.setattr(
            module_name + ".should_escape",
            AsyncMock(return_value=False),
        )
    yield
    cgroup_mod.reset_probe_cache_for_tests()

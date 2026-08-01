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
import socket
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


@pytest.fixture(autouse=True)
def _default_cgroup_escape_disabled(monkeypatch):
    """Default ``should_escape()`` to False for every test.

    Without this, ``sessions.spawn_session_command()`` / ``ttyd.spawn_ttyd()``
    would call the REAL ``cgroup_escape.should_escape()`` -- which, on any
    dev/CI host that happens to have a usable systemd --user session, spawns
    a REAL ``systemd-run --user --scope`` probe process as a test side
    effect. That is exactly the kind of host-touching behavior this suite's
    other autouse fixtures exist to prevent (see this file's module
    docstring). Tests that specifically exercise the escape-ENABLED path
    override this within the test body -- see test_cgroup_escape.py and the
    relevant cases in test_sessions.py / test_ttyd.py.
    """
    import muxplex.cgroup_escape as cgroup_escape_mod

    cgroup_escape_mod.reset_probe_cache_for_tests()
    for module_name in ("muxplex.sessions", "muxplex.ttyd"):
        try:
            monkeypatch.setattr(
                module_name + ".should_escape",
                AsyncMock(return_value=False),
            )
        except (ImportError, AttributeError):  # pragma: no cover
            pass
    yield
    cgroup_escape_mod.reset_probe_cache_for_tests()

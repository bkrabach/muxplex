"""Safety rails for the muxplex test suite.

WHY THIS FILE EXISTS -- do not delete it, and do not weaken what it guarantees:

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

RETIRED FIX: a ``pytest_sessionstart`` hook used to refuse to run the ENTIRE
suite whenever anything answered 127.0.0.1:8088. Safe, but backwards: it meant
the suite could never run at all on a host already serving a live muxplex
(this project's own primary dev host included) -- even though, by the time
that guard existed, the suite itself no longer needed that port for anything.
Refusing based on "is something else running" is an environment probe, not an
isolation guarantee.

CURRENT FIX -- structural isolation instead of refusal. Every fixture below is
autouse and applies to EVERY test, regardless of what else is running on the
host:

  * ``_isolate_settings_path`` -- no test's settings write can ever reach the
    real ``~/.config/muxplex/settings.json`` (closes incident 1).
  * ``_isolate_tmux_socket_dir`` -- no test's real tmux subprocess call can
    ever reach the ambient/production tmux server.
  * ``_neutralize_port_killer`` -- no test can invoke the REAL
    ``_kill_stale_port_holder`` (closes incident 2's *signal* step).
  * ``_neutralize_real_uvicorn_run`` -- no test can open a REAL listening
    socket via ``uvicorn.run`` (closes incident 2's *root cause*: an
    unmocked, unpinned ``serve()`` can no longer bind ANY port at all, so it
    is structurally unable to resolve to, bind, or signal 8088 -- or any
    other real/occupied port).

Together these make the dangerous outcome impossible by construction, not
merely discouraged. A test that genuinely needs a REAL server binds an
OS-allocated ephemeral port explicitly (see the ``free_port`` fixture below,
or ``test_bell_causality_integration.py`` / ``test_bell_hook_delivery_integration.py``
for two existing examples using the identical bind-port-0 technique) --
preferred over scanning upward from a well-known port and hoping it's free,
because an OS-allocated port cannot collide with a real listener by
construction.

``pytest_sessionstart`` still exists -- it is retargeted at the one thing
actually worth failing the WHOLE session for, up front: a NEW test
reintroducing incident 2's exact shape (opts into the real killer via
``@pytest.mark.allow_real_port_killer`` while calling ``.serve(`` without
pinning ``port=``). That is a structural (AST) scan of the test SOURCE
itself, not a probe of the host's network state -- it answers "would this
suite contain the dangerous shape," never "is something else running
elsewhere on this machine." It therefore never refuses the normal case, on
this host or any other, and has no environment-variable bypass: there is no
legitimate reason to run a suite that contains the dangerous shape, only a
reason to fix it.

``test_safety_rails.py`` fails if this file or any of the above goes missing
or is weakened, so removing it cannot pass silently either.
"""

from __future__ import annotations

import ast
import shutil
import socket
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def _serve_calls_without_pinned_port(tests_dir: Path) -> list[str]:
    """AST-scan every ``test_*.py`` file in *tests_dir* for the ONE shape
    that has actually caused live-host damage (incident 2 above): a test
    that opts into the real port killer
    (``@pytest.mark.allow_real_port_killer``) and calls ``.serve(`` without
    pinning ``port=``.

    Returns ``"<file>::<test_name>"`` for every offender, empty if none.
    Deliberately narrow: the marker is the ONLY way a test reaches the real
    killer at all (``_neutralize_port_killer`` below defaults every other
    test to a no-op), so this is the *complete* set of tests capable of
    repeating incident 2 -- not a heuristic sample of them. Used both by
    ``pytest_sessionstart`` (fail the session before any test runs) and by
    ``test_safety_rails.py`` (prove it currently finds nothing, and prove it
    actually finds something when something is there to find).
    """
    offenders: list[str] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith(
                "test_"
            ):
                continue
            decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
            if "allow_real_port_killer" not in decorators:
                continue
            body = ast.unparse(node)
            if ".serve(" in body and "port=" not in body:
                offenders.append(f"{path.name}::{node.name}")
    return offenders


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail the whole session, up front, if the suite itself contains the
    one shape that has actually destroyed a live server -- never merely
    because something else happens to be running on this host.

    See this module's docstring for why this replaced a host-network probe:
    a probe answers the wrong question ("is 8088 occupied?") and refuses the
    suite even when every test in it is isolated by construction. This asks
    the right one ("does any test still have the exact shape that killed a
    live server before?") and can only ever say yes about code that is
    actually sitting in this repo, not about a process this suite has no
    connection to.
    """
    offenders = _serve_calls_without_pinned_port(Path(__file__).parent)
    if not offenders:
        return

    raise pytest.UsageError(
        "\n"
        "REFUSING TO RUN: the shape that destroyed a live muxplex before is\n"
        "back. These tests opt into the REAL port killer\n"
        "(@pytest.mark.allow_real_port_killer) and call .serve( without\n"
        "pinning port= -- exactly incident 2 in muxplex/tests/conftest.py's\n"
        "module docstring:\n"
        "\n" + "\n".join(f"    {offender}" for offender in offenders) + "\n"
        "\n"
        "Fix: pin an OS-allocated ephemeral port (see the `free_port` "
        "fixture)\n"
        "or drop the marker if the real killer isn't actually needed.\n"
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
def _neutralize_real_uvicorn_run(request, monkeypatch):
    """No test may open a REAL listening socket via ``uvicorn.run`` by accident.

    This is the fixture that actually closes incident 2 (see this module's
    docstring), not merely its symptom: the port killer neutered above stops
    the SIGNAL, but an unmocked, unpinned ``serve()`` would still attempt a
    REAL bind to whatever ``DEFAULT_SETTINGS["port"]`` resolves to (8088). On
    a host where nothing is listening there yet, that bind would SUCCEED --
    ``uvicorn.run`` then blocks forever serving a real, world-reachable
    socket on the default port, hanging the test run instead of merely
    signalling something. Patching this ONE chokepoint makes that
    structurally impossible regardless of what port a test computes, without
    touching ``cli.py`` at all: ``uvicorn.run`` is called nowhere in
    production code except ``cli.serve()`` (verified by grep across
    ``muxplex/``). The two tests that legitimately need a real server
    (``test_bell_causality_integration.py``,
    ``test_bell_hook_delivery_integration.py``) build their own
    ``uvicorn.Config``/``Server`` directly on an OS-allocated ephemeral port
    -- a different API this fixture never touches.

    Every test in this suite that exercises ``cli.serve()``'s real call into
    ``uvicorn.run`` already patches it locally with its own recording fake
    (see test_cli.py's ``fake_run`` helpers) -- that local patch simply
    overrides this default for the lifetime of its own ``with patch(...)``
    block, so nothing here conflicts with them. This fixture only ever fires
    for a test that forgot to.

    Tests that genuinely need the real thing opt in explicitly:

        @pytest.mark.allow_real_uvicorn_run

    and must still pin an OS-allocated port (see the ``free_port`` fixture)
    -- opting in exempts a test from this ONE fixture, not from incident 2's
    other half (the port killer above, and settings/tmux isolation, still
    apply regardless).
    """
    if "allow_real_uvicorn_run" in request.keywords:
        yield
        return

    def _refuse_real_bind(app, *, host="127.0.0.1", port=8088, **kwargs):
        raise AssertionError(
            f"A test reached the REAL uvicorn.run(host={host!r}, "
            f"port={port!r}) without @pytest.mark.allow_real_uvicorn_run. "
            f"This suite never opens a real listening socket by accident -- "
            f"patch uvicorn.run (or muxplex.cli.serve) locally the way every "
            f"other cli.py test does, or add the marker and pin an "
            f"OS-allocated port (see the `free_port` fixture) if this test "
            f"genuinely needs a live server."
        )

    monkeypatch.setattr("uvicorn.run", _refuse_real_bind)
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


@pytest.fixture
def free_port() -> int:
    """An OS-allocated free TCP port on 127.0.0.1.

    Bind port 0, read back what the OS actually assigned, release it
    immediately. A port picked this way cannot collide with a real listener
    (the live muxplex on 8088, or anything else) by construction -- unlike
    scanning upward from a well-known port number and hoping it's free by
    the time you bind it for real. This is the canonical version of the
    identical technique already duplicated locally as ``_free_port()`` in
    ``test_bell_causality_integration.py`` and
    ``test_bell_hook_delivery_integration.py``; use this fixture for any NEW
    test that needs to bind a real socket, including one opting into
    ``@pytest.mark.allow_real_uvicorn_run`` above.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

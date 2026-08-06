"""Meta-tests: the safety rails must not be silently removable.

``conftest.py`` documents why these rails exist (a live settings.json
destroyed; a live server SIGTERMed 12+ times by the suite itself). Those
guards only help if they survive contact with future contributors who have
none of that context. These tests fail loudly if a rail is deleted, renamed,
or quietly weakened.

If you are here because one of these failed: read ``conftest.py`` first. The
rails are not ceremony -- each one maps to real damage that actually happened.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).parent / "conftest.py"


def test_conftest_exists():
    """The safety-rail file itself must be present."""
    assert _CONFTEST.is_file(), (
        "muxplex/tests/conftest.py is missing. It holds the guards that stop "
        "this suite from destroying a live muxplex. Restore it."
    )


def test_session_guard_is_installed():
    """``pytest_sessionstart`` must refuse to run against a live server."""
    from . import conftest as ct

    assert hasattr(ct, "pytest_sessionstart"), (
        "conftest.pytest_sessionstart was removed. That hook is what refuses "
        "to run the suite when something is already serving the default port."
    )
    src = inspect.getsource(ct.pytest_sessionstart)
    assert "UsageError" in src or "exit" in src, (
        "pytest_sessionstart no longer aborts. It must FAIL, not warn -- a "
        "warning gets scrolled past and the server dies anyway."
    )


def test_override_requires_explicit_optin():
    """The escape hatch must be explicit, not a default-on convenience."""
    from . import conftest as ct

    assert ct._OVERRIDE_ENV == "MUXPLEX_TEST_ALLOW_LIVE_HOST"
    src = inspect.getsource(ct.pytest_sessionstart)
    assert '== "1"' in src, "override must require an exact opt-in value"


def test_port_killer_is_neutralized_by_default():
    """A test that does not opt in must not reach the real port killer."""
    import muxplex.cli as cli_mod

    # This test carries no marker, so the autouse fixture is active.
    assert cli_mod._kill_stale_port_holder(8088) is None
    assert cli_mod._kill_stale_port_holder.__name__ == "<lambda>", (
        "The autouse _neutralize_port_killer fixture is not active. Without "
        "it, any test calling serve() without pinning a port runs "
        "`lsof -ti :8088` and SIGTERMs whatever owns it -- which is how this "
        "suite killed a developer's live server repeatedly."
    )


@pytest.mark.allow_real_port_killer
def test_optin_marker_restores_the_real_function():
    """The opt-in marker must actually hand back the real implementation."""
    import muxplex.cli as cli_mod

    assert cli_mod._kill_stale_port_holder.__name__ == "_kill_stale_port_holder"


def test_settings_path_is_isolated(tmp_path):
    """Every test must get a temp SETTINGS_PATH, isolated by default."""
    import muxplex.settings as settings_mod

    p = Path(str(settings_mod.SETTINGS_PATH))
    assert ".config/muxplex" not in p.as_posix(), (
        f"SETTINGS_PATH points at the real user config ({p}). A test that "
        f"writes settings would overwrite the developer's live configuration "
        f"-- which is exactly what destroyed an 8-view production config."
    )


def test_tmux_socket_dir_is_isolated_by_default():
    """Every test's real tmux subprocess calls must default to an isolated
    TMUX_TMPDIR, never the ambient one.

    Regression guard for the bell-hook delivery-proof incident: a script
    that called the real ``_arm_bell_hook()`` (tmux ``set-hook -g``, global
    to the whole tmux server) without overriding ``TMUX_TMPDIR`` reached the
    OWNER'S REAL tmux server, because the ambient shell already had it
    exported. This asserts the autouse isolation fixture is actually active
    and actually not the ambient default.
    """
    import os

    from . import conftest as ct

    assert hasattr(ct, "_isolate_tmux_socket_dir"), (
        "conftest._isolate_tmux_socket_dir was removed. That autouse fixture "
        "is what stops a test's real tmux calls (e.g. _arm_bell_hook's "
        "`set-hook -g`) from reaching the ambient/production tmux server."
    )
    tmpdir = os.environ.get("TMUX_TMPDIR", "")
    assert tmpdir, "TMUX_TMPDIR must be set (to an isolated dir) during tests"
    assert "tmux-isolated" in tmpdir, (
        f"TMUX_TMPDIR ({tmpdir!r}) does not look like the isolated per-test "
        f"directory -- the autouse fixture may have been weakened."
    )
    assert "TMUX" not in os.environ, (
        "TMUX must be unset during tests -- tmux prioritizes $TMUX over "
        "TMUX_TMPDIR when resolving which server socket to talk to, so a "
        "leaked $TMUX would silently defeat the isolation above."
    )


def test_no_test_calls_serve_without_a_guard():
    """Structural scan: catch careless NEW tests, not just the known six.

    Any test calling ``serve()`` must either pin a port or rely on the autouse
    neutralizer. Since the neutralizer is autouse, the only way to reach the
    real killer is the opt-in marker -- so a test that BOTH opts in AND calls
    serve() without pinning a port is the dangerous shape.
    """
    src = (Path(__file__).parent / "test_cli.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        decorators = ast.unparse(ast.Module(body=[], type_ignores=[]))
        decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
        if "allow_real_port_killer" not in decorators:
            continue
        body = ast.unparse(node)
        if ".serve(" in body and "port=" not in body:
            offenders.append(node.name)
    assert not offenders, (
        f"These tests opt into the real port killer AND call serve() without "
        f"pinning a port, so they will target the production default port: "
        f"{offenders}. Pin a scratch port or drop the marker."
    )


# ---------------------------------------------------------------------------
# Standing rule: muxplex must never emit anything that renders on a user's
# terminal (see AGENTS.md). This has been learned twice, in the same file,
# by the same class of fix: a loud persistent bell hook painted curl errors
# onto the owner's live panes, and the FIX for that (an arm-time delivery
# probe) was itself a diagnostic `tmux run-shell` call that reproduced the
# identical incident during restart windows. The probe was removed rather
# than re-silenced -- this guard makes sure a FUTURE diagnostic `run-shell`
# (a new probe, a new health check, anything) cannot be added back without
# this suite catching it immediately, rather than waiting to be discovered
# on a live host a third time.
# ---------------------------------------------------------------------------


def test_no_diagnostic_tmux_run_shell_construction_exists():
    """Structural scan of production source: exactly ONE place may ever
    build a `run-shell` command string, and it must be the persistent bell
    hook's own registration call inside `_arm_bell_hook()` -- never a
    diagnostic, probe, or health-check call.

    This scans the actual muxplex/*.py source tree (not just main.py) so a
    future diagnostic added to any module is caught, not just a regression
    in the one file this incident happened in twice.
    """
    package_dir = Path(__file__).parent.parent
    offenders: list[str] = []
    for path in sorted(package_dir.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            # Look for a string literal that IS (or begins) an actual
            # `run-shell` command argument -- e.g. the exact positional arg
            # `"run-shell"` passed to run_tmux(), or an f-string's leading
            # literal segment `"run-shell '"`. Deliberately `.startswith()`,
            # NOT a bare substring match: docstrings and comments legitimately
            # DISCUSS `run-shell` in prose (e.g. this file's own module
            # docstring, or a docstring explaining WHY a command is silent)
            # without ever constructing one, and a substring match would
            # flag every one of those as a false positive.
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.strip().startswith("run-shell")
            ):
                offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")

    # Exactly one production call site is allowed: main.py's registration
    # string inside _arm_bell_hook(), built as
    # f"run-shell '{_bell_hook_curl(...)}'". Anything else -- a second
    # occurrence anywhere, in any module -- is a new diagnostic/probe call
    # site and must be rejected outright, not silenced.
    assert len(offenders) == 1, (
        f"Expected exactly ONE `run-shell` construction site in production "
        f"source (the persistent bell hook's registration string), found "
        f"{len(offenders)}: {offenders}. A second `run-shell` call site is "
        f"almost certainly a new diagnostic/probe -- see AGENTS.md's "
        f"'never render to a pane' rule. tmux's `run-shell` paints a "
        f"background command's output onto a live client's active pane; "
        f"this has caused real, repeated production incidents. Server "
        f"diagnostics belong in the log, `GET /api/instance-info`, and "
        f"`muxplex doctor` -- never behind a new `run-shell` call."
    )
    assert "main.py" in offenders[0], (
        f"the sole run-shell construction site moved out of main.py: "
        f"{offenders[0]!r} -- verify this is still the persistent hook's "
        f"registration string, not a relocated diagnostic."
    )

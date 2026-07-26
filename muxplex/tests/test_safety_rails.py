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

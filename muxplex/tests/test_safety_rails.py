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


def _run_shell_construction_sites() -> list[str]:
    """RECURSIVE structural scan of production source for `run-shell`
    construction sites, returned as package-relative paths.

    Recursion is load-bearing, not cosmetic (plan §7.3 rail 1): the
    pre-S1 version of this scan used ``package_dir.glob("*.py")`` --
    non-recursive -- so the moment the bell code moved into the library
    subpackage, the moved construction site would have silently left the
    rail's coverage while the rail kept passing. S3 (the ``git mv`` to
    ``lib/tmux_kit/``) repeats that hazard one level up: the library is no
    longer under the ``muxplex`` package at all, so the scan now covers
    BOTH trees explicitly -- the ``muxplex`` app package AND
    ``lib/tmux_kit/`` -- and the assertions below FAIL
    (expected-one-found-zero) if either tree ever drops out, because the
    sole legal site lives in ``lib/tmux_kit/bell.py``.

    ``muxplex/tests/`` is excluded: this rail is about PRODUCTION source
    (test files legitimately quote hook strings in assertions).

    Returned paths are REPO-relative (``muxplex/...``,
    ``lib/tmux_kit/...``) so the app/library split is visible to the
    assertions.
    """
    repo_root = Path(__file__).parent.parent.parent
    scan_roots = [repo_root / "muxplex", repo_root / "lib" / "tmux_kit"]
    for root in scan_roots:
        assert root.is_dir(), (
            f"run-shell rail scan root missing: {root} -- if a package "
            f"moved, retarget this rail in the SAME commit (plan §7.3)."
        )
    offenders: list[str] = []
    for path in sorted(p for root in scan_roots for p in root.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        if rel.startswith("muxplex/tests/"):
            continue
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
                offenders.append(f"{rel}:{node.lineno}: {node.value!r}")
    return offenders


def test_no_diagnostic_tmux_run_shell_construction_exists():
    """Structural scan of production source: exactly ONE place may ever
    build a `run-shell` command string, and it must be the library's
    `build_alert_bell_hook()` (lib/tmux_kit/bell.py) -- never a
    diagnostic, probe, or health-check call.

    This scans BOTH production trees recursively (rglob, see
    `_run_shell_construction_sites`) -- the muxplex app package AND the
    `lib/tmux_kit/` workspace member the S3 extraction created -- so a
    future diagnostic added to any module in either tree is caught, not
    just a regression in the one file this incident happened in twice.
    """
    offenders = _run_shell_construction_sites()

    # Exactly one production call site is allowed: the library's
    # build_alert_bell_hook() in lib/tmux_kit/bell.py, built as
    # f"run-shell '{command}'". Anything else -- a second occurrence
    # anywhere, in any module -- is a new diagnostic/probe call site and
    # must be rejected outright, not silenced.
    assert len(offenders) == 1, (
        f"Expected exactly ONE `run-shell` construction site in production "
        f"source (the library's build_alert_bell_hook), found "
        f"{len(offenders)}: {offenders}. A second `run-shell` call site is "
        f"almost certainly a new diagnostic/probe -- see AGENTS.md's "
        f"'never render to a pane' rule. tmux's `run-shell` paints a "
        f"background command's output onto a live client's active pane; "
        f"this has caused real, repeated production incidents. Server "
        f"diagnostics belong in the log, `GET /api/instance-info`, and "
        f"`muxplex doctor` -- never behind a new `run-shell` call."
    )
    assert offenders[0].startswith("lib/tmux_kit/bell.py"), (
        f"the sole run-shell construction site moved out of "
        f"lib/tmux_kit/bell.py: {offenders[0]!r} -- verify this is still "
        f"the library's build_alert_bell_hook() (the one API that wraps a "
        f"caller-supplied, always-silent command), not a relocated "
        f"diagnostic."
    )


def test_app_code_builds_zero_run_shell_strings():
    """The §3.2 two-rail tightening (plan §7.3): with the construction
    site moved behind the library's `build_alert_bell_hook()`, APP-level
    code (everything in the muxplex package OUTSIDE `lib/tmux_kit/`) is
    allowed ZERO `run-shell` construction sites -- main.py included.

    Pre-S1, muxplex was allowed one (main.py's inline f-string). Post-S1
    it is allowed none: the one legal construction lives behind a library
    API that has no loudness parameter. This pair of assertions is a
    strictly STRONGER invariant than the old single-site rule.
    """
    offenders = _run_shell_construction_sites()
    app_offenders = [o for o in offenders if not o.startswith("lib/tmux_kit/")]
    assert not app_offenders, (
        f"App-level code must never build a `run-shell` string itself -- "
        f"found {app_offenders}. Call "
        f"tmux_kit.bell.build_alert_bell_hook(<always-silent command>) "
        f"instead, and read AGENTS.md's 'never render to a pane' rule "
        f"before doing even that."
    )


def _tmux_library_app_imports() -> list[str]:
    """AST scan of every ``.py`` under ``lib/tmux_kit/`` for imports that
    reach the app layer, returned as package-relative offender strings.

    Three shapes are offenses, because each is a way the boundary could
    erode back into ``load_settings()``-style coupling without this rail
    firing:

    - ``from muxplex.<any module> import ...`` (absolute ImportFrom)
    - ``import muxplex`` / ``import muxplex.<any module>`` (plain Import)
    - a RELATIVE import whose level climbs OUT of the ``lib/tmux_kit/``
      package (e.g. ``from .. import anything`` from ``tmux_kit/proc.py`` --
      post-S3 the parent directory is ``lib/``, not even a package, but the
      shape is still the boundary-erosion shape and stays an offense)

    ``tmux_kit[.*]`` itself is allowed -- library-internal imports are the
    point of the package. Pre-S3 this rail had to carve a ``muxplex.tmux``
    self-import exception; post-S3 (the ``git mv`` to ``lib/``) ANY
    ``muxplex`` import is an offense, full stop -- the library is a
    separate distribution and a ``muxplex`` import would be a literal
    circular dependency, not just a boundary leak.
    """
    lib_dir = Path(__file__).parent.parent.parent / "lib" / "tmux_kit"
    assert lib_dir.is_dir(), (
        f"import-purity rail scan root missing: {lib_dir} -- if the "
        f"library moved, retarget this rail in the SAME commit (plan §7.3)."
    )
    offenders: list[str] = []
    for path in sorted(lib_dir.rglob("*.py")):
        rel_parts = path.relative_to(lib_dir).parts
        rel = "lib/tmux_kit/" + "/".join(rel_parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    mod = node.module or ""
                    if mod == "muxplex" or mod.startswith("muxplex."):
                        offenders.append(f"{rel}:{node.lineno}: from {mod} import ...")
                elif node.level >= len(rel_parts) + 1:
                    # For a module at tmux_kit/<f>.py (depth 1), level 1 is
                    # the tmux_kit package itself (allowed); level 2 climbs
                    # out of the package (offense). Generalized for any
                    # future sub-package depth.
                    offenders.append(
                        f"{rel}:{node.lineno}: relative import escapes "
                        f"lib/tmux_kit/ (level={node.level})"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name == "muxplex" or name.startswith("muxplex."):
                        offenders.append(f"{rel}:{node.lineno}: import {name}")
    return offenders


def test_tmux_library_never_imports_the_app_layer():
    """The plan §7.2 import-purity rail, landed with the S2 inversion
    (which removed the last wrong-way arrow, proc.py's ``load_settings``
    read -- the rail could not exist before S2 without being born red).

    Nothing under ``lib/tmux_kit/`` may import from the muxplex app layer
    (``muxplex.settings``, ``muxplex.state``, ``muxplex.main``, ...). This
    is the entire value of the internal boundary: it converts "we intend a
    library" into "a library that cannot silently grow an app dependency,"
    which is what makes stage S3's ``git mv`` to ``lib/`` mechanical
    instead of archaeological. Configuration reaches the library by
    INJECTION only (plan §4.3): ``tmux_env(socket_dir)``,
    ``spawn_session(..., env=...)``, ``set_env_factory()``.
    """
    offenders = _tmux_library_app_imports()
    assert not offenders, (
        f"lib/tmux_kit/ (the extractable tmux library) imports the app "
        f"layer: {offenders}. The library must never read muxplex's "
        f"settings, state, or server code -- config is injected by the "
        f"caller (plan §4.3, §7.2). Resolve the value app-side (see "
        f"muxplex/sessions.py, the app facade) and pass it in as a "
        f"parameter or via tmux_kit.proc.set_env_factory()."
    )


def _should_escape_call_site_modules() -> set[str]:
    """AST scan of BOTH production trees for modules that CALL
    ``should_escape()`` -- the muxplex app package (tests excluded) and
    the ``lib/tmux_kit/`` workspace member -- returned as importable
    module names (``muxplex.ttyd``, ``tmux_kit.spawn``, ...).

    A *call site* is what matters, not an import: conftest's
    ``_default_cgroup_escape_disabled`` neutralizes the escape by patching
    the attribute in each CALLING module's namespace (from-import binding),
    so the set of callers is exactly the set of modules that fixture must
    patch to be effective.
    """
    app_pkg = Path(__file__).parent.parent
    lib_pkg = Path(__file__).parent.parent.parent / "lib" / "tmux_kit"
    assert lib_pkg.is_dir(), (
        f"should_escape rail scan root missing: {lib_pkg} -- if the "
        f"library moved, retarget this rail in the SAME commit."
    )

    roots = [("muxplex", app_pkg), ("tmux_kit", lib_pkg)]
    callers: set[str] = set()
    for pkg_name, root in roots:
        for path in sorted(root.rglob("*.py")):
            rel_parts = path.relative_to(root).parts
            if pkg_name == "muxplex" and rel_parts[0] == "tests":
                continue  # tests are not production call sites
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name == "should_escape":
                    mod_parts = [pkg_name, *rel_parts[:-1]]
                    if path.name != "__init__.py":
                        mod_parts.append(path.stem)
                    callers.add(".".join(mod_parts))
    return callers


def test_cgroup_escape_default_covers_every_live_call_site():
    """conftest's ``_default_cgroup_escape_disabled`` autouse fixture must
    patch ``should_escape`` in EXACTLY the modules that actually call it.

    INCIDENT (2026-08-08): the S1-S3 extraction moved the spawn body from
    ``muxplex.sessions`` into ``tmux_kit.spawn``, the fixture's hardcoded
    patch list went stale, and its ``except AttributeError: pass`` swallowed
    the miss. On CI's Linux runners (a usable systemd --user session --
    unlike macOS or the DTU container) every session-create test then ran
    its template FOR REAL through ``systemd-run --user --scope`` and never
    hit the test's ``create_subprocess_shell`` mock: five test_api tests
    red on CI, green everywhere the extraction had been verified. This rail
    makes the next code move fail here, loudly, naming the drift --
    instead of failing four environment-dependent CI jobs later.
    """
    from . import conftest as ct

    actual = _should_escape_call_site_modules()
    declared = set(ct.SHOULD_ESCAPE_BINDING_MODULES)
    assert actual == declared, (
        f"conftest.SHOULD_ESCAPE_BINDING_MODULES is out of sync with the "
        f"production call sites of should_escape(). Declared but no longer "
        f"calling: {sorted(declared - actual)}; calling but NOT patched by "
        f"the autouse default (these hit the REAL systemd-run probe on any "
        f"host with a usable systemd --user session): "
        f"{sorted(actual - declared)}. Update the constant in conftest.py "
        f"in the SAME commit as the code move -- and never re-add the "
        f"try/except swallow that hid this in 2026-08-08's CI failure."
    )


def test_library_tests_live_under_the_railed_tests_dir():
    """Plan §7.3 rail 2: `test_settings_path_is_isolated` and
    `_isolate_tmux_socket_dir` (conftest.py's autouse rails) must keep
    applying to library code that still shells out to real tmux. They do
    so because ALL tests -- the library's included -- live under
    ``muxplex/tests/``, where that conftest governs. A test module placed
    inside ``lib/tmux_kit/`` would silently escape every autouse rail
    (settings isolation, TMUX_TMPDIR isolation, the port-killer
    neutralizer), which is exactly the "moved code leaves its guard's
    coverage" failure shape rail 1 above just closed for the AST scan.
    """
    tmux_pkg = Path(__file__).parent.parent.parent / "lib" / "tmux_kit"
    assert tmux_pkg.is_dir(), "lib/tmux_kit/ library package is missing"
    strays = sorted(
        p.relative_to(tmux_pkg).as_posix()
        for p in tmux_pkg.rglob("*.py")
        if p.name.startswith("test_") or p.name == "conftest.py"
    )
    assert not strays, (
        f"Test files found inside lib/tmux_kit/: {strays}. Library tests "
        f"must live in muxplex/tests/ so conftest.py's autouse safety "
        f"rails (isolated SETTINGS_PATH, isolated TMUX_TMPDIR, neutralized "
        f"port killer) apply to them -- see plan §7.3 rail 2."
    )

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
import socket
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
    """``pytest_sessionstart`` must still exist and still fail LOUD -- now
    for a structural reason (see conftest.py's module docstring: it used to
    probe the host's network, and now scans the test suite's own source),
    not a network probe."""
    from . import conftest as ct

    assert hasattr(ct, "pytest_sessionstart"), (
        "conftest.pytest_sessionstart was removed. That hook is what refuses "
        "the whole session if the suite itself reintroduces the exact test "
        "shape that destroyed a live server before (see incident 2 in "
        "conftest.py's module docstring)."
    )
    src = inspect.getsource(ct.pytest_sessionstart)
    assert "UsageError" in src, (
        "pytest_sessionstart no longer aborts. It must FAIL, not warn -- a "
        "warning gets scrolled past and the server dies anyway."
    )


def test_session_guard_does_not_refuse_the_normal_case():
    """The guard must never refuse merely because this repo, as it stands,
    is clean -- proving it answers 'does a dangerous test exist', never 'is
    something else running on this host'. This is what makes the suite
    RUNNABLE on a host serving a live muxplex on the default port, which the
    retired network-probe guard could never do.

    (The real suite having zero offenders is proven independently by
    ``test_no_dangerous_serve_calls_exist_anywhere_in_the_suite`` below; this
    test additionally proves the hook itself does not raise given that.)
    """
    from . import conftest as ct

    ct.pytest_sessionstart(session=None)  # type: ignore[arg-type]


def test_session_guard_refuses_when_the_dangerous_shape_exists(monkeypatch):
    """Prove the backstop actually catches the shape it claims to, rather
    than being vacuously satisfied because the real repo happens to be
    clean right now."""
    from . import conftest as ct

    monkeypatch.setattr(
        ct,
        "_serve_calls_without_pinned_port",
        lambda tests_dir: ["test_synthetic.py::test_offender"],
    )
    with pytest.raises(pytest.UsageError, match="test_offender"):
        ct.pytest_sessionstart(session=None)  # type: ignore[arg-type]


def test_structural_guard_has_no_bypass():
    """Unlike the retired network-probe guard, this one must not be
    overridable by an environment variable. There is no legitimate reason to
    run a suite that contains the dangerous shape -- only a reason to fix
    it -- so, unlike the old guard, this one gets no escape hatch."""
    from . import conftest as ct

    assert not hasattr(ct, "_OVERRIDE_ENV"), (
        "conftest._OVERRIDE_ENV reappeared. The structural guard must never "
        "have an escape hatch: a test either has the dangerous shape (fix "
        "it) or it doesn't (nothing to override)."
    )
    src = inspect.getsource(ct.pytest_sessionstart)
    assert "os.environ" not in src, (
        "pytest_sessionstart must not read any environment variable to "
        "decide whether to run."
    )


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


def test_real_uvicorn_run_is_neutralized_by_default():
    """A test that does not opt in must not be able to open a real socket
    via ``uvicorn.run`` -- the fixture that actually closes incident 2 (see
    conftest.py's module docstring): the port killer alone stops the
    SIGNAL, but an unmocked, unpinned ``serve()`` would still attempt a REAL
    bind without this."""
    import uvicorn

    assert uvicorn.run.__name__ == "_refuse_real_bind", (
        "The autouse _neutralize_real_uvicorn_run fixture is not active. "
        "Without it, an unmocked, unpinned serve() can open a REAL "
        "listening socket on the default port."
    )
    with pytest.raises(AssertionError, match="allow_real_uvicorn_run"):
        uvicorn.run(object(), host="127.0.0.1", port=8088)  # type: ignore[arg-type]


@pytest.mark.allow_real_uvicorn_run
def test_optin_marker_restores_the_real_uvicorn_run():
    """The opt-in marker must actually hand back the real ``uvicorn.run``,
    not the neutered stub."""
    import uvicorn

    assert uvicorn.run.__name__ == "run", (
        "@pytest.mark.allow_real_uvicorn_run must hand back the real uvicorn.run."
    )


def test_free_port_fixture_returns_a_bindable_ephemeral_port(free_port):
    """The ``free_port`` fixture must hand back a port THIS process can
    immediately bind -- proving it's a real, currently-unused ephemeral
    port an OS actually allocated, not just a plausible-looking integer."""
    assert isinstance(free_port, int)
    assert 1024 < free_port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", free_port))  # must not raise


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
    rail's coverage while the rail kept passing.

    **Post tmux-kit cutover (docs/plans/2026-08-09-tmuxkit-own-repo-and-
    pypi-plan.md §3.3/§5): this repo no longer hosts ``lib/tmux_kit/`` at
    all** -- the one legal construction site (the library's
    ``build_alert_bell_hook()``) now lives in ``bkrabach/tmux-kit``, whose
    own suite (``tests/test_rails.py::test_exactly_one_run_shell_construction_site_exists``)
    is the rail for it. This scan covers ONLY the ``muxplex`` app package,
    and the invariant TIGHTENS to zero: app code must never construct a
    `run-shell` string itself, full stop -- it can only ever reach one by
    calling the library's API.

    ``muxplex/tests/`` is excluded: this rail is about PRODUCTION source
    (test files legitimately quote hook strings in assertions).

    Returned paths are REPO-relative (``muxplex/...``).
    """
    repo_root = Path(__file__).parent.parent.parent
    scan_roots = [repo_root / "muxplex"]
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


def test_app_code_builds_zero_run_shell_strings():
    """The §3.2 two-rail tightening (plan §7.3), TIGHTENED AGAIN at the
    tmux-kit cutover (docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-
    plan.md §3.3/§5): with ``lib/tmux_kit/`` no longer part of this repo
    at all, the scan (`_run_shell_construction_sites`) covers only the
    ``muxplex`` app package, and it is allowed ZERO `run-shell`
    construction sites -- main.py included. The one legal construction
    (the library's `build_alert_bell_hook()`) now lives in, and is railed
    by, `bkrabach/tmux-kit`'s own suite
    (`tests/test_rails.py::test_exactly_one_run_shell_construction_site_exists`).

    Pre-S1, muxplex was allowed one (main.py's inline f-string). Post-S1,
    one legal site behind a library API. Post-cutover, none at all in
    THIS repo -- a strictly monotonic tightening at every stage.
    """
    offenders = _run_shell_construction_sites()
    assert not offenders, (
        f"App-level code must never build a `run-shell` string itself -- "
        f"found {offenders}. Call "
        f"tmux_kit.bell.build_alert_bell_hook(<always-silent command>) "
        f"instead, and read AGENTS.md's 'never render to a pane' rule "
        f"before doing even that."
    )


# ---------------------------------------------------------------------------
# RETIRED at the tmux-kit cutover (docs/plans/2026-08-09-tmuxkit-own-repo-
# and-pypi-plan.md §3.3/§5): `test_tmux_library_never_imports_the_app_layer`
# (the import-purity rail) and its `_tmux_library_app_imports` AST scan used
# to live here, scanning `lib/tmux_kit/` for imports that reached back into
# the muxplex app layer. That directory no longer exists in this repo -- the
# library is `bkrabach/tmux-kit`'s own distribution now, and the identical
# rail travelled with it verbatim: see that repo's
# `tests/test_rails.py::test_library_is_import_pure_stdlib_and_self_only`.
# Retired by replacement, not silent deletion (this repo's own discipline,
# per `test_library_tests_live_under_the_railed_tests_dir` below) --
# `test_tmux_kit_contract.py` is this repo's replacement tripwire for
# cross-repo drift between what muxplex assumes and what tmux-kit ships.
# ---------------------------------------------------------------------------


def _should_escape_call_site_modules() -> set[str]:
    """AST scan of the muxplex app package for modules that CALL
    ``should_escape()``, returned as importable module names
    (``muxplex.ttyd``, ...).

    **Post tmux-kit cutover:** this scans ONLY ``muxplex`` (tests
    excluded) -- ``tmux_kit.spawn``'s own call site now lives in
    ``bkrabach/tmux-kit``, a separate installed distribution with no
    local source tree for this AST scan to walk. Its entry in
    ``conftest.SHOULD_ESCAPE_BINDING_MODULES`` is therefore NOT
    reconcilable against this scan -- it stays declared (the fixture
    still must patch the installed library's binding, or spawning a
    session in a test with a live systemd --user session would shell out
    for real) but is excluded from the comparison in
    `test_cgroup_escape_default_covers_every_live_call_site` below, whose
    docstring explains why.

    A *call site* is what matters, not an import: conftest's
    ``_default_cgroup_escape_disabled`` neutralizes the escape by patching
    the attribute in each CALLING module's namespace (from-import binding),
    so the set of callers is exactly the set of modules that fixture must
    patch to be effective.
    """
    app_pkg = Path(__file__).parent.parent

    roots = [("muxplex", app_pkg)]
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
    # Post-cutover: only muxplex.* call sites are locally re-derivable (see
    # _should_escape_call_site_modules' docstring) -- tmux_kit.spawn's own
    # call site lives in bkrabach/tmux-kit's source, not this repo's, so it
    # is excluded from this comparison rather than silently dropped from
    # the constant (dropping it would stop the fixture patching it, and
    # the NEXT session-create test with a usable systemd --user session
    # would shell out for real -- the exact incident this rail exists to
    # catch, just for the half of the call-site set this repo can't see).
    declared = {m for m in ct.SHOULD_ESCAPE_BINDING_MODULES if m.startswith("muxplex.")}
    assert actual == declared, (
        f"conftest.SHOULD_ESCAPE_BINDING_MODULES's muxplex.* entries are "
        f"out of sync with the production call sites of should_escape() "
        f"in THIS repo. Declared but no longer calling: "
        f"{sorted(declared - actual)}; calling but NOT patched by the "
        f"autouse default (these hit the REAL systemd-run probe on any "
        f"host with a usable systemd --user session): "
        f"{sorted(actual - declared)}. Update the constant in conftest.py "
        f"in the SAME commit as the code move -- and never re-add the "
        f"try/except swallow that hid this in 2026-08-08's CI failure."
    )
    assert "tmux_kit.spawn" in ct.SHOULD_ESCAPE_BINDING_MODULES, (
        "conftest.SHOULD_ESCAPE_BINDING_MODULES must keep patching "
        "tmux_kit.spawn.should_escape -- it is a real call site in the "
        "installed tmux-kit library, just not one this repo's AST scan "
        "can verify anymore (bkrabach/tmux-kit owns that source now)."
    )


def test_library_tests_live_under_the_railed_tests_dir():
    """RETIRED at the tmux-kit cutover (docs/plans/2026-08-09-tmuxkit-own-
    repo-and-pypi-plan.md §3.3/§5): this used to assert no stray test/
    conftest files existed inside ``lib/tmux_kit/`` (which would have
    silently escaped this suite's autouse safety rails). ``lib/`` no
    longer exists in this repo at all -- the library's own tests now live
    in, and are railed by, `bkrabach/tmux-kit`'s own suite and its own
    conftest.py (which reimplements the isolation fixtures this repo
    pioneered; see that repo's `tests/conftest.py`). Retired by
    replacement, not silent deletion, per this rail's own stated
    discipline: `test_tmux_kit_contract.py` is what now stands in this
    repo's suite as the drift tripwire between the two.
    """
    assert not (Path(__file__).parent.parent.parent / "lib").exists(), (
        "lib/ reappeared in the muxplex repo -- the tmux-kit cutover "
        "(docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md §5) "
        "deleted it deliberately; the library lives in bkrabach/tmux-kit "
        "now. If this is intentional, this retired rail's premise has "
        "changed again and needs a fresh look, not a quiet revert."
    )
    assert (Path(__file__).parent / "test_tmux_kit_contract.py").is_file(), (
        "test_tmux_kit_contract.py is missing -- it is this repo's "
        "replacement cross-repo drift tripwire for the tmux-kit library "
        "(see its own module docstring)."
    )

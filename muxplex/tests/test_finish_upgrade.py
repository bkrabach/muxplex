"""Tests for muxplex-lf6: the self-upgrade re-exec hardening.

`upgrade()` used to run its post-install steps (ensure_agent, service-file
regeneration, restart, verification) in-process, in the SAME interpreter
that had just overwritten muxplex's own package files on disk. That
process still had the OLD `muxplex.cli` / `muxplex.service` modules
cached in `sys.modules`, so a lazy cross-module import of a name that
only existed in the NEW code (e.g. `service.py`'s own `from muxplex.cli
import ensure_agent`) could resolve against the stale cache instead of
what was just written to disk -- `ImportError: cannot import name
'ensure_agent' from 'muxplex.cli'`, hit for real via `service_install()`.

The fix: `upgrade()` now hands the post-install steps off to a genuinely
fresh subprocess (the hidden `muxplex _finish-upgrade` subcommand),
launched via the just-installed entrypoint
(`_installed_muxplex_entrypoint()`). See `_finish_upgrade()`'s own
docstring in cli.py for the full mechanism.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest


def _pypi_info():
    return {
        "source": "pypi",
        "version": "0.1.0",
        "commit": None,
        "url": None,
        "ref": None,
    }


@pytest.fixture
def upgrade_ready(monkeypatch):
    """Common stubbing so `upgrade()` reaches the post-install handoff
    without touching the real filesystem/network: a pypi source, an
    update available, uv/muxplex resolvable via a mocked `shutil.which`,
    and the version-moved gate satisfied."""
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_installed_version_on_disk", lambda: "99.9.9")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (abc12345 \u2192 def67890)"),
    )
    return cli_mod


# ---------------------------------------------------------------------------
# A. `_finish-upgrade` is registered as a hidden subcommand.
# ---------------------------------------------------------------------------


def test_finish_upgrade_subcommand_registered_and_hidden():
    """`_finish-upgrade` must be a real, dispatchable subcommand but never
    DOCUMENTED in `--help` output -- it must carry no ``help=`` text and no
    entry under "positional arguments", even though argparse itself always
    lists every registered subparser choice in the ``{...}`` choices
    metavar -- TWICE, once in the top "usage:" line and once more as the
    positional argument's own display name under "positional arguments:"
    -- regardless of whether ``help=`` was passed. See argparse's own
    `_SubParsersAction`: that metavar is built from ALL choices
    unconditionally, while a per-choice INDENTED description line (e.g.
    "    serve               Start the server (default)") is only added
    when `help=` is passed to `add_parser`. So "hidden" here means
    undocumented (no description line of its own), not literally absent
    from the output."""
    import inspect
    import io
    import re
    from unittest.mock import patch

    import muxplex.cli as cli_mod

    source = inspect.getsource(cli_mod.main)
    assert '"_finish-upgrade"' in source
    assert "_finish_upgrade()" in source

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                cli_mod.main()
        except SystemExit:
            pass
    help_text = buf.getvalue()
    # Appears exactly twice -- embedded inside the `{...}` choices metavar
    # in the usage line, and again as that same metavar's repeat under
    # "positional arguments:" -- both unavoidable, both never containing
    # whitespace around the name (it sits between commas/braces).
    assert help_text.count("_finish-upgrade") == 2
    # Never appears as its OWN documented entry: every real subcommand's
    # description line starts with only leading whitespace before the
    # name (e.g. "    serve               Start the server"); a
    # `{...,_finish-upgrade,...}` metavar occurrence never starts a line
    # that way, since the name is always preceded by a comma or brace.
    assert re.search(r"^\s*_finish-upgrade\s", help_text, re.MULTILINE) is None


def test_finish_upgrade_subcommand_dispatches_and_exits_with_its_return_code(
    monkeypatch,
):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_finish_upgrade", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["muxplex", "_finish-upgrade"])

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 0

    monkeypatch.setattr(cli_mod, "_finish_upgrade", lambda: 1)
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# B. `_finish_upgrade()`'s own ordering: ensure_agent -> regen -> restart.
# ---------------------------------------------------------------------------


def test_finish_upgrade_runs_ensure_agent_then_regen_then_restart(monkeypatch):
    import muxplex.cli as cli_mod

    calls: list = []
    monkeypatch.setattr(
        cli_mod, "ensure_agent", lambda: calls.append("ensure_agent") or True
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(cli_mod, "_have_systemctl", lambda: True)
    monkeypatch.setattr(
        "muxplex.service.service_install",
        lambda: calls.append("service_install"),
    )

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(cli_mod, "_verify_service_started", lambda: True)
    monkeypatch.setattr(cli_mod, "_wait_for_service_ready", lambda port: True)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr("muxplex.settings.load_settings", lambda: {"port": 8088})

    assert cli_mod._finish_upgrade() == 0

    name_calls = [c for c in calls if isinstance(c, str)]
    assert name_calls == ["ensure_agent", "service_install"]

    restart_idx = next(
        i
        for i, c in enumerate(calls)
        if isinstance(c, list) and c[:3] == ["systemctl", "--user", "is-enabled"]
    )
    assert restart_idx > calls.index("service_install")


# ---------------------------------------------------------------------------
# C. ImportError-regression proxy.
# ---------------------------------------------------------------------------


def test_service_import_chain_resolves_ensure_agent_on_a_fresh_module_load(
    monkeypatch,
):
    """Regression-proxy for the shipped incident: pop BOTH `muxplex.cli`
    and `muxplex.service` out of `sys.modules` (monkeypatch restores the
    originals afterward), re-import both from scratch -- the exact shape a
    brand-new subprocess sees, nothing cached yet -- and actually CALL
    `service_install()` so its own lazy `from muxplex.cli import
    ensure_agent` statement executes for real. It must resolve cleanly and
    must NOT raise `ImportError: cannot import name 'ensure_agent' from
    'muxplex.cli'` (the actual incident this guards against).

    Both `muxplex.cli` and `muxplex.service` are imported fresh here (not
    just `muxplex.service`) and patched via their own fresh module objects
    -- not via the string-form ``monkeypatch.setattr("muxplex.cli.x", ...)``
    -- because that string form resolves through the `muxplex` PACKAGE's
    own `.cli`/`.service` attributes (see `_pytest.monkeypatch.resolve`),
    which is a DIFFERENT piece of global state than `sys.modules`. The
    lazy `from muxplex.cli import ensure_agent` inside `service_install()`
    resolves through `sys.modules` directly, not the package attribute --
    so patching through the string form here would silently patch a
    module `service_install()` never actually looks at.
    """
    import importlib
    import sys as sys_mod

    import muxplex as muxplex_pkg

    # Importing "muxplex.cli"/"muxplex.service" fresh below (while absent
    # from `sys.modules`) has an intrinsic side effect of Python's import
    # machinery: it also repoints the `muxplex` package's own
    # `.cli`/`.service` attributes at these fresh module objects.
    # `monkeypatch.delitem` below only restores the `sys.modules` dict
    # entries afterward -- it knows nothing about these package
    # attributes, so left unrestored they would keep pointing at this
    # test's now-orphaned fresh modules for the rest of the session,
    # silently breaking every later test that resolves
    # `"muxplex.service.something"` via pytest's string-target
    # `monkeypatch.setattr` (which walks this exact package attribute).
    # Save the true originals now and put them back in `finally` below --
    # a plain assignment, not `monkeypatch.setattr`, because monkeypatch's
    # own undo would restore to the value captured AT THE SETATTR CALL
    # (already the polluted fresh module by then), not the true original.
    orig_cli_pkg_attr = getattr(muxplex_pkg, "cli", None)
    orig_service_pkg_attr = getattr(muxplex_pkg, "service", None)

    monkeypatch.delitem(sys_mod.modules, "muxplex.cli", raising=False)
    monkeypatch.delitem(sys_mod.modules, "muxplex.service", raising=False)

    try:
        fresh_service = importlib.import_module("muxplex.service")
        fresh_cli = importlib.import_module("muxplex.cli")

        calls: list = []
        monkeypatch.setattr(
            fresh_cli, "ensure_agent", lambda: calls.append("ensure_agent") or True
        )
        monkeypatch.setattr(fresh_service, "_is_darwin", lambda: False)
        monkeypatch.setattr(fresh_service, "_have_systemctl", lambda: False)
        monkeypatch.setattr(
            fresh_service,
            "_no_systemctl_error",
            lambda cmd: calls.append(f"no_systemctl:{cmd}"),
        )

        fresh_service.service_install()  # must not raise ImportError

        assert calls[0] == "ensure_agent"
    finally:
        muxplex_pkg.cli = orig_cli_pkg_attr  # type: ignore[attr-defined]
        muxplex_pkg.service = orig_service_pkg_attr  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# D-F. `_finish_upgrade()`'s own failure/ordering guarantees.
# ---------------------------------------------------------------------------


def test_finish_upgrade_returns_1_when_service_not_confirmed(monkeypatch):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "ensure_agent", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(cli_mod, "_have_systemctl", lambda: True)
    monkeypatch.setattr("muxplex.service.service_install", lambda: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(cli_mod, "_verify_service_started", lambda: False)

    assert cli_mod._finish_upgrade() == 1


def test_finish_upgrade_restarts_even_if_service_install_raises(monkeypatch):
    """The restart in `finally` must run whether or not the steps before
    it succeeded -- mirrors `upgrade()`'s own original try/finally shape:
    a best-effort restart runs, then the exception still propagates."""
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "ensure_agent", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(cli_mod, "_have_systemctl", lambda: True)

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr("muxplex.service.service_install", boom)

    restart_calls: list = []

    def fake_run(cmd, **kwargs):
        restart_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(cli_mod, "_verify_service_started", lambda: True)

    with pytest.raises(RuntimeError, match="disk full"):
        cli_mod._finish_upgrade()

    assert any(c[:3] == ["systemctl", "--user", "is-enabled"] for c in restart_calls)


def test_finish_upgrade_never_calls_upgrade_itself():
    """`_finish_upgrade()` runs POST-install steps only -- it must never
    recursively call `upgrade()` (which would re-trigger the version
    check / reinstall against a server that is already on the new
    version)."""
    import inspect
    import re

    import muxplex.cli as cli_mod

    source = inspect.getsource(cli_mod._finish_upgrade)
    # `_finish_upgrade()`'s own docstring legitimately mentions `upgrade()`
    # several times as backtick-quoted (Markdown code-span) documentation,
    # e.g. "called ONLY as a fresh subprocess launched by `upgrade()`
    # itself" -- a bare `\bupgrade\(` false-matches those: a backtick is a
    # non-word character, so `\b` fires right between it and the `u`. The
    # negative lookbehind for a backtick excludes every one of those prose
    # references while still catching a genuine `upgrade()` call added to
    # the function body (which would never be backtick-quoted).
    assert re.search(r"(?<!`)\bupgrade\(", source) is None


# ---------------------------------------------------------------------------
# G-K. `upgrade()`'s handoff to `_finish_upgrade()`.
# ---------------------------------------------------------------------------


def test_upgrade_hands_off_to_installed_entrypoint(upgrade_ready, monkeypatch):
    cli_mod = upgrade_ready
    monkeypatch.setattr(
        cli_mod, "_installed_muxplex_entrypoint", lambda: ["/opt/x/muxplex"]
    )

    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cli_mod.upgrade()

    handoff_calls = [c for c in calls if c == ["/opt/x/muxplex", "_finish-upgrade"]]
    assert len(handoff_calls) == 1


def test_upgrade_does_not_run_post_install_in_process(upgrade_ready, monkeypatch):
    """`ensure_agent()` and `service_install()` must never be called
    directly from `upgrade()` anymore -- only reachable (in production)
    inside the CHILD process via the `_finish-upgrade` handoff, which
    here is just a mocked `subprocess.run` call, never actually
    executed."""
    cli_mod = upgrade_ready
    monkeypatch.setattr(
        cli_mod, "_installed_muxplex_entrypoint", lambda: ["/opt/x/muxplex"]
    )

    def fail_ensure_agent():
        raise AssertionError(
            "upgrade() must not call ensure_agent() in-process anymore"
        )

    monkeypatch.setattr(cli_mod, "ensure_agent", fail_ensure_agent)

    def fail_service_install():
        raise AssertionError(
            "upgrade() must not call service_install() in-process anymore"
        )

    monkeypatch.setattr("muxplex.service.service_install", fail_service_install)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    cli_mod.upgrade()  # must not raise


def test_upgrade_no_double_restart_when_handoff_succeeds(upgrade_ready, monkeypatch):
    cli_mod = upgrade_ready
    monkeypatch.setattr(
        cli_mod, "_installed_muxplex_entrypoint", lambda: ["/opt/x/muxplex"]
    )

    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cli_mod.upgrade()

    # upgrade()'s OWN post-install restart logic (is-enabled / daemon-reload
    # / start / reset-failed) must be fully suppressed once the handoff
    # ran -- that logic lives in the CHILD (_finish_upgrade()), which here
    # is just the single mocked handoff call above, never actually run.
    restart_shaped = [
        c
        for c in calls
        if len(c) >= 3
        and c[0] == "systemctl"
        and c[2] in ("is-enabled", "daemon-reload", "start", "reset-failed")
    ]
    assert restart_shaped == []


def test_upgrade_restarts_via_parent_when_entrypoint_missing(
    upgrade_ready, monkeypatch
):
    """F1: no launchable entrypoint -- the parent's OWN restart logic
    must still run (best-effort), and upgrade() must exit 1."""
    cli_mod = upgrade_ready
    monkeypatch.setattr(cli_mod, "_installed_muxplex_entrypoint", lambda: None)
    monkeypatch.setattr(cli_mod, "_verify_service_started", lambda: True)

    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.upgrade()
    assert exc_info.value.code == 1

    restart_shaped = [
        c for c in calls if len(c) >= 3 and c[0] == "systemctl" and c[2] == "is-enabled"
    ]
    assert len(restart_shaped) == 1  # the parent's own restart logic ran


def test_upgrade_restarts_via_parent_when_entrypoint_unlaunchable(
    upgrade_ready, monkeypatch
):
    """F1 (variant): entrypoint resolved but launching it raised OSError --
    same handling as entrypoint is None."""
    cli_mod = upgrade_ready
    monkeypatch.setattr(
        cli_mod, "_installed_muxplex_entrypoint", lambda: ["/opt/x/muxplex"]
    )
    monkeypatch.setattr(cli_mod, "_verify_service_started", lambda: True)

    def fake_run(cmd, **kwargs):
        if cmd == ["/opt/x/muxplex", "_finish-upgrade"]:
            raise OSError("permission denied")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.upgrade()
    assert exc_info.value.code == 1


def test_upgrade_exits_1_when_finish_upgrade_child_fails(upgrade_ready, monkeypatch):
    """F2: the handoff ran but the child exited non-zero -- upgrade()
    must exit 1 and must NOT restart a second time itself."""
    cli_mod = upgrade_ready
    monkeypatch.setattr(
        cli_mod, "_installed_muxplex_entrypoint", lambda: ["/opt/x/muxplex"]
    )

    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd == ["/opt/x/muxplex", "_finish-upgrade"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.upgrade()
    assert exc_info.value.code == 1

    restart_shaped = [
        c for c in calls if len(c) >= 3 and c[0] == "systemctl" and c[2] == "is-enabled"
    ]
    assert restart_shaped == []  # no double-restart even on child failure


# ---------------------------------------------------------------------------
# L. `_installed_muxplex_entrypoint()` precedence.
# ---------------------------------------------------------------------------


def test_installed_muxplex_entrypoint_prefers_path(monkeypatch):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/local/bin/muxplex" if name == "muxplex" else None,
    )
    assert cli_mod._installed_muxplex_entrypoint() == ["/usr/local/bin/muxplex"]


def test_installed_muxplex_entrypoint_falls_back_to_local_bin(monkeypatch, tmp_path):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(shutil, "which", lambda name: None)
    local_bin_dir = tmp_path / ".local" / "bin"
    local_bin_dir.mkdir(parents=True)
    local_bin = local_bin_dir / "muxplex"
    local_bin.write_text("#!/bin/sh\necho hi\n")
    local_bin.chmod(0o755)
    monkeypatch.setattr(cli_mod.Path, "home", staticmethod(lambda: tmp_path))

    assert cli_mod._installed_muxplex_entrypoint() == [str(local_bin)]


def test_installed_muxplex_entrypoint_falls_back_to_python_dash_m(
    monkeypatch, tmp_path
):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(shutil, "which", lambda name: None)
    # No ~/.local/bin/muxplex under this fake home -- forces the last resort.
    monkeypatch.setattr(cli_mod.Path, "home", staticmethod(lambda: tmp_path))

    assert cli_mod._installed_muxplex_entrypoint() == [sys.executable, "-m", "muxplex"]

"""Tests for muxplex/cli.py's amplifier-agent bootstrap (`ensure_agent`).

See `ensure_agent`'s own module docstring in cli.py for the full design
rationale (why neither a PyPI nor a plain git `uv tool install` of muxplex
gets amplifier-agent on its own, and why --with is safe to add
unconditionally here unlike tmux-kit's override), and the "muxplex-fx2 gap"
comment above `_AGENT_PANEL_PROVIDERS` for why `amplifier_agent_lib` being
importable was never sufficient proof a turn could actually run -- the
incident this test file's newer tests exist to guard against.
"""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture(autouse=True)
def _pin_above_agent_python_floor(monkeypatch):
    """Pin `_agent_python_supported()` True for every test in this file.

    This file exercises `ensure_agent()`'s install/fail-loud/retry
    machinery, all of which lives behind the `_agent_python_supported()`
    gate added for muxplex-x60: on an interpreter below the amplifier-agent
    floor (real Python 3.11, itself a fully-supported muxplex interpreter --
    see `pyproject.toml`'s `requires-python`), `ensure_agent()` prints the
    upgrade-floor message and returns True *before* ever calling
    `_get_install_info`/`_find_uv`/`subprocess.run` -- so on a bare 3.11
    run every test below that expects those calls to happen sees an empty
    command list or an unreached code path instead. That short-circuit is
    correct runtime behavior and is already covered on its own terms by
    `test_agent_python_floor.py`; it is simply not what THIS file is
    testing. Pinning the predicate here makes every test in this file
    deterministically exercise the above-floor path regardless of which
    interpreter actually runs the suite.
    """
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: True)


@pytest.fixture
def agent_not_yet_installed(monkeypatch):
    """Simulate a clean environment: amplifier-agent has never been
    installed here, and muxplex itself declares pin "9.9.9" for it."""
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "9.9.9"
    )
    monkeypatch.setattr(
        cli_mod,
        "_agent_import_probe",
        lambda: (None, "No module named 'amplifier_agent_lib'"),
    )
    return cli_mod


@pytest.fixture
def providers_ready(monkeypatch):
    """Stub `_agent_providers_importable()` (True, the pre-install fast-path
    check), `_agent_providers_importable_subprocess()` (True, the
    post-install verification), and `_run_agent_post_install()` (success,
    no-op) so tests focused on the amplifier-agent LIBRARY install path
    don't also need to fake a real bundle-prepare subprocess run. Tests
    that specifically exercise the provider-preparation step override these
    themselves.
    """
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_agent_providers_importable", lambda providers=(): (True, "")
    )
    monkeypatch.setattr(
        cli_mod,
        "_agent_providers_importable_subprocess",
        lambda providers=(): (True, ""),
    )
    monkeypatch.setattr(cli_mod, "_run_agent_post_install", lambda uv_path: (True, ""))
    return cli_mod


def _pypi_info():
    return {
        "source": "pypi",
        "version": "0.50.0",
        "commit": None,
        "url": None,
        "ref": None,
    }


def _git_info(url="https://github.com/bkrabach/muxplex", ref: str | None = "v0.50.0"):
    return {
        "source": "git",
        "version": "0.50.0",
        "commit": "abc123",
        "url": url,
        "ref": ref,
    }


# ---------------------------------------------------------------------------
# Idempotent fast path -- property #1: cheap, no subprocess, no network.
# ---------------------------------------------------------------------------


def test_ensure_agent_fast_noop_when_already_at_pin_and_providers_ready(
    monkeypatch, capsys
):
    """The ONLY truly free fast path: lib at pin AND every panel provider
    already importable. Neither `_get_install_info`, `_find_uv`,
    `subprocess.run`, nor `_run_agent_post_install` may be touched."""
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "0.12.0"
    )
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: ("0.12.0", None))
    monkeypatch.setattr(
        cli_mod, "_agent_providers_importable", lambda providers=(): (True, "")
    )

    def fail(*a, **k):
        raise AssertionError("must not shell out when already fully ready")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(cli_mod, "_get_install_info", fail)
    monkeypatch.setattr(cli_mod, "_find_uv", fail)
    monkeypatch.setattr(cli_mod, "_run_agent_post_install", fail)

    assert cli_mod.ensure_agent() is True
    out = capsys.readouterr().out
    assert "0.12.0" in out
    assert "installed" in out
    assert "providers ready" in out


def test_ensure_agent_skips_full_reinstall_when_only_providers_missing(
    monkeypatch, capsys
):
    """This is the exact gap that shipped the bug: lib import matches the
    pin, but the provider modules were never installed. Must NOT reinstall
    amplifier-agent itself (no `_upgrade_target`/install `uv tool install`
    subprocess) -- only run the bundle-prepare step."""
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "0.12.0"
    )
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: ("0.12.0", None))
    # Pre-install fast-path check (in-process): misses once, forcing the
    # bundle-prepare path below. Post-install verification (fresh
    # subprocess) is a SEPARATE function/call site -- stub it separately.
    monkeypatch.setattr(
        cli_mod,
        "_agent_providers_importable",
        lambda providers=(): (
            False,
            "anthropic (amplifier_module_provider_anthropic): missing",
        ),
    )
    monkeypatch.setattr(
        cli_mod,
        "_agent_providers_importable_subprocess",
        lambda providers=(): (True, ""),
    )
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")

    def fail_upgrade_target(*a, **k):
        raise AssertionError(
            "must not compute a reinstall target when lib is already ok"
        )

    monkeypatch.setattr(cli_mod, "_upgrade_target", fail_upgrade_target)

    def fail_run(*a, **k):
        raise AssertionError(
            "must not shell out to `uv tool install` for providers-only gap"
        )

    monkeypatch.setattr(subprocess, "run", fail_run)

    post_install_calls = []

    def fake_post_install(uv_path):
        post_install_calls.append(uv_path)
        return True, ""

    monkeypatch.setattr(cli_mod, "_run_agent_post_install", fake_post_install)

    assert cli_mod.ensure_agent() is True
    assert post_install_calls == ["/usr/bin/uv"]
    out = capsys.readouterr().out
    assert "provider" in out.lower()
    assert "preparing bundle" in out.lower()


def test_ensure_agent_reinstalls_on_version_mismatch(
    providers_ready, monkeypatch, capsys
):
    """Installed at the WRONG version (not merely absent) must still trigger
    a real reinstall, not be treated as a no-op."""
    cli_mod = providers_ready

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "0.13.0"
    )
    probes = iter([("0.12.0", None), ("0.13.0", None)])
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: next(probes))
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")

    captured_cmd = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert cli_mod.ensure_agent() is True
    assert captured_cmd["cmd"] is not None
    assert (
        "0.13.0 installed but muxplex pins" not in capsys.readouterr().out
    )  # sanity: message order not asserted here


# ---------------------------------------------------------------------------
# Source-preserving install target -- property #2.
# ---------------------------------------------------------------------------


def test_ensure_agent_uses_bare_name_for_pypi_target(
    agent_not_yet_installed, providers_ready, monkeypatch, capsys
):
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    # First call is the pre-install fast-path check (must miss, or
    # ensure_agent() short-circuits before ever building install_cmd);
    # second call is the post-install verification.
    probes = iter([(None, "not installed"), ("9.9.9", None)])
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: next(probes))

    assert cli_mod.ensure_agent() is True
    cmd = captured["cmd"]
    assert "muxplex" in cmd
    assert "git+https://github.com/bkrabach/muxplex" not in " ".join(cmd)
    assert "--with" in cmd
    with_idx = cmd.index("--with")
    assert (
        cmd[with_idx + 1]
        == "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v9.9.9"
    )


def test_ensure_agent_preserves_git_target_never_switches_to_pypi(
    providers_ready, monkeypatch, capsys
):
    """The exact regression class this task calls out: never switch
    muxplex's OWN install source from git to PyPI (or vice versa) while
    ensuring amplifier-agent."""
    cli_mod = providers_ready

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "9.9.9"
    )
    probes = iter([(None, "not installed"), ("9.9.9", None)])
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: next(probes))

    git_info = _git_info(ref=None)  # no ref recorded -> track default branch HEAD
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": git_info
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert cli_mod.ensure_agent() is True
    cmd = captured["cmd"]
    assert "git+https://github.com/bkrabach/muxplex" in cmd
    assert "muxplex" not in [
        c for c in cmd if c == "muxplex"
    ]  # bare pypi name never appears
    assert "--with" in cmd
    with_idx = cmd.index("--with")
    assert (
        cmd[with_idx + 1]
        == "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v9.9.9"
    )


def test_ensure_agent_refuses_editable_install(monkeypatch, capsys):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "9.9.9"
    )
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: (None, "not installed"))
    monkeypatch.setattr(
        cli_mod,
        "_get_install_info",
        lambda dist_name="muxplex": {
            "source": "editable",
            "version": "0.50.0",
            "commit": None,
            "url": "file:///home/user/muxplex",
            "ref": None,
        },
    )

    def fail(*a, **k):
        raise AssertionError("must not shell out for an editable install")

    monkeypatch.setattr(subprocess, "run", fail)

    assert cli_mod.ensure_agent() is False
    out = capsys.readouterr().out
    assert "editable" in out.lower()


# ---------------------------------------------------------------------------
# Fail loud -- property #5.
# ---------------------------------------------------------------------------


def test_ensure_agent_fails_loud_when_uv_absent(
    agent_not_yet_installed, monkeypatch, capsys
):
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: None)

    assert cli_mod.ensure_agent() is False
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "uv" in out.lower()


def test_ensure_agent_fails_loud_on_git_fetch_failure(
    agent_not_yet_installed, monkeypatch, capsys
):
    """Simulated git-fetch failure: uv tool install exits non-zero. Must
    report False and print the real stderr -- never silently leave the
    agent absent while reporting success."""
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="error: Failed to fetch: could not resolve host github.com",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert cli_mod.ensure_agent() is False
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "could not resolve host github.com" in out


def test_ensure_agent_fails_loud_when_source_shape_changes(
    agent_not_yet_installed, monkeypatch, capsys
):
    cli_mod = agent_not_yet_installed
    infos = iter([_pypi_info(), _git_info()])
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": next(infos)
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    assert cli_mod.ensure_agent() is False
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "changed shape" in out


def test_ensure_agent_fails_loud_when_still_not_importable_after_install(
    monkeypatch, capsys
):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "9.9.9"
    )
    probes = iter([(None, "not installed"), (None, "still broken")])
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: next(probes))
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    assert cli_mod.ensure_agent() is False
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "still not importable" in out


# ---------------------------------------------------------------------------
# Provider (bundle) preparation -- the muxplex-fx2 gap this fix closes.
# ---------------------------------------------------------------------------


def test_ensure_agent_runs_post_install_after_fresh_install(
    agent_not_yet_installed, monkeypatch, capsys
):
    """After a fresh amplifier-agent install, the bundle-prepare step must
    run before ensure_agent() reports success -- lib-importable alone is
    exactly the insufficient signal that shipped this bug."""
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    probes = iter([(None, "not installed"), ("9.9.9", None)])
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: next(probes))

    post_install_calls = []

    def fake_post_install(uv_path):
        post_install_calls.append(uv_path)
        return True, ""

    monkeypatch.setattr(cli_mod, "_run_agent_post_install", fake_post_install)
    monkeypatch.setattr(
        cli_mod,
        "_agent_providers_importable_subprocess",
        lambda providers=(): (True, ""),
    )

    assert cli_mod.ensure_agent() is True
    assert post_install_calls == ["/usr/bin/uv"]
    out = capsys.readouterr().out
    assert "providers ready" in out


def test_ensure_agent_fails_loud_when_post_install_itself_fails(
    agent_not_yet_installed, monkeypatch, capsys
):
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    probes = iter([(None, "not installed"), ("9.9.9", None)])
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: next(probes))
    monkeypatch.setattr(
        cli_mod,
        "_run_agent_post_install",
        lambda uv_path: (False, "bundle preparation exited 1: boom"),
    )

    assert cli_mod.ensure_agent() is False
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "bundle preparation exited 1: boom" in out


def test_ensure_agent_fails_loud_when_providers_still_missing_after_post_install(
    agent_not_yet_installed, monkeypatch, capsys
):
    """A 0 exit from bundle preparation is NOT proof every module actually
    installed (activate_all() swallows per-module failures unless strict --
    see _run_agent_post_install's docstring) -- ensure_agent() must never
    trust that exit code alone and must re-verify the providers are
    actually importable, even after exhausting its one retry."""
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    probes = iter([(None, "not installed"), ("9.9.9", None)])
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: next(probes))
    # bundle preparation "succeeds" (exit 0) on every attempt but the
    # provider module is still never actually present -- persistently
    # broken, not merely flaky, so the retry must not paper over it.
    monkeypatch.setattr(cli_mod, "_run_agent_post_install", lambda uv_path: (True, ""))
    monkeypatch.setattr(
        cli_mod,
        "_agent_providers_importable_subprocess",
        lambda providers=(): (
            False,
            "anthropic (amplifier_module_provider_anthropic): No module named 'anthropic'",
        ),
    )

    assert cli_mod.ensure_agent() is False
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "still not importable" in out
    assert "amplifier_module_provider_anthropic" in out
    assert "retrying" in out  # the retry was genuinely attempted


def test_ensure_agent_retries_once_on_transient_provider_flake(
    agent_not_yet_installed, monkeypatch, capsys
):
    """Real-world observed behavior (2026-08-17 spike): bundle preparation
    activates ~20 modules concurrently, and a transient per-module failure
    (network blip, resource contention) can leave a provider module not yet
    importable after the first attempt even though nothing in the code
    differs between runs. A second attempt succeeding must be reported as
    SUCCESS, not a hard failure -- a retry exists precisely for this."""
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    probes = iter([(None, "not installed"), ("9.9.9", None)])
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: next(probes))

    post_install_calls = []
    monkeypatch.setattr(
        cli_mod,
        "_run_agent_post_install",
        lambda uv_path: post_install_calls.append(uv_path) or (True, ""),
    )

    provider_results = iter(
        [
            (False, "anthropic (amplifier_module_provider_anthropic): transient"),
            (True, ""),
        ]
    )
    monkeypatch.setattr(
        cli_mod,
        "_agent_providers_importable_subprocess",
        lambda providers=(): next(provider_results),
    )

    assert cli_mod.ensure_agent() is True
    assert len(post_install_calls) == 2  # the retry actually ran
    out = capsys.readouterr().out
    assert "retrying" in out
    assert "providers ready" in out


def test_provider_module_import_name_matches_bundle_convention():
    import muxplex.cli as cli_mod

    assert (
        cli_mod._provider_module_import_name("anthropic")
        == "amplifier_module_provider_anthropic"
    )
    assert (
        cli_mod._provider_module_import_name("openai")
        == "amplifier_module_provider_openai"
    )


def test_agent_providers_importable_detects_a_genuinely_missing_module():
    """A REAL (unmocked) exercise of the check that would have caught the
    shipped bug: a provider name whose module can never exist reports
    False with a detail naming exactly what's missing, never a bare
    unexplained False and never a silent True."""
    import muxplex.cli as cli_mod

    ok, detail = cli_mod._agent_providers_importable(
        providers=("definitely-not-a-real-provider-xyz",)
    )
    assert ok is False
    assert "definitely-not-a-real-provider-xyz" in detail
    assert "amplifier_module_provider_definitely_not_a_real_provider_xyz" in detail


def test_agent_providers_importable_all_present_returns_true_with_empty_detail():
    """Sanity check on the positive branch using modules guaranteed
    importable in any test environment (this test file's own package)."""
    import muxplex.cli as cli_mod

    ok, detail = cli_mod._agent_providers_importable(providers=())
    assert ok is True
    assert detail == ""


# ---------------------------------------------------------------------------
# `_agent_providers_importable_subprocess` -- the false-negative fix itself.
#
# See that function's docstring in cli.py for the full diagnosis: the
# in-process check above is a reproducible false negative immediately after
# an install that just happened in the SAME process, because
# `importlib.invalidate_caches()` never reprocesses a `.pth` file written to
# site-packages after this interpreter's own `site` startup already ran. The
# test below reproduces exactly that shape -- a module that exists on disk
# but was never on the CURRENT process's `sys.path` -- without needing a
# real `uv`/`pip` install, and proves the in-process/subprocess checks give
# the two different answers this fix depends on.
# ---------------------------------------------------------------------------


def test_agent_providers_importable_subprocess_detects_a_genuinely_missing_module():
    """A REAL (unmocked) exercise of the fresh-subprocess check: a provider
    name whose module can never exist reports False with a detail naming
    exactly what's missing -- the subprocess path must fail loud exactly
    like the in-process one, never silently report success for a module
    that plain doesn't exist anywhere."""
    import muxplex.cli as cli_mod

    ok, detail = cli_mod._agent_providers_importable_subprocess(
        providers=("definitely-not-a-real-provider-xyz",)
    )
    assert ok is False
    assert "definitely-not-a-real-provider-xyz" in detail
    assert "amplifier_module_provider_definitely_not_a_real_provider_xyz" in detail


def test_agent_providers_importable_subprocess_all_present_returns_true_with_empty_detail():
    """Sanity check on the positive (trivial, no providers requested)
    branch -- must never shell out at all when there's nothing to check."""
    import muxplex.cli as cli_mod

    ok, detail = cli_mod._agent_providers_importable_subprocess(providers=())
    assert ok is True
    assert detail == ""


def test_agent_providers_importable_subprocess_sees_module_just_installed_after_process_start(
    monkeypatch, tmp_path
):
    """THE regression test for the shipped false-negative: a provider
    module that exists on disk but was never on THIS (the pytest worker's
    own) process's `sys.path` -- exactly the shape of a module `uv pip
    install -e` just wrote into site-packages after `ensure_agent()`'s
    process already started -- must be INVISIBLE to the in-process check
    and VISIBLE to the fresh-subprocess check. If a future change makes
    `_agent_providers_importable_subprocess` import in-process again (or
    otherwise stops spawning a genuinely fresh interpreter), this test
    fails, because the in-process assertion below would then also pass for
    the subprocess call and the two would stop disagreeing.
    """
    import muxplex.cli as cli_mod

    provider = "muxfx3testprovider"
    module_name = cli_mod._provider_module_import_name(provider)
    pkg_dir = tmp_path / module_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("VALUE = 1\n")

    # Sanity/control: NOT importable in the current process -- this
    # directory was never added to this pytest worker's own `sys.path`,
    # mirroring a package written to a venv's site-packages after this
    # interpreter's own `site` startup already ran.
    in_process_ok, in_process_detail = cli_mod._agent_providers_importable(
        providers=(provider,)
    )
    assert in_process_ok is False
    assert module_name in in_process_detail

    # The fresh-subprocess probe spawns a brand-new interpreter that
    # inherits PYTHONPATH from this process's environment -- so it DOES
    # see the module, exactly as a real fresh interpreter sees a
    # just-completed `uv pip install -e` that this process cannot.
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    subprocess_ok, subprocess_detail = cli_mod._agent_providers_importable_subprocess(
        providers=(provider,)
    )
    assert subprocess_ok is True
    assert subprocess_detail == ""


def test_run_agent_post_install_calls_loader_via_sys_executable(monkeypatch):
    """Must invoke `sys.executable -c <snippet calling load_and_prepare_bundle
    directly>` -- NOT the `amplifier-agent-post-install` CLI script (see the
    module-level comment above `_AGENT_PANEL_PROVIDERS` for why that script's
    own cache short-circuit makes it unsafe: it silently no-ops for every
    venv other than the first one that ever primed the shared cache)."""
    import sys

    import muxplex.cli as cli_mod

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="prepared ok")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ok, detail = cli_mod._run_agent_post_install("/opt/uvbin/uv")
    assert ok is True
    assert detail == "prepared ok"
    cmd = captured["cmd"]
    assert cmd[0] == sys.executable
    assert cmd[1] == "-c"
    assert "load_and_prepare_bundle" in cmd[2]
    assert "install_deps=True" in cmd[2]
    assert "amplifier-agent-post-install" not in " ".join(cmd)
    assert captured["env"]["PATH"].startswith("/opt/uvbin")


def test_run_agent_post_install_reports_nonzero_exit(monkeypatch):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="boom: disk full"
        ),
    )

    ok, detail = cli_mod._run_agent_post_install("/usr/bin/uv")
    assert ok is False
    assert "boom: disk full" in detail


def test_run_agent_post_install_reports_subprocess_launch_failure(monkeypatch):
    import muxplex.cli as cli_mod

    def fail(*a, **k):
        raise OSError("no such file or directory")

    monkeypatch.setattr(subprocess, "run", fail)

    ok, detail = cli_mod._run_agent_post_install("/usr/bin/uv")
    assert ok is False
    assert "no such file or directory" in detail


# ---------------------------------------------------------------------------
# Wiring: service_install(), upgrade(), the `ensure-agent` subcommand.
# ---------------------------------------------------------------------------


def test_service_install_calls_ensure_agent_first(monkeypatch):
    import muxplex.service as service_mod

    calls = []
    monkeypatch.setattr(
        "muxplex.cli.ensure_agent", lambda: calls.append("ensure_agent") or True
    )
    monkeypatch.setattr(service_mod, "_is_darwin", lambda: False)
    monkeypatch.setattr(service_mod, "_have_systemctl", lambda: False)
    monkeypatch.setattr(
        service_mod,
        "_no_systemctl_error",
        lambda cmd: calls.append(f"no_systemctl:{cmd}"),
    )

    service_mod.service_install()

    assert calls[0] == "ensure_agent"
    assert "no_systemctl:install" in calls


def test_ensure_agent_subcommand_registered():
    import inspect

    import muxplex.cli as cli_mod

    source = inspect.getsource(cli_mod.main)
    assert '"ensure-agent"' in source
    assert "ensure_agent(force=" in source


def test_ensure_agent_subcommand_exits_nonzero_on_failure(monkeypatch):
    import sys

    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "ensure_agent", lambda force=False: False)
    monkeypatch.setattr(sys, "argv", ["muxplex", "ensure-agent"])

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 1


def test_ensure_agent_subcommand_exits_zero_on_success(monkeypatch):
    import sys

    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "ensure_agent", lambda force=False: True)
    monkeypatch.setattr(sys, "argv", ["muxplex", "ensure-agent"])

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 0


def test_ensure_agent_subcommand_force_flag_propagates(monkeypatch):
    import sys

    import muxplex.cli as cli_mod

    captured = {}

    def fake_ensure_agent(force=False):
        captured["force"] = force
        return True

    monkeypatch.setattr(cli_mod, "ensure_agent", fake_ensure_agent)
    monkeypatch.setattr(sys, "argv", ["muxplex", "ensure-agent", "--force"])

    with pytest.raises(SystemExit):
        cli_mod.main()
    assert captured["force"] is True

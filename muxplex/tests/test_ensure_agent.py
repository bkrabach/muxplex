"""Public SDK/engine installation, import-contract gates, and source safety.

Retired activation tests are replaced by public-surface and fresh-interpreter
failure checks. No test requires a live provider or dependency activation.
"""

from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
from importlib import metadata
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _forbid_real_subprocesses(monkeypatch):
    """Every subprocess path must be explicitly replaced by a test double."""

    def fail(*args, **kwargs):
        raise AssertionError("test must mock subprocess.run; no real processes allowed")

    monkeypatch.setattr(subprocess, "run", fail)


@pytest.fixture(autouse=True)
def _pin_above_agent_python_floor(monkeypatch):
    """Unit tests exercise the install path even on base-only Python 3.11.

    The real installed-surface test independently skips only below 3.12.
    Floor behavior itself is covered in test_agent_python_floor.py.
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
        lambda: (None, "No module named 'amplifier_agent'"),
    )
    return cli_mod


@pytest.fixture
def public_surface_ready(monkeypatch):
    """Stub only the fresh-process verification for install-command tests."""
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_agent_import_probe_subprocess", lambda pin: (pin, None)
    )
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


def test_ensure_agent_fast_noop_when_public_surface_ready(monkeypatch, capsys):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_target_pin", lambda: "0.20.0")
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: ("0.20.0", None))

    def fail(*a, **k):
        raise AssertionError(
            "must not install/probe in a subprocess when already ready"
        )

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(cli_mod, "_get_install_info", fail)
    monkeypatch.setattr(cli_mod, "_find_uv", fail)
    monkeypatch.setattr(cli_mod, "_agent_import_probe_subprocess", fail)

    assert cli_mod.ensure_agent() is True
    out = capsys.readouterr().out
    assert "0.20.0" in out
    assert "public SDK/engine ready" in out


def test_ensure_agent_reinstalls_when_public_surface_broken(
    public_surface_ready, monkeypatch, capsys
):
    cli_mod = public_surface_ready
    monkeypatch.setattr(cli_mod, "_agent_target_pin", lambda: "0.20.0")
    # A version by itself must not satisfy the gate if the API/engine failed.
    monkeypatch.setattr(
        cli_mod, "_agent_import_probe", lambda: ("0.20.0", "engine unavailable")
    )
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli_mod.ensure_agent() is True
    assert len(calls) == 1
    assert calls[0][1:3] == ["tool", "install"]
    assert "engine unavailable" in capsys.readouterr().out


def test_ensure_agent_reinstalls_on_version_mismatch(
    public_surface_ready, monkeypatch, capsys
):
    """Installed at the WRONG version (not merely absent) must still trigger
    a real reinstall, not be treated as a no-op."""
    cli_mod = public_surface_ready

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "0.20.0"
    )
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: ("0.19.0", None))
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
        "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v0.20.0#subdirectory=packages/python"
        in captured_cmd["cmd"]
    )
    assert "reinstalling v0.20.0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Source-preserving install target -- property #2.
# ---------------------------------------------------------------------------


def test_ensure_agent_uses_bare_name_for_pypi_target(
    agent_not_yet_installed, public_surface_ready, monkeypatch, capsys
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
    assert cli_mod.ensure_agent() is True
    cmd = captured["cmd"]
    assert "muxplex" in cmd
    assert "git+https://github.com/bkrabach/muxplex" not in " ".join(cmd)
    assert "--with" in cmd
    with_idx = cmd.index("--with")
    assert (
        cmd[with_idx + 1]
        == "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v9.9.9#subdirectory=packages/python"
    )


def test_ensure_agent_preserves_git_target_never_switches_to_pypi(
    public_surface_ready, monkeypatch, capsys
):
    """The exact regression class this task calls out: never switch
    muxplex's OWN install source from git to PyPI (or vice versa) while
    ensuring amplifier-agent."""
    cli_mod = public_surface_ready

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "9.9.9"
    )
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: (None, "not installed"))

    git_info = _git_info(ref=None)  # no ref recorded -> track default branch HEAD
    monkeypatch.setattr(
        cli_mod,
        "_get_install_info",
        lambda dist_name="muxplex": (
            git_info if dist_name == "muxplex" else _pypi_info()
        ),
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
        == "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v9.9.9#subdirectory=packages/python"
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


@pytest.mark.parametrize("changed_dist", ["muxplex", "tmux-kit"])
@pytest.mark.parametrize("before_source", ["pypi", "git"])
def test_ensure_agent_fails_loud_when_source_shape_changes(
    agent_not_yet_installed, monkeypatch, capsys, changed_dist, before_source
):
    cli_mod = agent_not_yet_installed
    reads = []

    def install_info(dist_name="muxplex"):
        reads.append(dist_name)
        assert dist_name in ("muxplex", "tmux-kit")
        source = before_source
        if len(reads) > 2 and dist_name == changed_dist:
            source = "git" if before_source == "pypi" else "pypi"
        if source == "git":
            return _git_info(f"https://example.invalid/{dist_name}", None)
        return _pypi_info()

    def fail(*args, **kwargs):
        raise AssertionError("source drift must be rejected before the fresh SDK probe")

    monkeypatch.setattr(cli_mod, "_get_install_info", install_info)
    monkeypatch.setattr(cli_mod, "_agent_import_probe_subprocess", fail)
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
    assert changed_dist in out
    after_source = "git" if before_source == "pypi" else "pypi"
    assert f"{before_source} -> {after_source}" in out
    assert reads == ["muxplex", "tmux-kit", "muxplex", "tmux-kit"]


def test_ensure_agent_fails_loud_when_still_not_importable_after_install(
    monkeypatch, capsys
):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "9.9.9"
    )
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: (None, "not installed"))
    monkeypatch.setattr(
        cli_mod, "_agent_import_probe_subprocess", lambda pin: (None, "still broken")
    )
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
# More installer defenses: source targets, force, and command construction.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mux_source, url, ref, expected_target",
    [
        ("pypi", None, None, "muxplex"),
        (
            "git",
            "https://example.invalid/fork/muxplex",
            None,
            "git+https://example.invalid/fork/muxplex",
        ),
        (
            "git",
            "https://example.invalid/fork/muxplex",
            "v0.50.0",
            "git+https://example.invalid/fork/muxplex@v0.50.0",
        ),
        ("local-dir", "file:///tmp/owned%20checkout", None, "/tmp/owned checkout"),
        (
            "archive",
            "file:///tmp/owned%20wheel.whl",
            None,
            "file:///tmp/owned%20wheel.whl",
        ),
        (
            "archive",
            "https://example.invalid/muxplex.whl",
            None,
            "https://example.invalid/muxplex.whl",
        ),
    ],
)
@pytest.mark.parametrize("kit_source", ["pypi", "git"])
def test_install_target_matrix_preserves_muxplex_and_kit_sources(
    agent_not_yet_installed,
    public_surface_ready,
    monkeypatch,
    mux_source,
    url,
    ref,
    expected_target,
    kit_source,
):
    """Exercise the real target/command/source-shape guards, mocking only reads."""
    cli_mod = agent_not_yet_installed
    mux_info = {**_pypi_info(), "source": mux_source, "url": url, "ref": ref}
    kit_info = (
        _git_info("https://example.invalid/tmux-kit", "v0.4.0")
        if kit_source == "git"
        else _pypi_info()
    )
    reads = []
    calls = []

    def install_info(dist_name="muxplex"):
        reads.append(dist_name)
        assert dist_name in ("muxplex", "tmux-kit")
        return mux_info if dist_name == "muxplex" else kit_info

    monkeypatch.setattr(cli_mod, "_get_install_info", install_info)
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    # Ref classification is not this matrix's subject; no remote lookup.
    monkeypatch.setattr(
        cli_mod, "_git_ref_kind_and_target", lambda url, ref: ("commit", ref, None)
    )
    monkeypatch.setattr(
        cli_mod.Path,
        "exists",
        lambda path: str(path) in ("/tmp/owned checkout", "/tmp/owned wheel.whl"),
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs == {"capture_output": True, "text": True}
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli_mod.ensure_agent() is True
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:7] == [
        "/usr/bin/uv",
        "tool",
        "install",
        "--reinstall",
        "--refresh",
        "--force",
        expected_target,
    ]
    sdk = (
        "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent"
        "@v9.9.9#subdirectory=packages/python"
    )
    requirements = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--with"]
    expected_requirements = [sdk]
    if kit_source == "git" and mux_source != "git":
        expected_requirements.append(
            "tmux-kit @ git+https://example.invalid/tmux-kit@v0.4.0"
        )
    assert requirements == expected_requirements
    assert len(cmd) == 7 + 2 * len(expected_requirements)
    assert cli_mod._target_matches_source(mux_info, expected_target) is True
    assert reads == ["muxplex", "tmux-kit", "muxplex", "tmux-kit"]


def test_force_reinstalls_even_when_public_surface_already_ready(
    public_surface_ready, monkeypatch
):
    cli_mod = public_surface_ready
    monkeypatch.setattr(cli_mod, "_agent_target_pin", lambda: "0.20.0")
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: ("0.20.0", None))
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli_mod.ensure_agent(force=True) is True
    assert len(calls) == 1
    assert calls[0][1:6] == ["tool", "install", "--reinstall", "--refresh", "--force"]


@pytest.mark.parametrize("force", [False, True])
def test_editable_refusal_happens_before_uv_lookup(
    agent_not_yet_installed, monkeypatch, force
):
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod,
        "_get_install_info",
        lambda dist_name="muxplex": {**_pypi_info(), "source": "editable"},
    )

    def fail(*args, **kwargs):
        raise AssertionError("never find an installer for an editable checkout")

    monkeypatch.setattr(cli_mod, "_find_uv", fail)
    assert cli_mod.ensure_agent(force=force) is False


@pytest.mark.parametrize(
    "source, url, detail",
    [
        ("unknown", None, "install source not recognized"),
        ("not-installed", None, "install source not recognized"),
        ("git", None, "no recorded remote URL"),
        ("local-dir", "https://example.invalid/checkout", "no recorded path"),
        (
            "local-dir",
            "file:///missing/checkout",
            "original directory no longer exists",
        ),
        ("archive", "file:///missing/muxplex.whl", "original archive no longer exists"),
    ],
)
def test_unusable_source_records_are_rejected_before_install(
    agent_not_yet_installed, monkeypatch, capsys, source, url, detail
):
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod,
        "_get_install_info",
        lambda dist_name="muxplex": {**_pypi_info(), "source": source, "url": url},
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(cli_mod.Path, "exists", lambda path: False)
    assert cli_mod.ensure_agent() is False
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert detail in out


def test_corrupt_direct_url_metadata_never_installs(
    agent_not_yet_installed, monkeypatch
):
    """Current read helper fails loudly on invalid JSON; it must not fall back to PyPI."""
    cli_mod = agent_not_yet_installed
    dist = SimpleNamespace(
        metadata={"Version": "0.50.0"}, read_text=lambda name: "{broken"
    )
    monkeypatch.setattr(metadata, "distribution", lambda dist_name: dist)
    with pytest.raises(json.JSONDecodeError):
        cli_mod.ensure_agent()


@pytest.mark.parametrize("force", [False, True])
def test_substituted_target_is_rejected_even_with_force(
    agent_not_yet_installed, monkeypatch, capsys, force
):
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _git_info(ref=None)
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(cli_mod, "_upgrade_target", lambda info: ("muxplex", None))
    assert cli_mod.ensure_agent(force=force) is False
    assert (
        "target does not match muxplex's recorded install source"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("missing", ["url", "ref"])
def test_pypi_muxplex_refuses_incomplete_git_kit_override(
    agent_not_yet_installed, monkeypatch, capsys, missing
):
    cli_mod = agent_not_yet_installed
    kit = _git_info("https://example.invalid/tmux-kit", "v0.4.0")
    kit[missing] = None
    if missing == "ref":
        kit["commit"] = None
    monkeypatch.setattr(
        cli_mod,
        "_get_install_info",
        lambda dist_name="muxplex": _pypi_info() if dist_name == "muxplex" else kit,
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    assert cli_mod.ensure_agent() is False
    assert (
        "cannot preserve tmux-kit git source without URL/ref" in capsys.readouterr().out
    )


def test_pypi_muxplex_preserves_git_kit_commit_when_ref_absent(
    agent_not_yet_installed, public_surface_ready, monkeypatch
):
    cli_mod = agent_not_yet_installed
    kit = _git_info("https://example.invalid/tmux-kit", None)
    monkeypatch.setattr(
        cli_mod,
        "_get_install_info",
        lambda dist_name="muxplex": _pypi_info() if dist_name == "muxplex" else kit,
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli_mod.ensure_agent() is True
    assert "tmux-kit @ git+https://example.invalid/tmux-kit@abc123" in calls[0]


@pytest.mark.parametrize(
    "guard",
    ["_install_cmd_targets_install_target", "_install_cmd_preserves_kit_override"],
)
def test_bad_install_command_guard_stops_before_execution(
    agent_not_yet_installed, monkeypatch, capsys, guard
):
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(cli_mod, guard, lambda *args: False)
    assert cli_mod.ensure_agent() is False
    assert "install command does not preserve source targets" in capsys.readouterr().out


def test_install_launch_failure_is_reported(
    agent_not_yet_installed, monkeypatch, capsys
):
    cli_mod = agent_not_yet_installed
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")

    def fail(*args, **kwargs):
        raise OSError("installer could not launch")

    monkeypatch.setattr(subprocess, "run", fail)
    assert cli_mod.ensure_agent() is False
    assert (
        "ERROR: could not install amplifier-agent: installer could not launch"
        in capsys.readouterr().out
    )


# ---------------------------------------------------------------------------
# Public SDK version/API/contracts and engine gate (no agent construction).
# ---------------------------------------------------------------------------


@pytest.fixture
def public_sdk(monkeypatch):
    import muxplex.cli as cli_mod

    create_agent = AsyncMock(
        side_effect=AssertionError("the import probe must never construct an agent")
    )

    sdk = SimpleNamespace(
        **{name: type(name, (), {}) for name in cli_mod._AGENT_PUBLIC_API},
        __version__="0.20.0",
        contract_version="agent-interface/1",
        contract_versions=cli_mod._AGENT_CONTRACT_VERSIONS,
    )
    sdk.create_agent = create_agent
    real_import = importlib.import_module
    imports = []

    def fake_import(name, *args, **kwargs):
        if name == "amplifier_agent":
            imports.append(name)
            return sdk
        if name == "amplifier_agent_engine":
            imports.append(name)
            return SimpleNamespace()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(cli_mod, "_agent_target_pin", lambda: "0.20.0")
    yield sdk, imports
    create_agent.assert_not_called()


def test_public_probe_accepts_sdk_and_only_imports_public_roots(public_sdk):
    import muxplex.cli as cli_mod

    _, imports = public_sdk
    assert cli_mod._agent_import_probe() == ("0.20.0", None)
    assert imports == ["amplifier_agent", "amplifier_agent_engine"]


def test_public_probe_explicit_pin_wins_over_current_muxplex_metadata(
    public_sdk, monkeypatch
):
    import muxplex.cli as cli_mod

    def fail():
        raise AssertionError(
            "explicit postinstall pin must not read changed muxplex metadata"
        )

    monkeypatch.setattr(cli_mod, "_agent_target_pin", fail)
    assert cli_mod._agent_import_probe("0.20.0") == ("0.20.0", None)


@pytest.mark.parametrize("version", [None, "0.19.0", "0.20.1", 20])
def test_public_probe_rejects_wrong_or_missing_version(public_sdk, version):
    import muxplex.cli as cli_mod

    sdk, imports = public_sdk
    sdk.__version__ = version
    actual, error = cli_mod._agent_import_probe("0.20.0")
    assert actual is None
    assert error is not None
    assert "version mismatch: expected 0.20.0" in error
    assert imports == ["amplifier_agent"]


@pytest.mark.parametrize(
    "name",
    [
        "create_agent",
        "Agent",
        "AgentOptions",
        "Session",
        "SessionOptions",
        "Turn",
        "TurnInput",
        "Tool",
        "ToolContext",
        "ToolResultEvent",
        "UsageEvent",
    ],
)
@pytest.mark.parametrize("invalid", [False, True], ids=["missing", "noncallable"])
def test_public_probe_rejects_every_missing_or_invalid_api(public_sdk, name, invalid):
    import muxplex.cli as cli_mod

    sdk, imports = public_sdk
    if invalid:
        setattr(sdk, name, object())
    else:
        delattr(sdk, name)
    actual, error = cli_mod._agent_import_probe("0.20.0")
    assert actual is None
    assert error is not None
    assert "public API missing/invalid" in error
    assert name in error
    assert imports == ["amplifier_agent"]


def test_public_probe_rejects_synchronous_factory(public_sdk):
    import muxplex.cli as cli_mod

    sdk, _ = public_sdk
    sdk.create_agent = lambda *args, **kwargs: None
    actual, error = cli_mod._agent_import_probe("0.20.0")
    assert actual is None
    assert error is not None
    assert "create_agent must be async" in error


@pytest.mark.parametrize("contract", [None, "agent-interface/0", "agent-interface/2"])
def test_public_probe_rejects_wrong_primary_contract(public_sdk, contract):
    import muxplex.cli as cli_mod

    sdk, _ = public_sdk
    sdk.contract_version = contract
    actual, error = cli_mod._agent_import_probe("0.20.0")
    assert actual is None
    assert error is not None
    assert "contract_version must be agent-interface/1" in error


@pytest.mark.parametrize(
    "missing",
    ["agent-interface/1", "turn-events/1", "language-binding/1", "host-config/1"],
)
def test_public_probe_requires_each_contract_marker(public_sdk, missing):
    import muxplex.cli as cli_mod

    sdk, _ = public_sdk
    sdk.contract_versions = tuple(
        marker for marker in cli_mod._AGENT_CONTRACT_VERSIONS if marker != missing
    )
    actual, error = cli_mod._agent_import_probe("0.20.0")
    assert actual is None
    assert error is not None
    assert "contract_versions missing required markers" in error


@pytest.mark.parametrize(
    "contracts", [None, "agent-interface/1", {"agent-interface/1": 1}]
)
def test_public_probe_rejects_invalid_contract_collection(public_sdk, contracts):
    import muxplex.cli as cli_mod

    sdk, _ = public_sdk
    sdk.contract_versions = contracts
    actual, error = cli_mod._agent_import_probe("0.20.0")
    assert actual is None
    assert error is not None
    assert "contract_versions missing required markers" in error


@pytest.mark.parametrize("module_name", ["amplifier_agent", "amplifier_agent_engine"])
@pytest.mark.parametrize("exc_type", [ModuleNotFoundError, RuntimeError])
def test_public_probe_fails_loud_on_sdk_or_engine_import_error(
    public_sdk, monkeypatch, module_name, exc_type
):
    import muxplex.cli as cli_mod

    stub_import = importlib.import_module

    def failing_import(name, *args, **kwargs):
        if name == module_name:
            raise exc_type(f"broken {module_name}")
        return stub_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", failing_import)
    actual, error = cli_mod._agent_import_probe("0.20.0")
    assert actual is None
    assert error is not None
    assert "public SDK/engine import failed" in error
    assert exc_type.__name__ in error
    assert module_name in error


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="public SDK requires Python >=3.12"
)
def test_real_installed_sdk_public_surface():
    """Normal 3.12+ CI installs the agent extra: absence is FAILURE, not a skip.

    In-process imports only; no provider call, constructor, or subprocess.
    This proves packaging/public surface, not execution of a live turn.
    """
    import muxplex.cli as cli_mod

    assert metadata.version("amplifier-agent") == "0.20.0"
    assert metadata.distribution("amplifier-agent-engine") is not None
    sdk = importlib.import_module("amplifier_agent")
    for name in (
        "create_agent",
        "Agent",
        "AgentOptions",
        "Session",
        "SessionOptions",
        "Turn",
        "TurnInput",
        "Tool",
        "ToolContext",
        "ToolResultEvent",
        "UsageEvent",
    ):
        assert callable(getattr(sdk, name, None)), name
    assert inspect.iscoroutinefunction(sdk.create_agent)
    assert cli_mod._agent_import_probe("0.20.0") == ("0.20.0", None)


# ---------------------------------------------------------------------------
# Fresh-interpreter verification: never trust cached modules or installer exit 0.
# ---------------------------------------------------------------------------


def test_fresh_probe_uses_same_interpreter_explicit_pin_and_bounded_capture(
    monkeypatch,
):
    import muxplex.cli as cli_mod

    captured = []

    def fake_run(cmd, **kwargs):
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout='["0.20.0", null]', stderr="")

    def fail(*args, **kwargs):
        raise AssertionError(
            "fresh probe must not use this process's cached imports/pin"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(cli_mod, "_agent_import_probe", fail)
    monkeypatch.setattr(cli_mod, "_agent_target_pin", fail)
    assert cli_mod._agent_import_probe_subprocess("0.20.0") == ("0.20.0", None)
    assert len(captured) == 1
    cmd, kwargs = captured[0]
    assert cmd[:2] == [sys.executable, "-c"]
    assert "from muxplex.cli import _agent_import_probe" in cmd[2]
    assert "_agent_import_probe('0.20.0')" in cmd[2]
    assert kwargs == {"capture_output": True, "text": True, "timeout": 60}


@pytest.mark.parametrize("stdout", ["", "not JSON", "[", 'noise\n["0.20.0", null]'])
def test_fresh_probe_rejects_unparseable_output(monkeypatch, stdout):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 0, stdout=stdout, stderr=""
        ),
    )
    actual, error = cli_mod._agent_import_probe_subprocess("0.20.0")
    assert actual is None
    assert error is not None
    assert "unparseable output" in error


@pytest.mark.parametrize(
    "payload", [None, {}, "ready", [], ["0.20.0"], ["0.20.0", None, None]]
)
def test_fresh_probe_rejects_malformed_result_shape(monkeypatch, payload):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload), stderr=""
        ),
    )
    actual, error = cli_mod._agent_import_probe_subprocess("0.20.0")
    assert actual is None
    assert error is not None
    assert "invalid result" in error


@pytest.mark.parametrize(
    "payload",
    [
        ["0.19.0", None],
        ["0.20.0", "engine broken"],
        ["0.20.0", False],
        [None, ""],
        [None, None],
        [20, None],
    ],
)
def test_fresh_probe_rejects_unverified_version_or_error(monkeypatch, payload):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload), stderr=""
        ),
    )
    actual, error = cli_mod._agent_import_probe_subprocess("0.20.0")
    assert actual is None
    assert error is not None
    assert "did not verify version/API" in error


def test_fresh_probe_propagates_public_surface_error(monkeypatch):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 0, stdout='[null, "engine unavailable"]', stderr=""
        ),
    )
    assert cli_mod._agent_import_probe_subprocess("0.20.0") == (
        None,
        "engine unavailable",
    )


def test_fresh_probe_rejects_nonzero_exit_even_with_success_payload(monkeypatch):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 7, stdout='["0.20.0", null]', stderr="probe crashed"
        ),
    )
    actual, error = cli_mod._agent_import_probe_subprocess("0.20.0")
    assert actual is None
    assert error is not None
    assert "exited 7: probe crashed" in error


@pytest.mark.parametrize(
    "exception, detail",
    [
        (subprocess.TimeoutExpired("probe", 60), "timed out after 60s"),
        (OSError("no such interpreter"), "could not run public SDK import probe"),
    ],
)
def test_fresh_probe_rejects_timeout_and_launch_error(monkeypatch, exception, detail):
    import muxplex.cli as cli_mod

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(subprocess, "run", fail)
    actual, error = cli_mod._agent_import_probe_subprocess("0.20.0")
    assert actual is None
    assert error is not None
    assert detail in error


def test_fresh_install_checks_shape_then_fresh_sdk_once_without_activation(
    agent_not_yet_installed, monkeypatch, capsys
):
    cli_mod = agent_not_yet_installed
    reads = []
    calls = []
    probe_calls = []

    def install_info(dist_name="muxplex"):
        reads.append(dist_name)
        assert dist_name in ("muxplex", "tmux-kit")
        return _pypi_info()

    def cached_probe():
        probe_calls.append("cached")
        assert probe_calls == ["cached"], "postinstall must use a fresh interpreter"
        return None, "cached SDK not installed"

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["tool", "install"]:
            assert reads == ["muxplex", "tmux-kit"]
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[:2] == [sys.executable, "-c"]
        assert reads == ["muxplex", "tmux-kit", "muxplex", "tmux-kit"]
        assert "_agent_import_probe('9.9.9')" in cmd[2]
        return subprocess.CompletedProcess(cmd, 0, stdout='["9.9.9", null]', stderr="")

    monkeypatch.setattr(cli_mod, "_get_install_info", install_info)
    monkeypatch.setattr(cli_mod, "_agent_import_probe", cached_probe)
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli_mod.ensure_agent() is True
    assert len(calls) == 2  # installer + public probe; no activation/retry worker
    assert probe_calls == ["cached"]
    assert "public SDK/engine ready" in capsys.readouterr().out


@pytest.mark.parametrize(
    "stdout, returncode, exception, detail",
    [
        ("", 0, None, "unparseable output"),
        ("{}", 0, None, "invalid result"),
        ('["0.19.0", null]', 0, None, "did not verify version/API"),
        ('[null, "engine unavailable"]', 0, None, "engine unavailable"),
        ('["9.9.9", "engine unavailable"]', 0, None, "did not verify version/API"),
        ('["9.9.9", null]', 2, None, "exited 2: probe failed"),
        ("", 0, subprocess.TimeoutExpired("probe", 60), "timed out after 60s"),
        ("", 0, OSError("no interpreter"), "could not run public SDK import probe"),
    ],
)
def test_installer_success_never_masks_failed_fresh_probe(
    agent_not_yet_installed, monkeypatch, capsys, stdout, returncode, exception, detail
):
    cli_mod = agent_not_yet_installed
    calls = []
    monkeypatch.setattr(
        cli_mod, "_get_install_info", lambda dist_name="muxplex": _pypi_info()
    )
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["tool", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[:2] == [sys.executable, "-c"]
        if exception is not None:
            raise exception
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr="probe failed"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli_mod.ensure_agent() is False
    assert len(calls) == 2  # no silent retry/activation after a rejected public surface
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "still not importable" in out
    assert detail in out
    assert "public SDK/engine ready" not in out


def test_installer_and_probes_do_not_override_engine_dependencies_or_activate():
    import muxplex.cli as cli_mod

    source = "\n".join(
        inspect.getsource(fn)
        for fn in (
            cli_mod.ensure_agent,
            cli_mod._agent_import_probe,
            cli_mod._agent_import_probe_subprocess,
        )
    )
    for retired in (
        "load_and_prepare_bundle",
        "activate_all",
        "_run_agent_post_install",
        "amplifier-agent-post-install",
        "amplifier_module_provider_",
        "amplifier_agent.cli",
        "amplifier-foundation",
        "amplifier-core",
    ):
        assert retired not in source
    assert 'import_module("amplifier_agent_engine")' in source


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

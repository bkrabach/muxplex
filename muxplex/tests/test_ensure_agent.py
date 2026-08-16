"""Tests for muxplex/cli.py's amplifier-agent bootstrap (`ensure_agent`).

See `ensure_agent`'s own module docstring in cli.py for the full design
rationale (why neither a PyPI nor a plain git `uv tool install` of muxplex
gets amplifier-agent on its own, and why --with is safe to add
unconditionally here unlike tmux-kit's override).
"""

from __future__ import annotations

import subprocess

import pytest


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


def test_ensure_agent_fast_noop_when_already_at_pin(monkeypatch, capsys):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": "0.12.0"
    )
    monkeypatch.setattr(cli_mod, "_agent_import_probe", lambda: ("0.12.0", None))

    def fail(*a, **k):
        raise AssertionError("must not shell out when already at the pinned version")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(cli_mod, "_get_install_info", fail)
    monkeypatch.setattr(cli_mod, "_find_uv", fail)

    assert cli_mod.ensure_agent() is True
    out = capsys.readouterr().out
    assert "0.12.0" in out
    assert "already installed" in out


def test_ensure_agent_reinstalls_on_version_mismatch(monkeypatch, capsys):
    """Installed at the WRONG version (not merely absent) must still trigger
    a real reinstall, not be treated as a no-op."""
    import muxplex.cli as cli_mod

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
    agent_not_yet_installed, monkeypatch, capsys
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


def test_ensure_agent_preserves_git_target_never_switches_to_pypi(monkeypatch, capsys):
    """The exact regression class this task calls out: never switch
    muxplex's OWN install source from git to PyPI (or vice versa) while
    ensuring amplifier-agent."""
    import muxplex.cli as cli_mod

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

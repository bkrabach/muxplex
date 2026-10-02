"""Public SDK Python-floor guards without subprocess, service, or network effects.

amplifier-agent v0.20.0 requires Python >=3.12, but
muxplex's own floor is only >=3.11 (`pyproject.toml`'s `requires-python`).
Without a guard, `ensure_agent()` on Python 3.11 hands the uv resolver a
requirement (`amplifier-agent==X.Y.Z; python_version>='3.12'`) it can NEVER
satisfy, producing a raw "unsatisfiable" resolver traceback instead of a
clear explanation.

`_agent_python_supported()` is the single floor predicate every entry
point consults first: `ensure_agent()` and `doctor()`'s agent block. See
each function's own docstring in cli.py for the full rationale.
"""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture(autouse=True)
def _forbid_real_subprocesses(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("test must mock subprocess.run; no real processes allowed")

    monkeypatch.setattr(subprocess, "run", fail)


@pytest.fixture
def isolated_doctor(monkeypatch, tmp_path):
    """Doctor's unrelated diagnostics must not inspect a live service/network."""
    import muxplex.cli as cli_mod
    import muxplex.settings as settings_mod

    monkeypatch.setattr(cli_mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: None)
    monkeypatch.setattr(cli_mod, "_have_systemctl", lambda: False)
    monkeypatch.setattr(cli_mod, "_have_launchctl", lambda: False)
    monkeypatch.setattr(
        cli_mod, "_check_for_update", lambda info: (False, "up to date")
    )
    monkeypatch.setattr(
        cli_mod, "_fetch_local_instance_info", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(cli_mod, "pam_probe", lambda: (False, None))
    monkeypatch.setattr(cli_mod, "get_password_path", lambda: tmp_path / "password")
    monkeypatch.setattr(
        cli_mod, "_declared_dependency_pin", lambda dep, dist_name="muxplex": None
    )
    monkeypatch.setattr(
        settings_mod, "load_settings", lambda: dict(settings_mod.DEFAULT_SETTINGS)
    )
    return cli_mod


def test_agent_python_supported_false_below_floor(monkeypatch):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 11, 4, "final", 0))
    assert cli_mod._agent_python_supported() is False


def test_agent_python_supported_true_at_and_above_floor(monkeypatch):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 12, 0, "final", 0))
    assert cli_mod._agent_python_supported() is True

    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 13, 0, "final", 0))
    assert cli_mod._agent_python_supported() is True


# ---------------------------------------------------------------------------
# ensure_agent(): never construct/run the uv install command below the floor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("force", [False, True])
def test_ensure_agent_skips_uv_entirely_below_floor(monkeypatch, capsys, force):
    """The core guarantee: below the floor, `ensure_agent()` must never
    construct or run the uv install command -- the resolver must never be
    handed a requirement it cannot satisfy. Asserted on the mocks being
    UNCALLED, not merely on the return value.

    The floor guard precedes even optional SDK imports and provenance reads,
    including with --force. Python 3.11 base muxplex remains supported.
    """
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: False)
    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 11, 4, "final", 0))

    def fail(*a, **k):
        raise AssertionError(
            "must not construct/run the uv install command below the floor"
        )

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(cli_mod, "_find_uv", fail)
    monkeypatch.setattr(cli_mod, "_get_install_info", fail)
    monkeypatch.setattr(cli_mod, "_agent_target_pin", fail)
    monkeypatch.setattr(cli_mod, "_agent_import_probe", fail)
    monkeypatch.setattr(cli_mod, "_agent_import_probe_subprocess", fail)

    assert cli_mod.ensure_agent(force=force) is True
    out = capsys.readouterr().out
    assert ">=3.12" in out
    assert "3.11" in out


def test_ensure_agent_below_floor_message_names_a_reinstall_command(
    monkeypatch, capsys
):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: False)
    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 11, 9, "final", 0))
    assert cli_mod.ensure_agent() is True
    out = capsys.readouterr().out
    assert "uv tool install" in out
    assert "--python 3.12 --force muxplex" in out
    assert "muxplex itself is unaffected" in out


@pytest.mark.parametrize("force", [False, True])
def test_ensure_agent_subcommand_exits_0_below_floor(monkeypatch, force):
    """`muxplex ensure-agent` on an unsupported interpreter must exit 0 --
    this is a correctly-reported unsupported configuration, not a
    failure the user can do anything about."""
    import sys

    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: False)

    def fail(*a, **k):
        raise AssertionError("must not shell out below the floor")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(cli_mod, "_find_uv", fail)
    monkeypatch.setattr(cli_mod, "_get_install_info", fail)
    monkeypatch.setattr(
        sys, "argv", ["muxplex", "ensure-agent"] + (["--force"] if force else [])
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 0


def test_public_probes_refuse_below_floor_without_import_or_subprocess(monkeypatch):
    import importlib

    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 11, 4, "final", 0))

    def fail(*args, **kwargs):
        raise AssertionError(
            "no optional SDK import or installer metadata read below floor"
        )

    monkeypatch.setattr(importlib, "import_module", fail)
    monkeypatch.setattr(cli_mod, "_agent_target_pin", fail)
    assert cli_mod._agent_import_probe() == (
        None,
        "amplifier-agent requires Python >=3.12",
    )
    assert cli_mod._agent_import_probe_subprocess("0.20.0") == (
        None,
        "amplifier-agent requires Python >=3.12",
    )


# ---------------------------------------------------------------------------
# doctor(): explain the floor instead of nagging a command that can't work.
# ---------------------------------------------------------------------------


def _install_info_stub(cli_mod, agent_source="not-installed"):
    def fake_get_install_info(dist_name="muxplex"):
        if dist_name == cli_mod._AGENT_DIST_NAME:
            return {
                "source": agent_source,
                "version": None if agent_source == "not-installed" else "0.20.0",
                "commit": None,
                "url": None,
                "ref": None,
            }
        return {
            "source": "pypi",
            "version": "0.56.2",
            "commit": None,
            "url": None,
            "ref": None,
        }

    return fake_get_install_info


def test_doctor_explains_floor_instead_of_nagging_ensure_agent(
    isolated_doctor, monkeypatch, capsys
):
    """Below the floor: doctor must contain the explanation and must NOT
    contain `Run: muxplex ensure-agent` -- recommending a command that
    cannot possibly succeed on this interpreter is the exact friction
    muxplex-x60 Phase 1 removes."""
    cli_mod = isolated_doctor

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: False)
    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 11, 4, "final", 0))
    stub = _install_info_stub(cli_mod)
    reads = []

    def install_info(dist_name="muxplex"):
        reads.append(dist_name)
        assert dist_name != cli_mod._AGENT_DIST_NAME
        return stub(dist_name)

    def fail(*args, **kwargs):
        raise AssertionError("doctor must not import the optional SDK below its floor")

    monkeypatch.setattr(cli_mod, "_get_install_info", install_info)
    monkeypatch.setattr(cli_mod, "_agent_import_probe", fail)

    cli_mod.doctor()
    out = capsys.readouterr().out
    assert ">=3.12" in out
    assert "Run: muxplex ensure-agent" not in out
    assert reads == ["muxplex", "tmux-kit"]


def test_doctor_still_recommends_ensure_agent_above_floor(
    isolated_doctor, monkeypatch, capsys
):
    """Above the floor, doctor's behaviour for a missing agent is
    unchanged from before this fix: recommend `muxplex ensure-agent`."""
    cli_mod = isolated_doctor

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: True)
    monkeypatch.setattr(cli_mod, "_get_install_info", _install_info_stub(cli_mod))

    cli_mod.doctor()
    out = capsys.readouterr().out
    assert "Run: muxplex ensure-agent" in out


@pytest.mark.parametrize("error", [None, "engine import failed"])
def test_doctor_reports_public_surface_readiness_above_floor(
    isolated_doctor, monkeypatch, capsys, error
):
    cli_mod = isolated_doctor
    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: True)
    monkeypatch.setattr(
        cli_mod, "_get_install_info", _install_info_stub(cli_mod, "pypi")
    )
    monkeypatch.setattr(
        cli_mod,
        "_agent_import_probe",
        lambda: ("0.20.0", None) if error is None else (None, error),
    )

    cli_mod.doctor()
    out = capsys.readouterr().out
    if error is None:
        assert "Public SDK/engine import surface ready (v0.20.0)" in out
        assert "Run: muxplex ensure-agent" not in out
    else:
        assert f"public SDK/engine unavailable: {error}" in out
        assert "Run: muxplex ensure-agent" in out
        assert "Public SDK/engine import surface ready" not in out

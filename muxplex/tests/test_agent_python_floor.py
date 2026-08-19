"""Tests for muxplex-x60 Phase 1: the amplifier-agent Python-floor guard.

amplifier-agent requires Python >=3.12 at every released version, but
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


def test_ensure_agent_skips_uv_entirely_below_floor(monkeypatch, capsys):
    """The core guarantee: below the floor, `ensure_agent()` must never
    construct or run the uv install command -- the resolver must never be
    handed a requirement it cannot satisfy. Asserted on the mocks being
    UNCALLED, not merely on the return value.

    `_get_install_info()` is mocked to a non-editable ("pypi") source
    because `ensure_agent()`'s own editable-checkout short-circuit runs
    BEFORE the floor check (cli.py: editable checkouts are told to
    self-manage via `uv sync --extra agent` regardless of Python version)
    -- unmocked, this repo's own real install info (an editable checkout,
    since these tests run inside the muxplex source tree itself) would
    return early via THAT branch and never reach the floor guard this
    test exists to exercise.
    """
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: False)
    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 11, 4, "final", 0))
    monkeypatch.setattr(cli_mod, "_get_install_info", _install_info_stub(cli_mod))

    def fail(*a, **k):
        raise AssertionError(
            "must not construct/run the uv install command below the floor"
        )

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(cli_mod, "_find_uv", fail)

    assert cli_mod.ensure_agent() is True
    out = capsys.readouterr().out
    assert ">=3.12" in out
    assert "3.11" in out


def test_ensure_agent_below_floor_message_names_a_reinstall_command(
    monkeypatch, capsys
):
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: False)
    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 11, 9, "final", 0))
    # See test_ensure_agent_skips_uv_entirely_below_floor's docstring for
    # why this must be mocked to a non-editable source.
    monkeypatch.setattr(cli_mod, "_get_install_info", _install_info_stub(cli_mod))

    assert cli_mod.ensure_agent() is True
    out = capsys.readouterr().out
    assert "uv tool install" in out
    assert "muxplex itself is unaffected" in out


def test_ensure_agent_subcommand_exits_0_below_floor(monkeypatch):
    """`muxplex ensure-agent` on an unsupported interpreter must exit 0 --
    this is a correctly-reported unsupported configuration, not a
    failure the user can do anything about."""
    import sys

    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: False)
    # See test_ensure_agent_skips_uv_entirely_below_floor's docstring for
    # why this must be mocked to a non-editable source -- otherwise this
    # repo's own real (editable) install info short-circuits ensure_agent()
    # before it ever reaches the floor guard this test exercises.
    monkeypatch.setattr(cli_mod, "_get_install_info", _install_info_stub(cli_mod))

    def fail(*a, **k):
        raise AssertionError("must not shell out below the floor")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(sys, "argv", ["muxplex", "ensure-agent"])

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# doctor(): explain the floor instead of nagging a command that can't work.
# ---------------------------------------------------------------------------


def _install_info_stub(cli_mod, agent_source="not-installed"):
    def fake_get_install_info(dist_name="muxplex"):
        if dist_name == cli_mod._AGENT_DIST_NAME:
            return {
                "source": agent_source,
                "version": None,
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


def test_doctor_explains_floor_instead_of_nagging_ensure_agent(monkeypatch, capsys):
    """Below the floor: doctor must contain the explanation and must NOT
    contain `Run: muxplex ensure-agent` -- recommending a command that
    cannot possibly succeed on this interpreter is the exact friction
    muxplex-x60 Phase 1 removes."""
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: False)
    monkeypatch.setattr(cli_mod.sys, "version_info", (3, 11, 4, "final", 0))
    monkeypatch.setattr(cli_mod, "_get_install_info", _install_info_stub(cli_mod))

    cli_mod.doctor()
    out = capsys.readouterr().out
    assert ">=3.12" in out
    assert "Run: muxplex ensure-agent" not in out


def test_doctor_still_recommends_ensure_agent_above_floor(monkeypatch, capsys):
    """Above the floor, doctor's behaviour for a missing agent is
    unchanged from before this fix: recommend `muxplex ensure-agent`."""
    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_agent_python_supported", lambda: True)
    monkeypatch.setattr(cli_mod, "_get_install_info", _install_info_stub(cli_mod))

    cli_mod.doctor()
    out = capsys.readouterr().out
    assert "Run: muxplex ensure-agent" in out

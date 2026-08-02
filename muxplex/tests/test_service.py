"""Tests for muxplex/service.py — system service management module."""

import os
import shutil
import subprocess
import sys

import pytest


def test_service_module_importable():
    """All 7 public service functions must be importable from muxplex.service."""
    from muxplex.service import (  # noqa: F401
        service_install,
        service_logs,
        service_restart,
        service_start,
        service_status,
        service_stop,
        service_uninstall,
    )


def test_is_darwin_detection(monkeypatch):
    """_is_darwin() must return True when sys.platform=='darwin', False for 'linux'."""
    from muxplex.service import _is_darwin

    monkeypatch.setattr(sys, "platform", "darwin")
    assert _is_darwin() is True

    monkeypatch.setattr(sys, "platform", "linux")
    assert _is_darwin() is False


def test_resolve_muxplex_bin():
    """_resolve_muxplex_bin() must return a string containing 'muxplex' or 'python'."""
    from muxplex.service import _resolve_muxplex_bin

    result = _resolve_muxplex_bin()
    assert isinstance(result, str)
    assert "muxplex" in result or "python" in result


# ---------------------------------------------------------------------------
# systemd tests
# ---------------------------------------------------------------------------


def test_systemd_install_writes_unit_and_enables(monkeypatch, tmp_path):
    """_systemd_install writes unit file with 'muxplex serve' (no --host/--port)
    and calls daemon-reload + enable --now."""
    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    # Avoid interactive prompt
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._systemd_install()

    # Unit file must exist and contain the right content
    assert unit_path.exists(), "unit file was not written"
    content = unit_path.read_text()
    assert "muxplex" in content, "unit file must mention 'muxplex'"
    assert "serve" in content, "unit file ExecStart must include 'serve'"
    assert "--host" not in content, "ExecStart must NOT contain --host"
    assert "--port" not in content, "ExecStart must NOT contain --port"

    # daemon-reload must be called
    assert ["systemctl", "--user", "daemon-reload"] in calls, "daemon-reload not called"
    # enable --now must be called
    assert ["systemctl", "--user", "enable", "--now", "muxplex"] in calls, (
        "enable --now not called"
    )


def test_systemd_uninstall_stops_disables_removes(monkeypatch, tmp_path):
    """_systemd_uninstall calls stop, disable, daemon-reload and deletes the unit file."""
    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit_path = unit_dir / "muxplex.service"
    unit_path.write_text("[Unit]\nDescription=muxplex\n")

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    svc._systemd_uninstall()

    assert ["systemctl", "--user", "stop", "muxplex"] in calls, "stop not called"
    assert ["systemctl", "--user", "disable", "muxplex"] in calls, "disable not called"
    assert ["systemctl", "--user", "daemon-reload"] in calls, "daemon-reload not called"
    assert not unit_path.exists(), "unit file was not deleted"


def test_systemd_start_calls_systemctl(monkeypatch):
    """_systemd_start runs ['systemctl', '--user', 'start', 'muxplex']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    svc._systemd_start()
    assert ["systemctl", "--user", "start", "muxplex"] in calls


def test_systemd_stop_calls_systemctl(monkeypatch):
    """_systemd_stop runs ['systemctl', '--user', 'stop', 'muxplex']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    svc._systemd_stop()
    assert ["systemctl", "--user", "stop", "muxplex"] in calls


def test_systemd_restart_calls_systemctl(monkeypatch):
    """_systemd_restart runs ['systemctl', '--user', 'restart', 'muxplex']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    svc._systemd_restart()
    assert ["systemctl", "--user", "restart", "muxplex"] in calls


def test_systemd_status_calls_systemctl(monkeypatch):
    """_systemd_status runs ['systemctl', '--user', 'status', 'muxplex', '--no-pager']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    svc._systemd_status()
    assert ["systemctl", "--user", "status", "muxplex", "--no-pager"] in calls


def test_systemd_logs_calls_journalctl(monkeypatch):
    """_systemd_logs runs ['journalctl', '--user', '-u', 'muxplex', '-f']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    svc._systemd_logs()
    assert ["journalctl", "--user", "-u", "muxplex", "-f"] in calls


# ---------------------------------------------------------------------------
# launchd tests
# ---------------------------------------------------------------------------


def test_launchd_install_writes_plist_and_bootstraps(monkeypatch, tmp_path):
    """_launchd_install writes plist with 'com.muxplex' and 'serve' (no --host/--port)
    and calls launchctl bootstrap with gui/{uid}."""
    import os

    import muxplex.service as svc

    plist_dir = tmp_path / "LaunchAgents"
    plist_path = plist_dir / "com.muxplex.plist"

    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_DIR", plist_dir)
    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(os, "getuid", lambda: 501)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    # Suppress interactive prompt
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._launchd_install()

    # Plist file must exist and contain expected content
    assert plist_path.exists(), "plist file was not written"
    content = plist_path.read_text()
    assert "com.muxplex" in content, "plist must contain 'com.muxplex'"
    assert "serve" in content, "plist ProgramArguments must include 'serve'"
    assert "--host" not in content, "plist must NOT contain --host"
    assert "--port" not in content, "plist must NOT contain --port"

    # bootstrap must be called with gui/501
    bootstrap_calls = [c for c in calls if "bootstrap" in c]
    assert bootstrap_calls, "launchctl bootstrap not called"
    bootstrap_cmd = bootstrap_calls[0]
    assert "gui/501" in bootstrap_cmd, (
        f"bootstrap must use gui/501, got: {bootstrap_cmd}"
    )


def test_launchd_uninstall_bootouts_and_removes(monkeypatch, tmp_path):
    """_launchd_uninstall calls launchctl bootout and removes the plist file."""
    import os

    import muxplex.service as svc

    plist_dir = tmp_path / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.muxplex.plist"
    plist_path.write_text("<plist/>")

    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_DIR", plist_dir)
    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(os, "getuid", lambda: 501)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    svc._launchd_uninstall()

    # bootout must be called
    bootout_calls = [c for c in calls if "bootout" in c]
    assert bootout_calls, "launchctl bootout not called"
    bootout_cmd = bootout_calls[0]
    assert "gui/501" in " ".join(bootout_cmd), (
        f"bootout must reference gui/501, got: {bootout_cmd}"
    )
    assert "com.muxplex" in " ".join(bootout_cmd), (
        f"bootout must reference com.muxplex, got: {bootout_cmd}"
    )

    # plist must be removed
    assert not plist_path.exists(), "plist file was not deleted"


def test_launchd_stop_calls_bootout(monkeypatch):
    """_launchd_stop runs launchctl bootout gui/{uid}/com.muxplex."""
    import os

    import muxplex.service as svc

    monkeypatch.setattr(os, "getuid", lambda: 501)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    svc._launchd_stop()

    bootout_calls = [c for c in calls if "bootout" in c]
    assert bootout_calls, "launchctl bootout not called"
    bootout_cmd = bootout_calls[0]
    assert "gui/501" in " ".join(bootout_cmd), (
        f"bootout must reference gui/501, got: {bootout_cmd}"
    )
    assert "com.muxplex" in " ".join(bootout_cmd), (
        f"bootout must reference com.muxplex, got: {bootout_cmd}"
    )


def test_launchd_logs_tails_log_file(monkeypatch):
    """_launchd_logs runs exactly ['tail', '-f', '/tmp/muxplex.log']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    svc._launchd_logs()

    assert ["tail", "-f", "/tmp/muxplex.log"] in calls, (
        f"Expected ['tail', '-f', '/tmp/muxplex.log'], got: {calls}"
    )


def test_launchd_restart_calls_stop_then_start(monkeypatch):
    """_launchd_restart calls bootout (stop) followed by bootstrap (start)."""
    import os

    import muxplex.service as svc

    monkeypatch.setattr(os, "getuid", lambda: 501)

    calls = []
    booted_out = {"yes": False}

    def fake_run(cmd, **kw):
        # Model launchd honestly: `print` reports the job as loaded until a
        # bootout has happened, gone afterwards. A blanket rc=0 for every call
        # claims bootout succeeded AND the job is still loaded -- a state real
        # launchd cannot be in, and the reason this mock started failing once
        # bootout learned to wait for the job to actually disappear.
        calls.append(list(cmd))
        if cmd[1] == "bootout":
            booted_out["yes"] = True
        if cmd[1] == "print":
            return subprocess.CompletedProcess(cmd, 1 if booted_out["yes"] else 0)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(svc.time, "sleep", lambda _s: None)

    svc._launchd_restart()

    # Must have both bootout and bootstrap calls
    bootout_calls = [c for c in calls if "bootout" in c]
    bootstrap_calls = [c for c in calls if "bootstrap" in c]
    assert bootout_calls, "launchctl bootout (stop) not called during restart"
    assert bootstrap_calls, "launchctl bootstrap (start) not called during restart"

    # bootout must come before bootstrap
    bootout_index = next(i for i, c in enumerate(calls) if "bootout" in c)
    bootstrap_index = next(i for i, c in enumerate(calls) if "bootstrap" in c)
    assert bootout_index < bootstrap_index, (
        "bootout (stop) must be called before bootstrap (start) in restart"
    )


def test_launchd_status_runs_print_command(monkeypatch):
    """_launchd_status runs launchctl print gui/{uid}/com.muxplex."""
    import os

    import muxplex.service as svc

    monkeypatch.setattr(os, "getuid", lambda: 501)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    svc._launchd_status()

    print_calls = [c for c in calls if "print" in c]
    assert print_calls, "launchctl print not called"
    print_cmd = print_calls[0]
    assert "gui/501/com.muxplex" in " ".join(print_cmd), (
        f"print must reference gui/501/com.muxplex, got: {print_cmd}"
    )


# ---------------------------------------------------------------------------
# C1 regression tests — check=True must NOT be set on idempotent/informational
# operations: status, stop, uninstall (stop+disable for systemd, bootout for launchd)
# ---------------------------------------------------------------------------


def _make_kwargs_capture():
    """Return (calls_with_kw, monkeypatch_fn) for capturing subprocess.run kwargs."""
    calls_with_kw: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kw):
        calls_with_kw.append((list(cmd), dict(kw)))
        # Real subprocess.run always returns a CompletedProcess. Returning None
        # here was a lie the callers happened to get away with until production
        # code started reading .returncode.
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return calls_with_kw, fake_run


def test_systemd_status_no_check_true(monkeypatch):
    """_systemd_status must NOT pass check=True — a stopped service yields exit code 3."""
    import subprocess

    import muxplex.service as svc

    calls_with_kw, fake_run = _make_kwargs_capture()
    monkeypatch.setattr(subprocess, "run", fake_run)
    svc._systemd_status()

    assert calls_with_kw, "subprocess.run was not called"
    for cmd, kw in calls_with_kw:
        assert kw.get("check") is not True, (
            f"check=True must not be set on status command {cmd}"
        )


def test_systemd_stop_no_check_true(monkeypatch):
    """_systemd_stop must NOT pass check=True — stopping an already-stopped service is ok."""
    import subprocess

    import muxplex.service as svc

    calls_with_kw, fake_run = _make_kwargs_capture()
    monkeypatch.setattr(subprocess, "run", fake_run)
    svc._systemd_stop()

    assert calls_with_kw, "subprocess.run was not called"
    for cmd, kw in calls_with_kw:
        assert kw.get("check") is not True, (
            f"check=True must not be set on stop command {cmd}"
        )


def test_systemd_uninstall_stop_and_disable_no_check_true(monkeypatch, tmp_path):
    """_systemd_uninstall's stop and disable calls must NOT pass check=True."""
    import subprocess

    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit_path = unit_dir / "muxplex.service"
    unit_path.write_text("[Unit]\n")
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)

    calls_with_kw, fake_run = _make_kwargs_capture()
    monkeypatch.setattr(subprocess, "run", fake_run)
    svc._systemd_uninstall()

    # stop and disable must not have check=True
    for cmd, kw in calls_with_kw:
        if "stop" in cmd or "disable" in cmd:
            assert kw.get("check") is not True, (
                f"check=True must not be set on uninstall subcommand {cmd}"
            )


def test_launchd_status_no_check_true(monkeypatch):
    """_launchd_status must NOT pass check=True — service may not be loaded."""
    import os
    import subprocess

    import muxplex.service as svc

    monkeypatch.setattr(os, "getuid", lambda: 501)
    calls_with_kw, fake_run = _make_kwargs_capture()
    monkeypatch.setattr(subprocess, "run", fake_run)
    svc._launchd_status()

    assert calls_with_kw, "subprocess.run was not called"
    for cmd, kw in calls_with_kw:
        assert kw.get("check") is not True, (
            f"check=True must not be set on launchd status command {cmd}"
        )


def test_launchd_stop_no_check_true(monkeypatch):
    """_launchd_stop must NOT pass check=True — bootout on unloaded service is ok."""
    import os
    import subprocess

    import muxplex.service as svc

    monkeypatch.setattr(os, "getuid", lambda: 501)
    calls_with_kw, fake_run = _make_kwargs_capture()
    monkeypatch.setattr(subprocess, "run", fake_run)
    svc._launchd_stop()

    assert calls_with_kw, "subprocess.run was not called"
    for cmd, kw in calls_with_kw:
        assert kw.get("check") is not True, (
            f"check=True must not be set on launchd stop command {cmd}"
        )


def test_launchd_uninstall_no_check_true(monkeypatch, tmp_path):
    """_launchd_uninstall's bootout must NOT pass check=True."""
    import os
    import subprocess

    import muxplex.service as svc

    plist_dir = tmp_path / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.muxplex.plist"
    plist_path.write_text("<plist/>")
    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_DIR", plist_dir)
    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(os, "getuid", lambda: 501)

    calls_with_kw, fake_run = _make_kwargs_capture()
    monkeypatch.setattr(subprocess, "run", fake_run)
    svc._launchd_uninstall()

    for cmd, kw in calls_with_kw:
        assert kw.get("check") is not True, (
            f"check=True must not be set on launchd uninstall command {cmd}"
        )


# ---------------------------------------------------------------------------
# C2 regression tests — _prompt_host_if_localhost must be resilient
# ---------------------------------------------------------------------------


def test_prompt_host_eoferror_defaults_to_no_change(monkeypatch):
    """_prompt_host_if_localhost must not crash on EOFError (CI/piped stdin)."""
    import muxplex.service as svc

    patched: list[dict] = []

    def fake_load():
        return {"host": "127.0.0.1"}

    def fake_patch(settings):
        patched.append(settings)

    def fake_input(_prompt):
        raise EOFError

    monkeypatch.setattr("muxplex.settings.load_settings", fake_load)
    monkeypatch.setattr("muxplex.settings.patch_settings", fake_patch)
    monkeypatch.setattr("builtins.input", fake_input)

    # Must not raise, and must NOT patch settings (default to "n")
    svc._prompt_host_if_localhost()
    assert patched == [], "patch_settings must not be called when EOFError occurs"


def test_prompt_host_keyboard_interrupt_defaults_to_no_change(monkeypatch):
    """_prompt_host_if_localhost must not crash on KeyboardInterrupt."""
    import muxplex.service as svc

    patched: list[dict] = []

    def fake_load():
        return {"host": "127.0.0.1"}

    def fake_patch(settings):
        patched.append(settings)

    def fake_input(_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("muxplex.settings.load_settings", fake_load)
    monkeypatch.setattr("muxplex.settings.patch_settings", fake_patch)
    monkeypatch.setattr("builtins.input", fake_input)

    svc._prompt_host_if_localhost()
    assert patched == [], (
        "patch_settings must not be called when KeyboardInterrupt occurs"
    )


def test_prompt_host_missing_host_key_no_keyerror(monkeypatch):
    """_prompt_host_if_localhost must not raise KeyError when 'host' key is absent."""
    import muxplex.service as svc

    def fake_load():
        return {}  # no 'host' key

    def fake_patch(settings):
        pass  # should never be called

    monkeypatch.setattr("muxplex.settings.load_settings", fake_load)
    monkeypatch.setattr("muxplex.settings.patch_settings", fake_patch)

    # Must not raise KeyError
    svc._prompt_host_if_localhost()


# ---------------------------------------------------------------------------
# Bug fix: Ctrl+C handling in logs functions (clean exit on KeyboardInterrupt)
# ---------------------------------------------------------------------------


def test_systemd_logs_handles_keyboard_interrupt(monkeypatch):
    """service logs must exit cleanly on Ctrl+C."""
    import muxplex.service as svc

    def mock_run(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(subprocess, "run", mock_run)
    # Should not raise
    svc._systemd_logs()


# ---------------------------------------------------------------------------
# task: port-in-use crash-loop prevention — TimeoutStopSec in systemd unit
# ---------------------------------------------------------------------------


def test_systemd_unit_template_has_timeout_stop_sec():
    """_SYSTEMD_UNIT_TEMPLATE must include TimeoutStopSec to SIGKILL stale process."""
    import muxplex.service as svc

    assert "TimeoutStopSec" in svc._SYSTEMD_UNIT_TEMPLATE, (
        "_SYSTEMD_UNIT_TEMPLATE must include TimeoutStopSec so systemd sends SIGKILL "
        "if the old process does not exit on SIGTERM within the configured time"
    )


# ---------------------------------------------------------------------------
# Regression: KillMode must never be 'mixed' (or the systemd default,
# 'control-group') -- both SIGKILL every process left in the service's
# cgroup on stop/restart, including a tmux server muxplex auto-spawned as
# its own child (which inherits the cgroup). On 2026-07-29 this shipped
# configuration destroyed 44 live tmux sessions during a routine
# `systemctl --user restart muxplex`. Only KillMode=process spares
# processes outside the main PID. See AGENTS.md's "Two ways to destroy
# every live tmux session on this host" and SESSION_PERSISTENCE_DESIGN.md
# section 7.6.
#
# This is a structural fix, not a documented warning: the shipped unit
# template itself must never regress to a cgroup-wide kill mode, on ANY
# machine that runs `muxplex service install` -- not just this host (whose
# local override.conf was, until this fix, the ONLY thing standing between
# a routine restart and repeating the incident).
# ---------------------------------------------------------------------------


def test_systemd_unit_template_kill_mode_is_process_not_mixed():
    """_SYSTEMD_UNIT_TEMPLATE must set KillMode=process.

    Regression test for the exact configuration that destroyed 44 live tmux
    sessions on 2026-07-29: KillMode=mixed (and the systemd default,
    control-group) SIGKILLs every process left in the unit's cgroup on
    stop/restart -- including a tmux server that became a child of muxplex
    (see AGENTS.md's "muxplex auto-spawns the tmux server when none is
    running"). Only KillMode=process is scoped to the main PID alone.
    """
    import muxplex.service as svc

    assert "KillMode=process" in svc._SYSTEMD_UNIT_TEMPLATE, (
        "_SYSTEMD_UNIT_TEMPLATE must set KillMode=process -- KillMode=mixed "
        "(or an absent KillMode, which defaults to control-group) SIGKILLs "
        "every process in the service's cgroup on stop/restart, which is "
        "the exact mechanism that destroyed 44 live tmux sessions on "
        "2026-07-29. This must never regress."
    )
    assert "KillMode=mixed" not in svc._SYSTEMD_UNIT_TEMPLATE
    assert "KillMode=control-group" not in svc._SYSTEMD_UNIT_TEMPLATE


def test_systemd_install_writes_kill_mode_process(monkeypatch, tmp_path):
    """The unit file actually WRITTEN to disk by _systemd_install() must
    contain KillMode=process, not just the in-memory template constant."""
    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)
    monkeypatch.setattr(svc, "_show_tls_nudge_if_needed", lambda: None)

    svc._systemd_install()

    content = unit_path.read_text()
    assert "KillMode=process" in content
    assert "KillMode=mixed" not in content


def test_systemd_install_writes_timeout_stop_sec(monkeypatch, tmp_path):
    """The written unit file must contain TimeoutStopSec."""
    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._systemd_install()

    content = unit_path.read_text()
    assert "TimeoutStopSec" in content, "Written unit file must contain TimeoutStopSec"


def test_launchd_logs_handles_keyboard_interrupt(monkeypatch):
    """service logs must exit cleanly on Ctrl+C on macOS."""
    import muxplex.service as svc

    def mock_run(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(subprocess, "run", mock_run)
    # Should not raise
    svc._launchd_logs()


# ---------------------------------------------------------------------------
# task: TLS nudge hints in service install
# ---------------------------------------------------------------------------


def test_service_install_shows_tls_tip_on_network_host(capsys, tmp_path, monkeypatch):
    """service install must show TLS tip when host is network and TLS disabled."""
    import json

    import muxplex.service as svc
    import muxplex.settings as settings_mod

    # Setup paths
    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"
    settings_file = tmp_path / "settings.json"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    # Setup settings with network host and no TLS
    settings_file.write_text(
        json.dumps({"host": "0.0.0.0", "tls_cert": "", "tls_key": ""})
    )

    # Mock subprocess to avoid actual systemctl calls
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    # Mock the prompt function
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    from muxplex.service import service_install

    service_install()

    out = capsys.readouterr().out
    assert "muxplex setup-tls" in out, (
        f"Expected 'muxplex setup-tls' in service install output when host is 0.0.0.0 and TLS disabled, got: {out!r}"
    )


def test_service_install_hides_tls_tip_on_localhost(capsys, tmp_path, monkeypatch):
    """service install must NOT show TLS tip when host is 127.0.0.1."""
    import json

    import muxplex.service as svc
    import muxplex.settings as settings_mod

    # Setup paths
    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"
    settings_file = tmp_path / "settings.json"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    # Setup settings with localhost
    settings_file.write_text(
        json.dumps({"host": "127.0.0.1", "tls_cert": "", "tls_key": ""})
    )

    # Mock subprocess
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )

    # Mock the prompt function
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    from muxplex.service import service_install

    service_install()

    out = capsys.readouterr().out
    assert "muxplex setup-tls" not in out, (
        f"TLS tip must NOT appear in service install output when host is 127.0.0.1, got: {out!r}"
    )


# ---------------------------------------------------------------------------
# v0.6.7 fix — launchd plist ProgramArguments must use separate <string> tokens
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TMUX_TMPDIR propagation \u2014 systemd/launchd services don't inherit shell rc
# exports, so a customized tmux socket dir must be baked into the unit/plist
# (mirroring the existing PATH propagation) or the service can't see any
# tmux sessions.
# ---------------------------------------------------------------------------


def test_tmux_tmpdir_env_line_empty_when_unset(monkeypatch):
    """_tmux_tmpdir_env_line() returns '' when TMUX_TMPDIR is not set."""
    import os

    import muxplex.service as svc

    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    assert svc._tmux_tmpdir_env_line() == ""
    assert os.environ.get("TMUX_TMPDIR") is None


def test_tmux_tmpdir_env_line_set_when_present(monkeypatch):
    """_tmux_tmpdir_env_line() returns the Environment= line when TMUX_TMPDIR is set."""
    import muxplex.service as svc

    monkeypatch.setenv("TMUX_TMPDIR", "/home/user/.tmux")
    assert svc._tmux_tmpdir_env_line() == "Environment=TMUX_TMPDIR=/home/user/.tmux"


def test_tmux_tmpdir_plist_xml_empty_when_unset(monkeypatch):
    """_tmux_tmpdir_plist_xml() returns '' when TMUX_TMPDIR is not set."""
    import muxplex.service as svc

    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    assert svc._tmux_tmpdir_plist_xml() == ""


def test_tmux_tmpdir_plist_xml_set_when_present(monkeypatch):
    """_tmux_tmpdir_plist_xml() returns the key/string XML block when set."""
    import muxplex.service as svc

    monkeypatch.setenv("TMUX_TMPDIR", "/home/user/.tmux")
    xml = svc._tmux_tmpdir_plist_xml()
    assert "<key>TMUX_TMPDIR</key>" in xml
    assert "<string>/home/user/.tmux</string>" in xml


def test_systemd_install_omits_tmux_tmpdir_when_unset(monkeypatch, tmp_path):
    """Unit file must NOT contain a TMUX_TMPDIR line when the installer's
    environment doesn't have one set."""
    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._systemd_install()

    content = unit_path.read_text()
    assert "TMUX_TMPDIR" not in content, (
        f"Unit file must not mention TMUX_TMPDIR when unset, got: {content!r}"
    )


def test_systemd_install_propagates_custom_tmux_tmpdir(monkeypatch, tmp_path):
    """Unit file must contain Environment=TMUX_TMPDIR=<value> when the
    installer's environment has a customized tmux socket directory.

    Root cause this guards against: systemd --user services do not inherit
    shell rc-file exports, so a user who moved TMUX_TMPDIR away from tmux's
    compiled-in /tmp/tmux-$(id -u) default would have a service-mode muxplex
    silently look at the wrong socket dir and see zero sessions.
    """
    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)
    monkeypatch.setenv("TMUX_TMPDIR", "/home/user/.tmux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._systemd_install()

    content = unit_path.read_text()
    assert "Environment=TMUX_TMPDIR=/home/user/.tmux" in content, (
        f"Unit file must propagate TMUX_TMPDIR, got: {content!r}"
    )


def test_systemd_install_restarts_so_reinstall_applies_new_env(monkeypatch, tmp_path):
    """_systemd_install must call restart (not just enable --now), because
    enable --now is a no-op on an already-running service and would leave a
    re-install's updated environment (e.g. a changed TMUX_TMPDIR) unapplied."""
    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._systemd_install()

    assert ["systemctl", "--user", "restart", "muxplex"] in calls, (
        f"_systemd_install must call restart to apply env changes on re-install, got: {calls}"
    )


def test_launchd_install_omits_tmux_tmpdir_when_unset(monkeypatch, tmp_path):
    """Plist must NOT contain a TMUX_TMPDIR key when unset in the environment."""
    import os

    import muxplex.service as svc

    plist_dir = tmp_path / "LaunchAgents"
    plist_path = plist_dir / "com.muxplex.plist"

    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_DIR", plist_dir)
    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(os, "getuid", lambda: 501)
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._launchd_install()

    content = plist_path.read_text()
    assert "TMUX_TMPDIR" not in content, (
        f"Plist must not mention TMUX_TMPDIR when unset, got: {content!r}"
    )


def test_launchd_install_propagates_custom_tmux_tmpdir(monkeypatch, tmp_path):
    """Plist's EnvironmentVariables dict must contain TMUX_TMPDIR when the
    installer's environment has a customized tmux socket directory."""
    import os
    import plistlib

    import muxplex.service as svc

    plist_dir = tmp_path / "LaunchAgents"
    plist_path = plist_dir / "com.muxplex.plist"

    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_DIR", plist_dir)
    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(os, "getuid", lambda: 501)
    monkeypatch.setenv("TMUX_TMPDIR", "/Users/user/.tmux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._launchd_install()

    plist_data = plistlib.loads(plist_path.read_bytes())
    env_vars = plist_data.get("EnvironmentVariables", {})
    assert env_vars.get("TMUX_TMPDIR") == "/Users/user/.tmux", (
        f"Plist EnvironmentVariables must include TMUX_TMPDIR, got: {env_vars!r}"
    )


def test_launchd_install_boots_out_before_bootstrap(monkeypatch, tmp_path):
    """_launchd_install must bootout any existing load before bootstrap, so a
    re-install's updated plist environment (e.g. a changed TMUX_TMPDIR)
    actually takes effect instead of being ignored by an already-loaded job."""
    import os

    import muxplex.service as svc

    plist_dir = tmp_path / "LaunchAgents"
    plist_path = plist_dir / "com.muxplex.plist"

    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_DIR", plist_dir)
    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(os, "getuid", lambda: 501)

    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: (
            calls.append(list(cmd)),
            subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )[1],
    )
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    svc._launchd_install()

    bootout_index = next(i for i, c in enumerate(calls) if "bootout" in c)
    bootstrap_index = next(i for i, c in enumerate(calls) if "bootstrap" in c)
    assert bootout_index < bootstrap_index, (
        "bootout must be called before bootstrap so re-install applies new env"
    )


def test_launchd_plist_program_arguments_are_separate_strings(monkeypatch, tmp_path):
    """_launchd_install emits each argv token as its own <string> in ProgramArguments.

    The v0.6.6 bug: a single <string> containing e.g.
    "python3 -m muxplex" caused launchd to look for a literal executable
    named "python3 -m muxplex" (with spaces) — which doesn't exist — so the
    daemon silently failed to start on every boot.
    """
    import os
    import plistlib

    import muxplex.service as svc

    plist_dir = tmp_path / "LaunchAgents"
    plist_path = plist_dir / "com.muxplex.plist"

    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_DIR", plist_dir)
    monkeypatch.setattr(svc, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(os, "getuid", lambda: 501)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)
    monkeypatch.setattr(svc, "_show_tls_nudge_if_needed", lambda: None)

    svc._launchd_install()

    assert plist_path.exists(), "plist file must be written by _launchd_install"

    plist_data = plistlib.loads(plist_path.read_bytes())
    prog_args = plist_data.get("ProgramArguments", [])

    assert len(prog_args) >= 2, (
        f"ProgramArguments must have at least 2 elements, got: {prog_args!r}"
    )
    assert prog_args[-1] == "serve", (
        f"Last ProgramArguments element must be 'serve', got: {prog_args!r}"
    )
    for arg in prog_args:
        assert " " not in arg, (
            f"ProgramArguments element must not contain spaces "
            f"(embedded-space arg trap): {arg!r} in {prog_args!r}"
        )


# ── launchd bootstrap race (reported 2026-08-01) ───────────────────────────
#
# `muxplex update` on macOS crashed with a raw CalledProcessError traceback:
#
#   subprocess.CalledProcessError: Command '['launchctl', 'bootstrap',
#   'gui/501', '.../com.muxplex.plist']' returned non-zero exit status 5.
#
# ...immediately after printing "Service started". `launchctl bootout` returns
# before the job is actually gone, so a bootstrap issued into that window fails
# with exit 5 (EIO). It is a race, not a failure, and check=True turned it into
# a crash on a path that had in fact succeeded.
#
# These tests exercise the retry/verify logic directly. They do NOT exercise
# launchctl -- that binary only exists on macOS and this suite runs in a Linux
# container, so the real launchd behaviour is verified by the shape of the
# calls, not by running them.


def _fake_run(sequence, loaded_after=None, calls=None):
    """subprocess.run stand-in returning queued results by command kind."""
    import subprocess as sp

    state = {"bootstraps": 0}

    def run(cmd, *a, **kw):
        if calls is not None:
            calls.append(cmd)
        if cmd[1] == "bootstrap":
            i = min(state["bootstraps"], len(sequence) - 1)
            rc = (
                sequence[state["bootstraps"]]
                if state["bootstraps"] < len(sequence)
                else sequence[i]
            )
            state["bootstraps"] += 1
            return sp.CompletedProcess(cmd, rc, stdout="", stderr="Input/output error")
        if cmd[1] == "print":
            up = loaded_after is not None and state["bootstraps"] >= loaded_after
            return sp.CompletedProcess(cmd, 0 if up else 1, stdout="", stderr="")
        return sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    return run


def test_launchd_bootstrap_retries_through_the_bootout_race(monkeypatch):
    """Exit 5 right after bootout is launchd still tearing down. Retry, don't crash."""
    from muxplex import service

    calls = []
    monkeypatch.setattr(service.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        service.subprocess, "run", _fake_run([5, 5, 0], loaded_after=99, calls=calls)
    )

    service._launchd_bootstrap(501)  # must not raise

    bootstraps = [c for c in calls if c[1] == "bootstrap"]
    assert len(bootstraps) == 3, "should have retried until bootstrap succeeded"


def test_launchd_start_accepts_an_already_running_service(monkeypatch):
    """`start` means "make sure it is running", so already-loaded is success."""
    from muxplex import service

    monkeypatch.setattr(service.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        service.subprocess, "run", _fake_run([5, 5, 5, 5, 5, 5], loaded_after=1)
    )

    service._launchd_bootstrap(501, accept_already_loaded=True)  # must not raise


def test_install_and_restart_refuse_to_call_a_surviving_old_job_success(monkeypatch):
    """The regression that made `muxplex service restart` a silent no-op.

    v0.31.1 treated "already loaded" as success unconditionally. After a bootout
    that had not finished, that meant: bootstrap fails with 5, the OLD job is
    still loaded, we report success -- and the old process keeps serving. The
    user ran `service restart` twice and `doctor` still reported the previous
    version running, with no error either time.

    A stale job is a FAILED replacement. Only `start` may accept already-loaded.
    """
    from muxplex import service

    monkeypatch.setattr(service.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        service.subprocess, "run", _fake_run([5, 5, 5, 5, 5, 5], loaded_after=1)
    )

    with pytest.raises(RuntimeError, match="launchctl bootstrap failed"):
        service._launchd_bootstrap(501)  # default: accept_already_loaded=False


def test_bootout_waits_for_the_job_to_actually_disappear(monkeypatch):
    """launchctl bootout returns before the job is gone. Stop must mean stopped."""
    from muxplex import service

    state = {"polls": 0}

    def run(cmd, *a, **kw):
        import subprocess as sp

        if cmd[1] == "print":
            state["polls"] += 1
            # still loaded for the first two polls, gone on the third
            return sp.CompletedProcess(cmd, 0 if state["polls"] < 3 else 1)
        return sp.CompletedProcess(cmd, 0)

    monkeypatch.setattr(service.time, "sleep", lambda _s: None)
    monkeypatch.setattr(service.subprocess, "run", run)

    assert service._launchd_bootout_and_wait(501) is True
    assert state["polls"] >= 3, "must poll until the job is actually gone"


def test_bootout_reports_failure_when_the_job_outlives_the_timeout(monkeypatch):
    """If the old job will not die, say so -- do not start on top of it."""
    from muxplex import service

    monkeypatch.setattr(service.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda cmd, *a, **kw: __import__("subprocess").CompletedProcess(cmd, 0),
    )

    assert service._launchd_bootout_and_wait(501, timeout=0.0) is False


def test_launchd_bootstrap_fails_loud_on_a_real_error(monkeypatch):
    """A non-race exit code with the service down must still fail -- loudly.

    Loud means an actionable RuntimeError carrying launchd's own stderr, not a
    CalledProcessError traceback out of subprocess internals.
    """
    from muxplex import service

    monkeypatch.setattr(service.time, "sleep", lambda _s: None)
    monkeypatch.setattr(service.subprocess, "run", _fake_run([1], loaded_after=None))

    with pytest.raises(RuntimeError) as exc:
        service._launchd_bootstrap(501)

    message = str(exc.value)
    assert "launchctl bootstrap failed" in message
    assert "muxplex serve" in message, "must offer a way forward"
    assert "Input/output error" in message, "must surface launchd's own stderr"


def test_launchd_bootstrap_does_not_retry_a_non_race_error(monkeypatch):
    """Retrying a genuine error just delays the report. Fail on the first one."""
    from muxplex import service

    calls = []
    monkeypatch.setattr(service.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        service.subprocess, "run", _fake_run([1], loaded_after=None, calls=calls)
    )

    with pytest.raises(RuntimeError):
        service._launchd_bootstrap(501)

    assert len([c for c in calls if c[1] == "bootstrap"]) == 1


# ── Real launchd, not mocked launchd ───────────────────────────────────────
#
# Every other launchd test in this file mocks subprocess.run, so they verify
# the SHAPE of the calls, never launchd's actual behaviour. Three consecutive
# releases shipped macOS-only bugs that shape-checking could not have caught,
# and a human found all three in production:
#
#   v0.31.1  `muxplex update` crashed on a raw `launchctl bootstrap` exit 5
#   v0.31.2  `service restart` silently did nothing -- the v0.31.1 fix treated
#            a still-tearing-down OLD job as proof the NEW one had started
#   v0.31.3  `doctor` called a crash-looping job "running"
#
# The v0.31.2 bug in particular is *only* observable against real launchd:
# it hinges on `bootout` returning before the job is gone, which no mock
# reproduces unless someone already knows to write it that way -- and if they
# knew, they would not have written the bug.
#
# These tests use a THROWAWAY label, never com.muxplex, and clean up in a
# fixture finalizer so a failure cannot leave an agent behind on a CI runner
# or a developer's machine.

HAS_LAUNCHCTL = sys.platform == "darwin" and shutil.which("launchctl") is not None
needs_launchd = pytest.mark.skipif(
    not HAS_LAUNCHCTL, reason="requires macOS with launchctl"
)

_TEST_LABEL = "com.muxplex.selftest"

_TEST_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>-c</string>
        <string>{program}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""

# A job that takes a moment to die on SIGTERM. This detail is load-bearing and
# was found by probing a real Mac, not by reasoning: a plain `/bin/sleep` job
# dies instantly, `bootout` then looks SYNCHRONOUS, and a test built on it
# passes while proving nothing. The race only exists for a process with a
# shutdown sequence -- which is exactly what the real muxplex server is
# (uvicorn draining connections), and exactly why v0.31.2's bug reached
# production but no mock ever caught it.
_SLOW_TO_DIE = 'trap "sleep 3; exit 0" TERM; while :; do sleep 0.2; done'


@pytest.fixture
def throwaway_launchd_job(tmp_path, monkeypatch):
    """A real, disposable launchd agent pointed at /bin/sleep.

    Yields (uid, plist_path). Bootstrapping is the caller's job -- some tests
    need the job absent. Teardown boots it out unconditionally, so neither a
    failing assertion nor an exception can strand a live agent.
    """
    from muxplex import service

    uid = os.getuid()
    plist_path = tmp_path / f"{_TEST_LABEL}.plist"
    plist_path.write_text(_TEST_PLIST.format(label=_TEST_LABEL, program=_SLOW_TO_DIE))

    monkeypatch.setattr(service, "_LAUNCHD_LABEL", _TEST_LABEL)
    monkeypatch.setattr(service, "_LAUNCHD_PLIST_PATH", plist_path)

    try:
        yield uid, plist_path
    finally:
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{_TEST_LABEL}"], capture_output=True
        )


@needs_launchd
def test_real_launchctl_list_output_parses():
    """The `launchctl list` parser must handle REAL output, not a remembered sample.

    _launchd_job_pid_and_exit was written against output pasted from one Mac,
    once. Column widths, tab-vs-space, and the header row are all assumptions
    until a real launchctl produces them.
    """
    from muxplex.cli import _launchd_job_pid_and_exit

    raw = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True, check=False
    )
    assert raw.returncode == 0, "launchctl list should work on any macOS session"
    assert raw.stdout.strip(), "launchctl list returned nothing at all"

    # Must not raise or hang on real output, whatever this machine happens to run.
    pid, status = _launchd_job_pid_and_exit()
    assert pid is None or isinstance(pid, int)
    assert status is None or isinstance(status, int)


@needs_launchd
def test_real_launchd_reports_a_running_job_as_running(throwaway_launchd_job):
    """A bootstrapped job must read as loaded, with a real pid."""
    from muxplex import service

    uid, plist_path = throwaway_launchd_job

    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "could not bootstrap a throwaway launchd agent on this machine "
            f"(exit {result.returncode}): {(result.stderr or '').strip()}. "
            "If this is a CI runner without a usable gui/ domain, that is a real "
            "finding about what this job can and cannot cover -- not something to "
            "paper over with a skip."
        )

    assert service._launchd_is_loaded(uid) is True


@needs_launchd
def test_real_bootout_is_asynchronous_and_the_wait_covers_it(throwaway_launchd_job):
    """THE v0.31.2 REGRESSION, against real launchd.

    `launchctl bootout` returns before the job is actually gone. v0.31.2's
    restart booted out, immediately bootstrapped, saw the OLD job still loaded,
    and called that success -- so `service restart` silently kept the old
    process. _launchd_bootout_and_wait exists to close that window, and this is
    the only test in the suite that can prove it against the real thing.
    """
    from muxplex import service

    uid, plist_path = throwaway_launchd_job

    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], capture_output=True
    )
    assert service._launchd_is_loaded(uid) is True, "fixture job failed to start"

    # Issue the bootout by hand first and look immediately, the way v0.31.2 did.
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{_TEST_LABEL}"], capture_output=True
    )
    still_there = service._launchd_is_loaded(uid)

    # Now the real thing must close that window.
    assert service._launchd_bootout_and_wait(uid) is True
    assert service._launchd_is_loaded(uid) is False, (
        "bootout_and_wait returned True while the job was still loaded -- "
        "exactly the false-success that made `service restart` a no-op"
    )

    assert still_there, (
        "This launchd tore the job down synchronously, so the naive "
        "check-immediately approach would have passed too -- meaning this test "
        "did not actually exercise the race it exists to cover. The fixture "
        "program is supposed to be slow to die; if that stopped being true, "
        "this test is now a tautology and needs rebuilding, not muting."
    )


@needs_launchd
def test_real_bootstrap_refuses_to_call_a_surviving_job_success(
    throwaway_launchd_job,
):
    """install/restart must NOT report success when the old job is still there.

    accept_already_loaded=False is the guard that keeps a failed replacement
    from reading as a successful one.
    """
    from muxplex import service

    uid, plist_path = throwaway_launchd_job

    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], capture_output=True
    )
    assert service._launchd_is_loaded(uid) is True, "fixture job failed to start"

    # Bootstrapping over a live job is what a botched restart looks like.
    with pytest.raises(RuntimeError, match="launchctl bootstrap failed"):
        service._launchd_bootstrap(uid, attempts=2)

    # ...while `start` -- "make sure something is running" -- accepts it.
    service._launchd_bootstrap(uid, attempts=2, accept_already_loaded=True)

"""Tests for muxplex/service.py — system service management module."""

import subprocess
import sys


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
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

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
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

    svc._systemd_uninstall()

    assert ["systemctl", "--user", "stop", "muxplex"] in calls, "stop not called"
    assert ["systemctl", "--user", "disable", "muxplex"] in calls, "disable not called"
    assert ["systemctl", "--user", "daemon-reload"] in calls, "daemon-reload not called"
    assert not unit_path.exists(), "unit file was not deleted"


def test_systemd_start_calls_systemctl(monkeypatch):
    """_systemd_start runs ['systemctl', '--user', 'start', 'muxplex']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))
    svc._systemd_start()
    assert ["systemctl", "--user", "start", "muxplex"] in calls


def test_systemd_stop_calls_systemctl(monkeypatch):
    """_systemd_stop runs ['systemctl', '--user', 'stop', 'muxplex']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))
    svc._systemd_stop()
    assert ["systemctl", "--user", "stop", "muxplex"] in calls


def test_systemd_restart_calls_systemctl(monkeypatch):
    """_systemd_restart runs ['systemctl', '--user', 'restart', 'muxplex']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))
    svc._systemd_restart()
    assert ["systemctl", "--user", "restart", "muxplex"] in calls


def test_systemd_status_calls_systemctl(monkeypatch):
    """_systemd_status runs ['systemctl', '--user', 'status', 'muxplex', '--no-pager']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))
    svc._systemd_status()
    assert ["systemctl", "--user", "status", "muxplex", "--no-pager"] in calls


def test_systemd_logs_calls_journalctl(monkeypatch):
    """_systemd_logs runs ['journalctl', '--user', '-u', 'muxplex', '-f']."""
    import muxplex.service as svc

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))
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
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

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
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

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
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

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
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

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
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

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
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

    svc._launchd_status()

    print_calls = [c for c in calls if "print" in c]
    assert print_calls, "launchctl print not called"
    print_cmd = print_calls[0]
    assert "gui/501/com.muxplex" in " ".join(print_cmd), (
        f"print must reference gui/501/com.muxplex, got: {print_cmd}"
    )

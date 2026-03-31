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

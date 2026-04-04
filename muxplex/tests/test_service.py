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


# ---------------------------------------------------------------------------
# C1 regression tests — check=True must NOT be set on idempotent/informational
# operations: status, stop, uninstall (stop+disable for systemd, bootout for launchd)
# ---------------------------------------------------------------------------


def _make_kwargs_capture():
    """Return (calls_with_kw, monkeypatch_fn) for capturing subprocess.run kwargs."""
    calls_with_kw: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kw):
        calls_with_kw.append((list(cmd), dict(kw)))

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


def test_systemd_install_writes_timeout_stop_sec(monkeypatch, tmp_path):
    """The written unit file must contain TimeoutStopSec."""
    import muxplex.service as svc

    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"

    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", unit_path)

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))
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
    settings_file.write_text(json.dumps({"host": "0.0.0.0", "tls_cert": "", "tls_key": ""}))

    # Mock subprocess to avoid actual systemctl calls
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

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
    settings_file.write_text(json.dumps({"host": "127.0.0.1", "tls_cert": "", "tls_key": ""}))

    # Mock subprocess
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))

    # Mock the prompt function
    monkeypatch.setattr(svc, "_prompt_host_if_localhost", lambda: None)

    from muxplex.service import service_install

    service_install()

    out = capsys.readouterr().out
    assert "muxplex setup-tls" not in out, (
        f"TLS tip must NOT appear in service install output when host is 127.0.0.1, got: {out!r}"
    )

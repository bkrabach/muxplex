"""Tests for muxplex/cli.py — CLI entry point."""

import json
import os
import shutil
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

def test_cli_module_importable():
    """muxplex.cli must be importable."""
    from muxplex.cli import main  # noqa: F401

def test_main_calls_serve_by_default():
    """Calling main() with no args must invoke serve() with None defaults (settings layer resolves)."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        mock_serve.assert_called_once_with(
            host=None, port=None, auth=None, session_ttl=None
        )

def test_main_passes_custom_host_and_port():
    """main() with --host/--port must forward them to serve(); unset flags are None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--host", "192.168.1.1", "--port", "9000"]):
            main()
        mock_serve.assert_called_once_with(
            host="192.168.1.1", port=9000, auth=None, session_ttl=None
        )

def test_main_default_host_is_localhost():
    """Default --host must be None (settings layer resolves to 127.0.0.1)."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        _, kwargs = mock_serve.call_args
        assert kwargs["host"] is None

def test_main_passes_auth_flag():
    """main() with --auth password must forward auth='password'; unset flags are None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--auth", "password"]):
            main()
        mock_serve.assert_called_once_with(
            host=None, port=None, auth="password", session_ttl=None
        )

def test_main_passes_session_ttl_flag():
    """main() with --session-ttl 3600 must forward session_ttl=3600; unset flags are None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--session-ttl", "3600"]):
            main()
        mock_serve.assert_called_once_with(
            host=None, port=None, auth=None, session_ttl=3600
        )

def test_show_password_prints_password_from_file(tmp_path, monkeypatch, capsys):
    """show_password() prints the password when MUXPLEX_AUTH=password and file exists."""
    from muxplex.cli import show_password

    # Set up fake home with password file
    fake_home = tmp_path / "home"
    pw_dir = fake_home / ".config" / "muxplex"
    pw_dir.mkdir(parents=True)
    pw_file = pw_dir / "password"
    pw_file.write_text("my-test-password\n")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")

    show_password()

    captured = capsys.readouterr()
    assert "my-test-password" in captured.out

def test_show_password_no_file(tmp_path, monkeypatch, capsys):
    """show_password() tells user no file found when in password mode with no file."""
    from muxplex.cli import show_password

    # Set up fake home WITHOUT password file
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("MUXPLEX_AUTH", "password")

    show_password()

    captured = capsys.readouterr()
    output_lower = captured.out.lower()
    assert "no password" in output_lower or "not found" in output_lower

def test_show_password_pam_mode(monkeypatch, capsys):
    """show_password() reports PAM mode when pam_available() is True and not password mode."""
    from muxplex.cli import show_password

    monkeypatch.delenv("MUXPLEX_AUTH", raising=False)

    with patch("muxplex.cli.pam_available", return_value=True):
        show_password()

    captured = capsys.readouterr()
    assert "pam" in captured.out.lower()

def test_reset_secret_writes_new_secret(tmp_path, monkeypatch):
    """reset_secret() writes a new secret file with content longer than 20 chars."""
    from muxplex.cli import reset_secret

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    reset_secret()

    secret_path = fake_home / ".config" / "muxplex" / "secret"
    assert secret_path.exists(), "Secret file must be created"
    content = secret_path.read_text().strip()
    assert len(content) > 20, f"Secret must be longer than 20 chars, got {len(content)}"

def test_reset_secret_sets_0600_permissions(tmp_path, monkeypatch):
    """reset_secret() sets file permissions to 0o600."""
    from muxplex.cli import reset_secret

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    reset_secret()

    secret_path = fake_home / ".config" / "muxplex" / "secret"
    file_mode = stat.S_IMODE(secret_path.stat().st_mode)
    assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"

def test_reset_secret_prints_warning(tmp_path, monkeypatch, capsys):
    """reset_secret() prints a warning that sessions are now invalid."""
    from muxplex.cli import reset_secret

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    reset_secret()

    captured = capsys.readouterr()
    output_lower = captured.out.lower()
    assert "invalid" in output_lower or "warning" in output_lower, (
        f"Expected 'invalid' or 'warning' in output, got: {captured.out!r}"
    )

def test_check_dependencies_exits_when_ttyd_missing(monkeypatch):
    """_check_dependencies() must sys.exit(1) when ttyd is not in PATH."""
    import shutil
    import pytest
    from muxplex.cli import _check_dependencies

    orig_which = shutil.which

    def fake_which(name):
        if name == "ttyd":
            return None
        return orig_which(name)

    monkeypatch.setattr(shutil, "which", fake_which)

    with pytest.raises(SystemExit) as exc_info:
        _check_dependencies()
    assert exc_info.value.code == 1

def test_check_dependencies_exits_when_tmux_missing(monkeypatch):
    """_check_dependencies() must sys.exit(1) when tmux is not in PATH."""
    import shutil
    import pytest
    from muxplex.cli import _check_dependencies

    orig_which = shutil.which

    def fake_which(name):
        if name == "tmux":
            return None
        return orig_which(name)

    monkeypatch.setattr(shutil, "which", fake_which)

    with pytest.raises(SystemExit) as exc_info:
        _check_dependencies()
    assert exc_info.value.code == 1

def test_check_dependencies_passes_when_all_present(monkeypatch):
    """_check_dependencies() must not raise when both tmux and ttyd are found."""
    import shutil
    from muxplex.cli import _check_dependencies

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    # Should not raise
    _check_dependencies()

def test_main_check_dependencies_called_for_serve(monkeypatch):
    """main() must call _check_dependencies() when subcommand is serve."""
    from muxplex.cli import main

    calls = []
    monkeypatch.setattr("muxplex.cli._check_dependencies", lambda: calls.append(True))

    with patch("muxplex.cli.serve"):
        with patch("sys.argv", ["muxplex"]):
            main()

    assert len(calls) == 1, "_check_dependencies must be called once for serve"

def test_dunder_main_calls_main():
    """python -m muxplex must call cli.main()."""
    import importlib.util

    # Locate __main__.py without executing it (find_spec does not import)
    spec = importlib.util.find_spec("muxplex.__main__")
    assert spec is not None and spec.origin is not None

    with patch("muxplex.cli.main") as mock_main:
        exec(Path(spec.origin).read_text())  # noqa: S102
        mock_main.assert_called_once()

# ---------------------------------------------------------------------------
# doctor() tests
# ---------------------------------------------------------------------------

def test_doctor_shows_python_version(capsys):
    """doctor must show Python version."""
    from muxplex.cli import doctor

    doctor()
    out = capsys.readouterr().out
    assert "Python" in out

def test_doctor_checks_tmux(capsys, monkeypatch):
    """doctor must check for tmux."""
    import subprocess

    from muxplex.cli import doctor

    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/tmux" if name == "tmux" else None
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: type(
            "R", (), {"returncode": 0, "stdout": "tmux 3.4", "stderr": ""}
        )(),
    )
    doctor()
    out = capsys.readouterr().out
    assert "tmux" in out

def test_doctor_reports_missing_ttyd(capsys, monkeypatch):
    """doctor must report when ttyd is missing."""
    from muxplex.cli import doctor

    original_which = shutil.which

    def mock_which(name):
        if name == "ttyd":
            return None
        return original_which(name)

    monkeypatch.setattr("shutil.which", mock_which)
    doctor()
    out = capsys.readouterr().out
    assert "ttyd" in out
    assert "not found" in out

def test_doctor_shows_platform(capsys):
    """doctor must show platform info."""
    from muxplex.cli import doctor

    doctor()
    out = capsys.readouterr().out
    assert "Platform" in out

def test_doctor_subcommand_registered():
    """doctor must be a valid subcommand in main() argparse."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue().lower()
    assert "doctor" in help_text

def test_main_dispatches_to_doctor(monkeypatch):
    """main() with 'doctor' subcommand must invoke doctor()."""
    from muxplex.cli import main

    calls = []
    monkeypatch.setattr("muxplex.cli.doctor", lambda: calls.append(True))

    with patch("sys.argv", ["muxplex", "doctor"]):
        main()

    assert len(calls) == 1, (
        "doctor() must be called once when 'doctor' subcommand is used"
    )

# ---------------------------------------------------------------------------
# upgrade / update subcommand tests
# ---------------------------------------------------------------------------

def test_upgrade_subcommand_registered():
    """upgrade must be a valid subcommand."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue().lower()
    assert "upgrade" in help_text

def test_update_alias_registered():
    """update must be a valid subcommand (alias for upgrade)."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue().lower()
    assert "update" in help_text

def test_upgrade_calls_uv_tool_install(monkeypatch, capsys):
    """upgrade must attempt uv tool install when update is available."""
    import subprocess

    import muxplex.cli as cli_mod

    calls = []

    def mock_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    # Mock version check so upgrade proceeds regardless of local install type
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (abc12345 → def67890)"),
    )

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()

    # Should have called uv tool install
    uv_calls = [c for c in calls if isinstance(c, list) and "uv" in str(c)]
    assert len(uv_calls) > 0, "upgrade must call uv tool install"

def test_main_dispatches_to_upgrade(monkeypatch):
    """main() with 'upgrade' subcommand must invoke upgrade()."""
    from muxplex.cli import main

    calls = []
    monkeypatch.setattr("muxplex.cli.upgrade", lambda force=False: calls.append(True))

    with patch("sys.argv", ["muxplex", "upgrade"]):
        main()

    assert len(calls) == 1, "upgrade() must be called once for 'upgrade' subcommand"

def test_main_dispatches_update_to_upgrade(monkeypatch):
    """main() with 'update' subcommand must also invoke upgrade()."""
    from muxplex.cli import main

    calls = []
    monkeypatch.setattr("muxplex.cli.upgrade", lambda force=False: calls.append(True))

    with patch("sys.argv", ["muxplex", "update"]):
        main()

    assert len(calls) == 1, "upgrade() must be called once for 'update' subcommand"

# ---------------------------------------------------------------------------
# Smart version-check tests (_get_install_info / _check_for_update)
# ---------------------------------------------------------------------------

def test_get_install_info_returns_dict():
    """_get_install_info must return a dict with all required keys."""
    from muxplex.cli import _get_install_info

    info = _get_install_info()
    assert "source" in info
    assert "version" in info
    assert "commit" in info
    assert "url" in info
    assert info["source"] in ("git", "editable", "pypi", "unknown")

def test_check_for_update_editable_returns_false():
    """Editable installs must never suggest an update."""
    from muxplex.cli import _check_for_update

    info = {"source": "editable", "version": "0.1.0", "commit": None, "url": None}
    available, msg = _check_for_update(info)
    assert available is False
    assert "editable" in msg

def test_upgrade_force_skips_version_check(monkeypatch, capsys):
    """upgrade(force=True) must skip the version check and proceed to install."""
    import subprocess

    import muxplex.cli as cli_mod

    calls = []

    def mock_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    # With force=True the version check must be bypassed entirely
    check_calls = []
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: check_calls.append(info) or (True, "should not be reached"),
    )

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade(force=True)

    # _check_for_update must NOT have been called when force=True
    assert len(check_calls) == 0, "Version check must be skipped when force=True"
    # uv install must still be attempted
    uv_calls = [c for c in calls if isinstance(c, list) and "uv" in str(c)]
    assert len(uv_calls) > 0, "upgrade(force=True) must still call uv tool install"

def test_upgrade_already_up_to_date_skips_install(monkeypatch, capsys):
    """upgrade() must print 'up to date' and NOT call uv when version check says current."""
    import subprocess

    import muxplex.cli as cli_mod

    calls = []

    def mock_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (False, "up to date (commit abcd1234)"),
    )

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()

    out = capsys.readouterr().out
    assert "up to date" in out.lower() or "already" in out.lower()
    # uv install must NOT have been called
    uv_calls = [c for c in calls if isinstance(c, list) and "uv" in str(c)]
    assert len(uv_calls) == 0, "uv must NOT be called when already up to date"

def test_upgrade_force_flag_registered():
    """upgrade --force must be accepted by argparse without error."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "upgrade", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue()
    assert "--force" in help_text

# ---------------------------------------------------------------------------
# serve() settings.json integration tests
# ---------------------------------------------------------------------------

def test_serve_reads_host_from_settings(tmp_path, monkeypatch):
    """serve(host=None) must use host from settings.json."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"host": "192.168.0.1"}))

    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve

            serve(host=None)

    assert len(calls) == 1
    assert calls[0]["host"] == "192.168.0.1"

def test_serve_cli_flag_overrides_settings(tmp_path, monkeypatch):
    """serve(host='10.0.0.1') must override settings.json host."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"host": "192.168.0.1"}))

    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve

            serve(host="10.0.0.1")

    assert len(calls) == 1
    assert calls[0]["host"] == "10.0.0.1"

def test_serve_falls_back_to_default_when_no_settings_file(tmp_path, monkeypatch):
    """serve() with no settings file and no CLI flags uses hardcoded defaults."""
    settings_file = tmp_path / "nonexistent_settings.json"
    # Deliberately not written — file does not exist

    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve

            serve()

    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8088

def test_serve_port_from_settings(tmp_path, monkeypatch):
    """serve(port=None) must use port from settings.json."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"port": 9999}))

    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve

            serve(port=None)

    assert len(calls) == 1
    assert calls[0]["port"] == 9999

def test_serve_session_ttl_from_settings(tmp_path, monkeypatch):
    """serve(session_ttl=None) must use session_ttl from settings.json."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"session_ttl": 3600}))

    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)
    monkeypatch.delenv("MUXPLEX_SESSION_TTL", raising=False)

    with patch("uvicorn.run"):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve

            serve(session_ttl=None)

    assert os.environ.get("MUXPLEX_SESSION_TTL") == "3600"

def test_serve_session_ttl_zero_is_valid(tmp_path, monkeypatch):
    """serve(session_ttl=0) must work — 0 means browser session, a valid value."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"session_ttl": 3600}))

    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)
    monkeypatch.delenv("MUXPLEX_SESSION_TTL", raising=False)

    with patch("uvicorn.run"):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            from muxplex.cli import serve

            serve(session_ttl=0)

    assert os.environ.get("MUXPLEX_SESSION_TTL") == "0"

# ---------------------------------------------------------------------------
# argparse refactoring tests — None defaults, serve flags on both parsers,
# upgrade alias
# ---------------------------------------------------------------------------

def test_main_passes_none_for_unset_flags():
    """main() with no flags passes None for host/port/auth/session_ttl to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        mock_serve.assert_called_once_with(
            host=None, port=None, auth=None, session_ttl=None
        )

def test_main_passes_explicit_host_only():
    """main() with --host 10.0.0.1 passes host='10.0.0.1', others as None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--host", "10.0.0.1"]):
            main()
        mock_serve.assert_called_once_with(
            host="10.0.0.1", port=None, auth=None, session_ttl=None
        )

def test_main_serve_subcommand_accepts_flags():
    """'muxplex serve --host 10.0.0.1 --port 9000' passes values to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch(
            "sys.argv", ["muxplex", "serve", "--host", "10.0.0.1", "--port", "9000"]
        ):
            main()
        mock_serve.assert_called_once_with(
            host="10.0.0.1", port=9000, auth=None, session_ttl=None
        )

def test_help_shows_single_upgrade_line():
    """Help output shows 'upgrade (update)' alias notation, not two separate subcommand entries."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue()
    # With aliases=['update'], argparse renders: 'upgrade (update)   description'
    # With separate parsers, 'upgrade' and 'update' each have their own help lines
    assert "upgrade (update)" in help_text, (
        "upgrade and update must appear as alias notation 'upgrade (update)', not two separate entries. "
        f"Got help text:\n{help_text}"
    )

def test_doctor_shows_serve_config(tmp_path, monkeypatch, capsys):
    """doctor() must show the current serve config (host, port, auth)."""
    import json

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"host": "0.0.0.0", "port": 9999, "auth": "password"})
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    assert "0.0.0.0" in out
    assert "9999" in out
    assert "password" in out

# ---------------------------------------------------------------------------
# service subcommand dispatch tests
# ---------------------------------------------------------------------------

def test_service_install_dispatches():
    """muxplex service install must call service_install()."""
    from muxplex.cli import main

    with patch("muxplex.service.service_install") as mock_fn:
        with patch("sys.argv", ["muxplex", "service", "install"]):
            main()
    mock_fn.assert_called_once()

def test_service_uninstall_dispatches():
    """muxplex service uninstall must call service_uninstall()."""
    from muxplex.cli import main

    with patch("muxplex.service.service_uninstall") as mock_fn:
        with patch("sys.argv", ["muxplex", "service", "uninstall"]):
            main()
    mock_fn.assert_called_once()

def test_service_start_dispatches():
    """muxplex service start must call service_start()."""
    from muxplex.cli import main

    with patch("muxplex.service.service_start") as mock_fn:
        with patch("sys.argv", ["muxplex", "service", "start"]):
            main()
    mock_fn.assert_called_once()

def test_service_stop_dispatches():
    """muxplex service stop must call service_stop()."""
    from muxplex.cli import main

    with patch("muxplex.service.service_stop") as mock_fn:
        with patch("sys.argv", ["muxplex", "service", "stop"]):
            main()
    mock_fn.assert_called_once()

def test_service_restart_dispatches():
    """muxplex service restart must call service_restart()."""
    from muxplex.cli import main

    with patch("muxplex.service.service_restart") as mock_fn:
        with patch("sys.argv", ["muxplex", "service", "restart"]):
            main()
    mock_fn.assert_called_once()

def test_service_status_dispatches():
    """muxplex service status must call service_status()."""
    from muxplex.cli import main

    with patch("muxplex.service.service_status") as mock_fn:
        with patch("sys.argv", ["muxplex", "service", "status"]):
            main()
    mock_fn.assert_called_once()

def test_service_logs_dispatches():
    """muxplex service logs must call service_logs()."""
    from muxplex.cli import main

    with patch("muxplex.service.service_logs") as mock_fn:
        with patch("sys.argv", ["muxplex", "service", "logs"]):
            main()
    mock_fn.assert_called_once()

def test_service_subcommand_in_help():
    """'service' must appear in muxplex --help output."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue().lower()
    assert "service" in help_text

# ---------------------------------------------------------------------------
# task-6: Verify old launchd/systemd helpers removed from cli.py
# ---------------------------------------------------------------------------

def test_old_install_launchd_removed_from_cli():
    """_install_launchd must no longer exist in muxplex.cli (moved to muxplex.service)."""
    import muxplex.cli as cli_mod

    assert not hasattr(cli_mod, "_install_launchd"), (
        "_install_launchd should be removed from cli.py; functionality is in muxplex.service"
    )

def test_old_install_systemd_removed_from_cli():
    """_install_systemd must no longer exist in muxplex.cli (moved to muxplex.service)."""
    import muxplex.cli as cli_mod

    assert not hasattr(cli_mod, "_install_systemd"), (
        "_install_systemd should be removed from cli.py; functionality is in muxplex.service"
    )

def test_upgrade_uses_service_module_install(monkeypatch, capsys):
    """upgrade() must call muxplex.service.service_install."""
    import subprocess

    import muxplex.cli as cli_mod

    calls = []

    def mock_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (abc12345 \u2192 def67890)"),
    )

    service_install_calls = []
    with patch(
        "muxplex.service.service_install", lambda: service_install_calls.append(True)
    ):
        cli_mod.upgrade()

    assert len(service_install_calls) > 0, (
        "upgrade() must call muxplex.service.service_install() to regenerate the service file"
    )

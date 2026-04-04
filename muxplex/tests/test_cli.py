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
            host=None,
            port=None,
            auth=None,
            session_ttl=None,
            tls_cert=None,
            tls_key=None,
        )


def test_main_passes_custom_host_and_port():
    """main() with --host/--port must forward them to serve(); unset flags are None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--host", "192.168.1.1", "--port", "9000"]):
            main()
        mock_serve.assert_called_once_with(
            host="192.168.1.1",
            port=9000,
            auth=None,
            session_ttl=None,
            tls_cert=None,
            tls_key=None,
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
            host=None,
            port=None,
            auth="password",
            session_ttl=None,
            tls_cert=None,
            tls_key=None,
        )


def test_main_passes_session_ttl_flag():
    """main() with --session-ttl 3600 must forward session_ttl=3600; unset flags are None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--session-ttl", "3600"]):
            main()
        mock_serve.assert_called_once_with(
            host=None,
            port=None,
            auth=None,
            session_ttl=3600,
            tls_cert=None,
            tls_key=None,
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
    """main() with no flags passes None for host/port/auth/session_ttl/tls_cert/tls_key to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        mock_serve.assert_called_once_with(
            host=None,
            port=None,
            auth=None,
            session_ttl=None,
            tls_cert=None,
            tls_key=None,
        )


def test_main_passes_explicit_host_only():
    """main() with --host 10.0.0.1 passes host='10.0.0.1', others as None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--host", "10.0.0.1"]):
            main()
        mock_serve.assert_called_once_with(
            host="10.0.0.1",
            port=None,
            auth=None,
            session_ttl=None,
            tls_cert=None,
            tls_key=None,
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
            host="10.0.0.1",
            port=9000,
            auth=None,
            session_ttl=None,
            tls_cert=None,
            tls_key=None,
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


# ---------------------------------------------------------------------------
# config subcommand tests
# ---------------------------------------------------------------------------


def test_config_list_shows_all_keys(capsys, tmp_path, monkeypatch):
    """config list must show all DEFAULT_SETTINGS keys."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "s.json")

    from muxplex.cli import config_list

    config_list()
    out = capsys.readouterr().out
    for key in settings_mod.DEFAULT_SETTINGS:
        assert key in out, f"config list must show '{key}'"


def test_config_get_returns_value(capsys, tmp_path, monkeypatch):
    """config get must return the value of a known key."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "s.json")

    from muxplex.cli import config_get

    config_get("port")
    out = capsys.readouterr().out.strip()
    assert out == "8088"


def test_config_get_unknown_key_exits(tmp_path, monkeypatch):
    """config get with unknown key must exit 1."""
    import pytest
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "s.json")

    from muxplex.cli import config_get

    with pytest.raises(SystemExit):
        config_get("nonexistent_key")


def test_config_set_persists_value(tmp_path, monkeypatch):
    """config set must persist the value to settings.json."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "s.json")

    from muxplex.cli import config_set

    config_set("host", "0.0.0.0")

    settings = settings_mod.load_settings()
    assert settings["host"] == "0.0.0.0"


def test_config_set_coerces_int(tmp_path, monkeypatch):
    """config set must coerce port to int."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "s.json")

    from muxplex.cli import config_set

    config_set("port", "9090")

    settings = settings_mod.load_settings()
    assert settings["port"] == 9090


def test_config_set_coerces_bool(tmp_path, monkeypatch):
    """config set must coerce booleans."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "s.json")

    from muxplex.cli import config_set

    config_set("window_size_largest", "true")

    settings = settings_mod.load_settings()
    assert settings["window_size_largest"] is True


def test_config_reset_all(tmp_path, monkeypatch):
    """config reset (no key) must reset all settings to defaults."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "s.json")

    from muxplex.cli import config_set, config_reset

    config_set("host", "0.0.0.0")
    config_set("port", "9090")
    config_reset(None)

    settings = settings_mod.load_settings()
    assert settings["host"] == "127.0.0.1"
    assert settings["port"] == 8088


def test_config_reset_single_key(tmp_path, monkeypatch):
    """config reset <key> must reset only that key."""
    import muxplex.settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "s.json")

    from muxplex.cli import config_set, config_reset

    config_set("host", "0.0.0.0")
    config_set("port", "9090")
    config_reset("host")

    settings = settings_mod.load_settings()
    assert settings["host"] == "127.0.0.1"
    assert settings["port"] == 9090  # unchanged


def test_config_subcommand_registered():
    """config must appear in --help."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "muxplex", "config", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "list" in result.stdout
    assert "get" in result.stdout
    assert "set" in result.stdout
    assert "reset" in result.stdout


# ---------------------------------------------------------------------------
# task-3: generate-federation-key subcommand tests
# ---------------------------------------------------------------------------


def test_generate_federation_key_creates_file(tmp_path, monkeypatch, capsys):
    """generate_federation_key() creates key file with mode 0600 and prints key info."""
    import muxplex.settings as settings_mod

    key_file = tmp_path / ".config" / "muxplex" / "federation_key"
    monkeypatch.setattr(settings_mod, "FEDERATION_KEY_PATH", key_file)

    from muxplex.cli import generate_federation_key

    generate_federation_key()

    # File must exist
    assert key_file.exists(), "Federation key file must be created"

    # Content must be longer than 20 chars (stripping the trailing newline)
    content = key_file.read_text().strip()
    assert len(content) > 20, f"Key must be > 20 chars, got {len(content)}"

    # File mode must be 0600
    file_mode = stat.S_IMODE(key_file.stat().st_mode)
    assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"

    # Output must include key info
    captured = capsys.readouterr()
    assert "federation" in captured.out.lower() or "key" in captured.out.lower(), (
        f"Output must mention key info, got: {captured.out!r}"
    )
    # The actual key value must appear in output
    assert content in captured.out, "Key value must appear in output"


def test_main_dispatches_to_generate_federation_key(monkeypatch):
    """main() with 'generate-federation-key' subcommand must invoke generate_federation_key()."""
    import muxplex.cli as cli_mod

    calls = []
    monkeypatch.setattr(cli_mod, "generate_federation_key", lambda: calls.append(True))
    with patch("sys.argv", ["muxplex", "generate-federation-key"]):
        cli_mod.main()
    assert calls, (
        "generate_federation_key() must be called once for 'generate-federation-key' subcommand"
    )


# ---------------------------------------------------------------------------
# task: port-in-use crash-loop prevention — _kill_stale_port_holder
# ---------------------------------------------------------------------------


def test_kill_stale_port_holder_exists():
    """_kill_stale_port_holder must be importable from muxplex.cli."""
    from muxplex.cli import _kill_stale_port_holder  # noqa: F401


def test_kill_stale_port_holder_runs_lsof(monkeypatch):
    """_kill_stale_port_holder must invoke lsof -ti :<port> to find occupying PIDs."""
    import subprocess
    import muxplex.cli as cli_mod

    lsof_calls = []

    def fake_run(cmd, **kw):
        lsof_calls.append(cmd)
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    cli_mod._kill_stale_port_holder(8088)

    assert any("lsof" in str(c) for c in lsof_calls), (
        "_kill_stale_port_holder must call lsof to discover port occupants"
    )
    assert any("8088" in str(c) for c in lsof_calls), (
        "_kill_stale_port_holder must include the port number in the lsof call"
    )


def test_kill_stale_port_holder_kills_foreign_pid(monkeypatch):
    """_kill_stale_port_holder must send SIGTERM to PIDs that are not our own."""
    import os
    import signal
    import subprocess
    import muxplex.cli as cli_mod

    foreign_pid = 99999
    killed = []

    def fake_run(cmd, **kw):
        return type(
            "R", (), {"returncode": 0, "stdout": f"{foreign_pid}\n", "stderr": ""}
        )()

    def fake_kill(pid, sig):
        killed.append((pid, sig))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(os, "getpid", lambda: 12345)  # not the same as foreign_pid

    # Patch time.sleep so test doesn't actually sleep
    import time

    monkeypatch.setattr(time, "sleep", lambda _: None)

    cli_mod._kill_stale_port_holder(8088)

    assert (foreign_pid, signal.SIGTERM) in killed, (
        f"Expected SIGTERM sent to foreign PID {foreign_pid}, got: {killed}"
    )


def test_kill_stale_port_holder_skips_own_pid(monkeypatch):
    """_kill_stale_port_holder must NOT kill its own PID."""
    import os
    import subprocess
    import muxplex.cli as cli_mod

    my_pid = 12345
    killed = []

    def fake_run(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": f"{my_pid}\n", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(os, "getpid", lambda: my_pid)

    import time

    monkeypatch.setattr(time, "sleep", lambda _: None)

    cli_mod._kill_stale_port_holder(8088)

    assert my_pid not in killed, "_kill_stale_port_holder must not kill its own PID"


def test_kill_stale_port_holder_survives_lsof_not_available(monkeypatch):
    """_kill_stale_port_holder must not raise when lsof is unavailable."""
    import subprocess
    import muxplex.cli as cli_mod

    def fake_run(cmd, **kw):
        raise FileNotFoundError("lsof not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Should not raise
    cli_mod._kill_stale_port_holder(8088)


def test_serve_calls_kill_stale_port_holder(tmp_path, monkeypatch):
    """serve() must call _kill_stale_port_holder(port) before starting uvicorn."""
    import muxplex.cli as cli_mod

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    killed_ports = []
    monkeypatch.setattr(
        cli_mod, "_kill_stale_port_holder", lambda port: killed_ports.append(port)
    )

    with patch("uvicorn.run"):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            cli_mod.serve(port=9876)

    assert 9876 in killed_ports, (
        "serve() must call _kill_stale_port_holder with the resolved port before uvicorn.run"
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


# ---------------------------------------------------------------------------
# task-2-serve-ssl: serve() TLS / SSL parameter tests
# ---------------------------------------------------------------------------


def test_serve_passes_ssl_params_to_uvicorn(tmp_path, monkeypatch):
    """serve() with valid tls_cert and tls_key paths must pass ssl_certfile/ssl_keyfile to uvicorn."""
    import muxplex.cli as cli_mod

    # Create real cert/key files
    cert_file = tmp_path / "server.crt"
    key_file = tmp_path / "server.key"
    cert_file.write_text("fake cert content")
    key_file.write_text("fake key content")

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    uvicorn_calls = []

    def fake_run(*args, **kwargs):
        uvicorn_calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            cli_mod.serve(tls_cert=str(cert_file), tls_key=str(key_file))

    assert len(uvicorn_calls) == 1
    kwargs = uvicorn_calls[0]
    assert "ssl_certfile" in kwargs, (
        "uvicorn.run must receive ssl_certfile when TLS paths are set"
    )
    assert "ssl_keyfile" in kwargs, (
        "uvicorn.run must receive ssl_keyfile when TLS paths are set"
    )
    assert kwargs["ssl_certfile"] == str(cert_file)
    assert kwargs["ssl_keyfile"] == str(key_file)


def test_serve_no_ssl_when_tls_paths_empty(tmp_path, monkeypatch):
    """serve() with no TLS paths (default) must NOT pass ssl_certfile/ssl_keyfile to uvicorn."""
    import muxplex.cli as cli_mod

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    uvicorn_calls = []

    def fake_run(*args, **kwargs):
        uvicorn_calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            cli_mod.serve()  # Default: tls_cert=None, tls_key=None

    assert len(uvicorn_calls) == 1
    kwargs = uvicorn_calls[0]
    assert "ssl_certfile" not in kwargs, (
        "uvicorn.run must NOT receive ssl_certfile when no TLS"
    )
    assert "ssl_keyfile" not in kwargs, (
        "uvicorn.run must NOT receive ssl_keyfile when no TLS"
    )


def test_serve_falls_back_to_http_when_cert_file_missing(tmp_path, monkeypatch, capsys):
    """serve() prints a warning and skips SSL when tls_cert/tls_key paths don't exist on disk."""
    import muxplex.cli as cli_mod

    # Paths are set but the files do NOT exist
    cert_file = tmp_path / "nonexistent.crt"
    key_file = tmp_path / "nonexistent.key"

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    uvicorn_calls = []

    def fake_run(*args, **kwargs):
        uvicorn_calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            cli_mod.serve(tls_cert=str(cert_file), tls_key=str(key_file))

    # Warning must be printed
    captured = capsys.readouterr()
    out_lower = captured.out.lower()
    assert "not found" in out_lower or "falling back" in out_lower, (
        f"Must print warning about missing TLS files, got: {captured.out!r}"
    )

    # SSL must NOT be passed to uvicorn
    assert len(uvicorn_calls) == 1
    kwargs = uvicorn_calls[0]
    assert "ssl_certfile" not in kwargs, (
        "Must not pass ssl_certfile when cert file missing"
    )
    assert "ssl_keyfile" not in kwargs, (
        "Must not pass ssl_keyfile when cert file missing"
    )


def test_serve_prints_https_url_when_tls_active(tmp_path, monkeypatch, capsys):
    """serve() must print 'https://' URL when TLS is active."""
    import muxplex.cli as cli_mod

    cert_file = tmp_path / "server.crt"
    key_file = tmp_path / "server.key"
    cert_file.write_text("fake cert")
    key_file.write_text("fake key")

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    with patch("uvicorn.run"):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            cli_mod.serve(tls_cert=str(cert_file), tls_key=str(key_file))

    captured = capsys.readouterr()
    assert "https://" in captured.out, (
        f"Must print 'https://' when TLS is active, got: {captured.out!r}"
    )


def test_serve_prints_http_url_when_no_tls(tmp_path, monkeypatch, capsys):
    """serve() must print 'http://' URL when TLS is not configured."""
    import muxplex.cli as cli_mod

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    with patch("uvicorn.run"):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            cli_mod.serve()  # No TLS

    captured = capsys.readouterr()
    assert "http://" in captured.out, (
        f"Must print 'http://' when no TLS, got: {captured.out!r}"
    )
    assert "https://" not in captured.out, (
        f"Must NOT print 'https://' when no TLS, got: {captured.out!r}"
    )


# ---------------------------------------------------------------------------
# TLS CLI flags — task-3-cli-flags
# ---------------------------------------------------------------------------


def test_main_passes_tls_cert_and_key_flags():
    """main() with --tls-cert and --tls-key must forward exact paths to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch(
            "sys.argv",
            ["muxplex", "--tls-cert", "/path/cert.pem", "--tls-key", "/path/key.pem"],
        ):
            main()
        mock_serve.assert_called_once_with(
            host=None,
            port=None,
            auth=None,
            session_ttl=None,
            tls_cert="/path/cert.pem",
            tls_key="/path/key.pem",
        )


def test_main_passes_none_for_unset_tls_flags():
    """main() with no TLS flags must call serve() with tls_cert=None and tls_key=None."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        mock_serve.assert_called_once_with(
            host=None,
            port=None,
            auth=None,
            session_ttl=None,
            tls_cert=None,
            tls_key=None,
        )


# ---------------------------------------------------------------------------
# task-5: setup-tls subcommand tests
# ---------------------------------------------------------------------------


def test_setup_tls_subcommand_registered():
    """'setup-tls' must appear in muxplex --help output."""
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
    assert "setup-tls" in help_text, (
        f"'setup-tls' must appear in --help output, got:\n{help_text}"
    )


def test_main_dispatches_to_setup_tls(monkeypatch):
    """main() with 'setup-tls' subcommand must invoke setup_tls(method='auto')."""
    import muxplex.cli as cli_mod

    calls = []
    monkeypatch.setattr(
        cli_mod, "setup_tls", lambda method="auto": calls.append(method)
    )

    with patch("sys.argv", ["muxplex", "setup-tls"]):
        cli_mod.main()

    assert len(calls) == 1, "setup_tls() must be called once for 'setup-tls' subcommand"
    assert calls[0] == "auto", (
        f"setup_tls must be called with method='auto', got {calls[0]!r}"
    )


def test_setup_tls_selfsigned_creates_certs(tmp_path, monkeypatch, capsys):
    """setup_tls(method='selfsigned') generates cert and key in config dir, updates settings,
    prints summary mentioning 'self-signed'/'selfsigned' and 'restart'."""
    import muxplex.settings as settings_mod
    from muxplex.cli import setup_tls

    # Redirect SETTINGS_PATH to tmp_path
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    setup_tls(method="selfsigned")

    # Cert and key files must exist in the config dir (SETTINGS_PATH.parent = tmp_path)
    cert_files = list(tmp_path.glob("*.crt")) + list(tmp_path.glob("*.pem"))
    key_files = list(tmp_path.glob("*.key"))
    assert cert_files, (
        f"Cert file must exist in {tmp_path}, found: {list(tmp_path.iterdir())}"
    )
    assert key_files, (
        f"Key file must exist in {tmp_path}, found: {list(tmp_path.iterdir())}"
    )

    # Settings must be updated with non-empty tls_cert and tls_key
    settings = settings_mod.load_settings()
    assert settings.get("tls_cert"), (
        "tls_cert must be non-empty in settings after setup_tls"
    )
    assert settings.get("tls_key"), (
        "tls_key must be non-empty in settings after setup_tls"
    )

    # Output must mention self-signed and restart
    captured = capsys.readouterr()
    out_lower = captured.out.lower()
    assert "self-signed" in out_lower or "selfsigned" in out_lower, (
        f"Output must mention 'self-signed' or 'selfsigned', got: {captured.out!r}"
    )
    assert "restart" in out_lower, (
        f"Output must mention 'restart', got: {captured.out!r}"
    )


def test_serve_subcommand_accepts_tls_flags():
    """'muxplex serve --tls-cert ... --tls-key ...' must forward both paths to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch(
            "sys.argv",
            [
                "muxplex",
                "serve",
                "--tls-cert",
                "/path/cert.pem",
                "--tls-key",
                "/path/key.pem",
            ],
        ):
            main()
        mock_serve.assert_called_once_with(
            host=None,
            port=None,
            auth=None,
            session_ttl=None,
            tls_cert="/path/cert.pem",
            tls_key="/path/key.pem",
        )


# ---------------------------------------------------------------------------
# task-6-doctor-tls: TLS status section in doctor()
# ---------------------------------------------------------------------------


def test_doctor_shows_tls_disabled(tmp_path, monkeypatch, capsys):
    """doctor() shows TLS disabled when no TLS configured."""
    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    # No tls_cert/tls_key — just use empty settings
    settings_file.write_text("{}")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "tls" in out_lower, f"Expected 'tls' in doctor output, got: {out!r}"
    assert "disabled" in out_lower, (
        f"Expected 'disabled' in doctor output, got: {out!r}"
    )


def test_doctor_shows_tls_enabled(tmp_path, monkeypatch, capsys):
    """doctor() shows TLS enabled when valid certs are configured."""
    import json

    import muxplex.settings as settings_mod
    from muxplex.tls import generate_self_signed

    # Generate real self-signed certs in tmp_path
    cert_path = tmp_path / "muxplex.crt"
    key_path = tmp_path / "muxplex.key"
    generate_self_signed(cert_path, key_path)

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"tls_cert": str(cert_path), "tls_key": str(key_path)})
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "tls" in out_lower, f"Expected 'tls' in doctor output, got: {out!r}"
    assert "enabled" in out_lower, f"Expected 'enabled' in doctor output, got: {out!r}"


def test_doctor_shows_tls_clipboard_warning(tmp_path, monkeypatch, capsys):
    """doctor() mentions clipboard or https when TLS is disabled."""
    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "clipboard" in out_lower or "https" in out_lower, (
        f"Expected 'clipboard' or 'https' in doctor TLS-disabled output, got: {out!r}"
    )


# ---------------------------------------------------------------------------
# task-7: Edge case tests for serve() TLS behavior
# ---------------------------------------------------------------------------


def test_serve_no_ssl_when_only_cert_set(tmp_path, monkeypatch, capsys):
    """serve() must NOT enable SSL when tls_cert is set but tls_key is empty string."""
    import muxplex.cli as cli_mod

    # Create a real cert file so tls_cert path check passes the "file exists" guard
    cert_file = tmp_path / "server.crt"
    cert_file.write_text("fake cert content")

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    uvicorn_calls = []

    def fake_run(*args, **kwargs):
        uvicorn_calls.append(kwargs)

    with patch("uvicorn.run", fake_run):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            cli_mod.serve(tls_cert=str(cert_file), tls_key="")

    assert len(uvicorn_calls) == 1
    kwargs = uvicorn_calls[0]
    assert "ssl_certfile" not in kwargs, (
        "serve() must NOT pass ssl_certfile to uvicorn when tls_key is empty string — "
        "SSL requires both cert and key"
    )


# ---------------------------------------------------------------------------
# task-4: Auto-detection chain tests for setup_tls()
# ---------------------------------------------------------------------------


def test_setup_tls_auto_uses_tailscale_when_available(tmp_path, monkeypatch, capsys):
    """setup_tls(method='auto') uses Tailscale when detect_tailscale() returns info."""
    from datetime import datetime, timezone

    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    ts_hostname = "myhost.tailscale.net"
    ts_info = {
        "hostname": ts_hostname,
        "ips": ["100.0.0.1"],
        "cert_domains": [ts_hostname],
    }
    fake_expires = datetime(2025, 12, 31, tzinfo=timezone.utc)
    ts_result = {
        "method": "tailscale",
        "cert_path": str(tmp_path / "muxplex.crt"),
        "key_path": str(tmp_path / "muxplex.key"),
        "hostnames": [ts_hostname],
        "expires": fake_expires,
    }

    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: ts_info)
    monkeypatch.setattr(tls_mod, "generate_tailscale", lambda cp, kp, h: ts_result)

    from muxplex.cli import setup_tls

    setup_tls(method="auto")

    out = capsys.readouterr().out
    assert "tailscale" in out.lower(), f"Expected 'tailscale' in output, got: {out!r}"


def test_setup_tls_auto_falls_to_mkcert_when_no_tailscale(
    tmp_path, monkeypatch, capsys
):
    """setup_tls(method='auto') falls back to mkcert when Tailscale not available."""
    from datetime import datetime, timezone

    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    fake_expires = datetime(2025, 12, 31, tzinfo=timezone.utc)
    mkcert_result = {
        "method": "mkcert",
        "cert_path": str(tmp_path / "muxplex.crt"),
        "key_path": str(tmp_path / "muxplex.key"),
        "hostnames": ["localhost"],
        "expires": fake_expires,
    }

    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: None)
    monkeypatch.setattr(tls_mod, "detect_mkcert", lambda: True)
    monkeypatch.setattr(
        tls_mod,
        "generate_mkcert",
        lambda cp, kp, extra_hostnames=None: mkcert_result,
    )

    from muxplex.cli import setup_tls

    setup_tls(method="auto")

    out = capsys.readouterr().out
    assert "mkcert" in out.lower(), f"Expected 'mkcert' in output, got: {out!r}"


def test_setup_tls_auto_falls_to_selfsigned_when_nothing_available(
    tmp_path, monkeypatch, capsys
):
    """setup_tls(method='auto') falls back to self-signed when nothing else is available."""
    from datetime import datetime, timezone

    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    fake_expires = datetime(2025, 12, 31, tzinfo=timezone.utc)
    selfsigned_result = {
        "method": "selfsigned",
        "cert_path": str(tmp_path / "muxplex.crt"),
        "key_path": str(tmp_path / "muxplex.key"),
        "hostnames": ["localhost"],
        "expires": fake_expires,
    }

    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: None)
    monkeypatch.setattr(tls_mod, "detect_mkcert", lambda: False)
    monkeypatch.setattr(
        tls_mod, "generate_self_signed", lambda cp, kp: selfsigned_result
    )

    from muxplex.cli import setup_tls

    setup_tls(method="auto")

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "self-signed" in out_lower or "selfsigned" in out_lower, (
        f"Expected 'self-signed' or 'selfsigned' in output, got: {out!r}"
    )


# ---------------------------------------------------------------------------
# task-5-status-display: setup-tls --status tests
# ---------------------------------------------------------------------------


def test_setup_tls_status_shows_disabled(tmp_path, monkeypatch, capsys):
    """setup_tls_status() shows 'not configured' when no TLS certs are configured."""
    import muxplex.settings as settings_mod

    # Empty settings — no tls_cert or tls_key
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import setup_tls_status

    setup_tls_status()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "not configured" in out_lower or "disabled" in out_lower, (
        f"Expected 'not configured' or 'disabled' in output, got: {out!r}"
    )


def test_setup_tls_status_shows_enabled(tmp_path, monkeypatch, capsys):
    """setup_tls_status() shows 'enabled' and 'expires' when valid certs are configured."""
    import json

    import muxplex.settings as settings_mod
    from muxplex.tls import generate_self_signed

    # Generate real self-signed certs in tmp_path
    cert_path = tmp_path / "muxplex.crt"
    key_path = tmp_path / "muxplex.key"
    generate_self_signed(cert_path, key_path)

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"tls_cert": str(cert_path), "tls_key": str(key_path)})
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import setup_tls_status

    setup_tls_status()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "enabled" in out_lower or "certificate" in out_lower, (
        f"Expected 'enabled' or 'certificate' in output, got: {out!r}"
    )
    assert "expires" in out_lower, f"Expected 'expires' in output, got: {out!r}"


def test_setup_tls_status_flag_registered():
    """setup-tls --status must be accepted by argparse."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "setup-tls", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue()
    assert "--status" in help_text, (
        f"Expected '--status' in setup-tls --help output, got:\n{help_text}"
    )


def test_main_dispatches_status_flag_to_setup_tls_status(monkeypatch):
    """main() with 'setup-tls --status' must invoke setup_tls_status(), not setup_tls()."""
    import muxplex.cli as cli_mod

    status_calls = []
    setup_calls = []
    monkeypatch.setattr(cli_mod, "setup_tls_status", lambda: status_calls.append(True))
    monkeypatch.setattr(
        cli_mod, "setup_tls", lambda method="auto": setup_calls.append(method)
    )

    with patch("sys.argv", ["muxplex", "setup-tls", "--status"]):
        cli_mod.main()

    assert len(status_calls) == 1, (
        "setup_tls_status() must be called once for 'setup-tls --status'"
    )
    assert len(setup_calls) == 0, "setup_tls() must NOT be called when --status is used"


def test_setup_tls_method_choices_expanded():
    """setup-tls --help must show 'tailscale' and 'mkcert' as method choices."""
    import io

    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "setup-tls", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue()
    assert "tailscale" in help_text, (
        f"Expected 'tailscale' in setup-tls --help output, got:\n{help_text}"
    )
    assert "mkcert" in help_text, (
        f"Expected 'mkcert' in setup-tls --help output, got:\n{help_text}"
    )


# ---------------------------------------------------------------------------
# task-6-existing-cert-regenerate-prompt: Existing cert detection & prompt
# ---------------------------------------------------------------------------


def test_setup_tls_prompts_when_certs_exist(tmp_path, monkeypatch, capsys):
    """setup_tls() prints 'already configured' and prompts when certs already exist.

    When tls_cert/tls_key are set in settings and the cert file exists,
    setup_tls() must inform the user and prompt before overwriting.
    When the user answers 'n', it must keep existing certs and return early.
    """
    import json

    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod
    from muxplex.tls import generate_self_signed

    from muxplex.cli import setup_tls

    # Generate real self-signed cert in tmp_path
    cert_path = tmp_path / "muxplex.crt"
    key_path = tmp_path / "muxplex.key"
    generate_self_signed(cert_path, key_path)

    # Write settings pointing to the generated cert
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"tls_cert": str(cert_path), "tls_key": str(key_path)})
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    # Monkeypatch input to return 'n' (user declines regeneration)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    # Monkeypatch detection functions to isolate prompt behavior
    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: None)
    monkeypatch.setattr(tls_mod, "detect_mkcert", lambda: False)

    setup_tls()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "already configured" in out_lower or "regenerate" in out_lower, (
        f"Expected 'already configured' or 'regenerate' in output, got: {out!r}"
    )
    # User said 'n' — should keep existing certs and return early
    assert "keeping" in out_lower, (
        f"Expected 'keeping' in output (user declined regeneration), got: {out!r}"
    )
    # Must NOT proceed to generate new certs (no "TLS setup complete" message)
    assert "tls setup complete" not in out_lower, (
        f"setup_tls() must return early when user says 'n', got: {out!r}"
    )


def test_setup_tls_regenerates_on_eof(tmp_path, monkeypatch, capsys):
    """setup_tls() handles EOFError from input() gracefully (non-interactive mode).

    When running in a non-interactive environment (e.g. piped stdin), input()
    raises EOFError. The function must treat this as 'n' (keep existing certs)
    and return normally without crashing.
    """
    import json

    import muxplex.settings as settings_mod
    import muxplex.tls as tls_mod
    from muxplex.tls import generate_self_signed

    from muxplex.cli import setup_tls

    # Generate real self-signed cert in tmp_path
    cert_path = tmp_path / "muxplex.crt"
    key_path = tmp_path / "muxplex.key"
    generate_self_signed(cert_path, key_path)

    # Write settings pointing to the generated cert
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"tls_cert": str(cert_path), "tls_key": str(key_path)})
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    # Monkeypatch input to raise EOFError (non-interactive environment)
    def raise_eof(prompt=""):
        raise EOFError("non-interactive stdin")

    monkeypatch.setattr("builtins.input", raise_eof)

    # Monkeypatch detection functions to isolate behavior
    monkeypatch.setattr(tls_mod, "detect_tailscale", lambda: None)
    monkeypatch.setattr(tls_mod, "detect_mkcert", lambda: False)

    # Must not crash — EOFError is caught and treated as 'n'
    setup_tls()  # No exception should propagate

    out = capsys.readouterr().out
    out_lower = out.lower()
    # EOFError → default 'n' → keep existing certs
    assert "keeping" in out_lower, (
        f"Expected 'keeping' in output after EOFError (default 'n'), got: {out!r}"
    )

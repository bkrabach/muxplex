"""Tests for muxplex/cli.py — CLI entry point."""

import stat
from pathlib import Path
from unittest.mock import patch


def test_cli_module_importable():
    """muxplex.cli must be importable."""
    from muxplex.cli import main  # noqa: F401


def test_main_calls_serve_by_default():
    """Calling main() with no args must invoke serve() with new defaults."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        mock_serve.assert_called_once_with(
            host="127.0.0.1", port=8088, auth="pam", session_ttl=604800
        )


def test_main_passes_custom_host_and_port():
    """main() with --host/--port must forward them to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--host", "192.168.1.1", "--port", "9000"]):
            main()
        mock_serve.assert_called_once_with(
            host="192.168.1.1", port=9000, auth="pam", session_ttl=604800
        )


def test_main_default_host_is_localhost():
    """Default --host must be 127.0.0.1 (not 0.0.0.0)."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        _, kwargs = mock_serve.call_args
        assert kwargs["host"] == "127.0.0.1"


def test_main_passes_auth_flag():
    """main() with --auth password must forward auth='password' to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--auth", "password"]):
            main()
        mock_serve.assert_called_once_with(
            host="127.0.0.1", port=8088, auth="password", session_ttl=604800
        )


def test_main_passes_session_ttl_flag():
    """main() with --session-ttl 3600 must forward session_ttl=3600 to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--session-ttl", "3600"]):
            main()
        mock_serve.assert_called_once_with(
            host="127.0.0.1", port=8088, auth="pam", session_ttl=3600
        )


def test_main_install_service_subcommand():
    """main() with 'install-service' must invoke install_service()."""
    from muxplex.cli import main

    with patch("muxplex.cli.install_service") as mock_install:
        with patch("sys.argv", ["muxplex", "install-service"]):
            main()
        mock_install.assert_called_once_with(system=False)


def test_main_install_service_system_flag():
    """main() with 'install-service --system' passes system=True."""
    from muxplex.cli import main

    with patch("muxplex.cli.install_service") as mock_install:
        with patch("sys.argv", ["muxplex", "install-service", "--system"]):
            main()
        mock_install.assert_called_once_with(system=True)


def test_install_service_user_mode_writes_unit_file(tmp_path, monkeypatch):
    """install_service(system=False) writes a unit file to ~/.config/systemd/user/."""
    from muxplex.cli import install_service

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    install_service(system=False)

    unit_path = fake_home / ".config" / "systemd" / "user" / "muxplex.service"
    assert unit_path.exists()
    content = unit_path.read_text()
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content
    assert "muxplex" in content
    assert "default.target" in content


def test_install_service_system_mode_target(tmp_path, monkeypatch):
    """install_service(system=True) targets multi-user.target in the unit file."""
    from muxplex.cli import install_service

    # Redirect the system path to tmp so we don't write to /etc
    unit_path = tmp_path / "muxplex.service"
    monkeypatch.setattr("muxplex.cli._system_service_path", unit_path)

    install_service(system=True)

    assert unit_path.exists()
    content = unit_path.read_text()
    assert "multi-user.target" in content


def test_install_service_strips_wsl_mnt_paths_from_environment(tmp_path, monkeypatch):
    """Fix 3: install_service() must strip /mnt/ paths from Environment=PATH.

    WSL mounts Windows at /mnt/c/, /mnt/d/ etc.  Paths like
    '/mnt/c/Program Files/dotnet/' contain spaces, causing systemd to
    truncate and reject the Environment= line.
    """
    from muxplex.cli import install_service

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    wsl_path = (
        "/usr/local/bin:/usr/bin:/bin:/mnt/c/Program Files/dotnet:/mnt/d/tools/bin"
    )
    monkeypatch.setenv("PATH", wsl_path)

    install_service(system=False)

    unit_path = fake_home / ".config" / "systemd" / "user" / "muxplex.service"
    content = unit_path.read_text()

    # Find the Environment=PATH line
    env_line = next(
        (line for line in content.splitlines() if line.startswith("Environment=PATH=")),
        None,
    )
    assert env_line is not None, "Environment=PATH line must be present"
    assert "/mnt/" not in env_line, (
        f"WSL /mnt/ paths must be stripped from Environment=PATH; got: {env_line!r}"
    )
    # Safe paths must still be present
    assert "/usr/local/bin" in env_line
    assert "/usr/bin" in env_line


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


def test_install_service_writes_launchd_plist_on_macos(tmp_path, monkeypatch):
    """install_service() on macOS writes a launchd plist to ~/Library/LaunchAgents/."""
    from muxplex.cli import install_service

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr("sys.platform", "darwin")

    install_service(system=False)

    plist_path = fake_home / "Library" / "LaunchAgents" / "com.muxplex.plist"
    assert plist_path.exists(), "Plist file must be created on macOS"
    content = plist_path.read_text()
    assert "com.muxplex" in content
    assert "RunAtLoad" in content
    assert "ProgramArguments" in content
    assert "LaunchAgents" in str(plist_path)


def test_install_service_does_not_write_systemd_on_macos(tmp_path, monkeypatch):
    """install_service() on macOS must NOT write a systemd unit file."""
    from muxplex.cli import install_service

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr("sys.platform", "darwin")

    install_service(system=False)

    systemd_path = fake_home / ".config" / "systemd" / "user" / "muxplex.service"
    assert not systemd_path.exists(), "No systemd unit file should be written on macOS"


def test_install_service_writes_systemd_on_linux(tmp_path, monkeypatch):
    """install_service() on Linux writes a systemd unit to ~/.config/systemd/user/."""
    from muxplex.cli import install_service

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr("sys.platform", "linux")

    install_service(system=False)

    unit_path = fake_home / ".config" / "systemd" / "user" / "muxplex.service"
    assert unit_path.exists(), "Systemd unit file must be created on Linux"
    content = unit_path.read_text()
    assert "[Unit]" in content
    assert "[Service]" in content


def test_install_service_help_text_mentions_background_service():
    """install-service help must mention 'service', not just 'systemd'."""
    import io
    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "install-service", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    help_text = buf.getvalue().lower()
    assert "service" in help_text


def test_dunder_main_calls_main():
    """python -m muxplex must call cli.main()."""
    import importlib.util

    # Locate __main__.py without executing it (find_spec does not import)
    spec = importlib.util.find_spec("muxplex.__main__")
    assert spec is not None and spec.origin is not None

    with patch("muxplex.cli.main") as mock_main:
        exec(Path(spec.origin).read_text())  # noqa: S102
        mock_main.assert_called_once()

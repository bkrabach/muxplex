"""Tests for muxplex/cli.py — CLI entry point."""

from pathlib import Path
from unittest.mock import patch


def test_cli_module_importable():
    """muxplex.cli must be importable."""
    from muxplex.cli import main  # noqa: F401


def test_main_calls_serve_by_default():
    """Calling main() with no args must invoke serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        mock_serve.assert_called_once_with(host="0.0.0.0", port=8088)


def test_main_passes_custom_host_and_port():
    """main() with --host/--port must forward them to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--host", "127.0.0.1", "--port", "9000"]):
            main()
        mock_serve.assert_called_once_with(host="127.0.0.1", port=9000)


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


def test_dunder_main_calls_main():
    """python -m muxplex must call cli.main()."""
    import importlib.util

    # Locate __main__.py without executing it (find_spec does not import)
    spec = importlib.util.find_spec("muxplex.__main__")
    assert spec is not None and spec.origin is not None

    with patch("muxplex.cli.main") as mock_main:
        exec(Path(spec.origin).read_text())  # noqa: S102
        mock_main.assert_called_once()

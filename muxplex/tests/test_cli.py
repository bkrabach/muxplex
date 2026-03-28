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


def test_dunder_main_calls_main():
    """python -m muxplex must call cli.main()."""
    with patch("muxplex.cli.main") as mock_main:
        # Simulate `python -m muxplex` by exec'ing __main__.py
        import muxplex.__main__  # noqa: F401

        # The import itself calls main() at module level
        # Re-exec to test:
        mock_main.reset_mock()
        exec(Path("muxplex/__main__.py").read_text())
        mock_main.assert_called_once()

"""Tests for muxplex/cli.py — CLI entry point."""

import json
import os
import shutil
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_check_deps(monkeypatch):
    """No-op _check_dependencies so tests that call main() for serve work without ttyd installed.

    ttyd is not available in standard Ubuntu repos (used by GitHub Actions CI runners).
    Tests that exercise the serve path of main() should use this fixture to avoid
    SystemExit(1) when ttyd is absent from the test environment.
    """
    monkeypatch.setattr("muxplex.cli._check_dependencies", lambda: None)


def test_cli_module_importable():
    """muxplex.cli must be importable."""
    from muxplex.cli import main  # noqa: F401


def test_main_calls_serve_by_default(mock_check_deps):
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
            force_take_port=False,
        )


def test_main_passes_custom_host_and_port(mock_check_deps):
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
            force_take_port=False,
        )


def test_main_default_host_is_localhost(mock_check_deps):
    """Default --host must be None (settings layer resolves to 127.0.0.1)."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        _, kwargs = mock_serve.call_args
        assert kwargs["host"] is None


def test_main_passes_auth_flag(mock_check_deps):
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
            force_take_port=False,
        )


def test_main_passes_session_ttl_flag(mock_check_deps):
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
            force_take_port=False,
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
# `muxplex env` subcommand
# ---------------------------------------------------------------------------


def test_main_dispatches_to_env(monkeypatch):
    """main() with 'env' subcommand must invoke cmd_env()."""
    from muxplex.cli import main

    calls = []
    monkeypatch.setattr("muxplex.cli.cmd_env", lambda: calls.append(True))

    with patch("sys.argv", ["muxplex", "env"]):
        main()

    assert len(calls) == 1, (
        "cmd_env() must be called once when 'env' subcommand is used"
    )


def test_cmd_env_prints_only_the_export_line_on_stdout(tmp_path, monkeypatch, capsys):
    """cmd_env() prints exactly the export line on stdout -- nothing else (eval-safety)."""
    import muxplex.settings as settings_mod
    from muxplex.cli import cmd_env

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setenv("TMUX_TMPDIR", "/configured/via/env")

    cmd_env()

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines == ['export TMUX_TMPDIR="/configured/via/env"']
    # Human-facing notes go to stderr, not stdout.
    assert captured.out.count("\n") == 1


def test_cmd_env_uses_configured_tmux_socket_dir(tmp_path, monkeypatch, capsys):
    """cmd_env() prefers the explicit tmux_socket_dir setting over the environment."""
    import json

    import muxplex.settings as settings_mod
    from muxplex.cli import cmd_env

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_path.write_text(json.dumps({"tmux_socket_dir": "/configured/socket/dir"}))
    monkeypatch.setenv("TMUX_TMPDIR", "/should/be/ignored")

    cmd_env()

    captured = capsys.readouterr()
    assert captured.out == 'export TMUX_TMPDIR="/configured/socket/dir"\n'


def test_cmd_env_falls_back_to_tmux_default_when_nothing_configured(
    tmp_path, monkeypatch, capsys
):
    """cmd_env() never prints an empty TMUX_TMPDIR -- falls back to tmux's own default."""
    import muxplex.settings as settings_mod
    from muxplex.cli import cmd_env

    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)

    cmd_env()

    captured = capsys.readouterr()
    assert captured.out.startswith('export TMUX_TMPDIR="/tmp/tmux-')
    assert captured.out.strip() != 'export TMUX_TMPDIR=""'


# ---------------------------------------------------------------------------
# cmd_restore tests -- the record-only slice's read-only dry-run view
# ---------------------------------------------------------------------------


def test_cmd_restore_without_dry_run_refuses_and_exits_nonzero(capsys):
    """cmd_restore(dry_run=False) must refuse -- restore EXECUTION is not
    implemented in the record-only milestone. --dry-run is required, not one
    of two behaviors, so a future real restore does not silently change what
    a bare invocation does today."""
    from muxplex.cli import cmd_restore

    with pytest.raises(SystemExit) as exc_info:
        cmd_restore(dry_run=False)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not implemented" in captured.err
    assert "--dry-run" in captured.err


def test_cmd_restore_dry_run_no_pending_restore(tmp_path, monkeypatch, capsys):
    """cmd_restore(dry_run=True) with no pending_restore prints a clear
    'nothing to restore' message and creates/kills nothing."""
    import muxplex.manifest as manifest_mod
    from muxplex.cli import cmd_restore

    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", tmp_path / "sessions.json")

    cmd_restore(dry_run=True)

    captured = capsys.readouterr()
    assert "No cold start detected" in captured.out
    assert "Nothing to restore" in captured.out


def test_cmd_restore_dry_run_shows_pending_sessions(tmp_path, monkeypatch, capsys):
    """cmd_restore(dry_run=True) with a populated pending_restore prints the
    lost server's pid, the session count, and every session name -- and
    explicitly states that nothing was created or restored."""
    import muxplex.manifest as manifest_mod
    from muxplex.cli import cmd_restore

    manifest_path = tmp_path / "sessions.json"
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)

    manifest = {
        "schema": 1,
        "epoch": {"socket_path": "/home/user/.tmux/tmux-1000/default", "server_pid": 42, "inode": 7},
        "sessions": {},
        "pending_restore": {
            "detected_at": 1785378123.0,
            "lost_epoch": {
                "socket_path": "/home/user/.tmux/tmux-1000/default",
                "server_pid": 1519962,
                "inode": 5,
            },
            "sessions": {
                "a2a": {"first_seen_at": 1785372115.0, "last_seen_at": 1785378000.0},
                "bbs": {"first_seen_at": 1785372200.0, "last_seen_at": 1785378000.0},
            },
        },
    }
    manifest_mod.save_manifest(manifest)

    cmd_restore(dry_run=True)

    captured = capsys.readouterr()
    assert "Cold start detected" in captured.out
    assert "1519962" in captured.out
    assert "2 session(s)" in captured.out
    assert "a2a" in captured.out
    assert "bbs" in captured.out
    assert "[DRY RUN]" in captured.out
    assert "No sessions were created, killed, or restored" in captured.out


def test_restore_subcommand_wired_to_cmd_restore(monkeypatch):
    """`muxplex restore --dry-run` on the CLI must call cmd_restore(dry_run=True)."""
    import muxplex.cli as cli_mod

    called = {}
    monkeypatch.setattr(
        cli_mod, "cmd_restore", lambda dry_run: called.update(dry_run=dry_run)
    )
    monkeypatch.setattr("sys.argv", ["muxplex", "restore", "--dry-run"])

    cli_mod.main()

    assert called == {"dry_run": True}


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


def test_main_passes_none_for_unset_flags(mock_check_deps):
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
            force_take_port=False,
        )


def test_main_passes_explicit_host_only(mock_check_deps):
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
            force_take_port=False,
        )


def test_main_serve_subcommand_accepts_flags(mock_check_deps):
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
            force_take_port=False,
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
# doctor(): running vs installed version
# ---------------------------------------------------------------------------


def test_doctor_shows_running_version_match(tmp_path, monkeypatch, capsys):
    """doctor() must report the running version matches installed when they're equal."""
    import json
    from importlib.metadata import version as pkg_version

    import muxplex.cli as cli_mod
    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"host": "127.0.0.1", "port": 8088}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    installed_version = pkg_version("muxplex")
    monkeypatch.setattr(
        cli_mod,
        "_fetch_local_instance_info",
        lambda port, timeout=2.0: {"device_id": "abc", "version": installed_version},
    )

    cli_mod.doctor()

    out = capsys.readouterr().out
    assert "matches installed" in out


def test_doctor_shows_running_version_mismatch(tmp_path, monkeypatch, capsys):
    """doctor() must warn and point at a restart when running != installed version.

    This is the exact gap that left a live server on v0.14.0 for hours after
    the install moved to v0.15.0, with nothing anywhere saying so.
    """
    import json

    import muxplex.cli as cli_mod
    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"host": "127.0.0.1", "port": 8088}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    monkeypatch.setattr(
        cli_mod,
        "_fetch_local_instance_info",
        lambda port, timeout=2.0: {"device_id": "abc", "version": "0.0.1-stale"},
    )

    cli_mod.doctor()

    out = capsys.readouterr().out
    assert "0.0.1-stale" in out
    assert "restart the service" in out
    assert "muxplex upgrade" in out


def test_doctor_shows_running_not_serving_distinctly(tmp_path, monkeypatch, capsys):
    """doctor() must report 'not serving' plainly -- a normal state, not an error --
    and that message must never be confused with the version-mismatch wording."""
    import json

    import muxplex.cli as cli_mod
    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"host": "127.0.0.1", "port": 8088}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    monkeypatch.setattr(
        cli_mod, "_fetch_local_instance_info", lambda port, timeout=2.0: None
    )

    cli_mod.doctor()

    out = capsys.readouterr().out
    assert "not serving" in out
    assert "restart the service" not in out


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


@pytest.mark.allow_real_port_killer
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


@pytest.mark.allow_real_port_killer
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
    # Deterministic: never probe the real network. Without this the test would
    # hit 127.0.0.1:8088 and flip its outcome on a machine actually running muxplex.
    monkeypatch.setattr(
        cli_mod, "_port_holder_is_healthy_muxplex", lambda *a, **k: False
    )

    # Patch time.sleep so test doesn't actually sleep
    import time

    monkeypatch.setattr(time, "sleep", lambda _: None)

    cli_mod._kill_stale_port_holder(8088)

    assert (foreign_pid, signal.SIGTERM) in killed, (
        f"Expected SIGTERM sent to foreign PID {foreign_pid}, got: {killed}"
    )


@pytest.mark.allow_real_port_killer
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


@pytest.mark.allow_real_port_killer
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
        cli_mod,
        "_kill_stale_port_holder",
        lambda port, force=False: killed_ports.append(port),
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


def test_main_passes_tls_cert_and_key_flags(mock_check_deps):
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
            force_take_port=False,
        )


def test_main_passes_none_for_unset_tls_flags(mock_check_deps):
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
            force_take_port=False,
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


def test_serve_subcommand_accepts_tls_flags(mock_check_deps):
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
            force_take_port=False,
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
    """doctor() mentions clipboard or https when TLS is disabled on network host."""
    import json

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    # Set host to network to trigger the TLS warning (not localhost)
    settings_file.write_text(json.dumps({"host": "0.0.0.0"}))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "clipboard" in out_lower or "https" in out_lower, (
        f"Expected 'clipboard' or 'https' in doctor TLS-disabled output for network host, got: {out!r}"
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


# ---------------------------------------------------------------------------
# task: TLS nudge hints in doctor and service install
# ---------------------------------------------------------------------------


def test_doctor_tls_nudge_shows_run_command_on_network_host(
    capsys, tmp_path, monkeypatch
):
    """doctor must show 'Run: muxplex setup-tls' when host is network and TLS disabled."""
    import json

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"host": "0.0.0.0", "tls_cert": "", "tls_key": ""})
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    assert "muxplex setup-tls" in out, (
        f"Expected 'muxplex setup-tls' in doctor output when host is 0.0.0.0 and TLS disabled, got: {out!r}"
    )


def test_doctor_tls_nudge_hidden_on_localhost(capsys, tmp_path, monkeypatch):
    """doctor must NOT show TLS nudge when host is 127.0.0.1."""
    import json

    import muxplex.settings as settings_mod

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"host": "127.0.0.1", "tls_cert": "", "tls_key": ""})
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    assert "muxplex setup-tls" not in out, (
        f"TLS nudge must NOT appear in doctor output when host is 127.0.0.1, got: {out!r}"
    )


# ---------------------------------------------------------------------------
# task-1-pypi-metadata: pyproject.toml metadata tests
# ---------------------------------------------------------------------------


def test_pyproject_has_authors():
    """pyproject.toml must declare at least one author with name and email."""
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    authors = data["project"].get("authors", [])
    assert len(authors) >= 1
    assert "name" in authors[0]
    assert "email" in authors[0]


def test_pyproject_has_classifiers():
    """pyproject.toml must declare at least 3 classifiers including License and Python version."""
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    classifiers = data["project"].get("classifiers", [])
    assert len(classifiers) >= 3
    texts = " ".join(classifiers)
    assert "License" in texts
    assert "Python :: 3" in texts


def test_pyproject_has_keywords():
    """pyproject.toml must declare at least 3 keywords for PyPI discoverability."""
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    keywords = data["project"].get("keywords", [])
    assert len(keywords) >= 3


# ---------------------------------------------------------------------------
# task-4-upgrade-routing: upgrade routes based on install source
# ---------------------------------------------------------------------------


def test_upgrade_pypi_install_uses_package_name(monkeypatch, capsys):
    """upgrade() for PyPI installs must use 'muxplex' not git+https URL."""
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
        "_get_install_info",
        lambda: {"source": "pypi", "version": "0.1.0", "commit": None, "url": None},
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (v0.1.0 → v0.2.0)"),
    )
    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()
    uv_calls = [
        c for c in calls if isinstance(c, list) and "tool" in c and "install" in c
    ]
    assert len(uv_calls) > 0
    install_cmd = uv_calls[0]
    assert "muxplex" in install_cmd
    assert not any("git+" in str(arg) for arg in install_cmd)


# ---------------------------------------------------------------------------
# task-6-reset-device-id: --reset-device-id CLI command tests
# ---------------------------------------------------------------------------


def test_reset_device_id_writes_new_id(tmp_path, monkeypatch, capsys):
    """reset_device_id_command() writes a new device_id different from the previous one.

    Monkeypatches the identity path, creates an initial identity, calls the
    command, and verifies that:
    - The file now has a different device_id
    - Output mentions 'device_id' or 'identity'
    - Output includes a warning or mentions 'orphan'
    """
    import json

    import muxplex.identity as identity_mod
    from muxplex.cli import reset_device_id_command

    # Set up fake identity path
    identity_path = tmp_path / "identity.json"
    monkeypatch.setattr(identity_mod, "IDENTITY_PATH", identity_path)

    # Create an initial identity
    original_id = "11111111-1111-4111-a111-111111111111"
    identity_path.write_text(json.dumps({"device_id": original_id}))

    # Run the command
    reset_device_id_command()

    # Verify new ID was written and is different
    data = json.loads(identity_path.read_text())
    new_id = data["device_id"]
    assert new_id != original_id, (
        f"reset_device_id_command() must write a new different device_id, "
        f"got: {new_id!r} (same as original)"
    )

    # Verify output mentions device_id/identity
    captured = capsys.readouterr()
    output_lower = captured.out.lower()
    assert "device_id" in output_lower or "identity" in output_lower, (
        f"Output must mention 'device_id' or 'identity', got: {captured.out!r}"
    )

    # Verify output includes warning about orphaned session keys
    assert "orphan" in output_lower or "warning" in output_lower, (
        f"Output must include warning about orphaned session keys, got: {captured.out!r}"
    )


def test_main_dispatches_to_reset_device_id(monkeypatch):
    """main() with 'reset-device-id' subcommand must invoke reset_device_id_command()."""
    import muxplex.cli as cli_mod

    calls = []
    monkeypatch.setattr(cli_mod, "reset_device_id_command", lambda: calls.append(True))

    with patch("sys.argv", ["muxplex", "reset-device-id"]):
        cli_mod.main()

    assert len(calls) == 1, (
        "reset_device_id_command() must be called once for 'reset-device-id' subcommand"
    )


def test_upgrade_git_install_uses_git_url(monkeypatch, capsys):
    """upgrade() for git installs must still use git+https URL."""
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
        "_get_install_info",
        lambda: {
            "source": "git",
            "version": "0.1.0",
            "commit": "abc12345",
            "url": "https://github.com/bkrabach/muxplex",
        },
    )
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (abc12345 → def67890)"),
    )
    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()
    uv_calls = [
        c for c in calls if isinstance(c, list) and "tool" in c and "install" in c
    ]
    assert len(uv_calls) > 0
    install_cmd = uv_calls[0]
    assert any("git+" in str(arg) for arg in install_cmd)


# ---------------------------------------------------------------------------
# v0.6.1 bug-fix: tolerate systems without systemctl
# ---------------------------------------------------------------------------


def test_have_systemctl_helper_exists():
    """_have_systemctl must be importable from muxplex.cli."""
    from muxplex.cli import _have_systemctl  # noqa: F401


def test_have_systemctl_returns_bool(monkeypatch):
    """_have_systemctl() must return True when systemctl is on PATH, False otherwise."""
    from muxplex.cli import _have_systemctl

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    assert _have_systemctl() is True

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _have_systemctl() is False


def test_upgrade_no_systemctl_runs_to_completion(monkeypatch, capsys):
    """upgrade() must complete without raising when systemctl is not on PATH.

    Regression test for FileNotFoundError on Unraid / BSD / macOS-container hosts.
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    subprocess_calls = []

    def mock_run(cmd, **kwargs):
        subprocess_calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    # systemctl absent; uv and pgrep present
    def fake_which_no_systemctl(name):
        if name == "systemctl":
            return None
        return f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which_no_systemctl)
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (v0.6.0 → v0.6.1)"),
    )
    # Ensure we exercise the Linux (non-darwin) path
    monkeypatch.setattr(sys, "platform", "linux")

    # Must NOT raise FileNotFoundError (the original bug)
    cli_mod.upgrade()

    # systemctl must never have been called
    systemctl_calls = [
        c for c in subprocess_calls if isinstance(c, list) and "systemctl" in c
    ]
    assert len(systemctl_calls) == 0, (
        f"upgrade() must not call systemctl when _have_systemctl() is False; "
        f"got: {systemctl_calls}"
    )


def test_upgrade_no_systemctl_prints_skip_note(monkeypatch, capsys):
    """upgrade() must print a helpful note when systemctl is missing."""
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    def mock_run(cmd, **kwargs):
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_which_no_systemctl(name):
        if name == "systemctl":
            return None
        return f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which_no_systemctl)
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (v0.6.0 → v0.6.1)"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    cli_mod.upgrade()

    out = capsys.readouterr().out
    out_lower = out.lower()
    # Must mention that systemctl was not found / skipped at least once
    assert "systemctl" in out_lower, (
        f"upgrade() must print a note mentioning 'systemctl' when it is absent; got: {out!r}"
    )
    assert (
        "not found" in out_lower
        or "skipping" in out_lower
        or "not detected" in out_lower
    ), f"upgrade() must indicate the step was skipped; got: {out!r}"


def test_upgrade_no_systemctl_prints_manual_restart_note(monkeypatch, capsys):
    """upgrade() must tell the user to restart muxplex manually when systemd is absent."""
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    def mock_run(cmd, **kwargs):
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_which_no_systemctl(name):
        if name == "systemctl":
            return None
        return f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which_no_systemctl)
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (v0.6.0 → v0.6.1)"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    cli_mod.upgrade()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "restart" in out_lower or "manually" in out_lower, (
        f"upgrade() must advise manual restart when systemd is absent; got: {out!r}"
    )


def test_upgrade_with_systemctl_runs_systemd_commands(monkeypatch, capsys):
    """upgrade() must call systemctl when it IS available (full Linux systemd path)."""
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    subprocess_calls = []

    def mock_run(cmd, **kwargs):
        subprocess_calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (v0.6.0 → v0.6.1)"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()

    systemctl_calls = [
        c for c in subprocess_calls if isinstance(c, list) and "systemctl" in c
    ]
    assert len(systemctl_calls) > 0, (
        "upgrade() must invoke systemctl commands when systemctl is available"
    )
    # Verify the is-active check was performed
    is_active_calls = [c for c in systemctl_calls if "is-active" in c]
    assert len(is_active_calls) > 0, (
        "upgrade() must check is-active when systemctl is available"
    )


def test_doctor_no_systemctl_shows_graceful_message(monkeypatch, capsys):
    """doctor() must show 'systemd not available' when systemctl is not on PATH."""
    original_which = shutil.which

    def fake_which_no_systemctl(name):
        if name == "systemctl":
            return None
        return original_which(name)

    monkeypatch.setattr(shutil, "which", fake_which_no_systemctl)

    from muxplex.cli import doctor

    doctor()

    out = capsys.readouterr().out
    out_lower = out.lower()
    assert "systemd" in out_lower, (
        f"doctor() must mention 'systemd' in Service line when systemctl is absent; got: {out!r}"
    )
    assert "not available" in out_lower or "unavailable" in out_lower, (
        f"doctor() must say systemd is 'not available' when systemctl is absent; got: {out!r}"
    )


def test_doctor_no_systemctl_does_not_crash(monkeypatch, capsys):
    """doctor() must not raise FileNotFoundError or any exception when systemctl is absent."""
    original_which = shutil.which

    def fake_which_no_systemctl(name):
        if name == "systemctl":
            return None
        return original_which(name)

    monkeypatch.setattr(shutil, "which", fake_which_no_systemctl)

    from muxplex.cli import doctor

    # Must not raise — the original bug was FileNotFoundError on systems without systemctl
    doctor()


# ---------------------------------------------------------------------------
# v0.6.2 fixes: install failure propagation, service-restart-on-failure,
#               prefer uv over pip for uv-tool-managed installs
# ---------------------------------------------------------------------------


def test_have_launchctl_helper_exists():
    """_have_launchctl must be importable from muxplex.cli."""
    from muxplex.cli import _have_launchctl  # noqa: F401


def test_have_launchctl_returns_true_when_present(monkeypatch):
    """_have_launchctl() returns True when launchctl is on PATH."""
    import shutil as _shutil

    from muxplex.cli import _have_launchctl

    monkeypatch.setattr(
        _shutil,
        "which",
        lambda name: "/usr/bin/launchctl" if name == "launchctl" else None,
    )
    assert _have_launchctl() is True


def test_have_launchctl_returns_false_when_absent(monkeypatch):
    """_have_launchctl() returns False when launchctl is absent."""
    import shutil as _shutil

    from muxplex.cli import _have_launchctl

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    assert _have_launchctl() is False


def test_upgrade_propagates_install_failure_as_exit1(monkeypatch, capsys):
    """upgrade() must sys.exit(1) when the install subprocess returns non-zero.

    Regression test for Bug 1 (v0.6.2): previously upgrade() silently returned
    exit 0 even when pip/uv failed mid-flight.
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    calls = []

    def mock_run(cmd, **kwargs):
        calls.append(cmd)
        # Fail only on the install command (uv or pip)
        if (
            isinstance(cmd, list)
            and cmd
            and ("uv" in str(cmd[0]) or "pip" in str(cmd[0]))
        ):
            return type(
                "R", (), {"returncode": 1, "stdout": "", "stderr": "install error"}
            )()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (v0.6.1 \u2192 v0.6.2)"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.upgrade()

    assert exc_info.value.code == 1, (
        f"upgrade() must exit(1) on install failure, got code {exc_info.value.code}"
    )
    out = capsys.readouterr().out
    assert "error" in out.lower() or "fail" in out.lower(), (
        f"upgrade() must print an error message on install failure; got: {out!r}"
    )


def test_upgrade_restarts_systemctl_after_failed_install(monkeypatch, capsys):
    """upgrade() must attempt to restart the systemctl service even when install fails.

    Regression test for Bug 2 (v0.6.2) — systemctl path: the start step must
    run in the finally block regardless of install outcome.
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    calls: list = []

    def mock_run(cmd, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, list) else cmd)
        # Fail the install command
        if isinstance(cmd, list) and cmd and "uv" in str(cmd[0]):
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "uv fail"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(SystemExit):
        cli_mod.upgrade()

    # systemctl start must still have been called after the failed install
    start_calls = [
        c for c in calls if isinstance(c, list) and "systemctl" in c and "start" in c
    ]
    assert len(start_calls) > 0, (
        "upgrade() must call systemctl start after a failed install (try/finally)"
    )

    # Verify call ordering: install attempt precedes start
    install_idx = next(
        (
            i
            for i, c in enumerate(calls)
            if isinstance(c, list) and c and "uv" in str(c[0])
        ),
        -1,
    )
    start_idx = next(
        (
            i
            for i, c in enumerate(calls)
            if isinstance(c, list) and "systemctl" in c and "start" in c
        ),
        -1,
    )
    assert install_idx >= 0, "install command must have been attempted"
    assert start_idx > install_idx, (
        "systemctl start must be called AFTER the failed install attempt"
    )


def test_upgrade_restarts_launchctl_after_failed_install(monkeypatch, capsys, tmp_path):
    """upgrade() must attempt to re-load the launchd agent even when install fails (macOS).

    Regression test for Bug 2 (v0.6.2) — launchctl path: the bootstrap step
    must run in the finally block regardless of install outcome.
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    # Set up a fake macOS home with the launchd plist present
    fake_home = tmp_path / "home"
    plist_dir = fake_home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    (plist_dir / "com.muxplex.plist").write_text("")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(cli_mod, "_have_launchctl", lambda: True)

    calls: list = []

    def mock_run(cmd, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, list) else cmd)
        if isinstance(cmd, list) and cmd and "uv" in str(cmd[0]):
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "uv fail"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available"),
    )

    with pytest.raises(SystemExit):
        cli_mod.upgrade()

    # launchctl bootstrap (or legacy load) must have been called after the failed install
    restart_calls = [
        c
        for c in calls
        if isinstance(c, list)
        and "launchctl" in c
        and ("bootstrap" in c or "load" in c)
    ]
    assert len(restart_calls) > 0, (
        "upgrade() must call launchctl bootstrap/load after a failed install (try/finally)"
    )

    # Verify ordering: install attempt before restart
    install_idx = next(
        (
            i
            for i, c in enumerate(calls)
            if isinstance(c, list) and c and "uv" in str(c[0])
        ),
        -1,
    )
    restart_idx = next(
        (
            i
            for i, c in enumerate(calls)
            if isinstance(c, list)
            and "launchctl" in c
            and ("bootstrap" in c or "load" in c)
        ),
        -1,
    )
    assert install_idx >= 0, "install command must have been attempted"
    assert restart_idx > install_idx, (
        "launchctl bootstrap/load must be called AFTER the failed install attempt"
    )


def test_upgrade_no_launchctl_on_linux_uses_systemctl(monkeypatch, capsys):
    """On Linux without launchctl, upgrade() uses systemctl and never calls launchctl.

    Simulates a Linux host where launchctl is not installed.  The upgrade must
    complete normally (no SystemExit) using the systemctl path.
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    subprocess_calls: list = []

    def mock_run(cmd, **kwargs):
        subprocess_calls.append(list(cmd) if isinstance(cmd, list) else cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_which(name):
        if name == "launchctl":
            return None  # launchctl absent on Linux
        return f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()  # must not raise

    launchctl_calls = [
        c for c in subprocess_calls if isinstance(c, list) and "launchctl" in c
    ]
    assert len(launchctl_calls) == 0, (
        f"upgrade() must not call launchctl on Linux; got: {launchctl_calls}"
    )
    systemctl_calls = [
        c for c in subprocess_calls if isinstance(c, list) and "systemctl" in c
    ]
    assert len(systemctl_calls) > 0, (
        "upgrade() must call systemctl on Linux when available"
    )


def test_upgrade_prefers_uv_tool_when_uv_managed(monkeypatch, capsys):
    """upgrade() calls 'uv tool install --reinstall --force muxplex' for uv-tool installs.

    Regression test for Bug 3 (v0.6.2): when the running muxplex binary
    resolves to inside the uv tools directory, the upgrade must use the uv
    tool reinstall path (not pip).
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    # Build a fake muxplex path that looks like a uv-tool-managed install.
    # Use Path.home() so _uv_tools_dir computation matches inside upgrade().
    uv_tools_fake = str(Path.home() / ".local" / "share" / "uv" / "tools")
    fake_muxplex_resolved = f"{uv_tools_fake}/muxplex/bin/muxplex"
    fake_uv_path = str(Path.home() / ".local" / "bin" / "uv")

    calls: list = []

    def mock_run(cmd, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, list) else cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_which(name):
        if name == "uv":
            return fake_uv_path
        if name == "muxplex":
            # Return the already-resolved path under the uv tools dir so that
            # Path(fake_muxplex_resolved).resolve() == fake_muxplex_resolved
            return fake_muxplex_resolved
        return f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available (v0.6.1 \u2192 v0.6.2)"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()

    uv_reinstall_calls = [
        c
        for c in calls
        if isinstance(c, list) and "tool" in c and "install" in c and "--reinstall" in c
    ]
    assert len(uv_reinstall_calls) > 0, (
        "upgrade() must call 'uv tool install --reinstall' when install is uv-tool-managed"
    )
    install_cmd = uv_reinstall_calls[0]
    assert "--reinstall" in install_cmd, "command must include --reinstall"
    assert "--force" in install_cmd, "command must include --force"
    assert "muxplex" in install_cmd, "command must target 'muxplex' package"
    # Must not have fallen through to pip
    pip_calls = [c for c in calls if isinstance(c, list) and c and "pip" in str(c[0])]
    assert len(pip_calls) == 0, (
        "upgrade() must not call pip when uv is available and install is uv-managed"
    )


def test_upgrade_falls_back_to_pip_when_uv_absent(monkeypatch, capsys):
    """upgrade() uses pip install when uv is not found anywhere (_find_uv returns None).

    Regression test for Bug 3 (v0.6.2): uv absent \u2192 pip must be the installer.
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    calls: list = []

    def mock_run(cmd, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, list) else cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_which(name):
        if name in ("pip", "pip3"):
            return f"/usr/local/bin/{name}"
        return f"/usr/bin/{name}"

    # _find_uv returns None — uv absent even at known non-PATH locations
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: None)
    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()

    pip_calls = [c for c in calls if isinstance(c, list) and c and "pip" in str(c[0])]
    assert len(pip_calls) > 0, "upgrade() must call pip install when uv is absent"
    uv_calls = [c for c in calls if isinstance(c, list) and c and "uv" in str(c[0])]
    assert len(uv_calls) == 0, "upgrade() must not call uv when it is absent from PATH"


# ---------------------------------------------------------------------------
# v0.6.4 fixes: _find_uv / _find_pip path probing + exit code propagation
# ---------------------------------------------------------------------------


def test_find_uv_returns_path_from_shutil_which():
    """_find_uv() returns the path that shutil.which('uv') returns when present."""
    import muxplex.cli as cli_mod

    with patch("muxplex.cli.shutil") as mock_shutil:
        mock_shutil.which.return_value = "/usr/local/bin/uv"
        result = cli_mod._find_uv()

    assert result == "/usr/local/bin/uv", (
        "_find_uv must return the shutil.which result when uv is on PATH"
    )


def test_find_uv_probes_known_locations_when_which_returns_none(tmp_path, monkeypatch):
    """_find_uv() falls through to the candidate list when shutil.which returns None."""
    import muxplex.cli as cli_mod

    # Simulate shutil.which returning None for "uv"
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "uv" else f"/usr/bin/{name}"
    )

    # Create a fake uv binary in a location that _find_uv() probes
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\necho uv")
    fake_uv.chmod(0o755)

    # Patch _find_uv's candidate list so the temp path is probed
    import os as _os

    original_exists = _os.path.exists
    original_access = _os.access

    def fake_exists(path):
        if path == str(fake_uv):
            return True
        if path.endswith("/uv"):
            return False  # suppress all real candidates
        return original_exists(path)

    def fake_access(path, mode):
        if path == str(fake_uv):
            return True
        return original_access(path, mode)

    monkeypatch.setattr(_os.path, "exists", fake_exists)
    monkeypatch.setattr(_os, "access", fake_access)

    # Temporarily inject fake_uv as the first candidate to probe
    original_find_uv = cli_mod._find_uv

    def patched_find_uv():
        found = shutil.which("uv")
        if found:
            return found
        candidates = [str(fake_uv)]
        for path in candidates:
            if _os.path.exists(path) and _os.access(path, _os.X_OK):
                return path
        return None

    monkeypatch.setattr(cli_mod, "_find_uv", patched_find_uv)

    result = cli_mod._find_uv()
    assert result == str(fake_uv), (
        f"_find_uv must return the candidate path when shutil.which returns None; got {result!r}"
    )


def test_find_uv_returns_none_when_no_candidate_exists(monkeypatch):
    """_find_uv() returns None when neither shutil.which nor any candidate finds uv."""
    import os as _os
    import muxplex.cli as cli_mod

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(_os.path, "exists", lambda path: False)
    monkeypatch.setattr(_os, "access", lambda path, mode: False)

    result = cli_mod._find_uv()
    assert result is None, "_find_uv must return None when uv cannot be found anywhere"


def test_find_pip_returns_path_from_shutil_which():
    """_find_pip() returns the path that shutil.which('pip') returns when present."""
    import muxplex.cli as cli_mod

    with patch("muxplex.cli.shutil") as mock_shutil:
        mock_shutil.which.side_effect = lambda name: (
            "/usr/bin/pip" if name == "pip" else None
        )
        result = cli_mod._find_pip()

    assert result == "/usr/bin/pip", (
        "_find_pip must return shutil.which('pip') result when pip is on PATH"
    )


def test_find_pip_returns_pip3_when_pip_absent():
    """_find_pip() returns pip3 path when pip is absent but pip3 is on PATH."""
    import muxplex.cli as cli_mod

    with patch("muxplex.cli.shutil") as mock_shutil:
        mock_shutil.which.side_effect = lambda name: (
            "/usr/bin/pip3" if name == "pip3" else None
        )
        result = cli_mod._find_pip()

    assert result == "/usr/bin/pip3", (
        "_find_pip must return pip3 when pip is absent but pip3 is on PATH"
    )


def test_find_pip_probes_known_locations_when_which_returns_none(monkeypatch):
    """_find_pip() falls through to the candidate list when shutil.which returns None."""
    import os as _os
    import muxplex.cli as cli_mod

    monkeypatch.setattr(shutil, "which", lambda name: None)

    def patched_find_pip():
        for name in ("pip", "pip3"):
            found = shutil.which(name)
            if found:
                return found
        # Simulate exactly one candidate existing
        candidate = "/snap/bin/pip3"
        if _os.path.exists(candidate) and _os.access(candidate, _os.X_OK):
            return candidate
        return None

    monkeypatch.setattr(_os.path, "exists", lambda p: p == "/snap/bin/pip3")
    monkeypatch.setattr(_os, "access", lambda p, m: p == "/snap/bin/pip3")
    monkeypatch.setattr(cli_mod, "_find_pip", patched_find_pip)

    result = cli_mod._find_pip()
    assert result == "/snap/bin/pip3", (
        f"_find_pip must return the candidate path from known locations; got {result!r}"
    )


def test_find_pip_returns_none_when_no_candidate_exists(monkeypatch):
    """_find_pip() returns None when neither shutil.which nor any candidate finds pip."""
    import os as _os
    import muxplex.cli as cli_mod

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(_os.path, "exists", lambda path: False)
    monkeypatch.setattr(_os, "access", lambda path, mode: False)

    result = cli_mod._find_pip()
    assert result is None, (
        "_find_pip must return None when pip cannot be found anywhere"
    )


def test_upgrade_uses_find_uv_not_shutil_which(monkeypatch, capsys):
    """upgrade() calls _find_uv() to locate uv — not shutil.which('uv') directly.

    When shutil.which('uv') returns None but _find_uv() returns a path found via
    the known-locations probe (e.g. /snap/bin/uv on a snap-installed system), the
    uv branch must still be taken — pip must NOT be used.
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    calls: list = []

    def mock_run(cmd, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, list) else cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    # shutil.which returns None for 'uv' (as happens on stripped-PATH systems)
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "uv" else f"/usr/bin/{name}"
    )
    # but _find_uv() returns a path via the known-location fallback
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/snap/bin/uv")
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()

    uv_calls = [
        c for c in calls if isinstance(c, list) and c and "/snap/bin/uv" in c[0]
    ]
    assert len(uv_calls) > 0, (
        "upgrade() must invoke the uv binary found by _find_uv() even when shutil.which returns None"
    )
    pip_calls = [c for c in calls if isinstance(c, list) and c and "pip" in str(c[0])]
    assert len(pip_calls) == 0, (
        "upgrade() must NOT fall back to pip when _find_uv() returns a valid path"
    )


def test_upgrade_exits_1_after_finally_recovers_stopped_service(monkeypatch, capsys):
    """upgrade() propagates install failure as exit code 1 even after try/finally restarts service.

    Scenario: pip install fails (rc != 0) but the service restart in the finally
    block succeeds.  The user-visible behaviour must be:
      1. Error message printed.
      2. Service restarted (best-effort).
      3. Process exits with code 1 so callers / automation can detect the failure.
    """
    import subprocess
    import sys

    import muxplex.cli as cli_mod

    restart_called = []

    def mock_run(cmd, **kwargs):
        cmd_list = list(cmd) if isinstance(cmd, list) else [cmd]
        # Simulate pip install failing
        if cmd_list and "pip" in str(cmd_list[0]):
            return type(
                "R", (), {"returncode": 1, "stdout": "", "stderr": "pip install failed"}
            )()
        # Simulate all other subprocess calls succeeding (systemctl is-active, start, etc.)
        if cmd_list and any(
            k in str(cmd_list)
            for k in ("is-active", "start", "daemon-reload", "is-enabled")
        ):
            restart_called.append(cmd_list)
            return type("R", (), {"returncode": 0, "stdout": "active", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    # uv absent so we reach the pip path
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: None)
    monkeypatch.setattr(cli_mod, "_find_pip", lambda: "/usr/bin/pip")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(
        cli_mod,
        "_check_for_update",
        lambda info: (True, "update available"),
    )
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.upgrade()

    assert exc_info.value.code == 1, (
        f"upgrade() must exit with code 1 when install fails; got code {exc_info.value.code}"
    )
    out = capsys.readouterr().out
    assert "error" in out.lower() or "failed" in out.lower(), (
        f"upgrade() must print an error message when install fails; got: {out!r}"
    )


# ---------------------------------------------------------------------------
# v0.6.7 fixes — service-restart verification (Fix 1)
# ---------------------------------------------------------------------------


def test_verify_service_started_returns_true_when_active(monkeypatch):
    """_verify_service_started returns True when systemctl is-active exits 0 (active)."""
    import subprocess

    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_have_systemctl", lambda: True)
    monkeypatch.setattr(cli_mod, "_have_launchctl", lambda: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: type(
            "R", (), {"returncode": 0, "stdout": "active\n", "stderr": ""}
        )(),
    )

    assert cli_mod._verify_service_started() is True


def test_verify_service_started_returns_false_when_inactive(monkeypatch):
    """_verify_service_started returns False when systemctl is-active exits 3 (inactive)."""
    import subprocess

    import muxplex.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_have_systemctl", lambda: True)
    monkeypatch.setattr(cli_mod, "_have_launchctl", lambda: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: type(
            "R", (), {"returncode": 3, "stdout": "inactive\n", "stderr": ""}
        )(),
    )

    assert cli_mod._verify_service_started() is False


def test_upgrade_exits_1_if_service_fails_to_restart(monkeypatch, capsys):
    """upgrade() exits 1 when install succeeds but the service never becomes active."""
    import subprocess

    import muxplex.cli as cli_mod

    calls = []

    def mock_run(cmd, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, list) else cmd)
        return type("R", (), {"returncode": 0, "stdout": "enabled\n", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        cli_mod, "_check_for_update", lambda info: (True, "update available")
    )
    monkeypatch.setattr(cli_mod, "_have_systemctl", lambda: True)
    monkeypatch.setattr(cli_mod, "_have_launchctl", lambda: False)
    # Service never becomes active (simulates the spark-1 dead-service scenario)
    monkeypatch.setattr(cli_mod, "_verify_service_started", lambda timeout_s=10: False)

    with patch("muxplex.service.service_install", lambda: None):
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.upgrade()

    assert exc_info.value.code == 1, (
        f"upgrade() must exit 1 when service fails to restart; got {exc_info.value.code}"
    )
    out = capsys.readouterr().out
    assert "error" in out.lower() or "not running" in out.lower(), (
        f"upgrade() must print an error about the failed restart; got: {out!r}"
    )


def test_upgrade_calls_daemon_reload_before_start(monkeypatch, capsys):
    """upgrade() calls systemctl daemon-reload before start (stale unit-file fix)."""
    import subprocess

    import muxplex.cli as cli_mod

    calls: list = []

    def mock_run(cmd, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, list) else cmd)
        return type("R", (), {"returncode": 0, "stdout": "enabled\n", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        cli_mod, "_check_for_update", lambda info: (True, "update available")
    )
    monkeypatch.setattr(cli_mod, "_have_systemctl", lambda: True)
    monkeypatch.setattr(cli_mod, "_have_launchctl", lambda: False)
    monkeypatch.setattr(cli_mod, "_verify_service_started", lambda timeout_s=10: True)
    monkeypatch.setattr(cli_mod, "doctor", lambda: None)

    with patch("muxplex.service.service_install", lambda: None):
        cli_mod.upgrade()

    systemctl_calls = [c for c in calls if isinstance(c, list) and "systemctl" in c]
    reload_idx = next(
        (i for i, c in enumerate(systemctl_calls) if "daemon-reload" in c), None
    )
    start_idx = next(
        (i for i, c in enumerate(systemctl_calls) if "start" in c and "muxplex" in c),
        None,
    )

    assert reload_idx is not None, (
        "systemctl daemon-reload must be called during upgrade"
    )
    assert start_idx is not None, (
        "systemctl start muxplex must be called during upgrade"
    )
    assert reload_idx < start_idx, (
        "daemon-reload must be called BEFORE start to pick up the regenerated unit file"
    )


# ---------------------------------------------------------------------------
# v0.6.7 fixes — doctor launchd port-probe (Fix 2 / doctor enhancement)
# ---------------------------------------------------------------------------


def test_doctor_reports_launchd_registered_but_not_serving(
    monkeypatch, tmp_path, capsys
):
    """doctor() warns when launchd agent is registered but the service port is not responding."""
    import subprocess
    import sys

    import muxplex.cli as cli_mod
    import muxplex.settings as settings_mod

    # Create plist file so plist.exists() passes
    fake_home = tmp_path
    plist = fake_home / "Library" / "LaunchAgents" / "com.muxplex.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}")
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

    # Simulate macOS
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(cli_mod, "_have_launchctl", lambda: True)

    # launchctl print succeeds (agent is registered)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )

    # Port is NOT responding
    monkeypatch.setattr(cli_mod, "_probe_service_port", lambda port: False)

    cli_mod.doctor()

    out = capsys.readouterr().out
    assert "not serving" in out.lower(), (
        f"doctor() must warn 'not serving' when launchd is registered but port is down;"
        f" got: {out!r}"
    )


# ---------------------------------------------------------------------------
# task: never SIGTERM a HEALTHY muxplex — probe before kill
#
# A silent kill of a live server is indistinguishable from a mystery outage:
# it produces a clean graceful shutdown with no crash and no systemd "Stopping"
# line. These tests pin the refusal behaviour.
# ---------------------------------------------------------------------------


def _fake_lsof(pid_out: str):
    """subprocess.run stub returning *pid_out* as lsof stdout."""

    def fake_run(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": pid_out, "stderr": ""})()

    return fake_run


@pytest.mark.allow_real_port_killer
def test_kill_stale_port_holder_refuses_to_kill_healthy_server(monkeypatch, capsys):
    """A responding muxplex must NOT be killed; startup must abort instead."""
    import os
    import subprocess
    import muxplex.cli as cli_mod

    killed = []
    monkeypatch.setattr(subprocess, "run", _fake_lsof("4242\n"))
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(os, "getpid", lambda: 12345)
    monkeypatch.setattr(
        cli_mod, "_port_holder_is_healthy_muxplex", lambda *a, **k: True
    )

    with pytest.raises(SystemExit) as exc:
        cli_mod._kill_stale_port_holder(8088)

    assert exc.value.code != 0, "must exit non-zero rather than start"
    assert killed == [], f"must NOT signal a healthy server, but sent: {killed}"
    err = capsys.readouterr().err
    assert "8088" in err, "message must name the port"
    assert "4242" in err, "message must name the holder PID"
    assert "--force-take-port" in err, "message must offer the override"


@pytest.mark.allow_real_port_killer
def test_kill_stale_port_holder_kills_unresponsive_holder(monkeypatch):
    """A holder that does not answer the probe is stale — kill it as before."""
    import os
    import signal
    import subprocess
    import time
    import muxplex.cli as cli_mod

    killed = []
    monkeypatch.setattr(subprocess, "run", _fake_lsof("4242\n"))
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(os, "getpid", lambda: 12345)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setattr(
        cli_mod, "_port_holder_is_healthy_muxplex", lambda *a, **k: False
    )

    cli_mod._kill_stale_port_holder(8088)

    assert (4242, signal.SIGTERM) in killed


@pytest.mark.allow_real_port_killer
def test_kill_stale_port_holder_force_overrides_healthy_check(monkeypatch):
    """--force-take-port must kill even a healthy server."""
    import os
    import signal
    import subprocess
    import time
    import muxplex.cli as cli_mod

    killed = []
    monkeypatch.setattr(subprocess, "run", _fake_lsof("4242\n"))
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(os, "getpid", lambda: 12345)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setattr(
        cli_mod, "_port_holder_is_healthy_muxplex", lambda *a, **k: True
    )

    cli_mod._kill_stale_port_holder(8088, force=True)

    assert (4242, signal.SIGTERM) in killed


@pytest.mark.allow_real_port_killer
def test_kill_stale_port_holder_no_holder_never_probes(monkeypatch):
    """With nobody on the port there must be no probe and no signal."""
    import os
    import subprocess
    import muxplex.cli as cli_mod

    probed, killed = [], []

    def fake_run(cmd, **kw):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(
        cli_mod,
        "_port_holder_is_healthy_muxplex",
        lambda *a, **k: probed.append(True) or False,
    )

    cli_mod._kill_stale_port_holder(8088)

    assert probed == [], "must not probe when no process holds the port"
    assert killed == []


@pytest.mark.allow_real_port_killer
def test_kill_stale_port_holder_survives_missing_lsof(monkeypatch):
    """A missing/raising lsof must never prevent startup."""
    import subprocess
    import muxplex.cli as cli_mod

    def boom(cmd, **kw):
        raise FileNotFoundError("lsof")

    monkeypatch.setattr(subprocess, "run", boom)
    cli_mod._kill_stale_port_holder(8088)  # must not raise


def test_fetch_local_instance_info_returns_parsed_dict_on_200(monkeypatch):
    """200 + JSON object body => the parsed dict is returned verbatim."""
    import muxplex.cli as cli_mod

    class FakeResp:
        status = 200

        def read(self):
            return b'{"device_id": "abc", "version": "0.15.0", "name": "spark-1"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    result = cli_mod._fetch_local_instance_info(8088)
    assert result == {"device_id": "abc", "version": "0.15.0", "name": "spark-1"}


def test_fetch_local_instance_info_returns_none_when_nothing_answers(monkeypatch):
    """Refused/timeout on both schemes => None, not an exception."""
    import muxplex.cli as cli_mod

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert cli_mod._fetch_local_instance_info(8088) is None


def test_fetch_local_instance_info_returns_none_for_non_dict_body(monkeypatch):
    """A 200 response whose body isn't a JSON object (e.g. a bare list) => None."""
    import muxplex.cli as cli_mod

    class FakeResp:
        status = 200

        def read(self):
            return b"[1, 2, 3]"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    assert cli_mod._fetch_local_instance_info(8088) is None


def test_port_holder_is_healthy_muxplex_uses_shared_fetch_decision_only(monkeypatch):
    """After sharing the raw fetch, the healthy/unhealthy DECISION must be unchanged:
    device_id + version present => healthy; either missing, or no data => not healthy.

    This pins the deliberate design: _fetch_local_instance_info is a shared raw
    fetch, but the safety-critical decision stays entirely in
    _port_holder_is_healthy_muxplex.
    """
    import muxplex.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "_fetch_local_instance_info",
        lambda port, timeout=2.0: {"device_id": "x", "version": "1.0.0"},
    )
    assert cli_mod._port_holder_is_healthy_muxplex(8088) is True

    monkeypatch.setattr(
        cli_mod,
        "_fetch_local_instance_info",
        lambda port, timeout=2.0: {"hello": "world"},
    )
    assert cli_mod._port_holder_is_healthy_muxplex(8088) is False

    monkeypatch.setattr(
        cli_mod, "_fetch_local_instance_info", lambda port, timeout=2.0: None
    )
    assert cli_mod._port_holder_is_healthy_muxplex(8088) is False


def test_port_holder_probe_true_for_real_instance_info(monkeypatch):
    """200 + device_id + version => a healthy muxplex."""
    import muxplex.cli as cli_mod

    class FakeResp:
        status = 200

        def read(self):
            return b'{"device_id": "abc", "version": "0.14.0", "name": "spark-1"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    assert cli_mod._port_holder_is_healthy_muxplex(8088) is True


def test_port_holder_probe_false_for_non_muxplex_json(monkeypatch):
    """200 but the wrong shape => not a muxplex; do not treat as healthy."""
    import muxplex.cli as cli_mod

    class FakeResp:
        status = 200

        def read(self):
            return b'{"hello": "world"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    assert cli_mod._port_holder_is_healthy_muxplex(8088) is False


def test_port_holder_probe_false_when_connection_fails(monkeypatch):
    """Refused/timeout on both schemes => stale holder, safe to kill."""
    import muxplex.cli as cli_mod

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert cli_mod._port_holder_is_healthy_muxplex(8088) is False


def test_serve_parser_exposes_force_take_port():
    """The --force-take-port escape hatch must exist on serve."""
    import argparse
    import muxplex.cli as cli_mod

    parser = argparse.ArgumentParser()
    cli_mod._add_serve_flags(parser)
    args = parser.parse_args(["--force-take-port"])
    assert args.force_take_port is True
    assert parser.parse_args([]).force_take_port is False


# ---------------------------------------------------------------------------
# configure_logging() -- the accepted-/input-audit-line-never-fires fix
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_muxplex_logger():
    """Snapshot/restore the real "muxplex" logger's handlers+level.

    These tests deliberately mutate the actual "muxplex" package logger
    (the same one every muxplex.* module logs through) to prove
    configure_logging()'s real effect on it -- not a mock or a substitute
    logger. Restoring afterward keeps this test isolated from every other
    test in the suite that logs through muxplex.* loggers.
    """
    import logging as logging_mod

    package_logger = logging_mod.getLogger("muxplex")
    original_handlers = package_logger.handlers[:]
    original_level = package_logger.level
    package_logger.handlers.clear()
    package_logger.setLevel(logging_mod.NOTSET)
    try:
        yield package_logger
    finally:
        package_logger.handlers.clear()
        package_logger.handlers.extend(original_handlers)
        package_logger.setLevel(original_level)


def test_configure_logging_lets_accepted_audit_line_reach_a_real_handler(
    _isolated_muxplex_logger,
):
    """Regression test for the audit-log-never-fires bug.

    Before this fix, uvicorn.run(log_level="info") configured only
    uvicorn's OWN loggers, never touching root or any muxplex.* logger.
    An accepted /input call's audit `_log.info(...)` line therefore never
    passed the effective-level gate and reached no handler at all.

    This test deliberately does NOT use caplog.set_level()/at_level() --
    that API forces the level itself and would pass even against the
    broken code (see muxplex/tests/test_input.py's
    test_audit_log_line_present_and_redacted, which does exactly that and
    would not have caught this bug). Instead it starts from a pristine,
    unconfigured "muxplex" logger, proves the pre-fix state is actually
    broken, then calls the real production configure_logging() and proves
    a log record travels all the way to an independently-attached handler
    -- not just that isEnabledFor() flips.
    """
    import logging as logging_mod

    from muxplex.cli import configure_logging

    main_logger = logging_mod.getLogger("muxplex.main")

    # Pre-fix state: nothing has configured this logger, so INFO records
    # never pass the gate -- this is the actual bug, reproduced.
    assert not main_logger.isEnabledFor(logging_mod.INFO), (
        "test setup invalid: muxplex.main must start unconfigured"
    )

    configure_logging()

    assert main_logger.isEnabledFor(logging_mod.INFO), (
        "configure_logging() must raise muxplex.main's effective level to INFO"
    )

    captured: list[str] = []

    class _Capture(logging_mod.Handler):
        def emit(self, record: logging_mod.LogRecord) -> None:
            captured.append(record.getMessage())

    probe = _Capture()
    _isolated_muxplex_logger.addHandler(probe)
    try:
        # Exercise the exact call shape used by the /input audit line.
        main_logger.info("input: session=%r chars=%d", "alpha", 4)
    finally:
        _isolated_muxplex_logger.removeHandler(probe)

    assert any("input: session='alpha' chars=4" in msg for msg in captured), (
        f"accepted-input audit line must reach a real handler, got: {captured!r}"
    )


def test_configure_logging_does_not_widen_third_party_loggers(
    _isolated_muxplex_logger,
):
    """Deliberately scoped to the "muxplex" namespace, not the root logger.

    Configuring root wholesale would also raise unrelated dependency
    loggers (httpx, websockets, ...) to INFO, turning the audit trail into
    a noisy firehose. A sibling top-level logger must be unaffected.
    """
    import logging as logging_mod

    from muxplex.cli import configure_logging

    unrelated = logging_mod.getLogger("some_unrelated_dependency")
    original_level = unrelated.level
    unrelated.setLevel(logging_mod.NOTSET)
    try:
        configure_logging()
        assert not unrelated.isEnabledFor(logging_mod.INFO), (
            "configure_logging() must not raise unrelated loggers to INFO"
        )
    finally:
        unrelated.setLevel(original_level)


def test_configure_logging_is_idempotent(_isolated_muxplex_logger):
    """Calling configure_logging() twice must not install duplicate handlers.

    serve() may run more than once in a single process (e.g. across tests
    or a supervised restart loop) -- repeated calls must not accumulate
    handlers and duplicate every log line.
    """
    from muxplex.cli import configure_logging

    configure_logging()
    configure_logging()

    audit_handlers = [
        h for h in _isolated_muxplex_logger.handlers if h.name == "muxplex-audit"
    ]
    assert len(audit_handlers) == 1, (
        f"expected exactly one audit handler, got {len(audit_handlers)}"
    )


def test_serve_calls_configure_logging(tmp_path, monkeypatch):
    """serve() must configure logging before starting uvicorn.

    Without this call, muxplex's own INFO-level logging (including the
    /input audit line) is silently discarded once uvicorn.run() takes over.
    """
    import muxplex.cli as cli_mod

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", settings_file)

    calls = []
    monkeypatch.setattr(cli_mod, "configure_logging", lambda: calls.append(True))

    with patch("uvicorn.run"):
        with patch.dict("sys.modules", {"muxplex.main": MagicMock()}):
            cli_mod.serve()

    assert len(calls) == 1, "serve() must call configure_logging() exactly once"

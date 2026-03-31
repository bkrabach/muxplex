"""muxplex CLI — web-based tmux session dashboard."""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import secrets as _secrets

from muxplex.auth import (
    get_password_path,
    get_secret_path,
    load_password,
    pam_available,
)

# Module-level path constants (overridable in tests via monkeypatch)
_system_service_path = Path("/etc/systemd/system/muxplex.service")


def _get_install_info() -> dict:
    """Detect how muxplex was installed using PEP 610 direct_url.json.

    Returns dict with keys:
      source: 'git' | 'editable' | 'pypi' | 'unknown'
      version: installed version string
      commit: installed commit sha (git only)
      url: git repo URL (git only)
    """
    import json
    from importlib.metadata import PackageNotFoundError, distribution

    info: dict = {
        "source": "unknown",
        "version": "0.0.0",
        "commit": None,
        "url": None,
    }

    try:
        dist = distribution("muxplex")
        info["version"] = dist.metadata["Version"]

        du_text = dist.read_text("direct_url.json")
        if du_text:
            du = json.loads(du_text)

            if "vcs_info" in du:
                info["source"] = "git"
                info["commit"] = du["vcs_info"].get("commit_id", "")
                info["url"] = du.get("url", "")
            elif "dir_info" in du and du["dir_info"].get("editable"):
                info["source"] = "editable"
            else:
                info["source"] = "unknown"
        else:
            # No direct_url.json → probably PyPI
            info["source"] = "pypi"
    except PackageNotFoundError:
        pass

    return info


def _check_for_update(info: dict) -> tuple[bool, str]:
    """Check if an update is available. Returns (update_available, message).

    For git: compares installed commit_id against remote HEAD sha.
    For pypi: compares installed version against latest PyPI version.
    For editable: always returns (False, "editable install").
    For unknown: always returns (True, "unknown install source").
    """
    import json
    import urllib.request

    if info["source"] == "editable":
        return False, "editable install — manage updates manually"

    if info["source"] == "git":
        try:
            result = subprocess.run(
                ["git", "ls-remote", info["url"], "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return True, "could not check remote — upgrading to be safe"

            remote_sha = (
                result.stdout.strip().split()[0] if result.stdout.strip() else ""
            )
            local_sha = info["commit"] or ""

            if not remote_sha:
                return True, "could not read remote sha — upgrading to be safe"

            if local_sha == remote_sha:
                return False, f"up to date (commit {local_sha[:8]})"
            else:
                return True, f"update available ({local_sha[:8]} → {remote_sha[:8]})"
        except Exception:
            return True, "check failed — upgrading to be safe"

    if info["source"] == "pypi":
        try:
            req = urllib.request.Request(
                "https://pypi.org/pypi/muxplex/json",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                latest = data["info"]["version"]
                if latest == info["version"]:
                    return False, f"up to date (v{info['version']})"
                else:
                    return True, f"update available (v{info['version']} → v{latest})"
        except Exception:
            return True, "could not check PyPI — upgrading to be safe"

    # Unknown source
    return True, "unknown install source — upgrading to be safe"


def reset_secret() -> None:
    """Regenerate the signing secret and warn that all sessions are now invalid."""
    path = get_secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = _secrets.token_urlsafe(32)
    path.write_text(secret + "\n")
    path.chmod(0o600)
    print(f"Secret written to {path}")
    print("Warning: all active sessions are now invalid.")


def show_password() -> None:
    """Print the current muxplex password or indicate PAM mode."""
    auth_mode = os.environ.get("MUXPLEX_AUTH", "").lower()
    if auth_mode != "password" and pam_available():
        print("Auth mode: PAM — no password file used")
        return
    pw = load_password()
    if pw:
        print(f"Password: {pw}")
    else:
        print("No password file found. Start muxplex to auto-generate one.")


def serve(
    host: str | None = None,
    port: int | None = None,
    auth: str | None = None,
    session_ttl: int | None = None,
) -> None:
    """Start the muxplex server.

    Resolution order: CLI flag (if not None) > settings.json > hardcoded default.
    """
    import uvicorn  # noqa: PLC0415

    from muxplex.settings import load_settings  # noqa: PLC0415

    settings = load_settings()
    host = host if host is not None else settings.get("host", "127.0.0.1")
    port = port if port is not None else settings.get("port", 8088)
    auth = auth if auth is not None else settings.get("auth", "pam")
    session_ttl = (
        session_ttl if session_ttl is not None else settings.get("session_ttl", 604800)
    )

    os.environ["MUXPLEX_PORT"] = str(port)
    os.environ["MUXPLEX_AUTH"] = auth
    os.environ["MUXPLEX_SESSION_TTL"] = str(session_ttl)

    from muxplex.main import app  # noqa: PLC0415

    print(f"  muxplex → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def doctor() -> None:
    """Run diagnostic checks and report system status."""
    ok_mark = "\033[32m✓\033[0m"  # green check
    fail_mark = "\033[31m✗\033[0m"  # red x
    warn_mark = "\033[33m!\033[0m"  # yellow warning

    print("\nmuxplex doctor\n")

    # Python version
    py_version = platform.python_version()
    py_ok = tuple(int(x) for x in py_version.split(".")[:2]) >= (3, 11)
    print(
        f"  {ok_mark if py_ok else fail_mark} Python {py_version}"
        + ("" if py_ok else " (3.11+ required)")
    )

    # tmux
    tmux_path = shutil.which("tmux")
    if tmux_path:
        try:
            result = subprocess.run(
                ["tmux", "-V"], capture_output=True, text=True, timeout=5
            )
            tmux_version = result.stdout.strip()
            print(f"  {ok_mark} {tmux_version}")
        except Exception:
            print(f"  {ok_mark} tmux (version unknown)")
    else:
        print(f"  {fail_mark} tmux — not found")
        if sys.platform == "darwin":
            print("    Install: brew install tmux")
        else:
            print("    Install: sudo apt install tmux")

    # ttyd
    ttyd_path = shutil.which("ttyd")
    if ttyd_path:
        try:
            result = subprocess.run(
                ["ttyd", "--version"], capture_output=True, text=True, timeout=5
            )
            ttyd_version = result.stdout.strip() or result.stderr.strip()
            print(f"  {ok_mark} ttyd {ttyd_version}")
        except Exception:
            print(f"  {ok_mark} ttyd (version unknown)")
    else:
        print(f"  {fail_mark} ttyd — not found")
        if sys.platform == "darwin":
            print("    Install: brew install ttyd")
        else:
            print("    Install: sudo apt install ttyd")

    # muxplex version + install source + update check
    try:
        from importlib.metadata import version as pkg_version  # noqa: PLC0415

        muxplex_version = pkg_version("muxplex")
    except Exception:
        muxplex_version = "dev"

    info = _get_install_info()
    source_label = info["source"]
    if info["commit"]:
        source_label += f" @ {info['commit'][:8]}"
    print(f"  {ok_mark} muxplex {muxplex_version} (installed via {source_label})")

    update_available, update_msg = _check_for_update(info)
    if update_available:
        print(f"  {warn_mark} Update: {update_msg}")
        print("    Run: muxplex upgrade")
    else:
        print(f"  {ok_mark} {update_msg}")

    # Settings file
    from muxplex.settings import SETTINGS_PATH  # noqa: PLC0415

    if SETTINGS_PATH.exists():
        print(f"  {ok_mark} Settings: {SETTINGS_PATH}")
    else:
        print(
            f"  {warn_mark} Settings: {SETTINGS_PATH} (not yet created — will use defaults)"
        )

    # Auth status
    pw_path = get_password_path()
    if pam_available():
        import pwd  # noqa: PLC0415

        username = pwd.getpwuid(os.getuid()).pw_name
        print(f"  {ok_mark} Auth: PAM available (user: {username})")
    elif pw_path.exists():
        print(f"  {ok_mark} Auth: password file ({pw_path})")
    elif os.environ.get("MUXPLEX_PASSWORD"):
        print(f"  {ok_mark} Auth: password (env var)")
    else:
        print(f"  {warn_mark} Auth: no PAM, no password — will auto-generate on serve")

    # tmux sessions (if tmux is available)
    if tmux_path:
        try:
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                sessions = [s for s in result.stdout.strip().split("\n") if s]
                print(f"  {ok_mark} tmux sessions: {len(sessions)} active")
            else:
                print(f"  {warn_mark} tmux server not running (no sessions)")
        except Exception:
            print(f"  {warn_mark} tmux server not running")

    # Platform + service status
    print(f"  {ok_mark} Platform: {sys.platform} ({platform.machine()})")
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.muxplex.plist"
        if plist.exists():
            uid = os.getuid()
            result = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/com.muxplex"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print(f"  {ok_mark} Service: launchd agent running")
            else:
                print(
                    f"  {warn_mark} Service: launchd agent installed but not running ({plist})"
                )
        else:
            print(
                f"  {warn_mark} Service: not installed (run: muxplex install-service)"
            )
    else:
        systemd_user = Path.home() / ".config" / "systemd" / "user" / "muxplex.service"
        if systemd_user.exists():
            print(f"  {ok_mark} Service: systemd user unit installed ({systemd_user})")
        elif _system_service_path.exists():
            print(
                f"  {ok_mark} Service: systemd system unit installed ({_system_service_path})"
            )
        else:
            print(
                f"  {warn_mark} Service: not installed (run: muxplex install-service)"
            )

    print()  # trailing newline


def _check_dependencies() -> None:
    """Verify required external programs are installed.

    Checks for tmux and ttyd. Prints a helpful error message and exits with
    code 1 if any are missing.
    """
    missing = []
    if shutil.which("tmux") is None:
        missing.append(("tmux", "sudo apt install tmux  /  brew install tmux"))
    if shutil.which("ttyd") is None:
        missing.append(("ttyd", "sudo apt install ttyd  /  brew install ttyd"))

    if missing:
        print("\n  ERROR: Required dependencies not found:\n", file=sys.stderr)
        for name, install_hint in missing:
            print(f"    {name}: {install_hint}", file=sys.stderr)
        print(
            "\n  For details: https://github.com/bkrabach/muxplex#prerequisites\n",
            file=sys.stderr,
        )
        sys.exit(1)


def _install_launchd(executable: str) -> None:
    """Install a macOS launchd agent plist to ~/Library/LaunchAgents/."""
    import shutil

    # Prefer the entry point script ('muxplex') so macOS shows the correct
    # process name in Activity Monitor and launchctl list.
    muxplex_bin = shutil.which("muxplex")
    if muxplex_bin:
        program_args = f"""    <array>
        <string>{muxplex_bin}</string>
        <string>--host</string>
        <string>0.0.0.0</string>
    </array>"""
    else:
        program_args = f"""    <array>
        <string>{executable}</string>
        <string>-m</string>
        <string>muxplex</string>
        <string>--host</string>
        <string>0.0.0.0</string>
    </array>"""

    # Build PATH that includes Homebrew and common tool locations.
    # launchd agents run with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin).
    # Homebrew binaries (tmux, ttyd, uv, git) won't be found without this.
    _homebrew_paths = [
        "/opt/homebrew/bin",  # Apple Silicon Homebrew
        "/usr/local/bin",  # Intel Homebrew + system extras
        "/opt/homebrew/sbin",
        "/usr/local/sbin",
    ]
    _user_local = str(Path.home() / ".local" / "bin")  # uv/pip tool installs
    _extra = [_user_local] + _homebrew_paths
    _base = "/usr/bin:/bin:/usr/sbin:/sbin"
    _service_path = ":".join(_extra) + ":" + _base

    label = "com.muxplex"
    uid = os.getuid()
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
{program_args}
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{_service_path}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/muxplex.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/muxplex.err</string>
</dict>
</plist>
"""
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)

    # Unload existing service first (ignore errors if not loaded)
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True,
    )

    path.write_text(plist)
    print(f"Launch agent written to {path}")
    print()

    # Load the service using the modern bootstrap API
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"  Service started (launchctl bootstrap gui/{uid})")
    else:
        # Fallback to legacy load for older macOS
        result2 = subprocess.run(
            ["launchctl", "load", str(path)],
            capture_output=True,
            text=True,
        )
        if result2.returncode == 0:
            print("  Service started (launchctl load)")
        else:
            print("  Could not auto-start. Try manually:")
            print(f"    launchctl bootstrap gui/{uid} {path}")

    print()
    print("Management commands:")
    print(f"  Stop:    launchctl bootout gui/{uid}/{label}")
    print(f"  Start:   launchctl bootstrap gui/{uid} {path}")
    print(f"  Status:  launchctl print gui/{uid}/{label}")
    print("  Logs:    tail -f /tmp/muxplex.log")


def _install_systemd(executable: str, *, system: bool = False) -> None:
    """Install a Linux systemd service unit file."""
    _raw_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    _safe_path = ":".join(p for p in _raw_path.split(":") if not p.startswith("/mnt/"))
    _safe_path = _safe_path or "/usr/local/bin:/usr/bin:/bin"

    unit = f"""\
[Unit]
Description=muxplex — web-based tmux session dashboard
After=network.target

[Service]
Type=simple
ExecStart={executable} -m muxplex
Restart=on-failure
RestartSec=5s
Environment=PATH={_safe_path}

[Install]
WantedBy={"multi-user.target" if system else "default.target"}
"""

    if system:
        path = _system_service_path
        reload_cmd = (
            "sudo systemctl daemon-reload && sudo systemctl enable --now muxplex"
        )
    else:
        path = Path.home() / ".config" / "systemd" / "user" / "muxplex.service"
        path.parent.mkdir(parents=True, exist_ok=True)
        reload_cmd = (
            "systemctl --user daemon-reload && systemctl --user enable --now muxplex"
        )

    path.write_text(unit)
    print(f"Service file written to {path}")
    print(f"Enable with:\n  {reload_cmd}")


def install_service(*, system: bool = False) -> None:
    """Install muxplex as a background service (launchd on macOS, systemd on Linux)."""
    executable = sys.executable

    if sys.platform == "darwin":
        _install_launchd(executable)
    else:
        _install_systemd(executable, system=system)


def upgrade(*, force: bool = False) -> None:
    """Upgrade muxplex to the latest version and restart the service."""
    print("\nmuxplex upgrade\n")

    # Show current install info
    info = _get_install_info()
    commit_suffix = f" (commit {info['commit'][:8]})" if info["commit"] else ""
    print(f"  Installed: v{info['version']}{commit_suffix} via {info['source']}")

    if not force:
        update_available, message = _check_for_update(info)
        print(f"  Status: {message}")

        if not update_available:
            print(
                "\n  Already up to date."
                " Use 'muxplex upgrade --force' to reinstall anyway.\n"
            )
            return
    else:
        print("  Status: --force specified — skipping version check")

    # 1. Detect platform and stop service
    if sys.platform == "darwin":
        label = "com.muxplex"
        uid = os.getuid()
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if plist.exists():
            print("  Stopping launchd service...")
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True
            )
        else:
            print("  No launchd service found (skipping stop)")
    else:
        # Linux/WSL — check systemd
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "muxplex"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("  Stopping systemd service...")
            subprocess.run(
                ["systemctl", "--user", "stop", "muxplex"], capture_output=True
            )
        else:
            print("  No active systemd service found (skipping stop)")

    # 2. Reinstall via uv tool install
    print("  Installing latest version...")
    uv_path = shutil.which("uv")
    if uv_path:
        result = subprocess.run(
            [
                uv_path,
                "tool",
                "install",
                "git+https://github.com/bkrabach/muxplex",
                "--force",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  ERROR: uv tool install failed:\n{result.stderr}")
            return
        print("  Installed successfully")
    else:
        # Fallback: pip
        pip_path = shutil.which("pip") or shutil.which("pip3")
        if pip_path:
            result = subprocess.run(
                [
                    pip_path,
                    "install",
                    "--upgrade",
                    "git+https://github.com/bkrabach/muxplex",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"  ERROR: pip install failed:\n{result.stderr}")
                return
            print("  Installed successfully")
        else:
            print("  ERROR: neither uv nor pip found — cannot upgrade")
            return

    # 3. Regenerate service file (picks up any plist/unit changes)
    print("  Regenerating service file...")
    install_service(system=False)

    # 4. Restart service
    if sys.platform == "darwin":
        label = "com.muxplex"
        uid = os.getuid()
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if plist.exists():
            print("  Starting launchd service...")
            result = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("  Service started")
            else:
                # Fallback to legacy load for older macOS
                subprocess.run(["launchctl", "load", str(plist)], capture_output=True)
                print("  Service started (legacy)")
        else:
            print("  Service file not found — run: muxplex install-service")
    else:
        result = subprocess.run(
            ["systemctl", "--user", "is-enabled", "muxplex"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("  Restarting systemd service...")
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"], capture_output=True
            )
            subprocess.run(
                ["systemctl", "--user", "start", "muxplex"], capture_output=True
            )
            print("  Service started")
        else:
            print("  Service not enabled — run: muxplex install-service")

    # 5. Doctor check
    print("\n  Verifying...")
    doctor()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="muxplex",
        description="muxplex — web-based tmux session dashboard",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    parser.add_argument("--port", type=int, default=8088, help="Port (default: 8088)")
    parser.add_argument(
        "--auth",
        choices=["pam", "password"],
        default="pam",
        help="Authentication method: pam or password (default: pam)",
    )
    parser.add_argument(
        "--session-ttl",
        type=int,
        default=604800,
        dest="session_ttl",
        help="Session TTL in seconds (default: 604800 = 7 days; 0 = browser session)",
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Start the server (default)")

    svc = sub.add_parser(
        "install-service",
        help="Install as a background service (systemd on Linux, launchd on macOS)",
    )
    svc.add_argument(
        "--system", action="store_true", help="System-wide (requires sudo)"
    )

    sub.add_parser("show-password", help="Show the current muxplex password")

    sub.add_parser(
        "reset-secret", help="Regenerate signing secret (invalidates sessions)"
    )

    sub.add_parser("doctor", help="Check dependencies and system status")

    upgrade_parser = sub.add_parser(
        "upgrade", help="Upgrade muxplex to latest version and restart service"
    )
    upgrade_parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinstall even if already up to date",
    )
    update_parser = sub.add_parser("update", help="Alias for upgrade")
    update_parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinstall even if already up to date",
    )

    args = parser.parse_args()

    if args.command == "install-service":
        install_service(system=args.system)
    elif args.command == "show-password":
        show_password()
    elif args.command == "reset-secret":
        reset_secret()
    elif args.command == "doctor":
        doctor()
    elif args.command in ("upgrade", "update"):
        upgrade(force=getattr(args, "force", False))
    else:
        _check_dependencies()
        serve(
            host=args.host, port=args.port, auth=args.auth, session_ttl=args.session_ttl
        )

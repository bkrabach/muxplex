"""muxplex CLI — web-based tmux session dashboard."""

import argparse
import os
import shutil
import sys
from pathlib import Path

import secrets as _secrets

from muxplex.auth import get_secret_path, load_password, pam_available

# Module-level path constants (overridable in tests via monkeypatch)
_system_service_path = Path("/etc/systemd/system/muxplex.service")


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
    host: str = "127.0.0.1",
    port: int = 8088,
    auth: str = "pam",
    session_ttl: int = 604800,
) -> None:
    """Start the muxplex server."""
    import uvicorn  # noqa: PLC0415

    os.environ.setdefault("MUXPLEX_PORT", str(port))
    os.environ.setdefault("MUXPLEX_AUTH", auth)
    os.environ.setdefault("MUXPLEX_SESSION_TTL", str(session_ttl))

    from muxplex.main import app  # noqa: PLC0415

    print(f"  muxplex → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


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
    label = "com.muxplex"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{executable}</string>
        <string>-m</string>
        <string>muxplex</string>
    </array>
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
    path.write_text(plist)
    print(f"Launch agent written to {path}")
    print("Enable with:")
    print(f"  launchctl load {path}")
    print("Disable with:")
    print(f"  launchctl unload {path}")


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

    args = parser.parse_args()

    if args.command == "install-service":
        install_service(system=args.system)
    elif args.command == "show-password":
        show_password()
    elif args.command == "reset-secret":
        reset_secret()
    else:
        _check_dependencies()
        serve(
            host=args.host, port=args.port, auth=args.auth, session_ttl=args.session_ttl
        )

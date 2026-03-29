"""muxplex CLI — web-based tmux session dashboard."""

import argparse
import os
import sys
from pathlib import Path

from muxplex.auth import load_password, pam_available

# Module-level path constants (overridable in tests via monkeypatch)
_system_service_path = Path("/etc/systemd/system/muxplex.service")


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


def install_service(*, system: bool = False) -> None:
    """Install muxplex as a systemd service."""
    executable = sys.executable

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

    svc = sub.add_parser("install-service", help="Install systemd service unit")
    svc.add_argument(
        "--system", action="store_true", help="System-wide (requires sudo)"
    )

    sub.add_parser("show-password", help="Show the current muxplex password")

    args = parser.parse_args()

    if args.command == "install-service":
        install_service(system=args.system)
    elif args.command == "show-password":
        show_password()
    else:
        serve(
            host=args.host, port=args.port, auth=args.auth, session_ttl=args.session_ttl
        )

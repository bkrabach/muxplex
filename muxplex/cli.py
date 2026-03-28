"""muxplex CLI — web-based tmux session dashboard."""

import argparse
import os
import sys
from pathlib import Path

# Module-level path constants (overridable in tests via monkeypatch)
_system_service_path = Path("/etc/systemd/system/muxplex.service")


def serve(host: str = "0.0.0.0", port: int = 8088) -> None:
    """Start the muxplex server."""
    import uvicorn  # noqa: PLC0415

    os.environ.setdefault("MUXPLEX_PORT", str(port))

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
        "--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)"
    )
    parser.add_argument("--port", type=int, default=8088, help="Port (default: 8088)")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Start the server (default)")

    svc = sub.add_parser("install-service", help="Install systemd service unit")
    svc.add_argument(
        "--system", action="store_true", help="System-wide (requires sudo)"
    )

    args = parser.parse_args()

    if args.command == "install-service":
        install_service(system=args.system)
    else:
        serve(host=args.host, port=args.port)

"""muxplex/service.py — System service management (systemd on Linux, launchd on macOS)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SYSTEMD_UNIT_DIR: Path = Path.home() / ".config" / "systemd" / "user"
_SYSTEMD_UNIT_PATH: Path = _SYSTEMD_UNIT_DIR / "muxplex.service"

_LAUNCHD_PLIST_DIR: Path = Path.home() / "Library" / "LaunchAgents"
_LAUNCHD_PLIST_PATH: Path = _LAUNCHD_PLIST_DIR / "com.muxplex.plist"
_LAUNCHD_LABEL: str = "com.muxplex"

_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=muxplex
After=network.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5s
Environment=PATH={safe_path}

[Install]
WantedBy=default.target
"""

_LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{muxplex_bin}</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{safe_path}</string>
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

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _is_darwin() -> bool:
    """Return True if running on macOS."""
    return sys.platform == "darwin"


def _resolve_muxplex_bin() -> str:
    """Return the muxplex binary path.

    Prefers the ``muxplex`` executable on PATH; falls back to
    ``<sys.executable> -m muxplex`` when not found.
    """
    which = shutil.which("muxplex")
    if which:
        return which
    return f"{sys.executable} -m muxplex"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _prompt_host_if_localhost() -> None:
    """Prompt the user to change host from 127.0.0.1 to 0.0.0.0 for service use."""
    from muxplex.settings import load_settings, patch_settings

    settings = load_settings()
    if settings["host"] == "127.0.0.1":
        answer = input(
            "Host is 127.0.0.1 — change to 0.0.0.0 so the service is reachable? [Y/n] "
        )
        if answer.strip().lower() in ("y", ""):
            patch_settings({"host": "0.0.0.0"})


# ---------------------------------------------------------------------------
# Private implementations — systemd (Linux)
# ---------------------------------------------------------------------------


def _systemd_install() -> None:
    muxplex_bin = _resolve_muxplex_bin()
    safe_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    exec_start = f"{muxplex_bin} serve"
    unit_content = _SYSTEMD_UNIT_TEMPLATE.format(
        exec_start=exec_start, safe_path=safe_path
    )
    _SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    _SYSTEMD_UNIT_PATH.write_text(unit_content)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "muxplex"], check=True)
    _prompt_host_if_localhost()


def _systemd_uninstall() -> None:
    subprocess.run(["systemctl", "--user", "stop", "muxplex"], check=True)
    subprocess.run(["systemctl", "--user", "disable", "muxplex"], check=True)
    _SYSTEMD_UNIT_PATH.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


def _systemd_start() -> None:
    subprocess.run(["systemctl", "--user", "start", "muxplex"], check=True)


def _systemd_stop() -> None:
    subprocess.run(["systemctl", "--user", "stop", "muxplex"], check=True)


def _systemd_restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", "muxplex"], check=True)


def _systemd_status() -> None:
    subprocess.run(
        ["systemctl", "--user", "status", "muxplex", "--no-pager"], check=True
    )


def _systemd_logs() -> None:
    subprocess.run(["journalctl", "--user", "-u", "muxplex", "-f"], check=True)


# ---------------------------------------------------------------------------
# Private implementations — launchd (macOS)
# ---------------------------------------------------------------------------


def _launchd_install() -> None:
    muxplex_bin = _resolve_muxplex_bin()
    base_path = os.environ.get("PATH", "/usr/bin:/bin")
    safe_path = f"/opt/homebrew/bin:/usr/local/bin:{base_path}"
    plist_content = _LAUNCHD_PLIST_TEMPLATE.format(
        label=_LAUNCHD_LABEL, muxplex_bin=muxplex_bin, safe_path=safe_path
    )
    _LAUNCHD_PLIST_DIR.mkdir(parents=True, exist_ok=True)
    _LAUNCHD_PLIST_PATH.write_text(plist_content)
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(_LAUNCHD_PLIST_PATH)], check=True
    )
    _prompt_host_if_localhost()


def _launchd_uninstall() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LAUNCHD_LABEL}"], check=True)
    _LAUNCHD_PLIST_PATH.unlink(missing_ok=True)


def _launchd_start() -> None:
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(_LAUNCHD_PLIST_PATH)], check=True
    )


def _launchd_stop() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LAUNCHD_LABEL}"], check=True)


def _launchd_restart() -> None:
    _launchd_stop()
    _launchd_start()


def _launchd_status() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "print", f"gui/{uid}/{_LAUNCHD_LABEL}"], check=True)


def _launchd_logs() -> None:
    subprocess.run(["tail", "-f", "/tmp/muxplex.log"], check=True)


# ---------------------------------------------------------------------------
# Public API — platform-dispatching wrappers
# ---------------------------------------------------------------------------


def service_install() -> None:
    """Install the muxplex service unit for the current user."""
    if _is_darwin():
        _launchd_install()
    else:
        _systemd_install()


def service_uninstall() -> None:
    """Remove the muxplex service unit for the current user."""
    if _is_darwin():
        _launchd_uninstall()
    else:
        _systemd_uninstall()


def service_start() -> None:
    """Start the muxplex service."""
    if _is_darwin():
        _launchd_start()
    else:
        _systemd_start()


def service_stop() -> None:
    """Stop the muxplex service."""
    if _is_darwin():
        _launchd_stop()
    else:
        _systemd_stop()


def service_restart() -> None:
    """Restart the muxplex service."""
    if _is_darwin():
        _launchd_restart()
    else:
        _systemd_restart()


def service_status() -> None:
    """Print the current status of the muxplex service."""
    if _is_darwin():
        _launchd_status()
    else:
        _systemd_status()


def service_logs() -> None:
    """Stream or print logs for the muxplex service."""
    if _is_darwin():
        _launchd_logs()
    else:
        _systemd_logs()

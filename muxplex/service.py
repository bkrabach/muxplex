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
TimeoutStopSec=10
KillMode=mixed
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
{program_arguments_xml}
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


def _have_systemctl() -> bool:
    """Return True if systemctl is on PATH (gates all systemd service operations)."""
    return shutil.which("systemctl") is not None


def _resolve_muxplex_bin() -> str:
    """Return the muxplex binary path.

    Prefers the ``muxplex`` executable on PATH; falls back to
    ``<sys.executable> -m muxplex`` when not found.
    """
    which = shutil.which("muxplex")
    if which:
        return which
    return f"{sys.executable} -m muxplex"


def _resolve_muxplex_bin_for_launchd() -> list[str]:
    """Return the argv token list for the muxplex binary in a launchd plist.

    Uses Option A: prefer ``~/.local/bin/muxplex`` (stable uv-tool
    console-script symlink that survives ``uv tool reinstall``).  Falls back
    to ``shutil.which("muxplex")``, then to ``[sys.executable, "-m",
    "muxplex"]`` as explicitly split tokens.

    Each element must become its own ``<string>`` in ProgramArguments.
    launchd does **not** shell-split inside a ``<string>``; an element like
    ``"python3 -m muxplex"`` is treated as a literal executable name, causing
    the daemon to silently fail to start.
    """
    # Option A: stable console-script symlink installed by `uv tool`
    local_bin = Path.home() / ".local" / "bin" / "muxplex"
    if local_bin.exists() and os.access(str(local_bin), os.X_OK):
        return [str(local_bin)]

    # Fall back to PATH lookup
    which = shutil.which("muxplex")
    if which:
        return [which]

    # Last resort: explicit python -m invocation — correctly split into tokens
    return [sys.executable, "-m", "muxplex"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _prompt_host_if_localhost() -> None:
    """Prompt the user to change host from 127.0.0.1 to 0.0.0.0 for service use."""
    from muxplex.settings import load_settings, patch_settings

    settings = load_settings()
    if settings.get("host") == "127.0.0.1":
        try:
            answer = (
                input(
                    "Host is 127.0.0.1 — change to 0.0.0.0 so the service is reachable? [Y/n] "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("y", ""):
            patch_settings({"host": "0.0.0.0"})


# ---------------------------------------------------------------------------
# Private implementations — systemd (Linux)
# ---------------------------------------------------------------------------


def _show_tls_nudge_if_needed() -> None:
    """Show TLS setup nudge if host is network and TLS is not configured."""
    from muxplex.settings import load_settings

    settings = load_settings()
    host = settings.get("host", "127.0.0.1")
    tls_cert = settings.get("tls_cert", "")

    if host != "127.0.0.1" and not tls_cert:
        print("  Tip: Enable HTTPS for clipboard support: muxplex setup-tls")


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
    _show_tls_nudge_if_needed()


def _systemd_uninstall() -> None:
    subprocess.run(["systemctl", "--user", "stop", "muxplex"])
    subprocess.run(["systemctl", "--user", "disable", "muxplex"])
    _SYSTEMD_UNIT_PATH.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


def _systemd_start() -> None:
    subprocess.run(["systemctl", "--user", "start", "muxplex"], check=True)


def _systemd_stop() -> None:
    subprocess.run(["systemctl", "--user", "stop", "muxplex"])


def _systemd_restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", "muxplex"], check=True)


def _systemd_status() -> None:
    subprocess.run(["systemctl", "--user", "status", "muxplex", "--no-pager"])


def _systemd_logs() -> None:
    try:
        subprocess.run(["journalctl", "--user", "-u", "muxplex", "-f"])
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Private implementations — launchd (macOS)
# ---------------------------------------------------------------------------


def _launchd_install() -> None:
    bin_args = _resolve_muxplex_bin_for_launchd()
    argv = bin_args + ["serve"]
    # Each argv token is its own <string> element.  launchd does NOT
    # shell-split inside a <string>, so we must NOT put the whole command
    # (e.g. "python3 -m muxplex") into a single element.
    program_arguments_xml = "\n".join(
        f"        <string>{arg}</string>" for arg in argv
    )
    base_path = os.environ.get("PATH", "/usr/bin:/bin")
    safe_path = f"/opt/homebrew/bin:/usr/local/bin:{base_path}"
    plist_content = _LAUNCHD_PLIST_TEMPLATE.format(
        label=_LAUNCHD_LABEL,
        program_arguments_xml=program_arguments_xml,
        safe_path=safe_path,
    )
    _LAUNCHD_PLIST_DIR.mkdir(parents=True, exist_ok=True)
    _LAUNCHD_PLIST_PATH.write_text(plist_content)
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(_LAUNCHD_PLIST_PATH)], check=True
    )
    _prompt_host_if_localhost()
    _show_tls_nudge_if_needed()


def _launchd_uninstall() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LAUNCHD_LABEL}"])
    _LAUNCHD_PLIST_PATH.unlink(missing_ok=True)


def _launchd_start() -> None:
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(_LAUNCHD_PLIST_PATH)], check=True
    )


def _launchd_stop() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LAUNCHD_LABEL}"])


def _launchd_restart() -> None:
    _launchd_stop()
    _launchd_start()


def _launchd_status() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "print", f"gui/{uid}/{_LAUNCHD_LABEL}"])


def _launchd_logs() -> None:
    try:
        subprocess.run(["tail", "-f", "/tmp/muxplex.log"])
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Public API — platform-dispatching wrappers
# ---------------------------------------------------------------------------


def _no_systemctl_error(command: str) -> None:
    """Print a clear error when systemctl is not available."""
    print(
        f"  ERROR: 'muxplex service {command}' requires systemctl, which was not found on PATH.",
        file=sys.stderr,
    )
    print(
        "  This system does not appear to use systemd (e.g. Unraid, BSD, macOS, container).",
        file=sys.stderr,
    )
    print(
        "  Run muxplex serve directly to start the server without a service manager.",
        file=sys.stderr,
    )


def service_install() -> None:
    """Install the muxplex service unit for the current user."""
    if _is_darwin():
        _launchd_install()
    elif _have_systemctl():
        _systemd_install()
    else:
        _no_systemctl_error("install")


def service_uninstall() -> None:
    """Remove the muxplex service unit for the current user."""
    if _is_darwin():
        _launchd_uninstall()
    elif _have_systemctl():
        _systemd_uninstall()
    else:
        _no_systemctl_error("uninstall")


def service_start() -> None:
    """Start the muxplex service."""
    if _is_darwin():
        _launchd_start()
    elif _have_systemctl():
        _systemd_start()
    else:
        _no_systemctl_error("start")


def service_stop() -> None:
    """Stop the muxplex service."""
    if _is_darwin():
        _launchd_stop()
    elif _have_systemctl():
        _systemd_stop()
    else:
        _no_systemctl_error("stop")


def service_restart() -> None:
    """Restart the muxplex service."""
    if _is_darwin():
        _launchd_restart()
    elif _have_systemctl():
        _systemd_restart()
    else:
        _no_systemctl_error("restart")


def service_status() -> None:
    """Print the current status of the muxplex service."""
    if _is_darwin():
        _launchd_status()
    elif _have_systemctl():
        _systemd_status()
    else:
        _no_systemctl_error("status")


def service_logs() -> None:
    """Stream or print logs for the muxplex service."""
    if _is_darwin():
        _launchd_logs()
    elif _have_systemctl():
        _systemd_logs()
    else:
        _no_systemctl_error("logs")

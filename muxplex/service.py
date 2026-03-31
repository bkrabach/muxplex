"""muxplex/service.py — System service management (systemd on Linux, launchd on macOS)."""

import shutil
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
# Private stubs — systemd (Linux)
# ---------------------------------------------------------------------------


def _systemd_install() -> None:
    raise NotImplementedError("systemd install not implemented")


def _systemd_uninstall() -> None:
    raise NotImplementedError("systemd uninstall not implemented")


def _systemd_start() -> None:
    raise NotImplementedError("systemd start not implemented")


def _systemd_stop() -> None:
    raise NotImplementedError("systemd stop not implemented")


def _systemd_restart() -> None:
    raise NotImplementedError("systemd restart not implemented")


def _systemd_status() -> None:
    raise NotImplementedError("systemd status not implemented")


def _systemd_logs() -> None:
    raise NotImplementedError("systemd logs not implemented")


# ---------------------------------------------------------------------------
# Private stubs — launchd (macOS)
# ---------------------------------------------------------------------------


def _launchd_install() -> None:
    raise NotImplementedError("launchd install not implemented")


def _launchd_uninstall() -> None:
    raise NotImplementedError("launchd uninstall not implemented")


def _launchd_start() -> None:
    raise NotImplementedError("launchd start not implemented")


def _launchd_stop() -> None:
    raise NotImplementedError("launchd stop not implemented")


def _launchd_restart() -> None:
    raise NotImplementedError("launchd restart not implemented")


def _launchd_status() -> None:
    raise NotImplementedError("launchd status not implemented")


def _launchd_logs() -> None:
    raise NotImplementedError("launchd logs not implemented")


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

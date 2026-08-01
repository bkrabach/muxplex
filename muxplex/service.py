"""muxplex/service.py — System service management (systemd on Linux, launchd on macOS)."""

import os
import shutil
import subprocess
import sys
import time
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
KillMode=process
Environment=PATH={safe_path}
{extra_environment_lines}
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
{extra_environment_variables_xml}
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
# Environment propagation — TMUX_TMPDIR
# ---------------------------------------------------------------------------
#
# systemd --user and launchd services do NOT inherit the installer's shell
# rc-file exports (.bashrc/.zshrc). If a user has moved their tmux socket
# directory via `TMUX_TMPDIR` (e.g. to avoid a world-writable /tmp), a
# service-mode muxplex silently falls back to tmux's compiled-in
# /tmp/tmux-$(id -u) default and can no longer see any of the user's
# sessions — with no error, just an empty session list. Baking TMUX_TMPDIR
# into the unit/plist (mirroring the existing PATH propagation) closes that
# gap the same way for both service managers.


def _tmux_tmpdir_env_line() -> str:
    """Return a systemd `Environment=TMUX_TMPDIR=...` line, or '' if unset.

    Reads TMUX_TMPDIR from the installer's current environment so a
    customized tmux socket directory survives into the systemd unit.
    """
    tmux_tmpdir = os.environ.get("TMUX_TMPDIR")
    if not tmux_tmpdir:
        return ""
    return f"Environment=TMUX_TMPDIR={tmux_tmpdir}"


def _tmux_tmpdir_plist_xml() -> str:
    """Return a launchd EnvironmentVariables <key>/<string> XML block for
    TMUX_TMPDIR, or '' if unset. Mirrors _tmux_tmpdir_env_line() for launchd.
    """
    tmux_tmpdir = os.environ.get("TMUX_TMPDIR")
    if not tmux_tmpdir:
        return ""
    return f"        <key>TMUX_TMPDIR</key>\n        <string>{tmux_tmpdir}</string>"


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
        exec_start=exec_start,
        safe_path=safe_path,
        extra_environment_lines=_tmux_tmpdir_env_line(),
    )
    _SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    _SYSTEMD_UNIT_PATH.write_text(unit_content)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "muxplex"], check=True)
    # `enable --now` is a no-op on an already-running service, so re-installing
    # (e.g. after TMUX_TMPDIR or PATH changed) would silently keep the stale
    # environment without this restart. `restart` starts a stopped unit too,
    # so it is safe on both first install and re-install.
    subprocess.run(["systemctl", "--user", "restart", "muxplex"], check=True)
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
    program_arguments_xml = "\n".join(f"        <string>{arg}</string>" for arg in argv)
    base_path = os.environ.get("PATH", "/usr/bin:/bin")
    safe_path = f"/opt/homebrew/bin:/usr/local/bin:{base_path}"
    plist_content = _LAUNCHD_PLIST_TEMPLATE.format(
        label=_LAUNCHD_LABEL,
        program_arguments_xml=program_arguments_xml,
        safe_path=safe_path,
        extra_environment_variables_xml=_tmux_tmpdir_plist_xml(),
    )
    _LAUNCHD_PLIST_DIR.mkdir(parents=True, exist_ok=True)
    _LAUNCHD_PLIST_PATH.write_text(plist_content)
    uid = os.getuid()
    # bootstrap on an already-loaded label fails with EEXIST-style errors, so
    # bootout first (ignore failure if it wasn't loaded) to force the new
    # plist's environment (e.g. an updated TMUX_TMPDIR) to actually apply on
    # re-install, not just on first install.
    _launchd_bootout_and_wait(uid)
    _launchd_bootstrap(uid)
    _prompt_host_if_localhost()
    _show_tls_nudge_if_needed()


def _launchd_is_loaded(uid: int) -> bool:
    """True if launchd currently knows about our label."""
    return (
        subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{_LAUNCHD_LABEL}"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _launchd_bootout_and_wait(uid: int, *, timeout: float = 10.0) -> bool:
    """bootout the job and WAIT for launchd to actually finish tearing it down.

    `launchctl bootout` returns before the job is gone. Not waiting is what made
    `muxplex service restart` a silent no-op: bootstrap raced the teardown, saw
    the OLD job still loaded, and reported success while the old process kept
    serving. Stop must mean stopped before start can mean started.

    Returns True if the job is confirmed gone, False if it outlived the timeout.
    """
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{_LAUNCHD_LABEL}"], capture_output=True
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _launchd_is_loaded(uid):
            return True
        time.sleep(0.25)
    return not _launchd_is_loaded(uid)


def _launchd_bootstrap(
    uid: int, *, attempts: int = 6, accept_already_loaded: bool = False
) -> None:
    """bootstrap the plist, retrying through launchd's asynchronous teardown.

    Exit 5 ("Input/output error") right after a bootout is the teardown race,
    not a real failure, so it is retried.

    `accept_already_loaded` is the load-bearing distinction. For `start` --
    "make sure it is running" -- finding it already loaded IS the desired state.
    For `install` and `restart` the caller has just booted the job out and is
    replacing it, so an already-loaded job means the OLD one survived; reporting
    success there is a lie that hides a failed upgrade. Only `start` opts in.

    Real failures fail LOUDLY, with launchd's own stderr and an actionable hint
    rather than a raw CalledProcessError traceback.
    """
    last = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.5)
        last = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(_LAUNCHD_PLIST_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        if last.returncode == 0:
            return
        # Exit 5 (EIO) and 37 are the teardown race; retry. Anything else is a
        # genuine error and retrying only delays the report.
        if last.returncode not in (5, 37):
            break

    if accept_already_loaded and _launchd_is_loaded(uid):
        return

    detail = (last.stderr or last.stdout or "").strip() if last else ""
    code = last.returncode if last else "unknown"
    raise RuntimeError(
        f"launchctl bootstrap failed (exit {code})"
        + (f": {detail}" if detail else "")
        + f"\n  The service plist is at {_LAUNCHD_PLIST_PATH}."
        + f"\n  Try: launchctl bootout gui/{uid}/{_LAUNCHD_LABEL} && "
        + f"launchctl bootstrap gui/{uid} {_LAUNCHD_PLIST_PATH}"
        + "\n  Or run 'muxplex serve' directly to start without a service manager."
    )


def _launchd_uninstall() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LAUNCHD_LABEL}"])
    _LAUNCHD_PLIST_PATH.unlink(missing_ok=True)


def _launchd_start() -> None:
    _launchd_bootstrap(os.getuid(), accept_already_loaded=True)


def _launchd_stop() -> None:
    _launchd_bootout_and_wait(os.getuid())


def _launchd_restart() -> None:
    uid = os.getuid()
    if not _launchd_bootout_and_wait(uid):
        raise RuntimeError(
            f"launchctl bootout did not release {_LAUNCHD_LABEL} within the timeout, "
            f"so restarting would leave the OLD process running.\n"
            f"  Check it: launchctl print gui/{uid}/{_LAUNCHD_LABEL}\n"
            f"  Then:     launchctl bootout gui/{uid}/{_LAUNCHD_LABEL}"
        )
    _launchd_bootstrap(uid)


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

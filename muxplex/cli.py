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


def _have_systemctl() -> bool:
    """Return True if systemctl is on PATH (used to gate service-management steps)."""
    return shutil.which("systemctl") is not None


def _have_launchctl() -> bool:
    """Return True if launchctl is on PATH (used to gate service-management steps)."""
    return shutil.which("launchctl") is not None


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


def generate_federation_key() -> None:
    """Generate a random federation key and write it to FEDERATION_KEY_PATH."""
    import muxplex.settings as settings_mod

    path = settings_mod.FEDERATION_KEY_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = _secrets.token_urlsafe(32)
    path.write_text(key + "\n")
    path.chmod(0o600)
    print(f"Federation key written to {path}")
    print(f"Key: {key}")


def reset_secret() -> None:
    """Regenerate the signing secret and warn that all sessions are now invalid."""
    path = get_secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = _secrets.token_urlsafe(32)
    path.write_text(secret + "\n")
    path.chmod(0o600)
    print(f"Secret written to {path}")
    print("Warning: all active sessions are now invalid.")


def reset_device_id_command() -> None:
    """Regenerate the device identity UUID and warn about orphaned session keys."""
    from muxplex.identity import IDENTITY_PATH, load_device_id, reset_device_id  # noqa: PLC0415

    old_id = load_device_id()
    new_id = reset_device_id()
    print(f"New device_id: {new_id}")
    print(f"Identity file: {IDENTITY_PATH}")
    print(f"Previous device_id: {old_id}")
    print("Warning: existing session keys are now orphaned.")


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


def _kill_stale_port_holder(port: int) -> None:
    """Kill any existing process on *port* to prevent EADDRINUSE crash-loops.

    On service restart (``systemctl restart muxplex``), the old process may still
    be holding the port in TIME_WAIT state or simply not have exited yet.  Without
    this guard the new process fails to bind, exits with status=1, and systemd
    restarts it in an infinite loop (observed: 2075+ restarts before manual
    intervention).

    Uses ``lsof -ti :<port>`` to find occupants, sends SIGTERM, then waits 1 s
    for the port to free.  Silently swallows all errors so that a missing ``lsof``
    or a permission error never prevents the server from starting.
    """
    import signal
    import time

    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            my_pid = os.getpid()
            for pid_str in result.stdout.strip().split("\n"):
                try:
                    pid = int(pid_str.strip())
                    if pid != my_pid:
                        os.kill(pid, signal.SIGTERM)
                except (ValueError, ProcessLookupError, PermissionError):
                    pass
            time.sleep(1)  # Brief wait for the port to be released
    except Exception:
        pass  # lsof not available or other error — proceed; uvicorn will fail naturally


def serve(
    host: str | None = None,
    port: int | None = None,
    auth: str | None = None,
    session_ttl: int | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
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
    tls_cert = tls_cert if tls_cert is not None else settings.get("tls_cert", "")
    tls_key = tls_key if tls_key is not None else settings.get("tls_key", "")

    os.environ["MUXPLEX_PORT"] = str(port)
    os.environ["MUXPLEX_AUTH"] = auth
    os.environ["MUXPLEX_SESSION_TTL"] = str(session_ttl)

    # Prevent crash-loop on restart: kill any stale process holding the port
    _kill_stale_port_holder(port)

    from muxplex.main import app  # noqa: PLC0415

    # Resolve SSL configuration
    ssl_kwargs: dict = {}
    if tls_cert and tls_key:
        cert_path = Path(tls_cert)
        key_path = Path(tls_key)
        missing = [str(p) for p in (cert_path, key_path) if not p.exists()]
        if missing:
            print(f"  TLS {', '.join(missing)} not found, falling back to HTTP")
        else:
            ssl_kwargs = {"ssl_certfile": tls_cert, "ssl_keyfile": tls_key}

    scheme = "https" if ssl_kwargs else "http"
    print(f"  muxplex → {scheme}://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)


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

    # Serve config
    from muxplex.settings import load_settings  # noqa: PLC0415

    cfg = load_settings()
    print(
        f"  {ok_mark} Serve config: {cfg['host']}:{cfg['port']}"
        f" (auth={cfg['auth']}, ttl={cfg['session_ttl']}s)"
    )

    # TLS status
    tls_cert = cfg.get("tls_cert", "")
    tls_key = cfg.get("tls_key", "")
    if tls_cert and tls_key:
        from datetime import datetime, timezone  # noqa: PLC0415

        from muxplex.tls import get_cert_info  # noqa: PLC0415

        cert_info = get_cert_info(tls_cert)
        if cert_info is not None:
            expires = cert_info["expires"]
            # Ensure timezone-aware for comparison
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if expires < now:
                days_ago = (now - expires).days
                print(
                    f"  {warn_mark} TLS: WARNING \u2014 cert expired {days_ago} days ago."
                    " Run muxplex setup-tls to renew"
                )
            else:
                expiry_str = expires.strftime("%Y-%m-%d")
                print(f"  {ok_mark} TLS: enabled (cert expires {expiry_str})")
        else:
            print(f"  {warn_mark} TLS: configured but cert not readable ({tls_cert})")
    else:
        # Only show TLS warning if host is not localhost
        host = cfg.get("host", "127.0.0.1")
        if host != "127.0.0.1":
            # Network host without TLS: show nudge
            print(
                f"  {warn_mark} TLS: disabled — clipboard won't work on remote devices"
            )
            print("    Run: muxplex setup-tls")

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
            if _have_launchctl():
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
                    f"  {warn_mark} Service: launchctl not found — cannot check status"
                )
        else:
            print(
                f"  {warn_mark} Service: not installed (run: muxplex service install)"
            )
    else:
        if not _have_systemctl():
            print(f"  {warn_mark} Service: systemd not available on this platform")
        else:
            systemd_user = (
                Path.home() / ".config" / "systemd" / "user" / "muxplex.service"
            )
            if systemd_user.exists():
                print(
                    f"  {ok_mark} Service: systemd user unit installed ({systemd_user})"
                )
            elif _system_service_path.exists():
                print(
                    f"  {ok_mark} Service: systemd system unit installed ({_system_service_path})"
                )
            else:
                print(
                    f"  {warn_mark} Service: not installed (run: muxplex service install)"
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

    # Bug 3: Detect whether this install is managed by uv tool.
    # If the resolved muxplex binary lives inside the uv tools directory we
    # always use `uv tool install --reinstall --force muxplex` regardless of
    # the PEP-610 source tag so that the correct tool-environment is upgraded.
    _uv_tools_dir = str(Path.home() / ".local" / "share" / "uv" / "tools")
    _muxplex_script = shutil.which("muxplex")
    _is_uv_managed = False
    if _muxplex_script:
        try:
            if _uv_tools_dir in str(Path(_muxplex_script).resolve()):
                _is_uv_managed = True
        except Exception:
            pass

    # install_target: pypi / uv-tool-managed → package name; git → git URL
    install_target = (
        "muxplex"
        if info["source"] == "pypi" or _is_uv_managed
        else "git+https://github.com/bkrabach/muxplex"
    )
    uv_path = shutil.which("uv")

    # Pre-compute macOS service identifiers — used in both stop and finally blocks.
    label = "com.muxplex"
    uid = os.getuid()
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    # 1. Stop service
    if sys.platform == "darwin":
        # Bug 2a: guard every launchctl call with _have_launchctl()
        if not _have_launchctl():
            print("  ! launchctl not found — skipping service management step")
        elif plist.exists():
            print("  Stopping launchd service...")
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True
            )
        else:
            print("  No launchd service found (skipping stop)")
    else:
        # Linux/WSL — check systemd
        if not _have_systemctl():
            print("  ! systemctl not found — skipping service management step")
        else:
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

    # 2+4. Install (try) with guaranteed service restart in finally.
    # Bug 1+2b: try/finally ensures the start step always runs — success OR failure.
    _install_failed = False
    print("  Installing latest version...")
    try:
        # Bug 3: dispatch — uv-tool-managed gets --reinstall; plain uv/pip otherwise
        if uv_path:
            if _is_uv_managed:
                # uv-tool install: always reinstall the package by name
                install_cmd = [
                    uv_path,
                    "tool",
                    "install",
                    "--reinstall",
                    "--force",
                    "muxplex",
                ]
            else:
                install_cmd = [uv_path, "tool", "install", install_target, "--force"]
            result = subprocess.run(install_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  ERROR: uv tool install failed:\n{result.stderr}")
                _install_failed = True
            else:
                print("  Installed successfully")
        else:
            # Bug 3: uv absent → fall back to pip
            pip_path = shutil.which("pip") or shutil.which("pip3")
            if pip_path:
                result = subprocess.run(
                    [pip_path, "install", "--upgrade", install_target],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    print(f"  ERROR: pip install failed:\n{result.stderr}")
                    _install_failed = True
                else:
                    print("  Installed successfully")
            else:
                print("  ERROR: neither uv nor pip found — cannot upgrade")
                _install_failed = True

        if not _install_failed:
            # 3. Regenerate service file (picks up any plist/unit changes)
            if sys.platform == "darwin" or _have_systemctl():
                print("  Regenerating service file...")
                from muxplex.service import service_install  # noqa: PLC0415

                service_install()
            else:
                print("  ! systemctl not found — skipping service file regeneration")
    finally:
        # 4. Restart service — ALWAYS runs after install, even on failure (best-effort).
        if sys.platform == "darwin":
            # Bug 2a: guard launchctl
            if _have_launchctl():
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
                        subprocess.run(
                            ["launchctl", "load", str(plist)], capture_output=True
                        )
                        print("  Service started (legacy)")
                else:
                    print("  Service file not found — run: muxplex service install")
        elif _have_systemctl():
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
                print("  Service not enabled — run: muxplex service install")
        else:
            # No service manager — ask the user to restart manually
            pid_hint = ""
            try:
                pid_result = subprocess.run(
                    ["pgrep", "-f", "muxplex serve"],
                    capture_output=True,
                    text=True,
                )
                pid_str = pid_result.stdout.strip()
                if pid_str:
                    pid_hint = f" (running PID: {pid_str})"
            except Exception:
                pass
            print(
                "  ! systemd not detected — restart muxplex manually to pick up the new version"
                + pid_hint
            )

    # Bug 1: propagate install failure as a non-zero exit so callers / scripts
    # can detect the partial upgrade.  Service restart was attempted above (best-effort).
    if _install_failed:
        print(
            "\n  ERROR: upgrade failed — muxplex service has been restarted (best-effort).\n"
        )
        sys.exit(1)

    # 5. Doctor check
    print("\n  Verifying...")
    doctor()


def config_list() -> None:
    """Show all settings with current values."""
    from muxplex.settings import DEFAULT_SETTINGS, SETTINGS_PATH, load_settings  # noqa: PLC0415

    settings = load_settings()
    print(f"\nmuxplex config ({SETTINGS_PATH})\n")

    for key in DEFAULT_SETTINGS:
        value = settings.get(key)
        default = DEFAULT_SETTINGS[key]
        is_default = value == default
        marker = "" if is_default else " (modified)"
        if isinstance(value, str):
            display = f'"{value}"'
        elif value is None:
            display = "null"
        elif isinstance(value, bool):
            display = "true" if value else "false"
        elif isinstance(value, list):
            display = str(value) if value else "[]"
        else:
            display = str(value)
        print(f"  {key}: {display}{marker}")
    print()


def config_get(key: str) -> None:
    """Show one setting value."""
    from muxplex.settings import DEFAULT_SETTINGS, load_settings  # noqa: PLC0415

    if key not in DEFAULT_SETTINGS:
        print(f"Unknown setting: {key}", file=sys.stderr)
        print(
            f"Valid keys: {', '.join(sorted(DEFAULT_SETTINGS.keys()))}", file=sys.stderr
        )
        sys.exit(1)

    settings = load_settings()
    value = settings.get(key)
    if isinstance(value, str):
        print(value)
    elif value is None:
        print("null")
    elif isinstance(value, bool):
        print("true" if value else "false")
    else:
        print(value)


def config_set(key: str, raw_value: str) -> None:
    """Set a setting value. Auto-detects type from the default."""
    import json  # noqa: PLC0415

    from muxplex.settings import DEFAULT_SETTINGS, patch_settings  # noqa: PLC0415

    if key not in DEFAULT_SETTINGS:
        print(f"Unknown setting: {key}", file=sys.stderr)
        print(
            f"Valid keys: {', '.join(sorted(DEFAULT_SETTINGS.keys()))}", file=sys.stderr
        )
        sys.exit(1)

    default = DEFAULT_SETTINGS[key]

    try:
        if isinstance(default, bool):
            value: object = raw_value.lower() in ("true", "1", "yes", "on")
        elif isinstance(default, int):
            value = int(raw_value)
        elif default is None:
            value = None if raw_value.lower() in ("null", "none", "") else raw_value
        elif isinstance(default, list):
            value = json.loads(raw_value) if raw_value else []
        else:
            value = raw_value
    except (ValueError, json.JSONDecodeError) as e:
        print(f"Invalid value for {key}: {e}", file=sys.stderr)
        sys.exit(1)

    patch_settings({key: value})
    print(f"  {key}: {value}")


def config_reset(key: str | None = None) -> None:
    """Reset one or all settings to defaults."""
    import copy  # noqa: PLC0415

    from muxplex.settings import (  # noqa: PLC0415
        DEFAULT_SETTINGS,
        SETTINGS_PATH,
        patch_settings,
        save_settings,
    )

    if key is not None:
        if key not in DEFAULT_SETTINGS:
            print(f"Unknown setting: {key}", file=sys.stderr)
            print(
                f"Valid keys: {', '.join(sorted(DEFAULT_SETTINGS.keys()))}",
                file=sys.stderr,
            )
            sys.exit(1)
        patch_settings({key: DEFAULT_SETTINGS[key]})
        print(f"  {key} reset to: {DEFAULT_SETTINGS[key]}")
    else:
        save_settings(copy.deepcopy(DEFAULT_SETTINGS))
        print(f"  All settings reset to defaults ({SETTINGS_PATH})")


def setup_tls(method: str = "auto") -> None:
    """Generate TLS certificates and update settings.

    Auto-detection chain (method='auto'): Tailscale → mkcert → self-signed.
    Use --method to force a specific certificate source.
    """
    from muxplex.settings import SETTINGS_PATH, load_settings, patch_settings  # noqa: PLC0415
    from muxplex.tls import (  # noqa: PLC0415
        _default_hostnames,
        _default_lan_ip,
        _default_tailnet_name,
        detect_mkcert,
        detect_tailscale,
        generate_leaf_signed_by_ca,
        generate_local_ca,
        generate_mkcert,
        generate_self_signed,
        generate_tailscale,
        get_cert_info,
    )

    config_dir = SETTINGS_PATH.parent
    cert_path = config_dir / "muxplex.crt"
    key_path = config_dir / "muxplex.key"

    # Check for existing certificates and prompt before overwriting
    _settings = load_settings()
    _existing_cert = _settings.get("tls_cert", "")
    _existing_key = _settings.get("tls_key", "")
    if _existing_cert and _existing_key and Path(_existing_cert).exists():
        _info = get_cert_info(_existing_cert)
        if _info is not None:
            print(f"TLS already configured (expires {str(_info['expires'])[:10]}).")
        try:
            _answer = input("Regenerate? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            _answer = "n"
        if _answer.lower() not in ("y", "yes"):
            print("Keeping existing certificates.")
            return

    result = None
    tailscale_info = None

    # Step 1: Try Tailscale
    if method in ("auto", "tailscale"):
        tailscale_info = detect_tailscale()
        if tailscale_info:
            hostname = tailscale_info["hostname"]
            print(f"  Detected Tailscale: {hostname}")
            result = generate_tailscale(cert_path, key_path, hostname)
            if result:
                print("  Tailscale certificate obtained")
            else:
                print("  Tailscale certificate generation failed")
        if method == "tailscale" and result is None:
            print(
                "Error: Tailscale not available or certificate generation failed",
                file=sys.stderr,
            )
            sys.exit(1)

    # Step 2: Try mkcert
    if result is None and method in ("auto", "mkcert"):
        if detect_mkcert():
            print("  Detected mkcert, generating certificate...")
            extra_hostnames = None
            if tailscale_info:
                extra_hostnames = tailscale_info.get("cert_domains") or None
            result = generate_mkcert(
                cert_path, key_path, extra_hostnames=extra_hostnames
            )
        else:
            if method == "mkcert":
                print(
                    "Error: mkcert not found. Install from https://mkcert.dev",
                    file=sys.stderr,
                )
                sys.exit(1)

    # Step 3: Try self-signed
    if result is None and method in ("auto", "selfsigned"):
        result = generate_self_signed(cert_path, key_path)

    # Step 3.5: Try local CA (explicit opt-in only — not part of "auto").
    # Generates a persistent local CA in ~/.config/muxplex/ca/ and signs
    # a short-lived leaf with it. Install the CA on each client to get
    # browser-trusted HTTPS without Tailscale or a public domain.
    if result is None and method == "ca":
        ca_dir = config_dir / "ca"
        ca_cert_path = ca_dir / "muxplex-ca.crt"
        ca_key_path = ca_dir / "muxplex-ca.key"

        ca_info = generate_local_ca(ca_cert_path, ca_key_path)
        if ca_info["regenerated"]:
            print(f"  Generated local CA at {ca_cert_path}")
        else:
            print(f"  Reusing existing local CA at {ca_cert_path}")

        # Build the SAN list: defaults + tailnet name (if reachable) + LAN IP.
        leaf_hostnames: list[str] = list(_default_hostnames())
        tailnet_name = _default_tailnet_name()
        if tailnet_name and tailnet_name not in leaf_hostnames:
            leaf_hostnames.append(tailnet_name)

        leaf_ips: list[str] = ["127.0.0.1", "::1"]
        lan_ip = _default_lan_ip()
        if lan_ip and lan_ip not in leaf_ips:
            leaf_ips.append(lan_ip)

        result = generate_leaf_signed_by_ca(
            ca_cert_path,
            ca_key_path,
            cert_path,
            key_path,
            hostnames=leaf_hostnames,
            ip_addresses=leaf_ips,
        )
        # Decorate the result with the CA path so the success block can
        # surface install instructions.
        if result:
            result["ca_cert_path"] = str(ca_cert_path)
            result["ca_regenerated"] = ca_info["regenerated"]

    # Step 4: Final failure check
    if result is None:
        print(
            "Error: TLS certificate generation failed with all methods",
            file=sys.stderr,
        )
        sys.exit(1)

    # Update settings with cert/key paths
    patch_settings({"tls_cert": str(cert_path), "tls_key": str(key_path)})

    # Print cert info
    hostnames_str = ", ".join(result["hostnames"])
    expiry_str = (
        result["expires"].strftime("%Y-%m-%d")
        if hasattr(result["expires"], "strftime")
        else str(result["expires"])
    )

    print("TLS setup complete")
    print(f"  Certificate: {result['cert_path']}")
    print(f"  Key:         {result['key_path']}")
    print(f"  Hostnames:   {hostnames_str}")
    print(f"  Expires:     {expiry_str}")
    print()

    # Method-specific warnings
    method_used = result.get("method", "")
    if method_used == "selfsigned":
        print(
            "  Note: Browsers will show a security warning for self-signed certificates."
        )
        print("  Consider using mkcert or Tailscale for a trusted certificate.")
        print()
    elif method_used == "tailscale":
        print("  Note: Tailscale certificates expire after 90 days.")
        print("  Run 'muxplex setup-tls' to renew.")
        print()
    elif method_used == "ca":
        ca_cert_path_str = result.get("ca_cert_path", "")
        print(f"  Local CA:    {ca_cert_path_str}")
        print()
        print("  Install the CA on each client to eliminate browser warnings.")
        print("  The leaf rotates without re-trusting; the CA is what you trust.")
        print()
        print("  Windows (PowerShell, no admin needed):")
        print(
            "    Import-Certificate -FilePath <path-to-ca.crt> "
            "-CertStoreLocation Cert:\\CurrentUser\\Root"
        )
        print()
        print("  macOS:")
        print(
            "    sudo security add-trusted-cert -d -r trustRoot "
            "-k /Library/Keychains/System.keychain <path-to-ca.crt>"
        )
        print()
        print("  Linux (system-wide):")
        print("    sudo cp <path-to-ca.crt> /usr/local/share/ca-certificates/")
        print("    sudo update-ca-certificates")
        print()
        print("  Leaf cert rotates yearly — re-run 'muxplex setup-tls --method ca'")
        print("  to generate a fresh leaf signed by the same CA (no client re-trust).")
        print()

    print("  Restart service to apply: muxplex service restart")


def setup_tls_status() -> None:
    """Display the current TLS configuration status."""
    from muxplex.settings import load_settings  # noqa: PLC0415
    from muxplex.tls import get_cert_info  # noqa: PLC0415

    settings = load_settings()
    tls_cert = settings.get("tls_cert", "")
    tls_key = settings.get("tls_key", "")

    print("muxplex TLS status")
    print()

    if not tls_cert or not tls_key:
        print("  TLS: not configured")
        print("  Run: muxplex setup-tls")
        return

    print(f"  Certificate: {tls_cert}")
    print(f"  Key:         {tls_key}")

    cert_info = get_cert_info(tls_cert)
    if cert_info is None:
        print("  Status: configured but cert not readable")
        return

    hostnames_str = ", ".join(cert_info["hostnames"])
    expires = cert_info["expires"]
    expiry_str = (
        expires.strftime("%Y-%m-%d") if hasattr(expires, "strftime") else str(expires)
    )
    print(f"  Hostnames:   {hostnames_str}")
    print(f"  Expires:     {expiry_str}")
    print("  Status: enabled")


def _add_serve_flags(parser: argparse.ArgumentParser) -> None:
    """Add --host, --port, --auth, --session-ttl, --tls-cert, --tls-key flags to a parser.

    All default to None so serve() can distinguish 'not passed' from
    'passed the default value'.
    """
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host (default: from settings.json, then 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port (default: from settings.json, then 8088)",
    )
    parser.add_argument(
        "--auth",
        choices=["pam", "password"],
        default=None,
        help="Auth method: pam or password (default: from settings.json, then pam)",
    )
    parser.add_argument(
        "--session-ttl",
        type=int,
        default=None,
        dest="session_ttl",
        help="Session TTL in seconds (default: from settings.json, then 604800; 0 = browser session)",
    )
    parser.add_argument(
        "--tls-cert",
        default=None,
        dest="tls_cert",
        help="Path to TLS certificate file (default: from settings.json)",
    )
    parser.add_argument(
        "--tls-key",
        default=None,
        dest="tls_key",
        help="Path to TLS private key file (default: from settings.json)",
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="muxplex",
        description="muxplex — web-based tmux session dashboard",
    )
    _add_serve_flags(parser)

    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", help="Start the server (default)")
    _add_serve_flags(serve_parser)

    service_parser = sub.add_parser(
        "service", help="Manage the muxplex background service"
    )
    service_sub = service_parser.add_subparsers(dest="service_command")
    service_sub.add_parser("install", help="Install + enable + start the service")
    service_sub.add_parser("uninstall", help="Stop + disable + remove the service")
    service_sub.add_parser("start", help="Start the service")
    service_sub.add_parser("stop", help="Stop the service")
    service_sub.add_parser("restart", help="Stop + start the service")
    service_sub.add_parser("status", help="Show service status")
    service_sub.add_parser("logs", help="Tail service logs")

    sub.add_parser("show-password", help="Show the current muxplex password")

    sub.add_parser(
        "reset-secret", help="Regenerate signing secret (invalidates sessions)"
    )

    sub.add_parser(
        "reset-device-id",
        help="Regenerate device identity UUID (orphans existing session keys)",
    )

    sub.add_parser(
        "generate-federation-key",
        help="Generate a random federation key and write it to disk",
    )

    sub.add_parser("doctor", help="Check dependencies and system status")

    upgrade_parser = sub.add_parser(
        "upgrade",
        aliases=["update"],
        help="Upgrade muxplex to latest version and restart service",
    )
    upgrade_parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinstall even if already up to date",
    )

    setup_tls_parser = sub.add_parser(
        "setup-tls", help="Generate TLS certificate and configure HTTPS"
    )
    setup_tls_parser.add_argument(
        "--method",
        choices=["auto", "tailscale", "mkcert", "selfsigned", "ca"],
        default="auto",
        help="Certificate generation method (default: auto). 'ca' creates "
        "a persistent local CA in ~/.config/muxplex/ca/ and signs a leaf "
        "cert with it — install the CA on each client to eliminate browser "
        "warnings without requiring Tailscale or a public domain.",
    )
    setup_tls_parser.add_argument(
        "--status",
        action="store_true",
        help="Show current TLS configuration status",
    )

    config_parser = sub.add_parser("config", help="View and manage settings")
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_sub.add_parser("list", help="Show all settings (default)")
    config_get_parser = config_sub.add_parser("get", help="Show one setting")
    config_get_parser.add_argument("key", help="Setting key")
    config_set_parser = config_sub.add_parser("set", help="Set a setting value")
    config_set_parser.add_argument("key", help="Setting key")
    config_set_parser.add_argument("value", help="New value")
    config_reset_parser = config_sub.add_parser("reset", help="Reset to defaults")
    config_reset_parser.add_argument(
        "key", nargs="?", help="Setting key (omit to reset all)"
    )

    args = parser.parse_args()

    if args.command == "show-password":
        show_password()
    elif args.command == "reset-secret":
        reset_secret()
    elif args.command == "reset-device-id":
        reset_device_id_command()
    elif args.command == "generate-federation-key":
        generate_federation_key()
    elif args.command == "doctor":
        doctor()
    elif args.command in ("upgrade", "update"):
        upgrade(force=getattr(args, "force", False))
    elif args.command == "config":
        cmd = getattr(args, "config_command", None)
        if cmd == "get":
            config_get(args.key)
        elif cmd == "set":
            config_set(args.key, args.value)
        elif cmd == "reset":
            config_reset(getattr(args, "key", None))
        else:
            # Default: list (no subcommand or explicit "list")
            config_list()
    elif args.command == "setup-tls":
        if args.status:
            setup_tls_status()
        else:
            setup_tls(method=args.method)
    elif args.command == "service":
        from muxplex.service import (  # noqa: PLC0415
            service_install,
            service_logs,
            service_restart,
            service_start,
            service_status,
            service_stop,
            service_uninstall,
        )

        cmd = getattr(args, "service_command", None)
        if cmd == "install":
            service_install()
        elif cmd == "uninstall":
            service_uninstall()
        elif cmd == "start":
            service_start()
        elif cmd == "stop":
            service_stop()
        elif cmd == "restart":
            service_restart()
        elif cmd == "status":
            service_status()
        elif cmd == "logs":
            service_logs()
        else:
            service_parser.print_help()
    else:
        _check_dependencies()
        serve(
            host=args.host,
            port=args.port,
            auth=args.auth,
            session_ttl=args.session_ttl,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
        )

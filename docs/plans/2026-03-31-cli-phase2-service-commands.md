# CLI Refactor Phase 2: Service Subcommand Group

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Phase:** 2 of 2. Complete [Phase 1](./2026-03-31-cli-phase1-config-serve.md) before starting this phase.
**Design doc:** [`docs/plans/2026-03-31-cli-service-refactor-design.md`](./2026-03-31-cli-service-refactor-design.md)

**Goal:** Replace `muxplex install-service` with a full `muxplex service <command>` subcommand group (`install`, `uninstall`, `start`, `stop`, `restart`, `status`, `logs`) — thin wrappers over `systemctl --user` (Linux) and `launchctl` (macOS).

**Architecture:** Create a new `muxplex/service.py` module containing platform-dispatched functions for each service operation. The existing `_install_systemd()` and `_install_launchd()` functions in `cli.py` are moved and simplified (no CLI flags in service files — they just run `muxplex serve`, which reads `settings.json`). A new `service` subparser in `main()` dispatches to these functions. The old `install-service` subparser remains as a deprecated alias.

**Tech Stack:** Python 3.11+, argparse, subprocess, pytest, monkeypatch/capsys

**Working directory:** `/home/bkrabach/dev/web-tmux/muxplex/`

**Prerequisite:** Phase 1 must be complete — `settings.json` must contain `host`, `port`, `auth`, `session_ttl` keys and `serve()` must read from settings.json. Service files generated in this phase rely on `muxplex serve` reading config from disk (no flags in ExecStart/ProgramArguments).

---

### Task 1: Create the service module with platform detection

**Files:**
- Create: `muxplex/service.py`
- Test: `muxplex/tests/test_service.py`

**Step 1: Write the failing tests**

Create `muxplex/tests/test_service.py`:

```python
"""Tests for muxplex/service.py — service lifecycle management."""

import sys
from pathlib import Path
from unittest.mock import patch


def test_service_module_importable():
    """muxplex.service must be importable."""
    from muxplex.service import service_install  # noqa: F401
    from muxplex.service import service_uninstall  # noqa: F401
    from muxplex.service import service_start  # noqa: F401
    from muxplex.service import service_stop  # noqa: F401
    from muxplex.service import service_restart  # noqa: F401
    from muxplex.service import service_status  # noqa: F401
    from muxplex.service import service_logs  # noqa: F401


def test_is_darwin_detection(monkeypatch):
    """_is_darwin() returns True on macOS, False on Linux."""
    from muxplex import service as svc_mod

    monkeypatch.setattr(sys, "platform", "darwin")
    assert svc_mod._is_darwin() is True

    monkeypatch.setattr(sys, "platform", "linux")
    assert svc_mod._is_darwin() is False


def test_resolve_muxplex_bin():
    """_resolve_muxplex_bin() returns a path that includes 'muxplex'."""
    from muxplex.service import _resolve_muxplex_bin

    result = _resolve_muxplex_bin()
    assert "muxplex" in result.lower() or "python" in result.lower()
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_service.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'muxplex.service'`

**Step 3: Create the service module skeleton**

Create `muxplex/service.py`:

```python
"""Service lifecycle management for muxplex.

Thin wrappers over systemctl (Linux) and launchctl (macOS).
Each function is 3-10 lines — no abstraction layer, just direct subprocess calls.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SYSTEMD_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
_SYSTEMD_UNIT_PATH = _SYSTEMD_UNIT_DIR / "muxplex.service"

_LAUNCHD_PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
_LAUNCHD_PLIST_PATH = _LAUNCHD_PLIST_DIR / "com.muxplex.plist"
_LAUNCHD_LABEL = "com.muxplex"


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _is_darwin() -> bool:
    """Return True if running on macOS."""
    return sys.platform == "darwin"


def _resolve_muxplex_bin() -> str:
    """Find the muxplex executable path.

    Prefers `shutil.which('muxplex')`, falls back to `sys.executable -m muxplex`.
    """
    found = shutil.which("muxplex")
    if found:
        return found
    return f"{sys.executable} -m muxplex"


# ---------------------------------------------------------------------------
# Public API — each dispatches to platform-specific implementation
# ---------------------------------------------------------------------------


def service_install() -> None:
    """Write service file, enable, and start the service."""
    if _is_darwin():
        _launchd_install()
    else:
        _systemd_install()


def service_uninstall() -> None:
    """Stop, disable, and remove the service file."""
    if _is_darwin():
        _launchd_uninstall()
    else:
        _systemd_uninstall()


def service_start() -> None:
    """Start the service."""
    if _is_darwin():
        _launchd_start()
    else:
        _systemd_start()


def service_stop() -> None:
    """Stop the service."""
    if _is_darwin():
        _launchd_stop()
    else:
        _systemd_stop()


def service_restart() -> None:
    """Stop and start the service."""
    if _is_darwin():
        _launchd_restart()
    else:
        _systemd_restart()


def service_status() -> None:
    """Show service status."""
    if _is_darwin():
        _launchd_status()
    else:
        _systemd_status()


def service_logs() -> None:
    """Tail service logs."""
    if _is_darwin():
        _launchd_logs()
    else:
        _systemd_logs()


# ---------------------------------------------------------------------------
# systemd implementations
# ---------------------------------------------------------------------------
# (filled in Task 2)


def _systemd_install() -> None:
    raise NotImplementedError


def _systemd_uninstall() -> None:
    raise NotImplementedError


def _systemd_start() -> None:
    raise NotImplementedError


def _systemd_stop() -> None:
    raise NotImplementedError


def _systemd_restart() -> None:
    raise NotImplementedError


def _systemd_status() -> None:
    raise NotImplementedError


def _systemd_logs() -> None:
    raise NotImplementedError


# ---------------------------------------------------------------------------
# launchd implementations
# ---------------------------------------------------------------------------
# (filled in Task 3)


def _launchd_install() -> None:
    raise NotImplementedError


def _launchd_uninstall() -> None:
    raise NotImplementedError


def _launchd_start() -> None:
    raise NotImplementedError


def _launchd_stop() -> None:
    raise NotImplementedError


def _launchd_restart() -> None:
    raise NotImplementedError


def _launchd_status() -> None:
    raise NotImplementedError


def _launchd_logs() -> None:
    raise NotImplementedError
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_service.py -v`

Expected: ALL PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/service.py muxplex/tests/test_service.py && git commit -m "feat: create service.py module skeleton with platform dispatch"
```

---

### Task 2: Implement systemd service commands

**Files:**
- Modify: `muxplex/service.py` (replace systemd stubs with real implementations)
- Test: `muxplex/tests/test_service.py`

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_service.py`:

```python
# ---------------------------------------------------------------------------
# systemd implementations (Phase 2, Task 2)
# ---------------------------------------------------------------------------


def test_systemd_install_writes_unit_and_enables(tmp_path, monkeypatch):
    """_systemd_install() writes unit file, runs daemon-reload, and enable --now."""
    import muxplex.service as svc_mod

    unit_dir = tmp_path / "systemd" / "user"
    unit_path = unit_dir / "muxplex.service"
    monkeypatch.setattr(svc_mod, "_SYSTEMD_UNIT_DIR", unit_dir)
    monkeypatch.setattr(svc_mod, "_SYSTEMD_UNIT_PATH", unit_path)
    monkeypatch.setattr(svc_mod, "_is_darwin", lambda: False)

    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: calls.append(cmd)
    )

    svc_mod._systemd_install()

    assert unit_path.exists()
    content = unit_path.read_text()
    assert "muxplex" in content
    assert "serve" in content
    # Must NOT contain --host, --port, or other flags
    assert "--host" not in content
    assert "--port" not in content

    # Verify subprocess calls
    assert any("daemon-reload" in str(c) for c in calls)
    assert any("enable" in str(c) for c in calls)


def test_systemd_uninstall_stops_disables_removes(tmp_path, monkeypatch):
    """_systemd_uninstall() stops, disables, removes unit, and reloads daemon."""
    import muxplex.service as svc_mod

    unit_path = tmp_path / "muxplex.service"
    unit_path.write_text("[Unit]\nDescription=test\n")
    monkeypatch.setattr(svc_mod, "_SYSTEMD_UNIT_PATH", unit_path)

    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: calls.append(cmd)
    )

    svc_mod._systemd_uninstall()

    assert not unit_path.exists()
    assert any("stop" in str(c) for c in calls)
    assert any("disable" in str(c) for c in calls)
    assert any("daemon-reload" in str(c) for c in calls)


def test_systemd_start_calls_systemctl(monkeypatch):
    """_systemd_start() calls systemctl --user start muxplex."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    from muxplex.service import _systemd_start
    _systemd_start()

    assert len(calls) == 1
    assert calls[0] == ["systemctl", "--user", "start", "muxplex"]


def test_systemd_stop_calls_systemctl(monkeypatch):
    """_systemd_stop() calls systemctl --user stop muxplex."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    from muxplex.service import _systemd_stop
    _systemd_stop()

    assert len(calls) == 1
    assert calls[0] == ["systemctl", "--user", "stop", "muxplex"]


def test_systemd_restart_calls_systemctl(monkeypatch):
    """_systemd_restart() calls systemctl --user restart muxplex."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    from muxplex.service import _systemd_restart
    _systemd_restart()

    assert len(calls) == 1
    assert calls[0] == ["systemctl", "--user", "restart", "muxplex"]


def test_systemd_status_calls_systemctl(monkeypatch):
    """_systemd_status() calls systemctl --user status muxplex --no-pager."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    from muxplex.service import _systemd_status
    _systemd_status()

    assert len(calls) == 1
    assert calls[0] == ["systemctl", "--user", "status", "muxplex", "--no-pager"]


def test_systemd_logs_calls_journalctl(monkeypatch):
    """_systemd_logs() calls journalctl --user -u muxplex -f."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    from muxplex.service import _systemd_logs
    _systemd_logs()

    assert len(calls) == 1
    assert calls[0] == ["journalctl", "--user", "-u", "muxplex", "-f"]
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_service.py -v -k "systemd" --no-header 2>&1 | tail -20`

Expected: FAIL — `NotImplementedError`

**Step 3: Implement systemd functions**

In `muxplex/service.py`, replace the systemd stubs with:

```python
# ---------------------------------------------------------------------------
# systemd implementations
# ---------------------------------------------------------------------------

_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=muxplex — web-based tmux session dashboard
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


def _systemd_install() -> None:
    muxplex_bin = _resolve_muxplex_bin()
    if " " in muxplex_bin:
        # sys.executable -m muxplex — split into command
        exec_start = f"{muxplex_bin} serve"
    else:
        exec_start = f"{muxplex_bin} serve"

    safe_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    unit_content = _SYSTEMD_UNIT_TEMPLATE.format(
        exec_start=exec_start, safe_path=safe_path
    )

    _SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    _SYSTEMD_UNIT_PATH.write_text(unit_content)
    print(f"  Wrote {_SYSTEMD_UNIT_PATH}")

    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "--now", "muxplex"])
    print("  Service installed and started.")

    _prompt_host_if_localhost()


def _systemd_uninstall() -> None:
    subprocess.run(["systemctl", "--user", "stop", "muxplex"])
    subprocess.run(["systemctl", "--user", "disable", "muxplex"])
    _SYSTEMD_UNIT_PATH.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    print("  Service stopped, disabled, and removed.")


def _systemd_start() -> None:
    subprocess.run(["systemctl", "--user", "start", "muxplex"])


def _systemd_stop() -> None:
    subprocess.run(["systemctl", "--user", "stop", "muxplex"])


def _systemd_restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", "muxplex"])


def _systemd_status() -> None:
    subprocess.run(["systemctl", "--user", "status", "muxplex", "--no-pager"])


def _systemd_logs() -> None:
    subprocess.run(["journalctl", "--user", "-u", "muxplex", "-f"])
```

Also add the host prompt helper function (used by both platforms):

```python
def _prompt_host_if_localhost() -> None:
    """If host is 127.0.0.1, prompt user to set to 0.0.0.0 for network access."""
    from muxplex.settings import load_settings, patch_settings  # noqa: PLC0415

    settings = load_settings()
    if settings.get("host") == "127.0.0.1":
        print(
            "\n  Note: host is 127.0.0.1 (localhost only)."
            " Set to 0.0.0.0 for network access? [Y/n] ",
            end="",
        )
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("", "y", "yes"):
            patch_settings({"host": "0.0.0.0"})
            print("  → Settings updated: host = 0.0.0.0")
            print("  → Restart the service to apply: muxplex service restart")
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_service.py -v -k "systemd" --no-header 2>&1 | tail -20`

Expected: ALL PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/service.py muxplex/tests/test_service.py && git commit -m "feat: implement systemd service commands (install, uninstall, start, stop, restart, status, logs)"
```

---

### Task 3: Implement launchd service commands

**Files:**
- Modify: `muxplex/service.py` (replace launchd stubs)
- Test: `muxplex/tests/test_service.py`

**Step 1: Write the failing tests**

Add to `muxplex/tests/test_service.py`:

```python
# ---------------------------------------------------------------------------
# launchd implementations (Phase 2, Task 3)
# ---------------------------------------------------------------------------


def test_launchd_install_writes_plist_and_bootstraps(tmp_path, monkeypatch):
    """_launchd_install() writes plist and calls launchctl bootstrap."""
    import muxplex.service as svc_mod

    plist_dir = tmp_path / "LaunchAgents"
    plist_path = plist_dir / "com.muxplex.plist"
    monkeypatch.setattr(svc_mod, "_LAUNCHD_PLIST_DIR", plist_dir)
    monkeypatch.setattr(svc_mod, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(svc_mod, "_is_darwin", lambda: True)
    monkeypatch.setattr(os, "getuid", lambda: 501)
    # Suppress the host prompt
    monkeypatch.setattr("builtins.input", lambda *a: "n")

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    svc_mod._launchd_install()

    assert plist_path.exists()
    content = plist_path.read_text()
    assert "com.muxplex" in content
    assert "serve" in content
    # Must NOT contain --host, --port
    assert "--host" not in content
    assert "--port" not in content

    assert any("bootstrap" in str(c) for c in calls)


def test_launchd_uninstall_bootouts_and_removes(tmp_path, monkeypatch):
    """_launchd_uninstall() calls bootout and removes plist."""
    import muxplex.service as svc_mod

    plist_path = tmp_path / "com.muxplex.plist"
    plist_path.write_text("<plist>test</plist>")
    monkeypatch.setattr(svc_mod, "_LAUNCHD_PLIST_PATH", plist_path)
    monkeypatch.setattr(os, "getuid", lambda: 501)

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    svc_mod._launchd_uninstall()

    assert not plist_path.exists()
    assert any("bootout" in str(c) for c in calls)


def test_launchd_stop_calls_bootout(monkeypatch):
    """_launchd_stop() calls launchctl bootout."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(os, "getuid", lambda: 501)

    from muxplex.service import _launchd_stop
    _launchd_stop()

    assert len(calls) == 1
    assert "bootout" in str(calls[0])


def test_launchd_logs_tails_log_file(monkeypatch):
    """_launchd_logs() calls tail -f /tmp/muxplex.log."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    from muxplex.service import _launchd_logs
    _launchd_logs()

    assert len(calls) == 1
    assert calls[0] == ["tail", "-f", "/tmp/muxplex.log"]
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_service.py -v -k "launchd" --no-header 2>&1 | tail -20`

Expected: FAIL — `NotImplementedError`

**Step 3: Implement launchd functions**

In `muxplex/service.py`, replace the launchd stubs with:

```python
# ---------------------------------------------------------------------------
# launchd implementations
# ---------------------------------------------------------------------------

_LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.muxplex</string>
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


def _launchd_install() -> None:
    muxplex_bin = _resolve_muxplex_bin()
    safe_path = (
        "/opt/homebrew/bin:/usr/local/bin:"
        + os.environ.get("PATH", "/usr/bin:/bin")
    )
    plist_content = _LAUNCHD_PLIST_TEMPLATE.format(
        muxplex_bin=muxplex_bin, safe_path=safe_path
    )

    _LAUNCHD_PLIST_DIR.mkdir(parents=True, exist_ok=True)
    _LAUNCHD_PLIST_PATH.write_text(plist_content)
    print(f"  Wrote {_LAUNCHD_PLIST_PATH}")

    uid = os.getuid()
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(_LAUNCHD_PLIST_PATH)])
    print("  Service installed and started.")

    _prompt_host_if_localhost()


def _launchd_uninstall() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LAUNCHD_LABEL}"])
    _LAUNCHD_PLIST_PATH.unlink(missing_ok=True)
    print("  Service stopped, disabled, and removed.")


def _launchd_start() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(_LAUNCHD_PLIST_PATH)])


def _launchd_stop() -> None:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LAUNCHD_LABEL}"])


def _launchd_restart() -> None:
    _launchd_stop()
    _launchd_start()


def _launchd_status() -> None:
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{_LAUNCHD_LABEL}"],
    )


def _launchd_logs() -> None:
    subprocess.run(["tail", "-f", "/tmp/muxplex.log"])
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_service.py -v --no-header 2>&1 | tail -25`

Expected: ALL PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/service.py muxplex/tests/test_service.py && git commit -m "feat: implement launchd service commands (install, uninstall, start, stop, restart, status, logs)"
```

---

### Task 4: Wire service subparser into main()

**Files:**
- Modify: `muxplex/cli.py` (add `service` subparser with sub-subparsers)
- Test: `muxplex/tests/test_cli.py`

**Step 1: Write the failing tests**

Add at the end of `muxplex/tests/test_cli.py`:

```python
# ---------------------------------------------------------------------------
# Service subcommand group (Phase 2)
# ---------------------------------------------------------------------------


def test_service_install_dispatches(monkeypatch):
    """'muxplex service install' must call service_install()."""
    from muxplex.cli import main

    calls = []
    with patch("muxplex.service.service_install", lambda: calls.append("install")):
        with patch("sys.argv", ["muxplex", "service", "install"]):
            main()

    assert calls == ["install"]


def test_service_uninstall_dispatches(monkeypatch):
    """'muxplex service uninstall' must call service_uninstall()."""
    from muxplex.cli import main

    calls = []
    with patch("muxplex.service.service_uninstall", lambda: calls.append("uninstall")):
        with patch("sys.argv", ["muxplex", "service", "uninstall"]):
            main()

    assert calls == ["uninstall"]


def test_service_start_dispatches(monkeypatch):
    """'muxplex service start' must call service_start()."""
    from muxplex.cli import main

    calls = []
    with patch("muxplex.service.service_start", lambda: calls.append("start")):
        with patch("sys.argv", ["muxplex", "service", "start"]):
            main()

    assert calls == ["start"]


def test_service_stop_dispatches(monkeypatch):
    """'muxplex service stop' must call service_stop()."""
    from muxplex.cli import main

    calls = []
    with patch("muxplex.service.service_stop", lambda: calls.append("stop")):
        with patch("sys.argv", ["muxplex", "service", "stop"]):
            main()

    assert calls == ["stop"]


def test_service_restart_dispatches(monkeypatch):
    """'muxplex service restart' must call service_restart()."""
    from muxplex.cli import main

    calls = []
    with patch("muxplex.service.service_restart", lambda: calls.append("restart")):
        with patch("sys.argv", ["muxplex", "service", "restart"]):
            main()

    assert calls == ["restart"]


def test_service_status_dispatches(monkeypatch):
    """'muxplex service status' must call service_status()."""
    from muxplex.cli import main

    calls = []
    with patch("muxplex.service.service_status", lambda: calls.append("status")):
        with patch("sys.argv", ["muxplex", "service", "status"]):
            main()

    assert calls == ["status"]


def test_service_logs_dispatches(monkeypatch):
    """'muxplex service logs' must call service_logs()."""
    from muxplex.cli import main

    calls = []
    with patch("muxplex.service.service_logs", lambda: calls.append("logs")):
        with patch("sys.argv", ["muxplex", "service", "logs"]):
            main()

    assert calls == ["logs"]


def test_service_subcommand_in_help():
    """'muxplex --help' must list 'service' as a subcommand."""
    import io
    from muxplex.cli import main

    buf = io.StringIO()
    with patch("sys.argv", ["muxplex", "--help"]):
        try:
            with patch("sys.stdout", buf):
                main()
        except SystemExit:
            pass

    assert "service" in buf.getvalue().lower()
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py::test_service_install_dispatches -v`

Expected: FAIL — no `service` subparser exists yet

**Step 3: Add the service subparser to main()**

In `muxplex/cli.py`, in the `main()` function, add the `service` subparser (after the `install-service` parser, before `show-password`):

```python
    # service subcommand group
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
```

Then add the dispatch in the `if/elif` chain in `main()`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py -v -k "service" --no-header 2>&1 | tail -25`

Expected: ALL PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "feat: wire service subcommand group into CLI (install, uninstall, start, stop, restart, status, logs)"
```

---

### Task 5: Update install-service deprecation to forward to service install

**Files:**
- Modify: `muxplex/cli.py` (update `install-service` dispatch to call `service_install()`)

**Step 1: Write the failing test**

Add at the end of `muxplex/tests/test_cli.py`:

```python
def test_install_service_deprecated_calls_service_install(monkeypatch, capsys):
    """'muxplex install-service' must print deprecation AND call service_install()."""
    from muxplex.cli import main

    calls = []
    with patch("muxplex.service.service_install", lambda: calls.append("install")):
        with patch("sys.argv", ["muxplex", "install-service"]):
            main()

    assert calls == ["install"]
    captured = capsys.readouterr()
    assert "deprecated" in captured.err.lower()
```

**Step 2: Run test to verify it fails**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py::test_install_service_deprecated_calls_service_install -v`

Expected: FAIL — currently `install-service` calls the old `install_service()` function, not `service_install()` from the new module

**Step 3: Update the dispatch**

In `muxplex/cli.py`, in `main()`, change the `install-service` dispatch from:

```python
    if args.command == "install-service":
        print(
            "⚠ 'muxplex install-service' is deprecated."
            " Use 'muxplex service install' instead.",
            file=sys.stderr,
        )
        install_service(system=args.system)
```

to:

```python
    if args.command == "install-service":
        print(
            "⚠ 'muxplex install-service' is deprecated."
            " Use 'muxplex service install' instead.",
            file=sys.stderr,
        )
        from muxplex.service import service_install  # noqa: PLC0415

        service_install()
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/test_cli.py::test_install_service_deprecated_calls_service_install -v`

Expected: PASS

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "refactor: install-service deprecated alias now forwards to service_install()"
```

---

### Task 6: Clean up old install_service code in cli.py

**Files:**
- Modify: `muxplex/cli.py` (remove old `_install_systemd()`, `_install_launchd()`, `install_service()` functions)

**Step 1: Identify the old functions to remove**

The old functions in `cli.py` are:
- `_install_launchd()` (around line 345)
- `_install_systemd()` (around line 425)
- `install_service()` (around line 475)

**Important:** The `upgrade()` function calls `install_service()` internally (to restart the service after upgrade). Check if `upgrade()` references need updating too.

**Step 2: Update upgrade() to use the new service module**

In the `upgrade()` function, find where it calls `install_service()` and replace with the new service module:
- Replace `install_service()` calls with `from muxplex.service import service_restart; service_restart()` (after an upgrade, we want restart, not full reinstall)
- Or if the upgrade function reinstalls the service file, use `service_install()` instead

Check the `upgrade()` function to understand the flow and decide which is appropriate.

**Step 3: Remove the old functions**

Delete `_install_launchd()`, `_install_systemd()`, and `install_service()` from `cli.py`. These are fully replaced by `muxplex/service.py`.

**Step 4: Run the full test suite**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/ -v --no-header 2>&1 | tail -40`

Expected: All PASS. If any tests reference the old `install_service()` function, update them to use the new `service_install()` from `muxplex.service`.

**Step 5: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add muxplex/cli.py muxplex/tests/test_cli.py && git commit -m "refactor: remove old install_service/launchd/systemd from cli.py, replaced by service module"
```

---

### Task 7: Update README with service commands

**Files:**
- Modify: `README.md`

**Step 1: Add service commands section to README**

In `README.md`, add a new section after the "Other commands" table (which was updated in Phase 1):

```markdown
### Service management

```bash
muxplex service install     # Write service file + enable + start
muxplex service uninstall   # Stop + disable + remove service file
muxplex service start       # Start the service
muxplex service stop        # Stop the service
muxplex service restart     # Stop + start
muxplex service status      # Show running/stopped + PID
muxplex service logs        # Tail service logs
```

The service runs `muxplex serve` with no flags — it reads all options from `~/.config/muxplex/settings.json`. To change host/port, edit the config and restart:

```bash
# Edit settings to bind to all interfaces
# (or use the Settings UI in the browser)
muxplex service restart
```
```

Also remove or update any remaining references to `muxplex install-service` in the install/setup sections.

**Step 2: Verify README renders correctly**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && head -160 README.md`

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex && git add README.md && git commit -m "docs: add service subcommand documentation to README"
```

---

### Task 8: Final verification and push

**Step 1: Run the full test suite**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m pytest muxplex/tests/ -v --tb=short 2>&1 | tail -50`

Expected: All tests pass

**Step 2: Run linting**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && python -m ruff check muxplex/ --fix && python -m ruff format muxplex/`

**Step 3: Verify git log**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && git log --oneline -15`

Expected: See all Phase 1 + Phase 2 commits

**Step 4: Push**

Run: `cd /home/bkrabach/dev/web-tmux/muxplex && git push`

---

## Summary of Changes

| File | What changed |
|---|---|
| `muxplex/service.py` | **New file** — platform-dispatched service lifecycle management (install, uninstall, start, stop, restart, status, logs) for both systemd and launchd |
| `muxplex/cli.py` | Added `service` subparser with sub-subparsers; `install-service` deprecated alias now forwards to `service_install()`; removed old `_install_systemd()`, `_install_launchd()`, `install_service()` functions; updated `upgrade()` to use new service module |
| `muxplex/tests/test_service.py` | **New file** — tests for all service commands on both platforms (mocked subprocess) |
| `muxplex/tests/test_cli.py` | Tests for service subcommand dispatch and deprecated install-service forwarding |
| `README.md` | Added service management section with all 7 subcommands |
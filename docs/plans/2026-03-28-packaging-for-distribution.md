# muxplex Distribution Packaging Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Package muxplex as a distributable Python tool installable via `uvx` or `uv tool install` from `git+https://github.com/bkrabach/muxplex`. Single-port server (no Caddy required), CLI entry point, systemd service support.

**Architecture:** The `coordinator/` package is renamed to `muxplex/`, the `frontend/` directory moves inside it as package data, and a WebSocket proxy route replaces Caddy's only remaining job (proxying `/terminal/ws` to ttyd). A new `cli.py` provides the `muxplex` command with `serve` and `install-service` subcommands.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, websockets, hatchling (build backend)

**Not in scope:** GitHub repo creation, git submodule setup, PyPI publishing — handled separately after coding tasks complete.

---

### Task 1: Rename `coordinator/` → `muxplex/` and move `frontend/` inside

**Files:**
- Rename: `coordinator/` → `muxplex/` (all files move)
- Move: `frontend/` → `muxplex/frontend/` (becomes package data)
- Modify: every `.py` file under `muxplex/` (import rewrite)
- Modify: `pyproject.toml` (testpaths)

This is a purely mechanical rename — no behavior changes, no new features. Every other task depends on this layout being in place.

**Step 1: Move the directories**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
mv coordinator muxplex
mv frontend muxplex/frontend
```

**Step 2: Rewrite all `coordinator` → `muxplex` in Python source files**

There are exactly 6 import lines in source files that need updating:

In `muxplex/bells.py`:
```python
# line 20 — old
from coordinator.sessions import run_tmux
# new
from muxplex.sessions import run_tmux

# line 21 — old
from coordinator.state import empty_bell
# new
from muxplex.state import empty_bell
```

In `muxplex/main.py`:
```python
# line 22 — old
from coordinator.bells import apply_bell_clear_rule, process_bell_flags
# new
from muxplex.bells import apply_bell_clear_rule, process_bell_flags

# line 23 — old
from coordinator.sessions import (
# new
from muxplex.sessions import (

# line 31 — old
from coordinator.state import (
# new
from muxplex.state import (

# line 40 — old
from coordinator.ttyd import kill_orphan_ttyd, kill_ttyd, spawn_ttyd, TTYD_PORT
# new
from muxplex.ttyd import kill_orphan_ttyd, kill_ttyd, spawn_ttyd, TTYD_PORT
```

**Step 3: Rewrite all `coordinator` → `muxplex` in test files**

There are 25 import references and 35 `monkeypatch.setattr("coordinator.*")` calls across 7 test files. Use sed for bulk replacement in each file:

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
# Replace in all Python files under muxplex/ (source + tests)
find muxplex -name '*.py' -exec sed -i 's/coordinator\./muxplex./g' {} +
find muxplex -name '*.py' -exec sed -i 's/from muxplex\.sessions/from muxplex.sessions/g' {} +
```

But verify these specific files have the correct replacements (every occurrence of `coordinator.` becomes `muxplex.`):

- `muxplex/tests/test_api.py` — 27 monkeypatch + 7 import references
- `muxplex/tests/test_bells.py` — 2 imports
- `muxplex/tests/test_integration.py` — 4 imports + 4 monkeypatch
- `muxplex/tests/test_sessions.py` — 2 imports
- `muxplex/tests/test_state.py` — 3 imports + 2 monkeypatch
- `muxplex/tests/test_ttyd.py` — 2 imports + 2 monkeypatch

**Step 4: Update `_FRONTEND_DIR` path in `muxplex/main.py`**

The static file path must change since `frontend/` is now inside the package:

```python
# OLD (line 344 of muxplex/main.py):
_FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"

# NEW:
_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"
```

**Step 5: Update frontend path references in test files**

Two test files compute the path to `frontend/` relative to their own location. After the restructure, the traversal changes from `.parent.parent.parent / "frontend"` (3 levels up) to `.parent.parent / "frontend"` (2 levels up, since tests are now at `muxplex/tests/` and frontend is at `muxplex/frontend/`):

In `muxplex/tests/test_frontend_css.py` (line 5):
```python
# OLD:
CSS_PATH = pathlib.Path(__file__).parent.parent.parent / "frontend" / "style.css"

# NEW:
CSS_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "style.css"
```

In `muxplex/tests/test_frontend_html.py` (line 7):
```python
# OLD:
HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "frontend" / "index.html"

# NEW:
HTML_PATH = pathlib.Path(__file__).parent.parent / "frontend" / "index.html"
```

**Step 6: Move `spike_bell_flag.py` out of the package**

The `spike_bell_flag.py` file is a one-off dev spike — it should not ship in the wheel:

```bash
mv muxplex/spike_bell_flag.py scripts/spike_bell_flag.py
```

**Step 7: Update `pyproject.toml` testpaths**

```toml
# OLD:
testpaths = ["coordinator/tests"]

# NEW:
testpaths = ["muxplex/tests"]
```

**Step 8: Run all tests to verify nothing broke**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
python -m pytest muxplex/tests/ --ignore=muxplex/tests/test_integration.py -q 2>&1 | tail -10
```
Expected: **169 passed** (same count as before)

```bash
node --test muxplex/frontend/tests/test_app.mjs 2>&1 | tail -3
```
Expected: **pass 136, fail 0**

```bash
node --test muxplex/frontend/tests/test_terminal.mjs 2>&1 | tail -3
```
Expected: **pass, fail 0**

**Step 9: Commit**

```bash
git add -A && git commit -m "refactor: rename coordinator → muxplex package, move frontend inside as package data"
```

---

### Task 2: Add WebSocket proxy route to replace Caddy

**Files:**
- Modify: `muxplex/main.py` (add WebSocket route + update bell hook port)
- Modify: `muxplex/tests/test_api.py` (add route-exists test)
- Modify: `requirements.txt` (add websockets)

This eliminates Caddy as a runtime dependency. The FastAPI app becomes the single-port server, proxying `/terminal/ws` to ttyd at `ws://localhost:7682/ws`.

**Step 1: Write the failing test**

Add to the end of `muxplex/tests/test_api.py`:

```python
# ---------------------------------------------------------------------------
# WebSocket proxy route
# ---------------------------------------------------------------------------


def test_terminal_ws_route_exists():
    """The app must have a WebSocket route registered at /terminal/ws."""
    from muxplex.main import app

    ws_routes = [
        r for r in app.routes
        if hasattr(r, "path") and r.path == "/terminal/ws"
    ]
    assert len(ws_routes) == 1, "Expected exactly one /terminal/ws route"
```

**Step 2: Run test to verify it fails**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
python -m pytest muxplex/tests/test_api.py::test_terminal_ws_route_exists -v
```
Expected: **FAIL** — no `/terminal/ws` route exists yet.

**Step 3: Add `websockets` dependency to requirements.txt**

```
websockets>=11.0
```

Install it:
```bash
pip install websockets>=11.0
```

**Step 4: Add the WebSocket proxy route to `muxplex/main.py`**

Add these imports near the top (after the existing imports):

```python
from fastapi import WebSocket
import websockets
```

Note: `asyncio` is already imported. `WebSocket` goes on the same line as the existing `FastAPI, HTTPException` import:

```python
# Change this line:
from fastapi import FastAPI, HTTPException

# To:
from fastapi import FastAPI, HTTPException, WebSocket
```

Add a `SERVER_PORT` config variable near `POLL_INTERVAL` (around line 46):

```python
SERVER_PORT: int = int(os.environ.get("MUXPLEX_PORT", "8088"))
```

Add the WebSocket route **after** the last API route (`setup_hooks`) but **before** the static file mount:

```python
# ---------------------------------------------------------------------------
# WebSocket proxy — relay browser ↔ ttyd (replaces Caddy reverse-proxy)
# ---------------------------------------------------------------------------


@app.websocket("/terminal/ws")
async def proxy_terminal_ws(websocket: WebSocket) -> None:
    """Proxy browser WebSocket to ttyd for terminal I/O.

    Accepts with subprotocol 'tty' (required by ttyd), connects to the local
    ttyd instance, and relays frames bidirectionally until either side closes.
    """
    await websocket.accept(subprotocol="tty")
    ttyd_url = f"ws://localhost:{TTYD_PORT}/ws"

    try:
        async with websockets.connect(ttyd_url, subprotocols=["tty"]) as ttyd_ws:

            async def client_to_ttyd() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if msg.get("bytes") is not None:
                            await ttyd_ws.send(msg["bytes"])
                        elif msg.get("text") is not None:
                            await ttyd_ws.send(msg["text"])
                except Exception:
                    pass

            async def ttyd_to_client() -> None:
                try:
                    async for message in ttyd_ws:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(client_to_ttyd(), ttyd_to_client())
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
```

**Step 5: Update the alert-bell hook URLs from port 8099 → `SERVER_PORT`**

There are exactly two hardcoded `8099` references in `muxplex/main.py`. Both are in the `run-shell` curl command for the tmux alert-bell hook.

Line ~148 (in `lifespan`):
```python
# OLD:
"run-shell 'curl -sfo /dev/null -X POST http://localhost:8099/api/sessions/#{session_name}/bell || true'",

# NEW:
f"run-shell 'curl -sfo /dev/null -X POST http://localhost:{SERVER_PORT}/api/sessions/#{{session_name}}/bell || true'",
```

Line ~333 (in `setup_hooks`):
```python
# OLD:
"run-shell 'curl -sfo /dev/null -X POST http://localhost:8099/api/sessions/#{session_name}/bell || true'",

# NEW:
f"run-shell 'curl -sfo /dev/null -X POST http://localhost:{SERVER_PORT}/api/sessions/#{{session_name}}/bell || true'",
```

Note the double-braces `#{{session_name}}` — in an f-string, `{{` produces a literal `{`, so the tmux variable `#{session_name}` is preserved.

**Step 6: Run tests to verify pass**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
python -m pytest muxplex/tests/test_api.py::test_terminal_ws_route_exists -v
```
Expected: **PASS**

Also run the full test suite to ensure nothing broke:
```bash
python -m pytest muxplex/tests/ --ignore=muxplex/tests/test_integration.py -q 2>&1 | tail -5
```
Expected: **170 passed** (169 + 1 new)

**Step 7: Commit**

```bash
git add -A && git commit -m "feat: add WebSocket proxy route to replace Caddy, update bell hook port to configurable SERVER_PORT"
```

---

### Task 3: Create `muxplex/cli.py` entry point and `muxplex/__main__.py`

**Files:**
- Create: `muxplex/cli.py`
- Create: `muxplex/__main__.py`
- Create: `muxplex/tests/test_cli.py`

The CLI is the user-facing interface: `muxplex` runs the server, `muxplex install-service` writes a systemd unit file.

**Step 1: Write the failing tests**

Create `muxplex/tests/test_cli.py`:

```python
"""Tests for muxplex/cli.py — CLI entry point."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_cli_module_importable():
    """muxplex.cli must be importable."""
    from muxplex.cli import main  # noqa: F401


def test_main_calls_serve_by_default():
    """Calling main() with no args must invoke serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex"]):
            main()
        mock_serve.assert_called_once_with(host="0.0.0.0", port=8088)


def test_main_passes_custom_host_and_port():
    """main() with --host/--port must forward them to serve()."""
    from muxplex.cli import main

    with patch("muxplex.cli.serve") as mock_serve:
        with patch("sys.argv", ["muxplex", "--host", "127.0.0.1", "--port", "9000"]):
            main()
        mock_serve.assert_called_once_with(host="127.0.0.1", port=9000)


def test_main_install_service_subcommand():
    """main() with 'install-service' must invoke install_service()."""
    from muxplex.cli import main

    with patch("muxplex.cli.install_service") as mock_install:
        with patch("sys.argv", ["muxplex", "install-service"]):
            main()
        mock_install.assert_called_once_with(system=False)


def test_main_install_service_system_flag():
    """main() with 'install-service --system' passes system=True."""
    from muxplex.cli import main

    with patch("muxplex.cli.install_service") as mock_install:
        with patch("sys.argv", ["muxplex", "install-service", "--system"]):
            main()
        mock_install.assert_called_once_with(system=True)


def test_install_service_user_mode_writes_unit_file(tmp_path, monkeypatch):
    """install_service(system=False) writes a unit file to ~/.config/systemd/user/."""
    from muxplex.cli import install_service

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    install_service(system=False)

    unit_path = fake_home / ".config" / "systemd" / "user" / "muxplex.service"
    assert unit_path.exists()
    content = unit_path.read_text()
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content
    assert "muxplex" in content
    assert "default.target" in content


def test_install_service_system_mode_target(tmp_path, monkeypatch):
    """install_service(system=True) targets multi-user.target in the unit file."""
    from muxplex.cli import install_service

    # Redirect the system path to tmp so we don't write to /etc
    unit_path = tmp_path / "muxplex.service"
    monkeypatch.setattr("muxplex.cli._system_service_path", unit_path)

    install_service(system=True)

    assert unit_path.exists()
    content = unit_path.read_text()
    assert "multi-user.target" in content


def test_dunder_main_calls_main():
    """python -m muxplex must call cli.main()."""
    with patch("muxplex.cli.main") as mock_main:
        # Simulate `python -m muxplex` by exec'ing __main__.py
        import importlib
        import muxplex.__main__  # noqa: F401

        # The import itself calls main() at module level
        # Re-exec to test:
        mock_main.reset_mock()
        exec(Path("muxplex/__main__.py").read_text())
        mock_main.assert_called_once()
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
python -m pytest muxplex/tests/test_cli.py -v 2>&1 | head -30
```
Expected: **FAIL** — `muxplex.cli` does not exist yet.

**Step 3: Create `muxplex/cli.py`**

```python
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

    unit = f"""\
[Unit]
Description=muxplex — web-based tmux session dashboard
After=network.target

[Service]
Type=simple
ExecStart={executable} -m muxplex
Restart=on-failure
RestartSec=5s
Environment=PATH={os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}

[Install]
WantedBy={"multi-user.target" if system else "default.target"}
"""

    if system:
        path = _system_service_path
        reload_cmd = "sudo systemctl daemon-reload && sudo systemctl enable --now muxplex"
    else:
        path = Path.home() / ".config" / "systemd" / "user" / "muxplex.service"
        path.parent.mkdir(parents=True, exist_ok=True)
        reload_cmd = "systemctl --user daemon-reload && systemctl --user enable --now muxplex"

    path.write_text(unit)
    print(f"Service file written to {path}")
    print(f"Enable with:\n  {reload_cmd}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="muxplex",
        description="muxplex — web-based tmux session dashboard",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8088, help="Port (default: 8088)")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Start the server (default)")

    svc = sub.add_parser("install-service", help="Install systemd service unit")
    svc.add_argument("--system", action="store_true", help="System-wide (requires sudo)")

    args = parser.parse_args()

    if args.command == "install-service":
        install_service(system=args.system)
    else:
        serve(host=args.host, port=args.port)
```

**Step 4: Create `muxplex/__main__.py`**

```python
"""Allow running muxplex as: python -m muxplex"""

from muxplex.cli import main

main()
```

**Step 5: Run tests to verify pass**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
python -m pytest muxplex/tests/test_cli.py -v
```
Expected: **PASS** (all 8 tests)

Run full suite:
```bash
python -m pytest muxplex/tests/ --ignore=muxplex/tests/test_integration.py -q 2>&1 | tail -5
```
Expected: **178 passed** (170 + 8 new)

**Step 6: Commit**

```bash
git add -A && git commit -m "feat: add CLI entry point (muxplex serve, muxplex install-service) and __main__.py"
```

---

### Task 4: Update `pyproject.toml` for full distribution packaging

**Files:**
- Modify: `pyproject.toml` (complete rewrite)

**Step 1: No new tests needed**

The existing test suite implicitly validates the package is importable. The verification step below confirms `pip install -e .` and the entry point work.

**Step 2: Replace `pyproject.toml` contents entirely**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "muxplex"
version = "0.1.0"
description = "Web-based tmux session dashboard — access all your tmux sessions from any browser"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "aiofiles>=23.0",
    "websockets>=11.0",
]

[project.optional-dependencies]
dev = [
    "httpx>=0.27.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "beautifulsoup4>=4.12",
]

[project.scripts]
muxplex = "muxplex.cli:main"

[project.urls]
Repository = "https://github.com/bkrabach/muxplex"
Issues = "https://github.com/bkrabach/muxplex/issues"

[tool.hatch.build.targets.wheel]
packages = ["muxplex"]

[tool.pytest.ini_options]
testpaths = ["muxplex/tests"]
asyncio_mode = "auto"
addopts = "--import-mode=importlib -m 'not integration'"
markers = [
    "integration: marks tests as integration tests requiring real tmux (deselect with '-m not integration')",
]
```

**Step 3: Verify installability**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
pip install -e ".[dev]" --quiet
```

Then verify the entry point:
```bash
muxplex --help
```
Expected output should show:
```
usage: muxplex [-h] [--host HOST] [--port PORT] {serve,install-service} ...
```

Also verify `python -m muxplex --help` shows the same.

**Step 4: Run full test suite one more time**

```bash
cd /home/bkrabach/dev/web-tmux/muxplex
python -m pytest -q 2>&1 | tail -5
```
Expected: all tests pass.

**Step 5: Commit**

```bash
git add -A && git commit -m "chore: configure pyproject.toml for distribution (entry point, deps, hatchling build)"
```

---

### Task 5: Rewrite `README.md`

**Files:**
- Modify: `README.md`

**Step 1: No tests needed — doc-only change**

**Step 2: Replace `README.md` contents**

```markdown
# muxplex

> Web-based tmux session dashboard — access and manage all your tmux sessions from any browser or mobile device.

![muxplex dashboard](assets/branding/og/og-dark.png)

## Features

- Live grid of all running tmux sessions with snapshot previews
- Full interactive terminal in the browser (via ttyd + xterm.js)
- Collapsible session sidebar — switch sessions without leaving the terminal
- Bell / activity notifications
- Mobile-friendly responsive layout, PWA-capable
- Works over Tailscale / private network

## Prerequisites

- **tmux** — must be installed and running sessions
- **ttyd** — WebSocket bridge for terminal access (`brew install ttyd` / `apt install ttyd`)
- **Python 3.11+**

## Quick start (uvx — no install)

```bash
uvx --from git+https://github.com/bkrabach/muxplex muxplex
```

Open http://localhost:8088 in your browser.

## Install permanently

```bash
uv tool install git+https://github.com/bkrabach/muxplex
muxplex
```

## Install as a service (systemd)

```bash
# User service (no sudo required)
muxplex install-service
systemctl --user daemon-reload
systemctl --user enable --now muxplex

# System service (runs on boot for all users)
muxplex install-service --system
sudo systemctl daemon-reload
sudo systemctl enable --now muxplex
```

## Usage

```
muxplex [--host HOST] [--port PORT] [serve]
muxplex install-service [--system]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8088` | Port to serve on |
| `install-service` | — | Install systemd service unit |
| `--system` | false | System-wide service (requires sudo) |

## Development

```bash
git clone https://github.com/bkrabach/muxplex
cd muxplex
pip install -e ".[dev]"

# Run server
muxplex

# Run tests
python -m pytest
node --test muxplex/frontend/tests/test_app.mjs
node --test muxplex/frontend/tests/test_terminal.mjs
```

## Architecture

- **muxplex/** — Python package (FastAPI coordinator + CLI)
  - `main.py` — FastAPI app: session API, static frontend, WebSocket proxy to ttyd
  - `sessions.py` — tmux session enumeration and snapshot capture
  - `bells.py` — bell/activity detection
  - `ttyd.py` — ttyd process lifecycle management
  - `state.py` — persistent state management (JSON)
  - `cli.py` — CLI entry point (`muxplex serve`, `muxplex install-service`)
  - `frontend/` — static web app (HTML/CSS/JS + xterm.js)
- **assets/branding/** — SVG sources, rendered PNGs, design tokens
- **scripts/** — `render-brand-assets.py` — regenerate brand PNGs from SVGs

## Brand assets

Design language, color tokens, and brand assets in `assets/branding/`. Regenerate:

```bash
python scripts/render-brand-assets.py
```
```

**Step 3: Commit**

```bash
git add -A && git commit -m "docs: rewrite README with uvx/uv install instructions and full usage guide"
```

---

## Post-plan: Git repo setup (manual, not automated)

After all 5 tasks are complete, the parent session will:

1. Create the GitHub repo at `github.com/bkrabach/muxplex`
2. Push the muxplex directory contents to it
3. Convert the local `muxplex/` directory into a git submodule of the parent `web-tmux` repo

These steps are outside the scope of this coding plan.

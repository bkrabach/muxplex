# muxplex

**Web-based tmux session dashboard — access and manage all your tmux sessions from any browser or mobile device.**

![muxplex dashboard](assets/branding/og/og-dark.png)

---

## Features

- **Live session grid** — thumbnail snapshots of every running tmux session, auto-refreshed
- **Full interactive terminal** — click any session to open a real terminal (powered by ttyd + xterm.js)
- **Collapsible session sidebar** — quick-switch between sessions without leaving the terminal view
- **Bell & activity notifications** — visual alerts when any session rings a bell or has new output
- **Mobile-friendly responsive layout** — works on phones and tablets; PWA-capable for home-screen install
- **Works over Tailscale / private network** — serve to any device on your network without exposing to the internet

---

## Prerequisites

- **Python 3.11+** — installed via `uv` or system Python
- **tmux** — terminal multiplexer
  - macOS: `brew install tmux`
  - Ubuntu/WSL: `sudo apt install tmux`
- **ttyd** — terminal sharing over HTTP (required for interactive terminal access)
  - macOS: `brew install ttyd`
  - Ubuntu/WSL: `sudo apt install ttyd` or `sudo snap install ttyd`
  - Other: https://github.com/tsl0922/ttyd#installation

> **Tip:** muxplex checks for `tmux` and `ttyd` at startup and prints install instructions if either is missing.

---

## Quick Start (uvx — no install)

Run muxplex directly without installing anything permanently:

```bash
uvx --from git+https://github.com/bkrabach/muxplex muxplex
```

Then open **http://localhost:8088** in your browser.

> **Note:** `uvx` is part of [uv](https://docs.astral.sh/uv/). Install uv with `curl -LsSf https://astral.sh/uv/install.sh | sh`.

---

## Install Permanently

Install muxplex as a persistent CLI tool using `uv tool`:

```bash
uv tool install git+https://github.com/bkrabach/muxplex
```

Then run it any time with:

```bash
muxplex
```

---

## Install as a Service

```bash
muxplex service install
```

The service starts automatically on login (macOS) or at boot (Linux) and restarts on failure.

To stop and remove:

```bash
muxplex service uninstall
```

> **Note:** All service commands use the `muxplex service` subcommand — see [Service management](#service-management) below.

---

## Usage

```bash
muxplex [OPTIONS]
muxplex serve [OPTIONS]     # explicit form
```

All serve options read from `~/.config/muxplex/settings.json` by default. CLI flags override for that run only.

| Option | settings.json key | Default | Description |
|---|---|---|---|
| `--host HOST` | `host` | `127.0.0.1` | Interface to bind (`0.0.0.0` for network access) |
| `--port PORT` | `port` | `8088` | Port to listen on |
| `--auth MODE` | `auth` | `pam` | Auth method: `pam` or `password` |
| `--session-ttl SEC` | `session_ttl` | `604800` | Session TTL in seconds (7 days; 0 = browser session) |

### Other commands

| Command | Description |
|---|---|
| `muxplex doctor` | Check dependencies and system status |
| `muxplex upgrade` | Upgrade to latest version and restart service |
| `muxplex show-password` | Show the current muxplex password |
| `muxplex reset-secret` | Regenerate signing secret (invalidates sessions) |
| `muxplex config` | Show all settings with current values |
| `muxplex config list` | Show all settings with current values |
| `muxplex config get <key>` | Show one setting |
| `muxplex config set <key> <value>` | Set a setting (auto-detects type) |
| `muxplex config reset [key]` | Reset one or all settings to defaults |

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

### Examples

```bash
# Start with defaults from settings.json
muxplex

# Override port for this run only
muxplex --port 9000

# Override host for this run only
muxplex serve --host 0.0.0.0
```

---

## Development

### Setup

```bash
git clone https://github.com/bkrabach/muxplex
cd muxplex

# Install with dev dependencies
uv pip install -e ".[dev]"
```

### Run the server

```bash
muxplex
# or directly:
python -m muxplex
```

### Run tests

```bash
# Python tests (pytest)
pytest

# JavaScript tests (node:test)
node --test muxplex/frontend/tests/test_terminal.mjs
node --test muxplex/frontend/tests/test_app.mjs
```

---

## Architecture

```
muxplex/
├── pyproject.toml          # Package metadata, entry points, dependencies
├── README.md               # This file
├── scripts/                # Utility scripts (asset generation, etc.)
│   └── render-brand-assets.py
├── assets/
│   └── branding/           # Brand design system and generated assets
│       ├── DESIGN-SYSTEM.md
│       ├── tokens.json / tokens.css
│       ├── svg/            # Source SVG files
│       ├── og/             # Open Graph images (og-dark.png, og-light.png)
│       ├── icons/          # App icons
│       ├── favicons/       # Favicon variants
│       ├── pwa/            # PWA manifest icons
│       ├── lockup/         # Wordmark + icon lockup
│       └── wordmark/       # Text-only wordmark
└── muxplex/                # Python package
    ├── __init__.py
    ├── __main__.py         # `python -m muxplex` entry point
    ├── cli.py              # CLI entry point and subcommand dispatch
    ├── main.py             # FastAPI app: session API, bell hooks, WebSocket proxy to ttyd, static frontend
    ├── sessions.py         # tmux session discovery and snapshot capture
    ├── bells.py            # Bell/activity notification tracking
    ├── ttyd.py             # ttyd process lifecycle management (spawn, kill, PID tracking)
    ├── state.py            # Shared in-process state (sessions, bells, ttyd)
    ├── frontend/           # Static frontend assets (served as package data)
    │   ├── index.html
    │   ├── app.js
    │   ├── terminal.js     # xterm.js + WebSocket terminal init
    │   ├── style.css
    │   ├── manifest.json   # PWA manifest
    │   └── tests/          # JavaScript unit tests
    └── tests/              # Python tests (pytest)
```

---

## Brand Assets

Design language, color tokens, and brand assets live in `assets/branding/`. See [`assets/branding/DESIGN-SYSTEM.md`](assets/branding/DESIGN-SYSTEM.md) for the full design reference.

To regenerate PNG/favicon assets from SVG sources:

```bash
python3 scripts/render-brand-assets.py
```

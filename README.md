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

| Requirement | Notes |
|---|---|
| **tmux** | Must have at least one session running (`tmux new -s main`) |
| **ttyd** | WebSocket bridge — `brew install ttyd` (macOS) / `apt install ttyd` (Debian/Ubuntu) |
| **Python 3.11+** | Required by the muxplex server |

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

## Install as a Service (systemd)

### User service (no sudo required)

```bash
# Install and enable the user systemd service
muxplex install-service
systemctl --user daemon-reload
systemctl --user enable --now muxplex
```

The service starts automatically when you log in and restarts on failure.

### System-wide service (requires sudo)

```bash
# Install as a system service (runs at boot for all users)
muxplex install-service --system
sudo systemctl daemon-reload
sudo systemctl enable --now muxplex
```

---

## Usage

```bash
muxplex [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--host HOST` | `0.0.0.0` | Interface to bind (use `127.0.0.1` to restrict to localhost) |
| `--port PORT` | `8088` | Port to listen on |
| `install-service` | — | Install a systemd service unit for muxplex |
| `--system` | — | (with `install-service`) Install as a system service instead of user service |

### Examples

```bash
# Start on default host/port
muxplex

# Start on a specific port
muxplex --port 9000

# Start bound to localhost only
muxplex --host 127.0.0.1

# Install as a user systemd service
muxplex install-service

# Install as a system-wide systemd service
muxplex install-service --system
```

---

## Development

### Setup

```bash
git clone https://github.com/bkrabach/muxplex
cd muxplex

# Install with dev dependencies
pip install -e ".[dev]"
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

# JavaScript tests (node)
node muxplex/frontend/tests/run-tests.js
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
    ├── cli.py              # CLI argument parsing and `install-service` subcommand
    ├── main.py             # FastAPI app factory and route registration
    ├── sessions.py         # tmux session discovery and snapshot capture
    ├── bells.py            # Bell/activity notification tracking
    ├── ttyd.py             # ttyd process lifecycle management and WebSocket proxy
    ├── state.py            # Shared in-process state (sessions, bells, ttyd)
    ├── frontend/           # Static frontend assets (served as package data)
    │   ├── index.html
    │   ├── app.js
    │   ├── style.css
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

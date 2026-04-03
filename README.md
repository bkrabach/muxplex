# muxplex

**Web-based tmux session dashboard — access, monitor, and manage all your tmux sessions from any browser on any device.**

![muxplex dashboard](assets/branding/og/og-dark.png)

---

## Features

### Dashboard

- **Live session grid** — preview tiles with ANSI-colored terminal snapshots, auto-refreshed
- **Two view modes** — Auto (scrollable grid) and Fit (all sessions fill the viewport)
- **Hover preview** — full-size overlay of session content on tile hover
- **Activity indicators** — bell notification badges, amber favicon dot for browser tab visibility
- **Session creation** — `+` button with custom command template support
- **Session deletion** — `×` button with custom command template support
- **Mobile-friendly** — responsive layout, PWA-capable for home-screen install

### Terminal

- **Full interactive terminal** — powered by xterm.js + ttyd
- **Native clipboard** — Ctrl+Shift+C to copy, Ctrl+Shift+V to paste
- **Mouse select auto-copy** — selecting text copies to system clipboard on release
- **OSC 52 tmux clipboard bridge** — tmux copy mode selections go to system clipboard
- **Sidebar session switcher** — quick-switch between sessions with live previews

### Settings

- **In-browser settings panel** — gear icon or `,` shortcut
- **Display** — font size, grid columns, hover delay, view mode, device badges, activity indicator
- **Sessions** — default session, sort order, hidden sessions, auto-open, bell sound, notifications
- **Commands** — custom create/delete session templates
- **Multi-Device** — remote instance federation
- **CLI** — `muxplex config list/get/set/reset`

### Service Management

- `muxplex service install/start/stop/restart/status/logs/uninstall`
- **Platform-aware** — systemd user service on Linux/WSL, launchd agent on macOS
- **Config-driven** — service reads all options from `~/.config/muxplex/settings.json` (no flags in the service file)

### Authentication

- **PAM authentication** — Linux/macOS system credentials
- **Password mode** — auto-generated or set via `MUXPLEX_PASSWORD` env var
- **Localhost bypass** — no auth needed on 127.0.0.1
- **Secure session cookies** — signed with configurable TTL

### Developer Tools

- `muxplex doctor` — dependency + config diagnostics with update check
- `muxplex upgrade` — smart version check + auto-update + service restart
- `muxplex config` — CLI settings management

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

> **Tip:** Run `muxplex doctor` to check all dependencies and system status.

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

```bash
uv tool install git+https://github.com/bkrabach/muxplex
muxplex doctor  # verify dependencies
```

Then run it any time with:

```bash
muxplex
```

---

## Install as a Service

```bash
muxplex service install
# → prompts to set host to 0.0.0.0 for network access
```

The service starts automatically on login (macOS) or at boot (Linux) and restarts on failure.

```bash
# Open in browser
open http://localhost:8088
```

To stop and remove:

```bash
muxplex service uninstall
```

---

## CLI Reference

```
muxplex                              Start server (default)
muxplex serve [flags]                Start with CLI flag overrides
muxplex service install              Install + enable + start as OS service
muxplex service uninstall            Stop + disable + remove
muxplex service start|stop|restart   Manage running service
muxplex service status               Show service status
muxplex service logs                 Tail service logs
muxplex config                       Show all settings
muxplex config get <key>             Show one setting
muxplex config set <key> <value>     Set a setting
muxplex config reset [key]           Reset one or all to defaults
muxplex upgrade [--force]            Smart update with version check
muxplex doctor                       Check dependencies + config
muxplex show-password                Show current auth password
muxplex reset-secret                 Regenerate signing secret
```

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

The service runs `muxplex serve` with no flags — it reads all options from `~/.config/muxplex/settings.json`. To change host/port, edit the config (or use the Settings UI in the browser) and restart:

```bash
muxplex config set host 0.0.0.0
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

## Configuration

All settings are stored in `~/.config/muxplex/settings.json`.

| Key | Default | Description |
|---|---|---|
| `host` | `127.0.0.1` | Bind address (set to `0.0.0.0` for network access) |
| `port` | `8088` | Server port |
| `auth` | `pam` | Authentication mode: `pam` or `password` |
| `session_ttl` | `604800` | Session cookie TTL in seconds (7 days; 0 = browser session) |
| `default_session` | `null` | Session to auto-open on load |
| `sort_order` | `manual` | Session ordering: `manual`, `alphabetical`, `recent` |
| `hidden_sessions` | `[]` | Sessions hidden from the dashboard |
| `window_size_largest` | `false` | Auto-set tmux `window-size largest` on connect |
| `auto_open_created` | `true` | Auto-open newly created sessions |
| `new_session_template` | `tmux new-session -d -s {name}` | Command template for creating sessions |
| `delete_session_template` | `tmux kill-session -t {name}` | Command template for deleting sessions |
| `device_name` | `""` (hostname) | Display name for this device |
| `federation_key` | `""` | Server-to-server authentication key for federation |
| `remote_instances` | `[]` | Remote muxplex instances to aggregate |
| `multi_device_enabled` | `false` | Enable multi-instance federation |

**Priority:** CLI flags > `settings.json` > defaults.

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+Shift+C | Copy terminal selection to system clipboard |
| Ctrl+Shift+V | Paste from system clipboard into terminal |
| `,` (comma) | Open settings |
| Escape | Close settings / return to dashboard |

Mouse select in the terminal auto-copies to the system clipboard on release.

---

## Platform Support

| Platform | Service | Auth |
|---|---|---|
| Linux (Ubuntu/Debian) | systemd user service | PAM |
| macOS | launchd agent | PAM |
| WSL | systemd user service | PAM |

---

## Project Structure

```
muxplex/
├── muxplex/
│   ├── __init__.py
│   ├── __main__.py          # python -m muxplex entry
│   ├── cli.py               # CLI entry point and subcommand dispatch
│   ├── main.py              # FastAPI app, routes, WebSocket proxy
│   ├── auth.py              # PAM/password auth middleware
│   ├── sessions.py          # tmux session enumeration + snapshots
│   ├── bells.py             # Bell flag detection + clear rules
│   ├── state.py             # Persistent state (JSON)
│   ├── settings.py          # User settings management
│   ├── service.py           # Service install/start/stop (systemd + launchd)
│   ├── ttyd.py              # ttyd process lifecycle
│   ├── frontend/
│   │   ├── index.html        # Main SPA
│   │   ├── login.html        # Login page
│   │   ├── app.js            # Dashboard, sidebar, settings, previews
│   │   ├── terminal.js       # xterm.js terminal + clipboard
│   │   ├── style.css         # All styles (dark theme)
│   │   ├── manifest.json     # PWA manifest
│   │   ├── wordmark-on-dark.svg
│   │   └── tests/            # JavaScript unit tests
│   └── tests/                # Python tests (pytest)
├── assets/branding/          # Logos, icons, design system
├── docs/plans/               # Historical design + implementation plans
├── scripts/                  # Utility scripts (asset generation)
├── pyproject.toml
└── README.md
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
python -m pytest muxplex/tests/ --ignore=muxplex/tests/test_integration.py

# JavaScript tests (node:test)
node --test muxplex/frontend/tests/test_terminal.mjs
node --test muxplex/frontend/tests/test_app.mjs
```

---

## Brand Assets

Design language, color tokens, and brand assets live in `assets/branding/`. See [`assets/branding/DESIGN-SYSTEM.md`](assets/branding/DESIGN-SYSTEM.md) for the full design reference.

To regenerate PNG/favicon assets from SVG sources:

```bash
python3 scripts/render-brand-assets.py
```

---

## License

MIT

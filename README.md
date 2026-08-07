# muxplex

**Web-based tmux session dashboard — access, monitor, and manage all your tmux sessions from any browser on any device.**

![muxplex dashboard](https://raw.githubusercontent.com/bkrabach/muxplex/main/assets/branding/og/og-dark.png)

---

## Features

### Dashboard

- **Live session grid** — preview tiles with ANSI-colored terminal snapshots, auto-refreshed
- **Two view modes** — Auto (scrollable grid) and Fit (all sessions fill the viewport)
- **Hover preview** — full-size overlay of session content on tile hover
- **Activity indicators** — bell notification badges on tiles; amber favicon dot + `(N)` count in browser tab title when sessions have unseen activity
- **Session creation** — `+` button with device selector dropdown when multi-device is enabled; custom command template support
- **Session deletion** — `×` button with custom command template support
- **Mobile-friendly** — responsive layout, PWA-capable for home-screen install

### Terminal

- **Full interactive terminal** — powered by xterm.js + ttyd
- **Native clipboard** — Ctrl+Shift+C to copy, Cmd+V (macOS) / Ctrl+Shift+V (Linux) to paste
- **Mouse select auto-copy** — selecting text copies to system clipboard on release
- **OSC 52 tmux clipboard bridge** — tmux copy mode selections go to system clipboard
- **Search** — Ctrl+F opens a search bar to find text in terminal scrollback (xterm-addon-search)
- **Clickable URLs** — Ctrl+Click (Cmd+Click on macOS) opens URLs in terminal output in a new tab (xterm-addon-web-links)
- **Inline image rendering** — Sixel and iTerm2 graphic protocols for tools like yazi file manager (xterm-addon-image)
- **Sidebar session switcher** — quick-switch between sessions with live previews

### Settings

- **In-browser settings panel** — gear icon or `,` shortcut
- **Display** — font size, grid columns, hover delay, view mode, device badges, activity indicator
- **Sessions** — default session, sort order, hidden sessions, auto-open, bell sound, notifications
- **Commands** — custom create/delete session templates
- **Multi-Device** — remote instance federation
- **CLI** — `muxplex config list/get/set/reset`

### Multi-Device

- **Remote session aggregation** — federate multiple muxplex instances into a unified dashboard view
- **Device selector in new session** — `+` button shows a device dropdown when multi-device is enabled; create sessions on any connected instance directly from the dashboard
- **Remote bell-clear** — opening a session on a remote device automatically clears its activity notification via federation API (`POST /api/bell/clear`)
- **Unique session keys** — sessions identified by `remoteId:name` across devices, preventing bell-state collisions for identically-named sessions on different machines

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

### Agents & Automation

- **Public HTTP API** — the contract is discoverable at `/openapi.json` and `/docs`; headless clients authenticate with a Bearer federation key
- **Terminal input over the API** — `POST /api/sessions/{name}/input` lets an agent type into a live session (RCE by design, default-CLOSED, fenced by `input_enabled` + `input_allowed_sessions`)
- **Vendor-neutral guide** — point any agent (or a `curl` script) at [Driving muxplex from an agent](docs/AGENT_GUIDE.md)

### HTTPS / TLS

- `muxplex setup-tls` — auto-detect and set up TLS certificates
- **Tailscale** — real Let's Encrypt certs via `tailscale cert` (recommended when every client has Tailscale)
- **mkcert** — locally-trusted certs, zero browser warnings (when mkcert is installed on each client)
- **Local CA** — persistent root CA + signed leaf for browser-trusted HTTPS on plain LAN names (`spark-1`, `192.168.1.5`) without Tailscale or a public domain; install the CA once per client → see [Trusting the local CA](docs/TRUSTING_THE_LOCAL_CA.md)
- **Self-signed** — fallback for immediate HTTPS (browser shows warning)
- Required for browser clipboard API on non-localhost, and for stable PWA install (browsers refuse to keep installed PWAs in standalone mode against an untrusted origin)

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
uvx muxplex
```

Then open **http://localhost:8088** in your browser.

> **Note:** `uvx` is part of [uv](https://docs.astral.sh/uv/). Install uv with `curl -LsSf https://astral.sh/uv/install.sh | sh`.

---

## Install Permanently

```bash
uv tool install muxplex
muxplex doctor  # verify dependencies
```

Upgrade later with either:

```bash
uv tool upgrade muxplex   # standard uv workflow
muxplex upgrade           # also restarts the service if installed
```

> **Installing from git instead?** `uv tool install git+https://github.com/bkrabach/muxplex`
> tracks the default branch and gets unreleased commits. Do **not** pin a tag
> (`...@v1.2.3`) unless you mean it: `uv tool upgrade` resolves strictly within the
> recorded requirement, so a pinned rev reports "Nothing to upgrade" forever. Released
> versions on PyPI are the recommended path.

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
muxplex setup-tls [--method auto]   Set up TLS certs (Tailscale/mkcert/self-signed)
muxplex setup-tls --status          Show current TLS configuration
muxplex env                          Print `eval`-able TMUX_TMPDIR export
muxplex tmux status                  Show whether muxplex's tmux config is active
muxplex tmux install [--dry-run]     Install muxplex's tmux config (safe, reversible)
muxplex tmux uninstall               Remove it, leaving your own config untouched
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

### HTTPS / TLS setup

```bash
# Auto-detect the best TLS method and set up certificates
muxplex setup-tls

# Use a specific TLS method
muxplex setup-tls --method tailscale
muxplex setup-tls --method mkcert
muxplex setup-tls --method selfsigned
muxplex setup-tls --method ca           # persistent local CA + signed leaf

# Show current TLS status and configuration
muxplex setup-tls --status

# Override TLS cert/key for a single run (without saving to config)
muxplex serve --tls-cert /path/cert.pem --tls-key /path/key.pem

# Check TLS configuration and dependencies
muxplex doctor
```

Auto-detection priority: **Tailscale** (if `tailscale` is installed and a cert is available) → **mkcert** (if `mkcert` is installed) → **self-signed** (always available as a fallback). Use `--method` to override.

> **Note:** Tailscale certs have a 90-day expiry. Run `muxplex setup-tls --method tailscale` to renew when needed.

#### When to use `--method ca`

The `ca` method is for the case where you want browser-trusted HTTPS on plain LAN names (e.g. `https://my-host:8088`, `https://192.168.1.5:8088`) but **can't** use Tailscale (no client install, blocked by IT policy, or the URL must be the bare LAN name) and **don't** want to buy a public domain.

It generates a persistent root CA in `~/.config/muxplex/ca/` and signs a 13-month leaf with it. The leaf's SAN automatically includes the hostname, `<hostname>.local`, `localhost`, the primary LAN IPv4 address, and the Tailscale MagicDNS name (if Tailscale is connected). Install the **CA** (not the leaf) once on each client; subsequent leaf rotations don't require re-trusting.

Not part of the `auto` cascade — must be opted into explicitly.

> **→ See [docs/TRUSTING_THE_LOCAL_CA.md](docs/TRUSTING_THE_LOCAL_CA.md)** for per-platform install instructions (Windows, macOS, Linux, iOS, Android, Firefox).

#### Fetching the CA over the network: `GET /api/ca`

Installing the CA on each client previously required `scp`-ing it off the server, which needs SSH access the client may not have — and it's easy to grab the wrong file (`muxplex.crt`, the **leaf** the server presents on the wire) instead of the CA, producing "unable to get local issuer certificate". `GET /api/ca` serves the CA's public certificate directly over HTTP(S) — no SSH, no auth (a CA public cert isn't a secret; it's the trust anchor clients are meant to install), and no ambiguity about which file it is:

```bash
curl -k https://my-host:8088/api/ca -o muxplex-ca.crt
```

`-k` is acceptable **only** for this one bootstrap fetch of a public trust anchor (there's nothing sensitive to expose by skipping verification here). For high-trust setups, confirm the fingerprint out-of-band before trusting it:

```bash
openssl x509 -in muxplex-ca.crt -noout -fingerprint -sha256
```

Returns 404 if this server isn't using `setup-tls --method ca` (e.g. it's on Tailscale, mkcert, or self-signed instead).

### tmux socket (the "invisible session" hazard)

muxplex looks for tmux sessions under a specific socket directory (the
`tmux_socket_dir` setting, mapped to tmux's `TMUX_TMPDIR` environment
variable). **Any other tool or script that creates a tmux session without
setting the same `TMUX_TMPDIR` lands on a *different* tmux server** and is
silently invisible to muxplex — `tmux list-sessions` from your interactive
shell will show it, but muxplex's dashboard, API, and Stream Deck sidecar
never will, because they're watching a different socket. This bites hardest
when `tmux_socket_dir` is left at its default (`""`): a systemd/launchd
*service* process doesn't inherit your login shell's `TMUX_TMPDIR`, so the
service quietly falls back to tmux's compiled-in default
(`/tmp/tmux-$UID`) even if your shell rc sets something else.

The one-line fix — run this before creating a session you want muxplex to see:

```bash
eval "$(muxplex env)"
tmux new-session -d -s my-session   # now lands where muxplex can see it
```

`muxplex env` prints a single `export TMUX_TMPDIR=...` line (nothing else,
so `eval` is always safe) resolved from the configured `tmux_socket_dir` —
or, if that's unset, your shell's own `TMUX_TMPDIR` — or, failing both,
tmux's own default. `GET /api/instance-info` also exposes `tmux_socket_dir`
(the exact value the *running server* resolves, since that endpoint runs
inside the server process itself) so remote tools/agents can discover it
without SSH access or tribal knowledge.

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
| `views` | `[]` | Named session views for grouping and filtering sessions. Each view entry may carry an optional `match_names: [str]` -- fnmatch-style glob patterns matched case-insensitively against a session's bare tmux name (never a device-qualified `device_id:name` key, since the qualifier is a UUID nobody would type). A session is in the view if it is pinned in `sessions` **or** matches a pattern -- a strict union; rules are resolved fresh on every read and never written back into `sessions`, so a rule-based view cannot decay. See `docs/API_SEMANTICS.md` and `GET /api/views`. |
| `stale_key_grace_hours` | `24.0` | Hours before a session key absent from all *known* live sessions is pruned from views/hidden_sessions (syncable; per-device bookkeeping is local-only). Federation-aware: a remote device's keys are only ever evaluated for pruning while that device is currently reachable (see "Stale-key pruning" below) -- an offline device's view membership is never touched. |
| `window_size_largest` | `false` | Auto-set tmux `window-size largest` on connect |
| `auto_open_created` | `true` | Auto-open newly created sessions |
| `new_session_template` | `tmux new-session -d -s {name}` | Command template for creating sessions |
| `delete_session_template` | `tmux kill-session -t {name}` | Command template for deleting sessions |
| `session_commands` | `[]` | Additional NAMED create/kill command pairs (see "Command pairs" below) |
| `tmux_theme` | `brand` | Which shipped tmux theme `muxplex tmux install` renders. `brand` is built from muxplex's own UI tokens, so a window that rings a bell turns the same amber in your terminal that its tile turns in the dashboard. Alternatives: `steel`, `catppuccin-mocha`. Not federation-syncable -- it renders to a file on this host. |
| `tmux_copy_mode` | `desktop` | Which copy-mode keybinding scheme `muxplex tmux install` (and `PATCH /api/tmux-config`) renders as `~/.config/muxplex/tmux.d/30-copy-mode.conf`. `desktop` is tmux's own default (arrow keys, Page Up/Down, Home/End, and Ctrl+C behave like every other desktop text field; no fragment is written). `vi` renders the modal `v`/`y` selection flow for vi/vim muscle memory. Not federation-syncable -- it renders to a file on this host. |
| `input_enabled` | `false` | Global opt-in for `POST /api/sessions/{name}/input` (typing into sessions over the API). **RCE by design** — `false` makes the endpoint a hard 403. **Local-file-only**: can ONLY be set by editing `settings.json` on disk — deliberately not settable via `PATCH /api/settings` (a Bearer-key holder must not be able to self-authorize input) and not federation-syncable. |
| `input_allowed_sessions` | `[]` | **Glob patterns** (matched case-INsensitively — both name and pattern are `.casefold()`-ed before `fnmatch.fnmatchcase`, so behavior is deterministic across platforms) naming sessions that may receive API terminal input, e.g. `["*"]` for all sessions, `["amplifier-*"]` for a prefix family, or an exact name (matches only itself). A session matching none of the patterns is a 403 even when `input_enabled` is true — this is how your own working panes stay un-typeable. Empty list = deny everything. **Local-file-only**: can ONLY be set by editing `settings.json` on disk — deliberately not settable via `PATCH /api/settings` and not federation-syncable. |
| `tmux_socket_dir` | `""` | Override tmux's socket directory (maps to `TMUX_TMPDIR`). Set this if your tmux sessions live somewhere other than `/tmp/tmux-$UID` (e.g. a custom `TMUX_TMPDIR` in your shell rc) -- a systemd/launchd service does not inherit your login shell's environment, so without this the service can't see sessions created with a custom socket directory. |
| `focus_app` | `""` | **macOS only.** The `.app` bundle name `POST /api/focus` runs `open -a` against to bring the muxplex PWA window to the foreground on this host. Empty = unconfigured (the endpoint returns `409` rather than silently doing nothing). **Wayland and Windows are not supported** -- Wayland has no portable activation path a headless server process can use; Windows has no muxplex port at all (see `docs/API_SEMANTICS.md`'s `POST /api/focus` section for the full platform table). **Local-file-only**: can ONLY be set by editing `settings.json` on disk -- not settable via `PATCH /api/settings` and not federation-syncable. |
| `device_name` | `""` (hostname) | Display name for this device |
| `federation_key` | `""` | Server-to-server authentication key for federation |
| `remote_instances` | `[]` | Remote muxplex instances to aggregate |
| `multi_device_enabled` | `false` | Enable multi-instance federation |
| `tls_cert` | `""` | Path to TLS certificate file (empty = HTTP) |
| `tls_key` | `""` | Path to TLS private key file (empty = HTTP) |
| `fontSize` | `14` | Terminal and tile preview font size (px) |
| `hoverPreviewDelay` | `1500` | Hover preview popup delay (ms) |
| `gridColumns` | `"auto"` | Number of grid columns (`"auto"` or integer) |
| `bellSound` | `false` | Play audio sound on terminal bell |
| `viewMode` | `"auto"` | Grid tile sizing: `auto` or `fit` |
| `deviceLabelPlacement` | `"titlebar"` | Where a session's device label is drawn: `titlebar` (in the tile/sidebar header, the default), `corner` (inside the preview, anchored lower right), or `off` (not drawn). Presentation only — views store device-qualified keys, so session identity is unaffected. Honored by the PWA only; the soft deck and the `muxplex-deck` sidecar draw no device label at all. |
| `showDeviceBadges` | `true` | **Derived — do not edit.** Maintained by the server as `deviceLabelPlacement != "off"`. Retained so pre-v0.36 clients keep working; set `deviceLabelPlacement` instead. |
| `showHoverPreview` | `true` | Show hover preview popover on tile hover |
| `activityIndicator` | `"both"` | Activity style: `none`, `glow`, `dot`, `both` |
| `gridViewMode` | `"flat"` | Multi-device grid layout: `flat`, `grouped`, `filtered` |
| `sidebarOpen` | `null` | Sidebar state: `true`, `false`, or `null` (auto-detect from screen width) |
| `settings_updated_at` | `0.0` | Unix timestamp of last settings write (used for federation sync) |
| `views_updated_at` | `0.0` | Unix timestamp of last change to `views`/`hidden_sessions` specifically. Metadata like `settings_updated_at`, used to arbitrate views-specific federation sync conflicts independently of unrelated field changes (e.g. a `fontSize` edit no longer bumps this). Not itself a syncable setting -- see `docs/API_SEMANTICS.md`. |

**Priority:** CLI flags > `settings.json` > defaults.

> **→ Writing something that drives muxplex?** The rows above define
> `input_enabled` / `input_allowed_sessions` as *configuration*. For the
> operational side — auth, the read endpoints, session lifecycle, the terminal-input
> contract and its threat model, and copy-pasteable `curl` examples — see
> [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md). It's vendor-neutral: point any agent
> or script at it.

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+Shift+C | Copy terminal selection to system clipboard |
| Cmd+V / Ctrl+Shift+V | Paste from system clipboard (native browser paste) |
| Ctrl+F | Open terminal search bar |
| Enter / Shift+Enter | Next / previous search match |
| Ctrl+Click (Cmd+Click) | Open URL in new tab |
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

`POST /api/focus` (foreground-focus for the muxplex PWA window) is
**macOS-only**. Linux/X11 is unreliable (a systemd user service doesn't
reliably carry `DISPLAY`), Wayland cannot work at all (no portable
activation path a headless server process can use), WSL has a Windows
browser window to raise rather than a Linux one, and Windows has no
muxplex port at all. See `docs/API_SEMANTICS.md`'s `POST /api/focus`
section for the full per-platform table.

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

## Using muxplex with Amplifier (optional)

muxplex does **not** depend on [Amplifier](https://github.com/microsoft/amplifier) — Amplifier is just one optional way to drive it. A behavior bundle at [bkrabach/amplifier-bundle-muxplex](https://github.com/bkrabach/amplifier-bundle-muxplex) lets AI agents list, create, and monitor muxplex sessions with the server URL, federation key, and CA trust resolved for you instead of per-consumer.

Install it **as a behavior bundle at the app level** — added to your own Amplifier app/bundle configuration. It is not something muxplex itself pulls in.

**→ See [docs/AMPLIFIER.md](docs/AMPLIFIER.md)** for setup. Driving the raw API without Amplifier? [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md).

---

## License

MIT

# CLI & Service Management Refactor Design

## Goal

Refactor the muxplex CLI so that `settings.json` is the single source of truth for serve options, replace `install-service` with a `muxplex service <command>` subcommand group, and clean up the CLI structure.

## Background

Three problems with the current CLI:

1. **Serve options are CLI-only.** `host`, `port`, `auth`, and `session-ttl` exist as argparse flags but aren't in `settings.json`. The systemd unit file runs `muxplex` with whatever flags were baked into `ExecStart` at install time — currently no `--host` flag, so it defaults to `127.0.0.1`. A service that only listens on localhost is useless. The macOS launchd plist hardcodes `--host 0.0.0.0`, creating a platform inconsistency.

2. **Service management requires copy-pasting platform commands.** After `muxplex install-service`, the user gets a wall of `systemctl --user daemon-reload && systemctl --user enable --now muxplex` or `launchctl bootstrap gui/{uid} ...` that they have to manually run. There's no `muxplex` command to stop, restart, check status, view logs, or uninstall the service.

3. **CLI structure has minor warts.** `upgrade` and `update` are separate subparsers with duplicated `--force` arguments instead of using argparse aliases. The `serve` subparser exists but doesn't accept any flags (the flags live on the root parser).

## Approach

Follow the **caddy model** (COE-approved): config file is the source of truth, service file is one line, no desync possible. Service management wrappers are thin — 3-5 lines of Python calling `systemctl --user` or `launchctl` directly, not an abstraction layer.

---

## Architecture

### Config File as Source of Truth

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────┐
│  CLI flags      │──┐  │  settings.json        │     │  Hardcoded  │
│  (one-time      │  │  │  (~/.config/muxplex/) │     │  defaults   │
│   override)     │  │  │  (persistent config)  │     │  (code)     │
└─────────────────┘  │  └──────────────────────┘     └─────────────┘
                     │           │                         │
                     ▼           ▼                         ▼
              ┌─────────────────────────────────────────────────┐
              │  Precedence: CLI flag > settings.json > default │
              └─────────────────────────────────────────────────┘
                                     │
                                     ▼
                            ┌────────────────┐
                            │  muxplex serve │
                            │  (FastAPI app)  │
                            └────────────────┘
```

### Service File Simplification

Before (Linux):
```
ExecStart=/usr/bin/python3 -m muxplex                    ← no host/port
```

Before (macOS):
```xml
<string>muxplex</string>
<string>--host</string>
<string>0.0.0.0</string>                                 ← hardcoded flag
```

After (both platforms):
```
ExecStart={muxplex_bin} serve                            ← reads settings.json
```

---

## Components

### 1. Extended `settings.json` Schema

Add `host`, `port`, `auth`, and `session_ttl` to `DEFAULT_SETTINGS` in `settings.py`:

```json
{
  "host": "127.0.0.1",
  "port": 8088,
  "auth": "pam",
  "session_ttl": 604800,
  "new_session_template": "tmux new-session -d -s {name}",
  "delete_session_template": "tmux kill-session -t {name}",
  "default_session": null,
  "sort_order": "manual",
  "hidden_sessions": [],
  "window_size_largest": false,
  "auto_open_created": true
}
```

The existing `load_settings()` / `save_settings()` / `patch_settings()` functions already merge saved values over defaults and ignore unknown keys — no structural changes needed. Just add the four new keys to `DEFAULT_SETTINGS`.

### 2. `serve()` Config Resolution

The `serve()` function in `cli.py` currently accepts `host`, `port`, `auth`, `session_ttl` as direct parameters from argparse. Refactor to:

1. Load settings from `~/.config/muxplex/settings.json` via `load_settings()`
2. Override with any CLI flags that were explicitly passed (not argparse defaults)
3. Pass resolved values to uvicorn / the FastAPI app

To distinguish "user passed `--port 8088`" from "argparse default `8088`", use argparse `default=None` for all serve flags. If the value is `None`, fall back to settings.json, then to hardcoded default.

```python
def serve(host=None, port=None, auth=None, session_ttl=None):
    settings = load_settings()
    host = host or settings.get("host", "127.0.0.1")
    port = port or settings.get("port", 8088)
    auth = auth or settings.get("auth", "pam")
    session_ttl = session_ttl if session_ttl is not None else settings.get("session_ttl", 604800)
    # ... start uvicorn
```

### 3. `muxplex service <command>` Subcommand Group

Replace `install-service` with:

| Command | Description |
|---|---|
| `muxplex service install` | Write systemd unit / launchd plist + enable + start |
| `muxplex service uninstall` | Stop + disable + remove service file |
| `muxplex service start` | Start the service |
| `muxplex service stop` | Stop the service |
| `muxplex service restart` | Stop + start |
| `muxplex service status` | Show running/stopped + PID + port |
| `muxplex service logs` | Tail the service log |

Each is a thin wrapper — 3-5 lines calling the platform's native service commands.

Platform detection: `sys.platform == 'darwin'` → launchd, else → systemd.

#### Linux (systemd) implementations

```python
# install
def _systemd_install():
    _write_systemd_unit()
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "--now", "muxplex"])

# uninstall
def _systemd_uninstall():
    subprocess.run(["systemctl", "--user", "stop", "muxplex"])
    subprocess.run(["systemctl", "--user", "disable", "muxplex"])
    unit_path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"])

# start / stop / restart
def _systemd_start():
    subprocess.run(["systemctl", "--user", "start", "muxplex"])

def _systemd_stop():
    subprocess.run(["systemctl", "--user", "stop", "muxplex"])

def _systemd_restart():
    subprocess.run(["systemctl", "--user", "restart", "muxplex"])

# status
def _systemd_status():
    subprocess.run(["systemctl", "--user", "status", "muxplex", "--no-pager"])

# logs
def _systemd_logs():
    subprocess.run(["journalctl", "--user", "-u", "muxplex", "-f"])
```

#### macOS (launchd) implementations

```python
# install
def _launchd_install():
    _write_launchd_plist()
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)])

# uninstall
def _launchd_uninstall():
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"])
    plist_path.unlink(missing_ok=True)

# start
def _launchd_start():
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)])

# stop
def _launchd_stop():
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"])

# restart
def _launchd_restart():
    _launchd_stop()
    _launchd_start()

# status
def _launchd_status():
    result = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], ...)
    # Parse and display: running/stopped, PID, exit status

# logs
def _launchd_logs():
    subprocess.run(["tail", "-f", "/tmp/muxplex.log"])
```

### 4. Service File Templates (No CLI Flags)

**systemd unit:**
```ini
[Unit]
Description=muxplex — web-based tmux session dashboard
After=network.target

[Service]
Type=simple
ExecStart={muxplex_bin_or_python} serve
Restart=on-failure
RestartSec=5s
Environment=PATH={safe_path}

[Install]
WantedBy=default.target
```

**launchd plist:**
```xml
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
        <string>{homebrew_paths}:{base_path}</string>
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
```

No `--host 0.0.0.0` or other flags. The service runs `muxplex serve` which reads `settings.json`. Users configure via:
- `settings.json` directly
- The Settings UI in the browser (Server tab — future)
- `muxplex serve --host 0.0.0.0` for one-time override

### 5. Backward Compatibility

`muxplex install-service` remains as an alias for `muxplex service install` with a deprecation notice:

```
⚠ 'muxplex install-service' is deprecated. Use 'muxplex service install' instead.
```

Remove after 2-3 releases.

### 6. CLI Help Cleanup — `upgrade` / `update` Alias

Currently `upgrade` and `update` are separate subparsers with duplicated `--force` arguments. Replace with a single parser using argparse aliases:

```python
upgrade_parser = sub.add_parser(
    "upgrade",
    aliases=["update"],
    help="Upgrade muxplex to latest version and restart service",
)
upgrade_parser.add_argument("--force", action="store_true", ...)
```

This shows a single help line instead of two.

---

## Data Flow

### Serve startup

```
1. argparse parses CLI → args.host (None if not passed), args.port, etc.
2. load_settings() reads ~/.config/muxplex/settings.json
3. Resolve: CLI flag if not None → settings.json value → hardcoded default
4. os.environ.setdefault() for MUXPLEX_PORT, MUXPLEX_AUTH, MUXPLEX_SESSION_TTL
5. uvicorn.run(app, host=resolved_host, port=resolved_port)
```

### Service install

```
1. Detect platform (darwin vs linux)
2. Resolve muxplex binary path (shutil.which("muxplex") or sys.executable + "-m muxplex")
3. Write service file with "muxplex serve" (no flags)
4. Enable + start service
5. Prompt about host setting if currently 127.0.0.1
```

### Service lifecycle (user perspective)

```
$ muxplex service install
  Service installed and started.
  Note: host is 127.0.0.1 (localhost only). Set to 0.0.0.0 for network access? [Y/n]
  → Settings updated: host = 0.0.0.0
  → Service restarted on 0.0.0.0:8088

$ muxplex service status
  muxplex: running (PID 12345)
  Listening: 0.0.0.0:8088
  Uptime: 2h 15m

$ muxplex service logs
  [tails journalctl or /tmp/muxplex.log]

$ muxplex service stop
  Service stopped.

$ muxplex service uninstall
  Service stopped, disabled, and removed.
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| `service start` when already running | Print "already running" + current PID, exit 0 |
| `service stop` when not running | Print "not running", exit 0 |
| `service install` when already installed | Overwrite service file, restart. Print "reinstalled" |
| `service uninstall` when not installed | Print "not installed", exit 0 |
| `service logs` with no log output | `journalctl` or `tail` handles this natively |
| `settings.json` missing at serve time | Use hardcoded defaults (existing behavior) |
| `settings.json` corrupt JSON | Use hardcoded defaults, print warning (existing behavior) |
| Invalid `host` or `port` in settings | Uvicorn fails with its own error — no extra validation needed |

---

## Testing Strategy

### Unit tests

- **Config resolution precedence:** Verify CLI flag > settings.json > default for each of `host`, `port`, `auth`, `session_ttl`. Use monkeypatch to set `SETTINGS_PATH` to a temp file.
- **Settings schema:** Verify `load_settings()` returns new keys (`host`, `port`, `auth`, `session_ttl`) with correct defaults when file is missing/empty.
- **Backward compat:** Verify old settings.json files (without new keys) load correctly with defaults filled in.

### Service command tests

- **Install:** Mock `subprocess.run`, verify correct systemd/launchd commands called per platform.
- **Uninstall:** Mock subprocess, verify stop + disable + file removal.
- **Start/stop/restart/status/logs:** Mock subprocess, verify correct platform commands.
- **Deprecation alias:** Verify `install-service` calls `service install` and prints deprecation warning.

### CLI structure tests

- **Argparse:** Verify `upgrade` and `update` both route to the same handler.
- **Serve flags:** Verify `--host`, `--port`, `--auth`, `--session-ttl` are accepted on both root parser and `serve` subparser.
- **Default behavior:** Verify bare `muxplex` (no subcommand) calls `serve()`.

### Integration test

- Start `muxplex serve --port 9999` → verify it listens on 9999 (CLI override).
- Write `{"port": 7777}` to settings.json → start `muxplex serve` → verify it listens on 7777 (config file).
- Write `{"port": 7777}` to settings.json → start `muxplex serve --port 9999` → verify it listens on 9999 (CLI wins).

---

## Files to Modify

| File | Changes |
|---|---|
| `muxplex/settings.py` | Add `host`, `port`, `auth`, `session_ttl` to `DEFAULT_SETTINGS` |
| `muxplex/cli.py` | Major refactor: `serve()` reads config, `service` subcommand group, `upgrade` aliases, `install-service` deprecation, argparse restructure |
| `muxplex/main.py` | Accept serve options from resolved config instead of reading env vars directly (minor) |
| `tests/test_cli.py` | New tests for config resolution, service commands, deprecation alias |
| `tests/test_settings.py` | Tests for new default keys |

---

## Open Questions

1. **Should `muxplex service install` auto-set host to `0.0.0.0`?** A service listening on localhost only is useless. Recommendation: prompt the user — "Current host is 127.0.0.1 (localhost only). Set to 0.0.0.0 for network access? [Y/n]". Default yes. This writes to `settings.json` so the service picks it up.

2. **Should `muxplex serve --host 0.0.0.0` save to settings.json?** Recommendation: no — CLI flags are one-time overrides. Persistent changes go through settings.json directly, the Settings UI, or a future `muxplex config set` command. Keeps the mental model simple.

3. **Should `serve` flags live on the root parser or the `serve` subparser?** Currently they're on root (so `muxplex --host 0.0.0.0` works). Keep them on both — root parser for convenience, `serve` subparser for explicitness. Both route to the same `serve()` function.

# Changelog

## v0.1.1 (2026-04-07)

### Features
- **TLS/HTTPS support** — `muxplex setup-tls` auto-detects Tailscale → mkcert → self-signed certificates
- **TLS nudge** in `doctor` and `service install` when clipboard requires HTTPS
- **Session device selector** — create sessions on remote devices when multi-device enabled
- **Activity count in page title** — browser tab shows `(2) hostname - muxplex` for unseen bells
- **Favicon activity badge** — amber dot overlay on favicon for unseen notifications
- **Terminal search** — Ctrl+F to search scrollback (xterm-addon-search)
- **Clickable URLs** — Ctrl+Click / Cmd+Click opens URLs in terminal output (xterm-addon-web-links)
- **Inline image rendering** — Sixel and iTerm2 graphic protocols (xterm-addon-image)

### Bug Fixes
- **Federation SSL** — federation client accepts self-signed TLS certificates on remote instances
- **Federation empty key** — skip Authorization header when federation key is empty
- **Federation WebSocket SSL** — WebSocket proxy accepts self-signed certs on wss:// remotes
- **Remote session connect** — terminal reconnect uses federation connect path for remote sessions
- **Remote session restore** — persist `active_remote_id` in state for page refresh restore
- **Bell clearing for remote sessions** — federation bell-clear endpoint + unique sessionKey
- **Service crash-loop prevention** — kill stale port holders on startup, TimeoutStopSec in systemd
- **UTF-8 terminal display** — decode WebSocket output with TextDecoder before xterm.js write
- **Clean clipboard handling** — removed custom paste handlers per COE review, native xterm.js paste
- **Guard empty session name** — openSession bails on empty name from unreachable federation tiles
- **Clean Ctrl+C exit** — `muxplex service logs` exits cleanly on keyboard interrupt

### Infrastructure
- **PyPI publish** — available as `pip install muxplex`
- **GitHub Actions CI** — tests run on push/PR (Python 3.11-3.13)
- **Self-hosted vendor libs** — eliminates Edge Tracking Prevention console noise

## v0.1.0 (2026-04-04)

Initial release.

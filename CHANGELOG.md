# Changelog

## v0.3.3 (2026-04-13)

### Bug Fixes
- **iOS/iPadOS touch scrolling** — fix touch scroll handling for Safari on iOS and iPadOS devices (PR #4, @samueljklee)

## v0.3.2 (2026-04-09)

### Bug Fixes
- **Hidden sessions filter now applies to federated sessions** -- hiding a session now hides it everywhere (local and remote), completing the federation-aware hidden sessions feature

## v0.3.1 (2026-04-08)

### Bug Fixes
- **Federation auth stale key** -- the auth middleware now reads the federation key fresh from disk on each request instead of caching it at startup; key generation and rotation no longer require a server restart
- **Settings sync silent push failures** -- the PUT response from `/api/settings/sync` is now checked; 409 (remote newer) is handled gracefully, other errors are logged

## v0.3.0 (2026-04-08)

### Features
- **Federation settings sync** -- user preferences (font size, sort order, hidden sessions, etc.) now sync across all connected muxplex servers using a P2P last-write-wins protocol with per-server timestamps; offline servers catch up automatically on reconnect
- **Heartbeat-driven bell clearing across federation** -- viewing a remote session now clears its activity bell on the remote server automatically; no more stale activity indicators for federated sessions

### Bug Fixes
- **`remoteId: 0` falsy bug** -- sessions from the first remote instance were incorrectly subject to the hidden-sessions filter due to a JavaScript falsy-0 check; fixed `!s.remoteId` to `s.remoteId == null`
- **Browser indicators ignore hidden sessions** -- tab title `(N)` count and favicon activity badge now filter through `getVisibleSessions()` so hidden sessions don't contribute to activity counts

### API
- **`GET /api/settings/sync`** -- returns syncable settings + timestamp for federation sync (Bearer token auth)
- **`PUT /api/settings/sync`** -- accepts synced settings; applies if incoming timestamp is newer (200), rejects if older (409 with local state)

## v0.2.0 (2026-04-08)

### Features
- **Server-side settings consolidation** -- all display preferences (font size, grid columns, hover delay, view mode, device badges, hover preview, activity indicator, grid view mode, sidebar state) moved from browser localStorage to server-side `settings.json`; settings now survive browser clears and are consistent per-server
- **Federation session deletion** -- kill sessions on remote devices from any muxplex client
- **Session creation error reporting** -- replaced fire-and-forget subprocess with async process that checks exit codes, surfaces stderr, and pre-flight checks the command binary on PATH
- **TTY-attach resilience** -- session commands that exit non-zero but still create the tmux session (e.g. `amplifier-workspace` which tries to attach after create) are detected and treated as success

### Bug Fixes
- **Federation key preservation on URL edit** -- editing a remote instance URL (e.g. `http://` to `https://`) no longer erases the federation key; added position-based fallback alongside the existing URL-based key restoration
- **PWA manifest auth bypass** -- added `.json` to the static extension allowlist so `/manifest.json` is not auth-gated; previously produced "Syntax error" in the browser console
- **`auto_open` toggle** -- fixed three-way key mismatch (`auto_open` vs `auto_open_created`) that made the auto-open setting completely non-functional
- **Session enumeration crash** -- `enumerate_sessions()` now catches `FileNotFoundError` when the session command binary is missing from PATH, preventing poll loop crashes
- **Settings PATCH key leak** -- the `PATCH /api/settings` response now redacts sensitive keys, matching the existing `GET /api/settings` behavior
- **Federation 503 diagnostics** -- all federation proxy 503 errors now include the exception type and message instead of just the remote URL
- **FastAPI version string** -- corrected the hardcoded `version` in the FastAPI app from `0.1.0` to match the release

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

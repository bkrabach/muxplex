# Changelog

## v0.5.0 (2026-05-06)

### Features
- **`muxplex setup-tls --method ca`** — generate a persistent local Certificate Authority and sign a 13-month leaf TLS certificate with it. Install the CA once on each client device to get browser-trusted HTTPS for plain LAN names (`my-host`, `192.168.1.5`) without requiring Tailscale on every client and without buying a public domain. The CA persists across regenerations, so leaf rotation does **not** require re-trusting on clients. The leaf SAN auto-discovers the host's primary outbound LAN IPv4 address and the Tailscale MagicDNS name (when Tailscale is connected), in addition to the existing `<hostname>`, `<hostname>.local`, `localhost`, `127.0.0.1`, and `::1` entries. The CA cert has proper `BasicConstraints CA:TRUE pathlen:0` and `KeyUsage keyCertSign+cRLSign` extensions, so OS / browser trust stores accept it cleanly as a Root.
- **PWA install reliability** — the `ca` method specifically addresses the symptom where an installed PWA with a self-signed-cert origin gets kicked back into a regular browser tab on relaunch. With the CA installed in the OS trust store, the PWA shell stays in standalone mode across reopens.
- **New documentation** — [`docs/TRUSTING_THE_LOCAL_CA.md`](docs/TRUSTING_THE_LOCAL_CA.md) walks through CA install on Windows (PowerShell, no admin), macOS (`security` CLI), Linux (`update-ca-certificates` / `update-ca-trust`), iOS (Profile + Trust Settings), Android, and Firefox (separate trust store).

### API
- **`muxplex.tls.generate_local_ca(ca_cert_path, ca_key_path, days_valid=3650)`** — idempotent CA generator. Reuses the existing CA if both files exist; generates a new one otherwise. Returns metadata including a `regenerated` boolean.
- **`muxplex.tls.generate_leaf_signed_by_ca(ca_cert_path, ca_key_path, leaf_cert_path, leaf_key_path, hostnames, ip_addresses=None, days_valid=397)`** — generates a leaf TLS cert signed by an existing local CA. Builds proper `KeyUsage`, `ExtendedKeyUsage serverAuth`, `SubjectKeyIdentifier`, and `AuthorityKeyIdentifier` extensions, plus `SubjectAlternativeName` from the supplied DNS + IP lists.
- **`muxplex.tls._default_lan_ip()`** — returns the primary outbound IPv4 address (no actual packets sent; uses a connected UDP socket to ask the kernel which interface would route external traffic). Returns `None` on failure.
- **`muxplex.tls._default_tailnet_name()`** — returns the host's MagicDNS name from `tailscale status --self --json`, or `None` if Tailscale is unavailable / disconnected. Best-effort with a 5-second timeout.

## v0.3.5 (2026-04-14)

### Bug Fixes
- **Connection pool exhaustion fix** — replaced `setInterval` with self-scheduling `setTimeout` for both `pollSessions` and `sendHeartbeat` loops; prevents `ERR_INSUFFICIENT_RESOURCES` death spiral when federation requests time out during 2-second poll cycles

## v0.3.4 (2026-04-13)

### Bug Fixes
- **Zero-session devices visible** — devices with no tmux sessions now show a "No sessions" status tile instead of being invisible
- **Flapping prevention** — server-side cache of last-known-good federation results per remote; returns cached sessions for up to 3 consecutive failures before marking unreachable
- **Status tiles show device name** — offline/unreachable tiles display the device name instead of blank (was passing session.name which is undefined for status entries)
- **Status entries filtered from session list** — unreachable/auth_failed entries no longer render as blank session tiles in dashboard or sidebar
- **remoteId=0 falsy bug in mobile sheet** — first remote instance (index 0) now works correctly in the mobile bottom sheet session switcher

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

# Changelog

## v0.15.0 (2026-07-26)

### Bug Fixes

- **Refuse to terminate a healthy muxplex when taking the port.** `_kill_stale_port_holder()` ran on every `muxplex serve` startup, called `lsof -ti :<port>`, and SIGTERMed whatever it found — unable to distinguish a stale holder from a healthy running server. Any second invocation of the startup path silently killed the live service, producing a clean graceful shutdown with no crash and no systemd `Stopping` line: nearly undiagnosable from logs. It now probes the holder via `GET /api/instance-info` first and kills only on positive evidence the holder is not serving; a healthy holder yields an actionable error naming the port and PID, and exit 1 instead of starting. `--force-take-port` restores the old unconditional behavior. A missing or erroring `lsof` still never blocks startup. The restart race is bounded: if an old instance is still draining, the new process exits non-zero and systemd retries after `RestartSec`.

- **Never accept() a WebSocket the client already abandoned.** `terminal_ws_proxy` performs real awaited work before accepting — killing and respawning ttyd, then waiting for it to bind. If the browser disconnected during that window, uvicorn's `connection_lost()` had already flipped its handshake-complete flag, so the subsequent `accept()` raised `RuntimeError: Expected ASGI message 'websocket.send' or 'websocket.close', but got 'websocket.accept'`, several times per hour in production. The wait is now raced against a disconnect watcher and `accept()` is skipped entirely when the client is already gone. Notably this only manifests on uvicorn's newer sansio WebSocket implementation, which is what a fresh `uv tool install` resolves — see Internal.

### Internal

- **Test-suite safety rails.** Running the suite on a host also serving muxplex destroyed real state twice in one day: a test overwrote a live `~/.config/muxplex/settings.json`, and six tests SIGTERMed the running server. Both were invisible from inside the suite, because a test that damages its host still passes. Four rails now close the class: a `pytest_sessionstart` guard that refuses to run when anything serves the default port, autouse isolation of `SETTINGS_PATH`, autouse neutering of `_kill_stale_port_holder` behind an explicit `@pytest.mark.allow_real_port_killer` opt-in, and `test_safety_rails.py` pinning all of it so removing a rail fails loudly. `make test` now runs the suite inside a Digital Twin Universe container, making the safe path the default path.

- **Dependency-drift CI job.** `uv.lock` pinned uvicorn 0.42.0 while a fresh `uv tool install` resolves 0.51.0 — and the WebSocket bug above existed only on the newer sansio implementation, so CI was green for weeks while production threw errors hourly. A new `test-latest-deps` job installs the way users install, ignoring the lockfile, so this class of drift fails CI instead of hiding.

## v0.14.0 (2026-07-25)

### Features

- **TLS bootstrap endpoint: GET /api/ca** — Clients that verify TLS against muxplex's locally-generated CA previously had no programmatic way to obtain it without SSH access, and the most natural approach (pointing ca_file at the server's own certificate) is reliably wrong: the server presents only the leaf certificate on the wire, producing "unable to get local issuer certificate" when a client tries to validate against it. GET /api/ca now returns the CA certificate PEM, unauthenticated (added to _AUTH_EXEMPT_PATHS alongside /api/instance-info) — a trust anchor is not a secret, and requiring auth would be circular since a client cannot authenticate over TLS it does not yet trust. The path is derived internally from settings.get_local_ca_cert_path(); no client-supplied path, query, or header reaches the filesystem. tls.get_local_ca_cert_bytes() fails closed: missing files, unreadable paths, unparseable content, missing BasicConstraints extension, or CA:FALSE all return None → 404 with a helpful detail. Never serves private key material. Addresses a recurrent TLS onboarding friction point.

## v0.13.0 (2026-07-25)

### Bug Fixes

- **Test-suite pollution of production settings** — `test_get_state_settings_updated_at_changes_after_settings_write` never redirected `SETTINGS_PATH` to a tmp directory, so running the test suite on a machine hosting a live muxplex instance read and wrote the real `~/.config/muxplex/settings.json`. On the dev box this repeatedly overwrote a production 8-view configuration with test fixture data — the exact `{"name": "Focus", "sessions": ["alpha"]}` payload from test_api.py that had to be recovered from backups. Test now monkeypatches SETTINGS_PATH into tmp_path like all sibling settings-writing tests. Also fixed three stale assertions in test_frontend_js.py that were matching literal `api('PATCH'` strings inside function bodies; v0.11.0 correctly refactored those call sites to route through `patchSettingsGuarded()`. Assertions now verify the guarded call; a new pinning test ensures end-to-end coverage of `/api/settings` is preserved rather than silenced. These three were the only real CI failures across 9 GitHub Actions job runs (3 commits × 3 Python versions).

- **Federation-aware stale-key pruning** — `prune_stale_keys()` built its live_keys set from local sessions only, while views entries are canonical `device_id:name` and routinely reference sessions on other devices. Every peer saw every other device's keys as dead and deleted them after grace period, then LWW-synced the deletion fleet-wide — a latent mutual eraser. Now the pruner takes optional `local_device_id` and `known_remote_device_ids`, and only prunes keys where the owning device's sessions are actually known: own-device keys are always evaluable, remote keys only when that device is currently reachable (checked via existing `_federation_cache`, gated on the same `fail_count` threshold used by `/api/federation/sessions`). Devices unreachable or unknown have their grace period clock reset (not paused) — a laptop offline for a week starts fresh when it returns instead of getting pruned on arrival. Legacy bare-name entries keep existing local evaluation. Also closed a gap: the poll-cycle prune bypassed v0.12.0's destructive-write backstop; it now runs `assess_views_destruction()` and refuses to persist if a mass prune would collapse views. Default-None params preserve legacy behavior exactly. 1480 tests passed; 435 frontend.

## v0.12.0 (2026-07-25)

### Bug Fixes

- **Catastrophic views destruction via stale clients and federation sync** — A PWA tab holding an hours-old snapshot of the settings blob could send 12 PATCHes in 7 minutes resubmitting a collapsed `views` array (8 views → 1), destroying your configuration; simultaneously an older federation peer could delete view members not stored locally, then LWW-broadcast the loss to every device. Root cause: `settings_updated_at` is a single scalar covering the whole syncable blob (views, sidebarOpen, fontSize, etc.), views is replaced wholesale (never merged) by both `patch_settings()` and `apply_synced_settings()`, and any PATCH re-stamps that timestamp — so a stale views value + unrelated fontSize write + federation race = cascade failure. Four defenses: (1) **Destructive-write backstop** — a single choke point (`assess_views_destruction()`) rejects any write that collapses >1 view to ≤1, removes ≥50% of views, or removes ≥50% of total members; returns 409 with `backstop:true` and makes NO write. Protects spark-1 fleet-wide even from unupgraded 0.6.7 peers and live stale browser tabs. Single-view and single-member edits stay far below thresholds. (2) **Federation sync now runs the backstop** and gains CAS discipline it previously lacked (`PUT /api/settings/sync` now uses the same optimistic concurrency as PATCH, never overwrites destructively). Deliberately NO force override on sync — only local config file edits can collapse a view now. (3) **Separate `views_updated_at`** — advances only when `views` or `hidden_sessions` actually change, so unrelated field writes can no longer re-arm a stale views value in a race. Absent on legacy peers (v0.6.7 compat); they fall back to prior behavior still gated by the backstop. (4) **PWA re-fetches settings before views mutations** instead of trusting cached blob; on 409 treats backstop and CAS distinctly — backstop 409 reloads from server (stale client), CAS 409 retries once with fresh state (lost race).

### Features

- **Settings snapshot history** — Every write to settings creates a time-stamped backup in `<config_dir>/settings-history/` (20 most recent kept). Enables instant forensics and recovery; monotonic sequence numbering for coarse-grained ordering independent of clock skew.


## v0.11.0 (2026-07-25)

### Bug Fixes

- **Settings clobber protection via rotating snapshots and optimistic concurrency** — a browser tab holding a stale copy of the settings PATCHed it back wholesale and destroyed 7 of 8 views in a single request — recovered only because a manual file backup happened to exist. Two defenses added: (1) Rotating snapshots in `<config_dir>/settings-history/` (20 most recent kept, monotonic sequence numbering for coarse-grained ordering, best-effort so a snapshot failure never blocks the real write). All writers (API PATCH, federation sync, internal code) covered at the lowest choke point. (2) Optimistic concurrency: `PATCH /api/settings` accepts optional `expected_settings_updated_at`; on mismatch returns 409 with the current value and makes no write. Omitting the field preserves existing behavior (federation sync and other clients keep working). PWA now routes all 14 settings-writing call sites through `patchSettingsGuarded()` which sends the precondition, re-fetches on 409, re-applies the mutation to fresh state, and retries exactly once; a second conflict re-renders from server truth. Successful writes update the client's baseline timestamp.

### Features

- **tmux socket directory discoverability** — muxplex manages tmux on a configurable socket dir (e.g. `~/.tmux`) while tmux itself defaults to `$TMUX_TMPDIR` or `/tmp` — so any tool creating a session without that variable set lands on a DIFFERENT tmux server and its sessions are invisible to muxplex. That knowledge was tribal and cost real debugging time. Now discoverable: (1) `settings.resolve_tmux_socket_dir()` resolves configured value, then `$TMUX_TMPDIR`, then `/tmp/tmux-<uid>`; (2) `GET /api/instance-info` returns the resolved `tmux_socket_dir`; (3) new `muxplex env` subcommand prints exactly `export TMUX_TMPDIR="..."` on stdout (human notes to stderr) for `eval "$(muxplex env)"`, following the ssh-agent/direnv convention; (4) README gains a "tmux socket" section explaining the two-server hazard and the one-line fix.

## v0.10.0 (2026-07-25)

### Bug Fixes

- **PWA now follows remote view-membership changes** — when a session is added to or removed from a view via `PUT /api/views/{name}` (on the server or another device), the PWA's 1s poll now detects the change and re-renders all view-related UI (dropdown, grid filters, sidebar, membership checkboxes). Root cause was that `_serverSettings` was fetched once at page load and cached forever, while the session list itself refreshed every second via `/api/sessions` — so "All" updated but "Focus" did not. Fix adds `settings_updated_at` to `GET /api/state` as a change signal (render-only, not persisted), then wires a new `followRemoteViewDefinitions()` into the 1s poll alongside the existing session and active_view followers. Absent field on older servers is treated as no-op. Completes the follow-the-remote-device family: `active_session`, `active_view`, and `view_definitions`.

### Features

- **Case-insensitive glob patterns for `input_allowed_sessions`** — the remote-agent input endpoint's session allowlist now matches case-insensitive. `"Amplifier-*"` matches `amplifier-foo`, `"amplifier-*"` matches `AMPLIFIER-Foo`, exact entries like `"Agent-Sbx"` match `agent-sbx`. Implemented by explicit `casefold()` on both sides rather than `fnmatch.fnmatch` (which would be case-insensitive on macOS/Windows as a side effect of `os.path.normcase`, silently widening the fence per-platform). All other fence properties unchanged: empty list denies all, pattern order preserved, LOCAL-FILE-ONLY still enforced, boundary validation intact.


## v0.9.0 (2026-07-24)

### Features

- **Glob pattern support in `input_allowed_sessions`** — the remote-agent input endpoint's session allowlist now accepts glob patterns instead of exact literals. `"*"` allows every session; `"amplifier-*"` matches a prefix family; bare names like `"agent-sbx"` work exactly as before (backward compatible). Matching uses `fnmatch.fnmatchcase` (case-sensitive, deterministic across all platforms) rather than `fnmatch.fnmatch`, which is case-insensitive on macOS/Windows and would silently widen the fence. Fail-closed guarantees preserved: empty list denies all, non-list values normalize to deny-all before matching, non-string entries are skipped, and the fence ORDER is unchanged (is_valid_session_name 400 → input_enabled 403 → allowlist 403 → fail-closed existence 404). Patterns remain LOCAL-FILE-ONLY — not settable via PATCH /api/settings or federation sync. 15 new tests; 1426 pass.

## v0.8.0 (2026-07-24)

### Features

- **Remote-agent terminal input endpoint** — `POST /api/sessions/{name}/input` allows remote agents
  (which cannot reach tmux directly) to send keystrokes to running sessions and receive a read-back
  snapshot. The endpoint is RCE-by-design and ships with eight hardened fences: (1) global
  `input_enabled` opt-in defaulting to False; (2) per-session `input_allowed_sessions` exact-match
  allowlist defaulting to empty; (3) both keys are LOCAL-FILE-ONLY (excluded from SYNCABLE_KEYS,
  rejected by PATCH /api/settings) so neither federation peers nor Bearer-key agents can
  self-authorize; (4) fail-closed target matching (empty allowlist rejects all) plus
  `is_valid_session_name` boundary validation; (5) keystrokes sent via `tmux send-keys -l`
  (literal text, never shell) through argv subprocess, with named keys restricted to an explicit
  allowlist; (6) payload caps: 8KiB text / 64 key events per request; (7) audit logging (one
  redacted line per action, full text at debug); (8) ~400ms settle + capture_pane read-back so
  agents aren't typing blind. Injection-safety proven end-to-end against real tmux with a hostile
  `; touch` payload — it appeared literally in the pane and never executed. 39 new tests; 1412
  pass. **Deployed default-off** — no behavior change until an operator sets `input_enabled: true`
  and populates `input_allowed_sessions` on the host machine.

## v0.7.1 (2026-07-24)

### Bug Fixes

- **PWA now follows remote `active_view` changes** — when another device (a Stream
  Deck sidecar, an agent, or another browser tab) changes the active view via
  `PATCH /api/state {active_view}`, the PWA's 1s state poll now applies it and
  re-renders, instead of only reflecting the view this tab itself last set. New
  `followRemoteActiveView()` mirrors the existing `followRemoteActiveSession()`
  follow path; the remote-apply is render-only (no re-`PATCH`), so it cannot echo.
  This completes view-switch parity with session-switch: both now propagate to
  every surface.

## v0.7.0 (2026-07-24)

### Features

- **Per-session `last_activity_at` in `GET /api/sessions`** (#11) — exposed for local and
  federation sessions, derived from tmux `#{window_activity}` (deliberately not
  `#{session_activity}`, which freezes for unattended sessions). The PWA's "Recent" sort
  now reflects real activity.
- **`GET /api/view` — server-side resolved view** (#13) — returns the current view with
  membership/hidden filtering, the canonical needs-attention predicate, and sorting
  applied (`?sort=attention` for bells-first / active / recency ordering). New clients
  (Stream Deck sidecar, agents) consume the resolved view instead of re-porting the
  PWA's rules.

### Bug Fixes

- **Session-name shell-injection RCE closed** (#14, security) — session names are
  validated against a strict allowlist (`is_valid_session_name`) at create, delete, and
  connect; `shlex.quote` added as defense-in-depth; matching is fail-closed exact-match.
- **PWA now follows remote `active_session` changes** (#15) — switches made from another
  device (Stream Deck, another browser, an agent) move the sidebar highlight and
  re-attach the terminal automatically — no more stale terminal stuck on
  "Reconnecting…" until you interact.
- **Remote-driven session switch ~8.8s → ~0.7s** (#16) — session-follow now runs on a
  dedicated ~1s `/api/state` poll (decoupled from the slow federation fetch), with a
  same-session connect short-circuit. The frontend is now served with
  `Cache-Control: no-cache` (ETag revalidation) so deploys reach installed PWAs without
  manual cache-clearing, and startup logs the served `app.js` md5.
- **Federation circuit breaker** (#17) — a dead/unreachable federation remote no longer
  drags every `/api/federation/sessions` call to the full timeout (~5s → ~0.02s steady
  state): 3 consecutive connection failures skip the remote for 60s, then re-probe;
  per-remote timeout reduced to 2s. Reachable-but-erroring remotes still report their
  honest status.
- **Clean, fast shutdown** (#18) — SIGTERM now completes in ~0.5s instead of hanging 10s
  and being SIGKILLed by systemd: shutdown cancels the poll loop and WebSocket relays
  first, then kills ttyd, then closes the HTTP client; the terminal WS relay no longer
  blocks on a live ttyd reader.

### Docs

- **`AGENTS.md`** (#12) — conventions for agents and contributors: `/api/*` is a public
  control surface with multiple consumers, additive evolution, API-first-frontend-second,
  and scratch-instance testing gotchas.

## v0.6.10 (2026-07-13)

### Bug Fixes

- **tmux_socket_dir not honored by create_session and delete_session** — When a custom
  `tmux_socket_dir` was configured (to support non-default socket directories via
  `TMUX_TMPDIR`), the muxplex service's session create and delete operations were still
  hitting the default tmux socket location because the subprocess environment was not
  being passed the overridden socket settings. Fixed by wiring the shared `tmux_env()`
  helper (already in use for session enumeration) into the create and delete code paths,
  ensuring all subprocess calls honor the configured socket directory.

## v0.6.9 (2026-07-11)

### Bug Fixes

- **Custom tmux socket directories invisible to the muxplex service** — If a user sets
  `TMUX_TMPDIR` in their shell rc (e.g. to keep sockets out of the shared `/tmp`), the
  muxplex *service* process (systemd/launchd, which does not inherit the login shell's
  environment) silently fell back to tmux's compiled-in default (`/tmp/tmux-$UID`) and
  saw none of the user's real sessions, even though `muxplex doctor` (run interactively)
  reported them correctly. Added a `tmux_socket_dir` setting (default empty, fully
  backward compatible) and a shared `tmux_env()` helper, wired into both session
  enumeration (`sessions.py`) and terminal attach (`ttyd.py`), that overrides
  `TMUX_TMPDIR` and strips `$TMUX` (which otherwise takes priority over `TMUX_TMPDIR`
  when a process is itself a descendant of an attached tmux client) for tmux subprocess
  calls.

## v0.6.8 (2026-07-10)

### Bug Fixes

- **OSC 52 clipboard bridge mangled multi-byte UTF-8 characters** — Copying text out of a
  tmux session via the OSC 52 clipboard bridge (`set-clipboard on`) mangled box-drawing
  lines, bullets, em dashes, and emoji (e.g. "─" became "â", "•" became "â¢"). The
  handler decoded the base64 payload with plain `atob()`, which returns a "binary string"
  (one JS char per raw byte, effectively Latin-1) rather than a UTF-8-decoded string. This
  path was never covered by the earlier `ced0c62` WebSocket-output decode fix — mouse-select
  copy was already correct; the OSC 52 bridge was not. Fixed by re-wrapping the decoded
  bytes and running them through the same `TextDecoder` used for the primary output path.
  Added a regression test and fixed a pre-existing test-mock gap
  (`terminal-container` missing `addEventListener`) that was silently failing ~26
  unrelated tests.

## v0.6.4 (2026-05-17)

### Bug Fixes

- **Empty device block still showing in grouped grid view** — Remote federation devices with
  zero tmux sessions were producing a visible "No sessions" block in the grouped grid view.
  The v0.6.3 fix targeted `renderGroupedGrid` but missed the unconditional `status:empty`
  status-tile append in `renderGrid` itself.  In grouped mode, `status:empty` tiles are now
  suppressed (`auth_failed` and `unreachable` tiles still appear in all modes).

- **`muxplex update` fails when uv/pip is installed outside PATH** — On Unraid (root user),
  macOS (user installs), and snap-packaged systems, `shutil.which("uv")` returned None even
  though uv was present at `~/.local/bin/uv`, `/snap/bin/uv`, or `/root/.local/bin/uv`.
  New helpers `_find_uv()` / `_find_pip()` probe a curated list of known install locations
  after PATH lookup fails, so the upgrade flow works on stripped-PATH environments
  (systemd, launchd, non-login SSH shells).

- **`muxplex update` exit code propagation** — Tests added to confirm that a failed install
  exits with code 1 after the `try/finally` service-recovery block runs (behaviour was
  implemented in v0.6.2; regression test coverage added here).

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

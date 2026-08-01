# muxplex API semantics external clients re-implement

**Audience:** anyone writing or maintaining a client that talks to muxplex's HTTP
API — the bundled PWA, the
[muxplex-deck](https://github.com/bkrabach/muxplex-deck) Stream Deck sidecar,
federation peers, AI agents — and anyone changing the server-side fields those
clients depend on.

This file records the *semantics* behind the wire contract: the predicates,
timestamps, precondition rules, and write-path guards that clients currently
re-derive on their own, plus the incidents that produced each one. Silently
changing any of them breaks consumers in ways this repo's tests cannot see.

Related docs, and why they aren't this one:

| Doc | What it is |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | Conventions for **developing muxplex itself** — invariants a contributor must not break. |
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) | How to **drive** a running muxplex from outside — auth, endpoints, worked examples. Read that first if you are writing a client. |
| **this file** | **Why** those endpoints behave the way they do, and what must not change silently. |
| [`../README.md`](../README.md) | Human install/config reference. |
| `/openapi.json`, `/docs` | The machine-readable contract, served by the running instance. Authoritative for exact request/response shapes. |

Every claim below is checkable against the source; file references are given so
you can verify rather than trust.

---

## Semantics external clients re-implement today (change with care)

These rules are currently ported into clients; silently changing them breaks
consumers in ways this repo's tests won't catch:

- **Needs-attention (bell) predicate**:
  `unseen_count > 0 and (seen_at is None or last_fired_at > seen_at)`
- **View membership entries** are normalized to `"device_id:name"` form by the
  background normalization pass; clients match by the `":<name>"` suffix
  (tmux forbids `:` in session names).
- **`last_activity_at`** derives from tmux `#{window_activity}` — deliberately
  NOT `#{session_activity}`, which freezes for unattended sessions (rationale
  and empirical evidence documented in `sessions.py`).
- **`active_view` / `active_session` are server-global BY DEFAULT** — last
  writer wins, across every connected client (browsers, deck, agents),
  *unless* a device has opted into its own private **sync group** (see "Sync
  groups" below). What that means for a client author that sends no
  `device_id` (and why an agent should usually leave both alone) is in
  `AGENT_GUIDE.md` §4 — that guidance is still exactly true for such a
  client, because omitting `device_id` resolves to the shared `"global"`
  group, identical to today.
- **The read model is eventually consistent**: GET endpoints serve a ~2s poll
  cache. POST create/delete aren't visible until the next cycle, and `connect`
  on a just-created session 404s until the cache catches up — that 404 is the
  cache, not a failed create. The measured timings and the reference polling
  pattern (short interval, generous ceiling — **not** a flat `sleep`) live in
  `AGENT_GUIDE.md` §4, "The read model is eventually consistent". They are
  deliberately kept in exactly one place; don't restate the numbers here.
  (Candidate future fix: write-through cache refresh on create/delete.)
- **`GET /api/state` carries `settings_updated_at: float`**, merged in at
  request time from `settings.settings_updated_at` (settings.py) — it is
  NOT persisted in state.json; `empty_state()`/`load_state()`/`save_state()`
  are unaware of it (see `state.py`'s module docstring for the split).
  Purpose: any client already polling `/api/state` (PWA, muxplex-deck,
  agents) can detect a settings change — including view-membership edits
  made by another device, which are otherwise only visible via a dedicated
  `GET /api/settings` fetch — without adding a second poll. The PWA's
  `followRemoteViewDefinitions()` (`frontend/app.js`) is the reference
  consumer: it compares this timestamp against the last-seen value and only
  re-fetches `/api/settings` (via the existing `loadServerSettings()`) and
  re-renders view-dependent UI when it actually changed — an unchanged
  value is a no-op, so this does not become a second per-second settings
  fetch. This closed a real staleness bug: a session added to a view via
  `PATCH /api/settings` appeared immediately on the deck sidecar's own poll,
  but the PWA's `_serverSettings` cache — populated once at page load —
  never refreshed, so the view dropdown / filtered list / manage-view
  membership UI all stayed wrong until a hard reload. Same class of bug as
  `active_view` being server-global above, one layer deeper: that fix
  follows the active *selection*; this one follows the view *definitions*
  (membership data) themselves.

Preferred direction as semantics grow: move resolution **server-side** (e.g. a
resolved-current-view endpoint) rather than expecting each client to port more
logic — duplication across PWA/sidecar/agents is where drift bugs come from.

- **`GET /api/view`** is now the canonical server-side resolution of the
  above: view membership (via `filter_visible`), the needs-attention
  predicate (`bells.needs_attention`), and sort ordering (`?sort=attention`
  for tiered bell/active/recency ordering, or the default that mirrors
  `settings.sort_order`). New clients should prefer it over re-deriving
  these rules; local sessions only in v1.
- **`?sort=attention`'s non-bell tier orders by `bell.last_fired_at`, NOT
  `last_activity_at`** (`main.py`'s `_attention_order()`; mirrored in
  `frontend/app.js`'s `sortByAttention()` and re-implemented in
  muxplex-deck's `attention.py` -- all three must move together). Sessions
  that have never belled (`last_fired_at is None`) sort last within this
  tier, preserving incoming order among themselves (stable sort). This
  closed a real bug: `last_activity_at` derives from tmux
  `#{window_activity}`, which bumps on ANY pane output -- spinners,
  redraws, status-line clocks -- not just the agent-turn-completion event
  `attention` sort exists to surface. Keying tier 3 off it meant the grid
  reordered on essentially every ~2s poll cycle even when nothing the user
  cared about had happened. `bell.last_fired_at` only changes when a real
  bell fires, so ordering is now stable between bells -- the whole point of
  an "attention" sort. **`last_activity_at` itself is unchanged** and still
  the sort key for the `recent` sort mode; this fix touches ONLY the
  `attention` mode's tier-3 key.
- **`PATCH /api/settings` accepts an OPTIONAL `expected_settings_updated_at`
  precondition** (compare-and-swap). When present, it must equal the
  server's current `settings_updated_at` or the request is rejected with
  409 (body includes the current `settings_updated_at`) and NO write is
  made; when omitted, behavior is unchanged (existing clients, including
  federation sync, keep working without it). This closes a real incident:
  a PWA tab holding a STALE `_serverSettings.views` snapshot PATCHed the
  entire array back and destroyed 7 of 8 views in one request. The PWA's
  `patchSettingsGuarded()` (`frontend/app.js`) is the reference consumer —
  it attaches the precondition, and on a single 409 re-fetches settings,
  re-applies the same mutation to the fresh copy, and retries exactly
  once (a second consecutive 409 re-renders from server truth instead of
  looping). New clients that write `views`/`hidden_sessions` SHOULD send
  this field; see `main.py`'s `update_settings()` for the exact-equality
  rationale (no epsilon — the value round-trips through JSON unmodified).
- **Every settings write is snapshotted first**, regardless of writer (API
  PATCH, federation sync, internal code): `settings.save_settings()` copies
  whatever is CURRENTLY on disk to
  `~/.config/muxplex/settings-history/settings-<unix_ts>.json` (mode 0700
  dir) before overwriting, keeping the most recent
  `settings.SETTINGS_HISTORY_KEEP` (20) snapshots. This is the automatic
  recovery path the incident above needed — previously recovery only
  worked because a manual file backup happened to exist. Best-effort: a
  snapshot failure is logged and swallowed, never blocks or corrupts the
  real write.
- **Destructive-write backstop on `views`** (`views.assess_views_destruction`,
  called from BOTH `settings.patch_settings()` and
  `settings.apply_synced_settings()` — the single lowest choke point each
  write path shares before `views` is replaced wholesale). The CAS
  precondition above stops a *stale* write from being accepted, but does
  nothing if the writer's timestamp genuinely looks newer — which is
  exactly what happened next: a phone PWA tab resubmitted an
  already-collapsed 1-view array 12 times over 7 minutes, and separately,
  federation LWW replicated a collapsed state fleet-wide because `views`
  shared `settings_updated_at` with unrelated fields (see `views_updated_at`
  below). The backstop is a second, independent line of defense: it
  inspects what's about to be written and refuses catastrophic shrinkage
  regardless of whether the CAS/LWW timestamp said the write should
  proceed. Catastrophic (thresholds are named module constants in
  `views.py`, not magic numbers): more than
  `DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD` (1) views collapsing to that
  threshold or below; incoming view count <= (1 -
  `DESTRUCTIVE_VIEW_DROP_RATIO` (0.5)) of current; or incoming total
  session-member count (summed across all views) <= (1 -
  `DESTRUCTIVE_MEMBER_DROP_RATIO` (0.5)) of current. A single view
  deletion, or trimming a handful of members from one view, stays under all
  three and is never flagged. Rejection makes NO write (not even to
  unrelated keys in the same request) and returns 409 with `{"backstop":
  true, "detail": <reason>, "settings_updated_at": <current>, "counts":
  {...}}` — `backstop: true` is how a client tells this apart from an
  ordinary CAS 409 (see `frontend/app.js`'s `patchSettingsGuarded` below).
  `PATCH /api/settings` accepts an `allow_destructive: true` body field to
  perform an intentional bulk deletion; **federation sync NEVER gets this
  override** — a peer must not be able to force a destructive change onto
  another device, only a local operator editing `settings.json` directly
  can. Guard is robust to `views` being absent/None/non-list on either side
  (never crashes; never treats "no incoming `views` key" as "delete all").
- **`views_updated_at`** (metadata alongside `settings_updated_at`; both are
  threaded through the `/api/settings/sync` GET/PUT payload but neither is
  itself in `SYNCABLE_KEYS`): a SEPARATE timestamp that advances ONLY when a
  PATCH/sync actually touches `views` or `hidden_sessions`, decoupled from
  `settings_updated_at`, which advances on ANY syncable key (`fontSize`,
  `sidebarOpen`, etc.). This closes the race that let a fleet-wide views
  collapse replicate: because everything shared one timestamp, an unrelated
  field edit on a peer could bump its `settings_updated_at` past ours and
  win a federation LWW race for `views` too, even though that peer's actual
  view data was stale or already-destroyed. `apply_synced_settings()` now
  takes an optional third argument `incoming_views_updated_at` and, when a
  peer supplies it, applies incoming `views`/`hidden_sessions` ONLY if it's
  strictly newer than our local `views_updated_at` — every OTHER present
  key still applies normally from the overall (newer) sync, so this is
  never all-or-nothing. **Backward compatible**:
  `incoming_views_updated_at=None` (the default, and what any
  pre-this-field peer's payload implies) falls back to the pre-existing
  behavior — apply views/hidden_sessions unconditionally, gated only by the
  backstop above — so older peers keep interoperating with zero changes on
  their end.
- **`PUT /api/settings/sync`'s existing `payload.settings_updated_at >
  local_ts` comparison IS this endpoint's CAS/precondition discipline** — a
  peer only gets to write when its view of the world is strictly newer than
  ours, the sync-path analogue of `PATCH`'s
  `expected_settings_updated_at`. What changed: `apply_synced_settings()`
  now runs the destructive-write backstop unconditionally as its first act
  for EVERY caller — this endpoint AND the periodic background
  `_sync_settings_with_remotes()` poll loop — so a catastrophic incoming
  `views`, however the timestamp comparison came out, is rejected with 409
  (`{"backstop": true, ...}`) and no write happens, with no override
  available on this path.
- **`GET /api/instance-info` includes `tmux_socket_dir`** — the resolved
  (not raw) socket directory this instance's tmux sessions live under (see
  `settings.resolve_tmux_socket_dir()`). Lets remote tools/agents discover
  where sessions need to land to be visible to this instance without
  tribal knowledge; see the "tmux socket" section below and README.md.
- **`GET /api/ca`** serves the local CA's PUBLIC certificate PEM (200,
  `Content-Type: application/x-pem-file`, `Content-Disposition: attachment`)
  when `muxplex setup-tls --method ca` is in use; 404 otherwise (no local CA
  configured, the file is missing, or the file at the CA path is not
  actually a CA cert — `BasicConstraints CA:TRUE` is checked via
  `tls.get_local_ca_cert_bytes()` before serving, so a leaf accidentally
  left at the CA path is refused rather than handed out). **Unauthenticated**
  — added to `auth._AUTH_EXEMPT_PATHS` alongside `/api/instance-info`: a CA
  public certificate is not a secret (no private key material; it's the
  trust anchor clients are meant to install), and requiring auth would be
  circular (a client can't authenticate over TLS it doesn't yet trust).
  Reads ONLY the single fixed path `settings.get_local_ca_cert_path()`
  resolves to (`<config_dir>/ca/muxplex-ca.crt`, mirroring cli.py's
  `setup_tls()`) — the handler takes no request parameters at all, so no
  path/query/body/header can redirect the read to an arbitrary file. Exists
  to close a real onboarding gap: the only prior way to get this file was
  `scp` from the server (needs SSH access a client may not have), and users
  reliably grabbed `muxplex.crt` (the LEAF the server presents on the wire)
  instead, producing "unable to get local issuer certificate" downstream —
  the exact mistake that cost real debugging time in the muxplex-deck
  onboarding flow. See README.md's "Fetching the CA over the network"
  subsection for the client one-liner.
- **`PATCH /api/settings` silently ignores any key in `settings.LOCAL_ONLY_KEYS`**
  (`settings.patch_settings()`), regardless of which other keys are present in
  the same request — the fenced key is skipped (with a `logger.warning`) and
  every other key in the patch still applies. This is not limited to the two
  terminal-input fence keys (`input_enabled`, `input_allowed_sessions`,
  documented in `../AGENTS.md`'s "Terminal input" section): it also covers
  every settings key that names a **command or a filesystem path the server
  itself later executes or reads** — `new_session_template` /
  `delete_session_template` (arbitrary shell commands run via
  `create_subprocess_shell` in `sessions.py`), `tmux_socket_dir` (fed into
  every tmux invocation as `TMUX_TMPDIR`, see `resolve_tmux_socket_dir()`
  above), and `tls_cert` / `tls_key` (paths the server later reads and
  parses in `cli.py`). **Incident (confirmed by audit):** the two session
  templates were NOT originally in `LOCAL_ONLY_KEYS`, so a client holding
  only the federation Bearer key — no PAM login, no interactive session —
  could `PATCH` `new_session_template` to an arbitrary shell command and
  then `POST /api/sessions` to trigger it: full remote code execution that
  never touches the fenced `POST /api/sessions/{name}/input` endpoint at
  all, because the Bearer key is the SAME credential satisfying auth on
  every other `/api/*` route. The fix widened `LOCAL_ONLY_KEYS` to the five
  keys above; all five are also deliberately absent from `SYNCABLE_KEYS`
  (federation sync must never widen a local-only fence). The legitimate
  operator path — editing `~/.config/muxplex/settings.json` directly — is
  unaffected: `load_settings()` applies no `LOCAL_ONLY_KEYS` filtering, so a
  local file edit still takes effect. Clients that write to any of these
  five keys via `PATCH` should expect the value to come back unchanged in
  the response and treat that as confirmation the fence held, not a bug.
- **Stale-key pruning (`views.prune_stale_keys`) is federation-aware and
  prunes ONLY on positive knowledge, never on ignorance.** A settings key
  `"<device_id>:<name>"` in `views`/`hidden_sessions` may be evaluated for
  removal ONLY when the owning device's session list is CURRENTLY KNOWN to
  this instance:
  - Own-device keys (`device_id == local_device_id`) are always evaluable —
    local `names` is authoritative.
  - Remote-device keys are evaluable ONLY if that device currently has a
    fresh entry in `main.py`'s `_federation_cache` (the same cache backing
    `GET /api/federation/sessions`, populated whenever any client — PWA,
    deck sidecar, agent — polls it) whose `fail_count` hasn't reached
    `_FEDERATION_GRACE_FAILURES` (the same reachability threshold that
    endpoint uses to report `status: "unreachable"`). When reachable, that
    device's live session keys are merged into the `live_keys` set passed
    to `prune_stale_keys`, so "reachable and genuinely absent" starts the
    grace clock and "reachable and present" clears it.
  - A remote device with NO current cache entry (never polled, evicted on
    auth failure, or past the reachability grace threshold) is **unknown,
    not dead**: its keys are never pruned and never accrue grace-clock
    time — `prune_stale_keys` actively clears (never advances) their
    `first_missed_at` bookkeeping while unknown, via the
    `local_device_id`/`known_remote_device_ids` parameters. **This is the
    offline-device guarantee**: a laptop that's closed/offline for days —
    far past `stale_key_grace_hours` — never has its view membership
    erased by every OTHER device in the fleet (each of which, before this
    fix, only knew ITS OWN local sessions and wrongly concluded the
    offline device's keys were gone — a real latent eraser, confirmed
    during the 2026-07 views-collapse incident investigation, though not
    its proximate cause). The key survives untouched no matter how long
    the device stays unknown, and resumes a **fresh** grace window (not a
    stale partial count) once the device is known again.
  - Legacy bare-name entries (no `device_id:` prefix) have no determinable
    owner and keep the pre-fix behavior unconditionally: evaluated directly
    against `live_keys`.
  - The whole positive-knowledge gate is opt-in via `local_device_id`
    (`known_remote_device_ids` defaults to empty): omitting it reproduces
    the exact pre-fix behavior for callers/tests that don't supply device
    identity, but `main.py`'s `_run_poll_cycle` (the only real caller)
    always supplies both.
  - The prune ACTION still goes through the v0.12.0 destructive-write
    backstop (`views.assess_views_destruction`) even though it writes via
    `save_settings()` directly rather than `patch_settings()` — the poll
    cycle assesses before/after `views` itself and refuses to persist (both
    `settings.json` and `pruning.json` left untouched, so the same
    situation reproduces visibly next cycle) if a mass prune would collapse
    views. Automatic pruning has no `allow_destructive` override — only a
    local operator editing `settings.json` directly can authorize a bulk
    deletion.
- **Sync groups let a device opt OUT of the server-global session/view
  selection** (`state.py`, `main.py`). A **sync group** owns its own
  `active_session` / `active_remote_id` / `active_view`. Group `"global"` —
  every client's implicit default — is stored in, and *is*, the top-level
  `state.json` keys described above; a device can instead select
  `"device:<its own device_id>"`, a private group scoped to itself.
  `GET`/`PATCH /api/state`, `GET /api/view`, `POST /api/sessions/{name}/connect`,
  and `DELETE /api/sessions/current` all accept an optional `?device_id=`
  query param that resolves which group is read or written; **omitting it is
  byte-identical to today** for every existing client (muxplex-deck,
  `muxplex_client`, and any client that predates this feature) — they all
  omit `device_id` and stay in `"global"`. `POST /api/heartbeat` accepts an
  optional `sync_group` body field (`"global"` or the caller's own
  `"device:<device_id>"` — anything else is 400) to move a device between
  groups; group creation happens there, seeded from global's CURRENT values
  (not defaults), so opting out doesn't teleport the device to the "All"
  view. **Unknown `device_id` on any of these endpoints is 404, never a
  silent fallback to `"global"`** — a device that has aged out of the 300s
  registry (`state.py`'s `prune_devices()`) must not be able to silently
  start driving everyone's screen; the client's recovery is to re-heartbeat
  and retry. Groups with no member device are garbage collected on the same
  poll-cycle pass that prunes stale devices — no separate TTL or timer.
  **`sync_groups` in `state.json` NEVER contains the key `"global"`** — the
  top-level keys ARE its one and only storage. This is deliberate: a
  `sync_groups["global"]` mirror would create two copies of one truth and
  therefore a divergence bug; with one copy there is no which-one-wins
  question to answer.
- **The single shared ttyd process is a SEPARATE, orthogonal claim from sync
  groups, and it is the safety-critical half of this feature.** Exactly one
  ttyd process exists server-wide, on a hardcoded port, with the session
  name baked into its argv at spawn time and a WRITABLE terminal (`ttyd.py`).
  **ttyd is loopback-only by design, and this is unconditional (not gated by
  sync groups, `device_id`, or anything else): it runs `-W` (writable) with
  no `-c` (credential), so it is an unauthenticated writable terminal that
  must never be reachable off-box — see `ttyd.py`'s `TTYD_BIND_ADDRESS` and
  `../AGENTS.md`'s "ttyd is loopback-only by design" section for the
  spawn-argv fence (`-i 127.0.0.1`), the incident (previously bound
  `0.0.0.0`, reachable over LAN and Tailscale with an empty `/token`), and
  the portability rationale. All access to it goes through the authenticated
  claims below, never a direct network path.**
  Two sync groups can each have their own `active_session` selection, but
  they cannot BOTH have their own live terminal — only one group's session
  can actually be relayed at a time. `state.json`'s `terminal_session` /
  `terminal_group` make ttyd's real attachment a first-class, inspectable
  fact instead of an assumption: `terminal_session` is what ttyd is
  currently attached to; `terminal_group` is the group that claimed it.
  `POST /api/sessions/{name}/connect` refuses to seize the terminal away
  from a DIFFERENT group's live session: if `terminal_session is not None`
  and `terminal_group` differs from the caller's resolved group, it returns
  **409** with `{"terminal_conflict": true, "detail": ..., "terminal_session":
  ..., "terminal_group": ...}` — `terminal_conflict: true` is the
  discriminator that tells this 409 apart from the settings backstop 409
  above (same established pattern as `{"backstop": true, ...}`) — and makes
  **no state write and no ttyd process action**. Passing `?takeover=true`
  proceeds anyway (an explicit, informed override — the client's job is to
  surface this as a real confirmation dialog naming the session that will
  move, never a silent retry). **This gate can never fire for a client that
  sends no `device_id`**: it resolves to `"global"`, and the terminal starts
  (and stays, until some other group explicitly takes it over) claimed by
  `"global"` too — both global, so equal, so no conflict. `DELETE
  /api/sessions/current` mirrors this: it only kills ttyd when the caller's
  resolved group IS `terminal_group`; otherwise it clears only the caller's
  own group `active_session` and reports `"terminal_released": false` —
  closing your own private fullscreen must never black out someone else's
  live terminal.
  - **`WS /terminal/ws` enforces the same claim with a loud, unconditional
    backstop that holds regardless of client correctness, and (as of the
    fix below) the wire behavior now matches the code's close-code
    arguments.** With an optional `?device_id=`: unknown `device_id` calls
    `_accept_then_close(websocket, code=4404)`; otherwise, if the resolved
    group's `active_session` is `None` or does not equal the current
    `terminal_session`, the code calls `_accept_then_close(websocket,
    code=4409)` — in both cases *before* any upstream ttyd connection is
    attempted. This device is never shown, and can never type into, a
    session it did not itself select. Precisely this scenario is why the
    guard exists: without it, one device's `POST /connect` silently
    redirects every other connected terminal's WebSocket to the
    newly-attached session — the viewer's UI still shows their own session
    name while their keystrokes land in a DIFFERENT, live session belonging
    to someone else.

    **Incident (original bug): the 4409/4404 codes never reached any real
    client.** Before the fix below, both branches called
    `websocket.close(code=...)` *before* `websocket.accept()`. Per
    ASGI/uvicorn semantics, that does not produce a real WebSocket close
    frame at all — the connection was never upgraded, so there is no
    WebSocket to close. It serialized on the wire as a bare HTTP handshake
    rejection. Live raw-socket verification against a running instance
    confirmed this directly:

    ```
    $ raw socket probe of /terminal/ws?device_id=<non-owner>
    HTTP/1.1 403 Forbidden
    Content-Length: 0
    Content-Type: text/plain
    Connection: close
    ```

    The `websockets` Python client reported only `server rejected WebSocket
    connection: HTTP 403` — no numeric close code was ever visible to it,
    and a real browser's WebSocket `close` event surfaced `event.code ===
    1006` for any failed opening handshake, never `4409`/`4404`. The
    `4404`/`4409` values existed solely inside the ASGI message passed to
    `websocket.close()` and were discarded by the server before a byte
    reached the client. **Unit tests in `muxplex/tests/test_ws_proxy.py`
    did not catch this** because Starlette's `TestClient` operates at the
    ASGI message layer and never serializes real bytes over a socket —
    `WebSocketDisconnect.code` there genuinely was `4409`/`4404`, correctly,
    at that layer, but that layer was not the wire.

    **The fix: `_accept_then_close()` (`main.py`).** Both branches now call
    `websocket.accept()` *first*, then immediately `websocket.close(code=
    ...)`. Completing the handshake before closing is the ONLY way a real
    close frame — carrying a real numeric code — can exist to be reported
    to a browser at all; a browser's WebSocket object never exposes the
    HTTP status or body of a *failed* (pre-101) handshake to JavaScript
    (a WHATWG WebSocket API restriction, confirmed against the same live
    instance both before and after this change — see the raw-socket
    evidence in this fix's commit). This is also why uvicorn's
    WebSocket-denial-response ASGI extension (`websocket.http.response.*`,
    which lets a rejection carry a custom HTTP status/body without ever
    accepting) was considered and **rejected** as the mechanism here: it
    would let a script client that reads the raw HTTP response (e.g.
    Python's `websockets` library, via `InvalidStatus.response`) see a
    reused `{"terminal_conflict": true, ...}` body, but it cannot make a
    numeric code — or any body — reach a real browser, because the
    browser-side restriction applies regardless of what the server sends
    pre-accept. Only completing the handshake does. Post-fix raw-socket
    verification:

    ```
    $ raw socket probe of /terminal/ws?device_id=<non-owner>, post-fix
    HTTP/1.1 101 Switching Protocols
    ...
    <WS close frame, code=4409>
    ```

    A real browser's `close` event now reports `event.code === 4409` (or
    `4404`) directly — `terminal.js`'s `4409` WS-close branch, previously
    documented as unreachable in production, now fires for real. **Trade-off
    accepted deliberately**: the browser's `open` event fires briefly before
    `close` follows, since the handshake genuinely completes — this does
    not weaken the refusal (no relay, no ttyd contact, and no session data
    is ever exchanged on this connection either way; see
    `_accept_then_close()`'s docstring in `main.py`). The separate HTTP 409
    `terminal_conflict` body on the `POST /connect` escalation (§4.3 below)
    remains a second, independent, always-worked recovery path — unaffected
    by any of this, before or after the fix. **Residual gap, unchanged by
    this fix**: a terminal client that supplies no `device_id` at all gets
    none of this protection (the bundled PWA always sends one; requiring it
    unconditionally would be a breaking change to any yet-unknown client and
    remains out of scope). No `device_id` on `/terminal/ws` is the ONLY case
    where this guard does not apply. This fix does not touch the
    pre-accept ttyd-auto-spawn disconnect race documented at
    `terminal_ws_proxy`'s and `_client_disconnected()`'s docstrings in
    `main.py`: that guard runs only in the branch where device_id is
    absent/matching and the function proceeds toward a real relay, which
    `_accept_then_close()`'s branches return well before reaching.

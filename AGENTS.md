# muxplex — Conventions for Agents & Contributors

## The API is a public control surface, not a PWA backend

`/api/*` has consumers beyond the bundled frontend: the
[muxplex-deck](https://github.com/bkrabach/muxplex-deck) Stream Deck sidecar,
federation peers, and AI agents (the contract is discoverable at `/openapi.json`
and `/docs`; headless clients authenticate with the Bearer federation key).
Treat the API as a contract:

- **Prefer additive changes** (new fields, new endpoints). Renaming, removing, or
  changing the semantics of existing fields/endpoints breaks clients this repo's
  tests cannot see.
- **New capabilities land in the API first, frontend second** — never as
  frontend-only state or logic.
- Clients are expected to tolerate unknown fields; the server should tolerate
  their absence (version tolerance in both directions).

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
- **`active_view` / `active_session` are server-global** — last writer wins,
  across every connected client (browsers, deck, agents).
- **The read model is eventually consistent**: GET endpoints serve a ~2s poll
  cache. POST create/delete aren't visible until the next cycle, and `connect`
  on a just-created session 404s until the cache catches up. **Measured, not
  assumed**: traced runs resolved well under 1s (one trace: 3rd attempt at a
  0.3s poll spacing, ~0.9s elapsed) — a flat `sleep 3` wastes most of that
  waiting on a race that's usually already over. Clients/agents should poll
  on a short interval (e.g. 0.3s) with a generous ceiling (e.g. 20 attempts /
  6s) rather than sleep a fixed delay; see `docs/AGENT_GUIDE.md`'s "read
  model is eventually consistent" section for the reference pattern.
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

## Terminal input: `POST /api/sessions/{name}/input` (RCE by design, fenced)

> **This file is conventions for *developing* muxplex.** The guidance for
> *driving* a running muxplex from outside — auth, read endpoints, session
> lifecycle, the input contract, threat model, and the two configuration
> postures — lives in [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md), which is
> deliberately vendor-neutral and safe to point any agent or script at. Keep the
> two in sync: a change to the fences or status-code ordering below is a change
> to that guide's security claims.

Lets a remote agent **type into** a session over the API. Typing into a shell
pane runs whatever is typed — this is remote code execution on purpose — so it
ships **fenced, default-CLOSED**. Every fence must pass, in this order:

1. `is_valid_session_name({name})` at the boundary → 400 (same guard as
   connect/delete; no `:`, no leading `-`, no shell metacharacters).
2. **Global opt-in** `settings.input_enabled` (default `false`) → 403 when off.
3. **Per-session allowlist** `settings.input_allowed_sessions` (default `[]`)
   → 403 if `{name}` matches none of the entries, *even when enabled*.
   Entries are **glob patterns**, matched case-**insensitively** (see
   `terminal_input.session_matches_allowlist`): `"*"` allows every session,
   `"amplifier-*"` (or `"Amplifier-*"`, `"AMPLIFIER-*"`, ...) allows a
   prefix family regardless of case, and a literal name with no glob
   metacharacters matches only that name, also case-insensitively (backward
   compatible with pre-glob exact-name configs, just no longer
   case-sensitive). Implemented as explicit `.casefold()` on both the
   session name and each pattern, then `fnmatch.fnmatchcase` — deliberately
   NOT plain `fnmatch.fnmatch`, whose case-folding is a side effect of
   `os.path.normcase` and therefore *platform-dependent* (no-op on Linux,
   case-folding on macOS/Windows). Explicit casefold + fnmatchcase gives the
   same case-insensitive result deterministically on every platform. An
   empty list still denies everything (fail-closed); a
   non-list value is treated as `[]`; non-string entries in the list are
   skipped rather than crashing the endpoint. This is how a human's own
   working panes stay un-typeable: don't list (or pattern-match) them.
   Checked BEFORE existence, so it never leaks whether a non-listed session
   exists. **Both keys are LOCAL-FILE-ONLY** (`settings.LOCAL_ONLY_KEYS`):
   they can only be changed by editing `~/.config/muxplex/settings.json` on
   disk — `PATCH /api/settings` silently ignores them (with a warning log),
   and they are deliberately NOT in `SYNCABLE_KEYS`. Rationale: the federation
   Bearer key satisfies the shared auth on PATCH and is the SAME credential
   handed to the remote agents that call `/input` — if these keys were
   PATCHable, a Bearer-key holder could self-authorize typing into the
   human's own panes. Widening the fence must be a local-operator action.
   Fence reads are strict-typed and fail CLOSED: only boolean `true` enables
   (`is not True` check), and a non-list allowlist is treated as empty (a
   string value would substring-match via `in`).
4. **Fail-closed target gate**: exact `{name} in get_session_list()` → 404
   (empty/unavailable cache rejects everything; same pattern as connect/delete).

Auth is the **shared middleware** (federation Bearer key / localhost bypass /
session cookie) — deliberately NOT a second key (the council rejected that as
theater).

**Contract.** Body `{ "text": str, "enter": bool=false, "keys": [str] }`
(at least one of the three required, else 400). Send order is **text → keys →
enter**:
- `text` is typed **literally** via `tmux send-keys -l -t <name> -- <text>`
  through `create_subprocess_exec` (argv, **never a shell**). Shell
  metacharacters in `text` are typed as characters, never interpreted by
  anything muxplex spawns. `--` stops tmux option parsing (leading-`-` guard).
- `keys` is a **closed allowlist** of named tmux keys (`Enter, Escape, Tab,
  C-c, C-d, Up, Down, Left, Right, PageUp, PageDown` — see
  `terminal_input.ALLOWED_KEYS`); anything else → 400. Lets an agent send
  Ctrl-C without a shell.
- Target is the **plain** session name (tmux's `=name` exact-match form is not
  valid for a `send-keys` pane target). The fail-closed exact-membership check
  above makes plain `-t name` exact-safe (tmux resolves an exact name to itself
  before prefix-matching).

**Read-back in the same call**: after a ~400ms settle the pane is re-captured
(`capture_pane`, same source as `/api/sessions`) and returned, so a typing
agent isn't guessing: `{ "ok": true, "session": name, "snapshot": "<text>" }`.

**Audit**: exactly one `logger.info` per accepted action (session, char count,
enter/keys flags, a ≤16-char redacted preview). Full text only at `debug` (may
contain secrets). Rejections log at `warning`.

Implementation: endpoint in `main.py` (`send_session_input`); argv/key-allowlist
helpers in `terminal_input.py`. Injection-safety is verified by `test_input.py:316`
(`test_text_sent_literally_via_argv`), which posts a hostile payload
`; rm -rf / && $(reboot) `id` | tee /etc/passwd` and asserts the exact argv is
`("send-keys", "-l", "-t", name, "--", payload)` — `-l` literal mode and `--`
end-of-options prevent shell interpretation, text goes as a single uninterpreted
argv element.

## Frontend delivery: the no-cache header is load-bearing

- `app.js`/`index.html` are served with `Cache-Control: no-cache` (revalidate
  via ETag — cheap 304s) because installed PWAs (Edge/Chrome on macOS) cache
  the app shell and **don't quit on window-close** — without the header,
  deployed frontend JS never reaches the user. Never remove it, and never ship
  a frontend change assuming the PWA will pick it up.
- Startup logs the served `app.js` md5, so "which frontend is live" is a
  glance, not a debugging session.

## Federation is fault-isolated

- A dead/unreachable remote must never gate the aggregate. `breaker.py` is a
  per-remote circuit breaker: 3 consecutive connection failures → skip for
  60s → half-open re-probe. Per-remote poll timeout is 2.0s (`fetch_remote`).
- Only `httpx.TransportError` trips the breaker; any HTTP response (incl.
  401/5xx) counts as reachable and is reported honestly.
- Symptom if regressed: every `/api/federation/sessions` call takes ~5s and
  lags the whole PWA (grid, previews, bells).
- Owner preference: when a dead dependency degrades the app, fix it with
  graceful degradation in the service (circuit breaker), not by asking the
  user to prune config.

## Clean shutdown ordering

- On lifespan shutdown: cancel the poll loop + open WS-relay tasks FIRST
  (bounded gather), THEN `kill_ttyd()`, THEN `aclose()` the httpx client.
  Target: ~0.5s wall time.
- The terminal WS relay must use `asyncio.wait(FIRST_COMPLETED)` + cancel the
  other direction (never gather-both) and return on `websocket.disconnect` —
  otherwise a reader blocked on a live ttyd hangs uvicorn until systemd
  SIGKILLs at the 10s stop timeout.

## Running a second instance on one box (scratch/testing)

- All config/state paths derive from `Path.home()` — **XDG env vars are
  ignored**. Isolate scratch instances with a scratch `HOME`.
- **`TTYD_PORT` is hardcoded** (7682) and `kill_orphan_ttyd()` sweeps that port
  at startup — an unpatched second instance WILL kill the first instance's
  ttyd. Monkeypatch `muxplex.ttyd.TTYD_PORT` before importing `muxplex.main`.
- tmux isolation needs `env -u TMUX` plus an isolated `TMUX_TMPDIR` (a set
  `$TMUX` silently overrides `TMUX_TMPDIR`).
- **`muxplex env`** prints the resolved `TMUX_TMPDIR` export for the
  instance sharing this box's config (`eval "$(muxplex env)"`) — this is
  the one-line fix for the "invisible session" hazard described in
  README.md's "tmux socket" section: any session created without matching
  `TMUX_TMPDIR` lands on a different tmux server and is invisible to this
  instance. Prints ONLY the export line to stdout (safe to `eval`); human
  notes go to stderr.
- Candidate future fixes: honor XDG paths; make the ttyd port configurable.

### ⚠️ NEVER broad-kill by process name on a host running a live muxplex

A scratch test's cleanup must NEVER use process-name matching to kill
things, because the live server's command line is literally `... muxplex
serve` and its ttyd is `ttyd ... -p 7682`. These patterns are landmines that
reach across and kill the production service (this bit us repeatedly — the
service kept "mysteriously" going down, and it was always a scratch cleanup):

- ❌ `pkill -f muxplex`, `pkill -f uvicorn`, `pkill -f ttyd`, `killall ...`
  — matches the live `muxplex serve` / live ttyd. **Forbidden.**
- ❌ bare `tmux kill-server` — with an unset/leaked `TMUX_TMPDIR` this kills
  the user's real tmux server (the live muxplex socket is
  `~/.tmux`, not the default). **Forbidden.**

Instead:
- Kill ONLY the exact PIDs your harness spawned (capture `proc.pid` at spawn,
  signal that PID and no other). Port-scope any sweep to the SCRATCH port
  (17682), never a name.
- Use an explicit named tmux socket: `tmux -L <unique-scratch-name> ...` and
  clean up with `tmux -L <unique-scratch-name> kill-server` (socket-scoped),
  never a bare `kill-server`.
- Prefer in-process `TestClient(app)` (no separate uvicorn process to kill) or
  a full DTU/container for true isolation over an ad-hoc host uvicorn.
- After any scratch run, VERIFY the live server is still up
  (`GET :8088/api/instance-info` → 200) as the last step.

## Testing & workflow

### ⚠️ NEVER run the test suite on a host running a live muxplex

`uv run pytest` on a developer box that is also serving muxplex has caused real
production damage, twice in one session:

1. A test that wrote settings without redirecting `SETTINGS_PATH` overwrote the
   host's real `~/.config/muxplex/settings.json`, replacing an 8-view production
   config with fixture data.
2. Six tests called the real `serve()` without mocking `_kill_stale_port_holder`
   and without pinning a port, so the port resolved to `DEFAULT_SETTINGS["port"]`
   (8088). The real killer ran `lsof -ti :8088`, found the live server, and
   SIGTERMed it — repeatedly. Symptom: a server that "keeps resetting," with
   clean graceful shutdowns, no crash, and no systemd `Stopping` line. Nearly
   undiagnosable from logs.

Both were invisible from inside the suite: **a test that destroys its host still
passes.**

`muxplex/tests/conftest.py` now makes this fail loud instead. Read its docstring
before changing anything there; `test_safety_rails.py` fails if a guard is
removed. The rails:

| Rail | Stops |
|---|---|
| `pytest_sessionstart` guard | Running at all when something serves the default port |
| autouse `SETTINGS_PATH` → tmp | Tests reaching the real user config |
| autouse killer-neutering | Tests SIGTERMing whatever owns the port |
| `test_safety_rails.py` | Silent removal of any of the above |

To reach the real port killer a test must opt in explicitly with
`@pytest.mark.allow_real_port_killer` — visible in review.

### Run it in an isolated environment

```
make test          # runs the suite inside a Digital Twin Universe container
```

The workflow, in this order — the commit is a **checkpoint**, so a bad DTU run
costs you nothing:

1. **Commit locally first.** This is what makes `git archive HEAD` correct: the
   DTU then tests exactly the artifact you would push, with no divergence
   between "what I tested" and "what I'm pushing."
2. **Test in the DTU.** Iterate there until green.
3. **Then push / open the PR.**

Skipping step 1 means the DTU tests something that exists only in your working
tree — and a green run there proves nothing about what lands.

`MUXPLEX_TEST_ALLOW_LIVE_HOST=1` overrides the guard. Legitimate on a CI runner
or a fresh container with no muxplex. Not legitimate on your dev box because the
guard is inconvenient.

- Python (inside an isolated env only): `uv sync --extra dev && uv run pytest`
  (tests marked `integration` need a real tmux binary).
- Frontend: `node --test frontend/tests/*.mjs`. Use the glob, not a single
  file — the previously-documented `test_app.mjs`-only command silently
  never ran `test_terminal.mjs`.
- CI: `.github/workflows/ci.yml` runs THREE jobs. The first two test the
  Python code against two DIFFERENT dependency stacks on purpose:
  - `test` (Python 3.11/3.12/3.13) installs via `uv sync`, i.e. `uv.lock`'s
    pinned versions -- a stable, reproducible dev baseline.
  - `test-latest-deps` installs via a fresh `uv pip install -e ".[dev]"`
    into a plain venv, deliberately bypassing `uv.lock` entirely -- this is
    what a real `uv tool install muxplex` resolves (it never reads the
    lock; it re-resolves against `pyproject.toml`'s version floors against
    whatever is newest on PyPI at install time).

  The third job covers the frontend, which neither Python job executes a
  line of:
  - `test-frontend` runs `node --test tests/*.mjs` in `muxplex/frontend`
    (Node 22, no install step — these suites have zero package
    dependencies and use only `node:` builtins, which is why there is no
    `package.json`). Added after v0.15.1 shipped a frontend-only fix whose
    new regression test CI never executed: all four Python jobs went green
    without running a line of it. Same shape as the `uv.lock` drift above —
    CI green while not testing the thing that changed.

  **Why both exist (2026-07 incident):** `uv.lock` was pinned to uvicorn
  0.42.0 / websockets 16.0 (uvicorn's legacy websocket ASGI
  implementation), while every real `uv tool install` -- including the
  user's production install -- resolved uvicorn 0.51.0 / websockets 16.1.1
  (the newer 'sansio' implementation). A WebSocket `RuntimeError`
  (`terminal_ws_proxy`'s pre-accept disconnect race, see `test_ws_proxy.py`)
  reproduced ONLY on the sansio impl. Because both the `test` job above AND
  the DTU (`make test`) install from `uv.lock`, they stayed green for a full
  day while production threw the error hourly -- **CI green did not imply
  production worked.** `test-latest-deps` closes that blind spot by testing
  the same dependency stack users actually get. Do not remove it as
  "redundant with `test`" -- that redundancy is the point; if you find
  yourself wanting to, refresh `uv.lock` instead (see below).

  **Deliberately NOT chosen:** pinning a floor on `uvicorn`/`websockets` in
  `pyproject.toml` to force the sansio impl everywhere. That would only
  patch this ONE known instance -- the next dependency to drift between
  `uv.lock` and a fresh resolve (fastapi, starlette, httpx, ...) would
  reopen the identical blind spot silently. `test-latest-deps` catches ANY
  future drift, not just this one, which is why it's the fix and a version
  floor isn't.
- **`test_frontend_js.py` asserts on JS SOURCE TEXT, and that is a tripwire for
  any frontend refactor.** It is 4,821 lines / 332 tests, of which 229 are
  regex matches against `app.js` source rather than checks of behavior. That
  style pins the *shape* of the code, so a legitimate refactor that preserves
  behavior can still fail it — which has now happened twice: v0.13.0 (three
  stale assertions matching literal `api('PATCH'` strings after those call
  sites moved into `patchSettingsGuarded()`) and v0.16.1 (four assertions
  requiring `autocomplete`/`spellcheck` literals inside `_createSessionInput`
  after they moved into the shared `_suppressAutofill` helper). Both times the
  behavior was correct and the test was wrong.

  When one of these fails after a refactor, first ask whether the BEHAVIOR
  changed. If it didn't, fix the assertion to follow the new structure (assert
  the delegation *and* the delegate) rather than loosening it to pass — a
  weakened assertion is worse than the stale one it replaced.

  Behavior-level coverage of the same code now lives in the node suite
  (`frontend/tests/*.mjs`, 8,173 lines, run by the `test-frontend` CI job
  since v0.15.1+), which exercises the real DOM contract instead of matching
  source strings. **Retiring the redundant source-scraping assertions in favor
  of the node suite is worth doing, but it is a project, not a cleanup:** it
  needs a per-assertion coverage comparison first, because deleting one that
  has no node equivalent silently removes real protection.
- PRs are squash-merged. `CHANGELOG.md` and version bumps happen at release
  time, by the owner — don't bump them in feature PRs.
- **Release hygiene is part of the fix**: a fix isn't done until it's
  versioned and on PyPI (tag `v*` → Trusted-Publishing workflow). Don't leave
  main N PRs ahead of the published version.

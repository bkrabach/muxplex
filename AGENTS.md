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
  on a just-created session 404s until the cache catches up — clients/agents
  must wait ~3s after writes. (Candidate future fix: write-through cache
  refresh on create/delete.)
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

## Terminal input: `POST /api/sessions/{name}/input` (RCE by design, fenced)

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
helpers in `terminal_input.py`. Injection-safety is proven by an E2E that sends
`; touch <canary>` to a `cat` (non-shell) pane and asserts the canary file is
never created — the text is typed literally, our subprocess layer never spawns a
shell.

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

- Python: `uv sync --extra dev && uv run pytest` (tests marked `integration`
  need a real tmux binary).
- Frontend: `node --test frontend/tests/test_app.mjs`.
- CI: `.github/workflows/ci.yml` (Python 3.11/3.12/3.13).
- PRs are squash-merged. `CHANGELOG.md` and version bumps happen at release
  time, by the owner — don't bump them in feature PRs.
- **Release hygiene is part of the fix**: a fix isn't done until it's
  versioned and on PyPI (tag `v*` → Trusted-Publishing workflow). Don't leave
  main N PRs ahead of the published version.

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

## Auto-updating views: rules never get materialized, and the matcher is deliberately duplicated

A view's optional `match_names` glob rules are matched with explicit
`.casefold()` + `fnmatch.fnmatchcase` (`views.matches_name_pattern`) --
**deliberately a separate implementation** from
`terminal_input.session_matches_allowlist`, which uses the identical
technique for an unrelated reason (that one is the security boundary for
the RCE-by-design `/input` endpoint; this one is a display filter). Two
consumers with opposite failure requirements -- fail-closed security vs.
fail-loud display -- must not share a mutable implementation: a future
tightening of the input fence must not silently change which sessions a
view contains, and a future loosening for views must not silently widen an
RCE fence. The duplication is a handful of lines and is the cheap side of
that trade.

**Standing prohibition, load-bearing:** the server must NEVER materialize a
rule match back into `view["sessions"]`. Rules stay rules on disk, forever
-- `views.filter_visible`/`views.view_names_for_session` resolve membership
fresh on every read. Materializing would re-introduce the exact decay this
feature exists to eliminate, turn every poll cycle into a settings write,
and hand federation LWW a brand-new race. See `docs/plans/2026-08-04-auto-views-plan.md`
for the full design.

## API semantics external clients re-implement → `docs/API_SEMANTICS.md`

The *semantics* behind the wire contract — the rules clients currently re-derive
locally, and the invariants a server change must not break silently — live in
[`docs/API_SEMANTICS.md`](docs/API_SEMANTICS.md), beside `AGENT_GUIDE.md`. It
covers the needs-attention (bell) predicate, `device_id:name` view-membership
normalization, `last_activity_at`'s derivation, the eventually-consistent read
model, `settings_updated_at` / `views_updated_at`, the
`expected_settings_updated_at` compare-and-swap precondition, the
settings-history snapshot, the destructive-write backstop on `views`,
federation sync's write discipline, `GET /api/instance-info`'s
`tmux_socket_dir`, `GET /api/ca`, and federation-aware stale-key pruning — each
with the incident that produced it.

**Read it before** you change the shape or behavior of any `/api/settings`,
`/api/state`, `/api/view`, or `/api/settings/sync` field; before you change what
the poll cycle, pruning, or federation sync writes; or when you are about to add
a rule a client would have to re-implement. That last case has a standing
answer: resolve it **server-side** (as `GET /api/view` now does) rather than
shipping more logic for each of PWA / sidecar / agents to port — duplication
across clients is where drift bugs come from.

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

   `session_commands` (a list of named create/kill pairs, each holding the same two
   arbitrary shell commands as `new_session_template`/`delete_session_template` below --
   the API may list and select a pair via `GET /api/session-commands` and
   `POST /api/sessions {"command_id": ...}`, never define one) is fenced for the identical
   reason: a PATCHable `session_commands` would let a Bearer-key holder define a pair AND
   select it at create time -- the same RCE with an extra layer of indirection. See
   `docs/API_SEMANTICS.md` for the full design.
   Fence reads are strict-typed and fail CLOSED: only boolean `true` enables
   (`is not True` check), and a non-list allowlist is treated as empty (a
   string value would substring-match via `in`).

   **This fence has a sibling, and it is not optional reading.** The `/input`
   fence above only protects the *typing* path. A Bearer-key holder who
   cannot type into a session can still get an equivalent RCE through a
   completely different door: `PATCH /api/settings` the
   `new_session_template` (or `delete_session_template`) to an arbitrary
   shell command, then `POST /api/sessions` to make the server run it —
   never touching `/input` at all. **Incident (confirmed by audit, fixed
   before it was exploited in the wild):** `new_session_template` and
   `delete_session_template` were NOT in `LOCAL_ONLY_KEYS`, so this path was
   open. The fix widens `LOCAL_ONLY_KEYS` to cover every settings key that
   names a **command or a filesystem path the server itself later executes
   or reads** — not just the two input-typing keys: `new_session_template`,
   `delete_session_template` (shell commands run via
   `create_subprocess_shell`), `tmux_socket_dir` (fed into every tmux
   invocation as `TMUX_TMPDIR` — a remote caller could otherwise redirect
   session create/kill to an attacker-controlled socket dir), and
   `tls_cert`/`tls_key` (paths the server later reads and parses — an
   unauthenticated file-read primitive on an attacker-chosen path
   otherwise). Same rationale as above, same remedy: local-file-only,
   `PATCH` silently ignores them, never in `SYNCABLE_KEYS`. See
   `settings.LOCAL_ONLY_KEYS`'s module comment for the authoritative list
   and `docs/API_SEMANTICS.md` for the client-facing semantics.
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

## ttyd is loopback-only by design (unauthenticated writable terminal) — now per-session, over AF_UNIX

**One ttyd process per tmux session**, each bound to its own UNIX domain
socket under `ttyd.ttyd_socket_dir()` (default `~/.local/share/muxplex/ttyd/`).
`ttyd.py`'s `spawn_ttyd()` execs `ttyd -W -m 3 -i <socket> tmux attach -t
<name>`. `-W` (writable) with **no `-c` credential** means every ttyd is an
**unauthenticated, writable terminal server** — anyone who can reach its
socket can both view and *type into* the attached tmux session, with zero
interaction with muxplex's auth stack (`_ws_auth_check`, the cookie/Bearer
middleware, TLS). All legitimate access goes through muxplex's own
authenticated `WS /terminal/ws` (or federation) proxy (`main.py`), which
dials a UNIX socket directly — never a public interface.

`AF_UNIX` is a **strictly stronger** fence than the old `-i 127.0.0.1` TCP
bind: filesystem permissions (0700 dir, 0600 socket, uid-checked) and no
network namespace involvement at all — there is no port to scan or interface
to misconfigure. `ttyd.SOCKET_SUFFIX` (`.sock`) is the successor to that old
fence: a non-`.sock` path does not make ttyd error — it silently falls back
to TCP port **7681** on `INADDR_ANY`, reopening the identical exposure. This
is why the socket path is never hand-built and the readiness gate checks the
actual socket file, never ttyd's liveness or its own log line.

**Incident (pre-UNIX-socket era, kept for the record):** this process
previously had no `-i`/bind flag at all. ttyd's default bind with no `-i` is
`INADDR_ANY` (`0.0.0.0`) — confirmed live: `ss -ltnp` showed `0.0.0.0:7682`,
`curl` from another host on the LAN and separately over Tailscale both got a
real ttyd terminal client (`200`, full HTML), and `GET /token` returned
`{"token": ""}` (no credential configured). Any device reachable on the LAN
or tailnet could open the port in a browser and type into the host's live
tmux session. Fixed at the time by adding `-i 127.0.0.1`; the per-session
UNIX-socket architecture supersedes that fix entirely (there is no longer a
TCP bind to secure).

**Not configurable, and must never become PATCHable.** The socket directory
is resolved from `MUXPLEX_TTYD_SOCKET_DIR` (env var) or `STATE_DIR/ttyd` —
deliberately **not** a settings key (would have to join
`settings.LOCAL_ONLY_KEYS`; an env var sidesteps the fence question entirely
and cannot be reached by a federation Bearer-key holder at all). If a future
need ever exposes ttyd's bind target as a setting, it **must** join
`LOCAL_ONLY_KEYS` (see "Terminal input" above), never `SYNCABLE_KEYS` — same
fence rationale already applied to `new_session_template` et al.

`ttyd_port` (`= 7682`) survives in `POST /connect`'s response as a
**legacy wire field only** — no ttyd binds this port anymore, but
`muxplex_client.parse_connect_result()` requires an int with no default
(vendored into muxplex-deck). Do not remove it or return `null`.

See `docs/API_SEMANTICS.md`'s "per-session ttyd" section for how this
interacts with sync groups' `terminal_session`/`terminal_group` bookkeeping
(now provenance metadata, not a resource claim).

## Frontend delivery: the no-cache header is load-bearing

- `app.js`/`index.html` are served with `Cache-Control: no-cache` (revalidate
  via ETag — cheap 304s) because installed PWAs (Edge/Chrome on macOS) cache
  the app shell and **don't quit on window-close** — without the header,
  deployed frontend JS never reaches the user. Never remove it, and never ship
  a frontend change assuming the PWA will pick it up.
- Startup logs the served `app.js` md5, so "which frontend is live" is a
  glance, not a debugging session.

## Frontend classic scripts share one global scope

`index.html` loads `app.js` and `terminal.js` (and any future frontend
script) as classic, non-module `<script defer>` tags. Classic scripts do
**not** get their own module scope -- every top-level `var`/`function` becomes
a property on the shared global object, and every top-level `let`/`const`
lives in the same shared global lexical environment. A second script cannot
redeclare a binding the first one created there.

**Incident (v0.31.3):** the sync-groups change (`fcfdcdd`) added a top-level
`function _ownDeviceId()` in `app.js` (a getter) and, independently, a
top-level `let _ownDeviceId = ''` in `terminal.js` (private module state).
Both parsed and worked fine on their own -- each file's own test suite
(`test_app.mjs`, `test_terminal.mjs`) loads only that one file in isolation,
so neither test could ever see the collision. In the real browser, loading
both in the same scope threw `Uncaught SyntaxError: Identifier
'_ownDeviceId' has already been declared` while parsing `terminal.js` --
which meant `terminal.js` never executed at all, and the interactive
terminal pane silently rendered nothing (the grid/previews, all in `app.js`,
kept working since `app.js` parsed fine on its own). Fixed by renaming
`terminal.js`'s private state to `_termOwnDeviceId`.

**The rule:** every top-level binding across ALL of our frontend classic
scripts must be unique -- prefix module-private state so it can't collide
with another file's globals (e.g. `_termOwnDeviceId`, not `_ownDeviceId`).
Per-file unit tests cannot catch a cross-file collision by construction, no
matter how thorough -- which is why `tests/test_shared_scope.mjs` exists: it
parses the real `<script src=...>` tags out of `index.html` (excluding
`/vendor/*`) and evaluates each one, in order, into one shared `vm` context,
asserting none throws a `SyntaxError`. Any new frontend script is
automatically covered the moment it's added to `index.html`.

## `attachCustomKeyEventHandler`: `return false` does NOT stop the browser

xterm.js's custom key handler returning `false` stops **xterm's own** key
processing. It does **not** call `preventDefault()`, so the browser's default
action still fires -- for any chord that produces text input, the key also
reaches xterm's hidden textarea and comes back around through `onData`. You get
the key twice, from two different paths.

**Incident (Shift+Enter, v0.34.0+):** the new Shift+Enter branch sent the CSI-u
sequence over the WebSocket and returned `false`. Measured in a live pane with a
raw byte dumper, the app received **`0a 0d`** -- our translated `C-j`, then a
stray `CR`. Visible symptom: a newline appeared and the line submitted anyway.
Both halves of the feature were correct and deployed; the bug was purely the
missing `e.preventDefault()`. Fixed by calling it before `return false`.

The pre-existing branches in that handler (Ctrl+Shift+C, Ctrl+F) never hit this,
which is exactly why it was easy to miss -- those chords produce no text input,
so there is no default action to suppress. **Any new branch that intercepts a
key which would otherwise type something MUST call `e.preventDefault()`.**
`tests/test_terminal.mjs` asserts this for the Enter branch; extend that
assertion rather than trusting review to catch the next one.

Corollary for debugging this class of bug: reason about **bytes**, not
behavior. "It submitted anyway" is ambiguous; `0a 0d` names the failure exactly
and distinguishes "our handler never fired" from "our handler fired twice."

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

## ⚠️ Two ways to destroy every live tmux session on this host

The user's tmux sessions are the product. They hold hours of in-flight work and
they are **not recoverable** — a tmux session is a live process tree, not a
file. Two distinct mechanisms have destroyed them for real. Mechanism 2 was
already written down here, and was being followed exactly on the day mechanism 1
killed 44 sessions: **narrow process hygiene is necessary and not sufficient.**
Before any action that stops, restarts, or kills anything on this box, check
both.

### 1. Restarting a service whose cgroup has adopted the tmux server

**Before `systemctl restart` / `stop` on ANY unit that could own a tmux server,
read its resolved `KillMode`.** `mixed` and `control-group` (the default) both
SIGKILL every remaining process in the service cgroup. Only `process` spares
them.

```
systemctl --user show muxplex.service -p KillMode      # resolved, incl. drop-ins
systemctl --user cat muxplex.service | grep -i KillMode
```

**muxplex is the specific hazard: it auto-spawns the tmux server when none is
running** (`WS proxy: ttyd not listening, auto-spawning for '<session>'`). That
server becomes a child of muxplex and inherits its cgroup, where it looks like
nothing at all until a stop kills it.

**Incident (2026-07-29):** a routine `systemctl --user restart muxplex.service`
destroyed **44 live tmux sessions**. The unit shipped `KillMode=mixed`.

```
17:03:28  muxplex.service: Killing process 1518471 (tmux: client) with signal SIGKILL
17:03:31  Started muxplex.service
17:03:31  44 sessions recorded in pruning.json first_missed_at — one identical timestamp
```

The identical timestamp across all 44 is what proves a single simultaneous kill
rather than gradual loss. This was never only a deploy hazard: the drop-in's own
2026-07-24 comment documents that something on this box periodically SIGTERMs
muxplex, and under `mixed` every one of those was a loaded gun.

**Fix in place:** `KillMode=process` in
`~/.config/systemd/user/muxplex.service.d/override.conf`.

**Prove a cgroup fix with a canary — never by reading the directive back.**
Start a throwaway tmux server, write its PID into the service's `cgroup.procs`
so it occupies the exact position the real sessions occupy, restart the service,
and assert the process is still alive with its session intact. That is the only
evidence that distinguishes "I set the right value" from "the sessions survive."

**Candidate real fix (not done):** muxplex should not be the parent of the tmux
server at all — if the tmux server lives outside muxplex's cgroup, the unit's
`KillMode` stops mattering. `KillMode=process` is a correct guard on one host's
config; it does not travel with the package to anyone else's.

**`setsid` does NOT achieve this. Do not re-propose it.** cgroup membership is
inherited across `fork()` and is entirely unaffected by `setsid()`, which creates
a new *session / process group* — a different kernel concept from a cgroup. The
proof is already in this repo: `ttyd.py:218` passes `start_new_session=True`
(which is exactly `setsid`), and the tmux server that ttyd's `tmux attach`
parented was still sitting in `muxplex.service`'s cgroup when it was SIGKILLed on
2026-07-29, taking 44 live sessions with it. Someone who "fixed" the hazard this
way would have changed nothing and believed otherwise.

Only an **explicit cgroup move** escapes: launch the tmux server in a transient
scope of its own (`systemd-run --user --scope`), or write its PID into a
different cgroup's `cgroup.procs`. Confirm with the canary above — never by
reading the resulting directive back.

### Recovering sessions after they are lost

**Capture the manifest BEFORE recreating anything.**
`~/.config/muxplex/pruning.json`'s `first_missed_at` map is the ONLY record of
the lost session names — and muxplex **clears an entry as soon as that session
comes back**. Recreating sessions one at a time destroys your own recovery list
mid-recovery. Copy it out first:

```
cp ~/.config/muxplex/pruning.json /tmp/lost-sessions.json
```

**Recreate with `amplifier-workspace`, not `tmux new-session`.** A bare tmux
session is one window with the wrong cwd; it looks restored and isn't. Session
name maps to `~/dev/<name>` (verify the directory exists — it did for all 44).
`amplifier-workspace` produces the real 4-window layout — `amplifier`, `shell`,
`git`, `files` — each with cwd set to the workspace directory:

```
env -u TMUX setsid amplifier-workspace ~/dev/<name> </dev/null
```

`env -u TMUX` and `setsid` are load-bearing when scripting this: inside tmux the
command calls `switch-client` and yanks the user's terminal on every iteration.
With no controlling TTY the final attach fails harmlessly *after* the session is
correctly created — exit 1 with `open terminal failed: not a terminal` is the
expected outcome, not an error.

Recreated sessions are **fresh shells**. Names, layout, and cwd come back;
process state does not. For sessions that held an agent, `amplifier resume` in
the right directory picks the transcript back up — don't guess at directories on
the user's behalf.

### 2. NEVER broad-kill by process name on a host running a live muxplex

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

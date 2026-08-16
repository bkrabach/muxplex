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
3. **Per-session allowlist** `settings.input_allowed_sessions` (default `["*"]`
   — see "the default was WIDENED" below; it used to be `[]`)
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
   exists. **Both keys are `settings.LOCAL_ONLY_KEYS`**, default-DENIED to
   the API and deliberately NOT in `SYNCABLE_KEYS`: `PATCH /api/settings`
   silently ignores them (with a warning log) for a caller authorized
   SOLELY by the federation Bearer key (`bearer_only` — see
   `main._bearer_only_caller()`), and no federation peer's sync can ever
   carry them. **They are also `settings.OPERATOR_SETTABLE_LOCAL_KEYS`**,
   which narrows that default-deny: a caller authorized by a real operator
   credential (a browser session cookie, or HTTP Basic) MAY set them
   through `PATCH /api/settings` too, in addition to editing
   `~/.config/muxplex/settings.json` on disk directly. Both remain
   equally valid ways for the one already-trusted party (the operator) to
   widen the fence; only a `bearer_only` caller is still fully blocked, for
   both keys, on every path. Rationale unchanged: the federation Bearer key
   satisfies the shared auth on PATCH and is the SAME credential handed to
   the remote agents that call `/input` — if these keys were PATCHable *by
   that credential*, a Bearer-key holder could self-authorize typing into
   the human's own panes. Widening the fence must be an operator action —
   it no longer has to be a *local-file* action specifically, but it can
   never be a Bearer-only one.

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

   **THE ALLOWLIST DEFAULT WAS WIDENED (v0.48.0) — if you remember the old
   two-gate behaviour, this is the paragraph you need.** `input_allowed_sessions`
   used to default to `[]`, which denies every session. That meant flipping
   `input_enabled: true` did *nothing on its own*: the operator hit a second
   403 and had to enumerate session names by hand before anything worked.
   Three separate times that read as "the feature is broken." The default is
   now `["*"]` — every session — so **one deliberate operator action
   (`input_enabled: true`) now opens typing for EVERY session, including the
   human's own working panes.** Narrowing is opt-in: if you want only some
   sessions typeable you must now say so explicitly
   (`"input_allowed_sessions": ["agent-*"]`). What did NOT change, and must
   not: `input_enabled` still defaults to `false` (the capability is still off
   out of the box), and both keys are still `LOCAL_ONLY_KEYS`. Consequence
   worth stating plainly: with the allowlist open by default, `input_enabled`
   is now the *only* fence standing between a federation-Bearer-key holder and
   RCE on every session — which is exactly why it must stay local-file-only
   and default-false. Correction to the sentence above: "this is how a human's
   own working panes stay un-typeable: don't list them" now requires an
   explicit narrowing edit; it is no longer what you get by default.

   **Both the list `["*"]` (the default) and the bare string `"*"` are
   accepted.** The fence itself still requires a list (`tmux_kit.keys.input_allowed_for_session`
   treats any non-list as empty — unchanged), so `settings.load_settings()`
   normalizes a bare string into a one-element list *upstream* of the fence
   (`settings.normalize_input_allowed_sessions`). Without that, a hand-written
   `"input_allowed_sessions": "*"` — the shorthand a human naturally writes
   after reading "the default is `*`" — would be read as deny-all and 403
   silently,
   which is the same dead end this change exists to remove. The normalization
   cannot widen anything for a remote caller: the key is `LOCAL_ONLY_KEYS`, so
   the only value it can ever see came from a local operator editing the file.
   It deliberately does NOT invent a comma syntax (`"a,b"` is one pattern that
   matches nothing) and does not reintroduce substring matching (`"alpha"`
   allows `alpha`, never `al`).

   **This fence has two siblings, and neither is optional reading.** The
   `/input` fence above only protects requests to *this one endpoint*. A
   Bearer-key holder who cannot type into a session via `/input` could
   until recently still get equivalent RCE through two completely
   different doors:

   - **Sibling 1 — settings, not typing.** `PATCH /api/settings` the
     `new_session_template` (or `delete_session_template`) to an arbitrary
     shell command, then `POST /api/sessions` to make the server run it —
     never touching `/input` at all. **Incident (confirmed by audit, fixed
     before it was exploited in the wild):** `new_session_template` and
     `delete_session_template` were NOT in `LOCAL_ONLY_KEYS`, so this path
     was open. The fix widens `LOCAL_ONLY_KEYS` to cover every settings key
     that names a **command or a filesystem path the server itself later
     executes or reads** — not just the two input-typing keys:
     `new_session_template`, `delete_session_template` (shell commands run
     via `create_subprocess_shell`), `tmux_socket_dir` (fed into every
     tmux invocation as `TMUX_TMPDIR` — a remote caller could otherwise
     redirect session create/kill to an attacker-controlled socket dir),
     and `tls_cert`/`tls_key` (paths the server later reads and parses —
     an unauthenticated file-read primitive on an attacker-chosen path
     otherwise). Same rationale as above, same remedy: local-file-only,
     `PATCH` silently ignores them, never in `SYNCABLE_KEYS`. See
     `settings.LOCAL_ONLY_KEYS`'s module comment for the authoritative
     list and `docs/API_SEMANTICS.md` for the client-facing semantics.

   - **Sibling 2 — the terminal WS, not the HTTP endpoint at all.**
     `WS /terminal/ws?session={name}` is a second, RAW typing path into the
     exact same tmux pane: `client_to_ttyd` (main.py) forwards every byte
     the caller sends straight into ttyd, which types it into the pane —
     a parallel RCE primitive that, until this fix, applied NEITHER
     `input_enabled` NOR `input_allowed_sessions`, because the WS route
     only ever checked auth (localhost / cookie / Bearer) and a
     device/group consistency guard, never "is this caller allowed to
     TYPE into this session." **Incident (confirmed by audit, fixed before
     it was exploited in the wild):** a Bearer-key holder — the same
     credential this file already says is handed to headless AI agents —
     could type into ANY live session by opening this WS directly and
     naming it via `?session=`, regardless of `input_enabled` or
     `input_allowed_sessions`. The fix gates ONLY the WS's client→ttyd
     TYPING direction (identifying real keystroke frames by the ttyd wire
     protocol's leading command byte, `0x30`; see `terminal_ws_proxy`'s
     docstring), and ONLY for callers `_ws_auth_check` classifies as
     `bearer_only` (Bearer credential present, no valid session cookie —
     see `WSAuth`'s docstring for why a valid cookie always wins that
     classification when both are present). A cookie-authenticated
     browser session — the product's core feature — is completely
     unaffected, exactly as before. VIEWING (the ttyd→client output
     direction, plus the ttyd wire handshake and resize control frames) is
     *never* gated, for anyone, at any classification: a Bearer holder can
     already read every session's live pane via `GET /api/sessions`'
     `snapshot` field regardless of this fence, so blocking the identical
     content over the WS would add no confidentiality — and it would
     break `federation_terminal_ws_proxy`'s legitimate peer-to-peer relay,
     which dials this same route with a Bearer header unconditionally
     (server-to-server, never a cookie) whenever a human uses the
     aggregated PWA to watch a REMOTE host's session. Net effect for
     federation: **viewing a remote host's terminal always still works**;
     **typing into it now requires that remote host's own
     `input_enabled`/`input_allowed_sessions` to explicitly allow the
     session**, the same local opt-in every other Bearer-only typing path
     already requires — because the wire is bit-identical between "my own
     federation peer relaying a human's keystrokes" and "a Bearer holder
     typing directly," and an undistinguishable case is denied, never
     guessed open. See `docs/API_SEMANTICS.md`'s "terminal WS input fence"
     entry for the full incident writeup, including the residual gap this
     leaves open for peers running a pre-fix version.

     **Correction to this section's older claim:** an earlier revision of
     this document said the `input_allowed_sessions` allowlist is "how a
     human's own working panes stay un-typeable" without qualification.
     That was true only against the HTTP endpoint — against a
     Bearer-key-holding WS caller it was false until this fix. It is now
     true against all three doors (`/input`, the settings-template sibling,
     and this WS sibling) for every caller class this file documents.
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
helpers in `tmux_kit.keys` (re-exported through `terminal_input.py`). Injection-safety
is verified by `test_input.py`'s `test_text_sent_literally_via_argv`, which posts a
hostile payload `; rm -rf / && $(reboot) `id` | tee /etc/passwd` and asserts the
exact argv is `("copy-mode", "-q", "-t", name, ";", "send-keys", "-l", "-t", name,
"--", payload)` — `-l` literal mode and `--` end-of-options prevent shell
interpretation, text goes as a single uninterpreted argv element. The leading
`copy-mode -q -t <name> ;` is **tmux-kit 0.4.0's own guarantee, not this
endpoint's**: `build_send_text_argv()`/`build_send_key_argv()` chain the
copy-mode-exit step into the SAME argv ahead of `send-keys`, via a literal `;`
argv element (two tmux commands, one subprocess call, one command-loop tick —
atomic). This endpoint used to also issue `build_exit_copy_mode_argv()` as its
own separate leading call; that call is now redundant (every send already
carries it) and has been removed — the guarantee living in the library, not a
caller, is what makes it impossible for a NEW consumer of the send builders to
forget it (exactly what happened to tmux-kit's own `lifecycle.interrupt_session()`
before 0.4.0: see `tmux_kit.keys.build_exit_copy_mode_argv`'s docstring).

A per-session, server-side, persisted list of text items (`state["followups"]`,
`muxplex/followups.py`) that fires one item per bell, until it drains. See
`docs/plans/2026-08-05-per-session-followup-queue-plan.md` for the full design; the load-bearing points a
contributor must not break silently:

- **The queue is a THIRD caller of `terminal_input.input_allowed_for_session()`**
  — the same fence `/input` and the terminal WS gate already both use. No
  bypass, no separate implementation. Re-evaluated at fire time against
  FRESH settings (an append-time check is UX only, not the safety boundary).
- **The seeded bell (v0.36.1, new-session-sorts-to-top) must NEVER advance the
  queue.** It writes `state["sessions"][name]["bell"]` directly inside
  `_run_poll_cycle`'s step 5, never through `receive_bell()` or
  `process_bell_flags()` — the queue's advance hangs off exactly those two
  functions, so the exclusion is structural (a property of where the code
  lives), not a runtime check. Do not route the seed through either function
  "for consistency" — that would silently give the queue a spurious advance.
- **The halt bell (v0.43.0, bell-causality Phase 1b) is a THIRD direct writer
  with the identical exclusion, for the identical reason.** When
  `_advance_followup_queue()`'s failure branch calls
  `followups.set_halted()`, it also calls `_bell_for_halt()` (`main.py`)
  which writes `state["sessions"][name]["bell"]` directly — never through
  `receive_bell()`/`process_bell_flags()`, since the queue's own advance
  hangs off exactly those two, and routing the halt bell through either
  would make the queue trigger itself. Cannot loop for two independent
  reasons, both test-covered: structurally, `_bell_for_halt()` never calls
  `_advance_followup_queue()`; behaviorally, `followups.acceptance_ok()` is
  `False` while a queue is halted, so no bell can advance it again until an
  explicit resume. See `docs/plans/2026-08-07-bell-causality-plan.md` §5.
- **Two live bell-detection paths, one advance rule.** `receive_bell()` (the
  tmux hook) always triggers an advance attempt. `process_bell_flags()` (the
  poll fallback) triggers one ONLY while `_bell_hook_armed` is False —
  a detached session's bell is independently observed by BOTH mechanisms at
  once (see the spec's case A), so triggering from both while armed would
  drain two items for one physical bell. Unarmed, the poll path is what
  keeps the queue from silently stalling until the hook heals.
- **State is a top-level `followups` key, never nested under
  `sessions[name]`** — nesting would inherit the poll cycle's free cleanup
  of vanished session entries, which cannot tell "tmux is briefly
  unreachable" from "every session was deleted" and would wipe queued,
  user-authored text on a transient hiccup. The explicit reaper
  (`followups.reap_stale_queues`, step 6b) only runs when
  `probe_tmux_epoch()` confirms tmux is alive this cycle.
- **`/input`'s argv builders are duplicated, not extracted**, into the
  advance path (`_advance_followup_queue` in main.py) — same rationale as
  `views.matches_name_pattern` vs `terminal_input.session_matches_allowlist`
  above: the two callers have different failure models (`/input` returns
  500 to a waiting caller; the queue halts and preserves the item), so a
  shared mutable implementation would let one silently change the other's
  behavior. Both still call the same `build_send_text_argv`/
  `build_send_key_argv`, so injection-safety is inherited, not re-derived.
- **Federation is out of scope.** Endpoints live only under
  `/api/sessions/{name}/followups` — no
  `/api/federation/{device_id}/sessions/{name}/followups` proxy. Bells are
  local-only state; a remote session's bell is the REMOTE's own concern.

## Foreground focus: `POST /api/focus` (server-side, macOS only)

Brings THIS host's muxplex PWA window to the foreground -- moved server-side
from `muxplex-deck` (backlog item 3 / `docs/plans/2026-08-05-focus-grab-plan.md`)
so every client (hardware deck, soft deck, an agent) asks the same way, over
HTTP, instead of each reimplementing platform-specific focus-stealing.

**`focus_app` is `LOCAL_ONLY_KEYS` for the exact same rule this file already
states for `input_enabled`/`new_session_template`/`session_commands`/etc.:
it names a value the server later feeds to a command it executes (`open -a
<focus_app>`). It must never become `PATCHable` or join `SYNCABLE_KEYS` --
doing so would let a federation Bearer-key holder self-authorize which app
gets launched on the operator's machine, reconstituting the exact
launch-anything capability this fence exists to contain. If you find
yourself "adding flexibility" by letting a request configure this, stop:
that is precisely how the `new_session_template` sibling incident above
happened.**

**The endpoint takes no target of any kind -- no request body, no query
parameter, nothing.** This is the entire security design, and it is why
`focus_app` needs no fence of its own beyond `LOCAL_ONLY_KEYS`: the app
raised is always exactly `settings["focus_app"]`, a value only a local
operator can set by editing `settings.json` on disk. A caller who fully
controls the request can still only trigger the one app the operator
already chose -- there is no `{"app": "..."}` field to add "for
flexibility." Doing so would turn this into `open -a <arbitrary>`, remote
process execution one thin layer removed from `/input`'s RCE-by-design.
`test_focus_endpoint_accepts_no_target` (`tests/test_focus.py`) guards this
property directly -- do not weaken it to accept a target "just for
testing" or "just for federation," either.

Platform support is macOS-only in v1: `muxplex.focus.resolve_focus_capability()`
is the single dispatch point (mirrors `service.py`'s `_is_darwin()` /
`_have_systemctl()` pattern), and every unsupported platform returns an
honest `501`, never a silent no-op. See `docs/API_SEMANTICS.md`'s
`POST /api/focus` section for the full response-ordering rationale and the
`GET /api/instance-info` capability block.

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

## Standing rule: muxplex must never emit anything that renders on a user's terminal

**This is load-bearing and applies to every future feature, not just bells.**
Server diagnostics -- health checks, self-tests, arm-time probes, anything
whose purpose is to verify muxplex's OWN behavior -- belong in the log, in
`GET /api/instance-info`, and in `muxplex doctor`. They must NEVER be built
as a `tmux run-shell` (or any other mechanism that writes to a pane), because
`run-shell`'s output (and a failing command's exit status) is displayed by
tmux in view mode on the client's *active* pane, per tmux's own manual --
completely independent of which session the command logically "belongs" to.
A user attached to any session, watching anything, can have their screen
overwritten by a background diagnostic that has nothing to do with what they
are looking at.

This was learned the hard way, **twice, in the same file, by the same class
of fix**: a revision meant to make a probe's failures diagnosable made the
persistent per-bell hook loud too (see below); the fix for that added a
*separate* arm-time delivery probe that was itself a `tmux run-shell` call,
loud by design -- and because it re-fired on every retry while unarmed (e.g.
every poll cycle during a restart window, before the server was listening),
a `curl ... returned 7` message replaced the owner's live panes just as
surely as the first incident did. The probe was removed entirely rather than
silenced a second time: **the rule is not "keep diagnostic `run-shell` calls
silent," it is "never construct a `run-shell` call for a diagnostic purpose
at all."** A silenced probe is still a probe that can be un-silenced by a
future "make this loud for debugging" change; a probe that structurally does
not exist cannot regress.

**Enforcement:** `_bell_hook_curl()` (main.py) has no parameter that can
request a loud variant -- there is exactly one code path, and it is always
silent (`test_persistent_hook_never_includes_dash_S`,
`test_persistent_hook_redirects_stderr_to_devnull`, and
`test_bell_hook_curl_has_no_loud_variant` in `test_api.py` guard this
structurally, not just by convention). `test_safety_rails.py`'s
`test_no_diagnostic_tmux_run_shell_construction_exists` greps the production
source tree for every `run-shell` call site and asserts there is exactly
one -- the persistent hook's own registration string -- so a future
diagnostic `run-shell` (arm-time probe, health check, anything) fails the
suite the moment it's added, rather than waiting to be discovered on a
live host.

## Bell hook: "armed" means registered, not delivered

`_arm_bell_hook()` (main.py) registers tmux's `alert-bell` hook so a real
bell forwards to `POST /api/sessions/{name}/bell` via a `run-shell 'curl
...'` hook string.

- **The hook must dial the scheme the server is actually serving, and
  cli.py is the only place that knows it.** `serve()` resolves TLS
  (`ssl_certfile`/`ssl_keyfile`) from settings/CLI flags immediately before
  `uvicorn.run()` -- so it sets `MUXPLEX_TLS_ENABLED` in `os.environ`
  *before* importing `muxplex.main`, exactly the pattern `MUXPLEX_PORT`
  already used for `SERVER_PORT`. `main.py`'s `SERVER_TLS_ENABLED` reads it
  at import time; `_bell_hook_curl()` is the ONLY place that builds the
  hook's curl command, and it is the single source of truth for scheme
  (`http`/`https`), host (`127.0.0.1`, never `localhost` -- unambiguous, and
  exactly the address the auth middleware's localhost bypass checks), and
  cert posture (`-k` whenever TLS is on -- this is a same-host loopback
  call, so there's no MITM to guard against, and the cert may be
  self-signed / signed by muxplex's own local CA / a Tailscale-hostname-only
  cert that doesn't cover `127.0.0.1` at all; mirrors the identical
  established pattern in `_probe_service_port` / `_fetch_local_instance_info`).
  **Incident:** the hook hardcoded `http://localhost` unconditionally. On
  any host actually serving TLS, `curl -sfo /dev/null ... || true` failed
  silently on every real bell (curl exit 52, swallowed by `-sf` + `|| true`)
  for the life of every process, forever -- while `bell_hook_armed` reported
  `true` the entire time, because registration (`set-hook`) succeeded
  perfectly. `bells.process_bell_flags()`'s fallback (see below) carried
  bell detection the whole time, which is exactly what hid it.
- **`bell_hook_armed` means `set-hook` was accepted -- nothing more, and
  this is a deliberate reversion.** A later revision (superseded by this
  one) tried to strengthen "armed" to mean "a delivery PROBE actually
  arrived": after registering, `_arm_bell_hook()` fired the EXACT command
  tmux would run on a real bell via `run_tmux("run-shell", ...)`, targeting
  a reserved sentinel session, and waited for that request to reach
  `receive_bell()` before reporting armed. That probe violated the standing
  rule above -- it was itself a diagnostic `run-shell` call, and it re-fired
  on every retry while unarmed, painting `curl ... returned 7` onto the
  owner's live panes during restart windows. **It was removed, not
  re-silenced.** The honest, weaker contract this reverts to: `set-hook`
  succeeding is real information (it rules out "tmux unreachable," the
  common startup-ordering failure this self-heals from), but it is NOT
  proof of delivery -- a scheme mismatch, for example, registers perfectly
  and still never delivers a bell, and there is currently no arm-time
  mechanism that would catch that class of bug without reintroducing the
  standing-rule violation. `muxplex doctor` surfaces `bell_hook_armed` (from
  `GET /api/instance-info`) the same non-fatal-advisory way it already
  surfaces TLS cert expiry -- read it as "registered," not "verified
  working."

`bells.poll_bell_flag()`'s fallback has its own window-scoping gotcha, also
verified live: `display-message -t <session>` (no window qualifier) reads
only the session's CURRENT (active) window's `window_bell_flag` -- a bell in
a background window sets THAT window's flag while the active window's stays
`0`, and goes completely undetected. Fixed by polling `list-windows -t
<session>` (every window) instead. The *stuck*-flag behavior this fallback
also has -- a flag that never clears (no client ever views that window)
means only the first bell is ever counted, since tmux exposes a boolean, not
a counter -- is correct-as-designed and already documented in
`process_bell_flags()`'s docstring; the hook fix above is what actually
matters here, since the poll fallback is meant to cover only the brief
window before the hook arms.

**The PERSISTENT hook must be silent, always -- loudness belongs only at
arm time.** `_bell_hook_curl()` builds two different commands from one
function, and they must stay different: the one-shot arm-time PROBE
(`swallow=False`) is deliberately loud (`-S`, no `|| true`) so a failure is
diagnosable in `_bell_hook_last_error`; the PERSISTENT hook (`swallow=True`,
registered via `set-hook -g`) fires on every real bell, in every session,
for the life of the process, with a client very likely attached and
watching -- it must never carry `-S`, and its stderr is explicitly
redirected to `/dev/null` on top of that, independent of curl's own
silence.

**Incident:** a revision meant to make probe failures diagnosable made
`_bell_hook_curl()` build ONE command for both callers (`-sSf`, no stderr
redirect) and only varied the trailing `|| true`. tmux's `run-shell`, per
its own manual, displays a background command's output in view mode on the
client's active pane when the command isn't `-C`/quiet -- so every real
bell whose curl call failed (independent of whether `|| true` swallowed the
*exit code*) painted curl's stderr text onto whatever the owner was looking
at. Confirmed live: `returned 52` repeatedly replaced the owner's screen,
across every one of his live sessions, for the life of the process. The
instinct to make failure loud was correct -- silence is what hid the
original TLS-scheme bug for so long -- but the hook is the wrong place for
it: loudness belongs in the one-shot probe, the log, `muxplex doctor`, and
`GET /api/instance-info`'s `bell_hook_armed`, never on a live client's
screen on every bell. See `test_persistent_hook_never_includes_dash_S` /
`test_persistent_hook_redirects_stderr_to_devnull` in `test_api.py` --
these are regression guards for exactly this, not incidental assertions.

**Any test or proof that arms this hook for real must run against an
isolated tmux server -- never the ambient one.** `_arm_bell_hook()`'s
`set-hook -g` is GLOBAL TO THE WHOLE TMUX SERVER. A delivery proof for this
exact fix once called the real function against a scratch TLS port but
never overrode `TMUX_TMPDIR`/`tmux_socket_dir` -- and because the proof's
own shell already had `TMUX_TMPDIR` exported (from `muxplex env`, see
"Running a second instance on one box" below), `tmux_env()` resolved to the
OWNER'S REAL tmux server. Every real bell across 53 live sessions then
curled the dead scratch port for as long as the hook stayed armed.
Hand-repaired once; not something to rediscover. `muxplex/tests/conftest.py`
now enforces this structurally rather than relying on a future author
remembering: `_isolate_tmux_socket_dir` is an AUTOUSE fixture that forces
`TMUX_TMPDIR` to a fresh per-test directory (and unsets `$TMUX`) for every
test by default -- see that fixture's docstring, and
`test_safety_rails.py`'s `test_tmux_socket_dir_is_isolated_by_default`,
which fails if it is ever removed or weakened. A test that wants a REAL,
working isolated tmux server on top of this still layers its own explicit
`-L <unique-name>` socket, same as `test_integration.py`'s `tmux_server`
fixture already does.

**The hook payload cannot be enriched -- verified live, not reasoned, so
don't rediscover this on a real host.** `docs/plans/2026-08-07-bell-causality-plan.md`
§1.1/§8 measured four things against a fresh isolated tmux 3.4 server before
concluding a `reason` field on the bell is unbuildable on this path:

- **F1** -- the hook resolves the BELLING window correctly (`#{window_index}`
  etc. are trustworthy), confirmed by firing a bell in an inactive window.
- **F2** -- pane-scoped format variables (`#{pane_current_command}`,
  `#{pane_current_path}`, `#{pane_id}`) are **confidently wrong** in any
  split-window layout: they resolve the window's *active* pane, not the
  belling one. Firing a bell in a non-active pane after `cd /etc` still
  logged the active pane's original path. Any field built from these would
  be silently, plausibly, incorrectly attributed.
- **F3** -- a pane CAN smuggle a payload the hook can read, via OSC 2
  (`printf '\033]2;marker\007'` then `printf '\a'` makes the marker
  readable as `#{pane_title}`). tmux's own `\ek..\e\\` window-rename escape
  does NOT work (`allow-rename` is off by default).
- **F4** -- that channel is **sticky and must never be used**: two
  subsequent, unrelated bare `printf '\a'` calls in the same pane both
  logged the SAME stale marker. A pane title is durable state; a bell is an
  event; the mismatch means the "cause" of bell #3 would silently read as
  the cause of bell #1.

Net effect, and why `bell.source` (the shipped feature) stops at "which
detection path fired," never "why": intent is information-theoretically
unrecoverable from a one-byte `\a` (finding (a) in the plan), the pane
context the hook *could* carry is exactly the part F2 proves is wrong in the
common case, and the one working side-channel is disqualified by F4. If you
are about to propose enriching `_bell_hook_curl()`'s payload, read
`docs/plans/2026-08-07-bell-causality-plan.md` §1.1 first -- the evidence
already exists.

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

## Push `main` first, tag only once CI is green — a release-time rule

**Publishing is irreversible. A version number, once on PyPI, can never be
reused.** `.github/workflows/publish.yml` triggers on `v*` tags, so the tag
IS the publish. Sequence a release so the tag is the last thing that
happens, and so it happens only against evidence:

1. `git push origin main:main` — explicit refspec, no `--tags`, no
   `--follow-tags`, no `--all`.
2. **Wait for CI to go fully green on that exact commit.** Every job, not
   just the ones that usually matter.
3. Only then `git tag -a vX.Y.Z` and `git push origin refs/tags/vX.Y.Z`.

**Never push `main` and the tag in one command.** Doing so starts CI and
Publish simultaneously, which means publish cannot be gated on CI: by the
time the suite reports, the wheel is already on PyPI and the version is
burned.

**Why this is written down (v0.48.1 and v0.48.2, both ways):** at v0.48.1
both refs went up in a single command. CI was already red at the time, so
the ordering changed nothing that release — but the *sequencing had made
the decision, not the evidence*, and that is only safe by luck.

At v0.48.2 the split was used for the first time, and it immediately paid
for itself. The Linux suite was green (2,472 passing), the DTU run was
green, and every local check passed — but the `test (macOS, arm64)` job
failed on the pushed commit, on a test the release's own fix had
invalidated. Under the old single-command sequencing, `0.48.2` would have
been published from a red tree and the number permanently spent. Under the
split, the fix was a normal follow-up commit on `main` with nothing to
undo: no tag existed, so no publish had fired.

The cost of the split is a few minutes of CI wait. The cost of skipping it
is a version number you cannot get back. **If CI is red, stop and report —
do not tag.** Shipping tomorrow is strictly cheaper than burning a version
today.

## tmux-kit pin/tag agreement — a release-time rule, checked twice

`pyproject.toml` carries the `tmux-kit` dependency in TWO places that must
always agree:

```toml
[project.dependencies]
"tmux-kit==0.1.0",                                              # X.Y.Z

[tool.uv.sources]
tmux-kit = { git = "https://github.com/bkrabach/tmux-kit", tag = "v0.1.0" }  # vX.Y.Z
```

**When you bump the `tmux-kit==` pin, bump the `tag` in the SAME commit —
never one without the other.** Both name a version; they must name the
*same* version.

**Why this matters (v0.44.0 rhymes with this, and this is the same failure
mode arriving a second way):** a public `uv tool install muxplex` only ever
sees the plain `==` pin — `[tool.uv.sources]` never enters `Requires-Dist`
in a published wheel, verified by inspecting a real built wheel's `METADATA`.
A managed-device `uv tool install git+https://github.com/bkrabach/muxplex@vX`
*does* resolve tmux-kit from the git source instead — verified via
`tmux_kit-*.dist-info/direct_url.json` showing `vcs_info` with the resolved
commit. If the pin and the tag ever name different versions, **a PyPI
install and a git install of the identical muxplex release silently run
different tmux-kit code.** Both installs succeed; nothing errors; the drift
is invisible until behavior differs between a PyPI user and a git user.
That is the same class of incident as v0.44.0 (PyPI users and git users
silently diverging) — this variant is quieter because neither install
fails.

**The load-bearing caveat that makes "bump both" insufficient by itself:**
a git tool install resolves tmux-kit from whatever `uv.lock` already
records — not a fresh re-resolve of `pyproject.toml`. Editing the
`[tool.uv.sources]` entry (or the pin) without immediately running
`uv lock` and committing the regenerated lock file leaves `uv.lock`
pointing at the OLD source, and the git installer silently keeps resolving
from the stale one as if the edit never happened. **Always run `uv lock`
in the same commit as any change to either the pin or the source tag**, and
check that `uv.lock`'s `tmux-kit` package entry shows
`source = { git = "...?tag=vX.Y.Z#<new-commit>" }` with the expected tag —
not a leftover `source = { registry = ... }` or an old commit hash.

**Checked twice, both ways:**
- `.github/workflows/ci.yml`'s `guard-tmux-kit-pin-source-agreement` job
  parses `pyproject.toml` and fails the build if the pin and tag disagree,
  or if the source is a `path` (a reverted-too-late cross-repo dev-loop
  override — see below) instead of `git`.
- `muxplex/tests/test_tmux_kit_pin_source_agreement.py` asserts the
  identical invariant, so it fails `make test`/`pytest` too, not only CI.

**The cross-repo dev loop still exists and still needs a revert.** Working
on tmux-kit and muxplex together, `uv add --editable ../tmux-kit` (or a
manual `{ path = ... }` edit) is the normal way to iterate with a local
tmux-kit checkout. Revert that `path` override back to the committed `git`
source before committing — both guards above reject a committed `path`
entry on sight.

### `muxplex upgrade`'s `--with tmux-kit` override must NEVER be added on top of a git muxplex target

**Incident (2026-08-15, v0.47.11): a real `muxplex update` failed with**
`Requirements contain conflicting URLs for package \`tmux-kit\`` — naming
**two byte-identical URLs**. uv rejects two requirement origins that both
carry a URL for the same package, even when the URLs agree exactly; it is
not a disagreement check, it is a "how many places named a URL for this
package" check.

Root cause: `upgrade()`'s uv-managed branch was unconditionally appending
`--with 'tmux-kit @ git+<url>@<ref>'` whenever tmux-kit's recorded source
was git — including when muxplex's OWN install target was ALSO a git
target (`git+https://github.com/bkrabach/muxplex@vX`). But a git muxplex
target's own `pyproject.toml` already carries the `[tool.uv.sources]` pin
documented at the top of this section — uv reads and honors that pin on
its own, with no override needed. The `--with` override is then a SECOND
url-bearing origin for the identical package, and uv refuses to resolve.

**The fix:** the override is only issued when muxplex's own install target
is NOT git (i.e. the PyPI-target case, where the published wheel's
metadata has stripped `[tool.uv.sources]` per the section above — there,
`--with` is the ONLY thing that can pin tmux-kit to git, and it must stay).
For a git muxplex target, the git target's own pin is sufficient by
itself — proven by installing the exact real-world command with `--with`
removed: it resolves tmux-kit from git at the expected ref with no error.
See `_install_cmd_preserves_kit_override`'s docstring in `cli.py` for the
full writeup and the corrected guard (it now REQUIRES the override's
ABSENCE for a git muxplex target, and its presence for every other target
— both directions are load-bearing, not just one).

**Why this wasn't caught sooner:** every test and design note that shaped
the original `--with`-override mechanism
(`docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md` §2.5) exercised
tmux-kit-is-git against a muxplex target that was either unconstrained or
implicitly PyPI-shaped — none combined "muxplex itself git-sourced" with
"tmux-kit git-sourced" through the real uv resolver. **We could not
determine why this exact pairing hadn't been hit on an earlier upgrade on
this host** (both muxplex and tmux-kit have been git-sourced here for a
while) — stated plainly rather than guessed at; possibly earlier upgrades
landed before both pins independently pointed at the same tag, or uv's own
conflicting-URL detection changed between versions. Do not invent a cause
if you're reading this and can't find one either — say so, the same way.

## Testing & workflow

### The suite is safe to run on a host running a live muxplex — by structural isolation, not by refusal

`uv run pytest` on a developer box that is also serving muxplex caused real
production damage, twice in one session (2026-07-25):

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

The original fix was a `pytest_sessionstart` hook that refused to run the
ENTIRE suite whenever anything answered the default port — safe, but it meant
the suite could never run at all on a host already serving a live muxplex
(this repo's own primary dev host included), even though nothing in the suite
still needed that port. **That guard has been replaced with structural
isolation**: every fixture below is autouse, applies to EVERY test regardless
of what else is running, and together makes the dangerous outcome impossible
by construction rather than merely refusing to proceed. Read
`muxplex/tests/conftest.py`'s module docstring before changing anything there;
`test_safety_rails.py` fails if a rail is removed or weakened. The rails:

| Rail | Stops |
|---|---|
| autouse `SETTINGS_PATH` → tmp | Tests reaching the real user config (closes incident 1) |
| autouse tmux-socket isolation | Tests' real tmux subprocess calls reaching the ambient/live tmux server |
| autouse killer-neutering | Tests SIGTERMing whatever owns the port (closes incident 2's signal step) |
| autouse `uvicorn.run` neutering | Any test opening a REAL listening socket for the app by accident (closes incident 2's root cause) |
| `pytest_sessionstart` structural (AST) scan | A NEW test reintroducing incident 2's exact shape (opts into the real killer without pinning a port) — fails at collection, not merely a code-review nit; never refuses just because something else is running |
| `test_safety_rails.py` | Silent removal or weakening of any of the above |

To reach the real port killer or the real `uvicorn.run`, a test must opt in
explicitly with `@pytest.mark.allow_real_port_killer` /
`@pytest.mark.allow_real_uvicorn_run` — visible in review, and (for the
latter) still required to pin an OS-allocated port via the `free_port`
fixture. Proven on this host: the full suite (`.venv/bin/python -m pytest
muxplex/tests/`) passes with the real production muxplex listening on 8088
the entire time, its PID unchanged before and after (verified via `ss -ltnp`).

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

**`make test` is not safe to run concurrently against the same DTU name.**
The default target (`DTU ?= muxplex-test`) pushes a fresh `git archive HEAD`
tarball and extracts it over `/opt/muxplex` inside that one named container on
every invocation. Two builders (or two agent sessions) running `make test` at
the same time against the default DTU name race each other's extraction and
corrupt the shared tree mid-run. The observed symptom was **not** an obvious
"file changed under me" error: `inspect.getsource`-based tests
(`test_ws_proxy.py`, `test_shutdown.py`) returned the WRONG function's source
text, producing 14 failures that vanished on a clean, non-concurrent re-run.
If source-text assertions fail in a way that looks impossible given the code
you're looking at, suspect a concurrent `make test` run before suspecting your
change. Run concurrent builders against distinct DTU names
(`make test DTU=muxplex-test-<yourname>`) or serialize `make test` invocations.

There is no environment-variable override anymore (`MUXPLEX_TEST_ALLOW_LIVE_HOST`
is retired along with the refusal it used to bypass) — the structural guard has
no bypass on purpose: a test either has the dangerous shape (fix it) or it
doesn't (nothing to override). The DTU remains the recommended way to test —
it's still how CI and release validation work, and still what proves you're
testing the exact artifact you're about to push — but it is no longer required
just to run the suite safely on this host.

- Python: `uv sync --extra dev && uv run pytest` (an isolated env is no longer
  required for safety, but is still recommended for reproducibility; tests
  marked `integration` need a real tmux binary and are deselected by default).
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

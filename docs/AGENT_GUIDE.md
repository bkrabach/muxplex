# Driving muxplex from an agent

**Audience:** anyone writing a program that talks to muxplex's HTTP API — an AI
coding agent, an automation script, a Stream Deck sidecar, a `curl` one-liner in
a cron job. Nothing here assumes a particular agent framework or vendor.

**Point an agent at this file.** It is the operational contract: how to
authenticate, how to read state, how to create and destroy sessions, and — the
part that needs the most care — how to *type into* a live terminal session.

Related docs, and why they aren't this one:

| Doc | What it is |
|---|---|
| [`README.md`](../README.md) | Human install/config reference. The settings table defines `input_enabled` / `input_allowed_sessions` as **configuration**. |
| [`AGENTS.md`](../AGENTS.md) | Conventions for **developing muxplex itself** — invariants a contributor must not break. |
| [`API_SEMANTICS.md`](API_SEMANTICS.md) | **Why** the API behaves as it does — the predicates, timestamps, preconditions and write-path guards clients re-derive, each with the incident that produced it. Read it if you are *implementing* a client rather than scripting one. |
| **this file** | How to **drive** a running muxplex from outside. |
| `/openapi.json`, `/docs` | The machine-readable contract, served by the running instance. Authoritative for exact request/response shapes. |

Everything below is checkable against the source; file references are given so
you can verify rather than trust.

---

## 0. Conventions used in the examples

Every example is copy-pasteable once you export two variables:

```bash
export MUXPLEX_URL="https://my-host:8088"          # your instance
export MUXPLEX_KEY="$(cat ~/.config/muxplex/federation_key)"   # on the server
```

All examples send `Accept: application/json` deliberately — see
[Authentication](#1-authentication) for why that matters.

---

## 1. Authentication

muxplex has a single shared auth middleware (`muxplex/auth.py`,
`AuthMiddleware.dispatch`). A request is accepted if **any** of these hold, in
this order:

1. **The connection came from localhost.** `127.0.0.1` or `::1` at the *socket*
   level (`request.client.host`) — not the `Host` header, so it cannot be forged
   by a remote client. An agent running on the same box as the server needs no
   credential at all.
2. **The path is public.** `/api/instance-info` and `/api/ca` are exempt by
   design (`auth._AUTH_EXEMPT_PATHS`), plus `/login`, `/auth/mode`,
   `/auth/logout`. Paths ending in a static-asset extension (`.css`, `.js`,
   `.json`, `.svg`, `.png`, `.ico`, `.woff`, `.woff2`, `.ttf`, `.map`) are also
   served without auth so the login page can load its own assets.
3. **A valid `muxplex_session` cookie** (what a browser gets after logging in).
4. **`Authorization: Bearer <federation key>`** — this is the headless path, and
   the one an agent uses.
5. **`Authorization: Basic <base64 user:pass>`** — password or PAM, depending on
   the server's auth mode.

### The Bearer key

* Lives at `~/.config/muxplex/federation_key` **on the server** (mode 0600).
  Override the path with the `MUXPLEX_FEDERATION_KEY_FILE` env var
  (`settings.load_federation_key`).
* Read fresh from disk on every request, so rotating it takes effect immediately
  — no server restart.
* Generate one with `POST /api/federation/generate-key`, which writes the file
  and returns `{"key": ..., "path": ...}`. (That endpoint is itself behind auth,
  so bootstrap it from localhost or the browser UI.)
* Compared with `hmac.compare_digest` — constant-time.

```bash
curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" \
        -H "Accept: application/json" \
        "$MUXPLEX_URL/api/sessions"
```

### Always send `Accept: application/json`

On an unauthenticated request the middleware branches on the `Accept` header: it
returns **401 with a JSON body** when `application/json` is present, and a **307
redirect to `/login`** otherwise. An agent that omits the header gets a redirect
to an HTML login page and — if its HTTP client follows redirects — a `200 OK`
full of HTML, which is a very confusing way to discover you aren't
authenticated.

### Network posture is the outer fence

Everything above is *application-layer*. The load-bearing question — **who can
reach the port at all** — is deliberately outside muxplex's scope. Bind to
localhost and reach it over an SSH tunnel, put it on a private overlay network
(Tailscale et al.), or firewall it. Treat the Bearer key as one layer, not the
perimeter.

---

## 2. Discover the instance

```bash
curl -sS "$MUXPLEX_URL/api/instance-info"      # no auth required
```

```json
{
  "name": "spark-1",
  "device_id": "…",
  "version": "0.16.1",
  "federation_enabled": true,
  "tmux_socket_dir": "/home/you/.tmux"
}
```

`version` is the running server's version (`importlib.metadata.version("muxplex")`
at app construction). Useful for feature-gating: if a field or endpoint you need
landed in a later release, check here rather than probing and handling a 404.

`tmux_socket_dir` matters if your agent ever creates tmux sessions **directly**
instead of through the API. muxplex only sees sessions on *its* tmux server. A
session created without a matching `TMUX_TMPDIR` lands on a different socket and
is silently invisible — no error, it just never appears. Either create sessions
through `POST /api/sessions` (which uses the right environment automatically), or
export the value from this endpoint before shelling out to `tmux`.

Also useful: `GET /health` → `{"status": "ok"}` — the cheapest liveness probe, but
note it is **not** in the auth-exempt set, so a remote caller still needs a
credential. `GET /api/instance-info` is the one to use for unauthenticated
reachability checks.

---

## 3. Reading state

Four read endpoints, each answering a different question. All require auth
except `/api/instance-info`.

### `GET /api/sessions` — everything, with pane contents

```bash
curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions"
```

```json
[
  {
    "name": "agent-build",
    "snapshot": "…captured pane text…",
    "bell": {"last_fired_at": 1753500000.0, "seen_at": null, "unseen_count": 1},
    "last_activity_at": 1753500123.0
  }
]
```

`snapshot` is the pane capture — this is how an agent *reads* what a terminal is
showing. Comparatively expensive; don't poll it at high frequency.

### `GET /api/view` — the server's resolved answer

```bash
curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/view?sort=attention"
```

```json
{
  "view": "all",
  "views": ["all", "work", "personal"],
  "sort": "attention",
  "sessions": [
    {"name": "agent-build", "active": false, "needs_attention": true,
     "bell": {…}, "last_activity_at": 1753500123.0}
  ]
}
```

**Prefer this over re-deriving view rules yourself.** It applies view membership,
the needs-attention bell predicate, and sort ordering server-side. `sort` is
either omitted (honors the server's `sort_order` setting; reports back
`"server"` or `"alphabetical"`) or `attention` (bells first, then the active
session, then recency). Any other value is a **400 — no silent fallback**.

Deliberately carries **no pane snapshots**, so it stays cheap for frequent
polling. Local sessions only — federated peers are not merged in.

### `GET /api/state` — persistent state

```json
{
  "active_session": "agent-build",
  "active_remote_id": null,
  "active_view": "all",
  "session_order": [],
  "sessions": {"agent-build": {"bell": {…}}},
  "devices": {},
  "settings_updated_at": 1753499000.0
}
```

`settings_updated_at` is merged in at request time from settings (it is *not*
stored in `state.json`). If you already poll `/api/state`, compare this value
against the last one you saw to detect a settings change — including view-membership
edits made from another device — without adding a second poll.

### `GET /api/settings` — configuration

Returns the full settings dict with `federation_key` and per-remote keys blanked
out. Read-only for most purposes; see [§6](#6-what-an-agent-may-not-do) before
reaching for `PATCH`.

---

## 4. Session lifecycle

### Create

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d '{"name":"agent-build"}' \
     "$MUXPLEX_URL/api/sessions"
```

→ `{"name": "agent-build", "ok": true}`

Names must match `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$` (`sessions.SESSION_NAME_RE`)
— ASCII letters, digits, `_ . -`, first character alphanumeric-or-underscore,
1–64 chars. No whitespace, no `:`, no leading `-` or `.`. Anything else is a
**400** at the boundary, before the name reaches any subprocess. The same rule
applies to every endpoint that takes a session name in the path.

The server runs the operator's configured `new_session_template`, which may be
something other than plain `tmux new-session`. A long-running template that
hasn't finished in 30 seconds is **not** treated as a failure — the endpoint
returns success and expects you to poll for the session to appear.

### Connect (point the web terminal at a session)

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions/agent-build/connect"
```

→ `{"active_session": "agent-build", "ttyd_port": 7682}`

This is for the *human's* browser view. An agent does not need to connect in
order to read or type — `/api/sessions` and `/input` work on any session.

### Delete

```bash
curl -sS -X DELETE -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions/agent-build"
```

→ `{"ok": true, "name": "agent-build"}` (400 on an invalid name, 404 if unknown).

`DELETE /api/sessions/current` is different: it disconnects the web terminal
without killing anything.

### The read model is eventually consistent — poll on a short interval, not a long sleep

⚠️ **This is the single most common way an agent gets confused.** GET endpoints
serve a **~2 second poll cache**. A session you just created via
`POST /api/sessions` does not appear in `GET /api/sessions` — and **404s on
`/connect` and `/input`** — until the next poll cycle catches up.

That 404 is not "your session failed to create." It is the cache.

**Measured, not assumed:** in traced runs, the new session was visible well
under 1 second after create — one trace resolved on the 3rd poll attempt at
0.3-second spacing (~0.9s elapsed total). A flat `sleep 3` wastes most of that
time waiting on a race that's usually already over. Poll on a short interval
with a generous ceiling instead:

```bash
curl -sS -X POST … -d '{"name":"agent-build"}' "$MUXPLEX_URL/api/sessions"

for _ in $(seq 1 20); do
  sleep 0.3
  curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
       "$MUXPLEX_URL/api/sessions" | grep -q '"agent-build"' && break
done
```

20 attempts at 0.3s is a 6-second ceiling — comfortably above the ~1s typical
case, so a genuinely slow poll cycle still resolves without a rewrite. This is
a known limitation, documented in `API_SEMANTICS.md`, with a candidate fix
(write-through cache refresh on create/delete) on the roadmap. It is a race, not
a mystery — poll for it, don't guess a fixed delay.

### `active_view` / `active_session` are server-global, last-writer-wins

There is one active session and one active view **per server**, shared by every
connected client: the browser tab, the Stream Deck sidecar, and your agent. If
your agent calls `/connect` or `PATCH /api/state`, it moves the human's view too.

This is deliberate for a single-user, multi-device system — the whole point is
that switching on the Stream Deck moves the phone. But it surprises agent authors
who assume per-client state. **An agent that only reads panes and types into them
never needs to touch either field**; prefer that over changing what the human is
looking at.

**`device_id` lets a caller opt out of the shared selection — omitting it
changes nothing.** `GET`/`PATCH /api/state`, `GET /api/view`,
`POST /api/sessions/{name}/connect`, and `DELETE /api/sessions/current` all
accept an optional `?device_id=` query param that scopes the read/write to
that device's own private selection instead of the one shared by everyone.
**Everything said above this paragraph assumes no `device_id` is sent, and
stays exactly true for a client that doesn't send one** — that's still the
default, and still what every existing agent/script does. Adopting a
`device_id` (and registering it via a heartbeat) is a real opt-in step, not
something that happens implicitly; there's normally no reason for an agent
to do it unless it specifically wants its own view/session selection kept
separate from the human's.

**`POST /connect` can now return `409` with a
`{"terminal_conflict": true, ...}` body** instead of succeeding. There is
exactly one underlying terminal process shared by the whole server, no
matter how many separate selections exist — so if some *other* device has
already claimed it for a different session, a caller asking for a session on
top of that gets refused rather than silently yanking it away. **An agent
that never sends `device_id` will essentially never see this** — refusal
only happens *between* different selections, and an agent staying in the
shared, default selection is always "the same one" as itself. If your agent
does adopt its own `device_id` and hits a 409, treat it like any other
error unless deliberately reclaiming the terminal is the intended action —
in which case retry the same request with `&takeover=true`. Do that only
when it's genuinely what you mean: it moves the terminal away from whichever
device/session held it.

---

## 5. Typing into a session: `POST /api/sessions/{name}/input`

This is the capability that lets an agent actually *operate* a terminal —
answer a prompt, hit Ctrl-C, run a command. Read this whole section before using it.

### 5.1 What it is, honestly

**Typing an executable line into a pane that is running a shell, with
`enter=true`, will run that line.** That is not a bug or an edge case — it is
the endpoint's purpose. This is remote code execution by design.

What muxplex *does* guarantee is narrower and worth stating precisely:

* Text is delivered with `tmux send-keys -l` (literal mode) through
  `asyncio.create_subprocess_exec` — **argv, never a shell**
  (`terminal_input.build_send_text_argv`). No shell that muxplex spawns ever
  sees your text.
* `--` terminates tmux's own option parsing, so text starting with `-` stays
  data rather than becoming a flag.
* This is pinned by a test, not just asserted in prose:
  `test_text_sent_literally_via_argv` (`muxplex/tests/test_input.py`) posts the
  payload ``; rm -rf / && $(reboot) `id` | tee /etc/passwd`` and asserts the
  resulting argv is exactly
  `("send-keys", "-l", "-t", "alpha", "--", <payload>)` — **one argv element,
  unmodified, with no shell anywhere in the chain**.

**What is explicitly NOT claimed:** that content is sanitized, filtered, or made
safe. If the pane is a shell, the pane will do what a shell does. Any guide
telling you otherwise would be lying to you.

### 5.2 The threat model — what is actually being protected

The asset is **not the box**. A caller holding the federation Bearer key can
already create and delete sessions; a session-creating template runs arbitrary
configured commands. The box is not the thing the fence is defending.

The asset is **the human's own live pane** — an already-authenticated interactive
shell carrying the operator's ambient authority: their sudo timestamp, their
loaded SSH agent, their logged-in cloud CLIs, their production kubeconfig. Typing
into *that* is qualitatively different from starting a fresh session.

And here is the uncomfortable part that shapes every design decision below: **the
endpoint's intended caller is also its most capable attacker.** Agents are handed
the same Bearer key that satisfies the rest of the API. There is no credential
separating "the agent we trust" from "the agent that has gone wrong," so the
fence cannot be a credential. It is a *list*.

### 5.3 The security boundary is the allowlist, not the content

Two fences, both **default-closed**, in `~/.config/muxplex/settings.json`:

```json
{
  "input_enabled": true,
  "input_allowed_sessions": ["agent-*"]
}
```

* **`input_enabled`** (default `false`) — global opt-in. Anything but the boolean
  `true` is off; the check is `settings.get("input_enabled") is not True`, so a
  hand-edited `"input_enabled": "false"` (a truthy *string*) correctly disables
  rather than enabling.
* **`input_allowed_sessions`** (default `[]`) — which session names may be typed
  into. **An empty list denies everything.** It is never interpreted as "no
  restriction." A non-list value is treated as empty; non-string entries in the
  list are skipped rather than crashing the endpoint.

**Glob semantics** (`terminal_input.session_matches_allowlist`):

| Pattern | Matches |
|---|---|
| `"*"` | every session |
| `"agent-*"` | `agent-build`, `AGENT-Build`, `agent-` … |
| `"build"` | only `build` (and `BUILD`, `Build`) |
| `[]` | nothing |

Matching is **case-insensitive**, achieved as explicit `.casefold()` on both the
name and the pattern followed by `fnmatch.fnmatchcase`. This is deliberate and
must not be "simplified" to plain `fnmatch.fnmatch`: that function gets its
case-folding as a side effect of `os.path.normcase`, which is a no-op on Linux
and case-folding on macOS/Windows. Plain `fnmatch` would make the fence
*platform-dependent* — the same config denying on one machine and allowing on
another. A security fence whose behavior varies by OS is not a fence.

### 5.4 Status codes are a decision tree — and the order is a security property

The checks in `main.py`'s `send_session_input`, in the exact order they run:

```
400   invalid session name              → fails ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$
403   "Session input is disabled"       → input_enabled is not exactly True
403   "does not match any input_allowed_sessions pattern"
404   "Session '<name>' not found"      → allowlisted, but not in the known-session set
413   text too large                    → > 8192 bytes UTF-8
400   too many keys                     → > 64
400   unsupported key(s)                → not in ALLOWED_KEYS
400   no input provided                 → text empty AND keys empty AND enter false
500   failed to send input              → tmux exec failure
```

**The allowlist check runs before the existence check on purpose.** If existence
were checked first, a 403-vs-404 difference would turn the endpoint into an
existence oracle: a caller could enumerate which sessions the human has running
by watching which status code comes back. Because the allowlist gate comes
first, a non-allowlisted name returns 403 whether or not it exists.

For the same reason, **the two 403s differ only by detail string**. Both mean
"you may not type here." Don't build logic that treats them as different states
— treat any 403 as "this is fenced; ask the operator."

Interpreting the rest:

* **404 after a create is almost always the poll cache** (see
  [§4, "The read model is eventually consistent"](#the-read-model-is-eventually-consistent--wait-3s-after-writes)).
  Retry after ~3s before concluding the session doesn't exist.
* **400 on the name** means your name is malformed, not that it's disallowed.
* **500** means tmux itself failed — e.g. the session vanished mid-flight.

### 5.5 The request contract

```json
{ "text": "ls -la", "keys": ["Enter"], "enter": false }
```

All three fields are optional and default to `""` / `[]` / `false`, but **at
least one must be non-empty** (else 400).

**Send order is `text` → `keys` → `enter`.** Fixed, and worth internalizing:
text is typed first, then each named key in list order, then Enter if `enter` is
true.

* **`text`** — typed literally. Max **8192 bytes UTF-8** (413 above that). The
  cap keeps a single argv element well below the platform's `E2BIG` limit.
* **`keys`** — a **closed allowlist** of named tmux keys
  (`terminal_input.ALLOWED_KEYS`); anything else is a 400. Max 64 per call (each
  key forks one tmux subprocess, so an unbounded list is a fork amplifier):

  ```
  Enter  Escape  Tab  C-c  C-d  Up  Down  Left  Right  PageUp  PageDown
  ```

  These go to the **pane's** input stream. A control key like `C-c` is delivered
  to the program running in the pane — it is *not* interpreted as a tmux prefix
  or command. (`C-b` isn't in the allowlist, but the same principle would apply:
  `send-keys` delivers to the pane, not to tmux's command layer.)
* **`enter`** — press Enter after `text` and `keys`.

`keys` and `enter` are **independent knobs**, and that has one sharp edge:
`{"keys": ["Enter"], "enter": true}` sends **two** Enters. That's intentional
(each is its own control), but it's the kind of thing that quietly submits a
blank line at a prompt. Pick one.

### 5.6 Read-back: verify, don't guess

Every accepted call settles for ~400 ms, re-captures the pane, and returns it:

```json
{ "ok": true, "session": "agent-build", "snapshot": "…pane text after your input…" }
```

**This is a correctness feature, not a security one.** It exists so a typing
agent can *check what happened* instead of assuming. Use it: after typing a
command, read the snapshot and confirm the prompt moved, the command echoed, or
the expected output appeared, before sending the next thing. An agent that fires
input blind and never reads the response is the agent that types the second
command into a prompt that was still waiting on the first.

Note the ~400 ms settle is a heuristic, not a completion signal. A slow command
will not be finished. For anything long-running, poll `GET /api/sessions` and
watch that session's `snapshot` and `last_activity_at`.

### 5.7 Worked examples

Type a command and run it:

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d '{"text":"ls -la","enter":true}' \
     "$MUXPLEX_URL/api/sessions/agent-build/input"
```

Answer a `[y/N]` prompt without a newline of your own:

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d '{"text":"y","enter":true}' \
     "$MUXPLEX_URL/api/sessions/agent-build/input"
```

Interrupt a runaway process (no text, just the key):

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d '{"keys":["C-c"]}' \
     "$MUXPLEX_URL/api/sessions/agent-build/input"
```

Navigate a menu, then confirm:

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d '{"keys":["Down","Down","Enter"]}' \
     "$MUXPLEX_URL/api/sessions/agent-build/input"
```

Read the pane without typing anything (there is no "empty input" — use the
regular read path):

```bash
curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions" | jq -r '.[] | select(.name=="agent-build") | .snapshot'
```

### 5.8 Auditing

Every accepted action logs exactly one `info` line: session name, character
count, the `enter`/`keys` flags, and a **≤16-character redacted preview**. Full
text goes to `debug` only, because typed input may contain secrets. Rejections
log at `warning`. If you are the operator, this log is your record of what your
agents typed.

**This is verified working, not just asserted.** For a stretch of time the
audit line above was silently discarded: `uvicorn.run(..., log_level="info")`
only configures uvicorn's *own* loggers (`uvicorn`, `uvicorn.error`,
`uvicorn.access`) — it never touches the root logger or any `muxplex.*`
logger, so every accepted call's `_log.info(...)` had nowhere to go. The
tell was asymmetry: a *rejected* call's `_log.warning` reached the terminal
(Python's handler-of-last-resort surfaces WARNING and above), while every
*accepted* call's line vanished. `cli.configure_logging()` now sets the
`muxplex` package logger to INFO with its own handler before `uvicorn.run()`
starts, so every module under `muxplex.*` (via normal logger propagation)
reaches a real handler. Traced directly: after this fix, a run of 12 accepted
`/input` calls produced exactly 12 matching audit lines in the server log —
one per call, in the documented format, e.g.:

```
2026-07-26 22:59:37,161 INFO muxplex.main: input: session='agent-build' chars=11 enter=True keys=[] preview="printf '\a'"
```

If you operate muxplex yourself: this log only appears when the server was
started via `muxplex serve` (which calls `configure_logging()`). A server
started some other way (e.g. importing `muxplex.main` directly without going
through the CLI) won't have a handler attached and the audit line will be
silently lost — the same failure mode this fix closed.

---

## 6. Running sessions unattended: completion, attention, and depth

Everything above lets you type into a session and read back what happened
~400ms later. That's enough for a quick command. It is **not** enough to run
something long — a build, a test suite, a deploy — and know when it's done.
This section covers three capabilities together because they're meant to be
composed: a **completion sentinel** (know when a command finished, and how),
a **bell convention** (surface only what needs a human), and **`lines`**
(recover output the default read-back window drops). All three are proven
below against a live instance, with real traces, not asserted.

### 6.1 Why `last_activity_at` alone can't tell you "done"

`last_activity_at` (§3, `GET /api/sessions`) advances on tmux pane output and
freezes when a pane goes quiet. That sounds like a completion signal, but it
isn't one, and the reason is structural, not a bug: **a silent pane and a
finished pane look identical.** A command that's still running but has
produced no new output in the last 10 seconds (waiting on a lock, a slow
network call, a `read` with no prompt echoed) freezes `last_activity_at`
exactly the same way a command that finished 10 seconds ago and is sitting
idle at a shell prompt does. There is no exit code, no "idle-at-a-prompt"
flag, no `pane_dead` — just a timestamp that stopped moving, for reasons
`last_activity_at` structurally cannot distinguish between. Don't build a
completion check on it. Use §6.2 instead.

### 6.2 The completion sentinel (recommended, primary pattern)

The pattern: append a marker to whatever you're running, so the shell prints
it — with the real exit code — only once the command has actually finished.
Poll for the marker, not for silence.

```
<your command>; echo "MUXPLEX_DONE_<unique-token>_EXIT_$?"
```

Send that whole line as `text` with `enter: true`, then poll
`GET /api/sessions/{name}?lines=N` (§6.3) until a regex like
`MUXPLEX_DONE_<token>_EXIT_(\d+)` matches, and read the captured digit as the
real exit code.

**The one sharp edge, and why this exact shape is robust against it:** tmux
echoes back what you typed *before* the shell runs it — so the pane briefly
contains the literal text `MUXPLEX_DONE_<token>_EXIT_$?` (the `$?` typed
character-for-character, not yet expanded). If your poll only checked for the
token substring, that echoed, unexecuted input line would look like a false
"done" the instant you sent it. The fix is built into the shape above, not
bolted on: search for the token **followed by a digit**. Shell variable
expansion doesn't happen in the terminal's input echo, only when the command
actually runs — so `..._EXIT_$?` (literal, unexpanded) never matches a
`\d+`-anchored pattern, and `..._EXIT_0` / `..._EXIT_1` (the real, substituted
exit code) only ever appears once the command has genuinely completed.

**Proven, with real traces.** Two cases, run against a live instance:

*A long-running command that succeeds* (`sleep 8 && echo mid-output-line`):

```
sent at T+0.00, /input responded at T+0.43s (the ~400ms settle, §5.6)
immediate read-back: digit-suffixed marker present? False   <- confirms no false positive
  (tail of immediate snapshot showed the literal, unexpanded "...EXIT_$?")
polled every 0.5s; MATCHED after 16 polls at T+8.15s, exit_code=0
```

*A command that fails* (`sleep 3; false`):

```
sent, /input responded 0.43s later (same settle)
immediate read-back: digit-suffixed marker present? False   <- again, no false positive
polled every 0.5s; MATCHED after 6 polls at T+3.03s, exit_code=1
```

Both traces confirm the pattern end-to-end: the immediate `/input` read-back
never falsely matches (it only ever shows the unexpanded echo), and the poll
loop correctly recovers **both** a zero and a nonzero real exit code, at
timing that tracks the actual command duration (~8.15s for an 8s sleep,
~3.03s for a 3s sleep) rather than some fixed guess.

```bash
TOKEN="build-$$-$RANDOM"
CMD="make test; echo \"MUXPLEX_DONE_${TOKEN}_EXIT_\$?\""

# jq builds the JSON body so the shell doesn't have to hand-escape quotes.
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d "$(jq -n --arg t "$CMD" '{text: $t, enter: true}')" \
     "$MUXPLEX_URL/api/sessions/agent-build/input" > /dev/null

for _ in $(seq 1 120); do
  sleep 2
  SNAP=$(curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
              "$MUXPLEX_URL/api/sessions/agent-build?lines=500" | jq -r '.snapshot')
  if [[ "$SNAP" =~ MUXPLEX_DONE_${TOKEN}_EXIT_([0-9]+) ]]; then
    echo "done, exit code ${BASH_REMATCH[1]}"
    break
  fi
done
```

### 6.3 Recovering deep output: `lines` and `GET /api/sessions/{name}`

The default read-back window (`/input`'s settle-and-capture, and the shared
`GET /api/sessions` cache) is **30 lines** — fine for a short command, not
enough for `pytest -v`, `make`, or any real build log. Two ways to ask for
more:

* **`POST /api/sessions/{name}/input`** accepts an optional `lines` field
  that overrides the read-back depth **for that one call**:

  ```json
  { "text": "make test", "enter": true, "lines": 500 }
  ```

* **`GET /api/sessions/{name}?lines=N`** — a new, single-session, always-live
  capture, independent of typing anything. This is what you poll in the
  completion-sentinel loop above: `/input`'s read-back settles for only
  ~400ms, long before a real build finishes, so the *polling* has to happen
  against a separate live read, not the one-shot `/input` response.
  Response shape: `{"name", "snapshot", "lines", "bell", "last_activity_at"}`
  — same `bell`/`last_activity_at` fields as `GET /api/sessions`, plus
  `lines` echoing back the depth actually used.

  Deliberately **not** added to the existing bulk `GET /api/sessions` — that
  endpoint serves one shared ~2s-cycle cache consumed simultaneously by the
  PWA, muxplex-deck, and every agent; a per-request depth there would mean
  either forking that shared contract or forking a live tmux call per
  session in the list on every poll (the exact "unbounded value against 38
  sessions" DoS bounds exist to prevent — see below). A separate
  single-target endpoint sidesteps both.

**Bounds are real, and enforced identically on both entry points** — traced
directly against a live instance:

| Request | Result |
|---|---|
| `lines` omitted | 30 (`DEFAULT_CAPTURE_LINES`, unchanged from before this existed) |
| `lines=0` | **400** — `"lines must be between 1 and 2000 (got 0)"` |
| `lines=2000` | 200 — the exact ceiling is accepted |
| `lines=2001` | **400** — `"lines must be between 1 and 2000 (got 2001)"` |

Out-of-range is always a 400, **never a silent clamp** — an agent that
thinks it got 2000 lines but actually got fewer would be a worse surprise
than an explicit rejection. Traced proof of the recovery itself: a
`seq 1 100` command, then the default (omitted `lines`) read-back started at
line **48** — lines 1–47 genuinely gone — while `?lines=200` recovered the
full range, containing lines `1`, `47`, and `100`. Sessions also get their
tmux `history-limit` raised to 5000 on creation specifically so a max-depth
request has real scrollback behind it, rather than tmux's own (possibly much
lower) default silently truncating what you asked for.

### 6.4 Bell-on-completion: an attention convention

`unseen_count` / `needs_attention` (§3's `GET /api/view`) drive the actual
human-facing attention signal — the amber ring on a Stream Deck's VIEW key,
the bell-sorted tier in `?sort=attention`. **Nothing an agent naturally runs
trips it.** Traced directly: `echo`, `sleep`, and `false` all left
`unseen_count` at `0` throughout. A bell only fires from an **actual BEL
byte** (`\a`, 0x07) reaching the pane — which means if you want a background
job to surface on the human's radar, you have to make it ring the bell
yourself:

```bash
your-long-command
[ $? -ne 0 ] && printf '\a'
```

**Recommended convention: ring on nonzero exit only, not on every
completion.** Reasoning: the bell is a scarce, human-facing attention
channel — its entire purpose is telling the operator "look at this one."
An agent running many background jobs that all ring on *every* completion
turns that channel into noise indistinguishable from a real problem: ten
successful builds finishing ring the bell exactly as insistently as one
failure that actually needs a decision. Routine success doesn't need a
human — that's the point of running it unattended — and it's already
discoverable via the completion-sentinel your own poll loop is watching
for (§6.2). Reserve the bell for outcomes that genuinely warrant a look.

Composed with the sentinel pattern from §6.2, one command line does both:

```
<your command>; rc=$?; [ $rc -ne 0 ] && printf '\a'; echo "MUXPLEX_DONE_<token>_EXIT_$rc"
```

**Proven, both directions:**

* An explicit bell reliably registers: `printf '\a'` moved `unseen_count`
  from `0` to `1` and set `last_fired_at`, traced directly against a live
  session.
* Repeated bells accumulate once the detection path is active: three
  further `printf '\a'` calls in sequence advanced `unseen_count`
  `2 → 3 → 4 → 5`, one increment per event.

**Operational note, if bells don't seem to fire at all:** muxplex detects
bells two ways — a tmux `alert-bell` hook (fires on every bell,
unconditionally) and a `window_bell_flag` poll fallback (only active when no
tmux client is attached to the pane, and it only detects a fresh 0→1
transition, not every repeat). The hook is registered at server startup, but
that registration is best-effort against a tmux server that may not be
running yet (e.g. a brand new install, before any session has ever been
created) — and it is **not automatically retried**. If you suspect bells
aren't registering, call `POST /api/internal/setup-hooks` once (safe to call
anytime; it's idempotent) to (re-)register it — this was reproduced directly
during this investigation: the hook was silently unregistered in a fresh
instance, and one call to this endpoint fixed it for the rest of the
session.

---

## 7. What an agent may *not* do

`input_enabled` and `input_allowed_sessions` are **local-file-only**
(`settings.LOCAL_ONLY_KEYS`). They can be changed **only** by editing
`~/.config/muxplex/settings.json` on the host. Specifically:

* `PATCH /api/settings` **silently ignores them** (with a warning in the server
  log) while applying the rest of the patch. It does not error — so don't build
  a flow that PATCHes them and checks for a 200 as confirmation. It will lie to
  you.
* They are **not federation-syncable**. A peer device cannot widen your fence.

The reason is direct: the Bearer key that authenticates `PATCH /api/settings` is
the *same* credential handed to the agents that call `/input`. If these keys were
PATCHable, an agent could self-authorize its way into the human's own panes, and
the allowlist would be decorative.

**So: if your agent gets a 403 from `/input`, the correct behavior is to stop and
tell the human what to add to their `settings.json`.** There is no API path
around it, and attempting one is a signal that something has gone wrong.

---

## 8. Configuration postures — read this before assuming you're safe

There are two legitimate ways to run this, and they give you very different
guarantees. Know which one you're on.

### Scoped

```json
{ "input_enabled": true, "input_allowed_sessions": ["agent-*"] }
```

Agents may type into `agent-build`, `agent-deploy`, and friends. **The human's
own working panes stay un-typeable because they are not on the list** — a session
named `dev`, `notes`, or `prod-tunnel` gets a 403 regardless of what any caller
asks for. This is the posture where the allowlist is genuinely protecting the
operator's ambient authority, and it's the right default for most people.

### Wide open

```json
{ "input_enabled": true, "input_allowed_sessions": ["*"] }
```

Every session is typeable, including the human's own shells.

This is **a deliberate, legitimate choice**, not a misconfiguration — it is what
you pick when the entire point is agents managing everything on your behalf
across all your work. **It is what this project's own maintainer runs.**

But be honest about what it costs: **in this posture the allowlist is not
protecting the human's shell.** It has been switched off. What remains is:

1. **`LOCAL_ONLY_KEYS`** — an agent still can't change the configuration through
   the API, so the posture can't drift wider on its own.
2. **The audit log** — one line per accepted action, so what happened is
   recoverable after the fact.
3. **Network posture** — who can reach the endpoint at all. In the wide-open
   posture this is doing most of the work, which is a good argument for a
   localhost bind plus an SSH tunnel, or a private overlay network, rather than
   exposing the port.

If you are writing an agent and you don't know which posture you're on, assume
you may be in the second one and behave accordingly: read before you type, target
sessions you created, and don't send input to a pane you can't account for.

---

## 9. The full contract

This guide covers the endpoints an agent needs day to day. The complete,
authoritative, machine-readable contract is served by the running instance:

```bash
curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" \
     "$MUXPLEX_URL/openapi.json" | jq '.paths | keys'
```

`/docs` serves the interactive Swagger UI for the same schema. If this guide and
`/openapi.json` disagree, the schema is right and this file has a bug — please
report it.

Endpoints not covered here (federation aggregation, bells, settings sync, the
terminal WebSocket relay) are all in the schema. `API_SEMANTICS.md` documents the
invariants behind them; `AGENTS.md` carries the conventions for anyone changing
the server.

---

## 10. Checklist for agent authors

- [ ] Send `Authorization: Bearer <key>` **and** `Accept: application/json` on
      every request.
- [ ] Read `GET /api/instance-info` once at startup — cheap, unauthenticated,
      gives you the version to feature-gate against.
- [ ] Prefer `GET /api/view` over re-implementing view/sort/bell rules.
- [ ] **Poll on a short interval (e.g. 0.3s) after every create/delete**,
      not a flat multi-second sleep. A 404 right after a create is the cache,
      not a failure — see §4; typical resolution is under 1s.
- [ ] Treat any 403 from `/input` as "the operator must edit `settings.json`" —
      never try to route around it.
- [ ] **Check the read-back `snapshot`** after every input. Don't fire blind.
- [ ] Don't send `keys: ["Enter"]` and `enter: true` together unless you really
      want two Enters.
- [ ] Avoid `/connect` and `PATCH /api/state` unless you intend to move what the
      human is looking at — those fields are server-global.
- [ ] Assume the pane is a shell and that what you type will run. Read first,
      type second.
- [ ] **For anything long-running, use the completion-sentinel pattern**
      (§6.2) — don't infer completion from `last_activity_at` going quiet.
- [ ] **Request `lines=` (or poll `GET /api/sessions/{name}?lines=N`)** for
      any command whose output might exceed 30 lines (§6.3).
- [ ] **If a background job needs a human's attention, ring the bell
      yourself on nonzero exit** (§6.4) — nothing an agent naturally runs
      trips it, and routine success shouldn't spend that channel.

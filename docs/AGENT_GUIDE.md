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

### Trusting the server's certificate — do this before anything above

`$MUXPLEX_URL` in §0 is `https://`. Against a TLS-enabled instance your machine
doesn't already trust, **every request in this guide fails before authentication
is ever consulted**:

```
curl: (60) SSL certificate problem: unable to get local issuer certificate
```

No credential fixes that. It is a trust problem, not an auth problem, and it has
to be solved first. muxplex serves its own trust anchor over three
**unauthenticated** endpoints so a client can bootstrap:

| Endpoint | Returns |
|---|---|
| `GET /api/ca` | The local CA's **public** certificate as PEM (`application/x-pem-file`, `Content-Disposition: attachment; filename="muxplex-ca.crt"`). This is the one an agent or script uses. |
| `GET /ca.crt` | The **same bytes** at a plain top-level path, typed `application/x-x509-ca-cert` — the MIME type Android's DownloadManager recognizes, so tapping the link routes into the system certificate installer instead of dropping a generic file the user has to hunt down. Byte-identical to `/api/ca`; only the advertised type differs. |
| `GET /setup` | An unauthenticated HTML onboarding page — download link plus per-platform install steps (Android / iOS / macOS / Windows), with the visiting platform's section opened by default. Point a human at this; point a program at `/api/ca`. |

All three are in `auth._AUTH_EXEMPT_PATHS` by design, and the reason is not
convenience: **a client cannot authenticate over TLS it does not yet trust.**
Requiring a credential to fetch the trust anchor would be circular. A CA
*public* certificate contains no private key material — it is precisely the
thing clients are meant to install.

#### Bootstrap once, then verify every request

```bash
# bootstrap trust
curl -sk https://HOST:8088/api/ca -o muxplex-ca.crt
```

`-k` (skip verification) is acceptable for **this one fetch of a public trust
anchor and nowhere else** — there is nothing sensitive to expose, and you have
nothing to verify against yet. If you want the stronger guarantee, confirm the
fingerprint out-of-band before trusting the file:

```bash
openssl x509 -in muxplex-ca.crt -noout -fingerprint -sha256
```

From then on, pass it as the verification bundle — never keep using `-k`:

```bash
# same machine as the server — localhost bypass, no key needed
curl -s --cacert muxplex-ca.crt -H "Accept: application/json" \
  https://127.0.0.1:8088/api/sessions

# another machine — Bearer key required
curl -s --cacert muxplex-ca.crt \
  -H "Accept: application/json" \
  -H "Authorization: Bearer $MUXPLEX_KEY" \
  https://HOST:8088/api/sessions
```

The first works with no credential at all because of branch 1 above (socket-level
localhost); it still needs `--cacert`, because TLS verification happens before
the auth middleware ever runs. The second is the ordinary remote case: trust
anchor *and* Bearer key, both required.

In Python, the `muxplex-client` package takes the same file
(`client/muxplex_client/sync_client.py:47-73`):

```python
MuxplexClient(server_url, federation_key, ca_file="muxplex-ca.crt")
```

`ca_file` becomes httpx's `verify` value. Omit it and you get the system trust
store, which will not contain a local CA.

#### `/api/ca` only exists under `setup-tls --method ca`

muxplex has four TLS methods and only one of them mints a local CA. Under
`selfsigned`, `mkcert`, or `tailscale` there is no such file, and `/api/ca`
returns **404** with a body that says so:

```
No local CA certificate is available. This server may not be using
'muxplex setup-tls --method ca' (e.g. it's on Tailscale, mkcert, or
self-signed instead), or the file at the expected CA path is missing or not
a valid CA certificate.
```

Treat that 404 as information, not an error to route around. What to do next
depends on the method the operator actually chose, and it is not something a
client can discover from the API:

* **tailscale** — the cert is a real Let's Encrypt cert. Your system trust store
  already works. Drop `--cacert` entirely and use the MagicDNS name the cert was
  issued for (an IP or a bare LAN name won't match its SAN).
* **mkcert** — trust comes from mkcert's own root, which lives on the *server*
  at `$(mkcert -CAROOT)/rootCA.pem`. There is no HTTP endpoint for it; it has to
  be installed on the client by whoever administers both machines.
* **selfsigned** — there is no CA at all. The only honest options are pinning the
  leaf itself as the verification bundle (out-of-band copy, not over HTTP) or
  asking the operator to switch to `--method ca`.

**Do not silently fall back to disabling verification.** An agent that answers a
404 by turning verification off has quietly converted a configuration question
into a permanently unverified connection. Report it to the human instead.

#### Fetch the CA, never the leaf

The file you want is `muxplex-ca.crt` — the CA. The file the server presents on
the wire is `muxplex.crt` — the **leaf**. They sit next to each other in
`~/.config/muxplex/`, they are both PEM, and grabbing the wrong one produces
exactly the failure you were trying to fix:

```
curl: (60) SSL certificate problem: unable to get local issuer certificate
```

This is not hypothetical. `main.py:1976-1980` records that this endpoint exists
in part *because* users reliably grabbed the leaf instead of the CA when copying
files off the server by hand. `GET /api/ca` removes the ambiguity: it reads one
fixed path and can only ever return the CA. Use the endpoint rather than picking
a file, and if you ever do copy manually, check first:

```bash
openssl x509 -in muxplex-ca.crt -noout -subject -issuer
# a CA is self-issued: subject == issuer
```

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
    "last_activity_at": 1753500123.0,
    "created_at": 1753499900.0,
    "followups": {"pending": 2, "halted": false},
    "views": ["work", "agents"],
    "cwd": "/home/you/dev/muxplex"
  }
]
```

Those eight keys are the whole entry — verified by diffing this example's key
set against a live instance's response (`['bell', 'created_at', 'cwd',
'followups', 'last_activity_at', 'name', 'snapshot', 'views']`).

`cwd` is where that session's active pane currently is — how you tell which
repo a sibling agent is working in. It is an **observation, not a stable
identity**: it moves when someone `cd`s, and for a multi-window session it
tracks whichever window is current. A session whose active pane is running a
TUI (this includes amplifier's own TUI) reports the directory the TUI process
was launched from, not necessarily wherever its own internal navigation
currently is; a session created by `amplifier-workspace` reports the
workspace directory across all four of its windows. `null` when tmux reported
nothing parseable. See `docs/API_SEMANTICS.md` for the full rationale,
including why `GET /api/view` deliberately does not carry this field.

`GET /api/sessions/{name}` (§6.3) carries the identical four extra keys
(`created_at`, `followups`, `views`, `cwd`) — a caller that has narrowed to
one session sees exactly what the bulk read shows for it, including a halted
follow-up queue.

`views` is **server-resolved membership** — hand-pinned sessions ∪ `match_names`
glob-rule matches, resolved fresh on every read. This is what lets a rule-based
view reach a polling client without the client re-deriving membership from raw
`settings.views`. A session in no view gets `[]`, never `null`.

`followups` is the follow-up-queue badge: how many items are waiting on this
session's next bell, and whether that queue has **halted**. See
[§6.5](#65-follow-up-queues--leaving-a-note-for-the-next-bell) — `halted: true`
is a stalled queue and nothing clears it for you.

`created_at` is tmux's own session-creation timestamp; `null` when tmux
reported nothing parseable. Compare it against `GET /api/instance-info`'s
`server_started_at` (`created_at >= server_started_at`) to tell "genuinely
new to this server process" from "merely first observed" — the same rule
the server itself uses to decide whether a session's bell should be seeded
as attention-worthy. See `docs/API_SEMANTICS.md` for the full rationale.

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

**Creating a self-maintaining view.** A view can populate itself from a rule
instead of a hand-curated session list: `PATCH /api/settings` a `views` entry
with `match_names: ["<glob>", ...]` (fnmatch-style patterns against the bare
session name -- never a device-qualified key). `{"name": "Agents", "sessions":
[], "match_names": ["agent-*"]}` then shows every session named `agent-*` on
any device, updating itself as sessions come and go, with no further writes.
`GET /api/views` returns the resolved patterns plus any validation errors (a
pattern containing `:` is rejected, since tmux forbids `:` in session names).

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
out. Read-only for most purposes; see [§7](#7-what-an-agent-may-not-do) before
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

#### Optional: pick a named command pair with `command_id`

An operator can configure additional named create/delete command pairs beyond
the single default template above (e.g. one pair that opens a full
`amplifier-workspace` layout, another that creates a scratch session in
`/tmp`). Discover what's configured with:

```bash
curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/session-commands"
```

```json
{
  "commands": [
    {"id": "default", "label": "Default", "new_session_template": "tmux new-session -d -s {name}", "delete_session_template": "tmux kill-session -t {name}"},
    {"id": "amplifier", "label": "Amplifier workspace", "new_session_template": "amplifier-workspace ~/dev/{name}", "delete_session_template": "amplifier-dev --destroy {name}"}
  ],
  "default_id": "default",
  "errors": []
}
```

`commands[0]` is always the reserved `"default"` entry (the same template
create/delete already use above). This endpoint is the canonical, server-side
resolution — don't re-derive the pair list from `GET /api/settings`'s raw
`session_commands` field.

Pass the id you want on create:

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d '{"name":"agent-build","command_id":"amplifier"}' \
     "$MUXPLEX_URL/api/sessions"
```

→ `{"name": "agent-build", "ok": true, "command_id": "amplifier"}`

**`command_id` is entirely optional, and omitting it is byte-identical to
today** — every pre-existing client (and every example elsewhere in this
guide) that sends no `command_id` gets the `"default"` pair, exactly as
before this existed. An unresolvable id (typo, or an entry removed from
config since you last called `GET /api/session-commands`) is a **400** with
`{"unknown_command_id": true, "available": [...]}` in the body, and nothing is
spawned.

You cannot define or edit a pair through the API — `session_commands` is
local-file-only, same fence as `input_enabled` (§7). "Managing" pairs means
the operator edits `~/.config/muxplex/settings.json`; this endpoint only
lists and selects.

### Connect (point the web terminal at a session)

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions/agent-build/connect"
```

→ `{"active_session": "agent-build", "ttyd_port": 7682}`

`ttyd_port` is a legacy wire field kept only for client compatibility (no
ttyd binds a TCP port any more — see `../AGENTS.md`). This call can now
return `500` if the session's terminal process fails to start, or `503` if
the server is at its terminal-count ceiling — both new failure modes, since
this endpoint now verifies the terminal actually came up before returning.

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

**Delete uses whichever command pair the session was created with,
automatically** — there is no `command_id` parameter on this endpoint, and
that's deliberate: the pair a session was created with is looked up for you
(so `command_id: "amplifier"` on create means the matching
`amplifier-dev --destroy` teardown runs on delete, with nothing further to
remember or pass). A session with no recorded pair (pre-existing, or created
outside muxplex) falls back to the `"default"` pair, unchanged from
pre-feature behavior. The response gains one additive field:
`{"ok": true, "name": "agent-build", "command_id": "amplifier"}`.

If the recorded pair has since been removed or renamed in
`~/.config/muxplex/settings.json`, delete **refuses rather than silently
substituting**: **409** with `{"unknown_command_id": true, "command_id":
"amplifier", "available": [...]}`, and nothing is run. Recover by restoring
the pair in settings, or retry the same request with `?force=true` to
explicitly fall back to the default kill command:

```bash
curl -sS -X DELETE -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions/agent-build?force=true"
```

→ `{"ok": true, "name": "agent-build", "command_id": "default", "forced": true}`

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

**`POST /connect` no longer arbitrates a shared terminal, and `?takeover=` is
inert.** There used to be exactly one terminal process for the whole server, so
one device claiming it for a different session had to refuse another's claim.
There is now **one terminal process per session** (`../AGENTS.md`, "ttyd is
loopback-only by design — now per-session"), so there is nothing left to
contend for and nothing to seize:

* The `takeover` query parameter is **accepted and ignored** — kept in the
  signature only so pre-existing clients don't get a 422. Don't send it in new
  code, and don't build recovery on it.
* The failure modes that *are* real here are the ones
  [§4, "Connect"](#connect-point-the-web-terminal-at-a-session) already
  describes: **500** if the session's terminal process fails to start, **503**
  at the server's terminal-count ceiling.

If you are reading an older client (or an older copy of this guide) that
handles a `409` here with a conflict discriminator in the body, leave it —
it simply never fires against a current server, which is the version tolerance
this project asks of clients in both directions.

### Foreground focus: `POST /api/focus`

A separate, much smaller capability: bring THIS host's muxplex PWA window to
the OS foreground. No body, no parameters -- it's a bare `POST` with nothing
to configure per-request. The app it raises is whatever the operator
configured server-side (`settings.json`'s `focus_app`); an agent cannot
name a target.

**On a default install (no `focus_app` configured), this is a `409`** with
`{"focus_not_configured": true, "detail": "..."}` -- not a silent no-op and
not something to retry differently. Treat it the same way you'd treat
`input_enabled=false` on `/input`: a local-operator opt-in that hasn't
happened yet, not a bug in your request.

Also expect `501` (`focus_unsupported_platform`) on any non-macOS host --
this is a real platform limit, not a transient failure worth retrying. See
`docs/API_SEMANTICS.md`'s `POST /api/focus` section for the full response
table and the `GET /api/instance-info` `focus` capability block, which lets
you check `supported`/`configured` before ever calling this endpoint.

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
  [§4, "The read model is eventually consistent"](#the-read-model-is-eventually-consistent--poll-on-a-short-interval-not-a-long-sleep)).
  Poll on a short interval (typical resolution is under 1s) before concluding
  the session doesn't exist — don't sleep a flat multi-second delay.
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
  Response shape: `{"name", "snapshot", "lines", "bell", "last_activity_at",
  "created_at", "followups", "views", "cwd"}` — full field parity with a
  `GET /api/sessions` entry, plus `lines` echoing back the depth actually
  used. Narrowing to one session never means losing sight of that session's
  `followups.halted` badge or its `cwd`.

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
full range, containing lines `1`, `47`, and `100`.

How much scrollback actually exists behind a max-depth request is set by the
host's tmux configuration, not by muxplex. `muxplex tmux install` provides
50000; without it you get tmux's compiled-in default of 2000 — the same number
as the `lines` ceiling, so a `?lines=2000` request against an unmanaged host can
legitimately return everything there is. `history-limit` binds a pane when it is
created and cannot be raised afterward, so if you need deeper scrollback the fix
is `muxplex tmux install`, or raising `history-limit` in your own `~/.tmux.conf`
before sessions start — never a request parameter.

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

### 6.5 Follow-up queues — leaving a note for the next bell

§6.4 taught you how to *ring* the bell. This is what can *fire on* one.

A follow-up queue is a **per-session, server-side, persisted list of text
items**. Exactly one item fires per bell — typed into that session through the
same fenced path `/input` uses — until the queue drains. It survives a muxplex
restart (it lives in `state.json`, not in a client). It is the durable
agent-to-agent note primitive: *"when this session next asks for attention, run
this."* It is **local-only** — see the federation note at the end.

Every trace below is a real response from a live instance, not an illustration.

#### The badge is the part a read-only agent needs

`GET /api/sessions` and `GET /api/view` already carry a per-session summary, so
a polling client sees a queue's state without a second request per session:

```json
"followups": {"pending": 2, "halted": false}
```

**`halted: true` is a stalled queue that nothing will clear implicitly.** A
fire-time send failed, the item was *retained rather than skipped*, and the
queue will ignore every subsequent bell until something explicitly resumes it.
No timeout clears it, no later bell clears it, no edit to the list clears it.
If you queue follow-ups, poll this field.

#### The five endpoints

All five take the plain session name, all five 404 on a session this server
can't see (including the poll-cache window right after a create — [§4](#the-read-model-is-eventually-consistent--poll-on-a-short-interval-not-a-long-sleep)).

**`GET`** — read the queue.

```bash
curl -sS -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions/agent-build/followups"
```

```json
{
  "session": "agent-build",
  "revision": 0,
  "items": [],
  "halted": null,
  "target_window": "0:bash"
}
```

An **absent** queue and an **empty** queue are indistinguishable, on purpose:
both are `revision: 0, items: [], halted: null`. Don't build logic that tries
to tell them apart.

**`POST`** — append one item. No precondition (appending is commutative and
cannot clobber a concurrent writer).

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d '{"text":"make test","enter":true}' \
     "$MUXPLEX_URL/api/sessions/agent-build/followups"
```

```json
{
  "session": "agent-build",
  "revision": 1,
  "item": {
    "id": "d63c9d9016a545f3b2b660240d6f0b53",
    "text": "make test",
    "enter": true,
    "created_at": 1786163849.5979865
  }
}
```

`text`/`enter` mean exactly what they mean on `/input` ([§5.5](#55-the-request-contract)),
and the same 8192-byte UTF-8 ceiling applies (413 over it).

**`PUT`** — replace the whole list: edit, reorder, and remove in one call.
`expected_revision` is **REQUIRED** (see below). An item echoed back with a
known `id` keeps that id and its original `created_at`; an item with no id is
treated as new — which is what makes reorder-and-edit expressible without the
client inventing ids.

```bash
curl -sS -X PUT -H "Authorization: Bearer $MUXPLEX_KEY" \
     -H "Content-Type: application/json" -H "Accept: application/json" \
     -d '{"expected_revision":2,"items":[{"text":"git status","enter":true}]}' \
     "$MUXPLEX_URL/api/sessions/agent-build/followups"
```

```json
{
  "session": "agent-build",
  "revision": 3,
  "items": [
    {"id": "2823140a045849338261e3cffb16fdf9", "text": "git status",
     "enter": true, "created_at": 1786163849.7696276}
  ],
  "halted": null
}
```

**`DELETE`** — clear items **and** any halt.

```bash
curl -sS -X DELETE -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions/agent-build/followups"
```

```json
{"session": "agent-build", "revision": 0, "items": [], "halted": null}
```

**`POST .../followups/resume`** — clear the halt **only**, keeping every item
and the current revision. This is the deliberate counterpart to `DELETE`:
resuming is how you say "I looked at why it stalled, go again," without
throwing the queued work away.

```bash
curl -sS -X POST -H "Authorization: Bearer $MUXPLEX_KEY" -H "Accept: application/json" \
     "$MUXPLEX_URL/api/sessions/agent-build/followups/resume"
```

```json
{
  "session": "agent-build",
  "revision": 3,
  "items": [
    {"id": "459c814e51df46ddbeb204f09d543fe6", "text": "echo FIRED_TWO",
     "enter": true, "created_at": 1786163918.7569284}
  ],
  "halted": null
}
```

#### `expected_revision`: why `PUT` refuses to guess

The precondition exists because **the queue mutates itself.** A bell can drain
an item between your `GET` and your `PUT`. Replaying a list built from a
pre-bell snapshot doesn't lose an update — it **re-adds an item that has
already been typed into the session**, i.e. runs a command a second time. That
is why this is required here and merely optional on `PATCH /api/settings`.

The loop is: `GET` → read `revision` → build the new list → `PUT` with that
revision. A stale revision is refused, and the refusal hands you what you need
to rebuild:

```
HTTP 409
{
  "detail": {
    "revision_mismatch": true,
    "revision": 2,
    "items": [ …the server's current list… ]
  }
}
```

**On a 409, re-`GET`, rebuild, and re-`PUT`. Never retry the same body.**

#### Failure table

| Status | Discriminator | What you do |
|---|---|---|
| 403 | (detail string) | The `/input` fence excludes this session, exactly as on `/input` — [§7](#7-what-an-agent-may-not-do). Stop and tell the human; there is no API route around it. |
| 404 | — | The server can't see this session. Right after a create, that is the poll cache ([§4](#the-read-model-is-eventually-consistent--poll-on-a-short-interval-not-a-long-sleep)), not a failure. |
| 409 | `revision_mismatch` | Someone else wrote. Re-`GET`, rebuild, re-`PUT`. |
| 409 | `queue_full` | At the 16-item ceiling: `{"queue_full": true, "max": 16}`. Drain it, or `PUT` a shorter list. |
| 409 | `send_in_flight` | A send for this session is mid-flight (`PUT` only). Re-`GET` and retry. |
| 409 | `bell_hook_unarmed` | The tmux bell hook isn't registered, so nothing would ever fire the queue — new items are refused rather than accepted into a queue that can't drain. Call `POST /api/internal/setup-hooks` ([§6.4](#64-bell-on-completion-an-attention-convention)) and retry. |
| 413 | — | `text` over 8192 bytes UTF-8. |
| — | `halted` in the body | Not an HTTP status: a *fire-time* send failed. The item was retained. Only `POST .../followups/resume` (or `DELETE`) clears it. |

On the `bell_hook_unarmed` row, one honest caveat from tracing it against a
fresh instance: it is **hard to actually observe**, and you should not design
around hitting it. A brand-new server with no tmux running does report
`bell_hook_armed: false` from `GET /api/instance-info` — but with no tmux there
are no sessions either, so an append gets the **404** gate first. Once tmux
comes up, the hook re-arms on its own within one poll cycle (traced: `false` →
`true` in ~3s, with no manual call). `POST /api/internal/setup-hooks` returns
`{"ok": true}` and leaves the hook armed, so it remains the right recovery to
attempt — just expect the self-heal to usually beat you to it.

#### Two properties that will otherwise surprise you

**The fence is re-evaluated at fire time, against fresh settings.** A
successful append is *not* a promise the item will ever be allowed to send.
Appending runs an allowlist check as a courtesy so you learn now rather than at
the next bell — but the check that decides is the one that runs when the bell
arrives. Traced directly: an item was queued while the session was allowed, the
operator then narrowed `input_allowed_sessions` on disk, and the next bell
halted the queue instead of typing:

```json
"halted": {
  "reason": "input_not_allowed",
  "detail": "Session 'agent-build' does not match any input_allowed_sessions pattern",
  "at": 1786163925.901484,
  "item_id": "459c814e51df46ddbeb204f09d543fe6"
}
```

The item is still in `items`. Further bells were ignored while halted; `resume`
brought it back with the item intact.

**`target_window` is display-only.** `GET` reports the session's *current*
window as `"<index>:<name>"` so a UI can show where an item would land, but the
send targets the session — whatever window is current when the bell actually
fires, which is not necessarily the window that belled.

#### Federation: these are local endpoints

There is no `/api/federation/{device_id}/sessions/{name}/followups`, and you
must not assume one. Bells are local state; a remote session's bell is that
host's own concern. To queue a follow-up on a peer, talk to that peer directly.

For *why* the queue behaves this way — the precondition's rationale, the
advance sequence, and the halt-rather-than-skip rule — see the "Follow-up
queues" section of [`API_SEMANTICS.md`](API_SEMANTICS.md).

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

Both keys are read fresh from disk on every request, so the human's edit takes
effect on your next call — **don't tell them to restart the server**; retry
instead. If the retry still 403s, the likeliest cause is a JSON syntax error in
the file, which silently discards *every* setting and reverts to defaults with
nothing in the log (`python3 -m json.tool ~/.config/muxplex/settings.json`
confirms it parses). See the README's "Editing local-file-only keys".

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

The endpoints this guide does not walk through are in the schema, and each is
one line here so you know it exists and where to read further. These are
pointers, not contracts — `/openapi.json` is authoritative for shapes.

| Endpoint | What it is | Read further |
|---|---|---|
| `GET /api/views` | The resolved, validated **view definitions** — the plural sibling of `GET /api/view`, already referenced in [§3](#get-apiview--the-servers-resolved-answer). Returns `{"views": [{"name", "sessions", "match_names", "errors"}], "errors": [...]}`, user-defined views only (no `all`/`hidden`). `match_names` holds only the patterns that will actually be used; an invalid one (e.g. containing `:`) is absent and named in `errors` — so a client never decides rule validity itself. | `main.py:1653` |
| `POST /api/views/preview` | Dry-run a draft `match_names` list against live sessions before writing it. Never writes settings. | `main.py:1709` |
| `POST /api/sessions/{name}/bell` | Record a bell for a session — this is what tmux's `alert-bell` hook calls (§6.4), and what advances a follow-up queue (§6.5). | `main.py:2570` |
| `POST /api/sessions/{name}/bell/clear` | Mark a session's bell seen. | `main.py:2610` |
| `POST /api/heartbeat` | Register a `device_id` — the opt-in step [§4](#active_view--active_session-are-server-global-last-writer-wins) describes. An agent that doesn't want its own selection never needs this. | `main.py:2516` |
| `GET` / `PATCH /api/tmux-config` | Inspect and manage the server-managed tmux config. See `docs/TERMINAL_CONFIG_OWNERSHIP.md`. | `main.py:2786`, `:2801` |
| `GET` / `PUT /api/settings/sync` | Federation settings sync. Read `API_SEMANTICS.md`'s write-discipline section before touching the `PUT`. | `main.py:2875`, `:2903` |
| `GET /api/federation/sessions` and the `/api/federation/{device_id}/*` proxies | Aggregated multi-host reads, plus connect / create / delete / bell-clear against a peer. Note there is deliberately **no** follow-ups proxy (§6.5). | `main.py:4091`, `:4331`, `:4375`, `:4421`, `:4482` |
| `WS /terminal/ws?session={name}` | The terminal relay the browser uses. An agent reads panes with `GET /api/sessions` and types with `/input` — it does not need this. | `../AGENTS.md` |

`API_SEMANTICS.md` documents the invariants behind these; `AGENTS.md` carries
the conventions for anyone changing the server.

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
- [ ] **If you queue follow-ups, poll `followups.halted`** (§6.5) — a halt is
      a silent stall, and nothing clears it for you.

# Mobile text-compose bar — implementation specification

Status: **SHIPPED in v0.37.0 (2026-08-05).** Retained as an architectural
decision record. This document lived only at the root of a throwaway
cross-repo workspace and existed nowhere else in the repo it describes; a
prior sweep of this repo mistook it for "a rejected draft that never
shipped" and deleted its citations — it was neither rejected nor unshipped,
and the citations are restored below. The `/compose` endpoint this document
weighed in §2 as a possible design was the part that *was* rejected on
security review before anything was built (`CHANGELOG.md`'s v0.37.0 entry,
"A `/compose` endpoint accepting a session cookie was designed, and then
rejected on security review"). What shipped is this document's own
fallback conclusion: a plain UI client of the existing, unmodified
`POST /api/sessions/{name}/input`, no new endpoint, no fence change. See
`AGENTS.md`'s "Mobile compose bar" note.

**Provisional:** the fence-relaxation premise is under independent security review in
parallel. §2 is written to be *checkable* against that review, not to pre-empt it. If the
review lands differently, §2.4's decision table is the single place to change; everything
downstream keys off it.

---

## 0. Read this first — the premise is sound, but not for the stated reason

The owner's rationale is:

> a PAM-cookie-authenticated interactive browser session already has a fully writable
> terminal — `ttyd` runs `-W` with no credential — so a compose bar grants them zero new
> capability.

**The conclusion is correct. The reasoning as stated is incomplete, and the gap matters
for the design.**

`ttyd` running `-W` with no credential is not, by itself, the argument. Every ttyd is
bound to an `AF_UNIX` socket under `ttyd.ttyd_socket_dir()` (0700 dir, 0600 socket,
uid-checked); a browser cannot reach it. The thing that actually grants the browser a
writable terminal is muxplex's own **`WS /terminal/ws` proxy**, and its access rules are
`_ws_auth_check()` (`main.py:2579`) — not ttyd's flags.

So the correct form of the argument is:

> **A caller that `_ws_auth_check` admits can already type arbitrary bytes into any live
> session. For that caller, a compose bar is a different keyboard for a terminal they
> already control.**

This matters because `_ws_auth_check` admits **three** classes, not one:

| Class | `_ws_auth_check` verdict | Source |
|---|---|---|
| Socket-level localhost (`127.0.0.1`/`::1`) | admitted, **no credential required** | `main.py:2588` |
| Valid `muxplex_session` cookie | admitted | `main.py:2590-2593` |
| Federation **Bearer** key | **admitted** | `main.py:2594-2598` |
| `Authorization: Basic` | **refused** | (not checked) |

### 0.1 Finding: the `/input` fence already has an open sibling door

**Verified by code reading; NOT yet verified by a live probe. Treat as high-confidence,
not proven.**

`terminal_ws_proxy` (`main.py:2718`) with a Bearer token, `?session=<name>` and **no**
`device_id`:

1. `_ws_auth_check` → `bearer_ok` → `True` (`main.py:2598`)
2. `device_id is None` → `group = None` → the `4409` group-consistency check at
   `main.py:2818` is **skipped entirely**
3. `target = session`; passes `is_valid_session_name` + `in get_session_list()`
4. `_prepare_ttyd(target)` → `ensure_ttyd(target)` spawns/reuses that session's ttyd
5. bidirectional relay begins — the caller types raw bytes into the pane

A federation Bearer-key holder can therefore already achieve, over WebSocket, exactly the
capability that `POST /api/sessions/{name}/input` exists to fence — regardless of
`input_enabled` and `input_allowed_sessions`. `AGENTS.md`'s own framing ("This fence has a
sibling, and it is not optional reading") named the `new_session_template` door; this is a
**third** door and it is not documented.

**What this does and does not mean for this feature:**

- It does **not** justify widening the `/input` fence. "It leaks elsewhere" is not a
  reason to open a second leak; the correct response is to close the WS door.
- It **does** mean the compose bar must be designed so it is unaffected either way — it
  must not depend on the WS gap existing, and it must not break when the gap is closed.
- It **does** give the design its governing rule (§2.2), and it makes Bearer's exclusion
  principled rather than arbitrary: **compose admits the classes whose terminal-WS access
  is by design, and refuses the class whose terminal-WS access is a bug.**

**Recommended action, out of scope for this feature:** file a separate issue —
`_ws_auth_check` should not admit a Bearer token for `/terminal/ws` unless a
federation-relay code path genuinely requires it, and `terminal_ws_proxy` should require
`device_id` for non-relay callers. Prove the gap with a live raw-socket probe first (the
same discipline `API_SEMANTICS.md` used for the `4409`/`1006` incident) — do not act on a
code reading alone.

### 0.2 Answer to "can the distinction be made safely?"

**Yes.** The distinction is made in exactly one place (the auth middleware, which is the
only component that knows *how* a request authenticated), recorded as an opaque
server-set string in the ASGI scope, and consumed by a **new, separate endpoint** whose
first act is to demand a positive match against a closed allowlist. Absence, ambiguity,
or an unrecognized value is a 403. Nothing about the existing `/input` endpoint changes.

---

## 1. Source material read

| File | What was taken from it |
|---|---|
| `AGENTS.md` (598 lines) | additive-API rule; `/input` fence ordering; `LOCAL_ONLY_KEYS` rationale; ttyd `-W` design; shared-global-scope rule; `preventDefault` lesson; test-suite hazards |
| `docs/API_SEMANTICS.md` (621 lines) | discriminator-flag convention (`backstop`/`terminal_conflict`/`unknown_command_id`/`invalid_view_rule`); `4409`/`4404` semantics; per-session ttyd; `device_id` residual gap |
| `muxplex/main.py` | `send_session_input` (1674-1801); `connect` (1635-1671); `_ws_auth_check` (2579-2602); `terminal_ws_proxy` (2718-2828); `SessionInputPayload` (970-993); middleware install (920-927) |
| `muxplex/terminal_input.py` (169) | argv builders, `ALLOWED_KEYS`, `MAX_TEXT_BYTES`/`MAX_KEYS`, `redact_preview`, allowlist matcher |
| `muxplex/auth.py` (354) | `AuthMiddleware.dispatch` ordering (281-348); `_LOCALHOST_ADDRS`; `_AUTH_EXEMPT_PATHS`; cookie verify; PAM |
| `muxplex/settings.py` | `LOCAL_ONLY_KEYS` (221-232), `input_enabled`/`input_allowed_sessions` defaults (96-97) |
| `frontend/index.html` (409) | header markup (44-52, 61-69); expanded-view tree (60-115); search-bar precedent (95-107); autofill-in-markup comment |
| `frontend/app.js` (6553) | `api()` (421-444); `isMobile()` (416-418); localStorage patterns (448-491); `showToast` (3617-3623); `openSession`/`closeSession` (3750-3914); `AUTOFILL_SUPPRESSION_ATTRS` (5407-5432) |
| `frontend/terminal.js` (843) | `initVisualViewport` (367-387); `createTerminal` (397-425); mobile threshold duplication (408) |
| `frontend/style.css` (2888) | `.view`/`.view--active` (122-135); `.expanded-header` (437); `.view-body` (476); `.terminal-wrapper`/`.terminal-container` (716-731); `.header-btn` (1233); breakpoints (392-427) |
| `frontend/tests/test_shared_scope.mjs`, `test_css_class_definitions.mjs` | coverage boundaries and the template-literal blind spot |
| `muxplex/tests/test_input.py`, `test_api.py`, `test_auth.py` | fixture patterns: cookie injection, `_InjectClientMiddleware`, `TestClient(app)` host = `testclient` |

---

## 2. The fence relaxation

### 2.1 Shape: a new endpoint, not a branch inside the old one

**`POST /api/sessions/{name}/compose`** — new, additive.

`POST /api/sessions/{name}/input` is **byte-identical** after this change: same fences,
same order, same status codes, same docstring, same tests, same threat model. A Bearer
caller observes no difference whatsoever.

Why a separate endpoint rather than a conditional inside `send_session_input`:

1. **`AGENTS.md` mandates additive changes at the API boundary.** A new endpoint is
   purely additive; a semantic change to an existing one is not.
2. **A fence with an `unless` clause stops being auditable.** The numbered fence list in
   `send_session_input`'s docstring is a security-review artifact that external readers
   (and `docs/AGENT_GUIDE.md`'s security claims) depend on. Adding "…except for callers
   of class X" degrades exactly the property that makes it reviewable.
3. **Failing closed becomes structural, not conditional.** The new handler's first
   statement demands positive proof of caller class. There is no path through it to
   `run_tmux` that skips that demand — as opposed to a branch, where a future refactor can
   reorder around one.
4. **Distinct audit stream.** An operator reading logs must be able to tell
   agent-typing from human-typing. Separate endpoint ⇒ separate log prefix, for free.
5. **Zero back-compat burden.** It has exactly one client, which ships in the same
   commit. Per this repo's own lesson ("Zero consumers means zero backward-compat
   burden"), it can require things `/input` cannot — notably `device_id` (§2.6).

**Anti-duplication requirement.** There must be exactly **one** implementation of "type
this into tmux." Extract the post-fence body of `send_session_input` (caps → key
allowlist → non-empty check → `run_tmux` calls → audit log) into a module-private
`async def _perform_session_input(name, text, keys, enter, *, audit_prefix) -> None` in
`main.py`. Both handlers call it. Only the fence differs, and only the fence should
differ.

**Deliberately NOT shared:** the *fence* logic. Following the precedent `AGENTS.md`
establishes for `views.matches_name_pattern` vs `terminal_input.session_matches_allowlist`
("Two consumers with opposite failure requirements must not share a mutable
implementation"), the compose fence and the `/input` fence are separate code with separate
tests. A future tightening of one must not silently change the other.

### 2.2 The governing rule

> **The compose endpoint admits exactly the caller classes whose ability to open
> `WS /terminal/ws` for the target session is by design — and no others. It grants no
> capability those callers do not already hold.**

Applying that rule to the four classes:

| Class | Already has writable terminal? | By design? | Compose |
|---|---|---|---|
| **cookie** — valid `muxplex_session` | Yes (`main.py:2590`) | Yes — this is the interactive browser session the whole app is built for | **ADMIT** |
| **bearer** — federation key | Yes (`main.py:2598`) | **No — §0.1 finding; a gap to close** | **REFUSE** |
| **localhost** — socket-level bypass, no credential | Yes (`main.py:2588`) | Yes, but proves *network position*, not identity | **REFUSE** — see §2.3 |
| **basic** — `Authorization: Basic` | **No** — `_ws_auth_check` does not check Basic | n/a | **REFUSE** |
| *anything else / unknown / absent* | — | — | **REFUSE** |

`basic` falling out as a refusal without special-casing is a good sign the rule is doing
real work rather than being reverse-engineered from a desired answer.

### 2.3 Why `localhost` is refused, even though it can already type

This is the least obvious call in the spec, and it is load-bearing.

The localhost bypass (`auth.py:284-286`) authenticates by **network position**, with no
credential at all. Any process on the host — and, critically, **any web page loaded in a
browser on the host** — carries it automatically. That makes it ambient authority, which
is precisely the substrate CSRF and DNS-rebinding attacks run on.

An `Origin` check (§2.5) stops classic CSRF. It does **not** stop DNS rebinding: an
attacker's page at `evil.com` that rebinds to `127.0.0.1` sends `Host: evil.com` and
`Origin: http://evil.com` — internally consistent, so the Origin check passes.

**Requiring the cookie kills rebinding outright.** A rebound page is on origin
`http://evil.com`, so the browser sends `evil.com`'s cookies — *never* the
`muxplex_session` cookie, which is scoped to muxplex's own origin. An endpoint that
demands a valid `muxplex_session` cookie is structurally unreachable by a rebinding
attack. That is not a mitigation; it is an immunity, and it is the reason `localhost`
must not be admitted. Admitting it would make the whole design lean on the `Origin`
check alone, which is the weaker of the two defenses.

**Consequence for the desktop-on-loopback user, and why it is acceptable:**

A user browsing `http://localhost:8088` never hits `/login`, so has no cookie, so gets a
403 from compose. The remedy is **one visit to `/login`** — self-service, discoverable,
and surfaced by the error UI (§7.4). This is nothing like "SSH in and edit JSON": no
shell, no file editing, no restart. It is also a rare path — the owner's own default is
compose **off** on desktop.

For this remedy to work, §2.4's classification refinement is mandatory: the middleware
must classify a *cookie-bearing localhost request* as `cookie`, not `localhost`.

### 2.4 Mechanism: classify in the middleware, consume in the handler

`AuthMiddleware.dispatch` (`auth.py:281`) is the only component that knows how a request
authenticated, and it currently discards that. Change it to record the class.

**Contract:** immediately before each `return await call_next(request)`, set

```
request.scope["muxplex_auth_class"] = "<class>"
```

with `<class>` one of the literal strings `"cookie"`, `"bearer"`, `"basic"`,
`"localhost"`, `"exempt"`, `"static"`.

Implementation constraints, all of which are requirements:

- **Use `request.scope[...]`, not `request.state`.** FastAPI constructs a fresh `Request`
  object for the endpoint; the *scope dict* is the same object threaded through
  `call_next`, so a plain string key is the unambiguous carrier. (`request.state` also
  works via `scope["state"]`, but adds an object-identity question with no benefit.)
- **Server-set only.** The key must never be derived from, defaulted from, or
  influenced by any request header, query param, cookie or body field. A client cannot
  write into the ASGI scope; keep it that way.
- **Classification must not change authorization.** This change adds *bookkeeping* to
  existing branches. No branch's admit/deny outcome moves. A test must pin this: the full
  existing `test_auth.py` suite passes unmodified.
- **Refinement (required):** in the localhost branch, evaluate the session cookie
  *before* classifying. If `verify_session_cookie(...)` succeeds → `"cookie"`; else →
  `"localhost"`. Both still pass the middleware — only the label differs. This is what
  makes §2.3's "just log in once" remedy real. Document it inline with that reason.
- The `"exempt"` and `"static"` labels exist only for completeness/observability; neither
  path can reach `/api/sessions/{name}/compose` (not an exempt path, no static
  extension), and both are refusals in the compose allowlist anyway.

**Consumption**, in a new module `muxplex/compose_input.py` (kept tiny and pure so it is
auditable at a glance, mirroring `terminal_input.py`'s stated design intent):

```
COMPOSE_ALLOWED_AUTH_CLASSES: frozenset[str] = frozenset({"cookie"})
```

with a module docstring stating the §2.2 rule, the §2.3 rebinding rationale, the §0.1
finding, and an explicit "do not add `bearer` or `localhost` here without re-deriving the
rule" warning. A pure predicate `caller_may_compose(auth_class: str | None) -> bool`
returns `auth_class in COMPOSE_ALLOWED_AUTH_CLASSES` — `None` is `False` by construction,
which is the fail-closed property expressed as data rather than control flow.

### 2.5 CSRF

**First defense, already in place and verified: `SameSite=Strict`.**
`POST /login` issues the cookie with `httponly=True, samesite="strict"`
(`main.py:3223-3229`). Under `Strict`, a browser sends `muxplex_session` **only** on
requests originating from muxplex's own site — including top-level navigations. A page on
an attacker's site cannot cause the cookie to be attached at all, so classic CSRF against
the compose endpoint is structurally impossible before any check in this spec runs.

That is good news, and it is not sufficient on its own, for two reasons:

- **`SameSite` is site-scoped, not origin-scoped.** "Site" is eTLD+1. If muxplex is served
  at `mux.example.com` and anything hostile is served at `evil.example.com`, the two are
  the *same site* and `Strict` still sends the cookie. Only an origin check closes that.
- **It is one line in an unrelated handler.** Nothing today marks it as load-bearing. A
  future change — e.g. loosening to `Lax` to fix a `?next=` deep-link navigation — would
  silently remove compose's first defense with no test failing.

**Therefore:** `SameSite=Strict` must be **pinned by a test** (§9.2) with a comment naming
compose as a dependent, and the explicit checks below are added as independent second and
third defenses. Do not treat any one of the three as sufficient.

**Do not rely on content-type parsing as a CSRF defense.** The reasoning "FastAPI needs
`application/json`, and a simple cross-site form POST cannot set that, so a preflight is
forced" depends on FastAPI/Pydantic version-specific body-coercion behavior. That is
exactly the class of environment-dependent security fence `terminal_input.py`'s own
docstring rejects for `fnmatch`. Make the defense explicit.

**Required checks, in order, before anything else in the handler:**

1. **Caller class** — `caller_may_compose(request.scope.get("muxplex_auth_class"))`.
   False → **403** `{"detail": ..., "compose_auth_required": true}`.
2. **`Origin` present** — a missing `Origin` header on this endpoint is a **403**. Every
   browser that can run this app sends `Origin` on a cross-origin-capable `fetch` POST.
   Absent means "not a browser we designed for" → refuse.
3. **`Origin` host matches `Host`** — compare the **host component** (host + port) of
   `Origin` against the request's `Host` header, case-insensitively. Mismatch → **403**.
   *Compare host, not scheme:* muxplex terminates TLS itself (`tls_cert`/`tls_key`) and
   has no proxy-header middleware, but a future reverse-proxy deployment would make
   `request.url.scheme` disagree with `Origin`'s scheme and produce a false refusal. A
   scheme downgrade requires an active MITM, who already has the cookie. Document this
   trade-off inline.
4. **`Sec-Fetch-Site`, when present** — if the header is present and is not
   `same-origin`, **403**. Applied *only when present*: Safari gained
   `Sec-Fetch-*` support relatively late, and the primary target of this feature is an
   iPhone. Requiring it unconditionally would fail closed on the exact device the feature
   exists for. Defense-in-depth, not a load-bearing gate.

**Deliberately NOT chosen:** a double-submit CSRF token. It would require a token
endpoint, client storage, rotation, and a failure mode of its own — to defend a surface
that checks 1–4 already close. A CSRF token is the right answer when cookies are the
*only* signal; here the cookie requirement plus same-origin verification is both simpler
and, against rebinding, strictly stronger.

**SameSite:** `POST /login`'s `Set-Cookie` should carry `SameSite=Lax` if it does not
already — verify during implementation and add if missing. `Lax` alone would stop
cross-site `POST`s outright, making it a *fourth* independent defense. Do not treat it as
a substitute for 2–4 (a same-site subdomain, or a `Lax`-ignoring client, breaks it), and
do not change it to `Strict` — that would break the `?next=` post-login redirect flow.

### 2.6 The group-consistency gate — compose must not exceed the WS

`terminal_ws_proxy` refuses (`4409`) when a caller supplies `device_id` and the resolved
sync group's `active_session` is not the target — "this device is never shown, and can
never type into, a session it did not itself select" (`API_SEMANTICS.md`).

If compose omitted that check, it would grant a cookie holder **more** than the terminal
WS does, and the entire equivalence argument in §0 collapses. So:

- **`device_id` is REQUIRED** on `POST /api/sessions/{name}/compose` (query param, same
  spelling as `/connect`). Absent → **400**.
  `API_SEMANTICS.md` documents the residual gap that a WS client sending no `device_id`
  "gets none of this protection," kept open only for unknown pre-existing clients. Compose
  has no such clients, so the gap is closed at birth rather than inherited.
- Unknown `device_id` → **404**, matching `_resolve_group_or_404`'s existing semantics
  ("never a silent fallback to `global`").
- Resolved group's `active_session != name` → **409** with
  `{"compose_session_mismatch": true, "active_session": <the group's actual value>}`.
  `compose_session_mismatch` joins the established discriminator convention
  (`backstop` / `terminal_conflict` / `unknown_command_id` / `invalid_view_rule`).

### 2.7 What compose does NOT consult, and why there is no new settings key

Compose **does not read** `input_enabled` or `input_allowed_sessions`. Those two keys fence
the *agent* typing path; gating compose on them would reintroduce the exact problem this
feature exists to solve (a phone user cannot edit `settings.json`), and would couple a
human-interaction control to an agent-authorization control that has to move
independently.

**No new settings key is added.** An operator who wants compose disabled wants the
interactive terminal disabled — which is the entire application. A `compose_enabled` key
would be dead complexity with a fence question attached (it would have to join
`LOCAL_ONLY_KEYS`, be excluded from `SYNCABLE_KEYS`, be documented in two places, and be
tested), all to express a preference nobody can coherently hold. Adding no key is also
strictly safer than adding one: there is no key to PATCH, no key to federate, no key to
get wrong.

### 2.8 Full fence order for `POST /api/sessions/{name}/compose`

Every check must pass, in this order. Each is a hard stop.

| # | Check | Failure |
|---|---|---|
| 1 | `caller_may_compose(scope["muxplex_auth_class"])` | **403** `compose_auth_required: true` |
| 2 | `Origin` header present | **403** `compose_origin_required: true` |
| 3 | `Origin` host == `Host` | **403** `compose_origin_required: true` |
| 4 | `Sec-Fetch-Site` absent, or `== "same-origin"` | **403** `compose_origin_required: true` |
| 5 | `_require_valid_session_name(name)` | **400** |
| 6 | `device_id` present | **400** |
| 7 | `device_id` resolves to a group | **404** |
| 8 | group `active_session == name` | **409** `compose_session_mismatch: true` |
| 9 | `name in get_session_list()` (exact, fail-closed) | **404** |
| 10 | body caps + key allowlist + non-empty (shared helper) | **413** / **400** |

Checks 1–4 are ordered before the name check so the endpoint never leaks session
existence to an unauthorized caller — the same reasoning `send_session_input` uses for
putting the allowlist before existence.

Ordering note on 8 vs 9: the group check runs before the known-session check because a
group's `active_session` is caller-scoped state (already known to the caller), whereas
`get_session_list()` membership is not. Both are fail-closed.

**Audit logging.** Exactly one `logger.info` per accepted action, via the shared helper
with `audit_prefix="compose"`, plus `device_id` and resolved `sync_group`. Full text at
`debug` only. Rejections at `warning`, and a rejection at check 1 caused by an **absent**
`muxplex_auth_class` (rather than a wrong value) must log distinctly — that means the
middleware did not run, which is a deployment fault, not an attack, and must be loud.

---

## 3. API contract

### `POST /api/sessions/{name}/compose?device_id=<id>`

**Auth:** shared middleware (unchanged) **plus** the §2.8 fence. Cookie class only.

**Body** — new `ComposeInputPayload` model, deliberately narrower than
`SessionInputPayload`:

| Field | Type | Default | Notes |
|---|---|---|---|
| `text` | `str` | `""` | typed literally via `tmux send-keys -l -- <text>` |
| `enter` | `bool` | `false` | press `Enter` after `text` |

**Deliberately omitted from v1:**

- `keys` — the compose bar has no UI for named keys. Omitting it means a compromised
  compose path cannot send `C-c`/`C-d`. The shared helper still takes a `keys` argument;
  the compose handler passes `[]`. Add it later only when a real control exists.
- `lines` — there is no read-back (below), so there is nothing to size.

At least one of `text` / `enter` must be present, else **400** (same rule as `/input`).

**Response 200:** `{"ok": true, "session": <name>}`

**No read-back snapshot.** `send_session_input` sleeps 400 ms and re-captures the pane so
a blind agent can see the effect. The compose caller has a live xterm.js relay showing the
pane in real time — the snapshot is redundant, and 400 ms per send on a phone is a real
tax on a rapid compose→send→compose loop. Omitting it also removes a `capture_pane`
subprocess per keystroke-batch. This asymmetry with `/input` is deliberate and must be
stated in the handler docstring.

**Error bodies** carry a discriminator flag per §2.6/§2.8 so the client can branch without
string-matching `detail`.

**Documentation obligations (part of the change, not follow-up):**

- `AGENTS.md` — new subsection under "Terminal input", stating: the §0.1 finding; the §2.2
  rule; why the `/input` fence is untouched; why the fence code is deliberately duplicated
  rather than shared. Cross-reference from the existing "Terminal input" section so a
  reader auditing the fence cannot miss the second door.
- `docs/API_SEMANTICS.md` — new entry: the endpoint is **cookie-class only and is not for
  agents**; `device_id` is required (unlike every other endpoint that takes it);
  `compose_session_mismatch` joins the discriminator convention; there is no read-back.
- `docs/AGENT_GUIDE.md` — one line: agents must use `/input`; `/compose` will 403 for a
  Bearer caller, by design. (`AGENTS.md` requires the two be kept in sync when fences or
  status-code ordering change.)

---

## 4. Frontend — where the bar renders and how the terminal resizes

### 4.1 Current layout

```
#view-expanded            .view (height:100dvh) + .view--active (flex, column)
├── header.expanded-header                         44px, flex-shrink:0
├── .view-body                                     flex:1, min-height:0, row
│   ├── #session-sidebar
│   └── .terminal-wrapper                          flex:1, column
│       ├── #terminal-search-bar                   flex-shrink:0, .hidden by default
│       └── #terminal-container                    flex:1
└── #reconnect-overlay                             absolute, inset: 44px 0 0
```

### 4.2 Placement

The compose bar is a **static element in `index.html`**, inserted as the last child of
`.terminal-wrapper`, immediately after `#terminal-container`:

```
.terminal-wrapper
├── #terminal-search-bar     (existing)
├── #terminal-container      (existing, flex:1)
└── #compose-bar             (new, flex-shrink:0, .hidden by default)
```

Three reasons this is the right node:

1. `.terminal-wrapper` is a column flexbox with `#terminal-container` at `flex:1`. Adding
   a `flex-shrink:0` sibling makes the terminal shrink **automatically**. No JS height
   arithmetic for the non-keyboard case — the layout engine already does it. This is the
   whole reason to put it here rather than under `.view-body`.
2. It sits beside the sidebar, not under it. The bar types into *this terminal*; scoping
   it to the terminal's own wrapper matches that.
3. `#terminal-search-bar` is the exact precedent: static markup, `.hidden` toggle, same
   parent, already-working sizing behavior.

**Static markup, not a template literal, not `createElement`.** This is a test-coverage
decision, not a style one — see §8.

### 4.3 DOM

```
#compose-bar.compose-bar.hidden
├── textarea#compose-input.compose-bar__input     rows=1, aria-label="Compose input"
├── button#compose-send-btn.compose-bar__send     aria-label="Send to session"
└── div#compose-error.compose-bar__error.hidden   role="alert"
```

Attributes on `#compose-input`, set **in markup** (index.html's own comment: Chrome scans
for autofill targets at parse time, before our JS runs):

| Attribute | Value | Why |
|---|---|---|
| `autocomplete` | `off` | suppress browser form autofill |
| `data-1p-ignore` / `data-lpignore` / `data-bwignore` / `data-form-type` | as in `AUTOFILL_SUPPRESSION_ATTRS` | keep password managers out of a free-text field |
| `autocorrect` | `on` | **deliberate deviation** — autocorrect is the *feature* here |
| `spellcheck` | `true` | **deliberate deviation** — this field holds prose |
| `autocapitalize` | `off` | a silently capitalized `Ls` lands in the pane and fails; a missed capital is visible and harmless. Dictation is unaffected. |
| `enterkeyhint` | `enter` | tells the mobile keyboard its Enter is a newline, not a submit |

The three deviations from `_suppressAutofill()` are the point of the field and **must**
carry an inline comment saying so. `test_frontend_js.py` has twice failed on assertions
pinning that helper's literals (v0.13.0, v0.16.1); a future reader must not "fix" this
field by routing it through the helper.

### 4.4 Terminal refit

Any change to the bar's presence or height changes `#terminal-container`'s box, so
xterm.js must refit. `_fitAddon` is module-private to `terminal.js`.

**Add `window._refitTerminal()`** to `terminal.js` — a guarded one-liner
(`if (_term && _fitAddon) { try { _fitAddon.fit(); } catch (_) {} }`), exposed on `window`
alongside the existing `window._openTerminal` / `window._closeTerminal`. The backing
function declaration must be named `_termRefit` (see §8.1 on global-scope collisions).

`app.js` calls `window._refitTerminal()` after: showing the bar, hiding it, and any
auto-grow height change. Guard every call with `if (window._refitTerminal)` — the same
shape `openSession` already uses for `window._openTerminal`.

### 4.5 The mobile-keyboard problem (visualViewport)

**Current behavior, and why it must change.** `terminal.js:367-387` listens on
`visualViewport.resize` and sets an **inline height** on `#terminal-container`:

```
container.style.height = Math.max(100, visualViewport.height - 44) + 'px'
```

This is wrong in three ways *today*, before the compose bar exists:

- The `44` is a hardcoded duplicate of `--header-height`.
- It subtracts only the header — it does not account for `#terminal-search-bar` when
  visible, so opening the keyboard with search open already mis-sizes the terminal.
- It sets an inline height on a `flex:1` child, overriding the flex layout with a number
  that has to be kept correct by hand. Every element added to `.terminal-wrapper` from now
  on has to be remembered here.

Adding the compose bar to this scheme means adding a second term to that subtraction, and
then a third. That is the wrong direction.

**Required change — drive the container, let flex distribute:**

1. In `terminal.js`, the `visualViewport` handler sets **one** CSS custom property on
   `document.documentElement`:
   `--app-viewport-height: <visualViewport.height>px`
   and calls `window._refitTerminal()`. It sets **no** element heights.
2. In `style.css`, `#view-expanded` uses it, falling back to today's value:

   ```
   #view-expanded { height: var(--app-viewport-height, 100dvh); }
   ```

   `.view`'s existing `100vh`/`100dvh` pair stays as the fallback chain for
   `#view-overview` and for browsers with no `visualViewport`.
3. Remove the inline `container.style.height` assignment entirely, and remove the
   hardcoded `44`.

The existing flex column then distributes: header (44, fixed) + search bar (auto, when
visible) + terminal (`flex:1`) + compose bar (auto). Adding or removing any of them is
handled by the layout engine. **This fixes the pre-existing search-bar sizing bug as a
side effect** — call that out in the PR body, and add a regression test for it (§9.3), or
it will look like an unexplained behavior change.

4. Register the handler on **both** `visualViewport` `resize` **and** `scroll`. `scroll`
   fires when the visual viewport pans within the layout viewport (iOS scrolling a focused
   input into view) without a `resize`.
5. Existing cleanup at `terminal.js:721` must remove both listeners and clear
   `--app-viewport-height`, or a stale value will pin the overview view's height after
   `closeSession()`.

**`visualViewport.offsetTop` — deliberately deferred, with a measurement gate.** On iOS,
the browser may scroll the *layout* viewport so a focused input is visible, leaving
`offsetTop > 0`; a `position: fixed` element then sits above the visible band. The
standard remedy is `transform: translateY(offsetTop)` on the fixed container.

Do **not** ship this speculatively. `#view-expanded` is not `position: fixed` today, so it
may not be affected at all. **Measurement gate — required before merge:** on a real
iPhone (Safari + installed PWA) and a real Android Chrome, focus `#compose-input`, and log
`visualViewport.offsetTop` and `getBoundingClientRect()` for `#compose-bar` with the
keyboard open, in both orientations. If the bar is fully visible and `offsetTop` is 0, the
translate is not needed and must not be added. If it is not, add
`transform: translateY(var(--app-viewport-offset, 0px))` driven by the same handler, and
record the measurement in the PR body. Either way the evidence goes in the PR — "I set the
right value" is not evidence the keyboard behaves.

### 4.6 Sizing rules

| Property | Value | Reason |
|---|---|---|
| `#compose-input` initial | `rows=1`, `resize: none` | starts as a one-line bar; the terminal keeps the space until it's needed |
| auto-grow | on `input`: `style.height='auto'` then `style.height = min(scrollHeight, cap) + 'px'`, then `window._refitTerminal()` | standard textarea auto-grow |
| cap | `min(8 * lineHeight, 40dvh)` | past ~8 lines the terminal is more valuable than more visible draft; internal scroll takes over |
| `#compose-bar` | `flex-shrink: 0`, top border matching `--border`, background `--bg-surface` | mirrors `.terminal-search-bar` |
| `#compose-send-btn` | fixed 44×44 min touch target | Apple/Material minimum; the header's 32px `.header-btn` is too small for a primary action on a phone |
| safe area | `padding-bottom: max(6px, env(safe-area-inset-bottom))` | the page is `apple-mobile-web-app-status-bar-style: black-translucent`; without this the bar sits under the iPhone home indicator |

### 4.7 Interaction with `#reconnect-overlay`

`#reconnect-overlay` is `position: absolute; inset: var(--header-height) 0 0` on
`#view-expanded` (`style.css:790-792`), so it covers the compose bar as well as the
terminal. **This is correct and must not be "fixed":** while the terminal relay is down,
a send would either fail or land in a session the user can no longer see. Covering the
bar is honest.

Requirement: no change to the overlay, and a test asserting the compose bar is **not**
excluded from it (e.g. by a higher `z-index`). If a future change makes the bar usable
during reconnect, the send handler must first prove the relay is live — do not build that
now.

---

## 5. Preference storage, three-state semantics, mobile detection

### 5.1 Storage

| Property | Value |
|---|---|
| Mechanism | `localStorage` |
| Key | `muxplex-compose-bar` |
| Value | one of the literal strings `'auto'`, `'on'`, `'off'` |
| Default (absent / unparseable / storage blocked) | `'auto'` |

A bare string, not a JSON blob: there is one field. `deck.js` uses a versioned JSON object
because it has eight; copying that here would be ceremony.

Key naming follows `muxplex-sync-group` (`app.js:222`). Do **not** follow
`tmux-web-device-id` — that is a legacy name predating the rename.

**Read/write must use the established try/catch shape** (`app.js:448-462`, `482-491`):
`localStorage` throws in Safari private browsing and under Edge/Chrome Tracking
Prevention. A blocked read yields `'auto'` for the session; a blocked write is swallowed.
Never let storage failure break the bar.

**Explicitly not a settings key.** Never sent to `PATCH /api/settings`, never in
`SYNCABLE_KEYS`, never federated, never in `GET /api/settings`'s response. Precedent: the
soft deck's settings are localStorage-by-design for exactly this reason (`deck.js:819-822`
— "why these live in localStorage rather than server-synced settings"). A phone wanting the
bar must not turn it on for the user's desktop.

### 5.2 Three-state semantics

```
effective = (pref === 'on')  ? true
          : (pref === 'off') ? false
          : /* 'auto' */       isTouchOrNarrow()
```

- `'auto'` is a **guess**, re-evaluated on every viewport change.
- `'on'` / `'off'` are **explicit user intent** and are never overridden by any heuristic —
  the property the owner asked for.
- Only Settings can select `'auto'`; the header toggle writes `'on'`/`'off'` only (§6.2).

### 5.3 Detection

```
isTouchOrNarrow() := isMobile() || matchMedia('(pointer: coarse)').matches
```

- `isMobile()` (`app.js:416`) is `innerWidth < MOBILE_THRESHOLD` (600). Note this is
  **not** user-agent sniffing — the owner's concern about "a user-agent guess that gets
  tablets wrong" is already avoided in this codebase.
- Width alone still gets the case the owner named wrong: a landscape tablet is ≥600px but
  is exactly the device that wants a compose bar. `pointer: coarse` reports the *primary*
  pointer, so a landscape iPad matches and a touchscreen laptop with a trackpad does not.
  Two signals, OR'd, one line.
- Guard `window.matchMedia` existence (the node test environment has no such stub) and
  treat absence as `false` — falling back to `isMobile()` alone.

**Re-evaluation:** recompute and re-render on `window` `resize` and `orientationchange`,
debounced ~150 ms, but **only when `pref === 'auto'`** — an explicit choice must never be
recomputed. Re-render only if the effective value actually changed (avoid a refit per
resize tick).

### 5.4 Application points

- On `openSession()` (after the expanded view is visible), apply the effective state.
- On `closeSession()`, hide the bar and clear its draft state (see §7.6).
- On toggle or Settings change, apply immediately, then `window._refitTerminal()`.

---

## 6. The header toggle — solving the crowding, not adding to it

### 6.1 The crowding is in a header the bar does not live in

The owner flagged four buttons at `index.html:44-50` — that is the **overview** header
(`#view-overview`). But the compose bar exists only in the **expanded** view, whose header
(`index.html:61-69`) contains:

| Element | Kind |
|---|---|
| `#back-btn` | 36×36 icon |
| `#sidebar-toggle-btn` | icon |
| `#expanded-session-name` | `flex: 1` |
| `#sync-group-btn-expanded` | `.header-btn` |
| `#settings-btn-expanded` | `.header-btn` |

**Recommendation: put the toggle in the expanded header, as a third `.header-btn` in its
upper-right cluster.** This is decisive, not a punt:

- It takes a 2-button cluster to 3, against the overview header's 4-plus-a-`<select>`.
  The crowding problem does not need solving because it does not arise.
- It is adjacent to the thing it controls. A toggle in the overview header changes
  something the user cannot see from the overview.
- It still satisfies "upper-right of the header" — it is the upper-right of the header the
  user is looking at while composing.
- The overview header stays at four buttons. Per this repo's own discipline, do not grow a
  known problem just because the growth is individually justified.

**Pre-existing observation, explicitly out of scope:** at ≤599px the overview header
already carries a wordmark, a view dropdown, a `<select>`, four buttons and
`#connection-status`. That is a real crowding problem today and the answer is an overflow
menu, which is its own change. Noted, not touched.

**Fallback, if the owner still wants it in the overview header:** it cannot go in as a
fifth sibling. The companion change would be, at ≤599px, collapsing `#new-session-btn`
(already duplicated by `#new-session-fab`, `index.html:159`) and `#view-mode-btn` behind a
single `⋯` overflow button. That is a larger, riskier change than this feature warrants —
which is itself an argument for the expanded header.

### 6.2 Toggle behavior

- **Element:** `<button id="compose-toggle-btn" class="header-btn" aria-pressed="false">`
  placed immediately before `#sync-group-btn-expanded`.
- **Glyph:** a keyboard or pencil entity, consistent with the existing HTML-entity icons.
- **Binary, writing explicit intent.** It renders the *effective* state via
  `aria-pressed` + `.header-btn--active` (an existing class with an existing rule —
  `style.css:1262`). Pressing it writes the **opposite of the current effective state** as
  an explicit `'on'`/`'off'`. It never writes `'auto'`.
  - Phone, `pref='auto'` → effective `on` → press → writes `'off'`, bar hides.
  - Press again → writes `'on'`, bar shows.
  - `'auto'` is recoverable only from Settings. This is the standard system/light/dark
    toggle pattern and is exactly the "an explicit choice isn't silently overridden by a
    guess" behavior the owner specified.
- **Settings > Display** gains a `<select id="setting-compose-bar">` with
  `Auto (mobile) / Always on / Always off`, wired to the same storage. This is the only
  surface that can restore `'auto'`.
  **It must not be written through `patchServerSetting`.** Add it to whatever local-only
  handling exists rather than the server-settings path; a test must assert the key never
  appears in any `/api/settings` payload (§9.4).
- **`aria-pressed` and `.header-btn--active` must move together.** The incident that
  produced `test_css_class_definitions.mjs` was precisely `aria-pressed` updating while a
  class had no CSS rule, so the button looked broken while every test passed.

---

## 7. Send semantics

### 7.1 What Enter does

**Enter inserts a newline. Always.** No `keydown` handler intercepts a bare Enter.
`enterkeyhint="enter"` tells the mobile keyboard to render a newline key rather than a
"Go"/"Send" key, so the affordance matches the behavior.

**Ctrl/Cmd+Enter sends** — a send action *separate from* Enter, per the owner's
requirement, and the universal chord for it. The handler must call `preventDefault()`
before acting. (`AGENTS.md`'s `attachCustomKeyEventHandler` incident is about xterm
specifically, but its lesson — a handler that intercepts a key which would otherwise
produce input must `preventDefault()` — applies verbatim.)

This is the one addition beyond the stated requirements. If the owner wants v1 narrower,
cut this and keep the button only; nothing else depends on it.

### 7.2 Text normalization before sending

Applied in this order to the textarea's value:

1. `replace(/\r\n/g, '\n')` — some Android IMEs emit CRLF; a stray `\r` in a pane is a
   carriage return, which overwrites the line.
2. `replace(/\n+$/, '')` — **strip trailing newlines.** A trailing newline is a textarea
   artifact. Sent literally it submits the line, and then `enter: true` submits a second,
   empty line — a phantom prompt on every send.
3. If the result is empty **and** the original had content (i.e. the draft was only
   whitespace/newlines): do not send; show the inline error "Nothing to send."
4. Send `{ text: <normalized>, enter: true }`.

`enter: true` is the default because "compose then send" means "submit it." A future
"insert without submitting" affordance can flip it; do not build one now.

### 7.3 Embedded newlines — the honest v1 behavior

`tmux send-keys -l` sends the literal bytes, including `0x0a`. In a shell's line
discipline `0x0a` is `accept-line`: **each embedded newline executes that line.** In most
TUIs it submits. This is identical to what pasting multiline text into the terminal does
today — it is not a new hazard, but it is a surprising one.

**v1 required behavior: send as typed, and make it visible before the user commits.**

- When the normalized text contains a newline, the send button's accessible label and
  visible affordance change to name the count: `Send 3 lines`. Single-line stays `Send`.
- That is the whole mechanism. No confirmation dialog (it would be in the way on every
  multi-line send), no silent joining (that would corrupt intent).

**Follow-up, explicitly scoped and NOT in v1: bracketed paste.** The genuinely useful
multiline case — composing a paragraph prompt for an agent TUI — needs the pane to receive
the text as a *paste*, not as keystrokes. `tmux load-buffer -` + `paste-buffer -d -p -t
<name>` (`-p` = bracketed) does this, and most modern TUIs (agent REPLs, editors) handle
bracketed paste correctly, inserting newlines without submitting. That is a new server
capability with its own argv-safety analysis (`load-buffer -` takes the text on **stdin**,
not argv, which changes the `MAX_TEXT_BYTES`/E2BIG reasoning), and it should not land in
the same change as the fence relaxation. File it as the natural v2, with an additive
`paste: bool = false` body field.

Be honest in the UI copy and docs about what v1 does. Do not describe v1 multiline as
"compose a multi-line message" when in a shell it means "run these three commands."

### 7.4 Failure handling — loud, inline, and non-destructive

**The draft is never destroyed by a failure.** Clear `#compose-input` **only** on a 200
response. A user who just dictated a paragraph must not lose it to a 403.

**Errors render inline in `#compose-error`** (`role="alert"`), not via `showToast()`. The
existing toast auto-hides after 3000 ms (`app.js:3622`) — a user watching the keyboard or
mid-dictation will miss it, which is a silent failure with extra steps. The inline error
persists until the next successful send or an explicit dismiss.

| Condition | Message |
|---|---|
| 403 `compose_auth_required` | "Compose needs a signed-in session. Open /login and sign in, then try again." (render `/login` as a real link) |
| 403 `compose_origin_required` | "Blocked for security: this request didn't come from this page. Reload and try again." |
| 409 `compose_session_mismatch` | "This session isn't open on this device anymore. Go back and reopen it." |
| 404 | "Session `<name>` no longer exists." |
| 413 | "Too long — the limit is 8 KiB." |
| 400 | server `detail`, verbatim |
| 500 | "The server couldn't send that: `<detail>`" |
| network/`fetch` rejection | "Couldn't reach the server. Your text is still here." |
| any other status | "Send failed (HTTP `<status>`)." — the catch-all must still say something specific |

**Absolute requirement: there is no silent-failure path.** Every branch of the send
handler's `catch` either renders an error or rethrows. No `.catch(function(){})`. This is
the opposite of the fire-and-forget pattern used elsewhere in `app.js` (`openSession`'s
state PATCH, `closeSession`'s DELETE) — those are best-effort bookkeeping; this is the
user's actual message. Both a code-review item and a test (§9.4).

`api()` (`app.js:421`) already attaches the parsed JSON error body as `err.body`, so the
discriminator flags are reachable without new plumbing.

### 7.5 In-flight state

- On send: disable `#compose-send-btn`, set `aria-busy="true"`, leave the textarea
  editable (the user may keep typing; the send captures the value at click time).
- Exactly one request in flight at a time. A second send while one is pending is ignored
  (button is disabled) — no queue.
- On resolution: re-enable, clear `aria-busy`.
- On success: clear the textarea, reset its height to one row, call
  `window._refitTerminal()`, and **keep focus in the textarea** so a dictate→send→dictate
  loop does not require re-tapping the field.

### 7.6 Draft lifetime

The draft is **in-memory only**, held in the DOM. It is cleared on `closeSession()` and on
switching sessions. It is **not** persisted to `localStorage`: a draft is transient, and
persisting one creates a "why did my old text reappear in a different session?" bug and a
place for typed secrets to linger on disk. If cross-session draft persistence is ever
wanted, it is a separate, deliberate feature.

---

## 8. Guard tests and the cross-file bug classes

This codebase has produced cross-file bugs twice, and both guards have blind spots this
feature could fall into.

### 8.1 `test_shared_scope.mjs` — global scope collisions

It parses `<script src>` out of `index.html` and evaluates each into one shared `vm`
context, asserting no `SyntaxError`. Any new script added to `index.html` is covered
automatically.

**Requirements:**

- **Do not add a new frontend script.** All compose-bar logic goes in `app.js`; the only
  `terminal.js` change is `_termRefit` + `window._refitTerminal` + the `visualViewport`
  rework. No new file means no new collision surface.
- Every new top-level binding in `app.js` is prefixed `_compose*` /
  `COMPOSE_*` (e.g. `_composePref`, `_composeSendInFlight`,
  `COMPOSE_PREF_STORAGE_KEY`).
- Every new top-level binding in `terminal.js` is prefixed `_term*` — the existing
  convention that fixed the v0.31.3 `_ownDeviceId` collision. `window._refitTerminal` is a
  window property, but its backing declaration is a top-level binding: name it
  `_termRefit`.
- **Watch for `_composeInput`-style names colliding with an existing binding.** Grep both
  files for each new identifier before adding it. The guard will catch it, but catching it
  in review is cheaper.

### 8.2 `test_css_class_definitions.mjs` — the template-literal blind spot

It extracts class names from `classList.add/remove/toggle(...)` and `.className = '...'`
**string literals only**. Classes emitted inside **template literals** (`innerHTML =
\`<div class="foo">\``) are **not covered** — a class applied that way with no CSS rule
would reproduce the exact `header-btn--active` bug the guard exists to prevent.

**Requirements:**

- **All compose-bar markup is static in `index.html`.** No `innerHTML`, no template
  literal, no `createElement`. Static markup has zero dynamically-applied class names, so
  the blind spot cannot be entered.
- The only classes the JS applies are `hidden` (defined, `style.css:114`) and
  `header-btn--active` (defined, `style.css:1262`), both via `classList.toggle` string
  literals — inside the guard's coverage.
- Every new class in the markup (`.compose-bar`, `.compose-bar__input`,
  `.compose-bar__send`, `.compose-bar__error`) must have a real rule in `style.css`. The
  guard will not check these (they are never applied by JS), so §9.3 adds an explicit
  assertion.

**Observation, separate change:** the blind spot itself is worth closing — extend
`extractAppliedClasses` to also scan template-literal `class="..."` occurrences. Out of
scope here (this feature deliberately avoids the pattern), but it is the third instance of
"a guard exists and has a hole"; file it.

### 8.3 `test_frontend_js.py` — the source-text tripwire

229 of its 332 tests regex-match `app.js` source. A refactor that preserves behavior can
still fail it (v0.13.0, v0.16.1). Expect breakage from the `terminal.js` `visualViewport`
rework in particular; if an assertion pins the old `container.style.height` code, **fix
the assertion to follow the new structure** (assert the custom-property write *and* the
refit call), never loosen it to pass.

---

## 9. Test plan

### 9.1 Environment discipline (non-negotiable)

`AGENTS.md`: never run the suite on a host running a live muxplex. Commit locally first,
then `make test` (DTU), then push. The commit is the checkpoint that makes
`git archive HEAD` correct.

Frontend: `node --test frontend/tests/*.mjs` — the glob, never a single file.

### 9.2 Python — `muxplex/tests/test_compose.py` (new)

Fixtures follow `test_input.py`'s isolation pattern plus `test_api.py`'s cookie injection
and `test_auth.py`'s `_InjectClientMiddleware` for socket-level client addresses. Note
`TestClient` sets `client.host = "testclient"`, which is **not** localhost — so cookie
tests work by default and localhost tests require the injector.

**Fence tests — one per row of §2.8, each asserting status AND that no tmux subprocess
ran.** The second half matters more than the first: a fence that returns 403 *after*
sending keys is not a fence.

| Test | Expect |
|---|---|
| valid cookie + same-origin + matching `device_id` | 200, argv `("send-keys","-l","-t",name,"--",text)` then `("send-keys","-t",name,"Enter")` |
| Bearer token (valid federation key) | 403, `compose_auth_required`, **no tmux call** |
| `Authorization: Basic` (correct credentials) | 403, no tmux call |
| socket-level `127.0.0.1`, no cookie | 403, no tmux call |
| socket-level `127.0.0.1`, **with** valid cookie | 200 — proves §2.4's classification refinement |
| no `muxplex_auth_class` in scope (app mounted without `AuthMiddleware`) | 403 + a distinct warning log — proves fail-closed on ambiguity |
| `muxplex_auth_class` set to an unrecognized string | 403 |
| `Origin` absent | 403 |
| `Origin` host ≠ `Host` | 403 |
| `Origin` scheme differs, host matches | 200 — pins the deliberate host-only comparison |
| `Sec-Fetch-Site: cross-site` | 403 |
| `Sec-Fetch-Site` absent | 200 — pins the compatibility decision |
| `device_id` omitted | 400 |
| `device_id` unknown | 404 |
| group's `active_session` ≠ target | 409, `compose_session_mismatch`, no tmux call |
| target not in `get_session_list()` | 404 |
| `get_session_list()` empty | 404 for every name (fail-closed cache) |
| `text` > `MAX_TEXT_BYTES` | 413 |
| empty `text`, `enter=false` | 400 |
| hostile payload `` ; rm -rf / && $(reboot) `id` \| tee /etc/passwd `` | exact argv asserted, mirroring `test_input.py:316` |

**Fence-independence tests (these are the regression guards that matter most):**

| Test | Expect |
|---|---|
| `input_enabled=False`, cookie caller, `/compose` | **200** — compose does not consult it |
| `input_allowed_sessions=[]`, cookie caller, `/compose` | **200** |
| `input_enabled=False`, **cookie** caller, `/input` | **403** — the old fence is untouched, for every class |
| `input_enabled=True` + `["*"]`, Bearer caller, `/input` | 200 — Bearer's existing path is unchanged |
| `LOCAL_ONLY_KEYS` membership unchanged | exact frozenset equality — no key added or removed |
| no `compose*` key in `DEFAULT_SETTINGS` / `SYNCABLE_KEYS` | asserts §2.7's "no new settings key" |

**Contract tests:**

- 200 response body is exactly `{"ok": true, "session": name}` — no `snapshot` key.
- No `capture_pane` call and no `asyncio.sleep` on the success path (pins the no-read-back
  decision; a future refactor that "helpfully" adds the snapshot back should fail).
- `ComposeInputPayload` rejects `keys` and `lines` (extra fields) or ignores them — pick
  one, assert it, document it.
- Audit: exactly one `info` line per accepted send, containing `compose`, the session, the
  char count, and a ≤16-char redacted preview; the full text appears at `debug` only and
  never at `info`. Mirror `test_input.py`'s log-hygiene assertions.
- **`SameSite=Strict` pin** (§2.5): `POST /login`'s `Set-Cookie` for `muxplex_session`
  contains `samesite=strict` (case-insensitive) **and** `httponly`. Lives in
  `test_compose.py`, not `test_api.py`, with a comment naming compose as the dependent —
  a future loosening to `Lax` must fail the test belonging to the feature that depends
  on it, not a login test whose author has no reason to think about compose.

**Middleware tests — `test_auth.py` additions:**

- Every existing test passes unmodified (classification changed no outcome).
- Each branch sets the expected `muxplex_auth_class`: localhost-without-cookie →
  `localhost`; localhost-with-cookie → `cookie`; cookie → `cookie`; bearer → `bearer`;
  basic → `basic`; exempt path → `exempt`; `.css` → `static`.
- No request header can influence the value: send
  `X-Muxplex-Auth-Class: cookie` / `muxplex_auth_class: cookie` as headers and a cookie of
  that name from an unauthenticated caller; assert the scope value is unaffected and the
  request is still refused.

### 9.3 Frontend — `frontend/tests/test_compose.mjs` (new) + existing suites

- **Preference:** `'auto'`/`'on'`/`'off'` resolution against a stubbed
  `innerWidth`/`matchMedia`; unknown stored value → `'auto'`; a throwing `localStorage`
  (get and set independently) → `'auto'`, no exception escapes.
- **Toggle:** from effective-on writes `'off'`; from effective-off writes `'on'`; never
  writes `'auto'`; `aria-pressed` and `header-btn--active` change together.
- **Enter:** a bare Enter `keydown` does **not** trigger a send. Ctrl+Enter and Cmd+Enter
  do, and both call `preventDefault()`.
- **Normalization:** `"a\r\nb\n\n"` → `"a\nb"`; whitespace-only draft → no request, error
  shown; the request body is exactly `{text, enter: true}`.
- **Failure surfacing** — a parameterized test over every row of §7.4's table: the error
  element is un-hidden, its text matches, **and the textarea still holds the draft**.
- **Success:** textarea cleared, height reset, `window._refitTerminal` called, focus
  retained.
- **No silent swallow:** a static assertion that the compose send path contains no
  `catch (_) {}` / `.catch(function(){})` empty handler, plus a behavioral test that a
  rejected `fetch` renders an error.
- **Refit:** `window._refitTerminal` is called on show, on hide, and on auto-grow.
- **Layout regression (the pre-existing bug this fixes):** with `#terminal-search-bar`
  visible and a simulated `visualViewport` resize, assert the handler sets
  `--app-viewport-height` and sets **no** inline height on `#terminal-container`.
- **CSS coverage for static markup** (the §8.2 gap): a test that reads `index.html`,
  extracts every `class="..."` token from the `#compose-bar` subtree, and asserts each
  resolves in `style.css`. This is the assertion `test_css_class_definitions.mjs` cannot
  make.
- `test_shared_scope.mjs` and `test_css_class_definitions.mjs` must pass **unmodified** —
  if either needs an edit or a `KNOWN_EXCEPTIONS` entry, the implementation took a shape
  §8 forbids.
- `test_frontend_html.py`: assert the presence of `#compose-bar`, `#compose-input`,
  `#compose-send-btn`, `#compose-error`, `#compose-toggle-btn`, and the markup attribute
  set from §4.3 — including that `autocorrect="on"` / `spellcheck="true"` are present
  (they are the deliberate deviation and must not be "corrected" later).

### 9.4 Round-trip proof against a real session

Unit tests cannot prove the round trip. Required evidence, in a DTU (`make test`
environment or an isolated scratch instance — **never** the dev box running a live
muxplex; see `AGENTS.md`'s two session-destruction mechanisms):

1. Start muxplex in the DTU with a real tmux; create session `compose-probe`
   (`tmux -L <unique-scratch-name>`, socket-scoped, per `AGENTS.md`).
2. `POST /login` → capture the `muxplex_session` cookie.
3. `POST /api/sessions/compose-probe/connect?device_id=<id>`.
4. `POST /api/sessions/compose-probe/compose?device_id=<id>` with cookie +
   `Origin: <server origin>`, body `{"text": "echo COMPOSE_PROOF_$$", "enter": true}` → 200.
5. `GET /api/sessions` (or `capture_pane`) after the poll cycle → the pane contains the
   echoed marker. **This is the round-trip evidence.** Capture the literal output in the
   PR body.
6. **Fence still refuses, with `input_enabled` deliberately `false` throughout:**
   - same request with `Authorization: Bearer <federation key>` and no cookie → 403,
     marker does **not** appear.
   - same request with no `Origin` → 403.
   - same request with `Origin: https://evil.example` → 403.
   - `POST /api/sessions/compose-probe/input` with the cookie → **403** (the old fence is
     intact for the interactive class too).
   Each with its literal response captured.
7. Confirm the audit log shows exactly one `compose` info line for the accepted send, with
   a redacted preview, and zero for the refusals (each refusal logs at `warning`).
8. After the run, verify the live server on the dev box is still up
   (`GET :8088/api/instance-info` → 200), per `AGENTS.md`'s scratch-run rule.

### 9.5 Device verification (manual, required before merge)

Not optional — the entire feature is a mobile-layout feature, and §4.5's measurement gate
cannot be satisfied any other way.

- iPhone Safari **and** the installed PWA; Android Chrome. Both orientations.
- Keyboard opens: terminal shrinks, bar stays fully visible, nothing is covered. Record
  `visualViewport.offsetTop` and `#compose-bar`'s `getBoundingClientRect()`.
- Keyboard closes: layout restores; the terminal refits (no clipped last row).
- Scroll the terminal back while the bar holds a draft → the draft survives; send → it
  lands.
- Native dictation into the field, then send.
- Auto-grow to the cap, then internal scroll; terminal refits at each step.
- `closeSession()` → bar hides, `--app-viewport-height` cleared, overview renders at full
  height (this is the regression the cleanup at `terminal.js:721` guards).
- Toggle off → bar hides, terminal reclaims the space immediately.

---

## 10. Files changed

| File | Change |
|---|---|
| `muxplex/auth.py` | set `scope["muxplex_auth_class"]` on each admit branch; evaluate cookie inside the localhost branch for classification only |
| `muxplex/compose_input.py` | **new** — `COMPOSE_ALLOWED_AUTH_CLASSES`, `caller_may_compose()`, origin/`Sec-Fetch-Site` predicates. Pure, no I/O, heavily documented |
| `muxplex/main.py` | extract `_perform_session_input()` from `send_session_input`; add `ComposeInputPayload`; add `POST /api/sessions/{name}/compose` |
| `muxplex/frontend/index.html` | `#compose-bar` subtree in `.terminal-wrapper`; `#compose-toggle-btn` in the expanded header; `#setting-compose-bar` in Settings > Display |
| `muxplex/frontend/app.js` | pref read/write/resolve; toggle wiring; send handler; error rendering; auto-grow; apply on open/close/resize. All bindings `_compose*` |
| `muxplex/frontend/terminal.js` | `_termRefit` + `window._refitTerminal`; `visualViewport` rework (custom property, no inline heights, `scroll` listener, cleanup) |
| `muxplex/frontend/style.css` | `.compose-bar*` rules; `#view-expanded { height: var(--app-viewport-height, 100dvh) }` |
| `muxplex/tests/test_compose.py` | **new** — §9.2 |
| `muxplex/tests/test_auth.py` | classification assertions; unforgeability |
| `muxplex/tests/test_frontend_html.py` | markup assertions |
| `muxplex/frontend/tests/test_compose.mjs` | **new** — §9.3 |
| `AGENTS.md` | compose subsection; §0.1 finding; deliberate fence duplication |
| `docs/API_SEMANTICS.md` | endpoint semantics; required `device_id`; `compose_session_mismatch` |
| `docs/AGENT_GUIDE.md` | one line: agents use `/input`; `/compose` 403s for Bearer, by design |

**Not changed:** `muxplex/terminal_input.py` (argv layer is already correct and shared),
`muxplex/settings.py` (no new key — §2.7), the `/input` handler's fences, `LOCAL_ONLY_KEYS`,
`SYNCABLE_KEYS`.

`CHANGELOG.md` and version bumps are the owner's, at release time.

---

## 11. Explicitly out of scope

| Item | Why, and where it goes |
|---|---|
| Closing the `WS /terminal/ws` Bearer door (§0.1) | Separate issue. Verify with a live raw-socket probe first. This feature is correct whether it is open or closed. |
| Bracketed paste (`paste: true`) for true multiline (§7.3) | The natural v2. Different argv surface (stdin, not argv), so a separate security review. |
| Overview-header overflow menu (§6.1) | Pre-existing crowding, unrelated to this feature. Do not grow it. |
| `test_css_class_definitions.mjs` template-literal coverage (§8.2) | Third instance of "a guard with a hole." File it; this feature avoids the pattern rather than depending on the fix. |
| Draft persistence across sessions (§7.6) | Deliberate omission; would need its own design for the secrets-on-disk question. |
| Named-key controls (Esc / Ctrl-C / arrows) in the bar | `keys` is deliberately absent from the v1 payload. Add the field only when a real control exists. |
| Rate limiting on `/compose` | The caller is authenticated and already holds a writable terminal; a limit would restrict them below what they already have. |

---

## 12. Decisions the owner should confirm

Everything else in this spec is resolved analysis. These four are genuine calls:

1. **Toggle placement.** §6.1 recommends the **expanded** header (2 buttons → 3) over the
   overview header (4 → 5). This differs from the literal instruction; the reasoning is
   that the bar only exists in the expanded view, and putting the toggle where the crowding
   isn't makes the crowding problem not arise. Confirm, or take the §6.1 fallback (overflow
   menu at ≤599px).
2. **Ctrl/Cmd+Enter to send** (§7.1). Compatible with "Enter must insert a newline," but it
   is the one addition beyond the stated requirements. Keep or cut.
3. **v1 multiline is literal** (§7.3) — in a shell, three lines run three commands. The
   mitigation is the "Send 3 lines" label; the real fix is bracketed paste in v2. Confirm
   that v1 shipping with this semantic is acceptable, or promote bracketed paste into v1
   and accept the larger security surface in one change.
4. **Refusing the `localhost` class** (§2.3) means a user on `http://localhost:8088` must
   visit `/login` once before compose works. This is what makes the design immune to DNS
   rebinding rather than merely resistant to it. Confirm the one-time login is acceptable.

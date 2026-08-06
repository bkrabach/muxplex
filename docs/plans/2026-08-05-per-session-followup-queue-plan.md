# Per-session follow-up queue — implementation specification

Status: **SHIPPED in v0.38.0 (2026-08-05).** Retained as an architectural
decision record. §0's blocking prerequisite — the tmux bell hook dialing a
plaintext URL into a TLS listener — was fixed in the same release before the
queue itself was built on top of it (`CHANGELOG.md`'s v0.38.0 entry, "the
tmux bell hook was silently dead on every TLS host"). See
`docs/API_SEMANTICS.md` and `AGENTS.md` for the shipped invariants.

**Verdict:** the design is sound and buildable. **It is blocked on a prerequisite (§0), which
is a live production defect discovered while verifying the trigger.**

---

## 0. STOP — read this before anything else

### 0.1 The primary bell path is dead on this host, and has been silently dead

The queue is specified to trigger on `POST /api/sessions/{name}/bell` (`main.py:2018`,
`receive_bell()`) — the tmux `alert-bell` hook path. That decision is correct (§1 proves it
empirically). **But on the machine this spec was written on, that path delivers zero bells,
and `GET /api/instance-info` reports it healthy.**

`_arm_bell_hook()` (`main.py:325`) bakes a hardcoded **plaintext** URL into the hook:

```
run-shell 'curl -sfo /dev/null -X POST http://localhost:{SERVER_PORT}/api/sessions/#{session_name}/bell || true'
```

This host serves **TLS** on that same port (`settings.json` has `tls_cert` / `tls_key` set;
`ss -ltnp` shows `muxplex` on `0.0.0.0:8088`). Running the hook's exact command:

```
$ curl -sfo /dev/null -X POST http://localhost:8088/api/sessions/nonexistent-probe/bell
$ echo $?
52
$ curl -sv -X POST http://localhost:8088/api/sessions/nonexistent-probe/bell 2>&1 | tail -3
* Connected to localhost (127.0.0.1) port 8088
> POST /api/sessions/nonexistent-probe/bell HTTP/1.1
* Empty reply from server            ← plaintext request into a TLS listener

$ curl -sk -o /dev/null -w '%{http_code}\n' -X POST https://localhost:8088/api/sessions/nonexistent-probe/bell
200                                   ← the endpoint is fine; the scheme is wrong
```

TCP connects, the TLS listener discards the plaintext request, curl exits 52. `-sf` prints
nothing and `|| true` discards the exit status, so tmux sees success and nothing is logged
anywhere.

**Evidence that this is not theoretical.** 30 minutes of uvicorn access log on the live,
actively-used 54-session instance:

| Endpoint | Requests in 30 min |
|---|---|
| `GET /api/state?device_id=…` | 1227 |
| `POST /api/heartbeat` | 641 |
| `POST /api/sessions/{n}/input` | 3 |
| **`POST /api/sessions/{n}/bell`** | **1 — and that one was this investigation's own HTTPS probe** |

Access logging is plainly on (641 heartbeats captured). Meanwhile `state.json` shows bells
that fired 187 s and 291 s before the sample was taken, and one session sitting at
`unseen_count: 17`. **Every one of those bells came from `bells.process_bell_flags()` — the
poll fallback.** The owner's attention view has been running entirely on the mechanism the
code calls a fallback, on a host where `GET /api/instance-info` reports
`"bell_hook_armed": true`.

`bell_hook_armed` is honest about what it measures and misleading about what it implies: it
records that `set-hook` was **accepted**, never that a bell posted by that hook can be
**delivered**. Registration succeeded. Delivery cannot.

**Scope.** `tls_cert`/`tls_key` default to `""`, so a default HTTP install is unaffected and
the hook works there. TLS is not an exotic posture — the repo ships `muxplex setup-tls`,
`GET /api/ca`, a README section on installing the CA, and this is the owner's own machine.
Any TLS instance is affected.

### 0.2 What this means for the follow-up queue

Do not build the queue on top of this. A queue triggered by a signal that never arrives is
the exact silent-uselessness failure the brief warned about, arrived at from the other
direction: the trap was named as "don't hang it on the poll path," and the poll path is in
fact the *only* path presently working here.

**Prerequisite P1 — the hook must reach the server.** Derive the hook's scheme from the same
TLS configuration `serve()` uses. On a TLS instance the hook must use `https://` with
`--cacert <local CA>` or, acceptably for a loopback destination, `-k`. Loopback TLS
verification is not load-bearing — there is no MITM position on `lo`, and the request carries
no secret.

**Prerequisite P2 — `bell_hook_armed` must mean "a bell would land."** After `set-hook`
succeeds, `_arm_bell_hook()` should issue one self-probe over the exact scheme/port/verify
settings it just baked into the hook, and only then report armed. Probe
`GET /api/instance-info` — it is in `auth._AUTH_EXEMPT_PATHS` (`auth.py:237`) so it needs no
credential, and unlike `POST …/bell` it writes no state (`receive_bell()` would create a
state entry for whatever name the probe used). A failed probe sets `_bell_hook_armed = False`
and records the reason in `_bell_hook_last_error`, which `GET /api/instance-info` already
surfaces. Registration-succeeded and delivery-works stop being conflated.

The probe must use the *same* HTTP client behaviour the hook's `curl` will use — same scheme,
same port, same certificate handling. A probe that verifies differently from the hook proves
nothing about the hook.

P1 and P2 are small, independently valuable, and should ship as their own fix with their own
test before any queue code is written. **They are also the queue's health check**: §6.5
requires the queue to refuse to accept new items while `_bell_hook_armed` is false, because
a queue armed against a dead trigger is worse than no queue.

### 0.3 Second finding, not blocking, but it shapes the UX

`tmux send-keys -t <session>` targets the session's **current window's active pane**, not
the window that belled. Measured (§1, case H): with `probe2` current-window = 1, a bell fired
in window 2 and the marker text landed in window 1.

The common muxplex session layout is four windows (`amplifier`, `shell`, `git`, `files` —
see `AGENTS.md`'s recovery section). An agent in the `amplifier` window bells; the queue
advances; the follow-up is typed into whichever window the user last left current. With a
human at the compose bar this is self-correcting — they are looking at the pane. **With an
autonomous queue nobody is looking.**

This does not block the feature, and the queue must not try to be clever about it (targeting
the belled window means storing per-item window targets, which is a workflow engine). It is
handled honestly in the UI (§7.3 supplies the data, §9.1 puts it on screen): the queue
affordance states which window it will type
into, resolved live from `#{window_index}:#{window_name}` at render time.

---

## 1. Empirical verification of the bell paths

Run against an isolated tmux 3.4 server (`tmux -L muxq-bellprobe-<pid>` with a scratch
`TMUX_TMPDIR`, socket-scoped teardown — never a bare `kill-server`, per `AGENTS.md`). The
global hook was `alert-bell → run-shell 'echo … >> /tmp/muxq-bells.txt'`. `monitor-bell` was
`on` and `bell-action` was `any` (both tmux defaults).

| # | Scenario | `alert-bell` hook | `window_bell_flag` |
|---|---|---|---|
| A | Detached, one bell | **fired** | `1` |
| B | **Client attached**, bell in the current window | **fired** | **`0` — never set** |
| C | Attached, 3 bells 600 ms apart | **fired 3×** | `0` |
| D | Attached, 3 bells in one burst (`printf '\a\a\a'`) | **fired 1×** | `0` |
| E | Detached, flag already stuck at `1`, 3 more bells | **fired 3×** | stays `1` |
| F | `tmux new-session -d` | **did not fire** | — |
| G | Attached, bell in a **non-current** window | **fired** (`session=probe2 window=bg`) | set on that window only |
| H | `send-keys -l -t probe2 -- MARKER` with window 1 current | — | marker landed in **window 1**, not the belled window 2 |

**Case B is the trap the brief named, confirmed.** With a client attached to the window
that bells, `window_bell_flag` never leaves `0`. `bells.poll_bell_flag()` reads exactly that
format, so `process_bell_flags()` sees no `0→1` transition and counts nothing. A queue driven
off the poll path never advances for any session the user is actually watching.

**Case E is a second, independent failure of the poll path**, not previously documented: even
detached, once the flag is stuck at `1` (it clears only when the window is next activated
inside tmux), every subsequent bell is invisible to the transition detector. The poll path
sees the *first* bell of a run and then nothing.

**Case G adds a third:** `poll_bell_flag()` calls `display-message -t <session>`, which
evaluates `#{window_bell_flag}` against the session's *current* window. A bell in a
background window sets that window's flag; the session-level read returns `0`.

The hook path fired in every case. **Trigger on `receive_bell()`. This is now measured, not
assumed.** (These findings also justify keeping `process_bell_flags()` — case A shows it
catches bells that fired before the coordinator armed the hook — while confirming it must
never drive the queue.)

**Case D is a real constraint on the design:** tmux coalesces bells arriving inside one
alert-check window into a single hook fire. Multiple bells can become one advance. This is
the safe direction (under-advance, never over-advance) and is accepted, not worked around.

**Case F closes the seeded-bell question, structurally.** See §4.

---

## 2. Scope

### In scope

A per-session, server-side, persisted list of text items. When the session's bell rings, the
head item is typed into that session and discarded. The list is manageable (add, edit,
reorder, remove, clear) while items are pending. A failed send halts the queue loudly. A
fired item leaves no trace.

### Deliberately out of scope

| Excluded | Why |
|---|---|
| Retries, backoff, dead-letter | A failed send means the fence closed, the session vanished, or tmux broke. None of those get better by trying again. Halt and tell the human. |
| Conditionals, scheduling, timers | This is a list and an event handler. |
| `keys` array on a queued item | A queued `C-c` firing unattended is strictly worse than a queued sentence, and nothing wants it. `text` + `enter` only. |
| History, audit list, completed items | Explicit owner requirement. Fired means gone. |
| Queues on **remote** (federated) sessions | §8. |
| A queued item changing `?sort=attention` | Its predicate is re-implemented in three places (`main.py`'s `_attention_order()`, `frontend/app.js`'s `sortByAttention()`, muxplex-deck's `attention.py`) and all three must move together. Not for a v1 feature. |
| Following a session rename | §5.4. |

---

## 3. Storage

### 3.1 Shape and location

A **new top-level key** in `state.json`, not a field under `state["sessions"][name]`:

```jsonc
"followups": {
  "<session name>": {
    "revision": 7,
    "items": [
      { "id": "8f3c…", "text": "now run the tests", "enter": true, "created_at": 1785952332.104 }
    ],
    "halted": null
  }
}
```

`halted`, when set:

```jsonc
"halted": {
  "reason": "input_not_allowed",
  "detail": "Session 'foo' does not match any input_allowed_sessions pattern",
  "at": 1785952400.0,
  "item_id": "8f3c…"
}
```

An entry is **deleted entirely** when `items` is empty and `halted` is null. Absence means
"no queue." This keeps `state.json` from accumulating one empty object per session forever
(the live instance already tracks 54).

### 3.2 Why top-level and not nested under `sessions[name]`

Nesting would give free cleanup — poll-cycle step 6 (`main.py`) deletes
`state["sessions"][name]` for every name absent from the enumeration. That free cleanup is a
trap:

```python
names = await enumerate_sessions()   # returns [] when tmux is not running
name_set = set(names)
...
deleted = [s for s in list(state["sessions"]) if s not in name_set]
for name in deleted:
    del state["sessions"][name]
```

`enumerate_sessions()`' own docstring (`sessions.py:311`) states it "returns `[]` if tmux is
not running," and the manifest code four steps earlier calls out that exact ambiguity as the
reason it uses a separate `probe_tmux_epoch()`. **One transient tmux hiccup wipes every
per-session state entry.** Bells survive that because they regenerate. Queued follow-up text
is authored by the user and is gone forever.

Top-level storage costs one explicit reap (§3.4) — about four lines — and makes the queue's
lifetime a decision instead of an accident.

### 3.3 `normalize_state()`

One line: `state.setdefault("followups", {})`.

No invariant to enforce (unlike `sync_groups`' `"global"` check), and deliberately **no**
repair of malformed entries. A hand-edited or corrupt entry is caught fail-closed at fire
time by the gate order in §6.3 and halts rather than sending.

### 3.4 Reaping

New step **6b** in `_run_poll_cycle`, immediately after step 6:

- Only runs when `_epoch_now is not None` — the value the poll cycle *already computed* at
  step 1b for the presence manifest. `probe_tmux_epoch()` returns `None` iff no tmux server
  is running, distinguishing "tmux is down" from "zero sessions" (`sessions.py:244`). If it
  is `None`, **skip reaping entirely this cycle.**
- When tmux is confirmed alive, delete every `state["followups"]` key not in `name_set`, and
  log one `warning` per dropped queue naming the session and the item count — dropping
  user-authored text must never be silent.

This reuses an existing computation and adds no tmux call.

### 3.5 Not a setting

`followups` lives in `state.json`, never `settings.json`. It is therefore automatically
outside `SYNCABLE_KEYS` and `LOCAL_ONLY_KEYS`, is never federation-synced, never touched by
`patch_settings()` / `apply_synced_settings()`, and cannot participate in the
`views`/`settings_updated_at` LWW races that `views_updated_at` exists to break. `save_state()`
is already atomic (tmp + `os.replace`, `state.py:409`) and every writer already holds
`state_lock`.

---

## 4. The seeded bell does not advance the queue — structurally

v0.36.1 seeds a bell at session creation so a new session sorts to the top of the attention
view. That seeding happens at `main.py:453-467`, inside the poll cycle, writing
`{"last_fired_at": now, "unseen_count": 1, "seen_at": None}` **directly into the state dict**.
It never calls `receive_bell()`. Neither does `bells.process_bell_flags()` (`bells.py:96`),
which also mutates state directly.

`receive_bell()` has exactly one caller in the entire system: the tmux `alert-bell` hook. And
case F above measured that `tmux new-session` does not fire `alert-bell`.

**Therefore triggering exclusively inside `receive_bell()` excludes the seeded bell and the
poll fallback with no discriminator logic at all** — no timestamp comparison, no flag, no
heuristic. The exclusion is a property of where the code lives.

That property is load-bearing and invisible, which makes it fragile. Two things protect it:

1. A comment at the seeding site (`main.py:453`) and at `receive_bell()` stating that routing
   seeded or poll-derived bells through `receive_bell()` would silently give the queue a
   spurious advance.
2. A test (§10, T-04) that creates a session with a non-empty queue, runs a full poll cycle
   including the seeding branch, and asserts the queue is untouched.

---

## 5. Advance semantics

### 5.1 The rule

**One item per accepted bell.** A bell is *accepted* for queue purposes when all of:

- `state["followups"][name]` exists with a non-empty `items` list, and
- `halted` is null, and
- `name` is not in the in-flight set `_followup_sending`, and
- `time.time() - _followup_last_send_at.get(name, 0) >= FOLLOWUP_SETTLE_SECONDS`.

Otherwise the bell does its normal bookkeeping and nothing else. A bell for a session with no
queue is free.

### 5.2 Peek–send–remove, never pop–send

The send is a subprocess and must not happen under `state_lock` (the poll cycle wants that
lock; a hung tmux would wedge the server). So the sequence is:

1. **Under `state_lock`** (the same critical section `receive_bell()` already holds for its
   bell bookkeeping): evaluate acceptance; **peek** the head item without removing it; add
   `name` to `_followup_sending`; set `_followup_last_send_at[name] = now`; `save_state()`.
2. **Outside the lock:** run the fence re-evaluation (§6.3), then `run_tmux(*build_send_text_argv(...))`
   and, if `enter`, `run_tmux(*build_send_key_argv(name, "Enter"))`.
3. **Under `state_lock` again:** on success remove the item **by `id`** and bump `revision`;
   on any failure set `halted` and leave the item exactly where it is. Delete the queue entry
   if it is now empty and unhalted. `save_state()`.
4. **`finally`:** discard `name` from `_followup_sending` — on every path, including
   exceptions, or that session's queue is wedged until restart.

Peek-then-remove-by-id is what makes "a failed send never loses the item" true by
construction rather than by a compensating write. Removing by id rather than index makes it
correct even if a concurrent `PUT` reordered the list mid-flight.

There is **no read-back capture.** `/input` sleeps 400 ms and re-captures the pane for a
caller that is waiting on the response. The queue has no caller. Skipping it keeps the
in-flight window at roughly one `send-keys` exec.

### 5.3 The three races, answered

**Two bells in rapid succession.** `state_lock` serialises the decision, so the two calls
cannot both peek the same head. The first sets `_followup_sending` and `_followup_last_send_at`
*before releasing the lock*; the second therefore fails acceptance and does nothing. Without
this the two would pop items 1 and 2 and send both — "dumps all three at once", exactly the
failure named in the brief.

**A bell arriving while a send is in flight.** Same mechanism — the in-flight set. It is
**ignored**, not deferred. Deferring would reconstruct the dump behaviour one layer down; a
bell during a send is either the coalescing artefact of case D or an echo of the item just
sent, and neither is a turn completion.

**A bell caused by the item we just sent.** This is the case the in-flight set cannot cover,
because the send has already completed. Case C measured that two bells 600 ms apart produce
two separate hook fires — an agent CLI that bells on receipt and again on completion would
advance the queue twice. Guarded by a single settle window:

> **`FOLLOWUP_SETTLE_SECONDS = 2.0`** — a named module constant, the **only** timing element
> in this design. A bell for session *N* within 2 s of the queue's own send to *N* is not
> accepted.

Accepted cost, stated plainly: an agent that completes a turn in under 2 s has that bell
swallowed and the queue stalls until the next bell. **Stalling is visible (§9.3's pending list
and §9.5's badge both show N items waiting) and recoverable (the next bell advances). Dumping
is neither.** The asymmetry decides it.

`_followup_sending` (a `set[str]`) and `_followup_last_send_at` (a `dict[str, float]`) are
module-level and **in-memory only, never persisted** — after a restart there is no in-flight
send and no settle window to honour. Persisting them would be state kept for its own sake.
They are the direct analogue of `bells._bell_seen`.

### 5.4 Session lifecycle

| Event | Queue behaviour |
|---|---|
| Session deleted (API or `tmux kill-session`) | Reaped by step 6b once tmux is confirmed alive (§3.4), with a warning log. |
| tmux server down / transient enumeration failure | **Queue preserved.** Step 6b does not run. |
| muxplex restart | **Queue preserved.** This is the point of persisting. |
| Session renamed outside muxplex | Presents as delete + create. Old queue reaped; new name starts empty. |

The rename case is deliberate. muxplex has no rename endpoint, `tmux rename-session` is
externally reachable, and nothing distinguishes a rename from a delete-plus-create. Following
a name on a guess risks firing someone's queued text into a different session. Dropping it is
the fail-safe direction, and the reap's warning log is the user's notice.

---

## 6. The fence — the queue is a third caller, with no bypass

This is muxplex's first autonomous write. Everything else the server does is human-initiated
or read-only.

### 6.1 The rule

`terminal_input.input_allowed_for_session(name, settings)` (`terminal_input.py:90`) is the
single fence evaluation. `/input` (`main.py:1723`) and the terminal WS gate (`main.py:2960`)
already both call it. **The queue is the third caller. There is no queue-specific fence, no
"the server is trusted" bypass, and no separate code path.** `input_enabled` and
`input_allowed_sessions` remain `LOCAL_ONLY_KEYS` — a local-operator `settings.json` edit is
still the only way to widen anything.

### 6.2 Evaluated twice, and the second one is the real one

**At enqueue** (`POST …/followups`): reject with 403 and `/input`'s exact status ordering and
detail strings, so the compose bar's existing `_composeErrorMessage()` branches (`app.js`)
apply unchanged. This is UX — the user learns now, not twenty minutes later.

**At fire** (inside the bell handler, step 2 of §5.2): re-evaluate against a fresh
`load_settings()`. `load_settings()` reads from disk on every call (`settings.py:368`), so an
operator who revoked `input_allowed_sessions` between enqueue and fire is honoured. **This is
the fence.** The enqueue check is a courtesy; removing it would be a UX regression, removing
this one would be a security hole.

### 6.3 Fire-time gate order — mirrors `/input` exactly, fails closed

| # | Check | Halt reason on failure |
|---|---|---|
| 1 | `sessions.is_valid_session_name(name)` | `invalid_name` |
| 2 | `settings.get("input_enabled") is not True` (strict `is True`) | `input_disabled` |
| 3 | `input_allowed_for_session(name, settings)` | `input_not_allowed` |
| 4 | `name in get_session_list()` (exact membership; empty cache denies) | `session_missing` |
| 5 | head item is a dict with a non-empty `str` `text` and a `bool` `enter` | `malformed_item` |
| 6 | `run_tmux(...)` raises `RuntimeError` / `OSError` | `send_failed` |

Check 1 exists because `state.json` is hand-editable and this is the only autonomous consumer
of a name from it. Checks 2 and 3 are separate rather than folded together so the halt reason
distinguishes "the operator turned input off globally" from "this session isn't listed" —
the same distinction `/input`'s two 403 messages already draw.

Every halt: item stays at the head, `halted` is set, `logger.warning` fires. **Never skip and
continue. Never discard.**

### 6.4 Audit

One `logger.info` per fire, mirroring `send_session_input`'s line and adding what is unique
to an autonomous write:

```
followup: session=%r id=%r chars=%d enter=%s remaining=%d preview=%r
```

Full text at `debug` only (may contain secrets), same as `/input`. Every halt at `warning`
with reason and detail. Every enqueue/edit/reorder/remove at `info` with session and count —
arming an autonomous writer is itself an auditable act.

### 6.5 The trigger-health interlock

`POST …/followups` returns **409 `{"bell_hook_unarmed": true, "detail": …}`** when
`_bell_hook_armed` is false. Accepting items into a queue whose trigger cannot fire is
precisely the silent uselessness this feature must not have. This depends on P2 (§0.2)
making `_bell_hook_armed` mean what it says; until P2 lands, the interlock is decorative.

`bell_hook_unarmed` joins the established discriminator-flag convention
(`backstop` / `terminal_conflict` / `unknown_command_id` / `invalid_view_rule`) documented in
`docs/API_SEMANTICS.md`.

### 6.6 Fence shared, send duplicated — deliberately

The **fence** is shared and must stay shared: `input_allowed_for_session()`'s own docstring
says two copies of "is this session typeable" is exactly the drift that lets one fence quietly
diverge.

The **send mechanics** (three `run_tmux` calls around `build_send_text_argv` /
`build_send_key_argv`) are duplicated rather than extracted, because the two consumers have
different failure requirements: `/input` returns 500 to a waiting caller, the queue halts a
queue and preserves an item. This is the same trade `AGENTS.md` documents for
`views.matches_name_pattern` vs `terminal_input.session_matches_allowlist` — a handful of
duplicated lines to keep two failure models from sharing a mutable implementation. Both still
call the same argv builders, so the injection-safety properties (`-l` literal, `--`
end-of-options, `create_subprocess_exec` with argv and never a shell) are inherited, not
re-derived.

---

## 7. API surface

Additive under the existing local-session namespace. No existing endpoint changes shape;
`GET /api/sessions` gains one field, which clients already tolerate per `AGENTS.md`.

### 7.1 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/sessions/{name}/followups` | Read the queue |
| `POST` | `/api/sessions/{name}/followups` | Append one item |
| `PUT` | `/api/sessions/{name}/followups` | Replace the whole list — edit + reorder + remove in one |
| `DELETE` | `/api/sessions/{name}/followups` | Clear items **and** any halt |
| `POST` | `/api/sessions/{name}/followups/resume` | Clear the halt only |

All five take `_require_valid_session_name(name)` → 400, and the same fail-closed
`name in get_session_list()` → 404 that `/input` uses. All require auth via the shared
middleware — no second credential (the council already rejected that as theater).

**`GET`** → `{"session": str, "revision": int, "items": [...], "halted": {...}|null}`.
Unknown-but-valid session with no queue returns `revision: 0, items: [], halted: null` — an
empty queue and an absent queue are the same thing to a client.

**`POST`** body `{"text": str, "enter": bool = true}`. Server assigns `id` (uuid4 hex) and
`created_at`. Returns the created item plus the new `revision`. **No precondition** —
appending is commutative and cannot clobber, so the compose bar's "queue it" is one call with
no read-modify-write. Rejections: 403 (fence, §6.2), 409 `bell_hook_unarmed` (§6.5), 413
`text` over `MAX_TEXT_BYTES` (8192, the same constant `/input` uses), 409
`{"queue_full": true}` at `MAX_FOLLOWUPS` (16).

**`PUT`** body `{"expected_revision": int, "items": [{"id"?, "text", "enter"}]}`. **The
precondition is required, not optional.** Mismatch → 409 with the current revision and item
list, no write.

Why one whole-list replace instead of per-item move/edit/delete endpoints: the list is
per-session, at most 16 entries, and has no history to lose. Three narrow endpoints would be
more surface for less safety. The precondition is what makes replace safe, and the repo
already established that discipline for `views` (`expected_settings_updated_at`).

Why the precondition is **required** here when it is optional on `PATCH /api/settings`: the
queue mutates *itself*. A stale `PUT` built from a snapshot taken before a bell fired would
**re-add an item that has already been typed into the session** — an unattended duplicate
write. That is not a lost update; it is a second execution. Optional is not defensible.

An item in the `PUT` body carrying a known `id` keeps that `id` and its `created_at`; an item
with no `id` (or an unknown one) is treated as new. That makes reorder-and-edit expressible
without the client inventing ids.

**`PUT` also returns 409 `{"send_in_flight": true}`** when `name ∈ _followup_sending`. The
window is one `send-keys` exec; the client retries. This closes the last hole in §5.2's
race analysis — without it, a `PUT` landing between peek and remove could resurrect the
in-flight item. The reference client behaviour is `patchSettingsGuarded()`'s (`app.js`):
re-fetch, re-apply, retry once, then render from server truth.

**`DELETE`** and **`resume`** are separate on purpose. `DELETE` throws the work away;
`resume` says "I fixed the thing, carry on with the same list." Nothing else clears a halt —
no implicit unhalt as a side effect of an edit, because a silent unhalt is how an autonomous
writer restarts without anyone deciding it should.

### 7.2 `GET /api/sessions` gains one field

Every entry gains, alongside `bell`:

```jsonc
"followups": { "pending": 3, "halted": false }
```

`{"pending": 0, "halted": false}` when there is no queue. Present on `GET /api/sessions` and
`GET /api/federation/sessions` (where remote entries carry whatever their own host reports,
or the zero value from a pre-feature peer — version tolerance in both directions).

This is deliberate, per `AGENTS.md`'s standing answer: resolve server-side rather than making
each of PWA / sidecar / agents fetch a second endpoint per session to render a badge. A
summary, not the items — the items are one `GET` away when the user opens the session.

`GET /api/view`'s `sessions[]` entries get the same field for the same reason.

### 7.3 Which window it will type into

`GET …/followups` includes `"target_window": "1:amplifier"` — resolved live from
`#{window_index}:#{window_name}` for the session's current window, or `null` if tmux does not
answer. This is the honest surfacing of §0.3. It is display-only; nothing branches on it.

---

## 8. Federation

**A follow-up queue on a remote session does not make sense, and v1 must not offer one.**

Bells are local-only state (`docs/API_SEMANTICS.md`). A remote session's bell is recorded by
the **remote's** poll cycle and its own `alert-bell` hook; the local instance never receives
`POST /api/sessions/{name}/bell` for it. `GET /api/federation/sessions` copies the remote's
`bell` sub-dict into the aggregate as *data*, which is not an event and cannot trigger
anything. A locally-held queue for a remote session would sit there forever.

**Whose server fires it: the host that owns the session.** Three independent arguments
converge, which is the sign the answer is right — that host is the only one that (a) receives
the bell, (b) has the tmux session to type into, and (c) holds the
`input_enabled`/`input_allowed_sessions` fence governing typing into it. Those keys are
`LOCAL_ONLY_KEYS` and never federation-synced precisely so that the decision stays local.

**Therefore:** the endpoints live only under `/api/sessions/{name}/followups` — local sessions
only, matching `GET /api/view`'s and `POST /api/views/preview`'s existing scope. There is no
`/api/federation/{device_id}/sessions/{name}/followups` proxy.

**Why not build the proxy now**, even though the shape is obvious (it would mirror
`POST /api/federation/{device_id}/sessions`, forwarding with a Bearer header so the remote's
own fence applies at the remote): every existing federation proxy forwards a **single,
human-initiated action**. A proxied queue means device A **arms an autonomous writer on device
B**. And `docs/API_SEMANTICS.md`'s closing paragraph documents that there is no way to detect
a peer's patch level short of a version-negotiation mechanism that does not exist — a peer
running a pre-v0.37 muxplex has no `bearer_only` classification and its terminal-WS typing
path is still ungated. Arming an autonomous writer across a trust boundary you cannot
version-check is not a v1 feature. Revisit if and when version negotiation exists.

**Frontend consequence:** when `_viewingRemoteId` is non-empty (`app.js:226`), the queue
affordance is **absent**, not present-and-failing, with one line of copy naming why
("Follow-ups run on the host that owns the session"). A control that is visible and always
403s is the silent-uselessness failure in miniature.

---

## 9. UX

### 9.1 The asymmetry must be on screen, not in the docs

The compose bar's visibility is per-device (`localStorage` key `muxplex-compose-bar`,
`auto|on|off`, `app.js:4011`). **The queue is server-side and shared** — another browser, the
soft deck, muxplex-deck, and any agent with the Bearer key all see and can edit the same list.
A user who assumes the queue is as private as the bar will be surprised in a way that matters,
because the surprise types into a live shell.

The pending-list header therefore reads, always, whenever the list is shown:

> **Follow-ups** · shared with every device · will type into **1:amplifier**

One line carrying both the sharing asymmetry and §0.3's window target. Not a tooltip, not a
help page.

### 9.2 Two buttons, never a mode

The compose bar keeps `#compose-send-btn` (↑, "send now") **byte-identical in behaviour** and
gains a sibling `#compose-queue-btn` (⤓ or similar, "add to follow-ups").

Two explicit buttons, not one button with a toggled mode. The two actions have very different
consequences — one types now into a session the user is looking at, the other arms an
unattended write — and a mode you can forget is a footgun. Keyboard: `Ctrl/Cmd+Enter` stays
send-now (unchanged); `Ctrl/Cmd+Shift+Enter` queues. Both branches call `e.preventDefault()`
before returning, per `AGENTS.md`'s `attachCustomKeyEventHandler` rule — any new branch that
intercepts a key which would otherwise type something must, and `tests/test_terminal.mjs`'s
existing assertion should be extended rather than trusted to review.

The queue button obeys exactly the same `disabled` logic as send
(`_composeRenderEnabledState()`), plus disabled when `_viewingRemoteId` is set (§8) and when
`bell_hook_armed` is false, each with its own specific inline reason. No control is ever
clickable-but-doomed.

Draft handling matches send: cleared **only** on a 2xx. A user who just dictated a paragraph
must not lose it to a 403.

### 9.3 The pending list

Above the compose input, shown only when `items` is non-empty or `halted` is set:

- The header line from §9.1.
- One row per item: ordinal, text (truncated with full text on expand), and controls
  **↑ ↓ ✎ ✕**. All four build a new full list and issue one `PUT` with the current
  `expected_revision`.
- A **Clear all** action → `DELETE`.
- On 409 `{"send_in_flight": true}`: silent single retry after a short delay, then surface.
  On 409 revision mismatch: re-fetch, re-apply, retry once, then render server truth — the
  `patchSettingsGuarded()` pattern.

### 9.4 Halted state

A halted queue is not a passive state; it is the user's to clear.

- The pending list gains a prominent banner: the halt reason in plain language, the offending
  item highlighted at the head, and two actions — **Resume** (`POST …/resume`) and **Remove
  this item** (a `PUT` dropping it, which leaves the halt set so Resume is still explicit).
- Reason copy is specific per §6.3, and for `input_disabled` / `input_not_allowed` it reuses
  `_composeErrorMessage()`'s existing wording, which already names the two settings keys and
  says they are edited in `~/.config/muxplex/settings.json` on the host, not from the UI.

### 9.5 Visibility from outside the session

Session tiles and sidebar rows render a **follow-up badge** from `GET /api/sessions`'
`followups` summary (§7.2) when `pending > 0` or `halted`:

- Normal: a count with a queue glyph (e.g. `⤓3`).
- Halted: an error-styled badge, because a halted queue needs action.

**It must be visually distinct from the bell badge** — different glyph and different colour.
Conflating "needs attention" with "has queued work" would corrupt the attention model the
repo spent several releases getting right, and the two mean opposite things: a bell says
*come look*, a follow-up badge says *something will happen without you*.

Sort order is untouched (see §2, out of scope).

---

## 10. Test plan

`AGENTS.md` is unambiguous: **never run the suite on a host serving muxplex** — a live
instance is on `0.0.0.0:8088` right now. `make test` (DTU), and commit first so
`git archive HEAD` tests the artifact that would ship.

### 10.1 Unit — `muxplex/tests/test_followups.py` (new)

| ID | Asserts |
|---|---|
| T-01 | `normalize_state()` fills `followups` when absent; leaves a populated one untouched. |
| T-02 | Append / replace-with-precondition / clear round-trip through `save_state` + `load_state`. |
| T-03 | `PUT` with a stale `expected_revision` → 409, **no write**, and specifically does **not** resurrect an already-fired item. |
| T-04 | **Seeded-bell isolation.** Queue on a session; run the poll-cycle seeding branch with `session_created >= _server_start_time`; assert bell seeded **and** queue unchanged. |
| T-05 | **Poll-fallback isolation.** `process_bell_flags()` over a `0→1` transition increments `unseen_count` and does **not** advance the queue. |
| T-06 | One `receive_bell()` sends exactly the head item and removes exactly it; `revision` bumps. |
| T-07 | Two concurrent `receive_bell()` calls (`asyncio.gather`) send **exactly one** item. |
| T-08 | A bell inside `FOLLOWUP_SETTLE_SECONDS` of a send does not advance; one after does. |
| T-09 | Fence matrix at fire time — each of `input_enabled=False`, non-matching allowlist, non-list allowlist, `input_enabled="true"` (truthy string) → **halt, item retained, nothing sent**. |
| T-10 | `session_missing`, `invalid_name`, `malformed_item`, and a `run_tmux` `RuntimeError` each halt with the right reason and retain the item. |
| T-11 | A halted queue ignores subsequent bells until `resume`; `resume` restores advancement. |
| T-12 | `_followup_sending` is cleared on the exception path (send raises → next bell is accepted). |
| T-13 | Reaper drops a queue for an absent session **when `probe_tmux_epoch()` returns a dict**, and **preserves every queue when it returns `None`** — the state-wipe hazard of §3.2. |
| T-14 | `POST` → 409 `bell_hook_unarmed` when `_bell_hook_armed` is false; `PUT` → 409 `send_in_flight` while in flight. |
| T-15 | Caps: `MAX_TEXT_BYTES` → 413; the 17th item → 409 `queue_full`. |
| T-16 | Fired items leave no residue anywhere in `state.json` (no `history`, no tombstone, no counter). |
| T-17 | The queue calls `input_allowed_for_session` — patch it to raise and assert the fire path propagates, proving there is no second implementation. |

### 10.2 Federation

| ID | Asserts |
|---|---|
| T-20 | No route matches `/api/federation/{device_id}/sessions/{name}/followups` (assert against `app.routes` — a future addition must be deliberate). |
| T-21 | `GET /api/federation/sessions` merges a pre-feature peer's entries (no `followups` key) without error, defaulting to the zero value. |

### 10.3 Frontend — `muxplex/frontend/tests/test_followups.mjs` (new)

Run with the glob (`node --test tests/*.mjs`), never a single file.

| ID | Asserts |
|---|---|
| T-30 | Queue button disabled with a specific reason for each of: input disabled, remote session, hook unarmed. |
| T-31 | `Ctrl/Cmd+Shift+Enter` queues and calls `preventDefault()`; `Ctrl/Cmd+Enter` still sends now. |
| T-32 | Reorder / edit / remove each emit one `PUT` carrying the current `expected_revision`. |
| T-33 | 409 revision mismatch → re-fetch, re-apply, retry once, then render server truth. |
| T-34 | Halted banner renders the reason and both actions; `resume` calls the resume endpoint. |
| T-35 | The "shared with every device" line and the `target_window` are rendered whenever the list is shown. |
| T-36 | Follow-up badge and bell badge use different classes/glyphs and can coexist on one tile. |
| T-37 | Draft survives a 403 on queue (cleared only on 2xx). |
| — | `test_shared_scope.mjs` covers any new script automatically; new top-level bindings must be prefixed to avoid the v0.31.3 collision class. |

### 10.4 Integration with real tmux — the one that actually matters

`@pytest.mark.integration`, inside the DTU, against an isolated tmux server
(`tmux -L muxq-followup-<pid>` + scratch `TMUX_TMPDIR`, socket-scoped teardown; never
`pkill -f`, never a bare `kill-server`).

**T-40 — a real queue drains across real bells.**

1. Start a muxplex instance in the DTU with `input_enabled: true`,
   `input_allowed_sessions: ["itest-*"]`, on a monkeypatched port, and arm the bell hook
   against that instance's real scheme and port.
2. Create `itest-followup` on the isolated tmux server; run one poll cycle so it is known.
3. Confirm `_bell_hook_armed` **and** that a manual `POST …/bell` lands in the access log —
   §0.1 exists because "armed" did not imply "delivers."
4. Queue three items with distinguishable markers: `MARK_ONE`, `MARK_TWO`, `MARK_THREE`.
5. Fire a real bell: `send-keys -t itest-followup 'printf "\a"' Enter`.
6. Assert, after a settle: `MARK_ONE` present in `capture-pane`, `MARK_TWO`/`MARK_THREE`
   absent, `GET …/followups` shows 2 pending, `revision` bumped.
7. Wait past `FOLLOWUP_SETTLE_SECONDS`, bell again → `MARK_TWO`, 1 pending. Repeat →
   `MARK_THREE`, 0 pending, and `state["followups"]` no longer has the key.
8. Bell a fourth time → no-op, no error, nothing typed.

**T-41 — the attached-client case, i.e. case B end-to-end.** Repeat T-40 with a real client
attached to the session's current window (a PTY running `tmux attach`). Assert
`#{window_bell_flag}` stays `0` throughout **and** the queue still drains. This is the test
that would have caught a poll-path-triggered implementation, and it is the reason the trap in
the brief is a trap.

**T-42 — halt, then resume, with real tmux.** Queue two items; drain one; flip
`input_allowed_sessions` to `[]` on disk; bell. Assert: nothing typed (`capture-pane`
unchanged), queue halted with `input_not_allowed`, item retained. Restore the setting, bell
again → still halted (a halt is not self-clearing). `POST …/resume`, bell → item fires.

**T-43 — restart durability.** Queue two items; stop and restart the DTU instance; assert
both survive with identical ids and order; bell → head fires.

**T-44 — the state-wipe hazard, live.** Queue items in two sessions; kill the isolated tmux
server so `enumerate_sessions()` returns `[]` and `probe_tmux_epoch()` returns `None`; run a
poll cycle; assert **both queues survive**. Restart tmux without the sessions; run a poll
cycle; assert both are reaped with a warning each.

### 10.5 Evidence requirements

Present only when every one of these holds:

1. `make test` green in the DTU, including T-40 through T-44.
2. T-40's transcript shows `MARK_ONE/TWO/THREE` appearing **one per bell, in order**, with
   `capture-pane` output quoted at each step.
3. T-41's transcript shows `window_bell_flag == 0` at every sample while the queue drained.
4. T-42's transcript shows the pane **unchanged** across the denied bell, and the halt reason.
5. `grep -rn "input_enabled\|input_allowed_sessions" muxplex/` shows no new evaluation site —
   only `terminal_input.input_allowed_for_session` and its three callers.
6. The access log from T-40 shows one `POST /api/sessions/itest-followup/bell` per fired
   item — proving the primary path, not the fallback, drove it.
7. `GET /api/instance-info` returns `bell_hook_armed: true` on the **TLS** DTU instance
   (P1/P2 landed).

---

## 11. Complexity budget

New: one top-level state key; one `setdefault`; one reap step reusing an existing probe; one
trigger block inside `receive_bell()`; five additive endpoints; one summary field on two
existing payloads; two in-memory dicts; three constants (`FOLLOWUP_SETTLE_SECONDS`,
`MAX_FOLLOWUPS`, reusing `MAX_TEXT_BYTES`).

Nothing is removed, so the budget is net-positive by design. What keeps it bounded is what is
**not** here: no retry policy, no scheduler, no per-item conditions, no history, no workflow
state machine, no second fence, no federation proxy, no sort-order change. The queue is a
list and an event handler.

---

## 12. Open items for the owner

1. **P1/P2 (§0.2) ship first, as their own PR.** The hook's plaintext URL against a TLS
   listener is a live defect independent of this feature, and it silently degrades the
   attention view — the product's core loop — to the fallback path on every TLS install.
   Recommend fixing it before writing any queue code, and re-verifying with the access-log
   check from §10.5 item 6.

2. **`FOLLOWUP_SETTLE_SECONDS = 2.0` is a judgement call, not a measurement.** Case C
   measured that 600 ms-apart bells fire separately, so *some* window is needed; 2 s is the
   round number above that. If real agent CLIs turn out to bell on receipt with a longer
   delay, this needs to grow — and it should be tuned against observation, not guessed twice.

3. **`AGENTS.md` gains a "Follow-up queue" section** covering: the seeded-bell structural
   exclusion (§4) and why it is invisible, the state-wipe hazard (§3.2), the fence-shared /
   send-duplicated split (§6.6), and the local-only federation scope (§8).
   `docs/API_SEMANTICS.md` gains the endpoint semantics, the required-precondition rationale,
   the `followups` summary field, and `bell_hook_unarmed` / `send_in_flight` / `queue_full` as
   new members of the discriminator convention.

4. **Small pre-existing doc gap noticed in passing:** `frontend/app.js:3980` refers to
   `AGENTS.md`'s "Mobile compose bar" note. No such section exists in `AGENTS.md` at
   `dae811f`. Worth writing, since it is the file that would otherwise explain why
   `docs/plans/2026-08-05-mobile-compose-bar-plan.md`'s proposed `/compose` endpoint was rejected and none of it built.

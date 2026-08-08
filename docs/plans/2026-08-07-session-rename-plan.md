# Session rename — design

**Status:** design only, nothing implemented.
**Verdict:** build it. Not as a one-liner — as a keyspace migration with a
write-ahead journal. Honest size: **~290 lines of production code across five
modules, plus one branch in the poll cycle.** The migration *is* the feature.

---

## 0. Verdict, up front

**Build it.** Three reasons, in order of weight:

1. **The gap is real and the workaround is destructive.** Delete + recreate
   kills a live process tree. `AGENTS.md` opens with "The user's tmux sessions
   are the product. They hold hours of in-flight work and they are **not
   recoverable**." A name that can't be corrected once the work is understood
   forces a choice between a wrong grid and losing the work.

2. **Rename-by-hand already exists and is already silently destructive.**
   Nothing stops an operator (or an agent with a shell) from running `tmux
   rename-session` today. When they do, the next poll cycle **destroys** the
   session's follow-up queue (`main.py:608-616`), **drops** its `created_with`
   record (`manifest.py:356-368`), **resets** its bell, **appends** it to the
   end of `session_order`, and **starts a 24-hour prune clock** on its view
   pins. This design is not only a new feature; it is the fix for a live,
   silent data-loss path.

3. **The one cheaper alternative is worse.** See §12.1 — a display-only
   `session_labels` map is ~40 lines and solves the legible-grid story exactly,
   but it creates **two names for one session, one of which is invisible in the
   UI and governs security** (`input_allowed_sessions` globs and `match_names`
   view rules both key on the real tmux name). That is a worse system than
   either the status quo or a real rename.

**Two conditions on building it**, both load-bearing, neither optional:

- **The write-ahead journal in §6 is not belt-and-braces — it is the only way
  the operation can be correct at all.** `tmux rename-session` is a subprocess,
  and this codebase's established discipline is that subprocesses run *outside*
  `state_lock` (`create_session`, `delete_session`, `_advance_followup_queue`
  all do). Worse, the poll cycle's `save_settings()` and `save_pruning_state()`
  calls (`main.py` steps 13b/14) run outside `state_lock` too — so no lock this
  codebase has protects the settings and pruning keyspaces. A ~2s poll cycle
  *will* interleave with a rename. Without a journal it will interleave
  destructively.
- **Rename must be fenced by `terminal_input.input_allowed_for_session()`, on
  both the old and the new name.** See §10. Unfenced, rename is a
  privilege-acquisition primitive against every glob-keyed fence in the system —
  a fourth door into the RCE surface `AGENTS.md` already documents three doors
  into.

---

## 1. What was verified empirically (tmux 3.4, isolated socket)

Every claim below was run live against a scratch tmux server on an isolated
`TMUX_TMPDIR` + `-L` socket, per `AGENTS.md`'s testing rules. Nothing here is
inferred from documentation.

| Probe | Result |
|---|---|
| `new-session -s 'build.js'` | session is named **`build_js`** |
| `rename-session -t build_js 'deploy.prod'` | **rc=0**, session is named **`deploy_prod`** |
| Charset sweep over muxplex's own allowlist (`[A-Za-z0-9_.-]`) | `-` preserved, `_` preserved, **`.` → `_` is the only mangled character** |
| `a..b` → `a__b`, `.leading` → `_leading`, `trail.` → `trail_` | mangling is per-character, not a normalization |
| `rename-session -t X Y` where `Y` is live | **rc=1**, stderr `duplicate session: Y`, nothing changes |
| `rename-session -t X X` (same name) | rc=0, no-op |
| `#{session_id}` across rename | **stable** (`$0` stays `$0`) |
| `#{session_created}` across rename | **preserved** |
| Attached client across rename | **follows the session** — `#{session_attached}` stays `1`, `list-clients` reports the *new* `client_session` |
| `rename-session -t =X Y` (exact-match target form) | **rc=0 — works.** Unlike `send-keys` pane targets, `=name` is valid here |
| `rename-session -t =X -- Y` (end-of-options) | **rc=0 — accepted** |
| `-t app` with both `app` and `app2` live | renamed `app`, not `app2` — tmux resolves an exact name before prefix-matching |

**Two consequences that shape the whole design:**

- **tmux reports success for a name that didn't happen.** `rename-session`
  returns rc=0 after silently producing `deploy_prod` from `deploy.prod`. Any
  design that trusts the exit code and echoes the *requested* name back to the
  caller is lying. §5.2 is the answer.
- **The correct argv is `tmux rename-session -t =<old> -- <new>`.** Both the
  exact-match `=` target form and the `--` end-of-options separator are accepted
  here, giving rename a *stronger* targeting guarantee than `/input` can
  achieve (`terminal_input.py`'s docstring notes `=name` is invalid for
  `send-keys` pane targets). Run via `create_subprocess_exec` — argv, never a
  shell. **Rename is the first session-lifecycle endpoint with no shell path at
  all**, which removes a whole class of concern that create and delete carry.

---

## 2. The complete keyspace audit

The brief named four. The real count is **eleven that require an explicit
decision**, spread across **four persistence layers**, **two in-memory
modules**, and **one process registry**. Three of the eleven must be
*deliberately left alone*, and two of those three would be a security
regression if migrated.

### 2.1 Tier 1 — MUST migrate (data loss or wrong behavior otherwise)

| # | Keyspace | Location | What breaks if skipped |
|---|---|---|---|
| 1 | `manifest["created_with"][name]` | `sessions.json` | `DELETE` silently falls back to the **default kill command** against a session created by `amplifier-dev`. The mirror image of the 2026-08-05 "looks restored and isn't" incident: looks deleted and isn't. Today the poll cycle pops this on tombstone (`manifest.py:367`), so a hand-rename destroys it within 2s. |
| 2 | `state["followups"][name]` | `state.json` | User-authored autonomous-writer text. Today `reap_stale_queues` (`main.py:608-616`) drops it with a warning. This is the one keyspace the followups design explicitly built a separate reaper for *because* losing it is unacceptable. |
| 3 | `settings["views"][*]["sessions"]` (`device_id:name` pins) | `settings.json` | The product is a phone-glanceable grid organized by views. A renamed session silently leaves every view it was pinned to, then gets pruned for real after `stale_key_grace_hours` (24.0, `settings.py:151`). |
| 4 | `settings["hidden_sessions"]` (`device_id:name`) | `settings.json` | A session the operator deliberately hid becomes visible. Same 24h prune clock. |
| 5 | `manifest["sessions"][name]` (`first_seen_at`, `cwd`) | `sessions.json` | The manifest's stated contract is that "an entry is removed by exactly one thing — observed individual death against a live, identity-matched server." **A rename is not a death.** Letting the tombstone loop handle it makes the file that exists to survive the 2026-07-29 incident lie about what happened, and resets `first_seen_at` to now. |

### 2.2 Tier 2 — MUST migrate (visible degradation, cheap to move)

| # | Keyspace | Location | What breaks if skipped |
|---|---|---|---|
| 6 | `state["sessions"][name]["bell"]` | `state.json` | `unseen_count` → 0, `last_fired_at` → `None`. An agent renaming a session that just belled **silently clears the operator's needs-attention flag** — the one signal this product exists to keep honest. The re-seed path can't recover it either: `created_at` is preserved by tmux, so `created_at >= _server_start_time` is False and the session gets `empty_bell()`. |
| 7 | `state["session_order"]` | `state.json` | User's manual ordering. `main.py:522-526` drops the old name and appends the new one at the end. A list `.index()` replace preserves position. |
| 8 | `pruning.json["first_missed_at"]["device_id:name"]` | `pruning.json` | A stale grace clock for a name that's gone. Harmless on its own, but it is also where a collision-with-orphan lives (§7.2). |
| 9 | `bells._bell_seen[name]` + `followups._followup_last_send_at[name]` | in-memory | `_bell_seen` defaulting to `False` against a stuck window bell flag produces one **spurious needs-attention bell** on the operator's phone. `_followup_last_send_at` resetting collapses the 2s settle window and lets the queue double-fire. Both are two-line dict moves in the modules that own them. |

### 2.3 Tier 3 — MUST NOT migrate (migrating is the bug)

| # | Keyspace | Why it must not move |
|---|---|---|
| 10 | `settings["input_allowed_sessions"]` globs | **Migrating this is a privilege escalation.** These are `LOCAL_ONLY_KEYS` — the RCE fence. If the operator allowlisted `agent-worker-1` and the agent renames itself, the *correct* outcome is that it has typed itself out of the allowlist. Carrying the grant along would let a rename preserve typing authority the operator scoped to a name. Never touch. |
| 11 | `settings["views"][*]["match_names"]` globs | `AGENTS.md`'s standing prohibition: "the server must NEVER materialize a rule match back into `view["sessions"]`. Rules stay rules on disk, forever." A renamed session correctly leaving `agent-*` and correctly joining `auth-*` **is the auto-views feature working.** Rewriting a glob to chase a rename is materialization wearing a different hat. A future contributor *will* want to "fix" this; the answer is no. |
| — | `state["devices"][*]["viewing_session"]` | Client-reported, refreshed every heartbeat. Self-healing within seconds. |
| — | `sessions._session_list` / `_snapshots` / `_activity` / `_created` / `_cwds` | Replaced wholesale every poll cycle. Self-healing within ~2s. |
| — | `_federation_cache` | Remote sessions only, keyed by `device_id`, refreshed per poll. Not local state. |

### 2.4 The one that is neither — `ttyd`

`ttyd._ttyds` is keyed by session name (`ttyd.py:373`) and
`socket_path_for(name)` is a **sha256 hash of the name** baked into the
on-disk socket path (`ttyd.py:308-320`), with the name also written into the
sibling run-record `.json`.

Three options were considered:

- **Leave it alone.** The ttyd's `tmux attach` client *follows the rename*
  (verified, §1), so the old ttyd stays functional and the idle reaper kills it
  within `IDLE_REAP_SECONDS` (60.0, `ttyd.py:117`). **Rejected — this is a
  security hole, not a UX preference.** `WS /terminal/ws?session=<old_name>`
  would still resolve `_ttyds[old_name]` for up to 60s, and
  `input_allowed_for_session()` would be evaluated against the **old** name
  while the bytes land in the **renamed** session. After renaming `scratch-x` →
  `production-db`, a Bearer holder keeps typing into `production-db` through
  the stale key. That reopens the exact fence `AGENTS.md`'s "Sibling 2" incident
  closed.
- **Re-key the registry entry.** Works mechanically, but leaves a live socket
  whose hash does not match its session — an invariant `socket_path_for` and
  `spawn_ttyd`'s collision guard implicitly promise. Invisible and permanent.
- **Kill the old ttyd. ✅** Killing a ttyd never touches the tmux session
  (`ttyd.py` module docstring). The browser's WS drops and reconnects; the
  next `/connect` spawns a correctly-hashed ttyd. Cost: one dropped terminal
  frame, visible and self-healing. Benefit: the stale typing path cannot exist.

**Decision: `kill_ttyd(old_name)`, unconditionally, as the last step after the
migration succeeds.**

### 2.5 Was `session_id` the right answer instead?

`#{session_id}` (`$0`) is stable across rename (verified). Keying every keyspace
on it would make rename free. **Rejected, and not narrowly:**

- `session_id` **does not survive a tmux server death.** A new server restarts
  the counter at `$0`. The manifest and `pending_restore` exist *specifically*
  to survive that event (that is the entire 2026-07-29 fix), so they must
  continue to key on name regardless. The refactor wouldn't even remove the
  problem it's meant to remove.
- View pins are `device_id:name` on the wire, and `API_SEMANTICS.md:183-185`
  documents that clients match by the `":<name>"` suffix. Re-keying is a
  breaking `/api/*` change across muxplex-client, muxplex-deck, and the PWA.

A ~290-line migration beats a schema rewrite that doesn't fully work.

---

## 3. A pre-existing defect this work surfaces

**`POST /api/sessions {"name": "build.js"}` already lies today.**

`spawn_session_command` only re-checks tmux for the session's existence in the
**non-zero exit** branch (`sessions.py:612-624`). With the default template
`tmux new-session -d -s {name}`, rc=0, so the check never runs. The endpoint
returns `{"name": "build.js", "ok": true}` while tmux has `build_js`, and
`set_created_with(manifest, "build.js", ...)` (`main.py:1837-1840`) writes a
record **born orphaned** — one the poll cycle can only garbage-collect on a
cold start (`manifest.py:411-416`).

This is not caused by rename; rename just makes it impossible to ignore. **It
is a separate, separately-shippable fix, and it is a breaking change to the
create path** (a name that is accepted today would start returning 400). Flagged
here for the owner's decision — **not bundled into this plan**.

---

## 4. The endpoint

```
POST /api/sessions/{name}/rename
Body: { "new_name": str }
```

Additive. No existing field or endpoint changes shape. A pre-rename client
never calls it; a pre-rename federation peer never sees it. No version
negotiation needed — the property is structural.

**Deliberately no `command_id`.** Same reasoning `DELETE` uses: it would be a
capability with no use case. Sharper here — rename runs no template at all.

**Success (200):**

```json
{ "ok": true,
  "from": "agent-worker-1",
  "name": "agent-auth-refactor",
  "migrated": { "bell": true, "followups": 3, "view_pins": 2,
                "hidden": false, "created_with": true, "order": true,
                "manifest": true, "pruning": 1 } }
```

`name` is the name **tmux actually has**, read back after the rename — never
the requested name echoed. `migrated` is per-keyspace evidence, not a boolean,
so a caller (and a test) can see exactly what moved.

**Errors** — each carries a discriminator key, per the established convention
(`backstop` / `terminal_conflict` / `unknown_command_id` / `queue_full` / …):

| Condition | Status | Discriminator |
|---|---|---|
| `new_name` fails the charset allowlist, or contains `.` | 400 | `invalid_session_name` (+ `suggested`) |
| `bearer_only` caller, fence denies old **or** new name | 403 | `rename_not_allowed` |
| `{name}` not an exact member of `get_session_list()` | 404 | — |
| Target name is a live session (pre-check **or** tmux rc=1) | 409 | `rename_target_exists` |
| Target name has an orphaned follow-up queue | 409 | `queue_target_conflict` |
| Target name is in `pending_restore` | 409 | `pending_restore_conflict` |
| A follow-up send is in flight for `{name}` | 409 | `rename_send_in_flight` |
| tmux rc=0 but the observed name ≠ requested | 500 | `rename_verification_failed` (+ `observed`) |
| A keyspace write failed mid-migration | 500 | `rename_partial` (+ `journal: true`) |

**Ordering discipline:** the fence is evaluated **before** the existence check,
matching `/input`, so a denied caller never learns whether a non-allowlisted
session exists.

---

## 5. Name handling: reject, don't predict

### 5.1 Reject `.` at the boundary

`SESSION_NAME_RE` (`sessions.py:102`) permits `.`. That permission is the
entire mangling problem, on create *and* rename. Two ways out:

- **Predict** — model `requested.replace(".", "_")`. Hardcodes one tmux
  version's behavior. A wrong prediction produces a wrong collision check.
- **Reject. ✅**

```
400 { "invalid_session_name": true,
      "suggested": "build_js",
      "detail": "tmux 3.4 silently converts '.' to '_' in session names;
                 'build.js' would become 'build_js'. Request 'build_js'
                 explicitly." }
```

Why this is the right answer and not the lazy one:

- It is the **only** option that never reports success for a name that didn't
  happen — the brief's own requirement.
- It requires detecting that mangling *might* happen, not modeling it
  *correctly*. Over-rejecting on a hypothetical tmux that wouldn't mangle costs
  the caller one retry. A wrong prediction costs a silently mis-keyed session.
- With `.` rejected, **requested name == effective name**, which makes the
  collision pre-check exact and eliminates the "requested name doesn't collide
  but the mangled name does" case entirely (e.g. live `a_b`, request `a.b`).

**Implementation:** add `is_tmux_stable_name(name)` in `sessions.py` beside
`is_valid_session_name`. **Do not edit `SESSION_NAME_RE`.** Its docstring is
load-bearing security documentation for `/input`, `/connect`, and `/delete`;
a charset change there deserves its own review, not a ride-along. (Worth
recording for that future review: **no live session can contain a `.` today**,
because tmux mangled it at creation — so tightening the shared regex would
break no existing session, only the create-path acceptance in §3.)

### 5.2 Verify anyway

After `rename-session` returns rc=0, re-enumerate and confirm. If the observed
name ≠ the requested name, tmux mangled something we didn't predict: return
**500 `rename_verification_failed`** carrying the observed name, and complete
the migration against the **observed** name (the tmux session is what it is).
The response tells the truth; the keyspaces stay consistent with reality. Belt
and braces, and the belt is three lines.

---

## 6. Atomicity: a write-ahead rename journal

### 6.1 Why neither pure ordering works

The operation touches: one irreversible subprocess, four independently-atomic
file writes (`state.json`, `sessions.json`, `settings.json`, `pruning.json`),
and one ttyd kill. There is **no cross-file transaction**, and building one is
out of scope by any reading of ruthless simplicity.

- **tmux rename LAST** → a crash before it leaves migrated keys for a session
  that still has the old name. The poll cycle *undoes* the work — silently
  reverting a rename the caller was told succeeded.
- **tmux rename FIRST** → a crash after it leaves keys under the old name for a
  session that now has the new name. The poll cycle *destroys* them — the exact
  silent orphaning this design exists to prevent.

### 6.2 The journal

Write the intent, fsync'd, **before** anything changes:

```json
"rename_in_flight": { "from": "agent-worker-1",
                      "to": "agent-auth-refactor",
                      "at": 1786161416.0 }
```

Stored in the **manifest** (`sessions.json`), because `save_manifest()` already
does the one thing this needs — an explicit `os.fsync` before `os.replace`
(`manifest.py:190-209`), chosen precisely so this file survives an unclean
shutdown. `save_state()` deliberately does not fsync; the journal must.

**The poll cycle honors it.** At step 1b, before `update_manifest()`:

| Journal says | tmux says | Poll cycle does |
|---|---|---|
| `from`→`to` | `to` live, `from` absent | **Completes the migration** (same idempotent function the endpoint calls), clears the journal |
| `from`→`to` | `from` live, `to` absent | The rename never happened. **Clears the journal**, does nothing else |
| `from`→`to` | neither live | Session died mid-rename. Clears the journal; the cold-start / tombstone paths handle the corpse as they always have |
| absent | — | Today's behavior, byte-identical |

**The migration function must be idempotent** — every step is
"move key X to key Y if X exists; leave Y alone if it's already right." It has
to be anyway, since both the endpoint and the poll cycle call it.

### 6.3 The failure contract

- **A write that raises** → stop immediately, do **not** continue to the
  remaining keyspaces, leave the journal in place, return
  `500 {"rename_partial": true, "journal": true, "failed_at": "<keyspace>"}`.
  The poll cycle retries the whole migration within ~2s.
- **A process death mid-sequence** → the journal survives (fsync'd); the next
  poll cycle completes the migration. This matters concretely: `AGENTS.md`
  documents that "something on this box periodically SIGTERMs muxplex," and the
  2026-07-29 incident was a routine restart destroying 44 sessions. The
  "vanishingly unlikely window" argument does not survive contact with this
  repo's own history.
- **`ok: true` is returned if and only if** every keyspace migrated **and** the
  observed tmux name equals the requested name. No silent partial success.

**Answer to "is a partial rename recoverable?"** With the journal: yes,
automatically, within one poll cycle. Without it: no — and the operator would
have no way to know it happened.

---

## 7. Collisions

Four distinct classes. They do not have the same answer, and collapsing them
into "409 on anything" would refuse the most common legitimate case.

### 7.1 Target is a live tmux session → 409

Pre-check against `get_session_list()` for a decent error, **and** treat tmux's
rc=1 (`duplicate session: Y`) as authoritative — a session can appear between
the check and the call. Both, not either.

### 7.2 Target has orphaned state → discriminate per keyspace

Implemented as **one loop over a six-row table**, not six code paths:

| Keyspace holding the target name | Action | Why |
|---|---|---|
| `views[*].sessions` / `hidden_sessions` pin | **Inherit** (set-union add, never append) | A pin is a *human's declared intent about a name*. Reusing the name inherits it — which is already what happens when a session is recreated with a pinned name. Refusing here would reject the single most likely legitimate rename. |
| `followups[new_name]` | **409 `queue_target_conflict`** | User-authored text queued for a *different* session. Firing it into the renamed session is a wrong-target autonomous write — precisely the hazard the followups design was built to prevent. The only keyspace where a stale entry is genuinely dangerous. |
| `manifest["pending_restore"]["sessions"][new_name]` | **409 `pending_restore_conflict`** | A session of that name is queued for restore. Taking the name means `muxplex restore` will later hit "duplicate session" and fail confusingly. One dict lookup buys a clear refusal now instead of a baffling failure later. |
| `manifest["created_with"][new_name]` | **Overwrite** | A stale record for a dead session is guaranteed garbage (the tombstone loop pops it; a survivor only exists via the known cold-start-only leak). We know the truth; write it. |
| `state["sessions"][new_name]["bell"]` | **Overwrite** | Cannot legitimately exist — the poll cycle deletes bell entries for absent sessions every cycle (`main.py:594-596`). If it does, tmux has that session live and §7.1 already fired. |
| `pruning.json first_missed_at["device_id:new_name"]` | **Delete** | A running grace clock for the old occupant. The name is live again, which is exactly the condition that clears the clock. Not a collision. |

### 7.3 Target equals the current name

After the §5.1 rejection this can only mean a literal no-op. tmux returns rc=0.
Return 200 with `"renamed": false` and the unchanged `name`. Nothing migrates.

### 7.4 A follow-up send is in flight

`followups.is_sending(name)` → **409 `rename_send_in_flight`**. Reusing the
existing precondition rather than inventing a second one. This is also what
guarantees `_followup_sending` never contains the name at migration time, so
§2.2 item 9 only has to move `_followup_last_send_at`.

---

## 8. Federation

**Rename needs no propagation mechanism. Eventual convergence via the two
existing channels is correct and already implemented.**

- **Session lists** converge within one poll. `GET /api/federation/sessions`
  re-fetches the remote's `/api/sessions`, which reports the new name
  immediately, and `_federation_cache` is replaced wholesale
  (`main.py:4212`).
- **View pins** converge within one settings-sync interval (~30s). The rename
  writes `settings.json`, bumping `views_updated_at`; the existing LWW sync
  pushes it. **The destructive-write backstop does not fire**: a rename removes
  one member and adds one, a 0% drop against `DESTRUCTIVE_MEMBER_DROP_RATIO`'s
  50% threshold. Worth stating so a reviewer doesn't have to re-derive it.
- **An offline peer** is protected by the existing offline-device guarantee:
  `prune_stale_keys` never prunes keys for a device it has no current knowledge
  of. On return, LWW applies — and if that peer edited its own views while
  offline, one side loses. **That is pre-existing LWW behavior for every
  settings write, not a rename-specific problem.**
- **A pre-rename peer** is unaffected: it never sees the endpoint, and the
  settings it receives are just settings.

**Explicitly out of scope: `POST /api/federation/{device_id}/sessions/{name}/rename`.**
Same reasoning that kept the followups queue and `POST /api/focus` local-only —
a proxy with no consumer and real failure modes (peer unreachable, peer
pre-feature, no version-negotiation mechanism in this codebase). If cross-host
rename is ever wanted, the targeted-proxy shape mirroring `federation_bell_clear`
is the correct Phase 2; a broadcast is not.

---

## 9. Restore

### 9.1 The good news: migration makes restore work

With `created_with` migrated (§2.1 item 1) and the manifest entry moved
(item 5), a `tmux kill-server` freezes the renamed session into
`pending_restore` under its new name, carrying its last-observed `cwd`.
`muxplex restore` then resolves the correct pair and recreates it.

**And if the migration were skipped, the existing fidelity check catches it.**
`get_created_with()` returns `None` → `_check_unrecorded_restore_fidelity`
(`restore.py:127-160`) compares the observed cwd (`~/dev/agent-worker-1`)
against `_default_workspace_root()/agent-auth-refactor` → mismatch → **refuses
with an actionable reason** instead of creating a directory that never existed.
The 2026-08-05 fix protects rename for free.

### 9.2 The bad news: migration introduces a *new* restore hazard

Renaming a tmux session **moves nothing on disk**. The session's real working
directory is still `~/dev/agent-worker-1`. After a faithful migration:

- `created_with["agent-auth-refactor"]` = the recorded pair
- restore runs `amplifier-workspace agent-auth-refactor`
- → creates `~/dev/agent-auth-refactor`, a directory that never existed
- → the session comes back rooted in the wrong place, and **the dashboard shows
  it green**

That is the 2026-08-05 incident verbatim, reintroduced by the very migration
that was supposed to be the careful thing to do. `_check_unrecorded_restore_fidelity`
does not fire, because it only runs when `recorded is None`
(`restore.py:338-348`).

### 9.3 The fix

**Record the rename in the manifest entry and refuse on it.**

```json
"sessions": { "agent-auth-refactor": {
    "first_seen_at": …, "last_seen_at": …, "cwd": "/home/b/dev/agent-worker-1",
    "renamed_from": "agent-worker-1" } }
```

The field freezes into `pending_restore` along with everything else. `restore.py`
gains one condition: **a pending session carrying `renamed_from` whose resolved
pair templates `{name}` is a FAIL, not a warn**, with a message naming the
observed cwd and the directory the pair would create. `muxplex restore --force`
(which already exists, `cli.py:1772-1868`) is the escape hatch.

**Refuse, not warn** — decisively. The existing refusal exists because two
daemons came back wrong and the dashboard showed green. A rename is *positive
evidence* that the name no longer describes the directory, which makes a
renamed session strictly more likely to be in that state than the unrecorded
sessions the check already refuses. Warning would be inconsistent with the
precedent the repo set after paying for it.

**Deliberately not chosen:** widening `_check_unrecorded_restore_fidelity` to
all default-pair sessions. Broader blast radius, could newly refuse restores
that work today, and it isn't needed — `renamed_from` targets exactly the
sessions that acquired the problem.

---

## 10. Who can call it

Apply the `DELETE` scrutiny ("a capability with no use case") and the
`/api/focus` scrutiny ("there is no field to add for flexibility").

### 10.1 The finding: rename is a privilege-acquisition primitive

Every glob-based fence in muxplex keys on the session **name**. Rename changes
the name. Therefore, unfenced, rename grants authority:

- With `input_allowed_sessions: ["scratch-*"]`, a Bearer holder renames
  `production-db` → `scratch-anything`, then types into it via `/input`.
  **Full RCE against a session the operator deliberately fenced out.**
- With `views[].match_names: ["agent-*"]`, rename injects any session into any
  rule-based view. Display-only, low stakes — but the same mechanism.

Rename over Bearer is `/input` with extra steps, against the exact fence
`AGENTS.md` documents three doors into. Shipping it unfenced would be a direct
repeat of the Sibling 1 / Sibling 2 incidents.

### 10.2 The resolution — not "operator-only"

Operator-only would kill the motivating use case (an agent renaming its own
session). Instead:

**Rename becomes the fourth caller of `terminal_input.input_allowed_for_session()`
— no bypass, no separate implementation — evaluated against BOTH names, and
only for `bearer_only` callers.**

| Caller class | Requirement |
|---|---|
| localhost | unfenced |
| valid `muxplex_session` cookie (the human at their own PWA) | unfenced |
| `bearer_only` (Bearer credential, no valid cookie) | `input_enabled` is `true` **and** the allowlist permits **both** `{name}` and `new_name` |

Reasoning for each half of the both-names rule:

- **Old name required** → proves the caller already held typing authority over
  that session. No new authority over sessions it could not already touch.
- **New name required** → prevents renaming *into* an allowlisted family to
  acquire authority. (Renaming *out* of one only reduces the caller's own
  authority, which is harmless.)

**The motivating use case still works.** With `input_allowed_sessions:
["agent-*"]`, `agent-worker-1` → `agent-auth-refactor` passes both checks.
`production-db` → `agent-pwn` fails the old-name check. `agent-worker-1` →
`scratch-x` fails the new-name check — conservative, and the agent simply picks
a name in its own family.

The `bearer_only` narrowing follows the terminal-WS precedent exactly
(`WSAuth`, `_ws_auth_check`): a cookie always wins classification when both
credentials are present, because forging a cookie requires `_auth_secret`,
which a Bearer holder does not have. Gating the human's own PWA rename button
on `input_enabled` would mean an operator cannot rename their own session
unless they have opened the RCE fence — absurd, and not what the fence is for.

### 10.3 Audit

One `logger.info` per accepted rename (`from`, `to`, caller classification,
per-keyspace migration counts). Rejections at `warning`. Matching `/input`'s
discipline. There is no secret in a session name, so no redaction is needed.

---

## 11. Execution order

```
 1. Validate new_name           — charset + is_tmux_stable_name        → 400
 2. Fence (bearer_only)         — input_allowed_for_session(old, new)  → 403
 3. Exact membership            — name in get_session_list()           → 404
 4. Collision pre-flight        — §7 table over all keyspaces          → 409
 5. Send-in-flight check        — followups.is_sending(name)           → 409
 ── nothing has changed yet; every refusal above is free ──
 6. Write journal               — save_manifest(), fsync'd
 7. tmux rename-session -t =<old> -- <new>   (argv, no shell)          → 409 on rc=1
 8. Re-enumerate and verify observed name                              → 500 on mismatch
 9. migrate_session_name(old, observed)      — idempotent, all keyspaces
10. kill_ttyd(old)                            — §2.4
11. Clear the journal          — save_manifest()
12. 200 with per-keyspace migration evidence
```

Steps 1–5 are where the overwhelming majority of failures live, and they cost
nothing to fail. That is what makes steps 6–11 tractable.

---

## 12. Deliberately not done

### 12.1 A display-only label instead of a rename

`settings["session_labels"]["device_id:name"] = "human label"` — ~40 lines,
migrates nothing, collides with nothing, needs no journal, and solves the
brief's literal story ("so the owner's phone grid is legible") exactly.

**Rejected**, and this deserves to be a considered rejection rather than an
omission. A label creates **two names for one session, one of which is
invisible in the UI and governs security**: the grid would read
`agent-auth-refactor` while `input_allowed_sessions` and `match_names` both
still see `agent-worker-1`. The auto-views feature's entire premise is "name
your sessions `amplifier-*` and they auto-join the view" — a label that diverges
from the name breaks that premise silently. Worse than the status quo.

If the owner's requirement really is only "legible grid," the label is the
right build and this whole plan is over-engineering. If the requirement is "the
name is wrong," it is the rename. **This is the one genuinely open product
question in this document** (§14).

### 12.2 Everything else

- **`command_id` on the endpoint** — no template is run; nothing to select.
- **A federation rename proxy** — §8.
- **Re-keying anything on `session_id`** — §2.5.
- **Migrating `input_allowed_sessions` or `match_names`** — §2.3. The first
  would be a privilege escalation; the second violates a standing prohibition.
- **Fixing the create-path mangling bug in this PR** — §3. Real, separate,
  and breaking.
- **A frontend rename affordance** — API first, frontend second, per
  `AGENTS.md`. Out of scope here.

---

## 13. Size

| Component | Lines |
|---|---|
| `sessions.is_tmux_stable_name()` + `rename_tmux_session()` | ~40 |
| `migrate_session_name()` — all keyspaces, idempotent, incl. the §7.2 table | ~110 |
| `manifest` journal write/read/clear + `renamed_from` | ~30 |
| Poll-cycle journal-completion branch | ~20 |
| `restore.py` `renamed_from` refusal | ~15 |
| Endpoint in `main.py` | ~90 |
| **Production total** | **~305** |
| Tests | ~300+ |

The brief anticipated "genuinely 200 lines." The honest number is ~305, of
which ~50 is the journal. **The migration is the feature**, and there is no
version of this that is smaller without leaving a keyspace behind.

---

## 14. Tests the implementation must carry

Regression guards, not incidental assertions:

1. **Every Tier 1/2 keyspace migrates** — one assertion per row of §2.1/§2.2,
   named after the keyspace.
2. **`input_allowed_sessions` is NOT migrated** — asserts the escalation in
   §10.1 is closed. This is the security test; it must be impossible to delete
   quietly.
3. **`match_names` is NOT rewritten** — guards `AGENTS.md`'s standing
   prohibition.
4. **`.` is rejected with a `suggested` name** — and a companion test asserting
   tmux 3.4's `.`→`_` behavior directly, so the day tmux changes, the *reason*
   test fails rather than the *policy* test silently passing for a new reason.
5. **Journal completion** — write a journal, rename in tmux out-of-band, run
   one poll cycle, assert every keyspace converged and the journal cleared.
6. **Journal reversion** — write a journal, do *not* rename in tmux, run one
   poll cycle, assert the journal cleared and nothing moved.
7. **Partial-write failure** — make one keyspace write raise; assert 500
   `rename_partial`, the journal survives, and the next poll cycle completes it.
8. **Each of the four 409 classes** fires with its own discriminator.
9. **The old ttyd is killed** — asserts §2.4's stale-typing-path hole is closed.
10. **The destructive-write backstop does not fire on a 1-for-1 pin swap.**

All tmux-touching tests inherit `conftest.py`'s autouse `_isolate_tmux_socket_dir`
and layer their own `-L` socket, per `AGENTS.md`.

---

## 15. Open decisions for the owner

Three, and only the first is a genuine fork:

1. **§12.1 — rename or label?** If the requirement is a legible grid, the label
   is ~40 lines and this plan is over-built. If the requirement is that the name
   is wrong, it is the rename. Recommendation: **rename**, because the name
   governs view rules and the input fence, so a divergent label is a worse
   system than either alternative.
2. **§3 — fix the create-path mangling bug?** Real and separate. It is a
   breaking change to `POST /api/sessions`. Recommendation: **yes, separately**,
   after this lands, so the rejection helper already exists and is proven.
3. **§6 — ship without the journal?** Recommendation: **no.** It is ~50 lines
   and it is the only reason the operation is correct at all under the
   established no-subprocess-under-`state_lock` discipline (§0). Shipping the
   migration without it would produce silent partial renames on a host whose own
   documentation says the process gets SIGTERMed unpredictably.

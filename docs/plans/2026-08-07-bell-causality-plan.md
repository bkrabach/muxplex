# Bell causality — design

**Status:** design, not committed. Written against `main` @ `85a8571` (v0.42.0 + the
`_require_valid_session_name` guard on `POST /api/sessions/{name}/bell`).

**The gap this addresses.** An agent polling muxplex sees `bell.unseen_count` and
`bell.last_fired_at`. It learns *that* a session belled. It cannot learn *why* —
whether the agent in that pane finished a turn, hit an error, is waiting on
approval, or crashed. Triage means regexing pane text, which is exactly the
fragile thing a control plane should remove.

---

## §0. Recommendation, up front

**Do not build a `reason` field yet.** The reason field that the gap description
implies — an agent-supplied, free-or-enumerated string explaining a bell — is
unbuildable on the path that produces the overwhelming majority of bells, and
belongs to a different concept (session *status*) with a different lifecycle than
the one it would be bolted onto. Both of those are argued below with evidence,
not asserted.

**Build the two cheap things instead.** Both are server-side facts muxplex
already holds, both are purely additive, neither requires an agent to cooperate,
neither adds a body to `POST /api/sessions/{name}/bell`, and neither touches
`needs_attention()`:

| # | Change | What it buys |
|---|---|---|
| **1** | `bell.source` — a small closed enum on the existing `bell` sub-dict recording **which detection path** recorded the last bell (`hook` / `poll` / `seeded` / `halt`) | Lets an agent discard muxplex's own manufactured bells, know when `unseen_count` is a floor rather than a count, and — via `halt` — get a real, already-enumerated cause |
| **2** | **Fire a bell when the follow-up queue halts** | Closes a real hole: muxplex's own autonomous writer currently fails *silently to the human*. A halt is a specific, knowable, already-modeled reason a session needs attention, and today it rings nothing |

Together these get most of the practical value at a fraction of the surface,
because **the one bell class whose cause muxplex genuinely knows is the one it
causes itself** — and #2 creates that class while #1 labels it.

**Phase 2 (`PUT /api/sessions/{name}/status`) is specified in §7 and deliberately
not scheduled.** Its trigger condition is named there: a *second, independent*
consumer that needs it. Building it now would ship an agent-owned mutable state
surface ahead of any agent that writes to it.

---

## §1. The three hard problems, answered

### §1.1 Problem 1 — the primary path cannot carry a payload

**Answer: correct for *intent*, and this cannot be solved. Partially wrong for
*context*, and the context it can carry must still not be shipped.** Verified
empirically rather than reasoned; methodology in §8.

The `alert-bell` hook (`main.py:433-440`) fires:

```
run-shell 'curl -sfo /dev/null -X POST http://127.0.0.1:PORT/api/sessions/#{session_name}/bell 2>/dev/null || true'
```

Four findings, each from a live isolated tmux 3.4 server:

| # | Question | Result |
|---|---|---|
| **F1** | Does the hook resolve the **belling** window or the session's active one? | **The belling window.** Bell fired in inactive window 2 → hook logged `W=2 WN=win-one ACTIVE=0`. The `display-message -t <session>` trap documented in `bells.poll_bell_flag()` does **not** apply to the hook. |
| **F2** | Do pane-level format vars resolve the **belling pane**? | **No — they resolve the window's *active* pane.** Bell fired in non-active pane `%2` after `cd /etc`; hook logged `PID=%1 PPATH=/home/bkrabach/dev/muxplex-qol-updates`. **Confidently wrong**, in the common split-pane layout. |
| **F3** | Can a program in the pane encode a payload the hook can read? | **Yes, via OSC 2.** `printf '\033]2;muxplex:awaiting_approval\007'` then `printf '\a'` → hook logged `PTITLE=muxplex:awaiting_approval`. (tmux's own `\ek…\e\\` window-rename escape does **not** work — `allow-rename` is off by default.) |
| **F4** | Is that payload safe? | **No — it is sticky.** Two subsequent, unrelated bare `printf '\a'` calls in the same pane both logged the *same* stale `PTITLE=muxplex:awaiting_approval`. |

What this means, in order:

**(a) Intent is unrecoverable, permanently.** `printf '\a'` is one byte with zero
payload bits. Nothing downstream can decode what was never encoded. This is
information-theoretic, not an implementation gap, and no amount of hook cleverness
changes it. **Any `reason` field on the hook path is `null`. Always. Forever.**
The gap description's own standard applies and settles it: *a field that's empty
in the common case is worse than no field.*

**(b) The pane-scoped context the hook *can* carry must not be shipped.** F2 shows
`pane_current_command` / `pane_current_path` / `pane_id` are the **active** pane's,
not the belling pane's, whenever the window has a split. A `POST /bell?cmd=bash&cwd=/home/…`
would be silently, confidently wrong in exactly the layouts people use — the same
class of bug as the `display-message -t <session>` incident already recorded in
`bells.py:45-57`, re-introduced on a new field. Wrong is worse than absent.

**(c) The OSC-2 side channel works and must not be used.** F3 proves it, F4 kills
it: the marker outlives its bell and is inherited by the next two unrelated ones.
That is not a bug to fix — a pane title is durable state, a bell is an event, and
the mismatch is structural. It also only works when the belling pane is the
window's active pane (F2), it hijacks a user-visible field, and — decisively — it
requires the agent to do compose-time work anyway. **An agent that can compose an
OSC-2 marker can compose a `curl` instead**, which is explicit, non-sticky, and
does not make muxplex the owner of a tmux-title convention.

**(d) The one thing the hook could honestly add is `#{window_index}` — and it is
still not worth it.** F1 proves window-scoped attribution is correct, and
"the `amplifier` window belled, not the `shell` window" is real triage on the
4-window `amplifier-workspace` layout this repo's own recovery docs describe. But:
`#{window_name}` is arbitrary user text going into a URL (encoding hazard in a
string built by `_bell_hook_curl()`), and index-without-name needs a server-side
tmux round trip at bell time to be legible. Both changes touch **the most
incident-prone string in the codebase** — two separate pane-painting incidents
live in `AGENTS.md:552-598` — for a marginal signal with no consumer asking.
**Rejected for v1**; recorded here so the evidence exists if a consumer ever
appears, and so nobody re-derives F1 from scratch.

### §1.2 Problem 2 — `POST /bell` is the one autonomous writer

**Answer: adding a body is survivable, but the incentive it creates is not. Put
the field elsewhere.**

`receive_bell()` (`main.py:2566-2603`) does three things in order: validate the
name (85a8571), increment the bell under `state_lock`, then — outside the lock —
`await _advance_followup_queue(name)`, which **types operator-authored text into a
live pane**. The endpoint carries no typing content itself; it is an RCE trigger
*by composition*. Everything that makes it safe lives inside
`_advance_followup_queue`: fresh `input_enabled`, `input_allowed_for_session()`,
exact `get_session_list()` membership, and the peek/settle/in-flight guards.

Two distinct hazards, only one of which is obvious:

**Hazard A — a body that influences the advance.** Any field of the shape
`{"advance": false}` / `{"suppress_followup": true}` hands a Bearer caller a
control primitive over the autonomous writer that **does not exist today**.
Today a queue can only be stopped by an explicit, revisioned `PUT`/`DELETE`, or
by a halt, which is recorded in state with a reason. A bell-body suppression flag
would let any caller silently starve a queue with no record anywhere.
**Standing rule for any future work here: the advance must remain a pure function
of "a bell was accepted for this session." No request field may ever be read by
the advance path.**

**Hazard B — the field makes the endpoint attractive to call, and every call
drains a queue.** This is the non-obvious one and it is the reason to say no.
Today an agent has almost no motive to POST `/bell` directly; the tmux hook does
it. Ship *"POST your bell with a reason so the human knows what you need"* and
agents will start firing bells they would never otherwise have fired — status
heartbeats, progress notes, phase transitions. **Every one of those advances the
follow-up queue by one item.** An agent politely reporting itself every 30 seconds
drains a 16-item queue (`MAX_FOLLOWUPS`) in eight minutes, typing eight minutes of
operator-authored text into a pane nobody asked it to. A pure-observability
feature would have become a queue-drain trigger, and the failure would look like
the queue misbehaving rather than the new field.

There is no good mitigation inside `POST /bell`. Making the advance conditional
reopens Hazard A. Documenting "please don't" is not a mechanism.

**Conclusion: whatever carries a reason must not be `POST /api/sessions/{name}/bell`.**
Note that §1.3 reaches the same conclusion from a completely independent
direction. Two unrelated arguments converging is the strongest signal in this
document.

### §1.3 Problem 3 — does this belong on the bell at all?

**Answer: no. What is being asked for is *session status*, which correlates with
the bell and is not the bell.** Three arguments, most decisive first.

**(1) Lifecycle mismatch → guaranteed staleness.** A bell's lifecycle is *unseen
until a human looks*: `apply_bell_clear_rule()` zeroes `unseen_count` and stamps
`seen_at` the moment any device views the session fullscreen within 60s. A
*condition* — "the agent is waiting for approval" — survives being looked at. Put
`reason` on the bell dict and you must choose between two wrong outcomes:

- clear it with the bell → destroy true information because someone glanced at
  the grid; or
- keep it → `{"unseen_count": 0, "reason": "awaiting_approval"}`, a reason
  attached to a notification nobody is being notified about.

This failure is not hypothetical; **F4 in §1.1 is the same failure observed one
layer down** — a durable marker outliving its event and being inherited by
subsequent unrelated ones. The tmux layer already demonstrates what happens when
state is attached to an event channel.

**(2) Cardinality mismatch.** One condition produces many bells (an agent that
retries and rings each time). Many conditions produce one bell (a job fails *and*
the queue halts). `unseen_count` is an event counter; a reason is a scalar state.
A scalar on a counter means every increment silently overwrites the previous
value, and there is no defined answer to "which of these 5 unseen bells does this
reason describe?"

**(3) The scarce-channel argument points the other way.** `AGENT_GUIDE.md:993-1002`
is explicit: ring on nonzero exit **only**, because the bell is a scarce
human-attention channel and routine success must not compete with it. That is a
design premise that *most conditions deliberately do not ring*. The bell is,
by construction, a lossy sample of session state. Building the causality channel
on top of the intentionally-lossy one inherits its lossiness for nothing.

**But "it is a different concept" is not "build the different concept."** A
`session_status` surface is a new agent-owned mutable per-session state with its
own unanswered lifecycle questions (who clears it, does it survive session death,
does it federate, does it persist across restart, what happens when two agents
write it). That is a lot of new surface for a problem where — see §2 — one case is
already solved end-to-end, one is unsolvable in principle, and one is already
modeled. §7 specifies it; §7.5 names the trigger that should unlock it.

---

## §2. Decomposing "why does this session need attention"

The single question hides four, with very different answers:

| Case | Asked by | Status today |
|---|---|---|
| **A. "Did the job *I* started finish, and how?"** | An agent that typed the command | **Already solved.** Completion sentinel + `GET /api/sessions/{name}?lines=N` (`AGENT_GUIDE.md` §6.2/§6.3), proven with traces, recovers the real exit code. The agent composed the command; nothing muxplex adds beats this. |
| **B. "Why did *that other* session bell?"** | An agent triaging a session it did not start | **Unsolvable in principle** — see §1.1(a). The information exists only in the pane text. muxplex regexing that text on the agent's behalf would be *the same fragile thing, moved server-side and blessed as a contract* — worse, because muxplex would then own a heuristic it cannot keep correct across every agent CLI that will ever run in a pane. |
| **C. "Is this bell even real?"** | Any poller | **Unsolved, cheap, 100% server-side.** Four bell classes exist and are indistinguishable on the wire. → **fixed by Phase 1a (§4).** |
| **D. "Is this session blocked in a way muxplex itself caused?"** | Any poller | **Already modeled** (`followups.halted`) — but invisible to the human. → **completed by Phase 1b (§5).** |

Case C is worth spelling out, because it is the concrete false positive an agent
hits today. Four things write a bell, and all four look identical:

| Writer | Site | What actually happened |
|---|---|---|
| tmux `alert-bell` hook | `receive_bell()` `main.py:2566` | A real BEL byte reached a real pane |
| `window_bell_flag` poll fallback | `bells.process_bell_flags()` 0→1 branch | A real BEL, session detached. Flag is boolean and can stick — **only the first bell is ever counted** (`AGENTS.md:544-548`) |
| muxplex itself, for a new session | `_run_poll_cycle()` step 5, `main.py:585-589` | **Nothing happened in the pane at all.** Manufactured so a just-created session sorts to the top |
| a Bearer caller | `receive_bell()`, same route | Someone asserted a bell |

The third is the common spurious one — it fires for *every* session created while
muxplex is running — and an agent has no way to skip it.

---

## §3. What Phase 1 deliberately does **not** change

These are contract invariants; a Phase-1 implementation that touches any of them
has gone wrong.

- **`needs_attention()` stays exactly `unseen_count > 0 and (seen_at is None or
  last_fired_at > seen_at)`.** It is ported into `muxplex_client.Bell.needs_attention`
  and contract-tested against a truth table (`test_client_contract.py:319-353`);
  `muxplex-deck`'s amber ring and `?sort=attention`'s tier 1 both ride on it.
  **`bell.source` must never be read by this predicate.**
- **`POST /api/sessions/{name}/bell` gains no request body** (§1.2).
- **`_advance_followup_queue()`'s trigger set is unchanged**: `receive_bell()`
  always, `process_bell_flags()` only while `_bell_hook_armed` is False. Phase 1b
  writes a bell *without* becoming a third trigger — see §5.2.
- **Nothing renders to a pane.** Both changes are pure `state.json` writes. No
  tmux call, no `run-shell`, no new curl. `test_safety_rails.py`'s
  `test_no_diagnostic_tmux_run_shell_construction_exists` must stay green
  unmodified.
- **The hook string (`_bell_hook_curl()`) is not touched** (§1.1(d)).

---

## §4. Phase 1a — `bell.source`

### §4.1 Contract

`empty_bell()` (`state.py:153`) gains one key:

```python
{"last_fired_at": None, "seen_at": None, "unseen_count": 0, "source": None}
```

`source` is a closed enum, `str | None`:

| Value | Written by | Means |
|---|---|---|
| `"hook"` | `receive_bell()` | `POST /bell` was called — normally by tmux's `alert-bell` hook |
| `"poll"` | `bells.process_bell_flags()` 0→1 branch | muxplex observed a `window_bell_flag` transition itself |
| `"seeded"` | `_run_poll_cycle()` step 5 seed branch | **muxplex manufactured this bell**; nothing happened in the pane |
| `"halt"` | Phase 1b (§5) | muxplex's own follow-up queue halted on this session |
| `null` | `empty_bell()`, and any pre-feature `state.json` entry | No bell has fired, or this server/state predates the field |

**Honest contract, stated the way `bell_hook_armed` states its own** (`AGENTS.md:518`):
`"hook"` means *the endpoint was called*. muxplex cannot distinguish tmux's hook
from a direct Bearer POST — both arrive at the same route, both may come from
`127.0.0.1` — and **does not claim to**. Keep the legible name; document the
weaker truth. Do not "fix" this by adding `?source=hook` to the hook string
(§1.1(d), §3).

### §4.2 Why this field is honest where `reason` is not

`source` pairs with `last_fired_at` — it describes the *same event*, written in
the *same update*, and is therefore exactly as fresh as the field beside it. It is
never null when a bell has fired, never stale, and requires no agent cooperation.
That is precisely the test `reason` fails in §1.3(1). Consistency check:
acknowledgment (`clear_bell()`, `apply_bell_clear_rule()`) does **not** clear
`last_fired_at`, so it must **not** clear `source` either.

### §4.3 What an agent does with it

- `source == "seeded"` → **skip.** There is nothing to triage. This is the single
  largest source of false-positive attention in the system today.
- `source == "poll"` → `unseen_count` is a **floor, not a count** (the tmux flag
  is boolean and can stick — `AGENTS.md:544-548`). Do not treat repeat counts as
  meaningful.
- `source == "halt"` → **a real, enumerated cause is available now.** Fetch
  `GET /api/sessions/{name}/followups` and read `halted.reason`, which is already
  a closed vocabulary (`input_disabled` / `input_not_allowed` / `session_missing`
  / `send_failed`) plus free-text `detail`. **This is bell causality, delivered
  without inventing a vocabulary** — see §6.2.
- `source == "hook"` → a real BEL reached a real pane. Read the snapshot if you
  care; muxplex knows no more than you do (§1.1(a)).

### §4.4 Implementation notes

Four one-line writes, at the four sites in §4.1's table. `receive_bell()` and
`process_bell_flags()` set it in the same statement group that already sets
`last_fired_at`. The seed branch (`main.py:585-589`) writes a literal dict — add
the key there.

Readers must use `bell.get("source")`: a `state.json` written by a pre-feature
build has no key, and `load_state()` does not migrate. No migration is warranted —
`null` is a correct answer for an unknown provenance.

### §4.5 Contract surface

`bell` appears on `GET /api/sessions`, `GET /api/sessions/{name}`, `GET /api/view`,
and `GET /api/federation/sessions`. All four gain the key for free; no endpoint
code changes. Federation stays version-tolerant in both directions — bells are
local-only state (`AGENTS.md:274-277`), a pre-feature peer's entry simply lacks
the key, exactly like the `followups` summary before it.

`muxplex_client.models.Bell` gains `source: str | None = None`, **defaulted and
last**, so every existing construction site keeps compiling and a pre-feature
server parses cleanly — the same treatment `views` and `created_at` already got
(`client/muxplex_client/models.py`). Client and server versions move in lockstep
(`test_client_contract.py`'s `test_client_version_matches_server_version`): the
field lands in both at one version or in neither.

---

## §5. Phase 1b — bell on follow-up halt

### §5.1 The hole

`_advance_followup_queue()`'s failure branch (`main.py:2343-2348`) calls
`followups.set_halted()` and logs a warning. **It rings nothing.** So a halted
queue is a genuine needs-attention condition that `needs_attention()` cannot see:

- An agent polling `GET /api/view` *can* see `followups.halted: true`.
- **The human staring at the phone grid sees nothing.** No bell, no tier-1 sort,
  no amber ring.

This is the exact inverse of the stated gap — not "a bell without a reason" but
"a reason without a bell" — and it sits inside the feature `AGENTS.md` calls
*muxplex's first autonomous write*. A halt means operator-authored text will never
be typed until a human runs `POST .../followups/resume`. It will not resolve
itself. It is precisely the outcome `AGENT_GUIDE.md:1002` says to reserve the
bell for.

### §5.2 The change

In the halt branch, inside the `state_lock` block already open there, write the
bell **directly** — never through `receive_bell()` or `process_bell_flags()`:

```python
followups.set_halted(state, name, halt_reason, halt_detail, item["id"])
_log.warning("followups: halted for %r -- %s: %s", name, halt_reason, halt_detail)
_bell_for_halt(state, name)          # direct write; see below
save_state(state)
```

```python
def _bell_for_halt(state: dict, name: str) -> None:
    """Ring session *name*'s bell because its follow-up queue just halted.

    Writes state["sessions"][name]["bell"] DIRECTLY, never via receive_bell()
    or process_bell_flags() -- the queue's advance hangs off exactly those two
    functions, so routing this through either would make the queue trigger
    itself. The exclusion is structural (a property of where this code lives),
    identical in kind and rationale to the seeded-bell exclusion documented in
    AGENTS.md's "Follow-up queue" section. Do not route it through either
    "for consistency."
    """
    session = state.setdefault("sessions", {}).setdefault(name, {})
    bell = session.setdefault("bell", empty_bell())
    bell["unseen_count"] = bell.get("unseen_count", 0) + 1
    bell["last_fired_at"] = time.time()
    bell["source"] = "halt"
```

### §5.3 Why this cannot loop

Two independent guards, and both must be stated in the docstring and covered by a
test — a self-triggering autonomous writer would be a serious bug:

1. **Structural.** `_bell_for_halt()` is a plain state write. Nothing calls
   `_advance_followup_queue()` from it. This is the same structural exclusion that
   keeps the seeded bell from draining a queue.
2. **Behavioral.** Even if a *later* real bell arrives, `followups.acceptance_ok()`
   returns `False` while `entry["halted"] is not None` (`followups.py:266`). A
   halted queue cannot advance at all until someone explicitly resumes it.

It also fires **once per halt transition, not once per poll**: `set_halted()` has
exactly one production call site (`main.py:2344`), reached only from the advance
path, which itself only runs on a bell. Once halted, `acceptance_ok()` is False,
so no further advance, so no further halt, so no further bell.

### §5.4 Interaction with acknowledgment — this is correct, not a bug

When a human views the session, `apply_bell_clear_rule()` zeroes the bell while
`followups.halted` stays set. That is the desired separation and a direct
vindication of §1.3(1): **the bell means "come look," the halt means "still
broken."** The human has looked, so the notification is spent; the condition
persists and stays visible via the `followups: {halted: true}` summary already on
`GET /api/sessions` / `GET /api/view` and in the PWA's follow-ups panel.

### §5.5 No setting, no toggle

It fires at most once per halt, on an event that is by definition
operator-actionable, and it cannot be added retroactively once the halt has been
resumed. A config flag would be dead complexity contradicting its own rationale.

---

## §6. The questions the gap description asked, answered directly

### §6.1 Who sets it — does "any Bearer key can set any session's reason" matter?

**For security: no.** Measured against the baseline a Bearer holder already has,
a status string adds no new capability class — it is strictly *less* powerful than
writes that already exist:

| Already possible with the Bearer key | Consequence |
|---|---|
| `POST /api/sessions/{name}/bell` on any session | **Advances that session's follow-up queue** — types operator-authored text into a pane |
| `GET /api/sessions` | Reads every session's live pane content |
| `PUT` / `DELETE /api/sessions/{name}/followups` | Rewrites or destroys any queue |
| `POST /api/sessions/{name}/input` | RCE, fenced, default-closed |

Writing a non-executed string is a smaller capability than the first row.

**For trust semantics: yes, and it is cheap to fix.** A status with no timestamp
is uncalibratable — the human cannot tell "waiting on approval, 4 seconds ago"
from "waiting on approval, since Tuesday." **Any status record must carry a
server-stamped `set_at`** (§7.1). That is the load-bearing field, and it is what
makes staleness detectable by the consumer instead of being muxplex's problem.

A self-declared `source: "my-agent"` field was considered and **rejected**: it is
unverifiable, it would be read as an authorization claim it cannot support, and
`set_at` plus the caller's own logs already cover the debugging need. One field,
not two.

### §6.2 Closed vocabulary versus free text

The gap description is right that `PATCH /api/tmux-config`'s calculus does not
transfer: that vocabulary is a **security model** (tmux config carries `run-shell`
and `default-command`). A status string is never executed, so the security
argument is simply absent. What remains is a consumer/UX argument, and it cuts
both ways:

- **Free text loses.** A phone grid cannot render arbitrary agent prose on a tile
  glanced at from across a room.
- **A muxplex-defined closed vocabulary also loses**, if it is the *only* thing.
  It amounts to muxplex asserting it knows the state machine of every agent CLI
  that will ever run in a pane. `awaiting_approval` is Amplifier/Claude-Code-shaped;
  a `pytest` watcher has no such state. Vocabularies that do not fit get abused —
  everything becomes `other` and the field stops meaning anything.

**The resolution is the one this repo already uses, twice.**
`followups.halted` is `{reason: <closed>, detail: <free>, at, item_id}`, and
`API_SEMANTICS.md` already names `reason`-style keys **the discriminator
convention** (`backstop`, `terminal_conflict`, `unknown_command_id`,
`invalid_view_rule`, `bell_hook_unarmed`, `queue_full`, `focus_failed`): *a client
distinguishes cases by the discriminator key, never by parsing `detail`.*

So: **closed enum for the machine-actionable discriminator, bounded free text for
the human-readable detail.** Precedent, not invention.

And the enum should describe **the human's decision, not the agent's internals** —
that is what makes it survive contact with agents muxplex has never heard of:

| `state` | Means | Human action |
|---|---|---|
| `working` | Running, no human needed | none |
| `blocked` | Needs a human decision or input to proceed | **look** |
| `failed` | Finished badly | **look** |
| `done` | Finished well | none |

Four values. `awaiting_approval` collapses to `blocked` + `detail: "approve tool
use: bash"`. Note this is the *same shape* Phase 1a already delivers for the one
case muxplex owns: `bell.source == "halt"` → `halted.reason` (closed) +
`halted.detail` (free).

### §6.3 What the human sees

**Phase 1 (§4, §5): the grid changes in exactly one way** — a halted queue now
rings, so it enters `?sort=attention` tier 1 and lights the same amber ring every
other bell does, using the badge (`followups.halted`) the grid already renders.
`bell.source` itself is **agent-facing and rendered nowhere.** That is not a
shortfall; there is no room on a phone tile for provenance, and the field's job is
to let a *poller* discard noise.

**Phase 2, if built: `state` is rendered, and only partially.** Only `blocked` and
`failed` — the two that mean *you* — should reach the tile. `working` and `done`
are agent-facing. `detail` belongs in the fullscreen view, never the grid.

**Load-bearing constraint either way: status must never become a second attention
channel.** If `blocked` lit a tile the way a bell does, two channels would compete
for the one scarce thing the bell exists to protect (`AGENT_GUIDE.md:993-1002`), and
`needs_attention()` would have to change — a predicate ported into `muxplex-deck`
and `muxplex_client` and contract-tested for agreement (§3). **Status is a
modifier on an existing alert, never a new alert.**

### §6.4 Do follow-ups already cover part of this?

**Yes — more than expected, and the overlap is the plan.**

Already present: a halt is a specific, knowable, *already-enumerated* reason a
session needs attention, carrying exactly the `{closed reason, free detail,
timestamp}` shape §6.2 argues for, already summarized as
`followups: {pending, halted}` on `GET /api/sessions`, `GET /api/view`, and
`GET /api/federation/sessions`.

Missing: **it rings nothing** (§5.1). The condition is modeled, the human just
never hears about it.

So the cheapest real causality muxplex can offer is not a new vocabulary — it is
**wiring the vocabulary that already exists to the channel that already exists.**
That is Phase 1b, and Phase 1a's `source: "halt"` is the pointer that tells an
agent where to look.

---

## §7. Phase 2 — deferred spec: `PUT /api/sessions/{name}/status`

Written down so it can be built the day a consumer exists, and so nobody rebuilds
§1's reasoning from scratch. **Not scheduled** — see §7.5.

### §7.1 Shape

```
PUT    /api/sessions/{name}/status   { "state": <enum>, "detail": <str, ≤200> }
DELETE /api/sessions/{name}/status
```

Stored at `state["sessions"][name]["status"]`, surfaced on `GET /api/sessions`,
`GET /api/sessions/{name}`, and `GET /api/view` as:

```json
"status": { "state": "blocked", "detail": "approve tool use: bash", "set_at": 1786000000.0 }
```

`null` when unset. `state` ∈ `{working, blocked, failed, done}` (§6.2); anything
else → 400 naming the valid values, matching `PATCH /api/tmux-config`'s rejection
style. `set_at` is **server-stamped**, never client-supplied (§6.1). `detail` is
bounded and never parsed by a client for control flow (the discriminator
convention).

### §7.2 Non-negotiable constraints, all inherited from §1

- **Separate endpoint. Never a body on `POST /bell`** — §1.2 (queue-drain
  incentive) and §1.3 (lifecycle) each independently require this.
- **Never advances the follow-up queue**, and never reads or is read by
  `_advance_followup_queue()`.
- **`needs_attention()` unchanged** (§3, §6.3).
- **`_require_valid_session_name(name)` at the boundary**, plus fail-closed exact
  `get_session_list()` membership → 404, matching every sibling endpoint.
- **Purely additive**, absent-tolerant in both directions, landing in
  `muxplex_client` at the same version (§4.5).

### §7.3 The unsolved question — who clears it

The honest answer, and the reason this is deferred rather than sketched: **there
isn't a good server-side one.** muxplex cannot know when an agent's `blocked`
stopped being true. Options and the recommendation:

- **Agent-owned, no TTL** — the writer clears it via `DELETE`, and `set_at` lets
  every consumer age it however it likes. **Recommended.** A stale status is the
  writer's bug, stated as contract, not muxplex's to guess at.
- Server TTL — muxplex inventing an expiry it cannot justify. Rejected.
- Cleared on bell — reintroduces §1.3's lifecycle coupling. Rejected.

Free for nothing: `_run_poll_cycle()` step 6 already deletes
`state["sessions"][name]` for vanished sessions, so status dies with the session
with no new code.

### §7.4 Federation

Out of scope, same rationale as follow-ups (`API_SEMANTICS.md`): no
`/api/federation/{device_id}/sessions/{name}/status` proxy without a
version-negotiation mechanism this codebase does not have.

### §7.5 Trigger condition

Build this when **a second, independent consumer needs it** — one agent
integration wanting it is a preference; two converging is a contract. Until then
the field would be a mutable, agent-owned state surface with zero writers, and
`AGENTS.md`'s own standing answer (resolve server-side *what clients would
otherwise re-implement*) does not yet apply, because no client is re-implementing
anything here.

---

## §8. Verification methodology for §1.1 — reproducible, and safely

The tmux findings F1-F4 were produced live, **not reasoned**. Recorded so they can
be re-checked, and so nobody re-runs them against the ambient tmux server.

`AGENTS.md:580-598` is unambiguous: `set-hook -g` is **global to the whole tmux
server**, and a prior proof that forgot to override `TMUX_TMPDIR` pointed the
owner's 53 live sessions at a dead scratch port. The probe used:

- a fresh `TMUX_TMPDIR` (`mktemp -d`), **and** an explicit `tmux -L <unique>`
  socket — belt and braces, since a set `$TMUX` silently overrides `TMUX_TMPDIR`;
- `env -u TMUX` on every invocation;
- a hook whose command **appends to a file**, never anything that could reach a
  pane;
- teardown by `tmux -L <unique> kill-server` (socket-scoped — never a bare
  `kill-server`, never `pkill -f`), then `rm -rf` of the scratch dir, then a
  confirmation that the ambient socket was untouched.

Raw observations:

```
F1  bell in INACTIVE window 2:
    S=probesess W=2 WN=win-one PID=%1 ACTIVE=0 BELLFLAG=1      <- belling window, correct

F2  bell in NON-ACTIVE pane %2 of window 2, after `cd /etc`:
    S=probesess W=2 WN=win-one PID=%1 PPATH=/home/bkrabach/dev/muxplex-qol-updates
                                       ^^^^^^^^^^^^^^^^^^^^^^ active pane's path -- WRONG

F3  printf '\033]2;muxplex:awaiting_approval\007'; printf '\a'
    S=probesess W=3 WN=win-payload PTITLE=muxplex:awaiting_approval   <- payload readable
    (the tmux \ek..\e\\ window-rename escape did NOT take: allow-rename is off by default)

F4  two subsequent, unrelated bare `printf '\a'` in the same pane:
    PTITLE=muxplex:awaiting_approval
    PTITLE=muxplex:awaiting_approval                                   <- stale, inherited
```

tmux 3.4. Nothing in the probe touched `muxplex/`, the ambient tmux server, or a
running muxplex.

---

## §9. Test plan (Phase 1)

Per `AGENTS.md`: commit locally first, then `make test` in the DTU, then push.
**Never run the suite on a host serving a live muxplex.**

**Phase 1a — `bell.source`**

1. Each of the four writers stamps its own value: `receive_bell()` → `"hook"`;
   `process_bell_flags()` 0→1 → `"poll"`; the seed branch → `"seeded"`;
   `_bell_for_halt()` → `"halt"`.
2. `empty_bell()` has `source: None`; a bell dict missing the key is read without
   raising (pre-feature `state.json`).
3. **`needs_attention()` is unaffected by `source`** — re-run the existing truth
   table with every enum value injected; all results identical. Guards the §3
   invariant that the contract-tested predicate never learns about this field.
4. `clear_bell()` and `apply_bell_clear_rule()` leave `source` intact (it pairs
   with `last_fired_at`, which they also leave intact).
5. `muxplex_client` parses a `bell` **without** `source` (pre-feature server) and
   **with** an unknown future value, neither raising.

**Phase 1b — halt bell**

6. A halt increments `unseen_count`, sets `last_fired_at`, and sets
   `source == "halt"`.
7. **Loop guard:** a halt fires exactly one bell, and `_advance_followup_queue()`
   is not re-entered. Assert `acceptance_ok()` is `False` immediately afterward.
8. **Idempotence:** a second bell arriving at a halted session advances nothing
   and fires no further halt bell.
9. **Seeded-bell isolation is preserved** — the existing `test_followups.py` test
   stays green unmodified.
10. **`test_safety_rails.py`'s `test_no_diagnostic_tmux_run_shell_construction_exists`
    stays green unmodified** — neither change constructs a `run-shell`.

---

## §10. Documentation changes (part of the work, not follow-up)

- **`docs/API_SEMANTICS.md`** — `bell.source` under the needs-attention section:
  the enum, the honest `"hook"` contract (§4.1), that `needs_attention()` does not
  read it, and that acknowledgment does not clear it. Plus a line under follow-up
  queues: *a halt now rings a bell.*
- **`docs/AGENT_GUIDE.md` §6.4** — how to use `source` for triage (§4.3),
  including "`seeded` means skip." The ring-on-nonzero-exit convention is
  **unchanged and restated**; nothing here weakens it.
- **`AGENTS.md`, bell-hook section** — the §1.1 findings, especially F2 and F4.
  **This may be the most durable output of this document:** the next person to
  propose enriching the hook payload should find the evidence that pane-scoped
  format variables are confidently wrong, and that the OSC-2 side channel is
  sticky, rather than rediscovering it on a live host.
- **`AGENTS.md`, follow-up queue section** — add the halt bell to the list of
  writers that must never route through `receive_bell()`/`process_bell_flags()`,
  beside the seeded bell, with the same structural rationale.
- **`docs/BACKLOG.md`** — file Phase 2 (§7) with its §7.5 trigger. (Unrelated
  housekeeping noticed in passing: backlog item 7, "Put `session_created` on the
  wire," has shipped — `created_at` is on `GET /api/sessions` and on
  `muxplex_client.Session`. The entry should be deleted.)

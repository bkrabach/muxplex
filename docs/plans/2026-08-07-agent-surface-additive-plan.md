# Agent-surface additive plan — five small changes, one theme

**Status:** design only, not implemented. Written against `main` at v0.42.0 (`85a8571`).
**Theme:** *make agents able to see each other and each other's state.*
**Scope:** `docs/AGENT_GUIDE.md`, `docs/API_SEMANTICS.md`, `muxplex/main.py`,
`muxplex/sessions.py`, `client/muxplex_client/*`, and their tests.

Five items came out of one audit of the agent-facing surface. They are individually
small and collectively coherent: two are pure documentation, one publishes a value the
server already computes, one closes the typed client's coverage gaps, and one deletes a
guarantee that does not exist. Item **E** gates
[`2026-08-07-scrollback-paging-plan.md`](2026-08-07-scrollback-paging-plan.md).

The audit was **code-reading, not runtime-verified**. §1 re-checks every claim against
the source, corrects the two that were overstated, and records the one runtime proof
that was actually run.

---

## 0. What should NOT be built

The same audit produced a do-not-build list. It is restated here because three of the
five items below sit one design decision away from it, and a builder who "adds a little
flexibility" lands on the wrong side.

| Rejected | Which item could drift into it | Why it stays rejected |
|---|---|---|
| `PATCH` for `settings.LOCAL_ONLY_KEYS` | D (client convenience) | The Bearer key that authenticates `PATCH /api/settings` is the same credential handed to agents calling `/input`. A PATCHable fence is a decorative fence. `AGENTS.md`, "Terminal input", Sibling 1. |
| A target on `POST /api/focus` | D (a `raise_focus(app=...)` sugar) | The endpoint taking **no target of any kind** *is* the security design. `test_focus_endpoint_accepts_no_target` guards it. `AGENTS.md`, "Foreground focus". |
| `command_id` on `DELETE /api/sessions/{name}` | D (symmetry with `create_session(command_id=)`) | The pair is looked up from the manifest; accepting a caller-chosen id would let any authenticated caller run pair A's teardown against a pair-B session. `main.py:2393-2400`. **D adds `force`, never `command_id`.** |
| A wider `/input` key allowlist | D (`KNOWN_KEYS` is mirrored in the client) | `terminal_input.ALLOWED_KEYS` is a closed set; `constants.KNOWN_KEYS` mirrors it and `test_client_contract.py:306` pins the equality. D **mirrors**, never extends. |
| Silent clamping anywhere | C, D | Out-of-range is a 400, never a short answer (`AGENT_GUIDE.md:967`). Nothing here introduces a clamp. |
| Materializing `match_names` into `view["sessions"]` | C (touching `annotate_view_membership` call sites) | Standing prohibition, load-bearing. `AGENTS.md`, "Auto-updating views". C calls the existing resolver; it never writes settings. |

One further exclusion, decided in this plan rather than inherited: **C does not add
`cwd` to `GET /api/view`**, and **E does not add `history_limit` to any response.**
Rationale in §6.2 and §8.4.

---

## 1. Verification ledger

Every audit claim, re-checked against `main` at `85a8571`. Two were overstated; one
needed runtime confirmation and got it.

### 1.1 Confirmed as stated

| Claim | Evidence |
|---|---|
| `followup` appears **zero** times in `docs/AGENT_GUIDE.md` | `grep -ic followup docs/AGENT_GUIDE.md` → `0` |
| Five follow-up endpoints exist | `main.py:2072` (GET), `:2098` (POST), `:2163` (PUT), `:2212` (DELETE), `:2229` (POST `/resume`) |
| Badged on three read endpoints | `main.py:1390` (`GET /api/sessions`), `:1620` (`GET /api/view`), `:4117` (`GET /api/federation/sessions`) |
| `AGENT_GUIDE.md:540-553` prescribes `409 {"terminal_conflict": true}` + `&takeover=true` | Read directly; text is present and prescriptive |
| `API_SEMANTICS.md:744-748` says that response is RETIRED and cannot fire | "**`409 terminal_conflict` on `/connect` is RETIRED — it cannot fire.**" |
| `main.py:1845-1909` has no 409 path; `takeover` is "accepted and ignored" | `connect_session()` body raises only 404/500/503; docstring `main.py:1869-1871` |
| `AGENT_GUIDE.md:268-278`'s `GET /api/sessions` example omits fields the route returns | Route returns `views` **and** `followups` (`main.py:1382-1396`); example shows neither |
| `get_session_cwds()` imported at `main.py:74`, refreshed every poll, never published | Sole consumer is `update_manifest(..., cwds=get_session_cwds())` at `main.py:507`. No response dict in `main.py` contains a cwd |
| `tmux_socket_dir` precedent is exact | `main.py:3063-3079` — on the **unauthenticated** `/api/instance-info`, with the stated reasoning "a filesystem path is not a secret in the way `federation_key` or TLS material would be." Also `API_SEMANTICS.md:514` |
| `create_session()` hardcodes `json={"name": name}` | `sync_client.py:157`; `async_client.py` mirror |
| `delete_session()` has no `force` | `sync_client.py:163-165`; server signature is `delete_session(name: str, force: bool = False)` at `main.py:2387`, 409 path at `main.py:2443` |
| `followup` appears **zero** times in `client/muxplex_client/*.py` | `grep -ric followup` → 0 across all eight modules |
| Three sites promise the retention guarantee | `sessions.py:452-462`, `main.py:1415` (`get_session_snapshot` docstring), `AGENT_GUIDE.md:972-975` |
| `base.conf:28` sets `history-limit 50000` on a managed host | `muxplex/tmux_templates/base.conf:28` |
| `SESSION_HISTORY_LIMIT = 5000` would be a 10× reduction where it binds | `sessions.py:462` vs `base.conf:28` |

### 1.2 Corrected — the audit overstated two things

**`GET /api/views` is not absent from `AGENT_GUIDE.md`.** It is mentioned in prose at
`AGENT_GUIDE.md:324-325` ("`GET /api/views` returns the resolved patterns plus any
validation errors"). What is missing is a request/response shape, not the endpoint. Item
A should *complete* that mention, not introduce it.

The genuinely absent endpoints, re-derived from `grep -n '@app\.' muxplex/main.py`
against the guide:

| Absent from `AGENT_GUIDE.md` | Route |
|---|---|
| `POST /api/views/preview` | `main.py:1705` |
| `POST /api/sessions/{name}/bell` | `main.py:2566` |
| `POST /api/sessions/{name}/bell/clear` | `main.py:2606` |
| `POST /api/heartbeat` | `main.py:2512` |
| `GET`/`PATCH /api/tmux-config` | `main.py:2782`, `:2797` |
| `GET`/`PUT /api/settings/sync` | `main.py:2871`, `:2899` |
| `GET /api/federation/sessions` and the four `/api/federation/{device_id}/*` proxies | `main.py:4087`, `:4327`, `:4371`, `:4417`, `:4478` |

(`POST /api/federation/generate-key` **is** covered, at `AGENT_GUIDE.md:192`.)

**The frontend's `terminal_conflict` handling is not drift and must not be "cleaned
up."** `frontend/tests/test_terminal.mjs:1477-1560` exercises a client-side defensive
path against a response an *older peer* could still send. Item B corrects the **guide**,
which tells new agent authors to write handling for a response the current server cannot
produce. Leave the frontend alone.

### 1.3 Runtime-confirmed — claim E

The audit reported this empirically and the scrollback plan re-states it. It is the only
claim in this set whose fix **deletes shipped behavior**, so it was re-run here rather
than passed through. Measured on **tmux 3.4**, against an isolated `-L` socket with a
scratch `TMUX_TMPDIR` and `-f /dev/null` (per `AGENTS.md` — no `pkill`, no bare
`kill-server`, socket-scoped teardown):

```
$ tmux -f /dev/null new-session -d -s defprobe
$ tmux display-message -p -t defprobe 'history_limit=#{history_limit}'
history_limit=2000                                  # compiled-in default confirmed

# exactly what ensure_history_retention() does, to a LOW value
$ tmux new-session -d -s beh
  at create: history_limit=2000 history_size=0
$ tmux set-option -t beh history-limit 50
  after set-option 50: history_limit=2000           # the live pane ignores it
$ tmux send-keys -t beh 'seq 1 500' Enter
  after 500 lines:  history_limit=2000 history_size=481

# a window created AFTER the set-option DOES inherit it
$ tmux new-window -t beh -d -n small
  new window: history_limit=50

# the case that actually matters: the RAISE this code performs
$ tmux new-session -d -s hi
$ tmux set-option -t hi history-limit 5000          # == ensure_history_retention()
$ tmux send-keys -t hi 'seq 1 4000' Enter
  raise-to-5000 case: history_limit=2000 history_size=1981
  capture -S - rows:  2005                          # evicted at ~2000, not 5000
```

This is stronger than the audit's version, which showed the *lowering* case being
ignored. The **raise** case — the one the code performs and three documents describe —
also does not take: 4,000 emitted lines produced 1,981 retained rows, evicted at the
compiled default, on a pane whose limit had just been "raised" to 5,000. **The exact
failure `main.py:1415` claims to prevent is what happens.**

The one behavior the call *does* have: windows created inside the session **after** the
call inherit the value. That is not what any of the three documents promise, and on a
managed host it makes things worse (50000 → 5000). Both facts belong in the commit
message; neither argues for keeping the call.

### 1.4 Needs runtime confirmation before shipping — not settled by this plan

| Open question | Item | Why reading code isn't enough |
|---|---|---|
| Does `#{pane_current_path}` report a stable value for a session whose active pane is a TUI (amplifier's own TUI is the primary workload)? | C | The field is documented as the *active window's active pane's* cwd. Under a TUI the pane's cwd may be the process's launch directory rather than anything the user navigated to. C's wire contract must describe what is actually observed, not what "cwd" implies. **Measure before writing the docstring.** |
| Does a session created by `amplifier-workspace {name}` (the reference non-default pair) report the workspace directory or `$HOME`? | C | This is the motivating use case — "which repo is a sibling session in." If the answer is `$HOME`, C still ships (an honest observation is better than none) but the guide's framing changes from "which repo" to "where the active pane currently is." |
| Does `POST /api/sessions/{name}/followups` reliably 409 `bell_hook_unarmed` on a fresh instance? | A | The guide will tell agents this 409 is recoverable via `POST /api/internal/setup-hooks` (`AGENT_GUIDE.md:1027` already documents that endpoint for the sibling bell problem). Confirm the recovery actually clears it before printing the instruction. |

Everything else below is settled and a builder should not re-derive it.

---

## 2. Should all five be built?

Yes — with one scope cut inside C and one inside E.

**A and B are unambiguous.** Zero code. B is the only item that removes *active harm*:
agents are currently being told to write error handling for a response the server cannot
emit, and to recover from it with a parameter the server explicitly ignores.

**C is the only item that adds a permanent field to a public contract**, so it gets the
hardest look. It survives: the value is already computed every poll cycle (zero new
subprocesses), the precedent for publishing a filesystem path is exact and stronger
(`tmux_socket_dir` is on the *unauthenticated* endpoint; these are authenticated), and
without it the stated theme is unreachable — a sibling's identity is its working
directory. **Scope cut: `GET /api/view` does not get it** (§6.2).

**D is the client catching up to a server contract that already exists.** It adds no
wire surface at all. The `followups` gap is the sharp one: a halted queue is a silent
stall, and the typed client currently cannot see it even though the field is already on
every `GET /api/sessions` entry it parses.

**E is a deletion.** It removes a false guarantee and unblocks paging.

---

## 3. Sequencing and parallelism

```
        ┌──────────────┐
        │ PR1: A + B   │  docs only, no code, no test changes
        └──────────────┘
        ┌──────────────┐
        │ PR2: E       │  deletion + 3 doc corrections   ──gates──▶ scrollback paging
        └──────────────┘
        ┌──────────────┐        ┌──────────────┐
        │ PR3: C       │───────▶│ PR4: D       │
        └──────────────┘        └──────────────┘
```

**PR1 and PR2 are independent of each other and of everything else — run them in
parallel, two builders.** They share `docs/AGENT_GUIDE.md` but touch disjoint regions
(PR1: §3, §4, a new §; PR2: §6.3 only). If one builder does both, do PR1 first so PR2's
§6.3 edit lands against final surrounding text.

**PR3 must precede PR4.** D parses fields C adds. Landing D first would mean touching
`models.py` and `_protocol.py` twice for one feature.

**C and D can be *developed* concurrently** — D's `create_session`/`delete_session`/
followups work has no dependency on C. Only D's `SessionSnapshot` parity fields do. If
two builders are available, have D's author write everything except the parity fields
while C is in review, then rebase.

**Nothing here blocks on paging, and paging blocks on E only.** The scrollback plan's
Phase 0 (`2026-08-07-scrollback-paging-plan.md` §1, §4) **is** item E. Delivering E here
satisfies it; that plan's Phase 0 row should be marked as landed by this work rather
than done twice.

---

## 4. Item A — document the follow-up queue (and the rest of the missing surface)

**Files:** `docs/AGENT_GUIDE.md` only.
**Contract change:** none.

### 4.1 Why this is the highest-value item

The follow-up queue is muxplex's **first autonomous write** — the durable
agent-to-agent note primitive — and the one document written to tell an agent what it
can do never mentions it. The semantics already exist in `API_SEMANTICS.md:29-80`; what
is missing is the operational half: the requests, the recoverable failures, and the
badge an agent should be polling.

### 4.2 New section: `## 6.5 Follow-up queues — leaving a note for the next bell`

Place it inside §6 ("Running sessions unattended"), after §6.4 ("Bell-on-completion").
That is the correct home: §6.4 already teaches an agent to *ring* the bell; the queue is
what *fires on* one. Renumber nothing else — §7 onward stays put.

Content, in this order:

1. **What it is, in one paragraph.** A per-session, server-side, persisted list of text
   items. One item fires per bell, into that session, until the queue drains. It
   survives a muxplex restart; it is not federated.

2. **The badge first, endpoints second.** An agent that only *reads* needs one fact:
   `GET /api/sessions` and `GET /api/view` entries carry
   `followups: {"pending": int, "halted": bool}` (`followups.summary`, `followups.py:71-83`).
   `halted: true` is a **stalled queue that nothing will clear implicitly** — this is
   the sentence that has to be impossible to miss.

3. **The five endpoints**, with a worked `curl` each, matching §5.7's style:

   | Method | Path | Notes for the example |
   |---|---|---|
   | `GET` | `/api/sessions/{name}/followups` | Returns `{session, revision, items, halted, target_window}`. An absent queue and an empty queue are indistinguishable: `revision: 0, items: [], halted: null`. |
   | `POST` | `/api/sessions/{name}/followups` | Append one item, `{text, enter}`. No precondition. |
   | `PUT` | `/api/sessions/{name}/followups` | Whole-list replace. `expected_revision` is **REQUIRED**. |
   | `DELETE` | `/api/sessions/{name}/followups` | Clears items **and** the halt. |
   | `POST` | `/api/sessions/{name}/followups/resume` | Clears the halt only, keeping every item. |

4. **`expected_revision` gets its own short subsection.** Explain the failure it
   prevents in agent terms, not database terms: a stale `PUT` built from a pre-bell
   snapshot can **re-add an item that has already been typed into the session** — a
   second execution, not a lost update. The loop is: `GET` → read `revision` → build the
   new list → `PUT` with that `revision`. On 409, re-`GET` and rebuild; never retry the
   same body.

5. **The failure table**, which is the part an agent author actually needs:

   | Status | Discriminator | What an agent does |
   |---|---|---|
   | 409 | `bell_hook_unarmed` | The tmux bell hook isn't registered, so nothing would ever fire the queue. Call `POST /api/internal/setup-hooks` (already documented at `AGENT_GUIDE.md:1027`) and retry. **Confirm this recovery at runtime before printing it — §1.4.** |
   | 409 | `queue_full` | At `MAX_FOLLOWUPS` (16). Drain or `PUT` a shorter list. |
   | 409 | `send_in_flight` | A send for this session is mid-flight (`PUT` only). Re-`GET` and retry. |
   | 409 | `expected_revision` mismatch | Someone else wrote. Re-`GET`, rebuild, re-`PUT`. |
   | halted (not an HTTP status) | — | A fire-time send failed. The item was **retained, not skipped.** Nothing clears this implicitly — an operator or agent must `POST .../resume`. |

6. **Two properties that will otherwise surprise an author**, stated plainly:
   - The `/input` fence is **re-evaluated at fire time against fresh settings.** An
     append that succeeded is not a promise the item will ever be allowed to send.
     Appending is UX; the allowlist is the boundary (`AGENTS.md`, "Follow-up queue").
   - `target_window` is **display-only.** `tmux send-keys` types into whatever window is
     current at fire time, not necessarily the window that belled.

7. **One line on federation:** these endpoints are local-only. There is no
   `/api/federation/{device_id}/sessions/{name}/followups` and one must not be assumed.

8. Link to `API_SEMANTICS.md`'s "Follow-up queues" section for the *why*, per the
   existing division of labour between the two documents (`AGENT_GUIDE.md:11-19`).

### 4.3 The other missing endpoints

Do **not** write a §6.5-depth section for each. `AGENT_GUIDE.md` is already 1,158 lines
and its own §9 says `/openapi.json` is authoritative for exact shapes. The proportionate
fix is a **coverage table in §9**, replacing the current hand-wave at
`AGENT_GUIDE.md:1126-1129` ("Endpoints not covered here … are all in the schema"):

| Endpoint | One line | Read this |
|---|---|---|
| `GET /api/views` | Resolved view definitions + `match_names` rule errors. Complete the existing mention at `:324` with a response shape. | `main.py:1649` |
| `POST /api/views/preview` | Dry-run a draft `match_names` list against live sessions. Never writes. | `main.py:1705` |
| `POST /api/sessions/{name}/bell` | Record a bell for a session (what the tmux hook calls). | `main.py:2566` |
| `POST /api/sessions/{name}/bell/clear` | Mark a session's bell seen. | `main.py:2606` |
| `POST /api/heartbeat` | Register a `device_id` — the opt-in step `:527-538` already describes. | `main.py:2512` |
| `GET`/`PATCH /api/tmux-config` | Inspect/manage the managed tmux config. See `docs/TERMINAL_CONFIG_OWNERSHIP.md`. | `main.py:2782` |
| `GET`/`PUT /api/settings/sync` | Federation settings sync. See `API_SEMANTICS.md`'s write-discipline section. | `main.py:2871` |
| `GET /api/federation/sessions` + `/api/federation/{device_id}/*` | Aggregated multi-host reads and the four proxies. | `main.py:4087`, `:4327`, `:4371`, `:4417`, `:4478` |

Each row is a pointer, not a contract. The follow-up queue gets the full treatment
because it is the one an agent is expected to *drive*; the rest an agent mostly *reads*
or does not touch at all.

### 4.4 Done when

- `grep -c followup docs/AGENT_GUIDE.md` is non-zero and §6.5 exists.
- Every `curl` in §6.5 has been executed against a scratch instance and its real response
  pasted in — this guide's established standard ("proven below against a live instance,
  with real traces, not asserted," `AGENT_GUIDE.md:836`). A fabricated example body is a
  worse defect than the omission it replaces.
- Every route in the §9 table resolves to the `main.py` line given.
- The §10 checklist gains one item: *if you queue follow-ups, poll `followups.halted` —
  a halt is a silent stall nothing clears for you.*
- **No test changes.** Nothing in the suite asserts on `AGENT_GUIDE.md` content
  (`test_readme.py` reads `README.md` only). See §4.5 for why that stays true here.

### 4.5 Deliberately no doc-content test for A

`AGENTS.md` documents `test_frontend_js.py` as a cautionary tale: 229 regex assertions
against source text that pin *shape* rather than behavior and have twice failed a correct
refactor. A grep-suite over prose has the identical failure mode and less value. A adds
none. **B adds exactly one** (§5.4) because it guards a claim that has already rotted
once and whose rotting is silently harmful.

---

## 5. Item B — fix the drift in the same file

**Files:** `docs/AGENT_GUIDE.md`, plus one test in `muxplex/tests/test_api.py`.
**Contract change:** none. This documents the contract that already exists.

### 5.1 The retired 409

`AGENT_GUIDE.md:540-553` currently tells agents that `POST /connect` "can now return
`409` with a `{"terminal_conflict": true, ...}` body," and prescribes retrying with
`&takeover=true`. All three of the following are true on `main`:

- `API_SEMANTICS.md:744-748`: "**`409 terminal_conflict` on `/connect` is RETIRED — it
  cannot fire.** Its condition … arbitrated a single contended resource that no longer
  exists."
- `main.py:1845-1909`: `connect_session()` raises 400, 404, 500, 503. There is no 409
  path.
- `main.py:1869-1871`: "`takeover` is accepted and ignored: with no single shared
  terminal to seize, there is nothing left to take over."

**Replace the paragraph.** New text, in the same location:

- `POST /connect` no longer arbitrates a shared terminal — there is one ttyd per session
  (`AGENTS.md`, "ttyd is loopback-only by design — now per-session"), so there is nothing
  to contend for.
- `?takeover=true` is **accepted and ignored.** It is retained in the signature so
  pre-existing clients don't 422. Do not send it in new code; do not build recovery on it.
- The failure modes that *are* real and *are* new: **500** if the session's terminal
  process fails to start, **503** at the terminal-count ceiling. `AGENT_GUIDE.md:439-443`
  already says this — the replacement paragraph should point at it rather than restate it.
- One sentence for readers of older docs: a client that still handles a 409 here simply
  never sees it. Version-tolerant in the direction `AGENTS.md` requires. **This is why
  the frontend's handling stays** (§1.2).

### 5.2 The stale `GET /api/sessions` example

`AGENT_GUIDE.md:268-278` shows five keys. The route (`main.py:1382-1396`) returns seven.
Corrected example:

```json
[
  {
    "name": "agent-build",
    "snapshot": "…captured pane text…",
    "bell": {"last_fired_at": 1753500000.0, "seen_at": null, "unseen_count": 1},
    "last_activity_at": 1753500123.0,
    "created_at": 1753499900.0,
    "views": ["work", "agents"],
    "followups": {"pending": 2, "halted": false}
  }
]
```

Add two sentences after it:

- `views` — server-resolved membership, pins ∪ glob-rule matches
  (`views.annotate_view_membership`). This is what makes rule-based views reach a polling
  client without re-deriving membership from raw `settings.views`.
- `followups` — the queue badge. Forward-reference §6.5 (item A). **This is the ordering
  reason A and B belong in one PR**: B's example introduces a field only A explains.

If item C lands, the same example gains `cwd` — see §6.5.

### 5.3 One more drift found while checking B

`AGENT_GUIDE.md:690` cross-references an anchor that does not exist:
`#the-read-model-is-eventually-consistent--wait-3s-after-writes`. The heading was
rewritten to "poll on a short interval, not a long sleep" (`:484`) and the link was not.
The adjacent prose still says "Retry after ~3s," contradicting the measured ~1s guidance
the section itself now gives. Fix both — same file, same class of defect, one line each.

### 5.4 The one test worth adding

```python
# muxplex/tests/test_api.py — beside test_connect_no_longer_returns_terminal_conflict:1495

def test_agent_guide_does_not_prescribe_retired_terminal_conflict():
    """AGENT_GUIDE.md must not tell agents to handle a response /connect cannot emit.

    Pairs with test_connect_no_longer_returns_terminal_conflict (:1495), which
    pins the server side. That test kept the server honest while the guide
    rotted for a full release; this one closes the other half.
    """
    guide = (Path(__file__).parent.parent.parent / "docs" / "AGENT_GUIDE.md").read_text(
        encoding="utf-8"
    )
    assert "terminal_conflict" not in guide
    assert "takeover=true" not in guide
```

Two assertions, both on strings that only appear when the defect is present. This is the
narrow, behavior-adjacent end of the source-text-assertion spectrum, not the
`test_frontend_js.py` end (§4.5). **Do not grow it into a general doc-lint suite.**

Note `encoding="utf-8"` — mandatory per this repo's cross-platform rule; the existing
`test_readme.py:5` omits it and should not be copied.

### 5.5 Done when

- `grep -c "terminal_conflict\|takeover=true" docs/AGENT_GUIDE.md` → 0.
- The new test fails against `HEAD~1` of the doc change and passes after.
- The §3 example's key set is byte-equal to the route's, verified by fetching
  `GET /api/sessions` from a scratch instance and diffing key names.
- `grep -n "wait-3s-after-writes" docs/AGENT_GUIDE.md` → 0.
- `frontend/tests/test_terminal.mjs` is **untouched**.

---

## 6. Item C — put `cwd` on the wire

**Files:** `muxplex/main.py`, `docs/API_SEMANTICS.md`, `docs/AGENT_GUIDE.md`,
`muxplex/tests/test_api.py`.
**Contract change:** additive. One new field on two response shapes, plus three parity
fields on a third.

### 6.1 What exists already

`enumerate_sessions()` (`sessions.py:381-429`) requests
`#{session_name}\t#{window_activity}\t#{session_created}\t#{pane_current_path}` and
caches the fourth field into `_cwds`. `get_session_cwds()` (`sessions.py:165-184`)
returns a copy. `main.py:74` imports it. Its only consumer is the presence manifest
(`main.py:506-508`).

So the value is refreshed every ~2s poll cycle, at **zero** additional subprocess cost,
and thrown away for wire purposes. C publishes it. **No change to `sessions.py`.**

### 6.2 Where it goes — and where it does not

| Endpoint | `cwd`? | Reason |
|---|---|---|
| `GET /api/sessions` (`main.py:1382-1396`) | **Yes** | The bulk read is where an agent enumerates siblings. This is the whole point. |
| `GET /api/sessions/{name}` (`main.py:1438-1446`) | **Yes**, with parity — §6.4 | An agent polling one session must not have to also poll the bulk endpoint to learn the same facts. |
| `GET /api/federation/sessions`, local branch (`main.py:4110-4126`) | **Yes** | Remote entries are spread `**s` (`main.py:4232`), so a peer's `cwd` rides along automatically. Omitting it locally would make local entries the *poorer* ones in a merged list — a needless asymmetry. |
| `GET /api/view` | **No** | Deliberately carries no pane snapshots so it stays cheap for frequent polling (`AGENT_GUIDE.md:315`). It is a **display** resolution — view membership, attention, sort order. A working directory is not a display concern, and adding it invites `/api/view` to become a second `/api/sessions`, which is exactly the split this endpoint exists to maintain. |
| `GET /api/instance-info` | **No** | Instance-scoped, unauthenticated. Per-session data has no business there. |

### 6.3 Field contract

```jsonc
"cwd": "/home/you/dev/muxplex"   // or null
```

- **Type:** `string | null`. Key **always present**; `null` when tmux reported nothing
  parseable. Same convention as `last_activity_at` and `created_at`
  (`main.py:1362-1364`). `get_session_cwds().get(name)` yields exactly this — empty
  paths are already dropped at parse time (`sessions.py:412-414`).
- **Semantics:** the **active window's active pane's** `#{pane_current_path}`, observed
  at the last poll. It is an **observation, not an identity**: it moves when the user
  `cd`s, and for a multi-window session it tracks whichever window is current. Say this
  in the docstring in these words. `sessions.py:165-184` and `manifest.py:78-85` already
  carry the honest limitations — reuse that language rather than inventing softer wording.
- **Freshness:** the ~2s poll cache on the bulk endpoint; live on
  `GET /api/sessions/{name}` only if the builder chooses to re-read — **do not.** Serve
  the same cached `get_session_cwds()` value on both, so the two endpoints cannot
  disagree. A live per-request cwd read would be a new subprocess on a path whose entire
  design premise is "one fresh `capture-pane`, nothing else" (`main.py:1401-1410`).
- **Not a secret:** the precedent is `tmux_socket_dir` on the **unauthenticated**
  `/api/instance-info` (`main.py:3063-3079`). These three endpoints are authenticated.
  The judgment is strictly weaker than one already made and shipped.

### 6.4 The parity fix on `GET /api/sessions/{name}`

The single-session read currently returns `{name, snapshot, lines, bell,
last_activity_at}` (`main.py:1438-1446`). The bulk read returns `{name, snapshot, bell,
last_activity_at, created_at, followups, views}`. **A polling agent that has narrowed to
one session cannot see a halted queue** — the exact silent stall item A teaches it to
watch for.

Bring it to parity in the same PR. After `cwd`, the response becomes:

```jsonc
{
  "name": "agent-build",
  "snapshot": "…",
  "lines": 500,                                  // unchanged — depth REQUESTED
  "bell": { … },
  "last_activity_at": 1753500123.0,
  "created_at": 1753499900.0,                    // NEW (parity)
  "followups": {"pending": 0, "halted": false},  // NEW (parity)
  "views": ["work"],                             // NEW (parity)
  "cwd": "/home/you/dev/muxplex"                 // NEW (item C)
}
```

Implementation — mirror `get_sessions()` (`main.py:1382-1396`) exactly, including the
synthetic-key dance, so the two cannot drift:

```python
# in get_session_snapshot(), replacing the current return dict
settings = load_settings()
local_device_id = load_device_id()
entry = {
    "name": name,
    "sessionKey": f"{local_device_id}:{name}",   # synthetic; popped below
    "snapshot": snapshot,
    "lines": lines,
    "bell": bell,
    "last_activity_at": activity.get(name),
    "created_at": get_session_created_times().get(name),
    "followups": followups.summary(state, name),
    "cwd": get_session_cwds().get(name),
}
annotated = annotate_view_membership([entry], settings)[0]
annotated.pop("sessionKey", None)
return annotated
```

`annotate_view_membership` requires `sessionKey` to resolve device-qualified pins
(`views.py:312-327`); it returns **new** dicts and never mutates, so the single-item list
is safe. It resolves rules fresh — it does **not** write `settings` (§0's standing
prohibition).

`lines` keeps its exact current meaning: the depth **requested**. Redefining it would be
a silent semantic change to a shipped field. The scrollback paging plan depends on this
(`2026-08-07-scrollback-paging-plan.md` §3.3) — do not touch it.

### 6.5 Documentation

- `docs/API_SEMANTICS.md` — new bullet in "Semantics external clients re-implement,"
  beside the `tmux_socket_dir` entry at `:514`. State: the source field, that it is an
  observation not an identity, `null` when absent, why `/api/view` is excluded, and that
  the single-session read now reaches parity with the bulk read.
- `docs/AGENT_GUIDE.md` §3 — add `cwd` to the corrected example from §5.2 and one
  sentence: *`cwd` is where that session's active pane currently is — how you tell which
  repo a sibling agent is working in. It is an observation, not a stable identity: it
  moves when someone `cd`s.* §6.3's `GET /api/sessions/{name}` shape gains the four new
  keys.
- Both wordings must match what §1.4's measurements actually show for a TUI-held pane
  and for an `amplifier-workspace`-created session. **Measure first, write second.**

### 6.6 Tests

In `muxplex/tests/test_api.py`, using the existing session-seeding fixtures:

1. `test_sessions_includes_cwd` — seeded cwd appears on the bulk entry.
2. `test_sessions_cwd_is_null_when_tmux_reports_none` — key present, value `None`. Guards
   the "always present" half of the convention.
3. `test_session_snapshot_reaches_parity_with_bulk` — assert the **key sets** of
   `GET /api/sessions/{name}` and the matching `GET /api/sessions` entry differ by exactly
   `{"lines"}` and `{"snapshot"}`-depth. Asserting the *key sets* rather than a hardcoded
   list is what keeps the two from drifting again when the next field lands.
4. `test_session_snapshot_surfaces_halted_followups` — the motivating case: a halted queue
   is visible from the single-session read.
5. `test_view_does_not_carry_cwd` — pins the §6.2 exclusion. Without it, a future
   "consistency" PR quietly adds it.
6. `test_federation_local_entries_carry_cwd` — the local branch, since it is a separate
   literal dict from `get_sessions()`.

Integration coverage (real tmux, `-L` scratch socket per `test_integration.py::tmux_server`):
one test that creates a session in a known directory and asserts `cwd` comes back as that
directory. That is the only assertion that proves the field is wired to reality rather
than to a mock.

### 6.7 Done when

All six unit tests plus the integration test pass under `make test` (DTU), the
`API_SEMANTICS.md` bullet is written, and a real `GET /api/sessions` against a scratch
instance shows a real path — pasted into the guide, not invented.

---

## 7. Item D — close the client's coverage gaps

**Files:** `client/muxplex_client/{sync_client,async_client,models,_protocol}.py`,
`client/tests/test_protocol.py`, `muxplex/tests/test_client_contract.py`.
**Contract change:** none on the wire. Client-side only.

Every change below is mirrored in **both** `sync_client.py` and `async_client.py` — the
duplication is deliberate and documented (`_protocol.py:1-12`: the shell is thin and
honest, the logic lives in `_protocol`). Anything with a decision in it goes in
`_protocol.py` and is tested once, without network, in `client/tests/test_protocol.py`.

### 7.1 `create_session(command_id=)`

`sync_client.py:157` sends `json={"name": name}`. The server accepts
`{"name", "command_id"}` (`main.py:1772`), and `AGENT_GUIDE.md:378-428` documents
`command_id` as the way to spawn a configured pair. A typed-client agent that wants the
`amplifier` pair must drop to raw `httpx` — which defeats the client's reason to exist.

```python
def create_session(
    self,
    name: str,
    *,
    command_id: str | None = None,
    wait: bool = True,
    timeout: float = 6.0,
    interval: float = 0.3,
) -> None:
    body: dict[str, Any] = {"name": name}
    if command_id is not None:
        body["command_id"] = command_id
    self._request("POST", "/api/sessions", json=body, session_name=name)
    ...
```

`command_id=None` omits the key entirely — byte-identical to today's request. Do **not**
send `"command_id": null`; the server's own contract note is that omitting it is
byte-identical to pre-feature behavior (`AGENT_GUIDE.md:417-419`), and an explicit null
is a different request.

An unresolvable id is a **400** with `{"unknown_command_id": true, "available": [...]}`
and nothing is spawned. `map_status_error` (`_protocol.py:178-205`) sends 400 to
`ApiError(status, detail)` — correct and sufficient. **Do not add an
`UnknownCommandId` exception class.** One new error type per discriminator is how a
six-shape client becomes a twenty-shape client; `ApiError.status == 400` plus the detail
string is the honest surface, and the caller who needs the `available` list is the caller
who should be calling `list_session_commands()` first.

**Also add `list_session_commands()`** → `GET /api/session-commands` (`main.py:1447`).
Without it, `command_id` is a parameter whose legal values the client cannot discover, and
the guide is explicit that clients must not re-derive the pair list from raw
`GET /api/settings` (`AGENT_GUIDE.md:401-404`). New model:

```python
@dataclass(frozen=True)
class SessionCommand:
    id: str
    label: str
    new_session_template: str
    delete_session_template: str

@dataclass(frozen=True)
class SessionCommands:
    commands: tuple[SessionCommand, ...]
    default_id: str
    errors: tuple[str, ...]
```

The templates are arbitrary shell commands the server runs. They are already in the
response body of an authenticated endpoint that deliberately sits outside
`_AUTH_EXEMPT_PATHS` because it "discloses server-side shell commands"
(`main.py:1460-1462`). The client parses what the server sent; it does not re-fence it.

### 7.2 `delete_session(force=)`

Server: `delete_session(name: str, force: bool = False)` (`main.py:2387`). When the
recorded pair no longer resolves, it **409s and runs nothing** (`main.py:2443-2459`);
`?force=true` substitutes the default kill command and logs a warning. The typed client
cannot reach the recovery.

```python
def delete_session(self, name: str, *, force: bool = False) -> None:
    params = {"force": "true"} if force else None
    self._request("DELETE", f"/api/sessions/{name}", params=params, session_name=name)
```

Omit the param entirely when `force=False` so the default request is byte-identical.

The docstring must carry the warning, because `force=True` is a real decision: *the 409
means the session was created with a command pair that is no longer configured.
`force=True` kills it with the **default** command instead — which may not perform the
teardown the original pair would have (e.g. `amplifier-dev --destroy` cleanup is skipped).
Prefer restoring the pair in `settings.json`.*

**`command_id` is never added to this method.** §0.

### 7.3 Follow-ups

The largest piece of D, and the one that makes a halted queue visible.

**Model field (do this even if nothing else in D ships):**

```python
@dataclass(frozen=True)
class Followups:
    """The queue badge on GET /api/sessions and GET /api/view entries."""
    pending: int = 0
    halted: bool = False

@dataclass(frozen=True)
class Session:
    ...
    followups: Followups = Followups()   # NEW, defaulted
```

Parsed with `.get()` so a pre-feature server (no key) yields `Followups()` — the
version-tolerance rule in both directions (`_protocol.py:9-11`, `AGENTS.md`). Also add it
to `ViewSession` (`GET /api/view` carries it too, `main.py:1620`) and to
`SessionSnapshot` once item C lands.

**Methods — all five endpoints, plus one composed helper.**

Partial coverage of a five-endpoint feature is itself the drift the client exists to
prevent: a caller needing `PUT` drops to raw `httpx`, which is the original complaint. It
is roughly 60 lines of thin shell across sync + async.

| Method | Endpoint |
|---|---|
| `followups(name) -> FollowupQueue` | `GET` |
| `append_followup(name, text, *, enter=False) -> FollowupItem` | `POST` |
| `replace_followups(name, items, *, expected_revision) -> FollowupQueue` | `PUT` |
| `clear_followups(name) -> None` | `DELETE` |
| `resume_followups(name) -> FollowupQueue` | `POST .../resume` |

```python
@dataclass(frozen=True)
class FollowupItem:
    id: str
    text: str
    enter: bool
    created_at: float | None = None

@dataclass(frozen=True)
class FollowupQueue:
    session: str
    revision: int
    items: tuple[FollowupItem, ...]
    halted: Mapping[str, Any] | None      # None = not halted
    target_window: str | None = None
```

`halted` stays a raw mapping rather than a typed dataclass: it is a diagnostic payload
whose shape is the server's to evolve, and the only question a caller asks of it is
`is not None`. Typing it would create a second place to keep in sync for no benefit.

**`expected_revision` is a required keyword-only argument on `replace_followups`, never
defaulted.** The server requires it and the failure it prevents is a **second execution**
of already-typed text, not a lost update. A default value would be the client silently
choosing when re-execution is acceptable.

**One composed helper, because this is where the CAS discipline belongs:**

```python
def edit_followups(self, name, mutate, *, attempts: int = 3) -> FollowupQueue:
    """GET -> mutate(items) -> PUT with the observed revision; retry on 409.

    The revision-mismatch loop written once, correctly. `mutate` receives the
    current items and returns the new list. On 409 the queue is re-read and
    `mutate` re-applied to the FRESH items -- never the same body retried.
    """
```

This is the same judgment as `run_shell_command()` (`sync_client.py:222`): a documented
loop that is easy to get wrong, composed from primitives the caller can still use
directly. `run_shell_command`'s docstring says "rebuild it yourself from those if this
shape does not fit" — say the same here.

### 7.4 `SessionSnapshot` parity (depends on item C)

Once C lands, add `created_at`, `followups`, `views`, and `cwd` to `SessionSnapshot`, all
defaulted, parsed with `.get()` in `parse_session_snapshot()` (`_protocol.py:85-92`). New
dataclass fields need defaults — the existing three (`lines`, `bell`,
`last_activity_at`) are positional and must keep their order.

Add `cwd: str | None = None` to `Session` in the same pass.

### 7.5 Version lockstep

`test_client_contract.py:432` asserts `client/pyproject.toml`'s `version` equals the
repo root's. It is a **manual** discipline with a same-PR tripwire — nothing derives one
from the other. D changes the client's public surface, so the release that ships it bumps
both. Per `AGENTS.md`, **do not bump versions in the feature PR**; the owner does it at
release time. Note the requirement in the PR description so it is not missed.

D adds **no new mirrored constants.** `MIN_SERVER_VERSION` stays at `0.18.0` —
`check_server()` is opt-in and never called automatically, and raising it would turn a
provenance marker into a runtime gate for callers who don't need the new methods.

### 7.6 Tests

**`client/tests/test_protocol.py`** (pure, no network) — parsing of every new shape,
including the version-tolerance cases: a `GET /api/sessions` entry with **no**
`followups` key parses to `Followups()`; a `FollowupQueue` with `halted: null` parses to
`halted is None`; `SessionSnapshot` from a pre-C server parses with all four new fields
at their defaults.

**`muxplex/tests/test_client_contract.py`** (drives the real ASGI app in-process) — this
is the file that makes shipping a second distribution acceptable at all. Add:

1. `test_create_session_command_id_reaches_server` — `command_id="default"` round-trips
   and the response echoes it.
2. `test_create_session_without_command_id_sends_no_key` — assert the request body has no
   `command_id` key. This is the byte-identity claim; assert it, don't assume it.
3. `test_delete_session_force_reaches_server` — `force=True` produces `?force=true`.
4. `test_session_commands_fields_present` — same shape as the existing
   `test_sessions_fields_present:224`: client model vs raw JSON, so a server rename turns
   this red in the same PR.
5. `test_followups_round_trip` — append → read → replace with the observed revision →
   resume → clear, against the real app.
6. `test_followups_badge_parses_from_sessions` — a halted queue is visible via
   `client.sessions()[0].followups.halted`.
7. `test_edit_followups_retries_on_revision_conflict` — the helper re-reads rather than
   retrying the same body. Assert the **second** `PUT` carries the second revision.

### 7.7 Done when

`uv run pytest client/tests muxplex/tests/test_client_contract.py` is green inside the
DTU, `client/muxplex_client/__init__.py`'s `__all__` includes every new public name
(`Followups`, `FollowupItem`, `FollowupQueue`, `SessionCommand`, `SessionCommands`), and
a scratch script drives one full loop — create with `command_id`, append a follow-up,
observe the badge, resume, delete — through the typed client with **no raw `httpx`
anywhere**. That last one is the actual acceptance criterion; the rest is scaffolding.

---

## 8. Item E — delete `ensure_history_retention()`

**Files:** `muxplex/sessions.py`, `muxplex/main.py` (docstring only),
`docs/AGENT_GUIDE.md`, `muxplex/tests/test_sessions.py`.
**Contract change:** none on the wire. Three documents stop claiming something false.

### 8.1 What it does and does not do

Runtime-proven in §1.3: `set-option -t <session> history-limit N` does not change an
existing pane's limit. `spawn_session_command()` calls it at `sessions.py:653`, **after**
the template has created the session and its pane. A pane created on a host with the
compiled default evicted at ~2000 rows despite the call having "raised" it to 5,000.

The one real effect: **windows created inside that session afterward inherit the value.**
On a `muxplex tmux install` host that means 50000 → 5000, a 10× reduction. On an
unmanaged host it means 2000 → 5000 for later windows only. Neither is what any of the
three documents describe, and the first is actively harmful.

### 8.2 Fix: delete, do not repair

Remove `SESSION_HISTORY_LIMIT` (`sessions.py:452-462`), `ensure_history_retention()`
(`sessions.py:486-509`), and the call site (`sessions.py:650-653`).

Retention policy already lives — and correctly applies — in the managed tmux config
(`base.conf:28`), which is evaluated at pane creation, the only moment it can work.

**Explicitly rejected, and someone will propose it: `tmux set-option -g history-limit N`
from the server at startup.** It would work. It is wrong for this repo.
`tmux_config.py`'s module docstring states the posture in as many words — *"This is
deliberately the opposite of the conda/rustup/nvm convention: they install last because
they want to win. We install first because we want to lose."* A runtime `set -g` would
silently outrank the user's own `~/.tmux.conf`. This is the same rejection recorded in
`2026-08-07-scrollback-paging-plan.md` §1.4; it should not be re-litigated per-PR.

### 8.3 The three promise sites

| Site | Current claim | Replacement |
|---|---|---|
| `sessions.py:452-462` | "tmux `history-limit` applied to every session muxplex creates … set well above `MAX_CAPTURE_LINES`" | Deleted with the constant. `MAX_CAPTURE_LINES`'s own comment (`:444-451`) stays — it is about request cost, not retention, and is still true. |
| `main.py:1415` (`get_session_snapshot` docstring) | "Sessions are created with their tmux `history-limit` raised well above `MAX_CAPTURE_LINES` … so a max-depth request has real backing data" | "Retention is whatever the host's tmux config provides — `history-limit 50000` under `muxplex tmux install` (`tmux_templates/base.conf:28`), tmux's compiled-in **2000** otherwise. On an unmanaged host that equals `MAX_CAPTURE_LINES` exactly, so the deepest legal request sits on the retention boundary and tmux clamps **silently**. muxplex does not set `history-limit` and cannot: the option binds a pane at creation time." |
| `AGENT_GUIDE.md:972-975` | "Sessions also get their tmux `history-limit` raised to 5000 on creation specifically so a max-depth request has real scrollback behind it" | Agent-facing version of the same, plus the operator remedy: *if you need deeper scrollback than 2000 lines, the fix is `muxplex tmux install` or raising `history-limit` in your own `~/.tmux.conf` — not a request parameter.* |

All three must land in the **same** commit as the deletion. A merge that removes the code
and leaves any one of them is worse than the status quo: the false claim survives with
nothing left to point at.

### 8.4 Deliberately NOT in E: `history_limit` on the wire

The paging plan's §1.4 proposes surfacing `history_limit` per session as "the honest
substitute." It is right, and it is **paging Phase 2** (`2026-08-07-scrollback-paging-plan.md`
§4), not this item. Three reasons to hold it there:

1. `history_limit` **alone** tells an agent how deep the wall is but not how close it is.
   The signal only becomes actionable next to `history_size` and the derived `saturated`,
   which arrive together in Phase 2.
2. A field on `/api/sessions` is permanent. Shipping half a signal now and completing it
   later means two contract additions where one would do.
3. Splitting ownership of one field across two plans is how the drift in items A and B
   happened.

E's job is to stop lying. Phase 2's job is to start telling the truth in detail. Keeping
them separate is what makes E a same-day merge.

### 8.5 Tests

**Delete:**
- `test_sessions.py:469` `test_ensure_history_retention_calls_tmux_set_option`
- `test_sessions.py:483` `test_ensure_history_retention_swallows_tmux_failure`
- `test_sessions.py:494` `test_session_history_limit_exceeds_max_capture_lines`

All three test a function that never worked. Deleting them alongside it is correct; do not
"preserve coverage."

**Simplify:** four tests mock `ensure_history_retention` away specifically because it made
its own unrelated `create_subprocess_exec` call — `test_sessions.py:704-730`, `:746-769`,
`:819`, and their docstrings explaining the mock. Remove the patches and the explanatory
docstring paragraphs. These tests get *simpler*, which is the tell that the deletion is
right.

**Add one guard:**

```python
def test_muxplex_never_sets_history_limit():
    """history-limit binds a pane at creation; muxplex must not pretend otherwise.

    Runtime-measured on tmux 3.4: `set-option -t <s> history-limit 5000` on a
    live session left the pane at 2000 and evicted at ~2000 after 4000 lines
    of output -- the exact failure the removed code claimed to prevent. See
    docs/plans/2026-08-07-agent-surface-additive-plan.md §1.3.

    Guards against a future "fix" that reintroduces the call, or the rejected
    `set-option -g` variant (see that plan's §8.2 and tmux_config.py's
    install-first-so-we-lose posture).
    """
    source = (Path(__file__).parent.parent / "sessions.py").read_text(encoding="utf-8")
    assert "history-limit" not in source
```

One assertion on one file, guarding a decision with a measured rationale. Same narrow
class as §5.4.

**Integration (real tmux, `-L` scratch socket):** create a session via
`spawn_session_command()` on a server started with `-f /dev/null` and assert
`#{history_limit}` is **2000**, not 5000. This is the assertion that would have caught the
bug originally, and it is the paging plan's evidence item 10.

### 8.6 Done when

`grep -rn "ensure_history_retention\|SESSION_HISTORY_LIMIT" muxplex/ docs/` returns
nothing outside `CHANGELOG.md` and plan documents; the three replacement texts are in;
`make test` is green; the integration test reports 2000. Then mark
`2026-08-07-scrollback-paging-plan.md`'s Phase 0 row as delivered by this work so paging
does not do it twice.

---

## 9. Contract discipline (applies to C and D)

- **Additive only.** C adds one field to two shapes and three parity fields to a third. No
  existing key is renamed, removed, or redefined. `lines` in particular keeps meaning
  "depth requested."
- **Version tolerance in both directions.** A pre-C client ignores the new keys. A post-D
  client against a pre-C server parses every new field to its default — this is what the
  `.get()` discipline in `_protocol.py:9-11` buys, and D's new dataclass fields must all
  carry defaults for it to hold.
- **Lockstep.** `muxplex-client` and server versions move together;
  `test_client_contract.py:432` is the same-PR tripwire. Version bumps happen at release
  time, by the owner (`AGENTS.md`, "Testing & workflow").
- **No new mirrored constants.** Nothing here adds one. `MAX_CAPTURE_LINES` /
  `DEFAULT_CAPTURE_LINES` / `KNOWN_KEYS` / `MAX_KEYS` are untouched, so
  `test_client_contract.py:306-316` needs no companions.
- **Resolve rules server-side.** C's `views` parity field is produced by the existing
  `annotate_view_membership`, not by a second implementation. `AGENTS.md`'s standing
  answer.

---

## 10. Evidence requirements

Per `AGENTS.md`: **never run the suite on a host serving a live muxplex.** `make test`
(DTU). Any test touching a real tmux server layers its own `-L <unique-name>` socket on
top of conftest's autouse `TMUX_TMPDIR` isolation, as `test_integration.py::tmux_server`
already does. No `pkill`, no bare `kill-server`. Commit locally *before* the DTU run so
`git archive HEAD` tests the artifact that would be pushed.

| # | Item | Requirement |
|---|---|---|
| 1 | A | `grep -c followup docs/AGENT_GUIDE.md` > 0; §6.5 exists; every `curl` example's output captured from a live scratch instance, not written by hand |
| 2 | A | The `bell_hook_unarmed` → `POST /api/internal/setup-hooks` recovery reproduced end-to-end before it is printed as instruction (§1.4) |
| 3 | B | `grep -c "terminal_conflict\|takeover=true" docs/AGENT_GUIDE.md` → 0 |
| 4 | B | New doc test fails against the pre-change doc, passes after |
| 5 | B | §3 example key set diffed against a real `GET /api/sessions` response |
| 6 | B | `frontend/tests/test_terminal.mjs` unchanged; `node --test frontend/tests/*.mjs` still green |
| 7 | C | `cwd` present and non-null on a real bulk read against a scratch instance, showing a real path |
| 8 | C | Integration: session created in a known directory reports that directory |
| 9 | C | Key-set parity test between `GET /api/sessions/{name}` and the bulk entry |
| 10 | C | `GET /api/view` verifiably still has no `cwd` (the exclusion is pinned, not assumed) |
| 11 | C | §1.4's TUI and `amplifier-workspace` measurements taken, and the docstring wording matches what they showed |
| 12 | D | Full loop — create with `command_id`, append, observe badge, resume, delete — through the typed client with zero raw `httpx` |
| 13 | D | `edit_followups` retry test asserts the second `PUT` carries the **second** revision |
| 14 | D | Pre-C-server parse test: `SessionSnapshot` with all four new fields at defaults |
| 15 | E | Integration: session created against `tmux -f /dev/null` reports `history_limit=2000`, not 5000 |
| 16 | E | `grep -rn "ensure_history_retention\|SESSION_HISTORY_LIMIT" muxplex/ docs/` clean outside CHANGELOG/plans |
| 17 | E | The four `ensure_history_retention` mock-patches in `test_sessions.py` removed, suite still green |
| 18 | all | `make test` green; `node --test frontend/tests/*.mjs` green (C touches `main.py`, which the PWA reads) |

---

## 11. Summary

- **A and B are pure documentation and land first, in parallel with E.** B is the only
  item that removes active harm: agents are being told to write dead error handling.
- **C publishes a value the server already computes every poll cycle**, on two endpoints
  and one federation branch — and deliberately **not** on `GET /api/view`, which stays a
  cheap display resolution. It also closes a real parity hole: a polling agent currently
  cannot see a halted follow-up queue from the single-session read.
- **D adds no wire surface.** It is the typed client catching up to `command_id`, `force`,
  and a five-endpoint feature it has zero awareness of.
- **E is a deletion, runtime-proven.** `history-limit` binds a pane at creation; the call
  runs after. Measured: a pane "raised" to 5,000 evicted at ~2,000. Three documents stop
  claiming otherwise, and the scrollback paging work is unblocked.
- **The audit was right on four claims and overstated two** — `GET /api/views` is
  mentioned in the guide (incompletely, not absent), and the frontend's
  `terminal_conflict` handling is correct version tolerance, not drift.
- **One field is deliberately deferred:** `history_limit` on the wire belongs to paging
  Phase 2, next to `history_size` and `saturated`, where it becomes actionable rather
  than half a signal on a permanent contract.

# Finding — the sidecar calls every well-formed tool continuation "broken"

**Work item:** muxplex-2nm · **Date:** 2026-08-15 · **Status:** root cause identified, fix recommended, not applied (investigation lane)

**Fix tracked as muxplex-3aw** — filed separately because the change lands in
`amplifier-agent`, not in this repo. No muxplex file needs to change.

## Verdict

**The sidecar owns this defect. The panel is correct and needs no change.**

`amplifier_agent_http._reconciler` judges an *in-flight chat-completions request*
with a health model written for a *stored, at-rest transcript*. The two contracts
disagree about exactly one thing: whether a transcript is allowed to end on a tool
result.

- At rest, a transcript ending on a `role=tool` message means the process died
  before the model answered. That is genuinely broken and worth repairing.
- On the wire, a request ending on a `role=tool` message is a **tool-call
  continuation** — the client has run the tools and is asking for the closing
  assistant response. That is not just legal, it is the only legal shape.

The reconciler applies the first rule to the second case, on every continuation
turn, forever. It then fabricates the missing assistant response and writes it to
the session store.

`chat.js`'s `index:id` workaround for the HTTP face's `index: 0` bug is **not**
involved. See "The lead that was ruled out".

## What the warning actually says

The bug report quoted only the first sentence. The full log line carries the
diagnosis, and it is unambiguous:

```
WARNING:amplifier_agent_http._reconciler:Client-sent transcript was broken — repaired
before reconcile. failure_modes=['incomplete_assistant_turn'] orphaned_tool_ids=[]
misplaced_tool_ids=[] incomplete_turns=1 entries_before=4 entries_after=5
session=http-chat-msu7y069-co53smc9xyp
```

Across **every** occurrence in the sidecar journal, without exception:

| field | value | meaning |
|---|---|---|
| `failure_modes` | `['incomplete_assistant_turn']` | never anything else |
| `orphaned_tool_ids` | `[]` | every tool call has its result |
| `misplaced_tool_ids` | `[]` | every result is correctly ordered |
| `entries_after` | `entries_before + 1` | the repair **appends** one message |

`orphaned_tool_ids=[]` alone acquits the panel of the accusation in the item:
the client's tool-call/tool-result pairing is flawless. The only thing the
diagnoser objects to is that the answer the request exists to obtain has not been
written yet.

## The mechanism, in source

`amplifier_foundation/session/diagnosis.py`, failure mode 3
(`diagnose_transcript`, lines 197–222):

```python
# Check what comes after the last tool result
next_idx = last_result_idx + 1
if next_idx >= len(entries):
    # End of transcript — incomplete if we're missing the closing response
    incomplete_turns.append({... "missing": "assistant_response"})
```

"End of transcript" is the entire condition. A continuation request *always* ends
on its last tool result, so this *always* fires.

`repair_transcript` step 6 then appends a fabricated turn
(`_make_synthetic_assistant_response`, line 255), whose content is a fixed string
defined at line 52:

> `The previous tool calls were interrupted. This response was automatically repaired.`

That sentence is false in this situation. Nothing was interrupted.

## The sidecar contradicts itself inside one request

The decisive evidence is that the sidecar's own request parser classifies the very
same payload correctly, one line earlier in the same journal, for the same request:

```
15:46:51 INFO  ...chat_completions:continuation turn: last message is role=tool
               (tool_call_id=toolu_012yicRT73aXQa4EUF8txhnY); passing full history with empty prompt
15:46:51 WARN  ..._reconciler:Client-sent transcript was broken — repaired before reconcile.
               failure_modes=['incomplete_assistant_turn'] orphaned_tool_ids=[] misplaced_tool_ids=[]
               incomplete_turns=1 entries_before=4 entries_after=5
               session=http-sidecar-lane-repro-1786808809
15:46:51 INFO  ...chat_completions:chat-completion start chunk_id=chatcmpl-d5e2784bbfe54880b2676094
               history_len=4 prompt_chars=0 ... client_session_id='sidecar-lane-repro-1786808809'
```

`chat_completions.py:244` calls it *"continuation turn … passing full history"* at
INFO. `_reconciler.py:82` calls it *"broken"* at WARNING. Both are describing
`payload.messages` of the same POST. One of them is wrong, and it is the second:
`_split_history_and_prompt`'s docstring documents `role=tool`-terminated arrays as
a first-class supported case (Case 1, lines 230–248).

## Blast radius — measured, not assumed

**The model never sees the fabricated message.** The return value of
`reconcile_client_history(...)` is **discarded** at the call site
(`chat_completions.py:872`, no assignment), and the kernel context is seeded from
`history`, which comes from the *client's* messages
(`_session_runner.py:413–421`; the code comment at line 399 says so outright:
*"here every POST reseeds from the CLIENT's history"*). The third journal line
above confirms it numerically: `history_len=4` matches `entries_before=4`, not
`entries_after=5`.

**The session store is corrupted.** `store.save()` receives the repaired list. The
on-disk transcript for the live panel session named in the original bug report:

```
1 | system    | You are a small assistant embedded in a muxplex dashboard …
2 | user      | Type "hello from the debug repro" into the counter session.
3 | assistant | tool_calls=1
4 | tool      | [{"name":"counter", …}]
5 | assistant | Confirmed the "counter" session exists. Sending the input now.
6 | tool      | {"error":"POST /api/sessions/counter/input failed: HTTP 403 …"}
7 | assistant | [{"type":"text","text":"The previous tool calls were interrupted.
                 This response was automatically repaired."}]   ← fabricated
```

Two things are wrong with entry 7. It asserts an interruption that did not happen,
and it sits exactly where the model's real closing answer belongs — that answer is
never persisted at all, because the client has no reason to POST again after it
arrives. Any consumer of this store (context-intelligence, session forking,
`amplifier-agent` resume, a human debugging an incident) reads a fabricated
account of the conversation.

So: **not user-visible, not model-visible, but it silently falsifies the durable
record** — and it burns a WARNING on every continuation turn, which is precisely
the signal that would otherwise announce a *real* transcript defect.

## Evidence

### 1. Isolating experiment — `probe_diagnose_transcript.py`

Run against the sidecar's own installed `amplifier_foundation`, feeding it the
exact message shapes `chat.js` builds (`chat.js:1041`, `:1212`, `:1248`):

| # | array | status | failure_modes | orphaned | before→after |
|---|---|---|---|---|---|
| R1 | `[system, user]` | **healthy** | `[]` | `[]` | — |
| R2 | `[system, user, A(tc), tool]` | **broken** | `incomplete_assistant_turn` | `[]` | 4→5 |
| R3 | `[system, user, A(tc1), tool, A(tc2), tool]` | **broken** | `incomplete_assistant_turn` | `[]` | 6→7 |
| R2‑par | `[system, user, A(2 tc), tool, tool]` | **broken** | `incomplete_assistant_turn` | `[]` | 5→6 |
| **control** | R2 **+ closing assistant text** | **healthy** | `[]` | `[]` | — |
| **control** | tool call with **no** result | **broken** | `missing_tool_results` | `['toolu_X']` | 3→5 |

Every `entries_before → entries_after` pair observed in the journal (4→5, 6→7,
7→8, 3→4) is reproduced exactly by this table. The first control is the crux:
**append the answer the request is asking for, and the identical array is
healthy.** The second control shows the diagnoser catches genuine breakage fine —
the problem is scope, not correctness.

### 2. Fresh end-to-end reproduction — `replay_panel_continuation.py`

Driven through muxplex's own proxy (`POST /api/agent/chat/completions`), using the
panel's real `MODEL`, `SYSTEM_PROMPT` and `TOOLS` extracted from `chat.js`, and
the panel's exact message-assembly logic including the `index:id` keying. R1's
real `tool_call_id` was carried into R2.

```
R1  POSTed 2 messages | finish_reason=tool_calls | chunk_id=chatcmpl-684fe2a3b794429cbc0cc9c4
R2  POSTed 4 messages | finish_reason=stop       | chunk_id=chatcmpl-d5e2784bbfe54880b2676094
client_session_id = sidecar-lane-repro-1786808809
```

R1 → no warning. R2 → the warning quoted above, correlated by both
`client_session_id` and `chunk_id`. The store written for that session ends with
the fabricated entry, and the model's real answer (`finish_reason=stop`) is absent
from it.

### 3. Journal survey

20+ occurrences sampled across nine distinct `client_session_id`s — synthetic test
sessions and real panel sessions (`chat-mstfddro-e89lebu606v`,
`chat-msu7y069-co53smc9xyp`, …). `failure_modes` is `['incomplete_assistant_turn']`
in 100% of them; `orphaned_tool_ids` and `misplaced_tool_ids` are empty in 100% of
them.

## The lead that was ruled out

The item asked whether `chat.js`'s `index:id` workaround for the HTTP face's
`index: 0`-for-all-parallel-calls bug is what the reconciler objects to.

**It is not.** Two independent lines of evidence:

1. If the workaround mis-keyed parallel calls, results would go missing or land
   out of order — surfacing as `missing_tool_results` / `ordering_violation` with
   non-empty `orphaned_tool_ids` / `misplaced_tool_ids`. Those lists are empty in
   every observed occurrence.
2. Row R2‑par of the experiment feeds a two-parallel-call assistant turn with both
   results present, exactly as the workaround produces them. `orphaned=[]`,
   `misplaced=[]` — the pairing is correct. It is flagged for the same trailing
   reason as the single-call case, and for no other.

The workaround is doing its job. Leave it alone (as `_SHARED.md` already requires).

## Recommended fix

**Owner: `amplifier-agent` (`amplifier_agent_http/_reconciler.py`). No muxplex
change.**

Suppress the *trailing* incomplete turn only — the one whose `after_index` is the
last entry in the array. That is the case the wire protocol guarantees and the
request exists to resolve. Keep every other repair intact.

```python
diagnosis = diagnose_transcript(annotated)

# On the chat-completions wire a transcript that ends on a tool result is a
# CONTINUATION, not a casualty: the client ran the tools and is asking for the
# closing assistant response. Only the LAST entry gets this exemption --
# an incomplete turn anywhere earlier is still genuine breakage.
last_idx = len(annotated) - 1
if (
    diagnosis["failure_modes"] == ["incomplete_assistant_turn"]
    and diagnosis["incomplete_turns"]
    and all(t.get("after_index") == last_idx for t in diagnosis["incomplete_turns"])
):
    diagnosis = {**diagnosis, "status": "healthy", "failure_modes": [],
                 "incomplete_turns": [], "recommended_action": "none"}

if diagnosis["status"] != "healthy":
    ...  # unchanged
```

Why this is safe in the one case that looks risky: a client resuming a genuinely
interrupted conversation also sends an array ending on a tool result. The two are
**indistinguishable on the wire** — and they want the same outcome, namely "run
the model and produce the closing response." Fabricating a turn that says the
tools were interrupted is the wrong answer to both.

Alternative, cleaner but crossing a repo boundary: give
`amplifier_foundation.session.diagnose_transcript` an explicit
`expect_closing_response: bool = True` parameter, and have the HTTP face pass
`False`. That puts the contract where the rule lives instead of post-filtering the
result. Preferred if `amplifier-foundation` is in scope for the fixer.

**Rejected: suppressing the log line.** The item forbids it, and it would leave the
store corruption in place while disabling the alarm that reports real breakage.

### Verifying the fix

1. `probe_diagnose_transcript.py` — rows R2, R3, R2‑par flip to `healthy`; both
   controls keep their current verdicts (the orphan control **must** still be
   `broken`/`missing_tool_results`).
2. `replay_panel_continuation.py` — no `transcript was broken` line for the new
   `client_session_id`; the `continuation turn: last message is role=tool` INFO
   line still appears.
3. The written store for that session no longer contains
   `"The previous tool calls were interrupted."`
4. Then repeat via the real panel in a browser and correlate the exported record's
   `client_session_id`/`chunk_id` against the journal (see caveat below).

## Discovered separately — not fixed here

Even with this fix, the persisted transcript will still be **one assistant turn
short**: the store only ever receives what the client POSTed, and the client never
POSTs again after the final answer arrives. The model's closing response is never
written to disk for any conversation. Filed as discovered work
(**muxplex-c1x**, discovered-from muxplex-2nm).

## Caveat on evidence

The fresh reproduction (§2) was driven through muxplex's own proxy rather than the
panel's Export button in a real browser. At the time of this run another lane held
the browser's foreground tab; `browser_click`/`browser_type` against the
backgrounded muxplex tab timed out at 120 s (Chrome throttles DOM injection in
background tabs), and the documented workaround — `activate=true` — would have
stolen the other lane's focus.

This is stated rather than papered over. What it does and does not weaken:

- The claim under investigation is about a **server-side log line and an on-disk
  file**, not about anything rendered to a user, so the project's "curl is not
  proof" rule is not being stretched to cover a UI claim.
- Real-browser evidence for the same defect already exists independently: the
  journal occurrences under `client_session_id`s in the panel's own
  `chat-<ts>-<rand>` format, and the polluted store for
  `http-chat-msu7y069-co53smc9xyp` quoted above, were produced by the panel in a
  browser, not by this replay.
- Step 4 of "Verifying the fix" should still be performed by the lane that applies
  the fix, when the browser is free.

## Files

- `2026-08-15-sidecar-transcript-repair/probe_diagnose_transcript.py` — isolating
  experiment; run with the sidecar's interpreter
  (`/home/aa-svc/.local/share/uv/tools/amplifier-agent/bin/python`) inside the DTU.
- `2026-08-15-sidecar-transcript-repair/replay_panel_continuation.py` — live
  reproduction; needs `/tmp/panel-consts.json` (`MODEL`/`SYSTEM_PROMPT`/`TOOLS`
  extracted from `chat.js`) and runs inside the DTU against `127.0.0.1:8088`.

## Source references

| What | Where |
|---|---|
| The warning | `amplifier_agent_http/_reconciler.py:79–93` |
| Return value discarded | `amplifier_agent_http/routes/chat_completions.py:872` |
| `role=tool` is a supported continuation | `amplifier_agent_http/routes/chat_completions.py:230–248` |
| Context seeded from client history | `amplifier_agent_http/_session_runner.py:399, 413–421` |
| Trailing-end detection (FM3) | `amplifier_foundation/session/diagnosis.py:197–222` |
| Fabricated text | `amplifier_foundation/session/diagnosis.py:52–63` |
| Repair appends it | `amplifier_foundation/session/diagnosis.py:341–351` |
| Panel builds the request | `muxplex/frontend/chat.js:1041` |
| Panel records assistant turn + one `role=tool` per call | `muxplex/frontend/chat.js:1212, 1248` |

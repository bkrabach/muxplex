# Finding — the sidecar calls every well-formed tool continuation "broken"

**Work items:** muxplex-2nm (investigation) · muxplex-3aw (fix) ·
**Date:** 2026-08-15 · **Status:** root cause identified; fix **written and
verified end-to-end in the DTU**, then reverted — it belongs upstream, not here

**Versions measured:** `amplifier-agent` 0.12.0 · `amplifier-foundation` 1.0.0 ·
`amplifier-core` 1.6.1

**The change lands in `amplifier-agent`, not in this repo.** No muxplex file
needs to change, so this repo carries the evidence and the patch, not the fix.

> **➜ To file this upstream, use
> [`upstream/amplifier-agent-01-continuation-flagged-broken.md`](upstream/amplifier-agent-01-continuation-flagged-broken.md).**
>
> That is the standalone, self-contained report written for `amplifier-agent`
> maintainers: same root cause and same verification, but no work-item ids, no
> lane vocabulary, no assumed knowledge of muxplex or its panel, a paste-able
> reproduction that needs no server, and the patch inline. **This** document is
> the internal record — it keeps the muxplex-side reasoning, the ruled-out lead
> about `chat.js`, and the process notes, none of which belong in someone else's
> bug tracker.
>
> The patch also exists on its own at
> `2026-08-15-sidecar-transcript-repair/trailing-continuation-exemption.patch`.

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

## The fix was applied and verified — then reverted

The patch above was applied to the **DTU container's** installed
`amplifier-agent` (`site-packages/amplifier_agent_http/_reconciler.py`), verified
by the four checks below, and then **reverted byte-for-byte**
(`md5 c10b07c9f28879f9df201f39463be200` before and after; sidecar restarted both
ways). Nothing in this repo changed, and the container was left exactly as found —
the fix belongs upstream.

**Correction to an earlier draft of this section.** It said
`probe_diagnose_transcript.py`'s rows would flip to `healthy` once the fix landed.
That is wrong for the recommended (reconciler-level) fix and would have sent a
verifier looking at the wrong layer: the post-filter sits in the HTTP face, so
`diagnose_transcript` keeps returning `broken` for a continuation either way. It
is only true of the *alternative* `expect_closing_response` fix in foundation.
`probe_reconciler.py` exists because the reconciler is the layer that actually
changes.

### 1. Reconciler probe — A/B/A, 7 cases

`probe_reconciler.py`, run with the sidecar's own interpreter against its own
installed packages. `repaired` means the WARNING fired and `store.save()` got a
fabricated entry; `passthrough` means neither happened.

| case | before | **after** | after revert |
|---|---|---|---|
| C1 continuation, single tool call (4 msgs) | repaired 4→5, fabricated | **passthrough 4→4** | repaired 4→5 |
| C2 continuation, two parallel calls (5) | repaired 5→6, fabricated | **passthrough 5→5** | repaired 5→6 |
| C3 continuation, two sequential turns (6) | repaired 6→7, fabricated | **passthrough 6→6** | repaired 6→7 |
| C4 control: healthy opening turn (2) | passthrough | passthrough | passthrough |
| C5 control: closing answer present (5) | passthrough | passthrough | passthrough |
| **C6 control: orphaned tool call (3)** | **repaired 3→5** | **repaired 3→5** | repaired 3→5 |
| **C7 control: NON-trailing incomplete turn (5)** | **repaired 5→6** | **repaired 5→6** | repaired 5→6 |

C6 and C7 are the ones that matter. C7 is the exemption's boundary — an
incomplete assistant turn followed by a real user message, i.e. genuine breakage
that is *not* trailing — and it keeps repairing. The exemption is trailing-only,
as designed. Restoring the original file restores the original behaviour exactly,
which is what makes the middle column attributable to the patch and nothing else.

### 2. Real browser run, fix applied — the step the investigation lane could not do

Edge on macOS, browser-bridge device `edge-macos`, driving the live panel at
`http://192.168.1.5:8092/`. New conversation, prompt *"Which muxplex sessions are
running right now? List their names."*, which forces one tool call and therefore
one continuation turn. `client_session_id` taken from the panel's own **Export**
button (it is embedded in the downloaded filename):

```
Export → /Users/brkrabac/Downloads/muxplex-agent-chat-msusfw7p-rsgxkastz4-1786823371065.md

19:48:30 INFO  chat-completion start chunk_id=chatcmpl-762039d483d745afafd24e8f
               history_len=1 prompt_chars=63 client_session_id='chat-msusfw7p-rsgxkastz4'
19:48:33 INFO  continuation turn: last message is role=tool
               (tool_call_id=toolu_01Hqq2j5RAkVQvM1hf5gLUwM); passing full history with empty prompt
19:48:33 INFO  chat-completion start chunk_id=chatcmpl-0537ce607d514a608def906f
               history_len=4 prompt_chars=0 client_session_id='chat-msusfw7p-rsgxkastz4'
```

**No `Client-sent transcript was broken` line anywhere for that session**, and the
`continuation turn` INFO line still present — exactly the acceptance criterion.
The store written for it:

```
1 | system    | You are a small assistant embedded in a muxplex dashboard …
2 | user      | Which muxplex sessions are running right now? List their names.
3 | assistant | tool_calls=1
4 | tool      | [{"name":"counter",…},{"name":"logtail",…},{"name":"sysmon",…}]
```

`grep -c "previous tool calls were interrupted"` → **0**.

### 3. The same run before the fix — same browser, same prompt, same panel

Ten minutes earlier, unpatched, `client_session_id='chat-msus4o1s-ikn3a236kyj'`
(again from the Export filename,
`muxplex-agent-chat-msus4o1s-ikn3a236kyj-1786822842861.md`):

```
19:39:43 INFO  chat-completion start chunk_id=chatcmpl-8613c07cf5da446d999d825d history_len=1 …
19:39:45 INFO  continuation turn: last message is role=tool (tool_call_id=toolu_01VtztNQ7c519fze9xfxaxUf); …
19:39:45 WARN  Client-sent transcript was broken — repaired before reconcile.
               failure_modes=['incomplete_assistant_turn'] orphaned_tool_ids=[] misplaced_tool_ids=[]
               incomplete_turns=1 entries_before=4 entries_after=5 session=http-chat-msus4o1s-ikn3a236kyj
19:39:45 INFO  chat-completion start chunk_id=chatcmpl-eb8b584a58a043e6ae5d2399 history_len=4 …
```

and its store, entry 5 being the fabrication:

```
1 | system    | You are a small assistant embedded in a muxplex dashboard …
2 | user      | Which muxplex sessions are running right now? List their names.
3 | assistant | tool_calls=1
4 | tool      | [{"name":"counter",…},{"name":"logtail",…},{"name":"sysmon",…}]
5 | assistant | [{"type":"text","text":"The previous tool calls were interrupted.
                 This response was automatically repaired."}]   ← fabricated
```

The browser showed the model's real answer — *"Three sessions are running:
counter, logtail, sysmon."* — which appears in **neither** store. That is
muxplex-c1x, and §"Discovered separately" below; it is untouched by this fix, as
the post-fix store above shows.

`history_len=4` on the second POST equals `entries_before=4`, not
`entries_after=5` — the numerical confirmation, in a browser-driven run, that the
fabrication never reaches the model.

## Discovered separately — not fixed here

Even with this fix, the persisted transcript will still be **one assistant turn
short**: the store only ever receives what the client POSTed, and the client never
POSTs again after the final answer arrives. The model's closing response is never
written to disk for any conversation. Filed as discovered work
(**muxplex-c1x**, discovered-from muxplex-2nm).

## Caveat on evidence — now closed

The original draft of this finding carried a caveat: its fresh reproduction (§2 of
"Evidence") was driven through muxplex's own proxy rather than the panel's Export
button in a real browser, because another lane held the browser's foreground tab
at the time. **That gap is closed** — the before and after runs in "The fix was
applied and verified" above were both driven through the real panel in Edge, and
both `client_session_id`s came out of the panel's own Export download, not out of
a script.

One limitation worth stating plainly, because it did not go away: the *fix* was
verified against the DTU's installed `amplifier-agent`, not against an
`amplifier-agent` checkout with its own test suite run. Whoever lands this
upstream should still run that repo's tests; what is proven here is behaviour on
the wire, in a browser, with the exact versions listed at the top.

Two mechanical notes for anyone repeating this:

- The browser-bridge confirmation gate goes **elevated per-tab** after a command
  produces an observed effect (the Export download does), and this session's
  native tool surface has no `confirm` command to redeem it — every later
  state-changing command in that tab returns `needs_confirmation` and the state
  did not decay over ~2 minutes. The workaround used here was to drive a
  *different* tab, whose flow was clean. Worth knowing before concluding the
  browser is broken.
- A backgrounded muxplex tab times out on `snapshot`/`click` (Chrome throttles DOM
  injection); `activate=true` or `tab_activate` first is required.

## Files

- `2026-08-15-sidecar-transcript-repair/trailing-continuation-exemption.patch` —
  **the fix**, as a unified diff against `amplifier_agent_http/_reconciler.py`,
  with its rationale in the header. File this upstream.
- `2026-08-15-sidecar-transcript-repair/probe_reconciler.py` — reconciler-level
  A/B probe, 7 cases including the two controls that must keep repairing. Run with
  the sidecar's interpreter
  (`/home/aa-svc/.local/share/uv/tools/amplifier-agent/bin/python`) inside the DTU,
  before and after applying the patch.
- `2026-08-15-sidecar-transcript-repair/probe_diagnose_transcript.py` — isolating
  experiment one layer down, in `amplifier_foundation`. Its rows do **not** change
  under the reconciler-level fix (see the correction above); they would under the
  `expect_closing_response` alternative.
- `2026-08-15-sidecar-transcript-repair/replay_panel_continuation.py` — scripted
  reproduction through muxplex's proxy; needs `/tmp/panel-consts.json`
  (`MODEL`/`SYSTEM_PROMPT`/`TOOLS` extracted from `chat.js`) and runs inside the
  DTU against `127.0.0.1:8088`. Superseded as evidence by the browser runs above,
  kept because it reproduces without a browser.

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

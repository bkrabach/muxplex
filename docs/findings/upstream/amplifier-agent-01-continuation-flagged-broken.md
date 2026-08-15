# amplifier-agent: the chat-completions face calls every tool continuation a broken transcript, and writes a fabricated assistant turn to the session store

**Component:** `amplifier_agent_http` (chat-completions HTTP face)
**Affected versions:** `amplifier-agent` 0.12.0 · `amplifier-foundation` 1.0.0 · `amplifier-core` 1.6.1
**Severity:** low user impact, high record integrity impact — the durable session
transcript is falsified, and a WARNING fires on every continuation turn, masking
the signal that would report a real transcript defect
**Fix:** included below, applied and verified against 0.12.0, then reverted

---

## Summary

`amplifier_agent_http._reconciler.reconcile_client_history` judges an **in-flight
chat-completions request** using a health model written for a **stored, at-rest
transcript**. The two contracts disagree about exactly one thing: whether a
transcript is allowed to end on a tool result.

- **At rest**, a transcript ending on a `role=tool` message means the process died
  before the model answered. That is genuinely broken and worth repairing.
- **On the wire**, a request ending on a `role=tool` message is a **tool-call
  continuation** — the client has executed the tools and is asking for the closing
  assistant response. That is not merely legal; it is the only legal shape for
  that request.

The reconciler applies the first rule to the second case. Because a continuation
request *always* ends on its last tool result, this fires on **every continuation
turn, in every conversation, forever**. It then fabricates the missing assistant
response and persists it.

The face contradicts itself inside a single request — see "The self-contradiction"
below.

## Impact

**The model never sees the fabricated message.** `reconcile_client_history`'s
return value is discarded at the call site (`routes/chat_completions.py:872`, no
assignment), and the kernel context is seeded from the client's own history
(`_session_runner.py:399, 413–421`; the comment there states it outright: *"here
every POST reseeds from the CLIENT's history"*). Live turns are correct.

**The session store is falsified.** `store.save()` receives the repaired list, so
the on-disk `transcript.jsonl` asserts an interruption that did not happen, in the
exact slot where the model's real answer belongs. Every consumer of that store —
`amplifier-agent run` resume (`amplifier_agent_lib/_runtime.py:453`, which replays
it into the kernel), `amplifier-agent doctor`
(`amplifier_agent_cli/admin/doctor.py:454`), and any human or tool reading a
transcript to reconstruct an incident — reads a fabricated account of the
conversation.

**The alarm is burned.** A WARNING on every continuation turn means the log line
that should announce genuine transcript breakage is indistinguishable from
routine traffic.

## Reproduction — self-contained, no server, no network, no API key

Runs against the installed package with a stub store. Substitute your own
interpreter path.

```python
"""Minimal reproduction. Run with the installed amplifier-agent's interpreter."""

import json
import logging

from amplifier_agent_http._reconciler import reconcile_client_history


class StubStore:
    saved = None

    def save(self, session_id, transcript, metadata):
        StubStore.saved = transcript


warnings = []
logging.getLogger("amplifier_agent_http._reconciler").addHandler(
    type("H", (logging.Handler,), {"emit": lambda s, r: warnings.append(r.getMessage())})(level=logging.WARNING)
)

TC = {"id": "toolu_1", "type": "function", "function": {"name": "list_things", "arguments": "{}"}}

CASES = {
    # A tool-call continuation: the client ran the tool and is asking for the
    # closing assistant response. This is the ONLY legal shape for that request.
    "continuation (should pass through)": [
        {"role": "user", "content": "list things"},
        {"role": "assistant", "content": "", "tool_calls": [TC]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": '["a","b"]'},
    ],
    # Control: genuinely broken -- a tool_call with no result. MUST still repair.
    "orphaned tool_call (should repair)": [
        {"role": "user", "content": "list things"},
        {"role": "assistant", "content": "", "tool_calls": [TC]},
    ],
}

for name, msgs in CASES.items():
    warnings.clear()
    StubStore.saved = None
    reconcile_client_history(client_messages=msgs, session_id="repro", store=StubStore())
    fabricated = [m for m in StubStore.saved if "were interrupted" in json.dumps(m.get("content", ""))]
    print(f"{name}")
    print(f"  warning fired : {bool(warnings)}")
    print(f"  entries       : {len(msgs)} in -> {len(StubStore.saved)} saved")
    print(f"  fabricated    : {len(fabricated)}")
    if warnings:
        print(f"  log           : {warnings[0][:150]}")
    print()
```

Observed output on 0.12.0, verbatim:

```
continuation (should pass through)
  warning fired : True
  entries       : 3 in -> 4 saved
  fabricated    : 1
  log           : Client-sent transcript was broken — repaired before reconcile. failure_modes=['incomplete_assistant_turn'] orphaned_tool_ids=[] misplaced_tool_ids=[]

orphaned tool_call (should repair)
  warning fired : True
  entries       : 2 in -> 4 saved
  fabricated    : 1
  log           : Client-sent transcript was broken — repaired before reconcile. failure_modes=['missing_tool_results'] orphaned_tool_ids=['toolu_1'] misplaced_tool_ids
```

The first block is the defect: a perfectly-formed continuation, every tool call
paired with its result (`orphaned_tool_ids=[]`, `misplaced_tool_ids=[]`), reported
broken and silently grown by one fabricated entry.

The second block is the control, and it is the reason this must not be fixed by
suppressing the warning: that path is doing real, necessary work.

## Root cause, in source

`amplifier_foundation/session/diagnosis.py`, failure mode 3, inside
`diagnose_transcript` (defined at `:127`):

```python
# Check what comes after the last tool result          # :200
next_idx = last_result_idx + 1
if next_idx >= len(entries):                            # :202
    # End of transcript — incomplete if we're missing the closing response
    incomplete_turns.append({... "missing": "assistant_response"})
```

`next_idx >= len(entries)` — "the transcript ends here" — is the entire condition.
A continuation request ends on its last tool result by construction, so this is
unconditionally true for every continuation.

`repair_transcript` (`:278`) then appends `_make_synthetic_assistant_response()`
(`:255`, appended at `:339` and `:351`), whose content is the fixed string at
`diagnosis.py:58`:

> `The previous tool calls were interrupted. This response was automatically repaired.`

That sentence is false in this situation. Nothing was interrupted.

`diagnose_transcript` is not wrong — it is correct for the at-rest case it was
written for. The defect is that the HTTP face applies it to a case with a
different contract, and the HTTP face is the layer that knows the difference.

## The self-contradiction

The face's own request parser classifies the identical payload correctly, one
line earlier in the same journal, for the same request:

```
INFO  amplifier_agent_http.chat_completions: continuation turn: last message is role=tool
      (tool_call_id=toolu_01VtztNQ7c519fze9xfxaxUf); passing full history with empty prompt
WARN  amplifier_agent_http._reconciler: Client-sent transcript was broken — repaired before reconcile.
      failure_modes=['incomplete_assistant_turn'] orphaned_tool_ids=[] misplaced_tool_ids=[]
      incomplete_turns=1 entries_before=4 entries_after=5 session=http-chat-msus4o1s-ikn3a236kyj
INFO  amplifier_agent_http.chat_completions: chat-completion start
      chunk_id=chatcmpl-eb8b584a58a043e6ae5d2399 history_len=4 prompt_chars=0
```

`chat_completions.py:246` calls it *"continuation turn … passing full history"* at
INFO. `_reconciler.py:82` calls it *"broken"* at WARNING. Both describe
`payload.messages` of the same POST. `_split_history_and_prompt`'s own docstring
documents `role=tool`-terminated arrays as a first-class supported case
(`chat_completions.py:230`, "Case 1: continuation").

`history_len=4` on the third line equals `entries_before=4`, not
`entries_after=5` — numerical confirmation that the fabrication is persisted but
never reaches the model.

## Fix

```diff
--- a/amplifier_agent_http/_reconciler.py
+++ b/amplifier_agent_http/_reconciler.py
@@ -76,6 +76,27 @@ def reconcile_client_history(
         annotated = [{**m, "line_num": i + 1} for i, m in enumerate(client_messages)]
         diagnosis = diagnose_transcript(annotated)
 
+        # A transcript that ends on a tool result is a CONTINUATION on this
+        # wire, not a casualty: the client has run the tools and is asking for
+        # the closing assistant response. `diagnose_transcript` cannot know
+        # that -- its failure mode 3 fires on `next_idx >= len(entries)`, which
+        # is true of every continuation request by construction -- so the HTTP
+        # face, which does know, waives it here.
+        #
+        # TRAILING ONLY. An incomplete assistant turn anywhere earlier in the
+        # array is real breakage and still repairs. A client resuming a
+        # genuinely interrupted conversation sends an indistinguishable array
+        # and wants the same outcome -- run the model, produce the closing
+        # response -- so the exemption is correct in that case too.
+        last_idx = len(annotated) - 1
+        if (
+            diagnosis["failure_modes"] == ["incomplete_assistant_turn"]
+            and diagnosis["incomplete_turns"]
+            and all(t.get("after_index") == last_idx for t in diagnosis["incomplete_turns"])
+        ):
+            diagnosis = {
+                **diagnosis,
+                "status": "healthy",
+                "failure_modes": [],
+                "incomplete_turns": [],
+                "recommended_action": "none",
+            }
+
         if diagnosis["status"] != "healthy":
             repaired = repair_transcript(annotated, diagnosis)
             client_messages = [{k: v for k, v in m.items() if k != "line_num"} for m in repaired]
```

**The exemption is trailing-only by construction.** It fires only when
`incomplete_assistant_turn` is the *sole* failure mode and *every* incomplete turn
sits at the last index. An incomplete turn anywhere earlier, or any other failure
mode alongside it, still repairs exactly as before.

**The one case that looks risky is not.** A client resuming a genuinely
interrupted conversation also sends an array ending on a tool result. The two are
indistinguishable on the wire — and they want the same outcome: run the model and
produce the closing response. Fabricating a turn that claims the tools were
interrupted is the wrong answer to *both*.

**Preferred alternative if `amplifier-foundation` is also in scope for you:** give
`diagnose_transcript` an explicit `expect_closing_response: bool = True`
parameter and have the HTTP face pass `False`. That puts the contract where the
rule lives instead of post-filtering the result, and removes the need for the
caller to know the shape of the diagnosis dict. The patch above was chosen only
because it is confined to one repository.

**Please do not fix this by suppressing the log line.** That leaves the store
falsified and disables the alarm that reports real breakage.

## Verification performed

The patch was applied to an installed 0.12.0, exercised, and then reverted
byte-for-byte (`md5 c10b07c9f28879f9df201f39463be200` before and after, sidecar
restarted both ways).

### Reconciler probe, 7 cases, three states

`repaired` = the WARNING fired and a fabricated entry was persisted.
`passthrough` = neither happened.

| case | before | **patched** | after revert |
|---|---|---|---|
| continuation, single tool call (4 msgs) | repaired 4→5 | **passthrough 4→4** | repaired 4→5 |
| continuation, two parallel calls (5) | repaired 5→6 | **passthrough 5→5** | repaired 5→6 |
| continuation, two sequential turns (6) | repaired 6→7 | **passthrough 6→6** | repaired 6→7 |
| control: healthy opening turn (2) | passthrough | passthrough | passthrough |
| control: closing answer already present (5) | passthrough | passthrough | passthrough |
| **control: orphaned tool call (3)** | **repaired 3→5** | **repaired 3→5** | repaired 3→5 |
| **control: NON-trailing incomplete turn (5)** | **repaired 5→6** | **repaired 5→6** | repaired 5→6 |

The last two rows are the ones that matter. The final row is the exemption's
boundary — an incomplete assistant turn followed by a real user message, i.e.
genuine breakage that is not trailing — and it keeps repairing, unchanged.
Restoring the original file restores the original behaviour exactly, which is what
makes the middle column attributable to the patch and nothing else.

### End-to-end, through a real client

Driven through a browser-based OpenAI-compatible client that executes tools
client-side and re-POSTs the full history, against a live
`amplifier-agent serve chat-completions`. Same prompt before and after, one tool
call and therefore one continuation turn.

**Before** (`client_session_id='chat-msus4o1s-ikn3a236kyj'`) — the journal excerpt
quoted under "The self-contradiction" above, and its stored transcript:

```
1 | system    | …
2 | user      | Which sessions are running right now? List their names.
3 | assistant | tool_calls=1
4 | tool      | [{"name":"counter",…},{"name":"logtail",…},{"name":"sysmon",…}]
5 | assistant | [{"type":"text","text":"The previous tool calls were interrupted.
                 This response was automatically repaired."}]   ← fabricated
```

**After** (`client_session_id='chat-msusfw7p-rsgxkastz4'`):

```
19:48:30 INFO  chat-completion start chunk_id=chatcmpl-762039d483d745afafd24e8f history_len=1 prompt_chars=63
19:48:33 INFO  continuation turn: last message is role=tool (tool_call_id=toolu_01Hqq2j5RAkVQvM1hf5gLUwM)
19:48:33 INFO  chat-completion start chunk_id=chatcmpl-0537ce607d514a608def906f history_len=4 prompt_chars=0
```

No `Client-sent transcript was broken` line anywhere for that session, and the
`continuation turn` INFO line still present. Its stored transcript:

```
1 | system    | …
2 | user      | Which sessions are running right now? List their names.
3 | assistant | tool_calls=1
4 | tool      | [{"name":"counter",…},{"name":"logtail",…},{"name":"sysmon",…}]
```

`grep -c "previous tool calls were interrupted"` → `0`.

### What was NOT verified

This verifies behaviour on the wire against an installed 0.12.0. It does **not**
include a run of this repository's own test suite — that was not available to the
reporter. Please run it before merging.

## A separate defect, visible in the "after" transcript above

Note that the fixed transcript ends on the tool result: the model's real closing
answer — which the client displayed — is not in the store either. That is a
distinct defect with a distinct cause (there is no end-of-turn write at all), and
it is filed separately as
[amplifier-agent-02-final-assistant-turn-not-persisted.md](amplifier-agent-02-final-assistant-turn-not-persisted.md).

The two compose badly and are best landed together, **this one first**: it is a
post-filter, the other needs a new write path. Fixing only this one leaves the
store ending one turn short; fixing only the other leaves a fabricated turn and
the real one adjacent, contradicting each other.

## Source references

| What | Where |
|---|---|
| The warning | `amplifier_agent_http/_reconciler.py:82–93` |
| Diagnosis call to patch | `amplifier_agent_http/_reconciler.py:77` |
| The only `store.save()` in the face | `amplifier_agent_http/_reconciler.py:95` |
| Return value discarded at the call site | `amplifier_agent_http/routes/chat_completions.py:872` |
| `role=tool` documented as supported ("Case 1: continuation") | `routes/chat_completions.py:230` |
| The contradicting INFO line | `routes/chat_completions.py:246` |
| Context seeded from client history, not the store | `amplifier_agent_http/_session_runner.py:399, 413–421` |
| Trailing-end detection (failure mode 3) | `amplifier_foundation/session/diagnosis.py:200–202` |
| `diagnose_transcript` | `amplifier_foundation/session/diagnosis.py:127` |
| Fabricated text constant | `amplifier_foundation/session/diagnosis.py:58` |
| `repair_transcript` appends it | `amplifier_foundation/session/diagnosis.py:278, 339, 351` |
| Store consumer: CLI resume | `amplifier_agent_lib/_runtime.py:453` |
| Store consumer: `doctor` | `amplifier_agent_cli/admin/doctor.py:454` |

Every line number above was re-verified by `grep` against the installed 0.12.0
after this report was written.

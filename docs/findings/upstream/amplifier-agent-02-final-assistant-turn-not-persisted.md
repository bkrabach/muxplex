# amplifier-agent: the chat-completions session store never records the model's answer to the last turn

**Component:** `amplifier_agent_http` (chat-completions HTTP face)
**Affected versions:** `amplifier-agent` 0.12.0 · `amplifier-foundation` 1.0.0 · `amplifier-core` 1.6.1
**Severity:** low user impact, high record integrity impact — every persisted
transcript ends one assistant turn short, and the CLI resume path replays it
**Fix:** recommended below with insertion points and conditions; **not applied** —
unlike the companion report, this one needs a new write path rather than a
post-filter, and the design decision is yours

---

## Summary

The chat-completions face has **exactly one** `store.save()` call site, and it
runs at the *start* of a request, on the *client's* posted array:

```
$ grep -rn "\.save(" site-packages/amplifier_agent_http/
site-packages/amplifier_agent_http/_reconciler.py:95:    store.save(
```

`reconcile_client_history` is invoked from `routes/chat_completions.py:872`,
before the model has produced anything. **Nothing writes to the store at end of
turn.** The persisted transcript is therefore a mirror of what the client last
sent — never a record of what the server last said.

The stored metadata says so in as many words. Every session's `metadata.json`, in
full:

```json
{
  "last_turn": "client_reconciled"
}
```

There is no other value it can take, because there is no other writer.

## The precise behaviour: a one-turn lag

It would be easy to describe this as "assistant turns are missing from the
store," which would send you looking for a lossy serializer. That is not what
happens. What happens is a **one-turn lag**:

| after… | store holds | answer to turn 1 |
|---|---|---|
| turn 1 completes | `[system, user1, assistant(tool_calls), tool]` | **absent** |
| client POSTs turn 2 | `[system, user1, assistant(tool_calls), tool, **assistant1**, user2]` | **present** |
| turn 2 completes | unchanged — still ends at `user2` | present |

Every assistant answer *except the last one* eventually lands, one POST late,
carried back by the client on the next user message. The last one never does,
because after `finish_reason=stop` the client has nothing further to send.

Since every conversation has exactly one last turn, **every stored transcript on
disk is incomplete** — but the mechanism is a missing write at end-of-turn, not a
serialization bug.

## Impact

**Live turns are unaffected.** The face never reads its own store back for
context: `_session_runner.py:399` states it outright — *"here every POST reseeds
from the CLIENT's history"* — and `is_resumed` is computed from directory
existence (`chat_completions.py:869`), not from a load. The model always sees the
client's complete view, including the answer it just gave.

**The store is read by the other face, and by `doctor`.** `SessionStore.load` has
exactly two callers in the installed tree:

```
$ grep -rn "store\.load(" --include=*.py site-packages/
site-packages/amplifier_agent_lib/_runtime.py:453:            loaded = store.load(session_id)
site-packages/amplifier_agent_cli/admin/doctor.py:454:            result = store.load(session_id)
```

The first is `make_turn_handler`'s resume path — `amplifier-agent run` — which
replays the loaded transcript into the kernel via `context.set_messages`. And
`SessionStore.load` walks *every* workspace when the id is not found in the
current one (its docstring calls this the "D10 cross-workspace resume fallback").
So a conversation held over the HTTP face and later resumed from the CLI under the
same session id replays a conversation missing its last answer.

The second is `amplifier-agent doctor` — the tool an operator reaches for
*specifically* when a session looks wrong. It reads the same truncated file.

Anything else that consumes a transcript — session forking, context-intelligence
tooling, a human reconstructing an incident — sees answers that stop one short.

## The data is not lost — it is written to a different file

The event log in the *same session directory* does capture the answer:

`sessions/<id>/context-intelligence/events.jsonl`

```json
{"event": "content_block:end",
 "data": {"block": {"type": "text",
                    "text": "Three sessions are running: **counter**, **logtail**, and **sysmon**."},
          "block_index": 0, "total_blocks": 1,
          "session_id": "http-chat-msusfw7p-rsgxkastz4",
          "turn_id": "turn-8cd7e34b9baa",
          "usage": {"input_tokens": 15405, "output_tokens": 29, "total_tokens": 15434,
                    "cost_usd": "0.046650"}},
 "timestamp": "2026-08-15T19:48:35.210593952+00:00"}
```

Two consequences:

1. **The fix is cheap.** The text is in hand at end-of-turn, in the same process,
   already assembled — the non-streaming path literally builds it as
   `"".join(content_parts)` (`chat_completions.py:677`). Nothing needs recovering.
2. **There is a workaround today** for anyone reconstructing a conversation before
   the fix lands: read `content_block:end` events out of
   `context-intelligence/events.jsonl` and interleave them with
   `transcript.jsonl` by `session_id`. Ugly, but no answer is irrecoverably gone
   from a machine that still has the session directory.

## Reproduction

Unlike the companion report, this one is observational — it is about what is on
disk after a real turn, so it needs a running server. No UI is involved.

1. Start `amplifier-agent serve chat-completions`.
2. POST a single-turn conversation with an explicit `client_session_id` (header
   `X-Client-Session-Id`, or the equivalent body field your client uses) and let
   it run to `finish_reason=stop`.
3. Read
   `~/.amplifier-agent/state/workspaces/<workspace>/sessions/http-<client_session_id>/transcript.jsonl`.

The assistant answer the response stream just delivered is not there. The file
ends on the last thing the *client* sent.

4. POST a second user message in the same conversation, carrying the full history
   as the wire protocol requires, and read the file again: the first answer is now
   present, and the second is not.

Static confirmation, if you would rather not run a server: the two greps at the
top of this report. One `save()` call site, invoked before the model runs; no
end-of-turn writer anywhere.

## Recommended fix

Append the assistant turn the server just produced to the session store when the
turn ends, instead of relying solely on the next client POST to carry it back.

Insertion points — the turn's final text exists in both transports at the moment
the terminal chunk is emitted:

- **streaming:** `routes/chat_completions.py`, immediately after the
  `stop_chunk(...)` yield (~`:593`), with a running accumulator over the emitted
  `content` deltas;
- **non-streaming:** the same stream, already buffered by
  `_buffer_stream_to_completion` into `"".join(content_parts)` (~`:677`).

Three conditions, each of which is a real failure if skipped:

1. **Only on `finish_reason == "stop"`.** A `tool_calls` turn *does* come back on
   the continuation POST, so appending it is unnecessary — and appending an
   assistant turn carrying `tool_calls` with no paired results would leave the
   stored transcript in exactly the shape `diagnose_transcript` calls
   `missing_tool_results`. The fix would manufacture the breakage the reconciler
   exists to repair.
2. **Only when the session id is set.** Without a `client_session_id` there is no
   session directory and the turn is deliberately ephemeral
   (`chat_completions.py:878`).
3. **Append, never rewrite.** `SessionStore.save` replaces the whole file
   (`session_store.py:42`), so whatever is written must be the reconciled client
   array *plus* the new turn. The reconciled array is already in hand:
   `reconcile_client_history` returns it, and its return value is currently
   discarded at the call site (`chat_completions.py:872`, no assignment).
   Capturing that return value is the natural way to build the end-of-turn write,
   and it costs nothing.

**Why this is safe against divergence.** The client stays authoritative: the very
next POST overwrites the transcript wholesale with the client's own array. An
appended turn can therefore only matter when there *is* no next POST — which is
precisely, and only, the gap being closed. A client that rewound or edited history
overwrites the appended turn on its next request exactly as it does today.

**On a mid-stream client abort:** the accumulated text is partial, so a partial
answer is persisted. That is still strictly better than the present behaviour, and
the next POST corrects it. Worth a comment in the code rather than a guard.

**Rejected: having the client POST once more after `finish_reason=stop`** purely
to flush the answer to the store. It puts the durability of the server's own
record in the hands of every client, costs a round trip per turn, and silently
fails for any client that does not implement it — including the
OpenAI-compatible clients this face exists to serve.

### Verifying the fix

1. Drive one conversation to `finish_reason=stop`; the last entry of
   `transcript.jsonl` must be the assistant answer the client received,
   byte-for-byte.
2. Drive a conversation containing a tool-call continuation and read the whole
   file back: every assistant turn present, in order (this one also requires the
   companion fix, below).
3. **Regression:** a `finish_reason=tool_calls` turn must **not** append anything.
   Assert the store after the tool-call turn still ends on the tool result, and
   that `diagnose_transcript` does not report `missing_tool_results` for it.
4. Send a second user message afterwards and confirm the answer appears exactly
   once, not twice — the client's array wins.
5. Resume the same session id from `amplifier-agent run` and confirm the replayed
   context contains the answer.

## Interaction with the companion report

[amplifier-agent-01-continuation-flagged-broken.md](amplifier-agent-01-continuation-flagged-broken.md)
describes a defect that fabricates an assistant turn reading *"The previous tool
calls were interrupted. This response was automatically repaired."* — and writes
it into **exactly the slot this defect leaves empty**.

The one-turn lag makes that fabrication **transient in the middle of a
conversation and permanent at the end of one**: the next POST overwrites the
transcript with the client's array, which contains the real answer, so the lie is
replaced. Measured on one session file, one turn apart:

```
after turn 1:  5 | assistant | "The previous tool calls were interrupted. …"   ← fabricated
after turn 2:  5 | assistant | "Three sessions are running: - counter - logtail - sysmon"
               6 | user      | "Thanks. Repeat that list back to me, no tools needed."
               grep -c "previous tool calls were interrupted" → 0
```

So the two compose into the worst available outcome: **the one assistant turn that
never gets corrected is also the one that gets fabricated.**

Land them together, the companion **first** — it is a three-line post-filter,
this one needs a new write path. Fixing only the companion leaves the store
ending on a tool result with no answer; fixing only this one leaves a fabricated
turn and the real one adjacent, contradicting each other.

## Evidence

Both sessions below were driven through a browser-based OpenAI-compatible client
that executes tools client-side and re-POSTs the full history, against a live
`amplifier-agent serve chat-completions`, and correlated to the server journal by
`client_session_id` and `chunk_id`.

### 1. The one-turn lag — `chat-msusov0i-bywny8tw33`

Turn 1 forces one tool call; turn 2 does not.

```
chat-completion start chunk_id=chatcmpl-e071620c675a44cd91e4ab42 history_len=1 prompt_chars=63
continuation turn: last message is role=tool (tool_call_id=toolu_0128Ky7BZR7od4PMvw5Fq13R)
chat-completion start chunk_id=chatcmpl-9540c2da0e084018be10dfcb history_len=4 prompt_chars=0
--- turn 1 ends; the client displays the answer ---
chat-completion start chunk_id=chatcmpl-0409d6730c824849b7ad8800 history_len=5 prompt_chars=53
```

`history_len=5` on the third POST is the client carrying turn 1's answer back.

Store immediately after turn 1 (5 entries — entry 5 is the companion defect's
fabrication):

```
1 | system            | …
2 | user              | Which sessions are running right now? List their names.
3 | assistant (tc=1)  |
4 | tool              | [{"name":"counter",…},{"name":"logtail",…},{"name":"sysmon",…}]
5 | assistant         | [{"type":"text","text":"The previous tool calls were interrupted. …"}]
```

Store after turn 2 (6 entries) — same file, same session:

```
1 | system            | …
2 | user              | Which sessions are running right now? List their names.
3 | assistant (tc=1)  |
4 | tool              | [{"name":"counter",…},…]
5 | assistant         | Three sessions are running:  - **counter** - **logtail** - **sysmon**
6 | user              | Thanks. Repeat that list back to me, no tools needed.
```

Turn 1's answer arrived. Turn 2's has not, and will not unless a third message is
sent.

### 2. The gap isolated from the companion defect — `chat-msusfw7p-rsgxkastz4`

Same client, same prompt, run with the companion report's patch applied to the
installed `amplifier-agent` (since reverted):

```
19:48:30 chat-completion start chunk_id=chatcmpl-762039d483d745afafd24e8f history_len=1 prompt_chars=63
19:48:33 continuation turn: last message is role=tool (tool_call_id=toolu_01Hqq2j5RAkVQvM1hf5gLUwM)
19:48:33 chat-completion start chunk_id=chatcmpl-0537ce607d514a608def906f history_len=4 prompt_chars=0
         (no "transcript was broken" warning anywhere for this session)
```

Store:

```
1 | system            | …
2 | user              | Which sessions are running right now? List their names.
3 | assistant (tc=1)  |
4 | tool              | [{"name":"counter",…},{"name":"logtail",…},{"name":"sysmon",…}]
```

Four entries. No fabrication (`grep -c` → 0) — **and no answer.** The client
displayed *"Three sessions are running: counter, logtail, sysmon."*; the durable
record ends on the tool result. This is the defect in this report, isolated.

### What was NOT verified

No fix was applied for this defect, so there is no post-fix measurement — only the
root cause, the mechanism, and the recommendation. This repository's own test
suite was not run; it was not available to the reporter.

## Source references

| What | Where |
|---|---|
| The only `store.save()` in the face | `amplifier_agent_http/_reconciler.py:95` |
| Called at request start, with client messages | `amplifier_agent_http/routes/chat_completions.py:872` |
| Its return value discarded (the array a fix would append to) | `routes/chat_completions.py:872`, no assignment |
| `is_resumed` from directory existence, not a load | `routes/chat_completions.py:869` |
| Ephemeral when no session id | `routes/chat_completions.py:878` |
| Terminal `stop_chunk` emission (streaming) | `routes/chat_completions.py:593` |
| Final text already assembled (non-streaming) | `routes/chat_completions.py:677` |
| Context seeded from client history, not the store | `amplifier_agent_http/_session_runner.py:399, 413–421` |
| `SessionStore.save` replaces the whole transcript | `amplifier_agent_lib/session_store.py:42` |
| `SessionStore.load` + cross-workspace fallback | `amplifier_agent_lib/session_store.py:56` |
| Store consumer: CLI resume | `amplifier_agent_lib/_runtime.py:453` |
| Store consumer: `doctor` | `amplifier_agent_cli/admin/doctor.py:454` |
| The answer, captured in the event log instead | `sessions/<id>/context-intelligence/events.jsonl`, `content_block:end` |

Every line number above was re-verified by `grep` against the installed 0.12.0
after this report was written.

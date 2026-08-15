# Finding — the sidecar's session store never records the model's answer to the last turn

**Work item:** muxplex-c1x (discovered from muxplex-2nm) · **Date:** 2026-08-15 ·
**Status:** root cause identified, fix recommended, **not applied** — the change
lands in `amplifier-agent`, not in this repo

**Versions measured:** `amplifier-agent` 0.12.0 · `amplifier-foundation` 1.0.0 ·
`amplifier-core` 1.6.1, running as `amplifier-agent serve chat-completions` in the
`muxplex-lan-twin` DTU.

**Companion finding:** `2026-08-15-sidecar-transcript-repair.md` (muxplex-3aw).
The two defects sit in the same slot of the same file and are routinely mistaken
for one another. Fixing 3aw removes a fabricated entry; it does not add the real
one. Both browser runs below show that directly.

## Verdict

**The sidecar owns this defect. The panel is correct and needs no change.**

The HTTP face has **exactly one** `store.save()` call site, and it runs at the
*start* of a request, on the *client's* posted array:

```
$ grep -rn "\.save(" site-packages/amplifier_agent_http/
site-packages/amplifier_agent_http/_reconciler.py:95:    store.save(
```

`reconcile_client_history` is called from `chat_completions.py:872`, before the
model has produced anything. Nothing writes to the store at end of turn. The
persisted transcript is therefore a mirror of what the client last sent — never a
record of what the server last said.

The stored metadata says so in as many words. Every session's `metadata.json`, in
full:

```json
{
  "last_turn": "client_reconciled"
}
```

There is no other value it can take, because there is no other writer.

## The correction that matters

The item as filed says the model's closing answer is *"never written to disk, for
any conversation."* That overstates it, and the overstatement would send a fixer
looking in the wrong place — for a serialization bug that drops assistant turns,
rather than for a missing write.

What actually happens is a **one-turn lag**:

| after… | store holds | the model's answer to turn 1 |
|---|---|---|
| turn 1 completes | `[system, user1, assistant(tool_calls), tool]` | **absent** |
| user sends turn 2 | `[system, user1, assistant(tool_calls), tool, **assistant1**, user2]` | **present** |
| turn 2 completes | unchanged — still ends at `user2` | present |

Every assistant answer *except the last one* eventually lands, one POST late,
carried back by the client on the next user message. The last one never does,
because there is no next POST — after `finish_reason=stop` the client has nothing
left to say.

So the honest statement of the defect is: **the store is always exactly one
assistant turn behind, and every conversation ends one turn short.** Since every
conversation has exactly one last turn, every stored transcript on disk is
incomplete — but the mechanism is a missing write at end-of-turn, not a lossy
serializer.

This was measured, not reasoned — see Evidence §1.

## Interaction with muxplex-3aw

Today the empty slot is not empty: 3aw's repair fabricates an assistant turn
reading *"The previous tool calls were interrupted. This response was
automatically repaired."* and writes it into exactly the position the real answer
belongs.

The one-turn lag makes that fabrication **transient in the middle of a
conversation and permanent at the end of one**: the next POST overwrites the whole
transcript with the client's array, which contains the real answer, so the lie is
replaced. Measured directly — the same session file, one turn apart:

```
after turn 1:  5 | assistant | "The previous tool calls were interrupted. …"   ← fabricated
after turn 2:  5 | assistant | "Three sessions are running: - counter …"       ← the real answer
               6 | user      | "Thanks. Repeat that list back to me, …"
               grep -c "previous tool calls were interrupted" → 0
```

The two defects therefore compose into the worst available outcome: **the one
assistant turn that never gets corrected is also the one that gets fabricated.**

Fixing 3aw alone leaves the store ending on a tool result with no answer. Fixing
c1x alone leaves a fabricated turn *and* the real one, adjacent, contradicting
each other. They want fixing together, and 3aw first — the exemption is a
three-line post-filter, this one needs a new write path.

## Blast radius — measured, not assumed

**Live turns are unaffected.** The HTTP face never reads its own store back for
context: `_session_runner.py:399` states it outright — *"here every POST reseeds
from the CLIENT's history"* — and `is_resumed` is computed from directory
existence (`chat_completions.py:869`), not from a load. The model always sees the
client's complete view, including the answer it just gave.

**The store is read by the other face, and by `doctor`.** `SessionStore.load` has
exactly two callers in the whole installed tree:

```
$ grep -rn "store\.load(" --include=*.py site-packages/
site-packages/amplifier_agent_lib/_runtime.py:453:            loaded = store.load(session_id)
site-packages/amplifier_agent_cli/admin/doctor.py:454:            result = store.load(session_id)
```

The first is `make_turn_handler`'s resume path — the CLI face
(`amplifier-agent run`) — which replays the loaded transcript into the kernel via
`context.set_messages`. And `SessionStore.load` walks *every* workspace when the
id is not found in the current one (its own docstring calls this the "D10
cross-workspace resume fallback"). So a conversation held in the browser panel and
later resumed from the CLI under the same session id replays a transcript whose
last assistant answer is missing — and, until 3aw lands, whose last entry claims
an interruption that never happened. That is a concrete consumer, not a
hypothetical one.

The second is `amplifier-agent doctor`, i.e. the tool an operator reaches for
*specifically* when a session looks wrong. It reads the same truncated file.

**Everything else that reads a transcript** — context-intelligence, session
forking, a human opening `transcript.jsonl` to reconstruct an incident — reads a
conversation whose answers stop one short.

## The data is not lost — it is written to a different file

The sidecar's own event log, in the *same session directory*, does capture the
answer:

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

Two consequences, both load-bearing:

1. **The fix is cheap.** The text is in hand at end-of-turn, in the same process,
   already assembled — the non-streaming path literally builds it as
   `"".join(content_parts)` (`chat_completions.py:677`). Nothing needs to be
   recovered or re-derived.
2. **There is a workaround today**, for anyone reconstructing a conversation
   before the fix lands: read `content_block:end` events out of
   `context-intelligence/events.jsonl` and interleave them with
   `transcript.jsonl` by `session_id`. Ugly, but it means no answer is
   irrecoverably gone from a machine that still has the session directory.

## Recommended fix

**Owner: `amplifier-agent` (`amplifier_agent_http`). No muxplex change.**

Append the assistant turn the server just produced to the session store when the
turn ends, instead of relying solely on the next client POST to carry it back.

Placement — the turn's final text exists in both transports at the point the
terminal chunk is emitted:

- streaming: `chat_completions.py`, immediately after the `stop_chunk(...)` yield
  (~line 593), with a running accumulator over the emitted `content` deltas;
- non-streaming: the same stream, already buffered by
  `_buffer_stream_to_completion` into `"".join(content_parts)` (~line 677).

Three conditions the implementation has to respect, each of which is a real
failure if ignored:

1. **Only on `finish_reason == "stop"`.** A `tool_calls` turn *does* come back on
   the continuation POST, so appending it is unnecessary — and appending an
   assistant turn carrying `tool_calls` with no paired results would leave the
   stored transcript in the exact shape `diagnose_transcript` calls
   `missing_tool_results`, i.e. the fix would manufacture the breakage the
   reconciler exists to repair.
2. **Only when `sid` is set.** Without a `client_session_id` there is no session
   directory and the turn is deliberately ephemeral (`chat_completions.py:878`).
3. **Append, never rewrite.** `SessionStore.save` replaces the whole file, so
   whatever is appended must be the reconciled client array *plus* the new turn.
   The reconciled array is already in hand — `reconcile_client_history` returns
   it, and its return value is currently discarded at the call site
   (`chat_completions.py:872`, no assignment). Capturing that return value is the
   natural way to build the end-of-turn write, and it costs nothing.

**Why this is safe against divergence.** The client stays authoritative: the very
next POST overwrites the transcript wholesale with the client's own array. An
appended turn can therefore only matter when there *is* no next POST — which is
precisely, and only, the gap being closed. A client that rewound or edited history
overwrites the appended turn on its next request exactly as it does today.

**On a mid-stream client abort:** the accumulated text is partial, so a partial
answer is persisted. That is still strictly better than the present behaviour
(nothing, or a fabricated interruption), and the next POST corrects it. Worth a
comment in the code rather than a guard.

**Rejected: having the panel POST once more after `finish_reason=stop`** purely to
flush the answer to the store. It puts the durability of the server's own record
in the hands of every client, costs a round trip per turn, and silently fails for
any client that does not implement it — including the OpenAI-compatible clients
this face exists to serve.

### Verifying the fix

1. Drive one conversation to `finish_reason=stop` and read
   `sessions/<id>/transcript.jsonl`: its last entry must be the assistant answer
   the client rendered, byte-for-byte.
2. Drive a conversation containing a tool-call continuation and read the whole
   file back: every assistant turn present, in order, and **no** entry containing
   `"The previous tool calls were interrupted"` (this one requires the 3aw fix
   too).
3. Regression: a `finish_reason=tool_calls` turn must **not** append anything —
   assert the store after the tool-call turn still ends on the tool result, and
   that `diagnose_transcript` reports it `healthy`/`incomplete_assistant_turn`
   rather than `missing_tool_results`.
4. Send a second user message afterwards and confirm the store is not corrupted by
   the overlap — the client's array wins, and the answer appears exactly once, not
   twice.
5. Resume the same session id from the CLI face (`amplifier-agent run`) and
   confirm the replayed context contains the answer.

## Evidence

All runs driven through the **real panel in a real browser** (Edge on macOS,
browser-bridge device `edge-macos`) against the live muxplex at
`http://192.168.1.5:8092/`, correlated to the sidecar journal by
`client_session_id` and `chunk_id`. No curl, no replay script.

### 1. The one-turn lag — `chat-msusov0i-bywny8tw33`

Turn 1: *"Which muxplex sessions are running right now? List their names."*
(forces one tool call). Turn 2: *"Thanks. Repeat that list back to me, no tools
needed."* (no tool call).

```
19:54:5x  chat-completion start chunk_id=chatcmpl-e071620c675a44cd91e4ab42 history_len=1 prompt_chars=63
          continuation turn: last message is role=tool (tool_call_id=toolu_0128Ky7BZR7od4PMvw5Fq13R)
          WARNING Client-sent transcript was broken — repaired … entries_before=4 entries_after=5
          chat-completion start chunk_id=chatcmpl-9540c2da0e084018be10dfcb history_len=4 prompt_chars=0
          --- turn 1 ends, browser shows the answer ---
          chat-completion start chunk_id=chatcmpl-0409d6730c824849b7ad8800 history_len=5 prompt_chars=53
```

`history_len=5` on the third POST is the client carrying turn 1's answer back.

Store immediately after turn 1 (5 entries):

```
1 | system            | You are a small assistant embedded in a muxplex dashboard …
2 | user              | Which muxplex sessions are running right now? List their names.
3 | assistant (tc=1)  |
4 | tool              | [{"name":"counter",…},{"name":"logtail",…},{"name":"sysmon",…}]
5 | assistant         | [{"type":"text","text":"The previous tool calls were interrupted. …"}]  ← fabricated
```

Store after turn 2 (6 entries) — same file, same session:

```
1 | system            | …
2 | user              | Which muxplex sessions are running right now? List their names.
3 | assistant (tc=1)  |
4 | tool              | [{"name":"counter",…},…]
5 | assistant         | Three sessions are running:  - **counter** - **logtail** - **sysmon**   ← the real answer
6 | user              | Thanks. Repeat that list back to me, no tools needed.
```

`grep -c "previous tool calls were interrupted"` → `0`. Turn 1's answer arrived;
turn 2's has not, and will not unless a third message is sent.

### 2. The gap survives the 3aw fix — `chat-msusfw7p-rsgxkastz4`

Same panel, same prompt, run with the 3aw trailing-continuation exemption applied
to the container's `amplifier-agent` (since reverted — see the companion finding).
`client_session_id` taken from the panel's own **Export** download,
`muxplex-agent-chat-msusfw7p-rsgxkastz4-1786823371065.md`.

```
19:48:30  chat-completion start chunk_id=chatcmpl-762039d483d745afafd24e8f history_len=1 prompt_chars=63
19:48:33  continuation turn: last message is role=tool (tool_call_id=toolu_01Hqq2j5RAkVQvM1hf5gLUwM)
19:48:33  chat-completion start chunk_id=chatcmpl-0537ce607d514a608def906f history_len=4 prompt_chars=0
          (no "transcript was broken" warning anywhere for this session)
```

Store:

```
1 | system            | …
2 | user              | Which muxplex sessions are running right now? List their names.
3 | assistant (tc=1)  |
4 | tool              | [{"name":"counter",…},{"name":"logtail",…},{"name":"sysmon",…}]
```

Four entries. No fabrication (`grep -c` → 0) — and no answer. The browser
displayed *"Three sessions are running: counter, logtail, sysmon."*; the durable
record ends on the tool result. **This is the c1x defect isolated, with 3aw out of
the way.**

### 3. The unpatched control — `chat-msus4o1s-ikn3a236kyj`

The same conversation ten minutes earlier, unpatched, again Export-derived:
5 entries, ending on the fabrication, with the real answer absent. Full journal
lines in the companion finding.

## Source references

| What | Where |
|---|---|
| The only `store.save()` in the HTTP face | `amplifier_agent_http/_reconciler.py:95` |
| Called at request start, with client messages | `amplifier_agent_http/routes/chat_completions.py:872` |
| Its return value discarded (the array a fix would append to) | `chat_completions.py:872`, no assignment |
| `is_resumed` from directory existence, not a load | `chat_completions.py:869` |
| HTTP face reseeds context from client history | `amplifier_agent_http/_session_runner.py:399, 413–421` |
| `SessionStore.save` replaces the whole transcript | `amplifier_agent_lib/session_store.py:42–55` |
| The only `store.load()` — CLI resume path | `amplifier_agent_lib/_runtime.py:453` |
| Cross-workspace resume fallback | `amplifier_agent_lib/session_store.py:56–86` |
| Final text already assembled (non-streaming) | `chat_completions.py:677` |
| Terminal `stop_chunk` emission (streaming) | `chat_completions.py:593` |
| The answer, captured in the event log instead | `sessions/<id>/context-intelligence/events.jsonl`, `content_block:end` |

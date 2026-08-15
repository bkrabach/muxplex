# Upstream-fileable reports

Two defects in **`amplifier-agent`** (the chat-completions HTTP face), found while
building muxplex's agent panel. No muxplex file needs to change for either.

The documents in this directory are written to be **filed verbatim** in
`amplifier-agent`'s tracker: self-contained, addressed to its maintainers, with no
muxplex work-item ids, no lane vocabulary, and no assumed knowledge of this
project. Copy one into an issue as-is.

| Report | Defect | Fix status |
|---|---|---|
| [01 — continuation flagged broken](amplifier-agent-01-continuation-flagged-broken.md) | Every tool continuation is called a broken transcript, and a fabricated assistant turn is written to the session store | Patch included, **applied and verified** against 0.12.0, then reverted |
| [02 — final assistant turn not persisted](amplifier-agent-02-final-assistant-turn-not-persisted.md) | There is no end-of-turn write, so every stored transcript ends one assistant turn short | Recommendation only — needs a new write path, design decision is theirs |

**Land them together, 01 first.** 01 is a three-line post-filter; 02 needs a new
write path. They occupy the same slot in the same file: 01 fabricates an assistant
turn into exactly the position 02 leaves empty. Fixing only 01 leaves the store
ending one turn short; fixing only 02 leaves a fabricated turn and the real one
adjacent, contradicting each other.

The muxplex-side records — including the reasoning that ruled out `chat.js`'s
parallel-tool-call workaround as a cause, and the process notes — live one level
up:

- `../2026-08-15-sidecar-transcript-repair.md`
- `../2026-08-15-sidecar-final-turn-not-persisted.md`

Runnable scripts referenced by the internal records are in
`../2026-08-15-sidecar-transcript-repair/`. Report 01 inlines a smaller,
self-contained reproduction that needs no server, no network and no API key, so a
maintainer never has to fetch anything from this repository.

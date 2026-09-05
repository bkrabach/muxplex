# Implementation Plans (Historical)

These are historical design and implementation plan documents from the initial build of muxplex. Nearly all plans have been fully implemented; they are retained as architectural decision records (ADRs) and build logs. The one exception is `2026-08-05-focus-grab-plan.md`, which is the specification for `docs/BACKLOG.md` item 3 and has **not** been built yet — its own header says so.

See the main [README.md](../../README.md) for current documentation.

## When a dated plan goes stale

These documents are dated, so the temptation is to leave every stale sentence
alone as a record of what was believed that day. That is right for most of a
plan and wrong for part of it. Split by **how a reader uses the sentence**:

- **Reasoning — background, principles, alternatives considered, "why not X"
  sections.** Leave untouched. The value is that it records a decision *as
  argued at the time*; correcting it destroys the only thing it is for.
- **Claims about what the shipped code does — safety-property tables, API
  shapes, file layouts, "the X pattern" references to another module.** Correct
  in place, and append a dated correction paragraph that quotes the original
  wording verbatim and names the change that invalidated it (item id + commit).
  A reader consults these to learn how the system behaves *now*; a stale one is
  read as current and acted on.

The correction paragraph is what keeps the record intact — nothing is deleted,
it just stops being the first thing a reader trusts. A "superseded by" banner on
the whole document is the wrong instrument for a single stale cell: it discards
the accurate 95% along with the wrong 5%.

Worked example: the "Atomic write" row in
[`2026-08-01-tmux-config-design.md`](2026-08-01-tmux-config-design.md#safety-properties)
(`muxplex-bzx`). The same rule sent the equivalent claim in
`docs/TERMINAL_CONFIG_OWNERSHIP.md` the other way — it lives under **"Binding
rules"**, a list a reader treats as live constraints, so it was struck rather
than corrected, with the history moved into that document's `→` note.

# Lane: deck — soft deck ANSI rendering bug

Owner of: muxplex/frontend/deck/* ONLY. Do not touch chat.js, index.html,
style.css, or docs owned by other lanes.

Work item muxplex-111. Read its full description and acceptance criteria.

The deck's key previews render terminal output without stripping ANSI SGR escape
sequences, so raw bytes appear as visible garbage. Observed: a key body reading
"MiB Swap:\x1b[1m 0", and vision analysis called the logtail key "garbled and
unreadable due to text overwriting itself".

This matters more on the deck than in the dashboard: a key body is a ~13x6
character budget, so a few escape bytes consume most of the legible area, and the
deck's whole premise is a glanceable surface with no progressive disclosure.

The chat panel just solved this class of problem for its own tool-result summaries
(strip for display only, never mutate the underlying capture). Same approach likely
applies. Verify in a real browser at /deck/ per _SHARED.md's proof bar, using a
session whose output actually contains escapes (sysmon runs top).

## Terminal states
PASS / FAIL-<named> / BLOCKED-<named> / PENDING-HUMAN.

Complete when **either** every item reaches a terminal state, **or** it is
conclusively demonstrated the remainder cannot, naming the blocker for each.
Items ending FAIL or BLOCKED are residuals, not failures of the goal.

## Rules
- Work ONLY in this worktree. Do not touch the main checkout or sibling worktrees.
- Crossing into another lane FILE is a defect, not a courtesy. Record the needed
  edit as a residual and stop.
- Commit early and often to your own branch. Never merge to main. Do not push.
- Read `.amplifier/goals/_SHARED.md` FIRST — it has the environment, the hard
  safety constraints, and the proof bar. All of it binds you.
- Exceeding the time bound is a terminal BUDGET state. Commit your work; do not rush it.
- Write DONE.json in the worktree root as your FINAL act:
  {lane, session_id, verdict (COMPLETE|BLOCKED|PARTIAL), branch, head, pushed,
   items[], residuals[], pending_human[], suite}


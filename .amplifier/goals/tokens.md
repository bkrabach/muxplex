# Lane: tokens — reconcile two competing token files
Owner of: assets/branding/tokens.css, muxplex/frontend/tokens.css, docs/DESIGN_LANGUAGE.md.
Do NOT touch style.css, chat.js, or index.html — the panel lane owns those. Record anything
needing applied there as a residual.

Work item muxplex-cnd. Read it fully.

Two different token files now exist at different paths with different content:
  assets/branding/tokens.css   12,716 bytes  (pre-existing, commit 8234e2e)
  muxplex/frontend/tokens.css  19,234 bytes  (mxp1 design lane, commit 89104aa)
index.html links /tokens.css, which serves the frontend one.

Git never flagged this because the paths differ — it surfaced only by diffing across lanes.
A design language that ships as two competing token files is worse than none, because the
next person picks whichever they find first and the app drifts both ways.

Decide and record: superseded, two clearly-scoped layers with a written rule, or merged into
one source of truth. Then make the codebase reflect the decision — no orphaned duplicate.
Note style.css's own :root currently wins every shared name by design, guarded by
test_design_tokens.py. Keep that test green.

## Terminal states
PASS / FAIL-<named> / BLOCKED-<named> / PENDING-HUMAN.
Complete when **either** every item reaches a terminal state, **or** it is conclusively
demonstrated the remainder cannot, naming the blocker for each. Items ending FAIL or
BLOCKED are residuals, not failures of the goal.

## Rules
- Work ONLY in this worktree. Do not touch the main checkout or sibling worktrees.
- Crossing into another lane FILE is a defect, not a courtesy. Record the needed edit as
  a residual and stop.
- Commit early and often to your own branch. Never merge to main. Do not push.
- Read `.amplifier/goals/_SHARED.md` FIRST. All of it binds you.
- Read each work item in full before starting: `amplifier-work-tracker` CLI, project `muxplex`.
- Exceeding the time bound is a terminal BUDGET state. Commit your work; do not rush it.
- Add DONE.json to .gitignore if absent. Write DONE.json in the worktree root as your FINAL
  act: {lane, session_id, verdict (COMPLETE|BLOCKED|PARTIAL), branch, head, pushed, items[],
  residuals[], pending_human[], suite}


# Lane: design — muxplex design language

Owner of: docs/DESIGN_LANGUAGE.md (new) and muxplex/frontend/tokens.css (new).
You may NOT edit style.css, chat.js, index.html, or deck/* — the panel and deck
lanes own those. Deliver tokens the panel lane can adopt; record anything you
need applied as a residual.

Work item muxplex-lcc. Read its full description and acceptance criteria.

Owner's words: "the entire styling of the panel does not match the UI/UX of the
rest of muxplex, we need design principles/language/tokens/etc. created for the
muxplex project holistically and then applied."

Inventory what ALREADY exists before inventing anything: the CSS custom properties
in style.css, assets/branding/DESIGN-SYSTEM.md, deck/DESIGN_RESPONSIVE.md's
adopted 48x48 touch floor, and the .quick-link component consolidation
(commits d9061ba, 290b71e) — that last one is the reference for how muxplex wants
components done. Read its long explanatory comments.

Evidence of the problem, from an independent design review: five unrelated
spacing/sizing families with no common unit (10/12 padding, 6/8/4 bubble metrics,
22px logo, min(420px,92vw) width, z-index 9000) — "five separate eyeballings".

Deliver written principles + a token scale + a small named component vocabulary.
NOT a rewrite of the app. Scope it so the panel lane can adopt it incrementally.

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


# Lane: server — default the input allowlist to "*"
Owner of: muxplex/settings.py, its tests, and the settings documentation. Do NOT touch
any frontend file.

Work item muxplex-ph0. Read it fully.

Owner has now hit a 403 on this path THREE times. Today input_allowed_sessions defaults to
an empty list, so flipping input_enabled:true still leaves a second wall.

Change the default to "*" (all sessions), keeping the list form fully supported.

DO NOT weaken the real boundary while doing it:
- input_enabled stays FALSE by default. That is the actual gate.
- Both settings stay in LOCAL_ONLY_KEYS — file-only, never settable over the API, never by
  the agent. That partition exists because the federation Bearer key is also the agent
  credential and this endpoint is RCE-by-design. Prove it still holds with a test.
- The panel's write-confirmation gate is unaffected; do not touch it.

Document the shift plainly: flipping the switch is now BROADER than it used to be, and a
reader who remembers the old two-gate behaviour needs to be told.

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


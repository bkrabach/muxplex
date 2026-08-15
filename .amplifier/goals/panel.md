# Lane: panel — agent panel UX fixes + the Agent settings tab
Owner of: muxplex/frontend/{chat.js,index.html,style.css} and the settings-dialog code in
app.js. NOTHING else touches these. You are the long pole; you hold the container.

Work these items. Read each one fully first. Two are SHIPPED BUGS from the last batch —
do those first, they are user-visible and wrong today:

1. muxplex-9n9  BUG: the 403 message names the WRONG cause. Prose says "not on the
   allowlist" while the server detail says input_enabled=false (the GLOBAL switch).
   The panel told the user to fix the wrong setting. Key off the actual server response.
   Add a test covering BOTH branches — this path has now shipped wrong twice.
2. muxplex-ixl  BUG: model-directed instructions leaking into the user-visible chat
   ("TELL THE USER exactly this", "Do NOT retry this call"). Separate machine-facing
   guidance from human-facing text. Also fix raw internal labels (Status:/Error:/Agent:)
   and the duplicated tool result (summary immediately followed by the full raw payload).
3. muxplex-2y1  BUG: composer renders ~1/3 height on load until the first keystroke.
4. muxplex-2qs  Persist panel open/closed state using the SAME mechanism the left session
   sidebar already uses — find it and follow it, do not invent a second scheme. Remove
   the close X; the Agent button is the toggle.
5. muxplex-d8f  "Amplifier Agent" in the byline -> site cyan (from a token, not a hex),
   linked to https://github.com/microsoft/amplifier-agent.
6. muxplex-3lr  Agent settings tab, following muxplex's existing settings-dialog tab
   pattern. Per-device LOCAL storage only — never server settings, never federation-synced.
7. muxplex-18f  Configurable send/newline: Mode A (Enter=newline, Ctrl/Cmd+Enter=send,
   DEFAULT, and the only mode that works on a touch keyboard) and Mode B (Enter=send,
   Shift+Enter/Ctrl+J=newline, matches amplifier-app-cli). The on-screen hint MUST read
   from the same setting the handler reads — one source of truth.
8. muxplex-enr  In that settings tab, a short factual note disclosing where transcripts
   are stored. Owner has ACCEPTED the default logging; this is disclosure only, not policy.

Verify every one in a real browser per _SHARED.md. The six tools and the write-confirmation
gate (cancel AND confirm paths) must still work when you are done.

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


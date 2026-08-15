# Lane: sidecar — transcript-repair investigation

Owner of: docs/findings/ (new directory) ONLY. This is an INVESTIGATION.
Do not change chat.js or any frontend file — record recommended edits as residuals
for the panel lane.

Work item muxplex-2nm. Read its full description and acceptance criteria.

The agent sidecar logs this on continuation turns, and nobody knows why:
  WARNING: Client-sent transcript was broken -- repaired before reconcile.
Two occurrences in a single three-request conversation, both immediately before a
chat-completion start. The turns SUCCEEDED, so nothing surfaced to the user — the
sidecar silently repaired and continued.

Find out which side is wrong: is the panel sending a malformed message array on
tool-call continuations, or is the sidecar's reconciler over-eager? Strong lead:
amplifier-agent's HTTP face emits index:0 for ALL parallel tool calls, and chat.js
works around it by keying on index:id. Check whether that workaround is what the
reconciler objects to.

Method: use the panel's own Export button to capture a debug record, then correlate
it against the sidecar journal by client_session_id and chunk_id — that correlation
is proven to work. journalctl -u amplifier-agent-http in the container.

Do NOT "fix" this by suppressing the warning. Deliver a written finding with
evidence and a recommended fix, naming which side owns it.

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


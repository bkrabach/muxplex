# Lane: upstream — two amplifier-agent defects found from this panel
Owner of: docs/findings/ ONLY. These are defects in the amplifier-agent SIDECAR, a
different repo you cannot commit to. Your deliverable is evidence and a precise report,
not a patch. Do not touch any muxplex frontend or server file.

Work items muxplex-3aw and muxplex-c1x. Read both fully.

- muxplex-3aw: the sidecar logs "Client-sent transcript was broken -- repaired before
  reconcile" on continuation turns. Turns succeed, so nothing surfaces — it silently
  repairs and continues. Determine which side is actually wrong.
- muxplex-c1x: the sidecar session store never records the model's final assistant turn.

Method that is proven to work here: use the panel's Export button to capture a debug record,
then correlate it against the sidecar journal by client_session_id and chunk_id
(journalctl -u amplifier-agent-http in the container). That correlation is verified.

For each: a written finding with reproducible evidence, the precise root cause, and a
recommended fix naming which side owns it — written well enough to file upstream verbatim.
Do NOT "fix" either by suppressing a warning.

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


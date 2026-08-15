# Lane: panel — the agent-panel frontend chain

Owner of: muxplex/frontend/chat.js, muxplex/frontend/index.html, and the
agent-panel sections of muxplex/frontend/style.css. NOTHING else touches these.

Work these work-tracker items IN THIS ORDER. Each builds on the last.
Read each item's full description and acceptance criteria first:
  amplifier-work-tracker CLI, project 'muxplex'. Items:

1. muxplex-rle  Panel must sit INLINE beside content like the left session
   sidebar — below the header bar, NOT a fixed overlay. Agent button becomes a
   toggle with an active state. Applies to BOTH single-session and main views.
   THIS IS THE BIGGEST ONE. Do it first — everything after applies to the new geometry.
   Note: full-width-at-599px and the attention badge were compensating for the
   overlay. This item deletes their reason. Reconcile them, do not preserve blindly.
2. muxplex-z6h  Strip chrome: remove conversation id, "Muxplex Agent" title,
   Amplifier logo from the PANEL header (KEEP it on the Agent button), and the
   attention badge. Move "Powered by Amplifier Agent" to a byline under the input.
3. muxplex-5bn  Shared link-button component matching .quick-link / view-dropdown
   (now present after the rebase — study d9061ba, 290b71e and the long comments
   in style.css). Apply to New/Export/Send. Survey the app for consolidation.
4. muxplex-6oq  Export: markdown (token-optimised, context-rich) + JSON. If JSON
   has no real consumer, make Export a plain link button that downloads markdown.
5. muxplex-8qp  Enter=newline, Shift+Enter=newline, Ctrl+J=newline,
   Ctrl/Cmd+Enter=send. Textarea auto-grows to a max. Keep 16px font (iOS zoom).
6. muxplex-d5v  Focus the input when the panel opens.
7. muxplex-46p  Accessibility: ARIA live region for streaming (coherent, NOT one
   announcement per token), focus management on open AND close, role not conveyed
   by colour alone, labelled icon-only button at <=599px.
8. muxplex-04m  Render model markdown (bold, lists, code) instead of literal text.
   Must be safe against injection — this renders model output and tool results.
9. muxplex-oi2  Tool activity + errors: humane summaries, raw payload inspectable.
   Partly done already (appendToolResult). Finish it, especially error text —
   users currently see doubly-escaped JSON and internal config key names.
10. muxplex-l2y Status indicator naming the CONCRETE action, not a spinner.
    Different treatment for an inert read vs the irreversible write.
11. muxplex-5so On a 403 from the input endpoint, tell the user the actual remedy
    (operator-only, on-disk settings, named keys) instead of "no workaround
    available". Never attempt to change it, never retry.

## Definition of done
Every item above verified IN A REAL BROWSER per _SHARED.md's proof bar, including
the cancel AND confirm paths of the write-confirmation gate still working, and the
six tools still working. Take the container when you need it.

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


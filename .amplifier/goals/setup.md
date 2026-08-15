# Lane: setup — the first-run path a real user has to walk
Owner of: docs/ setup documentation and the sidecar configuration artifacts under
docs/agent-chat-sidecar/. Do NOT touch chat.js, index.html, or style.css — the panel lane
owns those. Anything needing changed there is a residual for that lane.

Work item muxplex-757. Read it fully — it contains the verified configuration facts.

The gap: today a user configures this by SSHing to the box and editing a systemd env file.
The model is hardcoded to claude-sonnet-5 in chat.js. There is no way to see which provider
is active, and a missing key produces an opaque failure. This is the single biggest thing
between the feature and being usable by anyone but its author.

Deliver a setup path someone can actually follow without reading source or work items:
install, provider/key configuration, verifying it works, and what to do when it does not.

ALSO settle and document one open question the owner asked, using the verified architecture:
the panel POSTs to /api/agent/chat/completions on whichever muxplex SERVED THE PAGE, which
proxies to that box's own sidecar (`_AGENT_PROXY_URL`, default 127.0.0.1:9099). Tools execute
in the BROWSER with the browser's cookie, so federated reach is already free and no peer needs
a key. Net: the key must live on the box you load the UI from. Document that plainly — users
will assume otherwise. Note that AMPLIFIER_AGENT_URL makes remoting the sidecar possible but
that doing so would require opening the loopback-scoped fence, which is not recommended.

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


# Shared context — batch `mxp2`

## Environment
- Repo: this worktree. Branch base is the commit named in your goal file.
- Running system: DTU container `muxplex-lan-twin`.
  Exec: `amplifier-digital-twin exec --visual-id "" muxplex-lan-twin -- <cmd>`
  muxplex inside at `127.0.0.1:8088`, source `/opt/muxplex` (**now a REAL git checkout**
  of poc/agent-chat-panel — use git there, it will show you collisions).
  LAN `http://192.168.1.5:8092/` · loopback `http://localhost:8093/`
  Login: password-only form, NO username field. The credential is NOT in this repo —
  read it from the container's `/root/.config/muxplex/settings.json` or ask the operator.
  NEVER write it into a tracked file; this repo is PUBLIC.
  Live seeded tmux sessions: `counter`, `logtail`, `sysmon`.
- Agent sidecar: `amplifier-agent serve chat-completions` v0.12.0 as user `aa-svc` on
  `127.0.0.1:9099`, unit `amplifier-agent-http`. muxplex proxies via
  `POST /api/agent/chat/completions` (`main.py` `_AGENT_PROXY_URL`).

## ONE CONTAINER
There is exactly ONE running muxplex and all lanes share it. The `panel` lane holds it
most of the time. NEVER restart the `muxplex` service — a previous run did and wiped the
seeded tmux sessions. Frontend files are static; reload the page instead.

## Architecture you must not break
Tools are DECLARED to the model but EXECUTED BY THE BROWSER, same-origin, with the
logged-in user's cookie. The sidecar holds NO muxplex credential and is firewalled from
muxplex by UID (`muxplex-agent-fence.service`, deny-all-local + narrow DNS allowance).
The agent can never exceed the user's own authority. Do not add any path that changes
this. The write-confirmation gate in front of `send_muxplex_session_input` is a safety
feature, not friction — do not remove it or let the agent auto-approve it.

## HARD CONSTRAINTS — safety critical
1. Host port 8088 is the owner's LIVE PRODUCTION muxplex. Never bind, proxy, stop,
   restart or touch it. All runtime work happens in the DTU container.
2. NEVER run `tmux kill-server`, `pkill tmux`, or `killall tmux` — with or without
   `-L`/`-S`. This has destroyed 70+ live sessions twice on this machine.
3. Do not restart the container's `muxplex` service.
4. Only ever send terminal input to `counter`, `logtail`, `sysmon`.
5. No fallbacks, no mocks, no synthetic data. Failures surface loudly.
6. Never commit a secret. Verify by grepping the LITERAL value, not a pattern.
7. Do not push. Do not merge to main. Commit to your own branch only.

## PROOF BAR
Four times in this project a change was reported "working" on non-browser evidence and
was dead, clobbered, or wrong in the browser. **Curl is NOT proof for anything a user sees.**
Use the browser-bridge `browser_*` tools (device `edge-macos`,
id `16909b75-aeec-4bd7-ae9a-c184ed08222a`). `agent-browser` has no Linux ARM64 Chrome build
here. For narrow viewports, local headless FIREFOX works (Chromium crashes on aarch64) —
state which client produced which evidence. Use `browser_vision_read` on real pixels for
anything visual; a broken layout still yields valid DOM text.

## KNOWN
- Suite baseline on this base: **2426 passed, 0 failed, 10 skipped**. Run it in the
  `muxplex-test` DTU, never on the host. Any new failure is yours.
- `frontend/tests/test_shared_scope.mjs` may be RED (pre-existing vm-harness gap). Not yours.
- `muxplex/tests/test_agent_fence.py` needs root + `aa-svc` + iptables; cannot run here or in CI.
- amplifier-agent's HTTP face emits `index: 0` for ALL parallel tool calls. `chat.js` works
  around it by keying on `index:id`. Do not "simplify" that away.
- Transcripts persist unconditionally at
  `/home/aa-svc/.amplifier-agent/state/workspaces/<ws>/sessions/<id>/transcript.jsonl`.
  Owner has ACCEPTED this for now; the only requirement is disclosure, not a retention policy.

# Shared context for every lane in batch `mxp1`

## Where things are
- Repo: this worktree. Base commit `5b77074` on `poc/agent-chat-panel`.
- Running system: DTU container `muxplex-lan-twin`.
  Exec: `amplifier-digital-twin exec --visual-id "" muxplex-lan-twin -- <cmd>`
  muxplex inside at `127.0.0.1:8088`, source `/opt/muxplex`, systemd unit `muxplex`.
  LAN `http://192.168.1.5:8092/` · loopback `http://localhost:8093/`
  Login: password-only form (NO username field). Credential is NOT in this repo —
  read it from the container: `amplifier-digital-twin exec --visual-id "" muxplex-lan-twin -- \
  python3 -c "import json;print(json.load(open('/root/.config/muxplex/settings.json')).get('password','<see auth.py'))"`
  or ask the operator. NEVER write it into a tracked file — this repo is PUBLIC.
  Live seeded tmux sessions: `counter`, `logtail`, `sysmon`.
- Agent sidecar: `amplifier-agent serve chat-completions` v0.12.0 as user `aa-svc`
  on `127.0.0.1:9099`, unit `amplifier-agent-http`. muxplex proxies via
  `POST /api/agent/chat/completions`.

## THE ONE CONTAINER PROBLEM
There is exactly ONE running muxplex. Lanes share it. The `panel` lane holds it
most of the time. Before you verify, check nothing else is mid-verification.
NEVER restart the `muxplex` service — a previous run did and wiped the seeded
tmux sessions.

## Architecture you must not break
Tools are DECLARED to the model but EXECUTED BY THE BROWSER, same-origin, with the
logged-in user's cookie. The sidecar holds NO muxplex credential and is firewalled
from muxplex by UID (`muxplex-agent-fence.service`). The agent can never exceed the
user's own authority. Do not add any path that changes this.
The write-confirmation gate in front of `send_muxplex_session_input` is a safety
feature, not friction. Do not remove or auto-approve it.

## HARD CONSTRAINTS — safety critical
1. Host port 8088 is the owner's LIVE PRODUCTION muxplex. Never bind, proxy, stop,
   restart or touch it. All runtime work happens in the DTU container.
2. NEVER run `tmux kill-server`, `pkill tmux`, or `killall tmux` — with or without
   `-L`/`-S`. This has destroyed 70+ live sessions twice on this machine.
3. Do not restart the container's `muxplex` service.
4. Only ever send terminal input to `counter`, `logtail`, `sysmon`.
5. No fallbacks, no mocks, no synthetic data. Failures surface loudly.
6. Never commit a secret. The sidecar env file holds a real ANTHROPIC_API_KEY.
   Verify by grepping the LITERAL value, not just a pattern.
7. Do not push. Do not merge to main. Commit to your own branch only.

## PROOF BAR — this is the whole game
Three times in this project a change was reported "working" on non-browser evidence
and was dead or clobbered in the browser. Curl is NOT proof for anything the user sees.
Use the browser-bridge `browser_*` tools (device `edge-macos`,
id `16909b75-aeec-4bd7-ae9a-c184ed08222a`). `agent-browser` has no Linux ARM64 Chrome
build on this host. For narrow viewports, local headless FIREFOX works (Chromium
crashes rendering this app on aarch64) — state which client produced which evidence.
Use `browser_vision_read` on actual pixels for anything visual: a broken layout still
yields perfectly valid DOM text.

## KNOWN
- `frontend/tests/test_shared_scope.mjs` is RED and was red before this work. Pre-existing
  branch defect (vm harness lacks `document`/`performance` stubs). NOT yours. Do not
  "fix" it by weakening it.
- `muxplex/tests/test_agent_fence.py` needs `MUXPLEX_TEST_ALLOW_LIVE_HOST=1`, root,
  `aa-svc` and iptables. It cannot run on this host or in CI. Do not delete it.
- amplifier-agent's HTTP face emits `index: 0` for ALL parallel tool calls. `chat.js`
  works around it by keying on `index:id`. Do not "simplify" that away.

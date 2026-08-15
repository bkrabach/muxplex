# The agent chat sidecar (POC deployment notes)

> **Status: proof-of-concept.** This documents a working POC, not a shipped
> feature. The panel (`muxplex/frontend/chat.js`) and the proxy route
> (`/api/agent/chat/completions`) are in-repo; the sidecar that route talks to
> is *not* — it is a separate process, owned by a separate OS user, wired up
> out-of-band. This file is that wiring, written down, so the POC can be stood
> back up from a clean box instead of being re-derived.

> **Setting it up for the first time? → [`AGENT_CHAT_SETUP.md`](AGENT_CHAT_SETUP.md).**
> That page is the task-shaped path — install, configure a provider, prove it
> works, diagnose it when it doesn't — with the failure modes named and their
> real log output quoted. **This** page is the architecture and the reasoning
> behind it: read it to understand *why* the deployment looks like this, or
> when you need to change something the setup path doesn't cover.

## What this is

An AI chat panel embedded in the muxplex dashboard. The interesting property
is not the panel — it is where tool execution happens.

The model is *declared* six tools. It never calls any of them. Every tool call
comes back down the SSE stream to the browser, and **the browser executes it**,
against muxplex's own origin, with the logged-in user's own `muxplex_session`
cookie. The agent therefore inherits **exactly** the calling user's authority,
because it is literally the user's browser making every request.

The agent process holds **no muxplex credential of any kind** — no cookie, no
muxplex API key, no federation key. That is the whole point, and the sections
below are what make it structurally true rather than merely conventional.

## Request path

```
browser  --(muxplex_session cookie)-->  muxplex  --(sidecar bearer)-->  amplifier-agent
   ^                                                                         |
   |                                                                         |
   +----------------- SSE: tool_calls come back to the browser --------------+
   |
   +--(muxplex_session cookie)--> muxplex /api/sessions, /api/state, ...
                                  (the browser executes the tool, not the agent)
```

Traffic only ever flows browser → muxplex → agent. Never the reverse. There is
no path by which the sidecar initiates a call into muxplex — and the iptables
rule below is what removes that path from the realm of "we just don't do that."

### Which box needs a key, in a federated deployment

The owner's standing question, answered from the request path above rather than
by assumption: **the box that served the page, and only that box.**

`chat.js` POSTs to `/api/agent/chat/completions` as a *relative* URL, so it
reaches whichever muxplex served the page; that muxplex proxies to its **own**
sidecar at `_AGENT_PROXY_URL` (default `127.0.0.1:9099`). Peers are not in that
path at any point. One configured device is therefore enough — provided it is
the one the browser is pointed at. Two people opening the UI on two different
boxes need two configured boxes.

Federated *visibility* is already free, and for a structural reason rather than
a lucky one: tools execute in the browser with the user's own cookie, so
`list_muxplex_federated_sessions` hits `GET /api/federation/sessions` on the
serving muxplex, which already aggregates every peer. No peer needs a key, a
sidecar, or any configuration for its sessions to be visible to the agent.

Federated *driving* is a different matter, and it is a capability gap rather
than a configuration one: the other five tools all call local `/api/sessions`
and `/api/state` paths, and muxplex has **no federation input route at all**.
The agent can list a peer's sessions and cannot type into one.

`AMPLIFIER_AGENT_URL` does make it mechanically possible to point muxplex at a
sidecar on another host. **Not recommended** — the sidecar binds loopback and
the fence rejects its every other local destination, both deliberately;
remoting it means publishing an agent endpoint onto a network the fence
argument was never built against. Treat it as a design change, not a config
change. Operational detail: [`AGENT_CHAT_SETUP.md`](AGENT_CHAT_SETUP.md) §1.

## The six tools

All six are declared in `chat.js`'s `TOOLS` array and dispatched in the same
file. Each maps to an existing public `/api/*` endpoint — no new capability was
added for the agent, and no endpoint was widened for it.

| Tool | Endpoint | Kind |
|---|---|---|
| `list_muxplex_sessions` | `GET /api/sessions` | read |
| `get_muxplex_session_details` | `GET /api/sessions/{name}` | read |
| `list_muxplex_federated_sessions` | `GET /api/federation/sessions` | read |
| `switch_muxplex_session` | `POST /api/sessions/{name}/connect` | drive |
| `switch_muxplex_view` | `PATCH /api/state` | drive |
| `send_muxplex_session_input` | `POST /api/sessions/{name}/input` | **write (RCE by design)** |

### The input fence governs the agent exactly as it governs a human

`send_muxplex_session_input` hits the same fenced endpoint documented in
AGENTS.md → "Terminal input: `POST /api/sessions/{name}/input` (RCE by design,
fenced)". It is gated server-side by `settings.input_enabled` and
`settings.input_allowed_sessions` — both `LOCAL_ONLY_KEYS`, settable only by
the operator editing `settings.json` on disk, never over the API.

`chat.js` makes no attempt to open, probe around, or route past that fence. It
calls the endpoint and surfaces whatever muxplex decides, including a 403.
**This was proven in the POC**: with `input_enabled` false on disk, the agent's
input tool gets the same 403 a human clicking the same control gets. The
operator's on-disk fence is the single control point for both.

This is the load-bearing claim of the whole design: there is no "agent mode"
bypass, because the agent has no channel of its own to bypass anything with.

## Standing up the sidecar

Everything below lives outside the repo, on the host. Reproduce in order.

### 1. An unprivileged user for the agent

```bash
sudo useradd --system --create-home --shell /bin/bash aa-svc
```

The POC ran it as `uid=999(aa-svc) gid=990(aa-svc)`. **Note the UID** — the
iptables rule in §5 matches on it. Substitute your actual UID there.

### 2. Install amplifier-agent as that user

```bash
sudo -u aa-svc -H bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u aa-svc -H bash -lc 'uv tool install amplifier-agent'
```

POC ran `amplifier-agent, version 0.12.0`, landing at
`/home/aa-svc/.local/bin/amplifier-agent`.

### 3. Host config — provider selection, and prompt caching disabled

`/etc/amplifier-agent-host-config.json`, world-readable. Template:
[`agent-chat-sidecar/etc/amplifier-agent-host-config.json.example`](agent-chat-sidecar/etc/amplifier-agent-host-config.json.example).

```json
{
  "providers": {
    "anthropic": { "module": "anthropic", "config": { "enable_prompt_caching": false } }
  },
  "provider": {
    "module": "anthropic",
    "config": { "enable_prompt_caching": false }
  }
}
```

`enable_prompt_caching: false` is **a workaround, not a preference** — it works
around a filed upstream bug. Re-check whether it is still needed before
carrying this forward; if the upstream fix has landed, this whole file may be
deletable.

**This file also silently decides which provider you get, and that is not
obvious from looking at it.** When a `providers` block is present it is
*authoritative*: the sidecar loads exactly those providers and does not consult
provider environment variables for selection at all. Only when the block is
absent does it auto-enable from whatever credentials it can resolve. So on this
deployment, setting `OPENAI_API_KEY` in §4's env file changes nothing — you get
Anthropic, silently, because this file says so. Deleting the file (or just the
block) is what hands provider choice back to the environment.

A declared provider that cannot authenticate fails **loudly and at startup**,
not at message time: it logs `failed to enumerate models — AuthenticationError`
and, if no declared provider produced any models, the process exits `2` rather
than starting with an empty registry. See
[`AGENT_CHAT_SETUP.md`](AGENT_CHAT_SETUP.md) §3.1 and §7 for the operational
consequences and the verbatim log output.

### 4. The sidecar environment file

`/etc/amplifier-agent-http-aasvc.env`, mode `0600`, owned `aa-svc:aa-svc`.
Template:
[`agent-chat-sidecar/etc/amplifier-agent-http-aasvc.env.example`](agent-chat-sidecar/etc/amplifier-agent-http-aasvc.env.example).

```ini
AMPLIFIER_AGENT_HTTP_BIND=127.0.0.1
AMPLIFIER_AGENT_HTTP_PORT=9099
AMPLIFIER_AGENT_HTTP_WORKSPACE=muxplex-chat-poc
AMPLIFIER_AGENT_HTTP_MODEL_ID=amplifier
AMPLIFIER_AGENT_HTTP_CONFIG_PATH=/etc/amplifier-agent-host-config.json
PATH=/home/aa-svc/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
HOME=/home/aa-svc

# SECRETS — supply real values, never commit them:
AMPLIFIER_AGENT_HTTP_API_KEY=<generate: openssl rand -hex 32>
ANTHROPIC_API_KEY=<your Anthropic API key>
```

> `AMPLIFIER_AGENT_HTTP_API_KEY` is the bearer muxplex presents to the sidecar.
> It must match `AMPLIFIER_AGENT_BEARER_TOKEN` in §6. Generate it; do not reuse
> a credential from anywhere else. It is scoped to exactly one thing — "muxplex
> may call the agent's chat API" — and grants nothing in the other direction.

Bind is `127.0.0.1` deliberately: the sidecar is loopback-only, never on the
LAN, in the same spirit as AGENTS.md → "ttyd is loopback-only by design".

### 5. systemd unit

`/etc/systemd/system/amplifier-agent-http.service` — shipped as an artifact at
[`agent-chat-sidecar/etc/systemd/system/amplifier-agent-http.service`](agent-chat-sidecar/etc/systemd/system/amplifier-agent-http.service),
so it installs alongside its own fence drop-in rather than being retyped from
this page:

```ini
[Unit]
Description=amplifier-agent HTTP chat-completions face (muxplex chat POC sidecar, isolated user)
After=network.target

[Service]
Type=simple
User=aa-svc
Group=aa-svc
EnvironmentFile=/etc/amplifier-agent-http-aasvc.env
ExecStart=/home/aa-svc/.local/bin/amplifier-agent serve chat-completions
Restart=on-failure
RestartSec=2
WorkingDirectory=/home/aa-svc

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now amplifier-agent-http.service
```

### 6. The network fence — this is the mechanism, not decoration

The claim "the agent holds no muxplex credential" is only worth anything if the
agent also *cannot reach muxplex to use one*. muxplex treats loopback specially
(`muxplex/auth.py`, `_LOCALHOST_ADDRS`): a process on the same box that can open
a socket to muxplex is not merely "inside the network," it is inside the trust
boundary, **unauthenticated**.

> **An earlier revision of this section published a fence that did not hold.**
> It rejected two destinations — `127.0.0.1/32` and the host's LAN IP, port 8088
> only. muxplex binds `0.0.0.0:8088`, so it answers on all of `127.0.0.0/8`.
> Measured on the live POC host, `aa-svc` reached `127.0.0.2:8088` and
> `127.0.0.9:8088` with **HTTP 200, unauthenticated**, plus a second muxplex on
> `127.0.0.1:8188` and ttyd on `127.0.0.1:7681`. The hand-verification below it
> passed because it probed the one address the rule covered. **Do not rebuild
> that fence.** If you are reading a copy of this file that still shows two
> `iptables -A OUTPUT -d <addr>/32` commands here, it is stale.

The fence installed now inverts the default: `aa-svc` may not initiate a
connection to anything local, with one narrow allowance for the DNS stub
resolver it needs to reach its upstream model API. An address denylist cannot
express "you may not talk to this machine"; this does.

Artifacts live in [`agent-chat-sidecar/`](agent-chat-sidecar/), laid out to
mirror their install paths. See
[`agent-chat-sidecar/README.md`](agent-chat-sidecar/README.md) for the rule
set, the measurements above, and the design rationale.

```bash
cd docs/agent-chat-sidecar
sudo install -m 0755 usr/local/sbin/muxplex-agent-fence /usr/local/sbin/
sudo install -m 0644 etc/muxplex-agent-fence.conf        /etc/
sudo cp -a etc/systemd/system/.                          /etc/systemd/system/

# Name every port a muxplex serves on this box, and the sidecar's OS user.
sudoedit /etc/muxplex-agent-fence.conf

sudo systemctl daemon-reload
sudo systemctl enable --now muxplex-agent-fence.service
sudo systemctl enable --now muxplex-agent-fence-watchdog.timer
sudo systemctl restart amplifier-agent-http.service   # picks up the drop-in
```

`--reject-with tcp-reset` rather than `DROP` throughout: a reset fails fast and
loudly, instead of hanging until a timeout and looking like a network blip.

**Reboot persistence is the fence unit, not `iptables-persistent`.** There is
none installed and none is needed. `muxplex-agent-fence.service` re-derives the
rules on every boot and then *proves* them (`ExecStartPost=… verify 60`) before
declaring success. That is strictly stronger than restoring a saved ruleset — a
restore that no longer blocks anything still restores, silently. This fails, and
`Requires=`/`BindsTo=` on the sidecar mean the sidecar does not start.

Verify. The `verify` subcommand is the real check — it attempts the connections
the fence exists to stop, from the sidecar's own UID, over real sockets, at
every address muxplex answers on, and refuses to score a timeout or an error as
a pass:

```bash
sudo /usr/local/sbin/muxplex-agent-fence verify   # exit 0 only if PROVEN
sudo /usr/local/sbin/muxplex-agent-fence status   # human-readable rule dump
```

It runs a positive control first (root *must* be able to reach muxplex),
because without one "muxplex is down" and "the fence works" produce identical
green output. It also discovers muxplex's listening ports from the running
system and **fails** if a live instance is on a port missing from
`/etc/muxplex-agent-fence.conf` — a new instance widens the hole loudly rather
than silently.

Three independent locks keep the sidecar from running un-fenced, wired by
`/etc/systemd/system/amplifier-agent-http.service.d/fence.conf`: `Requires=`
(boot), `BindsTo=` (the fence unit failing at runtime stops the sidecar), and
`ExecStartPre=+…verify` (an operator starting the sidecar by hand over a chain
that has been flushed underneath a nominally-"active" fence unit). A 30s
watchdog timer re-proves the property and, on breach, logs `auth.alert` and
stops both units. There is deliberately no warn-and-continue, no
timeout-and-proceed, and no environment variable that turns any of it off.

**Side effect worth knowing before you debug it:** the sidecar can no longer
reach ttyd on `127.0.0.1:7681`, where it previously could. The "nothing local"
rule is destination-wide, not muxplex-port-specific, and ttyd is inside that
blast radius. This is intended — ttyd hands out terminals — but it means any
future local service `aa-svc` legitimately needs must be added to the fence
explicitly.

Regression coverage: `muxplex/tests/test_agent_fence.py` (see §"Known gaps" for
what it does and does not cover).

### 7. Point muxplex at the sidecar

`/etc/muxplex-agent-proxy.env`, mode `0600`, owned `root:root`. Template:
[`agent-chat-sidecar/etc/muxplex-agent-proxy.env.example`](agent-chat-sidecar/etc/muxplex-agent-proxy.env.example).

```ini
AMPLIFIER_AGENT_URL=http://127.0.0.1:9099
AMPLIFIER_AGENT_BEARER_TOKEN=<same value as AMPLIFIER_AGENT_HTTP_API_KEY in §4>
```

Drop-in at `/etc/systemd/system/muxplex.service.d/agent-proxy.conf`:

```ini
[Service]
EnvironmentFile=/etc/muxplex-agent-proxy.env
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart muxplex
```

If `AMPLIFIER_AGENT_BEARER_TOKEN` is unset, the proxy route returns a `503`
naming the missing variable rather than failing obscurely — see
`agent_chat_completions_proxy` in `muxplex/main.py`.

## Secrets inventory

Two secrets exist in this deployment. **Neither is in this repo, and neither
should ever be.**

| Secret | Lives in | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `/etc/amplifier-agent-http-aasvc.env` (`0600`) | Real upstream API key. Supply your own. |
| `AMPLIFIER_AGENT_HTTP_API_KEY` / `AMPLIFIER_AGENT_BEARER_TOKEN` | `/etc/amplifier-agent-http-aasvc.env` and `/etc/muxplex-agent-proxy.env` (both `0600`) | Same value on both sides. Generate per-deployment. |

Both env files are mode `0600` and outside the repo tree. This document names
the variables only.

## Known gaps

Honest list of what this POC does **not** have.

Closed since the first draft of this document:

- ~~**iptables rules do not survive reboot.**~~ Replaced (§6). Reboot
  persistence is now `muxplex-agent-fence.service`, which re-derives *and*
  re-proves the rules each boot. Note that non-persistence was never the worst
  of it — the fence it replaced was **porous while running**, reachable at
  `127.0.0.2:8088` unauthenticated. See
  [`agent-chat-sidecar/README.md`](agent-chat-sidecar/README.md).
- ~~**No test covers the fence.**~~ `muxplex/tests/test_agent_fence.py`, 6
  tests, probes as the real UID over real sockets at every address muxplex
  answers on. It requires `MUXPLEX_TEST_ALLOW_LIVE_HOST=1` and a deployment
  host — it is only evidence *because* it runs against a live system, which is
  exactly what `conftest.pytest_sessionstart` otherwise refuses.

Still open:

- **The fence test does not run in CI**, and cannot: CI has no `aa-svc` user,
  no live muxplex, and no iptables privileges. It skips there. The fence is
  therefore proven on the deployment host by the boot-time `verify` and the 30s
  watchdog, not by the pipeline. Treat a green CI run as saying nothing about
  the fence.
- **`chat.js` and the proxy route are still untested.** Nothing in `tests/` or
  `frontend/tests/` covers the panel, the six tool handlers, the write-
  confirmation gate, or `/api/agent/chat/completions`. The `input_enabled` 403
  result and the confirmation gate were both proven by hand.
- **`frontend/tests/test_shared_scope.mjs` is red because of `chat.js`**, and
  has been since the panel landed. That test evaluates every classic script in
  one shared vm context to catch top-level binding collisions; `chat.js` throws
  during top-level evaluation there — first on the loud-fail `__missing` DOM
  check (the harness's `document.getElementById` returns `null` for
  everything), and now earlier still on `performance`, which the harness
  sandbox does not stub. **Neither is a real collision**, which is what the
  test exists to catch — but a permanently-red guard protects nothing, and the
  next genuine collision will land in a test that was already failing. Fixing
  it means either stubbing the harness up to what `chat.js` touches at load
  time, or moving `chat.js`'s load-time work behind an init function. Not done
  here.
- **The write-confirmation gate is client-side only.** It is a
  mistake-and-surprise stop on `send_muxplex_session_input`, not a security
  boundary — the server-side `input_enabled` / `input_allowed_sessions` fence
  is the security boundary, and it is unchanged. Anyone with the user's cookie
  and a terminal can still call the endpoint directly. Do not let the dialog's
  presence be mistaken for authorization.
- **Prompt caching disabled** as an upstream-bug workaround (§3); revisit.
- **The sidecar is not in this repo** and is not versioned with it. A breaking
  change in `amplifier-agent`'s HTTP face breaks the panel with no signal here.
- **`MODEL` is hardcoded** in `chat.js`, as is the `9099` default in `main.py`
  — and the consequence is larger than "not configurable." The sidecar's served
  model list is enumerated **live from the configured provider** at startup and
  published at `GET /v1/models`; `AMPLIFIER_AGENT_HTTP_MODEL_ID` does not
  constrain it (measured: that variable was `amplifier` while the served list
  was three Claude ids, none of them `amplifier`). Whatever `chat.js` sends must
  appear in that list or **every turn** returns
  `400 {"code":"unknown_model"}` — verified live by requesting `gpt-4o`. So
  pointing the host config at a non-Anthropic provider breaks the panel until
  `chat.js` is edited too: **changing provider is not a configuration-only
  change today.** Making the panel's model selectable, and surfacing the active
  provider/model in the UI, is the fix; see
  [`AGENT_CHAT_SETUP.md`](AGENT_CHAT_SETUP.md) §3.5.
- **A dead sidecar renders as an empty turn.** When the sidecar is not
  listening, the proxy route emits a loud in-stream error frame
  (`agent sidecar unreachable at …`, `main.py`) — but `chat.js`'s stream loop
  drops any SSE chunk carrying no `choices`, and that frame carries exactly
  that shape. The user sees a turn that ends with nothing. Since a missing or
  invalid provider key is a *startup* failure (exit `2`), this is the normal
  presentation of the single most common misconfiguration. Rendering
  stream-level `error` objects in the panel would close it.
- **The debug-capture recorder is always on.** `chat.js` wraps all six tool
  handlers plus page-wide console/error hooks and buffers events in memory for
  the Export button. It is capped, and never leaves the browser unless a human
  clicks Export — but it is instrumentation shipped in the POC path, and should
  be a deliberate decision before this becomes a feature.

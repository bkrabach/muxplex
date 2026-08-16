# Setting up the agent chat panel

This is the first-run path: install it, give it a model provider, prove it
works, and diagnose it when it doesn't. Follow it top to bottom on a box that
already runs muxplex.

You should not have to read any source file or work item to get through this
page. If you do, that is a bug in this page — say so.

- **Why the architecture is the way it is** → [`AGENT_CHAT_SIDECAR.md`](AGENT_CHAT_SIDECAR.md).
- **Why the network fence exists and what it proves** → [`agent-chat-sidecar/README.md`](agent-chat-sidecar/README.md).

> **Status: proof-of-concept.** The panel and muxplex's proxy route ship in
> this repo. The thing they talk to — the *sidecar* — does not: it is a
> separate program (`amplifier-agent`), run as a separate OS user, wired up by
> hand. That wiring is what this page walks you through.

---

## 0. The 60-second version

Four moving parts. All four must be true or the panel does nothing useful.

| # | Part | Where it lives | How you know it's right |
|---|---|---|---|
| 1 | `amplifier-agent` installed | `/home/aa-svc/.local/bin/amplifier-agent` | `amplifier-agent --version` |
| 2 | A **provider API key** | sidecar's env file, `0600` | sidecar is `active`, `GET /v1/models` lists models |
| 3 | The **shared bearer**, same value on both sides | sidecar env file **and** muxplex env file | `/v1/models` returns `200`, not `401` |
| 4 | The **network fence** | `muxplex-agent-fence.service` | `muxplex-agent-fence verify` exits `0` |

The single most common way this goes wrong: **the sidecar refuses to start
because of the API key, and the panel reports it as a network problem** — or
reports nothing at all. §7 covers that specifically, with the real log output.
It is worth reading before you need it.

---

## 1. Which box needs configuring

**The box that serves the page.** Nothing else.

The panel POSTs to `/api/agent/chat/completions` as a *relative* URL, so it
lands on whichever muxplex served the page you are looking at. That muxplex
proxies to its **own** sidecar (`AMPLIFIER_AGENT_URL`, default
`http://127.0.0.1:9099`). So: load the UI from `spark-1`, and `spark-1` is the
box that needs a key and a sidecar. A peer with neither still works fine as a
peer.

Most people assume the opposite, so, stated plainly:

- **One configured device is enough for a federated deployment**, as long as it
  is the one you actually open the browser on. Peers need no key, no sidecar,
  and no configuration of any kind for the panel to work.
- If two people each open the UI on their *own* box, **each of those boxes**
  needs its own key and sidecar. What matters is where the page came from, not
  who is in the federation.
- **Federated visibility is already free.** Tools execute in *your browser*
  with *your* cookie, so `list_muxplex_federated_sessions` reaches the
  aggregating `GET /api/federation/sessions` endpoint on the serving muxplex,
  which already fans out to peers. No peer needs a key for you to see its
  sessions.

### What the agent can see vs. what it can drive

This is a real asymmetry and it is not a configuration problem — do not go
looking for a setting that fixes it.

| Tool | Endpoint it calls | Federation-aware? |
|---|---|---|
| `list_muxplex_federated_sessions` | `GET /api/federation/sessions` | **yes** — aggregates every peer |
| `list_muxplex_sessions` | `GET /api/sessions` | no — serving box only |
| `get_muxplex_session_details` | `GET /api/sessions/{name}` | no — serving box only |
| `switch_muxplex_session` | `POST /api/sessions/{name}/connect` | no — serving box only |
| `switch_muxplex_view` | `PATCH /api/state` | no — serving box only |
| `send_muxplex_session_input` | `POST /api/sessions/{name}/input` | no — serving box only |

So the agent can **list** a peer's sessions and can **not** type into one.
muxplex has federation routes for connect and for session create/delete
(`/api/federation/{device_id}/...`), and the panel does not call them; there is
**no federation input route in muxplex at all**, so typing into a peer's
session is not a thing the panel could be pointed at today even if it tried.

### Can the sidecar live on another box?

Mechanically yes — `AMPLIFIER_AGENT_URL` accepts any URL. **Don't.**

The sidecar's bind is `127.0.0.1` and the fence rejects everything else it
might reach locally, both deliberately. Moving it off-box means publishing an
unauthenticated-by-default agent endpoint onto a network and re-deriving the
whole fence argument against a threat model it was not designed for. If you
genuinely need this, treat it as a design change, not a config change.

---

## 2. Install the sidecar

The sidecar runs as its own unprivileged user. That is load-bearing: the
network fence in §4 matches on that user's UID.

```bash
sudo useradd --system --create-home --shell /bin/bash aa-svc
sudo -u aa-svc -H bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u aa-svc -H bash -lc 'uv tool install amplifier-agent'
sudo -u aa-svc -H /home/aa-svc/.local/bin/amplifier-agent --version
```

Expect `amplifier-agent, version 0.12.0` or later. Note the UID
(`id -u aa-svc`) — §4 needs it.

---

## 3. Give it a provider and a key

This is the step the rest of the page exists for. Read §3.1 before writing
anything: which provider you get is decided by a file most people don't know is
authoritative.

### 3.1 How the provider is actually chosen

There are two paths, and the first one wins whenever it is present.

**Path A — an explicit `providers:` block in the host-config file.** If
`/etc/amplifier-agent-host-config.json` declares a `providers` object, that
object is authoritative and *nothing else is consulted*. Setting
`OPENAI_API_KEY` on a box whose host config declares only `anthropic` gets you
Anthropic, silently, forever.

**Path B — no `providers:` block.** Only then does the sidecar auto-enable from
whatever credentials it can resolve (a provider env var, or a key stored via
`amplifier-agent auth set`).

The deployment documented in `AGENT_CHAT_SIDECAR.md` §3 uses **Path A**, with
`anthropic` as the only entry. If you copy that file and then wonder why your
OpenAI key is being ignored, this paragraph is the answer.

### 3.2 Which env var goes with which provider

| Provider | Environment variable(s) |
|---|---|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` (or `AZURE_OPENAI_AD_TOKEN`) |
| Ollama | `OLLAMA_HOST` |
| GitHub Copilot | `GITHUB_TOKEN` — environment only, no credential-store path |

Alternative to env vars: `amplifier-agent auth set <provider> <key>`, which
writes `~/.amplifier-agent/credentials.json` (mode `0600`) for that OS user —
so `sudo -u aa-svc -H`, not your own shell. `auth list|status|remove|clear`
round it out.

> **Auto-detect precedence** (`ANTHROPIC` > `OPENAI` > `AZURE` > `OLLAMA`) is a
> **CLI-path** behaviour. The HTTP face has no `provider: "auto"` and does not
> use it. On the HTTP face, Path A or Path B above is the whole story.

### 3.3 The host-config file

`/etc/amplifier-agent-host-config.json`, world-readable, **no secrets in it**:

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

Template: [`agent-chat-sidecar/etc/amplifier-agent-host-config.json.example`](agent-chat-sidecar/etc/amplifier-agent-host-config.json.example).

Two things worth knowing:

- The schema is a **closed 7-key set** — `mcp`, `approval`, `provider`,
  `providers`, `allowProtocolSkew`, `skills`, `debug`. An unknown key is a hard
  startup error, not a warning. It is a **file path only**; there is no way to
  pass this inline.
- `enable_prompt_caching: false` is **a workaround for a filed upstream bug**,
  not a preference. Re-check whether it is still needed before carrying it
  forward; if the fix has landed, this file may be deletable entirely — which
  would move you to Path B and let env vars pick the provider.

To use a different provider, change **this file**, not just the env var. To let
env vars decide, delete the `providers` block (Path B).

### 3.4 The sidecar env file

`/etc/amplifier-agent-http-aasvc.env`, mode `0600`, owned `aa-svc:aa-svc`.
Template: [`agent-chat-sidecar/etc/amplifier-agent-http-aasvc.env.example`](agent-chat-sidecar/etc/amplifier-agent-http-aasvc.env.example).

```ini
AMPLIFIER_AGENT_HTTP_BIND=127.0.0.1
AMPLIFIER_AGENT_HTTP_PORT=9099
AMPLIFIER_AGENT_HTTP_WORKSPACE=muxplex-chat-poc
AMPLIFIER_AGENT_HTTP_MODEL_ID=amplifier
AMPLIFIER_AGENT_HTTP_CONFIG_PATH=/etc/amplifier-agent-host-config.json
PATH=/home/aa-svc/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
HOME=/home/aa-svc

# SECRETS — real values only, never committed:
AMPLIFIER_AGENT_HTTP_API_KEY=<openssl rand -hex 32>
ANTHROPIC_API_KEY=<your provider key>
```

```bash
sudo install -m 0600 -o aa-svc -g aa-svc \
  docs/agent-chat-sidecar/etc/amplifier-agent-http-aasvc.env.example \
  /etc/amplifier-agent-http-aasvc.env
sudoedit /etc/amplifier-agent-http-aasvc.env     # fill in the two secrets
```

### 3.5 `AMPLIFIER_AGENT_HTTP_MODEL_ID` does not choose your model

It looks like it does. It doesn't, and this trips people up.

The served model list is **enumerated live from the provider at startup** and
published at `GET /v1/models`. Measured on the reference deployment, with
`AMPLIFIER_AGENT_HTTP_MODEL_ID=amplifier` set:

```
COUNT 3
  claude-haiku-4-5-20251001
  claude-opus-5
  claude-sonnet-5
```

`amplifier` is not in that list. The variable names the id advertised by the
POC's own single-model path; it does not constrain, select, or rename anything
the registry serves.

**What actually decides the model is the panel**, which sends a hardcoded id
(`claude-sonnet-5`, `muxplex/frontend/chat.js`). That id must appear in
`GET /v1/models` or every turn fails:

```
HTTP 400
{"detail":{"error":{"type":"invalid_request_error","code":"unknown_model",
 "message":"model 'gpt-4o' is not served by this instance.
            Call GET /v1/models for the list of served models."}}}
```

So **switching provider is not a one-line change today.** Point the host config
at OpenAI and every turn 400s, because the panel is still asking for a Claude
model. Until the panel's model becomes configurable, a non-Anthropic provider
also requires an edit to `chat.js`. That is a known gap, not something you
misconfigured.

---

## 4. Fence it, then start it

The sidecar must not be able to reach muxplex. muxplex trusts loopback
*unauthenticated*, so an un-fenced sidecar has the entire muxplex API for free —
which would quietly demolish the whole "the agent inherits only the user's
authority" claim.

Full rationale, the measured hole this replaced, and why `verify` is the
load-bearing subcommand: [`agent-chat-sidecar/README.md`](agent-chat-sidecar/README.md).

```bash
cd docs/agent-chat-sidecar
sudo install -m 0755 usr/local/sbin/muxplex-agent-fence /usr/local/sbin/
sudo install -m 0644 etc/muxplex-agent-fence.conf        /etc/
sudo install -m 0644 etc/systemd/system/amplifier-agent-http.service /etc/systemd/system/
sudo cp -a etc/systemd/system/.                          /etc/systemd/system/

sudoedit /etc/muxplex-agent-fence.conf   # AA_USER + every port a muxplex serves here

sudo systemctl daemon-reload
sudo systemctl enable --now muxplex-agent-fence.service
sudo systemctl enable --now muxplex-agent-fence-watchdog.timer
sudo systemctl enable --now amplifier-agent-http.service
```

The sidecar unit `Requires=` and `BindsTo=` the fence unit, so an unfenced
sidecar cannot start and a fence that dies takes the sidecar with it. That is
intentional: there is no warn-and-continue and no env var that turns it off.

---

## 5. Point muxplex at the sidecar

`/etc/muxplex-agent-proxy.env`, mode `0600`, owned `root:root`. Template:
[`agent-chat-sidecar/etc/muxplex-agent-proxy.env.example`](agent-chat-sidecar/etc/muxplex-agent-proxy.env.example).

```ini
AMPLIFIER_AGENT_URL=http://127.0.0.1:9099
AMPLIFIER_AGENT_BEARER_TOKEN=<same value as AMPLIFIER_AGENT_HTTP_API_KEY>
```

`AMPLIFIER_AGENT_BEARER_TOKEN` **must be byte-identical** to
`AMPLIFIER_AGENT_HTTP_API_KEY` from §3.4. It is the one credential in this
system that muxplex presents to the sidecar, and it grants exactly one thing —
"muxplex may call the agent's chat API". It grants nothing in the other
direction. Generate it fresh; never reuse the federation key or anything else.

Drop-in at `/etc/systemd/system/muxplex.service.d/agent-proxy.conf`:

```ini
[Service]
EnvironmentFile=/etc/muxplex-agent-proxy.env
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart muxplex
```

> **Before you restart muxplex, read AGENTS.md → "Two ways to destroy every
> live tmux session on this host."** Confirm `KillMode=process` is resolved for
> the unit (`systemctl --user show muxplex.service -p KillMode`). Under the
> default `KillMode`, a restart can SIGKILL the tmux server muxplex parented,
> taking every live session with it. This has happened.

---

## 6. Prove it works

Run these in order. Each one isolates a different link, so the first failure
names the layer. Substitute your own paths if you deviated.

**1 — the fence holds.** Nothing else matters if this fails.

```bash
sudo /usr/local/sbin/muxplex-agent-fence verify   # exit 0 only if PROVEN
#   ok    aa-svc BLOCKED from 127.0.0.1:8088
#   ok    aa-svc BLOCKED from 127.0.0.2:8088
#   VERIFY OK: aa-svc cannot reach muxplex on any local address.
```

**2 — both services are up.**

```bash
systemctl is-active amplifier-agent-http.service   # active
systemctl is-active muxplex.service                # active
ss -ltnp | grep -E '9099|8088'
#   127.0.0.1:9099   amplifier-agent
#   0.0.0.0:8088     muxplex
```

**3 — the sidecar has a working provider key.** This is the one that catches a
bad key, and it is the check most worth remembering.

```bash
set -a; . /etc/amplifier-agent-http-aasvc.env; set +a
curl -sS -H "Authorization: Bearer $AMPLIFIER_AGENT_HTTP_API_KEY" \
  http://127.0.0.1:9099/v1/models
```

A JSON list of models means the provider authenticated. An **empty list is not
possible** — the sidecar refuses to start rather than serve zero models.

- `401 {"error":{"message":"Invalid API key"...}}` → wrong **bearer**, not a
  provider problem. §3.4 and §5 disagree with each other.
- connection refused → the sidecar is not running. Go to §7.

**4 — the model the panel asks for is served.** The single most likely reason a
correctly-installed panel still fails every turn:

```bash
grep -n 'var MODEL' muxplex/frontend/chat.js     # what the panel sends
```

That id must appear in step 3's output. If it doesn't, every turn returns
`400 unknown_model` (§3.5).

**5 — muxplex's proxy is configured.** A deliberately empty body, so no turn
runs:

```bash
curl -sS -w '\nhttp=%{http_code}\n' -X POST \
  http://127.0.0.1:8088/api/agent/chat/completions \
  -H 'Content-Type: application/json' -d '{}'
```

Read the **body**, not just the code — once the token check passes this route
is a streaming relay, so it answers `200` regardless of what the sidecar
thinks:

| Response | Means |
|---|---|
| `http=200` + `{"detail":[{"type":"missing","loc":["body","model"]...` | **Correct.** muxplex authenticated to the sidecar and relayed; the sidecar rejected the empty body, which is the point. |
| `http=503` + `Agent proxy is not configured on this server (AMPLIFIER_AGENT_BEARER_TOKEN unset)` | muxplex never loaded this section's env file — check the drop-in, and that you restarted muxplex. |
| `http=200` + `{"error":{"message":"Invalid API key"...` | Bearer mismatch between §3.4 and §5. |
| `http=200` + `agent sidecar unreachable at http://127.0.0.1:9099` | Sidecar is down. Go to §7 — this is almost always a key problem. |

**6 — the browser.** Open muxplex, click **Agent**, send "list my sessions".

Steps 1–5 are necessary and **not sufficient**: they prove the plumbing, and
they prove nothing about what a user sees. Four times in this project a change
passed on non-browser evidence and was broken in the browser. The panel is not
working until you have watched it answer.

---

## 7. When it doesn't work

### First move, always

```bash
systemctl status amplifier-agent-http.service
journalctl -u amplifier-agent-http.service -n 50 --no-pager
```

The sidecar's own log is precise about configuration failures. The panel is
not. Start here, not in the browser console.

### The failure mode that looks like something else

**A bad or missing provider key does not fail at message time. It stops the
sidecar from starting at all.** Both cases below were reproduced against a real
sidecar; the log lines are verbatim.

*No credentials resolvable anywhere:*

```
ERROR ... no providers configured and no resolvable credentials. Set one via
`amplifier-agent auth set <provider> <key>`, export the provider's env var
(e.g. ANTHROPIC_API_KEY), or pass --config with an explicit `providers:` block.
ERROR:    Application startup failed. Exiting.
```

*A key that is present but invalid, with an explicit `providers:` block:*

```
INFO:httpx:HTTP Request: GET https://api.anthropic.com/v1/models "HTTP/1.1 401 Unauthorized"
ERROR ... provider 'anthropic': failed to enumerate models — AuthenticationError:
        Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error',
        'message': 'API key is invalid.'}}
ERROR ... no provider produced any models (all 1 declared provider(s) failed or
        returned empty lists). Cannot start.
ERROR:    Application startup failed. Exiting.
```

Both exit `2`. With `Restart=on-failure`, systemd restart-loops it, so
`is-active` may read `activating` rather than a clean `failed`.

**What you see in the browser** is the panel telling you exactly this. The
sidecar is simply absent, so muxplex's proxy emits an in-stream error naming
*its* layer — `agent sidecar unreachable at http://127.0.0.1:9099` — and the
panel renders that as a visible error bubble:

> **Error: The agent sidecar isn't running.**
> This is almost always a missing or invalid provider API key at sidecar
> startup, not a network problem. Its own log names the exact reason -- on the
> box running the sidecar: `journalctl -u amplifier-agent-http -n 50`

That is the panel doing its job — you should not need the rest of this
section for the common case above. Keep reading only if you want the full
diagnosis or you hit a *different* stream-level error than this one.

> **Fixed in muxplex-695, confirmed against a real browser.** Earlier versions
> of the panel dropped any SSE chunk carrying no `choices`
> (`muxplex/frontend/chat.js`'s stream loop), and this exact error frame
> carries that shape — so the message above was silently discarded and the
> turn ended with nothing rendered at all: no error, no explanation, just the
> "Thinking..." status left on screen indefinitely. That was reproduced
> against a genuinely stopped sidecar in a real browser (not inferred from
> source), then fixed by handling any `error`-carrying frame in the stream
> loop *before* the `choices` check — not just this one message, since the
> proxy or the sidecar can each emit this shape for different underlying
> causes. If you are running a panel old enough to predate that fix, the
> symptom is a silent hang instead of the error box above; the diagnosis
> command below is unchanged either way.

Either way the diagnosis is the same and does not depend on which of those the
panel does: `journalctl -u amplifier-agent-http`. The sidecar knows exactly
what is wrong; the panel is the wrong place to look.

### Symptom table

| What you see | Most likely cause | Fix |
|---|---|---|
| "Error: The agent sidecar isn't running." | Sidecar not running — almost always a key problem at startup | `journalctl -u amplifier-agent-http -n 50` |
| `HTTP 503 -- ...AMPLIFIER_AGENT_BEARER_TOKEN unset` | muxplex never loaded §5's env file | Check the drop-in; `systemctl restart muxplex` (heed §5's warning) |
| `HTTP 401 ... Invalid API key` | Bearer mismatch between §3.4 and §5 | Make the two values byte-identical; restart both |
| Every turn `400 unknown_model` | Panel's model id isn't served by the configured provider | §3.5 — compare `chat.js`'s `MODEL` against `GET /v1/models` |
| Sidecar won't start, fence named in the error | Fence unit down; `BindsTo=` is doing its job | `systemctl start muxplex-agent-fence`, then `muxplex-agent-fence verify` |
| Agent lists sessions but **403** on typing | muxplex's input fence — working as designed | Not an agent setting. `input_enabled` + `input_allowed_sessions` in `settings.json`, on disk, local-only |
| Set a new provider env var, nothing changed | Host config's `providers:` block is authoritative | §3.1 — edit the host config, or delete the block |
| Everything green, panel still dead | Stale cached frontend | Hard-reload; installed PWAs cache the app shell aggressively |

### Things that are not bugs

- **The sidecar can't reach `127.0.0.1:7681` (ttyd).** Intended. The fence
  rejects *all* sidecar-initiated local TCP, not just muxplex's ports.
- **A `403` on typing.** The input fence governs the agent exactly as it
  governs a human. Both settings are local-file-only and cannot be changed
  through any API, by the agent or anyone else.
- **Transcripts on disk.** Every turn persists to
  `~aa-svc/.amplifier-agent/state/workspaces/<workspace>/sessions/<id>/transcript.jsonl`,
  unconditionally, with no retention window. This is a known, accepted POC
  property — tool results inside can carry terminal scrollback. It lives on the
  sidecar's box, not the user's device.

---

## 8. Secrets: where they are and what they'd cost

Two secrets. **Neither is in this repo and neither should ever be.** Both files
are mode `0600` and outside the repo tree; this page names only the variables.

| Secret | Lives in | Blast radius if leaked |
|---|---|---|
| Provider API key (`ANTHROPIC_API_KEY` etc.) | `/etc/amplifier-agent-http-aasvc.env` | Your upstream model account. Nothing muxplex-related. |
| Shared bearer (`AMPLIFIER_AGENT_HTTP_API_KEY` = `AMPLIFIER_AGENT_BEARER_TOKEN`) | `/etc/amplifier-agent-http-aasvc.env` **and** `/etc/muxplex-agent-proxy.env` | Permission to call the sidecar's chat API from that box. Grants **nothing** in muxplex — the sidecar holds no muxplex credential and the fence blocks the path regardless. |

### On in-app key entry

The work item behind this page left "optional in-app key entry" open, with the
condition that if it happens, the storage decision must be stated rather than
defaulted. Stating it:

**Today the answer is env-var-only, deliberately.** The key is readable only by
`root` and `aa-svc`, on one box, in one file.

Putting a provider API key in browser `localStorage` would be a materially
worse trade, and worth naming precisely:

- It moves a **server-side** secret onto **every device** that opens the UI, and
  multiplies the number of places it can leak by the number of clients.
- `localStorage` is readable by any script that achieves execution on the
  origin. muxplex renders model output and tool results into the page; the
  markdown renderer is built to make that safe, but "the key is only as safe as
  every future rendering path" is a far weaker property than "the key is in a
  `0600` file the browser cannot address."
- It buys nothing structural. The sidecar — not the browser — is what talks to
  the provider, so a browser-held key would have to be *sent to the sidecar
  anyway*.

If in-app entry is ever built, the defensible shape is entry that writes to the
**sidecar's own credential store** (`amplifier-agent auth set`, mode `0600`)
over an authenticated admin path, and never persists the key in the browser.
That is a design change with its own threat model — not a settings-tab checkbox.

Displaying **which provider and model are active** is a different question with
no such tradeoff, and is worth doing on its own.

---

## 9. What this setup does not give you

Honest list, so you don't go hunting for switches that don't exist.

- **No in-app configuration.** Provider, model, and keys are all
  files-on-the-box. The Agent settings tab does not yet show which provider or
  model is live.
- **No model switching.** The panel's model id is hardcoded; the sidecar serves
  whatever the configured provider enumerates. Changing provider families needs
  a code edit (§3.5).
- **No first-class "not configured" state.** A dead sidecar renders as an empty
  turn (§7).
- **The sidecar is not versioned with muxplex.** A breaking change in
  `amplifier-agent`'s HTTP face breaks the panel with no signal in this repo.
  Pin the version you tested.
- **The fence is not covered by CI** and cannot be — CI has no `aa-svc`, no live
  muxplex, and no iptables privileges. It is proven on the deployment host by
  the boot-time `verify` and the 30s watchdog. **A green CI run says nothing
  about the fence.**

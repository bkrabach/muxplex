# Agent provider credentials from Settings → Agent

**Status:** design, not implemented. Release blocker for public `main`.
**Scope:** how a provider API key gets from a browser form into the agent sidecar,
without muxplex becoming a credential-holding service and without weakening the
sidecar's isolation.
**Branch:** `poc/agent-chat-panel`.

---

## 0. Verdict on route 3

**Route 3 works.** The sidecar reads `credentials.json` and serves from it, and
key material is re-resolved **per request** — a rotated key takes effect on the
very next turn with no restart. Both facts were proven against a live sidecar,
not inferred (§2).

Route 3 also carries one property nobody flagged going in, and it changes the
design materially:

> **The sidecar cannot run without a working credential.** A missing *or invalid*
> key is a startup failure (`sys.exit(2)`), and with `Restart=on-failure` systemd
> restart-loops it forever. There is no "running but unconfigured" state.

So the naive shape — *write the file, restart, done* — has a trapdoor: **writing
a bad key and restarting produces a permanently dead sidecar**, and the panel
renders that as an empty turn. The design below closes it by validating the key
before it is ever persisted, and by restarting only when a restart is actually
required.

---

## 1. What was verified

Everything in this section was checked against source or a live system. Line
references are to this workspace at the time of writing.

### 1.1 From source

| Claim | Evidence |
|---|---|
| Credential resolution is **env-first**, then file | `provider_sources.py:315-410` (`resolve_credential_detailed`) |
| The file is read from disk on **every** call — no cache, no memoisation | `admin/auth.py:92-124` (`_load_credentials` → `path.read_text()`) |
| Provider injection happens **per request**, and deliberately defeats `inject_provider`'s "don't clobber" guard by wiping the list first | `_session_runner.py:281-288`; guard at `provider_sources.py:600-637` |
| Which providers are *served* is fixed **once at boot** | `app.py:274-330` builds `served_models_registry` in the lifespan only |
| No resolvable credential at boot → `sys.exit(2)` | `app.py:259-267` |
| No provider produced models at boot → `sys.exit(2)` | `app.py:344-350` |
| Writes are atomic, `0600`, dir `0700` | `admin/auth.py:127-155` |
| `auth set` accepts `--stdin` specifically to keep the key out of argv | `admin/auth.py:276-286` |
| `auth set` refuses `github-copilot` (env-only provider) | `admin/auth.py:74`, `316-327` |
| `auth set --endpoint` writes an **arbitrary URL** into the credentials entry, and the resolver feeds it to the provider | `admin/auth.py:287-291, 349-350`; `provider_sources.py:293-313` |
| muxplex's settings sync is an **allowlist** (`SYNCABLE_KEYS`), not a denylist | `settings.py:393-422`, `settings.py:1080-1087` |
| `LOCAL_ONLY_KEYS` means "never settable over the API" | `settings.py:298-310` |
| muxplex waves through **all** loopback requests with no auth at all | `auth.py:266`, `auth.py:330-333` |
| The agent's tools are a hardcoded `if`-chain in the browser — there is no generic HTTP tool | `frontend/chat.js:39` (`TOOLS`), `chat.js:1729` (`executeToolCall`) |
| muxplex runs as root, same systemd namespace as the sidecar, with `sudo`/`systemctl` available | measured in the twin (§1.2) |

### 1.2 From the live system (DTU `muxplex-lan-twin`)

The twin was driven through the whole matrix and **restored to its original
state** afterwards (env var back, `credentials.json` removed, sidecar healthy,
`NRestarts=0`).

**Test A/B/C — per-request read, single PID 929632 throughout, no restart:**

| Step | File contents | Result |
|---|---|---|
| A | good key | `"content": "PONG"` |
| B | key swapped to bogus, **no restart** | `[amplifier-agent error: ... AuthenticationError: ... "API key is invalid."]` |
| C | good key restored, **no restart** | `"content": "PONG"` |

`MainPID` was `929632` before A and after C. **The running process picked up
both file changes mid-life.** This is the load-bearing result.

**Test D — bogus key present at boot:**

```
provider 'anthropic': failed to enumerate models — AuthenticationError: Error code: 401 ...
ERROR:amplifier_agent_http:amplifier-agent serve: no provider produced any models
        (all 1 declared provider(s) failed or returned empty lists). Cannot start.
```
`NRestarts` climbed to 5 and kept going. Crash loop.

**Test E — no credential anywhere at boot:**

```
provider 'anthropic': credentials missing — ANTHROPIC_API_KEY not set and no
        credentials.json entry for 'anthropic'; cannot fetch live model list.
        Run `amplifier-agent auth set anthropic <key>` ...
ERROR ... Cannot start.
```
Crash loop.

**Test F — second provider added at runtime, no restart:**

```
models BEFORE: ['claude-haiku-4-5-20251001', 'claude-opus-5', 'claude-sonnet-5']
openai key written to file, confirmed present by `auth list`
models AFTER:  ['claude-haiku-4-5-20251001', 'claude-opus-5', 'claude-sonnet-5']
```
Unchanged. **A newly-credentialed provider is invisible until restart.**

**Boot from file, with the env var removed** (the migration state) — works:

```
INFO:amplifier_agent_http:Loaded 3 models from provider 'anthropic'
INFO:amplifier_agent_http:Prepared bundle loaded with providers; 6 agents hydrated. Ready to serve.
```
Warm restart, `systemctl restart` → `Ready to serve`, measured **~3 s**.

---

## 2. Is the credential read at boot, or per request?

**Both, at different layers.** This is the single most important thing to
understand before implementing, and it is why "does this need a restart?" has
three different answers.

| Layer | When resolved | Consequence |
|---|---|---|
| **Which providers exist / which models are served** (`served_models_registry`, `available_models`) | **Boot only** — `app.py:274-330` | Enabling a *new* provider needs a restart |
| **The API key handed to the provider for a turn** | **Per request** — `_session_runner.py:281-288` re-runs `inject_provider` → `build_provider_entry` → `resolve_provider_credentials` → `_load_credentials()` → `read_text()` | Rotating a key for an *already-served* provider needs no restart |
| **Whether the process may run at all** | **Boot only** — two `sys.exit(2)` gates | No credential = no process |

### The restart matrix

| Change | Restart? | Why |
|---|---|---|
| Rotate the key for a provider already being served | **No** | Test A/B/C |
| Set the first-ever key (sidecar is crash-looping) | **Yes** — a `start` | Test E; there is no live process to update |
| Add a key for a provider that isn't in the registry | **Yes** | Test F |
| Switch which provider is active | **Yes** | Registry is boot-built |
| Remove a key | **Yes**, eventually | Turns fail per-request immediately; the process stays up until restarted, then refuses to boot |

**Design consequence:** the write endpoint must *decide* whether a restart is
needed rather than always restarting. Always-restart throws away the best
property route 3 has (seamless rotation) and pays the full cost (§6) every time.

---

## 3. Chosen mechanism

### 3.1 Shape

```
Browser (Settings → Agent)
  │  POST /api/agent/provider-credential  { provider, api_key }
  ▼
muxplex (root, same box)
  │  1. authorize + reject non-key providers
  │  2. VALIDATE in a scratch home, as aa-svc          ← never touches live creds
  │  3. persist:  sudo -u aa-svc -H amplifier-agent auth set <p> --stdin
  │  4. restart ONLY if the provider is not already served
  │  5. read back status; report loudly on failure
  ▼
/home/aa-svc/.amplifier-agent/credentials.json   (0600, aa-svc:aa-svc)
  ▲
  │  read on every turn
amplifier-agent sidecar (aa-svc, 127.0.0.1:9099, fenced)
```

muxplex **stores nothing**. The key exists in muxplex's address space for the
duration of one request and is never written to a muxplex-owned file, never
logged, never returned in any response.

### 3.2 The write

```
sudo -u aa-svc -H <AGENT_BIN> auth set <provider> --stdin
```
with the key written to the child's stdin and the pipe closed.

**Why the vendor CLI rather than root writing the JSON:**

- The file format is not ours. `_load_credentials` carries a version envelope
  and a legacy-shape upgrade path (`auth.py:92-124`); reimplementing it in
  muxplex creates a drift surface that breaks silently on an `amplifier-agent`
  upgrade — and "breaks silently" here means the sidecar refuses to boot.
- **Ownership footgun.** A root-written file is `root:root`. At `0600`, `aa-svc`
  cannot read it, so the sidecar reports "credentials missing" while the file
  visibly exists. Every direct-write implementation has to remember a `chown`
  that the CLI does correctly for free.
- Atomic write + `0600` + `0700` parent + `os.replace` are already implemented
  and tested upstream (`auth.py:127-155`).
- Free validation we would otherwise reimplement: unknown-provider rejection
  (`auth.py:311-313`) and the `github-copilot` refusal (`auth.py:316-327`).

**Why `--stdin` and not an argv positional:** argv is world-readable via
`/proc/<pid>/cmdline` for the lifetime of the process. Upstream documents this
as the reason the flag exists (`auth.py:276-286`). A key passed as an argument
is readable by every local user, including `aa-svc`.

**`AGENT_BIN` is configuration, never request data.** Read it from an env var
(`MUXPLEX_AGENT_AUTH_CMD`) defaulting to `/home/aa-svc/.local/bin/amplifier-agent`.
No part of the request may influence the executable path, the target user, or
any flag. Build the argv as a fixed list — no shell.

### 3.3 Validate before persisting

Because a bad key that reaches disk *and* triggers a restart yields a permanent
crash loop (Test D), validation is not a nicety — it is what makes the feature
safe to expose.

Validate using the **same code path the sidecar's own boot uses**, as the **same
user**, through the **same egress path**, against a throwaway home:

```
TMP=$(mktemp -d)                                   # owned aa-svc, 0700
sudo -u aa-svc -H env AMPLIFIER_AGENT_HOME=$TMP <AGENT_BIN> auth set <p> --stdin
sudo -u aa-svc -H env AMPLIFIER_AGENT_HOME=$TMP <AGENT_BIN> models list --provider <p>
```

`credentials_path()` honours `AMPLIFIER_AGENT_HOME` (`auth.py:82-89`), so the
scratch home is a real isolation boundary: **the live credential is never at
risk during validation.** `models list` exits `2` on an invalid key — that is
the documented contract (`admin/models.py:11-21`) and is exactly the check the
lifespan performs. Delete `$TMP` in a `finally`.

Rejected: muxplex calling the provider API itself. It would make muxplex an
outbound HTTP client to a provider endpoint, duplicate the resolver, and — worst
— validate through a *different* network path than the one the sidecar uses, so
a fence or DNS problem would pass validation and then fail at boot.

### 3.4 Restart only when required

Ask the sidecar what it is already serving, using the bearer muxplex already
holds (`main.py:1209-1210`):

```
GET {_AGENT_PROXY_URL}/v1/models     →  200 with a model whose _provider == <p>
```

- **Served already** → write, do **not** restart. The next turn picks it up.
- **Not served, or the sidecar is unreachable** → write, then
  `systemctl restart amplifier-agent-http`, then poll `/v1/models` until it
  answers or a deadline (~30 s) passes.

**Use `systemctl`, not `amplifier-agent serve restart`.** The CLI exposes
`serve stop|restart` (`amplifier_agent_cli/__main__.py:23`), but under systemd
that races the unit's own `Restart=on-failure`: the CLI kills the process,
systemd sees the exit and respawns it, and the CLI respawns another. systemd
owns this process; only systemd should cycle it.

### 3.5 Removing the environment shadow

Resolution is env-first. `ANTHROPIC_API_KEY` in
`/etc/amplifier-agent-http-aasvc.env` **permanently shadows** anything written
to `credentials.json`. If it is still set, this whole feature silently does
nothing while reporting success — the worst failure available to us.

Two parts, and both are required:

1. **Deployment change (one-time, out of band):** delete the provider key line
   from `/etc/amplifier-agent-http-aasvc.env`. This is a documented step, not
   something muxplex does — muxplex must never edit the sidecar's unit
   environment (that is route 2, which the owner rejected).
2. **Detection, so step 1 is never merely assumed.** The status endpoint runs
   `auth status` as `aa-svc` and reads the precedence line it already prints:

   ```
   Per-provider resolution (env wins if both are set):
     anthropic       USING env=ANTHROPIC_API_KEY
   ```

   When the active provider resolves from `env`, the UI says so plainly and
   marks the form as having no effect. **We detect the shadow rather than
   assuming it is gone.**

`auth status` has no `--json` flag, so this is line parsing — a real coupling
point to `amplifier-agent`'s output format. Pin it with a test that asserts the
parser against captured output, and file an upstream ask for `--json` on
`auth status` / `auth list` as the durable fix.

### 3.6 Provider allowlist

Accept **`anthropic`, `openai`** (key-only providers). Refuse everything else,
with a specific reason:

- **`azure-openai` — refuse.** Its credentials entry carries an `endpoint` URL
  that the resolver feeds straight to the provider (`provider_sources.py:293-313`).
  Accepting it over HTTP would hand a browser-reachable caller the ability to
  redirect every LLM request — and therefore every conversation, including the
  terminal scrollback the agent reads — to a server of their choosing. See §7.3.
  If Azure support is wanted later it needs its own design pass with an
  endpoint allowlist; it is not a matter of adding a string to a list.
- **`github-copilot` — refuse.** Environment-only; `auth set` refuses it
  upstream (`auth.py:74`), and a stored value would never reach the provider.
  Refuse in muxplex too, with the same explanation, so the user gets a real
  message instead of a subprocess error.
- **`ollama` — refuse.** Its "credential" is a host URL, not a key. Same
  redirect concern as Azure, minus the key. Out of scope.

The allowlist is a **positive** list in muxplex. A new provider appearing
upstream must not become settable by default.

---

## 4. Where this lives in muxplex's settings model

**Nowhere. It is not a muxplex setting.** `settings.json` gains zero keys.

This is the structural answer to "it must never be federation-synced":

1. `apply_synced_settings` applies **only** keys present in `SYNCABLE_KEYS`
   (`settings.py:1080-1087`, allowlist). A key that does not exist in
   `DEFAULT_SETTINGS` and is not in `SYNCABLE_KEYS` **cannot** be synced. Not by
   policy — there is no code path that would carry it.
2. `LOCAL_ONLY_KEYS` is the wrong home and would be actively harmful. Its
   meaning is precisely *"never settable over the API"* (`settings.py:298-310`),
   which is the property we are deliberately not preserving here. Adding a key
   there and then carving an API exception for it would break the one invariant
   that partition exists to state — and that partition is load-bearing because
   the federation Bearer key *is* the agent credential.
3. The system of record is the sidecar's `credentials.json`. There is no
   muxplex-side copy to sync, to leak, or to keep consistent.

**The invariant to state in code and hold in review:**

> No provider API key is ever persisted by muxplex, in `settings.json` or
> anywhere else; is ever written to a log; or is ever included in any HTTP
> response body. muxplex is a conduit, not a store.

That is the honest description of what muxplex gains: a **transient
credential-handling role** (the key is in its memory for one request), not a
credential-holding one. That is the line, and §7 argues it is acceptable.

---

## 5. The UI

Extend the existing Settings → Agent tab (`frontend/index.html:587`), which
today holds per-device prefs and the storage disclosure.

**`GET /api/agent/provider-credential`** returns status only — never a key:

```json
{
  "state": "configured",
  "provider": "anthropic",
  "masked": "sk-ant...EwAA",
  "source": "file",
  "sidecar": "running",
  "models": ["claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-5"]
}
```

`masked` uses the sidecar's own convention (first 6 + last 4, `auth.py:213-224`)
— reuse it rather than inventing a second masking rule. A key too short to mask
meaningfully renders `***`.

**The five states the UI must render distinctly.** Today all of them look
identical: a turn that ends with nothing (`AGENT_CHAT_SETUP.md` §7).

| State | What it means | What the UI says |
|---|---|---|
| `not_configured` | No credential resolvable; the sidecar is almost certainly crash-looping | "The Agent has no model provider key. It cannot run until one is set." Show the form. |
| `configured` / `source: file` | Working, managed here | Provider + masked key + "Replace key". |
| `configured` / `source: env` | **Shadowed.** A key in the service env file wins | "A key set in the service's environment file is taking precedence. Changes made here will have no effect until it is removed." Form disabled or clearly marked inert. |
| `sidecar_down` | Credential looks fine, the process is not answering | Distinguish from `not_configured`. Name `journalctl -u amplifier-agent-http`. |
| `validating` / `restarting` | Transient | Progress, with the restart warning from §6 shown *before* the user commits. |

**Form rules:**

- `type="password"`, `autocomplete="off"`, never pre-populated. The GET has no
  field that could populate it.
- Submitting an empty field is not "clear the key" — that is a separate,
  explicitly-confirmed action, because clearing it makes the sidecar unbootable
  on its next restart.
- When a restart will be required (§3.4), say so before submission: *"This will
  restart the Agent service. Any conversation in progress will be interrupted."*

---

## 6. What a restart costs

- **In-flight turns die.** The sidecar holds the LLM loop; a restart drops the
  SSE stream mid-turn. muxplex's proxy emits an in-stream error frame
  (`main.py:5395-5410`), which per `AGENT_CHAT_SETUP.md` §7 the panel likely
  drops entirely (`chat.js`'s `if (!choice) continue;`) — so the visible result
  is a turn that stops. Warn before, not after.
- **~3 s of downtime** on a warm cache (measured). Cold cache is longer — the
  lifespan re-prepares the bundle and may re-resolve provider modules
  (`app.py:174-188`).
- **The fence re-verifies on every start.** `ExecStartPre=+/usr/local/sbin/muxplex-agent-fence verify`
  probes from `aa-svc`'s UID against every muxplex port before the sidecar is
  allowed up. Confirmed running in the twin's journal on each restart. **A
  UI-triggered restart can therefore fail for a reason that has nothing to do
  with the key.** The UI must not report a fence failure as a bad key — read
  the unit's status and say which it was.
- **`BindsTo=muxplex-agent-fence.service`** is untouched by a sidecar restart —
  we cycle the sidecar unit, never the fence unit. If the fence unit is down or
  failed, the sidecar will not start, and that is correct behaviour we must
  not work around. **Nothing in this design may `systemctl start`, `stop`, or
  `reload` the fence, its watchdog, or its timer.**
- **If the restart doesn't happen** (systemctl unavailable, permission denied,
  different namespace), the file is already written and the sidecar is serving
  the *old* key — or is dead. This must be a loud, specific error, not a
  success with a footnote. The endpoint returns non-2xx and names which step
  failed.

---

## 7. Threat model delta

What an attacker gains that they did not have before. Being blunt: this adds a
**browser-reachable write primitive into the isolated user's home directory**.
It is narrow, but it is new, and it is worth stating precisely what changes.

### 7.1 What does *not* change

- The sidecar still holds **no muxplex credential**. Nothing here gives it one.
- The **network fence is untouched**. No iptables rule, no unit, no
  `AA_USER`, no port list changes. The sidecar still cannot initiate a
  connection to muxplex.
- The **direction of trust is unchanged**: muxplex → sidecar, never the
  reverse. This design adds a second muxplex → sidecar action (write a file)
  alongside the existing one (proxy a chat request).
- **`LOCAL_ONLY_KEYS` / `SYNCABLE_KEYS` are untouched** (§4).
- The agent **cannot call this endpoint**. Tools are declared to the model but
  dispatched by a hardcoded `if`-chain in the browser (`chat.js:1729`); there is
  no generic HTTP tool. **This is the structural mitigation and it must be
  preserved: do not add a credential tool, and do not make `executeToolCall`
  generic.** If either happens, prompt injection becomes credential rewrite.

### 7.2 What is genuinely new

| # | Delta | Assessment |
|---|---|---|
| 1 | A browser-reachable path writes a file into `/home/aa-svc/` | The *capability* is not new — muxplex is root and could always do this. The *reachability* is new: it moves from "requires shell on the box" to "requires an authenticated muxplex session". Accepted; it is the feature. |
| 2 | A browser-reachable path can **restart a system service** | New. Bounded to one unit, no arguments from the request. Worst case is DoS by repeated restart — each one kills in-flight conversations. Mitigate with a short server-side cooldown; do not build a general service-control endpoint. |
| 3 | An attacker with a muxplex session can **replace the provider key** | They cannot read it back (§5). They can break the Agent (DoS) or substitute a key they control. Substituting a key they control is the interesting one — see 7.3. |
| 4 | **The loopback bypass makes this unauthenticated for local processes** | `auth.py:330-333` waves through **every** request from `127.0.0.1`/`::1` before any auth check. Any local process that can reach muxplex's port can write the credential and restart the sidecar. This is pre-existing and applies to every `/api/` route, but it is worth naming here because this route's blast radius is a secret and a service restart. The fence keeps `aa-svc` itself out; other local users are not covered. **This should be raised with the owner as a known property, not silently inherited.** |
| 5 | muxplex handles key material in memory | Transient, one request. Never persisted, logged, or echoed (§4). |

### 7.3 The one that would be a real hole — and why it is closed

`credentials.json` entries can carry an **`endpoint`** field, which the resolver
attaches to the provider's config (`provider_sources.py:293-313`), and
`auth set --endpoint` writes it verbatim (`auth.py:349-350`).

If the endpoint accepted `azure-openai` with a caller-supplied `endpoint`, then
anyone who can reach this route — **including any local process, per 7.2 #4** —
could point the sidecar's model traffic at a server they control. Every
subsequent conversation, including whatever terminal scrollback the agent read
via `get_muxplex_session_details`, would be delivered to them. The network fence
does not help: it blocks *local* egress, and deliberately permits outbound to
the internet so the sidecar can reach its model API.

That is an exfiltration channel for the most sensitive data in the system.

**Closed by construction:** the provider allowlist (§3.6) accepts only key-only
providers, and the request body has **no `endpoint` field at all** — not an
ignored one, not a validated one. Absent. Adding Azure support later means
designing an endpoint allowlist first.

### 7.4 Endpoint hardening (all required)

- **Rate-limit** writes and restarts server-side; cooldown between restarts.
- **Length and charset bounds** on the key before it reaches a subprocess.
- **No shell.** Fixed argv list; the provider name is validated against the
  allowlist before use, never interpolated.
- **Never log the body.** Add the route to whatever body-logging exclusions
  exist, and assert it in a test.
- **Audit line on every write:** timestamp, provider, authenticated principal,
  restart-or-not. No key material, not even masked.
- **Scratch home cleanup** in a `finally`, with `0700` and a unique directory.

---

## 8. Alternatives inside route 3 that were rejected

Route 2 (muxplex injects into the sidecar's environment) and route 1 (browser
holds the key) were rejected by the owner and are not re-litigated. These are
the choices *within* route 3.

| Rejected | Why |
|---|---|
| **muxplex writes `credentials.json` directly as root** | Reimplements a format we don't own (`auth.py:92-124`); breaks silently on upstream change, and "silently" here means an unbootable sidecar. Plus the `root:root` ownership footgun that makes a present file unreadable to the sidecar. |
| **Key as an argv positional to `auth set`** | World-readable in `/proc/<pid>/cmdline`. `--stdin` exists precisely for this (`auth.py:276-286`). |
| **Always restart after writing** | Throws away the best property route 3 has — proven seamless rotation (Test A/B/C) — and pays the full §6 cost on every key change, including ones that need nothing. |
| **Never restart** | Cannot work. First-ever key and new-provider cases require it (Tests E, F), and in the first-key case there is no live process to update. |
| **`amplifier-agent serve restart`** | Races systemd's `Restart=on-failure`. systemd owns the process. |
| **Write first, validate by restarting and seeing if it comes up** | This *is* the trapdoor (Test D). A bad key leaves a permanent crash loop, and the previously-working key is already overwritten. Validation must precede persistence. |
| **Validate against the live credentials file** (write, test, roll back on failure) | A rollback window in which the live credential is wrong, and a rollback that can itself fail. `AMPLIFIER_AGENT_HOME` gives us a real scratch home for free (`auth.py:82-89`). |
| **Store the key in `settings.json` under `LOCAL_ONLY_KEYS`** | `LOCAL_ONLY` means "not settable via API" — the exact opposite of the requirement. Would require carving an exception into the one partition whose value is that it has none. |
| **Store a muxplex-side copy for display** | A second copy of a secret, with nothing to gain: the sidecar can already report masked status via `auth list`/`auth status`. |
| **Accept `azure-openai` / `ollama` for symmetry** | Both carry a caller-controlled URL. §7.3. |

---

## 9. Failure modes

| Failure | Detection | Behaviour |
|---|---|---|
| **Bad key submitted** | `models list` exits non-zero in the scratch home | Reject with the provider's own error (401 / "API key is invalid"). **Nothing is written. Nothing is restarted.** Live state untouched. |
| **Key valid but provider unreachable** (DNS, fence, outage) | Same non-zero exit, different stderr | Do not write. Report as a connectivity problem, distinct from a bad key — the user must not go hunting for a typo that isn't there. |
| **`credentials.json` unwritable** (disk full, perms, immutable) | `auth set` exits non-zero | Surface the CLI's stderr verbatim. Do not retry silently. |
| **Written but sidecar didn't come back** | Post-restart `/v1/models` poll times out (~30 s) | Non-2xx naming the step. Read `systemctl is-active`/`is-failed` and the last journal lines to distinguish: crash loop (key), fence refusal (`ExecStartPre`), or unit failure. Report which. |
| **Fence unit down at restart time** | `ExecStartPre` verify fails; sidecar refuses to start | Report as a fence problem, name `systemctl start muxplex-agent-fence`. **Never work around it.** |
| **Env var still shadowing** | `auth status` reports `USING env=...` | Refuse to pretend. Surface the shadow state in the UI (§3.5). Do not write and claim success. |
| **Key valid at write time, revoked later** | None at write time | Turns fail per-request with the in-band error string; the process stays up. See below. |
| **Concurrent writes** | — | Serialise with a lock around validate→write→restart. `auth set` is atomic per write, but two racing restarts are not. |
| **Malformed `credentials.json` on disk** | `_load_credentials` raises; `resolve_credential_from_file` swallows it and returns `""` (`auth.py:173-181`) | Presents as "not configured". The status endpoint should call `auth list` and surface its `ClickException` message, which names the file and the remediation. |

### A failure mode that is not loud, and that we should fix

At runtime, an invalid key does **not** produce an error response. It produces a
**`200` chat completion with `finish_reason: "stop"`**, whose message *content*
is the error text (Test B):

```json
{"choices":[{"message":{"role":"assistant","content":"\n\n[amplifier-agent error:
  RuntimeError: Execution failed: AuthenticationError: ... \"API key is invalid.\" ...]\n"}}]}
```

The user sees the text, so it is not invisible — but it is not machine-detectable
by the panel either, so the UI cannot react (e.g. by surfacing "your key was
rejected" next to the form). Recommendation, small and worth doing: have the
panel pattern-match the `[amplifier-agent error: ...AuthenticationError...]`
prefix and link to Settings → Agent. Note this as a coupling to an upstream
string format; the durable fix is an upstream ask for a structured error.

---

## 10. Implementation sequence

Each step is independently verifiable. Do not start a step before the previous
one is proven.

1. **Prove the migration on the deployment box.** Move the existing key from the
   env file into `credentials.json` via `auth set`, remove the env line, restart,
   confirm `Loaded N models` / `Ready to serve`. This is the whole feature's
   premise and it needs no code. *(Already proven in the twin — repeat on the
   real box before writing any code.)*

2. **`GET /api/agent/provider-credential`** (read-only). Shells `auth status` +
   `auth list` as `aa-svc`, plus `GET /v1/models`. Returns the §5 status shape.
   Ship this alone: it is the "which provider and model are active" display
   `AGENT_CHAT_SETUP.md` §8 already calls out as valuable with no tradeoff, and
   it makes every later step debuggable. **No secret crosses this boundary.**

3. **UI: status only.** Render the five states in Settings → Agent. At this
   point a missing key stops being an opaque failure — the release blocker's
   diagnostic half is already solved.

4. **Validation helper** (server-side, no route yet): scratch-home
   `auth set` + `models list`, returning
   `ok | bad_key | unreachable | error(stderr)`. Unit-test each branch with a
   fake binary.

5. **`POST /api/agent/provider-credential`.** Allowlist → validate → write →
   decide restart → restart → poll → report. Lock around the whole sequence.
   With §7.4 hardening from the first commit, not bolted on later.

6. **UI: the form.** Password field, restart warning before submit, distinct
   error rendering per §9.

7. **Panel auth-error detection** (§9, optional but cheap).

8. **Docs.** Rewrite `AGENT_CHAT_SETUP.md` §3.4 (key now goes here, not the env
   file), §7's symptom table, §8's "On in-app key entry" (which predicted
   exactly this design — update it from *"if it is ever built"* to *"this is how
   it is built"*), and §9's "No in-app configuration" / "No first-class not-
   configured state" entries.

### Tests worth pinning

- `auth status` / `auth list` parser against captured upstream output *(coupling
  to another repo's stdout — the most likely thing to break silently)*.
- The provider allowlist rejects `azure-openai`, `ollama`, `github-copilot`, and
  anything unknown.
- The request model has **no** `endpoint` field (assert on the schema, so adding
  one is a deliberate, visible act).
- No response body on any route ever contains the key; `masked` never exceeds
  10 chars of key material.
- The credential key name appears in neither `DEFAULT_SETTINGS`, nor
  `SYNCABLE_KEYS`, nor `LOCAL_ONLY_KEYS` — i.e. muxplex has no such setting.
- Restart path invokes `systemctl` on `amplifier-agent-http` only, and never
  touches the fence, watchdog, or timer units.

---

## 11. Assumptions to re-check before implementing

Stated explicitly because the design leans on each of them.

1. **muxplex and the sidecar share a systemd namespace, and muxplex can `sudo`.**
   Verified in the twin (muxplex runs as root, PID 87821; both units visible to
   one `systemctl`; `sudo` and `systemctl` on `PATH`). **Verify on the real
   deployment box** — muxplex is described as running "in its container", and
   if that container does not share the host's init and filesystem view, the
   entire write path needs a different transport (a small privileged helper
   with a unix socket) and this design needs a revision, not a patch.
2. **`amplifier-agent` stays at v0.12.0, or its `auth`/`serve` surface stays
   compatible.** The design depends on `auth set --stdin`, `auth status`
   precedence output, `AMPLIFIER_AGENT_HOME`, and `models list --provider`
   exit codes. The sidecar is not versioned with muxplex
   (`AGENT_CHAT_SETUP.md` §9) — pin the version in deployment docs.
3. **The panel's model id remains served.** `chat.js:37` hardcodes
   `claude-sonnet-5`. Changing provider family via this UI would produce
   `400 unknown_model` on every turn. **This UI must not offer a provider
   whose models the panel cannot request** — which is a second, independent
   reason the allowlist starts at `anthropic` only in practice, and why adding
   `openai` should land together with panel model selection.

## 12. What would make this design wrong

- If the deployment box turns out **not** to share a namespace (assumption 1),
  the transport changes. Everything else — validate-before-write, restart-only-
  when-needed, no-endpoint-field, nothing-in-settings — survives.
- If upstream ever **caches** `credentials.json` in the serve process, the
  no-restart rotation path disappears and every write becomes a restart. Watch
  for a cache in `_load_credentials`; the Test A/B/C sequence is the regression
  test for it.
- If the loopback auth bypass (§7.2 #4) is judged unacceptable for a route with
  this blast radius, this route needs its own authentication rather than
  inheriting the middleware's. That is an owner decision, and it should be made
  before this ships publicly.

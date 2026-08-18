## v0.56.0 (2026-08-17)

**Ask the chat panel about a session on another machine and it just answers.**
Previously the embedded agent could only see this device's own sessions: asked
about a session running on a peer, it listed the local sessions, said it didn't
see it, and only looked across the federation when you explicitly told it to
"check federation". Even once it had learned which device the session was on, it
still couldn't read it -- `get_muxplex_session_details` was local-only and
returned a bare 404. The agent now lists every session across the whole
federation by default, tells you which machine each one is on, and can open a
remote session's details and scrollback on the first try without being prompted.

### Added

- **`GET /api/federation/{device_id}/sessions/{session_name}`** -- a proxy route
  that fetches a single session's details and scrollback from the device that
  owns it. It mirrors the rest of the `/api/federation/{device_id}/sessions`
  family for device lookup, Bearer auth, and 404-on-unknown-device, but follows
  `GET /api/federation/sessions`' own reachability convention rather than the
  write proxies' 502/503-on-any-failure: it reuses the shared
  `_federation_breaker` (keyed by remote URL, so a peer already known dead to the
  sessions/devices fan-out costs this route nothing extra) and returns the same
  legible `{"status": "unreachable"}` / `{"status": "auth_failed"}` shapes as a
  **200** body instead of throwing. A session genuinely absent on a *reachable*
  peer stays an honest 404, mirroring `GET /api/sessions/{name}`.
- **`get_muxplex_session_details` resolves local-vs-remote transparently.** It
  tries the local endpoint first (unchanged fast path, zero extra cost for the
  common case); on a local 404 it falls back to a fresh federation lookup and
  proxies to the owning device. A new optional `device_id` parameter lets a
  caller that already knows the device skip the local attempt entirely. If the
  same session name exists on more than one federated device, it refuses to
  guess and reports every candidate device so the caller can retry with an
  explicit `device_id`.

### Changed

- **The two session-listing tools are merged into one.**
  `list_muxplex_sessions` now always lists across the entire federation and tags
  every entry with `deviceId`/`deviceName`/`remoteId` (`null` for a session on
  *this* device); `list_muxplex_federated_sessions` is gone. This was chosen over
  biasing two similarly-named tools' descriptions and hoping the model reaches
  for the right one -- there is no longer a wrong tool to reach for. There is no
  cost regression for an unfederated install: the backend takes a zero-fan-out
  early return when no peers are configured, so this costs exactly what the old
  local-only call did, and the bounded/cached/circuit-broken fan-out cost applies
  only when federation is actually configured.
- **Tool descriptions and the system prompt no longer claim details are
  "local-only".** That text was accurate before this release and actively
  misleading after it. `switch_muxplex_session` *is* still local-only (wiring it
  to the existing federation connect-proxy is out of scope here), and now says so
  explicitly, so the model tells you to switch from that device's own dashboard
  rather than silently failing or claiming the session doesn't exist.

### Fixed

- **Field-parity gap in `GET /api/federation/sessions`.** The endpoint's *local*
  branch was missing `created_at`, which was already present in `GET
  /api/sessions` and in every remote entry (forwarded verbatim). Harmless while
  the endpoint had separate consumers; it matters now that a single tool is this
  endpoint's only consumer for both local and remote listing.

### Notes

- `focused: true` is still applied only to the local entry matching this
  browser's own open/zoomed session, and is now explicitly guarded so a
  same-named *remote* session is never mis-marked. `getFocusedSessionName()`
  staying local-scoped is now a deliberate decision rather than a tooling
  limitation -- "focused" describes a live local browser affordance that a
  proxied remote terminal view doesn't carry the same guarantees for.
- 18 new tests: 9 backend (`muxplex/tests/test_api.py`) covering success +
  tagging, lines validation, unknown device, remote 404, `auth_failed`,
  transport-error unreachable + breaker recording, breaker-already-open with no
  network call, and 502 on an unexpected remote HTTP error; 9 frontend
  (`frontend/tests/test_chat_panel.mjs`) covering device tagging, unreachable-peer
  passthrough, focus non-leakage to remote entries, the local fast path making
  client-observably zero federation calls, explicit `device_id` routing,
  local-miss federation fallback with exact call order, not-found-anywhere,
  multi-device disambiguation refusal, and remote unreachable/`auth_failed`
  passthrough as data.

## v0.55.2 (2026-08-17)

**v0.55.1's provider install worked. Its verification of that install did not, and
every host was told the opposite.** The first `muxplex ensure-agent` (or fleet
update) on a device installed the provider modules correctly -- and then reported
`providers not importable`, burned all 3 retries, and exited `rc=1`. On `tower`
the supervisor escalated that false failure into a log line warning that **the
embedded agent panel will be UNAVAILABLE**, which was untrue at the moment it was
printed. Running `muxplex ensure-agent` a second time passed instantly via the
fast path, because the providers had been installed correctly all along. If you
saw this during the v0.55.1 rollout: **your providers were genuinely installed and
your panel was fine** -- what failed was the check, and the report.

### Fixed

- **The post-install provider check now runs in a fresh subprocess, not in the
  interpreter that just did the install.** `ensure_agent()` verified the
  just-installed provider modules by importing them *in its own already-running
  process* -- an interpreter that started **before** those modules existed in its
  venv's site-packages. `importlib.invalidate_caches()` does not rescue this: it
  invalidates path-finder *directory* caches, but never re-runs the interpreter's
  `site` startup, so an editable install's `.pth`-based import hook written to
  site-packages after this interpreter already processed every `.pth` file at
  startup stays invisible no matter how many times caches are invalidated. That
  made the check a **reproducible false negative** -- it failed on the first
  `ensure_agent()` invocation on 6 of 6 fleet hosts, while a brand-new interpreter
  against the identical, already-installed venv imported the exact same modules
  successfully moments later. `_agent_providers_importable_subprocess()` asks the
  same question via `sys.executable` in a genuinely fresh subprocess, which
  reprocesses every `.pth` file in site-packages from scratch and so reliably
  observes a just-completed install.
- **The in-process fast path is deliberately unchanged.** The *pre*-install check
  (`_agent_providers_importable()`) still runs in-process, and correctly so: it
  runs before any install happens in this call, so nothing under it has changed
  what it can see, and it stays cheap -- no subprocess, no network -- for the
  common already-ready case. Only the post-install verification call site inside
  `ensure_agent()`'s retry loop, the one racing an install that just completed in
  this same process, switched to the subprocess check.
- **Fail-loud is preserved.** A genuinely missing or broken provider -- an install
  that actually failed -- still fails loud with the real detail after exhausting
  retries. A subprocess that cannot launch, times out, or returns unparseable
  output is reported as a failure too, never silently treated as success. This
  release only stops reporting failure when the install actually succeeded.

### Notes

- **This is purely corrective -- there is no functional change to what gets
  installed.** v0.55.1 installed the providers correctly; upgrading to v0.55.2
  changes only whether muxplex tells you the truth about it. The v0.55.1 note
  advising a re-run after an apparent bundle-preparation failure described this
  false negative: with this release, the first invocation reports accurately and
  the re-run is no longer needed to get a correct answer.
- 3 new tests in `muxplex/tests/test_ensure_agent.py`, including a real (unmocked)
  regression test that reproduces the exact false-negative shape -- a module
  present on disk but never on the running process's `sys.path` -- and proves the
  in-process and fresh-subprocess checks give the different answers this fix
  depends on.

## v0.55.1 (2026-08-17)

**The embedded agent panel was dead on every device, and the health check said it was fine.**
Any chat turn in the embedded panel failed with
`provider module not installed for 'anthropic': No module named 'anthropic'`.
The panel looked fully configured -- a credential was on disk, the gate was
open, the agent library imported -- and then every real turn failed the moment a
provider was mounted. This affected the entire fleet, and had been shipping
broken since the v0.53 rollout.

### Fixed

- **`ensure_agent()` now installs the provider modules, not just the agent
  library.** It installed `amplifier-agent` with
  `uv tool install ... --with 'amplifier-agent @ git+...@v0.12.0'`, which
  resolves that package's own `pyproject.toml` dependencies (`amplifier-core`,
  `amplifier-foundation`) and stops there. The providers, orchestrator, context
  module, and tools are **not** ordinary dependencies -- they are git-only
  packages declared in amplifier-agent's *bundle*, installed by a separate
  bundle-prepare step that `--with` never runs. So `amplifier_agent_lib`
  imported fine while the `anthropic` SDK itself was never installed anywhere on
  the machine. `ensure_agent()` now runs that bundle-prepare step
  (`amplifier_agent_lib.bundle.loader.load_and_prepare_bundle(install_deps=True)`)
  through the target venv's own interpreter, installing exactly what the pinned
  amplifier-agent version's own `bundle.md` declares -- so muxplex never carries
  a second provider pin that could drift from the agent pin.
- **The health check that let this ship is closed.** The old check was
  `import amplifier_agent_lib` and nothing else -- it passed on every broken
  device, which is precisely why the bug survived the whole v0.53/v0.54 rollout
  without a single failure signal. `ensure_agent()` now also verifies that every
  panel-selectable provider module actually imports before reporting success,
  and when only the providers are missing it skips straight to bundle
  preparation instead of reinstalling the agent -- the exact state a device left
  by the old code is in. A test exercising a genuinely missing provider now
  covers the check that would have caught this.

### Notes

- **Bundle preparation is intermittently flaky on a heavily-loaded host, and
  recovers by re-running.** The step installs roughly 20 modules concurrently;
  under heavy load it can fail on first invocation. It is safe: it **fails
  loud** -- it never reports success it did not achieve, which is the specific
  weakness this release exists to fix -- and it is self-healing. Re-run
  `muxplex ensure-agent`; the second pass takes the fast path over whatever
  already landed and completes. If you see this fail once during a fleet
  update, re-run it rather than treating the device as broken.

## v0.55.0 (2026-08-16)

**The Soft Deck now sees the whole federation, not just its own server.**
The browser-tab deck at `/deck/` polled `GET /api/sessions` -- local sessions
only. A session on a federated peer, plainly visible in the PWA, simply did not
exist as far as the deck was concerned. It now polls
`GET /api/federation/sessions`, the same endpoint the PWA uses.

### Added

- **Federation-aware Soft Deck** (`frontend/deck/`). Sessions from every
  reachable peer are listed alongside local ones, each remote tile marked with
  its origin device. Clicking a remote session connects through the federation
  proxy (`POST /api/federation/{remoteId}/connect/{name}`); local sessions still
  use `POST /api/sessions/{name}/connect`, unchanged.
- **Collision-safe identity.** Sessions key by `sessionKey`
  (`{device_id}:{name}`) rather than bare name, so two peers each running a
  session called `main` are never conflated.
- **Unreachable peers stay visible.** A peer reporting `unreachable` or
  `auth_failed` renders as a degraded tile instead of vanishing from the list --
  the list never silently shrinks and leaves you guessing where a session went.
- 33 new tests in `frontend/tests/test_deck.mjs`, written against the real
  `/api/federation/sessions` response shape.

### Documented (shipped in v0.54.0, recorded here)

`muxplex_client` gained federation support in commit 88d4d39, which landed
before the v0.54.0 release and is therefore already published in
`muxplex-client` 0.54.0 -- it went out undocumented, and is recorded here for
the record rather than being claimed as new:

- `federation_sessions()` (sync and async), returning `FederationSessions`
  (`sessions` + `statuses`).
- `Session` gained `device_id`, `device_name`, `device_version`, `remote_id`,
  and `session_key` (camelCase on the wire: `deviceId`/`deviceName`/
  `deviceVersion`/`remoteId`/`sessionKey`). All default to `None`, so a
  pre-federation server parses exactly as before.
- `RemoteStatus`, carrying a peer's `unreachable`/`auth_failed`/`empty` status.
- `connect(remote_id=...)`, routing through the federation connect-proxy.
- `RemoteNotFoundError`, `RemoteUnreachableError`, and `RemoteError`.

### Notes

- The physical `muxplex-deck` project has a matching federation update **coming
  in its own separate release**; it is not in muxplex-deck v0.16.0, which was
  device identity and target pairing.
- `muxplex-deck` does **not** need this release to unpin its client dependency:
  `muxplex-client` 0.54.0 on PyPI already contains the federation API, so its
  `[tool.uv.sources]` git pin can be replaced with `muxplex-client>=0.54.0` now.

## v0.54.0 (2026-08-16)

**The embedded chat panel now notices when you've configured it, and knows which session you're looking at.**
Two owner-reported gaps in the agent chat panel, both of the same family: the
panel knew less about the browser it lives in than it easily could. The first was
a stale gate -- you added a provider key and the panel went on insisting the
agent wasn't set up. The second was a blind spot -- ask the agent about "the one
currently in focus" and it answered, correctly but uselessly, that it had no way
to see which session you were looking at.

### Fixed

- **The "The Agent isn't set up on this server yet" gate no longer goes stale
  after you save a provider key.** `checkAgentGate()` (`chat.js`) was only ever
  invoked from `applyPanelVisualState()`'s open branch, so a panel that was
  already open never learned that a key had landed -- the owner had to close and
  reopen it to clear a message that was no longer true. The credential form's
  submit handler now re-runs the gate check immediately on a **successful** save,
  covering both the real "key persisted" path and the `no_op` "an env var already
  provides it" path, since either can change the underlying status. It
  deliberately does **not** re-check on the failure branch: a rejected key must
  leave the gate exactly as it was. `closeSettings()` (`app.js`) re-checks as
  well, as belt-and-suspenders for any other way the Agent tab's status could
  have changed while Settings was open (an operator setting a provider env var
  out of band, say). That path goes through `chat.js`'s own exposed
  `window.muxplexAgentCredential.recheckGate` -- the same "chat.js owns the
  implementation, app.js calls the exposed name" shape that object's other
  entries already use -- rather than reaching into `chat.js` internals.

### Added

- **The agent can see which session you actually have open.** A new exported
  `getFocusedSessionName()` (`app.js`) surfaces this browser's own zoomed-in
  session, and the chat panel hands it to the model two ways: the
  `list_muxplex_sessions` result now marks the matching entry `focused: true`
  (purely additive -- no other entry or field changes, so anything reading the
  existing `name`/`last_activity_at`/`created_at`/`cwd` shape is unaffected), and
  every request in a turn carries a fresh context line naming the focused
  session, or stating plainly that the user is on the all-sessions dashboard with
  nothing expanded. Both are read **live**, at the moment the request is built
  rather than cached at panel-open, so a session switched mid-conversation --
  even between tool-call round trips inside a single turn -- is reflected on the
  very next request. If `getFocusedSessionName()` isn't available at all (an
  older frontend build, or `chat.js` loaded standalone), the line is omitted
  entirely rather than asserting either state: silence, not a fabricated
  "nothing is focused".

  **The signal is `_viewingSession`, not `_activeView`** -- worth recording,
  because the plausible-looking choice is the wrong one. `_activeView` is the
  dashboard's grid *view filter* (`'all'` / `'hidden'` / a named view), and it is
  independent of what is actually open: verified against a live instance, `GET
  /api/state` returned `active_session:"sort-check"` alongside
  `active_view:"all"` at the same moment. Treating `_activeView == 'all'` as
  "nothing is focused" would therefore have been actively wrong in exactly the
  common case. Relatedly, a session opened from a federation peer reports as "no
  local focus" on purpose: `list_muxplex_sessions` is local-only, so naming a
  remote session would point the model at something that tool's own result never
  contains.

## v0.53.0 (2026-08-16)

**You can turn on agent/federation typing from the UI now — no more hand-editing settings.json on every host.**
Letting a remote or federated caller type into this host's sessions used to mean
opening `~/.config/muxplex/settings.json` by hand and flipping two keys, on every
box in the fleet. The failure mode when you hadn't was the quiet one: attach to a
peer's session, watch it render fine, and silently not be able to type. Those two
keys are now settable from the Multi-Device settings tab — but only by a real
operator at a browser, never by the federation Bearer key itself.

### Added

- **Operator-settable terminal input.** `input_enabled` and
  `input_allowed_sessions` can now be set via `PATCH /api/settings` when the
  request is authenticated by a **session cookie or HTTP Basic** (a real
  operator) — they were previously `LOCAL_ONLY_KEYS`, editable only on disk. A
  `bearer_only` caller (the federation Bearer key as the sole credential — the
  same one handed to headless agents) is still refused: `update_settings` computes
  `_bearer_only_caller(request)` and only passes the new
  `OPERATOR_SETTABLE_LOCAL_KEYS` carve-out into
  `patch_settings(allow_local_keys=...)` when the caller is not bearer-only. Both
  keys stay out of `SYNCABLE_KEYS`, so federation sync can never carry them.
  Defaults are unchanged (`input_enabled=false`, `input_allowed_sessions=["*"]`).
- **"Agent Terminal Input" settings sub-form** (Multi-Device tab): a toggle for
  `input_enabled` and a proper add/remove **glob list editor** for
  `input_allowed_sessions`, replacing the single-line hand-edit. The agent compose
  bar re-enables without a page reload the moment input is turned on.
- **One-click "Enable typing for this fleet"** next to the Federation Key block:
  sets `input_enabled=true`, and `["*"]` only if the allow-list is empty (it never
  clobbers a list you've narrowed).
- **First-run welcome** offering to enable typing or jump to federation settings,
  shown once per browser.

### Security

- The boundary "a Bearer/federation/agent caller cannot enable terminal input"
  is enforced server-side and proven by tests that send a real
  `Authorization: Bearer <federation key>` through the real auth middleware — no
  stubbed auth — and were mutation-checked (they fail against a deliberately
  broken gate, pass against the real one). A forged cookie presented alongside a
  valid Bearer cannot downgrade the caller to operator. The other seven
  `LOCAL_ONLY_KEYS` (`new_session_template`, `delete_session_template`,
  `session_commands`, `tmux_socket_dir`, `tls_cert`, `tls_key`, `focus_app`)
  remain disk-edit-only.

## v0.52.0 (2026-08-16)

**Devices can now follow each other, not just the shared server state.**
This is the server + PWA half of a larger design
(`docs/plans/2026-08-16-deck-control-target-design.md`) for choosing what
a browser tab, the Soft Deck, or a physical Stream Deck (`bkrabach/muxplex-deck`,
released separately) controls or follows.

### Added

- **`muxplex_client`**: new `heartbeat()` method; `device_id` parameter added
  to `state()`/`view()`/`connect()`/`set_active_view()`; `ServerState` gains
  `sync_group`, `controlled_by`, and `active_remote_id`; two new error types,
  `TargetGoneError` and `TargetNotSelfOwningError`.
- **Server**: `POST /api/heartbeat`'s `sync_group` now also accepts pairing to
  another known device (`"device:<other id>"`), not just self-claim or
  `"global"` — subject to a cycle guard (a device already being followed
  cannot itself start following someone else; rejected with `400
  target_not_self_owning`, naming the follower). A device record now exposes
  `controlled_by` (who is following it). A heartbeat naming a target that no
  longer exists gets `409 target_gone`, never a silent fallback or a 500. New
  `PATCH /api/devices/{device_id}` sets a human `display_name` that a
  heartbeat's self-reported label never overwrites. New read-only
  `GET /api/federation/devices` fans out to federated peer servers (same
  pattern as the existing `/api/federation/sessions`) so a device registered
  elsewhere in your federation can be seen (not controlled — that stays
  local to each device's own server) from any tab.
- **PWA**: the old binary "Independent view" toggle is replaced by a
  "Follows" dropdown — "Server (shared)", "Nothing — just me", every device
  registered with this server, and (new) an informational "Elsewhere in your
  federation" section for devices registered with a different federated
  peer (not selectable — links out to that peer instead). A persistent
  "Controlled by: ..." chip appears when something else is following this
  tab. A new **Decks** settings tab lists registered devices with an
  inline-editable name, a link to open the Soft Deck, and a link to the
  `muxplex-deck` project for physical hardware. (The existing "Multi-Device"
  settings tab is unrelated — that one is federation setup — and is
  unchanged.)
- **Soft Deck** (`/deck/`): the same "Follows" dropdown, including the
  federated section.

### Compatibility

- Fully additive. A client that never sends `device_id`/`sync_group`/`kind`
  sees byte-identical behavior to before this release (verified by dedicated
  regression tests at every step of this design's build).
- An old `muxplex-deck` (pre-0.16.0) or any other client with no device
  identity continues to operate in the shared/global group exactly as
  always — nothing about this release requires upgrading a deck to keep
  working.

### Also in this release

- CI fix: the `agent` extra is now actually installed for the jobs that need
  it, so the agent-credential test suite (23 tests) runs for real instead of
  silently `ModuleNotFoundError`-ing past coverage it was supposed to have.

## v0.51.0 (2026-08-16)

**The agent is now part of the muxplex install — there is no separate daemon.**
A chat turn that used to be proxied over HTTP to an `amplifier-agent serve
chat-completions` sidecar on `127.0.0.1:9099` now runs **in-process**, as a
library call inside the muxplex server itself (`muxplex/agent_embedded/`). The
entire sidecar is deleted, not deprecated: the proxy path, the `sudo -u aa-svc`
credential machinery, and the systemd/iptables fence that existed to contain it
are all gone.

What did **not** change is the part carrying the security weight: tools still
execute **in the browser**, under the user's own cookie, and the
write-confirmation gate still stands in front of every mutating tool call.
Moving the model call in-process removed a process boundary, not a permission
boundary. The SSE stream is wire-compatible with what the sidecar produced —
`chat.js` cannot tell which one served a given turn.

### Behavioural change worth knowing

- **There is no sidecar to install and no fence to run.** If you followed the
  old `AGENT_CHAT_SETUP.md` / `AGENT_CHAT_SIDECAR.md` procedure, none of it is
  needed anymore — those documents and the `docs/agent-chat-sidecar/` unit and
  fence tree are gone along with the code they described.
- **Provider keys are set in Settings → Agent, or via an environment variable.**
  Credentials resolve **env-first**, falling back to a durable
  `~/.amplifier-agent/credentials.json` (mode `0600`). A key is **validated
  before it is persisted**, so a bad key is rejected rather than written, and a
  persisted key survives a server restart.
- `GET`/`POST /api/agent/provider-credential` **survive** with their contract
  intact, rebuilt on the embedded path. `GET` still returns status only, never a
  key.

### Added

- **`muxplex/agent_embedded/`** — six modules: `runner.py` (per-turn session
  construction, streaming, host-tool yield/continuation), `wire.py` (OpenAI Chat
  Completions SSE chunk builders), `message_shape.py` (client → kernel message
  translation), `host_tool_glue.py` (host-tool proxy and hook), `credentials.py`
  (env-first resolution plus the durable fallback above), and `__init__.py`.
- **`muxplex ensure-agent`** (`muxplex/cli.py`) — bootstraps `amplifier-agent`
  into muxplex's own tool environment from git
  (`github.com/microsoft/amplifier-agent@v0.12.0`) at **first run (service
  install) and on update**. This exists because amplifier-agent is not published
  on PyPI, so neither a PyPI nor a git install of muxplex can pull it as an
  ordinary dependency. It reuses `_get_install_info`/`_upgrade_target` so it can
  never switch muxplex's own install source, is idempotent (a fast no-op when
  the agent is already present), and fails loud rather than leaving a
  half-installed environment behind.
- **An `agent` optional extra** plus its `[tool.uv.sources]` entry, pinning
  `amplifier-agent==0.12.0` / `tag = "v0.12.0"`. It is **not** installed by
  default: without it muxplex runs exactly as before, and the embedded path
  fails with a clean, actionable error rather than an import traceback.

### Removed

- The sidecar in its entirety — the HTTP proxy path in `main.py`, the
  `sudo -u aa-svc` credential machinery, `docs/agent-chat-sidecar/` (systemd
  units, iptables fence, watchdog timer, env examples), `docs/AGENT_CHAT_SETUP.md`,
  `docs/AGENT_CHAT_SIDECAR.md`, and `muxplex/tests/test_agent_fence.py`.
  Net effect on `main.py`: 776 lines deleted.

### Fixed

- `host_tool_glue`'s `amplifier_core` import is now genuinely lazy, so importing
  `muxplex.agent_embedded` no longer requires the `agent` extra to be installed.
- The 503 credential-save headline is mode-agnostic — it no longer names a
  sidecar that no longer exists.

### Dependency pins

- **`uv.lock` regenerated so the `agent` extra is actually recorded.**
  `amplifier-agent` now locks to
  `git+https://github.com/microsoft/amplifier-agent?tag=v0.12.0#421379ad…`, with
  its transitive git dependency `amplifier-foundation` pinned to a commit, and
  muxplex's own lock entry gains `provides-extras = ["dev", "agent"]`. Before
  this, the lock had **no** `amplifier-agent` entry at all — and since a **git**
  install of muxplex resolves from `uv.lock` rather than re-resolving
  `pyproject.toml`, such an install would have re-resolved the agent live on
  every run instead of getting the pinned one. Same class of silent
  PyPI-vs-git drift the tmux-kit pin/tag agreement exists to prevent.
- `tmux-kit` is untouched by this release and still agrees across all four legs:
  pin `tmux-kit==0.4.0`, source `tag="v0.4.0"`, lock
  `?tag=v0.4.0#148c15d9…`, and upstream `v0.4.0^{}` → `148c15d9…`.

### Testing & Proof

- Python suite: **2536 passed, 0 failed, 4 skipped** (52 integration tests
  deselected — they need a real tmux binary). Up from 2504 on the pre-rebase
  stack; the +32 is the deck-control work this release rebased onto, which added
  a net 28 test functions across `test_sync_groups.py` / `test_sync_groups_api.py`
  plus three new `parametrize` decorators expanding to the remaining cases.
- Frontend suite: **1090 passed, 0 failed**. Up from 998; the +92 is exactly the
  deck-control frontend tests rebased onto — net 52 `test()` cases from Steps 1–3
  (`test_app.mjs` / `test_deck.mjs`) plus net 40 from Step 4's PWA dropdown work.
- Both run in the `muxplex-test` DTU from a clean extraction into a fresh
  directory, with `uv sync --extra dev --extra agent` (the embedded-agent tests
  need the `agent` extra; the Makefile default installs `--extra dev` only).

## v0.50.0 (2026-08-16)

> **Correction (same day):** this entry as originally published named APIs that
> do not exist in the actual release (`device_name`/`device_kind` constructor
> params, and error types `DeviceNotFoundError`/`DeviceAlreadyExistsError`/
> `SyncGroupError`/`DeviceIdentityError`). The text below has been corrected to
> match the real, shipped code; the corresponding GitHub/PyPI release notes for
> this historical tag could not be rewritten (the tag is immutable) and still
> carry the original inaccurate wording. **The actual published wheel's code
> was always correct** — only this changelog's prose was wrong. See
> `client/muxplex_client/{sync_client.py,async_client.py,models.py,errors.py}`
> for ground truth.

**Client library now exports device identity scaffolding** — the first slice of
the deck control-target design (`docs/plans/2026-08-16-deck-control-target-design.md`).
Additive only: the server is untouched, and all new client capabilities (`heartbeat()`,
`device_id` parameters on existing methods, new `ServerState` fields for group sync,
and two new error types) are structured to make no behavioral change for any
existing caller.

### Added

- **`heartbeat()` method** on `SyncClient`/`AsyncClient`: `heartbeat(*, device_id,
  label, viewing_session=None, view_mode="grid", last_interaction_at=0.0,
  sync_group=None, kind=None)`. Sends device liveness + identity to
  `POST /api/heartbeat` (the server already accepts `device_id`/`sync_group`
  today via the pre-existing sync-groups feature; `kind` is new and currently
  inert server-side). Returns a new `HeartbeatResult` (`device_id`, `status`,
  `sync_group`).
- **`device_id: str | None = None` parameter** added to `state()`, `view()`,
  `connect()`, and `set_active_view()` on both clients — sent as `?device_id=`
  only when provided; omitting it is byte-identical to today's wire shape
  (verified by dedicated regression tests).
- **New `ServerState` fields**: `sync_group`, `controlled_by`, and
  `active_remote_id` — all parsed via `.get()`, so a server response that
  doesn't include them (any server today) parses exactly as before.
- **Two new error types** in `muxplex_client.errors`: `TargetGoneError`
  (`ApiError` subclass, maps a future `409` with a `{"target_gone": true}`
  detail) and `TargetNotSelfOwningError` (maps a future `400` with
  `{"target_not_self_owning": true}`). Neither can be raised by any server
  today — they're forward-compatible recognizers for Step 2 of the design,
  not built yet.
- `connect()`'s docstring corrected: no longer unconditionally asserts
  "active_session is server-global" (Step 2+ will make that conditional).

### Testing & Proof

- Full test suite: 105 tests pass (65 baseline on v0.49.1 + 40 new), including
  dedicated byte-identical wire-shape tests (exact query string + JSON body)
  for every existing method called with no `device_id`/`kind`.
- All CI jobs green: test (Python 3.11/3.12/3.13), test-latest-deps,
  test-frontend, and all platform variants (Linux/macOS/arm64).
- `ruff format`, `ruff check`, `pyright` all clean on `client/`.

## v0.49.1 (2026-08-16)

**A browser tab or installed PWA showing the muxplex UI kept working at
full rate while you weren't looking at it.** Backgrounded or occluded,
the web app went on running both poll loops -- the 2s sessions poll and
the 1s `/api/state` follow poll -- and the deck PWA went on firing its
1s render tick. None of that is visible work while the tab is hidden, so
it was pure cost: wasted client CPU, a tab the browser could never put
to sleep, and a contributor to the switch-to/typing stalls (the macOS
beachball) seen when the host was already under memory pressure.

The web app had no Page Visibility handling at all. The deck already
stopped its poll on `document.hidden`, but left its render `setInterval`
running.

### Fixed

- `app.js` now gates both poll loops on the Page Visibility API: they
  stop on `document.hidden` and resume on visible, firing one immediate
  poll plus a `sendHeartbeat()` re-register so a device pruned during a
  long hide heals on return. A `_visibilityPaused` guard in each loop's
  tail closes the race where a poll already in flight at the moment the
  tab hid would re-arm its timer after the visibility handler had
  cleared it.
- `deck/deck.js` render tick is now lifecycle-controlled: cleared on
  `document.hidden`, restarted on visible, instead of a bare 1s
  `setInterval` that ran while backgrounded.
- `terminal.js` is deliberately untouched -- gating the terminal
  WebSocket write path risks dropping or reordering output, and xterm's
  own renderer is already paused while the tab is hidden.

### Proof

- `node --test tests/*.mjs` (`muxplex/frontend`): 1008 pass, 0 fail
  (1003 baseline on v0.49.0 + 5 new). The new tests cover: both loops
  stopping on hidden; resume firing exactly one immediate poll plus
  heartbeat; the in-flight-fetch re-arm race for each loop; and the
  deck render-tick lifecycle.
- Full CI green on PR #37 -- all 11 jobs, including macOS/arm64, the
  real-tmux/ttyd integration job, and the frontend `node:test` job.

## v0.49.0 (2026-08-16)

**If you installed muxplex from git rather than PyPI, `muxplex upgrade`
could silently move you onto PyPI -- and never say so.** On any host
where muxplex itself was git-sourced but `tmux-kit` happened not to be,
`upgrade()` built `uv tool install --reinstall --refresh --force
muxplex`: the bare PyPI name, with the git URL absent from the command
entirely. The upgrade reported success. Nothing errored. The install
method had changed underneath you, and the only way to notice was to
read `direct_url.json` afterwards.

That matters beyond tidiness. Some machines **cannot** take their
dependencies from PyPI at all; for those hosts a silent switch to a
PyPI-sourced install is not a cosmetic difference, it is a broken
machine that still reports a successful upgrade. This is the third time
a variant of "upgrade silently switches install method" has been
found -- v0.47.11 and v0.47.12 fixed the `--with tmux-kit` half of it.
This release fixes the half underneath, and adds a mechanical guard so
the next variant fails loudly instead of quietly.

### Fixed

- **`muxplex upgrade` now decides muxplex's own install target from
  muxplex's own recorded source.** The uv-managed branch was keyed off
  `info_kit["source"]` -- *tmux-kit's* source -- to choose between the
  bare `"muxplex"` PyPI shortcut and the explicit `install_target`. That
  conflated two independent questions: "does tmux-kit need a `--with`
  override?" (correctly answered by `info_kit["source"]`, already
  computed into `kit_with_args`) and "what should muxplex install
  itself as?" (answerable only by `info["source"]`). `_upgrade_target()`
  and `_target_matches_source()` had already computed and validated the
  correct git target; a later, unrelated branch threw it away. The
  branch now keys on `info["source"] == "pypi"`, and **both** branches
  use `install_target` literally rather than a separate hardcoded
  `"muxplex"` string, so the two can no longer drift apart. For a PyPI
  install `_upgrade_target()` already returns the bare string
  `"muxplex"`, so this is byte-identical to the old behaviour in the
  common case -- nothing changes for PyPI users.

  Reachable on **every** git install made before v0.45.1 added
  tmux-kit's own git pin, and reachable again any time tmux-kit's
  install source drifts independently of muxplex's.

- **A new mechanical guard, `_install_cmd_targets_install_target()`,
  checks the constructed command against the intended target before
  every install attempt.** `_target_matches_source()` could never catch
  this class of defect: it validates the intermediate `install_target`
  string, never the `install_cmd` actually built from it -- and the bug
  lived entirely in the gap between those two. The new check closes that
  gap by asserting `install_target` appears in the command verbatim,
  whichever branch produced it, and it stays correct if future edits add
  more branches. It mirrors `_install_cmd_preserves_kit_override()`'s
  existing role for the tmux-kit pairing. A regression that reintroduces
  a hardcoded literal now refuses to install and says why, instead of
  succeeding at the wrong thing.

### Changed

- **The agent chat panel no longer renders a working-looking chat UI
  when the Agent has never been configured.** Opening the panel on a
  fresh install used to show a composer that accepted your message and
  only then failed -- "I shouldn't have to submit a chat to find out
  it's broken." A pre-flight gate (`checkAgentGate`/`setGateState` in
  `chat.js`) now checks `GET /api/agent/provider-credential` on every
  panel open and, for `not_installed`/`not_configured`, blanks the panel
  to a notice linking straight into Settings with the Agent tab already
  selected -- the same `openSettings()` + `switchSettingsTab()` pair
  app.js's "manage views" action already uses. It **fails open** on a
  status-check error or timeout, and never resets to "checking" once a
  real answer is known, so the panel cannot flash a broken- or
  working-looking state that turns out to be wrong. The byline's
  "Ctrl+Enter to send" hint hides with the rest of the panel chrome
  while the gate is up, rather than describing a control that is not on
  screen.

- **New syncable setting `composeBarOpen`; the terminal compose bar now
  defaults to visible on every device width.** The compose bar's on/off
  preference was a per-device, localStorage-only value
  (`muxplex-compose-bar`, tri-state `auto`/`on`/`off`) that defaulted to
  visible on mobile widths and hidden on desktop -- easy to miss
  entirely. It now persists through `settings.py` via the **same**
  `sidebarOpen`/`agentPanelOpen` mechanism (`GET`/`PATCH /api/settings`,
  `composeBarOpen` added to `DEFAULT_SETTINGS` and `SYNCABLE_KEYS`)
  rather than a second parallel persistence scheme. Tri-state on one
  value: `null` means "never toggled" and resolves to **visible on every
  width**; an explicit `true`/`false` always wins, on any width.

  **Migration:** an explicit `on`/`off` in the old localStorage key is
  migrated into the new setting exactly once, and only when the server
  has never held an explicit value of its own -- so if you deliberately
  hid the compose bar, the new on-by-default does not silently reopen it
  out from under you. A legacy `auto` carried no explicit intent and is
  not migrated.

  Per this repo's API contract (AGENTS.md, "The API is a public control
  surface"), `SYNCABLE_KEYS` is consumed by muxplex-deck, federation
  peers, and headless clients. The addition is additive; clients that do
  not know the key are unaffected.

- **Settings -> Agent: New/Export are now right-aligned icon buttons**
  (`header-btn`, matching every other icon control in this app's header
  rows), reversing the earlier text-link treatment now that they carry
  icons rather than text labels. A tiny Amplifier mark precedes the
  "Amplifier Agent" byline link, and "Powered by" is coloured to match
  the send-chord hint so the link stays the one emphasised element.

### Fixed (agent chat first-run polish)

All from an owner walkthrough on a fresh install, treated as spec.

- **A rejected credential save was doubled and un-collapsible.** The
  client rendered `"Rejected: " + data.detail` while the server's own
  400 detail *already* began "Rejected: ...", producing one long line
  reading `Rejected: Rejected: the provider reported this key as invalid
  (# openai: list_models() failed: AuthenticationError: ...)` with the
  raw vendor error, masked key, and a support URL inline. The client now
  owns a short summary keyed off the HTTP status (400 rejected, 502
  connectivity, 429 rate-limited, 503 not installed, else a generic
  `Save failed (HTTP n)`) and puts the server's full detail behind the
  existing collapsed "technical detail" disclosure -- the same
  `.agent-msg-tool`/`-summary`/`-raw` pattern `appendToolError()`
  already uses. Investigation confirmed neither client nor server ever
  gated Save on key *shape*; any non-empty key is submitted and the
  server's real connectivity check is the only gate. That was already
  correct and is unchanged -- only the legibility of a rejection.

- **The error icon is inline with the headline** instead of orphaned
  above it (`.agent-msg-error` is now a flex row with the text wrapped
  in `.agent-msg-error-body`, mirroring `.agent-status`'s own icon+text
  row).

- **Doc paths removed from user-facing strings.** The "Agent isn't set
  up" remedy is one short sentence and no longer points at
  `docs/AGENT_CHAT_SETUP.md`; a sibling string in the Settings
  credential notice that had been missed was fixed too. Most installs
  arrive via `uv tool install` and never see `docs/` or `README.md`, and
  Settings -> Agent is reachable from inside the app itself.

### Why this is a minor, not a patch

Everything here is corrective in intent, which argued for a patch --
and the code comments written during development said `v0.48.3`
outright. Both are wrong, for separate reasons.

On the number itself, this repo's precedent decides it. v0.47.0 took a
minor for *retiring* one key from `DEFAULT_SETTINGS`/`SYNCABLE_KEYS`
plus a migration preserving an existing explicit choice; this release
*adds* one, plus a migration preserving an existing explicit choice --
the same change in the mirror direction, on the same public contract.
v0.46.0 and v0.48.0 each took a minor for growing real surface area.
Every v0.47.x and v0.48.x patch, by contrast, was corrective with no new
key and no visible behaviour change; this release has both.

On the in-code references: `v0.48.3` shipped separately while this work
was being prepared, and it contains **none** of it -- it is the
sync-group toggle fix, and nothing else. Six comments and docstrings in
`cli.py` and `test_cli.py` attributed the upgrade incident to `v0.48.3`;
left alone they would have pointed a future reader at a release that
does not contain the fix they describe. They now read `v0.49.0`. All six
are prose; none is asserted on by any test.

### A live illustration, from outside this repo

While this release was being cut, a fleet host was rebooted and its
supervisor's `bootstrap()` ran `uv tool install --force muxplex` with no
version pin -- silently moving that host from 0.48.2 to 0.48.3, a
release it had never been tested against. **That is an ops-side bug,
fixed separately, and it is not the defect this release repairs** (it is
a different program taking a different code path; `muxplex upgrade` was
not involved). It is recorded here because it is the same shape: an
unattended install path that chooses its own target, with no operator
present to notice it chose differently than last time. That is precisely
the failure mode the guard above now makes impossible inside
`muxplex upgrade` -- a command that cannot silently retarget is one
fewer way for a fleet to drift.

### Test-suite coverage (no user-facing change)

**This repo has two suites, and only one of them was in anybody's loop.**
That gap is the most useful thing this release found, and fixing the
mechanism matters more than the individual test failures it caused.

The Python suite was run in the DTU, went green (2,483 passing), and the
release was pushed on that evidence. CI then failed with **31 frontend
failures** in `test (frontend, node:test)` -- against a release whose
single largest surface was frontend JavaScript. The failures were real
and were caused by this release's own changes; they had simply never
been run. `make test` invoked `pytest` and nothing else, so the frontend
suite was effectively opt-in, and nobody opted in.

- **`make test` now runs BOTH suites.** New `test-frontend` and
  `test-python` targets, with `test` depending on both and a shared
  `dtu-sync` step so they run against the *same* snapshot (two syncs
  could otherwise report a combined green that never existed as one
  commit). Frontend runs first -- ~15s versus pytest's ~100s. If `node`
  is missing in the DTU the target **fails loudly**; it is never a
  silent skip, because CI will run that suite regardless.

- **The chat-panel harness can no longer silently lag `init()`'s
  contract.** 23 of the 31 failures were one root cause: `init()` gained
  six required elements (the gate and the chrome it hides) and the
  harness's `REQUIRED_IDS` list -- a hand-maintained second copy of that
  contract -- was not updated, so every test in the file died at load.
  A new drift guard reads chat.js's own `__missing.push("...")` calls and
  fails if the two lists disagree **in either direction**. The
  duplication is now self-checking rather than a standing invitation to
  repeat this.

- **The 7 compose-bar failures were tests asserting a contract this
  release deliberately retired** (the localStorage `auto`/`on`/`off`
  tri-state and its mobile-only default). They now assert the
  `composeBarOpen` contract instead: server-persisted, on by default at
  every width, PATCHed through `/api/settings`. No assertion was
  weakened -- the suite grew from 990 to 998 tests, and the added
  coverage is the **legacy migration**, which had none. That is the path
  with the most user-visible risk in this release (someone who
  deliberately hid the bar must not have it silently reopened), and it
  was the one part with no test at all.

- **The 1 remaining failure was a source-text tripwire**, not a
  behavioural one: `initComposePref()` had to move below
  `await loadServerSettings()` (it reads a value that does not exist
  until then), pushing `updatePageTitle` past the byte window
  `test_app.mjs` scans. The window was widened and documented, exactly as
  the four prior widenings recorded in that test's own comments. Both of
  its assertions are unchanged.

**A note on `init()` hard-failing.** The fix was to update the harness,
not to relax the contract. If the gate elements are absent the panel
comes up with a working-looking composer and no gate -- which is
precisely the "submit a turn to find out it's broken" failure this
release exists to fix. Degrading there would silently restore the bug.
The loud `chat panel BROKEN -- missing DOM element(s)` failure is the
app's own pre-existing convention; this release added elements to a check
that already existed.

### Testing

- **Python: 2,483 passing, 10 skipped, 52 deselected, 0 failing** -- run
  in the `muxplex-test` DTU on a clean extraction into a fresh directory
  (never a tarball overlay onto an existing tree, which produced a false
  failure earlier in this project).
- **Frontend: 998 passing, 0 failing** (`node --test tests/*.mjs`), up
  from 990 at v0.48.3 -- 8 net new tests: 7 covering the compose-bar
  migration (which had none) and 1 harness drift guard.

`tmux-kit` is untouched by this release, and the four-leg pin agreement
was re-verified rather than assumed: `[project.dependencies]`
`tmux-kit==0.4.0`, `[tool.uv.sources]` `tag = "v0.4.0"`, `uv.lock`
`?tag=v0.4.0#148c15d9ff7d660ff001888f13ef82873bcbca8d`, and upstream's
annotated `v0.4.0` peeling (`^{}`) to that same `148c15d9` commit.

## v0.48.3 (2026-08-16)

**The "Follow this server's view" header button showed no visual change
while you were actually following** -- the common case. The static link
glyph never told you which state you were in; only the title tooltip (on
hover) did, and even `aria-pressed` was inverted: `true` meant
independent, not following.

`renderSyncGroupControls()` toggled `.header-btn--active` on `independent`
instead of `following` -- backwards from what the link icon implies. The
class itself has rendered correctly since v0.31.5 (which fixed the
earlier "class has no CSS rule at all" bug); nobody had verified the
boolean feeding it was correct, and it wasn't, since the feature's
original commit (`33eaf80f`).

### Fixed

- `renderSyncGroupControls()` (`app.js`) now keys `header-btn--active`,
  `aria-pressed`, and the button's icon on `following`
  (`_syncGroup === 'global'`) instead of `independent`. The button is now
  visually active exactly when it is actually following.
- The button's icon now differs per state instead of a static glyph:
  linked chain while following, broken chain while independent -- so the
  state reads without hovering for the title tooltip.

### Proof

- `node --test tests/*.mjs` (`muxplex/frontend`): 990 pass, 0 fail
  (988 baseline + 2 new). The 2 new tests were confirmed to catch the
  exact regression: reverting `renderSyncGroupControls()` to the pre-fix
  boolean fails both with the expected inverted assertions (`git stash`
  round-trip against `app.js` only).

## v0.48.2 (2026-08-16)

**If you run muxplex on macOS and had the agent sidecar configured,
`Settings -> Agent` crashed instead of showing you your credential
status.** `GET /api/agent/provider-credential` reached a bare
`asyncio.create_subprocess_exec("systemctl", ...)`. macOS has no
systemd, so that call raised `FileNotFoundError` -- and because no
exception handler is registered on the app, the raw traceback went
straight to you. Nothing was wrong with your setup; muxplex was running
a Linux-only command on a platform where it cannot exist.

This is the same bug v0.48.1 fixed, in a function that patch did not
reach. v0.48.1 (muxplex-at9) stopped the credential check from leaking
raw subprocess stderr into the UI by refusing to spawn a subprocess it
knew would fail. The precondition it added,
`_agent_sidecar_install_gap()`, checks the `aa-svc` service account and
the CLI binary -- both of which can genuinely exist on a non-systemd
host -- and never asks whether systemd itself is present. So the
identical failure survived in the two functions that call `systemctl`
directly. Finding one instance of a class of bug and not sweeping the
module for its siblings is what turned one fix into two releases.

### Fixed

- **`systemctl` is now a checked precondition, not an assumed binary**
  on the agent-sidecar path. A `_have_systemctl()` helper
  (`shutil.which`) gates both direct callers before any process is
  spawned: `_agent_service_env_shadow_vars()` returns an empty set (on
  a host with no systemd there is no unit to introspect, so "no shadow
  vars" is the correct answer, not a degraded fallback), and
  `_restart_agent_sidecar_and_wait()` returns a plain-language failure
  saying the agent sidecar is systemd-only. Neither swallows an
  exception -- the guard runs *before* `create_subprocess_exec`, so
  there is no error to catch. This mirrors the existing
  `_have_systemctl`/`_is_darwin` split in `muxplex/service.py` and
  `muxplex/cli.py`; it is duplicated rather than imported because the
  three modules gate unrelated units and none needs a cross-module
  dependency for a one-line `shutil.which`.

### What macOS CI proved, and what is still unverified

This entry originally claimed the opposite of what turned out to be
true, and the correction is worth more than the original claim.

**What was written first:** that no test exercised
`_agent_service_env_shadow_vars()` at all, and that a green macOS job
therefore proved only that the module imports. That was written from a
grep for the function's name, which found no direct callers in the test
suite.

**CI disproved it.** The first release attempt went red on the
`test (macOS, arm64)` job -- one failure, in
`test_get_status_detects_systemd_environment_file_shadow`, which
reaches `_agent_service_env_shadow_vars()` indirectly through
`GET /api/agent/provider-credential`. The grep missed it because the
test never names the function.

That failure is stronger evidence for this fix than the original entry
claimed to have. It proves, on a real macOS/arm64 host, that
`_have_systemctl()` returns False there and that the guard
short-circuits **before** the spawn -- the test failed precisely
*because* the repaired path executed and correctly returned "no shadow
vars" on a host with no systemd. The mechanism of the fix is verified
on the platform it was written for. The test itself was asserting a
systemd premise it never stated; it now stubs `_have_systemctl` True to
say so explicitly (see CI hygiene below).

**What remains unverified, narrowly:**

- **End-to-end UI behaviour on a *configured* macOS box.** No macOS
  machine was available to open `Settings -> Agent` against a real
  configured sidecar and confirm the panel renders credential status
  rather than a traceback. The guard's mechanism is proven; the
  user-visible outcome is inferred from it.
- **`_restart_agent_sidecar_and_wait()`'s repaired path.** All three
  tests touching it monkeypatch the entire function away, so no test
  ran the real body on any platform. Its guard is identical in shape to
  the one macOS CI did prove, and it now has direct unit coverage for
  both branches -- but it has never executed on macOS.

Stated this precisely because v0.48.0 shipped its headline feature dead
on arrival for exactly this reason: it was verified only in the
environment that happened to be at hand. If you run muxplex on macOS
with the agent sidecar configured, this release is still the one to
report back on.

### CI hygiene (no user-facing change)

Two test failures fixed. Both were faults in the tests themselves; in
neither case was the code under test wrong, and no assertion was
weakened to make either pass.

- **`frontend, node:test`** (muxplex-fii). The vm harness never stubbed
  `performance`, and its `elementStub()` was too thin for what
  `chat.js`'s `init()` actually touches. The harness was fixed -- a
  `performance.now()` stub, and `elementStub()` rebuilt as a Proxy --
  while the test's assertion was left untouched. 988 passing, 0
  failing.
- **`integration, real tmux/ttyd`.** Three tests called real endpoints
  with no credential. They did not break on their own: commit
  `52a2634` deliberately closed the loopback auth bypass (the
  GHSA-7c6r-fvrh-9qp4 fix) and these three were never updated to match.
  They now authenticate via session cookie, exactly as
  `test_session_rename_integration.py` already did. The security fix
  was not weakened or worked around -- the tests were brought in line
  with it. 50 passing, 1 xfailed, 1 xpassed.

### Testing

2,472 passing, 10 skipped, 0 failing on Linux -- unchanged from the
v0.48.1 baseline, as expected for a change whose only non-test edit is
a guard that is false only on non-systemd hosts. The frontend and
integration suites are green for the first time in this release series
(counts above); previously both were red, and the release before this
one was cut with them red.

## v0.48.1 (2026-08-15)

**If you tried the agent panel on v0.48.0 and gave up, that was our bug,
not your setup.** On any server where the agent sidecar had never been
installed -- the default state of every muxplex install, and so the state
essentially every v0.48.0 upgrader was in -- the feature dead-ended
twice, and neither dead end told you what was actually missing:

- The chat panel rendered the plain "the agent isn't set up here" 503 as
  a transient server fault: *"muxplex hit an error of its own while
  handling that. Worth retrying once."* Retrying could never help. There
  was no fault to recover from and nothing on your end to fix -- the
  sidecar was simply never installed.
- `Settings -> Agent` rendered raw subprocess stderr straight into the
  UI -- `sudo: unknown user aa-svc`, `sudo: error initializing audit
  plugin sudoers_audit` -- because the credential check shelled out to
  `sudo -u aa-svc <binary>` with nothing checking first whether that user
  or that binary existed.

Upgrading fixes both. Where the sidecar is genuinely absent, muxplex now
says so plainly and points at `Settings -> Agent` and
`docs/AGENT_CHAT_SETUP.md`. The configured, working path is unchanged.

### Fixed

- **"Not installed" is now a precondition, not a failed command**
  (muxplex-at9). `_agent_sidecar_install_gap()` answers "can the sidecar
  be invoked at all?" using only `pwd.getpwnam` and `os.path.exists` --
  it NEVER shells out, so detecting the not-installed case cannot itself
  produce subprocess stderr to leak. `_run_agent_cli` consults it first
  and raises `AgentSidecarNotInstalled` **before**
  `create_subprocess_exec` is reached, so "we never tried" can no longer
  be dressed up as "we tried and it failed" -- which is precisely the
  conflation both symptoms came from.
- **A new `not_installed` credential state, distinct from
  `not_configured`.** The two were collapsing into the generic `error`
  state carrying whatever stderr came back. `not_installed` (no service
  account, no CLI binary) now renders as a fact with a next step and
  disables the key-submission form rather than offering a POST that could
  only ever be refused; `not_configured` (installed, no provider key yet)
  is unchanged. Both agent endpoints refuse cleanly when the sidecar is
  absent, however they are reached.
- **The chat panel checks for the not-configured 503 before its generic
  5xx branch** (`chat.js`), so the specific, actionable message wins
  instead of being swallowed by "worth retrying once". Matched on the
  server's own wording rather than on the error name, because this
  failure arrives through the plain `!resp.ok` path, never the SSE
  error-frame path.

### Why this shipped broken

Every pre-release verification of the agent panel ran in an environment
where the sidecar was already installed. The configured path was
exercised thoroughly; the state every upgrading user would actually land
in -- no sidecar at all -- was never exercised once. That is a process
failure rather than a coding one, and it is the whole reason a headline
feature shipped dead on arrival for its entire audience.

### Testing

2,472 passing, 10 skipped, 0 failing -- up from 2,466 at v0.48.0. The six
new tests cover the install-gap detection (missing service account,
missing binary, both present), the guarantee that `_run_agent_cli` raises
without spawning a subprocess, and both endpoints' clean refusal when the
sidecar is absent. Verified in a browser in both states: a server with
genuinely no sidecar (the production state that broke), and a configured
server (working path unchanged).

## v0.48.0 (2026-08-15)

**If you run muxplex, upgrade.** This release closes three unauthenticated
authentication bypasses (GHSA-7c6r-fvrh-9qp4, High) that let a *remote*
caller reach the full muxplex API, and the terminal WebSocket, with no
credential at all. It also lands the embedded agent chat panel, but the
security fix is the reason to take this release.

**Read the "Upgrading" note below before you upgrade** -- closing the
bypass is a deliberate behavior change, and anything that reached muxplex
credential-free over loopback will now get a 401.

### Security

- **Three loopback authentication bypasses are closed
  (GHSA-7c6r-fvrh-9qp4, High).** muxplex unconditionally trusted any
  request whose socket peer address was `127.0.0.1`/`::1`. That is not a
  safe test, for two compounding reasons:
  - muxplex binds `0.0.0.0`, so it answers on **every** address in
    `127.0.0.0/8` -- not just `127.0.0.1`. Measured on a live host:
    `127.0.0.2:8088` and `127.0.0.9:8088` both returned HTTP 200
    unauthenticated, because `ip route get 127.0.0.2` selects
    `src 127.0.0.1`, the exact address the check waved through.
  - Any userspace-mode proxy -- `socat`, `ssh -L`, an Incus/Docker
    userspace port-forward -- **re-originates** the connection, so the
    peer address is `127.0.0.1` for a genuinely **remote** caller too.
    Measured live: an unauthenticated `GET /api/sessions` through such a
    proxy returned HTTP 200 with full session data, which muxplex itself
    logged as `127.0.0.1:<port>`.

  There is no socket-level signal that distinguishes "the process calling
  me is truly local" from "a proxy re-originated this for someone
  remote", so **no IP-based rule can be correct here.** All three sites
  are removed rather than tightened:

  1. **`muxplex/auth.py`** -- `SessionAuthMiddleware.dispatch` had a step
     1 that short-circuited auth entirely for a loopback peer. Every HTTP
     request now requires a session cookie, the federation Bearer key, or
     HTTP Basic. The `dispatch` NOTE block (`auth.py:325`) is the
     canonical write-up; `main.py` and `docs/AGENT_GUIDE.md` reference it.
  2. **`muxplex/main.py`** -- `_ws_auth_check` mirrored the same bypass
     for the terminal WebSocket, which carries **live scrollback and
     keystroke input**. Arguably the worse of the two: it exposed both
     read of everything on screen and write into the pane. Now requires a
     cookie or the Bearer key.
  3. **`muxplex/main.py`** -- `_bearer_only_caller` short-circuited to
     "as trusted as a cookie" for a loopback peer, dissolving the fence
     that constrains what the shared federation key may do. A Bearer-only
     caller is now classified Bearer-only regardless of apparent source
     address.

  Regression coverage asserting the bypass stays closed lives in
  `test_auth.py`, `test_api.py`, `test_ws_proxy.py`, and
  `test_client_contract.py`.

### Upgrading -- a behavior change you will notice

**Anything that previously reached muxplex unauthenticated over loopback
now gets a 401.** This is the fix working, not a regression. It will
affect, at minimum:

- local scripts and cron jobs hitting `/api/*` with no credential
- health checks and monitoring probes against `http://127.0.0.1:<port>/`
- `localhost` browser bookmarks that were never asked to log in
- anything driving the terminal WebSocket from the same box
- reverse proxies that terminate in front of muxplex and forward without
  passing a credential through

Each of these needs a real credential now: a `muxplex_session` cookie,
the federation Bearer key, or HTTP Basic. There is deliberately no
"local" exemption to re-enable, because a special case is exactly what
this bypass was.

### Added

- **Embedded agent chat panel ("Muxplex Agent")** -- an in-browser AI
  chat panel served by muxplex and proxied to a local `amplifier-agent`
  sidecar via `POST /api/agent/chat/completions`. Streaming SSE responses
  with markdown rendering, an attention badge, transcript export, WCAG
  streaming announcements, and focus management.

  The interesting property is not the panel, it is **where tool execution
  happens.** The model is *declared* six tools and never calls any of
  them: every tool call comes back down the SSE stream to the browser,
  and **the browser** executes it, same-origin, under the logged-in
  user's own `muxplex_session` cookie. The agent therefore inherits
  *exactly* the calling user's authority -- it is literally the user's
  browser making every request -- and can never grant itself more than
  the cookie already permits. The agent process holds no muxplex
  credential of any kind: no cookie, no API key, no federation key.

  All six tools map to existing public `/api/*` endpoints. **No new
  capability was added for the agent and no endpoint was widened for
  it:**

  | | tool | endpoint |
  |---|---|---|
  | read | `list_muxplex_sessions` | `GET /api/sessions` |
  | read | `get_muxplex_session_details` | `GET /api/sessions/{name}` |
  | read | `list_muxplex_federated_sessions` | `GET /api/federation/sessions` |
  | drive | `switch_muxplex_session` | `POST /api/sessions/{name}/connect` |
  | drive | `switch_muxplex_view` | `PATCH /api/state` |
  | write | `send_muxplex_session_input` | `POST /api/sessions/{name}/input` |

- **Write-confirmation gate on `send_muxplex_session_input`.** That last
  tool is the fenced RCE-by-design endpoint (AGENTS.md -> "Terminal
  input"). It now routes through a modal showing the target session and
  the literal text before anything is sent, with Cancel as the focused
  default so dismissing by any route (Escape, backdrop, blur) lands on
  "do not send". The gate's DOM elements join the panel's loud-fail
  `__missing` check -- the panel refuses to initialise at all rather than
  run with the gate absent, because a gate that silently isn't there is
  worse than no gate.

  **This is a mistake-and-surprise stop, NOT a security boundary.** The
  server-side `input_enabled` / `input_allowed_sessions` fence is
  unchanged and remains the only thing standing between that endpoint and
  anyone holding the user's cookie. Proven behaviorally: with
  `input_enabled` false on disk, the agent's input tool gets the same 403
  a human clicking the same control gets. There is no "agent mode"
  bypass, because the agent has no channel of its own to bypass anything
  with.

- **Provider API key setup via Settings -> Agent**
  (`GET`/`POST /api/agent/provider-credential`). The key is validated
  against a scratch `AMPLIFIER_AGENT_HOME` **before** it is persisted --
  never write-then-restart-and-hope -- and a sidecar restart is decided
  by asking the sidecar's own `/v1/models`, not performed
  unconditionally. Provider allowlist is `anthropic` and `openai` only;
  the request schema carries no endpoint field at all. **muxplex stores
  nothing:** `settings.json` gains zero keys.

- **Debug export path.** All six tool handlers go through one
  `apiFetch()` wrapper recording request/response/duration into a capped
  in-memory log, alongside page-wide console and error hooks. Nothing
  leaves the browser unless a human clicks Export.

- **A UID-level firewall isolating the sidecar from muxplex**
  (`muxplex-agent-fence`, `docs/agent-chat-sidecar/`). The first version
  of this fence was an address denylist and **did not hold** -- it named
  `127.0.0.1/32` and the LAN IP, while muxplex answers on all of
  `127.0.0.0/8`, so the sidecar retained the full unauthenticated API on
  a box whose hand-verification had passed. It checked one address out of
  sixteen million. The replacement inverts the default: the sidecar's UID
  may not initiate a connection to *anything* local, with one narrow
  allowance for the DNS stub resolver it needs to reach its upstream
  model API, plus a destination-independent reject on muxplex's ports.
  Mirrored in ip6tables. Reboot persistence is a systemd unit that
  re-derives the rules each boot and then **proves** them
  (`ExecStartPost=verify`) before declaring success -- a restored ruleset
  that no longer blocks anything still restores silently, whereas this
  fails and `Requires=`/`BindsTo=` keep the sidecar down. A 30s watchdog
  re-proves at runtime and stops both units on breach. `verify` runs a
  positive control first so "muxplex is down" cannot masquerade as "the
  fence works", and refuses to score a timeout or error as a pass.

  Documented deliberate side effect: the sidecar UID can no longer reach
  ttyd on `127.0.0.1:7681`, where it previously could.

- **Design tokens** -- a muxplex design language and token scales, with a
  guard, superseding `assets/branding/tokens.css` as the single token
  file.

### Changed

- **`input_allowed_sessions` now defaults to `["*"]` (was `[]`).** The
  per-session allowlist defaulted to deny-every-session, so flipping
  `input_enabled: true` opened nothing on its own -- the operator hit a
  SECOND 403 naming the allowlist and had to enumerate session names by
  hand. Three separate times that read as "the feature is broken." One
  deliberate operator action now turns typing on for every session;
  narrowing is opt-in (`["agent-*"]`), and a bare string form (`"*"`) is
  normalized to a one-element list upstream of the fence.

  **The real boundary is untouched.** `input_enabled` still defaults to
  `False`; with the allowlist now open by default it is the *only* fence
  between a federation-Bearer-key holder and RCE on every session, which
  is exactly why it stays local-file-only and default-false. Both keys
  stay in `LOCAL_ONLY_KEYS` -- not PATCHable, not federation-syncable,
  not settable by the agent -- and that is now proved behaviorally: a new
  test drives both remote doors (`patch_settings` and
  `apply_synced_settings`) and asserts neither can widen OR narrow.

- **Settings gains an Agent tab** (7 tabs, was 6): per-device send/newline
  bindings and a transcript disclosure, plus the provider-credential form
  above. The agent panel's open/closed state persists via a new
  `agentPanelOpen` setting, documented in the README settings table.

- **Merge shape, recorded deliberately.** The 60-commit
  `poc/agent-chat-panel` branch was merged with `--no-ff` rather than
  squashed. AGENTS.md says PRs are squash-merged; that convention is
  sized for an ordinary feature PR, and squashing a branch carrying a
  security fix whose reasoning is spread across several commits would
  destroy history worth keeping.

### Verification

- `tmux-kit` pin/tag/lock three-way agreement re-checked at release time
  per AGENTS.md's "tmux-kit pin/tag agreement": `[project.dependencies]`
  `tmux-kit==0.4.0`, `[tool.uv.sources]` `tag = "v0.4.0"`, and `uv.lock`
  `source = { git = "...?tag=v0.4.0#148c15d9..." }`. The locked commit
  `148c15d9` is confirmed to be what upstream's annotated tag `v0.4.0`
  peels to, and `v0.4.0` is the newest upstream tag. **No change needed
  this release** -- recorded because "they agree" is only worth anything
  when it was actually looked at.
- Full suite green on the exact published tree: **2466 passed, 10
  skipped, 0 failed**, run in the `muxplex-test` DTU on a clean
  extraction.

## v0.47.12 (2026-08-15)

A fix to the upgrade path itself -- v0.47.11 could not be installed by
`muxplex update` at all. Real failure on a real machine going v0.47.10 ->
v0.47.11:

```
  Installing latest version...
  ERROR: uv tool install failed:
  × Failed to resolve dependencies for `muxplex` (v0.47.11)
  ╰─▶ Requirements contain conflicting URLs for package `tmux-kit`:
      - git+https://github.com/bkrabach/tmux-kit@v0.4.0
      - git+https://github.com/bkrabach/tmux-kit@v0.4.0
  ERROR: upgrade failed — muxplex service has been restarted (best-effort).
```

The two URLs are byte-identical -- uv rejects two url-bearing requirement
origins for the same package regardless of whether they agree.

### Fixed

- **`muxplex upgrade` no longer adds a `--with tmux-kit` override on top of a
  git muxplex install target** (PR #34, `735c45d`). `upgrade()`'s uv-managed
  branch was appending `--with "tmux-kit @ git+<url>@<ref>"` unconditionally
  whenever tmux-kit's recorded source was git -- including when muxplex's OWN
  install target was ALSO a git URL (`git+https://github.com/bkrabach/muxplex@vX`).
  A git muxplex target's own `pyproject.toml` already carries the
  `[tool.uv.sources] tmux-kit = { git = ..., tag = ... }` pin, which uv reads
  and honors on its own -- the override then became a SECOND url-bearing
  origin for the identical package, and uv refuses to resolve.
  - **The distinction now encoded:** git install target -> `[tool.uv.sources]`
    is honored, tmux-kit is already pinned to the right git ref, so the
    override is redundant *and* fatal. PyPI install target ->
    `[tool.uv.sources]` is stripped from published wheel metadata, so only the
    plain `tmux-kit==X.Y.Z` pin survives; the override is load-bearing there
    (managed devices that can't reach PyPI), and that protection is unchanged.
  - **Reproduced in isolation** in a scratch `UV_TOOL_DIR`: with the override
    on a git target -> the failure above; without it -> installs cleanly and
    resolves tmux-kit 0.4.0 from git (verified via the chained
    `build_send_key_argv` output), proving the git source pin alone suffices.
  - `_install_cmd_preserves_kit_override()` now takes the install target and
    enforces both directions -- override absent for a git target, present for
    a PyPI target -- so the guard still rejects a PyPI command with the
    override stripped.
  - **We could not determine why this same git+git pairing did not fail on
    earlier upgrades** -- the identical shape existed at v0.3.5. The code's
    own comments already flag uv's `--with` preserve-vs-replace semantics as
    "unproven and version-dependent," so a uv version change is plausible but
    unproven. Stated as unknown rather than guessed at.
  - **Operator note:** anyone currently on v0.47.10 or earlier whose
    `muxplex update` failed with the conflicting-URLs error can get unstuck
    with a direct install, e.g. `uv tool install --force --refresh
    git+https://github.com/bkrabach/muxplex@v0.47.12`, then restart the
    service.

## v0.47.11 (2026-08-15)

"Scrolling up silently enters it" -- `muxplex/tmux_templates/base.conf:98`'s own
comment already knew about this. With `mouse on` (`base.conf:27`), tmux enters
copy-mode silently on mouse wheel-up, and every key sent afterward through
`send-keys` is captured by copy-mode's key table instead of reaching the
pane's program. Scroll back in a terminal panel, send a command from the
compose bar, and nothing happens -- no error, no feedback. Two
developer-facing hardening PRs landed alongside the fix.

### Fixed

- **Scrolling back in a terminal panel no longer swallows keystrokes --
  including interrupts.** tmux-kit bumped 0.3.5 -> 0.4.0 (PR #31, `ffdfea8`).
  Worse than the silent-no-op above: `api.stop()` (tmux-kit's MCP-exposed
  interrupt, what an AI agent calls to kill a runaway process) sends `C-c`
  the same way it sends any other key. Measured on real tmux 3.4 with
  muxplex's own `base.conf`: with the pane scrolled back, a `while true` loop
  kept running after `stop()` -- output went from 5 lines to 9 over 4
  seconds -- because `base.conf` rebinds `C-c` in copy-mode to
  `copy-selection-and-cancel`. The interrupt was consumed as "copy the
  selection," never delivered.
  - The fix lives in the library, not in muxplex: `build_send_text_argv()`/
    `build_send_key_argv()` now each chain a `copy-mode -q` exit into the
    SAME argv ahead of `send-keys`, via a literal `;` separator -- the
    identical one-invocation-chaining technique
    `observe.capture_pane_window()` already used:
    ```
    ["copy-mode","-q","-t",name,";","send-keys","-l","-t",name,"--",text]
    ["copy-mode","-q","-t",name,";","send-keys","-t",name,key]
    ```
  - `/api/sessions/{name}/input` needed no runtime change at all -- it, and
    every other tmux-kit consumer, gets the guarantee for free, and no
    future caller can forget it.
  - `copy-mode -q` was chosen over `send-keys -X cancel`, which exits 1 with
    "not in a mode" on a pane that isn't already in one -- that would have
    made every ordinary send raise. A single chained invocation also closes
    the window for the user to re-scroll between the exit and the send.
  - Verified: text containing a literal `;` still passes as one argv element
    after `--` and reaches the shell as characters -- the literal-send
    security property is unchanged.

### Changed

- **The test suite now runs cleanly on a host serving a live muxplex,
  instead of refusing to start** (PR #32, `5071a86`). Previously
  `pytest_sessionstart` refused the entire suite whenever anything answered
  `DEFAULT_SETTINGS["port"]` (8088) -- safe, but unrunnable on a dev box
  running a real muxplex. Refusal is replaced with structural isolation:
  `uvicorn.run` is neutralized by default (opt in via the new
  `allow_real_uvicorn_run` marker, which must still pin a port), a
  `free_port` fixture hands out an OS-allocated ephemeral port, and the
  session guard is now a cheap AST scan for the one shape that actually
  caused past damage, rather than a network probe. The
  `MUXPLEX_TEST_ALLOW_LIVE_HOST` bypass is retired -- there's no override
  needed once the dangerous shape is structurally impossible. Audit finding:
  the suite never needed a live port in the first place -- every API/UI
  test uses `TestClient`, and the two tests that need a real server already
  bound port 0.
- **The tmux-kit bump bot is now idempotent on branch existence, not just
  open PRs** (PR #33, `5e27b70`). Real incident: run `31891698184` pushed
  branch `bump-tmux-kit-v0.4.0`, then failed at `gh pr create` ("GitHub
  Actions is not permitted to create or approve pull requests"). With a
  pushed branch and no PR, the next scheduled run would have re-committed
  identical content under a new timestamp and been rejected
  non-fast-forward -- a red run every day, indefinitely. The workflow now
  probes the branch with `git ls-remote` first and, when the branch exists
  with no open PR, opens the PR against it instead of re-committing.

## v0.47.10 (2026-08-15)

Follow-up to v0.47.2: the terminal no longer corrupts while scrolling on
mobile, but it still wasn't *smooth* -- "the scrolling wasn't smooth [on
mobile] ... it's very smooth on desktop." That desktop/mobile split was the
clue: desktop's `visualViewport` `resize` essentially never fires during a
scroll, so v0.47.2's height-unchanged guard makes it a no-op every time;
mobile's `scroll`/`resize` genuinely re-fire a NEW height on every tick of a
toolbar-collapse/keyboard-open animation, and each one still ran
`_termRefit()` (`FitAddon.fit()`) fully synchronously -- a forced layout
read right after the CSS write invalidates layout, competing with the
browser's own scroll/toolbar compositing on every single tick.

### Fixed

- **Mobile terminal scrolling now coalesces the local refit to at most once
  per rendered animation frame**, instead of once per raw `visualViewport`
  event. `initVisualViewport()`'s `_vpHandler` (`terminal.js`) now schedules
  `_termRefit()` through `requestAnimationFrame` (schedule-if-not-already-
  scheduled, the same pattern `initMobileTerminalScroll()`'s rAF-batched
  wheel dispatch already uses further down the same file) rather than
  calling it directly. The `--app-viewport-height` CSS write remains fully
  immediate and unthrottled on every genuine height change -- only the
  comparatively expensive `fit()` call is batched, so the container still
  visually tracks the animating viewport in real time.
  - Measured directly against a synthesized 20-tick burst of genuinely
    different heights: the pre-fix code ran `fit()` 20 times (one per
    event); the fix runs it once per animation frame -- 1 time when the
    whole burst arrives before a single frame is painted, exactly 2 when
    split across two frames, etc. (`tests/test_terminal.mjs`, new tests in
    the "mobile scroll SMOOTHNESS" section).
  - Falls back to the old synchronous-immediate call when
    `requestAnimationFrame` is unavailable, so Node's test environment (and
    any other non-browser context) is byte-for-byte unaffected -- this is
    also why both v0.47.2 corruption regression tests
    (`initVisualViewport: a scroll/resize tick with an UNCHANGED height is a
    true no-op` and `connectWebSocket: rapid onResize firings are throttled
    to the server`) still pass unmodified.
  - `closeTerminal()` now cancels a still-pending coalesced refit
    (`cancelAnimationFrame`) so a stray callback from a closed session can
    never fire an extra `fit()` against whatever session opens next.
  - **Not independently confirmed on a physical mobile device** -- the
    available tooling has no way to drive a real browser-toolbar animation.
    The fix is evidenced by the measured refit-count reduction above and by
    it degrading to exactly today's behavior wherever `requestAnimationFrame`
    isn't exercised; a phone is needed to confirm the felt smoothness.

## v0.47.9 (2026-08-14)

Fifth round of "the four quick-link controls still don't behave the same" --
this time the owner explicitly overruled the previously-deliberate
"`#sort-order-select` / `#sidebar-sort-order-select` stay native `<select>`s"
constraint from v0.47.8 (see that entry's "Known ceiling" note). Four rounds
of CSS-only fixes converged the two sort `<select>`s and the two view
`<button>` triggers on identical color/border/background/transition
declarations, but a `<select>`'s own popup, focus ring, and value display are
rendered by the browser -- outside CSS's (and JS's) reach. No amount of CSS
could make a `<select>`'s hover/focus/**open** behavior genuinely match a
`<button>`'s. The fix is no longer structural-CSS; it's changing the element.

### Changed

- **`#sort-order-select` (overview header) and `#sidebar-sort-order-select`
  (sidebar) are `<button>` triggers now, not `<select>`s** -- same ids, same
  `quick-sort-select quick-link` classes, but each now opens a sibling
  `role="menu"` popup (`#sort-order-menu` / `#sidebar-sort-order-menu`) built
  from the same `.view-dropdown__item` / `.view-dropdown__menu` markup the
  view dropdown already uses, instead of a browser-native option list.
- **New shared "quick dropdown controller"** (`createQuickDropdown()` and
  friends in `app.js`) is the ONE open/close/toggle/keyboard/click-away
  mechanism now driving all FOUR quick controls -- the header + sidebar view
  switchers (refactored to use it) and the header + sidebar sort controls
  (new). Previously there were two near-duplicate view-dropdown
  implementations; adding two more hand-rolled sort-dropdown copies would
  have made four. One implementation, four instances.
- **Arrow-key navigation now works on the sidebar view dropdown too** -- a
  pre-existing gap (the old keydown handler only ever checked
  `#view-dropdown-menu`) closed as a side effect of unifying the mechanism.
- **Opening one quick dropdown now closes any other that's open** (mutual
  exclusivity) -- new behavior enabled by the shared controller; previously
  the sort `<select>`'s native popup and the view dropdown's custom popup
  had no way to know about each other.
- Settings > Sessions' own sort select (`#setting-sort-order`) is
  **unchanged** -- still a native `<select>` in a settings form, never part
  of the "these four should be the same component" ask.

### Lost relative to the native `<select>`

- **Keyboard type-ahead** (typing a letter to jump to a matching option) --
  a `<select>`'s built-in behavior; the custom menu has no equivalent.
- **The native mobile OS picker wheel** -- iOS/Android render a `<select>`
  with their own full-screen picker UI; the custom menu is a regular
  absolutely/fixed-positioned popup instead, styled and behaving the same on
  mobile as on desktop.

Both are real, functional regressions the owner has accepted in exchange for
genuine visual/behavioral consistency across all four quick controls -- kept
here as an honest record, not smoothed over.

## v0.47.8 (2026-08-14)

Fourth round of "the four quick-link controls still don't look the same" --
this time the fix is structural, not another value tweak. Root cause: two
legacy per-element base classes (`.view-dropdown__trigger`,
`.quick-sort-select`) kept their OWN `:hover`/`:focus-visible` rules with a
border-color + background swap, left over from before either control read as
a link. CSS cascades per PROPERTY, not per rule -- a higher-specificity rule
that never mentions `background` doesn't beat a lower-specificity rule that
sets it, so the legacy background fill kept winning on hover for the header
view trigger and both selects, and -- because only the SELECT's legacy rule
also fired on `:focus-visible`, never the button's -- the sort select alone
showed a boxed highlight on keyboard focus that the view trigger never did.
That's exactly what the owner reported this round.

### Changed

- **`.quick-link` is now the ONLY rule anywhere that sets color, border,
  background, or transition for any of the four view/sort quick controls, in
  ANY state** (rest, hover, focus-visible, active, expanded). The shared
  interactive-state rule now EXPLICITLY declares `background: transparent;
  border: none;` rather than relying on the legacy rules simply not
  mentioning those properties -- an explicit override wins the cascade
  regardless of what any other rule declares for the same property.
- **`focus-visible` now gets the same color change as `hover`, for all four
  controls** -- previously only the two `<select>`s did this (a native
  `<select>` doesn't reliably fire `:hover` on keyboard focus, so its own
  legacy rule listed `:focus-visible` too); the two buttons' focus-visible
  got the outline ring only. Tab-focusing any of the four now looks
  identical to hovering any of the four.
- **Added an explicit `:active` state** and a best-effort `:open` state for
  the two `<select>`s (progressive enhancement via its own rule, so an
  engine that doesn't support `:open` on `<select>` simply skips it).
- **Base padding (`4px 10px`) moved into the shared `.quick-link` rule.** The
  header's sort select (`#sort-order-select`) keeps ONE narrowly-scoped,
  honestly-commented `padding-right` override for its caret-clearance
  reserve -- a genuine structural need of the caret-overlay mechanism (the
  disclosure arrow is a pseudo-element on the wrapper, not real DOM content
  like the button's `<span>` caret), not a "look" difference.
- **`.view-dropdown__trigger` and `.quick-sort-select` now carry ONLY
  structural resets** a `<button>` vs. a native `<select>` genuinely,
  differently need (the appearance reset and a max-width for the select;
  nothing at all for the button) -- zero color/border/background/transition,
  and zero `:hover`/`:focus-visible`/`:active`/`[aria-expanded]` rules of
  their own, permanently.

### Known ceiling (not a bug)

Verified in a real browser (Edge/macOS): both `<select>`s show a very faint,
persistent native background tint behind their text/caret, at rest, that
neither `<button>` shows -- present identically on both the header and
sidebar select (so it is not a drift between instances), and not removable
via `appearance: none` or an explicit `background: transparent`. This is a
native rendering artifact of the `<select>` element itself on this
engine/OS, not an authored CSS difference; it may vary across
macOS/Linux/Windows/Android. Converting the select to a styled `<button>`
would remove it, but `test_frontend_html.py` deliberately asserts both stay
native `<select>` elements (keyboard type-ahead, the mobile OS picker), so
this residual difference is accepted rather than papered over.

### Testing

- Strengthened `test_css_quick_link_header_pair_no_longer_boxed_on_hover`,
  which previously only asserted the shared rule didn't mention
  border/background -- true, and irrelevant, since the legacy rules still
  did. It now requires an EXPLICIT no-box guarantee in the shared rule.
- Added `test_css_legacy_classes_have_no_visual_state_rules`, which asserts
  neither legacy class may ever again pair with an interactive
  pseudo-class/attribute selector, and neither rule body may declare
  color/border/background/transition -- closing the loophole structurally
  instead of re-checking a value that can drift back.

## v0.47.7 (2026-08-14)

Fixes the view-switch flicker bug the owner reported after v0.47.6 (new value
-> reverts to prior value for ~1-2s -> flips forward again), and extends the
sidebar's "quick link" treatment (v0.47.4-v0.47.6) to the overview header's
matching view/sort pair, per explicit owner direction. Also drops the
underline at rest from all four controls now that the header pair has joined
-- the owner noticed the sidebar's view trigger showed one and its sort
select didn't, despite sharing the same rule.

### Fixed

- **View-switch flicker (self-stomp race between an optimistic local switch
  and the dedicated ~1s state poll).** `switchView()` applied the new view
  locally and fired a fire-and-forget `PATCH /api/state`; `followRemoteActive
  View()` runs on every poll tick and reverted to whatever the server
  currently had if it differed from the local value. A poll landing after
  the local switch but before the PATCH's write was visible server-side read
  the OLD value and reverted the UI -- then the next poll, once the write
  landed, flipped forward again. Reproduced live in a real browser (an
  artificially delayed mock `PATCH /api/state` held the write open long
  enough to watch the header's view label revert and then recover) against
  the unfixed v0.47.6 build, and confirmed absent against the fix.
  - Root cause traced by comparing against `onSortOrderChange()`, which
    updates its local snapshot before firing its PATCH and never exhibited
    the bug -- the missing piece for `active_view` was a race *guard*, not
    write ordering (`switchView()` already applied locally before its PATCH).
  - Fix: a new `_pendingViewSwitches` counter, incremented before persisting
    a local `active_view` change and decremented once that write settles
    (success or failure) -- the same proven mechanism `openSession()`
    already uses (`_pendingLocalSwitches`) to guard the identical class of
    race on `active_session`. `followRemoteActiveView()` now suppresses a
    remote-apply while a local write is still in flight.
  - New shared helper `persistActiveView(viewName)` is the ONE place every
    `active_view` PATCH now goes through -- `switchView()`, the
    delete-active-view fallback in `renderViewsSettingsTab()`, and the
    rename-active-view path in `openManageViewPanel()`'s `commitRename()`
    all previously PATCHed independently (two of the three had also drifted
    from `withDevice()`, meaning a device in its own private sync group was
    writing `active_view` to the shared "global" group instead of its own --
    fixed as part of this consolidation).
  - PATCH failure is no longer silent: `persistActiveView()` shows a toast,
    logs a warning, and reconciles with the server's actual current
    `active_view` (render-only, never re-PATCHed) rather than leaving the
    UI showing a value the server never accepted.
  - Cross-device sync (another tab/device/deck genuinely changing the view)
    is unaffected -- the guard only suppresses the window while THIS tab's
    own write is unconfirmed; a remote change is followed as soon as it
    clears.

### Changed

- **The header's view-dropdown trigger (`#view-dropdown-trigger`) and sort
  select (`#sort-order-select`) now carry the sidebar's link treatment too**
  -- accent-colored text, no border, no filled background, at rest or on
  hover/focus (previously a boxed control: border + background swap on
  hover, matching the header's other icon buttons). The shared rule was
  renamed `.sidebar-quick-link` -> `.quick-link` and extended to all four
  controls (single comma-separated selector list, still ID-qualified per
  control for specificity) rather than duplicated, per
  `test_css_quick_link_is_one_shared_rule_not_four`. The header pair KEEPS
  its caret/disclosure-arrow (a compact command in a packed row of icon
  buttons still benefits from one, unlike the sidebar's spacious two-row
  rail) and its own existing sizing -- only the sidebar's two-row-rail
  sizing (`flex: 1`, tight padding, ellipsis truncation) stays scoped to the
  sidebar instances.
- **Underline dropped at rest, everywhere.** A native `<select>`'s own
  displayed value doesn't reliably honor `text-decoration` across engines
  (the sidebar's sort select never showed the underline the CSS declared,
  while its sibling button did) -- color + the adjacent label/caret already
  read as "interactive" without it, so `text-decoration: underline` and
  `text-underline-offset` are removed from the shared rule entirely.

### Verification

- Frontend suite (`node --test tests/*.mjs`) extended with: a deterministic
  reproduction of the flicker race (never-resolving mock PATCH pins the
  pending-switch window open), a genuinely-stale-remote-switch-still-
  followed case, a guard-clears-itself-after-settling case, device-scoped
  PATCH URL assertion, two PATCH-failure reconciliation cases (network
  failure and a genuine concurrent remote change), and delegation checks for
  the delete/rename call sites.
- `test_frontend_css.py`/`test_frontend_html.py` updated for the renamed
  `.quick-link` class and the header pair's new markup/CSS coverage.

## v0.47.6 (2026-08-14)

Third pass on the sidebar's view/sort quick controls, per explicit owner
direction on v0.47.5: drop the disclosure carets so each control reads as a
*pure* link, unify the two controls' styling into one shared rule so they
can never drift out of sync again, label each control, and remove the
sidebar's own collapse button (redundant with the expanded header's
hamburger).

### Changed

- **Both sidebar quick controls now share ONE CSS rule.** The view trigger
  button and `#sidebar-sort-order-select` previously had two independently
  maintained rules (`.sidebar-view-trigger`, ~19 declarations, vs. a
  3-declaration `#sidebar-sort-order-select.quick-sort-select--link`
  override) that had already drifted out of sync -- the owner noticed the
  select was missing properties the button had. Both elements now carry
  the identical `.sidebar-quick-link` class, styled by one comma-separated
  selector list (`.sidebar-quick-link, #sidebar-sort-order-select.sidebar-
  quick-link { ... }`); changing a value changes both controls at once.
  The select still needs the ID-qualified selector to reliably win
  specificity over the base `.quick-sort-select` rules it also carries
  (equal-specificity + source-order was verified-in-browser unreliable
  for a native `<select>`'s own text rendering in a prior pass -- not
  re-litigated).
- **No disclosure caret/arrow on either sidebar control.** The view
  trigger's `<span class="view-dropdown__caret">` was removed from the
  markup outright; the sort select's arrow (drawn via
  `.quick-sort-dropdown::after`) is suppressed for the sidebar instance
  only, via `.sidebar-header-controls .quick-sort-dropdown::after {
  content: none; }`. The overview header keeps its caret/arrow unchanged
  in both cases -- out of scope for this pass.
- **Each control now has a text caption ("View" / "Sort").** A small
  `.sidebar-title` label (revived -- it had been dead CSS, unreferenced by
  any markup, since a prior refactor) precedes each link so the control is
  still self-explanatory without a caret to hint "this opens something."
  Text was chosen over an icon/emoji: this fleet spans macOS, Linux,
  Windows, and Android, and emoji glyph rendering (colored vs. monochrome
  vs. missing/tofu) is inconsistent across those platforms -- exactly the
  kind of visual-inconsistency risk this pass exists to remove, just moved
  from CSS to font rendering. A single glyph is also inherently ambiguous
  between "view" and "sort" without a text label alongside it anyway,
  which would have undercut the simplification an icon was meant to buy.
- **`#sidebar-collapse-btn` is removed.** The expanded header's
  `#sidebar-toggle-btn` hamburger already calls the identical
  `toggleSidebar()`, so the sidebar's own collapse button was a second
  control for the same action. `toggleSidebar()` no longer reads or
  writes a `#sidebar-collapse-btn` element (the function used to update
  its chevron text on every toggle); the click-listener binding for it
  in `bindStaticEventListeners()` is removed too, along with the
  now-orphaned `.sidebar-collapse-btn` CSS (including its `<960px`
  responsive `display: none` override).
- **Verified in a real browser via `browser-tester` agents:** desktop
  sidebar width and mobile overlay width screenshots, before/after;
  both quick controls confirmed visually identical in treatment at rest
  (same accent color, same underline, no caret on either); collapse
  button confirmed absent; the hamburger (`#sidebar-toggle-btn`) confirmed
  to still open/close the sidebar; sort selection confirmed to round-trip
  live from the sidebar to the overview header's quick-sort and Settings >
  Sessions; both controls confirmed keyboard-reachable via Tab with a
  visible focus ring.

## v0.47.5 (2026-08-14)

Revises the sidebar's view/sort quick controls per explicit owner direction on
v0.47.4: two rows instead of one, and a genuinely link-like at-rest look
instead of the boxed hover/focus affordance that pass shipped.

### Changed

- **Sidebar quick controls now stack in two rows.** `.sidebar-header-controls`
  wraps `#sidebar-view-dropdown` and the sort control's `.quick-sort-dropdown`
  in a flex column, so the view switcher and the sort control each get their
  own line. A single-row layout was tried in v0.47.4 and explicitly
  overruled by the owner -- not re-litigated here.
- **Both controls now read as links, not boxed controls.** `.sidebar-view-trigger`
  and the new `.quick-sort-select--link` modifier drop the border/background
  affordance entirely (at rest **and** on hover/focus) in favor of
  accent-colored, underlined text (`color: var(--accent)`,
  `text-decoration: underline`) -- the "this is clickable" signal now lives in
  color and underline, the idiom the owner asked for, rather than a box.
  `:focus-visible` gets a 2px accent outline ring (there's no border left to
  recolor for keyboard focus). The disclosure caret
  (`.view-dropdown__caret` / `.quick-sort-dropdown::after`) stays muted-gray
  on purpose in both places -- the label text carries the link signal, the
  caret is a secondary hint, same as a trailing chevron next to a hyperlink.
- **`#sidebar-collapse-btn` stays a sibling of the two-row stack, not a child
  of it**, so it keeps its fixed top-right position in `.sidebar-header`
  regardless of how many rows the controls grow to -- it toggles the whole
  panel, not a link in this family, so it doesn't inherit the treatment or
  move into the stack.
- **`.quick-sort-select--link` is sidebar-only, added as an additional class
  alongside the base `.quick-sort-select`** (`#sidebar-sort-order-select`
  carries both). The overview header's `#sort-order-select` keeps the bare
  `.quick-sort-select` look from v0.47.4 unchanged -- it's a compact command
  in a row of icon buttons, not a narrow rail, and the owner's two-row /
  link-like direction was specifically about the sidebar. Both selects stay
  native `<select>` elements (`tests/test_frontend_html.py` asserts
  `el.name == "select"` for both, unchanged, deliberate).
- **Verified in a real browser (with two honestly-documented gaps):** both
  controls read as interactive at rest -- accent-colored, underlined text --
  confirmed via CDP screenshot + vision comparison against an isolated
  control page (both showed the same cyan, distinct from the base's near-
  white). Sort selection round-trips live: changing the sidebar's own
  select propagates to the overview header's quick-sort AND Settings >
  Sessions (`syncSortOrderControls()` / `onSortOrderChange()` untouched --
  no `app.js` changes were needed). **Gap 1:** the sort select's underline
  and the `:focus-visible` outline ring could not be confirmed visually in
  this session -- a Tab dispatched via automation never rendered a visible
  ring even on an isolated, unrelated test control using the identical CSS
  technique, pointing at a synthetic-input/`:focus-visible` interaction in
  the automation pipeline rather than a CSS defect (the source is
  confirmed correct: `text-decoration: underline` and
  `:focus-visible { outline: 2px solid var(--accent); }` are both present
  and unambiguously scoped -- see `#sidebar-sort-order-select.quick-sort-
  select--link` in style.css). A manual keyboard check is recommended as
  final confirmation. **Gap 2:** the header-height delta below is computed
  from the shipped CSS box model (padding, ~16px line-height at 13px font,
  4px inter-row gap), not measured via a live `getBoundingClientRect()` --
  this browser-automation session had no script-execution primitive to
  pull real layout geometry, and screenshot-based pixel-counting proved
  unreliable for something this precise. Computed: sidebar-header grows
  from **~43px (one row)** to **~71px (two rows)**, a delta of **~+28px**,
  at both a normal desktop sidebar width (200px) and the mobile overlay
  width (240px) -- neither control wraps at either width, so the delta is
  the same at both. That spends back roughly a third of the ~90px the
  sidebar list itself reclaimed in v0.47.3 (a different area of the
  sidebar -- the list body, not this header) -- a real, explicit vertical
  cost for the two-row layout the owner asked for.

## v0.47.4 (2026-08-13)

Reworks the sidebar header's view/sort controls so both read as obviously
interactive, compact commands instead of a mix of a muted static-looking
label and a boxed form control.

### Changed

- **Sidebar view dropdown and quick-sort control now share one visual
  language.** The view switcher (`#sidebar-view-dropdown-trigger`) was
  already a fully-functional dropdown (button + caret + menu), but
  `.sidebar-view-trigger` styled it as transparent, borderless, muted
  text -- so it read as a static label rather than something clickable,
  which is what actually prompted the "make the view label a dropdown"
  request. Meanwhile the quick-sort control (`#sidebar-sort-order-select`
  / the header's `#sort-order-select`) was a `.settings-select` native
  `<select>` with a full border and solid background, so *it* read as the
  interactive one -- backwards from what a glance at the two suggested.
  Fixed by aligning both to the same treatment: full-contrast text
  (`var(--text)`, not `--text-muted`), no border/background at rest, and a
  visible border + surface background on hover/focus (`aria-expanded` for
  the trigger, `:hover`/`:focus-visible` for the select) -- mirroring
  `.view-dropdown__trigger`, the header's already-correct instance.
- **Quick-sort selects restyled as compact commands, not boxed selects.**
  `#sort-order-select` and `#sidebar-sort-order-select` **stay native
  `<select>` elements** -- `tests/test_frontend_html.py` asserts
  `el.name == "select"` for both, a deliberate, guarded contract (a
  browser-native select keeps full keyboard operability -- arrow keys,
  type-ahead, the OS picker -- for free; a hand-rolled listbox would have
  to re-earn every one of those). New `.quick-sort-select` (replacing
  `.settings-select` for just these two quick instances; the Settings >
  Sessions dialog select is unaffected) uses `appearance: none` to drop
  the browser's native arrow and the width it reserves, and a new
  `.quick-sort-dropdown` wrapper redraws the same `\25BE` caret glyph
  `.view-dropdown__caret` uses via `::after` (a `<select>` can't reliably
  host generated content itself) -- so the two controls read as one
  family, and the option text ("Alphabetical") is no longer squeezed
  inside a fixed-width boxed control.
- **Verified in a real browser, not just against source:** both controls
  now show full-contrast text and a caret at rest (not only on hover);
  hovering/focusing either shows the same border + surface background;
  the sort selection still round-trips live across the sidebar quick-sort,
  the overview header's quick-sort, and Settings > Sessions (all three
  still write/read the same `sort_order` setting via the unchanged
  `syncSortOrderControls()`/`onSortOrderChange()`); both controls remain
  reachable and operable by Tab/Enter/Arrow keys with a visible focus
  ring. The sidebar header stays a **single row** at both a normal desktop
  sidebar width (200px) and the mobile overlay width (240px) -- the
  restyle is narrower than the previous boxed select, not wider, so the
  v0.47.3 vertical-space win is undisturbed; no second row was needed.
  Applied identically to the overview header's matching pair
  (`#view-dropdown-trigger` / `#sort-order-select`), which already used
  `.view-dropdown__trigger` for its view switcher and needed only the
  quick-sort half of this fix.

## v0.47.3 (2026-08-13)

Fixes wasted vertical space in the sidebar session list: device-group
headings carried extra, inconsistent spacing that made the list feel
looser than it needed to be, especially on mobile where every pixel of
scrollable height matters.

### Fixed

- **Sidebar vertical rhythm standardized to a single gap unit.**
  `.sidebar-device-header` is rendered as an `<h4>` (`app.js`), so it
  carried the browser's default `margin: 1.33em 0` on top of
  `.sidebar-list`'s own flex `gap` -- producing ~27px above a device
  heading, ~23px below it, and ~29px of dead space above the very first
  item in the list, none of it intentional. Root cause: the heading was
  the only child of `.sidebar-list` bringing its own vertical
  margin/padding into a flex layout that already owns spacing via `gap`.
  Fixed by making `gap` (now `--sidebar-gap`, defined once on
  `.sidebar-list`) the single source of vertical rhythm: a device heading
  counts as an "item" for spacing purposes, so item-to-item and
  heading-to-first-item both stay at 1x `--sidebar-gap`, while the
  boundary between one device's group and the next is 2x (the heading's
  own `margin-top` reuses the identical `--sidebar-gap` value rather than
  a separate literal). The first heading zeroes that extra margin so the
  list's own top padding is the only inset above it. Vertical padding
  removed from the heading entirely; item height, borders, horizontal
  padding, and typography are unchanged.
  Measured in a real browser against a 3-device/9-session sidebar:
  item-to-item stays 6px; heading-to-first-item drops from ~19px to 6px;
  last-item-to-next-heading drops from ~19px to 12px (exactly double the
  6px unit, as intended); top-of-list drops from ~13px to 0px -- ~90px
  reclaimed for that one realistic list, confirmed identical at a mobile
  (375px-equivalent) width since no responsive breakpoint touches these
  rules. Regression coverage added in `tests/test_frontend_css.py`.

## v0.47.2 (2026-08-13)

Fixes a mobile-only terminal rendering regression from v0.44.0: scrolling
could duplicate lines (a variable count, sometimes a dozen) or freeze a
region of the pane while everything else kept scrolling behind it, far
more often on mobile than desktop and worse on slower devices/networks.

### Fixed

- **Terminal content duplication/freezing while scrolling on mobile.**
  Root cause: `initVisualViewport()`'s `_vpHandler` (`terminal.js`, added in
  v0.44.0 / `b7186b0`) ran an unconditional, undebounced `_termRefit()` on
  *every* `visualViewport` `scroll` **and** `resize` event, deliberately
  bypassing the existing 50ms-debounced `ResizeObserver` to avoid a
  one-frame lag while an on-screen keyboard animates. `scroll` fires far
  more often on mobile than the viewport genuinely changes height (touch
  scrolling, the browser's own dynamic toolbar hide/show, and ordinary
  content panning while a keyboard is already open) -- each of those calls
  could reach `FitAddon.fit()` -> `term.resize()` -> a PTY resize dispatched
  to the server, and each PTY resize makes tmux redraw its entire pane via
  a fresh `SIGWINCH`. A burst of these (routine during a single mobile
  scroll gesture) sends overlapping full-pane redraws addressed for
  whatever size tmux believed was current at each moment; if the client's
  actual size has since moved on, a redraw lands against a differently
  sized buffer, corrupting the visible terminal -- confirmed directly
  against a real xterm.js `Terminal` buffer (not just a screenshot) under a
  synthetic resize-storm harness, which is a genuine **data** corruption
  in the terminal's own scrollback, not a rendering/paint artifact.
  Fixed with two complementary changes in `terminal.js`:
  - `_vpHandler` now skips entirely (no CSS write, no refit) when
    `visualViewport.height` hasn't actually changed since the last
    applied value -- eliminates the large majority of `scroll` events,
    which fire for reasons other than a genuine height change.
  - `connectWebSocket()`'s `_term.onResize` now throttles the
    server-bound PTY resize dispatch (leading edge instant, ~50ms
    trailing coalesce during a burst) so a rapid, genuinely-changing
    sequence of heights -- a real keyboard/toolbar animation -- converges
    to a bounded resize rate instead of one dispatch per raw browser
    event. The terminal's own local reflow (`term.resize()` itself)
    remains fully immediate; only the network-bound dispatch is throttled,
    so there is no added visible lag for the keyboard-animation case the
    original bypass was protecting.
  - New regression coverage in `tests/test_terminal.mjs` exercises both
    the height-unchanged no-op and the resize-dispatch throttle directly.

## v0.47.1 (2026-08-12)

Dependency-only release: bumps the `tmux-kit` pin from `0.3.2` to `0.3.5`
(now published on PyPI, verified: wheel + sdist present, clean install
imports). Nothing in muxplex's own behavior changes -- this release exists
so muxplex picks up a data-loss fix in tmux-kit's presence tracking.

### Fixed

- **Previously-frozen `pending_restore` entries now survive a second cold
  start.** `tmux_kit.presence.update_manifest()`'s cold-start branch used
  to replace `pending_restore` wholesale, so a SECOND tmux server death
  before an operator finished restoring from the first silently discarded
  any entries the first cold start had recorded but the operator had not
  yet acted on. This fired twice against a real muxplex host on
  2026-08-12: 20 entries lost on the first double-death, 4 more on the
  second. muxplex surfaces `pending_restore` through `muxplex restore`, so
  muxplex users get the fix -- unrecovered sessions from an earlier crash
  are no longer silently dropped by a later one -- only via this pin
  bump; muxplex's own code neither reproduced nor patched the bug
  directly.

### Changed

- **`tmux-kit==0.3.2` -> `tmux-kit==0.3.5`** in `[project.dependencies]`,
  and the matching `[tool.uv.sources]` git entry's `tag` moves
  `v0.3.2` -> `v0.3.5` in the same commit (AGENTS.md's "tmux-kit pin/tag
  agreement" rule). `uv.lock` is regenerated in this same commit so a
  git-sourced install resolves the new tag rather than the stale one.
- Version bump: `pyproject.toml` + `client/pyproject.toml`, 0.47.0 ->
  0.47.1 (patch: consuming code is unchanged, only the vendored fix
  moves).

## v0.47.0 (2026-08-09)

Merges the hover-preview popover's two independent off-switches into one
control. `hoverPreviewDelay`'s "Off" option and a separate
`showHoverPreview` checkbox each alone suppressed the popover -- both
worked, but the duplication made the disable option hard to find.

### Changed

- **Settings > Display: "Hover preview" replaces "Hover Delay" + "Show
  hover preview".** One `<select>` now controls both timing and on/off:
  `Off` / `After 1s` / `After 1.5s` / `After 2s` / `After 3s`. Default
  stays 1.5s, unchanged for everyone. `showHoverPreview` is retired from
  `DEFAULT_SETTINGS` and `SYNCABLE_KEYS`.

### Fixed

- **Migration prevents a silent regression for anyone who had disabled
  the preview via the retired checkbox.** A `settings.json` (or an old
  client's PATCH/federation-sync payload) carrying `showHoverPreview:
  false` is translated to `hoverPreviewDelay: 0` and persisted -- not
  dropped on the floor now that the key is gone from the schema. An old
  client sending `showHoverPreview: true` is treated as a no-op (it
  requests no specific delay); an explicit `hoverPreviewDelay` in the
  same payload always wins over a translated legacy value.

## v0.46.1 (2026-08-08)

Fixes the terminal reconnect loop dying silently after a Mac wakes from
sleep, which forced a manual Cmd-R every time (hit daily). Two related
failures in `frontend/terminal.js`'s reconnect path, both introduced by
the per-session-ttyd rework:

### Fixed

- **A rejected `/connect`-escalation fetch dead-ended the retry loop
  forever.** `connect()`'s escalation path (fired after 2 failed WS
  attempts) mapped a genuine network rejection -- `fetch()` rejects only
  on `ERR_NETWORK_CHANGED`/DNS/TLS, never on an HTTP error status -- to a
  resolved `null` via a `.catch()` sitting *between* the chain's two
  `.then()`s. No WebSocket was ever created after that, so no `close`
  event could fire, and every other retry in the file is scheduled
  exclusively from a WebSocket's own `close` handler. The chain died
  permanently and silently, right in the wake-from-sleep window where
  Wi-Fi re-association / DHCP renewal is still in flight when the fetch
  fires. Fixed by moving the `.catch()` to the end of the chain (so it
  never intercepts the 409 branch's intentional "stop, don't retry" path)
  and having it schedule the next retry the same way a closed WebSocket
  does.
- **A `4404` WebSocket close retried forever with the same stale
  `device_id`, never healing.** `terminal_ws_proxy` (`main.py`) closes
  with `4404` for an unknown `device_id` or unknown target session --
  `prune_devices(ttl_seconds=300.0)` forgets a device after a routine
  multi-minute sleep. The close handler only special-cased `4409`
  (session-desync conflict), so a `4404` fell through to the generic
  retry path and kept reconnecting with the now-unknown `device_id`
  forever. Fixed by recognizing `4404` and re-registering via
  `sendHeartbeat()` -- the same self-heal `app.js` already applies to its
  own `/api/state` 404s (see `pollActiveState()`/`restoreState()`) --
  before falling through to the normal backoff retry, instead of
  inventing a parallel recovery mechanism.
- Factored the overlay-then-backoff-then-`setTimeout(connect, delay)`
  sequence into a single `_scheduleReconnectRetry()` helper, called by
  both the WebSocket `close` handler and the escalation fetch's
  `.catch()` -- a duplicated copy is exactly what let the two paths
  silently diverge in the first place.

### Testing

`frontend/tests/test_terminal.mjs` gained 4 tests covering both paths
(a test that would have caught the original dead-end: it asserts a
reconnect retry is scheduled after a rejected escalation fetch). Verified
live in a real Chromium browser (Playwright) against a scratch muxplex
instance: forced the escalation fetch to fail via a network-level abort
(`reconnectAttempts` kept climbing 1->5 across repeated `/connect`
attempts, never stuck) and forced a real `WebSocket.close(4404, ...)`
from the page (`reconnectAttempts` kept climbing, a new
`POST /api/heartbeat` fired immediately, no terminal-conflict overlay
shown).

- Version bump: `pyproject.toml` + `client/pyproject.toml`, 0.46.0 -> 0.46.1.

## v0.46.0 (2026-08-08)

Dependency-only release: bumps the `tmux-kit` pin from `0.1.0` to `0.3.2`
(now published on PyPI, verified: wheel + sdist present, clean install
imports, both `tmux-kit` and `tmux-kit-mcp` console scripts register).
Nothing in muxplex's own behavior changes -- this release exists so
muxplex picks up what tmux-kit gained since its first PyPI release.

### Changed

- **`tmux-kit==0.1.0` -> `tmux-kit==0.3.2`** in `[project.dependencies]`,
  and the matching `[tool.uv.sources]` git entry's `tag` moves
  `v0.1.0` -> `v0.3.2` in the same commit (AGENTS.md's "tmux-kit pin/tag
  agreement" rule). `uv.lock` is regenerated in this same commit so a
  git-sourced install resolves the new tag rather than the stale one.
- Version bump: `pyproject.toml` + `client/pyproject.toml`, 0.45.1 ->
  0.46.0 (minor: the dependency it wraps grew real new surface area, even
  though muxplex's own code is untouched).

### What tmux-kit 0.3.2 brings (upstream, not consumed by muxplex yet)

Since 0.1.0, tmux-kit gained: a high-level facade
(`tmux_kit.start/read/list_sessions/...`), a Click CLI, an MCP server
exposing the identical verb vocabulary, `isolated_tmux_server()` (an
isolation primitive for tests/tools), a deny-by-default authorization
fence in front of the MCP server's destructive verbs, an `exit_code`
field, and a CI rail that fails the build if any test/example/script
spawns tmux without an explicit `-L`/`-S`. muxplex continues to use only
the same core verbs it already depended on at 0.1.0; none of this new
surface is wired into muxplex's own code by this release.

## v0.45.1 (2026-08-08)

Managed-device (CISO) installs of muxplex no longer require a hand-typed
`--with 'tmux-kit @ git+...'` override to get tmux-kit from git. Behavior
for PyPI users is unchanged -- this release only changes how a
`git+https://...muxplex@vX` install resolves its `tmux-kit` dependency.

### Changed

- **`pyproject.toml` gains a committed `[tool.uv.sources]` git entry for
  tmux-kit, pinned to the tag matching the `==` dependency pin.** v0.45.0's
  design reasoned that a project-level source was both pointless (never
  enters wheel metadata) and possibly unsafe (unproven whether a git tool
  install honors it) -- reasoning that was half right. Building a real
  wheel from this release confirms `Requires-Dist: tmux-kit==0.1.0` stays a
  plain pin with no git URL leak; running a real
  `uv tool install git+file://...` confirms uv DOES resolve tmux-kit from
  the git source (verified via `tmux_kit-*.dist-info/direct_url.json`
  showing `vcs_info` with the resolved commit) -- **once `uv.lock` has been
  regenerated to record it.** That caveat is the empirical surprise: a git
  tool install resolves from whatever `uv.lock` already says, not a fresh
  ad-hoc re-resolve, so the fix is only real because `uv lock` is re-run in
  this same release.
- **CI's `guard-no-tmux-kit-source-override` job is converted, not
  deleted, to `guard-tmux-kit-pin-source-agreement`.** The old guard
  forbade any committed source entry outright, encoding the now-disproven
  assumption above. The underlying concern -- PyPI installs and git
  installs of the same muxplex release silently resolving DIFFERENT
  tmux-kit versions -- is still completely real and becomes the new
  invariant: the `tmux-kit==X.Y.Z` pin and the source `tag = "vX.Y.Z"` must
  name the same version. The job still rejects a `path` source (the
  cross-repo dev loop's temporary override) as a local-dev leak.
- **New test, same invariant, checked in `make test` too.**
  `muxplex/tests/test_tmux_kit_pin_source_agreement.py` asserts the
  identical pin/tag agreement the CI guard checks, so drift fails the
  local suite as well as CI.
- **`AGENTS.md` (both repos) documents the release-time rule.** muxplex's
  `AGENTS.md` gets a new "tmux-kit pin/tag agreement" section covering why
  the pin and tag must be bumped together, and the `uv lock`
  re-generation requirement. `bkrabach/tmux-kit` (previously README-only,
  no `AGENTS.md`) gets one covering its stdlib-only contract
  (`dependencies = []`), its two safety rails (never-render, import-
  purity), the positive-presence-record rule, incident-test handling, and
  the coordinated two-repo release shape.
- Version bump: `pyproject.toml` + `client/pyproject.toml`, 0.45.0 ->
  0.45.1 (patch: no user-visible behavior change for PyPI installs; only
  how a git install resolves a dependency).

## v0.45.0 (2026-08-08)

The tmux session-management core moved OUT of this repo entirely: it is now
`tmux-kit` (import `tmux_kit`), published independently on PyPI at its own
`bkrabach/tmux-kit` repo. This release cuts muxplex over to depend on the
PyPI-published package instead of the in-repo `lib/` workspace member v0.44.0
introduced, which **restores public `uv tool install muxplex`** -- broken
since v0.44.0's wheel pinned a dependency (`tmuxkit`) that could never
resolve from PyPI (see v0.44.0's own entry below, and
`docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md` for the full design
and its honest cost accounting).

### Changed

- **`lib/` is deleted from this repo.** The tmux session-management core
  (`proc`, `spawn`, `names`, `observe`, `presence`, `bell`, `keys`, `cgroup`)
  now lives at `bkrabach/tmux-kit`, published to PyPI as `tmux-kit`
  (`import tmux_kit`). The code itself is unchanged in behavior -- it was
  renamed (`tmuxkit` -> `tmux-kit`/`tmux_kit`, a prior commit on this branch)
  and moved via `git subtree split`, verified tree-identical modulo three
  enumerated deltas (flattened layout, version, repo URLs) -- not
  reimplemented. `lib/`'s history is not lost: it remains in this repo's git
  history through the tag `v0.44.0` and every commit before this one.
- **`pyproject.toml`: `tmux-kit` is now a plain PyPI version pin, not a
  workspace source.** `[tool.uv.workspace] members` drops `"lib"` (down to
  `["client"]`); `[tool.uv.sources]` no longer has a `tmux-kit` entry at
  all -- deliberately no replacement `{ git = ... }` or `{ path = ... }`
  entry either. `[project.dependencies]`'s `tmux-kit==0.44.0` (a workspace
  pin that could never resolve from PyPI) becomes `tmux-kit==0.1.0` (tmux-kit's
  own first PyPI release; see that project's own version rationale --
  0.44.0 continuity was never earned by a name with no prior PyPI history).
  This is the plain-`==`-pin shape the design's SS0.3/SS2.1 findings settled
  on: `[tool.uv.sources]` never enters wheel metadata regardless of what it
  says, so a project-level git/path source would be a lie the published
  wheel can't tell; the managed-device (CISO, no-pypi.org) path that needs a
  git-sourced tmux-kit instead uses an install-TIME `--with 'tmux-kit @
  git+https://github.com/bkrabach/tmux-kit@vX.Y.Z'` override, never a
  committed source. `uv lock` re-resolved tmux-kit as a PyPI registry
  dependency (`source = { registry = "https://pypi.org/simple" }` in
  `uv.lock`, confirmed by inspection -- not a workspace/path source).
- **CI gains a guard against ever re-committing a source override.** The
  cross-repo dev loop for coupled tmux-kit + muxplex changes (`uv add
  --editable ../tmux-kit`, develop, then revert before committing) has an
  obvious failure mode -- forgetting the revert. A new CI job asserts the
  committed `pyproject.toml` carries a plain `tmux-kit==X.Y.Z` pin and no
  `[tool.uv.sources]` entry for it, failing loud at PR time instead of
  silently shipping a moving-target wheel.
- **`publish.yml`'s existing preflight now actually exercises the fix.**
  The wheel-resolution preflight this workflow already carries (added in
  response to the v0.44.0 incident it documents inline) previously failed
  correctly, every time, because `tmux-kit` did not exist on PyPI yet. With
  tmux-kit 0.1.0 published, this same preflight now PASSES against the real
  index -- the fail-to-pass flip is the proof this cutover actually
  resolved the incident, not merely moved it (see this release's PR
  description for the before/after preflight output).
- **Two safety rails retire, one tightens, because their scan target no
  longer exists in this repo.** `test_tmux_library_never_imports_the_app_layer`
  (the import-purity rail) and its AST scan of `lib/tmux_kit/` retire --
  the identical rail travelled with the code and lives on, unweakened, in
  `bkrabach/tmux-kit`'s own suite
  (`tests/test_rails.py::test_library_is_import_pure_stdlib_and_self_only`).
  `test_library_tests_live_under_the_railed_tests_dir` retires the same way
  (by replacement, not silent deletion -- it now asserts `lib/` stays gone
  and that `test_tmux_kit_contract.py` exists as this repo's replacement
  tripwire). The never-render rail **tightens**: with the library's own
  `build_alert_bell_hook()` call site no longer in this repo at all, `muxplex/`
  is now held to **zero** `run-shell` construction sites, full stop (it was
  already zero for app code; the change is that the scan no longer carves
  out an exception for a `lib/tmux_kit/bell.py` that isn't here anymore).
- **The differential harness (`test_differential_harness.py`) and its
  recorded fixtures leave this repo.** It tested `tmux-kit`'s own internals
  (`enumerate_sessions` parsing tolerances, `update_manifest` rebuild
  branches, bell detection, tmux-env injection, argv construction) via this
  repo's re-export shims -- coverage that is now a byte-identical duplicate
  of `bkrabach/tmux-kit`'s own differential harness, which tests the same
  functions directly against the real library source it moved with. The
  three ttyd-specific cases the old harness also carried (socket naming,
  liveness, `validate_socket_dir`) test muxplex's OWN code (`ttyd.py`,
  which stays muxplex-private) but are not stranded: `test_ttyd.py` already
  covers each of those properties directly, without the now-foreign
  recorded fixture. Two purely-duplicate test files
  (`test_cgroup_escape.py`, `test_lib_import_smoke.py` -- byte-identical,
  diffed, to their `bkrabach/tmux-kit` counterparts) are removed for the
  same reason.

### Added

- **`test_tmux_kit_contract.py` -- the cross-repo drift tripwire.** Modeled
  directly on `test_client_contract.py`'s rationale: with tmux-kit released
  independently now (a real, priced cost -- see the design's §9), nothing
  else catches a pin bump that silently drifts muxplex's assumptions away
  from what the installed package provides. Runs against the INSTALLED
  `tmux-kit` at the pinned version and asserts: mirrored constants
  (`DEFAULT_CAPTURE_LINES`, `MAX_CAPTURE_LINES`, `ALLOWED_KEYS`, `MAX_KEYS`)
  match between muxplex's re-export shims and the library directly;
  `build_alert_bell_hook()`'s signature carries no loudness-shaped
  parameter (the never-render rail's structural guarantee, checkable from
  outside the library); importing `tmux_kit` in a fresh interpreter drags
  in no fastapi/uvicorn/starlette/httpx/pam/muxplex; and an unknown
  top-level manifest key still round-trips verbatim through
  `tmux_kit.presence.update_manifest()` (the v0.44.0 S4 contract, now
  pinned against the installed package rather than local source).
- **`muxplex doctor`/`muxplex upgrade` tmux-kit source-awareness** (landed
  in this branch's rename commits, ahead of this release): `doctor` reports
  tmux-kit's own installed version and install source (PyPI vs git)
  alongside muxplex's, and warns if the installed version drifts from
  muxplex's own pin; `upgrade` reconstructs a git-sourced `--with
  'tmux-kit @ git+...'` override at upgrade time from the target muxplex's
  own pin, rather than silently dropping it to a bare-name reinstall --
  the exact managed-device (CISO) failure mode this release's design
  document prices and proves against.

### Verification

- `make test` (DTU `muxplex-test`, against `git archive HEAD` of the
  committed, bumped tree) -- see this release's PR/commit for the exact
  pass count.
- The client-parity test, re-run by name after the bump:
  `test_client_contract.py::test_client_version_matches_server_version`.
- `pytest -m integration` (real tmux + real ttyd, inside the DTU).
- `node --test tests/*.mjs` in `muxplex/frontend`, inside the DTU.
- `publish.yml`'s preflight (`uv pip install --dry-run dist/muxplex-*.whl`
  against the real PyPI index), run locally against the freshly built
  wheel: FAILS on the pre-cutover tree (tmux-kit==0.44.0 unresolvable),
  PASSES on this release's tree (tmux-kit==0.1.0 resolves from PyPI) -- the
  fail-to-pass flip is the structural proof this cutover works, not an
  assertion.

## v0.44.0 (2026-08-08)

Internal refactor: the tmux session-management core moves into `tmuxkit`, a
new, separately-versioned uv workspace library beside `client/`. `/api/*` is
byte-identical throughout -- `test_client_contract.py` stays green across
every stage -- but this release changes muxplex's **own install contract**,
which is why it is documented here rather than treated as invisible
housekeeping.

### Changed

- **The tmux session-management core (`proc`, `spawn`, `names`, `observe`
  [capture/enumeration/epoch], `presence`, `bell` detection, `keys`, `cgroup`)
  is now `tmuxkit`, a stdlib-only workspace-member library at `lib/`, beside
  `client/`.** `docs/plans/2026-08-08-tmux-lib-extraction-plan.md` SS7,
  SS13.2 stages 0-3.5. muxplex runs entirely on it: `muxplex/sessions.py`,
  `bells.py`, `manifest.py`, `cgroup_escape.py`, and `terminal_input.py` are
  now thin **re-export shims** at the old import paths (`from tmuxkit.proc
  import ...` etc.) -- no app code outside those five shim modules changed,
  and no caller anywhere had to change an import. The move was staged behind
  a **differential harness** recording real tmux argv/stdout and real
  `update_manifest()` inputs from a live poll cycle, replayed at every stage
  to prove the moved code byte-identical to the code it replaced before it
  shipped -- not asserted after the fact.
  - `spawn_session()` (new, `tmuxkit/spawn.py`) is the general half of
    session creation -- cgroup escape, the TTY-attach
    exists-despite-nonzero-exit tolerance, the 30s wait -- with the session
    **template caller-resolved**: the library never reads which command a
    session should run. `sessions.py`'s `spawn_session_command()` resolves
    the template (`session_commands` / `new_session_template`, muxplex
    settings) and calls the library with it.
  - **The settings dependency is inverted, not merely re-exported.**
    Pre-move, `sessions.py:294`'s `tmux_env()` called `load_settings()`
    directly -- the one wrong-way import arrow into what is now library
    code. `tmuxkit.proc.tmux_env(socket_dir)` is now a pure function of an
    injected parameter; `run_tmux()` takes an injected env, defaulting to a
    process-wide factory the app installs once via `set_env_factory()`.
    Nothing under `lib/tmuxkit/` reads muxplex's settings file, its state
    directory, or any other muxplex module -- enforced structurally, not by
    review: `test_tmux_library_never_imports_the_app_layer`
    (`test_safety_rails.py`) AST-scans every file under `lib/tmuxkit/` and
    fails on any `muxplex.*` import, any `import muxplex`, or any relative
    import that climbs out of the package.
  - **`lib/tmuxkit`'s own dependency list is empty, on purpose, and a test
    pins it**: `test_lib_distribution_declares_zero_dependencies`
    (`test_lib_import_smoke.py`). A second application can depend on it
    without dragging in fastapi, uvicorn, python-pam, or httpx --
    `test_public_surface_imports_without_the_muxplex_server` proves the
    published surface imports cleanly from a fresh interpreter, from a
    neutral cwd, with none of `muxplex`/`fastapi`/`pam` in `sys.modules`
    afterward -- the same "importable by someone else" proof
    `client/`'s no-server-dependency note already established for the HTTP
    client, now proven for the tmux core too.
  - **`ttyd.py` (the per-session AF_UNIX terminal-server lifecycle,
    including the `SOCKET_SUFFIX` fence) and the `Sender`/`SendPolicy` typed
    send API stay muxplex-private in this release.** The extraction plan's
    original SS7.1 sketch named both as in-scope for the pure-move stage;
    the plan's own addendum (SS13.2's revised stage table, SS15.3, SS16)
    supersedes that sketch and this release follows the addendum: `ttyd.py`
    still imports app-side `STATE_DIR` and is judged "second tranche,"
    shaped by a second app's own embedded-terminal design rather than
    muxplex's; `Sender`/`SendPolicy` are listed in the plan's SS15.1 public
    surface as the *target* shape but "it does not exist yet; building it is
    not a pure move" (`lib/tmuxkit/__init__.py`'s own docstring) -- both are
    deliberately deferred until a second consumer's real requirements settle
    the interface (SS15.3), not built speculatively from one consumer's
    guess.

- **muxplex's own PyPI distribution is no longer installable from PyPI
  directly -- only through this repo (git or a local path).** muxplex's
  wheel now declares a hard runtime dependency on `tmuxkit==0.44.0`
  (`pyproject.toml`'s `[project.dependencies]`), resolved via
  `[tool.uv.sources]` as a uv **workspace** source
  (`[tool.uv.workspace] members = ["client", "lib"]`) -- and `tmuxkit` is
  deliberately never uploaded to PyPI (see below), so PyPI has no way to
  satisfy that pin. `uv tool install git+https://github.com/bkrabach/muxplex@v0.44.0`
  (or an editable/path install from this repo) resolves `tmuxkit` from the
  same checkout via the workspace and succeeds; a bare `pip install muxplex`
  against PyPI fails at dependency resolution with "no matching distribution
  found for tmuxkit" -- loud, not a broken install that silently omits tmux
  support. This is intended, not a regression: the library rides muxplex's
  existing git-based rollout rather than getting a rollout of its own, and
  the fleet already installs from git, never from PyPI (`AGENTS.md`).
  `.github/workflows/publish.yml`'s build step is explicit `--package
  muxplex` + `--package muxplex-client` (not `--all-packages`) specifically
  so `tmuxkit`'s files are never produced in `dist/` for the publish step to
  upload -- a PyPI release cannot be unpublished, so this is enforced at
  build time, not left to the publish step's discretion.

- **Two safety rails that scan the whole codebase for exactly one
  construction site now cover `lib/tmuxkit/` as well as `muxplex/`, in the
  same commit the code moved (`docs/plans/...` SS7.3).**
  `test_no_diagnostic_tmux_run_shell_construction_exists` previously globbed
  `muxplex/*.py` non-recursively; it now scans both `muxplex/` and
  `lib/tmuxkit/` from the repo root (asserting each root exists first) and
  still asserts exactly **one** `run-shell` string-construction site exists
  anywhere in production code (`lib/tmuxkit/bell.py`'s
  `build_alert_bell_hook`) -- app-level code is held to **zero**. Without
  this fix, code moved into the library would have silently left the rail's
  coverage, and a future diagnostic `run-shell` call added inside
  `lib/tmuxkit/` could reach a live pane undetected. A third rail,
  `test_library_tests_live_under_the_railed_tests_dir`, pins that the
  library's own tests stay inside `muxplex/tests/`, under that suite's
  autouse isolation fixtures (isolated `SETTINGS_PATH`, isolated
  `TMUX_TMPDIR`, neutralized port killer) -- a library test suite living
  anywhere else would run unrailed against whatever tmux server and config
  path happen to be ambient.

- **The differential harness is retained, not a one-time migration tool.**
  `pytest -m differential` (`muxplex/tests/test_differential_harness.py`)
  stays in the suite as `tmuxkit`'s permanent regression bed for the
  presence rule, `enumerate_sessions()`'s parsing tolerances, bell
  detection, and the tmux-env/argv-injection seam S2 inverted -- replayed
  against recorded real tmux argv/stdout and real `update_manifest()` inputs
  captured from a live host, not synthetic fixtures.

### Added

- **`update_manifest()` now round-trips unknown top-level manifest keys
  verbatim -- the one behavior change in this release, and it is additive.**
  `docs/plans/...` SS13.3. Previously false: all three of the function's
  rebuild branches (first-run adoption, same-epoch, cold-start) rebuilt the
  top-level manifest dict from a **closed** key set, so any app-owned
  top-level key not in that set was silently dropped on any cycle that
  changed anything. In muxplex today this was unreachable for
  `rename_in_flight` only by a call-order accident (the poll cycle reads and
  clears the rename journal *before* calling `update_manifest()`) -- not a
  contract. Each of the three rebuild sites in `lib/tmuxkit/presence.py` now
  spreads the incoming manifest first (`{**manifest, ...}`) and overwrites
  exactly the keys the function owns, computed exactly as before --
  known-key behavior (discrimination, tombstoning, `pending_restore`
  freezing) is byte-identical, proven by the differential harness re-run
  against the new contract. This is what makes it safe for a second,
  future application to write its own top-level keys beside the library's
  core keys in the same per-app manifest file **without splitting it**
  (the original plan's riskiest stage is dissolved by this contract instead
  of attempted). Covered by ten `test_s4_*` tests in `test_manifest.py`
  (every writer and rebuild branch, with an app-owned key no muxplex code
  knows about) and by
  `test_differential_harness.py::test_s4_unknown_toplevel_keys_round_trip_verbatim`
  against real recorded manifest cycles.

### Deferred (recorded, not dropped)

- **`ttyd.py`'s AF_UNIX lifecycle (including the `SOCKET_SUFFIX` fence) and
  the `Sender`/`SendPolicy` typed send API** -- both named in the
  extraction plan's public-surface sketch (SS15.1) as the eventual shape,
  neither built in this release. See the Changed entry above for why.
- **Publishing `tmuxkit` to PyPI, or promising it semver stability.** The
  library holds at 0.x with no semver promise -- its public surface is
  deliberately smaller than SS15.1's eventual sketch until a second,
  independent application actually depends on it and its interface
  decisions (error model, bell-hook coexistence, observation scoping) get a
  second vote instead of being settled unilaterally from muxplex's own
  usage. Nothing in this plan builds that second application; it builds
  only the library it will import.

### Verification

- **Both suites were run AFTER the four-way version bump to `0.44.0`
  (`pyproject.toml`, `client/pyproject.toml`, `lib/pyproject.toml`, and the
  `tmuxkit==0.44.0` pin inside `pyproject.toml`'s own dependency list), per
  the v0.31.1 incident.** `uv lock` was re-run against the bumped tree
  (`Updated muxplex v0.43.0 -> v0.44.0`, `Updated muxplex-client v0.43.0 ->
  v0.44.0`, `Updated tmuxkit v0.43.0 -> v0.44.0`), and `uv lock --check` /
  `uv sync` were both re-run afterward to prove the **workspace itself**
  resolves post-bump now that a third member is in the mix -- new this
  release, since a workspace source is a new failure mode the client-only
  precedent never had.
- `make test` (DTU `muxplex-test`, never the host -- run against `git
  archive HEAD` of the bumped, committed tree, so the artifact tested is the
  artifact that ships): **2399 passed, 4 skipped, 52 deselected in 99.95s
  (0:01:39)**.
- The parity test, re-run **by name** inside the same container after
  grepping all four version locations:
  `muxplex/tests/test_client_contract.py::test_client_version_matches_server_version PASSED [100%]`
  / **1 passed in 0.16s**, with `pyproject.toml`, `client/pyproject.toml`,
  and `lib/pyproject.toml` grepped in the same container, all three reading
  `version = "0.44.0"`, and `pyproject.toml`'s own dependency pin reading
  `tmuxkit==0.44.0`.
- `pytest -m integration` (real tmux 3.4 + real ttyd, inside the DTU): **50
  passed, 2403 deselected, 2 xpassed, 2 warnings in 35.30s**. This suite now
  runs at all because of this range's three CI/test-repair commits (see the
  release commit message) -- it was rotted and never executed before them.
- `pytest -m differential` (the tmux-lib extraction's own permanent
  regression bed, `test_differential_harness.py`): **27 passed, 2428
  deselected in 0.47s**, including
  `test_s4_unknown_toplevel_keys_round_trip_verbatim` (the one behavior
  change in this release) and every recorded-real-input replay for
  `update_manifest`, `enumerate_sessions`, `probe_tmux_epoch`,
  `capture_pane*`, `poll_bell_flag`, `tmux_env`, and the `keys`/ttyd-naming
  fixtures.
- `node --test tests/*.mjs` in `muxplex/frontend`: **920 tests, 920 pass, 0
  fail, 0 skipped, duration 3143.7ms**. Byte-identical count to v0.43.0's
  920 -- expected: `git diff --stat v0.43.0..HEAD -- muxplex/frontend/` is
  empty, this range touches no frontend file. Run and quoted as a
  regression gate, not because this release changed `app.js`.
- **Workspace resolution, proved post-bump before the suites above ran**
  (new this release -- a workspace member is a failure mode the
  client/-only precedent never had): `uv lock --check` -> `Resolved 43
  packages in 1ms` (no drift from the committed `uv.lock`); `uv sync
  --extra dev` -> uninstalled `muxplex==0.43.0` / `muxplex-client==0.43.0`
  / `tmuxkit==0.43.0` and installed `muxplex==0.44.0` /
  `muxplex-client==0.44.0` / `tmuxkit==0.44.0` in their place, confirming
  `tmuxkit` resolves as a workspace source at the bumped version rather
  than failing to resolve or silently pinning stale.

## v0.43.0 (2026-08-08)

### Added

- **`POST /api/sessions/{name}/rename` -- session rename, implemented as an eleven-keyspace migration with a write-ahead journal, not a one-line wrapper around `tmux rename-session`.** `docs/plans/2026-08-07-session-rename-plan.md`. The brief named four keyspaces; the real count is eleven across four persistence layers, two in-memory modules, and one process registry -- three deliberately left untouched, and two of those three would be a security regression if migrated.
  - **The journal is load-bearing, not belt-and-braces.** `tmux rename-session` is a subprocess run outside `state_lock` (this codebase's established discipline), and the poll cycle's own settings/pruning writes run outside it too -- a rename racing a ~2s poll cycle *will* interleave. `manifest.json` gains `rename_in_flight: {from, to, at}`, written fsync'd via the existing `save_manifest()` *before* anything else changes (`manifest.start_rename_journal()`/`clear_rename_journal()`). The poll cycle honors it at step 1c, before `update_manifest()`: completes the migration (same idempotent function the endpoint calls) when tmux confirms the new name is live and the old is gone, reverts (clears the journal, migrates nothing) when the rename never happened, and clears it without migrating when the session died mid-rename.
  - **Rename is the fourth caller of `terminal_input.input_allowed_for_session()`, evaluated against BOTH the old and new name, for `bearer_only` callers only.** Every glob-based fence in muxplex keys on the session name; unfenced, rename would let a Bearer-key holder retarget `input_allowed_sessions` by renaming `production-db` -> an allowlisted name and typing into it -- a fourth door into the RCE surface `AGENTS.md` already documents three doors into. Old-name-required proves the caller already held typing authority; new-name-required blocks renaming *into* a family to acquire it. localhost and a cookie-authenticated caller (including an HTTP Basic caller) are unfenced, matching the terminal WS's existing `bearer_only` classification precedent -- gating an operator's own rename on `input_enabled` would be backwards.
  - **`.` is rejected outright, not predicted.** tmux 3.4 silently mangles `.` to `_` in session names (verified live) while `SESSION_NAME_RE` permits it; `sessions.is_tmux_stable_name()` rejects any `new_name` tmux could mangle, returning `400 invalid_session_name` with a `suggested` name, rather than modeling the substitution and risking a silently mis-keyed session. Success always re-enumerates and verifies the observed name regardless (`500 rename_verification_failed` if tmux's rc=0 still doesn't match) -- tmux's exit code alone is never trusted.
  - **`input_allowed_sessions` and `views[*].match_names` are the two keyspaces this migration deliberately never touches.** Migrating the allowlist would be the privilege escalation the fence above closes from the other side; rewriting a view's glob rules would violate `AGENTS.md`'s standing prohibition on materializing a rule match back into `sessions` -- a renamed session correctly leaving one rule-matched view and joining another *is* the auto-views feature working.
  - **Restore gains a new hazard the migration itself introduces, and a new refusal for it.** Renaming a tmux session moves nothing on disk, so a session restored under its *new* name via a `{name}`-templated session command (e.g. `amplifier-workspace {name}` -> `~/dev/{name}`) would recreate it rooted in a directory that never existed -- the 2026-08-05 incident, reintroduced by the very migration meant to prevent data loss. `manifest["sessions"][name]["renamed_from"]` records the prior name and freezes into `pending_restore` verbatim; `restore.execute_restore()` refuses (not warns) on it regardless of whether the session has a resolvable command pair, bypassed only by `--force` (which now covers this refusal in addition to its pre-existing staleness gate).
  - **`muxplex_client` gains `rename_session(name, new_name) -> RenameResult` on both the sync and async clients**, plus the `RenameResult` model and `parse_rename_result`. `RenameResult.name` is the name tmux actually has afterward -- never the requested name echoed back.
  - **Endpoint response**: `{ok, from, name, renamed?, migrated: {bell, followups, view_pins, hidden, created_with, order, manifest, pruning}}` -- per-keyspace evidence, not a boolean, so a caller (and a test) can see exactly what moved. `renamed: false` only for the no-op case (`new_name == name`), which migrates nothing and never calls tmux at all.
  - **A pre-existing defect this work surfaces but does not fix**: `POST /api/sessions {"name": "build.js"}` already silently creates `build_js` today (the create path re-checks tmux only on a non-zero exit). Real, separate, and a breaking change to the create path -- flagged for a future PR, not bundled here.

- **`bell.source` -- a closed enum on the existing `bell` sub-dict recording WHICH detection path recorded the last bell, and a halted follow-up queue now rings a bell.** Phase 1 of `docs/plans/2026-08-07-bell-causality-plan.md`. The plan's own recommendation, backed by evidence rather than assumed: a `reason` field on the bell (an agent-supplied string explaining *why* a session belled) is explicitly rejected, because the path that produces the overwhelming majority of bells -- tmux's one-byte `\a` -- cannot carry a payload (verified live against real tmux 3.4: pane-scoped format variables resolve the window's *active* pane, not the belling one, and the one side-channel that does work, OSC 2 pane-title smuggling, is sticky and outlives the bell it was meant to describe). Both changes below are pure `state.json` writes -- **nothing reaches a pane**, and `needs_attention()` is unchanged (verified across the full truth table with every enum value injected).
  - **`bell.source` : `"hook" | "poll" | "seeded" | "halt" | None`.** `empty_bell()` (`state.py`) gains the key, defaulted `None` for pre-feature `state.json` entries (no migration -- `null` is a correct answer for an unknown provenance). Each of the four writers stamps its own value in the same update that already sets `last_fired_at`: `receive_bell()` -> `"hook"` (honest about its limit -- it cannot distinguish tmux's own hook from a direct Bearer `POST /bell`, and does not claim to), `bells.process_bell_flags()`'s 0->1 transition -> `"poll"` (a floor, not a count -- the underlying tmux flag is boolean and can stick), `_run_poll_cycle()`'s new-session seed branch -> `"seeded"` (muxplex manufactured this bell; nothing happened in the pane -- the single largest source of false-positive attention today), and the new halt-bell writer below -> `"halt"`. `clear_bell()`/`apply_bell_clear_rule()` leave it untouched, exactly like they already leave `last_fired_at` untouched. **Deliberately never read by `needs_attention()`** -- it is a labeling/triage field for a poller, not part of the attention predicate, and reading it there would be exactly the lifecycle-coupling mistake the plan's §1.3 argues against. Appears for free on `GET /api/sessions`, `GET /api/sessions/{name}`, `GET /api/view`, and `GET /api/federation/sessions` (bells are local-only state; a pre-feature peer's entry simply lacks the key). `muxplex_client.models.Bell` gains `source: str | None = None`, defaulted and last, landing at the same version per the client/server lockstep discipline.
  - **A halted follow-up queue now rings a bell.** `_advance_followup_queue()`'s failure branch (`followups.set_halted()`) previously rang nothing: an agent polling `GET /api/view` could see `followups.halted: true`, but a human staring at the phone grid saw no bell, no tier-1 sort, no amber ring -- muxplex's own autonomous writer failing silently *to the human*, the exact inverse of the gap this feature addresses. `_bell_for_halt()` (`main.py`) writes the bell **directly** inside the same `state_lock` block already open there, deliberately never through `receive_bell()`/`process_bell_flags()` -- routing it through either would make the queue's advance trigger itself, the same structural exclusion `AGENTS.md` already documents for the seeded bell. Cannot loop for two independent reasons, both covered by test: structurally, nothing in `_bell_for_halt()` calls the advance function at all; behaviorally, `followups.acceptance_ok()` returns `False` while a queue is halted, so no further advance can happen until an explicit resume, regardless. Interaction with acknowledgment is intentional, not a bug: viewing the session clears the bell (`unseen_count` -> 0) while `followups.halted` stays set -- the bell means "come look," the halt means "still broken," and the condition stays visible via the `followups: {halted: true}` badge already on `GET /api/sessions`/`GET /api/view` and the PWA's follow-ups panel.
  - **What was deliberately NOT built, and why:** a `reason` field on `POST /api/sessions/{name}/bell` itself. Beyond the payload problem above, adding *any* field to that endpoint's body is a second, independent hazard: every accepted bell already advances the follow-up queue by one item, so a field that makes the endpoint attractive to call (e.g. "POST your bell with a reason so the human knows what you need") would turn a pure-observability feature into a queue-drain trigger -- an agent politely reporting itself every 30 seconds would drain a full 16-item queue in eight minutes, typing operator-authored text into a pane nobody asked it to, and the failure would present as the queue misbehaving rather than as the new field. `POST /bell` gains no request body. A `PUT /api/sessions/{name}/status` surface (closed `working`/`blocked`/`failed`/`done` enum, agent-owned, separate lifecycle from the bell) is fully specified in the plan's §7 but deliberately not scheduled -- its trigger condition is a *second, independent* consumer needing it, which does not exist yet.

- **`GET /api/sessions/{name}?before=<abs>` -- scrollback paging, plus five always-present depth fields, reaching arbitrarily far back into a pane's history at unchanged per-request cost.** `docs/plans/2026-08-07-scrollback-paging-plan.md`, Phase 1. `MAX_CAPTURE_LINES` (2000) was a correctly-sized **window** cap being used as a **depth** cap: there was no way to see scrollback older than the most recent 2000 lines, at all. **Measured, not assumed: `capture-pane`'s cost is O(window requested), not O(depth)** -- a 10-row window 40,000 lines back costs the same as 10 rows at the live end -- which is the entire reason paging needs no quota, no rate limit, and no budget, and why `MAX_CAPTURE_LINES` **stays at 2000**. An agent can already issue 25 requests at `lines=2000` today; paging only makes those 25 requests return 25 *different* pages instead of 25 copies of the same one.
  - **Raw `-S`/`-E` passthrough was tried and proven insufficient -- this is why the server converts coordinates instead of forwarding them.** tmux's own capture coordinates are relative to the current top of the visible screen and drift under a live, growing pane: **identical coordinates 31 lines apart returned different content.** Worse, tmux clamps an out-of-range request **silently -- exit 0, no diagnostic** -- so a caller paging past the beginning gets a plausible page and no signal. One integer conversion the server performs per request fixes both: `abs = history_size + rel`. `?before=<abs>` returns the `lines` rows immediately older than absolute row `abs`; an out-of-range `before` becomes a loud `400` rather than tmux's silent clamp, and `before=0` is a legitimate empty page (`200`), never a `400`.
  - **Five new response keys, always present** (`main.py:1660-1664`): `start` (the next page is always `?before={start}` -- the caller never computes an offset), `row_count`, `total`, `has_more`, and `saturated` (`history_size >= history_limit`). **The `has_more`/`saturated` pair is the part that makes "no silent truncation" true rather than claimed**: it distinguishes *"you have reached the true beginning of this pane"* from *"you have reached the retention wall and there was more, once."* Omitting `before` is **byte-identical** to the pre-paging endpoint.
  - **Two tmux round trips on the `before` path, one on the unchanged path, and the asymmetry is deliberate.** Converting a caller-supplied absolute `before` into tmux's relative coordinates needs a fresh `history_size` *before* the `capture-pane` argv can be built (tmux has no absolute-addressing mode -- confirmed against the tmux 3.4 man page), so a cheap capture-free probe (`capture_pane_metadata`) computes the coordinates and the atomic paired read+capture (`capture_pane_window`, `;`-chaining both tmux commands into one subprocess invocation so they share a command-loop tick) reports from its OWN fresh `history_size` -- truthful regardless of any growth-only drift between the two calls. The `before=None` path needs no probe at all: `-S -{lines}` is independent of `history_size`, exactly as before.
  - **`muxplex_client.SessionSnapshot` gains the five fields, all defaulted**, so a pre-paging server still parses cleanly. The request-side sugar (`session(name, lines=, before=)`, a backward-paging generator) is Phase 3 and deliberately not in this release -- see Deferred below.

- **`cwd` on the wire, and `GET /api/sessions/{name}` brought to full field parity with the bulk read.** Item C of `docs/plans/2026-08-07-agent-surface-additive-plan.md`. The session's active-pane working directory (tmux's own `#{pane_current_path}`, already read every poll cycle by `enumerate_sessions()` for the presence manifest) is published on `GET /api/sessions`, `GET /api/sessions/{name}`, and the local branch of `GET /api/federation/sessions` at **zero new subprocesses** -- `sessions.get_session_cwds()` predates this change; only the wire exposure is new. This is how one agent tells which repo a sibling session is working in.
  - **`cwd` is an OBSERVATION, not an identity, and the docstring says so** (`main.py:1364`): it moves whenever the user or a process in the pane `cd`s, and for a multi-window session it tracks whichever window is currently active. Same always-present / `null`-when-absent convention as `last_activity_at` and `created_at`.
  - **The plan's two open runtime questions were measured against a real host, not reasoned about.** A session whose active pane is running the amplifier TUI reports **the directory the TUI process was launched from** -- stable for the life of that process, since the TUI never `cd`s away itself, and therefore *not* necessarily wherever the TUI's own internal navigation currently is. A session created by `amplifier-workspace` reports the workspace directory across **all four** of its windows (`amplifier`, `shell`, `git`, `files`), which is what makes the guide's "which repo is this session working in" framing hold for the reference workflow.
  - **`GET /api/view` is deliberately excluded, and the exclusion is pinned by a test.** That endpoint is a cheap, frequently-polled display resolution that carries no pane snapshots by design; a working directory is not a display concern. `test_view_does_not_carry_cwd` exists so a future "consistency" PR cannot quietly add it.
  - **Parity is the other half, and it closed a real blind spot**: `GET /api/sessions/{name}` gains `created_at`, `followups`, `views`, and `cwd`. Before this, an agent polling a single session by name **could not see a halted follow-up queue at all** -- the exact silent stall the same release's AGENT_GUIDE work teaches agents to watch for.

- **`muxplex_client` closes its typed-coverage gaps: `command_id` on create, `force` on delete, and the entire follow-up-queue subsystem.** Item D of the same plan. Client-side only -- **no wire contract change**; every endpoint below already existed and was reachable only by hand-rolling HTTP.
  - `create_session(command_id=None)` selects a configured session-command pair. **`None` omits the key entirely, so a pre-feature request stays byte-identical** -- asserted, not assumed. `list_session_commands() -> SessionCommands` (`GET /api/session-commands`) gives `command_id` a discoverable set of legal values.
  - `delete_session(force=False)` reaches the server's `?force=true` escape for the `409` a stale command pair produces. **`force` is never added to `create_session`** -- that is the do-not-build fence for this item, and it stays intact.
  - Full queue coverage: `followups()`, `append_followup()`, `replace_followups()`, `clear_followups()`, `resume_followups()`, plus the composed `edit_followups()` CAS-retry helper, which re-**reads** on a `409` rather than retrying the same body against a moved revision. The `followups` badge is added to `Session`/`SessionSnapshot`, parsed via `.get()` with a default -- version-tolerant in both directions. Six new exported models (`FollowupItem`, `FollowupQueue`, `Followups`, `SessionCommand`, `SessionCommands`, and the parity fields on `SessionSnapshot`).
  - **All decision logic lives in `_protocol.py` and is tested once without a network** (`client/tests/test_protocol.py`); `sync_client.py`/`async_client.py` stay thin, deliberately-duplicated shells per this repo's existing convention. `MIN_SERVER_VERSION` stays at `0.18.0` -- `check_server()` is opt-in and never called automatically, so nothing here forces a server upgrade.

### Fixed

- **`append_followup()` shipped defaulting `enter=False`, which silently queued items that type a line into a pane and never submit it.** The plan specified `enter=False` by analogy to `send_input(enter=False)`. **The server had already considered exactly that analogy and reached the opposite conclusion, in its own docstring** -- *"default True -- the common case; `/input` defaults enter to False since it also supports a bare `keys` action, but a queued follow-up is always 'type this line and submit it'."* The client was also internally inconsistent with itself: `_protocol.py` parses **inbound** items with `raw.get("enter", True)` while the write path defaulted `False`. Left as shipped, a caller who omitted the flag got a **silent no-op that presents as the follow-up queue not working** -- the failure lands nowhere near the parameter that caused it. `send_input` keeps `enter=False`; that default is correct and is unchanged.

- **`ensure_history_retention()` is deleted -- it never did anything, and the one thing it did do was harmful.** Phase 0 of the scrollback-paging plan; item E of the agent-surface plan. `history-limit` binds a tmux pane **at creation time**, and this call ran `set-option -t <session> history-limit 5000` **after** `spawn_session_command()` had already created the session and its pane. **Re-measured on tmux 3.4 against an isolated `-L` socket** (never the ambient server), rather than argued from the manual:
  - **Raise case -- the one this code actually performed:** `set-option ... history-limit 5000`, then 4000 lines of output -> `history_limit=2000`, **`history_size=1981`**, `capture -S -` returned 2005 rows. Evicted at tmux's compiled-in default, not the raised value -- **the exact failure `main.py`'s own docstring claimed the call prevented.**
  - **Lower case:** `set-option ... history-limit 50` on a live pane left `history_limit=2000`; after ~500 lines, `history_size=455`. Also bounded by the compiled default, not the value set.
  - **The one real effect was a regression**: a window created in that session *after* the call inherits the value -- so on a host running `muxplex tmux install` (whose `base.conf` sets `history-limit 50000`) this was a **10x reduction** for every later window.
  - Deletes `SESSION_HISTORY_LIMIT`, the function, and its call site. **Real retention is whatever the host's tmux config provides: 50000 under `muxplex tmux install`, tmux's compiled default 2000 otherwise -- and 2000 is exactly the `lines` ceiling**, which is why a deep request on an unmanaged host hits a wall. `get_session_snapshot()`'s docstring now says this instead of promising a guarantee. A runtime `set-option -g` at server startup was rejected rather than overlooked: `tmux_config.py`'s stated posture is that muxplex installs its config **first** specifically so the user's own `~/.tmux.conf` always wins, and a runtime `-g` would silently outrank that.
  - Two guards replace the two deleted tests that had asserted the never-true contract: `test_muxplex_never_sets_history_limit` (source-text -- `"history-limit"` must not reappear in `sessions.py`) and `test_spawn_session_command_leaves_compiled_default_history_limit`, which drives the **real** `spawn_session_command()` against an isolated `-L` socket bootstrapped with `-f /dev/null` and asserts `#{history_limit}` reads back as **2000, never 5000** -- the assertion that would have caught the original bug. The plan's own audit undercounted the `patch("muxplex.sessions.ensure_history_retention", ...)` mock sites at three; there were **eight**, all removed.

### Documentation

- **The follow-up queue had ZERO mentions in `docs/AGENT_GUIDE.md`** -- the one document written to tell an agent what it can do -- despite being muxplex's first autonomous write and its durable agent-to-agent note primitive. Item A of the agent-surface plan. New section 6.5 covers, in order: the `{pending, halted}` badge already on `GET /api/sessions` and `GET /api/view` (**`halted` is a stall that nothing clears implicitly**); all five endpoints with a worked `curl` each; `expected_revision` explained as *"a stale PUT re-runs a command"* rather than as a database concern; the failure table keyed on discriminator; the fire-time fence re-evaluation and the display-only `target_window`; and the absence of a federation proxy. Section 9's hand-wave at *"endpoints not covered here ... are all in the schema"* becomes a real coverage table (`/api/views`, `/api/views/preview`, both bell routes, `/api/heartbeat`, `/api/tmux-config`, `/api/settings/sync`, the federation aggregate and its four proxies, the terminal WS), with every line number re-derived against this tree rather than copied from the plan. Section 10 gains one item: poll `followups.halted`.
  - **Every response body in 6.5 was captured from a live instance in the `muxplex-test` DTU** (isolated `HOME`, isolated `TMUX_TMPDIR`, scratch port, self-spawned PID only), including the halt -- produced by narrowing `input_allowed_sessions` on disk between enqueue and bell, which halted with `reason: input_not_allowed` and **retained** the item. No body in that section is fabricated.
  - **An open runtime question is recorded as open rather than answered from reasoning.** The `409 bell_hook_unarmed` response could **not** be observed on a fresh instance: with no tmux there are no sessions, so an append hits the `404` gate first, and once tmux comes up the hook self-heals within one poll cycle (traced `false -> true` in ~3s, with no manual call). `POST /api/internal/setup-hooks` returns `{"ok": true}` and leaves the hook armed, so it remains the right recovery to attempt -- and 6.5 says exactly that, rather than asserting a recovery path that was never watched to clear.
  - **A dead `409` is retired from the guide.** `AGENT_GUIDE.md:540-553` told agents `POST /connect` can return `409 {"terminal_conflict": true}` and prescribed `&takeover=true` recovery. **Both are dead**: `API_SEMANTICS.md:744` already records the response as RETIRED, `connect_session` has no `409` path, and `takeover` is accepted and ignored. Agents reading this wrote error handling that can never run. Replaced with what is true (one ttyd per session, nothing to contend for, `takeover` inert, the real failures are `500` and `503`), plus a note that an older client still handling the `409` simply never sees it. **The frontend's own `terminal_conflict` handling is deliberate version tolerance against older peers and is untouched.** `test_agent_guide_does_not_prescribe_retired_terminal_conflict` (`test_api.py`) is the guard. One correction to the plan was needed to make that guard coherent: its §5.1 prescribes writing the literal `?takeover=true` while its §5.5/§5.4 test require that exact string to appear **zero** times -- resolved in favour of the checkable gate, so the parameter is named without the `=true` literal.
  - The `GET /api/sessions` example at `:268` showed five of the seven keys the route returns; `views` and `followups` are added, verified by diffing the example's key set against a live response. Two dead in-document anchors fixed. **Correction to an earlier claim in the plan: `GET /api/views` WAS already mentioned in the guide at `:324`** -- this work completes that mention with a response shape rather than introducing the endpoint.

- **The guide's history-limit claim was false and is corrected.** It told agents sessions get `history-limit` raised to 5000 on creation *"so a max-depth request has real scrollback behind it."* That never happened -- see the `ensure_history_retention()` entry above. Replaced with what is true: retention comes from the host's tmux config (50000 under `muxplex tmux install`, tmux's compiled default 2000 otherwise), **and 2000 equals the `lines` ceiling -- so `?lines=2000` against an unmanaged host can legitimately return everything there is.** Routed to its own commit because the file was held by a concurrent builder when the deletion landed.

### Deferred (recorded, not dropped)

Three follow-ups are specified in their plans and deliberately not in this release:

- **Scrollback paging Phase 2** -- `history_size` / `history_limit` / `alternate_screen` on `GET /api/sessions` entries, from the fields `enumerate_sessions()` already reads for free. Lets a UI or client decide affordances (is there anything to page back to? is this pane in an alternate screen?) **without a probe request**. Independent of Phase 3; ships value alone. `docs/plans/2026-08-07-scrollback-paging-plan.md` §4.
- **Scrollback paging Phase 3 -- the client's `before=` request-side sugar.** `session(name, lines=, before=)` plus an optional backward-paging generator. The response-side fields landed here (see above); this is sugar over the loop the guide already documents, not new capability. Same plan, §4.
- **Bell-causality Phase 2 -- `PUT`/`DELETE /api/sessions/{name}/status`.** A closed `working`/`blocked`/`failed`/`done` enum with bounded `detail` and a server-stamped `set_at`. Fully specified in `docs/plans/2026-08-07-bell-causality-plan.md` §7 and slotted in `docs/BACKLOG.md`, deliberately **not scheduled**: it is a different concept from bell causality, it carries an unanswered lifecycle question (**who clears it**), and its stated trigger condition is a *second, independent* consumer needing it. One integration wanting it is a preference; two converging is a contract.

### Verification

- **Both suites were run AFTER the version bump to `0.43.0`, per the v0.31.1 incident.** The client/server parity test -- `test_client_contract.py::test_client_version_matches_server_version` -- asserts `client/pyproject.toml`'s version equals the repo root's, so it is the one assertion that can only fail once the bump exists; a release that runs its suite before bumping never tests the thing the bump can break. **This release is the unusual case the discipline was written for**: the bump did not happen in a release commit at all -- `0a0fe08` (PR6) carried `pyproject.toml`, `client/pyproject.toml`, `uv.lock`, and a `0.43.0` CHANGELOG section inside a feature commit, contrary to `AGENTS.md`'s "version bumps happen at release time." Both files were verified to already read `version = "0.43.0"` and `uv.lock` to already carry `0.43.0` for both `muxplex` and `muxplex-client`, so no version file changed here -- and the suites were re-run anyway rather than inferring a pass from an unchanged number.
- `make test` (DTU `muxplex-test`, never the host -- a live muxplex serves 61 real sessions on 8088): **2356 passed, 4 skipped, 52 deselected in 96.06s (0:01:36)**. Run against `git archive HEAD` of the bumped tree, so the artifact tested is the artifact that ships. The parity test was additionally re-run **by name** inside the same container: **`muxplex/tests/test_client_contract.py::test_client_version_matches_server_version PASSED [100%]` / `1 passed in 0.17s`, with `pyproject.toml` and `client/pyproject.toml` grepped in the same container and both reading `version = "0.43.0"`**.
- `node --test tests/*.mjs` in `muxplex/frontend`: **920 tests, 920 pass, 0 fail, 0 skipped, duration 3205.9ms**. **No commit in this range touches a frontend file** (`git diff --stat v0.42.0..HEAD -- muxplex/frontend/` is empty), so unlike v0.40.0/v0.41.0 the node suite is a regression gate here rather than the load-bearing one -- it is run and quoted because a green Python suite says nothing about `app.js`, not because this release changed it.
- **The `## v0.42.0 (2026-08-06)` section header was destroyed and is restored here.** `0a0fe08` inserted the `0.43.0` section by **replacing** the `v0.42.0` heading line rather than adding above it (`git diff v0.42.0..HEAD -- CHANGELOG.md` shows `-## v0.42.0 (2026-08-06)` / `+## v0.43.0 (2026-08-07)`), which left v0.42.0's entire body -- the `POST /api/focus` release -- silently filed under `v0.43.0`, complete with a second `### Added` heading and its own stale `0.42.0` Verification block. A reader would have attributed the focus endpoint to this release. Recorded rather than quietly fixed, because it is the concrete cost of bumping a version outside a release commit: the same commit that skipped the release step also mangled the file the release step exists to consolidate.
- **PR1 through PR5 had no CHANGELOG representation at all.** They landed before `0a0fe08` created the section, and the section it created covered only its own change; PR7 then appended to it. Everything above under Added/Fixed/Documentation other than the rename and `bell.source` entries is new in this release commit, written from each commit's diff rather than from its message.

## v0.42.0 (2026-08-06)

### Added

- **`POST /api/focus` -- foreground-focus for this host's muxplex PWA window moves OUT of the `muxplex-deck` sidecar and into muxplex itself, so any client can ask for it over HTTP instead of only the machine with a physical Stream Deck plugged into it.** Backlog item 3, implementing `docs/plans/2026-08-05-focus-grab-plan.md`. Before this, focus-grabbing lived in `muxplex-deck/focus.py` and therefore only worked where the deck process ran; the soft deck's `focus_app` binding was a documented `console.info` no-op and the web UI had nothing at all. One server-side implementation now serves the hardware deck, the soft deck, and any agent holding a Bearer key -- rather than each client re-deriving a platform-specific focus-stealing mechanism, which is the same "resolve it server-side" rule `AGENTS.md` already states for anything a client would otherwise have to re-implement.
  - **macOS only, and every other platform returns an honest `501` naming its own reason -- never a success-shaped no-op.** `muxplex.focus.resolve_focus_capability()` (`muxplex/focus.py`) is the single dispatch point, mirroring `service.py`'s `_is_darwin()`/`_have_systemctl()` pattern. macOS works because muxplex's launchd agent is bootstrapped into the `gui/$UID` Aqua domain, which has window-server access; `open -a <app>` activates a running app or launches it if it isn't, with no AppleScript/Automation permission prompt (`osascript` was rejected for exactly that prompt). **Wayland is structurally impossible, not merely unimplemented:** `xdg-activation-v1` requires the *requesting* process to already be a Wayland client holding a surface and an input serial, and muxplex is a headless HTTP server -- it cannot mint a token to hand to anything, on any compositor. **Linux/X11 is unreliable rather than impossible:** the systemd user service carries no guaranteed `DISPLAY`/`XAUTHORITY`, and there is no muxplex-owned mechanism to drive `wmctrl`/`xdotool` even where it does. WSL 501s because the window being raised is a *Windows* browser window -- there is nothing on the Linux side of the boundary to raise. Each of these carries its own distinct `detail` string, so an operator learns *why*, not just *no*.
  - **The endpoint accepts no target of any kind -- no request body, no query parameter, nothing -- and that is the entire security design.** The app raised is always exactly `settings["focus_app"]`, and `focus_app` joins `settings.LOCAL_ONLY_KEYS` (`settings.py:248`) under the rule this repo already applies to `new_session_template`, `delete_session_template`, `tmux_socket_dir`, `tls_cert`/`tls_key`, and `session_commands`: **any settings key naming a command or path the server itself later executes is local-file-only.** `PATCH /api/settings` silently ignores it, it is never in `SYNCABLE_KEYS`, and federation sync can never apply it -- so widening the fence requires a deliberate on-disk operator action. A caller who fully controls the request can still only trigger the one app the operator already chose. Adding an `{"app": "..."}` field "for flexibility" would turn this into `open -a <arbitrary>` -- remote process execution one thin layer removed from `/input`'s RCE-by-design, and precisely how the `new_session_template` sibling incident happened. `test_focus_endpoint_accepts_no_target` and `test_focus_endpoint_accepts_no_query_param_target` (`muxplex/tests/test_focus.py`) guard the property directly.
  - **Two fences were argued for and deliberately NOT built, both as theater.** A `focus_enabled` boolean: there is exactly one host, one operator-chosen app, and nothing to scope -- an empty `focus_app` (the default) already means "off," and a second toggle would only create a state where the operator configured the app and still gets nothing. A rate limit: `DELETE /api/sessions/{name}` -- strictly more destructive, and unrecoverable, since a tmux session is a live process tree and not a file -- carries none, so rate-limiting the *window-raise* endpoint would be an inconsistency that reads as security rather than being it. The mechanism itself uses `create_subprocess_exec` with argv (`focus.py:173`), never a shell, and the argv is pinned by `test_macos_focus_command_is_argv_never_shell` and `test_raise_window_uses_create_subprocess_exec_never_shell`.
  - **Response ordering is platform (501) -> configuration (409) -> mechanism failure (502) -> success (200), and the order is load-bearing.** The platform check is public, non-sensitive information; running it first means an unsupported-platform caller learns nothing about whether the operator configured anything locally. `test_platform_checked_before_configuration` pins it. A `502` carries the real stderr verbatim (usually "Unable to find application named ...") because that is the text an operator needs to fix their own `settings.json`, not a generic failure message.
  - **`GET /api/instance-info` gains an additive `focus` block** (`{supported, configured, platform, mechanism}`, `main.py:3092`) so a client can render an honest disabled state instead of a dead key -- the same purpose `bell_hook_armed` already serves. **The `focus_app` VALUE is deliberately never exposed there**: that endpoint is unauthenticated, and a local app name is not a fact an unauthenticated caller needs; an authenticated client that genuinely wants it can read `GET /api/settings`. `test_instance_info_is_unauthenticated_for_focus_block` pins that the block is reachable without auth *and* that the value is absent from it.
  - **`muxplex_client` gains `raise_focus() -> FocusResult` on both the sync and async clients** (`sync_client.py:275`, `async_client.py:253`), plus the `FocusResult` model and `parse_focus_result`. Takes no parameters -- the no-target property is expressed in the signature, not just enforced on the wire. Raises `ApiError` for every documented failure mode with `status` distinguishing 501/409/502; callers wanting best-effort semantics catch `MuxplexError`. **This is what `muxplex-deck` needs from this release**: its own `focus.py` is deleted and it now calls this method, so its type-check fails against a published client older than this one.
  - **REGRESSION, stated here rather than left to be discovered: `muxplex-deck`'s Windows focus implementation is deleted with no replacement.** The sidecar's `focus.py` carried a real Win32 path (`EnumWindows` title match plus a `SendInput` ALT tap); it is removed in full, because muxplex has **no Windows port at all** -- `README.md`'s platform table lists Linux, macOS, and WSL, and there is no muxplex process on a Windows desktop for a client to ask. Keeping it would mean the deck permanently owns a second, divergent focus implementation for exactly one platform, and would force every client into a "if the server says 501 and my own host is Windows, do it locally" rule -- the per-client platform logic this whole change exists to delete. **Net effect: `focus_app` stops working for a Windows-hosted PWA.** The correct future fix is named but not built: a WSL->Windows interop mechanism (`powershell.exe` from the WSL side), which would restore it *through* the server rather than around it. See the plan's §0.2 and §7.3.
  - **The builder shipped the `LOCAL_ONLY_KEYS` rationale as a comment and forgot the string, and the fence tests caught it immediately.** `settings.py`'s comment block explaining exactly why `focus_app` must be local-file-only was written before `"focus_app"` was actually added to the frozenset -- so for a moment the file *documented* a fence it did not *have*, and `PATCH /api/settings` would have accepted the key. `test_focus_app_is_local_only`, `test_focus_app_is_not_syncable`, `test_patch_settings_ignores_focus_app`, and `test_apply_synced_settings_never_applies_focus_app` failed on the spot. Recorded because it is the argument for *testing* a fence rather than *documenting* it: the prose was correct, complete, and persuasive, and it protected nothing. The shipped tree has the string (`settings.py:248`).
  - **Not proven: that `open -a` raises the window on a Mac from a CI runner or this Linux dev box.** There is no macOS host in the test environment, so every macOS-path test mocks the subprocess and asserts the argv, the exception mapping, and the timeout behavior -- not that a window actually came forward. What *was* verified live, on the owner's Mac immediately before implementation, is the load-bearing precondition the plan gated on (§9.1): muxplex's **installed** launchd agent (not a foreground process) can run `open -a` for real from the `gui/$UID` Aqua domain. That is quoted from the implementation commit, deliberately not re-measured here.

### Fixed

- **A soft deck could be stranded by its own Settings panel: `gridOverride: 12x2` on a landscape phone produced a completely blank black screen -- no keys, no message, no RETRY, no way back in.** Backlog item 4, per `docs/plans/2026-08-06-settings-recovery-plan.md`. `computeGridForShape` returns a valid, **non-zero** `rows`/`cols` alongside `tooSmall: true` when an override doesn't fit the current viewport; CSS then hid `#deck-surface` via `.too-small`, while `render()`'s takeover guard only checked `rows === 0 || cols === 0`. Neither branch fired: `renderKeys()` painted into a `display:none` container and the user saw nothing at all. `render()` now also takes the takeover branch on `grid.tooSmall` (`deck.js:2469`), and the takeover message *names the offending shape* ("This screen is too small for a 12x2 grid.") rather than a generic string. **Only the soft deck can strand a user this way** -- the hardware deck has the `muxplex-deck` config CLI and the web UI has the `muxplex config` CLI plus the address bar, each a second tool unaffected by the broken settings; the soft deck's settings live in `localStorage` with exactly one reader/writer, which is the page those settings can break.
  - **The takeover gained a SETTINGS button beside RETRY, shown unconditionally** (`deck/index.html:77`) -- the one affordance in this design that no grid shape, no binding, and no brightness value can remove, because the takeover replaces the grid entirely.
  - **Brightness stops persisting, reaching parity with the hardware deck, which solved this first and wrote down why.** `persistableDeckSettings()` (`deck.js:1003`) excludes it from both `saveDeckSettings` and `exportSettingsJSON`, `mergeDeckSettings` ignores an incoming `brightness`, and `setBrightness` no longer writes -- so a deck dimmed to 10% comes back at 100% on reload, exactly as the hardware sidecar re-asserts on bring-up. The dim filter also **moved off `#deck-root`** onto `#deck-grid`/`#deck-dial-strip`/`#deck-touch-strip` via a `--deck-dim` custom property: a CSS filter on an ancestor composites the whole subtree, so the old placement dimmed the recovery surfaces too. `#deck-settings` and `#deck-disconnected` now stay legible at any brightness. One pre-existing test ("mergeDeckSettings: valid incoming fields are adopted") asserted the old adoption behavior and was corrected, not loosened -- the behavior is deliberately reversed.
  - **Unreachable grid shapes are refused at write time, and pre-existing ones are named at boot without being touched.** `gridOverrideReachability(rows, cols)` (`deck.js:1254`) refuses to *save* any shape that leaves no way back into Settings -- every degenerate 1xN/Nx1, and 2x2 -- with an inline error naming the fix. Write-time refusal only helps future writes, so `settingsReachability(p)` (`deck.js:1284`) is a boot-time detector: when persisted settings leave the deck at `longpress-only` or `none` reachability for the *current* grid, `boot()` opens Settings automatically with a `role="alert"` banner naming the specific cause (`grid-too-small` / `grid-degenerate` / `grid-too-few-keys` / `bindings-consumed-slots`) -- **and changes nothing.** Import likewise still applies a stranding `gridOverride` (recovery posture: an operator pasting a config may be doing so to recover) but warns instead of silently repairing it. Refusing to repair is deliberate throughout: silently rewriting a user's configuration is how they stop trusting the panel.
- **Four defects in the soft deck's Settings panel -- a settings panel it turned out already had.** Backlog item 2, per `docs/plans/2026-08-06-soft-deck-settings-menu-plan.md`. **The backlog entry was stale: it was written before the panel was implemented and never updated.** A ten-`<section>` panel (`deck/index.html`; the plan's §0 says eleven, the tree has ten) already covered grid shape, emulated dials, the emulated touch strip, session sort, poll interval, brightness, arbitrary address->action bindings, and export/import/reset, reachable four ways. Rather than build a second one, item 2 became: fix what the existing panel gets wrong. Zero new settings, zero new actions, zero `/api/*` surface. Three of the four are the same defect wearing different clothes -- *the panel shows you a configuration and does not tell you which parts of it are doing anything*:
  - **An inert binding rendered identically to a working one.** Worst case: `key.0` bound on a corners-mode grid is painted by `computeKeyPlan` and then silently erased by `_setControlFace` -- the reserved VIEW control face wins, with zero trace. New pure exported `bindingApplicability(bindings, shape)` (`deck.js:616`) classifies every binding (`key-out-of-range`, `key-is-reserved-control`, `no-dials`, `dial-out-of-range`, `no-strip`, `strip-zone-out-of-range`), and `renderBindingsList` marks unapplied rows with the reason -- **never removing them**. Address-level reasons win the evaluation order over action-level ones, because the address problem is the more actionable fix.
  - **Nothing told the user which key index is which** -- and reserved control positions *move* between corners and bottom-row grid modes, so the answer isn't even stable. New read-only `renderKeyMap()` legend (`deck.js:3385`) inside the bindings section: one cell per key index showing VIEW/PREV/NEXT/bound-action/plain, and tapping a cell fills the address field.
  - **A binding write that starves the picker's SETTINGS slot was silently permitted**, caught only at the next cold start by the boot detector above -- no signal at the moment of the write. `renderBindingsList` now recomputes `settingsReachability` on every render into `#settings-bindings-warning`: warn, apply, never refuse or repair, matching the import handler's established precedent.
  - **`focus_app` was a bindable no-op** whose only signal was a `console.info`. Item 3 above then made it real: the soft deck now POSTs `/api/focus` same-origin, and `bindingApplicability` no longer reports `unsupported-on-soft-deck` for it at all -- it applies structurally like any other momentary action. The two commits reconcile in `test_deck.mjs`, whose F4 test was rewritten from asserting the reason to asserting its absence.
  - **A fifth item was considered and rejected**: a confirm dialog before Reset. Reset is the documented escape hatch *from* a stranded state, and friction on an emergency control is the wrong trade; Export, two sections above it, is the mitigation that already exists.
- **Three tests built "fake" pids from `hash()` and were sending real `SIGTERM`s to arbitrary live processes on CI runners.** `test_kill_all_ttyd_kills_every_registered_session` failed intermittently -- `assert 2 == 3`, three times (twice macOS, once Python 3.13), always green on an unchanged rerun. Root cause traced end to end: CPython randomizes `str` `hash()` per process, so `hash(name) % 50000 + N` is a **different real integer every run**, and this test (unlike its neighbor) did not mock `os.kill`. `kill_ttyd()` therefore called the *real* `os.kill(pid, SIGTERM)` on that value. When it hit a genuinely live pid, `ProcessLookupError` was harmlessly suppressed -- but `PermissionError` (a live process this user can't signal, common for low-pid system daemons on shared runners) is **not** in that `suppress()` and escaped uncaught, where `kill_all_ttyd()`'s `gather(return_exceptions=True)` captured it in place of `True` and undercounted by exactly one.
  - **The flake was the benign outcome.** On a host where the collision lands on a pid the process *can* signal, the unmocked call delivers a real `SIGTERM` to an unrelated live process -- confirmed in the DTU, where sampled fake pids landed on a leftover scoped tmux test server and the exec session's own bash. On a developer box that is also serving muxplex, this is the same class of hazard `AGENTS.md`'s "never run the test suite on a host running a live muxplex" section exists for: **a test that damages its host still passes.**
  - Fixed by replacing all three `hash()`-derived pids with deterministic module-level constants and mocking `os.kill` in the two tests that reach a real `kill_ttyd()`, matching the existing pattern in `test_kill_removes_socket_and_run_file`. `test_cap_raises_when_all_busy` needs no mock -- both sessions hold an acquired relay, so `reap_idle_ttyds()`'s `relays == 0` filter never reaches `kill_ttyd()`. Grepping `hash(` across `muxplex/` confirmed these were the only three instances in the repo.

### Documentation

- **`docs/plans/2026-08-06-restore-consolidation-plan.md` is preserved into the repo** -- a design written and executed the same day, which deleted five files of the owner's personal boot infrastructure (two dotfiles scripts, three systemd units). Its rollback procedure and the corrected by-path `systemctl` commands were the only record of how to undo that, and they existed solely in a scratch workspace destroyed at session end. No code change; recorded here because a plan doc that survives its own workspace is the difference between a reversible change and an unreversible one.

### Verification

- **Both suites were run AFTER the version bump to `0.42.0`, per the v0.31.1 incident.** The client/server parity test -- `test_client_contract.py::test_client_version_matches_server_version` -- asserts `client/pyproject.toml`'s version equals the repo root's, so it is the one assertion that can only fail once the bump exists; a release that runs its suite before bumping never tests the thing the bump can break. It passed as part of the run below.
- `make test` (DTU `muxplex-test`, never the host -- a live muxplex serves on 8088): **2228 passed, 4 skipped, 37 deselected in 94.58s**. Run against `git archive HEAD` of the bumped tree, so the artifact tested is the artifact that ships. The parity test was additionally re-run by name inside the same container against the bumped files -- `pyproject.toml` and `client/pyproject.toml` both reading `version = "0.42.0"` -- and reported `muxplex/tests/test_client_contract.py::test_client_version_matches_server_version PASSED [100%]` / `1 passed in 0.16s`.
- `node --test tests/*.mjs` in `muxplex/frontend`: **920 tests, 920 pass, 0 fail, 0 skipped, duration 3188.9ms**. Load-bearing for this release: three of the five commits in range are frontend changes to `deck/deck.js`, and the node suite is the only suite that executes a line of it.
- **`muxplex/tests/test_focus.py` is new: 25 tests** covering the `LOCAL_ONLY_KEYS`/`SYNCABLE_KEYS` fences, the PATCH and federation-sync ignore paths, the no-body and no-query-param target guards, the full 501/409/502/200 response ordering, the `instance-info` capability block (including that it stays unauthenticated and never carries the `focus_app` value), and `focus.py`'s platform dispatch and argv construction.
- **Playwright/real-Chromium smoke coverage exists for the two deck commits and is deliberately NOT part of either suite.** `test_deck.mjs` is intentionally DOM-free, so the CSS/DOM behavior it cannot reach (the dim filter's compositing scope, the takeover's SETTINGS button, `?settings=1`) was exercised by a standalone script driving real Chromium against a scratch static file server with a mocked `/api/view`/`/api/sessions`. That script lives in the throwaway development workspace, not in the repo, and is quoted from the implementing commits rather than re-run here -- it is not a gate this or any future release runs.
- **Not covered by any suite: that `open -a` actually raises a window.** No macOS host exists in this environment; the macOS path is mocked at the subprocess boundary throughout. See the `POST /api/focus` entry above for what *was* verified live on the owner's Mac, and when.

## v0.41.0 (2026-08-06)

### Added

- **Dictation now has a cloud mode, behind a one-time per-device consent gate -- because the on-device-only gate v0.40.0 shipped could never open on either machine its owner actually uses.** v0.40.0's entry states the design plainly: "There is no cloud fallback -- not as an option, not behind a setting, not with a warning -- and the mechanism is that the button's *existence* is the gate." Measured on the owner's real hardware, that gate reports `cloud: available`, `ondevice: unavailable` on **both** macOS Edge 151 and Windows Edge 150 -- so the mic button was hidden on every device he owns and the feature was, in practice, unreachable. **A gate that can only ever be closed is not a safety property; it is a dead feature.** The privacy justification behind it was imposed rather than asked for: he runs cloud LLM agents in every tmux session, so his code and terminal contents already leave the box by his own deliberate, standing choice. He asked for dictation, not for private dictation.
  - **The principle that was conflated, stated precisely: "no fallbacks / fail loud" is about *hidden* degradation -- the user believing X is happening while Y actually is.** An explicit, labeled, opt-in cloud mode is not that. It is a disclosed choice, made once, in the open, in the same terms the owner already accepts elsewhere. The rule is preserved exactly where it does apply: the one thing this change still forbids outright is a *silent* switch between modes (see the mid-session clause below).
  - **On-device is unchanged and still preferred: silent, no prompt, starts on click.** `_sttCheckAvailability()` (`app.js:4583`) calls `SR.available({ langs: [lang], processLocally: true })` **first** and returns `mode: 'ondevice'` on `'available'`/`'downloadable'`; only when that first call comes back neither does it make a second call with `processLocally: false` and return `mode: 'cloud'`. There is no "prefer cloud" path and no merge of the two -- whichever branch resolves wins outright, and the loser is never consulted again. Both unavailable still resolves `null` and the button stays hidden, exactly as before.
  - **Cloud requires a consent gate that names, in its own static text, where the audio goes.** `#compose-cloud-consent` (`index.html:137`, static markup, ships `hidden`, no `innerHTML`/template literals -- the compose bar's existing discipline) reads: *"On-device dictation isn't available in this browser. If you continue, your voice recording will be sent off this device to your browser's cloud speech-recognition service to be converted to text."* `_sttHandleClick()` (`app.js:5015`) consults `_sttCloudConsentGranted()` before constructing anything, and shows the gate instead of starting (`app.js:5024`); only `_sttCloudConsentAllow()` (`app.js:4744`) -- the "Use cloud dictation" button -- ever writes the flag or calls `_sttProceedToStart()`. "Not now" hides the gate and stays idle, deliberately with no error message: declining is an ordinary choice, not a failure.
  - **The consent is per-device and never federated.** `STT_CLOUD_CONSENT_STORAGE_KEY = 'muxplex-stt-cloud-consent'` (`app.js:4544`) lives in `localStorage`, the same precedent as `COMPOSE_PREF_STORAGE_KEY`/`SYNC_GROUP_STORAGE_KEY` -- deliberately not a settings key, not in `SYNCABLE_KEYS`, not anything a peer or a Bearer-key holder can set. A device that has not granted it is asked, even when another of the owner's devices already has. A blocked/unavailable `localStorage` fails toward asking again, never toward assuming consent (`app.js:4711`).
  - **The mode is visible at rest, not only once the mic is already live.** `_sttRenderButton()` applies `.compose-bar__mic--cloud` whenever `_sttMode === 'cloud'` independent of state (`app.js:4673`), which paints a persistent amber corner dot (`style.css:967`); the **listening** pulse itself is amber for cloud against red for on-device (`style.css:999`); and the title names the mode either way ("Dictate (cloud speech-to-text -- sends audio off this device)" vs "Dictate (on-device speech-to-text)"). The reasoning for giving cloud the louder treatment: a live mic picks up whoever else is in the room or a call in the background, so the case where that audio actually leaves the device is the one that should be unmistakable before the click, not after.
  - **The mode is fixed at init and never silently upgraded mid-session -- this is the part of "no fallback" that survives intact.** `_sttInit()` assigns `_sttMode` once per page load; `_sttStart()` sets `recognition.processLocally = _sttMode !== 'cloud'` explicitly rather than omitting it (`app.js:4920`), and `_sttHandleClick()` never re-probes availability. If the mode a session started in degrades afterward (`'language-not-supported'`, e.g. an evicted local model), the gate **re-closes entirely** -- both `_sttStatus` and `_sttMode` reset to `null` (`app.js:4856`) and any pending consent panel is force-hidden -- rather than falling through to the other mode. Falling through is precisely the silent substitution this design forbids. `test_stt.mjs` guards it directly: *"_sttHandleClick never re-runs _sttCheckAvailability -- the resolved mode is trusted for the rest of the session"* and *"mode resolved to ondevice at init is used by _sttStart even if on-device later 'goes away' -- no re-check, no fallback"*.
  - **A pending consent gate is torn down on session switch, for the same reason an in-flight utterance is.** `_composeClearDraft()` already called `_sttForceStop()` so a live dictation could not leak into another session's draft; it now also calls `_sttHideCloudConsent()`, so a click aimed at the **new** session cannot land on a consent prompt raised by the **old** one.
  - **True push-to-talk was considered and deliberately deferred.** Hold-to-record would sharpen the "is the mic live right now" signal further than any badge can, but it is a redesign of the whole click-to-toggle interaction, separately justified -- not something to bundle into a gating fix that is urgent because the feature is currently dead.
  - **NOT PROVEN, and this is unchanged from the original spike: that real speech becomes real text, on either path.** There is still no microphone and no on-device model in this build environment, so the browser<->recognizer round trip has never been exercised for on-device or for cloud. What *is* proven is the gating, the consent flow, and the mode plumbing: `frontend/tests/test_stt.mjs` grew from 610 to 834 lines / **67 tests**, covering the on-device-first-then-cloud cascade (including "never checks cloud at all once on-device already resolved available"), the consent gate's show/allow/cancel/persist/repeat-click paths, `processLocally` being set explicitly per mode, mode-specific button visuals idle and listening, and the mid-session no-re-decide regression above. `test_frontend_html.py` gains `test_html_compose_cloud_consent_exists_and_starts_hidden`, which asserts the panel ships `hidden`, exposes exactly the two buttons `app.js` binds, and that its disclosure text actually says the audio leaves the device.
  - **Frontend-only: `git show 4fc3ae0 --stat` touches `frontend/app.js`, `frontend/index.html`, `frontend/style.css`, `frontend/tests/test_stt.mjs`, and `tests/test_frontend_html.py`.** No `main.py`, no `settings.py`, no `client/`, no wire contract -- nothing for `muxplex-deck`, a federation peer, or an API consumer to adapt to.
  - **Why this takes the minor slot rather than extending v0.40.1's patch.** The repo's rule is minor for new capability or contract surface, patch for a user-visible behavior change with no new capability. On the two machines this was measured on, dictation did not exist before this commit -- the gate could not open -- so what arrives here is the capability itself, not a change to one the user already had. It also adds surface: a new persisted `localStorage` key, a new consent panel with three new element IDs, and a new `_sttMode` axis through the STT module, all of which the suite now pins. v0.40.0's own entry set the precedent that a frontend-only capability takes the minor slot, citing v0.37.0's compose bar.

### Fixed

- **The bell hook's arm-time delivery probe painted `curl ... returned 7` onto the owner's live tmux panes -- the same class of incident v0.36.1's `9164cdc` fixed for the persistent hook, reproduced by the fix that was supposed to prevent it a level up.** `_arm_bell_hook()` self-heals by retrying registration every poll cycle while unarmed -- exactly the common case during a restart, before the new server process is listening yet. The delivery probe it fired after registering was itself a `tmux run-shell` call (loud by design, to make its own failures diagnosable), so it re-fired on every one of those retries -- and each failing attempt (curl exit 7, connection refused, while the socket wasn't accepting yet) painted its error text onto whatever the owner's attached client was displaying, repeatedly, for the length of the restart window. The probe is **removed entirely**, not re-silenced: a silenced probe can still be made loud again by a future "help diagnose this" change, but a probe that does not exist cannot regress. `_bell_hook_curl()` (`main.py`) no longer has any parameter or code path that can construct a loud command at all.
  - **`bell_hook_armed` reverts to its pre-v0.38.1 meaning: `set-hook` was accepted, not that delivery was proven.** This is a deliberate, honest downgrade rather than a silent one -- `_arm_bell_hook()`'s docstring, `GET /api/instance-info`'s field comment, `muxplex doctor`'s advisory line, and `docs/API_SEMANTICS.md`'s `bell_hook_unarmed` entry were all updated to say so. The public field name and shape are unchanged (`bell_hook_armed: bool`, `/api/instance-info`) -- `muxplex-client` and `muxplex-deck` consumers are unaffected -- only the documented meaning of `True` is weaker. The known cost: a scheme mismatch (the ORIGINAL bell-hook incident this whole line of work started from) now registers successfully and reports armed, because `set-hook` cannot validate the command string it's given and there is no longer an arm-time HTTP round trip that could. `test_registration_succeeding_with_wrong_scheme_is_a_known_limitation` (`test_api.py`) documents this explicitly rather than leaving it implicit.
  - **New standing rule, encoded structurally, not just in prose: muxplex must never emit anything that renders on a user's terminal.** `AGENTS.md` gained a dedicated section ahead of the bell-hook entry. Enforcement is mechanical: `_bell_hook_curl()`'s signature no longer accepts a parameter that could request a loud variant (`test_bell_hook_curl_has_no_loud_variant` asserts this via `inspect.signature`), and a new structural scan (`test_safety_rails.py`'s `test_no_diagnostic_tmux_run_shell_construction_exists`) walks every `muxplex/*.py` source file and asserts exactly ONE `run-shell` string construction exists anywhere in production code -- the persistent hook's own registration call. A future diagnostic, probe, or health check built as a `tmux run-shell` call fails this test immediately, in any module, rather than waiting to be discovered on a live host a third time.
  - **Dead code removed**: `_BELL_PROBE_SESSION`, `_bell_probe_event`, `_BELL_PROBE_TIMEOUT_S` module state, and `receive_bell()`'s sentinel-session branch -- nothing else referenced them.
- **Dictation transcribed a ladder of every intermediate state instead of the sentence.** Dictating
  "what needs to be worked on next" on Android Chrome produced `what what needs what needs to what
  needs to be what needs to be worked what needs to be worked on what needs to be worked on next`.
  `_sttHandleResult()` iterated `event.resultIndex … results.length` and applied each result
  incrementally; the `isFinal` branch committed text and **advanced the insertion point past it**.
  That is correct only for an engine that never re-sends an already-finalized result -- an assumption
  the handler's own docstring stated outright (*"a spec-compliant implementation never re-sends an
  already-committed prior result"*). Android Chrome instead re-delivers a single, still-growing entry
  marked `isFinal: true` on every event, so each one landed after the previous.
  - **The fix makes transcript application idempotent rather than special-casing a browser.** Every
    `result` event now rebuilds the entire dictated region from the full `SpeechRecognitionResultList`
    against a fixed per-session anchor -- the output is a pure function of (anchor, current results),
    never of what a prior call did. Correct for both engine behaviors from one code path: a cumulative
    re-delivery replaces the region instead of appending after it, and a spec-clean disjoint list
    concatenates exactly as before. `event.resultIndex` is no longer consulted at all.
  - Pinned with the real observed failure: `test_stt.mjs` asserts the Android-shaped sequence ends with
    exactly the spoken sentence, plus the spec-clean shape, idempotency under event replay, and that a
    stop/restart mid-dictation cannot clobber previously committed text.

### Verification

- **Both suites were run AFTER the version bump to `0.41.0`, per the v0.31.1 incident.** The client/server parity test -- `test_client_contract.py::test_client_version_matches_server_version` -- asserts `client/pyproject.toml`'s version equals the repo root's, so it is the one assertion that can only fail once the bump exists; a release that runs its suite before bumping never tests the thing the bump can break. It passed as part of the run below.
- `make test` (DTU `muxplex-test`, never the host -- a live muxplex serves on 8088): **2199 passed, 4 skipped, 37 deselected in 96.23s**. Run against `git archive HEAD` of the bumped tree, so the artifact tested is the artifact that ships. The parity test was additionally re-run by name inside the same container against the bumped files -- `pyproject.toml` and `client/pyproject.toml` both reading `version = "0.41.0"` -- and reported `test_client_contract.py::test_client_version_matches_server_version PASSED [100%] / 1 passed in 0.17s`.
- `node --test tests/*.mjs` in `muxplex/frontend`: **897 tests, 897 pass, 0 fail, 0 skipped, duration 3237.7ms**. This release **does** carry a frontend change (the dictation cloud mode above), so unlike a server-only fix the node suite is load-bearing here -- it is the only suite that executes a line of `app.js`'s behavior.
- **`uv.lock` was stale and is corrected here.** `709c0f4` bumped `pyproject.toml` and `client/pyproject.toml` to `0.40.1` but left both `uv.lock` entries at `0.40.0`. The parity test compares the two `pyproject.toml` files only, so it could not have caught this; the lock is now `0.41.0` for both `muxplex` and `muxplex-client`.
- Restart-window reproduction: `test_arm_succeeds_with_no_http_round_trip_at_arm_time` and `test_arm_bell_hook_never_calls_run_shell_for_a_probe` (`test_bell_hook_delivery_integration.py`) prove a single `set-hook` call arms the hook with zero HTTP dependency and zero `run-shell` calls beyond the persistent hook's own registration string.

## v0.40.0 (2026-08-06)

### Added

- **On-device dictation in the compose bar — a spike, and it is gated on the capability rather than on the platform.** A mic button (`#compose-mic-btn`) beside the compose bar's queue and send buttons dictates into `#compose-input` using the Web Speech API's *on-device* recognition (`recognition.processLocally = true`; Chrome/Edge 139+, desktop). Interim results render live and are replaced in place as they firm up; a final result is committed and the insertion point advances past it, so continued speech never overwrites already-committed text, and dictation starts at the textarea's current cursor position rather than blindly appending (`app.js`'s `_sttApplyTranscript()`). Optional `SpeechRecognition.phrases` biasing is sourced from real, already-loaded data — live session names and this device's own configured hostname — never a hardcoded word list. Frontend-only: `git show 1d6743c --stat` touches `frontend/app.js`, `frontend/index.html`, `frontend/style.css`, two frontend test files, and `tests/test_frontend_html.py`; no `main.py`, no `settings.py`, no `client/`. Same shape as v0.37.0's compose bar, which is this release's precedent for a frontend-only capability taking the minor slot.
  - **There is no cloud fallback — not as an option, not behind a setting, not with a warning — and the mechanism is that the button's *existence* is the gate.** Verified mechanically, not taken on assertion. The markup ships `hidden` (`index.html:159`: `class="compose-bar__mic hidden"`). `_sttRenderButton()` (`app.js:4590`) returns immediately after `btn.classList.add('hidden')` when `_sttStatus` is falsy. `_sttStatus` is assigned in exactly one place that can make it truthy — `_sttInit()` (`app.js:4858`), from `_sttCheckAvailability()`'s return — and `_sttCheckAvailability()` (`app.js:4521`) returns non-null only when `await SR.available({ langs: [lang], processLocally: true })` resolves `'available'` or `'downloadable'`. `'unavailable'`, `'downloading'`, a missing `.available`, a missing constructor, and any thrown exception all return `null`. There is exactly one `new SR()` in the file (`app.js:4760`) and the next line but one is `recognition.processLocally = true`, so no code path constructs a recognizer without it. Even the post-hoc regression is fail-closed: a `language-not-supported` error at runtime sets `_sttStatus = null` (`app.js:4703`) and re-hides the button rather than leaving one that will only fail again.
  - **The elegant part: one condition does the work of two.** `processLocally` exists only on desktop Chromium, so gating on it *alone* already excludes every mobile browser — which is the correct outcome, because on mobile the OS keyboard's own dictation is on-device, better, and free. No viewport-width check and no user-agent sniffing is layered on top; the capability gate **is** the platform split. A second, platform-shaped condition would have been redundant with the first and would have had to be kept in sync with it forever.
  - **NOT PROVEN: that real speech becomes real text.** There was no microphone and no Chromium 139+ in this build environment, so the browser↔on-device-model round trip was never exercised. What the 610-line `frontend/tests/test_stt.mjs` suite does prove: the availability gating (including every rejecting status and the throwing case), the button's DOM state machine across idle/listening/downloading, the transcript-insertion algorithm against synthetic `SpeechRecognitionEvent`-shaped objects, and every `SpeechRecognitionErrorEvent` code's message path. The live path is unproven and is stated here rather than left implied.
  - **No auto-restart on `end`/`no-speech`, deliberately.** Chrome ends a continuous session after roughly a minute of silence, and auto-restarting from `onend`/`onerror` gets the *origin* rate-limited by the browser — a naive restart loop is precisely the bug this avoids. `_sttHandleEnd()` (`app.js:4724`) never calls `_sttStart()`; mechanically, the only callers of `_sttStart()` are `_sttHandleClick()` (`app.js:4847`) and `_sttInstallThenStart()` (`app.js:4835`), which is itself only reached from that same click handler, and the sole `recognition.start()` is inside `_sttStart()` (`app.js:4772`). Every stop, explicit or not, surfaces a specific inline reason via `#compose-error` — never silence.
  - **Click-only, no keyboard shortcut in v1.** `terminal.js`'s `attachCustomKeyEventHandler` already owns several Ctrl/Shift+Enter chords and the terminal holds focus nearly all the time — v0.38.1 already had to cut a carve-out there for the follow-up queue shortcut. A dictation chord is a second such carve-out and a separately-justified v2 cost, not a v1 default. The only binding is `on($('compose-mic-btn'), 'click', _sttHandleClick)` (`app.js:4429`).

### Fixed

- **The active-session tier is removed from `?sort=attention` — and this reverses v0.38.1's own fix, which was built on a wrong diagnosis.** The reported symptom was real: the session the owner was working in sat at the *bottom* of the attention order. The diagnosis was not. v0.38.1 concluded "the client is missing the server's tier 2" and shipped a dedicated active-session tier into `app.js`'s `sortByAttention()` to match `main.py`. The actual cause was the **dead bell hook** — `_arm_bell_hook()` curled `http://` at a TLS port, so bells were never recorded for an attached session and its `bell.last_fired_at` froze at whatever had fired last. That hook was fixed in the *same* release. With bells actually delivering, a session under active work rises on its own merit: measured on the live host before this change, the session in question had a `last_fired_at` 246 seconds old and ranked **5 of 57** by bell recency alone. Tier 2 was therefore fixing a symptom that no longer existed.
  - **Redundant would have been reason enough; it was also wrong.** Tier 2 meant that *selecting* a session bumped it up the list. This sort's contract is to track **agent-turn completions**, not user navigation — a session should move because something happened in it, not because someone looked at it. The second reason is worth recording separately because it outlives the first: tier 2 **masks bell-hook failure**. If the hook breaks again, an active-session tier silently props the attached session to the top and the symptom that would otherwise expose the breakage never appears. Without it, the ordering stays honest about bell state.
  - **Removed from all three mirrored implementations, per `docs/API_SEMANTICS.md`'s "all three must move together" contract.** `main.py`'s `_attention_order()` (`main.py:1537`) is now two tiers: needs-attention by `bell.last_fired_at` desc (`main.py:1567`), then everything else by `bell.last_fired_at` desc with never-belled last (`main.py:1575`), returning `tier1 + tier2` (`main.py:1583`). `frontend/app.js`'s `sortByAttention()` (`app.js:82`) mirrors it and dropped the now-unused `currentSessionName`/`currentRemoteId` parameters — along with the same two parameters on `applySortOrder()` (`app.js:114`), which existed solely to forward them — updating both call sites (`renderGrid`, `renderSidebar`). `muxplex-deck`'s `apply_attention_sort()` (`attention.py:51`) is the third, released separately as that repo's v0.13.1.
  - **The `active` field stays on the wire, unchanged.** Only the *ordering semantics* changed. `active` is still computed and returned on every `GET /api/view` entry (`main.py:1680`) and is still in the endpoint's documented key set (`main.py:1616`), because it is a public contract field with consumers that have nothing to do with sorting — `muxplex-deck`, federation, and the PWA's own sidebar highlight. Removing it would have been a breaking change to a concept this fix never touched.
  - **The three-way agreement is now pinned by a shared fixture plus one consumer test per implementation — verified, not assumed.** `tests/fixtures/attention_sort_cases.json` holds **7 cases** and is duplicated byte-for-byte into `muxplex-deck/tests/fixtures/attention_sort_cases.json`; the two copies were confirmed identical by checksum (`md5sum` → `a6a50d1b8632f6aa3c565eab87363022` for both). Duplication rather than a shared file is deliberate and stated in the fixture's own header: the two live in separate git repos with independently versioned releases. Three consumers read it — `tests/test_attention_order_fixture.py` (`_attention_order()`), `frontend/tests/test_attention_fixture.mjs` (`sortByAttention()`), and `muxplex-deck/tests/test_attention_fixture.py` (`apply_attention_sort()`). Three of the seven cases pin the *absence* of the removed tier specifically: "selecting a session must not change its position (no active-session tier)", "the active session's OLDEST bell still sorts it last — being active confers no boost", and "an active session that also needs attention is ranked by tier 1 only, appears once". This is what makes a future drift in any one implementation a test **failure** rather than a silent divergence found in production — which is exactly how the v0.38.1 mistake reached a release.
  - **Tier 3's keying is unaffected, only renumbered.** The surviving non-bell tier still orders by `bell.last_fired_at` rather than `last_activity_at`, for the reason its docstring has always given: `last_activity_at` derives from tmux `#{window_activity}` and bumps on any pane redraw, which reordered the grid on every poll cycle. It is simply called tier 2 now that there are two tiers.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break. It passed at 0.40.0, run explicitly inside the DTU against the tested tree: `muxplex/tests/test_client_contract.py::test_client_version_matches_server_version PASSED [100%]` — `1 passed, 24 deselected in 0.18s`, with that tree's own `pyproject.toml` and `client/pyproject.toml` both reading `version = "0.40.0"`.
- `make test` (DTU `muxplex-test`, never the host — a live muxplex serves on 8088): **2198 passed, 4 skipped, 36 deselected**, exit code 0. Run twice, with identical counts (98.40s, then 97.24s): once on the release commit as first written, and again after this verification paragraph was filled in with the real numbers, so the second run covers the committed tree — the two trees differ only in this paragraph. `node --test tests/*.mjs` in `muxplex/frontend`: **881 pass, 0 fail, 0 cancelled, 0 skipped, 0 todo** across the whole glob (not a single file — the glob is what runs `test_terminal.mjs`, `test_stt.mjs`, and `test_attention_fixture.mjs`). `make test` snapshots `git archive HEAD`, so the DTU tested exactly this commit's tree.
- Every claim above was re-checked against this tree rather than carried over from the commit messages. The no-cloud-fallback claim in particular was verified by tracing the only assignment path to `_sttStatus` and confirming the single `new SR()` site sets `processLocally = true`, rather than by reading the section banner that asserts it. The cross-repo fixture's byte-identity was checked by checksum against the other repo's working tree, not inferred from the commit message that claims it.
- **The 246s / rank-5-of-57 measurement is quoted from the fix commit, not re-measured for this release.** It was taken on the live host at the time of the fix; re-running it now would sample a different moment and prove nothing about the state that justified the change.

## v0.39.0 (2026-08-05)

### Added

- **`#{session_created}` is finally on the wire: `GET /api/sessions` entries gain `created_at`, and `GET /api/instance-info` gains `server_started_at`.** v0.36.1 plumbed tmux's own creation timestamp internally, to tell a genuinely-new session from a merely-first-observed one and seed its bell so it sorts to the top of the attention view — and deliberately kept it off the payload. That release's own "Known limitation" section called exposing it "the obvious future change; it is not made here because nothing yet asks for it." Something asks now: external `muxplex-client` integrations land this month, and the ordering of those two events is the whole argument. A field that exists *before* its clients do costs a client nothing; the same field added *after* means every integration ships a local workaround for its absence and then carries that workaround forever, long past the point the field arrives. `created_at` is a raw unix-epoch float from `sessions.get_session_created_times()`, present exactly like `last_activity_at` — the key is always there, `null` when tmux reported nothing parseable (`main.py:1450`; `sessions.py:421` logs the malformed case rather than crashing the enumeration).
  - **Two fields shipped, and the second is the one that makes the first useful.** The server's rule is a comparison, not a value: `main.py:635` seeds a bell only when `created_at >= _server_start_time`. A client holding `created_at` alone has no watermark to compare it against — it can render "created 4m ago" but cannot reproduce "new to *this server's current process*," which is the distinction the field exists to carry. So `GET /api/instance-info` now also returns `server_started_at` (`main.py:3057`, the process-lifetime `_server_start_time` reset in `lifespan()`). Shipping the timestamp without the watermark would have been half the fix, and the half that leaves every client to invent its own answer.
  - **Raw timestamps, not a derived `is_new` boolean — an explicit decision.** `AGENTS.md`'s standing rule is to resolve a client-facing *rule* server-side rather than ship logic to every client, and that rule is already satisfied here: the "is this session new enough to need attention" decision is made server-side, once, at bell-seed time, and its *outcome* is already on the wire as `bell.last_fired_at` / `unseen_count`. `created_at` and `server_started_at` are not that rule re-exposed as duplicate logic — they are the raw **inputs** it consumed, published for the uses a raw timestamp serves and a boolean forecloses: age display, or a client applying its own freshness window instead of "since this server last restarted." Precedent on the same entry: `last_activity_at` is itself a raw timestamp, not a `stale: bool`. `created_at` matches the shape of the field it sits beside, not merely its `_at` naming.
  - **Federation: reproducing "genuinely new" for a *remote* session needs that peer's own `server_started_at`, never the local one.** A federated session's `created_at` arrives through `GET /api/federation/sessions`, which spreads the remote `/api/sessions` entry verbatim — so the value comes from that remote host's tmux and that remote host's clock, exactly as `last_activity_at` already does. Comparing it against *this* instance's `server_started_at` compares two unrelated clocks and two unrelated process lifetimes. The correct watermark is fetched from `<remote_url>/api/instance-info` directly, the same unauthenticated per-host pattern `_fetch_remote_version()` already uses. No federation code was needed to carry the new field: the existing dict-spread forwards any new session key the same way it already forwards `last_activity_at`.
  - **A pre-existing imprecision this release makes visible rather than introduces.** tmux's `#{session_created}` is **integer-second** granularity while `server_started_at` is a sub-second `time.time()` float — measured for this release on this host in an isolated tmux server (`-L` scratch socket, own `TMUX_TMPDIR`): `#{session_created}` returned `1785995205` for a session created at `time.time() == 1785995205.8134`. So a session created within the same second as server start can round *below* the watermark and read as "not new." That is not a defect of these two fields: `main.py:635`'s comparison is byte-identical to v0.36.1's (`git show v0.36.1:muxplex/main.py` line 451 is the same `created_at >= _server_start_time`), so the server's own bell-seeding has always had this ~1s edge. Publishing the raw pair simply lets a client observe it, which is the honest trade for shipping inputs instead of a boolean.

### Documentation

- **Design-doc citations across the source tree pointed at files that never existed in this repository.** Twenty-nine comments, docstrings, and test headers named `*_SPEC.md` / `*_DESIGN.md` documents — cited by line and section number, as the design of record for shipped behavior — that lived only in a throwaway cross-repo workspace, destroyed at session end. For anyone cloning the repo they were dead pointers to authority that could not be checked. Two commits repaired it. Measured rather than asserted: the v0.38.1 tree carried **208 such citations across 63 tracked files**; this tree carries **98 across 28**, with **118 citation lines changed across 45 tracked files** in the range (`4d277e5` repointed 94 lines across 36 files; `5097bef` repointed 29 more across 18). All **seven** stale-pointer names now resolve to a tracked file — `AUTO_VIEWS_SPEC.md`, `PER_SESSION_TTYD_SPEC.md`, `COMMAND_PAIRS_SPEC.md`, `DEVICE_LABEL_SPEC.md`, `FOLLOWUP_QUEUE_SPEC.md`, `COMMAND_PAIRS_UI_DESIGN.md`, and `COMPOSE_BAR_SPEC.md` — most of them repointed to dated successors already sitting in `docs/plans/`.
  - **Three specs were preserved into `docs/plans/` before they were lost forever**, because for these there was no successor to point at: the mobile compose bar (`2026-08-05-mobile-compose-bar-plan.md`, shipped v0.37.0), the per-session follow-up queue (`2026-08-05-per-session-followup-queue-plan.md`, shipped v0.38.0), and the focus-grab design (`2026-08-05-focus-grab-plan.md`, written but **not built** — it is `docs/BACKLOG.md` item 3). That last one is the reason `docs/plans/README.md` no longer claims "all plans have been fully implemented": preserving a design *before* citing it is the correct pattern for a not-yet-built feature, and the directory's own README had to stop asserting otherwise.
  - **The first pass got two calls wrong, and both are worth recording rather than quietly fixing.** `4d277e5` **deleted** the compose bar's citations outright — its commit message says `COMPOSE_BAR_SPEC.md` "was a rejected draft spec that never shipped." It was not. Only one sub-proposal *inside* that spec — a new `POST /compose` endpoint gated on a session cookie — was rejected on security review (see v0.37.0's own entry); the compose bar itself shipped, via the existing `/input` endpoint, exactly as that spec's fallback conclusion described. Deleting the citation destroyed the pointer to the reasoning behind shipped code. Restored in `5097bef`, now aimed at the preserved file. The same commit also *kept* `FOLLOWUP_QUEUE_SPEC.md`'s references dangling on the stated reasoning that they "refer to internal design documentation that is acceptable to cite without a formal external document" — which is precisely the habit that produced this whole class of dead pointer. Both were reversed.
  - **The first pass also missed `frontend/style.css` entirely** — it never touched the file, which carried four citations of its own (`DEVICE_LABEL_SPEC.md`, `FOLLOWUP_QUEUE_SPEC.md`, `COMMAND_PAIRS_UI_DESIGN.md`, `AUTO_VIEWS_SPEC.md`). A sweep that reads `.py`, `.js`, and `.mjs` and stops there will miss CSS comments every time.
  - **`docs/BACKLOG.md` item 6 was corrected downward: eight genuinely-absent documents, not eight — six.** `COMMAND_PAIRS_UI_DESIGN.md` and `COMPOSE_BAR_SPEC.md` were miscatalogued as needing to be *written* when they existed in the workspace the whole time and only needed to be *moved*. The compose-bar miscatalog is what caused the deletion above — reading a name as "nothing behind it" is what licensed concluding the spec was a rejected draft. **Six remain, and this release does not close them:** `KEY_DESIGN_SYSTEM.md` (50 refs / 9 files), `SOFT_DECK_DESIGN.md` (33/6), `SESSION_PERSISTENCE_DESIGN.md` (24/13, including `muxplex restore --help`, which makes the CLI itself a dead end), `DECK_PARITY_ARCHITECTURE.md` (21/7), `muxplex-client-design.md` (18/10), and `CONTROL_MAPPING_DESIGN.md` (8/5). Those are writing jobs, not renames, and saying the repo's references all resolve now would be false.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break.
- Every claim above was re-checked against this tree rather than carried over from the commit messages, and pre-release claims against the **tagged** tree, not from memory: `git show v0.36.1:muxplex/main.py` line 451 is the same `created_at >= _server_start_time` comparison this release's `main.py:635` still makes, which is what makes the sub-second edge pre-existing rather than new. The citation counts are mechanical — `git grep` for `*_SPEC.md` / `*_DESIGN.md` / `muxplex-client-design.md` names at `v0.38.1` returns 208 hits in 63 tracked files, and at `HEAD` returns 98 in 28.
- **The task brief's own counts were corrected rather than repeated.** It described the documentation work as "roughly 29 references across ~20 tracked files." That is `5097bef` alone (29 lines, 18 files); it omits `4d277e5` entirely (94 lines, 36 files). The combined figure is 118 lines across 45 files, stated above. The brief also said the references "all now resolve" — seven stale-pointer names do; six absent documents, carrying 154 citations between them, still do not, and are itemized above rather than left implied.
- The tmux granularity figure was **measured for this release**, not quoted: an isolated tmux server (unique `-L` socket, own `TMUX_TMPDIR`, torn down socket-scoped) reported `#{session_created} = 1785995205` for a session created at `time.time() == 1785995205.8134`.

## v0.38.1 (2026-08-05)

### Fixed

- **The session you are actively working in sorted to the *bottom* of the attention order in focus view — and the more you used it, the further it sank.** The server was right the whole time; the client was reproducing only part of its answer. `main.py`'s `_attention_order()` has three tiers — needs-attention, then **the active session**, then everything else by `bell.last_fired_at` descending — and `frontend/app.js`'s `sortByAttention()` deliberately omitted the middle one. Its own docstring made the case: the *grid* has no active session while it is on screen, so a tier-2 concept could only ever mean something for the sidebar, and "the two surfaces sorting differently would violate the very invariant this sort exists to serve." That reasoning is correct for the grid and wrong for the **focus view** — the sidebar shown alongside an open session — where one session genuinely *is* open. With tier 2 gone, that session fell through to tier 3, which sorts by `bell.last_fired_at` descending. And the session you work in continuously has the **oldest** bell of any session on the box, precisely *because* viewing it clears the bell and nothing refires it. Sorting the freshest-bell-first tier by a timestamp that only goes stale while you watch it is what produced the inversion: a session's rank fell in direct proportion to how much attention it was getting.
  - **Measured against the live host for this release, not quoted from the investigation.** `GET /api/view?sort=attention` returned 22 sessions; the open session (`muxplex-qol-updates`) came back with `active=True`, correctly placed immediately behind the single needs-attention session, with `bell.last_fired_at = 1785975900.640427` — the **minimum** of all 22, against a maximum of `1785988858.320663`. All 22 sessions had a non-null `last_fired_at`, so ranking that set by the tier-3 key alone put the open session **22nd of 22**. The server's order was never wrong; the client's port of it was missing the tier that rescued it.
  - **The fix completes the existing port rather than replacing it with the server's answer, and that choice is load-bearing.** Consuming `GET /api/view?sort=attention`'s server-computed order — the pattern `frontend/deck/deck.js` already uses — was considered and rejected for *this* consumer, for two reasons that hold on inspection. First, `docs/API_SEMANTICS.md:218-221` sanctions exactly three mirrored implementations of this ordering (`main.py`'s `_attention_order()`, `app.js`'s `sortByAttention()`, and `muxplex-deck`'s `attention.py`) and states they "must move together" — so the honest repair of a two-of-three drift is to move the third, and the other two were checked rather than assumed: `main.py:1544` (`tier2 = [s for s in remaining if s["active"]]`) and `muxplex-deck`'s `apply_attention_sort()` both implement the tier; neither had the gap. Second, and decisively, `GET /api/view` is **local-sessions-only by design** (`main.py`'s own docstring: "Unlike `GET /api/federation/sessions`, this answers 'what does *this* device's current view look like' — remote peers are not merged in"), while the grid and sidebar display federated sessions. Consuming its order would have fixed the ordering of the sessions it knows about and had nothing to say about the rest.
  - **Tier 2 is client-knowable, which is what makes the mirror possible at all.** Identity is the `(name, remoteId)` pair `buildSidebarHTML()` already uses for its own active-row highlight — so it works for local *and* federated sessions, where the server's `active` flag necessarily cannot (`GET /api/view` has no concept of a session open on a remote device). `renderGrid()` and `renderSidebar()` forward the **same** `_viewingSession` / `_viewingRemoteId` (`app.js:2273` and `app.js:1433`), which is `null` while the grid is on screen. So the grid/sidebar agreement invariant the original docstring existed to protect is *preserved* rather than worked around: it now holds because both surfaces call one function with identical arguments, not because the tier was deleted from both.

- **`muxplex restore` could create `~/dev/<name>` directories that had never existed and start the wrong process inside them — reporting success for every one.** After a tmux-server death, `muxplex restore` correctly rebuilt 8 sessions with zero reported failures. Two of them were long-running, hand-started daemons rooted **outside** the `~/dev/<name>` convention — one at `$HOME`, one several levels below `~/dev/` at `~/dev/better-attention/voice-chief-of-staff`. All 8 had `created_with = <none>`, so restore fell through to the reserved default session command, which substitutes `{name}` into a template that conventionally resolves to `~/dev/{name}`. The result was not merely a wrong process under a right name: it **created full workspace scaffolds — `.git`, submodules, `AGENTS.md` — at `~/dev/attention-manager` and `~/dev/vcos-review`, paths that had never existed on this machine.** Both then showed green on the dashboard while running nothing. This is the failure `AGENTS.md`'s own recovery section warned about on 2026-08-02 in almost these words: *"Pointing it at a directory that doesn't exist would give you a restore that fails — or worse, creates the wrong thing."*
  - **The fix records the one thing that is genuinely, continuously observable — and refuses rather than guessing.** Every poll cycle now records each live session's actual working directory into the session-presence manifest, updated in place exactly like `last_seen_at` and never setting `changed=True` on its own, so the manifest's steady-state write budget is untouched. The source is tmux's own `#{pane_current_path}`, appended as a fourth field to the `tmux list-sessions -F` call `sessions.py:382` was **already making** for `#{window_activity}` and `#{session_created}` — no second subprocess, no new round trip. When a cold start freezes a session into `pending_restore`, whatever `cwd` was last observed freezes with it: a dated fact about where the session was really running moments before it was lost (`sessions.py:165` `get_session_cwds()`, `manifest.py:245`'s new `cwds=` parameter, `manifest.py:490` `get_restore_cwd()`, wired at `main.py:571`).
  - **The launch *command* is deliberately not recorded, and that is the honest half of this fix.** A daemon's original command line is only as durable as its own `/proc/<pid>/cmdline`, and tmux's `#{pane_start_command}` reflects a pane's first command — not anything a user later typed into an interactive shell. **A shell that has been typed into is not proof of what it started as.** Rather than fabricate a maybe-right command from an untrustworthy signal, this records only where a session runs from, and uses that to choose between two honest outcomes: proceed, or refuse with an actionable reason. It never substitutes a best guess.
  - **Two independent gates, and they gate only the unrecorded path.** `restore.py:127`'s `_check_unrecorded_restore_fidelity()` is called from exactly one place — the `recorded is None` branch of `execute_restore()` (`restore.py:343`). A session with a resolvable named `session_commands` pair never reaches it; an operator who configured one explicitly owns its behavior, directory creation included. (1) **Known divergence** (`restore.py:157`): if a cwd *was* observed and it isn't the conventional `~/dev/<name>`, that is positive, dated evidence this was never such a workspace — refuse. (2) **The hard floor** (`restore.py:167`): `~/dev/<name>` must **already exist**, checked unconditionally, and this is what makes "restore never creates a directory that didn't exist" true *structurally* rather than by heuristic — it fires even when no cwd was ever recorded, which is exactly the case a legacy manifest entry presents. The two together leave one path through: no recorded cwd **and** the directory already exists, which returns byte-identical pre-fix behavior for the common, correctly-rooted case.

- **The follow-up queue's keyboard shortcut never reached the server, because the terminal ate it first.** `Ctrl/Cmd+Shift+Enter` was bound only on `#compose-input`'s own `keydown` handler — and the terminal has focus essentially all the time. `terminal.js`'s xterm `attachCustomKeyEventHandler` independently matched the identical chord: its condition for the unrelated CSI-u passthrough feature was `e.key === 'Enter' && !e.altKey && !e.metaKey && (e.shiftKey || e.ctrlKey)`, which is true whenever `ctrlKey` is true. It called `preventDefault()` and consumed the keystroke before it could ever reach the compose bar, so with the terminal focused the chord produced **zero** `/followups` requests and the typed text landed in the pane instead of the draft. Root-caused in a real browser session against a scratch instance, not by inspection. The fix carves that exact combo out of the terminal's branch (`terminal.js:626`) and moves the shortcut onto a document-level listener (`app.js:4324` `_followupsQueueKeydown()`, bound at `app.js:4454`), gated identically to the queue button's own enablement so the two can never disagree about when queuing is possible. **No terminal capability is lost:** the branch below already collapsed `Shift+Enter` and `Ctrl+Shift+Enter` to the *identical* CSI-u sequence (`e.shiftKey ? '\x1b[13;2u' : '\x1b[13;5u'` picks the shift form whenever `shiftKey` is set, regardless of `ctrlKey`), so plain `Shift+Enter` still produces the encoded form and plain `Ctrl+Enter` is untouched. The carve-out calls `preventDefault()` — no stray newline into the pty — **without** `stopPropagation()`, which is precisely what lets the native event still bubble to `document`. Relatedly, a failed queue attempt now also fires `showToast()` alongside the inline compose error, so "nothing happened" and "it failed" are never visually identical; and `_composeErrorMessage()` gained a 409 branch naming the queue-specific `bell_hook_unarmed` / `queue_full` payloads instead of falling through to a generic "Send failed (HTTP 409)".

- **Switching sessions left the follow-up panel showing the previous session's queue — and a mutation could write it onto the new one.** `_followupsRefresh()` had exactly four call sites, all inside follow-up mutation handlers; none on the session open/close path. So after a switch, `_followupsData` still held session A's items while `_viewingSession` already pointed at B — and `_followupsPut()` posts `_followupsData`'s items to `/api/sessions/<_viewingSession>/followups`. A reorder, edit, or removal issued before the panel caught up would have overwritten **B's real queue with A's stale snapshot**. Fixed in two places, because the display bug and the write bug are not the same bug: `_composeOnSessionOpen()` / `_composeOnSessionClose()` now refresh (`app.js:4230`), so the panel always reflects the currently-viewed session; and `_followupsPut()` gained a structural guard (`app.js:4573`) that refuses — and re-fetches instead of writing — whenever `_followupsData.session !== _viewingSession`. The guard reuses the `session` field the server already returns on every GET/PUT rather than inventing new client state. **`revision` alone could not have caught this:** revision is unique only *within* one session's queue, so a coincidentally-matching revision on the wrong session would sail straight through the `expected_revision` precondition server-side.

### Testing

- **Four commits repair pre-existing rot in the `-m integration` suite, found while proving the restore fix and fixed as a side effect.** `pyproject.toml:64` sets `addopts = "--import-mode=importlib -m 'not integration'"`, so these tests are deselected by default — neither `make test` nor CI has ever run them, and they rotted invisibly. Three distinct causes, none introduced by anything in this release:
  - **A hardcoded absolute epoch that had just expired.** `test_cli.py`'s `_save_pending_manifest()` defaulted `detected_at` to the literal `1785378123.0`, while `manifest.RESTORE_MAX_AGE_SECONDS` gates restore on age relative to wall-clock `time.time()`. A time bomb by construction: every test using the default begins failing the day real time crosses 7 days past whenever that constant was authored — and it had, by 7.04 days, at the moment the suite was run. Now computed as `time.time() - 3600.0` at call time. The one test that deliberately wants a stale record already passes an explicit `detected_at=1.0` and is unaffected.
  - **Fixtures configuring `LOCAL_ONLY_KEYS` via `patch_settings()`, which silently no-ops them.** Both restore test files' `isolated` fixture called `patch_settings({"tmux_socket_dir": ...})` — but `tmux_socket_dir` is a `settings.LOCAL_ONLY_KEYS` member, so `patch_settings()` logs a warning and writes nothing. Every test using that fixture was therefore running its production tmux calls (`probe_tmux_epoch`, `enumerate_sessions`, `spawn_session_command`) against **whatever `conftest.py`'s own autouse `_isolate_tmux_socket_dir` had put in `TMUX_TMPDIR`** — a different, always-empty directory — never the socket dir on which those tests actually created their sessions. `probe_tmux_epoch()` consequently always returned `None`, which is what the `AssertionError`s were. The same trap applied to `new_session_template` and `session_commands`: those tests were silently exercising the untouched **default** template (a bare 1-window `tmux new-session -d -s {name}`) rather than the intended 4-window fake workspace. Fixed by isolating through the `TMUX_TMPDIR` environment variable directly — what `sessions.tmux_env()` itself falls back to — and by using `save_settings()` (a local-operator write, exactly what `LOCAL_ONLY_KEYS` requires) for the fenced keys, matching the pattern `test_command_pairs_integration.py` already used. **The fence itself is untouched and stays exactly as strict**; nothing here bypasses it.
  - **Correction, recorded rather than repeated:** the brief attributed this fencing to "v0.37.0's security fix." It is not from v0.37.0. `git show <tag>:muxplex/settings.py` across every tag from v0.31.6 forward shows `LOCAL_ONLY_KEYS` **identical** at v0.36.1, v0.37.0 and v0.38.0 — v0.37.0's security fix was the terminal-WS third-door fence and did not touch this set at all. The widening that covers `tmux_socket_dir` / `new_session_template` / `delete_session_template` is commit `3b63b2c` ("fix(security): fence command/path settings keys behind `LOCAL_ONLY_KEYS`"), whose earliest containing tag is **v0.31.2**; `session_commands` joined in **v0.33.0**. The rot is correspondingly older than v0.37.0, which is the point of stating it: `-m integration` being deselected by default is what let it sit that long.
  - **One genuinely wrong assertion.** `test_restore_no_record_uses_default` asserted `report.ok_count == 1`, but the default template it never overrides produces exactly one window, and `execute_restore()` correctly reports any 1-window result as `"warn"` — a divergence, not a failure (`restore.py:388`). The assertion could never have passed once the test actually reached real tmux. Corrected to the invariant it meant: `not report.any_failed`, plus the session existing.
  - **Measured, not asserted — and not clean.** Against the **v0.38.0** tree in the DTU: `13 failed, 13 passed, 2187 deselected, 2 xfailed, 4 errors in 98.75s`. Against this release's tree: `5 failed, 25 passed, 2197 deselected, 2 xfailed, 4 errors in 101.09s`. **The remaining 5 failures and all 4 errors are pre-existing and untouched by this release** — the 5 are a strict subset of the baseline 13 (four in `test_integration.py`, one in `test_auto_views_integration.py`), and the 4 errors in `test_command_pairs_integration.py` are byte-identical between the two runs. This release fixes 8 of 13 failures and fixes none of the errors. Saying the integration suite is now clean would be false.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break.
- Every claim above was re-checked against this tree rather than carried over from the commit messages, and pre-fix claims against the **tagged** tree, not from memory: `git show v0.38.0:muxplex/frontend/app.js` has `function sortByAttention(sessions)` — one parameter, no tier 2 — at line 76, and the pre-fix terminal branch is literally `if (e.key === 'Enter' && !e.altKey && !e.metaKey && (e.shiftKey || e.ctrlKey))` at line 623. `git diff v0.38.0..HEAD -- muxplex/main.py` is nine lines, all of them the poll-cycle `cwds=` wiring; `muxplex/settings.py` has no diff in this range at all.
- The attention-sort figures were **measured against the live host for this release**, not quoted from the investigation: `GET /api/view?sort=attention` → 22 sessions, the open session returned with `active=True` behind the single needs-attention session, `bell.last_fired_at = 1785975900.640427` (the minimum of all 22; maximum `1785988858.320663`), ranking **22 of 22** when that set is ordered by the tier-3 key alone.
- The integration-suite figures are two real DTU runs against two real trees (`git archive` of `v0.38.0` and of this commit's `HEAD`), not a reconstruction — quoted verbatim above, errors and remaining failures included.

## v0.38.0 (2026-08-05)

### Added

- **A per-session follow-up queue: stack up "then do X, then do Y" and walk away.** The compose bar sends the moment you hit send, which is right for one message and wrong for three — typing into a pane while an agent is mid-turn *steers that turn* rather than following it. The queue is a per-session, server-side, persisted list of text items (`muxplex/followups.py`, `state["followups"]`) that fires **exactly one item per bell** and keeps firing, one per bell, until it drains. A bell is an agent's turn completing, so "one per bell" is "one per turn": queue three follow-ups, close the laptop, and each one lands only once the previous one has actually finished. Five endpoints under `/api/sessions/{name}/followups` (`GET`/`POST`/`PUT`/`DELETE`/`POST .../resume`, `main.py:2100-2276`), plus an additive `followups: {pending, halted}` summary on `GET /api/sessions`, `GET /api/view`, and `GET /api/federation/sessions` (`main.py:1429`, `1648`, `4029`) so a badge costs no second fetch. In the UI, `Ctrl/Cmd+Shift+Enter` queues the compose draft instead of sending it (`app.js:4226-4233`).
  - **Pending items are fully manageable; a fired item is gone.** While an item is waiting you can reorder it, edit its text, remove it, clear the whole queue, or resume a halt (`app.js:4386-4460`; `PUT` replaces the whole list in one call). The instant an item fires it is **discarded entirely** — no history, no tombstone, no completed-items list, no counter anywhere in `state.json`. That is a deliberate product decision, not an omission: the queue is a thing you point at the future, and a growing record of everything it has already typed is a different feature with different storage, different privacy questions, and a different reason to exist. Fired means gone.
  - **This is muxplex's first autonomous write.** Every prior path that types into a tmux pane is a human's keystroke or an explicit request in flight — `POST /input`, the terminal WebSocket relay, session create/kill. This one decides on its own, with nobody waiting on the other end, on a trigger the server observes rather than one a caller sends. That distinction is the whole reason the rest of this entry is written the way it is.
  - **So the queue is a THIRD caller of the shared `terminal_input.input_allowed_for_session()` fence — the same gate `/input` and the terminal WS already pass** (`main.py:2326`, alongside `main.py:1989` and `main.py:2148`). Not a copy of the fence, not a "the server is trusted" bypass: the same function, re-evaluated **at fire time against freshly loaded settings**, regardless of what was true when the item was enqueued. The append-time 403 (`main.py:2143-2152`) is UX only — it tells you now instead of at the next bell — and is explicitly not the safety boundary. v0.37.0 was spent closing a third door around this fence; this release deliberately does not open a fourth.
  - **A failed send halts the queue and surfaces it. It never skips and continues.** Any of `input_disabled`, `input_not_allowed`, `session_missing`, or `send_failed` (`main.py:2324-2343`) stops the queue where it stands, keeps the item that failed exactly where it was, records the reason, and shows a halt banner. Skip-and-continue is the tempting behavior and it is the wrong one — it would quietly drain a user's queued work into the void, one bell at a time, while the UI showed a shrinking list and everything looked fine. Nothing clears a halt implicitly either: `POST .../resume` is a separate, explicit act, because a silent unhalt is how an autonomous writer restarts without anyone deciding it should.
  - **The seeded bell cannot advance the queue, and the exclusion is structural.** v0.36.1 seeds a bell at session *creation* so a new session sorts to the top of the attention view. That is a "look at me," not a turn completion — advancing on it would fire someone's first queued follow-up into a shell that has done nothing yet. The seeding branch writes `state["sessions"][name]["bell"]` directly (`main.py:639`) and never routes through `receive_bell()` or `process_bell_flags()`, which are the only two functions the advance hangs off. The exclusion is therefore a property of where the code lives rather than a runtime check that could be edited out — and it is pinned by `test_followups.py::test_seeded_bell_does_not_advance_queue`, precisely *because* an invariant made of absence is invisible to a future reader. The obvious "route the seed through `receive_bell()` for consistency" refactor would silently break it.
  - **Both live bell paths advance the queue; the poll path is gated on `not _bell_hook_armed`.** `receive_bell()` (the tmux hook) always triggers an advance (`main.py:2622`). `process_bell_flags()` (the poll fallback) triggers one only while the hook is unarmed (`main.py:707`). Wiring only the hook would stall every queue whenever the hook is down; wiring both unconditionally would be worse — a *detached* session's bell is independently observed by both mechanisms at once, so a single physical bell would drain two items. Unarmed, the poll path is what keeps the queue moving until the hook heals; armed, the hook is the sole trigger. Relatedly, `POST` refuses new items with a `409 bell_hook_unarmed` while the hook is not confirmed delivering (`main.py:2153-2160`): a queue armed against a dead trigger is worse than no queue at all.

### Fixed

- **The tmux bell hook was silently dead on every TLS host, and its health check was reporting green the whole time.** `_arm_bell_hook()` registered a hook that curled `http://localhost:{PORT}` unconditionally, while the server on any TLS host is serving **https** on that port. curl connects, the TLS listener discards the plaintext request, curl exits 52 — and `-sf` suppressed the message while `|| true` discarded the exit code, so tmux saw success and nothing was ever logged. Measured on the owner's host at the time of this release: `https://127.0.0.1:8088/api/instance-info` → `200`; `http://127.0.0.1:8088/api/instance-info` → `000`, curl exit `52`. Worse than the bug was the instrument: `GET /api/instance-info`'s `bell_hook_armed` reported `true` for the life of every process, because it recorded whether **tmux accepted `set-hook`** — which it always did — not whether anything was ever *delivered*. A green light wired to a disconnected wire, which is exactly what let this hide for as long as it did.
  - **Be clear about the blast radius: this was not an outage.** `bells.process_bell_flags()` — the documented poll fallback — carried bell detection correctly the entire time, and live state showed bells landing seconds old. What actually shipped broken was the *redundancy*: the fast, always-correct path was dead, the slower fallback was silently carrying the whole load alone, and the health check said everything was fine. Dead redundancy plus a dishonest instrument is a real defect and worth a release note; a user-visible bell outage is not what happened, and saying otherwise would be theater.
  - **The fix, in four parts.** The scheme is derived from the server's real configuration rather than assumed (`SERVER_TLS_ENABLED`, set in `cli.py`'s `serve()` from the same `ssl_kwargs` uvicorn is about to be handed — resolved *before* `muxplex.main` is imported, since `main.py` reads it at import time). `-k` is added on TLS (`main.py:376-377`) because this is a loopback call to the host's own port and the cert may be self-signed, signed by muxplex's own CA, or issued for a hostname that doesn't cover `127.0.0.1` at all — the same `CERT_NONE` posture `cli.py`'s `_probe_service_port` already uses for the same reason. `127.0.0.1` replaces `localhost`, so a host whose resolver prefers `::1` can't send the bell somewhere nothing is listening. And **"armed" now requires a probe to actually arrive**: after registering, `_arm_bell_hook()` fires the exact command tmux would run on a real bell against a reserved sentinel session (`_BELL_PROBE_SESSION`, recognized by `receive_bell()` and never persisted to `state.json`) and waits for that request to reach the endpoint. Two independent failure surfaces, both honest — tmux's `run-shell` propagating curl's nonzero exit, or the request never arriving before the timeout.
  - **This fix caused a regression that hit the owner live, and correcting it is part of this release.** Making the probe's failures diagnosable meant making curl loud — but the first attempt built **one** command for both callers (`curl -sS…f`, no stderr redirect) and varied only the trailing `|| true`. tmux's `run-shell`, per its own manual, displays a non-quiet background command's output on the client's active pane. So every real bell whose curl call failed painted curl's error text across whatever the owner was looking at: `returned 52`, repeatedly, on every one of his live sessions, for the life of the process. (For the record, and corrected here rather than repeated: there was never a `run-shell -S` flag involved — the loudness came entirely from sharing one `curl -sS…f` command string between the persistent hook and the one-shot probe, and the painting is `run-shell`'s ordinary display behavior.) The two commands are now permanently separate (`main.py:394` vs `main.py:404`): the **persistent** hook is silent three independent ways — no `-S`, stderr redirected to `/dev/null`, and `|| true` — while the **one-shot arm-time probe** stays loud, because it runs once, nobody is watching a pane when it does, and its diagnostic is the entire thing that makes `bell_hook_armed` actionable. The instinct to make failure loud was right; silence is what hid the original bug. The hook is simply the wrong place for it. `test_api.py::test_persistent_hook_never_includes_dash_S` and `::test_persistent_hook_redirects_stderr_to_devnull` are regression guards for exactly this, not incidental assertions. `muxplex doctor` now surfaces the honest armed state as a non-fatal advisory, the same way it already surfaces TLS cert expiry.

- **A bell in a background window was invisible to the poll fallback.** `bells.poll_bell_flag()` called `display-message -t <session> -p '#{window_bell_flag}'`, and a session-only target resolves to that session's **active** window — not necessarily the window the bell fired in. Verified live: a bell in an inactive window set *that* window's flag to `1` while the active window's stayed `0`, and the session-scoped read returned `0`. The tmux-native flag was correct; this path just wasn't looking at it. Any multi-window layout (an `amplifier-workspace` session is four windows) could bell in a background window and go completely undetected by the fallback. Now polls `list-windows -t <session> -F '#{window_bell_flag}'` and reports a bell if **any** window has one (`bells.py:60-64`). The separate stuck-flag behavior of this fallback — tmux exposes a boolean, not a counter, so a flag no client ever clears means only the first bell is counted — is correct-as-designed and already documented in `process_bell_flags()`'s docstring; it is not changed here.

- **The autouse tmux-isolation fixture broke 12 tests on macOS by overrunning the `sun_path` limit.** The `_isolate_tmux_socket_dir` fixture added earlier in this range forces every test's real tmux calls onto a per-test `TMUX_TMPDIR`, and it built that directory from pytest's own `tmp_path`. tmux derives its actual AF_UNIX socket path as `$TMUX_TMPDIR/tmux-<uid>/<socket-name>`, which must fit the kernel's ~104-byte `sun_path` budget. macOS's `tmp_path` resolves under `/private/var/folders/…/pytest-of-<user>/pytest-<n>/<test name>0/` and is already 100+ bytes before a socket filename is appended. Reproduced and fixed on real macOS hardware (arm64, Darwin 25.6.0, tmux 3.6a): a **115-byte** socket path — 116 with the `sun_path` NUL — against a 102-byte budget, **14 bytes over**, failing with `error connecting to … (File name too long)`. The failure mode is the nasty part: from a test's point of view tmux simply never started, so every assertion reading a real tmux option back saw an empty string. CI job `test (macOS, arm64)` failed 12 tests, all in `test_tmux_config.py`, with symptoms like `assert '' == 'base-3'` and `KeyError: 'status-left-length'` that name nothing resembling a path length. Linux stayed green because its `tmp_path` fits under budget by coincidence, not by design. Same bug class as `087ac83`, which fixed it for ttyd's sockets. Fixed the same way that one was: `mkdtemp` directly under `/tmp` with explicit cleanup (`conftest.py:192`), deliberately not `tempfile.gettempdir()`, which on macOS *is* the deep `$TMPDIR` path. The isolation guarantee is untouched — unique per-test `TMUX_TMPDIR`, `$TMUX` unset — only the base directory moved, so `test_safety_rails.py::test_tmux_socket_dir_is_isolated_by_default` needed no change.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break.
- Every claim above was re-checked against this tree rather than carried over from the commit messages. The pre-fix state is checked against the tagged tree, not from memory: `git show v0.37.0:muxplex/main.py` has the hook registered as a literal `curl -sfo /dev/null -X POST http://localhost:{SERVER_PORT}/…/bell || true`, with `bell_hook_armed` set purely from `set-hook` being accepted. The TLS measurement (`https` → 200, `http` → 000 / curl exit 52) was taken against the live host for this release, not quoted from the earlier investigation.
- The macOS figures are from CI and from real hardware, not reconstructed: run `31049692494` (`test (macOS, arm64)`, commit `91c4d7c`) reports `12 failed, 2175 passed, 32 deselected`, every failure in `test_tmux_config.py`, one of them naming `File name too long` outright. Run `31050579200` on `d3e98f9` is green across all nine jobs, macOS included.
## v0.37.0 (2026-08-05)

### Security

- **The terminal WebSocket was a third door around the `input_allowed_sessions` fence, and a federation Bearer-key holder could walk through it into any live session.** `AGENTS.md` stated, without qualification, that the per-session allowlist is "how a human's own working panes stay un-typeable: don't list (or pattern-match) them." Against `POST /api/sessions/{name}/input` that was true. Against `WS /terminal/ws?session=<name>` it was **false**, and had been for as long as that route has accepted a `?session=` parameter. Four individually-defensible pieces composed into it. `_ws_auth_check()` accepted localhost **or** a valid session cookie **or** a Bearer token and returned a bare `bool` (`async def _ws_auth_check(websocket: WebSocket) -> bool` at `main.py:2579` in v0.36.1) — so no caller downstream could tell *which* credential had authorized the connection, only that one of them had. The WS handler contained **zero** references to `input_enabled` or `input_allowed_sessions`: slicing v0.36.1's `main.py` from `terminal_ws_proxy` to the next function definition gives 266 lines and `grep -c` over them returns `0`. `client_to_ttyd()` was an unconditional byte relay — `if msg.get("bytes"): await ttyd_ws.send(msg["bytes"])`, with no inspection of any kind. And `?session=` lets a caller name any live session directly, so nothing constrained the target either. Stated plainly: the same credential this repo hands to headless AI agents was fenced on `/input` and unfenced on a WebSocket that types into the identical tmux pane. This is the third of three such doors — `LOCAL_ONLY_KEYS` closed the settings-template sibling (`new_session_template` et al.), and this closes the WS one.
  - **The fix is a narrowing, and it fails safe.** `_ws_auth_check()` now returns `WSAuth(ok, bearer_only)`, a `NamedTuple` (`main.py:2579-2601`), where `bearer_only` is True only when the Bearer key was the *sole* credential that authorized the connection (`main.py:2629`). `terminal_ws_proxy` evaluates the fence once per `bearer_only` connection (`main.py:2958-2960`) and `client_to_ttyd()` then drops **only** the ttyd wire protocol's keystroke command — leading byte `0x30` (`main.py:2991`), the same byte `frontend/terminal.js:122` emits from `onData()`. The `0x31` resize command, the initial text-frame `AuthToken` handshake, and 100% of the ttyd→client output direction are untouched. A denied connection stays a live, **resizable viewer**; it simply cannot type. One `logger.warning` per denied connection, not per dropped keystroke.
  - **Both enforcement points now call one function, so they cannot drift.** `terminal_input.input_allowed_for_session()` (`terminal_input.py:90-116`) is the single evaluation of `input_enabled` + `input_allowed_sessions`, called from `send_session_input()` at `main.py:1723` and from the WS gate at `main.py:2960`. The fail-closed semantics are carried over verbatim from the inline check it replaced: only the literal boolean `True` enables, and a non-list allowlist is treated as `[]` (a string would substring-match via `in`). Two independent copies of "is this session typeable" is precisely how one fence quietly diverges from the other; there is now one copy.
  - **A valid cookie wins the classification even when a Bearer header is also present**, and that is a deliberate, load-bearing asymmetry rather than an oversight. Presenting a valid `muxplex_session` cookie requires knowing `_auth_secret`, which a Bearer-key holder does not have and cannot derive. "Cookie + Bearer" therefore can only mean a genuine browser session that also happens to send a Bearer header — never a Bearer-only caller impersonating one. Narrowing on a certain classification is safe; widening on a guessed one never is, so `bearer_only=False` is asserted only where the answer is certain (localhost, or a cookie verified by `verify_session_cookie`).
  - **Viewing is never gated. Only typing is.** This is a confidentiality argument, not a convenience one: a Bearer holder can already read any session's live pane through `GET /api/sessions`' `snapshot` field, regardless of this fence. Blocking the identical content over the WebSocket would add no confidentiality whatsoever, and it would break `federation_terminal_ws_proxy`'s legitimate peer-to-peer relay outright — that relay dials this same route with a Bearer header **unconditionally** (server-to-server, never a cookie) whenever a human uses the aggregated PWA to watch a remote host's session. Net effect for federation: **viewing a remote host's terminal always still works; typing into it now requires that remote host's own `input_enabled`/`input_allowed_sessions` to allow the session.** The wire is bit-identical between "my own federation peer relaying a human's keystrokes" and "a rogue agent holding the same key," so the two cannot be distinguished — and an indistinguishable case is denied, never guessed open. Restoring federation typing to a specific session is the same local `settings.json` edit every other Bearer-only typing path already requires.
  - **Known residual, not closed by this release: a federation peer running a pre-fix version is exactly as exposed as it was before, and this host cannot tell.** The fence lives on the *receiving* side of the WS, so a peer that has not upgraded has no `bearer_only` classification to make and applies nothing. There is no server-side version negotiation on this route to detect it with — the relay dials `/terminal/ws` and gets a terminal or it doesn't. Upgrading every peer is the only remedy, and it is worth saying rather than leaving implied: this release secures **this** host's sessions against Bearer-only WS typing, and says nothing about anyone else's.

### Added

- **A mobile text-compose bar: type or dictate below the terminal, then send.** A textarea and a send button below the terminal in the expanded (session) view (`index.html:118-141`). What it enables that the raw terminal cannot: **multiline composition** (compose a whole paragraph before anything reaches the shell), **native voice input** via the platform keyboard's dictation, and **scrolling back through the terminal while composing** — the raw pane sends every keystroke the instant it is typed, so on a phone there is no way to draft, review, or dictate.
  - **It is a plain UI client of the existing, unmodified `POST /api/sessions/{name}/input`** (`app.js:4287`). No new endpoint, no new settings key, no change to any fence — the same `input_enabled` / `input_allowed_sessions` gate an agent, `muxplex-deck`, or `curl` already passes through. `grep -rn compose muxplex/*.py` returns nothing; the entire feature is frontend.
  - **A `/compose` endpoint accepting a session cookie was designed, and then rejected on security review.** The draft proposed a new endpoint gated on caller class — cookie-authenticated browsers permitted, Bearer-only callers not — on the theory that a browser session implies a human at a keyboard. It does not. Possession of a cookie is not proof of human presence, and gating an RCE surface on one would mean any future same-origin XSS becomes **unconditional** remote code execution on every session. Today the blast radius of that same XSS is bounded by `input_enabled` defaulting to `false` and `input_allowed_sessions` defaulting to `[]` — an operator-set fence the browser cannot widen, since both are `LOCAL_ONLY_KEYS` and `PATCH /api/settings` silently ignores them. Building the endpoint would have traded a real, existing bound for a convenience. None of it was built.
  - **On a default install this bar's Send is inert, and it says so rather than hiding.** `input_enabled` defaults to `false`, so the bar renders with its textarea and send button disabled plus a persistent notice naming **both** settings keys and stating they are edited in `~/.config/muxplex/settings.json` **on the host**, not in this UI (`index.html:119-126`; the disabled state is driven by the already-loaded `_serverSettings.input_enabled` at `app.js:4116`). Hiding the bar outright would mean nobody discovers the feature exists; shipping a Send button that silently fails would be worse than either. If `input_enabled` is true but a specific session is not allow-listed, the real 403 from `/input` surfaces inline with its own specific message (`app.js:4228-4258`) — never a silent failure, and there is a static test asserting the send path has no empty `catch`.
  - **The preference is per-device, in `localStorage`, and deliberately not a settings key.** `muxplex-compose-bar` holds `auto` | `on` | `off` (`app.js:4011`); `auto` resolves through the existing `isMobile()` / `MOBILE_THRESHOLD` judgment rather than a second width check, and an explicit `on`/`off` is never overridden by a width guess — which is what gets tablets wrong. Not federated, not synced, same precedent as sync-group mode and the soft deck's local-only settings. Reading a blocked or unknown value falls back to `auto` for the session and never throws. The toggle button lives in the expanded header only (`index.html:65`), since the bar exists only in that view.
  - **Enter inserts a newline; Ctrl+Enter or Cmd+Enter sends** (`app.js:4213-4218`). Enter is the textarea's default and is left alone deliberately: the whole point of the bar is composing more than one line, so the key that makes a new line must be the unmodified one.

### Fixed

- **The on-screen keyboard no longer mis-sizes the terminal when the search bar is open — a pre-existing bug, fixed by reworking the handler rather than extending it.** The old `visualViewport` handler wrote an inline height directly onto `#terminal-container`, computed as `visualViewport.height` minus a **hardcoded 44px** header offset (`terminal.js:377-380` at v0.36.1). That arithmetic only ever accounted for the header, so anything else stacked in the same column was simply unaccounted for — `#terminal-search-bar`, when open, already clipped the terminal by its own height before this release, and the compose bar would have been a second instance of the identical bug. Extending the subtraction to cover the compose bar would have added a third hardcoded constant to a formula whose failure mode is "add a sibling, break the layout."
  - The measurement moves one level up instead: the handler now sets `--app-viewport-height` on `#view-expanded` (`terminal.js:419`), which `style.css:444` consumes as `height: var(--app-viewport-height, 100dvh)`. Flexbox then subtracts every visible sibling's **actual rendered height** automatically, however many there are. `#terminal-container` gets no inline height at all any more, and the fallback to `100dvh` covers desktop, keyboard-closed, and browsers without `visualViewport`.
  - Also in this rework: `visualViewport`'s **`scroll`** event is now listened for alongside `resize` (`terminal.js:427-428`), because iOS Safari can change `visualViewport.height` on scroll without ever firing a resize; `--app-viewport-height` is cleared on `closeTerminal()` (`terminal.js:777`) so no stale keyboard-open pixel value leaks into the next session; and `window._refitTerminal` is exposed (`terminal.js:380`) so `app.js` can request a refit when something other than a viewport event changes the terminal's available space — the compose bar growing a line, for instance.

### Backlog

Three follow-ups filed to `docs/BACKLOG.md` rather than folded into this release:

- **5. Federation key hygiene** — per-host key generation and a documented rotation path. `muxplex generate-federation-key` already generates correctly; what is missing is anything stating the key is meant to be *per-host*, and anything describing what an operator does when one must be replaced. It matters more after this release, not less: the federation key is the shared Bearer credential for the whole `/api/*` surface and now fronts the WS typing path explicitly, so a key reused across hosts turns one host's compromise into a fleet compromise.
- **6. Referenced design docs that don't exist** — a repo-wide sweep found **eight** documents cited with nothing to point at (`KEY_DESIGN_SYSTEM.md` 48 refs across 7 files, `SOFT_DECK_DESIGN.md` 32/5, `SESSION_PERSISTENCE_DESIGN.md` 22/11 — including `muxplex restore --help`, which makes the CLI itself the dead end — `DECK_PARITY_ARCHITECTURE.md` 18/5, `muxplex-client-design.md` 15/8 including `client/README.md`, `COMMAND_PAIRS_UI_DESIGN.md` 9/4, `CONTROL_MAPPING_DESIGN.md` 6/3, and `COMPOSE_BAR_SPEC.md` 4/3, whose spec never landed alongside the compose bar in this very release). Separately, four `*_SPEC.md` names across 35 files are stale pointers to content that *was* preserved into `docs/plans/` under dated names — a rename, not a writing job.
- **7. Put `session_created` on the wire** — v0.36.1 plumbed it internally and deliberately kept it off `GET /api/sessions`; that release's own CHANGELOG named exposing it as the obvious future change. Purely additive, and wanted before the external `muxplex-client` integrations land rather than after.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break.
- Every claim above was re-checked against this tree rather than carried over from the commit messages. The pre-fix state is checked against the tagged tree, not from memory: `git show v0.36.1:muxplex/main.py` has `_ws_auth_check` returning `bool`, `client_to_ttyd()` sending every byte unconditionally, and no occurrence of `input_enabled` or `input_allowed_sessions` anywhere in `terminal_ws_proxy`'s 266 lines. The post-fix mechanism is checked at `main.py:2579-2601` (`WSAuth`), `2629` (the classification), `2958-2960` (the gate), `2991` (the `0x30` drop), and `terminal_input.py:90-116` with its two callers at `main.py:1723` and `main.py:2960`.
- The "no new endpoint" claim for the compose bar is mechanical: `grep -rn compose muxplex/*.py` exits 1 with no output. The `/input` endpoint's own diff across this range is confined to swapping its inline fence read for the shared `input_allowed_for_session()` call — the fence's semantics are unchanged, and the same function now backs both doors.
- The `visualViewport` fix is a **pre-existing** bug, not one introduced and then fixed in this range: v0.36.1's handler already subtracted only the 44px header, so the search-bar mis-size predates the compose bar entirely.
## v0.36.1 (2026-08-05)

### Fixed

- **A session you just created no longer sorts to the bottom of the attention view.** The newest thing in the system landed dead last, which is the exact inverse of what an attention ordering is for. Nothing was broken in isolation — three individually-correct pieces composed into it. `state.empty_bell()` seeds a new session's bell as `{"last_fired_at": None, "seen_at": None, "unseen_count": 0}`. `bells.needs_attention()`'s **first line** returns `False` on `unseen_count <= 0`, so a fresh session could never reach `_attention_order()`'s tier 1 regardless of anything else about it. And tier 3 sorts *descending* on `(last_fired_at is not None, last_fired_at or 0)` — for which a never-belled session's `(False, 0)` is the **worst possible key**. Each of those is the right behavior for the case it was written for; together they put the newest session at the very bottom.
  - **The fix is data, not logic — `needs_attention()` and `_attention_order()` are byte-for-byte untouched.** That was an explicit constraint on this change, not an incidental outcome: the needs-attention predicate is published in `docs/API_SEMANTICS.md` as the canonical rule external clients re-derive, and the tiered sort is the one place view ordering is decided. Widening either to special-case "new" would have meant a second concept of newness living inside a predicate about bells. Instead, a genuinely-new session's bell is seeded **as if it had just fired** — `last_fired_at=now`, `unseen_count=1`, `seen_at=None` — and the *existing* sort places it at the top of tier 1 on its own, because the freshest `last_fired_at` within tier 1 is precisely what that sort already rewards. No new ordering rule was written.
  - **Viewing it lands in the right place for free, too.** Once the session is selected, the ordinary `apply_bell_clear_rule()` path clears that bell exactly like any other, `needs_attention()` flips `False`, and the session falls through to tier 2 — the active session, first after the needs-attention group. The follow-on behavior cost zero additional code and no special-casing, which is the payoff for having made the change in the data rather than in the sort.
  - **The trap, and why the rule is as narrow as it is.** A session's bell is first assigned in the poll cycle's "ensure bell entries" step, and that step fires for **first observed by muxplex**, not for **just created**. Seeding there naively would have flagged *every existing session at once* — fifty-four of them on the host this was written on — the first time a restarted or freshly-installed muxplex looked at the machine. The discriminator is tmux's own `#{session_created}` compared against `_server_start_time`, the moment this process came up: seed only when the session was created **during this process's lifetime**. That timestamp is intrinsic to tmux — set once, by tmux, never revised — so it survives everything muxplex's own bookkeeping can lose. `state.json`, the presence manifest, and `pruning.json` can all be deleted without changing it. Four cases, all of which must stay quiet and do:
    - **muxplex restarts** — every pre-existing session's `session_created` predates this process's startup. Not flagged; identical to pre-fix behavior.
    - **state file reset or fresh install** — same reasoning, and deliberately so: the signal deciding this cannot be muxplex's own state, or a state wipe becomes a mass-flag event.
    - **a session created while muxplex was down**, discovered at the next startup — still created before *this* process started, so still not flagged. This is a real, if narrow, gap and it is the correct side to err on: the feature exists for the live create-and-look flow, not for a startup backfill.
    - **federation peers** — a remote session never reaches this branch at all. Bells are local-only state; a remote session's bell is governed entirely by the remote instance's own poll cycle.

- **A brand-new session no longer fires a desktop notification for itself.** `detectBellTransitions()` defaulted an absent key's previous count to `0`, so any session appearing for the first time with `unseen_count > 0` read as an *increase* and notified. Harmless before this release — sessions arrived with `unseen_count: 0` — and immediately wrong after it, because the seeding above makes `0 → 1` the normal shape of a session the user just created by hand. A session's first appearance is a **new session, not a new bell**: the function now returns false outright when the key is absent from the previous map, instead of comparing against an invented zero. An existing test asserted the old behavior (`detectBellTransitions fires for new session not in prev with bell > 0`) and was corrected rather than deleted; a second test covers the mixed case, where a genuinely new session and a real transition arrive in the same poll and only the real one fires.

### Known limitation

**`#{session_created}` is plumbed internally by this release and is deliberately *not* on the public payload.** It was not previously read anywhere in the codebase — `git grep session_created v0.36.0` returns nothing — and is now parsed out of the same `tmux list-sessions` call that already produced `#{window_activity}` (no second subprocess round trip), cached in `sessions.py` and exposed to the server as `get_session_created_times()`. `GET /api/sessions` gains **no** `created` field; its entries remain `name`, `snapshot`, `bell`, `last_activity_at`, `views`. The consequence is honest and worth stating: an external client — `muxplex-deck`, or an agent driving muxplex over the API — still cannot see when a session was created, and cannot reproduce this ordering decision locally. Adding it would be a purely additive field and is the obvious future change; it is not made here because nothing yet asks for it, and per `AGENTS.md` the API is a contract rather than a scratch surface.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break.
- Each mechanism claim above was re-checked against this tree rather than carried over from the commit message: `empty_bell()`'s three fields at `state.py:129-135`; the `unseen_count <= 0` early return as the **first** statement of `needs_attention()` at `bells.py:174-175`; tier 3's `(last_fired_at is not None, last_fired_at or 0)` key at `main.py:1276-1283`. `git show 7707bc7 -- muxplex/bells.py` produces no diff, and `_attention_order()` does not appear in the commit's `main.py` diff — the "byte-for-byte untouched" claim is mechanically checked, not asserted.
- The absent-`created`-field claim is checked the same way: the response dict built at `main.py:1153-1166` carries no such key, and `git grep session_created` against the v0.36.0 tree exits 1 with no output.


## v0.36.0 (2026-08-04)

### Added

- **A view can now define itself with rules, and a rule-based view cannot decay.** A view entry may carry an optional `match_names: [<glob>, ...]` alongside its existing `sessions` pins. Membership is the **union** of the two, and the rule half is re-resolved **on every read** — `views.view_names_for_session` and `views.filter_visible` recompute it against the live session list each time, and nothing is ever written back to disk. That is the entire feature: a hand-curated view is a snapshot that begins rotting the moment it is saved (`session_ttl` and `stale_key_grace_hours` exist precisely because manual lists decay), while a view defined as `amplifier-*` is correct the instant a new `amplifier-something` appears and correct again the instant it is killed. Patterns are fnmatch-style, matched case-insensitively against the bare tmux session name via explicit `.casefold()` + `fnmatch.fnmatchcase` — deliberately not plain `fnmatch.fnmatch`, whose case folding is a platform-dependent side effect of `os.path.normcase`.
  - **One syntax, matched against the bare session name — because the device qualifier is a UUID.** `spark-1:*` reads like the obvious thing to want to type, and it could never work: view keys are `<device_id>:<name>` where `device_id` is `str(uuid.uuid4())`, while `spark-1` is a `device_name` — a different settings field, and for remote peers a per-observer local one, so two devices can legitimately call the same peer different things. A correct qualified glob would mean typing a UUID prefix. A pattern containing `:` is therefore **rejected at validation** (`views.validate_view_rules`, rule R4) with an error naming this reason, rather than accepted and left to silently match nothing forever. `amplifier-*` already means "on any device," which is the case that matters.
  - **The server never materializes a match back into `sessions`.** Rules stay rules on disk, permanently; this is now a standing prohibition in `AGENTS.md`. Materializing would re-introduce the exact decay this feature exists to eliminate, turn every poll cycle into a settings write, and hand federation's last-writer-wins a brand-new race. The self-healing property is a direct consequence of not doing it.
  - **A rule editor in the Manage View panel**, with a live match preview and inline validation. `POST /api/views/preview` resolves a draft, unsaved `match_names` list against the currently-live local sessions and returns `{errors, matches}`, so the editor can say "these N sessions match" and name a rejected pattern's exact reason as you type instead of letting you discover it via a 400 on save. It wraps the draft in a throwaway, never-persisted view dict and runs it through the same `validate_view_rules` / `filter_visible` a saved view uses — there is deliberately no second matcher behind it. Patterns already in the settings file that were rejected are surfaced in the Views settings tab with a count badge, so a bad pattern is visible rather than merely inert.
  - **The matcher lives in exactly one place, and it is server-side.** `grep -rn fnmatch muxplex/frontend/` returns nothing. No client ports glob semantics; each reads the server's resolved answer, which is the mechanism that keeps the PWA, the soft deck, and the sidecar from disagreeing about what a view contains.
  - **Exclusions are deliberately out of v1.** `amplifier-*` minus `*-scratch` will be wanted, and the shape is pre-committed — a sibling key subtracted after the union — so adding it later is additive rather than a redesign. It is not implemented here.
  - **Ordering falls through to `sort_order`.** A rule carries no ordering of its own. `sort_order` is already the ordering authority; letting a view order too would make a fourth one, and "why is this view sorted differently from every other" is a bug report nobody can answer.
  - **Attention is a SORT, and it stays one permanently.** The backlog listed "anything with a bell" as a candidate rule type. As a *view* it is self-erasing: attention clears when you look at a session, so the view empties itself as you work through it and the tile you are currently reading disappears from under you. As a *sort*, the same sessions rise to the top and stay where they are.

- **`deviceLabelPlacement` — where a session's device label is drawn is now three-way: `titlebar` | `corner` | `off`.** `titlebar` is the default and is precisely today's behavior. `corner` exists because the title bar is a fixed-width budget shared between the device label and the session name, and the label wins by position — so on a narrow tile the *session name* is what truncates. You lose the thing you navigate by in order to keep the thing that is identical on every tile from that device. `corner` moves the label into the preview body (anchored lower right), which has room, and hands the whole title bar back to the name. Presentation only: views store device-qualified keys, so session identity is untouched by what the tile draws.
  - **`showDeviceBadges` is retained forever, as a server-derived mirror.** The two-position version of this feature already shipped as `showDeviceBadges` (`true` = title bar, `false` = off), and external clients read it. Adding an independent key would have produced a 2×3 grid with three nonsense cells (`showDeviceBadges: false` + `deviceLabelPlacement: "corner"` means nothing) and left clients to disagree about which one wins. `deviceLabelPlacement` is now authoritative; `settings.reconcile_device_label()` is the only function permitted to write either key and maintains `showDeviceBadges == (deviceLabelPlacement != "off")` on every write path, PATCH and federation sync alike.
  - **A load-time migration**, so an upgrade does not quietly undo a choice you already made: a `settings.json` predating this release carries only `showDeviceBadges`, and `load_settings()` derives the placement from it (`true` → `titlebar`, `false` → `off`) before reconciliation runs. Anyone who had unchecked that box does not get device labels back on upgrade. Idempotent — once the new key is present in the file, the branch stops firing.
  - **The corner chip is fully opaque, and that is a correctness property, not a style choice.** It overlays arbitrary, arbitrarily-coloured live terminal output. `#8E95A3` on `#0D1117` is 6.28:1, and it is 6.28:1 over a full-bright `htop` and over an empty prompt alike — precisely because the terminal pixel contributes nothing to the calculation. Any alpha at all (`rgba()`, `opacity`, `backdrop-filter`, `mix-blend-mode`) re-admits that pixel, and for each one there is a terminal background colour that defeats it. This is not a safety margin being eroded; it is the proof itself being destroyed. A guard test greps the `.tile-device-tag` rule for those tokens.
  - **Honestly: the empirical half of that proof was not completed.** The spec called for byte-identity screenshot evidence over adversarial terminal content. That was not done. The mechanical guard test stands in for it, and it is the weaker instrument — it proves nobody re-admitted alpha, not that the rendered result is legible on a real display.

### Changed

- **Every session dict from `GET /api/sessions` and `GET /api/federation/sessions` now carries an additive `views: [<view name>, ...]`** — the server's resolved answer for which user views that session belongs to, pins union rule matches. `muxplex_client`'s `Session` gains a matching `views: tuple[str, ...]`, defaulting to `()` so a pre-feature server that omits the field parses cleanly rather than raising.
  - **This was a design correction, and it is the most consequential decision in the release.** The original plan put rule evaluation behind `GET /api/view` — which `docs/API_SEMANTICS.md` correctly names as the server-side resolution point — and would have shipped a feature that **renders empty in the PWA**. Traced before building rather than discovered after shipping: `filter_visible()` had exactly one server-side caller in the entire repo, inside `GET /api/view`, and three of the four surfaces that display view membership re-derived it *client-side* from `settings.views[].sessions` — the PWA's grid, dropdown counts, sidebar and Manage View panel (`app.js`'s ported `filterVisible()`), the `muxplex-deck` sidecar (`resolve_view()`), and the soft deck's picker counts. Only the soft deck's session list actually asked the server. Teaching `filter_visible` about globs alone would have made a rule-based view correct on that single surface and empty on all the others.
  - Putting the resolved answer on the payload clients already poll means client membership logic **shrinks** rather than grows: `app.js`'s dual-key search against `views[].sessions` collapses to `(s.views || []).indexOf(view) !== -1`. The alternative would have been porting glob semantics into each of PWA, soft deck, and sidecar — three implementations to drift apart.
  - The annotation builds new dicts rather than mutating in place. `GET /api/federation/sessions` re-serves cached remote session objects, so in-place annotation would bake a point-in-time membership answer into that cache and keep serving it after the settings that produced it had changed.

### Note on the destructive-write backstop (a finding, not a change)

`DESTRUCTIVE_MEMBER_DROP_RATIO` has **no absolute-count floor**, unlike `DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD`, which guards small numbers explicitly. Removing a view's **last** session pin while exactly one `match_names` pattern remains is a 2-members → 1-member write — exactly 50%, which the `<=` comparison flags as destructive and rejects with 409. That is a single incremental removal, identical in kind to the nineteen harmless ones before it, and it is reachable from the existing Manage View checkboxes.

This is pre-existing `assess_views_destruction` arithmetic and is not introduced by the rule editor — the editor cannot cause it, because it never writes `sessions` at all; the *pin* checkbox is what hits it. It surfaced now because rules make "one pin plus one rule" a normal shape for the first time. It is left as-is and documented by a test (`test_patch_settings_last_member_removal_can_trip_backstop_even_incrementally`) rather than quietly adjusted: whether that ratio needs an absolute floor the way the collapse check already has one is a judgement about the backstop's own design, and loosening a guard that exists because view definitions were destroyed twice for real is not a decision to make in passing. `allow_destructive: true` remains the escape hatch, and the test asserts it still works.

### Note on the `muxplex-deck` sidecar

**A rule-based view renders as empty on muxplex-deck** until a corresponding change lands in that repo: its `resolve_view()` still re-derives membership from `settings.views[].sessions` and knows nothing about `match_names`. The `muxplex_client` half of that change ships here (`Session.views`), so the sidecar's fix is an array lookup rather than a port of the matcher. Sessions **pinned** into a rule-bearing view continue to appear on the deck exactly as before; only rule-matched ones are missing.

### Documentation

- `docs/API_SEMANTICS.md` gains `match_names` semantics, the resolved `views` field on the session payloads, and `POST /api/views/preview`. `AGENTS.md` gains the never-materialize prohibition and the rationale for the deliberately duplicated matcher (`views.matches_name_pattern` vs `terminal_input.session_matches_allowlist` — a display filter and an RCE fence, with opposite failure requirements, which must not share a mutable implementation). `README.md`'s settings table documents `match_names`, `deviceLabelPlacement`, and `showDeviceBadges`'s new derived status. The auto-views and device-label specs are preserved as dated ADRs under `docs/plans/`, the two backlog entries these features close are deleted from `docs/BACKLOG.md`, and the sync-groups ADR's "constraint is still live" header — stale since per-session ttyd shipped in v0.35.0 — is corrected.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break.
- The delivery-mechanism correction was established by enumerating every consumer of view membership in the repo *before* the feature was written, not by finding an empty PWA afterwards: `filter_visible` had a single caller outside `views.py`, and each of the other three surfaces was located in its own source and named.
- `grep -rn fnmatch muxplex/frontend/` returns nothing — the single-matcher claim is mechanically checked rather than asserted.
- The corner chip's opacity invariant is guarded by a test that greps the `.tile-device-tag` rule for `rgba(`, `hsla(`, `opacity`, `backdrop-filter`, and `mix-blend-mode` (comments stripped first, so the rule's own "never rgba()" note does not trip it). The spec's empirical byte-identity screenshot proof was **not** completed; the guard test stands in for it.
- The backstop edge above was found by the DTU run itself, not by reading the arithmetic — a test that asserted a full 20-pin drain passed nineteen removals and failed the twentieth.


## v0.35.0 (2026-08-04)

### Changed

- **`upgrade` and `doctor` are now install-source-aware, and `upgrade` can no longer silently replace what you installed with something else.** PEP 610 has the installer write a `direct_url.json` beside every non-PyPI install, recording where that install actually came from. muxplex read two fields out of that file, ignored the rest, and then reconstructed an install target from assumptions instead of from the record — and every time an assumption was wrong, `upgrade` installed something the user had never asked for, on top of what they had. The recorded origin is now used **verbatim**: whatever URL, ref, or path is in that file is what gets installed. A fork or a mirror upgrades from *its* remote, not from canonical upstream.
  - **Five install shapes are recognized, and the classification came from probing what each one actually writes** rather than from reading the spec and reasoning about it: `pypi` writes no `direct_url.json` at all, `git` writes `vcs_info`, an editable install writes `dir_info` with `editable: true`, a **non-editable local directory** install writes `dir_info` with no `editable`, and a **local wheel or sdist** writes `archive_info`. The last two look nothing alike in the file and were both being misread (see Fixed).
  - **A git pin keeps its meaning.** `vcs_info.requested_revision` is now read and classified against the remote: a **tag** pin tracks the latest tag, a **branch** pin tracks that branch's HEAD, **no ref** tracks the default branch's HEAD, and an **exact commit** pin never moves on its own. The ref you pinned is the ref that is compared and the ref that is installed.
  - **Never claims an update when no check happened.** Every path that genuinely cannot check — a network failure, an unrecognized source, an exact-commit pin, a local directory or archive with no remote to ask — now returns `update_available=False` with a message that begins `not checkable` and names the reason. There is no longer any path that reports an update it did not observe.
  - **A source change is refused.** `_target_matches_source()` re-derives the shape of the computed install target from the recorded source and checks the two agree, immediately before the target is handed to the installer. If they disagree, `upgrade` prints both and stops; `--force` is the only way past, and it overrides that gate alone. Editable installs refuse unconditionally — reinstalling over a checkout someone is actively working in is not an upgrade, it is a deletion.
  - **`doctor` reports provenance as information, not as a problem.** It prints `↳ from <source>` in green, because running from git, from a fork, or from a local build is a deliberate choice rather than a misconfiguration. The warning marker is now spent only on `not checkable` — a genuinely degraded capability, meaning nothing is watching this install's version — instead of on the fact that you did not install from PyPI.

### Added

- **Shift+Enter and Ctrl+Enter insert a newline instead of submitting.** Every desktop chat app has trained people to expect this, and terminal apps have never had it, for a real reason: the legacy key encoding has no field for a modifier on Enter, so `Enter`, `Shift+Enter` and `Ctrl+Enter` are byte-identical `0x0D` and no TUI can tell them apart. The workaround TUIs settled on is `Ctrl+J` (`0x0A`), which genuinely is distinct. But muxplex is not a legacy terminal — it is xterm.js in a browser, where the `KeyboardEvent` carries the modifier already. `terminal.js` intercepts the chord and sends the real kitty-keyboard-protocol CSI-u encoding (`\x1b[13;2u` / `\x1b[13;5u`) over the existing ttyd input frame, and the shipped tmux config binds `S-Enter` and `C-Enter` to `send-keys C-j` so apps that only speak the legacy encoding still get something they understand. Apps that read CSI-u natively never reach those binds. Deliberately **not** a bare `\n`: that is indistinguishable from `Ctrl+J` and would lie to any app that wants to know which key was actually pressed. `Alt+Enter` and `Cmd+Enter` fall through unchanged — `Alt+Enter` already has a working legacy `ESC CR` encoding that apps rely on. The tmux config load sentinel moves `base-2` → `base-3`, so a running server can prove it re-sourced the new config.
  - **The first working version still submitted the line anyway, and finding out required looking at bytes rather than at behavior.** "It inserted a newline and then submitted" is ambiguous — it does not distinguish "our handler never fired" from "our handler fired and something else fired too." A raw byte dumper in a live pane said `0a 0d`: our translated `C-j` *and* a stray `CR`. xterm.js's `attachCustomKeyEventHandler` returning `false` stops **xterm's own** key processing and does not call `preventDefault()`, so Enter still reached xterm's hidden textarea and came back around through `onData` a second time. The pre-existing branches in that handler (Ctrl+Shift+C, Ctrl+F) never hit this, because those chords produce no text input and so have no default action to suppress — which is exactly why it was easy to miss. Fixed by calling `e.preventDefault()` before `return false`, asserted by a regression test, and written into `AGENTS.md` so the next branch added to that handler does not repeat it.

### Fixed

- **A `uv tool install` of a git build was silently converted to PyPI on upgrade.** `_is_uv_managed` answers *which environment* to upgrade — the uv tools venv rather than a pip site-packages — and it answers that correctly. It was also being OR'd into the branch that decided *what to install*, so any uv-managed install resolved its target to the PyPI package name no matter where it had actually come from. The real case that surfaced it: a host installed from `git+https://github.com/bkrabach/muxplex@v0.34.0` was handed the PyPI package by `muxplex upgrade`. `_is_uv_managed` stays and is unchanged; it is simply no longer asked a question it was never answering.
  - The two halves of the command were already inconsistent, which made this worse for anyone on a fork. The update **check** used `info["url"]` — the fork's own remote — while the **install** used a hardcoded canonical URL, or `"muxplex"` when uv-managed. A fork user was therefore told "up to date" against their fork, and then handed upstream's code the moment they acted on it.
- **The pinned ref was discarded, so a tag-pinned install was compared against the wrong thing and upgraded onto the wrong thing.** `requested_revision` was never read. A pin to `@v0.34.0` was checked against the default branch's `HEAD`, which reported "update available" to someone already sitting on the newest release — and then moved them onto unreleased `main` while they believed they were following releases. Git refs now carry their intent through both the check and the install.
- **Local installs were classified `unknown`, and `unknown` was destructive.** `_get_install_info` recognized only `vcs_info` and `dir_info` + `editable`, so a **non-editable local directory install** and a **local wheel** both fell through to `unknown`. `_check_for_update` then had an unconditional `unknown → (True, "unknown install source — upgrading to be safe")` fallback, which is neither true nor safe: it permanently claimed an update existed, so `doctor` nagged forever about a version it had no way to check, and `upgrade` installed canonical upstream over the user's own local build. Doctor was nagging people into destroying their own builds. Both shapes are now classified from what they really write to `direct_url.json`, and neither is checkable, which is now something the code can say out loud.
- **A macOS-only test had been passing for the wrong reason, and never exercised the case it names.** `test_upgrade_exits_1_if_service_fails_to_restart` mocks `_have_systemctl=True` / `_have_launchctl=False` to drive `upgrade()` down the systemd dead-service path, but it never pinned `sys.platform` — so on a real macOS runner `upgrade()` took the darwin branch, never consulted the systemctl mocks at all, printed "skipping service management step", and returned normally. It passed regardless, because the ambient install info made `_installed_version_on_disk()` read back the same version `_get_install_info()` reported, so `_verify_version_moved()` always said "version did not change" and `SystemExit(1)` fired through the unrelated `_install_failed` branch instead. When `7badd8f` pinned the install source to a fake `pypi` / `0.1.0` — needed on its own, to stop these tests depending on this repo's editable dev install — the version check started succeeding, execution reached the block the test is actually about, and the gap surfaced on macOS for the first time. Fixed by pinning `sys.platform = "linux"`, the same pattern nine sibling `upgrade()`/`doctor()` tests already use; this one was simply not in that batch, because at the time it was still passing by accident.
- **Four ttyd tests failed on macOS CI for a reason that lived entirely in the fixtures.** `SUN_PATH_BUDGET` (102 bytes — macOS's limit, the tightest of the three qualified platforms) and `validate_socket_dir()`'s real bind probe are both correct; real usage is `~/.local/share/muxplex/ttyd/`, around 40 bytes, nowhere near the ceiling. The fixtures routed every ttyd test through pytest's `tmp_path`, which on the macOS runner resolves under `/private/var/folders/...` at roughly 114–120 bytes before a socket filename is appended at all. A new `short_socket_dir` fixture `mkdtemp`s under `/tmp` explicitly — deliberately not `tempfile.gettempdir()`, which macOS CI points at that same deep path via `$TMPDIR` — and `/tmp` is short on Linux, on macOS (a symlink to `/private/tmp`, still short after `.resolve()`), and on WSL alike, so no platform branch is needed. No product code changed.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break.
- The upgrade fixes were established against **real installs of each shape** rather than from the specification: what `uv` and `pip` actually wrote to `direct_url.json` for a git install, an editable install, a non-editable local directory install, a local wheel, and a PyPI install. That probe is what showed a local build and a local wheel were two different shapes both landing in `unknown` — reading the code would only have shown that neither was handled, not what each needed.
- The macOS platform-dispatch fix was confirmed by an in-process repro on a real Mac (ambient `darwin`, matching CI): `upgrade()` now raises `SystemExit(1)` through the intended `_service_restart_failed` path, not through the accidental `_install_failed` one it had been passing on.
- The macOS `sun_path` fixture fix was reproduced locally first, with a standalone script that rebuilds the runner's `tmp_path` shape and length: all four failures reproduce before the fix and pass after it, against unmodified product code.
- Shift+Enter was proved at the byte level against tmux 3.4 with the real edited `base.conf` — Shift+Enter CSI-u → `\n`, Ctrl+Enter → `\n`, plain Enter → `\r`, Ctrl+J → `\n`, Alt+Enter → `\x1b\r`, `a` → `a` — then end to end in a browser against a live pane.


## v0.34.0 (2026-08-04)

### Changed

- **One ttyd per session, on its own UNIX domain socket — two devices viewing two different sessions now simply works.** Until now muxplex ran exactly one `ttyd` for the whole server, on hardcoded TCP port 7682, with the tmux session name baked into its argv (`ttyd ... tmux attach -t <name>`). That single process *was* "the terminal", so a second device wanting a second session was not something the server could serve — it was a **conflict the server had to detect and refuse**, which is precisely what `POST /connect`'s 409 `terminal_conflict` existed for. Switching sessions meant killing that ttyd and respawning it with different argv, which yanked the terminal out from under whoever was already attached. `POST /connect` is now a single idempotent `ensure_ttyd(name)`: one ttyd per tmux session, each bound to its own socket under `~/.local/share/muxplex/ttyd/` (`MUXPLEX_TTYD_SOCKET_DIR` overrides), and the WebSocket proxy dials that socket with `unix_connect()` instead of `ws://localhost:7682/ws`. Connecting to session X never disturbs session Y. There is no contended resource left, so there is nothing to arbitrate.
  - **`AF_UNIX` is a strictly stronger fence than the `-i 127.0.0.1` bind it replaces.** Every ttyd is still `-W` with no `-c` credential — an unauthenticated, writable terminal — so the fence around it is load-bearing. A socket is guarded by filesystem permissions (0700 directory, uid-checked, symlink-refused) with no network namespace involved at all: there is no port to scan and no interface to misconfigure. `validate_socket_dir()` runs at startup, before anything else, and fails loud rather than degrading.
  - **Both WebSocket routes take an optional `?session=<name>`.** `WS /terminal/ws` and `WS /federation/{device_id}/terminal/ws` now name their target directly instead of reading the server's implicit "current" terminal; absent the parameter both fall back to `state["terminal_session"]` exactly as before, so the change is additive and an older client keeps working. The frontend sends it on both branches. The federation relay dials the remote's own `/terminal/ws` over HTTPS and never touches a ttyd socket, so it needed no socket handling at all — only to forward `?session=` upstream.
  - **A ttyd is a view, not the session, so it is reclaimed on resource grounds.** Relays are refcounted; `DELETE /api/sessions/current` kills only the caller's own session's ttyd, and only when `relay_count()` for it is zero — a structural check that is strictly stronger than the group-ownership claim it replaces, because it also covers two devices in the *same* group co-viewing one session. Idle ttyds are reaped after 60s by a pass riding the existing ~1s poll cycle, `MAX_TTYDS = 32` is a backstop against a reaper bug rather than against users, and orphans from a previous process are reaped at startup only after their identity is confirmed against a fresh `ps` snapshot.
  - **`POST /connect` can now fail honestly.** It returns 500 on a spawn error and 503 at the capacity ceiling. The old endpoint never verified the spawn at all, so it could — and did — return 200 for a terminal that did not exist.
  - **`ttyd_port` (7682) survives in `/connect`'s response as a legacy wire-only field.** Nothing binds it any more, but `muxplex_client.parse_connect_result()` requires an int with no default and is vendored into muxplex-deck. Returning `null` or removing it would break a client this repo's tests cannot see.

- **The `lsof` port sweep is deleted.** `_kill_pids_on_port()` ran `lsof -ti :7682` and signalled **every PID it found**, without ever checking what those processes were — it existed only because it was the sole thing that could reap a ttyd whose PID was never recorded. That is the same accident class as the `KillMode` incident this repo already documents, the one that destroyed 44 live tmux sessions: act on a process you have not identified and you eventually kill something you did not mean to. The safety net it provided is replaced rather than merely removed — `reap_orphan_ttyds()` confirms identity against a fresh `ps` snapshot before signalling anything, and never signals an unconfirmed PID. `grep -c lsof muxplex/ttyd.py` now returns 0, and a source-level test asserts the string cannot come back.

- **Three platform constraints are enforced in code, not just written down.** Each was learned from a *silent* failure across the Linux / macOS / WSL2 spikes in `scripts/`, and in every case ttyd stayed **alive and non-functional** rather than erroring — which is why none of them can be caught by a liveness check:
  - **The socket path must end in `.sock`.** ttyd's UNIX-socket detection is suffix-based and a non-`.sock` path does not error: it silently falls back to TCP **7681** on `INADDR_ANY`, reopening the exact unauthenticated-writable-terminal exposure the old `-i 127.0.0.1` bind was added to close. `ttyd.SOCKET_SUFFIX` is why the path is never hand-built, and why paths are hashed and range-checked against a 102-byte `sun_path` budget (macOS's limit, the tightest of the three qualified platforms) rather than concatenated from a session name.
  - **The socket directory must be a Linux-native filesystem.** On a WSL DrvFs mount `bind()` fails `ENOTSUP`, and ttyd does not treat that as fatal — it busy-retries roughly every 10ms, indefinitely, with no backoff and no self-termination: alive, burning CPU, bound to nothing. `validate_socket_dir()` refuses such a directory at startup with the right diagnosis instead of leaving that process spinning.
  - **A socket file proves a bind happened at spawn; it never proves liveness.** A `SIGKILL`ed ttyd leaves its socket behind forever and `Path.exists()` answers `True` for it. `socket_is_live()` is therefore a real `AF_UNIX connect()`. Porting it as an existence check reintroduces the reconnect-bounce bug the pre-accept liveness check was written to fix.

- **Guard decisions, recorded because they are the kind of thing re-litigated in six months.** 409 `terminal_conflict` is **deleted** — with one ttyd per session it cannot fire. WS **4409 is kept but redefined narrower**: it now means "you asked to attach to a session your own group has not selected," a per-request consistency check, not "another group holds the one terminal." 4404 widens to cover a missing, invalid, or unknown session. `state["terminal_group"]`'s **server logic is deleted** while the **wire field is retained** as informational provenance — no server behavior branches on it any more, and subtracting a documented public field buys nothing. The frontend's conflict overlay loses its Take-over button accordingly — there is no longer a terminal to take over — and, per the reconnect-loop fix below, now does nothing but display; getting back in sync is the poll loop's job, not the overlay's.

### Added

- **`muxplex commands list|add|remove`** — the local-operator write path for `session_commands`, validated through the same `resolve_session_commands()` V1–V7 rules the server applies rather than a second reimplementation, and written via `save_settings()`. This is not a fence bypass: `LOCAL_ONLY_KEYS` is enforced in `patch_settings()` (the API's write path) and deliberately not in `save_settings()` (the local-operator path). A CLI subcommand invoked by the human at the keyboard is the intended writer, no different in kind from hand-editing `settings.json`.
- **Settings > Commands now shows the feature to the person who has never heard of it**, and a Duplicate/copy authoring aid replaces the wall. Every resolved pair is rendered including the built-in `default` — previously `default` was filtered out and the whole field hid itself when no extra pairs existed, so the user who most needed to discover the feature saw nothing at all. Settings re-fetches on every open, closing the apply-then-verify loop without a page reload. Malformed pairs are surfaced where they are actually seen: a non-selectable "N pairs failed to load" row in the New Session picker, count badges on the settings gear and the Commands tab that fire before Settings is ever opened, and `DELETE /api/sessions/{name}`'s 409 `unknown_command_id` now points at Settings > Commands alongside the `command_id` it already named. Each row gets **Duplicate…** and **Copy command**: Duplicate opens an inline composer with editable id/label/create/delete fields and a live `muxplex commands add` line to copy and paste into any shell. **There is no Save button anywhere in that composer, by design** — `session_commands` holds arbitrary shell the server executes, and `AuthMiddleware`'s loopback source-IP bypass precedes every credential check with no CSRF defense, so any endpoint meaning "define a pair" would be RCE reachable from a page in the operator's own browser. The composer's client-side checks are labeled advisory; the server's `GET /api/session-commands` `errors[]` after apply-and-reload remains the authority.

### Requirements

- **`websockets>=14.0`** (was `>=11.0`). The proxy's `unix_connect()` lives in `websockets.asyncio.client` and does not exist below 14.0 — below that floor the import fails outright. This is a hard API requirement, deliberately not the version-pinning approach `AGENTS.md`'s uvicorn/websockets incident rejected: that one forced a single implementation to patch one known bug, and `test-latest-deps` remains the general defense against dependency drift.

### Security

- **An orphaned legacy ttyd on port 7682 is now always reported, even with no PID file.** `reap_legacy_ttyd()` returned early when `LEGACY_TTYD_PID_PATH` was absent, before ever probing the port — which made the loud "UNAUTHENTICATED WRITABLE TERMINAL" report unreachable for exactly the scenario it exists to catch: a pre-upgrade ttyd (`-W`, no credential) still bound to 7682 whose PID file was lost to a crash, a manual kill, or a state-dir wipe. Live DTU verification confirmed such a ttyd was neither swept nor reported; it could sit there indefinitely with nothing said. The detect-and-report check now runs unconditionally after every branch. It deliberately **reports without reaping** on that path: with no PID file there is no recorded identity to confirm, and killing whatever happens to be listening on a hardcoded port *because it is on that port* is the exact sweep pattern this release deletes.

### Fixed

- **The per-device sync-group terminal guard has been silently non-functional since it shipped, and this is the release that found out.** If you turned a device off the shared session selection (Settings > Devices, or the header toggle), the terminal-side guard that was supposed to keep that device from attaching to a session its own group had not selected **never ran at all**. `openTerminal()` called `connectWebSocket(sessionName, remoteId)` and simply omitted the third argument, so `ownDeviceId` was `undefined` for every local browser client and the local branch's `if (ownDeviceId)` guard never appended `&device_id=` to the `/terminal/ws` URL. Server-side (`main.py`'s `terminal_ws_proxy`), an absent `device_id` leaves `group` as `None`, and the entire per-device consistency block — including the WS 4409 path — is skipped outright. Not narrowed, not degraded: skipped.
  - **Be clear about the duration.** The missing argument was present from the moment `ownDeviceId` was added to both function signatures, in `33eaf80` ("per-device sync groups with single-owner terminal guard"), which shipped in **v0.31.3**. It has therefore been broken in every tagged release since: v0.31.3, v0.31.4, v0.31.5, v0.31.6, v0.32.0, v0.33.0. It was not introduced by the per-session-ttyd work, and none of that work made it worse.
  - **What changed is only that something finally looked.** The rewrite added the first test that asserts the local branch's actual `/terminal/ws` URL, and it failed. Both sides of this were tested the whole time, and the seam between them was not: `test_ws_proxy.py` builds the `device_id` query parameter itself in its `_ws_url()` helper, so it proved the server correctly handles a parameter no browser was sending; and `test_terminal.mjs` called `openTerminal()` in 37 places without the string `device_id` appearing in the file even once — at `33eaf80`, at v0.33.0, and at every tag in between. Nothing asserted that the front end produced the input the back end was being tested against. Fixed by forwarding the argument at the single call site in `terminal.js`.
- **The terminal conflict overlay no longer reconnects in an unbounded loop.** `_showTerminalConflictOverlay()` re-POSTed `/connect` and then called `connectWebSocket()` unconditionally, regardless of that POST's outcome. `_reconnectAttempts` is module-level and is cleared only by a successful data message or a fresh `openTerminal()`, so a second 409/4409 re-entered the same function through `connect()`'s own escalation branch — with no `setTimeout`, no cap, and nothing gating the recursion. The result was a pure promise-microtask loop (overlay → fetch → connectWebSocket → escalation → overlay → …) that ran until V8's heap was exhausted: in the frontend suite, about six minutes and a `SIGABRT`; in a browser, an unthrottled request storm aimed at the server. It was introduced in this release's own work, when the Take-over button's user-gesture gate was removed while the reconnect kept firing automatically.
  - The underlying mistake is a semantics mismatch this release created. WS 4409 no longer means "another device holds the one shared terminal" — that resource is gone — it means "this device asked to attach to a session its own group has not selected," a state desync rather than a transient conflict. Retrying the identical request cannot resolve a desync, and here it would actively make things worse: `connect_session()` unconditionally writes `{active_session: name}` for the group, so a stale client re-POSTing `/connect` would silently overwrite whatever the other device had just correctly selected — fighting the very guard the code path exists to enforce.
  - **The fix is structural rather than a cap or a backoff.** The overlay now displays and stops: zero network calls, zero timers, so no server response can make it recurse, no matter how it is provoked. Recovery goes through the channel that already exists and is already correct — `app.js`'s poll loop picks up the group's real `active_session` on its next tick and calls `openTerminal()` with the right target, resetting all reconnect state cleanly; re-selecting a session does the same thing immediately. The client-side HTTP 409 `terminal_conflict` branch is kept as a version-tolerant no-op: this server can no longer send it, but an older or federated peer might, and it funnels into the same, now-safe, overlay.
- **`muxplex config set`/`config reset` no longer claim success while writing nothing.** Both went through `patch_settings()`, which silently skips any `settings.LOCAL_ONLY_KEYS` key so a Bearer-key-holding remote caller can never widen one of those fences — correct behavior for `PATCH /api/settings`, and a lie when the caller is the local operator at the keyboard, who has every right to change these keys. The CLI printed `<key>: <value>` unconditionally regardless. Both now check `LOCAL_ONLY_KEYS` first and exit non-zero naming the actual escape hatch (edit `settings.json`, or `muxplex commands add` for `session_commands`). One check covers all eight fenced keys.
- **The socket-directory diagnostics stop telling WSL operators to run an impossible `chmod`.** `validate_socket_dir()` ran its ownership and mode checks before the WSL/DrvFs check. On a default, metadata-less DrvFs mount `chmod()` is a **silent no-op** — verified directly on a real WSL2 host: `chmod(0o755)` then `chmod(0o700)` on a fresh `/mnt/c` directory both return success and both leave the mode at `0o777`. So the function's own corrective `chmod(0o700)` did nothing, the mode check then fired, and the operator was told to do the one thing the process had just tried and failed to do — while the accurate diagnosis (`AF_UNIX bind()` fails `ENOTSUP` on DrvFs; ttyd busy-retries forever without binding) sat unreachable a few lines below. The same mount reports a synthetic mount-wide uid rather than real ownership, so the ownership check had the identical hazard, equally unfixable by `chown`. Both now run *after* the DrvFs check. The symlink check reads the file-type bit, which `chmod`/`chown` cannot affect, so it is unaffected and stays first. The mode message is also generalized: since the function always calls `chmod(0o700)` immediately beforehand, reaching that check with group/other bits still set is proof the `chmod` had no effect on **any** filesystem, so it now says that and points at relocating the directory. The safety property is unchanged throughout — the directory is refused either way. This was found by testing on a real WSL2 host; the existing DrvFs test modeled a directory whose mode had already become `0o700`, as if the `chmod` had worked, so the mode check was never in its path and it could not have caught this.

### Documentation

- The per-session-ttyd and command-pairs-UI design records, the named-command-pairs implementation record, and the terminal-config ownership record all lived at the root of a throwaway cross-repo workspace and existed nowhere else. All four are preserved under `docs/plans/` and `docs/`, bodies intact, headers reconciled against what actually shipped. The source tree cites two of them by their workspace filenames (`docs/plans/2026-08-02-per-session-ttyd-plan.md`, `docs/plans/2026-08-02-named-session-command-pairs-ui-design.md`) from 36 places, so each header records that name.
- `AGENTS.md`, `docs/API_SEMANTICS.md`, and `docs/AGENT_GUIDE.md` are rewritten for the per-session architecture. The `0.0.0.0` exposure incident and the 4409-never-reached-the-wire writeup are kept verbatim — they remain true records of what happened.
- `scripts/README.md` records **WSL2 as GO**, matching Linux and macOS on every question, which is what qualified all three target platforms for this architecture.
- `CHANGELOG.md` gained the v0.33.0 named-command-pairs entry, which shipped inside that artifact with no coverage at all. `docs/BACKLOG.md` records the open question of where a device label belongs on a preview tile.

### Verification

- Full Python suite green in the Digital Twin Universe (`make test`), **run after the version bump** — 1969 passed, 4 skipped, 28 deselected in 92s — including `test_client_contract.py::test_client_version_matches_server_version`, which asserts `client/pyproject.toml`'s version equals the root's and can only catch a mismatch once the bump exists. v0.31.1 shipped a mismatch precisely because the suite ran before the bump and was never re-run after.
- Frontend suite green after the bump: `node --test tests/*.mjs` → 717 tests, 717 pass, 0 fail, 3.2s. That last number is itself part of the proof for the reconnect-loop fix above: before it, this same command did not finish at all — it consumed the V8 heap for roughly six minutes and aborted.
- The §12.5 acceptance test is real end to end: real tmux, real ttyd, real UNIX sockets, two devices on two sessions through the real ASGI app, concurrent WebSockets proven not to cross session-unique in-pane markers, then A's teardown proven not to disturb B. Mocked suites can prove the wiring; only this can prove the two terminals are actually independent.
- **Sessions survive the upgrade, proven rather than asserted.** A canary process was placed in the service's own cgroup and the canary itself was validated by a negative control first — it dies under `KillMode=mixed` and survives under `process`, so a survival result means something. Then a real `muxplex upgrade` was run end to end: identical session-creation timestamps, identical tmux server PID, and identical in-pane markers before and after.
- `reap_legacy_ttyd()`'s gap was found by live DTU verification of the real function, not by reading it, and its regression test was confirmed failing before the fix and passing after.
- The DrvFs `chmod` no-op was measured on a real WSL2 host rather than reasoned about, and both new tests fail against the old check ordering and pass against the fix.
- This release was cut twice. The first attempt was prepared and then dropped unpushed because the frontend suite was red — the reconnect loop was OOM-ing it, and the `device_id` seam above was failing a real assertion. Both are fixed in commits that precede this one, so the release sits on top of them rather than over them.

### Upgrading

**On systemd hosts, check `KillMode` before you upgrade:**

```
systemctl --user show muxplex.service -p KillMode
```

It must report `process`. `muxplex upgrade` regenerates the safe unit, but it does so **after** its stop step — so a host still carrying a pre-v0.24.0 unit, or a hand-edited one, with `mixed` or `control-group` would have its tmux server SIGKILLed by that stop, taking every live session with it. muxplex auto-spawns the tmux server when none is running, which is how that server ends up in muxplex's cgroup looking like nothing at all. This is confirmed behavior, observed in testing. It is **not new in this release** — `service.py` and `cli.py`'s `upgrade()` are byte-identical to v0.33.0 — but this is the release worth saying it out loud in, because it is also the release in which the per-session ttyd rewrite makes people upgrade.

The upgrade otherwise needs nothing: socket directories are created on demand, the pre-upgrade single ttyd on 7682 is reaped at startup once its identity is confirmed (and reported, never blind-swept, if it cannot be), and a client that sends no `?session=` gets the previous fallback behavior unchanged.


## v0.33.0 (2026-08-02)

### Added

- **Named session command pairs — more than one way to create a session.** Until now there was exactly one: `new_session_template` created every session on the host and `delete_session_template` killed it. That is the wrong shape as soon as you want a workspace session rooted in one directory and a scratch shell in another, or a genuinely different creation command for some sessions. A new `session_commands` list holds additional named pairs, each with an `id`, a `label`, a create command and a delete command. The two singular keys fold in automatically as the reserved `default` pair — always first in the resolved list, and never claimable by a config entry — so a settings file that has never heard of this feature resolves to a one-element list, and a client that sends no `command_id` runs the same code path with the same template it ran before.
  - **Which pair created a session is recorded at create time**, and delete looks that record up instead of asking the caller. That is what makes this a *pair* rather than two unrelated lists: kill a session that was created by `amplifier-workspace` and you get `amplifier-workspace`'s teardown, without anyone having to remember which command made it three days ago. `DELETE /api/sessions/{name}` takes no `command_id` at all, and that is deliberate — a caller-chosen kill command is a caller-chosen shell command.
  - **The record lives in the manifest, not `state.json`, and that placement is the whole trick.** The manifest's `sessions` map is reaped every ~2s against live tmux, and `spawn_session_command` has a documented branch that returns success *before* the session is visible to an enumeration — a record written into a reaped map would be deleted inside that window, and the session would later be torn down by the wrong pair with nothing in any log to say why. `created_with` is therefore a new top-level map (manifest schema 1 → 2) that reaping never touches.
  - **`muxplex restore` is pair-aware.** Without that, a restore after a reboot would recreate an `amplifier-workspace` session as a bare `tmux new-session` — one window, wrong cwd, the exact "looks restored and isn't" failure this repo's own recovery notes warn about. A recorded id that no longer resolves is a hard `fail` in the restore report, naming the missing pair and the file to fix, never a silent substitution of the default.
  - **Registering the key in `DEFAULT_SETTINGS` was load-bearing, not bookkeeping.** `save_settings()` merges only keys it knows about and drops the rest, so an unregistered `session_commands` would have been erased by the next write of *any* setting — a hand-edited list silently deleted the first time someone toggled a display preference in the UI.
- **`GET /api/session-commands`**, returning the resolved, ordered, validated list plus one human-readable string per rejected entry. The fold and the validation resolve once on the server rather than being re-derived by each of PWA, sidecar, and agents — the standing answer in `AGENTS.md` for any rule a client would otherwise have to re-implement. `POST /api/sessions` gains an optional `command_id`, rejected with 400 before any subprocess runs if it does not resolve. `DELETE /api/sessions/{name}` answers **409 `unknown_command_id`** and runs nothing when a session's recorded pair is no longer configured; `?force=true` is the explicit override that substitutes the default kill command and says so in the response. All additive, per this repo's API rule.
- **A command picker in the create UI, and a read-only list in Settings > Commands.** The picker appears only when two or more pairs are configured — below that the create control is byte-identical to what shipped before, which is what almost every install will see. Settings displays the additional pairs and any configuration errors without an editable control anywhere, and names `~/.config/muxplex/settings.json` as the place to manage them, because that is honestly the only place they can be managed (see Security below).
- **Settings > Terminal — the tmux config is now editable from the dashboard.** Until now the only way to pick a theme was `muxplex config set tmux_theme`, which the target audience will never do. The new tab shows install status, a theme picker, a copy-mode choice, and the generated config behind a "Show the generated config" disclosure. Changes apply to a running tmux server immediately — a setting that appears to do nothing until restart is a bug, not a deferral.
- **`GET`/`PATCH /api/tmux-config`.** API first, per this repo's own rule; the tab is a client of it, not a parallel implementation. GET returns install status, the current theme and copy mode, the available themes, and a preview of the rendered config.
- **New setting `tmux_copy_mode`** — `desktop` (default) or `vi`. Selecting `vi` renders an extra `30-copy-mode.conf` fragment; selecting `desktop` removes it. Machine-scoped like `tmux_theme`, so it does not sync between devices.

### Security

- **The API may list and select a command pair; it can never define one.** `session_commands` is in `settings.LOCAL_ONLY_KEYS` and deliberately absent from `SYNCABLE_KEYS` — `PATCH /api/settings` ignores it, and federation sync never carries it. These are arbitrary shell commands the server itself executes, and the federation Bearer key is the same credential handed to remote agents, so a PATCHable `session_commands` would let a Bearer-key holder define a pair *and* select it at create time: the identical RCE that fence closed in v0.31.4 for `new_session_template`, with one extra layer of indirection. The cost is real and accepted: managing pairs means editing `~/.config/muxplex/settings.json` on the host, there is no editable control in Settings, and there is not going to be one.
- **The API's tmux vocabulary is closed, by construction.** tmux config can carry `run-shell` and `default-command` — arbitrary code execution — and the API bearer key is the same credential handed to remote agents. So there is no free-text directive field and there will not be one: `theme` is validated against the shipped theme list and `copy_mode` against exactly two values, and anything else is rejected with 400 before it reaches disk. Verified against a live server with four injection attempts (a `run-shell` payload, a newline-smuggled `default-command`, a path-traversal theme, and a plausible-but-invalid enum value) — all four rejected, nothing written.

### Fixed

- **The config preview no longer looks broken.** It was a `<textarea>` 170px tall holding ~3600px of text with no visible scrollbar. Two rounds of adding a fade and a styled scrollbar changed nothing, and the measurement explains why: `offsetWidth - clientWidth` came back 2px, not the 12px the rule asked for — this platform paints overlay scrollbars, which reserve no gutter and ignore `::-webkit-scrollbar` outright. The fix was not a third styling attempt. The dialog body already scrolls, so an inner scroll box was a second scroll container that put the interesting edge below the dialog's own fold; removing it removes the thing that needed an affordance. The preview also now shows directives only — 116 of its 162 lines were comment or blank, 71% of what a reader scrolled past answering a question they had not asked. 162 lines to 46. The template files keep every comment.

### Verification

- Full suite green in the Digital Twin Universe (1845 passed) on the released tree.
- The API was exercised against a running server, not asserted from source: GET shape, valid PATCH in both directions, fragment appearing and being removed, the rendered vi fragment loading into a real tmux server, and the four injection attempts above.
- The tab was driven in a real browser across three rounds. The first two found real defects that were fixed rather than explained away.
- Command pairs are proved against a real tmux server, not mocks. Unit tests with mocked subprocesses prove the *wiring*; they cannot prove that pair B's teardown ran and pair A's did not. `tests/test_command_pairs_integration.py` does, with marker files written by real create and delete commands on an isolated tmux socket — the one claim the mocked suites structurally cannot make.

### Note on the command-pairs entry

Command pairs shipped inside the v0.33.0 artifact and had no changelog coverage at all until this entry was added after the fact. Two sessions were working and releasing from this branch at the same time; each wrote up its own workstream and neither noticed the other's was missing from the file.

The write-up is added to this section rather than carried into a new release because v0.33.0 is factually the version that introduced the feature — all seven of its commits fall in `git log v0.32.0..v0.33.0`, and none straddle the tag. PyPI artifacts are immutable, so cutting a v0.34.0 to hold nothing but a version bump and this text would credit the feature to a release that does not contain it — the same misattribution the v0.31.6 note below exists to correct.


## v0.32.0 (2026-08-02)

### Changed

- **The default tmux config now targets people who never asked to learn tmux.** muxplex users drive sessions from the web dashboard — creating windows, switching sessions, splitting panes are all things the UI does — and almost nobody arrives as a tmux user. The default was still shaped like a tmux user's config. It now optimises for the terminal *content* behaving the way a desktop app behaves, and not for teaching keybindings. The bar applied to every line: would someone who has never heard of tmux be surprised by it?
  - **Copy mode is `emacs`, not `vi`.** In vi copy-mode the arrow keys are the least useful way to move, selection is modal (`v` to start, `y` to finish), and nothing on screen indicates a mode exists. In emacs copy-mode the arrows, PageUp/PageDown, and Home/End all do the obvious thing — which is what someone reaching for the keyboard after scrolling tries first.
  - **`Ctrl+C` copies the selection**, and **`Esc` leaves copy mode.** Scrolling up silently enters copy mode, so there has to be an obvious way back out; without it, "my typing stopped working" is the common report. And Ctrl+C is the first thing anyone tries after highlighting text — the alternative was `Alt+w`, which nobody guesses.
  - **Double-click selects a useful word.** tmux's default separators break on `/ . - _`, so double-clicking a path, a filename, or a package name grabbed a fragment instead of the thing you pointed at. `word-separators` now keeps those intact.
  - **Vim-style `Alt+hjkl` pane navigation is no longer a default.** It is muscle memory this audience does not have. `Alt+arrows` remains — discoverable by trying it, and the direction is self-evident. Anyone who wants hjkl adds four lines to `90-local.conf`.
  - Unchanged and deliberately so: mouse on, 50k scrollback, 1-based window/pane numbering, `renumber-windows` (close a middle window and the rest close the gap, like browser tabs), and the terminal/colour/clipboard correctness settings.

**Upgrading:** `90-local.conf` is loaded last and is never written by muxplex, so anything you have set there already wins over all of this. If you were relying on the old vi copy-mode or `Alt+hjkl` defaults, put them back with:

```
setw -g mode-keys vi
bind -T copy-mode-vi v send -X begin-selection
bind -T copy-mode-vi y send -X copy-selection-and-cancel
bind -n M-h select-pane -L
bind -n M-l select-pane -R
bind -n M-k select-pane -U
bind -n M-j select-pane -D
```

### Verification

- Full suite green in the Digital Twin Universe, run after the version bump.
- The rendered config was loaded into a real tmux server and every changed option read back from it — `mode-keys emacs`, `word-separators`, and the three new `copy-mode` bindings — rather than asserted from the template text.
- Verified live on a server with 51 running sessions: the new base loaded, a local override still won, and the server pid was unchanged throughout.


## v0.31.6 (2026-08-02)

### Bug Fixes

- **`muxplex setup-tls` no longer fails outright on a long hostname.** X.509 caps a CommonName at 64 characters (RFC 5280's `ub-common-name`), and `cryptography` enforces that by raising rather than truncating — so on any host whose name exceeds the limit, certificate generation died with a bare `ValueError: Attribute's length must be >= 1 and <= 64, but it was 87` thrown from deep inside the library, with nothing in the message naming the hostname as the cause. This is not a corner case: a GitHub macOS runner reports a 67-character hostname, and long Tailscale names, corporate FQDNs, and cloud instance names all reach it. For those users `setup-tls` simply did not work. The CommonName is now capped at all three cert-generation sites (`generate_self_signed`, `generate_local_ca`, `generate_leaf_signed_by_ca`). Truncating is safe here for a specific reason: the **full, untruncated hostname is still written into subjectAltName**, and SAN — not CN — is what every modern TLS client actually validates against, RFC 6125 having superseded RFC 2818. A shortened CN costs nothing a client will ever notice; the alternative cost the user their certificate.

### Testing

- **The launchd tests now exercise real `launchctl`, not mocks.** Every launchd test until now stubbed `subprocess.run`, so they verified the *shape* of the calls and nothing about launchd's behaviour -- which is how v0.31.1, v0.31.2, and v0.31.3 each shipped a macOS-only bug that a human, not CI, found in production. Four new tests run against real launchd on the macOS runner using a throwaway `com.muxplex.selftest` label with fixture-finalizer cleanup, and skip on Linux. The fixture detail is load-bearing and came from probing a real Mac rather than reasoning about it: a plain `/bin/sleep` job dies instantly, so `bootout` looks *synchronous* and a test built on it passes while proving nothing. Measured on real hardware -- plain sleep: no race to catch; SIGTERM-trapping job: still loaded ~2.25s after bootout. The fixture therefore traps SIGTERM and takes a moment to exit, like the real server draining connections, and the race test additionally asserts that the window *was* observable so it cannot quietly decay into a tautology.
- **The macOS CI job passes for the first time since it was added.** It had been red on every commit since introduction (`b7cf04b`, `7b076c6`, `225dba7`) -- nine failures. A permanently-red job protects nothing, so the new launchd coverage would have gated nothing either. Six of the nine were the CommonName bug above. The other three were tests that quietly meant something different depending on which runner picked them up: two asserted the systemd/Linux service branch without pinning `sys.platform`, and one mocked `create_subprocess_shell` while leaving `create_subprocess_exec` live, so it really exec'd `tmux` and depended on a binary and a live server it was not trying to assert.

### Note on v0.31.5

The `v0.31.5` tag and the artifact published to PyPI point at `225dba7`. The CommonName fix landed *after* that tag, so it was never in the released v0.31.5 despite the CHANGELOG briefly crediting it there. PyPI artifacts are immutable, so the entry has been moved here rather than the release re-cut.


## v0.31.5 (2026-08-02)

### Bug Fixes

- **The sync-group toggle in the header visibly changes state again when you press it.** The header button that switches a device between following this server's shared session/view selection and running independently did everything correctly except look different: `renderSyncGroupControls()` applied `header-btn--active`, flipped `aria-pressed`, and updated the tooltip — and `.header-btn--active` had no rule in `style.css` at all, so nothing rendered. A user on v0.31.4 reported the toggle as visually broken, and it was. The JavaScript was right in every respect; the class it toggled simply did not exist in the stylesheet. This shipped in v0.31.4 alongside sync groups and went out undetected. Fixed by adding the rule — filled `--accent-dim` background, `--accent` border and icon colour, plus a hover variant — reusing the accent tokens `.header-btn:hover` and `.settings-tab--active` already use rather than introducing a new colour. Filled background *and* solid border, not just a colour swap, so the two states stay distinguishable without relying on hue alone; both the collapsed and expanded header buttons pick it up, since both carry `.header-btn`.
  - **No existing suite could have caught this, by construction.** `test_frontend_js.py` checks JS source, `test_frontend_html.py` checks markup, and `frontend/tests/*.mjs` check JS behaviour — none of them cross-checks a class name the JS applies against the stylesheet that is supposed to render it. A new `tests/test_css_class_definitions.mjs` now does: it extracts every string-literal class name applied via `classList.add/remove/toggle(...)` or a literal `className = '...'` assignment in `app.js`, `terminal.js`, and `deck/deck.js`, and asserts each one resolves in the stylesheet the same page actually loads (`style.css` for the app, `deck/deck.css` for the deck). Only the class-name argument position is read, so `classList.toggle('is-stale', staleness === 'warn')` does not misfire on `'warn'`. Proved against the real bug: with the `header-btn--active` rule removed it fails naming that exact class, and passes once restored. This is the second cross-file blind spot this one feature has produced — the first was the `app.js`/`terminal.js` global collision now guarded by `test_shared_scope.mjs`.
- **`/terminal/ws` rejections now arrive as the close codes they always claimed.** v0.31.4 corrected the documentation about this without fixing the behaviour, and this release fixes it. The `device_id` guard closed the WebSocket with 4404 (unknown device) and 4409 (terminal claimed by a different sync group) *before* calling `websocket.accept()`, and a pre-accept close never produces a real WebSocket close frame — the connection was never upgraded — so it went over the wire as a bare `HTTP/1.1 403 Forbidden` with an empty body. A real browser reports `1006` for any failed opening handshake and cannot see 4404/4409 at all, which left `terminal.js`'s 4409 branch unreachable in production; only `test_terminal.mjs`'s synthetic close event ever exercised it. Fixed by accepting the handshake and then immediately closing with the code — completing the handshake is the only way a real close frame carrying a real code can exist to be reported to a browser. Evaluated and rejected: uvicorn's WebSocket-denial-response extension, which would let the rejection carry the existing HTTP-level JSON body without accepting; the WHATWG WebSocket API never exposes a failed handshake's status or body to JavaScript, so that helps a script client reading raw HTTP and does nothing for a browser. Trade-off accepted deliberately: the browser's `open` event now fires briefly before `close` follows, because the handshake genuinely completes. The refusal is not weakened — the helper returns immediately after `close()` without ever touching ttyd or the relay loop, so no session data crosses that connection regardless of what the client sends afterwards.
- **An open federation terminal relay no longer hangs shutdown.** `federation_terminal_ws_proxy` carried two defects that its local-terminal sibling had already fixed. It relayed with `asyncio.gather()` over both directions, which waits for *both* to finish: when the browser disconnects, the remote→client direction keeps streaming from a still-live remote ttyd, the handler never returns, and uvicorn's "waiting for connections to close" phase blocks until systemd SIGKILLs the process at the stop timeout. Separately, it never registered its task in `_ws_proxy_tasks`, so lifespan shutdown could not find it to cancel — the shutdown count silently undercounted, and every open federation relay survived the cancellation pass untouched. With N concurrent relays this was a reliable hang, not an edge case. Both fixed by mirroring `terminal_ws_proxy`: `asyncio.wait(FIRST_COMPLETED)` plus cancel-the-other-direction, an explicit `websocket.disconnect` check rather than relying on a `RuntimeError` from a second `receive()`, and register/deregister in `_ws_proxy_tasks`.
- **`muxplex upgrade` no longer reports success for an upgrade that did nothing.** Observed on a real machine: three consecutive upgrades printed `Status: update available (v0.31.2 → v0.31.3)` followed by `Installed successfully` and `Service started`, while the tool environment never left v0.31.2. The install target is unpinned (`muxplex`, meaning "latest"), so uv answered it from a cached PyPI index that predated the release, resolved "latest" to the version already installed, reinstalled it, and exited 0. The code checked the exit code — which asks *did the installer do what I asked*, not *did the version change*. Those two come apart precisely here, and nothing in the exit code distinguishes them. Two changes: the upgrade now passes `--refresh`, so a stale index cannot silently pin you to the version you already have; and the version is read back **from disk in a fresh interpreter** and compared against what you started on. An upgrade known to be available that leaves the version unchanged is now a loud failure carrying the exact command that fixes it, rather than a green message. A `--force` reinstall of the current version is still a legitimate no-op.
  - The fresh interpreter matters: `importlib.metadata` resolves and caches at import time, and the process asking the question *is* the old build. Asking it in-process returns the version you started with regardless of what just landed on disk — the same blind spot one level up.

### Infrastructure

- **CI now runs on macOS.** Every job in `ci.yml` ran on `ubuntu-latest` only, despite muxplex targeting macOS, WSL, and Linux — and that gap produced three consecutive production incidents caught by a human rather than by CI: v0.31.1 (`muxplex update` crashing on a `launchctl bootstrap` race), v0.31.2 (`service restart` silently doing nothing — itself a regression from the v0.31.1 fix), and v0.31.3 (doctor reporting a tunnel as the local server). v0.31.2's own changelog said it plainly: the launchd tests exercise the logic, not `launchctl`, because the suite only ran in a Linux container. A new `test-macos` job (macos-latest / arm64, Python 3.13 only, with tmux and ttyd installed via Homebrew) closes that. Deliberately one job rather than a matrix axis on the existing one: macOS runners cost roughly 10x Linux minutes, and what was missing was platform coverage, not more Python versions. It paid for itself immediately — the X.509 CommonName failure above was found by this job rather than by a user, along with three tests whose results depended on the runner rather than on the thing they claimed to assert. Four real-`launchctl` tests now exercise actual launchd on macOS instead of subprocess mocks; they skip on Linux, which is why the DTU run below reports 4 skipped where v0.31.4 reported none.

### Documentation

- **`docs/AGENT_GUIDE.md` now covers TLS trust bootstrap.** The guide is what agents and scripts get pointed at, and its quick-start uses an `https://` URL — but it never mentioned `/api/ca`, `/ca.crt`, `/setup`, `--cacert`, or verification at all. An agent following it exactly against a TLS-enabled instance got `curl: (60) SSL certificate problem: unable to get local issuer certificate` and found no answer anywhere in the document; that failure happens before the auth middleware runs, so none of the five documented acceptance branches could help. Added: the three unauthenticated endpoints and why they must be auth-exempt (a client cannot authenticate over TLS it does not yet trust), verified bootstrap recipes for localhost and remote, muxplex-client's `ca_file=` argument, what to do when `/api/ca` 404s because the server used a different `setup-tls` method — and an explicit instruction not to silently disable verification in response.

### Verification

- 1822 Python tests passed, 4 skipped, 15 deselected in the Digital Twin Universe (`make test`) — run *after* the version bump, per the v0.31.1 incident. The 4 skips are the new real-`launchctl` tests, which require macOS and are covered by the new CI job instead.
- 698 frontend tests passed (`node --test tests/*.mjs`), including the new `test_css_class_definitions.mjs`, which was proved to catch the bug it targets: removing the `header-btn--active` rule fails it by name, restoring it passes.
- CI green on `add0d48`: all five jobs, nine matrix checks, including the new macOS (arm64) job.
- 9 new TLS tests cover CN truncation at all three cert-generation sites. Their subjectAltName assertions are load-bearing rather than incidental: a future change that truncated the SAN too would sail past a success-only test while silently breaking certificate validity, so the full untruncated hostname is asserted present explicitly. Proved to guard the regression — reverting `_common_name()` to a pass-through fails all 9 with the original `ValueError: Attribute's length must be >= 1 and <= 64, but it was 87`.
- The `/terminal/ws` close-code fix was verified by raw-socket probe in the DTU, not only through `TestClient` — which operates on ASGI messages and never serializes real bytes, and is precisely why the original defect went unnoticed. Pre-fix the wire showed `HTTP/1.1 403 Forbidden`; post-fix it shows `HTTP/1.1 101 Switching Protocols` followed by a real close frame carrying 4409, with a non-owning device still fully refused.
- Five new upgrade tests: the unchanged-version failure (including that it prints the `--refresh` command), the legitimate `--force` no-op, the successful move, an unreadable version treated as failure rather than success, and that the version is read by shelling out rather than importing in-process. Verified on the affected machine by reproducing the original condition, not only by asserting the new code path.


## v0.31.4 (2026-08-01)

### Security

- **ttyd no longer binds to every interface.** `spawn_ttyd()` execs ttyd with `-W` (writable) and no `-c` credential, but until now it also had no `-i` bind flag, so it defaulted to `INADDR_ANY` (`0.0.0.0:7682`). Confirmed live: another host on the LAN, and separately over Tailscale, both got a real ttyd terminal client back (`200`, full HTML), and `GET /token` returned `{"token": ""}` — no credential configured at all. Anyone who could reach the port could type into whatever tmux session was currently attached, with zero interaction with muxplex's own auth stack — `_ws_auth_check`, the cookie/Bearer middleware, TLS never entered the picture. Fixed by adding `-i 127.0.0.1` to the spawn argv, so ttyd now binds loopback only; all legitimate access already went through muxplex's authenticated `WS /terminal/ws` proxy, which dials `127.0.0.1` directly. **This closes at bind time, not retroactively — anyone running muxplex as a service must restart it (`muxplex service restart`) to pick it up.** Not configurable: there is no settings key for this, and if one is ever added it must live in `settings.LOCAL_ONLY_KEYS`, never `SYNCABLE_KEYS` — the federation Bearer key is the same credential handed to remote callers, and a PATCHable bind address would let any of them widen ttyd's exposure right back open.

### Added

- **A device can opt out of the shared session/view selection.** Until now every browser tab and every device converged on the same server-global `active_session`/`active_remote_id`/`active_view` — switch sessions on your phone and your laptop switched too. A device can now join its own private sync group instead (toggle it from the header or Settings > Devices), while the single shared ttyd process stays a safely-arbitrated, single-claim resource regardless of grouping: taking over a different group's live terminal is refused (`409 terminal_conflict`) unless the caller passes `?takeover=true`. Clients that send no `device_id` — muxplex-deck, `muxplex_client`, any existing browser session — are unaffected; omitting it resolves to the `global` group, identical to today's behavior.

### Bug Fixes

- **The interactive terminal pane came back after rendering nothing in v0.31.3.** This release's own sync-groups change introduced the regression, and a user's browser console caught it, not CI. `app.js` declared a top-level `function _ownDeviceId()` (a getter); `terminal.js`, in the same commit, separately declared a top-level `let _ownDeviceId = ''` (private module state). Both load as classic `<script>` tags in `index.html`, which share one global scope — a second script cannot redeclare a binding the first one created there. The browser threw `Uncaught SyntaxError: Identifier '_ownDeviceId' has already been declared` while parsing `terminal.js`, so `terminal.js` never executed at all: the interactive terminal pane rendered nothing, while the grid and previews (all in `app.js`) kept working since that file parsed fine on its own. Each file's own test suite (`test_app.mjs`, `test_terminal.mjs`) loads only that one file in isolation, so neither could ever see the collision — a per-file unit test cannot catch a cross-file collision by construction, no matter how thorough. Fixed by renaming `terminal.js`'s private state to `_termOwnDeviceId`. A new `tests/test_shared_scope.mjs` now parses every non-vendor `<script src=...>` tag out of `index.html` and evaluates them, in order, into one shared context, failing on any collision — any future frontend script is covered the moment it's added to `index.html`.
- **`muxplex update` no longer prints a false "not serving" warning right after a successful restart.** It restarted the service and immediately ran `doctor()` to verify, but the only thing checked was `systemctl --user is-active` — true the instant the process starts, not once uvicorn has actually finished loading settings and binding the configured host:port. Calling `doctor()` in that gap raced a server that was in fact healthy, and a manual `muxplex doctor` moments later showed nothing wrong. `update` now polls the same probe doctor's own "Running:" check uses, on a short interval with a generous ceiling, before verifying. If the service genuinely never comes up in time, that's reported as a real failure — no suppression, no flag to skip verification.
- **Corrected: `/terminal/ws` does not actually close with WS codes 4409/4404.** `docs/API_SEMANTICS.md` and code comments claimed the sync-groups conflict/unknown-device cases closed the WebSocket with those codes. Live raw-socket verification showed otherwise: both `close()` calls happen before `websocket.accept()`, so per ASGI/uvicorn semantics they never produce a real WebSocket close frame — the connection was never upgraded. They serialize as a bare HTTP 403 handshake rejection instead (confirmed: `HTTP/1.1 403 Forbidden`, empty body); a real browser reports close code 1006, never 4409/4404. The existing test assertions were correct at the layer they test — Starlette's `TestClient` operates on ASGI messages and never serializes real bytes, so it couldn't see this gap — but the docs and comments were wrong about what ships over the wire. No behavior change: the real, working recovery path is the ordinary HTTP `409 terminal_conflict` body on the `/connect` escalation POST, which is unaffected. Also added the `docs/AGENT_GUIDE.md` §4 coverage of the `device_id` opt-out and the `409 terminal_conflict` response that the sync-groups spec required and the original change had missed.

### Verification

- 1804 Python tests passed, 15 deselected, in the Digital Twin Universe (`make test`) -- run *after* the version bump, per the v0.31.1 incident above the fold in this same file.
- 696 frontend tests passed (`node --test tests/*.mjs`), including the new `test_shared_scope.mjs` (proved to actually catch this class of bug: reverting the rename reproduces the exact production `SyntaxError`, restoring it passes).
- ttyd's loopback bind reasserted by test (`-i 127.0.0.1` present in the spawned argv) and by the live probe described above.
- **Anyone running muxplex as a service must restart it** (`muxplex service restart`) to pick up the ttyd fix — the running process keeps serving on the old, LAN/Tailscale-reachable bind until it does.


## v0.31.3 (2026-08-01)

### Bug Fixes

- **`muxplex doctor` no longer reports a dead service as running.** `Service: launchd agent running` was decided by two things that both lie: `launchctl print` returning 0 (which only means the *label is loaded*, not that anything is alive) and a probe of the configured port (which any port-forward will answer). On the machine that surfaced this, the job had **no pid and a last exit status of 1**, relaunching in a loop with 15 MB of stderr — and doctor showed a green check the whole time. It now asks launchd for the actual pid and reports `LOADED BUT NOT RUNNING — last exit status N`, pointing at `/tmp/muxplex.err`. A health check that reads green on a crash-looping service is worse than no health check.
- **`Running: vX` now proves the server it found is actually this host's.** `localhost:PORT` is not evidence of locality: an `ssh -N -L 8088:127.0.0.1:8088 otherhost` tunnel makes another machine's muxplex answer on your own loopback, indistinguishable by probing alone. Doctor was reaching a *remote* muxplex through exactly such a tunnel and reporting its version as the local running version — which read as an ordinary stale install and sent a real investigation chasing a service that had in fact never started. The probe now compares the returned `device_id` against this install's own, and when they differ says so plainly, naming the other machine and pointing at `lsof -nP -iTCP:PORT -sTCP:LISTEN`.
- **The port guard now names a tunnel a tunnel.** When something already holds the serve port, muxplex probes it and refuses to kill a healthy peer — correct, but the message said *"port 8088 is already served by a healthy muxplex … Refusing to terminate it"* even when the "healthy muxplex" was a forwarded connection to a different machine. That framing implies a local server worth protecting. It now distinguishes the two: a foreign `device_id` produces an error that says the port is forwarded, shows the likely `ssh -L` shape, explains that this host's muxplex cannot bind while it is up, and declines to kill it on the grounds that the holder is probably the tunnel rather than a server.

### Verification

- Full suite green in the Digital Twin Universe, run after the version bump.
- Five new tests: `launchctl list` parsing for the crash-looping (`-` pid, exit 1), healthy, and unregistered cases; and device-identity matching, including that a missing or absent `device_id` reads as *cannot tell* rather than *ours*.
- Verified on the affected Mac end to end, not just in the container: the tunnel was removed, the launchd job took the port and started (pid 4779), and the host now serves its own build instead of a remote one.


## v0.31.2 (2026-08-01)

### Bug Fixes

- **`muxplex service restart` actually restarts the service again on macOS.** This was a regression introduced in v0.31.1, and it is the worse kind: v0.31.0 crashed loudly on `launchctl bootstrap` exit 5, and the v0.31.1 fix for that traded the crash for a silent lie. `launchctl bootout` returns *before* the job is gone, so `restart` booted the old job out, immediately bootstrapped, got exit 5 because the old job was still tearing down, then checked "is it loaded?" -- saw the **old** job still loaded -- and reported success. The old process kept serving. Running `muxplex service restart` twice produced no output and no error either time, while `muxplex doctor` went on reporting a version from two releases earlier as the running one. Two changes fix it: `bootout` now waits for launchd to confirm the job is actually gone before anything bootstraps on top of it, and "already loaded" now counts as success only for `service start`, whose job is to make sure something is running. For `install` and `restart` -- which have just removed the old job on purpose -- a surviving job means the replacement failed, and that now fails loudly with the plist path and the manual commands.
- **`muxplex doctor` no longer tells macOS users to run `systemctl`.** The stale-version warning and the port-conflict error both printed `systemctl --user restart muxplex` unconditionally, which is meaningless on a machine where launchd is the service manager -- and it appeared right next to a line correctly reporting `Service: launchd agent running`. Both now print `muxplex service restart`, which is correct on every platform, so there is nothing to branch on.

### Verification

- Full suite green in the Digital Twin Universe, run *after* the version bump.
- Three new tests cover the regression directly: that `install`/`restart` refuse to call a surviving old job a success, that `start` still accepts an already-running service, and that `bootout` polls until the job is really gone rather than returning early.
- Honest limit, unchanged from v0.31.1: these tests exercise the logic, not `launchctl`. That binary is macOS-only and this suite runs in a Linux container, so the real launchd behaviour is verified by the shape of the calls, not by running them. The race and the false-success path are both provable in the code; whether they are the *whole* story on any given Mac is not something this suite can attest.


## v0.31.1 (2026-08-01)

### Bug Fixes

- **The status bar no longer shows your session name twice.** `status-right` displayed `#{b:pane_current_path}` -- the basename of the current directory. amplifier-workspace derives tmux session names *from* the directory basename, so for any workspace session that rendered the exact string already shown in the session badge on the left. The same name, twice, costing about 22 columns that the window list wanted. Removed rather than shortened: the badge on the left already answers "where am I", and `status-right-length` drops 60 to 40, so the clickable window list gains again.
- **The clock shows AM/PM again.** It silently became 24-hour (`%H:%M`) in the v0.31.0 theme rewrite. Restored to `%I:%M %p`.
- **`muxplex update` no longer crashes on macOS with a launchd traceback.** The upgrade installed correctly, printed "Service started", and then died with a raw `CalledProcessError` on `launchctl bootstrap gui/501 ... exit status 5`. `launchctl bootout` returns *before* the job is actually gone, so a bootstrap issued into that window fails with exit 5 (EIO). That is a race, not a failure -- and `check=True` turned a path that had in fact succeeded into a traceback out of subprocess internals. Bootstrap now retries through the teardown window, treats an already-loaded service as success (a running service is the only outcome the caller cares about), refuses to retry genuine errors, and on real failure raises an actionable message carrying launchd's own stderr plus the manual command and the `muxplex serve` fallback. `muxplex service start` had the identical crash and now shares the same path.
- **`muxplex-client` version matches the server again.** v0.31.0 shipped with `client/pyproject.toml` still at 0.30.1. The repo has a test that exists precisely to catch this, and it did not, because the release ran the suite *before* the version bump and never re-ran it after. The test was right; the process was wrong.

### Verification

- 1729 tests passed in the Digital Twin Universe, 0 failed -- run *after* the version bump this time.
- Four new tests cover the launchd bootstrap retry, the already-loaded case, loud failure on a genuine error, and no-retry-on-non-race-errors. They exercise the logic, not `launchctl` -- that binary is macOS-only and this suite runs in a Linux container, so the real launchd behaviour is verified by the shape of the calls, not by running them. Stated plainly rather than implied.
- Fixing the launchd path surfaced that 24 test mocks returned `None` from `subprocess.run`, which always returns a `CompletedProcess` in reality. The mocks were lying about the contract and got away with it while nothing read the result. Fixed the mocks rather than making production code defensive about a test artifact.


## v0.31.0 (2026-08-01)

### Added

- **muxplex now manages your tmux configuration.** `muxplex tmux install` ships an opinionated tmux config and wires it up by adding one guarded `source-file -q` line to your `~/.tmux.conf`. This exists because the package that used to own that config is being retired, and muxplex is the natural home -- it already owns tmux *behaviour* settings (`new_session_template`, `tmux_socket_dir`, `window_size_largest`) and has three co-equal configuration surfaces. The measured detail the whole design rests on: tmux >= 3.1 loads *every* user config in its search path, not just the first one found, and the later file wins conflicts. So the block installs into `~/.tmux.conf` -- the earliest file -- at the top, which puts muxplex first in the chain and means anything you have set, anywhere, overrides it. That is deliberately the opposite of the conda/rustup/nvm convention: they install last because they want to win, muxplex installs first because it wants to lose. Everything muxplex generates lives in `~/.config/muxplex/tmux.d/`; `90-local.conf` is created once and never written again, so it is yours and it loads last. Because this is the only place muxplex writes a file it did not create, every write is backed up first, written atomically, verified by re-reading the file *and* by starting a throwaway tmux server on a private socket, applied to your running server so you see it immediately, and removable with `muxplex tmux uninstall`, which restores the file byte-for-byte. A symlinked tmux.conf -- usually a tracked dotfiles repo -- is refused unless you pass `--allow-symlink`, and even then the symlink is resolved first so it is never replaced by a regular file.
- **A `tmux_theme` setting**, defaulting to `brand`, with `steel` and `catppuccin-mocha` as alternatives. `brand` is built from muxplex's own UI tokens, so a window that rings a bell turns the same amber in your terminal that its tile turns in this dashboard -- one signal, one colour, two surfaces. Deliberately not federation-syncable: it renders to a file on this host, exactly as machine-scoped as `tmux_socket_dir`.

### Bug Fixes

- **Clicking a window label in the status bar switches to that window again.** The `MouseDown1Status` binding and `status-format[0]` were byte-identical to tmux's defaults the whole time -- the binding was never the problem. The cause was an unbounded `#{pane_current_path}` in `status-right`. tmux gives the window list whatever columns `status-left` and `status-right` do not take, so a deep path pushed windows behind the `>` truncation marker, and a window that is not on screen has no mouse range to click. `status-right` now uses `#{b:pane_current_path}` and `status-right-length` drops from 120 to 60, returning 17 columns of clickable window labels on a 179-column client. Worth stating plainly because it is the kind of bug that sends you looking in exactly the wrong place: nothing about the mouse was broken; the thing you were trying to click had been pushed off the bar.
- **Every status-bar segment now paints its own background.** Only the session badge looked padded before. A terminal cell's background fills the whole character cell including the line-height leading, so a segment with a background reads as a padded cell and one without reads as bare text floating on the bar -- which looks exactly like inconsistent vertical padding. The window cells sat on a colour 10 RGB-steps from the bar (invisible in practice) and the `status-right` path painted no background at all. All three shipped themes were affected and all three are fixed.

### Verification

- 1725 tests passed in the Digital Twin Universe (`make test`), up from 1721 -- 28 of them cover the tmux config feature, all exercised against a real tmux server on private sockets with paths redirected to a sandbox.
- The four new regression tests immediately caught that `steel` and `catppuccin-mocha` carried both status-bar bugs too; the assertions are structural (bounded left+right column budget, no unbounded path format, every segment paints a background, window cell backgrounds at least 40 RGB-steps from the bar) so a future theme cannot reintroduce either bug.
- `ruff check` and `pyright` clean on all new and changed files.
- Installed and running on a live server with 51 sessions throughout: session count and server PID unchanged across every step, including the live theme reapply.


## v0.30.1 (2026-07-30)

### Bug Fixes

- **The soft deck's grid no longer collides with the dial and touch strips.** The grid was being sized correctly for the space above the strips but then centred against the full viewport, so it sat too low and its bottom row disappeared behind them — 50px of overlap with dials on a phone in landscape. Separately, the two strips both anchored to the bottom of the screen rather than stacking, so with dials and the touch strip both enabled the strip painted over the lower part of every dial. A code comment claimed that stacking already happened; it never did, and that comment is corrected. Contrary to the original report this was not limited to having both enabled — dials-only and strip-only were already overlapping at phone-landscape sizes and only looked correct on larger screens, where extra letterboxing happened to hide it. Worth noting: this was invisible to overflow-based checks because `position: fixed` elements never enlarge document flow — `scrollHeight` equalled `clientHeight` in every broken configuration. The new tests compare element rectangles directly instead.

### Verification

- 685 frontend tests passed (Node 22, baseline 680 + 5 new).
- No Python changes (frontend + CSS only).
- All 15 dial/strip × viewport combinations tested in real Chromium against a scratch instance: every grid/strip and strip/strip overlap value is ≤ 0 (negative = letterbox slack). Screenshot at 844×390 with both enabled confirmed visually clean.


## v0.30.0 (2026-07-30)

### Added

- **The soft deck's touch strip now shows live status, like the Stream Deck+ does.** On the hardware, that strip is an LCD the sidecar repaints on every poll with the current view, page, session count and active session — and its touch input is deliberately inert, so it is purely a display. The soft deck's strip previously showed only static text naming what each zone was bound to, which told you how it was wired but never what the system was doing. It now carries the same live headline the hardware does. Hostname is omitted: `DESIGN_LAYOUT.md` §1 already ruled it out of the phone deck as install-constant rather than state, and that reasoning applies here unchanged. The strip's own tap, drag and swipe gestures are unaffected — they are a soft-deck-only addition, since the physical strip's touch does nothing — and the status line shares the strip's existing footprint rather than growing it.

### Verification

- 680 frontend tests passed (Node 22, baseline 671 + 9 new).
- No Python changes (frontend only).
- Live status line identical with dials present and absent, still populated when nothing is bound, and mid-drag it changed `all · p1/3 · 8 sessions · ACTIVE: sess-3` → `all · p2/3 · …` read back from the DOM.

## v0.29.0 (2026-07-30)

### Bug Fixes

- **You can now find the soft deck's settings.** v0.27.0 shipped them behind a 600ms motionless long-press on the VIEW key with nothing on screen to suggest it existed — the person who asked for the feature couldn't find it. Tapping VIEW already repaints the grid with the view picker, so Settings is now a key on that page: no permanent pixels, no permanent key slot, reached by a tap people already make. It is a real focusable button with an accessible name, so a screen reader can find it too — the old long-press was a timer on a pointer event and had no presence in the accessibility tree at all. The long-press survives as a shortcut, now with the same 8px movement tolerance the dials use and a filling ring while it arms, so a hold that fails tells you it failed instead of doing nothing. On a grid too small to carry control keys, or one with only a single free slot, the Settings key is omitted rather than displacing the picker's own job.

### Verification

- 671 frontend tests passed (Node 22, baseline 664 + 7 new).
- No Python changes (frontend + docs only).
- Soft deck settings now discoverable: tap VIEW to open picker, Settings key visible and tappable, long-press shortcut now has a filling ring and 8px movement tolerance (same as dials), settings panel opens. Accessible name verified via Playwright `aria_snapshot()`.

### Note on Design Documentation

The design documents were amended in this commit: `DESIGN_SOFTDECK.md` rejected long-press by name and `DESIGN_LAYOUT.md` said "no settings gear," and both now carry dated addenda recording what shipped and why. `DESIGN_LAYOUT.md`'s 52px header is marked specified-but-never-built.

## v0.28.0 (2026-07-30)

### Features

- **The soft deck now has an emulated touch strip.** Tap a zone, swipe left or right, or drag — each bindable separately, addressed as `strip.N.tap`, `strip.N.drag`, `strip.swipe.left`, and `strip.swipe.right`. Swipe and tap drive the same momentary actions the keys use, and drag emits the same signed ticks the dials do, so the existing 19-action catalog works on it unchanged. One genuinely new action, `brightness_set`, takes an absolute position along the strip — the touch strip's canonical use on the hardware, and the one thing the existing catalog had no way to express. It lives in its own table so the 19-action catalog that mirrors the Stream Deck sidecar stays byte-for-byte intact. Gestures are disambiguated with the same 8px/300ms tap threshold the dials already use, and a zone with a bound drag never also fires a swipe.

### Verification

- 664 frontend tests passed (Node 22, baseline 634 + 30 new).
- 1696 Python tests passed (no regression).
- All CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend.
- Touch strip verified on real Chromium at phone landscape (844×390): tap-to-single-fire on zone, swipe-left −10 / swipe-right +10 measured by CSS brightness delta, drag continuous and absolute (zone baseline to 80% → brightness 80%, verified mid-gesture).
- `brightness_set` action verified callable and delivering absolute position.

## v0.27.0 (2026-07-30)

### Features

- **Sort your session lists, including by attention.** The dashboard and sidebar now have a sort selector — manual, alphabetical, recent, and attention (bells first, then by activity) — matching the ordering the Stream Deck sidecar has had for a while. The choice is the existing server-synced `sort_order` setting, so it follows you across devices.

- **The soft deck has a settings menu.** Long-press the VIEW key to open it. Rebind any key from the full action catalog, override the grid (rows × columns) per device, add up to four emulated dials — drag to turn, tap to push — and set sort, poll interval, and brightness. Config is per-device with export/import, since a phone and a tablet genuinely want different layouts. `?settings=1` and `?reset=1` always work, so a configuration that locks you out of the UI is still recoverable.

### Bug Fixes

- **The sidebar was never applying your sort order at all.** It always rendered raw server order, silently disagreeing with the grid regardless of what the setting said. Both surfaces now share one ordering path.

### Verification

- 634 frontend tests passed (Node 22, baseline 592 + 42 new).
- 1696 Python tests passed (no regression).
- All 8 CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend.
- Soft deck settings verified on real Chromium at landscape (844×390): long-press opens settings, 3×5 override renders exactly 15 keys, dial count 2 renders 2 dials, dial tap fired `brightness_down`, 2-tick drag fired `brightness_cycle`, `?reset=1` restored defaults.
- Sort order verified to apply consistently to dashboard, sidebar, and grid.

## v0.26.1 (2026-07-30)

### Bug Fixes

- **The dashboard now respects your phone's rotation lock.** Its manifest declared `"orientation": "any"`, which reads like "no preference" but is not — per the Web App Manifest spec it means "allows the app to rotate freely to match the orientation of the device," and Android bakes that into the installed app as sensor-based rotation that applies *even when the user has locked rotation*. v0.26.0 fixed the dashboard opening stuck in landscape but left this in place, so it swapped one wrong behavior for another: free rotation that ignored the lock. The orientation preference is now omitted entirely, which is what leaves the device's own setting in charge. Browser tabs were never affected — manifest orientation only applies to an installed app. The deck's deliberate forced landscape is unchanged.

### Note for Existing Users

Anyone with the dashboard already installed must remove it from their home screen and re-add it — Android caches a PWA's manifest at install time, so a server-side change does not reach an already-installed icon.

### Verification

- 457 frontend tests passed (Node 22).
- Real hardware verified: device rotation lock now honored on the dashboard; deck's landscape lock is unchanged.

## v0.26.0 (2026-07-30)

### Bug Fixes

- **muxplex no longer runs the tmux server inside its own service cgroup.** When no tmux server is running, muxplex starts one — and until now that server became muxplex's child, which put it in the service's control group. Anything that stopped the service could then take every session on the machine with it. v0.24.0 shipped `KillMode=process` to stop systemd doing that, but that was a guard on the unit file, not a fix for the relationship: edit the unit, or run muxplex under a different supervisor, and the hazard came back. Session-spawning subprocesses now run in their own transient systemd scope, so the tmux server is never in muxplex's cgroup to begin with. Verified by deliberately setting `KillMode=mixed` and restarting: the sessions survive anyway. On macOS, and on Linux without a usable systemd user session, there is no such cgroup and nothing changes — and if the escape is expected but genuinely unavailable, it is logged loudly rather than passed over in silence.

- **The dashboard no longer opens sideways after you have visited the deck.** The deck deliberately forces landscape. Because the dashboard's scope covers the whole site, reaching the deck from inside it kept you in the same browsing context, and the deck's orientation lock could outlive the deck and follow you back. The dashboard now releases any inherited lock when it starts. The deck's forced landscape is unchanged.

### Verification

- 1692 Python tests passed (baseline 1676 + 16 new tests covering cgroup escape on mixed KillMode, escape unavailability logging, macOS no-op, and dashboard orientation release).
- cgroup escape verified in a DTU: tmux server moved from `.../muxplex.service` into `.../run-<id>.scope`, and sessions survived a `systemctl restart` with `KillMode=mixed` deliberately set — a test that fails fatally under the old code and passes under the new one.
- Dashboard orientation unlock verified on real hardware: visited deck (locked to landscape), returned to dashboard, confirmed dashboard immediately reverted to any-orientation behavior.
- 583 frontend tests passed (Node 22).

## v0.25.0 (2026-07-29)

### Features

- **`muxplex restore` now actually restores.** v0.24.0 taught muxplex to remember which sessions existed and to tell a deliberately-closed session apart from one lost when the tmux server died. This release acts on that: it recreates the lost ones, using the same `new_session_template` that created them in the first place, so a restored session comes back with the structure it had rather than a bare shell. Sessions you closed on purpose are never resurrected. Running it twice does nothing the second time. If some sessions fail to come back it names exactly which and exits non-zero rather than reporting a partial success as a win. It asks before creating anything — `--yes` skips the prompt for scripted use.
- **Restore runs in your shell, not inside the service.** Deliberate. muxplex spawns the tmux server as its own child when none is running, which is what let a service restart take out 44 sessions in the first place; creating sessions from a short-lived command in your own shell keeps restore out of that path entirely, and works whether or not the service is running.

### Verification

- 1676 Python tests passed (baseline 1655 + 21 new restore tests covering full restore with 45 sessions, tombstoned session immunity, idempotency, partial failure handling, and exit code verification).
- Restore verified on isolated tmux servers: full 45-session restore with all four windows and cwd asserted for every session, tombstoned session staying dead, idempotency (second run is no-op), and partial failure (names the failed session, exits non-zero).
- API endpoints for restore are not implemented — restore is CLI-only by choice. A one-tap restore button on a phone is not the right affordance for an operation that spawns dozens of processes, not until it has a proven track record.

## v0.24.0 (2026-07-29)

### Bug Fixes

- **The service unit shipped a setting that could destroy every session on the machine.** `muxplex service install` wrote `KillMode=mixed`, which tells systemd to SIGKILL every process in the service's control group whenever the service stops. Because muxplex starts the tmux server itself when none is running, that server became muxplex's child — so any restart, upgrade, or crash-loop was a mass kill of every session on the box. This is not hypothetical: it destroyed 44 live sessions on the author's machine on 2026-07-29. New installs now get `KillMode=process`, which signals only muxplex itself. **Anyone who installed the service before this version should reinstall it** — the old unit file is still on disk and still carries the old setting.

### Features

- **muxplex now remembers which sessions it has seen, so a lost tmux server is recoverable.** Until now tmux was the only record of what existed; when the server died, that knowledge died with it. muxplex keeps a small manifest and can tell the difference between a session you deliberately closed and one that vanished because the server underneath it went away — the first is never resurrected, the second becomes a restore candidate. `muxplex restore --dry-run` shows what a restore would do. **Restore execution is not in this release**; nothing is created, killed, or restarted by this change.

### Verification

- 1655 Python tests passed (1626 baseline + 29 new tests covering manifest recording and restore candidate selection).
- A real tmux server was killed in a DTU and the manifest survived it with the right sessions marked as restore candidates; a deliberate single-session kill was correctly tombstoned and would never be resurrected.
- Service unit verified to contain `KillMode=process` instead of `mixed`, and no other tmux-killing paths remain in the codebase besides user-initiated `delete_session_template`.

## v0.23.0 (2026-07-29)

### Bug Fixes

- **The deck's three control keys were blank.** `controlKeyContent` computed the right labels, was exported, and was covered by nine tests — but nothing ever called it. The renderer was handed the context where the content belonged, so every field came back undefined and painted as an empty string. The keys are now wired to the function that was always meant to fill them, and the render path was restructured so that a key face can only be painted from a computed plan — there is no longer a second route from state to the screen for that bug to hide in.

- **The deck listed sessions alphabetically instead of by attention.** It asked the server for a view without saying how to sort it, so the server fell back to alphabetical while the hardware decks defaulted to attention order. The two now agree.

- **Long session names were clipped without warning.** The deck left fitting to the browser, which trims a centred label from whichever side overflows and leaves no mark that anything was cut. Names are now measured and truncated the same way the hardware does it, so what you see is always a real prefix followed by an ellipsis.

### Features

- **The hidden view is now reachable from the deck.** It was possible to hide a session from a deck and then have no way to bring it back, because the server left `hidden` out of the browsable view list.

### Verification

- 1626 Python + 583 frontend tests passed (DTU and Node 22).
- All four fixes proven by reading the DOM in real Chromium at 915×412 against scratch instance.
- Golden fixture proven to fail on drift (perturbed value → red; restored → green).
- No remaining path from state to a painted key that bypasses the plan.

## v0.22.0 (2026-07-29)

### Features

- **The deck is now a software Stream Deck rather than a web page.** It draws a fixed grid of keys sized so every key is legible at arm's length, fits as many as the screen allows, and shows them all at once — there is no scrolling. Extra sessions move to further pages reached by dedicated previous and next keys, exactly as the hardware does, and choosing a view takes over the whole surface with a back key instead of sliding a panel over the grid. The key faces, the three-zone layout, and the control vocabulary are the same ones the physical decks use, so the two surfaces behave alike. On a phone in landscape this works out to a four-by-eight grid — the same geometry as the largest hardware deck.

- **Installed apps now carry proper names** — "Muxplex" and "Muxplex Deck" — instead of lowercase placeholders.

### Bug Fixes

- **The Android certificate instructions sent people to the wrong place.** Android offers two ways to install a certificate and only one of them accepts a certificate authority; the instructions did not say which, so following them produced a refusal with no explanation. The steps now name the correct choice, warn about the one that looks right and is not, and note that the browser must be fully closed and reopened rather than merely reloaded.

### Verification

- 1625 Python + 567 frontend tests passed.
- All eight CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend (node:test).
- Deck grid responsive to phone form factor: 4×8 keys on flagship (915×412), 3×7 on compact (780×360), no scroll and no overlap at either resolution.

## v0.21.0 (2026-07-29)

### Features

- **Installing the certificate is now a link, not a chore.** A new setup page at `/setup` offers the certificate authority for download with step-by-step instructions for the device you're on, and `/ca.crt` serves it with the content type Android recognises. Until the certificate is installed a browser marks the site untrusted, which — beyond the warning — silently prevents installing the deck as a real app, because browsers refuse to install from an origin they consider insecure. Both pages are reachable without logging in; only the public certificate is served, never the private key.

- **The deck now installs as a genuine fullscreen app in landscape.** It previously asked to be a standalone window, which is not enough for a browser to honour a fixed orientation — the deck now declares fullscreen, so it locks to landscape on its own instead of requiring the phone's rotation lock to be toggled by hand. A minimal service worker was added purely to satisfy the install requirement; it caches nothing, deliberately.

### Verification

- 1625 tests passed (baseline v0.20.1: 1604 passed, +21 new tests covering PWA setup and service worker integration).
- All eight CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend (node:test).
- Deck installability validated end-to-end on Android Edge: setup page accessible without login, CA certificate downloadable with correct MIME type, app installs fullscreen and locks to landscape, no scroll or dropdown menus, control grid responsive to phone form factor (3×7 on compact, 4×8 on flagship).

## v0.20.1 (2026-07-29)

### Bug Fixes

- **Deck tiles overlapped each other on a phone held upright.** Rows in the tile grid were sized from the longest unbroken word in each tile rather than from the tile's actual rendered size, so every tile was drawn taller than the space reserved for it and painted over the one below — leaving terminal previews stacked on top of each other and unreadable. Rows are now sized from the tiles themselves. Landscape and tablet layouts were unaffected and are unchanged.


## v0.20.0 (2026-07-29)

### Features

- **A new `/deck/` page turns a phone or tablet into a session switcher.** Propped beside the laptop, it shows the sessions in the current view as tiles; tapping one switches the laptop to that session. It can be installed to a phone's home screen as its own app. Scope is deliberately narrow — switch session and change view, nothing else.

- **The deck shows what it actually knows.** A tap shows a distinct in-flight state rather than claiming success before the server has answered, and reverts visibly if the request fails. If the server stops responding, the whole grid dims rather than continuing to present stale tiles as current.

- **The screen is kept awake while the deck is open**, with a control that reports the true state rather than an assumption, and can be toggled.

### Bug Fixes

- **Logging in no longer discards where you were going.** The login form always redirected to the site root, so a deep link was lost and — for anyone running the deck as an installed app — logging in ejected them out of their app into a browser tab showing the terminal. The intended destination is now preserved and validated as a same-origin path, rejecting absolute URLs, protocol-relative URLs, embedded schemes, and path traversal.

### Verification

- 1604 tests passed (baseline v0.19.0: 1412 passed, +192 new tests covering the deck route and authentication validation).
- All eight CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend (node:test).
- Deck functionality verified end-to-end in a real browser against a scratch-instance muxplex server: session tile tap confirmed active_session changed on the server, and visual state transitions (in-flight, success, error, stale-server dim) all verified.
- Link preservation validated: deep links like `/deck?view=2&session=work` survive login and redirect correctly.

## v0.19.0 (2026-07-26)

### Features

- **A first-class Python client, published as `muxplex-client`.** The Stream Deck sidecar had been hand-rolling 264 lines of httpx against four endpoints, and the Amplifier tool module planned next would have written a second copy — while AGENTS.md already carried a section titled "Semantics external clients re-implement today (change with care)," a standing admission that this duplication was a known drift hazard. The client ships as a separate distribution from the same repo and the same tag, depending only on httpx: consumers install a laptop-friendly package rather than an ASGI server, a PAM binding, and a `muxplex` console script that would collide with a real server's config directory. Sync and async clients share one I/O-free core holding all parsing, error mapping, and sentinel logic, so the two transports differ only in the shape of their awaits — both are genuinely required, since converting the deck's threaded HID architecture to async would be a rewrite while a synchronous call inside an Amplifier tool would block the event loop. The surface is twelve methods against roughly twenty-nine endpoints, deliberately excluding `PATCH /api/settings`: a convenience wrapper there is exactly the shape of the views-collapse incident, and doing it safely needs compare-and-swap plus 409 backstop discrimination built in, which is a v2 design rather than a v1 convenience. `run_shell_command()` composes the completion-sentinel convention over public primitives, carrying the digit-anchor rule that keeps tmux's input echo — which shows the literal unexpanded `$?` before the shell runs — from producing an instant false "done" with a bogus exit code.

- **A contract test that makes the second distribution safe.** Living in the server's own suite and driving the real ASGI app in-process, it asserts that the client's parsed fields match the raw endpoint JSON, that its key allowlist equals `terminal_input.ALLOWED_KEYS`, that its capture-depth constants match `sessions.py`, and that `Bell.needs_attention` agrees with the server's implementation across an eight-row truth table. This is what a separate repo structurally could not do: it catches a client/server break in the same pull request that introduces it, rather than against the last published server.

### Internal

- **The release path now knows the second distribution exists.** A bare `uv build` builds only the root package, so a tag would have published half the release behind a green workflow; the publish step now uses `--all-packages`. Root `testpaths` confined collection to the server's tests, so the client's suite would never have run in CI; a dedicated `test-client` job now runs it across all three supported Python versions. A contract test asserts both `pyproject.toml` versions match, so a release that bumps one and forgets the other fails in the same pull request instead of publishing mismatched wheels.

### Verification

- 1570 tests passed / 5 deselected in muxplex server suite (baseline v0.18.0: 1546 passed, +24 new tests for client integration). The new `muxplex-client` package runs 44 pure-contract tests across Python 3.11/3.12/3.13 in CI.
- All six CI jobs green on the feature commit: frontend (node:test), Python 3.11/3.12/3.13, test-latest-deps, and the new test-client matrix (3 versions).
- Contract test verified: `test_client_version_matches_server_version`, `test_client_fields_match_endpoints`, `test_client_allowlist_matches_server`, and `test_bell_needs_attention_consistency` all pass.
- Both distributions published to PyPI from the same v0.19.0 tag: muxplex and muxplex-client.


## v0.18.0 (2026-07-26)

### Bug Fixes

- **Accepted `/input` calls are now actually audited.** `uvicorn.run(..., log_level="info")` configures only uvicorn's own loggers; it never touched the root logger or any `muxplex.*` logger, so every module logger sat at WARNING with no handler attached. Every `_log.info` audit line for an accepted terminal-input call was silently discarded — while the *rejected*-input warning appeared normally, since Python's handler-of-last-resort surfaces WARNING and above. That asymmetry hid the defect. `configure_logging()` now scopes the `muxplex` package logger to INFO with a single idempotent handler, deliberately not configuring the root logger, which would drag every third-party dependency to INFO and bury the audit trail. This matters beyond tidiness: the agent guide documents this log as the operator's record of what their agents typed, and lists it as one of only three protections remaining in the wide-open `input_allowed_sessions: ["*"]` posture. The guarantee was documented but not delivered. Notably a test already covered this line and stayed green throughout, because it used `caplog.at_level(logging.INFO, logger="muxplex.main")` — forcing the very level it was meant to verify. The replacement starts from an unconfigured logger, asserts the broken state is real, then proves a record reaches an independently attached handler.

- **The tmux bell hook now self-heals.** Startup registration was wrapped in `except Exception: pass` with a comment promising the hook would be set on the first poll — but nothing anywhere re-registered it. The failure it names, tmux not yet running at startup, is the normal case on a fresh boot, so the most likely path left bells permanently dead with no error, no log, and no signal. Confirmed in a clean container: bell counts frozen until `/api/internal/setup-hooks` was called by hand. Registration is now a single shared helper called from startup, the poll cycle, and the manual endpoint, retried from the poll cycle only while genuinely unarmed — so it heals on the next 2-second cycle and costs a boolean read thereafter rather than a subprocess every cycle forever. Failures log at WARNING with the real tmux error, and `GET /api/instance-info` exposes `bell_hook_armed` so the state can be queried rather than inferred.

### Features

- **Caller-controlled read depth for pane snapshots.** `capture_pane()` hardcoded a 30-line window, so `seq 1 100` returned only lines 48 onward with no API able to recover the rest — a hard ceiling for any agent running `pytest -v`, a build, or anything else output-heavy. `POST /api/sessions/{name}/input` now accepts an optional `lines`, and a new `GET /api/sessions/{name}?lines=N` performs a live single-session capture without typing anything, covering the case the read-back structurally cannot: polling a long job started earlier. The default stays 30 so existing callers are unchanged; the maximum is 2000, and an out-of-range value is a 400 rather than a silent clamp, because a caller believing it received 5000 lines while getting 2000 is worse than an explicit rejection. tmux `history-limit` is now set explicitly per session, since sessions are created through an operator-configurable template and muxplex never controlled retained scrollback — without it a deep request could return fewer lines than asked for, a worse lie than the original honest ceiling. The bulk `GET /api/sessions` deliberately does *not* take a depth parameter: it serves one shared poll cache consumed simultaneously by the PWA, the Stream Deck sidecar, and agents, and a per-request override there would either fork that contract or fan out one live tmux call per session on every request.

### Documentation

- **The agent guide now documents proven unattended-operation patterns.** A completion-sentinel convention is the primary recommendation, verified end-to-end against a live instance for both a successful long command and a nonzero exit — `last_activity_at` alone cannot distinguish a command running silently from one finished at an idle prompt, and that ambiguity is the actual blocker on unattended operation. A bell-on-nonzero-exit convention is documented alongside it, with the explicit warning that nothing an agent naturally runs will ring a bell otherwise. The poll-cache guidance was corrected from a guessed "sleep ~3s" to the measured reality — a just-created session resolved on the third attempt at 0.3s spacing, under one second — with a retry loop rather than a blind sleep. AGENTS.md's copy of the same claim was corrected to match.

### Verification

- 1546 tests passed / 5 deselected in an isolated Digital Twin Universe container (baseline v0.17.0: 1522 passed, +24 new tests covering the audit-log fix, bell self-heal, and sentinel completion patterns).
- All five CI jobs green: frontend (node:test), Python 3.11/3.12/3.13, and test-latest-deps (mirrors user install behavior).
- Sentinel completion detection verified end-to-end against a live instance: `sleep 8 && echo` matched at 8.15s with exit_code=0, `sleep 3; false` matched at 3.03s with exit_code=1.
- Bell hook self-heal verified: fresh DTU with hook registration failure, then polled until hook healed on the next cycle.
- Audit log verified: 12 accepted `/input` calls → 12 matching INFO lines in audit log.

## v0.17.0 (2026-07-26)

### Bug Fixes

- **Selecting a session no longer snaps back to the previous one.** `openSession()` sets the local viewing session synchronously on a sidebar click, then fires `PATCH /api/state` fire-and-forget. A dedicated 1-second `/api/state` poll (added in `2f50c22`) meant a poll frequently landed in the window before that PATCH reached the server, read the server's still-stale `active_session`, concluded a *remote* device had switched away, and force-reopened the previous session. The 1s poll did not create the race; it made it common. A pending-write counter now tracks genuine local switches — incremented on a real user switch (not on `restoreState()` or the follow logic's own re-opens), decremented when that switch's PATCH actually settles rather than after a guessed timeout — and a divergent remote read is ignored while any local switch is in flight. Cross-device following is unchanged; that feature is the entire reason the PWA watches remote state.

- **TMUX_TMPDIR now propagates into the installed service environment.** A user who set `TMUX_TMPDIR` in their shell, ran `muxplex service install`, and never set the `tmux_socket_dir` setting got a service pointed at the default socket directory — `GET /api/sessions` returned an empty list with no error, and live sessions were simply invisible. Reproduced end-to-end in an isolated container: a real session under a custom socket dir was completely absent while `/api/instance-info` reported `/tmp/tmux-0`. The installer now bakes its own `TMUX_TMPDIR` into the systemd `Environment=` line and the launchd plist, mirroring the existing PATH propagation, and re-installs restart (systemd) or bootout-then-bootstrap (launchd) so a changed value actually applies. An explicit `tmux_socket_dir` setting remains authoritative; only the fallback changed.

### Documentation

- **A vendor-neutral agent usage guide.** `docs/AGENT_GUIDE.md` is a document any agent can be pointed at — Amplifier, Claude Code, Codex, or a shell script with curl. It carries the reasoning that is not derivable from the source: that the asset protected by the input fences is the operator's own live pane rather than the host, that the endpoint's intended caller holds the same Bearer key as its most capable attacker, why the allowlist check runs before the existence check (so a 403 cannot be used as an existence oracle), why glob matching casefolds both sides rather than relying on platform-varying `fnmatch`, and why an empty allowlist denies rather than permits. It states plainly that the security boundary is the allowlist and not the content — typing an executable line into a shell pane will run it, by design — and documents both the scoped and wide-open postures honestly, including which protections remain in each. It also documents the ~2s poll-cache race that makes a just-created session 404 on `/input`, as a known race with a retry pattern rather than a mystery.

### Internal

- Source-text assertion tripwires in the frontend test suite were deduped and documented.
- A doc claim in AGENTS.md asserting an end-to-end canary test that does not exist was corrected to cite the test that does (`test_input.py` `test_text_sent_literally_via_argv`, which asserts the exact argv). A doc claiming evidence it does not have is the same fabricated-attestation failure this repo's testing rails exist to prevent.

## v0.16.1 (2026-07-26)

### Bug Fixes

- **Autofill suppression extended from one input to every input where it belongs.** v0.15.1 fixed the new-session name field; the rest of the PWA's text inputs were still bare. Now covered: the "+ New View" name inputs in both the header and sidebar view dropdowns, the inline view-rename input, the remote-instance URL and display-name inputs, and — in `index.html` markup — the terminal search box and the device-name setting. The seven attributes (`autocomplete`, `autocorrect`, `autocapitalize`, `data-1p-ignore`, `data-lpignore`, `data-bwignore`, `data-form-type`) now live in one `AUTOFILL_SUPPRESSION_ATTRS` constant applied by one `_suppressAutofill()` helper, rather than being copy-pasted per call site. The two static inputs carry the attributes as literal markup rather than via the helper, deliberately: Chrome scans the DOM for autofill targets at parse time, before app.js runs, so markup is the only placement early enough to matter. That markup/JS duplication is held in sync by a test that derives its expectations from the exported constant, so adding an eighth vendor attribute without updating the markup fails CI instead of silently drifting.

  Three exclusions are deliberate and each is now pinned by a test, so a future "apply it everywhere" sweep cannot quietly break them: **login.html** (both inputs keep `autocomplete="username"` / `current-password` — it is the one form where password managers are wanted, and it was left byte-for-byte untouched), the **federation key** input in `_buildRemoteInstanceRow` (`type="password"` — a genuine secret a user may legitimately keep in their password manager), and the six **settings checkboxes** (autofill does not target checkboxes).

  Scope note: this sends every documented opt-out signal each vendor publishes. It does not, and cannot, prove a given password manager honors them — that is vendor behavior no test here can exercise.

### Internal

- **Four frontend assertions in `test_frontend_js.py` were pinning implementation shape, not behavior.** They regex-extracted the body of `_createSessionInput` and asserted the literal strings "autocomplete" and "spellcheck" appeared inside *that function*. Factoring the attributes into the shared `_suppressAutofill` helper moved those literals one call away and failed all four Python jobs, while the behavior was not merely intact but extended to five more inputs — a false negative, and the same class of stale-source-assertion breakage recorded in v0.13.0. They now follow the indirection and assert both halves of the chain: that the factory delegates to `_suppressAutofill()`, and that the helper/constant is what actually sets the attribute. The guarantee is unchanged; only the shape it is pinned to moved. Caught by CI, which is the only place the Python suite can run when the dev host is also serving muxplex.

## v0.16.0 (2026-07-26)

### Features

- **Surface the running version, not just the installed one.** `muxplex doctor` reported only the INSTALLED version, so a machine that had been upgraded but whose service had not been restarted looked perfectly healthy while still serving stale code — the exact situation that stranded a live server on 0.14.0 while 0.15.0 sat installed. Doctor now probes the locally-configured host and port and reports three distinct states: matching (`Running: v0.16.0 (matches installed)`), mismatched (naming both versions plus how to restart), and not serving (a normal state, worded so it can never be confused with staleness). The PWA gained a read-only Version field under Settings → Display, populated from the `/api/instance-info` fetch it already makes — no additional network call. Federated devices now carry `deviceVersion` through the existing federation path, surfaced in device-badge tooltips, the grouped sidebar's per-device header, and federation status tiles. A remote that is unreachable, too old to serve `/api/instance-info`, or returns a malformed body renders as "version unknown", deliberately formatted so it can never be mistaken for a real version — an unknown that looks like agreement is worse than no data. The remote probe runs concurrently with the existing `/api/sessions` poll, so it adds no latency, and it still works when that poll returns 401 because `/api/instance-info` is unauthenticated.

### Documentation

- **Recommend the PyPI install.** The README's `uvx` and `uv tool install` commands pointed at the git URL, predating publication to PyPI; both now use the published package. It also documents how to upgrade, and warns about the trap that prompted this: `uv tool upgrade` resolves strictly within the recorded requirement, so an install pinned to a tag (`...@v1.2.3`) reports "Nothing to upgrade" indefinitely — which is precisely how a local install sat on 0.14.0 while 0.15.0 was published. Unpinned git tracking remains documented for anyone wanting unreleased commits.

### Verification

- 1514 tests passed / 5 deselected in an isolated Digital Twin Universe container.
- 491 frontend tests via `node --test tests/*.mjs` (including the previously-undiscovered `test_terminal.mjs`).
- All five CI jobs green: frontend (node:test), Python 3.11/3.12/3.13, and test-latest-deps (mirrors user install behavior).
- Running vs installed version mismatch rendered end-to-end against real muxplex 0.14.0 server in container.

## v0.15.1 (2026-07-26)

### Bug Fixes

- **Browser and password-manager autofill triggering on the new-session name input.** The field already set `autocomplete="off"`, which is not sufficient for this field's shape: it is a bare, form-less text input whose placeholder reads "Session name", served from an origin that also hosts a real login form (`frontend/login.html`) — exactly the shape password managers heuristically classify as a username field, and one they ignore `autocomplete="off"` on by design. `_createSessionInput()` — the single factory feeding all three creation entry points (header `+`, sidebar `+ New`, and the mobile FAB overlay) — now also sends each vendor's documented per-field opt-out: `data-1p-ignore` (1Password), `data-lpignore` (LastPass), `data-bwignore` (Bitwarden), and `data-form-type="other"` (Dashlane). It additionally sets `autocorrect="off"` and `autocapitalize="off"` for the mobile PWA path, where iOS otherwise capitalizes and "corrects" tmux session names as they are typed. Wrapping the input in a `<form>` was considered and rejected: a form containing exactly one text input is the canonical username-first login step, so it would make the field a *larger* autofill target, and native submit-on-Enter would race the existing keydown handler and risk a full page reload in the installed PWA. A doc comment on the factory records why `autocomplete="off"` alone is insufficient, so the attributes are not later removed as redundant. Frontend-only; no API or Python changes. 436 frontend tests pass, including a new regression test pinning every suppression attribute.

## v0.15.0 (2026-07-26)

### Bug Fixes

- **Refuse to terminate a healthy muxplex when taking the port.** `_kill_stale_port_holder()` ran on every `muxplex serve` startup, called `lsof -ti :<port>`, and SIGTERMed whatever it found — unable to distinguish a stale holder from a healthy running server. Any second invocation of the startup path silently killed the live service, producing a clean graceful shutdown with no crash and no systemd `Stopping` line: nearly undiagnosable from logs. It now probes the holder via `GET /api/instance-info` first and kills only on positive evidence the holder is not serving; a healthy holder yields an actionable error naming the port and PID, and exit 1 instead of starting. `--force-take-port` restores the old unconditional behavior. A missing or erroring `lsof` still never blocks startup. The restart race is bounded: if an old instance is still draining, the new process exits non-zero and systemd retries after `RestartSec`.

- **Never accept() a WebSocket the client already abandoned.** `terminal_ws_proxy` performs real awaited work before accepting — killing and respawning ttyd, then waiting for it to bind. If the browser disconnected during that window, uvicorn's `connection_lost()` had already flipped its handshake-complete flag, so the subsequent `accept()` raised `RuntimeError: Expected ASGI message 'websocket.send' or 'websocket.close', but got 'websocket.accept'`, several times per hour in production. The wait is now raced against a disconnect watcher and `accept()` is skipped entirely when the client is already gone. Notably this only manifests on uvicorn's newer sansio WebSocket implementation, which is what a fresh `uv tool install` resolves — see Internal.

### Internal

- **Test-suite safety rails.** Running the suite on a host also serving muxplex destroyed real state twice in one day: a test overwrote a live `~/.config/muxplex/settings.json`, and six tests SIGTERMed the running server. Both were invisible from inside the suite, because a test that damages its host still passes. Four rails now close the class: a `pytest_sessionstart` guard that refuses to run when anything serves the default port, autouse isolation of `SETTINGS_PATH`, autouse neutering of `_kill_stale_port_holder` behind an explicit `@pytest.mark.allow_real_port_killer` opt-in, and `test_safety_rails.py` pinning all of it so removing a rail fails loudly. `make test` now runs the suite inside a Digital Twin Universe container, making the safe path the default path.

- **Dependency-drift CI job.** `uv.lock` pinned uvicorn 0.42.0 while a fresh `uv tool install` resolves 0.51.0 — and the WebSocket bug above existed only on the newer sansio implementation, so CI was green for weeks while production threw errors hourly. A new `test-latest-deps` job installs the way users install, ignoring the lockfile, so this class of drift fails CI instead of hiding.

## v0.14.0 (2026-07-25)

### Features

- **TLS bootstrap endpoint: GET /api/ca** — Clients that verify TLS against muxplex's locally-generated CA previously had no programmatic way to obtain it without SSH access, and the most natural approach (pointing ca_file at the server's own certificate) is reliably wrong: the server presents only the leaf certificate on the wire, producing "unable to get local issuer certificate" when a client tries to validate against it. GET /api/ca now returns the CA certificate PEM, unauthenticated (added to _AUTH_EXEMPT_PATHS alongside /api/instance-info) — a trust anchor is not a secret, and requiring auth would be circular since a client cannot authenticate over TLS it does not yet trust. The path is derived internally from settings.get_local_ca_cert_path(); no client-supplied path, query, or header reaches the filesystem. tls.get_local_ca_cert_bytes() fails closed: missing files, unreadable paths, unparseable content, missing BasicConstraints extension, or CA:FALSE all return None → 404 with a helpful detail. Never serves private key material. Addresses a recurrent TLS onboarding friction point.

## v0.13.0 (2026-07-25)

### Bug Fixes

- **Test-suite pollution of production settings** — `test_get_state_settings_updated_at_changes_after_settings_write` never redirected `SETTINGS_PATH` to a tmp directory, so running the test suite on a machine hosting a live muxplex instance read and wrote the real `~/.config/muxplex/settings.json`. On the dev box this repeatedly overwrote a production 8-view configuration with test fixture data — the exact `{"name": "Focus", "sessions": ["alpha"]}` payload from test_api.py that had to be recovered from backups. Test now monkeypatches SETTINGS_PATH into tmp_path like all sibling settings-writing tests. Also fixed three stale assertions in test_frontend_js.py that were matching literal `api('PATCH'` strings inside function bodies; v0.11.0 correctly refactored those call sites to route through `patchSettingsGuarded()`. Assertions now verify the guarded call; a new pinning test ensures end-to-end coverage of `/api/settings` is preserved rather than silenced. These three were the only real CI failures across 9 GitHub Actions job runs (3 commits × 3 Python versions).

- **Federation-aware stale-key pruning** — `prune_stale_keys()` built its live_keys set from local sessions only, while views entries are canonical `device_id:name` and routinely reference sessions on other devices. Every peer saw every other device's keys as dead and deleted them after grace period, then LWW-synced the deletion fleet-wide — a latent mutual eraser. Now the pruner takes optional `local_device_id` and `known_remote_device_ids`, and only prunes keys where the owning device's sessions are actually known: own-device keys are always evaluable, remote keys only when that device is currently reachable (checked via existing `_federation_cache`, gated on the same `fail_count` threshold used by `/api/federation/sessions`). Devices unreachable or unknown have their grace period clock reset (not paused) — a laptop offline for a week starts fresh when it returns instead of getting pruned on arrival. Legacy bare-name entries keep existing local evaluation. Also closed a gap: the poll-cycle prune bypassed v0.12.0's destructive-write backstop; it now runs `assess_views_destruction()` and refuses to persist if a mass prune would collapse views. Default-None params preserve legacy behavior exactly. 1480 tests passed; 435 frontend.

## v0.12.0 (2026-07-25)

### Bug Fixes

- **Catastrophic views destruction via stale clients and federation sync** — A PWA tab holding an hours-old snapshot of the settings blob could send 12 PATCHes in 7 minutes resubmitting a collapsed `views` array (8 views → 1), destroying your configuration; simultaneously an older federation peer could delete view members not stored locally, then LWW-broadcast the loss to every device. Root cause: `settings_updated_at` is a single scalar covering the whole syncable blob (views, sidebarOpen, fontSize, etc.), views is replaced wholesale (never merged) by both `patch_settings()` and `apply_synced_settings()`, and any PATCH re-stamps that timestamp — so a stale views value + unrelated fontSize write + federation race = cascade failure. Four defenses: (1) **Destructive-write backstop** — a single choke point (`assess_views_destruction()`) rejects any write that collapses >1 view to ≤1, removes ≥50% of views, or removes ≥50% of total members; returns 409 with `backstop:true` and makes NO write. Protects spark-1 fleet-wide even from unupgraded 0.6.7 peers and live stale browser tabs. Single-view and single-member edits stay far below thresholds. (2) **Federation sync now runs the backstop** and gains CAS discipline it previously lacked (`PUT /api/settings/sync` now uses the same optimistic concurrency as PATCH, never overwrites destructively). Deliberately NO force override on sync — only local config file edits can collapse a view now. (3) **Separate `views_updated_at`** — advances only when `views` or `hidden_sessions` actually change, so unrelated field writes can no longer re-arm a stale views value in a race. Absent on legacy peers (v0.6.7 compat); they fall back to prior behavior still gated by the backstop. (4) **PWA re-fetches settings before views mutations** instead of trusting cached blob; on 409 treats backstop and CAS distinctly — backstop 409 reloads from server (stale client), CAS 409 retries once with fresh state (lost race).

### Features

- **Settings snapshot history** — Every write to settings creates a time-stamped backup in `<config_dir>/settings-history/` (20 most recent kept). Enables instant forensics and recovery; monotonic sequence numbering for coarse-grained ordering independent of clock skew.


## v0.11.0 (2026-07-25)

### Bug Fixes

- **Settings clobber protection via rotating snapshots and optimistic concurrency** — a browser tab holding a stale copy of the settings PATCHed it back wholesale and destroyed 7 of 8 views in a single request — recovered only because a manual file backup happened to exist. Two defenses added: (1) Rotating snapshots in `<config_dir>/settings-history/` (20 most recent kept, monotonic sequence numbering for coarse-grained ordering, best-effort so a snapshot failure never blocks the real write). All writers (API PATCH, federation sync, internal code) covered at the lowest choke point. (2) Optimistic concurrency: `PATCH /api/settings` accepts optional `expected_settings_updated_at`; on mismatch returns 409 with the current value and makes no write. Omitting the field preserves existing behavior (federation sync and other clients keep working). PWA now routes all 14 settings-writing call sites through `patchSettingsGuarded()` which sends the precondition, re-fetches on 409, re-applies the mutation to fresh state, and retries exactly once; a second conflict re-renders from server truth. Successful writes update the client's baseline timestamp.

### Features

- **tmux socket directory discoverability** — muxplex manages tmux on a configurable socket dir (e.g. `~/.tmux`) while tmux itself defaults to `$TMUX_TMPDIR` or `/tmp` — so any tool creating a session without that variable set lands on a DIFFERENT tmux server and its sessions are invisible to muxplex. That knowledge was tribal and cost real debugging time. Now discoverable: (1) `settings.resolve_tmux_socket_dir()` resolves configured value, then `$TMUX_TMPDIR`, then `/tmp/tmux-<uid>`; (2) `GET /api/instance-info` returns the resolved `tmux_socket_dir`; (3) new `muxplex env` subcommand prints exactly `export TMUX_TMPDIR="..."` on stdout (human notes to stderr) for `eval "$(muxplex env)"`, following the ssh-agent/direnv convention; (4) README gains a "tmux socket" section explaining the two-server hazard and the one-line fix.

## v0.10.0 (2026-07-25)

### Bug Fixes

- **PWA now follows remote view-membership changes** — when a session is added to or removed from a view via `PUT /api/views/{name}` (on the server or another device), the PWA's 1s poll now detects the change and re-renders all view-related UI (dropdown, grid filters, sidebar, membership checkboxes). Root cause was that `_serverSettings` was fetched once at page load and cached forever, while the session list itself refreshed every second via `/api/sessions` — so "All" updated but "Focus" did not. Fix adds `settings_updated_at` to `GET /api/state` as a change signal (render-only, not persisted), then wires a new `followRemoteViewDefinitions()` into the 1s poll alongside the existing session and active_view followers. Absent field on older servers is treated as no-op. Completes the follow-the-remote-device family: `active_session`, `active_view`, and `view_definitions`.

### Features

- **Case-insensitive glob patterns for `input_allowed_sessions`** — the remote-agent input endpoint's session allowlist now matches case-insensitive. `"Amplifier-*"` matches `amplifier-foo`, `"amplifier-*"` matches `AMPLIFIER-Foo`, exact entries like `"Agent-Sbx"` match `agent-sbx`. Implemented by explicit `casefold()` on both sides rather than `fnmatch.fnmatch` (which would be case-insensitive on macOS/Windows as a side effect of `os.path.normcase`, silently widening the fence per-platform). All other fence properties unchanged: empty list denies all, pattern order preserved, LOCAL-FILE-ONLY still enforced, boundary validation intact.


## v0.9.0 (2026-07-24)

### Features

- **Glob pattern support in `input_allowed_sessions`** — the remote-agent input endpoint's session allowlist now accepts glob patterns instead of exact literals. `"*"` allows every session; `"amplifier-*"` matches a prefix family; bare names like `"agent-sbx"` work exactly as before (backward compatible). Matching uses `fnmatch.fnmatchcase` (case-sensitive, deterministic across all platforms) rather than `fnmatch.fnmatch`, which is case-insensitive on macOS/Windows and would silently widen the fence. Fail-closed guarantees preserved: empty list denies all, non-list values normalize to deny-all before matching, non-string entries are skipped, and the fence ORDER is unchanged (is_valid_session_name 400 → input_enabled 403 → allowlist 403 → fail-closed existence 404). Patterns remain LOCAL-FILE-ONLY — not settable via PATCH /api/settings or federation sync. 15 new tests; 1426 pass.

## v0.8.0 (2026-07-24)

### Features

- **Remote-agent terminal input endpoint** — `POST /api/sessions/{name}/input` allows remote agents
  (which cannot reach tmux directly) to send keystrokes to running sessions and receive a read-back
  snapshot. The endpoint is RCE-by-design and ships with eight hardened fences: (1) global
  `input_enabled` opt-in defaulting to False; (2) per-session `input_allowed_sessions` exact-match
  allowlist defaulting to empty; (3) both keys are LOCAL-FILE-ONLY (excluded from SYNCABLE_KEYS,
  rejected by PATCH /api/settings) so neither federation peers nor Bearer-key agents can
  self-authorize; (4) fail-closed target matching (empty allowlist rejects all) plus
  `is_valid_session_name` boundary validation; (5) keystrokes sent via `tmux send-keys -l`
  (literal text, never shell) through argv subprocess, with named keys restricted to an explicit
  allowlist; (6) payload caps: 8KiB text / 64 key events per request; (7) audit logging (one
  redacted line per action, full text at debug); (8) ~400ms settle + capture_pane read-back so
  agents aren't typing blind. Injection-safety proven end-to-end against real tmux with a hostile
  `; touch` payload — it appeared literally in the pane and never executed. 39 new tests; 1412
  pass. **Deployed default-off** — no behavior change until an operator sets `input_enabled: true`
  and populates `input_allowed_sessions` on the host machine.

## v0.7.1 (2026-07-24)

### Bug Fixes

- **PWA now follows remote `active_view` changes** — when another device (a Stream
  Deck sidecar, an agent, or another browser tab) changes the active view via
  `PATCH /api/state {active_view}`, the PWA's 1s state poll now applies it and
  re-renders, instead of only reflecting the view this tab itself last set. New
  `followRemoteActiveView()` mirrors the existing `followRemoteActiveSession()`
  follow path; the remote-apply is render-only (no re-`PATCH`), so it cannot echo.
  This completes view-switch parity with session-switch: both now propagate to
  every surface.

## v0.7.0 (2026-07-24)

### Features

- **Per-session `last_activity_at` in `GET /api/sessions`** (#11) — exposed for local and
  federation sessions, derived from tmux `#{window_activity}` (deliberately not
  `#{session_activity}`, which freezes for unattended sessions). The PWA's "Recent" sort
  now reflects real activity.
- **`GET /api/view` — server-side resolved view** (#13) — returns the current view with
  membership/hidden filtering, the canonical needs-attention predicate, and sorting
  applied (`?sort=attention` for bells-first / active / recency ordering). New clients
  (Stream Deck sidecar, agents) consume the resolved view instead of re-porting the
  PWA's rules.

### Bug Fixes

- **Session-name shell-injection RCE closed** (#14, security) — session names are
  validated against a strict allowlist (`is_valid_session_name`) at create, delete, and
  connect; `shlex.quote` added as defense-in-depth; matching is fail-closed exact-match.
- **PWA now follows remote `active_session` changes** (#15) — switches made from another
  device (Stream Deck, another browser, an agent) move the sidebar highlight and
  re-attach the terminal automatically — no more stale terminal stuck on
  "Reconnecting…" until you interact.
- **Remote-driven session switch ~8.8s → ~0.7s** (#16) — session-follow now runs on a
  dedicated ~1s `/api/state` poll (decoupled from the slow federation fetch), with a
  same-session connect short-circuit. The frontend is now served with
  `Cache-Control: no-cache` (ETag revalidation) so deploys reach installed PWAs without
  manual cache-clearing, and startup logs the served `app.js` md5.
- **Federation circuit breaker** (#17) — a dead/unreachable federation remote no longer
  drags every `/api/federation/sessions` call to the full timeout (~5s → ~0.02s steady
  state): 3 consecutive connection failures skip the remote for 60s, then re-probe;
  per-remote timeout reduced to 2s. Reachable-but-erroring remotes still report their
  honest status.
- **Clean, fast shutdown** (#18) — SIGTERM now completes in ~0.5s instead of hanging 10s
  and being SIGKILLed by systemd: shutdown cancels the poll loop and WebSocket relays
  first, then kills ttyd, then closes the HTTP client; the terminal WS relay no longer
  blocks on a live ttyd reader.

### Docs

- **`AGENTS.md`** (#12) — conventions for agents and contributors: `/api/*` is a public
  control surface with multiple consumers, additive evolution, API-first-frontend-second,
  and scratch-instance testing gotchas.

## v0.6.10 (2026-07-13)

### Bug Fixes

- **tmux_socket_dir not honored by create_session and delete_session** — When a custom
  `tmux_socket_dir` was configured (to support non-default socket directories via
  `TMUX_TMPDIR`), the muxplex service's session create and delete operations were still
  hitting the default tmux socket location because the subprocess environment was not
  being passed the overridden socket settings. Fixed by wiring the shared `tmux_env()`
  helper (already in use for session enumeration) into the create and delete code paths,
  ensuring all subprocess calls honor the configured socket directory.

## v0.6.9 (2026-07-11)

### Bug Fixes

- **Custom tmux socket directories invisible to the muxplex service** — If a user sets
  `TMUX_TMPDIR` in their shell rc (e.g. to keep sockets out of the shared `/tmp`), the
  muxplex *service* process (systemd/launchd, which does not inherit the login shell's
  environment) silently fell back to tmux's compiled-in default (`/tmp/tmux-$UID`) and
  saw none of the user's real sessions, even though `muxplex doctor` (run interactively)
  reported them correctly. Added a `tmux_socket_dir` setting (default empty, fully
  backward compatible) and a shared `tmux_env()` helper, wired into both session
  enumeration (`sessions.py`) and terminal attach (`ttyd.py`), that overrides
  `TMUX_TMPDIR` and strips `$TMUX` (which otherwise takes priority over `TMUX_TMPDIR`
  when a process is itself a descendant of an attached tmux client) for tmux subprocess
  calls.

## v0.6.8 (2026-07-10)

### Bug Fixes

- **OSC 52 clipboard bridge mangled multi-byte UTF-8 characters** — Copying text out of a
  tmux session via the OSC 52 clipboard bridge (`set-clipboard on`) mangled box-drawing
  lines, bullets, em dashes, and emoji (e.g. "─" became "â", "•" became "â¢"). The
  handler decoded the base64 payload with plain `atob()`, which returns a "binary string"
  (one JS char per raw byte, effectively Latin-1) rather than a UTF-8-decoded string. This
  path was never covered by the earlier `ced0c62` WebSocket-output decode fix — mouse-select
  copy was already correct; the OSC 52 bridge was not. Fixed by re-wrapping the decoded
  bytes and running them through the same `TextDecoder` used for the primary output path.
  Added a regression test and fixed a pre-existing test-mock gap
  (`terminal-container` missing `addEventListener`) that was silently failing ~26
  unrelated tests.

## v0.6.4 (2026-05-17)

### Bug Fixes

- **Empty device block still showing in grouped grid view** — Remote federation devices with
  zero tmux sessions were producing a visible "No sessions" block in the grouped grid view.
  The v0.6.3 fix targeted `renderGroupedGrid` but missed the unconditional `status:empty`
  status-tile append in `renderGrid` itself.  In grouped mode, `status:empty` tiles are now
  suppressed (`auth_failed` and `unreachable` tiles still appear in all modes).

- **`muxplex update` fails when uv/pip is installed outside PATH** — On Unraid (root user),
  macOS (user installs), and snap-packaged systems, `shutil.which("uv")` returned None even
  though uv was present at `~/.local/bin/uv`, `/snap/bin/uv`, or `/root/.local/bin/uv`.
  New helpers `_find_uv()` / `_find_pip()` probe a curated list of known install locations
  after PATH lookup fails, so the upgrade flow works on stripped-PATH environments
  (systemd, launchd, non-login SSH shells).

- **`muxplex update` exit code propagation** — Tests added to confirm that a failed install
  exits with code 1 after the `try/finally` service-recovery block runs (behaviour was
  implemented in v0.6.2; regression test coverage added here).

## v0.5.0 (2026-05-06)

### Features
- **`muxplex setup-tls --method ca`** — generate a persistent local Certificate Authority and sign a 13-month leaf TLS certificate with it. Install the CA once on each client device to get browser-trusted HTTPS for plain LAN names (`my-host`, `192.168.1.5`) without requiring Tailscale on every client and without buying a public domain. The CA persists across regenerations, so leaf rotation does **not** require re-trusting on clients. The leaf SAN auto-discovers the host's primary outbound LAN IPv4 address and the Tailscale MagicDNS name (when Tailscale is connected), in addition to the existing `<hostname>`, `<hostname>.local`, `localhost`, `127.0.0.1`, and `::1` entries. The CA cert has proper `BasicConstraints CA:TRUE pathlen:0` and `KeyUsage keyCertSign+cRLSign` extensions, so OS / browser trust stores accept it cleanly as a Root.
- **PWA install reliability** — the `ca` method specifically addresses the symptom where an installed PWA with a self-signed-cert origin gets kicked back into a regular browser tab on relaunch. With the CA installed in the OS trust store, the PWA shell stays in standalone mode across reopens.
- **New documentation** — [`docs/TRUSTING_THE_LOCAL_CA.md`](docs/TRUSTING_THE_LOCAL_CA.md) walks through CA install on Windows (PowerShell, no admin), macOS (`security` CLI), Linux (`update-ca-certificates` / `update-ca-trust`), iOS (Profile + Trust Settings), Android, and Firefox (separate trust store).

### API
- **`muxplex.tls.generate_local_ca(ca_cert_path, ca_key_path, days_valid=3650)`** — idempotent CA generator. Reuses the existing CA if both files exist; generates a new one otherwise. Returns metadata including a `regenerated` boolean.
- **`muxplex.tls.generate_leaf_signed_by_ca(ca_cert_path, ca_key_path, leaf_cert_path, leaf_key_path, hostnames, ip_addresses=None, days_valid=397)`** — generates a leaf TLS cert signed by an existing local CA. Builds proper `KeyUsage`, `ExtendedKeyUsage serverAuth`, `SubjectKeyIdentifier`, and `AuthorityKeyIdentifier` extensions, plus `SubjectAlternativeName` from the supplied DNS + IP lists.
- **`muxplex.tls._default_lan_ip()`** — returns the primary outbound IPv4 address (no actual packets sent; uses a connected UDP socket to ask the kernel which interface would route external traffic). Returns `None` on failure.
- **`muxplex.tls._default_tailnet_name()`** — returns the host's MagicDNS name from `tailscale status --self --json`, or `None` if Tailscale is unavailable / disconnected. Best-effort with a 5-second timeout.

## v0.3.5 (2026-04-14)

### Bug Fixes
- **Connection pool exhaustion fix** — replaced `setInterval` with self-scheduling `setTimeout` for both `pollSessions` and `sendHeartbeat` loops; prevents `ERR_INSUFFICIENT_RESOURCES` death spiral when federation requests time out during 2-second poll cycles

## v0.3.4 (2026-04-13)

### Bug Fixes
- **Zero-session devices visible** — devices with no tmux sessions now show a "No sessions" status tile instead of being invisible
- **Flapping prevention** — server-side cache of last-known-good federation results per remote; returns cached sessions for up to 3 consecutive failures before marking unreachable
- **Status tiles show device name** — offline/unreachable tiles display the device name instead of blank (was passing session.name which is undefined for status entries)
- **Status entries filtered from session list** — unreachable/auth_failed entries no longer render as blank session tiles in dashboard or sidebar
- **remoteId=0 falsy bug in mobile sheet** — first remote instance (index 0) now works correctly in the mobile bottom sheet session switcher

## v0.3.3 (2026-04-13)

### Bug Fixes
- **iOS/iPadOS touch scrolling** — fix touch scroll handling for Safari on iOS and iPadOS devices (PR #4, @samueljklee)

## v0.3.2 (2026-04-09)

### Bug Fixes
- **Hidden sessions filter now applies to federated sessions** -- hiding a session now hides it everywhere (local and remote), completing the federation-aware hidden sessions feature

## v0.3.1 (2026-04-08)

### Bug Fixes
- **Federation auth stale key** -- the auth middleware now reads the federation key fresh from disk on each request instead of caching it at startup; key generation and rotation no longer require a server restart
- **Settings sync silent push failures** -- the PUT response from `/api/settings/sync` is now checked; 409 (remote newer) is handled gracefully, other errors are logged

## v0.3.0 (2026-04-08)

### Features
- **Federation settings sync** -- user preferences (font size, sort order, hidden sessions, etc.) now sync across all connected muxplex servers using a P2P last-write-wins protocol with per-server timestamps; offline servers catch up automatically on reconnect
- **Heartbeat-driven bell clearing across federation** -- viewing a remote session now clears its activity bell on the remote server automatically; no more stale activity indicators for federated sessions

### Bug Fixes
- **`remoteId: 0` falsy bug** -- sessions from the first remote instance were incorrectly subject to the hidden-sessions filter due to a JavaScript falsy-0 check; fixed `!s.remoteId` to `s.remoteId == null`
- **Browser indicators ignore hidden sessions** -- tab title `(N)` count and favicon activity badge now filter through `getVisibleSessions()` so hidden sessions don't contribute to activity counts

### API
- **`GET /api/settings/sync`** -- returns syncable settings + timestamp for federation sync (Bearer token auth)
- **`PUT /api/settings/sync`** -- accepts synced settings; applies if incoming timestamp is newer (200), rejects if older (409 with local state)

## v0.2.0 (2026-04-08)

### Features
- **Server-side settings consolidation** -- all display preferences (font size, grid columns, hover delay, view mode, device badges, hover preview, activity indicator, grid view mode, sidebar state) moved from browser localStorage to server-side `settings.json`; settings now survive browser clears and are consistent per-server
- **Federation session deletion** -- kill sessions on remote devices from any muxplex client
- **Session creation error reporting** -- replaced fire-and-forget subprocess with async process that checks exit codes, surfaces stderr, and pre-flight checks the command binary on PATH
- **TTY-attach resilience** -- session commands that exit non-zero but still create the tmux session (e.g. `amplifier-workspace` which tries to attach after create) are detected and treated as success

### Bug Fixes
- **Federation key preservation on URL edit** -- editing a remote instance URL (e.g. `http://` to `https://`) no longer erases the federation key; added position-based fallback alongside the existing URL-based key restoration
- **PWA manifest auth bypass** -- added `.json` to the static extension allowlist so `/manifest.json` is not auth-gated; previously produced "Syntax error" in the browser console
- **`auto_open` toggle** -- fixed three-way key mismatch (`auto_open` vs `auto_open_created`) that made the auto-open setting completely non-functional
- **Session enumeration crash** -- `enumerate_sessions()` now catches `FileNotFoundError` when the session command binary is missing from PATH, preventing poll loop crashes
- **Settings PATCH key leak** -- the `PATCH /api/settings` response now redacts sensitive keys, matching the existing `GET /api/settings` behavior
- **Federation 503 diagnostics** -- all federation proxy 503 errors now include the exception type and message instead of just the remote URL
- **FastAPI version string** -- corrected the hardcoded `version` in the FastAPI app from `0.1.0` to match the release

## v0.1.1 (2026-04-07)

### Features
- **TLS/HTTPS support** — `muxplex setup-tls` auto-detects Tailscale → mkcert → self-signed certificates
- **TLS nudge** in `doctor` and `service install` when clipboard requires HTTPS
- **Session device selector** — create sessions on remote devices when multi-device enabled
- **Activity count in page title** — browser tab shows `(2) hostname - muxplex` for unseen bells
- **Favicon activity badge** — amber dot overlay on favicon for unseen notifications
- **Terminal search** — Ctrl+F to search scrollback (xterm-addon-search)
- **Clickable URLs** — Ctrl+Click / Cmd+Click opens URLs in terminal output (xterm-addon-web-links)
- **Inline image rendering** — Sixel and iTerm2 graphic protocols (xterm-addon-image)

### Bug Fixes
- **Federation SSL** — federation client accepts self-signed TLS certificates on remote instances
- **Federation empty key** — skip Authorization header when federation key is empty
- **Federation WebSocket SSL** — WebSocket proxy accepts self-signed certs on wss:// remotes
- **Remote session connect** — terminal reconnect uses federation connect path for remote sessions
- **Remote session restore** — persist `active_remote_id` in state for page refresh restore
- **Bell clearing for remote sessions** — federation bell-clear endpoint + unique sessionKey
- **Service crash-loop prevention** — kill stale port holders on startup, TimeoutStopSec in systemd
- **UTF-8 terminal display** — decode WebSocket output with TextDecoder before xterm.js write
- **Clean clipboard handling** — removed custom paste handlers per COE review, native xterm.js paste
- **Guard empty session name** — openSession bails on empty name from unreachable federation tiles
- **Clean Ctrl+C exit** — `muxplex service logs` exits cleanly on keyboard interrupt

### Infrastructure
- **PyPI publish** — available as `pip install muxplex`
- **GitHub Actions CI** — tests run on push/PR (Python 3.11-3.13)
- **Self-hosted vendor libs** — eliminates Edge Tracking Prevention console noise

## v0.1.0 (2026-04-04)

Initial release.

# muxplex API semantics external clients re-implement

**Audience:** anyone writing or maintaining a client that talks to muxplex's HTTP
API — the bundled PWA, the
[muxplex-deck](https://github.com/bkrabach/muxplex-deck) Stream Deck sidecar,
federation peers, AI agents — and anyone changing the server-side fields those
clients depend on.

This file records the *semantics* behind the wire contract: the predicates,
timestamps, precondition rules, and write-path guards that clients currently
re-derive on their own, plus the incidents that produced each one. Silently
changing any of them breaks consumers in ways this repo's tests cannot see.

Related docs, and why they aren't this one:

| Doc | What it is |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | Conventions for **developing muxplex itself** — invariants a contributor must not break. |
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) | How to **drive** a running muxplex from outside — auth, endpoints, worked examples. Read that first if you are writing a client. |
| **this file** | **Why** those endpoints behave the way they do, and what must not change silently. |
| [`../README.md`](../README.md) | Human install/config reference. |
| `/openapi.json`, `/docs` | The machine-readable contract, served by the running instance. Authoritative for exact request/response shapes. |

Every claim below is checkable against the source; file references are given so
you can verify rather than trust.

---

## Follow-up queues: `/api/sessions/{name}/followups` (additive, local-only)

Five endpoints (`GET`/`POST`/`PUT`/`DELETE`/`POST .../resume`) implementing a
per-session, server-side, persisted queue of text items that fire one at a
time, each on that session's next bell. See `FOLLOWUP_QUEUE_SPEC.md` for the
full design and `../AGENTS.md`'s "Follow-up queue" section for the
invariants a contributor must not break.

- **`GET`** returns `{session, revision, items, halted, target_window}`.
  An unknown-but-valid session with no queue returns `revision: 0, items:
  [], halted: null` — an empty queue and an absent queue are the same thing
  to a client. `target_window` is `"<index>:<name>"` resolved live from the
  session's CURRENT window (display-only: `tmux send-keys` types into
  whatever window is current at fire time, not necessarily the window that
  belled — this is surfaced honestly rather than the queue trying to be
  clever about targeting).
- **`POST`** appends one item, no precondition (appending is commutative).
  New members of the discriminator convention (see `backstop` /
  `terminal_conflict` / `unknown_command_id` above): `bell_hook_unarmed`
  (409 — refuses new items while the bell hook isn't confirmed delivering,
  since a queue armed against a dead trigger is worse than no queue) and
  `queue_full` (409 at `MAX_FOLLOWUPS`, 16).
- **`PUT`** replaces the whole item list — edit + reorder + remove in one
  call. `expected_revision` is a **REQUIRED** precondition (unlike `PATCH
  /api/settings`' optional `expected_settings_updated_at`): the queue
  mutates itself, so a stale `PUT` built from a pre-bell snapshot could
  re-add an item that has already been typed into the session — a second
  execution, not a lost update. Also 409s with `send_in_flight` while a
  send for that session is in progress (the window between peek and
  remove-by-id in the advance sequence below).
- **`DELETE`** clears items AND any halt. **`POST .../resume`** clears the
  halt only, keeping every item — nothing else clears a halt implicitly
  (an implicit unhalt is how an autonomous writer would restart without
  anyone deciding it should).
- **`GET /api/sessions`, `GET /api/view`, and `GET /api/federation/sessions`**
  each gain a `followups: {pending: int, halted: bool}` summary field
  (`{"pending": 0, "halted": false}` when there is no queue) — a summary,
  not the items, so PWA/sidecar/agent clients render a badge without a
  second per-session fetch. Version-tolerant in both directions: a
  pre-feature peer's federation entry simply lacks the key.
- **The advance sequence (peek → fence-check-and-send → remove-by-id) is
  a THIRD caller of `terminal_input.input_allowed_for_session()`** — no
  bypass, re-evaluated at fire time against fresh settings, regardless of
  what was true when the item was enqueued. A failed send halts the queue
  (retaining the item) rather than skipping it — see `../AGENTS.md`.
- **No federation proxy.** `/api/federation/{device_id}/sessions/{name}/followups`
  does not exist and must not be added without a version-negotiation
  mechanism this codebase doesn't have yet (arming an autonomous writer on
  a peer whose patch level can't be verified is out of scope for v1).

## Semantics external clients re-implement today (change with care)

These rules are currently ported into clients; silently changing them breaks
consumers in ways this repo's tests won't catch:

- **Needs-attention (bell) predicate**:
  `unseen_count > 0 and (seen_at is None or last_fired_at > seen_at)`
- **View membership entries** are normalized to `"device_id:name"` form by the
  background normalization pass; clients match by the `":<name>"` suffix
  (tmux forbids `:` in session names).
- **`last_activity_at`** derives from tmux `#{window_activity}` — deliberately
  NOT `#{session_activity}`, which freezes for unattended sessions (rationale
  and empirical evidence documented in `sessions.py`).
- **`active_view` / `active_session` are server-global BY DEFAULT** — last
  writer wins, across every connected client (browsers, deck, agents),
  *unless* a device has opted into its own private **sync group** (see "Sync
  groups" below). What that means for a client author that sends no
  `device_id` (and why an agent should usually leave both alone) is in
  `AGENT_GUIDE.md` §4 — that guidance is still exactly true for such a
  client, because omitting `device_id` resolves to the shared `"global"`
  group, identical to today.
- **The read model is eventually consistent**: GET endpoints serve a ~2s poll
  cache. POST create/delete aren't visible until the next cycle, and `connect`
  on a just-created session 404s until the cache catches up — that 404 is the
  cache, not a failed create. The measured timings and the reference polling
  pattern (short interval, generous ceiling — **not** a flat `sleep`) live in
  `AGENT_GUIDE.md` §4, "The read model is eventually consistent". They are
  deliberately kept in exactly one place; don't restate the numbers here.
  (Candidate future fix: write-through cache refresh on create/delete.)
- **`GET /api/state` carries `settings_updated_at: float`**, merged in at
  request time from `settings.settings_updated_at` (settings.py) — it is
  NOT persisted in state.json; `empty_state()`/`load_state()`/`save_state()`
  are unaware of it (see `state.py`'s module docstring for the split).
  Purpose: any client already polling `/api/state` (PWA, muxplex-deck,
  agents) can detect a settings change — including view-membership edits
  made by another device, which are otherwise only visible via a dedicated
  `GET /api/settings` fetch — without adding a second poll. The PWA's
  `followRemoteViewDefinitions()` (`frontend/app.js`) is the reference
  consumer: it compares this timestamp against the last-seen value and only
  re-fetches `/api/settings` (via the existing `loadServerSettings()`) and
  re-renders view-dependent UI when it actually changed — an unchanged
  value is a no-op, so this does not become a second per-second settings
  fetch. This closed a real staleness bug: a session added to a view via
  `PATCH /api/settings` appeared immediately on the deck sidecar's own poll,
  but the PWA's `_serverSettings` cache — populated once at page load —
  never refreshed, so the view dropdown / filtered list / manage-view
  membership UI all stayed wrong until a hard reload. Same class of bug as
  `active_view` being server-global above, one layer deeper: that fix
  follows the active *selection*; this one follows the view *definitions*
  (membership data) themselves.

- **`deviceLabelPlacement` is the authoritative placement key; `showDeviceBadges` is
  a server-derived mirror** (`showDeviceBadges == deviceLabelPlacement != "off"`),
  reconciled on every write path (`patch_settings`, `apply_synced_settings`, and a
  read-time migration in `load_settings`). Clients should read and write
  `deviceLabelPlacement` and treat `showDeviceBadges` as read-only.
  A client that writes only `showDeviceBadges` (a pre-v0.36 client, or an old
  federation peer) still works: `false` sets the placement to `"off"`; `true` moves
  it off `"off"` **only when it is currently `"off"`**, so an old peer's sync can
  never silently drag a user from `"corner"` to `"titlebar"`. An unknown
  `deviceLabelPlacement` on `PATCH /api/settings` is a **400** carrying
  `unknown_device_label_placement: true` plus an `allowed` list, with no write —
  the fourth member of the `backstop` / `terminal_conflict` / `unknown_command_id`
  discriminator convention below. On the **federation sync path** the same value
  is ignored with a warning and every other key in the payload still applies; a
  peer must not be able to wedge sync. **This key is deliberately PWA-scoped and
  is NOT a semantic other clients are expected to re-implement.** At the time of
  writing neither the soft deck (`frontend/deck/`) nor the `muxplex-deck` sidecar
  renders a device label on a session tile, and neither fetches federated
  sessions — so there is nothing for the key to govern there. A sidecar that
  later grows federated tiles should read this key at that point; until then,
  ignoring it is correct, not a gap.

Preferred direction as semantics grow: move resolution **server-side** (e.g. a
resolved-current-view endpoint) rather than expecting each client to port more
logic — duplication across PWA/sidecar/agents is where drift bugs come from.

- **Session dicts carry `views`: the resolved list of user-view names that
  session belongs to** (pins in `view.sessions` UNION glob-rule matches from
  `view.match_names` -- see "Auto-updating views" below). Present on every
  entry from `GET /api/sessions` and `GET /api/federation/sessions`.
  Clients MUST read this field rather than re-deriving membership from raw
  `settings.views` -- the incident this closes: the PWA grid, its view
  dropdown counts, the Manage View panel, and the soft deck's own picker
  counts all used to re-derive membership client-side from
  `settings.views[].sessions`, so a rule-based view (which never populates
  `sessions`) rendered correctly on exactly one surface (the soft deck's
  session list via `GET /api/view`, which already called
  `views.filter_visible` server-side) and empty everywhere else. `GET
  /api/view`'s own `sessions[]` entries do NOT carry `views` -- that
  payload is already the resolved membership; the annotation exists for
  the payloads that are not.
- **Auto-updating views**: a view entry may carry an optional
  `match_names: [str]` -- fnmatch-style glob patterns matched via explicit
  `.casefold()` + `fnmatch.fnmatchcase` (same technique as, but a
  deliberately SEPARATE implementation from,
  `terminal_input.session_matches_allowlist` -- see `../AGENTS.md`)
  against a session's BARE tmux name. Never the device-qualified
  `"<device_id>:<name>"` key: the qualifier is `identity.load_device_id()`,
  a UUID nobody would type, and the payload most clients poll
  (`GET /api/sessions`) doesn't carry it in single-device mode anyway.
  Membership is a strict UNION of `sessions` (pins) and `match_names`
  matches, resolved fresh on every read in `views.filter_visible` /
  `views.view_names_for_session` -- **the server never materializes a
  rule match back into `view["sessions"]`**; rules stay rules on disk
  forever, which is the whole reason a rule-based view cannot decay.
  `GET /api/views` is the canonical resolution + validation-errors
  endpoint for view rules (mirrors `GET /api/session-commands`'s
  established pattern): `match_names` on each view contains only the
  patterns that will actually be used, with rejected patterns named in
  `errors`. `invalid_view_rule: true` is a new member of the
  discriminator convention below, returned by `PATCH /api/settings` (400,
  no write) when a patch's `match_names` is structurally malformed (a
  non-list, a non-string entry, an empty string, or a pattern containing
  `:` -- tmux forbids `:` in session names, so such a pattern could never
  match anything). A malformed rule arriving via federation sync or a
  direct file edit is stored as-is (never rejected -- one bad peer must
  not break fleet-wide sync) and surfaced only at read time via the same
  `GET /api/views` `errors[]`.
- **`POST /api/views/preview`** is the rule editor's live-match preview
  (the Manage View panel's `match_names` textarea, AUTO_VIEWS_SPEC.md §9.3):
  given a body `{"match_names": [str, ...]}` -- a DRAFT list, never
  persisted -- it returns `{"errors": [...], "matches": [<session name>,
  ...]}` by wrapping the draft in a throwaway view dict and running it
  through the SAME `validate_view_rules` / `filter_visible` every saved
  view uses, rather than a second matcher. This is why the frontend can
  show "these N sessions match" and name a rejected pattern's exact reason
  as the user types, with `grep -rn "fnmatch" frontend/` staying empty --
  the client asks the server instead of porting the matcher. Local sessions
  only (same scope note as `GET /api/view`); never writes anything, so it
  is safe to call on every keystroke (debounced client-side). Requires
  auth, same as `GET /api/views` -- which local sessions match a draft
  pattern is not for an unauthenticated caller.
- **`GET /api/view`** is now the canonical server-side resolution of the
  above: view membership (via `filter_visible`), the needs-attention
  predicate (`bells.needs_attention`), and sort ordering (`?sort=attention`
  for tiered bell/active/recency ordering, or the default that mirrors
  `settings.sort_order`). New clients should prefer it over re-deriving
  these rules; local sessions only in v1.
- **`?sort=attention`'s non-bell tier orders by `bell.last_fired_at`, NOT
  `last_activity_at`** (`main.py`'s `_attention_order()`; mirrored in
  `frontend/app.js`'s `sortByAttention()` and re-implemented in
  muxplex-deck's `attention.py` -- all three must move together). Sessions
  that have never belled (`last_fired_at is None`) sort last within this
  tier, preserving incoming order among themselves (stable sort). This
  closed a real bug: `last_activity_at` derives from tmux
  `#{window_activity}`, which bumps on ANY pane output -- spinners,
  redraws, status-line clocks -- not just the agent-turn-completion event
  `attention` sort exists to surface. Keying tier 3 off it meant the grid
  reordered on essentially every ~2s poll cycle even when nothing the user
  cared about had happened. `bell.last_fired_at` only changes when a real
  bell fires, so ordering is now stable between bells -- the whole point of
  an "attention" sort. **`last_activity_at` itself is unchanged** and still
  the sort key for the `recent` sort mode; this fix touches ONLY the
  `attention` mode's tier-3 key.
- **`PATCH /api/settings` accepts an OPTIONAL `expected_settings_updated_at`
  precondition** (compare-and-swap). When present, it must equal the
  server's current `settings_updated_at` or the request is rejected with
  409 (body includes the current `settings_updated_at`) and NO write is
  made; when omitted, behavior is unchanged (existing clients, including
  federation sync, keep working without it). This closes a real incident:
  a PWA tab holding a STALE `_serverSettings.views` snapshot PATCHed the
  entire array back and destroyed 7 of 8 views in one request. The PWA's
  `patchSettingsGuarded()` (`frontend/app.js`) is the reference consumer —
  it attaches the precondition, and on a single 409 re-fetches settings,
  re-applies the same mutation to the fresh copy, and retries exactly
  once (a second consecutive 409 re-renders from server truth instead of
  looping). New clients that write `views`/`hidden_sessions` SHOULD send
  this field; see `main.py`'s `update_settings()` for the exact-equality
  rationale (no epsilon — the value round-trips through JSON unmodified).
- **Every settings write is snapshotted first**, regardless of writer (API
  PATCH, federation sync, internal code): `settings.save_settings()` copies
  whatever is CURRENTLY on disk to
  `~/.config/muxplex/settings-history/settings-<unix_ts>.json` (mode 0700
  dir) before overwriting, keeping the most recent
  `settings.SETTINGS_HISTORY_KEEP` (20) snapshots. This is the automatic
  recovery path the incident above needed — previously recovery only
  worked because a manual file backup happened to exist. Best-effort: a
  snapshot failure is logged and swallowed, never blocks or corrupts the
  real write.
- **Destructive-write backstop on `views`** (`views.assess_views_destruction`,
  called from BOTH `settings.patch_settings()` and
  `settings.apply_synced_settings()` — the single lowest choke point each
  write path shares before `views` is replaced wholesale). The CAS
  precondition above stops a *stale* write from being accepted, but does
  nothing if the writer's timestamp genuinely looks newer — which is
  exactly what happened next: a phone PWA tab resubmitted an
  already-collapsed 1-view array 12 times over 7 minutes, and separately,
  federation LWW replicated a collapsed state fleet-wide because `views`
  shared `settings_updated_at` with unrelated fields (see `views_updated_at`
  below). The backstop is a second, independent line of defense: it
  inspects what's about to be written and refuses catastrophic shrinkage
  regardless of whether the CAS/LWW timestamp said the write should
  proceed. Catastrophic (thresholds are named module constants in
  `views.py`, not magic numbers): more than
  `DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD` (1) views collapsing to that
  threshold or below; incoming view count <= (1 -
  `DESTRUCTIVE_VIEW_DROP_RATIO` (0.5)) of current; or incoming total
  session-member count (summed across all views) <= (1 -
  `DESTRUCTIVE_MEMBER_DROP_RATIO` (0.5)) of current. A single view
  deletion, or trimming a handful of members from one view, stays under all
  three and is never flagged. Rejection makes NO write (not even to
  unrelated keys in the same request) and returns 409 with `{"backstop":
  true, "detail": <reason>, "settings_updated_at": <current>, "counts":
  {...}}` — `backstop: true` is how a client tells this apart from an
  ordinary CAS 409 (see `frontend/app.js`'s `patchSettingsGuarded` below).
  `PATCH /api/settings` accepts an `allow_destructive: true` body field to
  perform an intentional bulk deletion; **federation sync NEVER gets this
  override** — a peer must not be able to force a destructive change onto
  another device, only a local operator editing `settings.json` directly
  can. Guard is robust to `views` being absent/None/non-list on either side
  (never crashes; never treats "no incoming `views` key" as "delete all").
- **`views_updated_at`** (metadata alongside `settings_updated_at`; both are
  threaded through the `/api/settings/sync` GET/PUT payload but neither is
  itself in `SYNCABLE_KEYS`): a SEPARATE timestamp that advances ONLY when a
  PATCH/sync actually touches `views` or `hidden_sessions`, decoupled from
  `settings_updated_at`, which advances on ANY syncable key (`fontSize`,
  `sidebarOpen`, etc.). This closes the race that let a fleet-wide views
  collapse replicate: because everything shared one timestamp, an unrelated
  field edit on a peer could bump its `settings_updated_at` past ours and
  win a federation LWW race for `views` too, even though that peer's actual
  view data was stale or already-destroyed. `apply_synced_settings()` now
  takes an optional third argument `incoming_views_updated_at` and, when a
  peer supplies it, applies incoming `views`/`hidden_sessions` ONLY if it's
  strictly newer than our local `views_updated_at` — every OTHER present
  key still applies normally from the overall (newer) sync, so this is
  never all-or-nothing. **Backward compatible**:
  `incoming_views_updated_at=None` (the default, and what any
  pre-this-field peer's payload implies) falls back to the pre-existing
  behavior — apply views/hidden_sessions unconditionally, gated only by the
  backstop above — so older peers keep interoperating with zero changes on
  their end.
- **`PUT /api/settings/sync`'s existing `payload.settings_updated_at >
  local_ts` comparison IS this endpoint's CAS/precondition discipline** — a
  peer only gets to write when its view of the world is strictly newer than
  ours, the sync-path analogue of `PATCH`'s
  `expected_settings_updated_at`. What changed: `apply_synced_settings()`
  now runs the destructive-write backstop unconditionally as its first act
  for EVERY caller — this endpoint AND the periodic background
  `_sync_settings_with_remotes()` poll loop — so a catastrophic incoming
  `views`, however the timestamp comparison came out, is rejected with 409
  (`{"backstop": true, ...}`) and no write happens, with no override
  available on this path.
- **`GET /api/instance-info` includes `tmux_socket_dir`** — the resolved
  (not raw) socket directory this instance's tmux sessions live under (see
  `settings.resolve_tmux_socket_dir()`). Lets remote tools/agents discover
  where sessions need to land to be visible to this instance without
  tribal knowledge; see the "tmux socket" section below and README.md.
- **`GET /api/ca`** serves the local CA's PUBLIC certificate PEM (200,
  `Content-Type: application/x-pem-file`, `Content-Disposition: attachment`)
  when `muxplex setup-tls --method ca` is in use; 404 otherwise (no local CA
  configured, the file is missing, or the file at the CA path is not
  actually a CA cert — `BasicConstraints CA:TRUE` is checked via
  `tls.get_local_ca_cert_bytes()` before serving, so a leaf accidentally
  left at the CA path is refused rather than handed out). **Unauthenticated**
  — added to `auth._AUTH_EXEMPT_PATHS` alongside `/api/instance-info`: a CA
  public certificate is not a secret (no private key material; it's the
  trust anchor clients are meant to install), and requiring auth would be
  circular (a client can't authenticate over TLS it doesn't yet trust).
  Reads ONLY the single fixed path `settings.get_local_ca_cert_path()`
  resolves to (`<config_dir>/ca/muxplex-ca.crt`, mirroring cli.py's
  `setup_tls()`) — the handler takes no request parameters at all, so no
  path/query/body/header can redirect the read to an arbitrary file. Exists
  to close a real onboarding gap: the only prior way to get this file was
  `scp` from the server (needs SSH access a client may not have), and users
  reliably grabbed `muxplex.crt` (the LEAF the server presents on the wire)
  instead, producing "unable to get local issuer certificate" downstream —
  the exact mistake that cost real debugging time in the muxplex-deck
  onboarding flow. See README.md's "Fetching the CA over the network"
  subsection for the client one-liner.
- **`PATCH /api/settings` silently ignores any key in `settings.LOCAL_ONLY_KEYS`**
  (`settings.patch_settings()`), regardless of which other keys are present in
  the same request — the fenced key is skipped (with a `logger.warning`) and
  every other key in the patch still applies. This is not limited to the two
  terminal-input fence keys (`input_enabled`, `input_allowed_sessions`,
  documented in `../AGENTS.md`'s "Terminal input" section): it also covers
  every settings key that names a **command or a filesystem path the server
  itself later executes or reads** — `new_session_template` /
  `delete_session_template` (arbitrary shell commands run via
  `create_subprocess_shell` in `sessions.py`), `tmux_socket_dir` (fed into
  every tmux invocation as `TMUX_TMPDIR`, see `resolve_tmux_socket_dir()`
  above), and `tls_cert` / `tls_key` (paths the server later reads and
  parses in `cli.py`). **Incident (confirmed by audit):** the two session
  templates were NOT originally in `LOCAL_ONLY_KEYS`, so a client holding
  only the federation Bearer key — no PAM login, no interactive session —
  could `PATCH` `new_session_template` to an arbitrary shell command and
  then `POST /api/sessions` to trigger it: full remote code execution that
  never touches the fenced `POST /api/sessions/{name}/input` endpoint at
  all, because the Bearer key is the SAME credential satisfying auth on
  every other `/api/*` route. The fix widened `LOCAL_ONLY_KEYS` to the five
  keys above; all five are also deliberately absent from `SYNCABLE_KEYS`
  (federation sync must never widen a local-only fence). The legitimate
  operator path — editing `~/.config/muxplex/settings.json` directly — is
  unaffected: `load_settings()` applies no `LOCAL_ONLY_KEYS` filtering, so a
  local file edit still takes effect. Clients that write to any of these
  five keys via `PATCH` should expect the value to come back unchanged in
  the response and treat that as confirmation the fence held, not a bug.
  **`session_commands` is a sixth key in the same fence** (named session
  command pairs, below) -- extend the count wherever a client or test
  enumerates it.
- **Named session command pairs** (`session_commands`, `GET
  /api/session-commands`, `POST /api/sessions`'s `command_id`, `DELETE
  /api/sessions/{name}`'s automatic pair-matching and `?force=true`).
  There is one implicit pair today -- `new_session_template` /
  `delete_session_template`. This lets an operator configure several NAMED
  pairs (different working paths, or an entirely different command) and
  select one at create time; delete automatically runs the matching
  teardown, so a client never has to remember what created a session.
  - **`GET /api/session-commands`** is the canonical SERVER-SIDE resolution
    of the configured pairs: the legacy singular pair folded in as the
    reserved `id: "default"` entry (always `commands[0]`), invalid
    `session_commands` entries excluded, human-readable `errors` reported.
    Clients MUST NOT re-derive this fold from `GET /api/settings`'s raw
    `session_commands` field -- same rationale as `GET /api/view` above:
    resolve once, server-side, rather than have every client (PWA,
    muxplex-deck, agents) reimplement the fold and drift.
  - **`POST /api/sessions`'s optional `command_id`** selects a pair by id.
    Omitting it is BYTE-IDENTICAL to pre-feature behavior -- it resolves to
    `"default"`, i.e. today's `new_session_template`. An unresolvable id is
    a 400 (`unknown_command_id: true`, an `available` id list), before any
    subprocess runs. The response gains one additive field, `command_id`,
    naming the pair that actually ran.
  - **`DELETE /api/sessions/{name}`'s pair-matching is automatic and has NO
    `command_id` input** -- the server looks up which pair created the
    session (recorded in the device-local session-presence manifest at
    create time, never in `state.json`, whose session map is reaped every
    ~2s against live tmux) and runs its `delete_session_template`. No
    record (a pre-existing session, or one created outside muxplex) uses
    the default pair -- unchanged pre-feature behavior. A record naming a
    pair that no longer resolves (deleted or renamed in settings) is a
    **409** with **no command run** -- deliberate: substituting the
    default teardown for an unknown pair could leave the real cleanup
    undone (a container still running, a worktree still mounted).
    `?force=true` performs that substitution explicitly, returning
    `forced: true` and logging a warning naming the missing id. The
    response gains `command_id` (+ `forced` on the force path).
  - **`unknown_command_id: true`** is the third member of the established
    "tell this 409/400 apart from others" convention alongside
    `backstop: true` (settings destructive-write guard) and
    `terminal_conflict: true` (connect terminal-claim gate).
  - **`session_commands` is in `LOCAL_ONLY_KEYS` and absent from
    `SYNCABLE_KEYS`** -- same fence, same reasoning as the singular
    templates: arbitrary server-executed shell commands, so the API may
    list and select a pair but can never define one. Managing pairs means
    editing `~/.config/muxplex/settings.json` directly; the PWA's Settings
    > Commands tab reflects this honestly (read-only rows, no
    `patchServerSetting('session_commands', ...)` call anywhere).
  - **Federation (v1 decision, deliberately narrow):** `command_id` is
    forwarded on `POST /api/federation/{device_id}/sessions` when supplied
    (omitted, not `null`, when absent -- byte-identical proxied request for
    the common case), but the id namespace belongs to the REMOTE -- a
    local id may mean nothing (or something different) there, where it
    correctly 400s, surfaced as a 502 by the proxy.
    `GET /api/federation/{device_id}/session-commands` is **deliberately
    NOT added** -- no consumer, and a cross-device picker's failure modes
    (peer unreachable, peer pre-feature, id drift between hosts) are real
    design work with zero demand today. Command pairs are per-host
    configuration and are NOT synced across a federated fleet -- the
    non-negotiable consequence of the `LOCAL_ONLY_KEYS` fence above.
- **Stale-key pruning (`views.prune_stale_keys`) is federation-aware and
  prunes ONLY on positive knowledge, never on ignorance.** A settings key
  `"<device_id>:<name>"` in `views`/`hidden_sessions` may be evaluated for
  removal ONLY when the owning device's session list is CURRENTLY KNOWN to
  this instance:
  - Own-device keys (`device_id == local_device_id`) are always evaluable —
    local `names` is authoritative.
  - Remote-device keys are evaluable ONLY if that device currently has a
    fresh entry in `main.py`'s `_federation_cache` (the same cache backing
    `GET /api/federation/sessions`, populated whenever any client — PWA,
    deck sidecar, agent — polls it) whose `fail_count` hasn't reached
    `_FEDERATION_GRACE_FAILURES` (the same reachability threshold that
    endpoint uses to report `status: "unreachable"`). When reachable, that
    device's live session keys are merged into the `live_keys` set passed
    to `prune_stale_keys`, so "reachable and genuinely absent" starts the
    grace clock and "reachable and present" clears it.
  - A remote device with NO current cache entry (never polled, evicted on
    auth failure, or past the reachability grace threshold) is **unknown,
    not dead**: its keys are never pruned and never accrue grace-clock
    time — `prune_stale_keys` actively clears (never advances) their
    `first_missed_at` bookkeeping while unknown, via the
    `local_device_id`/`known_remote_device_ids` parameters. **This is the
    offline-device guarantee**: a laptop that's closed/offline for days —
    far past `stale_key_grace_hours` — never has its view membership
    erased by every OTHER device in the fleet (each of which, before this
    fix, only knew ITS OWN local sessions and wrongly concluded the
    offline device's keys were gone — a real latent eraser, confirmed
    during the 2026-07 views-collapse incident investigation, though not
    its proximate cause). The key survives untouched no matter how long
    the device stays unknown, and resumes a **fresh** grace window (not a
    stale partial count) once the device is known again.
  - Legacy bare-name entries (no `device_id:` prefix) have no determinable
    owner and keep the pre-fix behavior unconditionally: evaluated directly
    against `live_keys`.
  - The whole positive-knowledge gate is opt-in via `local_device_id`
    (`known_remote_device_ids` defaults to empty): omitting it reproduces
    the exact pre-fix behavior for callers/tests that don't supply device
    identity, but `main.py`'s `_run_poll_cycle` (the only real caller)
    always supplies both.
  - The prune ACTION still goes through the v0.12.0 destructive-write
    backstop (`views.assess_views_destruction`) even though it writes via
    `save_settings()` directly rather than `patch_settings()` — the poll
    cycle assesses before/after `views` itself and refuses to persist (both
    `settings.json` and `pruning.json` left untouched, so the same
    situation reproduces visibly next cycle) if a mass prune would collapse
    views. Automatic pruning has no `allow_destructive` override — only a
    local operator editing `settings.json` directly can authorize a bulk
    deletion.
- **Sync groups let a device opt OUT of the server-global session/view
  selection** (`state.py`, `main.py`). A **sync group** owns its own
  `active_session` / `active_remote_id` / `active_view`. Group `"global"` —
  every client's implicit default — is stored in, and *is*, the top-level
  `state.json` keys described above; a device can instead select
  `"device:<its own device_id>"`, a private group scoped to itself.
  `GET`/`PATCH /api/state`, `GET /api/view`, `POST /api/sessions/{name}/connect`,
  and `DELETE /api/sessions/current` all accept an optional `?device_id=`
  query param that resolves which group is read or written; **omitting it is
  byte-identical to today** for every existing client (muxplex-deck,
  `muxplex_client`, and any client that predates this feature) — they all
  omit `device_id` and stay in `"global"`. `POST /api/heartbeat` accepts an
  optional `sync_group` body field (`"global"` or the caller's own
  `"device:<device_id>"` — anything else is 400) to move a device between
  groups; group creation happens there, seeded from global's CURRENT values
  (not defaults), so opting out doesn't teleport the device to the "All"
  view. **Unknown `device_id` on any of these endpoints is 404, never a
  silent fallback to `"global"`** — a device that has aged out of the 300s
  registry (`state.py`'s `prune_devices()`) must not be able to silently
  start driving everyone's screen; the client's recovery is to re-heartbeat
  and retry. Groups with no member device are garbage collected on the same
  poll-cycle pass that prunes stale devices — no separate TTL or timer.
  **`sync_groups` in `state.json` NEVER contains the key `"global"`** — the
  top-level keys ARE its one and only storage. This is deliberate: a
  `sync_groups["global"]` mirror would create two copies of one truth and
  therefore a divergence bug; with one copy there is no which-one-wins
  question to answer.
- **Per-session ttyd (formerly "the single shared ttyd process"): now ONE
  ttyd PER SESSION, each bound to its own UNIX domain socket** — see
  `ttyd.py`'s module docstring and `PER_SESSION_TTYD_SPEC.md` for the full
  design. Two devices connecting to two DIFFERENT sessions now get two
  independent ttyds and never interact; that used to be a **conflict**
  muxplex had to detect and refuse (`terminal_conflict`, below), and is now
  simply not a shared-resource question at all.
  **ttyd remains loopback-only by design in spirit, now via `AF_UNIX`
  rather than TCP, and this is unconditional (not gated by sync groups,
  `device_id`, or anything else): every ttyd runs `-W` (writable) with no
  `-c` (credential), so each is an unauthenticated writable terminal that
  must never be reachable off-box** — `AF_UNIX` is a *strictly stronger*
  fence than the old `-i 127.0.0.1` TCP bind (filesystem permissions, no
  network namespace at all). See `ttyd.py`'s module docstring and
  `../AGENTS.md`'s "ttyd is loopback-only by design" section for the
  socket-directory validation, the `0.0.0.0` incident this superseded, and
  the `.sock`-suffix fence against ttyd's silent TCP-7681 fallback. All
  access to a ttyd goes through the authenticated claims below, never a
  direct network path.
  Two sync groups can each have their own `active_session` selection AND
  their own live terminal now — there is no longer a single contended
  resource to arbitrate. `state.json`'s `terminal_session` / `terminal_group`
  are **retained, redefined**: `terminal_session` is now the *fallback
  target* a `WS /terminal/ws` with no `?session=` param resolves to (still
  written by `/connect`, still what the federation relay and any
  pre-this-change client rely on unchanged); `terminal_group` is
  **informational provenance only** — the group that most recently
  connected `terminal_session` — **no server behavior branches on it any
  more**. `POST /api/sessions/{name}/connect` no longer refuses or seizes
  anything: `ensure_ttyd()` is idempotent, so connecting to session X never
  disturbs session Y's ttyd, no matter which group holds which. `?takeover=
  true` is **accepted and silently ignored** (there is no longer a terminal
  to take over) — kept in the signature so existing clients sending
  `&takeover=true` don't 422. `DELETE /api/sessions/current` kills the
  caller's session's ttyd only when `relay_count(mine) == 0` — a
  **structural refcount check**, stronger than the old group-ownership
  claim: it also covers two devices in the *same* group co-viewing one
  session, which the old check did not. Closing your own private fullscreen
  must never black out someone else's live terminal.
  - **`409 terminal_conflict` on `/connect` is RETIRED — it cannot fire.**
    Its condition (`terminal_session is not None and terminal_group !=
    group`) arbitrated a single contended resource that no longer exists.
    A client that still handles this response body simply never sees it —
    version-tolerant in the direction `../AGENTS.md` requires.
  - **`WS /terminal/ws` and `WS /federation/{device_id}/terminal/ws` both
    take a new, additive, optional `?session=` query param** naming the
    target session directly. Absent, both fall back to
    `state["terminal_session"]` exactly as before — byte-identical to
    pre-this-change behavior in both directions (an old peer ignores the
    unknown param from a new peer; a new peer with no `session` falls back
    identically). The federation proxy forwards `?session=` upstream
    verbatim when supplied; it never dials a ttyd socket itself (see
    `federation_terminal_ws_proxy`'s docstring) — the transport change is
    invisible to it beyond that one forwarded parameter.
  - **`WS 4409` is KEPT, REDEFINED, and NARROWER**: no longer "another
    group holds the one terminal" (that resource is gone) but "you asked to
    attach to a session your own group has not selected" — a per-request
    consistency check rather than a resource claim. It fires only on genuine
    desync (e.g. a stale reconnect after the user switched sessions on
    another device), and — as of the fix below — the wire behavior matches
    the code's close-code arguments.** With an optional `?device_id=`:
    unknown `device_id` calls `_accept_then_close(websocket, code=4404)`;
    otherwise, if the resolved group's `active_session` is `None` or does
    not equal the *resolved target session* (`?session=` if given, else
    `terminal_session`), the code calls `_accept_then_close(websocket,
    code=4409)` — in both cases *before* any upstream ttyd connection is
    attempted. `4404` is additionally now the response for a missing,
    invalid, or unknown target session (widened from the old single-ttyd
    version, which could only ever see one possible session). This device
    is never shown, and can never type into, a session it did not itself
    select. Precisely this scenario is why the guard exists: without it,
    one device's `POST /connect` silently redirects every other connected
    terminal's WebSocket to the newly-attached session — the viewer's UI
    still shows their own session name while their keystrokes land in a
    DIFFERENT, live session belonging to someone else. **Residual gap,
    unchanged by per-session ttyd**: a client that sends no `device_id` at
    all (the federation relay is exactly such a client) gets none of this
    protection — but with `?session=` now forwarded, the remote relays the
    *named* session rather than whatever its fallback happened to hold, so
    the misdirection window closes on the transport side even though this
    guard still doesn't fire for it.

    **Incident (original bug): the 4409/4404 codes never reached any real
    client.** Before the fix below, both branches called
    `websocket.close(code=...)` *before* `websocket.accept()`. Per
    ASGI/uvicorn semantics, that does not produce a real WebSocket close
    frame at all — the connection was never upgraded, so there is no
    WebSocket to close. It serialized on the wire as a bare HTTP handshake
    rejection. Live raw-socket verification against a running instance
    confirmed this directly:

    ```
    $ raw socket probe of /terminal/ws?device_id=<non-owner>
    HTTP/1.1 403 Forbidden
    Content-Length: 0
    Content-Type: text/plain
    Connection: close
    ```

    The `websockets` Python client reported only `server rejected WebSocket
    connection: HTTP 403` — no numeric close code was ever visible to it,
    and a real browser's WebSocket `close` event surfaced `event.code ===
    1006` for any failed opening handshake, never `4409`/`4404`. The
    `4404`/`4409` values existed solely inside the ASGI message passed to
    `websocket.close()` and were discarded by the server before a byte
    reached the client. **Unit tests in `muxplex/tests/test_ws_proxy.py`
    did not catch this** because Starlette's `TestClient` operates at the
    ASGI message layer and never serializes real bytes over a socket —
    `WebSocketDisconnect.code` there genuinely was `4409`/`4404`, correctly,
    at that layer, but that layer was not the wire.

    **The fix: `_accept_then_close()` (`main.py`).** Both branches now call
    `websocket.accept()` *first*, then immediately `websocket.close(code=
    ...)`. Completing the handshake before closing is the ONLY way a real
    close frame — carrying a real numeric code — can exist to be reported
    to a browser at all; a browser's WebSocket object never exposes the
    HTTP status or body of a *failed* (pre-101) handshake to JavaScript
    (a WHATWG WebSocket API restriction, confirmed against the same live
    instance both before and after this change — see the raw-socket
    evidence in this fix's commit). This is also why uvicorn's
    WebSocket-denial-response ASGI extension (`websocket.http.response.*`,
    which lets a rejection carry a custom HTTP status/body without ever
    accepting) was considered and **rejected** as the mechanism here: it
    would let a script client that reads the raw HTTP response (e.g.
    Python's `websockets` library, via `InvalidStatus.response`) see a
    reused `{"terminal_conflict": true, ...}` body, but it cannot make a
    numeric code — or any body — reach a real browser, because the
    browser-side restriction applies regardless of what the server sends
    pre-accept. Only completing the handshake does. Post-fix raw-socket
    verification:

    ```
    $ raw socket probe of /terminal/ws?device_id=<non-owner>, post-fix
    HTTP/1.1 101 Switching Protocols
    ...
    <WS close frame, code=4409>
    ```

    A real browser's `close` event now reports `event.code === 4409` (or
    `4404`) directly — `terminal.js`'s `4409` WS-close branch, previously
    documented as unreachable in production, now fires for real. **Trade-off
    accepted deliberately**: the browser's `open` event fires briefly before
    `close` follows, since the handshake genuinely completes — this does
    not weaken the refusal (no relay, no ttyd contact, and no session data
    is ever exchanged on this connection either way; see
    `_accept_then_close()`'s docstring in `main.py`). The separate HTTP 409
    `terminal_conflict` body on the `POST /connect` escalation (§4.3 below)
    remains a second, independent, always-worked recovery path — unaffected
    by any of this, before or after the fix. **Residual gap, unchanged by
    this fix**: a terminal client that supplies no `device_id` at all gets
    none of this protection (the bundled PWA always sends one; requiring it
    unconditionally would be a breaking change to any yet-unknown client and
    remains out of scope). No `device_id` on `/terminal/ws` is the ONLY case
    where this guard does not apply. This fix does not touch the
    pre-accept ttyd-auto-spawn disconnect race documented at
    `terminal_ws_proxy`'s and `_client_disconnected()`'s docstrings in
    `main.py`: that guard runs only in the branch where device_id is
    absent/matching and the function proceeds toward a real relay, which
    `_accept_then_close()`'s branches return well before reaching.

- **Terminal WS input fence (`WS /terminal/ws`'s `client_to_ttyd`, gating
  `settings.input_enabled` / `settings.input_allowed_sessions` for
  Bearer-only callers)** — closes the third door to the RCE-by-design
  fence `POST /api/sessions/{name}/input` and the settings-template
  sibling both already document (`../AGENTS.md`'s "Terminal input"
  section). **Incident (confirmed by audit, fixed before it was exploited
  in the wild):** `terminal_ws_proxy` checked auth (localhost / cookie /
  Bearer) and the device/group consistency guard (§ above), but nothing
  about `input_enabled` / `input_allowed_sessions` at all. `client_to_ttyd`
  is a raw byte passthrough into ttyd, which types whatever it receives
  into the pane — identical RCE to `/input`, reachable by any
  Bearer-key holder simply by opening `WS /terminal/ws?session={name}`
  directly, regardless of either setting. Since the federation Bearer key
  is the SAME credential this repo already hands to headless AI agents for
  API access (`../AGENTS.md`, "The API is a public control surface"), this
  meant `input_enabled=false` and an empty `input_allowed_sessions`
  protected `/input` but not the terminal a human is actually looking at.

  **The fix, and why it is narrower than "gate the whole connection":**
  `_ws_auth_check` (main.py) now returns a `WSAuth(ok, bearer_only)` pair
  instead of a bare bool. `bearer_only` is True only when the ONLY
  credential that authorized the connection was the Bearer key — neither
  localhost nor a valid `muxplex_session` cookie applied. **Cookie always
  wins the classification when both are present**: forging a valid cookie
  requires `_auth_secret`, which a Bearer-key holder does not have, so
  "cookie + Bearer both sent" is a genuine browser session, never a
  Bearer-only caller in disguise. Localhost and cookie-authenticated
  callers are completely unaffected by everything below — this is a pure
  narrowing for the one caller class (`bearer_only`) that a human's own
  browser session can never be classified as.

  For a `bearer_only` connection, `terminal_ws_proxy` evaluates
  `terminal_input.input_allowed_for_session(target, settings)` — the SAME
  helper `send_session_input` (`/input`'s handler) now also calls, so the
  two enforcement points can never silently diverge — ONCE per connection
  (these are `LOCAL_ONLY_KEYS`; re-reading them on every keystroke buys
  nothing) and stores the boolean result as `input_gate_open`.
  `client_to_ttyd` then inspects each outgoing binary frame's LEADING BYTE,
  which is ttyd's own wire-protocol command byte (see `frontend/terminal.js`'s
  `_encodePayload`): `0x30` ('0') is keystroke/input data; `0x31` ('1') is
  a resize; the one text frame ttyd's client ever sends is the
  `{"AuthToken": ""}` handshake. When `input_gate_open` is False, ONLY
  `0x30`-prefixed frames are dropped (logged once per connection, not per
  keystroke) — the resize frame, the auth handshake, and (unconditionally,
  regardless of gate state) 100% of the ttyd→client OUTPUT direction all
  still flow. A denied `bearer_only` connection is therefore a fully live,
  resizable, real-time VIEWER that simply cannot inject keystrokes — never
  a closed or degraded connection.

  **Why viewing is deliberately never gated, for anyone:** a Bearer-key
  holder can already read every session's current pane content via `GET
  /api/sessions`' `snapshot` field (and `GET /api/sessions/{name}` for a
  deeper capture) with no per-session fence at all — that read access
  predates this fix and is unrelated to it. Gating the WS's viewing
  direction would add no confidentiality the API doesn't already expose,
  while it WOULD break `federation_terminal_ws_proxy`'s legitimate
  peer-to-peer relay (main.py), which dials a remote host's `/terminal/ws`
  with `Authorization: Bearer {remote_key}` **unconditionally** — never a
  cookie, since a browser session cookie is signed with each host's own
  `_auth_secret` and does not verify cross-host — every time a human uses
  the aggregated PWA to open a REMOTE host's terminal. From the remote
  host's point of view this relayed connection is `bearer_only` by
  construction, indistinguishable on the wire from a rogue agent holding
  the identical key and connecting directly. **Net effect for federation,
  stated precisely:** watching a remote host's terminal through the
  aggregated PWA keeps working unconditionally, exactly as before this
  fix. Typing into it now requires that the REMOTE host locally opts the
  target session into ITS OWN `input_enabled` / `input_allowed_sessions`
  (a `settings.json` edit on that host — `LOCAL_ONLY_KEYS`, never
  `PATCH`-able, never federation-synced, same as every other Bearer-only
  typing path) — this is an accepted, deliberate narrowing, not an
  oversight: the wire cannot distinguish "my own peer relaying a human's
  keystrokes" from "a Bearer holder typing directly," and per this
  fix's fail-safe rule, an undistinguishable case is denied, never
  guessed open.

  **Residual gap, explicitly not closed by this fix:** a federation PEER
  running a pre-fix muxplex version has no `bearer_only` classification at
  all — every caller it accepts over `/terminal/ws` is still ungated for
  typing, exactly as before, until that peer upgrades. This fix closes the
  door on every HOST that has it; it cannot retroactively close it on
  hosts that don't. There is no server-side way to detect a peer's patch
  level short of a version-negotiation mechanism that does not exist
  today — flagged here rather than silently assumed away.

# Backlog

Ideas captured but not committed to. Each entry states what we want, what already
exists that it has to fit alongside, and the open questions that would need
answers before it becomes a plan. Nothing here is a spec.

When an item graduates, it moves to `docs/plans/` as a real design and the entry
here is deleted.

---

## 2. A settings menu inside the soft deck

**What we want.** The soft deck (`/deck/`) should be configurable from inside
itself — subtle, out of the way, mobile-friendly, and covering everything you'd
otherwise have to set with the `muxplex-deck` CLI.

**Why it matters.** The hardware Stream Deck has no text input, so its config
lives in a JSON file driven by the CLI, and that is the right answer *for the
hardware*. The soft deck runs on a phone: it has a keyboard, it is often the only
thing in reach, and there is no comfortable way to run `muxplex-deck controls set
key.11 view_prev` from it. Config belongs where the device can actually accept
input. This is the one place the two decks *should* diverge rather than converge.

**Parity surface.** `muxplex-deck`'s config today: `server_url`, `key_file`,
`ca_file`, `poll_interval`, `sort`, `focus_app`, `controls`. Of those,
`RELOADABLE_KEYS` — `controls`, `sort`, `focus_app`, `poll_interval` — already
apply without a restart, which is a strong hint about which settings are cheap to
expose first.

**Beyond parity — what only the soft deck can offer.**

- **Layout as a setting.** Rows × columns per device. A phone in landscape, a
  tablet, and a propped-up spare screen all want different grids, and the
  hardware deck's grid is fixed by physics. This is the clearest example of a
  setting that is *inherently* per-device.
- **Control types the hardware has but the soft deck doesn't.** Emulated touch
  strip and emulated dials, matching the Stream Deck+. The action vocabulary
  already distinguishes `relative` actions (`view_cycle`, `page_cycle`,
  `brightness_cycle`) from `momentary` ones — the relative actions exist and
  currently have nowhere to live on a device with no dials. Adding emulated dials
  makes an existing, tested half of the action set reachable.
- **Control types neither has yet**, worth thinking about while the shape is
  still soft: long-press as a second binding per key (doubles the action surface
  without doubling the grid), swipe-along-strip, haptic feedback on press,
  theme/brightness for a phone left on a desk overnight.

**Open questions.**

- **Where does soft-deck config live?** localStorage is per-device, which matches
  the layout use case exactly — but it is invisible to backup, invisible to the
  server, and lost when the PWA is reinstalled (which we now know users have to
  do when a manifest changes). Server-synced config survives all that but makes
  "per-device layout" a contradiction. A split — synced *bindings*, local
  *layout* — is the obvious compromise and deserves scrutiny before being
  adopted on the strength of being obvious.
- **How does it stay honest with `muxplex-deck`?** `DECK_PARITY_ARCHITECTURE.md`
  already reached the conclusion that matters here: sharing a module would *not*
  have caught the `controlKeyContent` bug, because both sides can import the same
  function and one can still fail to call it. Parity has to be enforced by a
  golden fixture both sides assert against. If the soft deck grows its own
  config UI, that fixture is what stops the two schemas drifting.
- **What does "subtle and out of the way" actually mean here?** The deck is a
  deliberately game-like fullscreen surface; a visible gear icon spends a key
  slot and breaks the illusion. Candidates: long-press a reserved corner, edge
  swipe, a bindable `settings` action so the user chooses where it lives, or a
  URL the user can bookmark separately. Worth mocking before building.
- **What is the escape hatch?** If someone binds every key to `none`, or points
  `server_url` at nothing, the settings UI has to remain reachable. A reset
  gesture or a `?reset=1` URL parameter is not optional — it is the thing that
  makes the rest safe to ship.

---

## 3. Move focus-grabbing out of the deck and into muxplex

**What we want.** The code that raises the Muxplex window to the foreground
should live in **muxplex**, exposed over its API, instead of living in
`muxplex-deck`.

**Why it matters — and the concrete bug it fixes.** Focus-grabbing sits in the
hardware sidecar today, which means it only works from the machine the Stream
Deck is physically plugged into. The soft deck talks to muxplex *over the
network*, so it has no way to raise a window on the target machine — using the
soft deck from a phone cannot pop Muxplex forward on a Mac. Adding the capability
to `muxplex-deck` would not fix that either, because `muxplex-deck` is itself
locally attached to whichever machine holds the hardware.

The observation that resolves it: **muxplex already runs on every device, and
muxplex is the thing being raised.** The capability belongs in the process that
is local to the window it needs to act on. Every client — hardware deck, soft
deck, dashboard, a remote agent — then asks for focus the same way, over HTTP,
and the local server makes the OS call.

This also collapses a duplication problem before it starts. Platform support is
uneven (works on macOS; on Windows it flashes the taskbar icon, a documented OS
restriction on background processes stealing focus). Today that unevenness lives
in one client. Left alone, every new client that wants focus reimplements it.
Moved into muxplex, there is exactly one implementation to get right per
platform.

**Scope for a first pass.** Popping the window on *all* devices is acceptable —
no per-device targeting needed yet. That removes the hardest design question from
the first cut and can be tightened later if it turns out to be annoying in
practice.

**Open questions.**

- **Where does the fan-out happen?** The server could broadcast a focus request to
  its federation peers, or the client could call each peer directly. Server-side
  keeps clients dumb and matches how views already resolve; client-side avoids
  giving the server a new outbound-call responsibility. The federation circuit
  breaker is relevant either way.
- **Should this be fenced?** It lets a network client raise a window on someone's
  desktop. That is far milder than the input endpoint's RCE-by-design, but it is
  still "act on my machine," and `input_enabled` set the precedent for a
  local-file-only, default-off gate. Decide deliberately rather than by omission.
- **What stays per-device?** `focus_app` is a window-title match string today.
  That is inherently machine-specific and probably stays local config even after
  the code moves.
- **Is focus an action or a consequence?** Right now it is bound to a deck
  control. It could equally be an automatic side effect of switching sessions.
  Both are defensible; picking one is a UX decision, not a technical one.
- **What does `muxplex-deck` keep?** Ideally it deletes its focus implementation
  entirely and just calls the endpoint, leaving one code path. Worth confirming
  nothing else depends on the local-only behavior.

---

## 4. Recovering when Settings itself is unreachable

**What we want.** A way back into the soft deck's Settings that survives the deck
having configured itself into a state where Settings can't be seen, reached, or
used -- distinct from and more severe than item 2's original discoverability
problem, because discoverability means "the door is there but you didn't notice
it" and this means "the door isn't there, or you can't see well enough to use it."

**Why it matters -- three concrete ways in.** The 2026-07 incident that produced
the settings-discoverability fix (a real `SETTINGS` key on the view picker,
replacing long-press as the only entry point) fixed *finding* the door. It did not
fix what happens when the door itself is the thing that's broken:

- **Brightness is self-sealing.** `deckSettings.brightness` can be set down to 10%
  (`setBrightness`'s floor). At 10% on a phone screen in daylight, or just in a
  dim room, the picker's own SETTINGS key may not be legible enough to tap
  accurately -- the control that would fix low brightness is itself dimmed by it.
  There is no floor *below which Settings becomes forcibly readable regardless of
  the saved value*.
- **A degenerate grid removes the controls that reach Settings.** `computeKeyPlan`'s
  own settings-key guard (this fix) is honest about this: `reserved.mode ===
  'degenerate'` (fewer than 4 keys, or corners collide) means no BACK/PREV/NEXT
  *and* no SETTINGS -- by design, because there's nowhere to put them. That's the
  correct behavior for the key-grid itself, but it means a `gridOverride` saved
  from Settings (e.g. a 1xN layout picked for some other propped-device reason)
  can wall off the only path back to the panel that could undo it.
- **`?settings=1` was signed off as a mitigation and is not one.** It was described
  to the user as "always works" during the incident, but the deck's manifest is
  `display: fullscreen` / `start_url: /deck/` -- a fullscreen installed PWA has no
  address bar, so a URL parameter is unreachable from inside the exact thing it
  was supposed to rescue. It still works from a browser tab, which is not nothing,
  but it is not the universal escape hatch it was treated as.

**What it has to fit alongside.**

- `checkURLEscapeHatch()` (`?settings=1` / `?reset=1`) already exists and is real
  -- from a browser tab. Whatever ships here is additive to it, not a replacement;
  the browser-tab path should keep working exactly as it does today.
- `defaultDeckSettings()` / `saveDeckSettings` already model "reset to factory" as
  a full-object replacement. A recovery mechanism most likely wants to reuse that
  shape rather than inventing a second partial-reset concept.
- Whatever floor or override gets added has to coexist with `gridOverride`,
  `dialCount`, `stripCount`, and `brightness` all being independently saved,
  independently capable of contributing to "I can no longer reach the panel that
  controls me."

**Candidate directions** (roughly in the order the council's severity ranking
suggests looking at them):

- **A floor on self-sealing settings.** Render the Settings panel itself (and,
  more narrowly, just the SETTINGS key face) at a brightness floor independent of
  `deckSettings.brightness` -- e.g. Settings is never dimmed below some legible
  minimum regardless of the saved value, because the saved value is exactly what
  a user trying to reach Settings is trying to escape.
- **Out-of-band reset from the desktop dashboard.** The dashboard PWA already
  talks to the same `/api/settings` surface and isn't wearing whatever local
  `localStorage` state the phone dug itself into (deck settings are local-only by
  design -- see item 2's own open question on this). A "reset this device's deck"
  affordance reachable from a *different, unaffected* device sidesteps the whole
  self-sealing problem rather than trying to out-guess every way the phone could
  get stuck.
- **A hard, degenerate-grid-proof physical gesture**, e.g. a fixed multi-finger
  tap or a shake, that bypasses `computeKeyPlan` entirely and always opens
  Settings regardless of grid shape. Weighed against item 2's own accessibility
  finding (a gesture with no accessibility-tree node is exactly the failure mode
  this whole area of work exists to get away from) -- if pursued, it would need
  its own real affordance, not a repeat of the original long-press mistake.

**Open questions.**

- Does a brightness floor apply narrowly (Settings panel only) or broadly (the
  SETTINGS key face specifically, even before Settings is open)? The narrow
  version is simpler but doesn't help if the user can't see the key well enough
  to tap it in the first place.
- Is a `gridOverride` that produces a degenerate grid something Settings should
  refuse to save in the first place (validate at write time), rather than
  something recovery has to route around after the fact? Prevention and recovery
  are not mutually exclusive, but they are different amounts of work.
- Does the dashboard-reset direction need new API surface, or can it be expressed
  entirely as "the phone re-reads `defaultDeckSettings()` next time it loads,"
  driven by something set through `/api/settings`? That would keep deck settings
  local-only in spirit while still giving a remote-reset path.

---

## 5. Federation key hygiene: per-host generation and a rotation path

**What we want.** Every host in a federation holding its own generated federation
key, and a documented way to rotate one without taking a running federation down.
`muxplex generate-federation-key` already exists and already generates correctly.
What is missing is everything around it: nothing states that the key is meant to
be per-host, and nothing describes what an operator does when a key has to be
replaced.

**Why it matters.** The federation key is the highest-value credential muxplex
issues. It is the shared Bearer credential for the entire `/api/*` surface, and
`docs/AGENT_GUIDE.md` hands it to headless agents on purpose. What it authorizes
depends on the host's own fences -- and those fences are precisely what a
key-holder ends up standing in front of:

- Combined with `input_enabled` and a matching `input_allowed_sessions` entry, it
  authorizes **typing into a session**, which is RCE by design (`AGENTS.md`).
- As of the commit this entry is filed against, the terminal WS honors that same
  fence for `bearer_only` callers, closing the door that used to bypass it
  entirely. The fence is now consistent across all three typing paths -- which
  means the key is what gets a caller *to* the fence, and the fence is the only
  thing left in front of it.
- It is the same credential `muxplex-client` consumers and `muxplex-deck` carry.

A key that is unique per host scopes a compromise to that host. A key reused
across N hosts turns any single host's compromise into a fleet compromise, with
no way to contain it host-by-host after the fact. Generation and rotation are one
item because they fail together: without a documented rotation path, an operator
who later decides a key was a mistake has no cheap way to fix it, and that is
exactly the pressure that makes a bad key persist.

The realistic path to a reused key is not a decision anyone makes deliberately --
it is copying a working config to a second host because that is the fastest way
to get federation up. Nothing in the setup flow currently steers away from that.

**What it has to fit alongside.**

- `cli.py:670` `generate_federation_key()` is not the gap: `secrets.token_urlsafe(32)`
  into `~/.config/muxplex/federation_key`, 0700 parent, 0600 file, printed to
  stdout so it can be carried to peers. The generator is right; the lifecycle and
  the guidance around it are what's absent.
- `settings.load_federation_key()` reads a **single** value from
  `FEDERATION_KEY_PATH` (env-overridable). Any overlapping-window rotation has to
  reckon with that shape.
- Rotation is N-sided. Each peer holds the remote's key in its own per-remote
  config, so "rotate host A's key" is a simultaneous change on every peer that
  dials A. It also degrades quietly rather than loudly: per `AGENTS.md`, only
  `httpx.TransportError` trips the circuit breaker, so a peer that is reachable
  but now 401ing is reported as reachable.
- `GET /api/settings` already blanks `federation_key` and per-remote keys
  (`docs/AGENT_GUIDE.md`). Whatever gets added must not become the thing that
  un-blanks them.
- `muxplex doctor` already warns on TLS certificate expiry (`cli.py:1197`) --
  advisory, non-fatal, printed inline with everything else. That is the precedent
  for a credential-hygiene check and it is the right shape: a warning, not a
  refusal to serve.

**Open questions.**

- **What can `doctor` assert without becoming a bad password meter?** Shape is
  checkable locally and cheaply -- length and charset consistent with
  `token_urlsafe(32)` -- and would flag a hand-chosen key with no cross-host
  knowledge at all. Whether two hosts share a key is the more useful signal and
  the much harder one: it needs comparison across hosts, and moving key material
  around to enable a warning is a new disclosure surface in service of a warning.
  Would a salted digest over the existing sync channel be acceptable, or is any
  cross-host comparison worse than the warning is worth?
- **Is rotation atomic or overlapping?** Accepting both an old and a new key for
  a window is the standard answer and makes zero-downtime rotation easy -- but it
  doubles the credential surface for the duration, on the one credential that
  fronts an RCE fence. `load_federation_key()` returns one string today; making it
  a list is a small change with a large security question attached.
- **Should a key be generated automatically on first serve?** Uniqueness by
  construction beats uniqueness by instruction. Against it: a key that appears
  without the operator asking is a key the operator may not know exists, and the
  current command prints it precisely so a human can carry it to peers.
- **Where does the rotation procedure get documented?** `docs/AGENT_GUIDE.md` is
  the vendor-neutral operator-facing doc and already says where the key lives;
  README's settings table names `federation_key`. Neither describes a lifecycle,
  and a rotation procedure that lives only in a commit message is not a procedure.
- **Does the pre-fix-peer residue constrain the ordering?** `docs/API_SEMANTICS.md`'s
  terminal-WS-fence entry notes a residual gap for peers still running a pre-fix
  version. If a federation has to be swept for both an upgrade and a rotation,
  doing them in the wrong order may leave a window where neither property holds.

---

## 6. Referenced design docs that don't exist

**What we want.** Every `.md` filename cited by a tracked file to resolve to
something. A mechanical sweep of the repo turns up two distinct failures that look
identical from a reader's seat and need completely different fixes.

**Absent documents -- eight, with nothing to point at.** Counts are occurrences
across tracked files:

- **`KEY_DESIGN_SYSTEM.md`** -- 48 refs / 7 files. The deck's four surviving
  `DESIGN_*.md` docs, `deck.css`, `deck.js`, and `test_deck.mjs` all treat it as
  the authority for key rendering. The most-cited missing document in the repo.
- **`SOFT_DECK_DESIGN.md`** -- 32 / 5. Cited by all four deck design docs and by
  `deck/index.html:22`, which defers to its "OQ4" to justify a shipped icon
  decision.
- **`SESSION_PERSISTENCE_DESIGN.md`** -- 22 / 11, **including `muxplex restore
  --help`** (`cli.py:2641`). A user who follows the CLI's own pointer lands
  nowhere. `restore.py:2` and `manifest.py` both name it as the design of record
  for the restore milestone.
- **`DECK_PARITY_ARCHITECTURE.md`** -- 18 / 5, including **this file**
  (`BACKLOG.md:57`, item 2's open question about staying honest with
  `muxplex-deck`) and `layout.fixtures.json:3`, where it is named as the source
  and ownership rationale for the golden fixture both decks assert against.
- **`muxplex-client-design.md`** -- 15 / 8, including `client/README.md:10` and
  `:50` (presented as the authoritative design rationale, and as the home of the
  §3 included/excluded endpoint table), `client/pyproject.toml:29`,
  `pyproject.toml:75`, both CI workflows, and `test_client_contract.py`, which
  cites §1/§2/§7/§8 as the contract it is enforcing.
- **`COMMAND_PAIRS_UI_DESIGN.md`** -- 9 / 4, including `CHANGELOG.md:156`.
- **`CONTROL_MAPPING_DESIGN.md`** -- 6 / 3.
- **`COMPOSE_BAR_SPEC.md`** -- 4 / 3 (`app.js`, `index.html:133`,
  `test_compose.mjs:328`). The newest of the set: the compose bar shipped in the
  two commits immediately before this entry was written, and its spec never
  landed alongside it.

**Stale pointers -- 35 files, content that does exist.** Four `*_SPEC.md`
filenames are cited that never existed in this repo, but whose content *was*
preserved into `docs/plans/` under dated names:

- `AUTO_VIEWS_SPEC.md` (38 / 16) -> `docs/plans/2026-08-04-auto-views-plan.md`
- `PER_SESSION_TTYD_SPEC.md` (25 / 14) -> `docs/plans/2026-08-02-per-session-ttyd-plan.md`
- `COMMAND_PAIRS_SPEC.md` (13 / 9) -> `docs/plans/2026-08-02-named-session-command-pairs-plan.md`
- `DEVICE_LABEL_SPEC.md` (13 / 7) -> `docs/plans/2026-08-04-device-label-placement-plan.md`

This half is a rename, not a writing job -- but it reaches into `pyproject.toml:31`,
`CHANGELOG.md:156`, `scripts/README.md:163`, and into the successor plans
themselves (`2026-08-02-per-session-ttyd-plan.md:47` cites the ghost it replaced).
Worth noting that `AGENTS.md:39` cites `docs/plans/2026-08-04-auto-views-plan.md`
by its dated name and resolves cleanly: the convention already works everywhere it
was actually applied.

**Why it matters.** Two of these are load-bearing for people outside this repo
right now. `client/README.md` is the first thing a project integrating against
`muxplex-client` reads, and it hands them a dead link for the design rationale and
for the endpoint table -- with several such integrations landing this month. And
`muxplex restore --help` makes the CLI itself the source of the dead end, which is
worse than a dead link in a document: the user did the right thing and followed
the tool's own pointer.

**Open questions.**

- **Write them, or delete the citations?** There is not one answer for all eight.
  A document cited 48 times by four other design docs is load-bearing and probably
  has to be written; a document cited once from a comment can lose the citation.
  Reference count is a hint and not a rule -- `muxplex-client-design.md` is
  mid-pack by count and first by urgency.
- **Which of these are ADRs rather than designs?** Several describe things that
  shipped. `docs/plans/README.md` already frames that directory as ADRs and build
  logs under dated names, which is both the existing convention and the obvious
  home. Reconstructing a shipped design as a dated ADR after the fact is honest;
  back-dating it as though it were written before the code is not.
- **One sweep or two?** The rename and the writing are one symptom and two
  completely different jobs. Doing them together means a single change touches
  59 tracked files across source, tests, CI config, and `CHANGELOG.md` -- and
  `CHANGELOG.md` is release-owner territory per `AGENTS.md`.
- **Is a test the real fix?** A check that fails when a tracked file cites a `.md`
  that doesn't resolve would stop eight from becoming nine. But this repo already
  has a source-text tripwire it regrets (`test_frontend_js.py`, see `AGENTS.md`),
  and a link checker is exactly that shape. Weigh it against the alternative,
  which is running this same sweep again in six months.
- **What is the rule for citing a document that isn't written yet?** The failure
  mode here is a design doc that existed in someone's workspace, got cited from
  code as that code was written, and never made it into the repo. If
  cite-before-landing is going to keep happening, the citation format should make
  an unlanded document visibly different from a landed one.

---

## 7. Put `session_created` on the wire

**What we want.** A session's creation time on `GET /api/sessions` entries. The
value already exists inside the server; it just stops before the response.

**Why it matters.** v0.36.1 fixed a real inversion -- the session you just created
sorted dead last in the attention view -- by seeding a genuinely-new session's bell
as though it had just fired. The discriminator is tmux's own `#{session_created}`
compared against `_server_start_time` (`main.py:173`, `main.py:422`): created
during this process's lifetime means genuinely new, anything earlier means merely
first observed. That value is parsed out of the same `tmux list-sessions` call
that already produced `#{window_activity}` (no second round trip), cached in
`sessions.py`, and handed to the server as `get_session_created_times()`.

It stops there deliberately. `GET /api/sessions` entries remain `name`,
`snapshot`, `bell`, `last_activity_at`, `views`. That release's own "Known
limitation" states the consequence and names the remedy: an external client "still
cannot see when a session was created, and cannot reproduce this ordering decision
locally... Adding it would be a purely additive field and is the obvious future
change; it is not made here because nothing yet asks for it."

Something asks for it now. `muxplex-deck` orders sessions itself, and the projects
integrating against `muxplex-client` this month will each hit the same wall.
`AGENTS.md`'s standing answer for a rule clients would otherwise re-implement is
to resolve it server-side -- but that answer presumes the client has the inputs,
and here the input isn't on the wire at all.

**Urgency -- the reason this one isn't "someday."** It is wanted *before* those
integrations land, not after. A field that exists before its clients do costs
nothing. The same field added afterward means every client ships a local
workaround first and then carries it forever, which is exactly the
drift-across-clients problem `docs/API_SEMANTICS.md` exists to prevent.

**What it has to fit alongside.**

- Purely additive, which is the change class `AGENTS.md` explicitly prefers: new
  field, no rename, no semantic change. Clients tolerate unknown fields; the
  server tolerates their absence.
- `get_session_created_times()` is already defensive -- returns a copy, keyed by
  session name, unix epoch seconds, drops malformed values and sessions that have
  since closed (`test_sessions.py:302`+). The value needs no new hardening to be
  exposed.
- `docs/API_SEMANTICS.md` is where the semantics external clients re-derive live,
  and the needs-attention predicate documented there is the exact rule this field
  supports. Adding the field without updating that document would reproduce the
  gap the change exists to close.
- `muxplex_client`'s models are hand-rolled dataclasses rather than pydantic
  (`client/muxplex_client/models.py:4`), and client and server versions are locked
  in step (`test_client_contract.py:416`). The field lands in both at one version
  or in neither.

**Open questions.**

- **Is the timestamp enough?** The server's decision is `created_at >=
  _server_start_time`. A client holding only `created_at` cannot make that
  comparison -- it also needs to know when that server came up.
  `GET /api/instance-info` is the natural home for the other half, but putting it
  there means publishing a process-lifetime watermark as part of the contract.
- **The raw value, or the conclusion?** `AGENTS.md`'s standing answer is to
  resolve client-facing rules server-side rather than shipping logic to each
  client, and the purest form of that here is a derived boolean, not a timestamp.
  The raw value is more honest and more reusable; the boolean is less for each
  client to get wrong. This deserves an explicit decision rather than defaulting
  to whichever is easier to add.
- **Name and shape.** `created`, `created_at`, or `session_created`.
  `last_activity_at` is the field to match rather than invent against -- both are
  tmux-derived timestamps on the same entry, and an inconsistent pair is worse
  than either choice.
- **Absent value: `null`, or omit the key?** `get_session_created_times()` can
  legitimately have no entry when tmux reported nothing parseable. Both are
  tolerable under the two-way version-tolerance rule; picking deliberately beats
  picking by accident.
- **Federation and clocks.** A remote session's entry comes from that host's poll
  cycle and that host's clock. `last_activity_at` already carries that property,
  so there may be nothing new here -- but "may be" is not "checked."

---

## Notes

- Item 2 spans both repos in spirit but lands almost entirely here: the soft deck
  is served from `muxplex/frontend/deck/`. `muxplex-deck` is the hardware sidecar
  and its CLI-driven config is correct as-is.
- Item 3 is the reverse: it *moves* code out of `muxplex-deck` and into this repo,
  and should shrink the sidecar rather than grow it.
- Item 4 is a direct descendant of item 2's own "what is the escape hatch?" open
  question, split out once the 2026-07 incident and product-council review made
  clear it's a distinct, higher-severity problem from plain discoverability.
- Item 5 is the only entry here that spans *hosts* rather than repos. Nothing
  about it can be finished on one machine alone, and that is most of what makes
  it hard -- both halves (uniqueness, rotation) are properties of a fleet.
- Item 6 is one symptom and two jobs. The 35-file rename sweep is mechanical and
  could go first on its own; the eight absent documents are writing work with no
  shortcut. Splitting them is probably right, but the split has to be deliberate
  or the mechanical half will ship and the writing half will not.
- Item 7 is the only entry in this file with a deadline shape. Everything else
  here is genuinely "someday"; that one has a window, and the window closes when
  the first external client ships a workaround.
- No item has an owner or a date. They are written down so they stop occupying
  anyone's head, not to imply they are next.

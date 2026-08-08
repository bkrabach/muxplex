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

**Absent documents -- six, with nothing to point at.** Counts are occurrences
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
- **`CONTROL_MAPPING_DESIGN.md`** -- 6 / 3.

Two entries that used to be in this bucket were miscatalogued -- both existed the
whole time in the same throwaway cross-repo workspace as the `*_SPEC.md` set below,
and have since been moved to the stale-pointers bucket rather than written from
scratch: `COMMAND_PAIRS_UI_DESIGN.md` and `COMPOSE_BAR_SPEC.md`. The latter was a
particularly costly miscatalog: a prior sweep of this list read `COMPOSE_BAR_SPEC.md`
as a name with nothing behind it, concluded the compose bar's spec was "a rejected
draft that never shipped," and deleted its citations outright. The compose bar
shipped; only one sub-proposal inside that spec (a new `/compose` endpoint) was
rejected. The citations are restored below, pointed at the preserved file.

**Stale pointers -- content that does exist.** `*_SPEC.md`/`*_DESIGN.md`
filenames are cited that never existed in this repo, but whose content *was*
preserved into `docs/plans/` under dated names:

- `AUTO_VIEWS_SPEC.md` (38 / 16) -> `docs/plans/2026-08-04-auto-views-plan.md`
- `PER_SESSION_TTYD_SPEC.md` (25 / 14) -> `docs/plans/2026-08-02-per-session-ttyd-plan.md`
- `COMMAND_PAIRS_SPEC.md` (13 / 9) -> `docs/plans/2026-08-02-named-session-command-pairs-plan.md`
- `DEVICE_LABEL_SPEC.md` (13 / 7) -> `docs/plans/2026-08-04-device-label-placement-plan.md`
- `FOLLOWUP_QUEUE_SPEC.md` (14 / 12) -> `docs/plans/2026-08-05-per-session-followup-queue-plan.md`
- `COMMAND_PAIRS_UI_DESIGN.md` (7 / 2) -> `docs/plans/2026-08-02-named-session-command-pairs-ui-design.md`
  (this one already had a preserved copy in `docs/plans/` from an earlier pass;
  only the source-tree citations needed repointing)
- `COMPOSE_BAR_SPEC.md` (4 / 3) -> `docs/plans/2026-08-05-mobile-compose-bar-plan.md`

All seven are now a rename, not a writing job, and all source-tree citations have
been repointed to the dated files above. `AGENTS.md:39` and similar citations that
already used the dated name continue to resolve cleanly, as they did before.

One further preservation, not a rename: `FOCUS_GRAB_SPEC.md`, written for backlog
item 3 ("move focus-grabbing out of the deck and into muxplex") and not yet
built, also lived only in that same throwaway workspace. It is now
`docs/plans/2026-08-05-focus-grab-plan.md` -- the one file in `docs/plans/` that
is not a record of something shipped; its own header says so, and so does
`docs/plans/README.md`. Nothing in the source tree cites it yet, since the
feature it specifies has not been built.

**Why it matters.** Two of the remaining six absent documents are load-bearing for
people outside this repo right now. `client/README.md` is the first thing a project
integrating against `muxplex-client` reads, and it hands them a dead link for the
design rationale and for the endpoint table -- with several such integrations
landing this month. And `muxplex restore --help` makes the CLI itself the source of
the dead end, which is worse than a dead link in a document: the user did the right
thing and followed the tool's own pointer.

**Open questions.**

- **Write them, or delete the citations?** There is not one answer for all six.
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
  completely different jobs. The rename half is now done; the six-document
  writing half remains, and it alone touches `CHANGELOG.md` territory if any of
  the six turn out to already be described there -- `CHANGELOG.md` is
  release-owner territory per `AGENTS.md`.
- **Is a test the real fix?** A check that fails when a tracked file cites a `.md`
  that doesn't resolve would stop six from becoming seven. But this repo already
  has a source-text tripwire it regrets (`test_frontend_js.py`, see `AGENTS.md`),
  and a link checker is exactly that shape. Weigh it against the alternative,
  which is running this same sweep again in six months.
- **What is the rule for citing a document that isn't written yet?** The failure
  mode here is a design doc that existed in someone's workspace, got cited from
  code as that code was written, and never made it into the repo. `FOCUS_GRAB_SPEC.md`
  is the inverse case done right: preserved before anything cited it, so there is
  no dangling reference to create in the first place. If cite-before-landing is
  going to keep happening for the other six, the citation format should make an
  unlanded document visibly different from a landed one.

---

## 7. `PUT /api/sessions/{name}/status` -- agent-owned session status

**Status: shipped elsewhere, and superseded.** This item as originally framed
("put `session_created` on the wire") has shipped: `created_at` is on every
`GET /api/sessions` entry and on `muxplex_client.Session`
(`docs/API_SEMANTICS.md`'s "Semantics external clients re-implement" section).
The slot is repurposed here for the item that actually remains open, per
`docs/plans/2026-08-07-bell-causality-plan.md` §7 (Phase 2, deliberately not
scheduled with Phase 1).

**What we want.** `PUT /api/sessions/{name}/status` /
`DELETE /api/sessions/{name}/status` -- an agent-owned, closed-enum
(`working` / `blocked` / `failed` / `done`) plus bounded free-text `detail`,
server-stamped `set_at`, surfaced on `GET /api/sessions`,
`GET /api/sessions/{name}`, and `GET /api/view`. Full shape, constraints, and
rejected alternatives are in the plan's §7.1/§7.2 -- this entry exists so the
trigger condition is visible without re-deriving the design.

**Why this is Phase 2 and not Phase 1.** Phase 1 (`bell.source` + a bell on
follow-up-queue halt, shipped in v0.43.0) already covers the two bell-causality
cases muxplex can answer honestly and cheaply. What Phase 2 would add is a
*different* concept -- session status, not bell causality -- with its own
unanswered lifecycle question: **who clears it.** The plan's §7.3 recommends
agent-owned/no-TTL (the writer clears it via `DELETE`, `set_at` lets each
consumer age it), but that is a recommendation, not a proven answer.

**Trigger condition (from the plan's §7.5): build this when a SECOND,
independent consumer needs it.** One agent integration wanting it is a
preference; two converging on the same need is a contract. Until then this
would ship a mutable, agent-owned state surface with zero writers --
`AGENTS.md`'s standing rule (resolve server-side what clients would otherwise
re-implement) does not yet apply, because no client is re-implementing
anything here.

**What it has to fit alongside** (see plan §7.2 for the full, non-negotiable
list): separate endpoint, never a body on `POST /bell` (two independent
arguments in the plan's §1.2/§1.3 each require this); never advances the
follow-up queue; `needs_attention()` stays unchanged; same
`_require_valid_session_name` + fail-closed session-membership fence every
sibling endpoint uses; purely additive and version-tolerant in both
directions, landing in `muxplex_client` at the same version as the server
(the lockstep discipline `bell.source` just established).

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

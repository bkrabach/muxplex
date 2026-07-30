# Backlog

Ideas captured but not committed to. Each entry states what we want, what already
exists that it has to fit alongside, and the open questions that would need
answers before it becomes a plan. Nothing here is a spec.

When an item graduates, it moves to `docs/plans/` as a real design and the entry
here is deleted.

---

## 1. Auto-updating views

**What we want.** Views that populate themselves from rules instead of being
hand-curated lists. The first rule type is fnmatch-style globs against session
names — `amplifier-*`, `*-test` — so a view stays correct as sessions come and go.

**Why it matters.** Today a view is a frozen snapshot. Every new session that
belongs in it has to be added by hand, and every deleted one rots in the list
until something prunes it. `session_ttl` and `stale_key_grace_hours` exist
precisely because manual lists decay. A rule-based view cannot decay — it is
recomputed from live state every time it is read. That self-healing property is
the real prize; glob matching is just the first way to express a rule.

**What it has to fit alongside.**

- Views currently store **device-qualified keys**: `d502b663-…:team-pulse-manager`,
  not bare session names. Any matching rule has to decide what it matches against.
- `views_updated_at` is tracked separately from `settings_updated_at` specifically
  so a federation LWW race can't let a peer's stale `views` win. Rules are far
  smaller and change far less often than materialized lists, so this design
  probably *reduces* that race surface rather than adding to it.
- `hidden` is a reserved pseudo-view, never a member of `settings.views`.
- `GET /api/view` already resolves views server-side and returns a flat session
  list. `docs/API_SEMANTICS.md` states the direction plainly: resolve server-side
  rather than expecting each client to port more logic. Rule evaluation belongs
  on the server so the PWA, the soft deck, and the hardware sidecar cannot
  disagree about what a view contains.

**Open questions.**

- Does a glob match the bare session name, the device-qualified key, or either?
  `amplifier-*` almost certainly means "on any device," but `spark-1:*` is a
  natural thing to want to type. Two syntaxes or one?
- Can a view mix rules and manual pins? Pinning one specific session into an
  otherwise-automatic view seems obviously useful and obviously fiddly.
- Exclusions. `amplifier-*` minus `*-scratch` will be wanted the day after this
  ships.
- Ordering *within* an auto view — does it fall through to `sort_order`, or can a
  rule carry its own ordering?
- What happens to an existing manual view when someone adds a rule to it? Migrate,
  coexist, or refuse?

**Other rule types worth considering** (roughly in order of how often I'd expect
to reach for them):

| Rule | Sketch | Note |
|---|---|---|
| Device / host | everything on `spark-1` | Federation makes this immediately useful; the device qualifier is already in the key. |
| Attention state | anything with a bell or unread activity | Overlaps the new `attention` sort — worth deciding whether this is a *view* or just a sort, before building both. |
| Activity window | active in the last N hours | Cheap given `last_activity_at` already exists. |
| Working directory | sessions under `~/dev/foo` | Requires exposing cwd, which the API does not do today. |
| Set composition | union / intersection / difference of other views | Powerful, and the point at which this stops being a feature and starts being a query language. Probably the line not to cross. |
| Regex | escape hatch for the 1% | Strictly more power, strictly more footgun. Only if globs demonstrably fall short. |

**The thing to be careful about.** Every rule type added is a rule type the
server, the PWA, the soft deck, and the hardware sidecar all have to agree on
forever. Ship globs, live with them, and let real use decide what comes second.

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

## Notes

- Item 2 spans both repos in spirit but lands almost entirely here: the soft deck
  is served from `muxplex/frontend/deck/`. `muxplex-deck` is the hardware sidecar
  and its CLI-driven config is correct as-is.
- Item 3 is the reverse: it *moves* code out of `muxplex-deck` and into this repo,
  and should shrink the sidecar rather than grow it.
- Item 4 is a direct descendant of item 2's own "what is the escape hatch?" open
  question, split out once the 2026-07 incident and product-council review made
  clear it's a distinct, higher-severity problem from plain discoverability.
- No item has an owner or a date. They are written down so they stop occupying
  anyone's head, not to imply they are next.

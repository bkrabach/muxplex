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

## Notes

- Item 2 spans both repos in spirit but lands almost entirely here: the soft deck
  is served from `muxplex/frontend/deck/`. `muxplex-deck` is the hardware sidecar
  and its CLI-driven config is correct as-is.
- Neither item has an owner or a date. They are written down so they stop
  occupying anyone's head, not to imply they are next.

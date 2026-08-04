# Implementation Specification — Device Label Placement on Preview Tiles

Status: **MERGED to `main` on 2026-08-04 (`d17a040`..`76bfee2`, five commits) — NOT
YET IN A RELEASE.** The newest tag, `v0.35.0`, predates all five, so an installed
muxplex still has only the two-position `showDeviceBadges` checkbox. Retained as an
architectural decision record.

Source brief: `docs/BACKLOG.md` item 5, deleted in `76bfee2` now that it is done. Do
not re-add it.

Cross-repo impact: **none, and deliberately so** — `muxplex-deck` and the soft deck
are untouched. Neither draws a device label today, so there is nothing for this key to
govern there. That is a designed answer (§1 Q2), not an unfinished edge.

**Read §0 first — the headline finding is that the three-way choice was right and
"add a setting" was wrong.** The two-position version of this axis already shipped:
`showDeviceBadges` is in `DEFAULT_SETTINGS` (`settings.py:106`), is federation-syncable
(`settings.py:248`), is a Display-tab checkbox (`index.html:222-223`), and already gates
the tile and sidebar device labels. `true` **is** "title bar"; `false` **is** "not at
all". Adding an independent `deviceLabelPlacement` beside it would have produced a 2 × 3
cross product with three nonsense cells (`showDeviceBadges: false` +
`placement: "corner"` — which wins?) and put two Display-tab controls on the same
question. What shipped is a *widening of the existing axis*: `deviceLabelPlacement` is
authoritative, and `showDeviceBadges` is **retained forever** as a server-derived boolean
mirror (`showDeviceBadges == deviceLabelPlacement != "off"`), reconciled on every write
path by `settings.reconcile_device_label()` with a one-time load-time migration for
configs that predate the new key. Removing `showDeviceBadges` would break clients this
repo cannot see; it is never to be written directly.

**The legibility invariant is the load-bearing constraint and it is fragile in one
specific way.** The corner chip is **fully opaque** — `background: var(--bg-header)`
(`#0D1117`) under `color: var(--text-muted)` (`#8E95A3`), a constant **6.28:1**. Because
the terminal pixel underneath contributes *nothing* to the rendered chip, a full-bright
`htop` and an empty prompt produce byte-identical chip pixels; there is no ANSI colour
that can defeat it. **Any alpha < 1 — `rgba()`, `hsla()`, `opacity`, `backdrop-filter`,
`mix-blend-mode` — re-admits the background into the contrast calculation and destroys
the proof, not just the margin.** §6 records why blur, text-shadow, hover-reveal, and
`mix-blend-mode: difference` were each evaluated and rejected.

**Known gap between what this spec asked for and what was actually verified.** §8.5
specifies an *empirical* proof of that invariant: render the chip over hostile fixtures
(full white, full-bright magenta/green, mid grey, real `htop`), crop the chip's bounding
rect from the white and empty-prompt screenshots at identical coordinates, and `cmp` the
two crops for byte-identity. **That was not completed.** What shipped instead is a
mechanical guard test — `frontend/tests/test_app.mjs`, "G3: `.tile-device-tag` rule never
re-admits the terminal pixel via alpha" — which reads `style.css`, isolates the
`.tile-device-tag` rule, strips comments, and asserts none of the five alpha-introducing
tokens appears in a real property value while `background: var(--bg-header)` does. That
guard protects the invariant against future edits, which is the durable half of the
value; it is **not** the pixel evidence §8.5 asked for. Do not read §8.5 as done.

---

## §0. READ THIS FIRST — the three-way choice is right; "a new setting" is wrong

The three positions in the brief are correct and I am specifying all three. But the
framing — *"add a setting"* — is wrong, and shipping it that way produces a Display
tab with two controls that contradict each other.

**The two-position version of this setting already exists and already ships.**

| Fact | Evidence |
|---|---|
| `showDeviceBadges` is a real, persisted setting | `muxplex/settings.py:106` — `"showDeviceBadges": True` in `DEFAULT_SETTINGS` |
| …it is federation-syncable | `muxplex/settings.py:248` — member of `SYNCABLE_KEYS` |
| …it is a Display-tab checkbox today | `frontend/index.html:222-223` — `#setting-show-device-badges` |
| …it gates the tile's device label | `frontend/app.js:946` — `… && ds.showDeviceBadges !== false` |
| …and the sidebar item's | `frontend/app.js:1008` — same condition |
| …and it is user-documented | `README.md:340` — "Show device name labels on tiles" |

`showDeviceBadges: true` **is** "title bar". `showDeviceBadges: false` **is** "not at
all". The backlog item adds exactly one new position to an axis that already has two.

Adding an independent `deviceLabelPlacement` alongside it produces a 2 × 3 cross
product with three nonsense cells (`showDeviceBadges: false` + `placement: "corner"`
— which wins?) and puts two controls on the Display tab that answer the same
question differently. That is the same class of defect `docs/API_SEMANTICS.md`
already names for `sync_groups["global"]`: *"a mirror would create two copies of one
truth and therefore a divergence bug; with one copy there is no which-one-wins
question to answer."*

**So this spec is a widening of an existing axis, not a new setting**:
`deviceLabelPlacement` becomes the single source of truth; `showDeviceBadges` is
**retained forever** (it's in `/api/settings` and `SYNCABLE_KEYS`; removing it breaks
clients this repo cannot see) as a **server-derived boolean mirror** with one
authoritative direction. §2 specifies the reconciliation exactly.

### A fourth value was considered and rejected

`"auto"` — title bar on wide tiles, corner on narrow ones — is the obvious fourth
option and directly targets the stated symptom. **Reject it.** It needs a breakpoint
nobody can name, it requires container queries to be correct, and it makes a tile's
chrome rearrange itself while the user resizes a window or the grid reflows. The
honest read is that `"titlebar"` does *not* fix truncation — it is today's behavior —
and a user with narrow tiles picks `"corner"` once (which never truncates the session
name, because it takes zero title-bar width) and is done. A manual, stable answer
beats a clever, moving one.

---

## §1. Decisions on the four open questions

### Q1 — Syncable or per-device? → **Syncable.** `SYNCABLE_KEYS`, not `LOCAL_ONLY_KEYS`.

The per-device argument is real but it has nowhere to land, and the mechanism people
reach for when they say "per-device" is not what they think it is.

1. **`LOCAL_ONLY_KEYS` is a security fence, not a per-device tier.** Every member
   (`input_enabled`, `new_session_template`, `tmux_socket_dir`, `tls_cert`, …) is
   there because it names *a command or a filesystem path the server itself later
   executes or reads* (`settings.py:167-218`). `PATCH /api/settings` **silently
   ignores** those keys. Putting a display preference there would mean the user
   cannot change it from the Settings UI at all — only by SSH-ing to the host and
   editing `settings.json`. That is not "per-device"; it is "unreachable."
2. **`settings.json` is per-*host*, not per-*screen*.** The narrow screen is the
   browser — a phone rendering the PWA served by the host. Even a local-only key
   would be shared by the phone and the ultrawide pointed at the same muxplex. There
   is no server-side per-screen settings store; `state.py`'s `sync_groups` govern
   *selection state* (`active_session` / `active_view` / `active_remote_id`), not
   settings. The only per-screen store is browser `localStorage`, which this codebase
   deliberately abandoned for display settings
   (`docs/plans/2026-04-08-server-side-settings-design.md`). Going back is a
   regression, not a feature.
3. **Precedent is unanimous.** Every display preference is syncable: `fontSize`,
   `hoverPreviewDelay`, `gridColumns`, `bellSound`, `viewMode`, `showHoverPreview`,
   `activityIndicator`, `gridViewMode`, `sidebarOpen` — and `showDeviceBadges`
   itself. Making this key's *successor* per-device would silently regress behavior
   for anyone who relies on the sync today.
4. **`fontSize` already has the identical phone-vs-ultrawide tension** and is
   syncable. If per-screen display profiles ever become a real need, that is its own
   feature covering *all* display keys at once. Solving it for this one key would
   leave `fontSize` fleet-wide and be strictly more confusing than solving it for
   none.

**Decision: add `deviceLabelPlacement` to `SYNCABLE_KEYS`. Do not add it to
`LOCAL_ONLY_KEYS`** — it names no command and no path the server executes or reads,
so it fails every criterion of that fence.

### Q2 — Does the deck honor it? → **No. Nothing, deliberately. Zero cross-repo work.**

The open question assumes three surfaces draw a device label. **Two of them don't
draw one at all, and don't even fetch remote sessions.** Verified:

| Surface | Draws a device label? | Fetches federated sessions? | Evidence |
|---|---|---|---|
| PWA | Yes | Yes | `app.js:947` `.device-badge`; `app.js:596-598` `/api/federation/sessions` |
| Soft deck (`frontend/deck/`) | **No** — tile is NAME / STATE / PREVIEW only | **No** | `deck.js:1551-1571` plan object has `name`/`state`/`preview`/`target`/`flags`, no device field; `deck.js:1794-1795` fetches `/api/view` + `/api/sessions` (local only) |
| `muxplex-deck` sidecar | **No** — `session.name` in the NAME band | **No** | `src/muxplex_deck/rendering.py:346-410` `render_session_key`; `src/muxplex_deck/views.py:65-66` reads `GET /api/sessions` |

There is nothing to put out of agreement. The premise behind "a setting only the PWA
honors puts three surfaces out of disagreement" is false at `50b1560`.

**Why it stays that way, not just "not yet":**

- Honoring the setting on either deck requires *first adding a device label to a deck
  tile that has none*, which requires *first making the deck federation-aware*. That
  is a separate, much larger feature ("federated deck"), of which this setting would
  be a downstream detail — not a prerequisite.
- A StreamDeck key's NAME band is `round(0.28 * size)` px tall
  (`rendering.py:146`) — roughly 20px on a 72px key — and `_fit_label`
  (`rendering.py:229-242`) already truncates session names by measured pixel fit.
  There is no lower-right corner to anchor anything in, and adding a device label to
  the NAME band would reproduce, on a far smaller budget, *exactly the truncation
  problem this setting exists to fix*.
- **Cost if it were done anyway** (for the record, so this is a decision and not an
  omission): `muxplex-deck` already fetches `GET /api/settings` (`main.py:8`), so
  reading the key is free. The real cost is everything downstream — a device-name
  source (federation client work in the sidecar), a `Config` field, re-tuned band
  geometry in `rendering.py`, a `muxplex-deck` minor release, and a version-skew
  window in which the sidecar silently ignores the key. For zero present benefit.

**Deliverable instead of code:** one paragraph in `docs/API_SEMANTICS.md` (see §7)
telling a sidecar author that this key is PWA-scoped and is *not* a semantic external
clients are expected to re-implement. That doc exists precisely to stop each client
from porting rules; here the rule is "there is no rule for you," and saying so is
what prevents a future author from wondering.

Do **not** add a test asserting the deck has no device label. Asserting an absence
locks in a non-decision; the doc paragraph is the durable artifact.

### Q3 — Silently override "off" when >1 device is in view? → **No. Never. Agree with the recorded lean.**

Three reasons, in order of weight:

1. **A setting that ignores itself is indistinguishable from a bug.** The user turns
   labels off, they stay off for a week, then a second host comes online and labels
   reappear with no user action. The user's model becomes "this setting is broken,"
   and they are not wrong.
2. **The override would be unreliable in exactly the direction that matters.** Its
   trigger — "more than one device has sessions in the current view" — is recomputed
   every ~2s poll from federation data whose reachability flaps
   (`docs/API_SEMANTICS.md`, circuit breaker / `_FEDERATION_GRACE_FAILURES`). Labels
   would blink in and out as a remote goes unreachable and comes back. That is worse
   than either steady state.
3. **Nothing is actually lost.** This is presentation only. Views store
   `device_id:name` keys; `sessionKey` is `f"{device_id}:{name}"`
   (`main.py:1254`, `main.py:3094`). Identity survives regardless of what the tile
   draws.

**"Some other way," concretely** — three mechanisms, two of which already exist:

1. **Grouped grid mode is explicitly out of scope of this setting.**
   `renderGroupedGrid()` (`app.js:1442-1469`) emits `<h3 class="device-group-header">`
   with the full device name above each device's tiles. That is a *per-group* header,
   so it costs **zero per-tile title-bar width** — it is the ambiguity answer that
   does not compete with the session name. `deviceLabelPlacement` must not touch it
   in any mode.
2. **The sidebar's device headers are likewise out of scope.** `renderSidebar()`
   (`app.js:1312-1328`) emits `<h4 class="sidebar-device-header">` whenever
   `multi_device_enabled` is true. Unchanged in all three modes.
3. **New, and this is the piece that makes the choice honest at the moment it is
   made:** Settings > Display shows a live consequence line directly under the
   control when `deviceLabelPlacement === "off"` **and** `multi_device_enabled ===
   true` (§5). Surfacing the consequence *where the user makes the decision* is the
   correct place for it. Surfacing it at render time, by overriding the choice, is
   not.
4. Plus one additive accessibility change that costs nothing and is unconditional:
   the tile's `aria-label` carries the device name in **all three** modes (§4.1), so
   assistive tech and automation never lose the disambiguator even when the pixels do.

Explicitly rejected: a "show the label anyway when two visible sessions share a name"
exception. That *is* the silent override, wearing a narrower hat.

### Q4 — Is lower-right the right corner? → **Yes, and the reason is sharper than "least used."**

Lower-right is **not** reliably empty — a full-screen TUI paints there (htop's
function-key bar, vim's ruler, tmux's status-right clock). Neither is any other
corner. So corner choice cannot be settled on "which is blank"; it is settled on
**which occludes the fewest *meaningful* pixels**, given that the legibility treatment
must be worst-case-proof anyway (§6).

The tile preview is **bottom-anchored**: `.tile-body pre { position: absolute;
bottom: 0; }` (`style.css:290-303`), and the JS trims trailing blank lines then takes
the last N (`app.js:957-961`). The tile therefore shows the *bottom* of the pane —
where the prompt and cursor live.

| Corner | What it occludes |
|---|---|
| upper-left | TUI title bars, htop's CPU meters, the first line of any file — always painted, often the primary content |
| upper-right | htop's Tasks/Load block, tmux status-right — often painted |
| **lower-left** | **The shell prompt and the command being typed.** The single worst choice, and the one the bottom-anchoring points straight at |
| **lower-right** | The tail of the last line — nothing in a shell at a prompt; a *status field* (never the primary content region) in a TUI |

**Keep lower-right.** It is the only corner that is blank in the most common case, it
moves *away* from the bottom-left content the anchoring emphasizes, and in the case
where it is painted it covers a status field rather than content.

---

## §2. The setting

### 2.1 Definition

| Property | Value |
|---|---|
| Key | `deviceLabelPlacement` |
| Type | `str`, closed vocabulary |
| Allowed values | `"titlebar"` \| `"corner"` \| `"off"` |
| **Default** | **`"titlebar"`** — byte-identical to today's rendering |
| Storage | `~/.config/muxplex/settings.json`, via `DEFAULT_SETTINGS` |
| Sync | **Yes** — add to `SYNCABLE_KEYS` |
| Fence | **No** — must NOT be in `LOCAL_ONLY_KEYS` |
| Naming | camelCase, matching the display-settings cluster (`fontSize`, `showDeviceBadges`, `activityIndicator`); snake_case is reserved for server/behavior keys in this file |

Semantics:

- `"titlebar"` — device label rendered in the tile header / sidebar item header, as
  the `.device-badge` span. Today's behavior.
- `"corner"` — device label rendered inside the preview area, anchored lower-right,
  as the `.tile-device-tag` chip. Header carries no badge.
- `"off"` — no device label on the tile or sidebar item. Group headers and sidebar
  device headers are unaffected (Q3).

**In all three modes the existing `multi_device_enabled` + non-empty `deviceName`
guard still applies.** A single-device install renders no label today and must render
none after this change, in any mode.

### 2.2 `settings.py` changes

Add, immediately after `"showDeviceBadges": True,` in `DEFAULT_SETTINGS` so the two
sit together:

```python
    # Where a session's device label is drawn on its preview tile / sidebar item.
    # Closed vocabulary (DEVICE_LABEL_PLACEMENTS):
    #   "titlebar" -- in the tile header (today's behavior; the default)
    #   "corner"   -- inside the preview, anchored lower-right
    #   "off"      -- not drawn at all
    # Presentation ONLY: views store device-qualified "device_id:name" keys, so
    # session identity survives regardless of what the tile draws.
    #
    # THIS KEY IS AUTHORITATIVE; `showDeviceBadges` above is a DERIVED MIRROR of
    # it (showDeviceBadges == deviceLabelPlacement != "off"), maintained by
    # reconcile_device_label() on every write path. showDeviceBadges is retained
    # for pre-v0.36 clients that read it and must never be removed from
    # DEFAULT_SETTINGS or SYNCABLE_KEYS. Do not write showDeviceBadges directly.
    "deviceLabelPlacement": "titlebar",
```

Add the vocabulary constant beside `COPY_MODES`-style closed sets:

```python
DEVICE_LABEL_PLACEMENTS: frozenset[str] = frozenset({"titlebar", "corner", "off"})
```

Add `"deviceLabelPlacement"` to `SYNCABLE_KEYS`'s "Display preferences" block. Do
**not** add it to `LOCAL_ONLY_KEYS`.

### 2.3 The one truth and its mirror — exact reconciliation rules

Add one helper, and call it from exactly the places listed. This is the whole
mechanism; there is no other place that may write either key.

```python
def reconcile_device_label(current: dict, incoming: dict | None = None) -> None:
    """Reconcile deviceLabelPlacement (authoritative) with showDeviceBadges (mirror).

    Mutates *current* in place. *incoming* is the patch/sync payload that produced
    *current*, or None when reconciling a settings dict with no payload behind it
    (the load-time migration and the self-heal pass).
    """
```

**Rules, evaluated in this order. Exactly one branch applies.**

| # | Condition | Action |
|---|---|---|
| R1 | `incoming` contains a **valid** `deviceLabelPlacement` | Apply it. Set `showDeviceBadges = (value != "off")`. Any `showDeviceBadges` in the same payload is **ignored** (authoritative key wins). No log. |
| R2 | `incoming` contains `showDeviceBadges` as a **`bool`** and no valid `deviceLabelPlacement` | `False` → `deviceLabelPlacement = "off"`. `True` → set `deviceLabelPlacement = "titlebar"` **only if it is currently `"off"`**; otherwise leave it unchanged. Then set `showDeviceBadges` to match the derivation. |
| R3 | `incoming` contains `showDeviceBadges` as a **non-bool** | Ignore both keys; leave `current` unchanged for this pair. `logger.warning`. |
| R4 | Neither key present (or `incoming is None`) | **Self-heal**: set `showDeviceBadges = (current["deviceLabelPlacement"] != "off")` if it disagrees. |

**Why R2's asymmetry is correct and not clever-and-wrong.** A legacy client can only
express two states, so `False → "off"` is lossless but `True → ?` is genuinely
ambiguous. Choosing "if it's already shown somewhere, don't move it" means an old peer
syncing `showDeviceBadges: true` can never silently drag a user off `"corner"` and
onto `"titlebar"`. The `off → true` case does lose information — the user's previous
corner-vs-titlebar choice — but that information was destroyed by the round trip
through a boolean, not by this rule, and the old client cannot express more.

**Why R4 exists.** Without it, a hand-edit of `showDeviceBadges` in `settings.json`
creates a permanent divergence that a legacy client would then read as truth. R4 makes
the mirror self-healing on the next write of any kind. Document in the README row that
`showDeviceBadges` is derived and must not be hand-edited.

### 2.4 Call sites

| Site | File | Call |
|---|---|---|
| Load-time migration | `settings.load_settings()`, after the merge loop and beside the existing `if not result["device_name"]:` fixup | See below |
| API write path | `settings.patch_settings()`, after the `for key in DEFAULT_SETTINGS:` apply loop, before `save_settings()` | `reconcile_device_label(current, patch)` |
| Federation write path | `settings.apply_synced_settings()`, at the same relative position (after incoming keys are applied, before persist) | `reconcile_device_label(current, incoming)` |

**Load-time migration** (read-only; no write, no side effects):

```python
    # One-time migration: an existing settings.json predating deviceLabelPlacement
    # carries only showDeviceBadges. Derive the placement from it so the mirror
    # and its source never start out disagreeing. Idempotent: once
    # deviceLabelPlacement is present in the FILE, this branch stops firing.
    if "deviceLabelPlacement" not in data and "showDeviceBadges" in data:
        result["deviceLabelPlacement"] = (
            "titlebar" if data["showDeviceBadges"] is True else "off"
        )
    reconcile_device_label(result)
```

`data` is the parsed file dict already in scope in `load_settings()`. Note the
migration keys off **file presence**, not the merged result — the merged result always
has both keys.

Without this migration, every existing user who had unchecked "Show device badges"
would get labels back on upgrade. That is the single most likely regression in this
change; it is why the migration is a required deliverable, not an optimization.

### 2.5 Validation — reject, never coerce

`load_settings()` performs **no** enum validation (matching `activityIndicator`, whose
test suite at `tests/test_settings.py:1023` persists `"icon"` verbatim). The client
normalizes unknown values to `"titlebar"` (§4.2). That is the house style and it is
preserved.

The **write** paths differ, and differ from each other on purpose:

- **`PATCH /api/settings` with an unknown `deviceLabelPlacement` → `400`, no write.**
  Response body:
  ```json
  {"detail": "unknown deviceLabelPlacement", "unknown_device_label_placement": true,
   "allowed": ["corner", "off", "titlebar"]}
  ```
  `allowed` is `sorted(DEVICE_LABEL_PLACEMENTS)`. The boolean discriminator follows
  the convention `docs/API_SEMANTICS.md` already names — `backstop: true`,
  `terminal_conflict: true`, `unknown_command_id: true` — and this is its fourth
  member. Validate in `main.py`'s `update_settings()`, before `patch_settings()` is
  called, so no partial write occurs. This is not a breaking change: a value that was
  never valid cannot have a working client behind it.

- **Federation sync with an unknown `deviceLabelPlacement` → ignore the key, keep the
  local value, `logger.warning`, apply every other key in the payload normally.** A
  peer must never be able to wedge sync, and sync is consistently more conservative
  than PATCH in this codebase (it never gets `allow_destructive`). Implemented as
  R1's "valid" test failing, which falls through to R2/R4 — no extra branch needed.

Neither path coerces a bad value into a good one. There is no silent fallback.

---

## §3. Surface-by-surface rendering

| Surface | `titlebar` | `corner` | `off` | Change required |
|---|---|---|---|---|
| PWA grid tile | `.device-badge` in `.tile-header` (today) | `.tile-device-tag` in `.tile-body` | nothing | `buildTileHTML()` |
| PWA sidebar item | `.device-badge` in `.sidebar-item-header` (today) | `.tile-device-tag` in `.sidebar-item-body` | nothing | `buildSidebarHTML()` |
| PWA grouped-grid header | full device name | full device name | full device name | **none — out of scope (Q3)** |
| PWA sidebar device header | full device name | full device name | full device name | **none — out of scope (Q3)** |
| PWA hover-preview popover | snapshot only | snapshot only | snapshot only | **none** — it is `pointer-events: none` and full-viewport; it answers "what is on this pane," not "which device" |
| Soft deck (`frontend/deck/`) | n/a | n/a | n/a | **none, deliberately (Q2)** |
| `muxplex-deck` sidecar | n/a | n/a | n/a | **none, deliberately (Q2)** |

The sidebar honors all three positions with identical semantics — "the label is drawn
in the preview, lower-right" means the same thing everywhere a preview is drawn. No
special case to remember. The sidebar item's body is ~86px tall (`.sidebar-item`
120px, `.sidebar-item-header` 32px), which comfortably carries a 16px chip; and the
sidebar's name field at a 200px sidebar width is even more width-starved than a tile's,
so `"corner"` buys back more there than it does on the grid.

---

## §4. Frontend — `frontend/app.js`

### 4.1 `buildTileHTML(session, index, mobile)` — replace lines 943-948 and 969-979

**Constraint (`AGENTS.md`, "Frontend classic scripts share one global scope"):** every
new top-level binding must be globally unique across `app.js` + `terminal.js`.
`DEVICE_LABEL_PLACEMENTS` and `deviceLabelPlacement` were checked against `terminal.js`
at `50b1560` — neither exists there. `test_shared_scope.mjs` covers this automatically
once the code lands; run it.

Replace the badge block:

```js
  // Device label — placement governed by deviceLabelPlacement (see
  // DEVICE_LABEL_SPEC.md). The multi_device_enabled + deviceName guard is
  // unchanged from the showDeviceBadges era: a single-device install draws
  // no label in any placement.
  var placement = deviceLabelPlacement(ds);
  var showDeviceLabel = !!(_serverSettings && _serverSettings.multi_device_enabled
    && session.deviceName) && placement !== 'off';
  let badgeHtml = '';
  let cornerHtml = '';
  if (showDeviceLabel && placement === 'titlebar') {
    badgeHtml = `<span class="device-badge" title="${escapeHtml(formatDeviceVersion(session.deviceVersion))}">${escapeHtml(session.deviceName)}</span>`;
  } else if (showDeviceLabel && placement === 'corner') {
    cornerHtml = `<span class="tile-device-tag">${escapeHtml(session.deviceName)}</span>`;
  }
```

Replace the `<div class="tile-body">` line so the chip is the **last child of
`.tile-body`, after the `<pre>`** (source order gives it paint priority; `z-index: 1`
in CSS makes that explicit):

```js
    `<div class="tile-body"><pre>${ansiToHtml(lastLines)}</pre>${cornerHtml}</div>` +
```

Replace the `aria-label` on the `<article>` — **unconditional across all three
placements** (this is Q3's accessibility guarantee):

```js
  const ariaLabel = (_serverSettings && _serverSettings.multi_device_enabled && session.deviceName)
    ? `${name} on ${session.deviceName}`
    : name;
```
…and use `aria-label="${escapeHtml(ariaLabel)}"` in the `<article>` tag.

**The corner chip deliberately carries no `title` attribute.** A native tooltip inside
the preview area would race the hover-preview popover (`app.js:5583-5592`, default
1500ms delay) and produce two overlapping tooltips. Device *version* remains
discoverable via `.sidebar-device-header__version` (`app.js:1326`), which `"corner"`
does not remove.

### 4.2 New top-level helper — place beside `formatDeviceVersion()` (~line 916)

```js
// Closed vocabulary for deviceLabelPlacement. An unknown value (a hand-edited
// settings.json, a peer from a future version) resolves to 'titlebar' -- today's
// behavior -- exactly as activityIndicator resolves unknown values to 'both'.
const DEVICE_LABEL_PLACEMENTS = ['titlebar', 'corner', 'off'];

/**
 * Resolve the effective device-label placement from display settings.
 * @param {object} ds - getDisplaySettings() result
 * @returns {'titlebar'|'corner'|'off'}
 */
function deviceLabelPlacement(ds) {
  var v = ds && ds.deviceLabelPlacement;
  return DEVICE_LABEL_PLACEMENTS.indexOf(v) !== -1 ? v : 'titlebar';
}
```

Export both from the module-exports block at `app.js:6011-6050` (`buildTileHTML`,
`buildSidebarHTML`, `getDisplaySettings` are already there): add
`deviceLabelPlacement` and `DEVICE_LABEL_PLACEMENTS`.

### 4.3 `buildSidebarHTML(session, currentSession, currentRemoteId)` — lines 1006-1010, 1034

Identical badge/corner block to §4.1 (same `placement` / `showDeviceLabel` /
`badgeHtml` / `cornerHtml` shape), and:

```js
    `<div class="sidebar-item-body"><pre>${ansiToHtml(lastLines)}</pre>${cornerHtml}</div>` +
```

The sidebar `<article>` has no `aria-label` today; do not add one (out of scope).

### 4.4 `DISPLAY_DEFAULTS` — `app.js:345-355`

Add one line. **Do not remove `showDeviceBadges`** — it is still returned by
`/api/settings` and `test_app.mjs:3966` / `:5288` assert its presence and default:

```js
  showDeviceBadges: true,        // DERIVED mirror of deviceLabelPlacement; not read by the renderer
  deviceLabelPlacement: 'titlebar', // 'titlebar' | 'corner' | 'off' -- authoritative
```

### 4.5 `readDisplaySettingsFromUI` / `onDisplaySettingChange` — `app.js:4198-4216`

- **Delete** the two `showDeviceBadgesEl` lines (4198-4199).
- **Delete** `showDeviceBadges: ds.showDeviceBadges,` from the `patch` object (4211).
  After this change `app.js` never writes `showDeviceBadges` — one writer, one truth.
- **Add**:
  ```js
  var deviceLabelPlacementEl = document.getElementById('setting-device-label-placement');
  if (deviceLabelPlacementEl) ds.deviceLabelPlacement = deviceLabelPlacementEl.value;
  ```
  and `deviceLabelPlacement: ds.deviceLabelPlacement,` in the `patch` object.
- **Add** at the end of the function, after `applyDisplaySettings(ds)`:
  `_updateDeviceLabelAmbiguityNote(ds);` (§5.3).

**Do not add a re-render call.** Today a `showDeviceBadges` change takes effect on the
next poll (≤2s); the same is true here, and the Settings dialog is covering the grid
anyway. Adding a synchronous re-render would be a behavior change outside this spec's
scope, and the test plan assumes the poll-driven path.

### 4.6 `openSettings()` — `app.js:4267-4271`

- **Delete** the two `showDeviceBadgesEl` lines.
- **Add**:
  ```js
  const deviceLabelPlacementEl = $('setting-device-label-placement');
  if (deviceLabelPlacementEl) deviceLabelPlacementEl.value = deviceLabelPlacement(settings);
  _updateDeviceLabelAmbiguityNote(settings);
  ```
  Using `deviceLabelPlacement()` here (not the raw value) is load-bearing: assigning
  an unknown string to a `<select>.value` silently yields `""` and renders a blank
  control.

### 4.7 `bindStaticEventListeners` — `app.js:5626` region

Replace the `setting-show-device-badges` binding with:

```js
  on($('setting-device-label-placement'), 'change', onDisplaySettingChange);
```

---

## §5. Settings UI

### 5.1 Control choice — a `<select>`, matching `Activity indicator`

Two precedents exist for a closed enum in this dialog:

- `#setting-activity-indicator` — a `<select>` with four values, one row, no helper
  text (`index.html:226-233`). Same shape of choice as ours: *where/how is a visual
  treatment applied*.
- `#setting-tmux-copy-mode-*` — a radio group with a `settings-helper` sentence per
  option (`index.html:348-366`). Radios are right *there* because each option needs a
  paragraph explaining a behavioral difference the user cannot infer.

Our option labels are self-explanatory in three words. **Use a `<select>`**, which
preserves the Display tab's one-row-per-setting density.

### 5.2 Markup — `frontend/index.html`, replacing lines 221-224

The new row sits in **exactly the position the checkbox occupied**: inside
`<div class="settings-panel" data-tab="display">`, after the "Show hover preview" row
and before the "Activity indicator" row.

```html
          <div class="settings-field">
            <label class="settings-label" for="setting-device-label-placement">Device label</label>
            <select id="setting-device-label-placement" class="settings-select">
              <option value="titlebar" selected>In the title bar</option>
              <option value="corner">In the preview (lower right)</option>
              <option value="off">Don't show</option>
            </select>
          </div>
          <div class="settings-field settings-field--column hidden" id="device-label-ambiguity-note">
            <span class="settings-helper">Sessions from different devices will look identical on the grid. Grouped view and the sidebar still show device headers.</span>
          </div>
```

The row label is `Device label`; the options complete the sentence ("Device label: in
the title bar"), which is what makes the choice self-evident without a helper line.
`selected` on `titlebar` mirrors the server default, matching how
`#setting-font-size` marks `14` and `#setting-grid-columns` marks `auto` (and
`test_html_settings_font_size_options` asserts that pattern).

`.hidden` is an existing class (`style.css:114`). `.settings-helper` is existing
(`style.css:1591`). `.settings-field--column` is existing (`style.css:1466`). **No new
CSS classes are introduced by the Settings UI** — the only new class in this whole
change is `.tile-device-tag` (§6).

### 5.3 The ambiguity note — new helper in `app.js`

```js
/**
 * Show the "devices will look identical" consequence line under the device-label
 * control when, and only when, the user has chosen 'off' AND this install actually
 * aggregates more than one device. Making the consequence visible at the moment of
 * the decision is deliberately the ONLY place this is surfaced -- the render path
 * never second-guesses the setting (see DEVICE_LABEL_SPEC.md, Q3).
 * @param {object} ds - display settings (or server settings; only the one key is read)
 */
function _updateDeviceLabelAmbiguityNote(ds) {
  var el = $('device-label-ambiguity-note');
  if (!el) return;
  var ambiguous = deviceLabelPlacement(ds) === 'off'
    && !!(_serverSettings && _serverSettings.multi_device_enabled);
  el.classList.toggle('hidden', !ambiguous);
}
```

`.hidden` is toggled via `classList.toggle`, so `test_css_class_definitions.mjs`
**does** cover it — and `.hidden` is already defined. No action needed.

### 5.4 Considered and declined

Disabling or hiding the whole row when `multi_device_enabled === false`. Today's
checkbox is shown unconditionally in that case and does nothing; the same is true of
several rows on this tab. Fixing that is a broader, separate concern across the
multi-device family of controls. Do not grow this change to cover it.

---

## §6. The legibility problem — the real design work

`"corner"` draws over live terminal content: arbitrary text, arbitrary ANSI colors,
arbitrary luminance, changing every poll. The label must be readable over a
full-bright `htop` and over an empty black prompt, with no intermediate case that
breaks.

### 6.1 Approaches evaluated

| Approach | Verdict |
|---|---|
| `backdrop-filter: blur()` | **Insufficient.** Blur reduces *busy-ness*, not *luminance*. Blurred full-bright magenta is still full-bright magenta; light text over it is still low-contrast. It also has real compositing bugs inside `overflow: hidden` positioned ancestors on older WebKit. Solves the wrong axis. |
| `text-shadow` outline (4- or 8-way) | **Reinforcement, not a guarantee.** A dark outline adds nothing over a dark background; over a *white* background the light glyph collapses into the outline and only the outline is legible. Also mushy at 10px. Acceptable as a redundant extra; not acceptable as the mechanism. |
| Opacity-on-hover | **Defeats the purpose.** The label exists for at-a-glance identification of one tile among many. Hiding it until hover removes the glance, and touch devices have no hover at all. |
| `mix-blend-mode: difference` | **Has a reachable zero.** Auto-inverting is elegant until the background is mid-grey, where `difference` drives the glyph toward the background and the text vanishes. A failure point that a real `htop` theme can reach is not a solution. |
| **Opaque scrim chip** | **Chosen.** The only approach whose worst case is *provably identical* to its best case. |

### 6.2 Chosen: a fully opaque chip

The chip is **completely opaque**. Terminal content is not visible through it at all.
Therefore the contrast calculation has exactly two inputs, and both are constants:

- Foreground `var(--text-muted)` = `#8E95A3` → relative luminance ≈ `0.2987`
- Background `var(--bg-header)` = `#0D1117` → relative luminance ≈ `0.0055`
- **Contrast ratio ≈ 6.28:1** — passes WCAG AA for normal text (4.5:1), and is the
  *same ratio* `.tile-meta` already uses on `.tile-header` (`style.css:224-229`), so
  the chip reads at exactly the header's weight.

**Why it holds in the worst case, stated as a proof rather than a hope:** the terminal
pixel underneath contributes nothing to the rendered chip, so a full-bright `htop`
and an empty prompt produce *byte-identical* chip pixels. There is no ANSI color, no
256-color index, no truecolor value that changes the ratio. The moment any alpha < 1
is introduced — `rgba()`, `opacity`, `backdrop-filter`, `mix-blend-mode` — the
terminal pixel re-enters the calculation and some background defeats it. **That is the
invariant, and §8.4 tests it mechanically.**

Two supporting decisions, each carrying its own load:

1. **1px `var(--border-subtle)` border.** An opaque near-black chip on a black
   terminal is legible but shapeless — the user cannot tell whether it is chrome or
   terminal output. The border separates it over dark content; over bright content
   the dark chip is self-separating. Both extremes read as chrome.
2. **`font-family: var(--font-ui)`, not mono.** A second, *independent* legibility
   signal that does not depend on color at all: the chip is distinguishable from
   terminal output by typeface before contrast is even considered. Everything inside
   `.tile-body pre` is `'SF Mono', 'Fira Code', Consolas, monospace`
   (`style.css:296`); nothing else in the preview area is.

**The accepted cost, named honestly:** an opaque chip occludes absolutely — you cannot
see the characters behind it. A translucent scrim would let you half-see them. That is
the right trade: a label you cannot read has no value, and half-seeing one character
of a status bar has none either.

### 6.3 CSS — append to `frontend/style.css`, in the "Tile body" section, after the `.tile-body pre` block (ends line 303) and before `.tile--loading` (line 306)

```css
/* Device label drawn INSIDE the preview (deviceLabelPlacement === 'corner').
   Also used inside .sidebar-item-body, which has the same
   position:relative / overflow:hidden / black background shape.

   LEGIBILITY INVARIANT -- do not weaken. This chip overlays arbitrary, arbitrarily
   coloured live terminal content, so its contrast must NOT be a function of what is
   underneath it. It is FULLY OPAQUE, which makes both sides of the contrast
   calculation constants: #8E95A3 on #0D1117 = 6.28:1 (WCAG AA), identical over a
   full-bright htop and over an empty prompt. Introducing rgba(), opacity,
   backdrop-filter, or mix-blend-mode here re-admits the terminal pixel into that
   calculation, and for every one of them there is a background colour that defeats
   it. The 1px border makes the chip read as chrome over BLACK content (where the
   opaque fill alone is shapeless); the UI font makes it read as chrome regardless
   of colour. See DEVICE_LABEL_SPEC.md §6 and its guard test in
   frontend/tests/test_app.mjs. */
.tile-device-tag {
  position: absolute;
  right: 6px;
  bottom: 4px;
  z-index: 1;
  max-width: 45%;
  padding: 1px 5px;
  border: 1px solid var(--border-subtle);
  border-radius: 3px;
  background: var(--bg-header);   /* #0D1117 -- OPAQUE. never rgba(). */
  color: var(--text-muted);       /* #8E95A3 -- 6.28:1 on the above */
  font-family: var(--font-ui);    /* NOT mono: reads as chrome, not as output */
  font-size: 10px;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;           /* tile click + hover-preview delegation must pass through */
}
```

`z-index: 1` is required: `.tile-body pre` is `position: absolute`, so both are
positioned and source order alone would be relied upon implicitly. Be explicit.

`max-width: 45%` bounds a long device name to under half the tile. Truncating *here*
is acceptable in a way it never is in the title bar, because the chip takes no budget
from the session name — which is the entire point of the feature.

`pointer-events: none` is required: the tile is clickable and `#session-grid` carries
delegated `mouseenter`/`mouseleave` handlers for the hover preview
(`app.js:5583-5592`). A chip that swallowed events would break both.

### 6.4 Mobile — append inside the existing `@media (max-width:599px)` block (opens line 852, closes line 887), after the tier-idle rules

```css
  /* The corner chip needs a preview tall enough to have a corner. The 'active'
     (24px body) and 'idle' (36px body) tiers are single-line strips -- the chip
     would cover the only visible line. Both tiers still render the title bar, so
     a tile is never label-less by accident; in 'corner' placement they simply
     draw no label. 'bell' (90px body) keeps it. */
  .session-tile--tier-active .tile-device-tag,
  .session-tile--tier-idle .tile-device-tag { display: none; }
```

**Known, accepted consequence:** on a phone in `"corner"` mode, only bell-tier tiles
show a device label. This is acceptable because mobile tiles are full-width, so the
title-bar truncation this setting exists to fix barely occurs there — a mobile user
has little reason to choose `"corner"`. Do not "fix" it by silently degrading
`"corner"` to `"titlebar"` on mobile: that is a mode change the user did not make, in
the same family as the Q3 override this spec rejects.

---

## §7. Documentation deliverables

### 7.1 `README.md` — the settings table (~line 340)

`tests/test_readme.py:51` (`test_readme_documents_all_settings_keys`) asserts that
every non-`_`-prefixed key in `DEFAULT_SETTINGS` appears as `` `key` `` in the README.
**A missing row fails the suite.** Two edits:

- Add a row for `deviceLabelPlacement`:
  > `` `deviceLabelPlacement` `` | `"titlebar"` | Where a session's device label is drawn: `titlebar` (in the tile/sidebar header, the default), `corner` (inside the preview, anchored lower right), or `off` (not drawn). Presentation only — views store device-qualified keys, so session identity is unaffected. Honored by the PWA only; the soft deck and the `muxplex-deck` sidecar draw no device label at all.
- Rewrite the existing `showDeviceBadges` row:
  > `` `showDeviceBadges` `` | `true` | **Derived — do not edit.** Maintained by the server as `deviceLabelPlacement != "off"`. Retained so pre-v0.36 clients keep working; set `deviceLabelPlacement` instead.

### 7.2 `docs/API_SEMANTICS.md` — one new bullet in "Semantics external clients re-implement today"

Content, in that file's voice:

- `deviceLabelPlacement` is the authoritative placement key; `showDeviceBadges` is a
  **server-derived mirror** (`showDeviceBadges == deviceLabelPlacement != "off"`),
  reconciled on every write path (`patch_settings`, `apply_synced_settings`, and a
  read-time migration in `load_settings`). Clients should read and write
  `deviceLabelPlacement` and treat `showDeviceBadges` as read-only.
- A client that writes only `showDeviceBadges` (a pre-v0.36 client, or an old
  federation peer) still works: `false` sets the placement to `"off"`; `true` moves
  it off `"off"` **only when it is currently `"off"`**, so an old peer's sync can
  never silently drag a user from `"corner"` to `"titlebar"`.
- An unknown `deviceLabelPlacement` on `PATCH /api/settings` is a **400** carrying
  `unknown_device_label_placement: true` plus an `allowed` list, with no write — the
  fourth member of the `backstop` / `terminal_conflict` / `unknown_command_id`
  discriminator convention. On the **federation sync path** the same value is
  ignored with a warning and every other key in the payload still applies; a peer
  must not be able to wedge sync.
- **This key is deliberately PWA-scoped and is NOT a semantic other clients are
  expected to re-implement.** At the time of writing neither the soft deck
  (`frontend/deck/`) nor the `muxplex-deck` sidecar renders a device label on a
  session tile, and neither fetches federated sessions — so there is nothing for the
  key to govern there. A sidecar that later grows federated tiles should read this
  key at that point; until then, ignoring it is correct, not a gap.

### 7.3 `docs/BACKLOG.md`

Remove item 5 (or mark it done, per whatever the repo does at merge time — the
existing items carry no status field, so removal is the consistent action). Renumber
nothing else; item 5 is the last numbered entry. Update the `## Notes` section only
if it references item 5 (at `50b1560` it does not).

### 7.4 `AGENTS.md`

**No change.** This feature introduces no new invariant a contributor could break
unknowingly that isn't already covered by the existing "Frontend classic scripts share
one global scope" and testing sections. The legibility invariant lives in the CSS
comment beside the rule it governs and in the guard test — which is where someone
about to weaken it will actually be looking.

---

## §8. Test plan

Run frontend tests with the glob, never a single file:
`node --test frontend/tests/*.mjs` (per `AGENTS.md`).
Run Python tests **in the DTU only**: `make test`. Commit locally first — that is
what makes `git archive HEAD` correct.

### 8.1 Python — `muxplex/tests/test_settings.py`

| # | Test | Assertion |
|---|---|---|
| P1 | Default exists | `DEFAULT_SETTINGS["deviceLabelPlacement"] == "titlebar"` |
| P2 | Vocabulary | `DEVICE_LABEL_PLACEMENTS == frozenset({"titlebar", "corner", "off"})` |
| P3 | Syncable | `"deviceLabelPlacement" in SYNCABLE_KEYS` |
| P4 | Not fenced | `"deviceLabelPlacement" not in LOCAL_ONLY_KEYS` |
| P5 | Mirror retained | `"showDeviceBadges" in DEFAULT_SETTINGS` **and** `in SYNCABLE_KEYS` (regression guard: a future cleanup must not delete it). Extend the existing `test_syncable_keys_contains_display_settings` set at `tests/test_settings.py:1054` to include the new key. |
| P6 | Migration, off | File `{"showDeviceBadges": false}`, no placement → `load_settings()["deviceLabelPlacement"] == "off"` |
| P7 | Migration, on | File `{"showDeviceBadges": true}`, no placement → `"titlebar"` |
| P8 | Migration is idempotent | File `{"showDeviceBadges": false, "deviceLabelPlacement": "corner"}` → `"corner"` (placement present ⇒ no migration) **and** `showDeviceBadges is True` (R4 self-heal on load) |
| P9 | R1 derive, off | `patch_settings({"deviceLabelPlacement": "off"})` → `showDeviceBadges is False` |
| P10 | R1 derive, corner | `patch_settings({"deviceLabelPlacement": "corner"})` → `showDeviceBadges is True` |
| P11 | R1 authoritative wins | `patch_settings({"deviceLabelPlacement": "off", "showDeviceBadges": True})` → placement `"off"`, `showDeviceBadges is False` |
| P12 | R2 no clobber | Start at `"corner"`; `patch_settings({"showDeviceBadges": True})` → placement still `"corner"` |
| P13 | R2 off | Start at `"corner"`; `patch_settings({"showDeviceBadges": False})` → `"off"` |
| P14 | R2 on-from-off | Start at `"off"`; `patch_settings({"showDeviceBadges": True})` → `"titlebar"` |
| P15 | R3 non-bool | `patch_settings({"showDeviceBadges": "yes"})` → placement unchanged, no exception |
| P16 | R4 self-heal | File `{"deviceLabelPlacement": "corner", "showDeviceBadges": false}`; `patch_settings({"fontSize": 16})` → `showDeviceBadges is True` |
| P17 | Sync ignores garbage | `apply_synced_settings({"deviceLabelPlacement": "banana", "fontSize": 18}, …)` → placement unchanged, **`fontSize == 18`** (other keys still apply), no exception |
| P18 | Sync from old peer | `apply_synced_settings({"showDeviceBadges": False}, …)` from a device at `"corner"` → `"off"` |

### 8.2 Python — `muxplex/tests/test_api.py`

| # | Test | Assertion |
|---|---|---|
| P19 | Exposed | `GET /api/settings` body contains `deviceLabelPlacement` |
| P20 | Valid PATCH | `PATCH /api/settings {"deviceLabelPlacement": "corner"}` → 200, body `deviceLabelPlacement == "corner"`, `showDeviceBadges is True` |
| P21 | Invalid PATCH rejects | `PATCH {"deviceLabelPlacement": "banana"}` → **400**, body `unknown_device_label_placement is True`, `allowed == ["corner", "off", "titlebar"]` |
| P22 | Invalid PATCH writes nothing | Same request with `{"deviceLabelPlacement": "banana", "fontSize": 99}` → 400 **and** a follow-up `GET /api/settings` shows `fontSize` unchanged (no partial write) |

### 8.3 Node — `frontend/tests/test_app.mjs`

| # | Test | Assertion |
|---|---|---|
| N1 | Default in `DISPLAY_DEFAULTS` | source includes `deviceLabelPlacement`; `getDisplaySettings().deviceLabelPlacement === 'titlebar'` |
| N2 | `showDeviceBadges` still present | existing tests at `:3966` and `:5288` still pass unmodified |
| N3 | Normalizer | `deviceLabelPlacement({deviceLabelPlacement:'corner'}) === 'corner'`; `…'banana'` → `'titlebar'`; `…undefined` → `'titlebar'`; `deviceLabelPlacement(null) === 'titlebar'` |
| N4 | Tile, titlebar | `multi_device_enabled:true`, `deviceName:'spark-1'`, placement `'titlebar'` → HTML contains `device-badge`, does **not** contain `tile-device-tag` |
| N5 | Tile, corner | placement `'corner'` → contains `tile-device-tag`, does **not** contain `device-badge` |
| N6 | Tile, corner position | in the `'corner'` HTML, `indexOf('tile-device-tag') > indexOf('</pre>')` **and** the chip is inside `.tile-body` (assert the substring between `<div class="tile-body">` and the following `</div>` contains `tile-device-tag`) |
| N7 | Tile, off | placement `'off'` → contains neither |
| N8 | Single-device guard | `multi_device_enabled:false` → contains neither, for **all three** placements |
| N9 | Missing deviceName | `deviceName:''` → contains neither, for all three placements |
| N10 | Escaping | `deviceName: '<img src=x onerror=1>'`, placement `'corner'` → HTML contains no raw `<img` |
| N11 | aria-label, all modes | `multi_device_enabled:true`, `deviceName:'spark-1'` → `aria-label` contains `on spark-1` for **all three** placements |
| N12 | aria-label, single device | `multi_device_enabled:false` → `aria-label` is the bare session name |
| N13-N19 | Sidebar parity | Repeat N4-N10 against `buildSidebarHTML`, substituting `.sidebar-item-body` for `.tile-body` |
| N20 | HTML control exists | `index.html` includes `setting-device-label-placement`, and the `<select>` has options `titlebar`/`corner`/`off` |
| N21 | Old control removed | `index.html` does **not** include `setting-show-device-badges`. This mirrors the existing negative-assertion precedent at `test_app.mjs:4005` / `:4015` (`setting-show-activity-glow` / `-dot`, removed when `activityIndicator` replaced two checkboxes with one select — the identical shape of change). Update `:3998` and `:4048` rather than deleting them: assert the new control and its binding. |
| N22 | Binding | `bindStaticEventListeners` source includes `setting-device-label-placement`, no longer includes `setting-show-device-badges` |
| N23 | Ambiguity note markup | `index.html` includes `device-label-ambiguity-note` and it carries `hidden` in the static markup |

### 8.4 Guard tests — the cross-file blind spots

**G1 — shared global scope.** `test_shared_scope.mjs` covers this automatically (it
parses `index.html`'s real `<script src>` tags). No new file. Verify manually before
committing that `DEVICE_LABEL_PLACEMENTS` and `deviceLabelPlacement` do not exist as
top-level bindings in `terminal.js` (confirmed absent at `50b1560`), then let the test
prove it.

**G2 — `.tile-device-tag` must be defined in `style.css`. The existing guard does NOT
cover it, and the builder must not assume otherwise.**
`test_css_class_definitions.mjs` extracts classes applied via `classList.add/remove/
toggle` and literal `.className =` assignments only (see its own header comment).
`.tile-device-tag` is emitted inside an HTML template literal, so it is invisible to
that scan. This is a real gap in the guard, not a reason to skip the check.

Do this, in order:

1. **First, on unmodified `main`**, extend `test_css_class_definitions.mjs` with a
   second extractor that also collects **fully-literal** `class="…"` attributes from
   the script sources (skip any attribute whose value contains `${` or a string
   concatenation), and feed those tokens through the same
   defined-in-paired-stylesheet assertion. Run it.
2. **If it is green on unmodified `main`**, keep the generalization — it retroactively
   covers `session-tile`, `tile-header`, `tile-name`, `tile-body`, `device-badge`,
   `sidebar-item*`, `source-tile*`, and every future template-emitted class, for about
   fifteen lines. That is the right outcome and the file's stated purpose already
   covers it ("every class name our own frontend JS applies to an element").
3. **If it is NOT green on unmodified `main`**, revert the generalization, ship only a
   targeted assertion in `test_app.mjs` (read `style.css`, assert it contains a
   `.tile-device-tag` rule), and open a follow-up issue naming the pre-existing
   violations it found. Do not turn this feature into a stylesheet cleanup.

**G3 — the legibility invariant, tested mechanically.** New test in `test_app.mjs`:
read `frontend/style.css`, extract the `.tile-device-tag` rule body, and assert it
contains **none of** `rgba(`, `hsla(`, `opacity`, `backdrop-filter`, `mix-blend-mode`,
and that its `background` value is `var(--bg-header)`. Message: *"the corner device
label overlays arbitrary terminal content; its contrast is only provable while it is
fully opaque — see DEVICE_LABEL_SPEC.md §6."* This is the guard against the actual
likely regression: someone "softening" the chip with `rgba(13,17,23,0.6)`.

### 8.5 Legibility over hostile terminal content — verifying the pixels, not the class

A class assertion cannot prove readability. Prove it by showing the chip is
**invariant** under the worst content the terminal can produce.

**Setup.** Two federated muxplex instances (or one with `multi_device_enabled` and a
loopback remote) so `deviceName` is non-empty and the label renders at all. Set
`deviceLabelPlacement: "corner"`. Open the grid at desktop width.

**Hostile fixtures** — in a tmux session visible on the grid, run each and let the
poll cycle pick it up (~2s):

| Fixture | Command | Why |
|---|---|---|
| Full-bright magenta | `printf '\e[48;5;201m'; clear` | Saturated colour, maximum chroma |
| Full white | `printf '\e[48;5;231m'; clear` | Maximum luminance — the case that defeats a dark translucent scrim |
| Full-bright green | `printf '\e[48;5;46m'; clear` | Maximum green channel, where luminance weighting peaks |
| Mid grey | `printf '\e[48;5;244m'; clear` | The value that defeats `mix-blend-mode: difference` |
| Real TUI | `htop` | Real-world mixed content painting the lower-right function-key bar |
| Empty prompt | `clear` | The best case / control |

**Evidence requirement — byte-identity, not opinion.** For the *white* and *empty
prompt* fixtures, screenshot the tile, crop the chip's bounding rect from both at the
same coordinates, and assert the two crops are **byte-identical** (`cmp` on the two
PNG crops, or a pixel-diff with max ΔE = 0). That is the direct proof that the worst
case equals the best case, and it is the only evidence that distinguishes "I chose a
readable colour" from "the background provably cannot affect it." `AGENTS.md`'s own
debugging corollary applies: reason about bytes, not about whether it *looks* fine.

Also record, as supporting evidence:

- A screenshot per fixture with the device name legible in each — attach to the PR.
- `htop` specifically: confirm the chip sits over the function-key bar and the device
  name is still readable, and that the 1px border makes the chip read as chrome.
- Mobile at ≤599px: bell-tier tile shows the chip; active/idle tiers do not (§6.4),
  and both still show the title bar.

**Round-trip verification.** Independently of pixels:

1. Set `"titlebar"` → labels in headers, no chips. Confirm a long session name still
   truncates (this is today's behavior; the setting does not claim to fix it).
2. Set `"corner"` → the same long session name now uses the **full** header width and
   does **not** truncate. This is the feature working; capture before/after.
3. Set `"off"` → no labels anywhere on tiles or sidebar items; **grouped grid mode
   and the sidebar device headers still show device names** (Q3's answer, verified
   rather than asserted).
4. With `"off"` and `multi_device_enabled: true`, open Settings > Display → the
   ambiguity note is visible. Turn `multi_device_enabled` off → the note is hidden.
5. Reload the page → the placement persists (server-backed, not localStorage).
6. On a second browser on the same host → the placement is the same (syncable, Q1).

**Upgrade path.** Before upgrading, on a real install with the checkbox **unchecked**
(`showDeviceBadges: false`, no `deviceLabelPlacement` in `settings.json`), upgrade and
confirm the grid still shows **no** device labels and `GET /api/settings` reports
`deviceLabelPlacement: "off"`. This is the highest-risk regression in the change (§2.4)
and must be verified against a real pre-upgrade config, not a fixture.

---

## §9. Explicit non-goals

- No change to `frontend/deck/` or the `muxplex-deck` repo (§1 Q2).
- No new `/api/*` endpoint. `deviceLabelPlacement` is an additive field on the
  existing `GET`/`PATCH /api/settings` and on the federation sync payload (it is in
  `SYNCABLE_KEYS`, which is what threads it through).
- No `auto` placement (§0).
- No render-time override of `"off"` under any condition (§1 Q3).
- No synchronous re-render on setting change; the ≤2s poll picks it up, as it does
  today for `showDeviceBadges` (§4.5).
- No removal of `showDeviceBadges` from `DEFAULT_SETTINGS`, `SYNCABLE_KEYS`, or
  `DISPLAY_DEFAULTS` — ever. It is a public field with unknown readers.
- No change to `CHANGELOG.md` or the version — those happen at release time, by the
  owner (`AGENTS.md`).

---

## §10. Success criteria

- [ ] `deviceLabelPlacement` defaults to `"titlebar"`; a fresh install renders
      byte-identically to `50b1560`.
- [ ] An existing install with `showDeviceBadges: false` renders **no** device labels
      after upgrade, verified against a real pre-upgrade `settings.json`.
- [ ] `showDeviceBadges` is never written by `app.js` and always equals
      `deviceLabelPlacement != "off"` on disk.
- [ ] An old peer syncing `showDeviceBadges: true` cannot move a device off
      `"corner"`.
- [ ] The `.tile-device-tag` chip renders byte-identically over a full-white pane and
      over an empty prompt (§8.5).
- [ ] `.tile-device-tag` is defined in `style.css` and is proven so by a test (§8.4 G2).
- [ ] `node --test frontend/tests/*.mjs` green, including `test_shared_scope.mjs` and
      `test_css_class_definitions.mjs`.
- [ ] `make test` green in the DTU.
- [ ] `tests/test_readme.py::test_readme_documents_all_settings_keys` green (requires
      the new README row).
- [ ] `docs/API_SEMANTICS.md` carries the derived-mirror semantics and the
      PWA-scoped-by-design note.
